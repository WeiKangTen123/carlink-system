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

from app.ai.keys import AiActor  # noqa: E402
import app.conversation.flow as flow  # noqa: E402
from app.reports.schema import SecurityIncidentDraft, VehicleInfo  # noqa: E402

# build_draft needs to know who pays; the fakes below never look.
ACTOR = AiActor.cli()

FAILURES: list[str] = []


def _session(**overrides):
    class S:
        pass

    s = S()
    s.location = "Bedok"
    s.incident_datetime = "2026-09-13 05:02 UTC"
    # The template's printed facts (2026-11-04): who asked for the report,
    # the type of claim and the workshop. The AI never sees these in a photo.
    s.requested_by = "Tokio Marine Insurance"
    s.claim_type = "Own damage"
    s.workshop_name = "Precise Auto Service"
    s.vehicle_plate = "SBD1234C"
    s.pending_edits = []
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _ai(**fields):
    """Stands in for draft_report. Defaults mimic the normal case: the model
    returns nothing for reporter identity, because it cannot see a name or a
    phone number in a photo."""
    def _call(description, photo_paths, known_facts=None, **_kw):
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
        d = flow.build_draft("x", [], _session(), actor=ACTOR).draft
        check("requested_by applied (cover page)", d.cover_page.requested_by, "Tokio Marine Insurance")
        check("claim_type applied", d.insurance_details.claim_type, "Own damage")
        check("workshop applied", d.insurance_details.workshop_assigned, "Precise Auto Service")
        check("location applied", d.location, "Bedok")
        check("typed plate beats the AI's photo reading", d.vehicle_info.plate_number, "SBD1234C")

    print("\n  redraft, AI returns nothing new -- nothing may be lost")
    with mock.patch.object(flow, "draft_report", _ai()):
        d = flow.build_draft("x", [], _session(pending_edits=["the plate is actually ABC1234"]), actor=ACTOR).draft
        check("requested_by survives", d.cover_page.requested_by, "Tokio Marine Insurance")
        check("claim_type survives", d.insurance_details.claim_type, "Own damage")
        check("workshop survives", d.insurance_details.workshop_assigned, "Precise Auto Service")
        check("location survives", d.location, "Bedok")
        check("incident_datetime survives", d.incident_datetime, "2026-09-13 05:02 UTC")

    print("\n  redraft with a real correction -- the correction must win")
    with mock.patch.object(
        flow, "draft_report", _ai(vehicle_info=VehicleInfo(plate_number="ABC1234"), location="Tampines")
    ):
        d = flow.build_draft("x", [], _session(pending_edits=["plate is ABC1234, it was Tampines"]), actor=ACTOR).draft
        check("corrected plate wins over the stale template", d.vehicle_info.plate_number, "ABC1234")
        check("corrected location wins over the stale template", d.location, "Tampines")
        check("requested by preserved alongside", d.cover_page.requested_by, "Tokio Marine Insurance")

    print("\n  reporter corrects the type of claim in chat")
    with mock.patch.object(flow, "draft_report", _ai(insurance_details={"claim_type": "Third party"})):
        d = flow.build_draft("x", [], _session(pending_edits=["it is a third party claim"]), actor=ACTOR).draft
        check("AI-extracted claim type correction wins", d.insurance_details.claim_type, "Third party")

    print("\n  the template and its summary")
    prompt = flow.build_template_prompt()
    for asked in ("Vehicle Plate:", "Requested by", "Type of claim", "Workshop:", "Date/time of accident:", "Damaged Side", "What happened:"):
        check(f"the template asks {asked!r}", asked in prompt, True)
    for dropped in ("Name:", "Role", "Contact", "Reported to Authorities", "Location:", "Insurer"):
        check(f"the template no longer asks {dropped!r}", dropped in prompt, False)
    parsed = flow.parse_template_reply(
        "Vehicle Plate: SMV7530X\nRequested by (insurer / company): Tokio Marine\nType of claim (Own damage / Third party): Own damage\n"
        "Workshop: Precise Auto\nDate/time of accident: 27 Feb 2023 10.15am\nDamaged Side (Left / Right / Front / Rear): Rear\n"
        "What happened: Rear-ended at a junction.\nSecond line of the story."
    )
    check("parsed: plate", parsed["vehicle_plate"], "SMV7530X")
    check("parsed: requested by", parsed["requested_by"], "Tokio Marine")
    check("parsed: type of claim", parsed["claim_type"], "Own damage")
    check("parsed: workshop", parsed["workshop_name"], "Precise Auto")
    check("parsed: date/time of accident", parsed["incident_datetime"], "27 Feb 2023 10.15am")
    check("parsed: side", parsed["damaged_side"], "Rear")
    check("parsed: what happened (continued lines)", parsed["description"], "Rear-ended at a junction. Second line of the story.")
    check("parsed: no reporter keys", any(k.startswith("reporter") or k == "reported_to_authorities" for k in parsed), False)
    text = flow.summarize(d)
    check("summary has no 'Police report' line", "Police report" in text, False)
    check("summary shows who asked for the report", "Requested by:* Tokio Marine Insurance" in text, True)
    check("summary shows the type of claim", "Claim Type:* Third party" in text, True)

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
