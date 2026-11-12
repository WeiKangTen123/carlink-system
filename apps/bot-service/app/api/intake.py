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
    PUT    /intake/{id}/checked             confirm fields, or mark them N/A (field review)
    POST   /intake/{id}/process             start AI sorting + reading in the background
    POST   /intake/{id}/finalize            turn the draft into a case

The Telegram/WhatsApp bots and the edit form keep using draft_report directly.
"""
import copy
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, or_, update
from sqlalchemy.exc import IntegrityError

from app.ai import keys as ai_keys
from app.ai import runs as run_log
from app.ai.keys import AiActor
from app.api import limits
from app.auth.core import CurrentUser, audit
from app.auth.deps import ApiError, current_user, editable_case, not_allowed, visible_case
from app.auth.permissions import PUBLIC_CHANNEL, in_inbox, is_staff
from app.intake import pipeline, public
from app.intake.photos import (
    copy_into_case,
    intake_dir,
    is_readable_image,
    ordered_photos,
    photo_label,
    remove_intake_dir,
    serialize_photo,
)
from app.reports import owners
from app.reports.db import SessionLocal
from app.reports.filing import PHOTO_INDEX_KEYS, ai_read_for, file_case_data
from app.reports import review
from app.reports.models import IntakePhoto, IntakeSession, Report, User, gen_id, iso_utc
from app.reports.schema import SecurityIncidentDraft, SignOffInfo
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
    is merged into the form when it finishes (pipeline.merge_redraft).
    mutating also refuses a public report back in the inbox since the
    route's edit check (released a moment ago): nobody changes those."""
    report = db.get(Report, report_id)
    session = db.get(IntakeSession, report_id)
    # A filed case first: filing deletes the session row, so checking it
    # first answered 404 -- and the wizard, polling a draft being filed (or
    # retrying a finalize whose answer was lost), looks for these words to
    # go to the case page instead.
    if report is not None and report.status != "intake":
        raise HTTPException(status_code=409, detail="This intake has already been turned into a case")
    if report is None or session is None:
        raise HTTPException(status_code=404, detail="Intake draft not found")
    if mutating and in_inbox(report):
        raise not_allowed(BACK_IN_INBOX)
    # A filing cut off by a restart reads as failed (_state), so the draft
    # can be worked on and filed again rather than staying locked for good.
    if mutating and session.stage == "finalizing" and not pipeline.filing_interrupted(session):
        raise HTTPException(status_code=409, detail="This intake is being turned into a case.")
    if block_while_running and session.stage in pipeline.RUNNING_STAGES and pipeline.is_running(report_id):
        raise HTTPException(status_code=409, detail="Wait for the AI to finish reading this case.")
    return report, session


def _claim_stage(db, report_id: str, new_stage: str, seen_owner: str | None) -> bool:
    """Atomically moves the draft to new_stage unless finalize already holds
    it. A plain read-then-write let two finalizes (two tabs, a client retry)
    both proceed; the second then created the case with no photos.

    Also only while the draft is still seen_owner's (as _load_draft read
    it) and not in the Public Inbox: a public draft released (POST
    /inbox/{id}/release) between the route's checks and here gets no AI run
    and isn't filed (review M4). Release refuses once this has committed.

    A "finalizing" left by a filing that a restart cut off
    (pipeline.filing_interrupted) may be claimed again. stage_started_at is
    set on every claim: it is what tells a live filing from a stale one, and
    the moment a claimed run's stage began (the pipeline's own _set_stage
    only stamps a change of stage)."""
    owner_is = Report.owner_id.is_(None) if seen_owner is None else Report.owner_id == seen_owner
    started = func.coalesce(IntakeSession.stage_started_at, IntakeSession.updated_at)
    result = db.execute(
        update(IntakeSession)
        .where(
            IntakeSession.report_id == report_id,
            or_(IntakeSession.stage != "finalizing", started < pipeline.finalize_cutoff()),
            exists().where(Report.id == report_id, owner_is, public.not_untaken_clause()),
        )
        .values(stage=new_stage, progress_done=0, progress_total=0, error=None, error_code=None, error_by=None,
                stage_started_at=datetime.now(timezone.utc))
    )
    db.commit()
    return result.rowcount == 1


