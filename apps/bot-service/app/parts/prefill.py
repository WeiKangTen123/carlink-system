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

from app.parts.history import (
    END_FALLBACK,
    FITTING_CONDITIONS,
    _without_side,
    canonical_part,
    family_and_side,
    firm_wording,
    normalise_item,
    side_of,
    suggest,
)
from app.reports.synthesize_annexes import (
    DEFAULT_DISCOUNT,
    REPAIR_CONDITION,
    REPAIR_DISCOUNT,
    synthesize_annexes_for_report,
)
from app.reports.taxonomy import (
    CANONICAL_PARTS,
    OTHER_PART,
    SIDE_CODES,
    annex_condition,
    body_type_for,
    clean_part_name,
    firm_part_name,
    other_part_name,
    unsided_part,
)

ORIGINS = ("detection", "history", "lookup", "typed")
# How protected a row is: a client may raise a row's origin, never lower it.
# lookup and typed are both a person's (sync keeps either), so one may
# become the other: [Use] on a lookup price, or overtyping that price.
ORIGIN_RANK = {"detection": 0, "history": 1, "lookup": 2, "typed": 2}


def _damaged(data: dict) -> list[dict]:
    """The damaged parts, each once, with what the main Annex A row says
    about them: the condition in the firm's words (taxonomy.annex_condition:
    the item's condition, else the firm word for its damage type, else the
    type as written) and the remarks (Replace / Repair): from
    damage_summary, else the older damaged_parts list. An "Other / Not
    Listed" item with a name is a part of its own (item_key): "other" is
    its name, "key" and "source" what its rows are found by."""
    seen: set[str] = set()
    out = []
    items = [it for it in (data.get("damage_summary") or []) if it.get("part")]
    if not items:
        items = [{"part": p} for p in (data.get("damaged_parts") or []) if p]
    for it in items:
        part = str(it["part"]).strip()
        other = other_part_name(it)
        # Two unlisted parts were one: both were "Other / Not Listed".
        ident = item_source(it) if other else part
        if part and ident not in seen:
            seen.add(ident)
            out.append({
                "part": part,
                "other": other,
                "key": item_key(it),
                "source": ident,
                "damage_type": (it.get("damage_type") or "").strip(),
                "condition": annex_condition(it),
                "remarks": (it.get("remarks") or "").strip(),
                "oem_part_number": it.get("oem_part_number"),
                "unsided": side_unconfirmed(it),
            })
    return out


def side_unconfirmed(item: dict) -> bool:
    """A side the pipeline inferred (intake/pipeline.resolve_sides) and no
    person has confirmed yet. Its Annex A rows are worded and looked up as
    "(side undetermined)": the report the firm signs never states a side
    nobody established (owner decision, 2026-11-08). The rows still name
    the sided part as their source_part, so they aren't stale and tab 1's
    cross-reference finds them; once a person confirms the part (ticks it,
    or any edit, which clears side_inferred) the next sync rebuilds the
    unpriced rows with LH / RH."""
    return bool(item.get("side_inferred")) and not item.get("human_verified")


def _main_row_terms(part: dict) -> dict:
    """The fields a damaged part's own row takes from the damage item, and
    the `main` mark that lets a later sync find that row again
    (_refresh_kept). Condition "Repair" is the firm's "repair, don't
    renew": the line stays priced (the workshop asked for it) and is
    disallowed at 100%, as in the firm's sheets -- a list item, since a
    S/Nett line takes no discount."""
    terms = {"condition": part["condition"], "action": part["remarks"], "main": True}
    if part["condition"] == REPAIR_CONDITION:
        terms.update(discount_pct=REPAIR_DISCOUNT, is_net_item=False)
    return terms


