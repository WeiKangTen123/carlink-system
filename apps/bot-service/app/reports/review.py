"""Field review (contract 7, 2026-11-11): for every cover and parties field,
damage item and Annex row, whether the AI filled it and is usually right
("ai"), a person should check it ("check"), only a person can know it and
it is empty ("needs"), a person entered or confirmed it ("you"), a person
said it rightly stays blank ("na"), or there is nothing to show ("none").

GET /reports/{id} and GET /intake/{id} send review_state()'s answer as
`review`; the dashboard's lib/review.ts types it (Review) and shows each
state with <ReviewBadge>. Sign-off warns while anything is open
(api/main.sign_off_report).

The measured basis (4 real cases, 89 runs): the AI reads the plate and the
make reliably, the model nearly always; colour, chassis/VIN and the
mileage only partly (and only from a document photo); the dates, the
requester, the claim type, the workshop and the static check are only a
person's to know. FIELD_RULES says which is which.

What a person confirmed is data["checked"] = {key: {by, by_id, at, na?}}
(PUT /reports/{id}/checked, PUT /intake/{id}/checked, and the wizard's
autosave). The firm's usual wording for the static check -- the same on
all four past reports -- fills a new draft's empty fields and is listed in
data["template_fields"], so it shows as "check" until someone confirms it.
Nothing here calls the AI or reads anything but the case.
"""
from datetime import datetime, timezone

from app.auth.core import display_name
from app.parts.prefill import origin_of, side_unconfirmed

# How a field is judged while nobody has confirmed it (lib/review.ts
# FieldRule): doc -- read from a document or plate photo, the AI is
# reliable; guess -- the AI's guess, check it; office -- only the office
# knows it; template -- the firm's usual wording, filled in, check it;
# optional -- may rightly stay blank. Keys are dotted paths into the case
# data, as CoverPageFields / PartiesStep / the Case record show them.
FIELD_RULES: dict[str, str] = {
    # Cover page 1 -- Reference
    "report_id": "office",  # Our Ref
    "cover_page.date_of_request": "office",
    "cover_page.requested_by": "office",
    "cover_page.requested_by_address": "office",
    "insurance_details.claim_type": "guess",
    "insurance_details.workshop_assigned": "guess",
    "cover_page.workshop_address": "office",
    "incident_datetime": "guess",
    "cover_page.date_of_survey": "office",
    "cover_page.date_of_dismantle": "optional",
    "cover_page.date_before_painting": "optional",
    "cover_page.date_after_painting": "optional",
    # 2 -- Details of vehicle
    "vehicle_info.plate_number": "doc",
    "vehicle_info.make": "doc",
    "vehicle_info.model": "doc",
    "vehicle_info.date_of_registration": "guess",
    "vehicle_info.engine_number": "guess",
    "vehicle_info.vin": "guess",
    "vehicle_info.odometer_reading": "guess",
    "vehicle_info.transmission": "guess",
    "vehicle_info.color": "guess",
    # 3 -- Static check
    "vehicle_info.steering_condition": "template",
    "vehicle_info.brakes_condition": "template",  # Footbrake
    "cover_page.handbrake_condition": "template",
    "vehicle_info.paintwork_condition": "template",
    "cover_page.modification": "template",
    "cover_page.general_condition": "template",
    # 4 -- Tyre condition: the whole table, one key
    "vehicle_info.tyres": "guess",
    # 5 -- Point of impact
    "vehicle_info.point_of_impact": "guess",
    "cover_page.point_of_impact_note": "optional",
    # Parties (wizard step 4, the Case record)
    "location": "guess",
    "accident_type": "guess",
    "insurance_details.insurer_name": "guess",
    "insurance_details.policy_number": "guess",
    "insurance_details.claim_number": "guess",
    "police_report.reported_to_police": "guess",
    "police_report.police_station": "optional",
    "police_report.report_number": "optional",
    "police_report.officer_name": "optional",
    "police_report.date_reported": "optional",
}

# Counted under counts.parties; every other FIELD_RULES key is the cover's.
PARTIES_KEYS = (
    "location", "accident_type",
    "insurance_details.insurer_name", "insurance_details.policy_number", "insurance_details.claim_number",
    "police_report.reported_to_police", "police_report.police_station", "police_report.report_number",
    "police_report.officer_name", "police_report.date_reported",
)

# Guess fields a case can't go without: empty is "needs", not "none".
NEEDED_GUESSES = ("insurance_details.insurer_name", "insurance_details.claim_type")

# The Annex keys a person may confirm or mark N/A, with the rule a form
# judges them by while editing. "annex_b" {na: true} is "No labour"; a
# plain confirm of it changes nothing (its lines are confirmed one by one).
ANNEX_C_KEYS = ("annex_c.agreed_lump_sum", "annex_c.repair_days")
ANNEX_RULES: dict[str, str] = {"annex_b": "office", **{key: "office" for key in ANNEX_C_KEYS}}
CHECKABLE_KEYS = frozenset(FIELD_RULES) | frozenset(ANNEX_RULES)

