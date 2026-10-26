"""What an account may spend, beyond which cases it can see (app/auth/
permissions.py): the company AI key, PDF renders, drafts and new cases, and
disk. Signup is open to strangers, and the api, the Telegram bot and
another app share one small VM, so each of these is capped.

Every refusal is an ApiError with a code the dashboard explains:

    409 ai_key_missing   AI is only for admins and the "company AI key" tick
    429 render_busy      too many PDFs from this person lately
    409 draft_limit      too many open drafts, or too many new cases today
    507 disk_full        the storage disk is nearly full
    403 not_allowed      Our Ref is for staff only

Our Ref is here too: with the firm's letterhead on every PDF, the firm's
own file reference is what would make a stranger's report pass as issued.
"""
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from app.auth.core import CurrentUser
from app.auth.deps import ApiError, not_allowed
from app.auth.permissions import is_admin, is_staff, may_use_company_key
from app.reports.models import Report
from app.storage.files import disk_space_low

# ------------------------------------------------------------------- AI

AI_OFF = "AI is off for your account. Ask an admin to allow the company AI key."


def require_ai(user: CurrentUser) -> None:
    """Before anything that ends in a Gemini call on the company key. Case
    routes ask after their view (404) and edit (403) gates."""
    if not may_use_company_key(user):
        raise ApiError(409, AI_OFF, "ai_key_missing")


# ----------------------------------------------------------------- disk


def require_disk_space() -> None:
    """Before writing an upload or a new case. FastAPI has already spooled
    the request body to a temp file by then; nginx's client_max_body_size
    (30M) is what bounds that."""
    if disk_space_low():
        raise ApiError(507, "The server is low on storage. Please tell your admin.", "disk_full")


# -------------------------------------------------------------- renders

# Per person, on top of the renderer's two-at-a-time limit. Every save,
# sign-off, reopening and download of a changed case renders once, so a
# surveyor working steadily on a few cases makes a handful in ten minutes;
# 20 leaves room for a busy spell. A script looping save-then-download
# (each render 200-500 MB of Chromium) is held to one every 30 seconds.
RENDERS_PER_WINDOW = 20
RENDER_WINDOW_SECONDS = 10 * 60
RENDER_BUSY = "Too many PDFs at once. Try again in a minute."


class _RenderBudget:
    """Renders per account over a rolling window. In memory, so per process:
    the api is one uvicorn process; the Telegram bot's renders have no
    account and only the renderer's slot limit applies to them."""

    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._lock = threading.Lock()
        self._times: dict[str, deque] = {}

    def take(self, user_id: str) -> bool:
        """Counts one render for this account; False (and not counted) if
        it has had its share for now."""
        now = time.monotonic()
        with self._lock:
            times = self._times.setdefault(user_id, deque())
            while times and now - times[0] >= self.window:
                times.popleft()
            if len(times) >= self.limit:
                return False
            times.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._times.clear()


renders = _RenderBudget(RENDERS_PER_WINDOW, RENDER_WINDOW_SECONDS)


def render_busy() -> ApiError:
    return ApiError(429, RENDER_BUSY, "render_busy")


def require_render(user: CurrentUser) -> None:
    """For a request whose answer is the PDF (render-pdf, a download that
    has to render first): 429 render_busy over this person's share. A save
    that renders instead skips the render (renders.take) and leaves the PDF
    to the next download."""
    if not renders.take(user.id):
        raise render_busy()


# ------------------------------------------------------- drafts and cases

MAX_OPEN_DRAFTS_PER_PERSON = 5
# For accounts with no tick and no admin role (a fresh self-signup). Counts
# filed cases -- manual ones and finished intakes -- by when they were
# started; open drafts are capped by MAX_OPEN_DRAFTS_PER_PERSON instead.
MAX_NEW_CASES_PER_DAY = 10
_DAY = timedelta(hours=24)


def counts_toward_shared_drafts(user) -> bool:
    """Admins and ticked staff ("see all cases" or "May sign off") neither
    count toward nor are stopped by the shared cap on open drafts (api/
    intake.MAX_OPEN_DRAFTS): six strangers could otherwise fill it."""
    return not (is_admin(user) or getattr(user, "sees_all_cases", False) or getattr(user, "may_sign_off", False))


def _daily_capped(user) -> bool:
    return not is_admin(user) and not any(
        getattr(user, tick, False) for tick in ("sees_all_cases", "ai_company_key_allowed", "may_sign_off")
    )


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
    if not _daily_capped(user):
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
            "draft_limit",
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