def _refresh_kept(row: dict, part: dict, sided_name) -> None:
    """A kept row made for `part` follows what a person changed on tab 1,
    its price left as it is -- unless the row is theirs (origin "typed": a
    person edited it in Annex A, and that stands).
      - The part's own row (main) takes the item's condition and remarks;
        Repair disallows it at 100%, and leaving Repair puts back the
        standard discount (owner decision via review, 2026-11-08: before
        this a tab-1 change never reached a priced row, so the report could
        print one condition while tab 1 showed another).
      - Once an inferred side is confirmed, a row worded without the side
        while it was unconfirmed takes the sided wording (sided_name maps a
        wording to it; None when the part isn't sided now)."""
    if row.get("origin") == "typed":
        return
    if row.get("main"):
        was_repair = row.get("condition") == REPAIR_CONDITION
        row["condition"], row["action"] = part["condition"], part["remarks"]
        if part["condition"] == REPAIR_CONDITION:
            row["discount_pct"], row["is_net_item"] = REPAIR_DISCOUNT, False
        elif was_repair and float(row.get("discount_pct") or 0) == REPAIR_DISCOUNT:
            row["discount_pct"] = DEFAULT_DISCOUNT
    if sided_name is not None and row.get("origin") in ("history", "detection") and side_of(row.get("part_name") or "") is None:
        row["part_name"] = sided_name(row.get("part_name") or "")


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


def _held_family(family: str) -> str:
    """The family an end-less past line is held under: a rim or tyre line
    with no front/rear ("Sport rim") is drawn from "Wheel"/"Tyre"
    (history.END_FALLBACK) whichever wheel asked for it, so a kept row's
    "Rear Wheel" and the line's "Wheel" must compare equal, or the next sync
    prints it again (review, 2026-11-10)."""
    return END_FALLBACK.get(family, family)


def _family(part: str) -> str:
    """The past-case family of a damaged part: "Left Tail Lamp" and "Right
    Tail Lamp" are both "Tail Lamp". A named unlisted part is its own."""
    if _is_other_source(part):
        return _part_key(part)
    return family_and_side(canonical_part(part) or part)[0]


def _other_source(name: str) -> str:
    """The source_part of a named "Other / Not Listed" item's rows: "Other
    / Not Listed: Rear air vent" -- the part, and which unlisted part."""
    return f"{OTHER_PART}: {name}"


def _is_other_source(name: str | None) -> bool:
    return (name or "").startswith(OTHER_PART + ":")


def _part_key(name: str | None) -> str | None:
    """One key per vehicle part, so "Tail gate" in an old row and "Tail
    Gate" in damage_summary are the same part; a named unlisted part's
    source (_other_source) keys by its name in any case."""
    if not name:
        return None
    if _is_other_source(name):
        return f"{OTHER_PART}: {' '.join(name[len(OTHER_PART) + 1:].lower().split())}"
    return canonical_part(name) or name.strip()


def item_key(item: dict) -> str | None:
    """The part a damage item stands for, as sync_annex_a keys it: its
    canonical part, or for a named "Other / Not Listed" item that name (two
    unlisted parts are two parts). For callers that compare damage items
    (a re-read keeping a ticked one, dismissing a removed one)."""
    other = other_part_name(item)
    return _part_key(_other_source(other) if other else str(item.get("part") or "").strip())


def row_name(db: Session | None, part: str, vehicle: dict, unsided: bool = False) -> str:
    """The Annex A wording for a damaged part's row when no past line is
    priced for it, never the canonical name itself ("Boot Lid" was printed
    in the firm's report until 2026-11-06): for a canonical name, the floor
    (taxonomy.FIRM_PART_NAMES) whenever it has one -- a family's past
    wording often names a different sub-part (history.firm_wording) -- else
    the same make's past wording (when `db` is given). A pre-taxonomy case's
    free text ("Tail gate lock striker") is kept as written: its family's
    wording would only be coarser. unsided: worded without LH / RH
    (side_unconfirmed)."""
    if part not in CANONICAL_PARTS:
        return part
    make, model = vehicle.get("make"), vehicle.get("model")
    body = vehicle.get("body_type") or body_type_for(make, model)
    floor = firm_part_name(part, body, unsided=unsided)
    if floor:
        return floor
    lookup = (unsided_part(part) or part) if unsided else part
    return (firm_wording(db, lookup, make, model) if db is not None else None) or part


