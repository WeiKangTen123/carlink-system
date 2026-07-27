"""The one piece of conversation logic every channel adapter calls into.

Keeping this channel-agnostic is the point of the adapter architecture in
docs/proposal.md section D: Telegram and WhatsApp both normalize down to
"chat id, free text, list of local photo file paths" and share everything
from here on -- drafting, summarizing, and (in the channel adapters)
rendering and confirming.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.ai.extraction import draft_report
from app.reports.schema import SecurityIncidentDraft, VehicleInfo


@dataclass
class DraftResult:
    draft: SecurityIncidentDraft
    summary_text: str


def build_draft(description: str, photo_paths: list[str], session=None) -> DraftResult:
    draft = draft_report(description, photo_paths)
    if session is not None:
        # User-stated ground truth (from the template) always wins over
        # anything the AI guessed for these fields -- category, damaged
        # parts, and severity stay AI-derived, never overridden here.
        if session.location:
            draft.location = session.location
        if session.incident_datetime:
            draft.incident_datetime = session.incident_datetime
        draft.reported_to_authorities = session.reported_to_authorities
        if session.reporter_name:
            draft.reporter_name = session.reporter_name
        if session.reporter_role:
            draft.reporter_role = session.reporter_role
        if session.reporter_contact:
            draft.reporter_contact = session.reporter_contact
        if session.vehicle_plate:
            if draft.vehicle_info is None:
                draft.vehicle_info = VehicleInfo()
            draft.vehicle_info.plate_number = session.vehicle_plate
    return DraftResult(draft=draft, summary_text=summarize(draft))


TEMPLATE_PROMPT = (
    "📋 Please fill in these details and send them back (edit only what you need "
    "to -- Category, Damaged Parts, and Severity are worked out automatically "
    "from your photos, no need to fill those in):\n\n"
    "Name: \n"
    "Role/Position: \n"
    "Contact Number: \n"
    "Vehicle Plate: \n"
    "Location: \n"
    "Date/Time: {now}\n"
    "Description: \n"
    "Reported to Authorities (Yes/No): No"
)


def build_template_prompt() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return TEMPLATE_PROMPT.format(now=now)


_TEMPLATE_REPLY_RE = re.compile(
    r"name\s*:\s*(?P<name>.*?)\s*"
    r"role\s*/?\s*position\s*:\s*(?P<role>.*?)\s*"
    r"contact\s*number\s*:\s*(?P<contact>.*?)\s*"
    r"vehicle\s*plate\s*:\s*(?P<plate>.*?)\s*"
    r"location\s*:\s*(?P<location>.*?)\s*"
    r"date\s*/?\s*time\s*:\s*(?P<datetime>.*?)\s*"
    r"description\s*:\s*(?P<description>.*?)\s*"
    r"reported\s*to\s*authorities[^:]*:\s*(?P<reported>.*)",
    re.IGNORECASE | re.DOTALL,
)


def parse_template_reply(text: str) -> Optional[dict]:
    """Pulls the 8 reporter-filled fields out of a reply to build_template_prompt().
    Returns None (rather than a best-effort partial parse) if the reply doesn't
    match the template shape at all, so the caller can fall back to treating
    the whole message as a free-text description -- same as before this
    template step existed -- instead of silently dropping content.
    """
    match = _TEMPLATE_REPLY_RE.search(text)
    if not match:
        return None
    reported_raw = match.group("reported").strip().splitlines()[0].strip().lower()
    return {
        "reporter_name": match.group("name").strip() or None,
        "reporter_role": match.group("role").strip() or None,
        "reporter_contact": match.group("contact").strip() or None,
        "vehicle_plate": match.group("plate").strip() or None,
        "location": match.group("location").strip() or None,
        "incident_datetime": match.group("datetime").strip() or None,
        "description": match.group("description").strip(),
        "reported_to_authorities": reported_raw in {"yes", "y", "true"},
    }


def summarize(draft: SecurityIncidentDraft) -> str:
    lines = [
        "Here's the draft -- reply 'confirm' to generate the PDF, or tell me what to change.",
        "",
    ]
    if draft.reporter_name or draft.reporter_role or draft.reporter_contact:
        reporter_line = draft.reporter_name or "Not specified"
        if draft.reporter_role:
            reporter_line += f" ({draft.reporter_role})"
        if draft.reporter_contact:
            reporter_line += f" -- {draft.reporter_contact}"
        lines.append(f"Reporter: 👤 {reporter_line}")
    if draft.vehicle_info and draft.vehicle_info.plate_number:
        lines.append(f"Vehicle Plate: 🚘 {draft.vehicle_info.plate_number}")
    lines.extend([
        f"Location: {draft.location or 'Not specified'}",
        f"Date/time: {draft.incident_datetime or 'Not specified'}",
        f"Category: {', '.join(draft.category) or 'Not specified'}",
    ])
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
