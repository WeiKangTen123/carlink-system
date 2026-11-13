"""Catalogue worker: answers the Carlink server's catalogue look-up jobs.

The API never contacts a parts site; this worker does, through the same
polite fetcher, page cache and limits as the MCP server. It asks the API for
a job, finds the catalogue car, the diagrams for each damaged part and their
part rows, and posts the result back. Shapes and endpoints follow
plans/2026-11-12-parts-catalogue/CONTRACT.md (Job, Result, claim / progress /
result, Bearer token). In production it runs as the `parts-worker` container
next to the API (infra/docker-compose.prod.yml) and reads the Microfiches
catalogues only (BMW, MINI, Honda).

Run from the `tools` folder:

    python -m parts_mcp.worker                                  # poll the server
    python -m parts_mcp.worker --once                           # at most one job, then stop
    python -m parts_mcp.worker --job job.json --out result.json # dry run, no server

Environment:
    CARLINK_API_BASE        API base (default http://api:8000, the API inside the compose
                            network); https, or plain http to localhost / 127.0.0.1 / api only
    CATALOGUE_WORKER_TOKEN  Bearer token; required for the loop, never logged
    PARTS_MCP_DATA_DIR      data folder (default <home>/carlink-parts-data)
    PARTS_MCP_SOURCES       sources to load (default microfiches)
    PARTS_MCP_LOG_FILE      "off" = log to the console only (default: also <data>/logs/worker.log)
    WORKER_NAME             name the dashboard shows (default this computer's name)

Logs are one line per event on stdout (stderr for a dry run that prints its result).
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import math
import os
import re
import signal
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

try:
    import httpx
except ImportError:  # the mcp 2.x SDK ships the client under this name
    import httpx2 as httpx  # type: ignore[no-redef]

from .config import Settings, load_settings
from .core.errors import ChooseTrim, NotFound, NotSupported, PartsError
from .core.fetch import PageCache
from .core.models import PartRow, Vehicle, normalize_brand, utcnow
from .core.store import open_store
from .service import CatalogueService
from .sources.registry import load_sources

log = logging.getLogger("parts_mcp.worker")

VERSION = "0.1"
DEFAULT_API_BASE = "http://api:8000"  # the API container, seen from inside the compose network
DEFAULT_DATA_DIR = Path.home() / "carlink-parts-data"  # outside OneDrive: the caches must not sync
DEFAULT_POLL_SECONDS = 20.0
FIRST_BACKOFF = 30.0  # server unreachable: wait 30 s, then double up to 5 min
MAX_BACKOFF = 300.0
OFF_POLL_SECONDS = 300.0  # the API answers 404 (look-up switched off): ask again every 5 min, quietly
IDLE_SECONDS = 3600.0  # --stay-up without a token: nothing to do until the container is recreated
RESULT_ATTEMPTS = 6  # ~17 min of retries; the server re-queues a silent job after 45 min

# --- contract caps (server validation) -----------------------------------------------
MAX_PARTS = 40
MAX_DIAGRAMS = 3
MAX_ROWS = 250
MAX_CANDIDATES = 10
MAX_STR = 400
# The server's caps on failures (app/parts/catalogue.py MAX_FAILURES /
# MAX_PART_FAILURES): more is refused with 422, so the list is cut here.
MAX_FAILURES = 40
MAX_PART_FAILURES = 20
MIN_SCORE = 0.5  # find_diagrams: 0.5 or more is a real match
_SITES = ("bmw-spare-parts.com", "mini-spare-parts.com", "parts-honda.uk")
ALLOWED_HOSTS = frozenset(h for site in _SITES for h in (site, f"www.{site}"))

# Codes the server accepts in error_code (and that failures use).
ERROR_CODES = ("not_supported", "no_vehicle", "needs_human", "challenge_blocked", "site_blocked",
               "daily_cap_reached", "internal")
# After one of these a source is not asked again in the same job.
STOP_CODES = frozenset({"needs_human", "challenge_blocked", "site_blocked", "daily_cap_reached"})

# --- which catalogue, which model name ------------------------------------------------
# Brand -> sources to try, in order. A source is asked only when the ones before it
# found no car. Other makes: no catalogue (not_supported).
ROUTES: dict[str, tuple[str, ...]] = {
    "BMW": ("microfiches",),
    "MINI": ("microfiches",),
    "HONDA": ("microfiches",),
}
DEFAULT_ROUTE: tuple[str, ...] = ()

# Source -> brand -> model (as on the case, normalized) -> the source's name for its market
# twin. Tried only after the source found nothing under the case's own model name; the
# result is then marked twin_of. parts-honda.uk lists European models only: no VEZEL, FIT
# or GRACE, but HRV, JAZZ (and JAZZ-HYBRID) and CITY (checked on its model list, Nov 2026).
# Same body and sub-part structure; part numbers may differ from the Singapore car.
MODEL_ALIASES: dict[str, dict[str, dict[str, tuple[str, ...]]]] = {
    "microfiches": {
        "HONDA": {
            "VEZEL": ("HR-V",),
            "FIT": ("Jazz",),
            "GRACE": ("City",),
        },
    },
}

# Trims opened for one job when the model text names none (each uncached trim page is
# one live request). Cached pages are opened first, and opening stops at the first
# Singapore-market car.
MAX_TRIM_PAGES = 8


# --- small helpers --------------------------------------------------------------------


def clip(value: Any, limit: int = MAX_STR) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _norm(text: str | None) -> str:
    """'HR-V' -> 'HRV': for comparing model names typed by people."""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def _iso(dt: Any) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _float(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def in_range(value: Any, low: float, high: float) -> Any:
    """`value` when it is a number from `low` to `high`, else None: the server
    refuses a result with a year, score or price outside its bounds (422), so
    an odd one is dropped here rather than the whole look-up."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(number) and low <= number <= high else None


