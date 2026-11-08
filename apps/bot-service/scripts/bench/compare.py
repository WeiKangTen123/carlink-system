"""Compares two benchmark result files (scripts/benchmark_intake.py --out).

    python scripts/bench/compare.py BEFORE.json AFTER.json [--all] [--total-only]

For every metric: before and after as mean [min-max] over the runs, the
change in the mean, and a verdict. Gemini answers vary from run to run, so a
change whose ranges overlap is "inside noise" -- not shown to be real; one
whose ranges don't overlap is UP or DOWN (better / worse where the metric
has a direction). With a single run on either side there is no range to
judge noise by: the verdict says so instead of calling the change real.

Both files are summarised again here with benchmark_intake.summarize, so a
file from before --repeat (the 2026-11-05 baseline: {"results", "totals"})
compares like a new one. Offline: reads two JSON files, imports no app code.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import benchmark_intake as bench  # noqa: E402

# Shown by default, in this order; --all adds every other metric present.
KEY_METRICS = (
    "found", "visible_found", "extra", "wrong_side", "side_undetermined", "facts_ok",
    "photos_read", "sort_s", "read_s", "calls", "n_429", "wait_429_s", "sort_cache_hits",
    "label_precision", "photographed_recall", "selection_coverage",
    "cond_exact_rate", "cond_first_token_rate", "cond_token_recall", "cond_firm_share",
    "annex_coverage", "annex_extra", "annex_unpriced", "annex_canonical", "annex_perfect_coverage",
)
HIGHER_IS_BETTER = {
    "found", "visible_found", "facts_ok", "visible_recall", "checklist_found", "checklist_visible_found",
    "label_precision", "photographed_recall", "selection_coverage", "cond_exact", "cond_first_token",
    "cond_exact_rate", "cond_first_token_rate", "cond_token_recall", "cond_firm_share",
    "annex_covered", "annex_coverage", "annex_priced",
}
LOWER_IS_BETTER = {
    "extra", "wrong_side", "side_undetermined", "checklist_extra", "sort_s", "read_s", "calls", "attempts",
    "n_429", "wait_429_s", "slot_wait_s", "annex_extra", "annex_unpriced", "annex_canonical",
}


def load(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    runs = data.get("runs")
    if runs is None:
        runs = data.get("results") or []  # before --repeat: one run per case
    return {
        "label": data.get("label") or Path(path).stem,
        "git_ref": data.get("git_ref"),
        "summary": bench.summarize(runs),
    }


def verdict(metric: str, a: dict | None, b: dict | None) -> str:
    if a is None and b is None:
        return ""
    if a is None:
        return "only after"
    if b is None:
        return "only before"
    if a["min"] == b["min"] and a["max"] == b["max"] and a["mean"] == b["mean"]:
        return "same"
    if a["n"] < 2 or b["n"] < 2:
        return "1 run: noise unknown"
    if a["max"] >= b["min"] and b["max"] >= a["min"]:
        return "inside noise"
    up = b["mean"] > a["mean"]
    word = "UP" if up else "DOWN"
    if metric in HIGHER_IS_BETTER:
        return f"{word} (better)" if up else f"{word} (worse)"
    if metric in LOWER_IS_BETTER:
        return f"{word} (worse)" if up else f"{word} (better)"
    return word


def compare_metrics(a: dict, b: dict, everything: bool = False) -> list[dict]:
    """Rows {metric, before, after, delta, verdict} for two metric tables
    ({metric: stats}) in KEY_METRICS order (then the rest with --all)."""
    names = [m for m in KEY_METRICS if m in a or m in b]
    if everything:
        names += [m for m in list(dict.fromkeys(list(a) + list(b))) if m not in names]
    rows = []
    for name in names:
        sa, sb = a.get(name), b.get(name)
        delta = round(sb["mean"] - sa["mean"], 3) if sa and sb else None
        rows.append({"metric": name, "before": sa, "after": sb, "delta": delta, "verdict": verdict(name, sa, sb)})
    return rows


def _delta(value) -> str:
    return "" if value is None else f"{value:+.4g}"


def format_rows(title: str, rows: list[dict]) -> list[str]:
    cells = [[title, "before", "after", "change", "verdict"]]
    cells += [["  " + r["metric"], bench._fmt(r["before"]), bench._fmt(r["after"]), _delta(r["delta"]), r["verdict"]]
              for r in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(cells[0]))]
    return ["  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in cells]


def _side(name: str, doc: dict) -> str:
    s = doc["summary"]
    ref = f" ({doc['git_ref']})" if doc["git_ref"] else ""
    return (f"{name}: {doc['label']}{ref} -- {s['reps']} run(s) of {s['cases_n']} case(s), "
            f"{len(s['complete_reps'])} complete, {len(s['errors'])} error(s)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Before/after table of two benchmark_intake.py result files.")
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("--all", action="store_true", help="every metric, not only the main ones")
    ap.add_argument("--total-only", action="store_true", help="the TOTAL table only, no per-case tables")
    args = ap.parse_args(argv)
    a, b = load(args.before), load(args.after)
    print(_side("before", a))
    print(_side("after ", b))
    print("ranges are [min-max] over runs; TOTAL sums the cases within each complete run")
    print()
    for line in format_rows("TOTAL", compare_metrics(a["summary"]["total"], b["summary"]["total"], args.all)):
        print(line)
    if not args.total_only:
        cases = list(dict.fromkeys(list(a["summary"]["cases"]) + list(b["summary"]["cases"])))
        for case in cases:
            ma = (a["summary"]["cases"].get(case) or {}).get("metrics") or {}
            mb = (b["summary"]["cases"].get(case) or {}).get("metrics") or {}
            print()
            for line in format_rows(str(case), compare_metrics(ma, mb, args.all)):
                print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
