"""The report's other pages, in the firm's spreadsheet format (2026-11-01):
the Cover Page, Annex B (labour and spray painting), Annex C (the
recommendation letter's figures), and pricing Annex A from past cases.

    PUT  /reports/{id}/cover-page           the cover page's reference, vehicle and insurance facts
    PUT  /reports/{id}/annex-b              save the labour and spray-painting table
    PUT  /reports/{id}/annex-c              the contract lump sum and working days
    GET  /reports/{id}/labour-suggestions   the firm's usual labour lines, with past prices
    POST /reports/{id}/annex-a/prefill      price Annex A from past cases (app/parts/prefill.py)

Annex A itself is saved in api/parts.py (PUT /annex-a), whose rules these
follow: a change needs the right to edit the case (auth/deps.editable_case:
404 to anyone who may not see it, 403 otherwise, and nobody on a public
report still in the inbox), refuses an intake draft (409) and a signed-off
case (409), drops the rendered PDF so the next download is current, and
writes a history entry. The past-case prices are the firm's price book:
admins and "see all cases" staff only (parts._sees_price_book).
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api import limits
from app.api.parts import _report, _sees_price_book, _signed_off
from app.auth.core import CurrentUser, audit
from app.auth.deps import editable_case, not_allowed, visible_case
from app.parts.history import suggest_labour
from app.parts.prefill import prefill_annex_a, prefill_counts
from app.reports.db import SessionLocal
from app.reports.models import Report
from app.reports.schema import AnnexBLabourItem, CoverPageInfo, InsuranceDetails, TyreCondition, VehicleInfo
from app.reports.synthesize_annexes import synthesize_annexes_for_report
from app.reports.taxonomy import body_type_for

logger = logging.getLogger(__name__)
router = APIRouter(tags=["annexes"])

LOCKED = "Report is signed off and locked. Reopen it before editing."
PRICE_BOOK_ONLY = "Prices from past cases are the firm's price book: for admins and \"see all cases\" staff."


def _editable(db, report_id: str) -> Report:
    """The case, when it may be changed: 404 / 409 intake (_report), 409
    when signed off."""
    r = _report(db, report_id)
    if _signed_off(r):
        raise HTTPException(status_code=409, detail=LOCKED)
    return r


def _vehicle_name(data: dict) -> str | None:
    vehicle = data.get("vehicle_info") or {}
    return " ".join(x for x in (vehicle.get("make"), vehicle.get("model")) if x) or None


# ----------------------------------------------------------- cover page


class CoverPageSave(BaseModel):
    """Only keys present in the body change (pydantic's exclude_unset tells
    an absent key from an explicit null, which clears)."""
    report_id: Optional[str] = None
    incident_datetime: Optional[str] = None
    cover_page: Optional[CoverPageInfo] = None
    vehicle_info: Optional[VehicleInfo] = None
    insurance_details: Optional[InsuranceDetails] = None


@router.put("/reports/{report_id}/cover-page")
def save_cover_page(report_id: str, body: CoverPageSave, user: CurrentUser = Depends(editable_case)) -> dict:
    """Saves the cover page: Our Ref, the accident date, the firm's own
    cover fields (dates of request / survey / dismantle / painting, static
    checks), and the vehicle and insurance facts it prints, merged key by
    key into what is stored. Tyres are replaced as a whole (the four
    positions are one table on the page)."""
    db = SessionLocal()
    try:
        r = _editable(db, report_id)
        data = dict(r.data or {})
        sent = body.model_dump(exclude_unset=True)
        if "report_id" in sent:
            # Our Ref is staff's to set (api/limits.checked_our_ref).
            data["report_id"] = limits.checked_our_ref(user, sent["report_id"], data.get("report_id"))
        if "incident_datetime" in sent:
            data["incident_datetime"] = sent["incident_datetime"]
        defaults = {"cover_page": CoverPageInfo, "vehicle_info": VehicleInfo, "insurance_details": InsuranceDetails}
        for key, model in defaults.items():
            if key not in sent:
                continue
            patch = sent[key]
            if patch is None:
                # The whole section sent as null: back to blank.
                data[key] = model().model_dump()
                continue
            current = dict(data.get(key) or {})
            if key == "vehicle_info" and "tyres" in patch:
                tyres = body.vehicle_info.tyres
                patch["tyres"] = (tyres if tyres is not None else TyreCondition()).model_dump()
            current.update(patch)
            if key == "vehicle_info":
                # The catalogue decides the body type from make and model,
                # as every other save of vehicle_info does (api/main.py).
                current["body_type"] = body_type_for(current.get("make"), current.get("model"), current.get("body_type"))
            data[key] = current
        r.data = data
        # Rendered again on the next download (GET /reports/{id}/download).
        r.pdf_path = None
        audit(db, user, "cover_page_edited", r.id)
        db.commit()
        return {
            "report_id": data.get("report_id"),
            "incident_datetime": data.get("incident_datetime"),
            "cover_page": data.get("cover_page"),
            "vehicle_info": data.get("vehicle_info"),
            "insurance_details": data.get("insurance_details"),
        }
    finally:
        db.close()


# -------------------------------------------------------------- Annex B


class AnnexBSave(BaseModel):
    items: list[AnnexBLabourItem] = Field(max_length=100)


@router.put("/reports/{report_id}/annex-b")
def save_annex_b(report_id: str, body: AnnexBSave, user: CurrentUser = Depends(editable_case)) -> dict:
    """Saves the labour and spray-painting table. The adjusted cost is the
    surveyor's own figure per line (no discount rule), so it stays as
    typed; only the totals and Annex C's are worked out here."""
    db = SessionLocal()
    try:
        r = _editable(db, report_id)
        items = []
        for item in body.items:
            row = item.model_dump()
            row["description"] = " ".join(row["description"].split())
            if not row["description"]:
                continue
            row["item_no"] = f"{len(items) + 1}.0"
            row["workshop_est"] = round(float(row.get("workshop_est") or 0.0), 2)
            row["adjusted_cost"] = round(float(row.get("adjusted_cost") or 0.0), 2)
            items.append(row)
        data = dict(r.data or {})
        data["annex_b"] = {"items": items}
        data = synthesize_annexes_for_report(data)
        r.data = data
        r.pdf_path = None
        audit(db, user, "annex_b_edited", r.id, detail=f"{len(items)} line(s)")
        db.commit()
        return {"annex_b": data["annex_b"], "annex_c": data["annex_c"]}
    finally:
        db.close()


