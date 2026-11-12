"""Records the AI accuracy (app/reports/accuracy.py, contract 8 L) of the
cases signed off before sign-off recorded it itself.

    docker exec carlink_api python scripts/backfill_ai_accuracy.py            # dry run: what it would record
    docker exec carlink_api python scripts/backfill_ai_accuracy.py --apply    # writes the rows

Only signed-off cases with the AI's own draft (Report.ai_draft): a case
filed before the draft was kept (2026-11-04) has nothing to compare. A case
whose row is of today's metric version (accuracy.VERSION) is left as it is
unless --redo; rows of cases no longer signed off, or deleted, are dropped.
A dry run writes nothing at all. Prints case ids and counts only, never
case data. No Gemini call. Safe to run again.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect  # noqa: E402

from app.parts.learn import signed_off_as  # noqa: E402
from app.reports import accuracy  # noqa: E402
from app.reports.db import SessionLocal, engine, init_db  # noqa: E402
from app.reports.models import AiAccuracy, Report  # noqa: E402


def _line(report_id: str, metrics: dict) -> str:
    d, a = metrics["damage"], metrics["annex_a"]
    annex = f"lines kept {a['kept']}/{a['machine']}, price changed {a['price_changed']}/{a['priced_kept']}" if a.get("machine") is not None else "lines not compared"
    return (f"  {report_id}  parts: AI found {d['ai_found']}, kept {d['kept']}, removed {d['removed']}, "
            f"added {d['added']}, wrong side {d['side_changed']}; {annex}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    apply, redo = "--apply" in argv, "--redo" in argv
    if apply:
        init_db()  # makes the ai_accuracy table on a server not restarted since the deploy
    elif "ai_accuracy" not in inspect(engine).get_table_names():
        print("(the ai_accuracy table isn't there yet: the api makes it at start-up, --apply here too)")
    db = SessionLocal()
    try:
        have = {}
        if "ai_accuracy" in inspect(engine).get_table_names():
            have = {rid: version for rid, version in db.query(AiAccuracy.report_id, AiAccuracy.version)}
        signed = [rid for (rid,) in db.query(Report.id).filter(Report.status == "Signed Off").order_by(Report.created_at, Report.id)]
        measured = no_draft = current = skipped = 0
        for report_id in signed:
            report = db.get(Report, report_id)
            if not signed_off_as(report, None):
                skipped += 1
                continue
            if not isinstance(report.ai_draft, dict) or not report.ai_draft:
                no_draft += 1
                continue
            if have.get(report_id) == accuracy.VERSION and not redo:
                current += 1
                continue
            try:
                metrics = accuracy.record(db, report_id) if apply else accuracy.measure(db, report)
            except Exception as exc:  # one odd case mustn't stop the rest
                db.rollback()
                print(f"  SKIPPED {report_id}: {type(exc).__name__}: {str(exc)[:120]}")
                skipped += 1
                continue
            finally:
                if not apply:
                    db.rollback()
            if metrics is None:
                skipped += 1
                continue
            measured += 1
            print(_line(report_id, metrics))
        gone = sorted(set(have) - set(signed))
        if gone and apply:
            db.query(AiAccuracy).filter(AiAccuracy.report_id.in_(gone)).delete(synchronize_session=False)
            db.commit()
    finally:
        db.close()
    verb = "recorded" if apply else "would be recorded"
    print(f"\n{len(signed)} signed-off case(s): {measured} {verb}, {current} already recorded, "
          f"{no_draft} without an AI draft, {skipped} skipped; "
          f"{len(gone)} row(s) of cases no longer signed off {'dropped' if apply else 'to drop'}")
    if not apply:
        print("Dry run: nothing written. Run again with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
