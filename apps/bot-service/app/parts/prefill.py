"""Prices a case's Annex A from the firm's past reports (app/parts/history.py).

The owner's rule (2026-11-01): every broken part carries a price as soon as
it is detected. The damage found names the parts; the imported CL reports
say which Annex A lines a surveyor wrote for each such part on the same
make and model, how many, and what one unit was adjusted to. This writes
those lines into the case, priced from the usual unit price and tagged
`prefilled`, for the surveyor to check and correct -- never a finding:
`suggested_from` says how many past cases each row rests on.

Deterministic and local: no Gemini call, nothing read but the case's own
data and the past_annex_lines table (never public_submissions). A damaged
part no past case covers stays as one unpriced row, as before.
"""
from sqlalchemy.orm import Session

from app.parts.history import FITTING_CONDITIONS, canonical_part, normalise_item, suggest
from app.reports.synthesize_annexes import DEFAULT_DISCOUNT, synthesize_annexes_for_report


def _damaged(data: dict) -> list[dict]:
    """The damaged parts, each once, with the AI's damage type: from
    damage_summary, else the older damaged_parts list."""
    seen: set[str] = set()
    out = []
    items = [it for it in (data.get("damage_summary") or []) if it.get("part")]
    if not items:
        items = [{"part": p} for p in (data.get("damaged_parts") or []) if p]
    for it in items:
        part = str(it["part"]).strip()
        if part and part not in seen:
            seen.add(part)
            out.append({"part": part, "damage_type": (it.get("damage_type") or "").strip(), "oem_part_number": it.get("oem_part_number")})
    return out


def _is_typed(row: dict) -> bool:
    """A row a person priced: an estimate, and not one this put there."""
    return float(row.get("workshop_est") or 0) > 0 and not row.get("prefilled")


def _unpriced_row(part: dict) -> dict:
    return {
        "part_name": part["part"],
        "condition": part["damage_type"],
        "action": "",
        "qty": 1.0,
        "qty_text": None,
        "workshop_est": 0.0,
        "discount_pct": DEFAULT_DISCOUNT,
        "adjusted_cost": 0.0,
        "is_net_item": False,
        "oem_part_number": part.get("oem_part_number"),
        "suggested_from": None,
        "usual_unit_price": None,
        "prefilled": False,
    }


def _scope_vehicle(scope: str) -> str:
    """"Honda Vezel" from suggest's scope label ("past Honda Vezel cases"),
    "Honda" when only the make matched, "all makes" otherwise."""
    if scope.startswith("past ") and scope.endswith(" cases"):
        return scope[len("past "):-len(" cases")]
    return "all makes"


def _rows_for(part: dict, suggestion: dict, group: dict) -> list[dict]:
    """The past cases' lines for one damaged part, as priced Annex A rows."""
    vehicle = _scope_vehicle(suggestion.get("scope") or "")
    lines = group["lines"]
    # The damaged part itself is the shortest wording among the lines that
    # are parts rather than fittings ("tailgate" before "tailgate stopper"
    # and "tailgate emblem vezel"); it alone takes the AI's damage type.
    parts_only = [ln for ln in lines if ln["kind"] != "fitting"]
    main = min(parts_only, key=lambda ln: (len(normalise_item(ln["name"])), ln["name"])) if parts_only else None
    rows = []
    for ln in lines:
        qty = float(ln.get("qty") or 1.0)
        price = ln.get("usual_unit_price")
        # The W/shop Est column is the workshop's line total. Past cases give
        # two figures per unit: what the workshop asked (usual_unit_est) and
        # what it was adjusted to (usual_unit_price). Putting the adjusted
        # figure in the estimate column discounted it twice (a bumper the
        # firm took from 1,020 to 816 came out as 816 -> 652.80).
        est_unit = ln.get("usual_unit_est")
        if ln is main:
            condition = part["damage_type"]
        elif ln["kind"] == "fitting" and ln.get("conditions"):
            # Every past case said Necessary / Serviceable: a fitting label,
            # not a damage finding, so it carries over.
            conds = [c for c in ln["conditions"] if c.strip().lower() in FITTING_CONDITIONS]
            condition = max(sorted(conds), key=conds.count) if conds else ""
        else:
            condition = ""
        nett = bool(ln.get("nett_majority"))
        if price and not est_unit:
            est_unit = price if nett else round(float(price) / (1.0 - DEFAULT_DISCOUNT), 2)
        cases = int(ln.get("cases") or 0)
        rows.append({
            "part_name": ln["name"],
            "condition": condition,
            "action": "",
            "qty": qty,
            "qty_text": ln.get("qty_text"),
            # The line total the workshop usually asked for this many.
            "workshop_est": round(float(est_unit) * qty, 2) if price and est_unit else 0.0,
            "discount_pct": 0.0 if nett else DEFAULT_DISCOUNT,
            "adjusted_cost": 0.0,  # synthesize_annexes works it out
            "is_net_item": nett,
            "oem_part_number": None,
            "suggested_from": f"{cases} past case{'s' if cases != 1 else ''} ({vehicle})",
            "usual_unit_price": price,
            "prefilled": True,
        })
    return rows


