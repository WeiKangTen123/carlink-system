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
    # Behind and inside the front bumper / fenders. Real reports price these
    # separately; without names for them the model folded them into "Front
    # Bumper" or "Other / Not Listed".
    "Front Bumper Beam",
    "Left Fog Lamp",
    "Right Fog Lamp",
    "Front Number Plate",
    "Left Front Wheel Arch Liner",
    "Right Front Wheel Arch Liner",
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
    # A rear-end hit is rarely just "Rear Bumper" and "Boot Lid": the four
    # example surveyor reports (2018-2023) list the bumper beam, reflectors,
    # rear end panel, the boot lid's lock, hinges, weatherstrip, inner trim
    # and lamps, and the boot floor and side trims as separate priced lines.
    # "Boot Lid" also means a tailgate or hatch door.
    "Rear Bumper Beam",
    "Left Rear Bumper Reflector",
    "Right Rear Bumper Reflector",
    "Rear End Panel",
    "Boot Lid Lock",
    "Boot Lid Hinge",
    "Boot Lid Weatherstrip",
    "Boot Lid Inner Trim",
    "Left Boot Lid Lamp",
    "Right Boot Lid Lamp",
    "Boot Floor Board",
    "Left Boot Side Trim",
    "Right Boot Side Trim",
    "Left Rear Wheel Arch Liner",
    "Right Rear Wheel Arch Liner",
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
    "Engine Undercover",
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
    # Side-agnostic variants.
    #
    # A close-up photo frequently shows WHICH panel is damaged without
    # showing which side of the car it belongs to. With only Left/Right
    # names available, the model's single honest option was
    # "Other / Not Listed" -- observed on a real filing, where it wrote
    # "likely a rear quarter panel, though the side is not specified" and
    # then had to discard that identification entirely, producing two
    # unnamed items and a blank 3D blueprint.
    #
    # Naming the part without asserting a side keeps the identification,
    # keeps it aggregatable in parts analytics, and leaves a surveyor able
    # to correct the side in the damage checklist. These map to no 3D mesh
    # (see vehicleZones.ts) because picking a side to highlight would be
    # inventing the one fact the model said it could not determine.
    "Quarter Panel (side undetermined)",
    "Front Fender (side undetermined)",
    "Door (side undetermined)",
    "Door Glass (side undetermined)",
    "Headlamp (side undetermined)",
    "Tail Lamp (side undetermined)",
    "Wing Mirror (side undetermined)",
    "Wheel (side undetermined)",
    "Sill / Rocker Panel (side undetermined)",
    "Fog Lamp (side undetermined)",
    "Rear Bumper Reflector (side undetermined)",
    "Boot Lid Lamp (side undetermined)",
    "Wheel Arch Liner (side undetermined)",
    "Boot Side Trim (side undetermined)",
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
    # Commercial Vans
    "hiace": "Van",
    "starex": "Van",
    "transporter": "Van",
    "caravelle": "Van",
    "kombi": "Van",
    "nv200": "Van",
    "nv350": "Van",
    "urvan": "Van",
    # MPVs / Minivans
    "alphard": "MPV",
    "vellfire": "MPV",
    "sienta": "MPV",
    "noah": "MPV",
    "voxy": "MPV",
    "serena": "MPV",
    "stepwgn": "MPV",
    "odyssey": "MPV",
    "spade": "MPV",
    "roomy": "MPV",
    "carnival": "MPV",
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
    "atto 3": "SUV",
    "atto": "SUV",
    "yaris cross": "SUV",
    "tucson": "SUV",
    "cx-3": "SUV",
    "cx-5": "SUV",
    "cx-30": "SUV",
    "sportage": "SUV",
    "tiguan": "SUV",
    "gla": "SUV",
    "glc": "SUV",
    "x1": "SUV",
    "x3": "SUV",
    # Hatchbacks
    "jazz": "Hatchback",
    "fit": "Hatchback",
    "yaris": "Hatchback",
    "swift": "Hatchback",
    "march": "Hatchback",
    "note": "Hatchback",
    "golf": "Hatchback",
    "polo": "Hatchback",
    "a-class": "Hatchback",
    "1 series": "Hatchback",
    # Sedans
    "altis": "Sedan",
    "camry": "Sedan",
    "vios": "Sedan",
    "civic": "Sedan",
    "accord": "Sedan",
    "city": "Sedan",
    "c-class": "Sedan",
    "e-class": "Sedan",
    "s-class": "Sedan",
    "3 series": "Sedan",
    "5 series": "Sedan",
    # Pickups
    "hilux": "Pickup",
    "ranger": "Pickup",
    "navara": "Pickup",
    "triton": "Pickup",
}


def body_type_for(make: str | None, model: str | None, stated: str | None = None) -> str | None:
    """Resolves a vehicle to one of BODY_TYPES, or None when unknown.

    The catalogue wins over the model's own guess, because a make/model is a
    matter of fact while the guess is inference. Unknown stays unknown: this
    used to return "Sedan", which was then saved on the case and printed in
    the PDF as the vehicle's body type. The 3D viewer picks its own model
    when none is recorded (dashboard detectBodyType).
    """
    text = f"{make or ''} {model or ''}".lower()
    for fragment, body in MODEL_BODY_TYPES.items():
        if fragment in text:
            return body
    if stated and stated in BODY_TYPES:
        return stated
    return None