# The firm's usual static-check wording: the same on all four past reports'
# cover pages (example_case CL*.xls, Cover Page section 3), as the cover
# form's "Use the firm's usual values" button already offers it.
TEMPLATE_DEFAULTS: dict[str, str] = {
    "vehicle_info.steering_condition": "see annex(es)",
    "vehicle_info.brakes_condition": "see annex(es)",
    "cover_page.handbrake_condition": "see annex(es)",
    "vehicle_info.paintwork_condition": "Good",
    "cover_page.modification": "Nil",
    "cover_page.general_condition": "Good",
}

STATES = ("ai", "check", "needs", "you", "na", "none")


# ------------------------------------------------------------ values


def value_at(data: dict | None, key: str):
    """The value at a dotted key ("vehicle_info.plate_number"); None when
    any step is missing."""
    node = data
    for part in key.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def with_value(data: dict, key: str, value) -> dict:
    """A copy of `data` with `value` at a dotted key, copying each section
    on the way (the stored dicts are never changed in place: SQLAlchemy
    only sees a JSON column change on assignment)."""
    parts = key.split(".")
    out = dict(data or {})
    node = out
    for part in parts[:-1]:
        child = dict(node.get(part) or {}) if isinstance(node.get(part), dict) else {}
        node[part] = child
        node = child
    node[parts[-1]] = value
    return out


def is_blank(value) -> bool:
    """Nothing recorded: None, "", blanks, False (a "reported to police"
    left unticked says nothing), or a section / tyre table of blanks."""
    if value is None or value is False:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return all(is_blank(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(is_blank(v) for v in value)
    return False


def _norm(value):
    """For comparing a value with the AI's: text trimmed, blanks dropped,
    numbers as floats (a tyre's 6 and 6.0 are one reading)."""
    if isinstance(value, bool):
        return value or None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in sorted(value.items()) if not is_blank(v)} or None
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value if not is_blank(v)] or None
    return value


def same_value(a, b) -> bool:
    return _norm(a) == _norm(b)


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# ----------------------------------------------------------- checked


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def checked_mark(user, na: bool = False) -> dict:
    """One data["checked"] entry: who confirmed it (their display name, as
    the history shows people), their id, when, and na when they said the
    field rightly stays blank."""
    mark = {"by": display_name(user), "by_id": user.id, "at": _now()}
    if na:
        mark["na"] = True
    return mark


def _na(mark) -> bool:
    return isinstance(mark, dict) and bool(mark.get("na"))


def checked_of(data: dict | None) -> dict:
    checked = (data or {}).get("checked")
    return checked if isinstance(checked, dict) else {}


def merged_checked(incoming, stored, user) -> dict:
    """data["checked"] from a whole-draft save (the wizard's PUT
    /intake/{id}/data and its finalize), which carries the browser's copy.
    None (a client that doesn't know the field): the stored marks stay.
    Otherwise the keys sent are the confirmed ones: unknown keys dropped, a
    mark already stored with the same N/A kept as it was (who and when),
    any other stamped now as this person's -- never the browser's by/at."""
    stored = stored if isinstance(stored, dict) else {}
    if not isinstance(incoming, dict):
        return dict(stored)
    out = {}
    for key, mark in incoming.items():
        if key not in CHECKABLE_KEYS:
            continue
        na = _na(mark)
        before = stored.get(key)
        out[key] = before if isinstance(before, dict) and _na(before) == na else checked_mark(user, na)
    return out


def changed_checked(stored, set_marks: dict, clear: list, user) -> dict:
    """PUT .../checked: the stored marks with `clear` removed and `set`
    stamped now as this person's ({key: {na}}). Keys are checked by the
    caller (CHECKABLE_KEYS)."""
    out = dict(stored) if isinstance(stored, dict) else {}
    for key in clear:
        out.pop(key, None)
    for key, na in set_marks.items():
        out[key] = checked_mark(user, bool(na))
    return out


def keep_checked(current: dict, merged: dict) -> dict:
    """For a re-read (intake/pipeline.merge_redraft): the confirmations and
    the template list stay, and a confirmed field keeps the value it has
    now -- the AI never changes what a person confirmed."""
    checked = checked_of(current)
    if checked:
        merged["checked"] = dict(checked)
    if current.get("template_fields"):
        merged["template_fields"] = list(current["template_fields"])
    for key in checked:
        if key in FIELD_RULES:
            merged = with_value(merged, key, value_at(current, key))
    return merged


# ---------------------------------------------------------- template


