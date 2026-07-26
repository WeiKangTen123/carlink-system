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
from pathlib import Path

from app.ai.client import get_client
from app.config import settings
from app.reports.schema import SecurityIncidentDraft

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are drafting an official Car Incident & Security Report for Carlink System, based on a "
    "reporter's photos and free-text description sent over chat.\n"
    "Every field below is optional -- leave it null or empty unless it is actually stated by the "
    "reporter or directly visible in a photo. A human reviews and corrects this draft before it is "
    "ever finalized, so an honest gap (null) is always better than a plausible-sounding guess.\n"
    "1. Set 'category' to 'Vehicle Collision or Damage' for vehicle incidents, or the other security categories, based on what was actually described.\n"
    "2. For vehicle incidents:\n"
    "   - Set 'accident_type' if the described events make the type clear (e.g. Collision with another vehicle, Rear-end collision, Side impact, Front impact, Parked vehicle hit, Single vehicle accident, Hit-and-run, Scrape / minor contact).\n"
    "   - Populate 'vehicle_info' only with make, model, plate_number, or driver details that are actually mentioned or legible in a photo. Never guess a plate number, VIN, or model you cannot actually read.\n"
    "   - For each damaged part actually visible in a photo or described in text, add an entry to 'damage_summary' with part, damage_type, and severity. Only set 'ai_confidence' if you can give a genuine, meaningful confidence estimate for that specific detection -- otherwise leave it null. Never invent a photo_reference; leave it null.\n"
    "   - Set 'damaged_parts' array with the part names you actually identified.\n"
    "   - Set 'severity_level' only if the damage shown/described supports a clear Minor/Moderate/Severe judgment.\n"
    "3. Only add entries to 'timeline' for events whose time was actually stated by the reporter (e.g. \"around 2pm\"). Do not invent precise clock times that weren't given, and do not invent events that weren't mentioned.\n"
    "4. Only fill in 'recommendations' fields when there is a genuine, specific basis for the suggestion from the described damage -- do not fabricate generic repair advice.\n"
    "5. Only set 'ai_analysis.confidence_score' if you can give a genuine confidence estimate; otherwise leave it null. Do not output a placeholder percentage.\n"
    "6. Write 'description' in clear, professional English, based strictly on what the reporter said and what is visible in the photos.\n"
    "Never fabricate names, phone numbers, plate numbers, VINs, claim numbers, timestamps, or confidence scores "
    "to make the report look more complete than the actual evidence supports."
)



def _model_chain() -> list[str]:
    return [m.strip() for m in settings.gemini_model_chain.split(",") if m.strip()]


def _build_input(description: str, photo_paths: list[str]) -> list[dict]:
    parts: list[dict] = [{"type": "text", "text": f"Reporter's description:\n{description}"}]
    for path in photo_paths:
        media_type, _ = mimetypes.guess_type(path)
        media_type = media_type or "image/jpeg"
        data_b64 = base64.b64encode(Path(path).read_bytes()).decode("utf-8")
        parts.append({"type": "image", "data": data_b64, "mime_type": media_type})
    return parts


def draft_report(description: str, photo_paths: list[str]) -> SecurityIncidentDraft:
    client = get_client()
    input_parts = _build_input(description, photo_paths)
    schema = SecurityIncidentDraft.model_json_schema()

    last_error: Exception | None = None
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
            )
            return SecurityIncidentDraft.model_validate_json(interaction.output_text)
        except Exception as exc:
            # Covers rate limits, quota exhaustion, and other transient
            # failures on this model -- move to the next one in the chain.
            logger.warning("Gemini model %r failed, trying next fallback model: %s", model_id, exc)
            last_error = exc

    raise RuntimeError(
        f"All Gemini fallback models ({', '.join(_model_chain())}) failed."
    ) from last_error
