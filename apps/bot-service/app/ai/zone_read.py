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
reading's model chain, thinking level and tools -- structured.CALL_KINDS)
and merges the answers.
"""
import json
import logging
import re
from dataclasses import dataclass, field

from app.ai.extraction import _firm_condition, _sanitize_damage_summary
from app.ai.grid import CELLS, grid_path
from app.ai.keys import AiActor
from app.ai.second_look import ZONES
from app.ai.structured import call_structured, image_part
from app.reports.schema import DamageSummaryItem, SecurityIncidentDraft
from app.reports.taxonomy import (
    CANONICAL_PARTS,
    CANONICAL_SEVERITIES,
    FIRM_CONDITION_TOKENS,
    FIRM_REMARKS,
    OTHER_PART,
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


def system_prompt(zone: str, grid: bool = False) -> str:
    """Short on purpose: the light models this is for follow a short prompt
    better than the overall reading's long one."""
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
        + "In 'checked_intact' list the allowed parts you can see clearly in these photos with no damage.\n"
        "Left and right are the car's own sides as its driver sees them, never the photo's: work out first which way "
        "the car's front points -- front toward the camera, the car's right is on the photo's left; tail lamps toward "
        "the camera, the car's left is on the photo's left.\n"
        "Only the insured car, never the other party's vehicle. Only damage you can see: a wrong part or side is "
        "worse than none."
    )


def response_schema(zone: str, labels: list[str], grid: bool = False) -> dict:
    """The small schema: zone_side_seen first (fields are filled in schema
    order, so the model settles what it is looking at before it lists
    parts), photo_reference before part (where, then what), grid_cells
    between them when grid marks are on. Field names are DamageSummaryItem's.
    No minItems/maxItems: each adds states Gemini counts against a schema's
    limit (extraction._response_schema)."""
    names = checklist(zone)
    item: dict = {"photo_reference": {"type": "string", "enum": list(labels)}}
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
    required = ["photo_reference"] + (["grid_cells"] if grid else []) + ["part", "ai_confidence"]
    return {
        "type": "object",
        "properties": {
            "zone_side_seen": {"type": "string", "enum": _SEEN[_kind(zone)]},
            "damage": {"type": "array", "items": {"type": "object", "properties": item, "required": required}},
            "checked_intact": {"type": "array", "items": {"type": "string", "enum": names[:-1]}},
        },
        "required": ["zone_side_seen", "damage", "checked_intact"],
    }


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
    items: list[dict] = field(default_factory=list)
    # Canonical parts the model saw undamaged: for the run log only.
    intact: list[str] = field(default_factory=list)
    # Why the finds were dropped, when they were (photos of the other end).
    note: str | None = None


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


def parse_answer(zone: str, raw: str, labels: list[str], grid: bool = False) -> ZoneAnswer:
    """The zone call's JSON as canonical damage items. Raises ValueError on
    an answer that isn't the object asked for, so call_structured tries the
    next model. Items whose part the zone doesn't offer, and an "Other / Not
    Listed" with no name (nothing a person could check), are left out; a
    photo label that wasn't sent becomes None. Then the overall reading's
    own clean-up (extraction._sanitize_damage_summary, _firm_condition)."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("the zone answer is not an object")
    seen = str(data.get("zone_side_seen") or "").strip().lower()
    seen = seen if seen in _SEEN[_kind(zone)] else UNCLEAR
    sent = set(labels)
    answer = ZoneAnswer(zone=zone, side_seen=seen)
    if zone in END_ZONES and seen in END_ZONES and seen != zone:
        answer.note = f"the photos show the {seen}, not the {zone}: its finds were dropped"
        return answer
    kept: list[tuple[DamageSummaryItem, list[str]]] = []
    for raw_item in data.get("damage") or []:
        if not isinstance(raw_item, dict):
            continue
        part = canonical(zone, raw_item.get("part"), seen)
        if part is None:
            continue
        values = {k: raw_item.get(k) for k in _ITEM_FIELDS if raw_item.get(k) is not None}
        item = DamageSummaryItem.model_validate(
            {**values, "part": part, "photo_reference": _label(raw_item.get("photo_reference"), sent)}
        )
        if part == OTHER_PART and not item.other_part_name:
            continue
        kept.append((item, _cells(raw_item.get("grid_cells")) if grid else []))
    # The overall reading's clean-up, on a draft holding only these items.
    draft = SecurityIncidentDraft(description="", damage_summary=[item for item, _ in kept])
    draft = _firm_condition(_sanitize_damage_summary(draft))
    for item, (_, cells) in zip(draft.damage_summary, kept):
        dumped = item.model_dump()
        # Cells of a photo: none without one.
        dumped["grid_cells"] = cells if item.photo_reference else []
        answer.items.append(dumped)
    for name in data.get("checked_intact") or []:
        part = canonical(zone, name, seen)
        if part and part != OTHER_PART and part not in answer.intact:
            answer.intact.append(part)
    return answer


def read_zone(
    zone: str,
    photos: list[tuple[str, str, str | None]],
    *,
    actor: AiActor,
    models: list[str],
    grid: bool = False,
    timeout: float = ZONE_TIMEOUT,
) -> ZoneAnswer:
    """One call for one zone: photos as (label, AI input path, kind). Raises
    as call_structured does; the caller (intake/zones) never lets that fail
    the reading. judge_key=False, as the second look: an extra call failing
    says nothing new about the person's own key -- the reading that just
    answered used it -- and must not switch it off for every AI call."""
    labels = [label for label, _, _ in photos]
    return call_structured(
        purpose=PURPOSE,
        system_instruction=system_prompt(zone, grid),
        input_parts=build_input(photos, grid),
        schema=response_schema(zone, labels, grid),
        parse=lambda raw: parse_answer(zone, raw, labels, grid),
        models=models,
        actor=actor,
        timeout=timeout,
        judge_key=False,
    )