# A public draft put back in the inbox (or given to someone else) after the
# route's checks: _load_draft and _claim_refused.
BACK_IN_INBOX = "This report is back in the Public Inbox: take it again to work on it."
CHANGED_HANDS = "This draft changed hands a moment ago. Reload it."


def _claim_refused(db, report_id: str, busy: str) -> HTTPException:
    """Why _claim_stage changed nothing: busy (finalize holds it), 404 (it
    is gone), or 409 inbox_changed (released or taken over meanwhile)."""
    db.rollback()
    # Columns, not the rows _load_draft loaded (one may be gone by now).
    report = db.query(Report.status, Report.channel, Report.owner_id).filter(Report.id == report_id).first()
    session = (
        db.query(IntakeSession.stage, IntakeSession.stage_started_at, IntakeSession.updated_at)
        .filter(IntakeSession.report_id == report_id)
        .first()
    )
    # Filed meanwhile (filing deletes the session row): the wizard's "open
    # the case" answer, not a 404 -- the session check alone said "not found"
    # when a second File case landed as the first one committed.
    if report is not None and report.status != "intake":
        return HTTPException(status_code=409, detail="This intake has already been turned into a case")
    if report is None or session is None:
        return HTTPException(status_code=404, detail="Intake draft not found")
    if (session.stage == "finalizing" and not pipeline.filing_interrupted(session)) or report.status != "intake":
        return HTTPException(status_code=409, detail=busy)
    return ApiError(409, BACK_IN_INBOX if in_inbox(report) else CHANGED_HANDS, "inbox_changed")


def _error_by(db, user_id: str | None) -> dict | None:
    """{id, name, username} of who started the run that failed, or None."""
    if not user_id:
        return None
    who = db.get(User, user_id)
    return {"id": user_id, "name": who.name if who else None, "username": (who.username or None) if who else None}


def _sorted_photos(photos: list[IntakePhoto]) -> list[dict]:
    """The draft's photos as a photo index ({label, kind}), excluded ones
    left out: which photo the AI read a cover field from."""
    return [{"label": photo_label(i), "kind": p.kind} for i, p in enumerate(photos) if not p.excluded]


def _draft_review(report: Report, session: IntakeSession, sorted_photos: list[dict]) -> dict:
    """review.review_state for a draft. Before the AI's first reading every
    value is a person's (or the firm's usual wording), so a draft with no
    AI draft counts as an empty one ({}), not as an old case's unknown."""
    try:
        return review.review_state(report.data, session.ai_draft or {}, sorted_photos, signed_off=False)
    except Exception:
        logger.exception("Could not work out the field review of draft %s", report.id)
        return None


def _state(db, report: Report, session: IntakeSession, user: CurrentUser) -> dict:
    # A run or a filing a restart cut off reads as failed (pipeline.shown_state).
    stage, error, error_code, error_by = pipeline.shown_state(session)
    photos = ordered_photos(db, report.id)
    # The AI's own values for the cover fields and the sorted photo each
    # was read from, so the wizard's cover page can tag them "(P03)";
    # null / {} before a read. From IntakeSession.ai_draft alone (never a
    # public sender's details), like GET /reports/{id} after filing.
    sorted_photos = _sorted_photos(photos)
    ai_read, ai_read_photos = ai_read_for(session.ai_draft, sorted_photos)
    return {
        "id": report.id,
        "status": report.status,
        "created_at": iso_utc(report.created_at),
        "ai_read": ai_read,
        "ai_read_photos": ai_read_photos,
        # Field review (app/reports/review.py): per field whether the AI
        # read it, it is to check, it needs a person, or a person confirmed
        # it (_draft_review).
        "review": _draft_review(report, session, sorted_photos),
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
        # {model, main_model, main_spent_until} when the newest reading was
        # done by a backup model because the main one's free day was used
        # up (ai/runs.read_by): the review step says so. Else None.
        "read_by": run_log.read_by(db, report.id),
        "photos": [serialize_photo(p, photo_label(i)) for i, p in enumerate(photos)],
        "data": report.data or {},
        "limits": {"max_photos": MAX_PHOTOS_PER_CASE, "max_photo_bytes": MAX_PHOTO_SIZE_BYTES},
        **owners.access_fields(db, report, user),
        # Who sent it, on a report from the public (P6); else None. The
        # wizard sends an untaken one (no owner) to /inbox/{id}.
        "public_submission": public.submission_info(db, report),
    }


