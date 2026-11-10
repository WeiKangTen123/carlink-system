import asyncio
import json
import logging
import re
import shutil
import uuid
from typing import Literal, Optional
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import defer
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

# The api container runs `uvicorn app.api.main:app`, which gives a handler
# only to its own loggers ("uvicorn", "uvicorn.access"; neither passes lines
# on to the root), so the app's INFO lines went nowhere and its errors came
# out bare. One root handler for everything else: uvicorn's lines aren't
# printed twice. Does nothing if app/main.py has set logging up already.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
# httpx logs every request URL at INFO, and a Telegram Bot API URL carries
# the bot token (POST /setup/test/telegram): see app/main.py.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from app.ai import keys as own_keys
from app.ai import runs as run_log
from app.ai import secretbox
from app.ai.extraction import draft_report
from app.ai.keys import AiActor, AiUnavailable
from app.api import limits
from app.api.ai_allowance import router as ai_allowance_router
from app.api.ai_keys import router as ai_keys_router
from app.api.ai_runs import router as ai_usage_router
from app.api.annexes import router as annexes_router
from app.api.auth import case_recipient, recipient_still_active, router as auth_router
from app.api.inbox import clear_stale_run, router as inbox_router
from app.api.parts import price_book_for_case, router as parts_router
from app.api.signup import router as signup_router
from app.api.telegram_link import router as telegram_link_router
from app.api.intake import MAX_PHOTOS_PER_CASE, delete_intake_files, router as intake_router
from app.auth import chat_links
from app.auth.core import CurrentUser, audit, reopened_cases, report_history
from app.auth.deps import (
    CASE_NOT_FOUND,
    ApiError,
    can_view_file,
    current_user,
    editable_case,
    not_allowed,
    require_admin,
    visible_case,
)
from app.auth.middleware import LOCAL_DEV_ORIGIN, AuthMiddleware
from app.auth.passwords import HashingBusy
from app.auth.permissions import (
    PUBLIC_CHANNEL,
    can_assign,
    can_delete_case,
    can_reopen,
    can_sign_off,
    can_view_all_cases,
    in_inbox,
    is_public,
    is_staff,
    list_scope,
)
from app.channels.whatsapp import router as whatsapp_router
from app.config import settings
from app.intake import pipeline, public
from app.intake.cleanup import draft_deletes_at, last_photo_at
from app.intake.photos import first_photo_paths, remove_intake_dir
from app.reports import owners
from app.parts.prefill import item_key, item_source, sync_annex_a, unpriced_rows
from app.reports.db import SessionLocal, init_db
from app.reports.filing import PHOTO_INDEX_KEYS, ai_read_for
from app.reports.review import open_items, open_summary, review_state
from app.intake.photos import photo_label
from app.ai.client import get_client, reset_client_cache
from app.reports.models import ApiKey, AppSetting, IntakeSession, Report, User, gen_id, iso_utc
from app.reports.taxonomy import (
    BODY_TYPES,
    CANONICAL_DAMAGE_TYPES,
    CANONICAL_PARTS,
    CANONICAL_SEVERITIES,
    FIRM_CONDITION_COMMON,
    FIRM_CONDITION_TOKENS,
    FIRM_REMARKS,
    FIRM_WHOLE_CONDITIONS,
    MODEL_BODY_TYPES,
    OTHER_PART,
    body_type_for,
    clean_part_name,
    damage_type_from_tokens,
    firm_condition,
    parse_condition,
)
from app.reports.schema import DamageSummaryItem, SecurityIncidentDraft, SignOffInfo
from app.rendering.renderer import RenderBusy, render_pdf
from app.storage.files import (
    generate_preview,
    generate_thumbnail,
    remove_incident_dirs,
    report_pdf_path,
    save_photo,
    tmp_dir,
    to_preview_url,
    to_public_url,
    to_thumbnail_url,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Own AI keys: this process is the one that makes the secret if there
    # isn't one, and seals the canary the bot checks (ai/secretbox.py).
    secretbox.startup(create=True)
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    if settings.run_background_cleanup:
        from app.intake.cleanup import start_background_cleanup

        start_background_cleanup()
    yield


# The API docs list every route to anyone signed in, which with open signup
# is anyone. Off unless ENABLE_API_DOCS=true; app.openapi() works either way.
_docs = settings.enable_api_docs
app = FastAPI(
    title="Carlink Bot Service API",
    lifespan=lifespan,
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)
# Every request needs a signed-in user (app/auth/middleware.py), except
# /health and signing in itself. Added before CORS so CORS wraps it: a 401
# still carries CORS headers in local dev instead of looking like a network
# error.
app.add_middleware(AuthMiddleware)
# CORS is only needed in local development, where the dashboard (:3000) and
# this API (:8000) are different origins. In production the browser reaches
# the API same-origin through nginx (/api/), so no other origin is allowed:
# the previous pattern matched any host merely containing "carlink" or
# "sslip.io", which with credentials allowed would let such a site read the
# API as the signed-in user.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=LOCAL_DEV_ORIGIN.pattern,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# The WhatsApp webhook is unauthenticated (no Twilio signature check) and
# fetches a caller-supplied media URL server-side, so it is only exposed on
# a deployment that actually uses WhatsApp. (Behind AuthMiddleware it would
# now refuse Twilio anyway; it needs a signature check before it can be
# made public again.)
if settings.twilio_account_sid and settings.twilio_auth_token:
    app.include_router(whatsapp_router)
app.include_router(auth_router)
app.include_router(signup_router)
app.include_router(parts_router)
app.include_router(annexes_router)
app.include_router(intake_router)
app.include_router(ai_keys_router)
app.include_router(telegram_link_router)
app.include_router(inbox_router)
app.include_router(ai_usage_router)
app.include_router(ai_allowance_router)


@app.exception_handler(HashingBusy)
async def _hashing_busy(request: Request, exc: HashingBusy):
    return JSONResponse({"detail": "The server is busy. Try again in a moment."}, status_code=503)


@app.exception_handler(ApiError)
async def _api_error(request: Request, exc: ApiError):
    return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(AiUnavailable)
async def _ai_unavailable(request: Request, exc: AiUnavailable):
    """The person's AI isn't available (ai/keys.py): no key of theirs works
    and they may not use the company's. Same shape as ApiError."""
    return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=409)


def blank_sign_off() -> dict:
    """data["sign_off"] for a case nobody has signed. Only /sign-off and
    /reopen write sign_off: every other save keeps the stored one (or this),
    whatever the request carries -- otherwise a case could be saved as
    "Signed Off" by anyone, under any name."""
    return SignOffInfo().model_dump()

# The only parts of storage_dir that are meant to be downloadable: case
# photos/PDFs and in-progress intake photos. storage_dir also holds the
# SQLite database (carlink.db, -wal, -shm), tmp uploads and any backups, and
# the mount used to publish all of it -- the live database was downloadable
# at /files/carlink.db until 2026-10-21. nginx enforces the same allowlist.
_PUBLIC_FILE_PREFIXES = ("incidents/", "intake/")


class _ImmutableStaticFiles(StaticFiles):
    """Serves /files with long-lived caching and on-the-fly preview/thumbnail generation.

    Stored photos and generated PDFs are write-once at a given path -- a
    report's photo_00.jpg is never rewritten in place -- so they can be
    cached hard. Missing preview/thumbnail files are dynamically generated
    on first request so existing cases get high-speed previews automatically.
    """

    async def get_response(self, path: str, scope):
        # get_path() has already normalized the path, so ".." segments are
        # resolved before this check rather than slipping past it.
        if not path.replace("\\", "/").startswith(_PUBLIC_FILE_PREFIXES):
            raise HTTPException(status_code=404)
        # Only people who may see the case get its files, and anyone else
        # the same "not found" as for a file that doesn't exist. Checked
        # before super(), whose lookup_path would otherwise make a missing
        # thumbnail or preview for whoever asked. (Here rather than in a
        # middleware: only this sees the path after ".." is resolved.)
        user = scope.get("state", {}).get("user")
        if user is None or not await run_in_threadpool(can_view_file, user, path):
            raise HTTPException(status_code=404)
        resp = await super().get_response(path, scope)
        if path.replace("\\", "/").startswith("intake/"):
            # A draft's photos (a public report's among them) go when it is
            # discarded, withdrawn or swept: a browser keeps them an hour at
            # most, not a year (review L4).
            resp.headers["Cache-Control"] = "private, max-age=3600"
        return resp

    def lookup_path(self, path: str):
        full_path, stat_result = super().lookup_path(path)
        if stat_result is None and self.directory:
            p = Path(self.directory) / path
            if p.name.endswith("_preview.webp"):
                stem = p.stem.replace("_preview", "")
                for ext in (".jpg", ".jpeg", ".png", ".webp", ".JPG", ".JPEG", ".PNG"):
                    orig = p.parent / f"{stem}{ext}"
                    if orig.exists():
                        generate_preview(str(orig))
                        return super().lookup_path(path)
            elif p.name.endswith("_thumb.jpg"):
                stem = p.stem.replace("_thumb", "")
                for ext in (".jpg", ".jpeg", ".png", ".webp", ".JPG", ".JPEG", ".PNG"):
                    orig = p.parent / f"{stem}{ext}"
                    if orig.exists():
                        generate_thumbnail(str(orig))
                        return super().lookup_path(path)
        return full_path, stat_result

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        # "private": case photos need a sign-in, so shared caches must not
        # keep copies (nginx sets the same on /files/; /api/files/ gets this).
        resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        return resp


Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
app.mount("/files", _ImmutableStaticFiles(directory=settings.storage_dir), name="files")


def _refuse_intake_draft(r: Report) -> None:
    """Unfinished intakes (status "intake") are only handled through
    /intake. Run through these older endpoints, a draft got signed off or
    reopened with its photos still in storage/intake/ (never attached, and
    leaked on delete), got a PDF showing raw "@<photo id>" references, or
    had invented quantum written into it by AI Auto-Map."""
    if r.status == "intake":
        raise HTTPException(
            status_code=409,
            detail="This is an unfinished intake -- finish it in New Intake first.",
        )


Scope = Optional[Literal["mine", "all", "unassigned"]]


