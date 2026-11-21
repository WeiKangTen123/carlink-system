"""The car map's own part names (plans/2026-11-18-car-knowledge/K1-design.md
section 2, owner option a): taxonomy.CAR_MAP_PARTS behind
settings.intake_car_map_names (app/reports/taxonomy.py, app/config.py,
app/intake/car_map.py, app/intake/links.py, app/ai/second_look.py,
app/parts/history.py, app/parts/prefill.py, app/api/main.py).

    python scripts/test_carmap_names.py              # every check
    python scripts/test_carmap_names.py --snapshot   # the names-off digests only

1. Names off is today, byte for byte: the Gemini prompts and schemas, the
   linked parts (L1 and the car map's table), the second look's candidates,
   the side names and the Annex A wording on fixed fixtures hash to what the
   branch base (f8d6014) gave -- BASE below, printed there by --snapshot,
   which uses nothing the base lacks. The prompts and schemas hash the same
   with the names on too: the new names never reach a read's enum.
2. Names on: the car map's table keeps the relations naming a new part,
   links.build suggests them (sides as links.py gives them: a firm side, a
   2:1 impact majority, else the "(side undetermined)" twin), the body gate
   reads the make/model only, the linked check takes them, Annex A words
   them the firm's way (sedan and tailgate).
3. A part a person added stays valid and printable when the names go off:
   the API takes it, Annex A prints it, and it never keys as its coarse
   family (a listed "Front Bumper" doesn't swallow an absorber).

Offline: no Gemini call (fakes only). The API part runs through TestClient
with scripts/test_annexes.py's temporary database, no example_case folder.
"""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="carmap-names-test-")
os.environ.setdefault("STORAGE_DIR", _tmp)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'test.db').as_posix()}")
os.environ.setdefault("GEMINI_API_KEY", "fake")

SNAPSHOT_ONLY = "--snapshot" in sys.argv
sys.argv = sys.argv[:1]

# The names-off digests at the branch base (f8d6014, K1b), from
# `python scripts/test_carmap_names.py --snapshot` run there. A section that
# changes on purpose later is re-taken the same way, with a note why.
BASE = {
    "taxonomy": "6dc255bd286b29c88b1fb1d1c2b151748fd7463487fea1c326bed0ac139b6c94",
    "schemas": "8e5a2888e19aa037bbbde4062f374b4201e06d2a738b22273ab792869377812d",
    "links_l1": "fb90c1e2e83c2f11895efd589c3d6e2f0afbd2fc936483ab1552f8bbce2f53f5",
    "links_map": "4879bccd6f52cedde21eb1e66b3f2a771b8720ae4063bad1bb359ab962c8e77e",
    "second_look": "1c93ee8028bab2f9bf43632af0c4374f583cf0b27dacddaf700b6aaa4f58ca7a",
    "annex": "a01332ede9f6f891523b71b338b38fed2b3a5c4cb06a7b69ad95fd8211bc4f6f",
}

FAILED: list[str] = []


def ok(label: str) -> None:
    print(f"    ok   {label}")


def check(cond, label: str, why="") -> None:
    if cond:
        ok(label)
    else:
        FAILED.append(label)
        print(f"    FAIL {label}" + (f": {why}" if why else ""))


def digest(value) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------ 1. the snapshot
#
# Only what the branch base has: --snapshot runs this there.

BODIES = (None, "Sedan", "Hatchback", "SUV", "Pickup", "Other")
VEHICLES = (
    {"make": "Alphamotors", "model": "Saloon"},
    {"make": "Alphamotors", "model": "Saloon", "body_type": "Sedan"},
    {"make": "Toyota", "model": "Yaris Cross"},
    {"make": "Toyota", "model": "Hilux"},
)
# Listed parts as a read gives them: words, a type, a severity, a remark.
FIXTURES = (
    [{"part": "Front Bumper", "condition_tokens": ["Buckled", "Cut"], "remarks": "Replace", "photo_reference": "@p1"},
     {"part": "Left Headlamp", "condition_tokens": ["Cracked"], "ai_confidence": "High", "photo_reference": "@p1"},
     {"part": "Left Front Fender", "condition_tokens": ["Dented"], "seen_in": ["@p1", "@p2"]},
     {"part": "Bonnet", "damage_type": "Deformation", "severity": "Severe"}],
    [{"part": "Rear Bumper", "condition_tokens": ["Crumpled"], "photo_reference": "@p3"},
     {"part": "Boot Lid", "condition_tokens": ["Bent"], "remarks": "Replace"},
     {"part": "Right Tail Lamp", "condition_tokens": ["Cracked"]},
     {"part": "Rear End Panel", "condition_tokens": ["Buckled"]}],
    [{"part": "Left Front Door", "condition_tokens": ["Bent", "Jammed"]},
     {"part": "Left Rear Door", "condition_tokens": ["Dented"], "side_inferred": True},
     {"part": "Sill / Rocker Panel (side undetermined)", "condition_tokens": ["Deformed"]}],
    [{"part": "Door (side undetermined)", "condition_tokens": ["Buckled"]},
     {"part": "Tail Lamp (side undetermined)", "condition_tokens": ["Cracked"]},
     {"part": "Roof", "condition_tokens": ["Crumpled"]}],
    [{"part": "Front Bumper", "condition_tokens": ["Grazed"]},
     {"part": "Rear Bumper", "condition_tokens": ["Dented"], "severity": "Moderate"},
     {"part": "Other / Not Listed", "other_part_name": "Rear air vent", "condition_tokens": ["Cut"]}],
)
PHOTOS = (
    {"id": "p1", "kind": "damage", "area": "front-left", "part": "Front Bumper", "shows_damage": True},
    {"id": "p2", "kind": "damage", "area": "front", "part": "Fog Lamp (side undetermined)", "shows_damage": True},
    {"id": "p3", "kind": "damage", "area": "rear", "part": "Rear Bumper Beam", "shows_damage": True},
    {"id": "p4", "kind": "overview", "area": "rear-right", "part": None, "shows_damage": False},
)


