"""Dashboard case intake (the New Intake wizard).

Replaces the single-request /reports/analyze-photos flow for the dashboard,
which couldn't take a real case: the four sample cases are 41-78 photos and
88-194MB each, against a 25MB request limit and one 45s Gemini call.

    POST   /intake                          create a draft (a Report with status "intake")
    GET    /intake/{id}                     draft, photos and processing progress
    POST   /intake/{id}/photos              upload ONE photo (the browser sends several in parallel)
    PATCH  /intake/{id}/photos/{photo_id}   surveyor regroups / excludes a photo
    DELETE /intake/{id}/photos/{photo_id}
    PUT    /intake/{id}/data                autosave the form
    POST   /intake/{id}/process             start AI sorting + reading in the background
    POST   /intake/{id}/finalize            turn the draft into a case

The Telegram/WhatsApp bots and the edit form keep using draft_report directly.
"""
import hashlib
import logging
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, or_, update
from sqlalchemy.exc import IntegrityError

from app.ai import keys as ai_keys
from app.ai.keys import AiActor
from app.api import limits
from app.auth.core import CurrentUser, audit
from app.auth.deps import current_user, editable_case, visible_case
from app.intake import pipeline
from app.intake.photos import (
    copy_into_case,
    intake_dir,
    ordered_photos,
    photo_label,
    remove_intake_dir,
    serialize_photo,
)
from app.reports import owners
from app.reports.db import SessionLocal
from app.reports.models import IntakePhoto, IntakeSession, Report, User, gen_id, iso_utc
from app.reports.schema import SecurityIncidentDraft, SignOffInfo
from app.reports.synthesize_annexes import synthesize_annexes_for_report
from app.reports.taxonomy import body_type_for
from app.rendering.renderer import RenderBusy, render_pdf
from app.storage.files import generate_preview, generate_thumbnail, report_pdf_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/intake", tags=["intake"])

ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAX_PHOTO_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB, same as /reports/analyze-photos
MAX_PHOTOS_PER_CASE = 150
# Real cases are 90-200MB; this leaves room for a big one while stopping a
# single draft from taking 150 x 25MB = 3.75GB of a shared VM's disk.
MAX_DRAFT_BYTES = 1536 * 1024 * 1024
# Open drafts across everyone but admins and ticked staff (limits.
# counts_toward_shared_drafts); each person also has their own cap
# (limits.MAX_OPEN_DRAFTS_PER_PERSON).
MAX_OPEN_DRAFTS = 30
_CHUNK = 1024 * 1024


def _load_draft(db, report_id: str, *, mutating: bool = False, block_while_running: bool = False) -> tuple[Report, IntakeSession]:
    """mutating: refuse while finalize is turning the draft into a case --
    an upload or autosave landing then would write into a folder finalize
    is about to delete, or overwrite the finished case's data.
    block_while_running: also refuse while the AI is working, whose result
    is merged into the form when it finishes (pipeline.merge_redraft)."""
    report = db.get(Report, report_id)
    session = db.get(IntakeSession, report_id)
    if report is None or session is None:
        raise HTTPException(status_code=404, detail="Intake draft not found")
    if report.status != "intake":
        raise HTTPException(status_code=409, detail="This intake has already been turned into a case")
    if mutating and session.stage == "finalizing":
        raise HTTPException(status_code=409, detail="This intake is being turned into a case.")
    if block_while_running and session.stage in pipeline.RUNNING_STAGES and pipeline.is_running(report_id):
        raise HTTPException(status_code=409, detail="Wait for the AI to finish reading this case.")
    return report, session


def _claim_stage(db, report_id: str, new_stage: str) -> bool:
    """Atomically moves the draft to new_stage unless finalize already holds
    it. A plain read-then-write let two finalizes (two tabs, a client retry)
    both proceed; the second then created the case with no photos."""
    result = db.execute(
        update(IntakeSession)
        .where(IntakeSession.report_id == report_id, IntakeSession.stage != "finalizing")
        .values(stage=new_stage, progress_done=0, progress_total=0, error=None, error_code=None, error_by=None)
    )
    db.commit()
    return result.rowcount == 1


def _error_by(db, user_id: str | None) -> dict | None:
    """{id, name, username} of who started the run that failed, or None."""
    if not user_id:
        return None
    who = db.get(User, user_id)
    return {"id": user_id, "name": who.name if who else None, "username": (who.username or None) if who else None}


