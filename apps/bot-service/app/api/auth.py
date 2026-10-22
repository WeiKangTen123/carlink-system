"""Sign-in, the signed-in user's own account, and user management.

    POST  /auth/login                  email + password -> session cookie
    POST  /auth/logout
    GET   /auth/me                     the signed-in user
    PUT   /auth/me                     own name and surveyor profile
    POST  /auth/password               change own password
    GET   /users                       admin: every account
    POST  /users                       admin: add an account (temporary password)
    PATCH /users/{id}                  admin: edit, change role, disable/enable
    POST  /users/{id}/reset-password   admin: new temporary password

There is no sign-up and no emailed reset: the first admin is created on the
server (python -m app.auth.manage), and a forgotten password is reset by an
admin, who passes the temporary password on.
"""
import re
import threading
import time
from collections import deque
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.auth.core import (
    SESSION_COOKIE,
    CurrentUser,
    active_admin_count,
    audit,
    create_session,
    end_session,
    end_user_sessions,
    locked_for,
    max_age,
    me_dict,
    normalize_email,
    record_failed_login,
    record_login,
    token_hash,
    user_dict,
)
from app.auth.deps import current_user, require_admin
from app.auth.passwords import (
    MAX_PASSWORD_LENGTH,
    burn_time,
    hash_password,
    password_problem,
    temporary_password,
    verify_password,
)
from app.config import settings
from app.reports.db import SessionLocal
from app.reports.models import User

router = APIRouter(tags=["auth"])

WRONG_CREDENTIALS = "Email or password is wrong."
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

# Per-network limit on wrong passwords, on top of the per-account lockout,
# so one address can't try a few passwords on every account. In memory: the
# API is a single process, and a restart forgetting it is harmless.
_IP_MAX_FAILURES = 20
_IP_WINDOW_SECONDS = 15 * 60
_ip_failures: dict[str, deque] = {}
_ip_lock = threading.Lock()


def client_ip(request: Request) -> str:
    # Set by infra/nginx.conf from the real client address (its realip
    # settings trust the host nginx in front of it); absent in local dev.
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "")


def _ip_blocked(ip: str) -> bool:
    now = time.monotonic()
    with _ip_lock:
        failures = _ip_failures.get(ip)
        while failures and now - failures[0] > _IP_WINDOW_SECONDS:
            failures.popleft()
        return bool(failures) and len(failures) >= _IP_MAX_FAILURES


def _note_ip_failure(ip: str) -> None:
    with _ip_lock:
        _ip_failures.setdefault(ip, deque()).append(time.monotonic())


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(max_age().total_seconds()),
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def _minutes(seconds: float) -> str:
    m = max(1, round(seconds / 60))
    return f"{m} minute{'s' if m != 1 else ''}"


