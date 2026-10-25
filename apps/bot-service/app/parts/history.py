"""Repair kits learned from the firm's own past reports.

A finished case's Annex A says which lines a surveyor wrote for each damaged
part -- a Honda Vezel tailgate job is the tailgate itself plus hinges,
stopper, lock, striker, weatherstrip, emblem, inner trim board and its clips,
and the rear windscreen moulding, seal and clips that have to come off with
it -- and what each line actually cost after adjustment. Imported from the
CL-xxxxx spreadsheets (Cover Page + Annex A), that becomes:

  - suggested Annex A lines for a new case, by the parts it has damaged, for
    the same make and model first;
  - a "usual price" for each line, from what past cases were adjusted to --
    a Singapore price source that needs no web search or scraping.

Nothing here is a finding: every suggestion says how many past cases it
comes from, and the surveyor ticks what applies.
"""
import hashlib
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.reports.models import PastAnnexLine

# Conditions that mean "renewed on refit" or "kept", not "damaged".
FITTING_CONDITIONS = {"necessary", "serviceable"}

# Makes that take two words, so "Mercedes Benz C200" isn't make "Mercedes".
_TWO_WORD_MAKES = {"mercedes benz", "land rover", "alfa romeo", "aston martin", "rolls royce"}

_ORDINAL = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3})[a-z]*\.?,?\s+(\d{4})")
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# ---------------------------------------------------------------- parsing


@dataclass
class PastLine:
    line_no: int
    section: str  # "list" (discounted) or "nett"
    item: str
    condition: str
    remarks: str
    qty: float
    qty_raw: str
    workshop_est: float
    discount: float | None
    adjusted_cost: float


@dataclass
class PastCase:
    ref: str
    plate: str
    make: str
    model: str
    make_model_raw: str
    registered: str | None
    surveyed: str | None
    lines: list[PastLine] = field(default_factory=list)


def _date(text: str) -> str | None:
    m = _ORDINAL.search(text or "")
    if not m or m.group(2).lower() not in _MONTHS:
        return None
    try:
        return datetime(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))).date().isoformat()
    except ValueError:
        return None


def split_make_model(text: str) -> tuple[str, str]:
    """"Honda Vezel 1.5X CVT" -> ("Honda", "Vezel")."""
    words = (text or "").replace("-", " ").split()
    if len(words) >= 2 and " ".join(words[:2]).lower() in _TWO_WORD_MAKES:
        return " ".join(words[:2]).title(), (words[2] if len(words) > 2 else "")
    return (words[0].title() if words else ""), (words[1] if len(words) > 1 else "")


