"""One area of the car read with that area's checklist (contract 8 D2).

Why: the light reader (gemini-3.5-flash-lite) drafting a whole case in one
call found 14 of the 73 parts on the four practice cases, against 25 for
the strong model, and the sides it got wrong came from the sorter's photo
areas. Asked about everything at once it stops at the obvious panels. Here
it is asked about one area at a time -- the front, the rear, one side, or
close-ups whose area wasn't clear -- with only that area's parts to choose
from, and a short prompt and a small schema the light models handle: the
damage items (a subset of reports/schema.DamageSummaryItem's model fields,
so they merge into the overall reading as they are), the parts it saw
intact, and the side the photos really show.

The area is the sorter's guess (intake/zones.plan_zones), so the call says
what the photos really show first (zone_side_seen), and that decides:
  - a side zone's parts are offered WITHOUT a side ("Front Door") and take
    the side the model saw for the whole set of photos -- its wide shots
    show which way the car faces, which a close-up alone doesn't; the
    sorter's left or right is never used. "unclear": the "(side
    undetermined)" names, as the reader would use;
  - an end zone (front, rear) offers both sides' parts: one rear photo
    shows both tail lamps, so the side is the model's, per part. Photos
    that really show the other end mean the checklist was the wrong one:
    the zone's finds are dropped;
  - "close" (damage close-ups whose area wasn't clear) offers every outside
    part without a side: a lamp filling the frame shows no LH or RH.

intake/zones.py plans the zones, makes the calls (purpose "draft_zone": the
light models' own chain, settings.gemini_zone_chain, with the reading's
thinking level per model and never its tools -- structured.CALL_KINDS)
and merges the answers.

Contract 9 (settings.intake_zone_evidence): each damage item may also say
why -- `reason` -- and where -- `box_2d`, Gemini's own box convention --
as two optional fields after ai_confidence and one prompt sentence. They
become the item's reports/schema.Evidence entry (ai/evidence.py), never
fields of the drafting schema. "off" (the default) asks for neither: the
request is as before, byte for byte.

The assembly walk (plans/2026-11-18-car-knowledge/K1-design.md 5a;
settings.intake_assembly_walk, intake/assembly_walk.py): with a car map
that has signs, `walk` (its assembly_walk.MapIndex) adds one paragraph and
a line per anchor panel this zone offers (after the packs, before the
closing line) and an `assembly_walk` field after `damage`; the answer's
walk is ZoneAnswer.walk. None (the default): the request as before, byte
for byte. A zone that offers no anchor with a sign is asked nothing new.

Detection Round 2 (settings.intake_box_every, intake_zone_inventory; the
"Round 2" section below): `r2` (a Round2Ask) adds, after everything else,
a `locate` field -- a box round each part already on the drafted list
that the photos show, damaged or not: where it is, never a new find --
and/or an `inventory` field -- the area's parts each photo shows, damaged
or not, each with a box. Both last in the schema, so an answer cut off in
them is read as today's (salvage). None (the default): the request as
before, byte for byte.
"""
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from app.ai import evidence, prompt_packs, runs
from app.ai.extraction import _firm_condition, _sanitize_damage_summary
from app.ai.grid import CELLS, grid_path
from app.ai.keys import AiActor
from app.ai.second_look import ZONES
from app.ai.structured import call_structured, image_part
from app.intake import assembly_walk
from app.reports.schema import DamageSummaryItem, SecurityIncidentDraft
from app.reports.taxonomy import (
    CANONICAL_PARTS,
    CANONICAL_SEVERITIES,
    FIRM_CONDITION_TOKENS,
    FIRM_REMARKS,
    OTHER_PART,
    UNDETERMINED_SUFFIX,
    known_part,
    split_side,
    unsided_part,
)

logger = logging.getLogger(__name__)

PURPOSE = "draft_zone"
END_ZONES = ("front", "rear")
SIDE_ZONES = ("left", "right")
CLOSE = "close"
ZONE_NAMES = END_ZONES + SIDE_ZONES + (CLOSE,)
# One model try. The overall reading's 32 photos answer well inside its
# 120 s; a zone sends at most 12 (intake/zones.MAX_ZONE_PHOTOS).
ZONE_TIMEOUT = 90.0
CONFIDENCES = ["High", "Medium", "Low"]
UNCLEAR = "unclear"

# What zone_side_seen may say, per kind of zone.
_SEEN = {
    "end": ["front", "rear", UNCLEAR],
    "side": ["left", "right", UNCLEAR],
    CLOSE: ["front", "rear", "left", "right", UNCLEAR],
}
_LABEL_RE = re.compile(r"^P0*(\d+)$")
_CELL_RE = re.compile(r"^[ABC][123]$")


def _kind(zone: str) -> str:
    return "end" if zone in END_ZONES else "side" if zone in SIDE_ZONES else CLOSE


def _families(zone: str) -> list[str]:
    """A side zone's parts without their side, in second_look.ZONES order:
    "Left Front Door" -> "Front Door". Left and right list the same ones."""
    return list(dict.fromkeys(split_side(p)[0] for p in ZONES[zone]))


def _close_parts() -> list[str]:
    """Every part of the four areas, by its "(side undetermined)" name where
    it has a side: a close-up shows which part, rarely which side."""
    names = [unsided_part(p) or p for zone in ("rear", "front", "left", "right") for p in ZONES[zone]]
    return list(dict.fromkeys(n for n in names if n in CANONICAL_PARTS))


def checklist(zone: str) -> list[str]:
    """The part names the zone call may answer with (OTHER_PART last)."""
    if zone in END_ZONES:
        names = list(ZONES[zone])
    elif zone in SIDE_ZONES:
        names = _families(zone)
    elif zone == CLOSE:
        names = _close_parts()
    else:
        raise ValueError(f"no such zone {zone!r}")
    return names + [OTHER_PART]


