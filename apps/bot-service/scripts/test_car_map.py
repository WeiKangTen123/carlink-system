"""The car map (plans/2026-11-18-car-knowledge/K1-design.md; app/intake/
car_map.py) -- pure, offline, no Gemini.

Covers K1's done-when list:
  - the new names (owner option a, car_map_new_names.json): 20-30
    families, none already canonical, each sided one with the "(side
    undetermined)" twin taxonomy.unsided_part would find, a 3D zone the
    dashboard has, the firm's wording style; broken copies refused;
  - the schema file: its enums and keys are car_map.py's;
  - the validator on a worked example (marked: example, K1b rewrites):
    passes; ~35 broken copies each give a line (names, ids, {side}
    symmetry, signs, hops, cycles, assemblies, priors, reasons, gates,
    keys); any junk anywhere is a line, never an exception;
  - load / load_new_names never raise; sha256_lf ignores CRLF;
  - from_l1: L1 as a car map validates and compiles back into L1 field
    for field;
  - THE EQUIVALENCE: links.build on the converted map gives the same
    output as on L1 -- (a) every build / build_all / for_parent call
    test_links.py makes, replayed on both tables; (b) a sweep of every L1
    parent (both sides and undetermined) x hit words x read confidence x
    options, and multi-part cases with photos, intact lists, dismissals
    and catalogue neighbours;
  - links_table: body gates (Sedan drops a tailgate-only link; unknown
    keeps it), new names left out until applied, member defaults;
    relation_info for the Review step;
  - the seam: settings.intake_car_map ("off" by default) and
    table_for_links -- off is L1 itself, shadow L1 + the map, on the map
    (L1 when the map can't be used);
  - pure: no AI client imported; inputs not changed; links never imports
    car_map.

    python scripts/test_car_map.py

Parts, photos and the example map are made up from general knowledge.
"""
import contextlib
import copy
import io
import json
import os
import random
import re
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="car-map-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ.setdefault("GEMINI_API_KEY", "fake-car-map-key")
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"

import test_links as TL  # noqa: E402  (its fixtures: the equivalence replays them)

from app.intake import car_map, links  # noqa: E402

# Imported by car_map itself? Checked before anything else pulls them in.
AI_CLIENT_IMPORTED = sorted(m for m in sys.modules if m in ("app.ai.structured", "google.genai")
                            or m.startswith("google.genai."))

from app import config  # noqa: E402
from app.reports.taxonomy import BODY_TYPES, CANONICAL_PARTS, FIRM_CONDITION_TOKENS, unsided_part  # noqa: E402

L1 = TL.TABLE
ON = links.Options(mode="on")
DASHBOARD = ROOT.parent / "dashboard"

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}


def check(cond, label: str, why="") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append((_section["name"], label))
        print(f"    FAIL {label}" + (f"\n         {why}" if why != "" else ""))
    return bool(cond)


def run_section(name: str, fn) -> None:
    _section["name"] = name
    print(f"\n{name}")
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()


item, photo = TL.item, TL.photo


# ---------------------------------------------------------------- the worked example (example, K1b rewrites)

R = "general body-repair knowledge (example)"


def rel(rid, assembly, parent, child, kind, side, min_hit, reason, **more):
    return {"id": rid, "assembly": assembly, "parent": parent, "child": child, "type": kind, "side": side,
            "min_hit": min_hit, "trigger_tokens": [], "reason": reason, "sources": ["example"], **more}


def prior(rank):
    return {"rank": rank, "source": R}


