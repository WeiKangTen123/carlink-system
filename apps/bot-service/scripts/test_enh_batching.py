"""The batching items B2-B5 and B9 (plans/2026-11-22-enhance-staging/
R-batching.md sections 2d and 5; plan.md section 6), backend only --
offline: Gemini is the fake of scripts/test_c10_integration.py (through
scripts/test_enh_fixes.py's helpers), no server, made-up cases only.

  0. settings: each defaults to today's, is passed through docker-compose
     once in the shared block, takes its choices and refuses a typo;
  1. everything at the defaults, byte for byte as master 5ee5e03: the full
     routes with a person regrouping photos (filing, sign-off, the
     AI-accuracy row), a 60-photo case (5 sort batches), and a case whose
     linked check asks two areas -- on a git archive of master and here;
  2. B2 (intake_parallel_reads on): the zone reads start beside the main
     read; the list, suggestions and AI draft as one after the other; the
     progress only counts up; a failed main read cancels and waits for
     them (the run stays failed); with F1 on, the zone answers are already
     there (not asked twice);
  3. B3 (intake_link_check_parallel on): both areas' calls at once, the
     answers as one after the other; one area failing leaves the other's;
  4. B4 (intake_sort_workers): 5 batches at once on 60 photos (3 by
     default); through the real rate limiter they start spaced;
  5. B5 (intake_keep_sort_label on): the sorter's kind kept on a regroup
     (the first one), filed into the photo index, counted at sign-off and
     in GET /admin/ai-accuracy; the column added to an old table; off:
     nothing kept or counted;
  6. B9 (intake_sort_rescue on): one more sort call at thinking "low" for
     the photos no read would see -- a "yes" puts a photo back as damage,
     and the zone reads see it; skipped photos too; none to ask: no call;
     the call failing never fails the run.

    python scripts/test_enh_batching.py
    python scripts/test_enh_batching.py --probe OUT.json   (section 1's
        scenarios alone, on the tree C10_TEST_ROOT names)
"""
import contextlib
import copy
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(1, str(HERE))
for _name in ("INTAKE_PARALLEL_READS", "INTAKE_LINK_CHECK_PARALLEL", "INTAKE_SORT_WORKERS", "INTAKE_KEEP_SORT_LABEL",
              "INTAKE_SORT_RESCUE"):
    os.environ.pop(_name, None)

# test_enh_fixes brings the fake Gemini (test_c10_integration) and its helpers.
import test_enh_fixes as fx  # noqa: E402

t = fx.t
from fastapi.testclient import TestClient  # noqa: E402

from app.reports.models import AiAccuracy, IntakePhoto, IntakeSession, Report, User  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

ZONES, LINKS = fx.ZONES, fx.LINKS


def busy_first(n: int):
    """The main read's answer: busy for its first n requests (one whole
    draft_report call), then the answer."""
    count = [0]
    lock = threading.Lock()

    def answer(kwargs, labels):
        with lock:
            count[0] += 1
            failing = count[0] <= n
        if failing:
            raise fx.Busy()
        return t._draft_answer(labels)
    return answer


def two_area_draft(kwargs, labels):
    """The main read with the Left Rear Door bent: its linked parts (glass,
    regulator, sill) give the linked check a second area beside the rear."""
    out = t._draft_answer(labels)
    for item in out["damage_summary"]:
        if item["part"] == "Left Rear Door":
            item.update(condition_tokens=["Bent"], severity="Severe", remarks="Replace", ai_confidence="High")
    return out


SIXTY = [("damage", "rear", "Rear Bumper", True) if i % 3 == 0 else ("overview", "rear", None, False)
         for i in range(60)]


@contextlib.contextmanager
def big_case(rows: list):
    """t.CASE as `rows` for the block (photo P01 is rows[0])."""
    old = list(t.CASE)
    t.CASE[:] = rows
    try:
        yield
    finally:
        t.CASE[:] = old


def _ids(rid, pids, uid=None):
    ids = {rid: "<rid>", **{pid: f"<p{i + 1:02d}>" for i, pid in enumerate(pids)}}
    if uid:
        ids.update({uid: "<uid>", t.db_row(User, uid).email: "<email>"})
    return ids


