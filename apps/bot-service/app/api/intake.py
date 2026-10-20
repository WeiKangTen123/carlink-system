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

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.intake import pipeline
from app.intake.photos import (
    intake_dir,
    move_into_case,
    ordered_photos,
    photo_label,
    remove_intake_dir,
    serialize_photo,
)
from app.reports.db import SessionLocal
from app.reports.models import IntakePhoto, IntakeSession, Report, gen_id
from app.reports.schema import SecurityIncidentDraft
from app.reports.synthesize_annexes import synthesize_annexes_for_report
from app.reports.taxonomy import body_type_for
from app.rendering.renderer import render_pdf
from app.storage.files import generate_preview, generate_thumbnail, report_pdf_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/intake", tags=["intake"])

ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAX_PHOTO_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB, same as /reports/analyze-photos
MAX_PHOTOS_PER_CASE = 150
_CHUNK = 1024 * 1024


def _load_draft(db, report_id: str) -> tuple[Report, IntakeSession]:
    report = db.get(Report, report_id)
    session = db.get(IntakeSession, report_id)
    if report is None or session is None:
        raise HTTPException(status_code=404, detail="Intake draft not found")
    if report.status != "intake":
        raise HTTPException(status_code=409, detail="This intake has already been turned into a case")
    return report, session


def _state(db, report: Report, session: IntakeSession) -> dict:
    stage, error = session.stage, session.error
    # Left mid-run by a restart: the thread is gone but the row still says
    # it's working, and the wizard would poll forever.
    if stage in pipeline.RUNNING_STAGES and not pipeline.is_running(report.id):
        stage = "failed"
        error = (
            "Processing stopped before finishing (the server restarted). "
            "Press Retry to continue -- photos already sorted are kept."
        )
    photos = ordered_photos(db, report.id)
    return {
        "id": report.id,
        "status": report.status,
        "created_at": report.created_at.isoformat(),
        "stage": stage,
        "progress": {"done": session.progress_done, "total": session.progress_total},
        "error": error,
        "reporter_notes": session.reporter_notes,
        "read_photo_ids": session.read_photo_ids or [],
        "photos": [serialize_photo(p, photo_label(i)) for i, p in enumerate(photos)],
        "data": report.data or {},
        "limits": {"max_photos": MAX_PHOTOS_PER_CASE, "max_photo_bytes": MAX_PHOTO_SIZE_BYTES},
    }


@router.post("")
def create_intake() -> dict:
    db = SessionLocal()
    try:
        report = Report(
            channel="manual",
            reporter_chat_id="dashboard",
            status="intake",
            # Full default shape, not a stub: the Cases Repository lists and
            # previews drafts like any other report, and reads nested fields
            # (people_involved, insurance_details...) without guarding.
            data=SecurityIncidentDraft(description="", category=["Vehicle Collision or Damage"]).model_dump(),
        )
        db.add(report)
        db.flush()
        db.add(IntakeSession(report_id=report.id))
        db.commit()
        return {"id": report.id}
    finally:
        db.close()


@router.get("/{report_id}")
def get_intake(report_id: str) -> dict:
    db = SessionLocal()
    try:
        report, session = _load_draft(db, report_id)
        return _state(db, report, session)
    finally:
        db.close()