def _schemas() -> dict:
    from app.ai import check as recheck
    from app.ai import extraction, link_check, second_look, triage, zone_read

    out: dict = {"triage": [[triage.system_prompt(box), triage.response_schema(box)] for box in (False, True)]}
    out["extraction"] = [extraction.SYSTEM_PROMPT, extraction._response_schema()]
    out["zone_read"] = {
        f"{zone}/{grid}/{mode}": [zone_read.system_prompt(zone, grid, mode),
                                  zone_read.response_schema(zone, ["P01", "P02"], grid, mode),
                                  zone_read.checklist(zone)]
        for zone in zone_read.ZONE_NAMES for grid in (False, True)
        for mode in (zone_read.EVIDENCE_OFF, *zone_read.EVIDENCE_SENTENCE)
    }
    out["second_look"] = second_look.response_schema(second_look.ZONES["rear"] + second_look.ZONES["front"])
    out["link_check"] = [link_check.SYSTEM_PROMPT, link_check.response_schema(["C1", "C2"], ["P01"])] + [
        link_check.system_prompt(link_check.Group(area=area, skill=skill)) for area, skill in link_check.SKILL_OF.items()]
    out["recheck"] = [recheck.LITE_PROMPT, recheck.lite_schema(["S1"], ["P01"])]
    return out


def _taxonomy() -> dict:
    from app.reports import taxonomy as t

    canonical = list(t.CANONICAL_PARTS)
    return {
        "parts": canonical,
        "firm": {p: t.FIRM_PART_NAMES.get(p) for p in canonical},
        "firm_hatch": {p: t.FIRM_PART_NAMES_HATCH.get(p) for p in canonical},
        "floor": {f"{p}|{b}|{u}": t.firm_part_name(p, b, unsided=u) for p in canonical for b in BODIES for u in (False, True)},
        "unsided": {p: t.unsided_part(p) for p in canonical},
        "sided": {f"{p}|{s}|{e}": t.sided_part(p, s, e) for p in canonical if p.endswith(t.UNDETERMINED_SUFFIX)
                  for s in ("Left", "Right", None) for e in ("Front", "Rear", None)},
        "split": {p: list(t.split_side(p)) for p in canonical},
    }


def _links_l1() -> dict:
    from app.intake import links

    opts = links.Options(mode="on")
    out = {"valid": links.validate_table(links.TABLE),
           "covers": {p: links.covers(p) for p in links.CANONICAL_PARTS},
           "end": {p: links.end_of(p) for p in links.CANONICAL_PARTS}}
    for n, listed in enumerate(FIXTURES):
        for strict in (False, True):
            o = links.Options(mode="on", side="strict" if strict else "firm")
            out[f"{n}/{strict}"] = links.build_all([dict(it) for it in listed], [], list(PHOTOS), {}, links.TABLE, o)
    out["for_parent"] = links.for_parent({"part": "Rear End Panel", "condition_tokens": ["Buckled"]},
                                         {"damage_summary": [dict(it) for it in FIXTURES[1]]}, [], links.TABLE, opts)
    return out


def _links_map() -> dict:
    from app.intake import car_map, links

    opts = links.Options(mode="on")
    out = {}
    for body in BODIES:
        table = car_map.links_table(car_map.MAP, body_type=body)
        out[f"table/{body}"] = table
        for n, listed in enumerate(FIXTURES):
            out[f"{body}/{n}"] = links.build_all([dict(it) for it in listed], [], list(PHOTOS), {}, table, opts)
    for mode in ("off", "shadow", "on"):
        shown, shadow = car_map.table_for_links(links.TABLE, mode=mode)
        out[f"seam/{mode}"] = [shown is links.TABLE, shown, shadow]
    return out


def _second_look() -> dict:
    from app.ai import second_look

    out = {}
    for n, listed in enumerate(FIXTURES):
        names = {it["part"] for it in listed}
        cands, scope = second_look.candidates(None, make=None, model=None, listed=names, dismissed=set(),
                                              sorter_parts=["Left Fog Lamp", "Rear Bumper Beam", "Left Front Door Glass"])
        out[str(n)] = [[c.part, c.source, c.reason, c.hints] for c in cands] + [scope]
        out[f"listed/{n}"] = sorted(second_look.listed_parts({"damage_summary": listed}))
    parts = list(second_look.CANONICAL_PARTS)
    out["covers"] = {p: second_look.covers_of(p) for p in parts}
    out["other_side"] = {p: second_look.other_side_listed(p, set(parts)) for p in parts}
    return out


