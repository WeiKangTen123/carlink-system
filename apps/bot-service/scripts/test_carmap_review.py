"""The car map's review screen (K4; plans/2026-11-18-car-knowledge/
K1-design.md 3, 5b and 6) -- offline, no Gemini, made-up cases only.

Covers:
  - settings: the four new ones (intake_links_order, _assembly_max,
    _stripdown_cap, _stripdown_max) default to today, are parsed as
    contract 10's, and pass through docker-compose.prod.yml with the same
    defaults; links.options reads them (and a source without them);
  - defaults identical to master: links.build_all and for_parent, with the
    car map off, give what master's links.py (git 99aaa85) gives, field for
    field and in order, over a sweep of lists, photos, intact reads,
    dismissals, catalogue neighbours and every contract 10 knob;
  - each setting's effect: the prior order inside a bucket (None counts as
    3); the per-group cap; strip-down "out" -- its own folded block after
    the others, outside max_items, capped by stripdown_max; a second hop's
    part in that block unless a photo shows it; for_parent's rooms and an
    assembly's open parts counted in its cap;
  - the car map's facts (car_map.review_lookup, from_l1(L1) and a made-up
    map): the same parts as L1 with the group fields; one group per side
    for a sided assembly; two parents in one assembly share its cap; "if
    fitted"; priors; "also check" words for the parts the names-off table
    leaves out (their hit and body gates);
  - the seam: off is L1 itself and never touches the car map; on without a
    map, with a broken map or a map whose table fails the check: L1, logged;
    shadow: L1 shown, the map's linked parts on ai_draft only
    (car_map_suggestions); second_look._store, suggestions.relinked and the
    linked check's areas through it;
  - old drafts load and dump unchanged; the new fields are left out at
    their defaults; filing relabels a yes sign's photo and the shadow list;
    an added part carries none of the group fields;
  - the replay (scripts/bench/replay_links.py): no K4 flag -> today's
    options and rows; the flags -> the review metrics; a bad map -> exit 2;
  - the benchmark's ai_settings: the K4 knobs only off their defaults, the
    car map's mode (and version, sha256) only when not off.

    python scripts/test_carmap_review.py
"""
import copy
import json
import logging
import os
import subprocess
import sys
import tempfile
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(Path(__file__).resolve().parent))
_tmp = tempfile.mkdtemp(prefix="carmap-review-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake-carmap-review-key"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"
os.environ["SECOND_LOOK_ENABLED"] = "false"
for _name in ("INTAKE_LINKS", "INTAKE_LINK_CHECK", "INTAKE_LINKS_MAX", "INTAKE_LINKS_TYPES", "INTAKE_LINKS_SIDE",
              "INTAKE_LINKS_INTACT", "INTAKE_LINKS_UNKNOWN_HIT", "INTAKE_LINKS_CATALOGUE", "INTAKE_CAR_MAP",
              "INTAKE_LINKS_ORDER", "INTAKE_LINKS_ASSEMBLY_MAX", "INTAKE_LINKS_STRIPDOWN_CAP",
              "INTAKE_LINKS_STRIPDOWN_MAX", "CATALOGUE_ENABLED"):
    os.environ.pop(_name, None)

from app import config  # noqa: E402
from app.ai import link_check, second_look  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import car_map, links  # noqa: E402
from app.reports import filing, suggestions  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import IntakeSession, Report  # noqa: E402
from app.reports.schema import LINK_SUGGESTION_DEFAULTS, SecondLook, SecondLookItem  # noqa: E402
from app.reports.taxonomy import CANONICAL_PARTS, unsided_part  # noqa: E402

L1 = links.TABLE
ON = links.Options(mode="on")
MASTER = "99aaa85"  # contract 10 live (PR #34): links.py as deployed
K4_FIELDS = ("assembly", "assembly_name", "prior", "hop", "via", "fitted", "fitted_note", "signs_yes", "also_check",
             "folded")

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


class Settings:
    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        self.old = {k: getattr(settings, k) for k in self.values}
        for k, v in self.values.items():
            setattr(settings, k, v)

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(settings, k, v)


class Logged(logging.Handler):
    def __init__(self, name: str):
        super().__init__(logging.WARNING)
        self.logger = logging.getLogger(name)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def __enter__(self):
        self.logger.addHandler(self)
        return self

    def __exit__(self, *exc):
        self.logger.removeHandler(self)


def item(part: str, *tokens, **more) -> dict:
    return {"part": part, "condition_tokens": list(tokens), "ai_confidence": "High", **more}


def parts(found) -> list[str]:
    return [it["part"] for it in found]


def strip(found) -> list[dict]:
    """The items without the K4 fields (what L1 would give)."""
    return [{k: v for k, v in it.items() if k not in K4_FIELDS} for it in found]


# ---------------------------------------------------------------- maps (made up from L1, never from a case)

CONVERTED = car_map.from_l1(L1)
LOOKUP = car_map.review_lookup(CONVERTED)
CONVERTED_TABLE = car_map.links_table(CONVERTED)


def made_up_map() -> dict:
    """from_l1(L1), then: priors on two rear-bumper relations; the front
    bumper's and the grille's relations in one "front-end" assembly; the
    fog lamps "if fitted"; a relation to a new name (the absorber, min_hit
    heavy) and one gated to hatches (the bracket) for the "also check"
    words. Version K4T."""
    cm = copy.deepcopy(CONVERTED)
    cm["version"] = "K4T"
    by_id = {a["id"]: a for a in cm["assemblies"]}
    front, grille = by_id["front-bumper"], by_id["front-grille"]
    front["id"], front["name"] = "front-end", "Front end assembly"
    front["anchors"] = ["Front Bumper", "Front Grille"]
    have = {m["part"] for m in front["members"]}
    front["members"] += [m for m in grille["members"] if m["part"] not in have]
    front["members"].append({"part": "Front Bumper Absorber", "role": "absorber", "hidden": True, "strip_down": True,
                             "new": True})
    front["members"].append({"part": "{side} Front Bumper Bracket", "role": "mount", "hidden": True,
                             "strip_down": True, "new": True, "body": ["hatch"]})
    cm["assemblies"] = [a for a in cm["assemblies"] if a["id"] != "front-grille"]
    for member in front["members"]:
        if member["part"] == "{side} Fog Lamp":
            member["fitted"] = "if_fitted"
    for rel in cm["relations"]:
        if rel["assembly"] in ("front-bumper", "front-grille"):
            rel["assembly"] = "front-end"
        if rel["id"] == "rear-bumper>reversing-camera":
            rel["prior"] = {"rank": 1, "source": "made up for the test"}
        if rel["id"] == "rear-bumper>rear-number-plate":
            rel["prior"] = {"rank": 5, "source": "made up for the test"}
    cm["relations"].append({"id": "front-bumper>absorber", "assembly": "front-end", "parent": "Front Bumper",
                            "child": "Front Bumper Absorber", "type": "behind", "side": "none", "min_hit": "heavy",
                            "trigger_tokens": [], "reason": "Sits behind the cover.", "sources": ["test"]})
    cm["relations"].append({"id": "front-bumper>bracket", "assembly": "front-end", "parent": "Front Bumper",
                            "child": "{side} Front Bumper Bracket", "type": "behind", "side": "impact",
                            "min_hit": "medium", "trigger_tokens": [], "reason": "Holds the cover's end.",
                            "sources": ["test"]})
    return cm


def rule_ids(table) -> set[str]:
    return {link["id"] for link in table["links"]}


# ---------------------------------------------------------------- settings


def settings_section() -> None:
    fields = config.Settings.model_fields
    check((fields["intake_links_order"].default, fields["intake_links_assembly_max"].default,
           fields["intake_links_stripdown_cap"].default, fields["intake_links_stripdown_max"].default)
          == ("table", 4, "in", 12), "defaults: table, 4, in, 12 (today's order and caps)")
    check(fields["intake_car_map"].default == "off", "intake_car_map still off by default")
    check(links.options() == links.Options() and links.Options().assembly_max == links.MAX_PER_PARENT,
          "links.options() at the defaults is Options() (per group = L1's per parent)")
    custom = types.SimpleNamespace(intake_links="on", intake_links_max=5, intake_links_types="attached,behind",
                                   intake_links_side="strict", intake_links_unknown_hit="heavy",
                                   intake_links_intact="drop", intake_links_catalogue=True)
    check(links.options(custom) == links.Options("on", 5, ("attached", "behind"), "strict", "heavy", "drop", True),
          "a source without the four new names: their defaults")
    custom.intake_links_order, custom.intake_links_assembly_max = "prior", 2
    custom.intake_links_stripdown_cap, custom.intake_links_stripdown_max = "out", 6
    opts = links.options(custom)
    check((opts.order, opts.assembly_max, opts.stripdown_cap, opts.stripdown_max) == ("prior", 2, "out", 6),
          "links.options reads the four new settings")
    check(config.normal_links_order(None) == "table" and config.normal_links_order(" Prior ") == "prior"
          and config.normal_stripdown_cap("") == "in" and config.normal_stripdown_cap("OUT") == "out",
          "parsers: blank is the default, any letter case")
    bad = []
    for name, value in (("intake_links_order", "random"), ("intake_links_stripdown_cap", "folded"),
                        ("intake_links_assembly_max", "-1"), ("intake_links_stripdown_max", "many")):
        try:
            config.Settings(**{name: value})
            bad.append(name)
        except Exception:
            pass
    check(bad == [], "a bad value stops the app at start-up", bad)
    check(config.Settings(intake_links_assembly_max="3", intake_links_stripdown_max="0").intake_links_assembly_max == 3,
          "counts parsed as contract 10's (0 or more)")
    compose = (ROOT.parent.parent / "infra" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    want = {"INTAKE_CAR_MAP": "off", "INTAKE_LINKS_ORDER": "table", "INTAKE_LINKS_ASSEMBLY_MAX": "4",
            "INTAKE_LINKS_STRIPDOWN_CAP": "in", "INTAKE_LINKS_STRIPDOWN_MAX": "12"}
    lines = {name: [ln for ln in compose.splitlines() if ln.startswith(f"    - {name}=")] for name in want}
    check(all(lines[n] == [f"    - {n}=${{{n}:-{v}}}"] for n, v in want.items()),
          "docker-compose.prod.yml passes each through once, in the shared env block, with config's default", lines)
    anchor = compose.index("x-bot-service: &bot-service")
    services = compose.index("\nservices:")
    check(all(anchor < compose.index(f"- {n}=") < services for n in want),
          "... inside the x-bot-service anchor both containers share")


# ---------------------------------------------------------------- defaults == master


def _master_links():
    """master's links.py, as deployed, exec'd as its own module (its
    imports are this checkout's, unchanged since)."""
    src = subprocess.run(["git", "-C", str(ROOT), "show", f"{MASTER}:apps/bot-service/app/intake/links.py"],
                         capture_output=True, text=True, encoding="utf-8")
    if src.returncode != 0:
        return None
    module = types.ModuleType("links_master")
    module.__file__ = links.__file__
    exec(compile(src.stdout, f"{MASTER}:app/intake/links.py", "exec"), module.__dict__)
    return module


def _sweep_cases():
    """Made-up lists: every L1 parent (both sides and undetermined) at a few
    hit words, multi-part ends, photos, intact reads, dismissals."""
    names = []
    for link in L1["links"]:
        for name, _ in links._expand(link["parent"]):
            names += [name] + ([unsided_part(name)] if unsided_part(name) else [])
    names = list(dict.fromkeys(names))
    words = ([], ["Grazed"], ["Dented"], ["Bent"], ["Crumpled"], ["Jammed"])
    cases = [([item(n, *w)], [], [], {}, ()) for n in names for w in words]
    photos = [{"n": 1, "kind": "damage", "area": "rear", "quality": "good", "damage": True, "part": "Rear Bumper Beam"},
              {"n": 2, "kind": "damage", "area": "rear-left", "quality": "good", "damage": True, "part": "Left Tail Lamp"},
              {"n": 3, "kind": "damage", "area": "front", "quality": "good", "damage": True, "part": "Front Grille"},
              {"n": 4, "kind": "damage", "area": "underbody", "quality": "good", "damage": True, "part": None}]
    rear = [item("Rear Bumper", "Crumpled", severity="Severe"), item("Boot Lid", "Dented"),
            item("Left Rear Quarter Panel", "Buckled", seen_in=["P01", "P02"]), item("Left Tail Lamp", "Cracked")]
    front = [item("Front Bumper", "Deformed"), item("Left Front Fender", "Buckled"),
             item("Right Front Fender", "Dented", side_inferred=True), item("Bonnet", "Bent"), item("Front Grille")]
    sides = [item("Left Front Door", "Dented"), item("Left Rear Door", "Buckled"), item("Right Front Door", "Bent"),
             item("Door (side undetermined)", "Dented")]
    intact = {"zone:rear": ["Rear Number Plate", "Left Boot Lid Lamp"], "zone:front": ["Front Number Plate"]}
    others = [{"part": "Rear Bumper Reflector (side undetermined)", "key": "Rear Bumper Reflector (side undetermined)",
               "source": "zone"}, {"part": "Radiator", "key": "Radiator", "source": "zone"}]
    for listed in (rear, front, sides, rear + front, front + sides):
        cases += [(listed, [], [], {}, ()), (listed, others, photos, intact, ()),
                  (listed, others, photos, intact, ("Rear Bumper Beam", "Left Headlamp", "Front Bumper Beam"))]
    return cases


def master_section() -> None:
    master = _master_links()
    if not check(master is not None, f"master's links.py ({MASTER}) read from git"):
        return
    variants = [ON, links.Options(mode="on", side="strict"), links.Options(mode="on", unknown_hit="light"),
                links.Options(mode="on", unknown_hit="heavy"), links.Options(mode="on", intact="drop"),
                links.Options(mode="on", intact="ignore"), links.Options(mode="on", types=("attached", "behind")),
                links.Options(mode="on", max_items=3), links.Options(mode="on", max_items=0),
                links.Options(mode="on", catalogue=True)]
    neighbours = {"Rear Bumper": [{"part": "Rear Bumper Beam", "diagram": "REAR BUMPER"},
                                  {"part": "Left Rear Wheel Arch Liner", "diagram": "REAR BUMPER"}],
                  "Front Bumper": [{"part": "Front Grille", "diagram": "GRILLE"}]}
    cases = _sweep_cases()
    n, differ = 0, []
    for opts in variants:
        mopts = master.Options(**{k: v for k, v in opts.__dict__.items() if k in master.Options.__dataclass_fields__})
        for listed, others, photos, intact, dismissed in cases:
            args = (copy.deepcopy(listed), copy.deepcopy(others), copy.deepcopy(photos), copy.deepcopy(intact), L1)
            mine = links.build_all(*args, opts, dismissed=dismissed, neighbours=neighbours)
            theirs = master.build_all(*copy.deepcopy(args[:4]), L1, mopts, dismissed=dismissed, neighbours=neighbours)
            n += 1
            if json.dumps(mine, sort_keys=False) != json.dumps(theirs, sort_keys=False):
                differ.append((parts(listed), opts))
    check(not differ and n > 2000, f"build_all == master's on {n} builds (every field, in order, the joins too)",
          differ[:3])
    relinks, differ = 0, []
    for listed, others, photos, intact, dismissed in cases[::7]:
        for added in listed:
            case = {"damage_summary": copy.deepcopy(listed),
                    "second_look": {"items": copy.deepcopy(others), "dismissed": list(dismissed), "decisions": []}}
            for opts in (ON, links.Options(mode="on", max_items=4)):
                mopts = master.Options(**{k: v for k, v in opts.__dict__.items()
                                          if k in master.Options.__dataclass_fields__})
                mine = links.for_parent(copy.deepcopy(added), copy.deepcopy(case), photos, L1, opts, zone_intact=intact)
                theirs = master.for_parent(copy.deepcopy(added), copy.deepcopy(case), photos, L1, mopts,
                                           zone_intact=intact)
                relinks += 1
                if mine != theirs:
                    differ.append(added["part"])
    check(not differ and relinks > 50, f"for_parent == master's on {relinks} re-links", differ[:3])
    sample = links.build([item("Rear Bumper", "Crumpled")], [], [], {}, L1, ON)
    check(sample and not any(k in it for it in sample for k in K4_FIELDS),
          "no K4 field on a linked part with the defaults (nothing new in a draft)")


# ---------------------------------------------------------------- each setting


def settings_effects() -> None:
    bumper = [item("Rear Bumper", "Crumpled", severity="Severe")]
    plain = links.build(bumper, [], [], {}, L1, ON)
    by_bucket = {}
    for it in plain:
        by_bucket.setdefault(it["bucket"], []).append(it["part"])

    # Order: prior (a made-up map: camera 1, plate 5).
    cm = made_up_map()
    table, look = car_map.links_table(cm), car_map.review_lookup(cm)
    likely = lambda found: [it["part"] for it in found if it["bucket"] == "likely"]  # noqa: E731
    by_table = links.build(bumper, [], [], {}, table, ON, info=look)
    by_prior = links.build(bumper, [], [], {}, table, links.Options(mode="on", order="prior"), info=look)
    roomy = links.Options(mode="on", order="prior", assembly_max=20, max_items=40)
    by_prior_all = links.build(bumper, [], [], {}, table, roomy, info=look)
    check(likely(by_table)[:1] == ["Rear Number Plate"] and likely(by_prior)[0] == "Reversing Camera"
          and likely(by_prior_all)[-1] == "Rear Number Plate",
          "order prior: rank 1 first in its bucket, rank 5 after the unranked (None = 3)",
          (likely(by_table), likely(by_prior_all)))
    check("Rear Number Plate" not in likely(by_prior) and len(likely(by_prior)) == 4,
          "... so inside the cap of 4 a group, rank 5 gives way to an unranked one", likely(by_prior))
    check([it["bucket"] for it in by_prior] == sorted((it["bucket"] for it in by_prior), key=links.BUCKETS.index),
          "... the bucket still comes first")
    check(links.build(bumper, [], [], {}, L1, links.Options(mode="on", order="prior")) == plain,
          "order prior on L1 (no priors): today's order")
    prior_info = next(it for it in by_prior if it["part"] == "Reversing Camera")
    unranked = next(it for it in by_table if it["part"] == "Parking Sensor")
    check(prior_info.get("prior") == 1 and "prior" in unranked and unranked["prior"] is None
          and SecondLookItem.model_validate(unranked).model_dump().get("prior", "absent") == "absent",
          "the rank is on the item (None when the relation has none, left out of the dump)")

    # Per-group cap.
    for n in (1, 2, 3):
        found = links.build(bumper, [], [], {}, L1, links.Options(mode="on", assembly_max=n))
        per = {}
        for it in found:
            per[it["bucket"]] = per.get(it["bucket"], 0) + 1
        check(per and max(per.values()) <= n and all(per[b] == min(n, len(by_bucket[b])) for b in per),
              f"assembly_max {n}: at most {n} per group and bucket", per)
    check(links.build(bumper, [], [], {}, L1, links.Options(mode="on", assembly_max=0)) == [],
          "assembly_max 0: none")

    # Strip-down out.
    out = links.build(bumper, [], [], {}, L1, links.Options(mode="on", stripdown_cap="out", max_items=3))
    shown = [it for it in out if not it.get("folded")]
    folded = [it for it in out if it.get("folded")]
    check(len(shown) == 3 and all(it["bucket"] != "strip_down" for it in shown)
          and [it["part"] for it in folded] == by_bucket["strip_down"],
          "stripdown_cap out: max_items counts seen + likely only; the strip-down block after them, folded",
          (parts(shown), parts(folded)))
    check(not any("folded" in it for it in plain) and all(it["folded"] is True for it in folded),
          "folded only on the block's parts, never with the cap in")
    inside = links.build(bumper, [], [], {}, L1, links.Options(mode="on", max_items=3))
    check(len(inside) == 3 and not any(it["bucket"] == "strip_down" for it in inside),
          "stripdown_cap in (today): the 12 counts every bucket, strip-down cut first")
    few = links.build(bumper, [], [], {}, L1, links.Options(mode="on", stripdown_cap="out", stripdown_max=2))
    check([it["part"] for it in few if it.get("folded")] == by_bucket["strip_down"][:2],
          "stripdown_max caps the block", parts(few))
    none = links.build(bumper, [], [], {}, L1, links.Options(mode="on", stripdown_cap="out", stripdown_max=0))
    check(not any(it.get("folded") for it in none) and parts(none) == by_bucket.get("seen", []) + by_bucket["likely"],
          "stripdown_max 0: no block, the rest as before")
    check(parts(links.build(bumper, [], [], {}, L1, links.Options(mode="on", stripdown_cap="out")))
          == parts(plain), "with room for all: the same parts in the same order, the block last")

    # Hop 2 (K3b's items): the block unless seen.
    def cand(part, bucket, hop=None, group="g"):
        return {"key": part, "part": part, "bucket": bucket, "linked_from": [group], **({"hop": hop} if hop else {})}
    kept = [cand("A", "likely"), cand("B", "likely", hop=2), cand("C", "seen", hop=2), cand("D", "strip_down")]
    placed = links._cap(copy.deepcopy(kept), links.Options(mode="on", stripdown_cap="out"))
    check([it["part"] for it in placed] == ["A", "C", "B", "D"] and [it.get("folded", False) for it in placed]
          == [False, False, True, True], "a second hop's part goes in the block unless a photo shows it",
          [(it["part"], it.get("folded")) for it in placed])
    capped = links._cap([cand(p, "likely", hop=2) for p in "ABCDE"], links.Options(mode="on", assembly_max=2))
    check(len(capped) == 2, "... and counts as strip-down in its group's cap with the cap in")

    # for_parent rooms.
    case = {"damage_summary": bumper, "second_look": {"items": [], "dismissed": [], "decisions": []}}
    opened = links.for_parent(bumper[0], case, [], L1, links.Options(mode="on", stripdown_cap="out", max_items=2))
    check(len([it for it in opened if not it.get("folded")]) == 2 and any(it.get("folded") for it in opened),
          "for_parent, out: max_items for the others, the block its own room", parts(opened))
    held_case = copy.deepcopy(case)
    held_case["second_look"]["items"] = [dict(it) for it in opened]
    boot = item("Rear End Panel", "Crumpled")
    held_case["damage_summary"] = bumper + [boot]
    again = links.for_parent(boot, held_case, [], L1, links.Options(mode="on", stripdown_cap="out", max_items=2))
    check(not [it for it in again if not it.get("folded")],
          "for_parent, out: no room left beside the open ones (not folded)", parts(again))
    # An assembly's open parts count in its cap (car map facts).
    grille = item("Front Grille", "Crumpled")
    front_case = {"damage_summary": [item("Front Bumper", "Crumpled")],
                  "second_look": {"items": [], "dismissed": [], "decisions": []}}
    first = links.for_parent(front_case["damage_summary"][0], front_case, [], table, ON, info=look)
    front_case["second_look"]["items"] = first
    front_case["damage_summary"].append(grille)
    second = links.for_parent(grille, front_case, [], table, ON, info=look)
    count = {}
    for it in first + second:
        if it.get("assembly") == "front-end":
            count[it["bucket"]] = count.get(it["bucket"], 0) + 1
    check(count and max(count.values()) <= 4, "for_parent with facts: the assembly's open parts count in its cap",
          count)


# ---------------------------------------------------------------- the car map's facts


def facts_section() -> None:
    check(car_map.validate(CONVERTED) == [] and links.validate_table(CONVERTED_TABLE) == [],
          "from_l1(L1) validates and compiles")
    door = next(link["id"] for link in L1["links"] if link["parent"] == "{side} Front Door")
    check(LOOKUP("L1:rear-bumper>beam") == {**car_map.relation_info(CONVERTED, "L1:rear-bumper>beam"), "sided": False}
          and LOOKUP(f"L1:{door}") == {**car_map.relation_info(CONVERTED, f"L1:{door}"), "sided": True},
          "review_lookup = relation_info + sided (every anchor a {side} name)")
    check(LOOKUP("K9:rear-bumper>beam") is None and LOOKUP(None) is None and LOOKUP(3) is None,
          "review_lookup: another version, no rule, junk -> None")
    check(car_map.review_lookup(None) is None and car_map.review_lookup({"version": "X", "relations": [1, "a"]})("X:a")
          is None, "review_lookup: no map -> None; a junk map -> a lookup that knows nothing")
    sweep_listed = [[item("Rear Bumper", "Crumpled", severity="Severe"), item("Boot Lid", "Dented")],
                    [item("Front Bumper", "Deformed"), item("Left Front Fender", "Buckled"),
                     item("Right Front Fender", "Buckled")],
                    [item("Left Front Door", "Dented"), item("Right Front Door", "Bent")],
                    [item("Door (side undetermined)", "Dented")]]
    same, grouped = True, True
    for listed in sweep_listed:
        plain = links.build(copy.deepcopy(listed), [], [], {}, L1, ON)
        faced = links.build(copy.deepcopy(listed), [], [], {}, CONVERTED_TABLE, ON, info=LOOKUP)
        same = same and strip(faced) == plain
        grouped = grouped and all(it.get("assembly") and it.get("assembly_name") and it.get("hop") == 1
                                  for it in faced)
    check(same, "from_l1 with its facts: the very items L1 gives, plus the group fields")
    check(grouped, "every item has its assembly, its name and hop 1")
    doors = links.build([item("Left Front Door", "Dented"), item("Right Front Door", "Dented")], [], [], {},
                        CONVERTED_TABLE, ON, info=LOOKUP)
    groups = sorted({it["assembly"] for it in doors})
    check(groups == ["front-door.left", "front-door.right"],
          "a sided assembly is one group per side (left and right doors apart, as L1's two parents)", groups)
    bumper = links.build([item("Front Bumper", "Crumpled")], [], [], {}, CONVERTED_TABLE, ON, info=LOOKUP)
    check({it["assembly"] for it in bumper} == {"front-bumper"},
          "an unsided assembly is one group, its {side} children too")
    check(links.group_of({"linked_from": ["Rear Bumper"]}) == "Rear Bumper"
          and links.group_of({"assembly": "rear-bumper", "linked_from": ["Rear Bumper"]}) == "rear-bumper",
          "group_of: the assembly, else the listed part")

    cm = made_up_map()
    table, look = car_map.links_table(cm), car_map.review_lookup(cm)
    check(links.validate_table(table) == [] and "front-bumper>absorber" not in rule_ids(table),
          "the made-up map compiles; the names-off table leaves the new-name relations out")
    two = [item("Front Bumper", "Crumpled"), item("Front Grille", "Crumpled")]
    merged = links.build(copy.deepcopy(two), [], [], {}, table, ON, info=look)
    per = {}
    for it in merged:
        if it.get("assembly") == "front-end":
            per[it["bucket"]] = per.get(it["bucket"], 0) + 1
    l1_per = {}
    for it in links.build(copy.deepcopy(two), [], [], {}, L1, ON):
        if it["linked_from"][0] in ("Front Bumper", "Front Grille"):
            l1_per[it["bucket"]] = l1_per.get(it["bucket"], 0) + 1
    check(per and max(per.values()) <= 4 and sum(per.values()) < sum(l1_per.values()),
          "two listed parents in one assembly share its cap of 4 per bucket", (per, l1_per))
    check({it["assembly_name"] for it in merged if it.get("assembly") == "front-end"} == {"Front end assembly"},
          "the group's name for its header")
    fog = next((it for it in merged if "Fog Lamp" in it["part"]), None)
    plate = next((it for it in merged if it["part"] == "Front Number Plate"), None)
    check(fog is not None and fog.get("fitted") == "if_fitted" and plate is not None and "fitted" not in plate,
          "\"if fitted\" only on an option some cars lack", (fog, plate))

    # Also check: the absorber (heavy) and the bracket (hatch only).
    levels = links.levels_of(two, [], ON)
    words = car_map.add_also_check(copy.deepcopy(merged), cm, levels)
    check({tuple(it.get("also_check") or ()) for it in words if it.get("assembly") == "front-end"}
          == {("absorber", "side brackets")}, "also check: the new parts' words, once per group, on each of its items",
          {tuple(it.get("also_check") or ()) for it in words})
    check(not any(it.get("also_check") for it in words if it.get("assembly") != "front-end"),
          "... only on the group whose relations they are")
    sedan = car_map.add_also_check(copy.deepcopy(merged), cm, levels, body_type="Sedan")
    check({tuple(it.get("also_check") or ()) for it in sedan if it.get("assembly") == "front-end"} == {("absorber",)},
          "... a body gate that excludes this car's body drops its words")
    light = [item("Front Bumper", "Dented")]
    lit = links.build(copy.deepcopy(light), [], [], {}, table, ON, info=look)
    lit = car_map.add_also_check(lit, cm, links.levels_of(light, [], ON))
    check({tuple(it.get("also_check") or ()) for it in lit} == {("side brackets",)},
          "... a medium hit: not the heavy-only absorber", {tuple(it.get("also_check") or ()) for it in lit})
    check(car_map.add_also_check(copy.deepcopy(merged), cm, levels, names=True) == merged
          and car_map.add_also_check(copy.deepcopy(merged), None, levels) == merged
          and car_map.add_also_check(copy.deepcopy(merged), cm, levels, new_names={"names": []}) == merged,
          "... nothing with the names on, no map, or no words")
    check(car_map.add_also_check([{"assembly": 5}, "junk", {"assembly": "front-end", "linked_from": None}], cm,
                                 {"Front Bumper": "heavy"}) is not None, "... junk items never raise")

    # A bad answer from the lookup never breaks a build.
    def broken(rule):
        raise KeyError(rule)
    with Logged("app.intake.links"):
        found = links.build([item("Rear Bumper", "Crumpled")], [], [], {}, L1, ON, info=broken)
    check(strip(found) == found and found == links.build([item("Rear Bumper", "Crumpled")], [], [], {}, L1, ON),
          "an info lookup that raises: the L1 items, no group fields")
    odd = links.build([item("Rear Bumper", "Crumpled")], [], [], {}, L1, ON,
                      info=lambda rule: {"assembly": "x", "prior": True, "hop": 7, "fitted": "maybe", "sided": True})
    check(all(it["assembly"] == "x" and it["prior"] is None and it["hop"] is None and "fitted" not in it for it in odd),
          "odd facts are cleaned (a prior 1-5, a hop 1 or 2, if_fitted only)")


# ---------------------------------------------------------------- the seam


def make_draft(rid: str, data: dict, ai_draft: dict | None = None) -> None:
    db = SessionLocal()
    try:
        db.add(Report(id=rid, status="intake", channel="dashboard", reporter_chat_id="test", data=data))
        db.add(IntakeSession(report_id=rid, stage="review", reporter_notes="",
                             ai_draft=ai_draft if ai_draft is not None else {"zone_suggestions": [],
                                                                             "zone_reads": ["overall"]}))
        db.commit()
    finally:
        db.close()


def stored(rid: str) -> tuple[dict, dict]:
    db = SessionLocal()
    try:
        session = db.get(IntakeSession, rid)
        return copy.deepcopy(db.get(Report, rid).data), copy.deepcopy(session.ai_draft if session else None)
    finally:
        db.close()


SKIPPED = {"status": "skipped", "note": "The second look is switched off.", "items": [], "seconds": 0.1}
CASE = {"damage_summary": [item("Rear Bumper", "Crumpled", severity="Severe"), item("Left Front Door", "Dented")],
        "damaged_parts": ["Rear Bumper", "Left Front Door"],
        "vehicle_info": {"make": "Testmake", "model": "Testmodel"}}


def seam_section() -> None:
    real_map = car_map.MAP
    check(car_map.link_tables(L1, {}, mode="off").shown is L1 and car_map.link_tables(L1, {}, mode="off").shown_info
          is None, "link_tables off: L1 itself, no facts")
    with Logged("app.intake.car_map") as log:
        none_on = car_map.link_tables(L1, {}, mode="on", car_map=None)
    check(none_on.shown is L1 and none_on.shown_info is None and log.records,
          "on without a map: L1, logged", [r.getMessage() for r in log.records])
    on = car_map.link_tables(L1, {}, mode="on", car_map=CONVERTED)
    check(on.shown == CONVERTED_TABLE and on.shown_info is not None and on.shadow is None,
          "on with a map: its table and its facts")
    sh = car_map.link_tables(L1, {}, mode="shadow", car_map=CONVERTED)
    check(sh.shown is L1 and sh.shown_info is None and sh.shadow == CONVERTED_TABLE and sh.shadow_info is not None,
          "shadow: L1 shown, the map's table and facts for the shadow")
    bad = copy.deepcopy(CONVERTED)
    bad["relations"][0]["type"] = "glued"
    with Logged("app.intake.car_map") as log:
        broken = car_map.link_tables(L1, {}, mode="on", car_map=bad)
    check(broken.shown is L1 and broken.shown_info is None and log.records,
          "on with a map whose table fails the check: L1, logged")
    path = Path(_tmp, "car_map.json")
    path.write_text("{not json", encoding="utf-8")
    with Logged("app.intake.car_map"):
        check(car_map.load(path) is None, "a car_map.json that can't be read loads as no map (L1 then)")
    with Logged("app.intake.car_map"):
        junk = car_map.link_tables(L1, {}, mode="on", car_map={"version": "J", "relations": "x"})
    check(junk.shown is L1, "a junk map never raises: L1")

    # _store: off never touches the car map.
    calls = []
    real_tables = car_map.link_tables

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real_tables(*args, **kwargs)

    car_map.link_tables = spy
    try:
        make_draft("k4-off", copy.deepcopy(CASE))
        with Settings(intake_links="on"):
            second_look._store("k4-off", dict(SKIPPED))
        off_data, off_draft = stored("k4-off")
        off_items = off_data["second_look"]["items"]
        check(calls == [] and off_items and not any(k in it for it in off_items for k in K4_FIELDS)
              and "car_map_suggestions" not in off_draft,
              "intake_car_map off: the car map is never asked; no group field, no shadow list")

        car_map.MAP = CONVERTED
        make_draft("k4-on", copy.deepcopy(CASE))
        with Settings(intake_links="on", intake_car_map="on"):
            second_look._store("k4-on", dict(SKIPPED))
        on_data, on_draft = stored("k4-on")
        on_items = on_data["second_look"]["items"]
        check(calls and [strip([it])[0] for it in on_items] == off_items,
              "on (from_l1): the same suggestions as off, plus the group fields")
        check(all(it.get("assembly") for it in on_items if it.get("source") == "link")
              and {it["assembly"] for it in on_items if it.get("source") == "link"} >= {"rear-bumper", "front-door.left"},
              "... each linked part grouped by its assembly")
        check("car_map_suggestions" not in on_draft, "... no shadow list when on")

        make_draft("k4-shadow", copy.deepcopy(CASE))
        with Settings(intake_links="on", intake_car_map="shadow"):
            second_look._store("k4-shadow", dict(SKIPPED))
        sh_data, sh_draft = stored("k4-shadow")
        check(sh_data["second_look"]["items"] == off_items,
              "shadow: the shown suggestions exactly as off")
        mapped = sh_draft.get("car_map_suggestions")
        check(isinstance(mapped, list) and mapped and all(it.get("assembly") for it in mapped),
              "... the map's linked parts on ai_draft[\"car_map_suggestions\"], grouped")

        make_draft("k4-shadow-links", copy.deepcopy(CASE))
        with Settings(intake_links="shadow", intake_car_map="shadow"):
            second_look._store("k4-shadow-links", dict(SKIPPED))
        _, both = stored("k4-shadow-links")
        check(isinstance(both.get("link_suggestions"), list) and both["link_suggestions"]
              and isinstance(both.get("car_map_suggestions"), list) and both["car_map_suggestions"],
              "intake_links shadow too: both lists on the AI draft")

        car_map.MAP = None
        make_draft("k4-nomap", copy.deepcopy(CASE))
        with Settings(intake_links="on", intake_car_map="on"), Logged("app.intake.car_map"):
            second_look._store("k4-nomap", dict(SKIPPED))
        nomap, _ = stored("k4-nomap")
        check(nomap["second_look"]["items"] == off_items, "on with no car_map.json: L1 exactly, as off")

        car_map.MAP = bad
        make_draft("k4-badmap", copy.deepcopy(CASE))
        with Settings(intake_links="on", intake_car_map="on"), Logged("app.intake.car_map"):
            second_look._store("k4-badmap", dict(SKIPPED))
        badmap, _ = stored("k4-badmap")
        check(badmap["second_look"]["items"] == off_items, "on with a bad map: L1 exactly, as off")

        car_map.MAP = CONVERTED

        def explode(*args, **kwargs):
            raise RuntimeError("no tables")
        car_map.link_tables = explode
        make_draft("k4-explode", copy.deepcopy(CASE))
        with Settings(intake_links="on", intake_car_map="on"), Logged("app.ai.second_look"):
            second_look._store("k4-explode", dict(SKIPPED))
        exploded, _ = stored("k4-explode")
        check(isinstance(exploded.get("second_look"), dict),
              "the car map raising: the read's suggestions are stored all the same")
        car_map.link_tables = spy

        # The relink after an add, through the seam.
        look = SecondLook.model_validate(on_data["second_look"]).model_dump()
        boot = item("Boot Lid", "Buckled")
        data = {**copy.deepcopy(CASE), "damage_summary": CASE["damage_summary"] + [boot]}
        with Settings(intake_links="on", intake_car_map="on"):
            relinked = suggestions.relinked(boot, data, look)
        with Settings(intake_links="on"):
            relinked_off = suggestions.relinked(boot, data, SecondLook.model_validate(off_data["second_look"]).model_dump())
        check(relinked and all(it.get("assembly") == "boot-lid" for it in relinked)
              and relinked_off and not any("assembly" in it for it in relinked_off)
              and [strip([it])[0] for it in relinked] == relinked_off,
              "suggestions.relinked: on -> the map's table and group fields; off -> as before",
              (parts(relinked), parts(relinked_off)))

        # The linked check's areas come from the table shown.
        calls.clear()
        make_draft("k4-check", {**copy.deepcopy(CASE), "second_look": on_data["second_look"]})
        with Settings(intake_links="on", intake_car_map="on", intake_link_check="lite"):
            link_check._plan("k4-check", "on", 2)
        check(calls and calls[-1].get("mode") == "on", "the linked check's plan asks the seam for its areas")
        calls.clear()
        with Settings(intake_links="on", intake_link_check="lite"):
            link_check._plan("k4-check", "on", 2)
        check(calls == [], "... not with the car map off")
    finally:
        car_map.link_tables = real_tables
        car_map.MAP = real_map


# ---------------------------------------------------------------- the draft


def draft_section() -> None:
    old = links.build([item("Rear Bumper", "Crumpled")], [], [], {}, L1, ON)
    old_items = [SecondLookItem.model_validate(it).model_dump() for it in old]
    check(not any(k in it for it in old_items for k in K4_FIELDS),
          "a linked part without the facts dumps no new field")
    look = {"status": "ok", "items": old_items, "dismissed": [], "decisions": []}
    again = SecondLook.model_validate(json.loads(json.dumps(look))).model_dump()
    check(again["items"] == old_items, "an old draft loads and dumps unchanged")
    check(set(K4_FIELDS) <= set(LINK_SUGGESTION_DEFAULTS), "the new fields are all left out at their defaults")
    full = {**old_items[0], "assembly": "rear-bumper", "assembly_name": "Rear bumper assembly", "prior": 2, "hop": 2,
            "via": "Rear Bumper Beam", "fitted": "if_fitted", "fitted_note": "this model lists it",
            "signs_yes": [{"id": "rb-pushed", "ask": "Is the bumper pushed in?", "photo": "@ph1"}],
            "also_check": ["absorber"], "folded": True}
    dumped = SecondLookItem.model_validate(full).model_dump()
    check(all(dumped[k] == full[k] for k in K4_FIELDS), "each new field round-trips")
    relabelled = suggestions.relabel_item(dumped, {"ph1": 6})
    check(relabelled["signs_yes"] == [{"id": "rb-pushed", "ask": "Is the bumper pushed in?", "photo": "P07"}],
          "filing: a yes sign's photo becomes its P-number", relabelled["signs_yes"])
    gone = suggestions.relabel_item(dumped, {})
    check(gone["signs_yes"][0]["photo"] is None and gone["signs_yes"][0]["id"] == "rb-pushed",
          "... a deleted photo's gone, the sign kept")
    check("signs_yes" not in suggestions.relabel_item(old_items[0], {}), "... an old item gets no signs at filing")
    ai_draft = {"car_map_suggestions": [dumped], "link_suggestions": [dumped]}
    filed = filing.relabel_ai_draft(ai_draft, {"ph1": 6})
    check(filed["car_map_suggestions"][0]["signs_yes"][0]["photo"] == "P07"
          and "car_map_suggestions" in filing.AI_DRAFT_PHOTO_LISTS, "filing relabels the shadow list too")
    added = suggestions.item_from_suggestion(dumped)
    check(not any(k in added for k in K4_FIELDS) and added["linked_from"] == dumped["linked_from"],
          "an added part keeps where it hangs off, none of the group fields")


# ---------------------------------------------------------------- the replay


def replay_section() -> None:
    code = (
        "import sys, json, io, contextlib, importlib.util\n"
        f"sys.path.insert(0, {str(ROOT)!r}); sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
        f"spec = importlib.util.spec_from_file_location('rl', {str(ROOT / 'scripts' / 'bench' / 'replay_links.py')!r})\n"
        "rl = importlib.util.module_from_spec(spec); spec.loader.exec_module(rl)\n"
        "args = json.loads(sys.argv[1])\n"
        "out = io.StringIO()\n"
        "try:\n"
        "    with contextlib.redirect_stdout(out):\n"
        "        rc = rl.main(args)\n"
        "except SystemExit as exc:\n"
        "    rc = exc.code\n"
        "print(json.dumps({'rc': rc}))\n"
    )
    work = Path(_tmp, "replay")
    work.mkdir(exist_ok=True)
    key = {"cases": [{"name": "MADE UP", "folder": "x", "parts": [{"part": "Rear Bumper"}, {"part": "Boot Lid"},
                                                                  {"part": "Rear Bumper Beam", "hidden": True}]}]}
    run = {"case": "MADE UP", "rep": 1, "parts": {"found": 1}, "drafted_parts": ["Rear Bumper", "Boot Lid"],
           "drafted_items": [item("Rear Bumper", "Crumpled", severity="Severe"), item("Boot Lid", "Buckled")],
           "suggestions": [], "sorted_photos": []}
    (work / "key.json").write_text(json.dumps(key), encoding="utf-8")
    (work / "res.json").write_text(json.dumps({"label": "made up", "runs": [run]}), encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    def replay(*flags):
        target = work / f"out{len(list(work.iterdir()))}.json"
        argv = [str(work / "res.json"), "--key", str(work / "key.json"), "--out", str(target), *flags]
        done = subprocess.run([sys.executable, "-c", code, json.dumps(argv)], capture_output=True, text=True,
                              encoding="utf-8", env=env, cwd=str(ROOT))
        rc = json.loads(done.stdout.strip().splitlines()[-1])["rc"] if done.stdout.strip() else None
        doc = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
        return rc, doc, done.stderr

    rc, plain, err = replay()
    check(rc == 0 and plain and "review_screen" not in plain and set(plain["options"]) ==
          {"mode", "max_items", "types", "side", "unknown_hit", "intact", "catalogue"}
          and all("review" not in r for r in plain["runs"])
          and all(set(row) == set(_ROW_FIELDS) for r in plain["runs"] for row in r["link_suggestions"]),
          "no K4 flag: today's options, rows and no review block", err[-300:])
    rc, k4, err = replay("--car-map", "from-l1", "--stripdown-cap", "out", "--links-order", "prior",
                         "--assembly-max", "3", "--stripdown-max", "2")
    review = (k4 or {}).get("review_screen") or {}
    metrics = ((k4 or {}).get("runs") or [{}])[0].get("review") or {}
    check(rc == 0 and review.get("car_map", "").startswith("from-l1") and review.get("links") == len(L1["links"])
          and k4["options"]["stripdown_cap"] == "out" and k4["options"]["assembly_max"] == 3 and not k4["frozen"],
          "the flags: the map, the knobs in the options, not the L1 headline", (rc, review, err[-300:]))
    check({"folded", "stripdown_shown", "cap_cut_seen_likely", "cap_cut_stripdown", "cap_hit", "group_cut", "groups",
           "group_max", "per_group"} <= set(metrics) and metrics["folded"] <= 2 and metrics["group_max"] >= 1
          and all(r.get("assembly") for r in k4["runs"][0]["link_suggestions"]),
          "... the review metrics per case-run (strip-down block, cap hits, items per group) and the group fields",
          metrics)
    rc_same, same, _ = replay("--car-map", "off")
    check(rc_same == 0 and [r["part"] for r in same["runs"][0]["link_suggestions"]]
          == [r["part"] for r in plain["runs"][0]["link_suggestions"]] and "review" in same["runs"][0],
          "--car-map off: L1's parts, with the review metrics")
    rc_l1map, l1map, _ = replay("--car-map", "from-l1")
    check(rc_l1map == 0 and [r["part"] for r in l1map["runs"][0]["link_suggestions"]]
          == [r["part"] for r in plain["runs"][0]["link_suggestions"]], "--car-map from-l1: L1's parts exactly")
    cm_path = work / "map.json"
    cm_path.write_text(json.dumps(made_up_map()), encoding="utf-8")
    rc_map, mapped, err = replay("--car-map", str(cm_path))
    check(rc_map == 0 and mapped["review_screen"]["version"] == "K4T" and mapped["review_screen"]["sha256_lf"],
          "--car-map PATH: that map, its version and sha256", err[-300:])
    bad = work / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    rc_bad, _, _ = replay("--car-map", str(bad))
    check(rc_bad == 2, "a map that doesn't load stops the replay (exit 2)", rc_bad)


def bench_section() -> None:
    import benchmark_intake as bi

    with Settings(intake_links="on"):
        plain = bi.ai_settings(settings)
    check(not any(k in plain for k in (*bi.K4_DEFAULTS, "intake_car_map", "car_map_version", "car_map_sha256")),
          "a benchmark run at the defaults records no K4 key (its ai_settings as before)")
    check(set(bi.K4_DEFAULTS) == {"intake_links_order", "intake_links_assembly_max", "intake_links_stripdown_cap",
                                  "intake_links_stripdown_max"}
          and all(config.Settings.model_fields[k].default == v for k, v in bi.K4_DEFAULTS.items()),
          "the benchmark's K4 defaults are config.py's")
    real_map = car_map.MAP
    try:
        car_map.MAP = None
        with Settings(intake_links="on", intake_links_order="prior", intake_links_stripdown_cap="out",
                      intake_car_map="on"):
            got = bi.ai_settings(settings)
        check(got.get("intake_links_order") == "prior" and got.get("intake_links_stripdown_cap") == "out"
              and "intake_links_assembly_max" not in got and got.get("intake_car_map") == "on"
              and got.get("car_map_version") is None and got.get("car_map_sha256") is None,
              "off their defaults: recorded; the car map's mode, and no version without a map", got)
    finally:
        car_map.MAP = real_map
    with Settings(intake_links="off", intake_links_order="prior"):
        check("intake_links_order" not in bi.ai_settings(settings), "linked parts off: nothing of them recorded")


_ROW_FIELDS = ("key", "part", "link_type", "link_rule", "hit", "bucket", "hidden", "needs_strip_down", "linked_from",
               "side_from", "intact_in", "seen_in", "truth")


def main() -> int:
    init_db()
    check(L1 is not None, "the link table loads")
    for name, fn in (
        ("settings: the four review knobs", settings_section),
        ("defaults: master's linked parts, field for field", master_section),
        ("each setting's effect", settings_effects),
        ("the car map's facts on the linked parts", facts_section),
        ("the seam: off, on, shadow, a bad map", seam_section),
        ("old drafts, filing, an added part", draft_section),
        ("the replay's K4 flags (0 calls)", replay_section),
        ("the benchmark records the K4 knobs off their defaults", bench_section),
    ):
        run_section(name, fn)
    print(f"\ntest_carmap_review: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
