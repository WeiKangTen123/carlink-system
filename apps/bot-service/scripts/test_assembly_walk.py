"""The assembly walk (plans/2026-11-18-car-knowledge/K1-design.md 5a;
app/intake/assembly_walk.py) -- offline: Gemini is a fake, call_structured
is recorded or answers a canned reply.

Covers:
  - settings, their parsers, compose's passthrough: off / one / 1 by default;
  - off is today: every request test_light_packs snapshots is byte for
    byte master's; the walk on with no map, a bad map or a map without
    signs is skipped (logged), never an error; a zone the map has no anchor
    for is asked nothing new;
  - on, with a small generic map (scripts/fixtures/car_map_walk.json):
    the paragraph and one line per anchor after the packs, before the
    closing line; `assembly_walk` after `damage` (panel = this zone's
    anchors, signs = their ids, photo = the labels sent or null); the run
    log's "+walk-W1" tag;
  - every parse rule: canonical panel, side from zone_side_seen, only
    damaged panels (Z5's undetermined twin), a sign of the panel's own
    assembly, the first answer, a yes without a sent photo, junk;
  - merging across zones: yes > no > cannot_see, whole wins, centre
    against corner is cannot_see; photos as "@<photo id>";
  - the zone stage and the pipeline end to end: ai_draft["assembly_walk"],
    the run log entry, and second_look's linked parts with the walk;
  - the code side: the hit raise (one / full, never lower, the end bonus
    after), the position gates, the bucket lift and signs_yes, the second
    hop (fires; not without a yes, a hop setting or the level; at most 4;
    side rules; never a third hop; the per-case room in for_parent);
  - with the walk off, or on but without a yes and with hit_at whole /
    cannot_see, links.build gives today's output on every call
    test_links.py makes (replayed);
  - the benchmark's flags, refusals and ai_settings.

    python scripts/test_assembly_walk.py

Parts, makes and photos are made up; the map is a small generic one.
"""
import contextlib
import copy
import io
import json
import logging
import os
import sys
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["SECOND_LOOK_ENABLED"] = "false"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"

# test_links first: its "links imports no AI client" is taken at its import.
import test_links as TL  # noqa: E402  (its fixtures: the identity replay runs them)
# Its offline set-up (no waits) and master's request hashes.
import test_light_packs as TLP  # noqa: E402

import app.ai.structured as structured  # noqa: E402
from app import config  # noqa: E402
from app.ai import runs, second_look, zone_read  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import assembly_walk as aw  # noqa: E402
from app.intake import car_map, links, zones  # noqa: E402
from app.reports.schema import SecondLookItem  # noqa: E402

FIXTURE_PATH = ROOT / "scripts" / "fixtures" / "car_map_walk.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
IX = aw.index(FIXTURE)
TABLE = car_map.links_table(FIXTURE)
ON = links.Options(mode="on")
ACTOR = AiActor.cli()
LABELS = ["P02", "P05", "P09"]
Settings = TLP.Settings

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


@contextlib.contextmanager
def the_map(cm):
    """car_map.MAP as `cm` for a block (the walk's index re-checked)."""
    old = car_map.MAP
    car_map.MAP = cm
    aw._CHECKED[:] = [None, None]
    try:
        yield
    finally:
        car_map.MAP = old
        aw._CHECKED[:] = [None, None]


class LogLines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@contextlib.contextmanager
def logged():
    handler = LogLines()
    log = logging.getLogger(aw.__name__)
    log.addHandler(handler)
    old = log.level
    log.setLevel(logging.INFO)
    try:
        yield handler.lines
    finally:
        log.removeHandler(handler)
        log.setLevel(old)


def item(part: str, *tokens, photo: str | None = None, **more) -> dict:
    return TL.item(part, *tokens, photo=photo, **more)


def walk(answers: dict, raise_mode="one", hops=1, ix=IX) -> aw.Walk:
    return aw.for_links(answers, ix, raise_mode, hops)


def rec(hit_at="cannot_see", **signs) -> dict:
    """A stored walk record: rec("centre", fb_pushed=("yes", "@a"))."""
    return {"hit_at": hit_at, "signs": {k.replace("_", "-"): {"answer": a, "photo": p, "zone": "zone:x"}
                                        for k, (a, p) in signs.items()}}


def build(listed, answers=None, raise_mode="one", hops=1, others=(), photos=(), table=TABLE, ix=IX, **kw):
    w = walk(answers, raise_mode, hops, ix) if answers is not None else None
    return links.build(list(listed), list(others), list(photos), {}, table, ON, walk=w, **kw)


def by_part(found):
    return {it["part"]: it for it in found}


def zone_call(zone: str, walk_ix=None, **kw) -> dict:
    photos = [(label, TLP.photo(i), kind) for label, i, kind in TLP.ZONE_PHOTOS]
    calls = TLP.recorded(zone_read, lambda: zone_read.read_zone(zone, photos, actor=ACTOR, models=["lite-z"],
                                                                walk=walk_ix, **kw))
    return calls[0]


def answer(zone: str, seen: str, damage: list, walk_raw, **kw):
    raw = json.dumps({"zone_side_seen": seen, "damage": damage, "assembly_walk": walk_raw, "checked_intact": []})
    return zone_read.parse_answer(zone, raw, LABELS, walk=IX, **kw)


def dmg(part, ref="P05", **more):
    return {"photo_reference": ref, "part": part, "ai_confidence": "High", **more}


def sign(sid, ans, photo=None):
    return {"sign": sid, "answer": ans, "photo": photo}


# ---------------------------------------------------------------- 1. settings