def canonical(zone: str, name, side_seen: str) -> str | None:
    """A name the zone call answered with, as a canonical part: a side
    zone's family takes the side seen ("Front Door" + left -> "Left Front
    Door"), or its "(side undetermined)" name when the side is unclear.
    None for a name the zone doesn't offer."""
    name = str(name or "").strip()
    if name not in checklist(zone):
        return None
    if zone not in SIDE_ZONES or name == OTHER_PART:
        return name
    if side_seen in SIDE_ZONES:
        sided = f"{side_seen.capitalize()} {name}"
        return sided if sided in CANONICAL_PARTS else None
    return unsided_part(f"Left {name}")


# ------------------------------------------------------------ the request

_AREA_TEXT = {
    "end": (
        "These photos were sorted as the car's {where} -- a quick sorter's guess. First set zone_side_seen to what "
        "they really show: 'front', 'rear', or 'unclear'. Part names carry their side (Left / Right) where they have one."
    ),
    "side": (
        "These photos were sorted as one side of the car -- a quick sorter's guess. First set zone_side_seen to the "
        "side they really show, as the car's own side: 'left' or 'right', or 'unclear' when you can't tell or they "
        "show both sides. The part names here carry no side: zone_side_seen gives it."
    ),
    CLOSE: (
        "These are close-ups of damage whose place on the car wasn't clear. First set zone_side_seen to where on the "
        "car they are: 'front', 'rear', 'left', 'right', or 'unclear'. The part names here carry no side."
    ),
}
# The rules the overall reading's prompt has for this area's parts, short.
_ZONE_NOTES = {
    "rear": (
        "'Boot Lid' also means a tailgate or hatch door; the 'Boot Lid ...' parts are mounted on it. A lamp on the "
        "body beside the boot opening is a 'Tail Lamp'; a reflector in the bumper is a 'Rear Bumper Reflector'.\n"
    ),
    "left": "'Wheel' is the rim and 'Tyre' the tyre.\n",
    "right": "'Wheel' is the rim and 'Tyre' the tyre.\n",
    CLOSE: "'Wheel' is the rim and 'Tyre' the tyre.\n",
}
# The one sentence grid marks add (settings.intake_grid_marks): where the
# damage is before what it is.
GRID_SENTENCE = (
    "Each photo has a light 3x3 grid on it, cells A1 to C3 (rows A to C from the top, columns 1 to 3 from the left): "
    "for each damaged part first set grid_cells to the cells the damage covers, then name the panel in those cells.\n"
)
# The one sentence the evidence tail fields add (settings.
# intake_zone_evidence): words the Review step shows as "AI says", and a box
# on the photo it cites. No numbers: a reason that quotes the plate is
# shown to people (evidence.clean_reason drops one all the same).
EVIDENCE_OFF = "off"
EVIDENCE_SENTENCE = {
    "reason": (
        "For each damage item also give reason: what you can see that shows this part is damaged, at most 15 words, "
        "no plate or other numbers.\n"
    ),
    "reason_box": (
        "For each damage item also give reason: what you can see that shows this part is damaged, at most 15 words, "
        "no plate or other numbers; and box_2d: [y_min, x_min, y_max, x_max] on 0-1000 round the damage in its "
        "photo_reference photo -- leave box_2d out if you can't place it.\n"
    ),
}


def _evidence_mode(value) -> str:
    return value if value in EVIDENCE_SENTENCE else EVIDENCE_OFF


# The prompt's lines that a light-model pack takes the place of, or goes
# before (system_prompt), as they are sent.
_INTACT_LINE = "In 'checked_intact' list the allowed parts you can see clearly in these photos with no damage.\n"
_SIDES_LINE = (
    "Left and right are the car's own sides as its driver sees them, never the photo's: work out first which way "
    "the car's front points -- front toward the camera, the car's right is on the photo's left; tail lamps toward "
    "the camera, the car's left is on the photo's left.\n"
)
_CLOSING = (
    "Only the insured car, never the other party's vehicle. Only damage you can see: a wrong part or side is "
    "worse than none."
)
# Light-model detection (plans/2026-11-18-light-detection/P-prompts.md 3.3;
# settings.intake_zone_packs, ai/prompt_packs.py), each pack in today's
# prompt: intact-v1 in place of the checked_intact line (Z2), orient-v1 in
# place of the sides line (Z3) -- for a side or close-up zone with P's one
# line after it -- and cues-v1 (Z3) and this zone's rows of small-v1 (Z1)
# before the closing line. Off: the prompt as before.
_ORIENT_NO_SIDE = "Here the part names carry no side: zone_side_seen gives it.\n"
# Z5 (settings.intake_zone_photo_half), end zones only: where each find is
# in its photo, as the camera sees it; code turns it into the car's side
# (side_from_half). Not P's words -- P gave the field, not a sentence.
PHOTO_HALVES = ("left", "centre", "right")
PHOTO_HALF_SENTENCE = (
    "For each damage item also give photo_half: where its damage is in its photo_reference photo, as the photo shows "
    "it -- the photo's left half, its centre, or its right half.\n"
)


def _photo_half_on(zone: str, photo_half: bool) -> bool:
    return bool(photo_half) and zone in END_ZONES


# ------------------------------------------------------------ Round 2
#
# Detection Round 2 (owner: "try, see whether there is any enhancement"),
# two switches, both off by default, zone mode only:
#   R2-box, settings.intake_box_every: the zone calls are told the drafted
#     list's parts their checklist can name and answer `locate` -- a box
#     round each one the photos show, damaged or not. Where it is, never a
#     new find: intake/zones.apply_round2 puts the box on the list item
#     that has none (evidence by "locate:zone:<zone>"), the list unchanged.
#   R2-inv, settings.intake_zone_inventory: the zone calls answer `inventory` --
#     per photo, the area's parts it shows (the checklist, and the car
#     map's small outside parts while settings.intake_car_map_names is on),
#     damaged or not, each with a box. The damaged ones nobody listed or
#     suggested become zone suggestions (capped per case); the undamaged
#     ones are kept apart (ai_draft["zone_inventory"]) and never suppress
#     anything.
# No extra call: both ride on the zone call, after every field it has
# today -- an answer cut off there is today's answer (salvage) -- and the
# caps keep the extra output small (scripts/test_detect_r2.py prints and
# caps the schema sizes).

