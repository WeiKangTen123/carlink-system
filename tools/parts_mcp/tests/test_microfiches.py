"""Offline tests for the Microfiches plug-in (BMW, MINI, Honda shops).

Fixtures are pages saved from the live sites in Nov 2026, cut down to what the
parsers read: only the elements they select (and their parents), a few list
entries, part rows and hotspots per page, no scripts, styles or images; gzipped.
The sites' terms forbid reproducing their catalogue, so keep new fixtures as
small. A fake transport serves them by URL, so the real fetcher and the real
source code run without network.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from parts_mcp import synonyms
from parts_mcp.core.errors import ChooseTrim, NotSupported
from parts_mcp.core.models import Diagram, Vehicle
from parts_mcp.core.store import open_store
from parts_mcp.service import CatalogueService
from parts_mcp.sources import microfiches as mf
from parts_mcp.sources.microfiches_glossary import side_of, translate

from .conftest import HONEST, FakeTransport, resp

FIXTURES = Path(__file__).parent / "fixtures" / "microfiches"
BMW = "https://www.bmw-spare-parts.com"
HONDA = "https://www.parts-honda.uk"
X1 = "/bmw-cars/X1/2014/X1-18i"
CIVIC = "/honda-cars/CIVIC/2008/18-EXI"
HRV = "/honda-cars/HRV/2016/ELEGANCE"


def page(name: str) -> bytes:
    return gzip.decompress((FIXTURES / f"{name}.html.gz").read_bytes())


ROUTES = {
    f"{BMW}/bmw-cars": "bmw_index",
    f"{BMW}/bmw-cars/X1": "bmw_x1",
    f"{BMW}/bmw-cars/X1/2014": "bmw_x1_2014",
    f"{BMW}{X1}/X1-18i/57545HS11": "bmw_type_57545HS11",
    f"{BMW}{X1}/X1-18i/Vehicle-trim/51/57545HS11": "bmw_group_51",
    f"{BMW}{X1}/X1-18i/Bodywork/41/57545HS11": "bmw_group_41",
    f"{BMW}{X1}/Vehicle-trim/Trim-panel-rear/7/51_3279/51/57545HS11": "bmw_diag_51_3279",
    f"{BMW}{X1}/Bodywork/Trunk-lid/7/41_2581/41/57545HS11": "bmw_diag_41_2581",
    f"{BMW}{X1}/Vehicle-trim/Trim-panel-front/9/51_3294/51/57545HS11": "bmw_diag_51_3294",
    f"{BMW}/bmw-cars/assignment_spare_parts/51117453705": "bmw_part_51117453705",
    f"{BMW}/bmw-cars/search_vehicle/chassis/WBAHS110X0P000001": "bmw_vin_WBAHS110X0P000001",
    f"{HONDA}/honda-cars": "honda_index",
    f"{HONDA}/honda-cars/CIVIC": "honda_civic",
    f"{HONDA}/honda-cars/CIVIC/2008": "honda_civic_2008",
    f"{HONDA}{CIVIC}": "honda_civic_2008_18exi",
    f"{HONDA}{CIVIC}/14488": "honda_type_14488",
    f"{HONDA}{CIVIC}/UPHOLSTERY/BUMPERS-08-/17SNA801/B__4600/5/14488": "honda_diag_civic_bumpers",
    f"{HONDA}{HRV}/UPHOLSTERY/REAR-BUMPER/30584/B4650/5/30584": "honda_diag_hrv_rear_bumper",
}


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport({url: resp(200, page(name), content_type="text/html; charset=UTF-8")
                          for url, name in ROUTES.items()})


@pytest.fixture
def source(transport, make_fetcher) -> mf.MicrofichesSource:
    return mf.MicrofichesSource(make_fetcher(transport, HONEST))


def bmw_x1(source, steering: str = "RHD") -> Vehicle:
    found = source.find_vehicles("BMW", f"X1 F48 18i {steering}", 2014)
    assert len(found) == 1
    return found[0]


def civic(source) -> Vehicle:
    found = source.find_vehicles("HONDA", "Civic 1.8 EXi automatic Singapore", 2008)
    return next(v for v in found if v.id == "14488")


def hrv(at) -> Vehicle:
    return Vehicle(source="microfiches", url=f"{HONDA}/honda-cars/HRV/2016/ELEGANCE", fetched_at=at,
                   source_ref={"path": "/honda-cars/HRV/2016/ELEGANCE/30584"}, id="30584", brand="HONDA")


# --- vehicles -----------------------------------------------------------------------


def test_bmw_type_cards_become_vehicles(source):
    v = bmw_x1(source)
    assert (v.id, v.brand, v.model, v.name) == ("57545HS12", "BMW", "X1", "X1 18i")
    assert (v.chassis, v.market, v.engine, v.body) == ("F48", "ECE", "B38", "SAV")
    assert v.transmission is None  # the card says "Gearbox: N", not a transmission
    assert v.attributes == {"steering": "RHD", "listed_year": "2014", "gearbox": "N", "options_id": "57545",
                            "type_key": "HS12", "chassis_type": "X1 18i"}
    assert v.source_ref == {"path": "/bmw-cars/X1/2014/X1-18i/X1-18i/57545HS12"}
    assert v.url == f"{BMW}/bmw-cars/X1/2014" and v.fetched_at.tzinfo is not None
    assert v.year_from is None and v.year_to is None  # one year page does not show the range
    assert bmw_x1(source, "LHD").id == "57545HS11"


def test_bmw_find_vehicles_edges(source):
    assert len(source.find_vehicles("BMW", "x1", 2014)) == 6  # every card when nothing narrows it
    assert source.find_vehicles("BMW", "X1", 1990) == []
    assert source.find_vehicles("BMW", "Z9 Roadster", 2014) == []
    assert source.find_vehicles("TOYOTA", "Corolla", 2014) == []
    with pytest.raises(NotSupported, match="2008-2019"):
        source.find_vehicles("BMW", "X1", None)


def test_honda_needs_a_trim_then_returns_type_cards(source):
    with pytest.raises(NotSupported, match="11 matching trims.*1.8 EXI") as first:
        source.find_vehicles("HONDA", "Civic", 2008)
    assert isinstance(first.value, ChooseTrim) and first.value.code == "not_supported"
    assert (first.value.brand, first.value.series, first.value.year) == ("HONDA", "CIVIC", 2008)
    assert len(first.value.trims) == 11 and "1.8 EXI" in first.value.trims and first.value.cached == []
    found = source.find_vehicles("HONDA", "Civic 1.8 EXi automatic Singapore", 2008)
    with pytest.raises(ChooseTrim) as again:  # the 1.8 EXI page is in the page cache now
        source.find_vehicles("HONDA", "Civic", 2008)
    assert again.value.cached == ["1.8 EXI"]
    assert [v.id for v in found] == ["14488", "14487"]
    v = found[0]
    assert (v.model, v.trim, v.name) == ("CIVIC", "1.8 EXI", "1.8 EXI 4 Doors 5 speed automatic")
    assert (v.body, v.transmission, v.chassis, v.engine) == ("4 Doors", "5 speed automatic", None, None)
    assert "Singapore" in v.market and "Thailand" in v.market
    assert v.attributes["steering"] == "RHD" and v.attributes["options"] == "ABS"


def test_match_trims():
    trims = mf.parse_trims(mf._soup(page("honda_civic_2008").decode()), "honda-cars", "CIVIC", 2008)
    assert len(trims) == 11

    def labels(words):
        chosen, left = mf.match_trims(trims, words)
        return [label for label, _ in chosen], left

    assert labels(["18", "exi", "automatic"]) == (["1.8 EXI"], ["automatic"])
    assert labels(["exi"]) == (["EXI"], [])  # the trim named exactly beats '1.8 EXI'
    assert labels(["type", "r", "rhd"]) == (["2.0 TYPE R", "2.0 TYPE R PLUS", "2.0 TYPE R RACE"], ["rhd"])
    assert labels(["fd", "sedan"]) == ([], ["fd", "sedan"])


# --- groups and diagram lists -----------------------------------------------------------


def test_bmw_groups_and_diagram_list(source):
    v = bmw_x1(source, "LHD")
    groups = source.list_groups(v)
    assert len(groups) == 8
    trim = next(g for g in groups if g.id == "51")
    assert trim.name == "Vehicle trim" and trim.source_ref["path"].endswith("/Vehicle-trim/51/57545HS11")
    diagrams = source.list_diagrams(v, trim)
    assert len(diagrams) == 11 and {d.group_id for d in diagrams} == {"51"}
    rear = next(d for d in diagrams if d.id == "51_3279")
    assert (rear.name, rear.code, rear.group_path) == ("Trim panel, rear", "51_3279", ["Vehicle trim"])
    assert rear.source_ref["path"] == f"{X1}/Vehicle-trim/Trim-panel-rear/7/51_3279/51/57545HS11"
    body = source.list_diagrams(v, next(g for g in groups if g.id == "41"))
    remote = [d for d in body if d.name == "Radio remote control"]
    assert len(remote) == 3 and "after : 20190300" in (remote[0].notes or "")


def test_honda_groups_come_from_the_type_page(source, transport):
    v = civic(source)
    groups = source.list_groups(v)
    assert [(g.id, g.name) for g in groups] == [
        ("1", "ENGINE"), ("2", "TRANSMISSION"), ("3", "ELECTRICAL EQUIPMENTS, EXHAUST, HEATER"),
        ("4", "STEERING, BRAKE, SUSPENSION"), ("5", "UPHOLSTERY"), ("6", "BODY PARTS")]
    before = len(transport.requests)
    upholstery = source.list_diagrams(v, groups[4])
    assert len(transport.requests) == before  # same page, served from the page cache
    bumpers = next(d for d in upholstery if d.id == "B__4600")
    assert bumpers.name == "BUMPERS ('08)" and bumpers.group_path == ["UPHOLSTERY"]
    assert sum(len(source.list_diagrams(v, g)) for g in groups) == 43


# --- diagram pages ----------------------------------------------------------------------


def _diagram(source, v, group_id: str, diagram_id: str):
    group = next(g for g in source.list_groups(v) if g.id == group_id)
    return next(d for d in source.list_diagrams(v, group) if d.id == diagram_id)


def test_bmw_diagram_rows_prices_and_supersession(source):
    v = bmw_x1(source, "LHD")
    full, rows = source.get_diagram(v, _diagram(source, v, "51", "51_3279"))
    assert full.id == "51_3279" and full.name == "Trim panel, rear"
    assert full.image_url == (f"{BMW}/thumbs/bmw/moto_img_new/7/900_900/"
                              "Trim-panel-rear-BMW-BMW-X1-2014-X1-18i-N-X1-18i-72898.webp")
    assert (full.image_width, full.image_height) == (800, 560)
    assert len(rows) == 7 and [r.row_index for r in rows] == list(range(7))
    assert [r.callout for r in rows[:4]] == ["01", "02", "03", "03"]
    first, superseded = rows[0], rows[1]
    assert (first.part_number, first.superseded_from) == ("51127403391", None)
    assert first.name_original == "RIV. PARAURTI CON APPL. M.FONDO, POST."
    assert first.attributes["price"] == "504.00" and first.attributes["currency"] == "EUR"
    assert first.attributes["price_note"] == "Unit Price Excl Tax"
    assert first.attributes["part_number_display"] == "51 12 7 403 391"
    assert (superseded.part_number, superseded.superseded_from) == ("51127403392", "51127355271")
    assert superseded.name == "Trim panel bumper centre rear"
    assert superseded.notes == ("For vehicles with Park Distance Control (PDC), rear S507A : "
                                "No Park Distance Control (PDC), rear S507A")
    assert all(r.qty is None for r in rows)
    assert [(r.part_number, r.side) for r in rows if r.callout == "07"] == [
        ("51127403389", "left"), ("51127403390", "right")]  # "...LATERALE SX" / "...DX"
    assert {r.vehicle_id for r in rows} == {"57545HS11"} and {r.diagram_id for r in rows} == {"51_3279"}


def test_hotspots_are_deduplicated(source):
    v = bmw_x1(source, "LHD")
    full, rows = source.get_diagram(v, _diagram(source, v, "51", "51_3279"))
    assert len(full.hotspots) == 8  # 15 on the page: each repeated once per part row
    sixteen = [(h.x, h.y) for h in full.hotspots if h.callout == "16"]
    assert sixteen == [(356.0, 123.0), (303.0, 174.0), (233.0, 109.0), (585.0, 520.0)]
    assert {h.callout for h in full.hotspots} <= {r.callout for r in rows}


def test_bmw_not_sold_rows_and_superseded_duplicates(source):
    v = bmw_x1(source, "LHD")
    _, rows = source.get_diagram(v, _diagram(source, v, "41", "41_2581"))
    gone = [r for r in rows if r.attributes.get("availability") == "not sold"]
    assert len(gone) == 3 and all("price" not in r.attributes for r in gone)
    link_lever = gone[0]
    assert (link_lever.part_number, link_lever.name_original) == ("51242584673", "LINK LEVER, SPRING SUPPORT, LEFT")
    assert link_lever.name == "Link lever spring support left" and link_lever.side == "left"
    # the old number's row points at 51247412408, which also has its own row: keep both
    pair = [(r.part_number, r.superseded_from) for r in rows if r.part_number == "51247412408"]
    assert pair == [("51247412408", "51242712312"), ("51247412408", None)]
    assert rows[0].name == "Tailgate rear" and rows[0].name_original == "PORTELLONE POSTERIORE"


def test_bmw_name_replaced_by_part_number_and_notes(source):
    v = bmw_x1(source, "LHD")
    _, rows = source.get_diagram(v, _diagram(source, v, "51", "51_3294"))
    odd = next(r for r in rows if r.callout == "06")
    assert (odd.name, odd.name_original) == (None, None)
    assert odd.attributes["name_shown"] == "07145A48F73"
    assert (odd.part_number, odd.superseded_from) == ("07145A48F73", "07147406384")
    sra = rows[1]
    assert sra.notes == "SRA For vehicles with Headlight cleaning system S502A : Yes Headlight cleaning system S502A"
    assert [r.side for r in rows if r.callout == "02"] == ["left", "right"]  # "...BASE SIN." / "...BASE DES."
    plate = next(r for r in rows if r.callout == "10")
    assert plate.attributes["availability"] == "not sold" and plate.name == "Licence plate holder"


def test_honda_diagram_rows(source):
    v = civic(source)
    full, rows = source.get_diagram(v, _diagram(source, v, "5", "B__4600"))
    assert (full.image_width, full.image_height) == (930, 464) and len(full.hotspots) == 5
    assert full.hotspots[0].callout == "1" and full.hotspots[0].x == pytest.approx(92.03125)
    assert len(rows) == 6
    face = rows[0]
    assert (face.name, face.name_original, face.notes) == ("FACE, FR. BUMPER", "FACE, FR. BUMPER", "PART NOT PAINTED")
    assert face.attributes["part_number_display"] == "04711-SNB-E00ZZ"
    assert (rows[1].part_number, rows[1].superseded_from) == ("04715SNB010ZZ", "04715SNB000ZZ")
    painted = rows[2]  # Honda's colour popover is an <a>, not a <button>
    assert (painted.name, painted.notes) == ("FACE, RR. BUMPER *YR566M*", "Color of the vehicle: BORREGO BEIGE "
                                             "METALLIC | Color of the spare part: BORREGO BEIGE METALLIC")
    assert [(r.name, r.side) for r in rows if r.callout in ("12", "13")] == [
        ("SPACER, R. FR. BUMPER SIDE", "right"), ("SPACER, L. FR. BUMPER SIDE", "left")]
    assert rows[1].side is None  # "FACE, RR. BUMPER": RR. is rear, not right
    assert rows[0].attributes["currency"] == "EUR"


def test_honda_bilingual_names_keep_the_english_part(source, clock):
    path = "/honda-cars/HRV/2016/ELEGANCE/UPHOLSTERY/REAR-BUMPER/30584/B4650/5/30584"
    stub = Diagram(source="microfiches", url=HONDA + path, fetched_at=clock.now(), source_ref={"path": path},
                   id="B4650", vehicle_id="30584", group_id="5", name="REAR BUMPER", group_path=["UPHOLSTERY"])
    full, rows = source.get_diagram(hrv(clock.now()), stub)
    assert full.hotspots == []  # this page has no hotspot markup: callouts are only printed in the image
    assert (full.image_width, full.image_height) == (768, 384)
    corner = rows[0]
    assert corner.name == "FACE, R. RR. BUMPER CORNE"
    assert corner.name_original.startswith("FACE, R. RR. BUMPER CORNE --- FACE, COIN PARE-CHOCS AR. D.")
    assert corner.side == "right" and rows[2].side == "left"


# --- prices -------------------------------------------------------------------------------


def test_get_price_from_part_page(source):
    quote = source.get_price("51 11 7 453 705", "BMW")
    assert (quote.part_number, quote.amount, quote.currency) == ("51117453705", 21.89, "EUR")
    assert quote.tax_note == "excl. VAT; BMW EU list price (seller: Autolux, Italy)"
    assert quote.name == "Shield spray nozzle with base coat left"
    assert "name on site: schermo spruzzatore con mano base sin." in quote.notes
    assert "Unit Price Excl Tax" in quote.notes and quote.availability == "InStock"
    assert source.get_price("99999999999", "BMW") is None  # 404 from the fake site
    assert source.get_price("51117453705", "TOYOTA") is None


def test_get_price_reuses_prices_seen_on_a_diagram(source, transport, clock, monkeypatch):
    monkeypatch.setattr(mf, "utcnow", clock.now)
    v = bmw_x1(source, "LHD")
    source.get_diagram(v, _diagram(source, v, "51", "51_3279"))
    before = len(transport.requests)
    quote = source.get_price("51127403391", "BMW")
    assert len(transport.requests) == before
    diagram_url = f"{BMW}{X1}/Vehicle-trim/Trim-panel-rear/7/51_3279/51/57545HS11"
    assert (quote.amount, quote.currency, quote.url) == (504.0, "EUR", diagram_url)
    assert quote.tax_note == "excl. VAT; BMW EU list price (seller: Autolux, Italy)"
    # a superseded row's price is not reused (it is unclear which number it is for): ask the part page
    assert source.get_price("51127355271", "BMW") is None  # the fake site has no such page
    assert len(transport.requests) == before + 1


# --- VIN ---------------------------------------------------------------------------------------


def _v(type_id: str, at) -> Vehicle:
    return Vehicle(source="microfiches", url="u", fetched_at=at, id=type_id, brand="BMW")


def test_vin_check_keeps_matching_type_keys(clock):
    at = clock.now()
    kept = mf.check_vin("WBAHS110X0P000001", [_v("57545HS11", at), _v("48922UF12", at)], type_key_rule=True)
    assert [(v.id, v.vin, v.attributes["vin_match"]) for v in kept] == [
        ("57545HS11", "WBAHS110X0P000001", "type_key")]
    loose = mf.check_vin("WBAHS110X0P000001", [_v("48922UF12", at)], type_key_rule=True)
    assert [(v.id, v.attributes["vin_match"]) for v in loose] == [("48922UF12", "unverified")]
    honda = mf.check_vin("JHMZZ000000000001", [_v("14488", at)], type_key_rule=False)
    assert honda[0].attributes["vin_match"] == "unverified"


def test_resolve_vin_marks_an_unrelated_hit_unverified(source):
    found = source.resolve_vin("wbahs110x0p000001")
    assert [(v.id, v.chassis, v.vin) for v in found] == [("48922UF12", "E87", "WBAHS110X0P000001")]
    v = found[0]
    assert v.attributes["vin_match"] == "unverified" and (v.year_from, v.year_to) == (2003, 2007)
    assert v.model == "1 Series" and v.attributes["steering"] == "RHD"
    assert source.resolve_vin("ZZZ00000000000000") == []  # not a Microfiches brand


# --- glossary -------------------------------------------------------------------------------


@pytest.mark.parametrize("italian, english", [
    ("RIVESTIMENTO PARAURTI CON FONDO ANT.", "Trim panel bumper primed front"),
    ("PORTELLONE POSTERIORE", "Tailgate rear"),
    ("CERNIERA DI PORTELLONE SINISTRA", "Hinge tailgate left"),
    ("MOLLA A GAS PORTELLONE", "Gas strut tailgate"),
    ("SERRATURA DI PORTELLONE", "Lock tailgate"),
    ("CATARIFRANGENTE POST. SX", "Reflector rear left"),
    ("FANALE FIANCATA POSTERIORE DESTRA", "Lamp side panel rear right"),
    ("LUCE NEL COFANO POSTERIORE SINISTRA", "Light in trunk lid left"),
    ("VITE ESAGONALE CON RONDELLA", "Hex bolt with washer"),
    ("DADO IN PLASTICA", "Nut in plastic"),
    ("FERMAGLIO SUPPORTO DEL TUBO FLESSIBILE", "Clip bracket hose flexible"),
    ("GUARNIZIONE COPERTURA SCHERMO GUIDA", "Seal cover shield guide"),
    ("VITE ESAGONALE M6X20", "Hex bolt M6X20"),
    ("LINK LEVER, SPRING SUPPORT, LEFT", "Link lever spring support left"),
    ("CRISTALLO LATER. FISSO POST. SX GLANZSCHWARZ", "Glass lateral fixed rear left gloss black"),
])
def test_glossary_translates(italian, english):
    assert translate(italian) == english


def test_glossary_gives_up_below_half():
    assert translate("FRIZZOLO BALDACCHINO PARAURTI") is None  # 1 of 3 words known
    assert translate("STAFFA FRIZZOLO") == "Bracket"  # half is enough
    assert translate("07145A48F73") is None and translate("") is None and translate(None) is None


@pytest.mark.parametrize("name, side", [
    ("PELLICOLA ANTISFREG. TELAIO LATERALE SX", "left"), ("CONVOGL. ACQUA DESTRO", "right"),
    ("VETRO LATERALE PORTA POST.SIN.", "left"), ("VETRO LATERALE FISSO PORTA POST.DES.", "right"),
    ("LEVA DI ACCOPPIAMENTO SN.", "left"), ("LEVA DI ACCOPPIAMENTO DS.", "right"),
    ("PORTALAMPADA LUCE NEL COFANO POSTER. DX.", "right"), ("SEDE PARAURTI POSTERIORE SINISTRA", "left"),
    ("SPACER, R. FR. BUMPER SIDE", "right"), ("BEAM COMP., L. FR. BUMPER UPPER", "left"),
    ("COMBINATION SWITCH (RH)", "right"), ("FACE, RR. BUMPER", None), ("PORTELLONE POSTERIORE", None),
    ("KIT SX E DX", None),
])
def test_side_from_name(name, side):
    assert side_of(name) == side


# --- mapping hints and the service end to end --------------------------------------------------


def test_synonyms_cover_platform_names():
    assert synonyms.score("rear bumper", "Trim panel, rear")[0] >= 0.9
    assert synonyms.score("rear bumper", "BUMPERS ('08)")[0] >= 0.9
    assert synonyms.score("rear bumper", "Trim panel, front")[0] < 0.25
    assert synonyms.score("tailgate", "Trunk lid")[0] >= 0.9
    assert synonyms.score("tail lamp", "Rear light")[0] >= 0.9
    assert synonyms.score("tail lamp", "TAILLIGHT / LICENSE LIGHT ('08)")[0] >= 0.9
    assert synonyms.group_hint_score("rear bumper", "UPHOLSTERY") > 0
    assert synonyms.group_hint_score("rear bumper", "Vehicle trim") > 0
    # the specific group wins a tie, so a 3-group budget opens "Lighting" for a tail lamp
    lighting = synonyms.group_hint_score("tail lamp", "Lighting")
    assert lighting > synonyms.group_hint_score("tail lamp", "Vehicle electrical system")
    assert lighting > synonyms.group_hint_score("tail lamp", "Body equipment") > 0


def test_service_finds_rear_bumper_diagrams(source, clock):
    store = open_store(":memory:")
    svc = CatalogueService(store, [source], routes={"BMW": ["microfiches"], "HONDA": ["microfiches"]},
                           default_route=["microfiches"], now=clock.now)
    bmw = svc.find_vehicles("BMW", "X1 F48 18i LHD", 2014).value[0]
    hits = svc.find_diagrams(bmw, "rear bumper")
    assert [(m.diagram.id, m.diagram.name, m.score) for m in hits.matches[:2]] == [
        ("51_3279", "Trim panel, rear", 0.9), ("51_3958", "M trim panel, rear", 0.9)]
    assert all(m.score < 0.5 for m in hits.matches[2:])
    assert {g.name for g in hits.searched_groups} == {"Bodywork", "Vehicle trim"}
    honda = svc.find_vehicles("HONDA", "Civic 1.8 EXi automatic Singapore", 2008).value[0]
    found = svc.find_diagrams(honda, "rear bumper")
    assert (found.matches[0].diagram.id, found.matches[0].diagram.name) == ("B__4600", "BUMPERS ('08)")
    assert {g.name for g in found.searched_groups} == {"UPHOLSTERY", "BODY PARTS"}
    assert svc.find_diagrams(honda, "trunk lid").matches[0].diagram.name == "TRUNK LID"
    assert svc.find_diagrams(honda, "tail lamp").matches[0].diagram.name == "TAILLIGHT / LICENSE LIGHT ('08)"
    diagram, rows = svc.get_diagram(honda, found.matches[0].diagram).value
    assert len(rows) == 6 and len(diagram.hotspots) == 5
    store.close()
