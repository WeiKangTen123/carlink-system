"""Why one key part is on the damage list in some benchmark runs and not in
others (contract 10 P0): per run of one case, everything a stored result
file (scripts/benchmark_intake.py --out) says about that part.

    python scripts/bench/why_missing.py CASE PART FILE.json [FILE.json ...]
    python scripts/bench/why_missing.py CASE PART FILES... --rates

CASE matches the start of a run's case name; PART is a canonical part name
("Left Tail Lamp"). Per file: the sort cache it used (the folder's tag,
triage chain, prompt version) and the reading settings. Per run of the case:

  - the sort: a fingerprint of every photo's kind / area / part / damage
    flag, and of the photos the reading was sent -- two runs with the same
    fingerprints read the same photos with the same captions;
  - the photos the sorter tagged with the part's family (either side), and
    whether each was read;
  - who answered (a 503 falls back down the chain: draft 3.5-lite ->
    nothing; zone calls 3.5-lite -> 3.1-lite) and the drafting call's
    prompt tokens (the same number = the same request size);
  - the list's items of the part's family: name, found_by, seen_in, photo,
    side_inferred, confidence -- and whether the overall reading had it
    (zone mode: "overall" in found_by; single mode: every list item is the
    overall reading's);
  - the scoring: wrong_side and side_undetermined pairs of the family;
  - each zone read: status, side seen, finds, and whether it listed the
    family INTACT (which sides);
  - the suggestions of the family (option B's zone-only finds);
  - a verdict: FOUND, WRONG SIDE, UNDETERMINED, SUGGESTED, NOT READ (the
    overall reading listed no part of the family), or RUN FAILED.

--rates: instead, per file, how often each of the case's key parts was on
the list (exact / wrong side / as "(side undetermined)") -- to see whether one part's
gap between two settings stands out from the others' or is one of many.

Offline: reads the JSON files, imports only app/reports/taxonomy (names).
The files hold real plates: they never go into git.
"""
import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.reports.taxonomy import UNDETERMINED_SUFFIX, split_side, unsided_part  # noqa: E402

OVERALL = "overall"


def family(part: str) -> str:
    """"Left Tail Lamp" -> "Tail Lamp (side undetermined)": the name every
    side of the part shares (taxonomy.unsided_part), or the part itself."""
    return unsided_part(part or "") or part or ""


def same_family(a: str, b: str) -> bool:
    return bool(a) and bool(b) and family(a) == family(b)


def fingerprint(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:8]


def sort_fingerprints(run: dict) -> tuple[str, str]:
    """(the sort, the photos read): two runs alike in both sent the reading
    the same photos with the same sorter captions."""
    photos = run.get("sorted_photos") or []
    sort = fingerprint([(p.get("file"), p.get("kind"), p.get("area"), p.get("part"), p.get("damage"), p.get("note"))
                        for p in photos])
    read = fingerprint([p.get("file") for p in photos if p.get("read")])
    return sort, read


def mode_of(doc: dict) -> str:
    settings = doc.get("ai_settings") or {}
    args = doc.get("args") or {}
    mode = settings.get("intake_read_mode") or args.get("read_mode") or "single"
    if mode == "zones":
        mode += "+" + (settings.get("intake_zone_only") or args.get("zone_only") or "list(c8)")
    if settings.get("intake_sort_damage_box") or args.get("sort_damage_box"):
        mode += " sort-box"
    if settings.get("intake_read_crops") or args.get("read_crops") is not None:
        mode += " crops"
    if settings.get("intake_zone_evidence") or args.get("zone_evidence"):
        mode += " ev=" + str(settings.get("intake_zone_evidence") or args.get("zone_evidence"))
    return mode


def answered(run: dict) -> dict[str, list[str]]:
    """{stage: ["model x n", ...]} from answered_by ("draft: gemini-...")."""
    out: dict[str, list[str]] = {}
    for key, n in (run.get("answered_by") or {}).items():
        stage, _, model = key.partition(": ")
        out.setdefault(stage, []).append(f"{model} x{n}")
    return out


def draft_calls(run: dict) -> list[str]:
    return [f"{c.get('model') or '-'} ok={c.get('ok')} in={c.get('prompt_tokens')} out={c.get('output_tokens')} "
            f"think={c.get('thought_tokens')}"
            for c in run.get("ai_calls") or [] if c.get("purpose") == "draft"]


