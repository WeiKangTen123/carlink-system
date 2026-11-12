"""Repair kits learned from the firm's own past reports.

A finished case's Annex A says which lines a surveyor wrote for each damaged
part -- a Honda Vezel tailgate job is the tailgate itself plus hinges,
stopper, lock, striker, weatherstrip, emblem, inner trim board and its clips,
and the rear windscreen moulding, seal and clips that have to come off with
it -- and what each line actually cost after adjustment. Its Annex B says
which labour and spray-painting lines the surveyor wrote, and what each was
adjusted to. Imported from the CL-xxxxx spreadsheets (Cover Page + Annex A +
Annex B), that becomes:

  - suggested Annex A lines for a new case, by the parts it has damaged, for
    the same make and model first (suggest), and the firm's usual labour
    lines (suggest_labour);
  - a "usual price" for each line, from what past cases were adjusted to --
    a Singapore price source that needs no web search or scraping.

Nothing here is a finding: every suggestion says how many past cases it
comes from, and the surveyor ticks what applies (app/parts/prefill.py writes
priced Annex A rows from it, tagged prefilled, for the surveyor to check).
"""
import hashlib
import re
import statistics
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.reports.models import PastAnnexLine, PastLabourLine, Report

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
class PastLabour:
    """One Annex B line: the job as worded, the workshop's figure and the
    adjusted one. No qty or discount -- the firm's labour sheet has none."""
    line_no: int
    description: str
    workshop_est: float
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
    labour: list[PastLabour] = field(default_factory=list)


def _date(text: str) -> str | None:
    m = _ORDINAL.search(text or "")
    if not m or m.group(2).lower() not in _MONTHS:
        return None
    try:
        return datetime(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))).date().isoformat()
    except ValueError:
        return None


def split_make_model(text: str) -> tuple[str, str]:
    """"Honda Vezel 1.5X CVT" -> ("Honda", "Vezel"); "Honda HR-V 1.5" ->
    ("Honda", "HR-V"); "Mercedes-Benz C200" -> ("Mercedes Benz", "C200").
    Only a make's hyphen splits: the model's used to as well, so HR-V was
    stored as "HR" and CX-3 and CX-5 were both "CX"."""
    # Punctuation-only words ("Honda - Vezel") are no model (review,
    # 2026-11-10); a make and model run together by a hyphen with nothing
    # after it ("HONDA-VEZEL") are split.
    words = [w for w in (text or "").split() if re.search(r"[0-9A-Za-z]", w)]
    if len(words) == 1 and "-" in words[0].strip("-"):
        words = words[0].split("-", 1)
    if words and "-" in words[0]:
        head = words[0].replace("-", " ").split()
        if " ".join(head[:2]).lower() in _TWO_WORD_MAKES:
            words = head + words[1:]
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


def _label(cell) -> str:
    return re.sub(r"\s+", " ", str(cell)).strip().lower()


def _is_line_number(value) -> bool:
    return bool(re.fullmatch(r"\d+(\.0)?", str(value).strip()))


def _labour_lines(rows: list[list]) -> list[PastLabour]:
    """The Annex B sheet's job lines: the rows between the `Item | Job
    Description | Workshop Est. ($) | Adjusted Cost ($)` header and the
    `TOTAL FOR LABOUR` row. The sheets wrap a long description by padding
    it with runs of spaces, so whitespace is collapsed."""
    header_at, cols = None, {}
    for i, row in enumerate(rows):
        labels = [_label(c) for c in row]
        if any("job description" in lab for lab in labels):
            header_at = i
            for j, lab in enumerate(labels):
                if "job description" in lab:
                    cols["desc"] = j
                elif lab == "item":
                    cols["item"] = j
                elif "est" in lab:
                    cols["est"] = j
                elif "adjusted" in lab:
                    cols["adjusted"] = j
            break
    if header_at is None:
        return []
    number_col = cols.get("item", cols["desc"] - 1)
    get = lambda row, key: row[cols[key]] if key in cols and cols[key] < len(row) else ""  # noqa: E731
    out = []
    for row in rows[header_at + 1:]:
        text = " ".join(_label(c) for c in row if str(c).strip())
        if text.startswith("total"):
            break
        description = re.sub(r"\s+", " ", str(get(row, "desc"))).strip()
        number = row[number_col] if 0 <= number_col < len(row) else ""
        if not description or not _is_line_number(number):
            continue
        out.append(PastLabour(
            line_no=int(float(number)),
            description=description,
            workshop_est=_number(get(row, "est")),
            adjusted_cost=_number(get(row, "adjusted")),
        ))
    return out


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
    # Annex B is optional: a report without one still imports its parts.
    try:
        case.labour = _labour_lines(_sheet(sheets, "annex b"))
    except ValueError:
        case.labour = []
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