def _state(db, report: Report, session: IntakeSession, user: CurrentUser) -> dict:
    stage, error, error_code, error_by = session.stage, session.error, session.error_code, session.error_by
    # Left mid-run by a restart: the thread is gone but the row still says
    # it's working, and the wizard would poll forever.
    if stage in pipeline.RUNNING_STAGES and not pipeline.is_running(report.id):
        stage = "failed"
        error = (
            "Processing stopped before finishing (the server restarted). "
            "Press Retry to continue -- photos already sorted are kept."
        )
        error_code = error_by = None
    photos = ordered_photos(db, report.id)
    return {
        "id": report.id,
        "status": report.status,
        "created_at": iso_utc(report.created_at),
        "stage": stage,
        "progress": {"done": session.progress_done, "total": session.progress_total},
        "stage_started_at": iso_utc(session.stage_started_at) if session.stage_started_at else None,
        "error": error,
        # Why the last run stopped when the person's AI wasn't available
        # (ai/keys.AiUnavailable's codes), else None.
        "error_code": error_code,
        # Whose run that was: the error is about THEIR key, so the wizard
        # words it for the viewer ("your AI key" only when error_mine).
        "error_by": _error_by(db, error_by),
        "error_mine": bool(error_by) and error_by == user.id,
        "reporter_notes": session.reporter_notes,
        "read_photo_ids": session.read_photo_ids or [],
        "photos": [serialize_photo(p, photo_label(i)) for i, p in enumerate(photos)],
        "data": report.data or {},
        "limits": {"max_photos": MAX_PHOTOS_PER_CASE, "max_photo_bytes": MAX_PHOTO_SIZE_BYTES},
        **owners.access_fields(db, report, user),
    }


def _shared_open_drafts(db) -> int:
    """Open drafts that count toward MAX_OPEN_DRAFTS: all but those owned by
    admins and ticked staff (limits.counts_toward_shared_drafts, in SQL)."""
    staff = or_(User.role == "admin", User.sees_all_cases.is_(True), User.may_sign_off.is_(True))
    return (
        db.query(func.count(IntakeSession.report_id))
        .outerjoin(Report, Report.id == IntakeSession.report_id)
        .outerjoin(User, User.id == Report.owner_id)
        .filter(or_(User.id.is_(None), ~staff))
        .scalar()
    )


@router.post("")
def create_intake(user: CurrentUser = Depends(current_user)) -> dict:
    limits.require_disk_space()
    db = SessionLocal()
    try:
        # Each draft can hold MAX_DRAFT_BYTES; an unbounded number of them is
        # an unbounded amount of disk on a VM shared with other apps.
        # Abandoned drafts are swept after a week (intake/cleanup.py).
        limits.check_open_drafts(db, user)
        limits.check_new_cases_today(db, user)
        if limits.counts_toward_shared_drafts(user) and _shared_open_drafts(db) >= MAX_OPEN_DRAFTS:
            raise HTTPException(
                status_code=409,
                detail=f"There are already {MAX_OPEN_DRAFTS} unfinished intakes. Finish or delete some first.",
            )
        report = Report(
            channel="manual",
            reporter_chat_id="dashboard",
            status="intake",
            # The draft, and the case it becomes, are the caller's -- also
            # when an admin finishes it for them.
            owner_id=user.id,
            # Full default shape, not a stub: the Cases Repository lists and
            # previews drafts like any other report, and reads nested fields
            # (people_involved, insurance_details...) without guarding.
            data=SecurityIncidentDraft(description="", category=["Vehicle Collision or Damage"]).model_dump(),
        )
        db.add(report)
        db.flush()
        db.add(IntakeSession(report_id=report.id))
        audit(db, user, "created", report.id)
        db.commit()
        return {"id": report.id}
    finally:
        db.close()


@router.get("/{report_id}")
def get_intake(report_id: str, user: CurrentUser = Depends(visible_case)) -> dict:
    db = SessionLocal()
    try:
        report, session = _load_draft(db, report_id)
        return _state(db, report, session, user)
    finally:
        db.close()


