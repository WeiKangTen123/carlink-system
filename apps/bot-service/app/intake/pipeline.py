"""Background AI processing for a dashboard intake.

Two steps, run in a background thread so the browser can poll progress (and
leave and come back) instead of holding one request open for minutes:

  1. sorting  -- every unsorted photo, BATCH_SIZE per call, SORT_WORKERS
                 calls at a time (ai/triage.py)
  2. reading  -- one drafting call (ai/extraction.draft_report) on the photos
                 that matter most, picked by select_for_reading()

The api process is a single uvicorn worker (app/main.py), so an in-process
set is enough to stop the same draft being processed twice at once.
"""
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.ai.extraction import draft_report
from app.ai.triage import BATCH_SIZE, triage_photos
from app.intake.photos import ai_input_path, ordered_photos, photo_label
from app.reports.db import SessionLocal
from app.reports.models import IntakePhoto, IntakeSession, Report
from app.reports.taxonomy import body_type_for

logger = logging.getLogger(__name__)

RUNNING_STAGES = {"sorting", "reading"}

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
# 120-second reading timeout.
MAX_READ_PHOTOS = 32
# Kinds whose photo still counts as damage evidence when it shows damage and
# names the damaged part (e.g. a boot photo the sorter called interior).
_DAMAGE_EVIDENCE_KINDS = {"interior", "other"}

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


def start_reserved(report_id: str, mode: str) -> None:
    """mode "all" sorts unsorted photos then reads; "read" only re-reads
    (after the surveyor regrouped photos). Call reserve() first."""
    threading.Thread(target=_run, args=(report_id, mode), name=f"intake-{report_id}", daemon=True).start()


def _set_stage(report_id: str, stage: str, done: int = 0, total: int = 0, error: str | None = None) -> None:
    db = SessionLocal()
    try:
        session = db.get(IntakeSession, report_id)
        if session:
            session.stage = stage
            session.progress_done = done
            session.progress_total = total
            session.error = error
            db.commit()
    finally:
        db.close()


def _friendly_error(exc: Exception) -> str:
    """What the wizard shows. Says what actually went wrong: a blanket "quota
    is used up" for every failure sent people to Retry even when the request
    itself was being rejected (the 400s before 877d99d), where Retry can
    never help."""
    if isinstance(exc, RuntimeError) and "fallback models" in str(exc):
        cause = str(exc.__cause__ or "")
        if any(s in cause for s in ("429", "RESOURCE_EXHAUSTED", "quota", "503", "high demand")):
            return (
                "The AI is busy or today's quota is used up. Your photos are saved -- "
                "press Retry in a few minutes."
            )
        return f"The AI rejected the request on every model ({cause[:200] or 'no detail'}). Retrying won't help; please report this."
    if isinstance(exc, FileNotFoundError):
        return "A photo was removed while the AI was working. Press Retry."
    return f"AI processing failed: {exc}"[:300]


def _run(report_id: str, mode: str) -> None:
    try:
        if mode == "all":
            _sort(report_id)
        _read(report_id)
        _set_stage(report_id, "ready", 1, 1)
    except Exception as exc:
        logger.exception("Intake processing failed for %s", report_id)
        _set_stage(report_id, "failed", error=_friendly_error(exc))
    finally:
        with _lock:
            _running.discard(report_id)


def _sort(report_id: str) -> None:
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
    done_lock = threading.Lock()

    def sort_batch(batch: list[tuple[str, str, str]]) -> None:
        nonlocal done
        # Paths were captured at the start of the run; a photo deleted since
        # is skipped rather than failing the whole run on a missing file.
        batch = [item for item in batch if Path(item[2]).exists()]
        if not batch:
            return
        result = triage_photos([(label, path) for label, _, path in batch])
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
    with ThreadPoolExecutor(max_workers=SORT_WORKERS, thread_name_prefix=f"sort-{report_id}") as pool:
        futures = [pool.submit(sort_batch, batch) for batch in batches]
    # A failed batch fails the run, but every batch that finished stays
    # sorted -- the others are left to complete rather than cancelled -- so
    # Retry only sorts what's left.
    for future in futures:
        if future.exception():
            raise future.exception()


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


