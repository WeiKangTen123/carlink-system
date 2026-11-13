"""PoliteFetcher: page cache + TTL, throttling, caps, robots, retries."""

from __future__ import annotations

import pytest

from parts_mcp.core.errors import DailyCapReached, FetchError, HttpStatusError, RateLimited, RobotsDisallowed
from parts_mcp.core.fetch import DAY, FetchPolicy, PageCache, TransportError

from .conftest import HONEST, FakeTransport, resp

URL = "https://www.shop.example/en/catalog/x"
URL2 = "https://www.shop.example/en/catalog/y"


def test_cache_hit_within_ttl_skips_network(make_fetcher, clock):
    t = FakeTransport({URL: resp(200, "<p>page</p>", content_type="text/html; charset=utf-8")})
    f = make_fetcher(t)
    first = f.get(URL, ttl=180 * DAY)
    clock.advance(3600)
    second = f.get(URL, ttl=180 * DAY)
    assert len(t.requests) == 1
    assert not first.from_cache and second.from_cache
    assert second.body == b"<p>page</p>" and second.text == "<p>page</p>"
    assert second.fetched_at == first.fetched_at  # original fetch time, not the hit time


def test_ttl_expiry_and_zero_ttl_refetch(make_fetcher, clock):
    t = FakeTransport({URL: resp()})
    f = make_fetcher(t)
    f.get(URL, ttl=7 * DAY)
    clock.advance(7 * DAY + 1)
    assert not f.get(URL, ttl=7 * DAY).from_cache  # expired for a price-style TTL
    assert f.get(URL, ttl=180 * DAY).from_cache  # still fine for a catalogue TTL
    assert not f.get(URL, ttl=0).from_cache  # ttl=0 forces live
    assert len(t.requests) == 3


def test_cache_persists_gzip_body_and_drops_cookie_headers(tmp_path, clock, make_fetcher):
    cache = PageCache(tmp_path / "pages.db")
    body = ("<html>" + "x" * 50_000 + "</html>").encode()
    t = FakeTransport({URL: resp(200, body, set_cookie="secret=1", x_test="yes")})
    make_fetcher(t, page_cache=cache).get(URL)
    cache.close()

    reopened = PageCache(tmp_path / "pages.db")
    hit = reopened.get(f"GET {URL}")
    assert hit is not None and hit.body == body and hit.status == 200
    assert "set-cookie" not in hit.headers and hit.headers["x-test"] == "yes"
    reopened.close()


def test_errors_raise_and_are_not_cached(make_fetcher):
    t = FakeTransport({URL: resp(404, "gone")})
    f = make_fetcher(t)
    with pytest.raises(HttpStatusError) as info:
        f.get(URL)
    assert info.value.status == 404 and info.value.to_dict()["source"] == "shop"
    assert f.get(URL, accept_status=(404,)).status == 404
    assert f.cached(URL) is None
    assert len(t.requests) == 2


def test_throttle_per_host(make_fetcher, clock):
    other = "https://other.example/a"
    t = FakeTransport({URL: resp(), URL2: resp(), other: resp()})
    f = make_fetcher(t)
    f.get(URL)
    f.get(other)  # different host: no wait
    assert clock.sleeps == []
    f.get(URL2)  # same host: waits 8-12 s
    assert len(clock.sleeps) == 1 and 8 <= clock.sleeps[0] <= 12


def test_daily_cap_per_host(make_fetcher, cache):
    policy = FetchPolicy(name="shop", ua_mode="impersonate", min_delay=0, max_delay=0, daily_cap=2)
    t = FakeTransport({URL: resp(), URL2: resp()})
    f = make_fetcher(t, policy)
    f.get(URL)
    f.get(URL2)
    with pytest.raises(DailyCapReached):
        f.get(URL, ttl=0)
    assert f.get(URL).from_cache  # cache hits are free
    assert f.requests_today("www.shop.example") == 2
    assert make_fetcher(t, policy).requests_today("www.shop.example") == 2  # persisted in the cache DB


def test_honest_mode_reads_robots_and_crawl_delay(make_fetcher, clock):
    robots = "User-agent: *\nDisallow: /private\nCrawl-delay: 7\n"
    base = "https://www.bmw-spare-parts.com"
    t = FakeTransport({
        f"{base}/robots.txt": resp(200, robots),
        f"{base}/ok": resp(),
        f"{base}/ok2": resp(),
    })
    f = make_fetcher(t, HONEST)
    with pytest.raises(RobotsDisallowed):
        f.get(f"{base}/private/x")
    f.get(f"{base}/ok")
    f.get(f"{base}/ok2")
    assert t.urls() == [f"{base}/robots.txt", f"{base}/ok", f"{base}/ok2"]
    assert all(s >= 7 for s in clock.sleeps)  # crawl-delay beats the 1 s policy
    assert t.requests[1]["headers"]["User-Agent"] == "TestBot/1.0 (+test)"


def test_missing_robots_allows_all(make_fetcher):
    base = "https://parts-honda.uk"
    t = FakeTransport({f"{base}/robots.txt": resp(404), f"{base}/a": resp()})
    assert make_fetcher(t, HONEST).get(f"{base}/a").ok


def test_retry_once_on_503_then_ok(make_fetcher):
    t = FakeTransport({URL: [resp(503, "busy"), resp(200, "ok")]})
    assert make_fetcher(t).get(URL).body == b"ok"
    assert len(t.requests) == 2


def test_429_backs_off_without_retry(make_fetcher):
    t = FakeTransport({URL: resp(429, "slow down", retry_after="30")})
    with pytest.raises(RateLimited):
        make_fetcher(t).get(URL)
    assert len(t.requests) == 1


def test_transport_error_retried_then_raised(make_fetcher):
    t = FakeTransport({URL: TransportError("timeout")})
    with pytest.raises(FetchError) as info:
        make_fetcher(t).get(URL)
    assert info.value.code == "fetch_failed"
    assert len(t.requests) == 2  # one retry


def test_post_cached_by_body(make_fetcher):
    t = FakeTransport({URL: resp(200, "result")})
    f = make_fetcher(t)
    f.post(URL, {"vin": "A"}, ttl=DAY)
    f.post(URL, {"vin": "A"}, ttl=DAY)
    f.post(URL, {"vin": "B"}, ttl=DAY)
    assert len(t.requests) == 2
    assert t.requests[0]["data"] == b"vin=A"
    assert t.requests[0]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
