"""Pydantic schema for the incident report draft.

Mirrors the skeleton shared by both sample templates in Resource/ (reporter,
incident metadata, description, people/witnesses, actions, category
checklist) -- see docs/proposal.md section I for the field-by-field mapping.

`accident_type` / `damaged_parts` / `severity_level` / `vehicle_details` are
a lightweight, pragmatic bridge toward Phase 2's vehicle-claims work so this
one form can already handle a simple car-damage report -- they're optional
and only populated when the incident is vehicle-related. The full Phase 2
data model (Vehicle/Claim/DamageItem as their own entities, part-price
lookup, etc.) in docs/proposal.md section F is still the target once claims
volume justifies the added complexity; don't add more claim-specific fields
here without checking whether that point has arrived.
"""
from typing import Annotated, Optional
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, Field, StringConstraints, field_validator, model_serializer, model_validator
from pydantic.json_schema import SkipJsonSchema

from app.reports.taxonomy import OTHER_PART, clean_part_name

# The parts sites the catalogue worker reads (app/parts/catalogue.py,
# plans/2026-11-12-parts-catalogue/CONTRACT.md): the three Microfiches
# sites, so the only hosts a catalogue link may point to, with or without
# "www." -- the same list as the worker's (tools/parts_mcp/worker.py
# ALLOWED_HOSTS; both checked against tests/fixtures/catalogue_links.json).
CATALOGUE_HOSTS = frozenset({"bmw-spare-parts.com", "mini-spare-parts.com", "parts-honda.uk"})
CATALOGUE_TEXT_MAX = 400


def catalogue_link(value: str | None) -> str | None:
    """A link from the parts catalogues: https on one of CATALOGUE_HOSTS,
    no user name or password, the standard port -- anything else is
    refused (ValueError), never stored, so no page ever shows it as a
    link. The worker's result and an Annex A line's catalogue_ref are
    checked by this one rule (the worker nulls what it would refuse:
    tools/parts_mcp/worker.safe_url)."""
    if value is None:
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise ValueError("links must be https:// on the parts sites") from None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443):
        raise ValueError("links must be https:// on the parts sites")
    if host.removeprefix("www.") not in CATALOGUE_HOSTS:
        raise ValueError(f"links must be on {', '.join(sorted(CATALOGUE_HOSTS))} (not {host or 'no host'})")
    return value


_CatalogueText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=CATALOGUE_TEXT_MAX)]


class PersonInvolved(BaseModel):
    name: str
    role: str = Field(default="Participant", description="driver, passenger, owner, surveyor, witness, admin staff, police officer, or other")
    department: Optional[str] = None
    contact: Optional[str] = None


class Witness(BaseModel):
    name: str
    contact: Optional[str] = None
    statement: Optional[str] = None
    observation_time: Optional[str] = None


# ------------------------------------------------ contract 9: evidence
# A reason, a photo box and a 3D link for every finding (plans/2026-11-15-
# evidence/contract9.md section 4.1). Written by code, never by the model:
# none of these is in a schema Gemini sees, and the drafting schema shared
# with the Telegram bot stays byte for byte as it was (the fields that
# carry them below are SkipJsonSchema). Lenient on purpose -- defaults
# everywhere, no validator that raises -- so a whole-draft save or a case
# from before never fails on them; the data is cleaned where it is made
# (app/ai/evidence.py).


class Evidence(BaseModel):
    """One photo's proof for one finding."""
    # "@<photo id>" in a draft, "P07" once filed (api/intake relabels it).
    photo: str
    # [ymin, xmin, ymax, xmax], ints 0..1000, on the upright AI-input frame
    # (the 1600 px preview). None: no box on this photo.
    box: Optional[list[int]] = None
    # "ai" | "sort" | None -- None exactly when box is None.
    box_kind: Optional[str] = None
    # Which read said so: "overall" | "zone:front|rear|left|right|close" |
    # "sort" | "check" | "second_look". Contract 10: "link" -- the photo
    # of the listed part a linked part hangs off (no box); "link_check" --
    # the linked check's photo, with its box when it drew one. Detection
    # Round 2: "locate:zone:<zone>" -- a zone read's box round a listed
    # part it saw (where it is, not a find: intake/zones.place_boxes).
    by: str = "overall"
    # That source's words, cleaned (evidence.clean_reason), at most 160
    # characters.
    reason: Optional[str] = None
    # "High" | "Medium" | "Low", from that source.
    confidence: Optional[str] = None
    # The size of the image the box was drawn on: the UI hides a box drawn
    # on another frame (no preview, a rotated photo).
    img_w: Optional[int] = None
    img_h: Optional[int] = None


class SuggestionCheck(BaseModel):
    """The re-check's answer on one suggestion (app/ai/check.py)."""
    # "yes" | "no" | "unsure"
    verdict: str
    # The Decisions API's yes-probability, 0..1; None from the lite re-check.
    p: Optional[float] = None
    # "decision" | "lite"
    by: str
    model: Optional[str] = None
    # The lite re-check's words only, cleaned, at most 160 characters.
    reason: Optional[str] = None


class SuggestionDecision(BaseModel):
    """A person's add / dismiss / undo on one suggestion. Stamped by the
    server's decide endpoint, never taken from a browser's draft."""
    # The suggestion's key (prefill.item_key).
    key: str
    # Its canonical part: history and AI accuracy keep names only.
    part: str
    # "added" | "dismissed" | "undone"
    action: str
    by_id: Optional[str] = None
    by_name: Optional[str] = None
    # ISO 8601 UTC, the server's clock.
    at: str
    # The person opened the evidence viewer on it before deciding.
    looked: bool = False
    # "intake" | "case"
    where: str = "intake"


class CheckRun(BaseModel):
    """What the re-check step did on this read."""
    # "ok" | "skipped" | "failed"
    status: str = "skipped"
    # "decision" | "lite"
    by: Optional[str] = None
    model: Optional[str] = None
    # Plain words, shown on the suggestions panel.
    note: Optional[str] = None
    seconds: Optional[float] = None
    calls: int = 0
    # The linked check's "Signs of hidden damage" answers, by area
    # (settings.intake_link_signs, L3 of plans/2026-11-18-light-detection):
    # {"rear": [{"n": "1", "answer": "yes", "photo": "P03"}]}, the photo as
    # labelled in that call. A log only; left out of a dump while None, so a
    # run without them saves what it did before.
    signs: Optional[dict] = None

    @model_serializer(mode="wrap")
    def _without_unset_signs(self, handler):
        return _without_defaults(handler(self), {"signs": None})


