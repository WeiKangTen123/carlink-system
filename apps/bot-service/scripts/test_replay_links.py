"""Offline tests of the linked parts' replay and gate (contract 10, agent M):
scripts/bench/replay_links.py -- the guard (no Gemini client, no network),
the inputs rebuilt from a stored run (older files too), links.build_all as
second_look._store calls it, the scoring against the key, the
pre-registered variants, the damage list asserted unchanged, the L1 freeze
check, determinism -- and scripts/bench/gate.py --suggestions (G1-G6) and
compare.py's variants on its output.

    python scripts/test_replay_links.py

Synthetic data only: made-up cases, keys and result files; a made-up link
table for the exact numbers, the frozen L1 table where main() runs. No
Gemini call, no network (the replay's own guard is on in this process).
"""
import contextlib
import importlib.util
import io
import json
import socket
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))
_tmp = Path(tempfile.mkdtemp(prefix="replay-links-test-"))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"bench_{name}", SCRIPTS / "bench" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Loading it installs its guard in this process, before anything of the app.
rl = _load("replay_links")
gate = _load("gate")
compare = _load("compare")
bench = rl.bench
links = rl.links


def ok(label: str) -> None:
    print(f"    ok   {label}")


# A made-up table (the exact numbers below follow from it, not from L1).
TABLE = {"version": "T1", "built_from": ["test"], "links": [
    {"id": "rear-bumper>beam", "parent": "Rear Bumper", "child": "Rear Bumper Beam", "type": "behind", "side": "none",
     "min_hit": "heavy", "hidden": True, "strip_down": True, "area": "rear", "reason": "Behind the bumper.", "sources": []},
    {"id": "rear-bumper>plate", "parent": "Rear Bumper", "child": "Rear Number Plate", "type": "attached", "side": "none",
     "min_hit": "medium", "hidden": False, "strip_down": False, "area": "rear", "reason": "On the bumper.", "sources": []},
    {"id": "rear-bumper>floor", "parent": "Rear Bumper", "child": "Rear Floor Panel", "type": "crush", "side": "none",
     "min_hit": "crush", "hidden": True, "strip_down": True, "area": "rear", "reason": "Crush path.", "sources": []},
    {"id": "boot-lid>lock", "parent": "Boot Lid", "child": "Boot Lid Lock", "type": "opens_with", "side": "none",
     "min_hit": "medium", "hidden": True, "strip_down": False, "area": "rear", "reason": "Opens with it.", "sources": []},
    {"id": "quarter>tail-lamp", "parent": "{side} Rear Quarter Panel", "child": "{side} Tail Lamp", "type": "attached",
     "side": "same", "min_hit": "medium", "hidden": False, "strip_down": False, "area": "rear", "reason": "Next to it.",
     "sources": []},
    {"id": "bonnet>grille", "parent": "Bonnet", "child": "Front Grille", "type": "attached", "side": "none",
     "min_hit": "medium", "hidden": False, "strip_down": False, "area": "front", "reason": "In front of it.", "sources": []},
]}

KEY_A = {"name": "TEST CASE A", "folder": "case_a", "parts": [
    {"part": "Rear Bumper"}, {"part": "Boot Lid"}, {"part": "Boot Lid Lock"},
    {"part": "Rear Bumper Beam", "hidden": True}, {"part": "Right Tail Lamp"}]}
KEY_B = {"name": "TEST CASE B", "folder": "case_b", "parts": [{"part": "Bonnet"}, {"part": "Front Grille"}]}


