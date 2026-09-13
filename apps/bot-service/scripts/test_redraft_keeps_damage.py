"""Regression test: a redraft must not change the photo-derived findings
unless the reporter's message is about the damage.

Why: on a real filing a time-only correction ("16:45 not 08:00") produced a
second draft with one part instead of two, no photo reference, no bounding
box, no confidence, and a different severity -- and that second draft is
the one that got confirmed and stored. Reproduced 3 of 3 locally.

    python3 scripts/test_redraft_keeps_damage.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("STORAGE_DIR", tempfile.mkdtemp(prefix="redraft-test-"))
os.environ.setdefault("GEMINI_API_KEY", "unused")

import app.conversation.flow as flow  # noqa: E402
from app.conversation.flow import edit_mentions_damage  # noqa: E402
from app.reports.schema import DamageSummaryItem, SecurityIncidentDraft  # noqa: E402


def ok(msg: str) -> None:
    print(f"    ok   {msg}")


FIRST = SecurityIncidentDraft(
    description="first",
    severity_level="Moderate",
    damaged_parts=["Right Rear Door", "Right Rear Quarter Panel"],
    damage_summary=[
        DamageSummaryItem(part="Right Rear Door", damage_type="Puncture / Tear", severity="Moderate",
                          photo_reference="P01", bbox_2d=[248, 446, 963, 836], ai_confidence="High"),
        DamageSummaryItem(part="Right Rear Quarter Panel", damage_type="Scratch", severity="Minor",
                          photo_reference="P01", bbox_2d=[100, 100, 200, 200], ai_confidence="High"),
    ],
)


def thin_redraft(*_a, **_k):
    """What the model actually returned on the failing redrafts."""
    return SecurityIncidentDraft(
        description="second",
        severity_level="Severe",
        damaged_parts=["Right Rear Door"],
        damage_summary=[DamageSummaryItem(part="Right Rear Door", damage_type="Puncture / Tear", severity="Severe")],
    )


def session(**over):
    base = dict(
        description="scraped", pending_edits=[], incident_datetime="2026-09-13 08:00 UTC",
        location="Tampines Ave 5", vehicle_plate="SDD7788N", reporter_name="Raj Kumar",
        reporter_role="Senior Adjuster", reporter_contact="90001111",
        reported_to_authorities=False, damaged_side="Right", photo_paths=[], draft=FIRST,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


print("\n  edit classification")
assert not edit_mentions_damage("correction: the time was 16:45 not 08:00")
assert not edit_mentions_damage("actually it was Jurong West")
assert not edit_mentions_damage("my number is 91234567")
ok("time / location / contact corrections are not about damage")
assert edit_mentions_damage("the quarter panel is not damaged, only the door")
assert edit_mentions_damage("it's the left door not the right")
assert edit_mentions_damage("also the wing mirror is broken")
assert edit_mentions_damage("severity should be minor")
ok("part names, sides, damage words and severities are")


print("\n  time-only redraft keeps the findings")
captured = {}


def capture(description, photo_paths, known_facts=None):
    captured["facts"] = known_facts
    return thin_redraft()


with mock.patch.object(flow, "draft_report", capture):
    s = session(pending_edits=["correction: the time was 16:45 not 08:00"])
    d = flow.build_draft("scraped\ncorrection: the time was 16:45 not 08:00", [], s).draft
    assert [i.part for i in d.damage_summary] == ["Right Rear Door", "Right Rear Quarter Panel"], d.damage_summary
    assert d.damage_summary[0].photo_reference == "P01" and d.damage_summary[0].bbox_2d and d.damage_summary[0].ai_confidence == "High"
    assert d.damaged_parts == ["Right Rear Door", "Right Rear Quarter Panel"]
    assert d.severity_level == "Moderate"
    ok("both parts, their metadata and the severity survive")
    assert d.description == "second"
    ok("everything else still comes from the new draft")
    facts = captured["facts"]
    assert "Right Rear Quarter Panel" in facts["damage already identified from the photos"], facts
    assert facts["overall severity"] == "Moderate"
    ok("previous findings were also handed to the model as established facts")

    print("\n  damage-related redraft lets the model revise, but fills what it left blank")
    s = session(pending_edits=["the quarter panel is not damaged, only the door"])
    d = flow.build_draft("scraped\nthe quarter panel is not damaged, only the door", [], s).draft
    assert [i.part for i in d.damage_summary] == ["Right Rear Door"]
    ok("the model's revised list stands (quarter panel dropped)")
    item = d.damage_summary[0]
    assert item.photo_reference == "P01" and item.bbox_2d == [248, 446, 963, 836] and item.ai_confidence == "High"
    assert item.severity == "Severe"
    ok("blank metadata backfilled from the matching previous item; stated severity kept")

    print("\n  first draft is untouched")
    s = session(pending_edits=[], draft=None)
    d = flow.build_draft("scraped", [], s).draft
    assert [i.part for i in d.damage_summary] == ["Right Rear Door"] and d.severity_level == "Severe"
    assert "damage already identified from the photos" not in (captured["facts"] or {})
    ok("no carry-over and no findings fact on a first draft")

print("\n  all redraft assertions passed\n")