# ------------------------------------------------ contract 10: linked parts
# The parts tied to a listed part -- the beam behind a bumper cover, the
# hinges a boot lid opens with -- suggested with it (plans/2026-11-17-
# linked-parts/contract10.md section 4.1; intake/links.py, ai/link_check.py).
# Code-set, lenient and out of every schema Gemini sees, as contract 9's.
# While a field below is at its default it is left out of a dump (the
# models' _without_unset_links), so a case with the linked parts off saves
# byte for byte what it did before them: read them with .get().


class SignYes(BaseModel):
    """A sign the assembly walk answered "yes" for a linked part (the car
    map, K1-design.md 5a): its id, its question, the photo it was seen in
    ("@<photo id>" while a draft, "P07" once filed)."""
    id: str
    ask: Optional[str] = None
    photo: Optional[str] = None


class LinkCheck(BaseModel):
    """The linked check's answer on one linked part (ai/link_check.py)."""
    # "seen" | "likely" | "strip_down" | "no" | "cannot_tell"
    verdict: str
    # What it looked for first: "crush" | "gap_misfit" | "displaced" |
    # "cracked" | "none_seen" | "cannot_see".
    cue: Optional[str] = None
    model: Optional[str] = None
    # Its words, cleaned (evidence.clean_reason), at most 160 characters.
    reason: Optional[str] = None
    # F6 (settings.intake_link_hidden_no "cannot_tell"; the review's L8):
    # the model's own verdict when a code rule changed it ("no" on a hidden
    # part, now "cannot_tell"). Not in a dump while None, so a check made
    # without the rule saves what it did before.
    model_verdict: Optional[str] = None

    @model_serializer(mode="wrap")
    def _without_unset_model_verdict(self, handler):
        return _without_defaults(handler(self), {"model_verdict": None})


# Each linked-part field with its default, per model: what
# _without_defaults leaves out of a dump.
LINK_ITEM_DEFAULTS = {"linked_from": [], "link_type": None, "hidden": False, "needs_strip_down": False}
LINK_SUGGESTION_DEFAULTS = {
    "linked_from": [], "link_type": None, "link_rule": None, "hit": None, "bucket": None, "hidden": False,
    "needs_strip_down": False, "side_from": None, "intact_in": [], "link_check": None, "oem": [],
    # The car map's review screen (K4): only with intake_car_map "on".
    "assembly": None, "assembly_name": None, "prior": None, "hop": None, "via": None, "fitted": None,
    "fitted_note": None, "signs_yes": [], "also_check": [], "folded": False,
}
LINK_LOOK_DEFAULTS = {"link_run": None}
# plans/2026-11-22-enhance-staging's fixes, the same way: left out of a dump
# while at their default, so a draft made with the fixes off saves what it
# did before them (read them with .get()). F3: other_side_of; F2/F5:
# cut_reason on a cut item, and SecondLook.cut / cut_counts.
FIX_SUGGESTION_DEFAULTS = {"other_side_of": None, "cut_reason": None}
FIX_LOOK_DEFAULTS = {"cut": [], "cut_counts": {}}
# Wave 2 (plans/2026-11-22-enhance-staging 3c), the same way. E11:
# wrong_end_note on a "wrong end" zone read's suggestion.
WAVE2_SUGGESTION_DEFAULTS = {"wrong_end_note": None}
# Detection Round 2 (settings.intake_zone_inventory), the same way:
# inventory_note on a suggestion from a zone read's inventory.
R2_SUGGESTION_DEFAULTS = {"inventory_note": None}


def _without_defaults(dumped, defaults: dict):
    """A model's dump without the fields of `defaults` still at their
    default. Leaves anything but a dict alone."""
    if isinstance(dumped, dict):
        for name, empty in defaults.items():
            if name in dumped and dumped[name] == empty:
                del dumped[name]
    return dumped


