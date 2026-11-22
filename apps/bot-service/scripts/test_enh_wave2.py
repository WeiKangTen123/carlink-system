"""Wave 2's switches (plans/2026-11-22-enhance-staging/plan.md 3c, 6) --
offline: Gemini is the fake of scripts/test_c10_integration.py, no server,
made-up cases only.

  0. settings: each Wave 2 setting defaults to today's behaviour, takes its
     choices, refuses a typo, and is passed through docker-compose.prod.yml
     once, in the block both containers share;
  1. everything at the defaults: the requests and the stored data byte for
     byte as fix/enh-backend's (a850771) -- the full routes in single mode,
     zone mode, links on and links capped hard, and the reads Wave 2
     touches: a 40-photo case, an end zone that sees the other end, a side
     zone -- the same scenarios on a git archive of that commit and here;
  2. E12: the main read's photos -- the priority order kept when more fit
     (32 among 40 among 64), the byte budget above 32, the main read's own
     resolution on its photos only (the zone reads' as before), the limit
     checked 1-64;
  3. E7: the geometry -> side mapping (a whole-side view by its angle and
     the steering wheel, right-hand drive; corners, ends and conflicts
     none), the majority (ties none), the side zones' reconcile and the
     resolver's vote / tie-break; the sort's request only when on; end to
     end in both modes;
  4. E11: a "wrong end" zone read -- drop (as before), shadow (the run log
     only, the stored data as drop's), keep (suggestions with the note,
     never on the list);
  5. the benchmark: Wave 2's flags (and the ones that would change
     nothing refused), ai_settings, the sort cache with the geometry, the
     sorter's counts (E10), the linked check's verdict metrics (E9);
  6. replay_link_check.py with the fake client: production's linked parts
     and linked check on a stored run's reads, through the rate limiter,
     the arms (E9a's third area, E9b's L2 skill), the output compare.py and
     gate.py --suggestions read, the list untouched.

    python scripts/test_enh_wave2.py
    python scripts/test_enh_wave2.py --probe OUT.json   (section 1's
        scenarios alone; C10_TEST_ROOT=<a bot-service checkout> runs them
        on that tree, as section 1 does with fix/enh-backend's)
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
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(1, str(HERE))
WAVE2_ENV = ("INTAKE_MAX_READ_PHOTOS", "GEMINI_IMAGE_RESOLUTION_MAIN_READ", "INTAKE_SIDE_GEOMETRY",
             "INTAKE_ZONE_WRONG_END", "INTAKE_LINK_CHECK_MAX_CALLS", "GEMINI_IMAGE_RESOLUTION",
             "GEMINI_THINKING_LEVEL_SORT", "GEMINI_TRIAGE_MODEL_CHAIN", "GEMINI_LINK_CHAIN")
for _name in WAVE2_ENV + ("INTAKE_MAIN_READ_FALLBACK", "INTAKE_KEEP_CUT", "INTAKE_KEEP_OTHER_SIDE",
                          "INTAKE_LINK_HIDDEN_NO", "INTAKE_SORT_RETRY_SKIPPED", "INTAKE_PARALLEL_READS",
                          "INTAKE_LINK_CHECK_PARALLEL", "INTAKE_SORT_WORKERS", "INTAKE_KEEP_SORT_LABEL",
                          "INTAKE_SORT_RESCUE", "INTAKE_CAR_MAP", "INTAKE_LINKS_ORDER", "INTAKE_LINKS_ASSEMBLY_MAX",
                          "INTAKE_LINKS_STRIPDOWN_CAP", "INTAKE_LINKS_STRIPDOWN_MAX", "INTAKE_ASSEMBLY_WALK",
                          "INTAKE_READ_PACKS", "INTAKE_ZONE_PACKS", "INTAKE_SORT_PROMPT", "INTAKE_LINK_SKILL",
                          "BENCHMARK_SCRATCH"):
    os.environ.pop(_name, None)

# The fake Gemini, the scratch database and the routes' helpers (C10_TEST_ROOT
# picks the tree the app is imported from).
import test_c10_integration as t  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.intake.photos import ordered_photos  # noqa: E402
from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import AiRun, IntakeSession, Report, User  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

ZONES = dict(intake_read_mode="zones", intake_zone_only="suggest", intake_zone_evidence="reason")
LINKS = dict(ZONES, intake_links="on", intake_link_check="lite")
CAPPED = dict(LINKS, intake_links_max=2, intake_links_stripdown_cap="out", intake_links_stripdown_max=1)
PROBE_MODES = {
    "single": {},
    "zones": dict(ZONES, intake_check_mode="lite"),
    "links_on": dict(LINKS, intake_check_mode="lite"),
    "links_capped": CAPPED,
}
BASE_REF = "a850771"  # fix/enh-backend, merged with master: what this branch starts from


def kinds(requests=None) -> Counter:
    return Counter(t.kind_of(r) for r in (t.REQUESTS if requests is None else requests))


# --------------------------------------------------------------- the fakes' cases

def big_rows(n: int = 40) -> list:
    """n photos: a log card, 3 overviews, then rear damage close-ups (two
    parts in turn), a few tyres and "other" photos, the scene last -- more
    than the main read's 32 slots."""
    rows = [("log_card", None, None, False), ("overview", "rear", None, False), ("overview", "left", None, False),
            ("overview", "rear-right", None, False)]
    parts = ["Rear Bumper", "Boot Lid", "Left Tail Lamp", "Right Tail Lamp"]
    while len(rows) < n - 6:
        rows.append(("damage", "rear", parts[len(rows) % len(parts)], True))
    rows += [("tyre", "left", None, False), ("tyre", "right", None, False), ("other", "rear", None, True),
             ("other", "left", None, False), ("interior", "interior", None, False), ("scene", None, None, False)]
    return rows[:n]


# A side-hit case for E7: P06 a whole-side overview, P07/P08 close-ups of the
# rear door, all sorted "left"; the geometry says the camera saw the RIGHT side.
SIDE_ROWS = [
    ("log_card", None, None, False), ("overview", "rear", None, False), ("damage", "rear", "Rear Bumper", True),
    ("damage", "rear", "Left Tail Lamp", True), ("damage", "rear", "Boot Lid", True), ("overview", "left", None, False),
    ("damage", "left", None, True), ("damage", "left", None, True),
    ("damage", "rear", None, True), ("third_party", None, None, True),
]
# label -> (view, angle, steering_wheel) the fake sort answers when asked.
SIDE_GEOMETRY = {"P06": ("side", "front to photo right", "near_side"), "P07": ("side", "front to photo right", "not_seen"),
                 "P08": ("unknown", None, "not_seen"), "P02": ("rear", "front away from camera", "photo_right")}


@contextlib.contextmanager
def fakes(answers: dict | None = None, rows: list | None = None):
    """The fake's answers and case photos (the whole list), for the block."""
    old_answers, old_case = dict(t.ANSWERS), list(t.CASE)
    t.ANSWERS.update(answers or {})
    if rows is not None:
        t.CASE[:] = list(rows)
    try:
        yield
    finally:
        t.ANSWERS.clear()
        t.ANSWERS.update(old_answers)
        t.CASE[:] = old_case


