"""Regression test for the reporter-stated damaged side.

Why this exists: on a real close-up the model could name the panel but not
which side of the vehicle it was on, and every such part maps to no 3D
marker -- the blueprint came out blank. Left to infer the side it also
named the WRONG one. So the side comes from the reporter (template field),
is handed to the model as an established fact on every draft, and when it
is still unknown the Telegram summary asks for it.

    python3 scripts/test_damaged_side.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("STORAGE_DIR", tempfile.mkdtemp(prefix="side-test-"))
os.environ.setdefault("GEMINI_API_KEY", "unused")

import app.conversation.flow as flow  # noqa: E402
from app.conversation.flow import parse_template_reply, summarize  # noqa: E402
from app.reports.schema import DamageSummaryItem, SecurityIncidentDraft  # noqa: E402


def ok(msg: str) -> None:
    print(f"    ok   {msg}")


TEMPLATE_WITH_SIDE = """Name: Raj Kumar
Role/Position: Senior Adjuster
Contact Number: 90001111

Vehicle Plate: SDD5566M
Damaged Side (Left / Right / Front / Rear): Right
Location: Tampines Ave 5
Date/Time: 2026-09-13 08:00 UTC
Description: The rear panel was scraped against a wall.

Reported to Authorities (Yes/No): No
"""

TEMPLATE_WITHOUT_SIDE = TEMPLATE_WITH_SIDE.replace(
    "Damaged Side (Left / Right / Front / Rear): Right\n", ""
)

print("\n  template parsing")
parsed = parse_template_reply(TEMPLATE_WITH_SIDE)
assert parsed and parsed["damaged_side"] == "Right", parsed
assert parsed["vehicle_plate"] == "SDD5566M" and parsed["location"] == "Tampines Ave 5"
ok("side is read from the new field")

parsed = parse_template_reply(TEMPLATE_WITHOUT_SIDE)
assert parsed and parsed["damaged_side"] is None, parsed
ok("a template issued before the field existed still parses (side None)")

parsed = parse_template_reply(TEMPLATE_WITH_SIDE.replace(": Right\n", ": \n"))
assert parsed and parsed["damaged_side"] is None
ok("a blank side is None, not an empty string")

assert "Damaged Side" in flow.build_template_prompt()
ok("the issued template asks for it")


print("\n  side reaches the model as an established fact")


def session(**over):
    base = dict(
        description="scraped", pending_edits=[], incident_datetime="2026-09-13 08:00 UTC",
        location="Tampines Ave 5", vehicle_plate="SDD5566M", reporter_name="Raj Kumar",
        reporter_role="Senior Adjuster", reporter_contact="90001111",
        reported_to_authorities=False, damaged_side=None, photo_paths=[],
    )
    base.update(over)
    return types.SimpleNamespace(**base)


def fake_draft(description, photo_paths, known_facts=None):
    fake_draft.calls.append(known_facts)
    return SecurityIncidentDraft(description=description)


fake_draft.calls = []
with mock.patch.object(flow, "draft_report", fake_draft):
    flow.build_draft("scraped", [], session(damaged_side="Right"))
    facts = fake_draft.calls[-1]
    assert facts and any(v == "Right" and "side" in k for k, v in facts.items()), facts
    assert "location" not in facts
    ok("first draft: side passed, nothing else echoed back")

    flow.build_draft("scraped", [], session(damaged_side=None))
    assert fake_draft.calls[-1] is None
    ok("first draft without a side: no facts block at all (unchanged behaviour)")

    flow.build_draft("scraped\nthe time was 16:45", [], session(damaged_side="Right", pending_edits=["the time was 16:45"]))
    facts = fake_draft.calls[-1]
    assert facts["location"] == "Tampines Ave 5" and any(v == "Right" for v in facts.values())
    ok("redraft: side kept alongside the other established facts")


print("\n  summary asks for the side only when it is unknown")


def draft(parts):
    return SecurityIncidentDraft(
        description="x",
        damage_summary=[DamageSummaryItem(part=p, damage_type="Dent", severity="Minor") for p in parts],
    )


text = summarize(draft(["Door (side undetermined)", "Quarter Panel (side undetermined)", "Rear Bumper"]))
assert "Which side?* Door, Quarter Panel" in text, text
ok("unsided parts are named in a 'Which side?' prompt")

text = summarize(draft(["Right Rear Door", "Rear Bumper"]))
assert "Which side?" not in text
ok("no prompt when every part is placed")

print("\n  all damaged-side assertions passed\n")