def apply_template_defaults(data: dict) -> dict:
    """A new draft, and a case being filed: each EMPTY static-check field
    takes the firm's usual wording and is listed in template_fields
    ("check" until a person confirms it). Never overwrites a value, nor a
    field a person marked (checked)."""
    out = dict(data or {})
    checked = checked_of(out)
    listed = [k for k in (out.get("template_fields") or []) if isinstance(k, str)]
    for key, words in TEMPLATE_DEFAULTS.items():
        if key in checked or not is_blank(value_at(out, key)):
            continue
        out = with_value(out, key, words)
        if key not in listed:
            listed.append(key)
    if listed:
        out["template_fields"] = listed
    return out


# ------------------------------------------------------------- states


def _counts() -> dict:
    return {"total": 0, "you": 0, "ai": 0, "check": 0, "needs": 0, "na": 0}


def _tally(counts: dict, state: str) -> None:
    counts["total"] += 1
    if state in counts:
        counts[state] += 1


def _marked(mark, value) -> dict:
    """A field a person marked: "na" while it stays blank; once it has a
    value (an N/A given one since, or a confirmed value) it is "you"."""
    state = "na" if mark.get("na") and is_blank(value) else "you"
    return {"state": state, "by": mark.get("by"), "at": mark.get("at")}


def _field_state(key: str, data: dict, ai_draft: dict | None, photos: dict, checked: dict, signed_off: bool) -> dict:
    mark = checked.get(key)
    value = value_at(data, key)
    if isinstance(mark, dict):
        return _marked(mark, value)
    if signed_off:
        return {"state": "none"}
    rule = FIELD_RULES[key]
    if is_blank(value):
        if rule in ("office", "doc", "template") or key in NEEDED_GUESSES:
            return {"state": "needs"}
        return {"state": "none"}
    photo = photos.get(key.split(".", 1)[1]) if key.startswith("vehicle_info.") else None
    if ai_draft:
        ai_value = value_at(ai_draft, key)
        if not is_blank(ai_value) and same_value(value, ai_value):
            out = {"state": "ai" if rule == "doc" else "check"}
            if photo:
                out["photo"] = photo
            return out
    if key in (data.get("template_fields") or []) and same_value(value, TEMPLATE_DEFAULTS.get(key)):
        return {"state": "check"}
    # No AI draft at all (None): a case from before the AI's draft was
    # kept, whose values may well be the AI's -- nothing to say. An empty
    # one ({}: a wizard draft nobody has run the AI on) means a person
    # typed every value.
    if ai_draft is None:
        return {"state": "none"}
    return {"state": "you"}


def damage_state(item: dict, signed_off: bool) -> str:
    if signed_off:
        return "none"
    if item.get("human_verified"):
        return "you"
    if str(item.get("ai_confidence") or "").strip().lower() == "low" or side_unconfirmed(item):
        return "check"
    return "ai"


def annex_a_state(row: dict, damaged_parts: set, signed_off: bool) -> tuple[str, bool]:
    """(state, priced from past cases only), as the Annex A tab works it
    out live (dashboard lib/reviewRows.ts annexAState):
      - a line with no description: "none" (the save drops it);
      - stale (its part no longer damaged): "check" -- Keep or Remove;
      - no price: "needs", unless its Condition is Serviceable (any letter
        case): a part kept rightly has none;
      - a price from past cases (prefilled, estimate > 0): "check" until a
        person types or confirms the price (prefilled false) -- also after
        they edit only its Condition (origin "typed"): the owner's rule,
        past-case prices never count as confirmed;
      - otherwise "you" for a person's row (origin "typed"; a row saved
        before origins existed with a price nobody prefilled reads as
        typed, prefill.origin_of), "check" for one the machine put there
        nobody confirmed (detection, a suggestion, a looked-up price).
    Confirming is the Annex A save itself: prefilled false, origin "typed"."""
    if not str(row.get("part_name") or "").strip():
        return "none", False
    if signed_off:
        return "none", False
    if row.get("stale"):
        return "check", False
    priced = _number(row.get("workshop_est")) > 0
    if not priced and str(row.get("condition") or "").strip().lower() != "serviceable":
        return "needs", False
    if priced and row.get("prefilled"):
        return "check", True
    return ("you" if origin_of(row, damaged_parts) == "typed" else "check"), False


