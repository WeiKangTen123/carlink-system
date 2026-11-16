"""A job with no year, end to end through the real Microfiches plug-in, the
polite fetcher and its page cache: the catalogue worker answers need_year with
cars to pick from, fetching at most its live-page budget (cached pages are
free), and a candidate's key then works as the next job's vehicle_key.

The site is made up: a few tiny pages in the Microfiches layout (model list,
years, trims, type cards, one type page and one diagram), served by a fake
transport -- no catalogue content, no network.
"""

from __future__ import annotations

from typing import Any

import pytest

from parts_mcp import worker as w
from parts_mcp.core.store import open_store
from parts_mcp.service import CatalogueService
from parts_mcp.sources import microfiches as mf

from .conftest import HONEST, FakeTransport, resp

H = "https://www.parts-honda.uk"
P = "/honda-cars"


def page(body: str) -> str:
    return f"<!DOCTYPE html><html><head><title>Made-up page</title></head><body>{body}</body></html>"


def buttons(links: list[tuple[str, str]]) -> str:
    return page("".join(f'<div><a class="btn-identification" href="{href}"><h4>{label}</h4></a></div>'
                        for href, label in links))


def cards(path: str, cars: list[tuple[str, str]]) -> str:
    """Type cards under their headings ("5 Doors 6 speed manual"), as on a trim page."""
    out = []
    for type_id, heading in cars:
        out.append(f'<div><span class="h4">{heading}</span><div class="accordion-collapse"><div class="card-modele">'
                   f'<div class="infos_vehicle"><a href="{path}/{type_id}"><span>ABS<br/></span></a></div>'
                   f'<p class="text-microfiches">Germany, France</p></div></div></div>')
    return page("".join(out))


MANUAL, AUTO = "5 Doors 6 speed manual", "5 Doors full automatic"
# year -> trim -> (path segment, cars). Two generations: 2001-2003 and 2016-2019.
YEARS: dict[int, dict[str, tuple[str, list[tuple[str, str]]]]] = {
    2001: {"1.6": ("16", [("80101", MANUAL)])},
    2002: {"1.6": ("16", [("80201", MANUAL)])},
    2003: {"1.6": ("16", [("80301", MANUAL), ("80302", AUTO)]), "1.6 4WD": ("16-4WD", [("80311", MANUAL)])},
    2016: {"ELEGANCE": ("ELEGANCE", [("81601", MANUAL)])},
    2017: {"ELEGANCE": ("ELEGANCE", [("81701", MANUAL)])},
    2018: {"ELEGANCE": ("ELEGANCE", [("81801", MANUAL)])},
    2019: {name: (name, [(f"819{i}1", MANUAL), (f"819{i}2", AUTO)])
           for i, name in enumerate(["COMFORT", "ELEGANCE", "SPORT", "EXECUTIVE"])},
}
BUMPER = "UPHOLSTERY/REAR-BUMPER"


def site_pages() -> dict[str, str]:
    pages = {
        P: buttons([(f"{P}/CIVIC", "CIVIC"), (f"{P}/HRV", "HRV")]),
        f"{P}/HRV": buttons([(f"{P}/HRV/{y}", str(y)) for y in YEARS]),
    }
    for year, trims in YEARS.items():
        pages[f"{P}/HRV/{year}"] = buttons([(f"{P}/HRV/{year}/{seg}", label) for label, (seg, _) in trims.items()])
        for seg, cars in trims.values():
            pages[f"{P}/HRV/{year}/{seg}"] = cards(f"{P}/HRV/{year}/{seg}", cars)
            for type_id, _ in cars:  # its type page: one diagram, the rear bumper
                pages[f"{P}/HRV/{year}/{seg}/{type_id}"] = page(
                    f'<h2 class="titre_cat">UPHOLSTERY</h2><a href="{P}/HRV/{year}/{seg}/{BUMPER}/{type_id}/B4650/5/{type_id}">'
                    '<h2>REAR BUMPER</h2></a>')
                pages[f"{P}/HRV/{year}/{seg}/{BUMPER}/{type_id}/B4650/5/{type_id}"] = page(
                    '<img id="img_microfiche_img" src="/img/made-up.webp" data-width="800" data-height="400">'
                    '<div itemtype="https://schema.org/Product"><span class="ref-libelle">1</span>'
                    '<span itemprop="name">FACE, RR. BUMPER</span><span itemprop="mpn">00000TEST001</span>'
                    f'<a href="{P}/assignment_spare_parts/00000TEST001">00000-TEST-001</a></div>')
    return pages


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport({H + path: resp(200, body, content_type="text/html; charset=UTF-8")
                          for path, body in site_pages().items()})


@pytest.fixture
def proc(transport, make_fetcher, clock) -> w.JobProcessor:
    source = mf.MicrofichesSource(make_fetcher(transport, HONEST))
    service = CatalogueService(open_store(":memory:"), [source], routes={"HONDA": ["microfiches"]},
                               default_route=["microfiches"], now=clock.now)
    return w.JobProcessor(service)  # the production routes and twins: Vezel -> HR-V on microfiches


