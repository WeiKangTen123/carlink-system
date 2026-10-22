"""Requesting access, and the one-time links that create accounts and reset
passwords.

Public (app/auth/middleware.PUBLIC_PATHS):
    POST   /auth/access-requests          ask for an account (no password)
    POST   /auth/links/lookup             what a link is for, without using it
    POST   /auth/links/accept             use it: set a password, get signed in
Admin:
    GET    /access-requests               pending (or all) requests
    POST   /access-requests/{id}/approve  choose the role -> invite link
    POST   /access-requests/{id}/reject
    DELETE /access-requests/{id}          erase it (PDPA)
    GET    /account-links                 invites and reset links, never tokens
    POST   /account-links                 invite someone directly
    DELETE /account-links/{id}            revoke
    POST   /users/{id}/reset-link         one-time link to set a new password

A request is never an account and never carries a password: anyone could
type a colleague's name and email. An account only comes from an admin's
link, and the admin choosing whom to send it to is the identity check
(there's no email service to verify addresses with). The token travels in
the link's #fragment and in POST bodies -- never in a URL path -- so it
stays out of access logs, Referer headers and link previews.

Every public answer is the same whether or not the email has an account,
like sign-in's.
"""
import re
import secrets
import threading
import time
from collections import deque
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.api.auth import _EMAIL, _checked_email, _ip_blocked, _note_ip_failure, _set_session_cookie, client_ip
from app.auth.core import (
    CurrentUser,
    as_utc,
    audit,
    create_session,
    end_user_sessions,
    me_dict,
    normalize_email,
    record_login,
    utc_now,
)
from app.auth.deps import require_admin
from app.auth.links import (
    DEFAULT_INVITE_HOURS,
    DEFAULT_RESET_HOURS,
    claim,
    creator_still_admin,
    find_link,
    link_dict,
    link_status,
    new_link,
)
from app.auth.passwords import MAX_PASSWORD_LENGTH, hash_password, password_problem
from app.reports.db import SessionLocal
from app.reports.models import AccessRequest, AccountLink, User, iso_utc

router = APIRouter(tags=["signup"])

ExpiryHours = Literal[24, 72, 168]
Role = Literal["surveyor", "admin"]

# Per network: a person sends one request; a script sends hundreds.
_REQUESTS_PER_IP = 5
_REQUEST_WINDOW_SECONDS = 60 * 60
# Beyond these, new requests are refused (all) or quietly dropped (one
# email), so a flood can neither fill the database nor bury a real request.
MAX_PENDING = 200
MAX_PENDING_PER_EMAIL = 3

_request_times: dict[str, deque] = {}
_request_lock = threading.Lock()

_PHONE = re.compile(r"\+?[\d\s()-]{8,30}")
_REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

LINK_GONE = {
    "expired": "This link has expired. Ask your admin for a new one.",
    "used": "This link has already been used. Sign in with your email and password.",
    "invalid": "This link doesn't work. Check you copied all of it, or ask your admin for a new one.",
}


def _reference() -> str:
    return "REQ-" + "".join(secrets.choice(_REFERENCE_ALPHABET) for _ in range(4))


def _too_many_requests(ip: str) -> bool:
    now = time.monotonic()
    with _request_lock:
        if len(_request_times) > 5000:
            _request_times.clear()
        times = _request_times.setdefault(ip, deque())
        while times and now - times[0] > _REQUEST_WINDOW_SECONDS:
            times.popleft()
        if len(times) >= _REQUESTS_PER_IP:
            return True
        times.append(now)
        return False


def _gone(code: str) -> JSONResponse:
    return JSONResponse(status_code=410, content={"detail": LINK_GONE[code], "code": code})


# ------------------------------------------------------------ public


class AccessRequestIn(BaseModel):
    name: str = Field(max_length=120)
    email: str = Field(max_length=320)
    phone: str = Field(max_length=40)
    organisation: str = Field(default="", max_length=160)
    requested_role: Role = "surveyor"
    note: str = Field(default="", max_length=500)
    consent: bool = False
    # Honeypot: hidden from people by CSS, filled in by form-spamming bots.
    website: str = Field(default="", max_length=200)


