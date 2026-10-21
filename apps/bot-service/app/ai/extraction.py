"""AI extraction + drafting for the security incident report (Phase 0).

One Gemini call: takes the reporter's free-text description plus any
photos, and returns a validated SecurityIncidentDraft via structured
outputs. This intentionally collapses the "extraction / vision tagging /
drafting" three-call design from docs/proposal.md section H into a single
call for Phase 0 -- split them out in Phase 2 once damage-item tagging
needs its own confidence scoring and multiple-choice fallback.

Runs through a fallback chain of models (GEMINI_MODEL_CHAIN in .env) so
that hitting one free-tier model's RPM/TPM/RPD quota doesn't stop the bot
-- each model has its own independent quota bucket. If every model in the
chain fails, the caller (channels/telegram.py, channels/whatsapp.py) tells
the reporter drafting failed rather than fabricating a report.

The draft is never final on its own: conversation/flow.py always shows it
back to the reporter for confirmation before a PDF is generated.
"""
import json
import logging
import re

from app.ai.structured import call_structured, image_part
from app.config import settings
from app.reports.schema import DamageSummaryItem, SecurityIncidentDraft
from app.reports.taxonomy import (
    BODY_TYPES,
    CANONICAL_DAMAGE_TYPES,
    CANONICAL_PARTS,
    CANONICAL_SEVERITIES,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an expert Singapore Automotive Loss Adjuster & Forensic Assessor drafting an official "
    "Car Incident & Insurance Claim Assessment Report for Carlink System, based on photos, documents, "
    "and free-text descriptions sent over chat or uploaded.\n"
    "Every field below is optional -- leave it null or empty unless it is actually stated by the "
    "reporter, directly visible in a photo, or clearly legible in an uploaded document (such as an LTA log card, "
    "police report, or workshop estimate). A human surveyor reviews and corrects this draft before it is "
    "finalized, so an honest gap (null) is always better than a plausible-sounding guess.\n"
    "1. First, write 'description' in clear, professional loss-adjuster English, summarizing what happened based "
    "strictly on what was reported and visible in the photos and evidence. Every structured field below must be "
    "consistent with this description: if a specific accident type, damaged part, severity, or insurer is named in "
    "'description', the matching structured field must reflect that fact -- never leave 'accident_type', "
    "'damaged_parts', 'severity_level', or 'category' null/empty when you've already stated that exact fact in "
    "the description you just wrote.\n"
    "2. Set 'category' to 'Vehicle Collision or Damage' for vehicle incidents, or the other security categories, based on what was actually described.\n"
    "3. For vehicle incidents:\n"
    "   - Set 'accident_type' if the described events make the type clear (e.g. Collision with another vehicle, Rear-end collision, Side impact, Front impact, Parked vehicle hit, Single vehicle accident, Hit-and-run, Scrape / minor contact).\n"
    "   - Populate 'vehicle_info' with make, model, plate_number, color, year, body_type, and driver details that are actually mentioned or legible in photos or documents. Also extract:\n"
    "     * 'odometer_reading': mileage if visible in dashboard/speedometer cluster photos or stated (e.g. '015,287 km').\n"
    "     * 'transmission': 'Automatic', 'Manual', or 'CVT' if stated or clearly identifiable from interior photos.\n"
    "     * 'vin' (chassis number) and 'engine_number' if visible on vehicle identification plates, log cards, or stated.\n"
    "     * 'point_of_impact': primary contact area e.g. 'Rear centre', 'Front LH corner', 'Driver side rear quarter'.\n"
    "     * 'paintwork_condition': 'Good', 'Scratched', 'Resprayed', or 'Original' if discernible.\n"
    "     * 'tyres': if photos show tyre sidewalls or tread depth, extract brand, size (e.g. '215/60 R16'), and tread depth in mm.\n"
    "     Never guess a plate number, VIN, or model you cannot actually read.\n"
    "   - For each damaged part actually visible in a photo or described in text, add an entry to 'damage_summary' with part, damage_type, and severity. Set 'ai_confidence' to 'High', 'Medium', or 'Low' based on how clearly you can actually see and identify that specific damage in the photo. Photos are labeled P01, P02, P03... in the order given -- set 'photo_reference' to the one that actually shows this part when you can genuinely tell. Never fill in 'oem_part_number' unless supplied by human.\n"
    "   - When 'photo_reference' is set, detect the 2D bounding box of that specific damage within that photo and set 'bbox_2d' to [y_min, x_min, y_max, x_max], each value normalized 0-1000 with (0,0) at the photo's top-left corner -- tightly around the visible damage itself.\n"
    "   - When you can identify WHICH panel is damaged but not which side of the vehicle it is on, use the matching '(side undetermined)' option.\n"
    "   - LEFT and RIGHT mean the vehicle's own left and right as seen by its driver facing forward. If the reporter stated the side, use it.\n"
    "   - Set 'damaged_parts' array with the part names you actually identified.\n"
    "   - Set 'severity_level' only if the damage shown/described supports a clear Minor/Moderate/Severe judgment.\n"
    "4. Insurance & Workshop Details ('insurance_details'):\n"
    "   - Extract 'insurer_name' (e.g. Tokio Marine, NTUC Income, AIG, Allianz, Etiqa, Great American, Singlife, MSIG, DirectAsia) if stated, shown on documents, or implied by claim papers.\n"
    "   - Extract 'policy_number' and 'claim_number' if mentioned.\n"
    "   - Set 'claim_type' to 'Own damage', 'Third party', 'Comprehensive', or 'Special case' based on stated context.\n"
    "   - Extract 'workshop_assigned' (e.g. Precise Auto Service, Sin Ming Autocare, EM-1 Auto, ComfortDelGro) if mentioned as the repairer or inspection location.\n"
    "   - Extract 'estimated_repair_cost' if an estimate or quantum is stated.\n"
    "5. Police & Regulatory ('police_report'):\n"
    "   - If reported to police or a police report is attached, set 'reported_to_police' to true and extract 'police_station', 'report_number', and 'officer_name' if given.\n"
    "6. Third-Party Counterparties ('third_party_info'):\n"
    "   - If another vehicle is involved (e.g. 'hit by taxi SHB1234' or 'third party lorry SBA8821X'), extract their plate_number, make_model, driver details, insurer_name, and damage_description.\n"
    "7. Environmental Context:\n"
    "   - Set 'weather_condition' (Clear, Rainy, Night/Dark, Foggy, Wet Surface), 'road_condition' (Dry, Wet, Slippery, Gravel, Uneven), and 'traffic_condition' (Light, Moderate, Heavy, Stationed) if stated or evident from photos.\n"
    "8. Set 'location' to where the incident happened and 'incident_datetime' to when, but ONLY as stated or legible in evidence. Never return a bare time with no date.\n"
    "9. Only add entries to 'timeline' for events whose time was stated (e.g. 'around 2pm').\n"
    "10. Only fill in 'recommendations' fields when there is a genuine, specific basis from the described damage.\n"
    "Never fabricate names, phone numbers, plate numbers, VINs, claim numbers, timestamps, or confidence scores."
)