LOCATE = "locate"
INVENTORY = "inventory"
ROUND2_FIELDS = (LOCATE, INVENTORY)
# Listed parts one zone call is asked to place (a case lists a handful per
# area), and the photos of a listed part named beside it as a hint.
MAX_LOCATE = 8
MAX_HINTS = 2
# The inventory's caps: entries per photo and per zone call (the prompt
# says them; code keeps no more).
INVENTORY_PER_PHOTO = 6
INVENTORY_PER_ZONE = 16
# The car map's small outside parts (taxonomy.CAR_MAP_PARTS) an inventory
# may name, by zone, only while settings.intake_car_map_names is "on" -- a
# car map name is never offered otherwise (taxonomy's rule). A side zone's
# without their side, as its checklist's; a close-up zone's unsided.
SMALL_PARTS = {
    "front": ["Left Fog Lamp Cover", "Right Fog Lamp Cover"],
    "rear": ["Boot Lid Garnish", "Boot Lid Striker", "Rear Number Plate Lamp", "Boot Lid Spoiler"],
    "left": ["Front Door Handle", "Rear Door Handle", "Fuel Filler Flap"],
    "right": ["Front Door Handle", "Rear Door Handle", "Fuel Filler Flap"],
    CLOSE: ["Fog Lamp Cover (side undetermined)", "Boot Lid Garnish", "Boot Lid Striker", "Rear Number Plate Lamp",
            "Boot Lid Spoiler", "Door Handle (side undetermined)", "Fuel Filler Flap"],
}


@dataclass
class Round2Ask:
    """What one zone call asks beyond today's (intake/zones builds it per
    zone; read_zone's r2). locate: this zone's checklist names of the
    drafted list's parts to place (none: no `locate`); hints: {name: the
    labels of the listed item's own photos among the call's}; inventory:
    the names the inventory may use (none: no `inventory`)."""
    locate: list[str] = field(default_factory=list)
    hints: dict[str, list[str]] = field(default_factory=dict)
    inventory: list[str] = field(default_factory=list)

    def on(self) -> bool:
        return bool(self.locate or self.inventory)


def _r2(r2) -> "Round2Ask | None":
    return r2 if isinstance(r2, Round2Ask) and r2.on() else None


def _can_be(zone: str, name: str) -> list[str]:
    """The canonical parts a checklist name can come back as (canonical):
    a side zone's family on either side or unsided; a close-up's unsided
    name or any of its sided ones."""
    if zone in SIDE_ZONES:
        out = [f"{side} {name}" for side in ("Left", "Right")] + [unsided_part(f"Left {name}") or ""]
        return [p for p in out if p in CANONICAL_PARTS]
    if zone == CLOSE:
        return [name] + [p for p in CANONICAL_PARTS if p != name and unsided_part(p) == name]
    return [name]


def place_match(listed_part: str, located: str, zone: str) -> int:
    """How a located part (canonical) stands for a listed one: 2 the same
    part; 1 the listed part is "(side undetermined)" and this is one of its
    sided names, or -- a close-up zone, whose names carry no side -- the
    located name is the listed part's unsided one; 0 not it. Never the
    other side's twin."""
    if not listed_part or not located:
        return 0
    if located == listed_part:
        return 2
    if listed_part.endswith(UNDETERMINED_SUFFIX) and unsided_part(located) == listed_part:
        return 1
    if zone == CLOSE and located.endswith(UNDETERMINED_SUFFIX) and unsided_part(listed_part) == located:
        return 1
    return 0


def twin_name(name: str) -> str | None:
    """The other side's name of a sided name ("Left Tail Lamp" -> "Right
    Tail Lamp"), else None."""
    base, side = split_side(str(name or ""))
    if side not in ("Left", "Right"):
        return None
    return f"{'Right' if side == 'Left' else 'Left'} {base}"


def locate_names(zone: str, parts) -> list[str]:
    """The checklist names a zone call is asked to place for the drafted
    list's parts (canonical): each name that can come back as one of them
    (place_match), in checklist order, at most MAX_LOCATE -- a Left / Right
    pair both or neither (the review's L5: a pair cut in two would make one
    side's box look like the only one). "Other / Not Listed" is never
    asked: nothing names it."""
    wanted = [str(p) for p in parts or [] if p and p != OTHER_PART]
    found = [name for name in checklist(zone)[:-1]
             if any(place_match(part, cand, zone) for cand in _can_be(zone, name) for part in wanted)]
    out: list[str] = []
    for name in found:
        if name in out:
            continue
        twin = twin_name(name)
        pair = [name] + ([twin] if twin in found and twin not in out else [])
        if len(out) + len(pair) <= MAX_LOCATE:
            out += pair
    return out


def locate_names_of(zone: str, part: str) -> list[str]:
    """locate_names for one listed part: the names that can place it."""
    return [n for n in checklist(zone)[:-1] if any(place_match(part, c, zone) for c in _can_be(zone, n))]


def inventory_names(zone: str, car_names: bool = False) -> list[str]:
    """The names a zone's inventory may use: its checklist (not "Other /
    Not Listed"), then -- car_names, settings.intake_car_map_names "on" --
    the car map's small outside parts of the zone (SMALL_PARTS)."""
    names = checklist(zone)[:-1]
    if car_names:
        names += [n for n in SMALL_PARTS.get(zone, []) if n not in names]
    return names


def inventory_part(zone: str, name, side_seen: str, allowed) -> str | None:
    """An inventory name as a known part (taxonomy.known_part): as
    canonical() names a find -- a side zone's family takes the side seen,
    or its "(side undetermined)" name -- and a car map name the same way.
    None for a name that wasn't offered (allowed)."""
    name = str(name or "").strip()
    if name not in allowed:
        return None
    if zone not in SIDE_ZONES:
        return name if known_part(name) else None
    if side_seen in SIDE_ZONES:
        sided = f"{side_seen.capitalize()} {name}"
        if known_part(sided):
            return sided
    else:
        unsided = unsided_part(f"Left {name}")
        if unsided and known_part(unsided):
            return unsided
    # A part with no side at all ("Fuel Filler Flap"); a family ("Front
    # Door") is no part without one.
    return name if known_part(name) else None


