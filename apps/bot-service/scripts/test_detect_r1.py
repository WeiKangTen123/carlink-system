"""Detection Round 1 (owner, 25 Nov 2026: "try, see whether there is any
enhancement, then tell me") -- offline: Gemini is the fake of
scripts/test_c10_integration.py, no server, made-up cases only.

  0. settings: intake_sorter_hints (off | shadow | on) and
     intake_intact_rule (old | shadow | strict) default to today's
     behaviour, take their choices, refuse a typo, and are passed through
     docker-compose.prod.yml once, in the block both containers share, at
     config.py's default (and test_compose's allow-list has them); A3
     (intake_read_pick) dropped: app/intake/pipeline.py is master's;
  1. everything at the defaults: the requests and the stored data byte for
     byte as master ae0fde4's -- the full routes (read, decisions,
     autosave, filing, the case page, sign-off) in single mode, zone mode,
     links on and links capped hard, and the reads Round 1 touches: a case
     whose sorter saw parts no read listed (single mode, and zones + links
     with intake_links_intact "drop"), and a 44-photo case with 16 photos of
     one part -- the same scenarios on a git archive of that commit and here;
  2. A1 (units, intake/sorter_hints.py): the note parser on the review's
     M1 notes (negation, the first part mentioned, the other end, whole
     words, locators, sub-parts we have no name for), what the sorter saw
     (never another vehicle, a document, the scene, an excluded or
     unusable photo, or one with no damage seen), the side-less compare,
     one per part on its best photo, the cap, the skips (listed,
     suggested, dismissed), the box through evidence.finish (M6);
  3. A1 end to end: "on" shows them (source "sorter", found_by
     "sorter_seen", the firm's names, the sorter's evidence; a person adds
     one; one dismissed stays away after a re-read); "shadow" stores them
     on the AI draft only -- the case's data as "off"'s -- relabelled at
     filing and counted at sign-off (M7);
  4. A2: strict keeps the parts the sorter saw damaged that a zone read
     called intact -- never across sides or ends (M2) -- a suggestion with
     the sorter's note, intact_in the reads and found_by "sorter_intact"
     (no photo label in any text: M5; renumbered at filing after a
     deletion), at most 4 (L3); "shadow" on the AI draft only; the linked
     parts no longer demoted or dropped for them, and never lost when the
     rule fails (L4); the re-check asks them last (L1); a zone read's find
     cut by the limit is never the sorter's (L2); "old" as before;
  6. scripts/bench/replay_sorter_hints.py on its made-up fixture: the
     command line, its output, the same functions as the pipeline,
     deterministic; links_diff -- the linked parts old and strict, rebuilt
     with second_look.build_links, against the stored ones;
  7. scripts/benchmark_intake.py: --set NAME=VALUE (intake_* / photo_*
     settings, validated; a bad one exits non-zero) reaches the pipeline
     through the benchmark's own runner and is recorded in the result's
     ai_settings; a result keeps the sorter's suggestions (and shadow
     list), counts them apart, prints them; gate.py reads such files.

    python scripts/test_detect_r1.py
    python scripts/test_detect_r1.py --probe OUT.json   (section 1's
        scenarios alone; C10_TEST_ROOT=<a bot-service checkout> runs them
        on that tree, as section 1 does with master's)
"""
import contextlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(1, str(HERE))
R1_ENV = ("INTAKE_SORTER_HINTS", "INTAKE_INTACT_RULE", "INTAKE_READ_PICK")
for _name in R1_ENV + ("INTAKE_MAX_READ_PHOTOS", "GEMINI_IMAGE_RESOLUTION_MAIN_READ", "INTAKE_SIDE_GEOMETRY",
                       "INTAKE_ZONE_WRONG_END", "INTAKE_LINK_CHECK_MAX_CALLS", "GEMINI_IMAGE_RESOLUTION",
                       "GEMINI_THINKING_LEVEL_SORT", "GEMINI_TRIAGE_MODEL_CHAIN", "GEMINI_LINK_CHAIN",
                       "INTAKE_MAIN_READ_FALLBACK", "INTAKE_KEEP_CUT", "INTAKE_KEEP_OTHER_SIDE",
                       "INTAKE_LINK_HIDDEN_NO",
                       "INTAKE_SORT_RETRY_SKIPPED", "INTAKE_PARALLEL_READS", "INTAKE_LINK_CHECK_PARALLEL",
                       "INTAKE_SORT_WORKERS", "INTAKE_KEEP_SORT_LABEL", "INTAKE_SORT_RESCUE", "INTAKE_CAR_MAP",
                       "INTAKE_LINKS_ORDER", "INTAKE_LINKS_ASSEMBLY_MAX", "INTAKE_LINKS_STRIPDOWN_CAP",
                       "INTAKE_LINKS_STRIPDOWN_MAX", "INTAKE_ASSEMBLY_WALK", "INTAKE_READ_PACKS", "INTAKE_ZONE_PACKS",
                       "INTAKE_SORT_PROMPT", "INTAKE_LINK_SKILL", "INTAKE_FIRM_WORDS", "BENCHMARK_SCRATCH"):
    os.environ.pop(_name, None)

# The fake Gemini, the scratch database and the routes' helpers (C10_TEST_ROOT
# picks the tree the app is imported from).
import test_c10_integration as t  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.intake.photos import ordered_photos  # noqa: E402
from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import IntakeSession, Report, User  # noqa: E402
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
BASE_REF = "ae0fde4"  # origin/master this branch is on (PR #57)

# --------------------------------------------------------------- the fakes' cases

# A rear hit whose sorter saw more than the reads list. The fake main read
# lists Rear Bumper (P03), Boot Lid (P05) and Left Rear Door (P07); the fake
# rear zone read finds the bumper and the left tail lamp (P04) and calls the
# Parking Sensor and the Rear Windscreen intact.
SORTER_ROWS = [
    ("log_card", None, None, False), ("overview", "rear", None, False), ("damage", "rear", "Rear Bumper", True),
    ("damage", "rear", "Left Tail Lamp", True), ("damage", "rear", "Boot Lid", True), ("overview", "left", None, False),
    ("damage", "left", "Left Rear Door", True), ("damage", "rear", "Rear Bumper Reflector (side undetermined)", True),
    ("damage", "rear", "Rear Windscreen", True), ("third_party", None, "Front Bumper", True),
    ("damage", "rear", "Parking Sensor", True), ("damage", "front-left", "Left Fog Lamp", True),
    ("damage", "rear", None, True), ("document", None, None, False),
]
SORTER_NOTES = {
    "P03": "Rear bumper cracked near LH reflector", "P05": "Boot lid striker bent", "P09": "Rear windscreen cracked",
    "P10": "Other car's front bumper dented", "P11": "Parking sensor pushed in", "P12": "Left fog lamp cover cracked",
    "P13": "Boot floor board torn",
}
# The sorter's notes the fake sort answers with (else "photo P07").
NOTES: dict[str, str] = {}


def sort_answer(kwargs, labels):
    out = t._sort_answer(labels)
    for photo in out["photos"]:
        photo["note"] = NOTES.get(photo["label"], photo["note"])
    return out


def big_rows() -> list:
    """44 photos: a log card, 3 overviews, 16 near-identical close-ups of one
    part (Rear Bumper, rear), 8 of the boot lid, 4 of a tail lamp, 4 tyres
    and 3 wheel-arch "other" photos showing damage (left / right), an
    overview of the left showing damage, 3 "other" and the scene."""
    rows = [("log_card", None, None, False), ("overview", "rear", None, False), ("overview", "rear-left", None, False),
            ("overview", "rear-right", None, False)]
    rows += [("damage", "rear", "Rear Bumper", True)] * 16
    rows += [("damage", "rear", "Boot Lid", True)] * 8
    rows += [("damage", "rear-left", "Left Tail Lamp", True)] * 4
    rows += [("tyre", "left", None, False), ("tyre", "right", None, False), ("tyre", "left", None, True),
             ("tyre", "right", None, False)]
    rows += [("other", "left", "Left Rear Wheel Arch Trim", True), ("other", "right", None, True),
             ("other", "front-left", None, True)]
    rows += [("overview", "left", None, True)]
    rows += [("other", "rear", None, False)] * 3 + [("scene", None, None, False)]
    return rows


@contextlib.contextmanager
def fakes(answers: dict | None = None, rows: list | None = None, notes: dict | None = None):
    """The fake's answers, case photos (the whole list) and notes, for the block."""
    old_answers, old_case, old_notes = dict(t.ANSWERS), list(t.CASE), dict(NOTES)
    t.ANSWERS.update(answers or {})
    if rows is not None:
        t.CASE[:] = list(rows)
    NOTES.clear()
    NOTES.update(notes or {})
    try:
        yield
    finally:
        t.ANSWERS.clear()
        t.ANSWERS.update(old_answers)
        t.CASE[:] = old_case
        NOTES.clear()
        NOTES.update(old_notes)


@contextlib.contextmanager
def a_case(values: dict, answers: dict | None = None, rows: list | None = None, notes: dict | None = None):
    """A new draft read once with these settings and fake answers: yields
    (client, rid, pids, uid, state), the settings still set."""
    t.REQUESTS.clear()
    with t.Settings(**values), fakes(answers, rows, notes), TestClient(t._app()) as client:
        uid = sign_in(client, role="admin", may_sign_off=True)
        rid, pids = t.new_draft(client, len(t.CASE))
        state = t.read(client, rid)
        yield client, rid, pids, uid, state


def photos_of(rid: str) -> list[tuple]:
    db = SessionLocal()
    try:
        return [(p.kind, p.area, p.part, p.shows_damage, p.note) for p in ordered_photos(db, rid)]
    finally:
        db.close()


