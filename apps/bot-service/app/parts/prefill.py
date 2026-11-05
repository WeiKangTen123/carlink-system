"""Keeps a case's Annex A in step with its damage list, pricing what the
firm's past reports can (app/parts/history.py).

The owner's rule (2026-11-01): every broken part carries a price as soon as
it is detected. The damage found names the parts; the imported CL reports
say which Annex A lines a surveyor wrote for each such part on the same
make and model, how many, and what one unit was adjusted to. This writes
those lines into the case, priced from the usual unit price and tagged
`prefilled`, for the surveyor to check and correct -- never a finding:
`suggested_from` says how many past cases each row rests on.

The handover rule (2026-11-04, contract2 section 1): damage_summary says
what is damaged; annex_a.items says what the report lists and at what
price. Flow is one way, through sync_annex_a: rows the machine made and
nobody priced (origin detection/history, estimate 0) are rebuilt from the
current damage list; rows a person priced, typed or looked up are kept as
they are, and flagged stale when the part they were made for is no longer
damaged -- never deleted by the machine. Parts the surveyor dismissed
(data["dismissed_parts"]) get no rows back.

Deterministic and local: no Gemini call, nothing read but the case's own
data and the past_annex_lines table (never public_submissions). A damaged
part no past case covers stays as one unpriced row, as before.
"""
from sqlalchemy.orm import Session

from typing import Iterable

from app.parts.history import FITTING_CONDITIONS, canonical_part, normalise_item, side_of, suggest
from app.reports.synthesize_annexes import DEFAULT_DISCOUNT, synthesize_annexes_for_report

ORIGINS = ("detection", "history", "lookup", "typed")
# How protected a row is: a client may raise a row's origin, never lower it.
# lookup and typed are both a person's (sync keeps either), so one may
# become the other: [Use] on a lookup price, or overtyping that price.
ORIGIN_RANK = {"detection": 0, "history": 1, "lookup": 2, "typed": 2}


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


def _est(row: dict) -> float:
    return float(row.get("workshop_est") or 0)


def origin_of(row: dict, damaged_parts: Iterable[str] = ()) -> str:
    """The row's origin, derived for rows saved before the field existed:
    prefilled -> history; an estimate -> a person typed it; an unpriced
    row named exactly like a current damaged part is the detection
    scaffold (synthesize_annexes._annex_a_rows wrote it so); any other
    unpriced row is a line a person typed -- the safe reading, since a
    "detection" row is the one kind a sync may drop."""
    origin = row.get("origin")
    if origin in ORIGINS:
        return origin
    if row.get("prefilled"):
        return "history"
    if _est(row) > 0:
        return "typed"
    return "detection" if (row.get("part_name") or "").strip() in set(damaged_parts) else "typed"


def _is_typed(row: dict) -> bool:
    """A row a person wrote or priced: tagged typed (the dashboard sends
    origin "typed" on an edit), or an estimate this didn't put there."""
    return row.get("origin") == "typed" or (_est(row) > 0 and not row.get("prefilled"))


def _machine_owned(row: dict, damaged_parts: Iterable[str]) -> bool:
    """A row sync may rebuild: the machine made it and nobody priced it.
    Everything else -- priced (from anywhere), typed even when unpriced,
    looked up, or picked by a person from the suggestions panel (it says
    suggested_from but was never prefilled) -- is a person's and is kept
    as it is."""
    if row.get("suggested_from") and not row.get("prefilled"):
        return False
    return origin_of(row, damaged_parts) in ("detection", "history") and _est(row) <= 0


def line_key(name: str) -> tuple[str, str | None]:
    """One key per Annex A wording AND side: normalise_item strips LH/RH,
    so on its own a kept "Tail lamp LH" would stand for "Tail lamp RH" too."""
    return normalise_item(name or ""), side_of(name or "")


def _covered(name: str, kept_keys: set) -> bool:
    """Whether a kept row already stands for this line: the same wording on
    the same side, or the same wording with no side at all."""
    norm, side = line_key(name)
    return (norm, side) in kept_keys or (norm, None) in kept_keys


def _part_key(name: str | None) -> str | None:
    """One key per vehicle part, so "Tail gate" in an old row and "Tail
    Gate" in damage_summary are the same part."""
    if not name:
        return None
    return canonical_part(name) or name.strip()


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
        "origin": "detection",
        "source_part": part["part"],
        "stale": False,
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
            "origin": "history",
            "source_part": part["part"],
            "stale": False,
        })
    return rows