# The review's M2: the zone read is a second opinion, so the parts to
# place are not called the damage list, and the sentence says it is no
# damage judgement. The photos in brackets only help find the part.
LOCATE_SENTENCE = (
    "Also locate these parts: {names}. In 'locate' give each of them you can see in these photos, damaged or not: "
    "photo_reference, the photo that shows the part best (the one in brackets when it shows it); part; and box_2d: "
    "[y_min, x_min, y_max, x_max] on 0-1000 round the whole part. Leave out a part you can't see. This is not a "
    "damage judgement: decide damage from the photos alone, and list damage in 'damage' as you would without this "
    "list.\n"
)
INVENTORY_SENTENCE = (
    "Last, in 'inventory' list the parts{other} you can see in each photo, damaged or not, by the inventory's part "
    "names -- small parts next to a damaged panel first (covers, trims, garnish, lamps, clips) -- at most "
    f"{INVENTORY_PER_PHOTO} a photo and {INVENTORY_PER_ZONE} in all. For each: photo_reference; part; damaged, true "
    "or false; ai_confidence; and box_2d round the part.\n"
)


def _hinted(name: str, hints: dict) -> str:
    labels = [str(x) for x in (hints or {}).get(name) or []][:MAX_HINTS]
    return f"{name} ({', '.join(labels)})" if labels else name


def round2_text(r2) -> str:
    """Round 2's sentences (system_prompt, before the closing line): ""
    without r2."""
    ask = _r2(r2)
    if ask is None:
        return ""
    text = ""
    if ask.locate:
        text += LOCATE_SENTENCE.format(names=", ".join(_hinted(n, ask.hints) for n in ask.locate))
    if ask.inventory:
        text += INVENTORY_SENTENCE.format(other=" not in 'locate'" if ask.locate else "")
    return text


def round2_schema(r2, labels: list[str]) -> dict:
    """Round 2's fields, in order ({} without r2): `locate` (photo,
    part, box) and `inventory` (photo, part, damaged, confidence, box).
    Small on purpose: no descriptions, no min/max items."""
    ask = _r2(r2)
    if ask is None:
        return {}
    box = {"type": "array", "items": {"type": "integer"}}
    out: dict = {}
    if ask.locate:
        out[LOCATE] = {"type": "array", "items": {"type": "object", "properties": {
            "photo_reference": {"type": "string", "enum": list(labels)},
            "part": {"type": "string", "enum": list(ask.locate)},
            "box_2d": box,
        }, "required": ["photo_reference", "part", "box_2d"]}}
    if ask.inventory:
        out[INVENTORY] = {"type": "array", "items": {"type": "object", "properties": {
            "photo_reference": {"type": "string", "enum": list(labels)},
            "part": {"type": "string", "enum": list(ask.inventory)},
            "damaged": {"type": "boolean"},
            "ai_confidence": {"type": "string", "enum": CONFIDENCES},
            "box_2d": box,
        }, "required": ["photo_reference", "part", "damaged", "box_2d"]}}
    return out


# The largest zone schema Round 2 may send, in characters (json.dumps):
# over it, the inventory is left out first, then locate (fit_round2; the
# review's L2). Today's zone schemas are 1.9k-4.0k (6.3k with the assembly
# walk and every light-model option on); the main read's, which the same
# light models answer, ~23k.
R2_SCHEMA_CAP = 6500


def fit_round2(zone: str, labels: list[str], r2, **options) -> tuple["Round2Ask | None", list[str]]:
    """r2 as it may be sent with these options (response_schema's grid,
    evidence_mode, packs, photo_half, walk): the whole ask while the schema
    is at most R2_SCHEMA_CAP characters; else without the inventory, then
    without locate. (the ask or None, the fields left out)."""
    ask = _r2(r2)
    dropped: list[str] = []
    while ask is not None and len(json.dumps(response_schema(zone, labels, r2=ask, **options))) > R2_SCHEMA_CAP:
        if ask.inventory:
            ask = Round2Ask(locate=list(ask.locate), hints=dict(ask.hints), inventory=[])
            dropped.append(INVENTORY)
        else:
            ask = None
            dropped.append(LOCATE)
        ask = _r2(ask)
    return ask, dropped


def salvage(raw: str, required=("zone_side_seen", "damage", "checked_intact")) -> dict:
    """A zone answer that isn't JSON because it was cut off (or spoilt)
    inside Round 2's fields, which come last: the object up to the last of
    its top-level fields that still reads, closed, holding every field of
    today's answer -- today's answer (parse_answer then reads none of Round
    2's). ValueError when no such object is left: a cut in today's own
    fields fails as it always did, and the next model is asked. (So an
    answer whose own fields are whole but which has junk after them is read
    too, without its Round 2 fields -- off would refuse it; the review's
    L10. Only with r2: off never comes here.)"""
    text = str(raw or "")
    depth, in_string, escaped = 0, False, False
    commas: list[int] = []
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == "," and depth == 1:
            commas.append(i)
    for at in reversed(commas):
        try:
            data = json.loads(text[:at] + "}")
        except ValueError:
            continue
        if isinstance(data, dict) and all(name in data for name in required):
            return data
    raise ValueError("the zone answer is not JSON")


def _truthy(value) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() in ("true", "yes"))


