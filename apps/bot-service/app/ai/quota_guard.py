"""The shared quota guard: production and staging on ONE Gemini key.

Why (plans/2026-11-22-enhance-staging, R-batching.md 3d "B1")
--------------------------------------------------------------
The owner chose one free key for both stacks. Google's limits are per Google
Cloud project, not per key: one daily allowance per model (about 500 a day
on each light model) and one per-minute window, shared. But each stack kept
its rate-limit window (ai/rate_limit.py) and its "day used up" memory
(ai/allowance.py) in its OWN storage volume, so neither saw the other: two
12-a-minute windows add up to 24 on one project, and a staging benchmark
could eat production's day.

So, when settings.gemini_shared_quota_dir names a folder that is there (the
host folder infra/docker-compose.ai-shared.yml mounts into production's api
and bot and staging's api), every Gemini request of every stack goes
through ONE set of files in it, per key (named by a hash of the key, never
the key):

    window/<tag>.json   the last 60 s of requests, each tagged with its lane
    day/<tag>.json      today's requests per model per lane (Pacific day)
    locks/<tag>.lock    flock, so processes in different containers on the
                        host take turns (same kernel, same host folder)
    allowance/days.json ai/allowance.py's "day used up" memory, moved here

Lanes (settings.gemini_quota_lane):

    prod      up to gemini_max_requests_per_minute (12) of its OWN requests in
              any 60 s, spaced gemini_min_call_interval_seconds apart; never
              waits for staging. Its base pipeline is never cut.
    staging   a slot only while the window, with its slot, holds at most
              gemini_staging_window_share (6) requests of both lanes, and at
              most gemini_staging_max_per_minute (4) of its own; and nothing
              more until the next reset (midnight Pacific) once
                - staging has sent gemini_staging_day_max (300) today, or
                - production has sent gemini_staging_prod_busy (400) today, or
                - the model has fewer than gemini_staging_model_reserve (300)
                  of its day left -- a model with no known daily limit
                  (gemini_quota_day_limits, or Google's own "limit: N") is
                  never used by staging.
              Without the shared folder staging sends nothing at all: it
              could not see production's use.

Every request sent is counted, a 503 "busy" or a 429 included (whether
Google counts those toward the day is unknown; counting them is the safe
side), retries too: call_structured takes a slot for its retry of a busy
model in shared mode (rate_limit.acquire_retry).

Without the folder (gemini_shared_quota_dir blank, the default, or not
mounted yet) and in the production lane, nothing here runs: the old
per-volume files, waits and requests, exactly as before.

A refusal is a LaneRefused, an allowance.DaySpent: call_structured skips the
model with nothing sent, as it skips a model whose free day is used up, and
a call whose every model was skipped ends in allowance.DayUsedUp.

Production's "extras" tiers (extras_tier) are for later features that spend
spare calls (E8 voting, E9a a third link-check area, ...): RICH when the
tier model (gemini_quota_tier_model) has more than half its day left,
NORMAL 25-50%, LEAN under 25%. Nothing calls it yet.

Nothing here may break production's AI: an unreadable file is a fresh one
(logged), a lock that can't be taken is skipped (logged), as rate_limit.py
does.
"""
import hashlib
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.ai import allowance
from app.config import day_limits, settings

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60.0
# A stamp further ahead than this is a clock that went back: dropped.
FUTURE_SLACK_SECONDS = 5.0
LANES = ("prod", "staging")
TIERS = ("RICH", "NORMAL", "LEAN")
# The count of a request whose model the caller didn't say.
UNKNOWN_MODEL = "(model not given)"

# The window's clock: wall time, the same in every container on the host
# (unlike time.monotonic across a reboot). Tests move it; and the sleep.
_now = time.time
_sleep = time.sleep

# Same-process threads; flock covers process to process.
_local_lock = threading.Lock()
_warned: set[str] = set()


class LaneRefused(allowance.DaySpent):
    """The staging lane may not send this request: its share of the shared
    key is used up for today, or there is no shared folder to see
    production's use in. Nothing was sent. A DaySpent, so call_structured
    treats it as the model's free day used up: skipped, on to the next."""

    def __init__(self, model: str | None, why: str):
        Exception.__init__(
            self, f"{model or 'Gemini'}: the staging lane may not send now ({why}); no request sent"
        )
        self.model = model
        self.why = why


# ------------------------------------------------------------ the mode


def lane() -> str:
    return settings.gemini_quota_lane