@router.post("/{report_id}/photos")
def upload_intake_photo(
    report_id: str,
    file: UploadFile = File(...),
    position: int = Form(0),
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

    db = SessionLocal()
    try:
        _load_draft(db, report_id)
        if db.query(IntakePhoto).filter(IntakePhoto.report_id == report_id).count() >= MAX_PHOTOS_PER_CASE:
            raise HTTPException(status_code=409, detail=f"A case can hold at most {MAX_PHOTOS_PER_CASE} photos.")

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
        db.commit()
        return {"photo": serialize_photo(photo, "")}
    finally:
        db.close()


class PhotoUpdateRequest(BaseModel):
    kind: Optional[str] = None
    excluded: Optional[bool] = None


@router.patch("/{report_id}/photos/{photo_id}")
def update_intake_photo(report_id: str, photo_id: str, body: PhotoUpdateRequest) -> dict:
    from app.ai.triage import PHOTO_KINDS

    db = SessionLocal()
    try:
        _load_draft(db, report_id)
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
def delete_intake_photo(report_id: str, photo_id: str) -> dict:
    from app.storage.files import preview_path_for, thumbnail_path_for

    db = SessionLocal()
    try:
        _load_draft(db, report_id)
        photo = db.get(IntakePhoto, photo_id)
        if photo is None or photo.report_id != report_id:
            raise HTTPException(status_code=404, detail="Photo not found")
        for path in (Path(photo.path), thumbnail_path_for(photo.path), preview_path_for(photo.path)):
            path.unlink(missing_ok=True)
        db.delete(photo)
        db.commit()
        return {"deleted": photo_id}
    finally:
        db.close()


class IntakeDataRequest(BaseModel):
    data: dict
    reporter_notes: Optional[str] = None


@router.put("/{report_id}/data")
def save_intake_data(report_id: str, body: IntakeDataRequest) -> dict:
    db = SessionLocal()
    try:
        report, session = _load_draft(db, report_id)
        # Validated so a draft always has the shape every other reader of
        # Report.data (list, analytics, PDF) expects.
        report.data = SecurityIncidentDraft.model_validate(body.data).model_dump()
        if body.reporter_notes is not None:
            session.reporter_notes = body.reporter_notes
        db.commit()
        return {"ok": True}
    finally:
        db.close()


class ProcessRequest(BaseModel):
    mode: Literal["all", "read"] = "all"


@router.post("/{report_id}/process")
def process_intake(report_id: str, body: ProcessRequest) -> dict:
    db = SessionLocal()
    try:
        report, session = _load_draft(db, report_id)
        if db.query(IntakePhoto).filter(IntakePhoto.report_id == report_id).count() == 0 and not session.reporter_notes.strip():
            raise HTTPException(status_code=400, detail="Upload photos or describe what happened first.")
        if pipeline.is_running(report_id):
            raise HTTPException(status_code=409, detail="AI processing is already running for this draft.")
        # Committed before the thread starts, never after: the thread writes
        # its own progress straight away, and a later commit here would
        # overwrite it with these zeros.
        session.stage = "sorting" if body.mode == "all" else "reading"
        session.progress_done = 0
        session.progress_total = 0
        session.error = None
        db.commit()
        if not pipeline.start(report_id, body.mode):
            raise HTTPException(status_code=409, detail="AI processing is already running for this draft.")
        return _state(db, report, session)
    finally:
        db.close()


class FinalizeRequest(BaseModel):
    data: SecurityIncidentDraft


@router.post("/{report_id}/finalize")
def finalize_intake(report_id: str, body: FinalizeRequest) -> dict:
    db = SessionLocal()
    try:
        report, session = _load_draft(db, report_id)
        if pipeline.is_running(report_id):
            raise HTTPException(status_code=409, detail="Wait for AI processing to finish first.")

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
        # Annex A/B are only synthesized when empty, so the ones drafted from
        # the AI's first damage list would otherwise survive the surveyor
        # changing that list at review. Rebuild them from the confirmed one.
        data["annex_a"] = {}
        data["annex_b"] = {}
        data = synthesize_annexes_for_report(data)

        report.photo_paths = move_into_case(report_id, photos)
        report.data = data
        report.status = "confirmed"
        db.query(IntakePhoto).filter(IntakePhoto.report_id == report_id).delete()
        db.delete(session)

        pdf_path = report_pdf_path(report.id)
        try:
            render_pdf(report.data, report.photo_paths, pdf_path, report_id=report.id)
            report.pdf_path = pdf_path
        except Exception as exc:
            logger.error("Failed to render PDF for finalized intake %s: %s", report.id, exc)
            report.pdf_path = None

        db.commit()
        remove_intake_dir(report_id)
        return {"id": report.id}
    finally:
        db.close()


def delete_intake_files(db, report_id: str) -> None:
    """Called by DELETE /reports/{id} for a draft, whose photos live under
    storage/intake/ rather than the incident folder that route removes."""
    db.query(IntakePhoto).filter(IntakePhoto.report_id == report_id).delete()
    session = db.get(IntakeSession, report_id)
    if session:
        db.delete(session)
    remove_intake_dir(report_id)
