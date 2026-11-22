"""Cross-process sliding-window rate limiting for Gemini calls.

What this replaces
------------------
A single module-level "wait N seconds since the last call" gate. Three
things were wrong with it, all confirmed against the live API:

  1. It was applied ONCE per draft_report(), which then makes up to five
     model attempts per API key. After the first, the whole fallback chain
     fired back-to-back with no spacing -- the burst that produced
     "Quota exceeded ... limit: 20" during testing.
  2. A 429 was caught as a generic failure, so the chain immediately tried
     the next model, which was equally rate-limited, and burned all five
     attempts in a second. The API says exactly how long to wait
     ("Please retry in 4.58s") and that was ignored.
  3. The counter lived in module state, and the deployment runs TWO
     processes against the same key (carlink_api and carlink_telegram_bot),
     so the real request rate was double whatever was configured.

Design
------
A rolling 60-second window per API KEY, not per process. Windows live as
small JSON files on the shared storage volume both containers already
mount, coordinated with flock -- one true limit across every process,
without adding Redis or any other infrastructure.

When the window is full the caller waits until the oldest call ages out of
it, which is the "queue it into the next minute" behaviour: requests are
delayed, never dropped, and never fail for being early.

The key itself is never written to disk -- files are named by a hash of it,
so a window file leaks nothing if read.

Two stacks, one key (2026-11-22)
--------------------------------
With settings.gemini_shared_quota_dir set and mounted, production and
staging share ONE window (and a per-day count) in that folder instead, with
a lane each: ai/quota_guard.py. Without it -- the default -- everything
below runs exactly as before.
"""
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from app.ai import quota_guard
from app.ai.quota_guard import SlotDeadline, slot_deadline  # noqa: F401 (callers import them from here)
from app.config import settings

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60.0

# Same-process concurrency. flock handles process-to-process; this stops two
# threads in ONE process racing between their read and their write.
_local_lock = threading.Lock()


def _window_dir() -> Path:
    d = Path(settings.storage_dir) / ".ratelimit"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _window_file(api_key: str | None) -> Path:
    # Hashed so the key never touches disk. Truncated because collisions
    # only cost two keys sharing a window, and 16 hex chars makes that
    # vanishingly unlikely.
    tag = hashlib.sha256((api_key or "env-default").encode()).hexdigest()[:16]
    return _window_dir() / f"{tag}.json"


def _read_window(path: Path, now: float) -> list[float]:
    try:
        with open(path) as f:
            raw = f.read().strip()
        # A brand-new window file is empty because it was just created by the
        # "a+" open that takes the lock. That's the normal first call, not
        # corruption, and shouldn't log a warning.
        if not raw:
            return []
        stamps = json.loads(raw)
        # Anything outside the window is irrelevant; this is also what keeps
        # the file from growing without bound.
        return [t for t in stamps if isinstance(t, (int, float)) and now - t < WINDOW_SECONDS]
    except FileNotFoundError:
        return []
    except Exception:
        # A truncated or corrupt file must not stop the bot working. Losing
        # the history means at worst one over-permissive window.
        logger.warning("Rate-limit window %s unreadable; starting a fresh window", path.name)
        return []


def _write_window(path: Path, stamps: list[float]) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(stamps, f)
    os.replace(tmp, path)  # atomic, so a reader never sees a half-written file


def _locked_window(path: Path, now: float):
    """Opens a separate lock file with an exclusive flock. Returns (handle, stamps).

    Using a dedicated .lock file avoids locking the .json data file itself, which
    would prevent atomic os.replace() updates on Windows (PermissionError: WinError 5/32).
    """
    lock_path = path.with_suffix(".lock")
    handle = open(lock_path, "a+")
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except (ImportError, OSError):
        pass
    return handle, _read_window(path, now)


def _release(handle) -> None:
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass
    try:
        handle.close()
    except Exception:
        pass


def estimate_wait(api_key: str | None = None) -> float:
    """Seconds a call would have to wait right now, without taking a slot.

    Used to warn a reporter that their draft is queued, rather than leaving
    them watching a silent "Drafting your report..." for most of a minute.
    """
    guard = quota_guard.mode()
    if guard != "local":
        # The shared window (this lane's share of it); nothing to wait for
        # when the staging lane may not send at all.
        try:
            return quota_guard.estimate_wait(api_key) if guard == "shared" else 0.0
        except Exception as exc:
            logger.warning("Shared AI quota: couldn't estimate the wait (%s)", type(exc).__name__)
            return 0.0
    limit = max(1, settings.gemini_max_requests_per_minute)
    now = time.monotonic()
    path = _window_file(api_key)
    with _local_lock:
        handle, stamps = _locked_window(path, now)
        try:
            if len(stamps) < limit:
                return 0.0
            return max(0.0, WINDOW_SECONDS - (now - min(stamps)))
        finally:
            _release(handle)


