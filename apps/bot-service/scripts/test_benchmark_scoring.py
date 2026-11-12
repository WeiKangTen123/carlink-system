"""Offline tests of the intake benchmark's harness: scoring (strict and
fair), summary, compare, AI accounting (tokens per call), sort cache,
leave-one-out Annex A, the model-setting flags, --scratch, --rescore of a
stored result file, and a whole benchmark run with a fake pipeline
(scripts/benchmark_intake.py, scripts/bench/compare.py); contract 8: the
found parts' sides, Replace / Repair and zones, the Annex A prices, and
the owner's gate (scripts/bench/gate.py).

    python scripts/test_benchmark_scoring.py

Synthetic data only: no Gemini call (the pipeline's sorting and reading are
replaced by fakes), no real case, plate or chassis number. Runs on a
temporary database and storage folder.
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))
_tmp = tempfile.mkdtemp(prefix="bench-scoring-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["STORAGE_DIR"] = str(Path(_tmp, "storage"))
os.environ.setdefault("GEMINI_API_KEY", "fake")

import openpyxl  # noqa: E402
from PIL import Image  # noqa: E402

import benchmark_intake as bi  # noqa: E402

_spec = importlib.util.spec_from_file_location("bench_compare", SCRIPTS / "bench" / "compare.py")
compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare)
_spec = importlib.util.spec_from_file_location("bench_gate", SCRIPTS / "bench" / "gate.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def ok(label: str) -> None:
    print(f"    ok   {label}")


# A rear-end case, made up: the key's parts with the wordings a surveyor
# might have used.
KEY_PARTS = [
    {"part": "Rear Bumper", "surveyor": ["Rear bumper fascia", "Rear bumper side garnish LH"]},
    {"part": "Left Tail Lamp", "surveyor": ["Tail lamp LH"]},
    {"part": "Boot Lid", "surveyor": ["Tail gate assy"]},
    {"part": "Boot Lid Lock", "surveyor": ["Tail gate lock striker"]},
    {"part": "Left Rear Bumper Reflector", "surveyor": ["Rear bumper reflector LH"]},
    {"part": "Rear Bumper Beam", "hidden": True, "surveyor": ["Rear bumper beam (inner)"]},
    {"part": "Left Headlamp", "surveyor": ["Headlamps (x2)"]},
    {"part": "Right Headlamp", "surveyor": ["Headlamps (x2)"]},
]


def line(item: str, condition: str = "") -> SimpleNamespace:
    return SimpleNamespace(item=item, condition=condition)


# ---------------------------------------------------------------- CLI


def test_cli() -> None:
    print("command line")
    env = {k: v for k, v in os.environ.items() if k != "BENCHMARK_SCRATCH"}
    env["PYTHONIOENCODING"] = "utf-8"
    script = SCRIPTS / "benchmark_intake.py"
    r = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    for flag in ("--repeat", "--sort-cache", "--reports", "--models", "--triage-models", "--label", "--ref",
                 "--second-look-models", "--no-second-look", "--thinking-level-read", "--thinking-level-sort",
                 "--thinking-level-second-look", "--image-resolution", "--read-tools", "--rescore", "--scratch"):
        assert flag in r.stdout, flag
    ok("--help works without BENCHMARK_SCRATCH and lists the new options")
    out = Path(_tmp, "never.json")
    r = subprocess.run([sys.executable, str(script), "--cases", _tmp, "--key", str(Path(_tmp, "none.json")), "--out", str(out)],
                       capture_output=True, text=True, env=env)
    assert r.returncode != 0 and "Refusing to run" in r.stderr and not out.exists(), (r.returncode, r.stderr)
    ok("a real run without BENCHMARK_SCRATCH=1 is refused before anything is written")
    r = subprocess.run([sys.executable, str(SCRIPTS / "bench" / "compare.py"), "--help"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "--total-only" in r.stdout, r.stderr
    ok("compare.py --help")
    r = subprocess.run([sys.executable, str(SCRIPTS / "bench" / "gate.py"), "--help"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "--key" in r.stdout and "candidates" in r.stdout, r.stderr
    ok("gate.py --help")


# ---------------------------------------------------------------- parts, facts, sorter


def test_score_parts() -> None:
    print("parts")
    drafted = ["Rear Bumper", "Tail Lamp (side undetermined)", "Right Rear Bumper Reflector", "Bonnet",
               "Rear Bumper", "Headlamp (side undetermined)", "Boot Lid", None]
    p = bi.score_parts(KEY_PARTS, drafted)
    assert (p["found"], p["expected"], p["visible_found"], p["visible_expected"]) == (4, 8, 4, 7), p
    assert p["found_parts"] == {"Rear Bumper": "exact", "Boot Lid": "exact",
                                "Left Tail Lamp": "as 'Tail Lamp (side undetermined)'",
                                "Left Headlamp": "as 'Headlamp (side undetermined)'"}, p["found_parts"]
    assert p["matched"] == {"Rear Bumper": "Rear Bumper", "Boot Lid": "Boot Lid",
                            "Left Tail Lamp": "Tail Lamp (side undetermined)",
                            "Left Headlamp": "Headlamp (side undetermined)"}, p["matched"]
    assert p["wrong_side"] == [["Right Rear Bumper Reflector", "Left Rear Bumper Reflector"]], p["wrong_side"]
    assert p["extra"] == ["Bonnet"], p["extra"]  # the second "Rear Bumper" is a repeat, not an extra
    assert p["missed"] == ["Boot Lid Lock", "Rear Bumper Beam", "Right Headlamp"], p["missed"]
    assert p["missed_visible"] == ["Boot Lid Lock", "Right Headlamp"]
    assert p["side_undetermined"] == ["Tail Lamp (side undetermined)", "Headlamp (side undetermined)"]
    ok("exact, side-undetermined (found), wrong side (not found), repeats, extras, hidden parts; matched names kept")
    assert bi.covers("Door (side undetermined)", "Left Front Door") and not bi.covers("Door (side undetermined)", "Left Front Door Glass")
    assert bi.covers("Quarter Panel (side undetermined)", "Right Rear Quarter Panel") and not bi.covers("Left Tail Lamp", "Right Tail Lamp")
    ok("a side-undetermined name covers its family only")


def test_second_look_scoring() -> None:
    print("second look, as if every suggestion were ticked")
    drafted = ["Rear Bumper", "Tail Lamp (side undetermined)", "Bonnet"]
    p = bi.score_parts(KEY_PARTS, drafted)
    suggested = ["Boot Lid Lock", "Right Rear Bumper Reflector", "Boot Lid", "Rear End Panel",
                 "Headlamp (side undetermined)", "Rear Bumper", None]
    s = bi.score_second_look(KEY_PARTS, p, drafted, suggested)
    assert s["suggested"] == 6 and s["added"] == 3 and s["added_visible"] == 3, s
    assert s["added_parts"] == ["Boot Lid Lock", "Boot Lid", "Left Headlamp"], s["added_parts"]
    assert s["wrong_side"] == 1, s  # the right reflector: the key's is on the left
    # Rear End Panel is in no key; Rear Bumper the reading had already (a
    # suggestion never takes a part the reading found).
    assert s["extra"] == 2 and s["extra_parts"] == ["Rear End Panel", "Rear Bumper"], s
    assert (s["found"], s["visible_found"]) == (p["found"] + 3, p["visible_found"] + 3), (s, p)
    ok("found with every suggestion ticked, parts it added, its extras and wrong sides")
    none = bi.score_second_look(KEY_PARTS, p, drafted, [])
    assert (none["found"], none["added"], none["extra"], none["suggested"]) == (p["found"], 0, 0, 0), none
    run = {"parts": p, "drafted_parts": drafted, "vehicle": {}, "parts_with_second_look": s,
           "second_look": {"status": "ok", "calls": 1, "seconds": 21.5}}
    m = bi.run_metrics(run)
    assert (m["look_found"], m["look_added"], m["look_extra"], m["look_wrong_side"], m["look_suggested"]) == (
        p["found"] + 3, 3, 2, 1, 6), m
    assert (m["look_calls"], m["look_s"], m["look_ok"]) == (1, 21.5, 1), m
    assert "look_found" not in bi.run_metrics({"parts": p, "vehicle": {}})
    ok("run metrics: look_found / added / extra / wrong side / suggested / calls / seconds; absent without one")


# Made up: a side swipe down the right, the key in the firm's words.
SIDE_KEY = [
    {"part": "Right Front Fender", "surveyor": ["Front fender assy RH"]},
    {"part": "Right Front Door", "surveyor": ["Front door assy RH", "Front door glass regulator mechanism"]},
    {"part": "Right Front Wheel Arch Liner", "surveyor": ["Front fender splash shield"]},
    {"part": "Right Front Wheel", "hidden": True, "surveyor": ["Front wheel rim RH"]},
    {"part": "Right Rear Door", "surveyor": ["Rear door assy RH"]},
]


def test_fair_score() -> None:
    print("fair score")
    items = [
        {"part": "Right Front Fender"},
        {"part": "Right Rear Quarter Panel"},  # next to the missed rear door
        {"part": "Right Sill / Rocker Panel"},  # next to the missed front door
        {"part": "Left Front Fender"},  # the other side: nothing
        {"part": "Wing Mirror (side undetermined)"},  # no side: never a neighbour
        {"part": "Right Front Tyre"},  # the new name, next to the (hidden) wheel
        {"part": "Other / Not Listed", "other_part_name": "Front door window regulator"},
        {"part": "Other / Not Listed", "other_part_name": "Front badge"},  # "front" places, doesn't name
        {"part": "Other / Not Listed"},
        {"part": "Bonnet"},
    ]
    strict = bi.score_parts(SIDE_KEY, [i["part"] for i in items])
    f = bi.score_fair(SIDE_KEY, items)
    assert (strict["found"], len(strict["extra"])) == (1, 9), strict
    assert (f["found"], f["strict_extra"]) == (1, 9), f
    assert f["neighbour"] == [["Right Rear Quarter Panel", "Right Rear Door"], ["Right Sill / Rocker Panel", "Right Front Door"],
                              ["Right Front Tyre", "Right Front Wheel"]], f["neighbour"]
    assert (f["fair_found"], f["fair_visible_found"]) == (2.5, 2.0), f
    assert f["other_named"] == [["Front door window regulator", "Right Front Door"]], f["other_named"]
    assert f["extra"] == ["Left Front Fender", "Wing Mirror (side undetermined)", "Other / Not Listed", "Other / Not Listed",
                          "Bonnet"] and f["fair_extra"] == 5, f["extra"]
    assert f["contradicted"] is None
    ok("neighbour on the same side at 0.5 (hidden key parts not in the visible count); Other named like a surveyor line not extra")
    assert bi.score_fair(SIDE_KEY, items, strict) == f
    ok("the strict numbers are score_parts' own, unchanged")

    f = bi.score_fair([{"part": "Right Front Door"}], [{"part": "Right Front Door"}, {"part": "Right Rear Door"}])
    assert f["neighbour"] == [] and f["extra"] == ["Right Rear Door"], f
    f = bi.score_fair([{"part": "Right Rear Door"}], [{"part": "Right Front Door"}, {"part": "Right Rear Quarter Panel"}])
    assert f["neighbour"] == [["Right Front Door", "Right Rear Door"]] and f["extra"] == ["Right Rear Quarter Panel"], f
    f = bi.score_fair([{"part": "Right Front Door"}], [{"part": "Left Front Door"}, {"part": "Left Front Fender"}])
    assert f["neighbour"] == [] and f["extra"] == ["Left Front Fender"] and f["fair_found"] == 0, f  # wrong side, not extra
    ok("only next to a key part the strict score missed; one neighbour per key part; a wrong side is not a neighbour")
    assert bi.neighbours("Left Front Wheel Arch Liner", "Left Front Wheel") and bi.neighbours("Left Rear Wheel Arch Trim", "Left Rear Quarter Panel")
    assert bi.neighbours("Left Sill / Rocker Panel", "Left Rear Door") and not bi.neighbours("Left Front Fender", "Left Rear Door")
    assert not bi.neighbours("Left Front Wheel Arch Liner", "Left Rear Wheel") and not bi.neighbours("Left Front Door", "Right Front Fender")
    ok("the panels that meet: fender-front door-rear door-quarter, sill-doors, arch-fender/quarter, arch liner-wheel")

    run = {"parts": strict, "drafted_parts": [i["part"] for i in items], "vehicle": {},
           "parts_fair": bi.score_fair(SIDE_KEY, items, strict, [["Bonnet", "Bonnet assy"]])}
    m = bi.run_metrics(run)
    assert (m["fair_found"], m["fair_visible_found"], m["fair_extra"], m["neighbour"], m["other_named"], m["contradicted"]) == (
        2.5, 2.0, 5, 3, 1, 1), m
    assert "contradicted" not in bi.run_metrics(dict(run, parts_fair=bi.score_fair(SIDE_KEY, items, strict)))
    ok("run metrics: fair found / visible / extra, neighbours, Other named, contradicted (absent without a report)")


def test_contradicted() -> None:
    print("contradicted by the report")
    from app.parts import history

    lines = [line("Front fender assy RH", "Dented"), line("Rear door assy RH", "Serviceable"),
             line("Rear door moulding RH", "Serviceable"), line("Front door assy LH", "Serviceable"),
             line("Front door trim RH", "Necessary"), line("Rear wheel rim LH", "Serviceable"),
             line("Headlamps (x2)", "Serviceable")]
    drafted = ["Right Front Fender", "Right Rear Door", "Right Front Door", "Left Front Wheel", "Left Headlamp", "Bonnet",
               "Other / Not Listed", "Right Rear Door", None]
    got = bi.contradicted_parts(drafted, lines, history.family_of, history.side_of, history.family_and_side)
    assert got == [["Right Rear Door", "Rear door assy RH"], ["Left Headlamp", "Headlamps (x2)"]], got
    ok("every line of the part's family, side and end Serviceable: flagged once; a damage or Necessary line, "
       "another side or end, or no line at all: not")


def test_vehicle() -> None:
    expected = {"plate_number": "TEST 1 A", "make": "Honda", "model": "Vezel", "color": "White",
                "vin": "TESTVIN0000000001", "engine_number": "TESTENG1", "date_of_registration": "2020-01-15"}
    got = {"plate_number": "test1a", "make": "HONDA", "model": "Vezel 1.5", "color": "Pearl White",
           "vin": "TESTVIN0000000002", "date_of_registration": "15 Jan 2020"}
    v = bi.score_vehicle(expected, got)
    assert {k: x["verdict"] for k, x in v.items()} == {
        "plate_number": "ok", "make": "ok", "model": "ok", "color": "ok", "vin": "WRONG",
        "engine_number": "missing", "date_of_registration": "ok"}, v
    ok("vehicle facts: ok / WRONG / missing as before")


def test_sorter_quality() -> None:
    print("sorter quality")
    photos = [
        {"part": "Rear Bumper", "read": True},
        {"part": "Rear Bumper", "read": False},
        {"part": "Tail Lamp (side undetermined)", "read": False},
        {"part": "Bonnet", "read": True},  # a part the surveyor didn't claim
        {"part": None, "read": True},
        {"part": "Rear Bumper Beam", "read": False},  # hidden in the key
    ]
    s = bi.sorter_quality(KEY_PARTS, photos)
    assert (s["tagged_photos"], s["tagged_in_key"], s["label_precision"]) == (5, 4, 0.8), s
    assert (s["key_parts"], s["key_parts_photographed"], s["photographed_recall"]) == (8, 3, 0.375), s
    assert (s["visible_key_parts"], s["visible_photographed"]) == (7, 2), s
    assert (s["key_photos"], s["key_photos_read"], s["selection_coverage"]) == (4, 1, 0.25), s
    assert s["key_parts_read"] == 1 and s["photographed_not_read"] == ["Left Tail Lamp", "Rear Bumper Beam"], s
    assert s["tags_not_in_key"] == ["Bonnet"] and "Boot Lid" in s["not_photographed"], s
    ok("label precision, photographed-part recall and selection coverage")
    empty = bi.sorter_quality(KEY_PARTS, [])
    assert empty["label_precision"] is None and empty["photographed_recall"] == 0.0
    ok("no tagged photos: precision is unknown (None), not 0")


# ---------------------------------------------------------------- conditions


def test_conditions() -> None:
    print("conditions")
    A = bi._app()
    from app.parts import history

    assert bi.FITTING_CONDITIONS == set(history.FITTING_CONDITIONS)
    ok("FITTING_CONDITIONS matches app.parts.history")
    lines = [
        line("Rear bumper fascia", "Grazed/cracked"),
        line("Rear bumper side garnish LH", "Necessary"),
        line("Tail lamp LH", "Cracked"),
        line("Tail gate assy", "Dented/buckled"),
        line("Tail gate lock striker", "Necessary"),  # refitted only: no damage condition
        line("Headlamps", "Grazed/cracked"),  # the key wrote "Headlamps (x2)"
        line("Rear bumper reflector RH", "Cracked"),  # the other side's reflector
    ]
    by = {p["part"]: p for p in KEY_PARTS}
    sc = lambda part: bi.surveyor_condition(by[part], lines, A.normalise_item, A.side_of)  # noqa: E731
    assert sc("Rear Bumper") == {"item": "Rear bumper fascia", "condition": "Grazed/cracked"}
    assert sc("Boot Lid Lock") is None
    assert sc("Left Headlamp") == {"item": "Headlamps", "condition": "Grazed/cracked"}
    assert sc("Left Rear Bumper Reflector") is None
    assert bi.surveyor_condition(by["Left Rear Bumper Reflector"], lines, A.normalise_item, lambda _: None)["condition"] == "Cracked"
    ok("surveyor condition: first damage line of the part's wordings; '(x2)' wordings by normalised name; never the other side's line")

    items = [
        {"part": "Rear Bumper", "damage_type": "Crack", "condition": "Grazed/Cracked"},
        {"part": "Tail Lamp (side undetermined)", "damage_type": "Crack", "condition": None},
        {"part": "Boot Lid", "damage_type": "Dent", "condition": "Buckled/dented"},
        {"part": "Headlamp (side undetermined)", "damage_type": "Scratch", "condition": "Grazed"},
        {"part": "Bonnet", "damage_type": "Scratch"},
        {"part": "Boot Lid Lock", "damage_type": "Bent"},
    ]
    parts = bi.score_parts(KEY_PARTS, [i["part"] for i in items])
    firm = {w for ln in lines for w in bi.words(ln.condition)}
    c = bi.score_conditions(KEY_PARTS, parts["matched"], items, lines, firm, A.normalise_item, A.side_of)
    assert (c["found_parts"], c["scored"], c["no_surveyor_condition"]) == (5, 4, 1), c
    assert (c["exact"], c["first_token"], c["token_hits"], c["tokens"], c["token_recall"]) == (1, 2, 5, 7, 0.714), c
    assert (c["drafted_conditions"], c["firm_words_only"], c["firm_words_share"]) == (6, 3, 0.5), c
    assert c["words_not_in_firm_reports"] == ["bent", "crack", "scratch"], c
    rows = {d["part"]: d for d in c["details"]}
    assert rows["Left Tail Lamp"]["source"] == "damage_type" and rows["Left Tail Lamp"]["drafted"] == "Crack"
    assert rows["Boot Lid"]["source"] == "condition" and rows["Boot Lid"]["exact"] is False and rows["Boot Lid"]["token_hits"] == 2
    assert rows["Rear Bumper"]["exact"] is True and rows["Left Headlamp"]["first_token"] is True
    assert "exact" not in rows["Boot Lid Lock"]
    ok("exact (any case), first word, word recall; condition before damage_type; firm-word share over every drafted item")
    assert bi.condition_tokens("Dented / Buckled ") == ["dented", "buckled"] and bi.condition_tokens(None) == []


# ---------------------------------------------------------------- summary and compare


def fake_run(case: str, rep: int, found: int, extra: int, sort_s: float, covered: int, lines: int, error: str | None = None) -> dict:
    run = {
        "case": case, "rep": rep, "read": 30, "sort_seconds": sort_s, "read_seconds": 20.0,
        "drafted_parts": ["Rear Bumper", "Tail Lamp (side undetermined)"],
        "parts": {"found": found, "expected": 10, "visible_found": found - 1, "visible_expected": 9,
                  "extra": ["Bonnet"] * extra, "wrong_side": [], "found_parts": {}, "missed": []},
        "vehicle": {"make": {"verdict": "ok"}, "vin": {"verdict": "WRONG"}},
        "annex_a": {"real_lines": lines, "covered": covered, "rows": 5, "priced": 4, "unpriced": 1, "extra": 2,
                    "canonical_rows": 1, "perfect_covered": lines - 1},
    }
    if error:
        run["error"] = error
    return run


def test_summary() -> None:
    print("summary over repeats")
    runs = [
        fake_run("CASE A", 1, 4, 1, 60.0, 1, 10), fake_run("CASE B", 1, 6, 0, 40.0, 9, 30),
        fake_run("CASE A", 2, 5, 2, 0.1, 1, 10), fake_run("CASE B", 2, 8, 0, 0.1, 9, 30),
        fake_run("CASE A", 3, 6, 0, 0.1, 1, 10), fake_run("CASE B", 3, 0, 0, 0.1, 0, 30, error="RuntimeError: quota"),
    ]
    s = bi.summarize(runs)
    assert (s["cases_n"], s["reps"], s["complete_reps"]) == (2, 3, [1, 2]), s
    a = s["cases"]["CASE A"]["metrics"]
    assert a["found"] == {"mean": 5.0, "min": 4, "max": 6, "n": 3}, a["found"]
    assert a["side_undetermined"]["mean"] == 1 and a["facts_ok"]["mean"] == 1
    b = s["cases"]["CASE B"]
    assert b["runs"] == 3 and b["errors"] == 1 and b["metrics"]["found"] == {"mean": 7.0, "min": 6, "max": 8, "n": 2}
    ok("per case: mean, min, max over its runs; an errored run is counted apart, not averaged in as zeros")
    t = s["total"]
    assert t["found"] == {"mean": 11.5, "min": 10, "max": 13, "n": 2}, t["found"]
    assert t["sort_s"]["max"] == 100.0 and t["extra"]["max"] == 2
    # 10/40 over both cases, not the mean of 10% and 30%.
    assert t["annex_coverage"] == {"mean": 0.25, "min": 0.25, "max": 0.25, "n": 2}, t["annex_coverage"]
    assert s["errors"] == [{"case": "CASE B", "rep": 3, "error": "RuntimeError: quota"}]
    ok("total: per complete repetition the sum over cases; ratios from summed counts")


def test_old_format() -> None:
    old = {  # a result as written before --repeat: no rep, accounting or sorter block
        "case": "CASE A", "read": 32, "photos": 40, "sort_seconds": 66.0, "read_seconds": 22.0,
        "drafted_parts": ["Rear Bumper", "Bonnet"],
        "parts": bi.score_parts(KEY_PARTS[:3], ["Rear Bumper", "Bonnet"]),
        "parts_with_checklist": bi.score_parts(KEY_PARTS[:3], ["Rear Bumper", "Bonnet", "Boot Lid"]),
        "vehicle": {"make": {"verdict": "ok"}},
        "sorted_photos": [{"part": "Rear Bumper", "read": True}, {"part": "Boot Lid", "read": False}, {"part": None, "read": True}],
        "calls": {"photo sort": 4, "draft": 1}, "answered_by": {"draft: test-model": 1},
    }
    for key in ("matched", "side_undetermined"):  # not in the older files either
        old["parts"].pop(key)
        old["parts_with_checklist"].pop(key)
    m = bi.run_metrics(old)
    assert (m["found"], m["extra"], m["checklist_found"], m["photos_read"], m["sort_s"]) == (1, 1, 2, 32, 66.0), m
    assert (m["sorter_tagged"], m["sorter_tagged_in_key"], m["key_parts_photographed"], m["key_photos_read"]) == (2, 2, 2, 1), m
    assert "calls" not in m and "annex_coverage" not in m
    s = bi.summarize([old])
    assert s["complete_reps"] == [1] and s["total"]["found"]["mean"] == 1
    ok("files from before --repeat ({'results': [...]}) still summarise, sorter numbers included")


def test_compare() -> None:
    print("compare")
    def runs(founds, extras):
        return [fake_run("CASE A", i + 1, f, e, 1.0, 1, 10) for i, (f, e) in enumerate(zip(founds, extras))]

    before = bi.summarize(runs([4, 6, 5], [1, 1, 2]))["total"]
    noise = bi.summarize(runs([5, 7, 6], [2, 1, 1]))["total"]
    better = bi.summarize(runs([9, 10, 9], [4, 5, 4]))["total"]
    single = bi.summarize(runs([7], [1]))["total"]
    v = {r["metric"]: r for r in compare.compare_metrics(before, noise)}
    assert v["found"]["verdict"] == "inside noise" and v["found"]["delta"] == 1.0, v["found"]
    v = {r["metric"]: r for r in compare.compare_metrics(before, better)}
    assert v["found"]["verdict"] == "UP (better)" and v["extra"]["verdict"] == "UP (worse)", (v["found"], v["extra"])
    assert v["photos_read"]["verdict"] == "same"
    v = {r["metric"]: r for r in compare.compare_metrics(before, single)}
    assert v["found"]["verdict"] == "1 run: noise unknown"
    assert compare.verdict("sort_s", {"mean": 60, "min": 50, "max": 70, "n": 3}, {"mean": 2, "min": 1, "max": 3, "n": 3}) == "DOWN (better)"
    assert compare.verdict("found", None, {"mean": 1, "min": 1, "max": 1, "n": 1}) == "only after"
    ok("overlapping ranges are 'inside noise'; separate ranges UP/DOWN with better/worse; a single run says noise is unknown")

    old_file, new_file = Path(_tmp, "old.json"), Path(_tmp, "new.json")
    old_file.write_text(json.dumps({"results": [fake_run("CASE A", 1, 4, 1, 60.0, 1, 10)], "totals": {}}), encoding="utf-8")
    new_file.write_text(json.dumps({"label": "after", "git_ref": "test-branch@0000000", "args": {},
                                    "runs": runs([5, 6, 7], [1, 1, 1]), "summary": {}}), encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert compare.main([str(old_file), str(new_file)]) == 0
    text = buf.getvalue()
    assert "before: old" in text and "after : after (test-branch@0000000)" in text and "1 run: noise unknown" in text, text
    assert "CASE A" in text and "found" in text
    ok("compare.py reads an old-format and a new-format file and prints the before/after tables")


# ---------------------------------------------------------------- accounting


def test_accounting() -> None:
    print("AI accounting")
    A = bi._app()
    assert A.triage.call_structured._bench_wrapped and A.extraction.call_structured._bench_wrapped
    assert A.structured.call_structured._bench_wrapped and A.runs.note_attempt._bench_wrapped
    ok("call_structured is wrapped at its source, so triage and extraction call the counting wrapper")

    def fake_gemini(*, purpose, script, input_parts=None):
        # What call_structured reports per try (structured._note_attempt),
        # key fields included, through the real runs.note_attempt.
        for attempt in script:
            A.runs.note_attempt(dict(attempt, key_source="company", key_last4="ZZZZ"))
        if script[-1]["outcome"] != "ok":
            raise RuntimeError("all models failed")
        return "answer"

    counted = bi._counting(fake_gemini)
    bi.ACCOUNTING.clear()
    counted(purpose="photo sort", script=[{"model": "lite-a", "outcome": "429", "waited_s": 2.0, "ms": 300},
                                          {"model": "lite-a", "outcome": "ok", "waited_s": 5.5, "ms": 4000}])
    worker = threading.Thread(target=counted, kwargs={"purpose": "photo sort", "script": [
        {"model": "lite-a", "outcome": "ok", "waited_s": 1.0, "ms": 2000}]})
    worker.start()
    worker.join()
    counted(purpose="draft", script=[{"model": "strong", "outcome": "timeout", "waited_s": 0.0, "ms": 120000},
                                     {"model": "lite-b", "outcome": "ok", "waited_s": 4.0, "ms": 9000}])
    try:
        counted(purpose="draft", script=[{"model": "strong", "outcome": "error", "waited_s": 0, "ms": 100}])
        raise AssertionError("the failed call should raise")
    except RuntimeError:
        pass
    s = bi.ACCOUNTING.summary()
    sort, draft = s["photo sort"], s["draft"]
    assert (sort["calls"], sort["failed_calls"], sort["attempts"], sort["n_429"]) == (2, 0, 3, 1), sort
    assert (sort["wait_429_s"], sort["slot_wait_s"]) == (5.5, 3.0), sort
    assert sort["by_model"] == {"lite-a": {"attempts": 3, "answered": 2, "seconds": 6.3, "outcomes": {"429": 1, "ok": 2}}}, sort
    assert (draft["calls"], draft["failed_calls"], draft["attempts"], draft["slot_wait_s"], draft["n_429"]) == (2, 1, 3, 4.0, 0), draft
    assert draft["outcomes"] == {"timeout": 1, "ok": 1, "error": 1}
    assert draft["by_model"]["strong"]["answered"] == 0 and draft["by_model"]["lite-b"]["answered"] == 1
    assert dict(bi.ACCOUNTING.answered_by()) == {"photo sort: lite-a": 2, "draft: lite-b": 1}
    assert dict(bi.ACCOUNTING.call_counts()) == {"photo sort": 2, "draft": 2}
    assert "ZZZZ" not in json.dumps([bi.ACCOUNTING.attempts, s]) and "key_last4" not in json.dumps(s)
    ok("per purpose and model: calls, tries, answers, seconds; slot waits apart from 429 waits; pool threads keep their purpose")
    ok("nothing of the key (not even its last 4) is kept")
    m = bi.run_metrics({"parts": bi.score_parts([], []), "vehicle": {}, "accounting": s})
    assert (m["calls"], m["attempts"], m["n_429"], m["wait_429_s"], m["slot_wait_s"]) == (4, 6, 1, 5.5, 7.0), m
    merged = bi.merge_accounting([s, s])
    assert merged["photo sort"]["calls"] == 4 and merged["photo sort"]["by_model"]["lite-a"]["seconds"] == 12.6
    ok("run metrics and the merge over runs")
    assert "read_tok_in" not in m and "prompt_tokens" not in s["draft"], (m, s["draft"])
    ok("no tokens reported: no token numbers (never a made-up 0)")

    bi.ACCOUNTING.clear()
    image = {"type": "image", "data": "x"}
    counted(purpose="draft", input_parts=[{"type": "text", "text": "Photo P01:"}, dict(image, resolution="ultra_high"), image, image],
            script=[{"model": "strong", "outcome": "error", "waited_s": 0, "ms": 100},
                    {"model": "lite-b", "outcome": "ok", "waited_s": 0, "ms": 9000, "prompt_tokens": 30000,
                     "output_tokens": 2000, "thought_tokens": 800, "tool_calls": 2}])
    counted(purpose="draft", script=[{"model": "lite-b", "outcome": "ok", "waited_s": 0, "ms": 5000, "prompt_tokens": 1000,
                                      "output_tokens": 100}])
    calls = bi.ACCOUNTING.call_list()
    assert calls[0] == {"purpose": "draft", "seconds": calls[0]["seconds"], "ok": True, "model": "lite-b", "attempts": 2,
                        "images": {"ultra_high": 1, "default": 2}, "prompt_tokens": 30000, "output_tokens": 2000,
                        "thought_tokens": 800, "tool_calls": 2}, calls[0]
    assert "thought_tokens" not in calls[1] and "images" not in calls[1] and calls[1]["prompt_tokens"] == 1000, calls[1]
    s = bi.ACCOUNTING.summary()
    d = s["draft"]
    assert (d["prompt_tokens"], d["output_tokens"], d["thought_tokens"], d["tool_calls"]) == (31000, 2100, 800, 2), d
    assert d["by_model"]["lite-b"]["prompt_tokens"] == 31000 and "prompt_tokens" not in d["by_model"]["strong"], d["by_model"]
    m = bi.run_metrics({"parts": bi.score_parts([], []), "vehicle": {}, "accounting": s})
    assert (m["read_tok_in"], m["read_tok_out"], m["read_tok_think"], m["read_code_runs"]) == (31000, 2100, 800, 2), m
    assert bi.merge_accounting([s, s])["draft"]["thought_tokens"] == 1600
    ok("per call: the model that answered, tries, tokens (prompt, output, thinking), code runs, images by resolution; "
       "summed per kind of call and model; read_tok_* run metrics")


# ---------------------------------------------------------------- sort cache


def add_report(db, A, photos: list[tuple[str, str, dict | None]]) -> str:
    """A draft with photos (name, sha256, sorted fields or None)."""
    report = A.Report(channel="manual", reporter_chat_id="test", status="intake", data={})
    db.add(report)
    db.flush()
    db.add(A.IntakeSession(report_id=report.id))
    for position, (name, sha, sort) in enumerate(photos):
        db.add(A.IntakePhoto(report_id=report.id, position=position, original_name=name, path=str(Path(_tmp, name)),
                             sha256=sha, size_bytes=1, **(sort or {})))
    db.commit()
    return report.id


def test_sort_cache() -> None:
    print("sort cache")
    A = bi._app()
    A.init_db()
    sha_a, sha_b, sha_c = "a" * 64, "b" * 64, "c" * 64
    damage = {"kind": "damage", "area": "rear", "quality": "good", "shows_damage": True, "part": "Boot Lid",
              "note": "Tail gate dented near the lock"}
    overview = {"kind": "overview", "area": "rear", "quality": "blurry", "shows_damage": False, "part": None, "note": None}
    db = A.SessionLocal()
    try:
        first = add_report(db, A, [("p1.jpg", sha_a, damage), ("p2.jpg", sha_b, overview), ("p3.jpg", sha_c, None),
                                   ("p4.jpg", "../../not-a-hash", damage)])
        second = add_report(db, A, [("q1.jpg", sha_a, None), ("q2.jpg", sha_c, None)])
    finally:
        db.close()
    root = Path(_tmp, "sortcache")
    cache, meta = bi.sort_cache_dir(root)
    assert meta["sort_prompt_version"] == str(getattr(A.triage, "SORT_PROMPT_VERSION", "v0"))
    assert meta["triage_model_chain"] and len(meta["prompt_fingerprint"]) == 12 and (cache / "meta.json").exists()
    assert bi.store_sort_cache(first, cache) == 2
    assert sorted(p.name for p in cache.iterdir()) == sorted([f"{sha_a}.json", f"{sha_b}.json", "meta.json"])
    assert bi.store_sort_cache(first, cache) == 0
    ok("sorted photos are kept by sha256 (unsorted ones and odd hashes are not); a second store adds nothing")
    assert bi.apply_sort_cache(second, cache) == 1
    db = A.SessionLocal()
    try:
        q1, q2 = A.ordered_photos(db, second)
        assert {f: getattr(q1, f) for f in bi.SORT_FIELDS} == damage and q2.kind is None
    finally:
        db.close()
    ok("a cached photo gets the stored result; an uncached one stays for the sorter")

    chain = A.settings.gemini_triage_model_chain
    try:
        A.settings.gemini_triage_model_chain = "test-lite-z"
        other_chain, _ = bi.sort_cache_dir(root)
    finally:
        A.settings.gemini_triage_model_chain = chain
    had_version = hasattr(A.triage, "SORT_PROMPT_VERSION")
    version = getattr(A.triage, "SORT_PROMPT_VERSION", None)
    A.triage.SORT_PROMPT_VERSION = "test-v9"
    try:
        other_version, meta9 = bi.sort_cache_dir(root)
    finally:
        if had_version:
            A.triage.SORT_PROMPT_VERSION = version
        else:
            del A.triage.SORT_PROMPT_VERSION
    prompt = A.triage.SYSTEM_PROMPT
    try:
        A.triage.SYSTEM_PROMPT = prompt + " (changed)"
        other_prompt, _ = bi.sort_cache_dir(root)
    finally:
        A.triage.SYSTEM_PROMPT = prompt
    assert len({cache, other_chain, other_version, other_prompt}) == 4 and meta9["sort_prompt_version"] == "test-v9"
    assert bi.sort_cache_dir(root)[0] == cache
    ok("the cache is per triage chain, SORT_PROMPT_VERSION and prompt fingerprint")
    import hashlib

    chain_text = ",".join(m.strip() for m in A.settings.gemini_triage_model_chain.split(",") if m.strip())
    old_tag = hashlib.sha256(f"{chain_text}|{meta['sort_prompt_version']}|{meta['prompt_fingerprint']}".encode("utf-8")).hexdigest()[:16]
    assert cache.name == old_tag and "thinking_level_sort" not in meta
    try:
        A.settings.gemini_thinking_level_sort = "low"
        thinking, meta_low = bi.sort_cache_dir(root)
    finally:
        A.settings.gemini_thinking_level_sort = None
    assert thinking != cache and meta_low["thinking_level_sort"] == "low"
    ok("a sorting thinking level gets its own folder; unset, the folder is the one filled before the setting existed")


# ---------------------------------------------------------------- reports and Annex A


def report_xlsx(path: Path, ref: str, plate: str, make_model: str, lines: list[tuple]) -> None:
    """A spreadsheet laid out like the firm's CL reports (as in test_parts_history)."""
    book = openpyxl.Workbook()
    cover = book.active
    cover.title = "Cover Page"
    cover.append([None, "ACCIDENT VEHICLE INSPECTION / ASSESSMENT REPORT"])
    cover.append([None, f"Our  Ref: {ref} {plate}"])
    cover.append([None, None, "Date of survey", ":", "27th January 2018"])
    cover.append([None, None, "Registration No.", ":", plate])
    cover.append([None, None, "Make & Model", ":", make_model])
    cover.append([None, None, "Date of registration", ":", "12th January 2017"])
    annex = book.create_sheet("Annex A")
    annex.append([None, None, plate])
    annex.append([None, "Item", "Vehicle Parts Description", "Condition", "Remarks", "Qty", "W/shop   Est. ($)", "Disc       (%)", "Adjusted    Cost ($)"])
    annex.append([None, None, "List Items"])
    for i, (item, cond, qty, est, adj) in enumerate(lines, start=1):
        annex.append([None, float(i), item, cond, "Replace", qty, est, 0.2, adj])
    annex.append([None, None, "Total for Spare Parts :", None, None, None, 0, None, 0])
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)