EXAMPLE = {
    "version": "EX1", "format": 1, "built_from": ["example for tests: K1b rewrites"],
    "assemblies": [
        {"id": "front-bumper", "name": "Front bumper assembly", "area": "front", "anchors": ["Front Bumper"],
         "members": [
             {"part": "Front Bumper", "role": "panel"},
             {"part": "Front Bumper Absorber", "role": "absorber", "hidden": True, "strip_down": True, "new": True},
             {"part": "Front Bumper Beam", "role": "structure", "hidden": True, "strip_down": True},
             {"part": "{side} Front Bumper Bracket", "role": "mount", "hidden": True, "strip_down": True, "new": True},
             {"part": "{side} Fog Lamp", "role": "lamp", "fitted": "if_fitted"},
             {"part": "{side} Fog Lamp Cover", "role": "trim", "new": True},
             {"part": "Front Number Plate", "role": "trim"},
             {"part": "Front Parking Sensor", "role": "electrical", "fitted": "if_fitted", "new": True},
             {"part": "Front Grille", "role": "trim"},
             {"part": "Engine Undercover", "role": "trim", "hidden": True},
         ],
         "signs": [
             {"id": "fb-pushed", "ask": "Is the bumper cover pushed inward, not just scratched?", "level": "heavy",
              "points_to": ["front-bumper>beam", "front-bumper>absorber"],
              "unlocks": ["front-bumper>condenser>radiator"]},
             {"id": "fb-sag", "ask": "Does the bumper hang lower at one end or stand out from the fender?",
              "level": "medium", "points_to": ["front-bumper>bracket"]},
             {"id": "fb-gap", "ask": "Is a gap between bumper and fender or headlamp uneven or closed?",
              "level": "heavy", "points_to": ["front-bumper>beam"]},
             {"id": "fb-fluid", "ask": "Is there coolant or other fluid on the ground under the front?",
              "level": "heavy", "points_to": ["front-bumper>condenser"], "unlocks": ["front-bumper>condenser>radiator"]},
         ],
         "catalogue": {"oem_diagrams": ["FRONT BUMPER", "BUMPERS", "FRONT BUMPER & BUMPER STAY"],
                       "oem_numbers": {"toyota": ["5201"]}},
         "sources": [R]},
        {"id": "front-end", "name": "Front end and cooling", "area": "front", "anchors": [],
         "members": [
             {"part": "Air Conditioning Condenser", "role": "mechanical", "hidden": True, "strip_down": True},
             {"part": "Radiator", "role": "mechanical", "hidden": True, "strip_down": True},
             {"part": "Radiator Support", "role": "structure", "hidden": True, "strip_down": True, "new": True},
         ],
         "signs": [], "catalogue": {"oem_diagrams": ["RADIATOR", "RADIATOR SUPPORT"], "oem_numbers": {"toyota": ["5301"]}}},
        {"id": "boot-lid", "name": "Boot lid / tailgate assembly", "area": "rear", "anchors": ["Boot Lid"],
         "members": [
             {"part": "Boot Lid", "role": "panel"},
             {"part": "Boot Lid Lock", "role": "mechanism", "hidden": True},
             {"part": "Boot Lid Hinge", "role": "mechanism", "hidden": True},
             {"part": "Boot Lid Striker", "role": "mechanism", "hidden": True, "new": True},
             {"part": "{side} Boot Lid Strut", "role": "mechanism", "hidden": True, "fitted": "if_fitted", "new": True},
             {"part": "Boot Lid Weatherstrip", "role": "seal", "hidden": True},
             {"part": "Boot Lid Inner Trim", "role": "trim", "hidden": True},
             {"part": "{side} Boot Lid Lamp", "role": "lamp"},
             {"part": "Rear Number Plate Lamp", "role": "lamp", "new": True},
             {"part": "Reversing Camera", "role": "electrical", "fitted": "if_fitted"},
             {"part": "Rear Windscreen", "role": "glass"},
             {"part": "Rear Wiper Motor", "role": "electrical", "hidden": True, "fitted": "if_fitted", "body": ["hatch"],
              "new": True},
             {"part": "Boot Lid Spoiler", "role": "trim", "fitted": "if_fitted", "new": True},
             {"part": "Rear End Panel", "role": "structure", "hidden": True, "strip_down": True},
         ],
         "signs": [
             {"id": "bl-shut", "ask": "Is the boot lid open, tied shut, or not closed flush?", "level": "heavy",
              "points_to": ["boot-lid>lock", "boot-lid>striker"]},
             {"id": "bl-gap", "ask": "Are the gaps at the lid's top corners uneven?", "level": "heavy",
              "points_to": ["boot-lid>hinge"]},
             {"id": "bl-crease", "ask": "Is the lid creased or buckled, not just dented?", "level": "heavy",
              "points_to": ["boot-lid>inner-trim", "boot-lid>rear-end-panel"],
              "unlocks": ["boot-lid>rear-end-panel>rear-floor-panel"]},
         ],
         "catalogue": {"oem_diagrams": ["TRUNK LID", "TAILGATE PANEL", "LUGGAGE COMPARTMENT DOOR & LOCK", "BACK DOOR"],
                       "oem_numbers": {"toyota": ["6105"]}}},
        {"id": "rear-end", "name": "Rear end panel and boot floor", "area": "rear", "anchors": ["Rear End Panel"],
         "members": [
             {"part": "Rear End Panel", "role": "structure", "hidden": True, "strip_down": True},
             {"part": "Rear Floor Panel", "role": "structure", "hidden": True, "strip_down": True},
             {"part": "Boot Floor Board", "role": "trim", "hidden": True},
         ],
         "signs": [], "catalogue": {"oem_diagrams": ["REAR PANEL", "FLOOR / INNER PANELS"]}},
    ],
    "relations": [
        rel("front-bumper>fog-lamp", "front-bumper", "Front Bumper", "{side} Fog Lamp", "attached", "impact", "medium",
            "Fog lamps sit in the cover's lower corners; a crack or dent there can break the lamp or its bracket.",
            hidden=False, strip_down=False, position="corner", prior=prior(2)),
        rel("front-bumper>fog-lamp-cover", "front-bumper", "Front Bumper", "{side} Fog Lamp Cover", "attached", "impact",
            "medium", "The lamp bezel or blank cover clips into the cover's corner and breaks with it.",
            position="corner", prior=prior(2)),
        rel("front-bumper>number-plate", "front-bumper", "Front Bumper", "Front Number Plate", "attached", "none",
            "medium", "Plate and holder are fixed to the cover's centre; a dent there bends or cracks them.",
            position="centre", prior=prior(2)),
        rel("front-bumper>grille", "front-bumper", "Front Bumper", "Front Grille", "attached", "none", "medium",
            "The grille clips into or beside the cover; a pushed-in centre breaks its tabs.", prior=prior(3)),
        rel("front-bumper>sensor", "front-bumper", "Front Bumper", "Front Parking Sensor", "attached", "none", "medium",
            "Sensors sit in holes in the cover; a dent near them pushes them in or cuts the wiring.", prior=prior(3)),
        rel("front-bumper>bracket", "front-bumper", "Front Bumper", "{side} Front Bumper Bracket", "behind", "impact",
            "medium", "The cover's end clips into a side bracket; a torn or pushed end snaps it.", position="corner",
            prior=prior(2)),
        rel("front-bumper>absorber", "front-bumper", "Front Bumper", "Front Bumper Absorber", "behind", "none", "heavy",
            "The foam absorber behind the cover crushes when the cover is pushed in.", prior=prior(2)),
        rel("front-bumper>beam", "front-bumper", "Front Bumper", "Front Bumper Beam", "behind", "none", "heavy",
            "Sits behind the cover and absorber; a pushed-in cover can bend it. Check with the bumper off.",
            prior=prior(1)),
        rel("front-bumper>undercover", "front-bumper", "Front Bumper", "Engine Undercover", "behind", "none", "heavy",
            "Fixed to the bumper's lower edge; a pushed-in or torn lower bumper tears it.", strip_down=False,
            prior=prior(3)),
        rel("front-bumper>condenser", "front-bumper", "Front Bumper", "Air Conditioning Condenser", "crush", "none",
            "heavy", "The first cooling part behind the bumper; a pushed-in front bends its fins or pipes.",
            position="centre", prior=prior(3)),
        rel("condenser>radiator", "front-end", "Air Conditioning Condenser", "Radiator", "crush", "none", "heavy",
            "Sits behind the condenser; a condenser pushed back can bend its core or crack a tank."),
        rel("boot-lid>lock", "boot-lid", "Boot Lid", "Boot Lid Lock", "opens_with", "none", "heavy",
            "The lock sits in the lid's lower edge; a lid that will not open or latch usually means a bent lock.",
            trigger_tokens=["Jammed"], prior=prior(1)),
        rel("boot-lid>hinge", "boot-lid", "Boot Lid", "Boot Lid Hinge", "opens_with", "none", "heavy",
            "A lid pushed forward or out of line bends its hinges; uneven gaps at the top corners are the sign.",
            trigger_tokens=["Jammed", "Shifted"], prior=prior(2)),
        rel("boot-lid>striker", "boot-lid", "Boot Lid", "Boot Lid Striker", "opens_with", "none", "heavy",
            "The striker the lock catches sits under the opening; a lid that will not latch points at it.",
            trigger_tokens=["Jammed"], prior=prior(3)),
        rel("boot-lid>strut", "boot-lid", "Boot Lid", "{side} Boot Lid Strut", "opens_with", "impact", "heavy",
            "Gas struts hold the lid up; a lid pushed out of line bends them.", prior=prior(4)),
        rel("boot-lid>weatherstrip", "boot-lid", "Boot Lid", "Boot Lid Weatherstrip", "opens_with", "none", "heavy",
            "The seal round the opening is cut or pushed out when the lid buckles.", prior=prior(2)),
        rel("boot-lid>inner-trim", "boot-lid", "Boot Lid", "Boot Lid Inner Trim", "opens_with", "none", "heavy",
            "The trim inside the lid cracks or loses its clips when the lid buckles.", prior=prior(2)),
        rel("boot-lid>lamp", "boot-lid", "Boot Lid", "{side} Boot Lid Lamp", "attached", "impact", "medium",
            "Lamps on the lid crack or come loose when the lid is dented next to them.", position="corner",
            prior=prior(2)),
        rel("boot-lid>plate-lamp", "boot-lid", "Boot Lid", "Rear Number Plate Lamp", "attached", "none", "medium",
            "The plate lamp sits above the plate; a dent there cracks it.", position="centre", prior=prior(3)),
        rel("boot-lid>camera", "boot-lid", "Boot Lid", "Reversing Camera", "attached", "none", "medium",
            "Where the camera sits in the lid's garnish, a dent there cracks or misaligns it.", position="centre",
            prior=prior(3)),
        rel("boot-lid>rear-windscreen", "boot-lid", "Boot Lid", "Rear Windscreen", "attached", "none", "heavy",
            "On a tailgate the rear glass sits in the lid, so a twisted lid can crack it.", body=["hatch"],
            prior=prior(2)),
        rel("boot-lid>wiper-motor", "boot-lid", "Boot Lid", "Rear Wiper Motor", "behind", "none", "heavy",
            "Inside a tailgate under the glass; a tailgate pushed in at its centre can crack it.", position="centre",
            prior=prior(4)),
        rel("boot-lid>rear-end-panel", "boot-lid", "Boot Lid", "Rear End Panel", "crush", "none", "heavy",
            "The lid closes onto the rear end panel; a lid pushed in often means that panel buckled too.",
            prior=prior(2)),
        rel("rear-end-panel>rear-floor-panel", "rear-end", "Rear End Panel", "Rear Floor Panel", "crush", "none", "heavy",
            "The panel is welded to the boot floor; pushed forward, it wrinkles the floor behind it."),
    ],
    "chains": [
        {"id": "front-bumper>condenser>radiator", "first": "front-bumper>condenser", "then": "condenser>radiator",
         "min_hit": "heavy", "reason": "A front pushed in far enough to reach the condenser can push it into the radiator."},
        {"id": "boot-lid>rear-end-panel>rear-floor-panel", "first": "boot-lid>rear-end-panel",
         "then": "rear-end-panel>rear-floor-panel", "reason": "A creased lid with the panel under it pushed in can wrinkle the floor."},
    ],
}
NEW = car_map.new_name_set(car_map.load_new_names())


