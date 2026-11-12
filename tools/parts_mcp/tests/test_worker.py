"""Catalogue worker: Job -> Result in the contract's shapes, the server loop, error codes,
caps and the dry run. Offline: FakeSource-based catalogues and a fake server on
httpx's MockTransport."""

from __future__ import annotations

import json
import logging
import re
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from parts_mcp import worker as w
from parts_mcp.core.errors import (ChallengeBlocked, ChooseTrim, DailyCapReached, NeedsHuman, NotSupported,
                                   SiteBlocked)
from parts_mcp.core.models import Vehicle
from parts_mcp.core.store import open_store
from parts_mcp.service import CatalogueService, DiagramMatch, DiagramSearch
from parts_mcp.sources import fake
from parts_mcp.sources.fake import FakeSource

httpx = w.httpx
TOKEN = "tok-SECRET-0123456789abcdef"
API = "https://carlink.test/api"

RESULT_KEYS = {"status", "error_code", "error_text", "vehicle", "candidates", "parts", "failures"}
VEHICLE_KEYS = {"key", "source", "brand", "model", "name", "market", "year_from", "year_to", "url", "twin_of"}
PART_KEYS = {"source_part", "query", "side", "diagrams", "failures"}
DIAGRAM_KEYS = {"diagram_key", "diagram_name", "diagram_url", "image_url", "match_score", "rows"}
ROW_KEYS = {"callout", "part_number", "superseded_from", "name", "name_original", "qty", "side", "notes",
            "ref_amount", "ref_currency", "ref_tax_note", "source", "url", "fetched_at", "stale"}
FAILURE_KEYS = {"source", "code", "message"}


@pytest.fixture(autouse=True)
def catalogue_host(monkeypatch):
    """Fake records on a host the contract allows, so URLs survive the caps."""
    monkeypatch.setattr(fake, "BASE", "https://www.bmw-spare-parts.com/fake")


# --- catalogues ---------------------------------------------------------------------------


class ShopSource(FakeSource):
    """FakeSource whose first row of every diagram shows a price, like Microfiches."""

    def get_diagram(self, vehicle, diagram):
        full, rows = super().get_diagram(vehicle, diagram)
        rows[0].attributes.update(price="312.40", currency="EUR", price_note="Unit Price Excl Tax")
        return full, rows


class CarsSource(FakeSource):
    """find_vehicles answers from a table: normalized model text -> car specs or an exception.
    Groups, diagrams and rows come from FakeSource."""

    def __init__(self, table: dict[str, Any], brands=("HONDA",), **kw: Any) -> None:
        super().__init__(brands=brands, **kw)
        self.table = table

    def asked(self) -> list[str]:
        return [args[1] for method, args in self.calls if method == "find_vehicles"]

    def find_vehicles(self, brand, model=None, year=None):
        at = self._enter("find_vehicles", (brand, model, year))
        hit = self.table.get(w._norm(model))
        if isinstance(hit, BaseException):
            raise hit
        return [self._car(at, brand, **spec) for spec in hit or []]

    def _car(self, at, brand, id, model="CIVIC", market=None, year_from=None, year_to=None, steering=None):
        return Vehicle(source=self.name, url=f"https://www.parts-honda.uk/honda-cars/{model}/{id}", fetched_at=at,
                       id=id, brand=brand, model=model, name=f"{model} type {id}", market=market,
                       year_from=year_from, year_to=year_to, attributes={"steering": steering} if steering else {})


def make_service(clock, *sources) -> CatalogueService:
    return CatalogueService(open_store(":memory:"), list(sources), routes={}, default_route=(sources[0].name,),
                            now=clock.now)


def processor(svc, **kw) -> w.JobProcessor:
    kw.setdefault("routes", {"BMW": ("fake",), "HONDA": ("fake",)})
    kw.setdefault("aliases", {"fake": {"HONDA": {"VEZEL": ("HR-V",)}}})
    return w.JobProcessor(svc, **kw)


def make_job(**kw: Any) -> dict[str, Any]:
    job = {
        "job_id": 12, "kind": "lookup", "brand": "BMW", "model": "3 Series", "model_key": "3", "year": 2016,
        "vehicle_key": None,
        "parts": [
            {"source_part": "Rear Bumper", "query": "rear bumper", "side": None},
            {"source_part": "Rear Lamp (Left)", "query": "rear light", "side": "left"},
        ],
    }
    job.update(kw)
    return job


def honda_job(model="Civic", model_key="Civic", year=2008, **kw: Any) -> dict[str, Any]:
    return make_job(brand="Honda", model=model, model_key=model_key, year=year,
                    parts=[{"source_part": "Rear Bumper", "query": "rear bumper", "side": None}], **kw)


# --- the fake server -------------------------------------------------------------------------


