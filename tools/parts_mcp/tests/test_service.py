"""CatalogueService with FakeSource: read-through, TTL, fallback, stale, search."""

from __future__ import annotations

import pytest

from parts_mcp.core.errors import AllSourcesFailed, ChallengeBlocked, DailyCapReached, NotFound, NotSupported
from parts_mcp.core.fetch import DAY
from parts_mcp.core.store import open_store
from parts_mcp.service import CatalogueService
from parts_mcp.sources.fake import DEMO_VIN, FakeSource


@pytest.fixture
def store():
    s = open_store(":memory:")
    yield s
    s.close()


def make(store, clock, *sources, routes=None):
    sources = sources or (FakeSource(now=clock.now),)
    return CatalogueService(store, list(sources), routes=routes or {}, default_route=(sources[0].name,),
                            now=clock.now)


def test_read_through_find_vehicles(store, clock):
    src = FakeSource(now=clock.now)
    svc = make(store, clock, src)
    first = svc.find_vehicles("bmw", "3 series", 2016)
    second = svc.find_vehicles("BMW", "3 Series", 2016)
    assert src.calls_to("find_vehicles") == 1
    assert (first.source, first.from_store) == ("fake", False)
    assert (second.source, second.from_store) == ("fake", True)
    assert second.value == first.value
    v = second.value[0]
    assert v.url.startswith("https://") and v.fetched_at.tzinfo is not None
    assert svc.vehicle(v.key) == v


def test_ttl_expiry_refetches(store, clock):
    src = FakeSource(now=clock.now)
    svc = make(store, clock, src)
    v = svc.find_vehicles("BMW", "3 series").value[0]
    svc.list_groups(v)
    clock.advance(179 * DAY)
    assert svc.list_groups(v).from_store
    clock.advance(2 * DAY)
    assert not svc.list_groups(v).from_store
    assert src.calls_to("list_groups") == 2


def test_lookup_dated_by_oldest_page(store, clock):
    """A plug-in answering from a 179-day-old page cache must not look fresh."""
    from datetime import timedelta

    old = clock.now() - timedelta(days=179)
    src = FakeSource(now=lambda: old)
    svc = make(store, clock, src)
    v = svc.find_vehicles("BMW", "3 series").value[0]
    svc.list_groups(v)
    clock.advance(2 * DAY)
    svc.list_groups(v)
    assert src.calls_to("list_groups") == 2


def test_empty_answer_is_remembered(store, clock):
    src = FakeSource(now=clock.now)
    svc = make(store, clock, src)
    assert svc.find_vehicles("BMW", "Z4").value == []
    assert svc.find_vehicles("BMW", "Z4").value == []
    assert src.calls_to("find_vehicles") == 1


def test_fallback_to_next_source_on_failure(store, clock):
    primary = FakeSource("primary", fail_with=ChallengeBlocked("cf", host="www.shop.example"), now=clock.now)
    secondary = FakeSource("secondary", now=clock.now)
    svc = make(store, clock, primary, secondary, routes={"BMW": ["primary", "secondary"]})
    ans = svc.find_vehicles("BMW", "3 series")
    assert ans.source == "secondary" and len(ans.value) == 1
    assert ans.failures == [{"error": "challenge_blocked", "message": "cf", "source": "primary",
                             "host": "www.shop.example"}]
    assert ans.value[0].source == "secondary"


def test_fallback_on_empty_answer(store, clock):
    primary = FakeSource("primary", now=clock.now)
    primary.find_vehicles = lambda brand, model=None, year=None: []
    secondary = FakeSource("secondary", now=clock.now)
    svc = make(store, clock, primary, secondary, routes={"BMW": ["primary", "secondary"]})
    assert svc.find_vehicles("BMW", "3 series").source == "secondary"


def test_all_sources_failing(store, clock):
    one = FakeSource("one", fail_with=DailyCapReached("cap"), now=clock.now)
    svc = make(store, clock, one)
    with pytest.raises(DailyCapReached):  # a single failure keeps its own code
        svc.find_vehicles("BMW", "3 series")
    two = FakeSource("two", fail_with=ChallengeBlocked("cf"), now=clock.now)
    svc = make(store, clock, one, two, routes={"BMW": ["one", "two"]})
    with pytest.raises(AllSourcesFailed) as info:
        svc.find_vehicles("BMW", "3 series")
    assert [f["source"] for f in info.value.to_dict()["failures"]] == ["one", "two"]


def test_plugin_crash_is_wrapped(store, clock):
    src = FakeSource(fail_with=KeyError("td"), now=clock.now)
    svc = make(store, clock, src)
    with pytest.raises(Exception) as info:
        svc.find_vehicles("BMW", "3 series")
    assert info.value.to_dict()["error"] == "source_error"


def test_stale_answer_when_source_fails(store, clock):
    src = FakeSource(now=clock.now)
    svc = make(store, clock, src)
    v = svc.find_vehicles("BMW", "3 series").value[0]
    groups = svc.list_groups(v).value
    clock.advance(200 * DAY)
    src.fail_with = ChallengeBlocked("cf")
    ans = svc.list_groups(v)
    assert ans.stale and ans.from_store and [g.id for g in ans.value] == [g.id for g in groups]
    assert ans.failures[0]["error"] == "challenge_blocked"


