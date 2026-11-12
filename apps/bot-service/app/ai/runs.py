"""The AI run log (round 3, B3): one models.AiRun row per piece of AI work
on a case -- an intake sorting or reading stage and its second look, a
Telegram (re)draft, Annex A suggestions, and Phase 2's re-detection and
price lookups.

Before this the only trace of an AI call was a log line without a case id:
nobody could see, per case or across cases, which photos the AI read, how
long it took, which key paid, or why it failed.

    with runs.recorded(report_id, "read", actor) as run:
        ...any number of call_structured calls, on this thread...
        run.photos_sent = ["P01", "P03"]
        run.parts_found = 5

recorded() writes the row ("running") before the work and finishes it
after: ok, or the failure's status and code with the error scrubbed. Every
call_structured try made on the thread meanwhile is collected through a
thread-local (collecting()) -- model, key last 4, outcome, waits, time,
tokens when the SDK reports them -- so call_structured's signature stays
as it was. Work spread over threads (the sorting stage's pool) binds each
worker to the same run: `with runs.collecting(run.attempts): ...`.

start_run / finish_run are the same two steps for work that can't sit in
one with-block (Phase 2's re-detection, whose run outlives the request).

The log never stops the AI work: a row that can't be written is logged and
the work goes on unrecorded. It never holds a key (only its last 4
characters) nor anything a member of the public sent (names, phones,
statements, plates): error_text goes through scrub_error, and the rows
carry report ids, never the sender's details.

Imports the database lazily, as keys.py does: this module is imported by
every AI path (structured.py), and app.reports.db makes the engine.
"""
import logging
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from app.ai.keys import AiActor, AiUnavailable, scrub
from app.config import settings
from app.reports.models import AiRun, iso_utc

logger = logging.getLogger(__name__)

# second_look: the one call after an intake reading that suggests parts to
# check (ai/second_look.py) -- its own row, so the reading's time and model
# ("AI read 14 of 41 photos (48 s)") stay the reading's.
KINDS = ("sort", "read", "redraft", "annex_suggest", "redetect", "lookup", "second_look")
# Contract 8 D2: the zone calls after an intake reading (intake/zones.py,
# settings.intake_read_mode "zones"), one row for all of them, like the
# sorting stage's batches. Its photos_sent says per zone what was sent and
# what came back (intake/zones.read_and_merge); not a READ_KIND, so "AI
# read 14 of 41 photos" stays the overall reading's.
ZONES_KIND = "read_zones"
KINDS += (ZONES_KIND,)
CHANNELS = ("dashboard", "telegram", "whatsapp", "system")
STATUSES = ("running", "ok", "failed", "refused", "aborted")
# What counts as a failure on GET /ai-usage and in the case's list.
FAILED_STATUSES = ("failed", "refused", "aborted")
# Runs that read the case's photos and drafted from them: the newest one is
# GET /reports/{id}'s ai_last_read (tab 1's "AI read 14 of 41 photos").
READ_KINDS = ("read", "redraft")
# A row still "running" this long after it started lost its process (a
# restart, a deploy): nothing runs this long -- the slowest stage, 150
# photos sorted with every model timing out, is well under it. Shown as
# aborted ("interrupted"); the row itself is left as it is.
RUN_STALE_AFTER = timedelta(hours=1)
# GET /reports/{id} sends at most this many, newest first.
MAX_PER_CASE = 50
MAX_ERROR = 200

# An attempt's outcome (call_structured) -> the run's error_code when it
# was the last try before the run gave up. AiUnavailable's own codes
# (ai_key_missing, ai_quota_used...) are used as they are.
_OUTCOME_CODES = {"429": "rate_limited", "timeout": "timeout", "schema": "bad_answer", "error": "ai_error"}
# The last try was skipped for its model's used-up free day (outcome
# "day_spent"), or was the 429 that said so (it carries spent_until): not
# "too many requests", which a wait would cure.
DAY_ALLOWANCE_USED = "day_allowance_used"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------ the attempts


