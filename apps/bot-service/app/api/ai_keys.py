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
import logging
import threading
import time
from collections import deque
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError, OperationalError

from app.ai import keys, secretbox
from app.api import limits
from app.auth.core import CurrentUser, audit
from app.auth.deps import ApiError, current_user, require_admin
from app.auth.permissions import is_admin, is_staff, may_use_company_key
from app.reports.db import SessionLocal
from app.reports.models import AuthSession, User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai-keys"])

INVALID = (
    "Google didn't accept this key. Check you copied all of it, and that the Gemini API "
    "is turned on for its Google Cloud project."
)
BILLING = "Tick the box to confirm billing is turned on for this key's Google Cloud project."
STORAGE_OFF = "Saving AI keys is off on this server right now. Please tell your admin."
NO_KEY = "You haven't added an AI key yet."
CHECK_FAILED = "Couldn't reach Google to check the key. Try again in a minute."
TOO_MANY = "Too many tries with AI keys. Try again in an hour."
TOO_MANY_DAY = "Too many tries with AI keys today. Try again tomorrow."
TOO_MANY_TODAY = "A lot of AI keys have been added today. Try again tomorrow."
SIGNED_OUT = "Not signed in."
ACCOUNT_OFF = "This account has been disabled."

# Per person: someone pastes a key, maybe fixes a typo or two. An account
# with no admin role and no ticks (a fresh self-signup, limits._unvetted)
# gets fewer. In total: a day's worth for the whole firm, so even many
# accounts together can't test keys at any pace -- charged only once the
# format and billing checks pass, and not to admins or ticked staff, so one
# stranger can't use it up for everyone. In memory: the api is one process,
# and a restart forgetting them is harmless.
SAVES_PER_HOUR = 10
UNVETTED_SAVES_PER_HOUR = 3
UNVETTED_SAVES_PER_DAY = 10
SAVES_PER_DAY_ALL = 100
TESTS_PER_HOUR = 10
_HOUR = 60 * 60
_DAY = 24 * _HOUR
# A 429 goes into the history at most this often per person, so a flood of
# them can't flood the history too.
_NOTE_EVERY = _HOUR

# The limits' clock (tests move it on).
_clock = time.monotonic


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

    def used(self, key: str) -> int:
        return len(self._times.get(key, ()))

    def reset(self) -> None:
        self._times.clear()


_lock = threading.Lock()
saves = _Budget(SAVES_PER_HOUR, _HOUR)
unvetted_hour = _Budget(UNVETTED_SAVES_PER_HOUR, _HOUR)
unvetted_day = _Budget(UNVETTED_SAVES_PER_DAY, _DAY)
saves_all = _Budget(SAVES_PER_DAY_ALL, _DAY)
tests = _Budget(TESTS_PER_HOUR, _HOUR)
_BUDGETS = (saves, unvetted_hour, unvetted_day, saves_all, tests)
_noted: dict[str, float] = {}  # user id -> when a 429 last went into the history


def _note_due(user_id: str, now: float) -> bool:
    """Whether this person's 429 goes into the history: at most once an
    hour each. Call with _lock held."""
    last = _noted.get(user_id)
    if last is not None and now - last < _NOTE_EVERY:
        return False
    if len(_noted) > 5000:
        _noted.clear()
    _noted[user_id] = now
    return True


def _refused(user: CurrentUser, message: str, which: str, note: bool) -> ApiError:
    """The 429, after the history entry if one is due (written outside
    _lock: a busy database mustn't hold up everyone's limits)."""
    if note:
        db = SessionLocal()
        try:
            audit(db, user, "ai_key_rate_limited", detail=f"over {which} (more within the hour aren't recorded)")
            db.commit()
        except OperationalError:
            db.rollback()  # a busy database mustn't turn a 429 into a 500
        finally:
            db.close()
    return ApiError(429, message, "rate_limited")


def _charge_person(user: CurrentUser) -> None:
    """Counts one save, refused or not; 429 rate_limited over this person's
    limit."""
    now = _clock()
    refusal = None
    with _lock:
        if len(saves._times) + len(unvetted_day._times) > 5000:
            for budget in (saves, unvetted_hour, unvetted_day):
                budget.reset()
        if limits._unvetted(user):
            if unvetted_hour.full(user.id, now):
                refusal = (TOO_MANY, f"{UNVETTED_SAVES_PER_HOUR} key saves an hour")
            elif unvetted_day.full(user.id, now):
                refusal = (TOO_MANY_DAY, f"{UNVETTED_SAVES_PER_DAY} key saves a day")
            else:
                unvetted_hour.add(user.id, now)
                unvetted_day.add(user.id, now)
        elif saves.full(user.id, now):
            refusal = (TOO_MANY, f"{SAVES_PER_HOUR} key saves an hour")
        else:
            saves.add(user.id, now)
        note = refusal is not None and _note_due(user.id, now)
    if refusal:
        raise _refused(user, *refusal, note)