def suggestions(run: dict) -> list[dict]:
    """The run's suggestions as dicts: contract 9 files keep each one
    ("suggestions"); older ones only their names (second_look.suggested)."""
    rows = run.get("suggestions")
    if isinstance(rows, list) and rows:
        return [r for r in rows if isinstance(r, dict)]
    names = (run.get("second_look") or {}).get("suggested") or []
    return [{"part": n} for n in names if isinstance(n, str)]


def verdict(run: dict, part: str, zone_mode: bool) -> str:
    if run.get("error") and not run.get("drafted_items"):
        return "RUN FAILED"
    parts = run.get("parts") or {}
    how = (parts.get("found_parts") or {}).get(part)
    if how == "exact":
        return "FOUND"
    if how:  # "as 'Tail Lamp (side undetermined)'": counted found by the scoring
        return "UNDETERMINED"
    if any(pair[-1] == part for pair in parts.get("wrong_side") or []):
        return "WRONG SIDE"
    # The family's items, less those that are the case's other key parts
    # found as they are (a Right Tail Lamp both lamps' case lists rightly).
    found = parts.get("found_parts") or {}
    listed = [it for it in run.get("drafted_items") or [] if same_family(str(it.get("part") or ""), part)
              and found.get(str(it.get("part"))) != "exact"]
    if listed:
        return "LISTED OTHERWISE"
    if any(same_family(str(s.get("part") or ""), part) for s in suggestions(run)):
        return "SUGGESTED ONLY"
    return "NOT READ"


def cause(run: dict, part: str, zone_mode: bool) -> str:
    """The likeliest reason, from what the file holds (the report explains
    the rules behind each)."""
    v = verdict(run, part, zone_mode)
    if v == "RUN FAILED":
        return f"the run failed before any list: {str(run.get('error'))[:90]}"
    if v == "FOUND":
        return "on the list"
    listed = [it for it in run.get("drafted_items") or [] if same_family(str(it.get("part") or ""), part)]
    if v == "WRONG SIDE":
        it = next((i for i in listed if split_side(str(i.get("part") or ""))[1]), listed[0] if listed else {})
        by = it.get("found_by") or []
        if it.get("side_inferred"):
            return "H5: the side resolver placed it from the sorter's areas"
        if zone_mode and len(by) > 1:
            return f"H4: a zone read's side taken ({by})"
        return "H2: the overall reading itself named the other side"
    if v == "UNDETERMINED":
        it = next((i for i in listed if str(i.get("part") or "").endswith(UNDETERMINED_SUFFIX)), {})
        if zone_mode and len(it.get("found_by") or []) > 1 and it.get("ai_confidence") == "Low":
            return "H3: the reads disputed the side on one photo"
        return "the overall reading left the side undetermined and nothing placed it"
    if v == "SUGGESTED ONLY":
        return "only a zone read found it: a suggestion (option B), not a list item"
    if v == "NOT READ":
        return ("the overall reading listed no part of this family (option B keeps every overall item on the "
                "list: test_zone_merge.py)" if zone_mode else "the reading listed no part of this family")
    return "listed under another name"