def run_a(rep: int = 1) -> dict:
    """Case A as a contract 9 zone-mode run stored it: a heavy rear hit."""
    return {
        "case": "TEST CASE A", "rep": rep, "parts": {"found": 2},
        "drafted_parts": ["Rear Bumper", "Boot Lid", "Left Rear Quarter Panel", "Bonnet"],
        "drafted_items": [
            {"part": "Rear Bumper", "condition_tokens": ["Dented"], "severity": "Severe", "remarks": "Replace",
             "damage_type": "Dent", "ai_confidence": "High", "side_inferred": False, "found_by": ["overall", "zone:rear"],
             "seen_in": ["P01", "P02"], "photo_reference": "P01", "evidence": [{"photo": "P01", "box": None, "by": "overall"}]},
            {"part": "Boot Lid", "condition_tokens": ["Dented"], "ai_confidence": "High", "found_by": ["overall"],
             "seen_in": ["P02"], "photo_reference": "P02"},
            {"part": "Left Rear Quarter Panel", "condition_tokens": ["Dented"], "ai_confidence": "High",
             "side_inferred": False, "found_by": ["overall"], "seen_in": ["P01"], "photo_reference": "P01"},
            {"part": "Bonnet", "ai_confidence": "Medium", "found_by": ["overall"], "seen_in": ["P03"]},
        ],
        "suggestions": [{"key": "Boot Lid Lock", "part": "Boot Lid Lock", "source": "zone", "confidence": "Medium",
                         "found_by": ["zone:rear"], "photo_reference": "P02", "seen_in": ["P02"], "truth": "real",
                         "check": None}],
        "sorted_photos": [{"n": 1, "kind": "damage", "area": "rear", "quality": "good", "damage": True, "part": "Rear Bumper"},
                          {"n": 2, "kind": "damage", "area": "rear", "quality": "good", "damage": True, "part": "Boot Lid"},
                          {"n": 3, "kind": "overview", "area": "front", "quality": "good", "damage": False, "part": None}],
        "zones": {"status": "ok", "zones": [
            {"zone": "rear", "status": "ok", "photos": ["P01", "P02"], "found": 2, "intact": ["Rear Number Plate"]},
            {"zone": "front", "status": "failed", "photos": ["P03"], "intact": ["Front Grille"]}]},
    }


def run_b(rep: int = 1) -> dict:
    """Case B as an OLDER file kept it: names only for the suggestions, no
    found_by, no evidence, no zones."""
    return {"case": "TEST CASE B", "rep": rep, "parts": {"found": 1}, "drafted_parts": ["Bonnet"],
            "drafted_items": [{"part": "Bonnet", "condition_tokens": ["Dented"], "ai_confidence": "High"}],
            "second_look": {"status": "skipped", "suggested": []},
            "sorted_photos": [{"n": 1, "kind": "damage", "area": "front", "quality": "good", "damage": True,
                               "part": "Front Grille"}]}


# ---------------------------------------------------------------- the guard


def test_guard() -> None:
    print("the guard: no Gemini client, no network")
    for name in ("app.ai.structured", "app.ai.client", "google.genai", "app.ai.keys", "httpx", "requests"):
        try:
            __import__(name)
            raise AssertionError(f"{name} should be refused")
        except ImportError as exc:
            assert "replay_links" in str(exc), exc
    assert not [m for m in sys.modules if rl.blocked(m)], [m for m in sys.modules if rl.blocked(m)]
    ok("importing the Gemini client (or what calls it, or an HTTP library) is refused; none is loaded")
    for attempt in (lambda: socket.create_connection(("127.0.0.1", 9), timeout=1),
                    lambda: socket.getaddrinfo("example.com", 443),
                    lambda: socket.socket().connect(("127.0.0.1", 9))):
        try:
            attempt()
            raise AssertionError("a connection should be refused")
        except rl.GuardError:
            pass
    ok("any connection or name look-up raises GuardError")
    sys.modules["app.ai.client"] = types.ModuleType("app.ai.client")
    try:
        rl.install_guard()
        raise AssertionError("a loaded client should stop the guard")
    except rl.GuardError as exc:
        assert "app.ai.client" in str(exc), exc
    finally:
        del sys.modules["app.ai.client"]
    ok("the guard refuses to start when the client is loaded already")
    assert rl.GUARD["network"] == "refused" and "google.genai" in rl.GUARD["blocked_imports"]
    ok("its state goes in the output (guard)")