# Made-up references and plates; the vehicle is a model the firm sees often.
REPORT_A = [("Tail gate assy", "Dented/bent", 1, 1450, 1160), ("Tail gate lock striker", "Distorted/slack", 1, 90, 72),
            ("Tail lamp LH", "Grazed/cracked", 1, 652, 521.6), ("Tail gate stopper", "Necessary", 2, 50, 40)]
REPORT_B = [("Tail gate", "Buckled", 1, 1400, 1120), ("Tail gate lock striker", "Bent", 1, 100, 80),
            ("Rear bumper fascia", "Grazed", 1, 800, 640)]
REPORT_C = [("Tail gate assy", "Dented", 1, 1500, 1200), ("Tail gate lock striker", "Bent", 1, 95, 76),
            ("Tail gate stopper", "Necessary", 2, 50, 40), ("Tail lamp LH", "Cracked", 1, 600, 480)]


def make_reports(folder: Path) -> None:
    report_xlsx(folder / "TEST A" / "CL 90001 TEST-A.xlsx", "CL 90001", "TEST-A", "Honda Vezel 1.5X CVT", REPORT_A)
    report_xlsx(folder / "CL 90002 TEST-B.xlsx", "CL 90002", "TEST-B", "Honda Vezel 1.5X CVT", REPORT_B)
    report_xlsx(folder / "older" / "CL 90003 TEST-C.xlsx", "CL 90003", "TEST-C", "Honda Vezel 1.5X CVT", REPORT_C)
    (folder / "notes.txt").write_text("not a report", encoding="utf-8")
    book = openpyxl.Workbook()
    book.active.title = "Something else"
    book.save(folder / "not-a-report.xlsx")