def select_for_reading(photos: list[IntakePhoto]) -> list[IntakePhoto]:
    """The photos the drafting call sees, in case order. Excluded and
    unusable photos never go; blurry ones only after sharp ones of the same
    kind."""
    usable = [p for p in sorted(photos, key=lambda p: p.position) if not p.excluded and p.quality != "unusable"]
    by_kind: dict[str, list[IntakePhoto]] = {}
    for p in usable:
        by_kind.setdefault(p.kind or "unsorted", []).append(p)

    damage_pool = [
        p for p in usable
        if p.kind == "damage" or (p.kind in _DAMAGE_EVIDENCE_KINDS and p.shows_damage and p.part)
    ]
    overviews = by_kind.get("overview", [])
    areas: dict[str | None, list[IntakePhoto]] = {}
    for p in overviews:
        areas.setdefault(p.area, []).append(p)

    chosen: list[IntakePhoto] = []
    chosen_ids: set[str] = set()

    def take(candidates: list[IntakePhoto], limit: int) -> None:
        for p in candidates:
            if limit <= 0 or len(chosen) >= MAX_READ_PHOTOS:
                return
            if p.id not in chosen_ids:
                chosen.append(p)
                chosen_ids.add(p.id)
                limit -= 1

    for kind, limit in FACT_BUDGET:
        take(_by_tiers(by_kind.get(kind, [])), limit)
    take(_round_robin([_by_tiers(g) for g in areas.values()]), OVERVIEW_BUDGET)
    unsorted = _spread_order(by_kind.get("unsorted", []))
    take(_damage_order(damage_pool), MAX_READ_PHOTOS - len(chosen) - min(UNSORTED_BUDGET, len(unsorted)))
    take(unsorted, UNSORTED_BUDGET)
    # Room left over (a case with few damage photos): more of the rest.
    take(_damage_order(damage_pool) + _spread_order(overviews) + unsorted, MAX_READ_PHOTOS)

    order = {p.id: i for i, p in enumerate(photos)}
    return sorted(chosen, key=lambda p: order[p.id])


def _read(report_id: str) -> None:
    _set_stage(report_id, "reading", 0, 1)
    db = SessionLocal()
    try:
        session = db.get(IntakeSession, report_id)
        if session is None:
            return  # draft deleted mid-run
        photos = ordered_photos(db, report_id)
        labels = {p.id: photo_label(i) for i, p in enumerate(photos)}
        inputs = [
            (labels[p.id], p.id, ai_input_path(p.path))
            for p in select_for_reading(photos)
            if Path(p.path).exists()
        ]
        notes = session.reporter_notes or ""
    finally:
        db.close()

    if not inputs and not notes.strip():
        raise ValueError("there is nothing to read -- every photo is excluded or unusable. Include some photos again.")

    draft = draft_report(
        notes,
        [path for _, _, path in inputs],
        photo_labels=[label for label, _, _ in inputs],
        timeout=120.0,
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

    vehicle = data.get("vehicle_info")
    if vehicle:
        vehicle["body_type"] = body_type_for(vehicle.get("make"), vehicle.get("model"), vehicle.get("body_type"))

    db = SessionLocal()
    try:
        report = db.get(Report, report_id)
        session = db.get(IntakeSession, report_id)
        if report is None or session is None or report.status != "intake":
            return
        # Merged against the form as it is NOW (re-read inside this
        # transaction), not as it was when the run started.
        report.data = merge_redraft(report.data or {}, session.ai_draft, data)
        session.ai_draft = data
        session.read_photo_ids = [photo_id for _, photo_id, _ in inputs]
        db.commit()
    finally:
        db.close()


# Sections only the surveyor fills in (step 4 and the reporter fields). A
# re-read never touches them when they hold anything.
_SURVEYOR_SECTIONS = (
    "people_involved", "witnesses", "police_report", "insurance_details", "third_party_info",
    "immediate_actions", "reported_to_authorities", "authority_reference", "recommendations",
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
        AI's items are added only for parts not already among them.
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
    for key in set(cur_v) | set(new_v):
        new_v[key] = pick(cur_v.get(key), prev_v.get(key), new_v.get(key))
    merged["vehicle_info"] = new_v

    kept = [item for item in (current.get("damage_summary") or []) if item.get("human_verified")]
    kept_parts = {item.get("part") for item in kept}
    merged["damage_summary"] = kept + [
        item for item in (new_ai.get("damage_summary") or []) if item.get("part") not in kept_parts
    ]
    merged["damaged_parts"] = [item.get("part") for item in merged["damage_summary"] if item.get("part")]
    return merged
