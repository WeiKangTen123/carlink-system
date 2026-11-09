"""Scores the intake AI against real surveyor reports.

Runs the real pipeline -- sorting, then reading -- on whole example cases
with real Gemini calls, and compares the drafted damage list and vehicle
facts with what the surveyor actually wrote (an answer key built from each
case's report, kept out of git because it holds real plates and chassis
numbers). Use it before and after changing photo selection, prompts or the
parts vocabulary, so changes are judged on numbers rather than one example.

It writes to whatever DATABASE_URL / STORAGE_DIR point at, so it refuses to
run unless BENCHMARK_SCRATCH=1 -- run it in a throwaway container or venv
with both pointing at scratch locations, never against the live database:

    BENCHMARK_SCRATCH=1 DATABASE_URL=sqlite:////bench/run.db STORAGE_DIR=/bench/storage \\
        python scripts/benchmark_intake.py --cases /bench/cases --key /bench/key.json \\
        --out /bench/out/baseline.json [--only "<case name>"] [--repeat 3] \\
        [--sort-cache /bench/sortcache] [--reports /bench/reports] [--label NAME --ref GIT_REF]

Gemini answers differ from run to run, so a single run cannot tell a real
gain of +3 parts from noise: --repeat N runs every case N times and reports
mean, min and max. --sort-cache freezes the sorting step (each photo's
result kept by its content hash), so a change to reading is measured without
sorting's variance. --reports scores the drafted conditions, and the Annex A
that prefill builds from the drafted parts, against each case's own surveyor
report, with the other reports as the firm's history (leave-one-out).

The reading is followed by the second look (app/ai/second_look.py), as in
the app: its suggestions are scored as if every one were ticked -- parts
found then, parts it added, its extras -- with its calls and models apart
from the reading's, so it ships only if it finds more beyond the noise for
few extras. --second-look-models measures another chain; --no-second-look
leaves it out.

On the server, scripts/bench/run_bench.sh runs this inside the live api
container and scripts/bench/compare.py compares two result files; see
scripts/bench/README.md.

Nothing from app is imported when this file loads: the offline tests
(scripts/test_benchmark_scoring.py) and compare.py import the scoring
functions. The app is imported by main() after the BENCHMARK_SCRATCH check,
because app.reports.db binds to DATABASE_URL the moment it is imported.
"""
import argparse
import copy
import hashlib
import json
import logging
import os
import re
import shutil
import statistics
import sys
import threading
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

BOT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BOT_DIR))

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
REPORT_EXTENSIONS = {".xls", ".xlsx"}
SIDE_UNDETERMINED = " (side undetermined)"
# app.parts.history.FITTING_CONDITIONS: a line written for a part renewed on
# refit or kept, not damaged. Repeated (not imported) so scoring loads
# without the app; test_benchmark_scoring checks that the two agree.
FITTING_CONDITIONS = frozenset({"necessary", "serviceable"})
# What sorting writes on each photo (pipeline._sort); the sort cache keeps
# exactly these.
SORT_FIELDS = ("kind", "area", "quality", "shows_damage", "part", "note")
REFUSAL = "Refusing to run: set BENCHMARK_SCRATCH=1 with DATABASE_URL and STORAGE_DIR pointing at scratch locations."

log = logging.getLogger("benchmark")


# ---------------------------------------------------------------- parts


def _family(part: str) -> str:
    for side in ("Left ", "Right "):
        if part.startswith(side):
            part = part[len(side):]
    return part.removesuffix(SIDE_UNDETERMINED)


def covers(name: str, key_part: str) -> bool:
    """Whether a drafted or sorter part name stands for this key part: the
    same name, or a side-undetermined name of its family ("Door (side
    undetermined)" for "Left Front Door") -- score_parts' rule."""
    if name == key_part:
        return True
    return name.endswith(SIDE_UNDETERMINED) and _family(key_part).endswith(_family(name))


def score_parts(expected: list[dict], drafted: list[str]) -> dict:
    """Matches drafted part names to the key: exact name first, then a
    side-undetermined name against a same-family part (counted as found),
    then a same-family part on the wrong side (reported, not counted).
    `matched` says which drafted name each found key part came from, for
    the condition scoring."""
    drafted = [p for p in drafted if p]
    remaining = [e["part"] for e in expected]
    found: dict[str, str] = {}
    matched: dict[str, str] = {}
    wrong_side: list[list[str]] = []
    extra: list[str] = []
    leftover = []
    for part in drafted:
        if part in remaining:
            remaining.remove(part)
            found[part] = "exact"
            matched[part] = part
        else:
            leftover.append(part)
    for part in leftover:
        family = _family(part)
        if part.endswith(SIDE_UNDETERMINED):
            match = next((e for e in remaining if covers(part, e)), None)
            if match:
                remaining.remove(match)
                found[match] = f"as '{part}'"
                matched[match] = part
                continue
        elif part.startswith(("Left ", "Right ")):
            match = next((e for e in remaining if _family(e) == family), None)
            if match:
                remaining.remove(match)
                wrong_side.append([part, match])
                continue
        if part in found:  # the same part listed twice
            continue
        extra.append(part)
    hidden = {e["part"] for e in expected if e.get("hidden")}
    visible_total = len(expected) - len(hidden)
    return {
        "expected": len(expected),
        "found": len(found),
        "visible_expected": visible_total,
        "visible_found": len([p for p in found if p not in hidden]),
        "found_parts": found,
        "matched": matched,
        "missed": [p for p in remaining],
        "missed_visible": [p for p in remaining if p not in hidden],
        "wrong_side": wrong_side,
        "extra": extra,
        "side_undetermined": [p for p in drafted if p.endswith(SIDE_UNDETERMINED)],
    }


def score_second_look(expected: list[dict], parts: dict, drafted: list[str], suggested: list[str]) -> dict:
    """The second look's suggestions as if a person ticked every one. parts:
    score_parts of the reading alone. found / visible_found: the key parts
    found by the reading and the suggestions together. added: key parts the
    reading missed that a suggestion found (scored against the missed parts
    only, so a suggestion can't take a part the reading already had).
    extra: suggestions that match no missed key part; wrong_side: ones that
    name a missed part's family on the other side -- both cost the surveyor
    an untick."""
    suggested = [p for p in suggested if p]
    with_look = score_parts(expected, list(drafted) + suggested)
    missed = [e for e in expected if e["part"] in parts["missed"]]
    gain = score_parts(missed, suggested)
    return {
        "suggested": len(suggested),
        "found": with_look["found"],
        "visible_found": with_look["visible_found"],
        "added": gain["found"],
        "added_visible": gain["visible_found"],
        "added_parts": list(gain["found_parts"]),
        "extra": len(gain["extra"]),
        "extra_parts": gain["extra"],
        "wrong_side": len(gain["wrong_side"]),
    }


