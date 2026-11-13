"""In-memory source for tests and for trying the server without network.

Its data is made up (fake.example URLs, made-up part numbers). It shows the
shape real plug-ins return, including Italian `name_original` values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from ..core.errors import NotFound
from ..core.models import (
    Diagram,
    Group,
    Hotspot,
    PartRow,
    PriceQuote,
    Vehicle,
    normalize_brand,
    normalize_part_number,
    utcnow,
)
from .base import Source, SourceContext

BASE = "https://fake.example"

# group id -> (English name, original name)
_GROUPS = {
    "41": ("Bodywork", "Carrozzeria"),
    "51": ("Vehicle trim", "Equipaggiamento vettura"),
    "63": ("Lighting", "Illuminazione"),
    "11": ("Engine", "Motore"),
}

# diagram id -> (group id, English name, original name)
_DIAGRAMS = {
    "51_0010": ("51", "Trim panel, rear bumper", "Rivestimento paraurti posteriore"),
    "51_0020": ("51", "Trim panel, front bumper", "Rivestimento paraurti anteriore"),
    "41_0010": ("41", "Luggage compartment lid", "Coperchio bagagliaio"),
    "41_0020": ("41", "Side panel, front", "Fiancata anteriore"),
    "63_0010": ("63", "Rear light", "Fanale posteriore"),
    "63_0020": ("63", "Headlight", "Proiettore"),
    "11_0010": ("11", "Short engine", "Motore parziale"),
}

# diagram id -> rows: (callout, part number, English, Italian, qty, side)
_ROWS: dict[str, list[tuple[str, str, str, str, str, str | None]]] = {
    "51_0010": [
        ("1", "00 00 0 000 101", "Trim panel, bumper, primed, rear", "Rivestimento paraurti, con primer, post.", "1", None),
        ("2", "00 00 0 000 102", "Reflector, rear left", "Catadiottro posteriore sinistro", "1", "left"),
        ("3", "00 00 0 000 103", "Reflector, rear right", "Catadiottro posteriore destro", "1", "right"),
    ],
    "51_0020": [
        ("1", "00 00 0 000 201", "Trim panel, bumper, primed, front", "Rivestimento paraurti, con primer, ant.", "1", None),
    ],
    "41_0010": [
        ("1", "00 00 0 000 301", "Trunk lid", "Coperchio bagagliaio", "1", None),
        ("2", "00 00 0 000 302", "Hinge, trunk lid, left", "Cerniera coperchio bagagliaio sinistra", "1", "left"),
    ],
    "41_0020": [("1", "00 00 0 000 401", "Side panel, front left", "Fiancata anteriore sinistra", "1", "left")],
    "63_0010": [("1", "00 00 0 000 501", "Rear light in side panel, left", "Fanale posteriore nella fiancata, sinistro", "1", "left")],
    "63_0020": [("1", "00 00 0 000 601", "Headlight, LED, right", "Proiettore LED destro", "1", "right")],
    "11_0010": [("1", "00 00 0 000 701", "Short engine", "Motore parziale", "1", None)],
}

# normalized part number -> EUR excl. VAT
_PRICES = {"00000000101": 312.40, "00000000301": 845.00, "00000000501": 221.15}

DEMO_VIN = "WBA8E1C50JA000001"


class FakeSource(Source):
    def __init__(
        self,
        name: str = "fake",
        brands: tuple[str, ...] = ("BMW",),
        *,
        fail_with: BaseException | None = None,
        fail_methods: set[str] | None = None,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.name = name
        self.description = "In-memory demo catalogue (made-up data, no network)"
        self._brands = [normalize_brand(b) for b in brands]
        self.fail_with = fail_with  # raised by every call (or only `fail_methods`)
        self.fail_methods = fail_methods
        self.now = now
        self.calls: list[tuple[str, Any]] = []

    def _enter(self, method: str, arg: Any) -> datetime:
        self.calls.append((method, arg))
        if self.fail_with is not None and (self.fail_methods is None or method in self.fail_methods):
            raise self.fail_with
        return self.now()

    def calls_to(self, method: str) -> int:
        return sum(1 for m, _ in self.calls if m == method)

    def _vehicle(self, at: datetime, brand: str, vin: str | None = None) -> Vehicle:
        return Vehicle(
            source=self.name,
            url=f"{BASE}/{brand.lower()}/3-series/f30-320i",
            fetched_at=at,
            source_ref={"path": "/catalog/f30-320i"},
            id="F30-320i-sedan",
            brand=brand,
            model="3 Series",
            name="320i Sedan (F30)",
            year_from=2012,
            year_to=2019,
            production="03/2012 - 10/2018",
            market="Europe",
            chassis="F30",
            body="Sedan",
            engine="N20",
            vin=vin,
        )

    def brands(self) -> list[str]:
        return list(self._brands)

    def find_vehicles(self, brand: str, model: str | None = None, year: int | None = None) -> list[Vehicle]:
        at = self._enter("find_vehicles", (brand, model, year))
        brand = normalize_brand(brand)
        if brand not in self._brands:
            return []
        v = self._vehicle(at, brand)
        if model and model.lower() not in f"{v.model} {v.chassis} {v.name}".lower():
            return []
        if year is not None and not (v.year_from or 0) <= year <= (v.year_to or 9999):
            return []
        return [v]

    def resolve_vin(self, vin: str) -> list[Vehicle]:
        at = self._enter("resolve_vin", vin)
        if vin.upper() != DEMO_VIN or "BMW" not in self._brands:
            return []
        return [self._vehicle(at, "BMW", vin=DEMO_VIN)]

    def list_groups(self, vehicle: Vehicle) -> list[Group]:
        at = self._enter("list_groups", vehicle.id)
        return [
            Group(
                source=self.name,
                url=f"{vehicle.url}/groups",
                fetched_at=at,
                id=gid,
                vehicle_id=vehicle.id,
                name=en,
                name_original=it,
            )
            for gid, (en, it) in _GROUPS.items()
        ]

    def _diagram(self, vehicle: Vehicle, did: str, at: datetime, detail: bool) -> Diagram:
        gid, en, it = _DIAGRAMS[did]
        url = f"{vehicle.url}/diagram/{did}"
        diagram = Diagram(
            source=self.name,
            url=url,
            fetched_at=at,
            source_ref={"token": f"opaque-{did}"},
            id=did,
            vehicle_id=vehicle.id,
            group_id=gid,
            name=en,
            name_original=it,
            code=did,
            group_path=[_GROUPS[gid][0]],
        )
        if detail:
            diagram.image_url = f"{BASE}/img/{did}.png"
            diagram.image_width, diagram.image_height = 800, 600
            diagram.hotspots = [
                Hotspot(source=self.name, url=url, fetched_at=at, callout=row[0],
                        x=100.0 * i, y=50.0, width=24.0, height=24.0)
                for i, row in enumerate(_ROWS[did], start=1)
            ]
        return diagram

    def list_diagrams(self, vehicle: Vehicle, group: Group) -> list[Diagram]:
        at = self._enter("list_diagrams", (vehicle.id, group.id))
        return [self._diagram(vehicle, did, at, False) for did, d in _DIAGRAMS.items() if d[0] == group.id]

    def get_diagram(self, vehicle: Vehicle, diagram: Diagram) -> tuple[Diagram, list[PartRow]]:
        at = self._enter("get_diagram", (vehicle.id, diagram.id))
        if diagram.id not in _DIAGRAMS:
            raise NotFound(f"no diagram {diagram.id}", source=self.name)
        full = self._diagram(vehicle, diagram.id, at, True)
        rows = [
            PartRow(
                source=self.name,
                url=full.url,
                fetched_at=at,
                vehicle_id=vehicle.id,
                diagram_id=full.id,
                row_index=i,
                callout=callout,
                part_number=pn,
                name=en,
                name_original=it,
                qty=qty,
                side=side,  # type: ignore[arg-type]
            )
            for i, (callout, pn, en, it, qty, side) in enumerate(_ROWS[diagram.id])
        ]
        return full, rows

    def get_price(self, part_number: str, brand: str) -> PriceQuote | None:
        at = self._enter("get_price", (part_number, brand))
        amount = _PRICES.get(normalize_part_number(part_number))
        if amount is None:
            return None
        return PriceQuote(
            source=self.name,
            url=f"{BASE}/part/{normalize_part_number(part_number)}",
            fetched_at=at,
            part_number=part_number,
            brand=brand,
            amount=amount,
            currency="EUR",
            tax_note="excl. VAT",
        )


def create_source(ctx: SourceContext) -> FakeSource:
    return FakeSource()
