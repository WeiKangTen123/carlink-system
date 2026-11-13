"""Shared fakes: an HTTP layer and a clock, so tests are offline and instant."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import pytest

from parts_mcp.core.fetch import FetchPolicy, PageCache, PoliteFetcher, RawResponse, TransportError


class Clock:
    """Fake monotonic clock + sleep + UTC now, all driven by one counter."""

    def __init__(self, start: datetime = datetime(2026, 11, 11, tzinfo=timezone.utc)) -> None:
        self.t = 1000.0
        self.start = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds

    def now(self) -> datetime:
        return self.start + timedelta(seconds=self.t)

    def advance(self, seconds: float) -> None:
        self.t += seconds


def resp(status: int = 200, body: str | bytes = "<html>ok</html>", **headers: str) -> RawResponse:
    data = body.encode() if isinstance(body, str) else body
    return RawResponse(
        status=status,
        headers={k.replace("_", "-").lower(): v for k, v in headers.items()},
        body=data,
        final_url="",
    )


Handler = RawResponse | list[RawResponse] | Exception | Callable[[Mapping[str, str]], RawResponse]


class FakeTransport:
    """Scripted responses per URL. A list is consumed in order (last one repeats)."""

    def __init__(self, routes: dict[str, Handler] | None = None) -> None:
        self.routes: dict[str, Handler] = dict(routes or {})
        self.requests: list[dict[str, Any]] = []
        self.cookies: dict[str, list[dict[str, Any]]] = {}

    def request(self, method: str, url: str, *, headers: Mapping[str, str], data: bytes | None,
                timeout: float) -> RawResponse:
        self.requests.append({"method": method, "url": url, "headers": dict(headers), "data": data})
        handler = self.routes.get(url)
        if handler is None:
            return resp(404, "not found")
        if isinstance(handler, list):
            handler = handler.pop(0) if len(handler) > 1 else handler[0]
        if isinstance(handler, Exception):
            raise handler
        if callable(handler):
            handler = handler(headers)
        return RawResponse(handler.status, handler.headers, handler.body, handler.final_url or url)

    def add_cookies(self, host: str, cookies: list[dict[str, Any]]) -> None:
        self.cookies.setdefault(host, []).extend(cookies)

    def close(self) -> None:
        pass

    def urls(self) -> list[str]:
        return [r["url"] for r in self.requests]


IMPERSONATE = FetchPolicy(name="shop", ua_mode="impersonate", min_delay=8, max_delay=12, daily_cap=300)
HONEST = FetchPolicy(name="microfiches", ua_mode="honest", user_agent="TestBot/1.0 (+test)",
                     min_delay=1, max_delay=1, daily_cap=500)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def cache() -> PageCache:
    c = PageCache(":memory:")
    yield c
    c.close()


@pytest.fixture
def make_fetcher(cache: PageCache, clock: Clock):
    def build(transport: FakeTransport, policy: FetchPolicy = IMPERSONATE, **kw: Any) -> PoliteFetcher:
        return PoliteFetcher(policy, kw.pop("page_cache", cache), transport=transport, sleep=clock.sleep,
                             clock=clock.monotonic, now=clock.now, **kw)

    return build


__all__ = ["Clock", "FakeTransport", "resp", "IMPERSONATE", "HONEST", "TransportError"]