def _annex() -> dict:
    from app.parts.history import canonical_part
    from app.parts.prefill import item_key, item_row_name, row_name, sync_annex_a
    from app.reports.synthesize_annexes import synthesize_annexes_for_report
    from app.reports.taxonomy import CANONICAL_PARTS

    out = {
        "row_name": {f"{p}|{n}|{u}": row_name(None, p, v, unsided=u) for p in CANONICAL_PARTS
                     for n, v in enumerate(VEHICLES) for u in (False, True)},
        "item_key": {p: item_key({"part": p}) for p in CANONICAL_PARTS},
        "canonical_part": {p: canonical_part(p) for p in CANONICAL_PARTS + [
            "Tail gate lock striker", "Front bumper absorber", "Rear bumper side bracket LH", "Door lock RH",
            "Bonnet hinge", "Radiator fan assy", "Fuel filler lid"]},
    }
    for n, listed in enumerate(FIXTURES):
        for m, vehicle in enumerate(VEHICLES):
            data = {"vehicle_info": dict(vehicle), "damage_summary": [dict(it) for it in listed]}
            out[f"sync/{n}/{m}"] = sync_annex_a(None, data, price_book=False)["annex_a"]
            out[f"scaffold/{n}/{m}"] = synthesize_annexes_for_report(dict(data))
            out[f"item_row/{n}/{m}"] = [item_row_name(it, vehicle, unsided=u) for it in listed for u in (False, True)]
    return out


SECTIONS = {
    "taxonomy": _taxonomy,
    "schemas": _schemas,
    "links_l1": _links_l1,
    "links_map": _links_map,
    "second_look": _second_look,
    "annex": _annex,
}


def snapshot() -> dict[str, str]:
    return {name: digest(build()) for name, build in SECTIONS.items()}


def check_names_off_is_today() -> None:
    print("names off: today's prompts, schemas, links and Annex A, byte for byte (branch base f8d6014)")
    from app.config import settings

    assert settings.intake_car_map_names == "off", "the default is off"
    got = snapshot()
    for name, want in BASE.items():
        check(got[name] == want, f"{name}: as at the branch base", f"{got[name]} != {want}")
    settings.intake_car_map_names = "on"
    try:
        on = digest(_schemas())
        check(on == BASE["schemas"], "schemas: the same with the names on (no new name in any read's prompt or enum)",
              f"{on} != {BASE['schemas']}")
    finally:
        settings.intake_car_map_names = "off"


# ------------------------------------------------------------ 2. the names


def _new_names() -> tuple[list[dict], list[str], list[str]]:
    """(N1's entries, their names {side} expanded, their twins)."""
    from app.intake import car_map

    doc = car_map.load_new_names()
    assert doc is not None, "car_map_new_names.json loads"
    entries = doc["names"]
    expanded = [full for e in entries for full in car_map.expand(e["name"])]
    twins = list(dict.fromkeys(e["twin"] for e in entries if e["twin"]))
    return entries, expanded, twins


def check_setting() -> None:
    print("the setting: intake_car_map_names off | on, off by default")
    from app.config import CAR_MAP_NAMES, Settings, normal_car_map_names, settings

    check(CAR_MAP_NAMES == ("off", "on"), "off, on")
    check(Settings.model_fields["intake_car_map_names"].default == "off" and settings.intake_car_map_names == "off",
          "default off")
    check((normal_car_map_names(None), normal_car_map_names(" ON "), normal_car_map_names("")) == ("off", "on", "off"),
          "written one way; blank is off")
    try:
        normal_car_map_names("yes")
        check(False, "a typo stops the app")
    except ValueError:
        ok("a typo stops the app (ValueError)")


