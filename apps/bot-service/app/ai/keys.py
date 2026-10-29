"""People's own AI (Gemini) keys, added on Settings > My AI key
(models.UserAiKey, the routes in api/ai_keys.py), and which key an AI call
uses (P4, the end of this file).

Whoever presses the button pays. Every Gemini call is made for an AiActor
-- a person, never a key -- and resolve_keys looks the keys up afresh on
each call, so a removed key, an admin's untick or a disabled account
applies from the very next call, even in the middle of an intake run:

    1. their own key, if it is saved, Google hasn't refused it (out of
       quota counts again after an hour: usable()), and own keys can be
       read here (secretbox.state() ok);
    2. then the company keys, for admins and people ticked "may use the
       company key" (auth/permissions.may_use_company_key) -- also when
       their own key has stopped working.

Nothing left: AiUnavailable, with a code the dashboard explains. Scripts
use AiActor.cli(): the company keys, as before P4. No HTTP route ever does,
nor the Telegram bot, which drafts for the account its Telegram is linked
to (AiActor.for_user(id, "telegram")).

One key per person and one person per key: a key someone else has saved,
or one of the company's, is refused. The key is sealed the moment it
arrives (ai/secretbox.py) and never leaves the server again. Everyone,
admins included, sees only its last 4 characters and a status:

    untested    saved, but Google couldn't be reached to check it
    ok          Google accepted it at the last check
    invalid     Google refused it (wrong, deleted, or the API is off for it)
    quota       Google accepted it but it has no allowance left for now
    unreadable  this server can't open saved keys right now (secretbox.state()
                isn't "ok"). Worked out when read, never stored: the rows
                keep Google's last verdict, so the right secret put back
                mends them all
"""
import hashlib
import hmac
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import delete, or_, update
from sqlalchemy.orm import Session

from app.ai import secretbox
from app.auth.core import as_utc, audit, utc_now
from app.auth.permissions import may_use_company_key
from app.config import settings
from app.reports.models import ApiKey, User, UserAiKey, iso_utc

logger = logging.getLogger(__name__)

PROVIDER = "gemini"
STATUSES = ("untested", "ok", "invalid", "quota", "unreadable")
# What a row can hold: Google's verdicts. "unreadable" is only ever shown.
STORED_STATUSES = ("untested", "ok", "invalid", "quota")
# The one answer for a key someone else has (an own key, or a company
# key): it mustn't confirm which, or that the key exists at all.
IN_USE = "This key can't be used here."
# What the AI uses: a key Google hasn't refused or run dry.
WORKING = ("ok", "untested")
# A key out of quota is used again this long after it was found so (its
# last_checked_at): Google's allowances refill, and nobody should have to
# press Test for that. A call that still hits the limit marks it again,
# for another hour; one that works puts it back to "ok".
QUOTA_RETRY_AFTER = timedelta(hours=1)
# The retry's clock (tests move it on).
_clock = utc_now

# Gemini keys are 39 characters starting "AIza"; looser than that, so a
# newer key format isn't turned away by this check rather than by Google.
_FORMAT = re.compile(r"[A-Za-z0-9_-]{20,60}")
FORMAT_PROBLEM = "That doesn't look like a Gemini API key. It is usually 39 letters and numbers, starting with AIza."
# Anything key-like in a message from Google, before it is stored.
_KEYLIKE = re.compile(r"AIza[0-9A-Za-z_-]*")
MAX_ERROR = 200
# models.list answers in well under a second; a hung connection mustn't
# hold a worker thread for long.
PROBE_TIMEOUT_MS = 10_000
UNREACHABLE = "Couldn't reach Google to check the key."
STORAGE_OFF_ERROR = "This server can't read saved AI keys right now. Please tell your admin."
ROW_UNREADABLE_ERROR = "This key can't be opened on this server. Add it again."


def format_problem(key: str) -> str | None:
    """Why this (trimmed) text can't be a key, or None."""
    return None if _FORMAT.fullmatch(key) else FORMAT_PROBLEM


