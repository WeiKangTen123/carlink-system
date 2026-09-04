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
import base64
import logging
import mimetypes
import threading
import time
from pathlib import Path

from app.ai.client import available_api_keys, get_client
from app.config import settings
from app.reports.schema import DamageSummaryItem, SecurityIncidentDraft

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are drafting an official Car Incident & Security Report for Carlink System, based on a "
    "reporter's photos and free-text description sent over chat.\n"
    "Every field below is optional -- leave it null or empty unless it is actually stated by the "
    "reporter or directly visible in a photo. A human reviews and corrects this draft before it is "
    "ever finalized, so an honest gap (null) is always better than a plausible-sounding guess.\n"
    "1. First, write 'description' in clear, professional English, based strictly on what the reporter said and "
    "what is visible in the photos -- this is where you actually work out what happened. Every field after this "
    "one must be consistent with it: if a specific accident type, damaged part, or severity is stated in "
    "'description', the matching structured field below must reflect that same fact -- never leave 'accident_type', "
    "'damaged_parts', 'severity_level', or 'category' null/empty when you've already stated that exact fact in "
    "the description you just wrote. This works the other direction too: never state something in the description "
    "that the structured fields below don't back up.\n"
    "2. Set 'category' to 'Vehicle Collision or Damage' for vehicle incidents, or the other security categories, based on what was actually described.\n"
    "3. For vehicle incidents:\n"
    "   - Set 'accident_type' if the described events make the type clear (e.g. Collision with another vehicle, Rear-end collision, Side impact, Front impact, Parked vehicle hit, Single vehicle accident, Hit-and-run, Scrape / minor contact).\n"
    "   - Populate 'vehicle_info' only with make, model, plate_number, or driver details that are actually mentioned or legible in a photo. Never guess a plate number, VIN, or model you cannot actually read.\n"
    "   - For each damaged part actually visible in a photo or described in text, add an entry to 'damage_summary' with part, damage_type, and severity. Set 'ai_confidence' to 'High', 'Medium', or 'Low' based on how clearly you can actually see and identify that specific damage in the photo -- always give this honest qualitative self-assessment, never a fabricated-looking precise percentage, and never skip it just because you're not fully certain (uncertainty is exactly what 'Low' is for). Photos are labeled P01, P02, P03... in the order they're given to you below -- set 'photo_reference' to the one that actually shows this part when you can genuinely tell, otherwise leave it null. Never fill in 'oem_part_number' -- that is entered by a human during review, never looked up or guessed by you.\n"
    "   - When 'photo_reference' is set, detect the 2D bounding box of that specific damage within that photo and set 'bbox_2d' to [y_min, x_min, y_max, x_max], each value normalized 0-1000 with (0,0) at the photo's top-left corner -- tightly around the visible damage itself, not the whole part or the whole vehicle. Only set it when you can actually see the damage clearly enough to place a box around it with real confidence -- leave it null rather than a rough or centered guess.\n"
    "   - Set 'damaged_parts' array with the part names you actually identified.\n"
    "   - Set 'severity_level' only if the damage shown/described supports a clear Minor/Moderate/Severe judgment.\n"
    "4. Leave 'witnesses' and 'people_involved' as empty arrays [] unless a specific person is actually named or clearly described (e.g. \"my colleague Ahmad saw it happen\"). The reporter describing their own incident is not a witness or a person_involved entry -- do not create one for them. Never add a placeholder entry like 'Reporter', 'Unknown', or 'Unspecified' just to have something in the array; an empty array is the correct, honest answer when no one else is actually mentioned.\n"
    "5. Only add entries to 'timeline' for events whose time was actually stated by the reporter (e.g. \"around 2pm\"). Do not invent precise clock times that weren't given, and do not invent events that weren't mentioned.\n"
    "6. Only fill in 'recommendations' fields when there is a genuine, specific basis for the suggestion from the described damage -- do not fabricate generic repair advice.\n"
    "7. Only set 'ai_analysis.confidence_score' if you can give a genuine confidence estimate; otherwise leave it null. Do not output a placeholder percentage.\n"
    "Never fabricate names, phone numbers, plate numbers, VINs, claim numbers, timestamps, or confidence scores "
    "to make the report look more complete than the actual evidence supports."
)