# The outside of a wheel arch: its garnish (the Vezel's "wheel arch
# protector ganrish", sic), moulding or flare, and the mudflap.
_ARCH_TRIM = r"\barch\b.*\b(garnish|ganrish|moulding|molding|trim|flare|cladding)|mud ?flap"

# First match wins, so the specific rules come before the general ones.
# Families are canonical part names without the side (taxonomy.py); one
# ("Wheel Nut") has no canonical part and only comes up as a past line.
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
    # The arch's outside garnish (and the mudflap behind it) before the
    # liner inside it: one family held both, so a "wheel arch garnish" was
    # priced and worded as the splash shield.
    (rf"front\b.*({_ARCH_TRIM})", "Front Wheel Arch Trim"),
    (rf"(rear|quarter)\b.*({_ARCH_TRIM})", "Rear Wheel Arch Trim"),
    (_ARCH_TRIM, "Wheel Arch Trim"),
    (r"(rear bumper|rear fender).*(wheel arch|arch protector)", "Rear Wheel Arch Liner"),
    (r"rear bumper", "Rear Bumper"),
    # The pressure-relief vent behind the rear bumper (the Civic's "Rear air
    # vent"): renewed with the bumper, and it had no family at all.
    (r"rear\b.*\bair (vent|outlet|extractor)", "Rear Bumper"),
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
    # Inside the door, priced apart from it ("Front door glass regulator
    # mechanism", "... motor"): it used to be folded into the door.
    (r"front\b.*\b(door|window|glass)\b.*\bregulator", "Front Window Regulator"),
    (r"rear\b.*\b(door|window|glass)\b.*\bregulator", "Rear Window Regulator"),
    (r"(door|window|glass)\b.*\bregulator", "Window Regulator"),
    (r"front door", "Front Door"),
    (r"rear door", "Rear Door"),
    (r"side sill|sill panel|rocker panel", "Sill / Rocker Panel"),
    (r"(wing|side) mirror", "Wing Mirror"),
    (r"reverse sensor|parking sensor", "Parking Sensor"),
    (r"reverse camera|reversing camera", "Reversing Camera"),
    (r"number plate", "Rear Number Plate"),
    (r"exhaust|silencer", "Exhaust System"),
    (r"rear\b.*\b(cross member|subframe)", "Rear Subframe"),
    (r"cross member|subframe", "Front Subframe"),
    (r"rear\b.*\b(lower arm|trailing arm|anti roll|stabili[sz]er|shock absorber|strut|axle beam|torsion beam)", "Suspension - Rear"),
    (r"lower arm|anti roll|stabili[sz]er|shock absorber|strut", "Suspension - Front"),
    (r"steering", "Steering Assembly"),
    (r"chassis|frame member", "Underbody / Chassis"),
    (r"front\b.*\b(wheel arch|arch liner|arch protector|splash shield)", "Front Wheel Arch Liner"),
    (r"rear\b.*\b(wheel arch|arch liner|arch protector|splash shield)", "Rear Wheel Arch Liner"),
    (r"wheel arch|arch liner|arch protector|splash shield", "Wheel Arch Liner"),
    # "Wheel" held the rims, the tyres and the spare wheel's nut, so a rim's
    # kit priced the tyre and the nut too, and its main row could be either.
    # Now the rim, the tyre and the nut each have their own family, and a
    # rim or tyre that names its end is that end's: "Front tyre" is no rear
    # tyre's line. The spare wheel's hold-down nut sits in the boot floor.
    (r"spare (tyre|wheel)\b.*\b(nut|bolt|clamp)", "Boot Floor Board"),
    (r"wheel (nut|bolt)s?|lug nut", "Wheel Nut"),
    (r"front\b.*\b(tyre|tire)s?\b", "Front Tyre"),
    (r"rear\b.*\b(tyre|tire)s?\b", "Rear Tyre"),
    (r"\b(tyre|tire)s?\b", "Tyre"),
    (r"front\b.*\b(rim|wheel)", "Front Wheel"),
    (r"rear\b.*\b(rim|wheel)", "Rear Wheel"),
    (r"\brims?\b|wheel", "Wheel"),
    (r"bonnet|hood", "Bonnet"),
    (r"roof", "Roof"),
]

