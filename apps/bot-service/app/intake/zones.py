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
light models' own chain (settings.gemini_zone_chain, contract 9: never the
reading's, whose first model has ~20 calls a day on the free tier), for
the same actor and keys, through the same rate limit
(structured.call_structured), ZONE_WORKERS at a time. A zone call
that fails -- busy, quota, timeout, an answer that doesn't parse, the
person's allowance gone -- is skipped and noted in the run log; the
reading never fails for it. All the zone calls are one run of the log
(runs.ZONES_KIND), whose photos_sent says per zone what was sent and what
came back, the parts seen intact included. Contract 10: those intact lists
are also kept beside the AI draft, ai_draft["zone_intact"] = {"zone:rear":
[part, ...]} (read_and_merge's `intact`, intact_of), for the linked parts
to weigh; they never change the list -- a zone that calls a part intact
can be wrong about it. The assembly walk (settings.intake_assembly_walk,
intake/assembly_walk.py) is kept the same way: each zone read's yes/no
signs about its damaged anchor panels, merged into ai_draft[
"assembly_walk"] (read_and_merge's `walk`), for the linked parts.

Contract 9, option B (settings.intake_zone_only "suggest", the default; it
acts only in zone mode): the damage list keeps the parts the overall
reading found, with what the zone reads that agree add (found_by, seen_in,
a photo); a part ONLY zone reads found becomes a suggestion for a person to
add (split_suggestions), never a list item -- measured offline on m6, the
list then has fewer wrong sides and extras. "list": every find in the list
(contract 8's m5/m6 behaviour).

Contract 9, evidence (ai/evidence.py): every read's photo of a part is kept
as an Evidence entry -- the overall reading's with its bbox_2d, a zone
read's with its reason and box (settings.intake_zone_evidence) -- one per
photo, best first, the item's photo_reference first. The merge joins them
(_credit), a suggestion carries them, and pipeline._read_into settles them
against the photos (evidence.finish: frames, the sorter's boxes).
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.ai import allowance, evidence, runs
from app.ai.keys import AiActor, AiUnavailable
from app.ai.second_look import MAX_SEEN_IN, ZONES, covers_for, covers_of, other_side_note
from app.ai.zone_read import CLOSE, END_ZONES, SIDE_ZONES, ZONE_NAMES, ZoneAnswer, read_zone
from app.config import ZONE_PACKS, chain, pack_list, settings
from app.intake import assembly_walk
from app.intake.photos import ai_input_path
from app.parts.prefill import item_key, row_name
from app.reports.schema import SecondLookItem
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


def zone_chain() -> list[str]:
    """The zone calls' models (settings.gemini_zone_chain)."""
    return chain(settings.gemini_zone_chain)


def grid_on() -> bool:
    return bool(getattr(settings, "intake_grid_marks", False))


def evidence_mode() -> str:
    """What the zone calls ask for besides the part (settings.
    intake_zone_evidence): "off", "reason" or "reason_box"."""
    return getattr(settings, "intake_zone_evidence", "off") or "off"


def light_options() -> dict:
    """The light-model switches of the zone calls (plans/2026-11-18-light-
    detection): read_zone's packs (settings.intake_zone_packs) and photo_half
    (settings.intake_zone_photo_half), each only while on -- at the
    defaults {}, and read_zone is called as before. The assembly walk too
    (walk: the car map's assembly_walk.MapIndex), only while on and with a
    map that has signs (walk_index)."""
    out: dict = {}
    packs = pack_list(getattr(settings, "intake_zone_packs", ""), ZONE_PACKS, "intake_zone_packs")
    if packs:
        out["packs"] = packs
    if getattr(settings, "intake_zone_photo_half", False):
        out["photo_half"] = True
    walk = walk_index()
    if walk is not None:
        out["walk"] = walk
    return out


def walk_index() -> "assembly_walk.MapIndex | None":
    """The car map the assembly walk reads (car_map.MAP, checked once:
    assembly_walk.index_for), only with settings.intake_assembly_walk "on";
    None when off or there is no map with signs (logged: the walk is
    skipped, never an error)."""
    if getattr(settings, "intake_assembly_walk", "off") != "on":
        return None
    from app.intake import car_map

    return assembly_walk.index_for(car_map.MAP)


def suggest_zone_only() -> bool:
    """Option B: zone-only finds are suggestions (settings.intake_zone_only)."""
    return getattr(settings, "intake_zone_only", "suggest") == "suggest"


# ------------------------------------------------------------ Wave 2


def wrong_end_mode() -> str:
    """E11: settings.intake_zone_wrong_end -- "drop" (today), "shadow" or
    "keep"."""
    mode = getattr(settings, "intake_zone_wrong_end", "drop") or "drop"
    return mode if mode in ("drop", "shadow", "keep") else "drop"


@dataclass
class ZoneExtras:
    """Wave 2's options for the zone reads (plans/2026-11-22-enhance-staging
    3c), made only when one is on (extras_for) -- at the defaults no zone
    function is handed one, and every call is as before.

    E7: geometry_mode ("vote" / "tiebreak"; "shadow": logged only) and
    photo_sides ({photo id: "Left" / "Right"}, intake/geometry.photo_sides):
    a side zone's side_seen is reconciled with its photos' geometry side
    when they are unanimous, else each find with its own photo's (the
    review's M4; zone_read.parse_answer).
    E11: wrong_end_mode ("shadow" / "keep") -- an end zone that sees the
    other end has its finds parsed apart; _read_zones files them in
    wrong_end ({"zone:rear": {"seen": "front", "items": [... photos as
    "@<photo id>"]}}) and in the zone's run-log entry.

    Detection Round 2 (settings.intake_box_every, intake_zone_inventory;
    zone_read's "Round 2"), carried here the same way so every path that
    makes zone reads (the reading's, F1's, the parallel reads') has it: box_every,
    inventory (car_names: the inventory may name the car map's small
    parts, settings.intake_car_map_names "on"). listed: the drafted list
    the zone reads place (read_and_merge sets it; None while it isn't
    known -- reads started before or beside the main read -- and nothing
    is placed then). _read_zones fills located and inventory_rows (photos
    as "@<photo id>", each with its read), asked (each read's located
    names), zone_finds (the zone reads' own damage items) and zone_run_id;
    place_round2_boxes and add_inventory use them."""
    geometry_mode: str = "off"
    photo_sides: dict = field(default_factory=dict)
    wrong_end_mode: str = "drop"
    wrong_end: dict = field(default_factory=dict)
    box_every: bool = False
    inventory: bool = False
    car_names: bool = False
    listed: list | None = None
    located: list = field(default_factory=list)
    inventory_rows: list = field(default_factory=list)
    asked: dict = field(default_factory=dict)
    zone_finds: int | None = None
    zone_run_id: str | None = None
    # place_round2_boxes: list items with a box before the located ones.
    boxed_before: int | None = None

    def round2_on(self) -> bool:
        return bool(self.box_every or self.inventory)


def box_every_on() -> bool:
    """Round 2's R2-box: settings.intake_box_every is "on"."""
    return getattr(settings, "intake_box_every", "off") == "on"


def inventory_on() -> bool:
    """Round 2's R2-inv: settings.intake_zone_inventory is "on"."""
    return getattr(settings, "intake_zone_inventory", "off") == "on"


def extras_for(photos) -> "ZoneExtras | None":
    """The reading's ZoneExtras from the settings and its photos (IntakePhoto
    rows), or None when every Wave 2 and Round 2 zone option is at its
    default."""
    from app.intake import geometry

    how, end = geometry.mode(), wrong_end_mode()
    box_every, inventory = box_every_on(), inventory_on()
    if how == "off" and end == "drop" and not (box_every or inventory):
        return None
    return ZoneExtras(geometry_mode=how, photo_sides=geometry.photo_sides(photos) if how != "off" else {},
                      wrong_end_mode=end, box_every=box_every, inventory=inventory,
                      car_names=inventory and getattr(settings, "intake_car_map_names", "off") == "on")


def _zone_sides(job, extras) -> dict[str, str]:
    """E7: {label: its photo's geometry side} for a job's photos that have
    one."""
    return {label: extras.photo_sides[pid] for label, pid, _, _ in job.photos if pid in extras.photo_sides}


def _zone_geometry(job, extras) -> tuple | None:
    """E7, for read_zone: (the side the zone's photos' geometry names when
    they are unanimous, else None; the mode; {label: each photo's geometry
    side}) -- None for an end or close zone, or with the geometry off or in
    shadow (asked, not used)."""
    from app.intake.geometry import ACTING, unanimous

    if extras is None or extras.geometry_mode not in ACTING or job.zone not in SIDE_ZONES:
        return None
    sides = _zone_sides(job, extras)
    return unanimous(sides.values()), extras.geometry_mode, sides


def _extra_kwargs(job, extras) -> dict:
    """read_zone's Wave 2 arguments for this job, only those on ({} at the
    defaults: read_zone called as before)."""
    out: dict = {}
    geo = _zone_geometry(job, extras)
    if geo is not None:
        out["geometry"] = geo
    if extras is not None and extras.wrong_end_mode != "drop":
        out["wrong_end"] = extras.wrong_end_mode
    return out


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
    only where item had none, and grid cells only for item's own photo.
    Contract 9: found's evidence joins item's (one entry per photo, the
    better kept, item's photo first), and its reason when item has none
    yet; a photo filled in takes its box from that photo's entry (before,
    the box was dropped with it, and the zone's place for the part lost)."""
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
    evidence.join(item, evidence.entries_of(found, read))
    if not item.get("reason") and found.get("reason"):
        item["reason"] = found["reason"]
    evidence.settle(item, fill_box=True)


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
    found["evidence"] = evidence.entries_of(found, read)
    out.append(evidence.settle(found, fill_box=True))


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
      - Evidence (contract 9): each item's entries from every read that
        found it, one per photo (_credit); an overall item's own is its
        photo and bbox_2d, by "overall".

    Returns (items, the disputed "(side undetermined)" names, which the
    caller keeps out of resolve_sides)."""
    out: list[dict] = []
    for item in overall:
        item = dict(item)
        ref = item.get("photo_reference") or None
        item["found_by"] = [OVERALL]
        item["seen_in"] = [ref] if ref else []
        item["grid_cells"] = list(item.get("grid_cells") or [])
        # Copies: the inputs' entries are never changed.
        item["evidence"] = evidence.entries_of(item, OVERALL)
        out.append(evidence.settle(item, fill_box=True))
    disputed: set[int] = set()
    disputed_names: set[str] = set()
    for read, items in reads:
        for found in items:
            found = dict(found)
            found["grid_cells"] = list(found.get("grid_cells") or [])
            found["evidence"] = [dict(e) for e in found.get("evidence") or [] if isinstance(e, dict)]
            _merge_one(out, found, read, disputed, disputed_names)
    return out, disputed_names


def as_suggestion(item: dict) -> dict:
    """A merged item only zone reads found, as a SecondLookItem dict
    (source "zone"): the model's own confidence and side, untouched by
    resolve_sides and the agreement rule, which are for the list; its
    reason and evidence as the reads gave them. Its name in the firm's
    words and other_side_listed are set against the list once it is final
    (name_suggestions; second_look._store again against the form)."""
    part = str(item.get("part") or "")
    return SecondLookItem(
        key=item_key(item) or part, part=part, photo_reference=item.get("photo_reference") or None,
        condition_tokens=list(item.get("condition_tokens") or []), condition=item.get("condition"),
        damage_type=item.get("damage_type"), confidence=item.get("ai_confidence"), source="zone",
        covers=covers_of(part), found_by=list(item.get("found_by") or []),
        seen_in=list(item.get("seen_in") or [])[:MAX_SEEN_IN],
        reason=evidence.clean_reason(item.get("reason")),
        evidence=evidence.order_evidence(item.get("evidence"), item.get("photo_reference") or None),
        other_part_name=item.get("other_part_name"), severity=item.get("severity"), remarks=item.get("remarks"),
    ).model_dump()


def split_suggestions(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Option B (contract 9): merge_reads' items as (the list, the
    suggestions). An item the overall reading found stays in the list, with
    what agreeing zone reads added; one only zone reads found becomes a
    suggestion (as_suggestion), in the order found."""
    listed, suggested = [], []
    for item in items:
        if OVERALL in (item.get("found_by") or []):
            listed.append(item)
        elif item.get("part"):
            suggested.append(as_suggestion(item))
    return listed, suggested


def name_suggestions(suggestions: list[dict], listed: list[dict], db, vehicle: dict) -> list[dict]:
    """In place, once the list is final (pipeline._read_into, after the side
    resolver): each suggestion's name in the firm's words (prefill.row_name)
    and other_side_listed -- the list's part of the same family on the
    other side ("Right Tail Lamp" beside a suggested "Left Tail Lamp"), so a
    person sees the side may be the question, not the part."""
    parts = {str(it.get("part") or "") for it in listed if isinstance(it, dict)}
    for suggestion in suggestions:
        suggestion["name"] = row_name(db, suggestion["part"], vehicle or {})
        suggestion["other_side_listed"] = other_side_note(suggestion, parts)
    return suggestions


OTHER_SIDE = "other_side"


def other_side_suggestion(item: dict) -> dict:
    """F3 (settings.intake_keep_other_side): a list item the side resolver
    placed on a side already listed, from another photo (resolve_sides'
    other_side: the item, still "(side undetermined)", with other_side_of
    the listed name), as a suggestion -- "Other side? Tail Lamp (side
    undetermined)": source "other_side", Low, its covers without the listed
    name (second_look.covers_for), other_side_listed that name (the Review
    step's "Check this isn't the same damage on the other side")."""
    out = as_suggestion(item)
    out.update(source=OTHER_SIDE, confidence="Low", other_side_of=item["other_side_of"],
               covers=covers_for({**out, "other_side_of": item["other_side_of"]}),
               other_side_listed=item["other_side_of"])
    return out


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
    intact: dict[str, list[str]] | None = None,
    walk: dict | None = None,
    *,
    extras: ZoneExtras | None = None,
) -> tuple[list[dict], set[str], list[str]]:
    """The zone stage, for pipeline._read_into once the overall reading has
    answered: one call per job, then merge_reads. items: the overall
    reading's damage items ("@<photo id>" references). progress(n) after
    each zone call ends, n of len(jobs). Returns (items, disputed names,
    the reads that answered: "overall" and each zone read's name, for
    SecondLook.reads). Never raises: whatever goes wrong, the overall
    reading's items stand (with found_by "overall").

    intact, when given, is filled with what each zone read that answered
    saw undamaged, {"zone:rear": [canonical part, ...]} (contract 10,
    ai_draft["zone_intact"]); left empty when no zone read answered.

    walk, when given, is filled with the zone reads' assembly walk merged
    (assembly_walk.merge_all, photos as "@<photo id>"; ai_draft[
    "assembly_walk"]); left empty when no zone read walked.

    extras (Wave 2, ZoneExtras): passed on to zone_reads only when given.
    With Round 2's box_every, its listed is set to `items` first: the zone
    reads place the drafted list's parts."""
    if extras is not None and extras.box_every:
        extras.listed = [{"part": it.get("part"), "photo_reference": it.get("photo_reference")}
                         for it in items if isinstance(it, dict)]
    reads = zone_reads(report_id, actor, jobs, progress, intact, walk, **({"extras": extras} if extras else {}))
    return merged_with(report_id, items, reads)


def zone_reads(
    report_id: str,
    actor: AiActor,
    jobs: list[ZoneJob],
    progress: Callable[[int], None] | None = None,
    intact: dict[str, list[str]] | None = None,
    walk: dict | None = None,
    *,
    cancel: threading.Event | None = None,
    extras: ZoneExtras | None = None,
) -> list[tuple[str, list[dict]]]:
    """read_and_merge's calls alone: each zone read that answered as (read
    name, items), intact and walk filled as there. Never raises: [] when
    nothing answered. F1 (settings.intake_main_read_fallback) calls it
    before the overall reading's second try, and B2 (settings.
    intake_parallel_reads) beside the overall reading, then merged_with.
    cancel (B2): once set, no zone call starts (each left "skipped",
    "cancelled"); the ones in flight finish. extras (Wave 2): the zone
    reads' E7 / E11 options, its wrong_end filled (ZoneExtras)."""
    seen_intact: dict[str, list[str]] = {}
    seen_walk: dict = {}
    try:
        extra = {"cancel": cancel} if cancel is not None else {}
        if extras is not None:
            extra["extras"] = extras
        reads = _read_zones(report_id, actor, jobs, progress, seen_intact, seen_walk, **extra) if jobs else []
    except Exception:
        logger.exception("Zone reading for %s failed; the overall reading stands", report_id)
        reads, seen_intact, seen_walk = [], {}, {}
    if intact is not None:
        intact.update(seen_intact)
    if walk is not None:
        walk.update(seen_walk)
    return reads


def merged_with(report_id: str, items: list[dict], reads: list[tuple[str, list[dict]]]
                ) -> tuple[list[dict], set[str], list[str]]:
    """read_and_merge's merge alone: (items, disputed names, the reads that
    answered). Never raises: the overall reading's items stand."""
    answered = [OVERALL] + [name for name, _ in reads]
    try:
        merged, disputed = merge_reads(items, reads)
        return merged, disputed, answered
    except Exception:
        logger.exception("Zone reading for %s: couldn't merge; the overall reading stands", report_id)
        return items, set(), [OVERALL]


def _read_zones(report_id: str, actor: AiActor, jobs: list[ZoneJob],
                progress: Callable[[int], None] | None,
                intact: dict[str, list[str]] | None = None,
                walk: dict | None = None,
                cancel: threading.Event | None = None,
                extras: ZoneExtras | None = None) -> list[tuple[str, list[dict]]]:
    models = zone_chain()
    if cancel is not None:
        # B2's cancel also stops a zone call in flight at its current model
        # (no next model, no "all busy" round): the run ends sooner.
        from app.ai.second_look import _ChainUntil

        models = _ChainUntil(models, cancel)
    grid = grid_on()
    asked = evidence_mode()
    light = light_options()
    # Round 2's fallback (the review's M6): at most one request per model of
    # the chain, no all-busy round -- F1's second try's rule.
    tries_cap = max(1, len(zone_chain()))
    if extras is not None and extras.box_every and extras.listed is None and jobs and not round2_latched():
        # The review's M8: the zone reads run before (or beside) the main
        # read -- F1, intake_parallel_reads -- so there is no list to place.
        logger.warning("Reading %s: Round 2's box-every is on, but the zone reads ran before the main read's list "
                       "was known (parallel or fallback reads): nothing is located", report_id)
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
        # Round 2 (extras' box_every / inventory): what this call asks
        # beyond today's, and its run-log note; None while off.
        ask = answer = r2_note = None
        try:
            if stop.is_set():
                entry.update(status="skipped", note="no AI allowance left")
                return
            if cancel is not None and cancel.is_set():
                entry.update(status="skipped", note="cancelled")
                return
            if time.monotonic() - started > TIME_BOX:
                entry.update(status="skipped", note="out of time")
                return
            ask = _guarded(lambda: round2_ask(job, extras), None, "couldn't work out what to ask")
            r2_note = {} if ask is not None else None
            photos = [(label, path, kind) for label, _, path, kind in job.photos]
            if ask is not None:
                # The schema cap (the review's L2): the inventory left out
                # first, then locate.
                ask, trimmed = _guarded(lambda: zone_read_fit(job.zone, [p[0] for p in photos], ask, grid=grid,
                                                              evidence_mode=asked, **light), (None, []),
                                        "couldn't size the schema; asked without it")
                if trimmed:
                    r2_note["trimmed"] = trimmed
                    logger.warning("Zone %s of %s: Round 2's %s left out, the schema over its cap", job.zone,
                                   report_id, " and ".join(trimmed))
                if ask is not None and ask.locate:
                    extras.asked[f"zone:{job.zone}"] = list(ask.locate)
            try:
                # This worker's Gemini tries, kept apart for the zone's own
                # error code, then added to the stage's one run.
                with runs.collecting(attempts):
                    answer = read_zone(job.zone, photos, actor=actor, models=models, grid=grid, evidence_mode=asked,
                                       **light, **_extra_kwargs(job, extras),
                                       **({"r2": ask} if ask is not None else {}))
            except Exception as exc:
                answer = None
                first = attempts.snapshot()
                if (ask is not None and round2_fallback_worthy(exc, first) and not stop.is_set()
                        and not (cancel is not None and cancel.is_set()) and time.monotonic() - started <= TIME_BOX):
                    # Round 2: a failure its fields may have caused (a refused
                    # schema, a timeout, an answer that didn't parse) is asked
                    # once more as today, one request per model at most --
                    # the zone's finds are never lost for Round 2.
                    r2_note["fallback"] = runs.error_code_for(exc, first)
                    logger.warning("Zone %s of %s: asked again without Round 2 (%s)", job.zone, report_id,
                                   type(exc).__name__)
                    try:
                        with runs.collecting(attempts):
                            answer = read_zone(job.zone, photos, actor=actor, models=models, grid=grid,
                                               evidence_mode=asked, **light, **_extra_kwargs(job, extras),
                                               max_tries=tries_cap)
                    except Exception as again:
                        exc = again
                    else:
                        if round2_refused(first):
                            # Refused with the fields, answered without: the
                            # schema itself; later calls ask as today.
                            _latch_round2(job.zone, report_id, r2_note["fallback"])
                if answer is None:
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
            if extras is not None and extras.geometry_mode != "off" and job.zone in SIDE_ZONES:
                # E7: the side zone's geometry, as the run log keeps it (in
                # shadow too: what it would have said, nothing applied).
                from app.intake.geometry import tally, unanimous

                sides = _zone_sides(job, extras)
                entry["geometry"] = {"mode": extras.geometry_mode, "side": unanimous(sides.values()),
                                     "votes": tally(sides.values()), "model": answer.side_model or answer.side_seen,
                                     "per_find": answer.per_find}
            if extras is not None and extras.wrong_end_mode != "drop" and answer.note:
                # E11: what the end zone that saw the other end found.
                entry["wrong_end"] = {"mode": extras.wrong_end_mode,
                                      "parts": [str(it.get("part")) for it in answer.wrong_end_items]}
            if "walk" in light:
                # The assembly walk, short: each panel's hit_at and yes signs.
                entry["walk"] = assembly_walk.log_entry(answer.walk)
        finally:
            tries, size = attempts.drain()
            for attempt in tries:
                run.attempts.add(attempt)
            if size is not None:
                run.attempts.add_input(size)
            if error is not None:
                entry.update(status="failed", error_code=runs.error_code_for(error, tries))
            if r2_note is not None:
                entry["r2"] = _guarded(lambda: round2_log(ask, answer, tries, r2_note), {"error": True},
                                       "couldn't log the zone call")
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
            if extras is not None and extras.round2_on():
                # Round 2: what the zone reads placed and listed, and their
                # extra output (the pipeline adds box_coverage once the list
                # is final: note_round2).
                run.photos_sent["r2"] = _guarded(lambda: round2_summary(entries, extras), {"error": True},
                                                 "couldn't sum up the zone calls")
                extras.zone_run_id = run.id
                extras.zone_finds = sum(len(a.items) for a in answers.values())
            run.parts_found = sum(len(a.items) for a in answers.values())
            if not answers:
                run.error_code = next((e.get("error_code") for e in reversed(entries) if e.get("error_code")), "skipped")
                raise _NoZoneAnswered("no zone call answered")
    except _NoZoneAnswered:
        return []

    reads = []
    walks: list[tuple[str, dict, dict]] = []
    for i, job in enumerate(jobs):
        answer = answers.get(i)
        if answer is None:
            continue
        label_to_id = {label: pid for label, pid, _, _ in job.photos}
        if answer.walk:
            walks.append((f"zone:{job.zone}", answer.walk, label_to_id))
        found = [_by_photo_id(item, label_to_id) for item in answer.items]
        reads.append((f"zone:{job.zone}", found))
        if intact is not None:
            intact[f"zone:{job.zone}"] = list(answer.intact)
        if extras is not None and answer.wrong_end_items:
            # E11: its finds apart, photos by id as the reads' are.
            extras.wrong_end[f"zone:{job.zone}"] = {
                "seen": answer.side_seen, "items": [_by_photo_id(it, label_to_id) for it in answer.wrong_end_items]}
        if extras is not None and extras.round2_on():
            # Round 2: what it located and listed, photos by id, with its read.
            for rows, into in ((answer.located, extras.located), (answer.inventory, extras.inventory_rows)):
                for row in rows:
                    pid = label_to_id.get(row.get("photo") or "")
                    if pid:
                        into.append({**row, "photo": f"@{pid}", "read": f"zone:{job.zone}"})
    if walk is not None and walks:
        try:
            walk.update(assembly_walk.merge_all([(read, assembly_walk.with_photo_ids(found, ids))
                                                 for read, found, ids in walks]))
        except Exception:  # never the reason the zone reads are lost
            logger.exception("Zone reading for %s: couldn't merge the assembly walk; none kept", report_id)
    return reads


def _guarded(work: Callable, default, what: str):
    """Round 2's helpers in the zone stage (the review's L3): work(), or
    default -- logged -- when it raises. Never the reason a zone call, or
    the reading, is lost."""
    try:
        return work()
    except Exception:
        logger.exception("Round 2: %s", what)
        return default


def zone_read_fit(zone: str, labels: list[str], ask, **options):
    from app.ai.zone_read import fit_round2

    return fit_round2(zone, labels, ask, **options)


def _by_photo_id(item: dict, label_to_id: dict[str, str]) -> dict:
    """A zone read's item (a copy) with its photo as "@<photo id>": a label
    not sent becomes None (and its grid cells go), and an evidence entry on
    a label not sent goes."""
    item = dict(item)
    photo_id = label_to_id.get(item.get("photo_reference") or "")
    item["photo_reference"] = f"@{photo_id}" if photo_id else None
    if not photo_id:
        item["grid_cells"] = []
    # Its evidence by photo id too; an entry on a label not sent goes.
    item["evidence"] = [{**e, "photo": f"@{label_to_id[e['photo']]}"}
                        for e in item.get("evidence") or [] if label_to_id.get(e.get("photo") or "")]
    return item


def wrong_end_suggestions(wrong_end: dict) -> list[dict]:
    """E11 "keep": each find of an end zone that saw the other end (ZoneExtras.
    wrong_end) as a zone suggestion (as_suggestion: source "zone", found by
    that read, its photo, reason and evidence) with wrong_end_note -- "The
    rear read thought these photos show the front." -- never merged with
    the reads (it neither credits nor disputes a list part) and never on
    the list. The review's M5: its side was worked out by a read wrong about
    which end it saw (or the part is of the wrong end), so it goes by its
    "(side undetermined)" name, and Low whatever the model said;
    second_look._store ranks it after every other suggestion and drops it
    behind a twin a read genuinely found (the note stays on it alone). A
    part listed or dismissed goes, as any suggestion."""
    from app.ai.zone_read import wrong_end_note

    out = []
    for read, block in wrong_end.items():
        zone = read.split(":", 1)[-1]
        for item in block.get("items") or []:
            if not item.get("part"):
                continue
            part = str(item["part"])
            found = {**item, "part": unsided_part(part) or part, "ai_confidence": "Low", "found_by": [read],
                     "seen_in": [item["photo_reference"]] if item.get("photo_reference") else []}
            suggestion = as_suggestion(found)
            suggestion["wrong_end_note"] = wrong_end_note(zone, str(block.get("seen") or ""))
            out.append(suggestion)
    return out


def intact_of(ai_draft) -> dict[str, list[str]]:
    """ai_draft["zone_intact"] as a zone-mode reading kept it (contract 10):
    {zone read: [canonical part, ...]}. {} for a draft from before it, a
    single-mode reading, or anything that isn't that shape."""
    raw = ai_draft.get("zone_intact") if isinstance(ai_draft, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {str(read): [part for part in parts if isinstance(part, str)]
            for read, parts in raw.items() if isinstance(parts, list)}


# ------------------------------------------------------------ Round 2
#
# Detection Round 2 (zone_read's "Round 2"; settings.intake_box_every,
# "R2-box", and intake_zone_inventory, "R2-inv"; zone mode only). Off:
# nothing here runs, and no zone call, merge or stored draft changes. On,
# with no extra call -- but one, as today, after a zone call its fields
# made fail:
#   - round2_ask: what each zone call asks beyond today's, fit to the
#     schema cap (zone_read.fit_round2) by _read_zones;
#   - place_round2_boxes (pipeline, right after evidence.finish, so the
#     sorter's boxes the reading shows are on by then and stand): R2-box
#     puts a located part box on each list item that has no box
#     (place_boxes);
#   - drop_crossed_boxes and add_inventory (pipeline, after the side
#     resolver): a located box its side now contradicts goes; R2-inv keeps
#     the inventory apart (ai_draft["zone_inventory"]) and makes its damaged
#     parts nothing listed or suggested suggestions (inventory_suggestions);
#   - box_coverage / note_round2: how many list items have a box -- with the
#     zone reads' own finds and the list's agreement, to compare on and off
#     -- in the AI draft and in the zone run's log.

LOCATE_BY = evidence.LOCATED
# A suggestion from a zone read's inventory is found by "zone_inventory:
# <zone>", never by the zone read's own name: the AI-accuracy record and
# the parts library count it apart from the reads' damage finds (the
# review's M7).
INVENTORY_READ = "zone_inventory:"
INVENTORY_MAX_SUGGESTIONS = 6
INVENTORY_NOTE = "Seen in the zone check: marked damaged among the parts in view."
_CONFIDENCE_RANK = {"High": 0, "Medium": 1, "Low": 2}

# The review's M6: once a zone call with Round 2's fields was refused and
# the same call without them answered, every later zone call of this
# process asks without them, until it restarts -- a refused schema would
# be refused again, each time costing a request per model. Logged once.
_LATCH = threading.Event()
_LATCH_LOCK = threading.Lock()


def round2_latched() -> bool:
    return _LATCH.is_set()


def reset_round2_latch() -> None:
    """Round 2 asked again (the tests)."""
    _LATCH.clear()


def _latch_round2(zone: str, report_id: str, code: str) -> None:
    with _LATCH_LOCK:
        if _LATCH.is_set():
            return
        _LATCH.set()
    logger.warning("Round 2: the %s zone call of %s was refused with Round 2's fields (%s) and answered without them; "
                   "every zone call of this process now asks without them until it restarts", zone, report_id, code)


def round2_ask(job: ZoneJob, extras: "ZoneExtras | None"):
    """The zone_read.Round2Ask for one zone call, or None -- Round 2 off or
    latched (a refused schema), or nothing to ask in this zone (the call is
    then today's). R2-box: the listed parts this zone's checklist can name
    (extras.listed, none while the list isn't known), each with the labels
    of its own photos among the call's; R2-inv: the zone's inventory
    names."""
    from app.ai import zone_read

    if extras is None or not extras.round2_on() or round2_latched():
        return None
    locate: list[str] = []
    hints: dict[str, list[str]] = {}
    if extras.box_every and extras.listed:
        label_of = {pid: label for label, pid, _, _ in job.photos}
        locate = zone_read.locate_names(job.zone, [it.get("part") for it in extras.listed])
        for it in extras.listed:
            label = label_of.get(evidence.photo_id(it.get("photo_reference")) or "")
            if not label:
                continue
            for name in zone_read.locate_names_of(job.zone, str(it.get("part") or "")):
                if name in locate and label not in hints.get(name, []):
                    hints.setdefault(name, []).append(label)
    inventory = zone_read.inventory_names(job.zone, extras.car_names) if extras.inventory else []
    ask = zone_read.Round2Ask(locate=locate, hints=hints, inventory=inventory)
    return ask if ask.on() else None


def round2_fallback_worthy(exc: BaseException, tries: list[dict]) -> bool:
    """Whether a zone call with Round 2's fields failed in a way they may
    have caused -- the schema refused (an error that isn't "busy"), a
    timeout, an answer that didn't parse -- so it is asked once more as
    today. Never for the person's AI going away or the day's allowance, nor
    for a chain that was only busy or rate-limited."""
    if isinstance(exc, (AiUnavailable, allowance.DayUsedUp)):
        return False
    return any(a.get("outcome") in ("schema", "timeout") or (a.get("outcome") == "error" and not a.get("busy"))
               for a in tries or [] if isinstance(a, dict))


def round2_refused(tries: list[dict]) -> bool:
    """A try Google refused outright (an error that isn't "busy": a 400 for
    the schema), not a timeout or an answer that didn't parse."""
    return any(isinstance(a, dict) and a.get("outcome") == "error" and not a.get("busy") for a in tries or [])


def _extra_tokens(answer, tries: list[dict]) -> int:
    """The output tokens Round 2's fields took, estimated: the answering
    try's output tokens by the fields' share of the answer's characters,
    else a token for every four characters."""
    if not answer.r2_chars:
        return 0
    used = next((a.get("output_tokens") for a in reversed(tries or []) if a.get("outcome") == "ok"), None)
    if isinstance(used, int) and not isinstance(used, bool) and used > 0 and answer.answer_chars > 0:
        return round(used * answer.r2_chars / answer.answer_chars)
    return round(answer.r2_chars / 4)


def round2_log(ask, answer, tries: list[dict], note: dict) -> dict:
    """A zone call's Round 2 in the run log (its zones entry's "r2"): what
    was asked, what came back, the extra output, and whether the answer
    was cut off (salvaged), the schema too big for a field (trimmed) or
    the call asked again as today (fallback). Counts only: no photo, no
    part name."""
    out: dict = {"locate_asked": len(ask.locate) if ask is not None else 0,
                 "inventory_asked": bool(ask is not None and ask.inventory)}
    if answer is not None:
        out.update(located=len(answer.located), inventory=len(answer.inventory),
                   inventory_damaged=sum(1 for row in answer.inventory if row.get("damaged")),
                   extra_chars=answer.r2_chars, answer_chars=answer.answer_chars,
                   extra_output_tokens_est=_extra_tokens(answer, tries))
        if answer.r2_salvaged:
            out["salvaged"] = True
    out.update(note or {})
    return out


def round2_summary(entries: list[dict], extras: "ZoneExtras") -> dict:
    """The zone run's Round 2 totals (photos_sent["r2"]): what was placed
    and listed, the extra output, and the zone reads' own finds (to set
    against a run with Round 2 off)."""
    logs = [e.get("r2") for e in entries if isinstance(e.get("r2"), dict)]

    def total(name: str) -> int:
        return sum(int(log.get(name) or 0) for log in logs)

    return {"box_every": extras.box_every, "inventory": extras.inventory, "car_names": extras.car_names,
            "list_known": extras.listed is not None, "latched": round2_latched(), "calls_asked": len(logs),
            "located": total("located"), "inventory_rows": total("inventory"),
            "inventory_damaged": total("inventory_damaged"),
            "extra_output_tokens_est": total("extra_output_tokens_est"),
            "salvaged": sum(1 for log in logs if log.get("salvaged")),
            "trimmed": sum(1 for log in logs if log.get("trimmed")),
            "fallbacks": sum(1 for log in logs if log.get("fallback")),
            "zone_finds": sum(int(e.get("found") or 0) for e in entries if e.get("status") == "ok")}


def _standing(ref, facts: dict | None) -> bool:
    """A box on this photo stands (evidence.finish keeps it): the AI got
    it as its preview, not raw. Without facts, every photo does."""
    if facts is None:
        return True
    found = facts.get(evidence.photo_id(ref))
    return found is not None and not found.raw


def has_standing_box(item: dict, facts: dict | None = None) -> bool:
    """A list item already boxed, for good: its bbox_2d, or an evidence
    entry's box -- the AI's, the sorter's (evidence.finish has put the
    sorter's on by the time Round 2 places boxes: the review's M1) or a
    located one -- on a photo where it stands."""
    if item.get("bbox_2d") and _standing(item.get("photo_reference"), facts):
        return True
    return any(isinstance(e, dict) and e.get("box") and _standing(e.get("photo"), facts)
               for e in item.get("evidence") or [])


def _zone_of(source: dict) -> str:
    return str(source.get("read") or "").split(":", 1)[-1]


def _framed(photo: str, by: str, box, facts: dict | None, *, kind: str = "ai", confidence=None) -> dict:
    """An evidence entry made after evidence.finish, finished as it would
    have been: no box on a photo the AI got raw, the frame set."""
    found = facts.get(evidence.photo_id(photo)) if facts is not None else None
    raw = facts is not None and (found is None or found.raw)
    e = evidence.entry(photo, by, box=evidence.clean_box(box, raw_original=raw), kind=kind, confidence=confidence)
    frame = evidence.frame_of(found.ai_path) if e["box"] and found is not None else None
    e["img_w"], e["img_h"] = frame if frame else (None, None)
    return e


def place_boxes(listed: list[dict], sources: list[dict], facts: dict | None = None, disputed=(),
                asked: dict | None = None) -> int:
    """R2-box, in place: each list item with no box (has_standing_box) and
    a free evidence slot gets a located part box from a source ({"part",
    "photo" "@<id>", "box", "read"}: a zone read's located part, or -- via
    "inventory" -- its inventory's):
      - the same part (zone_read.place_match 2); a side the reads disputed
        (disputed names; the review's M3) takes only that;
      - else its family when that is one part that isn't itself listed
        (the review's M4): a "(side undetermined)" item whose sided names
        were placed on one side only -- an end zone's only when both sides
        were asked in that call (asked: {read: names}; the review's L5) --
        or a sided item a close-up placed by its unsided name that no
        other listed item is;
      - on the item's own photo, else a photo it was seen in, else any
        (the review's M5).
    The entry is by "locate:<read>", box_kind "part", with no reason and no
    confidence (L8), finished as evidence.finish would (no box on a raw
    photo, its frame), kept after the reads' entries (evidence.
    order_evidence): it never takes a read's entry's place, and it is not
    added when the item has MAX_ENTRIES already. found_by, seen_in, the
    confidence, the part and bbox_2d never change. Returns how many."""
    from app.ai.zone_read import END_ZONES, place_match, twin_name

    usable = [s for s in sources or [] if isinstance(s, dict) and s.get("part") and evidence.clean_box(s.get("box"))
              and s.get("photo") and _standing(s.get("photo"), facts)]
    parts = {str(it.get("part") or "") for it in listed if isinstance(it, dict)}
    disputed = set(disputed or ())
    placed = 0
    for item in listed:
        if not isinstance(item, dict) or has_standing_box(item, facts):
            continue
        part = str(item.get("part") or "")
        if not part or part == OTHER_PART:
            continue
        entries = [e for e in item.get("evidence") or [] if isinstance(e, dict)]
        if len(entries) >= evidence.MAX_ENTRIES:
            continue
        pick = [s for s in usable if place_match(part, s["part"], _zone_of(s)) == 2]
        if not pick and part not in disputed:
            loose = [s for s in usable if place_match(part, s["part"], _zone_of(s)) == 1 and s["part"] not in parts]
            if len({s["part"] for s in loose}) != 1:
                continue
            if part.endswith(UNDETERMINED_SUFFIX):
                if asked is not None and any(
                        s.get("via") != "inventory" and _zone_of(s) in END_ZONES
                        and twin_name(s["part"]) not in (asked.get(str(s.get("read") or "")) or ()) for s in loose):
                    continue
            elif any(other != part and place_match(other, loose[0]["part"], CLOSE) for other in parts):
                continue
            pick = loose
        if not pick:
            continue
        seen = {item.get("photo_reference")} | set(item.get("seen_in") or []) | {e.get("photo") for e in entries}
        chosen = (next((s for s in pick if s["photo"] == item.get("photo_reference")), None)
                  or next((s for s in pick if s["photo"] in seen), None) or pick[0])
        new = _framed(chosen["photo"], f"{LOCATE_BY}{chosen.get('read') or 'zone'}", chosen["box"], facts,
                      kind=evidence.PART)
        if not new["box"]:
            continue
        item["evidence"] = evidence.order_evidence(entries + [new], item.get("photo_reference"))
        placed += 1
    return placed


def place_round2_boxes(listed: list[dict], extras: "ZoneExtras", facts: dict | None = None, disputed=()) -> int:
    """R2-box on a reading's list, in place (pipeline._read_into, right
    after evidence.finish): boxed_before is what R2-box off shows -- every
    box the reading put on, the sorter's too -- then place_boxes with the
    zone reads' located parts, then their inventory's."""
    extras.boxed_before = sum(1 for it in listed if isinstance(it, dict) and has_standing_box(it, facts))
    sources = list(extras.located) + ([{**row, "via": "inventory"} for row in extras.inventory_rows]
                                      if extras.inventory else [])
    return place_boxes(listed, sources, facts, disputed, extras.asked)


def inventory_suggestions(rows: list[dict], listed: list[dict], suggested: list[dict],
                          facts: dict | None = None) -> list[dict]:
    """R2-inv: the inventory's damaged parts (rows: {"part", "photo"
    "@<id>", "damaged", "confidence", "box", "read"}) as zone suggestions
    (as_suggestion: source "zone", its photo and box) found by
    "zone_inventory:<zone>" (INVENTORY_READ), with INVENTORY_NOTE -- none
    that the list or a suggestion already is (second_look.covers_of: a
    sided name and its "(side undetermined)" one are one part), one per
    part, surest first (no confidence is Low), at most
    INVENTORY_MAX_SUGGESTIONS. The other side's twin of a listed part stays
    a suggestion; name_suggestions notes the listed one beside it."""
    listed_parts = {str(it.get("part") or "") for it in listed if isinstance(it, dict)}
    taken = {str(s.get("part") or "") for s in suggested if isinstance(s, dict)}
    damaged = [r for r in rows or [] if isinstance(r, dict) and r.get("damaged") and r.get("part")]
    damaged.sort(key=lambda r: _CONFIDENCE_RANK.get(r.get("confidence") or "Low", 2))
    out: list[dict] = []
    for row in damaged:
        part = str(row["part"])
        if set(covers_of(part)) & (listed_parts | taken):
            continue
        confidence = row.get("confidence") or "Low"
        photo = row.get("photo") or None
        read = f"{INVENTORY_READ}{_zone_of(row)}"
        found = {"part": part, "photo_reference": photo, "ai_confidence": confidence, "found_by": [read],
                 "seen_in": [photo] if photo else [],
                 "evidence": [_framed(photo, read, row.get("box"), facts, confidence=confidence)] if photo else []}
        suggestion = as_suggestion(found)
        suggestion["inventory_note"] = INVENTORY_NOTE
        out.append(suggestion)
        taken.add(part)
        if len(out) >= INVENTORY_MAX_SUGGESTIONS:
            break
    return out


def add_inventory(listed: list[dict], zone_draft: dict, extras: "ZoneExtras", facts: dict | None = None) -> None:
    """R2-inv on a reading, in place (pipeline._read_into, after the side
    resolver -- the list's sides as final, the review's L6): the inventory
    kept as ai_draft["zone_inventory"] (one row a part a photo: read, part,
    photo_reference, damaged, confidence, box) and its suggestions added to
    ai_draft["zone_suggestions"]."""
    zone_draft["zone_inventory"] = [
        {"read": row.get("read"), "part": row.get("part"), "photo_reference": row.get("photo"),
         "damaged": bool(row.get("damaged")), "confidence": row.get("confidence"), "box": row.get("box")}
        for row in extras.inventory_rows]
    added = inventory_suggestions(extras.inventory_rows, listed, zone_draft.get("zone_suggestions") or [], facts)
    if added:
        zone_draft.setdefault("zone_suggestions", []).extend(added)


def drop_crossed_boxes(listed: list[dict], extras: "ZoneExtras") -> int:
    """R2-box, in place, after the side resolver (pipeline): a located box
    that a "(side undetermined)" item took from one side's name ("Left Tail
    Lamp") is dropped once the resolver has put the item on the other side
    -- a box on the wrong lamp misleads more than none. Returns how many."""
    names: dict[tuple, list[str]] = {}
    for source in list(extras.located) + list(extras.inventory_rows):
        box = evidence.clean_box(source.get("box"))
        if box and source.get("photo"):
            key = (source["photo"], tuple(box), str(source.get("read") or "zone"))
            names.setdefault(key, []).append(str(source.get("part") or ""))
    dropped = 0
    for item in listed:
        part = str(item.get("part") or "") if isinstance(item, dict) else ""
        side = split_side(part)[1]
        if not side:
            continue
        family = unsided_part(part) or part
        kept = []
        for e in item.get("evidence") or []:
            if evidence.located(e) and e.get("box"):
                placed = [p for p in names.get((e.get("photo"), tuple(e["box"]), str(e["by"])[len(LOCATE_BY):]), [])
                          if (unsided_part(p) or p) == family]
                if placed and all(split_side(p)[1] not in (None, side) for p in placed):
                    dropped += 1
                    continue
            kept.append(e)
        if len(kept) != len(item.get("evidence") or []):
            item["evidence"] = kept
    return dropped


def box_coverage(listed: list[dict], boxed_before: int | None = None, zone_finds: int | None = None) -> dict:
    """How many list items have a box (ai_draft["box_coverage"], and the
    zone run's log): items; boxed (any box: the AI's, the sorter's, a
    located part box or bbox_2d); ai_boxed (the AI's damage box or
    bbox_2d); located (a Round 2 located part box); boxed_before_locate
    (what R2-box off shows of these answers). And, to set on against off
    (the review's M2): list_agreed (items two or more reads found),
    list_low (items marked Low: the Review step's "Check") and zone_finds
    (the zone reads' own damage items)."""
    items = [it for it in listed or [] if isinstance(it, dict)]

    def boxes(it: dict) -> list[dict]:
        return [e for e in it.get("evidence") or [] if isinstance(e, dict) and e.get("box")]

    out = {"items": len(items),
           "boxed": sum(1 for it in items if boxes(it) or it.get("bbox_2d")),
           "ai_boxed": sum(1 for it in items if it.get("bbox_2d") or any(e.get("box_kind") == "ai" for e in boxes(it))),
           "located": sum(1 for it in items if any(evidence.located(e) for e in boxes(it))),
           "list_agreed": sum(1 for it in items if len(set(it.get("found_by") or [])) >= 2),
           "list_low": sum(1 for it in items if it.get("ai_confidence") == "Low")}
    if boxed_before is not None:
        out["boxed_before_locate"] = boxed_before
    if zone_finds is not None:
        out["zone_finds"] = zone_finds
    return out


def note_round2(extras: "ZoneExtras", coverage: dict | None) -> None:
    """The final box coverage into the zone run's log (photos_sent
    ["box_coverage"]), once the list is final. Never raises."""
    if coverage is None or not extras.zone_run_id:
        return
    try:
        runs.add_detail(extras.zone_run_id, "box_coverage", coverage)
    except Exception:
        logger.warning("Round 2: couldn't note the box coverage in the run log", exc_info=True)
