"""scripts/scrub_invented_values.py without real case values in it.

The benchmark case's real values the old code copied into other cases (its
odometer, its workshop, the surveyor's name) come from the command line now,
never from the file: with none given those checks are skipped and the
made-up values the old code wrote are still cleared; with them given they
clear on every other car, never on the benchmark car itself, and never on a
signed-off report. All values here are made up.

    python scripts/test_scrub_invented_values.py

Offline; a temporary SQLite database.
"""
import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="scrub-invented-test-")
os.environ.setdefault("STORAGE_DIR", _tmp)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'test.db').as_posix()}")
os.environ.setdefault("GEMINI_API_KEY", "fake")

import scrub_invented_values as scrub_mod  # noqa: E402

from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import Report  # noqa: E402

PLATE = "TST 1001 A"
ODOMETER = "011,111 km"
WORKSHOP = "M/S XYZ Auto Service, 1 Test Ave, Singapore 000200"
SIGNER = "Alex Tan"
ARGS = ["--benchmark-plate", PLATE, "--benchmark-odometer", ODOMETER, "--copied-workshop", WORKSHOP, "--copied-signer", SIGNER]


def ok(label: str) -> None:
    print(f"    ok   {label}")


def case(plate: str, status: str = "Draft") -> dict:
    return {
        "vehicle_info": {"plate_number": plate, "odometer_reading": ODOMETER},
        "insurance_details": {"workshop_assigned": WORKSHOP, "adjuster_assigned": f"{SIGNER} (Carlink Consultancy)"},
        "sign_off": {"status": status, "reviewed_by": SIGNER, "approved_by": SIGNER},
    }


def main() -> int:
    print("options")
    apply, copied = scrub_mod.parse_args([])
    assert apply is False and copied == scrub_mod.CopiedValues() and len(copied.skipped()) == 3, copied
    apply, copied = scrub_mod.parse_args(["--apply", *ARGS])
    assert apply is True and copied.benchmark_plate == "TST1001A" and copied.benchmark_odometer == ODOMETER
    assert copied.workshops == {WORKSHOP} and copied.signers == {SIGNER} and copied.skipped() == []
    for half in (["--benchmark-plate", PLATE], ["--benchmark-odometer", ODOMETER]):
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                scrub_mod.parse_args(half)
            except SystemExit as exc:
                assert exc.code == 2
            else:
                raise AssertionError(f"{half} alone was accepted")
    ok("no defaults; --apply as before; plate spaces dropped; plate and odometer only together")

    print("without the values: those checks are skipped")
    data = case("TST2002B")
    assert scrub_mod.scrub(data) == [], data
    assert data == case("TST2002B")
    made_up = {"vehicle_info": {"plate_number": "TST2002B", "odometer_reading": "185,742 km"},
               "insurance_details": {"workshop_assigned": "Sin Ming Autocare (Cluster A)"},
               "police_report": {"officer_name": "Insp. Marcus Tan"}}
    changes = scrub_mod.scrub(made_up)
    assert made_up["vehicle_info"]["odometer_reading"] is None and made_up["insurance_details"]["workshop_assigned"] is None
    assert made_up["police_report"]["officer_name"] is None and len(changes) == 3, changes
    ok("the copied values stay; the made-up odometer, workshop and officer are still cleared")

    print("with the values")
    _, copied = scrub_mod.parse_args(ARGS)
    other = case("TST 2002 B")
    changes = scrub_mod.scrub(other, copied)
    assert other["vehicle_info"]["odometer_reading"] is None, other
    assert other["insurance_details"] == {"workshop_assigned": None, "adjuster_assigned": None}, other
    assert other["sign_off"]["reviewed_by"] is None and other["sign_off"]["approved_by"] is None, other
    assert len(changes) == 5, changes
    own = case(PLATE)
    scrub_mod.scrub(own, copied)
    assert own["vehicle_info"]["odometer_reading"] == ODOMETER, own
    signed = case("TST2002B", status="Signed Off")
    scrub_mod.scrub(signed, copied)
    assert signed["sign_off"]["reviewed_by"] == SIGNER and signed["sign_off"]["approved_by"] == SIGNER, signed
    ok("cleared on another car; the benchmark car keeps its own odometer; a signed-off sign-off is left")

    print("a run over the database")
    init_db()
    db = SessionLocal()
    db.add(Report(id="scrub-draft", channel="manual", reporter_chat_id="test", status="confirmed", data=case("TST2002B")))
    db.add(Report(id="scrub-signed", channel="manual", reporter_chat_id="test", status="Signed Off",
                  data=case("TST2002B", status="Signed Off")))
    db.commit()
    db.close()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert scrub_mod.main([]) == 0
    assert out.getvalue().count("skipped (no value given)") == 3 and "0 report(s) would be scrubbed" in out.getvalue(), out.getvalue()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert scrub_mod.main(["--apply", *ARGS]) == 0
    assert "skipped" not in out.getvalue() and "1 report(s) scrubbed" in out.getvalue(), out.getvalue()
    assert "SIGNED (left as is, review by hand) scrub-signed" in out.getvalue(), out.getvalue()
    db = SessionLocal()
    draft, signed_row = db.get(Report, "scrub-draft"), db.get(Report, "scrub-signed")
    assert draft.data["vehicle_info"]["odometer_reading"] is None and draft.data["insurance_details"]["workshop_assigned"] is None
    assert signed_row.data == case("TST2002B", status="Signed Off")
    db.close()
    ok("dry run without values changes nothing; --apply with them scrubs the draft and leaves the signed-off report")

    print("\nALL SCRUB SCRIPT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
