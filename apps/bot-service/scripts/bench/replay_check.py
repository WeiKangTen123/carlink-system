"""LIVE: the re-check alone (contract 9, app/ai/check.py) on a stored
benchmark result's suggestions -- runs R4 (the Decisions API) and R5 (the
lite re-check) -- without sorting or reading again; and, offline, the sweep
of the yes / no cut-offs over what came back.

For each run of the result file (every case and repetition, or --only /
--rep): the case's photos go through what an upload does (a copy and its
1600 px preview, in the scratch STORAGE_DIR), each takes what that run's
sorter said of it (kind, area, quality, damage, part, note, box), the run's
suggestions go on the draft as the second look left them (their photos back
to "@<photo id>", any earlier verdict dropped), and check.run_for_intake
asks about them as the app would: the same photo choice, the same
requests. Each answer is set against the truth the run stored for that
suggestion ("real", "wrong_side" or "extra" against the key; scored again
here from the run's list, so a changed key counts).

On the server, through run_bench.sh (the result file is the first option:
its path inside the container):

    BENCH_SCRIPT=replay_check bash ~/carlink-bench/run_bench.sh c9-check-dec GIT_REF \\
        /tmp/bench/out/c9-zBev-35.json --check decision

By hand (a scratch database and storage, as benchmark_intake.py):

    BENCHMARK_SCRATCH=1 DATABASE_URL=sqlite:////tmp/x/run.db STORAGE_DIR=/tmp/x/storage \\
        python scripts/bench/replay_check.py RESULT.json --cases DIR --key key.json --out OUT.json \\
        --check decision|lite|decision_then_lite [--repeat N] [--only CASE] [--rep N]
        [--decision-model M] [--check-models CHAIN] [--thinking-level-check L] [--check-resolution R]
        [--check-yes P] [--check-no P] [--label L] [--ref R] [--scratch DIR]

Offline, no call -- the cut-offs over one or more replays (or benchmark
results run with --check):

    python scripts/bench/replay_check.py --sweep OUT.json [MORE.json ...]

The gate (R4, R5): "no" on at least 40% of the wrong suggestions, yes or
unsure on at least 85% of the real ones, and (the Decisions API) a median
of at most 3 s a request. The sweep gives, for each "no" cut-off, the share
of wrong suggestions at or under it and of real ones over it -- the gate's
two numbers -- and for each "yes" cut-off the real and wrong ones at or over
it: settings.intake_check_no / _yes come from it.

Gemini calls: one or two Decisions API requests per run (decision), one
lite call per run (lite). The key is the environment's company key, never
printed; the output holds part names, verdicts and p -- no photo -- and
stays with the benchmark files.
"""
import argparse
import json
import os
import shutil
import statistics
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

