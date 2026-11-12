"""Catalogue records.

Rules every plug-in must follow:
- Every record says where it came from: `source`, `url`, `fetched_at`.
  `fetched_at` is when the page was fetched (use `FetchResult.fetched_at`,
  which is the original fetch time even for a cache hit), timezone-aware UTC.
- A value the page does not show is `None`. Never guess or fill defaults.
- `source_ref` holds opaque navigation data the plug-in needs later
  (e.g. a session-bound URL). It is stored but never shown to MCP clients.
- `id` fields are natural ids that stay stable between visits. Do not use
  session tokens (ssd) as ids; put those in `source_ref`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Literal, Self
from urllib.parse import quote, unquote

Side = Literal["left", "right"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_brand(brand: str) -> str:
    """'  bmw ' -> 'BMW', 'Mercedes-Benz' -> 'MERCEDES-BENZ'."""
    return " ".join(brand.split()).upper()


def normalize_part_number(part_number: str) -> str:
    """Drop spaces, dashes and dots: '51 12 7 285 795' -> '51127285795'."""
    folded = unicodedata.normalize("NFKC", part_number)
    return re.sub(r"[^0-9A-Za-z]", "", folded).upper()


def make_key(*parts: str) -> str:
    """Join id parts into one opaque key. Parts are %-encoded so ':' inside ids is safe."""
    return ":".join(quote(str(p), safe="") for p in parts)


def split_key(key: str) -> list[str]:
    return [unquote(p) for p in key.split(":")]


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Record):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


@dataclass(kw_only=True)
class Record:
    """Provenance shared by every record."""

    source: str
    url: str
    fetched_at: datetime
    source_ref: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError(f"{type(self).__name__}.source is empty")
        if not isinstance(self.fetched_at, datetime):
            self.fetched_at = _parse_dt(self.fetched_at)
        if self.fetched_at.tzinfo is None:
            raise ValueError(f"{type(self).__name__}.fetched_at must be timezone-aware (UTC)")

    def to_dict(self, *, include_source_ref: bool = True) -> dict[str, Any]:
        out = {f.name: _encode(getattr(self, f.name)) for f in fields(self)}
        if not include_source_ref:
            out.pop("source_ref", None)
            for value in out.values():  # nested records (Diagram.hotspots)
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            item.pop("source_ref", None)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        names = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in names}  # ignore unknown keys
        kwargs["fetched_at"] = _parse_dt(kwargs["fetched_at"])
        return cls(**kwargs)


@dataclass(kw_only=True)
class Vehicle(Record):
    """Something you can list groups for (one catalogue vehicle / model variant)."""

    id: str
    brand: str
    model: str | None = None
    name: str | None = None  # full label as the catalogue shows it
    year_from: int | None = None
    year_to: int | None = None
    production: str | None = None  # period as shown, e.g. "09/2016 - 06/2019"
    market: str | None = None
    chassis: str | None = None  # series / frame code, e.g. "G30", "FK7"
    body: str | None = None
    engine: str | None = None
    transmission: str | None = None
    trim: str | None = None
    vin: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)  # other label/value pairs shown

    def __post_init__(self) -> None:
        super().__post_init__()
        self.brand = normalize_brand(self.brand)

    @property
    def key(self) -> str:
        return make_key(self.source, self.id)


@dataclass(kw_only=True)
class Group(Record):
    """A catalogue section, e.g. 'Bodywork' or 'Lighting'."""

    id: str
    vehicle_id: str
    name: str
    name_original: str | None = None  # untranslated name, if `name` was translated
    parent_id: str | None = None

    @property
    def key(self) -> str:
        return make_key(self.source, self.vehicle_id, self.id)


@dataclass(kw_only=True)
class Hotspot(Record):
    """A clickable callout on a diagram image.

    Coordinates are pixels in the image at `Diagram.image_url`, at its natural
    size, origin top-left. Unknown coordinates are None.
    """

    callout: str
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None


@dataclass(kw_only=True)
class Diagram(Record):
    """An exploded diagram. From `list_diagrams` it may lack image/hotspots;
    `get_diagram` returns it complete."""

    id: str
    vehicle_id: str
    group_id: str
    name: str
    name_original: str | None = None
    code: str | None = None  # the catalogue's own diagram code, if shown
    group_path: list[str] = field(default_factory=list)  # e.g. ["Bodywork", "Bumper"]
    image_url: str | None = None
    image_width: int | None = None
    image_height: int | None = None
    notes: str | None = None
    hotspots: list[Hotspot] = field(default_factory=list)

    @property
    def key(self) -> str:
        return make_key(self.source, self.vehicle_id, self.id)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        data = dict(data)
        data["hotspots"] = [
            h if isinstance(h, Hotspot) else Hotspot.from_dict(h) for h in data.get("hotspots") or []
        ]
        return super().from_dict(data)


@dataclass(kw_only=True)
class PartRow(Record):
    """One row of a diagram's parts table."""

    vehicle_id: str
    diagram_id: str
    row_index: int  # 0-based order on the page; with diagram_id it is the natural key
    callout: str | None = None  # number on the diagram image
    part_number: str | None = None  # current OEM number as shown
    superseded_from: str | None = None  # older number this one replaces, if shown
    name: str | None = None  # English (translated if the site is not English)
    name_original: str | None = None  # as shown on the site, untranslated
    qty: str | None = None  # as shown: "1", "2", "AR", "X"... kept as text
    notes: str | None = None
    side: Side | None = None  # only when the source states it
    attributes: dict[str, str] = field(default_factory=dict)  # other columns shown

    @property
    def part_number_norm(self) -> str | None:
        return normalize_part_number(self.part_number) if self.part_number else None


@dataclass(kw_only=True)
class PriceQuote(Record):
    """A reference price as shown by one source at `fetched_at`."""

    part_number: str
    brand: str | None = None
    name: str | None = None
    amount: float | None = None
    currency: str | None = None  # ISO 4217, e.g. "EUR"; never converted
    tax_note: str | None = None  # e.g. "excl. VAT"
    availability: str | None = None  # as shown, None if the page does not say
    notes: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.brand is not None:
            self.brand = normalize_brand(self.brand)

    @property
    def part_number_norm(self) -> str:
        return normalize_part_number(self.part_number)