class DamageSummaryItem(BaseModel):
    # Deliberately no example list here. The allowed values are supplied to
    # the model as a schema enum from taxonomy.CANONICAL_PARTS; examples in
    # this description previously contradicted that list ("Boot/Trunk" vs
    # "Boot Lid", "Headlight" vs "Left Headlamp"), giving the model two
    # different answers to the same question.
    part: str = Field(description="The damaged part. Choose the closest match from the allowed values; side is part of the name where it applies.")
    # What an "Other / Not Listed" part is, so Annex A can print and price
    # it (prefill.sync_annex_a: through history.family_of when the name
    # reads as a known family) and two unlisted parts stay two rows. Before
    # it, every such item printed as one unpriced "Other part (to be
    # named)". Right after part, and a short description: the drafting
    # schema is also read by flash-lite models, which cope worse with a
    # longer one. Cleared on any other part (the name is the part's).
    other_part_name: Optional[str] = Field(default=None, description="Only with 'Other / Not Listed': the part's name, e.g. 'Rear air vent'.")
    damage_type: Optional[str] = Field(default=None, description="How the part is damaged -- choose from the allowed values. Pick the dominant type for compound damage and leave the nuance to the description. Only set if actually known.")
    # The firm's own words for the damage (taxonomy.FIRM_CONDITION_TOKENS),
    # which the Annex A Condition column prints joined: ["Grazed", "Cut"] ->
    # "Grazed/cut". A plain array with an enum on its items and no
    # minItems/maxItems: the drafting schema is shared with the Telegram
    # bot, and a schema Gemini finds too complex fails EVERY draft with 400
    # (extraction._NOT_MODEL_OUTPUT). Always the words of `condition` once
    # drafted (empty for Necessary / Serviceable / Repair).
    condition_tokens: list[str] = Field(default_factory=list, description="The firm's words for this damage, 1 to 3 from the allowed values.")
    # Built in code from condition_tokens (extraction._firm_condition), never
    # by the model, so it is left out of the schema Gemini sees; a person may
    # change it (PATCH /damage-items: firm words only). None on cases filed
    # before it existed: readers fall back through taxonomy.annex_condition.
    condition: SkipJsonSchema[Optional[str]] = None
    severity: Optional[str] = Field(default=None, description="Minor, Moderate, or Severe -- only set if actually assessable from the photo/description")
    # The Annex A Remarks column for this part's main row: "Replace" or
    # "Repair" (taxonomy.FIRM_REMARKS), null when unsure.
    remarks: Optional[str] = Field(default=None, description="Replace or Repair; null if unsure.")
    # True when intake/pipeline.resolve_sides placed a "(side undetermined)"
    # part on a side from the sorter's photo areas rather than the reader
    # seeing it; cleared by a person's edit. Never the model's, so not in
    # its schema.
    side_inferred: SkipJsonSchema[bool] = False
    photo_reference: Optional[str] = Field(
        default=None,
        description="Which uploaded photo actually shows this damage, e.g. 'P01' for the first photo, 'P02' for the second. Photos are given to you in that order -- only set this when you can genuinely tell which photo shows the part; leave null if unclear or if it's not visible in any single photo.",
    )
    # box_2d / [y_min, x_min, y_max, x_max] on a 0-1000 scale is Gemini's
    # own native, specifically-trained bounding-box convention (see
    # https://github.com/google/skills/blob/main/skills/cloud/gemini-api/references/bounding_box.md)
    # -- a custom top/left/width/height-as-percentage format (tried first)
    # made the model translate out of its trained convention on every
    # call, which likely explains why it almost never committed to a box.
    # A flat list also avoids the nested-object $ref that separately broke
    # damage_summary entirely (see git history on this field).
    bbox_2d: Optional[list[int]] = Field(
        default=None,
        description="This damage's bounding box within its photo_reference photo, as [y_min, x_min, y_max, x_max] with each value normalized 0-1000 (top-left origin). Only set alongside photo_reference, and only when you can genuinely localize the damage -- never a rough or centered guess.",
    )
    ai_confidence: Optional[str] = Field(
        default=None,
        description="The model's own 'High', 'Medium', or 'Low' assessment of how clearly it can identify this specific damage from the photo. A qualitative self-assessment, not a fabricated-looking precise percentage.",
    )

    @field_validator("bbox_2d", mode="before")
    @classmethod
    def _whole_box(cls, value):
        # A model now and then writes 100.2: list[int] would refuse it and
        # with it the whole draft (the chain then asks the next model). Round
        # fractions; anything else stays for evidence.clean_box to judge.
        # A validator, not a float type, so the schema Gemini sees is unchanged.
        if isinstance(value, list):
            return [round(v) if isinstance(v, float) else v for v in value]
        return value

    human_verified: bool = False
    repair_required: bool = False
    oem_part_number: Optional[str] = Field(
        default=None,
        description="OEM replacement part number, e.g. '71501-T7A-A00' -- never invent this. Only set it if the reporter or surveyor actually supplied it; a human enters this during review, it is never looked up or guessed.",
    )
    # Contract 8 D2 (the shared data contract): which reads saw this part,
    # set by intake/zones.py when settings.intake_read_mode is "zones" and
    # empty otherwise -- and on every case from before. Written by code,
    # never by the model, so left out of the schema Gemini sees: the
    # drafting schema, shared with the Telegram bot, must not change
    # (extraction._NOT_MODEL_OUTPUT). seen_in: the photos the part was seen
    # in across the reads, written as photo_reference is at the time
    # ("@<photo id>" in a draft, "P07" once filed: api/intake relabels both).
    # found_by: "overall" and/or "zone:front", "zone:rear", "zone:left",
    # "zone:right", "zone:close". grid_cells: the cells (A1..C3) of
    # photo_reference the damage covers, when a zone read had grid marks
    # (settings.intake_grid_marks).
    seen_in: SkipJsonSchema[list[str]] = Field(default_factory=list)
    found_by: SkipJsonSchema[list[str]] = Field(default_factory=list)
    grid_cells: SkipJsonSchema[list[str]] = Field(default_factory=list)
    # Contract 9 (section 4.1), code-set like the three above and left out
    # of the drafting schema the same way. reason: the AI's own words for
    # why this part is damaged, from the first evidence entry that has one
    # (a zone read's tail field, the re-check), at most 160 characters.
    # evidence: at most 4, one per photo, best first; [0] is
    # photo_reference's. While bbox_2d is empty and evidence[0] is on
    # photo_reference with a box, code fills bbox_2d from it, so today's
    # UI keeps drawing it; a person changing the photo clears bbox_2d and
    # leaves evidence alone (the AI's record). check: copied from the
    # suggestion when a person adds it; added_from: "suggestion" then.
    reason: SkipJsonSchema[Optional[str]] = None
    evidence: SkipJsonSchema[list[Evidence]] = Field(default_factory=list)
    check: SkipJsonSchema[Optional[SuggestionCheck]] = None
    added_from: SkipJsonSchema[Optional[str]] = None
    # Contract 10 (section 4.1), code-set and out of the drafting schema
    # like the fields above: copied from a linked-part suggestion when a
    # person adds it. linked_from: the keys of the listed parts it hangs
    # off; link_type: "attached" | "behind" | "opens_with" | "crush";
    # hidden: not seen in photos as they are usually taken; needs_strip_down:
    # confirmed only once parts come off, shown as "Confirm at strip-down"
    # until a person edits its condition. Not in a dump while at their
    # defaults.
    linked_from: SkipJsonSchema[list[str]] = Field(default_factory=list)
    link_type: SkipJsonSchema[Optional[str]] = None
    hidden: SkipJsonSchema[bool] = False
    needs_strip_down: SkipJsonSchema[bool] = False

    @field_validator("other_part_name", mode="before")
    @classmethod
    def _clean_other_name(cls, value):
        return clean_part_name(value)

    @model_validator(mode="after")
    def _other_name_only_for_other(self):
        if (self.part or "").strip() != OTHER_PART:
            self.other_part_name = None
        return self

    @model_serializer(mode="wrap")
    def _without_unset_links(self, handler):
        return _without_defaults(handler(self), LINK_ITEM_DEFAULTS)