# The gate (contract 9 section 9, R4 / R5).
GATE_WRONG_NO = 0.40
GATE_REAL_KEPT = 0.85
GATE_MEDIAN_S = 3.0
NO_CUTS = tuple(round(0.05 * i, 2) for i in range(1, 11))       # 0.05 .. 0.50
YES_CUTS = tuple(round(0.5 + 0.05 * i, 2) for i in range(0, 10))  # 0.50 .. 0.95


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="replay_check.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("result", type=Path, nargs="?", help="a benchmark result file (benchmark_intake.py --out)")
    ap.add_argument("--sweep", type=Path, nargs="+", metavar="FILE",
                    help="offline: the yes / no cut-offs over replay outputs or benchmark results; no call")
    ap.add_argument("--cases", type=Path, default=bench._env_path("BENCH_CASES"), help="the cases' photo folders")
    ap.add_argument("--key", type=Path, default=bench._env_path("BENCH_KEY"), help="the answer key (key.json)")
    ap.add_argument("--out", type=Path, help="the replay's result JSON, rewritten after every run")
    ap.add_argument("--check", metavar="MODE", help="decision, lite or decision_then_lite (settings.intake_check_mode)")
    ap.add_argument("--decision-model", metavar="MODEL", help="settings.gemini_decision_model")
    ap.add_argument("--check-models", type=bench._chain, metavar="CHAIN", help="settings.gemini_check_chain")
    ap.add_argument("--thinking-level-check", metavar="LEVEL", help="settings.gemini_thinking_level_check")
    ap.add_argument("--check-resolution", metavar="RES", help="settings.intake_check_resolution")
    ap.add_argument("--check-yes", type=float, metavar="P", help="settings.intake_check_yes")
    ap.add_argument("--check-no", type=float, metavar="P", help="settings.intake_check_no")
    ap.add_argument("--repeat", type=bench._positive, default=1, metavar="N", help="replay every run N times")
    ap.add_argument("--only", action="append", help="case name; repeatable")
    ap.add_argument("--rep", type=bench._positive, metavar="N", help="only the stored result's repetition N")
    ap.add_argument("--label", help="name of this replay (default: the --out file name)")
    ap.add_argument("--ref", help="git ref (and commit) the code came from")
    ap.add_argument("--scratch", type=Path, metavar="DIR",
                    help="a throwaway folder for the database and storage (stands for BENCHMARK_SCRATCH=1)")
    return ap.parse_args(argv)


# ---------------------------------------------------------------- offline


def _p(row: dict):
    check = row.get("check") if isinstance(row.get("check"), dict) else {}
    p = check.get("p")
    return p if isinstance(p, (int, float)) and not isinstance(p, bool) else None


def _share(values: list, test) -> float | None:
    return round(sum(1 for v in values if test(v)) / len(values), 3) if values else None


def sweep(rows: list[dict]) -> dict:
    """The cut-offs over suggestions that have a truth and a Decisions API
    p: for each "no" cut-off, the wrong ones at or under it (said "no") and
    the real ones over it (kept); for each "yes" cut-off, the real and the
    wrong ones at or over it. best_no: the highest "no" cut-off that still
    keeps GATE_REAL_KEPT of the real ones."""
    real = [_p(r) for r in rows if r.get("truth") == "real" and _p(r) is not None]
    wrong = [_p(r) for r in rows if r.get("truth") in ("wrong_side", "extra") and _p(r) is not None]
    no = [{"no": cut, "wrong_no": _share(wrong, lambda p, c=cut: p <= c), "real_kept": _share(real, lambda p, c=cut: p > c)}
          for cut in NO_CUTS]
    for row in no:
        row["passes"] = (row["wrong_no"] is not None and row["real_kept"] is not None
                         and row["wrong_no"] >= GATE_WRONG_NO and row["real_kept"] >= GATE_REAL_KEPT)
    yes = [{"yes": cut, "real_yes": _share(real, lambda p, c=cut: p >= c), "wrong_yes": _share(wrong, lambda p, c=cut: p >= c)}
           for cut in YES_CUTS]
    keeping = [row["no"] for row in no if row["real_kept"] is not None and row["real_kept"] >= GATE_REAL_KEPT]
    return {"real": len(real), "wrong": len(wrong), "no": no, "yes": yes, "best_no": max(keeping) if keeping else None}


def sweep_lines(result: dict) -> list[str]:
    out = [f"SWEEP over {result['real']} real and {result['wrong']} wrong suggestions with a Decisions API p",
           f"  gate: 'no' on >= {GATE_WRONG_NO:.0%} of the wrong, yes/unsure on >= {GATE_REAL_KEPT:.0%} of the real",
           "  no cut-off   wrong said no   real kept   gate"]
    for row in result["no"]:
        out.append(f"  <= {row['no']:<8} {bench._pct(row['wrong_no']):>13}   {bench._pct(row['real_kept']):>9}   "
                   + ("PASS" if row["passes"] else "-"))
    out.append("  yes cut-off  real at/over   wrong at/over")
    for row in result["yes"]:
        out.append(f"  >= {row['yes']:<8} {bench._pct(row['real_yes']):>12}   {bench._pct(row['wrong_yes']):>13}")
    out.append(f"  best 'no' cut-off keeping {GATE_REAL_KEPT:.0%} of the real: {result['best_no']}")
    return out


