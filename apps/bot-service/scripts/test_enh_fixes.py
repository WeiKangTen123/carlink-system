"""The detection fixes F1-F7 (plans/2026-11-22-enhance-staging/plan.md
section 2), backend only -- offline: Gemini is the fake of
scripts/test_c10_integration.py, no server, made-up cases only.

  0. settings: each fix's setting defaults to today's behaviour and is passed
     through docker-compose.prod.yml once, in the block both containers
     share, with config's default;
  1. everything at the defaults: the requests and the stored data byte for
     byte as master 5ee5e03's (production) -- the full routes (read,
     decisions, autosave, filing, the case page, sign-off) in single mode,
     zone mode, links on and links capped hard; and the reads the fixes
     touch: a main read every model is busy for, a sort answer that skips
     photos, a placed "(side undetermined)" find on a listed side from
     another photo, a hidden linked part answered "no" -- the same
     scenarios run on a git archive of master and on this tree;
  2. F1 (intake_main_read_fallback zones): the main read fails -> the zone
     reads, the main read once more; failing again, the zone finds are the
     list, all "Check", with the notice; answering the second time, the
     list as a clean read's, zone reads not repeated; zone reads finding
     nothing, single mode, or the setting off: the run fails as before; a
     re-read that works clears the notice;
  3. F2 + F5 (intake_keep_cut on): what the caps cut -- the suggestions'
     16, the linked parts' per-group and overall caps, a zone read's
     intact -- kept in second_look.cut with its reason and counts, items
     unchanged; add / dismiss / undo on a cut one; the re-link's cuts;
     filing relabels them; links.build_all / for_parent with and without;
  4. F3 (intake_keep_other_side on): two tail lamps on two photos stay two
     (an "Other side?" suggestion, not hidden by the listed side), one on
     the same photo is dropped as before; adding it;
  5. F6 (intake_link_hidden_no cannot_tell): a hidden linked part's "no"
     becomes cannot_tell and keeps its place; a visible one's stays "no";
  6. F7 (intake_sort_retry_skipped on): the photos a batch skipped go in one
     more batch (+1 call), and no batch when none was skipped.

    python scripts/test_enh_fixes.py
    python scripts/test_enh_fixes.py --probe OUT.json   (section 1's
        scenarios alone; C10_TEST_ROOT=<a bot-service checkout> runs them
        on that tree, as section 1 does with master's)
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

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(1, str(HERE))
FIX_ENV = ("INTAKE_MAIN_READ_FALLBACK", "INTAKE_KEEP_CUT", "INTAKE_KEEP_OTHER_SIDE", "INTAKE_LINK_HIDDEN_NO",
           "INTAKE_SORT_RETRY_SKIPPED")
for _name in FIX_ENV + ("INTAKE_CAR_MAP", "INTAKE_LINKS_ORDER", "INTAKE_LINKS_ASSEMBLY_MAX",
                        "INTAKE_LINKS_STRIPDOWN_CAP", "INTAKE_LINKS_STRIPDOWN_MAX", "INTAKE_ASSEMBLY_WALK",
                        "INTAKE_READ_PACKS", "INTAKE_ZONE_PACKS", "INTAKE_SORT_PROMPT", "INTAKE_LINK_SKILL"):
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
# The linked parts capped hard: most of the rear bumper's links are cut,
# the zone read's intact Parking Sensor among them.
CAPPED = dict(LINKS, intake_links_max=2, intake_links_stripdown_cap="out", intake_links_stripdown_max=1)
# Section 1's full routes (test_c10_integration.off_scenario).
PROBE_MODES = {
    "single": {},
    "zones": dict(ZONES, intake_check_mode="lite"),
    "links_on": dict(LINKS, intake_check_mode="lite"),
    "links_capped": CAPPED,
}


class Busy(Exception):
    """Google's 503 "busy" (structured.is_overloaded)."""
    status_code = 503

    def __init__(self):
        super().__init__("503 UNAVAILABLE: the model is overloaded, high demand")


def kinds(requests=None) -> Counter:
    return Counter(t.kind_of(r) for r in (t.REQUESTS if requests is None else requests))


def busy_draft(until_zone: bool = False):
    """The main read's answer: busy -- every try, or until a zone read has
    been asked (F1's second try then answers)."""
    def answer(kwargs, labels):
        if until_zone and any(t.kind_of(r) == "zone" for r in t.REQUESTS):
            return t._draft_answer(labels)
        raise Busy()
    return answer


def skipping_sort(skip=("P09", "P10")):
    """The sort's answer: the first call leaves `skip` out (8 of 10: at
    least half, so no failure), later calls answer in full."""
    calls = [0]

    def answer(kwargs, labels):
        calls[0] += 1
        out = t._sort_answer(labels)
        if calls[0] == 1:
            out["photos"] = [p for p in out["photos"] if p["label"] not in skip]
        return out
    return answer


def two_lamps_draft(kwargs, labels):
    """The main read with the Left Tail Lamp (P04) and an undetermined tail
    lamp on P09 -- a rear-left photo (OTHER_SIDE_CASE), so the resolver
    places it on the left, which is listed already."""
    out = t._draft_answer(labels)
    if "P04" in labels:
        out["damage_summary"].append({"part": "Left Tail Lamp", "photo_reference": "P04", "ai_confidence": "Medium",
                                      "condition_tokens": ["Cracked"]})
    if "P09" in labels:
        out["damage_summary"].append({"part": "Tail Lamp (side undetermined)", "photo_reference": "P09",
                                      "ai_confidence": "Medium", "condition_tokens": ["Cracked"]})
    return out


OTHER_SIDE_CASE = {8: ("damage", "rear-left", None, True)}