class FakeServer:
    """The three worker endpoints, scripted."""

    def __init__(self, jobs=(), *, poll=20, cancel_after=None, claim_errors=0, claim_status=200,
                 result_status=(200,), progress_status=200) -> None:
        self.jobs = list(jobs)
        self.poll = poll
        self.cancel_after = cancel_after
        self.claim_errors = claim_errors
        self.claim_status = claim_status
        self.result_status = list(result_status)
        self.progress_status = progress_status
        self.requests: list[Any] = []
        self.claims = 0
        self.progress: list[dict[str, Any]] = []
        self.results: list[dict[str, Any]] = []

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        body = json.loads(request.content or b"{}")
        if path.endswith("/catalogue-worker/claim"):  # base .../api (public) or http://api:8000 (compose)
            self.claims += 1
            if self.claim_errors:
                self.claim_errors -= 1
                raise httpx.ConnectError("connection refused", request=request)
            if self.claim_status != 200:
                return httpx.Response(self.claim_status, json={"detail": "nope"})
            assert body == {"worker": "test-pc", "version": "0.1"}
            return httpx.Response(200, json={"job": self.jobs.pop(0) if self.jobs else None,
                                             "poll_seconds": self.poll})
        if path.endswith("/progress"):
            self.progress.append(body)
            if self.progress_status != 200:
                return httpx.Response(self.progress_status)
            cancelled = self.cancel_after is not None and len(self.progress) > self.cancel_after
            return httpx.Response(200, json={"cancelled": cancelled})
        if path.endswith("/result"):
            self.results.append(body)
            status = self.result_status.pop(0) if len(self.result_status) > 1 else self.result_status[0]
            if status == 200:
                return httpx.Response(200, json={"ok": True, "suggestions": 3})
            return httpx.Response(status, json={"detail": "not accepted"})
        return httpx.Response(404)


def make_worker(server: FakeServer, proc: w.JobProcessor, sleeps: list[float], **kw: Any) -> w.Worker:
    client = w.ServerClient(API, TOKEN, worker="test-pc", transport=httpx.MockTransport(server))
    return w.Worker(client, proc, sleep=sleeps.append, **kw)


# --- the loop ----------------------------------------------------------------------------------


def test_no_job_sleeps_poll_seconds_and_sends_the_token(clock):
    server = FakeServer(poll=7)
    sleeps: list[float] = []
    make_worker(server, processor(make_service(clock, FakeSource(now=clock.now))), sleeps).run(
        stop=lambda: server.claims >= 3)
    assert server.claims == 3 and sleeps == [7, 7, 7]
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in server.requests)
    assert str(server.requests[0].url) == "https://carlink.test/api/catalogue-worker/claim"


def test_full_job_posts_progress_then_a_contract_result(clock):
    src = ShopSource(now=clock.now)
    server = FakeServer([make_job()])
    sleeps: list[float] = []
    make_worker(server, processor(make_service(clock, src)), sleeps).run(stop=lambda: server.claims >= 2)
    assert [(p["done_parts"], p["total_parts"]) for p in server.progress] == [(0, 2), (1, 2), (2, 2)]
    assert server.progress[0]["message"].startswith("Car: 3 Series")
    assert server.progress[1]["message"] == "Rear Bumper: Trim panel, rear bumper"
    assert [r.url.path for r in server.requests if "result" in r.url.path] == [
        "/api/catalogue-worker/jobs/12/result"]
    result = server.results[0]
    assert set(result) == RESULT_KEYS
    assert (result["status"], result["error_code"], result["error_text"]) == ("done", None, None)
    assert set(result["vehicle"]) == VEHICLE_KEYS
    assert result["vehicle"]["key"] == "fake:F30-320i-sedan"
    assert (result["vehicle"]["brand"], result["vehicle"]["twin_of"]) == ("BMW", None)
    assert (result["vehicle"]["year_from"], result["vehicle"]["year_to"]) == (2012, 2019)
    assert [c["key"] for c in result["candidates"]] == ["fake:F30-320i-sedan"]
    assert [set(p) for p in result["parts"]] == [PART_KEYS, PART_KEYS]
    bumper, lamp = result["parts"]
    assert (bumper["source_part"], bumper["query"], bumper["side"]) == ("Rear Bumper", "rear bumper", None)
    d = bumper["diagrams"][0]
    assert set(d) == DIAGRAM_KEYS and d["diagram_key"] == "fake:F30-320i-sedan:51_0010"
    assert (d["diagram_name"], d["match_score"]) == ("Trim panel, rear bumper", 1.0)
    assert d["image_url"] == "https://www.bmw-spare-parts.com/fake/img/51_0010.png"
    row = d["rows"][0]
    assert set(row) == ROW_KEYS
    assert (row["callout"], row["part_number"], row["name"]) == ("1", "00 00 0 000 101", "Trim panel, bumper, primed, rear")
    assert (row["ref_amount"], row["ref_currency"], row["ref_tax_note"]) == (312.40, "EUR", "Unit Price Excl Tax")
    assert row["fetched_at"].endswith("+00:00") and row["stale"] is False and row["source"] == "fake"
    assert d["rows"][1]["ref_amount"] is None and d["rows"][1]["ref_currency"] is None  # unknown stays null
    assert lamp["diagrams"][0]["diagram_name"] == "Rear light" and lamp["side"] == "left"
    assert all(set(f) == FAILURE_KEYS for p in result["parts"] for f in p["failures"])
    assert sleeps == [20]  # the job, then one empty claim