def last4(key: str) -> str:
    return key[-4:]


def scrub(message: str, key: str | None = None) -> str:
    """A message from Google made safe to keep: no key in it, one line, at
    most 200 characters."""
    text = message or ""
    if key:
        text = text.replace(key, "...")
    text = _KEYLIKE.sub("AIza...", text)
    return " ".join(text.split())[:MAX_ERROR]


def probe_key(key: str) -> tuple[str, str | None]:
    """Asks Google whether it takes this key: models.list, which spends none
    of the key's allowance. ("ok", None), ("invalid", why), ("quota", why),
    or ("untested", why) when Google couldn't give an answer.

    A client of its own, thrown away after: app/ai/client.get_client keeps
    every key it sees for the life of the process."""
    from google import genai
    from google.genai import errors, types

    client = None
    try:
        client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=PROBE_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=0)),
        )
        next(iter(client.models.list(config={"page_size": 1})), None)
        return "ok", None
    except errors.APIError as exc:
        why = scrub(f"Google said: {exc.code} {exc.message or exc.status or ''}", key)
        if exc.code in (400, 401, 403):
            return "invalid", why
        if exc.code == 429:
            return "quota", why
        # Google's own trouble (5xx): no verdict on the key.
        logger.info("AI key check got no answer from Google (HTTP %s)", exc.code)
        return "untested", why
    except Exception as exc:
        # Never the exception itself: some carry the request, key included.
        logger.info("AI key check couldn't reach Google (%s)", type(exc).__name__)
        return "untested", UNREACHABLE
    finally:
        close = getattr(client, "close", None)
        if close:
            try:
                close()
            except Exception:
                pass


def company_key_matches(db: Session, key: str) -> bool:
    """Whether this is one of the company's keys (Settings > Company, or
    GEMINI_API_KEY). Those are already shared by everyone allowed them."""
    candidates = [k for (k,) in db.query(ApiKey.key).filter(ApiKey.provider == PROVIDER).all() if k]
    if settings.gemini_api_key:
        candidates.append(settings.gemini_api_key)
    sent = key.encode("utf-8")
    return any(hmac.compare_digest(c.strip().encode("utf-8"), sent) for c in candidates)


def key_in_use(db: Session, key: str, fingerprint: str, user_id: str) -> str | None:
    """Why someone can't save this key, for the history (they are only
    told "This key can't be used here."), or None."""
    other = (
        db.query(UserAiKey.user_id)
        .filter(UserAiKey.fingerprint == fingerprint, UserAiKey.user_id != user_id)
        .first()
    )
    if other:
        owner = db.get(User, other[0])
        return f"another account's key ({'@' + owner.username if owner and owner.username else other[0]})"
    if company_key_matches(db, key):
        return "a company key"
    return None


def fingerprint_if_readable(key: str) -> str | None:
    """The key's fingerprint, or None when own keys can't be opened here
    (their fingerprints couldn't be compared then anyway)."""
    try:
        return secretbox.fingerprint(key)
    except secretbox.Unreadable:
        return None


def own_key_owner(db: Session, fingerprint: str | None) -> str | None:
    """Whose own key this is ("@username"), for refusing it as a company
    key, or None. fingerprint is fingerprint_if_readable's: worked out
    before the caller takes the write lock, as it may read the secret
    afresh. None (own keys can't be opened here) checks nothing: adding a
    company key, the backup, mustn't wait on that."""
    if fingerprint is None:
        logger.warning("A company key was added without checking it against own keys (secret %s)", secretbox.state())
        return None
    other = db.query(UserAiKey.user_id).filter(UserAiKey.fingerprint == fingerprint).first()
    if other is None:
        return None
    owner = db.get(User, other[0])
    return "@" + owner.username if owner and owner.username else other[0]


def get_row(db: Session, user_id: str) -> UserAiKey | None:
    return db.query(UserAiKey).filter(UserAiKey.user_id == user_id).first()