def test_reports_and_history() -> None:
    print("reports, leave-one-out history")
    A = bi._app()
    A.init_db()
    folder = Path(_tmp, "reports-unit")
    make_reports(folder)
    reports = bi.ReportSet(folder)
    assert len(reports.items) == 3 and reports.skipped == 1, (len(reports.items), reports.skipped)
    assert {"dented", "bent", "necessary", "buckled", "grazed", "cracked", "distorted", "slack"} == reports.firm_words
    by_plate = reports.for_case({"name": "CASE B", "folder": "elsewhere", "vehicle": {"plate_number": "test b"}})
    assert by_plate[1].ref == "CL 90002"
    by_folder = reports.for_case({"name": "CASE A", "folder": "TEST A", "vehicle": {"plate_number": "NO MATCH"}})
    assert by_folder[1].ref == "CL 90001"
    assert reports.for_case({"name": "X", "folder": "none", "vehicle": {"plate_number": "NONE"}}) is None
    ok("reports are found by plate, else by their folder; other spreadsheets are skipped; the firm's condition words")
    bi.load_history(reports.others(by_plate))
    db = A.SessionLocal()
    try:
        refs = {r for (r,) in db.query(A.PastAnnexLine.case_ref).distinct()}
    finally:
        db.close()
    assert refs == {"CL 90001", "CL 90003"}, refs
    ok("history holds every report but the held-out one")