def show_file(path: Path, case: str, part: str) -> list[tuple]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    cache = doc.get("sort_cache") or {}
    mode = mode_of(doc)
    zone_mode = mode.startswith("zones")
    tag = Path(str(cache.get("dir") or "")).name or "-"
    print(f"\n=== {path.name}  [{doc.get('label')}]  {doc.get('git_ref') or (doc.get('args') or {}).get('ref')}")
    print(f"    started {doc.get('started')}  mode: {mode}")
    print(f"    sort cache {tag}: chain {cache.get('triage_model_chain')}, prompt {cache.get('sort_prompt_version')} "
          f"/ {cache.get('prompt_fingerprint')}")
    rows = []
    for i, run in enumerate(doc.get("runs") or []):
        if not str(run.get("case") or "").startswith(case):
            continue
        sort, read = sort_fingerprints(run)
        v = verdict(run, part, zone_mode)
        why = cause(run, part, zone_mode)
        rows.append((path.name, doc.get("label"), mode, tag, i, run.get("rep"), sort, read, v, why))
        print(f"\n  run {i} (rep {run.get('rep')}): {v} -- {why}")
        print(f"    sort {sort}, photos read {read} ({run.get('read')} of {run.get('photos')}); "
              f"sort cache hits {(run.get('sort_cache') or {}).get('hits')}")
        for stage, models in answered(run).items():
            fallback = " <- 503 fallback" if stage == "draft_zone" and any("3.1" in m for m in models) else ""
            print(f"    answered {stage}: {', '.join(models)}{fallback}")
        for call in draft_calls(run):
            print(f"    draft call: {call}")
        tagged = [p for p in run.get("sorted_photos") or [] if same_family(str(p.get("part") or ""), part)]
        print("    sorter tagged the family on: " + (", ".join(
            f"P{p['n']:02d} {p.get('area')} {p.get('part')}{' READ' if p.get('read') else ''}"
            f"{'' if p.get('damage') else ' (no damage)'}" for p in tagged) or "no photo"))
        items = [it for it in run.get("drafted_items") or [] if same_family(str(it.get("part") or ""), part)]
        if not items:
            print(f"    list: no item of the family ({len(run.get('drafted_items') or [])} items: "
                  f"{', '.join(str(it.get('part')) for it in run.get('drafted_items') or [])})")
        for it in items:
            by = it.get("found_by")
            overall = (OVERALL in by) if by else True
            print(f"    list: {it.get('part')}  found_by={by} seen_in={it.get('seen_in')} "
                  f"photo={it.get('photo_reference', '(not kept)')} inferred={it.get('side_inferred')} "
                  f"conf={it.get('ai_confidence')}  overall reading had it: {'yes' if overall else 'no'}")
        parts = run.get("parts") or {}
        ws = [pair for pair in parts.get("wrong_side") or [] if any(same_family(x, part) for x in pair)]
        und = [u for u in parts.get("side_undetermined") or [] if same_family(u, part)]
        if ws or und:
            print(f"    scored: wrong_side {ws}  side_undetermined {und}")
        for zone in (run.get("zones") or {}).get("zones") or []:
            intact = [p for p in zone.get("intact") or [] if same_family(p, part)]
            print(f"    zone {zone.get('zone')}: {zone.get('status')}, side seen {zone.get('side_seen')}, "
                  f"found {zone.get('found')}, family intact: {intact or '-'}, photos {len(zone.get('photos') or [])}"
                  + (f" ({zone.get('error_code')})" if zone.get("error_code") else ""))
        sug = [s for s in suggestions(run) if same_family(str(s.get("part") or ""), part)]
        for s in sug:
            print(f"    suggestion: {s.get('part')} found_by={s.get('found_by')} seen_in={s.get('seen_in')} "
                  f"truth={s.get('truth', '-')}")
    return rows


def rates(paths: list[Path], case: str) -> None:
    """Per file: each key part's tally over the case's runs that read."""
    for path in paths:
        doc = json.loads(path.read_text(encoding="utf-8"))
        runs = [r for r in doc.get("runs") or [] if str(r.get("case") or "").startswith(case) and r.get("parts")
                and not (r.get("error") and not r.get("drafted_items"))]
        tally: dict[str, Counter] = {}
        for run in runs:
            parts = run["parts"]
            found = parts.get("found_parts") or {}
            wrong = [pair[-1] for pair in parts.get("wrong_side") or []]
            for key in list(found) + list(parts.get("missed") or []) + wrong:
                c = tally.setdefault(key, Counter())
                if found.get(key) == "exact":
                    c["found"] += 1
                elif key in found:
                    c["undetermined"] += 1
                elif key in wrong:
                    c["wrong_side"] += 1
        print(f"\n=== {path.name} [{mode_of(doc)}] {len(runs)} run(s) that read")
        for key in sorted(tally):
            c = tally[key]
            if c:
                print(f"    {key:<34} found {c['found']}/{len(runs)}"
                      + (f"  wrong side {c['wrong_side']}" if c["wrong_side"] else "")
                      + (f"  as undetermined {c['undetermined']}" if c["undetermined"] else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("case", help="the start of the case's name in the result files")
    ap.add_argument("part", help="a canonical part name, e.g. 'Left Tail Lamp'")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--rates", action="store_true", help="each key part's tally per file instead")
    args = ap.parse_args(argv)
    if args.rates:
        rates(args.files, args.case)
        return 0
    rows = []
    for path in args.files:
        rows += show_file(path, args.case, args.part)
    print(f"\n=== {args.part}: per run")
    print(f"{'file':<22} {'mode':<32} {'sort':<17} run  {'sort fp':<9}{'read fp':<9}verdict")
    for name, _label, mode, tag, i, _rep, sort, read, v, _why in rows:
        print(f"{name:<22} {mode:<32} {tag:<17} {i:>3}  {sort:<9}{read:<9}{v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
