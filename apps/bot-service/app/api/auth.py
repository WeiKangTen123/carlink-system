"""Sign-in, the signed-in user's own account, and user management.

    POST  /auth/login                  email or username + password -> session cookie
    POST  /auth/logout
    GET   /auth/me                     the signed-in user
    PUT   /auth/me                     own username, name and surveyor profile
    POST  /auth/password               change own password
    GET   /users                       admin: every account, with its case counts
    POST  /users                       admin: add an account (temporary password; API only)
    PATCH /users/{id}                  admin: edit, change role and ticks, disable (saying what
                                       happens to their open cases) / enable
    POST  /users/{id}/reset-password   admin: new temporary password

The first admin is created on the server (python -m app.auth.manage).
Everyone else signs up on /signup or is invited, and a forgotten password
is reset with a one-time link -- see app/api/signup.py. There is no email
service: admins send links themselves.
"""
import re
import threading
import time
from collections import deque
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.ai import keys
from app.auth.core import (
    LOCKOUT,
    MAX_FAILED_LOGINS,
    SESSION_COOKIE,
    CurrentUser,
    active_admin_count,
    audit,
    create_session,
    end_session,
    end_user_sessions,
    full_name_problem,
    locked_for,
    max_age,
    me_dict,
    normalize_email,
    normalize_username,
    record_failed_login,
    record_login,
    token_hash,
    user_dicts,
    username_from_email,
    username_problem,
    username_taken,
)
from app.auth.deps import ApiError, current_user, not_allowed, require_admin
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
from app.reports.owners import TICKS, assigned_detail, reassign_open_cases

router = APIRouter(tags=["auth"])

WRONG_CREDENTIALS = "Email, username or password is wrong."
# Plain ASCII only, checked after lower-casing. Emails aren't verified, so a
# zero-width space or a Cyrillic look-alike would otherwise make an address
# that reads exactly like someone else's in the users list.
_EMAIL = re.compile(r"[a-z0-9._%+'-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}")

# Changing your own username answers "is this one taken?", so it is limited
# per account like signup is per network.
_USERNAME_CHANGES_PER_HOUR = 10
_username_changes: dict[str, deque] = {}

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


# Emails with no account get the same lockout as real ones (5 wrong, then
# 15 minutes of 429), or the lockout itself would tell which emails exist.
_unknown_failures: dict[str, list] = {}  # email -> [failures, locked_until]


def _unknown_locked_for(email: str) -> float | None:
    with _ip_lock:
        entry = _unknown_failures.get(email)
        if entry and entry[1] > time.monotonic():
            return entry[1] - time.monotonic()
        return None


def _note_unknown_failure(email: str) -> float | None:
    """Returns seconds locked if this failure locks the email."""
    with _ip_lock:
        if len(_unknown_failures) > 5000:
            _unknown_failures.clear()
        entry = _unknown_failures.setdefault(email, [0, 0.0])
        entry[0] += 1
        if entry[0] >= MAX_FAILED_LOGINS:
            entry[0] = 0
            entry[1] = time.monotonic() + LOCKOUT.total_seconds()
            return LOCKOUT.total_seconds()
        return None


def _profile_changes(row: User, fields: tuple[str, ...]) -> dict:
    return {f: getattr(row, f) for f in fields}


def _describe_changes(before: dict, row: User) -> str:
    """'role: surveyor -> admin; license_number: '' -> 'LIC-9'' for the
    history, so a profile or role change can be traced afterwards."""
    parts = [f"{f}: {before[f]!r} -> {getattr(row, f)!r}" for f in before if before[f] != getattr(row, f)]
    return "; ".join(parts)


_PROFILE_FIELDS = ("username", "name", "qualifications", "license_number", "firm_name")


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
    # Email or username: anything with an "@" is taken as an email.
    email: str = Field(max_length=320)
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)


