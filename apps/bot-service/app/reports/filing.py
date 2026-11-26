"""The filing step every channel shares (contract2, 2026-11-04).

A case reaches the database by three roads -- the dashboard's intake wizard
(api/intake._finalize_claimed), the staff Telegram bot
(channels/telegram._finalize_report) and WhatsApp (channels/whatsapp.
_finalize) -- and until now only the wizard's ended with Annex A rows and
prices. A bot-filed case opened with an empty Annex A. file_case_data is
the tail they all run, so every filed case starts the same way:

  - sign_off blank: filing signs nothing off (api/main.blank_sign_off);
  - Annex A/B rebuilt from the confirmed damage list (the ones drafted
    from the AI's first list would otherwise survive the person changing
    it) and Annex A synced from past cases (parts/prefill.sync_annex_a):
    priced for someone who may see the price book, one unpriced row per
    part for anyone else -- and, for everyone, the filer's own prices
    first (Stage 1, own_prices_by);
  - data["photo_index"]: what each photo is, in P-number order, from the
    wizard's sort step; kind None for bot photos, which were never sorted,
    so a later re-detection (Phase 2) sorts them first;
  - the static check's empty fields take the firm's usual wording
    (review.apply_template_defaults), listed in data["template_fields"]
    so the case shows them as "check" until a person confirms them.

The AI's own draft (Report.ai_draft) is the caller's to store: the bot has
no Report row yet when this runs (it files before the PDF renders). The
wizard's caller turns its photo links into P-numbers first
(relabel_ai_draft).
"""
import copy
import logging

from app.auth.permissions import can_view_all_cases
from app.intake.photos import photo_label
from app.parts.prefill import sync_annex_a
from app.reports.db import SessionLocal
from app.reports.knowledge import case_stamp
from app.reports.review import apply_template_defaults, is_blank
from app.reports.schema import SignOffInfo
from app.reports.synthesize_annexes import synthesize_annexes_for_report

logger = logging.getLogger(__name__)

# Per photo, from the sort step (models.IntakePhoto); every one None when
# the photo was never sorted.
PHOTO_INDEX_KEYS = ("kind", "area", "part", "shows_damage", "quality")
# The AI draft's lists whose items name photos: the damage list (contract
# 8's seen_in, contract 9's evidence), the parts only zone reads found
# (contract 9), the linked parts kept in shadow mode (contract 10) and the
# car map's in its shadow mode (K4).
AI_DRAFT_PHOTO_LISTS = ("damage_summary", "zone_suggestions", "link_suggestions", "car_map_suggestions",
                        # Wave 2 E7 (settings.intake_side_geometry): the side
                        # resolver's changes (pipeline.resolve_sides).
                        "side_geometry",
                        # Detection Round 2 (settings.intake_zone_inventory):
                        # the zone reads' inventory (intake/zones.add_inventory).
                        "zone_inventory",
                        # Detection Round 1 A1 in shadow (settings.
                        # intake_sorter_hints): the sorter's finds kept apart.
                        "sorter_suggestions")


def relabel_ai_draft(ai_draft: dict | None, index_of: dict) -> dict | None:
    """The AI's draft as filed: its "@<photo id>" references (the pipeline
    keeps them so until the order is fixed) as the case's P-numbers, by the
    map the data gets (suggestions.relabel_item; a deleted photo's
    references dropped) -- in each of AI_DRAFT_PHOTO_LISTS, frozen there as
    "what the AI said". A copy; None or {} as it is."""
    from app.reports.suggestions import relabel_item

    if not ai_draft:
        return ai_draft
    out = copy.deepcopy(ai_draft)
    for key in AI_DRAFT_PHOTO_LISTS:
        if isinstance(out.get(key), list):
            out[key] = [relabel_item(item, index_of) for item in out[key] if isinstance(item, dict)]
    if isinstance(out.get("zone_inventory"), list):
        # Detection Round 2: an inventory row whose photo was deleted keeps
        # no box either (the R2 review's L4).
        out["zone_inventory"] = [{**row, "box": None} if not row.get("photo_reference") and row.get("box") else row
                                 for row in out["zone_inventory"]]
    if isinstance(out.get("assembly_walk"), dict):
        # The assembly walk (K3b): each sign's photo as the case's P-number
        # (a deleted photo's gone, the answer kept), so a re-link on the
        # filed case names its yes signs' photos as the case does.
        out["assembly_walk"] = relabel_walk(out["assembly_walk"], index_of)
    return out


