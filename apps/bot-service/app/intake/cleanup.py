"""Housekeeping for storage that nothing else ever frees.

- Intake drafts nobody has touched for DRAFT_MAX_IDLE: each can hold up to
  api/intake.MAX_DRAFT_BYTES of photos, and every folder dropped on New
  Intake creates one, so abandoned drafts would otherwise fill the disk of
  a VM shared with other apps.
- storage/tmp files older than TMP_MAX_AGE: /reports/analyze-photos leaves
  its uploads there until the report is saved, which it may never be.
- Expired and idle sign-in sessions (app/auth/core.py).
- Telegram link codes a day after they expired (app/auth/chat_links.py).

Runs in the api process (started from main.py's lifespan), every
SWEEP_EVERY. The telegram-bot process doesn't run it.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from app.auth.chat_links import sweep_codes
from app.auth.core import sweep_sessions
from app.auth.links import sweep_links
from app.intake.photos import remove_intake_dir
from app.reports.db import SessionLocal
from app.reports.models import IntakePhoto, IntakeSession, Report
from app.storage.files import tmp_dir

logger = logging.getLogger(__name__)

DRAFT_MAX_IDLE = timedelta(days=7)
TMP_MAX_AGE = timedelta(hours=24)
SWEEP_EVERY = timedelta(hours=6)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def sweep(now: datetime | None = None) -> dict:
    """One pass. Returns what it removed, for logging and tests."""
    now = now or datetime.now(timezone.utc)
    removed_drafts: list[str] = []
    db = SessionLocal()
    try:
        for session in db.query(IntakeSession).all():
            report = db.get(Report, session.report_id)
            # Last activity is the latest of: draft created, session row
            # changed (autosave, AI run), newest photo uploaded. The session
            # row alone isn't enough -- uploads don't touch it.
            last_photo = (
                db.query(func.max(IntakePhoto.created_at))
                .filter(IntakePhoto.report_id == session.report_id)
                .scalar()
            )
            candidates = [_as_utc(session.updated_at), _as_utc(last_photo)]
            if report is not None:
                candidates.append(_as_utc(report.created_at))
            last_activity = max(c for c in candidates if c is not None)
            if session.stage in ("sorting", "reading", "finalizing") or now - last_activity < DRAFT_MAX_IDLE:
                continue
            db.query(IntakePhoto).filter(IntakePhoto.report_id == session.report_id).delete()
            if report is not None and report.status == "intake":
                db.delete(report)
            db.delete(session)
            db.commit()
            remove_intake_dir(session.report_id)
            removed_drafts.append(session.report_id)
    finally:
        db.close()

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
    return {
        "drafts": removed_drafts,
        "tmp_files": removed_tmp,
        "sessions": removed_sessions,
        "links": removed_links,
        "telegram_codes": removed_codes,
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