def _scoped(query, user: CurrentUser, requested: str | None):
    """Narrows a Report query to the cases a list covers for this user
    (permissions.list_scope): their own, every case, or the Unassigned
    ones. Lists, counts and charts all go through this. Never public
    reports nobody has taken: those are the Public Inbox's (api/inbox.py),
    and a NULL channel on an older row still lists (review H2)."""
    scope = list_scope(user, requested)
    if scope is None:
        raise ApiError(403, "You can only list your own cases.", "scope_not_allowed")
    query = query.filter(public.not_untaken_clause())
    if scope == "mine":
        return query.filter(Report.owner_id == user.id)
    if scope == "unassigned":
        return query.filter(Report.owner_id.is_(None))
    return query


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _attention_fields(
    r: Report, data: dict, session: IntakeSession | None, last_photo: datetime | None, reopened: bool, now: datetime
) -> dict:
    """What GET /reports adds for the Overview's "Needs attention" list.
    Never the run's free-text error or whose run it was (both may name a
    person or quote Google), and nothing from a public sender's details.

    intake_stage: a draft's stage as the wizard shows it -- a run or a
    filing a restart cut off is "failed", the filing one with error code
    filing_interrupted (pipeline.shown_state); None for a filed case, or a
    draft with no progress row. intake_error_code: that code (ai/keys'
    AiUnavailable codes, filing_interrupted) when the stage is failed.
    draft_deletes_at: when the idle sweep would delete the draft if
    nobody works on it (cleanup.draft_deletes_at, the sweep's own rule);
    None while a run or filing is under way, and for a filed case.
    unpriced_rows: a filed case's Annex A lines with no estimate yet, stale
    ones left out (prefill.unpriced_rows). reopened: signed off once,
    reopened since, not signed off again (core.reopened_cases).
    needs_you / to_check: a filed case's field review (app/reports/
    review.py) -- the items only a person can fill in that are still
    empty (unpriced Annex A lines among them), and the AI guesses, usual
    wordings and past-case prices nobody has confirmed; 0 for a draft
    (the wizard shows its own) and for a signed-off case. From the row
    as loaded (data and ai_draft): no query of its own."""
    if r.status != "intake":
        return {
            "intake_stage": None,
            "intake_error_code": None,
            "draft_deletes_at": None,
            "unpriced_rows": unpriced_rows(data),
            "reopened": reopened,
            **_review_totals(r, data),
        }
    stage = error_code = deletes_at = None
    if session is not None:
        stage, _error, error_code, _error_by = pipeline.shown_state(session, now)
        deletes_at = draft_deletes_at(session, r, last_photo, now)
    return {
        "intake_stage": stage,
        "intake_error_code": error_code if stage == "failed" else None,
        "draft_deletes_at": iso_utc(deletes_at) if deletes_at else None,
        "unpriced_rows": 0,
        "reopened": False,
        "needs_you": 0,
        "to_check": 0,
    }


def _case_review(r: Report) -> dict:
    """review.review_state for a filed case, from its own row."""
    data = r.data or {}
    return review_state(data, r.ai_draft, data.get("photo_index"), signed_off=_is_signed_off(r))


def _review_totals(r: Report, data: dict) -> dict:
    """GET /reports' needs_you / to_check. A case whose data can't be read
    as a review counts 0 rather than failing the whole list."""
    try:
        needs, check = open_items(_case_review(r)["counts"])
    except Exception:
        logger.exception("Could not work out the field review of case %s for the list", r.id)
        needs = check = 0
    return {"needs_you": needs, "to_check": check}


@app.get("/reports")
def list_reports(scope: Scope = None, user: CurrentUser = Depends(current_user)) -> list[dict]:
    """?scope=mine|all|unassigned. Plain users only ever get their own
    cases; "see all" staff default to their own, admins to all."""
    db = SessionLocal()
    try:
        reports = _scoped(db.query(Report), user, scope).order_by(Report.created_at.desc()).all()
        owner_rows = owners.owners_by_id(db, {r.owner_id for r in reports})
        # TG-... of the reports the public sent, in one query.
        references = public.references_for(db, [r.id for r in reports if r.channel == PUBLIC_CHANNEL])
        # What the Overview's "Needs attention" reads, a query each for the
        # whole list, never one per case: drafts' first photos and progress
        # (the AI draft left unloaded: nothing here reads it), when the idle
        # sweep would delete them, and which filed cases were reopened.
        draft_ids = [r.id for r in reports if r.status == "intake"]
        first_photos = first_photo_paths(db, draft_ids)
        sessions = {}
        if draft_ids:
            query = db.query(IntakeSession).options(defer(IntakeSession.ai_draft))
            sessions = {s.report_id: s for s in query.filter(IntakeSession.report_id.in_(draft_ids))}
        last_photos = last_photo_at(db, draft_ids)
        reopened = reopened_cases(db, [r.id for r in reports if r.status != "intake"])
        now = datetime.now(timezone.utc)
        result = []
        for r in reports:
            data = r.data or {}
            # Same make+model-else-vehicle_details fallback StudioApp.tsx
            # already uses for its own header -- added because the
            # dashboard's list views only had `category` to show per case,
            # and every real report here has the same category ("Vehicle
            # Collision or Damage"), making every row look identical.
            vehicle_info = data.get("vehicle_info") or {}
            raw_vname = " ".join(filter(None, [vehicle_info.get("make"), vehicle_info.get("model")])) or data.get("vehicle_details")
            thumbnail_url = to_thumbnail_url(r.photo_paths[0]) if r.photo_paths else None
            if r.status == "intake":
                # An unfinished intake: photos are still in storage/intake/
                # (see api/intake.py), and the guesses below would label a
                # draft nobody has described yet as some specific car.
                raw_vname = raw_vname or "New intake (in progress)"
                thumbnail_url = to_thumbnail_url(first_photos[r.id]) if r.id in first_photos else None
            elif not raw_vname or raw_vname.strip() in ("", "None", "None None"):
                # Unrecorded stays unrecorded (the dashboard shows "Vehicle
                # not recorded"). This used to guess from description words
                # and plate prefixes ("SLJ..." -> Honda Civic 1.6), falling
                # back to "Passenger Sedan".
                raw_vname = None
            vehicle_name = raw_vname

            # Same damage_summary-else-damaged_parts fallback used in the
            # dashboard and get_analytics_summary -- lets the Overview
            # page's priority queue rank and label cases without having to
            # fetch every full report just to read two fields.
            damage_items = data.get("damage_summary") or data.get("damaged_parts") or []
            insurance = data.get("insurance_details") or {}
            recommendations = data.get("recommendations") or {}
            police_report = data.get("police_report") or {}
            result.append({
                "id": r.id,
                "type": r.type,
                "status": r.status,
                "channel": r.channel,
                "created_at": iso_utc(r.created_at),
                "location": data.get("location"),
                "category": data.get("category"),
                "thumbnail_url": thumbnail_url,
                "plate_number": vehicle_info.get("plate_number"),
                "vehicle_name": vehicle_name,
                "severity_level": data.get("severity_level"),
                "damage_count": len(damage_items),
                "accident_type": data.get("accident_type"),
                "incident_datetime": data.get("incident_datetime"),
                "estimated_repair_cost": insurance.get("estimated_repair_cost"),
                "final_approved_cost": insurance.get("final_approved_cost"),
                "insurer_name": insurance.get("insurer_name"),
                "workshop_assigned": insurance.get("workshop_assigned"),
                "claim_type": insurance.get("claim_type"),
                "disassembly_required": recommendations.get("disassembly_required"),
                "reported_to_police": police_report.get("reported_to_police"),
                "owner": owners.owner_dict(owner_rows.get(r.owner_id)),
                "public_reference": references.get(r.id),
                # Our Ref (None until staff issue one) and who asked for the
                # report, as the cover page records them -- from the case
                # data only, never from a public sender's details.
                "report_id": data.get("report_id") or None,
                "requested_by": (data.get("cover_page") or {}).get("requested_by") or None,
                **_attention_fields(r, data, sessions.get(r.id), last_photos.get(r.id), r.id in reopened, now),
            })
        return result
    finally:
        db.close()


@app.get("/reports/{report_id}")
def get_report(report_id: str, user: CurrentUser = Depends(visible_case)) -> dict:
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
        # Owner, what this user may do (the buttons follow it), and for an
        # admin on an Unassigned case, who it should probably go to. On a
        # report from the public, who sent it (else None).
        access = {**owners.access_fields(db, r, user), "public_submission": public.submission_info(db, r)}

        if r.status == "intake":
            # Unfinished intake -- the dashboard sends these to the intake
            # wizard (GET /intake/{id}) instead. Returned bare: synthesizing
            # annexes and rendering a PDF for a draft with no confirmed facts
            # yet would be wasted work and persist made-up quantum on it.
            return {
                "id": r.id,
                "type": r.type,
                "status": r.status,
                "channel": r.channel,
                "data": r.data,
                "photo_urls": [],
                "photo_thumb_urls": [],
                "photo_preview_urls": [],
                "pdf_url": None,
                "created_at": iso_utc(r.created_at),
                "ai_read": None,  # the wizard reads the draft's AI values from GET /intake/{id}
                "ai_read_photos": {},
                "review": None,  # and its review
                **access,
            }

        # Opening a case used to "ensure" annexes by synthesizing and saving
        # them -- a plain GET wrote invented quantum into the case. Annexes
        # now only change through the surveyor's own edits or the explicit
        # "fill derived fields" action (POST /reports/{id}/ai-enrich).

        # If PDF is missing or doesn't exist on disk, render it on-demand --
        # unless this person may not render right now (limits.render_turn)
        # or no Chromium slot is free this moment (slot_wait=0: opening a
        # case never waits for one). The case still opens, without a PDF
        # until later.
        pdf_path = r.pdf_path
        if not pdf_path or not Path(pdf_path).exists():
            try:
                with limits.render_turn(user):
                    target_pdf_path = report_pdf_path(r.id)
                    render_pdf(r.data or {}, r.photo_paths or [], target_pdf_path, report_id=r.id, slot_wait=0)
                r.pdf_path = target_pdf_path
                db.commit()
            except (limits.RenderRefused, RenderBusy):
                r.pdf_path = None
            except Exception as exc:
                logger.warning("Failed to render PDF on-demand for report %s: %s", r.id, exc)
                r.pdf_path = None

        existing_photos = [p for p in (r.photo_paths or []) if Path(p).exists()]
        photo_urls = [to_public_url(p) for p in existing_photos]
        photo_thumb_urls = [to_thumbnail_url(p) for p in existing_photos]
        photo_preview_urls = [to_preview_url(p) for p in existing_photos]
        # What the AI read for the cover fields, and from which photo, so
        # the Cover tab can tag a field whose value is still the AI's. From
        # Report.ai_draft alone; never printed, never in data.
        ai_read, ai_read_photos = ai_read_for(r.ai_draft, (r.data or {}).get("photo_index"))

        return {
            "id": r.id,
            "type": r.type,
            "status": r.status,
            "channel": r.channel,
            "data": r.data,
            "photo_urls": photo_urls,
            "photo_thumb_urls": photo_thumb_urls,
            "photo_preview_urls": photo_preview_urls,
            "pdf_url": to_public_url(r.pdf_path) if r.pdf_path and Path(r.pdf_path).exists() else None,
            "created_at": iso_utc(r.created_at),
            # Who created, edited, signed off, reopened and assigned it.
            "history": report_history(db, r),
            "ai_read": ai_read,
            "ai_read_photos": ai_read_photos,
            # Field review (app/reports/review.py): per field, damage item
            # and Annex row whether the AI read it, it is to check, it needs
            # a person, or a person confirmed it; with counts per section.
            "review": _case_review(r),
            # What the AI did on this case and its draft (app/ai/runs.py),
            # newest first: the Case record drawer's list, and tab 1's "AI
            # read 14 of 41 photos" line (null when the AI never read it).
            "ai_runs": run_log.summaries(db, r.id, viewer=user),
            "ai_last_read": run_log.last_read(db, r.id, len(r.photo_paths or [])),
            **access,
        }
    finally:
        db.close()


def _audit_export(db, user: CurrentUser, r: Report, detail: str) -> None:
    """History entry "exported" (pdf | pdf-no-photos | xlsx): the case left
    the server as a file. Best effort: a busy database must not stop the
    download the person asked for."""
    try:
        audit(db, user, "exported", r.id, detail=detail)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("Couldn't record the %s export of report %s: %s", detail, r.id, type(exc).__name__)


