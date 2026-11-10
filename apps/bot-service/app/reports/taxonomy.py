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
    # The garnish on the outside of the arch, apart from the liner (splash
    # shield) inside it: the firm prices them as separate lines ("Front
    # fender wheel arch garnish RH" beside "Front fender splash shield"),
    # and with one name for both the reader could list only one of them.
    "Left Front Wheel Arch Trim",
    "Right Front Wheel Arch Trim",
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
    # Inside the door, priced apart from it (the regulator mechanism and its
    # motor): a door torn open shows it, and the reader had no name for it.
    "Left Front Window Regulator",
    "Right Front Window Regulator",
    "Left Rear Window Regulator",
    "Right Rear Window Regulator",
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
    "Left Rear Wheel Arch Trim",
    "Right Rear Wheel Arch Trim",
    # Wheels. "Wheel" is the rim; the tyre is its own line in the firm's
    # sheets ("Sport rim" and "Front tyre"), often with its own finding (a
    # grazed rim on a serviceable tyre).
    "Left Front Wheel",
    "Right Front Wheel",
    "Left Rear Wheel",
    "Right Rear Wheel",
    "Left Front Tyre",
    "Right Front Tyre",
    "Left Rear Tyre",
    "Right Rear Tyre",
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
    "Wheel Arch Trim (side undetermined)",
    "Window Regulator (side undetermined)",
    "Tyre (side undetermined)",
    # Escape hatch. Without this the model is forced to mislabel anything
    # genuinely outside the list; with it, unusual damage stays honest, and
    # the item names the part in other_part_name (schema.DamageSummaryItem)
    # for Annex A to print and price (prefill.sync_annex_a).
    "Other / Not Listed",
]

OTHER_PART = "Other / Not Listed"
# A part's name as anyone writes it fits a report line ("Front door glass
# regulator mechanism motor" is 42); longer is a sentence, not a name.
MAX_OTHER_NAME = 80


def clean_part_name(text) -> str | None:
    """A name given for an "Other / Not Listed" part (DamageSummaryItem.
    other_part_name), as it will print: spaces collapsed, first letter
    capitalised, at most MAX_OTHER_NAME characters; None for blank."""
    name = " ".join(str(text or "").split())[:MAX_OTHER_NAME].strip()
    return (name[0].upper() + name[1:]) if name else None


def other_part_name(item: dict) -> str | None:
    """The name of an "Other / Not Listed" damage item, cleaned; None for a
    listed part (the name is the part's) or an Other nobody named."""
    if not isinstance(item, dict) or str(item.get("part") or "").strip() != OTHER_PART:
        return None
    return clean_part_name(item.get("other_part_name"))


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


# ---------------------------------------------------------------------------
# The firm's Annex A wording (agentD_detection_audit.md, contract4 A1).
#
# CANONICAL_DAMAGE_TYPES is how the AI classifies damage; it is not how the
# firm writes it. Across the four imported CL reports the Condition column
# holds 45 distinct strings built from 18 words ("Buckled/cut",
# "Grazed/cracked", "Distorted/jammed"), and seven of the 15 AI types never
# appear there. The AI now also picks from these words (a small enum; the
# 45 full strings would be the schema's longest list), and code joins them
# the firm's way, so the Annex A Condition column reads like the firm's.
# ---------------------------------------------------------------------------

# List order is print order: surface, shape, out of place, split or broken,
# no longer works -- the order the firm mostly writes them ("Grazed/
# deformed/cut", "Distorted/cut/faulty").
FIRM_CONDITION_TOKENS: list[str] = [
    "Grazed",
    "Dented", "Bent", "Buckled", "Deformed", "Distorted", "Crumpled", "Warped",
    "Shifted", "Slack",
    "Cracked", "Chipped", "Torn", "Cut",
    "Jammed", "Faulty", "Leaked", "Rattle",
]

# Every damage condition the four reports wrote on three or more lines, most
# used first (Bent 11 ... Crumpled 3): the editor's suggestions.
FIRM_CONDITION_COMMON: list[str] = [
    "Bent", "Deformed/cut", "Grazed/cut", "Bent/cut", "Distorted/jammed", "Dented/bent",
    "Grazed/cracked", "Buckled", "Distorted/cut", "Cracked", "Crumpled",
]