def test_side_filter_drops_rows_of_the_other_side_only(clock):
    proc = processor(make_service(clock, FakeSource(now=clock.now)))
    parts = [{"source_part": "Rear Bumper (Right)", "query": "rear bumper", "side": "right"},
             {"source_part": "Rear Bumper", "query": "rear bumper", "side": None},
             {"source_part": "Rear Bumper (Left)", "query": "rear bumper", "side": "left"}]
    result = proc.process(make_job(parts=parts))
    sides = [[r["side"] for r in p["diagrams"][0]["rows"]] for p in result["parts"]]
    assert sides == [[None, "right"], [None, "left", "right"], [None, "left"]]


def test_score_threshold_and_at_most_three_diagrams(clock, monkeypatch):
    svc = make_service(clock, FakeSource(now=clock.now))
    v = svc.find_vehicles("BMW", "3", 2016, sources=["fake"]).value[0]
    diagrams = [d for g in svc.list_groups(v).value for d in svc.list_diagrams(v, g).value]
    asked: list[dict[str, Any]] = []
    scores = {"first": [1.0, 0.9, 0.8, 0.6, 0.5, 0.45], "second": [0.49, 0.3]}

    def fake_find(vehicle, query, **kw):
        asked.append(kw)
        matches = [DiagramMatch(d, None, s, "test") for d, s in zip(diagrams, scores[query])]
        return DiagramSearch(matches[: kw["limit"]], [], [], "fake")

    monkeypatch.setattr(svc, "find_diagrams", fake_find)
    parts = [{"source_part": "A", "query": "first", "side": None}, {"source_part": "B", "query": "second", "side": None}]
    result = processor(svc).process(make_job(parts=parts))
    assert asked == [{"max_new_groups": 3, "limit": 5}] * 2
    assert [d["match_score"] for d in result["parts"][0]["diagrams"]] == [1.0, 0.9, 0.8]
    assert result["parts"][1]["diagrams"] == [] and result["parts"][1]["failures"] == []


# --- choosing the car -------------------------------------------------------------------------


def test_alias_gives_the_market_twin(clock):
    hrv = [{"id": "30584", "model": "HRV", "market": "Germany, France"}]
    src = CarsSource({"HRVHYBRID": hrv, "HRV": hrv}, now=clock.now)
    result = processor(make_service(clock, src)).process(honda_job("Vezel Hybrid", "Vezel", 2016))
    assert src.asked() == ["Vezel", "Vezel Hybrid", "HR-V Hybrid"]
    assert result["status"] == "done"
    assert (result["vehicle"]["model"], result["vehicle"]["twin_of"], result["vehicle"]["brand"]) == (
        "HRV", "Vezel", "Honda")
    assert result["candidates"][0]["twin_of"] == "Vezel"


def test_own_model_name_wins_over_the_alias(clock):
    src = CarsSource({"VEZEL": [{"id": "RU1", "model": "VEZEL", "market": "Japan"}],
                      "HRV": [{"id": "30584", "model": "HRV"}]}, now=clock.now)
    result = processor(make_service(clock, src)).process(honda_job("Vezel Hybrid", "Vezel", 2016))
    assert src.asked() == ["Vezel"]
    assert (result["vehicle"]["model"], result["vehicle"]["twin_of"]) == ("VEZEL", None)


def test_pick_by_year_then_singapore_then_asia_general_then_rhd(clock):
    cars = [
        {"id": "A", "market": "Europe", "year_from": 2010, "year_to": 2015},
        {"id": "E", "market": "Japan", "year_from": 2016, "year_to": 2020, "steering": "RHD"},
        {"id": "C", "market": "General", "year_from": 2016, "year_to": 2020},
        {"id": "F", "market": "Singapore", "year_from": 2016, "year_to": 2020, "steering": "LHD"},
        {"id": "B", "market": "Thailand, Singapore, Zambia", "year_from": 2016, "year_to": 2020, "steering": "RHD"},
        {"id": "D", "market": "Singapore", "year_from": 2021, "year_to": None},
    ]
    src = CarsSource({"CIVIC": cars}, now=clock.now)
    result = processor(make_service(clock, src)).process(honda_job(year=2017))
    assert result["vehicle"]["key"] == "fake:B"
    assert [c["key"] for c in result["candidates"]] == ["fake:B", "fake:F", "fake:C", "fake:E"]
    no_year = processor(make_service(clock, CarsSource({"CIVIC": cars}, now=clock.now))).process(honda_job(year=None))
    assert len(no_year["candidates"]) == 6 and no_year["vehicle"]["key"] == "fake:B"


def _trims(cached=("1.6 S", "1.8 EXI")) -> ChooseTrim:
    return ChooseTrim("HONDA CIVIC 2008 has 5 matching trims", source="fake", brand="HONDA", series="CIVIC",
                      year=2008, trims=["1.4 S", "1.6 ES", "1.6 S", "1.8 EXI", "2.0 TYPE R"], cached=list(cached))


