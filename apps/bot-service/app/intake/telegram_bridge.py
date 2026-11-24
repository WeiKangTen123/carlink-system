"""Staff photos from the Telegram bot through the website's intake pipeline
(contract 10, agent T: settings.telegram_full_detection).

Off (the default), the bot drafts a staff report with one drafting call
(conversation/flow.build_draft) and files it on Confirm, as before. On, a
staff report is handed over instead as an intake draft -- the same rows the
New Intake wizard makes: a Report with status "intake", its IntakeSession
and one IntakePhoto per photo -- and the same pipeline runs on it in the
background (intake/pipeline.py: sort, main read, zone reads, second look
with the linked parts, re-check), so a photo sent to the bot gets the same
detection as one uploaded on the website. The person then finishes it on
the website: checks the list, decides the suggestions, files the case.

The run is in the bot's own process (pipeline.reserve / start_reserved,
whose in-memory "running" set is per process): while it runs, the run's own
beat (pipeline.keep_beating, every 30 s, from start()) keeps the draft's
run_heartbeat_at fresh, which is how the api process tells a live run in
the bot from one a restart cut off (pipeline.is_live: the wizard shows it
reading, Retry / autosave / decisions / File case wait, the sweep leaves it).

Database and file work only, each call in its own session; run them off
the event loop. The chat side -- replies, the chat lock, who the person
is -- stays in channels/telegram.py.
"""
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from fastapi import HTTPException
from sqlalchemy import func, update

from app.ai.keys import AiActor
from app.auth.core import audit
from app.intake import pipeline
from app.intake.photos import intake_dir, is_readable_image, remove_intake_dir
from app.reports import review
from app.reports.db import SessionLocal
from app.reports.models import IntakePhoto, IntakeSession, Report, User, gen_id
from app.reports.schema import CoverPageInfo, InsuranceDetails, SecurityIncidentDraft, VehicleInfo
from app.storage.files import (
    compress_original,
    disk_space_low,
    generate_preview,
    generate_thumbnail,
    photo_taken_at,
)

logger = logging.getLogger(__name__)

CHANNEL = "telegram"
# The draft's "created" history entry (the case keeps it after filing).
CREATED_DETAIL = "Sent from the Telegram bot for a full reading"
# Photos the bot keeps in one draft (channels/telegram.MAX_PHOTOS_PER_DRAFT)
# are well under the website's 150; checked here too, so a caller can't
# hand over more than a draft holds.
MAX_PHOTOS = 150
DISK_LOW = "Sorry, the server is low on storage, so I can't save these photos right now. Please tell your Carlink admin."
NO_PHOTOS = "None of the photos for this report could be read. Please send them again."
NOT_STARTED = "The Telegram bot couldn't start reading these photos. Press Retry."
_CHUNK = 1024 * 1024


@dataclass
class Created:
    """create_draft's answer: the draft's id and how many photos it holds,
    or why there is none. linked False: the Telegram isn't connected to
    that account any more (the caller drops the chat's draft, as Confirm
    does)."""
    report_id: Optional[str] = None
    photos: int = 0
    skipped: int = 0
    problem: Optional[str] = None
    linked: bool = True


@dataclass
class Outcome:
    """What became of a run, for the chat's summary (outcome()).

    state: "ready" (read), "failed" (the run stopped: error and error_code
    as the wizard shows them), "running" (still at it), "gone" (the draft
    was deleted meanwhile), "filed" (it became a case meanwhile)."""
    state: str
    error: Optional[str] = None
    error_code: Optional[str] = None
    photos: int = 0
    read: int = 0
    parts: list = field(default_factory=list)  # the damage list's items
    suggestions: list = field(default_factory=list)  # the open second-look items
    vehicle: dict = field(default_factory=dict)
    # F1 (settings.intake_main_read_fallback): the notice of a list made
    # from the zone reads because the main read failed (pipeline.
    # read_notice), else None. The chat says it instead of "Finished
    # reading".
    read_notice: Optional[str] = None


# ----------------------------------------------------------------- the draft


def initial_data(facts: dict) -> dict:
    """Report.data for a new draft: as the wizard starts one (POST /intake:
    the firm's usual wording for the static check), with the facts the
    reporter typed into the bot's template. The reading's merge keeps them
    (pipeline.merge_redraft: cover page and insurance are the surveyor's
    sections, a typed plate, place or time differs from what the AI said
    before, which was nothing) -- the template wins over the AI, as on the
    bot's first draft (flow.build_draft)."""
    vehicle = VehicleInfo(plate_number=facts.get("vehicle_plate") or None,
                          body_type=facts.get("vehicle_body_type") or None)
    insurance = None
    if facts.get("insurer_name") or facts.get("workshop_name") or facts.get("claim_type"):
        insurance = InsuranceDetails(insurer_name=facts.get("insurer_name") or None,
                                     workshop_assigned=facts.get("workshop_name") or None,
                                     claim_type=facts.get("claim_type") or None)
    cover = CoverPageInfo(requested_by=facts["requested_by"]) if facts.get("requested_by") else None
    draft = SecurityIncidentDraft(
        description="",
        category=["Vehicle Collision or Damage"],
        location=facts.get("location") or None,
        incident_datetime=facts.get("incident_datetime") or None,
        vehicle_info=vehicle,
        insurance_details=insurance,
        cover_page=cover,
    )
    return review.apply_template_defaults(draft.model_dump())


