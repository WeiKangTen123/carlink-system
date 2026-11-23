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

Contract 10: the linked parts' numbers (link_*, lz_* / lzl_* recall) when
either file has them -- a result run with --links, or a replay
(scripts/bench/replay_links.py --out, its frozen defaults);
--before-variant / --after-variant take a replay's pre-registered variant
instead (the same file on both sides: a variant against the defaults).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import benchmark_intake as bench  # noqa: E402

# Shown by default, in this order; --all adds every other metric present.
KEY_METRICS = (
    "found", "visible_found", "extra", "wrong_side", "side_undetermined",
    "fair_found", "fair_visible_found", "fair_extra", "neighbour", "other_named", "contradicted", "facts_ok",
    "photos_read", "sort_s", "read_s", "calls", "n_429", "wait_429_s", "sort_cache_hits",
    "label_precision", "photographed_recall", "selection_coverage",
    "cond_exact_rate", "cond_first_token_rate", "cond_token_recall", "cond_firm_share",
    "annex_coverage", "annex_extra", "annex_unpriced", "annex_canonical", "annex_perfect_coverage",
    "look_found", "look_added", "look_extra", "look_wrong_side", "look_calls", "look_s",
    "read_tok_in", "read_tok_out", "read_tok_think", "read_code_runs", "sort_tok_in", "sort_tok_think",
    "tok_in", "total_s",
    # The found parts in detail and the whole chain (contract 8).
    "side_right", "side_undetermined_found", "side_right_inferred", "side_wrong_inferred", "side_right_rate",
    "remarks_agree", "remarks_disagree", "remarks_unsure", "remarks_repair_agree", "remarks_agree_rate",
    *(f"zone_{zone}_found" for zone in bench.ZONES),
    "annex_priced", "annex_price_diff", "annex_price_diff_rate", "annex_adjusted_diff", "annex_matched_priced",
    "annex_within_10",
    "annex_within_10_rate", "annex_price_abs_diff", "annex_perfect_within_10",
    # Contract 9: the suggestions' evidence and the re-check.
    "sug_n", "sug_real", "sug_wrong", "sug_box_fill", "sug_any_box_fill", "sug_reason_fill", "list_box_fill", "list_reason_fill",
    "sug_checked_rate", "chk_real_kept_rate", "chk_wrong_no_rate", "dec_calls", "dec_s", "check_calls", "recheck_s",
    "n_503",
    # Contract 10: the linked parts, the recall they add, the linked check.
    "link_shown", "link_real", "link_wrong_side", "link_extra", "link_wrong", "link_wrong_per_real", "link_strip_wrong",
    "link_hidden_shown", "lz_found", "lzl_found", "lz_recall", "lzl_recall", "lz_inside_found", "lzl_inside_found",
    "lz_inside_recall", "lzl_inside_recall", "lzl_hidden_recall", "lchk_calls", "lchk_s",
    # Wave 2 (plans/2026-11-22-enhance-staging 3c): the sorter's set-aside
    # photos (E10), the wrong-end zones (E11), the geometry's sides (E7), the
    # linked check's verdicts by truth (E9; replay_link_check.py too).
    *(f"sort_{k}" for k in bench.SORTER_SET_ASIDE), "zone_other_end", "wrong_end_items", "wrong_end_sugg",
    "wrong_end_sugg_real", "geo_zones", "geo_zone_changed", "geo_side_changed", "zone_side_flips",
    "lcv_answered", "lcv_unanswered", *(f"lcv_{g}_{v}" for g in ("real", "wrong") for v in bench.LINK_VERDICTS),
)
HIGHER_IS_BETTER = {
    "found", "visible_found", "facts_ok", "visible_recall", "checklist_found", "checklist_visible_found",
    "label_precision", "photographed_recall", "selection_coverage", "cond_exact", "cond_first_token",
    "cond_exact_rate", "cond_first_token_rate", "cond_token_recall", "cond_firm_share",
    "annex_covered", "annex_coverage", "annex_priced",
    "look_found", "look_visible_found", "look_added", "look_ok", "fair_found", "fair_visible_found",
    "side_right", "side_right_rate", "remarks_agree", "remarks_agree_rate", "remarks_repair_agree",
    "annex_matched_priced", "annex_within_10", "annex_within_10_rate", "annex_perfect_within_10",
    *(f"zone_{zone}_found" for zone in bench.ZONES), *(f"zone_{zone}_recall" for zone in bench.ZONES),
    "sug_real", "sug_box_fill", "sug_any_box_fill", "sug_reason_fill", "list_box_fill", "list_reason_fill", "sug_checked_rate",
    "chk_real_kept_rate", "chk_wrong_no_rate",
    "link_real", "lz_found", "lzl_found", "lz_recall", "lzl_recall", "lz_inside_found", "lzl_inside_found",
    "lz_inside_recall", "lzl_inside_recall", "lzl_hidden_recall",
    "wrong_end_sugg_real", "lcv_answered", "lcv_real_seen", "lcv_real_likely", "lcv_wrong_no",
}
LOWER_IS_BETTER = {
    "extra", "wrong_side", "side_undetermined", "checklist_extra", "sort_s", "read_s", "calls", "attempts",
    "n_429", "wait_429_s", "slot_wait_s", "annex_extra", "annex_unpriced", "annex_canonical",
    "look_extra", "look_wrong_side", "look_calls", "look_s", "fair_extra", "contradicted",
    "side_wrong_inferred", "remarks_disagree", "annex_price_abs_diff", "tok_in", "total_s",
    *(f"zone_{zone}_extra" for zone in bench.ZONES),
    "sug_wrong", "dec_s", "recheck_s", "n_503",
    "link_wrong_side", "link_extra", "link_wrong", "link_wrong_per_real", "link_strip_wrong", "lchk_calls", "lchk_s",
    "sort_unusable", "sort_unsorted", "lcv_unanswered", "lcv_real_no", "lcv_wrong_seen", "lcv_wrong_likely",
}
# In neither: annex_price_diff, annex_price_diff_rate and annex_adjusted_diff
# are signed, and closer to 0 is better whichever way they move.