def all_rows(docs: list[dict]) -> list[dict]:
    return [row for doc in docs for run in doc.get("runs") or [] for row in run.get("suggestions") or []
            if isinstance(row, dict)]


def summarize_replay(runs: list[dict]) -> dict:
    """The re-check over every replayed run: the verdicts on the real and the
    wrong suggestions, the gate's two shares, the seconds (each Decisions
    API request, each lite call, each step), calls per model, 429s and
    503s."""
    rows = [row for run in runs for row in run.get("suggestions") or []]
    counts = {f"{group}_{v}": 0 for group in ("real", "wrong") for v in bench.VERDICTS}
    for row in rows:
        group = "real" if row.get("truth") == "real" else "wrong"
        counts[f"{group}_{(row.get('check') or {}).get('verdict') or 'none'}"] += 1
    real = sum(counts[f"real_{v}"] for v in bench.VERDICTS)
    wrong = sum(counts[f"wrong_{v}"] for v in bench.VERDICTS)
    accounting = bench.merge_accounting([r["accounting"] for r in runs if r.get("accounting")])
    seconds = {kind: [s for r in runs for s in r.get(f"{kind}_seconds") or []] for kind in ("decision", "lite")}
    steps = [r["check_run"]["seconds"] for r in runs
             if isinstance(r.get("check_run"), dict) and isinstance(r["check_run"].get("seconds"), (int, float))]
    out = {
        "runs": len(runs),
        "errors": [{"case": r.get("case"), "rep": r.get("rep"), "error": r["error"]} for r in runs if r.get("error")],
        "suggestions": len(rows), "real": real, "wrong": wrong, "verdicts": counts,
        "real_kept": bench._ratio(counts["real_yes"] + counts["real_unsure"], real),
        "wrong_no": bench._ratio(counts["wrong_no"], wrong),
        "checked": bench._ratio(sum(1 for r in rows if (r.get("check") or {}).get("verdict")), len(rows)),
        "statuses": {s: sum(1 for r in runs if (r.get("check_run") or {}).get("status") == s) for s in ("ok", "skipped", "failed")},
        "by": {b: sum(1 for r in runs if (r.get("check_run") or {}).get("by") == b) for b in ("decision", "lite")},
        "accounting": accounting,
        "by_model": bench.model_calls(accounting),
        "n_429": sum(s.get("n_429", 0) for s in accounting.values()),
        "n_503": sum(s.get("n_503", 0) for s in accounting.values()),
    }
    for kind, values in (*seconds.items(), ("step", steps)):
        out[f"{kind}_s"] = ({"median": round(statistics.median(values), 2), "max": max(values), "n": len(values)}
                            if values else None)
    out["gate"] = {
        "wrong_no": out["wrong_no"] is not None and out["wrong_no"] >= GATE_WRONG_NO,
        "real_kept": out["real_kept"] is not None and out["real_kept"] >= GATE_REAL_KEPT,
        "median_s": None if not out["decision_s"] else out["decision_s"]["median"] <= GATE_MEDIAN_S,
    }
    return out