def look_of(rid: str) -> dict:
    return (t.db_row(Report, rid).data or {}).get("second_look") or {}


SORTED = {"sort": sort_answer}
# Section 1's reads Round 1 touches: (settings, answers, case rows, notes).
READ_SCENARIOS = {
    "sorter_single": ({}, SORTED, SORTER_ROWS, SORTER_NOTES),
    "sorter_links_drop": (dict(LINKS, intake_links_intact="drop", intake_keep_cut="on"), SORTED, SORTER_ROWS,
                          SORTER_NOTES),
    "big_one_part": ({}, {}, big_rows(), {}),
}


def read_scenario(name: str) -> dict:
    """One read-only scenario, normalised: what was asked and what was kept."""
    values, answers, rows, notes = READ_SCENARIOS[name]
    with a_case(values, answers, rows, notes) as (client, rid, pids, uid, state):
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
from app.ai import check as check_step  # noqa: E402
from app.ai import evidence, second_look  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import links, sorter_hints  # noqa: E402
from app.reports import accuracy, filing  # noqa: E402
from app.parts.prefill import item_key  # noqa: E402
from app.reports.models import AiAccuracy, IntakePhoto  # noqa: E402

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


def sorter_items(look: dict) -> list[dict]:
    return [it for it in (look or {}).get("items") or [] if it.get("source") == sorter_hints.SOURCE]


def parts(items) -> list[str]:
    return [it.get("part") for it in items]


def requests_of(ids: dict) -> list[dict]:
    """The block's requests, normalised, in a fixed order (the sort's
    batches run at once)."""
    return sorted((t.normalised(t._request(r), ids) for r in t.REQUESTS), key=lambda r: json.dumps(r, sort_keys=True))


# ------------------------------------------------------------- 0. settings

NEW_SETTINGS = {"intake_sorter_hints": "off", "intake_intact_rule": "old"}
COMPOSE = {"INTAKE_SORTER_HINTS": "off", "INTAKE_INTACT_RULE": "old"}


def settings_section() -> None:
    fresh = config.Settings()
    check({k: getattr(fresh, k) for k in NEW_SETTINGS} == NEW_SETTINGS, "each Round 1 setting defaults to today's",
          {k: getattr(fresh, k) for k in NEW_SETTINGS})
    check({k: getattr(settings, k) for k in NEW_SETTINGS} == NEW_SETTINGS, "and this process runs at them")
    problems = []
    for name, good, want in (("intake_sorter_hints", "Shadow", "shadow"), ("intake_sorter_hints", "ON", "on"),
                             ("intake_intact_rule", "STRICT", "strict"), ("intake_intact_rule", "Shadow", "shadow"),
                             ("intake_intact_rule", " old ", "old")):
        if getattr(config.Settings(**{name: good}), name) != want:
            problems.append((name, good, "not taken"))
    for name, bad in (("intake_sorter_hints", "yes"), ("intake_sorter_hints", "true"), ("intake_intact_rule", "new"),
                      ("intake_intact_rule", "on")):
        try:
            config.Settings(**{name: bad})
            problems.append((name, bad, "a typo accepted"))
        except Exception:
            pass
    for name in NEW_SETTINGS:
        if getattr(config.Settings(**{name: ""}), name) != NEW_SETTINGS[name]:
            problems.append((name, "", "blank is not the default"))
    check(not problems, "each takes its choices (any case), blank is its default, a typo stops the app", problems)
    check((config.SORTER_HINTS, config.INTACT_RULES) == (("off", "shadow", "on"), ("old", "shadow", "strict"))
          and not hasattr(config.settings, "intake_read_pick") and not hasattr(config, "READ_PICKS"),
          "the choices, today's first; A3 (intake_read_pick) dropped")
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
    allow = (REPO / "infra" / "tests" / "test_compose.py").read_text(encoding="utf-8")
    missing = [env for env, default in COMPOSE.items() if f'"{env}": "{default}",' not in allow]
    check(not missing and "INTAKE_READ_PICK" not in allow and "INTAKE_READ_PICK" not in compose,
          "infra/tests/test_compose.py allows each as an added line at its default only (A3's gone from both)",
          missing)
    base = subprocess.run(["git", "-C", str(REPO), "show", f"{BASE_REF}:apps/bot-service/app/intake/pipeline.py"],
                          capture_output=True, text=True, encoding="utf-8")
    here = (ROOT / "app" / "intake" / "pipeline.py").read_text(encoding="utf-8")
    check(base.returncode == 0 and base.stdout.replace("\r\n", "\n") == here.replace("\r\n", "\n"),
          f"A3 dropped: app/intake/pipeline.py is {BASE_REF}'s, byte for byte (the photo choice untouched)")
    check("sorter_suggestions" in filing.AI_DRAFT_PHOTO_LISTS and "sorter" in accuracy.SUGGESTION_SOURCES,
          "filing relabels the shadow list's photos; the AI-accuracy record counts source \"sorter\"")


# ------------------------------------------------------------- 1. the defaults, as master


