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
from app.reports.taxonomy import CANONICAL_DAMAGE_TYPES, CANONICAL_PARTS, CANONICAL_SEVERITIES


@dataclass
class DraftResult:
    draft: SecurityIncidentDraft
    summary_text: str


def _damage_vocabulary() -> re.Pattern:
    """Words that mean a reporter's message is about the damage itself, built
    from the same vocabulary the model chooses parts and damage types from,
    so a new canonical part is covered without touching this list."""
    words = {
        "damage", "damaged", "dent", "dented", "scratch", "scratched", "scrape", "scraped",
        "broken", "crack", "cracked", "severity", "part", "parts", "left", "right", "side", "panel",
    }
    # Words that appear in part names but do not, on their own, mean the
    # message is about damage: "number"/"plate" (a plate correction),
    # "front"/"rear" (a location: "at the rear carpark"), and the generic
    # tails of a few part names.
    skip = {"side", "undetermined", "not", "listed", "other", "and", "the",
            "number", "plate", "front", "rear", "system", "assembly", "trim"}
    for name in (*CANONICAL_PARTS, *CANONICAL_DAMAGE_TYPES, *CANONICAL_SEVERITIES):
        for word in re.findall(r"[a-z]+", name.lower()):
            if len(word) > 2 and word not in skip:
                words.add(word)
    return re.compile(r"\b(" + "|".join(sorted(map(re.escape, words))) + r")\b", re.IGNORECASE)


_DAMAGE_WORDS = _damage_vocabulary()


def edit_mentions_damage(text: str) -> bool:
    return bool(_DAMAGE_WORDS.search(text or ""))


def _carry_over_damage(draft: SecurityIncidentDraft, previous: SecurityIncidentDraft, latest_edit: str) -> SecurityIncidentDraft:
    """Keeps the photo-derived findings stable across a redraft.

    The photos do not change between drafts, but every redraft is a fresh
    model call, and on a real filing a time-only correction ("16:45 not
    08:00") turned two parts with photo references, bounding boxes and
    confidence into one part with none of them, and moved the severity
    from Moderate to Severe. Reproduced 3 of 3 locally. So unless the
    reporter's latest message is actually about the damage, the previous
    findings are carried over whole; when it is, the model's revised list
    stands and only metadata it left blank is filled from the matching
    previous item.
    """
    if not previous.damage_summary:
        return draft
    if not edit_mentions_damage(latest_edit):
        draft.damage_summary = [item.model_copy(deep=True) for item in previous.damage_summary]
        draft.damaged_parts = list(previous.damaged_parts or []) or [i.part for i in previous.damage_summary]
        if previous.severity_level:
            draft.severity_level = previous.severity_level
        return draft
    by_part = {item.part: item for item in previous.damage_summary}
    for item in draft.damage_summary or []:
        prev = by_part.get(item.part)
        if prev is None:
            continue
        for field_name in ("damage_type", "severity", "photo_reference", "bbox_2d", "ai_confidence"):
            if getattr(item, field_name) in (None, "", []) and getattr(prev, field_name) not in (None, "", []):
                setattr(item, field_name, getattr(prev, field_name))
    return draft