def _shared_open_drafts(db) -> int:
    """Open drafts that count toward MAX_OPEN_DRAFTS: all but those owned by
    admins and ticked staff (limits.counts_toward_shared_drafts, in SQL),
    and never public reports nobody has taken: up to public.MAX_OPEN of
    those would otherwise stop every plain user's New Intake (review H2).
    NULL-safe, so an older row with no channel still counts."""
    staff = or_(User.role == "admin", User.sees_all_cases.is_(True), User.may_sign_off.is_(True))
    return (
        db.query(func.count(IntakeSession.report_id))
        .outerjoin(Report, Report.id == IntakeSession.report_id)
        .outerjoin(User, User.id == Report.owner_id)
        .filter(or_(User.id.is_(None), ~staff), public.not_untaken_clause())
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
            # The static check starts with the firm's usual wording, for the
            # surveyor to check (review.apply_template_defaults).
            data=review.apply_template_defaults(
                SecurityIncidentDraft(description="", category=["Vehicle Collision or Damage"]).model_dump()
            ),
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


# Moved to intake/photos.py (P6: public Telegram reports use it too).
_is_readable_image = is_readable_image


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
        keep_review_marks(data, report.data, user)
        report.data = data
        if body.reporter_notes is not None:
            session.reporter_notes = body.reporter_notes
        db.commit()
        return {"ok": True}
    finally:
        db.close()


def keep_review_marks(data: dict, stored: dict | None, user: CurrentUser) -> None:
    """A whole-draft save (autosave, finalize) carries the browser's copy of
    data.checked: the keys it sends are the confirmed ones, stamped by the
    server (review.merged_checked: a mark kept as stored, a new one as
    this person's, now); none sent keeps the stored marks. template_fields
    is the server's: always the stored list."""
    stored = stored or {}
    checked = review.merged_checked(data.get("checked"), stored.get("checked"), user)
    if not is_staff(user):
        # Our Ref's mark is staff's: a non-staff save neither adds nor drops it.
        checked.pop("report_id", None)
        before = review.checked_of(stored).get("report_id")
        if before:
            checked["report_id"] = before
    data["checked"] = checked or None
    data["template_fields"] = stored.get("template_fields") or None


class CheckedMark(BaseModel):
    na: bool = False


class CheckedChange(BaseModel):
    """PUT .../checked (here and api/annexes.py): confirm the `set` keys
    (na: the field rightly stays blank), forget the marks of the `clear`
    keys."""
    set: dict[str, CheckedMark] = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list, max_length=len(review.CHECKABLE_KEYS))


def checked_change(stored: dict | None, body: CheckedChange, user: CurrentUser) -> tuple[dict, str]:
    """(data.checked after the change, the history detail). 400
    unknown_field for a key that isn't a reviewed field
    (review.CHECKABLE_KEYS: FIELD_RULES, "annex_b", "annex_c.agreed_lump_sum",
    "annex_c.repair_days"), 400 set_and_cleared for one sent in both."""
    unknown = sorted({*body.set, *body.clear} - review.CHECKABLE_KEYS)
    if unknown:
        raise ApiError(400, f"Not a field that can be confirmed: {', '.join(unknown[:5])}", "unknown_field")
    both = sorted(set(body.set) & set(body.clear))
    if both:
        raise ApiError(400, f"Both confirmed and cleared: {', '.join(both[:5])}", "set_and_cleared")
    if ("report_id" in body.set or "report_id" in body.clear) and not is_staff(user):
        raise ApiError(403, "Only staff can confirm Our Ref.", "not_allowed")
    checked = review.changed_checked(review.checked_of(stored), {k: v.na for k, v in body.set.items()}, body.clear, user)
    n, m = len(body.set), len(body.clear)
    detail = ", ".join(p for p in (
        f"confirmed {n} field{'s' if n != 1 else ''}" if n else "",
        f"unconfirmed {m} field{'s' if m != 1 else ''}" if m else "",
    ) if p)
    return checked, detail


