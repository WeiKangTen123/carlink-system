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
"""
import logging
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.ai import allowance, runs, second_look
from app.ai.extraction import draft_report
from app.ai.keys import AiActor, AiUnavailable, scrub
from app.ai.triage import BATCH_SIZE, clean_box, triage_photos
from app.config import settings
from app.intake import crops
from app.intake.photos import ai_input_path, ordered_photos, photo_label
from app.parts.prefill import item_key
from app.reports.db import SessionLocal
from app.reports.models import IntakePhoto, IntakeSession, Report
from app.reports.review import TEMPLATE_DEFAULTS, keep_checked, same_value, value_at
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
    # it's working, and the wizard would poll forever.
    if session.stage in RUNNING_STAGES and not is_running(session.report_id):
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
# plates, 2 tyres and 4 near-identical rear overviews on SLK 3063 Z while 22
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
            for label, photo_id, _ in batch:
                found = result.get(label)
                photo = db.get(IntakePhoto, photo_id)
                # Deleted mid-run, or regrouped by the surveyor meanwhile.
                if not found or photo is None or photo.kind_source == "surveyor":
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
        with ThreadPoolExecutor(max_workers=SORT_WORKERS, thread_name_prefix=f"sort-{report_id}") as pool:
            futures = [pool.submit(sort_batch, batch, actor, run.attempts) for batch in batches]
        run.photos_sent = {"count": sent}
        # A failed batch fails the run, but every batch that finished stays
        # sorted -- the others are left to complete rather than cancelled --
        # so Retry only sorts what's left. The person's AI going away is the
        # failure to report when it is among them: it says what to fix.
        failures = [future.exception() for future in futures if future.exception()]
        if failures:
            raise next((e for e in failures if isinstance(e, AiUnavailable)), failures[0])


def _spread_order(photos: list[IntakePhoto]) -> list[IntakePhoto]:
    """Reorders photos that are in camera order so that the first k of them
    are spread over the whole sequence: first, last, middle, then the
    quarters... Taking a group's earliest photos read only what was shot
    first -- on SLK 3063 Z the tailgate photos taken with the boot open,
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


def select_for_reading(photos: list[IntakePhoto]) -> list[IntakePhoto]:
    """The photos the drafting call sees, in case order: up to read_limit(),
    every slot filled while usable photos are left (_top_up_order). Excluded
    and unusable photos never go; blurry ones only after sharp ones of the
    same kind."""
    cap = read_limit()
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

    order = {p.id: i for i, p in enumerate(photos)}
    return sorted(chosen, key=lambda p: order[p.id])


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


def _read(report_id: str, actor: AiActor, kind: str = "read") -> None:
    """The reading stage, as one run of the log (ai/runs.py): kind "read",
    or "redraft" for a re-read -- then the second look at the damage it
    found (ai/second_look.py, its own run), which only adds suggestions,
    never raises and is time-boxed: the reading is saved by then."""
    with runs.recorded(report_id, kind, actor) as run:
        _read_into(report_id, actor, run)
    if not run.aborted:
        second_look.run_for_intake(report_id, actor)


def _read_into(report_id: str, actor: AiActor, run: runs.Run) -> None:
    _set_stage(report_id, "reading", 0, 1)
    db = SessionLocal()
    try:
        session = db.get(IntakeSession, report_id)
        if session is None:
            run.aborted = "draft_gone"
            return  # draft deleted mid-run
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
        # Contract 8 D1: close-ups beside the photos the sorter boxed. Off:
        # no box is read and the request is as before.
        with_crops = bool(getattr(settings, "intake_read_crops", False))
        candidates = crop_candidates(selected) if with_crops else []
    finally:
        db.close()

    # The photos as the model was told them ("Photo P03:"); renumbered to
    # the case's at filing (runs.relabel_last_read).
    run.photos_sent = [label for label, _, _ in inputs]
    if not inputs and not notes.strip():
        run.error_code = "nothing_to_read"
        raise ValueError("there is nothing to read -- every photo is excluded or unusable. Include some photos again.")

    extra: dict = {}
    if with_crops:
        made = read_crops(candidates, settings.intake_read_crops_max)
        extra["photo_crops"] = [made.get(photo_id) for _, photo_id, _ in inputs]
        logger.info("Reading %s: %d close-up(s) of %d boxed photo(s)", report_id, len(made), len(candidates))
    draft = draft_report(
        notes,
        [path for _, _, path in inputs],
        photo_labels=[label for label, _, _ in inputs],
        photo_hints=hints,
        timeout=120.0,
        actor=actor,
        **extra,
    )
    data = draft.model_dump()

    # Photo references are stored as "@<photo id>" until finalize, because
    # P-numbers shift whenever a photo is deleted or added before then.
    label_to_id = {label: photo_id for label, photo_id, _ in inputs}
    for item in data.get("damage_summary") or []:
        match = _LABEL_RE.match((item.get("photo_reference") or "").strip().upper())
        photo_id = label_to_id.get(photo_label(int(match.group(1)) - 1)) if match else None
        item["photo_reference"] = f"@{photo_id}" if photo_id else None
        if not photo_id:
            item["bbox_2d"] = None

    if settings.intake_side_from_sorter:
        side_from_sorter(data.get("damage_summary") or [], sorter_parts)
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
            data["damage_summary"] = resolve_sides(data["damage_summary"], photo_areas, keep_undetermined=checked)
            data["damaged_parts"] = [item["part"] for item in data["damage_summary"] if item.get("part")]
            run.parts_found = len(data["damage_summary"])
        # Merged against the form as it is NOW (re-read inside this
        # transaction), not as it was when the run started.
        report.data = merge_redraft(current, session.ai_draft, data)
        session.ai_draft = data
        session.read_photo_ids = [photo_id for _, photo_id, _ in inputs]
        db.commit()
    finally:
        db.close()


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


def resolve_sides(items: list[dict], photo_areas: list[tuple[str, str | None]],
                  keep_undetermined=frozenset()) -> list[dict]:
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
    found it already)."""
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
        if own_side:
            opposite = "Right" if own_side == "Left" else "Left"
            side = own_side if others[opposite] == 0 else None
        else:
            side = _clear_side(others)
        name = sided_part(part, side, _AREA_END.get(own)) if side else None
        if name is None:
            out.append(item)
        elif name not in names:
            names.add(name)
            out.append({**item, "part": name, "ai_confidence": "Low", "side_inferred": True})
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
    # reads ("Scratched" paintwork) takes its place (review, 2026-11-11).
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