def _report_filename(r: Report, ext: str, suffix: str = "") -> str:
    """"<Our Ref or REF-xxxxxx> <plate>.<ext>": the firm names its report
    files by reference and plate ("CL 12330 SMV 7530 X.xls"). REF- and the
    case id stand in while no Our Ref is issued -- never a made-up CL
    number. Characters a file name can't carry become "-"."""
    data = r.data or {}
    ref = (data.get("report_id") or "").strip() or f"REF-{r.id[:6].upper()}"
    plate = ((data.get("vehicle_info") or {}).get("plate_number") or "").strip()
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", " ".join(p for p in (ref, plate) if p) + suffix).strip(" .")
    return f"{name}.{ext}"


_NO_STORE = {"Cache-Control": "no-store"}


@app.get("/reports/{report_id}/download")
@app.head("/reports/{report_id}/download")
def download_report_pdf(request: Request, report_id: str, photos: int = 1, user: CurrentUser = Depends(visible_case)):
    """The report PDF. ?photos=0: the same pages without the photo
    appendix, rendered now into "<id>_nophotos.pdf" and never kept on
    r.pdf_path -- that is the full report, the one every save invalidates.
    A GET is an export in the case history (HEAD only asks)."""
    from fastapi.responses import FileResponse
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
        _refuse_intake_draft(r)

        if photos == 0:
            with limits.render_turn(user):
                out = Path(report_pdf_path(r.id)).with_name(f"{r.id}_nophotos.pdf")
                # Rendered beside the final name and swapped in whole: two
                # people asking at once must not read each other's half file.
                tmp = out.with_name(f"{out.stem}.{uuid.uuid4().hex[:8]}.part.pdf")
                nophotos_path = str(out)
                try:
                    render_pdf(r.data or {}, r.photo_paths or [], str(tmp), report_id=r.id, include_photos=False)
                    tmp.replace(out)
                except RenderBusy as exc:
                    raise limits.render_busy() from exc
                except Exception as exc:
                    logger.error("Failed to render the no-photos PDF for report %s: %s", r.id, exc)
                    raise HTTPException(status_code=503, detail="PDF generation currently unavailable.") from exc
            if request.method == "GET":
                _audit_export(db, user, r, "pdf-no-photos")
            return FileResponse(
                path=nophotos_path, filename=_report_filename(r, "pdf", " (no photos)"), media_type="application/pdf", headers=_NO_STORE
            )

        pdf_path = r.pdf_path
        if not pdf_path or not Path(pdf_path).exists():
            # 429 render_busy / render_limit if this person may not render now.
            with limits.render_turn(user):
                target_pdf_path = report_pdf_path(r.id)
                try:
                    render_pdf(r.data or {}, r.photo_paths or [], target_pdf_path, report_id=r.id)
                except RenderBusy as exc:
                    raise limits.render_busy() from exc
                except Exception as exc:
                    logger.error("Failed to render PDF for report %s download: %s", r.id, exc)
                    raise HTTPException(status_code=503, detail="PDF generation currently unavailable.") from exc
            r.pdf_path = target_pdf_path
            db.commit()

        if not r.pdf_path or not Path(r.pdf_path).exists():
            raise HTTPException(status_code=503, detail="PDF generation currently unavailable.")

        if request.method == "GET":
            _audit_export(db, user, r, "pdf")
        return FileResponse(
            path=r.pdf_path,
            filename=_report_filename(r, "pdf"),
            media_type="application/pdf",
            # Not kept by the browser: a cached copy outlives signing out and
            # losing access to the case (reassigned, account unticked).
            headers=_NO_STORE,
        )
    finally:
        db.close()


@app.get("/reports/{report_id}/download.xlsx")
def download_report_xlsx(report_id: str, user: CurrentUser = Depends(visible_case)):
    """The report as the firm's four-sheet workbook (Cover Page, Annex A,
    Annex B, Annex C -- rendering/xlsx_export.py), made now from the case
    data. openpyxl only, no Chromium: cheap enough that nothing is kept."""
    from fastapi.responses import FileResponse

    # Imported here so the API still starts, and every other route works,
    # if the workbook exporter can't be imported on a deployment.
    from app.rendering.xlsx_export import render_xlsx

    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
        _refuse_intake_draft(r)
        out = Path(report_pdf_path(r.id)).with_name(f"{r.id}.xlsx")
        # Written beside the final name and swapped in whole (see ?photos=0).
        tmp = out.with_name(f"{out.stem}.{uuid.uuid4().hex[:8]}.part.xlsx")
        try:
            render_xlsx(r.data or {}, str(tmp), report_id=r.id)
            tmp.replace(out)
        except Exception as exc:
            logger.error("Failed to make the Excel report for %s: %s", r.id, exc)
            raise HTTPException(status_code=503, detail="Excel export currently unavailable.") from exc
        _audit_export(db, user, r, "xlsx")
        return FileResponse(
            path=str(out),
            filename=_report_filename(r, "xlsx"),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=_NO_STORE,
        )
    finally:
        db.close()


@app.post("/reports/{report_id}/render-pdf")
def render_report_pdf_endpoint(report_id: str, user: CurrentUser = Depends(editable_case)) -> dict:
    """Explicitly triggers on-demand re-rendering of the official incident report PDF."""
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
        _refuse_intake_draft(r)

        # 429 render_busy / render_limit if this person may not render now.
        with limits.render_turn(user):
            target_pdf_path = report_pdf_path(r.id)
            try:
                render_pdf(r.data or {}, r.photo_paths or [], target_pdf_path, report_id=r.id)
            except RenderBusy as exc:
                raise limits.render_busy() from exc
            except Exception as exc:
                logger.error("Failed to render PDF for report %s on-demand: %s", r.id, exc)
                raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}") from exc
        r.pdf_path = target_pdf_path
        db.commit()
        return {
            "id": r.id,
            "status": "rendered",
            "pdf_url": to_public_url(r.pdf_path),
        }
    finally:
        db.close()


ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAX_PHOTO_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


def _temp_prefix(user: CurrentUser) -> str:
    """Dashboard uploads in storage/tmp are named u{user id}-{random}, so
    only the person who uploaded one can attach it to a case
    (_checked_temp_paths). The Telegram bot's are tg-{random}."""
    return f"u{user.id}-"


def _analysis_is_public(report_id: str, user: CurrentUser) -> bool:
    """analyze-photos for one case (its edit form): editable_case's answers
    -- 404 unless the user may see it, 403 not_allowed unless they may
    change it (an untaken public report: no one) -- then whether the public
    sent it (P6, filed ones too), whose AI takes the stricter key rule
    (ai/keys.resolve_keys, public_case)."""
    editable_case(report_id, user)
    db = SessionLocal()
    try:
        channel = db.query(Report.channel).filter(Report.id == report_id).scalar()
    finally:
        db.close()
    return channel == PUBLIC_CHANNEL


@app.post("/reports/analyze-photos")
async def analyze_report_photos(
    description: str = Form(""),
    photos: list[UploadFile] = File(default=[]),
    report_id: Optional[str] = Form(None),
    user: CurrentUser = Depends(current_user),
) -> dict:
    """Dashboard equivalent of the Telegram bot's photo+text drafting step:
    saves the uploaded photos to a temp dir, runs the same Gemini drafting
    call used by the bot, and returns the draft for the reporter to review
    and edit -- nothing is saved to the database yet. The returned
    temp_photo_paths get passed back in on POST /reports if the reporter
    goes on to save the report, so the photos are only ever attached to a
    report the reporter actually confirmed.

    report_id: the case the edit form is for (its Re-analyze, or photos
    added to it). Gated as the other routes about one case (404, 403), and
    a case the public sent takes only the presser's own billed key until
    company_key_billed (409 ai_key_missing). Left out by the New Report form.
    """
    # The case first, when there is one (the photos still go to it later,
    # through PUT /reports/{id}'s checks). Then AI (the presser pays: 409
    # {detail, code} when theirs isn't available; one request at a time,
    # 429 ai_busy), then room on the disk, the file types and the hourly
    # photo count, before any photo is saved.
    public_case = await asyncio.to_thread(_analysis_is_public, report_id, user) if report_id else False
    actor = AiActor.for_user(user.id, "dashboard", public_case=public_case)
    await asyncio.to_thread(own_keys.ensure_ai_ready, actor)
    limits.claim_ai_turn(user)
    try:
        limits.require_disk_space()
        exts = _photo_exts(photos)
        counted = limits.charge_photo_uploads(user, len(photos))
        saved_paths: list[str] = []
        try:
            saved_paths = await asyncio.to_thread(_save_temp_photos, photos, exts, user)
            draft = await asyncio.to_thread(draft_report, description, saved_paths, actor=actor)
            if draft.vehicle_info:
                draft.vehicle_info.body_type = body_type_for(draft.vehicle_info.make, draft.vehicle_info.model, draft.vehicle_info.body_type)
        except BaseException as exc:
            # Nothing was drafted, so nothing is kept: the photos go, and
            # back to the hourly count.
            for p in saved_paths:
                Path(p).unlink(missing_ok=True)
            if counted:
                limits.photo_uploads.refund(user.id, len(photos))
            if isinstance(exc, (AiUnavailable, HTTPException)) or not isinstance(exc, Exception):
                raise  # their own key refused mid-call (the 409 with its code), a 413...
            raise HTTPException(status_code=502, detail=f"AI drafting failed: {own_keys.scrub(str(exc))}") from exc
    finally:
        limits.ai_running.release(user.id)

    return {"draft": draft.model_dump(), "temp_photo_paths": saved_paths}


def _photo_exts(photos: list[UploadFile]) -> list[str]:
    """Each photo's extension, all checked before any is saved: these
    extensions, none meaning .jpg (400 otherwise)."""
    exts: list[str] = []
    for photo in photos:
        raw_ext = Path(photo.filename or "").suffix.lower()
        if raw_ext and raw_ext not in ALLOWED_PHOTO_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file format '{raw_ext}'. Allowed formats: {', '.join(sorted(ALLOWED_PHOTO_EXTENSIONS))}",
            )
        exts.append(raw_ext or ".jpg")
    return exts


def _save_temp_photos(photos: list[UploadFile], exts: list[str], user: CurrentUser) -> list[str]:
    """Saves them to storage/tmp as u{user id}-{random}{ext} (_temp_prefix),
    25MB each at most (413) -- all or none."""
    saved_paths: list[str] = []
    try:
        td = tmp_dir()
        for photo, ext in zip(photos, exts):
            dest = td / f"{_temp_prefix(user)}{uuid.uuid4().hex}{ext}"
            with open(dest, "wb") as f:
                shutil.copyfileobj(photo.file, f)
            saved_paths.append(str(dest))
            if dest.stat().st_size > MAX_PHOTO_SIZE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"File '{photo.filename}' exceeds maximum allowed size of 25MB.",
                )
    except BaseException:
        for p in saved_paths:
            Path(p).unlink(missing_ok=True)
        raise
    return saved_paths


def _too_many_photos(already: int, adding: int) -> None:
    """A case holds at most MAX_PHOTOS_PER_CASE, as an intake does: 409.
    Only when photos are being added: a case from before the limit with
    more still saves its other edits."""
    if adding > 0 and already + adding > MAX_PHOTOS_PER_CASE:
        raise HTTPException(
            status_code=409,
            detail=f"A case can hold at most {MAX_PHOTOS_PER_CASE} photos, and this one has {already}. "
            f"Add at most {max(0, MAX_PHOTOS_PER_CASE - already)} more.",
        )


