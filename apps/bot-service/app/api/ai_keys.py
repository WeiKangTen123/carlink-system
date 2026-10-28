"""Someone's own AI key (Settings > My AI key; app/ai/keys.py).

    GET    /auth/me/ai-key           last 4, status, and what the AI will use for me
    PUT    /auth/me/ai-key           add or replace: {api_key, billing_confirmed: true}
    POST   /auth/me/ai-key/test      ask Google about the saved key again
    DELETE /auth/me/ai-key           remove it
    DELETE /users/{id}/ai-key        admin: remove someone's key

No response ever holds a key, to anyone, admins included: GET, PUT and
test all answer with keys.ai_key_status. Like every route, these need a
signed-in user, and a temporary password refuses them (auth/middleware.py).

Saving is limited per person and in total: a refusal for a key someone
else has saved says only "This key can't be used here.", and the limits
keep anyone from using that answer to check a pile of stolen keys.
"""
import threading
import time
from collections import deque
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.ai import keys, secretbox
from app.auth.core import CurrentUser, audit
from app.auth.deps import ApiError, current_user, require_admin
from app.reports.db import SessionLocal
from app.reports.models import User

router = APIRouter(tags=["ai-keys"])

IN_USE = "This key can't be used here."
INVALID = (
    "Google didn't accept this key. Check you copied all of it, and that the Gemini API "
    "is turned on for its Google Cloud project."
)
BILLING = "Tick the box to confirm billing is turned on for this key's Google Cloud project."
STORAGE_OFF = "Saving AI keys is off on this server right now. Please tell your admin."
NO_KEY = "You haven't added an AI key yet."
CHECK_FAILED = "Couldn't reach Google to check the key. Try again in a minute."
TOO_MANY = "Too many tries with AI keys. Try again in an hour."
TOO_MANY_TODAY = "A lot of AI keys have been added today. Try again tomorrow."

# Per person: someone pastes a key, maybe fixes a typo or two. In total: a
# day's worth for the whole firm, so even many accounts together can't test
# keys at any pace. In memory: the api is one process, and a restart
# forgetting them is harmless.
SAVES_PER_HOUR = 10
SAVES_PER_DAY_ALL = 100
TESTS_PER_HOUR = 10
_HOUR = 60 * 60
_DAY = 24 * _HOUR


class _Budget:
    """Tries per key ("all" for the total) over a rolling window."""

    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._times: dict[str, deque] = {}

    def full(self, key: str, now: float) -> bool:
        times = self._times.setdefault(key, deque())
        while times and now - times[0] > self.window:
            times.popleft()
        return len(times) >= self.limit

    def add(self, key: str, now: float) -> None:
        self._times.setdefault(key, deque()).append(now)

    def reset(self) -> None:
        self._times.clear()


_lock = threading.Lock()
saves = _Budget(SAVES_PER_HOUR, _HOUR)
saves_all = _Budget(SAVES_PER_DAY_ALL, _DAY)
tests = _Budget(TESTS_PER_HOUR, _HOUR)


def _take_save(user_id: str) -> None:
    """Counts one save, refused or not; 429 rate_limited over either limit."""
    now = time.monotonic()
    with _lock:
        if len(saves._times) > 5000:
            saves.reset()
        if saves.full(user_id, now):
            raise ApiError(429, TOO_MANY, "rate_limited")
        if saves_all.full("all", now):
            raise ApiError(429, TOO_MANY_TODAY, "rate_limited")
        saves.add(user_id, now)
        saves_all.add("all", now)


def _take_test(user_id: str) -> None:
    now = time.monotonic()
    with _lock:
        if len(tests._times) > 5000:
            tests.reset()
        if tests.full(user_id, now):
            raise ApiError(429, TOO_MANY, "rate_limited")
        tests.add(user_id, now)


def reset_limits() -> None:
    """Tests."""
    with _lock:
        saves.reset()
        saves_all.reset()
        tests.reset()


def _status(user: CurrentUser) -> dict:
    db = SessionLocal()
    try:
        return keys.ai_key_status(db, user)
    finally:
        db.close()


@router.get("/auth/me/ai-key")
def my_ai_key(user: CurrentUser = Depends(current_user)) -> dict:
    return _status(user)


