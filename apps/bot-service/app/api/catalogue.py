"""The parts-catalogue look-up's endpoints (app/parts/catalogue.py; the
contract is plans/2026-11-12-parts-catalogue/CONTRACT.md).

For the case page (signed in, like the rest):

    GET  /reports/{id}/catalogue                   the look-up, its rows, the worker
    POST /reports/{id}/catalogue/lookup            start one (202)
    POST /reports/{id}/catalogue/cancel            stop the open one
    POST /reports/{id}/catalogue/suggestions/{sid} reject a row, or undo that

For the catalogue worker (no session: Authorization: Bearer
CATALOGUE_WORKER_TOKEN, checked by the sign-in middleware and again here;
since 12 Nov 2026 the server's own "parts-worker" container):

    POST /catalogue-worker/claim                   the oldest pending job, or none
    POST /catalogue-worker/jobs/{id}/progress      how far it got; {cancelled}
    POST /catalogue-worker/jobs/{id}/result        the rows it found

Off unless CATALOGUE_ENABLED is true and CATALOGUE_WORKER_TOKEN is set:
then GET .../catalogue answers {"enabled": false}, and every other route
here 404, the same as a path that doesn't exist. Seeing the look-up takes
seeing the case (deps.visible_case); starting, cancelling it and rejecting
rows take the right to change it (deps.editable_case), and refuse an
intake draft and a signed-off case (409) as every other save does --
cancelling excepted, which changes nothing in the report.

Nothing here contacts a parts site or calls the AI, and nothing writes
into the report: the rows are a reference beside Annex A. Putting one on
an Annex A line is PUT /reports/{id}/annex-a (api/parts.py), carrying the
line's catalogue_ref; that save marks the row accepted.
"""
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import IntegrityError

from app.api import limits
from app.auth.core import CurrentUser, audit, display_name
from app.auth.deps import CASE_NOT_FOUND, ApiError, editable_case, visible_case
from app.parts import catalogue
from app.reports.db import SessionLocal
from app.reports.models import CatalogueJob, CatalogueSuggestion, Report

logger = logging.getLogger(__name__)
router = APIRouter(tags=["catalogue"])

LOCKED = "Report is signed off and locked. Reopen it before editing."
DRAFT = "This case is still an intake draft."


def _feature_on() -> None:
    """Every route but GET .../catalogue: 404 while the feature is off,
    before anything else is looked at."""
    if not catalogue.enabled():
        raise HTTPException(status_code=404, detail="Not Found")


def _worker(authorization: Optional[str] = Header(default=None)) -> None:
    """The worker's routes: the middleware already let only a request with
    the right token through (auth/middleware.py); asked again here, so a
    route under /catalogue-worker/ is never open by a mistake there."""
    refused = catalogue.worker_refusal(authorization)
    if refused:
        status, body = refused
        if "code" in body:
            raise ApiError(status, body["detail"], body["code"])
        raise HTTPException(status_code=status, detail=body["detail"])


def _case(db, report_id: str) -> Report:
    r = db.get(Report, report_id)
    if r is None:
        raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
    return r


# ------------------------------------------------------- the case page


@router.get("/reports/{report_id}/catalogue")
def get_catalogue(report_id: str, user: CurrentUser = Depends(visible_case)) -> dict:
    """The case's look-up as the panel shows it (catalogue.catalogue_view),
    or {"enabled": false} while the feature is off."""
    if not catalogue.enabled():
        return {"enabled": False}
    db = SessionLocal()
    try:
        return catalogue.catalogue_view(db, _case(db, report_id), user)
    finally:
        db.close()


class LookupRequest(BaseModel):
    # The catalogue car the surveyor picked ("Change car"), e.g.
    # "microfiches:30584"; null lets the worker find one. Any key the worker
    # sends as a candidate must pass: up to MAX_TEXT long, and its parts are
    # %-encoded (tools/parts_mcp core.models.make_key), so "%" too.
    vehicle_key: Optional[str] = Field(default=None, max_length=catalogue.MAX_TEXT, pattern=r"^[A-Za-z0-9_.:/%~-]+$")


