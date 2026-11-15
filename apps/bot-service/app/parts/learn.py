"""Signed-off cases teach the past-case parts lists (contract 8 L, 2026-11-12).

The repair kits and usual prices (app/parts/history.py) came only from the
firm's CL spreadsheets, imported by hand. A case signed off in Carlink is
the same record: the Annex A lines a surveyor wrote for its damage and what
each was adjusted to. So at sign-off its final Annex A joins the history as
one past case, "carlink:<report id>" -- replaced when it is signed off
again, removed when it is reopened or deleted -- and the next case of that
make and model gets those lines and prices without anyone importing a file.

Exactly what the spreadsheet importer stores (history.import_case), from
the priced rows the firm's report prints (rendering/renderer.report_view):
List Items then S/Nett Items, numbered in that order; W/shop Est the line
total; Disc the fraction (none on a S/Nett line); the adjusted cost as the
report works it out (synthesize_annexes.adjusted_cost), which is where a
line's usual unit price comes from. Personal data: less than the importer keeps --
no plate (the case id leads back to the case); the vehicle's make, model
and registration date and the date of survey, as a spreadsheet has them.
Only for a case whose make and model are known: a line under no model would
be offered to every car of the make.

settings.learn_from_signed_off switches it off; history.past_lines then
also leaves out what was learned before, and never lets a case be priced
from its own lines. No Gemini call.
"""
import logging
from datetime import date

from sqlalchemy.orm import Session

from app.config import settings
from app.parts.history import LEARNED_PREFIX, PastCase, PastLine, _date, cl_ref, import_case, split_make_model
from app.reports.models import PastAnnexLine, PastLabourLine, Report
from app.reports.synthesize_annexes import adjusted_cost

logger = logging.getLogger(__name__)


def case_ref(report_id: str) -> str:
    """The past case a signed-off Carlink case is in the history as."""
    return f"{LEARNED_PREFIX}{report_id}"


def _iso_date(text) -> str | None:
    """A cover date as the spreadsheet importer stores it (ISO), from the
    firm's wording ("27th February 2023 (10.15am)", history._date) or an
    ISO date; None when it reads as neither."""
    text = str(text or "").strip()
    if not text:
        return None
    found = _date(text)
    if found:
        return found
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def our_ref(data: dict) -> str:
    """The case's Our Ref, a CL reference written one way ("CL 12330",
    history.cl_ref), so a case whose exported spreadsheet is imported too
    counts once (history.past_lines compares both sides through cl_ref);
    any other wording as typed; "" when none is issued."""
    return cl_ref(str((data or {}).get("report_id") or ""))[:60]


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def past_case_from(report: Report) -> PastCase | None:
    """The case's final Annex A as a past case, or None when there is
    nothing to learn: make or model unknown, or no priced line.

    Only the lines with a workshop estimate: an unpriced line on a Carlink
    case is mostly a row the machine made that nobody priced, not the
    surveyor's record of a line -- and a learned case of a new model with
    nothing but such lines made that model the scope of suggest() and hid
    the make's priced lines (review of contract 8 L, 2026-11-12). Line
    numbers stay the report's."""
    data = report.data if isinstance(report.data, dict) else {}
    vehicle = data.get("vehicle_info") if isinstance(data.get("vehicle_info"), dict) else {}
    raw = " ".join(str(v).strip() for v in (vehicle.get("make"), vehicle.get("model")) if v and str(v).strip())
    # The importer's own split, so make and model compare as an imported
    # case's do -- and history.refresh_lines, which splits make_model_raw
    # again after a rule change, reads them the same way.
    make, model = split_make_model(raw)
    if not make or not model:
        return None
    annex = data.get("annex_a") if isinstance(data.get("annex_a"), dict) else {}
    rows = [r for r in (annex.get("items") or []) if isinstance(r, dict) and str(r.get("part_name") or "").strip()]
    # As the report prints them: List Items, then S/Nett Items.
    rows = [r for r in rows if not r.get("is_net_item")] + [r for r in rows if r.get("is_net_item")]
    if not rows:
        return None
    sign_off = data.get("sign_off") if isinstance(data.get("sign_off"), dict) else {}
    cover = data.get("cover_page") if isinstance(data.get("cover_page"), dict) else {}
    case = PastCase(
        ref=case_ref(report.id),
        plate="",
        make=make,
        model=model,
        make_model_raw=raw,
        registered=_iso_date(vehicle.get("date_of_registration")),
        # The date of survey as the cover page says it; a case signed with
        # it left blank takes the sign-off date, so "last seen" still says
        # when the firm last wrote the line.
        surveyed=_iso_date(cover.get("date_of_survey")) or _iso_date(sign_off.get("signature_date")),
    )
    for number, row in enumerate(rows, start=1):
        nett = bool(row.get("is_net_item"))
        qty = _number(row.get("qty")) or 1.0
        est = round(_number(row.get("workshop_est")), 2)
        if est <= 0:
            continue
        discount = None if nett else _number(row.get("discount_pct"))
        qty_text = str(row.get("qty_text") or "").strip()
        case.lines.append(PastLine(
            line_no=number,
            section="nett" if nett else "list",
            item=str(row["part_name"]).strip()[:200],
            condition=str(row.get("condition") or "").strip()[:80],
            remarks=str(row.get("action") or "").strip()[:80],
            qty=qty,
            qty_raw=qty_text or f"{qty:g}",
            workshop_est=est,
            discount=discount,
            adjusted_cost=adjusted_cost(est, discount, nett),
        ))
    return case if case.lines else None