# Fields the model must EMIT rather than silently skip.
_REQUIRED_OUTPUT_FIELDS = [
    "description",
    "category",
    "damage_summary",
    "damaged_parts",
    "severity_level",
    "accident_type",
    "vehicle_info",
    "insurance_details",
    "police_report",
    "location",
    "incident_datetime",
]


# Sections of SecurityIncidentDraft the model never writes: Annex A/B/C and
# the BOLA assessment are synthesized after drafting
# (reports/synthesize_annexes.py), and the sign-off is the surveyor's. Since
# they were added to the draft model (2ae2183), sending them as part of the
# response schema made EVERY drafting call -- bot and dashboard alike --
# fail on every model with 400 "Request contains an invalid argument".
# Confirmed against the live API on 2026-10-20: the identical request with
# these left out succeeds. They keep their defaults when parsed.
_NOT_MODEL_OUTPUT = ("sign_off", "annex_a", "annex_b", "annex_c", "bola_assessment")


def _prune_unreferenced_defs(schema: dict) -> None:
    """Drops $defs no longer reachable from the remaining properties, so
    removing a section also removes the nested models only it used."""
    defs = schema.get("$defs", {})
    keep: set[str] = set()
    pending = [schema.get("properties", {})]
    while pending:
        for name in re.findall(r'"#/\$defs/([^"]+)"', json.dumps(pending.pop())):
            if name in defs and name not in keep:
                keep.add(name)
                pending.append(defs[name])
    schema["$defs"] = {name: node for name, node in defs.items() if name in keep}


def _constrain(node: dict, values: list[str]) -> None:
    """Applies an enum to a field that may be `str` or `anyOf[str, null]`.

    Written to handle both shapes because the nullable fields render as
    anyOf; a naive `node["enum"] = ...` silently does nothing there, which
    would look like the constraint was applied when it wasn't.
    """
    if "anyOf" in node:
        for branch in node["anyOf"]:
            if branch.get("type") == "string":
                branch["enum"] = values
    elif node.get("type") == "string":
        node["enum"] = values