# Families whose canonical names carry a side (taxonomy.CANONICAL_PARTS).
_SIDED = {
    "Tail Lamp", "Headlamp", "Fog Lamp", "Rear Bumper Reflector", "Boot Lid Lamp", "Boot Side Trim",
    "Rear Wheel Arch Liner", "Front Wheel Arch Liner", "Rear Wheel Arch Trim", "Front Wheel Arch Trim",
    "Rear Quarter Panel", "Front Fender", "Front Door", "Rear Door", "Wing Mirror",
    "Front Window Regulator", "Rear Window Regulator", "Sill / Rocker Panel",
    "Front Wheel", "Rear Wheel", "Front Tyre", "Rear Tyre",
}

# A rim or tyre line that names no end ("Sport rim" x2) stands in for
# either end's family when no past line in scope names that end: the
# Civic's only rim line is "Sport rim".
_END_FALLBACK = {"Front Wheel": "Wheel", "Rear Wheel": "Wheel", "Front Tyre": "Tyre", "Rear Tyre": "Tyre"}
# For prefill, which holds an end-less line once whichever wheel drew it.
END_FALLBACK = _END_FALLBACK


@lru_cache(maxsize=4096)
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


def _line_family(line: PastAnnexLine) -> str | None:
    """A past line's family by today's rules, from its wording. Not the
    family column stored at import: the rules change (a regulator was the
    door's until 2026-11-10), and the stored value would keep every past
    import on the old ones until it was imported again. refresh_lines
    brings the column up to date for what shows it (the price book list)."""
    return family_of(line.item or "")


def refresh_lines(db: Session) -> int:
    """Re-reads every imported line's family, side and model by today's
    rules (family_of, side_of, split_make_model), for a start-up step after
    a rule change; labour lines take their case's model. Returns how many
    lines changed; the caller commits."""
    changed = 0
    models: dict[str, str] = {}
    for ln in db.query(PastAnnexLine).all():
        model = split_make_model(ln.make_model_raw)[1] if ln.make_model_raw else ln.model
        models.setdefault(ln.case_ref, model)
        new = (family_of(ln.item or ""), side_of(ln.item or ""), model)
        if (ln.family, ln.side, ln.model) != new:
            ln.family, ln.side, ln.model = new
            changed += 1
    for job in db.query(PastLabourLine).all():
        model = models.get(job.case_ref)
        if model and job.model != model:
            job.model = model
            changed += 1
    return changed