@router.post("/{report_id}/photos")
def upload_intake_photo(
    report_id: str,
    file: UploadFile = File(...),
    position: int = Form(0),
    user: CurrentUser = Depends(editable_case),
) -> dict:
    """One photo per request, so a 150-photo case is 150 small requests the
    browser can run a few at a time and retry one by one, rather than one
    request that has to fit every photo at once.

    Plain `def`: FastAPI runs it in its threadpool, which keeps the hashing
    and thumbnail/preview generation off the event loop.
    """
    ext = Path(file.filename or "").suffix.lower() or ".jpg"
    if ext not in ALLOWED_PHOTO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{ext}'. Allowed formats: {', '.join(sorted(ALLOWED_PHOTO_EXTENSIONS))}",
        )
    limits.require_disk_space()

    db = SessionLocal()
    try:
        _load_draft(db, report_id, mutating=True)
        if db.query(IntakePhoto).filter(IntakePhoto.report_id == report_id).count() >= MAX_PHOTOS_PER_CASE:
            raise HTTPException(status_code=409, detail=f"A case can hold at most {MAX_PHOTOS_PER_CASE} photos.")
        used = db.query(func.coalesce(func.sum(IntakePhoto.size_bytes), 0)).filter(IntakePhoto.report_id == report_id).scalar()
        if used >= MAX_DRAFT_BYTES:
            raise HTTPException(status_code=409, detail="This intake has reached its storage limit.")

        photo_id = gen_id()
        dest = intake_dir(report_id) / f"{photo_id}{ext}"
        digest = hashlib.sha256()
        size = 0
        with open(dest, "wb") as out:
            while chunk := file.file.read(_CHUNK):
                size += len(chunk)
                if size > MAX_PHOTO_SIZE_BYTES:
                    break
                digest.update(chunk)
                out.write(chunk)
        if size > MAX_PHOTO_SIZE_BYTES:
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=413, detail=f"'{file.filename}' is over the 25MB limit.")

        sha = digest.hexdigest()
        duplicate = (
            db.query(IntakePhoto)
            .filter(IntakePhoto.report_id == report_id, IntakePhoto.sha256 == sha)
            .first()
        )
        if duplicate:
            # The same file dropped twice (common when adding a second batch
            # from the same folder). Not an error -- the browser shows it as
            # skipped.
            dest.unlink(missing_ok=True)
            return {"duplicate_of": duplicate.id}

        # Extension checks alone let anything through under a .jpg name. Only
        # files Pillow can actually decode as an image are kept (HEIC via
        # pillow-heif, registered in storage/files.py).
        if not _is_readable_image(dest):
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"'{file.filename}' isn't a readable photo.")

        generate_thumbnail(str(dest))
        generate_preview(str(dest))
        photo = IntakePhoto(
            id=photo_id,
            report_id=report_id,
            position=position,
            original_name=(file.filename or "")[:255],
            path=str(dest),
            sha256=sha,
            size_bytes=size,
        )
        db.add(photo)
        try:
            db.commit()
        except IntegrityError:
            # The same file uploaded twice concurrently (the browser sends a
            # few at a time): both passed the query above, and the unique
            # (report_id, sha256) index lets only the first in.
            db.rollback()
            _remove_photo_files(str(dest))
            duplicate = (
                db.query(IntakePhoto)
                .filter(IntakePhoto.report_id == report_id, IntakePhoto.sha256 == sha)
                .first()
            )
            return {"duplicate_of": duplicate.id if duplicate else None}
        return {"photo": serialize_photo(photo, "")}
    finally:
        db.close()


def _is_readable_image(path: Path) -> bool:
    try:
        from PIL import Image

        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:
        return False


def _remove_photo_files(photo_path: str) -> None:
    from app.storage.files import pdf_image_path_for, preview_path_for, thumbnail_path_for

    for path in (Path(photo_path), thumbnail_path_for(photo_path), preview_path_for(photo_path), pdf_image_path_for(photo_path)):
        path.unlink(missing_ok=True)


class PhotoUpdateRequest(BaseModel):
    kind: Optional[str] = None
    excluded: Optional[bool] = None