def check_taxonomy_names() -> None:
    print("taxonomy: CAR_MAP_PARTS is N1, apart from CANONICAL_PARTS, worded, twinned")
    from app.reports import taxonomy as t

    entries, expanded, twins = _new_names()
    check(t.CAR_MAP_PARTS == expanded + twins, f"CAR_MAP_PARTS is N1's {len(expanded)} names then its {len(twins)} twins",
          str(set(t.CAR_MAP_PARTS) ^ set(expanded + twins)))
    check(len(expanded) == 37 and len(twins) == 8 and len({e['name'] for e in entries}) == 26, "26 entries, 37 names, 8 twins")
    check(not set(t.CAR_MAP_PARTS) & set(t.CANONICAL_PARTS), "none is a canonical part")
    check(t.KNOWN_PARTS == t.CANONICAL_PARTS + t.CAR_MAP_PARTS and len(t.CANONICAL_PARTS) == 102,
          "KNOWN_PARTS = CANONICAL_PARTS (102, untouched) + CAR_MAP_PARTS")
    check(all(t.known_part(p) for p in t.KNOWN_PARTS) and not t.known_part("Front bumper absorber")
          and not t.known_part(None) and not t.known_part(["Front Bumper"]), "known_part: exact names only")

    def door_twin(word: str) -> str:
        """A door twin's wording: its door's without the end ("Front door
        lock" -> "Door lock")."""
        word = word.removeprefix("Front ").removeprefix("Rear ")
        return word[0].upper() + word[1:]

    wrong = []
    for e in entries:
        for full in [*expand_name(e["name"]), *([e["twin"]] if e["twin"] else [])]:
            _, side = t.split_side(full)
            code = f" {t.SIDE_CODES[side]}" if side else ""
            sedan, hatch = e["firm"], e["firm_hatch"] or e["firm"]
            if full == e["twin"] and full.startswith("Door "):
                sedan, hatch = door_twin(sedan), door_twin(hatch)
            want = (sedan + code, hatch + code, sedan + code)
            got = (t.firm_part_name(full, "Sedan"), t.firm_part_name(full, "SUV"), t.firm_part_name(full))
            if got != want:
                wrong.append((full, got, want))
    check(not wrong, "every name and twin has N1's firm wording (tailgate wording on a hatch), side code last", str(wrong[:3]))
    printed = {w for names in (t.FIRM_PART_NAMES, t.FIRM_PART_NAMES_HATCH) for w in names.values()}
    check(not printed & set(t.KNOWN_PARTS), "no wording is itself a part name")
    check(t.firm_part_name("Left Front Door Lock", unsided=True) == "Front door lock"
          and t.firm_part_name("Door Lock (side undetermined)") == "Door lock"
          and t.firm_part_name("Right Boot Lid Strut", "Wagon") == "Tail gate gas stay RH"
          and t.firm_part_name("Rear Parking Sensor") == "Reverse sensor", "examples: unsided, twin, tailgate, sensor")

    bad_twins = [(f, t.unsided_part(f), e["twin"]) for e in entries if e["sided"]
                 for f in expand_name(e["name"]) if t.unsided_part(f) != e["twin"]]
    check(not bad_twins, "unsided_part gives each sided name its N1 twin", str(bad_twins[:3]))
    placed = {(tw, s): t.sided_part(tw, s) for tw in twins for s in ("Left", "Right")}
    doors = {tw for tw in twins if tw.startswith("Door ")}
    check(all(placed[(tw, s)] is None for tw in doors for s in ("Left", "Right")),
          "a door twin is never placed (a side alone doesn't say which door)")
    check(all(placed[(tw, s)] == f"{s} {tw.removesuffix(t.UNDETERMINED_SUFFIX)}" for tw in twins if tw not in doors
              for s in ("Left", "Right")), "the other twins are placed on a side (bumper brackets, fog lamp covers, bonnet hinges, struts)")
    check(t.sided_part("Door Lock (side undetermined)", "Left", "Front") is None, "not even with an end")


def expand_name(name: str) -> list[str]:
    from app.intake import car_map

    return car_map.expand(name)


def check_covers() -> None:
    print("covers: a twin covers its sided names (links and the second look agree)")
    from app.ai import second_look
    from app.intake import links

    want = ["Left Front Door Lock", "Right Front Door Lock", "Left Rear Door Lock", "Right Rear Door Lock",
            "Door Lock (side undetermined)"]
    check(links.covers("Door Lock (side undetermined)") == want == second_look.covers_of("Door Lock (side undetermined)"),
          "Door Lock (side undetermined) covers all four door locks (and itself, as a canonical twin does)")
    check(links.covers("Tail Lamp (side undetermined)") == ["Left Tail Lamp", "Right Tail Lamp", "Tail Lamp (side undetermined)"],
          "a canonical twin's covers as before")
    check(links.covers("Left Front Bumper Bracket") == ["Left Front Bumper Bracket", "Front Bumper Bracket (side undetermined)"],
          "a sided name: itself and its twin")
    check(second_look.is_listed("Left Rear Door Hinge", {"Door Hinge (side undetermined)"})
          and not second_look.is_listed("Left Rear Door Hinge", {"Right Rear Door Hinge"}), "is_listed through the twin only")
    check(second_look.other_side_listed("Left Fog Lamp Cover", {"Right Fog Lamp Cover"}) == "Right Fog Lamp Cover",
          "other_side_listed knows the car map's sided names")
    check(links.end_of("Left Front Bumper Bracket") == "front" and links.end_of("Boot Lid Striker") == "rear"
          and links.end_of("Left Front Door Lock") is None and links.end_of("Fuel Filler Flap") == "rear",
          "ends: bumper and bonnet parts at theirs, door parts at neither")


def check_read_enums() -> None:
    print("the reads' enums: never a new name, names on or off")
    from app.ai import second_look
    from app.config import settings
    from app.reports.taxonomy import CAR_MAP_PARTS

    quoted = [json.dumps(p) for p in CAR_MAP_PARTS]
    for value in ("off", "on"):
        settings.intake_car_map_names = value
        try:
            text = json.dumps(_schemas())
            found = [q for q in quoted if q in text]
            check(not found, f"names {value}: no car map name in the sort, drafting, zone, second look, link check or "
                             "re-check prompts and schemas", str(found[:3]))
            cands, _ = second_look.candidates(None, make=None, model=None,
                                              listed={"Front Bumper", "Left Front Door Lock", "Boot Lid Striker"},
                                              dismissed=set(), sorter_parts=["Left Front Door Lock", "Radiator Support"])
            check(not {c.part for c in cands} & set(CAR_MAP_PARTS) and cands,
                  f"names {value}: the second look's candidates stay canonical, with new names listed or sorter-named")
        finally:
            settings.intake_car_map_names = "off"


