"""Normalized catalogue DB (SQLite), separate from the page cache.

Nothing happens at import. `open_store(path)` opens the file and creates the
schema. Each table keeps the indexed columns needed for queries plus the full
record as JSON in `data`, so records round-trip without loss.

Upserts are keyed by (source, natural id). For an existing row, a new value
that is None / empty does not wipe an old non-empty one (a diagram listing
must not erase the image URL a detail fetch stored earlier).
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .models import (
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

SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    name        TEXT PRIMARY KEY,
    description TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vehicles (
    pk         INTEGER PRIMARY KEY,
    source     TEXT NOT NULL,
    id         TEXT NOT NULL,
    brand      TEXT NOT NULL,
    model      TEXT,
    year_from  INTEGER,
    year_to    INTEGER,
    chassis    TEXT,
    vin        TEXT,
    url        TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    data       TEXT NOT NULL,
    UNIQUE (source, id)
);
CREATE INDEX IF NOT EXISTS vehicles_brand_model ON vehicles (brand, model);
CREATE TABLE IF NOT EXISTS "groups" (
    pk            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,
    vehicle_id    TEXT NOT NULL,
    id            TEXT NOT NULL,
    parent_id     TEXT,
    name          TEXT NOT NULL,
    name_original TEXT,
    url           TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    data          TEXT NOT NULL,
    UNIQUE (source, vehicle_id, id)
);
CREATE TABLE IF NOT EXISTS diagrams (
    pk            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,
    vehicle_id    TEXT NOT NULL,
    id            TEXT NOT NULL,
    group_id      TEXT NOT NULL,
    name          TEXT NOT NULL,
    name_original TEXT,
    code          TEXT,
    image_url     TEXT,
    url           TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    data          TEXT NOT NULL,
    UNIQUE (source, vehicle_id, id)
);
CREATE INDEX IF NOT EXISTS diagrams_group ON diagrams (source, vehicle_id, group_id);
CREATE TABLE IF NOT EXISTS hotspots (
    pk         INTEGER PRIMARY KEY,
    source     TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    diagram_id TEXT NOT NULL,
    idx        INTEGER NOT NULL,
    callout    TEXT NOT NULL,
    x REAL, y REAL, width REAL, height REAL,
    url        TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    data       TEXT NOT NULL,
    UNIQUE (source, vehicle_id, diagram_id, idx)
);
CREATE TABLE IF NOT EXISTS part_rows (
    pk               INTEGER PRIMARY KEY,
    source           TEXT NOT NULL,
    vehicle_id       TEXT NOT NULL,
    diagram_id       TEXT NOT NULL,
    row_index        INTEGER NOT NULL,
    callout          TEXT,
    part_number      TEXT,
    part_number_norm TEXT,
    name             TEXT,
    name_original    TEXT,
    notes            TEXT,
    url              TEXT NOT NULL,
    fetched_at       TEXT NOT NULL,
    data             TEXT NOT NULL,
    UNIQUE (source, vehicle_id, diagram_id, row_index)
);
CREATE INDEX IF NOT EXISTS part_rows_pn ON part_rows (part_number_norm);
CREATE INDEX IF NOT EXISTS part_rows_vehicle ON part_rows (source, vehicle_id);
CREATE TABLE IF NOT EXISTS price_quotes (
    pk               INTEGER PRIMARY KEY,
    source           TEXT NOT NULL,
    brand            TEXT NOT NULL,      -- '' when unknown
    part_number_norm TEXT NOT NULL,
    part_number      TEXT NOT NULL,
    amount           REAL,
    currency         TEXT,
    url              TEXT NOT NULL,
    fetched_at       TEXT NOT NULL,
    data             TEXT NOT NULL,
    UNIQUE (source, brand, part_number_norm)
);
-- What a source answered for a query (ids in order), so an empty answer is
-- remembered too and freshness is per question, not per row.
CREATE TABLE IF NOT EXISTS lookups (
    source     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    query      TEXT NOT NULL,
    result     TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source, kind, query)
);
CREATE VIRTUAL TABLE IF NOT EXISTS part_rows_fts USING fts5 (
    name, name_original, part_number, notes,
    content='part_rows', content_rowid='pk',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS part_rows_ai AFTER INSERT ON part_rows BEGIN
    INSERT INTO part_rows_fts (rowid, name, name_original, part_number, notes)
    VALUES (new.pk, new.name, new.name_original, new.part_number, new.notes);
END;
CREATE TRIGGER IF NOT EXISTS part_rows_ad AFTER DELETE ON part_rows BEGIN
    INSERT INTO part_rows_fts (part_rows_fts, rowid, name, name_original, part_number, notes)
    VALUES ('delete', old.pk, old.name, old.name_original, old.part_number, old.notes);
END;
CREATE TRIGGER IF NOT EXISTS part_rows_au AFTER UPDATE ON part_rows BEGIN
    INSERT INTO part_rows_fts (part_rows_fts, rowid, name, name_original, part_number, notes)
    VALUES ('delete', old.pk, old.name, old.name_original, old.part_number, old.notes);
    INSERT INTO part_rows_fts (rowid, name, name_original, part_number, notes)
    VALUES (new.pk, new.name, new.name_original, new.part_number, new.notes);
END;
"""