def relabel_walk(walk: dict, index_of: dict) -> dict:
    """ai_draft["assembly_walk"] ({panel: {hit_at, signs: {id: {answer,
    photo, zone}}}}) with each sign's "@<photo id>" as the case's P-number.
    Anything that isn't a panel's record is left as it is."""
    from app.reports.suggestions import final_label

    out = {}
    for panel, rec in walk.items():
        if isinstance(rec, dict) and isinstance(rec.get("signs"), dict):
            rec = {**rec, "signs": {sid: ({**got, "photo": final_label(got.get("photo"), index_of)}
                                          if isinstance(got, dict) and got.get("photo") else got)
                                    for sid, got in rec["signs"].items()}}
        out[panel] = rec
    return out


def photo_index_for(photo_kinds: list[dict] | None, ai_draft: dict | None, read_ids=None) -> list[dict]:
    """data["photo_index"]: one entry per case photo, positional (P01 is
    photo_paths[0]; append only). For a photo nobody sorted, the AI's own
    photo references are the one thing known about it: the damage item it
    stands for says the photo shows that part.

    read_ids: the photos the AI's last reading was sent (the wizard's
    IntakeSession.read_photo_ids, which goes when the draft is filed),
    matched against each photo_kinds entry's "id": those get read: true,
    so the case keeps which photos the facts were read from. None (the
    bots): no "read" key."""
    index = [
        {"label": photo_label(i), **{key: (p or {}).get(key) for key in PHOTO_INDEX_KEYS}}
        for i, p in enumerate(photo_kinds or [])
    ]
    if read_ids:
        for entry, p in zip(index, photo_kinds or []):
            if (p or {}).get("id") in read_ids:
                entry["read"] = True
    for entry, p in zip(index, photo_kinds or []):
        # B5: the sorter's own kind of a photo a person regrouped
        # (IntakePhoto.ai_kind), only where there is one.
        if isinstance(p, dict) and p.get("ai_kind"):
            entry["ai_kind"] = p["ai_kind"]
    unsorted = {entry["label"]: entry for entry in index if entry["kind"] is None}
    for item in (ai_draft or {}).get("damage_summary") or []:
        entry = unsorted.get(item.get("photo_reference"))
        if entry is not None:
            entry["shows_damage"] = True
            entry["part"] = entry["part"] or item.get("part")
    return index


def price_book_for(user, owner=None) -> bool:
    """Whether the price book may be written into a case filed by `user`
    for `owner` (None: the filer, or nobody): both must see it. An admin
    finishing a self-signup's draft files it unpriced, as that owner reads
    its Annex A (api/parts.price_book_for_case says the same for edits)."""
    return can_view_all_cases(user) and (owner is None or can_view_all_cases(owner))


def own_prices_by(user, owner=None) -> str | None:
    """Whose own prices ("My prices", Stage 1) fill a case `user` files
    for `owner`: the filer's -- for everyone (owner P3) -- when the case is
    theirs (owner None or the filer), or someone else's where the price
    book may go too (price_book_for). None for nobody's."""
    user_id = getattr(user, "id", None)
    if not user_id:
        return None
    if owner is None or getattr(owner, "id", None) == user_id or price_book_for(user, owner):
        return user_id
    return None


def file_case_data(db, data: dict, user, *, ai_draft: dict | None, photo_kinds: list[dict] | None,
                   owner=None, report_id: str | None = None, read_ids=None) -> dict:
    """The case data as it is filed: sign-off blank, annexes rebuilt from
    the damage list and Annex A priced when `user` -- and the case's
    `owner`, when that is someone else -- may see the price book
    (price_book_for), photo_index from `photo_kinds` (one dict per photo
    in order, {} for a photo never sorted; None for no photos).

    `db` is the caller's transaction and is left alone: the price-book
    read runs on a session of its own, so a failure there can't poison
    the filing -- the case is filed with unpriced rows rather than not.
    `ai_draft` is what the AI said (the caller stores it as
    Report.ai_draft); here it only labels unsorted photos. `report_id`
    names the case in the log, and finds its AI runs: the knowledge stamp
    of what made its draft (data.knowledge, reports/knowledge.case_stamp;
    L-library D.3, step 2B), read on a session of its own like the prices
    and never in the filing's way. `read_ids`: photo_index_for's.
    """
    data = dict(data)
    # data["second_look"] -- the AI's suggestions and what people decided
    # about them -- is kept (contract 9, conflict #8): a surveyor may still
    # decide on the case page until sign-off, and the AI-accuracy record
    # counts them then. The wizard's caller has turned its photo links into
    # P-numbers (api/intake._finalize_claimed); the bots have none.
    data["sign_off"] = SignOffInfo().model_dump()
    # Only the fields still empty, and none a person marked (data.checked).
    data = apply_template_defaults(data)
    data["annex_a"] = {}
    data["annex_b"] = {}
    data = synthesize_annexes_for_report(data)
    prices = SessionLocal()
    try:
        data = sync_annex_a(prices, data, price_book=price_book_for(user, owner), user_id=own_prices_by(user, owner))
    except Exception:
        logger.exception("Could not sync Annex A from past cases for case %s; its rows stay unpriced", report_id or "(new)")
    finally:
        prices.close()
    data["photo_index"] = photo_index_for(photo_kinds, ai_draft, read_ids)
    if report_id:
        runs = SessionLocal()
        try:
            data["knowledge"] = case_stamp(runs, report_id)
        except Exception:
            logger.exception("Could not stamp the knowledge versions of case %s at filing", report_id)
        finally:
            runs.close()
    return data