def review_state(report_data: dict | None, ai_draft: dict | None, photo_index: list | None, signed_off: bool) -> dict:
    """The Review shape of the dashboard's lib/review.ts. ai_draft: the
    AI's own draft (Report.ai_draft / IntakeSession.ai_draft); None when
    the case has none (filled fields then say nothing), {} for a draft
    the AI hasn't read (every value is a person's). photo_index: the
    case's data["photo_index"] (or the draft's sorted photos), for the
    photo an AI value was read from. signed_off: everything not confirmed
    is "none"."""
    from app.reports.filing import ai_read_for

    data = report_data if isinstance(report_data, dict) else {}
    checked = checked_of(data)
    _, photos = ai_read_for(ai_draft, photo_index) if ai_draft else (None, {})

    fields: dict = {}
    counts = {"cover": _counts(), "parties": _counts()}
    for key in FIELD_RULES:
        fields[key] = _field_state(key, data, ai_draft, photos, checked, signed_off)
        _tally(counts["parties" if key in PARTIES_KEYS else "cover"], fields[key]["state"])

    items = data.get("damage_summary")
    items = [it for it in items if isinstance(it, dict)] if isinstance(items, list) else []
    damage = [damage_state(it, signed_off) for it in items]
    counts["damage"] = _counts()
    for state in damage:
        _tally(counts["damage"], state)

    annex = data.get("annex_a") if isinstance(data.get("annex_a"), dict) else {}
    rows = [r for r in (annex.get("items") or []) if isinstance(r, dict)]
    damaged_parts = {str(it.get("part") or "").strip() for it in items}
    annex_a: list[str] = []
    a_counts = {"needs_price": 0, "history_price": 0, "check": 0, "you": 0}
    for row in rows:
        state, history = annex_a_state(row, damaged_parts, signed_off)
        annex_a.append(state)
        if state == "needs":
            a_counts["needs_price"] += 1
        elif state in ("check", "you"):
            a_counts[state] += 1
        if history:
            a_counts["history_price"] += 1
    counts["annex_a"] = a_counts

    # Annex B (as lib/reviewRows.ts): one item per described line -- a
    # prefilled one (a past case's price) "check" until a person edits it
    # (prefilled false), else "you" -- or, with no lines, the table as one
    # item: "na" once a person said there is no labour (checked "annex_b"
    # {na: true}), else "needs".
    annex_b = data.get("annex_b") if isinstance(data.get("annex_b"), dict) else {}
    lines = [it for it in (annex_b.get("items") or [])
             if isinstance(it, dict) and str(it.get("description") or "").strip()]
    b_mark = checked.get("annex_b") if isinstance(checked.get("annex_b"), dict) else None
    if lines:
        line_states = ["none" if signed_off else "check" if it.get("prefilled") else "you" for it in lines]
        b_state = next((s for s in ("check", "you") if s in line_states), "none")
    else:
        line_states = ["na" if _na(b_mark) else "none" if signed_off else "needs"]
        b_state = line_states[0]
    b_counts = _counts()
    for state in line_states:
        _tally(b_counts, state)
    fields["annex_b"] = {"state": b_state}
    if b_mark is not None and b_state == "na":
        fields["annex_b"].update(by=b_mark.get("by"), at=b_mark.get("at"))
    counts["annex_b"] = b_counts

    # Annex C: the lump sum and the working days, a person's alone.
    annex_c = data.get("annex_c") if isinstance(data.get("annex_c"), dict) else {}
    c_counts = _counts()
    for key in ANNEX_C_KEYS:
        mark = checked.get(key)
        value = annex_c.get(key.split(".", 1)[1])
        if isinstance(mark, dict):
            field = _marked(mark, value)
        elif signed_off:
            field = {"state": "none"}
        else:
            field = {"state": "needs" if value is None else "you"}
        fields[key] = field
        _tally(c_counts, field["state"])
    counts["annex_c"] = c_counts

    needs, check = open_items(counts)
    return {
        "fields": fields,
        "rules": {**FIELD_RULES, **ANNEX_RULES},
        "damage": damage,
        "annex_a": annex_a,
        "counts": counts,
        "open": needs + check,
    }


def open_items(counts: dict) -> tuple[int, int]:
    """(needs you, to check) over every section of review["counts"]."""
    needs = sum(counts[s]["needs"] for s in ("cover", "parties", "damage", "annex_b", "annex_c")) + counts["annex_a"]["needs_price"]
    check = sum(counts[s]["check"] for s in ("cover", "parties", "damage", "annex_a", "annex_b", "annex_c"))
    return needs, check


_SECTION_WORDS = (
    ("cover", "cover"), ("parties", "parties"), ("damage", "damage"),
    ("annex_a", "Annex A"), ("annex_b", "Annex B"), ("annex_c", "Annex C"),
)


def open_summary(review: dict) -> str:
    """For the sign-off's history entry: "no items open", or "7 items
    open: cover 4, Annex A 2, Annex C 1"."""
    counts = review["counts"]
    parts = []
    for key, words in _SECTION_WORDS:
        c = counts[key]
        n = c["check"] + (c["needs_price"] if key == "annex_a" else c["needs"])
        if n:
            parts.append(f"{words} {n}")
    total = review["open"]
    if not total:
        return "no items open"
    return f"{total} item{'s' if total != 1 else ''} open: " + ", ".join(parts)