def _trim_table() -> dict[str, Any]:
    return {"CIVIC": _trims(), "CIVIC14S": [{"id": "14S", "market": "Germany"}],
            "CIVIC16ES": [{"id": "16ES", "market": "Turkey"}], "CIVIC16S": [{"id": "16S", "market": "United Kingdom"}],
            "CIVIC18EXI": [{"id": "18EXI", "market": "Thailand, Singapore"}], "CIVIC20TYPER": [{"id": "TR"}]}


def test_many_trims_open_cached_pages_first_and_stop_at_singapore(clock):
    src = CarsSource(_trim_table(), now=clock.now)
    result = processor(make_service(clock, src)).process(honda_job())
    assert src.asked() == ["Civic", "CIVIC 1.6 S", "CIVIC 1.8 EXI"]
    assert result["vehicle"]["key"] == "fake:18EXI"
    assert [c["key"] for c in result["candidates"]] == ["fake:18EXI", "fake:16S"]
    note = result["failures"][0]
    assert note["code"] == "not_supported" and "opened 2 (1.6 S, 1.8 EXI)" in note["message"]


def test_trim_budget_and_a_named_trim(clock):
    src = CarsSource(_trim_table(), now=clock.now)
    capped = processor(make_service(clock, src), max_trim_pages=1).process(honda_job())
    assert capped["vehicle"]["key"] == "fake:16S" and src.asked()[-1] == "CIVIC 1.6 S"
    named = CarsSource(_trim_table(), now=clock.now)
    result = processor(make_service(clock, named)).process(honda_job(model="Civic 1.8 EXi"))
    assert named.asked() == ["Civic", "Civic 1.8 EXi"]  # the full model text names the trim
    assert result["vehicle"]["key"] == "fake:18EXI" and result["failures"] == []


def test_vehicle_key_pins_the_car(clock):
    cars = [{"id": "B", "market": "Singapore"}, {"id": "C", "market": "General"}, {"id": "E", "market": "Japan"}]
    src = CarsSource({"CIVIC": cars}, now=clock.now)
    proc = processor(make_service(clock, src))
    assert proc.process(honda_job())["vehicle"]["key"] == "fake:B"
    pinned = proc.process(honda_job(vehicle_key="fake:E"))
    assert pinned["status"] == "done" and pinned["vehicle"]["key"] == "fake:E"
    assert [c["key"] for c in pinned["candidates"]] == ["fake:E", "fake:B", "fake:C"]
    assert pinned["parts"][0]["diagrams"][0]["diagram_key"] == "fake:E:51_0010"
    unknown = proc.process(honda_job(vehicle_key="fake:NOPE"))
    assert (unknown["status"], unknown["error_code"], unknown["vehicle"]) == ("failed", "no_vehicle", None)


def test_pinned_twin_keeps_twin_of(clock):
    src = CarsSource({"HRV": [{"id": "30584", "model": "HRV"}]}, now=clock.now)
    proc = processor(make_service(clock, src))
    proc.process(honda_job("Vezel", "Vezel", 2016))
    src.table = {}  # the search finds nothing now; the pinned car comes from the store
    pinned = proc.process(honda_job("Vezel", "Vezel", 2016, vehicle_key="fake:30584"))
    assert (pinned["vehicle"]["key"], pinned["vehicle"]["twin_of"]) == ("fake:30584", "Vezel")


# --- cancel, errors, caps ---------------------------------------------------------------------------


def test_cancelled_job_stops_and_posts_no_result(clock):
    src = FakeSource(now=clock.now)
    server = FakeServer([make_job()], cancel_after=1)  # the car progress is fine, the first part's says cancelled
    worker = make_worker(server, processor(make_service(clock, src)), [])
    assert worker.handle(server.jobs.pop(0)) == "cancelled"
    assert len(server.progress) == 2 and server.results == []
    assert src.calls_to("get_diagram") == 1  # the second part was never looked up


@pytest.mark.parametrize("exc, code", [
    (NotSupported("the site needs a model name"), "not_supported"),
    (NeedsHuman("Cloudflare asked for a person"), "needs_human"),
    (ChallengeBlocked("challenged"), "challenge_blocked"),
    (SiteBlocked("the site blocked this worker"), "site_blocked"),
    (DailyCapReached("cap of 500 reached"), "daily_cap_reached"),
    (RuntimeError("boom"), "internal"),
])
def test_vehicle_errors_map_to_contract_codes(clock, exc, code):
    src = FakeSource(now=clock.now, fail_with=exc, fail_methods={"find_vehicles"})
    result = processor(make_service(clock, src)).process(make_job())
    assert (result["status"], result["error_code"], result["vehicle"]) == ("failed", code, None)
    assert result["parts"] == [] and result["candidates"] == []
    assert result["error_text"] and "Traceback" not in result["error_text"] and len(result["error_text"]) <= 300
    assert result["failures"][0]["code"] == code
    if code == "internal":
        assert result["error_text"] == "source_error: RuntimeError: boom"