# Conditions written as one whole word, never with damage words: a fitting
# renewed on refit (Necessary, 61 of 171 lines), a part kept (Serviceable),
# and a part the firm says to repair rather than renew (Repair: the line is
# disallowed at 100%, see prefill). The AI never picks these.
FIRM_WHOLE_CONDITIONS: list[str] = ["Necessary", "Serviceable", "Repair"]

# The Remarks column: "Replace" on 73 of 171 lines, blank on the rest.
FIRM_REMARKS: list[str] = ["Replace", "Repair"]

# One firm word per AI damage type, for drafts where the model gave a type
# but no words (lite fallbacks skip optional fields) and for cases filed
# before the words existed. Shatter, Missing, Water and Fire have no firm
# word: those keep the AI's type rather than a made-up translation.
DAMAGE_TYPE_TO_TOKEN: dict[str, str] = {
    "Scratch": "Grazed",
    "Scuff / Graze": "Grazed",
    "Paint Damage": "Grazed",
    "Dent": "Dented",
    "Deformation": "Deformed",
    "Crack": "Cracked",
    # The firm's word for broken plastic and torn mountings ("Deformed/cut").
    "Broken / Detached": "Cut",
    "Puncture / Tear": "Torn",
    "Misalignment": "Shifted",
    "Structural Damage": "Crumpled",
    "Electrical Fault": "Faulty",
}

# The AI type a firm word implies, to fill damage_type when the model gave
# only words (the 3D colours and analytics read damage_type). Crumpled maps
# to Deformation, the weaker claim: the firm uses it for insulator pads and
# trim boards too, not only structure. Jammed, Leaked and Rattle describe a
# function no AI type covers.
TOKEN_TO_DAMAGE_TYPE: dict[str, str] = {
    "Grazed": "Scuff / Graze",
    "Dented": "Dent",
    "Bent": "Deformation",
    "Buckled": "Deformation",
    "Deformed": "Deformation",
    "Distorted": "Deformation",
    "Crumpled": "Deformation",
    "Warped": "Deformation",
    "Shifted": "Misalignment",
    "Slack": "Misalignment",
    "Cracked": "Crack",
    "Chipped": "Crack",
    "Torn": "Puncture / Tear",
    "Cut": "Broken / Detached",
    "Faulty": "Electrical Fault",
}

_TOKEN_BY_LOWER = {t.lower(): t for t in FIRM_CONDITION_TOKENS}
_TOKEN_ORDER = {t: i for i, t in enumerate(FIRM_CONDITION_TOKENS)}


def condition_tokens_of(values) -> list[str]:
    """The firm words among `values` (a list, or one "a/b" string), each
    once, in print order. Anything else -- "Necessary", a sentence a model
    leaked -- is dropped."""
    if isinstance(values, str):
        values = values.split("/")
    picked: list[str] = []
    for value in values or []:
        token = _TOKEN_BY_LOWER.get(str(value or "").strip().lower())
        if token and token not in picked:
            picked.append(token)
    return sorted(picked, key=_TOKEN_ORDER.__getitem__)


def join_condition(tokens: list[str]) -> str | None:
    """["Grazed", "Cut"] -> "Grazed/cut": first word capitalised, the rest
    lower case, as the firm's sheets write it. None for no words."""
    words = [t.lower() for t in tokens if t]
    if not words:
        return None
    words[0] = words[0].capitalize()
    return "/".join(words)


# The firm never writes more than three words in one condition (171 lines);
# the schema can't say so without maxItems (the 400 risk), so code does.
MAX_CONDITION_WORDS = 3


def firm_condition(tokens, damage_type: str | None) -> tuple[list[str], str | None]:
    """(words, condition) for a damage item: the AI's words in print order,
    the first MAX_CONDITION_WORDS of them, or -- when it gave none -- the
    one word its damage type maps to; the condition is None when neither
    gives a word (the caller keeps the damage type)."""
    picked = condition_tokens_of(tokens)[:MAX_CONDITION_WORDS]
    if not picked and damage_type in DAMAGE_TYPE_TO_TOKEN:
        picked = [DAMAGE_TYPE_TO_TOKEN[damage_type]]
    return picked, join_condition(picked)


