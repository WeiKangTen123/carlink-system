"""Accounts, sessions and the case history.

A session is a random token in an HttpOnly cookie; the server keeps only its
SHA-256 (models.AuthSession). It ends after AUTH_SESSION_IDLE_HOURS without
use, AUTH_SESSION_MAX_DAYS after signing in, on sign-out, and when the
user's password is changed or reset or the account is disabled.
"""
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.reports.models import AuditEntry, AuthSession, Report, User, iso_utc

logger = logging.getLogger(__name__)

# "__Host-": the browser only accepts this cookie from this exact host, over
# HTTPS, for the whole site -- so a page on some other *.sslip.io host (they
# can count as one "site") can't plant its own session cookie over ours.
# The prefix needs Secure, so plain-HTTP setups (AUTH_COOKIE_SECURE=false)
# use the bare name. The dashboard forwards either (lib/api.ts).
SESSION_COOKIE = "__Host-carlink_session" if settings.auth_cookie_secure else "carlink_session"
ROLES = ("admin", "surveyor")

MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)
# last_seen_at is written at most this often, so a page loading 80
# thumbnails (one request each) doesn't turn every read into a write.
_TOUCH_EVERY = timedelta(minutes=1)
# Repeated edits of one case by one person within this window are one
# "edited" entry in its history, not one per save.
_EDIT_COALESCE = timedelta(minutes=30)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands DateTime columns back naive; they are written in UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def idle_limit() -> timedelta:
    return timedelta(hours=settings.auth_session_idle_hours)


def max_age() -> timedelta:
    return timedelta(days=settings.auth_session_max_days)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CurrentUser:
    """The signed-in user for one request. A plain copy rather than the ORM
    row, which is detached once the lookup's database session closes."""
    id: str
    email: str
    name: str
    role: str
    must_change_password: bool
    qualifications: str
    license_number: str
    firm_name: str
    session_hash: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _current(user: User, session_hash: str) -> CurrentUser:
    return CurrentUser(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        must_change_password=bool(user.must_change_password),
        qualifications=user.qualifications or "",
        license_number=user.license_number or "",
        firm_name=user.firm_name or "",
        session_hash=session_hash,
    )


def create_session(db: Session, user: User, ip: str = "", user_agent: str = "") -> str:
    """Adds the session row (the caller commits) and returns the cookie value."""
    token = secrets.token_urlsafe(32)
    now = utc_now()
    db.add(AuthSession(
        token_hash=token_hash(token),
        user_id=user.id,
        created_at=now,
        last_seen_at=now,
        expires_at=now + max_age(),
        ip=ip[:64],
        user_agent=user_agent[:256],
    ))
    return token


def resolve_session(db: Session, token: str | None) -> CurrentUser | None:
    if not token:
        return None
    session = db.get(AuthSession, token_hash(token))
    if session is None:
        return None
    now = utc_now()
    user = db.get(User, session.user_id)
    expired = as_utc(session.expires_at) <= now or as_utc(session.last_seen_at) + idle_limit() <= now
    if expired or user is None or not user.active:
        try:
            db.execute(delete(AuthSession).where(AuthSession.token_hash == session.token_hash))
            db.commit()
        except OperationalError:
            db.rollback()  # busy database; the sweep removes it later
        return None
    current = _current(user, session.token_hash)
    if now - as_utc(session.last_seen_at) >= _TOUCH_EVERY:
        # A plain UPDATE, not via the loaded row: if the session was ended
        # meanwhile (sign-out in another tab), this matches nothing instead
        # of failing the request.
        try:
            db.execute(update(AuthSession).where(AuthSession.token_hash == session.token_hash).values(last_seen_at=now))
            db.commit()
        except OperationalError:
            # Busy database (a long write elsewhere): the request still
            # counts as signed in; the next one records the activity.
            db.rollback()
    return current


def end_session(db: Session, session_hash: str) -> None:
    db.query(AuthSession).filter(AuthSession.token_hash == session_hash).delete()