def beam_no(kwargs, labels):
    """The linked check, answering "no" for the (hidden) Rear Bumper Beam."""
    out = t._link_answer(kwargs, labels)
    text = kwargs["input"][0]["text"]
    import re

    beam = dict(re.findall(r"^- (C\d+): (.+?)(?: -- .*)?$", text, re.M))
    for answer in out["answers"]:
        if beam.get(answer["id"]) == "Rear Bumper Beam":
            answer.update(cue="none_seen", verdict="no", photo=None, box_2d=None, reason="Not seen behind the bumper.")
    return out


def no_zone_damage(kwargs, labels):
    return {"zone_side_seen": "unclear", "damage": [], "checked_intact": []}


class Refused(Exception):
    """A 400 that is not "busy": Google refused the request itself."""
    status_code = 400

    def __init__(self):
        super().__init__("400 INVALID_ARGUMENT: request contains an invalid argument")


def refused_draft(kwargs, labels):
    raise Refused()


def slow_zone(seconds: float):
    """The zone answer, the rear zone's after `seconds`."""
    import time as _time

    def answer(kwargs, labels):
        if "the car's rear" in kwargs["system_instruction"]:
            _time.sleep(seconds)
        return t._zone_answer(kwargs, labels)
    return answer


@contextlib.contextmanager
def fakes(answers: dict | None = None, case: dict | None = None):
    """The fake's answers and case photos, for the block."""
    old_answers, old_case = dict(t.ANSWERS), list(t.CASE)
    t.ANSWERS.update(answers or {})
    for i, row in (case or {}).items():
        t.CASE[i] = row
    try:
        yield
    finally:
        t.ANSWERS.clear()
        t.ANSWERS.update(old_answers)
        t.CASE[:] = old_case


@contextlib.contextmanager
def a_case(values: dict, answers: dict | None = None, case: dict | None = None):
    """A new draft read once with these settings and fake answers: yields
    (client, rid, pids, uid, state), the settings still set."""
    t.REQUESTS.clear()
    with t.Settings(**values), fakes(answers, case), TestClient(t._app()) as client:
        uid = sign_in(client, role="admin", may_sign_off=True)
        rid, pids = t.new_draft(client, len(t.CASE))
        state = t.read(client, rid)
        yield client, rid, pids, uid, state


def photos_of(rid: str) -> list[tuple]:
    db = SessionLocal()
    try:
        return [(p.kind, p.area, p.part, p.shows_damage) for p in ordered_photos(db, rid)]
    finally:
        db.close()


def look_of(rid: str) -> dict:
    return (t.db_row(Report, rid).data or {}).get("second_look") or {}


# Section 1's reads the fixes touch: (settings, answers, case photos).
READ_SCENARIOS = {
    "main_read_busy": (dict(ZONES), {"draft": busy_draft()}, None),
    "main_read_busy_single": ({}, {"draft": busy_draft()}, None),
    "sort_partial": (dict(ZONES), {"sort": skipping_sort()}, None),
    "two_lamps": (dict(ZONES), {"draft": two_lamps_draft}, OTHER_SIDE_CASE),
    "hidden_no": (dict(LINKS), {"link": beam_no}, None),
}


def read_scenario(name: str) -> dict:
    """One read-only scenario, normalised: what was asked and what was kept."""
    values, answers, case = READ_SCENARIOS[name]
    with a_case(values, answers, case) as (client, rid, pids, uid, state):
        ids = {rid: "<rid>", uid: "<uid>", t.db_row(User, uid).email: "<email>",
               **{pid: f"<p{i + 1:02d}>" for i, pid in enumerate(pids)}}
        session = t.db_row(IntakeSession, rid)
        out = {
            "stage": state["stage"], "error": state["error"], "error_code": state["error_code"],
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
        # A fresh sort fake each time (it counts its calls).
        values, answers, case = READ_SCENARIOS[name]
        if "sort" in (answers or {}):
            READ_SCENARIOS[name] = (values, {**answers, "sort": skipping_sort()}, case)
        result[name] = read_scenario(name)
    out.write_text(json.dumps(result, sort_keys=True, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__" and "--probe" in sys.argv:
    sys.exit(probe(Path(sys.argv[sys.argv.index("--probe") + 1])))

# ------------------------------------------------------------- this tree only

from app import config  # noqa: E402
from app.ai import link_check, second_look  # noqa: E402
from app.ai.keys import AiUnavailable  # noqa: E402
from app.config import settings  # noqa: E402
from app.ai import allowance  # noqa: E402
from app.intake import links, pipeline  # noqa: E402
from app.intake import zones as zones_module  # noqa: E402
from app.reports import suggestions  # noqa: E402
from app.reports.schema import SecondLook, SecondLookItem  # noqa: E402

ROOT = HERE.parent
REPO = ROOT.parent.parent
MASTER = "5ee5e03"
PY = sys.executable
L1 = links.TABLE

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
                                                                "SECRETS_DIR"))}
    env.update(PYTHONIOENCODING="utf-8", **extra)
    return env


def _first_diff(a, b, path="$") -> str:
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k}: only in {'here' if k in a else 'master'}"
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

NEW_SETTINGS = {
    "intake_main_read_fallback": "off", "intake_keep_cut": "off", "intake_keep_other_side": "off",
    "intake_link_hidden_no": "keep", "intake_sort_retry_skipped": "off",
}