def save_key(
    db: Session, user_id: str, key: str, fingerprint: str, status: str, error: str | None, *, billing_confirmed: bool
) -> UserAiKey:
    """Seals and stores the key as this person's, replacing any they had
    (the caller commits). status is probe_key's verdict."""
    now = utc_now()
    row = get_row(db, user_id)
    if row is None:
        row = UserAiKey(user_id=user_id, provider=PROVIDER)
        db.add(row)
    row.key_enc = secretbox.seal(key)
    row.last4 = last4(key)
    row.fingerprint = fingerprint
    row.status = status
    row.last_error = error
    row.billing_confirmed = billing_confirmed
    row.last_checked_at = None if status == "untested" else now
    row.last_used_at = None
    # A new key: "added" now, even when it replaces one.
    row.created_at = now
    row.updated_at = now
    return row


def record_check(db: Session, user_id: str, fingerprint: str, status: str, error: str | None) -> bool:
    """Stores a re-check's verdict (the caller commits) -- only if the key
    tested is still this person's key: one saved meanwhile mustn't get the
    old key's verdict. False when it was thrown away."""
    result = db.execute(
        update(UserAiKey)
        .where(UserAiKey.user_id == user_id, UserAiKey.fingerprint == fingerprint)
        .values(status=status, last_error=error, last_checked_at=utc_now())
        .execution_options(synchronize_session=False)
    )
    return result.rowcount > 0


def unseal_key(row: UserAiKey) -> str:
    """The key itself, for asking Google about it. Raises
    secretbox.Unreadable. Never put it in a response or a log."""
    return secretbox.unseal(row.key_enc)


def delete_user_key(db: Session, user_id: str, actor, *, action: str = "ai_key_removed", reason: str = "") -> bool:
    """Removes this person's own key, if they have one, with a history
    entry; False if there was none. The caller commits, so this can be part
    of a bigger change -- disabling an account deletes its key in the same
    transaction (PATCH /users/{id} {active: false})."""
    # One DELETE, not a read and then a delete: it takes SQLite's write
    # lock as it runs, so a key saved a moment before goes too, and one
    # being saved meanwhile waits for this commit (then finds the account
    # off, api/ai_keys.py).
    gone = db.execute(
        delete(UserAiKey)
        .where(UserAiKey.user_id == user_id)
        .returning(UserAiKey.last4)
        .execution_options(synchronize_session=False)
    ).all()
    if not gone:
        return False
    owner = db.get(User, user_id)
    detail = f"{owner.email if owner else user_id}: key ...{gone[0][0]}"
    audit(db, actor, action, detail=f"{detail} ({reason})" if reason else detail)
    return True


def _status_of(stored: str, readable: bool) -> str:
    return stored if readable else "unreadable"


def usable(status: str | None, checked_at) -> bool:
    """Whether the AI uses an own key with this status (as shown: an
    "unreadable" one never): working, or out of quota for longer than
    QUOTA_RETRY_AFTER. checked_at is the row's last_checked_at, which
    marking it "quota" set."""
    if status in WORKING:
        return True
    if status != "quota":
        return False
    return checked_at is None or _clock() - as_utc(checked_at) >= QUOTA_RETRY_AFTER


def keys_by_user(db: Session, user_ids) -> dict[str, dict]:
    """{user id: {has_key, last4, status, added_at}} for the people who have
    a key, for the admin's Users screen -- one query for all of them, and
    never the sealed key."""
    ids = list(user_ids)
    if not ids:
        return {}
    rows = (
        db.query(UserAiKey.user_id, UserAiKey.last4, UserAiKey.status, UserAiKey.created_at)
        .filter(UserAiKey.user_id.in_(ids))
        .all()
    )
    readable = secretbox.state() == secretbox.OK if rows else True
    return {
        r.user_id: {
            "has_key": True,
            "last4": r.last4,
            "status": _status_of(r.status, readable),
            "added_at": iso_utc(r.created_at),
        }
        for r in rows
    }