def damage_type_from_tokens(tokens: list[str]) -> str | None:
    """The AI type for a set of firm words: the first word after a graze
    that maps (the firm writes the surface first and the main damage next:
    "Grazed/cracked" is a crack), else the graze; None when none maps."""
    mapped = [t for t in condition_tokens_of(tokens) if t in TOKEN_TO_DAMAGE_TYPE]
    main = [t for t in mapped if t != "Grazed"] or mapped
    return TOKEN_TO_DAMAGE_TYPE[main[0]] if main else None


def parse_condition(text: str | None) -> tuple[list[str], str | None]:
    """A condition a person typed, checked: one of FIRM_WHOLE_CONDITIONS
    as a whole, or firm words joined by "/" (kept in the person's order;
    the firm itself writes "Shifted/bent/cut"). Returns (words, condition
    in the firm's casing); ([], None) for blank. Raises ValueError naming
    the first word that isn't the firm's."""
    text = (text or "").strip()
    if not text:
        return [], None
    for whole in FIRM_WHOLE_CONDITIONS:
        if text.lower() == whole.lower():
            return [], whole
    tokens: list[str] = []
    for word in text.split("/"):
        token = _TOKEN_BY_LOWER.get(word.strip().lower())
        if token is None:
            raise ValueError(word.strip() or text)
        if token not in tokens:
            tokens.append(token)
    return tokens, join_condition(tokens)


def annex_condition(item: dict) -> str:
    """What a damaged part's main Annex A row says under Condition: the
    item's condition (built at drafting, or typed), else its words, else the
    firm word for its damage type, else the damage type as the AI wrote it
    (a case filed before the words existed, or a type with no firm word)."""
    condition = str(item.get("condition") or "").strip()
    if condition:
        return condition
    damage_type = str(item.get("damage_type") or "").strip()
    _, joined = firm_condition(item.get("condition_tokens") or [], damage_type or None)
    return joined or damage_type


# ---------------------------------------------------------------------------
# Part names as the firm writes them -- the floor under app/parts/history.
#
# Annex A rows take their wording from the firm's past reports when there
# are any (history.firm_wording); before this, a part no past case covered
# was printed by its canonical name ("Boot Lid", "Left Boot Lid Lamp") in a
# report the firm signs. This is the wording when history has none, from the
# four CL reports where they had the part. Side codes go last ("Tail lamp
# LH"); a "(side undetermined)" part gets none.
# ---------------------------------------------------------------------------

UNDETERMINED_SUFFIX = " (side undetermined)"
SIDE_CODES = {"Left": "LH", "Right": "RH"}

# A tailgate lifts with its window; a sedan's boot lid doesn't. The firm
# writes "Tail gate assy" for the first (three Vezel reports) and "Boot lid
# assy" for the second (the Civic), and the parts mounted on it follow.
# Pickups have a tail gate too. Other / unknown bodies take the plain words.
_HATCH_BODIES = {"Hatchback", "SUV", "MPV", "Van", "Wagon", "Pickup"}
_SEDAN_BODIES = {"Sedan", "Coupe"}


def body_class(body_type: str | None) -> str | None:
    """"hatch", "sedan", or None when the body type doesn't say."""
    if body_type in _HATCH_BODIES:
        return "hatch"
    if body_type in _SEDAN_BODIES:
        return "sedan"
    return None