# -------------------------------------------------------------- Annex C


class AnnexCSave(BaseModel):
    """The two figures the surveyor settles with the repairer; absent keys
    stay, null clears. Nothing else of Annex C is typed -- the totals are
    sums (synthesize_annexes) and the letter's wording is fixed."""
    agreed_lump_sum: Optional[float] = Field(default=None, ge=0)
    repair_days: Optional[int] = Field(default=None, ge=0)


@router.put("/reports/{report_id}/annex-c")
def save_annex_c(report_id: str, body: AnnexCSave, user: CurrentUser = Depends(editable_case)) -> dict:
    db = SessionLocal()
    try:
        r = _editable(db, report_id)
        data = dict(r.data or {})
        annex_c = dict(data.get("annex_c") or {})
        annex_c.update(body.model_dump(exclude_unset=True))
        data["annex_c"] = annex_c
        data = synthesize_annexes_for_report(data)
        r.data = data
        r.pdf_path = None
        lump = annex_c.get("agreed_lump_sum")
        days = annex_c.get("repair_days")
        audit(db, user, "annex_c_edited", r.id, detail=", ".join(
            p for p in (f"lump sum S${lump:,.2f}" if lump is not None else "", f"{days} working days" if days is not None else "") if p
        ))
        db.commit()
        return {"annex_c": data["annex_c"]}
    finally:
        db.close()


# ------------------------------------------------------- past-case help


@router.get("/reports/{report_id}/labour-suggestions")
def labour_suggestions(report_id: str, user: CurrentUser = Depends(visible_case)) -> dict:
    """The Annex B lines past cases wrote, with what each was usually
    adjusted to: the same make and model first, then the rest; lines already
    in this case's Annex B left out (history.suggest_labour). Built from
    the price book, so anyone else gets empty groups and hidden: true."""
    db = SessionLocal()
    try:
        r = _report(db, report_id)
        data = r.data or {}
        if not _sees_price_book(user):
            return {"vehicle": _vehicle_name(data), "groups": [], "cases_used": 0, "hidden": True}
        vehicle = data.get("vehicle_info") or {}
        existing = [it.get("description", "") for it in ((data.get("annex_b") or {}).get("items") or [])]
        out = suggest_labour(db, vehicle.get("make"), vehicle.get("model"), existing)
        return {"vehicle": _vehicle_name(data), "groups": out["groups"], "cases_used": out["cases_used"], "hidden": False}
    finally:
        db.close()


@router.post("/reports/{report_id}/annex-a/prefill")
def prefill_annex_a_route(report_id: str, user: CurrentUser = Depends(editable_case)) -> dict:
    """Prices Annex A from past cases (prefill.prefill_annex_a): rows a
    person priced stay, every prefilled or unpriced row is replaced by what
    past cases wrote for the damaged parts. The same thing finalize does
    when a case is filed, for pressing again after the damage list or the
    imported history changed. 403 for anyone the price book is hidden
    from: the prices would land in their case."""
    db = SessionLocal()
    try:
        r = _editable(db, report_id)
        if not _sees_price_book(user):
            raise not_allowed(PRICE_BOOK_ONLY)
        data = prefill_annex_a(db, r.data or {})
        r.data = data
        r.pdf_path = None
        filled, unpriced = prefill_counts(data)
        audit(db, user, "annex_a_prefilled", r.id, detail=f"{filled} row(s) priced from past cases, {unpriced} unpriced")
        db.commit()
        return {"annex_a": data["annex_a"], "annex_c": data["annex_c"], "filled": filled, "unpriced": unpriced}
    finally:
        db.close()