@app.post("/reports/{report_id}/upload-photos")
def upload_report_photos(
    report_id: str,
    photos: list[UploadFile] = File(default=[]),
    user: CurrentUser = Depends(editable_case),
) -> dict:
    """Photos for a case's edit form, with no AI at all: saved to
    storage/tmp just as /reports/analyze-photos saves them, and attached
    when the form is saved (PUT /reports/{id}, temp_photo_paths). Since P4
    analyze-photos needs AI, and was the only way in, so someone with AI
    off couldn't add a photo to their own case.

    Plain `def`: FastAPI runs it in its threadpool, off the event loop."""
    limits.require_disk_space()
    if not photos:
        raise HTTPException(status_code=400, detail="Choose at least one photo.")
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
        # What PUT /reports/{id} would refuse to attach them to, refused
        # before anything is saved for it.
        _refuse_intake_draft(r)
        if _is_signed_off(r):
            raise HTTPException(status_code=409, detail="Report is signed off and locked. Reopen it before editing.")
        _too_many_photos(len(r.photo_paths or []), len(photos))
    finally:
        db.close()

    # analyze-photos' rules: these extensions (none means .jpg), 25MB each.
    exts = _photo_exts(photos)
    counted = limits.charge_photo_uploads(user, len(photos))
    try:
        saved_paths = _save_temp_photos(photos, exts, user)
    except BaseException:
        # All or nothing, and the photos given back to the hourly count.
        if counted:
            limits.photo_uploads.refund(user.id, len(photos))
        raise
    return {"temp_photo_paths": saved_paths}


class CreateReportRequest(BaseModel):
    draft: SecurityIncidentDraft
    temp_photo_paths: list[str] = []


def _checked_temp_paths(paths: list[str], user: CurrentUser) -> list[str]:
    """temp_photo_paths arrive from the browser, which only ever got them
    from /reports/analyze-photos or /reports/{id}/upload-photos -- so
    anything not directly inside storage/tmp is refused. Each path is
    copied into the public case folder and then deleted, so before this
    check one request could publish and
    delete any file the API can read: the SQLite database, the source code.
    Only the caller's own uploads (_temp_prefix) pass, or anyone could move
    someone else's photos -- a Telegram draft's, say -- into their case.
    """
    root = tmp_dir().resolve()
    prefix = _temp_prefix(user)
    checked: list[str] = []
    for raw in paths:
        resolved = Path(raw).resolve()
        if resolved.parent != root or not resolved.name.startswith(prefix):
            raise HTTPException(status_code=400, detail="Invalid temporary photo reference.")
        checked.append(str(resolved))
    return checked


@app.post("/reports")
def create_report(body: CreateReportRequest, user: CurrentUser = Depends(current_user)) -> dict:
    """Manual entry from the dashboard -- optionally with photos that were
    already analyzed via POST /reports/analyze-photos and are sitting in the
    temp dir, which get moved into the report's permanent storage here once
    the reporter actually confirms and saves. The case is the caller's.
    """
    temp_paths = _checked_temp_paths(body.temp_photo_paths, user)
    if body.draft.vehicle_info:
        v = body.draft.vehicle_info
        v.body_type = body_type_for(v.make, v.model, v.body_type)
    data = body.draft.model_dump()
    data["sign_off"] = blank_sign_off()
    data["report_id"] = limits.checked_our_ref(user, data.get("report_id"), None)
    limits.require_disk_space()
    db = SessionLocal()
    try:
        limits.check_new_cases_today(db, user)
    finally:
        db.close()

    # Photos and PDF first, the database row last, so no SQLite write lock
    # is held while Chromium renders (it used to flush the row first and
    # hold the lock for the whole render, failing other writers -- the
    # Telegram bot included -- with "database is locked").
    report_id = gen_id()
    photo_paths = [
        save_photo(report_id, temp_path, i)
        for i, temp_path in enumerate(temp_paths)
        if Path(temp_path).exists()
    ]
    # When this person may not render now (limits.render_turn) or every
    # Chromium slot stays busy, the first download renders it instead.
    pdf_path: str | None = None
    try:
        with limits.render_turn(user):
            pdf_path = report_pdf_path(report_id)
            # Not saved yet, so the renderer can't look its owner up.
            render_pdf(data, photo_paths, pdf_path, report_id=report_id, unverified=not is_staff(user))
    except (limits.RenderRefused, RenderBusy):
        pdf_path = None
    except Exception as exc:
        logger.error("Failed to render PDF for created report %s: %s", report_id, exc)
        pdf_path = None

    db = SessionLocal()
    try:
        db.add(Report(
            id=report_id,
            channel="manual",
            reporter_chat_id="dashboard",
            data=data,
            status="confirmed",
            photo_paths=photo_paths,
            pdf_path=pdf_path,
            owner_id=user.id,
        ))
        audit(db, user, "created", report_id)
        db.commit()
    finally:
        db.close()
    for temp_path in temp_paths:
        Path(temp_path).unlink(missing_ok=True)
    return {"id": report_id}


def _merge_into(base: dict, patch: dict) -> dict:
    """Recursively applies `patch` onto `base`: nested objects merge field
    by field, anything else (values, lists) is replaced."""
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _merge_into(dict(base[key]), value)
        else:
            base[key] = value
    return base


def _render_after_change(user: CurrentUser, r: Report, what: str) -> Optional[str]:
    """Renders case r's PDF again after a change (an edit, signing off,
    reopening) and returns the path to keep on it. None -- the next download
    renders it -- when this person may not render now (limits.render_turn)
    or every Chromium slot stayed busy: the old file shows the case from
    before the change. A render that fails keeps the old path, as before."""
    try:
        with limits.render_turn(user):
            pdf_path = r.pdf_path or report_pdf_path(r.id)
            render_pdf(r.data, r.photo_paths or [], pdf_path, report_id=r.id)
        return pdf_path
    except (limits.RenderRefused, RenderBusy):
        return None
    except Exception as exc:
        logger.error("Failed to render PDF for %s report %s: %s", what, r.id, exc)
        return r.pdf_path


@app.put("/reports/{report_id}")
def update_report(report_id: str, body: CreateReportRequest, user: CurrentUser = Depends(editable_case)) -> dict:
    temp_paths = _checked_temp_paths(body.temp_photo_paths, user)
    if temp_paths:
        limits.require_disk_space()
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
        _refuse_intake_draft(r)
        if _is_signed_off(r):
            raise HTTPException(
                status_code=409,
                detail="Report is signed off and locked. Reopen it before editing.",
            )
        _too_many_photos(len(r.photo_paths or []), len([p for p in temp_paths if Path(p).exists()]))

        if body.draft.vehicle_info:
            v = body.draft.vehicle_info
            v.body_type = body_type_for(v.make, v.model, v.body_type)

        # Only what the client actually sent replaces what's stored. The
        # dashboard's Edit form sends a partial draft (no annexes, liability
        # assessment, vehicle year...), and replacing the whole case with
        # it -- defaults filling every gap -- silently erased those.
        sent = body.draft.model_dump(exclude_unset=True)
        # The field review's marks change only through PUT .../checked, and
        # template_fields is the server's (app/reports/review.py).
        sent.pop("checked", None)
        sent.pop("template_fields", None)
        if "report_id" in sent:
            sent["report_id"] = limits.checked_our_ref(user, sent["report_id"], (r.data or {}).get("report_id"))
        data = _merge_into(dict(r.data or {}), sent)
        data["sign_off"] = (r.data or {}).get("sign_off") or blank_sign_off()
        r.data = data

        # New photos from this edit get appended after whatever's already
        # attached -- indexed starting from the current count, never from 0,
        # so a report that already has photo_00.jpg/photo_01.jpg doesn't get
        # a freshly-added photo overwriting one of them.
        existing_photos = r.photo_paths or []
        new_photos = [
            save_photo(r.id, temp_path, len(existing_photos) + i)
            for i, temp_path in enumerate(temp_paths)
            if Path(temp_path).exists()
        ]
        if new_photos:
            r.photo_paths = existing_photos + new_photos
            # photo_index is positional (P-number = position): the new photos
            # get entries with kind None, never sorted, so the labels keep
            # lining up with photo_paths.
            index = data.get("photo_index")
            if isinstance(index, list):
                r.data = {**data, "photo_index": index + [
                    {"label": photo_label(len(existing_photos) + i), **{key: None for key in PHOTO_INDEX_KEYS}}
                    for i in range(len(new_photos))
                ]}
        for temp_path in temp_paths:
            Path(temp_path).unlink(missing_ok=True)

        r.pdf_path = _render_after_change(user, r, "updated")

        audit(db, user, "edited", r.id)
        db.commit()
        pdf_url = to_public_url(r.pdf_path) if r.pdf_path and Path(r.pdf_path).exists() else None
        return {"id": r.id, "status": r.status, "pdf_url": pdf_url}
    finally:
        db.close()


class DamageItemReviewRequest(BaseModel):
    """What a surveyor changes on one detected part in tab 1 (Damage
    detection): the part, how it is damaged (type, and the condition and
    remarks Annex A prints), how badly, which photo shows it, the verified
    tick and the OEM part number. Everything optional, so
    a caller can tick the box without touching the rest; a key left out
    is left alone (exclude_unset), and photo_reference sent as null clears
    the photo."""
    part: Optional[str] = None
    # What an "Other / Not Listed" part is (schema.DamageSummaryItem.
    # other_part_name), its own Annex A row; null or "" clears it, and so
    # does any other part.
    other_part_name: Optional[str] = None
    damage_type: Optional[str] = None
    # The Annex A Condition in the firm's words ("Grazed/cut", or Necessary
    # / Serviceable / Repair) and the Remarks (Replace / Repair); null or ""
    # clears either. See _damage_item_fields.
    condition: Optional[str] = None
    remarks: Optional[str] = None
    severity: Optional[str] = None
    photo_reference: Optional[str] = None
    human_verified: Optional[bool] = None
    oem_part_number: Optional[str] = None


class DamageItemAddRequest(BaseModel):
    """A part the surveyor adds by hand (tab 1 "+ Add a part")."""
    part: str
    other_part_name: Optional[str] = None
    damage_type: Optional[str] = None
    condition: Optional[str] = None
    remarks: Optional[str] = None
    photo_reference: Optional[str] = None


_PHOTO_REF = re.compile(r"P(\d{2,3})")


