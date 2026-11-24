"""The AI's words on the cover page's static check and transmission, in
the firm's words (R-report gap 10; owner-approved Phase 1, 23 Nov 2026).

Behind settings.intake_firm_words (INTAKE_FIRM_WORDS, default "off"): it
changes what an AI reading prints, so it stays off until it passes the
accuracy gate. Off, ai/extraction._parse_draft never calls it and every
reading is stored as before, byte for byte.

The drafting schema tells the AI "Automatic, Manual, CVT" for the
transmission, "Operable, Impaired, Damaged" for steering and footbrake and
"Good, Scratched, Resprayed, Original" for paintwork (schema.VehicleInfo),
but the field is free text and the AI writes "6-Speed Automatic" or
"Manual (MT)" too. The firm writes "Automatic" on all four of its reports
-- a CVT car among them -- and "Good" / "see annex(es)" on the static
check. So, switched on, after each reading every such word becomes:

  - the firm's word where it means the same thing, judged by meaning, not
    by an exact phrase: a transmission that clearly says automatic (an
    automatic, CVT, DCT/DSG, AMT, "A/T", "6AT", "6-Speed Automatic") is
    "Automatic", one that clearly says manual ("5-speed manual", "M/T",
    "Manual (MT)") is "Manual"; a static check that plainly says working /
    good ("Operable", "In good working order") is "Good", "See annexes"
    is "see annex(es)";
  - otherwise the AI's own word, as it wrote it: an unclear transmission
    ("Semi-automatic", "Unknown") and every finding ("Resprayed",
    "Scratched", "Faded" paintwork; "Impaired", "Damaged", "Faulty"
    steering or footbrake) -- never blanked, and never turned into "Good".
    Such a field holds a value the AI read, so the review shows it as
    "check": an open item until a person confirms it (review._field_state).

Whenever the stored word is not the AI's own, or is the AI's own kept for
a person to word, the AI's word is kept as a note
(SecurityIncidentDraft.ai_words, shown beside the field by GET
/reports/{id} and GET /intake/{id} ai_read.notes, never printed).

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

# ------------------------------------------------------------ transmission
#
# A CVT, a dual-clutch (DCT / DSG / PDK), an automated manual (AMT) and a
# torque-converter box are all an automatic -- no clutch pedal -- which is
# how the firm writes them. Makers' names for automatic boxes count too.
_AUTO_WORDS = frozenset({
    "automatic", "auto", "at", "cvt", "ecvt", "icvt", "ivt", "dct", "dsg", "pdk", "amt", "tiptronic",
    "steptronic", "multitronic", "xtronic", "lineartronic", "sportronic", "geartronic", "powershift",
    "tronic",
})
_AUTO_PHRASES = ("dual clutch", "twin clutch", "torque converter", "continuously variable", "automated manual")
_MANUAL_WORDS = frozenset({"manual", "mt", "stick", "stickshift"})
# "Automatic with manual mode": the manual words of an automatic's shift
# mode, not a manual box.
_MANUAL_MODE = re.compile(
    r"\bmanual (?:mode|shift|shifting|override|paddles?|gate|function|control|select|selection|sequential)\b")
# Words that make a transmission unclear whatever else it says -- a person
# words it ("Semi-automatic", "Not automatic", "Automatic or manual").
_UNCLEAR_WORDS = frozenset({"semi", "not", "non", "no", "or", "unknown", "unsure", "unclear", "maybe", "possibly"})


def _transmission_words(text: str) -> list[str]:
    """"A / T", "6-Speed AT", "e-CVT" -> ["at"], ["6", "speed", "at"],
    ["e", "cvt"]; "6AT" / "5MT" -> ["at"] / ["mt"] (the gear count
    dropped)."""
    s = text.lower()
    s = re.sub(r"\ba\s*[./]\s*t\b\.?", " at ", s)
    s = re.sub(r"\bm\s*[./]\s*t\b\.?", " mt ", s)
    words = []
    for word in re.findall(r"[a-z0-9]+", s):
        m = re.fullmatch(r"\d+(at|mt|amt|dct|cvt)", word)
        words.append(m.group(1) if m else word)
    return words


def transmission_meaning(text: str) -> str | None:
    """"Automatic" or "Manual" when the text clearly says which, else None
    (unclear: the AI's own word stays)."""
    words = _transmission_words(text)
    if not words or _UNCLEAR_WORDS & set(words):
        return None
    joined = " ".join(words)
    auto = bool(_AUTO_WORDS & set(words)) or any(phrase in joined for phrase in _AUTO_PHRASES)
    # An automatic's manual mode, and an "automated manual", are no manual box.
    rest = _MANUAL_MODE.sub(" ", joined.replace("automated manual", " "))
    manual = bool(_MANUAL_WORDS & set(rest.split()))
    if auto and not manual:
        return _AUTOMATIC
    if manual and not auto:
        return _MANUAL
    return None


# --------------------------------------------------------- static check
#
# Only plainly positive answers are "Good": every word of the answer is
# one of these or a filler ("condition", "order"), so "Not working",
# "Good, but scratched", "Operable (no issues)" stay as the AI wrote them.
# "Fair" is worse than good: never "Good". "Original" (factory paint) says
# nothing about its state.
_PAINT_GOOD = frozenset({"good", "ok", "okay", "fine", "satisfactory", "excellent"})
_WORKS_GOOD = _PAINT_GOOD | frozenset({"operable", "operational", "working", "functional", "functioning", "normal"})
_FILLER = frozenset({"condition", "order", "very", "all", "and", "overall"})
# "In good working order": the "in" dropped -- but never before "operable"
# and the like ("in operable" is inoperable).
_IN_BEFORE = frozenset({"good", "fine", "normal", "satisfactory", "excellent", "working"})
_SEE_ANNEX_PHRASES = frozenset({"see annex", "see annexes", "see annex es", "see annexure", "see annexures"})


def _condition_words(text: str) -> list[str]:
    """Lower-case words; a hyphen joins ("in-operable" -> "inoperable",
    never "in" + "operable")."""
    words = [w.replace("-", "") for w in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower())]
    if len(words) > 1 and words[0] == "in" and words[1] in _IN_BEFORE:
        words = words[1:]
    return words