def settings_section() -> None:
    fresh = config.Settings()
    check({k: getattr(fresh, k) for k in NEW_SETTINGS} == NEW_SETTINGS, "every fix's setting defaults to today's",
          {k: getattr(fresh, k) for k in NEW_SETTINGS})
    check({k: getattr(settings, k) for k in NEW_SETTINGS} == NEW_SETTINGS, "and this process runs at them")
    refused = []
    for name, good in (("intake_main_read_fallback", "zones"), ("intake_keep_cut", "on"),
                       ("intake_keep_other_side", "on"), ("intake_link_hidden_no", "cannot_tell"),
                       ("intake_sort_retry_skipped", "on")):
        if getattr(config.Settings(**{name: good.upper()}), name) != good:
            refused.append((name, "not taken"))
        try:
            config.Settings(**{name: "maybe"})
            refused.append((name, "a typo accepted"))
        except Exception:
            pass
        if getattr(config.Settings(**{name: ""}), name) != NEW_SETTINGS[name]:
            refused.append((name, "blank is not the default"))
    check(not refused, "each takes its choices (any case), blank is its default, a typo stops the app", refused)
    compose = (REPO / "infra" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    anchor, services = compose.index("x-bot-service: &bot-service"), compose.index("\nservices:")
    bad = []
    for name, default in NEW_SETTINGS.items():
        env = name.upper()
        want = f"    - {env}=${{{env}:-{default}}}"
        lines = [ln for ln in compose.splitlines() if ln.startswith(f"    - {env}=")]
        at = compose.find(f"- {env}=")
        if lines != [want] or not anchor < at < services:
            bad.append((env, lines, want))
    check(not bad, "docker-compose.prod.yml: each once, in the shared x-bot-service block, with config's default", bad)


# ------------------------------------------------------------- 1. the defaults, as master


def master_equal() -> None:
    work = Path(tempfile.mkdtemp(prefix="enh-fixes-master-"))
    tar = work / "master.tar"
    made = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", "-o", str(tar), MASTER,
                           "apps/bot-service"], capture_output=True, text=True)
    if not check(made.returncode == 0, f"master {MASTER}'s bot-service from git (archive)", made.stderr[-300:]):
        return
    with tarfile.open(tar) as archive:
        archive.extractall(work / "tree", filter="data")
    master_root = work / "tree" / "apps" / "bot-service"
    outs = {}
    for name, root in (("master", master_root), ("here", ROOT)):
        out = work / f"{name}.json"
        proc = subprocess.run([PY, str(HERE / "test_enh_fixes.py"), "--probe", str(out)], capture_output=True,
                              text=True, encoding="utf-8", cwd=str(root), env=clean_env(C10_TEST_ROOT=str(root)),
                              timeout=1800)
        if not check(proc.returncode == 0 and out.exists(), f"the scenarios ran on {name}'s tree",
                     (proc.stdout + proc.stderr)[-1500:]):
            return
        outs[name] = json.loads(out.read_text(encoding="utf-8"))
        check(Path(outs[name]["_tree"]["app"]).parent == root.resolve(), f"... with {name}'s own app",
              outs[name]["_tree"])
    for mode, values in PROBE_MODES.items():
        mine, theirs = outs["here"][mode], outs["master"][mode]
        check(mine["facts"].get("stage") == "ready" and mine["facts"].get("sign_off") == 200,
              f"{mode}: read, filed and signed off")
        check(mine == theirs, f"{mode} ({', '.join(f'{k}={v}' for k, v in values.items()) or 'the defaults'}): "
                              f"every request and the stored data byte for byte as master {MASTER}'s",
              _first_diff(mine, theirs))
    for name in READ_SCENARIOS:
        mine, theirs = outs["here"][name], outs["master"][name]
        check(mine == theirs, f"{name}: the requests, the run log, the photos and the stored draft as master's",
              _first_diff(mine, theirs))
    here = outs["here"]
    check(here["main_read_busy"]["stage"] == "failed" and here["main_read_busy_single"]["stage"] == "failed",
          "... a busy main read still fails the run at the defaults")
    check(sum(1 for p in here["sort_partial"]["photos"] if p[0] is None) == 2, "... a skipped photo stays unsorted")
    lamps = [it["part"] for it in here["two_lamps"]["data"].get("damage_summary") or []]
    check(lamps.count("Left Tail Lamp") == 1 and "Tail Lamp (side undetermined)" not in lamps
          and "other_side" not in json.dumps(here["two_lamps"]["data"].get("second_look")),
          "... the placed twin from another photo dropped, as before", lamps)
    beam = [it for it in (here["hidden_no"]["data"].get("second_look") or {}).get("items") or []
            if it.get("part") == "Rear Bumper Beam"]
    check(beam and (beam[0].get("link_check") or {}).get("verdict") == "no", "... a hidden part's \"no\" stays no")
    capped =here["links_capped"]["artefacts"]["case"].get("second_look") or {}
    text = json.dumps({k: v for k, v in here.items() if k != "_tree"})
    marks = ('"cut":', '"cut_counts":', '"cut_reason":', '"other_side_of":', '"main_read":', '"source": "other_side"')
    check(not [k for k in marks if k in text],
          "nothing of the fixes in any stored draft or request (no cut list, other side, main_read)",
          [k for k in marks if k in text])
    check(any(it.get("source") == "link" for it in capped.get("items") or []),
          "links capped: linked parts stored (and their cut ones gone, as before)")


# ------------------------------------------------------------- 2. F1


