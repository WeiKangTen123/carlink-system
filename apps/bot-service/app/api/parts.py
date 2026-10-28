"""Past-report history, Annex A suggestions, and saving Annex A.

    POST   /parts-history/import               admin: upload past report spreadsheets
    GET    /parts-history                      the imported cases and usual prices
    DELETE /parts-history/{case_ref}           admin: remove one imported report
    GET    /reports/{id}/annex-suggestions     lines past cases wrote for its damage
    POST   /reports/{id}/annex-suggestions/ai  Gemini's lines for parts with no history
    PUT    /reports/{id}/annex-a               save the surveyor's Annex A

Suggestions are never written into a case by themselves: the surveyor picks
lines, and the dashboard saves the table with PUT /annex-a.
"""
import json
import logging
import statistics
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.ai import keys as ai_keys
from app.ai.keys import AiActor, AiUnavailable
from app.ai.structured import call_structured
from app.auth.core import CurrentUser, audit
from app.auth.deps import CASE_NOT_FOUND, current_user, editable_case, require_admin, visible_case
from app.auth.permissions import can_view_all_cases, is_admin
from app.parts.history import FITTING_CONDITIONS, canonical_part, import_case, parse_report, suggest
from app.reports.db import SessionLocal
from app.reports.models import PastAnnexLine, Report
from app.reports.schema import AnnexAPartItem
from app.reports.synthesize_annexes import synthesize_annexes_for_report

logger = logging.getLogger(__name__)
router = APIRouter(tags=["parts"])

MAX_IMPORT_FILES = 50
MAX_IMPORT_BYTES = 5 * 1024 * 1024  # one report spreadsheet is well under 1MB


def _report(db, report_id: str) -> Report:
    r = db.get(Report, report_id)
    if r is None:
        raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
    if r.status == "intake":
        raise HTTPException(status_code=409, detail="This case is still an intake draft.")
    return r


def _signed_off(r: Report) -> bool:
    return ((r.data or {}).get("sign_off") or {}).get("status") == "Signed Off"


def _sees_price_book(user) -> bool:
    """The imported past reports -- their lines and what each was adjusted
    to -- are the firm's price book: admins and "see all cases" staff only.
    Anyone else gets the same answer shapes with empty lists and
    hidden: true. Plates and file names are for admins alone."""
    return can_view_all_cases(user)


# ------------------------------------------------------------ history


@router.post("/parts-history/import")
def import_past_reports(files: list[UploadFile] = File(...), admin: CurrentUser = Depends(require_admin)) -> dict:
    """Each file is one past report (Cover Page + Annex A). A report imported
    again replaces its earlier lines. Files that aren't reports are listed
    with the reason, the rest still import."""
    if len(files) > MAX_IMPORT_FILES:
        raise HTTPException(status_code=400, detail=f"At most {MAX_IMPORT_FILES} files at a time.")
    results = []
    db = SessionLocal()
    try:
        for upload in files:
            name = Path(upload.filename or "report").name
            suffix = Path(name).suffix.lower()
            if suffix not in (".xls", ".xlsx"):
                results.append({"file": name, "error": "Not an .xls or .xlsx spreadsheet."})
                continue
            raw = upload.file.read(MAX_IMPORT_BYTES + 1)
            if len(raw) > MAX_IMPORT_BYTES:
                results.append({"file": name, "error": "Over 5MB -- not a single report."})
                continue
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(raw)
                tmp_path = Path(tmp.name)
            try:
                case = parse_report(tmp_path)
                if not case.lines:
                    results.append({"file": name, "error": "No Annex A lines found."})
                    continue
                count = import_case(db, case, name)
                results.append({"file": name, "ref": case.ref, "make": case.make, "model": case.model, "lines": count})
            except Exception as exc:  # one bad file mustn't stop the rest
                results.append({"file": name, "error": f"Couldn't read it as a report ({str(exc)[:120]})."})
            finally:
                tmp_path.unlink(missing_ok=True)
        imported = [r for r in results if "lines" in r]
        if imported:
            audit(db, admin, "past_reports_imported", detail=", ".join(r["ref"] for r in imported)[:480])
        db.commit()
        return {"results": results}
    finally:
        db.close()