def sorter_quality(expected: list[dict], photos: list[dict]) -> dict:
    """How useful sorting's per-photo part labels were. photos: the run's
    sorted_photos ({"part", "read", ...} in case order).

    label precision: photos tagged with a part, whose part is in the key;
    photographed recall: key parts at least one photo is tagged with;
    selection coverage: key-part photos that reached the reading call."""
    key_parts = [e["part"] for e in expected]
    hidden = {e["part"] for e in expected if e.get("hidden")}
    tagged = [p for p in photos if p.get("part")]
    in_key = [p for p in tagged if any(covers(p["part"], k) for k in key_parts)]
    photographed = [k for k in key_parts if any(covers(p["part"], k) for p in tagged)]
    parts_read = [k for k in photographed if any(covers(p["part"], k) and p.get("read") for p in tagged)]
    key_read = [p for p in in_key if p.get("read")]
    return {
        "tagged_photos": len(tagged),
        "tagged_in_key": len(in_key),
        "label_precision": _ratio(len(in_key), len(tagged)),
        "key_parts": len(key_parts),
        "key_parts_photographed": len(photographed),
        "visible_key_parts": len([k for k in key_parts if k not in hidden]),
        "visible_photographed": len([k for k in photographed if k not in hidden]),
        "photographed_recall": _ratio(len(photographed), len(key_parts)),
        "key_photos": len(in_key),
        "key_photos_read": len(key_read),
        "selection_coverage": _ratio(len(key_read), len(in_key)),
        "key_parts_read": len(parts_read),
        "not_photographed": [k for k in key_parts if k not in photographed],
        "photographed_not_read": [k for k in photographed if k not in parts_read],
        "tags_not_in_key": sorted({p["part"] for p in tagged} - {p["part"] for p in in_key}),
    }


# ---------------------------------------------------------------- vehicle facts

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.lower()
    if m := re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text):
        y, mo, d = map(int, m.groups())
    elif m := re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", text):
        d, mo, y = map(int, m.groups())
    elif m := re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\.?,?\s+(\d{4})", text):
        d, mo, y = int(m.group(1)), _MONTHS.get(m.group(2)), int(m.group(3))
    else:
        return None
    try:
        return date(y, mo, d)
    except (TypeError, ValueError):
        return None


def _alnum(value) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def score_vehicle(expected: dict, drafted: dict) -> dict:
    checks = {
        "plate_number": lambda e, a: _alnum(e) == _alnum(a),
        "make": lambda e, a: e.lower() in str(a or "").lower(),
        "model": lambda e, a: e.lower() in str(a or "").lower(),
        "color": lambda e, a: e.lower() in str(a or "").lower(),
        "vin": lambda e, a: _alnum(e) == _alnum(a),
        "engine_number": lambda e, a: _alnum(e) == _alnum(a),
        "date_of_registration": lambda e, a: _parse_date(a) == date.fromisoformat(e),
    }
    result = {}
    for field, ok in checks.items():
        got = drafted.get(field)
        result[field] = {"expected": expected[field], "got": got,
                         "verdict": "missing" if got in (None, "") else ("ok" if ok(expected[field], got) else "WRONG")}
    return result


# ---------------------------------------------------------------- conditions


def _squash(text) -> str:
    return " ".join(str(text or "").split()).casefold()


def words(text) -> list[str]:
    return re.findall(r"[a-z]+", str(text or "").casefold())


def condition_tokens(text) -> list[str]:
    """"Dented/buckled" -> ["dented", "buckled"]: the firm writes a part's
    condition as words joined by "/"."""
    return [" ".join(words(t)) for t in str(text or "").split("/") if words(t)]