@router.patch("/{report_id}/photos/{photo_id}")
def update_intake_photo(
    report_id: str, photo_id: str, body: PhotoUpdateRequest, user: CurrentUser = Depends(editable_case)
) -> dict:
    from app.ai.triage import PHOTO_KINDS

    db = SessionLocal()
    try:
        _load_draft(db, report_id, mutating=True, block_while_running=True)
        photo = db.get(IntakePhoto, photo_id)
        if photo is None or photo.report_id != report_id:
            raise HTTPException(status_code=404, detail="Photo not found")
        if body.kind is not None:
            if body.kind not in PHOTO_KINDS:
                raise HTTPException(status_code=400, detail=f"Unknown photo group '{body.kind}'")
            photo.kind = body.kind
            # Never overwritten by a later sorting run.
            photo.kind_source = "surveyor"
        if body.excluded is not None:
            photo.excluded = body.excluded
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.delete("/{report_id}/photos/{photo_id}")
def delete_intake_photo(report_id: str, photo_id: str, user: CurrentUser = Depends(editable_case)) -> dict:
    db = SessionLocal()
    try:
        _load_draft(db, report_id, mutating=True, block_while_running=True)
        photo = db.get(IntakePhoto, photo_id)
        if photo is None or photo.report_id != report_id:
            raise HTTPException(status_code=404, detail="Photo not found")
        _remove_photo_files(photo.path)
        db.delete(photo)
        db.commit()
        return {"deleted": photo_id}
    finally:
        db.close()


class IntakeDataRequest(BaseModel):
    data: dict
    reporter_notes: Optional[str] = None


@router.put("/{report_id}/data")
def save_intake_data(report_id: str, body: IntakeDataRequest, user: CurrentUser = Depends(editable_case)) -> dict:
    db = SessionLocal()
    try:
        report, session = _load_draft(db, report_id, mutating=True, block_while_running=True)
        # Validated so a draft always has the shape every other reader of
        # Report.data (list, analytics, PDF) expects. sign_off is never
        # taken from the browser (see main.blank_sign_off).
        data = SecurityIncidentDraft.model_validate(body.data).model_dump()
        data["sign_off"] = SignOffInfo().model_dump()
        data["report_id"] = limits.checked_our_ref(user, data.get("report_id"), (report.data or {}).get("report_id"))
        report.data = data
        if body.reporter_notes is not None:
            session.reporter_notes = body.reporter_notes
        db.commit()
        return {"ok": True}
    finally:
        db.close()


class ProcessRequest(BaseModel):
    mode: Literal["all", "read"] = "all"


@router.post("/{report_id}/process")
def process_intake(report_id: str, body: ProcessRequest, user: CurrentUser = Depends(editable_case)) -> dict:
    # Whoever presses Start or Retry pays -- not the draft's owner -- and the
    # run keeps who that was, not a key (intake/pipeline.py).
    actor = AiActor.for_user(user.id, "dashboard")
    db = SessionLocal()
    try:
        report, session = _load_draft(db, report_id, mutating=True)
        if db.query(IntakePhoto).filter(IntakePhoto.report_id == report_id).count() == 0 and not session.reporter_notes.strip():
            raise HTTPException(status_code=400, detail="Upload photos or describe what happened first.")
        # After the view (404), edit (403) and input (400) checks, before
        # the stage is touched: 409 {detail, code} (api/main.py).
        ai_keys.ensure_ai_ready(actor)
        # Reserved before the stage is committed (so a GET in between never
        # sees a stage with no run behind it), and the stage committed
        # before the thread starts (whose own progress writes would
        # otherwise be overwritten with these zeros).
        if not pipeline.reserve(report_id):
            raise HTTPException(status_code=409, detail="AI processing is already running for this draft.")
        claimed = False
        try:
            # This person's one AI request at a time (limits.ai_turn), held
            # for the whole run and given back when it ends (on_done).
            limits.claim_ai_turn(user)
            claimed = True
            if not _claim_stage(db, report_id, "sorting" if body.mode == "all" else "reading"):
                raise HTTPException(status_code=409, detail="This intake is being turned into a case.")
            pipeline.start_reserved(report_id, body.mode, actor, on_done=lambda: limits.ai_running.release(user.id))
        except Exception:
            pipeline.release(report_id)
            if claimed:
                limits.ai_running.release(user.id)
            raise
        db.refresh(session)
        return _state(db, report, session, user)
    finally:
        db.close()


class FinalizeRequest(BaseModel):
    data: SecurityIncidentDraft