def ai_key_status(db: Session, user, *, unreadable: bool = False) -> dict:
    """GET /auth/me/ai-key: the person's own key and what the AI uses for
    them -- source and ai_available follow resolve_keys' order. unreadable:
    this row wouldn't open, though the secret is fine (shown, not stored)."""
    row = get_row(db, user.id)
    company = may_use_company_key(user)
    if row is None:
        return {
            "has_key": False,
            "last4": None,
            "status": None,
            "billing_confirmed": False,
            "added_at": None,
            "last_checked_at": None,
            "last_error": None,
            "company_allowed": company,
            "ai_available": company,
            "source": "company" if company else "none",
        }
    storage_ok = secretbox.state() == secretbox.OK
    readable = storage_ok and not unreadable
    status = _status_of(row.status, readable)
    # "quota" still shows as quota an hour on, while the AI tries it again.
    working = usable(status, row.last_checked_at)
    error = row.last_error if readable else STORAGE_OFF_ERROR if not storage_ok else ROW_UNREADABLE_ERROR
    return {
        "has_key": True,
        "last4": row.last4,
        "status": status,
        "billing_confirmed": bool(row.billing_confirmed),
        "added_at": iso_utc(row.created_at),
        "last_checked_at": iso_utc(row.last_checked_at) if row.last_checked_at else None,
        "last_error": error,
        "company_allowed": company,
        "ai_available": working or company,
        "source": "own" if working else "company" if company else "none",
    }


# ------------------------------------------------- P4: which key a call uses

# AiUnavailable's codes, and what the person is told (the dashboard has its
# own words for each, with a way to fix it).
AI_KEY_MISSING = "ai_key_missing"
AI_KEY_INVALID = "ai_key_invalid"
AI_QUOTA_USED = "ai_quota_used"
AI_KEY_UNREADABLE = "ai_key_unreadable"
AI_ACCOUNT_OFF = "ai_account_off"
MESSAGES = {
    AI_KEY_MISSING: (
        "AI is off for your account. Add your own AI key in Settings → My AI key, "
        "or ask an admin to allow the company key."
    ),
    AI_KEY_INVALID: "Your AI key stopped working (Google refused it). Check it in Settings → My AI key.",
    # The AI tries the key again after QUOTA_RETRY_AFTER by itself.
    AI_QUOTA_USED: "Your AI key has used up its quota for now. Try again in about an hour, or check billing in Google Cloud.",
    AI_KEY_UNREADABLE: "Your AI key can't be read on this server right now. Tell an admin (Settings → Company).",
    AI_ACCOUNT_OFF: "AI is off: this account is disabled.",
}
# A stored status that keeps an own key out, and the code it means when
# nothing else is left.
_STATUS_CODES = {"invalid": AI_KEY_INVALID, "quota": AI_QUOTA_USED}

# Google saying the key itself is wrong. A 400 is just as often about the
# request (a photo it can't take, a schema), which says nothing about the key.
_KEY_REFUSED = re.compile(r"API_KEY_INVALID|API key not valid|API key expired", re.IGNORECASE)
# A 403 about the key (suspended, restricted to other APIs or callers, its
# project's Gemini API off), not about one model it may not use.
_KEY_FORBIDDEN = re.compile(
    r"CONSUMER_SUSPENDED|API_KEY_[A-Z_]+|API key not valid|API key expired|SERVICE_DISABLED"
    r"|has not been used in project|API has not been enabled|permission denied on (the|this) (api )?key",
    re.IGNORECASE,
)
# A 429 for the day's allowance, not the per-minute window: waiting a few
# seconds can't help.
_DAILY_QUOTA = re.compile(r"PerDay|per[ _-]day", re.IGNORECASE)
# A 429 for a model the key has no allowance for at all ("limit: 0": a free
# tier asking for a paid-only model). On every model, the key needs billing.
_NO_ALLOWANCE = re.compile(r"\blimit: 0\b", re.IGNORECASE)
NO_ALLOWANCE_ERROR = "This key's Google project has no allowance for these models: turn on billing in Google Cloud."

