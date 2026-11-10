"""Sorting a case's photos before drafting (dashboard intake).

A real case arrives as 40-80 photos -- damage close-ups, walk-around shots,
the LTA log card, the odometer, tyres, the third party's car, the scene --
in camera order. The four sample cases in example_case/ are 41-78 photos
and 88-194MB each. Sending all of that to the drafting call doesn't fit one
request and would spend the strong models' small daily quota on photos of
tyres, so a cheap pass labels every photo first and intake/pipeline.py picks
the ones worth drafting from.
"""
import json
import logging

from app.ai.keys import AiActor
from app.ai.structured import call_structured, image_part
from app.config import settings
from app.reports.taxonomy import CANONICAL_PARTS, split_side, unsided_part

logger = logging.getLogger(__name__)

# Keys are stored on IntakePhoto.kind; the descriptions go to the model.
# The dashboard (lib/intake.ts) has the matching display labels.
PHOTO_KINDS: dict[str, str] = {
    "damage": "close-up of damage on the insured vehicle, including damage seen with the boot, bonnet or a door open",
    "overview": "the insured vehicle as a whole, or a corner/side of it (walk-around shot)",
    "log_card": "LTA vehicle log card or other registration document",
    "vin_plate": "chassis/VIN plate or stamped engine number",
    "police_report": "police report",
    "document": "any other document: workshop estimate, insurance letter, receipt",
    "odometer": "instrument cluster showing the odometer/mileage",
    "tyre": "tyre sidewall or tread",
    # "Inside the insured vehicle" alone had the model file every photo into
    # an open boot as interior -- 8 of 65 on the SLK 3063 Z case, including
    # the tailgate lock and boot floor damage the surveyor priced.
    "interior": "inside the cabin (seats, dashboard, headliner, door trims) -- NOT the boot",
    "scene": "the accident scene or road",
    "third_party": "the other party's vehicle",
    "other": "anything else",
}
# The four corners were added (contract4, 2026-11-06) so a close-up the
# reader could only call "(side undetermined)" can be placed from where the
# sorter saw it was taken (intake/pipeline.resolve_sides): "rear" alone says
# nothing about LH or RH, and the lamps, reflectors and boot-lid parts a
# rear-end case is mostly made of sit on one half of it. The old seven stay,
# so photos sorted before keep a valid area.
AREAS = [
    "front", "rear", "left", "right",
    "front-left", "front-right", "rear-left", "rear-right",
    "roof", "underbody", "interior",
]
QUALITIES = ["good", "blurry", "unusable"]
# Which way the insured car's front points in a photo; see _SIDES.
ANGLES = ["front toward camera", "front away from camera", "front to photo left", "front to photo right"]
# The areas that name a side of the car, and that side.
_AREA_SIDE = {
    "left": "Left", "front-left": "Left", "rear-left": "Left",
    "right": "Right", "front-right": "Right", "rear-right": "Right",
}

# Changes whenever the sort's prompt or vocabulary does, so a cache of sort
# results (scripts/benchmark_intake.py --sort-cache) never serves answers
# given to an older question. v1: corner areas. v2: the car's own sides
# (_SIDES, the batch_sides and angle fields; a part's side dropped when the
# photo's area disagrees).
SORT_PROMPT_VERSION = "v2-car-sides"

# ~12 x 200KB previews keeps each request a few MB, well inside what one
# inline request can carry, while still needing only ~6 calls for a
# 70-photo case.
BATCH_SIZE = 12

# Sides (contract6 A4, 2026-11-10). On a Honda Vezel hit on its RIGHT side
# the sort called the right-side photos "Left Front Door" and "Left Front
# Wheel Arch Liner", and an older frozen sort of the same photos did too:
# the photographer stood in front of the car facing it, where the car's
# right is on the left of the frame. Close-ups shot from behind the car,
# looking forward, came out right. "As seen by its driver" alone didn't stop
# the model reading sides off the frame, so the prompt says how to turn the
# frame into the car's sides, and each entry first says which way the car's
# front points (angle) before it names an area or a part: the light models
# the sort runs on think little, and fields are filled in the schema's order
# (reports/schema.py, description). batch_sides, written before any entry,
# is the case-level cue a batch can have without a call more (the sort runs
# on every case of the day, on the light model's allowance): the side its
# wide shots show, for close-ups with nothing to tell a side by.
_SIDES = (
    "Sides: left and right are always the insured car's OWN sides, as its driver sitting in it facing forward sees "
    "them -- never the left or right of the photo. Singapore and Malaysian cars are right-hand drive: the "
    "steering wheel is on the car's right. Before naming a side, work out which way the car's front (headlamps, "
    "grille, bonnet, wing mirrors) points in the photo:\n"
    "    toward the camera: the car's RIGHT side is on the photo's LEFT, and its left on the photo's right;\n"
    "    away from the camera (you face the tail lamps): the car's left is on the photo's left;\n"
    "    to the photo's left: you are looking at the car's LEFT side; to the photo's right: at its RIGHT side.\n"
    "A close-up with none of these clues usually shows the side of the wide shots taken just before it. "
    "When you still can't tell, use the part's '(side undetermined)' name: a wrong side is worse than none.\n"
)

