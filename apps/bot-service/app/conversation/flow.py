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
    # Merging the reporter's typed template answers with the AI's draft.
    #
    # Two failure modes have to be avoided at once, and each earlier attempt
    # traded one for the other:
    #
    #  1. Overriding unconditionally meant a genuine correction sent as a
    #     later chat message ("the plate is actually ABC1234") was extracted
    #     correctly by the AI and then immediately discarded, silently
    #     reverting to the stale first-draft value.
    #  2. Pinning only on the first draft (the fix for 1) meant ANY follow-up
    #     message dropped every template field, because the redraft comes
    #     from an AI that has never seen the reporter's name, role or phone
    #     number -- those exist only in the template. Confirmed on a real
    #     filed report: name, role, contact, location and date/time were all
    #     stored empty, and the typed plate was replaced by one the model
    #     read off the photo.
    #
    # One rule covers both: the template wins outright on the FIRST draft,
    # where it is freshly-stated ground truth; on any redraft it only fills
    # what the AI left empty, so a real correction still wins while nothing
    # the AI cannot know gets silently lost.
    if session is not None:
        first_draft = not session.pending_edits

        def keep(field: str, value) -> None:
            if not value:
                return
            if first_draft or not getattr(draft, field, None):
                setattr(draft, field, value)

        keep("location", session.location)
        keep("incident_datetime", session.incident_datetime)
        # The AI can never see these in a photo, so "the AI left it empty"
        # is their normal state on every redraft -- which is exactly why
        # they were the fields that vanished.
        keep("reporter_name", session.reporter_name)
        keep("reporter_role", session.reporter_role)
        keep("reporter_contact", session.reporter_contact)

        # A bool has no empty state to fall back on, so it is pinned on the
        # first draft only. Later changes still reach the model, because
        # combined_description() feeds every edit back in on each redraft.
        if first_draft:
            draft.reported_to_authorities = session.reported_to_authorities

        if session.vehicle_plate:
            if draft.vehicle_info is None:
                draft.vehicle_info = VehicleInfo()
            # The person standing at the vehicle knows its plate better than
            # a model reading a possibly-obscured or adjacent car, so the
            # typed value wins on the first draft.
            if first_draft or not draft.vehicle_info.plate_number:
                draft.vehicle_info.plate_number = session.vehicle_plate

    return DraftResult(draft=draft, summary_text=summarize(draft))


# *bold* is rendered by both channels without extra work: Telegram needs
# parse_mode="Markdown" on the reply_text() call that sends this (see
# telegram.py's handle_photo), while WhatsApp/Twilio interprets *bold* and
# _italic_ in the message body natively, no equivalent flag needed.
TEMPLATE_PROMPT = (
    "📋 *Incident Report — Reporter Details*\n\n"
    "Please fill in the fields below and send them back. I'll work out *Category*, "
    "*Damaged Parts*, and *Severity* automatically from your photos -- no need to "
    "fill those in.\n\n"
    "👤 *Reporter Information*\n"
    "Name: \n"
    "Role/Position: \n"
    "Contact Number: \n\n"
    "🚘 *Vehicle & Incident*\n"
    "Vehicle Plate: \n"
    "Location: \n"
    "Date/Time: {now}\n"
    "Description: \n\n"
    "🚓 *Authority Report*\n"
    "Reported to Authorities (Yes/No): No"
)


def build_template_prompt() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return TEMPLATE_PROMPT.format(now=now)


# One pattern per field, matched line-by-line rather than as a single
# sequential blob -- the old version required all 8 labels to appear
# back-to-back with nothing in between, which broke the moment the
# template gained section headers ("👤 *Reporter Information*") between
# fields: the old regex's non-greedy .*? would silently swallow that
# header line into the PRECEDING field's captured value instead of
# skipping over it. This version tolerates any decorative/header lines
# interspersed between fields, and doesn't care what order the reporter
# actually filled them in.
_FIELD_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("name", re.compile(r"^\s*name\s*:\s*(.*)$", re.IGNORECASE)),
    ("role", re.compile(r"^\s*role\s*/?\s*position\s*:\s*(.*)$", re.IGNORECASE)),
    ("contact", re.compile(r"^\s*contact\s*number\s*:\s*(.*)$", re.IGNORECASE)),
    ("plate", re.compile(r"^\s*vehicle\s*plate\s*:\s*(.*)$", re.IGNORECASE)),
    ("location", re.compile(r"^\s*location\s*:\s*(.*)$", re.IGNORECASE)),
    ("datetime", re.compile(r"^\s*date\s*/?\s*time\s*:\s*(.*)$", re.IGNORECASE)),
    ("description", re.compile(r"^\s*description\s*:\s*(.*)$", re.IGNORECASE)),
    ("reported", re.compile(r"^\s*reported\s*to\s*authorities[^:]*:\s*(.*)$", re.IGNORECASE)),
]


def parse_template_reply(text: str) -> Optional[dict]:
    """Pulls the 8 reporter-filled fields out of a reply to build_template_prompt().
    Returns None (rather than a best-effort partial parse) if the reply doesn't
    contain all 8 field labels, so the caller can fall back to treating the
    whole message as a free-text description -- same bar the old sequential
    regex enforced, just checked per-line instead of as one ordered blob.
    """
    values: dict[str, list[str]] = {}
    current_field: Optional[str] = None
    for line in text.splitlines():
        if not line.strip():
            # A blank line always ends any in-progress multi-line
            # continuation -- the template itself uses a blank line to
            # separate sections, so without this a description that spans
            # to the end of its section would keep "continuing" straight
            # through the blank line and swallow the next section's own
            # header text (confirmed live: "🚓 *Authority Report*" ended up
            # appended onto the description before this check existed).
            current_field = None
            continue
        for key, pattern in _FIELD_PATTERNS:
            m = pattern.match(line)
            if m:
                values[key] = [m.group(1).strip()]
                current_field = key
                break
        else:
            # Not a recognized label line -- if we're mid-"description"
            # (the one field that's realistically multi-line), treat it as
            # a continuation; otherwise it's decoration (a section header)
            # and gets skipped rather than polluting a field.
            if current_field == "description":
                values["description"].append(line.strip())

    if not all(key in values for key, _ in _FIELD_PATTERNS):
        return None

    def get(key: str) -> str:
        return " ".join(values[key]).strip()

    reported_raw = get("reported").splitlines()[0].strip().lower() if get("reported") else ""
    return {
        "reporter_name": get("name") or None,
        "reporter_role": get("role") or None,
        "reporter_contact": get("contact") or None,
        "vehicle_plate": get("plate") or None,
        "location": get("location") or None,
        "incident_datetime": get("datetime") or None,
        "description": get("description"),
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
