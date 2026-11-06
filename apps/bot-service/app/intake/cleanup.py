"""Housekeeping for storage that nothing else ever frees.

- Intake drafts nobody has touched for DRAFT_MAX_IDLE: each can hold up to
  api/intake.MAX_DRAFT_BYTES of photos, and every folder dropped on New
  Intake creates one, so abandoned drafts would otherwise fill the disk of
  a VM shared with other apps. Public reports nobody has taken are not
  idle drafts: they go at their expires_at instead (30 days after they
  were sent, intake/public.sweep_untaken); once taken, the idle rule holds.
- storage/tmp files older than TMP_MAX_AGE: /reports/analyze-photos leaves
  its uploads there until the report is saved, which it may never be.
- Expired and idle sign-in sessions (app/auth/core.py).
- Telegram link codes a day after they expired (app/auth/chat_links.py).
- Public reports' leftovers: submission rows whose report is gone, spam
  blocks that have run out, and intake/ folders with no draft behind them
  (a finalize or save stopped half-way) once ORPHAN_FOLDER_AGE old.

Runs in the api process (started from main.py's lifespan), every
SWEEP_EVERY. The telegram-bot process doesn't run it.
"""
import logging
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func

from app.auth.chat_links import sweep_codes
from app.auth.core import audit, sweep_sessions
from app.auth.links import sweep_links
from app.auth.permissions import in_inbox, is_public
from app.config import settings
from app.intake import pipeline, public
from app.intake.photos import remove_intake_dir
from app.reports.db import SessionLocal
from app.reports.models import IntakePhoto, IntakeSession, Report
from app.storage.files import tmp_dir

logger = logging.getLogger(__name__)

DRAFT_MAX_IDLE = timedelta(days=7)
TMP_MAX_AGE = timedelta(hours=24)
SWEEP_EVERY = timedelta(hours=6)
# An intake/ folder with no draft is left this long: public.save_submission
# writes the files before the rows that go with them.
ORPHAN_FOLDER_AGE = timedelta(hours=24)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _sweep_intake_folders(now: datetime) -> int:
    """intake/{id}/ folders with no IntakeSession, last changed over
    ORPHAN_FOLDER_AGE ago. Returns how many were removed."""
    root = Path(settings.storage_dir) / "intake"
    if not root.is_dir():
        return 0
    cutoff = (now - ORPHAN_FOLDER_AGE).timestamp()
    db = SessionLocal()
    try:
        live = {rid for (rid,) in db.query(IntakeSession.report_id).all()}
        removed = 0
        for folder in root.iterdir():
            try:
                if folder.is_symlink() or not folder.is_dir() or folder.name in live or folder.stat().st_mtime >= cutoff:
                    continue
                # Asked again: a draft may have been made since the list.
                if db.get(IntakeSession, folder.name) is not None:
                    continue
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
            except OSError:
                continue
        return removed
    finally:
        db.close()


