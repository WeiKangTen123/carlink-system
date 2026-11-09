"""Each key's free daily allowance per Gemini model, remembered until Google
resets it.

Why this exists
---------------
Everyone is on free-tier keys: the company key and most people's own. A
free allowance is per Google Cloud project, per model, per day -- only 20
requests a day on gemini-3-flash-preview -- and it refills at midnight US
Pacific time. Once a key's day is used up on a model, every request to it
is a 429 that still says "Please retry in 33s", which won't help: the day
is over. call_structured waited and retried that model once on every call,
so each read wasted two tries and ~5 s before reaching a backup model, all
day long (measured on the server).

So a 429 for the day's allowance (or "limit: 0": none at all on that
model) is remembered here, per key and model, until the next reset, and
call_structured skips that model on that key with nothing sent and no
rate-limit slot taken (outcome "day_spent" in the run log).

Also kept: the allowance Google stated in that 429 ("limit: 20"), so GET
/auth/me/ai-allowance can say "7 of 20". That outlives the reset: it is
what the model allows each day, not today's state.

Storage
-------
One small JSON file on the storage volume both containers mount (the api
and the Telegram bot), coordinated with flock on a separate .lock file, as
ai/rate_limit.py does -- so a day used up in one is skipped by the other.
Never a key, nor even a plain hash of one: entries are named by a hash of
the key's fingerprint (AiKey.fingerprint).

Nothing here may break an AI call: a file that can't be read or written is
logged and treated as "nothing remembered", which is the behaviour from
before this module.
"""
import hashlib
import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from app.config import settings

logger = logging.getLogger(__name__)

# Google refills free-tier daily allowances at midnight Pacific time.
RESET_ZONE = ZoneInfo("America/Los_Angeles")
# The clock (tests move it on).
_clock = lambda: datetime.now(timezone.utc)  # noqa: E731

# Same-process concurrency; flock covers process to process (as rate_limit).
_local_lock = threading.Lock()

# The allowance a daily-quota 429 states. Google's own JSON first (the
# per-day violation's quotaValue: a 429 can list the per-minute one too),
# else the message's "limit: N".
_PER_DAY_VALUE = re.compile(r"PerDay[^\]]{0,400}?quotaValue\W{1,6}(\d+)", re.IGNORECASE)
_LIMIT = re.compile(r"\blimit:\s*(\d+)", re.IGNORECASE)
# limits kept at most; keys x models is a few dozen in practice.
MAX_LIMITS = 2000


class DaySpent(Exception):
    """A try that was never sent: this key's free allowance for the model is
    used up until the reset. call_structured's last error when every model
    left was skipped -- so the intake's message (pipeline._friendly_error)
    finds "quota" in it, and the run log keeps these words."""

    def __init__(self, model: str):
        super().__init__(
            f"{model}: this key's free daily quota is used up until midnight Pacific time (no request sent)"
        )


# ------------------------------------------------------------ the day


def next_reset(now: datetime | None = None) -> datetime:
    """The next midnight America/Los_Angeles after `now`, in UTC."""
    local = (now or _clock()).astimezone(RESET_ZONE)
    # On the date, not +24 h: a DST change makes one day 23 or 25 hours.
    tomorrow = local.date() + timedelta(days=1)
    return datetime.combine(tomorrow, time.min, tzinfo=RESET_ZONE).astimezone(timezone.utc)


def day_started(now: datetime | None = None) -> datetime:
    """When Google's current day began (the last midnight Pacific), in UTC:
    what counts as "today" for the allowance."""
    local = (now or _clock()).astimezone(RESET_ZONE)
    return datetime.combine(local.date(), time.min, tzinfo=RESET_ZONE).astimezone(timezone.utc)


def iso(value: datetime) -> str:
    """'2026-11-10T08:00:00Z'."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def limit_from(error) -> int | None:
    """The daily allowance a 429 states ("limit: 20"; 0 for none at all),
    or None when it doesn't say."""
    text = str(error or "")
    found = _PER_DAY_VALUE.search(text)
    if found:
        return int(found.group(1))
    limits = [int(n) for n in _LIMIT.findall(text)]
    # Several when the per-minute window is listed too; the day's is never
    # the smaller of the two.
    return max(limits) if limits else None


# ------------------------------------------------------------ the store


def _tag(fingerprint: str) -> str:
    # A hash of the fingerprint, so not even a plain hash of a company key
    # (whose fingerprint is one) is on disk.
    return hashlib.sha256(f"allowance|{fingerprint}".encode("utf-8")).hexdigest()[:24]


def _entry(fingerprint: str, model: str) -> str:
    return f"{_tag(fingerprint)}|{model}"