def _model_chain() -> list[str]:
    return [m.strip() for m in settings.gemini_model_chain.split(",") if m.strip()]


def _build_input(description: str, photo_paths: list[str]) -> list[dict]:
    parts: list[dict] = [{"type": "text", "text": f"Reporter's description:\n{description}"}]
    for i, path in enumerate(photo_paths, start=1):
        media_type, _ = mimetypes.guess_type(path)
        media_type = media_type or "image/jpeg"
        data_b64 = base64.b64encode(Path(path).read_bytes()).decode("utf-8")
        # Labeled so the model can honestly attribute a damage_summary item to
        # the specific photo it actually saw it in (photo_reference), matching
        # the P0x numbering the renderer uses in the gallery/PDF.
        parts.append({"type": "text", "text": f"Photo P{i:02d}:"})
        parts.append({"type": "image", "data": data_b64, "mime_type": media_type})
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


# draft_report() is called via asyncio.to_thread() from three separate
# entry points (Telegram, WhatsApp, and the dashboard's /reports/
# analyze-photos), so concurrent calls land in real, distinct OS threads --
# a plain threading.Lock (not asyncio.Lock, which only coordinates within
# one event loop) is what actually serializes them. Holding the lock across
# the sleep is deliberate: it makes every caller queue up and get released
# one at a time, spaced by the interval, rather than all waking up at once
# and racing each other on the next check.
_rate_lock = threading.Lock()
_last_call_started_at = 0.0


def _wait_for_rate_limit() -> None:
    global _last_call_started_at
    with _rate_lock:
        wait = settings.gemini_min_call_interval_seconds - (time.monotonic() - _last_call_started_at)
        if wait > 0:
            time.sleep(wait)
        _last_call_started_at = time.monotonic()


def draft_report(description: str, photo_paths: list[str]) -> SecurityIncidentDraft:
    _wait_for_rate_limit()
    input_parts = _build_input(description, photo_paths)
    schema = SecurityIncidentDraft.model_json_schema()

    # Two nested fallbacks. Inner: each model in the chain, since they have
    # independent per-model quotas. Outer: each configured API key, since
    # every model shares one key's allowance -- once a key is exhausted
    # across the whole chain, only a different key helps. Ordered so a
    # single key's full chain is tried before moving on, rather than
    # burning every key on the first model.
    api_keys = available_api_keys() or [None]
    last_error: Exception | None = None

    for key_index, api_key in enumerate(api_keys):
        client = get_client(api_key)
        for model_id in _model_chain():
            try:
                interaction = client.interactions.create(
                    model=model_id,
                    system_instruction=SYSTEM_PROMPT,
                    input=input_parts,
                    response_format={
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": schema,
                    },
                    # Without this, a stalled call hangs indefinitely (hit
                    # this directly while testing model candidates locally
                    # -- one model call sat for 100+s with no response and
                    # no error) -- ties up a request/worker for no benefit
                    # since the whole point of the chain is to keep trying
                    # other models, not wait forever on one.
                    timeout=45.0,
                )
                draft = SecurityIncidentDraft.model_validate_json(interaction.output_text)
                draft = _strip_placeholder_people(draft)
                draft = _sanitize_damage_summary(draft)
                return _backfill_damage_summary(draft)
            except Exception as exc:
                # Covers rate limits, quota exhaustion, and other transient
                # failures on this model -- move to the next one in the
                # chain, then (outer loop) to the next API key.
                logger.warning(
                    "Gemini model %r failed on key #%d, trying next: %s",
                    model_id, key_index + 1, exc,
                )
                last_error = exc

    raise RuntimeError(
        f"All Gemini fallback models ({', '.join(_model_chain())}) failed "
        f"across {len(api_keys)} configured API key(s)."
    ) from last_error