def _round2_answer(zone: str, data: dict, sent: set[str], seen: str, ask: Round2Ask, side_of, answer) -> None:
    """parse_answer's Round 2 (in place on answer): the located parts and
    the inventory, canonical, photos as labels, boxes cleaned (a bad box:
    none). A part not offered, a photo not sent, a located part with no
    box, more than the caps: left out. Never raises for a bad field: the
    rest of the answer stands."""
    if ask.locate and isinstance(data.get(LOCATE), list):
        done: set[str] = set()
        for raw_item in data[LOCATE]:
            if not isinstance(raw_item, dict):
                continue
            name = str(raw_item.get("part") or "").strip()
            label = _label(raw_item.get("photo_reference"), sent)
            box = evidence.clean_box(raw_item.get("box_2d"))
            if name not in ask.locate or name in done or not label or not box:
                continue
            own = side_of(raw_item) if side_of is not None else None
            part = canonical(zone, name, own or seen)
            if not part or part == OTHER_PART:
                continue
            done.add(name)
            answer.located.append({"part": part, "photo": label, "box": box})
            if len(answer.located) >= MAX_LOCATE:
                break
    if ask.inventory and isinstance(data.get(INVENTORY), list):
        per_photo: Counter = Counter()
        kept: set[tuple[str, str]] = set()
        for raw_item in data[INVENTORY]:
            if not isinstance(raw_item, dict):
                continue
            label = _label(raw_item.get("photo_reference"), sent)
            if not label or per_photo[label] >= INVENTORY_PER_PHOTO:
                continue
            own = side_of(raw_item) if side_of is not None else None
            part = inventory_part(zone, raw_item.get("part"), own or seen, ask.inventory)
            if not part or (label, part) in kept:
                continue
            kept.add((label, part))
            per_photo[label] += 1
            confidence = raw_item.get("ai_confidence")
            answer.inventory.append({
                "part": part, "photo": label, "damaged": _truthy(raw_item.get("damaged")),
                "confidence": confidence if confidence in CONFIDENCES else None,
                "box": evidence.clean_box(raw_item.get("box_2d")),
            })
            if len(answer.inventory) >= INVENTORY_PER_ZONE:
                break


def _walk_spec(zone: str, walk) -> "assembly_walk.ZoneSpec | None":
    """The assembly walk this zone call asks for (walk: the car map's
    assembly_walk.MapIndex), or None: no walk, or no anchor here -- or a
    map it can't read, logged: the walk never costs a zone read."""
    if walk is None:
        return None
    try:
        return assembly_walk.zone_spec(walk, _kind(zone), checklist(zone))
    except Exception:
        logger.exception("Assembly walk: couldn't build zone %s's walk; asked without it", zone)
        return None


def system_prompt(zone: str, grid: bool = False, evidence_mode: str = EVIDENCE_OFF, packs=(),
                  photo_half: bool = False, walk=None, r2=None) -> str:
    """Short on purpose: the light models this is for follow a short prompt
    better than the overall reading's long one. evidence_mode
    (settings.intake_zone_evidence): "reason" / "reason_box" add the one
    sentence asking for the tail fields; "off" adds nothing. packs
    (settings.intake_zone_packs) and photo_half (settings.
    intake_zone_photo_half): the light-model packs and Z5's sentence, none
    by default. walk (settings.intake_assembly_walk): the walk's paragraph
    and panel lines, before the closing line. r2 (Round 2, a Round2Ask):
    its sentences after those, before the closing line; None adds
    nothing."""
    packs = set(packs or ())
    intact = prompt_packs.pack("intact") + "\n" if "intact" in packs else _INTACT_LINE
    sides = _SIDES_LINE
    if "orient" in packs:
        sides = prompt_packs.pack("orient") + "\n" + (_ORIENT_NO_SIDE if _kind(zone) != "end" else "")
    added = (prompt_packs.pack("cues") + "\n" if "cues" in packs else "") + (
        prompt_packs.small_rows(zone) + "\n" if "small" in packs else "")
    return (
        "You check one area of a damaged car for a Singapore motor insurance loss adjuster, from the photos below "
        "only. Each photo is labelled, e.g. P07.\n"
        + _AREA_TEXT[_kind(zone)].format(where=zone) + "\n"
        + _ZONE_NOTES.get(zone, "")
        + "Go through the allowed part names one by one and look for each in every photo. In 'damage' list each part "
        "you can see damaged, small damage included: a cracked lamp or reflector, a bent lock or hinge, torn or "
        "pushed-in trim or seal, a scraped wheel rim. For each: photo_reference, the label of the photo that shows "
        "the damage best; part, from the allowed names ('Other / Not Listed' only when none fits, with the part's "
        "name in other_part_name); condition_tokens, 1 to 3 of the allowed words; severity and remarks (Replace or "
        "Repair) when you can tell; ai_confidence, Low when the damage is slight or you are unsure.\n"
        + (GRID_SENTENCE if grid else "")
        + EVIDENCE_SENTENCE.get(_evidence_mode(evidence_mode), "")
        + (PHOTO_HALF_SENTENCE if _photo_half_on(zone, photo_half) else "")
        + intact
        + sides
        + added
        + assembly_walk.prompt_text(_walk_spec(zone, walk))
        + round2_text(r2)
        + _CLOSING
    )


def intact_description() -> str:
    """Z2: intact-v1's rule (the pack without its heading), as the schema's
    description of checked_intact."""
    return prompt_packs.pack("intact").split("\n", 1)[1].strip()


def side_from_half(end: str, half) -> str | None:
    """Z5: the car's side of a find at the photo's `half` in a photo of
    the car's `end` facing the camera (orient-v1's table): front -- the
    photo's left is the car's Right; rear -- the photo's left is its Left.
    None in the centre, or for anything else."""
    if half not in ("left", "right") or end not in END_ZONES:
        return None
    if end == "front":
        return "Right" if half == "left" else "Left"
    return "Left" if half == "left" else "Right"


