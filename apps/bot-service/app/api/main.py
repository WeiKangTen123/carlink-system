import asyncio
import logging
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
from pydantic import BaseModel
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
from app.ai import secretbox
from app.ai.extraction import draft_report
from app.ai.keys import AiActor, AiUnavailable
from app.api import limits
from app.api.ai_keys import router as ai_keys_router
from app.api.auth import case_recipient, recipient_still_active, router as auth_router
from app.api.parts import router as parts_router
from app.api.signup import router as signup_router
from app.api.telegram_link import router as telegram_link_router
from app.api.intake import MAX_PHOTOS_PER_CASE, delete_intake_files, router as intake_router
from app.auth import chat_links
from app.auth.core import CurrentUser, audit, report_history
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
from app.auth.permissions import can_assign, can_delete_case, can_reopen, can_sign_off, is_staff, list_scope
from app.channels.whatsapp import router as whatsapp_router
from app.config import settings
from app.reports import owners
from app.reports.db import SessionLocal, init_db
from app.ai.client import get_client, reset_client_cache
from app.reports.models import ApiKey, AppSetting, IntakePhoto, Report, User, gen_id, iso_utc
from app.reports.taxonomy import (
    BODY_TYPES,
    CANONICAL_DAMAGE_TYPES,
    CANONICAL_PARTS,
    CANONICAL_SEVERITIES,
    MODEL_BODY_TYPES,
    body_type_for,
)
from app.reports.schema import SecurityIncidentDraft, SignOffInfo
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
app.include_router(intake_router)
app.include_router(ai_keys_router)
app.include_router(telegram_link_router)


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
        return await super().get_response(path, scope)

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
    ones. Lists, counts and charts all go through this."""
    scope = list_scope(user, requested)
    if scope is None:
        raise ApiError(403, "You can only list your own cases.", "scope_not_allowed")
    if scope == "mine":
        return query.filter(Report.owner_id == user.id)
    if scope == "unassigned":
        return query.filter(Report.owner_id.is_(None))
    return query


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/reports")
def list_reports(scope: Scope = None, user: CurrentUser = Depends(current_user)) -> list[dict]:
    """?scope=mine|all|unassigned. Plain users only ever get their own
    cases; "see all" staff default to their own, admins to all."""
    db = SessionLocal()
    try:
        reports = _scoped(db.query(Report), user, scope).order_by(Report.created_at.desc()).all()
        owner_rows = owners.owners_by_id(db, {r.owner_id for r in reports})
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
                first_photo = (
                    db.query(IntakePhoto)
                    .filter(IntakePhoto.report_id == r.id)
                    .order_by(IntakePhoto.position, IntakePhoto.created_at)
                    .first()
                )
                thumbnail_url = to_thumbnail_url(first_photo.path) if first_photo else None
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
        # admin on an Unassigned case, who it should probably go to.
        access = owners.access_fields(db, r, user)

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
            **access,
        }
    finally:
        db.close()


@app.get("/reports/{report_id}/download")
@app.head("/reports/{report_id}/download")
def download_report_pdf(report_id: str, user: CurrentUser = Depends(visible_case)):
    from fastapi.responses import FileResponse
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
        _refuse_intake_draft(r)

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

        filename = f"Car_Incident_Report_CIR-2026-{r.id[:4].upper()}.pdf"
        return FileResponse(
            path=r.pdf_path,
            filename=filename,
            media_type="application/pdf",
            # Not kept by the browser: a cached copy outlives signing out and
            # losing access to the case (reassigned, account unticked).
            headers={"Cache-Control": "no-store"},
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


@app.post("/reports/analyze-photos")
async def analyze_report_photos(
    description: str = Form(""),
    photos: list[UploadFile] = File(default=[]),
    user: CurrentUser = Depends(current_user),
) -> dict:
    """Dashboard equivalent of the Telegram bot's photo+text drafting step:
    saves the uploaded photos to a temp dir, runs the same Gemini drafting
    call used by the bot, and returns the draft for the reporter to review
    and edit -- nothing is saved to the database yet. The returned
    temp_photo_paths get passed back in on POST /reports if the reporter
    goes on to save the report, so the photos are only ever attached to a
    report the reporter actually confirmed.
    """
    # No case to gate on: the photos go to a case later, through PUT
    # /reports/{id}'s checks. AI first (the presser pays: 409 {detail,
    # code} when theirs isn't available; one request at a time, 429
    # ai_busy), then room on the disk, the file types and the hourly photo
    # count, before any photo is saved.
    actor = AiActor.for_user(user.id, "dashboard")
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
    """Only the two fields a reviewer actually fills in on the checklist.
    Both optional so a caller can tick the verified box without touching
    the part number, or vice versa."""
    human_verified: Optional[bool] = None
    oem_part_number: Optional[str] = None


@app.patch("/reports/{report_id}/damage-items/{item_index}")
def review_damage_item(
    report_id: str, item_index: int, body: DamageItemReviewRequest, user: CurrentUser = Depends(editable_case)
) -> dict:
    """Marks one damage line item verified and/or records its OEM part
    number -- the two things a surveyor is meant to do while reviewing the
    checklist, which the schema always had fields for (oem_part_number's
    own description says "a human enters this during review") but nothing
    could actually set.

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
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
        # Same lock rule update_report enforces -- a signed-off report is
        # a finalized record; reopen it first.
        if _is_signed_off(r):
            raise HTTPException(
                status_code=409,
                detail="Report is signed off and locked. Reopen it before editing.",
            )

        data = dict(r.data or {})
        damage_summary = list(data.get("damage_summary") or [])
        if not 0 <= item_index < len(damage_summary):
            raise HTTPException(
                status_code=404,
                detail=f"Damage item {item_index} not found (report has {len(damage_summary)})",
            )

        item = dict(damage_summary[item_index])
        if body.human_verified is not None:
            item["human_verified"] = body.human_verified
        if body.oem_part_number is not None:
            # Empty string clears the field rather than storing "" -- keeps
            # "not entered" as a single honest null everywhere downstream.
            item["oem_part_number"] = body.oem_part_number.strip() or None
        damage_summary[item_index] = item

        # Reassigned rather than mutated in place: `data` is a plain JSON
        # column, so SQLAlchemy only detects the change on assignment (same
        # reason sign_off_report/reopen_report rebuild the dict).
        data["damage_summary"] = damage_summary
        r.data = data

        audit(db, user, "edited", r.id)
        db.commit()
        return {"id": r.id, "item_index": item_index, "item": item}
    finally:
        db.close()


