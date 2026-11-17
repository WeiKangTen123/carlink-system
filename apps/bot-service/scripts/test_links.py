"""The linked-parts engine (contract 10, plans/2026-11-17-linked-parts/
contract10.md; app/intake/links.py) -- pure, offline, no Gemini.

Covers plan.md P2's done-when list on the engine:
  - the table: agent L's real table (L1) loads and validates; broken copies
    (an unknown or undetermined name, a twice-used id, a wrong side, a long
    or numbered reason, a bad trigger, area or level, a missing field) load
    as None (links off);
  - hit levels: every condition word, damage type, severity, remark and
    whole condition the read can give has a level (or says none); the
    hardest wins; no words -> intake_links_unknown_hit; the end bonus;
  - the gate: min_hit, trigger_tokens, the types allowed;
  - sides: a firm parent's side; not firm (Low, inferred, strict) -> the
    "(side undetermined)" name; an undetermined parent through the
    taxonomy (only children every sided name has); "impact" by a clear
    majority or the point of impact; never two children for one link;
  - skip / join: listed (and covered), dismissed, a zone suggestion's twin
    (joined to it), two parents of one child;
  - intact: demote (last, with the read), drop, ignore;
  - buckets, order, the per-parent and overall caps; a heavily hit rear
    bumper gives the beam to confirm at strip-down and the parking sensor
    as likely;
  - evidence (the parent's photo by "link", no box; the sorter's photo by
    "sort" for a seen one), reasons (the table's words), link_rule;
  - for_parent (an added Rear End Panel at heavy unlocks its links, at
    light it does not); the catalogue's diagram neighbours;
  - pure: the inputs are not changed, and no AI client is imported.

    python scripts/test_links.py

Parts and photos are made up.
"""
import copy
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="links-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake-links-key"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"

from app.intake import links  # noqa: E402

# Imported by links itself? Checked before anything else pulls them in.
AI_CLIENT_IMPORTED = sorted(m for m in sys.modules if m in ("app.ai.structured", "google.genai") or m.startswith("google.genai."))

from app.reports.taxonomy import (  # noqa: E402
    CANONICAL_DAMAGE_TYPES,
    CANONICAL_PARTS,
    CANONICAL_SEVERITIES,
    FIRM_CONDITION_TOKENS,
    FIRM_REMARKS,
    FIRM_WHOLE_CONDITIONS,
)

TABLE = links.load_table()
ON = links.Options(mode="on")

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


def item(part: str, *tokens, photo: str | None = None, **more) -> dict:
    out = {"part": part, "condition_tokens": list(tokens), "ai_confidence": "High", **more}
    if photo:
        out["photo_reference"] = photo
        out["evidence"] = [{"photo": photo, "box": [100, 100, 300, 300], "box_kind": "ai", "by": "overall"}]
    return out


def parts(found: list[dict]) -> list[str]:
    return [it["part"] for it in found]


def by_part(found: list[dict]) -> dict[str, dict]:
    return {it["part"]: it for it in found}


def build(listed, others=(), photos=(), intact=None, opts=ON, **kw) -> list[dict]:
    return links.build(list(listed), list(others), list(photos), intact or {}, TABLE, opts, **kw)


def photo(pid: str, part=None, *, area="rear", kind="damage", damage=True, excluded=False, quality="good"):
    return SimpleNamespace(id=pid, part=part, area=area, kind=kind, shows_damage=damage, excluded=excluded,
                           quality=quality, damage_box=[10, 10, 200, 200])


# ---------------------------------------------------------------- the table