class Attempts:
    """What one run's Gemini calls did, try by try, as call_structured
    reports them. Shared by every thread of the run -- a sorting stage's
    batches run on pool workers at once -- so adding takes a lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[dict] = []
        self._input_bytes = 0
        self._calls = 0

    def add(self, attempt: dict) -> None:
        with self._lock:
            self._items.append(attempt)

    def add_input(self, size: int) -> None:
        with self._lock:
            self._input_bytes += size
            self._calls += 1

    def drain(self) -> tuple[list[dict], int | None]:
        """(the attempts so far, the bytes the calls sent or None when no
        call was made), emptied."""
        with self._lock:
            items, self._items = self._items, []
            size = self._input_bytes if self._calls else None
            self._input_bytes = self._calls = 0
        return items, size


_local = threading.local()


@contextmanager
def collecting(attempts: Attempts | None = None) -> Iterator[Attempts]:
    """Binds `attempts` (a new collector when None) to this thread for the
    block: every call_structured try made on this thread goes into it. The
    thread's previous collector is put back after."""
    previous = getattr(_local, "attempts", None)
    current = attempts if attempts is not None else Attempts()
    _local.attempts = current
    try:
        yield current
    finally:
        _local.attempts = previous


def note_attempt(attempt: dict) -> None:
    """call_structured, after every model try. Nothing when no run collects
    on this thread (a script, the key test)."""
    current = getattr(_local, "attempts", None)
    if current is not None:
        current.add(attempt)


def note_input(size: int) -> None:
    """call_structured, once per call: the request's size in bytes."""
    current = getattr(_local, "attempts", None)
    if current is not None:
        current.add_input(size)


# ------------------------------------------------------------ what's kept