def test_no_car_is_no_vehicle_and_no_make_is_not_supported(clock):
    proc = processor(make_service(clock, FakeSource(now=clock.now)))
    none = proc.process(make_job(model="Z4 Roadster", model_key="Z4"))
    assert (none["status"], none["error_code"]) == ("failed", "no_vehicle")
    assert "BMW Z4 Roadster 2016" in none["error_text"]
    blank = proc.process(make_job(brand="", model=""))
    assert blank["error_code"] == "not_supported"
    unknown_brand = proc.process(make_job(brand="Perodua", model="Myvi", model_key="Myvi"))
    assert unknown_brand["error_code"] == "not_supported"


def test_part_level_block_stops_the_source_but_the_job_is_done(clock):
    src = FakeSource(now=clock.now, fail_with=NeedsHuman("Cloudflare asked for a person", source="fake"),
                     fail_methods={"get_diagram"})
    result = processor(make_service(clock, src)).process(make_job())
    assert result["status"] == "done" and result["vehicle"]["key"] == "fake:F30-320i-sedan"
    first, second = result["parts"]
    assert first["diagrams"] == [] and first["failures"][0]["code"] == "needs_human"
    assert second["failures"][0]["code"] == "needs_human" and "not tried" in second["failures"][0]["message"]
    assert src.calls_to("get_diagram") == 1


def test_internal_part_error_is_short(clock):
    src = FakeSource(now=clock.now, fail_with=ValueError("bad page\nline two"), fail_methods={"get_diagram"})
    result = processor(make_service(clock, src)).process(make_job())
    f = result["parts"][0]["failures"][0]
    assert f["code"] == "internal" and "\n" not in f["message"] and f["source"] == "fake"
    assert result["parts"][1]["failures"][0]["code"] == "internal"  # not a stop code: the next part is tried


def test_caps_are_enforced():
    row = {k: None for k in ROW_KEYS} | {"name": "x" * 500, "url": "https://www.parts-honda.uk/p"}
    diagram = {"diagram_key": "k" * 401, "diagram_name": "D", "diagram_url": "http://www.parts-honda.uk/d",
               "image_url": "https://evil.example/i.png", "match_score": 1.0, "rows": [row] * 300}
    part = {"source_part": "P", "query": "p", "side": None, "diagrams": [diagram] * 5,
            "failures": [{"source": "s", "code": "internal", "message": "m"}] * 60}
    vehicle = {k: None for k in VEHICLE_KEYS} | {"key": "fake:1", "url": "https://parts-honda.uk/" + "a" * 400,
                                                 "market": "M" * 450}
    capped = w.apply_caps({"status": "done", "error_code": None, "error_text": None, "vehicle": vehicle,
                           "candidates": [vehicle] * 12, "parts": [part] * 45,
                           "failures": [{"source": "s", "code": "internal", "message": "m"}] * 60})
    assert len(capped["parts"]) == 40 and len(capped["candidates"]) == 10
    # The server's own failure caps: 40 for the result, 20 per part (more is a 422).
    assert len(capped["failures"]) == 40
    p = capped["parts"][0]
    assert len(p["diagrams"]) == 3 and len(p["diagrams"][0]["rows"]) == 250 and len(p["failures"]) == 20
    d = p["diagrams"][0]
    assert (d["diagram_key"], d["diagram_url"], d["image_url"]) == (None, None, None)
    assert len(d["rows"][0]["name"]) == 400 and d["rows"][0]["url"] == "https://www.parts-honda.uk/p"
    assert capped["vehicle"]["url"] is None and len(capped["vehicle"]["market"]) == 400
    assert w.safe_url("https://parts-honda.uk/x") and w.safe_url("https://www.mini-spare-parts.com/x")
    assert w.safe_url("https://fake.example/x") is None and w.safe_url("https://parts-honda.uk.evil.io/x") is None
    assert w.ALLOWED_HOSTS == {f"{www}{site}" for www in ("", "www.")
                               for site in ("bmw-spare-parts.com", "mini-spare-parts.com", "parts-honda.uk")}


# The server's side of the same cases: apps/bot-service/scripts/test_catalogue_accept.py.
LINKS = json.loads((Path(__file__).parent / "fixtures" / "catalogue_links.json").read_text(encoding="utf-8"))


def _link(case: dict) -> str:
    url = case["url"]
    return url + "a" * (case["pad_to"] - len(url)) if case.get("pad_to") else url


@pytest.mark.parametrize("case", LINKS["links"], ids=lambda c: _link(c)[:48])
def test_links_null_exactly_what_the_server_refuses(case):
    """A link the server would refuse (422 for the whole result) is nulled here, and
    one it takes is kept: the shared cases in tests/fixtures/catalogue_links.json."""
    kept = w.safe_url(_link(case))
    assert (kept is not None) == case["ok"]
    if kept is not None:
        assert kept == _link(case).strip()


def test_caps_match_the_servers():
    caps = LINKS["caps"]
    assert (w.MAX_PARTS, w.MAX_DIAGRAMS, w.MAX_ROWS, w.MAX_CANDIDATES) == (
        caps["parts"], caps["diagrams"], caps["rows"], caps["candidates"])
    assert (w.MAX_FAILURES, w.MAX_PART_FAILURES, w.MAX_STR) == (caps["failures"], caps["part_failures"], caps["text"])
    assert w.YEAR_RANGE == (caps["year_from"], caps["year_to"]) and w.MAX_REF_AMOUNT == caps["ref_amount_max"]