def drafted_condition(item: dict) -> tuple[str | None, str | None]:
    """(value, field) of what a drafted item says about the part's state:
    `condition` (the firm's wording, once reading writes one), else
    damage_type -- the fallback prefill uses for the part's main row."""
    for field in ("condition", "damage_type"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip(), field
    return None, None


def surveyor_condition(part: dict, lines, normalise=None, side_of=None) -> dict | None:
    """The surveyor's condition for one key part: the first Annex A line of
    the held-out report (in its order) whose wording is one of the part's
    `surveyor` wordings and whose condition is a damage -- not blank, not
    Necessary or Serviceable. The key writes some wordings with a count the
    sheet doesn't have ("Headlamps (x2)"), so a wording matching no line
    exactly is matched by normalise (history.normalise_item) on the same
    side (history.side_of), since normalise_item drops LH/RH."""
    wordings = [w for w in part.get("surveyor") or [] if w]

    def damage(line) -> str | None:
        condition = str(getattr(line, "condition", "") or "").strip()
        return condition if condition and condition.casefold() not in FITTING_CONDITIONS else None

    exact = {_squash(w) for w in wordings}
    for line in lines:
        if _squash(line.item) in exact and damage(line):
            return {"item": line.item, "condition": damage(line)}
    if normalise and side_of:
        loose = {(normalise(w), side_of(w)) for w in wordings}
        for line in lines:
            if (normalise(line.item), side_of(line.item)) in loose and damage(line):
                return {"item": line.item, "condition": damage(line)}
    return None


def score_conditions(
    expected: list[dict],
    matched: dict[str, str],
    drafted_items: list[dict],
    lines,
    firm_words: set[str],
    normalise=None,
    side_of=None,
) -> dict:
    """The drafted condition of every key part found, against the
    surveyor's (surveyor_condition): exact (the same "/" words, any case),
    first word the same, and word recall (the surveyor's words present in
    the drafted value). Also the share of all drafted conditions that use
    only words the firm's reports use (firm_words)."""
    by_part: dict[str, dict] = {}
    for item in drafted_items:
        by_part.setdefault(item.get("part"), item)
    details = []
    for entry in expected:
        name = matched.get(entry["part"])
        if name is None:
            continue  # not found: there is no drafted condition to compare
        surveyor = surveyor_condition(entry, lines, normalise, side_of)
        value, source = drafted_condition(by_part.get(name) or {})
        row = {"part": entry["part"], "drafted_as": name, "surveyor": surveyor["condition"] if surveyor else None,
               "drafted": value, "source": source}
        if surveyor:
            want = condition_tokens(surveyor["condition"])
            got = condition_tokens(value)
            have = " " + " ".join(words(value)) + " "
            row["exact"] = bool(want) and want == got
            row["first_token"] = bool(want and got) and want[0] == got[0]
            row["token_hits"] = sum(1 for token in want if f" {token} " in have)
            row["tokens"] = len(want)
        details.append(row)
    scored = [d for d in details if d["surveyor"]]
    conditions = [drafted_condition(item)[0] for item in drafted_items]
    conditions = [c for c in conditions if c and words(c)]
    firm_only = [c for c in conditions if set(words(c)) <= firm_words]
    hits = sum(d["token_hits"] for d in scored)
    tokens = sum(d["tokens"] for d in scored)
    return {
        "found_parts": len(details),
        "scored": len(scored),
        "no_surveyor_condition": len(details) - len(scored),
        "exact": sum(1 for d in scored if d["exact"]),
        "first_token": sum(1 for d in scored if d["first_token"]),
        "token_hits": hits,
        "tokens": tokens,
        "token_recall": _ratio(hits, tokens),
        "drafted_conditions": len(conditions),
        "firm_words_only": len(firm_only),
        "firm_words_share": _ratio(len(firm_only), len(conditions)),
        "words_not_in_firm_reports": sorted({w for c in conditions for w in words(c) if w not in firm_words}),
        "details": details,
    }


# ---------------------------------------------------------------- summary

# (name, numerator, denominator). Recomputed from the summed counts for a
# total, never averaged over cases: a 36-part case would otherwise weigh
# the same as a five-part one.
RATIOS = (
    ("visible_recall", "visible_found", "visible_expected"),
    ("label_precision", "sorter_tagged_in_key", "sorter_tagged"),
    ("photographed_recall", "key_parts_photographed", "expected"),
    ("selection_coverage", "key_photos_read", "key_photos"),
    ("cond_exact_rate", "cond_exact", "cond_scored"),
    ("cond_first_token_rate", "cond_first_token", "cond_scored"),
    ("cond_token_recall", "cond_token_hits", "cond_tokens"),
    ("cond_firm_share", "cond_firm_only", "cond_drafted"),
    ("annex_coverage", "annex_covered", "annex_lines"),
    ("annex_perfect_coverage", "annex_perfect_covered", "annex_lines"),
)
_RATIO_NAMES = {name for name, _, _ in RATIOS}


def _ratio(num, den) -> float | None:
    return round(num / den, 3) if den else None


def run_ok(run: dict) -> bool:
    """A run that scored: an error (quota, a crash) says nothing about
    detection, so it is counted apart rather than averaged in as zeros."""
    return "parts" in run and not run.get("error")


def run_metrics(run: dict) -> dict:
    """One run as flat numbers. Works on the files written before --repeat
    ({"results": [...]}) too, so compare.py can set a new run against the
    2026-11-05 baseline."""
    if "parts" not in run:
        return {}
    p = run["parts"]
    m = {
        "found": p["found"],
        "expected": p["expected"],
        "visible_found": p["visible_found"],
        "visible_expected": p["visible_expected"],
        "extra": len(p["extra"]),
        "wrong_side": len(p["wrong_side"]),
        "side_undetermined": sum(1 for d in run.get("drafted_parts") or [] if d and d.endswith(SIDE_UNDETERMINED)),
        "facts_ok": sum(1 for x in (run.get("vehicle") or {}).values() if x.get("verdict") == "ok"),
        "photos_read": run.get("read"),
        "sort_s": run.get("sort_seconds"),
        "read_s": run.get("read_seconds"),
    }
    pc = run.get("parts_with_checklist")
    if pc:
        m.update(checklist_found=pc["found"], checklist_visible_found=pc["visible_found"], checklist_extra=len(pc["extra"]))
    look = run.get("parts_with_second_look")
    if look:
        m.update(look_found=look["found"], look_visible_found=look["visible_found"], look_added=look["added"],
                 look_extra=look["extra"], look_wrong_side=look["wrong_side"], look_suggested=look["suggested"])
    info = run.get("second_look")
    if info:
        m.update(look_calls=info.get("calls"), look_s=info.get("seconds"), look_ok=1 if info.get("status") == "ok" else 0)
    accounting = run.get("accounting")
    if accounting is not None:
        m.update(
            calls=sum(s.get("calls", 0) for s in accounting.values()),
            attempts=sum(s.get("attempts", 0) for s in accounting.values()),
            n_429=sum(s.get("n_429", 0) for s in accounting.values()),
            wait_429_s=round(sum(s.get("wait_429_s", 0.0) for s in accounting.values()), 1),
            slot_wait_s=round(sum(s.get("slot_wait_s", 0.0) for s in accounting.values()), 1),
        )
    cache = run.get("sort_cache")
    if cache:
        m["sort_cache_hits"] = cache.get("hits", 0)
    sorter = run.get("sorter")
    if sorter is None and run.get("sorted_photos") is not None:
        # Older files: the key's parts are what was found plus what was missed.
        sorter = sorter_quality([{"part": k} for k in list(p["found_parts"]) + list(p["missed"])], run["sorted_photos"])
    if sorter:
        m.update(sorter_tagged=sorter["tagged_photos"], sorter_tagged_in_key=sorter["tagged_in_key"],
                 key_parts_photographed=sorter["key_parts_photographed"], key_photos=sorter["key_photos"],
                 key_photos_read=sorter["key_photos_read"])
    cond = run.get("conditions")
    if cond:
        m.update(cond_scored=cond["scored"], cond_exact=cond["exact"], cond_first_token=cond["first_token"],
                 cond_token_hits=cond["token_hits"], cond_tokens=cond["tokens"],
                 cond_drafted=cond["drafted_conditions"], cond_firm_only=cond["firm_words_only"])
    annex = run.get("annex_a")
    if annex:
        m.update(annex_lines=annex["real_lines"], annex_covered=annex["covered"], annex_rows=annex["rows"],
                 annex_priced=annex["priced"], annex_unpriced=annex["unpriced"], annex_extra=annex["extra"],
                 annex_canonical=annex["canonical_rows"], annex_perfect_covered=annex["perfect_covered"])
    m = {k: v for k, v in m.items() if v is not None}
    return with_ratios(m)


def with_ratios(m: dict) -> dict:
    for name, num, den in RATIOS:
        if num in m and m.get(den):
            m[name] = _ratio(m[num], m[den])
    return m


def stats(values) -> dict | None:
    vals = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not vals:
        return None
    return {"mean": round(statistics.fmean(vals), 3), "min": min(vals), "max": max(vals), "n": len(vals)}


def _stats_table(rows: list[dict]) -> dict:
    names = list(dict.fromkeys(k for row in rows for k in row))
    return {k: stats([row[k] for row in rows if k in row]) for k in names}


def _sum_counts(rows: list[dict]) -> dict:
    total: dict = {}
    for row in rows:
        for k, v in row.items():
            if k not in _RATIO_NAMES:
                total[k] = round(total.get(k, 0) + v, 3)
    return with_ratios(total)


def summarize(runs: list[dict]) -> dict:
    """Mean, min and max of every metric per case (over its runs) and in
    total (over the repetitions in which every case scored, each one the
    sum over the cases)."""
    names = list(dict.fromkeys(r.get("case") for r in runs))
    reps = sorted({int(r.get("rep") or 1) for r in runs})
    cases = {}
    for name in names:
        mine = [r for r in runs if r.get("case") == name]
        rows = [run_metrics(r) for r in mine if run_ok(r)]
        cases[name] = {"runs": len(mine), "errors": len(mine) - len(rows), "metrics": _stats_table(rows)}
    totals, complete = [], []
    for rep in reps:
        mine = [r for r in runs if int(r.get("rep") or 1) == rep]
        if sorted(r.get("case") for r in mine) == sorted(names) and all(run_ok(r) for r in mine):
            totals.append(_sum_counts([run_metrics(r) for r in mine]))
            complete.append(rep)
    return {
        "cases_n": len(names),
        "reps": len(reps),
        "complete_reps": complete,
        "cases": cases,
        "total": _stats_table(totals),
        "errors": [{"case": r.get("case"), "rep": int(r.get("rep") or 1), "error": r.get("error")} for r in runs if not run_ok(r)],
        "accounting": merge_accounting([r["accounting"] for r in runs if r.get("accounting")]),
        "sort_cache": {
            "hits": sum((r.get("sort_cache") or {}).get("hits", 0) for r in runs),
            "misses": sum((r.get("sort_cache") or {}).get("misses", 0) for r in runs),
        },
    }


# ---------------------------------------------------------------- AI accounting


def _empty_stage() -> dict:
    return {"calls": 0, "failed_calls": 0, "seconds": 0.0, "attempts": 0, "outcomes": {},
            "slot_wait_s": 0.0, "n_429": 0, "wait_429_s": 0.0, "by_model": {}}


def _empty_model() -> dict:
    return {"attempts": 0, "answered": 0, "seconds": 0.0, "outcomes": {}}


def accounting_summary(calls: list[dict], attempts: list[dict]) -> dict:
    """Per purpose ("photo sort", "draft", ...): calls and their wall-clock
    seconds; every model try, by outcome and by model (answered = it gave
    the answer used); seconds waited for a rate-limit slot, and 429s with
    the seconds waited on the delay Google asked for."""
    out: dict = {}
    for c in calls:
        s = out.setdefault(c["purpose"], _empty_stage())
        s["calls"] += 1
        s["failed_calls"] += 0 if c.get("ok") else 1
        s["seconds"] += c["seconds"]
    for a in attempts:
        s = out.setdefault(a["purpose"], _empty_stage())
        s["attempts"] += 1
        s["outcomes"][a["outcome"]] = s["outcomes"].get(a["outcome"], 0) + 1
        s["slot_wait_s"] += a["slot_wait_s"]
        s["wait_429_s"] += a["wait_429_s"]
        s["n_429"] += 1 if a["outcome"] == "429" else 0
        m = s["by_model"].setdefault(a["model"], _empty_model())
        m["attempts"] += 1
        m["answered"] += 1 if a["outcome"] == "ok" else 0
        m["seconds"] += a["seconds"]
        m["outcomes"][a["outcome"]] = m["outcomes"].get(a["outcome"], 0) + 1
    return merge_accounting([out])  # the same rounding as a merge


def merge_accounting(summaries: list[dict]) -> dict:
    out: dict = {}
    for summary in summaries:
        for purpose, s in summary.items():
            t = out.setdefault(purpose, _empty_stage())
            for k in ("calls", "failed_calls", "attempts", "n_429"):
                t[k] += s.get(k, 0)
            for k in ("seconds", "slot_wait_s", "wait_429_s"):
                t[k] = round(t[k] + s.get(k, 0.0), 1)
            for outcome, n in (s.get("outcomes") or {}).items():
                t["outcomes"][outcome] = t["outcomes"].get(outcome, 0) + n
            for model, ms in (s.get("by_model") or {}).items():
                tm = t["by_model"].setdefault(model, _empty_model())
                tm["attempts"] += ms.get("attempts", 0)
                tm["answered"] += ms.get("answered", 0)
                tm["seconds"] = round(tm["seconds"] + ms.get("seconds", 0.0), 1)
                for outcome, n in (ms.get("outcomes") or {}).items():
                    tm["outcomes"][outcome] = tm["outcomes"].get(outcome, 0) + n
    return out


class Accounting:
    """Every Gemini call and model try of the current case run. Sorting's
    batches run on pool threads at once, so adding takes a lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: list[dict] = []
        self.attempts: list[dict] = []

    def clear(self) -> None:
        with self._lock:
            self.calls, self.attempts = [], []

    def add_call(self, call: dict) -> None:
        with self._lock:
            self.calls.append(call)

    def add_attempt(self, attempt: dict) -> None:
        with self._lock:
            self.attempts.append(attempt)

    def summary(self) -> dict:
        with self._lock:
            return accounting_summary(list(self.calls), list(self.attempts))

    def call_counts(self) -> Counter:
        with self._lock:
            return Counter(c["purpose"] for c in self.calls)

    def answered_by(self) -> Counter:
        """"purpose: model" per answer, as the earlier versions logged it:
        a quiet fallback to a weaker model changes the score."""
        with self._lock:
            return Counter(f"{a['purpose']}: {a['model']}" for a in self.attempts if a["outcome"] == "ok")


ACCOUNTING = Accounting()
# The purpose of the call_structured call running on this thread, and its
# last try (model, outcome): runs.note_attempt's record carries neither.
_tls = threading.local()


def _counting(fn):
    """Wraps call_structured: each call's purpose and wall-clock time."""
    def wrapper(*args, **kwargs):
        purpose = str(kwargs.get("purpose") or "unnamed")
        outer = (getattr(_tls, "purpose", None), getattr(_tls, "last", None))
        _tls.purpose, _tls.last = purpose, None
        started = time.monotonic()
        ok = False
        try:
            result = fn(*args, **kwargs)
            ok = True
            return result
        finally:
            ACCOUNTING.add_call({"purpose": purpose, "seconds": round(time.monotonic() - started, 1), "ok": ok})
            _tls.purpose, _tls.last = outer
    wrapper._bench_wrapped = True
    return wrapper


def _note(attempt: dict) -> None:
    """One model try as call_structured reports it (runs.note_attempt).
    Only model, outcome, time and waits are kept -- never the key's last
    four characters. waited_s is the rate-limit slot wait, except on the
    retry after a 429, where it is the delay Google asked for."""
    try:
        model = str(attempt.get("model"))
        outcome = str(attempt.get("outcome"))
        waited = attempt.get("waited_s")
        waited = float(waited) if isinstance(waited, (int, float)) and not isinstance(waited, bool) else 0.0
        after_429 = getattr(_tls, "last", None) == (model, "429")
        ACCOUNTING.add_attempt({
            "purpose": getattr(_tls, "purpose", None) or "unnamed",
            "model": model,
            "outcome": outcome,
            "seconds": round(float(attempt.get("ms") or 0) / 1000, 1),
            "slot_wait_s": 0.0 if after_429 else waited,
            "wait_429_s": waited if after_429 else 0.0,
        })
        _tls.last = (model, outcome)
    except Exception:  # accounting must never fail a Gemini call
        log.warning("benchmark: couldn't account a Gemini try", exc_info=True)


def _noting(fn):
    def wrapper(attempt):
        try:
            fn(attempt)
        finally:
            _note(attempt)
    wrapper._bench_wrapped = True
    return wrapper


# ---------------------------------------------------------------- the app

_APP: SimpleNamespace | None = None


def _app() -> SimpleNamespace:
    """The app's modules, imported once. call_structured is wrapped at its
    source before triage, extraction and pipeline import it by name, so
    every Gemini call is counted under its purpose -- including steps added
    later in other modules."""
    global _APP
    if _APP is not None:
        return _APP
    for path in (str(BOT_DIR), str(SCRIPTS_DIR)):
        if path not in sys.path:
            sys.path.insert(0, path)
    import app.ai.runs as runs
    import app.ai.structured as structured

    if not getattr(structured.call_structured, "_bench_wrapped", False):
        structured.call_structured = _counting(structured.call_structured)
    if not getattr(runs.note_attempt, "_bench_wrapped", False):
        runs.note_attempt = _noting(runs.note_attempt)

    import app.ai.extraction as extraction
    import app.ai.second_look as second_look
    import app.ai.triage as triage

    for module in (triage, extraction, second_look):  # imported before the wrap (not today)
        if not getattr(module.call_structured, "_bench_wrapped", False):
            module.call_structured = structured.call_structured

    from app.ai.keys import AiActor
    from app.config import settings
    from app.intake import pipeline
    from app.intake.photos import intake_dir, ordered_photos
    from app.parts.history import import_case, normalise_item, parse_report, side_of
    from app.parts.prefill import prefill_annex_a
    from app.reports.db import SessionLocal, init_db
    from app.reports.models import IntakePhoto, IntakeSession, PastAnnexLine, PastLabourLine, Report, gen_id
    from app.reports.schema import SecurityIncidentDraft
    from app.reports.taxonomy import CANONICAL_PARTS
    from app.storage.files import generate_preview

    _APP = SimpleNamespace(
        runs=runs, structured=structured, extraction=extraction, triage=triage, AiActor=AiActor,
        settings=settings, pipeline=pipeline, intake_dir=intake_dir, ordered_photos=ordered_photos,
        import_case=import_case, normalise_item=normalise_item, parse_report=parse_report, side_of=side_of,
        prefill_annex_a=prefill_annex_a, SessionLocal=SessionLocal, init_db=init_db, IntakePhoto=IntakePhoto,
        IntakeSession=IntakeSession, PastAnnexLine=PastAnnexLine, PastLabourLine=PastLabourLine, Report=Report,
        gen_id=gen_id, SecurityIncidentDraft=SecurityIncidentDraft, CANONICAL_PARTS=CANONICAL_PARTS,
        generate_preview=generate_preview,
    )
    return _APP


def load_case(folder: Path) -> str:
    """A fresh intake draft holding every photo in the folder, in file-name
    (camera) order -- what dropping the folder on the wizard produces."""
    A = _app()
    files = sorted((p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_EXTENSIONS), key=lambda p: p.name.lower())
    db = A.SessionLocal()
    try:
        report = A.Report(
            channel="manual", reporter_chat_id="benchmark", status="intake",
            data=A.SecurityIncidentDraft(description="", category=["Vehicle Collision or Damage"]).model_dump(),
        )
        db.add(report)
        db.flush()
        db.add(A.IntakeSession(report_id=report.id))
        for position, src in enumerate(files):
            photo_id = A.gen_id()
            dest = A.intake_dir(report.id) / f"{photo_id}{src.suffix.lower()}"
            shutil.copyfile(src, dest)
            A.generate_preview(str(dest))
            db.add(A.IntakePhoto(
                id=photo_id, report_id=report.id, position=position, original_name=src.name, path=str(dest),
                sha256=hashlib.sha256(dest.read_bytes()).hexdigest(), size_bytes=dest.stat().st_size,
            ))
        db.commit()
        return report.id
    finally:
        db.close()


# ---------------------------------------------------------------- sort cache

_SHA256 = re.compile(r"[0-9a-f]{64}")


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def sort_cache_dir(root: Path) -> tuple[Path, dict]:
    """The cache folder for the sorting step as it is now: the triage model
    chain, triage.SORT_PROMPT_VERSION ("v0" when there is none) and a
    fingerprint of the sorting prompt and schema. The fingerprint is there
    because a branch that changes the prompt or the area list without
    bumping a version must not be handed sorts made by the old prompt."""
    A = _app()
    chain = ",".join(m.strip() for m in A.settings.gemini_triage_model_chain.split(",") if m.strip())
    version = str(getattr(A.triage, "SORT_PROMPT_VERSION", "v0"))
    prompt = json.dumps([getattr(A.triage, "SYSTEM_PROMPT", ""), getattr(A.triage, "_RESPONSE_SCHEMA", None)],
                        sort_keys=True, default=str)
    fingerprint = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    tag = hashlib.sha256(f"{chain}|{version}|{fingerprint}".encode("utf-8")).hexdigest()[:16]
    meta = {"dir": str(root / tag), "triage_model_chain": chain, "sort_prompt_version": version, "prompt_fingerprint": fingerprint}
    folder = root / tag
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / "meta.json").exists():
        _write_json(folder / "meta.json", meta)
    return folder, meta


