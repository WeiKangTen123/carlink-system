"""Annex scaffolding and arithmetic for a case.

Builds only what follows from recorded facts:
  - Annex A: when it has no rows yet, one UNPRICED row per damaged part
    (name, and condition from the recorded damage type) for the surveyor to
    price from the workshop's quotation. Rows the surveyor has priced get
    their adjusted cost worked out from estimate x qty x (1 - discount).
  - Annex B: exactly as recorded -- no labour lines are proposed.
  - Annex C: totals summed from the Annex A/B rows; GST worked out on the
    agreed lump sum once the surveyor has recorded one.
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

GST_RATE = 0.09
DEFAULT_DISCOUNT = 0.20  # the firm's standard trade discount on parts


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
            "workshop_est": 0.0,
            "discount_pct": DEFAULT_DISCOUNT,
            "adjusted_cost": 0.0,
            "is_net_item": False,
            "oem_part_number": it.get("oem_part_number"),
        }
        for i, it in enumerate(damage, start=1)
    ]


def synthesize_annexes_for_report(data: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(data)

    items_a = _annex_a_rows(data)
    for row in items_a:
        estimate = float(row.get("workshop_est") or 0.0)
        if estimate and not row.get("adjusted_cost"):
            qty = float(row.get("qty") or 1.0)
            discount = 0.0 if row.get("is_net_item") else float(row.get("discount_pct") or 0.0)
            row["adjusted_cost"] = round(qty * estimate * (1.0 - discount), 2)
    total_est_a = round(sum(float(r.get("workshop_est") or 0) * float(r.get("qty") or 1) for r in items_a), 2)
    total_adj_a = round(sum(float(r.get("adjusted_cost") or 0) for r in items_a), 2)
    data["annex_a"] = {"items": items_a, "total_workshop_est": total_est_a, "total_adjusted_cost": total_adj_a}

    items_b = [dict(it) for it in ((data.get("annex_b") or {}).get("items") or [])]
    total_est_b = round(sum(float(r.get("workshop_est") or 0) for r in items_b), 2)
    total_adj_b = round(sum(float(r.get("adjusted_cost") or 0) for r in items_b), 2)
    data["annex_b"] = {"items": items_b, "total_workshop_est": total_est_b, "total_adjusted_cost": total_adj_b}

    annex_c = dict(data.get("annex_c") or {})
    annex_c["workshop_total"] = round(total_est_a + total_est_b, 2)
    annex_c["adjusted_total"] = round(total_adj_a + total_adj_b, 2)
    annex_c.setdefault("gst_rate", GST_RATE)
    lump_sum = annex_c.get("agreed_lump_sum")
    if lump_sum:
        rate = float(annex_c.get("gst_rate") or GST_RATE)
        annex_c["gst_amount"] = round(float(lump_sum) * rate, 2)
        annex_c["total_with_gst"] = round(float(lump_sum) * (1 + rate), 2)
    else:
        annex_c["gst_amount"] = None
        annex_c["total_with_gst"] = None
    data["annex_c"] = annex_c
    return data
