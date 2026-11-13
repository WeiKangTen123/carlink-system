"""Past-report history, Annex A suggestions, and saving Annex A.

    POST   /parts-history/import               admin: upload past report spreadsheets
    GET    /parts-history                      the imported cases and usual prices
    DELETE /parts-history/{case_ref}           admin: remove one imported report
    GET    /reports/{id}/annex-suggestions     lines past cases wrote for its damage
    POST   /reports/{id}/annex-suggestions/ai  Gemini's lines for parts with no history
    PUT    /reports/{id}/annex-a               save the surveyor's Annex A

Suggestions are never written into a case by themselves: the surveyor picks
lines, and the dashboard saves the table with PUT /annex-a. That save also
settles the parts-catalogue rows put on lines (catalogue_ref): checked to
be the case's own, and marked accepted -- or suggested again once no line
carries them (parts/catalogue.sync_accepted).
"""
import json
import logging
import re
import statistics
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, update

from app.ai import keys as ai_keys
from app.ai import runs
from app.ai.keys import AiActor, AiUnavailable
from app.ai.structured import call_structured
from app.api import limits
from app.auth.core import CurrentUser, audit
from app.auth.deps import CASE_NOT_FOUND, ApiError, current_user, editable_case, require_admin, visible_case
from app.auth.permissions import PUBLIC_CHANNEL, can_view_all_cases, is_admin
from app.parts import catalogue
from app.parts.history import FITTING_CONDITIONS, canonical_part, import_case, parse_report, suggest
from app.parts.prefill import ORIGIN_RANK, ORIGINS, line_key, origin_of, side_unconfirmed
from app.reports.db import SessionLocal
from app.reports.models import PastAnnexLine, PastLabourLine, Report, User
from app.reports.schema import AnnexAPartItem
from app.reports.synthesize_annexes import adjusted_cost, synthesize_annexes_for_report
from app.reports.taxonomy import unsided_part

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


def price_book_for_case(db, r: Report, user) -> bool:
    """Whether the price book may be written INTO this case by this person:
    they must see it, and so must the case's owner (or it has none): a
    self-signup's case never carries the firm's prices, whoever edits it,
    since its owner reads its Annex A."""
    if not _sees_price_book(user):
        return False
    if r.owner_id is None or r.owner_id == getattr(user, "id", None):
        return True
    owner = db.get(User, r.owner_id)
    return owner is not None and _sees_price_book(owner)


# ------------------------------------------------------------ history