def regroup_scenario() -> dict:
    """A read, two photos regrouped by a person (one twice), filed and
    signed off: what is kept at each step."""
    t.REQUESTS.clear()
    with t.Settings(**ZONES), TestClient(t._app()) as client:
        uid = sign_in(client, role="admin", may_sign_off=True)
        rid, pids = t.new_draft(client)
        ids = _ids(rid, pids, uid)
        state = t.read(client, rid)
        out = {"stage": state["stage"]}
        answers = []
        for pid, kind in ((pids[8], "interior"), (pids[5], "damage"), (pids[5], "tyre")):
            r = client.patch(f"/intake/{rid}/photos/{pid}", json={"kind": kind})
            answers.append((r.status_code, r.json()))
        out["patches"] = answers
        out["photos"] = fx.photos_of(rid)
        state = client.get(f"/intake/{rid}").json()
        out["state_photos"] = state["photos"]
        out["finalize"] = client.post(f"/intake/{rid}/finalize", json={"data": state["data"]}).json()
        out["filed"] = t.db_row(Report, rid).data
        out["sign_off"] = t.sign_off(client, rid).status_code
        row = t.db_row(AiAccuracy, rid)
        out["accuracy"] = {"version": row.version if row else None, "metrics": row.metrics if row else None}
        out["admin"] = client.get("/admin/ai-accuracy").json().get("totals")
        out["requests"] = sorted((t.normalised(t._request(r), ids) for r in t.REQUESTS),
                                 key=lambda r: json.dumps(r, sort_keys=True))
        return t.normalised(out, ids)


def read_artefacts(values: dict, answers: dict | None = None, rows: list | None = None) -> dict:
    """One read with these settings, normalised: requests, the stored
    draft, the AI draft, the run log and the photos."""
    ctx = big_case(rows) if rows is not None else contextlib.nullcontext()
    with ctx, fx.a_case(values, answers, None) as (client, rid, pids, uid, state):
        ids = _ids(rid, pids, uid)
        session = t.db_row(IntakeSession, rid)
        out = {"stage": state["stage"], "data": t.db_row(Report, rid).data, "ai_draft": session.ai_draft,
               "runs": t._run_rows(rid), "photos": fx.photos_of(rid),
               "requests": sorted((t.normalised(t._request(r), ids) for r in t.REQUESTS),
                                  key=lambda r: json.dumps(r, sort_keys=True))}
        return t.normalised(out, ids)


PROBES = {
    "regroup": regroup_scenario,
    "sixty_photos": lambda: read_artefacts(dict(ZONES), None, SIXTY),
    "two_link_areas": lambda: read_artefacts(dict(LINKS), {"draft": two_area_draft}),
}