def family_and_side(part: str) -> tuple[str, str | None]:
    """Canonical part name -> (family, side): "Left Tail Lamp" ->
    ("Tail Lamp", "Left"); "Left Rear Wheel" -> ("Rear Wheel", "Left");
    "Wheel (side undetermined)" -> ("Wheel", None), the rim lines that
    name no end."""
    part = part.replace(" (side undetermined)", "")
    for side in ("Left", "Right"):
        if part.startswith(side + " "):
            return part[len(side) + 1:], side
    # The only quarter panel is the rear one; "Quarter Panel" found no
    # history under that name.
    if part == "Quarter Panel":
        return "Rear Quarter Panel", None
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


def normalise_labour(description: str) -> str:
    """Wording-level key for an Annex B line: lowercase, without a leading
    "To ", punctuation or double spaces, so "To remove, reinstall rear
    windscreen (to FR)" and "to remove reinstall rear windscreen" are one
    line."""
    t = re.sub(r"\s+", " ", (description or "").lower()).strip()
    t = re.sub(r"^to\s+", "", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _qty_text(qty_raw: str) -> str | None:
    """"1set" as the sheet had it; None for a plain number ("2", "1.0")."""
    text = (qty_raw or "").strip()
    if not text or re.fullmatch(r"\d+(\.\d+)?", text):
        return None
    return text[:20]


def _with_side(item: str, side: str | None) -> str:
    """Puts the case's side on a line learnt from the other side."""
    if not side:
        return item
    code = "LH" if side == "Left" else "RH"
    other = "RH" if side == "Left" else "LH"
    return re.sub(rf"\b{other}\b", code, item)


# LH, RH, L.H., R.H, L/H, R/H, "(LH)", left, right, nearside, offside.
_SIDE_WORDS = re.compile(r"\s*\(?\s*\b(?:l\.?/?h|r\.?/?h|left|right|nearside|offside)\b\.?\s*\)?", re.I)


def _without_side(item: str) -> str:
    """A past line's wording with its side taken out, for a part whose side
    nobody determined: "Tail lamp LH" -> "Tail lamp", "Tail lamp (LH)" ->
    "Tail lamp". Printing the past case's LH there stated a side this case
    never established."""
    return re.sub(r"\s{2,}", " ", _SIDE_WORDS.sub(" ", item)).strip()


# ------------------------------------------------------------- import


def import_case(db: Session, case: PastCase, source_name: str) -> int:
    """Replaces any earlier import of the same report (by its CL reference)
    with this one: its Annex A lines and its Annex B labour lines (so a
    report imported before Annex B was read gains them on re-import).
    Returns the number of Annex A lines stored; len(case.labour) is the
    labour count."""
    db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == case.ref).delete()
    db.query(PastLabourLine).filter(PastLabourLine.case_ref == case.ref).delete()
    for job in case.labour:
        db.add(PastLabourLine(
            case_ref=case.ref,
            source_file=source_name[:200],
            plate=case.plate,
            make=case.make,
            model=case.model,
            surveyed=case.surveyed,
            line_no=job.line_no,
            description=job.description[:500],
            desc_key=normalise_labour(job.description),
            workshop_est=job.workshop_est,
            adjusted_cost=job.adjusted_cost,
        ))
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
            qty_text=_qty_text(line.qty_raw),
            workshop_est=line.workshop_est,
            discount=line.discount,
            adjusted_cost=line.adjusted_cost,
        ))
    return len(case.lines)


def file_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ------------------------------------------- which past lines may answer

# Contract 8 L: a case signed off in Carlink joins the history as the past
# case "carlink:<report id>" (app/parts/learn.py), next to the imported CL
# spreadsheets.
LEARNED_PREFIX = "carlink:"

# (case refs left out, only lines imported before this naive-UTC moment):
# set for one piece of work by restricted(). A ContextVar, so a background
# task's restriction never reaches a request on another thread.
_VIEW: ContextVar[tuple[frozenset, datetime | None]] = ContextVar("past_lines_view", default=(frozenset(), None))