def _response_schema() -> dict:
    """The schema handed to Gemini -- deliberately stricter than the Pydantic
    model itself.

    The model stays permissive so the six reports already on file, which
    hold free-text parts like "Rear bumper fascia" and damage types like
    "Grazed/slack/cut", keep parsing and rendering. Only NEW extractions are
    constrained, so the vocabulary tightens going forward without
    invalidating history.
    """
    schema = SecurityIncidentDraft.model_json_schema()
    for name in _NOT_MODEL_OUTPUT:
        schema.get("properties", {}).pop(name, None)
    _prune_unreferenced_defs(schema)
    required = set(schema.get("required") or []) - set(_NOT_MODEL_OUTPUT)
    required.update(_REQUIRED_OUTPUT_FIELDS)
    schema["required"] = sorted(required)

    defs = schema.get("$defs", {})
    item = defs.get("DamageSummaryItem", {}).get("properties", {})
    if "part" in item:
        _constrain(item["part"], CANONICAL_PARTS)
    if "damage_type" in item:
        _constrain(item["damage_type"], CANONICAL_DAMAGE_TYPES)
    if "severity" in item:
        _constrain(item["severity"], CANONICAL_SEVERITIES)

    top = schema.get("properties", {})
    if "severity_level" in top:
        _constrain(top["severity_level"], CANONICAL_SEVERITIES)
    if "damaged_parts" in top:
        items = top["damaged_parts"].get("items")
        if isinstance(items, dict):
            _constrain(items, CANONICAL_PARTS)

    vehicle = defs.get("VehicleInfo", {}).get("properties", {})
    if "body_type" in vehicle:
        _constrain(vehicle["body_type"], BODY_TYPES)

    return schema


def _model_chain() -> list[str]:
    return [m.strip() for m in settings.gemini_model_chain.split(",") if m.strip()]


def _build_input(
    description: str,
    photo_paths: list[str],
    known_facts: dict | None = None,
    photo_labels: list[str] | None = None,
) -> list[dict]:
    parts: list[dict] = []
    if known_facts:
        # Facts the reporter already gave, passed so a PARTIAL correction can
        # be applied to them. Without this the model only ever saw the
        # description plus the reporter's edits: told "the time was 16:45 not
        # 08:00" it had no date to attach, correctly refused to invent one,
        # and returned null -- so the correction silently did nothing and the
        # stale value survived. Kept as its own labelled block, not folded
        # into the description, so it is context to reconcile rather than
        # narrative to repeat back.
        established = "\n".join(f"- {k}: {v}" for k, v in known_facts.items() if v)
        if established:
            parts.append({
                "type": "text",
                "text": (
                    "Already established by the reporter (treat as current values). "
                    "Keep each one exactly as-is UNLESS the reporter's message below corrects it; "
                    "if a message corrects only part of a value, such as the time within a date and "
                    "time, change only that part and keep the rest:\n" + established
                ),
            })
    parts.append({"type": "text", "text": f"Reporter's description:\n{description}"})
    if photo_labels:
        # Dashboard intake only sends a selection of a large case (see
        # intake/pipeline.py), so the labels skip numbers. Without saying so,
        # "labeled P01, P02... in the order given" in the system prompt
        # invites the model to renumber them from P01.
        parts.append({
            "type": "text",
            "text": (
                "Only a selection of this case's photos is shown below. Each is labelled with its "
                "case photo number -- use exactly that label for 'photo_reference'."
            ),
        })
    for i, path in enumerate(photo_paths, start=1):
        # Labeled so the model can honestly attribute a damage_summary item to
        # the specific photo it actually saw it in (photo_reference), matching
        # the P0x numbering the renderer uses in the gallery/PDF.
        label = photo_labels[i - 1] if photo_labels else f"P{i:02d}"
        parts.append({"type": "text", "text": f"Photo {label}:"})
        parts.append(image_part(path))
    return parts


# Structured-output models reach for a placeholder witnesses/people_involved
# entry despite being told to leave it empty -- confirmed reproducible
# against the live model chain (~35% of trials) even after the system
# prompt explicitly said not to. Prompting alone isn't reliable enough for
# a "never fabricate" requirement, so this is a deterministic backstop.
#
# An exact-match blocklist wasn't enough on its own -- live testing turned
# up a growing, unpredictable set of patterns beyond simple placeholders
# like "Unknown": the literal schema field name ("name"), and full
# explanatory sentences ("No other witnesses mentioned."). A real person's
# name doesn't look like either of those, so this checks the *shape* of
# the string rather than trying to enumerate every placeholder the model
# might invent next.
_PLACEHOLDER_NAMES = {
    "unknown", "unspecified", "n/a", "na", "reporter", "string", "none",
    "not specified", "not applicable", "tbd", "pending", "witness", "person",
    "jane doe", "john doe", "name", "value", "n.a.", "not provided",
    "not available", "not given", "not applicable.", "unidentified",
}
_PLACEHOLDER_SUBSTRINGS = (
    "no other", "not mentioned", "not identified", "no witness", "no name",
    "witnesses were", "person involved", "no one", "n/a", "unspecified",
)