# Digit runs of 5 or more, single spaces or dashes allowed between them:
# phone numbers, NRIC and policy numbers. "429" and "P03" stay.
_DIGITS = re.compile(r"\+?\d(?:[ \-]?\d){4,}")
# Singapore plates (SNA 1234 B: the checksum letter) and other letter-digit
# plates (JQB 1234). Upper case only, so ordinary words survive.
_PLATES = (
    re.compile(r"\b[A-Z]{1,3} ?\d{1,4} ?[A-Z]\b"),
    re.compile(r"\b[A-Z]{2,3} ?\d{3,4}\b"),
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# A Gemini key, as keys.scrub finds one.
_KEYLIKE = re.compile(r"AIza[0-9A-Za-z_-]*")
# A validation error quotes what the model wrote (pydantic's
# "input_value='Tan Ah Kow'"), which can be a name or a plate read off a
# photo: never kept.
# Up to ", input_type=<name>]" only: a dict or list value (pydantic prints
# its head and tail) contains "]" itself, and stopping there kept the tail,
# e.g. "...ter_name': 'Tan Ah Kow'}".
_INPUT_VALUE = re.compile(r"input_value=.*?(?=, input_type=\w+\]|$)")
# A database error quotes its SQL and the row's values (the case data JSON,
# which can hold names): everything from "[SQL:" on goes.
_SQL_TAIL = re.compile(r"\s*\[(?:SQL|parameters):.*$")


def scrub_error(text: str | None, key_values: tuple[str, ...] = ()) -> str | None:
    """An error made safe to keep and show: one line, no key (keys.scrub),
    no quoted model output, no e-mail, plate or long digit run, at most 200
    characters. None for nothing."""
    if not text:
        return None
    out = " ".join(str(text).split())
    for value in key_values:
        if value:
            out = out.replace(value, "...")
    # Anything key-shaped first and whole, before the digit rule below can
    # cut one into pieces; nothing is shortened before the rules have run,
    # so no half plate or number is left at the cut.
    out = _KEYLIKE.sub("AIza...", out)
    out = _INPUT_VALUE.sub("input_value=...", out)
    out = _SQL_TAIL.sub("", out)
    out = _EMAIL.sub("[email]", out)
    for plate in _PLATES:
        out = plate.sub("[plate]", out)
    out = _DIGITS.sub("#", out)
    # keys.scrub last: the length (and the key rule once more).
    return scrub(out)[:MAX_ERROR] or None


def status_for(exc: BaseException) -> str:
    """refused: no key could be used (AiUnavailable, which the person can
    fix); failed: any other error; aborted: the work was stopped from
    outside (a cancelled task, the process shutting down)."""
    if isinstance(exc, AiUnavailable):
        return "refused"
    return "failed" if isinstance(exc, Exception) else "aborted"


def error_code_for(exc: BaseException, attempts: list[dict]) -> str:
    """The existing codes where there is one: AiUnavailable's; then what
    the last model try said (rate_limited, timeout, bad_answer, ai_error,
    day_allowance_used); photo_missing; interrupted; else "error"."""
    if isinstance(exc, AiUnavailable):
        return exc.code
    if isinstance(exc, FileNotFoundError):
        return "photo_missing"
    if not isinstance(exc, Exception):
        return "interrupted"
    failed = [a for a in attempts if a.get("outcome") != "ok"]
    if failed:
        if failed[-1].get("outcome") == "day_spent" or failed[-1].get("spent_until"):
            return DAY_ALLOWANCE_USED
        return _OUTCOME_CODES.get(failed[-1].get("outcome"), "ai_error")
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "error"


def _error_text(exc: BaseException) -> str | None:
    """The words worth keeping, before scrub_error. call_structured's final
    error carries Google's own words as its cause (already scrubbed of the
    keys it was sent with); a missing photo's message is a storage path."""
    if isinstance(exc, AiUnavailable):
        return exc.message
    if isinstance(exc, FileNotFoundError):
        return "A photo was removed while the AI was working."
    if isinstance(exc, RuntimeError) and exc.__cause__ is not None and "fallback models" in str(exc):
        return str(exc.__cause__)
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


# ------------------------------------------------------------ the row


def _channel(channel: str | None, actor: AiActor | None) -> str:
    """Where the run was started. Scripts (AiActor.cli) are the system."""
    value = channel or getattr(actor, "channel", None) or "system"
    return value if value in CHANNELS else "system"


def start_run(db, *, report_id: str | None, kind: str, channel: str | None, actor: AiActor | None) -> AiRun:
    """Writes the row as "running" and commits it (the caller's session)."""
    row = AiRun(
        report_id=report_id,
        kind=kind,
        channel=_channel(channel, actor),
        actor_user_id=getattr(actor, "user_id", None),
        status="running",
        started_at=_now(),
        attempts=[],
    )
    db.add(row)
    db.commit()
    return row


def _total(values) -> int | None:
    numbers = [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
    return sum(numbers) if numbers else None


def finish_run(
    db,
    run: AiRun | str,
    status: str,
    *,
    attempts: list[dict] | None = None,
    error: BaseException | None = None,
    error_code: str | None = None,
    photos_sent=None,
    parts_found: int | None = None,
    input_bytes: int | None = None,
) -> AiRun | None:
    """Finishes a started row (by row or id) and commits. The key and model
    are the try that answered, else the last one tried; tokens are summed
    over the tries that reported them (None when none did). On anything
    but ok: error_code (the given one, else error_code_for) and the
    scrubbed error."""
    row = db.get(AiRun, run if isinstance(run, str) else run.id)
    if row is None:
        return None
    attempts = list(attempts or [])
    answered = next((a for a in reversed(attempts) if a.get("outcome") == "ok"), None)
    used = answered or (attempts[-1] if attempts else None)
    row.status = status
    row.finished_at = _now()
    row.attempts = attempts
    row.model_used = answered.get("model") if answered else None
    row.key_source = (used.get("key_source") if used else None) or "none"
    row.key_last4 = used.get("key_last4") if used else None
    row.prompt_tokens = _total(a.get("prompt_tokens") for a in attempts)
    row.output_tokens = _total(a.get("output_tokens") for a in attempts)
    row.input_bytes = input_bytes
    row.photos_sent = photos_sent
    row.parts_found = parts_found
    if status != "ok":
        row.error_code = error_code or (error_code_for(error, attempts) if error is not None else None)
        row.error_text = scrub_error(_error_text(error)) if error is not None else None
    db.commit()
    return row


class Run:
    """recorded()'s handle. The work sets what it found; the row gets it
    when the block ends."""

    def __init__(self, report_id: str | None, kind: str, actor: AiActor | None, channel: str | None):
        self.id: str | None = None
        self.report_id = report_id
        self.kind = kind
        self.actor = actor
        self.channel = channel
        self.attempts = Attempts()
        self.photos_sent = None
        self.parts_found: int | None = None
        # Set before raising, for a failure the exception can't name
        # ("nothing_to_read").
        self.error_code: str | None = None
        # Set (to a code) when the work ended with nothing to keep, e.g. the
        # draft was deleted or filed while the AI read it: "aborted".
        self.aborted: str | None = None


def _write(step, what: str):
    """step(db) on a session of its own; None (logged) when it fails."""
    from app.reports.db import SessionLocal

    db = SessionLocal()
    try:
        return step(db)
    except Exception as exc:
        db.rollback()
        logger.warning("AI run log: couldn't %s (%s)", what, type(exc).__name__)
        return None
    finally:
        db.close()


@contextmanager
def recorded(report_id: str | None, kind: str, actor: AiActor | None, *, channel: str | None = None) -> Iterator[Run]:
    """The row for the work inside the block (the top of this file). The
    block's exception, if any, is recorded and raised on unchanged."""
    run = Run(report_id, kind, actor, channel)
    run.id = _write(
        lambda db: start_run(db, report_id=report_id, kind=kind, channel=channel, actor=actor).id, "start a run"
    )
    with collecting(run.attempts):
        try:
            yield run
        except BaseException as exc:
            _close(run, exc)
            raise
    _close(run, None)


def _close(run: Run, exc: BaseException | None) -> None:
    if run.id is None:
        return
    attempts, size = run.attempts.drain()
    if exc is None:
        status, code = ("aborted", run.aborted) if run.aborted else ("ok", None)
    else:
        status, code = status_for(exc), run.error_code
    _write(
        lambda db: finish_run(
            db, run.id, status, attempts=attempts, error=exc, error_code=code,
            photos_sent=run.photos_sent, parts_found=run.parts_found, input_bytes=size,
        ),
        "finish a run",
    )


# ------------------------------------------------------------ reading


def effective_status(row: AiRun, now: datetime | None = None) -> str:
    """The row's status, with a "running" one that lost its process
    (RUN_STALE_AFTER) shown as aborted."""
    if row.status == "running" and row.started_at is not None:
        if _as_utc(row.started_at) < (now or _now()) - RUN_STALE_AFTER:
            return "aborted"
    return row.status


def effective_error_code(row: AiRun, status: str) -> str | None:
    if status == "aborted" and row.status == "running":
        return row.error_code or "interrupted"
    return row.error_code


def photos_count(photos_sent) -> int | None:
    """How many photos a run sent: its labels, or a sorting stage's count."""
    if isinstance(photos_sent, list):
        return len(photos_sent)
    if isinstance(photos_sent, dict):
        count = photos_sent.get("count")
        if isinstance(count, int) and not isinstance(count, bool):
            return count
    return None


def actor_names(db, user_ids) -> dict[str, str]:
    """{user id: the name the history shows}, in one query."""
    from app.auth.core import display_name
    from app.reports.models import User

    wanted = {i for i in user_ids if i}
    if not wanted:
        return {}
    return {u.id: display_name(u) for u in db.query(User).filter(User.id.in_(wanted)).all()}


def summary(row: AiRun, names: dict[str, str], now: datetime | None = None, viewer=None) -> dict:
    """One row as GET /reports/{id} sends it (lib/api.ts AiRunSummary).
    The key's last 4 only to an admin or to the person whose run it was:
    everyone who may see a case sees its runs, and a colleague's own key
    (or the company key) is not theirs to fingerprint."""
    status = effective_status(row, now)
    show_key = viewer is not None and (getattr(viewer, "is_admin", False) or getattr(viewer, "id", None) == row.actor_user_id)
    return {
        "id": row.id,
        "kind": row.kind,
        "channel": row.channel,
        "started_at": iso_utc(row.started_at),
        "finished_at": iso_utc(row.finished_at) if row.finished_at else None,
        "status": status,
        "error_code": effective_error_code(row, status),
        # Scrubbed when written (scrub_error).
        "error_text": row.error_text,
        "model_used": row.model_used,
        "photos_sent": row.photos_sent,
        "photos_count": photos_count(row.photos_sent),
        "parts_found": row.parts_found,
        "actor_name": names.get(row.actor_user_id) if row.actor_user_id else None,
        "key_source": row.key_source,
        "key_last4": row.key_last4 if show_key else None,
    }


def summaries(db, report_id: str, limit: int = MAX_PER_CASE, viewer=None) -> list[dict]:
    """A case's runs (its draft's too: the same id), newest first."""
    rows = (
        db.query(AiRun)
        .filter(AiRun.report_id == report_id)
        .order_by(AiRun.started_at.desc(), AiRun.id)
        .limit(limit)
        .all()
    )
    names = actor_names(db, {r.actor_user_id for r in rows})
    now = _now()
    return [summary(r, names, now, viewer) for r in rows]


def _newest_read(db, report_id: str) -> AiRun | None:
    """The newest reading that finished well and says which photos it sent."""
    rows = (
        db.query(AiRun)
        .filter(AiRun.report_id == report_id, AiRun.kind.in_(READ_KINDS), AiRun.status == "ok")
        .order_by(AiRun.started_at.desc(), AiRun.id)
        .limit(5)
        .all()
    )
    return next((r for r in rows if isinstance(r.photos_sent, list)), None)


def read_by(db, report_id: str) -> dict | None:
    """GET /intake/{id}'s read_by, for the review step's notice "Read by the
    backup model (gemini-3.6-flash): today's main-model allowance is used up
    until 4:00 pm": {model, main_model, main_spent_until (ISO)} when the
    newest reading that finished was answered by another model of the
    chain because the main one's free day was used up -- skipped
    (day_spent) or a 429 that said so -- else None.

    A timeout or busy main model doesn't count: that's no reason to tell
    the surveyor about allowances. The chain is today's setting; a reading
    may make more than one call (a second look on another chain), so only
    the chain's own models are looked at."""
    chain =[m.strip() for m in (settings.gemini_model_chain or "").split(",") if m.strip()]
    if not chain:
        return None
    main = chain[0]
    row = (
        db.query(AiRun)
        .filter(AiRun.report_id == report_id, AiRun.kind.in_(READ_KINDS), AiRun.status == "ok")
        .order_by(AiRun.started_at.desc(), AiRun.id)
        .first()
    )
    if row is None:
        return None
    attempts = [a for a in (row.attempts or []) if isinstance(a, dict) and a.get("model") in chain]
    if any(a.get("model") == main and a.get("outcome") == "ok" for a in attempts):
        return None
    spent = [a["spent_until"] for a in attempts if a.get("model") == main and isinstance(a.get("spent_until"), str)]
    answered = next((a for a in attempts if a.get("outcome") == "ok"), None)
    if not spent or answered is None:
        return None
    return {"model": answered.get("model"), "main_model": main, "main_spent_until": max(spent)}


def last_read(db, report_id: str, photos_total: int) -> dict | None:
    """GET /reports/{id}'s ai_last_read, for tab 1's line "AI read 14 of 41
    photos on 27 Feb 10:16 (48 s)": None when the AI never read the case."""
    row = _newest_read(db, report_id)
    if row is None:
        return None
    seconds = None
    if row.finished_at is not None:
        seconds = round((_as_utc(row.finished_at) - _as_utc(row.started_at)).total_seconds())
    return {
        "at": iso_utc(row.finished_at or row.started_at),
        "photos_read": len(row.photos_sent),
        "photos_total": photos_total,
        "seconds": seconds,
        "model": row.model_used,
    }


def relabel_last_read(db, report_id: str, read_ids: list[str], label_of: dict[str, str]) -> None:
    """At filing (api/intake._finalize_claimed, inside its transaction; no
    commit here): the newest reading's photos under the case's final
    P-numbers. A reading names photos as numbered then, and numbers shift
    when a photo is deleted before filing. read_ids is the session's
    read_photo_ids -- that reading's photos, in its order -- and label_of
    each kept photo's final label; a photo deleted since drops out."""
    row = _newest_read(db, report_id)
    if row is None or len(row.photos_sent) != len(read_ids):
        return
    row.photos_sent = [label_of[photo_id] for photo_id in read_ids if photo_id in label_of]


def relabel_zone_reads(db, report_id: str, label_of: dict[str, str]) -> None:
    """Contract 8 D2, at filing beside relabel_last_read (no commit here):
    the zone rows' photos, kept as "@<photo id>" as damage items' references
    are in a draft, under the case's final P-numbers; a photo deleted since
    drops out. Every zone row of the case: the photo rows are deleted at
    filing, so an "@id" left behind would point at nothing."""
    rows = db.query(AiRun).filter(AiRun.report_id == report_id, AiRun.kind == ZONES_KIND).all()
    for row in rows:
        detail = row.photos_sent
        if not isinstance(detail, dict) or not isinstance(detail.get("zones"), list):
            continue
        zones = []
        for zone in detail["zones"]:
            zone = dict(zone) if isinstance(zone, dict) else zone
            if isinstance(zone, dict) and isinstance(zone.get("photos"), list):
                zone["photos"] = [
                    label_of.get(ref[1:]) if str(ref).startswith("@") else ref
                    for ref in zone["photos"]
                    if not str(ref).startswith("@") or ref[1:] in label_of
                ]
            zones.append(zone)
        # A new object, so the JSON column is seen to change.
        row.photos_sent = {**detail, "zones": zones}