def f1_main_read_fallback() -> None:
    on = dict(ZONES, intake_main_read_fallback="zones")
    # The reference counts: a clean read, and one busy main read at the defaults.
    with a_case(dict(on)) as (client, rid, pids, uid, state):
        clean = kinds()
        clean_data = t.db_row(Report, rid).data
        clean_ai = t.db_row(IntakeSession, rid).ai_draft
        clean_ids = {rid: "<rid>", **{pid: f"<p{i + 1:02d}>" for i, pid in enumerate(pids)}}
    check(state["stage"] == "ready" and state.get("read_notice") is None and "main_read" not in clean_ai,
          "on, nothing failing: ready, no notice, nothing new on the AI draft")
    with a_case(dict(ZONES), {"draft": busy_draft()}) as (client, rid, pids, uid, state):
        off = kinds()
        off_state = state
    per_call = off["draft"]
    check(off_state["stage"] == "failed" and off["zone"] == 0 and per_call > 0,
          "off: a busy main read fails the run, no zone read asked", (off_state["stage"], dict(off)))

    # (a) busy both times: the zone reads' finds are the list, all Check.
    with a_case(dict(on, intake_links="on"), {"draft": busy_draft()}) as (client, rid, pids, uid, state):
        asked = kinds()
        data = t.db_row(Report, rid).data
        session = t.db_row(IntakeSession, rid)
        listed = data.get("damage_summary") or []
        check(state["stage"] == "ready" and state["error"] is None, "both tries busy: ready, not failed",
              (state["stage"], state["error"]))
        check(asked["draft"] == 2 * per_call and asked["zone"] == clean["zone"],
              "the main read tried twice, each zone read once", dict(asked))
        check(sorted(it["part"] for it in listed) == ["Left Tail Lamp", "Rear Bumper"]
              and all(it.get("ai_confidence") == "Low" for it in listed)
              and all(it.get("found_by") == ["zone:rear"] for it in listed),
              "the list is the zone reads' finds, each a Check row found by its zone read",
              [(it["part"], it.get("ai_confidence"), it.get("found_by")) for it in listed])
        check(state.get("read_notice") == pipeline.MAIN_READ_FAILED_NOTE
              and pipeline.MAIN_READ_FAILED_NOTE == "The overall read failed. This list is from the area reads. "
                                                   "Press Re-read.",
              "GET /intake/{id} says so (read_notice)", state.get("read_notice"))
        check(session.ai_draft.get("main_read") == pipeline.MAIN_READ_FALLBACK
              and "overall" not in session.ai_draft.get("zone_reads")
              and session.ai_draft.get("zone_suggestions") in (None, [])
              and (data.get("second_look") or {}).get("reads") == session.ai_draft.get("zone_reads"),
              "the AI draft: main_read failed, the reads that answered (no overall), no zone-only suggestions",
              {k: session.ai_draft.get(k) for k in ("main_read", "zone_reads", "zone_suggestions")})
        check(any(it.get("source") == "link" for it in (data.get("second_look") or {}).get("items") or []),
              "the linked parts hang off the zone list as off any list")
        check(not (data.get("description") or "") and data.get("damaged_parts") == [it["part"] for it in listed],
              "no description of its own; damaged_parts follows the list")
        # A re-read that works: the main read's list, the notice gone.
        with fakes({"draft": lambda kwargs, labels: t._draft_answer(labels)}):
            t.REQUESTS.clear()
            r = client.post(f"/intake/{rid}/process", json={"mode": "read"})
            again = t.wait_done(client, rid) if r.status_code == 200 else {"stage": r.status_code}
        reread = [it["part"] for it in (t.db_row(Report, rid).data.get("damage_summary") or [])]
        check(again["stage"] == "ready" and again.get("read_notice") is None
              and "main_read" not in (t.db_row(IntakeSession, rid).ai_draft or {})
              and {"Rear Bumper", "Boot Lid", "Left Rear Door"} <= set(reread),
              "a re-read that works: the main read's list, no notice", (again.get("stage"), reread))

    # (b) busy once: the second try answers; the list is a clean read's.
    with a_case(dict(on), {"draft": busy_draft(until_zone=True)}) as (client, rid, pids, uid, state):
        asked = kinds()
        ids = {rid: "<rid>", **{pid: f"<p{i + 1:02d}>" for i, pid in enumerate(pids)}}
        data = t.normalised(t.db_row(Report, rid).data, ids)
        ai = t.normalised(t.db_row(IntakeSession, rid).ai_draft, ids)
    check(state["stage"] == "ready" and state.get("read_notice") is None and asked["zone"] == clean["zone"]
          and asked["draft"] == per_call + 1,
          "busy once: ready, the second try answered, the zone reads not asked again", dict(asked))
    check(data == t.normalised(clean_data, clean_ids) and ai == t.normalised(clean_ai, clean_ids),
          "... and the list, suggestions and AI draft as a clean read's",
          _first_diff(data, t.normalised(clean_data, clean_ids)))

    # (c) the zone reads find nothing: failed as before.
    with a_case(dict(on), {"draft": busy_draft(), "zone": no_zone_damage}) as (client, rid, pids, uid, state):
        asked = kinds()
    check(state["stage"] == "failed" and asked["draft"] == 2 * per_call and asked["zone"] > 0
          and "busy" in (state["error"] or "").lower(),
          "zone reads find nothing: the run fails as before (after the second try)", (state["stage"], dict(asked)))

    # (d) single mode: no zone reads; one more try, then failed.
    with a_case({"intake_main_read_fallback": "zones"}, {"draft": busy_draft()}) as (client, rid, pids, uid, state):
        asked = kinds()
    check(state["stage"] == "failed" and asked["draft"] == 2 * per_call and asked["zone"] == 0,
          "single mode: tried once more, then the run fails", (state["stage"], dict(asked)))

    # (e) the lead's narrowing (22 Nov): a refusal that is not "busy" (a
    # 400) never falls back -- no zone read, no second try.
    with a_case(dict(ZONES), {"draft": refused_draft}) as (client, rid, pids, uid, state):
        refused_off = kinds()
    with a_case(dict(on), {"draft": refused_draft}) as (client, rid, pids, uid, state):
        asked = kinds()
    check(state["stage"] == "failed" and asked["zone"] == 0 and asked == refused_off and asked["draft"] > 0,
          "a refused request (400, not busy): fails as with the setting off -- no zone read, no second try",
          (state["stage"], dict(asked), dict(refused_off)))

    # (f) past the zone stage's time box: no second try; the zone finds are the list.
    old_box = zones_module.TIME_BOX
    zones_module.TIME_BOX = 0.5
    try:
        with a_case(dict(on), {"draft": busy_draft(), "zone": slow_zone(0.7)}) as (client, rid, pids, uid, state):
            asked = kinds()
            listed = [it["part"] for it in t.db_row(Report, rid).data.get("damage_summary") or []]
    finally:
        zones_module.TIME_BOX = old_box
    check(state["stage"] == "ready" and asked["draft"] == per_call and state.get("read_notice")
          and sorted(listed) == ["Left Tail Lamp", "Rear Bumper"],
          "past the zone stage's time box: no second try, the zone finds are the list", (dict(asked), listed))

    # Which failures it is for, and the draft it keeps.
    busy = [{"outcome": "error", "busy": True}, {"outcome": "timeout"}, {"outcome": "day_spent"}]
    chain_failed = RuntimeError("All Gemini fallback models (a, b) failed across 1 key(s).")
    check(pipeline.fallback_worthy(chain_failed, busy) and pipeline.fallback_worthy(TimeoutError("deadline"))
          and not pipeline.fallback_worthy(chain_failed, busy + [{"outcome": "error"}])
          and not pipeline.fallback_worthy(chain_failed, [{"outcome": "429", "spent_until": "x"}])
          and not pipeline.fallback_worthy(chain_failed, [{"outcome": "day_spent"}])
          and not pipeline.fallback_worthy(chain_failed, [])
          and not pipeline.fallback_worthy(AiUnavailable("ai_key_invalid"), busy)
          and not pipeline.fallback_worthy(AiUnavailable("ai_quota_used"), busy)
          and not pipeline.fallback_worthy(allowance.DayUsedUp("All Gemini fallback models (a) failed"), busy)
          and not pipeline.fallback_worthy(ValueError("there is nothing to read"), busy)
          and not pipeline.fallback_worthy(FileNotFoundError("x"), busy)
          and not pipeline.fallback_worthy(RuntimeError("something else"), busy),
          "only busy (503) or timeout: never a refused/invalid key, a spent day, a 400, a bad answer, nothing to "
          "read")
    prev = {"description": "Hit at the rear.", "vehicle_info": {"make": "Gammamotors", "plate_number": "SZZ1Z"},
            "damage_summary": [{"part": "Boot Lid"}], "damaged_parts": ["Boot Lid"], "zone_reads": ["overall"]}
    kept = pipeline.fallback_draft(prev)
    check(kept["description"] == "Hit at the rear." and kept["vehicle_info"] == prev["vehicle_info"]
          and kept["damage_summary"] == [] and kept["damaged_parts"] == [] and "zone_reads" not in kept
          and pipeline.fallback_draft(None)["description"] == "",
          "fallback_draft: the last reading's facts, no list of its own, nothing but the draft's fields")
    check(pipeline.read_notice({}) is None and pipeline.read_notice(None) is None
          and pipeline.read_notice({"main_read": {"status": "ok"}}) is None,
          "read_notice: None for any other draft (an old one too)")