def response_schema(zone: str, labels: list[str], grid: bool = False, evidence_mode: str = EVIDENCE_OFF,
                    packs=(), photo_half: bool = False, walk=None, r2=None) -> dict:
    """The small schema: zone_side_seen first (fields are filled in schema
    order, so the model settles what it is looking at before it lists
    parts), photo_reference before part (where, then what), grid_cells
    between them when grid marks are on. Field names are DamageSummaryItem's
    -- but for the evidence tail fields (evidence_mode): reason, and in
    "reason_box" box_2d, last (once the part is settled and its confidence
    said), not required. They go into an Evidence entry (parse_answer).
    No minItems/maxItems: each adds states Gemini counts against a schema's
    limit (extraction._response_schema).

    packs with "intact" (Z2): checked_intact carries intact-v1's rule as
    its description. photo_half (Z5, end zones): photo_half after
    photo_reference, required. walk (the assembly walk): `assembly_walk`
    after `damage`, required. r2 (Round 2): `locate` and / or `inventory`
    last, required (round2_schema). None of them by default."""
    names = checklist(zone)
    item: dict = {"photo_reference": {"type": "string", "enum": list(labels)}}
    half = _photo_half_on(zone, photo_half)
    if half:
        item["photo_half"] = {"type": "string", "enum": list(PHOTO_HALVES)}
    if grid:
        item["grid_cells"] = {"type": "array", "items": {"type": "string", "enum": list(CELLS)}}
    item.update({
        "part": {"type": "string", "enum": names},
        "other_part_name": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "condition_tokens": {"type": "array", "items": {"type": "string", "enum": list(FIRM_CONDITION_TOKENS)}},
        "severity": {"anyOf": [{"type": "string", "enum": list(CANONICAL_SEVERITIES)}, {"type": "null"}]},
        "remarks": {"anyOf": [{"type": "string", "enum": list(FIRM_REMARKS)}, {"type": "null"}]},
        "ai_confidence": {"type": "string", "enum": CONFIDENCES},
    })
    mode = _evidence_mode(evidence_mode)
    if mode != EVIDENCE_OFF:
        item["reason"] = {"type": "string"}
    if mode == "reason_box":
        item["box_2d"] = {"type": "array", "items": {"type": "integer"}}
    required = (["photo_reference"] + (["photo_half"] if half else []) + (["grid_cells"] if grid else [])
                + ["part", "ai_confidence"])
    intact: dict = {"type": "array", "items": {"type": "string", "enum": names[:-1]}}
    if "intact" in set(packs or ()):
        intact = {"type": "array", "description": intact_description(), "items": intact["items"]}
    schema = {
        "type": "object",
        "properties": {
            "zone_side_seen": {"type": "string", "enum": _SEEN[_kind(zone)]},
            "damage": {"type": "array", "items": {"type": "object", "properties": item, "required": required}},
            "checked_intact": intact,
        },
        "required": ["zone_side_seen", "damage", "checked_intact"],
    }
    spec = _walk_spec(zone, walk)
    if spec is not None:
        props = schema["properties"]
        schema["properties"] = {"zone_side_seen": props["zone_side_seen"], "damage": props["damage"],
                                "assembly_walk": assembly_walk.schema_field(spec, labels),
                                "checked_intact": props["checked_intact"]}
        schema["required"] = ["zone_side_seen", "damage", "assembly_walk", "checked_intact"]
    extra = round2_schema(r2, labels)
    if extra:
        schema["properties"].update(extra)
        schema["required"] = schema["required"] + list(extra)
    return schema


def build_input(photos: list[tuple[str, str, str | None]], grid: bool = False) -> list[dict]:
    """photos: (label, AI input path, the sorter's kind) in camera order.
    "Photo P07:" stays exactly that, as everything that reads labels back
    expects; a grid copy gets a text part of its own after it and goes in
    the plain photo's place (the same image tokens). A photo the grid can't
    be drawn on goes as it is."""
    parts: list[dict] = [{"type": "text", "text": f"{len(photos)} photos follow."}]
    for label, path, kind in photos:
        parts.append({"type": "text", "text": f"Photo {label}:"})
        if grid:
            try:
                path = grid_path(path)
                parts.append({"type": "text", "text": f"Photo {label} with grid marks:"})
            except Exception:
                logger.warning("Grid marks: couldn't draw on photo %s; sent without", label, exc_info=True)
        # The resolution the reading gives a photo of this kind
        # (settings.gemini_image_resolution), as the overall reading sends it.
        parts.append(image_part(path, kind=kind))
    return parts


# ------------------------------------------------------------ the answer


@dataclass
class ZoneAnswer:
    zone: str
    side_seen: str
    # DamageSummaryItem dumps: canonical parts, photo_reference a label of
    # this call's photos (or None), grid_cells set when grid marks were on.
    # Each with its evidence entry on photo_reference (by "zone:<zone>", the
    # photo given as its label), and with intake_zone_evidence its reason
    # and box (evidence.clean_reason, clean_box).
    items: list[dict] = field(default_factory=list)
    # Canonical parts the model saw undamaged: for the run log only.
    intact: list[str] = field(default_factory=list)
    # Why the finds were dropped, when they were (photos of the other end).
    note: str | None = None
    # The assembly walk (walk on): {canonical panel: {"hit_at", "signs":
    # {id: {"answer", "photo" (a label)}}}}, assembly_walk.parse_zone.
    walk: dict = field(default_factory=dict)
    # E7 (settings.intake_side_geometry, a side zone): the side the model
    # said, when the geometry changed side_seen (None otherwise).
    side_model: str | None = None
    # E7 (the review's M4): finds named by their own photo's geometry side,
    # in a side zone whose photos' geometry is split.
    per_find: int = 0
    # E11 (settings.intake_zone_wrong_end "shadow" / "keep", an end zone whose
    # photos it says show the other end): its finds, parsed as items are, and
    # kept apart -- `items` stays empty, as when they are dropped.
    wrong_end_items: list[dict] = field(default_factory=list)
    # Round 2 (read_zone's r2; empty without it): the listed parts this call
    # placed, {"part": canonical, "photo": label, "box"}; its inventory,
    # {"part": known part, "photo", "damaged", "confidence", "box" or None}
    # -- none from an end zone whose photos show the other end. r2_chars:
    # the characters of the answer's Round 2 fields, answer_chars the whole
    # answer's (the run log's estimate of the extra output tokens);
    # r2_salvaged: the answer was cut off inside them and read without them.
    located: list[dict] = field(default_factory=list)
    inventory: list[dict] = field(default_factory=list)
    r2_chars: int = 0
    answer_chars: int = 0
    r2_salvaged: bool = False