def probe(out: Path) -> int:
    t.init_db()
    import app

    result = {"_tree": {"app": str(Path(app.__file__).resolve().parent)}}
    for name, fn in PROBES.items():
        result[name] = fn()
    out.write_text(json.dumps(result, sort_keys=True, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__" and "--probe" in sys.argv:
    sys.exit(probe(Path(sys.argv[sys.argv.index("--probe") + 1])))

# ------------------------------------------------------------- this tree only

from app import config  # noqa: E402
from app.ai import link_check, rate_limit, structured, triage  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import pipeline  # noqa: E402
from app.intake import telegram_bridge as bridge  # noqa: E402
from app.reports import accuracy, db as db_module  # noqa: E402

ROOT = HERE.parent
REPO = ROOT.parent.parent
MASTER = fx.MASTER
PY = sys.executable

# The rescue sort's requests, to the fake (its schema's key).
_KIND_OF = t.kind_of


def kind_of(kwargs) -> str:
    if triage.RESCUE_KEY in kwargs["response_format"]["schema"].get("properties", {}):
        return "rescue"
    return _KIND_OF(kwargs)


t.kind_of = kind_of


def rescue_answer(yes: dict | None = None):
    """The rescue sort's answer: "yes" with the area for the labels in
    `yes`, "no" for the rest."""
    yes = yes or {}

    def answer(kwargs, labels):
        return {triage.RESCUE_KEY: [{"label": label, "insured_damage": "yes" if label in yes else "no",
                                     "area": yes.get(label, "unclear")} for label in labels]}
    return answer


t.ANSWERS["rescue"] = rescue_answer()

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


kinds = fx.kinds
_first_diff = fx._first_diff


# ------------------------------------------------------------- 0. settings

NEW_SETTINGS = {"intake_parallel_reads": "off", "intake_link_check_parallel": "off", "intake_sort_workers": 3,
                "intake_keep_sort_label": "off", "intake_sort_rescue": "off"}


def settings_section() -> None:
    fresh = config.Settings()
    check({k: getattr(fresh, k) for k in NEW_SETTINGS} == NEW_SETTINGS, "every B setting defaults to today's",
          {k: getattr(fresh, k) for k in NEW_SETTINGS})
    check({k: getattr(settings, k) for k in NEW_SETTINGS} == NEW_SETTINGS, "and this process runs at them")
    bad = []
    for name in ("intake_parallel_reads", "intake_link_check_parallel", "intake_keep_sort_label", "intake_sort_rescue"):
        if getattr(config.Settings(**{name: "ON"}), name) != "on" or getattr(config.Settings(**{name: ""}), name) != "off":
            bad.append((name, "choices"))
        try:
            config.Settings(**{name: "maybe"})
            bad.append((name, "typo accepted"))
        except Exception:
            pass
    for good in (1, 3, 5, "5"):
        if config.Settings(intake_sort_workers=good).intake_sort_workers != int(good):
            bad.append(("intake_sort_workers", good))
    if config.Settings(intake_sort_workers="").intake_sort_workers != 3:
        bad.append(("intake_sort_workers", "blank"))
    for wrong in (0, 6, "many", -1):
        try:
            config.Settings(intake_sort_workers=wrong)
            bad.append(("intake_sort_workers accepted", wrong))
        except Exception:
            pass
    check(not bad, "each takes its choices; sort workers 1 to 5, blank 3; a typo stops the app", bad)
    compose = (REPO / "infra" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    anchor, services = compose.index("x-bot-service: &bot-service"), compose.index("\nservices:")
    wrong = []
    for name, default in NEW_SETTINGS.items():
        env = name.upper()
        want = f"    - {env}=${{{env}:-{default}}}"
        lines = [ln for ln in compose.splitlines() if ln.startswith(f"    - {env}=")]
        if lines != [want] or not anchor < compose.find(f"- {env}=") < services:
            wrong.append((env, lines, want))
    check(not wrong, "docker-compose.prod.yml: each once, in the shared block, with config's default", wrong)


# ------------------------------------------------------------- 1. the defaults, as master


def master_equal() -> None:
    work = Path(tempfile.mkdtemp(prefix="enh-batching-master-"))
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
        proc = subprocess.run([PY, str(HERE / "test_enh_batching.py"), "--probe", str(out)], capture_output=True,
                              text=True, encoding="utf-8", cwd=str(root), env=fx.clean_env(C10_TEST_ROOT=str(root)),
                              timeout=1800)
        if not check(proc.returncode == 0 and out.exists(), f"the scenarios ran on {name}'s tree",
                     (proc.stdout + proc.stderr)[-1500:]):
            return
        outs[name] = json.loads(out.read_text(encoding="utf-8"))
        check(Path(outs[name]["_tree"]["app"]).parent == root.resolve(), f"... with {name}'s own app")
    for name in PROBES:
        check(outs["here"][name] == outs["master"][name],
              f"{name}: every request and everything stored as master's", _first_diff(outs["here"][name],
                                                                                        outs["master"][name]))
    here = outs["here"]
    reg = here["regroup"]
    check(reg["sign_off"] == 200 and all(code == 200 for code, _ in reg["patches"])
          and "ai_kind" not in json.dumps(reg) and "regroups" not in json.dumps(reg),
          "regroup: patched, filed, signed off; no sorter's kind kept, no regroup count (off)")
    sorts = [r for r in here["sixty_photos"]["requests"] if "photos" in json.dumps(r.get("response_format"))]
    check(len(here["sixty_photos"]["photos"]) == 60 and len(sorts) >= 5, "sixty photos: 5 sort batches", len(sorts))
    links = [r for r in here["two_link_areas"]["requests"] if '"answers"' in json.dumps(r.get("response_format"))]
    check(len(links) == 2, "two link areas: the linked check asked 2 areas", len(links))


# ------------------------------------------------------------- 2. B2


def b2_parallel_reads() -> None:
    zone_seen = threading.Event()
    overlap: list[bool] = []

    def zone(kwargs, labels):
        zone_seen.set()
        return t._zone_answer(kwargs, labels)

    def draft(kwargs, labels):
        overlap.append(zone_seen.wait(5))
        return t._draft_answer(labels)

    def artefacts(values):
        zone_seen.clear()
        overlap.clear()
        with fx.a_case(values, {"zone": zone, "draft": draft}) as (client, rid, pids, uid, state):
            ids = _ids(rid, pids)
            out = {"stage": state["stage"], "data": t.normalised(t.db_row(Report, rid).data, ids),
                   "ai_draft": t.normalised(t.db_row(IntakeSession, rid).ai_draft, ids),
                   "requests": sorted((t.normalised(t._request(r), _ids(rid, pids, uid)) for r in t.REQUESTS),
                                      key=lambda r: json.dumps(r, sort_keys=True))}
        return out, list(overlap)

    # B2 acts only with F1 "zones" (the review's M4).
    on = dict(LINKS, intake_parallel_reads="on", intake_main_read_fallback="zones")
    serial, serial_overlap = artefacts(dict(LINKS))
    alone, alone_overlap = artefacts(dict(LINKS, intake_parallel_reads="on"))
    seen = []
    real_set_stage = pipeline._set_stage

    def recording(report_id, stage, done=0, total=0, *a, **kw):
        if stage == "reading":
            seen.append((done, total))
        return real_set_stage(report_id, stage, done, total, *a, **kw)

    pipeline._set_stage = recording
    try:
        parallel, parallel_overlap = artefacts(on)
    finally:
        pipeline._set_stage = real_set_stage
    check(serial_overlap == [False] and parallel_overlap == [True] and alone_overlap == [False] and alone == serial,
          "off: the zone reads after the main read; on with F1: started beside it; on without F1: as off (M4)",
          (serial_overlap, parallel_overlap, alone_overlap))
    check(parallel == serial, "on: the same requests, list, suggestions, linked parts and AI draft as off",
          _first_diff(parallel, serial))
    totals = {total for _, total in seen[1:]}
    dones = [done for done, total in seen if total > 1]
    check(len(totals) == 1 and dones == sorted(dones) and max(dones) == max(totals),
          "the progress counts up to 1 + the zone calls, never back", seen)

    # The main read failing in a way F1 doesn't fall back for (a 400), the
    # rear zone still reading: the failure is shown at once (L3), the zone
    # reads are cancelled and waited for -- the run stays reserved -- and
    # nothing writes "reading" over the failure.
    with fx.a_case(dict(on), {"draft": fx.refused_draft, "zone": fx.slow_zone(1.5)}) as (
            client, rid, pids, uid, state):
        still_running = pipeline.is_running(rid)
        end = time.time() + 30
        while pipeline.is_running(rid) and time.time() < end:
            time.sleep(0.1)
        time.sleep(0.3)
        later = client.get(f"/intake/{rid}").json()
        zone_runs = [r for r in t._run_rows(rid) if r["kind"] == "read_zones"]
    check(state["stage"] == "failed" and still_running and later["stage"] == "failed"
          and not pipeline.is_running(rid) and zone_runs and all(r["status"] != "running" for r in zone_runs),
          "a main read failing without fallback: shown failed at once while the zone reads finish (still reserved), "
          "stays failed, the zone run closed",
          (state["stage"], still_running, later["stage"], [r["status"] for r in zone_runs]))

    # L2: a progress write that fails is never the main read failing.
    progress = pipeline.ReadProgress("no-such-draft", 2)
    real_set_stage = pipeline._set_stage

    def broken(*a, **kw):
        raise RuntimeError("database is locked")
    pipeline._set_stage = broken
    try:
        progress.main_done()
        progress.zone_done(1)
        survived = True
    except Exception:
        survived = False
    finally:
        pipeline._set_stage = real_set_stage
    check(survived, "a failing progress write (database locked) never raises from main_done / zone_done (L2)")

    # F1 on: the zone answers are already there; never asked twice.
    with fx.a_case(dict(on), {"draft": fx.busy_draft()}) as (client, rid, pids, uid, state):
        asked = kinds()
        listed = [it["part"] for it in t.db_row(Report, rid).data.get("damage_summary") or []]
        outcome = bridge.outcome(rid)
    with fx.a_case(dict(ZONES)) as (client, _rid, _pids, _uid, _state):
        clean = kinds()
    with fx.a_case(dict(ZONES), {"draft": fx.busy_draft()}) as (client, _rid, _pids, _uid, _state):
        per_call = kinds()["draft"]
    check(state["stage"] == "ready" and state.get("read_notice") and asked["zone"] == clean["zone"]
          and asked["draft"] == per_call + len(pipeline.main_read_chain())
          and sorted(listed) == ["Left Tail Lamp", "Rear Bumper"],
          "with F1: both tries busy -> the zone list; the zone reads asked once", (dict(asked), listed))
    # M2: the Telegram summary says so instead of "Finished reading".
    from types import SimpleNamespace

    from app.channels import telegram as tg

    text = tg._detection_text(outcome, "https://carlink.test/x", SimpleNamespace(filing_label=None, label="Tester"))
    check(outcome.read_notice == pipeline.MAIN_READ_FAILED_NOTE and text.startswith("⚠️ The overall read failed")
          and "Finished reading" not in text and "Re-read" in text,
          "the Telegram summary gives the notice, never \"Finished reading\" (M2)", text[:200])
    with fx.a_case(dict(on), {"draft": busy_first(per_call)}) as (
            client, rid, pids, uid, state):
        asked = kinds()
    check(state["stage"] == "ready" and not state.get("read_notice") and asked["zone"] == clean["zone"]
          and asked["draft"] == per_call + 1,
          "with F1: busy once -> the second try answers, the zone reads not repeated", dict(asked))

    # Single mode, or no zone job: B2 changes nothing.
    with fx.a_case({"intake_parallel_reads": "on"}) as (client, rid, pids, uid, state):
        asked = kinds()
    check(state["stage"] == "ready" and asked["zone"] == 0, "single mode: no zone read, read as before")


# ------------------------------------------------------------- 3. B3


def b3_link_check_parallel() -> None:
    second = threading.Event()
    waited: list[bool] = []

    def links_answer(kwargs, labels):
        if sum(1 for r in t.REQUESTS if t.kind_of(r) == "link") >= 2:
            second.set()
        waited.append(second.wait(5))
        return t._link_answer(kwargs, labels)

    def look(values, answers):
        second.clear()
        waited.clear()
        with fx.a_case(values, answers) as (client, rid, pids, uid, state):
            ids = _ids(rid, pids)
            data = t.db_row(Report, rid).data.get("second_look") or {}
            asked = kinds()
        run = dict(data.get("link_run") or {})
        run.pop("seconds", None)
        return t.normalised(data.get("items"), ids), run, asked, list(waited)

    base = {"draft": two_area_draft, "link": links_answer}
    s_items, s_run, s_asked, s_waited = look(dict(LINKS), base)
    p_items, p_run, p_asked, p_waited = look(dict(LINKS, intake_link_check_parallel="on"), base)
    check(s_asked["link"] == 2 and p_asked["link"] == 2 and p_waited == [True, True] and s_waited[0] is False,
          "two areas: off one after the other, on both in flight at once", (s_waited, p_waited))
    check(p_items == s_items and p_run == s_run, "on: the linked parts and the step's record as off",
          _first_diff(p_items, s_items) if p_items != s_items else (p_run, s_run))

    # One area fails (busy): on, the other area's answers stand.
    def rear_busy(kwargs, labels):
        if "Area: the rear of the car." in kwargs["input"][0]["text"]:
            raise fx.Busy()
        return t._link_answer(kwargs, labels)

    items, run, asked, _ = look(dict(LINKS, intake_link_check_parallel="on"), {"draft": two_area_draft,
                                                                              "link": rear_busy})
    checked = [it for it in items or [] if it.get("link_check")]
    check(run.get("status") == "ok" and "Checked 1 of 2 areas." in (run.get("note") or "")
          and link_check.NOTE_BUSY in (run.get("note") or "") and checked
          and all(it.get("linked_from") == ["Left Rear Door"] for it in checked),
          "on, the rear call busy: the other area's answers kept, the note says so", (run, [it["part"] for it in checked]))
    s_items, s_run, s_asked, _ = look(dict(LINKS), {"draft": two_area_draft, "link": rear_busy})
    deadline_probe()
    print(f"         (off, the same failure: {s_run.get('status')}, {s_run.get('note')!r}, "
          f"{sum(1 for it in s_items or [] if it.get('link_check'))} answered)")


def deadline_probe() -> None:
    """The review's M1 (its probe_b3_deadline.py, scaled): three areas,
    the real call_structured and rate limiter, a 2.5 s box, 1 s spacing,
    1.2 s calls. Every area's box used to start at once, so the third spent
    it queueing for its slot and timed out; now at most 2 calls are in
    flight and the second starts one spacing later."""
    from app.ai.keys import AiActor

    lock = threading.Lock()
    live = {"now": 0, "max": 0}

    def slow_link(kwargs, labels):
        with lock:
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
        time.sleep(1.2)
        with lock:
            live["now"] -= 1
        return {"answers": []}

    saved = (link_check.TIME_BOX, link_check.TIMEOUT, link_check.build_input, link_check.system_prompt,
             structured.acquire, t.ANSWERS["link"])
    link_check.TIME_BOX = link_check.TIMEOUT = 2.5
    link_check.build_input = lambda group, order="candidates_first": [{"type": "text", "text": "x"}]
    link_check.system_prompt = lambda *a, **k: "x"
    structured.acquire = rate_limit.acquire
    t.ANSWERS["link"] = slow_link
    actor = AiActor(user_id=None, channel="cli", cli=True)
    groups = [link_check.Group(area=a, skill=a, candidates=[link_check.Candidate(ref="C1", key=f"k-{a}", line=a)])
              for a in ("rear", "left", "front")]
    plan = link_check.Plan(groups=groups, total=3)
    results = {}
    try:
        with t.Settings(gemini_min_call_interval_seconds=1.0, gemini_max_requests_per_minute=12):
            for mode in ("off", "on"):
                time.sleep(1.1)  # past the last request's spacing
                live.update(now=0, max=0)
                with t.Settings(intake_link_check_parallel=mode):
                    outcome, _ = link_check._ask_all("probe-m1", actor, plan)
                results[mode] = (outcome["status"], outcome.get("note"), live["max"])
    finally:
        (link_check.TIME_BOX, link_check.TIMEOUT, link_check.build_input, link_check.system_prompt,
         structured.acquire, t.ANSWERS["link"]) = saved
    check(results["off"][:2] == ("ok", None) and results["on"][:2] == ("ok", None) and results["on"][2] == 2,
          "three areas through the real limiter: all answered off and on; on, at most 2 in flight (M1)", results)


# ------------------------------------------------------------- 4. B4


def b4_sort_workers() -> None:
    lock = threading.Lock()
    state = {"now": 0, "max": 0, "starts": []}

    def slow_sort(kwargs, labels):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
            state["starts"].append(time.monotonic())
        time.sleep(0.4)
        with lock:
            state["now"] -= 1
        return t._sort_answer(labels)

    def most(values):
        state.update(now=0, max=0, starts=[])
        with big_case(SIXTY), fx.a_case(values, {"sort": slow_sort}) as (client, rid, pids, uid, st):
            sorted_all = all(p[0] for p in fx.photos_of(rid))
        return state["max"], sorted_all, list(state["starts"])

    three, ok3, _ = most({})
    five, ok5, _ = most({"intake_sort_workers": 5})
    check(three == 3 and five == 5 and ok3 and ok5 and pipeline.sort_workers() == 3,
          "60 photos: 3 batches in flight by default, 5 with intake_sort_workers 5; all sorted", (three, five))
    # Through the real rate limiter: the five start spaced.
    fake_acquire = structured.acquire
    structured.acquire = rate_limit.acquire
    try:
        with t.Settings(gemini_min_call_interval_seconds=0.25, gemini_max_requests_per_minute=12):
            _, ok, starts = most({"intake_sort_workers": 5})
    finally:
        structured.acquire = fake_acquire
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    check(ok and len(starts) == 5 and min(gaps) >= 0.2, "with the real limiter the 5 starts come spaced (min gap "
                                                        f"{min(gaps) if gaps else 0:.2f} s at 0.25 s)", gaps)


# ------------------------------------------------------------- 5. B5


def b5_keep_sort_label() -> None:
    from sqlalchemy import inspect, text

    check(("intake_photos", "ai_kind", "VARCHAR") in db_module._ADDED_COLUMNS and hasattr(IntakePhoto, "ai_kind"),
          "IntakePhoto.ai_kind, and in db._ADDED_COLUMNS")
    with db_module.engine.begin() as conn:
        conn.execute(text("ALTER TABLE intake_photos DROP COLUMN ai_kind"))
    gone = "ai_kind" not in {c["name"] for c in inspect(db_module.engine).get_columns("intake_photos")}
    db_module._upgrade_schema()
    back = "ai_kind" in {c["name"] for c in inspect(db_module.engine).get_columns("intake_photos")}
    check(gone and back, "an old table without it: the start-up upgrade adds it (old rows stay valid, NULL)")

    def run(values):
        with t.Settings(**values), TestClient(t._app()) as client:
            uid = sign_in(client, role="admin", may_sign_off=True)
            rid, pids = t.new_draft(client)
            t.read(client, rid)
            for pid, kind in ((pids[8], "interior"), (pids[5], "damage"), (pids[5], "tyre"), (pids[2], "damage")):
                assert client.patch(f"/intake/{rid}/photos/{pid}", json={"kind": kind}).status_code == 200
            kept = fx.photos_of(rid)
            db = db_module.SessionLocal()
            try:
                ai_kinds = [p.ai_kind for p in db.query(IntakePhoto).filter(IntakePhoto.report_id == rid)
                            .order_by(IntakePhoto.position)]
            finally:
                db.close()
            state = client.get(f"/intake/{rid}").json()
            assert client.post(f"/intake/{rid}/finalize", json={"data": state["data"]}).status_code == 200
            index = t.db_row(Report, rid).data.get("photo_index")
            code = t.sign_off(client, rid).status_code
            row = t.db_row(AiAccuracy, rid)
            totals = client.get("/admin/ai-accuracy").json().get("totals") or {}
        return kept, ai_kinds, index, code, row, totals

    kept, ai_kinds, index, code, row, totals = run(dict(ZONES))
    check(code == 200 and not any(ai_kinds) and not any("ai_kind" in e for e in index)
          and "regroups" not in (row.metrics or {}) and row.version == 3 and "regroups" not in totals,
          "off: nothing kept, nothing counted (VERSION 3, as before)")
    kept, ai_kinds, index, code, row, totals = run(dict(ZONES, intake_keep_sort_label="on"))
    check(ai_kinds[8] == "damage" and ai_kinds[5] == "overview" and ai_kinds[2] == "damage"
          and sum(1 for k in ai_kinds if k) == 3 and kept[5][0] == "tyre",
          "on: the sorter's kind kept from the first regroup (a second one keeps it)", ai_kinds)
    check([e.get("ai_kind") for e in index] == ai_kinds and index[5]["kind"] == "tyre",
          "filed: photo_index keeps it beside the person's kind", [(e.get("ai_kind"), e["kind"]) for e in index])
    regroups = (row.metrics or {}).get("regroups")
    check(code == 200 and row.version == 3 and regroups == {
        "photos": 10, "moved": 2, "pairs": {"damage>interior": 1, "overview>tyre": 1}},
          "sign-off: regroups counted (moved 2: damage>interior, overview>tyre; the damage photo kept as damage "
          "isn't a move), VERSION 3", regroups)
    check((totals.get("regroups") or {}).get("moved", 0) >= 2 and (totals.get("regroups") or {}).get("cases", 0) >= 1,
          "GET /admin/ai-accuracy sums them (only once some case has them)", totals.get("regroups"))
    check(accuracy.compare({}, {"photo_index": [{"kind": "damage"}]}).get("regroups") is None
          and accuracy.sum_regroups([{"damage": {}}]) is None
          and accuracy.compare({}, {"photo_index": [{"kind": "tyre", "ai_kind": "damage"}, {"kind": "damage"}]})
          ["regroups"] == {"photos": 2, "moved": 1, "pairs": {"damage>tyre": 1}},
          "compare: regroups only on a case whose index keeps a sorter's kind; older rows sum as before")


# ------------------------------------------------------------- 6. B9


def b9_rescue() -> None:
    def rescue_requests():
        return [r for r in t.REQUESTS if t.kind_of(r) == "rescue"]

    with fx.a_case(dict(ZONES)) as (client, rid, pids, uid, state):
        off = kinds()
    check(off["rescue"] == 0, "off: no rescue call")

    interior = {8: ("interior", "interior", None, False)}
    with fx.a_case(dict(ZONES, intake_sort_rescue="on"), {"rescue": rescue_answer({"P09": "rear"})}, interior) as (
            client, rid, pids, uid, state):
        asked = rescue_requests()
        photos = fx.photos_of(rid)
        sort_run = next(r for r in t._run_rows(rid) if r["kind"] == "sort")
        zone_reqs = [r for r in t.REQUESTS if t.kind_of(r) == "zone" and "the car's rear" in r["system_instruction"]]
        note = t.db_row(IntakePhoto, pids[8]).note
    check(len(asked) == 1 and t._labels(asked[0]["input"]) == ["P09", "P10"]
          and asked[0].get("generation_config") == {"thinking_level": "low"},
          "on: one rescue call, thinking low, with just the photos no read would see (interior P09, third party P10)",
          [t._labels(r["input"]) for r in asked])
    check(photos[8] == ("damage", "rear", None, True) and photos[9][0] == "third_party"
          and "first sorted: interior" in (note or ""),
          "a yes puts P09 back as a rear damage photo (its note says so); a no leaves P10", (photos[8], photos[9]))
    check(zone_reqs and "P09" in t._labels(zone_reqs[0]["input"]) and state["stage"] == "ready",
          "the rear zone read sees the rescued photo")
    check(sort_run["photos_sent"] == {"count": 12, "rescue": {"asked": 2, "rescued": 1}},
          "the sort run counts it: asked 2, rescued 1", sort_run["photos_sent"])

    # Skipped by a batch (F7 off): the rescue sort asks them.
    with fx.a_case(dict(ZONES, intake_sort_rescue="on"), {"sort": fx.skipping_sort(),
                                                          "rescue": rescue_answer({"P09": "rear-left"})}) as (
            client, rid, pids, uid, state):
        asked = rescue_requests()
        photos = fx.photos_of(rid)
    check(len(asked) == 1 and t._labels(asked[0]["input"]) == ["P09", "P10"] and photos[8][:2] == ("damage", "rear-left")
          and photos[9][0] is None,
          "skipped photos (no F7): asked; the yes comes back as damage, the no stays unsorted", photos[8:])

    # Nothing to rescue: no call. The call failing: the run goes on.
    no_third = {9: ("damage", "rear", None, True)}
    with fx.a_case(dict(ZONES, intake_sort_rescue="on"), None, no_third) as (client, rid, pids, uid, state):
        none = rescue_requests()
    check(not none and state["stage"] == "ready", "no photo to rescue: no call")

    def busy(kwargs, labels):
        raise fx.Busy()
    with fx.a_case(dict(ZONES, intake_sort_rescue="on"), {"rescue": busy}, interior) as (client, rid, pids, uid, state):
        photos = fx.photos_of(rid)
        sort_run = next(r for r in t._run_rows(rid) if r["kind"] == "sort")
    check(state["stage"] == "ready" and photos[8][0] == "interior" and sort_run["status"] == "ok"
          and (sort_run["photos_sent"].get("rescue") or {}).get("failed") is True,
          "the rescue call failing: the run goes on, the photos as sorted", sort_run["photos_sent"])

    # L5: the likeliest first before the cut at 12 -- skipped, unusable,
    # "other" with no damage, then interior / document / third party / scene.
    many = [("interior", "interior", None, False)] * 20 + [("damage", "rear", "Rear Bumper", True)] * 4
    skipped_once: list[int] = []

    def skip_p22(kwargs, labels):
        out = t._sort_answer(labels)
        if "P22" in labels and not skipped_once:
            skipped_once.append(1)
            out["photos"] = [p for p in out["photos"] if p["label"] != "P22"]
        return out
    with big_case(many), fx.a_case(dict(ZONES, intake_sort_rescue="on"), {"sort": skip_p22}) as (
            client, rid, pids, uid, state):
        asked = rescue_requests()
    labels = t._labels(asked[0]["input"]) if asked else []
    check(len(labels) == 12 and "P22" in labels and labels == sorted(labels),
          "more than 12 candidates: the skipped photo (P22, after 20 interior ones) is among the 12, sent in camera "
          "order (L5)", labels)

    # B5 with a rescued photo: its regroup counts against the rescue sort.
    with t.Settings(**dict(ZONES, intake_sort_rescue="on", intake_keep_sort_label="on")),             fx.fakes({"rescue": rescue_answer({"P09": "rear"})}, interior), TestClient(t._app()) as client:
        sign_in(client, role="admin", may_sign_off=True)
        rid, pids = t.new_draft(client)
        t.read(client, rid)
        assert client.patch(f"/intake/{rid}/photos/{pids[8]}", json={"kind": "interior"}).status_code == 200
        ai_kind = t.db_row(IntakePhoto, pids[8]).ai_kind
        st = client.get(f"/intake/{rid}").json()
        assert client.post(f"/intake/{rid}/finalize", json={"data": st["data"]}).status_code == 200
        t.sign_off(client, rid)
        regroups = (t.db_row(AiAccuracy, rid).metrics or {}).get("regroups")
    check(ai_kind == "rescue:damage" and regroups == {"photos": 10, "moved": 0, "pairs": {"rescue:damage>interior": 1},
                                                      "rescue_moved": 1},
          "a rescued photo regrouped: kept as the rescue's label, counted as rescue_moved, not the sorter's (L5)",
          (ai_kind, regroups))

    class P:
        def __init__(self, **kw):
            self.__dict__.update(dict(excluded=False, kind_source="ai", kind="damage", quality="good",
                                      shows_damage=True), **kw)
    rules = [pipeline.rescue_candidate(P(**kw)) for kw in (
        {}, {"kind": None}, {"quality": "unusable"}, {"kind": "interior"}, {"kind": "document"},
        {"kind": "third_party"}, {"kind": "scene"}, {"kind": "other", "shows_damage": False},
        {"kind": "other", "shows_damage": True}, {"kind": "log_card"}, {"kind": "interior", "excluded": True},
        {"kind": "interior", "kind_source": "surveyor"})]
    ranks = [pipeline.rescue_rank(P(**kw)) for kw in ({"kind": None}, {"quality": "unusable"},
                                                       {"kind": "other", "shows_damage": False}, {"kind": "interior"})]
    check(ranks == [0, 1, 2, 3], "rescue_rank: skipped, unusable, other, then the rest", ranks)
    check(rules == [False, True, True, True, True, True, True, True, False, False, False, False],
          "rescue_candidate: skipped, unusable, interior/document/third_party/scene, other with no damage; never "
          "a fact photo, an excluded one or a person's regroup", rules)
    parsed = triage.parse_rescue(json.dumps({triage.RESCUE_KEY: [
        {"label": "P01", "insured_damage": "yes", "area": "rear"}, {"label": "P02", "insured_damage": "yes",
                                                                   "area": "unclear"},
        {"label": "P09", "insured_damage": "yes", "area": "rear"}]}), ["P01", "P02"])
    check(parsed == {"P01": {"damage": True, "area": "rear"}, "P02": {"damage": True, "area": None}},
          "parse_rescue: the labels sent only; unclear is no area")


def main() -> int:
    t.init_db()
    for name, fn in (
        ("0. settings: defaults and compose", settings_section),
        (f"1. the defaults: byte for byte as master {MASTER}", master_equal),
        ("2. B2: the main read and the zone reads at once", b2_parallel_reads),
        ("3. B3: the linked check's calls at once", b3_link_check_parallel),
        ("4. B4: sort workers", b4_sort_workers),
        ("5. B5: the sorter's label kept on a regroup", b5_keep_sort_label),
        ("6. B9: the rescue sort", b9_rescue),
    ):
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