def load(path: Path, variant: str | None = None) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    runs = data.get("runs")
    if runs is None:
        runs = data.get("results") or []  # before --repeat: one run per case
    if variant not in (None, "default"):
        # Contract 10: a replay's pre-registered variant (replay_links.py).
        runs = ((data.get("variants") or {}).get(variant) or {}).get("runs")
        if runs is None:
            sys.exit(f"{path}: no variant {variant!r} (a replay_links.py output run with --variant {variant})")
    return {
        "label": (data.get("label") or Path(path).stem) + (f" [{variant}]" if variant not in (None, "default") else ""),
        "git_ref": data.get("git_ref"),
        "ai_settings": {k: v for k, v in (data.get("ai_settings") or {}).items() if v},
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
    # The model settings tried (none: the request as before they existed).
    tried = "; ".join(f"{k} {v}" for k, v in doc.get("ai_settings", {}).items())
    return (f"{name}: {doc['label']}{ref} -- {s['reps']} run(s) of {s['cases_n']} case(s), "
            f"{len(s['complete_reps'])} complete, {len(s['errors'])} error(s)" + (f" -- {tried}" if tried else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Before/after table of two benchmark_intake.py result files.")
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("--all", action="store_true", help="every metric, not only the main ones")
    ap.add_argument("--total-only", action="store_true", help="the TOTAL table only, no per-case tables")
    ap.add_argument("--before-variant", metavar="NAME", help="a replay_links.py output's variant on the before side")
    ap.add_argument("--after-variant", metavar="NAME", help="the same on the after side")
    args = ap.parse_args(argv)
    a, b = load(args.before, args.before_variant), load(args.after, args.after_variant)
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