# A success stamps last_used_at at most this often per key: a busy intake
# makes dozens of calls, and each stamp is a write on the shared database.
TOUCH_EVERY = timedelta(minutes=10)
_touched: dict[tuple[str, str], float] = {}  # (user id, fingerprint) -> this process's last stamp
_touched_lock = threading.Lock()


@dataclass(frozen=True)
class AiActor:
    """Who an AI call is for: whoever pressed the button -- an admin working
    on someone else's case is the admin, never the case's owner. A person,
    never a key: a background run keeps this, and resolve_keys finds the
    keys again for every call.

    Passed by hand to every thread that calls Gemini. Context variables
    don't reach plain threads or ThreadPoolExecutor workers, so an intake
    run and each of its sorting workers would lose the person.

    cli: AiActor.cli(), for scripts."""
    user_id: str | None
    channel: str
    cli: bool = False

    @classmethod
    def for_user(cls, user_id: str | None, channel: str) -> "AiActor":
        return cls(user_id=user_id, channel=channel)


def _cli_actor(cls) -> AiActor:
    """The company keys, as before P4: scripts only. Never for an HTTP
    request or the Telegram bot."""
    return cls(user_id=None, channel="cli", cli=True)


# Attached after the class is made: the field and the factory share a name
# (AiActor(..., cli=True), AiActor.cli()). An instance's own value hides the
# factory, so on an actor .cli is always the bool.
AiActor.cli = classmethod(_cli_actor)


@dataclass(frozen=True)
class AiKey:
    """One key a call may use. value is the key itself, for the SDK and
    nothing else: repr and str show only label ("own ..1234", "company
    #1"), which is what logs say. retrying: an own key out of quota for
    over QUOTA_RETRY_AFTER, tried again (a success puts it back to ok)."""
    value: str = field(repr=False)
    fingerprint: str
    source: str  # "own" | "company"
    label: str
    retrying: bool = False

    def __repr__(self) -> str:
        return f"AiKey({self.label})"

    def __str__(self) -> str:
        return self.label

    @property
    def rate_limit_id(self) -> str:
        """What its rate-limit window is named after (ai/rate_limit.py
        hashes it): a company key's value, so its window is the one it has
        always had; an own key's fingerprint, so not even a plain hash of
        the key is on disk."""
        return self.value if self.source == "company" else self.fingerprint


