"""An OEM catalogue row's name -> one of OUR canonical parts (taxonomy.CANONICAL_PARTS).

The microfiche catalogues name rows head-first and in the maker's words:
"FACE, RR. BUMPER", "LOCK ASSY., T/GATE", "LAMP UNIT, L. LID", "PANEL R, FR
FENDER" (cut short by the site). to_part() reads one such name, with the
diagram it sits on, and answers the canonical part it is -- or None.

None is always better than a wrong part: a fastener, a clip, a label, a
bulb, a trim piece with no canonical part, anything doubtful. A listed part
the catalogue does not hold is only a missed neighbour; a wrong one would
put a part in front of a surveyor that has nothing to do with the damage.
So the rules go by the row's head noun (the words before the first comma)
and say what they accept; nothing is accepted by default.

Traps the rules are written around (each has a row in the gold file,
scripts/fixtures/oem_names_gold.json):
  * a trunk lid / door "SILENCER" is a sound pad, the Exhaust System is the
    "SILENCER COMP., EX." of the exhaust diagram;
  * "GRILLE, R. SPEAKER" is a speaker grille, not the Front Grille;
  * "WHEELHOUSE" is the body panel round a wheel, not a Wheel;
  * "PLATE, TIRE PRESSURE CAUTION" is a label, not a Tyre;
  * "GUARD, FR. SPLASH" on a brake diagram is the disc's dust shield, not
    the fender's splash shield;
  * the front and rear of a car can share a diagram (a Civic's "BUMPERS"):
    front or rear comes from the row's own FR./RR., never from the diagram.

Sides follow taxonomy: a side given in the name ("R.", "L.", "(RH)", the
site's cut "R," of "PANEL R, FR FENDER") or by the caller gives "Right Tail
Lamp"; a sided part with no side known gives its "(side undetermined)"
name, as the detection's own parts do. "RR." is rear, never right.
"""

import re

from app.reports.taxonomy import CANONICAL_PARTS, SIDE_CODES, UNDETERMINED_SUFFIX, unsided_part

# R / L alone as a side. The site cuts long names and loses the dot ("PANEL R,
# FR FENDER", "HINGE R RR DOOR U"), so a lone letter counts before a comma or
# an end. A cut-off "L" of LINING at the very end ("... DOOR L") is no side.
_LEFT = re.compile(r"(?<![A-Z0-9.])L(?:\.|(?=,|\s+(?:FR|RR)\b))|\(LH?\)|\bLH\b|\bLEFT\b")
_RIGHT = re.compile(r"(?<![A-Z0-9.])R(?:\.|(?=,|\s+(?:FR|RR)\b))|\(RH?\)|\bRH\b|\bRIGHT\b")

_CODES = re.compile(r"\*[^*]*\*?")  # *NH701M* colour codes, also cut short
_SPECS = re.compile(r"\([^)]*\)?")  # (AGC), (12V 21W), "(T135/90D16 102M) (K"
_SIDE_WORDS = {"r", "l", "lh", "rh", "left", "right"}
_SHORT = {"fr": "front", "rr": "rear", "wshld": "windshield", "t/gate": "tailgate", "tgate": "tailgate",
          "tire": "tyre", "glas": "glass"}

# Heads that are never a part of ours, whatever else the row says.
_NOT_A_PART_HEAD = re.compile(
    r"^(bolt|nut|clip|screw|washer|grommet|rivet|pin|plug|stud|spring|ring|clamp|bush|tape|stay|spacer|collar|"
    r"circlip|label|cap|seal|sub seal|packing|gasket|band|bulb|socket|plate|c clip|hex|fillister|emblem|sticker|"
    r"wrench|jack|weight|valve|cushion|stopper|bracket|stiffener|support|gusset|insulator)\b"
)
# Words that make the row something else wherever they stand.
_NOT_A_PART_WORD = re.compile(r"\b(speaker|caution|pressure|wheelhouse)\b")


def _side_of(raw: str, given: str | None) -> str | None:
    left, right = bool(_LEFT.search(raw)), bool(_RIGHT.search(raw))
    if left != right:
        return "Left" if left else "Right"
    if left and right:
        return None
    given = (given or "").strip().lower()
    return {"left": "Left", "lh": "Left", "right": "Right", "rh": "Right"}.get(given)