SYSTEM_PROMPT = (
    "You sort evidence photos for a Singapore motor insurance loss adjuster. The photos come in the order they "
    "were taken, each labelled (for example P07). Return exactly one entry per photo, using its label.\n"
    + _SIDES
    + "- batch_sides: before any photo entry, at most 25 words on where the wide shots in this batch show damage "
    "on the insured car, sides worked out as above, e.g. 'From the front: dent on the photo's left = the car's right "
    "front' or 'From behind: scrape on the photo's left = the car's left rear'. null when no wide shot shows damage.\n"
    "Then for each photo:\n"
    "- kind: what the photo is. One of:\n"
    + "".join(f"    {key}: {desc}\n" for key, desc in PHOTO_KINDS.items())
    + "- angle: which way the insured car's front points in this photo, as above. null when it can't be told or the "
    "insured car isn't shown.\n"
    "- area: which part of the INSURED vehicle the photo mainly shows -- front, rear, left, right, a corner "
    "(front-left, front-right, rear-left, rear-right), roof, underbody or interior, with sides as above. "
    "Use a corner when the photo shows that corner, or a close-up on "
    "the front or rear that you can tell is on its left or right half (the lamp, reflector or bumper end beside it); "
    "plain front or rear when you cannot tell which half. "
    "An open boot or tailgate, the boot floor and the boot's side trims are rear (or a rear corner), not 'interior'. "
    "null when the photo is not of the insured vehicle, or the area cannot be told.\n"
    "- quality: good; blurry (soft but still readable); unusable (nothing can be made out).\n"
    "- shows_damage: true only when damage to the insured vehicle is actually visible in this photo -- including a "
    "cracked or scuffed lamp or reflector, a bent lock, hinge or panel seen with the boot open, and torn or displaced trim.\n"
    "- part: the one damaged part this photo shows best, from the allowed list, with sides as above ('Boot Lid' "
    "also means a tailgate; "
    "'Boot Lid ...' entries are mounted on it). null when no damaged part of the insured vehicle is shown.\n"
    "- note: at most 12 words on what the photo shows, e.g. 'Rear bumper cracked near LH reflector'. "
    "Never invent plate numbers or text you cannot actually read."
)

# batch_sides and angle come before what they are there to inform (see
# _SIDES), and are named so they still do if a model fills keys in
# alphabetical order.
_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "batch_sides": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "photos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "kind": {"type": "string", "enum": list(PHOTO_KINDS)},
                    "angle": {"anyOf": [{"type": "string", "enum": ANGLES}, {"type": "null"}]},
                    "area": {"anyOf": [{"type": "string", "enum": AREAS}, {"type": "null"}]},
                    "quality": {"type": "string", "enum": QUALITIES},
                    "shows_damage": {"type": "boolean"},
                    "part": {"anyOf": [{"type": "string", "enum": CANONICAL_PARTS}, {"type": "null"}]},
                    "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["label", "kind", "angle", "area", "quality", "shows_damage", "part", "note"],
            },
        },
    },
    "required": ["batch_sides", "photos"],
}


def _triage_chain() -> list[str]:
    return [m.strip() for m in settings.gemini_triage_model_chain.split(",") if m.strip()]


def _clean(item: dict) -> dict:
    """Constrains one entry to the known vocabulary -- same reasoning as
    extraction._sanitize_damage_summary: a stray value is safer nulled (or
    put in "other") than stored and shown. angle and batch_sides only steer
    the model (_SIDES) and aren't kept.

    A part whose side disagrees with the photo's area ("Left Front Door" on
    a photo of the car's right) keeps its family without a side ("Door
    (side undetermined)"): one of the two is wrong and nothing here says
    which, and a wrong side is worse than none. Little is lost: the reader
    names sides on its own, and the photo still groups with its family's
    photos for reading (pipeline.select_for_reading). A sided part with no
    side-undetermined name is dropped. The area is kept as it is."""
    kind = item.get("kind")
    area = item.get("area")
    area = area if area in AREAS else None
    quality = item.get("quality")
    part = item.get("part")
    part = part if part in CANONICAL_PARTS and part != "Other / Not Listed" else None
    if part and area in _AREA_SIDE and split_side(part)[1] not in (None, _AREA_SIDE[area]):
        part = unsided_part(part)
    note = item.get("note")
    note = note.strip()[:120] if isinstance(note, str) and note.strip() else None
    return {
        "kind": kind if kind in PHOTO_KINDS else "other",
        "area": area,
        "quality": quality if quality in QUALITIES else "good",
        "shows_damage": item.get("shows_damage") is True,
        "part": part,
        "note": note,
    }


def triage_photos(photos: list[tuple[str, str]], *, actor: AiActor) -> dict[str, dict]:
    """Sorts one batch of (label, image path) pairs for actor (who pays,
    structured.call_structured). Returns label -> result.

    Labels the model returned that weren't asked about are dropped. A batch
    where it answered for under half the photos is treated as a failed call
    (so the chain moves on to another model); otherwise photos it skipped are
    simply missing from the result and stay unsorted.
    """
    parts: list[dict] = [{"type": "text", "text": f"{len(photos)} photos follow."}]
    for label, path in photos:
        parts.append({"type": "text", "text": f"Photo {label}:"})
        parts.append(image_part(path))

    expected = {label for label, _ in photos}

    def parse(raw_output: str) -> dict[str, dict]:
        data = json.loads(raw_output)
        result: dict[str, dict] = {}
        for item in data.get("photos") or []:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip().upper()
            if label in expected:
                result[label] = _clean(item)
        if len(result) * 2 < len(expected):
            raise ValueError(f"model sorted only {len(result)} of {len(expected)} photos")
        return result

    return call_structured(
        purpose="photo sort",
        system_instruction=SYSTEM_PROMPT,
        input_parts=parts,
        schema=_RESPONSE_SCHEMA,
        parse=parse,
        models=_triage_chain(),
        actor=actor,
        timeout=60.0,
    )