def test_annex_scoring() -> None:
    print("Annex A scoring")
    A = bi._app()
    held = SimpleNamespace(make="Honda", model="Vezel", lines=[
        priced("Tail gate assy", 1050, 840), priced("Tail gate lock striker", 100, 80),
        priced("Tail lamp LH", 500, 400), priced("Tail lamp RH", 500, 400)])
    seen = []

    def fake_prefill(db, data):
        seen.append(data)
        data["damage_summary"][0]["part"] = "changed by prefill"
        if len(seen) == 1:
            rows = [("Tail gate", 1000), ("Tail gate lock striker", 90), ("Bonnet", 0), ("Rear Bumper", 0), ("Boot lid emblem", 50)]
        else:  # the report's own damaged parts: perfect detection
            rows = [("Tail gate assy", 1200), ("Tail gate lock striker", 80), ("Tail lamp LH", 500)]
        return {"annex_a": {"items": [{"part_name": n, "workshop_est": e} for n, e in rows]}}

    items = [{"part": "Boot Lid", "damage_type": "Dent", "condition": "Dented"}, {"part": "Bonnet", "damage_type": "Scratch"}]
    original = A.prefill_annex_a
    A.prefill_annex_a = fake_prefill
    try:
        a = bi.score_annex_a(held, items)
    finally:
        A.prefill_annex_a = original
    assert seen[0]["vehicle_info"] == {"make": "Honda", "model": "Vezel"} and seen[0]["damaged_parts"] == ["Boot Lid", "Bonnet"]
    assert items[0]["part"] == "Boot Lid", "prefill gets a copy: the run's drafted items must not change"
    assert seen[1]["damaged_parts"] == ["Boot Lid", "Boot Lid Lock", "Left Tail Lamp", "Right Tail Lamp"], seen[1]["damaged_parts"]
    assert (a["real_lines"], a["rows"], a["priced"], a["unpriced"]) == (3, 5, 3, 2), a
    assert (a["covered"], a["coverage"], a["extra"]) == (2, 0.667, 3), a
    assert (a["canonical_rows"], a["canonical_names"]) == (2, ["Bonnet", "Rear Bumper"]), a
    assert (a["perfect_rows"], a["perfect_covered"], a["perfect_coverage"]) == (3, 3, 1.0), a
    ok("lines covered (LH/RH one wording, as eval_parts_kits counts), rows priced/unpriced, extra wordings, canonical names, perfect-detection ceiling")
    # Tail gate 1000 for 1050 and the striker 90 for 100: both within 10%;
    # perfect detection's 1200 and 80 are not, its lamp 500 against the
    # two lamps' 1000 neither.
    assert (a["price_total"], a["report_price_total"], a["price_diff"]) == (1140, 2150, -1010), a
    assert (a["matched_priced"], a["within_10pct"], a["perfect_within_10pct"], a["perfect_price_diff"]) == (2, 2, 0, -370), a
    ok("the prices of the rows against the surveyor's: totals, matched lines priced and within 10%, at the ceiling too")


# ---------------------------------------------------------------- a whole run, faked