def rel_of(cm: dict, rid: str) -> dict:
    return next(r for r in cm["relations"] if r["id"] == rid)


def asm_of(cm: dict, aid: str) -> dict:
    return next(a for a in cm["assemblies"] if a["id"] == aid)


def member_of(cm: dict, aid: str, part: str) -> dict:
    return next(m for m in asm_of(cm, aid)["members"] if m["part"] == part)


# ---------------------------------------------------------------- the new names


def _zone_keys() -> set[str]:
    src = (DASHBOARD / "lib" / "vehicleZones.ts").read_text(encoding="utf-8")
    labels = re.search(r"ZONE_LABELS: Record<string, string> = \{(.*?)\n\};", src, re.S).group(1)
    either = re.search(r"EITHER_SIDE: Record<string, string\[\]> = \{(.*?)\n\};", src, re.S).group(1)
    return set(re.findall(r"^\s*([a-z_]+):", labels, re.M)) | set(re.findall(r"^\s*([a-z_]+):", either, re.M))


def new_names() -> None:
    doc = car_map.load_new_names()
    check(doc is not None and car_map.validate_new_names(doc) == [], "car_map_new_names.json loads and validates",
          car_map.validate_new_names(doc)[:3] if doc else "None")
    entries = doc["names"]
    families = {e["twin"] or e["name"] for e in entries}
    check(20 <= len(families) <= 30, f"owner option a: 20-30 families ({len(families)}; {len(entries)} entries)")
    check(not set(NEW) & set(CANONICAL_PARTS) and len(NEW) == sum(2 if e["sided"] else 1 for e in entries),
          "none is already canonical; {side} expands to Left and Right", sorted(set(NEW) & set(CANONICAL_PARTS)))
    twins = {e["twin"] for e in entries if e["twin"]}
    check(all(t not in CANONICAL_PARTS and t.endswith(" (side undetermined)") for t in twins),
          "every sided family has a new \"(side undetermined)\" twin")
    widened = list(CANONICAL_PARTS) + sorted(twins)

    def unsided_with(name: str) -> str | None:  # taxonomy.unsided_part, over the widened list
        b = name.split(" ", 1)[1]
        for cand in (b, b.removeprefix("Front ").removeprefix("Rear ")):
            if cand + " (side undetermined)" in widened:
                return cand + " (side undetermined)"
        return None

    check(all(unsided_with(e["name"].replace("{side}", "Left")) == e["twin"] for e in entries if e["sided"]),
          "each twin is the one taxonomy.unsided_part will find once applied")
    check(unsided_part("Left Fog Lamp") == "Fog Lamp (side undetermined)", "(today's rule, for reference)")
    zones = _zone_keys()
    used = {z for e in entries for z in ([e["zone"]] if isinstance(e["zone"], str) else e["zone"].values())}
    check(used <= zones and "front_bumper" in zones and "any_door" in zones,
          "every 3D zone is one vehicleZones.ts has (no new mesh)", sorted(used - zones))
    check(all(e["firm"][0].isupper() and e["firm"][1:] == e["firm"][1:].lower() and not re.search(r"\b(LH|RH)\b", e["firm"])
              for e in entries), "firm wording in the firm's style: first letter capital, no side code (added last)")
    check({e["assembly"] for e in entries} <= {"front-bumper", "front-end", "bonnet", "front-door", "rear-door",
                                               "rear-bumper", "boot-lid", "rear-quarter"},
          "each names one of the design's suggested assemblies", sorted({e["assembly"] for e in entries}))
    check({e["coarse"] for e in entries if e["coarse"]} == {"Parking Sensor"},
          "the sensor split: Front / Rear Parking Sensor, covered by today's Parking Sensor")
    owner = {"Front Door Lock", "Rear Door Lock", "Front Door Hinge", "Rear Door Hinge", "Bonnet Latch", "Bonnet Hinge",
             "Front Bumper Absorber", "Rear Bumper Absorber", "Front Bumper Bracket", "Rear Bumper Bracket",
             "Front Parking Sensor", "Rear Parking Sensor"}
    check(owner <= {car_map.base(e["name"]) for e in entries}, "the owner's named ones are all there")

    def broken(change) -> list[str]:
        bad = copy.deepcopy(doc)
        change(bad["names"])
        return car_map.validate_new_names(bad)

    cases = {
        "already canonical": lambda n: n[0].update(name="Front Bumper Beam"),
        "sided flag wrong": lambda n: n[0].update(sided=True),
        "a sided name with no twin": lambda n: n[1].update(twin=None),
        "a canonical twin": lambda n: n[1].update(twin="Tail Lamp (side undetermined)"),
        "an unsided name with a twin": lambda n: n[0].update(twin="Absorber (side undetermined)"),
        "an unknown area": lambda n: n[0].update(area="roof"),
        "an unknown role": lambda n: n[0].update(role="gizmo"),
        "firm wording that is a part name": lambda n: n[0].update(firm="Front Bumper Beam"),
        "a sided zone without Left/Right/twin": lambda n: n[1].update(zone="front_bumper"),
        "also_check too long": lambda n: n[0].update(also_check="x" * 31),
        "why with a 3-digit number": lambda n: n[0].update(why="Part 123 of the bumper."),
        "why naming a practice make": lambda n: n[0].update(why="As on a Honda."),
        "listed twice": lambda n: n.append(copy.deepcopy(n[0])),
        "a coarse name that isn't canonical": lambda n: n[2].update(coarse="Sensor"),
        "hidden not a bool": lambda n: n[0].update(hidden="yes"),
    }
    for name, change in cases.items():
        check(broken(change), f"refused: {name}", broken(change)[:1])
    folder = Path(_tmp, "names")
    folder.mkdir()
    (folder / "broken.json").write_text("{nope", encoding="utf-8")
    check(car_map.load_new_names(folder / "missing.json") is None and car_map.load_new_names(folder / "broken.json") is None
          and car_map.load_new_names(folder) is None, "load_new_names: missing, broken, a folder -> None, no exception")