def pages_fetched(transport: FakeTransport) -> list[str]:
    """Live page requests, robots.txt left out."""
    return [u.removeprefix(H) for u in transport.urls() if not u.endswith("/robots.txt")]


def job(year: int | None = None, vehicle_key: str | None = None, parts: bool = False) -> dict[str, Any]:
    return {"job_id": 7, "kind": "lookup", "brand": "Honda", "model": "Vezel", "model_key": "Vezel", "year": year,
            "vehicle_key": vehicle_key,
            "parts": [{"source_part": "Rear Bumper", "query": "rear bumper", "side": None}] if parts else []}


def test_no_year_offers_cars_within_four_new_pages(proc, transport):
    result = proc.process(job())
    assert (result["status"], result["error_code"], result["vehicle"]) == ("failed", "need_year", None)
    fetched = pages_fetched(transport)
    assert fetched[:2] == [P, f"{P}/HRV"]  # finding the model: the case's Vezel is the catalogue's HR-V
    # The step itself: 4 pages -- the newest year of each generation and one of its trims.
    assert fetched[2:] == [f"{P}/HRV/2019", f"{P}/HRV/2019/COMFORT", f"{P}/HRV/2003", f"{P}/HRV/2003/16"]
    cars = [(c["key"], c["name"], c["year_from"], c["year_to"]) for c in result["candidates"]]
    assert cars == [
        ("microfiches:81901", f"COMFORT {MANUAL}", 2019, 2019), ("microfiches:81902", f"COMFORT {AUTO}", 2019, 2019),
        ("microfiches:80301", f"1.6 {MANUAL}", 2003, 2003), ("microfiches:80302", f"1.6 {AUTO}", 2003, 2003),
    ]
    assert {(c["model"], c["twin_of"], c["brand"]) for c in result["candidates"]} == {("HRV", "Vezel", "Honda")}
    assert all(c["url"].startswith(f"{H}{P}/HRV/") for c in result["candidates"])
    assert "2001-2003, 2016-2019" in result["error_text"]


def test_a_smaller_budget_is_kept(proc, transport):
    proc.need_year_pages = 2
    result = proc.process(job())
    assert pages_fetched(transport)[2:] == [f"{P}/HRV/2019", f"{P}/HRV/2019/COMFORT"]  # no page left for 2003
    assert [c["key"] for c in result["candidates"]] == ["microfiches:81901", "microfiches:81902"]


def test_cached_pages_are_free_and_a_later_look_up_widens_the_list(proc, transport):
    first = proc.process(job())
    before = len(pages_fetched(transport))
    second = proc.process(job())
    new = pages_fetched(transport)[before:]
    # The cached trims give each generation its car for free; the budget goes to more 2019 trims
    # until there are 10 cars.
    assert new == [f"{P}/HRV/2019/ELEGANCE", f"{P}/HRV/2019/SPORT", f"{P}/HRV/2019/EXECUTIVE"]
    keys = [c["key"] for c in second["candidates"]]
    assert len(keys) == 10 and {c["key"] for c in first["candidates"]} <= set(keys)
    assert [c["year_from"] for c in second["candidates"]] == [2019] * 8 + [2003] * 2
    third = len(pages_fetched(transport))
    proc.process(job())
    assert len(pages_fetched(transport)) - third <= w.NEED_YEAR_PAGES


def test_a_candidate_key_is_the_next_look_ups_car(proc, transport):
    offered = proc.process(job())["candidates"]
    before = len(pages_fetched(transport))
    picked = offered[2]["key"]  # the 2003 1.6 manual
    result = proc.process(job(vehicle_key=picked, parts=True))
    assert (result["status"], result["error_code"]) == ("done", None)
    v = result["vehicle"]
    assert (v["key"], v["twin_of"], v["year_from"], v["year_to"]) == (picked, "Vezel", 2003, 2003)
    diagram = result["parts"][0]["diagrams"][0]
    assert diagram["diagram_name"] == "REAR BUMPER" and diagram["rows"][0]["part_number"] == "00000TEST001"
    # The other cars are offered again, from the cache: the only new pages are the picked car's own.
    assert [c["key"] for c in result["candidates"]] == [picked] + [c["key"] for c in offered if c["key"] != picked]
    assert pages_fetched(transport)[before:] == [f"{P}/HRV/2003/16/80301", f"{P}/HRV/2003/16/{BUMPER}/80301/B4650/5/80301"]


def test_with_a_year_nothing_changes(proc, transport):
    result = proc.process(job(year=2016))
    assert result["status"] == "done" and result["vehicle"]["key"] == "microfiches:81601"
    assert result["vehicle"]["year_from"] is None  # one year page shows no span: none invented
    assert pages_fetched(transport) == [P, f"{P}/HRV", f"{P}/HRV/2016", f"{P}/HRV/2016/ELEGANCE"]