def _other_wording(name: str) -> str:
    """A named unlisted part as the firm would print it: the side last as
    LH / RH ("Left rear air vent" -> "Rear air vent LH")."""
    side = side_of(name)
    base = clean_part_name(_without_side(name)) or name
    return f"{base} {SIDE_CODES[side]}" if side else base


def item_source(item: dict) -> str:
    """The source_part an item's Annex A rows carry: its part, or for a
    named unlisted part "Other / Not Listed: <name>". A row made with the
    bare "Other / Not Listed" stands for every unnamed one instead."""
    other = other_part_name(item)
    return _other_source(other) if other else str(item.get("part") or "").strip()


def item_row_name(item: dict, vehicle: dict, unsided: bool = False) -> str:
    """An item's Annex A wording without past cases (the floor): a named
    unlisted part by its name, anything else as row_name(None, ...). For
    synthesize_annexes' scaffold, which has no database."""
    other = other_part_name(item)
    if other:
        return _other_wording(other)
    return row_name(None, str(item.get("part") or "").strip(), vehicle, unsided=unsided)


# Words that say where or on what, not what: "window regulator" is the
# "front door glass regulator mechanism".
_NOT_WHAT = {"front", "rear", "door", "glass", "window", "with", "and", "the", "of", "c", "w", "set", "pcs"}
# A last word that names no part of its own: the head is the word before.
_NOT_HEAD = {"mechanism", "unit", "set", "pcs"}


def _words(text: str) -> list[str]:
    """The words of a line's wording, plurals as singular ("clips" ->
    "clip")."""
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
            for w in normalise_item(text).split()]


def _head(words: list[str]) -> str | None:
    """The thing a wording names: its last word ("motor" in "... regulator
    mechanism motor", "regulator" in "... regulator mechanism")."""
    rest = list(words)
    while len(rest) > 1 and rest[-1] in _NOT_HEAD:
        rest.pop()
    return rest[-1] if rest else None


def _matching_line(name: str, lines: list[dict]) -> dict | None:
    """The past line a named unlisted part is: the one naming the same
    thing (_head: "motor", "garnish", "vent") with most of its other words,
    the closest first. None when no line does -- a "Rear bumper bracket" is
    not the "Rear bumper fascia", nor "Door glass" the "door outer glass
    moulding"."""
    words = _words(name)
    if not words:
        return None
    head, wanted = _head(words), set(words) - _NOT_WHAT or set(words)
    best, best_score = None, None
    for ln in lines:
        line_words = _words(ln["name"])
        have = set(line_words)
        if _head(line_words) != head:
            continue
        overlap = len(wanted & have) / len(wanted)
        if overlap < 0.6:
            continue
        # Most of the name found, then the fewest words beside it.
        score = (overlap, -len(have - set(words)), -len(ln["name"]))
        if best_score is None or score > best_score:
            best, best_score = ln, score
    return best


def _unpriced_row(part: dict, name: str) -> dict:
    row = {
        "part_name": name,
        "condition": "",
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
        "source_part": part.get("source") or part["part"],
        "stale": False,
    }
    row.update(_main_row_terms(part))
    return row


def _scope_vehicle(scope: str) -> str:
    """"Honda Vezel" from suggest's scope label ("past Honda Vezel cases"),
    "Honda" when only the make matched, "all makes" otherwise."""
    if scope.startswith("past ") and scope.endswith(" cases"):
        return scope[len("past "):-len(" cases")]
    return "all makes"