def condition_meaning(field: str, text: str) -> str | None:
    """The firm's word for a static-check answer with its meaning: "Good"
    for a plainly positive one, "see annex(es)" for a pointer to the
    annexes; else None (a finding, or unclear: the AI's own word stays)."""
    words = _condition_words(text)
    if not words:
        return None
    if " ".join(words) in _SEE_ANNEX_PHRASES:
        return _SEE_ANNEX
    good = _PAINT_GOOD if field == "paintwork_condition" else _WORKS_GOOD
    if set(words) <= good | _FILLER and set(words) & good:
        return _GOOD
    return None


# ------------------------------------------------------------- the API


def firm_word(field: str, value) -> tuple:
    """(the value to keep, the AI's own word kept as a note, or None) for
    one vehicle_info field. A field not in MAPPED_FIELDS, or a blank value,
    comes back as it is with no note. A word with a firm word of its
    meaning comes back as that word (with a note when that changed it); any
    other word comes back exactly as the AI wrote it, with itself as the
    note -- never blanked, never "Good"."""
    if field not in MAPPED_FIELDS or not isinstance(value, str) or not value.strip():
        return value, None
    word = value.strip()
    mapped = transmission_meaning(word) if field == "transmission" else condition_meaning(field, word)
    if mapped is None:
        return value, word[:80]
    return mapped, (None if mapped == word else word[:80])


def firm_vehicle_words(vehicle: dict | None) -> tuple[dict, dict]:
    """(vehicle_info with MAPPED_FIELDS in the firm's words, {field: the
    AI's own word} for the ones changed or kept for a person). A copy; the
    dict given is never changed."""
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
    transmission words in the firm's words (firm_word), and the AI's own
    words kept in draft.ai_words ({field: word}, None when there are none).
    Changes the draft in place and returns it, as the other clean-up steps
    of ai/extraction._parse_draft do. Called only while
    settings.intake_firm_words is "on"."""
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