def test_benchmark_run() -> None:
    print("a whole benchmark run (pipeline faked)")
    A = bi._app()
    A.init_db()
    base = Path(_tmp, "e2e")
    cases_dir, reports_dir, cache_dir, out = base / "cases", base / "reports", base / "sortcache", base / "out" / "run.json"
    for folder, colours in (("case_a", ("red", "green", "blue")), ("case_b", ("white", "black", "gray"))):
        (cases_dir / folder).mkdir(parents=True)
        for i, colour in enumerate(colours, start=1):
            Image.new("RGB", (48, 32), colour).save(cases_dir / folder / f"p{i}.jpg", "JPEG")
    make_reports(reports_dir)
    vehicle = {"make": "Honda", "model": "Vezel", "color": "White", "vin": "TESTVIN0000000001",
               "engine_number": "TESTENG1", "date_of_registration": "2017-01-12"}
    key = {"cases": [
        {"name": "TEST CASE A", "folder": "case_a", "vehicle": dict(vehicle, plate_number="TEST-A"), "parts": [
            {"part": "Boot Lid", "surveyor": ["Tail gate assy"]}, {"part": "Boot Lid Lock", "surveyor": ["Tail gate lock striker"]},
            {"part": "Left Tail Lamp", "surveyor": ["Tail lamp LH"]}]},
        {"name": "TEST CASE B", "folder": "case_b", "vehicle": dict(vehicle, plate_number="TEST-B"), "parts": [
            {"part": "Boot Lid", "surveyor": ["Tail gate"]}, {"part": "Boot Lid Lock", "surveyor": ["Tail gate lock striker"]},
            {"part": "Rear Bumper", "hidden": False, "surveyor": ["Rear bumper fascia"]}]},
    ]}
    key_path = base / "key.json"
    key_path.write_text(json.dumps(key), encoding="utf-8")

    labels = {"p1.jpg": ("damage", "Boot Lid"), "p2.jpg": ("damage", "Tail Lamp (side undetermined)"), "p3.jpg": ("overview", None)}
    sent: list[int] = []
    history_seen: list[set] = []
    gemini = bi._counting(lambda *, purpose, input_parts=None: A.runs.note_attempt(
        {"model": "test-model", "outcome": "ok", "waited_s": 0.5, "ms": 1000, "prompt_tokens": 1000, "output_tokens": 100,
         "thought_tokens": 40}))
    reads = {"n": 0}
    settings_seen: list[tuple] = []

    def fake_sort(rid, actor):
        db = A.SessionLocal()
        try:
            todo = [p for p in A.ordered_photos(db, rid) if p.kind is None]
            sent.append(len(todo))
            if todo:
                gemini(purpose="photo sort")
            for p in todo:
                p.kind, p.part = labels[p.original_name]
                p.area, p.quality, p.shows_damage, p.note = "rear", "good", p.kind == "damage", None
            db.commit()
        finally:
            db.close()

    def fake_read(rid, actor):
        reads["n"] += 1
        db = A.SessionLocal()
        try:
            history_seen.append({r for (r,) in db.query(A.PastAnnexLine.case_ref).distinct()})
            if reads["n"] == 4:
                raise RuntimeError("All Gemini fallback models (test-model) failed across 1 configured API key(s).")
            settings_seen.append((A.settings.gemini_thinking_level_read, A.settings.gemini_image_resolution, A.settings.gemini_read_tools))
            gemini(purpose="draft", input_parts=[{"type": "image", "resolution": "ultra_high"}, {"type": "image"}])
            report, session = db.get(A.Report, rid), db.get(A.IntakeSession, rid)
            photos = A.ordered_photos(db, rid)
            session.read_photo_ids = [photos[0].id, photos[2].id]
            gemini(purpose="second look")
            report.data = dict(report.data, vehicle_info={"plate_number": "TEST-A", "make": "Honda", "model": "Vezel"},
                               damage_summary=[{"part": "Boot Lid", "damage_type": "Dent", "condition": "Dented/bent"},
                                               {"part": "Tail Lamp (side undetermined)", "damage_type": "Crack"},
                                               {"part": "Bonnet", "damage_type": "Scratch"},
                                               {"part": "Other / Not Listed", "other_part_name": "Tail gate lock striker plate"}],
                               second_look={"status": "ok", "model": "test-look", "seconds": 0.0, "photos_sent": 2,
                                            "candidates": 30, "items": [{"part": "Boot Lid Lock"}, {"part": "Rear End Panel"}]})
            db.commit()
        finally:
            db.close()

    model_fields = [field for _, field, _ in bi.MODEL_SETTINGS]
    saved = (A.pipeline._sort, A.pipeline._read, A.settings.gemini_model_chain, A.settings.gemini_triage_model_chain,
             A.settings.gemini_second_look_chain)
    saved_settings = {f: getattr(A.settings, f) for f in model_fields}
    os.environ["BENCHMARK_SCRATCH"] = "1"
    A.pipeline._sort, A.pipeline._read = fake_sort, fake_read
    buf = io.StringIO()
    try:
        for bad, says in ((["--thinking-level-read", "extreme"], "--thinking-level-read"),
                          (["--image-resolution", "dmage=ultra_high"], "no such photo kind"),
                          (["--image-resolution", "damage=8k"], "--image-resolution"),
                          (["--read-tools", "google_search"], "--read-tools")):
            try:
                bi.main(["--cases", str(cases_dir), "--key", str(key_path), "--out", str(out)] + bad)
                raise AssertionError(f"{bad} should be refused")
            except SystemExit as exc:
                assert says in str(exc.code), exc.code
        assert not out.exists() and all(getattr(A.settings, f) == saved_settings[f] for f in model_fields)
        ok("a model setting config refuses, or a photo kind the sorter hasn't, stops the run before anything")
        with contextlib.redirect_stdout(buf):
            code = bi.main(["--cases", str(cases_dir), "--key", str(key_path), "--out", str(out), "--repeat", "2",
                            "--sort-cache", str(cache_dir), "--reports", str(reports_dir), "--label", "test-run",
                            "--ref", "test-branch@0000000", "--models", "test-strong", "--triage-models", " test-lite-a, test-lite-b ",
                            "--second-look-models", "test-look", "--thinking-level-read", " LOW, test-strong=high ",
                            "--thinking-level-sort", "minimal", "--image-resolution", "damage=ultra_high,*=medium",
                            "--read-tools", "code_execution"])
    finally:
        (A.pipeline._sort, A.pipeline._read, A.settings.gemini_model_chain, A.settings.gemini_triage_model_chain,
         A.settings.gemini_second_look_chain) = saved
        for field, value in saved_settings.items():
            setattr(A.settings, field, value)
        os.environ.pop("BENCHMARK_SCRATCH", None)
    text = buf.getvalue()
    assert code == 0, text
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert list(doc) == ["label", "git_ref", "args", "models", "ai_settings", "sort_cache", "started", "finished", "runs",
                         "summary"], list(doc)
    assert (doc["label"], doc["git_ref"], doc["args"]["repeat"]) == ("test-run", "test-branch@0000000", 2)
    assert doc["models"] == {"model_chain": "test-strong", "triage_model_chain": "test-lite-a,test-lite-b",
                             "second_look_chain": "test-look"}, doc["models"]
    assert doc["sort_cache"]["triage_model_chain"] == "test-lite-a,test-lite-b"
    ok("result file: label, git_ref, args, models (overrides applied), sort cache, runs, summary")
    assert doc["ai_settings"] == {"thinking_level_read": "low,test-strong=high", "thinking_level_sort": "minimal",
                                  "thinking_level_second_look": None, "image_resolution": "damage=ultra_high,*=medium",
                                  "read_tools": "code_execution"}, doc["ai_settings"]
    assert settings_seen[0] == ("low,test-strong=high", "damage=ultra_high,*=medium", "code_execution"), settings_seen
    assert doc["sort_cache"]["thinking_level_sort"] == "minimal"
    ok("the model-setting flags are on the app's settings while it reads, and in the result file (ai_settings)")
    runs = doc["runs"]
    assert [(r["case"], r["rep"]) for r in runs] == [("TEST CASE A", 1), ("TEST CASE B", 1), ("TEST CASE A", 2), ("TEST CASE B", 2)]
    assert sent == [3, 3, 0, 0], sent
    assert [r["sort_cache"]["hits"] for r in runs] == [0, 0, 3, 3] and [r["sort_cache"]["stored"] for r in runs] == [3, 3, 0, 0]
    assert "photo sort" in runs[0]["accounting"] and "photo sort" not in runs[2]["accounting"]
    assert runs[0]["calls"] == {"photo sort": 1, "draft": 1, "second look": 1}, runs[0]["calls"]
    assert runs[0]["answered_by"] == {"photo sort: test-model": 1, "draft: test-model": 1, "second look: test-model": 1}
    ok("run 1 sorts and fills the cache; run 2 takes every photo from it and makes no sorting call")
    draft_call = next(c for c in runs[0]["ai_calls"] if c["purpose"] == "draft")
    assert (draft_call["model"], draft_call["ok"], draft_call["attempts"], draft_call["prompt_tokens"], draft_call["output_tokens"],
            draft_call["thought_tokens"], draft_call["images"]) == ("test-model", True, 1, 1000, 100, 40,
                                                                     {"ultra_high": 1, "default": 1}), draft_call
    assert [c["purpose"] for c in runs[0]["ai_calls"]] == ["photo sort", "draft", "second look"]
    assert "call draft: test-model in" in text and "tokens in 1000 out 100 thinking 40" in text, text
    assert "call photo sort" not in text
    assert doc["summary"]["total"]["read_tok_think"]["mean"] == 80 and doc["summary"]["accounting"]["draft"]["thought_tokens"] == 120
    ok("every call's model, tries, tokens and images in the result (ai_calls) and the log; tokens in the summary")
    assert history_seen[0] == {"CL 90002", "CL 90003"} and history_seen[1] == {"CL 90001", "CL 90003"}, history_seen
    ok("while a case is read, the history is every report but its own")
    a1 = runs[0]
    assert a1["parts"]["found"] == 2 and a1["parts"]["extra"] == ["Bonnet", "Other / Not Listed"] and a1["parts"]["side_undetermined"] == ["Tail Lamp (side undetermined)"]
    assert a1["drafted_items"][0] == {"part": "Boot Lid", "damage_type": "Dent", "condition": "Dented/bent"}
    assert a1["sorter"]["tagged_in_key"] == 2 and a1["sorter"]["key_photos_read"] == 1
    c = a1["conditions"]
    assert (c["scored"], c["exact"], c["first_token"], c["token_hits"], c["tokens"]) == (2, 1, 1, 2, 4), c
    an = a1["annex_a"]
    assert an["real_lines"] == 4 and 0 <= an["covered"] <= an["real_lines"] and an["rows"] == an["priced"] + an["unpriced"], an
    assert an["perfect_covered"] >= 1 and an["extra"] >= 0 and isinstance(an["canonical_names"], list), an
    assert a1["vehicle"]["plate_number"]["verdict"] == "ok"
    ok("each run carries parts, sorter quality, conditions and the leave-one-out Annex A")
    assert a1["drafted_items"][3] == {"part": "Other / Not Listed", "other_part_name": "Tail gate lock striker plate"}
    fair = a1["parts_fair"]
    assert (fair["found"], fair["strict_extra"], fair["other_named"], fair["extra"], fair["contradicted"]) == (
        2, 2, [["Tail gate lock striker plate", "Boot Lid Lock"]], ["Bonnet"], []), fair
    assert "fair: parts 2/3" in text and "FAIR parts" in text and "fair found" in text, text
    ok("each run carries the fair score (an Other named like the key's lock is not extra; the report contradicts nothing)")
    look, ls = a1["second_look"], a1["parts_with_second_look"]
    assert (look["status"], look["model"], look["calls"], look["suggested"]) == ("ok", "test-look", 1, ["Boot Lid Lock", "Rear End Panel"]), look
    assert (ls["found"], ls["added"], ls["added_parts"], ls["extra"], ls["extra_parts"]) == (3, 1, ["Boot Lid Lock"], 1, ["Rear End Panel"]), ls
    assert "second look" in a1["accounting"] and a1["accounting"]["second look"]["calls"] == 1
    ok("each run scores the second look as if all ticked (found 3, added 1, extra 1) and counts its call apart")
    sd, zn, rr = a1["part_sides"], a1["part_zones"], a1["part_remarks"]
    assert (sd["sided_found"], sd["undetermined"], sd["right"], sd["wrong"]) == (1, 1, 0, 0), sd
    assert zn["rear"] == {"expected": 3, "found": 2, "wrong_side": 0, "missed": 1, "extra": 0, "missed_parts": ["Boot Lid Lock"]}, zn
    assert (zn["front"]["extra"], zn["other"]["extra"]) == (1, 1), zn  # the bonnet, the Other item
    assert (rr["scored"], rr["agree"], rr["unsure"]) == (2, 0, 2), rr  # the report says Replace; no remarks drafted
    assert an["report_price_total"] == 2242 and an["price_diff"] == round(an["price_total"] - 2242, 2), an
    assert 0 <= an["within_10pct"] <= an["covered"] and an["matched_priced"] <= an["covered"], an
    assert "sides of the found parts: right 0" in text and "by zone, found/expected: front 0/0 extra 1; rear 2/3" in text, text
    assert "Replace/Repair: agree 0, disagree 0, unsure 2 of 2" in text and "Annex A prices: W/shop Est" in text, text
    assert "side right" in text and "rear found" in text and "within 10%" in text, text
    t = doc["summary"]["total"]
    # Run 1, both cases: the boot lid in each, case A's tail lamp too.
    assert (t["zone_rear_found"]["mean"], t["zone_rear_expected"]["mean"], t["remarks_scored"]["mean"]) == (3, 6, 3), t
    ok("each run carries the found parts' sides, zones and Replace / Repair, and the Annex A's prices; in the log and summary")
    assert runs[3].get("error", "").startswith("RuntimeError: All Gemini fallback models")
    s = doc["summary"]
    assert s["complete_reps"] == [1] and s["errors"][0]["case"] == "TEST CASE B" and s["cases"]["TEST CASE A"]["metrics"]["found"]["n"] == 2
    assert s["total"]["found"]["n"] == 1 and s["sort_cache"] == {"hits": 6, "misses": 6}
    assert s["accounting"]["draft"]["calls"] == 3 and s["accounting"]["photo sort"]["calls"] == 2
    ok("an errored run is listed and left out of the totals; accounting merged over runs")
    for r in runs:
        assert not Path(os.environ["STORAGE_DIR"], "intake", r["report_id"]).exists()
    ok("each run's photo copies are deleted after scoring")
    assert "run 1: TOTAL parts" in text and "WITH CHECKLIST" in text and "SUMMARY: 2 run(s) of 2 case(s)" in text, text
    assert "run 1: WITH SECOND LOOK (all ticked)" in text and "second look: ok on test-look" in text and "look added" in text, text
    assert s["total"]["look_added"]["n"] == 1 and s["accounting"]["second look"]["calls"] == 3
    assert "Annex A (leave-one-out)" in text and "sorter: labels in key" in text
    assert "ZZZZ" not in out.read_text(encoding="utf-8") and "key_last4" not in out.read_text(encoding="utf-8")
    ok("console keeps the old per-case and TOTAL lines, plus the new detail and the summary table")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert compare.main([str(out), str(out), "--total-only"]) == 0
    assert "same" in buf.getvalue() and "TEST CASE A" not in buf.getvalue()
    ok("compare.py on the result file (--total-only prints no case names)")
    assert "thinking_level_read low,test-strong=high" in buf.getvalue() and "read_tools code_execution" in buf.getvalue()
    assert "fair_found" in buf.getvalue() and "read_tok_think" in buf.getvalue(), buf.getvalue()
    ok("compare.py names each side's model settings and compares the fair score and the reading's tokens")
    assert "zone_rear_found" in buf.getvalue() and "remarks_agree_rate" in buf.getvalue() and "annex_within_10" in buf.getvalue()
    ok("compare.py compares the found parts' detail and the Annex A prices")


