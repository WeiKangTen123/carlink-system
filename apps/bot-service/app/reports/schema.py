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


class BoundingBox(BaseModel):
    """Where a damaged part appears within its referenced photo, as a
    percentage-based box (top-left origin) -- resolution-independent, so it
    still lines up regardless of what size the photo is displayed at."""
    top: float = Field(description="Top edge of the box as a percentage (0-100) of the photo's height")
    left: float = Field(description="Left edge of the box as a percentage (0-100) of the photo's width")
    width: float = Field(description="Box width as a percentage (0-100) of the photo's width")
    height: float = Field(description="Box height as a percentage (0-100) of the photo's height")


class DamageSummaryItem(BaseModel):
    part: str = Field(description="Front Bumper, Rear Bumper, Left Door, Right Door, Bonnet/Hood, Boot/Trunk, Headlight, Taillight, Windshield, Side Mirror, Fender, Wheel/Rim, Tire, Roof, Chassis, Undercarriage")
    damage_type: Optional[str] = Field(default=None, description="Scratch, Dent, Crack, Broken, Bent, Loose, Missing, Water damage, Structural damage -- only set if actually known")
    severity: Optional[str] = Field(default=None, description="Minor, Moderate, or Severe -- only set if actually assessable from the photo/description")
    photo_reference: Optional[str] = Field(
        default=None,
        description="Which uploaded photo actually shows this damage, e.g. 'P01' for the first photo, 'P02' for the second. Photos are given to you in that order -- only set this when you can genuinely tell which photo shows the part; leave null if unclear or if it's not visible in any single photo.",
    )
    bounding_box: Optional[BoundingBox] = Field(
        default=None,
        description="Where this damage appears within the photo_reference photo specifically. Only set this when photo_reference is also set and you can genuinely localize the damage in that frame -- never a rough guess just to fill the field.",
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


class SignOffInfo(BaseModel):
    prepared_by: Optional[str] = None
    reviewed_by: Optional[str] = None
    approved_by: Optional[str] = None
    status: str = "Draft"
    signature_date: Optional[str] = None


class AIAnalysisInfo(BaseModel):
    summary: Optional[str] = None
    detected_parts: list[str] = Field(default_factory=list)
    detected_severity: Optional[str] = None
    confidence_score: Optional[str] = Field(default=None, description="Only set if the model actually computed a confidence, never a placeholder")
    suggested_category: Optional[str] = None
    suggested_repair_notes: Optional[str] = None


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
    ai_analysis: Optional[AIAnalysisInfo] = Field(default_factory=AIAnalysisInfo)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    recommendations: Optional[RecommendationsInfo] = Field(default_factory=RecommendationsInfo)
    sign_off: Optional[SignOffInfo] = Field(default_factory=SignOffInfo)
    reported_to_authorities: bool = False
    authority_reference: Optional[str] = None
    preventive_measures: Optional[str] = None
    additional_comments: Optional[str] = Field(
        None,
        description="Anything relevant the reporter mentioned that doesn't fit the fields above.",
    )