_MAX_SIGNATURE_CHARS = 700_000  # a drawn PNG is ~10-60 KB as base64


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
    and "May sign off" accounts (permissions.can_sign_off)."""
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
            "agreed_quantum": body_data.get("agreed_quantum"),
            "turnaround_days": body_data.get("turnaround_days"),
            "liability_opinion": body_data.get("liability_opinion"),
            "remarks": body_data.get("remarks"),
            # Only what the surveyor actually ticked -- this defaulted to
            # True, recording declarations nobody had accepted.
            "terms_accepted": bool(body_data.get("terms_accepted", False)),
            "signature_date": signed_at,
        }

        r.status = "Signed Off"
        new_data = _with_sign_off(r.data, **updates)

        # Update quantum in annex_c and insurance_details if provided
        if body_data.get("agreed_quantum") is not None:
            try:
                q_val = float(body_data["agreed_quantum"])
                ac = dict(new_data.get("annex_c") or {})
                ac["agreed_lump_sum"] = q_val
                ac["gst_amount"] = round(q_val * 0.09, 2)
                ac["total_with_gst"] = round(q_val * 1.09, 2)
                new_data["annex_c"] = ac
                ins = dict(new_data.get("insurance_details") or {})
                ins["final_approved_cost"] = f"S${q_val:,.2f}"
                new_data["insurance_details"] = ins
            except (ValueError, TypeError):
                pass

        if body_data.get("turnaround_days") is not None:
            try:
                ac = dict(new_data.get("annex_c") or {})
                ac["repair_days"] = int(body_data["turnaround_days"])
                new_data["annex_c"] = ac
            except (ValueError, TypeError):
                pass

        if body_data.get("liability_opinion"):
            ba = dict(new_data.get("bola_assessment") or {})
            ba["apportionment_rationale"] = str(body_data["liability_opinion"])
            new_data["bola_assessment"] = ba

        r.data = new_data

        # On a worker thread: this endpoint is async (it reads the JSON body
        # itself), and Playwright's sync API refuses to run on the event
        # loop -- the signed PDF used to keep the unsigned copy.
        r.pdf_path = await asyncio.to_thread(_render_after_change, user, r, "signed off")

        # The account's email too: the name on a profile can be edited.
        audit(db, user, "signed_off", r.id, detail=user.email)
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
    which the person filing it may throw away (permissions.can_delete_case)."""
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail=CASE_NOT_FOUND)
        if not can_delete_case(user, r):
            raise not_allowed(
                "Only an admin can delete a case." if r.status != "intake"
                else "Only the person filing this draft, or an admin, can throw it away."
            )

        # Unconditional (and a no-op for ordinary cases): a draft that was
        # turned into a case some other way would otherwise leak its folder.
        delete_intake_files(db, r.id)
        # Every month folder the case has files in, not only the PDF's.
        remove_incident_dirs(r.id)

        plate = ((r.data or {}).get("vehicle_info") or {}).get("plate_number")
        audit(db, user, "deleted", report_id, detail="Unfinished intake" if r.status == "intake" else (plate or ""))
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
# quietly fill the table with junk.
ALLOWED_SETTING_KEYS = {"company_name"}


class SettingsUpdateRequest(BaseModel):
    company_name: Optional[str] = None


@app.get("/settings")
def get_settings() -> dict:
    db = SessionLocal()
    try:
        rows = db.query(AppSetting).all()
        stored = {r.key: r.value for r in rows}
        return {key: stored.get(key, "") for key in sorted(ALLOWED_SETTING_KEYS)}
    finally:
        db.close()


@app.put("/settings")
def update_settings(body: SettingsUpdateRequest, admin: CurrentUser = Depends(require_admin)) -> dict:
    db = SessionLocal()
    try:
        for key, value in body.model_dump(exclude_none=True).items():
            if key not in ALLOWED_SETTING_KEYS:
                continue
            row = db.get(AppSetting, key)
            if row:
                row.value = value.strip()
            else:
                db.add(AppSetting(key=key, value=value.strip()))
        db.commit()
        rows = db.query(AppSetting).all()
        stored = {r.key: r.value for r in rows}
        return {key: stored.get(key, "") for key in sorted(ALLOWED_SETTING_KEYS)}
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
        }
    finally:
        db.close()


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