@contextmanager
def restricted(exclude_refs=(), imported_before: datetime | None = None):
    """Within the block, suggest() and firm_wording() leave out the past
    cases in exclude_refs and every line imported at or after
    imported_before. For the AI accuracy record (app/reports/accuracy.py),
    which rebuilds the Annex A the machine would have written when the case
    was filed: never from the case's own lines, nor from lines that only
    came in later; and for a signed-off case asking for suggestions."""
    if imported_before is not None and imported_before.tzinfo is not None:
        imported_before = imported_before.astimezone(timezone.utc).replace(tzinfo=None)
    token = _VIEW.set((frozenset(exclude_refs), imported_before))
    try:
        yield
    finally:
        _VIEW.reset(token)


def _naive_utc(value: datetime | None) -> datetime | None:
    """SQLite hands DateTime columns back naive; a fresh row holds an aware one."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def past_lines(db: Session) -> list[PastAnnexLine]:
    """Every past Annex A line suggestions may use. A Carlink case's own
    lines (LEARNED_PREFIX) count only while that case is signed off, and
    only while learning is on (settings.learn_from_signed_off): a reopened
    or deleted case's lines are removed with it (learn.forget), and this
    makes sure a removal that failed, or a sign-off still being recorded
    when the case was reopened, can never let a case be priced from its own
    lines. A case whose spreadsheet was imported as well (its Our Ref is a
    CL reference here) counts once, as the spreadsheet."""
    lines = db.query(PastAnnexLine).all()
    excluded, before = _VIEW.get()
    if excluded or before is not None:
        lines = [
            ln for ln in lines
            if ln.case_ref not in excluded
            and (before is None or (ln.imported_at is not None and _naive_utc(ln.imported_at) < before))
        ]
    if not any(is_learned(ln.case_ref) for ln in lines):
        return lines
    if not settings.learn_from_signed_off:
        return [ln for ln in lines if not is_learned(ln.case_ref)]
    signed = {LEARNED_PREFIX + rid for (rid,) in db.query(Report.id).filter(Report.status == "Signed Off")}
    imported = {ln.case_ref for ln in lines if not is_learned(ln.case_ref)}
    return [
        ln for ln in lines
        if not is_learned(ln.case_ref)
        or (ln.case_ref in signed and not (ln.source_file and ln.source_file in imported))
    ]


def is_learned(case_ref: str | None) -> bool:
    """A past case that is a signed-off Carlink case (learn.py), not a
    spreadsheet."""
    return (case_ref or "").startswith(LEARNED_PREFIX)


# --------------------------------------------------------- suggestions


def _same(a: str | None, b: str | None) -> bool:
    return bool(a and b) and a.strip().lower() == b.strip().lower()


# One car sold under two names: the home-market name on a parallel import,
# the export name on a dealer car. A reader's "HR-V" found no Vezel history
# and the scope widened to every Honda.
_MODEL_TWINS = {
    "hrv": "vezel", "jazz": "fit", "grace": "city", "altis": "corolla",
    "mazda2": "demio", "mazda3": "axela", "mazda6": "atenza",
    "qashqai": "dualis", "elantra": "avante", "forte": "cerato", "k3": "cerato", "crosstrek": "xv",
}


def _model_key(model: str | None) -> str:
    """The model name's first word, letters and digits only ("HR-V" ->
    "hrv"), as its twin's name ("vezel")."""
    words = (model or "").strip().lower().split()
    first = re.sub(r"[^a-z0-9]", "", words[0]) if words else ""
    return _MODEL_TWINS.get(first, first)