def acquire(api_key: str | None = None, *, model: str | None = None, fingerprint: str | None = None,
            deadline: float | None = None) -> float:
    """Claims one slot, waiting for the window if it's full.

    Returns how long it waited, for logging. The sleep happens with the file
    lock RELEASED -- holding it across a sleep would block every other
    process for the whole wait and turn a rate limit into a queue of one.

    model, fingerprint: the request's model and the key's fingerprint, for
    the shared quota guard's day counts and staging rules (quota_guard
    .acquire), which take over when the shared folder is there -- or when
    this is the staging lane, which then refuses (quota_guard.LaneRefused,
    nothing sent). Unused otherwise: the window below, as always.

    deadline: a time.monotonic() by which the slot must be had, else
    SlotDeadline is raised before any wait that would end at or past it,
    with nothing taken or counted -- so a call whose time box has run out
    never sends. Unset: the slot_deadline context's (with slot_deadline(t):
    ...), else none -- wait as long as it takes, as always.
    """
    deadline = quota_guard.current_deadline(deadline)
    quota_guard.check_deadline(deadline, 0.0)
    if quota_guard.mode() != "local":
        return _guarded(api_key, model, fingerprint, deadline)
    return _acquire_local(api_key, deadline)


def acquire_retry(api_key: str | None = None, *, model: str | None = None, fingerprint: str | None = None) -> float:
    """The slot for asking the same model again (a busy model's one retry,
    or a per-minute 429's). Without the shared folder a retry takes no slot,
    as always: 0, nothing done. With it, every request sent is in the shared
    window and today's count, so the retry takes one like any request."""
    if quota_guard.mode() == "local":
        return 0.0
    return _guarded(api_key, model, fingerprint, quota_guard.current_deadline())


def _guarded(api_key: str | None, model: str | None, fingerprint: str | None, deadline: float | None = None) -> float:
    """quota_guard.acquire. Its refusals (LaneRefused) go to the caller.
    Any other failure of the shared folder (a full disk, a lost mount) must
    not stop production's AI: the production lane falls back to its own
    window and says so; the staging lane is refused."""
    try:
        return quota_guard.acquire(api_key, model=model, fingerprint=fingerprint, deadline=deadline)
    except (quota_guard.LaneRefused, SlotDeadline):
        raise
    except Exception as exc:
        if quota_guard.lane() == "staging":
            raise quota_guard.LaneRefused(model, f"the shared quota folder failed: {type(exc).__name__}") from None
        logger.warning("Shared AI quota failed (%s); this request uses this stack's own window", type(exc).__name__)
        return _acquire_local(api_key, deadline)


def _acquire_local(api_key: str | None, deadline: float | None = None) -> float:
    """The window in this stack's own STORAGE_DIR/.ratelimit (before the
    shared quota guard, the only one)."""
    limit = max(1, settings.gemini_max_requests_per_minute)
    spacing = max(0.0, settings.gemini_min_call_interval_seconds)
    path = _window_file(api_key)
    waited = 0.0

    while True:
        now = time.monotonic()
        with _local_lock:
            handle, stamps = _locked_window(path, now)
            try:
                if len(stamps) < limit:
                    # Also keep a minimum gap between consecutive calls, so a
                    # burst well under the per-minute cap still arrives spaced
                    # rather than all at once.
                    gap = 0.0
                    if stamps and spacing > 0:
                        gap = max(0.0, spacing - (now - max(stamps)))
                    if gap <= 0:
                        stamps.append(now)
                        _write_window(path, stamps)
                        return waited
                    sleep_for = gap
                else:
                    # Window full: wait for the oldest call to age out of it.
                    sleep_for = max(0.05, WINDOW_SECONDS - (now - min(stamps)))
            finally:
                _release(handle)
        # A caller's deadline (None: none, as always): give up before a wait
        # that would end past it, with no slot taken.
        quota_guard.check_deadline(deadline, sleep_for)

        logger.info("Gemini rate limit: waiting %.1fs for a slot", sleep_for)
        time.sleep(sleep_for)
        waited += sleep_for


_RETRY_AFTER_RE = re.compile(r"retry in ([0-9.]+)\s*s", re.IGNORECASE)


def retry_after_seconds(error: Exception) -> float | None:
    """Extracts the server's own retry delay from a 429, if it gave one.

    Gemini returns "Please retry in 4.588220893s." in the error body. Waiting
    exactly that long and retrying the SAME model is far better than falling
    through to the next one, which shares the same per-key quota and will
    just 429 as well.
    """
    text = str(error)
    if "429" not in text and "RESOURCE_EXHAUSTED" not in text and "too_many_requests" not in text:
        return None
    m = _RETRY_AFTER_RE.search(text)
    if m:
        try:
            # Cap it: a pathological value shouldn't hang a reporter's chat.
            return min(float(m.group(1)) + 0.25, 30.0)
        except ValueError:
            pass
    return 5.0  # a 429 with no stated delay still deserves a pause


def is_rate_limit_error(error: Exception) -> bool:
    text = str(error)
    return "429" in text or "RESOURCE_EXHAUSTED" in text or "too_many_requests" in text