def shared_dir() -> Path | None:
    """The shared folder when it is set AND there (mounted, writable); else
    None -- this stack's own files, as before."""
    raw = (settings.gemini_shared_quota_dir or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_dir() and os.access(path, os.W_OK | os.X_OK):
        return path
    _warn_once(raw, "Shared AI quota folder %s is not there or not writable: this stack uses its own window "
                    "and day memory (the staging lane sends nothing)", raw)
    return None


def mode() -> str:
    """"shared" (the folder is there), "local" (production lane without
    it: the old per-volume files, exactly as before) or "refuse" (staging
    lane without it: nothing is sent)."""
    if shared_dir() is not None:
        return "shared"
    return "refuse" if lane() == "staging" else "local"


def key_tag(api_key: str | None) -> str:
    """What a key's files are named after: the same hash rate_limit.py
    names its window by, never the key."""
    return hashlib.sha256((api_key or "env-default").encode()).hexdigest()[:16]


def _warn_once(key: str, message: str, *args) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(message, *args)


# ------------------------------------------------------------ the files


def _folder(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


@contextmanager
def _locked(root: Path, tag: str) -> Iterator[None]:
    """One key's window and day files, held exclusively: this process's
    threads by _local_lock, every process on the host (any container) by
    flock on locks/<tag>.lock. Not the data files themselves: os.replace
    swaps them."""
    with _local_lock:
        handle = open(_folder(root, "locks") / f"{tag}.lock", "a+b")
        try:
            locked = _lock(handle)
            try:
                yield
            finally:
                if locked:
                    _unlock(handle)
        finally:
            handle.close()


def _lock(handle) -> bool:
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return True
    except ImportError:
        pass
    except OSError as exc:
        _warn_once("flock", "Shared AI quota: couldn't lock (%s); going on without it", type(exc).__name__)
        return False
    # Windows (tests and local runs only; the server is Linux): a byte lock.
    try:
        import msvcrt

        deadline = time.monotonic() + 30
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.005)
    except (ImportError, OSError) as exc:
        _warn_once("msvcrt", "Shared AI quota: couldn't lock (%s); going on without it", type(exc).__name__)
        return False


def _unlock(handle) -> None:
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    except ImportError:
        pass
    except OSError:
        return
    try:
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except (ImportError, OSError):
        pass


def _read_json(path: Path):
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return json.loads(raw) if raw else None
    except FileNotFoundError:
        return None
    except Exception:
        # Losing it costs at worst one over-permissive minute or day count.
        logger.warning("Shared AI quota file %s unreadable; starting it afresh", path.name)
        return None


def _write_json(path: Path, data) -> None:
    # Per process: even unlocked (a failed lock), two writers can't share one.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a reader never sees half a file


def _read_window(path: Path, now: float) -> list[list]:
    """[[stamp, lane], ...] of the last WINDOW_SECONDS."""
    data = _read_json(path)
    stamps = data.get("stamps") if isinstance(data, dict) else None
    out = []
    for item in stamps or []:
        if (isinstance(item, list) and len(item) == 2 and isinstance(item[0], (int, float))
                and not isinstance(item[0], bool) and item[1] in LANES
                and now - WINDOW_SECONDS < item[0] <= now + FUTURE_SLACK_SECONDS):
            out.append([float(item[0]), item[1]])
    return out


def today() -> str:
    """Google's current day (the Pacific date since the last midnight
    there), e.g. "2026-11-22": what the day counts are kept under."""
    return allowance.day_started().astimezone(allowance.RESET_ZONE).date().isoformat()


def _read_day(path: Path) -> dict:
    """{"day": today(), "counts": {model: {lane: n}}}; a file from an
    earlier day reads as a fresh one (the reset)."""
    day = today()
    data = _read_json(path)
    counts: dict[str, dict[str, int]] = {}
    if isinstance(data, dict) and data.get("day") == day and isinstance(data.get("counts"), dict):
        for model, lanes in data["counts"].items():
            if isinstance(lanes, dict):
                kept = {ln: n for ln, n in lanes.items()
                        if ln in LANES and isinstance(n, int) and not isinstance(n, bool) and n >= 0}
                if kept:
                    counts[str(model)] = kept
    return {"day": day, "counts": counts}


def _totals(counts: dict) -> dict[str, int]:
    return {ln: sum(lanes.get(ln, 0) for lanes in counts.values()) for ln in LANES}


# ------------------------------------------------------------ the rules


def model_limit(model: str | None, fingerprint: str | None = None) -> int | None:
    """Requests a day the model allows on this key: what Google itself said
    in a 429 (allowance.limit), else gemini_quota_day_limits; None when
    unknown."""
    if not model:
        return None
    if fingerprint:
        stated = allowance.limit(fingerprint, model)
        if stated is not None:
            return stated
    return day_limits(settings.gemini_quota_day_limits).get(model)


def staging_stop(counts: dict, model: str | None, limit: int | None) -> str | None:
    """Why the staging lane may not send a request to `model` today, given
    today's counts; None when it may."""
    totals = _totals(counts)
    if totals["staging"] >= settings.gemini_staging_day_max:
        return f"staging has sent {totals['staging']} requests today, its cap is {settings.gemini_staging_day_max}"
    if totals["prod"] >= settings.gemini_staging_prod_busy:
        return (f"production has sent {totals['prod']} requests today; staging stops at "
                f"{settings.gemini_staging_prod_busy}")
    if settings.gemini_staging_max_per_minute <= 0 or settings.gemini_staging_window_share <= 0:
        return "staging's share of the per-minute window is 0"
    if model:
        if limit is None:
            return f"no known daily limit for {model} (gemini_quota_day_limits): it stays production's"
        used = sum((counts.get(model) or {}).values())
        left = limit - used
        if left < settings.gemini_staging_model_reserve:
            return (f"{model} has {max(0, left)} of {limit} left today; production keeps "
                    f"{settings.gemini_staging_model_reserve}")
    return None


def _slot_wait(stamps: list[list], me: str, now: float, *, spacing: bool = True) -> float:
    """Seconds until `me` may take a slot in this window; 0 = now."""
    gap = max(0.0, settings.gemini_min_call_interval_seconds) if spacing else 0.0
    if me == "prod":
        mine = sorted(t for t, ln in stamps if ln == "prod")
        limit = max(1, settings.gemini_max_requests_per_minute)
        if len(mine) >= limit:
            return max(0.05, WINDOW_SECONDS - (now - mine[len(mine) - limit]))
        if mine and gap > 0:
            return max(0.0, gap - (now - mine[-1]))
        return 0.0
    every = sorted(t for t, _ in stamps)
    mine = sorted(t for t, ln in stamps if ln == "staging")
    own_limit = max(1, min(settings.gemini_max_requests_per_minute, settings.gemini_staging_max_per_minute))
    share = max(1, settings.gemini_staging_window_share)
    waits = [0.0]
    if len(mine) >= own_limit:
        waits.append(max(0.05, WINDOW_SECONDS - (now - mine[len(mine) - own_limit])))
    if len(every) + 1 > share:
        # The oldest requests that must age out first for this one to fit.
        waits.append(max(0.05, WINDOW_SECONDS - (now - every[len(every) - share])))
    if every and gap > 0:
        waits.append(max(0.0, gap - (now - every[-1])))
    return max(waits)


# ------------------------------------------------------------ taking a slot


def acquire(api_key: str | None = None, *, model: str | None = None, fingerprint: str | None = None) -> float:
    """Claims one slot in the shared window for one request about to be
    sent, and counts it for today (model, lane). Waits when the lane's
    share of the window is full (the file lock released while it sleeps).
    Returns the seconds waited. Raises LaneRefused for the staging lane
    when it may not send at all (staging_stop, or no shared folder)."""
    me = lane()
    root = shared_dir()
    if root is None:
        if me == "staging":
            raise LaneRefused(model, "no shared quota folder, so staging can't see production's use of the key")
        raise RuntimeError("quota_guard.acquire without the shared folder: use rate_limit.acquire")
    tag = key_tag(api_key)
    window_path = _folder(root, "window") / f"{tag}.json"
    day_path = _folder(root, "day") / f"{tag}.json"
    limit = model_limit(model, fingerprint) if me == "staging" else None
    waited = 0.0
    while True:
        now = _now()
        with _locked(root, tag):
            stamps = _read_window(window_path, now)
            day = _read_day(day_path)
            if me == "staging":
                why = staging_stop(day["counts"], model, limit)
                if why:
                    raise LaneRefused(model, why)
            sleep_for = _slot_wait(stamps, me, now)
            if sleep_for <= 0:
                stamps.append([now, me])
                counts = day["counts"].setdefault(model or UNKNOWN_MODEL, {})
                counts[me] = counts.get(me, 0) + 1
                _write_json(window_path, {"stamps": stamps})
                _write_json(day_path, day)
                return waited
        logger.info("Gemini rate limit (%s lane, shared window): waiting %.1fs for a slot", me, sleep_for)
        _sleep(sleep_for)
        waited += sleep_for


def estimate_wait(api_key: str | None = None) -> float:
    """Seconds this lane's next request would wait for the window now (the
    per-minute share only, as rate_limit.estimate_wait), without taking a
    slot. 0 without the shared folder."""
    root = shared_dir()
    if root is None:
        return 0.0
    now = _now()
    tag = key_tag(api_key)
    with _locked(root, tag):
        stamps = _read_window(_folder(root, "window") / f"{tag}.json", now)
    return _slot_wait(stamps, lane(), now, spacing=False)


# ------------------------------------------------------------ reading it


def day_counts(api_key: str | None = None) -> dict[str, dict[str, int]]:
    """Today's requests on this key, {model: {lane: n}}; {} without the
    shared folder (nothing is counted then)."""
    root = shared_dir()
    if root is None:
        return {}
    return _read_day(root / "day" / f"{key_tag(api_key)}.json")["counts"]


def extras_state(api_key: str | None = None, model: str | None = None, fingerprint: str | None = None) -> dict:
    """Production's extras tier and what it is worked out from:
    {"tier", "model", "limit", "used", "left", "known"}. RICH when the
    tier model has more than half its day left, NORMAL from a quarter to a
    half, LEAN under a quarter. Without the shared folder, or with no known
    limit for the model, nothing is counted: RICH, known False."""
    model = model or settings.gemini_quota_tier_model
    key = api_key if api_key is not None else (settings.gemini_api_key or None)
    limit = model_limit(model, fingerprint)
    if shared_dir() is None or not limit:
        return {"tier": "RICH", "model": model, "limit": limit, "used": None, "left": None, "known": False}
    used = sum((day_counts(key).get(model) or {}).values())
    left = max(0, limit - used)
    share = left / limit
    tier = "RICH" if share > 0.5 else "NORMAL" if share >= 0.25 else "LEAN"
    return {"tier": tier, "model": model, "limit": limit, "used": used, "left": left, "known": True}


def extras_tier(api_key: str | None = None, model: str | None = None, fingerprint: str | None = None) -> str:
    """"RICH", "NORMAL" or "LEAN" (extras_state). For features that spend
    spare calls; the base pipeline never asks."""
    return extras_state(api_key, model, fingerprint)["tier"]


def summary(key_labels: dict[str, str] | None = None) -> dict:
    """For an admin (GET /system/info ai_quota): the mode, this stack's
    lane, the reset, and per key with requests today -- per model per lane,
    the known limit, the last minute's window, and whether staging is
    stopped. Keys by label (key_labels: {key_tag: label}), never a key."""
    root = shared_dir()
    out = {
        "mode": mode(),
        "lane": lane(),
        "day": today(),
        "day_resets_at": allowance.iso(allowance.next_reset()),
        "limits": {
            "prod_per_minute": settings.gemini_max_requests_per_minute,
            "staging_per_minute": settings.gemini_staging_max_per_minute,
            "staging_window_share": settings.gemini_staging_window_share,
            "staging_day_max": settings.gemini_staging_day_max,
            "staging_stops_when_prod_sent": settings.gemini_staging_prod_busy,
            "staging_model_reserve": settings.gemini_staging_model_reserve,
            "per_model_day": day_limits(settings.gemini_quota_day_limits),
        },
        "keys": [],
    }
    if root is None:
        return out
    labels = key_labels or {}
    now = _now()
    for path in sorted((root / "day").glob("*.json")):
        tag = path.stem
        counts = _read_day(path)["counts"]
        if not counts:
            continue
        stamps = _read_window(root / "window" / f"{tag}.json", now)
        models = {}
        for model, lanes in sorted(counts.items()):
            limit = model_limit(model) if model != UNKNOWN_MODEL else None
            used = sum(lanes.values())
            models[model] = {"prod": lanes.get("prod", 0), "staging": lanes.get("staging", 0),
                             "limit": limit, "left": None if limit is None else max(0, limit - used)}
        out["keys"].append({
            "key": labels.get(tag, f"other key {tag[:6]}"),
            "models": models,
            "totals": _totals(counts),
            "last_minute": {ln: sum(1 for _, x in stamps if x == ln) for ln in LANES},
            "staging_stopped": staging_stop(counts, None, None),
        })
    return out