@router.post("/{report_id}/finalize")
def finalize_intake(report_id: str, body: FinalizeRequest, user: CurrentUser = Depends(editable_case)) -> dict:
    """Turns the draft into a case.

    Ordered so a failure at any point leaves either the untouched draft (and
    Retry works) or the finished case -- never a case missing its photos:
      1. claim the draft (stage "finalizing"), so uploads, autosaves and a
         second finalize are refused from here on;
      2. COPY the photos into the case folder -- the originals stay put
         until step 3 has committed;
      3. one short transaction: case data, photo paths, status, intake rows;
      4. only then delete the draft folder and render the PDF, with no
         database write lock held (rendering 70+ photos takes seconds, and
         holding the lock through it made the Telegram bot's writes fail).
    """
    db = SessionLocal()
    try:
        report, session = _load_draft(db, report_id, mutating=True)
        if pipeline.is_running(report_id):
            raise HTTPException(status_code=409, detail="Wait for AI processing to finish first.")
        body.data.report_id = limits.checked_our_ref(user, body.data.report_id, (report.data or {}).get("report_id"))
        limits.check_new_cases_today(db, user)
        # Finalizing copies every photo into the case folder (up to
        # MAX_DRAFT_BYTES, 1.5 GB): 507 disk_full before the draft is
        # claimed, so it stays as it was and Finish can be tried again.
        limits.require_disk_space()
        prior_stage = session.stage
        if not _claim_stage(db, report_id, "finalizing"):
            raise HTTPException(status_code=409, detail="This intake is already being turned into a case.")
        try:
            _finalize_claimed(db, report_id, body, user)
        except Exception:
            db.rollback()
            db.execute(update(IntakeSession).where(IntakeSession.report_id == report_id).values(stage=prior_stage))
            db.commit()
            raise
    finally:
        db.close()

    remove_intake_dir(report_id)
    _render_case_pdf(report_id, user)
    return {"id": report_id}


def _render_case_pdf(report_id: str, user: CurrentUser) -> None:
    # When this person may not render now (limits.render_turn) or every
    # Chromium slot stays busy, the first download renders it instead.
    db = SessionLocal()
    try:
        report = db.get(Report, report_id)
        try:
            with limits.render_turn(user):
                pdf_path = report_pdf_path(report_id)
                render_pdf(report.data, report.photo_paths, pdf_path, report_id=report_id)
        except (limits.RenderRefused, RenderBusy):
            return
        except Exception as exc:
            # The case exists regardless; GET /reports/{id} and /download
            # render a missing PDF on demand.
            logger.error("Failed to render PDF for finalized intake %s: %s", report_id, exc)
            return
        report.pdf_path = pdf_path
        db.commit()
    finally:
        db.close()


def _finalize_claimed(db, report_id: str, body: "FinalizeRequest", user: CurrentUser) -> None:
    report = db.get(Report, report_id)
    session = db.get(IntakeSession, report_id)
    photos = [p for p in ordered_photos(db, report_id) if Path(p.path).exists()]
    index_of = {p.id: i for i, p in enumerate(photos)}

    draft = body.data
    if draft.vehicle_info:
        v = draft.vehicle_info
        v.body_type = body_type_for(v.make, v.model, v.body_type)
    # "@<photo id>" -> its final P-number, now that the order is fixed.
    for item in draft.damage_summary:
        ref = item.photo_reference or ""
        if ref.startswith("@"):
            index = index_of.get(ref[1:])
            item.photo_reference = photo_label(index) if index is not None else None
            if index is None:
                item.bbox_2d = None
    if draft.damage_summary:
        draft.damaged_parts = [item.part for item in draft.damage_summary]

    data = draft.model_dump()
    # A new case is unsigned, whatever the request carried (main.blank_sign_off).
    data["sign_off"] = SignOffInfo().model_dump()
    # Annex A/B are only synthesized when empty, so the ones drafted from
    # the AI's first damage list would otherwise survive the surveyor
    # changing that list at review. Rebuild them from the confirmed one.
    data["annex_a"] = {}
    data["annex_b"] = {}
    data = synthesize_annexes_for_report(data)

    new_paths = copy_into_case(report_id, photos)
    report.photo_paths = new_paths
    report.data = data
    report.status = "confirmed"
    report.pdf_path = None
    db.query(IntakePhoto).filter(IntakePhoto.report_id == report_id).delete()
    db.delete(session)
    audit(db, user, "finalized", report_id)
    db.commit()


def delete_intake_files(db, report_id: str) -> None:
    """Called by DELETE /reports/{id} for a draft, whose photos live under
    storage/intake/ rather than the incident folder that route removes."""
    db.query(IntakePhoto).filter(IntakePhoto.report_id == report_id).delete()
    session = db.get(IntakeSession, report_id)
    if session:
        db.delete(session)
    remove_intake_dir(report_id)
