"""Detection Round 2 (settings.intake_box_every "R2-box",
intake_zone_inventory "R2-inv"; ai/zone_read.py and intake/zones.py "Round
2") -- offline: Gemini is the fake of scripts/test_c10_integration.py
(answering what each request's schema asks), no server, made-up cases
only.

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
     combination, with and without the assembly walk; held under the cap
     at run time (fit_round2: the inventory first, then locate);
  3. R2-box: what is asked (neutral words, pairs kept whole, hints, the
     field last), how the answer is read; place_boxes (the review's M1,
     M3, M4, M5, L5, L8: a box there stands, disputed exact only, a listed
     twin's never, the item's own or seen photo, a free slot only, a
     "part" box with no words); end to end (box_coverage, agreement and
     zone finds in the AI draft and the run log, no extra call); with the
     sorter's boxes on (M1); with the parallel reads (M8);
  4. R2-inv: the inventory's names (the car map's small parts only with
     intake_car_map_names on), its caps and sides, its suggestions (found
     by "zone_inventory:<zone>", cap, order after the reads' finds, de-dup
     against the list and the suggestions after the side resolver, the
     other side's twin), the inventory kept apart, relabelled at filing
     (a deleted photo's box gone), counted apart in the AI-accuracy
     record; a linked part keeps source "link" (M7); end to end;
  5. degrading: an answer cut off inside Round 2's fields is today's
     answer; bad fields are left out; a refused schema is asked again as
     today, at most one request per model, and latches Round 2 off for
     the process (M6); a busy chain is not asked again; a schema over the
     cap is trimmed; Round 2's helpers failing never cost a zone read;
  6. the benchmark: --box-every / --zone-inventory (refused without zone
     reads, --box-every with the parallel reads too unless allowed),
     ai_settings while on, the args left out at their defaults, a run's
     box_coverage, inventory and Round 2 metrics, list_low / zone_finds on
     and off.

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


def r2_zone(inventory=None, locate_parts=None, rear_finds=True):
    """The zone reads as the plain fake answers them -- the rear finds the
    bumper (P03) and the left tail lamp (P04) (rear_finds False: the bumper
    only), the left side the rear door (P07) -- and, when the schema asks,
    `locate` (each asked part on its photo: Rear Bumper P03, Boot Lid P05,
    Rear Door P07; or locate_parts' {name: label}) and `inventory`
    (inventory: {zone: [rows]})."""
    where = {"Rear Bumper": "P03", "Boot Lid": "P05", "Rear Door": "P07", **(locate_parts or {})}

    def answer(kwargs, labels):
        props = _props(kwargs)
        system = kwargs["system_instruction"]
        zone = "rear" if "the car's rear" in system else "left" if "one side of the car" in system else "other"
        if zone == "rear":
            out = t._zone_answer(kwargs, labels)
            if not rear_finds:
                out["damage"] = [it for it in out["damage"] if it["part"] == "Rear Bumper"]
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
                  "R2-box on: ZoneExtras with box_every, Wave 2's options as off", got)
        with t.Settings(intake_zone_inventory="on", intake_car_map_names="on"):
            got = zones.extras_for([])
            check(got is not None and got.inventory and got.car_names and not got.box_every,
                  "R2-inv on with the car map's names: ZoneExtras with inventory and car_names", got)


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
ALL_LIGHT = {"evidence_mode": "reason_box", "grid": True, "photo_half": True,
             "packs": ["intact", "orient", "cues", "small"]}


def enum_values(node) -> int:
    """How many enum values a schema holds (what makes a schema "too
    complex" for a light model is its states, not its characters)."""
    if isinstance(node, dict):
        return len(node.get("enum") or []) + sum(enum_values(v) for k, v in node.items() if k != "enum")
    if isinstance(node, list):
        return sum(enum_values(v) for v in node)
    return 0


def _ask(zone: str, box_every: bool, inventory: bool, names: bool) -> Round2Ask:
    locate = zone_read.locate_names(zone, LISTED) if box_every else []
    return Round2Ask(locate=locate, hints={n: ["P03", "P04"] for n in locate},
                     inventory=zone_read.inventory_names(zone, names) if inventory else [])


def size_section() -> None:
    from app.intake import assembly_walk, car_map

    walk = assembly_walk.index_for(car_map.MAP)
    main = extraction._response_schema()
    cap = zone_read.R2_SCHEMA_CAP
    print(f"         (the main read's schema, which the same light models answer: {len(json.dumps(main))} characters, "
          f"{enum_values(main)} enum values; Round 2's cap {cap})")
    print("         zone   switches           options    asked  (+ vs today)  sent  trimmed      prompt +  enums")
    problems = []
    worst = {}
    for zone in zone_read.ZONE_NAMES:
        for kw_name, kw in (("reason_box", {"evidence_mode": "reason_box"}), ("all light", ALL_LIGHT),
                            ("all + walk", {**ALL_LIGHT, "walk": walk})):
            today = len(json.dumps(zone_read.response_schema(zone, LABELS, **kw)))
            prompt = len(zone_read.system_prompt(zone, **kw))
            for box_every, inventory, names in ((False, False, False), (True, False, False), (False, True, False),
                                                (False, True, True), (True, True, False), (True, True, True)):
                ask = _ask(zone, box_every, inventory, names)
                size = len(json.dumps(zone_read.response_schema(zone, LABELS, r2=ask, **kw)))
                fit, dropped = zone_read.fit_round2(zone, LABELS, ask, **kw)
                schema = zone_read.response_schema(zone, LABELS, r2=fit, **kw)
                sent = len(json.dumps(schema))
                more = len(zone_read.system_prompt(zone, r2=fit, **kw)) - prompt
                tag = (f"R2-box={'on' if box_every else 'off'} inv={'on' if inventory else 'off'}"
                       f"{'+names' if names else ''}")
                if kw_name == "reason_box" or (box_every and inventory and names):
                    print(f"         {zone:<6} {tag:<18} {kw_name:<10} {size:>5}  (+{size - today:>4})  {sent:>5}  "
                          f"{'+'.join(dropped) or '-':<12} {more:>8}  {enum_values(schema):>5}")
                worst[zone] = max(worst.get(zone, 0), sent)
                if sent > max(cap, today) or more > PROMPT_CAP:
                    problems.append((zone, kw_name, tag, "over", sent, more))
                if size <= cap and dropped:
                    problems.append((zone, kw_name, tag, "trimmed under the cap", size, dropped))
                if dropped and inventory and dropped[0] != "inventory":
                    problems.append((zone, kw_name, tag, "locate dropped before the inventory", dropped))
                if not (box_every or inventory) and sent != today:
                    problems.append((zone, kw_name, "off differs", sent, today))
    check(not problems, f"every zone, option and switch combination (the assembly walk too): what is sent is at most "
                        f"{cap} characters -- the inventory left out first, then locate, only when over -- and "
                        f"Round 2's sentences at most {PROMPT_CAP} (largest sent: {worst})", problems)
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
    check(len(zone_read.locate_names("close", more)) <= zone_read.MAX_LOCATE,
          f"at most {zone_read.MAX_LOCATE} listed parts asked a call")


# ------------------------------------------------------------- 3. R2-box


def locate_units() -> None:
    names = zone_read.locate_names("rear", ["Rear Bumper", "Tail Lamp (side undetermined)", "Other / Not Listed",
                                            "Left Front Door", "Boot Lid"])
    check(names == ["Rear Bumper", "Boot Lid", "Left Tail Lamp", "Right Tail Lamp"],
          "rear: the listed parts its checklist names, in its order; an undetermined lamp asks both sides; "
          "'Other' and a door never", names)
    many = ["Rear Bumper", "Rear Bumper Beam", "Boot Lid", "Boot Lid Lock", "Boot Lid Hinge", "Boot Lid Weatherstrip",
            "Boot Lid Inner Trim", "Tail Lamp (side undetermined)", "Rear End Panel"]
    names = zone_read.locate_names("rear", many)
    check(len(names) == zone_read.MAX_LOCATE and "Left Tail Lamp" not in names and "Right Tail Lamp" not in names
          and names[-1] == "Rear End Panel",
          "the cap never cuts a Left / Right pair in two (the review's L5): both or neither", names)
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
    sentence = text[text.index("Also locate"):text.index("Only the insured car")]
    check("Also locate these parts: Rear Bumper (P03), Boot Lid, Left Tail Lamp (P05), Right Tail Lamp (P05)."
          in sentence and "This is not a damage judgement: decide damage from the photos alone" in sentence
          and "damage list" not in text and "already" not in sentence,
          "the prompt names the parts to locate with their photos, never as the damage list, and says it is no "
          "damage judgement (the review's M2) -- before the closing line", sentence)
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
    for pid in ("a1", "a2", "a3", "a4", "a5", "a7", "a9"):
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


def _located(item: dict) -> list[tuple]:
    return [(e["photo"], e["by"], e["box_kind"]) for e in item["evidence"] if evidence.located(e)]


BOTH_LAMPS = {"zone:rear": ["Left Tail Lamp", "Right Tail Lamp", "Left Rear Bumper Reflector",
                            "Right Rear Bumper Reflector"]}


def place_units() -> None:
    folder = Path(tempfile.mkdtemp(prefix="detect-r2-frames-"))
    facts = _facts(folder, raw={"a9"})
    boxed = _item("Boot Lid", "@a5", evidence=[evidence.entry("@a5", "zone:rear", box=[50, 50, 300, 300],
                                                              confidence="Medium")])
    listed = [
        _item("Rear Bumper", "@a3"),
        boxed,
        _item("Left Rear Door", "@a7"),
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
    placed = zones.place_boxes(listed, sources, facts, asked=BOTH_LAMPS)
    by = {it["part"]: it for it in listed}
    bumper = by["Rear Bumper"]["evidence"]
    new = bumper[-1]
    check(placed == 4 and [(e["photo"], e["by"]) for e in bumper] == [("@a3", "overall"), ("@a3", "locate:zone:rear")]
          and new["box"] == [300, 200, 700, 800] and new["box_kind"] == "part" and new["reason"] is None
          and new["confidence"] is None and (new["img_w"], new["img_h"]) == (320, 240),
          "a listed part with no box: a 'part' box on its own photo by locate:zone:rear, beside the read's entry "
          "there (never in its place), with no words and no confidence, its frame set (the review's M5, L8)", bumper)
    check(by["Rear Bumper"]["bbox_2d"] is None, "a part box is never the item's bbox_2d (that is the AI's damage box)")
    check(by["Boot Lid"] == before[1], "a listed part already boxed keeps its box (nothing added)")
    door = by["Left Rear Door"]["evidence"]
    check(door[0]["reason"] == "Pushed in." and door[0]["by"] == "overall" and door[1]["by"] == "locate:zone:left"
          and door[1]["reason"] is None, "the read's entry and its words stay as they were", door)
    check(_located(by["Tail Lamp (side undetermined)"]) == [("@a4", "locate:zone:rear", "part")],
          "an undetermined part placed on one side only, both sides asked: that box")
    check(by["Rear Bumper Reflector (side undetermined)"] == before[4],
          "an undetermined part with both sides placed: none -- which one isn't known")
    check(by["Other / Not Listed"] == before[5], "'Other / Not Listed': never")
    check(_located(by["Rear Windscreen"]) == [("@a5", "locate:zone:rear", "part")],
          "a box on a photo the AI got raw doesn't stand: the item gets one on another photo; none is placed on a "
          "raw photo", by["Rear Windscreen"]["evidence"])
    check(all((a["found_by"], a["seen_in"], a["ai_confidence"], a["part"], a["bbox_2d"])
              == (b["found_by"], b["seen_in"], b["ai_confidence"], b["part"], b["bbox_2d"])
              for a, b in zip(listed, before)), "found_by, seen_in, the confidence, the part and bbox_2d never change")
    check(all([e for e in a["evidence"] if not evidence.located(e)] == b["evidence"] for a, b in zip(listed, before)),
          "every read's own entry kept as it was")
    # L5: the other side wasn't asked in that call (the cap cut it).
    lamp = [_item("Tail Lamp (side undetermined)", "@a4")]
    check(zones.place_boxes(lamp, [_src("Left Tail Lamp", "@a4")], facts, asked={"zone:rear": ["Left Tail Lamp"]}) == 0
          and zones.place_boxes(lamp, [{**_src("Left Tail Lamp", "@a4"), "via": "inventory"}], facts,
                                asked={"zone:rear": ["Left Tail Lamp"]}) == 1,
          "one side placed when only that side was asked is no evidence (the review's L5); the inventory, which "
          "offered both, is")
    # M3: a side the reads disputed takes only an exact box.
    disputed = [_item("Tail Lamp (side undetermined)", "@a4")]
    check(zones.place_boxes(disputed, [_src("Left Tail Lamp", "@a4")], facts, {"Tail Lamp (side undetermined)"},
                            BOTH_LAMPS) == 0
          and zones.place_boxes(disputed, [_src("Tail Lamp (side undetermined)", "@a4", read="zone:close")], facts,
                                {"Tail Lamp (side undetermined)"}, BOTH_LAMPS) == 1,
          "a part whose side the reads disputed: never one side's box (the review's M3), its own name's only")
    # M4: the sided twin is listed itself.
    twins = [_item("Left Tail Lamp", "@a4"), _item("Tail Lamp (side undetermined)", "@a5")]
    zones.place_boxes(twins, [_src("Left Tail Lamp", "@a4")], facts, asked=BOTH_LAMPS)
    check(_located(twins[0]) == [("@a4", "locate:zone:rear", "part")] and _located(twins[1]) == [],
          "the listed left lamp takes its box; the undetermined lamp never takes it too (the review's M4)")
    # M5: the photo, and the free slot.
    seen = [_item("Boot Lid", "@a5", seen_in=["@a5", "@a4"])]
    zones.place_boxes(seen, [_src("Boot Lid", "@a3"), _src("Boot Lid", "@a4")], facts)
    full = _item("Boot Lid", "@a1", evidence=[evidence.entry(f"@a{n}", "zone:rear", reason=f"Mark {n}.")
                                               for n in (1, 2, 3, 4)])
    kept = copy.deepcopy(full)
    check(_located(seen[0]) == [("@a4", "locate:zone:rear", "part")]
          and zones.place_boxes([full], [_src("Boot Lid", "@a9"), _src("Boot Lid", "@a5")], facts) == 0
          and full == kept,
          "not its own photo: a photo it was seen in before any other; an item with 4 entries gets none -- a "
          "located box never pushes a read's entry out (the review's M5)", (seen[0]["evidence"], full["evidence"]))
    ranked = _item("Boot Lid", "@a3", evidence=[evidence.entry("@a3", "overall", confidence="High"),
                                                 evidence.entry("@a4", "zone:rear", reason="Dent.", confidence="Low")])
    zones.place_boxes([ranked], [_src("Boot Lid", "@a3")], facts)
    check([(e["photo"], e["by"]) for e in ranked["evidence"]]
          == [("@a3", "overall"), ("@a4", "zone:rear"), ("@a3", "locate:zone:rear")],
          "a located box after every read's entry, even on the item's own photo", ranked["evidence"])
    # The side resolver puts the undetermined lamp on a side afterwards.
    lamp = by["Tail Lamp (side undetermined)"]
    right = {**copy.deepcopy(lamp), "part": "Right Tail Lamp"}
    left = {**copy.deepcopy(lamp), "part": "Left Tail Lamp"}
    bumper_copy = copy.deepcopy(by["Rear Bumper"])
    dropped = zones.drop_crossed_boxes([right, left, bumper_copy], zones.ZoneExtras(box_every=True, located=sources))
    check(dropped == 1 and _located(right) == [] and right["evidence"] == lamp["evidence"][:-1]
          and left["evidence"] == lamp["evidence"] and bumper_copy == by["Rear Bumper"],
          "placed from 'Left Tail Lamp', then put on the right by the side resolver: the box goes (on the left: it "
          "stays; an exact one is never touched)", right)
    cov = zones.box_coverage(listed, 1, 9)
    check(cov == {"items": 7, "boxed": 5, "ai_boxed": 2, "located": 4, "list_agreed": 0, "list_low": 0,
                  "boxed_before_locate": 1, "zone_finds": 9},
          "box_coverage: items, boxed, the AI's damage boxes, located, agreement, Low, before, the zone finds", cov)
    # A close-up's unsided name: one listed twin only.
    two = [_item("Left Tail Lamp", "@a4"), _item("Right Tail Lamp", "@a5")]
    one = [_item("Left Tail Lamp", "@a4")]
    src = [_src("Tail Lamp (side undetermined)", "@a4", read="zone:close")]
    check(zones.place_boxes(two, src, facts) == 0 and zones.place_boxes(one, src, facts) == 1,
          "a close-up's unsided find places a sided listed part only when no other listed part is its twin")
    check(zones.place_boxes([_item("Left Tail Lamp", "@a4")], [_src("Right Tail Lamp", "@a4")], facts) == 0,
          "never the other side's twin")
    # The review's L9: a linked part's "listed part's photo" is never a located one.
    from app.intake import links

    parent = {"photo_reference": "@a1", "seen_in": [], "evidence": [
        {"photo": "@a9", "by": "locate:zone:rear", "box": [1, 1, 500, 500], "box_kind": "part"},
        {"photo": "@a1", "by": "overall", "box": None}]}
    check(links._parent_photo(parent, SimpleNamespace(photos=[])) == "@a1",
          "the linked parts take the listed part's own photo, never its located one (the review's L9)")


def _part_boxes(item: dict) -> list[tuple]:
    return [(e["photo"], e["box"], e["by"], e["box_kind"], e["reason"], e["confidence"], e["img_w"])
            for e in item["evidence"] if evidence.located(e)]


def _reads_entries(items: list[dict], pids: list[str]) -> list:
    return shape([{**it, "evidence": [e for e in it.get("evidence") or [] if not evidence.located(e)]}
                  for it in items], pids, True)


def b2_end_to_end() -> None:
    runs_: dict = {}
    for name, values in (("off", ZONES), ("on", dict(ZONES, intake_box_every="on")),
                         ("box_on", dict(BOX, intake_box_every="on"))):
        with r2_case(values, {"zone": r2_zone()}) as (client, rid, pids, uid, state):
            session = t.db_row(IntakeSession, rid)
            runs_[name] = {"state": state["stage"], "data": t.db_row(Report, rid).data, "ai_draft": session.ai_draft,
                           "zone": zone_run(rid), "requests": list(t.REQUESTS), "pids": pids}
    off, on, box_on = runs_["off"], runs_["on"], runs_["box_on"]
    check(on["state"] == "ready" and box_on["state"] == "ready", "the readings end ready")
    listed = on["data"]["damage_summary"]
    off_list = off["data"]["damage_summary"]
    check([it["part"] for it in listed] == [it["part"] for it in off_list]
          == ["Rear Bumper", "Boot Lid", "Left Rear Door"], "the list itself as off's", [it["part"] for it in listed])
    check(_reads_entries(listed, on["pids"]) == _reads_entries(off_list, off["pids"]),
          "every item as off's but for its located part box: found_by, seen_in, confidence, bbox_2d and every read's "
          "own entry (with this fake, which answers the same either way; the live A/B compares the zone reads' "
          "own finds and the agreement -- box_coverage below)")
    p = {f"P{i + 1:02d}": f"@{pid}" for i, pid in enumerate(on["pids"])}
    located = {it["part"]: _part_boxes(it) for it in listed}
    check(located == {"Rear Bumper": [(p["P03"], BOXES["P03"], "locate:zone:rear", "part", None, None, 320)],
                      "Boot Lid": [(p["P05"], BOXES["P05"], "locate:zone:rear", "part", None, None, 320)],
                      "Left Rear Door": [(p["P07"], BOXES["P07"], "locate:zone:left", "part", None, None, 320)]},
          "every list item has a part box on its own photo, from the zone read that saw it, with its frame and no "
          "words (reasons only, no zone box before)", located)
    check(not any(e.get("box") for it in off_list for e in it["evidence"]), "off: none of them had a box")
    agreed = sum(1 for it in off_list if len(set(it.get("found_by") or [])) >= 2)
    low = sum(1 for it in off_list if it.get("ai_confidence") == "Low")
    finds = sum(int(z.get("found") or 0) for z in off["zone"].photos_sent["zones"])
    cov = on["ai_draft"].get("box_coverage")
    check(cov == {"items": 3, "boxed": 3, "ai_boxed": 0, "located": 3, "list_agreed": agreed, "list_low": low,
                  "boxed_before_locate": 0, "zone_finds": finds} and finds == 3 and agreed == 2,
          "ai_draft['box_coverage']: 3 of 3 boxed, 0 before; with the agreement, the Low rows and the zone reads' own "
          "finds, to set against off (the review's M2)", cov)
    detail = on["zone"].photos_sent
    check(detail.get("box_coverage") == cov and detail["r2"]["located"] == 3 and detail["r2"]["list_known"] is True
          and detail["r2"]["extra_output_tokens_est"] > 0 and detail["r2"]["fallbacks"] == 0
          and detail["r2"]["zone_finds"] == finds and detail["r2"]["latched"] is False,
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
          == ["Rear Bumper", "Boot Lid"] and "Also locate these parts: Rear Bumper (P03), Boot Lid (P05)."
          in rear["system_instruction"], "the rear call asked to locate the listed rear parts, with their photos",
          _props(rear).get("locate"))
    tags = {a.get("prompt", "") for a in on["zone"].attempts}
    check(any("+locate-r2@" in tag for tag in tags), "the run log's tries are tagged +locate-r2", tags)
    items = {it["part"]: it for it in box_on["data"]["damage_summary"]}
    bumper = [(e["by"], e["box"], e["box_kind"]) for e in items["Rear Bumper"]["evidence"] if e.get("box")]
    cov = box_on["ai_draft"]["box_coverage"]
    check(bumper == [("zone:rear", [120, 140, 520, 600], "ai")] and cov["boxed_before_locate"] == 2
          and cov["located"] == 1 and cov["ai_boxed"] == 2,
          "with the zone reads' boxes: the bumper and the door keep theirs, only the boot lid is located", bumper)


def boxed_sort(kwargs, labels):
    """The sort, with the sorter's damage box on P09 when asked for one."""
    out = t._sort_answer(labels)
    props = kwargs["response_format"]["schema"]["properties"]["photos"]["items"]["properties"]
    if "damage_box" in props:
        for photo in out["photos"]:
            photo["damage_box"] = [400, 300, 600, 500] if photo["label"] == "P09" else None
    return out


def bumper_on_p09(kwargs, labels):
    """The main read, with the bumper on P09 (no part sorted there)."""
    out = t._draft_answer(labels)
    if "P09" in labels:
        out["damage_summary"][0]["photo_reference"] = "P09"
    return out


def sorter_boxes() -> None:
    """The review's M1: with the sorter's boxes on, a box the reading shows
    stands -- also one only the 'only finding on that photo' rule gives --
    and boxed_before_locate counts it."""
    answers = {"sort": boxed_sort, "draft": bumper_on_p09, "zone": r2_zone(locate_parts={"Boot Lid": "P09"})}
    got = {}
    for name, values in (("off", dict(ZONES, intake_sort_damage_box=True)),
                         ("on", dict(ZONES, intake_sort_damage_box=True, intake_box_every="on"))):
        with r2_case(values, answers) as (client, rid, pids, uid, state):
            got[name] = {"state": state["stage"], "data": t.db_row(Report, rid).data, "pids": pids,
                         "ai_draft": t.db_row(IntakeSession, rid).ai_draft}
    off, on = got["off"]["data"]["damage_summary"], got["on"]["data"]["damage_summary"]
    p_off = {f"P{i + 1:02d}": f"@{pid}" for i, pid in enumerate(got["off"]["pids"])}
    bumper_off = next(it for it in off if it["part"] == "Rear Bumper")
    check(got["on"]["state"] == "ready" and any(e.get("box_kind") == "sort" and e["photo"] == p_off["P09"]
                                                 for e in bumper_off["evidence"]),
          "off: the bumper, the only finding on P09, has the sorter's box there")
    check(_reads_entries(on, got["on"]["pids"]) == _reads_entries(off, got["off"]["pids"]),
          "on: every item's own evidence as off's -- the bumper keeps the sorter's box, though the boot lid is now "
          "located on P09 too (the only-finding rule ran first)")
    lid = next(it for it in on if it["part"] == "Boot Lid")
    bumper = next(it for it in on if it["part"] == "Rear Bumper")
    p_on = {f"P{i + 1:02d}": f"@{pid}" for i, pid in enumerate(got["on"]["pids"])}
    check([x[:4] for x in _part_boxes(lid)] == [(p_on["P09"], BOXES["P09"], "locate:zone:rear", "part")]
          and _part_boxes(bumper) == [],
          "the boot lid's part box on P09; the bumper, boxed by the sorter, gets none")
    cov = got["on"]["ai_draft"]["box_coverage"]
    off_boxed = zones.box_coverage(off)["boxed"]
    check(cov["boxed_before_locate"] == off_boxed == 1 and cov["located"] == 2 and cov["boxed"] == 3,
          "boxed_before_locate counts the sorter's box: what off shows", cov)


@contextlib.contextmanager
def warnings_of(name: str):
    import logging

    seen: list[str] = []

    class Keep(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())
    handler = Keep(level=logging.WARNING)
    logger = logging.getLogger(name)
    logger.addHandler(handler)
    try:
        yield seen
    finally:
        logger.removeHandler(handler)


def parallel_reads() -> None:
    """The review's M8: with the zone reads beside the main read there is
    no list to place -- said in the log, and nothing asked."""
    values = dict(ZONES, intake_box_every="on", intake_main_read_fallback="zones", intake_parallel_reads="on")
    with warnings_of("app.intake.zones") as seen, r2_case(values, {"zone": r2_zone()}) as (
            client, rid, pids, uid, state):
        draft = t.db_row(IntakeSession, rid).ai_draft
        detail = zone_run(rid).photos_sent
        asked = zone_requests()
    check(state["stage"] == "ready" and not any("locate" in _props(r) for r in asked) and asked
          and draft["box_coverage"]["located"] == 0 and detail["r2"]["list_known"] is False,
          "the parallel reads: nothing asked or located", detail.get("r2"))
    check(any("box-every is on, but the zone reads ran before the main read's list" in m for m in seen),
          "... and a warning says why", seen)


# ------------------------------------------------------------- 4. R2-inv


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
    check(first["source"] == "zone" and first["found_by"] == ["zone_inventory:rear"]
          and first["inventory_note"] == zones.INVENTORY_NOTE
          and first["evidence"][0]["box"] == [100, 100, 300, 300]
          and first["evidence"][0]["by"] == "zone_inventory:rear"
          and first["seen_in"] == [first["photo_reference"]] and first["confidence"] == "High",
          "each a zone suggestion found by 'zone_inventory:rear' -- never the read's own name (the review's M7) -- "
          "with its photo, box and note", first)
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
    zones.add_inventory([{"part": "Rear Bumper"}, {"part": "Left Tail Lamp"}], draft, extras)
    check(draft["zone_intact"] == {"zone:rear": ["Parking Sensor"]}
          and [r["part"] for r in draft["zone_inventory"]] == ["Rear Bumper", "Tail Lamp (side undetermined)",
                                                               "Boot Lid Lock", "Rear Windscreen"]
          and draft["zone_inventory"][3] == {"read": "zone:rear", "part": "Rear Windscreen", "photo_reference": "@p3",
                                              "damaged": False, "confidence": "High", "box": [100, 100, 300, 300]}
          and [s["part"] for s in draft["zone_suggestions"]] == ["Boot Lid Lock"],
          "the inventory kept as ai_draft['zone_inventory'], never in zone_intact; nothing new to suggest here", draft)
    filed = filing.relabel_ai_draft({"zone_inventory": draft["zone_inventory"]}, {"p0": 2, "p3": 4})
    check([(r["photo_reference"], r["box"]) for r in filed["zone_inventory"]]
          == [("P03", [100, 100, 300, 300]), (None, None), (None, None), ("P05", [100, 100, 300, 300])],
          "filing relabels its photos; a deleted photo's row keeps no box (the review's L4)", filed)
    # Counted apart in the AI-accuracy record.
    from app.reports import accuracy

    final = {"damage_summary": [], "second_look": {"dismissed": [], "decisions": [], "items": [
        {"key": "Boot Floor Board", "part": "Boot Floor Board", "source": "zone", "found_by": ["zone_inventory:rear"],
         "inventory_note": zones.INVENTORY_NOTE},
        {"key": "Left Tail Lamp", "part": "Left Tail Lamp", "source": "zone", "found_by": ["zone:rear"]}]}}
    counted = accuracy._suggestions({}, final)
    check(set(counted["by_read"]) == {"zone:rear", "zone_inventory:rear"}
          and counted["by_read"]["zone_inventory:rear"]["shown"] == 1 and counted["by_read"]["zone:rear"]["shown"] == 1,
          "the AI-accuracy record counts the inventory's suggestions apart from the zone read's finds",
          counted["by_read"])


REAR_INVENTORY = {"rear": [
    inv("P05", "Boot Lid", True, "High"),            # listed: no suggestion
    inv("P05", "Boot Floor Board", True, "Medium"),  # a suggestion
    inv("P05", "Rear Windscreen", False, "High"),    # intact: kept apart
    inv("P04", "Right Tail Lamp", True, "Low"),      # a suggestion (the left one is only suggested)
    inv("P03", "Rear Bumper", True, "High"),         # listed
    inv("P03", "Rear Number Plate Lamp", True, "High"),  # only with the car map's names
    inv("P05", "Rear End Panel", True, "High"),      # a suggestion, still after the reads' own finds
]}


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
    items = look_items(on["data"])
    inventory = [it for it in items if it.get("inventory_note")]
    check([it["part"] for it in inventory] == ["Rear End Panel", "Boot Floor Board", "Right Tail Lamp"]
          and [it["part"] for it in items][0] == "Left Tail Lamp" and items[0].get("confidence") == "Medium",
          "the inventory's damaged parts nobody listed or suggested are suggestions -- after the zone read's own find, "
          "the High one too (the review's L7); not the listed bumper or boot lid, the intact windscreen, nor a car "
          "map part while its names are off", [(it["part"], it.get("confidence")) for it in items])
    p = {f"P{i + 1:02d}": f"@{pid}" for i, pid in enumerate(on["pids"])}
    board = next(it for it in inventory if it["part"] == "Boot Floor Board")
    check(board["source"] == "zone" and board["found_by"] == ["zone_inventory:rear"]
          and board["photo_reference"] == p["P05"] and board["evidence"][0]["box"] == BOXES["P05"]
          and board["evidence"][0]["img_w"] == 320 and board["evidence"][0]["by"] == "zone_inventory:rear"
          and board.get("name"), "... each found by 'zone_inventory:rear', with its photo, box (its frame set) and the "
                                 "firm's name", board)
    lamp = next(it for it in inventory if it["part"] == "Right Tail Lamp")
    check(lamp.get("other_side_listed") is None and lamp["confidence"] == "Low",
          "the right tail lamp: Low; the left one is only suggested, so no other-side note", lamp)
    others = [it["part"] for it in items if not it.get("inventory_note")]
    check(others == [it["part"] for it in look_items(off["data"])], "the other suggestions as off's", others)
    check(shape(on["data"]["damage_summary"], on["pids"], True)
          == shape(off["data"]["damage_summary"], off["pids"], True),
          "the list as off's, evidence and all")
    check(on["ai_draft"]["zone_intact"] == off["ai_draft"]["zone_intact"],
          "zone_intact as off's: the inventory's intact parts never join it (they suppress nothing)")
    rows = on["ai_draft"].get("zone_inventory") or []
    check([(r["part"], r["damaged"]) for r in rows] == [("Boot Lid", True), ("Boot Floor Board", True),
                                                         ("Rear Windscreen", False), ("Right Tail Lamp", True),
                                                         ("Rear Bumper", True), ("Rear End Panel", True)],
          "ai_draft['zone_inventory']: every row kept, damaged or not", rows)
    cov = on["ai_draft"]["box_coverage"]
    check(cov["located"] == 0 and cov["boxed"] == 0 and "boxed_before_locate" not in cov and cov["items"] == 3,
          "box_coverage kept with R2-inv alone too (nothing placed: R2-box is off)", cov)
    rear = next(r for r in zone_requests(on["requests"]) if "the car's rear" in r["system_instruction"])
    enum = _props(rear)["inventory"]["items"]["properties"]["part"]["enum"]
    check("Rear Number Plate Lamp" not in enum and "'inventory'" in rear["system_instruction"]
          and "locate" not in _props(rear), "the rear request: the inventory by the checklist's names", enum[-3:])
    rear = next(r for r in zone_requests(names["requests"]) if "the car's rear" in r["system_instruction"])
    check("Rear Number Plate Lamp" in _props(rear)["inventory"]["items"]["properties"]["part"]["enum"]
          and "Rear Number Plate Lamp" in [it["part"] for it in look_items(names["data"])],
          "with intake_car_map_names on: the small car map parts offered, and found")
    detail = on["zone"].photos_sent["r2"]
    check(detail["inventory_rows"] == 6 and detail["inventory_damaged"] == 5 and detail["extra_output_tokens_est"] > 0,
          "the run log: the inventory's rows and its extra output", detail)
    # The cap, end to end: nine damaged parts nobody listed.
    many = {"rear": [inv("P05", name, True, "Medium") for name in (
        "Boot Floor Board", "Rear End Panel", "Boot Lid Hinge", "Boot Lid Weatherstrip", "Boot Lid Inner Trim",
        "Rear Windscreen")] + [inv("P04", name, True, "High") for name in (
        "Left Rear Wheel Arch Trim", "Right Boot Side Trim", "Exhaust System")]}
    with r2_case(dict(ZONES, intake_zone_inventory="on"), {"zone": r2_zone(many)}) as (client, rid, pids, uid, state):
        items = look_items(t.db_row(Report, rid).data)
    got = [it["part"] for it in items if it.get("inventory_note")]
    check(len(got) == zones.INVENTORY_MAX_SUGGESTIONS
          and got[:2] == ["Left Rear Wheel Arch Trim", "Right Boot Side Trim"],
          f"at most {zones.INVENTORY_MAX_SUGGESTIONS} a case, the surest first", got)
    # The review's L6: against the list's sides as the side resolver left them.
    from app.intake import pipeline

    original = pipeline.resolve_sides

    def resolver(items, *a, **k):
        out = original(items, *a, **k)
        return [{**it, "part": "Left Tail Lamp"} if it.get("part") == "Tail Lamp (side undetermined)" else it
                for it in out]

    def lamp_draft(kwargs, labels):
        out = t._draft_answer(labels)
        out["damage_summary"][1] = {"part": "Tail Lamp (side undetermined)", "photo_reference": "P04",
                                    "ai_confidence": "High", "condition_tokens": ["Cracked"]}
        return out
    pipeline.resolve_sides = resolver
    try:
        rows = {"rear": [inv("P04", "Right Tail Lamp", True, "High")]}
        with r2_case(dict(ZONES, intake_zone_inventory="on"),
                     {"zone": r2_zone(rows, rear_finds=False), "draft": lamp_draft}) as (client, rid, pids, uid, state):
            data = t.db_row(Report, rid).data
    finally:
        pipeline.resolve_sides = original
    lamps = [(it["part"], it.get("other_side_listed")) for it in look_items(data) if it.get("inventory_note")]
    check("Left Tail Lamp" in [it["part"] for it in data["damage_summary"]]
          and lamps == [("Right Tail Lamp", "Left Tail Lamp")],
          "the listed undetermined lamp put on the left by the side resolver: the inventory's damaged right lamp is "
          "still suggested, with the other-side note (the review's L6)", lamps)


def links_join() -> None:
    """The review's M7: a part the inventory suggests that a linked part is
    too stays one card, source "link" -- the linked check still asks about
    it -- with the inventory's photo, box and note on it."""
    links = dict(ZONES, intake_links="on", intake_link_check="lite")
    beam = {"rear": [inv("P03", "Rear Bumper Beam", True, "High")]}
    got = {}
    for name, values in (("off", links), ("on", dict(links, intake_zone_inventory="on"))):
        with r2_case(values, {"zone": r2_zone(beam)}) as (client, rid, pids, uid, state):
            got[name] = (state["stage"], [it for it in look_items(t.db_row(Report, rid).data)
                                          if it["part"] == "Rear Bumper Beam"])
    off, on = got["off"][1], got["on"][1]
    check(got["on"][0] == "ready" and len(off) == 1 and off[0]["source"] == "link"
          and (off[0].get("link_check") or {}).get("verdict") == "strip_down",
          "off: the beam is a linked part the linked check answered", off)
    check(len(on) == 1 and on[0]["source"] == "link" and on[0].get("inventory_note") == zones.INVENTORY_NOTE
          and (on[0].get("link_check") or {}).get("verdict") == "strip_down"
          and "zone_inventory:rear" in on[0].get("found_by", [])
          and any(e.get("by") == "zone_inventory:rear" and e.get("box") for e in on[0].get("evidence") or []),
          "on: still one card, source 'link' and checked, the inventory's read, box and note joined to it", on)


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
    from app.ai import allowance
    from app.ai.keys import AiUnavailable

    tries = [{"outcome": "error"}, {"outcome": "error", "busy": True}]
    check(zones.round2_fallback_worthy(RuntimeError(), [{"outcome": "schema"}])
          and zones.round2_fallback_worthy(RuntimeError(), [{"outcome": "timeout"}])
          and zones.round2_fallback_worthy(RuntimeError(), tries)
          and not zones.round2_fallback_worthy(RuntimeError(), [{"outcome": "error", "busy": True}, {"outcome": "429"}])
          and not zones.round2_fallback_worthy(RuntimeError(), [{"outcome": "day_spent"}])
          and not zones.round2_fallback_worthy(AiUnavailable("ai_quota_used"), [{"outcome": "schema"}])
          and not zones.round2_fallback_worthy(allowance.DayUsedUp("x"), [{"outcome": "schema"}]),
          "asked again as today after a refused schema, a timeout or a bad answer; never for busy, 429, a spent day "
          "or the person's AI going away")
    check(zones.round2_refused(tries) and not zones.round2_refused([{"outcome": "schema"}, {"outcome": "timeout"}]),
          "only a refusal (a non-busy error) latches Round 2 off; a timeout or a bad answer doesn't")
    from app.intake import assembly_walk, car_map

    walk = assembly_walk.index_for(car_map.MAP)
    big = Round2Ask(locate=zone_read.locate_names("close", LISTED), inventory=zone_read.inventory_names("close", True))
    fit, dropped = zone_read.fit_round2("close", LABELS, big, **ALL_LIGHT, walk=walk)
    small, none = zone_read.fit_round2("rear", LABELS, _ask("rear", True, True, False), evidence_mode="reason")
    check(dropped and dropped[0] == "inventory" and (fit is None or not fit.inventory)
          and len(json.dumps(zone_read.response_schema("close", LABELS, r2=fit, **ALL_LIGHT, walk=walk)))
          <= max(zone_read.R2_SCHEMA_CAP, len(json.dumps(zone_read.response_schema("close", LABELS, **ALL_LIGHT,
                                                                                    walk=walk))))
          and none == [] and small.inventory and small.locate,
          "over the cap (close-ups, every option, the walk): the inventory left out first, then locate if still "
          "over; under it, the whole ask (the review's L2)", dropped)


class Refused(Exception):
    status_code = 400

    def __str__(self):
        return "400 INVALID_ARGUMENT: the response schema has too many states"


class Busy(Exception):
    status_code = 503

    def __str__(self):
        return "503 UNAVAILABLE: the model is overloaded"


def _with_r2(request) -> bool:
    props = _props(request)
    return "locate" in props or "inventory" in props


@contextlib.contextmanager
def one_worker():
    """The zone calls one at a time, so which one comes first is known."""
    saved = zones.ZONE_WORKERS
    zones.ZONE_WORKERS = 1
    try:
        yield
    finally:
        zones.ZONE_WORKERS = saved


def degrade_end_to_end() -> None:
    zones.reset_round2_latch()
    chain = len(zones.zone_chain())
    with r2_case(dict(ZONES), {"zone": r2_zone()}) as (client, rid, pids, uid, state):
        plain = {"data": t.db_row(Report, rid).data, "zones": len(zone_requests())}
        pids = list(pids)
    base_list = shape(plain["data"]["damage_summary"], pids)
    sugg = [it["part"] for it in look_items(plain["data"])]
    both = dict(ZONES, intake_box_every="on", intake_zone_inventory="on")

    def refusing(kwargs, labels):
        return Refused() if _with_r2(kwargs) else r2_zone()(kwargs, labels)
    with one_worker(), warnings_of("app.intake.zones") as seen, r2_case(both, {"zone": refusing}) as (
            client, rid, pids, uid, state):
        data = t.db_row(Report, rid).data
        detail = zone_run(rid).photos_sent
        asked = zone_requests()
    got = shape(data["damage_summary"], pids)
    entries = {e["zone"]: e for e in detail["zones"]}
    with_r2 = [r for r in asked if _with_r2(r)]
    check(state["stage"] == "ready" and got == base_list and [it["part"] for it in look_items(data)] == sugg,
          "a schema the models refuse: the zone reads' finds as off's (asked again as today)", got)
    check(entries["rear"]["status"] == "ok" and entries["rear"]["r2"].get("fallback") == "ai_error"
          and "r2" not in entries["left"] and detail["r2"]["latched"] is True and detail["r2"]["fallbacks"] == 1
          and len(with_r2) == chain and len(asked) == chain + plain["zones"],
          f"the cost: the refused requests ({chain}, one per model) and the zone asked again as today; then Round 2 "
          f"is latched off -- the next zone is asked as today (the review's M6)",
          {"requests": len(asked), "with_r2": len(with_r2), "rear": entries["rear"].get("r2")})
    check(sum("asks without them until it restarts" in m for m in seen) == 1, "... said once in the log", seen)
    with r2_case(both, {"zone": refusing}) as (client, rid, pids, uid, state):
        detail = zone_run(rid).photos_sent
        asked = zone_requests()
    check(state["stage"] == "ready" and not [r for r in asked if _with_r2(r)] and detail["r2"]["latched"] is True
          and detail["r2"]["calls_asked"] == 0, "a later case asks without Round 2 at all while latched", detail["r2"])
    zones.reset_round2_latch()

    def refusing_then_busy(kwargs, labels):
        return Refused() if _with_r2(kwargs) else Busy()
    with one_worker(), r2_case(dict(ZONES, intake_box_every="on"), {"zone": refusing_then_busy}) as (
            client, rid, pids, uid, state):
        detail = zone_run(rid).photos_sent
        asked = zone_requests()
    plain_asked = [r for r in asked if not _with_r2(r)]
    jobs = len(detail["zones"])
    check(state["stage"] == "ready" and len(plain_asked) == jobs * chain and not round2_latched()
          and {e["status"] for e in detail["zones"]} == {"failed"},
          f"the fallback asked as today but busy: at most one request per model ({chain}), no all-busy round "
          f"(the review's M6); not latched (it never answered); the reading stands",
          {"plain requests": len(plain_asked), "zones": jobs})

    def busy(kwargs, labels):
        return Busy() if "inventory" in _props(kwargs) else r2_zone()(kwargs, labels)
    with r2_case(dict(ZONES, intake_zone_inventory="on"), {"zone": busy}) as (client, rid, pids, uid, state):
        detail = zone_run(rid).photos_sent
        asked = zone_requests()
    check(state["stage"] == "ready" and all("fallback" not in (e.get("r2") or {}) for e in detail["zones"])
          and all("inventory" in _props(r) for r in asked) and {e["status"] for e in detail["zones"]} == {"failed"}
          and not round2_latched(),
          "a busy chain is not asked again without Round 2 (it would be busy too): the zones fail as today's would, "
          "the reading stands", [e.get("r2") for e in detail["zones"]])

    def garbled(kwargs, labels):
        return Raw("not an answer") if _with_r2(kwargs) else r2_zone()(kwargs, labels)
    with r2_case(dict(ZONES, intake_zone_inventory="on"), {"zone": garbled}) as (client, rid, pids, uid, state):
        data = t.db_row(Report, rid).data
        detail = zone_run(rid).photos_sent
    check(state["stage"] == "ready" and shape(data["damage_summary"], pids) == base_list
          and all((e.get("r2") or {}).get("fallback") == "bad_answer" for e in detail["zones"])
          and not round2_latched(),
          "answers that don't parse: asked again as today, the finds as off's; not latched (no refusal)",
          [e.get("r2") for e in detail["zones"]])

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
          and detail["r2"]["salvaged"] >= 1 and len(zone_requests()) == plain["zones"],
          "an answer cut off inside the inventory: today's answer, no inventory, no extra call; the run log says "
          "salvaged", entries["rear"].get("r2"))
    # The schema cap at run time (the review's L2).
    saved = zone_read.R2_SCHEMA_CAP
    zone_read.R2_SCHEMA_CAP = 2600
    try:
        with r2_case(both, {"zone": r2_zone()}) as (client, rid, pids, uid, state):
            detail = zone_run(rid).photos_sent
            asked = zone_requests()
    finally:
        zone_read.R2_SCHEMA_CAP = saved
    entries = {e["zone"]: e for e in detail["zones"]}
    rear = next(r for r in asked if "the car's rear" in r["system_instruction"])
    left = next(r for r in asked if "one side of the car" in r["system_instruction"])
    check(state["stage"] == "ready" and entries["rear"]["r2"].get("trimmed") == ["inventory", "locate"]
          and entries["left"]["r2"].get("trimmed") == ["inventory"] and not _with_r2(rear)
          and "locate" in _props(left) and "inventory" not in _props(left) and detail["r2"]["trimmed"] == 2,
          "a schema over the cap: the inventory left out first, then locate -- logged", entries)
    # Round 2's helpers failing (the review's L3).
    for name in ("round2_ask", "round2_summary", "round2_log"):
        saved = getattr(zones, name)

        def boom(*a, **k):
            raise RuntimeError("bug")
        setattr(zones, name, boom)
        try:
            with r2_case(both, {"zone": r2_zone()}) as (client, rid, pids, uid, state):
                data = t.db_row(Report, rid).data
                detail = zone_run(rid).photos_sent
        finally:
            setattr(zones, name, saved)
        check(state["stage"] == "ready" and shape(data["damage_summary"], pids) == base_list
              and {e["status"] for e in detail["zones"]} == {"ok"},
              f"zones.{name} failing: every zone read answered and kept, the reading stands", detail.get("r2"))


def round2_latched() -> bool:
    return zones.round2_latched()


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
    parallel = SimpleNamespace(intake_parallel_reads="on", intake_main_read_fallback="zones")
    args = bench.parse_args(["--cases", "x", "--key", "y", "--out", "z", "--read-mode", "zones", "--box-every"])
    allowed = bench.parse_args(["--cases", "x", "--key", "y", "--out", "z", "--read-mode", "zones", "--box-every",
                                "--allow-box-every-parallel"])
    try:
        bench.r2_settings(args, True, parallel)
        refused = False
    except SystemExit:
        refused = True
    check(refused and bench.r2_settings(allowed, True, parallel) == {"intake_box_every": "on"}
          and bench.r2_settings(args, True, SimpleNamespace(intake_parallel_reads="on",
                                                            intake_main_read_fallback="off"))
          == {"intake_box_every": "on"},
          "--box-every with the zone reads beside the main read stops the run unless --allow-box-every-parallel "
          "(the review's M8)")
    flags = vars(bench.parse_args(["--cases", "x", "--key", "y", "--out", "z"]))
    check(all(flags[k] == v for k, v in bench.R2_ARGS.items()), "the flags' defaults are left out of a file's args")
    off = bench.ai_settings(settings)
    with t.Settings(intake_read_mode="zones", intake_zone_inventory="on", intake_car_map_names="on"):
        on = bench.ai_settings(settings)
    with t.Settings(intake_box_every="on"):
        single = bench.ai_settings(settings)
    check(on.get("intake_zone_inventory") == "on" and on.get("intake_box_every") == "off"
          and on.get("intake_car_map_names") == "on" and on.get("intake_parallel_reads") == "off"
          and on.get("intake_main_read_fallback") == "off"
          and not {"intake_box_every", "intake_zone_inventory", "intake_parallel_reads"} & (set(off) | set(single)),
          "ai_settings: Round 2's only while it acts (zone mode), with the parallel reads' settings", on)
    # A whole run of the benchmark with the fake: what it records.
    folder = Path(tempfile.mkdtemp(prefix="detect-r2-bench-")) / "case_r2"
    folder.mkdir(parents=True)
    for i in range(len(t.CASE)):
        (folder / f"p{i + 1:02d}.jpg").write_bytes(t.jpeg(i))
    case = {"name": "TEST R2", "folder": "case_r2", "vehicle": {
        "make": "Gammamotors", "model": "Vezel", "plate_number": "TEST-R2", "color": "White", "vin": "TESTVIN0000000R2",
        "engine_number": "TESTENGR2", "date_of_registration": "2019-01-01"},
        "parts": [{"part": p} for p in ("Rear Bumper", "Boot Lid", "Boot Floor Board", "Left Rear Door")]}
    runs_ = {}
    for name, values in (("off", ZONES), ("on", dict(ZONES, intake_box_every="on", intake_zone_inventory="on"))):
        with r2_fake(), t.Settings(**values), w.fakes({"zone": r2_zone(REAR_INVENTORY)}):
            runs_[name] = bench.run_case(case, folder.parent)
    on, off = runs_["on"], runs_["off"]
    check("error" not in on and "error" not in off, "the runs ran", (on.get("error"), off.get("error")))
    cov = on.get("box_coverage") or {}
    check(cov.get("items") == 3 and cov.get("located") == 3 and cov.get("boxed_before_locate") == 0
          and on["zones"]["box_coverage"] == cov and on["zones"]["r2"]["located"] == 3,
          "the result keeps box_coverage (from the AI draft and the run log)", cov)
    inv_rows = on.get("zone_inventory") or {}
    check(inv_rows.get("n") == 6 and inv_rows.get("damaged") == 5 and inv_rows["rows"][1]["photo_reference"] == "P05",
          "... the inventory, photos by case label", inv_rows)
    sugg = [r for r in on.get("suggestions") or [] if r.get("inventory_note")]
    check([r["part"] for r in sugg] == ["Rear End Panel", "Boot Floor Board", "Right Tail Lamp"]
          and [r["truth"] for r in sugg] == ["extra", "real", "extra"]
          and sugg[1]["found_by"] == ["zone_inventory:rear"],
          "... the inventory suggestions with their note and read, scored against the key", sugg)
    m, m_off = bench.run_metrics(on), bench.run_metrics(off)
    check(m.get("box_cov_items") == 3 and m.get("box_cov_located") == 3 and m.get("box_cov_boxed_before_locate") == 0
          and m.get("list_part_box") == 3 and m.get("list_box") == 0
          and m.get("inv_sugg") == 3 and m.get("inv_sugg_real") == 1 and m.get("inv_sugg_box") == 3
          and m.get("r2_extra_tok_est", 0) > 0 and m.get("r2_fallbacks") == 0
          and m.get("box_cov_zone_finds") == m.get("zone_finds") == 3,
          "run_metrics: the box coverage (part boxes apart from list_box, the AI's damage boxes), the inventory "
          "suggestions (real by the key), the extra tokens",
          {k: v for k, v in m.items() if k.startswith(("box_cov", "inv_", "r2_", "list_", "sug_", "zone_finds"))})
    check(m_off.get("list_box") == 0 and not [k for k in m_off if k.startswith(("box_cov", "inv_", "r2_"))]
          and "box_coverage" not in off and "zone_inventory" not in off and "r2" not in off["zones"]
          and m_off.get("list_low") == m.get("list_low") and m_off.get("zone_finds") == m.get("zone_finds"),
          "off: nothing of Round 2 in the result; list_low and zone_finds on both arms, to set side by side (M2)",
          {k: m_off.get(k) for k in ("list_box", "list_low", "zone_finds")})


def main() -> int:
    t.init_db()
    for name, fn in (
        ("0. settings: defaults and compose", settings_section),
        (f"1. both off: byte for byte as master {BASE_REF}", base_equal),
        ("1b. r2=None: today's request", requests_unchanged),
        ("2. the schema and prompt sizes, and the cap", size_section),
        ("3a. R2-box: what is asked and read", locate_units),
        ("3b. R2-box: placing the boxes", place_units),
        ("3c. R2-box: end to end", b2_end_to_end),
        ("3d. R2-box with the sorter's boxes (M1)", sorter_boxes),
        ("3e. R2-box with the parallel reads (M8)", parallel_reads),
        ("4a. R2-inv: the inventory and its suggestions", inventory_units),
        ("4b. R2-inv: end to end", b1_end_to_end),
        ("4c. R2-inv and the linked parts (M7)", links_join),
        ("5a. degrading (units)", degrade_units),
        ("5b. degrading end to end", degrade_end_to_end),
        ("6. the benchmark", bench_section),
    ):
        zones.reset_round2_latch()
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
