"""Microfiches.net dealer shops: BMW, MINI and Honda parts catalogues.

Three sister shops on one platform (server HTML only; no JSON, no paging):

    BMW    https://www.bmw-spare-parts.com   /bmw-cars/...
    MINI   https://www.mini-spare-parts.com  /mini-cars/...
    HONDA  https://www.parts-honda.uk        /honda-cars/...

Navigation, as links on each page (we follow links, we never build ids):

    /{prefix}                    model / series list          a.btn-identification
    /{prefix}/{SERIES}           years                        a.btn-identification
    /{prefix}/{SERIES}/{YYYY}    BMW, MINI: one card per type (div.infos_vehicle)
                                 HONDA: trims ("finish lines"); one more level,
                                 /{YYYY}/{TRIM}, holds the type cards, grouped
                                 under headings like "4 Doors 5 speed automatic"
    type page  .../{typeId}      BMW, MINI: main groups (a > h3), one page per group
                                 HONDA: every diagram of every group on this page
    group page .../{Group}/{GG}/{typeId}               diagrams (a > h2)
    diagram    .../{slug}/{v}/{code}/{GG}/{typeId}     image, hotspots, part rows
    part page  /{prefix}/assignment_spare_parts/{pn}   name, price
    VIN        /{prefix}/search_vehicle/chassis/{VIN}  type cards (matched on the
               last 7 characters only, so results are checked, see `check_vin`)

BMW / MINI part names are Italian (the seller is an Italian dealer); `name`
gets a best-effort English version from `microfiches_glossary`. Honda names
are English already. Prices are what the page shows (EUR, excl. VAT) and are
never converted. Quantity per vehicle is not shown, so `qty` is None.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup, Tag

from ..core.errors import ChooseTrim, NotFound, NotSupported, ParseError
from ..core.fetch import TTL_CATALOGUE, TTL_PRICE, FetchResult, PoliteFetcher
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
from . import microfiches_glossary as glossary
from .base import Source, SourceContext

log = logging.getLogger("parts_mcp.sources.microfiches")

NAME = "microfiches"
MAX_TRIM_PAGES = 3  # Honda: trims fetched for one find_vehicles call when the model text names none


@dataclass(frozen=True)
class Site:
    brand: str
    host: str
    prefix: str  # first path segment, e.g. "bmw-cars"
    names_language: str  # "it": part names are Italian and get translated; "en": English already
    tax_note: str
    vin_type_key: bool  # BMW group VINs carry the 4-character type key at positions 4-7
    wmis: tuple[str, ...]  # VIN prefixes routed to this site

    @property
    def base(self) -> str:
        return f"https://{self.host}"

    def url(self, path: str) -> str:
        return self.base + quote(path, safe="/%-_.~!$&'()*+,;=:@")


SITES: dict[str, Site] = {
    "BMW": Site(
        brand="BMW",
        host="www.bmw-spare-parts.com",
        prefix="bmw-cars",
        names_language="it",
        tax_note="excl. VAT; BMW EU list price (seller: Autolux, Italy)",
        vin_type_key=True,
        wmis=("WBA", "WBS", "WBY", "WBX", "WBW", "4US", "5UX", "5UJ", "5YM", "X4X"),
    ),
    "MINI": Site(
        brand="MINI",
        host="www.mini-spare-parts.com",
        prefix="mini-cars",
        names_language="it",
        tax_note="excl. VAT; MINI EU list price (seller: Autolux, Italy)",
        vin_type_key=True,
        wmis=("WMW", "WMZ", "WMX"),
    ),
    "HONDA": Site(
        brand="HONDA",
        host="www.parts-honda.uk",
        prefix="honda-cars",
        names_language="en",
        tax_note="excl. VAT; Honda list price as shown by parts-honda.uk (seller not named on the page)",
        vin_type_key=False,
        wmis=("JHM", "JHL", "JHG", "JH4", "SHH", "SHS", "1HG", "2HG", "3HG", "5FN", "5J6", "5J8",
              "19X", "19U", "MRH", "MHR", "MAK", "NLA", "LUC", "LVH", "93H"),
    ),
}


# --- small text helpers -----------------------------------------------------------


def _clean(text: str | None) -> str:
    return " ".join((text or "").replace("\xa0", " ").split())


def _soup(html: str) -> BeautifulSoup:
    # Honda cards close lines with "</br>", which lxml would drop entirely.
    return BeautifulSoup(re.sub(r"</br\s*>", "<br>", html, flags=re.IGNORECASE), "lxml")


def _norm(text: str) -> str:
    """'HR-V' -> 'hrv', '1.8 EXI' -> '18exi': for matching names typed by people."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _words(text: str) -> list[str]:
    """'Civic 1.8 EXi (FD)' -> ['civic', '18', 'exi', 'fd']."""
    return [w for w in (_norm(t) for t in re.split(r"[\s,/()]+", text)) if w]


def _segments(href: str | None) -> list[str] | None:
    """Path segments of a site-relative link, keeping empty ones ('2019//318d')."""
    href = (href or "").strip()
    if not href.startswith("/") or href.startswith("//"):
        return None
    path = urlsplit(href).path
    return path.strip("/").split("/") if path.strip("/") else []