def test_numbers_out_of_the_servers_bounds_are_dropped():
    assert w.in_range(2016, *w.YEAR_RANGE) == 2016 and w.in_range(9999, *w.YEAR_RANGE) is None
    assert w.in_range(0, *w.YEAR_RANGE) is None and w.in_range(None, *w.YEAR_RANGE) is None
    assert w.in_range(-5.0, 0, w.MAX_REF_AMOUNT) is None and w.in_range(float("inf"), 0, w.MAX_REF_AMOUNT) is None
    assert w.in_range(309.41, 0, w.MAX_REF_AMOUNT) == 309.41 and w.in_range(True, 0, 10) is None
    car = Vehicle(source="fake", url="https://www.parts-honda.uk/c/1", fetched_at=datetime(2026, 11, 12, tzinfo=timezone.utc), id="1", brand="HONDA",
                  model="HRV", name="HRV", market=None, year_from=1015, year_to=2019)
    out = w.vehicle_out(car, None)
    assert (out["year_from"], out["year_to"]) == (None, 2019)


def test_job_with_more_than_forty_parts(clock):
    parts = [{"source_part": f"Part {i}", "query": "rear bumper", "side": None} for i in range(45)]
    result = processor(make_service(clock, FakeSource(now=clock.now))).process(make_job(parts=parts))
    assert len(result["parts"]) == 40


# --- server trouble ----------------------------------------------------------------------------------


def test_server_outage_backs_off_30s_doubling_to_5_min(clock):
    server = FakeServer(claim_errors=7)
    sleeps: list[float] = []
    make_worker(server, processor(make_service(clock, FakeSource(now=clock.now))), sleeps).run(
        stop=lambda: server.claims >= 9)
    assert sleeps == [30, 60, 120, 240, 300, 300, 300, 20, 20]


def test_401_is_retried_like_an_outage(clock):
    server = FakeServer(claim_status=401)
    sleeps: list[float] = []
    make_worker(server, processor(make_service(clock, FakeSource(now=clock.now))), sleeps).run(
        stop=lambda: server.claims >= 2)
    assert sleeps == [30, 60]


@pytest.mark.parametrize("statuses, outcome, sleeps", [
    ((200,), "posted", []),
    ((503, 200), "posted", [30]),
    ((409,), "dropped", []),
    ((422,), "rejected", []),
    ((500,), "lost", [30, 60]),
])
def test_result_delivery(clock, statuses, outcome, sleeps):
    server = FakeServer([make_job()], result_status=statuses)
    slept: list[float] = []
    worker = make_worker(server, processor(make_service(clock, FakeSource(now=clock.now))), slept,
                         result_attempts=3)
    assert worker.handle(server.jobs.pop(0)) == outcome
    assert slept == sleeps


def test_progress_trouble_does_not_stop_the_job(clock):
    server = FakeServer([make_job()], progress_status=502)
    worker = make_worker(server, processor(make_service(clock, FakeSource(now=clock.now))), [])
    assert worker.handle(server.jobs.pop(0)) == "posted"
    gone = FakeServer([make_job()], progress_status=409)  # the job is no longer ours
    assert make_worker(gone, processor(make_service(clock, FakeSource(now=clock.now))), []).handle(
        gone.jobs.pop(0)) == "cancelled"


def test_token_is_never_logged(clock, caplog):
    caplog.set_level(logging.DEBUG)
    server = FakeServer([make_job()], claim_errors=1, result_status=(500, 200))
    worker = make_worker(server, processor(make_service(clock, FakeSource(now=clock.now))), [])
    worker.run(stop=lambda: server.claims >= 3)
    assert server.results and "server not reachable" in caplog.text
    assert TOKEN not in caplog.text
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "auth is %s", (f"Bearer {TOKEN}",), None)
    w.RedactFilter(TOKEN).filter(record)
    assert record.getMessage() == "auth is Bearer [token]"


def test_base_url_must_be_https():
    with pytest.raises(ValueError, match="https"):
        w.ServerClient("http://carlink.test/api", TOKEN, worker="x")
    with pytest.raises(ValueError, match="TOKEN"):
        w.ServerClient(API, "", worker="x")
    assert w.check_base_url("http://localhost:8080/api/") == "http://localhost:8080/api"


@pytest.mark.parametrize("base", ["http://127.0.0.1:8765", "http://localhost:8765/api", "http://127.0.0.1/api",
                                  "http://api:8000", "http://api:8000/", "http://API:8000"])
def test_plain_http_only_to_this_machine_or_the_api_container(base):
    """A local test server (apps/bot-service/scripts/e2e_catalogue.py) and the API container
    inside the compose network ("api") may be http; the token never goes in the clear
    anywhere else."""
    assert w.check_base_url(base) == base.rstrip("/")


@pytest.mark.parametrize("base", ["http://localhost.example.com/api", "http://127.0.0.1.nip.io/api",
                                  "http://10.0.0.5:8080/api", "http://carlink.34-16-92-98.sslip.io/api",
                                  "http://api.example.com:8000", "http://api.:8000", "http://apiserver:8000",
                                  "http://carlink_api:8000", "ftp://127.0.0.1/api", "ftp://api/x", "127.0.0.1:8080",
                                  "api:8000", "http://[::1/api"])