def prefill_annex_a(db: Session, data: dict) -> dict:
    """Returns the case data with Annex A priced from past cases and the
    annex totals worked out (synthesize_annexes_for_report).

    Rows a person priced (an estimate, not prefilled) are kept as they are;
    every other row -- prefilled earlier, or unpriced -- is replaced by the
    rows past cases wrote for the case's damaged parts: one row per past
    line, qty the past median (and "1set" when past cases wrote it so),
    workshop_est = usual unit price x qty, the past section's majority for
    List / S/Nett, 20% discount on list items, tagged prefilled with
    `suggested_from` and `usual_unit_price`. The damaged part itself takes
    the recorded damage type as its condition; a fitting (clips, seals)
    keeps the past cases' "Necessary"; anything else is left blank. A
    damaged part with no history gets one unpriced row. List items come
    first, then S/Nett, numbered "1.0"... in that order.
    """
    data = dict(data)
    vehicle = data.get("vehicle_info") or {}
    make, model = vehicle.get("make"), vehicle.get("model")
    kept = [dict(r) for r in ((data.get("annex_a") or {}).get("items") or []) if _is_typed(r)]
    kept_names = [r.get("part_name", "") for r in kept]
    kept_keys = {normalise_item(n) for n in kept_names}

    new_rows: list[dict] = []
    seen_names: set[str] = set()
    for part in _damaged(data):
        name = canonical_part(part["part"])
        found = None
        if name and name != "Other / Not Listed":
            # Per part, so two sides of one family ("Left Tail Lamp" and
            # "Right Tail Lamp") each get their own lines; lines already
            # typed into this Annex A are not repeated.
            suggestion = suggest(db, make=make, model=model, damaged_parts=[name], existing_items=kept_names)
            found = next((g for g in suggestion["groups"] if g["reason"] == "damaged" and g["lines"]), None)
        if not found:
            # No past case covers it (or its lines are all typed already).
            if normalise_item(part["part"]) not in kept_keys and part["part"] not in seen_names:
                seen_names.add(part["part"])
                new_rows.append(_unpriced_row(part))
            continue
        for row in _rows_for(part, suggestion, found):
            if row["part_name"] in seen_names or normalise_item(row["part_name"]) in kept_keys:
                continue
            seen_names.add(row["part_name"])
            new_rows.append(row)

    items = kept + [r for r in new_rows if not r["is_net_item"]] + [r for r in new_rows if r["is_net_item"]]
    for i, row in enumerate(items, start=1):
        row["item_no"] = f"{i}.0"
    data["annex_a"] = {"items": items}
    return synthesize_annexes_for_report(data)


def prefill_counts(data: dict) -> tuple[int, int]:
    """(rows priced from past cases, rows still unpriced) in Annex A."""
    items = (data.get("annex_a") or {}).get("items") or []
    filled = sum(1 for r in items if r.get("prefilled") and float(r.get("workshop_est") or 0) > 0)
    unpriced = sum(1 for r in items if float(r.get("workshop_est") or 0) <= 0)
    return filled, unpriced