def settings_and_constants() -> None:
    s = config.Settings()
    check((s.intake_assembly_walk, s.intake_walk_raise, s.intake_link_hops) == ("off", "one", 1),
          "defaults: walk off, raise one, one hop (today)")
    check((settings.intake_assembly_walk, settings.intake_walk_raise, settings.intake_link_hops) == ("off", "one", 1),
          "and the app runs at them here")
    s = config.Settings(intake_assembly_walk=" ON ", intake_walk_raise="Full", intake_link_hops="2")
    check((s.intake_assembly_walk, s.intake_walk_raise, s.intake_link_hops) == ("on", "full", 2),
          "written the one way; hops as a number")
    s = config.Settings(intake_assembly_walk="", intake_walk_raise="", intake_link_hops="")
    check((s.intake_assembly_walk, s.intake_walk_raise, s.intake_link_hops) == ("off", "one", 1), "blank: the default")
    for bad in ({"intake_assembly_walk": "yes"}, {"intake_walk_raise": "half"}, {"intake_link_hops": 3},
                {"intake_link_hops": "two"}, {"intake_link_hops": True}, {"intake_link_hops": 0}):
        try:
            config.Settings(**bad)
            check(False, f"{bad} should be refused")
        except ValueError:
            check(True, f"refused at start-up: {bad}")
    check(aw.HIT_AT == car_map.HIT_AT and aw.ANSWERS == car_map.SIGN_ANSWERS,
          "the walk's answers are the car map's (HIT_AT, SIGN_ANSWERS)")
    check(not any(w in v for v in aw.HIT_AT for w in ("left", "right")), "hit_at never names a side")
    check(car_map.validate(FIXTURE) == [] and links.validate_table(TABLE) == [] and IX is not None
          and IX.version == "W1", "the fixture map validates and compiles", car_map.validate(FIXTURE)[:3])
    body = (ROOT.parent.parent / "infra" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    shared, rest = body.split("\nservices:", 1)
    lines = {"INTAKE_ASSEMBLY_WALK": "off", "INTAKE_WALK_RAISE": "one", "INTAKE_LINK_HOPS": "1"}
    for name, default in lines.items():
        want = f"\n    - {name}=${{{name}:-{default}}}\n"
        check(shared.count(want) == 1 and name not in rest, f"prod compose passes {name} (default {default!r})")
    s = config.Settings(**{name.lower(): value for name, value in lines.items()})
    check((s.intake_assembly_walk, s.intake_walk_raise, s.intake_link_hops) == ("off", "one", 1),
          "compose's defaults are config's")


# ---------------------------------------------------------------- 2. off = today


def off_is_today() -> None:
    got = TLP.snapshot()
    check(all(got.get(k) == v for k, v in TLP.MASTER.items()), "every snapshotted request is master's (walk off)",
          [k for k, v in TLP.MASTER.items() if got.get(k) != v])
    check(zones.light_options() == {} and zones.walk_index() is None, "the zone calls get nothing new while off")
    with the_map(FIXTURE):
        check(zones.walk_index() is None and zones.light_options() == {}, "off with a map loaded: still nothing")
    with Settings(intake_assembly_walk="on"):
        with the_map(None), logged() as lines:
            check(zones.walk_index() is None and zones.light_options() == {}, "on with no car map: skipped")
            check(any("no car map" in line for line in lines), "and logged", lines)
            for zone in zone_read.ZONE_NAMES:
                check(TLP.digest(TLP.sent(zone_call(zone, **zones.light_options()))) == TLP.MASTER[f"zone_{zone}"],
                      f"{zone}: the request is master's")
        bad = copy.deepcopy(FIXTURE)
        bad["assemblies"][0]["signs"][0]["points_to"] = ["no-such-relation"]
        with the_map(bad), logged() as lines:
            check(zones.walk_index() is None, "on with a map that fails its check: skipped")
            check(any("fails its check" in line for line in lines), "and logged", lines)
        with the_map(car_map.from_l1(TL.TABLE)), logged() as lines:
            check(zones.walk_index() is None and any("no signs" in line for line in lines),
                  "on with a map without signs (L1 as a map): skipped, logged", lines)
        with the_map(["not", "a", "map"]):
            check(zones.walk_index() is None, "on with junk for a map: skipped, no exception")
    check(aw.index(None) is None and aw.index({"version": "x"}) is None and aw.index("junk") is None
          and aw.index({"version": "x", "assemblies": [None, 5, {"id": "a", "signs": [None, {"id": 3}]}]}) is None,
          "index of junk: None, never an exception")
    rear_only = copy.deepcopy(FIXTURE)
    for asm in rear_only["assemblies"]:
        if asm["id"] != "boot-lid":
            asm["anchors"], asm["signs"] = [], []
    for rel in rear_only["relations"]:
        rel.pop("position", None)
    rear_only["chains"] = [c for c in rear_only["chains"] if c["id"].startswith("bl>")]
    check(car_map.validate(rear_only) == [], "a map with a boot lid anchor only validates", car_map.validate(rear_only)[:2])
    rix = aw.index(rear_only)
    for zone in ("front", "left", "right"):
        check(TLP.digest(TLP.sent(zone_call(zone, rix))) == TLP.MASTER[f"zone_{zone}"],
              f"{zone}: no anchor of the map here -- master's request, no tag")
        check(not any(TLP.TAGS), "untagged")
    check(TLP.digest(TLP.sent(zone_call("rear", rix))) != TLP.MASTER["zone_rear"], "the rear (its anchor) is walked")
    real = aw.zone_spec
    aw.zone_spec = lambda *a, **k: 1 / 0
    try:
        check(TLP.digest(TLP.sent(zone_call("front", IX))) == TLP.MASTER["zone_front"] and not any(TLP.TAGS),
              "a walk that can't be built: the zone is read as today, never lost")
    finally:
        aw.zone_spec = real


# ---------------------------------------------------------------- 3. on: prompt and schema


PANELS = {"front": ["Front Bumper", "Left Headlamp", "Right Headlamp"], "rear": ["Boot Lid"],
          "left": ["Front Door"], "right": ["Front Door"],
          "close": ["Front Bumper", "Headlamp (side undetermined)", "Boot Lid", "Door (side undetermined)"]}
TOKENS: dict = {}


def prompt_and_schema() -> None:
    closing = zone_read._CLOSING
    for zone in zone_read.ZONE_NAMES:
        spec = zone_read._walk_spec(zone, IX)
        text = aw.prompt_text(spec)
        off, on = zone_read.system_prompt(zone), zone_read.system_prompt(zone, walk=IX)
        check(on.replace(text, "") == off and on.index(text) + len(text) == on.index(closing),
              f"{zone}: the walk's text right before the closing line, nothing else changed")
        check(text.startswith(aw.WALK_INTRO + "\n") and len(spec.lines) == len(text.splitlines()) - 1,
              f"{zone}: one paragraph, then one line per anchor (sides that share signs on one line)")
        so, sn = zone_read.response_schema(zone, LABELS), zone_read.response_schema(zone, LABELS, walk=IX)
        check(list(sn["properties"]) == ["zone_side_seen", "damage", "assembly_walk", "checked_intact"]
              and sn["required"] == ["zone_side_seen", "damage", "assembly_walk", "checked_intact"],
              f"{zone}: assembly_walk after damage, required")
        back = copy.deepcopy(sn)
        del back["properties"]["assembly_walk"]
        back["required"].remove("assembly_walk")
        check(back == so and list(back["properties"]) == list(so["properties"]), f"{zone}: the rest as today")
        field = sn["properties"]["assembly_walk"]["items"]
        check(field["properties"]["panel"]["enum"] == PANELS[zone], f"{zone}: panel = this zone's anchors",
              field["properties"]["panel"]["enum"])
        signs = field["properties"]["signs"]["items"]["properties"]
        check(signs["sign"]["enum"] == list(spec.sign_ids)
              and signs["photo"] == {"anyOf": [{"type": "string", "enum": LABELS}, {"type": "null"}]}
              and signs["answer"]["enum"] == ["yes", "no", "cannot_see"]
              and field["properties"]["hit_at"]["enum"] == list(aw.HIT_AT),
              f"{zone}: signs = their ids, answer yes/no/cannot_see, photo = the labels sent or null")
        TOKENS[zone] = (round((len(on) - len(off)) / 4), round((len(json.dumps(sn)) - len(json.dumps(so))) / 4))
    front = zone_read._walk_spec("front", IX)
    check(front.lines == (
        "Front Bumper: fb-pushed Is the bumper cover pushed inward, not just scratched? | fb-gap Is the gap between "
        "the bumper and a fender or lamp uneven or closed? | fb-fluid Is there fluid on the ground or dripping under "
        "the front?",
        "Left Headlamp, Right Headlamp: hl-pushed Is the lamp pushed back into the car or hanging out of its opening?"),
        "front: the bumper's line and the two headlamps' shared line", front.lines)
    check(front.sign_ids == ("fb-pushed", "fb-gap", "fb-fluid", "hl-pushed")
          and zone_read._walk_spec("left", IX).signs == {"Front Door": ("fd-crease", "fd-gap")},
          "sign ids per panel, in map order")
    close = zone_read._walk_spec("close", IX)
    check(close.signs["Door (side undetermined)"] == ("fd-crease", "fd-gap"), "close-ups: the undetermined names")
    for zone in ("rear", "left"):
        packed = zone_read.system_prompt(zone, packs=["small", "cues", "orient", "intact"], walk=IX)
        plain = zone_read.system_prompt(zone, packs=["small", "cues", "orient", "intact"])
        text = aw.prompt_text(zone_read._walk_spec(zone, IX))
        small = zone_read.prompt_packs.small_rows(zone) + "\n"
        check(packed.replace(text, "") == plain and packed.index(small) + len(small) == packed.index(text),
              f"{zone} with every pack: the walk after the packs' text, before the closing line")
    for zone in zone_read.ZONE_NAMES:
        call = zone_call(zone, IX)
        plain = zone_call(zone)
        check(call["system_instruction"] == zone_read.system_prompt(zone, walk=IX)
              and call["schema"] == zone_read.response_schema(zone, LABELS, walk=IX)
              and TLP.sent(call)["input_parts"] == TLP.sent(plain)["input_parts"] and call["timeout"] == plain["timeout"],
              f"read_zone {zone}: that prompt and schema, the same photos and timeout")
        zone_call(zone, IX)
        check(TLP.TAGS and str(TLP.TAGS[0]).startswith(f"zone:{zone}-v1+walk-W1@"), f"{zone}: tagged +walk-W1",
              TLP.TAGS)
    zone_call("rear", IX, packs=["small"])
    check(str(TLP.TAGS[0]).startswith("zone:rear-v1+small-v1+walk-W1@"), "with a pack: both in the tag", TLP.TAGS)
    with Settings(intake_assembly_walk="on", intake_zone_packs="small"), the_map(FIXTURE):
        light = zones.light_options()
    check(light.get("packs") == ["small"] and isinstance(light.get("walk"), aw.MapIndex)
          and light["walk"].version == "W1", "zones.light_options: the walk (the map's index) beside the packs")
    print("    token delta per zone call (characters / 4), prompt + schema: "
          + ", ".join(f"{z} +{p}+{s}" for z, (p, s) in TOKENS.items()))


# ---------------------------------------------------------------- 4. parsing


def parsing() -> None:
    walked = [{"panel": "Front Door", "hit_at": "corner", "signs": [sign("fd-crease", "yes", "P05")]}]
    got = answer("left", "left", [dmg("Front Door")], walked).walk
    check(got == {"Left Front Door": {"hit_at": "corner", "signs": {"fd-crease": {"answer": "yes", "photo": "P05"}}}},
          "a side zone: the panel canonical, its side from zone_side_seen", got)
    check(list(answer("left", "right", [dmg("Front Door")], walked).walk) == ["Right Front Door"],
          "the side the zone saw (right), never the sorter's or the walk's")
    check(list(answer("left", "unclear", [dmg("Front Door")], walked).walk) == ["Door (side undetermined)"],
          "unclear: the undetermined name")
    got = answer("rear", "rear", [dmg("Rear Bumper")], [{"panel": "Boot Lid", "hit_at": "whole",
                                                           "signs": [sign("bl-shut", "yes", "P05")]}]).walk
    check(got == {}, "a panel this zone did not list as damaged: no walk", got)
    got = answer("front", "front", [dmg("Front Bumper")], [{"panel": "Front Bumper", "hit_at": "centre", "signs": [
        sign("fb-pushed", "yes", "P05"), sign("hl-pushed", "yes", "P05"), sign("bl-shut", "yes", "P05"),
        sign("nothing", "yes", "P05")]}]).walk
    check(list(got["Front Bumper"]["signs"]) == ["fb-pushed"],
          "only the panel's own assembly's signs (the headlamp's and the boot lid's are left out)", got)
    got = answer("front", "front", [dmg("Front Bumper")], [
        {"panel": "Front Bumper", "hit_at": "corner", "signs": [sign("fb-pushed", "no"), sign("fb-pushed", "yes", "P05")]},
        {"panel": "Front Bumper", "hit_at": "centre", "signs": [sign("fb-gap", "yes", "P09"), sign("fb-pushed", "yes", "P02")]},
    ]).walk["Front Bumper"]
    check(got == {"hit_at": "corner", "signs": {"fb-pushed": {"answer": "no", "photo": None},
                                                 "fb-gap": {"answer": "yes", "photo": "P09"}}},
          "the first answer wins (and the first hit_at); a later entry only adds signs not answered", got)
    got = answer("front", "front", [dmg("Front Bumper")], [{"panel": "Front Bumper", "hit_at": "LEFT corner", "signs": [
        sign("fb-gap", "yes", "P99"), sign("fb-pushed", "yes"), sign("fb-fluid", "maybe", "P05")]}]).walk["Front Bumper"]
    check(got == {"hit_at": "cannot_see", "signs": {"fb-gap": {"answer": "cannot_see", "photo": None},
                                                     "fb-pushed": {"answer": "cannot_see", "photo": None}}},
          "a yes without a photo sent is cannot_see; an unknown hit_at is cannot_see; an unknown answer is no answer",
          got)
    got = answer("front", "front", [dmg("Front Bumper")], [{"panel": "Front Bumper", "hit_at": "Whole",
                                                             "signs": [sign("fb-gap", "NO", "p5")]}]).walk["Front Bumper"]
    check(got == {"hit_at": "whole", "signs": {"fb-gap": {"answer": "no", "photo": "P05"}}},
          "case and label spelling as the rest of the answer reads them", got)
    other = answer("front", "rear", [dmg("Front Bumper")], [{"panel": "Front Bumper", "hit_at": "centre", "signs": []}])
    check(other.walk == {} and other.items == [] and other.note, "photos of the other end: finds and walk dropped")
    z5 = answer("front", "front", [dmg("Left Headlamp", photo_half="left")],
                [{"panel": "Left Headlamp", "hit_at": "corner", "signs": [sign("hl-pushed", "yes", "P05")]}],
                photo_half=True)
    check([i["part"] for i in z5.items] == ["Headlamp (side undetermined)"]
          and list(z5.walk) == ["Headlamp (side undetermined)"],
          "Z5 took the lamp's side away: its walk goes under the undetermined name too (never a side from the walk)",
          (z5.items and z5.items[0]["part"], z5.walk))
    for junk in ("x", 5, None, [1, None, "a", {"panel": 5}, {"panel": "Bonnet", "signs": []},
                                {"panel": "Other / Not Listed"}, {"panel": "Front Bumper", "signs": "x"}]):
        got = answer("front", "front", [dmg("Front Bumper")], junk)
        check(got.items and got.walk in ({}, {"Front Bumper": {"hit_at": "cannot_see", "signs": {}}}),
              f"junk walk {str(junk)[:40]!r}: the answer stands, nothing walked", got.walk)
    raw = json.dumps({"zone_side_seen": "front", "damage": [dmg("Front Bumper")], "checked_intact": []})
    check(zone_read.parse_answer("front", raw, LABELS, walk=IX).walk == {}, "no assembly_walk in the answer: no walk")
    real = aw.parse_zone
    aw.parse_zone = lambda *a, **k: 1 / 0
    try:
        got = answer("front", "front", [dmg("Front Bumper")], [{"panel": "Front Bumper", "hit_at": "centre", "signs": []}])
        check(got.items and got.walk == {}, "a walk that can't be read: the finds stand (the answer is not refused)")
    finally:
        aw.parse_zone = real
    raw = json.dumps({"zone_side_seen": "front", "damage": [dmg("Front Bumper")], "checked_intact": [],
                      "assembly_walk": walked})
    check(zone_read.parse_answer("front", raw, LABELS).walk == {}, "walk off: an assembly_walk is not read")


# ---------------------------------------------------------------- 5. merging


def merging() -> None:
    one = {"Front Bumper": {"hit_at": "centre", "signs": {"fb-pushed": {"answer": "yes", "photo": "P05"},
                                                          "fb-gap": {"answer": "no", "photo": None}}}}
    ids = aw.with_photo_ids(one, {"P05": "ph5"})
    check(ids["Front Bumper"]["signs"]["fb-pushed"] == {"answer": "yes", "photo": "@ph5"}
          and ids["Front Bumper"]["signs"]["fb-gap"] == {"answer": "no", "photo": None}, "photos as '@<photo id>'")
    check(aw.with_photo_ids(one, {})["Front Bumper"]["signs"]["fb-pushed"]["answer"] == "cannot_see",
          "a yes whose photo is gone: cannot_see")
    a = {"P": {"hit_at": "centre", "signs": {"s1": {"answer": "no", "photo": None},
                                            "s2": {"answer": "yes", "photo": "@a"},
                                            "s3": {"answer": "cannot_see", "photo": None}}}}
    b = {"P": {"hit_at": "corner", "signs": {"s1": {"answer": "yes", "photo": "@b"},
                                            "s2": {"answer": "no", "photo": None},
                                            "s3": {"answer": "no", "photo": None}}},
         "Q": {"hit_at": "whole", "signs": {}}}
    got = aw.merge_all([("zone:front", a), ("zone:close", b)])
    check(got["P"]["signs"] == {"s1": {"answer": "yes", "photo": "@b", "zone": "zone:close"},
                                "s2": {"answer": "yes", "photo": "@a", "zone": "zone:front"},
                                "s3": {"answer": "no", "photo": None, "zone": "zone:close"}},
          "yes beats no beats cannot_see, with its photo and read", got["P"]["signs"])
    check(got["P"]["hit_at"] == "cannot_see" and got["Q"]["hit_at"] == "whole", "centre against corner: cannot_see")
    tie = aw.merge_all([("zone:front", {"P": {"hit_at": "centre", "signs": {"s": {"answer": "yes", "photo": "@a"}}}}),
                        ("zone:close", {"P": {"hit_at": "centre", "signs": {"s": {"answer": "yes", "photo": "@b"}}}})])
    check(tie["P"]["signs"]["s"]["photo"] == "@a", "a tie keeps the first read's")
    for seen, want in ((["centre", "cannot_see"], "centre"), (["cannot_see", "corner"], "corner"),
                       (["corner", "whole", "centre"], "whole"), (["centre", "corner", "centre"], "cannot_see"),
                       (["cannot_see"], "cannot_see"), (["centre", "centre"], "centre")):
        walks = [(f"zone:{n}", {"P": {"hit_at": h, "signs": {}}}) for n, h in enumerate(seen)]
        check(aw.merge_all(walks)["P"]["hit_at"] == want, f"hit_at {seen} -> {want}")
    check(aw.clean_answers({"P": {"hit_at": "left", "signs": {"s": {"answer": "yes"}, "t": "x", 5: {}}}, 3: {}})
          == {"P": {"hit_at": "cannot_see", "signs": {"s": {"answer": "cannot_see", "photo": None, "zone": None}}}},
          "a stored walk read back: junk left out, a yes without a photo is cannot_see")


# ---------------------------------------------------------------- 6. the zone stage and the pipeline end to end


REQUESTS: list[dict] = []
_lock = threading.Lock()
ZONE_ANSWERS: dict = {}
DRAFT = {"fn": None}


class FakeInteractions:
    """Gemini for this file's end-to-end sections: the sort is never asked
    (the photos come sorted), the drafting and the zone calls by schema."""

    def create(self, **kwargs):
        with _lock:
            REQUESTS.append(kwargs)
        props = kwargs["response_format"]["schema"].get("properties", {})
        labels = [p["text"].split()[1].rstrip(":") for p in kwargs["input"]
                  if p.get("type") == "text" and p["text"].startswith("Photo ") and p["text"].endswith(":")
                  and "grid" not in p["text"]]
        if "zone_side_seen" in props:
            zone = next(z for z, (wanted, _) in ZONE_ANSWERS.items() if wanted & set(labels))
            out = ZONE_ANSWERS[zone][1]
        elif "photos" in props:
            out = {"photos": []}
        else:
            out = DRAFT["fn"]()
        return SimpleNamespace(output_text=json.dumps(out),
                               usage=SimpleNamespace(total_input_tokens=100, total_output_tokens=10))


def zone_stage() -> None:
    structured.get_client = lambda key=None: SimpleNamespace(interactions=FakeInteractions())
    ZONE_ANSWERS.clear()
    ZONE_ANSWERS.update({
        "front": ({"P02"}, {"zone_side_seen": "front", "damage": [dmg("Front Bumper", "P02")], "checked_intact": [],
                            "assembly_walk": [{"panel": "Front Bumper", "hit_at": "centre",
                                               "signs": [sign("fb-fluid", "yes", "P02"), sign("fb-pushed", "no")]}]}),
        "close": ({"P07"}, {"zone_side_seen": "unclear", "damage": [dmg("Front Bumper", "P07")], "checked_intact": [],
                            "assembly_walk": [{"panel": "Front Bumper", "hit_at": "corner",
                                               "signs": [sign("fb-pushed", "yes", "P07")]}]}),
    })
    jobs = [zones.ZoneJob("front", [("P02", "ph2", TLP.photo(2), "damage")]),
            zones.ZoneJob("close", [("P07", "ph7", TLP.photo(7), "damage")])]
    with Settings(gemini_zone_chain="lite-z"):
        REQUESTS.clear()
        items, _, answered = zones.read_and_merge("aw-off", ACTOR, [], jobs)
        check(answered == ["overall", "zone:front", "zone:close"]
              and not any("assembly_walk" in r["response_format"]["schema"]["properties"] for r in REQUESTS),
              "walk off: the zone calls as before, nothing walked")
        with Settings(intake_assembly_walk="on"), the_map(FIXTURE):
            REQUESTS.clear()
            got: dict = {}
            zones.read_and_merge("aw-on", ACTOR, [], jobs, walk=got)
            asked = [r for r in REQUESTS if "assembly_walk" in r["response_format"]["schema"]["properties"]]
            check(len(asked) == 2 == len(REQUESTS), "walk on: both zone calls ask for it (+0 calls)")
            check(got == {"Front Bumper": {"hit_at": "cannot_see", "signs": {
                "fb-fluid": {"answer": "yes", "photo": "@ph2", "zone": "zone:front"},
                "fb-pushed": {"answer": "yes", "photo": "@ph7", "zone": "zone:close"}}}},
                "merged: photos as '@<photo id>', the read of each answer, centre against corner", got)
    from app.reports.db import SessionLocal
    from app.reports.models import AiRun

    db = SessionLocal()
    try:
        rows = {r.report_id: r for r in db.query(AiRun).filter(AiRun.kind == runs.ZONES_KIND).all()}
    finally:
        db.close()
    off_zones = (rows["aw-off"].photos_sent or {}).get("zones") or []
    on_zones = (rows["aw-on"].photos_sent or {}).get("zones") or []
    check(off_zones and not any("walk" in z for z in off_zones), "walk off: the run log's entries as before")
    check({z["zone"]: z.get("walk") for z in on_zones} == {
        "front": {"Front Bumper": {"hit_at": "centre", "yes": ["fb-fluid"]}},
        "close": {"Front Bumper": {"hit_at": "corner", "yes": ["fb-pushed"]}}},
        "walk on: each zone's entry has its walk, short", [z.get("walk") for z in on_zones])
    tags = [a.get("prompt") for a in (rows["aw-on"].attempts or [])]
    check(tags and all(str(t).startswith(("zone:front-v1+walk-W1@", "zone:close-v1+walk-W1@")) for t in tags)
          and not any(a.get("prompt") for a in (rows["aw-off"].attempts or [])),
          "the run log tags the walked calls (+walk-W1), not the others", tags)


PIPE_CASE = [("overview", "front", None, False), ("damage", "front", "Front Bumper", True),
             ("damage", "rear", "Boot Lid", True), ("overview", "rear", None, False)]


def _make_case(rid: str) -> None:
    from app.reports.db import SessionLocal
    from app.reports.models import IntakePhoto, IntakeSession, Report

    db = SessionLocal()
    try:
        db.add(Report(id=rid, status="intake", channel="dashboard", reporter_chat_id="test",
                      data={"description": "", "category": [], "damage_summary": []}))
        db.add(IntakeSession(report_id=rid, stage="reading", reporter_notes="Hit at the front."))
        for i, (kind, area, part, damage) in enumerate(PIPE_CASE):
            db.add(IntakePhoto(id=f"{rid}-{i}", report_id=rid, position=i, original_name=f"IMG_{i}.jpg",
                               path=TLP.photo(i + 1), sha256=f"{rid}{i}", kind=kind, area=area, part=part,
                               shows_damage=damage, quality="good"))
        db.commit()
    finally:
        db.close()


def _saved(rid: str) -> tuple[dict, dict]:
    from app.reports.db import SessionLocal
    from app.reports.models import IntakeSession, Report

    db = SessionLocal()
    try:
        return copy.deepcopy(db.get(Report, rid).data), copy.deepcopy(db.get(IntakeSession, rid).ai_draft or {})
    finally:
        db.close()


def pipeline_end_to_end() -> None:
    from app.intake import pipeline

    structured.get_client = lambda key=None: SimpleNamespace(interactions=FakeInteractions())
    DRAFT["fn"] = lambda: {"description": "Hit at the front.", "category": ["Vehicle Collision or Damage"],
                           "damage_summary": [{"part": "Front Bumper", "photo_reference": "P02", "ai_confidence": "High",
                                               "condition_tokens": ["Dented"]}],
                           "vehicle_info": {"make": "Gammamotors", "model": "Testa"}}
    ZONE_ANSWERS.clear()
    ZONE_ANSWERS.update({
        "front": ({"P02"}, {"zone_side_seen": "front", "damage": [dmg("Front Bumper", "P02", condition_tokens=["Dented"])],
                            "checked_intact": [], "assembly_walk": [{"panel": "Front Bumper", "hit_at": "centre", "signs": [
                                sign("fb-fluid", "yes", "P02"), sign("fb-pushed", "no")]}]}),
        "rear": ({"P03"}, {"zone_side_seen": "rear", "damage": [dmg("Boot Lid", "P03")], "checked_intact": [],
                           "assembly_walk": [{"panel": "Boot Lid", "hit_at": "whole",
                                              "signs": [sign("bl-crease", "yes", "P03")]}]}),
    })
    base = dict(gemini_model_chain="fake-reader", gemini_zone_chain="lite-z", intake_read_mode="zones",
                intake_links="on", second_look_enabled=False)
    old_table = links.TABLE
    links.TABLE = TABLE  # the car map's table, as I3's seam will give it
    try:
        with Settings(**base):
            _make_case("aw-pipe-off")
            pipeline._read("aw-pipe-off", ACTOR)
        with Settings(**base, intake_assembly_walk="on", intake_link_hops=2), the_map(FIXTURE):
            _make_case("aw-pipe-on")
            pipeline._read("aw-pipe-on", ACTOR)
    finally:
        links.TABLE = old_table
    data_off, draft_off = _saved("aw-pipe-off")
    data_on, draft_on = _saved("aw-pipe-on")
    check("assembly_walk" not in draft_off, "walk off: no assembly_walk in the AI draft")
    check(draft_on.get("assembly_walk") == {
        "Front Bumper": {"hit_at": "centre", "signs": {
            "fb-fluid": {"answer": "yes", "photo": "@aw-pipe-on-1", "zone": "zone:front"},
            "fb-pushed": {"answer": "no", "photo": None, "zone": "zone:front"}}},
        "Boot Lid": {"hit_at": "whole", "signs": {
            "bl-crease": {"answer": "yes", "photo": "@aw-pipe-on-2", "zone": "zone:rear"}}}},
        "walk on: ai_draft['assembly_walk'], photos by id, each answer's read", draft_on.get("assembly_walk"))
    off = by_part(i for i in (data_off.get("second_look") or {}).get("items") or [] if i.get("source") == "link")
    on = by_part(i for i in (data_on.get("second_look") or {}).get("items") or [] if i.get("source") == "link")
    check(off and on, "linked parts both times", (sorted(off), sorted(on)))
    check(not any(k in it for it in off.values() for k in ("hop", "via", "signs_yes")),
          "walk off: no walk field on a linked part's dump")
    check("Fog Lamp (side undetermined)" in off and "Fog Lamp (side undetermined)" not in on,
          "a centre hit: the corner's fog lamp goes")
    cond = on.get("Air Conditioning Condenser") or {}
    check(cond.get("bucket") == "likely" and [s["id"] for s in cond.get("signs_yes") or []] == ["fb-fluid"]
          and cond["signs_yes"][0]["photo"] == "@aw-pipe-on-1" and off.get("Air Conditioning Condenser") is None,
          "a yes sign: the condenser shows (the hit raised to heavy) and is likely, with its sign", cond)
    rad = on.get("Radiator") or {}
    check(rad.get("hop") == 2 and rad.get("via") == "Air Conditioning Condenser" and rad.get("bucket") == "strip_down"
          and rad.get("link_rule") == "W1:fb>condenser>radiator" and rad.get("linked_from") == ["Front Bumper"],
          "the second hop survives the store (hop, via, rule, linked_from)", rad)
    check("Boot Lid" not in [i.get("part") for i in data_on.get("damage_summary") or []]
          and not any("Boot Lid" in (it.get("linked_from") or []) for it in on.values()),
          "a panel only a zone found is walked, but only listed parts bring linked parts")


# ---------------------------------------------------------------- 7. the code side


def hit_raise() -> None:
    bumper = item("Front Bumper", "Dented")
    for answers, mode, want, label in (
            ({"Front Bumper": rec(fb_pushed=("yes", "@a"))}, "one", "heavy", "medium + a heavy yes, one: heavy"),
            ({"Front Bumper": rec(fb_fluid=("yes", "@a"))}, "one", "heavy", "medium + a crush yes, one: one step"),
            ({"Front Bumper": rec(fb_fluid=("yes", "@a"))}, "full", "crush", "medium + a crush yes, full: crush"),
            ({"Front Bumper": rec(fb_fluid=("no", None), fb_pushed=("cannot_see", None))}, "full", "medium",
             "no / cannot_see: unchanged"),
            ({"Front Bumper": rec(hl_pushed=("yes", "@a"), bl_crease=("yes", "@a"))}, "full", "medium",
             "another assembly's yes: unchanged"),
            ({}, "full", "medium", "no walk of the part: unchanged")):
        w = walk(answers, mode)
        check(links._context([bumper], [], [], {}, TABLE, ON, (), None, w).levels == [want], label)
    bent = item("Front Bumper", "Bent")
    w = walk({"Front Bumper": rec(fb_gap=("yes", "@a"))}, "full")
    check(links._context([bent], [], [], {}, TABLE, ON, (), None, w).levels == ["heavy"], "a sign never lowers")
    grazed = item("Front Bumper", "Grazed")
    w1 = walk({"Front Bumper": rec(fb_fluid=("yes", "@a"))}, "one")
    wf = walk({"Front Bumper": rec(fb_fluid=("yes", "@a"))}, "full")
    check(links._context([grazed], [], [], {}, TABLE, ON, (), None, w1).levels == ["medium"]
          and links._context([grazed], [], [], {}, TABLE, ON, (), None, wf).levels == ["crush"],
          "light + a crush yes: one -> medium, full -> crush")
    plain = item("Front Bumper")
    check(links._context([plain], [], [], {}, TABLE, ON, (), None, w1).levels == ["heavy"],
          "no words: unknown_hit (medium), raised one step")
    three = [item("Front Bumper", "Dented"), item("Front Grille", "Cracked"), item("Bonnet", "Dented")]
    w = walk({"Front Bumper": rec(fb_pushed=("yes", "@a"))}, "one")
    check(links._context(three, [], [], {}, TABLE, ON, (), None, None).levels == ["medium", "medium", "medium"]
          and links._context(three, [], [], {}, TABLE, ON, (), None, w).levels == ["crush", "medium", "medium"],
          "the end bonus after the raise: raised to heavy with three front parts listed -> crush")
    l1 = links.build([bumper], [], [], {}, TL.TABLE, ON, walk=walk({"Front Bumper": rec(fb_pushed=("yes", "@a"))}))
    check(all(it["hit"] == "heavy" for it in l1) and "Front Bumper Beam" in by_part(l1)
          and not any("signs_yes" in it for it in l1),
          "on L1 (another table version): the raise acts, the map's relation steps do not")


def position_gates() -> None:
    bumper = item("Front Bumper", "Bent")
    plain = set(by_part(build([bumper])))
    check({"Left Fog Lamp", "Right Fog Lamp"} & plain or "Fog Lamp (side undetermined)" in plain,
          "no walk: the fog lamp (a corner relation) shows", sorted(plain))
    centre = set(by_part(build([bumper], {"Front Bumper": rec("centre")})))
    corner = set(by_part(build([bumper], {"Front Bumper": rec("corner")})))
    check("Fog Lamp (side undetermined)" not in centre and "Front Number Plate" in centre
          and "Air Conditioning Condenser" in centre, "a centre hit: the corner relation fails, centre ones pass")
    check("Front Number Plate" not in corner and "Air Conditioning Condenser" not in corner
          and "Fog Lamp (side undetermined)" in corner, "a corner hit: the centre relations fail")
    for hit_at in ("whole", "cannot_see"):
        check(set(by_part(build([bumper], {"Front Bumper": rec(hit_at)}))) == plain, f"{hit_at}: every relation passes")
    check(set(by_part(build([bumper], {"Boot Lid": rec("centre")}))) == plain, "no walk of this part: all pass")
    check(centre <= plain and corner <= plain, "position only ever removes")
    door = item("Left Front Door", "Bent")
    check("Left Wing Mirror" not in by_part(build([door], {"Left Front Door": rec("centre")}))
          and "Left Wing Mirror" in by_part(build([door], {"Door (side undetermined)": rec("corner")})),
          "a sided part takes its undetermined twin's walk (a close-up's)")


def bucket_lift() -> None:
    bumper = item("Front Bumper", "Bent", photo="@b1")
    plain = by_part(build([bumper]))
    check(plain["Front Bumper Beam"]["bucket"] == "strip_down", "without a walk the beam is strip-down")
    lifted = by_part(build([bumper], {"Front Bumper": rec(fb_pushed=("yes", "@p3"), fb_gap=("yes", "@p4"))}))
    beam = lifted["Front Bumper Beam"]
    check(beam["bucket"] == "likely" and beam["signs_yes"] == [
        {"id": "fb-pushed", "ask": "Is the bumper cover pushed inward, not just scratched?", "photo": "@p3"},
        {"id": "fb-gap", "ask": "Is the gap between the bumper and a fender or lamp uneven or closed?", "photo": "@p4"}],
        "a yes pointing at it: likely, signs_yes [{id, ask, photo}]", beam)
    nope = by_part(build([bumper], {"Front Bumper": rec(fb_pushed=("no", None), fb_gap=("cannot_see", None))}))
    check(nope["Front Bumper Beam"]["bucket"] == "strip_down" and "signs_yes" not in nope["Front Bumper Beam"],
          "a no (or cannot_see): nothing changes, no signs_yes")
    check(nope == plain, "and the whole build is as without the walk")
    seen = TL.photo("s1", "Front Bumper Beam", area="front")
    got = by_part(build([bumper], {"Front Bumper": rec(fb_pushed=("yes", "@p3"))}, photos=[seen]))["Front Bumper Beam"]
    check(got["bucket"] == "seen" and [s["id"] for s in got["signs_yes"]] == ["fb-pushed"],
          "seen stays seen, its sign recorded")
    door = item("Left Front Door", "Bent")
    got = by_part(build([door], {"Left Front Door": rec(fd_gap=("yes", "@d"))}))
    check(got["Interior Trim"]["bucket"] == "likely" and got["Left Front Window Regulator"]["bucket"] == "strip_down",
          "a door's yes lifts the trim it points at, not the regulator")
    check(SecondLookItem.model_validate(beam).model_dump()["signs_yes"] == beam["signs_yes"]
          and "signs_yes" not in SecondLookItem.model_validate(plain["Front Bumper Beam"]).model_dump()
          and "hop" not in SecondLookItem(part="x").model_dump(),
          "SecondLookItem keeps signs_yes; not in a dump at its default")


def _hop2(found):
    return {it["part"]: it for it in found if it.get("hop") == 2}


def second_hop() -> None:
    bumper = item("Front Bumper", "Bent", photo="@b1")
    yes = {"Front Bumper": rec("centre", fb_fluid=("yes", "@f"))}
    got = _hop2(build([bumper], yes, hops=2))
    rad = got.get("Radiator") or {}
    check(set(got) == {"Radiator"} and rad.get("via") == "Air Conditioning Condenser"
          and rad.get("linked_from") == ["Front Bumper"] and rad.get("link_rule") == "W1:fb>condenser>radiator"
          and rad.get("bucket") == "strip_down" and rad.get("link_type") == "crush" and rad.get("hit") == "crush"
          and rad.get("reason") == FIXTURE["chains"][0]["reason"]
          and [s["id"] for s in rad.get("signs_yes") or []] == ["fb-fluid"]
          and [e.get("photo") for e in rad.get("evidence") or []] == ["@b1"],
          "fires: from `then`, via the middle part, linked_from the anchor, the chain's rule and reason, "
          "strip-down, its sign, the anchor's photo", rad)
    check(not _hop2(build([bumper], yes, hops=1)), "intake_link_hops 1: no second hop")
    check(not _hop2(build([bumper], {"Front Bumper": rec("centre", fb_fluid=("no", None))}, hops=2)),
          "no yes that unlocks it: none")
    check(not _hop2(build([bumper], {"Front Bumper": rec("centre", fb_gap=("yes", "@g"))}, hops=2)),
          "a yes that unlocks nothing: none")
    check(not _hop2(build([bumper], yes, hops=2, dismissed=["Air Conditioning Condenser"])),
          "the middle part dismissed: none")
    middle_listed = build([bumper, item("Air Conditioning Condenser", "Bent")], yes, hops=2)
    check(not _hop2(middle_listed) and "Radiator" in by_part(middle_listed),
          "the middle part listed: its own first hop gives the radiator, no second hop")
    check(not _hop2(build([bumper], yes, hops=2, dismissed=["Radiator"])), "the end part dismissed: none")
    seen = TL.photo("s9", "Radiator", area="front")
    check(_hop2(build([bumper], yes, hops=2, photos=[seen]))["Radiator"]["bucket"] == "seen",
          "a sorter photo shows the end part: seen")
    strict = copy.deepcopy(FIXTURE)
    strict["chains"][0]["min_hit"] = "crush"
    six = aw.index(strict)
    dented = item("Front Bumper", "Dented", photo="@b1")
    check(not _hop2(build([dented], yes, hops=2, ix=six, table=car_map.links_table(strict)))
          and "Air Conditioning Condenser" in by_part(build([dented], yes, hops=2, ix=six,
                                                            table=car_map.links_table(strict))),
          "the anchor (medium, raised one step to heavy) below the chain's min_hit (crush): the condenser, no hop")
    crowd = [dented, item("Front Grille", "Cracked"), item("Bonnet", "Dented")]
    check("Radiator" in _hop2(build(crowd, yes, hops=2, ix=six, table=car_map.links_table(strict))),
          "the end bonus takes it to crush: fires")
    on_l1 = links.build([bumper], [], [], {}, TL.TABLE, ON, walk=walk(yes, hops=2))
    check(not any(it.get("hop") for it in on_l1), "on another table version (L1): no second hop")

    # At most 4 a case.
    many = [item("Front Bumper", "Bent"), item("Boot Lid", "Bent"), item("Left Front Door", "Bent"),
            item("Right Front Door", "Bent")]
    answers = {"Front Bumper": rec("whole", fb_fluid=("yes", "@f"), fb_pushed=("yes", "@p")),
               "Boot Lid": rec("whole", bl_crease=("yes", "@c")),
               "Left Front Door": rec(fd_crease=("yes", "@l")), "Right Front Door": rec(fd_crease=("yes", "@r"))}
    big = links.Options(mode="on", max_items=40)
    found = links.build(many, [], [], {}, TABLE, big, walk=walk(answers, hops=2))
    hop2 = [it["part"] for it in found if it.get("hop") == 2]
    roomy = links._context(many, [], [], {}, TABLE, big, (), None, aw.with_room(walk(answers, hops=2), -10))
    first = sorted(links._candidates(list(range(len(many))), roomy, TABLE), key=lambda c: c["_order"])
    possible = len(links._hop2(first, roomy, TABLE))
    check(possible >= 5 and len(hop2) == 4, f"at most 4 second hops a case ({possible} possible, {len(hop2)} kept)", hop2)

    # Sides.
    firm = by_part(build([item("Left Front Door", "Bent")], {"Left Front Door": rec(fd_crease=("yes", "@l"))}, hops=2))
    glass = firm.get("Left Front Door Glass") or {}
    check(glass.get("hop") == 2 and glass.get("via") == "Left Front Window Regulator"
          and glass.get("side_from") == "Left Front Door",
          "same: the middle part's firm side (from the listed door)", glass)
    low = by_part(build([item("Left Front Door", "Bent", ai_confidence="Low")],
                        {"Left Front Door": rec(fd_crease=("yes", "@l"))}, hops=2))
    hop = _hop2(list(low.values()))
    check(list(hop) == ["Door Glass (side undetermined)"] and hop["Door Glass (side undetermined)"]["side_from"] is None,
          "the door not firm (Low): the middle part has no firm side, the end part is undetermined", list(hop))
    lid = item("Boot Lid", "Bent")
    walked = {"Boot Lid": rec("whole", bl_crease=("yes", "@c"))}
    alone = _hop2(build([lid], walked, hops=2))
    check(set(alone) == {"Rear Floor Panel", "Boot Side Trim (side undetermined)"},
          "impact with no firm side at the rear: undetermined", sorted(alone))
    with_lamp = _hop2(build([lid, item("Left Tail Lamp", "Cracked")], walked, hops=2))
    check("Left Boot Side Trim" in with_lamp and with_lamp["Left Boot Side Trim"]["side_from"] == "Left Tail Lamp",
          "impact as at the first hop: the firm sided parts at that end", sorted(with_lamp))

    # Never a third hop: a chain from the second hop's own relation never fires off a second hop.
    deep = copy.deepcopy(FIXTURE)
    deep["assemblies"][1]["anchors"] = ["Air Conditioning Condenser"]
    deep["assemblies"][1]["signs"] = [{"id": "fe-bent", "ask": "Are the condenser's fins bent or its pipes kinked?",
                                       "level": "heavy", "points_to": ["condenser>radiator"],
                                       "unlocks": ["cond>rad>steering"]}]
    deep["relations"].append({"id": "radiator>steering", "assembly": "front-end", "parent": "Radiator",
                              "child": "Steering Assembly", "type": "crush", "side": "none", "min_hit": "heavy",
                              "area": "front", "reason": "Behind the radiator the steering rack can be pushed back."})
    deep["chains"].append({"id": "cond>rad>steering", "first": "condenser>radiator", "then": "radiator>steering",
                           "reason": "A condenser pushed into the radiator can push the radiator back further."})
    check(car_map.validate(deep) == [], "a map with a chain starting at a second hop's relation validates",
          car_map.validate(deep)[:2])
    dix, dtable = aw.index(deep), car_map.links_table(deep)
    answers = {"Front Bumper": rec("centre", fb_fluid=("yes", "@f")),
               "Air Conditioning Condenser": rec(fe_bent=("yes", "@c"))}
    got = by_part(build([bumper], answers, hops=2, ix=dix, table=dtable))
    check("Radiator" in got and "Steering Assembly" not in got, "bumper -> condenser -> radiator, never a third hop")
    got = _hop2(build([item("Air Conditioning Condenser", "Bent")], answers, hops=2, ix=dix, table=dtable))
    check(set(got) == {"Steering Assembly"} and got["Steering Assembly"]["via"] == "Radiator",
          "from a listed condenser the same chain is its own two hops")

    # for_parent: the case's open second hops count.
    data = {"damage_summary": [bumper], "second_look": {"items": [
        {"part": f"Open {n}", "key": f"open-{n}", "source": "link", "hop": 2} for n in range(4)], "dismissed": []}}
    room = links.for_parent(bumper, data, [], TABLE, links.Options(mode="on", max_items=40), walk=walk(yes, hops=2))
    data["second_look"]["items"] = data["second_look"]["items"][:3]
    some = links.for_parent(bumper, data, [], TABLE, links.Options(mode="on", max_items=40), walk=walk(yes, hops=2))
    check(not _hop2(room) and "Radiator" in _hop2(some), "for_parent: no room left with 4 open, room with 3")


# ---------------------------------------------------------------- 8. identity on test_links' fixtures


def _l1_walk_map() -> dict:
    """L1 as a car map (version "L1") with a position on two thirds of its
    relations, a sign per assembly and chains: so the walk's steps all
    look at these links."""
    m = car_map.from_l1(TL.TABLE)
    rels = m["relations"]
    for n, r in enumerate(rels):
        if n % 3:
            r["position"] = "corner" if n % 3 == 1 else "centre"
    by_parent: dict = {}
    for r in rels:
        by_parent.setdefault(r["parent"], []).append(r)
    chains = []
    for a in rels:
        for b in by_parent.get(a["child"], []):
            if car_map.base(b["child"]) != car_map.base(a["parent"]) and len(chains) < 20:
                chains.append({"id": f"ch-{len(chains)}", "first": a["id"], "then": b["id"], "reason": "A test chain."})
    m["chains"] = chains
    first_asm = {c["id"]: next(r["assembly"] for r in rels if r["id"] == c["first"]) for c in chains}
    for n, asm in enumerate(m["assemblies"]):
        own = [r["id"] for r in rels if r["assembly"] == asm["id"]]
        asm["signs"] = [{"id": f"s{n}", "ask": "Is this a test sign?", "level": "crush", "points_to": own[:3],
                         "unlocks": [cid for cid, aid in first_asm.items() if aid == asm["id"]]}]
    return m


def identity_replay() -> None:
    l1_map = _l1_walk_map()
    check(car_map.validate(l1_map) == [] and l1_map["chains"], "L1 as a map with positions, signs and chains validates",
          car_map.validate(l1_map)[:2])
    lix = aw.index(l1_map)
    signs = list(lix.signs)

    def answers_for(listed) -> dict:
        out = {}
        for n, it in enumerate(listed or []):
            if isinstance(it, dict) and it.get("part"):
                out[str(it["part"])] = {"hit_at": ("whole", "cannot_see")[n % 2],
                                        "signs": {s: {"answer": ("no", "cannot_see")[k % 2], "photo": None}
                                                  for k, s in enumerate(signs)}}
        return out

    compared, mismatches = [0], []
    real_build_all, real_for_parent = links.build_all, links.for_parent

    def same(a, b) -> bool:
        return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)

    def twin_build_all(listed, zone_suggestions, photos, zone_intact, table, opts, **kw):
        theirs = real_build_all(listed, zone_suggestions, photos, zone_intact, table, opts, **kw)
        if table is TL.TABLE and kw.pop("walk", None) is None:
            for w in (aw.for_links({}, lix, "full", 2), aw.for_links(answers_for(listed), lix, "full", 2)):
                args = copy.deepcopy((listed, zone_suggestions, photos, zone_intact, kw))
                mine = real_build_all(*args[:4], table, opts, walk=w, **args[4])
                compared[0] += 1
                if not same(mine, theirs):
                    mismatches.append(("build_all", [it.get("part") for it in listed if isinstance(it, dict)]))
        return theirs

    def twin_for_parent(it, data, photos, table, opts, **kw):
        theirs = real_for_parent(it, data, photos, table, opts, **kw)
        if table is TL.TABLE and kw.pop("walk", None) is None:
            listed = (data or {}).get("damage_summary") if isinstance(data, dict) else []
            w = aw.for_links(answers_for(list(listed or []) + [it]), lix, "full", 2)
            args = copy.deepcopy((it, data, photos, kw))
            mine = real_for_parent(args[0], args[1], args[2], table, opts, walk=w, **args[3])
            compared[0] += 1
            if not same(mine, theirs):
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
    check(failed == 0 and passed >= 100, f"test_links.py's own checks pass with the replay in ({passed} passed)",
          TL.FAILURES[before[1]:][:3])
    check(compared[0] >= 100, f"{compared[0]} builds replayed with a walk (no yes; whole / cannot_see; full; 2 hops)")
    check(not mismatches, "every one gave today's output", mismatches[:3])
    listed = [item("Rear Bumper", "Bent"), item("Boot Lid", "Crumpled")]
    check(links.build(listed, [], [], {}, TL.TABLE, ON) == links.build(listed, [], [], {}, TL.TABLE, ON, walk=None),
          "walk=None is today's build")


