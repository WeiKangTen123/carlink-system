"""Polite HTTP fetching with an SQLite page cache.

One `PoliteFetcher` per fetch policy (usually one per source). It keeps
separate state per host: one request at a time, a random delay between
requests, a daily cap, robots.txt (honest mode), cookies and the
User-Agent a clearance provider handed back.

Thread-safe for one process: per-host locks serialise live requests, cache
reads do not wait. Call it from worker threads (the MCP SDK runs sync tools
in threads); from asyncio use `asyncio.to_thread`.
"""

from __future__ import annotations

import gzip
import http.cookiejar
import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha1
from pathlib import Path
from typing import Any, Callable, Collection, Literal, Mapping, Protocol
from urllib.parse import urlencode, urlsplit
from urllib.robotparser import RobotFileParser

from .errors import (
    ChallengeBlocked,
    DailyCapReached,
    FetchError,
    HttpStatusError,
    NeedsHuman,
    RateLimited,
    RobotsDisallowed,
)
from .models import utcnow

log = logging.getLogger("parts_mcp.fetch")

DAY = 86400.0
TTL_CATALOGUE = 180 * DAY
TTL_PRICE = 7 * DAY
TTL_ROBOTS = 1 * DAY

_RETRYABLE_STATUS = (502, 503, 504)
_CHALLENGE_TITLE = re.compile(rb"<title>\s*just a moment", re.IGNORECASE)


# --- policy and results ---------------------------------------------------------


@dataclass(frozen=True)
class FetchPolicy:
    """How politely to talk to the hosts of one source."""

    name: str  # source name; used in logs and error messages
    ua_mode: Literal["honest", "impersonate"]
    min_delay: float  # seconds between requests to the same host (lower bound)
    max_delay: float  # upper bound; actual delay is random in [min, max]
    daily_cap: int  # live requests per host per UTC day, retries included
    timeout: float = 30.0  # seconds per request
    user_agent: str | None = None  # honest: required. impersonate: None = browser's own UA
    impersonate: str = "chrome"  # curl_cffi target, impersonate mode only
    respect_robots: bool = True  # honest mode only
    accept_language: str = "en-GB,en;q=0.9"
    retries: int = 1  # extra attempts after a timeout / connection error / 502 / 503 / 504
    max_retry_after: float = 120.0  # cap on a server's Retry-After we will honour

    def __post_init__(self) -> None:
        if self.ua_mode not in ("honest", "impersonate"):
            raise ValueError(f"ua_mode must be 'honest' or 'impersonate', not {self.ua_mode!r}")
        if self.ua_mode == "honest" and not self.user_agent:
            raise ValueError("honest mode needs a descriptive user_agent")
        if self.min_delay < 0 or self.max_delay < self.min_delay:
            raise ValueError("need 0 <= min_delay <= max_delay")


