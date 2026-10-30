"""Signing up, and the one-time links that create accounts and reset
passwords.

Public (app/auth/middleware.PUBLIC_PATHS):
    GET    /auth/signup                   are new signups open?
    POST   /auth/signup                   username + email + password -> signed in
    POST   /auth/links/lookup             what a link is for, without using it
    POST   /auth/links/accept             use it: choose a username/password, get signed in
Admin:
    PUT    /signup-settings               switch new signups on or off
    GET    /account-links                 invites and reset links, never tokens
    POST   /account-links                 invite someone directly
    DELETE /account-links/{id}            revoke
    POST   /users/{id}/reset-link         one-time link to set a new password

Signup is open (the owner's choice, 2026-10-25): anyone with the address can
make an account and is in at once as a "user"; only an admin makes someone
an admin. There is no email service, so the email isn't verified. The
safety valves are the admins' on/off switch, disabling an account, the
per-network and per-day limits below, and the history of who signed up.
Until 2026-10-25 people requested access and an admin sent them a link.

Link tokens travel in the link's #fragment and in POST bodies -- never in a
URL path -- so they stay out of access logs, Referer headers and link
previews.
"""
import threading
import time
from collections import deque
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.api.auth import (
    _EMAIL,
    _checked_email,
    _ip_blocked,
    _note_ip_failure,
    _set_session_cookie,
    checked_username,
    clean_full_name,
    client_ip,
    username_input,
    username_issue,
    visible_name,
)
from app.auth.chat_links import end_chat_links
from app.auth.core import (
    SESSION_COOKIE,
    CurrentUser,
    audit,
    create_session,
    end_session,
    end_user_sessions,
    me_dict,
    normalize_email,
    record_login,
    token_hash,
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
from app.reports.models import AccountLink, AppSetting, User, iso_utc

router = APIRouter(tags=["signup"])

ExpiryHours = Literal[24, 72, 168]
Role = Literal["user", "admin"]

# Per network: a person tries a username or two; a script tries hundreds.
_SIGNUP_TRIES_PER_IP = 20
_SIGNUP_WINDOW_SECONDS = 60 * 60
# Active accounts made in the last 24 hours. Beyond it signup pauses, so a
# flood from many networks can't fill the users table before an admin sees
# it; disabling the junk accounts frees the room again.
MAX_SIGNUPS_PER_DAY = 50

_signup_tries: dict[str, deque] = {}
_signup_lock = threading.Lock()

# app_settings key; anything but "false" means open.
SIGNUPS_OPEN_KEY = "signups_open"
SIGNUPS_CLOSED = "New signups are closed. Ask the Carlink admin to invite you."

LINK_GONE = {
    "expired": "This link has expired. Ask your admin for a new one.",
    "used": "This link has already been used. Sign in with your email and password.",
    "invalid": "This link doesn't work. Check you copied all of it, or ask your admin for a new one.",
}


def _too_many_tries(ip: str) -> bool:
    now = time.monotonic()
    with _signup_lock:
        if len(_signup_tries) > 5000:
            _signup_tries.clear()
        times = _signup_tries.setdefault(ip, deque())
        while times and now - times[0] > _SIGNUP_WINDOW_SECONDS:
            times.popleft()
        if len(times) >= _SIGNUP_TRIES_PER_IP:
            return True
        times.append(now)
        return False


def _gone(code: str) -> JSONResponse:
    return JSONResponse(status_code=410, content={"detail": LINK_GONE[code], "code": code})


def _field_error(status: int, field: str, message: str) -> JSONResponse:
    """An error the signup form shows under one box."""
    return JSONResponse(status_code=status, content={"detail": message, "field": field})


def signups_open(db) -> bool:
    row = db.get(AppSetting, SIGNUPS_OPEN_KEY)
    return not (row and row.value == "false")


def _open_invite(db, email: str) -> bool:
    return db.query(AccountLink).filter(
        AccountLink.email == email,
        AccountLink.purpose == "invite",
        AccountLink.used_at.is_(None),
        AccountLink.revoked_at.is_(None),
        AccountLink.expires_at > utc_now(),
    ).first() is not None


def _end_previous_session(db, request: Request) -> None:
    """Someone else signed in on this browser: their session ends here, not
    merely loses its cookie, so a copy of that cookie is useless."""
    previous = request.cookies.get(SESSION_COOKIE)
    if previous:
        end_session(db, token_hash(previous))


# ------------------------------------------------------------ public


@router.get("/auth/signup")
def signup_status() -> dict:
    db = SessionLocal()
    try:
        return {"open": signups_open(db)}
    finally:
        db.close()


class SignupIn(BaseModel):
    username: str = Field(max_length=60)
    email: str = Field(max_length=320)
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)
    # Honeypot: hidden from people by CSS, filled in by form-spamming bots.
    website: str = Field(default="", max_length=200)