def _damage_item_fields(sent: dict, r: Report) -> dict:
    """The sent fields checked against the taxonomy, so a damage item never
    carries a part or condition the Annex A, the 3D model and the
    analytics don't know. 400 says which value was refused."""
    out: dict = {}
    if "part" in sent:
        part = (sent["part"] or "").strip()
        if part not in CANONICAL_PARTS:
            raise HTTPException(status_code=400, detail=f"'{part}' isn't a part on the list.")
        out["part"] = part
    if "other_part_name" in sent:
        # As the reader's is cleaned (schema.DamageSummaryItem): one line,
        # at most taxonomy.MAX_OTHER_NAME characters, blank -> None.
        out["other_part_name"] = clean_part_name(sent["other_part_name"])
    if "damage_type" in sent:
        damage_type = (sent["damage_type"] or "").strip() or None
        if damage_type is not None and damage_type not in CANONICAL_DAMAGE_TYPES:
            raise HTTPException(status_code=400, detail=f"'{damage_type}' isn't a damage type on the list.")
        out["damage_type"] = damage_type
    if "condition" in sent:
        # Only the firm's words, so the printed Condition column and the
        # condition statistics stay in one vocabulary. condition_tokens
        # follows: the words of the condition, [] for a whole word.
        try:
            tokens, condition = parse_condition(sent["condition"])
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"'{exc}' isn't one of the firm's condition words ({', '.join(FIRM_CONDITION_TOKENS)}); "
                       f"or write {', '.join(FIRM_WHOLE_CONDITIONS[:-1])} or {FIRM_WHOLE_CONDITIONS[-1]} on its own.",
            )
        out["condition"], out["condition_tokens"] = condition, tokens
    if "remarks" in sent:
        remarks = (sent["remarks"] or "").strip() or None
        match = next((r for r in FIRM_REMARKS if remarks and r.lower() == remarks.lower()), None)
        if remarks is not None and match is None:
            raise HTTPException(status_code=400, detail=f"'{remarks}' isn't a remark on the list ({' or '.join(FIRM_REMARKS)}).")
        out["remarks"] = match
    if "severity" in sent:
        severity = (sent["severity"] or "").strip() or None
        if severity is not None and severity not in CANONICAL_SEVERITIES:
            raise HTTPException(status_code=400, detail=f"'{severity}' isn't a severity on the list.")
        out["severity"] = severity
    if "photo_reference" in sent:
        ref = (sent["photo_reference"] or "").strip().upper() or None
        if ref is not None:
            m = _PHOTO_REF.fullmatch(ref)
            n = len(r.photo_paths or [])
            if not m or not 1 <= int(m.group(1)) <= n:
                raise HTTPException(status_code=400, detail=f"'{ref}' isn't one of this case's {n} photo(s).")
        out["photo_reference"] = ref
    if sent.get("human_verified") is not None:
        out["human_verified"] = bool(sent["human_verified"])
    if sent.get("oem_part_number") is not None:
        # Empty string clears the field rather than storing "" -- keeps
        # "not entered" as a single honest null everywhere downstream.
        out["oem_part_number"] = sent["oem_part_number"].strip() or None
    return out


def _damage_case(db, report_id: str) -> Report:
    """The case a damage item is edited on: as PUT /annex-a, an unfinished
    intake (409: the wizard owns its damage list) and a signed-off case
    (409: reopen it first) are refused."""
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
    _refuse_intake_draft(r)
    if _is_signed_off(r):
        raise HTTPException(status_code=409, detail="Report is signed off and locked. Reopen it before editing.")
    return r


def _undismiss(data: dict, item: dict) -> None:
    """A part the surveyor names again is theirs again: it leaves
    dismissed_parts, so its Annex A rows come back on this sync. Keyed as
    sync_annex_a keys parts (prefill.item_key): a named "Other / Not
    Listed" part by its name."""
    key = item_key(item)
    data["dismissed_parts"] = [p for p in (data.get("dismissed_parts") or []) if item_key({"part": p}) != key]


def _save_damage_items(db, user: CurrentUser, r: Report, data: dict, damage_summary: list[dict], action: str, detail: str) -> dict:
    """Writes the damage list back and lets Annex A follow it
    (parts/prefill.sync_annex_a): rows a person priced or typed are kept,
    the machine's unpriced rows are rebuilt, a kept row for a part no
    longer damaged is flagged stale. Priced from past cases only when
    this person AND the case's owner may see the price book
    (parts.price_book_for_case). Its own history action (not "edited",
    which audit() folds into the previous edit within 30 minutes, losing
    the detail). The PDF is rendered again on the next download, as after
    PUT /annex-a."""
    data["damage_summary"] = damage_summary
    data["damaged_parts"] = [it.get("part") for it in damage_summary if it.get("part")]
    data = sync_annex_a(db, data, price_book=price_book_for_case(db, r, user))
    # Reassigned rather than mutated in place: `data` is a plain JSON
    # column, so SQLAlchemy only detects the change on assignment (same
    # reason sign_off_report/reopen_report rebuild the dict).
    r.data = data
    r.pdf_path = None
    audit(db, user, action, r.id, detail=detail)
    db.commit()
    return {"id": r.id, "damage_summary": data["damage_summary"], "damaged_parts": data["damaged_parts"],
            "annex_a": data["annex_a"], "annex_c": data["annex_c"]}


@app.patch("/reports/{report_id}/damage-items/{item_index}")
def review_damage_item(
    report_id: str, item_index: int, body: DamageItemReviewRequest, user: CurrentUser = Depends(editable_case)
) -> dict:
    """Changes one damage item: the part (and an Other's name), damage
    type, condition (the firm's words), remarks, severity, photo, the
    verified tick, the OEM part number. Ticking Verified or renaming the
    part clears side_inferred. The box the AI drew is dropped when the
    photo changes (it was drawn on the old one). Answers the
    whole damage list and the Annex A/C that now follow it.

    Deliberately NOT routed through PUT /reports/{id}: that replaces the
    entire data blob and re-renders the PDF through Chromium on every
    call, which is far too heavy for ticking a checkbox (and this project
    has already hit Chromium crashes rendering photo-heavy reports). The
    PDF does show verification state, but sign_off_report/reopen_report
    both re-render it, so the authoritative document still picks these up
    at the moment it actually matters.
    """
    db = SessionLocal()
    try:
        r = _damage_case(db, report_id)
        data = dict(r.data or {})
        damage_summary = list(data.get("damage_summary") or [])
        if not 0 <= item_index < len(damage_summary):
            raise HTTPException(
                status_code=404,
                detail=f"Damage item {item_index} not found (report has {len(damage_summary)})",
            )
        item = dict(damage_summary[item_index])
        changes = _damage_item_fields(body.model_dump(exclude_unset=True), r)
        if "photo_reference" in changes and changes["photo_reference"] != item.get("photo_reference"):
            item["bbox_2d"] = None
        before = item_key(item)
        part_changed = "part" in changes and changes["part"] != item.get("part")
        item.update(changes)
        # The name is an Other's alone (schema.DamageSummaryItem): a part
        # renamed off "Other / Not Listed" drops it.
        if item.get("other_part_name") and str(item.get("part") or "").strip() != OTHER_PART:
            item["other_part_name"] = None
        # A new name for an Other is another part too (item_key).
        renamed = part_changed or item_key(item) != before
        if renamed:
            _undismiss(data, item)  # renamed to a part dismissed earlier: as POST
        # A side the pipeline inferred (resolve_sides) is confirmed only by
        # a person saying so: ticking Verified, or naming the part (and so
        # its side) themselves. A new photo, OEM number, condition or remark
        # says nothing about the side, and must not make Annex A print LH /
        # RH (prefill.side_unconfirmed; review, 2026-11-08).
        if changes.get("human_verified") is True or renamed:
            item["side_inferred"] = False
        # Firm words with no AI type: the type they imply, as at drafting
        # (the 3D colours and analytics read damage_type). A condition is
        # never rewritten from a damage type here: once set it changes only
        # when a person sends one.
        if changes.get("condition_tokens") and not item.get("damage_type"):
            item["damage_type"] = damage_type_from_tokens(changes["condition_tokens"])
        damage_summary[item_index] = item
        out = _save_damage_items(db, user, r, data, damage_summary, "damage_item_edited",
                                 f"item {item_index + 1}: {item_source(item)}")
        return {"item_index": item_index, "item": item, **out}
    finally:
        db.close()


@app.post("/reports/{report_id}/damage-items")
def add_damage_item(report_id: str, body: DamageItemAddRequest, user: CurrentUser = Depends(editable_case)) -> dict:
    """Appends a part the surveyor names: verified by them, so
    human_verified is true from the start. A part they had dismissed
    earlier (DELETE below) is theirs again: it leaves dismissed_parts, so
    its Annex A rows come back."""
    db = SessionLocal()
    try:
        r = _damage_case(db, report_id)
        data = dict(r.data or {})
        damage_summary = list(data.get("damage_summary") or [])
        fields = _damage_item_fields(body.model_dump(), r)
        # As the drafting step does (extraction._firm_condition): a type with
        # no words gets the firm word for it; words with no type (the
        # dashboard's add row sends words only) get the type they imply --
        # none for Necessary / Serviceable / Repair.
        if not fields.get("condition") and fields.get("damage_type"):
            fields["condition_tokens"], fields["condition"] = firm_condition([], fields["damage_type"])
        if not fields.get("damage_type") and fields.get("condition_tokens"):
            fields["damage_type"] = damage_type_from_tokens(fields["condition_tokens"])
        item = DamageSummaryItem(**fields, human_verified=True).model_dump()
        damage_summary.append(item)
        _undismiss(data, item)
        out = _save_damage_items(db, user, r, data, damage_summary, "damage_item_added", item_source(item))
        return {"item_index": len(damage_summary) - 1, "item": item, **out}
    finally:
        db.close()


@app.delete("/reports/{report_id}/damage-items/{item_index}")
def remove_damage_item(report_id: str, item_index: int, user: CurrentUser = Depends(editable_case)) -> dict:
    """Removes one damage item. The part goes into dismissed_parts (its
    canonical name, or a named Other's name: prefill.item_key) when no
    other item names it, so a later sync -- or a re-detection (Phase 2) --
    doesn't bring its rows back; Annex A rows a person priced for it stay,
    flagged stale."""
    db = SessionLocal()
    try:
        r = _damage_case(db, report_id)
        data = dict(r.data or {})
        damage_summary = list(data.get("damage_summary") or [])
        if not 0 <= item_index < len(damage_summary):
            raise HTTPException(
                status_code=404,
                detail=f"Damage item {item_index} not found (report has {len(damage_summary)})",
            )
        removed = damage_summary.pop(item_index)
        # Keyed as sync_annex_a keys parts (prefill.item_key): a named
        # "Other / Not Listed" part is dismissed by its name, so another
        # Other on the list neither keeps its rows nor loses its own.
        key = item_key(removed)
        dismissed = list(data.get("dismissed_parts") or [])
        still_listed = any(item_key(it) == key for it in damage_summary)
        if key and key not in dismissed and not still_listed:
            dismissed.append(key)
        data["dismissed_parts"] = dismissed
        out = _save_damage_items(db, user, r, data, damage_summary, "damage_item_removed", item_source(removed))
        return {"item_index": item_index, "item": removed, **out}
    finally:
        db.close()


_MAX_SIGNATURE_CHARS = 700_000  # a drawn PNG is ~10-60 KB as base64


def _number(value, cast, fallback):
    """value as cast (float / int), or fallback when it is missing or isn't
    a number -- a sign-off body is read as loose JSON, not a model."""
    if value is None:
        return fallback
    try:
        return cast(value)
    except (ValueError, TypeError):
        return fallback


def _drawn_signature(value) -> str | None:
    """Only the PNG the sign-off pad produces. The value is placed in an
    <img src> in the PDF template, so anything else -- a URL -- would make
    the PDF renderer fetch it from the server's own network."""
    if isinstance(value, str) and value.startswith("data:image/png;base64,") and len(value) <= _MAX_SIGNATURE_CHARS:
        return value
    return None