@dataclass(frozen=True)
class FetchResult:
    url: str  # the URL asked for
    final_url: str  # after redirects
    status: int
    headers: Mapping[str, str]  # lower-case names
    body: bytes
    fetched_at: datetime  # when the body was fetched live (original time for cache hits)
    from_cache: bool

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        match = re.search(r"charset=([\w-]+)", self.headers.get("content-type", ""), re.IGNORECASE)
        encoding = match.group(1) if match else "utf-8"
        try:
            return self.body.decode(encoding, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class RawResponse:
    """What a transport returns for one HTTP exchange."""

    status: int
    headers: dict[str, str]  # lower-case names
    body: bytes
    final_url: str


def is_challenge(status: int, headers: Mapping[str, str], body: bytes) -> bool:
    """True for a bot-challenge page (Cloudflare 'Just a moment...' and friends)."""
    if status in (403, 503) and headers.get("cf-mitigated", "").strip().lower() == "challenge":
        return True
    return bool(_CHALLENGE_TITLE.search(body[:16384]))


# --- clearance hook ---------------------------------------------------------------
# Optional and generic. No source in this package passes a provider: a challenge
# page then fails at once as ChallengeBlocked, and nothing tries to get past it.


@dataclass(frozen=True)
class ClearanceRequest:
    """Passed to the clearance provider when a challenge is detected."""

    source: str  # policy name, e.g. "microfiches"
    url: str  # the URL that was challenged
    host: str
    status: int  # HTTP status of the challenge response
    user_agent: str | None  # UA we sent; None = curl_cffi's impersonated default


@dataclass
class Clearance:
    """What the clearance provider hands back.

    `cookies` are dicts with `name` and `value`, optionally `domain`, `path`,
    `secure`, `expiry` (unix seconds). Without `domain` the cookie is set for
    the host's domain. `user_agent`, when given, is sent on every later
    request to that host (a clearance cookie is usually bound to it) and is
    remembered across restarts.
    """

    cookies: list[dict[str, Any]] = field(default_factory=list)
    user_agent: str | None = None
    needs_human: bool = False  # True: could not clear without a person
    message: str | None = None


class ClearanceProvider(Protocol):
    """Called with the host lock held, so at most one per host runs at once.
    It must not call back into the same fetcher. It may raise NeedsHuman.
    It must never try to solve a captcha."""

    def __call__(self, request: ClearanceRequest) -> Clearance: ...


# --- transports -------------------------------------------------------------------


class TransportError(Exception):
    """Network-level failure (timeout, DNS, TLS, connection reset)."""


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        data: bytes | None,
        timeout: float,
    ) -> RawResponse:
        """Send one request, following redirects. Raise TransportError on network failure."""
        ...

    def add_cookies(self, host: str, cookies: list[dict[str, Any]]) -> None: ...

    def close(self) -> None: ...


def _cookie_domain(host: str) -> str:
    return "." + host.removeprefix("www.")


def _to_jar_cookie(spec: Mapping[str, Any], host: str) -> http.cookiejar.Cookie:
    domain = str(spec.get("domain") or _cookie_domain(host))
    expiry = spec.get("expiry", spec.get("expires"))
    return http.cookiejar.Cookie(
        version=0,
        name=str(spec["name"]),
        value=str(spec["value"]),
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path=str(spec.get("path") or "/"),
        path_specified=True,
        secure=bool(spec.get("secure", False)),
        expires=int(expiry) if expiry is not None else None,
        discard=expiry is None,
        comment=None,
        comment_url=None,
        rest={},
    )


class CurlTransport:
    """curl_cffi transport, one Session per host, cookies saved per host as JSON."""

    def __init__(self, *, impersonate: str | None, cookie_dir: Path | None = None) -> None:
        self._impersonate = impersonate
        self._cookie_dir = Path(cookie_dir) if cookie_dir else None
        self._sessions: dict[str, Any] = {}
        self._lock = threading.Lock()

    def _cookie_file(self, host: str) -> Path | None:
        if self._cookie_dir is None:
            return None
        return self._cookie_dir / f"{re.sub(r'[^A-Za-z0-9.-]', '_', host)}.json"

    def _session(self, host: str) -> Any:
        with self._lock:
            session = self._sessions.get(host)
            if session is None:
                from curl_cffi import requests as curl_requests  # heavy import, only when used

                kwargs: dict[str, Any] = {}
                if self._impersonate:
                    kwargs["impersonate"] = self._impersonate
                session = curl_requests.Session(**kwargs)
                self._load_cookies(session, host)
                self._sessions[host] = session
            return session

    def _load_cookies(self, session: Any, host: str) -> None:
        path = self._cookie_file(host)
        if path is None or not path.exists():
            return
        try:
            specs = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("ignoring unreadable cookie file %s", path)
            return
        now = time.time()
        for spec in specs:
            expiry = spec.get("expiry")
            if expiry is not None and expiry < now:
                continue
            session.cookies.jar.set_cookie(_to_jar_cookie(spec, host))

    def _save_cookies(self, session: Any, host: str) -> None:
        path = self._cookie_file(host)
        if path is None:
            return
        specs = [
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path,
                "secure": c.secure,
                "expiry": c.expires,
            }
            for c in session.cookies.jar
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(specs, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        data: bytes | None,
        timeout: float,
    ) -> RawResponse:
        from curl_cffi.requests.exceptions import RequestException

        host = urlsplit(url).hostname or ""
        session = self._session(host)
        try:
            resp = session.request(
                method, url, headers=dict(headers), data=data, timeout=timeout, allow_redirects=True
            )
        except RequestException as exc:
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc
        self._save_cookies(session, host)
        return RawResponse(
            status=resp.status_code,
            headers={k.lower(): v for k, v in resp.headers.items()},
            body=resp.content,
            final_url=str(resp.url),
        )

    def add_cookies(self, host: str, cookies: list[dict[str, Any]]) -> None:
        session = self._session(host)
        for spec in cookies:
            session.cookies.jar.set_cookie(_to_jar_cookie(spec, host))
        self._save_cookies(session, host)

    def close(self) -> None:
        with self._lock:
            for session in self._sessions.values():
                try:
                    session.close()
                except Exception:  # closing is best-effort
                    pass
            self._sessions.clear()