# Keyed by the canonical name without its side. "assy" where the firm uses
# it for a whole panel, so no single-word part prints exactly as its
# canonical name.
_FIRM_WORDING: dict[str, str] = {
    "Front Bumper": "Front bumper",
    "Front Grille": "Front grille",
    "Bonnet": "Bonnet assy",
    "Headlamp": "Headlamp",
    "Front Windscreen": "Front windscreen",
    "Front Fender": "Front fender assy",
    "Front Bumper Beam": "Front bumper reinforcement",
    "Fog Lamp": "Fog lamp",
    "Front Number Plate": "Front number plate",
    "Front Wheel Arch Liner": "Front fender splash shield",
    # The Vezel report's "wheel arch protector ganrish", typo left out.
    "Front Wheel Arch Trim": "Front fender wheel arch garnish",
    "Front Door": "Front door assy",
    "Rear Door": "Rear door assy",
    "Front Door Glass": "Front door glass",
    "Rear Door Glass": "Rear door glass",
    "Door": "Door assy",
    "Door Glass": "Door glass",
    "Wing Mirror": "Wing mirror",
    "Front Window Regulator": "Front door glass regulator mechanism",
    "Rear Window Regulator": "Rear door glass regulator mechanism",
    "Window Regulator": "Door glass regulator mechanism",
    "Rear Bumper": "Rear bumper fascia",
    "Boot Lid": "Boot lid assy",
    "Rear Windscreen": "Rear windscreen glass",
    "Tail Lamp": "Tail lamp",
    # The firm calls the quarter panel the rear fender.
    "Rear Quarter Panel": "Rear fender",
    "Quarter Panel": "Rear fender",
    "Rear Number Plate": "Rear number plate",
    "Rear Bumper Beam": "Rear bumper beam",
    "Rear Bumper Reflector": "Rear bumper reflector",
    "Rear End Panel": "Rear end panel",
    "Boot Lid Lock": "Boot lid lock",
    "Boot Lid Hinge": "Boot lid hinges",
    "Boot Lid Weatherstrip": "Boot lid weatherstrip",
    "Boot Lid Inner Trim": "Boot lid insulator",
    "Boot Lid Lamp": "Boot lid lamp",
    "Boot Floor Board": "Spare tyre panel top board",
    "Boot Side Trim": "Rear fender inner trim",
    "Rear Wheel Arch Liner": "Rear bumper wheel arch protector",
    "Rear Wheel Arch Trim": "Rear fender wheel arch garnish",
    "Front Wheel": "Front wheel rim",
    "Rear Wheel": "Rear wheel rim",
    "Wheel": "Wheel rim",
    "Front Tyre": "Front tyre",
    "Rear Tyre": "Rear tyre",
    "Tyre": "Tyre",
    "Wheel Arch Liner": "Wheel arch protector",
    "Wheel Arch Trim": "Wheel arch garnish",
    "Roof": "Roof panel",
    "Underbody / Chassis": "Chassis frame member",
    "Sill / Rocker Panel": "Side sill panel",
    "Rear Floor Panel": "Rear floor panel",
    "Front Subframe": "Front cross member",
    "Rear Subframe": "Rear cross member",
    "Engine Undercover": "Engine under cover",
    "Radiator": "Radiator assy",
    "Air Conditioning Condenser": "Aircon condenser",
    "Exhaust System": "Exhaust pipe",
    "Fuel Tank": "Fuel tank",
    "Suspension - Front": "Front suspension",
    "Suspension - Rear": "Rear suspension",
    "Steering Assembly": "Steering rack and pinion",
    "Airbag System": "Airbag",
    "Parking Sensor": "Reverse sensor",
    "Reversing Camera": "Reverse camera",
    "Interior Trim": "Interior trim",
    "Seat": "Seat assy",
    "Dashboard": "Dashboard panel",
    # Not a part name: the row says a person still has to name it.
    "Other / Not Listed": "Other part (to be named)",
}
_FIRM_WORDING_HATCH: dict[str, str] = {
    "Boot Lid": "Tail gate assy",
    "Boot Lid Lock": "Tail gate lock",
    "Boot Lid Hinge": "Tail gate hinges",
    "Boot Lid Weatherstrip": "Tail gate weatherstrip",
    "Boot Lid Inner Trim": "Tail gate inner trim board",
    "Boot Lid Lamp": "Tail gate reflector lamp",
    "Boot Floor Board": "Rear floor panel board",
    "Boot Side Trim": "Rear fender inner trim board",
}