def forget(db: Session, report_id: str) -> int:
    """Takes the case out of the history (reopened, deleted, or about to be
    learned again). The caller commits. Returns the lines removed."""
    ref = case_ref(report_id)
    n = db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == ref).delete(synchronize_session=False)
    db.query(PastLabourLine).filter(PastLabourLine.case_ref == ref).delete(synchronize_session=False)
    return n


def signed_off_as(report: Report | None, signature_date: str | None) -> bool:
    """Still signed off, and by the sign-off this work is for: what a
    sign-off records after its response (api/main._after_sign_off) must not
    be written for a case reopened -- or signed again -- meanwhile.
    signature_date None: any sign-off (the backfill scripts)."""
    if report is None or report.status != "Signed Off":
        return False
    sign_off = (report.data or {}).get("sign_off") if isinstance(report.data, dict) else None
    if not isinstance(sign_off, dict) or sign_off.get("status") != "Signed Off":
        return False
    return signature_date is None or sign_off.get("signature_date") == signature_date


def learn_case(db: Session, report_id: str, signature_date: str | None = None) -> int | None:
    """Puts a signed-off case's final Annex A into the history, replacing
    what an earlier sign-off of it put there; commits. Returns the lines
    stored, or None when nothing was learned (learning off, the case no
    longer signed off by this sign-off, make or model unknown, no lines).

    The removal goes first: as the transaction's first write it takes
    SQLite's write lock before the case is read again, so a reopen
    committed a moment earlier is seen, and one arriving now waits."""
    if not settings.learn_from_signed_off:
        return None
    forget(db, report_id)
    report = db.get(Report, report_id)
    if not signed_off_as(report, signature_date):
        db.rollback()
        return None
    case = past_case_from(report)
    if case is None:
        # A re-sign-off whose make or model has gone blank keeps nothing of
        # the earlier one.
        db.commit()
        return None
    count = import_case(db, case, our_ref(report.data or {}))
    db.commit()
    return count


def learned_cases(db: Session) -> set[str]:
    """The report ids whose lines are in the history now."""
    refs = db.query(PastAnnexLine.case_ref).filter(PastAnnexLine.case_ref.startswith(LEARNED_PREFIX)).distinct()
    return {ref[len(LEARNED_PREFIX):] for (ref,) in refs}