@contextlib.contextmanager
def a_case(values: dict, answers: dict | None = None, rows: list | None = None):
    """A new draft read once with these settings and fake answers: yields
    (client, rid, pids, uid, state), the settings still set."""
    t.REQUESTS.clear()
    with t.Settings(**values), fakes(answers, rows), TestClient(t._app()) as client:
        uid = sign_in(client, role="admin", may_sign_off=True)
        rid, pids = t.new_draft(client, len(t.CASE))
        state = t.read(client, rid)
        yield client, rid, pids, uid, state


def _zone_item(props: dict, label: str, part: str, confidence: str = "Medium", tokens=("Cracked",)) -> dict:
    out = {"photo_reference": label, "part": part, "ai_confidence": confidence, "condition_tokens": list(tokens)}
    if "reason" in props:
        out["reason"] = f"The {part.lower()} looks damaged."
    if "box_2d" in props:
        out["box_2d"] = [120, 140, 520, 600]
    if "grid_cells" in props:
        out["grid_cells"] = []
    return out


def wrong_end_zone(kwargs, labels):
    """The rear zone says its photos show the FRONT, and lists finds."""
    if "the car's rear" not in kwargs["system_instruction"]:
        return t._zone_answer(kwargs, labels)
    props = kwargs["response_format"]["schema"]["properties"]["damage"]["items"]["properties"]
    damage = [_zone_item(props, lb, part) for lb, part in (("P04", "Left Tail Lamp"), ("P03", "Rear Bumper"))
              if lb in labels]
    return {"zone_side_seen": "front", "damage": damage, "checked_intact": []}


def side_zone(seen: str):
    """The side zone answers `seen` and the rear door on P07; the rest as the
    plain fake."""
    def answer(kwargs, labels):
        if "one side of the car" not in kwargs["system_instruction"]:
            return t._zone_answer(kwargs, labels)
        props = kwargs["response_format"]["schema"]["properties"]["damage"]["items"]["properties"]
        damage = [_zone_item(props, "P07", "Rear Door", "High", ("Dented",))] if "P07" in labels else []
        return {"zone_side_seen": seen, "damage": damage, "checked_intact": []}
    return answer


def no_door_draft(kwargs, labels):
    """The main read without the door: the side zone's find is a suggestion."""
    out = t._draft_answer(labels)
    out["damage_summary"] = [it for it in out["damage_summary"] if "Door" not in it["part"]]
    out["damaged_parts"] = [p for p in out["damaged_parts"] if "Door" not in p]
    return out


def geometry_sort(kwargs, labels):
    """The sort, answering the geometry when the schema asks for it."""
    out = t._sort_answer(labels)
    props = kwargs["response_format"]["schema"]["properties"]["photos"]["items"]["properties"]
    if "view" in props:
        for photo in out["photos"]:
            view, angle, wheel = SIDE_GEOMETRY.get(photo["label"], ("unknown", None, "not_seen"))
            photo.update(view=view, angle=angle, steering_wheel=wheel)
    return out


def photos_of(rid: str) -> list[tuple]:
    db = SessionLocal()
    try:
        return [(p.kind, p.area, p.part, p.shows_damage) for p in ordered_photos(db, rid)]
    finally:
        db.close()


def look_of(rid: str) -> dict:
    return (t.db_row(Report, rid).data or {}).get("second_look") or {}


def zone_entries(rid: str) -> list[dict]:
    db = SessionLocal()
    try:
        row = (db.query(AiRun).filter(AiRun.report_id == rid, AiRun.kind == "read_zones")
               .order_by(AiRun.started_at.desc()).first())
        return list(((row.photos_sent if row else None) or {}).get("zones") or [])
    finally:
        db.close()


# Section 1's reads Wave 2 touches: (settings, answers, case rows).
READ_SCENARIOS = {
    "big_case_links": (dict(LINKS), {}, big_rows(40)),
    "wrong_end": (dict(ZONES), {"zone": wrong_end_zone}, None),
    "side_zone": (dict(ZONES), {"zone": side_zone("left"), "draft": no_door_draft}, SIDE_ROWS),
}


def read_scenario(name: str) -> dict:
    """One read-only scenario, normalised: what was asked and what was kept."""
    values, answers, rows = READ_SCENARIOS[name]
    with a_case(values, answers, rows) as (client, rid, pids, uid, state):
        ids = {rid: "<rid>", uid: "<uid>", t.db_row(User, uid).email: "<email>",
               **{pid: f"<p{i + 1:02d}>" for i, pid in enumerate(pids)}}
        session = t.db_row(IntakeSession, rid)
        out = {
            "stage": state["stage"], "error": state["error"],
            "data": t.db_row(Report, rid).data, "ai_draft": session.ai_draft, "read_photo_ids": session.read_photo_ids,
            "runs": t._run_rows(rid), "photos": photos_of(rid),
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
from app.ai import link_check, structured, triage, zone_read  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import geometry, pipeline, zones  # noqa: E402
from app.reports.models import IntakePhoto  # noqa: E402
from app.reports.schema import SecondLookItem  # noqa: E402

ROOT = HERE.parent
REPO = ROOT.parent.parent
PY = sys.executable

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


def clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not (k.startswith("INTAKE_") or k.startswith("GEMINI_")
                                                       or k in ("C10_TEST_ROOT", "DATABASE_URL", "STORAGE_DIR",
                                                                "SECRETS_DIR", "BENCHMARK_SCRATCH"))}
    env.update(PYTHONIOENCODING="utf-8", **extra)
    return env


def _first_diff(a, b, path="$") -> str:
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k}: only in {'here' if k in a else 'base'}"
            if a[k] != b[k]:
                return _first_diff(a[k], b[k], f"{path}.{k}")
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} vs {len(b)} entries"
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                return _first_diff(x, y, f"{path}[{i}]")
    return f"{path}: {json.dumps(a, default=str)[:300]} vs {json.dumps(b, default=str)[:300]}"


# ------------------------------------------------------------- 0. settings

NEW_SETTINGS = {"intake_side_geometry": "off", "intake_zone_wrong_end": "drop", "intake_max_read_photos": None,
                "gemini_image_resolution_main_read": None, "intake_link_check_max_calls": 2}
COMPOSE = {"INTAKE_MAX_READ_PHOTOS": "", "GEMINI_IMAGE_RESOLUTION_MAIN_READ": "", "INTAKE_SIDE_GEOMETRY": "off",
           "INTAKE_ZONE_WRONG_END": "drop", "INTAKE_LINK_CHECK_MAX_CALLS": "2"}