# The server's bounds (app/parts/catalogue.py CatalogueVehicle / CatalogueRow).
YEAR_RANGE = (1900, 2100)
MAX_REF_AMOUNT = 10_000_000


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def safe_url(url: Any) -> str | None:
    """The URL if the server will take it, else None -- the server's own rule
    (apps/bot-service app/reports/schema.catalogue_link): https on a catalogue
    host, no user name or password, the standard port, at most 400 characters
    once trimmed. Anything it would refuse is nulled here, so one odd link never
    gets the whole result refused (422). A URL is never shortened: a cut URL is
    a wrong URL. The cases both sides are checked against:
    tests/fixtures/catalogue_links.json."""
    if not url:
        return None
    text = str(url).strip()
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443):
        return None
    if (parts.hostname or "").lower() not in ALLOWED_HOSTS or not text or len(text) > MAX_STR:
        return None
    return text


def error_code(code: str | None) -> str:
    """A PartsError code -> the contract's error code."""
    if code in ("not_supported", "needs_human", "challenge_blocked", "site_blocked", "daily_cap_reached"):
        return code
    if code == "no_vehicle":
        return code
    return "internal"


def short_text(exc: BaseException, limit: int = 300) -> str:
    """One line for error_text / failures: no stack trace."""
    if isinstance(exc, PartsError):
        text = exc.message
        if error_code(exc.code) == "internal" and exc.code not in text:
            text = f"{exc.code}: {text}"
    else:
        text = f"{type(exc).__name__}: {exc}"
    return clip(" ".join(text.split()), limit) or type(exc).__name__


def failure(source: str | None, code: str, message: str | None) -> dict[str, Any]:
    return {"source": source, "code": error_code(code), "message": clip(message, 300)}


def failure_from_exc(source: str | None, exc: BaseException) -> dict[str, Any]:
    code = exc.code if isinstance(exc, PartsError) else "internal"
    return failure(getattr(exc, "source", None) or source, code, short_text(exc))


def failure_from_dict(data: Mapping[str, Any], source: str | None = None) -> dict[str, Any]:
    raw = str(data.get("error") or "internal")
    message = str(data.get("message") or raw)
    if error_code(raw) == "internal" and raw not in message:
        message = f"{raw}: {message}"
    return failure(data.get("source") or source, raw, " ".join(message.split()))


def market_rank(vehicle: Vehicle) -> int:
    """0 Singapore, 1 Asia / General, 2 anything else (or unknown)."""
    text = (vehicle.market or "").lower()
    if "singapore" in text:
        return 0
    if re.search(r"\b(asia|asean|general)\b", text):
        return 1
    return 2


def steering_rank(vehicle: Vehicle) -> int:
    """Singapore drives on the left, so right-hand drive first: 0 RHD, 1 unknown, 2 LHD."""
    shown = " ".join(str(vehicle.attributes.get(k) or "") for k in ("steering", "Drivers Position", "Steering"))
    shown = shown.upper()
    if "RHD" in shown or "RIGHT" in shown:
        return 0
    if "LHD" in shown or "LEFT" in shown:
        return 2
    return 1


def covers_year(vehicle: Vehicle, year: int | None) -> bool:
    if year is None:
        return True
    if vehicle.year_from is not None and year < vehicle.year_from:
        return False
    if vehicle.year_to is not None and year > vehicle.year_to:
        return False
    return True


def rank_vehicles(vehicles: Sequence[Vehicle]) -> list[Vehicle]:
    """Singapore market first, then Asia / General, then others; RHD before LHD;
    otherwise the catalogue's order."""
    order = sorted(enumerate(vehicles), key=lambda iv: (market_rank(iv[1]), steering_rank(iv[1]), iv[0]))
    return [v for _, v in order]


def _opposite(wanted: str | None, row_side: str | None) -> bool:
    return wanted in ("left", "right") and row_side in ("left", "right") and wanted != row_side


# --- the job --------------------------------------------------------------------------


class Cancelled(Exception):
    """The server said the job was cancelled (or is gone)."""


@dataclass
class PartSpec:
    source_part: str
    query: str
    side: str | None


_SIDE_WORDS = re.compile(r"\((?:left|right|side undetermined)\)|\b(?:left|right|lh|rh)\b", re.IGNORECASE)


def part_spec(raw: Mapping[str, Any]) -> PartSpec:
    source_part = _clean(raw.get("source_part"))
    query = _clean(raw.get("query")) or _clean(_SIDE_WORDS.sub(" ", source_part)).lower()
    side = raw.get("side") if raw.get("side") in ("left", "right") else None
    return PartSpec(source_part, query, side)


@dataclass
class Search:
    found: list[tuple[Vehicle, str | None]] = field(default_factory=list)  # (vehicle, twin_of), best first
    failures: list[dict[str, Any]] = field(default_factory=list)
    code: str | None = None  # why nothing was found
    text: str | None = None


ProgressFn = Callable[[int, int, str], bool]  # (done, total, message) -> True when cancelled


