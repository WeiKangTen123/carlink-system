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


class DamageSummaryItem(BaseModel):
    part: str = Field(description="Front Bumper, Rear Bumper, Left Door, Right Door, Bonnet/Hood, Boot/Trunk, Headlight, Taillight, Windshield, Side Mirror, Fender, Wheel/Rim, Tire, Roof, Chassis, Undercarriage")
    damage_type: str = Field(description="Scratch, Dent, Crack, Broken, Bent, Loose, Missing, Water damage, Structural damage")
    severity: str = Field(default="Moderate", description="Minor, Moderate, or Severe")
    photo_reference: Optional[str] = Field(default="P01", description="e.g. P01, P02")
    ai_confidence: Optional[str] = Field(default="90%", description="e.g. 90%")
    human_verified: bool = True
    repair_required: bool = True


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
    claim_status: Optional[str] = Field(default="Pending", description="Draft, Under Review, Confirmed, Claim Submitted")
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
    prepared_by: Optional[str] = "Carlink Reporter / AI Assistant"
    reviewed_by: Optional[str] = "Pending Reviewer Sign-off"
    approved_by: Optional[str] = None
    status: str = "Confirmed"
    signature_date: Optional[str] = None


class AIAnalysisInfo(BaseModel):
    summary: Optional[str] = None
    detected_parts: list[str] = Field(default_factory=list)
    detected_severity: Optional[str] = None
    confidence_score: Optional[str] = "90%"
    suggested_category: Optional[str] = None
    suggested_repair_notes: Optional[str] = None


class SecurityIncidentDraft(BaseModel):
    report_id: Optional[str] = Field(None, description="Custom Report ID e.g. CIR-2026-001")
    company_name: Optional[str] = Field(default="Carlink Motors / Workshop Services")
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
    description: str = Field(
        description="Professional narrative description of the incident, drafted from the reporter's free text and photos"
    )
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