def settings_section() -> None:
    fresh = config.Settings()
    check({k: getattr(fresh, k) for k in NEW_SETTINGS} == NEW_SETTINGS, "every Wave 2 setting defaults to today's",
          {k: getattr(fresh, k) for k in NEW_SETTINGS})
    check({k: getattr(settings, k) for k in NEW_SETTINGS} == NEW_SETTINGS, "and this process runs at them")
    problems = []
    for name, good, want in (("intake_side_geometry", "VOTE", "vote"), ("intake_side_geometry", "tiebreak", "tiebreak"),
                             ("intake_zone_wrong_end", "Shadow", "shadow"), ("intake_zone_wrong_end", "keep", "keep"),
                             ("intake_max_read_photos", "40", 40), ("intake_max_read_photos", "64", 64),
                             ("gemini_image_resolution_main_read", "*=MEDIUM", "*=medium"),
                             ("gemini_image_resolution_main_read", "damage=low,*=medium", "damage=low,*=medium")):
        if getattr(config.Settings(**{name: good}), name) != want:
            problems.append((name, good, "not taken"))
    for name, bad in (("intake_side_geometry", "maybe"), ("intake_zone_wrong_end", "kept"),
                      ("intake_max_read_photos", "0"), ("intake_max_read_photos", "65"), ("intake_max_read_photos", "x"),
                      ("gemini_image_resolution_main_read", "*=tiny"), ("gemini_image_resolution_main_read", "medium")):
        try:
            config.Settings(**{name: bad})
            problems.append((name, bad, "a typo accepted"))
        except Exception:
            pass
    for name in ("intake_side_geometry", "intake_zone_wrong_end", "intake_max_read_photos",
                 "gemini_image_resolution_main_read"):
        if getattr(config.Settings(**{name: ""}), name) != NEW_SETTINGS[name]:
            problems.append((name, "", "blank is not the default"))
    check(not problems, "each takes its choices (any case), blank is its default, a typo stops the app", problems)
    compose = (REPO / "infra" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    anchor, services = compose.index("x-bot-service: &bot-service"), compose.index("\nservices:")
    bad = []
    for env, default in COMPOSE.items():
        want = f"    - {env}=${{{env}:-{default}}}"
        lines = [ln for ln in compose.splitlines() if ln.startswith(f"    - {env}=")]
        at = compose.find(f"- {env}=")
        if lines != [want] or not anchor < at < services:
            bad.append((env, lines, want))
    check(not bad, "docker-compose.prod.yml: each once, in the shared x-bot-service block, with config's default", bad)
    check(config.Settings(intake_link_check_max_calls="3").intake_link_check_max_calls == 3,
          "E9a: intake_link_check_max_calls takes 3 (no upper bound in config)")


# ------------------------------------------------------------- 1. the defaults, as fix/enh-backend


def base_equal() -> None:
    work = Path(tempfile.mkdtemp(prefix="enh-wave2-base-"))
    tar = work / "base.tar"
    made = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", "-o", str(tar), BASE_REF,
                           "apps/bot-service"], capture_output=True, text=True)
    if not check(made.returncode == 0, f"fix/enh-backend {BASE_REF}'s bot-service from git (archive)", made.stderr[-300:]):
        return
    with tarfile.open(tar) as archive:
        archive.extractall(work / "tree", filter="data")
    base_root = work / "tree" / "apps" / "bot-service"
    outs = {}
    for name, root in (("base", base_root), ("here", ROOT)):
        out = work / f"{name}.json"
        proc = subprocess.run([PY, str(HERE / "test_enh_wave2.py"), "--probe", str(out)], capture_output=True,
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
              _first_diff(mine, theirs))
    for name in READ_SCENARIOS:
        mine, theirs = outs["here"][name], outs["base"][name]
        check(mine == theirs, f"{name}: the requests, the run log, the photos and the stored draft as {BASE_REF}'s",
              _first_diff(mine, theirs))
    here = outs["here"]
    check(len(here["big_case_links"]["read_photo_ids"] or []) == 32, "... the 40-photo case reads 32 at the defaults",
          len(here["big_case_links"]["read_photo_ids"] or []))
    sugg = [it.get("part") for it in (here["wrong_end"]["data"].get("second_look") or {}).get("items") or []]
    check("Left Tail Lamp" not in sugg, "... a wrong-end zone's finds are dropped at the defaults", sugg)


# ------------------------------------------------------------- 2. E12


def _photo(i: int, kind, area=None, part=None, damage=False, **kw) -> IntakePhoto:
    return IntakePhoto(id=f"p{i}", report_id="r", position=i, path=kw.pop("path", ""), sha256="", kind=kind, area=area,
                       part=part, quality=kw.pop("quality", "good"), shows_damage=damage,
                       excluded=kw.pop("excluded", False), **kw)


def _many(n: int, folder: Path | None = None, size: int = 0) -> list[IntakePhoto]:
    """n photos: 2 facts, 3 overviews, damage close-ups of 6 parts in turn,
    5 "other" photos and a scene at the end; with a folder, each a file of
    `size` bytes."""
    parts = ["Rear Bumper", "Boot Lid", "Left Tail Lamp", "Right Tail Lamp", "Rear End Panel", "Rear Windscreen"]
    rows = [("log_card", None, None, False), ("odometer", None, None, False)]
    rows += [("overview", a, None, False) for a in ("rear", "rear-left", "rear-right")]
    while len(rows) < n - 6:
        rows.append(("damage", "rear", parts[len(rows) % len(parts)], True))
    rows += [("other", "rear", None, False)] * 5 + [("scene", None, None, False)]
    out = []
    for i, (kind, area, part, damage) in enumerate(rows[:n]):
        path = ""
        if folder is not None:
            path = str(folder / f"p{i}.jpg")
            Path(path).write_bytes(b"x" * size)
        out.append(_photo(i, kind, area, part, damage, path=path))
    return out


@contextlib.contextmanager
def read_limit(n):
    with t.Settings(intake_max_read_photos=n):
        yield


def e12_section() -> None:
    case = _many(70)
    picked = {}
    for n in (None, 40, 48, 64):
        with read_limit(n):
            picked[n] = pipeline.select_for_reading(case, priority_order=True)
    ids = {n: [p.id for p in got] for n, got in picked.items()}
    check(len(ids[None]) == 32 and len(ids[40]) == 40 and len(ids[64]) == 64, "32 by default; 40 and 64 with the setting",
          {n: len(v) for n, v in ids.items()})
    check(ids[40][:32] == ids[None] and ids[48][:40] == ids[40] and ids[64][:48] == ids[48],
          "the same priority order: a bigger limit takes further down it (the 32 first, in the same order)")
    with read_limit(40):
        in_case_order = pipeline.select_for_reading(case)
    check([p.position for p in in_case_order] == sorted(p.position for p in picked[40]),
          "sent in case order, as before (priority_order only for the tests and the notes)")
    extra = picked[40][32:]
    check(all(p.kind == "damage" for p in extra), "the 8 more are damage close-ups (the damage step takes more)",
          [p.kind for p in extra])
    facts = [p.kind for p in picked[None][:5]]
    check(facts == ["log_card", "odometer", "overview", "overview", "overview"],
          "the facts first, then the overviews (FACT_BUDGET, OVERVIEW_BUDGET)", facts)
    # The byte budget, only above 32.
    folder = Path(tempfile.mkdtemp(prefix="wave2-sizes-"))
    sized = _many(70, folder, size=600_000)  # 800 KB each as base64
    with read_limit(32):
        default = pipeline.select_for_reading(sized)
    with read_limit(64):
        big = pipeline.select_for_reading(sized, priority_order=True)
    budget = pipeline.READ_BYTES_BUDGET // pipeline._sent_bytes(sized[0])
    check(len(default) == 32, "at 32 nothing is measured: 32 photos of 800 KB still go (as before)", len(default))
    check(len(big) == budget < 64 and [p.id for p in big] == [p.id for p in pipeline.select_for_reading(
        sized, priority_order=True)][:budget], f"above 32 the request stops at READ_BYTES_BUDGET: {budget} of 64, "
          "the lowest priority left out", len(big))
    import shutil

    shutil.rmtree(folder, ignore_errors=True)
    # The main read's own resolution, end to end.
    def images_of(kind):
        return [p.get("resolution") for r in t.REQUESTS if t.kind_of(r) == kind for p in r["input"]
                if p.get("type") == "image"]
    with a_case(dict(LINKS, intake_max_read_photos=40, gemini_image_resolution_main_read="*=medium"),
                rows=big_rows(40)) as (client, rid, pids, uid, state):
        read, zone = images_of("draft"), images_of("zone")
        session = t.db_row(IntakeSession, rid)
    check(state["stage"] == "ready" and len(read) == 40 and set(read) == {"medium"},
          "E12b: 40 photos read, each at the main read's resolution (medium)", Counter(read))
    check(zone and set(zone) == {None}, "the zone reads' photos as before (no resolution)", Counter(zone))
    check(len(session.read_photo_ids) == 40, "read_photo_ids has the 40")
    with a_case(dict(ZONES, gemini_image_resolution="damage=low"), rows=big_rows(40)) as (client, rid, pids, uid, state):
        read, zone = images_of("draft"), images_of("zone")
    check(len(read) == 32 and "low" in read and "low" in zone,
          "unset, the main read takes gemini_image_resolution as before (the zone reads too)", Counter(read))
    check(structured.image_resolution("damage", "*=medium") == "medium"
          and structured.image_resolution(None, "*=medium") is None,
          "image_resolution: a table given wins; a photo with no kind (the bot's) none")