# ------------------------------------------------------------- 3. F2 + F5


def _cand(part: str, *, key=None, bucket="likely", intact=False, n=0, group="Rear Bumper") -> dict:
    return {"key": key or part, "part": part, "source": "link", "linked_from": [group], "link_type": "attached",
            "bucket": bucket, "intact_in": ["zone:rear"] if intact else [], "_order": (0, n), "covers": [part]}


def f5_units() -> None:
    # _cap: the per-group cap, the overall cap and the strip-down block's.
    kept = [_cand(f"P{i}", n=i) for i in range(6)] + [_cand("S0", bucket="strip_down", n=7),
                                                       _cand("S1", bucket="strip_down", n=8),
                                                       _cand("I0", intact=True, n=9)]
    opts = links.Options(mode="on", max_items=3, assembly_max=4, stripdown_cap="out", stripdown_max=1)
    plain = links._cap(copy.deepcopy(kept), opts)
    cut: list[dict] = []
    shown = links._cap(copy.deepcopy(kept), opts, cut=cut)
    check(shown == plain, "_cap shows the same with a cut list as without")
    check([c["part"] for c in cut] == ["P3", "P4", "P5", "S1", "I0"]
          and [c["cut_reason"] for c in cut] == ["limit"] * 4 + ["intact"],
          "... and the cut list holds the rest, in order: per group, overall, the block, a zone read's intact",
          [(c["part"], c["cut_reason"]) for c in cut])
    check(len(shown) + len(cut) == len(kept), "nothing vanishes: shown + cut = every candidate")

    # build_all on the fake case's list: same shown list, the cut one's reasons.
    listed = [{"part": "Rear Bumper", "condition_tokens": ["Cracked", "Crumpled"], "severity": "Severe",
               "remarks": "Replace", "photo_reference": "@p3", "seen_in": ["@p3"], "found_by": ["overall"]},
              {"part": "Boot Lid", "condition_tokens": ["Bent"], "photo_reference": "@p5"}]
    intact = {"zone:rear": ["Parking Sensor", "Rear Windscreen"]}
    for name, o in (("the defaults", links.options(settings)),
                    ("capped", links.Options(mode="on", max_items=2, stripdown_cap="out", stripdown_max=1)),
                    ("intact drop", links.Options(mode="on", max_items=2, intact="drop"))):
        before = links.build_all(copy.deepcopy(listed), [], [], intact, L1, o)
        got: list[dict] = []
        after = links.build_all(copy.deepcopy(listed), [], [], intact, L1, o, cut=got)
        wide = links.build_all(copy.deepcopy(listed), [], [], {}, L1,
                               links.Options(mode="on", max_items=999, assembly_max=999, intact="ignore"))
        check(after == before and {it["part"] for it in after[0]} | {it["part"] for it in got}
              == {it["part"] for it in wide[0]} and not {it["part"] for it in after[0]} & {it["part"] for it in got},
              f"build_all ({name}): shown as without a cut list; shown + cut = every linked part once",
              ({it["part"] for it in wide[0]} - {it["part"] for it in after[0]} - {it["part"] for it in got}))
        check(all("_order" not in it and it["cut_reason"] == ("intact" if it.get("intact_in") else "limit")
                  for it in got)
              and (name != "intact drop" or any(it["part"] == "Parking Sensor" and it["cut_reason"] == "intact"
                                                for it in got)),
              f"... each cut one with its reason ({Counter(it['cut_reason'] for it in got)})")
        SecondLook(cut=[SecondLookItem.model_validate(it) for it in got])  # the stored shape takes them

    # for_parent: the re-link's cut parts (room, caps).
    data = {"damage_summary": [], "second_look": {"items": []}}
    o = links.Options(mode="on", max_items=2)
    plain = links.for_parent(copy.deepcopy(listed[0]), copy.deepcopy(data), [], L1, o)
    got = []
    mine = links.for_parent(copy.deepcopy(listed[0]), copy.deepcopy(data), [], L1, o, cut=got)
    check(mine == plain and len(mine) == 2 and got and all(it["cut_reason"] in ("limit", "intact") for it in got)
          and not {it["key"] for it in mine} & {it["key"] for it in got},
          "for_parent: shown as without, what the room and caps left out in the cut list", len(got))

    # SecondLook's dump: cut and cut_counts only when there is some.
    empty = SecondLook().model_dump()
    check("cut" not in empty and "cut_counts" not in empty
          and "other_side_of" not in SecondLookItem(part="Boot Lid").model_dump()
          and "cut_reason" not in SecondLookItem(part="Boot Lid").model_dump(),
          "the new fields are left out of a dump at their defaults (an old draft saves as before)")
    old = {"status": "ok", "items": [{"part": "Boot Lid", "key": "Boot Lid"}], "dismissed": []}
    check(SecondLook.model_validate(old).model_dump()["items"][0] == {**SecondLookItem(part="Boot Lid", key="Boot Lid")
                                                                      .model_dump()},
          "an old draft's second look loads (no cut)")
    check(second_look.cut_counts([{"cut_reason": "limit"}, {"cut_reason": "intact"}, {}]) == {"intact": 1, "limit": 2},
          "cut_counts: by reason")