class TyreItem(BaseModel):
    tread_depth_mm: Optional[float] = Field(default=None, description="Tread depth in mm (e.g. 6.0)")
    brand: Optional[str] = Field(default=None, description="Tyre manufacturer e.g. Dunlop, Michelin, Goodride")
    size: Optional[str] = Field(default=None, description="Tyre size specification e.g. 215/60 R16")
    condition: Optional[str] = Field(default=None, description="Good, Worn, LTA Violation (<1.6mm)")


class TyreCondition(BaseModel):
    front_nearside: Optional[TyreItem] = Field(default_factory=TyreItem, description="Front Left tyre")
    front_offside: Optional[TyreItem] = Field(default_factory=TyreItem, description="Front Right tyre")
    rear_nearside: Optional[TyreItem] = Field(default_factory=TyreItem, description="Rear Left tyre")
    rear_offside: Optional[TyreItem] = Field(default_factory=TyreItem, description="Rear Right tyre")


class ThirdPartyInfo(BaseModel):
    plate_number: Optional[str] = None
    make_model: Optional[str] = None
    driver_name: Optional[str] = None
    driver_nric: Optional[str] = None
    driver_contact: Optional[str] = None
    insurer_name: Optional[str] = None
    policy_number: Optional[str] = None
    damage_description: Optional[str] = None


class VehicleInfo(BaseModel):
    plate_number: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    year: Optional[str] = None
    color: Optional[str] = None
    vin: Optional[str] = None
    engine_number: Optional[str] = None
    ownership_type: Optional[str] = None
    driver_name: Optional[str] = None
    driver_contact: Optional[str] = None
    body_type: Optional[str] = None
    # As the firm's cover page writes it: the digits as read, leading zeros
    # kept, then the unit.
    odometer_reading: Optional[str] = Field(default=None, description="Odometer mileage as the digits read, e.g. '058615 Km'")
    transmission: Optional[str] = Field(default=None, description="Automatic, Manual, CVT")
    date_of_registration: Optional[str] = None
    paintwork_condition: Optional[str] = Field(default=None, description="Good, Scratched, Resprayed, Original")
    steering_condition: Optional[str] = Field(default=None, description="Operable, Impaired, Damaged")
    brakes_condition: Optional[str] = Field(default=None, description="Operable, Impaired, Damaged")
    tyres: Optional[TyreCondition] = Field(default_factory=TyreCondition)
    point_of_impact: Optional[str] = Field(default=None, description="Point of impact e.g. 'Rear centre', 'Front LH quarter'")


class PoliceReportDetails(BaseModel):
    reported_to_police: bool = False
    police_station: Optional[str] = None
    report_number: Optional[str] = None
    officer_name: Optional[str] = None
    date_reported: Optional[str] = None
    reference_number: Optional[str] = None


class InsuranceDetails(BaseModel):
    insurer_name: Optional[str] = None
    policy_number: Optional[str] = None
    claim_number: Optional[str] = None
    claim_type: Optional[str] = Field(default=None, description="Own damage, Third party, Third party fire and theft, Comprehensive, Special case")
    claim_status: Optional[str] = Field(default=None, description="Draft, Under Review, Confirmed, Claim Submitted -- only set if a claim actually exists")
    adjuster_assigned: Optional[str] = None
    workshop_assigned: Optional[str] = None
    estimated_repair_cost: Optional[str] = None
    final_approved_cost: Optional[str] = None


class TimelineEvent(BaseModel):
    time: str
    event: str


class RecommendationsInfo(BaseModel):
    repair_recommendation: Optional[str] = None
    replacement_recommendation: Optional[str] = None
    inspection_recommendation: Optional[str] = None
    disassembly_required: bool = False
    follow_up_action: Optional[str] = None
    preventive_action: Optional[str] = None


class CoverPageInfo(BaseModel):
    """The report's Cover Page as the firm's spreadsheets have it (section
    1 Reference, 3 Static check, 5 Point of impact): what the surveyor types
    at review. Every value is free text printed as typed ("27th February
    2023 (10.15am)"); nothing here is read by the AI (see
    SecurityIncidentDraft.cover_page). The vehicle and insurance facts the
    cover page also prints stay in vehicle_info / insurance_details."""
    date_of_request: Optional[str] = None
    requested_by: Optional[str] = None
    requested_by_address: Optional[str] = None
    workshop_address: Optional[str] = None
    date_of_survey: Optional[str] = None
    date_of_dismantle: Optional[str] = None
    date_before_painting: Optional[str] = None
    date_after_painting: Optional[str] = None
    handbrake_condition: Optional[str] = None
    modification: Optional[str] = None
    general_condition: Optional[str] = None
    # Printed under 5 POINT OF IMPACT; the firm writes "See Annex(es) and
    # Motor Accident Reports".
    point_of_impact_note: Optional[str] = None


class CatalogueRef(BaseModel):
    """The parts-catalogue row a surveyor put on an Annex A line (Phase 2 of
    the catalogue look-up, app/parts/catalogue.py): which suggestion, its
    part number and name, the diagram it is on and the site's own price.
    A reference only -- it never sets the workshop's estimate and is never
    printed. PUT /annex-a checks the suggestion is the case's own and
    writes these facts from the stored row, whatever the client sent."""
    suggestion_id: int = Field(ge=1)
    source: Optional[_CatalogueText] = None
    part_number: Optional[_CatalogueText] = None
    name: Optional[_CatalogueText] = None
    diagram_name: Optional[_CatalogueText] = None
    diagram_url: Annotated[Optional[_CatalogueText], AfterValidator(catalogue_link)] = None
    ref_amount: Optional[float] = Field(default=None, ge=0, le=10_000_000)
    ref_currency: Optional[_CatalogueText] = None
    ref_tax_note: Optional[_CatalogueText] = None
    fetched_at: Optional[_CatalogueText] = None