def apply_sort_cache(report_id: str, cache: Path) -> int:
    """Gives every unsorted photo with a cached result that result, as
    sorting would have written it. Returns how many came from the cache;
    pipeline._sort then sends only the rest (none: no call at all)."""
    A = _app()
    db = A.SessionLocal()
    hits = 0
    try:
        for photo in A.ordered_photos(db, report_id):
            if photo.kind is not None or not _SHA256.fullmatch(photo.sha256 or ""):
                continue
            path = cache / f"{photo.sha256}.json"
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(entry, dict) or not entry.get("kind"):
                continue
            for field in SORT_FIELDS:
                setattr(photo, field, entry.get(field))
            photo.shows_damage = entry.get("shows_damage") is True
            hits += 1
        db.commit()
    finally:
        db.close()
    return hits


def store_sort_cache(report_id: str, cache: Path) -> int:
    """Keeps every sorted photo's result that isn't cached yet. Returns how
    many were written."""
    A = _app()
    db = A.SessionLocal()
    written = 0
    try:
        for photo in A.ordered_photos(db, report_id):
            if photo.kind is None or not _SHA256.fullmatch(photo.sha256 or ""):
                continue
            path = cache / f"{photo.sha256}.json"
            if path.exists():
                continue
            _write_json(path, {field: getattr(photo, field) for field in SORT_FIELDS})
            written += 1
    finally:
        db.close()
    return written