@router.post("/reports/{report_id}/catalogue/lookup", status_code=202, dependencies=[Depends(_feature_on)])
def request_lookup(report_id: str, body: LookupRequest | None = None,
                   user: CurrentUser = Depends(editable_case)) -> dict:
    """Queues a look-up of the case's damaged parts for the catalogue
    worker. 409 draft / signed_off; 409 job_open while one is pending or
    running (the table's one-open-job rule decides a double click); 400
    make_not_covered (no catalogue for this make: CATALOGUE_MAKES),
    no_make_model (nothing to look the car up by) or no_parts (no damaged
    part with a name); 429 lookup_limit when this person has started too
    many lately (limits.check_catalogue_lookups: 3 a day without staff
    access, 20 an hour otherwise; a refused request doesn't count)."""
    vehicle_key = body.vehicle_key if body else None
    db = SessionLocal()
    try:
        r = _case(db, report_id)
        reason = catalogue.blocked_reason(user, r)
        if reason == "draft":
            raise ApiError(409, DRAFT, "draft")
        if reason == "signed_off":
            raise ApiError(409, LOCKED, "signed_off")
        if catalogue.latest_job(db, r.id, catalogue.OPEN_STATUSES) is not None:
            raise ApiError(409, "A catalogue look-up is already queued or running for this case.", "job_open")
        if reason == "make_not_covered":
            make = ((r.data or {}).get("vehicle_info") or {}).get("make")
            raise ApiError(400, catalogue.not_covered_text(make), "make_not_covered")
        if reason == "no_make_model":
            raise ApiError(400, "Enter the car's make and model on the cover page first.", "no_make_model")
        payload = catalogue.job_payload(r, vehicle_key)
        # GET .../catalogue already says so (blocked_reason "no_parts"); a
        # click from an older page still gets this answer.
        if reason == "no_parts" or not payload["parts"]:
            raise ApiError(400, "There are no damaged parts to look up.", "no_parts")
        job = CatalogueJob(
            report_id=r.id, kind="lookup", status="pending", vehicle_key=payload["vehicle_key"], payload=payload,
            requested_by_id=user.id, requested_by=display_name(user), progress_total=len(payload["parts"]),
        )
        db.add(job)
        try:
            # The write lock from here to the commit: the count below
            # includes this job and holds.
            db.flush()
        except IntegrityError:
            db.rollback()
            raise ApiError(409, "A catalogue look-up is already queued or running for this case.", "job_open")
        try:
            limits.check_catalogue_lookups(db, user)
        except ApiError:
            db.rollback()
            raise
        audit(db, user, "catalogue_lookup_requested", r.id, detail=f"{len(payload['parts'])} part(s)")
        db.commit()
        db.refresh(job)
        return {"job": catalogue.job_out(job)}
    finally:
        db.close()


@router.post("/reports/{report_id}/catalogue/cancel", dependencies=[Depends(_feature_on)])
def cancel_lookup(report_id: str, user: CurrentUser = Depends(editable_case)) -> dict:
    """Cancels the case's pending or running look-up; a worker busy with
    it hears so at its next progress report and stops. Pressed again on
    the one just cancelled, it answers that job as it is; 409 job_not_open
    when the latest look-up has finished (or there is none)."""
    db = SessionLocal()
    try:
        r = _case(db, report_id)
        job = catalogue.latest_job(db, r.id)
        if job is not None and job.status == "cancelled":
            return {"job": catalogue.job_out(job)}
        if job is None or job.status not in catalogue.OPEN_STATUSES or not catalogue.cancel(db, job.id, display_name(user)):
            raise ApiError(409, "There is no queued or running look-up to cancel.", "job_not_open")
        audit(db, user, "catalogue_lookup_cancelled", r.id, detail=f"was {job.status}")
        db.commit()
        db.refresh(job)
        return {"job": catalogue.job_out(job)}
    finally:
        db.close()


class SuggestionDecision(BaseModel):
    action: Literal["reject", "undo"]


@router.post("/reports/{report_id}/catalogue/suggestions/{suggestion_id}", dependencies=[Depends(_feature_on)])
def decide_suggestion(report_id: str, suggestion_id: int, body: SuggestionDecision,
                      user: CurrentUser = Depends(editable_case)) -> dict:
    """reject: the row is not this case's part; undo: it is a suggestion
    again. Repeating either changes nothing. 409 accepted for a row
    already in Annex A (remove the line there instead), 409 draft /
    signed_off as every save."""
    db = SessionLocal()
    try:
        r = _case(db, report_id)
        s = db.get(CatalogueSuggestion, suggestion_id)
        if s is None or s.report_id != r.id:
            raise HTTPException(status_code=404, detail="Suggestion not found.")
        reason = catalogue.blocked_reason(user, r)
        if reason == "draft":
            raise ApiError(409, DRAFT, "draft")
        if reason == "signed_off":
            raise ApiError(409, LOCKED, "signed_off")
        if s.status == "accepted":
            raise ApiError(409, "This part is in Annex A: remove the line there instead.", "accepted")
        catalogue.decide(s, body.action, user)
        db.commit()
        db.refresh(s)
        matcher = catalogue.LineMatcher(((r.data or {}).get("annex_a") or {}).get("items") or [])
        return {"suggestion": catalogue.suggestion_out(s, matcher, catalogue.variant_key_of(db, s))}
    finally:
        db.close()


