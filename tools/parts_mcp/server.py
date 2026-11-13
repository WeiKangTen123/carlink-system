"""MCP server over stdio, on the official SDK (mcp 2.x `MCPServer`).

Run:  python -m parts_mcp [--data-dir DIR] [--sources microfiches]
Never print to stdout here: stdout carries the MCP protocol. Logs go to
stderr and to <data>/logs/parts_mcp.log.
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import logging.handlers
import sys
from typing import Annotated, Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from .config import Settings, load_settings
from .core.errors import PartsError
from .core.fetch import PageCache
from .core.models import Diagram, Group, PartRow, PriceQuote, Vehicle
from .core.store import open_store
from .service import CatalogueService
from .sources.registry import load_sources

log = logging.getLogger("parts_mcp.server")

INSTRUCTIONS = """\
OEM parts catalogue lookup for motor-claims surveys (Singapore).
Typical flow: find_vehicle -> find_diagrams (e.g. "rear bumper") -> get_diagram
-> optionally get_price. list_groups / list_diagrams browse the tree instead.
Keys returned by one tool (vehicle_key, group_key, diagram_key) are the inputs
of the next; pass them back unchanged.
Every record carries provenance: source, url, fetched_at (UTC). A value the
catalogue does not show is null; never fill it in. Prices are as shown by the
source (currency and tax note included), never converted.
Results are cached locally; `from_store: true` means no website was contacted.
Live lookups are deliberately slow (several seconds per page) to be polite.
Errors come back as {"error": code, "message": ..., "source": ...}; for
"challenge_blocked", "needs_human" or "daily_cap_reached" do not retry soon.
"""


# --- output shaping ----------------------------------------------------------------


def vehicle_out(v: Vehicle) -> dict[str, Any]:
    return {"vehicle_key": v.key, **v.to_dict(include_source_ref=False)}


def group_out(g: Group) -> dict[str, Any]:
    return {"group_key": g.key, **g.to_dict(include_source_ref=False)}


def diagram_out(d: Diagram, *, with_hotspots: bool = True) -> dict[str, Any]:
    data = d.to_dict(include_source_ref=False)
    if not with_hotspots:
        data.pop("hotspots", None)
    return {"diagram_key": d.key, **data}


def row_out(r: PartRow) -> dict[str, Any]:
    return r.to_dict(include_source_ref=False)


def price_out(q: PriceQuote) -> dict[str, Any]:
    return q.to_dict(include_source_ref=False)


def _result(payload: dict[str, Any], *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
        is_error=is_error,
    )


def _guard(fn: Callable[..., dict[str, Any]]) -> Callable[..., CallToolResult]:
    """Turn our errors into structured tool errors instead of tracebacks."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> CallToolResult:
        try:
            return _result(fn(*args, **kwargs))
        except PartsError as err:
            return _result(err.to_dict(), is_error=True)
        except ValueError as err:
            return _result({"error": "bad_request", "message": str(err)}, is_error=True)
        except Exception as err:
            log.exception("tool %s crashed", fn.__name__)
            return _result({"error": "internal_error", "message": f"{type(err).__name__}: {err}"}, is_error=True)

    # The SDK reads the wrapped signature (via __wrapped__) for the input and
    # output schemas; a returned CallToolResult passes through as-is.
    return wrapper


# --- the server ----------------------------------------------------------------------