def base_equal() -> None:
    work = Path(tempfile.mkdtemp(prefix="detect-r1-base-"))
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
        proc = subprocess.run([PY, str(HERE / "test_detect_r1.py"), "--probe", str(out)], capture_output=True,
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
        check(mine["stage"] == "ready", f"{name}: read")
        check(mine == theirs, f"{name}: the requests, the run log, the photos and the stored draft as {BASE_REF}'s",
              _first_diff(mine, theirs))
    here = outs["here"]
    check(not any(it.get("source") == "sorter" for name in READ_SCENARIOS
                  for it in (here[name]["data"].get("second_look") or {}).get("items") or [])
          and not any("sorter_suggestions" in (here[name]["ai_draft"] or {}) for name in READ_SCENARIOS),
          "... no sorter suggestion anywhere at the defaults")
    check(len(here["big_one_part"]["read_photo_ids"] or []) == 32, "... the 44-photo case reads 32 at the defaults")
    import shutil

    shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------- 2. A1 units


def _photo(i: int, kind, area=None, part=None, damage=False, **kw) -> IntakePhoto:
    return IntakePhoto(id=f"p{i}", report_id="r", position=i, path=kw.pop("path", ""), sha256="", kind=kind, area=area,
                       part=part, quality=kw.pop("quality", "good"), shows_damage=damage,
                       excluded=kw.pop("excluded", False), **kw)


def _find(photos, **kw):
    """sorter_hints.find with nothing listed, suggested or dismissed unless given."""
    args = dict(listed=set(), shown=[], dismissed=[], zone_intact={}, hints=True, strict_rule=False)
    args.update(kw)
    return sorter_hints.find(photos, **args)


# (note, the photo's area, its tag) -> the part the note names first. The
# review's M1 cases, each fixed, and the ones that worked before.
NOTE_CASES = {
    # The wording rules as before: a sub-part on a tagged photo, a canonical name word for word.
    ("Damaged left front wheel arch liner", "front-left", None): "Left Front Wheel Arch Liner",
    ("Boot lid striker bent", "rear", "Boot Lid"): "Boot Lid Lock",
    ("Boot floor board torn", "rear", None): "Boot Floor Board",
    ("Wheel arch trim scraped", "front-left", None): "Wheel Arch Trim (side undetermined)",
    ("Fog lamp cover cracked", "front-left", None): "Fog Lamp (side undetermined)",
    ("Left fog lamp cover missing", "front-right", None): "Fog Lamp (side undetermined)",
    ("Left fog lamp cover missing", "front-left", None): "Left Fog Lamp",
    # (d) the locator words: the part before them, a clause that is only a place left out.
    ("Rear bumper cracked near LH reflector", "rear", None): "Rear Bumper",
    ("Front bumper torn at LH fog lamp", "front", None): "Front Bumper",
    ("Rear door dented behind front door", "left", None): "Door (side undetermined)",
    ("Front bumper scratched under headlamp", "front", None): "Front Bumper",
    ("Scratch on front fender from front door", "left", None): "Front Fender (side undetermined)",
    ("Dent over the wheel arch", "left", None): None,
    ("Scuff on bumper corner by the fog lamp", "front", None): None,
    ("Paint transfer between front and rear door", "left", None): None,
    ("Bumper cover adjacent headlamp cracked", "front", None): None,
    ("Under the front bumper, undercover hanging", "front", None): None,
    # (a) negated in its clause: "ok", "fine", "good", "intact", "undamaged", "no" / "not" before it.
    ("Wheel scuffed, tyre ok", "front-left", "Left Front Wheel"): "Wheel (side undetermined)",
    ("Tyre sidewall cut, wheel fine", "front-left", None): "Tyre (side undetermined)",
    ("Bumper scraped, exhaust tip ok", "rear", "Rear Bumper"): None,
    ("Door undamaged, sill scraped", "left", None): "Sill / Rocker Panel (side undetermined)",
    ("No damage to door", "left", None): None,
    ("Front view of the car, no visible damage on bonnet", "front", None): None,
    ("Rear bumper intact; boot lid dented", "rear", None): "Boot Lid",
    ("Bonnet not damaged", "front", None): None,
    ("Headlamp in good condition", "front", None): None,
    # (b) the other end of the car from the photo's area (else its tag's).
    ("Windscreen shattered", "rear", "Rear Windscreen"): None,
    ("Number plate bent", "front", "Front Number Plate"): None,
    ("Left fog lamp cover cracked", "rear", None): None,
    ("Tail lamp cracked", "front", None): None,
    ("Rear bumper scraped", "left", "Left Front Door"): None,
    # (c) the part mentioned first, not the first rule that matches.
    ("Left headlamp broken, right headlamp ok", "front", None): "Left Headlamp",
    ("Front grille cracked and front bumper torn", "front", None): "Front Grille",
    ("Scrape along sill to rear wheel arch", "left", None): "Sill / Rocker Panel (side undetermined)",
    ("Rear bumper cracked, boot lid dented", "rear", None): "Rear Bumper",
    # (e) whole words: "hood" in "neighbourhood" is no bonnet.
    ("Car parked in neighbourhood", "front", None): None,
    ("Hood", "front", None): "Bonnet",
    # A sub-part we have no name for is not its part.
    ("Scratched door handle", "left", None): None,
    ("Number plate lamp broken", "rear", None): None,
    ("Radiator support bent", "front", None): None,
    ("Scratches", "rear", None): None, ("", "rear", None): None, (None, None, None): None,
    ("Rescue sort: damage on the insured car (first sorted: interior)", "rear", None): None,
}


def a1_units() -> None:
    same = sorter_hints.same_component
    check(same("Left Tail Lamp", "Right Tail Lamp") and same("Left Front Door", "Right Front Door")
          and same("Tail Lamp (side undetermined)", "Left Tail Lamp")
          and same("Door (side undetermined)", "Left Rear Door")
          and same("Rear Bumper", "Rear Bumper") and not same("Left Front Door", "Left Rear Door")
          and not same("Left Front Wheel Arch Liner", "Left Rear Wheel Arch Liner")
          and not same("Rear Bumper", "Rear Bumper Beam") and not same(None, "Rear Bumper"),
          "same_component: the part whatever its side, never another part")
    got = {k: sorter_hints.note_part(*k) for k in NOTE_CASES}
    wrong = {k: (v, NOTE_CASES[k]) for k, v in got.items() if v != NOTE_CASES[k]}
    check(not wrong, f"note_part on {len(NOTE_CASES)} notes (M1): the first part mentioned, whole words, up to the "
                     "first locator, never negated, never at the other end, never a sub-part we have no name for",
          wrong)
    two = sorter_hints.note_parts("Rear bumper cracked, boot lid dented", "rear")
    check([p for p, _ in two] == ["Rear Bumper", "Boot Lid"]
          and sorter_hints.note_parts("Front door dented", "right") == [("Door (side undetermined)", "Front Door")],
          "note_parts: every part, in the order mentioned, with its family (which keeps the door's front)", two)
    # The review's table (M1): the photo's own tag listed, so nothing to suggest -- and no wrong part from the note.
    table = {
        "windscreen": _photo(1, "damage", "rear", "Rear Windscreen", True, note="Windscreen shattered"),
        "plate": _photo(1, "damage", "front", "Front Number Plate", True, note="Number plate bent"),
        "wheel": _photo(1, "tyre", "front-left", "Left Front Wheel", True, note="Wheel scuffed, tyre ok"),
        "exhaust": _photo(1, "damage", "rear", "Rear Bumper", True, note="Bumper scraped, exhaust tip ok"),
        "sill": _photo(1, "damage", "left", "Left Sill / Rocker Panel", True, note="Door undamaged, sill scraped"),
    }
    made = {k: parts(_find([p], listed={p.part}).hints) for k, p in table.items()}
    check(all(v == [] for v in made.values()), "the review's five notes on a listed tag suggest nothing "
                                               "(no Front Windscreen, Rear Number Plate, Tyre, Exhaust or Door)", made)
    photos = [
        _photo(0, "log_card"), _photo(1, "damage", "rear", "Rear Bumper", True, note="Rear bumper cracked"),
        _photo(2, "third_party", None, "Front Bumper", True, note="Other car's front bumper dented"),
        _photo(3, "document", None, "Front Grille", True), _photo(4, "scene", None, "Roof", True),
        _photo(5, "damage", "rear", "Boot Lid", True, excluded=True), _photo(6, "damage", "rear", "Roof", True,
                                                                             quality="unusable"),
        _photo(7, "damage", "rear", "Rear End Panel", False), _photo(8, "damage", "rear", "Boot Lid", True,
                                                                     note="Boot lid striker bent"),
        _photo(9, None, None, "Boot Lid Hinge", True),
        _photo(10, "damage", "rear", "Rear Windscreen", True, note="Rear windscreen and tail lamp cracked"),
    ]
    seen = [(s.part, s.photo.id, s.via) for s in sorter_hints.seen_damage(photos)]
    check(seen == [("Rear Bumper", "p1", "part"), ("Boot Lid", "p8", "part"), ("Boot Lid Lock", "p8", "note"),
                   ("Rear Windscreen", "p10", "part"), ("Tail Lamp (side undetermined)", "p10", "note")],
          "seen_damage: tags and a note's first other part, on the insured car's photos showing damage only -- "
          "never another vehicle (its note neither), a document, the scene, an excluded, unusable, damage-free or "
          "unsorted photo", seen)
    many = [_photo(0, "overview", "rear", "Rear Bumper", True, note="Wide shot")]
    many += [_photo(i, "damage", "rear", part, True) for i, part in enumerate(
        ["Left Tail Lamp", "Right Tail Lamp", "Boot Lid Hinge", "Boot Lid Lock", "Rear End Panel", "Rear Number Plate",
         "Left Rear Bumper Reflector", "Boot Floor Board", "Rear Bumper Beam"], start=1)]
    many.append(_photo(10, "damage", "rear", "Rear Bumper", True, quality="blurry", damage_box=[100, 100, 500, 500]))
    many.append(_photo(11, "damage", "rear", "Rear Bumper", True, damage_box=[200, 200, 600, 600]))
    found = _find(many, listed={"Right Rear Bumper Reflector"}, shown=[{"part": "Tail Lamp (side undetermined)"}],
                  dismissed=["Rear Number Plate"])
    check(len(found.hints) == 6 and not found.intact, "at most MAX_HINTS (6) a case", parts(found.hints))
    check(parts(found.hints) == ["Boot Lid Hinge", "Boot Lid Lock", "Rear End Panel", "Boot Floor Board",
                                 "Rear Bumper Beam", "Rear Bumper"],
          "damage close-ups first, in camera order (the overview's bumper on its close-up); the tail lamps "
          "(suggested, without side), the plate (dismissed) and the reflector (listed on the other side) skipped",
          parts(found.hints))
    check(found.skipped == {"suggested": 1, "dismissed": 1, "listed": 1}, "each skipped part counted once, by why",
          found.skipped)
    bumper = found.hints[-1]
    check(bumper["photo_reference"] == "@p11" and bumper["seen_in"] == ["@p11", "@p10", "@p0"]
          and bumper["evidence"][0] == {**bumper["evidence"][0], "photo": "@p11", "box": [200, 200, 600, 600],
                                        "box_kind": "sort", "by": "sort"},
          "one per part on its best photo (sharp before blurry), its other photos in seen_in, the sorter's box as "
          "its evidence (evidence.finish)", {k: bumper[k] for k in ("photo_reference", "seen_in", "evidence")})
    check(all(it["source"] == "sorter" and it["confidence"] == "Low" and it["key"] == item_key({"part": it["part"]})
              and it["covers"] == second_look.covers_of(it["part"]) and it["found_by"] == [sorter_hints.SEEN]
              and "intact_in" not in it for it in found.hints),
          "each a SecondLookItem: source \"sorter\", found_by [\"sorter_seen\"] (A1's mark), Low, its key and covers")
    capped = _find(many, cap=2)
    check(len(capped.hints) == 2 and capped.skipped == {"cap": 7}, "a smaller cap: the rest counted as cut by it, "
          "once per part (the right tail lamp is the left's part)", capped.skipped)
    notes_only = [_photo(0, "damage", "rear", None, True, note="Boot floor board torn"),
                  _photo(1, "interior", "rear", "Boot Side Trim (side undetermined)", True, note="Trim cracked")]
    got = _find(notes_only)
    check(parts(got.hints) == ["Boot Floor Board", "Boot Side Trim (side undetermined)"]
          and got.hints[0]["reason"] == "Boot floor board torn" and got.origin[got.hints[0]["key"]]["via"] == "note",
          "a note's part (its note, cleaned, the reason); an interior photo of the boot counts; the sorter's own "
          "(side undetermined) kept", [(it["part"], it["reason"]) for it in got.hints])
    # M6: the sorter's box through evidence.finish -- round its own part only, never on a raw original, its frame.
    boxed = [_photo(0, "damage", "rear", "Boot Lid", True, note="Boot lid striker bent",
                    damage_box=[100, 100, 400, 400]),
             _photo(1, "damage", "rear", None, True, note="Boot floor board torn", damage_box=[200, 200, 500, 500])]
    got = _find(boxed, listed={"Boot Lid"})
    boxes = {it["part"]: it["evidence"][0]["box"] for it in got.hints}
    check(boxes == {"Boot Lid Lock": None, "Boot Floor Board": [200, 200, 500, 500]},
          "the sorter's box only round its own part: none for a note's part on a tagged photo, the box on an "
          "untagged one", boxes)
    from PIL import Image

    folder = Path(tempfile.mkdtemp(prefix="detect-r1-frames-"))
    image = folder / "p0.jpg"
    Image.new("RGB", (640, 480), (90, 90, 90)).save(image, "JPEG")
    hinge = [_photo(0, "damage", "rear", "Boot Lid Hinge", True, damage_box=[100, 100, 400, 400])]

    def facts(raw: bool) -> dict:
        return {"p0": evidence.PhotoFacts(ai_path=str(image), raw=raw, sort_box=[100, 100, 400, 400],
                                          sort_part="Boot Lid Hinge")}

    framed = _find(hinge, facts=facts(False)).hints[0]["evidence"][0]
    raw = _find(hinge, facts=facts(True)).hints[0]["evidence"][0]
    check(framed["box"] == [100, 100, 400, 400] and (framed["img_w"], framed["img_h"]) == (640, 480)
          and raw["box"] is None and raw["box_kind"] is None,
          "M6: with the photo's facts (pipeline.photo_facts), the box carries its frame; on a raw original "
          "(no preview, EXIF may turn it) there is none", (framed, raw))
    import shutil

    shutil.rmtree(folder, ignore_errors=True)
    off = _find(many, zone_intact={"zone:rear": []}, hints=False)
    check(not off.hints and not off.intact, "nothing with both off")
    check(sorter_hints.hints_mode() == "off" and sorter_hints.intact_rule() == "old" and not sorter_hints.active()
          and not second_look._round1_active(), "at the defaults nothing of it is active")


# ------------------------------------------------------------- 3. A1 end to end

SINGLE_A1 = ["Left Tail Lamp", "Rear Bumper Reflector (side undetermined)", "Rear Windscreen", "Parking Sensor",
             "Left Fog Lamp", "Boot Lid Lock"]


def a1_end_to_end() -> None:
    with a_case({}, SORTED, SORTER_ROWS, SORTER_NOTES) as (client, rid, pids, uid, state):
        off_data = t.db_row(Report, rid).data
        off_ai = t.db_row(IntakeSession, rid).ai_draft
        off_ids = {rid: "<rid>", uid: "<uid>", t.db_row(User, uid).email: "<email>",
                   **{pid: f"<p{i + 1:02d}>" for i, pid in enumerate(pids)}}
        off_requests = requests_of(off_ids)
    check(state["stage"] == "ready" and not sorter_items(off_data.get("second_look"))
          and "sorter_suggestions" not in (off_ai or {}), "off: no sorter suggestion, nothing on the AI draft")

    with a_case(dict(intake_sorter_hints="on"), SORTED, SORTER_ROWS, SORTER_NOTES) as (client, rid, pids, uid, state):
        look = look_of(rid)
        mine = sorter_items(look)
        check(state["stage"] == "ready" and parts(mine) == SINGLE_A1,
              "on: the parts only the sorter saw, damage close-ups first, at most 6 (the boot floor board's note 7th: "
              "cut); never the other car's bumper (P10); the listed ones and their twins left out", parts(mine))
        check(mine[0]["photo_reference"] == f"@{pids[3]}" and mine[-1]["photo_reference"] == f"@{pids[4]}"
              and mine[-1]["reason"] == "Boot lid striker bent" and mine[2]["reason"] == "Rear windscreen cracked"
              and all(it["found_by"] == [sorter_hints.SEEN] for it in mine),
              "each on the sorter's photo, its note the reason (the boot lid's striker from its note), A1's mark",
              [(it["part"], it["photo_reference"], it["reason"], it.get("found_by")) for it in mine])
        check(all(it.get("name") and it.get("covers") and it["evidence"] and it["evidence"][0]["by"] == "sort"
                  for it in mine), "named in the firm's words, with covers and the sorter's evidence",
              [(it.get("name"), it.get("covers")) for it in mine])
        check(state["data"]["second_look"]["items"] == look["items"], "GET /intake shows them in second_look.items")
        r = client.post(f"/intake/{rid}/suggestions", json={"key": mine[2]["key"], "action": "add"})
        added = r.json().get("item") or {}
        check(r.status_code == 200 and added.get("part") == "Rear Windscreen"
              and added.get("added_from") == "suggestion" and added.get("reason") == "Rear windscreen cracked"
              and added.get("photo_reference") == f"@{pids[8]}" and added.get("human_verified") is True,
              "a person adds one: on the list, from the suggestion, on its photo, the sorter's plain note as its "
              "reason", r.text[:400])
        listed_now = [it.get("part") for it in (t.db_row(Report, rid).data or {}).get("damage_summary") or []]
        check("Rear Windscreen" in listed_now, "... and it is on the damage list", listed_now)
        check("sorter_suggestions" not in (t.db_row(IntakeSession, rid).ai_draft or {}), "on: nothing on the AI draft")
        # L8: dismissed, then read again -- never suggested again.
        gone = mine[1]["key"]
        r = client.post(f"/intake/{rid}/suggestions", json={"key": gone, "action": "dismiss"})
        again = client.post(f"/intake/{rid}/process", json={"mode": "read"})
        state = t.wait_done(client, rid)
        look = look_of(rid)
        on_list = [it.get("part") for it in (t.db_row(Report, rid).data or {}).get("damage_summary") or []]
        open_now = [it for it in sorter_items(look) if it["key"] not in (look.get("dismissed") or [])
                    and it["part"] not in on_list]
        check(r.status_code == 200 and again.status_code == 200 and state["stage"] == "ready"
              and gone in (look.get("dismissed") or []) and gone not in [it["key"] for it in open_now]
              and "Rear Windscreen" not in parts(open_now) and "Boot Floor Board" in parts(open_now),
              "a dismissed one stays away after a re-read (the added one too); the next part takes its place",
              (parts(open_now), look.get("dismissed")))

    with a_case(dict(intake_sorter_hints="shadow"), SORTED, SORTER_ROWS, SORTER_NOTES) as (
            client, rid, pids, uid, state):
        data = t.db_row(Report, rid).data
        ai = t.db_row(IntakeSession, rid).ai_draft or {}
        ids = {rid: "<rid>", uid: "<uid>", t.db_row(User, uid).email: "<email>",
               **{pid: f"<p{i + 1:02d}>" for i, pid in enumerate(pids)}}
        check(t.normalised(data, ids) == t.normalised(off_data, off_ids),
              "shadow: the case's data (the list, the suggestions) byte for byte as off's",
              _first_diff(t.normalised(data, ids), t.normalised(off_data, off_ids)))
        check(requests_of(ids) == off_requests,
              "shadow: every request as off's (code only, no call)")
        shadow = ai.get("sorter_suggestions") or []
        check(parts(shadow) == SINGLE_A1 and all(it.get("source") == "sorter" and it.get("name") for it in shadow),
              "shadow: what on shows, kept on the AI draft only (ai_draft[\"sorter_suggestions\"])", parts(shadow))
        rest = {k: v for k, v in ai.items() if k != "sorter_suggestions"}
        check(t.normalised(rest, ids) == t.normalised(off_ai, off_ids), "... the rest of the AI draft as off's")
        state = client.get(f"/intake/{rid}").json()
        r = client.post(f"/intake/{rid}/finalize", json={"data": state["data"]})
        filed = t.db_row(Report, rid)
        refs = [it.get("photo_reference") for it in (filed.ai_draft or {}).get("sorter_suggestions") or []]
        check(r.status_code == 200 and refs and all(isinstance(x, str) and x.startswith("P") for x in refs)
              and not t.refs_in((filed.ai_draft or {}).get("sorter_suggestions")),
              "filing relabels the shadow list's photos to the case's P-numbers", refs)
        code = t.sign_off(client, rid).status_code
        metrics = (t.db_row(AiAccuracy, rid).metrics or {}).get("suggestions") or {}
        ss = metrics.get("sorter_shadow") or {}
        check(code == 200 and ss.get("predicted") == 6 and ss.get("on_final_list") == 0
              and (ss.get("by_rule") or {}).get(sorter_hints.SEEN, {}).get("predicted") == 6,
              "M7: the shadow list counted at sign-off (sorter_shadow: predicted, on the list, by rule)", ss)
    total = accuracy.sum_suggestions([{"suggestions": {"sorter_shadow": ss}}, {"suggestions": {"sorter_shadow": ss}}])
    check((total.get("sorter_shadow") or {}).get("predicted") == 12
          and "sorter_shadow" not in accuracy.sum_suggestions([{"suggestions": {"shown": 1}}]),
          "... and added up over cases on the AI-accuracy page (only once a case has it)", total.get("sorter_shadow"))


# ------------------------------------------------------------- 4. A2

A2_INTACT = {"zone:rear": ["Rear Windscreen", "Left Tail Lamp", "Parking Sensor"],
             "zone:left": ["Left Rear Door", "Left Wing Mirror"], "zone:front": ["Fog Lamp (side undetermined)"]}


def a2_units() -> None:
    photos = [_photo(0, "damage", "rear", "Rear Windscreen", True, note="Rear windscreen cracked"),
              _photo(1, "damage", "rear", "Right Tail Lamp", True),
              _photo(2, "damage", "rear", None, True, note="Left fog lamp cover cracked"),
              _photo(3, "damage", "left", "Door (side undetermined)", True)]
    check(sorter_hints.strict_intact(A2_INTACT, photos)
          == {"zone:rear": ["Left Tail Lamp", "Parking Sensor"], "zone:left": ["Left Wing Mirror"],
              "zone:front": ["Fog Lamp (side undetermined)"]},
          "strict_intact: each read's list without the parts the sorter saw damaged -- never the other side's (the "
          "Left Tail Lamp stays against a damaged Right one), never the other end's (a rear photo's fog lamp note "
          "doesn't touch the front read's fog lamps); the door on a left photo is a left door",
          sorter_hints.strict_intact(A2_INTACT, photos))
    over = sorter_hints.overridden(A2_INTACT, photos)
    check(over == [{"read": "zone:left", "part": "Left Rear Door", "photo": "@p3"},
                   {"read": "zone:rear", "part": "Rear Windscreen", "photo": "@p0"}],
          "overridden: each intact verdict a sorter photo contradicts, with that photo", over)
    # M2: an undetermined door on a RIGHT photo whose note says front: only the right front door.
    door = [_photo(0, "damage", "right", "Door (side undetermined)", True, note="Front door dented")]
    doors = {"zone:left": ["Left Rear Door", "Left Front Door", "Left Sill / Rocker Panel"],
             "zone:right": ["Right Rear Door", "Right Front Door"]}
    check(sorter_hints.strict_intact(doors, door)
          == {"zone:left": ["Left Rear Door", "Left Front Door", "Left Sill / Rocker Panel"],
              "zone:right": ["Right Rear Door"]},
          "M2: a door with no side on a right photo noted 'front door' matches the right FRONT door only -- not the "
          "left ones, not the right rear", sorter_hints.strict_intact(doors, door))
    wheel = [_photo(0, "tyre", "front-left", "Wheel (side undetermined)", True)]
    wheels = {"zone:left": ["Left Front Wheel", "Left Rear Wheel"], "zone:right": ["Right Front Wheel"]}
    check(sorter_hints.strict_intact(wheels, wheel) == {"zone:left": ["Left Rear Wheel"],
                                                        "zone:right": ["Right Front Wheel"]},
          "M2: an undetermined wheel on a front-left photo: the left FRONT wheel only (never across ends or sides)",
          sorter_hints.strict_intact(wheels, wheel))
    check(sorter_hints.strict_intact({"zone:rear": ["Rear Windscreen", None, 4, {"x": 1}], "zone:x": "junk"}, photos)
          == {"zone:rear": []}, "L4: strict_intact leaves out anything not a name (and a read that isn't a list)")
    found = sorter_hints.find(photos, listed={"Left Rear Door"}, shown=[], dismissed=[], zone_intact=A2_INTACT,
                              hints=False, strict_rule=True)
    rows = [(it["part"], it["reason"], it.get("intact_in"), it.get("found_by")) for it in found.intact]
    check(rows == [("Rear Windscreen", "Rear windscreen cracked", ["zone:rear"], [sorter_hints.INTACT])]
          and not found.hints and found.skipped == {"listed": 1},
          "find strict: the contradicted part a suggestion -- the sorter's note its reason, the reads in intact_in, "
          "A2's mark; the door listed already (without side) is not; A1 off adds nothing else", rows)
    check(not any(re.search(r"\bP\d+\b", str(it.get("reason") or "")) for it in found.intact),
          "M5: no photo label in any stored text (the photo is the evidence; filing renumbers it)")
    both = sorter_hints.find(photos, listed=set(), shown=[], dismissed=[], zone_intact=A2_INTACT, hints=True,
                             strict_rule=True, cap=0)
    check(parts(both.intact) == ["Rear Windscreen", "Door (side undetermined)"] and not both.hints
          and both.skipped == {"cap": 1},
          "A2's suggestions in best-photo order, outside A1's cap (cap 0: only A1's tail lamp cut -- the zone "
          "called the LEFT one intact)", (parts(both.intact), both.skipped))
    six = [_photo(i, "damage", "rear", part, True) for i, part in enumerate(
        ["Rear Windscreen", "Parking Sensor", "Rear Number Plate", "Boot Lid Hinge", "Boot Lid Lock",
         "Rear End Panel"])]
    many = sorter_hints.find(six, listed=set(), shown=[], dismissed=[], hints=False, strict_rule=True,
                             zone_intact={"zone:rear": [p.part for p in six]})
    check(len(many.intact) == sorter_hints.MAX_INTACT == 4 and many.skipped == {"intact_cap": 2},
          "L3: at most MAX_INTACT (4) A2 suggestions a case", (parts(many.intact), many.skipped))
    # The linked parts: what "drop" and "demote" do with the zone reads' intact.
    listed = [{"part": "Rear Bumper", "photo_reference": "@p9", "condition_tokens": ["Cracked", "Crumpled"],
               "severity": "Severe", "remarks": "Replace"}]
    pics = [_photo(9, "damage", "rear", "Rear Bumper", True), _photo(10, "damage", "rear", "Parking Sensor", True)]
    raw = {"zone:rear": ["Parking Sensor", "Rear Number Plate"]}
    for how in ("drop", "demote"):
        opts = links.Options(**{**links.options().__dict__, "intact": how})
        old, _ = links.build_all(listed, [], pics, raw, links.TABLE, opts)
        new, _ = links.build_all(listed, [], pics, sorter_hints.strict_intact(raw, pics), links.TABLE, opts)
        sensor_old = next((it for it in old if it["part"] == "Parking Sensor"), None)
        sensor_new = next((it for it in new if it["part"] == "Parking Sensor"), None)
        plate_new = next((it for it in new if it["part"] == "Rear Number Plate"), None)
        want_old = (sensor_old is None if how == "drop"
                    else bool(sensor_old and sensor_old["intact_in"] == ["zone:rear"]))
        want_plate = plate_new is None if how == "drop" else bool(plate_new and plate_new["intact_in"] == ["zone:rear"])
        check(want_old and sensor_new is not None and sensor_new["intact_in"] == [] and sensor_new["bucket"] == "seen"
              and want_plate,
              f"links {how}: old {'drops' if how == 'drop' else 'demotes'} the sensor the sorter saw damaged; strict "
              f"keeps it in its place (seen); the plate the sorter never saw as before",
              (sensor_old, sensor_new and sensor_new["intact_in"], plate_new and plate_new["intact_in"]))
    # L4: a failure in the strict rule never loses the linked parts.
    opts = links.Options(**{**links.options().__dict__, "intact": "drop"})
    plain, _ = second_look.build_links(listed, [], pics, {"zone_intact": raw}, [], None, opts=opts, strict=False)
    real = sorter_hints.strict_intact

    def broken(*_a, **_k):
        raise RuntimeError("made-up failure")

    sorter_hints.strict_intact = broken
    try:
        kept, _ = second_look.build_links(listed, [], pics, {"zone_intact": raw}, [], None, opts=opts, strict=True)
    finally:
        sorter_hints.strict_intact = real
    check([it["part"] for it in kept] == [it["part"] for it in plain] and kept,
          "L4: the strict rule failing falls back to the zone reads' lists -- the linked parts kept, as old's",
          ([it["part"] for it in kept], [it["part"] for it in plain]))
    # L1: the re-check asks the reads' finds, then the linked parts, then the sorter's.
    plan = check_step.plan_asks([{"key": "s", "part": "Rear Windscreen", "source": "sorter"},
                                 {"key": "l", "part": "Rear Bumper Beam", "source": "link"},
                                 {"key": "z", "part": "Left Tail Lamp", "source": "zone"},
                                 {"key": "k", "part": "Boot Lid Hinge", "source": "kit"}], [])
    check(plan.unasked == ["z", "k", "l", "s"], "L1: the sorter's after the linked parts in the re-check's order",
          plan.unasked)


def a2_end_to_end() -> None:
    with a_case(dict(ZONES), SORTED, SORTER_ROWS, SORTER_NOTES) as (client, rid, pids, uid, state):
        old = sorter_items(look_of(rid))
        intact = (t.db_row(IntakeSession, rid).ai_draft or {}).get("zone_intact")
    check(not old and (intact or {}).get("zone:rear") == ["Parking Sensor", "Rear Windscreen"],
          "old: the rear zone read calls the sensor and the windscreen intact; nothing of it is suggested", intact)
    with a_case(dict(ZONES, intake_intact_rule="strict"), SORTED, SORTER_ROWS, SORTER_NOTES) as (
            client, rid, pids, uid, state):
        mine = sorter_items(look_of(rid))
        ai = t.db_row(IntakeSession, rid).ai_draft or {}
        check([(it["part"], it["photo_reference"], it["reason"], it.get("intact_in"), it.get("found_by"))
               for it in mine]
              == [("Rear Windscreen", f"@{pids[8]}", "Rear windscreen cracked", ["zone:rear"], [sorter_hints.INTACT]),
                  ("Parking Sensor", f"@{pids[10]}", "Parking sensor pushed in", ["zone:rear"], [sorter_hints.INTACT])],
              "strict: each part the sorter saw damaged that a zone read called intact is a suggestion: the photo by "
              "id, the sorter's note, the reads in intact_in, A2's mark",
              [(it["part"], it["photo_reference"], it["reason"], it.get("intact_in")) for it in mine])
        check(ai.get("zone_intact") == intact and "sorter_suggestions" not in ai,
              "... the zone reads' own intact lists kept as they said them; no shadow mode: no shadow list")
        # L8 / M5: a photo deleted before filing -- the filed suggestion's photo renumbered, no label in its text.
        deleted = client.delete(f"/intake/{rid}/photos/{pids[1]}")
        state = client.get(f"/intake/{rid}").json()
        done = client.post(f"/intake/{rid}/finalize", json={"data": state["data"]})
        filed = [it for it in ((t.db_row(Report, rid).data or {}).get("second_look") or {}).get("items") or []
                 if it.get("source") == "sorter"]
        check(deleted.status_code == 200 and done.status_code == 200
              and [(it["part"], it["photo_reference"], it.get("intact_in")) for it in filed]
              == [("Rear Windscreen", "P08", ["zone:rear"]), ("Parking Sensor", "P10", ["zone:rear"])]
              and not any(re.search(r"\bP\d+\b", str(it.get("reason") or "")) for it in filed),
              "after a photo is deleted, filing renumbers the suggestion's photo (P09 -> P08); its text names none",
              [(it["part"], it["photo_reference"], it.get("reason")) for it in filed])
    # A2 "shadow": its suggestions on the AI draft only; the linked parts as old's.
    with a_case(dict(ZONES, intake_intact_rule="shadow"), SORTED, SORTER_ROWS, SORTER_NOTES) as (
            client, rid, pids, uid, state):
        shown = sorter_items(look_of(rid))
        shadow = (t.db_row(IntakeSession, rid).ai_draft or {}).get("sorter_suggestions") or []
    check(not shown and parts(shadow) == ["Rear Windscreen", "Parking Sensor"]
          and all(it.get("found_by") == [sorter_hints.INTACT] and it.get("intact_in") == ["zone:rear"]
                  for it in shadow),
          "A2 shadow: nothing shown; its suggestions kept on the AI draft (A2's mark)", (parts(shown), parts(shadow)))
    # With the linked parts on: the sensor is a linked part of the bumper.
    # keep_cut on: a linked part a cap or "intact" cut is kept apart, so it
    # can be found either way.
    values = dict(LINKS, intake_check_mode="off", intake_keep_cut="on")

    def sensor_of(look: dict) -> tuple[dict | None, dict | None]:
        return t.by_part(t.links_of(look), "Parking Sensor"), t.by_part(look.get("cut") or [], "Parking Sensor")

    with a_case(values, SORTED, SORTER_ROWS, SORTER_NOTES) as (client, rid, pids, uid, state):
        shown_old, cut_old = sensor_of(look_of(rid))
        old_mine = sorter_items(look_of(rid))
    with a_case(dict(values, intake_intact_rule="shadow"), SORTED, SORTER_ROWS, SORTER_NOTES) as (
            client, rid, pids, uid, state):
        shown_sh, cut_sh = sensor_of(look_of(rid))
    old_sensor = shown_old or cut_old
    check(not old_mine and old_sensor is not None and old_sensor.get("intact_in") == ["zone:rear"]
          and (shown_old is not None or cut_old.get("cut_reason") == "intact")
          and (shown_sh or cut_sh or {}).get("intact_in") == ["zone:rear"],
          "links, old (and A2 shadow): the linked sensor the sorter saw damaged is demoted for the zone read's intact",
          (shown_old and shown_old.get("intact_in"), cut_old and cut_old.get("cut_reason")))
    with a_case(dict(values, intake_intact_rule="strict"), SORTED, SORTER_ROWS, SORTER_NOTES) as (
            client, rid, pids, uid, state):
        look = look_of(rid)
        shown_new, cut_new = sensor_of(look)
        mine = sorter_items(look)
        others = [it["part"] for it in look.get("items") or [] if it.get("source") != "sorter"]
        order = [it.get("source") for it in look.get("items") or []]
    check(shown_new is not None and not shown_new.get("intact_in") and shown_new.get("bucket") == "seen"
          and cut_new is None,
          "links, strict: the linked sensor is shown in its place (seen), not demoted",
          (shown_new and shown_new.get("intact_in"), shown_new and shown_new.get("bucket"), cut_new))
    check(parts(mine) == ["Rear Windscreen"] and not any(sorter_hints.same_component(it["part"], p)
                                                          for it in mine for p in others)
          and order.index("sorter") > max(i for i, s in enumerate(order) if s == "link"),
          "links, strict: the sensor not suggested twice; the windscreen (no link) is the sorter's suggestion, "
          "after the linked parts", (parts(mine), order))
    with a_case(dict(values, intake_links_intact="drop"), SORTED, SORTER_ROWS, SORTER_NOTES) as (
            client, rid, pids, uid, state):
        dropped, dropped_cut = sensor_of(look_of(rid))
    with a_case(dict(values, intake_links_intact="drop", intake_intact_rule="strict"), SORTED, SORTER_ROWS,
                SORTER_NOTES) as (client, rid, pids, uid, state):
        kept, kept_cut = sensor_of(look_of(rid))
    check(dropped is None and (dropped_cut or {}).get("cut_reason") == "intact" and kept is not None
          and kept_cut is None,
          "links \"drop\": old drops the sensor the sorter saw damaged (cut as \"intact\"), strict keeps it",
          (dropped, dropped_cut and dropped_cut.get("cut_reason"), kept and kept.get("part")))
    # Both on, with links: A2's first, then A1's; none twice. Then signed off:
    # the accuracy record counts each rule apart (M7).
    with a_case(dict(values, intake_intact_rule="strict", intake_sorter_hints="on"), SORTED, SORTER_ROWS,
                SORTER_NOTES) as (client, rid, pids, uid, state):
        look = look_of(rid)
        mine = sorter_items(look)
        others = [it["part"] for it in look.get("items") or [] if it.get("source") != "sorter"]
        listed = [it.get("part") for it in (t.db_row(Report, rid).data or {}).get("damage_summary") or []]
        cut_parts = [it.get("part") for it in look.get("cut") or []]
        client.post(f"/intake/{rid}/suggestions", json={"key": mine[0]["key"], "action": "add"})
        state = client.get(f"/intake/{rid}").json()
        client.post(f"/intake/{rid}/finalize", json={"data": state["data"]})
        signed = t.sign_off(client, rid).status_code
        metrics = (t.db_row(AiAccuracy, rid).metrics or {}).get("suggestions") or {}
    check(mine and mine[0]["part"] == "Rear Windscreen" and mine[0]["found_by"] == [sorter_hints.INTACT]
          and all(it["found_by"] == [sorter_hints.SEEN] for it in mine[1:]) and len(mine) > 1
          and len(mine) <= sorter_hints.MAX_INTACT + sorter_hints.MAX_HINTS
          and not any(sorter_hints.same_component(it["part"], p) for it in mine for p in others + listed)
          and len({it["part"] for it in mine}) == len(mine) and "Front Bumper" not in parts(mine)
          and "Left Fog Lamp" in parts(mine),
          "both, links on: A2's first, A1's after (the fog lamp only the sorter saw); none listed, suggested or "
          "twice; never the other car's", (parts(mine), others))
    check(not any(p in parts(mine) for p in cut_parts), "... and none of them also in the folded cut list",
          cut_parts)
    by_read = metrics.get("by_read") or {}
    check(signed == 200 and (metrics.get("by_source") or {}).get("sorter", {}).get("shown") == len(mine)
          and by_read.get(sorter_hints.INTACT, {}).get("added") == 1
          and by_read.get(sorter_hints.SEEN, {}).get("shown") == len(mine) - 1,
          "M7: signed off, the accuracy record counts the sorter's under source \"sorter\" and each rule apart "
          "(by its mark)", (metrics.get("by_source"), {k: v for k, v in by_read.items() if k.startswith("sorter")}))
    # L2: a part a zone read found and the limit cut is never a sorter card.
    real_max = second_look.MAX_SUGGESTIONS
    second_look.MAX_SUGGESTIONS = 0
    try:
        with a_case(dict(ZONES, intake_sorter_hints="on"), SORTED, SORTER_ROWS, SORTER_NOTES) as (
                client, rid, pids, uid, state):
            mine = sorter_items(look_of(rid))
            zone_reads = (t.db_row(IntakeSession, rid).ai_draft or {}).get("zone_suggestions") or []
    finally:
        second_look.MAX_SUGGESTIONS = real_max
    check("Left Tail Lamp" in parts(zone_reads) and "Left Tail Lamp" not in parts(mine)
          and "Tail Lamp (side undetermined)" not in parts(mine),
          "L2: the tail lamp a zone read found, cut by the suggestion limit, is not re-suggested as the sorter's",
          (parts(zone_reads), parts(mine)))


# ------------------------------------------------------------- 6. the replay tool

REPLAY = HERE / "bench" / "replay_sorter_hints.py"
# A token shaped like a plate (letters and digits: SXX1234A, WXY 1234), a VIN
# (17 letters and digits), a phone number or an e-mail: never in a fixture.
_PLATE = re.compile(r"^[A-Z]{1,3}\d{1,4}[A-Z]{0,2}$")
_VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_PHONE = re.compile(r"\+?\d[\d -]{6,}\d")
_MAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_LABEL = re.compile(r"^P\d{1,3}$")


def private_tokens(obj) -> list[str]:
    """Every plate-, VIN-, phone- or e-mail-shaped token in obj's texts and
    keys (photo labels like P07 aside; two adjacent words read together,
    as a plate is written "WXY 1234")."""
    texts: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                texts.append(str(k))
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            texts.append(o)
    walk(obj)
    found = []
    for text in texts:
        found += _PHONE.findall(text) + _MAIL.findall(text)
        words = [w.strip(".,;:()[]'\"").upper() for w in text.split()]
        pairs = [a + b for a, b in zip(words, words[1:]) if not (_LABEL.match(a) or _LABEL.match(b))]
        for word in words + pairs:
            if _LABEL.match(word):
                continue
            if (_PLATE.match(word) and any(c.isdigit() for c in word)) or _VIN.match(word):
                found.append(word)
    return sorted(set(found))
FIXTURE = HERE / "fixtures" / "sorter_hints_sample.json"


def replay_section() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    check("made-up" in text.lower() and "plate_number" not in text and not private_tokens(json.loads(text)),
          "L8: the fixture is made up -- no plate-, VIN-, phone- or e-mail-shaped token in any of its texts or keys "
          "(photo labels P01.. aside)", private_tokens(json.loads(text)))
    work = Path(tempfile.mkdtemp(prefix="detect-r1-replay-"))
    outs = []
    for n in (1, 2):
        out = work / f"replay{n}.json"
        proc = subprocess.run([PY, str(REPLAY), str(FIXTURE), "--out", str(out)], capture_output=True, text=True,
                              encoding="utf-8", cwd=str(ROOT), env=clean_env(), timeout=300)
        check(proc.returncode == 0 and out.exists(), f"run {n}: the command line works",
              (proc.stdout + proc.stderr)[-800:])
        outs.append(out.read_bytes() if out.exists() else b"")
        if n == 1:
            print("\n".join("         " + ln for ln in proc.stdout.splitlines()[:6]))
    check(outs[0] == outs[1] and outs[0], "deterministic: the same files, the same output byte for byte")
    result = json.loads(outs[0] or b"{}")
    cases = {c["case"]: c for c in result.get("cases") or []}
    rear = cases.get("made-up rear hit") or {}
    check(result.get("tool") == "replay_sorter_hints" and result.get("cap") == 6 and result.get("version") == 3
          and len(cases) == 3,
          "the output: tool, cap, one entry per case", {k: result.get(k) for k in ("tool", "version", "cap")})
    check([(r["part"], r["photo"], r["via"]) for r in rear.get("a1") or []]
          == [("Left Rear Wheel Arch Trim", "P06", "part"), ("Rear Windscreen", "P07", "part"),
              ("Left Fog Lamp", "P10", "part"), ("Boot Lid Lock", "P05", "note"), ("Boot Floor Board", "P09", "note"),
              ("Left Rear Door", "P14", "part")]
          and rear["skipped"]["a1"] == {"cap": 1, "dismissed": 1, "listed": 3, "suggested": 1},
          "a1: the sorter's parts no read listed or suggested (without side), notes read, the other car's never, "
          "at most 6", [(r["part"], r["photo"]) for r in rear.get("a1") or []])
    check([(r["part"], r["photo"], r["intact_in"]) for r in rear.get("a2") or []]
          == [("Rear Windscreen", "P07", ["zone:rear"]), ("Left Rear Door", "P14", ["zone:left"])]
          and rear["a2"][0]["reason"] == "Rear windscreen cracked" and rear["a2"][0]["rule"] == "sorter_intact"
          and rear["a1"][0]["rule"] == "sorter_seen",
          "a2: the parts a zone read called intact that the sorter saw damaged, with the sorter's note and A2's mark",
          [(r["part"], r["reason"]) for r in rear.get("a2") or []])
    check(parts(rear.get("both") or [])[:2] == ["Rear Windscreen", "Left Rear Door"]
          and "Right Sill / Rocker Panel" in parts(rear.get("both") or [])
          and rear.get("a2_overrides") == [{"part": "Left Rear Door", "photo": "P14", "read": "zone:left"},
                                           {"part": "Rear Windscreen", "photo": "P07", "read": "zone:rear"}],
          "both: A2's first and outside A1's cap; the overrides with their photos", rear.get("a2_overrides"))
    check(rear.get("zone_intact") == {"zone:left": ["Left Rear Door", "Left Front Door"],
                                      "zone:rear": ["Rear Windscreen", "Parking Sensor"]},
          "zone_intact from the last read_zones run's zones that answered", rear.get("zone_intact"))
    check(all(set(r) == {"part", "source", "rule", "photo", "reason", "via", "intact_in", "read", "seen_in", "box"}
              and r["source"] == "sorter" for c in cases.values() for key in ("a1", "a2", "both") for r in c[key])
          and rear["a1"][0]["read"] is True and rear["a1"][0]["box"] == [500, 200, 800, 500]
          and t.by_part(rear["a1"], "Boot Lid Lock")["box"] is None,
          "each row: part, source, rule, photo, reason, via, intact_in, read, seen_in, box (the sorter's box only "
          "round "
          "its own part: none for the striker its note names on the boot lid's boxed photo)")
    side = cases.get("made-up side scrape, one read") or {}
    check(parts(side.get("a1") or []) == ["Right Wing Mirror"] and not side.get("a2") and side.get("zone_intact") == {},
          "one read, no zone reads: the door listed on the other side is not suggested; A2 has nothing to act on",
          side)
    check(result.get("totals") == {"a1": 8, "a2": 2, "a2_overrides": 3, "both": 8, "links_gained": 1,
                                   "links_lost": 1}, "the totals", result.get("totals"))
    links_section(cases, work)
    # The same functions as the pipeline: replay_case's rows are find()'s items.
    sys.path.insert(0, str(HERE / "bench"))
    import replay_sorter_hints as replay

    trail = json.loads(text)[0]
    photos = replay.photos_of(trail)
    found = sorter_hints.find(photos, listed=second_look.listed_parts({"damage_summary": trail["damage_summary"]}),
                              shown=trail["second_look"]["items"], dismissed=trail["second_look"]["dismissed"],
                              zone_intact=replay.zone_intact_of(trail), hints=True, strict_rule=False)
    check(parts(found.hints) == parts(rear.get("a1") or []), "replay_case = sorter_hints.find on the trail's facts")
    import shutil

    shutil.rmtree(work, ignore_errors=True)


CAPPED_OLD = ["Rear Number Plate", "Left Rear Bumper Reflector", "Reversing Camera", "Left Boot Lid Lamp",
              "Left Tail Lamp", "Left Rear Wheel Arch Trim", "Left Rear Door Glass", "Left Wing Mirror",
              "Left Front Door Glass", "Boot Lid Lock", "Boot Lid Hinge", "Boot Lid Weatherstrip"]


def links_section(cases: dict, work: Path) -> None:
    """The replay's links_diff: what A2 strict does to the linked parts."""
    crowded = cases.get("made-up crowded rear, links capped") or {}
    diff = crowded.get("links_diff") or {}
    check(diff.get("old", [])[:12] == CAPPED_OLD and "Parking Sensor" not in diff.get("old", [])
          and diff.get("strict", [])[:12] == ["Parking Sensor"] + CAPPED_OLD[:11],
          "links_diff, a capped case: old demotes the sensor a zone read called intact past intake_links_max (12); "
          "strict shows it first (seen) and pushes the 12th past the cap", (diff.get("old"), diff.get("strict")))
    check(diff.get("gained") == ["Parking Sensor"] and diff.get("lost") == ["Boot Lid Weatherstrip"]
          and diff.get("reordered") is False and len(diff.get("old") or []) == len(diff.get("strict") or []) == 22,
          "gained, lost, reordered (the others in the same order); the strip-down block after them in both",
          {k: diff.get(k) for k in ("gained", "lost", "reordered")})
    check(diff.get("check") == {"stored": 22, "matched": 22, "unmatched_rebuilt": 0, "unmatched_stored": 0,
                                "missing_inputs": []},
          "check: the old rebuild matches the trail's stored linked parts exactly", diff.get("check"))
    settings_used = diff.get("settings") or {}
    check({k: settings_used.get(k) for k in ("intake_links", "intake_links_max", "intake_links_intact",
                                             "intake_links_stripdown_cap", "intake_car_map")}
          == {"intake_links": "on", "intake_links_max": 12, "intake_links_intact": "demote",
              "intake_links_stripdown_cap": "out", "intake_car_map": "off"},
          "production's settings by default (links on, 12, demote, strip-down out, car map off)", settings_used)
    check(crowded.get("a2_links") == "rebuilt" and parts(crowded.get("a1") or []) == ["Parking Sensor"]
          and not crowded.get("a2") and crowded["skipped"]["a2"] == {"suggested": 1},
          "with the rebuild matching, A2 is found against the strict linked parts: the sensor is a linked part "
          "then, not a sorter suggestion (A1 alone, against the stored ones, suggests it)",
          (crowded.get("a2_links"), parts(crowded.get("a1") or []), crowded.get("skipped")))
    rear = cases.get("made-up rear hit") or {}
    ck = (rear.get("links_diff") or {}).get("check") or {}
    check(rear.get("a2_links") == "stored" and ck.get("stored") == 1 and ck.get("unmatched_stored") == 1
          and any("damage words" in m for m in ck.get("missing_inputs") or []),
          "a trail without the listed parts' damage words: the check says so, and A2 keeps the stored linked parts",
          ck)
    side = cases.get("made-up side scrape, one read") or {}
    missing = ((side.get("links_diff") or {}).get("check") or {}).get("missing_inputs") or []
    check(any("zone_intact" in m for m in missing) and any("no open linked parts stored" in m for m in missing),
          "... and no zone run, no stored linked parts", missing)
    # The flags: a bigger cap -- the sensor un-demoted, nothing lost.
    out = work / "replay-max30.json"
    proc = subprocess.run([PY, str(REPLAY), str(FIXTURE), "--out", str(out), "--links-max", "30"],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), env=clean_env(), timeout=300)
    wide = {c["case"]: c for c in json.loads(out.read_text(encoding="utf-8"))["cases"]} if out.exists() else {}
    d30 = (wide.get("made-up crowded rear, links capped") or {}).get("links_diff") or {}
    check(proc.returncode == 0 and d30.get("gained") == [] and d30.get("lost") == [] and d30.get("reordered") is True
          and d30.get("strict", [None])[0] == "Parking Sensor"
          and d30.get("settings", {}).get("intake_links_max") == 30,
          "--links-max 30: nothing past the cap -- strict only moves the sensor up (reordered)",
          (proc.stderr[-400:], {k: d30.get(k) for k in ("gained", "lost", "reordered")}))
    bad = subprocess.run([PY, str(REPLAY), str(FIXTURE), "--links-max", "-1"], capture_output=True, text=True,
                         encoding="utf-8", cwd=str(ROOT), env=clean_env(), timeout=300)
    check(bad.returncode == 2 and "intake_links_max" in bad.stderr, "a value config refuses stops the replay (exit 2)",
          (bad.returncode, bad.stderr[-300:]))
    sys.path.insert(0, str(HERE / "bench"))
    import replay_sorter_hints as replay

    trail = json.loads(FIXTURE.read_text(encoding="utf-8"))[2]
    own = replay.settings_for({**trail, "settings": {"intake_links_max": 30, "not_a_link_setting": 1}}, {})
    flagged = replay.settings_for({**trail, "settings": {"intake_links_max": 30}}, {"intake_links_max": 5})
    check(own["intake_links_max"] == 30 and "not_a_link_setting" not in own and flagged["intake_links_max"] == 5,
          "a trail's own settings over production's, a flag over both; other names ignored")
    try:
        replay.settings_for({"settings": {"intake_links_intact": "maybe"}}, {})
        refused = False
    except replay.SettingsError:
        refused = True
    check(refused, "a trail's setting config refuses is an error, not a guess")