def summary_lines(s: dict) -> list[str]:
    v = s["verdicts"]
    out = [f"REPLAY: {s['runs']} run(s), {s['suggestions']} suggestions: {s['real']} real, {s['wrong']} wrong; "
           f"answered {bench._pct(s['checked'])}",
           f"  steps: ok {s['statuses']['ok']}, skipped {s['statuses']['skipped']}, failed {s['statuses']['failed']}; "
           f"by decision {s['by']['decision']}, by lite {s['by']['lite']}",
           "  real:  " + " ".join(f"{k} {v[f'real_{k}']}" for k in bench.VERDICTS),
           "  wrong: " + " ".join(f"{k} {v[f'wrong_{k}']}" for k in bench.VERDICTS),
           f"  yes/unsure on the real {bench._pct(s['real_kept'])} (gate {GATE_REAL_KEPT:.0%}: "
           f"{'PASS' if s['gate']['real_kept'] else 'FAIL'}); 'no' on the wrong {bench._pct(s['wrong_no'])} "
           f"(gate {GATE_WRONG_NO:.0%}: {'PASS' if s['gate']['wrong_no'] else 'FAIL'})"]
    for kind in ("decision", "lite", "step"):
        st = s.get(f"{kind}_s")
        if st:
            line = f"  {kind} seconds: median {st['median']}, max {st['max']} over {st['n']}"
            if kind == "decision":
                line += f" (gate median <= {GATE_MEDIAN_S:g}: {'PASS' if s['gate']['median_s'] else 'FAIL'})"
            out.append(line)
    out.append(f"  429 x{s['n_429']}, 503 x{s['n_503']}")
    out += ["  " + line for line in bench.model_lines(s["by_model"]) if line]
    for e in s["errors"]:
        out.append(f"  ERROR run {e['rep']}: {e['error']}")
    return out


# ---------------------------------------------------------------- live


class ReplayError(Exception):
    pass


def _to_draft(row: dict, id_of: dict[str, str]) -> dict:
    """A stored suggestion row as the second look stores one: its photos
    back to "@<photo id>", the earlier verdict and the truth dropped."""
    item = {k: v for k, v in row.items() if k not in ("check", "truth")}
    if isinstance(item.get("photo_reference"), str):
        item["photo_reference"] = id_of.get(item["photo_reference"], item["photo_reference"])
    item["seen_in"] = [id_of.get(r, r) for r in item.get("seen_in") or []]
    item["evidence"] = [dict(e, photo=id_of.get(e.get("photo"), e.get("photo")))
                        for e in item.get("evidence") or [] if isinstance(e, dict)]
    item["found_by"] = list(item.get("found_by") or [])
    return item