# ---------------------------------------------------------------- the found parts in detail (contract 8)


def priced(item: str, est: float, adjusted: float, condition: str = "Dented") -> SimpleNamespace:
    return SimpleNamespace(item=item, condition=condition, workshop_est=est, adjusted_cost=adjusted)


def test_sides() -> None:
    print("sides of the found parts")
    key = [{"part": "Left Tail Lamp"}, {"part": "Right Tail Lamp"}, {"part": "Left Rear Bumper Reflector"},
           {"part": "Rear Bumper"}, {"part": "Right Front Door"}, {"part": "Left Headlamp"}, {"part": "Right Rear Quarter Panel"}]
    items = [
        {"part": "Left Tail Lamp", "side_inferred": True},  # placed from the sorter's area, and right
        {"part": "Tail Lamp (side undetermined)"},  # the right lamp, its side not said
        {"part": "Right Rear Bumper Reflector", "side_inferred": True},  # placed, and wrong
        {"part": "Rear Bumper"},  # no side: left out
        {"part": "Left Front Door"},  # read on the wrong side
        {"part": "Left Headlamp", "side_inferred": True}, {"part": "Left Headlamp"},  # also read there: not inferred
        {"part": "Bonnet"},
    ]
    parts = bi.score_parts(key, [i["part"] for i in items])
    s = bi.score_sides(key, items, parts)
    assert (s["sided_found"], s["right"], s["wrong"], s["undetermined"]) == (5, 2, 2, 1), s
    assert (s["right_inferred"], s["wrong_inferred"]) == (1, 1) and s["wrong"] == len(parts["wrong_side"]), s
    by = {d["part"]: d for d in s["details"]}
    assert by["Right Tail Lamp"] == {"part": "Right Tail Lamp", "drafted_as": "Tail Lamp (side undetermined)",
                                     "side": "undetermined", "inferred": False}, by
    assert by["Right Front Door"] == {"part": "Right Front Door", "drafted_as": "Left Front Door", "side": "wrong",
                                      "inferred": False}, by
    assert by["Left Rear Bumper Reflector"]["inferred"] and by["Left Tail Lamp"]["inferred"] and not by["Left Headlamp"]["inferred"]
    assert "Rear Bumper" not in by and "Right Rear Quarter Panel" not in by
    ok("right, wrong (score_parts' wrong side) and undetermined per found sided part; inferred only when no item of the name was read")
    run = {"parts": parts, "drafted_parts": [i["part"] for i in items], "drafted_items": items, "vehicle": {}}
    bi.score_details(run, {"parts": key})
    m = bi.run_metrics(run)
    assert (m["sided_found"], m["side_right"], m["side_undetermined_found"], m["side_right_inferred"], m["side_wrong_inferred"],
            m["wrong_side"], m["side_right_rate"]) == (5, 2, 1, 1, 1, 2, 0.4), m
    assert "part_remarks" not in run and "remarks_scored" not in m
    ok("run metrics: with a side, right, undetermined, inferred; no Replace / Repair without a report or a key that says")


def test_remarks() -> None:
    print("Replace / Repair")
    from app.parts import history

    lines = [line("Tail gate assy", "Dented/bent"), line("Tail gate lock striker", "Necessary"),
             line("Rear bumper fascia", "Repair"), line("Tail lamp LH", "Cracked"),
             line("Rear bumper reflector LH", "Shifted")]
    key = [{"part": "Boot Lid", "surveyor": ["Tail gate assy"]},
           {"part": "Boot Lid Lock", "surveyor": ["Tail gate lock striker"]},  # refitted only: undecided
           {"part": "Rear Bumper", "surveyor": ["Rear bumper fascia"]},
           {"part": "Left Tail Lamp", "surveyor": ["Tail lamp LH"]},
           {"part": "Left Rear Bumper Reflector", "surveyor": ["Rear bumper reflector LH"], "remarks": "Repair"},  # the key says
           {"part": "Bonnet", "surveyor": ["Bonnet"]}]  # not found
    by = {p["part"]: p for p in key}
    sr = lambda part: bi.surveyor_remarks(by[part], lines, history.normalise_item, history.side_of)  # noqa: E731
    assert [sr(p) for p in by] == ["Replace", None, "Repair", "Replace", "Repair", None], [sr(p) for p in by]
    ok("the surveyor's: condition Repair is Repair, any other damage Replace, refitted only undecided; the key's own first")
    items = [{"part": "Boot Lid", "remarks": "replace"}, {"part": "Boot Lid Lock", "remarks": "Replace"},
             {"part": "Rear Bumper", "remarks": "Replace"}, {"part": "Tail Lamp (side undetermined)", "remarks": None},
             {"part": "Left Rear Bumper Reflector", "remarks": "Repair"}]
    parts = bi.score_parts(key, [i["part"] for i in items])
    r = bi.score_remarks(key, parts["matched"], items, lines, history.normalise_item, history.side_of)
    assert (r["scored"], r["agree"], r["disagree"], r["unsure"], r["surveyor_repair"], r["repair_agree"]) == (4, 2, 1, 1, 2, 1), r
    assert [(d["part"], d["verdict"]) for d in r["details"]] == [
        ("Boot Lid", "agree"), ("Rear Bumper", "disagree"), ("Left Tail Lamp", "unsure"), ("Left Rear Bumper Reflector", "agree")], r
    assert bi._remarks("Fix it") is None and bi._remarks(" REPAIR ") == "Repair"
    ok("per found part: agree (any case), disagree, unsure (null or not a firm word); Repair counted apart")
    run = {"parts": parts, "drafted_parts": [i["part"] for i in items], "drafted_items": items, "vehicle": {}}
    bi.score_details(run, {"parts": key})  # no report: the key's own remarks are enough
    assert run["part_remarks"]["scored"] == 1 and run["part_remarks"]["agree"] == 1, run["part_remarks"]
    bi.score_details(run, {"parts": key}, lines, history.normalise_item, history.side_of)
    m = bi.run_metrics(run)
    assert (m["remarks_scored"], m["remarks_agree"], m["remarks_disagree"], m["remarks_unsure"], m["remarks_repair"],
            m["remarks_repair_agree"], m["remarks_agree_rate"]) == (4, 2, 1, 1, 2, 1, 0.5), m
    ok("run metrics: scored, agree, disagree, unsure, the surveyor's Repairs and the agree rate")


def test_zones() -> None:
    print("zones")
    names = ("Left Front Fender", "Right Rear Quarter Panel", "Left Front Tyre", "Left Headlamp", "Left Tail Lamp",
             "Rear Bumper", "Bonnet", "Roof", "Parking Sensor", "Headlamp (side undetermined)", "Door (side undetermined)",
             "Other / Not Listed")
    assert [bi.part_zone(p) for p in names] == ["left", "right", "left", "front", "rear", "rear", "front", "other", "other",
                                                "front", "other", "other"], [bi.part_zone(p) for p in names]
    bi._app()  # second_look imports call_structured: after the benchmark has wrapped it
    from app.ai import second_look
    from app.reports.taxonomy import CANONICAL_PARTS

    sides = {"left", "right"}
    for part in CANONICAL_PARTS:
        if part.endswith(bi.SIDE_UNDETERMINED) or part == bi.OTHER_PART:
            continue
        zones = {zone for zone, parts in second_look.ZONES.items() if part in parts}
        ends = zones - sides
        want = next(iter(zones & sides)) if zones & sides else next(iter(ends)) if len(ends) == 1 else "other"
        assert bi.part_zone(part) == want, (part, zones, bi.part_zone(part))
    ok("one zone a part: the flank's side even at a corner, else its end, else other -- as second_look.ZONES places them")

    key = [{"part": "Rear Bumper"}, {"part": "Left Tail Lamp"}, {"part": "Right Tail Lamp"}, {"part": "Left Rear Quarter Panel"},
           {"part": "Right Front Door"}, {"part": "Bonnet"}, {"part": "Rear Subframe", "hidden": True}]
    drafted = ["Rear Bumper", "Tail Lamp (side undetermined)", "Left Front Door", "Front Bumper", "Other / Not Listed"]
    parts = bi.score_parts(key, drafted)
    z = bi.score_zones(key, parts)
    assert list(z) == list(bi.ZONES)
    assert z["rear"] == {"expected": 3, "found": 2, "wrong_side": 0, "missed": 1, "extra": 0, "missed_parts": ["Right Tail Lamp"]}, z
    assert z["right"] == {"expected": 1, "found": 0, "wrong_side": 1, "missed": 0, "extra": 0, "missed_parts": []}, z
    assert (z["left"]["missed_parts"], z["front"]["missed_parts"], z["other"]["missed_parts"]) == (
        ["Left Rear Quarter Panel"], ["Bonnet"], ["Rear Subframe"]), z
    assert (z["front"]["extra"], z["other"]["extra"]) == (1, 1), z
    assert (sum(v["expected"] for v in z.values()), sum(v["found"] for v in z.values()),
            sum(v["wrong_side"] for v in z.values()), sum(v["extra"] for v in z.values())) == (
        len(key), parts["found"], len(parts["wrong_side"]), len(parts["extra"])), z
    ok("found, wrong side and missed (named) by where the key part sits, extras by where they would; they add up to the score")
    runs = []
    for rep in (1, 2):
        run = {"case": "CASE A", "rep": rep, "parts": parts, "drafted_parts": drafted, "vehicle": {}}
        bi.score_details(run, {"parts": key})
        runs.append(run)
    m = bi.run_metrics(runs[0])
    assert (m["zone_rear_found"], m["zone_rear_expected"], m["zone_rear_recall"], m["zone_front_extra"]) == (2, 3, 0.667, 1), m
    t = bi.summarize(runs)["total"]
    assert t["zone_rear_recall"]["mean"] == 0.667 and t["zone_left_recall"]["mean"] == 0.0, t
    ok("run metrics per zone (found, expected, extra) and the recall from the summed counts")