class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    ip = client_ip(request)
    if _ip_blocked(ip):
        raise HTTPException(status_code=429, detail="Too many wrong passwords from this network. Try again in 15 minutes.")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == normalize_email(body.email)).first()
        if user is None:
            burn_time(body.password)
            _note_ip_failure(ip)
            raise HTTPException(status_code=401, detail=WRONG_CREDENTIALS)

        # Checked before the password, so a locked account says nothing
        # about whether a guess was right.
        wait = locked_for(user)
        if wait:
            raise HTTPException(status_code=429, detail=f"Too many wrong passwords. Try again in {_minutes(wait.total_seconds())}.")

        if not verify_password(body.password, user.password_hash):
            record_failed_login(user)
            _note_ip_failure(ip)
            locked = locked_for(user)
            if locked:
                audit(db, None, "account_locked", detail=user.email)
            db.commit()
            if locked:
                raise HTTPException(status_code=429, detail=f"Too many wrong passwords. Try again in {_minutes(locked.total_seconds())}.")
            raise HTTPException(status_code=401, detail=WRONG_CREDENTIALS)

        if not user.active:
            raise HTTPException(status_code=403, detail="This account is disabled. Ask your admin.")

        record_login(user)
        token = create_session(db, user, ip, request.headers.get("user-agent", ""))
        db.commit()
        _set_session_cookie(response, token)
        db.refresh(user)
        return me_dict(user)
    finally:
        db.close()


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict:
    """Public (see AuthMiddleware), so a stale cookie can always be cleared."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db = SessionLocal()
        try:
            end_session(db, token_hash(token))
            db.commit()
        finally:
            db.close()
    response.delete_cookie(SESSION_COOKIE, path="/", secure=settings.auth_cookie_secure, httponly=True, samesite="lax")
    return {"ok": True}


@router.get("/auth/me")
def me(user: CurrentUser = Depends(current_user)) -> dict:
    return me_dict(user)


class ProfileFields(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    qualifications: Optional[str] = Field(default=None, max_length=200)
    license_number: Optional[str] = Field(default=None, max_length=60)
    firm_name: Optional[str] = Field(default=None, max_length=160)


def _apply_profile(row: User, body: ProfileFields) -> None:
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(status_code=400, detail="Name can't be empty.")
        row.name = body.name.strip()
    for field in ("qualifications", "license_number", "firm_name"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value.strip())


@router.put("/auth/me")
def update_me(body: ProfileFields, user: CurrentUser = Depends(current_user)) -> dict:
    db = SessionLocal()
    try:
        row = db.get(User, user.id)
        _apply_profile(row, body)
        db.commit()
        db.refresh(row)
        return me_dict(row)
    finally:
        db.close()


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(max_length=MAX_PASSWORD_LENGTH)


@router.post("/auth/password")
def change_password(body: PasswordChange, user: CurrentUser = Depends(current_user)) -> dict:
    db = SessionLocal()
    try:
        row = db.get(User, user.id)
        if not verify_password(body.current_password, row.password_hash):
            raise HTTPException(status_code=400, detail="Your current password is wrong.")
        problem = password_problem(body.new_password)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        if body.new_password == body.current_password:
            raise HTTPException(status_code=400, detail="Choose a password different from the current one.")
        row.password_hash = hash_password(body.new_password)
        row.must_change_password = False
        # Signs out every other browser, e.g. one that has the old password.
        end_user_sessions(db, row.id, keep_hash=user.session_hash)
        audit(db, user, "password_changed", detail=row.email)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ---------------------------------------------------------------- admin


class NewUser(ProfileFields):
    email: str = Field(max_length=320)
    name: str = Field(max_length=120)
    role: Literal["admin", "surveyor"] = "surveyor"


class UserUpdate(ProfileFields):
    email: Optional[str] = Field(default=None, max_length=320)
    role: Optional[Literal["admin", "surveyor"]] = None
    active: Optional[bool] = None


def _checked_email(db, email: str, exclude_id: str | None = None) -> str:
    email = normalize_email(email)
    if not _EMAIL.fullmatch(email):
        raise HTTPException(status_code=400, detail="That doesn't look like an email address.")
    clash = db.query(User).filter(User.email == email)
    if exclude_id:
        clash = clash.filter(User.id != exclude_id)
    if clash.first():
        raise HTTPException(status_code=409, detail="There is already an account with that email.")
    return email


@router.get("/users")
def list_users(admin: CurrentUser = Depends(require_admin)) -> list[dict]:
    db = SessionLocal()
    try:
        return [user_dict(u) for u in db.query(User).order_by(User.active.desc(), User.name).all()]
    finally:
        db.close()


@router.post("/users")
def create_user(body: NewUser, admin: CurrentUser = Depends(require_admin)) -> dict:
    db = SessionLocal()
    try:
        password = temporary_password()
        row = User(
            email=_checked_email(db, body.email),
            name="",
            role=body.role,
            password_hash=hash_password(password),
            must_change_password=True,
        )
        _apply_profile(row, body)
        db.add(row)
        audit(db, admin, "user_created", detail=f"{row.email} ({row.role})")
        db.commit()
        db.refresh(row)
        return {"user": user_dict(row), "temporary_password": password}
    finally:
        db.close()


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, admin: CurrentUser = Depends(require_admin)) -> dict:
    db = SessionLocal()
    try:
        row = db.get(User, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="User not found.")
        if body.active is False and row.id == admin.id:
            raise HTTPException(status_code=409, detail="You can't disable your own account.")
        losing_admin = row.role == "admin" and row.active and (body.role == "surveyor" or body.active is False)
        if losing_admin and active_admin_count(db) <= 1:
            raise HTTPException(status_code=409, detail="Keep at least one active admin.")

        if body.email is not None:
            row.email = _checked_email(db, body.email, exclude_id=row.id)
        if body.role is not None:
            row.role = body.role
        _apply_profile(row, body)
        if body.active is not None and body.active != row.active:
            row.active = body.active
            if not body.active:
                end_user_sessions(db, row.id)
            audit(db, admin, "user_enabled" if body.active else "user_disabled", detail=row.email)
        else:
            audit(db, admin, "user_updated", detail=row.email)
        db.commit()
        db.refresh(row)
        return user_dict(row)
    finally:
        db.close()


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: str, admin: CurrentUser = Depends(require_admin)) -> dict:
    db = SessionLocal()
    try:
        row = db.get(User, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="User not found.")
        if row.id == admin.id:
            raise HTTPException(status_code=409, detail="Use Change password for your own account.")
        password = temporary_password()
        row.password_hash = hash_password(password)
        row.must_change_password = True
        row.failed_logins = 0
        row.locked_until = None
        end_user_sessions(db, row.id)
        audit(db, admin, "password_reset", detail=row.email)
        db.commit()
        db.refresh(row)
        return {"user": user_dict(row), "temporary_password": password}
    finally:
        db.close()

