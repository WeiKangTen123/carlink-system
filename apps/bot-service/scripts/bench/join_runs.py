"""OFFLINE: joins the result files of ONE arm, run a round at a time, into
one result file -- for the live A/B sessions where the arms take turns
each round (plans/2026-11-22-enhance-staging/plan.md 3a: base, arm 1,
arm 2, base, arm 1, ...), since compare.py and gate.py take one file a
side.

    python scripts/bench/join_runs.py OUT.json ROUND1.json ROUND2.json [...] [--label L] [--force]

The runs of each file in the order given, their repetitions numbered on
(the first file's 1..n, the next file's n+1.., each run keeping
source_file / source_rep), and the summary made again
(benchmark_intake.summarize). Works on benchmark_intake.py results and on
replay_link_check.py outputs alike. Refuses files of different arms --
another kind, git ref, model chains or ai_settings -- unless --force, and
never writes over one of its inputs. No Gemini call, no app import.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import benchmark_intake as bench  # noqa: E402

# What makes two files the same arm.
SAME = ("kind", "git_ref", "models", "ai_settings")


def join(docs: list[tuple[str, dict]], label: str | None = None, force: bool = False) -> dict:
    """The joined result of (file name, result) pairs, in order. Raises
    ValueError when they are not one arm (SAME) and force is off."""
    if not docs:
        raise ValueError("no file to join")
    first = docs[0][1]
    if not force:
        for name, doc in docs[1:]:
            differ = [k for k in SAME if doc.get(k) != first.get(k)]
            if differ:
                raise ValueError(f"{name} is another arm than {docs[0][0]} ({', '.join(differ)} differ); "
                                 "--force joins them anyway")
    runs, sources, offset = [], [], 0
    for name, doc in docs:
        stored = [r for r in doc.get("runs") or [] if isinstance(r, dict)]
        reps = sorted({int(r.get("rep") or 1) for r in stored})
        number = {rep: offset + i + 1 for i, rep in enumerate(reps)}
        for r in stored:
            rep = int(r.get("rep") or 1)
            runs.append({**r, "rep": number[rep], "source_file": name, "source_rep": r.get("source_rep", rep)})
        sources.append({"file": name, "label": doc.get("label"), "started": doc.get("started"),
                        "finished": doc.get("finished"), "reps": [number[rep] for rep in reps]})
        offset += len(reps)
    out = {k: first.get(k) for k in ("kind", "git_ref", "args", "models", "ai_settings", "sort_cache") if k in first}
    out.update(label=label or f"{first.get('label')}+{len(docs) - 1}", joined_from=sources,
               started=first.get("started"), finished=docs[-1][1].get("finished"), runs=runs,
               summary=bench.summarize(runs))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="join_runs.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path, help="the joined result file")
    ap.add_argument("files", type=Path, nargs="+", help="the arm's result files, a round each, in order")
    ap.add_argument("--label", help="the joined file's label (default: the first file's, +N)")
    ap.add_argument("--force", action="store_true", help="join files of different arms all the same")
    args = ap.parse_args(argv)
    if any(args.out.resolve() == f.resolve() for f in args.files):
        sys.exit("join_runs: OUT must be a new file, not one of the inputs")
    docs = [(f.name, json.loads(f.read_text(encoding="utf-8"))) for f in args.files]
    try:
        joined = join(docs, args.label, args.force)
    except ValueError as exc:
        sys.exit(f"join_runs: {exc}")
    bench._write_json(args.out, joined)
    s = joined["summary"]
    print(f"joined {len(docs)} file(s): {len(joined['runs'])} case-run(s), {s['reps']} repetition(s), "
          f"{len(s['complete_reps'])} complete -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