# ---------------------------------------------------------- the worker


class ClaimRequest(BaseModel):
    worker: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.@-]+$")
    version: Optional[str] = Field(default=None, max_length=40)


@router.post("/catalogue-worker/claim", dependencies=[Depends(_worker)])
def worker_claim(body: ClaimRequest) -> dict:
    """Marks the worker seen, puts running jobs that went quiet back to
    pending, and hands out the oldest pending job (now running for this
    worker), or none. poll_seconds: when to ask again with nothing to do."""
    catalogue.mark_seen(body.worker, body.version)
    db = SessionLocal()
    try:
        # A write first, so the whole claim is one write transaction.
        catalogue.requeue_stale(db)
        job = catalogue.claim_next(db, body.worker)
        db.commit()
        return {"job": catalogue.job_for_worker(job) if job else None, "poll_seconds": catalogue.POLL_SECONDS}
    finally:
        db.close()


class ProgressReport(BaseModel):
    done_parts: int = Field(default=0, ge=0, le=10_000)
    total_parts: int = Field(default=0, ge=0, le=10_000)
    message: Optional[str] = Field(default=None, max_length=catalogue.MAX_TEXT)


@router.post("/catalogue-worker/jobs/{job_id}/progress", dependencies=[Depends(_worker)])
def worker_progress(job_id: int, body: ProgressReport) -> dict:
    """How far the worker got. cancelled: true when the job is no longer
    running -- a person cancelled it, or it ended or went back to the queue
    some other way -- and the worker should stop."""
    db = SessionLocal()
    try:
        job = catalogue.record_progress(db, job_id, body.done_parts, body.total_parts, body.message)
        worker = job.claimed_by if job else None
        db.commit()
    finally:
        db.close()
    catalogue.mark_seen(worker)
    return {"cancelled": job is None}


@router.post("/catalogue-worker/jobs/{job_id}/result", dependencies=[Depends(_worker)])
def worker_result(job_id: int, payload: dict = Body(...)) -> dict:
    """The worker's result (catalogue.WorkerResult). 409 job_not_running
    when the job was cancelled, has ended or doesn't exist: the worker just
    drops it. A result that fails the checks -- the caps, a link off the
    parts sites, a part the job never asked about -- ends the job as
    failed (so it doesn't come round again every 45 minutes) and answers
    422 bad_result."""
    try:
        result = catalogue.WorkerResult.model_validate(payload)
        problem = None
    except ValidationError as exc:
        result = None
        problem = "; ".join(
            f"{'.'.join(str(p) for p in err.get('loc', ()))}: {err.get('msg', '')}" for err in exc.errors()[:5]
        )
    db = SessionLocal()
    try:
        if result is None:
            if not catalogue.finish(db, job_id, "failed", error_code="internal",
                                    error_text=f"The worker's result was refused: {problem}"):
                raise ApiError(409, "This job is not running.", "job_not_running")
            db.commit()
            logger.warning("Catalogue job %s: result refused (%s)", job_id, problem)
            raise ApiError(422, f"Result refused: {problem}"[:catalogue.MAX_TEXT], "bad_result")

        # The conditional UPDATE first: it decides a race with a person's
        # cancel, and makes this one write transaction from the start.
        error_code = (result.error_code or "internal") if result.status == "failed" else None
        if not catalogue.finish(db, job_id, result.status, error_code=error_code, error_text=result.error_text):
            raise ApiError(409, "This job is not running.", "job_not_running")
        job = db.get(CatalogueJob, job_id)
        unknown = catalogue.unknown_parts(job, result)
        if unknown:
            job.status, job.error_code = "failed", "internal"
            job.error_text = f"The worker's result was refused: parts the job didn't ask about ({', '.join(unknown)})"[:catalogue.MAX_TEXT]
            db.commit()
            raise ApiError(422, job.error_text, "bad_result")
        stored = catalogue.store_result(db, job, result)
        db.commit()
        logger.info("Catalogue job %s %s: %d suggestion(s) stored", job_id, result.status, stored)
        return {"ok": True, "suggestions": stored}
    finally:
        db.close()
