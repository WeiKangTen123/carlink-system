"""Catalogue store: upserts, merge rule, diagram replacement, FTS, no import side effects."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from parts_mcp.core.models import Diagram, Group, Hotspot, PartRow, PriceQuote, Vehicle
from parts_mcp.core.store import build_fts_query, open_store

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
SRC = "microfiches"
VEH = "F30-320i"


def vehicle(**kw) -> Vehicle:
    base = dict(source=SRC, url="https://x/v", fetched_at=T0, id=VEH, brand="bmw", model="3 Series",
                year_from=2012, year_to=2019)
    return Vehicle(**{**base, **kw})


def diagram(did: str = "51_0010", **kw) -> Diagram:
    base = dict(source=SRC, url=f"https://x/d/{did}", fetched_at=T0, id=did, vehicle_id=VEH, group_id="51",
                name="Trim panel, rear bumper", name_original="Rivestimento paraurti posteriore")
    return Diagram(**{**base, **kw})


def row(i: int, name: str, original: str, pn: str, did: str = "51_0010", vid: str = VEH) -> PartRow:
    return PartRow(source=SRC, url=f"https://x/d/{did}", fetched_at=T0, vehicle_id=vid, diagram_id=did,
                   row_index=i, callout=str(i + 1), part_number=pn, name=name, name_original=original, qty="1")


@pytest.fixture
def store():
    s = open_store(":memory:")
    yield s
    s.close()


def test_open_store_creates_schema(tmp_path):
    path = tmp_path / "sub" / "catalogue.db"
    s = open_store(path)
    assert path.exists()
    assert s.stats() == {"vehicles": 0, "groups": 0, "diagrams": 0, "hotspots": 0, "part_rows": 0,
                         "price_quotes": 0, "lookups": 0}
    s.close()
    open_store(path).close()  # reopening is idempotent


def test_vehicle_upsert_updates_in_place_and_keeps_known_values(store):
    store.upsert_vehicles([vehicle(engine="N20", source_ref={"ssd": "a"})])
    store.upsert_vehicles([vehicle(fetched_at=T0 + timedelta(days=1), model="3er", engine=None,
                                   source_ref={"ssd": "b"})])
    assert store.stats()["vehicles"] == 1
    v = store.get_vehicle(SRC, VEH)
    assert v.model == "3er" and v.engine == "N20"  # None does not wipe a known value
    assert v.source_ref == {"ssd": "b"} and v.fetched_at == T0 + timedelta(days=1)
    assert v.brand == "BMW"
    assert [x.id for x in store.find_vehicles("BMW", "3er", 2015)] == [VEH]
    assert store.find_vehicles("BMW", year=2021) == []


def test_groups_and_diagram_listing_merge(store):
    store.upsert_groups([Group(source=SRC, url="https://x/g", fetched_at=T0, id="51", vehicle_id=VEH,
                               name="Vehicle trim")])
    assert store.get_groups(SRC, VEH)[0].name == "Vehicle trim"
    full = diagram(image_url="https://x/img.png",
                   hotspots=[Hotspot(source=SRC, url="https://x/d", fetched_at=T0, callout="1", x=1, y=2)])
    store.save_diagram_detail(full, [])
    store.upsert_diagrams([diagram()])  # a later listing without image must not erase it
    d = store.get_diagram(SRC, VEH, "51_0010")
    assert d.image_url == "https://x/img.png"
    assert [h.callout for h in d.hotspots] == ["1"]
    assert [x.id for x in store.get_diagrams(SRC, VEH, group_id="51")] == ["51_0010"]


def test_diagram_detail_replaces_rows_and_fts_follows(store):
    store.save_diagram_detail(diagram(), [
        row(0, "Trim panel, bumper, rear", "Rivestimento paraurti posteriore", "51 12 7 293 146"),
        row(1, "Reflector, rear left", "Catadiottro posteriore sinistro", "51 12 7 263 617"),
    ])
    assert len(store.search_parts("reflector", vehicle_id=VEH)) == 1
    store.save_diagram_detail(diagram(), [
        row(0, "Trim panel, bumper, rear", "Rivestimento paraurti posteriore", "51 12 7 293 146"),
    ])
    assert store.search_parts("reflector", vehicle_id=VEH) == []
    assert len(store.get_part_rows(SRC, VEH, "51_0010")) == 1
    assert store.stats()["part_rows"] == 1


def test_fts_search_names_prefix_italian_and_scope(store):
    store.save_diagram_detail(diagram(), [
        row(0, "Trim panel, bumper, rear", "Rivestimento paraurti posteriore", "51127293146"),
        row(1, "Reflector, rear left", "Catadiottro posteriore sinistro", "51127263617"),
    ])
    other = diagram("51_0010", vehicle_id="G20")
    store.save_diagram_detail(other, [row(0, "Reflector, rear left", "x", "1", vid="G20")])

    assert [r.row_index for r in store.search_parts("bump", vehicle_id=VEH)] == [0]  # prefix
    assert [r.row_index for r in store.search_parts("catadiottro", vehicle_id=VEH)] == [1]  # Italian
    assert len(store.search_parts("reflector")) == 2  # unscoped
    assert len(store.search_parts("reflector", vehicle_id=VEH)) == 1  # scoped to the vehicle
    assert len(store.search_parts(["tailgate", "reflector rear"], vehicle_id=VEH)) == 1  # any phrase
    assert store.search_parts('"; DROP TABLE part_rows; --') == []  # quoting is safe
    assert [r.row_index for r in store.find_part_rows("51-12-7-263-617", vehicle_id=VEH)] == [1]


def test_build_fts_query():
    assert build_fts_query("rear bumper") == '("rear"* AND "bumper"*)'
    assert build_fts_query(["trunk lid", "tailgate"]) == '("trunk"* AND "lid"*) OR ("tailgate"*)'
    assert build_fts_query("!!") is None


def test_prices_and_lookups(store):
    store.upsert_price(PriceQuote(source=SRC, url="https://x/p", fetched_at=T0, part_number="51 12 7 293 146",
                                  brand="bmw", amount=312.4, currency="EUR", tax_note="excl. VAT"))
    q = store.get_price(SRC, "51127293146", "BMW")
    assert q.amount == 312.4 and q.currency == "EUR" and q.availability is None
    assert store.get_price(SRC, "51127293146", None) is None

    store.record_lookup(SRC, "groups", VEH, ["51", "63"], T0)
    lk = store.get_lookup(SRC, "groups", VEH)
    assert lk.ids == ["51", "63"] and lk.fetched_at == T0
    store.record_lookup(SRC, "groups", VEH, [], T0 + timedelta(days=1))
    assert store.get_lookup(SRC, "groups", VEH).ids == []


def test_record_rules():
    with pytest.raises(ValueError):
        vehicle(fetched_at=datetime(2026, 1, 1))  # naive datetime rejected
    v = vehicle(source_ref={"ssd": "secret"})
    assert "source_ref" not in v.to_dict(include_source_ref=False)
    assert Vehicle.from_dict(v.to_dict()) == v
    d = diagram(hotspots=[Hotspot(source=SRC, url="u", fetched_at=T0, callout="1", source_ref={"a": 1})])
    assert Diagram.from_dict(d.to_dict()) == d
    assert "source_ref" not in d.to_dict(include_source_ref=False)["hotspots"][0]


def test_importing_package_writes_nothing(tmp_path):
    data_dir = tmp_path / "data_should_not_exist"
    code = ("import parts_mcp, parts_mcp.config, parts_mcp.core.store, parts_mcp.core.fetch, "
            "parts_mcp.service, parts_mcp.server, parts_mcp.sources.fake, parts_mcp.sources.registry")
    env = {**os.environ, "PARTS_MCP_DATA_DIR": str(data_dir), "PYTHONDONTWRITEBYTECODE": "1"}
    pkg_parent = Path(__file__).resolve().parents[2]
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env={**env, "PYTHONPATH": str(pkg_parent)},
                   check=True)
    assert not data_dir.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == []
