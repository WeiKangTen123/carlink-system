"""Import past report spreadsheets (CL-xxxxx .xls/.xlsx) into the parts
history, from the server's command line. Admins can also upload them in
the dashboard (Knowledge -> Parts price history).

    docker exec -it carlink_api python scripts/import_past_reports.py /path/to/folder [more folders or files]

A report imported again replaces its earlier lines.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parts.history import import_case, parse_report  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    files: list[Path] = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        files += sorted(f for f in p.rglob("*") if f.suffix.lower() in (".xls", ".xlsx") and not f.name.startswith("~$")) if p.is_dir() else [p]
    init_db()
    db = SessionLocal()
    ok = failed = 0
    try:
        for f in files:
            try:
                case = parse_report(f)
                n = import_case(db, case, f.name)
                db.commit()
                ok += 1
                print(f"  {case.ref:<10} {case.make} {case.model:<10} {n:>3} lines  ({f.name})")
            except Exception as exc:
                db.rollback()
                failed += 1
                print(f"  SKIPPED {f.name}: {exc}")
    finally:
        db.close()
    print(f"\n{ok} report(s) imported, {failed} skipped")
    return 0 if ok or not files else 1


if __name__ == "__main__":
    raise SystemExit(main())