class AiUnavailable(Exception):
    """No key is left that this person's call may use. code is one of
    MESSAGES'; message is what they are told. Never holds a key. The API
    answers it with 409 {detail, code} (api/main.py)."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.message = message or MESSAGES.get(code) or MESSAGES[AI_KEY_MISSING]
        super().__init__(self.message)


def company_keys() -> list[AiKey]:
    """The company's keys, in the order a call tries them: those added on
    Settings > Company (oldest first), then GEMINI_API_KEY -- so an
    env-var-only deployment works untouched, and adding a key needs no
    redeploy. Each is its own allowance across every model in the chain.
    Read afresh every call, as before P4."""
    values: list[str] = []
    # Imported here rather than at module scope: app.reports.db makes the
    # engine, and this module is imported by every AI path.
    try:
        from app.reports.db import SessionLocal

        db = SessionLocal()
        try:
            rows = db.query(ApiKey.key).filter(ApiKey.provider == PROVIDER).order_by(ApiKey.created_at).all()
            values.extend(k for (k,) in rows if k)
        finally:
            db.close()
    except Exception:
        # A missing or locked table must never take down drafting: the
        # environment key still works.
        pass
    if settings.gemini_api_key and settings.gemini_api_key not in values:
        values.append(settings.gemini_api_key)
    return [
        AiKey(value=v, fingerprint=hashlib.sha256(v.encode("utf-8")).hexdigest(), source="company", label=f"company #{n}")
        for n, v in enumerate(values, start=1)
    ]


def _own_key(row: UserAiKey) -> tuple[AiKey | None, str | None]:
    """(this person's own key, None) when a call may use it, else (None,
    the AiUnavailable code saying why not). The secret first, as
    ai_key_status shows it: unreadable hides the stored verdict."""
    if secretbox.state() != secretbox.OK:
        return None, AI_KEY_UNREADABLE
    if not usable(row.status, row.last_checked_at):
        return None, _STATUS_CODES.get(row.status, AI_KEY_INVALID)
    try:
        value = unseal_key(row)
    except secretbox.Unreadable:
        return None, AI_KEY_UNREADABLE
    return AiKey(
        value=value, fingerprint=row.fingerprint, source="own", label=f"own ..{row.last4}", retrying=row.status == "quota"
    ), None


def resolve_keys(actor: AiActor) -> list[AiKey]:
    """The keys a call for this person tries, in order (the top of this
    file). Read afresh every time: one small query, and the company keys
    only for those allowed them. Raises AiUnavailable when there are none:
    a disabled or missing account (ai_account_off), else why their own key
    can't be used (ai_key_missing when they have none)."""
    if actor.cli:
        found = company_keys()
        if not found:
            raise AiUnavailable(AI_KEY_MISSING)
        return found
    if not actor.user_id:
        raise AiUnavailable(AI_ACCOUNT_OFF)
    from app.reports.db import SessionLocal

    db = SessionLocal()
    try:
        found = (
            db.query(User, UserAiKey)
            .outerjoin(UserAiKey, UserAiKey.user_id == User.id)
            .filter(User.id == actor.user_id)
            .first()
        )
    finally:
        db.close()
    if found is None or not found[0].active:
        raise AiUnavailable(AI_ACCOUNT_OFF)
    user, row = found
    own, problem = _own_key(row) if row is not None else (None, AI_KEY_MISSING)
    keys = [own] if own else []
    if may_use_company_key(user):
        keys += company_keys()
    if not keys:
        raise AiUnavailable(problem)
    return keys


def ensure_ai_ready(actor: AiActor) -> None:
    """resolve_keys' check, spending nothing, before anything that ends in a
    Gemini call. The routes ask after their view (404), edit (403) and
    input (400) checks, and before reserving a run or saving a file."""
    resolve_keys(actor)


def ai_allowed(user) -> bool:
    """GET /auth/me's ai_allowed: whether ensure_ai_ready would pass for
    this signed-in person -- an admin, ticked, or a working own key that
    can be read here (usable(): out of quota counts again after an hour).
    One small query, and only when not ticked."""
    if may_use_company_key(user):
        return True
    from app.reports.db import SessionLocal

    db = SessionLocal()
    try:
        row = db.query(UserAiKey.status, UserAiKey.last_checked_at).filter(UserAiKey.user_id == user.id).first()
    finally:
        db.close()
    return row is not None and usable(row.status, row.last_checked_at) and secretbox.state() == secretbox.OK


def http_status(exc: BaseException) -> int | None:
    """The HTTP status of an error from the Gemini SDK, else None. Two
    families: google.genai.errors.APIError (.code; client.models...) and
    the interactions client's own (BadRequestError, RateLimitError...;
    .status_code), which is NOT a subclass of it -- and client.interactions
    is what call_structured uses. Their text holds Google's JSON either way
    ("Error code: 400 - {'error': {... 'API_KEY_INVALID' ...}}")."""
    if not type(exc).__module__.startswith("google.genai"):
        return None
    for name in ("code", "status_code"):
        value = getattr(exc, name, None)
        if isinstance(value, int) and not isinstance(value, bool) and 100 <= value < 600:
            return value
    return None


def refusal(exc: BaseException) -> str | None:
    """What a failed call says about the key it was made with:

        "invalid"        Google refused the key: a 401, or a 400 or 403
                         about the key itself (_KEY_REFUSED, _KEY_FORBIDDEN)
        "quota"          a 429 for the day's allowance
        "no_allowance"   a 429 "limit: 0": none at all for this model
        None             anything else: busy, the per-minute window, a 403
                         for one model, a timeout, an answer that didn't parse
    """
    status = http_status(exc)
    if status is None:
        return None
    text = str(exc)
    if status == 401:
        return "invalid"
    if status == 400 and _KEY_REFUSED.search(text):
        return "invalid"
    if status == 403 and _KEY_FORBIDDEN.search(text):
        return "invalid"
    if status == 429:
        if _NO_ALLOWANCE.search(text):
            return "no_allowance"
        if _DAILY_QUOTA.search(text):
            return "quota"
    return None