@router.post("/auth/signup")
def signup(body: SignupIn, request: Request, response: Response):
    """Makes a "user" account and signs it in. Unlike sign-in, this does say
    when a username or email is taken -- a signup form can't avoid it -- so
    it's limited per network and per day."""
    ip = client_ip(request)
    if _ip_blocked(ip) or _too_many_tries(ip):
        raise HTTPException(status_code=429, detail="Too many signups from this network. Try again in an hour.")
    if body.website:
        raise HTTPException(status_code=400, detail="Couldn't create the account.")

    username = username_input(body.username)
    email = normalize_email(body.email)
    db = SessionLocal()
    try:
        if not signups_open(db):
            raise HTTPException(status_code=403, detail=SIGNUPS_CLOSED)
        issue = username_issue(db, username)
        if issue:
            return _field_error(issue[0], "username", issue[1])
        if not _EMAIL.fullmatch(email):
            return _field_error(400, "email", "That doesn't look like an email address.")
        if db.query(User).filter(User.email == email).first():
            return _field_error(409, "email", "There's already an account with that email. Sign in instead.")
        # An admin is expecting this person to come in through their link
        # (maybe as an admin); a stranger mustn't claim the address first.
        if _open_invite(db, email):
            return _field_error(409, "email", "You've been invited with this email. Use the link your admin sent you.")
        problem = password_problem(body.password, (username, email))
        if problem:
            return _field_error(400, "password", problem)

        day_ago = utc_now() - timedelta(days=1)
        recent = db.query(User).filter(User.created_at >= day_ago, User.active.is_(True)).count()
        if recent >= MAX_SIGNUPS_PER_DAY:
            raise HTTPException(status_code=503, detail="Lots of people signed up today. Try again tomorrow.")

        user = User(
            username=username,
            email=email,
            # The name printed on sign-offs, until they set their full name.
            name=username,
            role="user",
            password_hash=hash_password(body.password),
            must_change_password=False,
        )
        try:
            db.add(user)
            # The INSERT happens here (the audit row needs the user's id), so
            # a unique-index clash is raised here, not at the commit.
            db.flush()
            audit(db, user, "signed_up", detail=email)
            _end_previous_session(db, request)
            record_login(user)
            token = create_session(db, user, ip, request.headers.get("user-agent", ""))
            db.commit()
        except IntegrityError:
            # The same username or email signed up a moment ago.
            db.rollback()
            taken_email = db.query(User).filter(User.email == email).first() is not None
            field = "email" if taken_email else "username"
            message = "There's already an account with that email. Sign in instead." if taken_email else "That username is taken."
            return _field_error(409, field, message)
        _set_session_cookie(response, token)
        db.refresh(user)
        return me_dict(user)
    finally:
        db.close()


class LinkToken(BaseModel):
    token: str = Field(max_length=200)


class LinkAccept(LinkToken):
    # An invite chooses one, like signup; a reset keeps the account's.
    username: str = Field(default="", max_length=60)
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
        username = ""
        if link.purpose == "invite":
            username = checked_username(db, body.username)
            problem = password_problem(body.password, (username, link.name, link.email))
        else:
            problem = password_problem(body.password, (user.name, user.username or "", user.email))
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        # Hashed before the link is claimed: a busy hasher (503) leaves the
        # link unused for a retry.
        password_hash = hash_password(body.password)

        if not claim(db, link):
            db.rollback()
            return _gone("used")
        try:
            if link.purpose == "invite":
                if db.query(User).filter(User.email == link.email).first():
                    db.rollback()
                    raise HTTPException(status_code=409, detail=f"There's already an account for {link.email}. Sign in instead.")
                user = User(
                    # The name the admin typed, if any, for sign-offs.
                    email=link.email, username=username, name=visible_name(link.name) or username, role=link.role,
                    password_hash=password_hash, must_change_password=False,
                )
                db.add(user)
                # The INSERT (and any unique-index clash) happens here.
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
                # And any Telegram connected with it (agent6 H2).
                end_chat_links(db, user.id, user, reason="password reset with a link")
            link.used_by_user_id = user.id
            _end_previous_session(db, request)
            record_login(user)
            token = create_session(db, user, ip, request.headers.get("user-agent", ""))
            db.commit()
        except IntegrityError:
            # The email or username was taken between the check and the
            # INSERT; the rollback also puts the link back to unused.
            db.rollback()
            if db.query(User).filter(User.email == link.email).first():
                raise HTTPException(status_code=409, detail=f"There's already an account for {link.email}. Sign in instead.")
            raise HTTPException(status_code=409, detail="That username is taken.")
        _set_session_cookie(response, token)
        db.refresh(user)
        return me_dict(user)
    finally:
        db.close()


# ------------------------------------------------------------- admin


class SignupSettings(BaseModel):
    open: bool


@router.put("/signup-settings")
def set_signups_open(body: SignupSettings, admin: CurrentUser = Depends(require_admin)) -> dict:
    """Not under /auth/signup: that path is public, so a session is never
    looked up for it."""
    db = SessionLocal()
    try:
        row = db.get(AppSetting, SIGNUPS_OPEN_KEY)
        value = "true" if body.open else "false"
        if row:
            row.value = value
        else:
            db.add(AppSetting(key=SIGNUPS_OPEN_KEY, value=value))
        audit(db, admin, "signups_opened" if body.open else "signups_closed")
        db.commit()
        return {"open": body.open}
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
            hours=body.expires_hours, name=clean_full_name(body.name) if body.name.strip() else "",
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