class JobProcessor:
    """Turns one Job into one Result (contract shapes), using the catalogue service."""

    def __init__(
        self,
        service: CatalogueService,
        *,
        routes: Mapping[str, Sequence[str]] | None = None,
        default_route: Sequence[str] = DEFAULT_ROUTE,
        aliases: Mapping[str, Mapping[str, Mapping[str, Sequence[str]]]] | None = None,
        max_trim_pages: int = MAX_TRIM_PAGES,
    ) -> None:
        self.service = service
        self.routes = {normalize_brand(b): tuple(s) for b, s in (ROUTES if routes is None else routes).items()}
        self.default_route = tuple(default_route)
        self.aliases = MODEL_ALIASES if aliases is None else aliases
        self.max_trim_pages = max_trim_pages

    # entry point ------------------------------------------------------------------

    def process(self, job: Mapping[str, Any], progress: ProgressFn | None = None) -> dict[str, Any] | None:
        """The Result for `job`, caps applied; None when the job was cancelled."""
        try:
            return apply_caps(self._process(job, progress or (lambda done, total, message: False)))
        except Cancelled:
            log.info("job %s cancelled; stopping", job.get("job_id"))
            return None
        except Exception as exc:  # one bad job must not stop the worker
            log.exception("job %s crashed", job.get("job_id"))
            return apply_caps(failed("internal", short_text(exc)))

    def _process(self, job: Mapping[str, Any], progress: ProgressFn) -> dict[str, Any]:
        brand = _clean(job.get("brand"))
        model = _clean(job.get("model"))
        model_key = _clean(job.get("model_key")) or (model.split(" ")[0] if model else "")
        year = _int(job.get("year"))
        pinned = _clean(job.get("vehicle_key")) or None
        specs = [part_spec(p) for p in (job.get("parts") or []) if isinstance(p, Mapping)][:MAX_PARTS]
        if not brand or not (model or model_key):
            return failed("not_supported", "the case has no vehicle make and model")

        search = self._search(brand, model, model_key, year)
        if pinned:
            chosen = self._pinned(pinned, search, model_key)
            if chosen is None:
                return failed("no_vehicle", f"the chosen catalogue car {pinned} is not known to the catalogue worker; "
                              "look up again without choosing a car", search.failures)
            found = [chosen] + [c for c in search.found if c[0].key != chosen[0].key]
        elif search.found:
            found = search.found
        else:
            return failed(search.code or "no_vehicle", search.text or "no catalogue car found", search.failures)
        vehicle, twin_of = found[0]
        log.info("car: %s %s (%s) market=%s twin_of=%s", vehicle.key, vehicle.model, vehicle.name,
                 clip(vehicle.market, 60), twin_of)
        if progress(0, len(specs), clip(f"Car: {vehicle.model or ''} {vehicle.name or ''}".strip(), 200) or ""):
            raise Cancelled

        parts: list[dict[str, Any]] = []
        stopped: dict[str, Any] | None = None  # failure that made us stop asking the source
        for i, spec in enumerate(specs, start=1):
            if stopped is not None:
                out = self._empty_part(spec)
                out["failures"].append(failure(stopped["source"], stopped["code"],
                                               f"not tried: the source stopped earlier in this job ({stopped['code']})"))
            else:
                out, stopped = self._lookup_part(vehicle, spec)
            parts.append(out)
            if out["diagrams"]:
                note = out["diagrams"][0]["diagram_name"]
            elif out["failures"]:
                note = f"failed ({out['failures'][0]['code']})"
            else:
                note = "not in the catalogue"
            if progress(i, len(specs), clip(f"{spec.source_part}: {note}", 200) or ""):
                raise Cancelled
        brand_shown = brand if normalize_brand(brand) == vehicle.brand else vehicle.brand
        return {
            "status": "done",
            "error_code": None,
            "error_text": None,
            "vehicle": vehicle_out(vehicle, twin_of, brand_shown),
            "candidates": [vehicle_out(v, t, brand_shown) for v, t in found[:MAX_CANDIDATES]],
            "parts": parts,
            "failures": search.failures,
        }

    # the car ------------------------------------------------------------------------

    def _route(self, brand: str) -> list[str]:
        loaded = self.service.sources
        names = [n for n in self.routes.get(normalize_brand(brand), self.default_route) if n in loaded]
        if not names:  # configured route not loaded: any loaded source that lists the brand
            for name, src in loaded.items():
                try:
                    if normalize_brand(brand) in src.brands():
                        names.append(name)
                except Exception:
                    log.exception("brands() failed for %s", name)
        return names

    def _aliases(self, source: str, brand: str, model_key: str) -> list[str]:
        table = self.aliases.get(source, {}).get(normalize_brand(brand), {})
        return list(table.get(_norm(model_key), ()))

    def _search(self, brand: str, model: str, model_key: str, year: int | None) -> Search:
        names = self._route(brand)
        if not names:
            return Search(code="not_supported", text=f"no catalogue here serves {brand}")
        out = Search()
        errors: list[tuple[str, str]] = []
        for name in names:
            try:
                found, twin_of, notes = self._find_on(name, brand, model, model_key, year)
            except Exception as exc:  # fetch / block / parse errors: next source, if any
                if not isinstance(exc, PartsError):
                    log.exception("vehicle search crashed on %s", name)
                out.failures.append(failure_from_exc(name, exc))
                errors.append((out.failures[-1]["code"], short_text(exc)))
                continue
            out.failures.extend(notes)
            if found:
                out.found = [(v, twin_of) for v in rank_vehicles(found)]
                return out
            errors.extend((f["code"], f["message"] or "") for f in notes)
        asked = f"{brand} {model or model_key} {year if year is not None else '(no year)'}"
        for code in ("needs_human", "site_blocked", "challenge_blocked", "daily_cap_reached", "internal",
                     "not_supported"):
            hit = next((text for c, text in errors if c == code), None)
            if hit is not None:
                out.code, out.text = code, hit
                return out
        out.code, out.text = "no_vehicle", f"no catalogue car found for {asked} (searched: {', '.join(names)})"
        return out

    def _find_on(self, source: str, brand: str, model: str, model_key: str,
                 year: int | None) -> tuple[list[Vehicle], str | None, list[dict[str, Any]]]:
        """Vehicles on one source: the case's own model name first (model_key, then the
        full model text), then the source's twin names. Returns (vehicles, twin_of, notes)."""
        found, notes = self._try_names(source, brand, [model_key, model], year)
        if found:
            return found, None, notes
        rest = model.split(" ", 1)[1] if model.lower().startswith(model_key.lower() + " ") else ""
        for alias in self._aliases(source, brand, model_key):
            twin, twin_notes = self._try_names(source, brand, [f"{alias} {rest}".strip(), alias], year)
            if twin:
                log.info("%s has no %s; using its market twin %s", source, model_key, alias)
                return twin, model_key, twin_notes
            notes += twin_notes
        return [], None, notes

    def _try_names(self, source: str, brand: str, texts: Sequence[str],
                   year: int | None) -> tuple[list[Vehicle], list[dict[str, Any]]]:
        notes: list[dict[str, Any]] = []
        trims: ChooseTrim | None = None
        seen: set[str] = set()
        for text in texts:
            if not text or _norm(text) in seen:
                continue
            seen.add(_norm(text))
            try:
                answer = self.service.find_vehicles(brand, text, year, sources=[source])
            except ChooseTrim as exc:
                trims = exc  # the later (longer) text may name the trim itself: try it first
                continue
            except NotSupported as exc:
                notes.append(failure_from_exc(source, exc))
                continue
            notes += [failure_from_dict(f, source) for f in answer.failures]
            hits = [v for v in answer.value if covers_year(v, year)]
            if hits:
                return hits, notes
        if trims is not None:
            found, note = self._open_trims(source, trims, year)
            if note:
                notes.append(note)
            if found:
                return found, notes
            notes.append(failure_from_exc(source, trims))
        return [], notes

    def _open_trims(self, source: str, exc: ChooseTrim, year: int | None) -> tuple[list[Vehicle], dict[str, Any] | None]:
        """The model has many trims and the case does not say which: open some (cached pages
        first, then in the catalogue's order), stopping at the first Singapore-market car."""
        order = [t for t in exc.trims if t in exc.cached] + [t for t in exc.trims if t not in exc.cached]
        found: dict[str, Vehicle] = {}
        opened: list[str] = []
        for label in order[: max(0, self.max_trim_pages)]:
            try:
                answer = self.service.find_vehicles(exc.brand, f"{exc.series} {label}", exc.year, sources=[source])
            except NotSupported:
                continue
            except PartsError:
                if found:  # keep what the earlier trims gave
                    break
                raise
            opened.append(label)
            for v in answer.value:
                if covers_year(v, year):
                    found.setdefault(v.key, v)
            if any(market_rank(v) == 0 for v in found.values()):
                break
        note = None
        if len(opened) < len(exc.trims):
            note = failure(source, "not_supported",
                           f"{exc.brand} {exc.series} {exc.year} lists {len(exc.trims)} trims and the case names "
                           f"none; opened {len(opened)} ({', '.join(opened) or 'none'}). Put the trim in the model "
                           "text to reach the others.")
        return list(found.values()), note

    def _pinned(self, key: str, search: Search, model_key: str) -> tuple[Vehicle, str | None] | None:
        """The car the surveyor chose ("Change car"), from the store or this search."""
        try:
            vehicle = self.service.vehicle(key)
        except NotFound:
            vehicle = next((v for v, _ in search.found if v.key == key), None)
        if vehicle is None:
            return None
        twin = next((t for v, t in search.found if v.key == key), None)
        if twin is None and _norm(vehicle.model) != _norm(model_key):
            names = self._aliases(vehicle.source, vehicle.brand, model_key)
            if any(_norm(vehicle.model).startswith(_norm(a)) for a in names):
                twin = model_key
        return vehicle, twin

    # the parts ------------------------------------------------------------------------

    @staticmethod
    def _empty_part(spec: PartSpec) -> dict[str, Any]:
        return {"source_part": spec.source_part, "query": spec.query, "side": spec.side,
                "diagrams": [], "failures": []}

    def _lookup_part(self, vehicle: Vehicle, spec: PartSpec) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Diagrams and rows for one damaged part. Returns (part, stop failure or None)."""
        out = self._empty_part(spec)
        if not spec.query:
            out["failures"].append(failure(vehicle.source, "not_supported", "no part name to look up"))
            return out, None
        try:
            search = self.service.find_diagrams(vehicle, spec.query, max_new_groups=3, limit=5)
        except Exception as exc:
            if not isinstance(exc, PartsError):
                log.exception("find_diagrams crashed for %r", spec.query)
            f = failure_from_exc(vehicle.source, exc)
            out["failures"].append(f)
            return out, f if f["code"] in STOP_CODES else None
        stop: dict[str, Any] | None = None
        for raw in search.failures:
            f = failure_from_dict(raw, vehicle.source)
            out["failures"].append(f)
            if stop is None and f["code"] in STOP_CODES:
                stop = f
        matches = [m for m in search.matches if m.score >= MIN_SCORE][:MAX_DIAGRAMS]
        for match in matches:
            if stop is not None:
                break
            try:
                answer = self.service.get_diagram(vehicle, match.diagram)
            except Exception as exc:
                if not isinstance(exc, PartsError):
                    log.exception("get_diagram crashed for %s", match.diagram.key)
                f = failure_from_exc(vehicle.source, exc)
                out["failures"].append(f)
                if f["code"] in STOP_CODES:
                    stop = f
                continue
            out["failures"] += [failure_from_dict(raw, vehicle.source) for raw in answer.failures]
            full, rows = answer.value
            kept = [r for r in rows if not _opposite(spec.side, r.side)]
            out["diagrams"].append({
                "diagram_key": full.key,
                "diagram_name": full.name,
                "diagram_url": full.url,
                "image_url": full.image_url,
                "match_score": min(1.0, max(0.0, round(float(match.score), 3))),
                "rows": [row_out(r, answer.stale) for r in kept],
            })
        log.info("part %r (%s): %s", spec.source_part, spec.query,
                 [(d["diagram_name"], d["match_score"], len(d["rows"])) for d in out["diagrams"]] or "none")
        return out, stop


# --- result shapes ------------------------------------------------------------------------


def vehicle_out(v: Vehicle, twin_of: str | None, brand: str | None = None) -> dict[str, Any]:
    return {
        "key": v.key,
        "source": v.source,
        "brand": brand or v.brand,
        "model": v.model,
        "name": v.name,
        "market": v.market,
        "year_from": in_range(v.year_from, *YEAR_RANGE),
        "year_to": in_range(v.year_to, *YEAR_RANGE),
        "url": v.url,
        "twin_of": twin_of,
    }


def row_out(r: PartRow, stale: bool) -> dict[str, Any]:
    """A part row as the contract shows it. The reference price is the one printed on the
    diagram page (row attributes); no price page is fetched here."""
    amount = in_range(_float(r.attributes.get("price")), 0, MAX_REF_AMOUNT)
    return {
        "callout": r.callout,
        "part_number": r.part_number,
        "superseded_from": r.superseded_from,
        "name": r.name,
        "name_original": r.name_original,
        "qty": r.qty,
        "side": r.side,
        "notes": r.notes,
        "ref_amount": amount,
        "ref_currency": (r.attributes.get("currency") or None) if amount is not None else None,
        "ref_tax_note": (r.attributes.get("price_note") or None) if amount is not None else None,
        "source": r.source,
        "url": r.url,
        "fetched_at": _iso(r.fetched_at),
        "stale": bool(stale),
    }


def failed(code: str, text: str, failures: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "status": "failed",
        "error_code": code if code in ERROR_CODES else "internal",
        "error_text": text,
        "vehicle": None,
        "candidates": [],
        "parts": [],
        "failures": list(failures or []),
    }


_URL_KEYS = ("url", "diagram_url", "image_url")
_KEY_KEYS = ("key", "diagram_key")


def _capped(obj: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in obj.items():
        if k in _URL_KEYS:
            out[k] = safe_url(v)
        elif k in _KEY_KEYS:
            out[k] = v if v is None or len(str(v)) <= MAX_STR else None  # a cut key would point elsewhere
        elif isinstance(v, str):
            out[k] = clip(v)
        else:
            out[k] = v
    return out


def apply_caps(result: Mapping[str, Any]) -> dict[str, Any]:
    """The contract's limits, enforced before sending: 40 parts, 3 diagrams per part,
    250 rows per diagram, 10 candidates, strings <= 400 chars, URLs https on the
    catalogue hosts (others become null). Returns a new dict."""
    out = _capped(dict(result))
    out["vehicle"] = _capped(result["vehicle"]) if result.get("vehicle") else None
    out["candidates"] = [_capped(c) for c in (result.get("candidates") or [])][:MAX_CANDIDATES]
    out["failures"] = [_capped(f) for f in (result.get("failures") or [])][:MAX_FAILURES]
    parts = []
    for part in (result.get("parts") or [])[:MAX_PARTS]:
        p = _capped(part)
        p["failures"] = [_capped(f) for f in (part.get("failures") or [])][:MAX_PART_FAILURES]
        diagrams = []
        for d in (part.get("diagrams") or [])[:MAX_DIAGRAMS]:
            dd = _capped(d)
            dd["rows"] = [_capped(r) for r in (d.get("rows") or [])][:MAX_ROWS]
            diagrams.append(dd)
        p["diagrams"] = diagrams
        parts.append(p)
    out["parts"] = parts
    return out


# --- talking to the server ------------------------------------------------------------------


class ServerError(Exception):
    """The server could not be reached or answered with an error; try again later."""


class FeatureOff(ServerError):
    """The claim was answered 404: the catalogue look-up is switched off on the server
    (CATALOGUE_ENABLED / CATALOGUE_WORKER_TOKEN), or the URL does not serve it."""


class ResultRejected(Exception):
    """The server refused the result itself (e.g. 422); sending it again will not help."""


# Plain http is allowed to these hosts only: this machine (a local test server, e.g.
# apps/bot-service/scripts/e2e_catalogue.py) and "api", the API container's name inside
# the compose network (infra/docker-compose.prod.yml), which never leaves the VM.
PLAIN_HTTP_HOSTS = ("localhost", "127.0.0.1", "api")


def check_base_url(base: str) -> str:
    """https, or plain http to PLAIN_HTTP_HOSTS only. The token is never sent in the
    clear anywhere else ("localhost.example.com", "127.0.0.1.nip.io" and "api.example.com"
    are other machines)."""
    rule = f"CARLINK_API_BASE must be an https URL, or http to {', '.join(PLAIN_HTTP_HOSTS)}"
    try:
        parts = urlsplit(base)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise ValueError(f"{rule} (got an unreadable URL)") from None
    if parts.scheme != "https" and not (parts.scheme == "http" and host in PLAIN_HTTP_HOSTS):
        raise ValueError(f"{rule} (got {parts.scheme or 'no'} scheme{f', host {host}' if host else ''})")
    return base.rstrip("/")


class ServerClient:
    """The three worker endpoints. Plain https with certificate checks; the token goes in
    the Authorization header only and never into logs or error messages."""

    def __init__(self, base_url: str, token: str, *, worker: str, transport: Any = None,
                 timeout: float = 30.0) -> None:
        if not token:
            raise ValueError("CATALOGUE_WORKER_TOKEN is not set")
        self.worker = worker
        self.base_url = check_base_url(base_url)
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}", "User-Agent": f"CarlinkCatalogueWorker/{VERSION}"},
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )

    def _post(self, path: str, body: Mapping[str, Any]) -> Any:
        try:
            return self._http.post(path, json=body)
        except httpx.HTTPError as exc:  # connect, timeout, TLS
            raise ServerError(clip(f"{type(exc).__name__}: {exc}", 200)) from None

    @staticmethod
    def _why(resp: Any) -> str:
        hint = {401: "token rejected", 403: "token rejected", 404: "catalogue feature off or wrong URL",
                429: "too many requests"}.get(resp.status_code, "server error")
        return f"HTTP {resp.status_code} ({hint})"

    @staticmethod
    def _json(resp: Any) -> dict[str, Any]:
        try:
            data = resp.json()
        except ValueError:
            raise ServerError(f"HTTP {resp.status_code}: answer is not JSON") from None
        if not isinstance(data, dict):
            raise ServerError(f"HTTP {resp.status_code}: unexpected answer")
        return data

    def claim(self) -> tuple[dict[str, Any] | None, float]:
        resp = self._post("/catalogue-worker/claim", {"worker": self.worker, "version": VERSION})
        if resp.status_code == 404:
            raise FeatureOff(self._why(resp))
        if resp.status_code != 200:
            raise ServerError(self._why(resp))
        data = self._json(resp)
        job = data.get("job")
        poll = _float(data.get("poll_seconds")) or DEFAULT_POLL_SECONDS
        return (job if isinstance(job, dict) else None), min(max(poll, 5.0), 600.0)

    def progress(self, job_id: Any, done: int, total: int, message: str) -> bool:
        """True when the worker must stop (cancelled, or the job is no longer running)."""
        resp = self._post(f"/catalogue-worker/jobs/{job_id}/progress",
                          {"done_parts": done, "total_parts": total, "message": message})
        if resp.status_code in (404, 409):
            return True
        if resp.status_code != 200:
            raise ServerError(self._why(resp))
        return bool(self._json(resp).get("cancelled"))

    def result(self, job_id: Any, result: Mapping[str, Any]) -> dict[str, Any] | None:
        """The server's answer, or None when it no longer wants this job (409)."""
        resp = self._post(f"/catalogue-worker/jobs/{job_id}/result", result)
        if resp.status_code == 409:
            return None
        if resp.status_code in (400, 413, 422):
            raise ResultRejected(f"HTTP {resp.status_code}: {clip(_detail(resp), 300)}")
        if resp.status_code != 200:
            raise ServerError(self._why(resp))
        return self._json(resp)

    def close(self) -> None:
        self._http.close()