_TOKEN = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class Lookup:
    ids: list[str]
    fetched_at: datetime


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _empty(value: Any) -> bool:
    return value is None or value == [] or value == {} or value == ""


def _merge(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    merged = dict(old)
    for key, value in new.items():
        if not _empty(value) or key not in merged:
            merged[key] = value
    return merged


def build_fts_query(alternatives: str | Sequence[str]) -> str | None:
    """Text -> FTS5 query. A string means all its words (prefix match);
    a list of phrases means any of them. Returns None if no usable words."""
    phrases = [alternatives] if isinstance(alternatives, str) else list(alternatives)
    clauses = []
    for phrase in phrases:
        tokens = _TOKEN.findall(phrase)
        if tokens:
            clauses.append("(" + " AND ".join(f'"{t}"*' for t in tokens) + ")")
    return " OR ".join(clauses) if clauses else None


class CatalogueStore:
    """Use `open_store()`; the constructor expects a ready connection."""

    def __init__(self, conn: sqlite3.Connection, path: str) -> None:
        self._conn = conn
        self.path = path
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- helpers ------------------------------------------------------------------

    def _existing_data(self, table: str, where: str, args: tuple[Any, ...]) -> dict[str, Any] | None:
        row = self._conn.execute(f"SELECT data FROM {table} WHERE {where}", args).fetchone()
        return json.loads(row[0]) if row else None

    def _rows(self, sql: str, args: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    # --- sources ------------------------------------------------------------------

    def register_source(self, name: str, description: str | None = None) -> None:
        now = _iso(utcnow())
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO sources (name, description, first_seen, last_seen) VALUES (?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     description = COALESCE(excluded.description, description),
                     last_seen = excluded.last_seen""",
                (name, description, now, now),
            )

    def list_sources(self) -> list[dict[str, Any]]:
        rows = self._rows("SELECT name, description, first_seen, last_seen FROM sources ORDER BY name")
        return [dict(zip(("name", "description", "first_seen", "last_seen"), r)) for r in rows]

    # --- vehicles -----------------------------------------------------------------

    def upsert_vehicles(self, vehicles: Iterable[Vehicle]) -> None:
        with self._lock, self._conn:
            for v in vehicles:
                old = self._existing_data("vehicles", "source = ? AND id = ?", (v.source, v.id))
                data = _merge(old, v.to_dict()) if old else v.to_dict()
                rec = Vehicle.from_dict(data)
                self._conn.execute(
                    """INSERT INTO vehicles (source, id, brand, model, year_from, year_to, chassis, vin,
                                             url, fetched_at, data)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(source, id) DO UPDATE SET
                         brand=excluded.brand, model=excluded.model, year_from=excluded.year_from,
                         year_to=excluded.year_to, chassis=excluded.chassis, vin=excluded.vin,
                         url=excluded.url, fetched_at=excluded.fetched_at, data=excluded.data""",
                    (
                        rec.source, rec.id, rec.brand, rec.model, rec.year_from, rec.year_to,
                        rec.chassis, rec.vin, rec.url, _iso(rec.fetched_at), json.dumps(data),
                    ),
                )

    def get_vehicle(self, source: str, vehicle_id: str) -> Vehicle | None:
        rows = self._rows("SELECT data FROM vehicles WHERE source = ? AND id = ?", (source, vehicle_id))
        return Vehicle.from_dict(json.loads(rows[0][0])) if rows else None

    def get_vehicles(self, source: str, ids: Sequence[str]) -> list[Vehicle]:
        found = [self.get_vehicle(source, i) for i in ids]
        return [v for v in found if v is not None]

    def find_vehicles(
        self,
        brand: str,
        model: str | None = None,
        year: int | None = None,
        source: str | None = None,
    ) -> list[Vehicle]:
        """Local query over vehicles already stored (no source call)."""
        sql = "SELECT data FROM vehicles WHERE brand = ?"
        args: list[Any] = [normalize_brand(brand)]
        if model:
            sql += " AND LOWER(COALESCE(model, '') || ' ' || COALESCE(chassis, '')) LIKE ?"
            args.append(f"%{model.lower()}%")
        if year is not None:
            sql += " AND (year_from IS NULL OR year_from <= ?) AND (year_to IS NULL OR year_to >= ?)"
            args += [year, year]
        if source:
            sql += " AND source = ?"
            args.append(source)
        return [Vehicle.from_dict(json.loads(r[0])) for r in self._rows(sql + " ORDER BY pk", tuple(args))]

    # --- groups -------------------------------------------------------------------

    def upsert_groups(self, groups: Iterable[Group]) -> None:
        with self._lock, self._conn:
            for g in groups:
                where = ("source = ? AND vehicle_id = ? AND id = ?", (g.source, g.vehicle_id, g.id))
                old = self._existing_data('"groups"', *where)
                data = _merge(old, g.to_dict()) if old else g.to_dict()
                rec = Group.from_dict(data)
                self._conn.execute(
                    """INSERT INTO "groups" (source, vehicle_id, id, parent_id, name, name_original,
                                           url, fetched_at, data)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(source, vehicle_id, id) DO UPDATE SET
                         parent_id=excluded.parent_id, name=excluded.name,
                         name_original=excluded.name_original, url=excluded.url,
                         fetched_at=excluded.fetched_at, data=excluded.data""",
                    (
                        rec.source, rec.vehicle_id, rec.id, rec.parent_id, rec.name, rec.name_original,
                        rec.url, _iso(rec.fetched_at), json.dumps(data),
                    ),
                )

    def get_group(self, source: str, vehicle_id: str, group_id: str) -> Group | None:
        rows = self._rows(
            'SELECT data FROM "groups" WHERE source = ? AND vehicle_id = ? AND id = ?',
            (source, vehicle_id, group_id),
        )
        return Group.from_dict(json.loads(rows[0][0])) if rows else None

    def get_groups(self, source: str, vehicle_id: str, ids: Sequence[str] | None = None) -> list[Group]:
        """Groups of a vehicle; with `ids`, exactly those in that order."""
        if ids is not None:
            found = [self.get_group(source, vehicle_id, i) for i in ids]
            return [g for g in found if g is not None]
        rows = self._rows(
            'SELECT data FROM "groups" WHERE source = ? AND vehicle_id = ? ORDER BY pk', (source, vehicle_id)
        )
        return [Group.from_dict(json.loads(r[0])) for r in rows]

    # --- diagrams -----------------------------------------------------------------

    def _upsert_diagram(self, d: Diagram) -> None:
        new = d.to_dict()
        new.pop("hotspots")  # hotspots live in their own table
        old = self._existing_data(
            "diagrams", "source = ? AND vehicle_id = ? AND id = ?", (d.source, d.vehicle_id, d.id)
        )
        data = _merge(old, new) if old else new
        rec = Diagram.from_dict(data)
        self._conn.execute(
            """INSERT INTO diagrams (source, vehicle_id, id, group_id, name, name_original, code,
                                     image_url, url, fetched_at, data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source, vehicle_id, id) DO UPDATE SET
                 group_id=excluded.group_id, name=excluded.name, name_original=excluded.name_original,
                 code=excluded.code, image_url=excluded.image_url, url=excluded.url,
                 fetched_at=excluded.fetched_at, data=excluded.data""",
            (
                rec.source, rec.vehicle_id, rec.id, rec.group_id, rec.name, rec.name_original, rec.code,
                rec.image_url, rec.url, _iso(rec.fetched_at), json.dumps(data),
            ),
        )

    def upsert_diagrams(self, diagrams: Iterable[Diagram]) -> None:
        """Save diagrams from a listing. Existing hotspots and part rows are kept."""
        with self._lock, self._conn:
            for d in diagrams:
                self._upsert_diagram(d)

    def save_diagram_detail(self, diagram: Diagram, rows: Sequence[PartRow]) -> None:
        """Save a fully fetched diagram: replaces its hotspots and part rows."""
        key = (diagram.source, diagram.vehicle_id, diagram.id)
        with self._lock, self._conn:
            self._upsert_diagram(diagram)
            self._conn.execute(
                "DELETE FROM hotspots WHERE source = ? AND vehicle_id = ? AND diagram_id = ?", key
            )
            self._conn.execute(
                "DELETE FROM part_rows WHERE source = ? AND vehicle_id = ? AND diagram_id = ?", key
            )
            for idx, h in enumerate(diagram.hotspots):
                self._conn.execute(
                    """INSERT INTO hotspots (source, vehicle_id, diagram_id, idx, callout, x, y, width,
                                             height, url, fetched_at, data)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (*key, idx, h.callout, h.x, h.y, h.width, h.height, h.url, _iso(h.fetched_at),
                     json.dumps(h.to_dict())),
                )
            for r in rows:
                if (r.source, r.vehicle_id, r.diagram_id) != key:
                    raise ValueError(f"part row {r.row_index} does not belong to diagram {diagram.id}")
                self._conn.execute(
                    """INSERT INTO part_rows (source, vehicle_id, diagram_id, row_index, callout,
                                              part_number, part_number_norm, name, name_original, notes,
                                              url, fetched_at, data)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(source, vehicle_id, diagram_id, row_index) DO UPDATE SET
                         callout=excluded.callout, part_number=excluded.part_number,
                         part_number_norm=excluded.part_number_norm, name=excluded.name,
                         name_original=excluded.name_original, notes=excluded.notes,
                         url=excluded.url, fetched_at=excluded.fetched_at, data=excluded.data""",
                    (*key, r.row_index, r.callout, r.part_number, r.part_number_norm, r.name,
                     r.name_original, r.notes, r.url, _iso(r.fetched_at), json.dumps(r.to_dict())),
                )

    def get_diagram(self, source: str, vehicle_id: str, diagram_id: str) -> Diagram | None:
        """The diagram with its hotspots."""
        rows = self._rows(
            "SELECT data FROM diagrams WHERE source = ? AND vehicle_id = ? AND id = ?",
            (source, vehicle_id, diagram_id),
        )
        if not rows:
            return None
        diagram = Diagram.from_dict(json.loads(rows[0][0]))
        spots = self._rows(
            "SELECT data FROM hotspots WHERE source = ? AND vehicle_id = ? AND diagram_id = ? ORDER BY idx",
            (source, vehicle_id, diagram_id),
        )
        diagram.hotspots = [Hotspot.from_dict(json.loads(s[0])) for s in spots]
        return diagram

    def get_diagrams(
        self,
        source: str,
        vehicle_id: str,
        group_id: str | None = None,
        ids: Sequence[str] | None = None,
    ) -> list[Diagram]:
        """Diagrams of a vehicle (optionally one group), without hotspots."""
        if ids is not None:
            out = []
            for i in ids:
                rows = self._rows(
                    "SELECT data FROM diagrams WHERE source = ? AND vehicle_id = ? AND id = ?",
                    (source, vehicle_id, i),
                )
                if rows:
                    out.append(Diagram.from_dict(json.loads(rows[0][0])))
            return out
        sql = "SELECT data FROM diagrams WHERE source = ? AND vehicle_id = ?"
        args: tuple[Any, ...] = (source, vehicle_id)
        if group_id is not None:
            sql += " AND group_id = ?"
            args += (group_id,)
        return [Diagram.from_dict(json.loads(r[0])) for r in self._rows(sql + " ORDER BY pk", args)]

    # --- part rows ----------------------------------------------------------------

    def get_part_rows(self, source: str, vehicle_id: str, diagram_id: str) -> list[PartRow]:
        rows = self._rows(
            """SELECT data FROM part_rows WHERE source = ? AND vehicle_id = ? AND diagram_id = ?
               ORDER BY row_index""",
            (source, vehicle_id, diagram_id),
        )
        return [PartRow.from_dict(json.loads(r[0])) for r in rows]

    def search_parts(
        self,
        query: str | Sequence[str],
        *,
        source: str | None = None,
        vehicle_id: str | None = None,
        limit: int = 20,
    ) -> list[PartRow]:
        """Full-text search on part names (English and original), numbers and notes.
        `query` is text (all words must match) or a list of alternative phrases."""
        fts = build_fts_query(query)
        if fts is None:
            return []
        sql = """SELECT p.data FROM part_rows_fts f JOIN part_rows p ON p.pk = f.rowid
                 WHERE part_rows_fts MATCH ?"""
        args: list[Any] = [fts]
        if source is not None:
            sql += " AND p.source = ?"
            args.append(source)
        if vehicle_id is not None:
            sql += " AND p.vehicle_id = ?"
            args.append(vehicle_id)
        sql += " ORDER BY bm25(part_rows_fts), p.pk LIMIT ?"
        args.append(limit)
        return [PartRow.from_dict(json.loads(r[0])) for r in self._rows(sql, tuple(args))]

    def find_part_rows(
        self,
        part_number: str,
        *,
        source: str | None = None,
        vehicle_id: str | None = None,
        limit: int = 50,
    ) -> list[PartRow]:
        """Rows whose part number matches, ignoring spaces/dashes."""
        sql = "SELECT data FROM part_rows WHERE part_number_norm = ?"
        args: list[Any] = [normalize_part_number(part_number)]
        if source is not None:
            sql += " AND source = ?"
            args.append(source)
        if vehicle_id is not None:
            sql += " AND vehicle_id = ?"
            args.append(vehicle_id)
        args.append(limit)
        return [PartRow.from_dict(json.loads(r[0])) for r in self._rows(sql + " ORDER BY pk LIMIT ?", tuple(args))]

    # --- prices -------------------------------------------------------------------

    def upsert_price(self, quote: PriceQuote) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO price_quotes (source, brand, part_number_norm, part_number, amount,
                                             currency, url, fetched_at, data)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source, brand, part_number_norm) DO UPDATE SET
                     part_number=excluded.part_number, amount=excluded.amount,
                     currency=excluded.currency, url=excluded.url,
                     fetched_at=excluded.fetched_at, data=excluded.data""",
                (
                    quote.source, quote.brand or "", quote.part_number_norm, quote.part_number,
                    quote.amount, quote.currency, quote.url, _iso(quote.fetched_at),
                    json.dumps(quote.to_dict()),
                ),
            )

    def get_price(self, source: str, part_number: str, brand: str | None) -> PriceQuote | None:
        rows = self._rows(
            "SELECT data FROM price_quotes WHERE source = ? AND brand = ? AND part_number_norm = ?",
            (source, normalize_brand(brand) if brand else "", normalize_part_number(part_number)),
        )
        return PriceQuote.from_dict(json.loads(rows[0][0])) if rows else None

    # --- lookups ------------------------------------------------------------------

    def record_lookup(
        self, source: str, kind: str, query: str, ids: Sequence[str], fetched_at: datetime
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO lookups (source, kind, query, result, fetched_at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(source, kind, query) DO UPDATE SET
                     result=excluded.result, fetched_at=excluded.fetched_at""",
                (source, kind, query, json.dumps(list(ids)), _iso(fetched_at)),
            )

    def get_lookup(self, source: str, kind: str, query: str) -> Lookup | None:
        rows = self._rows(
            "SELECT result, fetched_at FROM lookups WHERE source = ? AND kind = ? AND query = ?",
            (source, kind, query),
        )
        if not rows:
            return None
        return Lookup(ids=json.loads(rows[0][0]), fetched_at=datetime.fromisoformat(rows[0][1]))

    # --- misc ---------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        tables = ("vehicles", '"groups"', "diagrams", "hotspots", "part_rows", "price_quotes", "lookups")
        return {t.strip('"'): self._rows(f"SELECT COUNT(*) FROM {t}")[0][0] for t in tables}


def open_store(path: str | Path) -> CatalogueStore:
    """Open (and create if needed) the catalogue DB. Use ':memory:' for tests."""
    path = str(path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    with conn:
        if path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) ON CONFLICT(key) DO NOTHING",
            (SCHEMA_VERSION,),
        )
    return CatalogueStore(conn, path)
