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

Contract 10, the linked parts' gate (plans/2026-11-17-linked-parts/plan.md
8.1, G1-G6), on a replay (scripts/bench/replay_links.py --out, or Wave 2's
linked-check replay scripts/bench/replay_link_check.py --out) or a live
result run with --links (each file gated on its own: the B group's, the
lite1 group's):

    python scripts/bench/gate.py --suggestions FILE [FILE ...] [--baseline BASE.json] [--key KEY]
        [--variant NAME] [--names] [--limit G1=5 ...]

  G1  real parts added by links per 4-case run, mean           >= 5
  G2  wrong (extra + wrong side) per real, every case-run        <= 1.5;
      in any one case                                           <= 2.5
      (a case with no real link: wrong per run                  <= 4)
  G3  wrong sides from links per 4-case run, mean               <= 0.5
  G4  links shown per case-run                                  <= 12; median <= 10
  G5  the damage list unchanged: a replay's own assertion; a live result
      passes the list gate above against --baseline (the same-sort run
      without links; --key scores both lists again first)
  G6  the strip-down flood: wrong ones in the strip-down bucket per
      case-run                                                  <= 6

The owner sets the numbers (--limit NAME=VALUE; the names in
SUGGESTION_LIMITS). The linked parts are taken as the file scored them (a
live result on an older key: benchmark_intake.py --rescore it first). A
replay's variants other than "default" are pre-registered sensitivity runs:
--variant gates one, labelled so. Prints a per-case table ("case 1".. in
the file's order; --names prints the case names, which are plates) and
each check; exit status 0 when every file passes, 1 otherwise.
"""
import argparse
import json
import statistics
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


# ---------------------------------------------------------------- contract 10: the suggestions gate

# plan.md 8.1's defaults; the owner sets the numbers (--limit NAME=VALUE).
SUGGESTION_LIMITS = {
    "G1": 5.0,               # real parts added by links per 4-case run, mean: at least
    "G2": 1.5,               # wrong (extra + wrong side) per real, every case-run together: at most
    "G2_case": 2.5,          # the same in any one case: at most
    "G2_case_no_real": 4.0,  # a case with no real link: wrong per run, at most
    "G3": 0.5,               # wrong sides from links per 4-case run, mean: at most
    "G4_max": 12.0,          # links shown in any case-run: at most
    "G4_median": 10.0,       # links shown per case-run, median: at most
    "G6": 6.0,               # wrong ones in the strip-down bucket in any case-run: at most
}


def _limit(text: str) -> tuple[str, float]:
    name, sep, value = text.partition("=")
    name = name.strip()
    if not sep or name not in SUGGESTION_LIMITS:
        raise argparse.ArgumentTypeError(f"NAME=VALUE, NAME one of {', '.join(SUGGESTION_LIMITS)}")
    try:
        return name, float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{name}: a number") from None


def linked_runs(doc: dict, variant: str | None = None) -> tuple[list[dict], list[dict]]:
    """(every case-run of the file, or of a replay's variant; those that
    scored with linked parts: a "links" block). A replay's top-level runs
    are its frozen defaults ("default")."""
    if doc.get("kind") == "replay_links" and variant not in (None, "default"):
        runs = ((doc.get("variants") or {}).get(variant) or {}).get("runs")
        if runs is None:
            raise SystemExit(f"--variant {variant}: not in this replay (run replay_links.py with --variant {variant})")
    elif variant not in (None, "default"):
        raise SystemExit("--variant: only a replay (replay_links.py --out) has variants")
    else:
        runs = doc.get("runs") or []
    runs = [r for r in runs if isinstance(r, dict)]
    scored = [r for r in runs if bench.run_ok(r) and isinstance(r.get("links"), dict) and "shown" in r["links"]]
    return runs, scored


def _rep(run: dict) -> int:
    return int(run.get("rep") or 1)


def complete_runs(runs: list[dict], scored: list[dict]) -> dict[int, list[dict]]:
    """The 4-case runs (a repetition) in which every case of the file
    scored with linked parts: {rep: its case-runs}."""
    names = {r.get("case") for r in runs}
    out = {}
    for rep in sorted({_rep(r) for r in runs}):
        mine = [r for r in scored if _rep(r) == rep]
        if {r.get("case") for r in mine} == names and len(mine) == len([r for r in runs if _rep(r) == rep]):
            out[rep] = mine
    return out


def _mean(values: list) -> float | None:
    return round(statistics.fmean(values), 3) if values else None


def case_rows(scored: list[dict], names: bool) -> list[dict]:
    """Per case, in the file's order: its case-runs' linked parts summed (and
    the recall averaged)."""
    order = list(dict.fromkeys(r.get("case") for r in scored))
    rows = []
    for n, case in enumerate(order, 1):
        mine = [r["links"] for r in scored if r.get("case") == case]
        rows.append({
            "case": str(case) if names else f"case {n}", "runs": len(mine), "shown": [b["shown"] for b in mine],
            "real": sum(b["real"] for b in mine), "wrong": sum(b["wrong"] for b in mine),
            "wrong_side": sum(b["wrong_side"] for b in mine), "extra": sum(b["extra"] for b in mine),
            "strip_wrong_max": max(b["strip_down_wrong"] for b in mine),
            "lz": _mean([b["lz_found"] for b in mine]), "lzl": _mean([b["lzl_found"] for b in mine]),
            "expected": mine[0]["expected"], "lz_inside": _mean([b["lz_inside_found"] for b in mine]),
            "lzl_inside": _mean([b["lzl_inside_found"] for b in mine]), "inside": mine[0]["inside_expected"],
        })
    return rows


def suggestion_checks(runs: list[dict], scored: list[dict], g5: tuple[bool, str], limits: dict,
                      cases: list[dict]) -> list[dict]:
    """G1-G6 as {gate, ok, says}, in order. A number that can't be had (no
    complete 4-case run, no case-run with links) fails its gate."""
    lim = limits
    full = complete_runs(runs, scored)
    out = []

    def add(gate: str, ok: bool, says: str) -> None:
        out.append({"gate": gate, "ok": bool(ok), "says": says})

    g1 = _mean([sum(r["links"]["real"] for r in mine) for mine in full.values()])
    add("G1", g1 is not None and g1 >= lim["G1"],
        f"real parts added by links per 4-case run {_num(g1)} (>= {lim['G1']:g}; {len(full)} complete 4-case run(s))")
    real, wrong = sum(r["links"]["real"] for r in scored), sum(r["links"]["wrong"] for r in scored)
    overall = round(wrong / real, 3) if real else None
    over = []
    for c in cases:
        if c["real"] and c["wrong"] / c["real"] > lim["G2_case"]:
            over.append(f"{c['case']} {c['wrong'] / c['real']:.3g} per real")
        elif not c["real"] and c["wrong"] / c["runs"] > lim["G2_case_no_real"]:
            over.append(f"{c['case']} {c['wrong'] / c['runs']:.3g} wrong a run with no real")
    within = overall <= lim["G2"] if overall is not None else wrong == 0
    add("G2", bool(scored) and within and not over,
        f"wrong per real {_num(overall)} ({wrong} wrong, {real} real; <= {lim['G2']:g}); any one case <= "
        f"{lim['G2_case']:g} (no real: <= {lim['G2_case_no_real']:g} wrong a run)"
        + (f" -- over: {'; '.join(over)}" if over else ""))
    g3 = _mean([sum(r["links"]["wrong_side"] for r in mine) for mine in full.values()])
    add("G3", g3 is not None and g3 <= lim["G3"],
        f"wrong sides from links per 4-case run {_num(g3)} (<= {lim['G3']:g})")
    shown = [r["links"]["shown"] for r in scored]
    most, median = (max(shown), statistics.median(shown)) if shown else (None, None)
    add("G4", bool(shown) and most <= lim["G4_max"] and median <= lim["G4_median"],
        f"links shown per case-run: max {_num(most)} (<= {lim['G4_max']:g}), median {_num(median)} "
        f"(<= {lim['G4_median']:g})")
    add("G5", g5[0], g5[1])
    flood = max((r["links"]["strip_down_wrong"] for r in scored), default=None)
    add("G6", flood is not None and flood <= lim["G6"],
        f"wrong in the strip-down bucket per case-run: max {_num(flood)} (<= {lim['G6']:g})")
    return out


def _g5(doc: dict, scored: list[dict], baseline: dict | None, path: Path, key: dict | None) -> tuple[bool, str]:
    """G5: a replay asserted the list unchanged on every case-run (the
    linked parts' replay_links, or Wave 2's linked-check replay
    replay_link_check); a live result passes the contract 8 list gate
    (judge) against --baseline."""
    if doc.get("kind") in ("replay_links", "replay_link_check"):
        same = bool(scored) and bool(doc.get("damage_list_identical")) and all(
            r["links"].get("damage_list_identical") for r in scored)
        return same, f"the damage list {'identical' if same else 'CHANGED (or nothing replayed)'} in every replayed case-run"
    if baseline is None:
        return False, "the damage list: a live result needs --baseline (the same-sort run without links)"
    verdict = judge(baseline, load(path, key))
    return verdict["pass"], ("the list gate against the baseline " + ("PASS" if verdict["pass"] else "FAIL") + ": "
                             + "; ".join(c["says"] for c in verdict["checks"]) + "".join(f"; {p}" for p in verdict["problems"]))


def _num(value) -> str:
    return "-" if value is None else f"{value:g}"


def case_table(cases: list[dict], full: dict) -> list[str]:
    """Per case: mean per case-run (links shown with its range), wrong per
    real over its runs, the strip-down flood's worst run, and the recall
    as if all were ticked; then per 4-case run (the complete ones)."""
    cells = [["case", "runs", "links shown", "real", "wrong side", "extra", "wrong/real", "strip-down wrong max",
              "list+sugg -> +links", "inside -> +links"]]
    for c in cases:
        shown, runs = c["shown"], c["runs"]
        cells.append([c["case"], str(runs), f"{_mean(shown):g} [{min(shown)}-{max(shown)}]",
                      _num(round(c["real"] / runs, 3)), _num(round(c["wrong_side"] / runs, 3)),
                      _num(round(c["extra"] / runs, 3)),
                      _num(round(c["wrong"] / c["real"], 3)) if c["real"] else "- (no real)", str(c["strip_wrong_max"]),
                      f"{_num(c['lz'])} -> {_num(c['lzl'])} of {c['expected']}",
                      f"{_num(c['lz_inside'])} -> {_num(c['lzl_inside'])} of {c['inside']}"])
    if full:
        per = [[r["links"] for r in mine] for mine in full.values()]

        def each(k: str) -> list:
            return [sum(b[k] for b in bs) for bs in per]

        real, wrong = sum(each("real")), sum(each("wrong"))
        cells.append([f"per 4-case run ({len(full)})", "", _num(_mean(each("shown"))), _num(_mean(each("real"))),
                      _num(_mean(each("wrong_side"))), _num(_mean(each("extra"))),
                      _num(round(wrong / real, 3)) if real else "-", "",
                      f"{_num(_mean(each('lz_found')))} -> {_num(_mean(each('lzl_found')))}",
                      f"{_num(_mean(each('lz_inside_found')))} -> {_num(_mean(each('lzl_inside_found')))}"])
    widths = [max(len(row[i]) for row in cells) for i in range(len(cells[0]))]
    return ["  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in cells]


def suggestions_report(path: Path, doc: dict, *, variant: str | None, limits: dict, names: bool,
                       baseline: dict | None, key: dict | None) -> tuple[list[str], bool]:
    """One file's G1-G6: the lines to print and whether it passes."""
    runs, scored = linked_runs(doc, variant)
    cases = case_rows(scored, names)
    found = suggestion_checks(runs, scored, _g5(doc, scored, baseline, path, key), limits, cases)
    passed = bool(scored) and all(c["ok"] for c in found)
    if doc.get("kind") == "replay_links":
        what = (f"replay, table {(doc.get('table') or {}).get('version')} "
                f"({'frozen' if doc.get('frozen') else 'NOT FROZEN'}), variant {variant or 'default'}"
                + ("" if variant in (None, "default") else " (pre-registered, sensitivity only)"))
    elif doc.get("kind") == "replay_link_check":
        s = doc.get("ai_settings") or {}
        what = (f"linked-check replay of {doc.get('source_label')}, table {s.get('link_table_version')}, "
                f"{s.get('intake_link_check_max_calls')} area(s), skill {s.get('intake_link_skill', 'L1')}"
                + ("" if doc.get("links_match") else "; the linked parts rebuilt differ from the stored run's"))
    else:
        s = doc.get("ai_settings") or {}
        what = (f"live result, links {s.get('intake_links', 'off')}, table {s.get('link_table_version')} "
                f"sha256 {str(s.get('link_table_sha256'))[:12]}")
    out = [f"gate --suggestions (contract 10, plan.md 8.1): {doc.get('label') or path.stem} -- {what}; "
           f"{len(scored)} of {len(runs)} case-run(s) with linked parts", ""]
    out += case_table(cases, complete_runs(runs, scored)) if cases else ["   no case-run with linked parts "
                                                                          "(a file run without --links?)"]
    out.append("")
    out += [f"   {'ok  ' if c['ok'] else 'FAIL'} {c['gate']}: {c['says']}" for c in found]
    out.append(f"{doc.get('label') or path.stem}: {'PASS' if passed else 'FAIL'}")
    return out, passed


def suggestions_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="gate.py --suggestions",
                                 description="Contract 10's gate on the linked parts (plan.md 8.1, G1-G6).")
    ap.add_argument("files", type=Path, nargs="+", help="replay_links.py outputs, or live results run with --links")
    ap.add_argument("--baseline", type=Path, help="a live result's G5: the same-sort run without links")
    ap.add_argument("--key", type=Path, help="answer key: G5's two lists scored again on it first")
    ap.add_argument("--variant", help="a replay's pre-registered variant instead of its frozen defaults")
    ap.add_argument("--names", action="store_true", help="print the case names (plates) instead of case 1..")
    ap.add_argument("--limit", type=_limit, action="append", default=[], metavar="NAME=VALUE",
                    help=f"the owner's number for a check ({', '.join(SUGGESTION_LIMITS)})")
    args = ap.parse_args(argv)
    limits = dict(SUGGESTION_LIMITS, **dict(args.limit))
    key = None
    if args.key is not None:
        key = {c["name"]: c for c in json.loads(args.key.read_text(encoding="utf-8"))["cases"]}
    baseline = load(args.baseline, key) if args.baseline else None
    print("limits: " + ", ".join(f"{k} {v:g}" for k, v in limits.items())
          + (" (set with --limit)" if limits != SUGGESTION_LIMITS else " (plan.md 8.1 defaults)"))
    passed = []
    for path in args.files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        lines, ok = suggestions_report(path, doc, variant=args.variant, limits=limits, names=args.names,
                                       baseline=baseline, key=key)
        print()
        print("\n".join(lines))
        passed.append(ok)
    return 0 if all(passed) else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--suggestions" in argv:
        return suggestions_main([a for a in argv if a != "--suggestions"])
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