class AnnexAPartItem(BaseModel):
    item_no: str = Field(default="1.0", description="Item number e.g. '1.0'")
    part_name: str = Field(description="Part description e.g. 'Rear bumper fascia'")
    # Empty, not "Deformed/cut" / "Replace": a row listed from the damage
    # found (synthesize_annexes) has no surveyor finding yet.
    condition: str = Field(default="", description="Observed condition e.g. 'Deformed/cut', 'Grazed/cracked', 'Necessary', 'Serviceable'")
    # Printed under the Remarks column, as the firm's sheets have it.
    action: str = Field(default="", description="Action e.g. 'Replace', 'Repair', 'Straighten', 'Disallow'")
    qty: float = Field(default=1.0, description="Quantity")
    # "1set": printed instead of the number when set; qty stays numeric for
    # the statistics (1set -> qty 1).
    qty_text: Optional[str] = None
    # The LINE TOTAL as the workshop quoted it (10 clips, est 50), not a
    # unit price: adjusted_cost = workshop_est x (1 - discount), never
    # multiplied by qty.
    workshop_est: float = Field(default=0.0, description="Workshop claimed estimate in SGD (line total)")
    discount_pct: float = Field(default=0.20, description="Depreciation/trade discount (0.20 = 20%, 1.0 = 100% disallowed)")
    adjusted_cost: float = Field(default=0.0, description="Adjusted cost in SGD")
    is_net_item: bool = Field(default=False, description="True for net items (clips, sealants, plate) with 0% discount")
    oem_part_number: Optional[str] = Field(default=None, description="OEM part number")
    # Set when the line was added from a suggestion (app/parts/history.py):
    # where it came from, and what past cases paid per unit -- a reference
    # for the surveyor, never the workshop's estimate. Not printed.
    suggested_from: Optional[str] = None
    usual_unit_price: Optional[float] = None
    # The estimate came from past cases (app/parts/prefill.py), not from a
    # person: a tag in the editor, never printed, and the first rows the
    # next prefill replaces.
    prefilled: bool = False
    # Where the row came from -- "detection" (one unpriced row per damaged
    # part), "history" (lines past cases wrote), "lookup" (a price looked
    # up; Phase 3), "typed" (a person wrote or priced it) -- so a re-sync
    # after the damage list changes (prefill.sync_annex_a) rebuilds only
    # the unpriced detection/history rows and never touches a person's.
    # None on rows saved before 2026-11-04: prefill.origin_of derives it.
    origin: Optional[str] = None
    # The damage_summary.part this row was made for; None for a hand-added
    # line. A kept row whose part is no longer damaged is flagged stale,
    # never deleted by the machine: the surveyor keeps or removes it.
    source_part: Optional[str] = None
    stale: bool = False
    # The source part's own line (not a fitting or a companion): it carries
    # the damage item's condition and remarks, and follows a tab-1 change of
    # them while the row isn't a person's (origin "typed") -- price
    # untouched (prefill.sync_annex_a). False on rows from before 2026-11-08.
    main: bool = False
    # The parts-catalogue row a person attached to this line (CatalogueRef):
    # a reference shown under the line, never its estimate, never printed.
    # A row carrying one is a person's: a re-sync never rebuilds it.
    catalogue_ref: Optional[CatalogueRef] = None
    # Where the W/shop Est. came from (Stage 1, app/parts/prices.py):
    # "mine" (this person's own price), "approved" (an admin's book price),
    # "book" (the median of 2+ confirmed past cases), "typed" (a person
    # typed it), "ai" / "catalogue" (where the line came from); None for
    # no price. Never printed. The server keeps it: the Annex A save sets
    # "typed" when the estimate changed, else keeps the stored one. These
    # five are out of the JSON schema like cover_page (SkipJsonSchema):
    # code writes them, never the model, and the draft model's schema
    # stays as it was (test_c9_base / test_c10_base goldens).
    price_source: SkipJsonSchema[Optional[str]] = None
    # mine / approved / book wait for a person (False) until they confirm
    # (POST .../annex-a/confirm-prices, or type over it); sign-off refuses
    # while one waits. None on lines from before Stage 1: never held up.
    price_confirmed: SkipJsonSchema[Optional[bool]] = None
    # The book's (or the approved) unit price, for comparison; staff only.
    book_price: SkipJsonSchema[Optional[float]] = None
    # ISO date of the source: the newest past line used, the person's own
    # price's last change, or the approval.
    price_date: SkipJsonSchema[Optional[str]] = None
    # How many distinct past cases a "book" price rests on.
    price_cases: SkipJsonSchema[Optional[int]] = None
    # The line's own id, the server's (app/reports/annex_lines.py; Stage 1
    # review H2): a save matches each line a page sends back to the stored
    # line by it, never by its wording. A page sends it back unchanged; a
    # new line has none until it is saved. Never printed.
    line_id: SkipJsonSchema[Optional[str]] = None


class AnnexAInfo(BaseModel):
    items: list[AnnexAPartItem] = Field(default_factory=list)
    total_workshop_est: float = 0.0
    total_adjusted_cost: float = 0.0


class AnnexBLabourItem(BaseModel):
    item_no: str = Field(default="1.0", description="Item number e.g. '1.0'")
    description: str = Field(description="Job description e.g. 'To respray affected areas'")
    workshop_est: float = Field(default=0.0, description="Workshop claimed cost in SGD")
    adjusted_cost: float = Field(default=0.0, description="Surveyor adjusted cost in SGD")
    # Kept so stored cases still validate; no longer shown or printed (the
    # firm's Annex B has no such column).
    justification: Optional[str] = Field(default=None, description="Technical rationale")
    # As AnnexAPartItem's: the line came from past cases' Annex B
    # (history.suggest_labour), with what they were usually adjusted to.
    prefilled: bool = False
    suggested_from: Optional[str] = None
    usual_price: Optional[float] = None


class AnnexBInfo(BaseModel):
    items: list[AnnexBLabourItem] = Field(default_factory=list)
    total_workshop_est: float = 0.0
    total_adjusted_cost: float = 0.0