def find_by_login(db, login: str) -> tuple[User | None, str]:
    """The account an email or username belongs to, and the normalized
    form (the key unknown names are locked out under)."""
    if "@" in login:
        email = normalize_email(login)
        return db.query(User).filter(User.email == email).first(), email
    username = normalize_username(login)
    return db.query(User).filter(User.username == username).first(), username


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    ip = client_ip(request)
    if _ip_blocked(ip):
        raise HTTPException(status_code=429, detail="Too many wrong passwords from this network. Try again in 15 minutes.")

    db = SessionLocal()
    try:
        user, login_key = find_by_login(db, body.email)
        if user is None:
            wait_s = _unknown_locked_for(login_key)
            if wait_s:
                raise HTTPException(status_code=429, detail=f"Too many wrong passwords. Try again in {_minutes(wait_s)}.")
            burn_time(body.password)
            _note_ip_failure(ip)
            locked_s = _note_unknown_failure(login_key)
            if locked_s:
                raise HTTPException(status_code=429, detail=f"Too many wrong passwords. Try again in {_minutes(locked_s)}.")
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

        # Same answer as a wrong password: a disabled account mustn't
        # confirm that a guessed password is right.
        if not user.active:
            raise HTTPException(status_code=401, detail=WRONG_CREDENTIALS)

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
    # Case photos are cached for a year (/files), and they'd stay readable
    # on a shared computer after signing out. The browser calls this
    # directly (components/UserMenu.tsx), so the header reaches it.
    response.headers["Clear-Site-Data"] = '"cache", "storage"'
    return {"ok": True}


@router.get("/auth/me")
def me(user: CurrentUser = Depends(current_user)) -> dict:
    return me_dict(user)


class ProfileFields(BaseModel):
    username: Optional[str] = Field(default=None, max_length=60)
    name: Optional[str] = Field(default=None, max_length=120)
    qualifications: Optional[str] = Field(default=None, max_length=200)
    license_number: Optional[str] = Field(default=None, max_length=60)
    firm_name: Optional[str] = Field(default=None, max_length=160)


def username_issue(db, username: str, exclude_id: str | None = None, *, self_chosen: bool = True) -> tuple[int, str] | None:
    """(status, message) if a normalized username can't be used, else None.
    self_chosen=False only when an admin sets it."""
    problem = username_problem(username, self_chosen=self_chosen)
    if problem:
        return 400, problem
    if username_taken(db, username, exclude_id):
        return 409, "That username is taken."
    return None


def _commit_or_taken(db, *, flush_only: bool = False) -> None:
    """Commit (or only flush, keeping the transaction open), turning a
    unique-index clash (the same username or email claimed a moment ago by
    someone else) into a 409 rather than a 500."""
    try:
        if flush_only:
            db.flush()
        else:
            db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="That username or email was just taken by another account.")


def checked_username(db, username: str, exclude_id: str | None = None, *, self_chosen: bool = True) -> str:
    """The normalized username, or a 400/409 saying why it can't be used."""
    username = normalize_username(username)
    issue = username_issue(db, username, exclude_id, self_chosen=self_chosen)
    if issue:
        raise HTTPException(status_code=issue[0], detail=issue[1])
    return username


def _username_change_allowed(user_id: str) -> bool:
    now = time.monotonic()
    with _ip_lock:
        times = _username_changes.setdefault(user_id, deque())
        while times and now - times[0] > 3600:
            times.popleft()
        if len(times) >= _USERNAME_CHANGES_PER_HOUR:
            return False
        times.append(now)
        return True


def _apply_profile(db, row: User, body: ProfileFields, *, by_admin: bool) -> None:
    """by_admin: an admin editing an account may use reserved words and
    any full name; people naming themselves may not (core.full_name_problem)."""
    if body.username is not None:
        new = normalize_username(body.username)
        if new != (row.username or ""):
            if not by_admin and not _username_change_allowed(row.id):
                raise HTTPException(status_code=429, detail="Too many username changes. Try again in an hour.")
            row.username = checked_username(db, new, exclude_id=row.id, self_chosen=not by_admin)
    if body.name is not None:
        name = " ".join(body.name.split())
        if not name:
            raise HTTPException(status_code=400, detail="Name can't be empty.")
        if not by_admin and name != row.name:
            problem = full_name_problem(db, name, exclude_id=row.id)
            if problem:
                raise HTTPException(status_code=400, detail=problem)
        row.name = name
    for field in ("qualifications", "license_number", "firm_name"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value.strip())