# ---------------------------------------------------------------- past reports


class ReportSet:
    """The firm's past reports (--reports): each case's own report is held
    out and scored against; the others are the history prefill learns from."""

    def __init__(self, folder: Path):
        A = _app()
        self.items: list[tuple[Path, object]] = []
        self.skipped = 0
        paths = sorted(p for p in Path(folder).rglob("*")
                       if p.is_file() and p.suffix.lower() in REPORT_EXTENSIONS and not p.name.startswith("~$"))
        for path in paths:
            try:
                self.items.append((path, A.parse_report(path)))
            except Exception:  # not a report (ValueError) or an unreadable sheet
                self.skipped += 1
        # Every word of every Condition cell: the firm's own vocabulary.
        self.firm_words = {w for _, report in self.items for line in report.lines for w in words(line.condition)}

    def for_case(self, case: dict):
        """(path, parsed report) for a key case: by plate, else by a folder
        on the report's path named like the case's photo folder."""
        plate = _alnum((case.get("vehicle") or {}).get("plate_number"))
        if plate:
            for item in self.items:
                if _alnum(item[1].plate) == plate:
                    return item
        folder = str(case.get("folder") or "").casefold()
        if folder:
            for item in self.items:
                if any(part.casefold() == folder for part in item[0].parts[:-1]):
                    return item
        return None

    def others(self, held) -> list:
        return [item for item in self.items if item[0] != held[0]]


