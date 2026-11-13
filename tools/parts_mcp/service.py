"""CatalogueService: choose sources by brand, read through the store.

Read-through: every question (find vehicles, list groups, a diagram, a
price...) is remembered in the store's `lookups` table with the ids it
returned and when. A fresh lookup is answered from the store; otherwise the
source is asked, its records saved, and the lookup refreshed. If the source
fails and an older answer exists, that answer is returned marked `stale`.

Brand-level questions (find vehicles, VIN, price) fall back to the next
source on failure or empty answer. Vehicle-level questions go to the source
the vehicle came from (ids are per source).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Generic, Iterable, Mapping, Sequence, TypeVar

from . import synonyms
from .core.errors import AllSourcesFailed, NotFound, NotSupported, PartsError, SourceError, UnknownSource
from .core.fetch import TTL_CATALOGUE, TTL_PRICE
from .core.models import (
    Diagram,
    Group,
    PartRow,
    PriceQuote,
    Record,
    Vehicle,
    make_key,
    normalize_brand,
    normalize_part_number,
    split_key,
    utcnow,
)
from .core.store import CatalogueStore
from .sources.base import Source

log = logging.getLogger("parts_mcp.service")

T = TypeVar("T")

# VIN prefix -> brand. Only used to pick which source to ask first, never reported.
WMI_BRANDS = {
    "WBA": "BMW", "WBS": "BMW", "WBY": "BMW", "WBX": "BMW", "5UX": "BMW", "5YM": "BMW",
    "WMW": "MINI", "WMZ": "MINI",
    "JHM": "HONDA", "JHL": "HONDA", "JHG": "HONDA", "SHH": "HONDA", "SHS": "HONDA",
    "1HG": "HONDA", "2HG": "HONDA", "5FN": "HONDA", "5J6": "HONDA", "19X": "HONDA", "MRH": "HONDA",
}


@dataclass
class Answer(Generic[T]):
    value: T
    source: str | None  # which source answered; None if no source had anything
    from_store: bool  # True: served from the local store without contacting the source
    stale: bool = False  # True: store copy past its TTL, served because the source failed
    failures: list[dict[str, Any]] = field(default_factory=list)  # sources that failed on the way

    def meta(self) -> dict[str, Any]:
        return {
            "answered_by": self.source,
            "from_store": self.from_store,
            "stale": self.stale,
            "failures": self.failures,
        }


@dataclass
class DiagramMatch:
    diagram: Diagram
    group: Group | None
    score: float  # 0..1, from synonyms.score
    why: str


@dataclass
class DiagramSearch:
    matches: list[DiagramMatch]
    searched_groups: list[Group]  # groups whose diagrams were compared
    skipped_groups: list[Group]  # groups not opened (no name match, or over max_new_groups)
    source: str
    failures: list[dict[str, Any]] = field(default_factory=list)


def _oldest(records: Iterable[Record]) -> datetime | None:
    times = [r.fetched_at for r in records]
    return min(times) if times else None


class CatalogueService:
    def __init__(
        self,
        store: CatalogueStore,
        sources: Sequence[Source],
        *,
        routes: Mapping[str, Sequence[str]] | None = None,
        default_route: Sequence[str] = (),
        catalogue_ttl: float = TTL_CATALOGUE,
        price_ttl: float = TTL_PRICE,
        now: Callable[[], datetime] = utcnow,
        unavailable: Mapping[str, str] | None = None,
    ) -> None:
        self.store = store
        self._sources = {s.name: s for s in sources}
        self._routes = {normalize_brand(b): tuple(names) for b, names in (routes or {}).items()}
        self._default_route = tuple(default_route)
        self.catalogue_ttl = catalogue_ttl
        self.price_ttl = price_ttl
        self._now = now
        self.unavailable = dict(unavailable or {})  # configured sources that failed to load
        for s in sources:
            store.register_source(s.name, s.description or None)

    # --- sources and routing --------------------------------------------------------

    @property
    def sources(self) -> dict[str, Source]:
        return dict(self._sources)

    def source(self, name: str) -> Source:
        try:
            return self._sources[name]
        except KeyError:
            raise UnknownSource(f"source {name!r} is not loaded", source=name) from None

    def _serves(self, src: Source, brand: str) -> bool:
        try:
            brands = src.brands()
        except Exception:
            return True  # let it try; a failure is recorded later
        return not brands or normalize_brand(brand) in brands

    def route(self, brand: str | None) -> list[Source]:
        """Sources to try for a brand, in order."""
        if brand is None:
            names = self._default_route + tuple(n for n in self._sources if n not in self._default_route)
            return [self._sources[n] for n in names if n in self._sources]
        names = self._routes.get(normalize_brand(brand), self._default_route)
        chosen = [self._sources[n] for n in names if n in self._sources and self._serves(self._sources[n], brand)]
        if not chosen:  # configured route not loaded: any loaded source that serves the brand
            chosen = [s for s in self._sources.values() if self._serves(s, brand)]
        return chosen

    def list_sources(self) -> list[dict[str, Any]]:
        out = []
        for name, src in self._sources.items():
            try:
                brands: list[str] | None = src.brands()
            except Exception as exc:
                brands = None
                log.warning("brands() failed for %s: %s", name, exc)
            out.append({"name": name, "status": "loaded", "description": src.description, "brands": brands})
        for name, why in self.unavailable.items():
            out.append({"name": name, "status": "unavailable", "reason": why})
        return out

    def routes(self) -> dict[str, list[str]]:
        table = {brand: list(names) for brand, names in self._routes.items()}
        table["*"] = list(self._default_route)
        return table

    # --- read-through core ----------------------------------------------------------

    def _age(self, at: datetime) -> float:
        return (self._now() - at).total_seconds()

    @staticmethod
    def _as_error(src: Source, exc: BaseException) -> PartsError:
        if isinstance(exc, PartsError):
            if exc.source is None:
                exc.source = src.name
            return exc
        err = SourceError(f"{type(exc).__name__}: {exc}", source=src.name)
        err.__cause__ = exc
        return err

    def _read_through(
        self,
        src: Source,
        kind: str,
        query: str,
        ttl: float,
        live: Callable[[], tuple[list[str], datetime | None]],
        load: Callable[[list[str]], T],
    ) -> Answer[T]:
        """`live()` asks the source, saves, returns (ids, oldest fetched_at);
        `load(ids)` reads the answer back from the store."""
        cached = self.store.get_lookup(src.name, kind, query)
        if cached is not None and self._age(cached.fetched_at) <= ttl:
            return Answer(load(cached.ids), src.name, from_store=True)
        try:
            ids, oldest = live()
        except Exception as exc:
            err = self._as_error(src, exc)
            if not isinstance(exc, PartsError):
                log.exception("source %s crashed on %s %s", src.name, kind, query)
            if cached is not None:
                log.warning("serving stale %s %s from store: %s", kind, query, err.code)
                return Answer(load(cached.ids), src.name, from_store=True, stale=True, failures=[err.to_dict()])
            if err is exc:
                raise
            raise err from exc
        # Date the answer by its oldest page, so a page cache hit does not look new.
        self.store.record_lookup(src.name, kind, query, ids, oldest or self._now())
        return Answer(load(ids), src.name, from_store=False)

    def _first_answer(
        self,
        sources: Sequence[Source],
        kind: str,
        query: str,
        ttl: float,
        live: Callable[[Source], tuple[list[str], datetime | None]],
        load: Callable[[Source, list[str]], list[T]],
        *,
        collect_all: bool = False,
    ) -> Answer[list[T]]:
        """Try sources in order; first non-empty answer wins (or all, if collect_all)."""
        if not sources:
            raise NotSupported(f"no loaded source serves this ({kind})")
        failures: list[dict[str, Any]] = []
        errors: list[PartsError] = []
        answered: list[Answer[list[T]]] = []
        for src in sources:
            try:
                ans = self._read_through(
                    src, kind, query, ttl, lambda s=src: live(s), lambda ids, s=src: load(s, ids)
                )
            except PartsError as err:
                failures.append(err.to_dict())
                errors.append(err)
                continue
            failures.extend(ans.failures)
            if ans.value:
                answered.append(ans)
                if not collect_all:
                    break
        if answered:
            value = [item for a in answered for item in a.value]
            return Answer(
                value,
                ",".join(a.source or "" for a in answered),
                from_store=all(a.from_store for a in answered),
                stale=any(a.stale for a in answered),
                failures=failures,
            )
        if len(errors) == len(sources):  # every source failed, none answered empty
            if len(errors) == 1:
                raise errors[0]
            raise AllSourcesFailed(failures)
        return Answer([], None, from_store=False, failures=failures)

    def _check(self, records: Iterable[Record], src: Source, vehicle_id: str | None = None) -> None:
        for r in records:
            if r.source != src.name:
                raise SourceError(f"record source {r.source!r} != {src.name!r}", source=src.name)
            if vehicle_id is not None and getattr(r, "vehicle_id", vehicle_id) != vehicle_id:
                raise SourceError(f"record for vehicle {r.vehicle_id!r}, expected {vehicle_id!r}", source=src.name)  # type: ignore[attr-defined]

    # --- vehicles -------------------------------------------------------------------

    def find_vehicles(
        self,
        brand: str | None = None,
        model: str | None = None,
        year: int | None = None,
        vin: str | None = None,
        *,
        sources: Sequence[str] | None = None,
    ) -> Answer[list[Vehicle]]:
        """By VIN (brand optional) or by brand + optional model/year.

        `sources` asks only those sources, in that order, instead of the brand's
        route (the catalogue worker tries one source at a time)."""
        if vin:
            return self.resolve_vin(vin, brand)
        if not brand:
            raise ValueError("give a brand (and ideally model/year) or a VIN")
        brand = normalize_brand(brand)
        query = make_key(brand, (model or "").strip().lower(), "" if year is None else str(year))

        def live(src: Source) -> tuple[list[str], datetime | None]:
            found = src.find_vehicles(brand, model, year)
            self._check(found, src)
            self.store.upsert_vehicles(found)
            return [v.id for v in found], _oldest(found)

        route = [self.source(name) for name in sources] if sources is not None else self.route(brand)
        return self._first_answer(
            route, "vehicles", query, self.catalogue_ttl, live,
            lambda src, ids: self.store.get_vehicles(src.name, ids),
        )

    def resolve_vin(self, vin: str, brand: str | None = None) -> Answer[list[Vehicle]]:
        vin = "".join(vin.split()).upper()
        if not vin.replace("-", "").isalnum():
            raise ValueError(f"not a VIN / frame number: {vin!r}")
        brand = brand or WMI_BRANDS.get(vin[:3])

        def live(src: Source) -> tuple[list[str], datetime | None]:
            found = src.resolve_vin(vin)
            self._check(found, src)
            self.store.upsert_vehicles(found)
            return [v.id for v in found], _oldest(found)

        return self._first_answer(
            self.route(brand), "vin", vin, self.catalogue_ttl, live,
            lambda src, ids: self.store.get_vehicles(src.name, ids),
        )

    # --- keys -----------------------------------------------------------------------

    def vehicle(self, key: str) -> Vehicle:
        parts = split_key(key)
        found = self.store.get_vehicle(parts[0], parts[1]) if len(parts) == 2 else None
        if found is None:
            raise NotFound(f"unknown vehicle_key {key!r}; call find_vehicle first")
        return found

    def group(self, key: str) -> tuple[Vehicle, Group]:
        parts = split_key(key)
        found = self.store.get_group(*parts) if len(parts) == 3 else None
        if found is None:
            raise NotFound(f"unknown group_key {key!r}; call list_groups first")
        return self.vehicle(make_key(parts[0], parts[1])), found

    def diagram(self, key: str) -> tuple[Vehicle, Diagram]:
        parts = split_key(key)
        found = self.store.get_diagram(*parts) if len(parts) == 3 else None
        if found is None:
            raise NotFound(f"unknown diagram_key {key!r}; call find_diagrams or list_diagrams first")
        return self.vehicle(make_key(parts[0], parts[1])), found

    # --- catalogue tree ---------------------------------------------------------------

    def list_groups(self, vehicle: Vehicle) -> Answer[list[Group]]:
        src = self.source(vehicle.source)

        def live() -> tuple[list[str], datetime | None]:
            groups = src.list_groups(vehicle)
            self._check(groups, src, vehicle.id)
            self.store.upsert_groups(groups)
            return [g.id for g in groups], _oldest(groups)

        return self._read_through(
            src, "groups", make_key(vehicle.id), self.catalogue_ttl, live,
            lambda ids: self.store.get_groups(src.name, vehicle.id, ids),
        )

    def list_diagrams(self, vehicle: Vehicle, group: Group) -> Answer[list[Diagram]]:
        src = self.source(vehicle.source)

        def live() -> tuple[list[str], datetime | None]:
            diagrams = src.list_diagrams(vehicle, group)
            self._check(diagrams, src, vehicle.id)
            self.store.upsert_diagrams(diagrams)
            return [d.id for d in diagrams], _oldest(diagrams)

        return self._read_through(
            src, "diagrams", make_key(vehicle.id, group.id), self.catalogue_ttl, live,
            lambda ids: self.store.get_diagrams(src.name, vehicle.id, ids=ids),
        )

    def has_fresh_diagrams(self, vehicle: Vehicle, group: Group) -> bool:
        cached = self.store.get_lookup(vehicle.source, "diagrams", make_key(vehicle.id, group.id))
        return cached is not None and self._age(cached.fetched_at) <= self.catalogue_ttl

    def get_diagram(self, vehicle: Vehicle, diagram: Diagram) -> Answer[tuple[Diagram, list[PartRow]]]:
        src = self.source(vehicle.source)

        def live() -> tuple[list[str], datetime | None]:
            full, rows = src.get_diagram(vehicle, diagram)
            self._check([full, *full.hotspots, *rows], src, vehicle.id)
            if full.id != diagram.id:
                raise SourceError(f"get_diagram returned id {full.id!r} for {diagram.id!r}", source=src.name)
            self.store.save_diagram_detail(full, rows)
            return [full.id], _oldest([full, *rows])

        def load(ids: list[str]) -> tuple[Diagram, list[PartRow]]:
            stored = self.store.get_diagram(src.name, vehicle.id, diagram.id)
            if stored is None:
                raise NotFound(f"diagram {diagram.id!r} missing from store", source=src.name)
            return stored, self.store.get_part_rows(src.name, vehicle.id, diagram.id)

        return self._read_through(
            src, "diagram", make_key(vehicle.id, diagram.id), self.catalogue_ttl, live, load
        )

    # --- search -----------------------------------------------------------------------

    def find_diagrams(
        self,
        vehicle: Vehicle,
        query: str,
        *,
        max_new_groups: int = 3,
        limit: int = 10,
        min_score: float = 0.25,
    ) -> DiagramSearch:
        """Diagrams whose name matches the query, using the synonym table.

        Only the diagram's own name is scored. The group decides which groups
        are opened and breaks ties, never the score: "exhaust" must not lift
        every diagram of an "ELECTRICAL EQUIPMENTS, EXHAUST, HEATER" group.

        Groups already listed are searched for free. Of the others, at most
        `max_new_groups` are opened (each costs a live request), best first by
        group name, the synonym table's group hints and the source's own
        `group_hints`; groups that match none of these are not opened.
        Equal scores are ordered by how close the matching phrase is to the
        query (see `synonyms.expand`), then by group rank, then catalogue order.
        """
        src = self.source(vehicle.source)
        groups_answer = self.list_groups(vehicle)
        failures = list(groups_answer.failures)
        expansion = synonyms.expand(query)
        try:
            hints = list(src.group_hints(vehicle, query))
        except Exception:
            log.exception("group_hints failed for %s", src.name)
            hints = []

        ranks: dict[str, float] = {}

        def group_rank(g: Group) -> float:
            if g.key not in ranks:
                text = f"{g.name} {g.name_original or ''}"
                ranks[g.key] = max(
                    synonyms.score(query, text, expansion, specific=False)[0],
                    synonyms.group_hint_score(query, text),
                    synonyms.source_hint_score(hints, g.name),
                )
            return ranks[g.key]

        known = [g for g in groups_answer.value if self.has_fresh_diagrams(vehicle, g)]
        known_keys = {g.key for g in known}
        others = sorted(
            (g for g in groups_answer.value if g.key not in known_keys and group_rank(g) > 0),
            key=group_rank,
            reverse=True,
        )
        to_search = known + others[: max(0, max_new_groups)]
        searched: list[Group] = []
        found: list[tuple[tuple[float, int, float, int], DiagramMatch]] = []
        for g in to_search:
            try:
                diagrams = self.list_diagrams(vehicle, g)
            except PartsError as err:
                failures.append(err.to_dict())
                continue
            failures.extend(diagrams.failures)
            searched.append(g)
            for d in diagrams.value:
                best = synonyms.Rating(0.0, "", 0)
                for name in filter(None, (d.name, d.name_original)):
                    r = synonyms.rate(query, name, expansion)
                    if r.score > best.score:
                        best = r
                if best.score < 0.9 and self._source_says(src, vehicle, query, d):
                    best = synonyms.Rating(0.9, f"{src.name} naming pattern", 0)
                if best.score >= min_score:
                    s = round(best.score, 3)
                    order = (s, -best.rank, group_rank(g), -len(found))
                    found.append((order, DiagramMatch(d, g, s, best.why)))
        found.sort(key=lambda item: item[0], reverse=True)
        searched_keys = {g.key for g in searched}
        skipped = [g for g in groups_answer.value if g.key not in searched_keys]
        return DiagramSearch([m for _, m in found[:limit]], searched, skipped, vehicle.source, failures)

    @staticmethod
    def _source_says(src: Source, vehicle: Vehicle, query: str, diagram: Diagram) -> bool:
        try:
            return bool(src.diagram_matches(vehicle, query, diagram))
        except Exception:
            log.exception("diagram_matches failed for %s", src.name)
            return False

    def search_parts(self, vehicle: Vehicle, query: str, *, limit: int = 20) -> list[PartRow]:
        """Search part rows already stored for this vehicle (diagrams fetched
        with get_diagram). Matches names (English and original), numbers, notes."""
        hits: list[PartRow] = []
        norm = normalize_part_number(query)
        if len(norm) >= 5 and any(c.isdigit() for c in norm):
            hits += self.store.find_part_rows(query, source=vehicle.source, vehicle_id=vehicle.id, limit=limit)
        phrases = [p for p, _, _ in synonyms.expand(query)]
        hits += self.store.search_parts(phrases, source=vehicle.source, vehicle_id=vehicle.id, limit=limit)
        seen: set[tuple[str, int]] = set()
        unique = []
        for row in hits:
            if (row.diagram_id, row.row_index) not in seen:
                seen.add((row.diagram_id, row.row_index))
                unique.append(row)
        return unique[:limit]

    # --- prices -----------------------------------------------------------------------

    def get_price(
        self, part_number: str, brand: str | None, *, all_sources: bool = False
    ) -> Answer[list[PriceQuote]]:
        """Reference price(s) as each source shows them. No currency conversion."""
        brand = normalize_brand(brand) if brand else None
        query = make_key(brand or "", normalize_part_number(part_number))

        def live(src: Source) -> tuple[list[str], datetime | None]:
            quote = src.get_price(part_number, brand or "")
            if quote is None:
                return [], None
            self._check([quote], src)
            if quote.brand is None and brand:
                quote.brand = brand  # the brand we asked for; keeps the store key consistent
            self.store.upsert_price(quote)
            return [make_key(quote.brand or "", quote.part_number)], quote.fetched_at

        def load(src: Source, ids: list[str]) -> list[PriceQuote]:
            quotes = []
            for key in ids:
                quote_brand, quote_number = split_key(key)
                quote = self.store.get_price(src.name, quote_number, quote_brand or None)
                if quote is not None:
                    quotes.append(quote)
            return quotes

        return self._first_answer(
            self.route(brand), "price", query, self.price_ttl, live, load, collect_all=all_sources
        )

    def close(self) -> None:
        for src in self._sources.values():
            try:
                src.close()
            except Exception:
                log.exception("closing source %s", src.name)