def test_plain_http_elsewhere_is_refused(base):
    with pytest.raises(ValueError, match="https"):
        w.check_base_url(base)


def test_the_default_api_base_is_the_api_container():
    assert w.DEFAULT_API_BASE == "http://api:8000" and w.check_base_url(w.DEFAULT_API_BASE) == w.DEFAULT_API_BASE


def test_once_handles_one_job_then_stops(clock):
    server = FakeServer([make_job(), make_job()])
    sleeps: list[float] = []
    handled = make_worker(server, processor(make_service(clock, FakeSource(now=clock.now))), sleeps).run(once=True)
    assert handled == 1 and server.claims == 1 and len(server.results) == 1 and sleeps == []


def test_once_with_no_job_or_no_server_stops_at_once(clock):
    proc = processor(make_service(clock, FakeSource(now=clock.now)))
    server = FakeServer()
    sleeps: list[float] = []
    assert make_worker(server, proc, sleeps).run(once=True) == 0 and server.claims == 1 and sleeps == []
    down = FakeServer(claim_errors=5)
    assert make_worker(down, proc, sleeps).run(once=True) == 0 and down.claims == 1 and sleeps == []


def test_once_on_the_command_line(tmp_path):
    server = FakeServer([make_job()])
    env = {"CATALOGUE_WORKER_TOKEN": TOKEN, "WORKER_NAME": "test-pc", "CARLINK_API_BASE": API}
    code = w.main(["--once", "--sources", "fake", "--data-dir", str(tmp_path / "data")], env=env,
                  transport=httpx.MockTransport(server), sleep=lambda s: None)
    assert code == 0 and server.claims == 1 and len(server.results) == 1


# --- the command line --------------------------------------------------------------------------------


def test_dry_run_writes_the_result(tmp_path):
    job_file, out = tmp_path / "job.json", tmp_path / "result.json"
    job_file.write_text(json.dumps(make_job()), encoding="utf-8")
    code = w.main(["--job", str(job_file), "--out", str(out), "--sources", "fake",
                   "--data-dir", str(tmp_path / "data")], env={})
    assert code == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert set(result) == RESULT_KEYS and result["status"] == "done"
    assert result["vehicle"]["key"] == "fake:F30-320i-sedan"  # BMW's microfiches is not loaded: fake serves BMW
    assert [p["diagrams"][0]["diagram_name"] for p in result["parts"]] == ["Trim panel, rear bumper", "Rear light"]
    assert (tmp_path / "data" / "logs" / "worker.log").exists()


def test_loop_without_token_refuses_to_start(tmp_path):
    assert w.main(["--sources", "fake", "--data-dir", str(tmp_path / "data")], env={}) == 2


def test_ctrl_c_exits_cleanly_and_the_log_has_no_token(tmp_path):
    server = FakeServer(claim_status=401)

    def sleep(seconds: float) -> None:
        raise KeyboardInterrupt

    env = {"CATALOGUE_WORKER_TOKEN": TOKEN, "CARLINK_API_BASE": API, "WORKER_NAME": "test-pc"}
    code = w.main(["--sources", "fake", "--data-dir", str(tmp_path / "data")], env=env,
                  transport=httpx.MockTransport(server), sleep=sleep)
    assert code == 0 and server.claims == 1
    text = (tmp_path / "data" / "logs" / "worker.log").read_text(encoding="utf-8")
    assert "token rejected" in text and "stopped (Ctrl+C)" in text and TOKEN not in text


# --- running in a container ----------------------------------------------------------------------------


def test_feature_off_is_logged_once_then_polled_every_5_min(clock, caplog):
    """404 on claim = the look-up is switched off on the server: one INFO line, then a quiet
    claim every 5 minutes; one more line when it is switched on."""
    caplog.set_level(logging.DEBUG, logger="parts_mcp")
    server = FakeServer(claim_status=404)
    sleeps: list[float] = []

    def stop() -> bool:
        if server.claims == 4:
            server.claim_status = 200  # the owner sets CATALOGUE_ENABLED=true and redeploys
        return server.claims >= 6

    make_worker(server, processor(make_service(clock, FakeSource(now=clock.now))), sleeps).run(stop=stop)
    assert sleeps == [300, 300, 300, 300, 20, 20]
    worker_lines = [r for r in caplog.records if r.name == "parts_mcp.worker"]
    assert [r.levelno for r in worker_lines] == [logging.INFO, logging.INFO]
    assert "off on the server" in worker_lines[0].getMessage() and "HTTP 404" in worker_lines[0].getMessage()
    assert "is on" in worker_lines[1].getMessage()


