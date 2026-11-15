"""Reading the car one area at a time (contract 8 D2): settings.intake_read_mode
"zones".

The overall reading (pipeline._read_into, extraction.draft_report) runs as
it always has -- it also drafts the vehicle facts, conditions and people.
In zone mode, after it, the case's photos are grouped into the areas of the
car the sorter saw them in (plan_zones), and each area that has a photo
showing damage gets ONE short call with that area's checklist
(ai/zone_read.py). The finds are merged into the overall reading's by
canonical part (merge_reads), the existing side resolver runs, and the
agreement rule marks what only one read saw on one photo (apply_agreement).

Zones (plan_zones / photo_zones):
  - front, rear, left, right from the sorter's area; "close" for damage
    close-ups whose area isn't clear and whose part doesn't place them.
    Interior, documents, the scene and the other party's car get no zone:
    nothing of theirs is on an area's checklist. An "other" photo only
    when it shows damage.
  - A corner (front-left...) close-up goes to the END zone: the front and
    rear checklists hold both sides' lamps, reflectors, bumper ends,
    fenders, quarter panels and arches, and there the model names each
    part's side from the picture rather than from the sorter's corner --
    whose side is where the light reader's wrong sides came from. Unless
    the sorter's part is one only a side has (a door, mirror, sill, wheel):
    then its side zone. A corner wide shot (overview) goes to both zones:
    it is what shows which way the car faces.
  - Tyres go to their side's zone, where the wheels, tyres and arches are;
    a tyre photo with no side to it, only when it shows damage, to close.
  - Whatever the area says, a part the sorter named that the area's
    checklist doesn't have moves the photo to that part's zone (home_zone).
  - At most MAX_ZONE_PHOTOS a zone: damage first (every damaged part one
    photo before any part a second, pipeline._damage_order), then the
    overviews, sharp before blurry; sent in camera order. A zone with no
    photo showing damage isn't read: nothing points at damage there, and
    on the free tier a call is worth more than a look at clean panels.

Calls: one per zone read, at most MAX_ZONE_CALLS, so with the overall
reading at most MAX_READ_CALLS per case (there are five zones). On the
reading's model chain, for the same actor and keys, through the same rate
limit (structured.call_structured), ZONE_WORKERS at a time. A zone call
that fails -- busy, quota, timeout, an answer that doesn't parse, the
person's allowance gone -- is skipped and noted in the run log; the
reading never fails for it. All the zone calls are one run of the log
(runs.ZONES_KIND), whose photos_sent says per zone what was sent and what
came back, the parts seen intact included (they go nowhere else).
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.ai import allowance, runs
from app.ai.keys import AiActor, AiUnavailable
from app.ai.second_look import ZONES
from app.ai.zone_read import CLOSE, END_ZONES, SIDE_ZONES, ZONE_NAMES, ZoneAnswer, read_zone
from app.config import settings
from app.intake.photos import ai_input_path
from app.parts.prefill import item_key
from app.reports.taxonomy import CANONICAL_PARTS, OTHER_PART, UNDETERMINED_SUFFIX, split_side, unsided_part

logger = logging.getLogger(__name__)

MAX_ZONE_PHOTOS = 12
MAX_ZONE_CALLS = 6
# The overall reading and its zone calls, per case (contract 8's quota
# rule: the free tier's 12 calls a minute are shared with the live app).
MAX_READ_CALLS = 7
# Zone calls in flight at once: two halves the wait and stays well inside
# the per-minute limit, which call_structured's rate limiter keeps anyway.
ZONE_WORKERS = 2
# No zone call starts after this many seconds of zone reading (429 waits,
# busy retries): the wizard is waiting, and the overall reading is in hand.
TIME_BOX = 300.0
OVERALL = "overall"

# Kinds of photo no zone reads: the cabin, paperwork, the scene and the
# other party's car.
NO_ZONE_KINDS = frozenset({"interior", "log_card", "vin_plate", "police_report", "document", "odometer", "scene",
                           "third_party"})
_AREA_SIDE = {"left": "left", "front-left": "left", "rear-left": "left",
              "right": "right", "front-right": "right", "rear-right": "right"}
_AREA_END = {"front": "front", "front-left": "front", "front-right": "front",
             "rear": "rear", "rear-left": "rear", "rear-right": "rear"}
_PLAIN_AREAS = ("front", "rear", "left", "right")


def zones_on() -> bool:
    return getattr(settings, "intake_read_mode", "single") == "zones"


def grid_on() -> bool:
    return bool(getattr(settings, "intake_grid_marks", False))


# ------------------------------------------------------------ zoning


def _coarse(part: str) -> str:
    """A part's family without side or, for doors and wheels, front/rear:
    "Left Front Door" -> "Door (side undetermined)"; "Rear Bumper" as is."""
    return unsided_part(part) or part


# The families each zone's checklist can answer with (ai/zone_read.checklist).
_COVERS = {
    "front": {_coarse(p) for p in ZONES["front"]},
    "rear": {_coarse(p) for p in ZONES["rear"]},
    "left": {_coarse(p) for p in ZONES["left"]},
    "right": {_coarse(p) for p in ZONES["right"]},
}


def _covers(zone: str, part: str) -> bool:
    return _coarse(part) in _COVERS[zone]


def home_zone(part: str | None) -> str | None:
    """The zone a part the sorter named belongs to: its end of the car when
    an end's checklist has it (both sides' lamps, fenders, quarter panels
    and arches are there); else its side's (doors, mirrors, sills, wheels).
    None for a part no area lists (roof, cabin), a side-only part with no
    side ("Door (side undetermined)"), or one both ends list (a parking
    sensor, an undetermined wheel arch liner)."""
    if not part:
        return None
    ends = [z for z in END_ZONES if part in ZONES[z]] or [z for z in END_ZONES if _coarse(part) in _COVERS[z]]
    if ends:
        return ends[0] if len(ends) == 1 else None
    side = split_side(part)[1]
    return side.lower() if side and _covers("left", part) else None


def shows_damage(photo) -> bool:
    return photo.kind == "damage" or bool(photo.shows_damage)


def photo_zones(photo) -> list[str]:
    """The zones one photo is read in: none, one, or -- a corner wide shot
    -- its end and its side (the module's docstring says why)."""
    if photo.excluded or photo.quality == "unusable" or not photo.kind or photo.kind in NO_ZONE_KINDS:
        return []
    damage = shows_damage(photo)
    if photo.kind not in ("damage", "overview", "tyre") and not damage:
        return []
    area = photo.area
    if photo.kind == "overview" and area in _AREA_END and area in _AREA_SIDE:
        return [_AREA_END[area], _AREA_SIDE[area]]
    if photo.kind == "tyre":
        zone = _AREA_SIDE.get(area)
    elif area in _PLAIN_AREAS:
        zone = area
    else:
        zone = _AREA_END.get(area)
    home = home_zone(photo.part)
    if photo.part and home and (zone is None or not _covers(zone, photo.part)):
        zone = home
    if zone is None and damage:
        zone = CLOSE
    return [zone] if zone else []


@dataclass
class ZonePlan:
    zone: str
    # At most MAX_ZONE_PHOTOS, in the case's camera order.
    photos: list = field(default_factory=list)
    # Of the zone's photos, before the cap: how many show damage.
    damage_photos: int = 0


def _pick(group: list) -> list:
    """The zone's photos to send: damage first, every damaged part one
    photo before any part a second (pipeline._damage_order), then the
    overviews, then the rest; sharp before blurry; in camera order."""
    from app.intake.pipeline import _by_tiers, _damage_order

    damage = [p for p in group if shows_damage(p)]
    overviews = [p for p in group if not shows_damage(p) and p.kind == "overview"]
    rest = [p for p in group if not shows_damage(p) and p.kind != "overview"]
    chosen = (_damage_order(damage) + _by_tiers(overviews) + _by_tiers(rest))[:MAX_ZONE_PHOTOS]
    order = {id(p): i for i, p in enumerate(group)}
    return sorted(chosen, key=lambda p: order[id(p)])


def plan_zones(photos: list) -> list[ZonePlan]:
    """The zones to read for a case: photos in camera order (IntakePhoto
    rows or anything with their kind, area, part, shows_damage, quality,
    excluded and position). Only zones holding a photo that shows damage,
    the ones with most such photos first (when time runs out the last are
    skipped), at most MAX_ZONE_CALLS -- and MAX_READ_CALLS with the overall
    reading."""
    members: dict[str, list] = {zone: [] for zone in ZONE_NAMES}
    for photo in photos:
        for zone in photo_zones(photo):
            members[zone].append(photo)
    plans = []
    for zone in ZONE_NAMES:
        evidence = sum(1 for p in members[zone] if shows_damage(p))
        if evidence:
            plans.append(ZonePlan(zone, _pick(members[zone]), evidence))
    plans.sort(key=lambda plan: -plan.damage_photos)  # stable: ZONE_NAMES order breaks ties
    return plans[:min(MAX_ZONE_CALLS, MAX_READ_CALLS - 1)]


@dataclass
class ZoneJob:
    """One zone call to make: photos as (label, photo id, AI input path,
    kind), what the pipeline knows while its database session is open."""
    zone: str
    photos: list[tuple[str, str, str, str | None]]


def jobs_for(photos: list, labels: dict[str, str]) -> list[ZoneJob]:
    """plan_zones as calls, each photo by its case label; a photo whose file
    is gone is left out, and a zone left with none."""
    jobs = []
    for plan in plan_zones(photos):
        sent = [(labels[p.id], p.id, ai_input_path(p.path), p.kind) for p in plan.photos if Path(p.path).exists()]
        if sent:
            jobs.append(ZoneJob(plan.zone, sent))
    return jobs


# ------------------------------------------------------------ merging


def _side(part: str) -> str | None:
    return split_side(part)[1]


def _sharing(candidates: list[dict], ref: str | None) -> dict | None:
    return next((m for m in candidates if ref and ref in m["seen_in"]), None)


def _credit(item: dict, found: dict, read: str) -> None:
    """`found` (one read's item) is the same part as `item`: the read and
    its photo are added; item keeps its own details. A photo is filled in
    only where item had none, and grid cells only for item's own photo."""
    if read not in item["found_by"]:
        item["found_by"].append(read)
    ref = found.get("photo_reference") or None
    if ref and ref not in item["seen_in"]:
        item["seen_in"].append(ref)
    if ref and not item.get("photo_reference"):
        item["photo_reference"] = ref
        item["bbox_2d"] = None
    if ref and not item.get("grid_cells") and found.get("grid_cells") and item.get("photo_reference") == ref:
        item["grid_cells"] = list(found["grid_cells"])


def _merge_one(out: list[dict], found: dict, read: str, disputed: set[int], disputed_names: set[str]) -> None:
    part = str(found.get("part") or "")
    ref = found.get("photo_reference") or None
    family, side = split_side(part)

    # The same part (a named "Other" by its name: two unlisted parts are two).
    key = item_key(found)
    same = [m for m in out if item_key(m) == key]
    if same:
        _credit(_sharing(same, ref) or same[0], found, read)
        return

    if part.endswith(UNDETERMINED_SUFFIX):
        # This read couldn't tell the side; another listed the part with
        # one. Its own (on the same photo, or the only one) gets the credit;
        # an undetermined twin beside it would add nothing.
        sided = [m for m in out if _side(str(m.get("part") or "")) and _coarse(str(m.get("part") or "")) == part]
        if sided:
            match = _sharing(sided, ref) or (sided[0] if len(sided) == 1 else None)
            if match is not None:
                _credit(match, found, read)
            return
    elif side:
        # Another read listed the part without a side, and this one saw it:
        # the side it saw is taken -- seen in the photos, where the resolver
        # would only infer it from the sorter's areas. Not on an item whose
        # side the reads disputed on this same photo.
        coarse = _coarse(part)
        undetermined = [m for m in out if m.get("part") == coarse]
        if undetermined:
            match = _sharing(undetermined, ref) or undetermined[0]
            if id(match) not in disputed:
                match["part"] = part
                _credit(match, found, read)
                return
            if ref and ref in match["seen_in"]:
                _credit(match, found, read)
                return
        # The same family on the other side, on the same photo: the reads
        # disagree about one finding. Two reads that agreed outvote a third;
        # one against one, neither side is kept -- the "(side undetermined)"
        # name, Low, held out of the resolver (which would place it from the
        # sorter's area again). A wrong side in the report is worse than
        # none. On different photos both stay: both sides may be damaged.
        other = "Right" if side == "Left" else "Left"
        opposite = [m for m in out if split_side(str(m.get("part") or "")) == (family, other)
                    and ref and ref in m["seen_in"]]
        if opposite:
            match = opposite[0]
            if len(match["found_by"]) >= 2:
                return
            if coarse in CANONICAL_PARTS:
                match["part"] = coarse
                match["ai_confidence"] = "Low"
                match["side_inferred"] = False
                disputed.add(id(match))
                disputed_names.add(coarse)
                _credit(match, found, read)
                return

    found["found_by"] = [read]
    found["seen_in"] = [ref] if ref else []
    out.append(found)


def merge_reads(overall: list[dict], reads: list[tuple[str, list[dict]]]) -> tuple[list[dict], set[str]]:
    """The damage list from the overall reading's items and each zone read's
    (read name, items), in that order; items reference photos as "@<photo
    id>". Pure: the inputs are copied, never changed.

      - Every item says which reads found it (found_by) and in which photos
        (seen_in).
      - A part both found keeps the overall item's details (the first
        read's, between zones); the other read adds its name and photo.
      - A part only a zone found is added as that zone read it.
      - Sides (_merge_one): an undetermined name meets a sided one -> the
        sided one, credited; the same family on opposite sides on the same
        photo -> undetermined, Low, unless two reads agreed on one side.

    Returns (items, the disputed "(side undetermined)" names, which the
    caller keeps out of resolve_sides)."""
    out: list[dict] = []
    for item in overall:
        item = dict(item)
        ref = item.get("photo_reference") or None
        item["found_by"] = [OVERALL]
        item["seen_in"] = [ref] if ref else []
        item["grid_cells"] = list(item.get("grid_cells") or [])
        out.append(item)
    disputed: set[int] = set()
    disputed_names: set[str] = set()
    for read, items in reads:
        for found in items:
            found = dict(found)
            found["grid_cells"] = list(found.get("grid_cells") or [])
            _merge_one(out, found, read, disputed, disputed_names)
    return out, disputed_names


def apply_agreement(items: list[dict]) -> list[dict]:
    """The contract's agreement rule, in place (zone mode only): a part
    found by one read AND seen in at most one photo is ai_confidence Low --
    the Review step's "Check" (review.damage_state) for a person; found by
    two reads or seen in two photos keeps the model's confidence. A part a
    person checked is never touched."""
    for item in items:
        if item.get("human_verified"):
            continue
        if len(set(item.get("found_by") or [])) <= 1 and len(set(item.get("seen_in") or [])) <= 1:
            item["ai_confidence"] = "Low"
    return items


# ------------------------------------------------------------ the stage


class _NoZoneAnswered(Exception):
    """Every zone call failed or was skipped: the zone run is "failed"."""


def read_and_merge(
    report_id: str,
    actor: AiActor,
    items: list[dict],
    jobs: list[ZoneJob],
    progress: Callable[[int], None] | None = None,
) -> tuple[list[dict], set[str]]:
    """The zone stage, for pipeline._read_into once the overall reading has
    answered: one call per job, then merge_reads. items: the overall
    reading's damage items ("@<photo id>" references). progress(n) after
    each zone call ends, n of len(jobs). Returns (items, disputed names).
    Never raises: whatever goes wrong, the overall reading's items stand
    (with found_by "overall")."""
    try:
        reads = _read_zones(report_id, actor, jobs, progress) if jobs else []
    except Exception:
        logger.exception("Zone reading for %s failed; the overall reading stands", report_id)
        reads = []
    try:
        return merge_reads(items, reads)
    except Exception:
        logger.exception("Zone reading for %s: couldn't merge; the overall reading stands", report_id)
        return items, set()


def _read_zones(report_id: str, actor: AiActor, jobs: list[ZoneJob],
                progress: Callable[[int], None] | None) -> list[tuple[str, list[dict]]]:
    from app.ai.extraction import _model_chain

    models = _model_chain()
    grid = grid_on()
    started = time.monotonic()
    answers: dict[int, ZoneAnswer] = {}
    entries: list[dict] = [{"zone": job.zone, "photos": [f"@{pid}" for _, pid, _, _ in job.photos]} for job in jobs]
    stop = threading.Event()
    lock = threading.Lock()
    finished = [0]

    def one(i: int, job: ZoneJob, run: runs.Run) -> None:
        entry = entries[i]
        error = None
        attempts = runs.Attempts()
        try:
            if stop.is_set():
                entry.update(status="skipped", note="no AI allowance left")
                return
            if time.monotonic() - started > TIME_BOX:
                entry.update(status="skipped", note="out of time")
                return
            try:
                # This worker's Gemini tries, kept apart for the zone's own
                # error code, then added to the stage's one run.
                with runs.collecting(attempts):
                    answer = read_zone(job.zone, [(label, path, kind) for label, _, path, kind in job.photos],
                                       actor=actor, models=models, grid=grid)
            except Exception as exc:
                error = exc
                # The person's AI going away, or the day's allowance used up
                # on every model: the zones after this would only fail too.
                if isinstance(exc, (AiUnavailable, allowance.DayUsedUp)):
                    stop.set()
                logger.warning("Zone %s of %s skipped: %s", job.zone, report_id, type(exc).__name__)
                return
            answers[i] = answer
            entry.update(status="ok", side_seen=answer.side_seen, found=len(answer.items), intact=answer.intact)
            if answer.note:
                entry["note"] = answer.note
        finally:
            tries, size = attempts.drain()
            for attempt in tries:
                run.attempts.add(attempt)
            if size is not None:
                run.attempts.add_input(size)
            if error is not None:
                entry.update(status="failed", error_code=runs.error_code_for(error, tries))
            # Counted and shown under the lock: two calls ending together
            # must not write their counts in the wrong order.
            with lock:
                finished[0] += 1
                if progress is not None:
                    try:
                        progress(finished[0])
                    except Exception:
                        logger.warning("Zone reading for %s: couldn't show progress", report_id, exc_info=True)

    try:
        with runs.recorded(report_id, runs.ZONES_KIND, actor) as run:
            # The actor and the run go to each worker as arguments: a pool
            # thread sees nothing of the thread that submitted to it.
            with ThreadPoolExecutor(max_workers=ZONE_WORKERS, thread_name_prefix=f"zones-{report_id}") as pool:
                futures = [pool.submit(one, i, job, run) for i, job in enumerate(jobs)]
            for future in futures:
                if future.exception() is not None:
                    logger.error("Zone reading for %s: a worker failed", report_id, exc_info=future.exception())
            run.photos_sent = {
                "count": sum(len(job.photos) for job, entry in zip(jobs, entries) if entry.get("status") in ("ok", "failed")),
                "zones": entries,
            }
            run.parts_found = sum(len(a.items) for a in answers.values())
            if not answers:
                run.error_code = next((e.get("error_code") for e in reversed(entries) if e.get("error_code")), "skipped")
                raise _NoZoneAnswered("no zone call answered")
    except _NoZoneAnswered:
        return []

    reads = []
    for i, job in enumerate(jobs):
        answer = answers.get(i)
        if answer is None:
            continue
        label_to_id = {label: pid for label, pid, _, _ in job.photos}
        found = []
        for item in answer.items:
            item = dict(item)
            photo_id = label_to_id.get(item.get("photo_reference") or "")
            item["photo_reference"] = f"@{photo_id}" if photo_id else None
            if not photo_id:
                item["grid_cells"] = []
            found.append(item)
        reads.append((f"zone:{job.zone}", found))
    return reads