# ---------------------------------------------------------------- the schema file


def schema_file() -> None:
    schema = json.loads(car_map.SCHEMA_PATH.read_text(encoding="utf-8"))
    d = schema["$defs"]
    check(schema["properties"]["format"]["const"] == car_map.FORMAT, "format 1")
    check(d["member"]["properties"]["role"]["enum"] == list(car_map.ROLES), "roles")
    check(d["relation"]["properties"]["position"]["enum"] == list(car_map.POSITIONS), "positions")
    check(d["member"]["properties"]["fitted"]["enum"] == list(car_map.FITTED), "fitted")
    check(d["body"]["anyOf"][1]["items"]["enum"] == list(car_map.BODY_GATES), "body gates (taxonomy BODY_TYPES + classes)")
    check(d["assembly"]["properties"]["area"]["enum"] == list(car_map.AREAS), "assembly areas")
    check(set(d["relation"]["properties"]["area"]["enum"]) == set(car_map.RELATION_AREAS), "relation areas (L1's)")
    check(d["relation"]["properties"]["type"]["enum"] == list(links.LINK_TYPES)
          and d["relation"]["properties"]["side"]["enum"] == list(links.LINK_SIDES)
          and d["level"]["enum"] == list(links.HIT_LEVELS), "types, sides, levels")
    check(d["relation"]["properties"]["trigger_tokens"]["items"]["enum"] == list(FIRM_CONDITION_TOKENS), "trigger words")
    for kind, key in (("map", None), ("assembly", "assembly"), ("member", "member"), ("sign", "sign"),
                      ("relation", "relation"), ("chain", "chain"), ("catalogue", "catalogue")):
        props = set(schema["properties"]) if key is None else set(d[key]["properties"])
        check(props == car_map.KEYS[kind], f"{kind}: the schema's keys are the validator's", props ^ car_map.KEYS[kind])
    check(set(d["relation"]["required"]) >= {"id", "assembly", "parent", "child", "type", "side", "min_hit", "reason"},
          "a relation's required fields")


# ---------------------------------------------------------------- the validator