def load_history(items: list) -> None:
    """The scratch database's past-report history becomes exactly these
    reports."""
    A = _app()
    db = A.SessionLocal()
    try:
        db.query(A.PastAnnexLine).delete()
        db.query(A.PastLabourLine).delete()
        for path, report in items:
            A.import_case(db, report, path.name)
        db.commit()
    finally:
        db.close()


def score_annex_a(held, drafted_items: list[dict]) -> dict:
    """Annex A as prefill builds it from the drafted damage list (make and
    model from the held-out report, history = the other reports, loaded by
    load_history) against the held-out report's real lines, compared with
    eval_parts_kits._matched as the 2026-11-05 numbers were: lines covered,
    rows written, priced and unpriced; extra = distinct row wordings that
    match no real line; canonical rows = rows named exactly like a
    taxonomy part, which the firm never writes (target 0). The same with
    the report's own damaged parts as the damage list gives the ceiling
    that history allows (perfect detection)."""
    A = _app()
    from eval_parts_kits import _matched, damaged_parts

    real = {A.normalise_item(line.item) for line in held.lines}
    canonical = set(A.CANONICAL_PARTS)
    db = A.SessionLocal()
    try:
        def prefill(items: list[dict]) -> list[dict]:
            data = {
                "vehicle_info": {"make": held.make, "model": held.model},
                "damage_summary": copy.deepcopy(items),
                "damaged_parts": [i["part"] for i in items],
                "annex_a": {}, "annex_b": {},
            }
            return A.prefill_annex_a(db, data)["annex_a"]["items"]

        rows = prefill([i for i in drafted_items if i.get("part")])
        perfect = prefill([{"part": p, "damage_type": ""} for p in damaged_parts(held)])
    finally:
        db.rollback()
        db.close()
    keys = {A.normalise_item(r["part_name"]) for r in rows if r.get("part_name")}
    hit = _matched(real, keys)
    perfect_hit = _matched(real, {A.normalise_item(r["part_name"]) for r in perfect if r.get("part_name")})
    priced = sum(1 for r in rows if float(r.get("workshop_est") or 0) > 0)
    named = [r["part_name"] for r in rows if r.get("part_name") in canonical]
    return {
        "real_lines": len(real),
        "rows": len(rows),
        "priced": priced,
        "unpriced": len(rows) - priced,
        "covered": len(hit),
        "coverage": _ratio(len(hit), len(real)),
        # _matched pairs each covered line with a different wording.
        "extra": len(keys) - len(hit),
        "canonical_rows": len(named),
        "canonical_names": sorted(set(named)),
        "perfect_rows": len(perfect),
        "perfect_covered": len(perfect_hit),
        "perfect_coverage": _ratio(len(perfect_hit), len(real)),
    }


# ---------------------------------------------------------------- one run

_ITEM_FIELDS = ("part", "damage_type", "condition", "condition_tokens", "remarks", "severity", "ai_confidence", "side_inferred")