def sync_annex_a(db: Session, data: dict, price_book: bool) -> dict:
    """Returns the case data with Annex A following its damage list and
    the annex totals worked out (synthesize_annexes_for_report).

    Kept as they are: every row a person priced, typed (origin "typed",
    even unpriced) or looked up, and rows from past cases that carry a
    price. A kept row made for a part that is no longer damaged gets
    stale = True (and loses it when the part is damaged again); a line
    with no source_part is a hand-added one and is never stale.

    Rebuilt from the current damage list: the unpriced detection/history
    rows. For a price-book user (api/parts._sees_price_book) each damaged
    part gets the rows past cases wrote for it: one row per past line, qty
    the past median (and "1set" when past cases wrote it so), workshop_est
    = usual unit price x qty, the past section's majority for List /
    S/Nett, 20% discount on list items, tagged prefilled with
    `suggested_from` and `usual_unit_price`. The damaged part itself takes
    the recorded damage type as its condition; a fitting (clips, seals)
    keeps the past cases' "Necessary"; anything else is left blank. A part
    with no same-make history -- or anyone else's case, where the price
    book is hidden -- gets one unpriced row. Parts in data["dismissed_parts"]
    get no rows. Kept rows first in their order, then the new List items,
    then S/Nett, numbered "1.0"... in that order.
    """
    data = dict(data)
    vehicle = data.get("vehicle_info") or {}
    make, model = vehicle.get("make"), vehicle.get("model")
    damaged = _damaged(data)
    damaged_names = {p["part"] for p in damaged}
    damaged_keys = {_part_key(p["part"]) for p in damaged}
    dismissed = {_part_key(p) for p in (data.get("dismissed_parts") or []) if p}

    kept = []
    for row in ((data.get("annex_a") or {}).get("items") or []):
        if _machine_owned(row, damaged_names):
            continue
        row = dict(row)
        row["origin"] = origin_of(row, damaged_names)  # written back, so old rows are derived once
        row.setdefault("source_part", None)
        row["stale"] = bool(row["source_part"]) and _part_key(row["source_part"]) not in damaged_keys
        kept.append(row)
    kept_names = [r.get("part_name", "") for r in kept]
    kept_keys = {line_key(n) for n in kept_names}

    new_rows: list[dict] = []
    seen_names: set[str] = set()
    for part in damaged:
        if _part_key(part["part"]) in dismissed:
            continue
        name = canonical_part(part["part"])
        side = side_of(part["part"])
        # Kept lines that stand for this part's side (or no side): suggest()
        # strips LH/RH when it leaves out lines already in the table, so a
        # kept "Tail lamp LH" must not hide the lines for Right Tail Lamp.
        existing = [n for n in kept_names if side_of(n) in (None, side)]
        found = None
        if price_book and name and name != "Other / Not Listed":
            # Per part, so two sides of one family ("Left Tail Lamp" and
            # "Right Tail Lamp") each get their own lines; lines already
            # typed into this Annex A are not repeated.
            suggestion = suggest(db, make=make, model=model, damaged_parts=[name], existing_items=existing)
            found = next((g for g in suggestion["groups"] if g["reason"] == "damaged" and g["lines"]), None)
            # Another make's prices are no estimate for this car: with no
            # same-make history the part stays unpriced (the suggestions
            # panel still offers those lines, labelled, for a person to pick).
            if found and suggestion.get("scope") == "all imported cases":
                found = None
        if not found:
            # No past case covers it (or its lines are all typed already).
            if not _covered(part["part"], kept_keys) and part["part"] not in seen_names:
                seen_names.add(part["part"])
                new_rows.append(_unpriced_row(part))
            continue
        for row in _rows_for(part, suggestion, found):
            if row["part_name"] in seen_names or _covered(row["part_name"], kept_keys):
                continue
            seen_names.add(row["part_name"])
            new_rows.append(row)

    items = kept + [r for r in new_rows if not r["is_net_item"]] + [r for r in new_rows if r["is_net_item"]]
    for i, row in enumerate(items, start=1):
        row["item_no"] = f"{i}.0"
    data["annex_a"] = {"items": items}
    # The rows are decided here: an empty table stays empty (no scaffold
    # for a part the surveyor dismissed).
    return synthesize_annexes_for_report(data, scaffold_annex_a=False)


def prefill_annex_a(db: Session, data: dict) -> dict:
    """sync_annex_a for someone who may see the price book: the name the
    prefill route and older callers use."""
    return sync_annex_a(db, data, price_book=True)


def prefill_counts(data: dict) -> tuple[int, int]:
    """(rows priced from past cases, rows still unpriced) in Annex A."""
    items = (data.get("annex_a") or {}).get("items") or []
    filled = sum(1 for r in items if r.get("prefilled") and float(r.get("workshop_est") or 0) > 0)
    unpriced = sum(1 for r in items if float(r.get("workshop_est") or 0) <= 0)
    return filled, unpriced
