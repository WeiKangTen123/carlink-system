"""Linked parts (contract 10, plans/2026-11-17-linked-parts/contract10.md
section 3): for each part on the damage list, the parts tied to it -- the
beam behind a bumper cover, the hinges a boot lid opens with, the
regulator inside a door -- as tick-to-add suggestions (SecondLookItem,
source "link"). Never on the damage list until a person adds one, never
pre-ticked; grouped on the Review step by the listed part they hang off,
in three buckets: seen, likely, confirm at strip-down.

Pure: no database, no Gemini, no web request. The links come from a table,
intake/link_table.json (format in contract10.md section 4.3), built from
generic car knowledge only, never from a case. How hard a listed part was
hit (hit_of) gates which of its links show; a linked part takes the listed
part's side only when that side is firm, else its "(side undetermined)"
name. settings.intake_links ("off" by default) switches it all.

Step 0 of the build (S0): the names, the table's loading and the options.
The links engine agent (R) writes hit_of, build and for_parent; until then
they find nothing.
"""
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

# HIT_LEVELS and LINK_TYPES (config) are the links' names here too.
from app.config import HIT_LEVELS, LINK_TYPES, link_types, settings  # noqa: F401

logger = logging.getLogger(__name__)

TABLE_PATH = Path(__file__).resolve().parent / "link_table.json"
# A link's "side" in the table: "same" -- the listed part's side; "impact"
# -- an unsided listed part, a sided linked part: the side the car was hit
# on; "none" -- a part with no side.
LINK_SIDES = ("same", "impact", "none")
# The area a link belongs to: its skill file (ai/skills/<area>.md; left and
# right share side.md) and the linked check's end of the car.
AREAS = ("front", "rear", "left", "right", "underbody", "wheel")
# The Review step's buckets, in the order shown.
BUCKETS = ("seen", "likely", "strip_down")


@dataclass(frozen=True)
class Options:
    """What build works to: settings.intake_links_* (see options), passed
    in so that build stays pure and the benchmark's replay can set its
    own."""
    mode: str = "off"
    max_items: int = 12
    types: tuple[str, ...] = LINK_TYPES
    side: str = "firm"
    unknown_hit: str = "medium"
    intact: str = "demote"
    catalogue: bool = False


def options(source=None) -> Options:
    """Options from the settings, or from anything with the same names."""
    s = source if source is not None else settings
    return Options(mode=s.intake_links, max_items=s.intake_links_max, types=tuple(link_types(s.intake_links_types)),
                   side=s.intake_links_side, unknown_hit=s.intake_links_unknown_hit, intact=s.intake_links_intact,
                   catalogue=bool(s.intake_links_catalogue))


def load_table(path: Path | str | None = None) -> dict | None:
    """The link table at `path` (TABLE_PATH by default), or None -- and a
    log line -- when the file is missing or isn't a table: the linked parts
    are then off whatever settings.intake_links says. Step 0 checks the
    outline only, a version and a list of links; R adds the full check
    (contract10.md section 4.3)."""
    path = Path(path) if path else TABLE_PATH
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.info("Linked parts: no link table at %s; they stay off", path)
        return None
    except (OSError, ValueError) as exc:
        logger.warning("Linked parts: the link table at %s can't be read (%s); they stay off", path, exc)
        return None
    version = table.get("version") if isinstance(table, dict) else None
    if not (isinstance(version, str) and version.strip() and isinstance(table.get("links"), list)):
        logger.warning("Linked parts: %s is not a link table (a version and a list of links); they stay off", path)
        return None
    return table


def table_sha(path: Path | str | None = None) -> str | None:
    """sha256 of the table file as it is on disk -- recorded with every
    benchmark run beside its version -- or None when there is no file."""
    try:
        return hashlib.sha256((Path(path) if path else TABLE_PATH).read_bytes()).hexdigest()
    except OSError:
        return None


# The table as the app started with it (None: the linked parts are off),
# and its version ("L1"), which goes in each link's link_rule and in a
# benchmark run's ai_settings.
TABLE = load_table()
TABLE_VERSION = TABLE["version"] if TABLE else None


def hit_of(item: dict) -> str | None:
    """How hard a listed part was hit, one of HIT_LEVELS, from its own
    words (condition tokens, severity, remarks); None when they say
    nothing, and the caller takes Options.unknown_hit. Step 0: always
    None."""
    return None


def build(listed: list[dict], zone_suggestions: list[dict], photos: list, zone_intact: dict, table: dict | None,
          opts: Options) -> list[dict]:
    """The linked-part suggestions for one reading, as SecondLookItem dicts
    (source "link"), at most opts.max_items: for each listed part, the
    table's links whose min_hit its hit level reaches, without the parts
    already listed, suggested by a zone read or dismissed. Step 0: none."""
    return []


def for_parent(item: dict, data: dict, photos: list, table: dict | None, opts: Options) -> list[dict]:
    """The linked parts of one listed part, as build gives them: the
    re-link after a person adds or changes a part. Step 0: none."""
    return []