def _detail(resp: Any) -> str:
    try:
        data = resp.json()
    except ValueError:
        return "no detail"
    return json.dumps(data.get("detail") if isinstance(data, dict) else data, ensure_ascii=False)[:300]


class Worker:
    """The loop: claim -> process -> progress / result; sleep when there is no job;
    back off (30 s, doubling to 5 min) while the server cannot be reached; while the
    look-up is switched off (claim answers 404), ask every 5 min and log only the change."""

    def __init__(
        self,
        client: ServerClient,
        processor: JobProcessor,
        *,
        sleep: Callable[[float], None] = time.sleep,
        first_backoff: float = FIRST_BACKOFF,
        max_backoff: float = MAX_BACKOFF,
        result_attempts: int = RESULT_ATTEMPTS,
        live_counter: Callable[[], dict[str, int]] | None = None,
        off_poll: float = OFF_POLL_SECONDS,
    ) -> None:
        self.client = client
        self.processor = processor
        self.sleep = sleep
        self.first_backoff = first_backoff
        self.max_backoff = max_backoff
        self.result_attempts = result_attempts
        self.live_counter = live_counter
        self.off_poll = off_poll

    def _next_backoff(self, current: float) -> float:
        return self.first_backoff if current <= 0 else min(current * 2, self.max_backoff)

    def run(self, stop: Callable[[], bool] = lambda: False, *, once: bool = False) -> int:
        """Runs until `stop()` is true or Ctrl+C (KeyboardInterrupt propagates).
        once: ask once and handle at most one job, then return (an unreachable
        server is not waited for). Returns how many jobs were handled."""
        backoff = 0.0
        handled = 0
        off = False  # the server said 404 (look-up off); logged once, until it changes
        while not stop():
            try:
                job, poll = self.client.claim()
            except FeatureOff as exc:
                if once:
                    log.info("catalogue look-up is off on the server: %s", exc)
                    return handled
                if not off:
                    log.info("catalogue look-up is off on the server: %s; asking again every %.0f min "
                             "(no more log lines until it changes)", exc, self.off_poll / 60)
                    off = True
                backoff = 0.0
                self.sleep(self.off_poll)
                continue
            except ServerError as exc:
                if once:
                    log.error("server not reachable: %s", exc)
                    return handled
                backoff = self._next_backoff(backoff)
                log.warning("server not reachable: %s; trying again in %.0f s", exc, backoff)
                self.sleep(backoff)
                continue
            if off:
                log.info("catalogue look-up is on on the server; polling")
                off = False
            elif backoff:
                log.info("server reachable again")
            backoff = 0.0
            if job is None:
                if once:
                    log.info("no job waiting")
                    return handled
                self.sleep(poll)
                continue
            self.handle(job)
            handled += 1
            if once:
                return handled
        return handled

    def handle(self, job: Mapping[str, Any]) -> str:
        """Process one claimed job. Returns posted | dropped | cancelled | rejected | lost | bad_job."""
        job_id = job.get("job_id")
        if not isinstance(job_id, int) and not str(job_id or "").isdigit():
            log.error("claimed a job without a usable job_id; skipping it")
            return "bad_job"
        log.info("job %s: %s %s %s, %d part(s)%s", job_id, job.get("brand"), job.get("model"), job.get("year"),
                 len(job.get("parts") or []), f", car {job['vehicle_key']}" if job.get("vehicle_key") else "")
        before = self.live_counter() if self.live_counter else {}
        started = time.monotonic()

        def progress(done: int, total: int, message: str) -> bool:
            try:
                return self.client.progress(job_id, done, total, message)
            except ServerError as exc:
                log.warning("job %s: progress not sent (%s); carrying on", job_id, exc)
                return False

        result = self.processor.process(job, progress)
        if self.live_counter:
            log.info("job %s: %s live site request(s) in %.0f s", job_id,
                     live_diff(before, self.live_counter()) or "no", time.monotonic() - started)
        if result is None:
            return "cancelled"
        delay = 0.0
        for attempt in range(1, self.result_attempts + 1):
            try:
                answer = self.client.result(job_id, result)
            except ResultRejected as exc:
                log.error("job %s: the server refused the result: %s", job_id, exc)
                return "rejected"
            except ServerError as exc:
                if attempt == self.result_attempts:
                    log.error("job %s: result not delivered after %d tries (%s); dropping it "
                              "(the server re-queues the job)", job_id, attempt, exc)
                    return "lost"
                delay = self._next_backoff(delay)
                log.warning("job %s: result not sent (%s); trying again in %.0f s", job_id, exc, delay)
                self.sleep(delay)
                continue
            if answer is None:
                log.info("job %s: the server no longer wants it (cancelled or done); dropped", job_id)
                return "dropped"
            log.info("job %s: %s; %s suggestion(s) stored", job_id, result.get("status"), answer.get("suggestions"))
            return "posted"
        return "lost"