def replay_run(run: dict, case: dict, cases_dir: Path) -> dict:
    """One stored run's suggestions re-checked on a fresh draft of its case."""
    A = bench._app()
    import app.ai.check as check

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
                p.kind, p.area, p.quality, p.part, p.note = s.get("kind"), s.get("area"), s.get("quality"), s.get("part"), s.get("note")
                p.shows_damage = bool(s.get("damage"))
                if "box" in s and hasattr(p, "damage_box"):
                    p.damage_box = s.get("box")
            id_of = {f"P{i + 1:02d}": f"@{p.id}" for i, p in enumerate(photos)}
            report = db.get(A.Report, rid)
            items = [_to_draft(row, id_of) for row in run.get("suggestions") or []]
            report.data = {**(report.data or {}), "second_look": {"status": "skipped", "items": items}}
            db.commit()
        finally:
            db.close()
        bench.ACCOUNTING.clear()
        started = time.monotonic()
        check.run_for_intake(rid, A.AiActor.cli())
        out["seconds"] = round(time.monotonic() - started, 1)
        db = A.SessionLocal()
        try:
            look = (db.get(A.Report, rid).data or {}).get("second_look") or {}
        finally:
            db.close()
        answered = {it.get("key") or it.get("part"): it.get("check") for it in look.get("items") or []}
        out["check_run"] = look.get("check")
        out["suggestions"] = [
            {"key": row.get("key"), "part": row.get("part"), "source": row.get("source"),
             "confidence": row.get("confidence"), "found_by": row.get("found_by"), "truth": row.get("truth"),
             "check": answered.get(row.get("key") or row.get("part"))}
            for row in run.get("suggestions") or []]
        attempts = list(bench.ACCOUNTING.attempts)
        out["decision_seconds"] = [a["seconds"] for a in attempts if a["purpose"] == "decision" and a["outcome"] == "ok"]
        out["lite_seconds"] = [a["seconds"] for a in attempts if a["purpose"] == "check" and a["outcome"] == "ok"]
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
    c = r.get("check_run") or {}
    said = {}
    for row in r.get("suggestions") or []:
        group = "real" if row.get("truth") == "real" else "wrong"
        verdict = (row.get("check") or {}).get("verdict") or "none"
        said[f"{group} {verdict}"] = said.get(f"{group} {verdict}", 0) + 1
    return (f"   re-check {c.get('status')} by {c.get('by') or '-'} on {c.get('model') or '-'} in {r.get('seconds')}s, "
            f"{c.get('calls', 0)} call(s)" + (f" ({c['note']})" if c.get("note") else "")
            + f"; {', '.join(f'{k} {n}' for k, n in sorted(said.items()))}")


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.sweep:
        docs = [json.loads(Path(f).read_text(encoding="utf-8")) for f in args.sweep]
        for line in sweep_lines(sweep(all_rows(docs))):
            print(line)
        return 0
    missing = [f"--{n}" for n in ("cases", "key", "out", "check") if getattr(args, n) is None]
    if args.result is None:
        missing.insert(0, "RESULT")
    if missing:
        sys.exit(f"needs {', '.join(missing)} (or --sweep FILE)")
    if args.out.resolve() == args.result.resolve():
        sys.exit("--out must be another file: the stored result is kept as it is")
    if args.scratch:
        bench._use_scratch(args.scratch)
    if os.environ.get("BENCHMARK_SCRATCH") != "1":
        sys.exit(bench.REFUSAL)
    doc = json.loads(args.result.read_text(encoding="utf-8"))
    key = {c["name"]: c for c in json.loads(args.key.read_text(encoding="utf-8"))["cases"]}
    stored = [r for r in doc.get("runs") or [] if not r.get("error") and r.get("suggestions")
              and (not args.only or r.get("case") in args.only) and (args.rep is None or int(r.get("rep") or 1) == args.rep)]
    unknown = sorted({str(r.get("case")) for r in stored} - set(key))
    if unknown:
        sys.exit(f"{len(unknown)} case(s) of the result are not in the key")
    if not stored:
        sys.exit("no run of the result has suggestions to re-check (a run from before contract 9 keeps none)")

    A = bench._app()
    from app import config

    if args.check is None or config.normal_check_mode(args.check) == "off":
        sys.exit("--check: decision, lite or decision_then_lite")
    for field, value in bench.check_settings(args, config, False).items():
        setattr(A.settings, field, value)
    A.init_db()
    out_doc = {
        "label": args.label or args.out.stem, "git_ref": args.ref, "source": str(args.result),
        "source_label": doc.get("label"), "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "ai_settings": bench.ai_settings(A.settings), "started": bench._now(), "finished": None, "runs": [], "summary": None,
    }
    for rep in range(1, args.repeat + 1):
        for run in stored:
            case = key[run["case"]]
            # The truth again from the run's own list, on this key.
            rows = run["suggestions"]
            bench.mark_truth(rows, case["parts"], bench.score_parts(case["parts"], run.get("drafted_parts") or []))
            print(f"== {run['case']} (stored run {run.get('rep', 1)})" + (f"  (replay {rep}/{args.repeat})" if args.repeat > 1 else ""),
                  flush=True)
            r = replay_run(run, case, args.cases)
            r["rep"] = rep
            out_doc["runs"].append(r)
            print(run_line(r), flush=True)
            out_doc["summary"] = summarize_replay(out_doc["runs"])
            bench._write_json(args.out, out_doc)
    print()
    for line in summary_lines(out_doc["summary"]):
        print(line)
    rows = all_rows([out_doc])
    if any(_p(r) is not None for r in rows):
        print()
        out_doc["sweep"] = sweep(rows)
        for line in sweep_lines(out_doc["sweep"]):
            print(line)
    out_doc["finished"] = bench._now()
    bench._write_json(args.out, out_doc)
    print("written", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