@router.get("/parts-history")
def parts_history(q: str = "", make: str = "", model: str = "", user: CurrentUser = Depends(current_user)) -> dict:
    """Imported cases, and per line wording and vehicle: how many cases
    had it and what one unit was adjusted to (_sees_price_book)."""
    if not _sees_price_book(user):
        return {"cases": [], "lines": [], "hidden": True}
    admin = is_admin(user)
    db = SessionLocal()
    try:
        lines = db.query(PastAnnexLine).all()
        cases = {}
        for ln in lines:
            cases.setdefault(ln.case_ref, {"ref": ln.case_ref, "plate": ln.plate if admin else "", "make": ln.make,
                                           "model": ln.model, "surveyed": ln.surveyed, "lines": 0,
                                           "file": ln.source_file if admin else ""})
            cases[ln.case_ref]["lines"] += 1
        needle = q.strip().lower()
        groups: dict[tuple, list[PastAnnexLine]] = {}
        for ln in lines:
            if make and ln.make.lower() != make.lower():
                continue
            if model and ln.model.lower() != model.lower():
                continue
            if needle and needle not in ln.item.lower():
                continue
            groups.setdefault((ln.make, ln.model, ln.item_key), []).append(ln)
        rows = []
        for (mk, md, _), group in groups.items():
            prices = [round(g.adjusted_cost / (g.qty or 1), 2) for g in group if g.adjusted_cost and g.adjusted_cost > 0]
            names = [g.item for g in group]
            rows.append({
                "item": max(set(names), key=names.count),
                "family": group[0].family,
                "make": mk,
                "model": md,
                "cases": len({g.case_ref for g in group}),
                "usual_unit_price": round(statistics.median(prices), 2) if prices else None,
                "min_price": min(prices) if prices else None,
                "max_price": max(prices) if prices else None,
                "fitting": all((g.condition or "").strip().lower() in FITTING_CONDITIONS for g in group),
                "last_seen": max((g.surveyed or "") for g in group) or None,
            })
        rows.sort(key=lambda r: (r["make"], r["model"], r["family"] or "~", -r["cases"], r["item"]))
        return {"cases": sorted(cases.values(), key=lambda c: c["surveyed"] or "", reverse=True), "lines": rows[:1000],
                "hidden": False}
    finally:
        db.close()


@router.delete("/parts-history/{case_ref}")
def delete_past_report(case_ref: str, admin: CurrentUser = Depends(require_admin)) -> dict:
    db = SessionLocal()
    try:
        n = db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == case_ref).delete()
        if not n:
            raise HTTPException(status_code=404, detail="No imported report with that reference.")
        audit(db, admin, "past_report_removed", detail=case_ref)
        db.commit()
        return {"ok": True, "lines": n}
    finally:
        db.close()


# -------------------------------------------------------- suggestions


def _damaged_parts(data: dict) -> list[str]:
    parts = [it.get("part") for it in (data.get("damage_summary") or []) if it.get("part")]
    return parts or [p for p in (data.get("damaged_parts") or []) if p]


@router.get("/reports/{report_id}/annex-suggestions")
def annex_suggestions(report_id: str, user: CurrentUser = Depends(visible_case)) -> dict:
    db = SessionLocal()
    try:
        r = _report(db, report_id)
        data = r.data or {}
        vehicle = data.get("vehicle_info") or {}
        existing = [it.get("part_name", "") for it in ((data.get("annex_a") or {}).get("items") or [])]
        parts = []
        for p in _damaged_parts(data):
            name = canonical_part(p)
            if name and name != "Other / Not Listed" and name not in parts:
                parts.append(name)
        if _sees_price_book(user):
            result = suggest(db, make=vehicle.get("make"), model=vehicle.get("model"), damaged_parts=parts, existing_items=existing)
            result["hidden"] = False
        else:
            # Built from the price book, so nothing of it: as if no past
            # report covered any part (the AI may still be asked for lines).
            result = {"scope": "", "cases_used": 0, "groups": [], "without_history": parts, "hidden": True}
        result["vehicle"] = " ".join(x for x in (vehicle.get("make"), vehicle.get("model")) if x) or None
        result["damaged_parts"] = parts
        return result
    finally:
        db.close()


_AI_SYSTEM = (
    "You are a Singapore motor insurance loss adjuster writing Annex A (spare parts) of a repair assessment. "
    "For EACH damaged part given, list the lines a Singapore surveyor would write for replacing it: the part "
    "itself; parts mounted on or behind it that are normally damaged with it or renewed with it (brackets, "
    "retainers, reinforcements, lamps, emblems, mouldings, garnishes, trim boards, sensors); and the one-time "
    "fittings renewed on refit (clips, seals, sealant, weatherstrip, insulator). Include follow-on items a "
    "surveyor always adds -- e.g. replacing a tailgate means the rear windscreen comes off with it, so its "
    "moulding, inner seal, clips and sealant. Do NOT list screws, bolts, nuts, bulbs, bulb sockets, wiring, "
    "grommets or labour. Use short workshop names ('Tail gate lock striker', 'Rear bumper side retainer', "
    "'Tail lamp clips'), LH/RH for sides, and a quantity. No part numbers, no prices."
)
_AI_SCHEMA = {
    "type": "object",
    "properties": {
        "parts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "part": {"type": "string"},
                    "lines": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}, "qty": {"type": "number"}},
                            "required": ["name", "qty"],
                        },
                    },
                },
                "required": ["part", "lines"],
            },
        }
    },
    "required": ["parts"],
}