def _same_model(a: str | None, b: str | None) -> bool:
    """By the model name's first word: a case can say "Vezel 1.5X CVT"
    where the history says "Vezel", or its twin "HR-V" (_MODEL_TWINS)."""
    key = _model_key(a)
    return bool(key) and key == _model_key(b)


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
    unsided: bool = False,
) -> dict:
    """Annex A lines past cases wrote for these damaged parts.

    Scope: the same make and model when there is history for it, else the
    same make, else every imported case -- said in the answer. Per damaged
    part, the lines its family had in past cases; plus, under "often
    replaced with", families that appeared in most past cases where that
    part was damaged but aren't damaged here (the rear windscreen trims on a
    tailgate job). Lines already in this case's Annex A are left out.
    unsided: nobody has established the parts' sides (prefill.
    side_unconfirmed), so no line keeps a LH / RH; the end stays, which a
    "(side undetermined)" name loses ("Left Rear Wheel" -> the rear rims).
    """
    lines = past_lines(db)
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
    # Families named "(side undetermined)": also the ones _SIDED leaves out
    # because their history family has no side (Wheel).
    undetermined: set[str] = set()
    for part in damaged_parts:
        fam, side = family_and_side(part)
        if unsided:
            side = None
        wanted.setdefault(fam, side)
        if unsided or part.endswith(" (side undetermined)"):
            undetermined.add(fam)

    # Which families each past case had damaged (not just refitted).
    fam_of = {id(ln): _line_family(ln) for ln in pool}
    damaged_by_case: dict[str, set[str]] = {}
    for ln in pool:
        if fam_of[id(ln)] and (ln.condition or "").strip().lower() not in FITTING_CONDITIONS:
            damaged_by_case.setdefault(ln.case_ref, set()).add(fam_of[id(ln)])

    def lines_for(family: str, side: str | None, case_refs: set[str] | None = None, unsided: bool = False) -> list[dict]:
        """unsided: the damaged part's side is undetermined, so no line
        keeps a past case's LH / RH."""
        groups: dict[str, list[PastAnnexLine]] = {}
        for ln in pool:
            if fam_of[id(ln)] != family or (case_refs is not None and ln.case_ref not in case_refs):
                continue
            groups.setdefault(ln.item_key, []).append(ln)
        out = []
        for key, group in groups.items():
            if key in have:
                continue
            seen_in = sorted({g.case_ref for g in group})
            prices = [p for p in (_unit_price(g) for g in group) if p is not None]
            # What the workshop asked per unit, on the lines that were paid:
            # the W/shop Est column of a prefilled row (prefill.py). The
            # adjusted figure used to go there and was discounted a second time.
            ests = [round(g.workshop_est / (g.qty or 1), 2) for g in group if _unit_price(g) is not None and g.workshop_est > 0]
            qtys = [g.qty for g in group if g.qty]
            names = [g.item for g in group]
            # Most common wording; sorted first so a tie is decided the same
            # way in every process (a set's order is not).
            name = max(sorted(set(names)), key=names.count)
            conditions = sorted({g.condition for g in group if g.condition})
            fitting = all((g.condition or "").strip().lower() in FITTING_CONDITIONS for g in group)
            # A count some past case wrote: the plain median of a both-tyres
            # line (2) and a one-tyre line (1) was 1.5 tyres, priced 1.5 x.
            qty = statistics.median_low(qtys) if qtys else 1.0
            # "1set" when at least half the past lines were written as a
            # set (prefill prints it that way); the unit goes on the median.
            units = [re.sub(r"[\d.\s]", "", g.qty_text or "") for g in group if g.qty_text]
            unit = max(sorted(set(units)), key=units.count) if units and len(units) * 2 >= len(group) else ""
            if unsided:
                name = _without_side(name)
            elif side and family in _SIDED:
                # This side's own line: "Front tyre RH" -> "Front tyre LH",
                # and "Front tyre" (the wording most cases wrote) gets the
                # side a past case gave it, as firm_wording does.
                name = _with_side(name, side)
                if side_of(name) is None and any(side_of(g.item) for g in group):
                    name = f"{name} {'LH' if side == 'Left' else 'RH'}"
            out.append({
                "name": name,
                "family": family,
                "qty": qty,
                "qty_text": f"{qty:g}{unit}" if unit else None,
                "usual_unit_price": round(statistics.median(prices), 2) if prices else None,
                "usual_unit_est": round(statistics.median(ests), 2) if ests else None,
                "price_from_cases": len({g.case_ref for g in group if _unit_price(g) is not None}),
                "price_range": [min(prices), max(prices)] if len(prices) > 1 else None,
                "cases": len(seen_in),
                "nett": all(g.section == "nett" for g in group),
                # Which section most past cases put it in (ties: list).
                "nett_majority": sum(g.section == "nett" for g in group) * 2 > len(group),
                "conditions": conditions,
                "kind": "fitting" if fitting else "part",
                # Whether a past case wrote it for one side ("Tail lamp LH"):
                # one side's own line. A line no past case gave a side
                # ("Taillamps assy" x2 on a case with both lamps) stands for
                # its whole family (prefill.sync_annex_a prints it once).
                "sided": any(side_of(g.item) for g in group),
                "last_seen": max((g.surveyed or "") for g in group) or None,
            })
        out.sort(key=lambda s: (-s["cases"], s["kind"] == "fitting", s["name"]))
        return out

    groups, without_history = [], []
    for family, side in wanted.items():
        source = family
        # Only within the same model: another model's "Sport rim" x2 is no
        # line for this car's one rear wheel.
        if family in _END_FALLBACK and by_model and family not in fam_of.values():
            source = _END_FALLBACK[family]
        # A sided family with no side: a "(side undetermined)" part (or a
        # pre-taxonomy "Tail lamp"). The past lines' LH / RH said which side
        # THAT case had; printed here it read as this case's finding.
        found = lines_for(source, side, unsided=side is None and (family in _SIDED or family in undetermined))
        if found:
            groups.append({"part": family if not side else f"{side} {family}", "reason": "damaged", "lines": found})
        elif source not in fam_of.values():
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
            for fam in {fam_of[id(ln)] for ln in pool if ln.case_ref == ref and fam_of[id(ln)]}:
                counts[fam] = counts.get(fam, 0) + 1
        for fam, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            if fam in wanted or n < 2 or n * 2 < len(with_it):
                continue
            found = lines_for(fam, None, with_it)
            if found:
                groups.append({"part": fam, "reason": f"often replaced with {family} ({n} of {len(with_it)} cases)", "lines": found})
                wanted[fam] = None  # list each companion once

    return {"scope": scope_label, "cases_used": len(cases), "groups": groups, "without_history": without_history}