def _number(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(m.group(0)) if m else 0.0


def _rows(path: Path) -> dict[str, list[list]]:
    """Sheet name -> rows of cell values, for .xls and .xlsx alike."""
    if path.suffix.lower() == ".xls":
        import xlrd

        book = xlrd.open_workbook(str(path))
        return {
            s.name: [[s.cell_value(r, c) for c in range(s.ncols)] for r in range(s.nrows)]
            for s in book.sheets()
        }
    import io

    import openpyxl

    # From memory, not the path: a read-only workbook keeps its file open
    # until closed, and Windows then can't delete the uploaded temp copy.
    book = openpyxl.load_workbook(io.BytesIO(path.read_bytes()), read_only=True, data_only=True)
    try:
        return {ws.title: [[("" if v is None else v) for v in row] for row in ws.iter_rows(values_only=True)] for ws in book.worksheets}
    finally:
        book.close()


def _sheet(sheets: dict, wanted: str) -> list[list]:
    for name, rows in sheets.items():
        if name.strip().lower() == wanted:
            return rows
    raise ValueError(f"no '{wanted}' sheet")


def parse_report(path: Path) -> PastCase:
    """Reads one past report spreadsheet. Raises ValueError when it isn't
    one (no Annex A sheet, or no parts header)."""
    sheets = _rows(Path(path))
    cover = _sheet(sheets, "cover page")
    fields: dict[str, str] = {}
    ref = ""
    for row in cover:
        cells = [str(c).strip() for c in row]
        text = " ".join(c for c in cells if c)
        if not ref and (m := re.search(r"Ref\s*:\s*(CL\s*\d+)", text, re.I)):
            ref = re.sub(r"\s+", " ", m.group(1).upper())
        values = [c for c in cells if c]
        if len(values) >= 3 and values[1] == ":":
            fields.setdefault(values[0].lower(), values[2])
    make_model = fields.get("make & model", "")
    make, model = split_make_model(make_model)
    case = PastCase(
        ref=ref,
        plate=re.sub(r"\s+", "", fields.get("registration no.", "")).upper(),
        make=make,
        model=model,
        make_model_raw=make_model,
        registered=_date(fields.get("date of registration", "")),
        surveyed=_date(fields.get("date of survey", "")),
    )

    rows = _sheet(sheets, "annex a")
    header_at, cols = None, {}
    for i, row in enumerate(rows):
        labels = [re.sub(r"\s+", " ", str(c)).strip().lower() for c in row]
        if any("description" in lab for lab in labels):
            header_at = i
            for j, lab in enumerate(labels):
                if "description" in lab:
                    cols["item"] = j
                elif lab.startswith("condition"):
                    cols["condition"] = j
                elif lab.startswith("remark"):
                    cols["remarks"] = j
                elif lab.startswith("qty"):
                    cols["qty"] = j
                elif "est" in lab:
                    cols["est"] = j
                elif lab.startswith("disc"):
                    cols["disc"] = j
                elif "adjusted" in lab:
                    cols["adjusted"] = j
            break
    if header_at is None or "item" not in cols:
        raise ValueError("no Annex A parts header")

    section = "list"
    get = lambda row, key: row[cols[key]] if key in cols and cols[key] < len(row) else ""  # noqa: E731
    for row in rows[header_at + 1:]:
        item = str(get(row, "item")).strip()
        lowered = item.lower()
        if "nett" in lowered and "item" in lowered:
            section = "nett"
            continue
        if lowered.startswith("total"):
            break
        number = row[cols["item"] - 1] if cols["item"] >= 1 else ""
        if not item or not re.fullmatch(r"\d+(\.0)?", str(number).strip()):
            continue
        disc = get(row, "disc")
        qty_raw = str(get(row, "qty")).strip()
        case.lines.append(PastLine(
            line_no=int(float(number)),
            section=section,
            item=item,
            condition=str(get(row, "condition")).strip(),
            remarks=str(get(row, "remarks")).strip(),
            qty=_number(qty_raw) or 1.0,
            qty_raw=qty_raw,
            workshop_est=_number(get(row, "est")),
            discount=float(disc) if isinstance(disc, (int, float)) else None,
            adjusted_cost=_number(get(row, "adjusted")),
        ))
    if not case.ref:
        case.ref = Path(path).stem
    return case


# ------------------------------------------------------- line -> part


def side_of(text: str) -> str | None:
    t = f" {text.lower()} "
    if re.search(r"\blh\b|\bleft\b|\bnearside\b", t):
        return "Left"
    if re.search(r"\brh\b|\bright\b|\boffside\b", t):
        return "Right"
    return None


# First match wins, so the specific rules come before the general ones.
# Families are canonical part names without the side (taxonomy.py).
_FAMILY_RULES: list[tuple[str, str]] = [
    (r"(tail ?gate|boot ?lid|back ?door).*(lock|striker|catch)|key lock", "Boot Lid Lock"),
    (r"(tail ?gate|boot ?lid|back ?door).*hinge", "Boot Lid Hinge"),
    (r"(tail ?gate|boot ?lid|back ?door).*weather ?stri", "Boot Lid Weatherstrip"),
    (r"(tail ?gate|boot ?lid|back ?door).*(inner trim|insulator|trim board)", "Boot Lid Inner Trim"),
    (r"(tail ?gate|boot ?lid|back ?door).*(reflector|lamp)", "Boot Lid Lamp"),
    (r"tail ?gate|boot ?lid|back ?door", "Boot Lid"),
    (r"rear windscreen|rear screen|back glass", "Rear Windscreen"),
    (r"front windscreen|windscreen", "Front Windscreen"),
    (r"rear bumper.*(beam|reinforcement)", "Rear Bumper Beam"),
    (r"rear bumper.*reflector", "Rear Bumper Reflector"),
    (r"(rear bumper|rear fender).*(wheel arch|arch garnish|arch protector|mudflap)", "Rear Wheel Arch Liner"),
    (r"rear bumper", "Rear Bumper"),
    (r"tail ?lamp", "Tail Lamp"),
    (r"(rear )?end panel|taillamp panel", "Rear End Panel"),
    (r"spare tyre panel top|floor panel (board|insulator|under|tool)|tool tray|spare tyre panel top", "Boot Floor Board"),
    (r"spare tyre panel|rear floor panel", "Rear Floor Panel"),
    (r"rear fender inner trim|boot side trim|quarter inner trim", "Boot Side Trim"),
    (r"rear fender|quarter panel|top side joint", "Rear Quarter Panel"),
    (r"front bumper.*(reinforcement|beam)", "Front Bumper Beam"),
    (r"(front bumper|engine) under ?cover", "Engine Undercover"),
    (r"fog lamp", "Fog Lamp"),
    (r"front (bumper )?number plate|front number plate", "Front Number Plate"),
    (r"front bumper", "Front Bumper"),
    (r"grille", "Front Grille"),
    (r"head ?lamp", "Headlamp"),
    (r"front fender.*(splash|wheel arch|arch protector|liner)", "Front Wheel Arch Liner"),
    (r"front fender|wing panel", "Front Fender"),
    (r"front door", "Front Door"),
    (r"rear door", "Rear Door"),
    (r"(wing|side) mirror", "Wing Mirror"),
    (r"reverse sensor|parking sensor", "Parking Sensor"),
    (r"reverse camera|reversing camera", "Reversing Camera"),
    (r"number plate", "Rear Number Plate"),
    (r"exhaust|silencer", "Exhaust System"),
    (r"cross member|subframe", "Front Subframe"),
    (r"lower arm|anti roll|stabili[sz]er|shock absorber|strut", "Suspension - Front"),
    (r"steering", "Steering Assembly"),
    (r"chassis|frame member", "Underbody / Chassis"),
    (r"rim|tyre|wheel", "Wheel"),
    (r"bonnet|hood", "Bonnet"),
    (r"roof", "Roof"),
]

# Families whose canonical names carry a side (taxonomy.CANONICAL_PARTS).
_SIDED = {
    "Tail Lamp", "Headlamp", "Fog Lamp", "Rear Bumper Reflector", "Boot Lid Lamp", "Boot Side Trim",
    "Rear Wheel Arch Liner", "Front Wheel Arch Liner", "Rear Quarter Panel", "Front Fender",
    "Front Door", "Rear Door", "Wing Mirror",
}


def family_of(item: str) -> str | None:
    text = item.lower()
    for pattern, family in _FAMILY_RULES:
        if re.search(pattern, text):
            return family
    return None


def canonical_part(text: str) -> str | None:
    """A damaged part as a canonical name: as is when it already is one,
    otherwise read with the same rules as past-report lines -- cases filed
    before the taxonomy hold free text like "Tail gate lock striker" or
    "Tail lamp LH"."""
    from app.reports.taxonomy import CANONICAL_PARTS

    if text in CANONICAL_PARTS:
        return text
    family = family_of(text)
    if not family:
        return None
    side = side_of(text)
    return f"{side} {family}" if side and family in _SIDED else family


def family_and_side(part: str) -> tuple[str, str | None]:
    """Canonical part name -> (family, side): "Left Tail Lamp" ->
    ("Tail Lamp", "Left"); "Wheel (side undetermined)" -> ("Wheel", None)."""
    part = part.replace(" (side undetermined)", "")
    for side in ("Left", "Right"):
        if part.startswith(side + " "):
            rest = part[len(side) + 1:]
            # "Left Front Fender" -> family "Front Fender"; "Left Rear Wheel" -> "Wheel".
            if rest in ("Front Wheel", "Rear Wheel"):
                return "Wheel", side
            if rest == "Rear Quarter Panel":
                return "Rear Quarter Panel", side
            return rest, side
    return part, None


def normalise_item(item: str) -> str:
    """Wording-level key: "Tail lamp LH" and "Tail Lamp L.H." are one line."""
    t = item.lower()
    t = re.sub(r"\([^)]*\)|\bx\s*\d+\b", " ", t)
    # The panel itself goes by several names: "Tail gate", "Tail gate assy",
    # "Rear bumper fascia", "Rear bumper assy".
    t = re.sub(r"\b(assy|assembly|complete|fascia)\b|\bc/w\b.*$", " ", t)
    t = re.sub(r"\b(lh|rh|left|right|l\.h\.|r\.h\.|nearside|offside)\b", " ", t)
    t = t.replace("tail gate", "tailgate").replace("taillamp", "tail lamp").replace("weatherstripe", "weatherstrip")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _with_side(item: str, side: str | None) -> str:
    """Puts the case's side on a line learnt from the other side."""
    if not side:
        return item
    code = "LH" if side == "Left" else "RH"
    other = "RH" if side == "Left" else "LH"
    return re.sub(rf"\b{other}\b", code, item)


# ------------------------------------------------------------- import


def import_case(db: Session, case: PastCase, source_name: str) -> int:
    """Replaces any earlier import of the same report (by its CL reference)
    with this one. Returns the number of lines stored."""
    db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == case.ref).delete()
    for line in case.lines:
        db.add(PastAnnexLine(
            case_ref=case.ref,
            source_file=source_name[:200],
            plate=case.plate,
            make=case.make,
            model=case.model,
            make_model_raw=case.make_model_raw,
            registered=case.registered,
            surveyed=case.surveyed,
            line_no=line.line_no,
            section=line.section,
            item=line.item,
            item_key=normalise_item(line.item),
            family=family_of(line.item),
            side=side_of(line.item),
            condition=line.condition,
            qty=line.qty,
            workshop_est=line.workshop_est,
            discount=line.discount,
            adjusted_cost=line.adjusted_cost,
        ))
    return len(case.lines)