def _exempt_from_total(user: CurrentUser) -> bool:
    return is_admin(user) or may_use_company_key(user) or is_staff(user)


def _charge_total(user: CurrentUser) -> None:
    """Counts one save towards the day's total, once it has passed the
    format and billing checks; 429 rate_limited when the total is full."""
    if _exempt_from_total(user):
        return
    now = _clock()
    with _lock:
        full = saves_all.full("all", now)
        if not full:
            saves_all.add("all", now)
        note = full and _note_due(user.id, now)
    if full:
        raise _refused(user, TOO_MANY_TODAY, f"the day's {SAVES_PER_DAY_ALL} key saves for everyone", note)


def _charge_test(user: CurrentUser) -> None:
    now = _clock()
    with _lock:
        if len(tests._times) > 5000:
            tests.reset()
        full = tests.full(user.id, now)
        if not full:
            tests.add(user.id, now)
        note = full and _note_due(user.id, now)
    if full:
        raise _refused(user, TOO_MANY, f"{TESTS_PER_HOUR} key tests an hour", note)


def reset_limits() -> None:
    """Tests."""
    with _lock:
        for budget in _BUDGETS:
            budget.reset()
        _noted.clear()


def _still_signed_in(db, user: CurrentUser, *, admin: bool = False) -> None:
    """Once the request holds SQLite's write lock (it has written): the
    session it came in on still exists and the account is still on (and
    still an admin, for an admin's action). The middleware checked both
    when the request arrived, but a save waits on Google for a while -- an
    admin disabling the account meanwhile mustn't be undone by it. Refused
    with everything rolled back."""
    session = db.query(AuthSession.token_hash).filter(AuthSession.token_hash == user.session_hash).first()
    if session is None:
        db.rollback()
        raise ApiError(401, SIGNED_OUT, "signed_out")
    # Columns, not db.get: that would answer from a row loaded earlier.
    row = db.query(User.active, User.role).filter(User.id == user.id).first()
    if row is None or not row.active:
        db.rollback()
        raise ApiError(403, ACCOUNT_OFF, "account_disabled")
    if admin and row.role != "admin":
        db.rollback()
        raise HTTPException(status_code=403, detail="Only an admin can do this.")


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
    _charge_person(user)
    key = body.api_key.strip()
    if keys.format_problem(key):
        raise ApiError(400, keys.FORMAT_PROBLEM, "ai_key_format")
    if body.billing_confirmed is not True:
        raise ApiError(400, BILLING, "billing_not_confirmed")
    _charge_total(user)
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
            raise ApiError(409, keys.IN_USE, "ai_key_in_use")
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
                # Takes the write lock: from here nobody else commits until
                # this does, so what is read now holds.
                db.flush()
                _still_signed_in(db, user)
                if keys.company_key_matches(db, key):
                    # An admin added it as a company key meanwhile.
                    db.rollback()
                    raise ApiError(409, keys.IN_USE, "ai_key_in_use")
                if not secretbox.sealed_with_current(db):
                    # A reset or a restore since the secret was read: the
                    # key would be sealed with a secret that is gone.
                    db.rollback()
                    raise ApiError(503, STORAGE_OFF, "ai_key_storage_off")
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
                    raise ApiError(409, keys.IN_USE, "ai_key_in_use")
        return keys.ai_key_status(db, user)
    finally:
        db.close()


@router.post("/auth/me/ai-key/test")
def test_my_ai_key(user: CurrentUser = Depends(current_user)) -> dict:
    """Asks Google about the saved key again and stores the answer. If
    Google can't be reached nothing changes (503 ai_key_check_failed)."""
    _charge_test(user)
    db = SessionLocal()
    try:
        row = keys.get_row(db, user.id)
        if row is None:
            raise ApiError(404, NO_KEY, "no_ai_key")
        if secretbox.state() != secretbox.OK:
            return keys.ai_key_status(db, user)
        try:
            key = keys.unseal_key(row)
        except secretbox.Unreadable:
            # The secret opens the canary but not this row: shown, not stored.
            logger.warning("An own AI key (...%s) doesn't open with the current secret", row.last4)
            return keys.ai_key_status(db, user, unreadable=True)
        tested = row.fingerprint
    finally:
        db.close()

    status, error = keys.probe_key(key)
    if status == "untested":
        raise ApiError(503, CHECK_FAILED, "ai_key_check_failed")

    db = SessionLocal()
    try:
        # Only onto the key that was tested: one saved meanwhile keeps its own.
        keys.record_check(db, user.id, tested, status, error)
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
        if keys.delete_user_key(db, user_id, admin, action="ai_key_removed_by_admin"):
            # The DELETE holds the write lock: the admin is still one now.
            _still_signed_in(db, admin, admin=True)
        db.commit()
        return {"ok": True}
    finally:
        db.close()