def build_draft(description: str, photo_paths: list[str], session=None) -> DraftResult:
    # Hand the model what the reporter already told us, so a partial
    # correction ("the time was 16:45 not 08:00") has something to be applied
    # TO. Only on a redraft: on the first pass these values are what the
    # template just supplied, and repeating them back as context would invite
    # the model to treat its own echo as corroboration.
    known_facts: dict = {}
    if session is not None:
        # The side is the one fact the photo cannot be trusted for (see the
        # prompt), so it goes in on the first draft too: the person at the
        # car stated it, and nothing in the photo can corroborate or
        # contradict it.
        if getattr(session, "damaged_side", None):
            known_facts["side of the vehicle the damage is on (stated by the reporter at the car)"] = session.damaged_side
        if session.pending_edits:
            known_facts.update({
                "incident date and time": session.incident_datetime,
                "location": session.location,
                "vehicle plate": session.vehicle_plate,
                "reporter name": session.reporter_name,
            })
            # The findings from the previous draft, so a damage-related
            # correction is applied TO them rather than re-derived from a
            # blank slate (see _carry_over_damage for the non-damage case).
            previous = getattr(session, "draft", None)
            if previous is not None and previous.damage_summary:
                known_facts["damage already identified from the photos"] = "; ".join(
                    item.part + (f" ({item.damage_type}, {item.severity})" if item.damage_type or item.severity else "")
                    for item in previous.damage_summary
                )
                if previous.severity_level:
                    known_facts["overall severity"] = previous.severity_level
    draft = draft_report(description, photo_paths, known_facts or None)
    if session is not None and session.pending_edits and getattr(session, "draft", None) is not None:
        draft = _carry_over_damage(draft, session.draft, session.pending_edits[-1])
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
            if first_draft or not draft.vehicle_info.plate_number:
                draft.vehicle_info.plate_number = session.vehicle_plate

        if getattr(session, "insurer_name", None) or getattr(session, "workshop_name", None):
            if draft.insurance_details is None:
                from app.reports.schema import InsuranceDetails
                draft.insurance_details = InsuranceDetails()
            if getattr(session, "insurer_name", None) and (first_draft or not draft.insurance_details.insurer_name):
                draft.insurance_details.insurer_name = session.insurer_name
            if getattr(session, "workshop_name", None) and (first_draft or not draft.insurance_details.workshop_assigned):
                draft.insurance_details.workshop_assigned = session.workshop_name

    return DraftResult(draft=draft, summary_text=summarize(draft))


TEMPLATE_PROMPT = (
    "📋 *Incident Report — Surveyor / Reporter Details*\n\n"
    "Please fill in the fields below and send them back. I'll work out *Category*, "
    "*Damaged Parts*, and *Severity* automatically from your photos.\n\n"
    "👤 *Reporter Information*\n"
    "Name: \n"
    "Role/Position: \n"
    "Contact Number: \n\n"
    "🚘 *Vehicle & Incident*\n"
    "Vehicle Plate: \n"
    "Damaged Side (Left / Right / Front / Rear): \n"
    "Location: \n"
    "Date/Time: {now}\n"
    "Description: \n\n"
    "🏢 *Insurance & Repairer*\n"
    "Insurer (e.g. Tokio Marine, Income, AIG): \n"
    "Workshop (e.g. Precise Auto Service): \n\n"
    "🚓 *Authority Report*\n"
    "Reported to Authorities (Yes/No): No"
)


def build_template_prompt() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return TEMPLATE_PROMPT.format(now=now)


_FIELD_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("name", re.compile(r"^\s*name\s*:\s*(.*)$", re.IGNORECASE)),
    ("role", re.compile(r"^\s*role\s*/?\s*position\s*:\s*(.*)$", re.IGNORECASE)),
    ("contact", re.compile(r"^\s*contact\s*number\s*:\s*(.*)$", re.IGNORECASE)),
    ("plate", re.compile(r"^\s*vehicle\s*plate\s*:\s*(.*)$", re.IGNORECASE)),
    ("side", re.compile(r"^\s*damaged\s*side[^:]*:\s*(.*)$", re.IGNORECASE)),
    ("location", re.compile(r"^\s*location\s*:\s*(.*)$", re.IGNORECASE)),
    ("datetime", re.compile(r"^\s*date\s*/?\s*time\s*:\s*(.*)$", re.IGNORECASE)),
    ("insurer", re.compile(r"^\s*insurer[^:]*:\s*(.*)$", re.IGNORECASE)),
    ("workshop", re.compile(r"^\s*workshop[^:]*:\s*(.*)$", re.IGNORECASE)),
    ("description", re.compile(r"^\s*description\s*:\s*(.*)$", re.IGNORECASE)),
    ("reported", re.compile(r"^\s*reported\s*to\s*authorities[^:]*:\s*(.*)$", re.IGNORECASE)),
]