def _name(base: str | None, side: str | None) -> str | None:
    """A family ("Front Door") + a side -> the canonical name. Unsided parts
    pass through; a sided part with no side is its "(side undetermined)" name,
    and so is one whose end is not known ("Wheel": a side alone can't place it)."""
    if base is None:
        return None
    if base in CANONICAL_PARTS:
        return base
    sided = f"Left {base}"
    if sided in CANONICAL_PARTS:
        return f"{side} {base}" if side in SIDE_CODES else unsided_part(sided)
    undetermined = base + UNDETERMINED_SUFFIX
    return undetermined if undetermined in CANONICAL_PARTS else None


def part_name(family: str | None, side: str | None) -> str | None:
    """A family from to_family() and a side -> the canonical name, or None."""
    return _name(family, side)


def _head_and_words(oem_name: str) -> tuple[str, list[str]]:
    text = _SPECS.sub(" ", _CODES.sub(" ", oem_name.lower()))
    head = text.split(",")[0]
    words = [_SHORT.get(w, w) for w in re.findall(r"[a-z0-9/]+", text)]
    # "PANEL R, FR FENDER": the cut name keeps its side letter before the comma
    head_words = [w for w in re.findall(r"[a-z0-9/]+", head) if w not in _SIDE_WORDS]
    return " ".join(head_words), [w for w in words if w not in _SIDE_WORDS]


def _family(head: str, words: list[str], diagram: str) -> str | None:
    t = " ".join(words)
    w = set(words)
    front, rear = "front" in w, "rear" in w
    end = "Front" if front and not rear else "Rear" if rear and not front else None
    dg = diagram.lower()
    head_words = [_SHORT.get(x, x) for x in head.split()]
    head = " ".join(head_words)

    if _NOT_A_PART_HEAD.match(head) or _NOT_A_PART_WORD.search(t):
        return None

    # Exhaust: the silencer of the exhaust diagram, not a lid's sound pad.
    if re.match(r"silencer comp\b", head) or (head.startswith("pipe") and "ex" in w):
        return "Exhaust System" if "ex" in w else None

    # Lamps
    if re.match(r"(headlight|headlamp|head lamp)( unit| assy| comp)*$", head):
        return "Headlamp"
    if re.match(r"(lamp unit|light assy|lamp|light|taillight|tail lamp|tail light)( unit| assy| comp)*$", head):
        if "license" in w or "high" in w:
            return None
        if "fog" in w:
            return None if rear else "Fog Lamp"
        if "lid" in w:
            return "Boot Lid Lamp"
        if "tail" in w or "taillight" in w or "taillight" in dg:
            return "Tail Lamp"
        return None
    if re.match(r"reflect\w*", head) and rear:
        return "Rear Bumper Reflector"

    # Bumpers: front or rear from the row, not the diagram
    if re.match(r"(face|bumper face)$", head) and "bumper" in w and end:
        return f"{end} Bumper"
    if head.startswith("trim panel bumper") and "lower" not in w and end:
        return f"{end} Bumper"
    if re.match(r"(beam|reinforcement)\b", head) and "bumper" in w and end:
        return f"{end} Bumper Beam"
    if head == "base" and "license" in w and end:
        return f"{end} Number Plate"
    if re.match(r"(base|grille|grille assy|radiator grille)$", head) and "grille" in w and (front or "grille" in dg):
        return "Front Grille"
    if re.match(r"(hood|bonnet)( comp| assy| panel)*$", head):
        return "Bonnet"

    # Fenders, arches, body panels
    if re.match(r"(panel|fender|wing)( assy| comp)?$", head) and "fender" in w and front and "inner" not in w:
        return "Front Fender"
    if head == "garnish" and "fender" in w and end:
        return f"{end} Wheel Arch Trim"
    if re.match(r"(protector|guard|shield)\b", head) and ("wheel" in w or "splash" in w):
        if "brake" in dg or "disk" in w:
            return None
        return f"{end} Wheel Arch Liner" if end else "Wheel Arch Liner"
    if head == "mudguard":
        return f"{end} Wheel Arch Trim" if end else "Wheel Arch Trim"
    if head.startswith("panel set") and rear and w & {"outside", "outsid", "ou"}:
        return "Rear Quarter Panel"
    if head.startswith("panel set") and rear and "floor" in w:
        return "Rear Floor Panel"
    if head.startswith("panel") and {"side", "sill"} <= w and "inside" not in w:
        return "Sill / Rocker Panel"
    if head.startswith("panel") and "roof" in w and "rail" not in w:
        return "Roof"
    if t == "panel comp rear" and ("rear panel" in dg or "outer panel" in dg):
        return "Rear End Panel"

    # Doors and glass
    if re.match(r"(panel comp|panel|skin|door|door assy)$", head) and "door" in w and end and "inner" not in w:
        return f"{end} Door"
    if re.match(r"glass( assy| set)?$", head) and "door" in w and "quarter" not in w and end:
        return f"{end} Door Glass"
    if re.match(r"(glass|set)( assy| set)?$", head) and "windshield" in w and end:
        return f"{end} Windscreen"
    if head.startswith("regulator") and end:
        return f"{end} Window Regulator"
    if head.startswith("mirror") and not w & {"inner", "room", "view"}:
        return "Wing Mirror"

    # Boot lid / tailgate and the boot
    if w & {"trunk", "tailgate"}:
        if re.match(r"(lid comp|tailgate( comp| assy)?)$", head):
            return "Boot Lid"
        if head.startswith("hinge"):
            return "Boot Lid Hinge"
        if re.match(r"(lock|striker)\b", head):
            return "Boot Lid Lock"
        if head.startswith("weatherstrip"):
            return "Boot Lid Weatherstrip"
        if head.startswith("lining assy") and "tailgate" in w:
            return "Boot Lid Inner Trim"
        if head.startswith("lining assy") and "side" in w:
            return "Boot Side Trim"
        if head.startswith("board assy") and "floor" in w:
            return "Boot Floor Board"
    if head == "lid comp" and "spare" in w:
        return "Boot Floor Board"

    # Underbody, suspension, steering, wheels
    if head.startswith("sub frame"):
        return "Rear Subframe" if rear else "Front Subframe"
    if head == "cover" and "engine" in w and "under" in w:
        return "Engine Undercover"
    if re.match(r"(knuckle|absorber (assy|unit)|arm comp|link comp|joint comp)\b", head) and (
        w & {"knuckle", "shock", "lower", "stabilizer", "ball"}
    ):
        return "Suspension - Rear" if rear else "Suspension - Front" if front else None
    if re.match(r"(damper unit|beam assy)\b", head) and rear:
        return "Suspension - Rear"
    if head.startswith("rod comp") and {"trailing", "arm"} <= w:
        return "Suspension - Rear"
    if (head.startswith("box comp") and "steering" in w) or (
        re.match(r"end (comp|set)\b", head) and w & {"tie", "rack"}
    ):
        return "Steering Assembly"
    if head.startswith("module assy") and "driver" in w and ("srs" in dg or "steering" in dg):
        return "Airbag System"
    if re.match(r"disk\b", head) and "wheel" in w:
        return "Wheel"
    if head == "tyre":
        return "Tyre"

    # Inside
    if re.match(r"(armrest|pull pocket|carpet)\b", head):
        return "Interior Trim"
    return None


