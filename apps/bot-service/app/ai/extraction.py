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
    "1. Set 'category' to 'Vehicle Collision or Damage' for vehicle incidents, or other security categories.\n"
    "2. For vehicle incidents:\n"
    "   - Set 'accident_type' (e.g. Collision with another vehicle, Rear-end collision, Side impact, Front impact, Parked vehicle hit, Single vehicle accident, Hit-and-run, Scrape / minor contact).\n"
    "   - Populate 'vehicle_info' with make, model, plate_number, and driver details if mentioned/visible.\n"
    "   - Build a structured 'damage_summary' array listing each damaged part (e.g. Front Bumper, Right Door, Hood, Side Mirror), damage_type (Dent, Scratch, Broken, Crack), severity (Minor, Moderate, Severe), photo_reference (e.g. P01, P02), and ai_confidence (e.g. 92%).\n"
    "   - Set 'damaged_parts' array with part names.\n"
    "   - Set 'severity_level' (Minor, Moderate, Severe).\n"
    "3. Build a 'timeline' array with estimated timestamps and events (e.g. Collision, Photos taken, Report drafted).\n"
    "4. Provide realistic 'recommendations' (repair_recommendation, inspection_recommendation).\n"
    "5. Populate 'ai_analysis' with a summary and confidence score.\n"
    "6. Write 'description' in clear, professional English.\n"
    "Do not fabricate unverified details; extract what is visible or described."
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