def _rows_for(part: dict, suggestion: dict, group: dict, floor: str | None = None) -> list[dict]:
    """The past cases' lines for one damaged part, as priced Annex A rows.
    floor: the part's own wording (row_name without past cases)."""
    vehicle = _scope_vehicle(suggestion.get("scope") or "")
    lines = group["lines"]
    # The damaged part itself, which alone takes the item's condition and
    # remarks (_main_row_terms): the line worded as the part is ("Front
    # wheel rim" for a front wheel), else the shortest wording among the
    # lines that are parts rather than fittings ("tailgate" before
    # "tailgate stopper"). Shortest alone made another model's "Sport rim"
    # the main row of a front wheel the same make's "Front wheel rim" was
    # also priced for.
    parts_only = [ln for ln in lines if ln["kind"] != "fitting"]
    floor_key = normalise_item(floor) if floor else None
    main = next((ln for ln in parts_only if floor_key and normalise_item(ln["name"]) == floor_key), None)
    if main is None and parts_only:
        main = min(parts_only, key=lambda ln: (len(normalise_item(ln["name"])), ln["name"]))
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
            condition = part["condition"]  # _main_row_terms below
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
        row = {
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
            "source_part": part.get("source") or part["part"],
            "stale": False,
        }
        if ln is main:
            row.update(_main_row_terms(part))
        rows.append(row)
    return rows


def _sided_namer(db: Session | None, part: dict, vehicle: dict):
    """For a part on a confirmed side: wording -> the same line's sided
    wording ("Tail lamp" -> "Tail lamp LH", "Tail lamp clips" -> the past
    cases' own form of it), found by line key among the lines the same
    make's history would write for the sided part, and the floor. Built on
    first use (most syncs rename nothing). None when the part has no
    confirmed side."""
    if part["unsided"] or part.get("other") or part["part"] not in CANONICAL_PARTS or side_of(part["part"]) is None:
        return None
    cache: dict[str, str] | None = None

    def build() -> dict[str, str]:
        names: dict[str, str] = {}
        if db is not None:
            make, model = vehicle.get("make"), vehicle.get("model")
            suggestion = suggest(db, make=make, model=model, damaged_parts=[part["part"]], existing_items=[])
            if suggestion.get("scope") != "all imported cases":
                for group in suggestion["groups"]:
                    if group["reason"] == "damaged":
                        for ln in group["lines"]:
                            names.setdefault(normalise_item(ln["name"]), ln["name"])
        names.setdefault(normalise_item(row_name(None, part["part"], vehicle, unsided=True)), row_name(None, part["part"], vehicle))
        return names

    def sided(wording: str) -> str:
        nonlocal cache
        if cache is None:
            cache = build()
        return cache.get(normalise_item(wording), wording)

    return sided