class AnnexCInfo(BaseModel):
    workshop_total: float = Field(default=0.0, description="Annex A + Annex B workshop total")
    adjusted_total: float = Field(default=0.0, description="Annex A + Annex B adjusted total")
    agreed_lump_sum: Optional[float] = Field(default=None, description="Recommended negotiated contract lump sum")
    # The firm's letter has no GST line. These three stay so stored cases
    # validate, but nothing computes or prints them any more (they are left
    # None by synthesize_annexes and sign-off).
    gst_rate: float = Field(default=0.09, description="Singapore GST rate (9%)")
    gst_amount: Optional[float] = Field(default=None, description="GST on lump sum or adjusted total")
    total_with_gst: Optional[float] = Field(default=None, description="Total including GST")
    repair_days: Optional[int] = Field(default=None, description="Reasonable repair turnaround in working days -- set by the surveyor")
    terms: Optional[str] = Field(
        default="Repairs undertaken on contract lump sum basis according to acceptable quality and standard (Repairer discretion to repair parts, replace with reconditioned/used parts, or OEM/genuine parts).",
        description="Settlement terms"
    )
    without_prejudice: bool = Field(default=True, description="Strictly without prejudice declaration")


class BolaLiabilityAssessment(BaseModel):
    """The surveyor's liability finding. Empty until the surveyor records
    one: these defaults used to be a complete finding (scenario 14, rear-end,
    insured 0% / third party 100%, "Agreed", subrogation recommended), which
    every new case carried -- side and front impacts included."""
    scenario_number: Optional[int] = Field(default=None, description="GIA BOLA Matrix scenario index e.g. 14")
    scenario_name: Optional[str] = Field(default=None, description="Scenario title")
    insured_liability_pct: Optional[float] = Field(default=None, description="Insured fault percentage (0% to 100%)")
    third_party_liability_pct: Optional[float] = Field(default=None, description="Third-party fault percentage (0% to 100%)")
    dispute_status: str = Field(default="Pending surveyor assessment", description="'Agreed', 'Disputed', 'Pending Police Findings'")
    apportionment_rationale: Optional[str] = Field(default=None, description="Surveyor liability analysis")
    subrogation_prospect: Optional[str] = Field(default=None, description="Subrogation advice")


class SignOffInfo(BaseModel):
    prepared_by: Optional[str] = None
    # Set by the sign-off endpoint, not defaulted: a draft that names a
    # reviewer claims a review that hasn't happened.
    reviewed_by: Optional[str] = None
    approved_by: Optional[str] = None
    surveyor_name: Optional[str] = None
    # Filled from the signer's own account at sign-off (app/api/main.py).
    # These defaulted to one fixed surveyor's qualifications, licence and
    # firm, carried by every case.
    qualifications: Optional[str] = None
    license_number: Optional[str] = None
    firm_name: Optional[str] = None
    # Printed under the signer's name in Annex C ("Automotive Engineer
    # Assessor"): typed or confirmed at sign-off, never defaulted here.
    signatory_title: Optional[str] = None
    signature_hash: Optional[str] = None
    signature_data_url: Optional[str] = None
    agreed_quantum: Optional[float] = None
    turnaround_days: Optional[int] = None
    liability_opinion: Optional[str] = None
    remarks: Optional[str] = None
    status: str = "Draft"
    signature_date: Optional[str] = None
    terms_accepted: bool = False


class AIAnalysisInfo(BaseModel):
    summary: Optional[str] = None
    detected_parts: list[str] = Field(default_factory=list)
    detected_severity: Optional[str] = None
    confidence_score: Optional[str] = Field(default=None, description="Only set if the model actually computed a confidence, never a placeholder")
    suggested_category: Optional[str] = None
    suggested_repair_notes: Optional[str] = None