@router.put("/{report_id}/checked")
def set_intake_checked(report_id: str, body: CheckedChange, user: CurrentUser = Depends(editable_case)) -> dict:
    """Confirms draft fields (or marks them N/A) for the wizard, as PUT
    /reports/{id}/checked does for a case: the draft's owner or an admin
    (editable_case: "see all" staff only look; nobody touches a public
    report still in the inbox). Refused while the AI reads the draft, like
    the autosave. Answers data.checked and the new review."""
    db = SessionLocal()
    try:
        report, session = _load_draft(db, report_id, mutating=True, block_while_running=True)
        checked, detail = checked_change(report.data, body, user)
        report.data = {**(report.data or {}), "checked": checked or None}
        audit(db, user, "fields_confirmed", report.id, detail=detail)
        db.commit()
        return {"checked": checked, "review": _draft_review(report, session, _sorted_photos(ordered_photos(db, report.id)))}
    finally:
        db.close()


class ProcessRequest(BaseModel):
    mode: Literal["all", "read"] = "all"


@router.post("/{report_id}/process")
def process_intake(report_id: str, body: ProcessRequest, user: CurrentUser = Depends(editable_case)) -> dict:
    db = SessionLocal()
    try:
        report, session = _load_draft(db, report_id, mutating=True)
        # Whoever presses Start or Retry pays -- not the draft's owner -- and
        # the run keeps who that was, not a key (intake/pipeline.py). On a
        # public report the actor carries the stricter key rule
        # (ai/keys.resolve_keys, public_case) to every call of the run.
        actor = AiActor.for_user(user.id, "dashboard", public_case=report.channel == PUBLIC_CHANNEL)
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
            seen_owner = report.owner_id
            if not _claim_stage(db, report_id, "sorting" if body.mode == "all" else "reading", seen_owner):
                raise _claim_refused(db, report_id, "This intake is being turned into a case.")
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
        # Put back as they were on a failure: the claim stamps
        # stage_started_at, and a retry of an interrupted filing must still
        # read as interrupted afterwards, not as a filing in progress.
        prior_stage, prior_started = session.stage, session.stage_started_at
        seen_owner = report.owner_id
        if not _claim_stage(db, report_id, "finalizing", seen_owner):
            raise _claim_refused(db, report_id, "This intake is already being turned into a case.")
        try:
            _finalize_claimed(db, report_id, body, user)
        except Exception:
            db.rollback()
            db.execute(
                update(IntakeSession)
                .where(IntakeSession.report_id == report_id)
                .values(stage=prior_stage, stage_started_at=prior_started)
            )
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


def _with_final_labels(ai_draft: dict | None, index_of: dict) -> dict | None:
    """The AI's draft with its "@<photo id>" references (pipeline.py keeps
    them so until the order is fixed) turned into the case's P-numbers,
    with the same map the data gets -- the IntakePhoto rows are deleted at
    filing, so "@id" would point at nothing."""
    if not ai_draft:
        return ai_draft
    out = copy.deepcopy(ai_draft)
    for item in out.get("damage_summary") or []:
        ref = str(item.get("photo_reference") or "")
        if ref.startswith("@"):
            index = index_of.get(ref[1:])
            item["photo_reference"] = photo_label(index) if index is not None else None
            if index is None:
                item["bbox_2d"] = None
        if item.get("seen_in"):
            item["seen_in"] = _final_refs(item["seen_in"], index_of)
    return out