def to_family(oem_name: str, diagram: str | None = None, side: str | None = None) -> tuple[str, str | None] | None:
    """(family, side) of an OEM row, or None: the canonical part without its
    side ("Front Door", "Tail Lamp"; "Wheel" when the row names no end) and
    "Left" / "Right" / None. For callers that compare parts across sides."""
    if not oem_name or not isinstance(oem_name, str):
        return None
    head, words = _head_and_words(oem_name)
    base = _family(head, words, diagram or "")
    if _name(base, None) is None:
        return None
    return base, _side_of(_CODES.sub(" ", oem_name).upper(), side)


def to_part(oem_name: str, diagram: str | None = None, side: str | None = None) -> str | None:
    """The canonical part an OEM row is, or None. `diagram` is the name of
    the diagram the row sits on (context); `side` ("left"/"right"/"LH"/"RH")
    is used when the name itself states none."""
    found = to_family(oem_name, diagram, side)
    return _name(*found) if found else None


_FRONT_FAMILIES = {"Bonnet", "Headlamp", "Fog Lamp", "Engine Undercover", "Radiator", "Air Conditioning Condenser",
                   "Suspension - Front", "Steering Assembly"}
_REAR_FAMILIES = {"Rear Bumper", "Tail Lamp", "Suspension - Rear", "Exhaust System", "Fuel Tank", "Reversing Camera"}


def end_of(part: str | None) -> str | None:
    """"front" / "rear" for a part (a canonical name, sided or not, or a
    family) that sits at one end of the car, else None. Boot parts and the
    tail lamps are rear; doors, wheels, sills and the like have no end of
    their own unless the name says so."""
    if not part:
        return None
    base = part.removesuffix(UNDETERMINED_SUFFIX)
    for side in SIDE_CODES:
        base = base.removeprefix(side + " ")
    if base.startswith("Front ") or base in _FRONT_FAMILIES:
        return "front"
    if base.startswith(("Rear ", "Boot ")) or base in _REAR_FAMILIES:
        return "rear"
    return None
