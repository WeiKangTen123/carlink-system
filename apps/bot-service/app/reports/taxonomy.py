"""Canonical vehicle vocabulary.

Vehicle parts are a closed, standardised set, but the system was treating
them as free text: across six real reports, 35 damage line items produced
29 distinct part names, 24 of which appeared exactly once. No two reports
agreed on wording ("Rear bumper fascia", "Undercarriage", "Grazed/slack/cut"),
which made parts-demand analytics unable to aggregate anything and forced
the dashboard to guess at 3D zones with regexes that needed patching three
separate times.

These lists are fed to Gemini as schema enums, so the model is constrained
to them at the point of extraction rather than cleaned up afterwards --
verified against a real collision photo: free-text "Undercarriage" and
"Grazed/slack/cut" came back instead as "Underbody / Chassis" and
"Deformation".

CANONICAL_PARTS is the contract shared with the dashboard, whose
lib/vehicleZones.ts maps these same names onto 3D model zones. Adding a
part here without adding it there is safe -- it simply renders without a
3D marker, the same as any unmappable part today -- but the two should be
kept in step. scripts/check_taxonomy_sync.py verifies it.
"""

# Ordered roughly front-to-back, then structural. Side is part of the name
# rather than a separate field because that is how assessors write it, and
# it removes the left/right inference the dashboard previously had to do.
CANONICAL_PARTS: list[str] = [
    # Front
    "Front Bumper",
    "Front Grille",
    "Bonnet",
    "Left Headlamp",
    "Right Headlamp",
    "Front Windscreen",
    "Left Front Fender",
    "Right Front Fender",
    # Doors
    "Left Front Door",
    "Right Front Door",
    "Left Rear Door",
    "Right Rear Door",
    "Left Front Door Glass",
    "Right Front Door Glass",
    "Left Rear Door Glass",
    "Right Rear Door Glass",
    "Left Wing Mirror",
    "Right Wing Mirror",
    # Rear
    "Rear Bumper",
    "Boot Lid",
    "Rear Windscreen",
    "Left Tail Lamp",
    "Right Tail Lamp",
    "Left Rear Quarter Panel",
    "Right Rear Quarter Panel",
    "Rear Number Plate",
    # Wheels
    "Left Front Wheel",
    "Right Front Wheel",
    "Left Rear Wheel",
    "Right Rear Wheel",
    # Structure / body
    "Roof",
    "Underbody / Chassis",
    "Left Sill / Rocker Panel",
    "Right Sill / Rocker Panel",
    "Rear Floor Panel",
    "Front Subframe",
    "Rear Subframe",
    # Mechanical / systems a damage assessment legitimately records
    "Radiator",
    "Air Conditioning Condenser",
    "Exhaust System",
    "Fuel Tank",
    "Suspension - Front",
    "Suspension - Rear",
    "Steering Assembly",
    "Airbag System",
    "Parking Sensor",
    "Reversing Camera",
    # Interior
    "Interior Trim",
    "Seat",
    "Dashboard",
    # Escape hatch. Without this the model is forced to mislabel anything
    # genuinely outside the list; with it, unusual damage stays honest and
    # the free-text detail survives in damage_type and the description.
    "Other / Not Listed",
]

# A small controlled set replaces the 19 distinct free-text values six
# reports managed to produce (e.g. "Grazed/slack/cut", "Distorted/jammed").
# Compound real-world damage is captured by picking the dominant type here
# and leaving the nuance to the description.
CANONICAL_DAMAGE_TYPES: list[str] = [
    "Scratch",
    "Scuff / Graze",
    "Dent",
    "Deformation",
    "Crack",
    "Shatter / Broken Glass",
    "Broken / Detached",
    "Puncture / Tear",
    "Misalignment",
    "Structural Damage",
    "Paint Damage",
    "Missing",
    "Water Damage",
    "Fire / Heat Damage",
    "Electrical Fault",
]

CANONICAL_SEVERITIES: list[str] = ["Minor", "Moderate", "Severe"]


# ---------------------------------------------------------------------------
# Vehicle body types -- picks which 3D model the dashboard renders.
#
# Replaces a hardcoded regex in VehicleBlueprint3D that pattern-matched the
# vehicle name for van keywords. A real Toyota Hiace on file was relying on
# that regex; anything it didn't anticipate silently rendered as a sedan.
# ---------------------------------------------------------------------------

BODY_TYPES: list[str] = ["Sedan", "Hatchback", "SUV", "MPV", "Van", "Pickup", "Coupe", "Wagon", "Other"]

# make/model fragment -> body type. Deliberately small and specific: it
# encodes only models actually seen or likely in this market, and anything
# unmatched falls back to the model's own body_type answer, then to Sedan.
MODEL_BODY_TYPES: dict[str, str] = {
    # Vans / MPVs
    "hiace": "Van",
    "alphard": "MPV",
    "vellfire": "MPV",
    "starex": "Van",
    "transporter": "Van",
    "caravelle": "Van",
    "kombi": "Van",
    "noah": "MPV",
    "voxy": "MPV",
    "serena": "MPV",
    "stepwgn": "MPV",
    "odyssey": "MPV",
    # SUVs / crossovers
    "vezel": "SUV",
    "hr-v": "SUV",
    "hrv": "SUV",
    "cr-v": "SUV",
    "crv": "SUV",
    "rav4": "SUV",
    "harrier": "SUV",
    "forester": "SUV",
    "x-trail": "SUV",
    "qashqai": "SUV",
    # Hatchbacks
    "jazz": "Hatchback",
    "fit": "Hatchback",
    "yaris": "Hatchback",
    "swift": "Hatchback",
    "march": "Hatchback",
    # Pickups
    "hilux": "Pickup",
    "ranger": "Pickup",
    "navara": "Pickup",
    "triton": "Pickup",
}


def body_type_for(make: str | None, model: str | None, stated: str | None = None) -> str:
    """Resolves a vehicle to one of BODY_TYPES.

    The catalogue wins over the model's own guess, because a make/model is a
    matter of fact while the guess is inference. Falls back to Sedan, which
    is the only real 3D asset available -- an honest default rather than a
    claim about the vehicle.
    """
    text = f"{make or ''} {model or ''}".lower()
    for fragment, body in MODEL_BODY_TYPES.items():
        if fragment in text:
            return body
    if stated and stated in BODY_TYPES:
        return stated
    return "Sedan"