@router.post("/auth/access-requests", status_code=202)
def request_access(body: AccessRequestIn, request: Request) -> dict:
    name = " ".join(body.name.split())
    email = normalize_email(body.email)
    phone = " ".join(body.phone.split())
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Enter your full name.")
    if not _EMAIL.fullmatch(email):
        raise HTTPException(status_code=400, detail="That doesn't look like an email address.")
    if not _PHONE.fullmatch(phone) or not 8 <= sum(c.isdigit() for c in phone) <= 15:
        raise HTTPException(status_code=400, detail="Enter a mobile number, e.g. 9123 4567.")
    if not body.consent:
        raise HTTPException(status_code=400, detail="Tick the box to send your request.")

    ip = client_ip(request)
    if _too_many_requests(ip):
        raise HTTPException(status_code=429, detail="Too many requests from this network. Try again in an hour.")
    # The same answer as a stored request: nothing here says what happened.
    accepted = {"reference": _reference()}
    if body.website:
        return accepted

    db = SessionLocal()
    try:
        if db.query(AccessRequest).filter(AccessRequest.status == "pending").count() >= MAX_PENDING:
            raise HTTPException(status_code=503, detail="We've received a lot of requests. Try again tomorrow.")
        same_email = db.query(AccessRequest).filter(AccessRequest.status == "pending", AccessRequest.email == email).count()
        if same_email >= MAX_PENDING_PER_EMAIL:
            return accepted
        # Stored even when the email already has an account: refusing would
        # tell a stranger it does. Admins see the overlap flagged instead.
        db.add(AccessRequest(
            reference=accepted["reference"],
            name=name,
            email=email,
            phone=phone,
            organisation=body.organisation.strip(),
            requested_role=body.requested_role,
            note=body.note.strip(),
            consent_at=utc_now(),
            ip=ip[:64],
            user_agent=request.headers.get("user-agent", "")[:256],
        ))
        db.commit()
        return accepted
    finally:
        db.close()


class LinkToken(BaseModel):
    token: str = Field(max_length=200)


class LinkAccept(LinkToken):
    name: str = Field(default="", max_length=120)
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)


def _usable_link(db, token: str, ip: str) -> tuple[AccountLink | None, User | None, JSONResponse | None]:
    """The link and (for a reset) its account, or the 410 to send instead.
    Unknown tokens count against the network like wrong passwords, so links
    can't be guessed at speed (not that 256 random bits can be)."""
    link = find_link(db, token)
    if link is None:
        _note_ip_failure(ip)
        return None, None, _gone("invalid")
    status = link_status(link)
    if status != "open":
        return None, None, _gone("invalid" if status == "revoked" else status)
    if not creator_still_admin(db, link):
        return None, None, _gone("invalid")
    user = None
    if link.purpose == "reset":
        user = db.get(User, link.user_id) if link.user_id else None
        if user is None or not user.active:
            return None, None, _gone("invalid")
    return link, user, None


@router.post("/auth/links/lookup")
def lookup_link(body: LinkToken, request: Request):
    """What the /invite page shows before the person types anything. Never
    uses the link up: WhatsApp's preview fetcher and a curious reload
    mustn't spend it."""
    ip = client_ip(request)
    if _ip_blocked(ip):
        raise HTTPException(status_code=429, detail="Too many attempts from this network. Try again in 15 minutes.")
    db = SessionLocal()
    try:
        link, user, gone = _usable_link(db, body.token, ip)
        if gone:
            return gone
        return {
            "purpose": link.purpose,
            "email": link.email,
            "role": link.role,
            "name": (user.name if user else link.name) or "",
            "invited_by": link.created_by_name or "",
            "expires_at": iso_utc(link.expires_at),
        }
    finally:
        db.close()


