"""Whether a reading change may be turned on: the owner's gate (contract 8)
on benchmark result files (scripts/benchmark_intake.py --out).

    python scripts/bench/gate.py BASE.json CAND.json [CAND2.json ...] [--key KEY]

A candidate passes when, against the baseline, over its complete runs
(the summary's TOTAL: each run the sum over the cases, then the mean over
the runs):

  - parts found, mean, is above the baseline's mean;
  - wrong sides, mean, is at most the baseline's or 2.5, whichever is higher;
  - extras, mean, is at most the baseline's + 2.

The measure is the strict parts score (score_parts: found, wrong_side,
extra), the one the lead's numbers were taken with: rescoring the stored
files on key v2 gives light 14 [13-15] / wrong side 2.5 / extras 2 and
strong 25 / 0 / 5 strict, where the fair score says 25.5 / 0 / 4 for the
strong one. Beside the verdict: Gemini calls, seconds and prompt tokens per
case -- what a setting costs on the free tier's 12 calls a minute -- and
compare.py's noise verdict on each measure: a PASS whose found is "inside
noise" has not shown that the gain is real.

A candidate fails when a measure is missing on either side (no complete
run, or a file from before the measure existed): a gate can't be passed on
a number that isn't there. It fails too when the two were scored on keys
of a different size, which makes their numbers incomparable: --key KEY
scores every run's strict parts again from its drafted parts first, as
--rescore does, so files scored on an older key compare on one key.

Prints one table, then the verdict and its three checks per candidate.
Exit status 0 when every candidate passes, 1 when any fails. Offline:
reads JSON files, imports no app code; prints no case names (plates).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import benchmark_intake as bench  # noqa: E402
import compare  # noqa: E402

# The owner's rule: wrong sides may stay at the baseline's when that is
# above 2.5 (the light model's today), and two extras more are a price
# worth paying for more parts found.
WRONG_SIDE_FLOOR = 2.5
EXTRA_ALLOWANCE = 2
# Per case: (summary metric, column, decimals). total_s is the case's wall
# clock (sorting, reading, second look); tok_in every kind of call's prompt
# tokens. Two decimals on calls: 1.25 calls a case is a fifth call per round.
COSTS = (("calls", "calls/case", 2), ("total_s", "s/case", 1), ("tok_in", "prompt tok/case", 0))


def load(path: Path, key: dict | None = None) -> dict:
    """A result file summarised as compare.load does; with key ({case name:
    key case}), each run's strict parts scored again on it first."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    runs = data.get("runs")
    if runs is None:
        runs = data.get("results") or []  # before --repeat
    if key is not None:
        for run in runs:
            case = key.get(run.get("case"))
            if case is None or "parts" not in run:
                continue  # not in the key, or an errored run
            items = run.get("drafted_items") or []
            drafted = [p for p in run.get("drafted_parts") or [i.get("part") for i in items] if p]
            run["parts"] = bench.score_parts(case["parts"], drafted)
    return {
        "label": data.get("label") or Path(path).stem,
        "git_ref": data.get("git_ref"),
        "ai_settings": {k: v for k, v in (data.get("ai_settings") or {}).items() if v},
        "summary": bench.summarize(runs),
    }


def checks(base: dict, cand: dict) -> list[dict]:
    """The rule's three checks of a candidate's TOTAL against the
    baseline's (both {metric: stats}), in order: {metric, label, value,
    limit, ok, says, noise}. A measure missing on either side fails."""
    rule = (
        ("found", "parts found", lambda b: b, lambda v, limit: v > limit, ">", lambda b: f"baseline {b:g}"),
        ("wrong_side", "wrong sides", lambda b: max(b, WRONG_SIDE_FLOOR), lambda v, limit: v <= limit, "<=",
         lambda b: f"the higher of baseline {b:g} and {WRONG_SIDE_FLOOR:g}"),
        ("extra", "extras", lambda b: b + EXTRA_ALLOWANCE, lambda v, limit: v <= limit, "<=",
         lambda b: f"baseline {b:g} + {EXTRA_ALLOWANCE}"),
    )
    out = []
    for metric, label, limit_of, passes, sign, why in rule:
        b, c = base.get(metric), cand.get(metric)
        if b is None or c is None:
            side = "baseline" if b is None else "candidate"
            out.append({"metric": metric, "label": label, "value": None, "limit": None, "ok": False,
                        "says": f"{label}: missing in the {side} (no complete run, or a file from before it)",
                        "noise": ""})
            continue
        limit = round(limit_of(b["mean"]), 3)
        ok = passes(c["mean"], limit)
        says = f"{label} {c['mean']:g} {sign if ok else 'not ' + sign} {limit:g} ({why(b['mean'])})"
        out.append({"metric": metric, "label": label, "value": c["mean"], "limit": limit, "ok": ok, "says": says,
                    "noise": compare.verdict(metric, b, c)})
    return out