def _build(table, listed, **opts):
    from app.intake import links

    return links.build([dict(it) for it in listed], [], [], {}, table, links.Options(mode="on", **opts))


def check_table_seam() -> None:
    print("car_map.table_for_links: the names follow intake_car_map_names (one parameter at the seam)")
    from app.config import settings
    from app.intake import car_map, links
    from app.reports.taxonomy import CANONICAL_PARTS, CAR_MAP_PARTS

    new = set(CAR_MAP_PARTS)

    def names_in(table):
        return {n for link in table["links"] for f in ("parent", "child") for n in car_map.expand(link[f])} & new

    off_shown, _ = car_map.table_for_links(links.TABLE, mode="on")
    check(not names_in(off_shown) and len(off_shown["links"]) == 117, "names off: 117 links, no new name (K1b's count)")
    check(off_shown == car_map.links_table(car_map.MAP, names=False), "names off: links_table(names=False) itself")
    settings.intake_car_map_names = "on"
    try:
        shown, shadow = car_map.table_for_links(links.TABLE, mode="on")
        check(shadow is None and len(shown["links"]) == 158 and not links.validate_table(shown),
              "names on: all 158 relations, and links.validate_table takes them")
        check(len([l for l in shown["links"] if {*car_map.expand(l["parent"]), *car_map.expand(l["child"])} & new]) == 41,
              "names on: K1b's 41 relations that name a new part")
        used = names_in(shown)
        check(used == {p for p in new if not p.endswith(" (side undetermined)")} - {"Rear Parking Sensor"},
              "every new name but Rear Parking Sensor (no relation yet, K1b doubt 3) is in a relation",
              str(sorted({p for p in new if '(side' not in p} - used)))
        l1, shadow = car_map.table_for_links(links.TABLE, mode="shadow")
        check(l1 is links.TABLE and names_in(shadow), "shadow: L1 shown, the names in the shadow table")
        same, none = car_map.table_for_links(links.TABLE, mode="off")
        check(same is links.TABLE and none is None, "mode off: L1 itself whatever the names")
        forced, _ = car_map.table_for_links(links.TABLE, mode="on", names=False)
        check(forced == off_shown, "an explicit names=False still wins over the setting")
    finally:
        settings.intake_car_map_names = "off"
    sedan = car_map.links_table(car_map.MAP, names=True, body_type="Sedan")
    hatch = car_map.links_table(car_map.MAP, names=True, body_type="Hatchback")
    unknown = car_map.links_table(car_map.MAP, names=True)
    wiper = lambda tb: any(l["child"] == "Rear Wiper Motor" for l in tb["links"])  # noqa: E731
    check(not wiper(sedan) and wiper(hatch) and wiper(unknown), "body gate: Rear Wiper Motor on a hatch or an unknown body, never a sedan")
    check(car_map.link_body_type({"make": "Toyota", "model": "Vios", "body_type": "Hatchback"}) == "Sedan"
          and car_map.link_body_type({"make": "Alphamotors", "model": "X", "body_type": "Hatchback"}) is None
          and car_map.link_body_type(None) is None,
          "link_body_type: the make/model match only, never the AI's stated body (owner decision)")
    check(set(CANONICAL_PARTS) <= set(links.CANONICAL_PARTS), "links still imports today's list")


def check_links_names_on() -> None:
    print("links.build on the names-on table: new names suggested, sides as links.py gives them")
    from app.intake import car_map

    table = car_map.links_table(car_map.MAP, names=True)
    found = {it["part"]: it for it in _build(table, [{"part": "Front Bumper", "condition_tokens": ["Buckled"]}])}
    check("Front Bumper Absorber" in found and found["Front Bumper Absorber"]["bucket"] == "strip_down"
          and found["Front Bumper Absorber"]["link_rule"].startswith("K1.0:"),
          "a buckled front bumper: the absorber, to confirm at strip-down, by a K1.0 rule")
    check("Front Bumper Bracket (side undetermined)" in found and "Left Front Bumper Bracket" not in found,
          "no firm side at the front: the bracket's twin, never a guessed side")
    check(found["Front Bumper Bracket (side undetermined)"]["covers"] ==
          ["Left Front Bumper Bracket", "Right Front Bumper Bracket", "Front Bumper Bracket (side undetermined)"],
          "the twin covers both brackets")
    sided = {it["part"]: it for it in _build(table, [
        {"part": "Front Bumper", "condition_tokens": ["Buckled"]},
        {"part": "Left Headlamp", "condition_tokens": ["Cracked"]},
        {"part": "Left Front Fender", "condition_tokens": ["Dented"]}])}
    check("Left Front Bumper Bracket" in sided and sided["Left Front Bumper Bracket"]["side_from"] == "Left Headlamp",
          "two firm left parts at the front: the left bracket (impact side), side_from named")
    door = {it["part"]: it for it in _build(table, [{"part": "Right Rear Door", "condition_tokens": ["Bent", "Jammed"]}])}
    check({"Right Rear Door Lock", "Right Rear Door Hinge"} <= set(door), "a jammed right rear door: its lock and hinge, same side")
    inferred = {it["part"] for it in _build(table, [{"part": "Right Rear Door", "condition_tokens": ["Bent", "Jammed"],
                                                      "side_inferred": True}])}
    check({"Door Lock (side undetermined)", "Door Hinge (side undetermined)"} <= inferred and not inferred & {
        "Right Rear Door Lock", "Right Rear Door Hinge"}, "an inferred side is not firm: the door twins")
    support = {it["part"] for it in _build(table, [{"part": "Radiator Support", "condition_tokens": ["Buckled"]}])}
    check("Bonnet Latch" in support, "a person-listed new-name parent has its own links (Radiator Support -> Bonnet Latch)")
    listed = {it["part"] for it in _build(table, [{"part": "Front Bumper", "condition_tokens": ["Buckled"]},
                                                  {"part": "Front Bumper Absorber", "condition_tokens": ["Cut"]},
                                                  {"part": "Front Bumper Bracket (side undetermined)"}])}
    check(not listed & {"Front Bumper Absorber", "Front Bumper Bracket (side undetermined)", "Left Front Bumper Bracket"},
          "parts already listed (the absorber, the bracket twin) are not suggested again")
    off = {it["part"] for it in _build(car_map.links_table(car_map.MAP, names=False),
                                       [{"part": "Front Bumper", "condition_tokens": ["Buckled"]},
                                        {"part": "Right Rear Door", "condition_tokens": ["Bent", "Jammed"]}])}
    from app.reports.taxonomy import CAR_MAP_PARTS

    check(off and not off & set(CAR_MAP_PARTS), "the names-off table never suggests one")


