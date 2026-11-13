"""Smoke test: start the real server over stdio with FakeSource and call it
through the official MCP client."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

PKG_PARENT = Path(__file__).resolve().parents[2]  # the folder that contains parts_mcp/

EXPECTED_TOOLS = {"list_sources", "find_vehicle", "list_groups", "list_diagrams", "find_diagrams",
                  "get_diagram", "search_parts", "get_price"}


async def _session(data_dir: Path) -> dict:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "parts_mcp", "--sources", "fake", "--data-dir", str(data_dir)],
        cwd=str(PKG_PARENT),
    )
    out: dict = {}
    async with Client(params, read_timeout_seconds=60) as client:
        out["tools"] = {t.name for t in (await client.list_tools()).tools}
        out["find"] = await client.call_tool("find_vehicle", {"brand": "BMW", "model": "3 Series", "year": 2016})
        key = out["find"].structured_content["vehicles"][0]["vehicle_key"]
        out["diagrams"] = await client.call_tool("find_diagrams", {"vehicle_key": key, "query": "rear bumper"})
        dkey = out["diagrams"].structured_content["matches"][0]["diagram_key"]
        out["diagram"] = await client.call_tool("get_diagram", {"diagram_key": dkey})
        out["again"] = await client.call_tool("get_diagram", {"diagram_key": dkey})
        out["bad"] = await client.call_tool("get_diagram", {"diagram_key": "fake:nope:nope"})
        out["sources"] = await client.call_tool("list_sources", {})
    return out


def test_stdio_find_vehicle_and_get_diagram(tmp_path):
    out = asyncio.run(_session(tmp_path / "data"))
    assert out["tools"] == EXPECTED_TOOLS

    found = out["find"]
    assert not found.is_error
    vehicle = found.structured_content["vehicles"][0]
    assert found.structured_content["answered_by"] == "fake"
    assert vehicle["brand"] == "BMW" and vehicle["url"] and vehicle["fetched_at"].endswith("Z")
    assert "source_ref" not in vehicle

    diagram = out["diagram"].structured_content
    assert not out["diagram"].is_error and diagram["from_store"] is False
    assert diagram["diagram"]["name"] == "Trim panel, rear bumper"
    assert diagram["diagram"]["image_url"].startswith("https://")
    assert len(diagram["diagram"]["hotspots"]) == 3
    part = diagram["parts"][0]
    assert part["part_number"] and part["source"] == "fake" and part["url"] and part["fetched_at"]
    assert part["name_original"] and part["qty"] == "1"
    assert out["again"].structured_content["from_store"] is True

    bad = out["bad"]
    assert bad.is_error and bad.structured_content["error"] == "not_found"

    sources = out["sources"].structured_content
    assert sources["sources"][0]["name"] == "fake"
    assert sources["store"]["part_rows"] == 3
    assert (tmp_path / "data" / "catalogue.db").exists()
    assert (tmp_path / "data" / "logs" / "parts_mcp.log").exists()