# ------------------------------------------------------------- 7. the benchmark

BENCH = HERE / "benchmark_intake.py"
GATE = HERE / "bench" / "gate.py"
R1_KEY_PARTS = [{"part": "Rear Bumper"}, {"part": "Boot Lid"}, {"part": "Left Rear Door"}, {"part": "Rear Windscreen"},
                {"part": "Left Fog Lamp"}, {"part": "Boot Lid Lock"}]
R1_VEHICLE = {"make": "Gammamotors", "model": "Vezel", "plate_number": "TEST-R1", "color": "White",
              "vin": "TESTVIN0000000R1", "engine_number": "TESTENGR1", "date_of_registration": "2019-03-01"}


def _bench_case(folder: Path) -> tuple[dict, Path]:
    """A made-up key case over SORTER_ROWS' photos (coloured rectangles)."""
    case_dir = folder / "made_up_r1"
    case_dir.mkdir(parents=True, exist_ok=True)
    for i in range(len(SORTER_ROWS)):
        (case_dir / f"IMG_{i:04d}.jpg").write_bytes(t.jpeg(i))
    return {"name": "MADE UP R1", "folder": "made_up_r1", "vehicle": R1_VEHICLE, "parts": R1_KEY_PARTS}, folder


def bench_section() -> None:
    import benchmark_intake as bench

    def overrides(*pairs):
        args = bench.parse_args(["--cases", "x", "--key", "y", "--out", "z", *[a for p in pairs for a in ("--set", p)]])
        return bench.setting_overrides(args.set_values, settings)

    got = overrides("intake_sorter_hints=ON", "intake_intact_rule=strict", "intake_zone_wrong_end=Keep",
                    "photo_store_compress=on")
    check(got == {"intake_sorter_hints": "on", "intake_intact_rule": "strict", "intake_zone_wrong_end": "keep",
                  "photo_store_compress": "on"},
          "--set NAME=VALUE: each through its field's own validation (any case, as config writes it)", got)
    check(overrides() == {} and bench.parse_args(["--cases", "x", "--key", "y", "--out", "z"]).set_values is None
          and bench.SET_ARGS == {"set_values": None}, "no --set: nothing, and left out of a result's args")
    refused = {}
    for pair in ("gemini_api_key=x", "database_url=sqlite:///x.db", "intake_no_such_thing=1", "intake_sorter_hints=yes",
                 "intake_max_read_photos=65", "intake_read_anchor=true", "intake_sorter_hints", "=on"):
        try:
            overrides(pair)
            refused[pair] = "accepted"
        except SystemExit as exc:
            refused[pair] = exc.code if exc.code not in (0, None) else "exit 0"
    check(all(isinstance(v, str) and v.startswith("--set") for v in refused.values()),
          "a name not intake_* / photo_* or not a setting, a bad value, a cross-field conflict (the anchor without "
          "read prompt v2), a pair without '=': each stops with a message (exit non-zero)", refused)
    # The benchmark's own runner, in this process (the fake Gemini): the
    # overrides as main applies them reach the pipeline.
    work = Path(tempfile.mkdtemp(prefix="detect-r1-bench-"))
    case, cases_dir = _bench_case(work / "cases")
    bench._app()
    with fakes(SORTED, SORTER_ROWS, SORTER_NOTES), t.Settings(**ZONES), \
            t.Settings(**overrides("intake_sorter_hints=on", "intake_intact_rule=strict")):
        out = bench.run_case(case, cases_dir)
    rows = [r for r in out.get("suggestions") or [] if r.get("source") == "sorter"]
    check("error" not in out and rows and rows[0]["part"] == "Rear Windscreen"
          and rows[0]["reason"] == "Rear windscreen cracked" and rows[0].get("found_by") == ["sorter_intact"]
          and rows[0]["photo_reference"] == "P09" and {r["truth"] for r in rows} <= {"real", "extra", "wrong_side"},
          "--set intake_sorter_hints=on / intake_intact_rule=strict reach the pipeline: the run keeps the sorter's "
          "suggestions (source, reason, photo by label, evidence) and marks them against the key",
          [(r.get("part"), r.get("photo_reference"), r.get("truth")) for r in rows] or out.get("error"))
    check(all(set(r) <= set(bench._SUGGESTION_FIELDS) | {"truth"} for r in rows) and rows[0].get("evidence"),
          "... with the suggestion fields every suggestion keeps")
    m = bench.run_metrics(out)
    real = sum(1 for r in rows if r["truth"] == "real")
    check(m.get("sorter_sugg") == len(rows) and m.get("sorter_sugg_real") == real and m.get("sug_n", 0) >= len(rows)
          and "sug_real" in m and "sug_wrong" in m,
          "counted apart (sorter_sugg, sorter_sugg_real) and among every suggestion", m)
    lines = bench.detail_lines(out) + [bench.summary_line(out)]
    check(any("the sorter's:" in ln for ln in lines), "the printed per-case lines name them", lines)
    with fakes(SORTED, SORTER_ROWS, SORTER_NOTES), t.Settings(**ZONES), \
            t.Settings(**overrides("intake_sorter_hints=shadow")):
        shadow = bench.run_case(case, cases_dir)
    sm = bench.run_metrics(shadow)
    check(not [r for r in shadow.get("suggestions") or [] if r.get("source") == "sorter"]
          and shadow.get("sorter_shadow") and all(r.get("truth") for r in shadow["sorter_shadow"])
          and sm.get("sorter_shadow") == len(shadow["sorter_shadow"]) and "sorter_sugg" not in sm,
          "shadow: none among the suggestions; the AI draft's kept apart (sorter_shadow), marked and counted",
          (shadow.get("sorter_shadow"), sm.get("sorter_shadow")))
    with fakes(SORTED, SORTER_ROWS, SORTER_NOTES), t.Settings(**ZONES):
        plain = bench.run_case(case, cases_dir)
    check("sorter_shadow" not in plain and not [k for k in bench.run_metrics(plain) if k.startswith("sorter_s")],
          "the defaults: no sorter rows, no sorter metrics (a run as before)")
    # gate.py and the summary table on result files that have them.
    docs = {}
    for name, runs in (("base", [plain]), ("cand", [out]), ("shadow", [shadow])):
        doc = {"label": name, "git_ref": None, "args": {}, "models": {}, "ai_settings": {}, "runs": runs,
               "summary": bench.summarize(runs)}
        docs[name] = work / f"{name}.json"
        docs[name].write_text(json.dumps(doc, default=str), encoding="utf-8")
        bench.summary_table(doc["summary"])
    gated = subprocess.run([PY, str(GATE), str(docs["base"]), str(docs["cand"]), str(docs["shadow"])],
                           capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), env=clean_env(),
                           timeout=300)
    sugg = subprocess.run([PY, str(GATE), "--suggestions", str(docs["cand"])], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(ROOT), env=clean_env(), timeout=300)
    check(gated.returncode in (0, 1) and sugg.returncode in (0, 1) and "Traceback" not in gated.stderr + sugg.stderr,
          "gate.py (and --suggestions) read result files with the sorter's suggestions without crashing",
          (gated.returncode, sugg.returncode, (gated.stderr + sugg.stderr)[-600:]))
    # main: the overrides recorded in the result file; a bad one stops it.
    key = work / "key.json"
    key.write_text(json.dumps({"cases": [case]}), encoding="utf-8")
    result = work / "result.json"
    run = subprocess.run([PY, str(BENCH), "--cases", str(cases_dir), "--key", str(key), "--out", str(result),
                          "--scratch", str(work / "scratch"), "--set", "intake_sorter_hints=on",
                          "--set", "intake_intact_rule=strict", "--no-second-look"],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), env=clean_env(), timeout=600)
    doc = json.loads(result.read_text(encoding="utf-8")) if result.exists() else {}
    check(run.returncode == 0 and (doc.get("ai_settings") or {}).get("overrides")
          == {"intake_sorter_hints": "on", "intake_intact_rule": "strict"}
          and (doc.get("args") or {}).get("set_values") == ["intake_sorter_hints=on", "intake_intact_rule=strict"],
          "main: the result file records the overrides (ai_settings.overrides, args.set_values)",
          (run.returncode, (run.stdout + run.stderr)[-800:], doc.get("ai_settings")))
    bad = subprocess.run([PY, str(BENCH), "--cases", str(cases_dir), "--key", str(key), "--out", str(work / "bad.json"),
                          "--scratch", str(work / "scratch-bad"), "--set", "intake_sorter_hints=maybe"],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), env=clean_env(), timeout=600)
    bad_name = subprocess.run([PY, str(BENCH), "--cases", str(cases_dir), "--key", str(key), "--out",
                               str(work / "bad2.json"), "--scratch", str(work / "scratch-bad2"), "--set",
                               "gemini_model_chain=x"], capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
                              env=clean_env(), timeout=600)
    check(bad.returncode != 0 and "--set intake_sorter_hints" in bad.stderr and not (work / "bad.json").exists()
          and bad_name.returncode != 0 and "--set gemini_model_chain" in bad_name.stderr,
          "main: a bad value or a name --set may not change exits non-zero before any case runs",
          (bad.returncode, bad.stderr[-300:], bad_name.returncode, bad_name.stderr[-300:]))
    import shutil

    shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    t.init_db()
    for name, fn in (
        ("0. settings: defaults and compose", settings_section),
        (f"1. the defaults: byte for byte as master {BASE_REF}", base_equal),
        ("2. A1: the sorter's finds (units)", a1_units),
        ("3. A1: on and shadow, end to end", a1_end_to_end),
        ("4a. A2: the intact rule (units, the linked parts)", a2_units),
        ("4b. A2: end to end", a2_end_to_end),
        ("6. replay_sorter_hints.py on its fixture", replay_section),
        ("7. the benchmark: --set, the sorter's suggestions in a result, gate.py", bench_section),
    ):
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