# Families whose lines don't say which end of the car: "Wheel" and "Tyre"
# hold the rim and tyre lines that name no end ("Sport rim"), so their
# usual wording could name the wrong wheel. Those take the floor
# (taxonomy.FIRM_PART_NAMES), which knows front from rear.
_COARSE_FAMILIES = {"Wheel", "Tyre"}


def firm_wording(db: Session, part: str, make: str | None = None, model: str | None = None) -> str | None:
    """How the firm's past reports name a damaged part: the family's main
    wording -- its shortest part line (not a fitting), as prefill picks the
    main row -- in the form written most often.

    Only for a part the floor (taxonomy.FIRM_PART_NAMES) has no wording for
    (prefill.row_name asks the floor first): the shortest line of a family
    often names a different sub-part ("Front bumper fog lamps cover" for a
    fog lamp, "Rear fender top side joint panels" for a quarter panel,
    "Rear bumper mudflaps" for a wheel arch liner -- review, 2026-11-08). And
    only from past cases of the same make (the same model first), never any
    make's. The case's side goes on ("Tail lamp LH"); a part whose side
    nobody determined gets none, never the past case's. None when no such
    past case has the family as a part."""
    from app.reports.taxonomy import SIDE_CODES

    canon = canonical_part(part or "")
    if not canon or canon == "Other / Not Listed":
        return None
    family, side = family_and_side(canon)
    if family in _COARSE_FAMILIES:
        return None
    lines = [ln for ln in past_lines(db) if _line_family(ln) == family]
    scopes = (
        [ln for ln in lines if _same(ln.make, make) and _same_model(ln.model, model)],
        [ln for ln in lines if _same(ln.make, make)],
    )
    pool = next((s for s in scopes if s), [])
    by_key: dict[str, list[PastAnnexLine]] = {}
    for ln in pool:
        if ln.item_key:
            by_key.setdefault(ln.item_key, []).append(ln)
    # A family past cases only ever refitted (the rear windscreen's
    # moulding, seal and clips, all "Necessary") has no wording for the
    # damaged part itself.
    part_keys = [k for k, group in by_key.items()
                 if not all((g.condition or "").strip().lower() in FITTING_CONDITIONS for g in group)]
    if not part_keys:
        return None
    main = min(part_keys, key=lambda k: (len(k), k))
    names = [g.item for g in by_key[main]]
    name = max(sorted(set(names)), key=names.count)
    if side:
        name = _with_side(name, side)
        if side_of(name) is None:
            name = f"{name} {SIDE_CODES[side]}"
    elif family in _SIDED or canon.endswith(" (side undetermined)"):
        name = _without_side(name)
    return name


