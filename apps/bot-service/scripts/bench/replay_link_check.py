"""LIVE: the linked check alone (contract 10, app/ai/link_check.py) on stored
benchmark runs -- Wave 2's E9 arms (plans/2026-11-22-enhance-staging 3c) on
identical reads: no sort, no main read, no zone read; only the linked
check's calls are made (one per area, at most intake_link_check_max_calls:
about 2-3 a case).

For each stored run of the result file (benchmark_intake.py --out; an
errored run, or one with no list, is skipped -- never scored as a miss), as
the app does after a reading:

  - a fresh draft of its case: the photos copied and previewed as an
    upload does, in the scratch STORAGE_DIR -- the files and their order
    must be the run's (sorted_photos), else the run is an error;
  - each photo takes what that run's sorter said (kind, area, quality,
    damage, part, note; the box and the geometry when the run kept them);
  - the damage list is the run's drafted_items, photos back to "@<photo
    id>"; the AI draft holds the run's other suggestions as the reading
    stored them (ai_draft["zone_suggestions"]: the stored non-link rows,
    without what the linked parts' build and the checks added to them),
    what each zone read called intact (zones[].intact) and the reads that
    answered;
  - second_look.run_for_intake builds and stores the linked parts exactly
    as after a reading (production's _store and links.build_all; the second
    look's own call is off: 0 calls), with the linked-part knobs the stored
    file recorded (ai_settings; the car map must have been off);
  - link_check.run_for_intake -- production's plan (plan_groups) and calls
    (ask -> call_structured): every request waits for the shared rate
    limiter's slot (in the staging lane, the quota guard's too), with the
    busy handling and the time box of the app;
  - the linked parts are scored as a benchmark run scores them
    (benchmark_intake.score_links against the key, their truth), with the
    check's verdicts by truth (benchmark_intake.link_check_metrics: lcv_*).
    The damage list must come out as it went in (damage_list_identical,
    every run); links_match says the linked parts rebuilt are the stored
    run's (the same keys).

The arms (each a flag; at none, the app's defaults -- two areas, the L1
skill files):

    E9a  --link-check-max-calls 3
    E9b  --link-skill L2 [--link-prompt v2]
    B8   --link-models gemini-3.1-flash-lite,gemini-3.5-flash-lite
    F6   --link-hidden-no cannot_tell (code only, +0 calls)
    and --thinking-level-link, --link-order, --link-signs as benchmark_intake.

On the server, through run_bench.sh (the result file is the first option:
its path inside the container):

    BENCH_SCRIPT=replay_link_check bash run_bench.sh w2-lc-base GIT_REF /tmp/bench/out/STORED.json
    BENCH_SCRIPT=replay_link_check bash run_bench.sh w2-lc-e9a GIT_REF /tmp/bench/out/STORED.json \\
        --link-check-max-calls 3

By hand (a scratch database and storage, as benchmark_intake.py):

    BENCHMARK_SCRATCH=1 DATABASE_URL=sqlite:////tmp/x/run.db STORAGE_DIR=/tmp/x/storage \\
        python scripts/bench/replay_link_check.py RESULT.json --cases DIR --key key.json --out OUT.json
        [--repeat N] [--only CASE] [--rep N] [--label L] [--ref R] [--scratch DIR] [the arm's flags]

The output is a result file ("kind": "replay_link_check", "runs", and the
"summary" benchmark_intake.summarize makes of them): scripts/bench/
compare.py BASE.json ARM.json and gate.py --suggestions read it like one.
Gemini calls: the linked check's only; the key is the environment's
company key, never printed; the output holds part names, verdicts and the
check's short reasons -- no photo -- and stays with the benchmark files.
"""
import argparse
import copy
import json
import os
import shutil
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
BOT_DIR = SCRIPTS_DIR.parent
for _path in (str(BOT_DIR), str(SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# benchmark_intake imports nothing of the app when it loads.
import benchmark_intake as bench  # noqa: E402

KIND = "replay_link_check"
# What the linked parts' build and the checks add to a suggestion (_store's
# join with a link, the car map's review fields, the walk's, the re-check,
# the key's truth): dropped from a stored row, so it is the suggestion the
# reading stored (ai_draft["zone_suggestions"]); the build joins it again.
ADDED_BY_LINKS = tuple(f for f in bench._LINK_SUGGESTION_FIELDS if f not in ("severity", "condition_tokens", "remarks"))
DROPPED = (set(ADDED_BY_LINKS) | set(bench._CARMAP_SUGGESTION_FIELDS) | set(bench._WALK_SUGGESTION_FIELDS)
           | {"check", "truth", "oem", "covers", "name", "other_side_listed"})
# The linked parts' knobs a result file records (ai_settings, while the
# links were on), replayed as recorded; a K4 knob absent was at its default.
C10_KNOBS = ("intake_links_max", "intake_links_types", "intake_links_side", "intake_links_unknown_hit",
             "intake_links_intact")


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="replay_link_check.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("result", type=Path, help="a benchmark result file (benchmark_intake.py --out) run with --links")
    ap.add_argument("--cases", type=Path, default=bench._env_path("BENCH_CASES"), help="the cases' photo folders")
    ap.add_argument("--key", type=Path, default=bench._env_path("BENCH_KEY"), help="the answer key (key.json)")
    ap.add_argument("--out", type=Path, help="the replay's result JSON, rewritten after every run")
    ap.add_argument("--repeat", type=bench._positive, default=1, metavar="N", help="replay every stored run N times")
    ap.add_argument("--only", action="append", help="case name; repeatable")
    ap.add_argument("--rep", type=bench._positive, metavar="N", help="only the stored result's repetition N")
    ap.add_argument("--label", help="name of this replay (default: the --out file name)")
    ap.add_argument("--ref", help="git ref (and commit) the code came from")
    ap.add_argument("--scratch", type=Path, metavar="DIR",
                    help="a throwaway folder for the database and storage (stands for BENCHMARK_SCRATCH=1)")
    # The arms (benchmark_intake's flags of the same names).
    ap.add_argument("--link-check-max-calls", type=bench._positive, metavar="N",
                    help="E9a: areas checked a case (settings.intake_link_check_max_calls; default 2)")
    ap.add_argument("--link-skill", metavar="VERSION", help="E9b: L1 or L2 (settings.intake_link_skill)")
    ap.add_argument("--link-prompt", metavar="VERSION", help="v1 or v2 (settings.intake_link_prompt)")
    ap.add_argument("--link-signs", action="store_true", help="L3 (settings.intake_link_signs); needs --link-skill L2")
    ap.add_argument("--link-order", metavar="ORDER", help="candidates_first or photos_first (settings.intake_link_order)")
    ap.add_argument("--thinking-level-link", metavar="LEVEL", help="settings.gemini_thinking_level_link")
    ap.add_argument("--link-models", type=bench._chain, metavar="CHAIN", help="B8: settings.gemini_link_chain")
    ap.add_argument("--link-hidden-no", metavar="RULE",
                    help="F6: keep or cannot_tell (settings.intake_link_hidden_no; code only)")
    return ap.parse_args(argv)


class ReplayError(Exception):
    pass


# ---------------------------------------------------------------- the stored run, as the reading left it


def _refs_to_ids(row: dict, id_of: dict[str, str]) -> dict:
    """A copy of a stored item or suggestion with its photos ("P07") back to
    "@<photo id>": photo_reference, seen_in, each evidence entry's photo."""
    out = copy.deepcopy(row)
    if isinstance(out.get("photo_reference"), str):
        out["photo_reference"] = id_of.get(out["photo_reference"], out["photo_reference"])
    if isinstance(out.get("seen_in"), list):
        out["seen_in"] = [id_of.get(r, r) for r in out["seen_in"]]
    if isinstance(out.get("evidence"), list):
        out["evidence"] = [dict(e, photo=id_of.get(e.get("photo"), e.get("photo")))
                           for e in out["evidence"] if isinstance(e, dict)]
    return out


def listed_of(run: dict, id_of: dict[str, str]) -> list[dict]:
    """The run's damage list as the draft held it."""
    return [_refs_to_ids(item, id_of) for item in run.get("drafted_items") or [] if isinstance(item, dict)
            and item.get("part")]


def zone_suggestions_of(run: dict, id_of: dict[str, str]) -> list[dict]:
    """The run's other suggestions (never a linked part) as the reading
    stored them: without what the linked parts and the checks added."""
    out = []
    for row in run.get("suggestions") or []:
        if not isinstance(row, dict) or not row.get("part") or row.get("source") == "link":
            continue
        out.append({k: v for k, v in _refs_to_ids(row, id_of).items() if k not in DROPPED})
    return out


def zone_intact_of(run: dict) -> dict[str, list[str]]:
    """ai_draft["zone_intact"] as the run's zone reads left it."""
    zones = (run.get("zones") or {}).get("zones") if isinstance(run.get("zones"), dict) else None
    return {f"zone:{z['zone']}": [p for p in z["intact"] if isinstance(p, str)]
            for z in zones or [] if isinstance(z, dict) and z.get("status") == "ok" and z.get("zone")
            and isinstance(z.get("intact"), list)}


def zone_reads_of(run: dict) -> list[str]:
    zones = (run.get("zones") or {}).get("zones") if isinstance(run.get("zones"), dict) else None
    return ["overall"] + [f"zone:{z['zone']}" for z in zones or []
                          if isinstance(z, dict) and z.get("status") == "ok" and z.get("zone")]


def stored_knobs(doc: dict) -> dict:
    """The linked parts' knobs the stored file ran with ({Settings field:
    value}), from its ai_settings; the K4 ones it doesn't name at their
    defaults. Exits on a file whose linked parts were off, or built from
    the car map (this replays L1 only)."""
    s = doc.get("ai_settings") or {}
    if s.get("intake_links") not in ("on", "shadow"):
        sys.exit("replay_link_check: the stored file ran without --links: it holds no linked parts to check")
    if s.get("intake_car_map") in ("on", "shadow"):
        sys.exit("replay_link_check: the stored file built its linked parts from the car map; this replays L1 only")
    out = {k: s[k] for k in C10_KNOBS if s.get(k) is not None}
    out.update({k: s.get(k, default) for k, default in bench.K4_DEFAULTS.items()})
    return out


# ---------------------------------------------------------------- one run


def _rows(look: dict, label_of: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """(the other suggestions, the linked parts) as a benchmark run keeps
    them, photos by case label."""
    items = [it for it in look.get("items") or [] if isinstance(it, dict) and it.get("part")]
    fields = bench._SUGGESTION_FIELDS + bench._LINK_SUGGESTION_FIELDS
    others = bench.suggestion_rows({}, label_of, bench._SUGGESTION_FIELDS,
                                   [it for it in items if it.get("source") != "link"])
    linked = bench.suggestion_rows({}, label_of, fields, [it for it in items if it.get("source") == "link"])
    return others, linked


def replay_run(run: dict, case: dict, cases_dir: Path, inside: set[str]) -> dict:
    """One stored run's linked parts rebuilt on a fresh draft of its case,
    then checked live, scored."""
    A = bench._app()
    from app.ai import link_check, second_look

    out: dict = {"case": run.get("case"), "source_rep": int(run.get("rep") or 1)}
    rid = None
    try:
        rid = out["report_id"] = bench.load_case(cases_dir / case["folder"])
        db = A.SessionLocal()
        try:
            photos = A.ordered_photos(db, rid)
            stored = run.get("sorted_photos") or []
            if [s.get("file") for s in stored] != [p.original_name for p in photos]:
                raise ReplayError("the case folder's photos are not the run's (files or order differ)")
            for s, p in zip(stored, photos):
                p.kind, p.area, p.quality, p.part, p.note = (s.get("kind"), s.get("area"), s.get("quality"),
                                                             s.get("part"), s.get("note"))
                p.shows_damage = bool(s.get("damage"))
                if "box" in s and hasattr(p, "damage_box"):
                    p.damage_box = s.get("box")
                if "geometry" in s and hasattr(p, "geometry"):
                    p.geometry = s.get("geometry")
            id_of = {f"P{i + 1:02d}": f"@{p.id}" for i, p in enumerate(photos)}
            label_of = {v: k for k, v in id_of.items()}
            listed = listed_of(run, id_of)
            report = db.get(A.Report, rid)
            session = db.get(A.IntakeSession, rid)
            report.data = {**(report.data or {}), "damage_summary": listed,
                           "damaged_parts": [it["part"] for it in listed]}
            session.ai_draft = {"damage_summary": copy.deepcopy(listed),
                                "zone_suggestions": zone_suggestions_of(run, id_of),
                                "zone_intact": zone_intact_of(run), "zone_reads": zone_reads_of(run)}
            db.commit()
        finally:
            db.close()
        before = json.dumps(listed, sort_keys=True)
        bench.ACCOUNTING.clear()
        actor = A.AiActor.cli()
        # The linked parts, as after a reading: the second look's own call
        # is off (settings.second_look_enabled False), so 0 calls here.
        second_look.run_for_intake(rid, actor)
        started = time.monotonic()
        link_check.run_for_intake(rid, actor)
        out["seconds"] = round(time.monotonic() - started, 1)
        db = A.SessionLocal()
        try:
            data = db.get(A.Report, rid).data or {}
        finally:
            db.close()
        look = data.get("second_look") or {}
        identical = json.dumps(data.get("damage_summary") or [], sort_keys=True) == before
        others, linked = _rows(look, label_of)
        drafted = [it["part"] for it in listed]
        out["parts"] = bench.score_parts(case["parts"], drafted)
        out["drafted_parts"] = drafted
        out["suggestions"] = others
        bench.mark_truth(out["suggestions"], case["parts"], out["parts"])
        out["link_suggestions"] = linked
        out["links"] = bench.score_links(case["parts"], drafted, [r.get("part") for r in others], linked,
                                         inside=inside)
        stored_links = [r for r in run.get("link_suggestions") or [] if isinstance(r, dict)]
        stored_keys = {r.get("key") or r.get("part") for r in stored_links}
        rebuilt_keys = {r.get("key") or r.get("part") for r in linked}
        out["links"].update(mode="on", damage_list_identical=identical, links_match=stored_keys == rebuilt_keys,
                            links_stored=len(stored_links), links_rebuilt=len(linked))
        # The stored run's own verdicts, for a paired look (None: unchecked).
        out["stored_link_check"] = {str(r.get("key") or r.get("part")): (r.get("link_check") or {}).get("verdict")
                                    for r in stored_links}
        out["link_run"] = look.get("link_run")
        out["calls"] = bench.ACCOUNTING.call_counts()
        out["accounting"] = bench.ACCOUNTING.summary()
        out["ai_calls"] = bench.ACCOUNTING.call_list()
    except ReplayError as exc:
        out["error"] = str(exc)
    except Exception as exc:  # recorded, and the next run still goes
        out["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        if rid:
            shutil.rmtree(Path(A.settings.storage_dir) / "intake" / rid, ignore_errors=True)
    return out


def run_line(r: dict) -> str:
    if r.get("error"):
        return f"   ERROR {r['error']}"
    c = r.get("link_run") or {}
    m = bench.link_check_metrics(r.get("link_suggestions"))
    said = ", ".join(f"{g} {v} {m[f'lcv_{g}_{v}']}" for g in ("real", "wrong") for v in bench.LINK_VERDICTS
                     if m.get(f"lcv_{g}_{v}"))
    links = r.get("links") or {}
    return (f"   linked check {c.get('status')} on {c.get('model') or '-'} in {r.get('seconds')}s, "
            f"{c.get('calls', 0)} call(s)" + (f" ({c['note']})" if c.get("note") else "")
            + f"; {links.get('shown')} linked parts ({links.get('real')} real), answered "
            f"{m.get('lcv_answered', 0)}" + (f": {said}" if said else "")
            + ("" if links.get("links_match") else "; NOT the stored run's linked parts")
            + ("" if links.get("damage_list_identical") else "; THE LIST CHANGED"))


def summary_lines(runs: list[dict]) -> list[str]:
    ok = [r for r in runs if bench.run_ok(r)]
    total: dict[str, int] = {}
    for r in ok:
        for k, v in bench.link_check_metrics(r.get("link_suggestions")).items():
            total[k] = total.get(k, 0) + v
    calls = sum((r.get("link_run") or {}).get("calls", 0) for r in ok)
    out = [f"REPLAY linked check: {len(ok)} of {len(runs)} run(s) replayed, {calls} call(s); answered "
           f"{total.get('lcv_answered', 0)}, unanswered {total.get('lcv_unanswered', 0)}"]
    for group in ("real", "wrong"):
        out.append(f"  {group:5}: " + " ".join(f"{v} {total.get(f'lcv_{group}_{v}', 0)}" for v in bench.LINK_VERDICTS))
    if any(not (r.get("links") or {}).get("links_match") for r in ok):
        out.append("  NOTE: some runs' rebuilt linked parts differ from the stored run's (another table or knobs?)")
    for r in runs:
        if r.get("error"):
            out.append(f"  ERROR {r.get('case')} run {r.get('rep')}: {r['error']}")
    return out


def arm_settings(args: argparse.Namespace, config) -> dict:
    """The arm's flags as {Settings field: value}, checked as benchmark_
    intake checks them (the linked check is on here)."""
    given = {"intake_link_check": "lite"}
    out = bench.light_settings(args, config, False, given)
    out.update(bench.wave2_settings(args, config, False, given))
    if args.link_hidden_no is not None:
        try:
            out["intake_link_hidden_no"] = config._one_of(args.link_hidden_no, config.LINK_HIDDEN_NO, "the rule")
        except ValueError as exc:
            sys.exit(f"--link-hidden-no: {exc}")
    return out


def main(argv=None) -> int:
    args = parse_args(argv)
    missing = [f"--{n}" for n in ("cases", "key", "out") if getattr(args, n) is None]
    if missing:
        sys.exit(f"needs {', '.join(missing)}")
    if args.out.resolve() == args.result.resolve():
        sys.exit("--out must be another file: the stored result is kept as it is")
    if args.scratch:
        bench._use_scratch(args.scratch)
    # A scratch database and storage, never the live stack's (review L6).
    problem = bench.scratch_problem()
    if problem:
        sys.exit(problem)
    doc = json.loads(args.result.read_text(encoding="utf-8"))
    key = {c["name"]: c for c in json.loads(args.key.read_text(encoding="utf-8"))["cases"]}
    stored = [r for r in doc.get("runs") or [] if isinstance(r, dict) and bench.run_ok(r) and r.get("drafted_items")
              and (not args.only or r.get("case") in args.only) and (args.rep is None or int(r.get("rep") or 1) == args.rep)]
    unknown = sorted({str(r.get("case")) for r in stored} - set(key))
    if unknown:
        sys.exit(f"{len(unknown)} case(s) of the result are not in the key")
    if not stored:
        sys.exit("no run of the result has a list to replay (an errored file, or one from before drafted_items)")
    knobs = stored_knobs(doc)

    A = bench._app()
    from app import config
    from app.intake import links

    if links.TABLE is None:
        sys.exit("replay_link_check: the link table (app/intake/link_table.json) didn't load; see the log")
    # As after a reading with the linked parts on and the check "lite": the
    # second look's own call, the re-check, the car map and the walk off.
    base = {"intake_links": "on", "intake_link_check": "lite", "second_look_enabled": False,
            "intake_check_mode": "off", "intake_car_map": "off", "intake_assembly_walk": "off",
            "intake_links_catalogue": False}
    for field, value in {**base, **knobs, **arm_settings(args, config)}.items():
        setattr(A.settings, field, value)
    A.init_db()
    inside = bench.hidden_children(links.TABLE)
    reps = sorted({int(r.get("rep") or 1) for r in stored})
    out_doc = {
        "kind": KIND, "label": args.label or args.out.stem, "git_ref": args.ref, "source": str(args.result),
        "source_label": doc.get("label"),
        "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "ai_settings": bench.ai_settings(A.settings), "knobs": knobs, "started": bench._now(), "finished": None,
        "damage_list_identical": None, "links_match": None, "runs": [], "summary": None,
    }
    for again in range(1, args.repeat + 1):
        for run in stored:
            print(f"== {run['case']} (stored run {run.get('rep', 1)})"
                  + (f"  (replay {again}/{args.repeat})" if args.repeat > 1 else ""), flush=True)
            r = replay_run(run, key[run["case"]], args.cases, inside)
            # A 4-case run is one stored repetition replayed once.
            r["rep"] = (again - 1) * len(reps) + reps.index(int(run.get("rep") or 1)) + 1
            out_doc["runs"].append(r)
            print(run_line(r), flush=True)
            out_doc["summary"] = bench.summarize(out_doc["runs"])
            bench._write_json(args.out, out_doc)
    ok = [r for r in out_doc["runs"] if bench.run_ok(r)]
    out_doc["damage_list_identical"] = bool(ok) and all(r["links"].get("damage_list_identical") for r in ok)
    out_doc["links_match"] = bool(ok) and all(r["links"].get("links_match") for r in ok)
    print()
    for line in summary_lines(out_doc["runs"]):
        print(line)
    out_doc["finished"] = bench._now()
    bench._write_json(args.out, out_doc)
    print("written", args.out)
    if ok and not out_doc["damage_list_identical"]:
        print("replay_link_check: the damage list CHANGED in a replay -- the linked check must never touch it",
              file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
