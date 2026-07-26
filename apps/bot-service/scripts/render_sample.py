"""Renders a sample Car Incident Report PDF using the full template structure.
Run from apps/bot-service:
    python scripts/render_sample.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rendering.renderer import render_pdf  # noqa: E402

SAMPLE_CAR_REPORT = {
    "report_id": "CIR-2026-0891",
    "company_name": "Carlink Automotive Consultancy",
    "reporter_name": "Ahmad bin Ismail",
    "reporter_role": "Vehicle Inspector / Surveyor",
    "reporter_contact": "+6012-3456789",
    "reporter_department": "Claims Inspection Dept",
    "incident_datetime": "2026-07-26 14:15",
    "location": "Federal Highway KM 14.2, Petaling Jaya",
    "weather_condition": "Rainy / Wet Surface",
    "road_condition": "Slippery Asphalt",
    "traffic_condition": "Heavy Traffic",
    "category": ["Vehicle Collision or Damage"],
    "accident_type": "Rear-End Collision",
    "severity_level": "Moderate",
    "vehicle_details": "WX 8888 A - Toyota Camry 2.5V (2023)",
    "vehicle_info": {
        "plate_number": "WX 8888 A",
        "make": "Toyota",
        "model": "Camry 2.5V",
        "year": "2023",
        "color": "Pearl White",
        "vin": "JT123NC88492019",
        "driver_name": "Tan Kah Meng",
        "driver_contact": "+6016-9876543",
    },
    "damage_summary": [
        {
            "part": "Front Bumper",
            "damage_type": "Deep Dent + Scrape",
            "severity": "Moderate",
            "photo_reference": "P01",
            "ai_confidence": "94.5%",
            "human_verified": True,
            "repair_required": True,
        },
        {
            "part": "Right Headlight",
            "damage_type": "Cracked Housing & Lens",
            "severity": "Moderate",
            "photo_reference": "P02",
            "ai_confidence": "92.0%",
            "human_verified": True,
            "repair_required": True,
        },
        {
            "part": "Bonnet / Hood",
            "damage_type": "Panel Deformation",
            "severity": "Minor",
            "photo_reference": "P03",
            "ai_confidence": "88.7%",
            "human_verified": True,
            "repair_required": True,
        },
    ],
    "damaged_parts": ["Front Bumper", "Right Headlight", "Bonnet / Hood"],
    "description": (
        "Vehicle WX 8888 A was involved in a rear-end collision while decelerating in heavy traffic on "
        "Federal Highway during a rainstorm. The impact resulted in noticeable deformation to the front bumper assembly, "
        "cracked right headlight lens, and slight buckling on the front bonnet edge. No injuries reported."
    ),
    "people_involved": [
        {
            "name": "Tan Kah Meng",
            "role": "Driver / Owner",
            "contact": "+6016-9876543",
        }
    ],
    "witnesses": [
        {
            "name": "Lee Chong Wei",
            "contact": "+6019-1122334",
            "statement": "Saw the lead vehicle brake suddenly, trailing car skidded into the rear bumper.",
        }
    ],
    "immediate_actions": "Hazard lights activated, vehicle moved to emergency lane, police report filed on site.",
    "police_report": {
        "reported_to_police": True,
        "police_station": "Balai Polis Trafik PJ",
        "report_number": "POL/PJ/2026/88412",
        "officer_name": "Insp. Rosli",
    },
    "insurance_details": {
        "insurer_name": "Lonpac Insurance Bhd",
        "policy_number": "POL-99201-MOTOR",
        "claim_number": "CLM-2026-8812",
        "claim_type": "Own Damage Claim",
        "claim_status": "Under Assessment",
        "estimated_repair_cost": "MYR 4,800.00",
    },
    "ai_analysis": {
        "summary": "High confidence frontal impact detection with clear bumper deformation and headlight lens crack.",
        "confidence_score": "93.4%",
        "suggested_category": "Vehicle Rear-End Collision",
    },
    "timeline": [
        {"time": "02:15 PM", "event": "Vehicle collision occurred in heavy traffic"},
        {"time": "02:20 PM", "event": "Vehicle moved safely to emergency lane & photos captured"},
        {"time": "02:45 PM", "event": "Police report lodged at Traffic Police Station"},
        {"time": "03:15 PM", "event": "Report drafted & verified in Carlink System"},
    ],
    "recommendations": {
        "repair_recommendation": "Replace front bumper cover & right headlight unit; realign bonnet latch.",
        "inspection_recommendation": "Perform radar sensor calibration post-repair.",
    },
    "sign_off": {
        "prepared_by": "Ahmad bin Ismail (Surveyor)",
        "reviewed_by": "Encik Halim (Senior Loss Adjuster)",
        "status": "Confirmed",
    },
}

if __name__ == "__main__":
    out = Path("storage") / "car_incident_sample_report.pdf"
    render_pdf(SAMPLE_CAR_REPORT, photo_paths=[], output_path=str(out))
    print(f"Successfully rendered Car Incident Report PDF: {out.resolve()}")