def validator() -> None:
    check(car_map.validate(EXAMPLE, names=NEW) == [], "the worked example validates", car_map.validate(EXAMPLE, names=NEW)[:3])
    check(car_map.validate(EXAMPLE) == [], "with the new names from the file by default")
    check(car_map.validate(EXAMPLE, names=set()), "without the new names it does not")

    def broken(change) -> list[str]:
        bad = copy.deepcopy(EXAMPLE)
        change(bad)
        return car_map.validate(bad, names=NEW)

    def add_cycle(m):
        m["relations"].append(rel("condenser>front-bumper", "front-end", "Air Conditioning Condenser", "Front Bumper",
                                  "crush", "none", "heavy", "Made up, to make a cycle."))
        m["chains"].append({"id": "loop", "first": "front-bumper>condenser", "then": "condenser>front-bumper",
                            "reason": "A loop."})
        asm_of(m, "front-bumper")["signs"][0]["unlocks"].append("loop")

    def add_chain_of_chains(m):
        m["chains"].append({"id": "three-hops", "first": "front-bumper>condenser",
                            "then": "front-bumper>condenser>radiator", "reason": "Three hops."})
        asm_of(m, "front-bumper")["signs"][0]["unlocks"].append("three-hops")

    cases = {
        "an unknown part": lambda m: asm_of(m, "front-bumper")["members"].append({"part": "Front Bumper Foam", "role": "trim"}),
        "an undetermined child": lambda m: rel_of(m, "front-bumper>fog-lamp").update(child="Fog Lamp (side undetermined)"),
        "Other / Not Listed": lambda m: asm_of(m, "front-end")["members"].append({"part": "Other / Not Listed", "role": "other"}),
        "{side} in the middle": lambda m: rel_of(m, "front-bumper>fog-lamp").update(child="Front {side} Fog Lamp"),
        "a relation id twice": lambda m: rel_of(m, "front-bumper>grille").update(id="front-bumper>beam"),
        "a chain id that is a relation's": lambda m: m["chains"][0].update(id="front-bumper>beam"),
        "a sign id twice": lambda m: asm_of(m, "front-bumper")["signs"][1].update(id="fb-pushed"),
        "an assembly id twice": lambda m: asm_of(m, "rear-end").update(id="boot-lid"),
        "a side the names don't say": lambda m: rel_of(m, "front-bumper>fog-lamp").update(side="none"),
        "a parent outside its assembly": lambda m: rel_of(m, "front-bumper>beam").update(assembly="boot-lid"),
        "a child in no assembly": lambda m: rel_of(m, "front-bumper>grille").update(child="Roof"),
        "an unknown assembly": lambda m: rel_of(m, "front-bumper>grille").update(assembly="nowhere"),
        "a sign pointing at no relation": lambda m: asm_of(m, "boot-lid")["signs"][0]["points_to"].append("boot-lid>nothing"),
        "a sign pointing away from its anchors": lambda m: asm_of(m, "boot-lid")["signs"][0]["points_to"].append(
            "front-bumper>beam"),
        "a sign unlocking no chain": lambda m: asm_of(m, "boot-lid")["signs"][0].update(unlocks=["no-chain"]),
        "a sign unlocking a chain from elsewhere": lambda m: asm_of(m, "boot-lid")["signs"][0].update(
            unlocks=["front-bumper>condenser>radiator"]),
        "a sign pointing at nothing": lambda m: asm_of(m, "boot-lid")["signs"][1].update(points_to=[], unlocks=[]),
        "a chain whose second hop starts elsewhere": lambda m: m["chains"][0].update(then="rear-end-panel>rear-floor-panel"),
        "a chain of chains (3 hops)": add_chain_of_chains,
        "a cycle": add_cycle,
        "a chain no sign unlocks": lambda m: [s.update(unlocks=[]) for s in asm_of(m, "front-bumper")["signs"]],
        "a chain naming no relation": lambda m: m["chains"][0].update(first="nope"),
        "a prior of 6": lambda m: rel_of(m, "front-bumper>beam").update(prior={"rank": 6, "source": R}),
        "a prior of True": lambda m: rel_of(m, "front-bumper>beam").update(prior={"rank": True, "source": R}),
        "a prior with no source": lambda m: rel_of(m, "front-bumper>beam").update(prior={"rank": 1}),
        "a reason over 160": lambda m: rel_of(m, "front-bumper>beam").update(reason="x" * 161),
        "a 3-digit number": lambda m: rel_of(m, "front-bumper>beam").update(reason="Check part 123 behind the cover."),
        "a practice make": lambda m: rel_of(m, "front-bumper>beam").update(reason="On a Civic the beam is steel."),
        "a make in a source": lambda m: rel_of(m, "front-bumper>beam").update(sources=["Honda catalogue"]),
        "an unknown body": lambda m: rel_of(m, "boot-lid>rear-windscreen").update(body=["Limousine"]),
        "an empty body gate": lambda m: rel_of(m, "boot-lid>rear-windscreen").update(body=[]),
        "an unknown position": lambda m: rel_of(m, "front-bumper>fog-lamp").update(position="edge"),
        "new: false on a new name": lambda m: member_of(m, "front-bumper", "Front Bumper Absorber").update(new=False),
        "new: true on a canonical part": lambda m: member_of(m, "front-bumper", "Front Bumper").update(new=True),
        "fitted differing between assemblies": lambda m: member_of(m, "rear-end", "Rear End Panel").update(fitted="if_fitted"),
        "a sign that isn't a question": lambda m: asm_of(m, "boot-lid")["signs"][0].update(ask="The lid is open."),
        "an unknown sign level": lambda m: asm_of(m, "boot-lid")["signs"][0].update(level="severe"),
        "an unknown key (a typo)": lambda m: member_of(m, "boot-lid", "Boot Lid Lock").update({"strip-down": True}),
        "format 2": lambda m: m.update(format=2),
        "a version with a colon": lambda m: m.update(version="K1:0"),
        "an anchor that is no member": lambda m: asm_of(m, "boot-lid")["anchors"].append("Roof"),
        "a diagram number with a letter": lambda m: asm_of(m, "front-bumper")["catalogue"]["oem_numbers"].update(
            toyota=["52O1"]),
        "a trigger that isn't a firm word": lambda m: rel_of(m, "boot-lid>lock").update(trigger_tokens=["Smashed"]),
        "the same pair twice": lambda m: m["relations"].append({**copy.deepcopy(rel_of(m, "front-bumper>beam")),
                                                                "id": "front-bumper>beam-again"}),
        "a part linked to itself": lambda m: rel_of(m, "front-bumper>grille").update(child="Front Bumper"),
        "a part twice in one assembly": lambda m: asm_of(m, "rear-end")["members"].append(
            {"part": "Rear Floor Panel", "role": "structure"}),
    }
    for name, change in cases.items():
        found = broken(change)
        check(found, f"refused: {name}", found[:1])
    check(any("cycle" in e for e in broken(add_cycle)) and any("hops" in e for e in broken(add_chain_of_chains)),
          "the cycle and the 3-hop chain are named as such")

    # Total: junk anywhere is a line, never an exception.
    junk = [None, 0, 1, True, "x", "", [], {}, [None], {"a": 1}, [[]], 1.5]
    paths: list[tuple] = []

    def walk(node, path=()):
        paths.append(path)
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, path + (k,))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + (i,))

    walk(EXAMPLE)
    crashed = []
    for path in paths:
        for value in junk:
            bad = copy.deepcopy(EXAMPLE)
            if not path:
                bad = value
            else:
                node = bad
                for step in path[:-1]:
                    node = node[step]
                node[path[-1]] = value
            try:
                out = car_map.validate(bad, names=NEW)
                if not isinstance(out, list):
                    crashed.append((path, value, "not a list"))
            except Exception as exc:  # noqa: BLE001
                crashed.append((path, value, type(exc).__name__))
    check(not crashed, f"total: {len(paths) * len(junk)} junk values at every path give lines, never an exception",
          crashed[:3])
    doc = car_map.load_new_names()
    name_crashes = []
    for n in range(len(doc["names"])):
        for key in list(doc["names"][n]):
            for value in junk:
                bad = copy.deepcopy(doc)
                bad["names"][n][key] = value
                try:
                    car_map.validate_new_names(bad)
                except Exception as exc:  # noqa: BLE001
                    name_crashes.append((n, key, value, type(exc).__name__))
    check(not name_crashes, "validate_new_names is total too", name_crashes[:3])
    rng = random.Random(7)
    shuffled = 0
    for _ in range(200):
        bad = copy.deepcopy(EXAMPLE)
        rng.shuffle(bad["relations"])
        rng.shuffle(bad["assemblies"])
        shuffled += car_map.validate(bad, names=NEW) == []
    check(shuffled == 200, "order of assemblies and relations doesn't matter to the check")


