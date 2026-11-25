"""Background AI processing for a dashboard intake.

Two steps, run in a background thread so the browser can poll progress (and
leave and come back) instead of holding one request open for minutes:

  1. sorting  -- every unsorted photo, BATCH_SIZE per call, SORT_WORKERS
                 calls at a time (ai/triage.py)
  2. reading  -- one drafting call (ai/extraction.draft_report) on the photos
                 that matter most, picked by select_for_reading()

The api process is a single uvicorn worker (app/main.py), so an in-process
set is enough to stop the same draft being processed twice at once.

A run is for the person who started it (an AiActor: who, never a key),
handed by hand to the run's thread and to every sorting worker -- context
variables don't reach either -- and each Gemini call looks their keys up
again. When their AI stops being available mid-run (their key refused, an
admin's untick), the run fails with the reason's code in
IntakeSession.error_code, and error_by says whose run it was; photos
already sorted stay sorted.

Each stage is one row of the AI run log (ai/runs.py): "sort" (photos_sent
{"count": n}), and "read" -- "redraft" for a re-read -- with the labels sent;
the second look after a reading is a row of its own, "second_look".

Contract 8 D2: with settings.intake_read_mode "zones" the reading also reads
the car one area at a time after its one call, and merges what those calls
find into its own (intake/zones.py; their row is "read_zones").
Contract 9, option B (settings.intake_zone_only "suggest"): the parts only
the zone calls found are kept apart, in IntakeSession.ai_draft
["zone_suggestions"], and shown as suggestions (second_look._store) --
never in the damage list until a person adds one.
Contract 9, evidence: each finding -- list item or suggestion -- keeps
which photos show it, where, and in the AI's words (ai/evidence.py),
settled against the case's photos once the reads are in (_read_into).
"""
import copy
import logging
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.ai import allowance, check, evidence, link_check, runs, second_look
from app.ai.extraction import _model_chain as main_read_chain
from app.ai.extraction import draft_report
from app.ai.keys import AiActor, AiUnavailable, scrub
from app.ai.triage import BATCH_SIZE, clean_box, triage_photos
from app.config import settings
from app.intake import crops, geometry, zones
from app.intake.photos import ai_input_path, ordered_photos, photo_label
from app.parts import catalogue
from app.parts.prefill import item_key
from app.reports.db import SessionLocal
from app.reports.models import IntakePhoto, IntakeSession, Report
from app.reports.review import TEMPLATE_DEFAULTS, keep_checked, same_value, value_at
from app.reports.schema import SecurityIncidentDraft
from app.reports.taxonomy import UNDETERMINED_SUFFIX, body_type_for, sided_part, split_side, unsided_part

logger = logging.getLogger(__name__)

RUNNING_STAGES = {"sorting", "reading"}

# Filing (api/intake.finalize_intake) holds the stage "finalizing" for a
# second or two -- copying a 150-photo draft, well under a minute. A draft
# still "finalizing" after this long was being filed when the process died:
# it reads as failed (filing_interrupted) and the next File case claims it
# again. Before this it stayed locked for good: every upload, autosave and
# finalize answered 409, and the sweep skipped it forever.
FINALIZE_STALE_AFTER = timedelta(minutes=10)
FILING_INTERRUPTED = "filing_interrupted"
FILING_INTERRUPTED_MESSAGE = "Filing stopped before it finished (the server restarted). Press File case again."
RUN_INTERRUPTED_MESSAGE = (
    "Processing stopped before finishing (the server restarted). "
    "Press Retry to continue -- photos already sorted are kept."
)


def finalize_cutoff(now: datetime | None = None) -> datetime:
    """A "finalizing" stage that began before this is stale (naive UTC, as
    SQLite hands DateTime columns back)."""
    now = now or datetime.now(timezone.utc)
    return (now - FINALIZE_STALE_AFTER).replace(tzinfo=None)


def filing_interrupted(session: IntakeSession, now: datetime | None = None) -> bool:
    """Whether this draft's filing was cut off (see FINALIZE_STALE_AFTER).
    When the stage began: stage_started_at, which every claim sets
    (api/intake._claim_stage); a draft left "finalizing" by a version from
    before that falls back to updated_at."""
    if session.stage != "finalizing":
        return False
    since = session.stage_started_at or session.updated_at
    if since is None:
        return True
    since = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    return since < (now or datetime.now(timezone.utc)) - FINALIZE_STALE_AFTER


def shown_state(session: IntakeSession, now: datetime | None = None) -> tuple[str, str | None, str | None, str | None]:
    """(stage, error, error_code, error_by) as the wizard shows the draft
    (GET /intake/{id}); GET /reports' intake_stage and intake_error_code
    read the same, so the case list never calls "working" a draft the
    wizard calls failed."""
    # Left mid-run by a restart: the thread is gone but the row still says
    # it's working, and the wizard would poll forever. A run in the Telegram
    # bot's process (contract 10) is alive while it keeps beating (is_live).
    if session.stage in RUNNING_STAGES and not is_live(session, now):
        return "failed", RUN_INTERRUPTED_MESSAGE, None, None
    # Left mid-filing by a restart: failed, with File case enabled again
    # (the wizard words filing_interrupted and opens on the File case step).
    if filing_interrupted(session, now):
        return "failed", FILING_INTERRUPTED_MESSAGE, FILING_INTERRUPTED, None
    return session.stage, session.error, session.error_code, session.error_by


# Photos that carry facts rather than damage, taken first. Documents are the
# only reliable source of plate, VIN and make -- the facts parts lookup will
# depend on -- and there are rarely more than a few. One VIN plate, odometer
# or tyre photo says what a second one would: the old budget spent 2 VIN
# plates, 2 tyres and 4 near-identical rear overviews on TST 1001 A while 22
# of its 37 damage photos -- the tailgate's worst buckling, the cracked tail
# lamp -- were never read.
FACT_BUDGET: list[tuple[str, int]] = [
    ("log_card", 3),
    ("vin_plate", 1),
    ("police_report", 3),
    ("odometer", 1),
    ("document", 2),
    ("third_party", 2),
    ("tyre", 1),
]
OVERVIEW_BUDGET = 3
# Photos sorting skipped (a partial batch answer). Better a few of them than
# none, if a sorting call came back thin.
UNSORTED_BUDGET = 4
# Everything else goes to damage. ~32 x 200KB previews is ~8.5MB of base64 --
# still one request, and the strongest model answers well inside the
# 120-second reading timeout. The default of the intake_max_read_photos
# setting (read_limit): 40 is ~10.7MB, still one request.
MAX_READ_PHOTOS = 32
# E12 (plans/2026-11-22-enhance-staging 3c): with intake_max_read_photos
# above MAX_READ_PHOTOS, the photos past today's first 32 are added only
# while the request stays under this many bytes (the photos and close-ups as
# sent, base64): Gemini takes at most 20 MB in one inline request, the prompt
# and the notes included, and 64 previews of ~200 KB would pass it. The
# first 32 are picked exactly as today, whatever their size; a later photo
# that would pass the budget is skipped and the next one tried (the review's
# M2). At 32 or fewer photos nothing is counted -- the choice is as before.
READ_BYTES_BUDGET = 18_000_000
# The review's M3: with settings.intake_read_crops, each close-up the read
# may send (intake_read_crops_max) is counted at this many bytes before the
# extra photos are picked -- a 1600 px JPEG at quality 85 (crops.MAX_EDGE,
# QUALITY) is ~0.3-0.8 MB as base64 -- and the close-ups made are measured
# again after (fit_crops).
CROP_RESERVE_BYTES = 800_000
# The review's L5: the main read's try waits READ_TIMEOUT, plus this much for
# each photo past MAX_READ_PHOTOS, at most READ_TIMEOUT_MAX.
READ_TIMEOUT = 120.0
READ_TIMEOUT_PER_EXTRA = 1.5
READ_TIMEOUT_MAX = 180.0
# Kinds whose photo still counts as damage evidence when it shows damage and
# names the damaged part (e.g. a boot photo the sorter called interior).
# An "other" photo showing damage counts with no part too: the sorter can't
# name a part outside the list (triage._clean blanks "Other / Not Listed"),
# so a removed, bent window regulator was never read.
_DAMAGE_EVIDENCE_KINDS = {"interior", "other"}
# The photos of the insured car that aren't damage close-ups, and the rest,
# in the order they fill slots the budgets leave (_top_up_order).
_CAR_KINDS = ("overview", "tyre", "interior", "other")
_LAST_KINDS = ("third_party", "scene", "document", "log_card", "police_report", "vin_plate", "odometer")

# Sorting batches in flight at once. Each is one lite-model call of about a
# minute, so one after another a 65-photo case took ~7 minutes. Three at a
# time stays well inside the lite models' per-minute limits, and
# call_structured backs off on a 429 anyway.
SORT_WORKERS = 3

# Typed by the reporter at step 1; the drafting call can't know them better.
REPORTER_FIELDS = ("reporter_name", "reporter_role", "reporter_contact", "reporter_department", "reporter_email")

_LABEL_RE = re.compile(r"^P0*(\d+)$")

_running: set[str] = set()
_lock = threading.Lock()


def is_running(report_id: str) -> bool:
    with _lock:
        return report_id in _running


def reserve(report_id: str) -> bool:
    """Marks a run as in progress before its stage is committed, so a GET
    landing in between never sees "sorting" with no run behind it (which
    reads as "the server restarted"). False if one is already running."""
    with _lock:
        if report_id in _running:
            return False
        _running.add(report_id)
        return True


def release(report_id: str) -> None:
    """Undoes reserve() when the run never got started."""
    with _lock:
        _running.discard(report_id)


# Contract 10 (T-report H1): a run in ANOTHER process -- the Telegram bot's
# (settings.telegram_full_detection), which shares the database but not
# _running -- beats IntakeSession.run_heartbeat_at while it lives
# (keep_beating). The api counts a running stage as alive while that beat
# is fresh, so it neither calls the bot's run "stopped (server restarted)"
# nor lets Retry, an autosave, a decision or File case in meanwhile. A run
# here never beats: _running already says it lives, and with the setting
# off the column stays empty.
RUN_HEARTBEAT_SECONDS = 30.0
RUN_HEARTBEAT_STALE = timedelta(seconds=90)