# ---------------------------------------------------------------- one run


def _parts(rows: list[dict]) -> list[str]:
    return [r["part"] for r in rows]


def test_inputs() -> None:
    print("the inputs rebuilt from a stored run, as _store hands them over")
    assert links.validate_table(TABLE) == [], links.validate_table(TABLE)
    a = run_a()
    others, source = rl.suggestions_of(a)
    assert source == "suggestions" and others == [{"key": "Boot Lid Lock", "part": "Boot Lid Lock", "source": "zone",
                                                   "confidence": "Medium", "found_by": ["zone:rear"],
                                                   "photo_reference": "P02", "seen_in": ["P02"]}], others
    assert rl.zone_intact_of(a) == {"zone:rear": ["Rear Number Plate"]}, rl.zone_intact_of(a)
    ok("contract 9 file: the stored suggestion rows (truth and verdict dropped); intact of the zone reads that answered")
    b = run_b()
    b["second_look"]["suggested"] = ["Front Grille", "Other / Not Listed"]
    others, source = rl.suggestions_of(b)
    assert source == "second_look.suggested" and [o["part"] for o in others] == ["Front Grille", "Other / Not Listed"]
    assert all(o["key"] for o in others) and rl.zone_intact_of(b) == {}
    assert rl.listed_of({"drafted_parts": ["Boot Lid", None]}) == [{"part": "Boot Lid"}]
    assert rl.suggestions_of({"suggestions": [{"part": "Parking Sensor", "source": "link"}]}) == ([], "suggestions")
    assert rl.suggestions_of({}) == ([], "none")
    ok("older files: second_look.suggested's names, drafted_parts' names; never a linked part as a suggestion")