# ------------------------------------------------------------- 3. E7


def e7_units() -> None:
    side = geometry.photo_side
    cases = [
        ({"view": "side", "angle": "front to photo left", "wheel": "not_seen"}, "Left"),
        ({"view": "side", "angle": "front to photo right", "wheel": "not_seen"}, "Right"),
        ({"view": "side", "angle": None, "wheel": "near_side"}, "Right"),
        ({"view": "side", "angle": None, "wheel": "far_side"}, "Left"),
        ({"view": "side", "angle": "front to photo right", "wheel": "near_side"}, "Right"),
        ({"view": "side", "angle": "front to photo left", "wheel": "near_side"}, None),
        ({"view": "side", "angle": "front toward camera", "wheel": "not_seen"}, None),
        ({"view": "corner", "angle": "front to photo left", "wheel": "near_side"}, None),
        ({"view": "front", "angle": "front toward camera", "wheel": "photo_left"}, None),
        ({"view": "rear", "angle": "front away from camera", "wheel": "photo_right"}, None),
        ({"view": "unknown", "angle": "front to photo left", "wheel": "not_seen"}, None),
        (None, None),
    ]
    got = [(g, side(g)) for g, want in cases if side(g) != want]
    check(not got, "photo_side: a whole-side view by its angle (front to the photo's left = the LEFT side) and the "
                   "wheel (near = RIGHT, right-hand drive); a conflict, a corner, an end, unknown: none", got)
    check(geometry.rhd_check({"view": "front", "angle": "front toward camera", "wheel": "photo_left"}) == "ok"
          and geometry.rhd_check({"view": "rear", "angle": "front away from camera", "wheel": "photo_right"}) == "ok"
          and geometry.rhd_check({"view": "front", "angle": "front toward camera", "wheel": "photo_right"}) == "conflict"
          and geometry.rhd_check({"view": "side", "angle": "front to photo left", "wheel": "near_side"}) is None,
          "rhd_check: an end view's wheel against its angle (logged only)")
    cleaned = geometry.clean({"view": "sideways", "angle": "upside down", "steering_wheel": "near_side"})
    check(cleaned == {"view": "unknown", "angle": None, "wheel": "near_side", "side": None},
          "clean: anything outside the vocabularies is unknown / None", cleaned)
    check(geometry.clean({"view": "side", "angle": "front to photo left", "steering_wheel": "far_side"})["side"] == "Left",
          "clean: the side worked out")
    check(geometry.majority(["Left", "Left", "Right", None]) == "Left" and geometry.majority(["Left", "Right"]) is None
          and geometry.majority([None, None]) is None and geometry.majority([]) is None,
          "majority: the side more photos say; a tie or none: None")
    photos = [SimpleNamespace(id=f"g{i}", kind=k, area=a, quality="good", excluded=False, geometry=g)
              for i, (k, a, g) in enumerate([
                  ("overview", "left", {"view": "side", "angle": "front to photo right", "wheel": "not_seen"}),
                  ("damage", "left", {"view": "side", "angle": None, "wheel": "near_side"}),
                  ("damage", "left", {"view": "unknown", "angle": None, "wheel": "not_seen"}),
                  ("third_party", "left", {"view": "side", "angle": "front to photo left", "wheel": "not_seen"}),
                  ("overview", "front-left", {"view": "corner", "angle": "front to photo left", "wheel": "not_seen"}),
                  ("overview", "right", {"view": "side", "angle": "front to photo left", "wheel": "not_seen"}),
                  ("overview", "right", {"view": "side", "angle": "front to photo right", "wheel": "not_seen"}),
              ])]
    groups = geometry.area_sides(photos)
    check(groups == {"g0": "Right", "g1": "Right", "g2": "Right", "g3": "Right"},
          "area_sides: per sorter area; the other party's car never votes; a corner area of corner views and a "
          "tied area have none", groups)
    rec = geometry.reconcile
    combos = {("left", "Right", "vote"): "unclear", ("left", "Left", "vote"): "left", ("unclear", "Right", "vote"): "right",
              ("left", None, "vote"): "left", ("left", "Right", "tiebreak"): "left",
              ("unclear", "Left", "tiebreak"): "left", ("unclear", None, "tiebreak"): "unclear",
              ("left", "Right", "off"): "left"}
    bad = {k: rec(*k) for k, want in combos.items() if rec(*k) != want}
    check(not bad, "reconcile: vote -- agree or one abstains: that side; disagree: unclear; tiebreak -- only an "
                   "unclear model takes the geometry", bad)
    # The resolver.
    # c: a rear photo with no neighbour that has an area within reach.
    areas = [("a", "left"), ("b", "left"), ("x", None), ("y", None), ("c", "rear"), ("d", None)]

    def place(item_photo, mode, geo, part="Rear Quarter Panel (side undetermined)", extra=()):
        items = [{"part": part, "photo_reference": f"@{item_photo}"}, *extra]
        log: list = []
        args = {"geo_sides": geo, "geometry_mode": mode, "geometry_log": log} if mode else {}
        return pipeline.resolve_sides([dict(i) for i in items], areas, **args)[0]["part"], log
    check(place("a", None, None)[0] == "Left Rear Quarter Panel", "today: the photo's own area places it (left)")
    got, log = place("a", "vote", {"a": "Right"})
    check(got == "Rear Quarter Panel (side undetermined)" and log == [
        {"part": "Rear Quarter Panel (side undetermined)", "photo_reference": "@a", "was": "Left", "now": None,
         "geometry": "Right"}],
          "vote: the geometry against the photo's own area leaves it undetermined, and says so", (got, log))
    check(place("a", "vote", {"a": "Left"})[0] == "Left Rear Quarter Panel" and place("a", "vote", {"a": "Left"})[1] == [],
          "vote: agreeing changes nothing (no log)")
    check(place("a", "tiebreak", {"a": "Right"})[0] == "Left Rear Quarter Panel",
          "tiebreak: the photo's own area stands; the geometry only breaks ties")
    got, log = place("c", "tiebreak", {"c": "Right"})
    check(got == "Right Rear Quarter Panel" and log and log[0]["was"] is None,
          "tiebreak: where today's rules place nothing (a rear photo, no clue), the geometry side", (got, log))
    got_vote, _ = place("c", "vote", {"c": "Right"}, part="Tail Lamp (side undetermined)")
    check(got_vote == "Right Tail Lamp", "vote: as the only clue, a majority of one places it", got_vote)
    from app.reports.filing import relabel_ai_draft

    filed = relabel_ai_draft({"side_geometry": [{"part": "x", "photo_reference": "@c", "was": None, "now": "Right"}]},
                             {"c": 4})
    check(filed["side_geometry"][0]["photo_reference"] == "P05", "filing relabels the resolver's log (P-numbers)",
          filed)