def end_user_sessions(db: Session, user_id: str, keep_hash: str | None = None) -> None:
    query = db.query(AuthSession).filter(AuthSession.user_id == user_id)
    if keep_hash:
        query = query.filter(AuthSession.token_hash != keep_hash)
    query.delete()


def sweep_sessions(db: Session, now: datetime | None = None) -> int:
    """Deletes sessions that can no longer be used. Returns how many."""
    now = now or utc_now()
    stale = [
        s for s in db.query(AuthSession).all()
        if as_utc(s.expires_at) <= now or as_utc(s.last_seen_at) + idle_limit() <= now
    ]
    for s in stale:
        db.delete(s)
    db.commit()
    return len(stale)


def locked_for(user: User, now: datetime | None = None) -> timedelta | None:
    """How much longer the account is locked after too many wrong
    passwords, or None if it isn't."""
    now = now or utc_now()
    until = as_utc(user.locked_until)
    if until and until > now:
        return until - now
    return None


def record_failed_login(user: User, now: datetime | None = None) -> None:
    now = now or utc_now()
    user.failed_logins = (user.failed_logins or 0) + 1
    if user.failed_logins >= MAX_FAILED_LOGINS:
        user.locked_until = now + LOCKOUT
        user.failed_logins = 0


def record_login(user: User, now: datetime | None = None) -> None:
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now or utc_now()


def audit(
    db: Session,
    actor: CurrentUser | User | None,
    action: str,
    report_id: str | None = None,
    detail: str = "",
) -> None:
    """Adds a history entry (the caller commits)."""
    now = utc_now()
    if action == "edited" and actor and report_id:
        last = (
            db.query(AuditEntry)
            .filter(AuditEntry.report_id == report_id)
            .order_by(AuditEntry.id.desc())
            .first()
        )
        if last and last.action == "edited" and last.user_id == actor.id and now - as_utc(last.created_at) < _EDIT_COALESCE:
            last.created_at = now
            return
    db.add(AuditEntry(
        report_id=report_id,
        user_id=actor.id if actor else None,
        user_name=actor.name if actor else "",
        action=action,
        detail=detail[:500],
        created_at=now,
    ))


def report_history(db: Session, report: Report) -> list[dict]:
    """The case's history, oldest first. Cases filed before sign-in existed,
    or through the Telegram bot, have no "created" entry; one is shown from
    the case itself, without a name rather than with a guessed one."""
    rows = (
        db.query(AuditEntry)
        .filter(AuditEntry.report_id == report.id)
        .order_by(AuditEntry.created_at, AuditEntry.id)
        .all()
    )
    history = [
        {"action": e.action, "user_name": e.user_name or None, "detail": e.detail or None, "at": iso_utc(e.created_at)}
        for e in rows
    ]
    if not any(e["action"] == "created" for e in history):
        via = {"telegram": "Filed through the Telegram bot", "whatsapp": "Filed through WhatsApp"}.get(report.channel)
        history.insert(0, {
            "action": "created",
            "user_name": None,
            "detail": via or "Filed before sign-in was required",
            "at": iso_utc(report.created_at),
        })
    return history


def user_dict(user: User) -> dict:
    """Everything the dashboard shows about an account -- never the hash."""
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "active": bool(user.active),
        "must_change_password": bool(user.must_change_password),
        "qualifications": user.qualifications or "",
        "license_number": user.license_number or "",
        "firm_name": user.firm_name or "",
        "locked": locked_for(user) is not None,
        "last_login_at": iso_utc(user.last_login_at) if user.last_login_at else None,
        "created_at": iso_utc(user.created_at),
    }


def me_dict(user: CurrentUser | User) -> dict:
    """What the signed-in user sees about themselves (GET /auth/me)."""
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "must_change_password": bool(user.must_change_password),
        "qualifications": user.qualifications or "",
        "license_number": user.license_number or "",
        "firm_name": user.firm_name or "",
    }


def active_admin_count(db: Session) -> int:
    return db.query(User).filter(User.role == "admin", User.active.is_(True)).count()