class SecondLookItem(BaseModel):
    """One part the second look (ai/second_look.py) suggests checking. Never a
    finding: it becomes a damage item only when a person ticks it on the
    Review step. Contract 9: also the parts only a zone read found
    (intake/zones.py, option B), source "zone"."""
    # Its stable id, prefill.item_key: the same over re-reads, and what
    # SecondLook.dismissed and the decisions hold. Filled by
    # second_look._store; an old item without one goes by `part`.
    key: str = ""
    part: str
    # The firm's wording for the part (prefill.row_name), for the list.
    name: Optional[str] = None
    # "@<photo id>" while a draft, as damage items are (pipeline.py).
    photo_reference: Optional[str] = None
    note: Optional[str] = None
    condition_tokens: list[str] = Field(default_factory=list)
    condition: Optional[str] = None
    damage_type: Optional[str] = None
    confidence: Optional[str] = None
    # "kit": past cases of the same make had it with a part found here;
    # "nearby": next to a part found here; "zone": only a zone read found it
    # (contract 9); "link": tied to a listed part (contract 10, the fields
    # at the end). Not "linked": the dashboard uses that for a part a
    # person linked by hand.
    source: Optional[str] = None
    # The damage-list names that mean this part is already listed (its own
    # name, and the "(side undetermined)" name of its family or the sided
    # ones): the Review step hides the suggestion once one of them is listed.
    covers: list[str] = Field(default_factory=list)
    # Contract 9 (section 4.1), all code-set. reason / evidence as on a
    # damage item (at most 160 characters; at most 4 entries); found_by:
    # the reads that found it; seen_in: at most 12 photo refs; check: the
    # re-check's answer; other_side_listed: the list's part of the same
    # family on the other side ("Right Tail Lamp"); other_part_name,
    # severity, remarks: as the zone read gave them, for the item a person
    # adds and its Annex A line.
    reason: Optional[str] = None
    evidence: list[Evidence] = Field(default_factory=list)
    found_by: list[str] = Field(default_factory=list)
    seen_in: list[str] = Field(default_factory=list)
    check: Optional[SuggestionCheck] = None
    other_side_listed: Optional[str] = None
    other_part_name: Optional[str] = None
    severity: Optional[str] = None
    remarks: Optional[str] = None
    # Contract 10 (section 4.1), all code-set, on a source "link" item;
    # not in a dump while at their defaults. linked_from: the keys of the
    # listed parts it hangs off; link_type: "attached" | "behind" |
    # "opens_with" | "crush"; link_rule: the table's version and link id,
    # "L1:rear-bumper>beam" (at most 40 characters); hit: how hard the
    # listed part was hit, "light" | "medium" | "heavy" | "crush"; bucket:
    # "seen" | "likely" | "strip_down"; hidden / needs_strip_down: as on a
    # damage item; side_from: the listed part whose side it took, None when
    # it took none; intact_in: the zone reads ("zone:rear") that listed it
    # intact -- read names, not photos: ai_draft["zone_intact"] keeps no
    # photo per part -- (shown lower, with a note); link_check: the linked
    # check's answer;
    # oem: the catalogue's diagram names that put it with the listed part
    # (at most 3).
    linked_from: list[str] = Field(default_factory=list)
    link_type: Optional[str] = None
    link_rule: Optional[str] = None
    hit: Optional[str] = None
    bucket: Optional[str] = None
    hidden: bool = False
    needs_strip_down: bool = False
    side_from: Optional[str] = None
    intact_in: list[str] = Field(default_factory=list)
    link_check: Optional[LinkCheck] = None
    oem: list[str] = Field(default_factory=list)
    # The car map's review screen (K1-design.md 5b; K4), code-set, only
    # with intake_car_map "on" (an older draft has none, and loads as it
    # was): assembly: the group -- the map's assembly id, one per side for
    # a sided assembly ("front-door.left"); assembly_name: its name for
    # the header; prior: the relation's rank (1 listed most often .. 5),
    # None for none; hop: 1, or 2 for a second hop (via: the part between);
    # fitted: "if_fitted" for an option some cars lack ("-- if fitted");
    # fitted_note: what the stored catalogue says of this model (layer 2);
    # signs_yes: the walk's yes signs that point at it; also_check: the
    # group's words for parts with no name yet (the same on each item of
    # the group, shown once); folded: in the strip-down block outside
    # intake_links_max (intake_links_stripdown_cap "out").
    assembly: Optional[str] = None
    assembly_name: Optional[str] = None
    prior: Optional[int] = None
    hop: Optional[int] = None
    via: Optional[str] = None
    fitted: Optional[str] = None
    fitted_note: Optional[str] = None
    signs_yes: list[SignYes] = Field(default_factory=list)
    also_check: list[str] = Field(default_factory=list)
    folded: bool = False
    # plans/2026-11-22-enhance-staging, code-set, not in a dump while None.
    # other_side_of (F3, source "other_side"): the listed sided part this
    # "(side undetermined)" find was placed on -- seen on another photo, so
    # it may be the other side's ("Other side? Tail Lamp (side
    # undetermined)"); its covers leave that name out, so the listed part
    # doesn't hide it. cut_reason (F2/F5, on SecondLook.cut's items only):
    # why a cap cut it -- "limit" (a cap: second_look.MAX_SUGGESTIONS, the
    # linked parts' per-group or overall max) or "intact" (a zone read
    # called it not damaged: demoted, then cut, or dropped).
    other_side_of: Optional[str] = None
    cut_reason: Optional[str] = None
    # Wave 2 E11 (settings.intake_zone_wrong_end "keep"), code-set, not in a
    # dump while None: a find of an end zone read whose photos it said show
    # the other end -- "The rear read thought these photos show the front."
    # (zone_read.wrong_end_note); the Review step shows it as a note.
    wrong_end_note: Optional[str] = None
    # Detection Round 2 (settings.intake_zone_inventory "on"), code-set, not
    # in a dump while None: a part a zone read's inventory marked damaged
    # that nothing listed or suggested (intake/zones.inventory_suggestions)
    # -- "Seen in the zone check: ..."; the Review step shows it as a note.
    inventory_note: Optional[str] = None

    @model_serializer(mode="wrap")
    def _without_unset_links(self, handler):
        return _without_defaults(handler(self), {**LINK_SUGGESTION_DEFAULTS, **FIX_SUGGESTION_DEFAULTS,
                                                 **WAVE2_SUGGESTION_DEFAULTS, **R2_SUGGESTION_DEFAULTS})


# SecondLook.decisions kept per case.
MAX_DECISIONS = 200


class SecondLook(BaseModel):
    """The second look's last answer for a draft, and what a person did with
    it. status: ok, skipped (off, nothing to look at, no AI allowance) or
    failed (an error or no answer in time); note says why, in plain words."""
    status: str = "skipped"
    model: Optional[str] = None
    note: Optional[str] = None
    # "past Honda cases" when same-make history supplied candidates.
    scope: Optional[str] = None
    seconds: Optional[float] = None
    photos_sent: int = 0
    candidates: int = 0
    items: list[SecondLookItem] = Field(default_factory=list)
    # The keys of suggestions a person marked Not damaged: never suggested
    # again on this case, also after a re-read and after filing. An old
    # draft's holds part names, which are the keys of canonical parts.
    dismissed: list[str] = Field(default_factory=list)
    # Contract 9 (section 4.1). Status, model and note above still describe
    # the second-look call itself; the UI shows the items whatever it says.
    # reads: the reads that answered ("overall", "zone:rear", ...).
    # decisions: who added, dismissed or undid which suggestion, when --
    # server-stamped, at most MAX_DECISIONS, the oldest dropped. check: what
    # the re-check step did this read.
    reads: list[str] = Field(default_factory=list)
    decisions: list[SuggestionDecision] = Field(default_factory=list)
    check: Optional[CheckRun] = None
    # Contract 10: what the linked check did this read (status, model,
    # note, seconds, calls); not in a dump while None.
    link_run: Optional[CheckRun] = None
    # F2/F5 (settings.intake_keep_cut "on"; plans/2026-11-22-enhance-
    # staging): what the caps cut this read, surest first as they were
    # sorted, each a suggestion as items holds it plus its cut_reason --
    # for the Review step's folded "More (N)" block. Never in items, never
    # printed; a person's add or dismiss on one moves it into items
    # (reports/suggestions.decide). cut_counts: {reason: how many} of cut
    # as stored. Neither in a dump while empty.
    cut: list[SecondLookItem] = Field(default_factory=list)
    cut_counts: dict[str, int] = Field(default_factory=dict)

    @field_validator("decisions")
    @classmethod
    def _latest_decisions(cls, value):
        # Never refuses: a long-lived case keeps its newest decisions.
        return value[-MAX_DECISIONS:]

    @model_serializer(mode="wrap")
    def _without_unset_links(self, handler):
        return _without_defaults(handler(self), {**LINK_LOOK_DEFAULTS, **FIX_LOOK_DEFAULTS})