def file_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --------------------------------------------------------- suggestions


def _same(a: str | None, b: str | None) -> bool:
    return bool(a and b) and a.strip().lower() == b.strip().lower()


def _same_model(a: str | None, b: str | None) -> bool:
    """By the model name's first word: a case can say "Vezel 1.5X CVT"
    where the history says "Vezel"."""
    first = lambda s: (s or "").strip().split()[0].lower() if (s or "").strip() else ""  # noqa: E731
    return bool(first(a)) and first(a) == first(b)


def _unit_price(line: PastAnnexLine) -> float | None:
    """What one unit was adjusted to. None for a line not paid for -- kept
    (Serviceable, 100% disallowed) or never priced."""
    if not line.adjusted_cost or line.adjusted_cost <= 0:
        return None
    return round(line.adjusted_cost / (line.qty or 1.0), 2)


def suggest(
    db: Session,
    *,
    make: str | None,
    model: str | None,
    damaged_parts: list[str],
    existing_items: list[str],
    exclude_ref: str | None = None,
) -> dict:
    """Annex A lines past cases wrote for these damaged parts.

    Scope: the same make and model when there is history for it, else the
    same make, else every imported case -- said in the answer. Per damaged
    part, the lines its family had in past cases; plus, under "often
    replaced with", families that appeared in most past cases where that
    part was damaged but aren't damaged here (the rear windscreen trims on a
    tailgate job). Lines already in this case's Annex A are left out.
    """
    lines = db.query(PastAnnexLine).all()
    if exclude_ref:
        lines = [ln for ln in lines if ln.case_ref != exclude_ref]
    scope_label, pool = "all imported cases", lines
    by_model = [ln for ln in lines if _same(ln.make, make) and _same_model(ln.model, model)]
    by_make = [ln for ln in lines if _same(ln.make, make)]
    if by_model:
        scope_label, pool = f"past {make} {by_model[0].model} cases", by_model
    elif by_make:
        scope_label, pool = f"past {make} cases", by_make

    cases = sorted({ln.case_ref for ln in pool})
    have = {normalise_item(i) for i in existing_items}
    wanted: dict[str, str | None] = {}
    for part in damaged_parts:
        fam, side = family_and_side(part)
        wanted.setdefault(fam, side)

    # Which families each past case had damaged (not just refitted).
    damaged_by_case: dict[str, set[str]] = {}
    for ln in pool:
        if ln.family and (ln.condition or "").strip().lower() not in FITTING_CONDITIONS:
            damaged_by_case.setdefault(ln.case_ref, set()).add(ln.family)

    def lines_for(family: str, side: str | None, case_refs: set[str] | None = None) -> list[dict]:
        groups: dict[str, list[PastAnnexLine]] = {}
        for ln in pool:
            if ln.family != family or (case_refs is not None and ln.case_ref not in case_refs):
                continue
            groups.setdefault(ln.item_key, []).append(ln)
        out = []
        for key, group in groups.items():
            if key in have:
                continue
            seen_in = sorted({g.case_ref for g in group})
            prices = [p for p in (_unit_price(g) for g in group) if p is not None]
            qtys = [g.qty for g in group if g.qty]
            names = [g.item for g in group]
            name = max(set(names), key=names.count)
            conditions = sorted({g.condition for g in group if g.condition})
            fitting = all((g.condition or "").strip().lower() in FITTING_CONDITIONS for g in group)
            out.append({
                "name": _with_side(name, side if family in _SIDED else None),
                "family": family,
                "qty": statistics.median(qtys) if qtys else 1.0,
                "usual_unit_price": round(statistics.median(prices), 2) if prices else None,
                "price_from_cases": len({g.case_ref for g in group if _unit_price(g) is not None}),
                "price_range": [min(prices), max(prices)] if len(prices) > 1 else None,
                "cases": len(seen_in),
                "nett": all(g.section == "nett" for g in group),
                "conditions": conditions,
                "kind": "fitting" if fitting else "part",
                "last_seen": max((g.surveyed or "") for g in group) or None,
            })
        out.sort(key=lambda s: (-s["cases"], s["kind"] == "fitting", s["name"]))
        return out

    groups, without_history = [], []
    for family, side in wanted.items():
        found = lines_for(family, side)
        if found:
            groups.append({"part": family if not side else f"{side} {family}", "reason": "damaged", "lines": found})
        elif not any(ln.family == family for ln in pool):
            # Only parts no past case covers. A part whose past lines are all
            # already in this Annex A is simply done -- calling it "no
            # history" sent surveyors to ask the AI for nothing.
            without_history.append(family if not side else f"{side} {family}")

    # Companions: families present in at least half (and at least 2) of the
    # past cases that had this family damaged.
    for family in list(wanted):
        with_it = {ref for ref, fams in damaged_by_case.items() if family in fams}
        if len(with_it) < 2:
            continue
        counts: dict[str, int] = {}
        for ref in with_it:
            for fam in {ln.family for ln in pool if ln.case_ref == ref and ln.family}:
                counts[fam] = counts.get(fam, 0) + 1
        for fam, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            if fam in wanted or n < 2 or n * 2 < len(with_it):
                continue
            found = lines_for(fam, None, with_it)
            if found:
                groups.append({"part": fam, "reason": f"often replaced with {family} ({n} of {len(with_it)} cases)", "lines": found})
                wanted[fam] = None  # list each companion once

    return {"scope": scope_label, "cases_used": len(cases), "groups": groups, "without_history": without_history}