def table() -> None:
    check(AI_CLIENT_IMPORTED == [], "links imports no AI client (the benchmark's replay guard)", AI_CLIENT_IMPORTED)
    check(TABLE is not None and links.TABLE is not None, "agent L's link_table.json loads")
    check(links.validate_table(TABLE) == [], "and validates", links.validate_table(TABLE)[:3])
    check(TABLE["version"] == "L1" and links.TABLE_VERSION == "L1", "version L1 (frozen)", TABLE.get("version"))
    raw = json.loads(links.TABLE_PATH.read_text(encoding="utf-8"))
    check(len(raw["links"]) == len(TABLE["links"]) >= 60, "every link kept", len(TABLE["links"]))
    sided = [l for l in TABLE["links"] if "{side}" in l["parent"] or "{side}" in l["child"]]
    check(all(n in CANONICAL_PARTS for l in sided for f in ("parent", "child")
              for n, _ in links._expand(l[f])), "every {side} link expands to canonical Left and Right names")
    check(all(links.covers(n)[-1].endswith("(side undetermined)") for l in TABLE["links"] if "{side}" in l["child"]
              for n, _ in links._expand(l["child"])), "every sided child has a \"(side undetermined)\" name")

    def broken(change) -> list[str]:
        bad = copy.deepcopy(raw)
        change(bad)
        return links.validate_table(bad)

    cases = {
        "an unknown part": lambda t: t["links"][0].update(child="Front Bumper Bracket"),
        "an undetermined name": lambda t: t["links"][0].update(child="Tail Lamp (side undetermined)"),
        "Other / Not Listed": lambda t: t["links"][0].update(child="Other / Not Listed"),
        "an id twice": lambda t: t["links"][1].update(id=t["links"][0]["id"]),
        "a side the names don't say": lambda t: t["links"][0].update(side="same"),
        "a reason over 160": lambda t: t["links"][0].update(reason="x" * 161),
        "a 3-digit number": lambda t: t["links"][0].update(reason="Check part 123 of the bumper."),
        "a trigger that isn't a firm word": lambda t: t["links"][0].update(trigger_tokens=["Smashed"]),
        "an unknown area": lambda t: t["links"][0].update(area="roof"),
        "an unknown level": lambda t: t["links"][0].update(min_hit="severe"),
        "an unknown type": lambda t: t["links"][0].update(type="linked"),
        "hidden not a bool": lambda t: t["links"][0].update(hidden="yes"),
        "a missing field": lambda t: t["links"][0].pop("min_hit"),
        "no version": lambda t: t.update(version=" "),
        "the same pair twice": lambda t: t["links"].append({**t["links"][0], "id": "copy"}),
    }
    for name, change in cases.items():
        check(broken(change), f"refused: {name}", broken(change)[:1])
    folder = Path(_tmp, "tables")
    folder.mkdir()
    bad = copy.deepcopy(raw)
    bad["links"][0]["child"] = "Front Bumper Bracket"
    (folder / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    check(links.load_table(folder / "bad.json") is None, "load_table: a table that fails the check is None (links off)")
    check(links.build([item("Rear Bumper", "Crumpled")], [], [], {}, None, ON) == [], "no table: nothing")
    side_area = copy.deepcopy(raw)
    side_area["links"][0]["area"] = "side"
    check(links.validate_table(side_area) == [], "area \"side\" (L1's doors and sills) is allowed beside 4.3's six")


# ---------------------------------------------------------------- hit levels


def hit_levels() -> None:
    check(set(links.HIT_OF_TOKEN) == set(FIRM_CONDITION_TOKENS), "every firm condition word has a level",
          set(FIRM_CONDITION_TOKENS) ^ set(links.HIT_OF_TOKEN))
    check(set(links.HIT_OF_DAMAGE_TYPE) == set(CANONICAL_DAMAGE_TYPES), "every damage type is mapped",
          set(CANONICAL_DAMAGE_TYPES) ^ set(links.HIT_OF_DAMAGE_TYPE))
    check(set(links.HIT_OF_SEVERITY) == set(CANONICAL_SEVERITIES) and set(links.HIT_OF_REMARKS) == set(FIRM_REMARKS)
          and set(links.HIT_OF_WHOLE_CONDITION) == set(FIRM_WHOLE_CONDITIONS),
          "every severity, remark and whole condition is mapped")
    levels = set(links.HIT_LEVELS) | {None}
    check(all(v in levels for m in (links.HIT_OF_TOKEN, links.HIT_OF_DAMAGE_TYPE, links.HIT_OF_SEVERITY,
                                    links.HIT_OF_REMARKS, links.HIT_OF_WHOLE_CONDITION) for v in m.values()),
          "every level is one of HIT_LEVELS (or none)")
    want = {
        ("Grazed",): "light", ("Chipped",): "light", ("Dented",): "medium", ("Cracked",): "medium",
        ("Cut",): "medium", ("Torn",): "medium", ("Shifted",): "medium", ("Bent",): "heavy",
        ("Deformed",): "heavy", ("Distorted",): "heavy", ("Buckled",): "heavy", ("Warped",): "heavy",
        ("Jammed",): "heavy", ("Crumpled",): "crush", ("Grazed", "Crumpled"): "crush",
    }
    for tokens, level in want.items():
        check(links.hit_of({"condition_tokens": list(tokens)}) == level, f"{'/'.join(tokens)} -> {level}")
    check(links.hit_of({"damage_type": "Structural Damage"}) == "crush"
          and links.hit_of({"damage_type": "Scratch"}) == "light" and links.hit_of({"damage_type": "Water Damage"}) is None,
          "damage type: Structural Damage crush, Scratch light, Water Damage says nothing")
    check(links.hit_of({"severity": "Severe"}) == "heavy" and links.hit_of({"severity": "minor"}) == "light"
          and links.hit_of({"severity": "Moderate"}) == "medium", "severity: Minor / Moderate / Severe (any case)")
    check(links.hit_of({"remarks": "Replace", "condition_tokens": ["Grazed"]}) == "medium",
          "Replace lifts a graze to medium (past repair)")
    check(links.hit_of({"condition": "Grazed/cracked"}) == "medium" and links.hit_of({"condition": "Necessary"}) is None
          and links.hit_of({"condition": "Repair"}) == "light", "a person's condition is read too")
    check(links.hit_of({"part": "Rear Bumper"}) is None and links.hit_of(None) is None, "no words: None")
    check(links.hit_of({"condition_tokens": ["Grazed"], "severity": "Severe", "damage_type": "Dent"}) == "heavy",
          "the hardest word wins")


# ---------------------------------------------------------------- the gate


def gate() -> None:
    def rules(found):
        return {it["link_rule"] for it in found}

    light = build([item("Rear Bumper", "Grazed")])
    check(light == [], "a grazed rear bumper (light): no links", parts(light))
    medium = build([item("Rear Bumper", "Dented")])
    check(rules(medium) == {"L1:rear-bumper>rear-number-plate", "L1:rear-bumper>reflector",
                            "L1:rear-bumper>parking-sensor", "L1:rear-bumper>rear-wheel-arch-liner"},
          "a dented one (medium): only its medium links", sorted(rules(medium)))
    heavy = build([item("Rear Bumper", "Deformed")])
    check("L1:rear-bumper>beam" in rules(heavy) and not any(it["hit"] != "heavy" for it in heavy)
          and "L1:rear-bumper>rear-floor-panel" not in rules(heavy),
          "a deformed one (heavy): its heavy links too, not the crush-only ones", sorted(rules(heavy)))
    check(all(it["hit"] == "medium" for it in build([item("Rear Bumper")])) and build([item("Rear Bumper")]),
          "no words: intake_links_unknown_hit (medium)")
    check(build([item("Rear Bumper")], opts=links.Options(mode="on", unknown_hit="light")) == [],
          "unknown_hit light: no links for a part with no words")
    shifted = build([item("Rear Bumper", "Shifted")])
    check("L1:rear-bumper>tail-lamp" in rules(shifted) and "L1:rear-bumper>beam" not in rules(shifted),
          "trigger_tokens: Shifted (medium) opens the heavy tail-lamp link, not the beam", sorted(rules(shifted)))
    wheel = build([item("Left Front Wheel", "Cracked")])
    check("L1:front-wheel>suspension-front" in rules(wheel) and "L1:front-wheel>steering-assembly" not in rules(wheel),
          "trigger_tokens: a cracked rim opens the suspension link only", sorted(rules(wheel)))
    only = build([item("Rear Bumper", "Deformed")], opts=links.Options(mode="on", types=("behind",)))
    check(only and all(it["link_type"] == "behind" for it in only), "intake_links_types: only those types",
          sorted({it["link_type"] for it in only}))
    alone = build([item("Rear Bumper", "Deformed")])
    crowd = build([item("Rear Bumper", "Deformed"), item("Rear End Panel", "Dented")],
                  opts=links.Options(mode="on", types=("crush",)))
    check(all(it["hit"] == "heavy" for it in alone if "Rear Bumper" in it["linked_from"])
          and any(it["hit"] == "crush" for it in crowd if "Rear Bumper" in it["linked_from"])
          and "Rear Floor Panel" in parts(crowd),
          "end bonus: heavy + a structural part at that end -> crush (Rear Floor Panel unlocked)", parts(crowd))
    three = build([item("Rear Bumper", "Deformed"), item("Boot Lid", "Dented"), item("Rear Windscreen", "Cracked")])
    check(any(it["hit"] == "crush" for it in three), "end bonus: heavy + three parts at the rear end -> crush")
    under = build([item("Rear Bumper", "Deformed")], photos=[photo("u1", area="underbody")])
    check(any(it["hit"] == "crush" for it in under), "end bonus: heavy + an underbody photo showing damage -> crush")
    front = build([item("Rear Bumper", "Deformed"), item("Front Bumper", "Dented"), item("Bonnet", "Dented"),
                   item("Front Grille", "Cracked")])
    check(all(it["hit"] == "heavy" for it in front if "Rear Bumper" in it["linked_from"]),
          "the bonus is per end: three front parts don't crush the rear bumper")
    mediums = build([item("Rear Bumper", "Dented"), item("Rear End Panel", "Dented")])
    check(not any(it["hit"] == "crush" for it in mediums), "no heavy part at that end: no bonus")


# ---------------------------------------------------------------- sides


def sides() -> None:
    door = by_part(build([item("Left Front Door", "Buckled")]))
    check("Left Front Window Regulator" in door and door["Left Front Window Regulator"]["side_from"] == "Left Front Door",
          "same: a firm Left Front Door -> Left Front Window Regulator, side_from the door", sorted(door))
    check(door.get("Interior Trim", {}).get("side_from") is None, "an unsided child takes no side (side_from None)")
    for name, extra in (("Low", {"ai_confidence": "Low"}), ("inferred", {"side_inferred": True})):
        loose = parts(build([item("Left Front Door", "Buckled", **extra)]))
        check("Window Regulator (side undetermined)" in loose and not any(p.startswith("Left ") for p in loose),
              f"not firm ({name}): the \"(side undetermined)\" name, never a side", loose)
    strict = links.Options(mode="on", side="strict")
    once = parts(build([item("Left Front Door", "Buckled", seen_in=["@a"])], opts=strict))
    twice = parts(build([item("Left Front Door", "Buckled", seen_in=["@a", "@b"])], opts=strict))
    check("Window Regulator (side undetermined)" in once and "Left Front Window Regulator" in twice,
          "strict: firm needs two photos (or two reads)", (once, twice))
    und = by_part(build([item("Door (side undetermined)", "Buckled")]))
    check("Window Regulator (side undetermined)" in und and "Door Glass (side undetermined)" in und
          and not any("Wing Mirror" in p for p in und) and all(it["side_from"] is None for it in und.values()),
          "an undetermined door: its children every door has (no wing mirror: front doors only), undetermined",
          sorted(und))
    wheel = parts(build([item("Wheel (side undetermined)", "Bent")]))
    check("Tyre (side undetermined)" in wheel and "Suspension - Front" not in wheel and "Suspension - Rear" not in wheel,
          "an undetermined wheel: the tyre, not a front or rear suspension it can't place", wheel)
    lamp = parts(build([item("Tail Lamp (side undetermined)", "Cracked")]))
    check("Boot Lid Lamp (side undetermined)" in lamp, "an undetermined tail lamp maps through the taxonomy", lamp)

    alone = by_part(build([item("Rear Bumper", "Dented")]))
    check("Rear Bumper Reflector (side undetermined)" in alone, "impact, nothing says the side: undetermined")
    major = by_part(build([item("Rear Bumper", "Dented"), item("Left Rear Quarter Panel", "Dented")]))
    check(major.get("Left Rear Bumper Reflector", {}).get("side_from") == "Left Rear Quarter Panel",
          "impact: a firm Left part at the rear -> Left, side_from it", sorted(major))
    tie = parts(build([item("Rear Bumper", "Dented"), item("Left Rear Quarter Panel", "Dented"),
                       item("Right Rear Quarter Panel", "Dented")]))
    check("Rear Bumper Reflector (side undetermined)" in tie, "impact: one left, one right -> undetermined", tie)
    door_side = parts(build([item("Rear Bumper", "Dented"), item("Left Rear Door", "Dented")]))
    check("Rear Bumper Reflector (side undetermined)" in door_side, "impact: a door isn't at the rear end", door_side)
    poi = by_part(build([item("Rear Bumper", "Dented")], impact="Rear LH corner"))
    check(poi.get("Left Rear Bumper Reflector", {}).get("side_from") == "point of impact",
          "impact: the point of impact \"Rear LH corner\" -> Left", sorted(poi))
    other_end = parts(build([item("Rear Bumper", "Dented")], impact="Front left"))
    check("Rear Bumper Reflector (side undetermined)" in other_end, "a point of impact at the other end: no side")
    disagree = parts(build([item("Rear Bumper", "Dented"), item("Left Rear Quarter Panel", "Dented")],
                           impact="rear right"))
    check("Rear Bumper Reflector (side undetermined)" in disagree, "the list and the point of impact disagree: none")
    check(links.impact_words("REAR RH") == ("Right", "rear") and links.impact_words("left and right")[0] is None,
          "impact_words")
    many = build([item("Left Front Door", "Buckled"), item("Right Front Door", "Buckled"), item("Rear Bumper", "Deformed")])
    rules = [it["link_rule"] for it in many if len(it["linked_from"]) == 1]
    per_parent = [(it["linked_from"][0], it["link_rule"]) for it in many]
    check(len(per_parent) == len(set(per_parent)), "never two children for one link from one parent", rules)


# ---------------------------------------------------------------- skip / join


def skip_join() -> None:
    listed = parts(build([item("Rear Bumper", "Dented"), item("Parking Sensor", "Cracked")]))
    check("Parking Sensor" not in listed, "a listed part is not suggested")
    covered = parts(build([item("Rear Bumper", "Dented"), item("Rear Bumper Reflector (side undetermined)", "Cracked")]))
    check(not any("Reflector" in p for p in covered), "nor one its undetermined twin on the list covers", covered)
    gone = parts(build([item("Rear Bumper", "Dented")], dismissed={"Parking Sensor"}))
    check("Parking Sensor" not in gone, "a dismissed key is not suggested again")
    zone = {"key": "Left Rear Bumper Reflector", "part": "Left Rear Bumper Reflector", "source": "zone"}
    found, joins = links.build_all([item("Rear Bumper", "Dented")], [zone], [], {}, TABLE, ON)
    check(not any("Reflector" in p for p in parts(found)) and joins.get("Left Rear Bumper Reflector", {}).get(
        "linked_from") == ["Rear Bumper"], "a zone suggestion's twin: no item of its own, joined to it", joins)
    both = by_part(build([item("Rear Bumper", "Deformed"), item("Boot Lid", "Buckled")]))
    check(both.get("Rear End Panel", {}).get("linked_from") == ["Rear Bumper", "Boot Lid"],
          "two parents of one child: one item, linked_from both", both.get("Rear End Panel"))
    check(both.get("Reversing Camera", {}).get("linked_from") == ["Rear Bumper", "Boot Lid"]
          or both.get("Reversing Camera", {}).get("linked_from") == ["Boot Lid", "Rear Bumper"],
          "the camera from both too")
    twin = parts(build([item("Rear Bumper", "Shifted"), item("Left Rear Quarter Panel", "Dented"),
                        item("Right Rear Quarter Panel", "Dented")]))
    check(len([p for p in twin if "Tail Lamp" in p]) == 2 and "Tail Lamp (side undetermined)" not in twin,
          "a sided name and its undetermined twin: one item each side, no undetermined extra", twin)


# ---------------------------------------------------------------- intact


def intact() -> None:
    intact = {"zone:rear": ["Parking Sensor", "Left Tail Lamp"]}
    demoted = build([item("Rear Bumper", "Dented")], intact=intact)
    sensor = by_part(demoted)["Parking Sensor"]
    check(sensor["intact_in"] == ["zone:rear"] and demoted[-1]["part"] == "Parking Sensor",
          "demote: last, with the zone read that called it intact", parts(demoted))
    dropped = parts(build([item("Rear Bumper", "Dented")], intact=intact, opts=links.Options(mode="on", intact="drop")))
    check("Parking Sensor" not in dropped, "drop: left out")
    ignored = build([item("Rear Bumper", "Dented")], intact=intact, opts=links.Options(mode="on", intact="ignore"))
    check(not any(it["intact_in"] for it in ignored) and ignored[-1]["part"] != "Parking Sensor", "ignore: no note")
    check(links.intact_in("Tail Lamp (side undetermined)", intact) == []
          and links.intact_in("Tail Lamp (side undetermined)", {"zone:rear": ["Left Tail Lamp", "Right Tail Lamp"]})
          == ["zone:rear"], "an undetermined part is intact only when both sides are")
    check(ON.intact == "demote", "the default: demote (zone \"intact\" lists are often wrong)")


# ---------------------------------------------------------------- buckets, order, caps


def buckets_order() -> None:
    heavy = build([item("Rear Bumper", "Deformed", photo="@p1")])
    found = by_part(heavy)
    check(found.get("Rear Bumper Beam", {}).get("bucket") == "strip_down" and found["Rear Bumper Beam"]["hidden"]
          and found["Rear Bumper Beam"]["needs_strip_down"], "heavy rear bumper: the beam, to confirm at strip-down",
          found.get("Rear Bumper Beam"))
    check(found.get("Parking Sensor", {}).get("bucket") == "likely" and not found["Parking Sensor"]["hidden"],
          "...and the parking sensor, likely", found.get("Parking Sensor"))
    order = [(links.BUCKETS.index(it["bucket"]), links.LINK_TYPES.index(it["link_type"])) for it in heavy]
    check(order == sorted(order), "ordered seen > likely > strip_down, then attached > opens_with > behind > crush",
          [(it["part"], it["bucket"], it["link_type"]) for it in heavy])
    per = {}
    for it in heavy:
        per[it["bucket"]] = per.get(it["bucket"], 0) + 1
    check(all(n <= links.MAX_PER_PARENT for n in per.values()) and len(heavy) <= ON.max_items,
          "at most 4 per parent in each bucket, 12 in all", per)
    lid = by_part(build([item("Boot Lid", "Buckled")]))
    check(lid["Boot Lid Lock"]["bucket"] == "likely" and lid["Boot Lid Lock"]["hidden"],
          "opens with the lid (hidden, no strip-down): likely, still hidden (the re-check skips it)")
    big = build([item("Rear Bumper", "Crumpled"), item("Front Bumper", "Crumpled"), item("Left Front Door", "Buckled"),
                 item("Left Rear Quarter Panel", "Buckled")])
    check(len(big) == 12, "a big case: the own cap of 12", len(big))
    check(len(build([item("Rear Bumper", "Deformed")], opts=links.Options(mode="on", max_items=3))) == 3
          and build([item("Rear Bumper", "Deformed")], opts=links.Options(mode="on", max_items=0)) == [],
          "intake_links_max: 3 -> 3, 0 -> none")
    hit_first = build([item("Rear Bumper", "Dented"), item("Boot Lid", "Buckled")])
    likely = [it for it in hit_first if it["bucket"] == "likely" and it["link_type"] == "attached"]
    check(likely and likely[0]["linked_from"] == ["Boot Lid"], "the harder-hit parent's links first",
          [(it["part"], it["linked_from"]) for it in likely])
    seen = by_part(build([item("Rear Bumper", "Deformed", photo="@p1")],
                         photos=[photo("p1", "Rear Bumper"), photo("p2", "Rear Bumper Beam"),
                                 photo("p3", "Parking Sensor", excluded=True)]))
    beam = seen["Rear Bumper Beam"]
    check(beam["bucket"] == "seen" and beam["photo_reference"] == "@p2" and beam["seen_in"] == ["@p2"]
          and beam["evidence"][0]["by"] == "sort", "seen: the sorter tagged a photo with it (evidence by \"sort\")", beam)
    check(seen["Parking Sensor"]["bucket"] == "likely", "an excluded photo's tag doesn't count")
    seen_first = build([item("Rear Bumper", "Deformed")], photos=[photo("p2", "Rear Bumper Beam")])
    check(seen_first[0]["part"] == "Rear Bumper Beam", "seen first")
    replay = by_part(build([item("Rear Bumper", "Deformed")],
                           photos=[{"n": 3, "kind": "damage", "area": "rear", "damage": True, "part": "Parking Sensor"}]))
    check(replay["Parking Sensor"]["seen_in"] == ["P03"], "a stored run's photo dicts work (the benchmark's replay)")


# ---------------------------------------------------------------- evidence, reasons, rules


def evidence_reasons() -> None:
    found = build([item("Rear Bumper", "Deformed", photo="@p1")], photos=[photo("p1", "Rear Bumper")])
    ev = [e for it in found for e in it["evidence"]]
    check(found and all(e["photo"] == "@p1" and e["by"] == "link" and e["box"] is None and e["box_kind"] is None
                        for e in ev), "evidence: the parent's photo, by \"link\", no box")
    check(all(it["photo_reference"] is None and it["seen_in"] == [] for it in found),
          "no photo_reference or seen_in claimed from the parent's photo")
    reasons = {l["id"]: l["reason"] for l in TABLE["links"]}
    check(all(it["reason"] == reasons[it["link_rule"].split(":", 1)[1]] for it in found),
          "the reason is the table's own words")
    check(not any("AI says" in (it["reason"] or "") for it in found) and all(len(it["reason"]) <= 160 for it in found),
          "never \"AI says\"; at most 160 characters")
    check(all(it["link_rule"].startswith("L1:") and it["source"] == "link" and it["key"] == it["part"]
              and it["covers"] and it["confidence"] is None for it in found), "link_rule L1:<id>, source link, key, covers")
    gone = build([item("Rear Bumper", "Deformed", photo="@p1", seen_in=["@p1", "@p2"])],
                 photos=[photo("p1", excluded=True), photo("p2")])
    check(gone and all(it["evidence"][0]["photo"] == "@p2" for it in gone), "an excluded parent photo is passed over")
    none = build([item("Rear Bumper", "Deformed")])
    check(none and all(it["evidence"] == [] for it in none), "a parent with no photo: no evidence")
    from app.reports.schema import SecondLookItem

    dumped = [SecondLookItem.model_validate(it).model_dump() for it in found]
    check(all(d["source"] == "link" and d["linked_from"] == ["Rear Bumper"] for d in dumped),
          "each item is a valid SecondLookItem")


# ---------------------------------------------------------------- for_parent, catalogue, purity


def relink() -> None:
    zone_items = [{"key": "Left Rear Bumper Reflector", "part": "Left Rear Bumper Reflector", "source": "zone"}]
    data = {"damage_summary": [item("Rear Bumper", "Dented")],
            "second_look": {"items": zone_items, "dismissed": ["Boot Lid Weatherstrip"]}}
    heavy_panel = item("Rear End Panel", "Buckled", photo="@p5")
    found = links.for_parent(heavy_panel, data, [], TABLE, ON)
    check(parts(found) == ["Boot Lid Lock", "Rear Floor Panel"] and all(it["linked_from"] == ["Rear End Panel"]
                                                                        for it in found),
          "an added Rear End Panel at heavy: its links (the dismissed weatherstrip left out)", parts(found))
    check(links.for_parent(item("Rear End Panel", "Grazed"), data, [], TABLE, ON) == [],
          "at light: none")
    check(links.for_parent(item("Rear End Panel"), data, [], TABLE, ON) == [], "no words (unknown_hit medium): none")
    sensor_data = {"damage_summary": [item("Rear Bumper", "Dented")],
                   "second_look": {"items": [{"key": "Parking Sensor", "part": "Parking Sensor", "source": "link",
                                              "linked_from": ["Rear Bumper"]}]}}
    again = links.for_parent(item("Rear Bumper", "Dented"), sensor_data, [], TABLE, ON)
    check("Parking Sensor" not in parts(again), "a part suggested already is not suggested twice", parts(again))
    full = {"damage_summary": [], "second_look": {"items": [
        {"key": f"k{i}", "part": p, "source": "link", "linked_from": ["x"]}
        for i, p in enumerate(["Radiator", "Fuel Tank", "Seat"])]}}
    room = links.for_parent(item("Rear Bumper", "Deformed"), full, [], TABLE, links.Options(mode="on", max_items=4))
    check(len(room) == 1, "within the room intake_links_max leaves beside the open linked parts", parts(room))

    neighbours = {"Rear Bumper": [
        {"part": "Rear Bumper Beam", "oem_name": "REINFORCEMENT, RR BUMPER", "diagram": "BUMPERS", "source": "x"},
        {"part": "Right Rear Wheel Arch Trim", "oem_name": "GARNISH", "diagram": "BUMPERS", "source": "x"}]}
    off = build([item("Rear Bumper", "Deformed")], neighbours=neighbours)
    check(not any(it["oem"] for it in off) and "Wheel Arch Trim (side undetermined)" not in parts(off),
          "intake_links_catalogue off: the neighbours are not used")
    cat_on = links.Options(mode="on", catalogue=True)
    cat = by_part(build([item("Rear Bumper", "Deformed")], neighbours=neighbours, opts=cat_on))
    check(cat["Rear Bumper Beam"]["oem"] == ["BUMPERS"], "on: a table link's child shares a diagram -> oem")
    trim = by_part(build([item("Rear Bumper", "Dented")], neighbours=neighbours, opts=cat_on)).get(
        "Wheel Arch Trim (side undetermined)")
    check(trim is not None and trim["link_rule"].startswith("L1:oem>") and trim["oem"] == ["BUMPERS"]
          and trim["side_from"] is None and trim["link_type"] == "attached",
          "a neighbour no link names: an attached candidate, its side never the catalogue's", trim)

    listed = [item("Rear Bumper", "Deformed", photo="@p1"), item("Left Rear Quarter Panel", "Dented")]
    before = copy.deepcopy(listed)
    others = [{"part": "Boot Lid Lock", "key": "Boot Lid Lock"}]
    others_before = copy.deepcopy(others)
    links.build(listed, others, [photo("p1")], {"zone:rear": ["Parking Sensor"]}, TABLE, ON)
    check(listed == before and others == others_before, "pure: the damage list and the suggestions are not changed")
    check(links.build(listed, others, [], {}, TABLE, ON) == links.build(listed, others, [], {}, TABLE, ON),
          "deterministic")


def main() -> int:
    for name, fn in (
        ("the link table (L1) and its validator", table),
        ("hit levels from the read's words", hit_levels),
        ("the gate: min_hit, trigger words, types, the end bonus", gate),
        ("sides: firm, undetermined, impact", sides),
        ("skip and join", skip_join),
        ("intact: demote, drop, ignore", intact),
        ("buckets, order and caps", buckets_order),
        ("evidence, reasons, rules", evidence_reasons),
        ("for_parent, the catalogue, purity", relink),
    ):
        run_section(name, fn)
    print(f"\ntest_links: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAILED [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
