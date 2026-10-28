"""Accounts, sessions and the case history.

A session is a random token in an HttpOnly cookie; the server keeps only its
SHA-256 (models.AuthSession). It ends after AUTH_SESSION_IDLE_HOURS without
use, AUTH_SESSION_MAX_DAYS after signing in, on sign-out, and when the
user's password is changed or reset or the account is disabled.
"""
import hashlib
import logging
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.auth.permissions import can_view_all_cases, is_staff, may_use_company_key
from app.config import settings
from app.reports.models import AuditEntry, AuthSession, Report, User, iso_utc

logger = logging.getLogger(__name__)

# "__Host-": the browser only accepts this cookie from this exact host, over
# HTTPS, for the whole site -- so a page on some other *.sslip.io host (they
# can count as one "site") can't plant its own session cookie over ours.
# The prefix needs Secure, so plain-HTTP setups (AUTH_COOKIE_SECURE=false)
# use the bare name. The dashboard forwards either (lib/api.ts).
SESSION_COOKIE = "__Host-carlink_session" if settings.auth_cookie_secure else "carlink_session"
# "user" was called "surveyor" until 2026-10-25 (reports/db._DATA_FIXES).
ROLES = ("admin", "user")

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


# 3-30 characters: letters, numbers, dots, dashes, underscores, starting and
# ending with a letter or number. Stored lower-cased; "Wei.Kang" and
# "wei.kang" are one username.
_USERNAME = re.compile(r"[a-z0-9](?:[a-z0-9._-]{1,28})[a-z0-9]")
USERNAME_RULE = "Use 3-30 letters, numbers, dots, dashes or underscores, starting and ending with a letter or number."
# Names that would let a new account pass itself off as the system or the
# admins in the history and the top bar.
_RESERVED_USERNAMES = {
    "admin", "administrator", "root", "system", "support", "help", "staff", "owner",
    "carlink", "carlinkstudio", "security", "noreply", "no-reply", "info", "null", "undefined",
}
# Anywhere inside a name someone chooses for themselves ("sysadmin",
# "carlink.support"): only an admin can give out names like these.
_RESERVED_STEMS = ("admin", "carlink", "support", "staff", "owner", "system", "root", "security")
RESERVED_NAME = "That name is reserved. Choose another."


def normalize_username(username: str) -> str:
    return username.strip().lower()


def username_problem(username: str, *, self_chosen: bool = False) -> str | None:
    """Why a username (already normalized) is refused, or None. Doesn't
    check whether it's taken. self_chosen: also refuse the reserved words
    inside it (signup, invites, My account); an admin may still set them."""
    if not _USERNAME.fullmatch(username):
        return USERNAME_RULE
    if username in _RESERVED_USERNAMES:
        return "That username is reserved. Choose another."
    if self_chosen and any(stem in username for stem in _RESERVED_STEMS):
        return "That username is reserved. Choose another."
    return None


def _name_key(name: str) -> str:
    """Two names that look the same compare equal: compatibility forms,
    case and spacing folded, invisible characters dropped."""
    folded = unicodedata.normalize("NFKC", name).casefold()
    visible = "".join(ch for ch in folded if unicodedata.category(ch)[0] != "C")
    return " ".join(visible.split())


def full_name_problem(db: Session, name: str, exclude_id: str) -> str | None:
    """Why someone can't give themselves this full name: it reads as the
    system or the admins, or it is another account's name. The name is
    printed on sign-offs and shown in the history, so it mustn't let one
    account pass for another. (Admins can still set any name.)"""
    key = _name_key(name)
    words = re.split(r"[^a-z0-9]+", key)
    if any(stem in word for word in words for stem in _RESERVED_STEMS):
        return RESERVED_NAME
    for other_id, other_name in db.query(User.id, User.name).filter(User.id != exclude_id).all():
        if _name_key(other_name or "") == key:
            return "Another account already uses that name. If it really is yours, ask an admin to set it."
    return None