# --- page cache -------------------------------------------------------------------

_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    key        TEXT PRIMARY KEY,
    url        TEXT NOT NULL,
    method     TEXT NOT NULL,
    status     INTEGER NOT NULL,
    headers    TEXT NOT NULL,
    body       BLOB NOT NULL,      -- gzip
    final_url  TEXT NOT NULL,
    fetched_at TEXT NOT NULL       -- ISO 8601 UTC
);
CREATE TABLE IF NOT EXISTS request_counts (
    host  TEXT NOT NULL,
    day   TEXT NOT NULL,           -- UTC date
    count INTEGER NOT NULL,
    PRIMARY KEY (host, day)
);
CREATE TABLE IF NOT EXISTS host_state (
    host            TEXT PRIMARY KEY,
    user_agent      TEXT,
    last_request_at TEXT
);
"""

_DROP_HEADERS = {"set-cookie", "content-encoding", "content-length", "transfer-encoding"}


class PageCache:
    """Raw page cache plus per-host counters. Opening it creates the schema."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._lock = threading.RLock()
        with self._lock, self._conn:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_CACHE_SCHEMA)

    def get(self, key: str) -> FetchResult | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT url, status, headers, body, final_url, fetched_at FROM pages WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        url, status, headers, body, final_url, fetched_at = row
        return FetchResult(
            url=url,
            final_url=final_url,
            status=status,
            headers=json.loads(headers),
            body=gzip.decompress(body),
            fetched_at=datetime.fromisoformat(fetched_at),
            from_cache=True,
        )

    def put(self, key: str, method: str, result: FetchResult) -> None:
        headers = {k: v for k, v in result.headers.items() if k not in _DROP_HEADERS}
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO pages (key, url, method, status, headers, body, final_url, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                     url=excluded.url, method=excluded.method, status=excluded.status,
                     headers=excluded.headers, body=excluded.body,
                     final_url=excluded.final_url, fetched_at=excluded.fetched_at""",
                (
                    key,
                    result.url,
                    method,
                    result.status,
                    json.dumps(headers),
                    gzip.compress(result.body),
                    result.final_url,
                    result.fetched_at.astimezone(timezone.utc).isoformat(),
                ),
            )

    def requests_on(self, host: str, day: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT count FROM request_counts WHERE host = ? AND day = ?", (host, day)
            ).fetchone()
        return row[0] if row else 0

    def requests_by_host(self, day: str) -> dict[str, int]:
        """Live requests per host on one UTC day (robots.txt and retries included)."""
        with self._lock:
            rows = self._conn.execute("SELECT host, count FROM request_counts WHERE day = ?", (day,)).fetchall()
        return {host: count for host, count in rows}

    def add_request(self, host: str, day: str, at: datetime) -> int:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO request_counts (host, day, count) VALUES (?, ?, 1)
                   ON CONFLICT(host, day) DO UPDATE SET count = count + 1""",
                (host, day),
            )
            self._conn.execute(
                """INSERT INTO host_state (host, last_request_at) VALUES (?, ?)
                   ON CONFLICT(host) DO UPDATE SET last_request_at = excluded.last_request_at""",
                (host, at.isoformat()),
            )
        return self.requests_on(host, day)

    def host_state(self, host: str) -> tuple[str | None, datetime | None]:
        """(remembered User-Agent, time of last live request)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT user_agent, last_request_at FROM host_state WHERE host = ?", (host,)
            ).fetchone()
        if row is None:
            return None, None
        return row[0], datetime.fromisoformat(row[1]) if row[1] else None

    def set_user_agent(self, host: str, user_agent: str | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO host_state (host, user_agent) VALUES (?, ?)
                   ON CONFLICT(host) DO UPDATE SET user_agent = excluded.user_agent""",
                (host, user_agent),
            )

    def prune(self, older_than_seconds: float, *, now: datetime | None = None) -> int:
        """Delete cached pages older than the given age. Returns rows deleted."""
        cutoff = (now or utcnow()).timestamp() - older_than_seconds
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat()
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM pages WHERE fetched_at < ?", (cutoff_iso,))
        return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# --- the fetcher ------------------------------------------------------------------