class AiKeyIn(BaseModel):
    api_key: str = Field(max_length=200)
    billing_confirmed: Optional[bool] = None


@router.put("/auth/me/ai-key")
def save_my_ai_key(body: AiKeyIn, user: CurrentUser = Depends(current_user)) -> dict:
    """Checks the key with Google (models.list, free) and saves it sealed,
    replacing any key this person had. A key Google refuses isn't saved
    (400 ai_key_invalid); one it couldn't be asked about is saved as
    "untested"."""
    _take_save(user.id)
    key = body.api_key.strip()
    if keys.format_problem(key):
        raise ApiError(400, keys.FORMAT_PROBLEM, "ai_key_format")
    if body.billing_confirmed is not True:
        raise ApiError(400, BILLING, "billing_not_confirmed")
    try:
        fingerprint = secretbox.fingerprint(key)
    except secretbox.Unreadable:
        raise ApiError(503, STORAGE_OFF, "ai_key_storage_off")

    db = SessionLocal()
    try:
        clash = keys.key_in_use(db, key, fingerprint, user.id)
        if clash:
            # Kept for the admins: many of these from one account is
            # someone checking keys they shouldn't have.
            audit(db, user, "ai_key_refused", detail=f"key ...{keys.last4(key)} is {clash}")
            db.commit()
            raise ApiError(409, IN_USE, "ai_key_in_use")
    finally:
        db.close()

    # No database session is held while Google answers.
    status, error = keys.probe_key(key)
    if status == "invalid":
        raise ApiError(400, INVALID, "ai_key_invalid")

    db = SessionLocal()
    try:
        for attempt in range(2):
            try:
                keys.save_key(db, user.id, key, fingerprint, status, error, billing_confirmed=True)
                audit(db, user, "ai_key_saved", detail=f"key ...{keys.last4(key)} ({status})")
                db.commit()
                break
            except secretbox.Unreadable:
                db.rollback()
                raise ApiError(503, STORAGE_OFF, "ai_key_storage_off")
            except IntegrityError:
                # Someone else saved this key a moment ago (fingerprint), or
                # this person's other tab saved one (user_id): look again.
                db.rollback()
                if attempt or keys.key_in_use(db, key, fingerprint, user.id):
                    raise ApiError(409, IN_USE, "ai_key_in_use")
        return keys.ai_key_status(db, user)
    finally:
        db.close()


@router.post("/auth/me/ai-key/test")
def test_my_ai_key(user: CurrentUser = Depends(current_user)) -> dict:
    """Asks Google about the saved key again and stores the answer. If
    Google can't be reached nothing changes (503 ai_key_check_failed)."""
    _take_test(user.id)
    db = SessionLocal()
    try:
        row = keys.get_row(db, user.id)
        if row is None:
            raise ApiError(404, NO_KEY, "no_ai_key")
        try:
            key = keys.unseal_key(row)
        except secretbox.Unreadable:
            if secretbox.state() == secretbox.OK:
                # The secret is fine; this key was sealed with an older one.
                keys.record_check(row, "unreadable", secretbox.LOST_SECRET_ERROR)
                db.commit()
            return keys.ai_key_status(db, user)
    finally:
        db.close()

    status, error = keys.probe_key(key)
    if status == "untested":
        raise ApiError(503, CHECK_FAILED, "ai_key_check_failed")

    db = SessionLocal()
    try:
        row = keys.get_row(db, user.id)
        if row is not None:  # not removed meanwhile
            keys.record_check(row, status, error)
            db.commit()
        return keys.ai_key_status(db, user)
    finally:
        db.close()


@router.delete("/auth/me/ai-key")
def delete_my_ai_key(user: CurrentUser = Depends(current_user)) -> dict:
    db = SessionLocal()
    try:
        keys.delete_user_key(db, user.id, user)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.delete("/users/{user_id}/ai-key")
def delete_users_ai_key(user_id: str, admin: CurrentUser = Depends(require_admin)) -> dict:
    """For a leaver, or a key being misused. The person can add one again
    (disable the account to stop that)."""
    db = SessionLocal()
    try:
        if db.get(User, user_id) is None:
            raise HTTPException(status_code=404, detail="User not found.")
        keys.delete_user_key(db, user_id, admin, action="ai_key_removed_by_admin")
        db.commit()
        return {"ok": True}
    finally:
        db.close()