def e7_sort_request() -> None:
    base_prompt, base_schema = triage.system_prompt(False, "v2", False), triage.response_schema(False, False)
    check(triage.system_prompt() == base_prompt and triage.response_schema() is triage._RESPONSE_SCHEMA
          and triage.sort_prompt_version() == "v2-car-sides", "off: the sort's prompt, schema and version as before")
    with t.Settings(intake_side_geometry="vote"):
        prompt, schema, version = triage.system_prompt(), triage.response_schema(), triage.sort_prompt_version()
    props = list(schema["properties"]["photos"]["items"]["properties"])
    check(props == ["label", "kind", "view", "angle", "steering_wheel", "area", "quality", "shows_damage", "part", "note"]
          and schema["properties"]["photos"]["items"]["required"] == props,
          "on: view before the angle, steering_wheel after it, both required", props)
    check(prompt.replace(triage.GEOMETRY_VIEW_RULE, "").replace(triage.GEOMETRY_WHEEL_RULE, "") == base_prompt
          and prompt.index(triage.GEOMETRY_VIEW_RULE) < prompt.index("- angle: ")
          < prompt.index(triage.GEOMETRY_WHEEL_RULE) < prompt.index("- area: "),
          "on: the two rules, before the angle's line and the area's -- nothing else changed")
    check("left" not in triage.GEOMETRY_VIEW_RULE.replace("no left or right", "").lower()
          and set(schema["properties"]["photos"]["items"]["properties"]["view"]["enum"]) == set(geometry.VIEWS),
          "the view's answers name no side (front, rear, side, corner, unknown)")
    check(version == "v2-car-sides+geometry", "its own sort version (the benchmark's sort cache keeps it apart)", version)
    with t.Settings(intake_side_geometry="tiebreak", intake_sort_damage_box=True, intake_sort_prompt="v3"):
        both = triage.response_schema()
        prompt3 = triage.system_prompt()
    names = list(both["properties"]["photos"]["items"]["properties"])
    check(names.index("damage_box") == names.index("part") + 1 and "view" in names
          and triage.GEOMETRY_VIEW_RULE in prompt3 and triage.GEOMETRY_WHEEL_RULE in prompt3,
          "with the damage box and the v3 prompt too", names)
    parsed = triage._clean({"label": "P01", "kind": "overview", "area": "left", "view": "side",
                            "angle": "front to photo right", "steering_wheel": "near_side", "quality": "good"},
                           False, True)
    check(parsed.get("geometry") == {"view": "side", "angle": "front to photo right", "wheel": "near_side", "side": "Right"}
          and set(triage._clean({"kind": "overview"})) == {"kind", "area", "quality", "shows_damage", "part", "note"},
          "_clean: the geometry kept when asked; off, the keys as before", parsed)


def e7_end_to_end() -> None:
    answers = {"sort": geometry_sort, "zone": side_zone("unclear"), "draft": no_door_draft}
    with a_case(dict(ZONES), answers, SIDE_ROWS) as (client, rid, pids, uid, state):
        off_sort = [r for r in t.REQUESTS if t.kind_of(r) == "sort"]
        off_sugg = [it["part"] for it in look_of(rid).get("items") or []]
    off_props = off_sort[0]["response_format"]["schema"]["properties"]["photos"]["items"]["properties"]
    check(state["stage"] == "ready" and "view" not in off_props and "steering_wheel" not in off_props
          and "Door (side undetermined)" in off_sugg,
          "off: the sort asks nothing new; the side zone's 'unclear' names the door undetermined", off_sugg)
    with a_case(dict(ZONES, intake_side_geometry="tiebreak"), answers, SIDE_ROWS) as (client, rid, pids, uid, state):
        sugg = [it["part"] for it in look_of(rid).get("items") or []]
        entries = {e["zone"]: e for e in zone_entries(rid)}
        db = SessionLocal()
        try:
            stored = {i + 1: p.geometry for i, p in enumerate(ordered_photos(db, rid))}
        finally:
            db.close()
        ai_draft = t.db_row(IntakeSession, rid).ai_draft
    check(stored[6] == {"view": "side", "angle": "front to photo right", "wheel": "near_side", "side": "Right"}
          and stored[8]["side"] is None, "on: each photo keeps the sort's geometry and its side", stored[6])
    left = entries.get("left") or {}
    check(left.get("side_seen") == "right" and left.get("geometry") == {
        "mode": "tiebreak", "side": "Right", "votes": {"Left": 0, "Right": 2}, "model": "unclear"},
          "tiebreak: the side zone's 'unclear' takes the geometry's Right (2 votes of its photos); logged", left)
    check("Right Rear Door" in sugg and "Door (side undetermined)" not in sugg,
          "... so its find is named Right Rear Door", sugg)
    check(isinstance(ai_draft.get("side_geometry"), list), "the resolver's changes are kept beside the AI draft")
    answers_left = dict(answers, zone=side_zone("left"))
    with a_case(dict(ZONES, intake_side_geometry="vote"), answers_left, SIDE_ROWS) as (client, rid, pids, uid, state):
        sugg = [it["part"] for it in look_of(rid).get("items") or []]
        left = {e["zone"]: e for e in zone_entries(rid)}.get("left") or {}
    check(left.get("side_seen") == "unclear" and (left.get("geometry") or {}).get("model") == "left"
          and "Door (side undetermined)" in sugg,
          "vote: the model's left against the geometry's Right -> unclear (a wrong side is worse than none)",
          (left, sugg))
    with a_case(dict(ZONES, intake_side_geometry="tiebreak"), answers_left, SIDE_ROWS) as (client, rid, pids, uid, state):
        sugg = [it["part"] for it in look_of(rid).get("items") or []]
    check("Left Rear Door" in sugg, "tiebreak: the model's own side stands", sugg)


# ------------------------------------------------------------- 4. E11


