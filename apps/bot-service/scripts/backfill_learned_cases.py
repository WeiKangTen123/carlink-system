"""Puts the final Annex A of the cases signed off before sign-off did it
itself into the parts history (app/parts/learn.py, contract 8 L), and takes
out the lines of cases no longer signed off.

    docker exec carlink_api python scripts/backfill_learned_cases.py            # dry run: what it would do
    docker exec carlink_api python scripts/backfill_learned_cases.py --apply    # writes the lines

Each signed-off case whose make and model are known becomes the past case
"carlink:<report id>", replacing what was learned of it before: the same
fields the spreadsheet importer stores, and no plate. Nothing is learned
while settings.learn_from_signed_off is off. A dry run writes nothing at
all. Prints case ids, make and model and line counts only. No Gemini call.
Safe to run again.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.parts.learn import forget, learn_case, learned_cases, past_case_from, signed_off_as  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import Report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv
    if not settings.learn_from_signed_off:
        print("Learning from signed-off cases is off (LEARN_FROM_SIGNED_OFF=false): nothing to do.")
        return 0
    if apply:
        init_db()
    db = SessionLocal()
    try:
        signed = [rid for (rid,) in db.query(Report.id).filter(Report.status == "Signed Off").order_by(Report.created_at, Report.id)]
        learned = learned_cases(db)
        stored = cases = not_known = 0
        for report_id in signed:
            report = db.get(Report, report_id)
            if not signed_off_as(report, None):
                continue
            case = past_case_from(report)
            if case is None:
                not_known += 1
                continue
            if apply:
                try:
                    count = learn_case(db, report_id)
                except Exception as exc:  # one odd case mustn't stop the rest
                    db.rollback()
                    print(f"  SKIPPED {report_id}: {type(exc).__name__}: {str(exc)[:120]}")
                    continue
                if count is None:
                    continue
            else:
                count = len(case.lines)
            cases += 1
            stored += count
            again = " (again)" if report_id in learned else ""
            print(f"  {report_id}  {case.make} {case.model}  {count} line(s){again}")
        gone = sorted(learned - set(signed))
        if gone and apply:
            for report_id in gone:
                forget(db, report_id)
            db.commit()
        if not apply:
            db.rollback()
    finally:
        db.close()
    verb = "learned" if apply else "would be learned"
    print(f"\n{len(signed)} signed-off case(s): {cases} {verb} ({stored} lines), "
          f"{not_known} without a make and model or a priced Annex A line; "
          f"{len(gone)} case(s) no longer signed off {'taken out' if apply else 'to take out'}")
    if not apply:
        print("Dry run: nothing written. Run again with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
