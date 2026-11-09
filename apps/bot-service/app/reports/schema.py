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
from typing import Optional

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema


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


class DamageSummaryItem(BaseModel):
    # Deliberately no example list here. The allowed values are supplied to
    # the model as a schema enum from taxonomy.CANONICAL_PARTS; examples in
    # this description previously contradicted that list ("Boot/Trunk" vs
    # "Boot Lid", "Headlight" vs "Left Headlamp"), giving the model two
    # different answers to the same question.
    part: str = Field(description="The damaged part. Choose the closest match from the allowed values; side is part of the name where it applies.")
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
    human_verified: bool = False
    repair_required: bool = False
    oem_part_number: Optional[str] = Field(
        default=None,
        description="OEM replacement part number, e.g. '71501-T7A-A00' -- never invent this. Only set it if the reporter or surveyor actually supplied it; a human enters this during review, it is never looked up or guessed.",
    )


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
    Review step."""
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
    # "nearby": next to a part found here.
    source: Optional[str] = None
    # The damage-list names that mean this part is already listed (its own
    # name, and the "(side undetermined)" name of its family or the sided
    # ones): the Review step hides the suggestion once one of them is listed.
    covers: list[str] = Field(default_factory=list)


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
    # Parts a person dismissed: never suggested again on this draft, also
    # after a re-read.
    dismissed: list[str] = Field(default_factory=list)


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
    bola_assessment: Optional[BolaLiabilityAssessment] = Field(default_factory=BolaLiabilityAssessment)
    reported_to_authorities: bool = False
    authority_reference: Optional[str] = None
    preventive_measures: Optional[str] = None
    additional_comments: Optional[str] = Field(
        None,
        description="Anything relevant the reporter mentioned that doesn't fit the fields above.",
    )