def run_case(case: dict, cases_dir: Path, *, sort_cache: Path | None = None, reports: ReportSet | None = None,
             keep_photos: bool = False) -> dict:
    A = _app()
    held = reports.for_case(case) if reports else None
    if held is not None:
        # Loaded before reading, not only before scoring: the firm's history
        # as it would be for this case (every other report, not its own), so
        # a reading step that consults past cases is leave-one-out as well.
        load_history(reports.others(held))
    rid = load_case(cases_dir / case["folder"])
    ACCOUNTING.clear()
    out: dict = {"case": case["name"], "report_id": rid}
    cache_info = {"hits": apply_sort_cache(rid, sort_cache)} if sort_cache else None
    started = time.monotonic()
    try:
        # The company keys, as the benchmark always ran on.
        A.pipeline._sort(rid, A.AiActor.cli())
        out["sort_seconds"] = round(time.monotonic() - started, 1)
        started = time.monotonic()
        A.pipeline._read(rid, A.AiActor.cli())
        out["read_seconds"] = round(time.monotonic() - started, 1)
    except Exception as exc:  # recorded, and the next case still runs
        out["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        if sort_cache:
            cache_info["stored"] = store_sort_cache(rid, sort_cache)
    out["calls"] = ACCOUNTING.call_counts()
    out["answered_by"] = ACCOUNTING.answered_by()
    out["accounting"] = ACCOUNTING.summary()

    db = A.SessionLocal()
    try:
        report = db.get(A.Report, rid)
        session = db.get(A.IntakeSession, rid)
        photos = A.ordered_photos(db, rid)
        read_ids = set(session.read_photo_ids or [])
        data = report.data or {}
        out["photos"] = len(photos)
        out["read"] = len(read_ids)
        if cache_info is not None:
            cache_info["misses"] = len(photos) - cache_info["hits"]
            out["sort_cache"] = cache_info
        out["kinds"] = dict(Counter(p.kind or "unsorted" for p in photos))
        out["read_kinds"] = dict(Counter(p.kind or "unsorted" for p in photos if p.id in read_ids))
        damage_photos = [p for p in photos if p.shows_damage]
        out["damage_photos_read"] = f"{len([p for p in damage_photos if p.id in read_ids])}/{len(damage_photos)}"
        out["sorted_photos"] = [
            {"n": i + 1, "file": p.original_name, "kind": p.kind, "area": p.area, "quality": p.quality,
             "damage": p.shows_damage, "part": p.part, "read": p.id in read_ids, "note": p.note}
            for i, p in enumerate(photos)
        ]
        items = [item for item in data.get("damage_summary") or [] if isinstance(item, dict)]
        drafted = [item.get("part") for item in items]
        out["drafted_parts"] = drafted
        out["drafted_items"] = [{k: item.get(k) for k in _ITEM_FIELDS if k in item} for item in items]
        out["parts"] = score_parts(case["parts"], drafted)
        # What a "parts to check" list would add: every part the sorter named
        # on a photo of the insured vehicle, damage visible or not -- surveyors
        # photograph close up the parts they go on to claim.
        hinted = sorted({p.part for p in photos if p.part} - set(drafted))
        out["checklist"] = hinted
        out["parts_with_checklist"] = score_parts(case["parts"], drafted + hinted)
        look = data.get("second_look") if isinstance(data.get("second_look"), dict) else None
        if look:
            suggested = [it.get("part") for it in look.get("items") or [] if isinstance(it, dict) and it.get("part")]
            out["second_look"] = {
                "status": look.get("status"), "model": look.get("model"), "note": look.get("note"),
                "seconds": look.get("seconds"), "photos_sent": look.get("photos_sent"),
                "candidates": look.get("candidates"), "suggested": suggested,
                "calls": out["calls"].get("second look", 0),
            }
            out["parts_with_second_look"] = score_second_look(case["parts"], out["parts"], drafted, suggested)
            # _read runs the second look after the reading: read_seconds stays
            # the reading's own, comparable with runs from before it.
            if isinstance(look.get("seconds"), (int, float)) and "read_seconds" in out:
                out["read_seconds"] = round(max(0.0, out["read_seconds"] - look["seconds"]), 1)
        out["vehicle"] = score_vehicle(case["vehicle"], data.get("vehicle_info") or {})
        out["sorter"] = sorter_quality(case["parts"], out["sorted_photos"])
    finally:
        db.close()

    if held is not None:
        out["conditions"] = score_conditions(case["parts"], out["parts"]["matched"], items, held[1].lines,
                                             reports.firm_words, A.normalise_item, A.side_of)
        out["annex_a"] = score_annex_a(held[1], items)
    if not keep_photos:
        # A repeat run copies every case's photos again; on the live
        # server's small disk, a full disk stops the app taking uploads.
        shutil.rmtree(Path(A.settings.storage_dir) / "intake" / rid, ignore_errors=True)
    return out


# ---------------------------------------------------------------- printing


def summary_line(r: dict) -> str:
    if "parts" not in r:
        return f"{r['case']:<11} ERROR {r.get('error')}"
    p, v = r["parts"], r["vehicle"]
    facts = sum(1 for x in v.values() if x["verdict"] == "ok")
    return (f"{r['case']:<11} parts {p['found']:>2}/{p['expected']:<2} (visible {p['visible_found']:>2}/{p['visible_expected']:<2}) "
            f"wrong-side {len(p['wrong_side'])} extra {len(p['extra'])} | facts {facts}/{len(v)} | "
            f"read {r['read']}/{r['photos']} (damage {r['damage_photos_read']}) | "
            f"sort {r.get('sort_seconds', '-')}s read {r.get('read_seconds', '-')}s | models {dict(r['answered_by'])}"
            + (f" | ERROR {r['error']}" if r.get("error") else ""))


def _pct(value) -> str:
    return "-" if value is None else f"{100 * value:.0f}%"


def detail_lines(r: dict) -> list[str]:
    lines = []
    if "parts" not in r:
        return lines
    lines.append(f"   missed (visible): {r['parts']['missed_visible']}")
    lines.append(f"   wrong side: {r['parts']['wrong_side']}  extra: {r['parts']['extra']}  "
                 f"side undetermined: {len(r['parts']['side_undetermined'])}")
    lines.append(f"   facts: { {k: (x['verdict'], x['got']) for k, x in r['vehicle'].items()} }")
    pc = r["parts_with_checklist"]
    lines.append(f"   + checklist {r['checklist']}")
    lines.append(f"   with checklist: parts {pc['found']}/{pc['expected']} (visible {pc['visible_found']}/{pc['visible_expected']}) "
                 f"wrong-side {len(pc['wrong_side'])} extra {len(pc['extra'])} {pc['extra']}")
    look, ls = r.get("second_look"), r.get("parts_with_second_look")
    if look and ls:
        lines.append(f"   second look: {look['status']} on {look.get('model') or '-'} in {look.get('seconds')}s, "
                     f"{look['calls']} call(s), {look.get('photos_sent')} photos, {look.get('candidates')} candidates; "
                     f"suggested {look['suggested']}" + (f" ({look['note']})" if look.get("note") else ""))
        lines.append(f"   if every suggestion were ticked: parts {ls['found']}/{r['parts']['expected']} "
                     f"(visible {ls['visible_found']}/{r['parts']['visible_expected']}), added {ls['added']} "
                     f"{ls['added_parts']}, extra {ls['extra']} {ls['extra_parts']}, wrong side {ls['wrong_side']}")
    s = r["sorter"]
    cache = r.get("sort_cache")
    lines.append(f"   sorter: labels in key {s['tagged_in_key']}/{s['tagged_photos']} ({_pct(s['label_precision'])}), "
                 f"key parts photographed {s['key_parts_photographed']}/{s['key_parts']}, "
                 f"key-part photos read {s['key_photos_read']}/{s['key_photos']} ({_pct(s['selection_coverage'])})"
                 + (f" | sort cache: {cache['hits']} from cache, {cache['stored']} stored" if cache else ""))
    stages = []
    for purpose, st in r["accounting"].items():
        models = ", ".join(f"{m} {ms['answered']}/{ms['attempts']}" for m, ms in st["by_model"].items())
        stages.append(f"{purpose}: {st['calls']} call(s) {st['seconds']}s, tries {st['attempts']} [{models}], "
                      f"slot wait {st['slot_wait_s']}s, 429 x{st['n_429']} waited {st['wait_429_s']}s")
    lines.append("   AI: " + ("; ".join(stages) if stages else "no calls"))
    c = r.get("conditions")
    if c:
        lines.append(f"   conditions: {c['scored']} scored, exact {c['exact']}, first word {c['first_token']}, "
                     f"word recall {_pct(c['token_recall'])}; firm words only {c['firm_words_only']}/{c['drafted_conditions']}"
                     + (f", other words {c['words_not_in_firm_reports']}" if c["words_not_in_firm_reports"] else ""))
    a = r.get("annex_a")
    if a:
        lines.append(f"   Annex A (leave-one-out): covered {a['covered']}/{a['real_lines']} ({_pct(a['coverage'])}); "
                     f"rows {a['rows']} (priced {a['priced']}, unpriced {a['unpriced']}, extra {a['extra']}, "
                     f"canonical names {a['canonical_rows']}); perfect detection {a['perfect_covered']}/{a['real_lines']}")
    return lines


def _fmt(st: dict | None) -> str:
    if not st:
        return "-"
    if st["n"] == 1 or st["min"] == st["max"]:
        return f"{st['mean']:g}"
    return f"{st['mean']:.4g} [{st['min']:g}-{st['max']:g}]"


TABLES = (
    (("found", "found"), ("visible", "visible_found"), ("extra", "extra"), ("wrong side", "wrong_side"),
     ("undetermined", "side_undetermined"), ("facts", "facts_ok"), ("read", "photos_read"),
     ("sort s", "sort_s"), ("read s", "read_s")),
    (("labels in key", "label_precision"), ("photographed", "photographed_recall"),
     ("selection", "selection_coverage"), ("calls", "calls"), ("429 wait s", "wait_429_s"),
     ("cond exact", "cond_exact_rate"), ("cond words", "cond_token_recall"), ("firm words", "cond_firm_share"),
     ("Annex A", "annex_coverage"), ("A extra", "annex_extra"), ("A canonical", "annex_canonical")),
    (("+look found", "look_found"), ("+look visible", "look_visible_found"), ("look added", "look_added"),
     ("look extra", "look_extra"), ("look wrong side", "look_wrong_side"), ("suggested", "look_suggested"),
     ("look calls", "look_calls"), ("look s", "look_s")),
)


def summary_table(summary: dict) -> list[str]:
    out = [f"SUMMARY: {summary['reps']} run(s) of {summary['cases_n']} case(s), mean [min-max]; "
           f"TOTAL over the {len(summary['complete_reps'])} complete run(s)"]
    rows = [(name, c["metrics"]) for name, c in summary["cases"].items()] + [("TOTAL", summary["total"])]
    for table in TABLES:
        present = [(head, key) for head, key in table if any(m.get(key) for _, m in rows)]
        if not present:
            continue
        cells = [["case"] + [head for head, _ in present]]
        cells += [[name] + [_fmt(m.get(key)) for _, key in present] for name, m in rows]
        widths = [max(len(row[i]) for row in cells) for i in range(len(cells[0]))]
        out.append("")
        out += ["  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in cells]
    if summary["errors"]:
        out.append(f"\nERRORS ({len(summary['errors'])}, left out of the numbers above):")
        out += [f"  {e['case']} run {e['rep']}: {e['error']}" for e in summary["errors"]]
    return out


# ---------------------------------------------------------------- main


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _chain(text: str) -> str:
    models = [m.strip() for m in text.split(",") if m.strip()]
    if not models:
        raise argparse.ArgumentTypeError("needs at least one model name")
    return ",".join(models)


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="benchmark_intake.py",
        description="Scores the intake AI (sorting + reading, real Gemini calls) against the surveyors' reports. "
                    "Needs BENCHMARK_SCRATCH=1 with DATABASE_URL and STORAGE_DIR pointing at scratch locations.",
        epilog="On the server: scripts/bench/run_bench.sh LABEL GIT_REF [these options]; compare two result "
               "files with scripts/bench/compare.py BEFORE.json AFTER.json (scripts/bench/README.md).",
    )
    ap.add_argument("--cases", required=True, type=Path, help="folder holding one photo folder per key case")
    ap.add_argument("--key", required=True, type=Path, help="answer key (key.json, kept out of git)")
    ap.add_argument("--out", required=True, type=Path, help="result JSON, rewritten after every run")
    ap.add_argument("--only", action="append", help="case name; repeatable")
    ap.add_argument("--repeat", type=_positive, default=1, metavar="N",
                    help="run every case N times (default 1); results give mean, min and max")
    ap.add_argument("--sort-cache", type=Path, metavar="DIR",
                    help="keep sorting results by photo sha256 (+ triage chain, SORT_PROMPT_VERSION and prompt "
                         "fingerprint); a cached photo costs no sorting call")
    ap.add_argument("--reports", type=Path, metavar="DIR",
                    help="folder with the CL report spreadsheets: scores drafted conditions and the Annex A prefill "
                         "builds, each case against its own report with the others as history (leave-one-out)")
    ap.add_argument("--models", type=_chain, metavar="CHAIN", help="override the reading model chain (comma-separated)")
    ap.add_argument("--triage-models", type=_chain, metavar="CHAIN",
                    help="override the sorting model chain (comma-separated)")
    ap.add_argument("--second-look-models", type=_chain, metavar="CHAIN",
                    help="override the second look's model chain (comma-separated)")
    ap.add_argument("--no-second-look", action="store_true",
                    help="leave the second look out (the reading alone, as before it existed)")
    ap.add_argument("--label", help="name of this run in the result file (default: the --out file name)")
    ap.add_argument("--ref", help="git ref (and commit) the code came from, for the result file")
    ap.add_argument("--keep-photos", action="store_true",
                    help="keep each run's copied photos in STORAGE_DIR (default: deleted after scoring)")
    return ap.parse_args(argv)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main(argv=None) -> int:
    args = parse_args(argv)
    if os.environ.get("BENCHMARK_SCRATCH") != "1":
        sys.exit(REFUSAL)
    A = _app()
    if args.models:
        A.settings.gemini_model_chain = args.models
    if args.triage_models:
        A.settings.gemini_triage_model_chain = args.triage_models
    if args.second_look_models:
        A.settings.gemini_second_look_chain = args.second_look_models
    if args.no_second_look:
        A.settings.second_look_enabled = False
    A.init_db()
    key = json.loads(args.key.read_text(encoding="utf-8"))
    unknown = sorted(set(args.only or []) - {c["name"] for c in key["cases"]})
    if unknown:
        sys.exit(f"--only: not in the key: {unknown}")
    cases = [c for c in key["cases"] if not args.only or c["name"] in args.only]

    reports = None
    if args.reports:
        reports = ReportSet(args.reports)
        print(f"reports: {len(reports.items)} read, {reports.skipped} skipped", flush=True)
        for case in cases:
            if reports.for_case(case) is None:
                print(f"   no report for {case['name']}: its conditions and Annex A are not scored", flush=True)
    cache, cache_meta = sort_cache_dir(args.sort_cache) if args.sort_cache else (None, None)

    doc = {
        "label": args.label or args.out.stem,
        "git_ref": args.ref,
        "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "models": {"model_chain": A.settings.gemini_model_chain, "triage_model_chain": A.settings.gemini_triage_model_chain,
                   "second_look_chain": A.settings.gemini_second_look_chain if A.settings.second_look_enabled else None},
        "sort_cache": cache_meta,
        "started": _now(),
        "finished": None,
        "runs": [],
        "summary": None,
    }
    for rep in range(1, args.repeat + 1):
        for case in cases:
            print(f"== {case['name']}" + (f"  (run {rep}/{args.repeat})" if args.repeat > 1 else ""), flush=True)
            r = run_case(case, args.cases, sort_cache=cache, reports=reports, keep_photos=args.keep_photos)
            r["rep"] = rep
            doc["runs"].append(r)
            print(summary_line(r), flush=True)
            for line in detail_lines(r):
                print(line, flush=True)
            # After every run: a long run cut short keeps what it measured.
            doc["summary"] = summarize(doc["runs"])
            _write_json(args.out, doc)

    for rep in range(1, args.repeat + 1):
        totals = Counter()
        for r in doc["runs"]:
            if r["rep"] == rep and "parts" in r:
                for k in ("found", "expected", "visible_found", "visible_expected"):
                    totals[k] += r["parts"][k]
                    totals[f"checklist_{k}"] += r["parts_with_checklist"][k]
                totals["extra"] += len(r["parts"]["extra"])
                totals["checklist_extra"] += len(r["parts_with_checklist"]["extra"])
                ls = r.get("parts_with_second_look")
                if ls:
                    totals["look_runs"] += 1
                    for k in ("found", "visible_found", "added", "extra", "wrong_side", "suggested"):
                        totals[f"look_{k}"] += ls[k]
        head = f"run {rep}: " if args.repeat > 1 else ""
        print(f"\n{head}TOTAL parts {totals['found']}/{totals['expected']}, visible {totals['visible_found']}/{totals['visible_expected']}, "
              f"extra {totals['extra']}")
        print(f"{head}WITH CHECKLIST parts {totals['checklist_found']}/{totals['checklist_expected']}, "
              f"visible {totals['checklist_visible_found']}/{totals['checklist_visible_expected']}, extra {totals['checklist_extra']}")
        if totals["look_runs"]:
            print(f"{head}WITH SECOND LOOK (all ticked) parts {totals['look_found']}/{totals['expected']}, "
                  f"visible {totals['look_visible_found']}/{totals['visible_expected']}: added {totals['look_added']} "
                  f"from {totals['look_suggested']} suggestions, extra {totals['look_extra']}, "
                  f"wrong side {totals['look_wrong_side']}")
    print()
    for line in summary_table(doc["summary"]):
        print(line)
    doc["finished"] = _now()
    _write_json(args.out, doc)
    print("written", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