# ---------------------------------------------------------------- load, sha


def load_sha() -> None:
    folder = Path(_tmp, "maps")
    folder.mkdir()
    good = folder / "good.json"
    good.write_text(json.dumps(EXAMPLE), encoding="utf-8")
    loaded = car_map.load(good)
    check(loaded == EXAMPLE, "load: a good map")
    (folder / "broken.json").write_text("{not json", encoding="utf-8")
    (folder / "list.json").write_text("[]", encoding="utf-8")
    bad = copy.deepcopy(EXAMPLE)
    rel_of(bad, "front-bumper>beam")["side"] = "same"
    (folder / "invalid.json").write_text(json.dumps(bad), encoding="utf-8")
    (folder / "binary.json").write_bytes(b"\xff\xfe\x00\x81")
    for name in ("missing.json", "broken.json", "list.json", "invalid.json", "binary.json"):
        check(car_map.load(folder / name) is None, f"load: {name} -> None, no exception")
    check(car_map.load(folder) is None, "load: a folder -> None")
    lf, crlf = folder / "lf.json", folder / "crlf.json"
    lf.write_bytes(b'{"a": 1,\n "b": 2}\n')
    crlf.write_bytes(b'{"a": 1,\r\n "b": 2}\r\n')
    check(car_map.sha256_lf(lf) == car_map.sha256_lf(crlf) and len(car_map.sha256_lf(lf)) == 64,
          "sha256_lf: CRLF and LF copies have one sha (git's copy, for the freeze)")
    check(car_map.sha256_lf(folder / "missing.json") is None, "sha256_lf: no file -> None")
    check(car_map.MAP is None or car_map.validate(car_map.MAP) == [],
          "MAP: none yet (K1b writes car_map.json), or a valid one")


# ---------------------------------------------------------------- L1 as a car map


def from_l1() -> None:
    before = copy.deepcopy(L1)
    cm = car_map.from_l1(L1)
    check(L1 == before, "from_l1 doesn't change the table")
    check(car_map.validate(cm) == [], "L1 as a car map validates", car_map.validate(cm)[:3])
    parents = list(dict.fromkeys(link["parent"] for link in L1["links"]))
    check(len(cm["assemblies"]) == len(parents) == 23 and [a["anchors"] for a in cm["assemblies"]] == [[p] for p in parents],
          "one assembly per L1 parent (23), in table order, the parent its anchor")
    check([r["id"] for r in cm["relations"]] == [l["id"] for l in L1["links"]] and len(cm["relations"]) == 95,
          "95 relations, L1's ids in L1's order")
    check(all({k: v for k, v in r.items() if k != "assembly"} == l for r, l in zip(cm["relations"], L1["links"])),
          "each relation is its link, field for field, plus its assembly")
    table = car_map.links_table(cm)
    check(table == L1, "links_table(from_l1(L1)) is L1 again: version, built_from, every link and field, in order")
    check(links.validate_table(table) == [], "and passes links.validate_table")
    check(all(car_map.links_table(cm, body_type=b) == L1 for b in BODY_TYPES + [None]),
          "L1 has no gates: the same table for every body type")
    path = Path(_tmp, "l1-map.json")
    path.write_text(json.dumps(cm, indent=1), encoding="utf-8")
    again = car_map.load(path)
    check(again is not None and car_map.links_table(again) == L1, "through a file and load(): L1 again")
    check(car_map.relation_info(cm, "L1:rear-bumper>beam") == {
        "assembly": "rear-bumper", "assembly_name": "Rear Bumper (L1)", "prior": None, "position": "any",
        "fitted": "always", "hop": 1}, "relation_info on L1: its parent's assembly, no prior")
    check(car_map.relation_info(cm, "K9:rear-bumper>beam") is None and car_map.relation_info(cm, "L1:nope") is None
          and car_map.relation_info(None, "L1:rear-bumper>beam") is None, "relation_info: another version or id -> None")


# ---------------------------------------------------------------- the equivalence


CONVERTED = car_map.links_table(car_map.from_l1(L1))


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str) and a == b


def equivalence_fixtures() -> None:
    """Every links.build / build_all / for_parent call test_links.py makes,
    replayed on the converted map first (on copies), then on L1 (what the
    test sees and checks)."""
    check(CONVERTED is not L1 and CONVERTED == L1, "the converted table is a separate object, equal to L1")
    compared, mismatches = [0], []
    real_build_all, real_for_parent = links.build_all, links.for_parent

    def twin_build_all(listed, zone_suggestions, photos, zone_intact, table, opts, **kw):
        if table is not L1:
            return real_build_all(listed, zone_suggestions, photos, zone_intact, table, opts, **kw)
        args = copy.deepcopy((listed, zone_suggestions, photos, zone_intact, kw))
        mine = real_build_all(*args[:4], CONVERTED, opts, **args[4])
        theirs = real_build_all(listed, zone_suggestions, photos, zone_intact, table, opts, **kw)
        compared[0] += 1
        if not _same(mine, theirs):
            mismatches.append(("build_all", [it.get("part") for it in listed if isinstance(it, dict)]))
        return theirs

    def twin_for_parent(it, data, photos, table, opts, **kw):
        if table is not L1:
            return real_for_parent(it, data, photos, table, opts, **kw)
        args = copy.deepcopy((it, data, photos, kw))
        mine = real_for_parent(args[0], args[1], args[2], CONVERTED, opts, **args[3])
        theirs = real_for_parent(it, data, photos, table, opts, **kw)
        compared[0] += 1
        if not _same(mine, theirs):
            mismatches.append(("for_parent", it.get("part") if isinstance(it, dict) else it))
        return theirs

    sections = (TL.table, TL.hit_levels, TL.gate, TL.sides, TL.skip_join, TL.intact, TL.buckets_order,
                TL.evidence_reasons, TL.relink)
    before = (TL.PASSED[0], len(TL.FAILURES))
    links.build_all, links.for_parent = twin_build_all, twin_for_parent
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            for fn in sections:
                TL.run_section(fn.__name__, fn)
    finally:
        links.build_all, links.for_parent = real_build_all, real_for_parent
    passed, failed = TL.PASSED[0] - before[0], len(TL.FAILURES) - before[1]
    check(failed == 0 and passed >= 100, f"test_links.py's own checks all pass with the replay in ({passed} passed)",
          TL.FAILURES[before[1]:][:3])
    check(compared[0] >= 60, f"{compared[0]} of its build calls were replayed on the converted map")
    check(not mismatches, "every one gave the same output (links, buckets, sides, order, evidence, reasons)",
          mismatches[:3])