def _with_sign_off(report_data: dict, **updates) -> dict:
    """Returns a NEW report-data dict with sign_off fields applied.

    Copies at every level on purpose. `dict(r.data)` is a shallow copy, so
    `data["sign_off"]` is the *same object* SQLAlchemy loaded -- mutating
    it in place also mutates the "old" value it compares against, the JSON
    column looks unchanged, and the UPDATE is silently skipped. That is
    exactly what happened here: sign-off appeared to work because
    Report.status (a plain string) did persist, while reviewed_by and
    signature_date never saved at all -- which is why avg_resolution_time
    in /analytics/summary was permanently null.
    """
    data = dict(report_data or {})
    sign_off = dict(data.get("sign_off") or {})
    sign_off.update(updates)
    data["sign_off"] = sign_off
    return data


@app.post("/reports/{report_id}/reopen")
def reopen_report(report_id: str, user: CurrentUser = Depends(visible_case)) -> dict:
    """Explicitly unlocks a Signed Off report for editing -- a deliberate
    action rather than update_report silently allowing edits underneath an
    already-signed-off record.

    Reopening withdraws someone's signature, so only the surveyor who signed
    it or an admin may (permissions.can_reopen)."""
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
        _refuse_intake_draft(r)
        if _is_signed_off(r) and not can_reopen(user, r):
            raise not_allowed("Only the surveyor who signed this report, or an admin, can reopen it.")

        r.status = "confirmed"
        r.data = _with_sign_off(r.data, status="Draft")

        r.pdf_path = _render_after_change(user, r, "reopened")

        audit(db, user, "reopened", r.id)
        db.commit()
        return {"id": r.id, "status": r.status}
    finally:
        db.close()


@app.post("/reports/{report_id}/sign-off")
async def sign_off_report(report_id: str, request: Request, user: CurrentUser = Depends(editable_case)) -> dict:
    """Signs the case as the signed-in user, with their own profile
    (Account page). The name, qualifications, licence and firm used to come
    from the request -- defaulting to one fixed surveyor -- so anyone could
    sign as anyone; any such fields in the body are now ignored.

    Signing puts the firm's stamp on the report, so only staff may: admins
    and "May sign off" accounts (permissions.can_sign_off).

    The body must carry the declaration the sign-off box asks for
    (terms_accepted: true; 400 declaration_required). While the field
    review (app/reports/review.py) has items open -- empty fields only a
    person can fill, guesses, usual wordings and past-case prices nobody
    confirmed -- it also needs open_items_ack: true ("Sign off with N items
    open"), else 409 {detail, code: "open_items", open, counts}: a
    warning, never a block. The history entry records what was open."""
    import hashlib
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
        _refuse_intake_draft(r)
        if not can_sign_off(user, r):
            raise not_allowed("Only an admin, or an account an admin has allowed to sign off, can sign off a report.")
        if _is_signed_off(r):
            raise HTTPException(status_code=409, detail="This report is already signed off.")

        try:
            body_data = await request.json()
        except Exception:
            body_data = {}
        if not isinstance(body_data, dict):
            body_data = {}
        if body_data.get("terms_accepted") is not True:
            raise ApiError(400, "Tick the declaration before signing off.", "declaration_required")

        # The figures Annex C prints: typed at sign-off when the body has
        # them, else what is saved on Annex C (PUT /reports/{id}/annex-c) --
        # the dashboard saves them there now and sends nothing here.
        annex_c_now = (r.data or {}).get("annex_c") or {}
        quantum = _number(body_data.get("agreed_quantum"), float, annex_c_now.get("agreed_lump_sum"))
        days = _number(body_data.get("turnaround_days"), int, annex_c_now.get("repair_days"))
        title = body_data.get("signatory_title")
        title = title.strip()[:120] if isinstance(title, str) and title.strip() else None

        # What is still open, with the Annex C figures this sign-off sets.
        as_signed = {**(r.data or {}), "annex_c": {**annex_c_now, "agreed_lump_sum": quantum, "repair_days": days}}
        review = review_state(as_signed, r.ai_draft, as_signed.get("photo_index"), signed_off=False)
        if review["open"] > 0 and body_data.get("open_items_ack") is not True:
            n = review["open"]
            return JSONResponse({
                "detail": f"{n} item{'s are' if n != 1 else ' is'} still open (to fill in or check). "
                          f"Tick \"Sign off with {n} item{'s' if n != 1 else ''} open\" to sign anyway.",
                "code": "open_items",
                "open": n,
                "counts": review["counts"],
            }, status_code=409)

        signed_at = datetime.now(timezone.utc).isoformat()
        # Always computed here, never taken from the request.
        canonical_str = f"{r.id}:{user.id}:{user.name}:{signed_at}"
        sig_hash = hashlib.sha256(canonical_str.encode()).hexdigest().upper()[:32]

        updates = {
            "status": "Signed Off",
            "reviewed_by": user.name,
            "approved_by": user.name,
            "surveyor_name": user.name,
            "qualifications": user.qualifications or None,
            "license_number": user.license_number or None,
            "firm_name": user.firm_name or None,
            "signed_by_user_id": user.id,
            "signed_by_username": user.username,
            "signed_by_email": user.email,
            "signature_hash": sig_hash,
            "signature_data_url": _drawn_signature(body_data.get("signature_data_url")),
            "signatory_title": title,
            "agreed_quantum": quantum,
            "turnaround_days": days,
            "liability_opinion": body_data.get("liability_opinion"),
            "remarks": body_data.get("remarks"),
            # Only what the surveyor actually ticked (required above) -- this
            # defaulted to True, recording declarations nobody had accepted.
            "terms_accepted": True,
            "signature_date": signed_at,
        }

        r.status = "Signed Off"
        new_data = _with_sign_off(r.data, **updates)

        # Annex C carries the settled figures; the approved cost on the
        # insurance details follows the lump sum. No GST: the firm's letter
        # has none, so the figures a signature used to add are left None.
        ac = dict(new_data.get("annex_c") or {})
        if quantum is not None:
            ac["agreed_lump_sum"] = quantum
            ins = dict(new_data.get("insurance_details") or {})
            ins["final_approved_cost"] = f"S${quantum:,.2f}"
            new_data["insurance_details"] = ins
        if days is not None:
            ac["repair_days"] = days
        ac["gst_amount"] = None
        ac["total_with_gst"] = None
        new_data["annex_c"] = ac

        if body_data.get("liability_opinion"):
            ba = dict(new_data.get("bola_assessment") or {})
            ba["apportionment_rationale"] = str(body_data["liability_opinion"])
            new_data["bola_assessment"] = ba

        r.data = new_data

        # On a worker thread: this endpoint is async (it reads the JSON body
        # itself), and Playwright's sync API refuses to run on the event
        # loop -- the signed PDF used to keep the unsigned copy.
        r.pdf_path = await asyncio.to_thread(_render_after_change, user, r, "signed off")

        # The account's email too: the name on a profile can be edited. And
        # what the field review still had open ("no items open", or "3 items
        # open: cover 2, Annex A 1"), acknowledged or not.
        audit(db, user, "signed_off", r.id, detail=f"{user.email}; {open_summary(review)}")
        db.commit()
        pdf_url = to_public_url(r.pdf_path) if r.pdf_path and Path(r.pdf_path).exists() else None
        return {"id": r.id, "status": r.status, "pdf_url": pdf_url, "sign_off": r.data.get("sign_off")}
    finally:
        db.close()


@app.get("/analytics/summary")
def get_analytics_summary(scope: Scope = None, user: CurrentUser = Depends(current_user)) -> dict:
    """Over the same cases GET /reports lists for this ?scope=."""
    db = SessionLocal()
    try:
        # Unfinished intakes aren't incidents yet: counting them would add
        # empty "Moderate, Vehicle Collision" rows to every chart.
        reports = (
            _scoped(db.query(Report), user, scope)
            .filter(Report.status != "intake")
            .order_by(Report.created_at.desc())
            .all()
        )
        total_incidents = len(reports)
        pending_review = sum(1 for r in reports if r.status in ("confirmed", "draft", "pending", "Under Review"))
        signed_off = sum(1 for r in reports if r.status in ("Signed Off", "signed_off"))
        high_severity = sum(1 for r in reports if (r.data or {}).get("severity_level") == "Severe")
        
        category_counts: dict[str, int] = {}
        # "Unrated" is its own bucket: counting an unassessed case as
        # Moderate (as this did) inflated the severity chart.
        severity_counts: dict[str, int] = {"Minor": 0, "Moderate": 0, "Severe": 0, "Unrated": 0}
        damaged_parts_frequency: dict[str, int] = {}
        confidence_counts: dict[str, int] = {}
        resolution_hours: list[float] = []

        for r in reports:
            data = r.data or {}
            cats = data.get("category") or ["Vehicle Collision or Damage"]
            for c in cats:
                category_counts[c] = category_counts.get(c, 0) + 1

            sev = data.get("severity_level") or "Unrated"
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

            # Same damage_summary-else-damaged_parts fallback used everywhere
            # else in this codebase (see the dashboard's report detail page).
            damage_items = data.get("damage_summary") or [{"part": p} for p in (data.get("damaged_parts") or [])]
            for item in damage_items:
                part = item.get("part")
                if part:
                    damaged_parts_frequency[part] = damaged_parts_frequency.get(part, 0) + 1
                conf = item.get("ai_confidence")
                if conf:
                    confidence_counts[conf] = confidence_counts.get(conf, 0) + 1

            sign_off = data.get("sign_off") or {}
            sig_date = sign_off.get("signature_date")
            if sign_off.get("status") == "Signed Off" and sig_date:
                try:
                    signed_at = datetime.fromisoformat(sig_date)
                    created_at = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc)
                    resolution_hours.append((signed_at - created_at).total_seconds() / 3600)
                except (ValueError, TypeError):
                    pass

        total_rated = sum(confidence_counts.values())
        # Real, computed ratio rather than an invented percentage -- the
        # model only ever gives a qualitative High/Medium/Low self-rating
        # (see extraction.py's SYSTEM_PROMPT), so this is "what share of
        # rated detections were High confidence," not a fabricated score.
        ai_confidence_avg = (
            f"{round(100 * confidence_counts.get('High', 0) / total_rated)}% High confidence"
            if total_rated > 0
            else None
        )
        avg_resolution_time = (
            f"{round(sum(resolution_hours) / len(resolution_hours), 1)}h"
            if resolution_hours
            else None
        )

        recent_activity = [
            {
                "id": r.id,
                "created_at": iso_utc(r.created_at),
                "channel": r.channel,
                "status": r.status,
                "location": (r.data or {}).get("location"),
                "reporter": (r.data or {}).get("reporter_name"),
                "category": (r.data or {}).get("category") or ["Vehicle Collision or Damage"],
            }
            for r in reports[:5]
        ]
        
        return {
            "total_incidents": total_incidents,
            "pending_review": pending_review,
            "signed_off": signed_off,
            "high_severity": high_severity,
            "category_counts": category_counts,
            "severity_counts": severity_counts,
            "damaged_parts_frequency": damaged_parts_frequency,
            "recent_activity": recent_activity,
            "avg_resolution_time": avg_resolution_time,
            "ai_confidence_avg": ai_confidence_avg,
        }
    finally:
        db.close()