class SecurityIncidentDraft(BaseModel):
    report_id: Optional[str] = Field(None, description="Custom Report ID e.g. CIR-2026-001")
    company_name: Optional[str] = Field(default=None)
    reporter_name: Optional[str] = None
    reporter_role: Optional[str] = None
    reporter_contact: Optional[str] = None
    reporter_email: Optional[str] = None
    reporter_department: Optional[str] = None
    incident_datetime: Optional[str] = Field(
        None, description="Best-guess date/time as stated by the reporter, or null if not mentioned"
    )
    location: Optional[str] = None
    weather_condition: Optional[str] = Field(None, description="Clear, Rainy, Night/Dark, Foggy, Wet Surface")
    road_condition: Optional[str] = Field(None, description="Dry, Wet, Slippery, Gravel, Uneven")
    traffic_condition: Optional[str] = Field(None, description="Light, Moderate, Heavy, Stationed")
    # description comes before the categorical/list fields below on purpose:
    # structured output fills fields in this declaration order, and letting
    # the model write its narrative understanding out in prose FIRST makes it
    # far more likely that category/accident_type/damaged_parts/severity_level
    # actually reflect what it just described -- rather than committing to
    # those fields from scratch before it's "thought through" the incident,
    # then writing a detailed description afterward that the earlier fields
    # never got updated to match.
    description: str = Field(
        description="Professional narrative description of the incident, drafted from the reporter's free text and photos"
    )
    category: list[str] = Field(
        default_factory=list,
        description=(
            "One or more of: Vehicle Collision or Damage, Unauthorized Access, Theft or Burglary, "
            "Vandalism or Property Damage, Assault or Threat, Harassment, Cybersecurity Breach, Other."
        ),
    )
    accident_type: Optional[str] = Field(
        None,
        description=(
            "Collision with another vehicle, Rear-end collision, Side impact, Front impact, "
            "Parked vehicle hit, Single vehicle accident, Hit-and-run, Scrape / minor contact, "
            "Flood / weather damage, Theft / vandalism damage, Other."
        ),
    )
    damaged_parts: list[str] = Field(
        default_factory=list,
        description="List of damaged vehicle parts or areas observed",
    )
    severity_level: Optional[str] = Field(
        None,
        description="Damage severity assessment: Minor (Cosmetic), Moderate (Panel Repair), or Severe (Structural / Non-drivable)",
    )
    vehicle_details: Optional[str] = Field(
        None,
        description="Vehicle make, model, or plate number summary string",
    )
    vehicle_info: Optional[VehicleInfo] = Field(default_factory=VehicleInfo)
    damage_summary: list[DamageSummaryItem] = Field(default_factory=list)
    people_involved: list[PersonInvolved] = Field(default_factory=list)
    witnesses: list[Witness] = Field(default_factory=list)
    immediate_actions: Optional[str] = None
    police_report: Optional[PoliceReportDetails] = Field(default_factory=PoliceReportDetails)
    insurance_details: Optional[InsuranceDetails] = Field(default_factory=InsuranceDetails)
    third_party_info: Optional[ThirdPartyInfo] = Field(default_factory=ThirdPartyInfo)
    ai_analysis: Optional[AIAnalysisInfo] = Field(default_factory=AIAnalysisInfo)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    recommendations: Optional[RecommendationsInfo] = Field(default_factory=RecommendationsInfo)
    sign_off: Optional[SignOffInfo] = Field(default_factory=SignOffInfo)
    annex_a: Optional[AnnexAInfo] = Field(default_factory=AnnexAInfo)
    annex_b: Optional[AnnexBInfo] = Field(default_factory=AnnexBInfo)
    annex_c: Optional[AnnexCInfo] = Field(default_factory=AnnexCInfo)
    # Left out of model_json_schema() on purpose: ai/extraction.py hands
    # that schema to Gemini (minus its _NOT_MODEL_OUTPUT list), and the
    # cover page is dates of request/survey/painting and static checks the
    # surveyor records -- read off photos they would be invented.
    cover_page: SkipJsonSchema[Optional[CoverPageInfo]] = Field(default_factory=CoverPageInfo)
    # The intake's second-look suggestions (ai/second_look.py). Out of the
    # schema for the same reason: the pipeline writes it, never the drafting
    # call -- and the draft schema, shared with the Telegram bot, must not
    # grow (extraction._NOT_MODEL_OUTPUT). A field at all so the wizard's
    # autosave, validated through this model, keeps it.
    second_look: SkipJsonSchema[Optional[SecondLook]] = None
    # Field review (app/reports/review.py): who confirmed which field
    # ({key: {by, by_id, at, na?}}), and the static-check fields the firm's
    # usual wording filled in. Out of the schema like second_look (a
    # person's marks, never the model's); fields at all so the wizard's
    # autosave and finalize, validated through this model, keep them. The
    # server stamps the marks and owns template_fields: neither is ever
    # stored as a browser sent it.
    checked: SkipJsonSchema[Optional[dict]] = None
    template_fields: SkipJsonSchema[Optional[list[str]]] = None
    # The AI's own static-check / transmission words, with INTAKE_FIRM_WORDS
    # on: the ones the firm's words replaced and the ones kept for a person
    # ({"transmission": "CVT", "paintwork_condition": "Resprayed"}), a note
    # beside the field, never printed (app/reports/firm_words.py). Out of
    # the schema like checked: code writes it, never the model. Left out of
    # a dump while None (_without_unset_ai_words), so with the switch off a
    # reading saves byte for byte what it did before.
    ai_words: SkipJsonSchema[Optional[dict]] = None
    bola_assessment: Optional[BolaLiabilityAssessment] = Field(default_factory=BolaLiabilityAssessment)
    reported_to_authorities: bool = False
    authority_reference: Optional[str] = None
    preventive_measures: Optional[str] = None
    additional_comments: Optional[str] = Field(
        None,
        description="Anything relevant the reporter mentioned that doesn't fit the fields above.",
    )

    @model_serializer(mode="wrap")
    def _without_unset_ai_words(self, handler):
        return _without_defaults(handler(self), {"ai_words": None})