def sweep(now: datetime | None = None) -> dict:
    """One pass. Returns what it removed, for logging and tests."""
    now = now or datetime.now(timezone.utc)
    removed_drafts: list[str] = []
    db = SessionLocal()
    try:
        for (report_id,) in db.query(IntakeSession.report_id).all():
            # Fresh each time, not one list of rows: a public delete that is
            # refused rolls back, which would expire them all.
            session = db.get(IntakeSession, report_id)
            if session is None:
                continue
            report = db.get(Report, report_id)
            # Untaken public reports keep the date promised at consent
            # (public.sweep_untaken, below), not this one (review H3).
            if report is not None and in_inbox(report):
                continue
            # Last activity is the latest of: draft created, session row
            # changed (autosave, AI run, a public report taken), newest
            # photo uploaded. The session row alone isn't enough -- uploads
            # don't touch it.
            last_photo = (
                db.query(func.max(IntakePhoto.created_at))
                .filter(IntakePhoto.report_id == report_id)
                .scalar()
            )
            candidates = [_as_utc(session.updated_at), _as_utc(last_photo)]
            if report is not None:
                candidates.append(_as_utc(report.created_at))
            last_activity = max(c for c in candidates if c is not None)
            # A filing a restart cut off (pipeline.filing_interrupted) is a
            # failed draft like any other: skipped for good it was a leak.
            # Likewise an AI run a restart cut off: the stage still says
            # "sorting"/"reading" but no thread here runs it (the wizard
            # already shows it as failed, pipeline.is_running); left out it
            # was skipped forever, the same leak.
            busy = (session.stage in pipeline.RUNNING_STAGES and pipeline.is_running(report_id)) or (
                session.stage == "finalizing" and not pipeline.filing_interrupted(session, now)
            )
            if busy or now - last_activity < DRAFT_MAX_IDLE:
                continue
            if report is not None and is_public(report):
                # A taken public draft: deleted with its submission row, and
                # only while it is still that owner's (a release meanwhile
                # puts it back on the 30-day clock).
                if public.delete_submission(
                    db, report_id, seen_owner=report.owner_id, actor=None, action="expired",
                    detail=f"Not worked on for {DRAFT_MAX_IDLE.days} days", now=now,
                ):
                    db.commit()
                    remove_intake_dir(report_id)
                    removed_drafts.append(report_id)
                continue
            db.query(IntakePhoto).filter(IntakePhoto.report_id == report_id).delete()
            if report is not None and report.status == "intake":
                db.delete(report)
                # The history keeps that it went, and why, as it does for a
                # public draft (public.delete_submission): before this a
                # staff draft vanished after a week with no trace.
                audit(db, None, "expired", report_id, detail=f"Not worked on for {DRAFT_MAX_IDLE.days} days")
            db.delete(session)
            db.commit()
            remove_intake_dir(report_id)
            removed_drafts.append(report_id)
    finally:
        db.close()

    # Public reports nobody took within 30 days, and their leftovers.
    db = SessionLocal()
    try:
        expired_public = public.sweep_untaken(db, now)
        orphan_public = public.sweep_orphans(db)
        expired_blocks = public.sweep_blocks(db, now)
        db.commit()
    finally:
        db.close()
    orphan_folders = _sweep_intake_folders(now)

    removed_tmp = 0
    cutoff = (now - TMP_MAX_AGE).timestamp()
    for path in tmp_dir().iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed_tmp += 1
        except OSError:
            continue

    # Sign-in sessions that have expired or gone idle (app/auth/core.py).
    db = SessionLocal()
    try:
        removed_sessions = sweep_sessions(db, now)
        # Old invite/reset links (PDPA retention).
        removed_links = sweep_links(db, now)
        # Telegram link codes (only their hashes), a day after expiry.
        removed_codes = sweep_codes(db, now)
        db.commit()
    finally:
        db.close()

    if removed_drafts or removed_tmp or removed_sessions or removed_links or removed_codes:
        logger.info(
            "Cleanup removed %d idle intake draft(s) %s, %d tmp file(s), %d expired session(s), "
            "%d old account link(s) and %d old Telegram code(s)",
            len(removed_drafts), removed_drafts, removed_tmp, removed_sessions, removed_links, removed_codes,
        )
    if expired_public or orphan_public or expired_blocks or orphan_folders:
        # Ids and counts only: never who sent them.
        logger.info(
            "Cleanup removed %d untaken public report(s) after %d days %s, %d orphan public row(s), "
            "%d expired spam block(s) and %d orphan intake folder(s)",
            len(expired_public), public.UNTAKEN_MAX_AGE.days, expired_public, orphan_public, expired_blocks, orphan_folders,
        )
    return {
        "drafts": removed_drafts,
        "tmp_files": removed_tmp,
        "sessions": removed_sessions,
        "links": removed_links,
        "telegram_codes": removed_codes,
        # P6: untaken public reports past expires_at (ids), submission rows
        # with no report, spam blocks run out, intake/ folders with no draft.
        "public_expired": expired_public,
        "public_orphans": orphan_public,
        "public_blocks": expired_blocks,
        "intake_folders": orphan_folders,
    }


def _loop() -> None:
    while True:
        try:
            sweep()
        except Exception:
            logger.exception("Cleanup sweep failed")
        time.sleep(SWEEP_EVERY.total_seconds())


def start_background_cleanup() -> None:
    threading.Thread(target=_loop, name="intake-cleanup", daemon=True).start()
