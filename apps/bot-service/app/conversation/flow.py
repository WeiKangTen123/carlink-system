"""The one piece of conversation logic every channel adapter calls into.

Keeping this channel-agnostic is the point of the adapter architecture in
docs/proposal.md section D: Telegram and WhatsApp both normalize down to
"chat id, free text, list of local photo file paths" and share everything
from here on -- drafting, summarizing, and (in the channel adapters)
rendering and confirming.
"""
from dataclasses import dataclass

from app.ai.extraction import draft_report
from app.reports.schema import SecurityIncidentDraft


@dataclass
class DraftResult:
    draft: SecurityIncidentDraft
    summary_text: str


def build_draft(description: str, photo_paths: list[str]) -> DraftResult:
    draft = draft_report(description, photo_paths)
    return DraftResult(draft=draft, summary_text=summarize(draft))


def summarize(draft: SecurityIncidentDraft) -> str:
    lines = [
        "Here's the draft -- reply 'confirm' to generate the PDF, or tell me what to change.",
        "",
        f"Location: {draft.location or 'Not specified'}",
        f"Date/time: {draft.incident_datetime or 'Not specified'}",
        f"Category: {', '.join(draft.category) or 'Not specified'}",
    ]
    if draft.accident_type:
        lines.append(f"Accident Type: 🚗 {draft.accident_type}")
    if draft.damaged_parts:
        lines.append(f"Damaged Parts: 🛠️ {', '.join(draft.damaged_parts)}")
    if draft.severity_level:
        lines.append(f"Severity: ⚠️ {draft.severity_level}")
    if draft.vehicle_details:
        lines.append(f"Vehicle: 🚘 {draft.vehicle_details}")

    lines.append(f"Description: {draft.description}")

    if draft.people_involved:
        lines.append("People involved: " + "; ".join(p.name for p in draft.people_involved))
    if draft.witnesses:
        lines.append("Witnesses: " + "; ".join(w.name for w in draft.witnesses))
    if draft.immediate_actions:
        lines.append(f"Immediate actions: {draft.immediate_actions}")
    lines.append(f"Reported to authorities: {'Yes' if draft.reported_to_authorities else 'No'}")
    return "\n".join(lines)



def combined_description(session) -> str:
    parts = [session.description or "", *session.pending_edits]
    return "\n".join(p for p in parts if p)