def test_replay_run() -> None:
    print("one stored run replayed: links.build_all as _store calls it, scored strictly")
    inside = bench.hidden_children(TABLE)
    assert inside == {"Rear Bumper Beam", "Rear Floor Panel", "Boot Lid Lock"}, inside
    a = run_a()
    stored = json.dumps(a, sort_keys=True)
    out = rl.replay_run(a, KEY_A, TABLE, rl.frozen_options(), inside)
    assert json.dumps(a, sort_keys=True) == stored
    rows, b = out["link_suggestions"], out["links"]
    # Likely: the tail lamp (same side as the firm quarter panel) and the
    # grille (the bonnet: no words, unknown = medium); strip-down: the beam
    # and the floor (the rear end crushed: a heavy bumper and 3 rear parts);
    # the plate the rear zone called intact: demoted, last. The lock is the
    # zone suggestion already: joined, not shown.
    assert _parts(rows) == ["Left Tail Lamp", "Front Grille", "Rear Bumper Beam", "Rear Floor Panel",
                            "Rear Number Plate"], _parts(rows)
    assert [r["truth"] for r in rows] == ["wrong_side", "extra", "real", "extra", "extra"], rows
    assert [r["bucket"] for r in rows] == ["likely", "likely", "strip_down", "strip_down", "likely"], rows
    assert [r["hit"] for r in rows] == ["medium", "medium", "crush", "crush", "crush"], rows
    assert rows[0]["side_from"] == "Left Rear Quarter Panel" and rows[4]["intact_in"] == ["zone:rear"], rows
    assert rows[2]["link_rule"] == "T1:rear-bumper>beam" and rows[2]["hidden"] and rows[2]["needs_strip_down"]
    assert out["joins"] == {"Boot Lid Lock": ["Boot Lid"]} and b["zone_joined"] == 1, out["joins"]
    ok("the order, buckets, hits, side, intact demote and the zone twin joined -- as the engine gives them")
    want = {"shown": 5, "real": 1, "wrong_side": 1, "extra": 3, "wrong": 4, "wrong_per_real": 4.0,
            "real_parts": ["Rear Bumper Beam"], "wrong_side_parts": ["Left Tail Lamp"], "strip_down_wrong": 1,
            "hidden_shown": 2, "expected": 5, "list_found": 2, "lz_found": 3, "lzl_found": 4, "inside_expected": 2,
            "list_inside_found": 0, "lz_inside_found": 1, "lzl_inside_found": 2, "key_hidden_expected": 1,
            "lz_hidden_found": 0, "lzl_hidden_found": 1, "inside_parts": ["Boot Lid Lock", "Rear Bumper Beam"],
            "suggestions_from": "suggestions", "damage_list_identical": True}
    assert {k: b[k] for k in want} == want, {k: b[k] for k in want}
    assert b["by_link_type"]["attached"] == {"shown": 3, "real": 0, "wrong_side": 1, "extra": 2}, b["by_link_type"]
    assert b["by_link_type"]["crush"] == {"shown": 1, "real": 0, "wrong_side": 0, "extra": 1}
    assert b["by_bucket"]["strip_down"] == {"shown": 2, "real": 1, "wrong_side": 0, "extra": 1}, b["by_bucket"]
    assert b["by_hit"]["crush"] == {"shown": 3, "real": 1, "wrong_side": 0, "extra": 2}, b["by_hit"]
    assert b["by_hit"]["medium"] == {"shown": 2, "real": 0, "wrong_side": 1, "extra": 1}, b["by_hit"]
    ok("real = a key part the list AND the suggestions missed; wrong side; extra; recall l / l+s / l+s+links of "
       "all, inside and hidden parts; by kind, bucket and hit")
    variants = {name: rl.replay_run(run_a(), KEY_A, TABLE, rl.frozen_options(**change), inside)
                for name, change in rl.VARIANTS.items()}
    strict = variants["side_strict"]
    assert _parts(strict["link_suggestions"])[0] == "Tail Lamp (side undetermined)", strict["link_suggestions"]
    assert strict["links"]["real"] == 2 and strict["links"]["wrong_side"] == 0
    assert "Front Grille" not in _parts(variants["unknown_light"]["link_suggestions"])
    assert "Rear Number Plate" not in _parts(variants["intact_drop"]["link_suggestions"])
    assert "Rear Floor Panel" not in _parts(variants["no_crush"]["link_suggestions"])
    assert _parts(variants["default"]["link_suggestions"]) == _parts(rows)
    assert set(rl.VARIANTS) == {"default", "side_strict", "unknown_light", "intact_drop", "no_crush"}
    ok("the pre-registered variants each move one knob: strict side (only a twice-seen parent gives its side), "
       "unknown hit light, intact dropped, no crush links")
    old = rl.replay_run(run_b(), KEY_B, TABLE, rl.frozen_options(), inside)
    assert _parts(old["link_suggestions"]) == ["Front Grille"] and old["link_suggestions"][0]["bucket"] == "seen"
    assert old["link_suggestions"][0]["seen_in"] == ["P01"] and old["links"]["real"] == 1
    assert old["links"]["suggestions_from"] == "second_look.suggested", old["links"]
    ok("an older file (no found_by, no evidence, no zones): replayed; a sorter photo tagged with the child -> seen")


def test_damage_list() -> None:
    print("the damage list: asserted byte for byte")

    def meddling(listed, *args, **kwargs):
        listed[0]["remarks"] = "Repair"
        return links_build_all(listed, *args, **kwargs)

    links_build_all = links.build_all
    links.build_all = meddling
    try:
        out = rl.replay_run(run_a(), KEY_A, TABLE, rl.frozen_options(), set())
    finally:
        links.build_all = links_build_all
    assert out["links"]["damage_list_identical"] is False
    ok("a build that touched the list is caught (damage_list_identical false)")


# ---------------------------------------------------------------- main