def e11_units() -> None:
    labels = ["P03", "P04"]
    raw = json.dumps({"zone_side_seen": "front", "damage": [
        {"photo_reference": "P04", "part": "Left Tail Lamp", "ai_confidence": "Medium", "condition_tokens": ["Cracked"]}],
        "checked_intact": ["Rear Windscreen"]})
    drop = zone_read.parse_answer("rear", raw, labels)
    shadow = zone_read.parse_answer("rear", raw, labels, wrong_end="shadow")
    keep = zone_read.parse_answer("rear", raw, labels, wrong_end="keep")
    check(drop.items == [] and drop.intact == [] and drop.wrong_end_items == []
          and drop.note == "the photos show the front, not the rear: its finds were dropped",
          "drop: no finds, no intact, the note as before")
    check(shadow.items == [] and [i["part"] for i in shadow.wrong_end_items] == ["Left Tail Lamp"] and shadow.intact == []
          and shadow.note == drop.note, "shadow: the finds parsed apart, the answer itself as drop's")
    check(keep.items == [] and [i["part"] for i in keep.wrong_end_items] == ["Left Tail Lamp"]
          and keep.note.endswith("kept as suggestions"), "keep: the same, and the note says so")
    right = zone_read.parse_answer("rear", raw.replace('"front"', '"rear"'), labels, wrong_end="keep")
    check([i["part"] for i in right.items] == ["Left Tail Lamp"] and right.wrong_end_items == [],
          "a zone that sees its own end: as before, whatever the mode")
    suggestion = zones.wrong_end_suggestions({"zone:rear": {"seen": "front", "items": [
        {**shadow.wrong_end_items[0], "photo_reference": "@x"}]}})[0]
    check(suggestion["source"] == "zone" and suggestion["found_by"] == ["zone:rear"] and suggestion["seen_in"] == ["@x"]
          and suggestion["wrong_end_note"] == "The rear read thought these photos show the front.",
          "a kept find is a zone suggestion with the note", suggestion)
    dumped = SecondLookItem.model_validate({"part": "Boot Lid"}).model_dump()
    check("wrong_end_note" not in dumped and SecondLookItem.model_validate(suggestion).model_dump()["wrong_end_note"],
          "the note is left out of a dump while None (drafts as before), kept when set")


def e11_end_to_end() -> None:
    results = {}
    for mode in ("drop", "shadow", "keep"):
        with a_case(dict(ZONES, intake_zone_wrong_end=mode), {"zone": wrong_end_zone}) as (client, rid, pids, uid, state):
            session = t.db_row(IntakeSession, rid)
            results[mode] = {"state": state["stage"], "data": t.db_row(Report, rid).data, "ai_draft": session.ai_draft,
                             "rear": {e["zone"]: e for e in zone_entries(rid)}.get("rear") or {}, "pids": pids,
                             "rid": rid, "uid": uid}

    def scrub(r):
        ids = {r["rid"]: "<rid>", r["uid"]: "<uid>", **{pid: f"<p{i}>" for i, pid in enumerate(r["pids"])}}
        return t.normalised({"data": r["data"], "ai_draft": r["ai_draft"]}, ids)
    check(scrub(results["shadow"]) == scrub(results["drop"]),
          "shadow: the stored data and the AI draft as drop's", _first_diff(scrub(results["shadow"]), scrub(results["drop"])))
    check(results["shadow"]["rear"].get("wrong_end") == {"mode": "shadow", "parts": ["Left Tail Lamp", "Rear Bumper"]}
          and "wrong_end" not in results["drop"]["rear"],
          "shadow: the rear zone's run-log entry says what keep would add (drop: nothing new)", results["shadow"]["rear"])
    items = (results["keep"]["data"].get("second_look") or {}).get("items") or []
    kept = [it for it in items if it.get("wrong_end_note")]
    listed = [it["part"] for it in results["keep"]["data"]["damage_summary"]]
    check([it["part"] for it in kept] == ["Left Tail Lamp"]
          and kept[0]["wrong_end_note"] == "The rear read thought these photos show the front."
          and kept[0]["source"] == "zone" and kept[0]["found_by"] == ["zone:rear"],
          "keep: the tail lamp is a suggestion with the note (the Rear Bumper, already listed, isn't)", kept)
    check(listed == [it["part"] for it in results["drop"]["data"]["damage_summary"]] and "Left Tail Lamp" not in listed,
          "keep: never on the list; the list as drop's", listed)
    check(not any(it.get("wrong_end_note") for it in (results["drop"]["data"].get("second_look") or {}).get("items") or []),
          "drop: no such suggestion")


# ------------------------------------------------------------- 5. the benchmark


def bench_section() -> None:
    import benchmark_intake as bench

    def model_settings(*argv):
        args = bench.parse_args(["--cases", "x", "--key", "y", "--out", "z", *argv])
        return bench.model_settings(args)

    def exits(*argv) -> bool:
        try:
            model_settings(*argv)
        except SystemExit:
            return True
        return False

    got = model_settings("--read-mode", "zones", "--zone-wrong-end", "keep", "--side-geometry", "tiebreak",
                         "--image-resolution-main-read", "*=medium", "--max-read", "64", "--links", "on",
                         "--link-check", "lite", "--link-check-max-calls", "3", "--link-models",
                         "gemini-3.1-flash-lite,gemini-3.5-flash-lite", "--link-skill", "L2")
    want = {"intake_zone_wrong_end": "keep", "intake_side_geometry": "tiebreak",
            "gemini_image_resolution_main_read": "*=medium", "intake_link_check_max_calls": 3,
            "gemini_link_chain": "gemini-3.1-flash-lite,gemini-3.5-flash-lite", "intake_link_skill": "L2"}
    check(all(got.get(k) == v for k, v in want.items()), "the flags set their settings", got)
    refused = {flag: exits(*argv) for flag, argv in {
        "--zone-wrong-end without zones": ("--zone-wrong-end", "keep"),
        "--link-check-max-calls without the check": ("--link-check-max-calls", "3"),
        "--link-models without the check": ("--link-models", "a,b"),
        "--side-geometry typo": ("--side-geometry", "maybe"),
        "--max-read 65": ("--max-read", "65"),
        "--image-resolution-main-read bad kind": ("--image-resolution-main-read", "dent=low"),
    }.items()}
    check(all(refused.values()), "a flag that would change nothing, or a typo, stops the run", refused)
    with t.Settings(intake_max_read_photos=40, gemini_image_resolution_main_read="*=medium", intake_side_geometry="vote",
                    intake_read_mode="zones", intake_zone_wrong_end="shadow"):
        on = bench.ai_settings(settings)
    off = bench.ai_settings(settings)
    check(on.get("intake_max_read_photos") == 40 and on.get("image_resolution_main_read") == "*=medium"
          and on.get("intake_side_geometry") == "vote" and on.get("intake_zone_wrong_end") == "shadow"
          and not {"intake_max_read_photos", "image_resolution_main_read", "intake_side_geometry",
                   "intake_zone_wrong_end"} & set(off), "ai_settings: Wave 2's only while on", on)
    args = vars(bench.parse_args(["--cases", "x", "--key", "y", "--out", "z"]))
    check(all(args[k] == v for k, v in bench.WAVE2_ARGS.items()), "the flags' defaults are left out of a file's args")
    with t.Settings(intake_side_geometry="vote"):
        fields = bench._sort_fields()
    check(fields[-1] == "geometry" and "geometry" not in bench._sort_fields(),
          "the sort cache keeps the geometry while the sort answers it", fields)
    photos = [{"kind": k, "quality": q, "part": None, "read": False, "damage": False}
              for k, q in (("damage", "good"), (None, None), ("interior", "good"), ("other", "good"),
                           ("other", "unusable"), ("third_party", "good"), ("scene", "good"))]
    counts = bench.sorter_counts(photos)
    check(counts == {"unusable": 1, "interior": 1, "third_party": 1, "other": 2, "unsorted": 1},
          "E10: the sorter's set-aside photos counted per run", counts)
    metrics = bench.run_metrics({"parts": bench.score_parts([], []), "sorted_photos": photos})
    check(metrics.get("sort_other") == 2 and metrics.get("sort_unsorted") == 1, "... and in the run's metrics")
    rows = [{"truth": "real", "link_check": {"verdict": "seen"}}, {"truth": "real", "link_check": {"verdict": "no"}},
            {"truth": "extra", "link_check": {"verdict": "no"}}, {"truth": "wrong_side", "link_check": {"verdict": "likely"}},
            {"truth": "extra", "link_check": None}]
    lcv = bench.link_check_metrics(rows)
    check(lcv["lcv_answered"] == 4 and lcv["lcv_unanswered"] == 1 and lcv["lcv_real_seen"] == 1
          and lcv["lcv_real_no"] == 1 and lcv["lcv_wrong_no"] == 1 and lcv["lcv_wrong_likely"] == 1
          and bench.link_check_metrics([{"truth": "real"}]) == {},
          "E9: the linked check's verdicts by truth (none answered: no metrics)", lcv)
    check(tuple(bench.LINK_VERDICTS) == tuple(link_check.VERDICTS), "LINK_VERDICTS are the app's")
    m = bench.run_metrics({"parts": bench.score_parts([], []), "zones": {"zones": [
        {"zone": "rear", "side_seen": "front", "wrong_end": {"mode": "keep", "parts": ["A", "B"]}},
        {"zone": "left", "side_seen": "right", "geometry": {"model": "unclear"}}]},
        "side_geometry": [{"part": "x"}], "suggestions": [{"part": "A", "wrong_end_note": "n", "truth": "real"}]})
    check(m.get("zone_other_end") == 1 and m.get("wrong_end_items") == 2 and m.get("geo_zone_changed") == 1
          and m.get("geo_side_changed") == 1 and m.get("wrong_end_sugg") == 1 and m.get("wrong_end_sugg_real") == 1,
          "E11 / E7 metrics", m)


