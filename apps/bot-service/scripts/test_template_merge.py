"""Regression test for merging template answers into the AI draft.

This behaviour has broken twice in opposite directions, so it is pinned:

  1. Applying the template unconditionally discarded genuine corrections
     sent as later chat messages -- the AI extracted "the plate is actually
     ABC1234" correctly and the merge immediately reverted it.
  2. Fixing that by pinning only on the first draft then dropped EVERY
     template field on any redraft, because a redraft comes from an AI that
     has never seen the reporter's name, role or phone number. Confirmed on
     a real filed report: name, role, contact, location and date/time all
     stored empty, and the typed plate replaced by one read off the photo.

Both directions are asserted below. Run after touching build_draft():

    python3 scripts/test_template_merge.py
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.conversation.flow as flow  # noqa: E402
from app.reports.schema import SecurityIncidentDraft, VehicleInfo  # noqa: E402

FAILURES: list[str] = []


def _session(**overrides):
    class S:
        pass

    s = S()
    s.location = "Bedok"
    s.incident_datetime = "2026-09-13 05:02 UTC"
    s.reported_to_authorities = False
    s.reporter_name = "EK"
    s.reporter_role = "Ah Gou/ Car Surveyor 1"
    s.reporter_contact = "81238123"
    s.vehicle_plate = "SBD1234C"
    s.pending_edits = []
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _ai(**fields):
    """Stands in for draft_report. Defaults mimic the normal case: the model
    returns nothing for reporter identity, because it cannot see a name or a
    phone number in a photo."""
    def _call(description, photo_paths):
        base = dict(
            description="Rear-end collision.",
            category=["Vehicle Collision or Damage"],
            people_involved=[],
            witnesses=[],
            reported_to_authorities=False,
        )
        base.update(fields)
        return SecurityIncidentDraft(**base)

    return _call


def check(label, got, want):
    if got != want:
        FAILURES.append(f"{label}: expected {want!r}, got {got!r}")
        print(f"    FAIL {label}  got={got!r} want={want!r}")
    else:
        print(f"    ok   {label}")


def main() -> int:
    print("  first draft -- the typed template is stated ground truth")
    with mock.patch.object(flow, "draft_report", _ai(vehicle_info=VehicleInfo(plate_number="SLJ7948"))):
        d = flow.build_draft("x", [], _session()).draft
        check("reporter_name applied", d.reporter_name, "EK")
        check("reporter_contact applied", d.reporter_contact, "81238123")
        check("location applied", d.location, "Bedok")
        check("typed plate beats the AI's photo reading", d.vehicle_info.plate_number, "SBD1234C")

    print("\n  redraft, AI returns nothing new -- nothing may be lost")
    with mock.patch.object(flow, "draft_report", _ai()):
        d = flow.build_draft("x", [], _session(pending_edits=["the plate is actually ABC1234"])).draft
        check("reporter_name survives", d.reporter_name, "EK")
        check("reporter_role survives", d.reporter_role, "Ah Gou/ Car Surveyor 1")
        check("reporter_contact survives", d.reporter_contact, "81238123")
        check("location survives", d.location, "Bedok")
        check("incident_datetime survives", d.incident_datetime, "2026-09-13 05:02 UTC")

    print("\n  redraft with a real correction -- the correction must win")
    with mock.patch.object(
        flow, "draft_report", _ai(vehicle_info=VehicleInfo(plate_number="ABC1234"), location="Tampines")
    ):
        d = flow.build_draft("x", [], _session(pending_edits=["plate is ABC1234, it was Tampines"])).draft
        check("corrected plate wins over the stale template", d.vehicle_info.plate_number, "ABC1234")
        check("corrected location wins over the stale template", d.location, "Tampines")
        check("reporter identity preserved alongside", d.reporter_name, "EK")

    print("\n  reporter corrects their own name in chat")
    with mock.patch.object(flow, "draft_report", _ai(reporter_name="Ahmad")):
        d = flow.build_draft("x", [], _session(pending_edits=["actually my name is Ahmad"])).draft
        check("AI-extracted name correction wins", d.reporter_name, "Ahmad")

    print()
    if FAILURES:
        print(f"  {len(FAILURES)} FAILED")
        for f in FAILURES:
            print(f"    - {f}")
        return 1
    print("  all template-merge assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