@router.post("/auth/links/accept")
def accept_link(body: LinkAccept, request: Request, response: Response):
    ip = client_ip(request)
    if _ip_blocked(ip):
        raise HTTPException(status_code=429, detail="Too many attempts from this network. Try again in 15 minutes.")
    db = SessionLocal()
    try:
        link, user, gone = _usable_link(db, body.token, ip)
        if gone:
            return gone
        name = " ".join(body.name.split())
        if link.purpose == "invite":
            if not name:
                raise HTTPException(status_code=400, detail="Enter your full name.")
            problem = password_problem(body.password, (name, link.email))
        else:
            problem = password_problem(body.password, (user.name, user.email))
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        # Hashed before the link is claimed: a busy hasher (503) leaves the
        # link unused for a retry.
        password_hash = hash_password(body.password)

        if not claim(db, link):
            db.rollback()
            return _gone("used")
        if link.purpose == "invite":
            if db.query(User).filter(User.email == link.email).first():
                db.rollback()
                raise HTTPException(status_code=409, detail=f"There's already an account for {link.email}. Sign in instead.")
            user = User(email=link.email, name=name, role=link.role, password_hash=password_hash, must_change_password=False)
            db.add(user)
            db.flush()
            audit(db, user, "invite_accepted", detail=f"{link.email} ({link.role}), invited by {link.created_by_name}")
        else:
            user.password_hash = password_hash
            user.must_change_password = False
            user.failed_logins = 0
            user.locked_until = None
            # Every browser still signed in with the old password.
            end_user_sessions(db, user.id)
            audit(db, user, "password_reset_by_link", detail=user.email)
        link.used_by_user_id = user.id
        record_login(user)
        token = create_session(db, user, ip, request.headers.get("user-agent", ""))
        try:
            db.commit()
        except IntegrityError:
            # The email was taken between the check and the commit; the
            # rollback also puts the link back to unused.
            db.rollback()
            raise HTTPException(status_code=409, detail=f"There's already an account for {link.email}. Sign in instead.")
        _set_session_cookie(response, token)
        db.refresh(user)
        return me_dict(user)
    finally:
        db.close()


# ------------------------------------------------------------- admin


def _request_dict(row: AccessRequest, *, has_account: bool, same_email_pending: int, same_ip_24h: int) -> dict:
    return {
        "id": row.id,
        "reference": row.reference,
        "name": row.name,
        "email": row.email,
        "phone": row.phone,
        "organisation": row.organisation or "",
        "requested_role": row.requested_role,
        "note": row.note or "",
        "status": row.status,
        "created_at": iso_utc(row.created_at),
        "decided_at": iso_utc(row.decided_at) if row.decided_at else None,
        "decided_by_name": row.decided_by_name or "",
        "decision_note": row.decision_note or "",
        "has_account": has_account,
        "same_email_pending": same_email_pending,
        "same_ip_24h": same_ip_24h,
    }


def _requests_with_flags(db, rows: list[AccessRequest]) -> list[dict]:
    """Adds what an admin should notice before approving: the email already
    has an account, others asked with the same email, or many requests came
    from one network."""
    emails = {u.email for u in db.query(User.email).all()}
    everything = db.query(AccessRequest).all()
    day_ago = utc_now() - timedelta(days=1)
    out = []
    for row in rows:
        same_email = sum(1 for r in everything if r.id != row.id and r.status == "pending" and r.email == row.email)
        same_ip = sum(
            1 for r in everything
            if r.id != row.id and row.ip and r.ip == row.ip and as_utc(r.created_at) >= day_ago
        )
        out.append(_request_dict(row, has_account=row.email in emails, same_email_pending=same_email, same_ip_24h=same_ip))
    return out


def _one_request(db, row: AccessRequest) -> dict:
    return _requests_with_flags(db, [row])[0]


@router.get("/access-requests")
def list_access_requests(status: Literal["pending", "all"] = "pending", admin: CurrentUser = Depends(require_admin)) -> list[dict]:
    db = SessionLocal()
    try:
        query = db.query(AccessRequest)
        if status == "pending":
            query = query.filter(AccessRequest.status == "pending")
        return _requests_with_flags(db, query.order_by(AccessRequest.created_at.desc()).all())
    finally:
        db.close()


