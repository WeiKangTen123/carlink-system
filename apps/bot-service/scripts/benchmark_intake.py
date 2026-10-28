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
        --out /bench/out/baseline.json [--only "SLK 3063 Z"]
"""
import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

if os.environ.get("BENCHMARK_SCRATCH") != "1":
    sys.exit("Refusing to run: set BENCHMARK_SCRATCH=1 with DATABASE_URL and STORAGE_DIR pointing at scratch locations.")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.ai.extraction as extraction  # noqa: E402
import app.ai.triage as triage  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.intake import pipeline  # noqa: E402
from app.intake.photos import intake_dir, ordered_photos  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import IntakePhoto, IntakeSession, Report, gen_id  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402
from app.storage.files import generate_preview  # noqa: E402

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
SIDE_UNDETERMINED = " (side undetermined)"

calls: list[dict] = []


def _counting(fn):
    def wrapper(*args, **kwargs):
        started = time.monotonic()
        try:
            return fn(*args, **kwargs)
        finally:
            calls.append({"purpose": kwargs.get("purpose"), "seconds": round(time.monotonic() - started, 1)})
    return wrapper


triage.call_structured = _counting(triage.call_structured)
extraction.call_structured = _counting(extraction.call_structured)

answered_by: list[str] = []


class _ModelLog(logging.Handler):
    """Notes which model answered each call (call_structured logs it), since
    a quiet fallback to a weaker model changes the score."""

    def emit(self, record: logging.LogRecord) -> None:
        if "produced by" in record.msg and len(record.args) >= 2:
            answered_by.append(f"{record.args[0]}: {record.args[1]}")


_structured_log = logging.getLogger("app.ai.structured")
_structured_log.setLevel(logging.INFO)
_structured_log.addHandler(_ModelLog())


def load_case(folder: Path) -> str:
    """A fresh intake draft holding every photo in the folder, in file-name
    (camera) order -- what dropping the folder on the wizard produces."""
    files = sorted((p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_EXTENSIONS), key=lambda p: p.name.lower())
    db = SessionLocal()
    try:
        report = Report(
            channel="manual", reporter_chat_id="benchmark", status="intake",
            data=SecurityIncidentDraft(description="", category=["Vehicle Collision or Damage"]).model_dump(),
        )
        db.add(report)
        db.flush()
        db.add(IntakeSession(report_id=report.id))
        for position, src in enumerate(files):
            photo_id = gen_id()
            dest = intake_dir(report.id) / f"{photo_id}{src.suffix.lower()}"
            shutil.copyfile(src, dest)
            generate_preview(str(dest))
            db.add(IntakePhoto(
                id=photo_id, report_id=report.id, position=position, original_name=src.name, path=str(dest),
                sha256=hashlib.sha256(dest.read_bytes()).hexdigest(), size_bytes=dest.stat().st_size,
            ))
        db.commit()
        return report.id
    finally:
        db.close()


def _family(part: str) -> str:
    for side in ("Left ", "Right "):
        if part.startswith(side):
            part = part[len(side):]
    return part.removesuffix(SIDE_UNDETERMINED)


def score_parts(expected: list[dict], drafted: list[str]) -> dict:
    """Matches drafted part names to the key: exact name first, then a
    side-undetermined name against a same-family part (counted as found),
    then a same-family part on the wrong side (reported, not counted)."""
    remaining = [e["part"] for e in expected]
    found: dict[str, str] = {}
    wrong_side: list[list[str]] = []
    extra: list[str] = []
    leftover = []
    for part in drafted:
        if part in remaining:
            remaining.remove(part)
            found[part] = "exact"
        else:
            leftover.append(part)
    for part in leftover:
        family = _family(part)
        if part.endswith(SIDE_UNDETERMINED):
            match = next((e for e in remaining if _family(e).endswith(family)), None)
            if match:
                remaining.remove(match)
                found[match] = f"as '{part}'"
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
        "missed": [p for p in remaining],
        "missed_visible": [p for p in remaining if p not in hidden],
        "wrong_side": wrong_side,
        "extra": extra,
    }


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


def run_case(case: dict, cases_dir: Path) -> dict:
    rid = load_case(cases_dir / case["folder"])
    calls.clear()
    answered_by.clear()
    out: dict = {"case": case["name"], "report_id": rid}
    started = time.monotonic()
    try:
        # The company keys, as the benchmark always ran on.
        pipeline._sort(rid, AiActor.cli())
        out["sort_seconds"] = round(time.monotonic() - started, 1)
        started = time.monotonic()
        pipeline._read(rid, AiActor.cli())
        out["read_seconds"] = round(time.monotonic() - started, 1)
    except Exception as exc:  # recorded, and the next case still runs
        out["error"] = f"{type(exc).__name__}: {exc}"[:500]
    out["calls"] = Counter(c["purpose"] for c in calls)
    out["answered_by"] = Counter(answered_by)

    db = SessionLocal()
    try:
        report = db.get(Report, rid)
        session = db.get(IntakeSession, rid)
        photos = ordered_photos(db, rid)
        read_ids = set(session.read_photo_ids or [])
        data = report.data or {}
        out["photos"] = len(photos)
        out["read"] = len(read_ids)
        out["kinds"] = dict(Counter(p.kind or "unsorted" for p in photos))
        out["read_kinds"] = dict(Counter(p.kind or "unsorted" for p in photos if p.id in read_ids))
        damage_photos = [p for p in photos if p.shows_damage]
        out["damage_photos_read"] = f"{len([p for p in damage_photos if p.id in read_ids])}/{len(damage_photos)}"
        out["sorted_photos"] = [
            {"n": i + 1, "file": p.original_name, "kind": p.kind, "area": p.area, "quality": p.quality,
             "damage": p.shows_damage, "part": p.part, "read": p.id in read_ids, "note": p.note}
            for i, p in enumerate(photos)
        ]
        drafted = [item.get("part") for item in data.get("damage_summary") or []]
        out["drafted_parts"] = drafted
        out["parts"] = score_parts(case["parts"], drafted)
        # What a "parts to check" list would add: every part the sorter named
        # on a photo of the insured vehicle, damage visible or not -- surveyors
        # photograph close up the parts they go on to claim.
        hinted = sorted({p.part for p in photos if p.part} - set(drafted))
        out["checklist"] = hinted
        out["parts_with_checklist"] = score_parts(case["parts"], drafted + hinted)
        out["vehicle"] = score_vehicle(case["vehicle"], data.get("vehicle_info") or {})
    finally:
        db.close()
    return out


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True, type=Path)
    ap.add_argument("--key", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--only", action="append", help="case name; repeatable")
    args = ap.parse_args()

    init_db()
    key = json.loads(args.key.read_text(encoding="utf-8"))
    cases = [c for c in key["cases"] if not args.only or c["name"] in args.only]
    results = []
    for case in cases:
        print(f"== {case['name']}", flush=True)
        r = run_case(case, args.cases)
        results.append(r)
        print(summary_line(r), flush=True)
        if "parts" in r:
            print(f"   missed (visible): {r['parts']['missed_visible']}")
            print(f"   wrong side: {r['parts']['wrong_side']}  extra: {r['parts']['extra']}")
            print(f"   facts: { {k: (x['verdict'], x['got']) for k, x in r['vehicle'].items()} }", flush=True)
            pc = r["parts_with_checklist"]
            print(f"   + checklist {r['checklist']}")
            print(f"   with checklist: parts {pc['found']}/{pc['expected']} (visible {pc['visible_found']}/{pc['visible_expected']}) "
                  f"wrong-side {len(pc['wrong_side'])} extra {len(pc['extra'])} {pc['extra']}", flush=True)

    totals = Counter()
    for r in results:
        if "parts" in r:
            for k in ("found", "expected", "visible_found", "visible_expected"):
                totals[k] += r["parts"][k]
                totals[f"checklist_{k}"] += r["parts_with_checklist"][k]
            totals["extra"] += len(r["parts"]["extra"])
            totals["checklist_extra"] += len(r["parts_with_checklist"]["extra"])
    print(f"\nTOTAL parts {totals['found']}/{totals['expected']}, visible {totals['visible_found']}/{totals['visible_expected']}, "
          f"extra {totals['extra']}")
    print(f"WITH CHECKLIST parts {totals['checklist_found']}/{totals['checklist_expected']}, "
          f"visible {totals['checklist_visible_found']}/{totals['checklist_visible_expected']}, extra {totals['checklist_extra']}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"results": results, "totals": totals}, indent=1, default=str), encoding="utf-8")
    print("written", args.out)


if __name__ == "__main__":
    main()