@router.put("/auth/me")
def update_me(body: ProfileFields, user: CurrentUser = Depends(current_user)) -> dict:
    db = SessionLocal()
    try:
        row = db.get(User, user.id)
        before = _profile_changes(row, _PROFILE_FIELDS)
        _apply_profile(db, row, body, by_admin=False)
        changes = _describe_changes(before, row)
        if changes:
            # Username, name and licence show in the history and on
            # sign-offs; keep a record of every change to them.
            audit(db, user, "profile_updated", detail=f"{row.email}: {changes}")
        _commit_or_taken(db)
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
            # Counts toward the lockout, so a borrowed session can't be used
            # to guess the password; locking also ends every session.
            record_failed_login(row)
            if locked_for(row):
                end_user_sessions(db, row.id)
                audit(db, None, "account_locked", detail=row.email)
                db.commit()
                raise HTTPException(
                    status_code=429,
                    detail="Too many wrong passwords. You've been signed out; try again in 15 minutes.",
                )
            db.commit()
            raise HTTPException(status_code=400, detail="Your current password is wrong.")
        problem = password_problem(body.new_password, (row.name, row.username or "", row.email))
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        if body.new_password == body.current_password:
            raise HTTPException(status_code=400, detail="Choose a password different from the current one.")
        row.password_hash = hash_password(body.new_password)
        row.must_change_password = False
        row.failed_logins = 0
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
    role: Literal["admin", "user"] = "user"
    # The ticks, as on PATCH /users/{id}; off unless sent as true. Ignored
    # for an admin, who has all of it anyway.
    sees_all_cases: Optional[bool] = None
    ai_company_key_allowed: Optional[bool] = None
    may_sign_off: Optional[bool] = None


class UserUpdate(ProfileFields):
    email: Optional[str] = Field(default=None, max_length=320)
    role: Optional[Literal["admin", "user"]] = None
    active: Optional[bool] = None
    # The ticks (models.User, app/auth/permissions.py).
    sees_all_cases: Optional[bool] = None
    ai_company_key_allowed: Optional[bool] = None
    may_sign_off: Optional[bool] = None
    # Only with active: false, and used when it disables the account: their
    # open cases stay theirs ("keep"), become Unassigned, or go to the
    # active account open_cases_to ("move"); the response's "moved" says
    # how many. Signed-off cases and unfinished drafts stay theirs
    # whichever is chosen.
    open_cases: Literal["keep", "unassign", "move"] = "keep"
    open_cases_to: Optional[str] = Field(default=None, max_length=64)


def _apply_ticks(row: User, body: UserUpdate | NewUser) -> str:
    """Sets the ticks the request carries; returns what changed, for the
    history. Unset (None) and off read the same, so None -> off is no change."""
    changes = []
    for tick in TICKS:
        value = getattr(body, tick)
        if value is not None and bool(getattr(row, tick)) != value:
            changes.append(f"{tick}: {bool(getattr(row, tick))!r} -> {value!r}")
            setattr(row, tick, value)
    return "; ".join(changes)


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


LAST_ADMIN = "Keep at least one active admin."
OWNER_INACTIVE = "That account is disabled. Enable it first, or choose someone else."


def case_recipient(db, user_id: str, from_user_id: str) -> User:
    """The account someone's cases are to go to (disabling them here, POST
    /reports/reassign), or why it can't be: like PATCH /reports/{id}/owner,
    a disabled account is 409 owner_inactive."""
    if user_id == from_user_id:
        raise ApiError(422, "Choose someone else to give the cases to.", "same_owner")
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="The account to give the cases to wasn't found.")
    if not target.active:
        raise ApiError(409, OWNER_INACTIVE, "owner_inactive")
    return target


def recipient_still_active(db, recipient: User | None) -> None:
    """case_recipient again, once the request holds SQLite's write lock (it
    has written): the account may have been disabled since the first check,
    and must not end up owning the cases. Refused with everything rolled
    back, so nothing moves."""
    if recipient is None:
        return
    # A column, not db.get: that would answer from the row loaded earlier.
    if not db.query(User.active).filter(User.id == recipient.id).scalar():
        db.rollback()
        raise ApiError(409, OWNER_INACTIVE, "owner_inactive")


def _disable(db, row: User, admin: CurrentUser, open_cases: str, recipient: User | None) -> int | None:
    """Turns an account off. Everything goes into the caller's one commit,
    so a failure part-way leaves the account, its sessions and its cases
    as they were. Returns how many open cases moved, or None for "keep"."""
    row.active = False
    end_user_sessions(db, row.id)
    moved, outcome = None, ""
    if open_cases != "keep":
        # "unassign" has no recipient: the cases become Unassigned.
        moved = reassign_open_cases(db, row.id, recipient, admin)
        outcome = f"{moved} open case{'s' if moved != 1 else ''}: {assigned_detail(recipient)}"
    # Their own AI key goes too, in this same transaction: a disabled
    # account's key is never used, and isn't kept for later.
    keys.delete_user_key(db, row.id, admin, action="ai_key_removed_by_admin", reason="account disabled")
    audit(db, admin, "user_disabled", detail="; ".join(filter(None, [row.email, outcome])))
    return moved