def split_side(part: str) -> tuple[str, str | None]:
    """A canonical name -> (name without its side, "Left" / "Right" /
    None): "Left Front Fender" -> ("Front Fender", "Left"), "Tail Lamp
    (side undetermined)" -> ("Tail Lamp", None)."""
    if part.endswith(UNDETERMINED_SUFFIX):
        return part[: -len(UNDETERMINED_SUFFIX)], None
    for side in SIDE_CODES:
        if part.startswith(side + " "):
            return part[len(side) + 1:], side
    return part, None


def _firm_names(wording: dict[str, str]) -> dict[str, str]:
    out = {}
    for part in CANONICAL_PARTS:
        base, side = split_side(part)
        if base in wording:
            out[part] = f"{wording[base]} {SIDE_CODES[side]}" if side else wording[base]
    return out


# canonical name -> the firm's wording; scripts/test_detection_wording.py
# checks every canonical part has one and none prints as a canonical name.
FIRM_PART_NAMES: dict[str, str] = _firm_names(_FIRM_WORDING)
FIRM_PART_NAMES_HATCH: dict[str, str] = _firm_names({**_FIRM_WORDING, **_FIRM_WORDING_HATCH})


def firm_part_name(part: str | None, body_type: str | None = None, unsided: bool = False) -> str | None:
    """The floor wording for a canonical part on this body type; None for
    a name that isn't canonical (a case filed before the taxonomy holds the
    firm's own free text, which prints as it is). unsided: the same wording
    without its LH / RH, for a side nobody has confirmed (an inferred one:
    "Rear wheel rim LH" -> "Rear wheel rim", keeping the front/rear the
    undetermined name would lose)."""
    names = FIRM_PART_NAMES_HATCH if body_class(body_type) == "hatch" else FIRM_PART_NAMES
    name = names.get((part or "").strip())
    if name and unsided:
        for code in SIDE_CODES.values():
            if name.endswith(" " + code):
                return name[: -len(code) - 1]
    return name


def unsided_part(part: str) -> str | None:
    """The "(side undetermined)" name for a sided canonical part: "Left Tail
    Lamp" -> "Tail Lamp (side undetermined)", "Left Rear Wheel" -> "Wheel
    (side undetermined)", "Right Rear Quarter Panel" -> "Quarter Panel (side
    undetermined)". The part itself when it has no side; None when no such
    name exists. The inverse of sided_part."""
    base, side = split_side(part)
    if side is None:
        return part
    for candidate in (base, base.removeprefix("Front ").removeprefix("Rear ")):
        name = candidate + UNDETERMINED_SUFFIX
        if name in CANONICAL_PARTS:
            return name
    return None


# ---------------------------------------------------------------------------
# Placing a "(side undetermined)" part on a side (intake/pipeline.
# resolve_sides). Doors and door glass are not placed: a side alone doesn't
# say front or rear door, nor so which door's regulator. Wheels, tyres and
# the arch's liner and trim need the end too.
# ---------------------------------------------------------------------------

_NEEDS_END = {"Wheel", "Tyre", "Wheel Arch Liner", "Wheel Arch Trim"}
_NOT_PLACED = {"Door", "Door Glass", "Window Regulator"}


def sided_part(part: str, side: str | None, end: str | None = None) -> str | None:
    """"Tail Lamp (side undetermined)" + "Left" -> "Left Tail Lamp"; "Wheel
    (side undetermined)" + "Left" + "Rear" -> "Left Rear Wheel". None when
    the part isn't a side-undetermined one, or can't be placed with what is
    known."""
    if not part.endswith(UNDETERMINED_SUFFIX) or side not in SIDE_CODES:
        return None
    base = part[: -len(UNDETERMINED_SUFFIX)]
    if base in _NOT_PLACED:
        return None
    if base in _NEEDS_END:
        if end not in ("Front", "Rear"):
            return None
        name = f"{side} {end} {base}"
    elif base == "Quarter Panel":
        name = f"{side} Rear Quarter Panel"
    else:
        name = f"{side} {base}"
    return name if name in CANONICAL_PARTS else None