def _other_rows(db: Session | None, part: dict, vehicle: dict, price_book: bool) -> list[dict]:
    """The row for a named "Other / Not Listed" part: when its name reads
    as a known family (history.family_of: "window regulator motor") and the
    same make's past cases wrote a line that is this part (_matching_line),
    that line, priced, with the item's condition; else one unpriced row by
    its own name. Never the family's whole kit: the part is one line of it,
    and the rest is for the listed parts to bring."""
    name = part["other"]
    family_part = canonical_part(name) if price_book and db is not None else None
    if family_part:
        make, model = vehicle.get("make"), vehicle.get("model")
        suggestion = suggest(db, make=make, model=model, damaged_parts=[family_part], existing_items=[], unsided=True)
        group = next((g for g in suggestion["groups"] if g["reason"] == "damaged" and g["lines"]), None)
        line = _matching_line(name, group["lines"]) if group and suggestion.get("scope") != "all imported cases" else None
        if line is not None:
            row = _rows_for(part, suggestion, {"lines": [line]})[0]
            row.update(_main_row_terms(part))
            side = side_of(name)
            if side:
                row["part_name"] = f"{row['part_name']} {SIDE_CODES[side]}"
            return [row]
    return [_unpriced_row(part, _other_wording(name))]


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
    the item's condition in the firm's words and its remarks as the
    Remarks (condition "Repair": disallowed at 100%); a fitting (clips,
    seals) keeps the past cases' "Necessary"; anything else is left blank.
    A part with no same-make history -- or anyone else's case, where the
    price book is hidden -- gets one unpriced row, named in the firm's
    words (row_name), never by its canonical name; a part whose side is
    undetermined never takes a past case's LH / RH. A named "Other / Not
    Listed" part gets one row of its own (_other_rows), after the listed
    parts' rows and only when none of them is already that line. Parts in
    data["dismissed_parts"] get no rows. Kept rows first in their order,
    then the new List items, then S/Nett, numbered "1.0"... in that order.
    """
    data = dict(data)
    vehicle = data.get("vehicle_info") or {}
    make, model = vehicle.get("make"), vehicle.get("model")
    damaged = _damaged(data)
    damaged_names = {p["part"] for p in damaged}
    damaged_keys = {p["key"] for p in damaged}
    dismissed = {_part_key(p) for p in (data.get("dismissed_parts") or []) if p}

    # A row made for an unnamed "Other / Not Listed" (before other_part_name,
    # or before the surveyor named it) goes with the one named Other on the
    # list once the unnamed one is gone: naming it must not leave its priced
    # row stale and add a second, unpriced one (review, 2026-11-10).
    named_others = [p for p in damaged if p["other"]]
    adopt = named_others[0]["source"] if len(named_others) == 1 and OTHER_PART not in damaged_keys else None

    kept = []
    for row in ((data.get("annex_a") or {}).get("items") or []):
        if _machine_owned(row, damaged_names):
            continue
        row = dict(row)
        row["origin"] = origin_of(row, damaged_names)  # written back, so old rows are derived once
        row.setdefault("source_part", None)
        if adopt and row["source_part"] and _part_key(row["source_part"]) == OTHER_PART:
            row["source_part"] = adopt
        row["stale"] = bool(row["source_part"]) and _part_key(row["source_part"]) not in damaged_keys
        kept.append(row)

    # Kept rows follow tab-1 changes (_refresh_kept): a main row's condition
    # and remarks, and the sided wording once an inferred side is confirmed.
    by_key = {}
    for part in damaged:
        by_key.setdefault(part["key"], part)
    namers: dict = {}
    for row in kept:
        part = by_key.get(_part_key(row["source_part"])) if row["source_part"] and not row["stale"] else None
        if part is not None:
            if part["part"] not in namers:
                namers[part["part"]] = _sided_namer(db, part, vehicle)
            _refresh_kept(row, part, namers[part["part"]])

    new_rows: list[dict] = []
    # Per damaged part (by _part_key): two parts may share a wording ("Tail
    # lamp" for a left and a right lamp whose sides are both still
    # unconfirmed), and each keeps its own rows and prices. Two entries of
    # the damage list that name ONE part ("Rear bumper fascia" and "Rear
    # bumper side LH" are both Rear Bumper) share its rows: keyed by the
    # entry's own name, each pulled the whole past kit and every line was
    # priced twice (live check of 2026-11-09).
    seen: set[tuple] = set()
    # A past line no past case gave a side ("Taillamps assy" x2) stands for
    # its family: held once, by whichever damaged side's row has it.
    family_held = {(_held_family(_family(r["source_part"])), line_key(r.get("part_name", "")))
                   for r in kept if r.get("source_part") and not r["stale"]}
    # Named unlisted parts last, so one that a listed part's past lines
    # already hold (a "Rear air vent" in the Civic's bumper kit) isn't
    # printed twice.
    for part in sorted(damaged, key=lambda p: p["other"] is not None):
        key = part["key"]
        if key in dismissed:
            continue
        # The kept rows that may stand for this part's lines: its own, and
        # hand lines (no source_part). Another part's rows never do -- the
        # other lamp's "Tail lamp" is not this one's.
        own = [r for r in kept if not r.get("source_part") or _part_key(r["source_part"]) == key]
        own_names = [r.get("part_name", "") for r in own]
        own_keys = {line_key(n) for n in own_names}
        # A kept row already made for this part -- typed, priced, or a case
        # filed before 2026-11-06 whose row says "Boot Lid" where the
        # firm's wording now says "Tail gate assy" -- stands for it: no
        # second, unpriced row (review, 2026-11-08).
        has_own_row = any(r.get("source_part") and _part_key(r["source_part"]) == key for r in kept)
        if part["other"]:
            if not has_own_row:
                every_line = {line_key(r.get("part_name", "")) for r in kept + new_rows}
                for row in _other_rows(db if price_book else None, part, vehicle, price_book):
                    if not _covered(row["part_name"], every_line):
                        new_rows.append(row)
            continue
        # An unconfirmed inferred side is looked up without its side: the
        # family's lines with no LH / RH, at its end of the car (suggest's
        # unsided). The rows' source_part stays part["part"], the sided name
        # (side_unconfirmed).
        name = canonical_part(part["part"])
        side = None if part["unsided"] else side_of(part["part"])
        # Kept lines that stand for this part's side (or no side): suggest()
        # strips LH/RH when it leaves out lines already in the table, so a
        # kept "Tail lamp LH" must not hide the lines for Right Tail Lamp.
        existing = [n for n in own_names if side_of(n) in (None, side)]
        found = None
        if price_book and name and name != "Other / Not Listed":
            # Per part, so two sides of one family ("Left Tail Lamp" and
            # "Right Tail Lamp") each get their own lines; lines already
            # typed into this Annex A are not repeated.
            suggestion = suggest(db, make=make, model=model, damaged_parts=[name], existing_items=existing,
                                 unsided=part["unsided"])
            found = next((g for g in suggestion["groups"] if g["reason"] == "damaged" and g["lines"]), None)
            # Another make's prices are no estimate for this car: with no
            # same-make history the part stays unpriced (the suggestions
            # panel still offers those lines, labelled, for a person to pick).
            if found and suggestion.get("scope") == "all imported cases":
                found = None
        if not found:
            # No past case covers it (or its lines are all typed already):
            # one unpriced row, worded as the firm would (row_name).
            row_part_name = row_name(db, part["part"], vehicle, unsided=part["unsided"])
            covered = has_own_row or _covered(row_part_name, own_keys) or _covered(part["part"], own_keys)
            if not covered and (key, row_part_name) not in seen:
                seen.add((key, row_part_name))
                new_rows.append(_unpriced_row(part, row_part_name))
            continue
        # _rows_for gives one row per past line, in order.
        floor = row_name(None, part["part"], vehicle, unsided=part["unsided"])
        for ln, row in zip(found["lines"], _rows_for(part, suggestion, found, floor)):
            if _covered(row["part_name"], own_keys):
                continue
            if ln.get("sided"):
                # A past case wrote it for one side: this side's own row (a
                # left and a right lamp each get "Tail lamp LH" / "RH").
                owner = (key, row["part_name"])
            else:
                # No past case gave it a side: the past case's both-lamps
                # "Taillamps assy" x2 printed once per lamp priced four
                # lamps (live check, 2026-11-09). Once per family -- also
                # when another side's kept row already has it.
                fam_line = (_held_family(ln.get("family") or _family(part["part"])), line_key(row["part_name"]))
                if fam_line in family_held:
                    continue
                owner = ("family",) + fam_line
            if owner in seen:
                continue
            seen.add(owner)
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


def unpriced_rows(data: dict) -> int:
    """Annex A lines still without a workshop estimate, counted as the
    Annex A tab counts "N lines unpriced" (a line with a description and
    no estimate), stale lines left out: their part is no longer damaged,
    so they wait on Keep or Remove, not on a price. From the rows as
    stored (no sync_annex_a): GET /reports counts every case with it, so
    a value that isn't a number counts as no estimate rather than failing
    the whole list."""
    annex = data.get("annex_a")
    items = annex.get("items") if isinstance(annex, dict) else None

    def unpriced(row) -> bool:
        if not isinstance(row, dict) or row.get("stale") or not str(row.get("part_name") or "").strip():
            return False
        try:
            return float(row.get("workshop_est") or 0) <= 0
        except (TypeError, ValueError):
            return True

    return sum(1 for row in items or [] if unpriced(row))
