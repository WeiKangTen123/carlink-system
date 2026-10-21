"""One-off cleanup of values the old code invented and saved into cases.

Until 2026-10-21 the annex synthesizer and "AI Auto-Map" wrote made-up
facts into Report.data (see the history notes in reports/synthesize_annexes.py
and api/main._enrich_single_report_data). This removes them where they can be
recognised with certainty -- exact strings and patterns only those code paths
produced -- and leaves anything that could be a real recorded value alone.

Signed-off reports are never changed: they are listed for a human to review.
Changed reports get their PDF re-rendered on next view (pdf_path cleared).

    python3 scripts/scrub_invented_values.py           # dry run: report only
    python3 scripts/scrub_invented_values.py --apply   # write changes

Back up the database first.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import Report  # noqa: E402
from app.reports.synthesize_annexes import synthesize_annexes_for_report  # noqa: E402

INVENTED_WORKSHOPS = {
    "M/S Precise Auto Service, 1 Kaki Bukit Ave 6, #02-34, Singapore 417883",
    "Precise Auto Service (Kaki Bukit Autobay)",
    "Sin Ming Autocare (Cluster A)",
    "V-Kool Automotive (Ubi Techpark)",
    "ComfortDelGro Engineering (Toh Guan Hub)",
}
INVENTED_ADJUSTERS = {"Patrick Ng (Carlink Consultancy)"}
INVENTED_ODOMETERS = {"185,742 km", "074,820 km"}
SLK_ODOMETER = "015,287 km"
INVENTED_POLICE_OFFICER = "Insp. Marcus Tan"
INVENTED_POLICE_STATION = "Traffic Police HQ (Ubi Ave 3)"
INVENTED_BOLA_RATIONALES = (
    "Rear-end impact into stationary lead vehicle. Tortfeasor failed to maintain safe braking distance under BOLA Rule 14.",
    "Third party vehicle failed to maintain safe lateral clearance",
    "Third party vehicle emerged or turned into insured path of travel",
)
# Annex B lines the generator added to every case.
GENERATED_LABOUR = (
    "Towing charges from incident scene to workshop", "To check wiring system", "To tuff coat",
    "To adjust chassis alignment", "To respray affected areas", "To renew damaged parts",
    "To LTA remove/reseal", "To remove/refix", "To remove & refix", "To remove/replace reverse sensor",
)
# OEM numbers of the hard-coded SLK 3063 Z benchmark annex.
SLK_BENCHMARK_OEMS = {"71501-T7A-000", "68100-T7A-000ZZ", "33550-T7A-H01", "71530-T7A-000"}


def _plate(data: dict) -> str:
    return ((data.get("vehicle_info") or {}).get("plate_number") or "").replace(" ", "").upper()


def scrub(data: dict) -> list[str]:
    """Edits data in place; returns what changed."""
    changes: list[str] = []
    ins = data.get("insurance_details") or {}
    if ins.get("workshop_assigned") in INVENTED_WORKSHOPS:
        changes.append(f"workshop {ins['workshop_assigned']!r}")
        ins["workshop_assigned"] = None
    if ins.get("adjuster_assigned") in INVENTED_ADJUSTERS:
        changes.append("adjuster")
        ins["adjuster_assigned"] = None
    claim_no = ins.get("claim_number") or ""
    if re.fullmatch(r"CL-[^-]+-(SG|[A-Z0-9]+)", claim_no) and (claim_no.startswith("CL-11900-") or claim_no.endswith("-SG") or claim_no.endswith(_plate(data) or "\0")):
        changes.append(f"claim number {claim_no!r}")
        ins["claim_number"] = None

    annex_a_items = ((data.get("annex_a") or {}).get("items") or [])
    oems = {str(it.get("oem_part_number") or "") for it in annex_a_items}
    # Placeholder part numbers, or the hard-coded SLK 3063 Z benchmark annex
    # (even on that car it was copied in, not assessed in this system).
    generated_a = any(re.fullmatch(r"OEM-\d{3}", o) for o in oems) or bool(oems & SLK_BENCHMARK_OEMS)
    labour = [str(it.get("description") or "") for it in ((data.get("annex_b") or {}).get("items") or [])]
    generated_b = bool(labour) and all(any(l.startswith(g) for g in GENERATED_LABOUR) for l in labour)
    if generated_a or generated_b:
        changes.append("generated annexes (and the costs derived from them)")
        if generated_a:
            data["annex_a"] = {}
        if generated_b:
            data["annex_b"] = {}
        annex_c = data.get("annex_c") or {}
        for key in ("agreed_lump_sum", "repair_days", "gst_amount", "total_with_gst"):
            annex_c[key] = None
        data["annex_c"] = annex_c
        # Both were overwritten from the generated totals.
        ins["estimated_repair_cost"] = None
        ins["final_approved_cost"] = None
        data.update(synthesize_annexes_for_report(data))

    bola = data.get("bola_assessment") or {}
    if any(str(bola.get("apportionment_rationale") or "").startswith(r) for r in INVENTED_BOLA_RATIONALES):
        changes.append(f"BOLA scenario {bola.get('scenario_number')}")
        data["bola_assessment"] = {"dispute_status": "Pending surveyor assessment"}

    sign = data.get("sign_off") or {}
    if sign.get("status") != "Signed Off":
        for key in ("reviewed_by", "approved_by", "surveyor_name"):
            if sign.get(key) == "Patrick Ng":
                sign[key] = None
                changes.append(f"sign_off.{key}")
        if sign.get("liability_opinion", "") and "BOLA #14" in sign["liability_opinion"]:
            sign["liability_opinion"] = None
            changes.append("sign_off.liability_opinion")
        if sign.get("agreed_quantum") and generated_a:
            sign["agreed_quantum"] = None

    v = data.get("vehicle_info") or {}
    odo = v.get("odometer_reading")
    if odo in INVENTED_ODOMETERS or (odo == SLK_ODOMETER and "SLK3063Z" not in _plate(data)):
        changes.append(f"odometer {odo!r}")
        v["odometer_reading"] = None
    if v.get("make") == "Passenger" and v.get("model") == "Sedan":
        changes.append("make/model 'Passenger Sedan'")
        v["make"] = v["model"] = None
    if v.get("paintwork_condition") == "Good / Original":
        changes.append("paintwork")
        v["paintwork_condition"] = None
    tyres = v.get("tyres") or {}
    if tyres and all((t or {}).get("brand") == "Dunlop Enasave" and (t or {}).get("tread_depth_mm") == 6.0 for t in tyres.values()):
        changes.append("tyres (4x Dunlop Enasave 6.0mm)")
        v["tyres"] = {}

    pol = data.get("police_report") or {}
    if pol.get("officer_name") == INVENTED_POLICE_OFFICER or str(pol.get("report_number") or "").startswith("TP/2026/08/"):
        changes.append("police report details")
        for key in ("officer_name", "report_number", "police_station"):
            if pol.get(key) in (INVENTED_POLICE_OFFICER, INVENTED_POLICE_STATION) or str(pol.get(key) or "").startswith("TP/2026/08/"):
                pol[key] = None
        if data.get("authority_reference", "") and str(data["authority_reference"]).startswith("TP/2026/08/"):
            data["authority_reference"] = None
    return changes


def main() -> int:
    apply = "--apply" in sys.argv
    db = SessionLocal()
    try:
        changed = 0
        for r in db.query(Report).order_by(Report.created_at).all():
            data = dict(r.data or {})
            if r.status == "intake":
                continue
            signed = r.status == "Signed Off" or (data.get("sign_off") or {}).get("status") == "Signed Off"
            changes = scrub(data)
            if not changes:
                continue
            plate = _plate(data) or "?"
            if signed:
                print(f"  SIGNED (left as is, review by hand) {r.id} {plate}: {', '.join(changes)}")
                continue
            changed += 1
            print(f"  {'scrubbed' if apply else 'would scrub'} {r.id} {plate}: {', '.join(changes)}")
            if apply:
                r.data = data
                r.pdf_path = None  # re-rendered on next view
        if apply:
            db.commit()
        print(f"\n{changed} report(s) {'scrubbed' if apply else 'would be scrubbed'}.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