def f5_end_to_end() -> None:
    off_items = None
    with a_case(dict(CAPPED)) as (client, rid, pids, uid, state):
        ids = {rid: "<rid>", **{pid: f"<p{i + 1:02d}>" for i, pid in enumerate(pids)}}
        off_look = t.normalised(look_of(rid), ids)
        off_items = off_look.get("items")
    check("cut" not in off_look and "cut_counts" not in off_look, "off: no cut list")
    with a_case(dict(CAPPED, intake_keep_cut="on")) as (client, rid, pids, uid, state):
        ids = {rid: "<rid>", **{pid: f"<p{i + 1:02d}>" for i, pid in enumerate(pids)}}
        look = look_of(rid)
        cut = look.get("cut") or []
        check(t.normalised(look.get("items"), ids) == off_items,
              "on: the suggestions shown exactly as off (items unchanged, link check included)",
              _first_diff(t.normalised(look.get("items"), ids), off_items))
        check(cut and look.get("cut_counts") == dict(sorted(Counter(it["cut_reason"] for it in cut).items()))
              and all(it.get("source") == "link" and it.get("name") and it.get("covers") for it in cut)
              and not {it["key"] for it in cut} & {it["key"] for it in look["items"]},
              "the linked parts the caps cut: in second_look.cut, named, counted by reason, none shown too",
              look.get("cut_counts"))
        check(any(it["part"] == "Parking Sensor" and it["cut_reason"] == "intact" for it in cut),
              "the zone read's intact Parking Sensor, demoted and cut, kept as \"a zone read called it not "
              "damaged\" (intact)", [(it["part"], it["cut_reason"]) for it in cut])
        check(state["data"]["second_look"].get("cut") == cut, "GET /intake/{id} hands it to the Review step")
        # add, dismiss, undo on cut ones
        first, second = cut[0], cut[1]
        r = client.post(f"/intake/{rid}/suggestions", json={"key": first["key"], "action": "add"})
        look2 = r.json().get("second_look") or {}
        listed = [it["part"] for it in t.db_row(Report, rid).data.get("damage_summary") or []]
        check(r.status_code == 200 and (r.json().get("item") or {}).get("part") == first["part"]
              and first["part"] in listed
              and any(it["key"] == first["key"] and it.get("cut_reason") for it in look2.get("items") or [])
              and first["key"] not in {it["key"] for it in look2.get("cut") or []}
              and sum((look2.get("cut_counts") or {}).values()) == len(look2.get("cut") or []),
              "add on a cut one: listed, moved into items (decided, its reason kept), out of cut, recounted",
              (r.status_code, r.text[:300]))
        r = client.post(f"/intake/{rid}/suggestions", json={"key": second["key"], "action": "dismiss"})
        look3 = look_of(rid)
        check(r.status_code == 200 and second["key"] in look3.get("dismissed")
              and any(it["key"] == second["key"] for it in look3.get("items"))
              and second["key"] not in {it["key"] for it in look3.get("cut") or []},
              "dismiss on a cut one: dismissed, in items for \"Decided\" and undo", r.text[:300])
        r = client.post(f"/intake/{rid}/suggestions", json={"key": first["key"], "action": "undo"})
        listed = [it["part"] for it in t.db_row(Report, rid).data.get("damage_summary") or []]
        check(r.status_code == 200 and first["part"] not in listed, "undo of that add: off the list again", r.text[:300])
        r = client.post(f"/intake/{rid}/suggestions", json={"key": "No Such Part", "action": "add"})
        check(r.status_code in (404, 400, 409, 422), "an unknown key is still refused", r.status_code)
        # filing relabels the cut ones' photos
        state = client.get(f"/intake/{rid}").json()
        r = client.post(f"/intake/{rid}/finalize", json={"data": state["data"]})
        filed = t.db_row(Report, rid).data
        check(r.status_code == 200 and filed.get("second_look", {}).get("cut")
              and not t.refs_in(filed.get("second_look", {}).get("cut")),
              "filed: the cut list kept, its photos as P-numbers", (r.status_code, t.refs_in(filed.get("second_look"))))
        check(t.sign_off(client, rid).status_code == 200, "and signed off with a cut list on it")

    # the suggestions' own cap (second_look.MAX_SUGGESTIONS)
    old_max = second_look.MAX_SUGGESTIONS
    second_look.MAX_SUGGESTIONS = 0
    try:
        with a_case(dict(ZONES, intake_keep_cut="on")) as (client, rid, pids, uid, state):
            look = look_of(rid)
        with a_case(dict(ZONES)) as (client, rid, pids, uid, state):
            look_off = look_of(rid)
    finally:
        second_look.MAX_SUGGESTIONS = old_max
    cut = look.get("cut") or []
    check([(it["part"], it["source"], it["cut_reason"]) for it in cut] == [("Left Tail Lamp", "zone", "limit")]
          and look.get("cut_counts") == {"limit": 1} and not look.get("items") and "cut" not in look_off,
          "the suggestions past MAX_SUGGESTIONS: cut, \"limit\" (off: gone as before)",
          [(it["part"], it.get("source"), it.get("cut_reason")) for it in cut])

    # a hand-add's re-link: its cut parts join the cut list (suggestions.with_relinks)
    with t.Settings(**dict(CAPPED, intake_keep_cut="on")):
        data = {"damage_summary": [], "second_look": SecondLook().model_dump()}
        bumper = {"part": "Rear Bumper", "condition_tokens": ["Cracked", "Crumpled"], "severity": "Severe"}
        look = suggestions.with_relinks({**data, "damage_summary": [bumper]}, [bumper])
    with t.Settings(**dict(CAPPED)):
        look_off = suggestions.with_relinks({**data, "damage_summary": [bumper]}, [bumper])
    check(look and look.get("cut") and look["items"] == look_off["items"] and "cut" not in look_off
          and SecondLook.model_validate(look).model_dump().get("cut_counts") == look.get("cut_counts"),
          "a hand-add's re-link: shown as off, its cut parts in the cut list", (look or {}).get("cut_counts"))


