"""Detection Round 2 (settings.intake_box_every "B2", intake_zone_inventory
"B1"; ai/zone_read.py and intake/zones.py "Round 2") -- offline: Gemini is
the fake of scripts/test_c10_integration.py (answering what each request's
schema asks), no server, made-up cases only.

  0. settings: both default "off", take on / off in any case, refuse a
     typo; docker-compose.prod.yml passes each once, in the shared block,
     at config's default (infra/tests/test_compose.py's allow-list too);
  1. both off: the requests and the stored data byte for byte as master
     ae0fde4's -- the full routes (single, zone mode, zone boxes with the
     linked parts) and the reads Round 2 rides on (zone boxes, a wrong-end
     zone kept, the side geometry, the parallel zone reads, the car map's
     names on) -- the same scenarios on a git archive of master and here;
     r2=None / an empty ask is today's request;
  2. the schema and prompt sizes: printed for every zone and switch
     combination, capped (SCHEMA_CAP; the main read's own schema, which the
     same light models answer, is printed beside them);
  3. B2: what is asked (names, hints, the field last), how the answer is
     read (sides, wrong end, bad boxes, caps), place_boxes (raw photo none,
     a box already there kept, one side only, close-up twins), and end to
     end (every list item boxed, the list itself unchanged, box_coverage in
     the AI draft and the run log, no extra call);
  4. B1: the inventory's names (the car map's small parts only with
     intake_car_map_names on), its caps and sides, its suggestions (cap,
     order, de-dup against the list and the suggestions, the other side's
     twin), the inventory kept apart (never in zone_intact), relabelled at
     filing; end to end;
  5. degrading: an answer cut off inside Round 2's fields is today's
     answer; bad fields are left out; a refused schema is asked again as
     today (fallback, one call more), a busy chain is not; the reading
     never fails for Round 2;
  6. the benchmark: --box-every / --zone-inventory (refused without zone
     reads), ai_settings while on, the args left out at their defaults,
     a run's box_coverage, inventory and Round 2 metrics.

    python scripts/test_detect_r2.py
    python scripts/test_detect_r2.py --probe OUT.json   (section 1's
        scenarios alone, on C10_TEST_ROOT's tree)
"""
import contextlib
import copy
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(1, str(HERE))
for _name in list(os.environ):
    if _name.startswith(("INTAKE_", "GEMINI_")) or _name in ("SECOND_LOOK_ENABLED", "BENCHMARK_SCRATCH",
                                                             "CATALOGUE_ENABLED", "TELEGRAM_FULL_DETECTION"):
        os.environ.pop(_name, None)

# The fake Gemini, the scratch database and the routes' helpers (C10_TEST_ROOT
# picks the tree the app is imported from).
import test_c10_integration as t  # noqa: E402
import test_enh_wave2 as w  # noqa: E402

from app.reports.models import IntakeSession, Report, User  # noqa: E402

ZONES = dict(intake_read_mode="zones", intake_zone_only="suggest", intake_zone_evidence="reason")
BOX = dict(ZONES, intake_zone_evidence="reason_box")
PROBE_MODES = {
    "single": {},
    "zones": dict(ZONES, intake_check_mode="lite"),
    "box_links": dict(BOX, intake_links="on", intake_link_check="lite", intake_check_mode="lite"),
}
# The reads Round 2 rides on, off: (settings, answers, case rows).
READ_SCENARIOS = {
    "box": (dict(BOX), {}, None),
    "wrong_end_keep": (dict(ZONES, intake_zone_wrong_end="keep"), {"zone": w.wrong_end_zone}, None),
    "side_vote": (dict(ZONES, intake_side_geometry="vote"),
                  {"zone": w.side_zone("left"), "sort": w.geometry_sort, "draft": w.no_door_draft}, w.SIDE_ROWS),
    "parallel": (dict(BOX, intake_main_read_fallback="zones", intake_parallel_reads="on"), {}, None),
    "car_names": (dict(BOX, intake_car_map_names="on"), {}, None),
}
BASE_REF = "ae0fde4"  # origin/master: what this branch starts from


def read_scenario(name: str) -> dict:
    """One read-only scenario, normalised: what was asked and what was kept."""
    values, answers, rows = READ_SCENARIOS[name]
    with w.a_case(values, answers, rows) as (client, rid, pids, uid, state):
        ids = {rid: "<rid>", uid: "<uid>", t.db_row(User, uid).email: "<email>",
               **{pid: f"<p{i + 1:02d}>" for i, pid in enumerate(pids)}}
        session = t.db_row(IntakeSession, rid)
        out = {
            "stage": state["stage"], "error": state["error"],
            "data": t.db_row(Report, rid).data, "ai_draft": session.ai_draft, "read_photo_ids": session.read_photo_ids,
            "runs": t._run_rows(rid), "photos": w.photos_of(rid),
            "requests": sorted((t.normalised(t._request(r), ids) for r in t.REQUESTS),
                               key=lambda r: json.dumps(r, sort_keys=True)),
        }
        return t.normalised(out, ids)