def display_name(user) -> str:
    """How an account is named in the history: the full name, and the
    username when that differs -- the username is unique, the name isn't."""
    name = (user.name or "").strip()
    username = (getattr(user, "username", "") or "").strip()
    if username and name and _name_key(name) != username:
        return f"{name} (@{username})"
    return name or username


def username_taken(db: Session, username: str, exclude_id: str | None = None) -> bool:
    query = db.query(User).filter(User.username == username)
    if exclude_id:
        query = query.filter(User.id != exclude_id)
    return query.first() is not None


def username_from_email(db: Session, email: str) -> str:
    """A free, valid username made from an email's local part, for accounts
    that didn't choose one (from before usernames, or made by an admin)."""
    local = re.sub(r"[^a-z0-9._-]+", "", email.split("@", 1)[0].lower()).strip("._-")[:26]
    base = local if len(local) >= 3 and username_problem(local) is None else f"user{local}"[:26].strip("._-")
    if len(base) < 3:
        base = "user"
    candidate, n = base, 1
    # Not self_chosen: the reserved stems would never let "useradmin" through.
    while username_problem(candidate) is not None or username_taken(db, candidate):
        n += 1
        candidate = f"{base}{n}"
    return candidate


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
    username: str
    name: str
    role: str
    must_change_password: bool
    qualifications: str
    license_number: str
    firm_name: str
    session_hash: str
    # The ticks an admin sets (models.User); read fresh on every request,
    # so a change applies at once. app/auth/permissions.py says what they do.
    sees_all_cases: bool = False
    ai_company_key_allowed: bool = False
    may_sign_off: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _current(user: User, session_hash: str) -> CurrentUser:
    return CurrentUser(
        id=user.id,
        email=user.email,
        username=user.username or "",
        name=user.name,
        role=user.role,
        must_change_password=bool(user.must_change_password),
        qualifications=user.qualifications or "",
        license_number=user.license_number or "",
        firm_name=user.firm_name or "",
        session_hash=session_hash,
        sees_all_cases=bool(user.sees_all_cases),
        ai_company_key_allowed=bool(user.ai_company_key_allowed),
        may_sign_off=bool(user.may_sign_off),
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
        user_name=display_name(actor) if actor else "",
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
        "username": user.username or "",
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
        # The ticks as set, not what they add up to (an admin has all of it
        # whatever they say).
        "sees_all_cases": bool(user.sees_all_cases),
        "ai_company_key_allowed": bool(user.ai_company_key_allowed),
        "may_sign_off": bool(user.may_sign_off),
    }


def user_dicts(db: Session, users: list[User]) -> list[dict]:
    """user_dict plus each account's case counts (open, signed off,
    drafts) and own AI key ({has_key, last4, status, added_at} or None,
    never the key), for the admin's Users screen -- one query each for all
    of them, never one per account."""
    from app.ai.keys import keys_by_user  # P3 "My AI key"
    from app.reports.owners import case_counts

    ids = [u.id for u in users]
    counts = case_counts(db, ids)
    ai_keys = keys_by_user(db, ids)  # P3
    return [{**user_dict(u), "cases": counts[u.id], "ai_key": ai_keys.get(u.id)} for u in users]


def me_dict(user: CurrentUser | User) -> dict:
    """What the signed-in user sees about themselves (GET /auth/me). The
    last three are what the ticks add up to, admins having all of it."""
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username or "",
        "name": user.name,
        "role": user.role,
        "must_change_password": bool(user.must_change_password),
        "qualifications": user.qualifications or "",
        "license_number": user.license_number or "",
        "firm_name": user.firm_name or "",
        "can_view_all_cases": can_view_all_cases(user),
        "may_sign_off": is_staff(user),
        "ai_allowed": may_use_company_key(user),
    }


def active_admin_count(db: Session) -> int:
    return db.query(User).filter(User.role == "admin", User.active.is_(True)).count()