@router.post("/parts-history/import")
def import_past_reports(files: list[UploadFile] = File(...), admin: CurrentUser = Depends(require_admin)) -> dict:
    """Each file is one past report (Cover Page + Annex A, and Annex B when
    it has one). A report imported again replaces its earlier lines. Files
    that aren't reports are listed with the reason, the rest still import."""
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
                results.append({"file": name, "ref": case.ref, "make": case.make, "model": case.model, "lines": count,
                                "labour": len(case.labour)})
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
        # Annex B lines per report, so the Knowledge page shows the import
        # read them (the picker itself is GET /reports/{id}/labour-suggestions).
        labour_counts = dict(db.query(PastLabourLine.case_ref, func.count()).group_by(PastLabourLine.case_ref).all())
        cases = {}
        for ln in lines:
            cases.setdefault(ln.case_ref, {"ref": ln.case_ref, "plate": ln.plate if admin else "", "make": ln.make,
                                           "model": ln.model, "surveyed": ln.surveyed, "lines": 0,
                                           "labour": labour_counts.get(ln.case_ref, 0),
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
                # Sorted first so a tie between wordings is decided the same
                # way in every process, as history.suggest does.
                "item": max(sorted(set(names)), key=names.count),
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
        labour = db.query(PastLabourLine).filter(PastLabourLine.case_ref == case_ref).delete()
        if not n and not labour:
            raise HTTPException(status_code=404, detail="No imported report with that reference.")
        audit(db, admin, "past_report_removed", detail=case_ref)
        db.commit()
        return {"ok": True, "lines": n, "labour": labour}
    finally:
        db.close()


# -------------------------------------------------------- suggestions


def _damaged_parts(data: dict) -> list[str]:
    """The damaged parts to suggest lines for. A side the pipeline inferred
    and nobody confirmed is asked about as "(side undetermined)", so the
    panel offers "Tail lamp", never a past case's "Tail lamp LH" for a side
    this case hasn't established (prefill.side_unconfirmed)."""
    items = [it for it in (data.get("damage_summary") or []) if it.get("part")]
    if not items:
        return [p for p in (data.get("damaged_parts") or []) if p]
    return [(unsided_part(it["part"]) or it["part"]) if side_unconfirmed(it) else it["part"] for it in items]


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
    available -- on a case the public sent (P6, filed ones too), by the
    stricter rule for those (ai/keys.resolve_keys, public_case)."""
    db = SessionLocal()
    try:
        r = _report(db, report_id)
        vehicle = (r.data or {}).get("vehicle_info") or {}
        public_case = r.channel == PUBLIC_CHANNEL
    finally:
        db.close()
    actor = AiActor.for_user(user.id, "dashboard", public_case=public_case)
    ai_keys.ensure_ai_ready(actor)
    car = " ".join(x for x in (vehicle.get("make"), vehicle.get("model"), vehicle.get("year")) if x) or "unknown vehicle"
    prompt = f"Vehicle: {car}, Singapore.\nDamaged parts: {json.dumps(body.parts)}"
    try:
        # One AI request at a time per person (429 ai_busy). The run log's
        # row (ai/runs.py) only once the turn is had: a refused turn made no
        # AI call.
        with limits.ai_turn(user), runs.recorded(report_id, "annex_suggest", actor) as run:
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
            # The parts it gave lines for.
            parts = out.get("parts") if isinstance(out, dict) else None
            run.parts_found = sum(1 for p in (parts or []) if isinstance(p, dict) and p.get("lines"))
    except (AiUnavailable, ApiError):
        # Their own key refused mid-call (the 409), or AI already running
        # for them (429): not "try again in a minute".
        raise
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


def _qty_fields(row: dict) -> None:
    """qty_text as typed ("1set") with qty the number in it, so the sheet
    prints the text and the statistics keep the number; a bare number typed
    as text is just the qty."""
    text = (row.get("qty_text") or "").strip()
    if not text:
        row["qty_text"] = None
    elif re.fullmatch(r"\d+(\.\d+)?", text):
        row["qty"], row["qty_text"] = float(text), None
    else:
        row["qty_text"] = text[:20]
        m = re.match(r"\d+(\.\d+)?", text)
        if m:
            row["qty"] = float(m.group(0))
    row["qty"] = float(row.get("qty") or 1.0)


def _catalogue_number(row: dict) -> bool:
    """The row's part number is none, or the one its catalogue row brought
    (catalogue_ref.part_number)."""
    number = (row.get("oem_part_number") or "").strip()
    return not number or number == ((row.get("catalogue_ref") or {}).get("part_number") or "").strip()


def _attached_number(row: dict, before: dict) -> bool:
    """The part number changed only because a catalogue row was put on the
    line, unlinked or swapped: putting one on a line fills an empty part
    number and unlinking takes it off again (dashboard lib/catalogue.ts
    attachToLine / detachFromLine). That is not the person changing the
    line -- its origin, and with it the "from past cases" tag and its
    review state, stays."""
    return _catalogue_number(row) and _catalogue_number(before)


def _row_facts(row: dict, other: dict | None = None) -> tuple:
    """What a person can change on a row, normalised for comparing a saved
    row with its stored one (`other`: the row it is compared with -- a part
    number only a catalogue row brought or took counts as unchanged,
    _attached_number)."""
    number = None if other is not None and _attached_number(row, other) else (row.get("oem_part_number") or None)
    return (
        str(row.get("condition") or "").strip(),
        str(row.get("action") or "").strip(),
        float(row.get("qty") or 1.0),
        (row.get("qty_text") or None),
        round(float(row.get("workshop_est") or 0), 2),
        round(float(row.get("discount_pct") or 0), 4),
        bool(row.get("is_net_item")),
        number,
    )


def _from_suggestions(row: dict) -> bool:
    """A NEW row picked from the suggestions panel: it says where it came
    from (suggested_from) and carries the past cases' figure untouched --
    unpriced, or the usual unit price x qty. Anything else new is a
    person's own line."""
    if not row.get("suggested_from"):
        return False
    est = round(float(row.get("workshop_est") or 0), 2)
    usual = row.get("usual_unit_price")
    return est == 0 or (bool(usual) and est == round(float(usual) * float(row.get("qty") or 1.0), 2))


def _origin_after_edit(row: dict, before: dict | None, damaged_parts: set[str]) -> str:
    """"typed" once a person changed anything on the row -- its name (no
    stored row of that name and side), condition, remarks, qty, estimate,
    discount, section or part number -- so a sync (prefill.sync_annex_a)
    leaves it alone. A new row from the suggestions panel may come in as
    "history" (_from_suggestions). Otherwise the client's origin, which
    may raise a row's protection (a lookup price applied) but never lower
    it: a typed or looked-up row sent back as "detection" stays what it was."""
    if before is None:
        if row.get("origin") == "lookup":
            return "lookup"
        return "history" if row.get("origin") == "history" and _from_suggestions(row) else "typed"
    stored = origin_of(before, damaged_parts)
    if row.get("origin") == "lookup":
        # Only the dashboard's [Use] (Phase 3) says so, and applying a looked-up
        # price is itself a change to the row; the client sends "typed" once a
        # person overtypes that price.
        return "lookup"
    if _row_facts(row, before) != _row_facts(before, row):
        return "typed"
    client = row.get("origin") if row.get("origin") in ORIGINS else None
    return client if client and ORIGIN_RANK[client] >= ORIGIN_RANK[stored] else stored


@router.put("/reports/{report_id}/annex-a")
def save_annex_a(report_id: str, body: AnnexASave, user: CurrentUser = Depends(editable_case)) -> dict:
    """Saves the surveyor's Annex A table. Adjusted costs and the Annex A/C
    totals are worked out here, so they always agree with the rows. The
    estimate is the line total as quoted: adjusted = est x (1 - discount),
    not x qty (synthesize_annexes.adjusted_cost). A row whose name or
    estimate changed becomes origin "typed" (the dashboard sends it so too),
    so no later sync rebuilds it; source_part carries over from the stored
    row of the same name when the client leaves it out.

    A row's catalogue_ref (a parts-catalogue row the surveyor put on it):
    422 catalogue_ref unless the suggestion is this case's; its facts are
    the stored row's, whatever was sent; it never touches the estimate.
    After the save every suggestion a line carries is accepted and one no
    line carries any more is suggested again (catalogue.sync_accepted)."""
    db = SessionLocal()
    try:
        # A write first -- the PDF marked out of date, as the save does
        # anyway -- so SQLite's write lock is held from here to the commit
        # and the refs checked below can't change before it. A worker's
        # result (catalogue.store_result, replacing the case's "suggested"
        # rows) lands wholly before -- the check sees it -- or waits for the
        # commit, by which time the rows on lines are accepted, which it
        # keeps. (Read first, a result in between left a line pointing at a
        # deleted row, or failed the save with StaleDataError.) Nothing is
        # read before it in this session (as reports/db._once).
        db.execute(update(Report).where(Report.id == report_id).values(pdf_path=None)
                   .execution_options(synchronize_session=False))
        r = _report(db, report_id)
        if _signed_off(r):
            raise HTTPException(status_code=409, detail="Report is signed off and locked. Reopen it before editing.")
        stored = {}
        for it in ((r.data or {}).get("annex_a") or {}).get("items") or []:
            stored.setdefault(line_key(it.get("part_name") or ""), it)
        damaged_parts = {str(it.get("part") or "").strip() for it in ((r.data or {}).get("damage_summary") or [])}
        items, sent = [], []
        for item in body.items:
            row = item.model_dump()
            row["part_name"] = row["part_name"].strip()
            if not row["part_name"]:
                continue
            row["item_no"] = f"{len(items) + 1}.0"
            before = stored.get(line_key(row["part_name"]))
            # A client that doesn't know catalogue_ref (a page from before
            # it) mustn't unlink a line's catalogue part by leaving it out;
            # an explicit null is Unlink and sticks.
            if "catalogue_ref" not in item.model_fields_set and before is not None:
                row["catalogue_ref"] = before.get("catalogue_ref")
            items.append(row)
            sent.append((item, before))
        # Before anything is worked out from them: the refs as stored.
        try:
            catalogue.checked_refs(db, r.id, items)
        except catalogue.BadCatalogueRef as exc:
            lines = [row["item_no"].split(".")[0] for row in items
                     if (row.get("catalogue_ref") or {}).get("suggestion_id") in exc.ids]
            raise ApiError(
                422,
                f"The catalogue part on line {', '.join(lines)} is no longer on this case (a newer look-up "
                "replaced it). Unlink it and put it on the line again from the catalogue panel.",
                "catalogue_ref",
            )
        for row, (item, before) in zip(items, sent):
            _qty_fields(row)
            row["adjusted_cost"] = adjusted_cost(row.get("workshop_est"), row.get("discount_pct"), bool(row.get("is_net_item")))
            row["origin"] = _origin_after_edit(row, before, damaged_parts)
            # source_part carries over from the stored row only when the
            # client left it OUT: an explicit null is [Keep] on a stale row
            # (the line stays as a hand line), and must stick.
            if "source_part" not in item.model_fields_set and before is not None:
                row["source_part"] = before.get("source_part")
            # The part's-own-row mark (prefill._refresh_kept) likewise: a
            # client that doesn't know the field mustn't clear it.
            if "main" not in item.model_fields_set and before is not None:
                row["main"] = bool(before.get("main"))
        data = dict(r.data or {})
        data["annex_a"] = {"items": items}
        data = synthesize_annexes_for_report(data)
        r.data = data
        # Rendered again on the next download (GET /reports/{id}/download):
        # already cleared by the first statement.
        r.pdf_path = None
        accepted, released = catalogue.sync_accepted(db, r.id, items, user)
        from_catalogue = sum(1 for row in items if row.get("catalogue_ref"))
        detail = f"{len(items)} line(s)"
        if from_catalogue or released:
            detail += f", {from_catalogue} with a catalogue part"
        audit(db, user, "annex_a_edited", r.id, detail=detail)
        db.commit()
        if accepted or released:
            logger.info("Annex A of %s: %d catalogue row(s) accepted, %d back to suggested", r.id, accepted, released)
        return {"annex_a": data["annex_a"], "annex_c": data["annex_c"]}
    finally:
        db.close()