def live_diff(before: Mapping[str, int], after: Mapping[str, int]) -> dict[str, int]:
    return {h: n - before.get(h, 0) for h, n in after.items() if n - before.get(h, 0) > 0}


# --- wiring -------------------------------------------------------------------------------------


class RedactFilter(logging.Filter):
    """Belt and braces: the token never reaches a log line, whatever logs it."""

    def __init__(self, secret: str | None) -> None:
        super().__init__()
        self.secret = secret or ""

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secret and len(self.secret) >= 4:
            message = record.getMessage()
            if self.secret in message:
                record.msg, record.args = message.replace(self.secret, "[token]"), None
        return True


class OneLineFormatter(logging.Formatter):
    """'2026-11-12T10:42:00Z INFO worker job 12: ...': one line per event, as a container
    log is read. Line breaks in a message or a traceback become ' | '. The token is
    replaced wherever it shows, tracebacks included."""

    def __init__(self, secret: str | None = None) -> None:
        super().__init__("%(asctime)s %(levelname)s %(short_name)s %(message)s")
        self.secret = secret or ""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created))

    def format(self, record: logging.LogRecord) -> str:
        record.short_name = record.name.removeprefix("parts_mcp.")
        text = super().format(record)
        if self.secret and len(self.secret) >= 4:
            text = text.replace(self.secret, "[token]")
        return " | ".join(line.strip() for line in text.splitlines() if line.strip())