# ---------------------------------------------------------------- 9. second look wiring, purity


def wiring_and_purity() -> None:
    draft = {"assembly_walk": {"Front Bumper": rec("centre", fb_fluid=("yes", "@f"))}}
    check(aw.from_settings(draft, settings) is None, "off: no walk for links")
    with Settings(intake_assembly_walk="on", intake_walk_raise="full", intake_link_hops=2):
        with the_map(None):
            check(aw.from_settings(draft, settings) is None, "on with no map: none")
        with the_map(FIXTURE):
            w = aw.from_settings(draft, settings)
            check(isinstance(w, aw.Walk) and (w.raise_mode, w.hops, w.index.version) == ("full", 2, "W1")
                  and w.answers["Front Bumper"]["signs"]["fb-fluid"]["answer"] == "yes",
                  "on with a map: the stored walk and the knobs")
            check(aw.from_settings({}, settings) is None and aw.from_settings("junk", settings) is None,
                  "on with no stored walk: none")
    import app.intake.photos as photos_mod

    calls = []
    real_build_all, real_ordered = links.build_all, photos_mod.ordered_photos
    links.build_all = lambda *a, **kw: (calls.append(kw), ([], {}))[1]
    photos_mod.ordered_photos = lambda db, rid: []
    try:
        data = {"damage_summary": [item("Front Bumper", "Bent")]}
        second_look._with_links(None, "r", data, draft, [], [])
        with Settings(intake_assembly_walk="on"), the_map(FIXTURE):
            second_look._with_links(None, "r", data, draft, [], [])
    finally:
        links.build_all, photos_mod.ordered_photos = real_build_all, real_ordered
    check("walk" not in calls[0] and isinstance(calls[1].get("walk"), aw.Walk),
          "second_look: build_all called as before while off; with the walk while on", calls)
    src = Path(links.__file__).read_text(encoding="utf-8")
    check("car_map" not in src, "links still never names the car map module (no import cycle)")
    import ast

    tree = ast.parse(Path(aw.__file__).read_text(encoding="utf-8"))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    check(not any("car_map" in ast.dump(n) or "links" in ast.dump(n) or "app.ai" in ast.dump(n) for n in top),
          "assembly_walk imports neither the car map, links nor an AI module at its top")
    listed = [item("Front Bumper", "Bent", photo="@b"), item("Left Front Door", "Bent")]
    answers = {"Front Bumper": rec("centre", fb_fluid=("yes", "@f")), "Left Front Door": rec(fd_crease=("yes", "@d"))}
    before = copy.deepcopy((listed, answers))
    build(listed, answers, "full", 2)
    check((listed, answers) == before, "build with a walk changes none of its inputs")