def parse_template_reply(text: str) -> Optional[dict]:
    """Pulls the reporter-filled fields out of a reply to build_template_prompt().
    Resilient: extracts any matching fields provided by user without requiring all labels."""
    values: dict[str, list[str]] = {}
    current_field: Optional[str] = None
    for line in text.splitlines():
        if not line.strip():
            current_field = None
            continue
        for key, pattern in _FIELD_PATTERNS:
            m = pattern.match(line)
            if m:
                values[key] = [m.group(1).strip()]
                current_field = key
                break
        else:
            if current_field == "description":
                values["description"].append(line.strip())

    # If neither description nor plate nor name were parsed, treat as unparsed
    if not values:
        return None

    def get(key: str) -> str:
        return " ".join(values.get(key, [])).strip()

    reported_raw = get("reported").splitlines()[0].strip().lower() if get("reported") else ""
    return {
        "reporter_name": get("name") or None,
        "reporter_role": get("role") or None,
        "reporter_contact": get("contact") or None,
        "vehicle_plate": get("plate") or None,
        "damaged_side": get("side") or None,
        "insurer_name": get("insurer") or None,
        "workshop_name": get("workshop") or None,
        "location": get("location") or None,
        "incident_datetime": get("datetime") or None,
        "description": get("description") or text,
        "reported_to_authorities": reported_raw in {"yes", "y", "true"},
    }


# Telegram caps a message at 4096 characters. A real assessment can carry 18
# damage lines plus a long AI description, so the budget is enforced rather
# than hoped for -- and it's the DESCRIPTION that gets trimmed, never the
# damage list, because the parts list is the thing the reporter is being
# asked to verify.
_MAX_MESSAGE_CHARS = 3900

_SEVERITY_DOT = {"severe": "🔴", "moderate": "🟠", "minor": "🟢"}


def _severity_dot(severity: Optional[str]) -> str:
    """Colour-codes a part by severity, matching the dashboard's severity
    colours. Unrated deliberately gets a neutral dot rather than green --
    "nobody has assessed this" is not the same as "it's fine"."""
    return _SEVERITY_DOT.get((severity or "").strip().lower(), "⚪")


def _damage_lines(draft: SecurityIncidentDraft) -> list[str]:
    """One line per damaged part.

    Uses damage_summary rather than the bare damaged_parts name list, so the
    severity and damage type the AI already produced are actually shown --
    previously both were discarded into a single comma-separated run of
    names, which is the hardest possible shape to check against a vehicle.
    """
    items = draft.damage_summary or []
    if items:
        return [
            f"{_severity_dot(i.severity)} {i.part}" + (f" · {i.damage_type}" if i.damage_type else "")
            for i in items
        ]
    # Older drafts (and any extraction that returned only names) still render.
    return [f"⚪ {p}" for p in (draft.damaged_parts or [])]