# E11: what an end zone whose photos show the other end does with its finds.
WRONG_END_MODES = ("drop", "shadow", "keep")


def wrong_end_note(zone: str, seen: str) -> str:
    """E11's words on a kept suggestion: "The rear read thought these photos
    show the front.\""""
    return f"The {zone} read thought these photos show the {seen}."


def _label(value, labels: set[str]) -> str | None:
    match = _LABEL_RE.match(str(value or "").strip().upper())
    label = f"P{int(match.group(1)):02d}" if match else None
    return label if label in labels else None


def _cells(value) -> list[str]:
    out: list[str] = []
    for cell in value if isinstance(value, list) else []:
        cell = str(cell or "").strip().upper()
        if _CELL_RE.match(cell) and cell not in out:
            out.append(cell)
    return out


_ITEM_FIELDS = ("other_part_name", "condition_tokens", "severity", "remarks", "ai_confidence")


def parse_answer(zone: str, raw: str, labels: list[str], grid: bool = False,
                 evidence_mode: str = EVIDENCE_OFF, packs=(), photo_half: bool = False, walk=None,
                 geometry: tuple | None = None, wrong_end: str = "drop", r2=None) -> ZoneAnswer:
    """The zone call's JSON as canonical damage items. Raises ValueError on
    an answer that isn't the object asked for, so call_structured tries the
    next model. Items whose part the zone doesn't offer, and an "Other / Not
    Listed" with no name (nothing a person could check), are left out; a
    photo label that wasn't sent becomes None. Then the overall reading's
    own clean-up (extraction._sanitize_damage_summary, _firm_condition).

    Each item with a photo gets its evidence entry there, by "zone:<zone>".
    The tail fields are read only as asked (evidence_mode): the reason
    cleaned (evidence.clean_reason; the item's reason too), the box cleaned
    (evidence.clean_box) and only with a photo to be on -- a bad one is no
    box, never a failed answer.

    photo_half (Z5, an end zone whose photos show that end): a find's side
    worked out from its photo_half (side_from_half); when the model named
    the other side, the part's "(side undetermined)" name instead. packs
    with "intact" (Z2): a part listed damaged is never kept as intact too.
    walk: the answer's assembly_walk, for this zone's damaged panels only
    (assembly_walk.parse_zone) -- never a reason to refuse the answer.

    geometry (E7, a side zone only; intake/geometry's module docstring):
    (the side the zone's photos' geometry names when they are unanimous,
    else None; "vote" or "tiebreak"; {label: each photo's geometry side}).
    Unanimous: the side seen is reconciled with it before any find is named
    (geometry.reconcile), the model's own kept in side_model. Split (the
    review's M4): the side seen stays the model's, and each find whose own
    photo has a geometry side is named by reconcile(the model's, that side)
    (per_find counts them); the rest by the model's.
    wrong_end (E11): "drop" (the default) leaves an end zone that sees the
    other end with no finds, as before; "shadow" and "keep" also parse its
    finds into wrong_end_items (items stays empty either way). Their parse
    failing is never the answer failing (the review's L4): no finds kept,
    and the answer is drop's -- no other model is asked.

    r2 (Round 2, a Round2Ask): its fields read into located / inventory
    (_round2_answer), never a reason to refuse the answer -- a bad entry is
    left out, an answer cut off inside them is read without them (salvage:
    today's answer), and an end zone that sees the other end keeps none.
    None: the answer read exactly as before."""
    ask = _r2(r2)
    salvaged = False
    try:
        data = json.loads(raw)
    except ValueError:
        if ask is None:
            raise
        data = salvage(raw)
        salvaged = True
    if not isinstance(data, dict):
        raise ValueError("the zone answer is not an object")
    seen = str(data.get("zone_side_seen") or "").strip().lower()
    seen = seen if seen in _SEEN[_kind(zone)] else UNCLEAR
    model_seen = seen
    sent = set(labels)
    side_of = None
    if geometry is not None and zone in SIDE_ZONES:
        from app.intake.geometry import reconcile

        zone_geo, how, by_label = geometry
        seen = reconcile(seen, zone_geo, how)
        if zone_geo is None and by_label:
            # Split: each find by its own photo's geometry, when it has one.
            def side_of(raw_item: dict) -> str | None:
                own = by_label.get(_label(raw_item.get("photo_reference"), sent))
                return reconcile(model_seen, own, how) if own else None
    answer = ZoneAnswer(zone=zone, side_seen=seen)
    if seen != model_seen:
        answer.side_model = model_seen
    if ask is not None:
        answer.r2_salvaged = salvaged
        answer.answer_chars = len(raw or "")
        answer.r2_chars = 0 if salvaged else sum(
            len(json.dumps(data[name], separators=(",", ":"))) for name in ROUND2_FIELDS if name in data)
    mode = _evidence_mode(evidence_mode)
    wrong = zone in END_ZONES and seen in END_ZONES and seen != zone
    if wrong:
        answer.note = (f"the photos show the {seen}, not the {zone}: its finds were "
                       + ("kept as suggestions" if wrong_end == "keep" else "dropped"))
        if wrong_end in ("shadow", "keep"):
            # E11: apart from the zone's answer, which stays as dropped (no
            # finds, no intact, no walk) -- even when they can't be read.
            try:
                answer.wrong_end_items = _items(zone, data, sent, seen, grid, mode, False, None, answer)
            except Exception:
                logger.warning("Zone %s: the finds of the photos it says show the %s couldn't be read; none kept",
                               zone, seen, exc_info=True)
                answer.wrong_end_items = []
        return answer
    half = _photo_half_on(zone, photo_half) and seen == zone
    answer.items = _items(zone, data, sent, seen, grid, mode, half, side_of, answer)
    damaged = {item.get("part") for item in answer.items} if "intact" in set(packs or ()) else set()
    for name in data.get("checked_intact") or []:
        part = canonical(zone, name, seen)
        if part and part != OTHER_PART and part not in answer.intact and part not in damaged:
            answer.intact.append(part)
    spec = _walk_spec(zone, walk)
    if spec is not None:
        try:
            answer.walk = assembly_walk.parse_zone(
                spec, data.get("assembly_walk"), lambda name: canonical(zone, name, seen),
                {item.get("part") for item in answer.items}, lambda value: _label(value, sent))
        except Exception:  # parse_zone is total; this is in case it ever isn't
            logger.exception("Assembly walk: zone %s's walk couldn't be read; the finds stand", zone)
            answer.walk = {}
    if ask is not None and not salvaged:
        try:
            _round2_answer(zone, data, sent, seen, ask, side_of, answer)
        except Exception:  # _round2_answer leaves bad entries out; in case it ever raises
            logger.exception("Round 2: zone %s's locate / inventory couldn't be read; the finds stand", zone)
            answer.located, answer.inventory = [], []
    return answer