def _href_path(a: Tag) -> str:
    return urlsplit(str(a.get("href", "")).strip()).path


def _lines(tag: Tag) -> list[str]:
    """Visible lines of a tag, split at <br> and block ends."""
    for br in tag.find_all("br"):
        br.replace_with("\n")
    for block in tag.find_all(["p", "div", "li"]):
        block.append("\n")
    return [line for line in (_clean(x) for x in tag.get_text().split("\n")) if line]


def _own_text(tag: Tag) -> str:
    """Text directly inside a tag, without its child elements ('Trim lid <span>for BMW</span>')."""
    return _clean("".join(s for s in tag.find_all(string=True, recursive=False)))


def _snake(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _popover_text(raw: str) -> str:
    text = re.sub(r"<br\s*/?>", " | ", raw, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _clean(text)
    text = re.sub(r"\b(For vehicles with)(\s+\1)+", r"\1", text)
    text = re.sub(r"(\s*\|\s*)+", " | ", text)
    return text.strip(" |")


# --- page parsers (pure: soup in, plain data out) ----------------------------------


def parse_index(soup: BeautifulSoup, prefix: str) -> list[tuple[str, str]]:
    """Model / series links of /{prefix}: [(label, path)]."""
    out: dict[str, tuple[str, str]] = {}
    links = soup.select("a.btn-identification[href]") or soup.find_all("a", href=True)
    for a in links:
        segs = _segments(a.get("href"))
        if not segs or len(segs) != 2 or segs[0] != prefix:
            continue
        if segs[1] in ("assignment_spare_parts", "search_vehicle", "price", "cart") or not segs[1]:
            continue
        label = _clean(a.get_text(" ")) or segs[1]
        out.setdefault(segs[1], (label, _href_path(a)))
    return list(out.values())


def parse_years(soup: BeautifulSoup, prefix: str, series_seg: str) -> list[tuple[int, str]]:
    """Year links of a series page: [(year, path)], ascending."""
    out: dict[int, str] = {}
    for a in soup.find_all("a", href=True):
        segs = _segments(a.get("href"))
        if (segs and len(segs) == 3 and segs[0] == prefix and segs[1] == series_seg
                and re.fullmatch(r"\d{4}", segs[2])):
            out.setdefault(int(segs[2]), _href_path(a))
    return sorted(out.items())


def parse_trims(soup: BeautifulSoup, prefix: str, series_seg: str, year: int) -> list[tuple[str, str]]:
    """Honda 'finish line' links of a year page: [(label, path)]."""
    out: dict[str, tuple[str, str]] = {}
    for a in soup.select("a.btn-identification[href]"):
        segs = _segments(a.get("href"))
        if segs and len(segs) == 4 and segs[0] == prefix and segs[1] == series_seg and segs[2] == str(year):
            out.setdefault(segs[3], (_clean(a.get_text(" ")) or segs[3], _href_path(a)))
    return list(out.values())


@dataclass
class TypeCard:
    """One 'View catalogue' card on a year / trim / VIN result page."""

    type_id: str
    path: str
    header: str | None = None  # "BMW X1"
    labels: dict[str, str] = field(default_factory=dict)  # "Finish line" -> "F48"
    lines: list[str] = field(default_factory=list)  # unlabelled lines (Honda options)
    heading: str | None = None  # Honda section heading "4 Doors 5 speed automatic"
    markets: list[str] = field(default_factory=list)


def parse_type_cards(soup: BeautifulSoup) -> list[TypeCard]:
    cards: list[TypeCard] = []
    for info in soup.select("div.infos_vehicle"):
        a = info.find("a", href=True)
        segs = _segments(a.get("href")) if a else None
        if not a or not segs or len(segs) < 4 or not segs[-1]:
            continue
        card = TypeCard(type_id=segs[-1], path=_href_path(a))
        header = a.find("p")
        if header is not None:
            card.header = _clean(header.get_text(" ")) or None
            header.decompose()
        for line in _lines(a):
            m = re.match(r"^([A-Za-z][A-Za-z ]{1,40}?)\s*:\s*(.*)$", line)
            if m:
                card.labels[m.group(1).strip()] = m.group(2).strip()
            else:
                card.lines.append(line)
        box = info.find_parent("div", class_="card-modele") or info.parent
        tip = box.find(attrs={"data-bs-toggle": "tooltip", "title": True}) if box else None
        if tip is not None:
            listed = re.split(r"<\s*/?\s*br\s*/?\s*>", str(tip["title"]), flags=re.I)
            card.markets = [_clean(x) for x in listed if _clean(x)]
        elif box is not None and (flag := box.find("p", class_="text-microfiches")) is not None:
            card.markets = [_clean(x) for x in flag.get_text().split(",") if _clean(x)]
        collapse = info.find_parent("div", class_="accordion-collapse")
        if collapse is not None:
            heading = collapse.find_previous("span", class_="h4")
            card.heading = _clean(heading.get_text(" ")) if heading else None
        cards.append(card)
    return cards


def _type_tail(segs: list[str] | None, type_id: str) -> bool:
    return bool(segs) and len(segs) >= 4 and segs[-1] == type_id and bool(re.fullmatch(r"\d{1,3}", segs[-2]))


def parse_group_links(soup: BeautifulSoup, type_id: str) -> list[tuple[str, str, str]]:
    """BMW / MINI main-group cards: [(group number, name, path)]."""
    out: dict[str, tuple[str, str, str]] = {}
    for a in soup.find_all("a", href=True):
        segs = _segments(a.get("href"))
        if not _type_tail(segs, type_id) or re.search(r"\d", segs[-3]):  # type: ignore[index]
            continue
        head = a.find(["h3", "h2", "h4"])
        name = _clean((head or a).get_text(" "))
        if name:
            out.setdefault(segs[-2], (segs[-2], name, _href_path(a)))  # type: ignore[index]
    return list(out.values())


@dataclass
class DiagramLink:
    code: str  # the catalogue's diagram code: "51_3279", "B4650", "E__0100"
    group_id: str
    name: str
    path: str
    notes: str | None = None
    group_heading: str | None = None  # Honda: the section the card sits in
    raw_name: str | None = None  # the name as in the card image's alt text, spacing untouched


# Honda's source data wraps long diagram names at 25 characters and inserts a
# space there: "FRONT DOOR GLASS/DOOR REG ULATOR", "SIDE SILL GARNISH/UNDER C OVER".
# A real word break at that point shows as two spaces, or sits before a bracket
# the site appends ("CAMSHAFT/CAM CHAIN (1.5L) (KE/KG)"), so only a single space
# between two letters is taken as inserted.
HONDA_WRAP = 25


def unwrap_honda_name(raw: str | None, shown: str) -> str:
    """`shown` with a word split by Honda's 25-character wrap joined back
    ("DOOR REG ULATOR" -> "DOOR REGULATOR"). `raw` is the name with the site's
    original spacing (image alt text)."""
    at = HONDA_WRAP
    if not raw or len(raw) <= at + 1 or raw[at] != " " or not (raw[at - 1].isalpha() and raw[at + 1].isalpha()):
        return shown
    left = re.search(r"[A-Za-z]+$", raw[:at]).group()  # type: ignore[union-attr]
    right = re.match(r"[A-Za-z]+", raw[at + 1:]).group()  # type: ignore[union-attr]
    return re.sub(rf"(?<![A-Za-z]){re.escape(left)} {re.escape(right)}(?![A-Za-z])", left + right, shown, count=1)


def parse_diagram_links(soup: BeautifulSoup, type_id: str) -> list[DiagramLink]:
    out: dict[str, DiagramLink] = {}
    for a in soup.find_all("a", href=True):
        segs = _segments(a.get("href"))
        if not _type_tail(segs, type_id) or len(segs) < 6:  # type: ignore[arg-type]
            continue
        code = segs[-3]  # type: ignore[index]
        if not re.search(r"\d", code) or "-" in code:
            continue  # a group link: .../Vehicle-trim/51/{typeId}
        head = a.find(["h2", "h3"])
        name = _own_text(head) if head is not None else ""
        name = name or _clean(a.get_text(" "))
        if not name:
            continue
        notes_el = head.find("span", class_="card-text") if head is not None else None
        notes = _clean(notes_el.get_text(" ")) if notes_el is not None else None
        section = a.find_previous("h2", class_="titre_cat")
        img = a.find("img", alt=True)
        raw = re.split(r"\s+for\s+", str(img["alt"]), maxsplit=1)[0].strip() if img is not None else None
        out.setdefault(code, DiagramLink(
            code=code,
            group_id=segs[-2],  # type: ignore[index]
            name=name,
            path=_href_path(a),
            notes=notes or None,
            group_heading=(_clean(section.get_text(" ")) or None) if section is not None else None,
            raw_name=raw or None,
        ))
    return list(out.values())


@dataclass
class RowData:
    callout: str | None
    name_shown: str | None  # first line of the name cell, as shown
    remarks: list[str]  # further lines of the name cell ("PART NOT PAINTED")
    mpn: str | None  # number in the row's markup (the old one when superseded)
    current: str | None  # number the row links to (current number)
    current_display: str | None  # link text as shown ("51 12 7 403 392")
    struck: str | None  # crossed-out number as shown
    price: float | None
    currency: str | None
    availability: str | None
    price_note: str | None
    notes: list[str]
    sales_unit: str | None


@dataclass
class DiagramPage:
    title: str | None
    image_src: str | None
    image_width: int | None
    image_height: int | None
    hotspots: list[tuple[str, float | None, float | None]]  # (callout, x, y), de-duplicated
    rows: list[RowData]


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parse_row(row: Tag) -> RowData:
    def text(el: Tag | None) -> str | None:
        return _clean(el.get_text(" ")) or None if el is not None else None

    name_el = row.find("span", attrs={"itemprop": "name"})
    name_lines = _lines(name_el) if name_el is not None else []
    link = row.find("a", href=re.compile(r"/assignment_spare_parts/[^/]+$"))
    current = _segments(link.get("href"))[-1] if link is not None else None  # type: ignore[index]
    if link is not None:
        for hidden in link.select(".d-none"):  # a second, compact copy of the number
            hidden.decompose()
    price_el = row.find("span", attrs={"itemprop": "price"})
    currency_el = row.find(attrs={"itemprop": "priceCurrency"})
    avail_el = row.find("link", attrs={"itemprop": "availability"})
    box = row.find("div", class_="prix-etape7")
    not_sold = box is not None and "not available" in box.get_text(" ").lower()
    price = _float(price_el.get("content")) if price_el is not None else None
    availability = str(avail_el.get("href", "")).rstrip("/").rsplit("/", 1)[-1] or None if avail_el else None
    if not_sold or not price:
        price, availability = None, "not sold" if not_sold or price == 0 else availability
    note_p = box.find("p") if box is not None else None
    unit = row.find("input", attrs={"name": "unite_de_vente"})
    notes = []
    # a <button> on BMW / MINI, an <a> on Honda
    for info in row.find_all(attrs={"data-bs-toggle": "popover", "data-bs-content": True}):
        cleaned = _popover_text(str(info["data-bs-content"]))
        if cleaned:
            notes.append(cleaned)
    return RowData(
        callout=text(row.select_one("span.ref-libelle")),
        name_shown=name_lines[0] if name_lines else None,
        remarks=name_lines[1:],
        mpn=text(row.find("span", attrs={"itemprop": "mpn"})),
        current=current,
        current_display=text(link),
        struck=text(row.select_one("span.JS_ref_link.text-decoration-line-through")),
        price=price,
        currency=str(currency_el.get("content")) if currency_el is not None and price is not None else None,
        availability=availability,
        price_note=text(note_p) if price is not None else None,
        notes=notes,
        sales_unit=str(unit.get("value")) if unit is not None and unit.get("value") else None,
    )


def parse_diagram_page(soup: BeautifulSoup) -> DiagramPage:
    img = soup.find("img", id="img_microfiche_img")
    seen: set[tuple[str, str, str, str]] = set()
    hotspots: list[tuple[str, float | None, float | None]] = []
    for div in soup.select("div.hotspot[data-hotspot_id]"):
        key = tuple(str(div.get(k, "")).strip()
                    for k in ("data-hotspot_id", "data-index", "data-originalx", "data-originaly"))
        if key in seen:  # the page repeats each hotspot once per part row
            continue
        seen.add(key)  # type: ignore[arg-type]
        callout = key[1] or _clean(div.get_text())
        hotspots.append((callout, _float(key[2]), _float(key[3])))
    rows = [_parse_row(r) for r in soup.find_all("div", attrs={"itemtype": re.compile(r"schema\.org/Product$")})]
    return DiagramPage(
        title=_clean(soup.title.get_text()) if soup.title else None,
        image_src=str(img.get("src")) if img is not None and img.get("src") else None,
        image_width=_int(img.get("data-width")) if img is not None else None,
        image_height=_int(img.get("data-height")) if img is not None else None,
        hotspots=hotspots,
        rows=rows,
    )


@dataclass
class PartPage:
    part_number: str | None
    name: str | None
    price: float | None
    currency: str | None
    availability: str | None
    price_note: str | None


def parse_part_page(soup: BeautifulSoup) -> PartPage:
    ref = soup.find(id="part_ref")
    name = soup.find("div", attrs={"itemprop": "name"})
    price_el = soup.find("span", attrs={"itemprop": "price"})
    currency_el = soup.find(attrs={"itemprop": "priceCurrency"})
    avail_el = soup.find("link", attrs={"itemprop": "availability"})
    price = _float(price_el.get("content")) if price_el is not None else None
    availability = str(avail_el.get("href", "")).rstrip("/").rsplit("/", 1)[-1] if avail_el is not None else None
    if not price:
        price = None
        availability = "not sold" if price_el is not None else availability
    note = None
    if price is not None:
        shown = soup.find(string=re.compile(r"\bexcl\.?\s*(tax|vat)\b", re.I))
        note = _clean(str(shown)) if shown is not None else None
    return PartPage(
        part_number=_clean(ref.get_text()) if ref is not None else None,
        name=_clean(name.get_text(" ")) or None if name is not None else None,
        price=price,
        currency=str(currency_el.get("content")) if currency_el is not None and price is not None else None,
        availability=availability or None,
        price_note=note,
    )


# --- matching what people type ------------------------------------------------------


def match_series(
    series: list[tuple[str, str]], model: str, brand: str
) -> tuple[tuple[str, str] | None, list[str]]:
    """Pick the series named at the start of `model`; return it and the words left over.

    'X1 F48 18i' -> X1, ['f48', '18i']; 'Cooper S' -> COOPER-S (longest wins);
    'HR-V 1.5' -> HRV, ['15'].
    """
    words = _words(model)
    if words and words[0] == _norm(brand):
        words = words[1:]
    by_norm: dict[str, tuple[str, str]] = {}
    for label, path in series:
        by_norm.setdefault(_norm(label), (label, path))
        by_norm.setdefault(_norm(path.rstrip("/").rsplit("/", 1)[-1]), (label, path))
    for k in range(min(4, len(words)), 0, -1):
        hit = by_norm.get("".join(words[:k]))
        if hit:
            return hit, words[k:]
    return None, words


def match_trims(trims: list[tuple[str, str]], words: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """Honda trims named by `words`, and the words not used for it.

    First the trims whose every word was typed ('1.8 EXi automatic' -> '1.8 EXI';
    the most specific wins). Failing that, the trims containing every typed word
    that occurs in some trim name ('Type R' -> '2.0 TYPE R', '2.0 TYPE R PLUS', ...).
    """
    have = set(words)
    best: list[tuple[str, str]] = []
    best_size = 0
    for label, path in trims:
        tw = set(_words(label))
        if tw and tw <= have:
            if len(tw) > best_size:
                best, best_size = [(label, path)], len(tw)
            elif len(tw) == best_size:
                best.append((label, path))
    if best:
        used = {w for label, _ in best for w in _words(label)}
        return best, [w for w in words if w not in used]
    asked = have & {w for label, _ in trims for w in _words(label)}
    if not asked:
        return [], words
    return [t for t in trims if asked <= set(_words(t[0]))], [w for w in words if w not in asked]


def _card_words(card: TypeCard) -> set[str]:
    text = " ".join([card.type_id, card.header or "", card.heading or "", *card.labels.values(),
                     *card.lines, *card.markets])
    words = set(_words(text))
    steering = _steering(card)
    if steering:
        words.add(steering.lower())
    return words


def filter_cards(cards: list[TypeCard], words: list[str]) -> list[TypeCard]:
    """Cards matching the most of the leftover words; all cards if none match any."""
    if not words or not cards:
        return cards
    scored = [(sum(1 for w in words if w in _card_words(c)), c) for c in cards]
    top = max(s for s, _ in scored)
    return [c for s, c in scored if s == top] if top > 0 else cards


def _steering(card: TypeCard) -> str | None:
    side = card.labels.get("Side of steering wheel", "").lower()
    lines = " ".join(card.lines).upper()
    if side.startswith("right") or "RIGHT-HAND DRIVE" in lines:
        return "RHD"
    if side.startswith("left") or "LEFT-HAND DRIVE" in lines:
        return "LHD"
    return None


def check_vin(vin: str, vehicles: list[Vehicle], *, type_key_rule: bool) -> list[Vehicle]:
    """The site matches a VIN on its last 7 characters only, so check what came back.

    BMW group VINs carry the type key at positions 4-7 ('WBAHS110...' -> 'HS11'),
    and a type id ends with it ('57545HS11'). Keep the results whose key matches;
    if none (or the rule does not apply), return them all marked 'unverified'.
    """
    vin = vin.upper()
    for v in vehicles:
        v.vin = vin
    if type_key_rule and len(vin) == 17:
        key = vin[3:7]
        hits = [v for v in vehicles if v.id.upper().endswith(key)]
        if hits:
            for v in hits:
                v.attributes["vin_match"] = "type_key"
            return hits
    for v in vehicles:
        v.attributes["vin_match"] = "unverified"
    return vehicles


# --- the source -------------------------------------------------------------------------


class MicrofichesSource(Source):
    name = NAME
    description = (
        "Microfiches.net dealer catalogues: BMW (bmw-spare-parts.com), MINI (mini-spare-parts.com), "
        "Honda (parts-honda.uk). Diagrams with hotspots, OEM numbers, EUR prices excl. VAT."
    )

    def __init__(self, fetcher: PoliteFetcher, sites: dict[str, Site] | None = None) -> None:
        self.fetcher = fetcher
        self.sites = dict(sites or SITES)
        self._prices: dict[tuple[str, str], PriceQuote] = {}  # seen on diagram pages
        self._lock = threading.Lock()

    # plumbing ---------------------------------------------------------------------

    def _site(self, brand: str | None) -> Site | None:
        return self.sites.get(normalize_brand(brand)) if brand else None

    def _vehicle_site(self, vehicle: Vehicle) -> Site:
        site = self._site(vehicle.brand)
        if site is None:
            raise NotSupported(f"{vehicle.brand} is not a Microfiches brand", source=self.name)
        return site

    def _get(self, site: Site, path: str, *, ttl: float = TTL_CATALOGUE) -> tuple[FetchResult, BeautifulSoup]:
        url = site.url(path)
        res = self.fetcher.get(url, ttl=ttl, accept_status=(404,))
        if res.status == 404:
            raise NotFound(f"page not found: {url}", source=self.name, host=site.host, url=url)
        landed = urlsplit(res.final_url).path.rstrip("/")
        if landed in ("", f"/{site.prefix}") and path.rstrip("/") != landed:
            raise NotFound(f"{url} redirected to {res.final_url}", source=self.name, host=site.host, url=url)
        return res, _soup(res.text)

    def brands(self) -> list[str]:
        return list(self.sites)

    # vehicles -------------------------------------------------------------------

    def find_vehicles(self, brand: str, model: str | None = None, year: int | None = None) -> list[Vehicle]:
        site = self._site(brand)
        if site is None:
            return []
        if not model or not model.strip():
            raise NotSupported(f"{site.host}: give a model (e.g. a series name) as well as the brand",
                               source=self.name)
        res, soup = self._get(site, f"/{site.prefix}")
        series = parse_index(soup, site.prefix)
        if not series:
            raise ParseError(f"no model list on {res.url}", source=self.name, url=res.url)
        pick, words = match_series(series, model, site.brand)
        if pick is None:
            return []
        series_label, series_path = pick
        series_seg = series_path.rstrip("/").rsplit("/", 1)[-1]
        res, soup = self._get(site, series_path)
        years = parse_years(soup, site.prefix, series_seg)
        if year is None:
            listed = f"{years[0][0]}-{years[-1][0]}" if years else "none"
            raise NotSupported(f"{site.brand} {series_label}: give a year (listed: {listed})", source=self.name)
        year_path = dict(years).get(int(year))
        if year_path is None:
            return []
        page, soup = self._get(site, year_path)
        found: list[tuple[TypeCard, FetchResult, str | None]] = [(c, page, None) for c in parse_type_cards(soup)]
        if not found:
            trims = parse_trims(soup, site.prefix, series_seg, int(year))
            if not trims:
                raise ParseError(f"no types or trims on {page.url}", source=self.name, url=page.url)
            chosen, words = match_trims(trims, words)
            candidates = chosen or trims
            if len(candidates) > MAX_TRIM_PAGES:  # each trim costs a request: ask instead
                names = ", ".join(label for label, _ in candidates)
                raise ChooseTrim(
                    f"{site.brand} {series_label} {year} has {len(candidates)} matching trims; add one to the "
                    f"model text (e.g. '{series_label} {candidates[0][0]}'). Trims: {names}",
                    source=self.name,
                    brand=site.brand,
                    series=series_label,
                    year=int(year),
                    trims=[label for label, _ in candidates],
                    cached=[label for label, path in candidates if self.fetcher.cached(site.url(path)) is not None],
                )
            for label, path in candidates:
                trim_page, trim_soup = self._get(site, path)
                found += [(c, trim_page, label) for c in parse_type_cards(trim_soup)]
        keep = {id(c) for c in filter_cards([c for c, _, _ in found], words)}
        vehicles: dict[str, Vehicle] = {}
        for card, card_page, trim in found:
            if id(card) in keep and card.type_id not in vehicles:
                vehicles[card.type_id] = self._vehicle(site, card, card_page, series_label, trim, int(year))
        return list(vehicles.values())

    def _vehicle(self, site: Site, card: TypeCard, page: FetchResult, series_label: str | None,
                 trim: str | None, year: int | None) -> Vehicle:
        labels = dict(card.labels)
        attributes: dict[str, str] = {}
        steering = _steering(card)
        if steering:
            attributes["steering"] = steering
        if year is not None:
            attributes["listed_year"] = str(year)
        model = series_label
        if card.header:
            model = re.sub(rf"^{re.escape(site.brand)}\s+", "", card.header, flags=re.I) or model
        gearbox = labels.pop("Gearbox", None)
        if gearbox:
            attributes["gearbox"] = gearbox
        transmission = gearbox if gearbox and gearbox.upper() not in ("N", "-") else None
        body = labels.pop("Body type", None)
        if card.heading:  # Honda: "4 Doors 5 speed automatic"
            m = re.match(r"^(\d+\s*doors?)\s+(.+)$", card.heading, flags=re.I)
            if m:
                body = body or m.group(1)
                transmission = transmission or m.group(2)
            attributes["variant"] = card.heading
        options_id = labels.pop("Options", None)
        if options_id:
            attributes["options_id"] = options_id
        if site.vin_type_key and re.fullmatch(r"\d+[0-9A-Z]{4}", card.type_id):
            attributes["type_key"] = card.type_id[-4:]
        name = labels.pop("Model", None)
        labels.pop("Year", None)
        chassis = labels.pop("Finish line", None)
        market = labels.pop("Country", None)
        engine = labels.pop("Engine type", None) or labels.pop("Engine", None)
        labels.pop("Side of steering wheel", None)
        for label, value in labels.items():
            if value:
                attributes[_snake(label)] = value
        options = [line for line in card.lines if line.upper() not in ("RIGHT-HAND DRIVE", "LEFT-HAND DRIVE")]
        if options:
            attributes["options"] = ", ".join(options)
        if card.markets and not market:
            market = ", ".join(card.markets)
        if name is None:
            name = " ".join(p for p in (trim, card.heading) if p) or None
        return Vehicle(
            source=self.name,
            url=page.url,
            fetched_at=page.fetched_at,
            source_ref={"path": card.path},
            id=card.type_id,
            brand=site.brand,
            model=model,
            name=name,
            market=market,
            chassis=chassis,
            body=body,
            engine=engine,
            transmission=transmission,
            trim=trim,
            attributes=attributes,
        )

    def _site_for_vin(self, vin: str) -> Site | None:
        for site in self.sites.values():
            if vin[:3] in site.wmis:
                return site
        return None

    def resolve_vin(self, vin: str) -> list[Vehicle]:
        vin = "".join(vin.split()).upper()
        site = self._site_for_vin(vin)
        if site is None:
            return []
        path = f"/{site.prefix}/search_vehicle/chassis/{vin}"
        res = self.fetcher.get(site.url(path), accept_status=(404,))
        cards = parse_type_cards(_soup(res.text)) if res.ok else []
        if not cards:
            res, cards = self._vin_by_form(site, vin, res)
        by_id: dict[str, Vehicle] = {}
        years: dict[str, list[int]] = {}
        for card in cards:
            segs = _segments(card.path) or []
            year = _int(card.labels.get("Year")) or (_int(segs[2]) if len(segs) > 2 else None)
            if year is not None:
                years.setdefault(card.type_id, []).append(year)
            if card.type_id not in by_id:
                by_id[card.type_id] = self._vehicle(site, card, res, None, None, None)
        for type_id, vehicle in by_id.items():
            listed = sorted(set(years.get(type_id, [])))
            if listed:
                vehicle.year_from, vehicle.year_to = listed[0], listed[-1]
                vehicle.attributes["listed_years"] = ", ".join(str(y) for y in listed)
        return check_vin(vin, list(by_id.values()), type_key_rule=site.vin_type_key)

    def _vin_by_form(self, site: Site, vin: str, first: FetchResult) -> tuple[FetchResult, list[TypeCard]]:
        """POST the search form when the plain GET shows no results."""
        form_url = site.url(f"/{site.prefix}/search_vehicle/chassis")
        form = self.fetcher.get(form_url, ttl=0)  # live: the token must match the fresh csrf cookie
        token = _soup(form.text).find("input", attrs={"name": "csrf_test_name"})
        if token is None or not token.get("value"):
            return first, []
        res = self.fetcher.post(
            form_url,
            {"csrf_test_name": str(token["value"]), "recherche_intuitive_chassis": vin,
             "pays_vehicle": "ALL COUNTRIES"},
            referer=form_url,
        )
        return res, parse_type_cards(_soup(res.text)) if res.ok else []

    # catalogue tree ------------------------------------------------------------------

    def list_groups(self, vehicle: Vehicle) -> list[Group]:
        site = self._vehicle_site(vehicle)
        path = vehicle.source_ref.get("path")
        if not path:
            raise NotFound(f"vehicle {vehicle.id} has no catalogue path; find it again", source=self.name)
        res, soup = self._get(site, path)
        found = parse_group_links(soup, vehicle.id)
        if found:
            return [
                Group(source=self.name, url=res.url, fetched_at=res.fetched_at, source_ref={"path": gpath},
                      id=gid, vehicle_id=vehicle.id, name=name)
                for gid, name, gpath in found
            ]
        # Honda: no group pages; every diagram sits on the type page under a group heading.
        groups: dict[str, Group] = {}
        for link in parse_diagram_links(soup, vehicle.id):
            if link.group_id not in groups:
                segs = _segments(link.path) or []
                name = link.group_heading or (segs[-6].replace("-", " ") if len(segs) >= 6 else link.group_id)
                groups[link.group_id] = Group(
                    source=self.name, url=res.url, fetched_at=res.fetched_at, source_ref={"path": path},
                    id=link.group_id, vehicle_id=vehicle.id, name=name,
                )
        if not groups:
            raise ParseError(f"no groups on {res.url}", source=self.name, url=res.url)
        return list(groups.values())

    def list_diagrams(self, vehicle: Vehicle, group: Group) -> list[Diagram]:
        site = self._vehicle_site(vehicle)
        path = group.source_ref.get("path") or vehicle.source_ref.get("path")
        if not path:
            raise NotFound(f"group {group.id} has no page path; list groups again", source=self.name)
        res, soup = self._get(site, path)
        links = parse_diagram_links(soup, vehicle.id)
        if not links:
            raise ParseError(f"no diagrams on {res.url}", source=self.name, url=res.url)
        out = []
        for link in links:
            if link.group_id != group.id:
                continue
            name, notes = link.name, link.notes
            if site.brand == "HONDA":
                name = unwrap_honda_name(link.raw_name, link.name)
                if name != link.name:
                    notes = "; ".join(filter(None, [notes, f"name on site: {link.name}"]))
            out.append(Diagram(
                source=self.name,
                url=res.url,
                fetched_at=res.fetched_at,
                source_ref={"path": link.path},
                id=link.code,
                vehicle_id=vehicle.id,
                group_id=group.id,
                name=name,
                code=link.code,
                group_path=[group.name],
                notes=notes,
            ))
        return out

    def get_diagram(self, vehicle: Vehicle, diagram: Diagram) -> tuple[Diagram, list[PartRow]]:
        site = self._vehicle_site(vehicle)
        path = diagram.source_ref.get("path")
        if not path:
            raise NotFound(f"diagram {diagram.id} has no page path; list diagrams again", source=self.name)
        res, soup = self._get(site, path)
        page = parse_diagram_page(soup)
        if page.image_src is None and not page.rows:
            raise ParseError(f"no diagram image or part rows on {res.url}", source=self.name, url=res.url)
        name = diagram.name
        if not name and page.title:
            name = re.split(r"\s+for\s+", page.title, maxsplit=1)[0]
        full = Diagram(
            source=self.name,
            url=res.url,
            fetched_at=res.fetched_at,
            source_ref=dict(diagram.source_ref),
            id=diagram.id,
            vehicle_id=vehicle.id,
            group_id=diagram.group_id,
            name=name or diagram.id,
            name_original=diagram.name_original,
            code=diagram.code or diagram.id,
            group_path=list(diagram.group_path),
            image_url=(site.url(page.image_src) if page.image_src and page.image_src.startswith("/")
                       else page.image_src),
            image_width=page.image_width,
            image_height=page.image_height,
            notes=diagram.notes,
            hotspots=[
                Hotspot(source=self.name, url=res.url, fetched_at=res.fetched_at, callout=callout, x=x, y=y)
                for callout, x, y in page.hotspots
            ],
        )
        rows = [self._part_row(site, vehicle, full, res, i, r) for i, r in enumerate(page.rows)]
        return full, rows

    def _part_row(self, site: Site, vehicle: Vehicle, diagram: Diagram, res: FetchResult, index: int,
                  r: RowData) -> PartRow:
        current = r.current or r.mpn
        superseded_from = None
        if r.struck and r.mpn and current and normalize_part_number(r.mpn) != normalize_part_number(current):
            superseded_from = r.mpn
        attributes: dict[str, str] = {}
        name_original = r.name_shown
        if name_original and " " not in name_original and sum(c.isdigit() for c in name_original) >= 4:
            attributes["name_shown"] = name_original  # the site shows a part number instead of a name
            name_original = None
        if site.names_language == "en":
            english = name_original.split(" --- ")[0].strip() if name_original else None
        else:
            english = glossary.translate(name_original)
        side = glossary.side_of(english if site.names_language == "en" else name_original)
        if r.current_display:
            attributes["part_number_display"] = r.current_display
        if r.price is not None:
            attributes["price"] = f"{r.price:.2f}"
        if r.currency:
            attributes["currency"] = r.currency
        if r.price_note:
            attributes["price_note"] = r.price_note
        if r.availability:
            attributes["availability"] = r.availability
        if r.sales_unit:
            attributes["sales_unit"] = r.sales_unit
        notes = [*r.remarks, *r.notes]
        if r.price is not None and current and not superseded_from:
            self._remember_price(site, current, english or name_original, r, res)
        return PartRow(
            source=self.name,
            url=res.url,
            fetched_at=res.fetched_at,
            vehicle_id=vehicle.id,
            diagram_id=diagram.id,
            row_index=index,
            callout=r.callout,
            part_number=current,
            superseded_from=superseded_from,
            name=english,
            name_original=name_original,
            qty=None,
            notes=" | ".join(notes) or None,
            side=side,
            attributes=attributes,
        )

    # prices ----------------------------------------------------------------------------

    def _remember_price(self, site: Site, number: str, name: str | None, r: RowData, res: FetchResult) -> None:
        quote_ = PriceQuote(
            source=self.name, url=res.url, fetched_at=res.fetched_at, part_number=number, brand=site.brand,
            name=name, amount=r.price, currency=r.currency, tax_note=site.tax_note,
            availability=r.availability, notes="price from the diagram page",
        )
        with self._lock:
            self._prices[(site.brand, normalize_part_number(number))] = quote_

    def _price_site(self, brand: str, number: str) -> Site | None:
        site = self._site(brand)
        if site is None and not brand and re.fullmatch(r"\d{11}", number):
            site = self.sites.get("BMW")  # an 11-digit number with no brand: BMW group numbering
        return site

    def get_price(self, part_number: str, brand: str) -> PriceQuote | None:
        number = normalize_part_number(part_number)
        site = self._price_site(normalize_brand(brand) if brand else "", number)
        if site is None or not number:
            return None
        with self._lock:
            seen = self._prices.get((site.brand, number))
        if seen is not None and (utcnow() - seen.fetched_at).total_seconds() <= TTL_PRICE:
            return seen
        try:
            res, soup = self._get(site, f"/{site.prefix}/assignment_spare_parts/{number}", ttl=TTL_PRICE)
        except NotFound:
            return None
        page = parse_part_page(soup)
        if page.part_number is None and page.price is None:
            return None
        shown = page.part_number or number
        name = page.name
        notes = []
        if site.names_language == "it" and page.name:
            english = glossary.translate(page.name)
            if english:
                name = english
                notes.append(f"name on site: {page.name}")
        if normalize_part_number(shown) != number:
            notes.append(f"site shows part {shown}")
        if page.price_note:
            notes.append(page.price_note)
        return PriceQuote(
            source=self.name,
            url=res.url,
            fetched_at=res.fetched_at,
            part_number=shown,
            brand=site.brand,
            name=name,
            amount=page.price,
            currency=page.currency,
            tax_note=site.tax_note,
            availability=page.availability,
            notes="; ".join(notes) or None,
        )

    def close(self) -> None:
        self.fetcher.close()


def create_source(ctx: SourceContext) -> MicrofichesSource:
    return MicrofichesSource(ctx.fetcher(NAME))