# The cover fields the AI reads off the photos. The case page and the
# wizard tag such a field "(P03)" while its value is still the AI's. The
# tyre table is one value (review.FIELD_RULES "vehicle_info.tyres").
AI_COVER_FIELDS = (
    "plate_number", "make", "model", "date_of_registration", "engine_number", "vin", "odometer_reading",
    "transmission", "color", "paintwork_condition", "steering_condition", "brakes_condition", "point_of_impact",
    "tyres",
)
# The other facts the AI guesses (review.FIELD_RULES "guess"): top-level,
# and per section. Police: only whether it was reported.
AI_TOP_FIELDS = ("incident_datetime", "location", "accident_type")
AI_SECTION_FIELDS = {
    "insurance_details": ("claim_type", "workshop_assigned", "insurer_name", "policy_number", "claim_number"),
    "police_report": ("reported_to_police",),
}
# Which sorted photo a field is read from, by kind (ai/triage.PHOTO_KINDS),
# first choice first. Only fields a document photo states outright: the
# log card carries the plate, make, model, registration date, engine and
# chassis numbers and the colour; the instrument cluster the mileage.
# Nothing for what the AI judges from the car as a whole (conditions,
# the point of impact, the transmission).
_FIELD_PHOTO_KINDS = {
    "plate_number": ("log_card",), "make": ("log_card",), "model": ("log_card",),
    "date_of_registration": ("log_card",), "color": ("log_card",),
    "engine_number": ("vin_plate", "log_card"), "vin": ("vin_plate", "log_card"),
    "odometer_reading": ("odometer",),
}


def ai_read_for(ai_draft: dict | None, photo_index: list[dict] | None) -> tuple[dict | None, dict]:
    """(ai_read, ai_read_photos) for GET /reports/{id} and GET /intake/{id}:
    the AI's non-empty values for the cover and parties fields
    ({vehicle_info: {...}, incident_datetime, location, accident_type,
    insurance_details: {...}, police_report: {...}}; a top-level one None
    and a section {} when the AI left it empty; notes: {field: the AI's
    own word} for a static-check or transmission word the firm's word
    replaced or that was kept for a person, INTAKE_FIRM_WORDS on,
    app/reports/firm_words.py), and per vehicle field the
    photo it was read from ("P03") when the photo index can tell. (None,
    {}) with no draft. Nothing here is ever printed, and nothing comes from
    anywhere but the AI's own draft."""
    if not ai_draft:
        return None, {}
    vehicle = ai_draft.get("vehicle_info") or {}
    values = {key: vehicle[key] for key in AI_COVER_FIELDS if not is_blank(vehicle.get(key))}
    read = {"vehicle_info": values}
    for key in AI_TOP_FIELDS:
        read[key] = None if is_blank(ai_draft.get(key)) else ai_draft.get(key)
    for section, keys in AI_SECTION_FIELDS.items():
        found = ai_draft.get(section) or {}
        read[section] = {key: found[key] for key in keys if not is_blank(found.get(key))}
    # The AI's own static-check / transmission words (firm_words.
    # apply_firm_words, INTAKE_FIRM_WORDS on), by vehicle_info field: a
    # note beside the field, never printed. Only when there are.
    words = ai_draft.get("ai_words")
    notes = {key: word.strip()[:80] for key, word in words.items()
             if key in AI_COVER_FIELDS and isinstance(word, str) and word.strip()} if isinstance(words, dict) else {}
    if notes:
        read["notes"] = notes
    first_of_kind: dict[str, str] = {}
    for entry in photo_index or []:
        if entry.get("kind") and entry.get("label"):
            first_of_kind.setdefault(entry["kind"], entry["label"])
    photos = {}
    for key in values:
        label = next((first_of_kind[kind] for kind in _FIELD_PHOTO_KINDS.get(key, ()) if kind in first_of_kind), None)
        if label:
            photos[key] = label
    return read, photos
