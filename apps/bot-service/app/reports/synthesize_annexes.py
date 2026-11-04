"""Annex scaffolding and arithmetic for a case.

Builds only what follows from recorded facts:
  - Annex A: when it has no rows yet, one UNPRICED row per damaged part
    (name, and condition from the recorded damage type) for the surveyor to
    price from the workshop's quotation (app/parts/prefill.py then prices
    what past cases can). Rows with an estimate get their adjusted cost
    worked out from estimate x (1 - discount): the estimate is the LINE
    TOTAL as quoted (10 clips, est 50, adjusted 40), as in the firm's own
    sheets -- until 2026-11-01 it was multiplied by qty here and in
    PUT /annex-a, so 10 clips at "50" came to 400.
  - Annex B: exactly as recorded -- no labour lines are proposed, and the
    adjusted cost stays as typed.
  - Annex C: totals summed from the Annex A/B rows. No GST: the firm's
    letter has none (gst_amount / total_with_gst are left None).
The liability (BOLA) assessment, the sign-off and the insurance details are
never touched.

Until 2026-10-21 this "synthesized" complete priced dossiers: the real
CL 11900 / SLK 3063 Z annexes whenever that plate appeared, keyword-priced
parts with OEM-001 style part numbers for everything else, standard towing /
tuff-coat / LTA-reseal labour lines on every case, an 85% lump sum, a BOLA
liability finding, a reviewer name, and it overwrote the recorded estimated
and approved repair costs. All of that read as findings nobody had made.
"""
from typing import Any, Dict, List

DEFAULT_DISCOUNT = 0.20  # the firm's standard trade discount on parts


def adjusted_cost(workshop_est, discount_pct, is_net_item: bool = False) -> float:
    """The one place the Annex A line arithmetic lives: est x (1 - disc),
    no qty, discount 0 on a S/Nett item. 0 for an unpriced row."""
    est = float(workshop_est or 0.0)
    if not est:
        return 0.0
    discount = 0.0 if is_net_item else float(discount_pct or 0.0)
    return round(est * (1.0 - discount), 2)


def _annex_a_rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = [dict(it) for it in ((data.get("annex_a") or {}).get("items") or [])]
    if items:
        return items
    damage = [it for it in (data.get("damage_summary") or []) if it.get("part")]
    if not damage:
        damage = [{"part": p} for p in (data.get("damaged_parts") or []) if p]
    return [
        {
            "item_no": f"{i}.0",
            "part_name": it["part"],
            "condition": it.get("damage_type") or "",
            "action": "",
            "qty": 1.0,
            "qty_text": None,
            "workshop_est": 0.0,
            "discount_pct": DEFAULT_DISCOUNT,
            "adjusted_cost": 0.0,
            "is_net_item": False,
            "oem_part_number": it.get("oem_part_number"),
            "suggested_from": None,
            "usual_unit_price": None,
            "prefilled": False,
        }
        for i, it in enumerate(damage, start=1)
    ]


def synthesize_annexes_for_report(data: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(data)

    items_a = _annex_a_rows(data)
    for row in items_a:
        # Always from the estimate, so a row saved under the old qty rule
        # comes right the next time its case is touched.
        row["adjusted_cost"] = adjusted_cost(row.get("workshop_est"), row.get("discount_pct"), bool(row.get("is_net_item")))
    total_est_a = round(sum(float(r.get("workshop_est") or 0) for r in items_a), 2)
    total_adj_a = round(sum(float(r.get("adjusted_cost") or 0) for r in items_a), 2)
    data["annex_a"] = {"items": items_a, "total_workshop_est": total_est_a, "total_adjusted_cost": total_adj_a}

    items_b = [dict(it) for it in ((data.get("annex_b") or {}).get("items") or [])]
    total_est_b = round(sum(float(r.get("workshop_est") or 0) for r in items_b), 2)
    total_adj_b = round(sum(float(r.get("adjusted_cost") or 0) for r in items_b), 2)
    data["annex_b"] = {"items": items_b, "total_workshop_est": total_est_b, "total_adjusted_cost": total_adj_b}

    annex_c = dict(data.get("annex_c") or {})
    annex_c["workshop_total"] = round(total_est_a + total_est_b, 2)
    annex_c["adjusted_total"] = round(total_adj_a + total_adj_b, 2)
    # Never computed again: a case signed off with GST figures loses them
    # the next time its annexes are touched, as the letter never prints them.
    annex_c["gst_amount"] = None
    annex_c["total_with_gst"] = None
    data["annex_c"] = annex_c
    return data


def convert_per_unit_estimates(db) -> int:
    """One-off start-up step (db.init_db, "annex_a_line_totals_v1").

    Until 2026-11-04 the Annex A editor asked for the estimate PER UNIT and
    worked the adjusted cost out as qty x est x (1 - disc). The firm's
    W/shop Est is the line total (10 clips, 50, adjusted 40), which is what
    the save and the report use now. A row with qty > 1 that a person typed
    the old way keeps its adjusted cost by becoming est x qty; qty-1 rows
    and prefilled rows need no change. Returns how many rows changed; the
    caller commits.
    """
    from app.reports.models import Report

    changed = 0
    for r in db.query(Report).all():
        data = dict(r.data or {})
        items = [dict(it) for it in ((data.get("annex_a") or {}).get("items") or [])]
        touched = False
        for row in items:
            qty = float(row.get("qty") or 1.0)
            est = float(row.get("workshop_est") or 0.0)
            if qty > 1 and est > 0 and not row.get("prefilled"):
                row["workshop_est"] = round(est * qty, 2)
                touched = True
                changed += 1
        if touched:
            data["annex_a"] = {"items": items}
            r.data = synthesize_annexes_for_report(data)
            r.pdf_path = None  # rendered again on the next download
    return changed
