"""One-time account links -- invites and password resets -- and the sweep
that deletes old ones and old access requests.

There is no email service, so an admin copies the link and sends it by
WhatsApp. That makes the link itself the only proof of who someone is, so
it works once, expires, can be revoked, is tied to one email, and only its
SHA-256 is stored (like session tokens): a copy of the database, or a
backup, holds no usable link.
"""
import secrets
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.auth.core import as_utc, token_hash, utc_now
from app.reports.models import AccessRequest, AccountLink, User, iso_utc

# Choices offered to the admin, in hours (1, 3 and 7 days).
EXPIRY_CHOICES = (24, 72, 168)
DEFAULT_INVITE_HOURS = 72
DEFAULT_RESET_HOURS = 24

# PDPA: kept only as long as they're useful (app/intake/cleanup.py runs
# this every 6 hours).
KEEP_CLOSED_LINKS = timedelta(days=30)
KEEP_DECIDED_REQUESTS = timedelta(days=30)
KEEP_PENDING_REQUESTS = timedelta(days=60)


def new_link(
    db: Session,
    *,
    purpose: str,
    email: str,
    role: str,
    creator,
    hours: int,
    name: str = "",
    user_id: str | None = None,
    request_id: str | None = None,
) -> tuple[AccountLink, str]:
    """Adds a link (the caller commits) and returns it with its token, the
    only time the token exists outside the person's browser.

    Any earlier open link for the same email and purpose is revoked, so
    "lost it, send another" never leaves two working links around.
    """
    now = utc_now()
    db.execute(
        update(AccountLink)
        .where(
            AccountLink.email == email,
            AccountLink.purpose == purpose,
            AccountLink.used_at.is_(None),
            AccountLink.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    token = secrets.token_urlsafe(32)
    link = AccountLink(
        purpose=purpose,
        token_hash=token_hash(token),
        email=email,
        role=role,
        name=name,
        user_id=user_id,
        access_request_id=request_id,
        created_by=creator.id,
        created_by_name=creator.name,
        created_at=now,
        expires_at=now + timedelta(hours=hours),
    )
    db.add(link)
    db.flush()
    return link, token


def find_link(db: Session, token: str) -> AccountLink | None:
    if not token or len(token) > 200:
        return None
    return db.query(AccountLink).filter(AccountLink.token_hash == token_hash(token)).first()


def link_status(link: AccountLink, now: datetime | None = None) -> str:
    if link.used_at:
        return "used"
    if link.revoked_at:
        return "revoked"
    if as_utc(link.expires_at) <= (now or utc_now()):
        return "expired"
    return "open"


def creator_still_admin(db: Session, link: AccountLink) -> bool:
    """An invite dies with its creator's admin rights: a disabled or
    demoted admin's outstanding links stop working."""
    creator = db.get(User, link.created_by)
    return bool(creator and creator.active and creator.role == "admin")


def claim(db: Session, link: AccountLink) -> bool:
    """Marks the link used, in one statement, if it is still unused,
    unrevoked and unexpired. Two browsers submitting the same link at once:
    exactly one gets True. Expiry is checked here too, not only before the
    password is hashed, so a link can't be used in the moment it expires."""
    now = utc_now()
    result = db.execute(
        update(AccountLink)
        .where(
            AccountLink.id == link.id,
            AccountLink.used_at.is_(None),
            AccountLink.revoked_at.is_(None),
            AccountLink.expires_at > now,
        )
        .values(used_at=now)
        # Decided by the database alone. SQLAlchemy would otherwise re-run
        # the WHERE in Python on loaded rows, whose times come back from
        # SQLite without a timezone.
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def link_dict(link: AccountLink) -> dict:
    """What admins see about a link -- never the token or its hash."""
    return {
        "id": link.id,
        "purpose": link.purpose,
        "email": link.email,
        "role": link.role,
        "name": link.name or "",
        "created_by_name": link.created_by_name or "",
        "created_at": iso_utc(link.created_at),
        "expires_at": iso_utc(link.expires_at),
        "used_at": iso_utc(link.used_at) if link.used_at else None,
        "revoked_at": iso_utc(link.revoked_at) if link.revoked_at else None,
        "status": link_status(link),
    }


def sweep_signups(db: Session, now: datetime | None = None) -> dict:
    """Deletes links closed more than 30 days ago, requests decided more
    than 30 days ago and requests left pending for 60 days."""
    now = now or utc_now()
    removed_links = 0
    for link in db.query(AccountLink).all():
        status = link_status(link, now)
        closed_at = {"used": link.used_at, "revoked": link.revoked_at, "expired": link.expires_at}.get(status)
        if closed_at and as_utc(closed_at) + KEEP_CLOSED_LINKS <= now:
            db.delete(link)
            removed_links += 1
    removed_requests = 0
    for request in db.query(AccessRequest).all():
        if request.status == "pending":
            stale = as_utc(request.created_at) + KEEP_PENDING_REQUESTS <= now
        else:
            stale = as_utc(request.decided_at or request.created_at) + KEEP_DECIDED_REQUESTS <= now
        if stale:
            db.delete(request)
            removed_requests += 1
    db.commit()
    return {"links": removed_links, "requests": removed_requests}
