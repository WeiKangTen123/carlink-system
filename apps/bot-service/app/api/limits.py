"""What an account may spend, beyond which cases it can see (app/auth/
permissions.py): PDF renders, drafts and new cases, and disk. Signup is open
to strangers, and the api, the Telegram bot and another app share one small
VM, so each of these is capped. Which AI key a person may use -- their own,
or the company's for admins and ticked people -- is app/ai/keys.py's, whose
refusals are 409s with codes too (ai_key_missing, ai_key_invalid, ...).

Every refusal is an ApiError with a code the dashboard explains:

    429 render_busy      the server is busy making PDFs, or this person's
                         last one is still being made
    429 render_limit     too many PDFs lately (accounts without staff access)
    429 rate_limited     too many photos added in an hour (accounts without
                         staff access)
    429 ai_busy          this person already has an AI request running
    409 draft_limit      too many open drafts
    409 case_limit       too many new cases today (accounts without staff access)
    507 disk_full        the storage disk is nearly full
    403 not_allowed      Our Ref is for staff only

Our Ref is here too: with the firm's letterhead on every PDF, the firm's
own file reference is what would make a stranger's report pass as issued.
"""
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from app.auth.core import CurrentUser
from app.auth.deps import ApiError, not_allowed
from app.auth.permissions import is_admin, is_staff
from app.reports.models import Report
from app.storage.files import disk_space_low

# ----------------------------------------------------------------- disk


def require_disk_space() -> None:
    """Before writing an upload or a new case. FastAPI has already spooled
    the request body to a temp file by then; nginx's client_max_body_size
    (30M) is what bounds that."""
    if disk_space_low():
        raise ApiError(507, "The server is low on storage. Please tell your admin.", "disk_full")


def _unvetted(user) -> bool:
    """No admin role and none of the three ticks (a fresh self-signup): the
    accounts held to a number of new cases a day and of PDFs per window."""
    return not is_admin(user) and not any(
        getattr(user, tick, False) for tick in ("sees_all_cases", "ai_company_key_allowed", "may_sign_off")
    )


# -------------------------------------------------------------- renders

# For unvetted accounts, on top of one render at a time per person and the
# renderer's two at a time. Every save, sign-off, reopening and download of
# a changed case renders once, so a surveyor working steadily on a few
# cases makes a handful in ten minutes; 20 leaves room for a busy spell. A
# script looping save-then-download (each render 200-500 MB of Chromium) is
# held to one every 30 seconds.
RENDERS_PER_WINDOW = 20
RENDER_WINDOW_SECONDS = 10 * 60
RENDER_BUSY = "Too many PDFs at once. Try again in a minute."
RENDER_LIMIT = "You've made a lot of PDFs in the last few minutes. Wait a few minutes and try again."


class _RenderBudget:
    """Renders per account over a rolling window (and photos added without
    AI: photo_uploads). In memory, so per process: the api is one uvicorn
    process; the Telegram bot's renders have no account and only the
    renderer's slot limit applies to them."""

    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._lock = threading.Lock()
        self._times: dict[str, deque] = {}

    def take(self, user_id: str, count: int = 1) -> bool:
        """Counts count of them (one render) for this account; False, and
        none counted, if that would go over its share for now."""
        now = time.monotonic()
        with self._lock:
            times = self._times.setdefault(user_id, deque())
            while times and now - times[0] >= self.window:
                times.popleft()
            if len(times) + count > self.limit:
                return False
            times.extend([now] * count)
            return True

    def refund(self, user_id: str, count: int = 1) -> None:
        """Gives back the last count counted, for renders (photos) that
        didn't happen. They are that request's own: an account renders one
        at a time (render_turn), and a photo is a photo."""
        with self._lock:
            times = self._times.get(user_id)
            for _ in range(min(count, len(times or ()))):
                times.pop()

    def reset(self) -> None:
        with self._lock:
            self._times.clear()


class _OneAtATime:
    """Accounts with a render running now. Claimed without waiting."""

    def __init__(self):
        self._lock = threading.Lock()
        self._busy: set[str] = set()

    def claim(self, user_id: str) -> bool:
        with self._lock:
            if user_id in self._busy:
                return False
            self._busy.add(user_id)
            return True

    def release(self, user_id: str) -> None:
        with self._lock:
            self._busy.discard(user_id)


renders = _RenderBudget(RENDERS_PER_WINDOW, RENDER_WINDOW_SECONDS)
rendering_now = _OneAtATime()

# --------------------------------------------------------------- photos

# Photos added on the report form (POST /reports/{id}/upload-photos, and
# /reports/analyze-photos), for unvetted accounts. Each can be 25 MB of the shared
# disk in storage/tmp until the case is saved (or the day-old sweep), and a
# case rarely gets more than a few dozen: 60 an hour leaves room for a big
# one while stopping a script filling the disk.
PHOTOS_PER_HOUR = 60
PHOTO_LIMIT = "You've added a lot of photos in the last hour. Wait a while and try again."
photo_uploads = _RenderBudget(PHOTOS_PER_HOUR, 60 * 60)