def check_link_check() -> None:
    print("the linked check: a new-name candidate is asked like any (closed C-ids, its name in the line)")
    from app.ai import link_check
    from app.intake import car_map

    table = car_map.links_table(car_map.MAP, names=True)
    items = _build(table, [{"part": "Rear Bumper", "condition_tokens": ["Crumpled"], "photo_reference": "@r1"}])
    new = [it for it in items if it["part"] in ("Rear Bumper Absorber", "Rear Bumper Bracket (side undetermined)")]
    check(new, "the crumpled rear bumper brings its absorber / bracket twin")
    areas = link_check.rule_areas(table)
    check(all(link_check.area_of(it, [{"part": "Rear Bumper"}], areas) == "rear" for it in new), "their area is the rear")
    folder = Path(tempfile.mkdtemp(prefix="carmap-photos-"))
    photos = []
    for n, (area, part) in enumerate((("rear", "Rear Bumper"), ("rear-left", None))):
        path = folder / f"r{n}.jpg"
        path.write_bytes(b"\xff\xd8\xff\xd9")
        photos.append(SimpleNamespace(id=f"r{n + 1}", path=str(path), area=area, kind="damage", part=part,
                                      shows_damage=True, excluded=False, quality="good", position=n))
    data = {"damage_summary": [{"part": "Rear Bumper", "condition_tokens": ["Crumpled"], "photo_reference": "@r1"}]}
    plan = link_check.plan_groups(items, data, photos, table, 2)
    lines = [c.line for g in plan.groups for c in g.candidates]
    check(plan.groups and any("Rear Bumper Absorber" in ln for ln in lines),
          "plan_groups lists it by name", str(lines[:4]))
    refs = [c.ref for g in plan.groups for c in g.candidates]
    schema = link_check.response_schema(refs, ["P01", "P02"])
    check(schema["properties"]["answers"]["items"]["properties"]["id"]["enum"] == refs
          and "Rear Bumper Absorber" not in json.dumps(schema), "the schema's enum is the C-ids, no part names")


def check_annex_wording() -> None:
    print("Annex A: the firm's wording for a new name, sides as the floor gives them")
    from app.parts.prefill import item_key, row_name, sync_annex_a

    sedan = {"make": "Toyota", "model": "Vios"}
    suv = {"make": "Toyota", "model": "Yaris Cross"}
    check(row_name(None, "Boot Lid Striker", sedan) == "Boot lid lock striker"
          and row_name(None, "Boot Lid Striker", suv) == "Tail gate lock striker"
          and row_name(None, "Left Boot Lid Strut", suv) == "Tail gate gas stay LH"
          and row_name(None, "Left Boot Lid Strut", suv, unsided=True) == "Tail gate gas stay"
          and row_name(None, "Radiator Support", sedan) == "Radiator support panel", "row_name: sedan / tailgate, LH last, unsided")
    check(item_key({"part": "Front Bumper Absorber"}) == "Front Bumper Absorber"
          and item_key({"part": "Left Front Door Lock"}) == "Left Front Door Lock",
          "item_key: a new name keys as itself, never its coarse family (Front Bumper, Left Front Door)")
    data = {"vehicle_info": dict(suv), "damage_summary": [
        {"part": "Front Bumper", "condition_tokens": ["Buckled"], "remarks": "Replace"},
        {"part": "Front Bumper Absorber", "condition_tokens": ["Cut"], "remarks": "Replace"},
        {"part": "Left Front Bumper Bracket", "condition_tokens": ["Cut"], "side_inferred": True},
        {"part": "Right Boot Lid Strut", "condition_tokens": ["Bent"], "human_verified": True},
        {"part": "Door Lock (side undetermined)", "condition_tokens": ["Jammed"]}]}
    rows = sync_annex_a(None, data, price_book=False)["annex_a"]["items"]
    got = [(r["part_name"], r["source_part"], r.get("condition")) for r in rows]
    check(got == [("Front bumper", "Front Bumper", "Buckled"),
                  ("Front bumper absorber", "Front Bumper Absorber", "Cut"),
                  ("Front bumper side bracket", "Left Front Bumper Bracket", "Cut"),
                  ("Tail gate gas stay RH", "Right Boot Lid Strut", "Bent"),
                  ("Door lock", "Door Lock (side undetermined)", "Jammed")],
          "one row each: the absorber apart from the bumper, an inferred side unsided, the tailgate's strut RH, a twin",
          str(got))
    confirmed = [dict(it) for it in data["damage_summary"]]
    confirmed[2]["human_verified"] = True
    kept = {"vehicle_info": dict(suv), "damage_summary": confirmed,
            "annex_a": {"items": [dict(r, unit_price=10.0, workshop_est=10.0) if r["source_part"] == "Left Front Bumper Bracket"
                                  else r for r in rows]}}
    again = sync_annex_a(None, kept, price_book=False)["annex_a"]["items"]
    bracket = [r["part_name"] for r in again if r["source_part"] == "Left Front Bumper Bracket"]
    check(bracket == ["Front bumper side bracket LH"], "a priced row follows the side once a person confirms it", str(bracket))