def _final_refs(refs: list, index_of: dict) -> list[str]:
    """Contract 8 D2: a damage item's seen_in (intake/zones.py) as the
    case's P-numbers, the way its photo_reference is turned: "@<photo id>"
    to its final label, a photo deleted since dropped."""
    out: list[str] = []
    for ref in refs or []:
        ref = str(ref or "")
        if ref.startswith("@"):
            index = index_of.get(ref[1:])
            if index is None:
                continue
            ref = photo_label(index)
        if ref and ref not in out:
            out.append(ref)
    return out


def _finalize_claimed(db, report_id: str, body: "FinalizeRequest", user: CurrentUser) -> None:
    report = db.get(Report, report_id)
    session = db.get(IntakeSession, report_id)
    photos = [p for p in ordered_photos(db, report_id) if Path(p.path).exists()]
    index_of = {p.id: i for i, p in enumerate(photos)}
    ai_draft = _with_final_labels(session.ai_draft, index_of)

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
        if item.seen_in:
            item.seen_in = _final_refs(item.seen_in, index_of)
    if draft.damage_summary:
        draft.damaged_parts = [item.part for item in draft.damage_summary]

    data = draft.model_dump()
    # The confirmations as the wizard last sent them, stamped against the
    # stored ones; the template list as stored (review).
    keep_review_marks(data, report.data, user)
    # The filing step every channel shares (app/reports/filing.py): sign-off
    # blank, annexes rebuilt from the confirmed damage list and Annex A
    # priced for someone who may see the price book, photo_index from the
    # sort step (same order as `photos`, so P-numbers line up).
    # The owner may be someone else when an admin finishes their draft:
    # then the price book goes in only if the owner may see it too.
    owner = db.get(User, report.owner_id) if report.owner_id and report.owner_id != user.id else None
    # Which photos the AI's last reading was sent: read_photo_ids goes with
    # the session row below, so the case keeps it as photo_index[i].read,
    # and the run log names that reading's photos by their final P-numbers.
    read_ids = list(session.read_photo_ids or [])
    data = file_case_data(
        db, data, user, ai_draft=ai_draft,
        photo_kinds=[{"id": p.id, **{key: getattr(p, key) for key in PHOTO_INDEX_KEYS}} for p in photos],
        owner=owner, report_id=report_id, read_ids=set(read_ids),
    )
    label_of = {p.id: photo_label(i) for i, p in enumerate(photos)}
    run_log.relabel_last_read(db, report_id, read_ids, label_of)
    # Contract 8 D2: the zone calls' photos, kept as "@<photo id>" until now.
    run_log.relabel_zone_reads(db, report_id, label_of)

    new_paths = copy_into_case(report_id, photos)
    report.photo_paths = new_paths
    report.data = data
    # What the AI said, kept apart from the surveyor's version (ai_read).
    report.ai_draft = ai_draft
    report.status = "confirmed"
    report.pdf_path = None
    db.query(IntakePhoto).filter(IntakePhoto.report_id == report_id).delete()
    db.delete(session)
    # A public report: filed_at, the sender's Telegram details forgotten,
    # reporter_chat_id "public" (public.on_finalize). None otherwise.
    reference = public.on_finalize(db, report_id)
    audit(db, user, "finalized", report_id, detail=reference or "")
    db.commit()


def delete_intake_files(db, report_id: str) -> str | None:
    """Called by DELETE /reports/{id} for a draft, whose photos live under
    storage/intake/ rather than the incident folder that route removes.
    Also deletes a public report's submission row (the sender's details);
    returns its reference for the history entry, else None. (A taken public
    draft goes through public.delete_submission instead.)"""
    db.query(IntakePhoto).filter(IntakePhoto.report_id == report_id).delete()
    session = db.get(IntakeSession, report_id)
    if session:
        db.delete(session)
    reference = public.delete_row(db, report_id)
    remove_intake_dir(report_id)
    return reference