def charge_photo_uploads(user: CurrentUser, count: int) -> bool:
    """Before saving count photos: 429 rate_limited, nothing counted, when
    an unvetted account would go past PHOTOS_PER_HOUR. Returns whether they
    were counted (only unvetted accounts' are), so a failed upload can give
    them back (photo_uploads.refund)."""
    if not _unvetted(user):
        return False
    if not photo_uploads.take(user.id, count):
        raise ApiError(429, PHOTO_LIMIT, "rate_limited")
    return True


# ------------------------------------------------------------------- AI

# One AI request at a time per account, whoever's case it is for: Analyze
# photos, Annex A "Ask AI", and an intake run for as long as it runs. Each
# is up to minutes of Gemini calls on the presser's key (or the company's),
# holding one of the api's threads; a second one meanwhile is refused at
# once rather than queued. In memory: the api is one process.
AI_BUSY = "You already have an AI request running. Wait for it to finish, then try again."
ai_running = _OneAtATime()


def claim_ai_turn(user: CurrentUser) -> None:
    """Claims this person's one AI request, or 429 ai_busy. Whoever claims
    releases it (ai_running.release(user.id)) when the request -- or the
    intake run it starts -- ends; ai_turn does both around a block."""
    if not ai_running.claim(user.id):
        raise ApiError(429, AI_BUSY, "ai_busy")


@contextmanager
def ai_turn(user: CurrentUser):
    claim_ai_turn(user)
    try:
        yield
    finally:
        ai_running.release(user.id)


class RenderRefused(ApiError):
    """render_turn's refusal, raised before any render starts."""


def render_busy() -> ApiError:
    return ApiError(429, RENDER_BUSY, "render_busy")


@contextmanager
def render_turn(user: CurrentUser):
    """Around a render an HTTP request sets off. One at a time per account:
    a second one meanwhile is refused at once rather than queued, so nobody
    can park the API's threads waiting for Chromium (429 render_busy). An
    unvetted account also gets RENDERS_PER_WINDOW (429 render_limit).

    A refusal is RenderRefused, before the body runs. A request whose answer
    is the PDF (render-pdf, a download that has to render first) lets it
    through as the 429; a save catches it and leaves the PDF to the next
    download. A render that doesn't happen -- the body raises: RenderBusy,
    Chromium failing -- gives its count back."""
    if not rendering_now.claim(user.id):
        raise RenderRefused(429, RENDER_BUSY, "render_busy")
    try:
        counted = _unvetted(user)
        if counted and not renders.take(user.id):
            raise RenderRefused(429, RENDER_LIMIT, "render_limit")
        try:
            yield
        except BaseException:
            if counted:
                renders.refund(user.id)
            raise
    finally:
        rendering_now.release(user.id)


# ------------------------------------------------------- drafts and cases

MAX_OPEN_DRAFTS_PER_PERSON = 5
# For unvetted accounts (_unvetted). Counts filed cases -- manual ones and
# finished intakes -- by when they were started; open drafts are capped by
# MAX_OPEN_DRAFTS_PER_PERSON instead.
MAX_NEW_CASES_PER_DAY = 10
_DAY = timedelta(hours=24)


def counts_toward_shared_drafts(user) -> bool:
    """Admins and ticked staff ("see all cases" or "May sign off") neither
    count toward nor are stopped by the shared cap on open drafts (api/
    intake.MAX_OPEN_DRAFTS): six strangers could otherwise fill it."""
    return not (is_admin(user) or getattr(user, "sees_all_cases", False) or getattr(user, "may_sign_off", False))


def check_open_drafts(db, user: CurrentUser) -> None:
    """Before starting a draft (POST /intake)."""
    open_drafts = (
        db.query(func.count(Report.id)).filter(Report.owner_id == user.id, Report.status == "intake").scalar()
    )
    if open_drafts >= MAX_OPEN_DRAFTS_PER_PERSON:
        raise ApiError(
            409,
            f"You already have {MAX_OPEN_DRAFTS_PER_PERSON} unfinished drafts. Finish or delete one first.",
            "draft_limit",
        )


def check_new_cases_today(db, user: CurrentUser) -> None:
    """Before starting or filing a case (POST /intake, POST /reports,
    finalize), for accounts without staff ticks."""
    if not _unvetted(user):
        return
    # Stored as naive UTC (SQLite drops the offset), and so is the cutoff.
    since = (datetime.now(timezone.utc) - _DAY).replace(tzinfo=None)
    filed = (
        db.query(func.count(Report.id))
        .filter(Report.owner_id == user.id, Report.status != "intake", Report.created_at >= since)
        .scalar()
    )
    if filed >= MAX_NEW_CASES_PER_DAY:
        raise ApiError(
            409,
            f"You've filed {MAX_NEW_CASES_PER_DAY} cases in the last 24 hours, the most for an account "
            "without staff access. Try again later, or ask an admin.",
            "case_limit",
        )


# --------------------------------------------------------------- Our Ref


def _ref(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def checked_our_ref(user: CurrentUser, sent, stored):
    """The Our Ref to save. Our Ref (report_id in the case data, printed on
    every page of the PDF) is set and changed by staff only (permissions.
    is_staff). The forms send the whole case, so anyone may send back what
    is stored -- or nothing, on a case without one -- and keeps it as it
    was; a change from anyone else is refused."""
    if is_staff(user):
        return sent
    if _ref(sent) != _ref(stored):
        raise not_allowed("Only staff can set Our Ref.")
    return stored