def beat(report_id: str) -> None:
    """The draft's run_heartbeat_at, now -- only while its stage is a running
    one. Never raises: a busy database skips one beat."""
    from sqlalchemy import update

    db = SessionLocal()
    try:
        db.execute(
            update(IntakeSession)
            .where(IntakeSession.report_id == report_id, IntakeSession.stage.in_(RUNNING_STAGES))
            .values(run_heartbeat_at=datetime.now(timezone.utc))
            .execution_options(synchronize_session=False)
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.debug("Intake run %s: heartbeat skipped (%s)", report_id, type(exc).__name__)
    finally:
        db.close()


def keep_beating(report_id: str, every: float | None = None) -> threading.Thread:
    """Beats the draft's heartbeat (beat) at once and then every `every`
    seconds for as long as this process runs it (is_running) -- tied to the
    run, not to whoever waits for it: the bot's chat watcher gives up after
    20 minutes, a slow free-tier run may not. For a run started in a process
    other than the api's (intake/telegram_bridge.start)."""
    every = RUN_HEARTBEAT_SECONDS if every is None else every

    def loop() -> None:
        while is_running(report_id):
            beat(report_id)
            waited = 0.0
            while waited < every and is_running(report_id):
                step = min(0.5, every - waited)
                time.sleep(step)
                waited += step

    thread = threading.Thread(target=loop, name=f"intake-beat-{report_id}", daemon=True)
    thread.start()
    return thread


def running_elsewhere(session: IntakeSession, now: datetime | None = None) -> bool:
    """A running stage with no run in THIS process, whose run in another
    process (the Telegram bot) beat within RUN_HEARTBEAT_STALE."""
    if session.stage not in RUNNING_STAGES or is_running(session.report_id):
        return False
    beat_at = getattr(session, "run_heartbeat_at", None)
    if beat_at is None:
        return False
    beat_at = beat_at if beat_at.tzinfo else beat_at.replace(tzinfo=timezone.utc)
    return beat_at >= (now or datetime.now(timezone.utc)) - RUN_HEARTBEAT_STALE


def is_live(session: IntakeSession, now: datetime | None = None) -> bool:
    """Whether an AI run on this draft is alive in any process: here
    (is_running), or in another one that keeps beating (running_elsewhere).
    What the api asks wherever it asked is_running about a draft."""
    return is_running(session.report_id) or running_elsewhere(session, now)


def start_reserved(report_id: str, mode: str, actor: AiActor, on_done: Callable[[], None] | None = None) -> None:
    """mode "all" sorts unsorted photos then reads; "read" only re-reads
    (after the surveyor regrouped photos). actor: who pressed the button,
    and pays. on_done runs when the run ends, however it ends (api/intake
    releases the person's one AI request at a time). Call reserve() first."""
    threading.Thread(
        target=_run, args=(report_id, mode, actor, on_done), name=f"intake-{report_id}", daemon=True
    ).start()


def _set_stage(
    report_id: str,
    stage: str,
    done: int = 0,
    total: int = 0,
    error: str | None = None,
    error_code: str | None = None,
    error_by: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        session = db.get(IntakeSession, report_id)
        if session:
            if session.stage != stage:
                session.stage_started_at = datetime.now(timezone.utc)
            session.stage = stage
            session.progress_done = done
            session.progress_total = total
            session.error = error
            session.error_code = error_code
            session.error_by = error_by
            db.commit()
    finally:
        db.close()


def _friendly_error(exc: Exception) -> str:
    """What the wizard shows. Says what actually went wrong: a blanket "quota
    is used up" for every failure sent people to Retry even when the request
    itself was being rejected (the 400s before 877d99d), where Retry can
    never help."""
    if isinstance(exc, AiUnavailable):
        return exc.message
    if isinstance(exc, RuntimeError) and "fallback models" in str(exc) and allowance.day_spent_error(exc):
        return (
            "Today's free AI allowance is used up on every model this key can use. Your photos are saved: "
            "it works again after Google's daily reset, at 4 pm Malaysia time (3 pm from mid-March to early November)."
        )
    if isinstance(exc, RuntimeError) and "fallback models" in str(exc):
        cause = str(exc.__cause__ or "")
        if any(s in cause for s in ("429", "RESOURCE_EXHAUSTED", "quota", "503", "high demand")):
            return (
                "The AI is busy or today's quota is used up. Your photos are saved -- "
                "press Retry in a few minutes."
            )
        # Google's words, shown to the surveyor: scrubbed of anything key-like.
        return f"The AI rejected the request on every model ({scrub(cause) or 'no detail'}). Retrying won't help; please report this."
    if isinstance(exc, FileNotFoundError):
        return "A photo was removed while the AI was working. Press Retry."
    return f"AI processing failed: {exc}"[:300]


def _run(report_id: str, mode: str, actor: AiActor, on_done: Callable[[], None] | None = None) -> None:
    def done() -> None:
        # Once, and before the final stage is written: whoever sees the
        # result may start their next AI request at once.
        nonlocal on_done
        callback, on_done = on_done, None
        if callback is not None:
            try:
                callback()
            except Exception:
                logger.exception("Intake run %s: on_done failed", report_id)

    try:
        if mode == "all":
            _sort(report_id, actor)
        # A re-read (mode "read", after the surveyor regrouped photos)
        # merges into the form the surveyor has been editing, like a
        # redraft: the run log calls it one (ai/runs.py).
        _read(report_id, actor, kind="read" if mode == "all" else "redraft")
        done()
        _set_stage(report_id, "ready", 1, 1)
    except AiUnavailable as exc:
        # Not a fault: the person's AI stopped being available (their key
        # refused or out of quota, an untick). The wizard says how to fix it.
        logger.warning("Intake processing for %s stopped: %s", report_id, exc.code)
        done()
        _set_stage(report_id, "failed", error=_friendly_error(exc), error_code=exc.code, error_by=actor.user_id)
    except Exception as exc:
        logger.exception("Intake processing failed for %s", report_id)
        done()
        _set_stage(report_id, "failed", error=_friendly_error(exc), error_by=actor.user_id)
    finally:
        with _lock:
            _running.discard(report_id)
        done()


def _sort(report_id: str, actor: AiActor) -> None:
    db = SessionLocal()
    try:
        photos = ordered_photos(db, report_id)
        todo = [
            (photo_label(i), p.id, ai_input_path(p.path))
            for i, p in enumerate(photos)
            if p.kind is None
        ]
    finally:
        db.close()

    total = len(todo)
    _set_stage(report_id, "sorting", 0, total)
    done = 0
    sent = 0
    done_lock = threading.Lock()
    # F7 (settings.intake_sort_retry_skipped "on"): the photos a batch's
    # answer left out, for one more batch at the end. None while off.
    skipped: list[tuple[str, str, str]] | None = [] if sort_retry_on() else None

    def sort_batch(batch: list[tuple[str, str, str]], actor: AiActor, attempts: runs.Attempts) -> None:
        nonlocal done, sent
        # Paths were captured at the start of the run; a photo deleted since
        # is skipped rather than failing the whole run on a missing file.
        batch = [item for item in batch if Path(item[2]).exists()]
        if not batch:
            return
        with done_lock:
            sent += len(batch)
        # This worker's Gemini tries go to the stage's one run.
        with runs.collecting(attempts):
            result = triage_photos([(label, path) for label, _, path in batch], actor=actor)
        db = SessionLocal()
        try:
            for label, photo_id, path in batch:
                found = result.get(label)
                photo = db.get(IntakePhoto, photo_id)
                # Deleted mid-run, or regrouped by the surveyor meanwhile.
                if not found or photo is None or photo.kind_source == "surveyor":
                    if skipped is not None and not found and photo is not None and photo.kind_source != "surveyor":
                        with done_lock:
                            skipped.append((label, photo_id, path))
                    continue
                photo.kind = found["kind"]
                photo.area = found["area"]
                photo.quality = found["quality"]
                photo.shows_damage = found["shows_damage"]
                photo.part = found["part"]
                photo.note = found["note"]
                # Only a sort asked for boxes says anything about one
                # (settings.intake_sort_damage_box).
                if "damage_box" in found:
                    photo.damage_box = found["damage_box"]
                # E7: only a sort asked for the geometry says anything of it
                # (settings.intake_side_geometry).
                if "geometry" in found:
                    photo.geometry = found["geometry"]
            with done_lock:
                done += len(batch)
                progress = done
            session = db.get(IntakeSession, report_id)
            if session:
                session.progress_done = min(total, progress)
            db.commit()
        finally:
            db.close()

    batches = [todo[offset:offset + BATCH_SIZE] for offset in range(0, total, BATCH_SIZE)]
    if not batches:
        return  # all sorted already (a Retry after the reading failed): no AI call, no run
    # One run for the whole stage, not one per batch: a run is what one
    # press of Start or Retry did, and the batches run at once. photos_sent
    # is {"count": n}, summed over the batches that went out.
    with runs.recorded(report_id, "sort", actor) as run:
        # The actor (and the run's collector) go to each worker as
        # arguments: a pool thread sees nothing of the thread that
        # submitted to it.
        with ThreadPoolExecutor(max_workers=sort_workers(), thread_name_prefix=f"sort-{report_id}") as pool:
            futures = [pool.submit(sort_batch, batch, actor, run.attempts) for batch in batches]
        run.photos_sent = {"count": sent}
        # A failed batch fails the run, but every batch that finished stays
        # sorted -- the others are left to complete rather than cancelled --
        # so Retry only sorts what's left. The person's AI going away is the
        # failure to report when it is among them: it says what to fix.
        failures = [future.exception() for future in futures if future.exception()]
        if failures:
            raise next((e for e in failures if isinstance(e, AiUnavailable)), failures[0])
        if skipped:
            # F7: one more small batch, in camera order, at most BATCH_SIZE
            # photos -- one call. It never fails the run: a photo it can't
            # sort stays unsorted, as it would have.
            order = {photo_id: i for i, (_, photo_id, _) in enumerate(todo)}
            again = sorted(skipped, key=lambda item: order.get(item[1], 0))[:BATCH_SIZE]
            logger.info("Sorting %s: %d skipped photo(s) sent again", report_id, len(again))
            try:
                sort_batch(again, actor, run.attempts)
            except Exception as exc:
                logger.warning("Sorting %s: the skipped photos stay unsorted (%s)", report_id, type(exc).__name__)
            run.photos_sent = {"count": sent}
        if sort_rescue_on():
            # B9: one more short call for the photos no read would see.
            rescue = _rescue_sort(report_id, actor, todo)
            if rescue is not None:
                run.photos_sent = {"count": sent + rescue["asked"], "rescue": rescue}


def sort_retry_on() -> bool:
    """F7: settings.intake_sort_retry_skipped is "on"."""
    return getattr(settings, "intake_sort_retry_skipped", "off") == "on"


def sort_rescue_on() -> bool:
    """B9: settings.intake_sort_rescue is "on"."""
    return getattr(settings, "intake_sort_rescue", "off") == "on"


# B9: the sorter's kinds whose photos no zone read sees and the main read
# only takes when slots are left (zones.NO_ZONE_KINDS) -- the paperwork the
# main read needs (log card, VIN plate, police report, odometer) is not
# among them.
RESCUE_KINDS = frozenset({"interior", "document", "third_party", "scene"})


# B9: the rescue sort's mark at the start of a photo's note; api/intake's
# regroup (B5) reads it, so a rescued photo's regroup counts against the
# rescue sort, not the sorter.
RESCUE_NOTE_PREFIX = "Rescue sort:"


def rescue_rank(photo) -> int:
    """B9's order before the cut at BATCH_SIZE (the review's L5): the photos
    likeliest to hide damage first -- unsorted (a batch skipped it),
    unusable, "other" with no damage seen -- then interior, document, third
    party and scene."""
    if photo.kind is None:
        return 0
    if photo.quality == "unusable":
        return 1
    if photo.kind == "other":
        return 2
    return 3


def rescue_candidate(photo) -> bool:
    """B9: a photo the sort left where no read would look for damage --
    unsorted (a batch skipped it), unusable, one of RESCUE_KINDS, or "other"
    with no damage seen. Never an excluded photo or one a person regrouped."""
    if photo.excluded or photo.kind_source == "surveyor":
        return False
    if photo.kind is None or photo.quality == "unusable" or photo.kind in RESCUE_KINDS:
        return True
    return photo.kind == "other" and not photo.shows_damage


def _rescue_sort(report_id: str, actor: AiActor, todo: list[tuple[str, str, str]]) -> dict | None:
    """B9, on the sort run's thread (its tries go to that run): the photos
    sorted in this run that rescue_candidate picks, in camera order, at most
    BATCH_SIZE, in one triage.rescue_photos call; each it says shows damage
    on the insured car becomes a damage photo of the area it names (an
    unusable one "blurry", the first kind in its note). Never fails the
    sort: None when there was nothing to ask, else {"asked", "rescued"} (and
    "failed" when the call failed)."""
    from app.ai.triage import rescue_photos

    db = SessionLocal()
    try:
        ranked = []
        for n, (label, photo_id, path) in enumerate(todo):
            photo = db.get(IntakePhoto, photo_id)
            if photo is not None and Path(path).exists() and rescue_candidate(photo):
                ranked.append((rescue_rank(photo), n, (label, photo_id, path)))
        # The likeliest first (rescue_rank), at most BATCH_SIZE, then sent
        # in camera order.
        chosen = sorted(sorted(ranked)[:BATCH_SIZE], key=lambda r: r[1])
        asked = [entry for _, _, entry in chosen]
    finally:
        db.close()
    if not asked:
        return None
    logger.info("Sorting %s: %d photo(s) no read would see sent to the rescue sort", report_id, len(asked))
    try:
        answers = rescue_photos([(label, path) for label, _, path in asked], actor=actor)
    except Exception as exc:
        logger.warning("Sorting %s: the rescue sort failed (%s); the photos stay as sorted", report_id,
                       type(exc).__name__)
        return {"asked": len(asked), "rescued": 0, "failed": True}
    rescued = 0
    db = SessionLocal()
    try:
        for label, photo_id, _ in asked:
            answer = answers.get(label)
            photo = db.get(IntakePhoto, photo_id)
            if not answer or not answer["damage"] or photo is None or not rescue_candidate(photo):
                continue
            was = (photo.kind or "unsorted") + (", unusable" if photo.quality == "unusable" else "")
            if photo.kind == "third_party":
                photo.part = None  # the other vehicle's part, not ours
            photo.kind = "damage"
            photo.shows_damage = True
            photo.area = answer["area"] or (photo.area if photo.area != "interior" else None)
            photo.quality = "blurry" if photo.quality == "unusable" else (photo.quality or "good")
            photo.note = f"{RESCUE_NOTE_PREFIX} damage on the insured car (first sorted: {was})"
            rescued += 1
        db.commit()
    finally:
        db.close()
    logger.info("Sorting %s: the rescue sort put %d of %d photo(s) back", report_id, rescued, len(asked))
    return {"asked": len(asked), "rescued": rescued}


def sort_workers() -> int:
    """B4: sorting batches in flight at once -- settings.intake_sort_workers
    (1 to config.MAX_SORT_WORKERS), else SORT_WORKERS. Each batch's request
    still waits for the rate limiter (rate_limit.acquire), so five starts
    arrive spaced and under the per-minute limit."""
    from app.config import MAX_SORT_WORKERS

    value = getattr(settings, "intake_sort_workers", SORT_WORKERS)
    return value if isinstance(value, int) and 1 <= value <= MAX_SORT_WORKERS else SORT_WORKERS


def _spread_order(photos: list[IntakePhoto]) -> list[IntakePhoto]:
    """Reorders photos that are in camera order so that the first k of them
    are spread over the whole sequence: first, last, middle, then the
    quarters... Taking a group's earliest photos read only what was shot
    first -- on TST 1001 A the tailgate photos taken with the boot open,
    later in the walk-around, were never sent."""
    n = len(photos)
    if n <= 2:
        return list(photos)
    picked = [0, n - 1]
    seen = {0, n - 1}
    gaps = [(0, n - 1)]
    while gaps:
        next_gaps = []
        for lo, hi in gaps:
            if hi - lo < 2:
                continue
            mid = (lo + hi) // 2
            if mid not in seen:
                seen.add(mid)
                picked.append(mid)
            next_gaps += [(lo, mid), (mid, hi)]
        gaps = next_gaps
    return [photos[i] for i in picked]


def _round_robin(groups: list[list[IntakePhoto]]) -> list[IntakePhoto]:
    queues = [list(g) for g in groups]
    out: list[IntakePhoto] = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out


def _by_tiers(photos: list[IntakePhoto]) -> list[IntakePhoto]:
    """Sharp photos showing damage first, blurry or damage-free ones after;
    each tier spread over the camera sequence."""
    tiers: dict[tuple[bool, bool], list[IntakePhoto]] = {}
    for p in photos:
        tiers.setdefault((not p.shows_damage, p.quality == "blurry"), []).append(p)
    return [p for key in sorted(tiers) for p in _spread_order(tiers[key])]


def _damage_order(pool: list[IntakePhoto]) -> list[IntakePhoto]:
    """Every damaged part gets a photo before any part gets a second one.
    Photos are grouped by the part the sorter named, falling back to the
    area when it named none; groups whose photos show damage come first."""
    groups: dict[str, list[IntakePhoto]] = {}
    for p in pool:
        groups.setdefault(p.part or f"area:{p.area}", []).append(p)
    ordered = sorted(groups.values(), key=lambda g: (not any(p.shows_damage for p in g), g[0].position))
    showing = _round_robin([_by_tiers([p for p in g if p.shows_damage]) for g in ordered])
    rest = _round_robin([_by_tiers([p for p in g if not p.shows_damage]) for g in ordered])
    return showing + rest


def read_limit() -> int:
    """How many photos one reading sends: the intake_max_read_photos setting
    when there is one, else MAX_READ_PHOTOS. Read per call, so a test or
    the benchmark can try 40 without a restart."""
    return getattr(settings, "intake_max_read_photos", None) or MAX_READ_PHOTOS


def _is_damage_evidence(p: IntakePhoto) -> bool:
    if p.kind == "damage":
        return True
    if not p.shows_damage or p.kind not in _DAMAGE_EVIDENCE_KINDS:
        return False
    return p.kind == "other" or bool(p.part)


def _top_up_order(
    usable: list[IntakePhoto],
    by_kind: dict[str, list[IntakePhoto]],
    damage_pool: list[IntakePhoto],
    unsorted: list[IntakePhoto],
) -> list[IntakePhoto]:
    """Every usable photo, in the order it fills the slots left after the
    budgets: the rest of the damage, then any other photo of the insured car
    that shows damage, then the rest of the car (overviews, unsorted, tyre,
    interior, other), and the other party's car, the scene and paperwork
    last. Damage first, so that a light reader or a smaller limit still
    gets the damage. Before this only damage, overview and unsorted photos
    topped up, and a case with few damage photos left slots empty: a
    right-side case read 28 of its 41 photos -- none of its 8 "other", 1 of
    its 6 tyre close-ups (the wheel-arch trim's scrape was at the edge of
    one) -- with 4 slots unused."""
    showing = [p for p in usable if p.shows_damage and p.kind in _CAR_KINDS]
    rest_of_car = [p for kind in ("tyre", "interior", "other") for p in _by_tiers(by_kind.get(kind, []))]
    last = [p for kind in _LAST_KINDS for p in _by_tiers(by_kind.get(kind, []))]
    # usable at the end: a kind none of the lists names still goes.
    return (
        _damage_order(damage_pool) + _damage_order(showing)
        + _spread_order(by_kind.get("overview", [])) + unsorted + rest_of_car + last + usable
    )


def _file_bytes(path) -> int:
    """A file as base64 sends it; 0 when it is gone."""
    try:
        return (Path(path).stat().st_size + 2) // 3 * 4
    except (OSError, TypeError, ValueError):
        return 0


def _sent_bytes(photo: IntakePhoto) -> int:
    """What one photo adds to the reading's request: its AI input (the 1600
    px preview, else the original) as base64. 0 when the file is gone (it
    can't be sent anyway)."""
    try:
        return _file_bytes(ai_input_path(photo.path))
    except (OSError, TypeError, ValueError):
        return 0


def read_bytes_budget() -> int:
    """E12's budget for the photos (READ_BYTES_BUDGET), less the close-ups'
    reserve while settings.intake_read_crops is on (the review's M3)."""
    reserve = 0
    if getattr(settings, "intake_read_crops", False):
        reserve = max(0, int(getattr(settings, "intake_read_crops_max", 0) or 0)) * CROP_RESERVE_BYTES
    return max(0, READ_BYTES_BUDGET - reserve)


def read_timeout(photos: int) -> float:
    """The main read's try timeout for this many photos: READ_TIMEOUT up to
    MAX_READ_PHOTOS (as before), then READ_TIMEOUT_PER_EXTRA more for each
    photo past it, at most READ_TIMEOUT_MAX (the review's L5)."""
    extra = max(0, photos - MAX_READ_PHOTOS)
    return min(READ_TIMEOUT_MAX, READ_TIMEOUT + READ_TIMEOUT_PER_EXTRA * extra) if extra else READ_TIMEOUT


def select_for_reading(photos: list[IntakePhoto], *, priority_order: bool = False) -> list[IntakePhoto]:
    """The photos the drafting call sees, in case order: up to read_limit(),
    every slot filled while usable photos are left (_top_up_order). Excluded
    and unusable photos never go; blurry ones only after sharp ones of the
    same kind.

    The priority order the slots fill in (E12 documents it; a higher
    intake_max_read_photos only takes further down the same order):
      1. fact photos by FACT_BUDGET -- log card 3, VIN plate 1, police
         report 3, odometer 1, document 2, the other party's car 2, tyre 1
         (at most 13; sharp before blurry, spread over the camera order);
      2. OVERVIEW_BUDGET (3) overviews, one per area in turn;
      3. damage evidence (_is_damage_evidence: damage close-ups, and
         interior / "other" photos showing damage) in _damage_order --
         every damaged part one photo before any part a second, the groups
         showing damage first -- taking every slot but the unsorted ones';
      4. up to UNSORTED_BUDGET (4) unsorted photos, spread;
      5. slots still left: _top_up_order (the rest of the damage, other car
         photos showing damage, overviews, unsorted, tyre / interior /
         other, then the other party's car, the scene and paperwork).
    Above MAX_READ_PHOTOS (E12, the review's M2): the first MAX_READ_PHOTOS
    are exactly today's (_priority_pick at 32, whatever their size); the
    rest follow the order a pick of read_limit() photos takes (step 3 then
    takes further down the same damage order), then any usable photo left,
    each only while the request stays within read_bytes_budget() -- one that
    would pass it is skipped and the next tried (_extra_photos). At 32 or
    fewer nothing is measured.
    Detection Round 1, A3 (settings.intake_read_pick "v2"): steps 3 and 5
    as _priority_pick_v2 -- every damaged area a photo, at most
    READ_GROUP_MAX damage photos per sorter (part, area) while other
    photos of the insured car are left; the same limit. "v1": exactly as
    above.
    priority_order: the chosen photos in the order they were taken (for the
    tests and the benchmark's notes), not in case order."""
    cap = read_limit()
    chosen = _priority_pick(photos, min(cap, MAX_READ_PHOTOS))
    if cap > MAX_READ_PHOTOS:
        chosen = chosen + _extra_photos(photos, chosen, cap)
    if priority_order:
        return chosen
    order = {p.id: i for i, p in enumerate(photos)}
    return sorted(chosen, key=lambda p: order[p.id])


def _extra_photos(photos: list[IntakePhoto], first: list[IntakePhoto], cap: int) -> list[IntakePhoto]:
    """E12: the photos past today's first ones, up to cap in all: in the
    order a pick of `cap` photos takes them, then every other usable photo
    in an unlimited pick's order; one that would take the request past
    read_bytes_budget() is skipped (logged), never ending the choice."""
    budget = read_bytes_budget()
    spent = sum(_sent_bytes(p) for p in first)
    taken = {p.id for p in first}
    out: list[IntakePhoto] = []
    skipped = 0
    for p in _priority_pick(photos, cap) + _priority_pick(photos, len(photos)):
        if len(first) + len(out) >= cap:
            break
        if p.id in taken:
            continue
        taken.add(p.id)
        size = _sent_bytes(p)
        if spent + size > budget:
            skipped += 1
            continue
        spent += size
        out.append(p)
    if skipped:
        logger.warning("Reading: %d photo(s) left out of the %d-photo read to stay under %.1f MB (%d taken, %.1f MB)",
                       skipped, cap, budget / 1e6, len(first) + len(out), spent / 1e6)
    return out


def fit_crops(photo_bytes: int, made: dict[str, str]) -> dict[str, str]:
    """E12 with close-ups (the review's M3): the close-ups `made` (best
    first) that keep the request -- photo_bytes of photos, then each
    close-up as sent -- within READ_BYTES_BUDGET; the rest are left out."""
    out: dict[str, str] = {}
    total = photo_bytes
    for photo_id, path in made.items():
        size = _file_bytes(path)
        if total + size > READ_BYTES_BUDGET:
            continue
        total += size
        out[photo_id] = path
    if len(out) < len(made):
        logger.warning("Reading: %d close-up(s) left out to stay under %.1f MB", len(made) - len(out),
                       READ_BYTES_BUDGET / 1e6)
    return out


def read_pick() -> str:
    """Detection Round 1, A3: settings.intake_read_pick -- "v1" (today's
    choice) or "v2" (_priority_pick_v2)."""
    return "v2" if getattr(settings, "intake_read_pick", "v1") == "v2" else "v1"


def _priority_pick(photos: list[IntakePhoto], cap: int) -> list[IntakePhoto]:
    """select_for_reading's choice of at most `cap` photos, in the priority
    order they are taken (its docstring), nothing measured. A3 "v2":
    _priority_pick_v2's instead."""
    if read_pick() == "v2":
        return _priority_pick_v2(photos, cap)
    usable = [p for p in sorted(photos, key=lambda p: p.position) if not p.excluded and p.quality != "unusable"]
    by_kind: dict[str, list[IntakePhoto]] = {}
    for p in usable:
        by_kind.setdefault(p.kind or "unsorted", []).append(p)

    damage_pool = [p for p in usable if _is_damage_evidence(p)]
    overviews = by_kind.get("overview", [])
    areas: dict[str | None, list[IntakePhoto]] = {}
    for p in overviews:
        areas.setdefault(p.area, []).append(p)

    chosen: list[IntakePhoto] = []
    chosen_ids: set[str] = set()

    def take(candidates: list[IntakePhoto], limit: int) -> None:
        for p in candidates:
            if limit <= 0 or len(chosen) >= cap:
                return
            if p.id not in chosen_ids:
                chosen.append(p)
                chosen_ids.add(p.id)
                limit -= 1

    for kind, limit in FACT_BUDGET:
        take(_by_tiers(by_kind.get(kind, [])), limit)
    take(_round_robin([_by_tiers(g) for g in areas.values()]), OVERVIEW_BUDGET)
    unsorted = _spread_order(by_kind.get("unsorted", []))
    take(_damage_order(damage_pool), cap - len(chosen) - min(UNSORTED_BUDGET, len(unsorted)))
    take(unsorted, UNSORTED_BUDGET)
    # Room left over (a case with few damage photos): every other photo.
    take(_top_up_order(usable, by_kind, damage_pool, unsorted), cap)
    return chosen


# Detection Round 1, A3 "v2" (settings.intake_read_pick): at most this many
# damage photos of one sorter (part, area) go to the main read while other
# photos of the insured car are left. On a practice case 9 of the 32 slots
# went to near-identical photos of one removed part, and 4 wheel and arch
# overviews were never read.
READ_GROUP_MAX = 2


def _read_group(p: IntakePhoto) -> tuple:
    """A3 v2's group: the sorter's (part, area)."""
    return (p.part, p.area)


def _shows_any(p: IntakePhoto) -> bool:
    return p.kind == "damage" or bool(p.shows_damage)


def _group_counted(p: IntakePhoto) -> bool:
    """A photo READ_GROUP_MAX counts: damage evidence (_is_damage_evidence),
    or another photo of the insured car showing damage (an overview, a
    tyre)."""
    return _is_damage_evidence(p) or (bool(p.shows_damage) and p.kind in _CAR_KINDS)


def _best_first(group: list[IntakePhoto]) -> list[IntakePhoto]:
    """A3 v2: a group's photos, damage close-ups first, then those showing
    damage, sharp before blurry; each tier spread over the camera order."""
    tiers: dict[tuple[bool, bool, bool], list[IntakePhoto]] = {}
    for p in group:
        tiers.setdefault((p.kind != "damage", not p.shows_damage, p.quality == "blurry"), []).append(p)
    return [p for key in sorted(tiers) for p in _spread_order(tiers[key])]


def _priority_pick_v2(photos: list[IntakePhoto], cap: int) -> list[IntakePhoto]:
    """A3 "v2": select_for_reading's steps (its docstring) with the damage
    photos chosen another way, at most `cap` as v1:
      1-2. the fact photos and the overviews as v1;
      3a. every area with a photo of the insured car showing damage gets
          one -- its best (_best_first) -- unless a photo chosen already
          shows damage there;
      3b. then the damage evidence by the sorter's (part, area): the groups
          showing damage first, in camera order, one photo each in turn
          (_best_first), at most READ_GROUP_MAX a group (3a's photo counts);
          3a and 3b share v1's damage budget (every slot but the unsorted
          ones');
      4. the unsorted as v1;
      5. the slots left: v1's top-up order (_top_up_order), but a damage
         photo of a group that has READ_GROUP_MAX already goes after every
         other photo of the insured car, and before the other party's car,
         the scene and paperwork (_LAST_KINDS, past their fact budgets:
         nothing there for the damage) -- so no slot v1 would fill is left
         empty.
    The priority order they were taken in, as _priority_pick's."""
    usable = [p for p in sorted(photos, key=lambda p: p.position) if not p.excluded and p.quality != "unusable"]
    by_kind: dict[str, list[IntakePhoto]] = {}
    for p in usable:
        by_kind.setdefault(p.kind or "unsorted", []).append(p)

    damage_pool = [p for p in usable if _is_damage_evidence(p)]
    areas: dict[str | None, list[IntakePhoto]] = {}
    for p in by_kind.get("overview", []):
        areas.setdefault(p.area, []).append(p)

    chosen: list[IntakePhoto] = []
    chosen_ids: set[str] = set()
    per_group: Counter = Counter()

    def add(p: IntakePhoto) -> None:
        chosen.append(p)
        chosen_ids.add(p.id)
        if _group_counted(p):
            per_group[_read_group(p)] += 1

    def full(p: IntakePhoto) -> bool:
        return _group_counted(p) and per_group[_read_group(p)] >= READ_GROUP_MAX

    def take(candidates: list[IntakePhoto], limit: int, grouped: bool = False) -> None:
        for p in candidates:
            if limit <= 0 or len(chosen) >= cap:
                return
            if p.id in chosen_ids or (grouped and full(p)):
                continue
            add(p)
            limit -= 1

    for kind, limit in FACT_BUDGET:
        take(_by_tiers(by_kind.get(kind, [])), limit)
    take(_round_robin([_by_tiers(g) for g in areas.values()]), OVERVIEW_BUDGET)
    unsorted = _spread_order(by_kind.get("unsorted", []))
    budget = cap - len(chosen) - min(UNSORTED_BUDGET, len(unsorted))
    start = len(chosen)
    car = [p for p in usable if p.kind in ("damage",) + _CAR_KINDS and _shows_any(p)]
    for area in dict.fromkeys(p.area for p in car if p.area):
        if len(chosen) - start >= budget:
            break
        if any(c.area == area and _shows_any(c) for c in chosen):
            continue
        take(_best_first([p for p in car if p.area == area]), 1, grouped=True)
    groups: dict[tuple, list[IntakePhoto]] = {}
    for p in damage_pool:
        groups.setdefault(_read_group(p), []).append(p)
    ordered = sorted(groups.values(), key=lambda g: (not any(p.shows_damage for p in g), g[0].position))
    take(_round_robin([_best_first(g) for g in ordered]), budget - (len(chosen) - start), grouped=True)
    take(unsorted, UNSORTED_BUDGET)
    later: list[IntakePhoto] = []
    last: list[IntakePhoto] = []
    for p in _top_up_order(usable, by_kind, damage_pool, unsorted):
        if len(chosen) >= cap:
            break
        if p.id in chosen_ids:
            continue
        if p.kind in _LAST_KINDS:
            last.append(p)
        elif full(p):
            later.append(p)
        else:
            add(p)
    take(later, cap)
    take(last, cap)
    return chosen


# Contract 8 D1: the photos a close-up may be cut from -- the insured car's
# (crops.py). The other party's car, the scene and paperwork never get one,
# whatever box a photo regrouped by the surveyor still carries.
CROP_KINDS = ("damage",) + _CAR_KINDS


def crop_candidates(selected: list[IntakePhoto]) -> list[tuple[str, list[str], list[int]]]:
    """The reading's photos a close-up may be cut from, best first, as
    (photo id, its copies largest first, its box): those of the insured car
    showing damage with a sensible box, the damage close-ups first, each
    group spread over the camera order (_spread_order) so a cap doesn't
    spend itself on the first corner photographed."""
    boxed = [p for p in selected if p.shows_damage and p.kind in CROP_KINDS and clean_box(p.damage_box)]
    ordered = _spread_order([p for p in boxed if p.kind == "damage"]) + _spread_order(
        [p for p in boxed if p.kind != "damage"])
    return [(p.id, [p.path, ai_input_path(p.path)], clean_box(p.damage_box)) for p in ordered]


def read_crops(candidates: list[tuple[str, list[str], list[int]]], limit: int) -> dict[str, str]:
    """{photo id: its close-up} for up to `limit` of crop_candidates, in
    their order; one that can't be cut gives its place to the next."""
    out: dict[str, str] = {}
    for photo_id, sources, box in candidates:
        if len(out) >= limit:
            break
        path = crops.make_crop(sources, box)
        if path:
            out[photo_id] = path
    return out


def photo_facts(photo: IntakePhoto) -> evidence.PhotoFacts:
    """What a photo's findings' evidence needs (contract 9): the image the
    AI was sent -- the original when there is no preview, whose frame its
    EXIF may turn, so a box on it is dropped (evidence.clean_box) -- and the
    sorter's box with the part it named (settings.intake_sort_damage_box)."""
    ai_path = ai_input_path(photo.path)
    return evidence.PhotoFacts(ai_path=ai_path, raw=ai_path == photo.path,
                               sort_box=evidence.clean_box(photo.damage_box), sort_part=photo.part)


def _read(report_id: str, actor: AiActor, kind: str = "read") -> None:
    """The reading stage, as one run of the log (ai/runs.py): kind "read",
    or "redraft" for a re-read -- then the second look at the damage it
    found (ai/second_look.py, its own run), which only adds suggestions,
    never raises and is time-boxed: the reading is saved by then. Then the
    re-check of those suggestions (contract 9, ai/check.py): it labels and
    sorts them, never raises, and does nothing while it is off."""
    with runs.recorded(report_id, kind, actor) as run:
        _read_into(report_id, actor, run)
    if not run.aborted:
        second_look.run_for_intake(report_id, actor)
        # Contract 10: the linked check of the linked parts just stored (one
        # light call per damaged area, a closed list), before the re-check,
        # which skips the hidden ones. Never raises; nothing while it is off.
        link_check.run_for_intake(report_id, actor)
        check.run_for_intake(report_id, actor)


def _read_into(report_id: str, actor: AiActor, run: runs.Run) -> None:
    read_started = time.monotonic()
    _set_stage(report_id, "reading", 0, 1)
    # Contract 8 D2: read once, so a reading is all one mode.
    zone_mode = zones.zones_on()
    zone_jobs: list[zones.ZoneJob] = []
    db = SessionLocal()
    try:
        session = db.get(IntakeSession, report_id)
        if session is None:
            run.aborted = "draft_gone"
            return  # draft deleted mid-run
        # F1: the last reading's own facts, for a list from the zone reads
        # alone (fallback_draft). Read only.
        previous_ai = session.ai_draft if isinstance(session.ai_draft, dict) else {}
        photos = ordered_photos(db, report_id)
        labels = {p.id: photo_label(i) for i, p in enumerate(photos)}
        selected = [p for p in select_for_reading(photos) if Path(p.path).exists()]
        inputs = [(labels[p.id], p.id, ai_input_path(p.path)) for p in selected]
        # What the sorter saw in each photo, for the reader's captions
        # (extraction.photo_hint words them, without any side).
        hints = [{"kind": p.kind, "area": p.area, "part": p.part, "note": p.note} for p in selected]
        notes = session.reporter_notes or ""
        # Every photo in camera order with where the sorter saw it taken,
        # read or not: resolve_sides places side-undetermined findings by it.
        photo_areas = voting_areas(photos)
        sorter_parts = {p.id: p.part for p in photos if p.part}
        facts = {p.id: photo_facts(p) for p in photos}
        if zone_mode:
            # The areas to read one at a time, from every photo of the case.
            zone_jobs = zones.jobs_for(photos, labels)
        # Wave 2 (plans/2026-11-22-enhance-staging 3c): the zone reads' E7 /
        # E11 options, None at the defaults; E7's side of each photo's area
        # group for the side resolver, None while off.
        extras = zones.extras_for(photos) if zone_mode else None
        geo_mode = geometry.mode()
        side_groups = geometry.area_sides(photos) if geo_mode in geometry.ACTING else None
        # Contract 8 D1: close-ups beside the photos the sorter boxed. Off:
        # no box is read and the request is as before.
        with_crops = bool(getattr(settings, "intake_read_crops", False))
        candidates = crop_candidates(selected) if with_crops else []
    finally:
        db.close()
    with_extras = {"extras": extras} if extras is not None else {}

    # The photos as the model was told them ("Photo P03:"); renumbered to
    # the case's at filing (runs.relabel_last_read).
    run.photos_sent = [label for label, _, _ in inputs]
    if not inputs and not notes.strip():
        run.error_code = "nothing_to_read"
        raise ValueError("there is nothing to read -- every photo is excluded or unusable. Include some photos again.")

    if zone_jobs:
        # Progress over the overall reading and each zone call (GET
        # /intake/{id}'s progress; the wizard shows reading without counts).
        _set_stage(report_id, "reading", 0, 1 + len(zone_jobs))
    extra: dict = {}
    closeups: set[str] = set()
    if with_crops:
        made = read_crops(candidates, settings.intake_read_crops_max)
        if read_limit() > MAX_READ_PHOTOS:
            # E12 (the review's M3): the close-ups made count in the budget.
            made = fit_crops(sum(_file_bytes(path) for _, _, path in inputs), made)
        extra["photo_crops"] = [made.get(photo_id) for _, photo_id, _ in inputs]
        closeups = set(made)
        logger.info("Reading %s: %d close-up(s) of %d boxed photo(s)", report_id, len(made), len(candidates))

    def read_once(**limits):
        return draft_report(
            notes,
            [path for _, _, path in inputs],
            photo_labels=[label for label, _, _ in inputs],
            photo_hints=hints,
            # The review's L5: 120 s as before, more for a read past 32 photos.
            timeout=read_timeout(len(inputs)),
            actor=actor,
            **extra,
            **limits,
        )

    # The zone reads made before the merge below rather than in it
    # (early_reads): B2 (settings.intake_parallel_reads "on") starts them
    # beside the overall reading -- they need only the sort -- and F1
    # (settings.intake_main_read_fallback "zones") makes them when the
    # overall reading failed. Both off: none, and the zone reads run after
    # the overall reading, as before.
    early_reads: list[tuple[str, list[dict]]] | None = None
    early_intact: dict[str, list[str]] = {}
    early_walk: dict | None = None
    zone_started: float | None = None
    # B2 only with F1 "zones" (the review's M4): a failed main read then
    # uses the zone answers already paid for, instead of throwing them away.
    parallel = ParallelZones.start(report_id, actor, zone_jobs, early_intact, **with_extras) if (
        zone_mode and zone_jobs and parallel_reads_on() and main_read_fallback_on()) else None
    if parallel is not None:
        early_walk, zone_started = parallel.walk, parallel.started
    tries_before = len(run.attempts.snapshot())
    first_error: Exception | None = None
    try:
        draft = read_once()
    except Exception as exc:
        if not (main_read_fallback_on() and fallback_worthy(exc, run.attempts.snapshot()[tries_before:])):
            if parallel is not None:
                # Never left running past the run's end: the failure is shown
                # at once, no new zone call starts and the ones in flight
                # stop at their model (zones' cancel), and they are waited
                # for -- the run stays reserved until then, so Retry can't
                # overlap them.
                parallel.progress.close()
                _set_stage(report_id, "failed", error=_friendly_error(exc),
                           error_code=exc.code if isinstance(exc, AiUnavailable) else None,
                           error_by=actor.user_id)
                parallel.cancel()
            raise
        first_error, draft = exc, None
    else:
        if parallel is not None:
            parallel.progress.main_done()
    if first_error is not None:
        logger.warning("Reading %s: the overall read failed (%s); %s, then once more", report_id,
                       type(first_error).__name__,
                       "the zone reads first" if zone_mode and zone_jobs else "no zone reads")
        if parallel is not None:
            early_reads = parallel.result()
        elif zone_mode and zone_jobs:
            early_walk = {} if zones.walk_index() is not None else None
            zone_started = time.monotonic()
            early_reads = zones.zone_reads(
                report_id, actor, zone_jobs,
                progress=lambda done: _set_stage(report_id, "reading", done, 1 + len(zone_jobs)),
                intact=early_intact, walk=early_walk, **with_extras,
            )
        found_some = any(found for _, found in early_reads or [])
        # The lead's rule (22 Nov): the second try only while the run is
        # inside the zone stage's time box (zones.TIME_BOX from when the
        # zone reads started; with none, from when the reading began), and
        # (the review's M3) it must end inside it: that box as its
        # deadline, at most one request per model of the chain, no "all
        # busy" round.
        box_end = (zone_started or read_started) + zones.TIME_BOX
        if time.monotonic() >= box_end:
            if not found_some:
                raise first_error
            logger.warning("Reading %s: past the zone stage's time box, no second try; the list is the zone "
                           "reads' finds", report_id)
        else:
            try:
                draft = read_once(deadline=box_end, max_tries=max(1, len(main_read_chain())))
            except Exception as again:
                if not found_some:
                    raise
                logger.warning("Reading %s: the overall read failed again (%s); the list is the zone reads' finds",
                               report_id, type(again).__name__)
                draft = None
            else:
                if parallel is not None:
                    parallel.progress.main_done()
    if parallel is not None and early_reads is None:
        # The overall reading answered: the zone reads, as they finish.
        early_reads = parallel.result()

    if draft is None:
        data = fallback_draft(previous_ai)
    else:
        data = draft.model_dump()

    # Photo references are stored as "@<photo id>" until finalize, because
    # P-numbers shift whenever a photo is deleted or added before then.
    label_to_id = {label: photo_id for label, photo_id, _ in inputs}
    for item in data.get("damage_summary") or []:
        match = _LABEL_RE.match((item.get("photo_reference") or "").strip().upper())
        photo_id = label_to_id.get(photo_label(int(match.group(1)) - 1)) if match else None
        item["photo_reference"] = f"@{photo_id}" if photo_id else None
        # Contract 9 (decision 25): "P07" names both a photo and its
        # close-up, so a box with it may be on either -- none is kept.
        if not photo_id or photo_id in closeups:
            item["bbox_2d"] = None
        # The overall reading's own evidence: its photo and box. Its schema
        # asks for no reason (the drafting schema is unchanged): its items'
        # reasons are the zone reads' or none.
        item["reason"] = None
        own = evidence.own_entry(item, zones.OVERALL)
        item["evidence"] = [own] if own else []

    if settings.intake_side_from_sorter:
        side_from_sorter(data.get("damage_summary") or [], sorter_parts)
    # Contract 8 D2: the zone calls, their finds merged into the reading's
    # (found_by / seen_in on every item). Never raises: a zone call that
    # fails is skipped, and the overall reading is kept either way.
    disputed: set[str] = set()
    zone_draft: dict = {}
    if zone_mode:
        total = 1 + len(zone_jobs)
        if early_reads is not None:
            # F1: the zone reads ran before the overall reading's second try.
            intact, walked = early_intact, early_walk
            data["damage_summary"], disputed, zone_reads = zones.merged_with(
                report_id, data.get("damage_summary") or [], early_reads)
            if draft is None:
                # The overall reading never answered.
                zone_reads = [read for read in zone_reads if read != zones.OVERALL]
        else:
            if zone_jobs:
                _set_stage(report_id, "reading", 1, total)
            intact: dict[str, list[str]] = {}
            # The assembly walk (settings.intake_assembly_walk): only while on
            # with a map that has signs; off, read_and_merge is called as before.
            walked: dict | None = {} if zones.walk_index() is not None else None
            data["damage_summary"], disputed, zone_reads = zones.read_and_merge(
                report_id, actor, data.get("damage_summary") or [], zone_jobs,
                progress=lambda done: _set_stage(report_id, "reading", 1 + done, total),
                intact=intact, **({"walk": walked} if walked is not None else {}), **with_extras,
            )
        # Contract 9: kept beside the AI draft, never in Report.data
        # (second_look._store reads them).
        zone_draft["zone_reads"] = zone_reads
        # Contract 10: what each zone read saw undamaged, for the linked
        # parts (before, in the run log only). Never applied to the list.
        zone_draft["zone_intact"] = intact
        if walked is not None:
            # K3b: the zone reads' yes/no signs about their damaged anchor
            # panels, for the linked parts (assembly_walk.from_settings).
            zone_draft["assembly_walk"] = walked
        if draft is None:
            # F1: no overall reading, so no option B: the zone reads' finds
            # go on the list (below, with the last reading's own list), each
            # a "Check" row, with the notice GET /intake/{id} shows
            # (read_notice).
            zone_draft["main_read"] = fallback_notice(previous_ai)
        elif zones.suggest_zone_only():
            # Option B: the list is what the overall reading found; the rest
            # are suggestions. resolve_sides and the agreement rule below
            # then see the list only.
            data["damage_summary"], zone_draft["zone_suggestions"] = zones.split_suggestions(data["damage_summary"])
        if extras is not None and extras.wrong_end_mode == "keep" and extras.wrong_end:
            # E11 "keep": the finds of an end zone read that saw the other
            # end, as suggestions with their note -- never on the list.
            zone_draft.setdefault("zone_suggestions", []).extend(zones.wrong_end_suggestions(extras.wrong_end))
    try:
        # Each finding's evidence against the photos: frames, boxes on raw
        # originals dropped, the sorter's boxes, best first. Code only; the
        # reading is never lost for it.
        evidence.finish(data.get("damage_summary") or [], zone_draft.get("zone_suggestions") or [], facts)
    except Exception:
        logger.exception("Reading %s: couldn't settle the evidence; the reading stands", report_id)
    # F1 on a re-read (the review's H1): the last reading's own list stays,
    # as it was, and the zone reads' finds it hasn't got are added, each a
    # "Check" row. A person's ticked rows stay too (merge_redraft).
    previous_rows: list[dict] = []
    if draft is None:
        previous_rows = previous_list(previous_ai)
        data["damage_summary"] = with_zone_finds(previous_rows, data.get("damage_summary") or [])
        # The main read read nothing (the review's L1): the run log's
        # reading sent no photo, and read_photo_ids stays as it was.
        run.photos_sent = []
    run.parts_found = len(data.get("damage_summary") or [])

    vehicle = data.get("vehicle_info")
    if vehicle:
        vehicle["body_type"] = body_type_for(vehicle.get("make"), vehicle.get("model"), vehicle.get("body_type"))

    db = SessionLocal()
    try:
        report = db.get(Report, report_id)
        session = db.get(IntakeSession, report_id)
        if report is None or session is None or report.status != "intake":
            run.aborted = "draft_gone"  # read, but there is nothing to keep it in
            return
        current = report.data or {}
        # Sides placed here, against the form as it is now and before
        # merge_redraft: a part the surveyor checked as "(side undetermined)"
        # stays so -- the AI's repeat of it isn't placed, and merge drops it.
        if data.get("damage_summary"):
            checked = {it.get("part") for it in (current.get("damage_summary") or []) if it.get("human_verified")}
            # A side the reads disputed (zones.merge_reads) isn't placed from
            # the sorter's areas either: they are where wrong sides came from.
            # F3 (settings.intake_keep_other_side "on"): a placed find whose
            # name is listed already, on another photo, comes back in
            # other_side for a suggestion; off, resolve_sides is called as
            # before.
            other_side: list[dict] | None = [] if keep_other_side_on() else None
            # F1: the last reading's own rows were placed by their reading.
            kept_as_they_were = {str(it.get("part")) for it in previous_rows}
            # E7 (settings.intake_side_geometry): each photo's area group's
            # side by the sort's geometry, as a vote or a tie-break, and what
            # it changed, kept beside the AI draft (side_geometry); off,
            # resolve_sides is called as before.
            geo_log: list[dict] = []
            geo_args = ({"geo_sides": side_groups or {}, "geometry_mode": geo_mode, "geometry_log": geo_log}
                        if geo_mode in geometry.ACTING else {})
            data["damage_summary"] = resolve_sides(data["damage_summary"], photo_areas,
                                                   keep_undetermined=checked | disputed | kept_as_they_were,
                                                   **({"other_side": other_side} if other_side is not None else {}),
                                                   **geo_args)
            if geo_mode in geometry.ACTING:
                zone_draft["side_geometry"] = geo_log
            if zone_mode:
                zones.apply_agreement(data["damage_summary"])
            if other_side:
                zone_draft.setdefault("zone_suggestions", []).extend(
                    zones.other_side_suggestion(item) for item in other_side)
            data["damaged_parts"] = [item["part"] for item in data["damage_summary"] if item.get("part")]
            run.parts_found = len(data["damage_summary"])
        if zone_draft.get("zone_suggestions"):
            # Against the list as the side resolver left it: the firm's
            # names and the other side's twin (zones.name_suggestions).
            # Wording only: never the reason a reading is lost.
            try:
                zones.name_suggestions(zone_draft["zone_suggestions"], data.get("damage_summary") or [], db,
                                       data.get("vehicle_info") or {})
            except Exception:
                logger.exception("Reading %s: couldn't name the suggestions", report_id)
        # Merged against the form as it is NOW (re-read inside this
        # transaction), not as it was when the run started.
        report.data = merge_redraft(current, session.ai_draft, data)
        session.ai_draft = {**data, **zone_draft} if zone_draft else data
        if draft is not None:
            session.read_photo_ids = [photo_id for _, photo_id, _ in inputs]
        db.commit()
    finally:
        db.close()
    # The reading is saved: the case's first parts-catalogue look-up, if it
    # qualifies (catalogue.maybe_auto_lookup never raises; off by default).
    catalogue.maybe_auto_lookup(report_id)


# ---------------------------------------------------- F1: main read fallback
#
# plans/2026-11-22-enhance-staging F1 (owner decision 9). One failed main
# read -- both light models busy -- used to stop the run before the zone
# reads, which don't need it. settings.intake_main_read_fallback "zones":
# the zone reads run, the main read is tried once more, and if it fails
# again the zone reads' finds are the list, every row "Check" (Low), with
# IntakeSession.ai_draft["main_read"] = MAIN_READ_FALLBACK (GET
# /intake/{id}'s read_notice is its note). Nothing found by the zone reads:
# the run fails as before.

MAIN_READ_FAILED_NOTE = ("The overall read failed because Google was busy. This list comes from the area reads "
                         "only. Press Re-read when ready.")
# A re-read's (the review's H1): the last reading's list is kept.
MAIN_READ_FAILED_REREAD_NOTE = ("The overall read failed because Google was busy. The list keeps the last read's "
                                "parts and adds what the area reads found, marked Check. Press Re-read when ready.")
MAIN_READ_FALLBACK = {"status": "failed", "fallback": "zones", "note": MAIN_READ_FAILED_NOTE}


def fallback_notice(previous_ai: dict | None) -> dict:
    """ai_draft["main_read"] of an F1 list: MAIN_READ_FALLBACK, its note the
    re-read's when the last reading left a list."""
    out = dict(MAIN_READ_FALLBACK)
    if previous_list(previous_ai):
        out["note"] = MAIN_READ_FAILED_REREAD_NOTE
    return out


def previous_list(previous_ai: dict | None) -> list[dict]:
    """The last reading's own damage list (copies), [] on a first read."""
    rows = previous_ai.get("damage_summary") if isinstance(previous_ai, dict) else None
    return [copy.deepcopy(it) for it in rows or [] if isinstance(it, dict) and it.get("part")]


def _same_part(item: dict, rows: list[dict]) -> bool:
    """Whether `rows` has item's part already: a named "Other / Not Listed"
    by its name (item_key), any other part by its names (a sided name and
    its "(side undetermined)" twin are one part: second_look.is_listed)."""
    from app.ai.second_look import is_listed
    from app.reports.taxonomy import OTHER_PART

    if item_key(item) in {item_key(row) for row in rows}:
        return True
    part = str(item.get("part") or "")
    return part != OTHER_PART and is_listed(part, {str(row.get("part") or "") for row in rows})


def with_zone_finds(previous_rows: list[dict], zone_rows: list[dict]) -> list[dict]:
    """F1's list: the last reading's own rows as they were, then each zone
    find whose part isn't among them, as a "Check" row (Low)."""
    out = list(previous_rows)
    for row in zone_rows:
        if not _same_part(row, out):
            out.append({**row, "ai_confidence": "Low"})
    return out


def main_read_fallback_on() -> bool:
    return getattr(settings, "intake_main_read_fallback", "off") == "zones"


def fallback_worthy(exc: BaseException, tries: list[dict] | None = None) -> bool:
    """A main read failure the fallback is for (the lead, 22 Nov): Google
    busy (503) or out of time -- what a few minutes, or the zone reads' own
    smaller calls, may get past. Our TimeoutError (a deadline), or
    call_structured's "All Gemini fallback models ... failed" when every
    try it sent (`tries`: that call's attempts; a model skipped for its
    spent day is not a try) was busy or a timeout. Never for the person's
    AI being unavailable (AiUnavailable: a refused, invalid or unticked
    key), a spent daily allowance (DayUsedUp, a per-day 429), a request
    Google refused (400, 403) or a bad answer -- the zone reads would fail
    the same way -- nor for a photo removed mid-run or nothing to read."""
    if isinstance(exc, AiUnavailable) or allowance.day_spent_error(exc):
        return False
    if isinstance(exc, TimeoutError):
        return True
    if not (isinstance(exc, RuntimeError) and "fallback models" in str(exc)):
        return False
    sent = [a for a in tries or [] if isinstance(a, dict) and a.get("outcome") != "day_spent"]
    return bool(sent) and all(a.get("busy") or a.get("outcome") == "timeout" for a in sent)


# ------------------------------------------------- B2: the reads at once
#
# plans/2026-11-22-enhance-staging R-batching B2 (settings.
# intake_parallel_reads "on"): the zone reads need only the sort, so they
# start beside the overall reading instead of after it -- the same calls,
# through the same rate limiter and busy handling (call_structured), the
# zone stage's own run of the log and its time box. Thread safety: the
# zone reads run on a thread of their own (and its pool) with their own
# database sessions -- the run log's rows (runs.recorded) and the progress
# (_set_stage, each its own session) -- and touch no session or row object
# of the reading's; their answers are read only once that thread is done
# (result), before the reading saves. The progress of both goes through
# one lock (ReadProgress), so it only ever counts up. A reading that fails
# (and doesn't fall back) cancels them: no new zone call starts, and the
# ones in flight are waited for, so nothing writes "reading" over the
# failure and Retry can't overlap them.


def parallel_reads_on() -> bool:
    """B2: settings.intake_parallel_reads is "on"."""
    return getattr(settings, "intake_parallel_reads", "off") == "on"


class ReadProgress:
    """The reading's progress with the zone reads running beside it: 1 for
    the overall reading plus 1 per zone call, written under one lock so two
    threads' counts never step back."""

    def __init__(self, report_id: str, zone_calls: int):
        self.report_id = report_id
        self.total = 1 + zone_calls
        self._lock = threading.Lock()
        self._main = 0
        self._zones = 0
        self.closed = False

    def _write(self) -> None:
        if self.closed:
            return
        try:
            _set_stage(self.report_id, "reading", min(self.total, self._main + self._zones), self.total)
        except Exception:
            # Only the bar (the review's L2): a busy database here must never
            # count as the main read failing.
            logger.warning("Reading %s: couldn't show progress", self.report_id, exc_info=True)

    def main_done(self) -> None:
        with self._lock:
            self._main = 1
            self._write()

    def zone_done(self, done: int) -> None:
        with self._lock:
            self._zones = max(self._zones, done)
            self._write()

    def close(self) -> None:
        with self._lock:
            self.closed = True


class ParallelZones:
    """The zone reads of one reading, started at once on a thread of their
    own (B2). result() waits for them: zones.zone_reads' answer, `intact`
    and `walk` filled. cancel() stops new zone calls and waits for those in
    flight."""

    def __init__(self, report_id: str, actor: AiActor, jobs: list, intact: dict, extras=None):
        self.report_id = report_id
        self.walk: dict | None = {} if zones.walk_index() is not None else None
        self.progress = ReadProgress(report_id, len(jobs))
        self._stop = threading.Event()
        self.started = time.monotonic()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"zones-{report_id}")
        # Wave 2's zone options (zones.ZoneExtras), passed only when given.
        self._future = self._pool.submit(zones.zone_reads, report_id, actor, jobs, self.progress.zone_done, intact,
                                         self.walk, cancel=self._stop, **({"extras": extras} if extras else {}))
        self._pool.shutdown(wait=False)

    @classmethod
    def start(cls, report_id: str, actor: AiActor, jobs: list, intact: dict, extras=None) -> "ParallelZones":
        logger.info("Reading %s: %d zone read(s) started beside the overall reading", report_id, len(jobs))
        return cls(report_id, actor, jobs, intact, extras)

    def result(self) -> list[tuple[str, list[dict]]]:
        try:
            return self._future.result()
        except Exception:  # zone_reads never raises; in case it ever does
            logger.exception("Reading %s: the zone reads beside it failed", self.report_id)
            return []

    def cancel(self) -> None:
        """No new zone call, the ones in flight stopped at their model
        (zones' cancel), their progress closed first -- nothing writes
        "reading" again -- then waited for."""
        self.progress.close()
        self._stop.set()
        self.result()


def fallback_draft(previous: dict | None) -> dict:
    """The AI draft of a reading whose main read failed twice: an empty
    draft (SecurityIncidentDraft's fields), the description, facts and
    people taken from the last reading's AI draft when there was one -- so
    merge_redraft keeps the form as it is -- and no damage list: the zone
    reads' finds go there."""
    base = SecurityIncidentDraft(description="").model_dump()
    previous = previous if isinstance(previous, dict) else {}
    for key in base:
        if key in previous and key not in ("damage_summary", "damaged_parts"):
            base[key] = copy.deepcopy(previous[key])
    base["damage_summary"], base["damaged_parts"] = [], []
    return base


def read_notice(ai_draft) -> str | None:
    """GET /intake/{id}'s read_notice: the note of an F1 list, else None."""
    look = ai_draft.get("main_read") if isinstance(ai_draft, dict) else None
    if not isinstance(look, dict) or look.get("status") != "failed":
        return None
    return str(look.get("note") or MAIN_READ_FAILED_NOTE)


def keep_other_side_on() -> bool:
    """F3: settings.intake_keep_other_side is "on"."""
    return getattr(settings, "intake_keep_other_side", "off") == "on"


# ---------------------------------------------------------- side resolver
#
# The reader names a close-up's part but often not its side ("Tail Lamp
# (side undetermined)"): a lamp filling the frame shows no LH or RH. The
# sorter has already said where each photo was taken, and a surveyor walks
# round the car, so the photos shot around it show the same corner. No AI
# call: the photo's own area (uncontradicted), or else a clear majority of
# the other clues, names the side, flagged side_inferred with ai_confidence
# Low for a person to confirm (the dashboard marks it; Annex A prints no
# LH / RH for it until then: prefill.side_unconfirmed).

_AREA_SIDE = {
    "left": "Left", "front-left": "Left", "rear-left": "Left",
    "right": "Right", "front-right": "Right", "rear-right": "Right",
}
_AREA_END = {
    "front": "Front", "front-left": "Front", "front-right": "Front",
    "rear": "Rear", "rear-left": "Rear", "rear-right": "Rear",
}
# How many places along the camera order a neighbour may be. Each way, the
# nearest photo with an area within that many places counts: with 2, one
# area-less photo in between (a document, or a photo that doesn't vote --
# voting_areas) is skipped; two in a row end the search that way.
NEIGHBOUR_REACH = 2


def voting_areas(photos) -> list[tuple[str, str | None]]:
    """(photo id, area) for every photo in camera order, the area None for a
    photo whose area must not count as a clue: the other party's car
    (kind third_party: its LH is not ours), a photo the surveyor excluded,
    or one the sorter called unusable."""
    def votes(p) -> bool:
        return p.kind != "third_party" and not p.excluded and p.quality != "unusable"

    return [(p.id, p.area if votes(p) else None) for p in photos]


def _zone(area: str | None) -> str | None:
    """Front, Rear or Side of the car, for "findings in the same area"."""
    if area in _AREA_END:
        return _AREA_END[area]
    return "Side" if area in ("left", "right") else None


def _clear_side(votes: Counter) -> str | None:
    """The side the clues agree on, or None when they are split: a tie, or
    the other side holding more than half as many (2 to 1 places it, 3 to 2
    does not -- a wrong side printed in the report is worse than none)."""
    left, right = votes["Left"], votes["Right"]
    winner, loser = ("Left", right) if left > right else ("Right", left)
    lead = max(left, right)
    if lead == 0 or left == right or loser * 2 > lead:
        return None
    return winner


def side_from_sorter(items: list[dict], sorter_parts: dict[str, str]) -> list[dict]:
    """Each item whose photo the sorter tagged with the same part on the
    other side ("Left Front Door" read on a photo tagged "Right Front Door")
    takes the sorter's side, in place. The sorter names a photo's side only
    after working out which way the car faces in it (triage._SIDES, an
    "angle" per photo); a light reader, asked for a whole case at once,
    flips a side all the way through a case (a benchmark case, 2026-11-10:
    every right-side part read as left, every photo tagged right). Same part only:
    a different part on the photo says nothing about this one.

    sorter_parts: {photo id: the sorter's part}; items' photo_reference is
    "@<photo id>" by now."""
    for item in items:
        base, side = split_side(item.get("part") or "")
        tag = sorter_parts.get((item.get("photo_reference") or "").lstrip("@"))
        if side is None or not tag:
            continue
        tag_base, tag_side = split_side(tag)
        if tag_side and tag_base == base and tag_side != side:
            logger.info("Side of %r taken from the sorter's tag on its photo: %r", item.get("part"), tag)
            item["part"] = tag
    return items


def _photos_of(item: dict) -> set[str]:
    """The photos an item was seen in: its photo_reference and seen_in."""
    refs = [item.get("photo_reference")] + list(item.get("seen_in") or [])
    return {r for r in refs if isinstance(r, str) and r}


def _placed_side(own_side: str | None, others: Counter) -> str | None:
    """resolve_sides' rule: the photo's own area side when no clue opposes
    it, else (no own side) a clear majority of the clues (_clear_side)."""
    if own_side:
        opposite = "Right" if own_side == "Left" else "Left"
        return own_side if others[opposite] == 0 else None
    return _clear_side(others)


def resolve_sides(items: list[dict], photo_areas: list[tuple[str, str | None]],
                  keep_undetermined=frozenset(), other_side: list[dict] | None = None,
                  geo_sides: dict[str, str] | None = None, geometry_mode: str = "off",
                  geometry_log: list[dict] | None = None) -> list[dict]:
    """The damage items with each "(side undetermined)" part placed on a
    side where the clues agree. photo_areas: every photo of the case in
    camera order as (photo id, area) -- voting_areas; items reference photos
    as "@<photo id>". keep_undetermined: parts a person checked as they are
    (the form's human_verified parts): those are never placed.

    For an item whose photo is known:
      - when that photo's own area has a side (left, rear-left...), that is
        the side -- unless any other clue below points the other way, and
        then nothing is placed (review, 2026-11-08: the photo the reader
        saw the part in is the best evidence; others may only confirm it);
      - otherwise the clues decide on a clear majority (_clear_side): the
        nearest photo with an area before and after it in camera order
        (NEIGHBOUR_REACH), and every finding the reader placed itself (a
        sided name, not inferred) whose photo lies in the same zone (front,
        rear, side), one vote each.
    Placed only when the name exists (taxonomy.sided_part: doors aren't
    placed, a wheel needs its photo to say front or rear). The placed item
    becomes the sided name with ai_confidence Low and side_inferred True;
    when that name is already listed, it is dropped instead (the reader
    found it already).

    other_side (F3, settings.intake_keep_other_side): when a list is given,
    a dropped item none of whose photos the listed one was seen in (its
    photo_reference and seen_in, against the listed item's) is added to it
    instead, as it was with other_side_of = the listed name -- two lamps
    on two photos may be both lamps (pipeline makes it an "Other side?"
    suggestion). On a photo the listed one was seen in it is the same find,
    dropped as before.

    geo_sides (E7, settings.intake_side_geometry; intake/geometry.py):
    {photo id: the side of its sorter-area group by the sort's geometry}
    (geometry.area_sides) and geometry_mode -- "vote": that side is one
    more clue in `others` for an item on that photo (so against the
    photo's own area side it leaves the item undetermined, as any opposing
    clue does); "tiebreak": it is placed only where the rules above place
    nothing. geometry_log, when given, gets one entry for each item whose
    outcome the geometry changed: {"part", "photo_reference", "was",
    "now", "geometry"} (was / now the side placed, None for none; filing
    relabels the photo, filing.AI_DRAFT_PHOTO_LISTS). None / "off":
    as before."""
    position = {photo_id: i for i, (photo_id, _) in enumerate(photo_areas)}
    area_of = dict(photo_areas)

    def photo_of(item: dict) -> str | None:
        ref = str(item.get("photo_reference") or "")
        return ref[1:] if ref.startswith("@") and ref[1:] in position else None

    placed_by_reader: list[tuple[str, str]] = []
    for item in items:
        _, side = split_side(item.get("part") or "")
        photo_id = photo_of(item)
        zone = _zone(area_of.get(photo_id)) if photo_id else None
        if side and zone and not item.get("side_inferred"):
            placed_by_reader.append((zone, side))

    def neighbour_areas(photo_id: str) -> list[str]:
        at, out = position[photo_id], []
        for step in (-1, 1):
            for distance in range(1, NEIGHBOUR_REACH + 1):
                j = at + step * distance
                if not 0 <= j < len(photo_areas):
                    break
                if photo_areas[j][1]:
                    out.append(photo_areas[j][1])
                    break
        return out

    names = {item.get("part") for item in items}
    # F3: where each name on the list was seen (only with other_side).
    seen_at: dict[str, set[str]] = {}
    if other_side is not None:
        for item in items:
            seen_at.setdefault(item.get("part"), set()).update(_photos_of(item))
    out: list[dict] = []
    for item in items:
        part = item.get("part") or ""
        photo_id = photo_of(item)
        if not part.endswith(UNDETERMINED_SUFFIX) or photo_id is None or part in keep_undetermined:
            out.append(item)
            continue
        own = area_of.get(photo_id)
        others: Counter = Counter()
        for area in neighbour_areas(photo_id):
            if area in _AREA_SIDE:
                others[_AREA_SIDE[area]] += 1
        zone = _zone(own)
        for other_zone, side in placed_by_reader:
            if zone and other_zone == zone:
                others[side] += 1
        own_side = _AREA_SIDE.get(own)
        geo = (geo_sides or {}).get(photo_id) if geometry_mode in ("vote", "tiebreak") else None
        side = _placed_side(own_side, others)
        if geo:
            # E7: the area group's geometry side, by the mode.
            was = side
            if geometry_mode == "vote":
                others[geo] += 1
                side = _placed_side(own_side, others)
            else:
                side = geometry.resolve(side, geo, geometry_mode)
            if side != was and geometry_log is not None:
                geometry_log.append({"part": part, "photo_reference": f"@{photo_id}", "was": was, "now": side,
                                     "geometry": geo})
        name = sided_part(part, side, _AREA_END.get(own)) if side else None
        if name is None:
            out.append(item)
        elif name not in names:
            names.add(name)
            out.append({**item, "part": name, "ai_confidence": "Low", "side_inferred": True})
            if other_side is not None:
                seen_at.setdefault(name, set()).update(_photos_of(item))
        elif other_side is not None and not _photos_of(item) & seen_at.get(name, set()):
            other_side.append({**item, "other_side_of": name})
    return out


# Sections only the surveyor fills in (step 4, the reporter fields, Our
# Ref and the cover page, which the AI never drafts). A re-read never
# touches them when they hold anything. cover_page is here because the
# draft's model_dump() carries it as all-None: starting the merge from
# that would blank every cover fact typed at review (wizard audit risk 1).
_SURVEYOR_SECTIONS = (
    "people_involved", "witnesses", "police_report", "insurance_details", "third_party_info",
    "immediate_actions", "reported_to_authorities", "authority_reference", "recommendations",
    "report_id", "cover_page",
) + REPORTER_FIELDS

# Facts the AI drafts but the surveyor may correct.
_AI_FIELDS = (
    "description", "category", "accident_type", "severity_level", "incident_datetime", "location",
    "weather_condition", "road_condition", "traffic_condition",
)


def _filled(value) -> bool:
    if isinstance(value, dict):
        return any(_filled(v) for v in value.values())
    return value not in (None, "", [], False)


def merge_redraft(current: dict, previous_ai: dict | None, new_ai: dict) -> dict:
    """The form after an AI (re-)read.

    Before this, a re-read replaced Report.data wholesale (keeping only the
    reporter fields), wiping parties, insurance, police details and every
    damage item the surveyor had checked. Now:
      - surveyor-only sections are kept whenever they hold anything;
      - a drafted fact is kept if the surveyor changed it -- i.e. it differs
        from what the AI said last time -- and otherwise takes the new
        reading (or keeps the old value if the new reading is empty);
      - damage items the surveyor checked (human_verified) are kept, and the
        AI's items are added only for parts not already among them;
      - what a person confirmed (data.checked, app/reports/review.py) and
        the list of fields the firm's usual wording filled stay, and a
        confirmed field keeps its value, whatever the new reading says.
    """
    previous_ai = previous_ai or {}
    merged = dict(new_ai)

    for key in _SURVEYOR_SECTIONS:
        if _filled(current.get(key)):
            merged[key] = current[key]

    def pick(cur, prev, new):
        if _filled(cur) and cur != prev:
            return cur  # surveyor edit
        return new if _filled(new) else cur

    for key in _AI_FIELDS:
        merged[key] = pick(current.get(key), previous_ai.get(key), new_ai.get(key))

    cur_v = current.get("vehicle_info") or {}
    prev_v = previous_ai.get("vehicle_info") or {}
    new_v = dict(new_ai.get("vehicle_info") or {})
    # A field still holding the firm's usual wording (put there when the
    # draft was made, before any reading) is nobody's edit: what the AI
    # reads takes its place (review, 2026-11-11) -- its own word
    # ("Scratched" paintwork, "Impaired" steering), or with
    # INTAKE_FIRM_WORDS on the firm's word of the same meaning ("Operable"
    # -> "Good"); a finding is never blanked nor read as "Good"
    # (reports/firm_words.py). Only an empty reading leaves the usual
    # wording, still "check" until a person confirms it.
    usual = {
        key for key in (current.get("template_fields") or [])
        if isinstance(key, str) and same_value(value_at(current, key), TEMPLATE_DEFAULTS.get(key))
    }
    for key in set(cur_v) | set(new_v):
        if f"vehicle_info.{key}" in usual:
            new_v[key] = new_v.get(key) if _filled(new_v.get(key)) else cur_v.get(key)
        else:
            new_v[key] = pick(cur_v.get(key), prev_v.get(key), new_v.get(key))
    merged["vehicle_info"] = new_v

    kept = [item for item in (current.get("damage_summary") or []) if item.get("human_verified")]
    # By item_key, not the bare part: a checked "Other / Not Listed" item
    # named "Rear air vent" would otherwise hide a newly read Other named
    # "Window regulator motor" -- two unlisted parts, both "Other".
    kept_keys = {item_key(item) for item in kept}

    def already_kept(item: dict) -> bool:
        # A part the resolver placed ("Left Tail Lamp", side_inferred) is
        # the same finding as one the surveyor checked as "Tail Lamp (side
        # undetermined)": the re-read must not add it beside theirs.
        if item_key(item) in kept_keys:
            return True
        unsided = unsided_part(str(item.get("part") or ""))
        return bool(item.get("side_inferred")) and bool(unsided) and item_key({"part": unsided}) in kept_keys

    merged["damage_summary"] = kept + [item for item in (new_ai.get("damage_summary") or []) if not already_kept(item)]
    merged["damaged_parts"] = [item.get("part") for item in merged["damage_summary"] if item.get("part")]
    # The last second look stays until the one after this reading replaces
    # its suggestions, keeping the parts a person dismissed
    # (second_look._store). The reading itself never writes one.
    if current.get("second_look"):
        merged["second_look"] = current["second_look"]
    return keep_checked(current, merged)