def create_server(service: CatalogueService, settings: Settings | None = None) -> MCPServer:
    mcp = MCPServer(name="carlink-parts", instructions=INSTRUCTIONS, version="0.1.0")

    @mcp.tool(description="List the catalogue sources (loaded or not), the brands each serves, "
              "which source is tried first per brand, and how much is cached locally.")
    @_guard
    def list_sources() -> dict[str, Any]:
        out: dict[str, Any] = {
            "sources": service.list_sources(),
            "routes": service.routes(),
            "store": service.store.stats(),
        }
        if settings is not None:
            out["policies"] = {
                name: {"ua_mode": p.ua_mode, "delay_seconds": [p.min_delay, p.max_delay],
                       "daily_cap_per_host": p.daily_cap}
                for name, p in settings.policies.items()
            }
            out["data_dir"] = str(settings.data_dir)
        return out

    @mcp.tool(description="Find catalogue vehicles by VIN/frame number, or by brand with optional model "
              "and year (e.g. brand='BMW', model='3 Series', year=2016). Returns candidates, each with a "
              "vehicle_key for the other tools. Pick the one matching the car (chassis, engine, body, "
              "production dates); ask the user if several fit.")
    @_guard
    def find_vehicle(
        brand: Annotated[str | None, Field(description="Make, e.g. 'BMW', 'MINI', 'Honda'.")] = None,
        model: Annotated[str | None, Field(description="Model or series text, e.g. '3 Series', 'Civic', 'F30'.")] = None,
        year: Annotated[int | None, Field(description="Model or registration year, e.g. 2018.")] = None,
        vin: Annotated[str | None, Field(description="17-char VIN or frame number. Preferred when known.")] = None,
        limit: Annotated[int, Field(description="Max vehicles returned.", ge=1, le=200)] = 25,
    ) -> dict[str, Any]:
        answer = service.find_vehicles(brand=brand, model=model, year=year, vin=vin)
        return {
            **answer.meta(),
            "total": len(answer.value),
            "vehicles": [vehicle_out(v) for v in answer.value[:limit]],
        }

    @mcp.tool(description="List the top-level catalogue groups of a vehicle (e.g. Bodywork, Lighting, "
              "Engine), each with a group_key for list_diagrams.")
    @_guard
    def list_groups(
        vehicle_key: Annotated[str, Field(description="vehicle_key from find_vehicle.")],
    ) -> dict[str, Any]:
        vehicle = service.vehicle(vehicle_key)
        answer = service.list_groups(vehicle)
        return {**answer.meta(), "vehicle": vehicle_out(vehicle), "groups": [group_out(g) for g in answer.value]}

    @mcp.tool(description="List all exploded diagrams in one catalogue group, each with a diagram_key for "
              "get_diagram. Prefer find_diagrams when you know what part area you want.")
    @_guard
    def list_diagrams(
        group_key: Annotated[str, Field(description="group_key from list_groups.")],
    ) -> dict[str, Any]:
        vehicle, group = service.group(group_key)
        answer = service.list_diagrams(vehicle, group)
        return {
            **answer.meta(),
            "group": group_out(group),
            "diagrams": [diagram_out(d, with_hotspots=False) for d in answer.value],
        }

    @mcp.tool(description="Find the diagrams for a damaged area of a vehicle by plain words, e.g. "
              "'rear bumper', 'tailgate', 'headlamp left', 'spare tyre panel'. Understands common "
              "synonyms (trunk lid = boot lid = back door = tailgate; bonnet = hood; mudflap = mudguard; "
              "anti-roll bar = stabilizer, found under KNUCKLE in Honda catalogues), British spellings "
              "and the Italian names some sites use. Only the diagram's own name is scored; diagrams "
              "that usually hold parts of the area (e.g. EMBLEMS for a tailgate) score 0.8. Only groups "
              "that fit the words are opened, at most max_new_groups not seen before (each costs a slow "
              "live request). Returns diagrams with a score 0..1 and the reason, best first; 0.5 or more "
              "is a real match.")
    @_guard
    def find_diagrams(
        vehicle_key: Annotated[str, Field(description="vehicle_key from find_vehicle.")],
        query: Annotated[str, Field(description="Damaged area or part, in plain words.")],
        max_new_groups: Annotated[int, Field(description="Max groups to open that are not cached yet.", ge=0, le=10)] = 3,
        limit: Annotated[int, Field(description="Max diagrams returned.", ge=1, le=50)] = 10,
    ) -> dict[str, Any]:
        vehicle = service.vehicle(vehicle_key)
        found = service.find_diagrams(vehicle, query, max_new_groups=max_new_groups, limit=limit)
        out: dict[str, Any] = {
            "answered_by": found.source,
            "failures": found.failures,
            "vehicle": vehicle_out(vehicle),
            "matches": [
                {"score": m.score, "why": m.why, "group": m.group.name if m.group else None,
                 **diagram_out(m.diagram, with_hotspots=False)}
                for m in found.matches
            ],
            "searched_groups": [g.name for g in found.searched_groups],
            "skipped_groups": [{"name": g.name, "group_key": g.key} for g in found.skipped_groups],
        }
        if not found.matches:
            out["hint"] = ("No diagram name matched. Try other words, raise max_new_groups, or browse "
                           "with list_diagrams on a skipped group.")
        return out

    @mcp.tool(description="Get one exploded diagram: image URL, hotspots (callout numbers with pixel "
              "boxes on the image) and the parts table (callout, OEM part number, superseded number, "
              "name in English and as shown on the site, qty as shown, notes, side). Every row has "
              "source, url and fetched_at.")
    @_guard
    def get_diagram(
        diagram_key: Annotated[str, Field(description="diagram_key from find_diagrams or list_diagrams.")],
    ) -> dict[str, Any]:
        vehicle, diagram = service.diagram(diagram_key)
        answer = service.get_diagram(vehicle, diagram)
        full, rows = answer.value
        return {
            **answer.meta(),
            "vehicle": vehicle_out(vehicle),
            "diagram": diagram_out(full),
            "parts": [row_out(r) for r in rows],
        }

    @mcp.tool(description="Full-text search over part rows already fetched for this vehicle (only "
              "diagrams opened with get_diagram are searchable). Matches English and original names, "
              "part numbers (spaces/dashes ignored) and notes, with synonyms. Use find_diagrams + "
              "get_diagram first for areas not yet opened.")
    @_guard
    def search_parts(
        vehicle_key: Annotated[str, Field(description="vehicle_key from find_vehicle.")],
        query: Annotated[str, Field(description="Words or a part number, e.g. 'reflector', '51127293146'.")],
        limit: Annotated[int, Field(description="Max rows returned.", ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        vehicle = service.vehicle(vehicle_key)
        rows = service.search_parts(vehicle, query, limit=limit)
        diagrams: dict[str, Diagram | None] = {}
        parts = []
        for r in rows:
            if r.diagram_id not in diagrams:
                diagrams[r.diagram_id] = service.store.get_diagram(r.source, r.vehicle_id, r.diagram_id)
            d = diagrams[r.diagram_id]
            parts.append({**row_out(r), "diagram_key": d.key if d else None, "diagram_name": d.name if d else None})
        out: dict[str, Any] = {"answered_by": "store", "from_store": True, "vehicle": vehicle_out(vehicle), "parts": parts}
        if not parts:
            out["hint"] = "Nothing stored matches. Open the area first with find_diagrams and get_diagram."
        return out

    @mcp.tool(description="Reference price for an OEM part number, exactly as a catalogue shows it "
              "(amount, currency, tax note, availability if shown; null when not shown). Prices are "
              "not converted. Give brand, or a vehicle_key to take the brand from. all_sources=true "
              "asks every source for that brand to compare.")
    @_guard
    def get_price(
        part_number: Annotated[str, Field(description="OEM part number; spaces and dashes are ignored.")],
        brand: Annotated[str | None, Field(description="Make, e.g. 'BMW'.")] = None,
        vehicle_key: Annotated[str | None, Field(description="Alternative to brand.")] = None,
        all_sources: Annotated[bool, Field(description="Ask every source, not just the first that has a price.")] = False,
    ) -> dict[str, Any]:
        if brand is None and vehicle_key:
            brand = service.vehicle(vehicle_key).brand
        if not brand:
            raise ValueError("give brand or vehicle_key")
        answer = service.get_price(part_number, brand, all_sources=all_sources)
        out: dict[str, Any] = {**answer.meta(), "part_number": part_number, "brand": brand,
                               "quotes": [price_out(q) for q in answer.value]}
        if not answer.value:
            out["hint"] = "No source listed a price for this part number."
        return out

    return mcp


# --- startup ------------------------------------------------------------------------------


def setup_logging(settings: Settings, level: int = logging.INFO) -> None:
    logger = logging.getLogger("parts_mcp")
    logger.setLevel(level)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    for handler in (
        logging.StreamHandler(sys.stderr),
        logging.handlers.RotatingFileHandler(
            settings.log_dir / "parts_mcp.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        ),
    ):
        handler.setFormatter(fmt)
        logger.addHandler(handler)


def build_service(settings: Settings) -> CatalogueService:
    settings.ensure_dirs()
    page_cache = PageCache(settings.page_cache_db)
    store = open_store(settings.catalogue_db)
    sources, problems = load_sources(settings, page_cache)
    return CatalogueService(
        store,
        sources,
        routes=settings.routes,
        default_route=settings.default_route,
        catalogue_ttl=settings.catalogue_ttl,
        price_ttl=settings.price_ttl,
        unavailable=problems,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m parts_mcp", description=__doc__)
    parser.add_argument("--data-dir", help="data folder (default parts_mcp/data or $PARTS_MCP_DATA_DIR)")
    parser.add_argument("--sources", help="comma list, e.g. microfiches (or 'fake' to try it out)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    sources = tuple(s.strip() for s in args.sources.split(",") if s.strip()) if args.sources else None
    settings = load_settings(data_dir=args.data_dir, sources=sources)
    settings.ensure_dirs()
    setup_logging(settings, logging.DEBUG if args.debug else logging.INFO)
    service = build_service(settings)
    log.info("starting: sources=%s unavailable=%s data=%s",
             list(service.sources), service.unavailable, settings.data_dir)
    try:
        create_server(service, settings).run("stdio")
    finally:
        service.close()