def probe(out: Path) -> int:
    """Section 1's scenarios on whichever tree the app came from."""
    t.init_db()
    t.OFF_MODES.update(PROBE_MODES)
    import app

    result = {"_tree": {"app": str(Path(app.__file__).resolve().parent)}}
    for mode in PROBE_MODES:
        artefacts, facts = t.off_scenario(mode)
        result[mode] = {"artefacts": artefacts, "facts": facts}
    for name in READ_SCENARIOS:
        result[name] = read_scenario(name)
    out.write_text(json.dumps(result, sort_keys=True, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__" and "--probe" in sys.argv:
    sys.exit(probe(Path(sys.argv[sys.argv.index("--probe") + 1])))

# ------------------------------------------------------------- this tree only

from app import config  # noqa: E402
from app.ai import evidence, extraction, structured, zone_read  # noqa: E402
from app.ai.zone_read import Round2Ask  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import zones  # noqa: E402
from app.reports import filing  # noqa: E402
from app.reports.models import AiRun  # noqa: E402
from app.reports.schema import SecondLookItem  # noqa: E402
from app.reports.taxonomy import known_part  # noqa: E402

ROOT = HERE.parent
REPO = ROOT.parent.parent
PY = sys.executable
LABELS = [f"P{i:02d}" for i in range(1, 13)]
# Every zone schema Round 2 may send, at most this many characters (json.
# dumps). Today's measure 1.9k-3.4k with the evidence boxes (4.0k for the
# close-up zone with every light-model option on); the main read's schema,
# which the same light models answer, is ~23k. Round 2 adds at most ~2.3k
# (the close-up zone's inventory names its 43 parts); a schema a model
# refuses anyway is asked again as today (section 5).
SCHEMA_CAP = 6500
# Round 2's sentences, at most this many characters more in a zone prompt.
PROMPT_CAP = 1200

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}


def check(cond, label: str, why="") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append((_section["name"], label))
        print(f"    FAIL {label}" + (f"\n         {str(why)[:1500]}" if why != "" else ""))
    return bool(cond)


def run_section(name: str, fn) -> None:
    _section["name"] = name
    print(f"\n{name}")
    t.REQUESTS.clear()
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()


# ------------------------------------------------------------- the fake's answers


class Raw:
    """An answer sent as this text, not as JSON (a cut-off answer)."""

    def __init__(self, text: str):
        self.text = text


class R2Fake:
    """test_c10_integration's fake, but an answer may be Raw text or an
    exception to raise, and the output tokens are the answer's length / 4
    (so the run log's estimate has something to share out)."""

    def create(self, **kwargs):
        with t._lock:
            t.REQUESTS.append(kwargs)
        labels = t._labels(kwargs["input"])
        out = t.ANSWERS[t.kind_of(kwargs)](kwargs, labels)
        if isinstance(out, BaseException):
            raise out
        text = out.text if isinstance(out, Raw) else json.dumps(out)
        return SimpleNamespace(output_text=text, usage=SimpleNamespace(total_input_tokens=1000,
                                                                       total_output_tokens=max(1, len(text) // 4)))


@contextlib.contextmanager
def r2_fake():
    old = structured.get_client
    structured.get_client = lambda key=None: SimpleNamespace(interactions=R2Fake())
    try:
        yield
    finally:
        structured.get_client = old


def _props(kwargs) -> dict:
    return kwargs["response_format"]["schema"]["properties"]


BOXES = {"P03": [300, 200, 700, 800], "P04": [250, 100, 600, 400], "P05": [100, 150, 450, 850],
         "P07": [200, 100, 800, 900], "P08": [400, 300, 700, 600], "P09": [150, 150, 500, 500]}


def r2_zone(inventory=None, locate_parts=None):
    """The zone reads as the plain fake answers them -- the rear finds the
    bumper (P03) and the left tail lamp (P04), the left side the rear door
    (P07) -- and, when the schema asks, `locate` (each asked part on its
    photo: Rear Bumper P03, Boot Lid P05, Rear Door P07; or locate_parts'
    {name: label}) and `inventory` (inventory: {zone: [rows]})."""
    where = {"Rear Bumper": "P03", "Boot Lid": "P05", "Rear Door": "P07", **(locate_parts or {})}

    def answer(kwargs, labels):
        props = _props(kwargs)
        system = kwargs["system_instruction"]
        zone = "rear" if "the car's rear" in system else "left" if "one side of the car" in system else "other"
        if zone == "rear":
            out = t._zone_answer(kwargs, labels)
        elif zone == "left":
            item = w._zone_item(props["damage"]["items"]["properties"], "P07", "Rear Door", "High", ("Dented",))
            out = {"zone_side_seen": "left", "damage": [item] if "P07" in labels else [], "checked_intact": []}
        else:
            out = {"zone_side_seen": "unclear", "damage": [], "checked_intact": []}
        if "locate" in props:
            asked = props["locate"]["items"]["properties"]["part"]["enum"]
            out["locate"] = [{"photo_reference": where[name], "part": name, "box_2d": BOXES[where[name]]}
                             for name in asked if where.get(name) in labels]
        if "inventory" in props:
            names = props["inventory"]["items"]["properties"]["part"]["enum"]
            out["inventory"] = [row for row in (inventory or {}).get(zone, [])
                                if row["part"] in names and row["photo_reference"] in labels]
        return out
    return answer


def inv(label: str, part: str, damaged: bool, confidence: str | None = "Medium", box=None) -> dict:
    row = {"photo_reference": label, "part": part, "damaged": damaged,
           "box_2d": box or BOXES.get(label, [100, 100, 400, 400])}
    if confidence:
        row["ai_confidence"] = confidence
    return row


REAR_INVENTORY = {"rear": [
    inv("P05", "Boot Lid", True, "High"),            # listed: no suggestion
    inv("P05", "Boot Floor Board", True, "Medium"),  # a suggestion
    inv("P05", "Rear Windscreen", False, "High"),    # intact: kept apart
    inv("P04", "Right Tail Lamp", True, "Low"),      # a suggestion (the left one is only suggested)
    inv("P03", "Rear Bumper", True, "High"),         # listed
    inv("P03", "Rear Number Plate Lamp", True, "High"),  # only with the car map's names
]}


def zone_run(rid: str) -> AiRun | None:
    db = t.SessionLocal()
    try:
        row = (db.query(AiRun).filter(AiRun.report_id == rid, AiRun.kind == "read_zones")
               .order_by(AiRun.started_at.desc()).first())
        if row is not None:
            db.expunge(row)
        return row
    finally:
        db.close()


def shape(items: list[dict], pids: list[str], full: bool = False) -> list:
    """A case's list with its photos by position (two cases' photo ids
    differ): part, found_by and seen_in -- or, full, every field."""
    ids = {pid: f"<p{i + 1:02d}>" for i, pid in enumerate(pids)}
    if full:
        return t.normalised(items, ids)
    return t.normalised([(it.get("part"), it.get("found_by"), it.get("seen_in")) for it in items], ids)


def look_items(data: dict) -> list[dict]:
    return (data.get("second_look") or {}).get("items") or []


def zone_requests(requests=None) -> list[dict]:
    return [r for r in (t.REQUESTS if requests is None else requests) if t.kind_of(r) == "zone"]


@contextlib.contextmanager
def r2_case(values: dict, answers: dict | None = None, rows: list | None = None):
    """w.a_case with the Round 2 fake."""
    with r2_fake(), w.a_case(values, answers, rows) as got:
        yield got


# ------------------------------------------------------------- 0. settings


def settings_section() -> None:
    fresh = config.Settings()
    check((fresh.intake_box_every, fresh.intake_zone_inventory) == ("off", "off")
          and (settings.intake_box_every, settings.intake_zone_inventory) == ("off", "off"),
          "both default to off, and this process runs at them")
    problems = []
    for name in ("intake_box_every", "intake_zone_inventory"):
        for given, want in (("ON", "on"), ("on", "on"), (" Off ", "off"), ("", "off")):
            if getattr(config.Settings(**{name: given}), name) != want:
                problems.append((name, given, "not taken"))
        for bad in ("yes", "true", "1", "maybe"):
            try:
                config.Settings(**{name: bad})
                problems.append((name, bad, "a typo accepted"))
            except Exception:
                pass
    check(not problems, "each takes on / off in any case, blank is off, a typo stops the app", problems)
    compose = (REPO / "infra" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    anchor, services = compose.index("x-bot-service: &bot-service"), compose.index("\nservices:")
    bad = []
    for env in ("INTAKE_BOX_EVERY", "INTAKE_ZONE_INVENTORY"):
        want = f"    - {env}=${{{env}:-off}}"
        lines = [ln for ln in compose.splitlines() if ln.startswith(f"    - {env}=")]
        at = compose.find(f"- {env}=")
        if lines != [want] or not anchor < at < services:
            bad.append((env, lines, want))
    check(not bad, "docker-compose.prod.yml: each once, in the shared x-bot-service block, at config's default", bad)
    allow = (REPO / "infra" / "tests" / "test_compose.py").read_text(encoding="utf-8")
    check('"INTAKE_BOX_EVERY": "off"' in allow and '"INTAKE_ZONE_INVENTORY": "off"' in allow,
          "infra/tests/test_compose.py allows both lines, at off")
    with t.Settings(intake_read_mode="zones"):
        check(zones.extras_for([]) is None, "zone mode with every Wave 2 / Round 2 option off: no ZoneExtras at all")
        with t.Settings(intake_box_every="on"):
            got = zones.extras_for([])
            check(got is not None and got.box_every and not got.inventory and not got.car_names
                  and got.geometry_mode == "off" and got.wrong_end_mode == "drop",
                  "B2 on: ZoneExtras with box_every, Wave 2's options as off", got)
        with t.Settings(intake_zone_inventory="on", intake_car_map_names="on"):
            got = zones.extras_for([])
            check(got is not None and got.inventory and got.car_names and not got.box_every,
                  "B1 on with the car map's names: ZoneExtras with inventory and car_names", got)


# ------------------------------------------------------------- 1. off, as master


def clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not (k.startswith("INTAKE_") or k.startswith("GEMINI_")
                                                       or k in ("C10_TEST_ROOT", "DATABASE_URL", "STORAGE_DIR",
                                                                "SECRETS_DIR", "BENCHMARK_SCRATCH"))}
    env.update(PYTHONIOENCODING="utf-8", **extra)
    return env


def base_equal() -> None:
    work = Path(tempfile.mkdtemp(prefix="detect-r2-base-"))
    tar = work / "base.tar"
    made = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", "-o", str(tar), BASE_REF,
                           "apps/bot-service"], capture_output=True, text=True)
    if not check(made.returncode == 0, f"master {BASE_REF}'s bot-service from git (archive)", made.stderr[-300:]):
        return
    with tarfile.open(tar) as archive:
        archive.extractall(work / "tree", filter="data")
    base_root = work / "tree" / "apps" / "bot-service"
    outs = {}
    for name, root in (("base", base_root), ("here", ROOT)):
        out = work / f"{name}.json"
        proc = subprocess.run([PY, str(HERE / "test_detect_r2.py"), "--probe", str(out)], capture_output=True,
                              text=True, encoding="utf-8", cwd=str(root), env=clean_env(C10_TEST_ROOT=str(root)),
                              timeout=1800)
        if not check(proc.returncode == 0 and out.exists(), f"the scenarios ran on {name}'s tree",
                     (proc.stdout + proc.stderr)[-1500:]):
            return
        outs[name] = json.loads(out.read_text(encoding="utf-8"))
        check(Path(outs[name]["_tree"]["app"]).parent == root.resolve(), f"... with {name}'s own app",
              outs[name]["_tree"])
    for mode, values in PROBE_MODES.items():
        mine, theirs = outs["here"][mode], outs["base"][mode]
        check(mine["facts"].get("stage") == "ready" and mine["facts"].get("sign_off") == 200,
              f"{mode}: read, filed and signed off")
        check(mine == theirs, f"{mode} ({', '.join(f'{k}={v}' for k, v in values.items()) or 'the defaults'}): "
                              f"every request and the stored data byte for byte as {BASE_REF}'s",
              w._first_diff(mine, theirs))
    for name in READ_SCENARIOS:
        mine, theirs = outs["here"][name], outs["base"][name]
        check(mine["stage"] == "ready", f"{name}: read")
        check(mine == theirs, f"{name}: the requests, the run log, the photos and the stored draft as {BASE_REF}'s",
              w._first_diff(mine, theirs))
    text = json.dumps(outs["here"])
    check(not any(word in text for word in ("locate", "inventory", "box_coverage", "zone_inventory", '"r2"')),
          "nothing of Round 2 anywhere in what the off scenarios sent or kept")


def requests_unchanged() -> None:
    """r2=None and an empty ask are today's request, prompt and parse."""
    walkless = []
    for zone in zone_read.ZONE_NAMES:
        for kw in ({}, {"grid": True}, {"evidence_mode": "reason_box"},
                   {"packs": ["intact", "orient", "cues", "small"], "photo_half": True}):
            base = (zone_read.system_prompt(zone, **kw), zone_read.response_schema(zone, LABELS, **kw))
            for r2 in (None, Round2Ask()):
                got = (zone_read.system_prompt(zone, r2=r2, **kw), zone_read.response_schema(zone, LABELS, r2=r2, **kw))
                if json.dumps(got) != json.dumps(base):
                    walkless.append((zone, kw, r2))
    check(not walkless, "every zone and option: r2=None or an empty ask sends today's prompt and schema", walkless)
    raw = json.dumps({"zone_side_seen": "rear", "damage": [{"photo_reference": "P03", "part": "Rear Bumper",
                                                            "ai_confidence": "High", "condition_tokens": ["Cracked"]}],
                      "checked_intact": ["Boot Lid"], "locate": [{"photo_reference": "P03", "part": "Rear Bumper",
                                                                   "box_2d": [1, 2, 300, 400]}]})
    a, b = zone_read.parse_answer("rear", raw, LABELS), zone_read.parse_answer("rear", raw, LABELS, r2=None)
    check(a == b and not a.located and a.r2_chars == 0, "parse_answer without r2: Round 2's fields not even read", a)
    try:
        zone_read.parse_answer("rear", raw[:-30], LABELS)
        cut = False
    except ValueError:
        cut = True
    check(cut, "without r2 a cut-off answer still fails as today (the next model is asked)")


# ------------------------------------------------------------- 2. sizes


LISTED = ["Rear Bumper", "Boot Lid", "Left Tail Lamp", "Right Tail Lamp", "Tail Lamp (side undetermined)",
          "Left Rear Door", "Left Front Fender", "Front Bumper", "Rear Bumper Reflector (side undetermined)",
          "Left Rear Quarter Panel", "Front Grille", "Bonnet", "Left Headlamp", "Right Front Door",
          "Boot Lid Lock", "Rear Windscreen"]


def enum_values(node) -> int:
    """How many enum values a schema holds (what makes a schema "too
    complex" for a light model is its states, not its characters)."""
    if isinstance(node, dict):
        return len(node.get("enum") or []) + sum(enum_values(v) for k, v in node.items() if k != "enum")
    if isinstance(node, list):
        return sum(enum_values(v) for v in node)
    return 0


def size_section() -> None:
    main = extraction._response_schema()
    print(f"         (the main read's schema, which the same light models answer: {len(json.dumps(main))} characters, "
          f"{enum_values(main)} enum values)")
    print("         zone   options                     schema  (+ vs today)  prompt +  enum values")
    worst = {}
    over = []
    for zone in zone_read.ZONE_NAMES:
        for kw_name, kw in (("reason_box", {"evidence_mode": "reason_box"}),
                            ("all light options", {"evidence_mode": "reason_box", "grid": True, "photo_half": True,
                                                   "packs": ["intact", "orient", "cues", "small"]})):
            today = len(json.dumps(zone_read.response_schema(zone, LABELS, **kw)))
            prompt = len(zone_read.system_prompt(zone, **kw))
            for box_every, inventory, names in ((False, False, False), (True, False, False), (False, True, False),
                                                (False, True, True), (True, True, False), (True, True, True)):
                locate = zone_read.locate_names(zone, LISTED) if box_every else []
                ask = Round2Ask(locate=locate, hints={n: ["P03", "P04"] for n in locate},
                                inventory=zone_read.inventory_names(zone, names) if inventory else [])
                size = len(json.dumps(zone_read.response_schema(zone, LABELS, r2=ask, **kw)))
                more = len(zone_read.system_prompt(zone, r2=ask, **kw)) - prompt
                tag = f"B2={'on' if box_every else 'off'} B1={'on' if inventory else 'off'}{'+names' if names else ''}"
                if kw_name == "reason_box" or (box_every and inventory and names):
                    states = enum_values(zone_read.response_schema(zone, LABELS, r2=ask, **kw))
                    print(f"         {zone:<6} {tag:<18} {kw_name[:9]:<9} {size:>6}  (+{size - today:>4})  {more:>8}"
                          f"  {states:>11}")
                worst[zone] = max(worst.get(zone, 0), size)
                if size > SCHEMA_CAP or more > PROMPT_CAP:
                    over.append((zone, kw_name, tag, size, more))
                if not (box_every or inventory) and size != today:
                    over.append((zone, kw_name, "off differs", size, today))
    check(not over, f"every zone, option and switch combination: the schema at most {SCHEMA_CAP} characters and "
                    f"Round 2's sentences at most {PROMPT_CAP} (largest: {worst})", over)
    ask = Round2Ask(locate=zone_read.locate_names("rear", LISTED), inventory=zone_read.inventory_names("rear", True))
    schema = zone_read.response_schema("rear", LABELS, evidence_mode="reason_box", r2=ask)
    item = schema["properties"]["inventory"]["items"]
    last = ["locate", "inventory"]
    check(list(schema["properties"])[-2:] == last and schema["required"][-2:] == last
          and "maxItems" not in json.dumps(schema) and "minItems" not in json.dumps(schema)
          and "description" not in json.dumps(schema["properties"]["inventory"])
          and set(item["properties"]) == {"photo_reference", "part", "damaged", "ai_confidence", "box_2d"},
          "the fields last (an answer cut off there is today's), no min / max items, no descriptions",
          schema["required"])
    more = LISTED + ["Left Wing Mirror", "Right Wheel Arch Trim (side undetermined)"]
    check(len(zone_read.locate_names("close", more)) == zone_read.MAX_LOCATE,
          f"at most {zone_read.MAX_LOCATE} listed parts asked a call")


# ------------------------------------------------------------- 3. B2


def locate_units() -> None:
    names = zone_read.locate_names("rear", ["Rear Bumper", "Tail Lamp (side undetermined)", "Other / Not Listed",
                                            "Left Front Door", "Boot Lid"])
    check(names == ["Rear Bumper", "Boot Lid", "Left Tail Lamp", "Right Tail Lamp"],
          "rear: the listed parts its checklist names, in its order; an undetermined lamp asks both sides; "
          "'Other' and a door never", names)
    check(zone_read.locate_names("left", ["Left Front Door", "Right Rear Door", "Door (side undetermined)"])
          == ["Front Door", "Rear Door"], "a side zone asks the families, without a side")
    check(zone_read.locate_names("close", ["Left Tail Lamp"]) == ["Tail Lamp (side undetermined)"],
          "a close-up zone asks the unsided name")
    check(zone_read.locate_names("front", ["Rear Bumper", "Boot Lid"]) == [],
          "nothing of its area listed: nothing asked")
    job = zones.ZoneJob("rear", [(f"P0{n}", f"a{n}", "x", "damage") for n in (3, 5, 7)])
    extras = zones.ZoneExtras(box_every=True, listed=[
        {"part": "Rear Bumper", "photo_reference": "@a3"},
        {"part": "Tail Lamp (side undetermined)", "photo_reference": "@a5"},
        {"part": "Boot Lid", "photo_reference": "@a9"}])
    ask = zones.round2_ask(job, extras)
    check(ask is not None and ask.locate == ["Rear Bumper", "Boot Lid", "Left Tail Lamp", "Right Tail Lamp"]
          and ask.hints == {"Rear Bumper": ["P03"], "Left Tail Lamp": ["P05"], "Right Tail Lamp": ["P05"]}
          and ask.inventory == [], "round2_ask: the names, each hinted with its listed item's own photo when sent", ask)
    check(zones.round2_ask(job, zones.ZoneExtras(box_every=True, listed=None)) is None
          and zones.round2_ask(job, zones.ZoneExtras(box_every=True, listed=[])) is None
          and zones.round2_ask(job, None) is None,
          "no list known yet (the reads beside or before the main read), or none: nothing asked -- today's call")
    text = zone_read.system_prompt("rear", evidence_mode="reason", r2=ask)
    check("These parts are on the damage list already: Rear Bumper (P03), Boot Lid, Left Tail Lamp (P05), Right Tail "
          "Lamp (P05)." in text and text.index("'locate'") < text.index("Only the insured car"),
          "the prompt names them with their photos, before the closing line", text[-700:])
    # The answer.
    raw = {"zone_side_seen": "rear", "damage": [], "checked_intact": [], "locate": [
        {"photo_reference": "P03", "part": "Rear Bumper", "box_2d": [300, 200, 700, 800]},
        {"photo_reference": "P03", "part": "Rear Bumper", "box_2d": [10, 10, 90, 90]},       # twice: the first kept
        {"photo_reference": "P05", "part": "Boot Lid", "box_2d": [500, 500, 400, 400]},      # inverted: none
        {"photo_reference": "P99", "part": "Left Tail Lamp", "box_2d": [100, 100, 300, 300]},  # not sent
        {"photo_reference": "P05", "part": "Bonnet", "box_2d": [100, 100, 300, 300]},       # not asked
        {"photo_reference": "P05", "part": "Right Tail Lamp", "box_2d": [100, 600, 300, 900]},
        "junk"]}
    got = zone_read.parse_answer("rear", json.dumps(raw), ["P03", "P05"], r2=ask)
    check(got.located == [{"part": "Rear Bumper", "photo": "P03", "box": [300, 200, 700, 800]},
                          {"part": "Right Tail Lamp", "photo": "P05", "box": [100, 600, 300, 900]}],
          "read: each asked part once, on a photo sent, with a box that is one; the rest left out", got.located)
    check(got.r2_chars == len(json.dumps(raw["locate"], separators=(",", ":"))) and got.answer_chars > got.r2_chars,
          "the characters of the Round 2 fields are counted (the run log's extra tokens)")
    side = Round2Ask(locate=["Front Door", "Rear Door"])
    for seen, want in (("left", "Left Rear Door"), ("right", "Right Rear Door"),
                       ("unclear", "Door (side undetermined)")):
        got = zone_read.parse_answer("left", json.dumps({"zone_side_seen": seen, "damage": [], "checked_intact": [],
                                                         "locate": [{"photo_reference": "P07", "part": "Rear Door",
                                                                     "box_2d": [1, 1, 500, 500]}]}), ["P07"], r2=side)
        check([x["part"] for x in got.located] == [want], f"a side zone that sees {seen}: {want}", got.located)
    wrong = zone_read.parse_answer("rear", json.dumps({**raw, "zone_side_seen": "front"}), ["P03", "P05"], r2=ask,
                                   wrong_end="keep")
    check(wrong.located == [] and wrong.items == [], "an end zone whose photos show the other end: nothing placed")


def _facts(folder: Path, raw: set[str] = frozenset()) -> dict:
    from PIL import Image

    out = {}
    for pid in ("a3", "a4", "a5", "a7", "a9"):
        path = folder / f"{pid}.webp"
        Image.new("RGB", (320, 240), "grey").save(path, "WEBP")
        out[pid] = evidence.PhotoFacts(ai_path=str(path), raw=pid in raw)
    return out


def _item(part: str, ref: str | None, **more) -> dict:
    item = {"part": part, "photo_reference": ref, "ai_confidence": "High", "found_by": ["overall"],
            "seen_in": [ref] if ref else [], "bbox_2d": None, "reason": None,
            "evidence": [evidence.entry(ref, "overall", confidence="High")] if ref else []}
    item.update(more)
    return item


def _src(part: str, photo: str, read: str = "zone:rear", box=(100, 100, 400, 500)) -> dict:
    return {"part": part, "photo": photo, "box": list(box), "read": read}


def place_units() -> None:
    folder = Path(tempfile.mkdtemp(prefix="detect-r2-frames-"))
    facts = _facts(folder, raw={"a9"})
    boxed = _item("Boot Lid", "@a5", evidence=[evidence.entry("@a5", "zone:rear", box=[50, 50, 300, 300],
                                                              confidence="Medium")])
    listed = [
        _item("Rear Bumper", "@a3"),
        boxed,
        _item("Left Rear Door", "@a7", reason="Pushed in."),
        _item("Tail Lamp (side undetermined)", "@a4"),
        _item("Rear Bumper Reflector (side undetermined)", "@a4"),
        _item("Other / Not Listed", "@a3", other_part_name="Rear air vent"),
        _item("Rear Windscreen", "@a9", bbox_2d=[100, 100, 300, 300]),  # its box on a raw photo: it won't stand
    ]
    listed[2]["evidence"][0]["reason"] = "Pushed in."
    before = copy.deepcopy(listed)
    sources = [
        _src("Rear Bumper", "@a5"), _src("Rear Bumper", "@a3", box=(300, 200, 700, 800)),   # its own photo first
        _src("Boot Lid", "@a5"),
        _src("Left Rear Door", "@a7", read="zone:left"),
        _src("Left Tail Lamp", "@a4"),                                                      # one side only
        _src("Left Rear Bumper Reflector", "@a4"), _src("Right Rear Bumper Reflector", "@a4"),  # both: no answer
        _src("Rear Windscreen", "@a9"), _src("Rear Windscreen", "@a5", box=(10, 100, 200, 900)),  # raw photo: no
    ]
    placed = zones.place_boxes(listed, sources, facts)
    by = {it["part"]: it for it in listed}
    bumper = by["Rear Bumper"]["evidence"]
    check(placed == 4 and len(bumper) == 1 and bumper[0]["photo"] == "@a3" and bumper[0]["box"] == [300, 200, 700, 800]
          and bumper[0]["by"] == "locate:zone:rear" and bumper[0]["box_kind"] == "ai"
          and bumper[0]["confidence"] == "High",
          "a listed part with no box: the located box on its own photo, by locate:zone:rear, in place of the box-less "
          "entry there and with its confidence", by["Rear Bumper"])
    check(by["Boot Lid"] == before[1], "a listed part already boxed keeps its box (nothing added)")
    door = by["Left Rear Door"]["evidence"][0]
    check(door["by"] == "locate:zone:left" and door["reason"] == "Pushed in.", "... and the words said there", door)
    lamp = by["Tail Lamp (side undetermined)"]["evidence"]
    check([e["by"] for e in lamp] == ["locate:zone:rear"],
          "an undetermined part placed on one side only: that box", lamp)
    check(by["Rear Bumper Reflector (side undetermined)"] == before[4],
          "an undetermined part with both sides placed: none -- which one isn't known")
    check(by["Other / Not Listed"] == before[5], "'Other / Not Listed': never")
    windscreen = by["Rear Windscreen"]["evidence"]
    check([(e["photo"], e["by"]) for e in windscreen] == [("@a9", "overall"), ("@a5", "locate:zone:rear")],
          "a box on a photo the AI got raw doesn't stand: the item gets one on another photo; none is placed on a "
          "raw photo", windscreen)
    check(all((a["found_by"], a["seen_in"], a["ai_confidence"], a["part"]) == (b["found_by"], b["seen_in"],
                                                                                b["ai_confidence"], b["part"])
              for a, b in zip(listed, before)), "found_by, seen_in, the confidence and the part never change")
    # Through evidence.finish: frames, raw boxes dropped, bbox_2d from the own photo's box.
    evidence.finish(listed, [], facts)
    bumper = by["Rear Bumper"]
    check(bumper["bbox_2d"] == [300, 200, 700, 800] and bumper["evidence"][0]["img_w"] == 320
          and bumper["evidence"][0]["img_h"] == 240, "evidence.finish: the frame set, bbox_2d drawn from it", bumper)
    check(by["Rear Windscreen"]["bbox_2d"] is None and by["Rear Windscreen"]["evidence"][-1]["img_w"] == 320,
          "... the raw photo's bbox_2d dropped, the located one kept", by["Rear Windscreen"])
    # The side resolver puts the undetermined lamp on a side afterwards.
    lamp = by["Tail Lamp (side undetermined)"]
    right = {**copy.deepcopy(lamp), "part": "Right Tail Lamp"}
    left = {**copy.deepcopy(lamp), "part": "Left Tail Lamp"}
    bumper_copy = copy.deepcopy(by["Rear Bumper"])
    dropped = zones.drop_crossed_boxes([right, left, bumper_copy], zones.ZoneExtras(box_every=True, located=sources))
    check(dropped == 1 and lamp["bbox_2d"] == [100, 100, 400, 500] and right["bbox_2d"] is None
          and not any(e.get("box") for e in right["evidence"]) and left["evidence"] == lamp["evidence"]
          and bumper_copy == by["Rear Bumper"],
          "placed from 'Left Tail Lamp', then put on the right by the side resolver: the box goes (on the left: it "
          "stays; an exact one is never touched)", right)
    cov = zones.box_coverage(listed, 1)
    check(cov == {"items": 7, "boxed": 5, "ai_boxed": 5, "located": 4, "boxed_before_locate": 1},
          "box_coverage: items, boxed, the AI's, located, and before", cov)
    # A close-up's unsided name: one listed twin only.
    two = [_item("Left Tail Lamp", "@a4"), _item("Right Tail Lamp", "@a5")]
    one = [_item("Left Tail Lamp", "@a4")]
    src = [_src("Tail Lamp (side undetermined)", "@a4", read="zone:close")]
    check(zones.place_boxes(two, src, facts) == 0 and zones.place_boxes(one, src, facts) == 1,
          "a close-up's unsided find places a sided listed part only when no other listed part is its twin")
    check(zones.place_boxes([_item("Left Tail Lamp", "@a4")], [_src("Right Tail Lamp", "@a4")], facts) == 0,
          "never the other side's twin")


def b2_end_to_end() -> None:
    off_rows: dict = {}
    for name, values in (("off", ZONES), ("on", dict(ZONES, intake_box_every="on")),
                         ("box_on", dict(BOX, intake_box_every="on"))):
        with r2_case(values, {"zone": r2_zone()}) as (client, rid, pids, uid, state):
            session = t.db_row(IntakeSession, rid)
            off_rows[name] = {"state": state["stage"], "data": t.db_row(Report, rid).data, "ai_draft": session.ai_draft,
                              "zone": zone_run(rid), "requests": list(t.REQUESTS), "pids": pids}
    off, on, box_on = off_rows["off"], off_rows["on"], off_rows["box_on"]
    check(on["state"] == "ready" and box_on["state"] == "ready", "the readings end ready")
    listed = on["data"]["damage_summary"]
    check([it["part"] for it in listed] == [it["part"] for it in off["data"]["damage_summary"]]
          == ["Rear Bumper", "Boot Lid", "Left Rear Door"], "the list itself as off's", [it["part"] for it in listed])
    check(shape(listed, on["pids"]) == shape(off["data"]["damage_summary"], off["pids"])
          and [it.get("ai_confidence") for it in listed]
          == [it.get("ai_confidence") for it in off["data"]["damage_summary"]],
          "found_by, seen_in and each confidence as off's: a located box is not a find")
    p = {f"P{i + 1:02d}": f"@{pid}" for i, pid in enumerate(on["pids"])}
    located = {it["part"]: [(e["photo"], e["box"], e["by"]) for e in it["evidence"] if e.get("box")] for it in listed}
    check(located == {"Rear Bumper": [(p["P03"], BOXES["P03"], "locate:zone:rear")],
                      "Boot Lid": [(p["P05"], BOXES["P05"], "locate:zone:rear")],
                      "Left Rear Door": [(p["P07"], BOXES["P07"], "locate:zone:left")]},
          "every list item boxed on its own photo by the zone read that saw it (reasons only, no zone box before)",
          located)
    check(all(e.get("img_w") == 320 and e.get("img_h") == 240 for it in listed for e in it["evidence"] if e.get("box"))
          and all(it.get("bbox_2d") for it in listed), "each with its frame, and bbox_2d drawn from it")
    check(not any(e.get("box") for it in off["data"]["damage_summary"] for e in it["evidence"]),
          "off: none of them had a box (the owner's question)")
    cov = on["ai_draft"].get("box_coverage")
    check(cov == {"items": 3, "boxed": 3, "ai_boxed": 3, "located": 3, "boxed_before_locate": 0},
          "ai_draft['box_coverage']: 3 of 3 boxed, 0 before", cov)
    detail = on["zone"].photos_sent
    check(detail.get("box_coverage") == cov and detail["r2"]["located"] == 3 and detail["r2"]["list_known"] is True
          and detail["r2"]["extra_output_tokens_est"] > 0 and detail["r2"]["fallbacks"] == 0,
          "the zone run's log: the same coverage, what was placed, the extra output tokens", detail.get("r2"))
    entries = {e["zone"]: e for e in detail["zones"]}
    check(entries["rear"]["r2"]["locate_asked"] == 2 and entries["rear"]["r2"]["located"] == 2
          and entries["left"]["r2"]["locate_asked"] == 1 and entries["rear"]["r2"]["extra_output_tokens_est"] > 0,
          "each zone's entry: asked, placed, extra tokens", entries["rear"]["r2"])
    check("box_coverage" not in off["ai_draft"] and "r2" not in off["zone"].photos_sent,
          "off: no box_coverage, nothing in the run log")
    zones_off, zones_on = zone_requests(off["requests"]), zone_requests(on["requests"])
    check(len(zones_on) == len(zones_off) and len(on["requests"]) == len(off["requests"]),
          "no extra call: the same zone calls, the same requests in all", (len(zones_off), len(zones_on)))
    rear = next(r for r in zones_on if "the car's rear" in r["system_instruction"])
    check(list(_props(rear))[-1] == "locate" and _props(rear)["locate"]["items"]["properties"]["part"]["enum"]
          == ["Rear Bumper", "Boot Lid"] and "Rear Bumper (P03), Boot Lid (P05)" in rear["system_instruction"],
          "the rear call asked for the listed rear parts, with their photos", _props(rear).get("locate"))
    tags = {a.get("prompt", "") for a in on["zone"].attempts}
    check(any("+locate-r2@" in tag for tag in tags), "the run log's tries are tagged +locate-r2", tags)
    # With the zone reads' own boxes (reason_box) the boxed items keep them.
    items = {it["part"]: it for it in box_on["data"]["damage_summary"]}
    bumper = [(e["by"], e["box"]) for e in items["Rear Bumper"]["evidence"] if e.get("box")]
    cov = box_on["ai_draft"]["box_coverage"]
    check(bumper == [("zone:rear", [120, 140, 520, 600])] and cov["boxed_before_locate"] == 2 and cov["located"] == 1,
          "with the zone reads' boxes: the bumper and the door keep theirs, only the boot lid is located", bumper)


# ------------------------------------------------------------- 4. B1


def inventory_units() -> None:
    check(zone_read.inventory_names("rear") == zone_read.checklist("rear")[:-1]
          and not set(zone_read.SMALL_PARTS["rear"]) & set(zone_read.inventory_names("rear")),
          "the inventory's names: the zone's checklist -- no car map name while intake_car_map_names is off")
    check(zone_read.inventory_names("rear", True)[-4:] == zone_read.SMALL_PARTS["rear"],
          "with it on, the zone's small car map parts after them")
    bad = []
    for zone in zone_read.ZONE_NAMES:
        for name in zone_read.inventory_names(zone, True):
            for seen in (("left", "right", "unclear") if zone in zone_read.SIDE_ZONES else ("rear",)):
                part = zone_read.inventory_part(zone, name, seen, [name])
                if not part or not known_part(part):
                    bad.append((zone, name, seen, part))
    check(not bad, "every inventory name of every zone comes back as a known part, whatever the side seen", bad)
    check([zone_read.inventory_part("left", "Front Door Handle", s, ["Front Door Handle"]) for s in ("left", "unclear")]
          == ["Left Front Door Handle", "Door Handle (side undetermined)"]
          and zone_read.inventory_part("right", "Fuel Filler Flap", "right", ["Fuel Filler Flap"])
          == "Fuel Filler Flap",
          "a side zone's names take the side seen (or the undetermined name); a part with no side stays as it is")
    ask = Round2Ask(inventory=zone_read.inventory_names("rear"))
    rows = [inv("P05", name, i % 2 == 0, ("High", "Low", None, "Sure")[i % 4]) for i, name in enumerate(
        ["Boot Lid", "Boot Floor Board", "Rear Windscreen", "Boot Lid Lock", "Boot Lid Hinge", "Rear End Panel",
         "Rear Number Plate"])]
    rows += [inv("P03", "Rear Bumper", "true"), inv("P03", "Rear Bumper", True), inv("P03", "Bonnet", True),
             inv("P99", "Boot Lid", True), inv("P04", "Left Tail Lamp", False, box=[5, 5, 6, 6])]
    rows += [inv(f"P{n:02d}", part, True) for n in (6, 7, 8, 9, 10, 11, 12) for part in ("Rear Bumper", "Boot Lid")]
    got = zone_read.parse_answer("rear", json.dumps({"zone_side_seen": "rear", "damage": [], "checked_intact": [],
                                                     "inventory": rows}), LABELS, r2=ask)
    p05 = [r for r in got.inventory if r["photo"] == "P05"]
    check(len(p05) == zone_read.INVENTORY_PER_PHOTO and "Rear Number Plate" not in [r["part"] for r in p05],
          f"at most {zone_read.INVENTORY_PER_PHOTO} a photo", [r["part"] for r in p05])
    check(len(got.inventory) == zone_read.INVENTORY_PER_ZONE, f"at most {zone_read.INVENTORY_PER_ZONE} a zone call",
          len(got.inventory))
    p03 = [r for r in got.inventory if r["photo"] == "P03"]
    want = {"part": "Rear Bumper", "photo": "P03", "damaged": True, "confidence": "Medium", "box": BOXES["P03"]}
    check(p03 == [want],
          "one row a part a photo; a name not offered or a photo not sent left out; damaged 'true' read as true", p03)
    lamp = next(r for r in got.inventory if r["photo"] == "P04")
    check(lamp["box"] is None and lamp["damaged"] is False
          and [r["confidence"] for r in p05][:4] == ["High", "Low", None, None],
          "a bad box is no box (the row kept); a confidence not offered is none", (lamp, p05))
    wrong = zone_read.parse_answer("rear", json.dumps({"zone_side_seen": "front", "damage": [], "checked_intact": [],
                                                       "inventory": rows}), LABELS, r2=ask)
    check(wrong.inventory == [], "an end zone whose photos show the other end: no inventory")
    side = zone_read.parse_answer("left", json.dumps({"zone_side_seen": "right", "damage": [], "checked_intact": [],
                                                      "inventory": [inv("P07", "Rear Door", True)]}), LABELS,
                                  r2=Round2Ask(inventory=zone_read.inventory_names("left")))
    check([r["part"] for r in side.inventory] == ["Right Rear Door"], "a side zone: the side it saw", side.inventory)
    # The suggestions.
    rows = [{"part": part, "photo": f"@p{i}", "damaged": dmg, "confidence": conf, "box": [100, 100, 300, 300],
             "read": "zone:rear"} for i, (part, dmg, conf) in enumerate([
                 ("Rear Bumper", True, "High"),                    # listed
                 ("Tail Lamp (side undetermined)", True, "High"),   # its sided name listed: one part
                 ("Boot Lid Lock", True, "High"),                  # already suggested
                 ("Rear Windscreen", False, "High"),               # not damaged
                 ("Right Tail Lamp", True, None),                  # the other side's twin: kept, Low
                 ("Boot Floor Board", True, "Medium"), ("Boot Floor Board", True, "High"),  # once
                 ("Rear End Panel", True, "Low"), ("Left Boot Side Trim", True, "Medium"),
                 ("Right Boot Side Trim", True, "High"), ("Boot Lid Hinge", True, "Low"),
                 ("Rear Number Plate", True, "Low")])]
    listed = [{"part": "Rear Bumper"}, {"part": "Left Tail Lamp"}]
    suggested = [{"part": "Boot Lid Lock"}]
    got = zones.inventory_suggestions(rows, listed, suggested)
    check([s["part"] for s in got] == ["Boot Floor Board", "Right Boot Side Trim", "Left Boot Side Trim",
                                       "Right Tail Lamp", "Rear End Panel", "Boot Lid Hinge"],
          f"the damaged parts nobody listed or suggested, surest first, once, at most "
          f"{zones.INVENTORY_MAX_SUGGESTIONS}", [s["part"] for s in got])
    first = got[0]
    check(first["source"] == "zone" and first["found_by"] == ["zone:rear"]
          and first["inventory_note"] == zones.INVENTORY_NOTE
          and first["evidence"][0]["box"] == [100, 100, 300, 300] and first["evidence"][0]["by"] == "zone:rear"
          and first["seen_in"] == [first["photo_reference"]] and first["confidence"] == "High",
          "each a zone suggestion with its read, photo, box and note", first)
    lamp = next(s for s in got if s["part"] == "Right Tail Lamp")
    check(lamp["confidence"] == "Low" and zones.other_side_note(lamp, {"Left Tail Lamp"}) == "Left Tail Lamp",
          "the other side's twin of a listed part: a Low suggestion, with the listed one noted beside it", lamp)
    stored = SecondLookItem.model_validate(first).model_dump()
    plain = SecondLookItem.model_validate({k: v for k, v in first.items() if k != "inventory_note"}).model_dump()
    check(stored.get("inventory_note") == zones.INVENTORY_NOTE and "inventory_note" not in plain,
          "SecondLookItem keeps the note, and leaves it out of a dump while none (today's suggestions as before)")
    # Kept apart; relabelled at filing.
    extras = zones.ZoneExtras(inventory=True, inventory_rows=rows[:4])
    draft: dict = {"zone_suggestions": [{"part": "Boot Lid Lock"}], "zone_intact": {"zone:rear": ["Parking Sensor"]}}
    zones.apply_round2([{"part": "Rear Bumper"}, {"part": "Left Tail Lamp"}], draft, extras)
    check(draft["zone_intact"] == {"zone:rear": ["Parking Sensor"]}
          and [r["part"] for r in draft["zone_inventory"]] == ["Rear Bumper", "Tail Lamp (side undetermined)",
                                                               "Boot Lid Lock", "Rear Windscreen"]
          and draft["zone_inventory"][3] == {"read": "zone:rear", "part": "Rear Windscreen", "photo_reference": "@p3",
                                              "damaged": False, "confidence": "High", "box": [100, 100, 300, 300]}
          and [s["part"] for s in draft["zone_suggestions"]] == ["Boot Lid Lock"],
          "the inventory kept as ai_draft['zone_inventory'], never in zone_intact; nothing new to suggest here", draft)
    filed = filing.relabel_ai_draft({"zone_inventory": draft["zone_inventory"]}, {"p0": 2, "p3": 4})
    check([r["photo_reference"] for r in filed["zone_inventory"]] == ["P03", None, None, "P05"],
          "filing relabels its photos (a deleted photo's gone)", filed)


def b1_end_to_end() -> None:
    results = {}
    for name, values in (("off", ZONES), ("on", dict(ZONES, intake_zone_inventory="on")),
                         ("names", dict(ZONES, intake_zone_inventory="on", intake_car_map_names="on"))):
        with r2_case(values, {"zone": r2_zone(REAR_INVENTORY)}) as (client, rid, pids, uid, state):
            session = t.db_row(IntakeSession, rid)
            results[name] = {"state": state["stage"], "data": t.db_row(Report, rid).data, "ai_draft": session.ai_draft,
                             "zone": zone_run(rid), "requests": list(t.REQUESTS), "pids": pids}
    off, on, names = results["off"], results["on"], results["names"]
    check(on["state"] == "ready" and names["state"] == "ready", "the readings end ready")
    items = (on["data"].get("second_look") or {}).get("items") or []
    inventory = [it for it in items if it.get("inventory_note")]
    check([it["part"] for it in inventory] == ["Boot Floor Board", "Right Tail Lamp"],
          "the inventory's damaged parts nobody listed or suggested are suggestions; the listed bumper and boot lid "
          "aren't, nor the intact windscreen, nor a car map part while its names are off",
          [it["part"] for it in inventory])
    p = {f"P{i + 1:02d}": f"@{pid}" for i, pid in enumerate(on["pids"])}
    board = inventory[0]
    check(board["source"] == "zone" and board["found_by"] == ["zone:rear"] and board["photo_reference"] == p["P05"]
          and board["evidence"][0]["box"] == BOXES["P05"] and board["evidence"][0]["img_w"] == 320
          and board.get("name"), "... each with its read, photo, box (its frame set) and the firm's name", board)
    lamp = inventory[1]
    check(lamp.get("other_side_listed") is None and lamp["confidence"] == "Low",
          "the right tail lamp: Low; the left one is only suggested, so no other-side note", lamp)
    others = [it["part"] for it in items if not it.get("inventory_note")]
    check(others == [it["part"] for it in (off["data"].get("second_look") or {}).get("items") or []],
          "the other suggestions as off's", others)
    check(shape(on["data"]["damage_summary"], on["pids"], True)
          == shape(off["data"]["damage_summary"], off["pids"], True),
          "the list as off's, evidence and all")
    check(on["ai_draft"]["zone_intact"] == off["ai_draft"]["zone_intact"],
          "zone_intact as off's: the inventory's intact parts never join it (they suppress nothing)")
    rows = on["ai_draft"].get("zone_inventory") or []
    check([(r["part"], r["damaged"]) for r in rows] == [("Boot Lid", True), ("Boot Floor Board", True),
                                                         ("Rear Windscreen", False), ("Right Tail Lamp", True),
                                                         ("Rear Bumper", True)],
          "ai_draft['zone_inventory']: every row kept, damaged or not", rows)
    check(on["ai_draft"]["box_coverage"] == {"items": 3, "boxed": 0, "ai_boxed": 0, "located": 0},
          "box_coverage kept with B1 alone too (nothing placed: B2 is off)", on["ai_draft"].get("box_coverage"))
    rear = next(r for r in zone_requests(on["requests"]) if "the car's rear" in r["system_instruction"])
    enum = _props(rear)["inventory"]["items"]["properties"]["part"]["enum"]
    check("Rear Number Plate Lamp" not in enum and "'inventory'" in rear["system_instruction"]
          and "locate" not in _props(rear), "the rear request: the inventory by the checklist's names", enum[-3:])
    rear = next(r for r in zone_requests(names["requests"]) if "the car's rear" in r["system_instruction"])
    check("Rear Number Plate Lamp" in _props(rear)["inventory"]["items"]["properties"]["part"]["enum"]
          and "Rear Number Plate Lamp" in [it["part"] for it in look_items(names["data"])],
          "with intake_car_map_names on: the small car map parts offered, and found")
    detail = on["zone"].photos_sent["r2"]
    check(detail["inventory_rows"] == 5 and detail["inventory_damaged"] == 4 and detail["extra_output_tokens_est"] > 0,
          "the run log: the inventory's rows and its extra output", detail)
    # The cap, end to end: nine damaged parts nobody listed.
    many = {"rear": [inv("P05", name, True, "Medium") for name in (
        "Boot Floor Board", "Rear End Panel", "Boot Lid Hinge", "Boot Lid Weatherstrip", "Boot Lid Inner Trim",
        "Rear Windscreen")] + [inv("P04", name, True, "High") for name in (
        "Left Rear Wheel Arch Trim", "Right Boot Side Trim", "Exhaust System")]}
    with r2_case(dict(ZONES, intake_zone_inventory="on"), {"zone": r2_zone(many)}) as (client, rid, pids, uid, state):
        items = (t.db_row(Report, rid).data.get("second_look") or {}).get("items") or []
    got = [it["part"] for it in items if it.get("inventory_note")]
    check(len(got) == zones.INVENTORY_MAX_SUGGESTIONS
          and got[:2] == ["Left Rear Wheel Arch Trim", "Right Boot Side Trim"],
          f"at most {zones.INVENTORY_MAX_SUGGESTIONS} a case, the surest first", got)


# ------------------------------------------------------------- 5. degrading


def degrade_units() -> None:
    ask = Round2Ask(locate=["Rear Bumper"], inventory=zone_read.inventory_names("rear"))
    full = json.dumps({"zone_side_seen": "rear",
                       "damage": [{"photo_reference": "P03", "part": "Rear Bumper", "ai_confidence": "High",
                                   "condition_tokens": ["Cracked"]}],
                       "checked_intact": ["Boot Lid"],
                       "locate": [{"photo_reference": "P03", "part": "Rear Bumper", "box_2d": [1, 1, 500, 500]}],
                       "inventory": [inv("P03", "Rear Bumper", True), inv("P05", "Boot Lid", False)]})
    today = zone_read.parse_answer("rear", json.dumps({k: v for k, v in json.loads(full).items()
                                                       if k not in ("locate", "inventory")}), LABELS)
    for cut_at in ('"inventory"', '"part": "Boot Lid"', '"locate"', '"box_2d": [1, 1'):
        raw = full[:full.index(cut_at) + len(cut_at) - 3]
        got = zone_read.parse_answer("rear", raw, LABELS, r2=ask)
        check(got.items == today.items and got.intact == today.intact and got.r2_salvaged
              and got.located == [] and got.inventory == [] and got.r2_chars == 0,
              f"cut off at {cut_at}: today's answer (salvaged), Round 2's fields dropped", got)
    for cut_at in ('"checked_intact"', '"condition_tokens"'):
        try:
            zone_read.parse_answer("rear", full[:full.index(cut_at) + 5], LABELS, r2=ask)
            failed = False
        except ValueError:
            failed = True
        check(failed, f"cut off before them ({cut_at}): not an answer, as today (the next model is asked)")
    odd = json.dumps({"zone_side_seen": "rear", "locate": [], "damage": [], "checked_intact": []})
    try:
        zone_read.salvage(odd[:-3])
        refused = False
    except ValueError:
        refused = True
    check(refused, "a Round 2 field before today's: refused, never read as an answer without them")
    junk = json.dumps({**json.loads(full), "locate": "oops",
                       "inventory": [1, "x", {"part": 5}, None, {"photo_reference": "P03"}]})
    got = zone_read.parse_answer("rear", junk, LABELS, r2=ask)
    check(got.items == today.items and got.located == [] and got.inventory == [], "fields that aren't what was asked: "
          "left out, the finds stand", got)
    saved = zone_read._round2_answer

    def boom(*a, **k):
        raise RuntimeError("bug")
    zone_read._round2_answer = boom
    try:
        got = zone_read.parse_answer("rear", full, LABELS, r2=ask)
    finally:
        zone_read._round2_answer = saved
    check(got.items == today.items and got.located == [] and got.inventory == [],
          "Round 2's reading crashing: the finds stand, nothing of Round 2", got)
    tries = [{"outcome": "error"}, {"outcome": "error", "busy": True}]
    from app.ai import allowance
    from app.ai.keys import AiUnavailable

    check(zones.round2_fallback_worthy(RuntimeError(), [{"outcome": "schema"}])
          and zones.round2_fallback_worthy(RuntimeError(), [{"outcome": "timeout"}])
          and zones.round2_fallback_worthy(RuntimeError(), tries)
          and not zones.round2_fallback_worthy(RuntimeError(), [{"outcome": "error", "busy": True}, {"outcome": "429"}])
          and not zones.round2_fallback_worthy(RuntimeError(), [{"outcome": "day_spent"}])
          and not zones.round2_fallback_worthy(AiUnavailable("ai_quota_used"), [{"outcome": "schema"}])
          and not zones.round2_fallback_worthy(allowance.DayUsedUp("x"), [{"outcome": "schema"}]),
          "asked again as today after a refused schema, a timeout or a bad answer; never for busy, 429, a spent day "
          "or the person's AI going away")


class Refused(Exception):
    status_code = 400

    def __str__(self):
        return "400 INVALID_ARGUMENT: the response schema has too many states"


class Busy(Exception):
    status_code = 503

    def __str__(self):
        return "503 UNAVAILABLE: the model is overloaded"


def degrade_end_to_end() -> None:
    with r2_case(dict(ZONES), {"zone": r2_zone()}) as (client, rid, pids, uid, state):
        plain = {"data": t.db_row(Report, rid).data, "zones": len(zone_requests())}
        pids = list(pids)
    base_list = shape(plain["data"]["damage_summary"], pids)
    sugg = [it["part"] for it in (plain["data"].get("second_look") or {}).get("items") or []]

    def refusing(kwargs, labels):
        props = _props(kwargs)
        if "locate" in props or "inventory" in props:
            return Refused()
        return r2_zone()(kwargs, labels)
    both = dict(ZONES, intake_box_every="on", intake_zone_inventory="on")
    with r2_case(both, {"zone": refusing}) as (client, rid, pids, uid, state):
        data = t.db_row(Report, rid).data
        detail = zone_run(rid).photos_sent
        asked = zone_requests()
    got = shape(data["damage_summary"], pids)
    entries = {e["zone"]: e for e in detail["zones"]}
    check(state["stage"] == "ready" and got == base_list
          and [it["part"] for it in (data.get("second_look") or {}).get("items") or []] == sugg,
          "a schema the models refuse: the zone reads' finds as off's (asked again as today)", got)
    with_r2 = [r for r in asked if "locate" in _props(r) or "inventory" in _props(r)]
    check(entries["rear"]["status"] == "ok" and entries["rear"]["r2"].get("fallback") == "ai_error"
          and detail["r2"]["fallbacks"] == len(entries) and len(asked) - len(with_r2) == plain["zones"],
          "the run log says so per zone; the cost: one call per zone more, made as today",
          {k: e.get("r2") for k, e in entries.items()})

    def busy(kwargs, labels):
        props = _props(kwargs)
        if "inventory" in props:
            return Busy()
        return r2_zone()(kwargs, labels)
    with r2_case(dict(ZONES, intake_zone_inventory="on"), {"zone": busy}) as (client, rid, pids, uid, state):
        detail = zone_run(rid).photos_sent
        asked = zone_requests()
    check(state["stage"] == "ready" and all("fallback" not in (e.get("r2") or {}) for e in detail["zones"])
          and all("inventory" in _props(r) for r in asked) and {e["status"] for e in detail["zones"]} == {"failed"},
          "a busy chain is not asked again without Round 2 (it would be busy too): the zones fail as today's would, "
          "the reading stands", [e.get("r2") for e in detail["zones"]])

    def cut(kwargs, labels):
        out = r2_zone(REAR_INVENTORY)(kwargs, labels)
        text = json.dumps(out)
        return Raw(text[:text.index('"inventory"') + 40]) if '"inventory"' in text else out
    with r2_case(dict(ZONES, intake_zone_inventory="on"), {"zone": cut}) as (client, rid, pids, uid, state):
        data = t.db_row(Report, rid).data
        detail = zone_run(rid).photos_sent
        session = t.db_row(IntakeSession, rid)
    entries = {e["zone"]: e for e in detail["zones"]}
    got = shape(data["damage_summary"], pids)
    check(state["stage"] == "ready" and got == base_list and entries["rear"]["r2"].get("salvaged") is True
          and entries["rear"]["found"] == 2 and session.ai_draft["zone_inventory"] == []
          and detail["r2"]["salvaged"] >= 1
          and len(zone_requests()) == plain["zones"],
          "an answer cut off inside the inventory: today's answer, no inventory, no extra call; the run log says "
          "salvaged", entries["rear"].get("r2"))


# ------------------------------------------------------------- 6. the benchmark


def bench_section() -> None:
    import benchmark_intake as bench

    def model_settings(*argv):
        return bench.model_settings(bench.parse_args(["--cases", "x", "--key", "y", "--out", "z", *argv]))

    def exits(*argv) -> bool:
        try:
            model_settings(*argv)
        except SystemExit:
            return True
        return False

    got = model_settings("--read-mode", "zones", "--box-every", "--zone-inventory")
    check(got.get("intake_box_every") == "on" and got.get("intake_zone_inventory") == "on",
          "--box-every / --zone-inventory set their settings", got)
    check(exits("--box-every") and exits("--zone-inventory")
          and "intake_box_every" not in model_settings("--read-mode", "zones"),
          "either without zone reads stops the run; neither given, nothing set")
    args = vars(bench.parse_args(["--cases", "x", "--key", "y", "--out", "z"]))
    check(all(args[k] == v for k, v in bench.R2_ARGS.items()), "the flags' defaults are left out of a file's args")
    off = bench.ai_settings(settings)
    with t.Settings(intake_read_mode="zones", intake_zone_inventory="on", intake_car_map_names="on"):
        on = bench.ai_settings(settings)
    with t.Settings(intake_box_every="on"):
        single = bench.ai_settings(settings)
    check(on.get("intake_zone_inventory") == "on" and on.get("intake_box_every") == "off"
          and on.get("intake_car_map_names") == "on"
          and not {"intake_box_every", "intake_zone_inventory"} & (set(off) | set(single)),
          "ai_settings: Round 2's only while it acts (zone mode)", on)
    # A whole run of the benchmark with the fake: what it records.
    folder = Path(tempfile.mkdtemp(prefix="detect-r2-bench-")) / "case_r2"
    folder.mkdir(parents=True)
    for i in range(len(t.CASE)):
        (folder / f"p{i + 1:02d}.jpg").write_bytes(t.jpeg(i))
    case = {"name": "TEST R2", "folder": "case_r2", "vehicle": {
        "make": "Gammamotors", "model": "Vezel", "plate_number": "TEST-R2", "color": "White", "vin": "TESTVIN0000000R2",
        "engine_number": "TESTENGR2", "date_of_registration": "2019-01-01"},
        "parts": [{"part": p} for p in ("Rear Bumper", "Boot Lid", "Boot Floor Board", "Left Rear Door")]}
    runs = {}
    for name, values in (("off", ZONES), ("on", dict(ZONES, intake_box_every="on", intake_zone_inventory="on"))):
        with r2_fake(), t.Settings(**values), w.fakes({"zone": r2_zone(REAR_INVENTORY)}):
            runs[name] = bench.run_case(case, folder.parent)
    on, off = runs["on"], runs["off"]
    check("error" not in on and "error" not in off, "the runs ran", (on.get("error"), off.get("error")))
    check(on.get("box_coverage") == {"items": 3, "boxed": 3, "ai_boxed": 3, "located": 3, "boxed_before_locate": 0}
          and on["zones"]["box_coverage"] == on["box_coverage"] and on["zones"]["r2"]["located"] == 3,
          "the result keeps box_coverage (from the AI draft and the run log)", on.get("box_coverage"))
    inv_rows = on.get("zone_inventory") or {}
    check(inv_rows.get("n") == 5 and inv_rows.get("damaged") == 4 and inv_rows["rows"][1]["photo_reference"] == "P05",
          "... the inventory, photos by case label", inv_rows)
    sugg = [r for r in on.get("suggestions") or [] if r.get("inventory_note")]
    check([r["part"] for r in sugg] == ["Boot Floor Board", "Right Tail Lamp"] and sugg[0]["truth"] == "real",
          "... the inventory suggestions with their note, scored against the key", sugg)
    m, m_off = bench.run_metrics(on), bench.run_metrics(off)
    check(m.get("box_cov_items") == 3 and m.get("box_cov_located") == 3 and m.get("box_cov_boxed_before_locate") == 0
          and m.get("inv_sugg") == 2 and m.get("inv_sugg_real") == 1 and m.get("inv_sugg_box") == 2
          and m.get("r2_extra_tok_est", 0) > 0 and m.get("r2_fallbacks") == 0 and m.get("list_box") == 3,
          "run_metrics: the box coverage, the inventory suggestions (real by the key), the extra tokens; list_box "
          "counts the located boxes",
          {k: v for k, v in m.items() if k.startswith(("box_cov", "inv_", "r2_", "list_", "sug_"))})
    check(m_off.get("list_box") == 0 and not [k for k in m_off if k.startswith(("box_cov", "inv_", "r2_"))]
          and "box_coverage" not in off and "zone_inventory" not in off and "r2" not in off["zones"],
          "off: list_box 0 and nothing of Round 2 in the result", m_off.get("list_box"))


def main() -> int:
    t.init_db()
    for name, fn in (
        ("0. settings: defaults and compose", settings_section),
        (f"1. both off: byte for byte as master {BASE_REF}", base_equal),
        ("1b. r2=None: today's request", requests_unchanged),
        ("2. the schema and prompt sizes", size_section),
        ("3a. B2: what is asked and read", locate_units),
        ("3b. B2: placing the boxes", place_units),
        ("3c. B2: end to end", b2_end_to_end),
        ("4a. B1: the inventory and its suggestions", inventory_units),
        ("4b. B1: end to end", b1_end_to_end),
        ("5a. degrading (units)", degrade_units),
        ("5b. degrading end to end", degrade_end_to_end),
        ("6. the benchmark", bench_section),
    ):
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