def log_file_wanted(env: Mapping[str, str]) -> bool:
    """PARTS_MCP_LOG_FILE=off: the console only (the container: docker logs keeps them)."""
    return env.get("PARTS_MCP_LOG_FILE", "").strip().lower() not in ("off", "0", "false", "no", "none")


def setup_logging(settings: Settings, *, token: str | None, level: int = logging.INFO, stream: Any = None,
                  log_file: bool = True) -> Callable[[], None]:
    """One line per event to `stream` (default stdout) and, with log_file, to
    <data>/logs/worker.log; token redacted. Returns a function that undoes it."""
    logger = logging.getLogger("parts_mcp")
    saved = (logger.level, logger.propagate)
    logger.setLevel(level)
    logger.propagate = False
    fmt = OneLineFormatter(token)
    redact = RedactFilter(token)
    handlers: list[logging.Handler] = [logging.StreamHandler(stream or sys.stdout)]
    if log_file:
        handlers.append(logging.handlers.RotatingFileHandler(settings.log_dir / "worker.log", maxBytes=5_000_000,
                                                             backupCount=3, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(fmt)
        handler.addFilter(redact)
        logger.addHandler(handler)
    for name in ("httpx", "httpx2", "httpcore", "httpcore2"):  # request lines only, but keep them quiet
        logging.getLogger(name).setLevel(logging.WARNING)

    def undo() -> None:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(saved[0])
        logger.propagate = saved[1]

    return undo


@dataclass
class Runtime:
    service: CatalogueService
    page_cache: PageCache

    def live_counts(self) -> dict[str, int]:
        return self.page_cache.requests_by_host(utcnow().date().isoformat())

    def close(self) -> None:
        self.service.close()
        self.page_cache.close()


def build_runtime(settings: Settings) -> Runtime:
    """Like server.build_service, without the MCP SDK."""
    settings.ensure_dirs()
    page_cache = PageCache(settings.page_cache_db)
    store = open_store(settings.catalogue_db)
    sources, problems = load_sources(settings, page_cache)
    for name, why in problems.items():
        log.warning("source %s not loaded: %s", name, why)
    service = CatalogueService(
        store,
        sources,
        routes=settings.routes,
        default_route=settings.default_route,
        catalogue_ttl=settings.catalogue_ttl,
        price_ttl=settings.price_ttl,
        unavailable=problems,
    )
    return Runtime(service, page_cache)


class Terminated(KeyboardInterrupt):
    """SIGTERM (docker stop): stop like Ctrl+C."""


def _on_sigterm(signum: int, frame: Any) -> None:
    raise Terminated()


def sigterm_stops() -> Callable[[], None]:
    """Make SIGTERM stop the worker like Ctrl+C. In a container the worker is PID 1,
    and PID 1 ignores a signal it has no handler for: `docker stop` would wait 10 s
    and kill it. Returns a function that puts the old handler back."""
    try:
        previous = signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError, AttributeError):  # not the main thread, or no SIGTERM here
        return lambda: None

    def undo() -> None:
        try:
            signal.signal(signal.SIGTERM, previous)
        except (ValueError, OSError, TypeError):
            pass

    return undo


def worker_name(env: Mapping[str, str]) -> str:
    return _clean(env.get("WORKER_NAME") or env.get("COMPUTERNAME") or socket.gethostname()) or "parts-worker"


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m parts_mcp.worker",
                                     description="Carlink catalogue worker (see the module docstring).")
    parser.add_argument("--job", help="dry run: process this Job JSON file without any server")
    parser.add_argument("--out", help="dry run: write the Result JSON here (default: stdout)")
    parser.add_argument("--data-dir", help="data folder (default $PARTS_MCP_DATA_DIR or ~/carlink-parts-data)")
    parser.add_argument("--sources", help="comma list of sources to load, e.g. microfiches")
    parser.add_argument("--api-base", help="server API base (default $CARLINK_API_BASE)")
    parser.add_argument("--once", action="store_true",
                        help="ask the server once, handle at most one job, then stop")
    parser.add_argument("--max-trim-pages", type=int, default=MAX_TRIM_PAGES,
                        help=f"trims opened when the model text names none (default {MAX_TRIM_PAGES})")
    parser.add_argument("--stay-up", action="store_true",
                        help="without a token, say so once and idle instead of exiting (the container: "
                             "an exit would only be restarted, and log again, every minute)")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    transport: Any = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    env = os.environ if env is None else env
    args = parse_args(argv)
    token = env.get("CATALOGUE_WORKER_TOKEN", "").strip()
    sources = tuple(s.strip() for s in args.sources.split(",") if s.strip()) if args.sources else None
    settings = load_settings(
        data_dir=args.data_dir or env.get("PARTS_MCP_DATA_DIR") or DEFAULT_DATA_DIR, sources=sources, env=env
    )
    settings.ensure_dirs()
    # A dry run that prints its result keeps stdout for the JSON.
    stream = sys.stderr if args.job and not args.out else sys.stdout
    undo_logging = setup_logging(settings, token=token, level=logging.DEBUG if args.debug else logging.INFO,
                                 stream=stream, log_file=log_file_wanted(env))
    undo_signal = sigterm_stops()
    try:
        return _run(args, env, settings, token, transport, sleep)
    finally:
        undo_signal()
        undo_logging()


