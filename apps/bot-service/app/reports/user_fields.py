"""The facts only the user types in, and no example-case values in what the
AI reads (owner, 25 Nov 2026): the workshop and the odometer reading are
always left empty for the user, never filled by the AI; and no value taken
from the example cases (references only: the report's format and the
tests' answer key) is in the AI's instructions. Behind
settings.intake_user_fields, off by default.

"on":
  - every read is sent without them: no instruction or example about the
    workshop or the odometer in the prompt, neither field in the response
    schema (ai/extraction.py);
  - the read's other examples that came from the example cases -- the tyre
    size, the tyre brands, the OEM part number -- are generic ones (or
    none) in the prompt and the response schema (extraction.
    _USER_FIELDS_V1 / _V2, _SCHEMA_EXAMPLES);
  - the website's upload tip doesn't say the AI reads the odometer (GET
    /auth/me user_fields, so a New Report knows before its first upload;
    GET /intake/{id} too), and the Telegram bot's guide points the
    workshop to its template's "Workshop:" line (channels/telegram.
    welcome_guide); a staff correction's own "Workshop:" line is applied
    as the person's, a correction that only mentions a workshop gets a
    hint to send that line (telegram.correction_workshop); the public
    bot no longer invites a workshop it has no step for (telegram_public.
    _what_extras);
  - a value the model returns all the same is dropped as its answer is
    parsed (drop_from_draft, from extraction._parse_draft), before it can
    reach the draft, the AI draft (Report.ai_draft / IntakeSession.ai_draft)
    or the case -- the dashboard's New Report, the intake pipeline (which
    the Telegram bridge and public reports go through) and the Telegram
    and WhatsApp bots all draft through that one parse;
  - the field review counts both as a person's to fill ("office": "Needs
    you" while empty), and GET /reports/{id} and GET /intake/{id} ai_read
    never lists them, so no "AI" tag shows beside them (reports/review.py,
    reports/filing.ai_read_for), and the AI-accuracy row leaves them out
    of the fields the AI can fill (reports/accuracy.ai_fields);
  - a draft read before the switch went on and read again: a value still
    exactly the AI's last one, that no person confirmed, is cleared and
    shows "Needs you" (clear_ai_values, from pipeline.merge_redraft), and
    the F1 fallback's copy of the last AI draft drops them too
    (drop_from_dict, from pipeline.fallback_draft).
A value a person typed -- on the cover page, or into the Telegram bot's
template ("Workshop: ...") -- is theirs and stays. The photo kind
"odometer" is the sorter's and stays: a photo of the dashboard is still
sorted as one, only its reading isn't filled.

"off": everything as before, byte for byte (scripts/test_user_fields.py).
"""
import logging

from app.config import settings

logger = logging.getLogger(__name__)

# The review keys of the two facts (dotted paths into the case data).
REVIEW_KEYS = ("insurance_details.workshop_assigned", "vehicle_info.odometer_reading")
# The model's fields dropped from its answer: the two facts and the
# workshop's address (a cover-page field the read is never asked for, but
# a model answering off the schema could still write).
DRAFT_FIELDS = (
    ("vehicle_info", "odometer_reading"),
    ("insurance_details", "workshop_assigned"),
    ("cover_page", "workshop_address"),
)
# In the response schema: the model definition and its field.
SCHEMA_FIELDS = (("VehicleInfo", "odometer_reading"), ("InsuranceDetails", "workshop_assigned"))
# The run log's tag and the knowledge stamp's prompt carry this while on.
TAG = "user-fields"


def on() -> bool:
    return getattr(settings, "intake_user_fields", "off") == "on"


def drop_from_draft(draft) -> list[str]:
    """Empties the two facts (and the workshop address) on a parsed
    SecurityIncidentDraft, in place. Returns the fields that held a value,
    by name only: their values are never logged or returned."""
    dropped = []
    for section, field in DRAFT_FIELDS:
        node = getattr(draft, section, None)
        if node is None:
            continue
        value = getattr(node, field, None)
        if value is not None and str(value).strip():
            dropped.append(f"{section}.{field}")
        setattr(node, field, None)
    return dropped


def drop_from_dict(draft: dict) -> dict:
    """drop_from_draft for a draft kept as a dict (the F1 fallback's copy of
    the last AI draft): each of the fields emptied where its section is
    there. In place; returns the dict."""
    for section, field in DRAFT_FIELDS:
        node = draft.get(section)
        if isinstance(node, dict) and field in node:
            node[field] = None
    return draft


def clear_ai_values(current: dict, previous_ai: dict | None, merged: dict) -> dict:
    """For a re-read (pipeline.merge_redraft) while on: `merged` with each of
    the fields emptied whose value in the form now (`current`) is still the
    AI's last one (`previous_ai`) and that no person confirmed (data
    .checked) -- an AI value from before the switch went on, which the
    re-read would otherwise keep and the review then call "You". Emptied,
    it shows "Needs you". A value a person typed differs from the AI's and
    stays; so does a confirmed one. The case keeps no record of who typed
    a field, so a typed value that is the AI's to the letter, unconfirmed,
    is emptied too."""
    from app.reports.review import checked_of, is_blank, same_value, value_at, with_value

    checked = checked_of(current)
    for section, field in DRAFT_FIELDS:
        key = f"{section}.{field}"
        value = value_at(current, key)
        if is_blank(value) or key in checked:
            continue
        if same_value(value, value_at(previous_ai or {}, key)):
            merged = with_value(merged, key, None)
    return merged
