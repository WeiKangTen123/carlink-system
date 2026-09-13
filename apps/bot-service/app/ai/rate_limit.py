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
"""
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

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
    """Opens the window file with an exclusive flock. Returns (handle, stamps).

    flock is advisory and Linux-only, which is fine: both processes are
    Linux containers. If it isn't available the limiter still works
    per-process, just without cross-process coordination.
    """
    handle = open(path, "a+")
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
    handle.close()


def estimate_wait(api_key: str | None = None) -> float:
    """Seconds a call would have to wait right now, without taking a slot.

    Used to warn a reporter that their draft is queued, rather than leaving
    them watching a silent "Drafting your report..." for most of a minute.
    """
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


def acquire(api_key: str | None = None) -> float:
    """Claims one slot, waiting for the window if it's full.

    Returns how long it waited, for logging. The sleep happens with the file
    lock RELEASED -- holding it across a sleep would block every other
    process for the whole wait and turn a rate limit into a queue of one.
    """
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