def _run(args: argparse.Namespace, env: Mapping[str, str], settings: Settings, token: str, transport: Any,
         sleep: Callable[[float], None]) -> int:
    client = None
    if not args.job:
        if not token and args.stay_up:
            log.info("CATALOGUE_WORKER_TOKEN is not set, so the catalogue look-up is off; idling. Set it (and "
                     "CATALOGUE_ENABLED=true) in infra/.env and deploy again to start.")
            try:
                while True:
                    sleep(IDLE_SECONDS)
            except KeyboardInterrupt as exc:
                log.info("stopped (%s)", "SIGTERM" if isinstance(exc, Terminated) else "Ctrl+C")
            return 0
        if not token:
            log.error("CATALOGUE_WORKER_TOKEN is not set (server: infra/.env; PC: worker.env, see README); "
                      "or use --job for a dry run")
            return 2
        try:
            client = ServerClient(args.api_base or env.get("CARLINK_API_BASE") or DEFAULT_API_BASE, token,
                                  worker=worker_name(env), transport=transport)
        except ValueError as exc:  # not https, and the like
            log.error("%s", exc)
            return 2
    runtime = build_runtime(settings)
    processor = JobProcessor(runtime.service, max_trim_pages=args.max_trim_pages)
    try:
        if client is None:
            return dry_run(args, processor, runtime)
        log.info("worker %s %s: sources=%s data=%s api=%s; polling", client.worker, VERSION,
                 list(runtime.service.sources), settings.data_dir, client.base_url)
        Worker(client, processor, sleep=sleep, live_counter=runtime.live_counts).run(once=args.once)
    except KeyboardInterrupt as exc:
        log.info("stopped (%s)", "SIGTERM" if isinstance(exc, Terminated) else "Ctrl+C")
    finally:
        if client is not None:
            client.close()
        runtime.close()
    return 0


def dry_run(args: argparse.Namespace, processor: JobProcessor, runtime: Runtime) -> int:
    job = json.loads(Path(args.job).read_text(encoding="utf-8"))
    before = runtime.live_counts()
    started = time.monotonic()

    def progress(done: int, total: int, message: str) -> bool:
        log.info("progress %d/%d %s", done, total, message)
        return False

    result = processor.process(job, progress)
    live = live_diff(before, runtime.live_counts())
    log.info("dry run: %s live site request(s) in %.0f s", live or "no", time.monotonic() - started)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        log.info("result written to %s", args.out)
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