@app.delete("/reports/{report_id}")
def delete_report(report_id: str, user: CurrentUser = Depends(visible_case)) -> dict:
    """Admins only, except an unfinished intake draft (status "intake"),
    which the person filing it may throw away (permissions.can_delete_case).
    A public report in the inbox is refused (Discard it, with a reason:
    POST /inbox/{id}/discard); a taken one goes with its submission row,
    and its history entry carries the reference (never a name or phone).

    A refusal says which rule refused. The filed-case one used to read
    "Only an admin can delete a case.", which on a case its owner still
    took for their draft looked like drafts being admin-only too
    (scripts/test_delete_draft.py)."""
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
        if not can_delete_case(user, r):
            if in_inbox(r):
                raise not_allowed("This report is in the Public Inbox: use Discard, with a reason.")
            raise not_allowed(
                "This case has been filed: only an admin can delete it." if r.status != "intake"
                else "Only the person filing this draft, or an admin, can throw it away."
            )

        if is_public(r):
            # A taken public draft: only while it is still this owner's and
            # neither the AI nor finalize holds it (public.delete_submission).
            seen_owner = r.owner_id
            clear_stale_run(db, report_id)
            if not public.delete_submission(
                db, report_id, seen_owner=seen_owner, actor=user, action="deleted", detail="Unfinished intake",
            ):
                raise public.refusal(db, report_id)
            db.commit()
            remove_intake_dir(report_id)
            remove_incident_dirs(report_id)
            return {"deleted": report_id}

        # Unconditional (and a no-op for ordinary cases): a draft that was
        # turned into a case some other way would otherwise leak its folder.
        reference = delete_intake_files(db, r.id)
        # Every month folder the case has files in, not only the PDF's.
        remove_incident_dirs(r.id)

        plate = ((r.data or {}).get("vehicle_info") or {}).get("plate_number")
        detail = "Unfinished intake" if r.status == "intake" else (plate or "")
        audit(db, user, "deleted", report_id, detail=" · ".join(p for p in (reference, detail) if p))
        db.delete(r)
        db.commit()
        return {"deleted": report_id}
    finally:
        db.close()


def _enrich_single_report_data(raw_data: dict) -> dict:
    """"Fill derived fields" (the dashboard's former "AI Auto-Map"): only
    what follows from facts already recorded on the case --
      - the vehicle summary string from plate + make + model,
      - the body type from the vehicle catalogue, when make/model are known,
      - Annex A rows listed (unpriced) from the damage found, and the annex
        arithmetic (synthesize_annexes).

    Until 2026-10-21 this guessed make and model from the plate prefix,
    invented an estimated repair cost, insurer, claim type, workshop, claim
    number (reusing the real CL 11900), odometer, transmission, tyres,
    steering/brake checks and a police report with a named officer, and
    saved them all as case data. It is not, and never was, a Gemini call.
    """
    import copy

    data = copy.deepcopy(raw_data or {})
    vinfo = dict(data.get("vehicle_info") or {})
    if vinfo.get("make") or vinfo.get("model"):
        if not vinfo.get("body_type"):
            vinfo["body_type"] = body_type_for(vinfo.get("make"), vinfo.get("model"), None)
        if not data.get("vehicle_details"):
            name = " ".join(filter(None, [vinfo.get("make"), vinfo.get("model")]))
            data["vehicle_details"] = " - ".join(filter(None, [vinfo.get("plate_number"), name]))
    data["vehicle_info"] = vinfo

    from app.reports.synthesize_annexes import synthesize_annexes_for_report

    return synthesize_annexes_for_report(data)


def _is_signed_off(r: Report) -> bool:
    return r.status == "Signed Off" or ((r.data or {}).get("sign_off") or {}).get("status") == "Signed Off"


@app.post("/reports/{report_id}/ai-enrich")
def ai_enrich_report(report_id: str, user: CurrentUser = Depends(editable_case)) -> dict:
    """Fill derived fields on one report (see _enrich_single_report_data)."""
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
        _refuse_intake_draft(r)
        if _is_signed_off(r):
            raise HTTPException(status_code=409, detail="Report is signed off and locked. Reopen it before editing.")
        r.data = _enrich_single_report_data(r.data or {})
        audit(db, user, "edited", r.id)
        db.commit()
        return {"id": r.id, "status": "enriched", "data": r.data}
    finally:
        db.close()


class OwnerUpdate(BaseModel):
    # Required, so a body missing it can't unassign a case by accident;
    # null means Unassigned.
    owner_id: Optional[str]


@app.patch("/reports/{report_id}/owner")
def set_report_owner(report_id: str, body: OwnerUpdate, user: CurrentUser = Depends(visible_case)) -> dict:
    """Admins hand a case to an active account, or make it Unassigned (only
    admins and "see all" staff see those). Noted in the case history. A
    draft stays with the person filing it."""
    if not can_assign(user):
        raise not_allowed("Only an admin can assign cases.")
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
        if r.status == "intake":
            raise ApiError(409, "This is an unfinished intake; it stays with the person filing it.", "owner_draft")
        target_id = body.owner_id or None
        if target_id == r.owner_id:
            current = owners.owners_by_id(db, [target_id]).get(target_id)
            return {"owner": owners.owner_dict(current)}
        new_owner = db.get(User, target_id) if target_id else None
        if target_id and new_owner is None:
            raise HTTPException(status_code=404, detail="User not found.")
        if new_owner is not None and not new_owner.active:
            raise ApiError(409, "That account is disabled. Enable it first, or choose someone else.", "owner_inactive")
        if owners.set_owner(db, r, new_owner, user):
            # Holding the write lock now: refuse if they were disabled since
            # the check above, so a case never lands on a disabled account.
            db.flush()
            recipient_still_active(db, new_owner)
        db.commit()
        return {"owner": owners.owner_dict(new_owner)}
    finally:
        db.close()


class Reassign(BaseModel):
    from_user_id: str
    # Required, as OwnerUpdate.owner_id; null means Unassigned.
    to_user_id: Optional[str]
    include_signed: bool = False


@app.post("/reports/reassign")
def reassign_cases(body: Reassign, admin: CurrentUser = Depends(require_admin)) -> dict:
    """Admins move all of someone's open cases at once ("Move their cases"
    on the Users screen), and with include_signed their signed-off ones
    too, to an active account or Unassigned. Each case gets an "assigned"
    history entry, as PATCH /reports/{id}/owner writes; drafts stay."""
    db = SessionLocal()
    try:
        source = db.get(User, body.from_user_id)
        if source is None:
            raise HTTPException(status_code=404, detail="User not found.")
        target = case_recipient(db, body.to_user_id, source.id) if body.to_user_id else None
        # Its UPDATE takes the write lock; then the target, who may have
        # been disabled since the check above, is checked again.
        moved = owners.reassign_open_cases(db, source.id, target, admin, include_signed=body.include_signed)
        recipient_still_active(db, target)
        db.commit()
        return {"moved": moved}
    finally:
        db.close()


@app.post("/reports/ai-enrich-all")
def ai_enrich_all_reports(admin: CurrentUser = Depends(require_admin)) -> dict:
    """Fill derived fields on every open report. Signed-off reports are
    skipped -- this used to rewrite them too, past the sign-off lock."""
    db = SessionLocal()
    try:
        count = 0
        for r in db.query(Report).filter(Report.status != "intake").all():
            if _is_signed_off(r):
                continue
            r.data = _enrich_single_report_data(r.data or {})
            audit(db, admin, "edited", r.id)
            count += 1
        db.commit()
        return {"status": "success", "enriched_count": count}
    finally:
        db.close()


# =============================================================================
# Settings & system info
#
# Split deliberately into two halves, because conflating them is exactly
# what made the old Settings page dishonest -- it showed a hardcoded model
# chain (which didn't even match the real one) next to a Save button that
# persisted nothing, implying all of it was editable.
#
#   /settings     -> the few genuinely user-owned values, really persisted
#   /system/info  -> live, read-only facts about how this deployment is
#                    actually configured and what's in it
# =============================================================================

# Only keys listed here can be written, so a typo'd or hostile key can't
# quietly fill the table with junk. The public report settings (P6:
# public_contact_phone, dpo_contact, company_key_billed) are intake/
# public.py's SETTING_KEYS, which stores and reads them; admins only.
ALLOWED_SETTING_KEYS = {"company_name"}


class SettingsUpdateRequest(BaseModel):
    company_name: Optional[str] = None
    # P6, admins only (as all of PUT /settings): the public line the bot
    # gives, and the data-protection contact /privacy shows.
    public_contact_phone: Optional[str] = Field(None, max_length=public.MAX_SETTING_TEXT)
    dpo_contact: Optional[str] = Field(None, max_length=public.MAX_SETTING_TEXT)
    # A JSON true/false, or "true"/"false".
    company_key_billed: Optional[bool] = None

    @field_validator("company_key_billed", mode="before")
    @classmethod
    def _true_or_false(cls, value):
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise ValueError("company_key_billed is true or false")


def _settings(db, user: CurrentUser) -> dict:
    """company_name for everyone signed in; the public report settings only
    for admins (p6_decisions §1)."""
    stored = {r.key: r.value for r in db.query(AppSetting).filter(AppSetting.key.in_(ALLOWED_SETTING_KEYS)).all()}
    result = {key: stored.get(key, "") for key in sorted(ALLOWED_SETTING_KEYS)}
    if user.is_admin:
        result.update(public.settings_dict(db))
    return result


@app.get("/settings")
def get_settings(user: CurrentUser = Depends(current_user)) -> dict:
    db = SessionLocal()
    try:
        return _settings(db, user)
    finally:
        db.close()


@app.put("/settings")
def update_settings(body: SettingsUpdateRequest, admin: CurrentUser = Depends(require_admin)) -> dict:
    db = SessionLocal()
    try:
        billed_before = public.company_key_billed(db)
        for key, value in body.model_dump(exclude_none=True).items():
            if key in public.SETTING_KEYS:
                public.save_setting(db, key, value)
                continue
            if key not in ALLOWED_SETTING_KEYS:
                continue
            row = db.get(AppSetting, key)
            if row:
                row.value = value.strip()
            else:
                db.add(AppSetting(key=key, value=value.strip()))
        db.flush()
        billed = public.company_key_billed(db)
        if billed != billed_before:
            # Who said the company key's project is billed: from then on
            # public reports may use it (ai/keys.resolve_keys).
            audit(db, admin, "company_key_billed_on" if billed else "company_key_billed_off")
        db.commit()
        return _settings(db, admin)
    finally:
        db.close()


@app.get("/privacy/contact")
def privacy_contact() -> dict:
    """For the public privacy notice (/privacy#public-reports), read without
    signing in (middleware PUBLIC_PATHS): these two settings, nothing else.
    Empty strings when not set."""
    db = SessionLocal()
    try:
        return {"dpo_contact": public.dpo_contact(db), "public_contact_phone": public.public_contact_phone(db)}
    finally:
        db.close()