def check_added_part_survives() -> None:
    print("a person-added new name stays valid and printable when the names go off (API)")
    import test_annexes as annexes  # noqa: F401  (temporary database, render stand-ins)
    from fastapi.testclient import TestClient

    from app.api.main import app
    from app.config import settings
    from app.reports.db import init_db
    from app.reports.taxonomy import CANONICAL_PARTS, CAR_MAP_PARTS
    from auth_helpers import sign_in

    init_db()
    client = TestClient(app)
    with client:
        sign_in(client, role="user", name="Nina Names", sees_all_cases=True)
        r = client.post("/reports", json={"draft": {
            "category": ["Vehicle Collision or Damage"], "description": "Side swipe",
            "vehicle_info": {"plate_number": "TESTN1", "make": "Toyota", "model": "Vios"},
            "damage_summary": [{"part": "Left Front Door", "condition_tokens": ["Bent"]},
                               {"part": "Front Bumper", "condition_tokens": ["Grazed"]}]}})
        assert r.status_code == 200, r.text
        rid = r.json()["id"]
        tax = client.get("/taxonomy").json()
        check(tax["parts"] == CANONICAL_PARTS and tax["car_map_parts"] == CAR_MAP_PARTS and tax["car_map_names"] is False,
              "GET /taxonomy, names off: parts as today, the car map's listed, not offered")
        settings.intake_car_map_names = "on"
        try:
            check(client.get("/taxonomy").json()["car_map_names"] is True, "names on: offered")
            r = client.post(f"/reports/{rid}/damage-items", json={"part": "Left Front Door Lock", "condition": "Jammed",
                                                                  "remarks": "Replace"})
            check(r.status_code == 200, "names on: a person adds Left Front Door Lock", r.text)
        finally:
            settings.intake_car_map_names = "off"
        rows = lambda body: [(x["part_name"], x["source_part"]) for x in body["annex_a"]["items"]]  # noqa: E731
        r = client.post(f"/reports/{rid}/damage-items", json={"part": "Front Bumper Absorber", "condition": "Cut"})
        check(r.status_code == 200 and r.json()["item"]["part"] == "Front Bumper Absorber",
              "names off: the API still takes a car map part (the setting only decides what is offered)", r.text)
        body = r.json()
        parts = [it["part"] for it in body["damage_summary"]]
        check(parts == ["Left Front Door", "Front Bumper", "Left Front Door Lock", "Front Bumper Absorber"],
              "the absorber is its own item beside the listed Front Bumper", str(parts))
        check(("Front door lock LH", "Left Front Door Lock") in rows(body) and ("Front bumper absorber", "Front Bumper Absorber") in rows(body),
              "Annex A prints both in the firm's words with the names off", str(rows(body)))
        index = parts.index("Left Front Door Lock")
        r = client.patch(f"/reports/{rid}/damage-items/{index}", json={"condition": "Distorted/jammed"})
        check(r.status_code == 200 and r.json()["damage_summary"][index]["part"] == "Left Front Door Lock",
              "names off: the item can still be edited, its part kept", r.text)
        r = client.patch(f"/reports/{rid}/damage-items/{index}", json={"part": "Door Lock (side undetermined)"})
        check(r.status_code == 200 and ("Door lock", "Door Lock (side undetermined)") in rows(r.json()),
              "and moved to its twin, worded without a side", r.text)
        check(client.patch(f"/reports/{rid}/damage-items/{index}", json={"part": "Door lock"}).status_code == 400,
              "a name on neither list is still refused")
        filed = client.get(f"/reports/{rid}").json()["data"]
        check(any(it["part"] == "Door Lock (side undetermined)" for it in filed["damage_summary"]), "stored on the case")
        usage = client.get("/taxonomy").json()["usage"]
        check("Front Bumper Absorber" in usage["parts"] and "Front Bumper Absorber" not in usage["legacy_terms"],
              "usage counts it as a part, never a legacy term")


