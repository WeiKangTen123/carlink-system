"""The AI's words on the cover page's static check and transmission, in
the firm's words (R-report gap 10; owner-approved Phase 1, 23 Nov 2026).

The drafting schema tells the AI "Automatic, Manual, CVT" for the
transmission, "Operable, Impaired, Damaged" for steering and footbrake and
"Good, Scratched, Resprayed, Original" for paintwork (schema.VehicleInfo).
The firm writes "Automatic" on all four of its reports -- a CVT car among
them -- and "Good" / "see annex(es)" on the static check. So after each
reading (ai/extraction._parse_draft) every such word is mapped:

  - to the firm's word, where it means the same thing ("CVT" and "Auto"
    are "Automatic", "Operable" is "Good");
  - otherwise the field is left empty -- the firm's usual wording then
    stands (review.TEMPLATE_DEFAULTS, "check" until confirmed), or the
    transmission prints "Not recorded" -- and the AI's own word is kept as
    a note (SecurityIncidentDraft.ai_words, shown beside the field by
    GET /reports/{id} and GET /intake/{id} ai_read.notes, never printed).
    "Scratched" paintwork may still be "Good" paintwork on the firm's
    page, and "Impaired" steering is a finding for a person to word.

A post-processing step, not a prompt change: the model sees the same
schema and prompt as before. Nothing here calls the AI.
"""
import re

# vehicle_info fields mapped, as review keys ("vehicle_info.<field>").
MAPPED_FIELDS = ("transmission", "steering_condition", "brakes_condition", "paintwork_condition")

_AUTOMATIC = "Automatic"
_MANUAL = "Manual"
_GOOD = "Good"
_SEE_ANNEX = "see annex(es)"

# Normalised phrase (lower case, punctuation as spaces except "/") -> the
# firm's word. Only the same meaning: a CVT, a dual-clutch (DCT / DSG) and
# a torque-converter box are all an automatic -- no clutch pedal -- which
# is how the firm writes them. An automated manual (AMT) or a
# "semi-automatic" is left for a person.
_TRANSMISSION = {
    **{w: _AUTOMATIC for w in (
        "automatic", "auto", "at", "a/t", "a t", "cvt", "e cvt", "ecvt", "i cvt", "automatic cvt", "cvt automatic",
        "auto cvt", "cvt auto", "automatic transmission", "auto transmission", "dct", "dual clutch", "dsg",
        "automatic dct", "dct automatic", "tiptronic", "steptronic", "automatic tiptronic",
    )},
    **{w: _MANUAL for w in ("manual", "mt", "m/t", "m t", "manual transmission")},
}
# Steering and footbrake: working as they should is "Good"; the firm's own
# "see annex(es)" stays as it is. "Impaired" / "Damaged" are findings.
_WORKS = {
    **{w: _GOOD for w in ("good", "operable", "operational", "ok", "okay", "working", "functional",
                          "functioning", "normal", "satisfactory", "fine")},
    **{w: _SEE_ANNEX for w in ("see annex es", "see annexes", "see annex")},
}
# Paintwork: only "Good" and its plain synonyms. "Original" (factory
# paint) says nothing about its state; "Scratched" / "Resprayed" are
# findings for a person to word.
_PAINTWORK = {w: _GOOD for w in ("good", "ok", "okay", "fair", "satisfactory", "fine")}

_TABLES = {
    "transmission": _TRANSMISSION,
    "steering_condition": _WORKS,
    "brakes_condition": _WORKS,
    "paintwork_condition": _PAINTWORK,
}


def _phrase(text: str) -> str:
    """"A/T", "e-CVT", "Automatic (CVT)." -> "a/t", "e cvt", "automatic cvt"."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9/]+", " ", text.lower())).strip()


def firm_word(field: str, value) -> tuple[str | None, str | None]:
    """(the value to keep, the AI's word to keep as a note) for one
    vehicle_info field. A field not in MAPPED_FIELDS, or a blank value,
    comes back as it is with no note; a word with the firm's meaning as
    the firm's word; any other word as (None, the word)."""
    if field not in _TABLES or not isinstance(value, str) or not value.strip():
        return value, None
    word = value.strip()
    mapped = _TABLES[field].get(_phrase(word))
    if mapped:
        return mapped, None
    return None, word[:80]


def firm_vehicle_words(vehicle: dict | None) -> tuple[dict, dict]:
    """(vehicle_info with MAPPED_FIELDS in the firm's words, {field: the
    AI's own word} for the ones that had no same-meaning firm word). A
    copy; the dict given is never changed."""
    out = dict(vehicle or {})
    notes = {}
    for field in MAPPED_FIELDS:
        if field not in out:
            continue
        out[field], note = firm_word(field, out[field])
        if note:
            notes[field] = note
    return out, notes


def apply_firm_words(draft):
    """A reading's SecurityIncidentDraft with its static-check and
    transmission words in the firm's words (firm_vehicle_words), and the
    words that had none kept in draft.ai_words ({field: word}, None when
    there are none). Changes the draft in place and returns it, as the
    other clean-up steps of ai/extraction._parse_draft do."""
    vehicle = getattr(draft, "vehicle_info", None)
    if vehicle is None:
        return draft
    notes = {}
    for field in MAPPED_FIELDS:
        value, note = firm_word(field, getattr(vehicle, field, None))
        setattr(vehicle, field, value)
        if note:
            notes[field] = note
    draft.ai_words = notes or None
    return draft