def _items(zone: str, data: dict, sent: set[str], seen: str, grid: bool, mode: str, half: bool, side_of,
           answer: ZoneAnswer) -> list[dict]:
    """parse_answer's damage items (its docstring): each named with the
    side seen, or side_of(raw item)'s when that gives one (E7 per find)."""
    kept: list[tuple[DamageSummaryItem, list[str], dict]] = []
    for raw_item in data.get("damage") or []:
        if not isinstance(raw_item, dict):
            continue
        own = side_of(raw_item) if side_of is not None else None
        if own is not None:
            answer.per_find += 1
        part = canonical(zone, raw_item.get("part"), own or seen)
        if part is None:
            continue
        if half:
            named, code = split_side(part)[1], side_from_half(zone, raw_item.get("photo_half"))
            if named and code and named != code:
                part = unsided_part(part) or part
        values = {k: raw_item.get(k) for k in _ITEM_FIELDS if raw_item.get(k) is not None}
        item = DamageSummaryItem.model_validate(
            {**values, "part": part, "photo_reference": _label(raw_item.get("photo_reference"), sent)}
        )
        if part == OTHER_PART and not item.other_part_name:
            continue
        kept.append((item, _cells(raw_item.get("grid_cells")) if grid else [], raw_item))
    # The overall reading's clean-up, on a draft holding only these items.
    draft = SecurityIncidentDraft(description="", damage_summary=[item for item, _, _ in kept])
    draft = _firm_condition(_sanitize_damage_summary(draft))
    out: list[dict] = []
    for item, (_, cells, raw_item) in zip(draft.damage_summary, kept):
        dumped = item.model_dump()
        # Cells of a photo: none without one.
        dumped["grid_cells"] = cells if item.photo_reference else []
        reason = evidence.clean_reason(raw_item.get("reason")) if mode != EVIDENCE_OFF else None
        box = raw_item.get("box_2d") if mode == "reason_box" else None
        dumped["reason"] = reason
        dumped["evidence"] = [evidence.entry(item.photo_reference, f"zone:{zone}", box=box, reason=reason,
                                             confidence=item.ai_confidence)] if item.photo_reference else []
        out.append(dumped)
    return out


def read_zone(
    zone: str,
    photos: list[tuple[str, str, str | None]],
    *,
    actor: AiActor,
    models: list[str],
    grid: bool = False,
    evidence_mode: str = EVIDENCE_OFF,
    timeout: float = ZONE_TIMEOUT,
    packs=(),
    photo_half: bool = False,
    walk=None,
    geometry: tuple | None = None,
    wrong_end: str = "drop",
    r2: "Round2Ask | None" = None,
    max_tries: int | None = None,
) -> ZoneAnswer:
    """One call for one zone: photos as (label, AI input path, kind). Raises
    as call_structured does; the caller (intake/zones) never lets that fail
    the reading. judge_key=False, as the second look: an extra call failing
    says nothing new about the person's own key -- the reading that just
    answered used it -- and must not switch it off for every AI call.
    evidence_mode: settings.intake_zone_evidence. packs, photo_half:
    settings.intake_zone_packs (as config.pack_list gives them) and
    intake_zone_photo_half. walk: the car map's assembly_walk.MapIndex
    (settings.intake_assembly_walk), None for no walk; a walked call is
    tagged "+walk-<map version>" in the run log. geometry (E7) and
    wrong_end (E11): parse_answer's, how the answer is read -- the request
    is the same. r2 (Round 2, a Round2Ask): its fields asked for too, the
    call tagged "+locate-r2" / "+inventory-r2"; None (the default): the
    request as before. max_tries: call_structured's (Round 2's fallback:
    one request per model, no all-busy round); None as before."""
    labels = [label for label, _, _ in photos]
    packs = list(packs or ())
    ask = _r2(r2)
    extra = {"r2": ask} if ask is not None else {}
    system = system_prompt(zone, grid, evidence_mode, packs, photo_half, walk, **extra)
    named = packs + (["photo_half"] if _photo_half_on(zone, photo_half) else [])
    if _walk_spec(zone, walk) is not None:
        named.append(f"walk-{walk.version}")
    if ask is not None:
        named += (["locate-r2"] if ask.locate else []) + (["inventory-r2"] if ask.inventory else [])
    tag = prompt_packs.tag(f"zone:{zone}", "v1", named, system) if named else None
    with runs.prompt_tag(tag):
        return call_structured(
            purpose=PURPOSE,
            system_instruction=system,
            input_parts=build_input(photos, grid),
            schema=response_schema(zone, labels, grid, evidence_mode, packs, photo_half, walk, **extra),
            parse=lambda raw: parse_answer(zone, raw, labels, grid, evidence_mode, packs, photo_half, walk,
                                           geometry=geometry, wrong_end=wrong_end, **extra),
            models=models,
            actor=actor,
            timeout=timeout,
            judge_key=False,
            **({"max_tries": max_tries} if max_tries is not None else {}),
        )