# ------------------------------------------------------------- 4. F3


def f3_other_side() -> None:
    areas = [("p1", "rear-left"), ("p2", "rear-left"), ("p3", "rear")]
    lamp = {"part": "Left Tail Lamp", "photo_reference": "@p1", "seen_in": ["@p1"], "ai_confidence": "Medium"}
    other = {"part": "Tail Lamp (side undetermined)", "photo_reference": "@p2", "seen_in": ["@p2"]}
    same = {"part": "Tail Lamp (side undetermined)", "photo_reference": "@p1", "seen_in": ["@p1"]}
    items = [copy.deepcopy(lamp), copy.deepcopy(other)]
    plain = pipeline.resolve_sides(copy.deepcopy(items), areas)
    found: list[dict] = []
    mine = pipeline.resolve_sides(copy.deepcopy(items), areas, other_side=found)
    check(plain == mine and [it["part"] for it in mine] == ["Left Tail Lamp"],
          "resolve_sides: the list as before (the placed twin left out of it)")
    check([(it["part"], it["other_side_of"]) for it in found] == [("Tail Lamp (side undetermined)", "Left Tail Lamp")],
          "... another photo's: handed back, other_side_of the listed name", found)
    found = []
    pipeline.resolve_sides([copy.deepcopy(lamp), copy.deepcopy(same)], areas, other_side=found)
    check(found == [], "the same photo's: the same find, dropped as before")
    found = []
    two = [copy.deepcopy(other), {**copy.deepcopy(other), "photo_reference": "@p1", "seen_in": ["@p1"]}]
    out = pipeline.resolve_sides(two, areas, other_side=found)
    check([it["part"] for it in out] == ["Left Tail Lamp"] and len(found) == 1,
          "two undetermined lamps on two photos, both placed left: one listed, the other handed back")

    # end to end
    base = dict(ZONES)
    answers = {"draft": two_lamps_draft}
    with a_case(dict(base), answers, OTHER_SIDE_CASE) as (client, rid, pids, uid, state):
        off = look_of(rid)
    check(not [it for it in off.get("items") or [] if it.get("source") == "other_side"],
          "off: the placed twin is dropped, as before")
    with a_case(dict(base, intake_keep_other_side="on"), answers, OTHER_SIDE_CASE) as (client, rid, pids, uid, state):
        look = look_of(rid)
        sug = next((it for it in look.get("items") or [] if it.get("source") == "other_side"), None)
        listed = [it["part"] for it in t.db_row(Report, rid).data.get("damage_summary") or []]
        check(sug is not None and sug["part"] == "Tail Lamp (side undetermined)"
              and sug.get("other_side_of") == "Left Tail Lamp" and sug.get("other_side_listed") == "Left Tail Lamp"
              and "Left Tail Lamp" not in sug["covers"] and "Right Tail Lamp" in sug["covers"]
              and sug.get("confidence") == "Low" and "Left Tail Lamp" in listed
              and sug.get("photo_reference") == f"@{pids[8]}",
              "on: an \"Other side?\" suggestion -- its photo, the listed name, not covered by it", sug)
        check(second_look.split_settled([sug], t.db_row(Report, rid).data)[0] == [sug],
              "... open: the listed Left Tail Lamp doesn't settle it")
        r = client.post(f"/intake/{rid}/suggestions", json={"key": sug["key"], "action": "add"})
        listed = [it["part"] for it in t.db_row(Report, rid).data.get("damage_summary") or []]
        check(r.status_code == 200 and "Tail Lamp (side undetermined)" in listed and "Left Tail Lamp" in listed,
              "adding it lists the second lamp beside the first", (r.status_code, listed))
        check(t.sign_off(client, rid).status_code in (200, 400, 409), "sign-off still answers")


