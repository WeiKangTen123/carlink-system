"""Background AI processing for a dashboard intake.

Two steps, run in a background thread so the browser can poll progress (and
leave and come back) instead of holding one request open for minutes:

  1. sorting  -- every unsorted photo, BATCH_SIZE per call (ai/triage.py)
  2. reading  -- one drafting call (ai/extraction.draft_report) on the photos
                 that matter most, picked by select_for_reading()

The api process is a single uvicorn worker (app/main.py), so an in-process
set is enough to stop the same draft being processed twice at once.
"""
import logging
import re
import threading
from pathlib import Path

from app.ai.extraction import draft_report
from app.ai.triage import BATCH_SIZE, triage_photos
from app.intake.photos import ai_input_path, ordered_photos, photo_label
from app.reports.db import SessionLocal
from app.reports.models import IntakePhoto, IntakeSession, Report
from app.reports.taxonomy import body_type_for

logger = logging.getLogger(__name__)

RUNNING_STAGES = {"sorting", "reading"}

# How the drafting call's photo budget is spent, in priority order. Documents
# come first because they're the only reliable source of plate, VIN and make
# -- the facts parts lookup will depend on -- and there are rarely more than
# a few. Damage gets the bulk. Whatever budget is left after this pass goes
# to further damage and overview photos (see select_for_reading).
READ_BUDGET: list[tuple[str, int]] = [
    ("log_card", 3),
    ("vin_plate", 2),
    ("police_report", 3),
    ("odometer", 1),
    ("damage", 14),
    ("overview", 4),
    ("third_party", 2),
    ("tyre", 2),
    ("document", 2),
    # Photos sorting skipped (a partial batch answer). Better a few of them
    # than none, if a sorting call came back thin.
    ("unsorted", 4),
]
# ~24 x 200KB previews is ~6.5MB of base64 -- comfortably one request, and
# few enough that the strongest model still answers within the timeout.
MAX_READ_PHOTOS = 24

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
    for offset in range(0, total, BATCH_SIZE):
        # Paths were captured at the start of the run; a photo deleted since
        # is skipped rather than failing the whole run on a missing file.
        batch = [item for item in todo[offset:offset + BATCH_SIZE] if Path(item[2]).exists()]
        if not batch:
            continue
        # A failure here fails the run, but every batch already written
        # stays sorted, so Retry only sorts what's left.
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
                photo.note = found["note"]
            session = db.get(IntakeSession, report_id)
            if session:
                session.progress_done = min(total, offset + len(batch))
            db.commit()
        finally:
            db.close()


def _spread_by_area(photos: list[IntakePhoto]) -> list[IntakePhoto]:
    """Round-robin across vehicle areas, so a rear-end case with 30 bumper
    close-ups still sends the one photo of the scraped left door."""
    groups: dict[str | None, list[IntakePhoto]] = {}
    for p in photos:
        groups.setdefault(p.area, []).append(p)
    spread: list[IntakePhoto] = []
    while any(groups.values()):
        for area in list(groups):
            if groups[area]:
                spread.append(groups[area].pop(0))
    return spread


def select_for_reading(photos: list[IntakePhoto]) -> list[IntakePhoto]:
    """The photos the drafting call sees, in case order. Excluded and
    unusable photos never go; blurry ones only after sharp ones of the same
    kind."""
    by_kind: dict[str, list[IntakePhoto]] = {}
    for p in photos:
        if p.excluded or p.quality == "unusable":
            continue
        by_kind.setdefault(p.kind or "unsorted", []).append(p)
    for kind, group in by_kind.items():
        group.sort(key=lambda p: (p.quality == "blurry", not p.shows_damage, p.position))
        if kind == "damage":
            by_kind[kind] = _spread_by_area(group)

    chosen: list[IntakePhoto] = []
    chosen_ids: set[str] = set()

    def take(kind: str, limit: int) -> None:
        for p in by_kind.get(kind, []):
            if limit <= 0 or len(chosen) >= MAX_READ_PHOTOS:
                return
            if p.id not in chosen_ids:
                chosen.append(p)
                chosen_ids.add(p.id)
                limit -= 1

    for kind, limit in READ_BUDGET:
        take(kind, limit)
    for kind in ("damage", "overview", "unsorted"):
        take(kind, MAX_READ_PHOTOS)

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