class AiSuggestionRequest(BaseModel):
    parts: list[str] = Field(min_length=1, max_length=40)


@router.post("/reports/{report_id}/annex-suggestions/ai")
def annex_suggestions_ai(report_id: str, body: AiSuggestionRequest, user: CurrentUser = Depends(editable_case)) -> dict:
    """Gemini's lines for parts no imported report covers. Names and
    quantities only -- no prices, no part numbers -- and labelled as AI.
    Reads nothing from the price book, so it has nothing to hide. The
    person asking pays (ai/keys.py): 409 {detail, code} when their AI isn't
    available."""
    actor = AiActor.for_user(user.id, "dashboard")
    ai_keys.ensure_ai_ready(actor)
    db = SessionLocal()
    try:
        r = _report(db, report_id)
        vehicle = (r.data or {}).get("vehicle_info") or {}
    finally:
        db.close()
    car = " ".join(x for x in (vehicle.get("make"), vehicle.get("model"), vehicle.get("year")) if x) or "unknown vehicle"
    prompt = f"Vehicle: {car}, Singapore.\nDamaged parts: {json.dumps(body.parts)}"
    try:
        out = call_structured(
            purpose="annex suggestions",
            system_instruction=_AI_SYSTEM,
            input_parts=[{"type": "text", "text": prompt}],
            schema=_AI_SCHEMA,
            parse=json.loads,
            models=["gemini-3-flash-preview", "gemini-3.6-flash", "gemini-3.5-flash-lite"],
            actor=actor,
            timeout=60.0,
        )
    except AiUnavailable:
        raise  # their own key was refused mid-call: the 409, not "try again"
    except Exception as exc:
        logger.warning("AI annex suggestions failed for %s: %s", report_id, ai_keys.scrub(str(exc)))
        raise HTTPException(status_code=503, detail="The AI couldn't suggest lines just now. Try again in a minute.")
    groups = []
    for p in out.get("parts") or []:
        lines = [
            {"name": str(ln.get("name", "")).strip()[:120], "qty": float(ln.get("qty") or 1), "usual_unit_price": None,
             "cases": 0, "kind": "part", "source": "AI suggestion"}
            for ln in (p.get("lines") or []) if str(ln.get("name", "")).strip()
        ]
        if lines:
            groups.append({"part": str(p.get("part", ""))[:80], "reason": "AI suggestion (no past case)", "lines": lines[:30]})
    return {"groups": groups}


# ---------------------------------------------------------- Annex A save


class AnnexASave(BaseModel):
    items: list[AnnexAPartItem] = Field(max_length=300)


@router.put("/reports/{report_id}/annex-a")
def save_annex_a(report_id: str, body: AnnexASave, user: CurrentUser = Depends(editable_case)) -> dict:
    """Saves the surveyor's Annex A table. Adjusted costs and the Annex A/C
    totals are worked out here, so they always agree with the rows."""
    db = SessionLocal()
    try:
        r = _report(db, report_id)
        if _signed_off(r):
            raise HTTPException(status_code=409, detail="Report is signed off and locked. Reopen it before editing.")
        items = []
        for i, item in enumerate(body.items, start=1):
            row = item.model_dump()
            row["part_name"] = row["part_name"].strip()
            if not row["part_name"]:
                continue
            row["item_no"] = f"{i}.0"
            qty = float(row.get("qty") or 1.0)
            est = float(row.get("workshop_est") or 0.0)
            discount = 0.0 if row.get("is_net_item") else float(row.get("discount_pct") or 0.0)
            row["adjusted_cost"] = round(qty * est * (1.0 - discount), 2) if est else 0.0
            items.append(row)
        data = dict(r.data or {})
        data["annex_a"] = {"items": items}
        data = synthesize_annexes_for_report(data)
        r.data = data
        # Rendered again on the next download (GET /reports/{id}/download).
        r.pdf_path = None
        audit(db, user, "annex_a_edited", r.id, detail=f"{len(items)} line(s)")
        db.commit()
        return {"annex_a": data["annex_a"], "annex_c": data["annex_c"]}
    finally:
        db.close()
