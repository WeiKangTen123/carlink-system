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

The old code also copied facts of one real benchmark case into other cases:
that car's odometer reading, its workshop and the firm's surveyor as the
reviewer / approver. Those are real people's and cars' details, so they are
not written in this public file: give them on the command line. Each check
runs only when its values are given (no defaults); the run says which ones
were skipped.

    --benchmark-plate PLATE --benchmark-odometer READING
        the odometer as the old code copied it: cleared on every car but the
        benchmark one (the two options go together)
    --copied-workshop TEXT    (repeatable) a workshop string, exactly as saved
    --copied-signer NAME      (repeatable) a name saved as reviewer / approver /
        surveyor on an unsigned report (and as "NAME (Carlink Consultancy)",
        the adjuster)

Back up the database first.
"""
import argparse
import copy
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import Report  # noqa: E402
from app.reports.synthesize_annexes import synthesize_annexes_for_report  # noqa: E402

# Made-up workshop strings the old code wrote (the benchmark case's real
# workshop is given with --copied-workshop).
INVENTED_WORKSHOPS = {
    "Sin Ming Autocare (Cluster A)",
    "V-Kool Automotive (Ubi Techpark)",
    "ComfortDelGro Engineering (Toh Guan Hub)",
}
INVENTED_ODOMETERS = {"185,742 km", "074,820 km"}
INVENTED_POLICE_OFFICER = "Insp. Marcus Tan"
INVENTED_POLICE_STATION = "Traffic Police HQ (Ubi Ave 3)"
INVENTED_BOLA_RATIONALES = (
    "Rear-end impact into stationary lead vehicle. Tortfeasor failed to maintain safe braking distance under BOLA Rule 14.",
    "Tortfeasor vehicle collided directly into the rear of stationary insured vehicle.",
    "Third party vehicle failed to maintain safe lateral clearance",
    "Third party vehicle emerged or turned into insured path of travel",
)
# Annex B lines the generator added to every case.
GENERATED_LABOUR = (
    "Towing charges from incident scene to workshop", "To check wiring system", "To tuff coat",
    "To adjust chassis alignment", "To respray affected areas", "To renew damaged parts",
    "To LTA remove/reseal", "To remove/refix", "To remove & refix", "To remove/replace reverse sensor",
)
# OEM numbers of the hard-coded benchmark annex (Honda catalogue numbers).
BENCHMARK_OEMS = {"71501-T7A-000", "68100-T7A-000ZZ", "33550-T7A-H01", "71530-T7A-000"}


def _plate(data: dict) -> str:
    return ((data.get("vehicle_info") or {}).get("plate_number") or "").replace(" ", "").upper()


@dataclass(frozen=True)
class CopiedValues:
    """The benchmark case's real values the old code copied into other cases.
    Given on the command line, never written here; each empty one skips its
    check."""
    benchmark_plate: str = ""
    benchmark_odometer: str = ""
    workshops: frozenset[str] = frozenset()
    signers: frozenset[str] = frozenset()

    def skipped(self) -> list[str]:
        out = []
        if not self.benchmark_odometer:
            out.append("copied odometer (--benchmark-plate/--benchmark-odometer)")
        if not self.workshops:
            out.append("copied workshop (--copied-workshop)")
        if not self.signers:
            out.append("copied reviewer / adjuster (--copied-signer)")
        return out


def scrub(data: dict, copied: CopiedValues = CopiedValues()) -> list[str]:
    """Edits data in place; returns what changed."""
    changes: list[str] = []
    ins = data.get("insurance_details") or {}
    if ins.get("workshop_assigned") in INVENTED_WORKSHOPS | copied.workshops:
        changes.append(f"workshop {ins['workshop_assigned']!r}")
        ins["workshop_assigned"] = None
    if ins.get("adjuster_assigned") in {f"{name} (Carlink Consultancy)" for name in copied.signers}:
        changes.append("adjuster")
        ins["adjuster_assigned"] = None
    claim_no = ins.get("claim_number") or ""
    if re.fullmatch(r"CL-[^-]+-(SG|[A-Z0-9]+)", claim_no) and (claim_no.startswith("CL-11900-") or claim_no.endswith("-SG") or claim_no.endswith(_plate(data) or "\0")):
        changes.append(f"claim number {claim_no!r}")
        ins["claim_number"] = None

    annex_a_items = ((data.get("annex_a") or {}).get("items") or [])
    oems = {str(it.get("oem_part_number") or "") for it in annex_a_items}
    # Placeholder part numbers, or the hard-coded benchmark annex (even on
    # the benchmark car it was copied in, not assessed in this system).
    generated_a = any(re.fullmatch(r"OEM-\d{3}", o) for o in oems) or bool(oems & BENCHMARK_OEMS)
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
            if sign.get(key) and sign.get(key) in copied.signers:
                sign[key] = None
                changes.append(f"sign_off.{key}")
        if sign.get("liability_opinion", "") and "BOLA #14" in sign["liability_opinion"]:
            sign["liability_opinion"] = None
            changes.append("sign_off.liability_opinion")
        if sign.get("agreed_quantum") and generated_a:
            sign["agreed_quantum"] = None

    v = data.get("vehicle_info") or {}
    odo = v.get("odometer_reading")
    copied_odo = (bool(copied.benchmark_odometer) and odo == copied.benchmark_odometer
                  and copied.benchmark_plate not in _plate(data))
    if odo in INVENTED_ODOMETERS or copied_odo:
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


def parse_args(argv: list[str]) -> tuple[bool, CopiedValues]:
    ap = argparse.ArgumentParser(description="Clear values the old code invented (a dry run unless --apply).")
    ap.add_argument("--apply", action="store_true", help="write the changes (default: report only)")
    ap.add_argument("--benchmark-plate", default="", help="the benchmark car's plate, whose odometer the old code copied")
    ap.add_argument("--benchmark-odometer", default="", help="that odometer reading, exactly as saved")
    ap.add_argument("--copied-workshop", action="append", default=[], help="a copied workshop string, exactly as saved")
    ap.add_argument("--copied-signer", action="append", default=[], help="a name the old code saved as reviewer / approver")
    args = ap.parse_args(argv)
    plate = args.benchmark_plate.replace(" ", "").upper()
    if bool(plate) != bool(args.benchmark_odometer):
        ap.error("--benchmark-plate and --benchmark-odometer go together")
    copied = CopiedValues(
        benchmark_plate=plate, benchmark_odometer=args.benchmark_odometer,
        workshops=frozenset(w for w in args.copied_workshop if w),
        signers=frozenset(s for s in args.copied_signer if s),
    )
    return args.apply, copied


def main(argv: list[str] | None = None) -> int:
    apply, copied = parse_args(sys.argv[1:] if argv is None else argv)
    for check in copied.skipped():
        print(f"  skipped (no value given): {check}")
    db = SessionLocal()
    try:
        changed = 0
        for r in db.query(Report).order_by(Report.created_at).all():
            # A deep copy: scrub() edits nested dicts in place, and a JSON column
            # compared equal to its (also edited) old value is never written.
            data = copy.deepcopy(r.data or {})
            if r.status == "intake":
                continue
            signed = r.status == "Signed Off" or (data.get("sign_off") or {}).get("status") == "Signed Off"
            changes = scrub(data, copied)
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