def test_feature_off_then_outage_then_still_off_logs_no_more(clock, caplog):
    caplog.set_level(logging.DEBUG, logger="parts_mcp")
    server = FakeServer(claim_status=404)
    sleeps: list[float] = []

    def stop() -> bool:
        server.claim_errors = 1 if server.claims == 2 else 0  # the api restarts during a deploy
        return server.claims >= 4

    make_worker(server, processor(make_service(clock, FakeSource(now=clock.now))), sleeps).run(stop=stop)
    assert sleeps == [300, 300, 30, 300]
    messages = [r.getMessage() for r in caplog.records if r.name == "parts_mcp.worker"]
    assert len(messages) == 2 and "off on the server" in messages[0] and "not reachable" in messages[1]


def test_feature_off_with_once_stops_at_once(clock, caplog):
    caplog.set_level(logging.DEBUG, logger="parts_mcp")
    server = FakeServer(claim_status=404)
    sleeps: list[float] = []
    assert make_worker(server, processor(make_service(clock, FakeSource(now=clock.now))), sleeps).run(once=True) == 0
    assert server.claims == 1 and sleeps == [] and "off on the server" in caplog.text


def test_log_lines_are_one_line_each_on_stdout(tmp_path, capsys):
    server = FakeServer(claim_status=401)

    def sleep(seconds: float) -> None:
        raise KeyboardInterrupt

    env = {"CATALOGUE_WORKER_TOKEN": TOKEN, "CARLINK_API_BASE": "http://api:8000", "WORKER_NAME": "server",
           "PARTS_MCP_LOG_FILE": "off"}
    code = w.main(["--sources", "fake", "--data-dir", str(tmp_path / "data")], env=env,
                  transport=httpx.MockTransport(server), sleep=sleep)
    out = capsys.readouterr()
    assert code == 0 and out.err == ""  # a container's log is its stdout
    lines = out.out.splitlines()
    assert len(lines) >= 3 and all(re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ (INFO|WARNING|ERROR) \S+ \S", line)
                                   for line in lines)
    assert "api=http://api:8000" in lines[0] and "token rejected" in out.out and "stopped (Ctrl+C)" in out.out
    assert TOKEN not in out.out
    assert not (tmp_path / "data" / "logs" / "worker.log").exists()  # PARTS_MCP_LOG_FILE=off


def test_the_formatter_folds_tracebacks_and_hides_the_token():
    try:
        raise ValueError(f"bad page\nsent {TOKEN}")
    except ValueError:
        record = logging.LogRecord("parts_mcp.worker", logging.ERROR, __file__, 1, "job %s crashed", (12,),
                                   sys.exc_info())
    line = w.OneLineFormatter(TOKEN).format(record)
    assert "\n" not in line and line.split(" ")[1:4] == ["ERROR", "worker", "job"]
    assert "Traceback" in line and "ValueError: bad page | sent [token]" in line and TOKEN not in line


@pytest.mark.parametrize("value, wanted", [(None, True), ("", True), ("on", True), ("off", False), ("OFF", False),
                                           ("0", False), ("false", False), ("no", False), ("none", False)])
def test_log_file_switch(value, wanted):
    assert w.log_file_wanted({} if value is None else {"PARTS_MCP_LOG_FILE": value}) is wanted


def test_dry_run_to_stdout_keeps_the_logs_on_stderr(tmp_path, capsys):
    job_file = tmp_path / "job.json"
    job_file.write_text(json.dumps(make_job()), encoding="utf-8")
    assert w.main(["--job", str(job_file), "--sources", "fake", "--data-dir", str(tmp_path / "data")], env={}) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["status"] == "done" and "dry run" in out.err


def test_sigterm_stops_like_ctrl_c_and_the_handler_is_put_back(tmp_path):
    """docker stop sends SIGTERM to PID 1, which ignores signals it has no handler for."""
    before = signal.getsignal(signal.SIGTERM)
    server = FakeServer(claim_status=401)

    def sleep(seconds: float) -> None:
        handler = signal.getsignal(signal.SIGTERM)
        assert handler is w._on_sigterm
        handler(signal.SIGTERM, None)  # what the interpreter does when the signal arrives

    env = {"CATALOGUE_WORKER_TOKEN": TOKEN, "CARLINK_API_BASE": API, "WORKER_NAME": "test-pc"}
    code = w.main(["--sources", "fake", "--data-dir", str(tmp_path / "data")], env=env,
                  transport=httpx.MockTransport(server), sleep=sleep)
    assert code == 0 and server.claims == 1
    assert "stopped (SIGTERM)" in (tmp_path / "data" / "logs" / "worker.log").read_text(encoding="utf-8")
    assert signal.getsignal(signal.SIGTERM) is before


def test_stay_up_without_a_token_idles_quietly(tmp_path, capsys):
    """The container restarts a worker that exits; without a token it idles instead (one line)."""
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise KeyboardInterrupt

    code = w.main(["--stay-up", "--sources", "fake", "--data-dir", str(tmp_path / "data")],
                  env={"PARTS_MCP_LOG_FILE": "off"}, sleep=sleep)
    out = capsys.readouterr().out.splitlines()
    assert code == 0 and sleeps == [3600, 3600, 3600]
    assert len(out) == 2 and "CATALOGUE_WORKER_TOKEN is not set" in out[0] and "stopped" in out[1]
    assert w.main(["--sources", "fake", "--data-dir", str(tmp_path / "data")], env={}) == 2  # a PC: say it, stop