def _path() -> Path:
    d = Path(settings.storage_dir) / ".allowance"
    d.mkdir(parents=True, exist_ok=True)
    return d / "days.json"


@contextmanager
def _locked() -> Iterator[Path]:
    """The store's path, held exclusively: this process's threads by
    _local_lock, other processes by flock on a separate .lock file (locking
    the data file itself would stop os.replace on Windows)."""
    path = _path()
    with _local_lock:
        handle = open(path.with_suffix(".lock"), "a+")
        try:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass  # Windows (tests): one process, _local_lock is enough
            yield path
        finally:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            handle.close()


def _read(path: Path) -> dict:
    """{"spent": {entry: reset as epoch seconds}, "limits": {entry: N}}."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
        data = json.loads(raw) if raw else {}
    except FileNotFoundError:
        data = {}
    except Exception:
        # A truncated or corrupt file must not stop the AI: at worst one
        # wasted try per spent model, as before this store.
        logger.warning("Day allowance store unreadable; starting afresh")
        data = {}
    spent = data.get("spent") if isinstance(data, dict) else None
    limits = data.get("limits") if isinstance(data, dict) else None
    return {
        "spent": {k: v for k, v in (spent or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)},
        "limits": {k: v for k, v in (limits or {}).items() if isinstance(v, int) and not isinstance(v, bool) and v >= 0},
    }


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a reader never sees half a file


def _load() -> dict:
    with _locked() as path:
        return _read(path)


def mark_spent(fingerprint: str, model: str, limit: int | None = None) -> datetime:
    """Remembers that this key's allowance for `model` is used up until the
    next reset, and the allowance Google stated (limit), if it did. Returns
    the reset."""
    until = next_reset()
    try:
        with _locked() as path:
            data = _read(path)
            now = _clock().timestamp()
            # Expired entries go on every write: the file stays small.
            spent = {k: v for k, v in data["spent"].items() if v > now}
            spent[_entry(fingerprint, model)] = until.timestamp()
            limits = data["limits"]
            if isinstance(limit, int) and limit >= 0:
                limits[_entry(fingerprint, model)] = limit
                if len(limits) > MAX_LIMITS:
                    limits = dict(list(limits.items())[-MAX_LIMITS:])
            _write(path, {"spent": spent, "limits": limits})
    except Exception as exc:
        logger.warning("Day allowance: couldn't remember %s as used up (%s)", model, type(exc).__name__)
    return until


def spent_until(fingerprint: str, model: str) -> datetime | None:
    """When this key's allowance for `model` comes back, if it is used up
    now; else None."""
    try:
        value = _load()["spent"].get(_entry(fingerprint, model))
    except Exception as exc:
        logger.warning("Day allowance: couldn't read the store (%s)", type(exc).__name__)
        return None
    if value is None or value <= _clock().timestamp():
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def limit(fingerprint: str, model: str) -> int | None:
    """The daily allowance Google last stated for this key and model, or
    None when it never has."""
    try:
        return _load()["limits"].get(_entry(fingerprint, model))
    except Exception:
        return None


def key_state(fingerprint: str, models) -> dict[str, dict]:
    """{model: {"spent_until": datetime | None, "limit": int | None}} for
    one key, in one read (GET /auth/me/ai-allowance)."""
    try:
        data = _load()
    except Exception as exc:
        logger.warning("Day allowance: couldn't read the store (%s)", type(exc).__name__)
        data = {"spent": {}, "limits": {}}
    now = _clock().timestamp()
    out = {}
    for model in models:
        entry = _entry(fingerprint, model)
        value = data["spent"].get(entry)
        out[model] = {
            "spent_until": datetime.fromtimestamp(value, tz=timezone.utc) if value is not None and value > now else None,
            "limit": data["limits"].get(entry),
        }
    return out


def forget(fingerprint: str, *, limits: bool = False) -> None:
    """Drops what is remembered as used up for one key: a passing Test on
    Settings > My AI key (it may have been billed meanwhile), and -- with
    limits=True, since a free key's daily limits say nothing about a billed
    one -- a key saved again or switched between Free and Billed
    (api/ai_keys.py)."""
    prefix = _tag(fingerprint) + "|"
    try:
        with _locked() as path:
            data = _read(path)
            data["spent"] = {k: v for k, v in data["spent"].items() if not k.startswith(prefix)}
            if limits:
                data["limits"] = {k: v for k, v in data["limits"].items() if not k.startswith(prefix)}
            _write(path, data)
    except Exception as exc:
        logger.warning("Day allowance: couldn't forget a key (%s)", type(exc).__name__)


def clear() -> None:
    """Forgets everything, limits too (tests)."""
    with _locked() as path:
        _write(path, {"spent": {}, "limits": {}})