def _pending_request(db, request_id: str) -> AccessRequest:
    row = db.get(AccessRequest, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Request not found.")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail=f"This request has already been {row.status}.")
    return row


class Approval(BaseModel):
    # No default: the admin decides, whatever the request asked for.
    role: Role
    expires_hours: ExpiryHours = DEFAULT_INVITE_HOURS


@router.post("/access-requests/{request_id}/approve")
def approve_access_request(request_id: str, body: Approval, admin: CurrentUser = Depends(require_admin)) -> dict:
    db = SessionLocal()
    try:
        row = _pending_request(db, request_id)
        if db.query(User).filter(User.email == row.email).first():
            raise HTTPException(status_code=409, detail="There's already an account for this email. Use its Reset password instead.")
        link, token = new_link(
            db, purpose="invite", email=row.email, role=body.role, creator=admin,
            hours=body.expires_hours, name=row.name, request_id=row.id,
        )
        row.status = "approved"
        row.decided_at = utc_now()
        row.decided_by = admin.id
        row.decided_by_name = admin.name
        row.link_id = link.id
        audit(db, admin, "access_request_approved", detail=f"{row.email} as {body.role} ({row.reference})")
        db.commit()
        return {"request": _one_request(db, row), "link": link_dict(link), "token": token}
    finally:
        db.close()


class Rejection(BaseModel):
    note: str = Field(default="", max_length=300)


@router.post("/access-requests/{request_id}/reject")
def reject_access_request(request_id: str, body: Rejection, admin: CurrentUser = Depends(require_admin)) -> dict:
    db = SessionLocal()
    try:
        row = _pending_request(db, request_id)
        row.status = "rejected"
        row.decided_at = utc_now()
        row.decided_by = admin.id
        row.decided_by_name = admin.name
        row.decision_note = body.note.strip()
        audit(db, admin, "access_request_rejected", detail=f"{row.email} ({row.reference})")
        db.commit()
        return _one_request(db, row)
    finally:
        db.close()


@router.delete("/access-requests/{request_id}")
def delete_access_request(request_id: str, admin: CurrentUser = Depends(require_admin)) -> dict:
    db = SessionLocal()
    try:
        row = db.get(AccessRequest, request_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Request not found.")
        audit(db, admin, "access_request_deleted", detail=f"{row.email} ({row.reference})")
        db.delete(row)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.get("/account-links")
def list_account_links(admin: CurrentUser = Depends(require_admin)) -> list[dict]:
    db = SessionLocal()
    try:
        return [link_dict(link) for link in db.query(AccountLink).order_by(AccountLink.created_at.desc()).all()]
    finally:
        db.close()


class NewInvite(BaseModel):
    email: str = Field(max_length=320)
    role: Role
    name: str = Field(default="", max_length=120)
    expires_hours: ExpiryHours = DEFAULT_INVITE_HOURS


@router.post("/account-links")
def create_invite(body: NewInvite, admin: CurrentUser = Depends(require_admin)) -> dict:
    db = SessionLocal()
    try:
        email = _checked_email(db, body.email)
        link, token = new_link(
            db, purpose="invite", email=email, role=body.role, creator=admin,
            hours=body.expires_hours, name=" ".join(body.name.split()),
        )
        audit(db, admin, "invite_created", detail=f"{email} as {body.role}")
        db.commit()
        return {"link": link_dict(link), "token": token}
    finally:
        db.close()


@router.delete("/account-links/{link_id}")
def revoke_link(link_id: str, admin: CurrentUser = Depends(require_admin)) -> dict:
    db = SessionLocal()
    try:
        link = db.get(AccountLink, link_id)
        if link is None:
            raise HTTPException(status_code=404, detail="Link not found.")
        if link.used_at:
            raise HTTPException(status_code=409, detail="This link has already been used.")
        if not link.revoked_at:
            link.revoked_at = utc_now()
            audit(db, admin, "link_revoked", detail=f"{link.purpose} for {link.email}")
            db.commit()
        return link_dict(link)
    finally:
        db.close()


class ResetLinkIn(BaseModel):
    expires_hours: ExpiryHours = DEFAULT_RESET_HOURS


@router.post("/users/{user_id}/reset-link")
def create_reset_link(user_id: str, body: ResetLinkIn | None = None, admin: CurrentUser = Depends(require_admin)) -> dict:
    """Replaces handing out a temporary password: the admin never sees or
    chooses the new one (OWASP ASVS 6.4.6). The old password keeps working
    until the link is used, so a forgotten-then-remembered password isn't
    locked out by the request."""
    body = body or ResetLinkIn()
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found.")
        if user.id == admin.id:
            raise HTTPException(status_code=409, detail="Use Change password for your own account.")
        if not user.active:
            raise HTTPException(status_code=409, detail="Enable the account first.")
        link, token = new_link(
            db, purpose="reset", email=user.email, role=user.role, creator=admin,
            hours=body.expires_hours, name=user.name, user_id=user.id,
        )
        audit(db, admin, "reset_link_created", detail=user.email)
        db.commit()
        return {"link": link_dict(link), "token": token}
    finally:
        db.close()