# ------------------------------------------------------------- 5. F6


def f6_hidden_no() -> None:
    items = [{"key": "beam", "part": "Rear Bumper Beam", "source": "link", "hidden": True},
             {"key": "plate", "part": "Rear Number Plate", "source": "link", "hidden": False},
             {"key": "lamp", "part": "Left Tail Lamp", "source": "link", "hidden": False}]
    results = {"beam": {"link_check": {"verdict": "no", "cue": "none_seen"}, "entry": None},
               "plate": {"link_check": {"verdict": "no", "cue": "none_seen"}, "entry": None},
               "lamp": {"link_check": {"verdict": "likely", "cue": "cracked"}, "entry": None}}
    keep = link_check.apply(copy.deepcopy(items), results, set())
    told = link_check.apply(copy.deepcopy(items), results, set(), hidden_rule="cannot_tell")
    check([it["key"] for it in keep] == ["lamp", "beam", "plate"]
          and [it["link_check"]["verdict"] for it in keep] == ["likely", "no", "no"],
          "keep (default): every \"no\" goes last, as before")
    check([it["key"] for it in told] == ["beam", "lamp", "plate"]
          and [it["link_check"]["verdict"] for it in told] == ["cannot_tell", "likely", "no"],
          "cannot_tell: the hidden part's \"no\" is cannot_tell and keeps its place; the plate's stays no")
    check(link_check.apply(copy.deepcopy(items), results, set()) == link_check.apply(copy.deepcopy(items), results,
                                                                                       set(), hidden_rule="keep")
          and link_check.hidden_no() == "keep", "the setting's default is keep")

    def beam_verdict(values):
        with a_case(values, {"link": beam_no}) as (client, rid, pids, uid, state):
            look = look_of(rid)
        beam = next((it for it in look.get("items") or [] if it.get("part") == "Rear Bumper Beam"), None)
        return (beam or {}).get("link_check", {}).get("verdict"), beam
    off, beam = beam_verdict(dict(LINKS))
    on, beam_on = beam_verdict(dict(LINKS, intake_link_hidden_no="cannot_tell"))
    check(off == "no" and on == "cannot_tell" and beam_on.get("hidden"),
          "end to end: the beam's \"no\" stays at keep, is cannot_tell with the setting", (off, on))


# ------------------------------------------------------------- 6. F7


def f7_sort_retry() -> None:
    with a_case({}, {"sort": skipping_sort()}) as (client, rid, pids, uid, state):
        off = kinds()
        off_photos = photos_of(rid)
    check(off["sort"] == 1 and sum(1 for p in off_photos if p[0] is None) == 2,
          "off: one sort call, the 2 skipped photos stay unsorted", (dict(off), off_photos))
    with a_case({"intake_sort_retry_skipped": "on"}, {"sort": skipping_sort()}) as (client, rid, pids, uid, state):
        on = kinds()
        sorts = [r for r in t.REQUESTS if t.kind_of(r) == "sort"]
        on_photos = photos_of(rid)
        run = next(r for r in t._run_rows(rid) if r["kind"] == "sort")
    check(on["sort"] == 2 and t._labels(sorts[1]["input"]) == ["P09", "P10"]
          and all(p[0] is not None for p in on_photos) and state["stage"] == "ready",
          "on: one more sort call with just the skipped photos; every photo sorted", (dict(on), on_photos))
    check(run["status"] == "ok" and run["photos_sent"] == {"count": 12}, "the sort run counts both calls' photos",
          run["photos_sent"])
    with a_case({"intake_sort_retry_skipped": "on"}) as (client, rid, pids, uid, state):
        plain = kinds()
    check(plain["sort"] == 1, "on, nothing skipped: no extra call")

    def failing_retry(kwargs, labels):
        if labels == ["P09", "P10"]:
            raise Busy()
        return first(kwargs, labels)
    first = skipping_sort()
    with a_case({"intake_sort_retry_skipped": "on"}, {"sort": failing_retry}) as (client, rid, pids, uid, state):
        photos = photos_of(rid)
    check(state["stage"] == "ready" and sum(1 for p in photos if p[0] is None) == 2,
          "the extra batch failing never fails the run: those photos stay unsorted", (state["stage"], photos))


def main() -> int:
    t.init_db()
    for name, fn in (
        ("0. settings: defaults and compose", settings_section),
        (f"1. the defaults: byte for byte as master {MASTER}", master_equal),
        ("2. F1: a failed main read falls back to the zone reads", f1_main_read_fallback),
        ("3a. F2 + F5: the caps' cuts kept (units)", f5_units),
        ("3b. F2 + F5: end to end, decisions, filing, re-link", f5_end_to_end),
        ("4. F3: two of a pair stay two", f3_other_side),
        ("5. F6: a hidden part's \"no\"", f6_hidden_no),
        ("6. F7: skipped photos sorted once more", f7_sort_retry),
    ):
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
