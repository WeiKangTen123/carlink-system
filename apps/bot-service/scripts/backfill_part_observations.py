"""Writes the part observations (step 2B, app/reports/observations.py) of the
cases signed off before sign-off did it itself, makes again those written
under older definitions (a row's version below observations.VERSION: the
scrubber, the stamp or a field changed since), and takes out the rows of
cases no longer signed off.

    docker exec carlink_api python scripts/backfill_part_observations.py            # dry run: counts only
    docker exec carlink_api python scripts/backfill_part_observations.py --apply    # writes the rows

Each signed-off case gets its rows exactly as a sign-off writes them (a
case's rows of before are replaced, whatever their version; the knowledge
stamp from the case's own AI runs, never data.knowledge as stored), so
running it again changes nothing.
A dry run writes nothing at all. Prints counts only -- never a case id,
Our Ref, make, plate or name. No Gemini call.
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect  # noqa: E402

from app.parts.learn import signed_off_as  # noqa: E402
from app.reports.db import SessionLocal, engine, init_db  # noqa: E402
from app.reports.models import PartObservation, Report  # noqa: E402
from app.reports.observations import VERSION, capture, captured_cases, case_rows, forget  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv
    if apply:
        init_db()
    # A dry run on a database from before this release: no table yet.
    table = PartObservation.__tablename__ in inspect(engine).get_table_names()
    db = SessionLocal()
    stats: Counter = Counter()
    outcomes: Counter = Counter()
    try:
        signed = [rid for (rid,) in db.query(Report.id).filter(Report.status == "Signed Off").order_by(Report.created_at, Report.id)]
        had = captured_cases(db) if table else set()
        older = (db.query(PartObservation).filter(PartObservation.version < VERSION).count() if table else 0)
        for report_id in signed:
            report = db.get(Report, report_id)
            if not signed_off_as(report, None):
                continue
            stats["cases"] += 1
            stats["again" if report_id in had else "new"] += 1
            try:
                rows = case_rows(db, report)
                if apply:
                    count = capture(db, report_id)
                    if count is None:
                        stats["changed meanwhile"] += 1
                        continue
            except Exception as exc:  # one odd case mustn't stop the rest
                db.rollback()
                stats["skipped"] += 1
                print(f"  skipped a case: {type(exc).__name__}")
                continue
            stats["rows"] += len(rows)
            outcomes.update(r["outcome"] for r in rows)
        gone = sorted(had - set(signed))
        if gone and apply:
            for report_id in gone:
                forget(db, report_id)
            db.commit()
        if not apply:
            db.rollback()
        total = db.query(PartObservation).count() if table else 0
    finally:
        db.close()
    verb = "written" if apply else "would be written"
    print(f"{len(signed)} signed-off case(s): {stats['cases']} {verb} ({stats['new']} new, {stats['again']} again), "
          f"{stats['rows']} row(s)")
    if outcomes:
        print("  by outcome: " + ", ".join(f"{k} {outcomes[k]}" for k in sorted(outcomes)))
    if stats["skipped"] or stats["changed meanwhile"]:
        print(f"  {stats['skipped']} skipped, {stats['changed meanwhile']} changed while running (run again)")
    print(f"{older} row(s) from older definitions (version < {VERSION}): "
          f"{'made again' if apply else 'to make again'} with their cases")
    print(f"{len(gone)} case(s) no longer signed off: rows {'taken out' if apply else 'to take out'}")
    print(f"part_observations now holds {total} row(s)")
    if not apply:
        print("Dry run: nothing written. Run again with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