@app.get("/system/info")
def get_system_info(admin: CurrentUser = Depends(require_admin)) -> dict:
    """Live read-only facts about this deployment. Everything here is
    measured or read from real config at request time -- nothing is a
    hardcoded display value."""
    db = SessionLocal()
    try:
        reports = db.query(Report).all()
        channel_counts: dict[str, int] = {}
        photo_count = 0
        pdf_count = 0
        for r in reports:
            channel_counts[r.channel] = channel_counts.get(r.channel, 0) + 1
            photo_count += len(r.photo_paths or [])
            if r.pdf_path:
                pdf_count += 1

        storage_root = Path(settings.storage_dir)
        storage_bytes = 0
        if storage_root.exists():
            storage_bytes = sum(f.stat().st_size for f in storage_root.rglob("*") if f.is_file())

        return {
            "ai": {
                # The real chain this deployment will actually try, in order.
                "model_chain": [m.strip() for m in settings.gemini_model_chain.split(",") if m.strip()],
                "min_call_interval_seconds": settings.gemini_min_call_interval_seconds,
                "request_timeout_seconds": 45.0,
                # Never the key itself -- only whether one is present.
                "api_key_configured": bool(settings.gemini_api_key),
            },
            "channels": {
                "telegram_configured": bool(settings.telegram_bot_token),
                "whatsapp_configured": bool(settings.twilio_account_sid and settings.twilio_auth_token),
                "reports_by_channel": channel_counts,
            },
            "storage": {
                "reports": len(reports),
                "photos": photo_count,
                "pdfs": pdf_count,
                "bytes_used": storage_bytes,
                "storage_dir": settings.storage_dir,
            },
            "database": {
                # Scheme only -- the URL can contain credentials on a
                # non-sqlite deployment.
                "engine": settings.database_url.split("://", 1)[0],
            },
            "auth": {
                # Every request needs a signed-in user (app/auth).
                "configured": True,
            },
            # People's own AI keys: whether this server can open them, and
            # if not, why and what fixes it (ai/secretbox.py).
            "ai_key_storage": secretbox.describe(),
            # The bot's @username (the bot writes it when it starts; None
            # until then) and how many accounts have connected a Telegram.
            "telegram": {
                "bot_username": chat_links.bot_username(db),
                "accounts_connected": chat_links.accounts_connected(db),
            },
            # What infra/backup.sh last recorded, or None when no backup
            # has been recorded here (Settings > Company shows it in red).
            "last_backup": _last_backup(),
        }
    finally:
        db.close()


# Written by infra/backup.sh into the data volume, which is STORAGE_DIR in
# the container: {at, reports, local_path, uploaded, bucket}. Outside
# _PUBLIC_FILE_PREFIXES, so never served under /files.
LAST_BACKUP_FILE = ".last_backup"


def _last_backup() -> dict | None:
    """System info's last_backup: {at, reports, uploaded, bucket}, or None
    when there is no record or it can't be read -- never a guess. The
    backup's path on the server (local_path) isn't sent."""
    try:
        raw = json.loads((Path(settings.storage_dir) / LAST_BACKUP_FILE).read_text(encoding="utf-8"))
        # backup.sh writes UTC with a "Z" (2026-11-05T06:17:00Z).
        at = datetime.fromisoformat(raw["at"].replace("Z", "+00:00"))
        reports, uploaded, bucket = raw["reports"], raw["uploaded"], raw.get("bucket")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    if not isinstance(reports, int) or isinstance(reports, bool) or not isinstance(uploaded, bool):
        return None
    return {
        "at": iso_utc(at),
        "reports": reports,
        "uploaded": uploaded,
        "bucket": bucket if isinstance(bucket, str) and bucket else None,
    }


# =============================================================================
# Setup: operator-supplied API keys and connection tests
#
# The Settings page is a SETUP surface, not an internals dashboard -- the
# operator supplies credentials and gets told whether they work. Model
# chain, rate limits and timeouts are deliberately not exposed: they're
# implementation detail the operator can't act on, and the fallback
# behaviour is the system's job to handle silently.
#
# SECURITY: no endpoint here ever returns key material. /setup/llm-keys
# returns only an id, label, created_at and the last 4 characters -- enough
# to tell two keys apart in a list, useless to steal. Note this does NOT
# protect against tampering on its own; only admins can add or delete keys
# (require_admin on each endpoint).
# =============================================================================


class AddApiKeyRequest(BaseModel):
    apiKey: str
    label: Optional[str] = None


def _mask(key: str) -> str:
    return key[-4:] if len(key) >= 4 else "****"


@app.get("/setup/llm-keys")
def list_llm_keys(admin: CurrentUser = Depends(require_admin)) -> dict:
    db = SessionLocal()
    try:
        rows = db.query(ApiKey).filter(ApiKey.provider == "gemini").order_by(ApiKey.created_at).all()
        return {
            "keys": [
                {"id": r.id, "label": r.label or "", "last4": _mask(r.key), "created_at": iso_utc(r.created_at)}
                for r in rows
            ],
            # Surfaces that drafting still works off the deploy-time env var
            # even with no keys added here, so an empty list doesn't look
            # like a broken install.
            "env_key_configured": bool(settings.gemini_api_key),
        }
    finally:
        db.close()


@app.post("/setup/llm-keys")
def add_llm_key(body: AddApiKeyRequest, admin: CurrentUser = Depends(require_admin)) -> dict:
    key = body.apiKey.strip()
    if not key:
        raise HTTPException(status_code=400, detail="API key cannot be empty")

    # Someone's own key can't become everyone's: compared by fingerprint
    # (worked out first; it may read the secret).
    fingerprint = own_keys.fingerprint_if_readable(key)
    db = SessionLocal()
    try:
        existing = db.query(ApiKey).filter(ApiKey.provider == "gemini", ApiKey.key == key).first()
        if existing:
            raise HTTPException(status_code=409, detail="That API key has already been added")
        row = ApiKey(provider="gemini", key=key, label=(body.label or "").strip())
        db.add(row)
        # Checked once flushed, holding SQLite's write lock: an own key being
        # saved meanwhile waits, then finds this one (api/ai_keys.py).
        db.flush()
        owner = own_keys.own_key_owner(db, fingerprint)
        if owner:
            db.rollback()
            audit(db, admin, "ai_key_refused", detail=f"company key ...{_mask(key)} is {owner}'s own key")
            db.commit()
            raise ApiError(409, own_keys.IN_USE, "ai_key_in_use")
        db.commit()
        reset_client_cache()
        return {"id": row.id, "label": row.label, "last4": _mask(row.key)}
    finally:
        db.close()


@app.delete("/setup/llm-keys/{key_id}")
def delete_llm_key(key_id: str, admin: CurrentUser = Depends(require_admin)) -> dict:
    db = SessionLocal()
    try:
        row = db.get(ApiKey, key_id)
        if not row:
            raise HTTPException(status_code=404, detail="API key not found")
        db.delete(row)
        db.commit()
        reset_client_cache()
        return {"deleted": key_id}
    finally:
        db.close()


@app.post("/setup/test/{service}")
async def test_service(service: str, admin: CurrentUser = Depends(require_admin)) -> dict:
    """Proves a credential actually works, rather than only checking that
    a non-empty string is present. Returns ok/false plus a short message
    for display -- never raises for a failed credential, since 'your key
    is wrong' is a normal result here, not a server error."""
    if service == "llm":
        # The company keys, as always: own keys are tested on My AI key.
        keys = own_keys.company_keys()
        if not keys:
            return {"ok": False, "message": "No API key configured"}

        def _probe(key: own_keys.AiKey) -> tuple[bool, str]:
            try:
                client = get_client(key)
                # Cheapest real call that still proves the key is accepted:
                # list models. Avoids spending generation quota just to
                # answer "does this key work".
                next(iter(client.models.list()), None)
                return True, "Key accepted"
            except Exception as exc:
                return False, own_keys.scrub(str(exc), key.value)[:160]

        working = 0
        first_error = ""
        for k in keys:
            ok, msg = await asyncio.to_thread(_probe, k)
            if ok:
                working += 1
            elif not first_error:
                first_error = msg
        if working:
            return {"ok": True, "message": f"{working} of {len(keys)} key(s) working"}
        return {"ok": False, "message": first_error or "No key accepted"}

    if service == "telegram":
        if not settings.telegram_bot_token:
            return {"ok": False, "message": "No bot token configured"}

        def _probe_tg() -> tuple[bool, str]:
            try:
                import httpx

                r = httpx.get(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe",
                    timeout=10.0,
                )
                data = r.json()
                if r.status_code == 200 and data.get("ok"):
                    return True, f"Connected as @{data['result'].get('username', 'unknown')}"
                return False, str(data.get("description", "Token rejected"))[:160]
            except Exception as exc:
                return False, str(exc)[:160]

        ok, msg = await asyncio.to_thread(_probe_tg)
        return {"ok": ok, "message": msg}

    if service == "whatsapp":
        if not (settings.twilio_account_sid and settings.twilio_auth_token):
            return {"ok": False, "message": "Twilio credentials not configured"}

        def _probe_twilio() -> tuple[bool, str]:
            try:
                import httpx

                r = httpx.get(
                    f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}.json",
                    auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                    timeout=10.0,
                )
                if r.status_code == 200:
                    return True, "Credentials accepted"
                return False, f"Twilio rejected the credentials ({r.status_code})"
            except Exception as exc:
                return False, str(exc)[:160]

        ok, msg = await asyncio.to_thread(_probe_twilio)
        return {"ok": ok, "message": msg}

    raise HTTPException(status_code=404, detail=f"Unknown service {service!r}")


@app.get("/taxonomy")
def get_taxonomy(scope: Scope = None, user: CurrentUser = Depends(current_user)) -> dict:
    """The controlled vocabulary, plus how much of it real cases actually use.

    Usage counts are what make this more than a list: they show which parts
    are genuinely being seen, and -- via `legacy_terms` -- which free-text
    names from before the vocabulary existed are still in the data, so it's
    visible that older reports read differently rather than that being a
    silent inconsistency.

    Counted over the cases GET /reports lists for this ?scope=, drafts left
    out (like analytics).
    """
    db = SessionLocal()
    try:
        reports = _scoped(db.query(Report), user, scope).filter(Report.status != "intake").all()
        canonical = set(CANONICAL_PARTS)
        part_usage: dict[str, int] = {}
        legacy_usage: dict[str, int] = {}
        type_usage: dict[str, int] = {}

        for r in reports:
            for item in ((r.data or {}).get("damage_summary") or []):
                name = item.get("part")
                if name:
                    bucket = part_usage if name in canonical else legacy_usage
                    bucket[name] = bucket.get(name, 0) + 1
                dtype = item.get("damage_type")
                if dtype:
                    type_usage[dtype] = type_usage.get(dtype, 0) + 1

        return {
            "parts": CANONICAL_PARTS,
            "damage_types": CANONICAL_DAMAGE_TYPES,
            "severities": CANONICAL_SEVERITIES,
            # The firm's Annex A wording (taxonomy.py): the words a
            # condition is made of, in print order; the compounds the
            # firm's reports use most; the whole-word conditions (fittings,
            # kept parts, repair-not-renew); the Remarks values.
            "condition_tokens": FIRM_CONDITION_TOKENS,
            "condition_common": FIRM_CONDITION_COMMON,
            "condition_whole": FIRM_WHOLE_CONDITIONS,
            "remarks": FIRM_REMARKS,
            "body_types": BODY_TYPES,
            "model_body_types": MODEL_BODY_TYPES,
            "usage": {
                "parts": part_usage,
                "damage_types": type_usage,
                # Free-text names predating the vocabulary. Shown rather
                # than hidden so the gap between old and new reports is
                # explicit.
                "legacy_terms": legacy_usage,
                "reports_counted": len(reports),
            },
        }
    finally:
        db.close()