def summarize(draft: SecurityIncidentDraft) -> str:
    """Draft summary for the reporter to check before confirming.

    Grouped into sections with *bold* headers -- rendered by Telegram with
    parse_mode="Markdown" and by WhatsApp/Twilio natively, so one function
    serves both channels. Previously this was a flat run of "Label: value"
    lines sent as plain text, with the long description wedged between the
    facts and the call-to-action at the very top where it scrolled away.

    Order is deliberate: what the vehicle is and how bad it is first, then
    the damage list to check, then context, then the prose, then the action.
    """
    vehicle_bits = [b for b in [draft.vehicle_info.make if draft.vehicle_info else None,
                                draft.vehicle_info.model if draft.vehicle_info else None] if b]
    vehicle_name = " ".join(vehicle_bits) or draft.vehicle_details
    plate = draft.vehicle_info.plate_number if draft.vehicle_info else None

    # Only pair the plate with a real make/model. A generic "Vehicle" beside
    # a genuine plate number reads as though the make is unknown-but-stated.
    if plate and vehicle_name:
        head = f"🚗 *{plate}* · {vehicle_name}"
    elif plate:
        head = f"🚗 *{plate}*"
    else:
        head = f"🚗 *{vehicle_name or 'Vehicle'}*"
    lines = [head]

    damage = _damage_lines(draft)
    if draft.severity_level or damage:
        count = f"{len(damage)} damaged part{'' if len(damage) == 1 else 's'}" if damage else "no parts listed"
        sev = f"{_severity_dot(draft.severity_level)} *{draft.severity_level.upper()}*" if draft.severity_level else "⚪ *UNRATED*"
        lines.append(f"{sev} · {count}")

    if damage:
        lines += ["", "🔧 *Damage Found*", *damage]

    incident = []
    if draft.accident_type:
        incident.append(draft.accident_type)
    where_when = " · ".join(b for b in [draft.location, draft.incident_datetime] if b)
    if where_when:
        incident.append(where_when)
    incident.append(f"Police report: {'Yes' if draft.reported_to_authorities else 'No'}")
    lines += ["", "📍 *Incident*", *incident]

    reporter = []
    if draft.reporter_name or draft.reporter_role:
        reporter.append(" · ".join(b for b in [draft.reporter_name, draft.reporter_role] if b))
    if draft.reporter_contact:
        reporter.append(f"📞 {draft.reporter_contact}")
    if reporter:
        lines += ["", "👤 *Reporter*", *reporter]

    if draft.people_involved:
        lines += ["", "👥 *People Involved*", *[f"• {p.name}" + (f" ({p.role})" if p.role else "") for p in draft.people_involved]]
    if draft.witnesses:
        lines += ["", "👁 *Witnesses*", *[f"• {w.name}" for w in draft.witnesses]]
    if draft.immediate_actions:
        lines += ["", "🚨 *Immediate Actions*", draft.immediate_actions]

    # Claim & Governance details
    ins_lines = []
    if draft.insurance_details:
        if draft.insurance_details.insurer_name:
            ins_lines.append(f"🏢 *Insurer:* {draft.insurance_details.insurer_name}")
        if draft.insurance_details.workshop_assigned:
            ins_lines.append(f"🔧 *Workshop:* {draft.insurance_details.workshop_assigned}")
        if draft.insurance_details.claim_type:
            ins_lines.append(f"📋 *Claim Type:* {draft.insurance_details.claim_type}")
    if ins_lines:
        lines += ["", "💼 *Claim Governance*", *ins_lines]

    # Named rather than silently omitted: a blank location is something the
    # reporter can fix in their next message, but only if they notice it.
    missing = [label for label, value in
               [("location", draft.location), ("date/time", draft.incident_datetime)] if not value]
    if missing:
        lines += ["", f"⚠️ *Missing:* {', '.join(missing)} — tell me and I'll add it"]

    # A part the model could name but not place left/right gets no 3D
    # marker on the dashboard. The reporter is standing at the car and knows
    # the side instantly, so ask -- their reply comes back through the
    # redraft, where the model applies a stated side reliably.
    unsided = sorted({item.part.replace(" (side undetermined)", "")
                      for item in (draft.damage_summary or [])
                      if "(side undetermined)" in item.part})
    if unsided:
        lines += ["", f"❓ *Which side?* {', '.join(unsided)} — reply e.g. "
                      "\"the damage is on the right side\" and I'll place it"]

    footer = ["", "━━━━━━━━━━━━━━", "✅ Reply *confirm* to generate the PDF",
              "✏️ Or tell me what to change"]

    fixed = "\n".join(lines + footer)
    description = draft.description or ""
    room = _MAX_MESSAGE_CHARS - len(fixed) - len("\n\n📝 *What Happened*\n")
    if description and room > 120:
        if len(description) > room:
            description = description[: room - 40].rstrip() + "… _(full text in the PDF)_"
        lines += ["", "📝 *What Happened*", description]

    return "\n".join(lines + footer)


def combined_description(session) -> str:
    parts = [session.description or "", *session.pending_edits]
    return "\n".join(p for p in parts if p)