@router.get("/users")
def list_users(admin: CurrentUser = Depends(require_admin)) -> list[dict]:
    db = SessionLocal()
    try:
        return user_dicts(db, db.query(User).order_by(User.active.desc(), User.username, User.name).all())
    finally:
        db.close()


@router.post("/users")
def create_user(body: NewUser, admin: CurrentUser = Depends(require_admin)) -> dict:
    db = SessionLocal()
    try:
        password = temporary_password()
        email = _checked_email(db, body.email)
        row = User(
            email=email,
            username=checked_username(db, body.username, self_chosen=False) if body.username else username_from_email(db, email),
            name="",
            role=body.role,
            password_hash=hash_password(password),
            must_change_password=True,
        )
        body.username = None  # already applied
        _apply_profile(db, row, body, by_admin=True)
        # Recorded the way PATCH records them: "sees_all_cases: False -> True".
        ticks = "" if row.role == "admin" else _apply_ticks(row, body)
        db.add(row)
        audit(db, admin, "user_created", detail="; ".join(filter(None, [f"{row.email} ({row.role})", ticks])))
        _commit_or_taken(db)
        db.refresh(row)
        return {"user": user_dicts(db, [row])[0], "temporary_password": password}
    finally:
        db.close()


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, admin: CurrentUser = Depends(require_admin)) -> dict:
    """One commit for the whole request, disabling included: a refusal or a
    failure part-way changes nothing. Returns the account as GET /users
    shows it, plus "moved" (how many open cases went) when a disable
    applied an open_cases choice other than keep."""
    db = SessionLocal()
    try:
        row = db.get(User, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="User not found.")
        if row.id == admin.id and body.role is not None and body.role != row.role:
            # Even with another admin about: a mistaken click would lock
            # them out of the very screen that could undo it.
            raise not_allowed("You can't change your own role.")
        if body.active is False and row.id == admin.id:
            raise HTTPException(status_code=409, detail="You can't disable your own account.")
        losing_admin = row.role == "admin" and row.active and (body.role == "user" or body.active is False)
        if losing_admin and active_admin_count(db) <= 1:
            raise HTTPException(status_code=409, detail=LAST_ADMIN)
        if body.open_cases != "keep" and body.active is not False:
            raise HTTPException(status_code=422, detail="open_cases goes with disabling the account (active: false).")
        recipient = None
        if body.open_cases == "move" and row.active and body.active is False:
            if not body.open_cases_to:
                raise ApiError(422, "Choose who gets their open cases.", "owner_required")
            recipient = case_recipient(db, body.open_cases_to, row.id)

        before = _profile_changes(row, ("email", "role") + _PROFILE_FIELDS)
        if body.email is not None:
            row.email = _checked_email(db, body.email, exclude_id=row.id)
        if body.role is not None:
            row.role = body.role
        _apply_profile(db, row, body, by_admin=True)
        changes = "; ".join(filter(None, [_describe_changes(before, row), _apply_ticks(row, body)]))
        if changes:
            audit(db, admin, "user_updated", detail=f"{row.email}: {changes}")
        moved = None
        if body.active is not None and body.active != row.active:
            if body.active:
                row.active = True
                audit(db, admin, "user_enabled", detail=row.email)
            else:
                moved = _disable(db, row, admin, body.open_cases, recipient)

        # The checks above read before anything was written, so two admins
        # disabling or demoting each other at the same moment would both
        # pass the count, and the recipient may be disabled meanwhile.
        # Flushing takes SQLite's write lock (other writers wait for this
        # commit), so what is read from here on holds until the commit.
        _commit_or_taken(db, flush_only=True)
        if losing_admin and active_admin_count(db) < 1:
            db.rollback()
            raise HTTPException(status_code=409, detail=LAST_ADMIN)
        recipient_still_active(db, recipient)
        _commit_or_taken(db)
        db.refresh(row)
        account = user_dicts(db, [row])[0]
        if moved is not None:
            account["moved"] = moved
        return account
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
        return {"user": user_dicts(db, [row])[0], "temporary_password": password}
    finally:
        db.close()