# ------------------------------------------------------------- 6. replay_link_check


def _stored_result(folder: Path) -> tuple[Path, Path]:
    """A synthetic stored benchmark result (one case, two repetitions) and
    its key: the fake's 10-photo case, the main read's list, the zone
    read's tail lamp, the rear zone's intact list."""
    case = folder / "cases" / "case-a"
    case.mkdir(parents=True)
    for i in range(len(t.CASE)):
        (case / f"IMG_{i:04d}.jpg").write_bytes(t.jpeg(i))
    sorted_photos = [{"n": i + 1, "file": f"IMG_{i:04d}.jpg", "kind": k, "area": a, "quality": "good", "damage": d,
                      "part": p, "read": True, "note": f"photo P{i + 1:02d}"} for i, (k, a, p, d) in enumerate(t.CASE)]
    drafted = [
        {"part": "Rear Bumper", "photo_reference": "P03", "ai_confidence": "High", "condition_tokens": ["Cracked", "Crumpled"],
         "severity": "Severe", "remarks": "Replace", "found_by": ["overall", "zone:rear"], "seen_in": ["P03"],
         "evidence": [{"photo": "P03", "by": "overall"}]},
        {"part": "Boot Lid", "photo_reference": "P05", "ai_confidence": "High", "condition_tokens": ["Dented"],
         "severity": "Moderate", "found_by": ["overall"], "seen_in": ["P05"], "evidence": [{"photo": "P05", "by": "overall"}]},
        {"part": "Left Rear Door", "photo_reference": "P07", "ai_confidence": "Medium", "condition_tokens": ["Bent"],
         "severity": "Severe", "remarks": "Replace", "found_by": ["overall"], "seen_in": ["P07"], "evidence": []},
        {"part": "Front Bumper", "photo_reference": "P02", "ai_confidence": "Medium", "condition_tokens": ["Crumpled"],
         "severity": "Severe", "remarks": "Replace", "found_by": ["overall"], "seen_in": ["P02"], "evidence": []},
    ]
    suggestions = [{"key": "Left Tail Lamp", "part": "Left Tail Lamp", "source": "zone", "confidence": "Medium",
                    "found_by": ["zone:rear"], "photo_reference": "P04", "seen_in": ["P04"], "reason": "cracked",
                    "evidence": [{"photo": "P04", "by": "zone:rear"}], "linked_from": ["Rear Bumper"], "truth": "real"}]
    run = {"case": "Case A", "parts": {}, "drafted_parts": [d["part"] for d in drafted], "drafted_items": drafted,
           "sorted_photos": sorted_photos, "suggestions": suggestions, "link_suggestions": [],
           "zones": {"status": "ok", "zones": [{"zone": "rear", "status": "ok", "intact": ["Parking Sensor"]}]}}
    doc = {"label": "stored", "ai_settings": {"intake_links": "on", "intake_links_max": 12,
                                              "intake_links_types": "attached,opens_with,behind,crush",
                                              "intake_links_side": "firm", "intake_links_unknown_hit": "medium",
                                              "intake_links_intact": "demote"},
           "runs": [dict(copy.deepcopy(run), rep=1), dict(copy.deepcopy(run), rep=2),
                    {"case": "Case A", "rep": 3, "error": "RuntimeError: busy"}]}
    key = {"cases": [{"name": "Case A", "folder": "case-a", "vehicle": {}, "parts": [
        {"part": "Rear Bumper"}, {"part": "Boot Lid"}, {"part": "Left Rear Door"}, {"part": "Left Tail Lamp"},
        {"part": "Rear Bumper Beam", "hidden": True}, {"part": "Left Rear Bumper Reflector"}]}]}
    for r in doc["runs"][:2]:
        r["parts"] = {"found": 3}
    (folder / "stored.json").write_text(json.dumps(doc), encoding="utf-8")
    (folder / "key.json").write_text(json.dumps(key), encoding="utf-8")
    return folder / "stored.json", folder / "key.json"


