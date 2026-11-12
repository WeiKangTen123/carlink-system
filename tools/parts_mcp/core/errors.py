"""Error types shared by the fetcher, the sources and the service.

Every error has a short machine `code` and `to_dict()`, so the MCP server can
return it as a structured message instead of a stack trace.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class PartsError(Exception):
    """Base class for every error this package raises on purpose."""

    code = "error"

    def __init__(
        self,
        message: str = "",
        *,
        source: str | None = None,
        host: str | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.source = source
        self.host = host
        self.url = url

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": self.code, "message": self.message}
        for key in ("source", "host", "url"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out


# --- source / catalogue level -------------------------------------------------


class SourceError(PartsError):
    """A plug-in failed for a reason that is not a fetch problem."""

    code = "source_error"


class NotSupported(SourceError):
    """The source cannot do this (e.g. no VIN search, no prices)."""

    code = "not_supported"


class ChooseTrim(NotSupported):
    """The model has more trims (finish lines) for that year than the source
    opens in one call; the model text must name one. Same code and message as
    NotSupported, plus the trim labels so a caller can open some itself."""

    def __init__(
        self,
        message: str = "",
        *,
        brand: str,
        series: str,
        year: int,
        trims: list[str],
        cached: list[str] | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(message, **kw)
        self.brand = brand
        self.series = series  # the model as the source names it, e.g. "CIVIC"
        self.year = year
        self.trims = list(trims)  # labels in page order, e.g. ["1.6 ES", "1.8 EXI", ...]
        self.cached = list(cached or [])  # labels whose page is in the page cache (free to open)


class NotFound(SourceError):
    """The thing asked for does not exist (in this source or in the store)."""

    code = "not_found"


class ParseError(SourceError):
    """A page was fetched but did not have the expected layout."""

    code = "parse_error"


class UnknownSource(PartsError):
    code = "unknown_source"


class AllSourcesFailed(PartsError):
    """Every source tried for a request failed."""

    code = "all_sources_failed"

    def __init__(self, failures: list[dict[str, Any]], message: str = "") -> None:
        super().__init__(message or "every source failed")
        self.failures = failures

    def to_dict(self) -> dict[str, Any]:
        out = super().to_dict()
        out["failures"] = self.failures
        return out


# --- fetch level ----------------------------------------------------------------


class FetchError(PartsError):
    """Network / HTTP failure while fetching a page."""

    code = "fetch_failed"


class HttpStatusError(FetchError):
    code = "http_error"

    def __init__(self, status: int, message: str = "", **kw: Any) -> None:
        super().__init__(message or f"HTTP {status}", **kw)
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        out = super().to_dict()
        out["status"] = self.status
        return out


class RateLimited(FetchError):
    """The site answered 429. We back off instead of retrying."""

    code = "rate_limited"


class DailyCapReached(FetchError):
    """The per-host daily request cap is used up."""

    code = "daily_cap_reached"


class RobotsDisallowed(FetchError):
    """robots.txt forbids this URL for our honest User-Agent."""

    code = "robots_disallowed"


class ChallengeBlocked(FetchError):
    """A bot challenge (e.g. Cloudflare) could not be cleared automatically."""

    code = "challenge_blocked"


class NeedsHuman(ChallengeBlocked):
    """The clearance provider says a person must solve the challenge."""

    code = "needs_human"


class SiteBlocked(ChallengeBlocked):
    """The site itself blocked us (its own block page, not a bot check). A
    source raising it stops asking that site until `until`."""

    code = "site_blocked"

    def __init__(self, message: str = "", *, until: datetime | None = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.until = until

    def to_dict(self) -> dict[str, Any]:
        out = super().to_dict()
        if self.until is not None:
            out["until"] = self.until.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return out