def _looks_like_fabricated_name(name: str) -> bool:
    cleaned = name.strip()
    if not cleaned:
        return True
    lowered = cleaned.lower()
    if lowered in _PLACEHOLDER_NAMES:
        return True
    if any(sub in lowered for sub in _PLACEHOLDER_SUBSTRINGS):
        return True
    # A real name is a short label, not a sentence -- explanatory text
    # ("No other witnesses were mentioned in the report.") is exactly the
    # shape the model reaches for instead of an empty array.
    if any(ch in cleaned for ch in ".!?") or len(cleaned.split()) > 5:
        return True
    return False


def _strip_placeholder_people(draft: SecurityIncidentDraft) -> SecurityIncidentDraft:
    draft.witnesses = [w for w in draft.witnesses if not _looks_like_fabricated_name(w.name)]
    draft.people_involved = [p for p in draft.people_involved if not _looks_like_fabricated_name(p.name)]
    return draft


# Confirmed live (trial 7 of a 10-run backfill test): the model can leak
# its own internal reasoning straight into a structured field instead of a
# clean value -- one 'severity' came back as a ~600-character run-on
# string of the model visibly talking itself through the bbox_* fields
# ("...let us match prompt rules carefully only set when confident...").
# Constraining these to their documented enums and nulling anything else
# is safer than displaying whatever leaked through -- an honest null reads
# far better in the dashboard than a wall of garbled text in a severity
# badge.
_VALID_SEVERITIES = {"Minor", "Moderate", "Severe"}
_VALID_CONFIDENCE = {"High", "Medium", "Low"}


def _sanitize_damage_summary(draft: SecurityIncidentDraft) -> SecurityIncidentDraft:
    for item in draft.damage_summary:
        if item.severity not in _VALID_SEVERITIES:
            item.severity = None
        if item.ai_confidence not in _VALID_CONFIDENCE:
            item.ai_confidence = None
        # A real part name is a short label ("Rear Bumper"), not a
        # sentence -- same shape check as _looks_like_fabricated_name.
        if item.part and (any(ch in item.part for ch in "\n") or len(item.part) > 80):
            item.part = item.part[:80].strip()
    if draft.severity_level not in _VALID_SEVERITIES:
        draft.severity_level = None
    return draft


def _backfill_damage_summary(draft: SecurityIncidentDraft) -> SecurityIncidentDraft:
    """damage_summary comes back empty on a large share of real calls even
    when damaged_parts is populated -- confirmed live: the key is simply
    absent from the model's raw JSON in those responses (not an explicit
    []), typically alongside a shorter overall response, so this looks
    like the model treating a structured per-item breakdown as skippable
    effort rather than a deliberate "nothing to report" signal. The
    dashboard and PDF template already fall back to damaged_parts for
    display when damage_summary is empty (see ReportDetailPage's damage
    table) -- doing the same synthesis here, once, at draft time, keeps
    every consumer consistent instead of re-deriving it in three places.
    Adds nothing the model didn't already say: same part names, severity
    copied from the one already-drafted severity_level, everything else
    (damage_type, photo_reference, bounding box, ai_confidence) left null
    rather than guessed."""
    if draft.damage_summary or not draft.damaged_parts:
        return draft
    draft.damage_summary = [
        DamageSummaryItem(part=part, severity=draft.severity_level, human_verified=False)
        for part in draft.damaged_parts
    ]
    return draft


# draft_report() is called via asyncio.to_thread() from three separate entry
# points (Telegram, WhatsApp, and the dashboard's /reports/analyze-photos),
# so concurrent calls land in real, distinct OS threads. Serialising them is
# now ai/rate_limit.py's job, which additionally coordinates across the two
# processes that share one API key -- something module state never could.


def _parse_draft(raw_output: str) -> SecurityIncidentDraft:
    # No annex synthesis here any more: it used to fill every AI draft --
    # bot, dashboard and intake -- with priced annexes, a liability finding
    # and a workshop before any human had seen the case.
    draft = SecurityIncidentDraft.model_validate_json(raw_output)
    draft = _strip_placeholder_people(draft)
    return _backfill_damage_summary(draft)


def draft_report(
    description: str,
    photo_paths: list[str],
    known_facts: dict | None = None,
    photo_labels: list[str] | None = None,
    timeout: float = 45.0,
) -> SecurityIncidentDraft:
    """photo_labels overrides the default P01, P02... numbering, for callers
    sending a subset of a larger case (dashboard intake). timeout is per
    model attempt; intake raises it because it sends up to ~24 photos in one
    call, where the bot's few photos fit comfortably in 45s."""
    return call_structured(
        purpose="draft",
        system_instruction=SYSTEM_PROMPT,
        input_parts=_build_input(description, photo_paths, known_facts, photo_labels),
        schema=_response_schema(),
        parse=_parse_draft,
        models=_model_chain(),
        timeout=timeout,
    )