def _files() -> tuple[Path, Path, Path]:
    one = {"label": "test-one", "runs": [run_a(1), run_b(1), run_a(2),
                                         {"case": "TEST CASE B", "rep": 2,
                                          "error": "RuntimeError: All Gemini fallback models (test) failed"}]}
    two = {"label": "test-two", "runs": [run_a(1), run_b(1), {"case": "TEST CASE Z", "rep": 1, "parts": {}}]}
    key = {"cases": [KEY_A, KEY_B]}
    paths = (_tmp / "one.json", _tmp / "two.json", _tmp / "key.json")
    for path, doc in zip(paths, (one, two, key)):
        path.write_text(json.dumps(doc), encoding="utf-8")
    return paths


def _main(argv: list[str]) -> tuple[int | str, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            code = rl.main(argv)
        except SystemExit as exc:
            code = exc.code
            print(exc.code)
    return code, buf.getvalue()


def test_freeze() -> None:
    print("the L1 freeze: table, skill files and knob defaults checked")
    text = ("# L1 freeze (lead)\n\n```\n" + "\n".join(f"{sha} *apps/bot-service/{path}"
                                                    for path, sha in rl.FROZEN_SHA256.items()) + "\n```\n")
    version, shas = rl.parse_freeze(text)
    assert version == "L1" and shas == rl.FROZEN_SHA256, (version, shas)
    ok("parse_freeze reads the version and the sha256 lines as L1-freeze.md writes them")
    check = rl.check_freeze()
    assert check["ok"] and check["version"] == "L1" and all(f["ok"] for f in check["files"].values()), check
    assert all(k["ok"] for k in check["knobs"].values()) and check["knobs"]["intake_links_max"]["default"] == 12
    ok("this checkout's table and skill files are L1's (sha256 of git's copy, whatever the line endings), the knob "
       "defaults the frozen ones")
    crlf = _tmp / "crlf.txt"
    crlf.write_bytes(b"a\r\nb\r\n")
    lf = _tmp / "lf.txt"
    lf.write_bytes(b"a\nb\n")
    assert bench.lf_sha256(crlf) == bench.lf_sha256(lf) and bench.lf_sha256(_tmp / "missing") is None
    ok("lf_sha256: the same sha on a CRLF and an LF checkout")
    one, _, key = _files()
    wrong = _tmp / "freeze-wrong.md"
    wrong.write_text(text.replace(rl.FROZEN_SHA256["app/ai/skills/rear.md"], "0" * 64), encoding="utf-8")
    code, said = _main([str(one), "--key", str(key), "--freeze", str(wrong)])
    assert code == 2 and "not the frozen L1 setup: app/ai/skills/rear.md" in said, said
    out = _tmp / "unfrozen.json"
    code, said = _main([str(one), "--key", str(key), "--freeze", str(wrong), "--not-frozen-ok", "--out", str(out)])
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0 and "NOT the frozen L1 setup" in said and doc["frozen"] is False, said
    assert not doc["table"]["files"]["app/ai/skills/rear.md"]["ok"]
    ok("a sha that isn't the freeze's stops the replay (exit 2); --not-frozen-ok labels it NOT FROZEN")


def test_main() -> None:
    print("main: a group of files, every variant, the output, determinism")
    one, two, key = _files()
    out = _tmp / "group.json"
    code, said = _main([str(one), str(two), "--key", str(key), "--out", str(out), "--variant", "all",
                        "--label", "test-group", "--ref", "test@0000000"])
    assert code == 0, said
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert (doc["kind"], doc["label"], doc["git_ref"], doc["frozen"], doc["gemini_calls"], doc["damage_list_identical"]) == (
        "replay_links", "test-group", "test@0000000", True, 0, True), doc["kind"]
    assert doc["guard"]["loaded_after"] == [] and doc["knobs"] == rl.FROZEN_KNOBS
    assert doc["options"]["side"] == "firm" and doc["options"]["types"] == ["attached", "opens_with", "behind", "crush"]
    assert sorted(doc["variants"]) == ["intact_drop", "no_crush", "side_strict", "unknown_light"]
    assert doc["variants"]["side_strict"]["options"]["side"] == "strict"
    ok("the frozen defaults (the headline) and every pre-registered variant; the guard's state; 0 calls")
    reps = [(r["case"], r["rep"], r["source"], r["source_rep"]) for r in doc["runs"]]
    assert reps == [("TEST CASE A", 1, "one.json", 1), ("TEST CASE B", 1, "one.json", 1), ("TEST CASE A", 2, "one.json", 2),
                    ("TEST CASE A", 3, "two.json", 1), ("TEST CASE B", 3, "two.json", 1)], reps
    first, second = doc["sources"]
    assert first["reps"] == [1, 2] and second["reps"] == [3] and first["replayed"] == 3, first
    assert [s["why"] for s in first["skipped"]] == ["RuntimeError: All Gemini fallback models (test) failed"]
    assert [s["why"] for s in second["skipped"]] == ["not in the key"], second
    ok("each file's repetitions are 4-case runs numbered on across the files; an errored run is skipped and named")
    s = doc["summary"]
    assert s["complete_reps"] == [1, 3] and set(s["cases"]) == {"TEST CASE A", "TEST CASE B"}, s["complete_reps"]
    assert "link_shown" in s["total"] and "link_real" in s["cases"]["TEST CASE A"]["metrics"]
    assert s["cases"]["TEST CASE A"]["metrics"]["link_shown"]["n"] == 3
    assert all(r["links"]["damage_list_identical"] for r in doc["runs"])
    ok("per case and per 4-case run (summarize's TOTAL over the complete ones)")
    assert "REPLAY linked parts: table L1 (frozen), 0 Gemini calls (guard on)" in said and "== variant no_crush" in said
    assert "TOTAL (2 complete 4-case runs)" in said and "link_type" in said and "strip_down" in said, said
    ok("printed: per case, TOTAL, by kind / bucket / hit, per variant")
    again = _tmp / "group-again.json"
    _main([str(one), str(two), "--key", str(key), "--out", str(again), "--variant", "all", "--label", "test-group",
           "--ref", "test@0000000"])
    assert out.read_bytes() == again.read_bytes()
    ok("deterministic: the same files give the same output, byte for byte")
    code, said = _main([str(one), "--key", str(key), "--out", str(one)])
    assert code != 0 and "another file" in said, said
    ok("--out over a stored result is refused")

    def meddling(listed, *args, **kwargs):
        listed[0]["remarks"] = "Repair"
        return saved(listed, *args, **kwargs)

    saved = links.build_all
    links.build_all = meddling
    try:
        code, said = _main([str(one), "--key", str(key)])
    finally:
        links.build_all = saved
    assert code == 3 and "damage list CHANGED" in said, said
    ok("a changed damage list fails the replay (exit 3)")


def test_gate() -> None:
    print("gate.py --suggestions: G1-G6 on a replay and on a live result")
    one, two, key = _files()
    out = _tmp / "gate-replay.json"
    _main([str(one), str(two), "--key", str(key), "--out", str(out), "--variant", "intact_drop"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = gate.main(["--suggestions", str(out)])
    text = buf.getvalue()
    # Case A finds about 1 real a run with L1's table: G1 (>= 5) fails.
    assert code == 1 and "FAIL G1" in text and "TEST CASE" not in text and "case 1" in text, text
    assert "ok   G5: the damage list identical" in text and "variant default" in text, text
    ok("a replay: the per-case table without case names (case 1..), each check; G1 fails, the list identical")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        gate.main(["--suggestions", str(out), "--names", "--variant", "intact_drop"])
    assert "TEST CASE A" in buf.getvalue() and "pre-registered, sensitivity only" in buf.getvalue(), buf.getvalue()
    ok("--names prints the names; --variant gates a variant, labelled sensitivity only")

    def live(rows: list[tuple[str, int, dict]]) -> dict:
        runs = []
        for case, rep, b in rows:
            block = {"shown": 0, "real": 0, "wrong_side": 0, "extra": 0, "wrong": 0, "strip_down_wrong": 0,
                     "hidden_shown": 0, "lz_found": 3, "lzl_found": 3, "expected": 10, "lz_inside_found": 0,
                     "lzl_inside_found": 0, "inside_expected": 4, "key_hidden_expected": 1, "lz_hidden_found": 0,
                     "lzl_hidden_found": 0}
            block.update(b)
            block["wrong"] = block["wrong_side"] + block["extra"]
            block["lzl_found"] = block["lz_found"] + block["real"]
            runs.append({"case": case, "rep": rep, "parts": {"found": 3, "expected": 10, "visible_found": 3,
                                                             "visible_expected": 10, "extra": [], "wrong_side": [],
                                                             "found_parts": {}, "missed": []}, "links": block})
        return {"label": "test-live", "ai_settings": {"intake_links": "on", "link_table_version": "L1",
                                                      "link_table_sha256": rl.FROZEN_SHA256["app/intake/link_table.json"]},
                "runs": runs}

    good = live([("A", 1, {"shown": 8, "real": 3, "extra": 2}), ("B", 1, {"shown": 9, "real": 3, "extra": 3}),
                 ("A", 2, {"shown": 10, "real": 2, "extra": 3}), ("B", 2, {"shown": 7, "real": 3, "extra": 1})])
    path = _tmp / "live.json"
    path.write_text(json.dumps(good), encoding="utf-8")
    base = _tmp / "live-base.json"
    base.write_text(json.dumps({"label": "base", "runs": [dict(r, links=None) for r in good["runs"]]}), encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = gate.main(["--suggestions", str(path)])
    assert code == 1 and "FAIL G5: the damage list: a live result needs --baseline" in buf.getvalue(), buf.getvalue()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = gate.main(["--suggestions", str(path), "--baseline", str(base), "--limit", "G1=5.5"])
    text = buf.getvalue()
    # The list gate wants more found than the baseline: the same list fails it.
    assert code == 1 and "ok   G1: real parts added by links per 4-case run 5.5" in text, text
    assert "ok   G2" in text and "ok   G3" in text and "ok   G4" in text and "ok   G6" in text, text
    assert "FAIL G5: the list gate against the baseline FAIL" in text and "set with --limit" in text, text
    ok("a live result: G1-G4, G6 from its runs' links; G5 = the contract 8 list gate against --baseline")
    bad = live([("A", 1, {"shown": 13, "real": 1, "wrong_side": 1, "extra": 4, "strip_down_wrong": 7}),
                ("B", 1, {"shown": 11, "real": 0, "extra": 5}),
                ("A", 2, {"shown": 11, "real": 1, "wrong_side": 1, "extra": 1}), ("B", 2, {"shown": 3, "real": 0, "extra": 4})])
    path.write_text(json.dumps(bad), encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = gate.main(["--suggestions", str(path)])
    text = buf.getvalue()
    for check in ("FAIL G1", "FAIL G2", "FAIL G3", "FAIL G4", "FAIL G6"):
        assert check in text, (check, text)
    assert "case 1 3.5 per real" in text and "case 2 4.5 wrong a run with no real" in text, text
    ok("each check fails on its own number: a case over 2.5 wrong per real, one with no real over 4 wrong a run, "
       "a run over 12 shown, the strip-down flood")
    nothing = live([])
    nothing["runs"] = [{"case": "A", "rep": 1, "parts": {"found": 1}}]
    path.write_text(json.dumps(nothing), encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert gate.main(["--suggestions", str(path)]) == 1
    assert "no case-run with linked parts" in buf.getvalue()
    ok("a file without linked parts fails, said so")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        compare.main([str(out), str(out), "--after-variant", "intact_drop", "--total-only"])
    assert "[intact_drop]" in buf.getvalue() and "link_shown" in buf.getvalue() and "lzl_recall" in buf.getvalue()
    ok("compare.py reads the replay and its variants (--after-variant)")


def main() -> int:
    test_guard()
    test_inputs()
    test_replay_run()
    test_damage_list()
    test_freeze()
    test_main()
    test_gate()
    print("all replay_links tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