def test_prices() -> None:
    print("Annex A prices against the surveyor's")
    from app.parts import history
    from eval_parts_kits import _matched, matched_pairs

    lines = [priced("Tail gate assy", 1000, 800), priced("Tail gate lock striker", 100, 80),
             priced("Tail lamp LH", 600, 480), priced("Tail lamp RH", 600, 480),
             priced("Rear bumper fascia", 500, 400), priced("Rear bumper beam", 200, 160),
             priced("Tail gate stopper", 400, 320, "Necessary")]  # no row for it: missed
    rows = [{"part_name": "Tail gate assy", "workshop_est": 1050, "adjusted_cost": 840},  # 5% over
            {"part_name": "Tail gate lock striker", "workshop_est": 110, "adjusted_cost": 88},  # 10% over: still within
            {"part_name": "Tail lamp", "workshop_est": 1150, "adjusted_cost": 920},  # against the two lamps' 1200
            {"part_name": "Rear bumper fascia", "workshop_est": 600, "adjusted_cost": 480},  # 20% over
            {"part_name": "Rear bumper beam", "workshop_est": 0, "adjusted_cost": 0},  # unpriced
            {"part_name": "Bonnet", "workshop_est": 300, "adjusted_cost": 240}]  # extra
    real = {history.normalise_item(ln.item) for ln in lines}
    keys = {history.normalise_item(r["part_name"]) for r in rows}
    pairs = matched_pairs(real, keys)
    assert set(pairs) == _matched(real, keys) and len(pairs) == 5 and pairs["tail lamp"] == "tail lamp", pairs
    ok("matched_pairs pairs exactly the lines _matched covers")
    p = bi.score_prices(lines, rows, pairs, history.normalise_item)
    assert (p["price_total"], p["report_price_total"], p["price_diff"]) == (3210, 3400, -190), p
    assert (p["adjusted_total"], p["report_adjusted_total"], p["adjusted_diff"]) == (2568, 2720, -152), p
    assert (p["matched_priced"], p["within_10pct"], p["matched_abs_diff"]) == (4, 3, 410), p
    ok("totals of the whole sheet (missed and extra lines in them), matched lines priced, within 10% (LH + RH one wording)")
    run = {"parts": bi.score_parts([], []), "vehicle": {}, "annex_a": {
        "real_lines": 6, "covered": 5, "rows": 6, "priced": 5, "unpriced": 1, "extra": 1, "canonical_rows": 0,
        "perfect_covered": 6, **p, "perfect_within_10pct": 5, "perfect_price_diff": -50}}
    m = bi.run_metrics(run)
    assert (m["annex_price_diff"], m["annex_adjusted_diff"], m["annex_matched_priced"], m["annex_within_10"],
            m["annex_price_abs_diff"], m["annex_perfect_within_10"], m["annex_within_10_rate"]) == (-190, -152, 4, 3, 410, 5, 0.6), m
    assert m["annex_price_diff_rate"] == round(-190 / 3400, 3), m
    stored = dict(run, annex_a={k: v for k, v in run["annex_a"].items() if k not in p})
    assert "annex_price_diff" not in bi.run_metrics(stored) and bi.run_metrics(stored)["annex_covered"] == 5
    ok("run metrics: price and adjusted difference, matched priced, within 10% (and its rate); absent in older files")


def gate_doc(label: str, founds, wrongs, extras, *, expected: int = 10, errors=(), cases=("CASE A",)) -> dict:
    """A result file of one run per repetition and case: found, wrong
    sides and extras per run; 2 reading calls and a sorting one, 2000
    prompt tokens, 20 seconds per case."""
    runs = []
    for rep, (found, wrong, extra) in enumerate(zip(founds, wrongs, extras), start=1):
        for case in cases:
            if rep in errors:
                runs.append({"case": case, "rep": rep, "error": "RuntimeError: quota"})
                continue
            runs.append({
                "case": case, "rep": rep, "read": 30, "sort_seconds": 1.5, "read_seconds": 18.5,
                "drafted_parts": ["Rear Bumper"], "vehicle": {},
                "parts": {"found": found, "expected": expected, "visible_found": found, "visible_expected": expected,
                          "extra": ["Bonnet"] * extra, "wrong_side": [["Left Front Door", "Right Front Door"]] * wrong,
                          "found_parts": {}, "missed": []},
                "accounting": {"draft": {"calls": 2, "prompt_tokens": 2000}, "photo sort": {"calls": 1}},
            })
    return {"label": label, "git_ref": f"{label}@0000000", "ai_settings": {"read_tools": None}, "runs": runs, "summary": {}}


def test_gate() -> None:
    print("the gate")
    folder = Path(_tmp, "gate")
    folder.mkdir()

    def saved(doc: dict) -> Path:
        path = folder / f"{doc['label']}.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    base = gate.load(saved(gate_doc("base", [4, 5, 6], [3, 3, 3], [1, 1, 1])))
    verdict = lambda doc: gate.judge(base, gate.load(saved(doc)))  # noqa: E731
    better = verdict(gate_doc("better", [7, 8, 9], [3, 3, 3], [3, 3, 3]))
    assert better["pass"] and [c["ok"] for c in better["checks"]] == [True, True, True], better
    assert [c["noise"] for c in better["checks"]] == ["UP (better)", "same", "UP (worse)"], better
    assert better["checks"][2]["says"] == "extras 3 <= 3 (baseline 1 + 2)", better["checks"][2]
    ok("pass: found above the baseline, wrong sides at it, extras at the baseline + 2 (the limits are inclusive)")
    same = verdict(gate_doc("same", [5, 5, 5], [3, 3, 3], [1, 1, 1]))
    assert not same["pass"] and [c["ok"] for c in same["checks"]] == [False, True, True], same
    assert same["checks"][0]["says"] == "parts found 5 not > 5 (baseline 5)" and same["checks"][0]["noise"] == "inside noise"
    extras = verdict(gate_doc("extras", [7, 8, 9], [3, 3, 3], [4, 4, 4]))
    assert not extras["pass"] and [c["ok"] for c in extras["checks"]] == [True, True, False], extras
    ok("fail: found only equal to the baseline; extras above the baseline + 2")
    low = gate.load(saved(gate_doc("low-sides", [4, 5, 6], [1, 1, 1], [1, 1, 1])))
    sides = gate.judge(low, gate.load(saved(gate_doc("sides", [7, 8, 9], [3, 3, 2], [1, 1, 1]))))
    assert not sides["pass"] and [c["ok"] for c in sides["checks"]] == [True, False, True], sides
    assert sides["checks"][1]["limit"] == 2.5 and sides["checks"][1]["value"] == 2.667, sides
    floor = gate.judge(low, gate.load(saved(gate_doc("floor", [7, 8, 9], [2, 3, 2], [1, 1, 1]))))
    assert floor["pass"], floor  # 2.333 wrong sides: above the baseline's 1, under the floor of 2.5
    ok("fail: wrong sides above the higher of the baseline and 2.5; under 2.5 passes though the baseline had fewer")
    broken = gate.load(saved(gate_doc("broken", [7, 8, 9], [0, 0, 0], [0, 0, 0], errors=(1, 2, 3))))
    v = gate.judge(base, broken)
    assert not v["pass"] and all(not c["ok"] and c["value"] is None for c in v["checks"]), v
    assert v["checks"][0]["says"].startswith("parts found: missing in the candidate"), v
    assert gate.judge(broken, base)["checks"][1]["says"].startswith("wrong sides: missing in the baseline")
    no_sides = gate.load(saved(gate_doc("no-sides", [7, 8, 9], [0, 0, 0], [1, 1, 1])))
    del no_sides["summary"]["total"]["wrong_side"]  # a file from before the measure
    v = gate.judge(base, no_sides)
    assert not v["pass"] and [c["ok"] for c in v["checks"]] == [True, False, True], v
    ok("fail: a measure missing in the candidate (no complete run, or an older file) or in the baseline")
    other_key = verdict(gate_doc("other-key", [7, 8, 9], [3, 3, 3], [1, 1, 1], expected=9))
    assert not other_key["pass"] and all(c["ok"] for c in other_key["checks"]), other_key
    assert other_key["problems"] == ["scored on another key (9 key parts, the baseline 10): give --key"], other_key
    ok("fail: scored on a key of another size")

    two = gate.load(saved(gate_doc("two-cases", [7, 8, 9], [1, 1, 1], [1, 1, 1], cases=("CASE A", "CASE B"))))
    assert (gate.per_case(two["summary"], "calls"), gate.per_case(two["summary"], "total_s"),
            gate.per_case(two["summary"], "tok_in")) == (3, 20, 2000), two["summary"]["total"]
    assert gate.per_case(gate.load(saved(gate_doc("none", [1], [0], [0], errors=(1,))))["summary"], "calls") is None
    ok("calls, seconds and prompt tokens per case: TOTAL over the cases")

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = gate.main([str(folder / "base.json"), str(folder / "better.json"), str(folder / "floor.json")])
    text = out.getvalue()
    assert code == 0, text
    assert "strict parts score" in text and "calls/case" in text and "prompt tok/case" in text and "(baseline)" in text, text
    assert "cand 1 better (better@0000000): PASS" in text and "ok   parts found 8 > 5 (baseline 5) -- UP (better)" in text, text
    assert "3.00" in text and "2000" in text and "CASE A" not in text and "CASE B" not in text, text
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = gate.main([str(folder / "base.json"), str(folder / "better.json"), str(folder / "same.json")])
    assert code == 1 and "cand 2 same (same@0000000): FAIL" in out.getvalue() and "FAIL parts found 5 not > 5" in out.getvalue()
    ok("gate.py prints one table and a verdict per candidate, no case names; exit 0 when all pass, 1 when any fails")

    # --key: files scored on different keys, scored again on one.
    stored = gate_doc("old-key", [0, 0, 0], [0, 0, 0], [0, 0, 0], expected=3)
    for run in stored["runs"]:
        run["drafted_parts"] = ["Rear Bumper", "Left Tail Lamp", "Bonnet"]
    key_path = folder / "key.json"
    key_path.write_text(json.dumps({"cases": [{"name": "CASE A", "parts": KEY_PARTS}]}), encoding="utf-8")
    key = {c["name"]: c for c in json.loads(key_path.read_text(encoding="utf-8"))["cases"]}
    again = gate.load(saved(stored), key)["summary"]["total"]
    assert (again["found"]["mean"], again["extra"]["mean"], again["expected"]["mean"]) == (2, 1, 8), again
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        gate.main([str(folder / "old-key.json"), str(folder / "better.json"), "--key", str(key_path)])
    assert "give --key" not in out.getvalue(), out.getvalue()
    ok("--key scores every run's strict parts again on one key before the gate")


# ---------------------------------------------------------------- rescore, scratch


