"""Challenge detection and the (generic, optional) clearance-provider hook, on a fake HTTP layer.
No source in the package uses a provider; these tests keep the hook honest."""

from __future__ import annotations

import pytest

from parts_mcp.core.errors import ChallengeBlocked, NeedsHuman
from parts_mcp.core.fetch import Clearance, ClearanceRequest, is_challenge

from .conftest import FakeTransport, resp

URL = "https://www.shop.example/en/catalog/vehicle?c=BMW&ssd=abc"
CF = resp(403, "<html><head><title>Just a moment...</title></head></html>", cf_mitigated="challenge")
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Browser/142.0"


def test_is_challenge():
    assert is_challenge(403, {"cf-mitigated": "challenge"}, b"")
    assert is_challenge(503, {"cf-mitigated": "Challenge"}, b"")
    assert is_challenge(200, {}, b"<html><head><title>Just a moment...</title>")
    assert is_challenge(403, {}, b"<TITLE> Just a moment</TITLE>")
    assert not is_challenge(403, {}, b"<title>Forbidden</title>")
    assert not is_challenge(200, {"cf-mitigated": "challenge"}, b"<title>Catalog</title>")
    assert not is_challenge(200, {}, b"<p>Just a moment please</p>")


def test_challenge_without_provider_is_blocked(make_fetcher):
    t = FakeTransport({URL: CF})
    with pytest.raises(ChallengeBlocked) as info:
        make_fetcher(t).get(URL)
    err = info.value.to_dict()
    assert err["error"] == "challenge_blocked" and err["source"] == "shop"
    assert err["host"] == "www.shop.example"


def test_provider_cookies_and_ua_then_retry_succeeds(make_fetcher, cache):
    seen: list[ClearanceRequest] = []

    def provider(req: ClearanceRequest) -> Clearance:
        seen.append(req)
        return Clearance(cookies=[{"name": "clearance", "value": "tok", "domain": ".shop.example"}],
                         user_agent=BROWSER_UA)

    def page(headers):
        return resp(200, "<title>Catalog</title>") if headers.get("User-Agent") == BROWSER_UA else CF

    t = FakeTransport({URL: page})
    result = make_fetcher(t, clearance_provider=provider).get(URL)
    assert result.ok and not result.from_cache
    assert len(seen) == 1 and seen[0].url == URL and seen[0].status == 403 and seen[0].source == "shop"
    assert t.cookies["www.shop.example"][0]["name"] == "clearance"
    assert t.requests[-1]["headers"]["User-Agent"] == BROWSER_UA
    # the UA is remembered for this host across fetcher instances
    t2 = FakeTransport({URL.replace("vehicle", "groups"): page})
    make_fetcher(t2).get(URL.replace("vehicle", "groups"))
    assert t2.requests[0]["headers"]["User-Agent"] == BROWSER_UA


def test_still_challenged_after_clearance_is_blocked_once(make_fetcher):
    calls = []

    def provider(req):
        calls.append(req)
        return Clearance(cookies=[{"name": "clearance", "value": "tok"}], user_agent=BROWSER_UA)

    t = FakeTransport({URL: CF})
    f = make_fetcher(t, clearance_provider=provider)
    with pytest.raises(ChallengeBlocked) as info:
        f.get(URL)
    assert not isinstance(info.value, NeedsHuman)
    assert len(calls) == 1  # retried once, no loop
    assert len(t.requests) == 2
    assert f.cached(URL) is None  # challenge pages are never cached
    assert t.cookies["www.shop.example"][0].get("domain") is None  # fetcher fills the domain itself


def test_provider_reports_needs_human(make_fetcher):
    t = FakeTransport({URL: CF})
    f = make_fetcher(t, clearance_provider=lambda req: Clearance(needs_human=True, message="captcha shown"))
    with pytest.raises(NeedsHuman) as info:
        f.get(URL)
    assert info.value.to_dict()["error"] == "needs_human"
    assert "captcha" in info.value.message
    assert len(t.requests) == 1  # no retry without clearance


def test_provider_raising_needs_human_gets_location(make_fetcher):
    def provider(req):
        raise NeedsHuman("turnstile needs a click")

    with pytest.raises(NeedsHuman) as info:
        make_fetcher(FakeTransport({URL: CF}), clearance_provider=provider).get(URL)
    assert info.value.source == "shop" and info.value.url == URL


def test_provider_crash_becomes_challenge_blocked(make_fetcher):
    def provider(req):
        raise RuntimeError("browser not found")

    with pytest.raises(ChallengeBlocked) as info:
        make_fetcher(FakeTransport({URL: CF}), clearance_provider=provider).get(URL)
    assert "browser not found" in info.value.message


def test_title_challenge_on_200_is_detected(make_fetcher):
    t = FakeTransport({URL: resp(200, "<html><title>Just a moment...</title></html>")})
    f = make_fetcher(t)
    with pytest.raises(ChallengeBlocked):
        f.get(URL)
    assert f.cached(URL) is None