@dataclass
class _HostState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    next_ok: float = 0.0  # monotonic time before which we must not send
    user_agent: str | None = None  # from a clearance provider
    robots: RobotFileParser | None = None
    robots_checked: float = 0.0
    crawl_delay: float = 0.0


def _parse_retry_after(headers: Mapping[str, str]) -> float | None:
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value) - utcnow()).total_seconds())
        except (TypeError, ValueError):
            return None


class PoliteFetcher:
    def __init__(
        self,
        policy: FetchPolicy,
        cache: PageCache,
        *,
        transport: HttpTransport | None = None,
        clearance_provider: ClearanceProvider | None = None,
        cookie_dir: str | Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = utcnow,
        rng: random.Random | None = None,
    ) -> None:
        self.policy = policy
        self.cache = cache
        self.clearance_provider = clearance_provider
        self._transport: HttpTransport = transport or CurlTransport(
            impersonate=policy.impersonate if policy.ua_mode == "impersonate" else None,
            cookie_dir=Path(cookie_dir) if cookie_dir else None,
        )
        self._sleep = sleep
        self._clock = clock
        self._now = now
        self._rng = rng or random.Random()
        self._states: dict[str, _HostState] = {}
        self._states_lock = threading.Lock()

    # public API ----------------------------------------------------------------

    def get(
        self,
        url: str,
        *,
        ttl: float = TTL_CATALOGUE,
        headers: Mapping[str, str] | None = None,
        referer: str | None = None,
        accept_status: Collection[int] = (),
        cache_if: Callable[[FetchResult], bool] | None = None,
    ) -> FetchResult:
        """GET a page. A cached copy younger than `ttl` seconds is returned
        without touching the network; `ttl=0` forces a live request.
        Raises HttpStatusError for status >= 400 unless listed in `accept_status`.
        `cache_if(result)` returning False keeps a 2xx answer out of the cache
        (e.g. a site's own block page reached by redirect)."""
        return self._fetch("GET", url, None, ttl, headers, referer, accept_status, cache_if)

    def post(
        self,
        url: str,
        data: Mapping[str, str] | bytes | str,
        *,
        ttl: float = 0.0,
        headers: Mapping[str, str] | None = None,
        referer: str | None = None,
        accept_status: Collection[int] = (),
    ) -> FetchResult:
        """POST a form (dict) or raw body. Cached by URL + body like GET."""
        if isinstance(data, Mapping):
            body = urlencode(data).encode()
            headers = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
        else:
            body = data.encode() if isinstance(data, str) else data
        return self._fetch("POST", url, body, ttl, headers, referer, accept_status)

    def cached(self, url: str, *, ttl: float = TTL_CATALOGUE) -> FetchResult | None:
        """Cache-only peek; never touches the network."""
        return self._from_cache(self._cache_key("GET", url, None), ttl)

    def requests_today(self, host: str) -> int:
        return self.cache.requests_on(host, self._today())

    def close(self) -> None:
        self._transport.close()

    # internals -----------------------------------------------------------------

    def _today(self) -> str:
        return self._now().astimezone(timezone.utc).date().isoformat()

    @staticmethod
    def _cache_key(method: str, url: str, body: bytes | None) -> str:
        if body is None:
            return f"{method} {url}"
        return f"{method} {url} {sha1(body).hexdigest()}"

    def _from_cache(self, key: str, ttl: float) -> FetchResult | None:
        if ttl <= 0:
            return None
        hit = self.cache.get(key)
        if hit is None:
            return None
        age = (self._now() - hit.fetched_at).total_seconds()
        if age > ttl:
            return None
        log.info("cache hit host=%s age=%.0fs url=%s", urlsplit(hit.url).hostname, age, hit.url)
        return hit

    def _state(self, host: str) -> _HostState:
        with self._states_lock:
            state = self._states.get(host)
            if state is None:
                state = _HostState()
                user_agent, last = self.cache.host_state(host)
                state.user_agent = user_agent
                if last is not None:  # keep spacing across restarts
                    since = (self._now() - last).total_seconds()
                    state.next_ok = self._clock() + max(0.0, self.policy.min_delay - since)
                self._states[host] = state
            return state

    def _fetch(
        self,
        method: str,
        url: str,
        body: bytes | None,
        ttl: float,
        headers: Mapping[str, str] | None,
        referer: str | None,
        accept_status: Collection[int],
        cache_if: Callable[[FetchResult], bool] | None = None,
    ) -> FetchResult:
        parts = urlsplit(url)
        host = parts.hostname
        if parts.scheme not in ("http", "https") or not host:
            raise FetchError(f"not an http(s) URL: {url}", source=self.policy.name, url=url)
        key = self._cache_key(method, url, body)
        hit = self._from_cache(key, ttl)
        if hit is not None:
            return hit

        state = self._state(host)
        with state.lock:
            hit = self._from_cache(key, ttl)  # another thread may have fetched it meanwhile
            if hit is not None:
                return hit
            if self.policy.ua_mode == "honest" and self.policy.respect_robots:
                self._check_robots(state, parts.scheme, host, url)

            raw = self._send(state, host, method, url, body, headers, referer)
            if is_challenge(raw.status, raw.headers, raw.body):
                raw = self._clear_and_retry(state, host, method, url, body, headers, referer, raw)

            if raw.status >= 400 and raw.status not in accept_status:
                raise HttpStatusError(raw.status, source=self.policy.name, host=host, url=url)
            result = FetchResult(
                url=url,
                final_url=raw.final_url,
                status=raw.status,
                headers=raw.headers,
                body=raw.body,
                fetched_at=self._now(),
                from_cache=False,
            )
            if result.ok and (cache_if is None or cache_if(result)):
                self.cache.put(key, method, result)
            return result

    def _delay(self, state: _HostState) -> float:
        low = max(self.policy.min_delay, state.crawl_delay)
        high = max(self.policy.max_delay, low)
        return self._rng.uniform(low, high)

    def _headers(
        self, state: _HostState, extra: Mapping[str, str] | None, referer: str | None
    ) -> dict[str, str]:
        out: dict[str, str] = {"Accept-Language": self.policy.accept_language}
        if self.policy.ua_mode == "honest":
            out["User-Agent"] = self.policy.user_agent or ""
            out["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        elif state.user_agent or self.policy.user_agent:
            out["User-Agent"] = state.user_agent or self.policy.user_agent or ""
        if referer:
            out["Referer"] = referer
        out.update(extra or {})
        return out

    def _send(
        self,
        state: _HostState,
        host: str,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str] | None,
        referer: str | None,
    ) -> RawResponse:
        """Throttled live request with the daily cap and limited retries. Host lock held."""
        attempts = 1 + max(0, self.policy.retries)
        for attempt in range(1, attempts + 1):
            wait = state.next_ok - self._clock()
            if wait > 0:
                self._sleep(wait)
            day = self._today()
            if self.cache.requests_on(host, day) >= self.policy.daily_cap:
                raise DailyCapReached(
                    f"{self.policy.daily_cap} requests/day to {host} already used",
                    source=self.policy.name,
                    host=host,
                    url=url,
                )
            self.cache.add_request(host, day, self._now())
            started = self._clock()
            try:
                raw = self._transport.request(
                    method,
                    url,
                    headers=self._headers(state, headers, referer),
                    data=body,
                    timeout=self.policy.timeout,
                )
            except TransportError as exc:
                state.next_ok = self._clock() + self._delay(state)
                log.warning(
                    "live %s host=%s status=error secs=%.2f cache=miss url=%s err=%s",
                    method, host, self._clock() - started, url, exc,
                )
                if attempt < attempts:
                    continue
                raise FetchError(str(exc), source=self.policy.name, host=host, url=url) from exc

            state.next_ok = self._clock() + self._delay(state)
            log.info(
                "live %s host=%s status=%s secs=%.2f cache=miss url=%s",
                method, host, raw.status, self._clock() - started, url,
            )
            retry_after = _parse_retry_after(raw.headers)
            if retry_after is not None:
                state.next_ok = max(
                    state.next_ok, self._clock() + min(retry_after, self.policy.max_retry_after)
                )
            if raw.status == 429:
                if retry_after is None:
                    state.next_ok = max(state.next_ok, self._clock() + 60.0)
                raise RateLimited("HTTP 429", source=self.policy.name, host=host, url=url)
            if (
                raw.status in _RETRYABLE_STATUS
                and attempt < attempts
                and not is_challenge(raw.status, raw.headers, raw.body)
            ):
                continue
            return raw
        raise AssertionError("unreachable")

    def _check_robots(self, state: _HostState, scheme: str, host: str, url: str) -> None:
        agent = self.policy.user_agent or "*"
        if state.robots is None or self._clock() - state.robots_checked > TTL_ROBOTS:
            robots_url = f"{scheme}://{host}/robots.txt"
            key = self._cache_key("GET", robots_url, None)
            hit = self._from_cache(key, TTL_ROBOTS)
            fetched_live = hit is None
            if hit is not None:
                status, body = hit.status, hit.body
            else:
                raw = self._send(state, host, "GET", robots_url, None, None, None)
                status, body = raw.status, raw.body
                if 200 <= status < 300:
                    self.cache.put(
                        key,
                        "GET",
                        FetchResult(
                            url=robots_url,
                            final_url=raw.final_url,
                            status=status,
                            headers=raw.headers,
                            body=body,
                            fetched_at=self._now(),
                            from_cache=False,
                        ),
                    )
            parser = RobotFileParser(robots_url)
            if 200 <= status < 300:
                parser.parse(body.decode("utf-8", errors="replace").splitlines())
            elif 400 <= status < 500:
                parser.parse([])  # no robots.txt: everything allowed (RFC 9309)
            else:
                raise FetchError(
                    f"robots.txt unavailable (HTTP {status})",
                    source=self.policy.name,
                    host=host,
                    url=robots_url,
                )
            state.robots = parser
            state.robots_checked = self._clock()
            delay = parser.crawl_delay(agent)
            state.crawl_delay = float(delay) if delay else 0.0
            if fetched_live:  # that request was spaced before we knew the crawl-delay
                state.next_ok = max(state.next_ok, self._clock() + state.crawl_delay)
        if not state.robots.can_fetch(agent, url):
            raise RobotsDisallowed(
                "robots.txt disallows this URL", source=self.policy.name, host=host, url=url
            )

    def _clear_and_retry(
        self,
        state: _HostState,
        host: str,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str] | None,
        referer: str | None,
        challenged: RawResponse,
    ) -> RawResponse:
        where = {"source": self.policy.name, "host": host, "url": url}
        provider = self.clearance_provider
        if provider is None:
            raise ChallengeBlocked("bot challenge and no clearance provider configured", **where)
        log.warning("challenge host=%s status=%s url=%s; asking clearance provider", host, challenged.status, url)
        request = ClearanceRequest(
            source=self.policy.name,
            url=url,
            host=host,
            status=challenged.status,
            user_agent=state.user_agent or self.policy.user_agent,
        )
        try:
            clearance = provider(request)
        except NeedsHuman as exc:
            exc.source = exc.source or self.policy.name
            exc.host = exc.host or host
            exc.url = exc.url or url
            raise
        except Exception as exc:
            raise ChallengeBlocked(f"clearance provider failed: {exc}", **where) from exc
        if clearance.needs_human:
            raise NeedsHuman(clearance.message or "a person must solve the challenge", **where)
        if not clearance.cookies:
            raise ChallengeBlocked(clearance.message or "clearance provider returned no cookies", **where)

        self._transport.add_cookies(host, clearance.cookies)
        if clearance.user_agent:
            state.user_agent = clearance.user_agent
            self.cache.set_user_agent(host, clearance.user_agent)

        raw = self._send(state, host, method, url, body, headers, referer)
        if is_challenge(raw.status, raw.headers, raw.body):
            raise ChallengeBlocked("still challenged after clearance", **where)
        return raw