def _copy_hashing(src: Path, dest: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(src, "rb") as inp, open(dest, "wb") as out:
        while chunk := inp.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
            out.write(chunk)
    return digest.hexdigest(), size


def store_photos(report_id: str, photo_paths) -> tuple[list[dict], int]:
    """Copies the chat's photos into intake/{report_id}/ in the order they
    came (the temp files stay the bot's to delete), as a website upload
    keeps them: each one Pillow can read, the same photo once (the unique
    (report_id, sha256) index), with its thumbnail and the preview the AI
    reads (photos.ai_input_path). (kept, how many were skipped)."""
    folder = intake_dir(report_id)
    kept: list[dict] = []
    seen: set[str] = set()
    skipped = 0
    for raw in list(photo_paths or ())[:MAX_PHOTOS]:
        src = Path(raw)
        if not src.is_file():
            skipped += 1
            continue
        ext = src.suffix.lower() or ".jpg"
        photo_id = gen_id()
        dest = folder / f"{photo_id}{ext}"
        sha, size = _copy_hashing(src, dest)
        if sha in seen or size <= 0 or not is_readable_image(dest):
            dest.unlink(missing_ok=True)
            skipped += sha not in seen  # a repeat isn't a photo lost
            continue
        # As a website upload: when it was taken, read from the file as sent
        # (a photo sent as a Telegram "photo" has none -- Telegram strips
        # it; one sent as a file keeps it).
        taken = photo_taken_at(dest)
        # As a website upload: the thumbnail and the AI's preview from the
        # file as sent, then settings.photo_store_compress re-saves it
        # smaller -- only when both were made; sha256 stays the sent bytes'
        # hash, size is what is stored.
        thumb = generate_thumbnail(str(dest))
        preview = generate_preview(str(dest))
        if thumb is not None and preview is not None:
            stored_size = compress_original(str(dest))
            if stored_size is not None:
                size = stored_size
        seen.add(sha)
        kept.append({"id": photo_id, "path": str(dest), "sha256": sha, "size": size, "ext": ext,
                     "taken_at_local": taken.local if taken else None, "taken_at": taken.utc if taken else None})
    return kept, skipped


def _check_can_start(db, owner: User) -> None:
    """The website's checks for a new draft (api/intake.check_can_start):
    the person's open drafts, and the drafts everyone but admins and ticked
    staff share. ApiError / HTTPException with the words to show."""
    from app.api.intake import check_can_start

    check_can_start(db, owner)


def create_draft(*, external_id: str, photo_paths, facts: dict, notes: str,
                 who: Callable[[object], tuple[Optional[User], Optional[str]]]) -> Created:
    """Saves the chat's report as an intake draft owned by the linked
    person, ready for the pipeline (stage "sorting": start() follows at
    once). who(db) -> (the account, None) when this Telegram may start a
    case as it now, else (the account or None, why not) -- the bot's own
    step 8 check (channels/telegram._may_file).

    As public.save_submission: the files first, with no lock held; then ONE
    transaction -- the Report row goes in first, with no owner yet, which
    takes SQLite's write lock, so who() and the draft caps are checked with
    nothing able to commit in between; then the owner, the session, the
    photo rows and the history entry. Any failure rolls back and removes
    the folder; nothing is written for a refusal."""
    if disk_space_low():
        return Created(problem=DISK_LOW)
    report_id = gen_id()
    try:
        photos, skipped = store_photos(report_id, photo_paths)
    except BaseException:
        remove_intake_dir(report_id)
        raise
    if not photos:
        remove_intake_dir(report_id)
        return Created(problem=NO_PHOTOS, skipped=skipped)

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        report = Report(
            id=report_id,
            channel=CHANNEL,
            # The Telegram USER id, as a case filed in the chat has it.
            reporter_chat_id=str(external_id),
            status="intake",
            owner_id=None,  # set once the checks pass: not yet one of theirs
            data=initial_data(facts),
            photo_paths=[],
        )
        db.add(report)
        db.flush()  # the write lock, until the commit
        owner, problem = who(db)
        if problem is None:
            try:
                _check_can_start(db, owner)
            except HTTPException as exc:  # ApiError too: the words the website shows
                problem = str(exc.detail)
        if problem:
            db.rollback()
            remove_intake_dir(report_id)
            return Created(problem=problem, linked=owner is not None)
        report.owner_id = owner.id
        db.add(IntakeSession(
            report_id=report_id,
            # Handed to start() at once, which runs the sort first: never
            # "uploading", whose Start button would offer a second run.
            stage="sorting",
            stage_started_at=now,
            # Alive from the start for the api (pipeline.is_live), before
            # start()'s first beat.
            run_heartbeat_at=now,
            reporter_notes=notes or "",
        ))
        for position, photo in enumerate(photos):
            db.add(IntakePhoto(
                id=photo["id"],
                report_id=report_id,
                position=position,
                original_name=f"telegram-{position + 1}{photo['ext']}",
                path=photo["path"],
                sha256=photo["sha256"],
                size_bytes=photo["size"],
                taken_at_local=photo.get("taken_at_local"),
                taken_at=photo.get("taken_at"),
            ))
        audit(db, owner, "created", report_id, detail=CREATED_DETAIL)
        db.commit()
    except BaseException:
        db.rollback()
        remove_intake_dir(report_id)
        raise
    finally:
        db.close()
    logger.info("Telegram report saved as draft %s with %d photo(s) for a full reading", report_id, len(photos))
    return Created(report_id=report_id, photos=len(photos), skipped=skipped)


# ------------------------------------------------------------------ the run


def start(report_id: str, actor: AiActor, on_done: Callable[[], None] | None = None) -> bool:
    """Starts the website's run on the draft -- sort, then read (pipeline
    mode "all") -- in its own thread, on actor's AI: the person's own key,
    else the company's if they may use it (ai/keys.resolve_keys, for every
    call). on_done runs when the run ends, however it ends. False (and the
    draft marked failed, so the website offers Retry) if it couldn't."""
    if not pipeline.reserve(report_id):
        _mark_not_started(report_id)
        return False
    try:
        pipeline.start_reserved(report_id, "all", actor, on_done=on_done)
    except BaseException:
        pipeline.release(report_id)
        _mark_not_started(report_id)
        raise
    # The api's process can't see this process's run (pipeline._running):
    # it beats for as long as the run lives here, however long the chat
    # waits for it.
    pipeline.keep_beating(report_id)
    return True


def _mark_not_started(report_id: str) -> None:
    db = SessionLocal()
    try:
        db.execute(
            update(IntakeSession)
            .where(IntakeSession.report_id == report_id, IntakeSession.stage.in_(pipeline.RUNNING_STAGES))
            .values(stage="failed", error=NOT_STARTED)
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Telegram draft %s: couldn't mark the run as not started", report_id)
    finally:
        db.close()


def running(report_id: str) -> bool:
    """Whether this process's run on the draft is still going."""
    return pipeline.is_running(report_id)


def heartbeat(report_id: str) -> None:
    """While the bot's run goes on: one beat of the draft's heartbeat
    (pipeline.beat: run_heartbeat_at, and so updated_at, now) -- only while
    its stage is a running one. start()'s pipeline.keep_beating beats
    already; the chat's watcher beating too costs one write. Never raises."""
    pipeline.beat(report_id)


def outcome(report_id: str) -> Outcome:
    """The draft as the run left it, for the chat's summary: its stage (as
    this process sees it: a run here is never mistaken for one a restart
    cut off), the damage list, and the suggestions still open (second
    look, zone reads, linked parts: second_look.split_settled)."""
    from app.ai.second_look import split_settled

    db = SessionLocal()
    try:
        report = db.get(Report, report_id)
        if report is None:
            return Outcome(state="gone")
        if report.status != "intake":
            return Outcome(state="filed")
        session = db.get(IntakeSession, report_id)
        if session is None:
            return Outcome(state="gone")
        if session.stage == "finalizing":
            return Outcome(state="filed")  # being filed on the website right now
        photos = db.query(func.count(IntakePhoto.id)).filter(IntakePhoto.report_id == report_id).scalar() or 0
        data = report.data or {}
        stage = session.stage
        if stage in pipeline.RUNNING_STAGES:
            state = "running" if pipeline.is_running(report_id) else "failed"
        elif stage == "ready":
            state = "ready"
        else:
            state = "failed"
        look = data.get("second_look") if isinstance(data.get("second_look"), dict) else {}
        items = [it for it in look.get("items") or [] if isinstance(it, dict) and it.get("part")]
        open_items, _ = split_settled(items, data)
        error = session.error if state == "failed" else None
        if state == "failed" and not error:
            error = pipeline.RUN_INTERRUPTED_MESSAGE
        return Outcome(
            state=state,
            error=error,
            error_code=session.error_code if state == "failed" else None,
            photos=photos,
            read=len(session.read_photo_ids or []),
            parts=[it for it in data.get("damage_summary") or [] if isinstance(it, dict)],
            suggestions=open_items,
            vehicle=data.get("vehicle_info") if isinstance(data.get("vehicle_info"), dict) else {},
            read_notice=pipeline.read_notice(session.ai_draft) if state == "ready" else None,
        )
    finally:
        db.close()
