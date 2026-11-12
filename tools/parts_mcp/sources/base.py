"""The interface every catalogue plug-in implements.

A plug-in module exposes `create_source(ctx: SourceContext) -> Source` and is
listed in `config.SOURCE_FACTORIES`. It gets its HTTP access from
`ctx.fetcher(...)`, so politeness, caching and challenge handling are shared.

Contract for all methods:
- Return records whose `source` equals `self.name`, with `url` and
  `fetched_at` from the page they came from (`FetchResult.fetched_at`).
- Records for a vehicle carry `vehicle_id == vehicle.id`.
- Unknown values are None. Never invent stock, origin, prices or discounts.
- Raise `parts_mcp.core.errors` types: NotSupported (cannot do this at all),
  NotFound, ParseError (layout changed). Fetch errors (ChallengeBlocked,
  NeedsHuman, DailyCapReached, ...) just propagate. The service catches
  everything, records it, and falls back to the next source when it can.
- Do not write to the catalogue store; the service saves what you return.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..core.fetch import ClearanceProvider, FetchPolicy, HttpTransport, PageCache, PoliteFetcher
from ..core.models import Diagram, Group, PartRow, PriceQuote, Vehicle

if TYPE_CHECKING:
    from ..config import Settings


class Source(ABC):
    #: Unique lower-case name, e.g. "microfiches". Used in keys and in the store.
    name: str = ""
    #: One line for list_sources.
    description: str = ""

    @abstractmethod
    def brands(self) -> list[str]:
        """Brands served, upper-case (e.g. ["BMW", "MINI"]). Must be cheap:
        static or cached, no network on every call."""

    @abstractmethod
    def find_vehicles(
        self, brand: str, model: str | None = None, year: int | None = None
    ) -> list[Vehicle]:
        """Catalogue vehicles matching brand (upper-case), model text and year.
        Return the leaf vehicles you can list groups for; several candidates
        are fine. Empty list if none. May raise NotSupported if `model` is
        required."""

    @abstractmethod
    def resolve_vin(self, vin: str) -> list[Vehicle]:
        """Vehicles for a VIN / frame number (usually 0 or 1). Set Vehicle.vin.
        Raise NotSupported if the source has no VIN search."""

    @abstractmethod
    def list_groups(self, vehicle: Vehicle) -> list[Group]:
        """Top-level catalogue groups of the vehicle (e.g. Bodywork, Lighting)."""

    @abstractmethod
    def list_diagrams(self, vehicle: Vehicle, group: Group) -> list[Diagram]:
        """All diagrams under the group, flattening any sub-levels (put their
        names in Diagram.group_path). Image and hotspots may be missing here."""

    @abstractmethod
    def get_diagram(self, vehicle: Vehicle, diagram: Diagram) -> tuple[Diagram, list[PartRow]]:
        """The complete diagram (image_url, hotspots) and its part rows.
        The returned Diagram keeps the same `id`; rows use `diagram_id ==
        diagram.id` and `row_index` 0..n-1 in page order. `diagram.source_ref`
        holds whatever you stored at list time."""

    @abstractmethod
    def get_price(self, part_number: str, brand: str) -> PriceQuote | None:
        """Reference price as the source shows it (no conversion). None if the
        source lists no price. Raise NotSupported if it never shows prices."""

    # Optional hooks for find_diagrams. The defaults add nothing, so a plug-in
    # only overrides them when it knows its catalogue's habits.

    def group_hints(self, vehicle: Vehicle, query: str) -> list[str]:
        """Names (or parts of names) of this catalogue's groups that usually
        hold diagrams for `query` on this vehicle, most likely first; e.g. a
        catalogue that files bumpers under "UPHOLSTERY". find_diagrams opens
        these groups first and uses them to break ties between diagrams; they
        never add a diagram by themselves. Must be cheap (no network)."""
        return []

    def diagram_matches(self, vehicle: Vehicle, query: str, diagram: Diagram) -> bool:
        """True if this catalogue's own naming says `diagram` shows `query`
        (e.g. unit names like "BUMPER, RR"). find_diagrams then rates
        the diagram at least 0.9. Must be cheap (no network)."""
        return False

    def close(self) -> None:
        """Release browsers / sessions. Default: nothing."""


@dataclass
class SourceContext:
    """What a plug-in factory receives."""

    settings: Settings
    page_cache: PageCache

    def policy(self, name: str) -> FetchPolicy:
        return self.settings.policies[name]

    def fetcher(
        self,
        policy: str | FetchPolicy,
        *,
        clearance_provider: ClearanceProvider | None = None,
        transport: HttpTransport | None = None,
    ) -> PoliteFetcher:
        """A fetcher sharing the page cache and cookie folder. One per policy
        is enough; it keeps separate throttling per host."""
        pol = self.policy(policy) if isinstance(policy, str) else policy
        return PoliteFetcher(
            pol,
            self.page_cache,
            transport=transport,
            clearance_provider=clearance_provider,
            cookie_dir=self.settings.cookie_dir,
        )