def per_case(summary: dict, metric: str) -> float | None:
    """A TOTAL metric's mean over the cases: TOTAL sums them in each run."""
    st = summary["total"].get(metric)
    if not st or not summary["cases_n"]:
        return None
    return st["mean"] / summary["cases_n"]


def judge(base: dict, cand: dict) -> dict:
    """{pass, checks, problems}: the rule's checks, and what makes the two
    files incomparable (keys of a different size)."""
    problems = []
    eb, ec = base["summary"]["total"].get("expected"), cand["summary"]["total"].get("expected")
    if eb and ec and eb["mean"] != ec["mean"]:
        problems.append(f"scored on another key ({ec['mean']:g} key parts, the baseline {eb['mean']:g}): give --key")
    found = checks(base["summary"]["total"], cand["summary"]["total"])
    return {"pass": all(c["ok"] for c in found) and not problems, "checks": found, "problems": problems}


def _runs(doc: dict) -> str:
    s = doc["summary"]
    return f"{len(s['complete_reps'])}/{s['reps']}"


def _cost(value: float | None, decimals: int) -> str:
    return "-" if value is None else f"{value:.{decimals}f}"


def table(base: dict, cands: list[dict], verdicts: list[dict]) -> list[str]:
    cells = [["", "file", "runs", "found", "wrong side", "extras"] + [head for _, head, _ in COSTS] + ["verdict"]]
    for name, doc, verdict in [("base", base, None)] + [(f"cand {i}", d, v) for i, (d, v) in enumerate(zip(cands, verdicts), 1)]:
        total = doc["summary"]["total"]
        cells.append([name, doc["label"], _runs(doc)] + [bench._fmt(total.get(m)) for m in ("found", "wrong_side", "extra")]
                     + [_cost(per_case(doc["summary"], m), decimals) for m, _, decimals in COSTS]
                     + ["(baseline)" if verdict is None else "PASS" if verdict["pass"] else "FAIL"])
    widths = [max(len(row[i]) for row in cells) for i in range(len(cells[0]))]
    return ["  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in cells]


def report(base: dict, cands: list[dict]) -> tuple[list[str], list[dict]]:
    verdicts = [judge(base, cand) for cand in cands]
    out = [
        "gate (contract 8): the strict parts score (score_parts: found, wrong_side, extra), TOTAL over the complete "
        "runs, mean [min-max]",
        f"rule: found > baseline; wrong sides <= max(baseline, {WRONG_SIDE_FLOOR:g}); extras <= baseline + "
        f"{EXTRA_ALLOWANCE}; runs = complete/all; costs per case",
        "",
    ]
    out += table(base, cands, verdicts)
    for i, (cand, verdict) in enumerate(zip(cands, verdicts), 1):
        ref = f" ({cand['git_ref']})" if cand["git_ref"] else ""
        tried = "; ".join(f"{k} {v}" for k, v in cand["ai_settings"].items())
        out.append("")
        out.append(f"cand {i} {cand['label']}{ref}: {'PASS' if verdict['pass'] else 'FAIL'}" + (f" -- {tried}" if tried else ""))
        for c in verdict["checks"]:
            out.append(f"   {'ok  ' if c['ok'] else 'FAIL'} {c['says']}" + (f" -- {c['noise']}" if c["noise"] else ""))
        for problem in verdict["problems"]:
            out.append(f"   FAIL {problem}")
        if verdict["pass"] and verdict["checks"][0]["noise"] in ("inside noise", "1 run: noise unknown"):
            out.append(f"   note: parts found is {verdict['checks'][0]['noise']} -- more runs before turning it on")
    return out, verdicts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The owner's gate: does each candidate result file pass against the baseline?")
    ap.add_argument("base", type=Path, help="the baseline's result file (benchmark_intake.py --out)")
    ap.add_argument("candidates", type=Path, nargs="+", help="one or more candidates' result files")
    ap.add_argument("--key", type=Path, help="answer key: score every run's strict parts again on it first")
    args = ap.parse_args(argv)
    key = None
    if args.key is not None:
        key = {c["name"]: c for c in json.loads(args.key.read_text(encoding="utf-8"))["cases"]}
    base = load(args.base, key)
    cands = [load(path, key) for path in args.candidates]
    lines, verdicts = report(base, cands)
    for line in lines:
        print(line)
    return 0 if all(v["pass"] for v in verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