def replay_section() -> None:
    sys.path.insert(0, str(HERE / "bench"))
    import benchmark_intake as bench
    import compare
    import gate
    import replay_link_check as replay

    folder = Path(tempfile.mkdtemp(prefix="wave2-replay-"))
    stored, key = _stored_result(folder)
    slots: list = []
    real_acquire = structured.acquire

    def counting_acquire(key=None, *a, **kw):
        slots.append(key)
        return 0.0
    structured.acquire = counting_acquire
    os.environ["BENCHMARK_SCRATCH"] = "1"
    keep = {k: getattr(settings, k) for k in ("intake_links", "intake_link_check", "intake_link_check_max_calls",
                                              "intake_link_skill", "second_look_enabled", "intake_check_mode",
                                              "intake_links_stripdown_cap", "intake_links_order")}
    try:
        t.REQUESTS.clear()
        out = folder / "base.json"
        code = replay.main([str(stored), "--cases", str(folder / "cases"), "--key", str(key), "--out", str(out),
                            "--label", "lc-base"])
        doc = json.loads(out.read_text(encoding="utf-8"))
        reqs = list(t.REQUESTS)
        base_slots = len(slots)
        for k, v in keep.items():
            setattr(settings, k, v)
        t.REQUESTS.clear()
        out3 = folder / "e9a.json"
        replay.main([str(stored), "--cases", str(folder / "cases"), "--key", str(key), "--out", str(out3),
                     "--link-check-max-calls", "3", "--link-skill", "L2", "--label", "lc-e9"])
        doc3 = json.loads(out3.read_text(encoding="utf-8"))
        reqs3 = list(t.REQUESTS)
        # The stored run's own linked parts as the replay rebuilt them: a
        # second replay finds them the same (links_match).
        for k, v in keep.items():
            setattr(settings, k, v)
        again = json.loads(stored.read_text(encoding="utf-8"))
        for r in again["runs"][:2]:
            r["link_suggestions"] = [{"key": row["key"], "part": row["part"]} for row in doc["runs"][0]["link_suggestions"]]
        (folder / "stored2.json").write_text(json.dumps(again), encoding="utf-8")
        replay.main([str(folder / "stored2.json"), "--cases", str(folder / "cases"), "--key", str(key),
                     "--out", str(folder / "again.json"), "--rep", "1"])
        doc_again = json.loads((folder / "again.json").read_text(encoding="utf-8"))
    finally:
        structured.acquire = real_acquire
        os.environ.pop("BENCHMARK_SCRATCH", None)
        for k, v in keep.items():
            setattr(settings, k, v)
    kinds_seen = Counter(t.kind_of(r) for r in reqs)
    check(code == 0 and doc["kind"] == "replay_link_check" and len(doc["runs"]) == 2,
          "the replay ran the 2 stored runs that scored (the errored one skipped)", (code, len(doc.get("runs") or [])))
    check(set(kinds_seen) == {"link"} and kinds_seen["link"] >= 2,
          "only the linked check's calls were made (no sort, no read, no zone read)", dict(kinds_seen))
    check(base_slots == len(reqs) and base_slots > 0, "each request took a rate-limiter slot (call_structured's acquire)",
          (base_slots, len(reqs)))
    first = reqs[0]["input"][0]["text"]
    check(first.startswith("Area: ") and "Candidates to judge" in first
          and reqs[0]["system_instruction"].startswith(link_check.SYSTEM_PROMPT),
          "production's request: link_check.build_input and the L1 skill", first[:120])
    run = doc["runs"][0]
    links = run["links"]
    check(links["damage_list_identical"] and doc["damage_list_identical"]
          and run["drafted_parts"] == ["Rear Bumper", "Boot Lid", "Left Rear Door", "Front Bumper"],
          "the damage list untouched (asserted per run)")
    check(links["shown"] >= 2 and all(r.get("truth") for r in run["link_suggestions"])
          and any((r.get("link_check") or {}).get("verdict") for r in run["link_suggestions"]),
          "linked parts built by production's _store and answered, each with its truth", links["shown"])
    beam = next((r for r in run["link_suggestions"] if r["part"] == "Rear Bumper Beam"), None)
    check(beam and beam["link_check"]["verdict"] == "strip_down" and beam["truth"] == "real" and beam["bucket"] == "strip_down",
          "the beam: the fake's strip_down verdict, bucketed, real by the key", beam)
    check((run["link_run"] or {}).get("status") == "ok" and run["calls"].get("link") == (run["link_run"] or {}).get("calls"),
          "the step's CheckRun and the call accounting agree", (run["link_run"], run["calls"]))
    check(doc["summary"]["total"].get("lcv_answered") and "lcv_real_strip_down" in doc["summary"]["total"],
          "the summary carries the verdicts by truth (lcv_*)")
    check(not links["links_match"] and links["links_stored"] == 0, "links_match says the stored run had no linked parts")
    check(len(doc_again["runs"]) == 1 and doc_again["links_match"] and doc_again["runs"][0]["links"]["links_match"],
          "... and true when the stored run's linked parts are the ones rebuilt (--rep 1: one run)")
    zone_row = next((r for r in run["suggestions"] if r["part"] == "Left Tail Lamp"), None)
    check(zone_row and zone_row.get("source") == "zone", "the zone suggestion is replayed as the reading stored it")
    # The arms.
    areas3 = [r["input"][0]["text"].splitlines()[0] for r in reqs3]
    calls = [(r["link_run"] or {}).get("calls", 0) for r in doc["runs"]]
    calls3 = [(r["link_run"] or {}).get("calls", 0) for r in doc3["runs"]]
    check(all(c <= 2 for c in calls) and all(3 >= c3 >= c for c, c3 in zip(calls, calls3)) and sum(calls3) > sum(calls)
          and doc3["ai_settings"].get("intake_link_check_max_calls") == 3
          and doc["ai_settings"].get("intake_link_check_max_calls") == 2,
          "E9a: --link-check-max-calls 3 checks a third area (the base 2), recorded", (calls, calls3, areas3))
    check(all(link_check.skill_text("rear", "L2") in r["system_instruction"] for r in reqs3 if "rear" in
              r["input"][0]["text"].splitlines()[0]) and doc3["ai_settings"].get("intake_link_skill") == "L2",
          "E9b: --link-skill L2 sends the L2 skill files", areas3)
    # compare.py and gate.py read it.
    a, b = compare.load(out), compare.load(out3)
    rows = compare.compare_metrics(a["summary"]["total"], b["summary"]["total"], True)
    check(any(r["metric"] == "lcv_answered" for r in rows) and any(r["metric"] == "link_shown" for r in rows),
          "compare.py reads the replay like a result file")
    lines, passed = gate.suggestions_report(out, json.loads(out.read_text(encoding="utf-8")), variant=None,
                                            limits=dict(gate.SUGGESTION_LIMITS), names=False, baseline=None, key=None)
    g5 = [ln for ln in lines if "G5" in ln]
    check(g5 and "ok" in g5[0] and "linked-check replay" in lines[0], "gate.py --suggestions reads it; G5 from the "
                                                                         "replay's own assertion", g5 + lines[:1])
    # Refusals.
    try:
        bad = json.loads(stored.read_text(encoding="utf-8"))
        bad["ai_settings"]["intake_links"] = "off"
        (folder / "nolinks.json").write_text(json.dumps(bad), encoding="utf-8")
        os.environ["BENCHMARK_SCRATCH"] = "1"
        replay.main([str(folder / "nolinks.json"), "--cases", str(folder / "cases"), "--key", str(key),
                     "--out", str(folder / "x.json")])
        refused = False
    except SystemExit:
        refused = True
    finally:
        os.environ.pop("BENCHMARK_SCRATCH", None)
    check(refused, "a stored file run without --links is refused")
    os.environ.pop("BENCHMARK_SCRATCH", None)
    try:
        replay.main([str(stored), "--cases", str(folder / "cases"), "--key", str(key), "--out", str(folder / "y.json")])
        refused = False
    except SystemExit:
        refused = True
    check(refused, "without BENCHMARK_SCRATCH (a scratch database) it refuses")
    del bench


def main() -> int:
    t.init_db()
    for name, fn in (
        ("0. settings: defaults and compose", settings_section),
        (f"1. the defaults: byte for byte as fix/enh-backend {BASE_REF}", base_equal),
        ("2. E12: more photos for the main read", e12_section),
        ("3a. E7: geometry -> side (units)", e7_units),
        ("3b. E7: the sort's request", e7_sort_request),
        ("3c. E7: end to end", e7_end_to_end),
        ("4a. E11: a wrong-end zone (units)", e11_units),
        ("4b. E11: end to end", e11_end_to_end),
        ("5. the benchmark: flags, settings, counts", bench_section),
        ("6. replay_link_check with a fake client", replay_section),
    ):
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