def _touch_due(user_id: str, fingerprint: str) -> bool:
    now = time.monotonic()
    with _touched_lock:
        last = _touched.get((user_id, fingerprint))
        if last is not None and now - last < TOUCH_EVERY.total_seconds():
            return False
        if len(_touched) > 5000:
            _touched.clear()
        _touched[(user_id, fingerprint)] = now
        return True


def _google_said(error: BaseException | None) -> str | None:
    """'Google said: 400 API key not valid...' -- Google's own message from
    the error's JSON (.body on interactions errors, .details on the
    others), not the SDK's whole "Error code: ... {...}" text."""
    if error is None:
        return None
    code = http_status(error)
    if code is None:
        return f"Google said: {type(error).__name__}"
    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        body = getattr(error, "details", None)
    found = body.get("error") if isinstance(body, dict) else None
    words = (found.get("message") or found.get("status")) if isinstance(found, dict) else None
    return f"Google said: {code} {words or str(error)}"


def record_result(actor: AiActor, key: AiKey, outcome: str, error: BaseException | None = None) -> None:
    """Keeps what a call found out about an OWN key on its row; company keys
    are left as they always were.

        "ok"                 stamps last_used_at, at most every TOUCH_EVERY;
                             a key tried again after running out of quota
                             (key.retrying) goes back to status "ok"
        "invalid", "quota"   refusal()'s verdicts: the status, with Google's
                             words (scrubbed) as last_error. resolve_keys
                             leaves the key out -- an invalid one until it
                             is tested again on Settings > My AI key, one
                             out of quota for QUOTA_RETRY_AFTER
        "no_allowance"       no allowance on any model: stored as "invalid",
                             with NO_ALLOWANCE_ERROR (billing is the fix)

    Only while the key is still this person's (record_check's rule): one
    saved meanwhile mustn't get the old key's verdict. Never raises: a
    stamp that fails mustn't fail the call it is about."""
    if key.source != "own" or not actor.user_id or outcome not in ("ok", "invalid", "quota", "no_allowance"):
        return
    back = outcome == "ok" and key.retrying
    touch = outcome == "ok" and _touch_due(actor.user_id, key.fingerprint)
    if outcome == "ok" and not (back or touch):
        return
    from app.reports.db import SessionLocal

    db = SessionLocal()
    try:
        if outcome == "ok":
            now = utc_now()
            mine = (UserAiKey.user_id == actor.user_id, UserAiKey.fingerprint == key.fingerprint)
            if back:
                # It answered: the allowance is back. Only from "quota" --
                # a verdict that came in meanwhile stands.
                db.execute(
                    update(UserAiKey)
                    .where(*mine, UserAiKey.status == "quota")
                    .values(status="ok", last_error=None, last_checked_at=now)
                    .execution_options(synchronize_session=False)
                )
            if touch:
                db.execute(
                    update(UserAiKey)
                    .where(*mine, or_(UserAiKey.last_used_at.is_(None), UserAiKey.last_used_at < now - TOUCH_EVERY))
                    .values(last_used_at=now)
                    .execution_options(synchronize_session=False)
                )
        else:
            status = "invalid" if outcome == "no_allowance" else outcome
            why = NO_ALLOWANCE_ERROR if outcome == "no_allowance" else _google_said(error)
            if record_check(db, actor.user_id, key.fingerprint, status, scrub(why, key.value) if why else None):
                logger.warning("AI key %s of user %s marked %s by Google's answer (%s)", key.label, actor.user_id, status, outcome)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("Couldn't record %s for AI key %s (%s)", outcome, key.label, type(exc).__name__)
    finally:
        db.close()