def equivalence_sweep() -> None:
    sided = sorted({name for link in L1["links"] for name in car_map.expand(link["parent"]) if "{side}" in link["parent"]})
    plain = sorted({link["parent"] for link in L1["links"] if "{side}" not in link["parent"]})
    undetermined = sorted({unsided_part(n) for n in sided if unsided_part(n)})
    parents = plain + sided + undetermined
    tokens = [(), ("Grazed",), ("Dented",), ("Shifted",), ("Torn",), ("Cracked",), ("Jammed",), ("Deformed",),
              ("Bent",), ("Crumpled",)]
    extras = [{}, {"ai_confidence": "Low"}, {"side_inferred": True}, {"seen_in": ["@a", "@b"]}]
    variants = [ON, links.Options(mode="on", side="strict"), links.Options(mode="on", unknown_hit="light"),
                links.Options(mode="on", intact="drop"), links.Options(mode="on", types=("attached", "behind")),
                links.Options(mode="on", max_items=3), links.Options(mode="on", catalogue=True)]
    runs, differ, nonempty = 0, [], 0
    for part in parents:
        for words in tokens:
            for extra in extras:
                listed = [item(part, *words, photo="@p1", **extra)]
                a = links.build(copy.deepcopy(listed), [], [], {}, CONVERTED, ON)
                b = links.build(listed, [], [], {}, L1, ON)
                runs += 1
                nonempty += bool(b)
                if not _same(a, b):
                    differ.append((part, words, extra))
    check(len(parents) >= 40 and runs == len(parents) * len(tokens) * len(extras),
          f"one part: {len(parents)} parents (both sides and undetermined) x {len(tokens)} words x {len(extras)} reads "
          f"= {runs} builds ({nonempty} with links)")
    check(not differ and nonempty > runs // 3, "every one the same on the converted map", differ[:3])

    rng = random.Random(18)
    pool = plain + sided
    photos = [photo("p1", "Rear Bumper"), photo("p2", "Rear Bumper Beam"), photo("p3", "Left Fog Lamp", area="front"),
              photo("p4", "Boot Lid Lock", excluded=True), photo("u1", area="underbody"),
              SimpleNamespace(n=5, kind="damage", area="rear", damage=True, part="Parking Sensor", excluded=False,
                              quality="good", id=None)]
    intact = {"zone:rear": ["Parking Sensor", "Left Tail Lamp", "Right Tail Lamp"], "zone:front": ["Front Grille"]}
    neighbours = {"Rear Bumper": [{"part": "Rear Bumper Beam", "diagram": "BUMPERS"},
                                  {"part": "Right Rear Wheel Arch Trim", "diagram": "BUMPERS"}],
                  "Boot Lid": [{"part": "Boot Lid Hinge", "diagram": "TRUNK LID"}]}
    multi, differ = 0, []
    for _ in range(400):
        listed = [item(p, *rng.choice(tokens), photo=rng.choice([None, "@p1", "@p9"]), **rng.choice(extras))
                  for p in rng.sample(pool, rng.randint(2, 5))]
        others = [{"key": "Left Rear Bumper Reflector", "part": "Left Rear Bumper Reflector", "source": "zone"}] \
            if rng.random() < 0.3 else []
        dismissed = {"Boot Lid Weatherstrip", "Parking Sensor"} if rng.random() < 0.3 else ()
        opts = rng.choice(variants)
        kw = {"dismissed": dismissed, "neighbours": neighbours}
        pics = photos if rng.random() < 0.5 else []
        seen = intact if rng.random() < 0.5 else {}
        a = links.build_all(copy.deepcopy(listed), copy.deepcopy(others), pics, seen, CONVERTED, opts, **kw)
        b = links.build_all(listed, others, pics, seen, L1, opts, **kw)
        multi += 1
        if not _same(a, b):
            differ.append([it["part"] for it in listed])
        if listed:
            data = {"damage_summary": listed[:-1], "second_look": {"items": others, "dismissed": list(dismissed)}}
            fa = links.for_parent(copy.deepcopy(listed[-1]), copy.deepcopy(data), pics, CONVERTED, opts, zone_intact=seen)
            fb = links.for_parent(listed[-1], data, pics, L1, opts, zone_intact=seen)
            multi += 1
            if not _same(fa, fb):
                differ.append(["for_parent", listed[-1]["part"]])
    check(multi == 800 and not differ,
          f"{multi} multi-part builds and re-links (photos, intact, dismissed, zone twins, catalogue, all knobs): "
          "the same", differ[:3])


# ---------------------------------------------------------------- compiling a map


def compiling() -> None:
    table = car_map.links_table(EXAMPLE)
    ids = [l["id"] for l in table["links"]]
    dropped = {r["id"] for r in EXAMPLE["relations"]} - set(ids)
    check(dropped == {"front-bumper>fog-lamp-cover", "front-bumper>sensor", "front-bumper>bracket",
                      "front-bumper>absorber", "boot-lid>striker", "boot-lid>strut", "boot-lid>plate-lamp",
                      "boot-lid>wiper-motor"}, "names off: every relation naming a new part is left out", sorted(dropped))
    check(ids == [r["id"] for r in EXAMPLE["relations"] if r["id"] not in dropped], "the rest in map order")
    check(links.validate_table(table) == [], "the compiled table passes links.validate_table",
          links.validate_table(table)[:2])
    check(table["version"] == "EX1" and all(set(l) == set(car_map.LINK_KEYS) for l in table["links"]),
          "the map's version; L1's fields only")
    beam = next(l for l in table["links"] if l["id"] == "front-bumper>beam")
    check(beam["hidden"] is True and beam["strip_down"] is True and beam["area"] == "front",
          "hidden / strip_down from the child's member, area from the assembly, when the relation doesn't say")
    under = next(l for l in table["links"] if l["id"] == "front-bumper>undercover")
    check(under["hidden"] is True and under["strip_down"] is False, "the relation's own flag wins over the member's")
    glass = "boot-lid>rear-windscreen"
    by_body = {b: glass in {l["id"] for l in car_map.links_table(EXAMPLE, body_type=b)["links"]}
               for b in ("Sedan", "Coupe", "Hatchback", "SUV", "MPV", "Wagon", "Other", None)}
    check(by_body == {"Sedan": False, "Coupe": False, "Hatchback": True, "SUV": True, "MPV": True, "Wagon": True,
                      "Other": True, None: True},
          "body gate: the tailgate glass link goes on a sedan or coupe, stays on a hatch body or an unknown one", by_body)
    named = car_map.links_table(EXAMPLE, names=True)
    check(len(named["links"]) == len(EXAMPLE["relations"]) and links.validate_table(named),
          "names on: every relation kept -- and links.validate_table refuses them until the taxonomy has them")
    motor = "boot-lid>wiper-motor"
    check(motor not in {l["id"] for l in car_map.links_table(EXAMPLE, body_type="Sedan", names=True)["links"]}
          and motor in {l["id"] for l in car_map.links_table(EXAMPLE, body_type="Hatchback", names=True)["links"]},
          "a member's body gate (the rear wiper motor: hatch only) works like a relation's")
    found = links.build([item("Front Bumper", "Deformed", photo="@p1")], [], [], {}, table, ON)
    rules = {it["link_rule"] for it in found}
    check("EX1:front-bumper>beam" in rules and all(r.startswith("EX1:") for r in rules),
          "links.build on it: link_rule carries the map's version", sorted(rules))
    info = car_map.relation_info(EXAMPLE, "EX1:front-bumper>fog-lamp")
    check(info == {"assembly": "front-bumper", "assembly_name": "Front bumper assembly", "prior": 2,
                   "position": "corner", "fitted": "if_fitted", "hop": 1},
          "relation_info: assembly, prior, position and the child's \"if fitted\"", info)
    check(car_map.relation_info(EXAMPLE, "EX1:boot-lid>lock")["fitted"] == "always", "an always-fitted child")


# ---------------------------------------------------------------- the seam


def seam() -> None:
    check(config.Settings.model_fields["intake_car_map"].default == "off" and config.settings.intake_car_map == "off",
          "intake_car_map is \"off\" by default")
    check(config.normal_car_map_mode(" Shadow ") == "shadow" and config.normal_car_map_mode("") == "off"
          and config.normal_car_map_mode(None) == "off" and config.CAR_MAP_MODES == ("off", "shadow", "on"),
          "written the one way; blank is off")
    try:
        config.normal_car_map_mode("maybe")
        refused = False
    except ValueError:
        refused = True
    check(refused, "anything else stops the app at start-up (ValueError)")
    check(config.Settings(intake_car_map="ON").intake_car_map == "on", "Settings reads it")
    shown, shadow = car_map.table_for_links(L1, car_map=EXAMPLE)
    check(shown is L1 and shadow is None, "off (the setting's default): L1 itself, nothing else -- today, untouched")
    check(car_map.table_for_links(L1, mode="off", car_map=EXAMPLE) == (L1, None), "off with a map: still L1 alone")
    shown, shadow = car_map.table_for_links(L1, mode="shadow", car_map=EXAMPLE)
    check(shown is L1 and shadow == car_map.links_table(EXAMPLE), "shadow: L1 shown, the map's table beside it")
    shown, shadow = car_map.table_for_links(L1, mode="on", car_map=EXAMPLE, body_type="Sedan")
    check(shown == car_map.links_table(EXAMPLE, body_type="Sedan") and shadow is None, "on: the map's table, gated")
    check(car_map.table_for_links(L1, mode="on", car_map=None) == (L1, None)
          and car_map.table_for_links(L1, mode="shadow", car_map=None) == (L1, None), "no map: L1")
    check(car_map.table_for_links(L1, mode="on", car_map=EXAMPLE, names=True) == (L1, None),
          "a map whose table fails links' check (new names not applied): L1")
    check(car_map.table_for_links(L1, mode="on", car_map={"relations": [{}]}) == (L1, None),
          "a broken map object: L1, no exception")
    on = car_map.table_for_links(L1, mode="on", car_map=car_map.from_l1(L1))[0]
    check(on == L1, "on with L1 as a car map: L1's table")


# ---------------------------------------------------------------- purity


def purity() -> None:
    check(AI_CLIENT_IMPORTED == [], "car_map imports no AI client", AI_CLIENT_IMPORTED)
    check("car_map" not in Path(links.__file__).read_text(encoding="utf-8"),
          "links never imports car_map (no cycle once the seam is wired)")
    before = copy.deepcopy(EXAMPLE)
    car_map.validate(EXAMPLE, names=NEW)
    car_map.links_table(EXAMPLE, body_type="Sedan")
    car_map.from_l1(L1)
    check(EXAMPLE == before, "validate and links_table change nothing")
    check(car_map.links_table(EXAMPLE) == car_map.links_table(EXAMPLE), "deterministic")
    check(car_map.HIT_AT == ("centre", "corner", "whole", "cannot_see") and "left" not in " ".join(car_map.HIT_AT),
          "the walk's hit_at never names a side")


def main() -> int:
    for name, fn in (
        ("the new names (option a)", new_names),
        ("the schema file", schema_file),
        ("the validator (worked example, K1b rewrites)", validator),
        ("load and sha", load_sha),
        ("L1 as a car map", from_l1),
        ("the same output as L1: test_links.py's fixtures", equivalence_fixtures),
        ("the same output as L1: a sweep", equivalence_sweep),
        ("compiling a map: gates and new names", compiling),
        ("the seam: intake_car_map", seam),
        ("purity", purity),
    ):
        run_section(name, fn)
    print(f"\ntest_car_map: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAILED [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
