import sqlite3
import json
import os

db_path = '/var/lib/docker/volumes/infra_bot_data/_data/carlink.db'
conn = sqlite3.connect(db_path)
cur = conn.cursor()

report_id = '2790f292c0e3'
cur.execute("SELECT data FROM reports WHERE id=?", (report_id,))
row = cur.fetchone()
if not row:
    print(f"Report {report_id} not found!")
    exit(1)

data = json.loads(row[0])

# 1. Vehicle Info
v_info = data.get("vehicle_info") or {}
v_info["make"] = "Honda"
v_info["model"] = "Vezel"
v_info["body_type"] = "SUV"
v_info["color"] = "White"
v_info["point_of_impact"] = "Left Front Door / Side"
data["vehicle_info"] = v_info
data["vehicle_details"] = "Honda Vezel (White SUV)"

# 2. Accident Type & Description
data["accident_type"] = "Side impact / Side-swipe collision"
data["description"] = (
    "The reporter provided photo evidence (P01) showing a white Honda Vezel positioned inside a workshop "
    "environment with its left front passenger door opened. Physical deformation, dent creases, and sheet metal "
    "misalignment are visible along the lower door panel and frame edge. The left wing mirror assembly is "
    "detached/missing from its A-pillar mounting base."
)

# 3. Damaged Parts
data["damaged_parts"] = ["Left Front Door", "Left Wing Mirror"]

# 4. Damage Summary
data["damage_summary"] = [
    {
        "part": "Left Front Door",
        "damage_type": "Deformation",
        "severity": "Moderate",
        "photo_reference": "P01",
        "bbox_2d": [350, 400, 950, 680],
        "ai_confidence": "High",
        "human_verified": True,
        "repair_required": True,
        "oem_part_number": "67050-T7A-000ZZ"
    },
    {
        "part": "Left Wing Mirror",
        "damage_type": "Detached / Missing",
        "severity": "Moderate",
        "photo_reference": "P01",
        "bbox_2d": [40, 520, 150, 680],
        "ai_confidence": "High",
        "human_verified": True,
        "repair_required": True,
        "oem_part_number": "76250-T7A-J01"
    }
]

# 5. Annex A
data["annex_a"] = {
    "items": [
        {
            "item_no": "1.0",
            "part_name": "Left Front Door outer shell",
            "condition": "Deformed/bent",
            "action": "Replace",
            "qty": 1.0,
            "workshop_est": 450.0,
            "discount_pct": 0.20,
            "is_net_item": False,
            "oem_part_number": "67050-T7A-000ZZ",
            "adjusted_cost": 360.0
        },
        {
            "item_no": "2.0",
            "part_name": "Left Wing Mirror assembly",
            "condition": "Impact detached",
            "action": "Replace",
            "qty": 1.0,
            "workshop_est": 380.0,
            "discount_pct": 0.20,
            "is_net_item": False,
            "oem_part_number": "76250-T7A-J01",
            "adjusted_cost": 304.0
        },
        {
            "item_no": "3.0",
            "part_name": "Door moulding & weatherstrip LH",
            "condition": "Necessary",
            "action": "Replace",
            "qty": 1.0,
            "workshop_est": 75.0,
            "discount_pct": 0.20,
            "is_net_item": False,
            "oem_part_number": "72450-T7A-003",
            "adjusted_cost": 60.0
        },
        {
            "item_no": "4.0",
            "part_name": "Mounting clips & fasteners",
            "condition": "Necessary",
            "action": "Replace",
            "qty": 8.0,
            "workshop_est": 48.0,
            "discount_pct": 0.20,
            "is_net_item": False,
            "oem_part_number": "91505-TM8-003",
            "adjusted_cost": 38.4
        },
        {
            "item_no": "5.0",
            "part_name": "Door adhesive & seam sealer",
            "condition": "Necessary",
            "action": "Replace",
            "qty": 1.0,
            "workshop_est": 45.0,
            "discount_pct": 0.0,
            "is_net_item": True,
            "oem_part_number": "08712-0002",
            "adjusted_cost": 45.0
        }
    ],
    "total_workshop_est": 998.0,
    "total_adjusted_cost": 807.4
}

# 6. Annex B
data["annex_b"] = {
    "items": [
        {
            "item_no": "1.0",
            "description": "Towing charges from incident scene to workshop",
            "workshop_est": 100.0,
            "adjusted_cost": 60.0,
            "justification": "Benchmarked standard towing tariff"
        },
        {
            "item_no": "2.0",
            "description": "To check electrical wiring and mirror actuator circuits",
            "workshop_est": 80.0,
            "adjusted_cost": 60.0,
            "justification": "Electrical diagnostic scan allowance"
        },
        {
            "item_no": "3.0",
            "description": "To remove & refix door trim, power window regulator and latch",
            "workshop_est": 180.0,
            "adjusted_cost": 120.0,
            "justification": "Internal door assembly R&I transfer labour"
        },
        {
            "item_no": "4.0",
            "description": "To straighten door frame and align with B-pillar and sill",
            "workshop_est": 280.0,
            "adjusted_cost": 200.0,
            "justification": "Door aperture alignment and hinge fitting"
        },
        {
            "item_no": "5.0",
            "description": "To respray Left Front Door panel (2K Oven Baked - 1 panel)",
            "workshop_est": 380.0,
            "adjusted_cost": 260.0,
            "justification": "Panel surface preparation, 2K primer and oven-baked respray"
        },
        {
            "item_no": "6.0",
            "description": "To fit and calibrate Left Wing Mirror unit",
            "workshop_est": 90.0,
            "adjusted_cost": 60.0,
            "justification": "Side mirror electrical assembly and calibration"
        }
    ],
    "total_workshop_est": 1110.0,
    "total_adjusted_cost": 760.0
}

# 7. Annex C
data["annex_c"] = {
    "workshop_total": 2108.0,
    "adjusted_total": 1567.4,
    "agreed_lump_sum": 1300.0,
    "gst_rate": 0.09,
    "gst_amount": 117.0,
    "total_with_gst": 1417.0,
    "repair_days": 4,
    "terms": "Repairs undertaken on contract lump sum basis according to acceptable quality and standard (Repairer discretion to repair parts, replace with reconditioned/used parts, or OEM/genuine parts).",
    "without_prejudice": True
}

# 8. BOLA Assessment
data["bola_assessment"] = {
    "scenario_number": 23,
    "scenario_name": "Side-impact / Lateral collision during lane change or turn",
    "insured_liability_pct": 0.0,
    "third_party_liability_pct": 100.0,
    "dispute_status": "Agreed",
    "apportionment_rationale": "Third party vehicle failed to maintain safe lateral clearance, causing side impact / swipe along the insured vehicle's left front passenger door and wing mirror assembly.",
    "subrogation_prospect": "100% TPPD Recovery Recommended"
}

# 9. Sign-off block
signoff = data.get("sign_off") or {}
signoff["agreed_quantum"] = 1300.0
signoff["turnaround_days"] = 4
signoff["liability_opinion"] = "100% Third Party Liability (BOLA #23)"
signoff["remarks"] = "Assessment revised to reflect Left Front Door deformation and detached Left Wing Mirror assembly."
data["sign_off"] = signoff

# Save back to database
cur.execute("UPDATE reports SET data=? WHERE id=?", (json.dumps(data), report_id))
conn.commit()
conn.close()

print(f"Successfully updated report {report_id} with accurate damage detection and quantum!")