# ---------------------------------------------------------------- 10. the benchmark


def benchmark() -> None:
    import benchmark_intake as bi

    defaults = vars(bi.parse_args([]))
    check({k: defaults[k] for k in bi.WALK_ARGS} == bi.WALK_ARGS, "the walk flags' defaults are WALK_ARGS (left out of args)")
    got = bi.model_settings(bi.parse_args(["--read-mode", "zones", "--assembly-walk", "--links", "shadow",
                                           "--walk-raise", "FULL", "--link-hops", "2"]))
    check({k: got.get(k) for k in ("intake_assembly_walk", "intake_walk_raise", "intake_link_hops")}
          == {"intake_assembly_walk": "on", "intake_walk_raise": "full", "intake_link_hops": 2}, "each flag its setting", got)
    got = bi.model_settings(bi.parse_args(["--read-mode", "zones", "--assembly-walk"]))
    check(got.get("intake_assembly_walk") == "on" and "intake_link_hops" not in got, "the walk alone (no linked parts)")
    for bad, says in ((["--assembly-walk"], "--read-mode zones"),
                      (["--read-mode", "zones", "--links", "on", "--walk-raise", "full"], "--assembly-walk"),
                      (["--read-mode", "zones", "--assembly-walk", "--link-hops", "2"], "--links on"),
                      (["--read-mode", "zones", "--assembly-walk", "--links", "on", "--link-hops", "3"], "--link-hops"),
                      (["--read-mode", "zones", "--assembly-walk", "--links", "on", "--walk-raise", "half"],
                       "--walk-raise")):
        try:
            bi.model_settings(bi.parse_args(bad))
            check(False, f"{bad} should stop the run")
        except SystemExit as exc:
            check(says in str(exc.code), f"stops the run: {' '.join(bad)}", exc.code)
    plain = bi.ai_settings(settings)
    check(not any(k in plain for k in ("intake_assembly_walk", "car_map_version", "intake_link_hops")),
          "ai_settings while off: as before")
    with Settings(intake_read_mode="zones", intake_assembly_walk="on", intake_links="on", intake_link_hops=2), \
            the_map(FIXTURE):
        shown = bi.ai_settings(settings)
    check(shown.get("intake_assembly_walk") == "on" and shown.get("car_map_version") == "W1"
          and shown.get("intake_walk_raise") == "one" and shown.get("intake_link_hops") == 2,
          "while on: the walk, the map's version (and sha), the raise and the hops",
          {k: shown.get(k) for k in ("intake_assembly_walk", "car_map_version", "intake_walk_raise", "intake_link_hops")})
    with Settings(intake_assembly_walk="on"):
        check("intake_assembly_walk" not in bi.ai_settings(settings), "on outside zone mode: does not act, not recorded")
    rows = bi.walk_rows({"Front Bumper": {"hit_at": "centre", "signs": {"fb-fluid": {"answer": "yes", "photo": "@p1",
                                                                                     "zone": "zone:front"}}}},
                        {"@p1": "P01"})
    check(rows["Front Bumper"]["signs"]["fb-fluid"]["photo"] == "P01", "a run keeps the walk with photos by label")
    check(set(bi._WALK_SUGGESTION_FIELDS) == {"hop", "via", "signs_yes"}, "and the linked parts' walk fields")


def main() -> int:
    from app.reports.db import init_db

    init_db()
    for name, fn in (
        ("1. settings, constants, compose", settings_and_constants),
        ("2. off (and on without a usable map) = today", off_is_today),
        ("3. on: the prompt and the schema", prompt_and_schema),
        ("4. parsing one zone's walk", parsing),
        ("5. merging the zones' walks", merging),
        ("6a. the zone stage end to end", zone_stage),
        ("6b. the pipeline end to end (reading -> AI draft -> linked parts)", pipeline_end_to_end),
        ("7a. code: the hit raise", hit_raise),
        ("7b. code: position gates", position_gates),
        ("7c. code: the bucket lift", bucket_lift),
        ("7d. code: the second hop", second_hop),
        ("8. identity: test_links' builds with a walk that says nothing", identity_replay),
        ("9. second look wiring, purity", wiring_and_purity),
        ("10. the benchmark's flags", benchmark),
    ):
        run_section(name, fn)
    print(f"\ntest_assembly_walk: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAILED [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