def check_price_book() -> None:
    print("Annex A with the price book: a kit's line for a car map part prints once")
    from app.parts.history import PastCase, PastLine, import_case
    from app.parts.prefill import sync_annex_a
    from app.reports.db import SessionLocal, init_db

    def line(n, item, condition, est):
        return PastLine(line_no=n, section="list", item=item, condition=condition, remarks="", qty=1, qty_raw="1",
                        workshop_est=est, discount=0.2, adjusted_cost=est * 0.8)

    init_db()
    db = SessionLocal()
    try:
        import_case(db, PastCase(ref="CL 99101", plate="TESTK1", make="Gammamotors", model="Saloon",
                                 make_model_raw="Gammamotors Saloon", registered=None, surveyed="2025-03-01", lines=[
                                     line(1, "Front bumper fascia", "Buckled", 900),
                                     line(2, "Front bumper absorber", "Necessary", 150),
                                     line(3, "Front bumper side bracket LH", "Necessary", 60)]), "synthetic-n1.xlsx")
        db.commit()
        vehicle = {"make": "Gammamotors", "model": "Saloon"}
        items = [{"part": "Front Bumper", "condition_tokens": ["Buckled"], "remarks": "Replace"},
                 {"part": "Front Bumper Absorber", "condition_tokens": ["Cut"]},
                 {"part": "Left Front Bumper Bracket", "condition_tokens": ["Cut"], "human_verified": True}]
        rows = sync_annex_a(db, {"vehicle_info": vehicle, "damage_summary": [dict(it) for it in items]}, True)["annex_a"]["items"]
        names = [r["part_name"] for r in rows]
        check(sorted(names) == ["Front bumper absorber", "Front bumper fascia", "Front bumper side bracket LH"],
              "the bumper's kit holds the absorber and LH bracket lines: none printed a second time", str(names))
        again = sync_annex_a(db, {"vehicle_info": vehicle, "damage_summary": [dict(it) for it in items],
                                  "annex_a": {"items": rows}}, True)["annex_a"]["items"]
        check([r["part_name"] for r in again] == names, "stable on the next sync")
        priced = [dict(r) for r in rows if r["part_name"] != "Front bumper absorber"] + [
            {"part_name": "Front bumper absorber", "source_part": "Front Bumper Absorber", "origin": "typed",
             "qty": 1, "workshop_est": 99.0, "discount_pct": 0.2, "is_net_item": False}]
        kept = sync_annex_a(db, {"vehicle_info": vehicle, "damage_summary": [dict(it) for it in items],
                                 "annex_a": {"items": priced}}, True)["annex_a"]["items"]
        absorber = [(r["source_part"], r.get("workshop_est")) for r in kept if r["part_name"] == "Front bumper absorber"]
        check(absorber == [("Front Bumper Absorber", 99.0)],
              "the absorber's own priced row stands; the bumper's kit doesn't add its line again", str(absorber))
        alone = sync_annex_a(db, {"vehicle_info": vehicle, "damage_summary": [dict(items[1])]}, True)["annex_a"]["items"]
        check([(r["part_name"], r["source_part"]) for r in alone] == [("Front bumper absorber", "Front Bumper Absorber")],
              "without the bumper: its own unpriced row, in the firm's words (no past family of its own)")
    finally:
        db.close()


def check_suggestion_add() -> None:
    print("a new-name suggestion is added beside its coarse family")
    from app.reports import suggestions

    data = {"damage_summary": [{"part": "Front Bumper", "condition_tokens": ["Buckled"]}]}
    sug = {"key": "Front Bumper Absorber", "part": "Front Bumper Absorber", "source": "link", "bucket": "strip_down",
           "needs_strip_down": True, "linked_from": ["Front Bumper"], "link_type": "behind", "hidden": True,
           "covers": ["Front Bumper Absorber"], "evidence": []}
    check(not suggestions._listed(sug, data), "a listed Front Bumper doesn't list the absorber")
    item = suggestions.item_from_suggestion(sug)
    check(item["part"] == "Front Bumper Absorber" and item["needs_strip_down"] and item["linked_from"] == ["Front Bumper"],
          "item_from_suggestion keeps the name and where it hangs off")
    twin = {"key": "Door Lock (side undetermined)", "part": "Door Lock (side undetermined)", "covers": []}
    check(suggestions._listed(twin, {"damage_summary": [{"part": "Right Rear Door Lock"}]}),
          "a door-lock twin is listed by a sided door lock (covers_of)")


def main() -> int:
    if SNAPSHOT_ONLY:
        print(json.dumps(snapshot(), indent=1))
        return 0
    check_names_off_is_today()
    check_setting()
    check_taxonomy_names()
    check_covers()
    check_read_enums()
    check_table_seam()
    check_links_names_on()
    check_link_check()
    check_annex_wording()
    check_suggestion_add()
    check_added_part_survives()
    check_price_book()
    if FAILED:
        print(f"\n{len(FAILED)} FAILED: " + "; ".join(FAILED))
        return 1
    print("\nALL CAR MAP NAMES CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