def test_vin_and_diagram_flow(store, clock):
    src = FakeSource(now=clock.now)
    svc = make(store, clock, src)
    vehicles = svc.find_vehicles(vin=DEMO_VIN.lower()).value
    assert vehicles[0].vin == DEMO_VIN
    v = vehicles[0]
    group = next(g for g in svc.list_groups(v).value if g.id == "51")
    listed = svc.list_diagrams(v, group).value
    assert listed[0].image_url is None
    ans = svc.get_diagram(v, listed[0])
    diagram, rows = ans.value
    assert diagram.image_url and len(diagram.hotspots) == len(rows) == 3
    assert rows[1].side == "left" and rows[1].name_original.startswith("Catadiottro")
    assert all(r.url and r.fetched_at for r in rows)
    again = svc.get_diagram(*svc.diagram(diagram.key))
    assert again.from_store and again.value[1] == rows
    assert src.calls_to("get_diagram") == 1
    with pytest.raises(NotFound):
        svc.diagram("fake:nope:nope")


def test_find_diagrams_synonyms_and_group_budget(store, clock):
    src = FakeSource(now=clock.now)
    svc = make(store, clock, src)
    v = svc.find_vehicles("BMW", "3 series").value[0]

    trunk = svc.find_diagrams(v, "trunk lid")
    assert trunk.matches[0].diagram.name == "Luggage compartment lid"
    opened = {arg[1] for m, arg in src.calls if m == "list_diagrams"}
    assert "11" not in opened  # Engine group never opened for a body panel

    rear = svc.find_diagrams(v, "rear bumper")
    names = [m.diagram.name for m in rear.matches]
    assert names[0] == "Trim panel, rear bumper"
    front = [m for m in rear.matches if m.diagram.name == "Trim panel, front bumper"]
    assert not front or front[0].score < rear.matches[0].score / 2

    assert svc.find_diagrams(v, "back door").matches[0].diagram.name == "Luggage compartment lid"
    assert svc.find_diagrams(v, "tail lamp").matches[0].diagram.name == "Rear light"
    assert svc.find_diagrams(v, "paraurti posteriore").matches[0].diagram.name == "Trim panel, rear bumper"


def test_find_diagrams_respects_max_new_groups(store, clock):
    src = FakeSource(now=clock.now)
    svc = make(store, clock, src)
    v = svc.find_vehicles("BMW", "3 series").value[0]
    found = svc.find_diagrams(v, "rear bumper", max_new_groups=0)
    assert found.matches == [] and found.searched_groups == []
    assert src.calls_to("list_diagrams") == 0


def test_search_parts_after_get_diagram(store, clock):
    svc = make(store, clock)
    v = svc.find_vehicles("BMW", "3 series").value[0]
    for m in svc.find_diagrams(v, "rear bumper").matches[:1]:
        svc.get_diagram(v, m.diagram)
    assert [r.name for r in svc.search_parts(v, "reflector")] == ["Reflector, rear left", "Reflector, rear right"]
    assert svc.search_parts(v, "catadiottro destro")[0].side == "right"
    assert svc.search_parts(v, "00-00-0-000-102")[0].row_index == 1
    assert svc.search_parts(v, "radiator") == []


def test_get_price_read_through_and_unknown(store, clock):
    src = FakeSource(now=clock.now)
    svc = make(store, clock, src)
    ans = svc.get_price("00 00 0 000 101", "bmw")
    q = ans.value[0]
    assert (q.amount, q.currency, q.tax_note, q.availability) == (312.40, "EUR", "excl. VAT", None)
    assert svc.get_price("00000000101", "BMW").from_store
    clock.advance(8 * DAY)  # price TTL is 7 days
    assert not svc.get_price("00000000101", "BMW").from_store
    none = svc.get_price("99", "BMW")
    assert none.value == [] and none.source is None


def test_routing_by_brand(store, clock):
    micro = FakeSource("microfiches", brands=("BMW", "MINI", "HONDA"), now=clock.now)
    other = FakeSource("other", brands=(), now=clock.now)
    svc = CatalogueService(store, [micro, other], routes={"BMW": ["microfiches", "other"]},
                           default_route=("other",), now=clock.now)
    assert [s.name for s in svc.route("bmw")] == ["microfiches", "other"]
    assert [s.name for s in svc.route("Toyota")] == ["other"]
    assert [s.name for s in svc.route(None)] == ["other", "microfiches"]


def test_a_make_no_source_serves_is_not_supported(store, clock):
    """The server's sources serve BMW, MINI and Honda only; other makes get a clear answer."""
    micro = FakeSource("microfiches", brands=("BMW", "MINI", "HONDA"), now=clock.now)
    svc = CatalogueService(store, [micro], routes={"BMW": ["microfiches"]}, now=clock.now)  # default route: none
    assert svc.route("Toyota") == [] and [s.name for s in svc.route("Honda")] == ["microfiches"]
    with pytest.raises(NotSupported):
        svc.find_vehicles("Toyota", "Corolla", 2014)
    assert micro.calls == []