def _labour_rows(pool: list[PastLabourLine], have: set[str], vehicle_label) -> list[dict]:
    """One row per wording key: the most common wording, the median
    adjusted cost and median workshop figure, how many cases had it."""
    groups: dict[str, list[PastLabourLine]] = {}
    for ln in pool:
        if ln.desc_key and ln.desc_key not in have:
            groups.setdefault(ln.desc_key, []).append(ln)
    out = []
    for group in groups.values():
        names = [g.description for g in group]
        # Most common wording; sorted first so a tie is decided the same
        # way every time.
        description = max(sorted(set(names)), key=names.count)
        prices = [g.adjusted_cost for g in group if g.adjusted_cost and g.adjusted_cost > 0]
        ests = [g.workshop_est for g in group if g.workshop_est and g.workshop_est > 0]
        cases = len({g.case_ref for g in group})
        out.append({
            "description": description,
            "usual_price": round(statistics.median(prices), 2) if prices else None,
            "usual_workshop_est": round(statistics.median(ests), 2) if ests else None,
            "cases": cases,
            "source": f"{cases} past {vehicle_label(group)} case{'s' if cases != 1 else ''}",
        })
    out.sort(key=lambda s: (-s["cases"], s["description"].lower()))
    return out


def suggest_labour(db: Session, make: str | None, model: str | None, existing: list[str] | tuple = ()) -> dict:
    """Annex B lines past cases wrote, with what each was usually adjusted
    to: the same make and model first (when there is history for it), then
    every other imported case. Lines whose wording is already in this
    case's Annex B (`existing`) are left out, as are repeats of the first
    group in the second. Answer: {"groups": [{"reason", "lines": [
    {"description", "usual_price", "usual_workshop_est", "cases", "source"}
    ]}], "cases_used"}."""
    lines = db.query(PastLabourLine).all()
    have = {normalise_labour(e) for e in existing if e}
    same = [ln for ln in lines if _same(ln.make, make) and _same_model(ln.model, model)]
    same_ids = {ln.id for ln in same}
    others = [ln for ln in lines if ln.id not in same_ids]
    groups = []
    if same:
        rows = _labour_rows(same, have, lambda g: f"{make} {same[0].model}")
        if rows:
            groups.append({"reason": f"past {make} {same[0].model} cases", "lines": rows})
        have |= {ln.desc_key for ln in same}

    def vehicles(group: list[PastLabourLine]) -> str:
        names = sorted({" ".join(x for x in (g.make, g.model) if x) for g in group} - {""})
        return "other-vehicle" if not names else ", ".join(names[:3]) + (", ..." if len(names) > 3 else "")

    if others:
        rows = _labour_rows(others, have, vehicles)
        if rows:
            groups.append({"reason": "other past cases" if same else "all imported cases", "lines": rows})
    return {"groups": groups, "cases_used": len({ln.case_ref for ln in lines})}