def test_rescore() -> None:
    print("--rescore a stored result file")
    base = Path(_tmp, "rescore")
    base.mkdir()
    reports = base / "reports"
    report_xlsx(reports / "CL 90009 TEST-R.xlsx", "CL 90009", "TEST-R", "Honda Vezel 1.5X CVT",
                [("Tail gate assy", "Dented", 1, 1400, 1120), ("Tail gate lock striker", "Bent", 1, 90, 72),
                 ("Rear bumper fascia", "Serviceable", 1, 0, 0)])
    key = {"cases": [{"name": "TEST CASE R", "folder": "case_r", "vehicle": {"plate_number": "TEST-R"}, "parts": [
        {"part": "Boot Lid", "surveyor": ["Tail gate assy"]}, {"part": "Boot Lid Lock", "surveyor": ["Tail gate lock striker"]},
        {"part": "Left Rear Quarter Panel", "surveyor": ["Rear fender LH"]}]}]}
    key_path = base / "key.json"
    key_path.write_text(json.dumps(key), encoding="utf-8")
    items = [{"part": "Boot Lid", "condition": "Dented"}, {"part": "Rear Bumper"}, {"part": "Left Rear Door"},
             {"part": "Other / Not Listed", "other_part_name": "Tail gate striker plate"}, {"part": "Bonnet"}]
    names = [i["part"] for i in items]

    def stored(rep, parts):
        return {"case": "TEST CASE R", "rep": rep, "drafted_parts": names, "drafted_items": items, "parts": parts,
                "vehicle": {}, "checklist": ["Kept As Stored"]}

    doc = {"label": "old-run", "git_ref": "test@0", "runs": [
        stored(1, bi.score_parts(key["cases"][0]["parts"], names)),
        stored(2, dict(bi.score_parts([], names), found=0)),  # scored with an older key
        {"case": "NOT IN KEY", "rep": 1, "drafted_parts": ["Bonnet"], "parts": bi.score_parts([], ["Bonnet"]), "vehicle": {}},
        {"case": "TEST CASE R", "rep": 3, "error": "RuntimeError: quota"},
    ], "summary": {}}
    source = base / "old.json"
    source.write_text(json.dumps(doc), encoding="utf-8")
    before = source.read_bytes()
    out = base / "rescored.json"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert bi.main(["--rescore", str(source), "--key", str(key_path), "--reports", str(reports), "--out", str(out)]) == 0
    text = buf.getvalue()
    assert source.read_bytes() == before, "the stored file is never changed"
    new = json.loads(out.read_text(encoding="utf-8"))
    r1, r2, other, errored = new["runs"]
    assert r1["parts"]["found"] == 1 and r2["parts"]["found"] == 1 and r2["parts"]["extra"] == r1["parts"]["extra"]
    assert r1["checklist"] == ["Kept As Stored"] and "parts_fair" not in other and "parts_fair" not in errored
    f = r1["parts_fair"]
    assert f["neighbour"] == [["Left Rear Door", "Left Rear Quarter Panel"]] and f["fair_found"] == 1.5, f
    assert f["other_named"] == [["Tail gate striker plate", "Boot Lid Lock"]] and f["extra"] == ["Rear Bumper", "Bonnet"], f
    assert f["contradicted"] == [["Rear Bumper", "Rear bumper fascia"]], f["contradicted"]
    assert new["summary"]["total"]["fair_found"]["mean"] == 1.5 and new["summary"]["complete_reps"] == [1]
    assert new["rescored"]["from"] == str(source) and new["label"] == "old-run"
    ok("each run scored again strict and fair from its drafted items; the stored file untouched; other numbers kept")
    assert "differs from the stored one on 1 run(s)" in text and "not in the key, left as stored: 1 case(s)" in text, text
    assert "FAIR parts 1.5/3" in text and "contradicted by the report 1" in text, text
    ok("the console says which runs' strict score changed (an older key) and which cases the key hasn't")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert bi.main(["--rescore", str(source), "--key", str(key_path)]) == 0
    assert "nothing flagged as contradicted" in buf.getvalue() and "contradicted by the report" not in buf.getvalue()
    for argv, says in ((["--rescore", str(source)], "needs --key"),
                       (["--rescore", str(source), "--key", str(key_path), "--out", str(source)], "another file")):
        try:
            bi.main(argv)
            raise AssertionError(argv)
        except SystemExit as exc:
            assert says in str(exc.code), exc.code
    assert source.read_bytes() == before
    ok("without --reports nothing is flagged; no --key, or --out over the stored file: refused")


def test_rescore_chain() -> None:
    print("--rescore: the found parts' detail, and Annex A built again on a scratch database")
    A = bi._app()
    A.init_db()
    base = Path(_tmp, "rescore-chain")
    reports = base / "reports"
    make_reports(reports)
    key = {"cases": [{"name": "TEST CASE A", "folder": "case_a", "vehicle": {"plate_number": "TEST-A"}, "parts": [
        {"part": "Boot Lid", "surveyor": ["Tail gate assy"]}, {"part": "Boot Lid Lock", "surveyor": ["Tail gate lock striker"]},
        {"part": "Left Tail Lamp", "surveyor": ["Tail lamp LH"]}]}]}
    key_path = base / "key.json"
    key_path.write_text(json.dumps(key), encoding="utf-8")
    items = [{"part": "Boot Lid", "condition": "Dented/bent", "remarks": "Replace"},
             {"part": "Right Tail Lamp", "side_inferred": True, "remarks": "Replace"},
             {"part": "Boot Lid Lock", "remarks": "Repair"}, {"part": "Bonnet"}]
    names = [i["part"] for i in items]
    as_stored = {"real_lines": 99, "rows": 0, "priced": 0, "unpriced": 0, "covered": 0, "coverage": 0.0, "extra": 0,
                 "canonical_rows": 0, "canonical_names": [], "perfect_rows": 0, "perfect_covered": 0, "perfect_coverage": 0.0}
    doc = {"label": "old-chain", "runs": [{"case": "TEST CASE A", "rep": 1, "drafted_parts": names, "drafted_items": items,
                                           "parts": bi.score_parts(key["cases"][0]["parts"], names), "vehicle": {},
                                           "annex_a": as_stored}], "summary": {}}
    source = base / "old.json"
    source.write_text(json.dumps(doc), encoding="utf-8")
    kept, rebuilt = base / "kept.json", base / "rebuilt.json"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert bi.main(["--rescore", str(source), "--key", str(key_path), "--reports", str(reports), "--out", str(kept)]) == 0
    assert "Annex A kept as stored" in buf.getvalue(), buf.getvalue()
    run = json.loads(kept.read_text(encoding="utf-8"))["runs"][0]
    assert run["annex_a"] == as_stored
    sd, zn, rr = run["part_sides"], run["part_zones"], run["part_remarks"]
    assert (sd["sided_found"], sd["wrong"], sd["wrong_inferred"]) == (1, 1, 1), sd  # the tail lamp, placed on the wrong side
    assert (zn["rear"]["found"], zn["rear"]["wrong_side"], zn["front"]["extra"]) == (2, 1, 1), zn
    # The report: the tail gate and its striker are damaged -- Replace; the
    # striker was drafted Repair.
    assert [(d["part"], d["verdict"]) for d in rr["details"]] == [("Boot Lid", "agree"), ("Boot Lid Lock", "disagree")], rr
    assert "sides of the found parts" not in buf.getvalue()  # the console stays the totals and the summary
    ok("the found parts' detail scored again from the drafted items and the report; Annex A kept without a scratch database")

    os.environ["BENCHMARK_SCRATCH"] = "1"
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert bi.main(["--rescore", str(source), "--key", str(key_path), "--reports", str(reports), "--out", str(rebuilt)]) == 0
    finally:
        os.environ.pop("BENCHMARK_SCRATCH", None)
    new = json.loads(rebuilt.read_text(encoding="utf-8"))
    an = new["runs"][0]["annex_a"]
    assert "Annex A built again on the scratch database for 1 run(s)" in buf.getvalue(), buf.getvalue()
    assert an["real_lines"] == 4 and an["rows"] == an["priced"] + an["unpriced"] and an["rows"] > 0, an
    assert (an["report_price_total"], an["report_adjusted_total"]) == (2242, 1793.6), an
    assert an["price_diff"] == round(an["price_total"] - 2242, 2) and 0 <= an["within_10pct"] <= an["covered"], an
    assert new["rescored"]["annex_a_rebuilt"] == 1 and "annex_within_10" in new["summary"]["total"], new["rescored"]
    ok("with a scratch database: prefill builds Annex A again from the drafted items, prices scored against the report")


def test_scratch() -> None:
    print("--scratch and the paths from the environment")
    folder = Path(_tmp, "scratch run")
    env = bi.scratch_env(folder)
    assert env == {"BENCHMARK_SCRATCH": "1", "DATABASE_URL": f"sqlite:///{(folder.resolve() / 'run.db').as_posix()}",
                   "STORAGE_DIR": str(folder.resolve() / "storage")}, env
    ok("--scratch DIR: the database and storage in DIR, and BENCHMARK_SCRATCH=1")
    saved = {k: os.environ.get(k) for k in env}
    try:
        bi._use_scratch(folder)
        raise AssertionError("an app loaded on another database must refuse")
    except SystemExit as exc:
        assert "already loaded" in str(exc.code), exc.code
    assert {k: os.environ.get(k) for k in env} == saved
    ok("with the app already loaded on another database it refuses, the environment untouched")
    # The script on its own, as on a PC: no BENCHMARK_SCRATCH, DATABASE_URL,
    # STORAGE_DIR or key. An unknown --only stops it after the app has made
    # its database -- before any case, so before any Gemini call.
    key_path = Path(_tmp, "scratch-key.json")
    key_path.write_text(json.dumps({"cases": [{"name": "ONLY CASE", "folder": "x", "vehicle": {}, "parts": []}]}), encoding="utf-8")
    child = {k: v for k, v in os.environ.items()
             if k not in ("BENCHMARK_SCRATCH", "DATABASE_URL", "STORAGE_DIR", "GEMINI_API_KEY")}
    child["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, str(SCRIPTS / "benchmark_intake.py"), "--scratch", str(folder), "--cases", _tmp,
                        "--key", str(key_path), "--out", str(folder / "out.json"), "--only", "NOT A CASE"],
                       capture_output=True, text=True, env=child)
    assert r.returncode != 0 and "--only: not in the key" in r.stderr, (r.returncode, r.stderr[-800:])
    assert (folder / "run.db").exists() and (folder / "storage").is_dir() and not (folder / "out.json").exists()
    ok("a run with --scratch alone makes its database and storage in that folder")
    try:
        bi.main(["--out", str(Path(_tmp, "x.json"))])
        raise AssertionError("missing --cases/--key must be refused")
    except SystemExit as exc:
        assert "--cases" in str(exc.code) and "--key" in str(exc.code), exc.code
    names = ("BENCH_CASES", "BENCH_KEY", "BENCH_REPORTS", "BENCH_SORT_CACHE")
    saved = {k: os.environ.get(k) for k in names}
    try:
        for name in names:
            os.environ[name] = str(Path(_tmp, name.lower()))
        args = bi.parse_args(["--out", "o.json"])
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    assert (args.cases, args.key, args.reports, args.sort_cache) == tuple(Path(_tmp, n.lower()) for n in names), args
    ok("BENCH_CASES / BENCH_KEY / BENCH_REPORTS / BENCH_SORT_CACHE stand for the flags")


def main() -> int:
    test_cli()
    test_score_parts()
    test_fair_score()
    test_contradicted()
    test_second_look_scoring()
    test_vehicle()
    test_sorter_quality()
    test_conditions()
    test_summary()
    test_old_format()
    test_compare()
    test_accounting()
    test_sort_cache()
    test_reports_and_history()
    test_annex_scoring()
    test_sides()
    test_remarks()
    test_zones()
    test_prices()
    test_gate()
    test_benchmark_run()
    test_rescore()
    test_rescore_chain()
    test_scratch()
    print("all benchmark scoring tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
