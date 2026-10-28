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
from app.reports.taxonomy import CANONICAL_PARTS

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
AREAS = ["front", "rear", "left", "right", "roof", "underbody", "interior"]
QUALITIES = ["good", "blurry", "unusable"]

# ~12 x 200KB previews keeps each request a few MB, well inside what one
# inline request can carry, while still needing only ~6 calls for a
# 70-photo case.
BATCH_SIZE = 12

SYSTEM_PROMPT = (
    "You sort evidence photos for a Singapore motor insurance loss adjuster. Each photo is labelled "
    "(for example P07). Return exactly one entry per photo, using its label.\n"
    "- kind: what the photo is. One of:\n"
    + "".join(f"    {key}: {desc}\n" for key, desc in PHOTO_KINDS.items())
    + "- area: which part of the INSURED vehicle the photo mainly shows -- front, rear, left, right, roof, "
    "underbody or interior. Left and right are the vehicle's own sides as seen by its driver facing forward. "
    "An open boot or tailgate, the boot floor and the boot's side trims are 'rear', not 'interior'. "
    "null when the photo is not of the insured vehicle, or the area cannot be told.\n"
    "- quality: good; blurry (soft but still readable); unusable (nothing can be made out).\n"
    "- shows_damage: true only when damage to the insured vehicle is actually visible in this photo -- including a "
    "cracked or scuffed lamp or reflector, a bent lock, hinge or panel seen with the boot open, and torn or displaced trim.\n"
    "- part: the one damaged part this photo shows best, from the allowed list ('Boot Lid' also means a tailgate; "
    "'Boot Lid ...' entries are mounted on it). null when no damaged part of the insured vehicle is shown.\n"
    "- note: at most 12 words on what the photo shows, e.g. 'Rear bumper cracked near LH reflector'. "
    "Never invent plate numbers or text you cannot actually read."
)

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "photos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "kind": {"type": "string", "enum": list(PHOTO_KINDS)},
                    "area": {"anyOf": [{"type": "string", "enum": AREAS}, {"type": "null"}]},
                    "quality": {"type": "string", "enum": QUALITIES},
                    "shows_damage": {"type": "boolean"},
                    "part": {"anyOf": [{"type": "string", "enum": CANONICAL_PARTS}, {"type": "null"}]},
                    "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["label", "kind", "area", "quality", "shows_damage", "part", "note"],
            },
        },
    },
    "required": ["photos"],
}


def _triage_chain() -> list[str]:
    return [m.strip() for m in settings.gemini_triage_model_chain.split(",") if m.strip()]


def _clean(item: dict) -> dict:
    """Constrains one entry to the known vocabulary -- same reasoning as
    extraction._sanitize_damage_summary: a stray value is safer nulled (or
    put in "other") than stored and shown."""
    kind = item.get("kind")
    area = item.get("area")
    quality = item.get("quality")
    part = item.get("part")
    note = item.get("note")
    note = note.strip()[:120] if isinstance(note, str) and note.strip() else None
    return {
        "kind": kind if kind in PHOTO_KINDS else "other",
        "area": area if area in AREAS else None,
        "quality": quality if quality in QUALITIES else "good",
        "shows_damage": item.get("shows_damage") is True,
        "part": part if part in CANONICAL_PARTS and part != "Other / Not Listed" else None,
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
