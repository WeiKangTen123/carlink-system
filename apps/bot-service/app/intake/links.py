"""Linked parts (contract 10, plans/2026-11-17-linked-parts/contract10.md
section 3): for each part on the damage list, the parts tied to it -- the
beam behind a bumper cover, the hinges a boot lid opens with, the
regulator inside a door -- as tick-to-add suggestions (SecondLookItem,
source "link"). Never on the damage list until a person adds one, never
pre-ticked; grouped on the Review step by the listed part they hang off,
in three buckets: seen, likely, confirm at strip-down.

Pure: no database, no Gemini, no web request -- the only I/O is reading
the table. The links come from intake/link_table.json (format in
contract10.md section 4.3), built from generic car knowledge only, never
from a case, and checked when it loads (validate_table: a bad table is no
table, so the linked parts stay off).

How a listed part (the parent) becomes linked suggestions (build):
  1. hit_of: how hard it was hit, from its own words -- condition tokens,
     condition, damage type, severity, remarks (the HIT_OF_* maps); none of
     those -> Options.unknown_hit. An end of the car with a heavy part and
     a sign the hit went deep (_end_crush) lifts its heavy parts to crush.
  2. gate: a link shows when that level reaches its min_hit, or one of its
     trigger_tokens is in the parent's words; and its type is allowed.
  3. side: "same" takes the parent's side only when that side is firm
     (_firm); "impact" a clear majority of the firm sided parts at the
     parent's end; otherwise -- and always under a "(side undetermined)"
     parent -- the child's own "(side undetermined)" name. Never from
     vehicle_info.point_of_impact: that is the AI's own draft, not a firm
     side. Never two children for one link.
  4. skip what is listed (or covered: its undetermined twin), dismissed,
     or already a suggestion (a zone read's or the second look's: joined
     to that one instead, build_all's joins); two parents naming the same
     child give one item, linked_from both.
  5. bucket: "seen" when a sorter photo showing damage is tagged with the
     child; else "strip_down" for a hidden child behind the parent or on
     the crush path, or one the table says needs a strip-down; else
     "likely" (attached, opens with it, or visible).
  6. intact: a zone read that listed the child intact demotes it (last,
     intact_in names that read) -- the zone "intact" lists are often wrong
     -- or drops it, or is ignored (Options.intact).
  7. order: demoted last; seen > likely > strip_down; attached >
     opens_with > behind > crush; the harder-hit parent first; then list
     and table order. At most MAX_PER_PARENT per parent in each bucket,
     then Options.max_items in all (outside second_look.MAX_SUGGESTIONS).
     The car map's review screen (K4, Options.order / assembly_max /
     stripdown_cap / stripdown_max, defaults = the above): a relation's
     prior rank after the bucket; the per-parent cap per assembly when the
     caller passes the map's facts (info); the strip-down bucket as its
     own folded block outside max_items.
Each item's reason is the table's own words (never the AI's), its evidence
the parent's best photo by "link" with no box (and the sorter's photos by
"sort" for a seen one), its link_rule "<table version>:<link id>".

settings.intake_links ("off" by default) switches it all; the callers
(ai/second_look._store, reports/suggestions.decide) read it, not this
module, so the benchmark's replay can run build with any Options.

The assembly walk (K1-design.md 5a; settings.intake_assembly_walk, off by
default): with `walk` (an assembly_walk.Walk -- the zone reads' yes/no
signs and the map they came from), a yes sign lifts its panel's hit level
(step 1, before the end bonus; assembly_walk.raise_levels), a relation's
position on the parent gates it against where the walk saw the hit (step
2, only ever removing), a yes sign pointing at a strip-down relation makes
it likely and is kept in signs_yes (step 5), and with intake_link_hops 2 a
chain the walk unlocked adds its second hop (_hop2: at most
assembly_walk.MAX_HOP2 a case, hop 2, via the middle part, its link_rule
"<version>:<chain id>"). Without a walk -- the default -- none of it runs.
"""
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.ai import evidence
# HIT_LEVELS and LINK_TYPES (config) are the links' names here too.
from app.config import HIT_LEVELS, LINK_TYPES, link_types, settings  # noqa: F401
from app.intake import assembly_walk
from app.parts.prefill import item_key
from app.reports.taxonomy import (
    CANONICAL_DAMAGE_TYPES,
    CANONICAL_PARTS,
    CANONICAL_SEVERITIES,
    FIRM_CONDITION_TOKENS,
    FIRM_REMARKS,
    FIRM_WHOLE_CONDITIONS,
    KNOWN_PARTS,
    OTHER_PART,
    UNDETERMINED_SUFFIX,
    condition_tokens_of,
    known_part,
    split_side,
    unsided_part,
)

logger = logging.getLogger(__name__)

TABLE_PATH = Path(__file__).resolve().parent / "link_table.json"
# A link's "side" in the table: "same" -- the listed part's side; "impact"
# -- an unsided listed part, a sided linked part: the side the car was hit
# on, as the firm sided parts listed at that end say it; "none" -- a part
# with no side.
LINK_SIDES = ("same", "impact", "none")
# The area a link belongs to: its skill file (ai/skills/<area>.md; left and
# right share side.md) and the linked check's end of the car.
AREAS = ("front", "rear", "left", "right", "underbody", "wheel")
# What the table may write: AREAS, and "side" -- the doors' and sills'
# area in L1, which has one skill file for both sides (side.md).
TABLE_AREAS = AREAS + ("side",)
# The Review step's buckets, in the order shown.
BUCKETS = ("seen", "likely", "strip_down")
# A listed part's linked parts per bucket: a heavily hit bumper has four
# attached parts ahead of the beam behind it, so one cap over all of them
# would never show the beam.
MAX_PER_PARENT = 4
# Options.order "prior": a relation with no prior rank (1 listed most
# often .. 5) sorts as this middle one (K1-design.md 5b).
PRIOR_NONE = 3
# Catalogue diagram names on one linked part (SecondLookItem.oem).
MAX_OEM = 3
# Sorter photos a seen linked part names (its seen_in).
MAX_SEEN = 4
MAX_REASON = 160
SIDE = "{side}"
# A link from the parts catalogue's diagrams (Options.catalogue): no table
# row says more, so the plainest kind -- fixed to the part, visible, at a
# real hit.
CATALOGUE_LINK = {"type": "attached", "min_hit": "medium", "hidden": False, "strip_down": False}

_RANK = {level: n for n, level in enumerate(HIT_LEVELS)}
_TYPE_RANK = {kind: n for n, kind in enumerate(LINK_TYPES)}
_BUCKET_RANK = {bucket: n for n, bucket in enumerate(BUCKETS)}
# A second hop's place in its bucket: after every first hop, past any type
# rank (Options.order "table") and any prior rank 1..5 ("prior").
_HOP2_RANK = len(_TYPE_RANK) + 5 + 1
_FIELDS = ("id", "parent", "child", "type", "side", "min_hit", "hidden", "strip_down", "area", "reason")
_DIGITS3 = re.compile(r"\d{3,}")

# ------------------------------------------------------------ hit levels
#
# How hard a listed part was hit, from the read's own words. Every value
# the read can give is here (scripts/test_links.py checks the taxonomy's
# lists against these maps); None: it says nothing about how hard.
# Surface marks are light; dented, split, out of place or not working is
# medium; bent out of shape, jammed or leaking is heavy; crumpled or
# structural is the crush path.
HIT_OF_TOKEN = {
    "Grazed": "light", "Chipped": "light", "Rattle": "light",
    "Dented": "medium", "Cracked": "medium", "Cut": "medium", "Torn": "medium", "Shifted": "medium",
    "Slack": "medium", "Faulty": "medium",
    "Bent": "heavy", "Buckled": "heavy", "Deformed": "heavy", "Distorted": "heavy", "Warped": "heavy",
    "Jammed": "heavy", "Leaked": "heavy",
    "Crumpled": "crush",
}
HIT_OF_DAMAGE_TYPE = {
    "Scratch": "light", "Scuff / Graze": "light", "Paint Damage": "light",
    "Dent": "medium", "Crack": "medium", "Shatter / Broken Glass": "medium", "Broken / Detached": "medium",
    "Puncture / Tear": "medium", "Misalignment": "medium", "Missing": "medium", "Electrical Fault": "medium",
    "Deformation": "heavy",
    "Structural Damage": "crush",
    # Not a collision's force.
    "Water Damage": None, "Fire / Heat Damage": None,
}
HIT_OF_SEVERITY = {"Minor": "light", "Moderate": "medium", "Severe": "heavy"}
# Replace: the part is past repair -- at least a medium hit.
HIT_OF_REMARKS = {"Replace": "medium", "Repair": "light"}
# A condition written whole (a person's): Necessary / Serviceable say
# nothing about a hit; Repair as the remark does.
HIT_OF_WHOLE_CONDITION = {"Necessary": None, "Serviceable": None, "Repair": "light"}

# ------------------------------------------------------------ the car's ends
#
# Parts at the front or rear end (without their side), for the end bonus
# and an impact link's side. Doors, sills, mirrors, the roof and the cabin
# are at neither end.
FRONT_END = frozenset({
    "Front Bumper", "Front Grille", "Bonnet", "Headlamp", "Front Windscreen", "Front Fender", "Front Bumper Beam",
    "Fog Lamp", "Front Number Plate", "Front Wheel Arch Liner", "Front Wheel Arch Trim", "Front Wheel",
    "Front Tyre", "Engine Undercover", "Radiator", "Air Conditioning Condenser", "Front Subframe",
    "Suspension - Front", "Steering Assembly",
})
REAR_END = frozenset({
    "Rear Bumper", "Boot Lid", "Rear Windscreen", "Tail Lamp", "Rear Quarter Panel", "Quarter Panel",
    "Rear Number Plate", "Rear Bumper Beam", "Rear Bumper Reflector", "Rear End Panel", "Boot Lid Lock",
    "Boot Lid Hinge", "Boot Lid Weatherstrip", "Boot Lid Inner Trim", "Boot Lid Lamp", "Boot Floor Board",
    "Boot Side Trim", "Rear Wheel Arch Liner", "Rear Wheel Arch Trim", "Rear Wheel", "Rear Tyre",
    "Rear Floor Panel", "Rear Subframe", "Suspension - Rear", "Exhaust System", "Fuel Tank", "Parking Sensor",
    "Reversing Camera",
})
# The car map's names (taxonomy.CAR_MAP_PARTS) at each end; the doors' are
# at neither. Only a case holding one of them meets these.
FRONT_END |= {"Front Bumper Absorber", "Front Bumper Bracket", "Front Parking Sensor", "Fog Lamp Cover",
              "Radiator Support", "Radiator Fan", "Bonnet Hinge", "Bonnet Latch", "Bonnet Insulator"}
REAR_END |= {"Rear Bumper Absorber", "Rear Bumper Bracket", "Rear Parking Sensor", "Boot Lid Striker", "Boot Lid Strut",
             "Rear Number Plate Lamp", "Boot Lid Garnish", "Boot Lid Spoiler", "Rear Wiper Motor",
             "High Mount Stop Lamp", "Fuel Filler Flap"}
# A structural part listed at an end: the hit went past the outer panels.
STRUCTURAL = {
    "front": frozenset({"Front Bumper Beam", "Front Subframe", "Underbody / Chassis"}),
    "rear": frozenset({"Rear Bumper Beam", "Rear End Panel", "Rear Floor Panel", "Rear Subframe",
                       "Underbody / Chassis"}),
}
# Parts listed at one end that make a heavy hit there a crush.
END_CROWD = 3


@dataclass(frozen=True)
class Options:
    """What build works to: settings.intake_links_* (see options), passed
    in so that build stays pure and the benchmark's replay can set its
    own. The last four are the car map's review screen (K4; their
    defaults are today's order and caps, byte for byte): order "table" or
    "prior" (a relation's prior rank inside a bucket, None counting as
    PRIOR_NONE); assembly_max linked parts per group per bucket -- a group
    is the assembly when the relation info names one, else the listed part
    (L1's MAX_PER_PARENT); stripdown_cap "in" (strip-down counts in
    max_items, L1) or "out" (its own folded block of at most
    stripdown_max, outside max_items)."""
    mode: str = "off"
    max_items: int = 12
    types: tuple[str, ...] = LINK_TYPES
    side: str = "firm"
    unknown_hit: str = "medium"
    intact: str = "demote"
    catalogue: bool = False
    order: str = "table"
    assembly_max: int = MAX_PER_PARENT
    stripdown_cap: str = "in"
    stripdown_max: int = 12


def options(source=None) -> Options:
    """Options from the settings, or from anything with the same names
    (the K4 four may be missing there: their defaults then)."""
    s = source if source is not None else settings
    return Options(mode=s.intake_links, max_items=s.intake_links_max, types=tuple(link_types(s.intake_links_types)),
                   side=s.intake_links_side, unknown_hit=s.intake_links_unknown_hit, intact=s.intake_links_intact,
                   catalogue=bool(s.intake_links_catalogue),
                   order=getattr(s, "intake_links_order", Options.order),
                   assembly_max=getattr(s, "intake_links_assembly_max", Options.assembly_max),
                   stripdown_cap=getattr(s, "intake_links_stripdown_cap", Options.stripdown_cap),
                   stripdown_max=getattr(s, "intake_links_stripdown_max", Options.stripdown_max))


# ------------------------------------------------------------ the table


def _expand(name: str) -> list[tuple[str, str | None]]:
    """A table name as (canonical name, the side put in): "{side} Tail
    Lamp" -> Left and Right; any other name as it is, side None."""
    if SIDE in name:
        return [(name.replace(SIDE, side), side) for side in ("Left", "Right")]
    return [(name, None)]


def validate_table(table) -> list[str]:
    """What is wrong with a link table (contract10.md section 4.3), as
    plain lines; [] when nothing is. Every parent and child, {side}
    expanded, is a canonical or car map part (taxonomy.KNOWN_PARTS; not a
    "(side undetermined)" name, not Other); ids and parent/child pairs are
    unique; the side field matches where {side} is; types, sides, levels, areas and trigger words are the
    known ones; hidden / strip_down are true or false; a reason is at most
    MAX_REASON characters with no number of three or more digits. Total:
    a value of any JSON type anywhere is a line here, never an exception
    (load_table runs at import, and second_look._store must not fail)."""
    if not isinstance(table, dict):
        return ["not a JSON object"]
    errors: list[str] = []
    if not (isinstance(table.get("version"), str) and table["version"].strip()):
        errors.append("no version")
    links = table.get("links")
    if not isinstance(links, list):
        return errors + ["no list of links"]
    canonical = set(KNOWN_PARTS)
    ids, pairs = set(), set()
    for n, link in enumerate(links):
        where = f"link {n}"
        if not isinstance(link, dict):
            errors.append(f"{where}: not an object")
            continue
        where = f"link {n} ({link.get('id')!r})"
        missing = [f for f in _FIELDS if f not in link]
        if missing:
            errors.append(f"{where}: missing {', '.join(missing)}")
            continue
        if not (isinstance(link["id"], str) and link["id"].strip()):
            # A list or an object as the id can't go in a set: no id.
            errors.append(f"{where}: no id")
        elif link["id"] in ids:
            errors.append(f"{where}: the id is used twice")
        else:
            ids.add(link["id"])
        names_ok = True
        for field in ("parent", "child"):
            value = link[field]
            if not isinstance(value, str):
                errors.append(f"{where}: {field} is not a name")
                names_ok = False
                continue
            for name, _ in _expand(value):
                if name not in canonical or name.endswith(UNDETERMINED_SUFFIX) or name == OTHER_PART:
                    errors.append(f"{where}: {field} {name!r} is not a sided or unsided canonical part")
                    names_ok = False
        if names_ok:
            pair = (link["parent"], link["child"])
            if pair in pairs:
                errors.append(f"{where}: the same parent and child twice")
            pairs.add(pair)
            if link["parent"] == link["child"]:
                errors.append(f"{where}: a part linked to itself")
            p_sided, c_sided = SIDE in link["parent"], SIDE in link["child"]
            want = "same" if p_sided and c_sided else "impact" if c_sided else "none"
            if link["side"] != want:
                errors.append(f"{where}: side {link['side']!r} where the names say {want!r}")
        if link["type"] not in LINK_TYPES:
            errors.append(f"{where}: type {link['type']!r}")
        if link["side"] not in LINK_SIDES:
            errors.append(f"{where}: side {link['side']!r}")
        if link["min_hit"] not in HIT_LEVELS:
            errors.append(f"{where}: min_hit {link['min_hit']!r}")
        if link["area"] not in TABLE_AREAS:
            errors.append(f"{where}: area {link['area']!r}")
        for flag in ("hidden", "strip_down"):
            if not isinstance(link[flag], bool):
                errors.append(f"{where}: {flag} is not true or false")
        triggers = link.get("trigger_tokens", [])
        if not isinstance(triggers, list) or any(not isinstance(t, str) or t not in FIRM_CONDITION_TOKENS
                                                 for t in triggers):
            errors.append(f"{where}: trigger_tokens are not all firm condition words")
        reason = link["reason"]
        if not (isinstance(reason, str) and reason.strip()):
            errors.append(f"{where}: no reason")
        elif len(reason) > MAX_REASON or _DIGITS3.search(reason):
            errors.append(f"{where}: the reason is over {MAX_REASON} characters or has a 3+ digit number")
        if not isinstance(link.get("sources", []), list):
            errors.append(f"{where}: sources is not a list")
    return errors


def load_table(path: Path | str | None = None) -> dict | None:
    """The link table at `path` (TABLE_PATH by default), or None -- and a
    log line -- when the file is missing, can't be read or fails
    validate_table: the linked parts are then off whatever
    settings.intake_links says. Never raises: it runs when this module is
    imported, and an exception there would fail every caller's import."""
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
    try:
        errors = validate_table(table)
    except Exception as exc:  # validate_table is total; this is in case it ever isn't
        logger.warning("Linked parts: the link table at %s couldn't be checked (%s); they stay off", path,
                       type(exc).__name__)
        return None
    if errors:
        logger.warning("Linked parts: the link table at %s has %d problem(s), the first: %s; they stay off",
                       path, len(errors), errors[0])
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


# ------------------------------------------------------------ words


def _get(obj, name: str, default=None):
    """A field of a dict or of an object (an IntakePhoto row)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _tokens(item: dict) -> list[str]:
    """The firm words on an item: its condition_tokens and its condition."""
    words = list(condition_tokens_of(item.get("condition_tokens") or []))
    condition = item.get("condition")
    if isinstance(condition, str) and condition.strip():
        words += [t for t in condition_tokens_of(condition) if t not in words]
    return words


def _known(value, names) -> str | None:
    """value as one of `names` whatever its letter case, else None."""
    text = str(value or "").strip().lower()
    return next((n for n in names if n.lower() == text), None) if text else None


def hit_of(item: dict) -> str | None:
    """How hard a listed part was hit, one of HIT_LEVELS, from its own
    words -- the hardest any of them says: condition tokens and condition
    (HIT_OF_TOKEN, HIT_OF_WHOLE_CONDITION), damage type, severity, remarks.
    None when they say nothing; the caller then takes
    Options.unknown_hit."""
    if not isinstance(item, dict):
        return None
    levels = [HIT_OF_TOKEN.get(t) for t in _tokens(item)]
    whole = _known(item.get("condition"), FIRM_WHOLE_CONDITIONS)
    levels.append(HIT_OF_WHOLE_CONDITION.get(whole) if whole else None)
    damage_type = _known(item.get("damage_type"), CANONICAL_DAMAGE_TYPES)
    levels.append(HIT_OF_DAMAGE_TYPE.get(damage_type) if damage_type else None)
    severity = _known(item.get("severity"), CANONICAL_SEVERITIES)
    levels.append(HIT_OF_SEVERITY.get(severity) if severity else None)
    remarks = _known(item.get("remarks"), FIRM_REMARKS)
    levels.append(HIT_OF_REMARKS.get(remarks) if remarks else None)
    found = [level for level in levels if level]
    return max(found, key=_RANK.__getitem__) if found else None


def end_of(part: str) -> str | None:
    """"front" | "rear" | None (a door, a sill, the cabin, the roof)."""
    base, _ = split_side(part)
    return "front" if base in FRONT_END else "rear" if base in REAR_END else None


def covers(part: str) -> list[str]:
    """The names that mean `part` (second_look.covers_of, kept here so this
    module needs no AI client): itself and its family's "(side
    undetermined)" name, or for an undetermined name every sided one."""
    family = unsided_part(part) or part
    if part.endswith(UNDETERMINED_SUFFIX):
        return [p for p in KNOWN_PARTS if (unsided_part(p) or p) == family]
    return [part] if family == part else [part, family]


def _twins(a: str, b: str) -> bool:
    return a == b or b in covers(a)


def _firm(item: dict, strict: bool) -> bool:
    """A parent whose side a linked part may take: a sided name the side
    resolver didn't infer, not Low; strict: also seen twice (two photos or
    two reads)."""
    _, side = split_side(str(item.get("part") or ""))
    if side not in ("Left", "Right") or item.get("side_inferred") or item.get("ai_confidence") == "Low":
        return False
    if strict:
        return len(set(item.get("seen_in") or [])) >= 2 or len(set(item.get("found_by") or [])) >= 2
    return True


# ------------------------------------------------------------ photos


def _photo_ref(photo) -> str | None:
    """A photo as items name it: "@<id>" for a row, else a stored run's
    "ref" / "label" / number "n"."""
    pid = _get(photo, "id")
    if pid:
        return f"@{pid}"
    for name in ("ref", "label"):
        if _get(photo, name):
            return str(_get(photo, name))
    n = _get(photo, "n")
    return f"P{n:02d}" if isinstance(n, int) else None


def _usable(photo) -> bool:
    return not _get(photo, "excluded") and _get(photo, "quality") != "unusable"


def _shows_damage(photo) -> bool:
    return bool(_get(photo, "shows_damage", _get(photo, "damage"))) or _get(photo, "kind") == "damage"


# ------------------------------------------------------------ the build


@dataclass
class _Ctx:
    opts: Options
    version: str
    listed: list[dict]
    levels: list[str]
    listed_names: set[str]
    skip: set[str]
    others: list[dict]
    photos: list
    intact: dict
    neighbours: dict | None
    # The assembly walk (assembly_walk.Walk), None when off.
    walk: object = None
    # The car map's facts on a link, by its link_rule (the car map module's
    # review_info, through the caller -- this module never imports it), or
    # None: L1 alone, and no item gets the group fields.
    info: object = None


# What the relation info may put on a linked part (SecondLookItem, K4).
INFO_FIELDS = ("assembly", "assembly_name", "prior", "hop", "via", "fitted", "fitted_note")


def _info_of(link_rule: str, parent_part: str, ctx: "_Ctx") -> dict:
    """The review screen's fields for one linked part from ctx.info, {} for
    none (no info, a rule it doesn't know, or any error: a bad answer never
    fails a build). The group: the assembly, one per side of the car when
    the assembly is a sided one ("front-door.left") -- the left and right
    front doors are two groups, as L1's two parents are."""
    if ctx.info is None:
        return {}
    try:
        meta = ctx.info(link_rule)
    except Exception:
        logger.warning("Linked parts: no car map facts for %s this time", link_rule, exc_info=True)
        return {}
    if not isinstance(meta, dict) or not isinstance(meta.get("assembly"), str) or not meta["assembly"]:
        return {}
    group = meta["assembly"]
    side = split_side(parent_part)[1] if meta.get("sided") else None
    if side in ("Left", "Right"):
        group = f"{group}.{side.lower()}"
    out = {"assembly": group, "assembly_name": meta.get("assembly_name") or None}
    prior = meta.get("prior")
    out["prior"] = prior if isinstance(prior, int) and not isinstance(prior, bool) and 1 <= prior <= 5 else None
    hop = meta.get("hop")
    out["hop"] = hop if hop in (1, 2) else None
    for name in ("via", "fitted_note"):
        if isinstance(meta.get(name), str) and meta[name].strip():
            out[name] = meta[name].strip()
    if meta.get("fitted") == "if_fitted":
        out["fitted"] = "if_fitted"
    return out


def _end_crush(listed: list[dict], levels: list[str], photos: list) -> set[str]:
    """The ends hit to the crush path: a heavy part there AND either
    END_CROWD parts listed there, a structural part listed there (or the
    underbody), or a sorter photo of the underbody showing damage."""
    underbody_photo = any(_usable(p) and _get(p, "area") == "underbody" and _shows_damage(p) for p in photos)
    parts = {str(it.get("part")) for it in listed}
    out = set()
    for end in ("front", "rear"):
        here = [(it, lvl) for it, lvl in zip(listed, levels) if end_of(str(it.get("part") or "")) == end]
        if not any(lvl == "heavy" for _, lvl in here):
            continue
        crowd = len({str(it.get("part")) for it, _ in here}) >= END_CROWD
        if crowd or parts & STRUCTURAL[end] or underbody_photo:
            out.add(end)
    return out


def _impact_side(end: str | None, ctx: _Ctx) -> tuple[str | None, str | None]:
    """(side, side_from) for an "impact" link at `end`: a clear majority (at
    least two to one) of the firm sided listed parts at that end, naming
    the first of them; else (None, None) -- the child's "(side
    undetermined)" name. Never vehicle_info.point_of_impact: the AI drafts
    it ("Rear LH corner") and nothing checks it as _firm checks a part, so
    a side taken from it would be the AI's guess presented as the list's
    (contract 10 review H1); once added, the part would pass it on to its
    own linked parts as a firm side."""
    firm: dict[str, list[str]] = {"Left": [], "Right": []}
    for it in ctx.listed:
        part = str(it.get("part") or "")
        if end and end_of(part) == end and _firm(it, ctx.opts.side == "strict"):
            firm[split_side(part)[1]].append(item_key(it) or part)
    for side, other in (("Left", "Right"), ("Right", "Left")):
        if firm[side] and len(firm[side]) >= 2 * len(firm[other]):
            return side, firm[side][0]
    return None, None


def _parent_links(parent: dict, table_links: list[dict]) -> list[tuple[dict, str | None]]:
    """(link, the parent's side in it) for each link whose parent is this
    part, in table order. A "(side undetermined)" parent takes its sided
    names' links (taxonomy.unsided_part); when those are of two parts (a
    front and a rear door), only the children every one of them has, each
    by its strictest link."""
    part = str(parent.get("part") or "")
    if not part.endswith(UNDETERMINED_SUFFIX):
        return [(link, side) for link in table_links for name, side in _expand(link["parent"]) if name == part]
    groups: dict[str, dict[str, dict]] = {}
    for link in table_links:
        for name, side in _expand(link["parent"]):
            if side == "Right" or unsided_part(name) != part:
                continue
            child = link["child"].replace(SIDE, "Left")
            child = unsided_part(child) if link["side"] == "same" else child
            if child:
                group = groups.setdefault(split_side(name)[0], {})
                held = group.get(child)
                if held is None or _RANK[link["min_hit"]] > _RANK[held["min_hit"]]:
                    group[child] = link
    if not groups:
        return []
    common = set.intersection(*(set(g) for g in groups.values()))
    picked: dict[str, dict] = {}
    for group in groups.values():
        for child, link in group.items():
            if child in common and (child not in picked or _RANK[link["min_hit"]] > _RANK[picked[child]["min_hit"]]):
                picked[child] = link
    order = {id(link): n for n, link in enumerate(table_links)}
    return [(link, None) for link in sorted(picked.values(), key=lambda link: order[id(link)])]


def _child(link: dict, parent: dict, parent_side: str | None, ctx: _Ctx) -> tuple[str, str | None] | None:
    """(the child's name, side_from) for one link from one parent, or None
    when there is no honest name for it."""
    child, kind = link["child"], link["side"]
    if SIDE not in child:
        return child, None
    if kind == "same" and parent_side and _firm(parent, ctx.opts.side == "strict"):
        return child.replace(SIDE, parent_side), item_key(parent) or parent.get("part")
    if kind == "impact":
        side, side_from = _impact_side(end_of(str(parent.get("part") or "")), ctx)
        if side:
            return child.replace(SIDE, side), side_from
    undetermined = unsided_part(child.replace(SIDE, "Left"))
    return (undetermined, None) if undetermined else None


def _catalogue_links(parent: dict, table_links: list[dict], ctx: _Ctx) -> list[tuple[dict, str | None]]:
    """The parts catalogue's diagram neighbours of this parent (catalogue.
    diagram_neighbours' answer, given by the caller: nothing here reads a
    cache) that no table link of the parent names, as CATALOGUE_LINK
    links. A side comes from the parent as a table link's would, never
    from the catalogue."""
    part = str(parent.get("part") or "")
    named = {n for link in table_links if any(p == part for p, _ in _expand(link["parent"]))
             for n, _ in _expand(link["child"])}
    _, parent_side = split_side(part)
    out: dict[str, dict] = {}
    for found in (ctx.neighbours or {}).get(part) or []:
        child = found.get("part") if isinstance(found, dict) else None
        if not isinstance(child, str) or child not in CANONICAL_PARTS or child == OTHER_PART or child == part:
            continue
        base, side = split_side(child)
        sided = (side or child.endswith(UNDETERMINED_SUFFIX)) and f"Left {base}" in CANONICAL_PARTS
        name = f"{SIDE} {base}" if sided else child
        if name in out or any(_twins(n, c) for n, _ in _expand(name) for c in named):
            continue
        kind = "none" if SIDE not in name else "same" if parent_side else "impact"
        diagram = " ".join(str(found.get("diagram") or "").split())[:60]
        slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
        out[name] = {**CATALOGUE_LINK, "id": f"oem>{slug}"[:36], "parent": part, "child": name, "side": kind,
                     "trigger_tokens": [], "oem": [diagram] if diagram else [],
                     "reason": f"Shares a parts diagram with the {part}; check it with that part."}
    return [(link, parent_side) for link in out.values()]


def _seen_photos(child: str, ctx: _Ctx) -> list[str]:
    """The sorter's photos showing damage that it tagged with the child (the
    same part, its side unsaid on either, never the other side's twin:
    evidence.same_part), in camera order."""
    refs = [_photo_ref(p) for p in ctx.photos
            if _usable(p) and _shows_damage(p) and _get(p, "part") and evidence.same_part(_get(p, "part"), child)]
    return [r for r in refs if r][:MAX_SEEN]


def _parent_photo(parent: dict, ctx: _Ctx) -> str | None:
    """The parent's best photo still in the case: its first evidence entry
    (best first), its photo_reference, the first photo it was seen in.
    Never a detection Round 2 located part box's photo (by "locate:...",
    the R2 review's L9): where the part is, not why it is listed."""
    gone = {_photo_ref(p) for p in ctx.photos if not _usable(p)}
    refs = [e.get("photo") for e in parent.get("evidence") or []
            if isinstance(e, dict) and not evidence.located(e)]
    refs += [parent.get("photo_reference")] + list(parent.get("seen_in") or [])
    return next((r for r in refs if isinstance(r, str) and r and r not in gone), None)


def intact_in(child: str, intact: dict) -> list[str]:
    """The zone reads ("zone:rear") that listed the child intact
    (ai_draft["zone_intact"] keeps no photo per part). An undetermined
    child is intact only where every sided name of it is."""
    out = []
    for read, parts in (intact or {}).items():
        names = {p for p in parts if isinstance(p, str)} if isinstance(parts, list) else set()
        sided = [p for p in covers(child) if p != child] if child.endswith(UNDETERMINED_SUFFIX) else []
        if child in names or (sided and all(p in names for p in sided)):
            out.append(str(read))
    return sorted(out)


def bucket_of(link: dict, seen: bool) -> str:
    """seen: the sorter tagged a photo with it; strip_down: hidden behind
    the parent or on the crush path, or flagged for a strip-down; likely:
    the rest (attached, opens with it -- seen once it is opened -- or a
    visible part)."""
    if seen:
        return "seen"
    if link["hidden"] and (link["strip_down"] or link["type"] in ("behind", "crush")):
        return "strip_down"
    return "likely"


def _candidates(parents: list[int], ctx: _Ctx, table: dict) -> list[dict]:
    """Every linked part the gate lets through from these parents (indexes
    into ctx.listed), one per link, nothing skipped yet."""
    table_links = [link for link in table.get("links") or [] if isinstance(link, dict)]
    out = []
    for n in parents:
        parent = ctx.listed[n]
        part = str(parent.get("part") or "")
        if not known_part(part) or part == OTHER_PART:
            continue
        level, words = ctx.levels[n], set(_tokens(parent))
        pairs = _parent_links(parent, table_links)
        if ctx.opts.catalogue:
            pairs += _catalogue_links(parent, table_links, ctx)
        photo = _parent_photo(parent, ctx)
        for t_index, (link, parent_side) in enumerate(pairs):
            if link["type"] not in ctx.opts.types:
                continue
            if _RANK[level] < _RANK[link["min_hit"]] and not words & set(link.get("trigger_tokens") or []):
                continue
            if ctx.walk is not None and not assembly_walk.position_ok(ctx.walk, part, link["id"], ctx.version):
                continue
            named = _child(link, parent, parent_side, ctx)
            if named is None:
                continue
            child, side_from = named
            seen = _seen_photos(child, ctx)
            bucket = bucket_of(link, bool(seen))
            signs_yes = []
            if ctx.walk is not None:
                bucket, signs_yes = assembly_walk.lift(ctx.walk, part, link["id"], ctx.version, bucket)
            entries = [evidence.entry(r, evidence.SORT) for r in seen]
            entries += [evidence.entry(photo, "link")] if photo else []
            oem = list(link.get("oem") or [])
            if ctx.opts.catalogue:
                oem += [" ".join(str(f.get("diagram") or "").split())[:60] for f in (ctx.neighbours or {}).get(part) or []
                        if isinstance(f, dict) and isinstance(f.get("part"), str) and _twins(child, f["part"])]
            rule = f"{ctx.version}:{link['id']}"
            meta = _info_of(rule, part, ctx)
            order = (_BUCKET_RANK[bucket], _TYPE_RANK[link["type"]], -_RANK[level], n, t_index)
            if ctx.opts.order == "prior":
                # K1-design.md 5b: bucket, prior, type, -hit, parent, table.
                prior = meta.get("prior")
                order = order[:1] + (prior if prior is not None else PRIOR_NONE,) + order[1:]
            out.append({
                "key": item_key({"part": child}) or child, "part": child, "source": "link",
                "linked_from": [item_key(parent) or part], "link_type": link["type"],
                "link_rule": rule, "hit": level, "bucket": bucket,
                "hidden": bool(link["hidden"]), "needs_strip_down": bool(link["strip_down"]),
                "side_from": side_from, "intact_in": [], "oem": list(dict.fromkeys(o for o in oem if o))[:MAX_OEM],
                "reason": evidence.clean_reason(link["reason"]), "photo_reference": seen[0] if seen else None,
                "seen_in": seen, "evidence": evidence.order_evidence(entries, seen[0] if seen else None),
                "covers": covers(child), "confidence": None,
                **meta,
                "_order": order,
            })
            if signs_yes:
                out[-1]["signs_yes"] = signs_yes
    return out


def _hop2_child(then: dict, middle: dict, anchor: dict, ctx: _Ctx) -> tuple[str, str | None] | None:
    """(the end part's name, side_from) of a second hop through `middle`
    (a first-hop candidate): "same" takes the middle part's side only when
    that side came from a firm rule (it has a side_from); "impact" as at
    the first hop, at the middle part's end (else the anchor's); otherwise
    the "(side undetermined)" name. Never from the walk."""
    child, kind = then["child"], then["side"]
    if SIDE not in child:
        return child, None
    side = split_side(middle["part"])[1]
    if kind == "same" and side in ("Left", "Right") and middle.get("side_from"):
        return child.replace(SIDE, side), middle["side_from"]
    if kind == "impact":
        found, side_from = _impact_side(end_of(middle["part"]) or end_of(str(anchor.get("part") or "")), ctx)
        if found:
            return child.replace(SIDE, found), side_from
    undetermined = unsided_part(child.replace(SIDE, "Left"))
    return (undetermined, None) if undetermined else None


def _hop2(found: list[dict], ctx: _Ctx, table: dict) -> list[dict]:
    """K1 S4 (intake_link_hops 2): the second hops the walk unlocked, as
    candidates beside `found` (the first hops). A chain fires when its
    `first` relation gave a candidate that is not listed or dismissed (nor
    dropped as intact), a sign that unlocks it about the anchor said yes,
    and the anchor's level reaches the chain's min_hit. The item comes from
    `then` with the middle part as its parent: strip_down unless a sorter
    photo shows it, hop 2, via the middle part, linked_from the anchor, the
    chain's rule and reason, the unlocking signs in signs_yes. Never a
    third hop (a second hop is never a `first`), never a part a first hop
    already gives, at most walk.hop2_max a case."""
    walk = ctx.walk
    chains = assembly_walk.chains(walk, ctx.version)
    if not chains:
        return []
    by_id = {link["id"]: link for link in table.get("links") or []
             if isinstance(link, dict) and isinstance(link.get("id"), str)}
    anchors = {item_key(it) or str(it["part"]): n for n, it in enumerate(ctx.listed)}
    out: list[dict] = []
    for chain in chains:
        first, then = by_id.get(chain["first"]), by_id.get(chain["then"])
        if first is None or then is None or then["type"] not in ctx.opts.types:
            continue
        for middle in found:
            if middle["link_rule"] != f"{ctx.version}:{first['id']}" or len(out) >= walk.hop2_max:
                continue
            if set(middle["covers"]) & ctx.listed_names or middle["key"] in ctx.skip or middle["part"] in ctx.skip:
                continue
            if ctx.opts.intact == "drop" and intact_in(middle["part"], ctx.intact):
                continue
            n = anchors.get(middle["linked_from"][0])
            if n is None or _RANK[ctx.levels[n]] < _RANK[chain["min_hit"]]:
                continue
            anchor = ctx.listed[n]
            signs = assembly_walk.unlocked(walk, str(anchor.get("part") or ""), chain["id"])
            if not signs:
                continue
            named = _hop2_child(then, middle, anchor, ctx)
            if named is None:
                continue
            child, side_from = named
            key = item_key({"part": child}) or child
            if set(covers(child)) & ctx.listed_names or child in ctx.skip or key in ctx.skip:
                continue
            if any(_twins(child, other["part"]) for other in found + out):
                continue
            seen = _seen_photos(child, ctx)
            bucket = "seen" if seen else "strip_down"
            photo = _parent_photo(anchor, ctx)
            entries = [evidence.entry(r, evidence.SORT) for r in seen]
            entries += [evidence.entry(photo, "link")] if photo else []
            rule = f"{ctx.version}:{chain['id']}"
            # The Review step's group (K4): the chain's facts (the car map's
            # review_lookup knows chain rules: the first relation's assembly,
            # sided by the anchor as its first hop is, no prior, the end
            # part's "if fitted"); {} without the map's facts.
            meta = _info_of(rule, str(anchor.get("part") or ""), ctx)
            out.append({
                "key": key, "part": child, "source": "link",
                "linked_from": [middle["linked_from"][0]], "link_type": then["type"],
                "link_rule": rule, "hit": ctx.levels[n], "bucket": bucket,
                "hidden": bool(then["hidden"]), "needs_strip_down": bool(then["strip_down"]),
                "side_from": side_from, "intact_in": [], "oem": [],
                "reason": evidence.clean_reason(chain["reason"]), "photo_reference": seen[0] if seen else None,
                "seen_in": seen, "evidence": evidence.order_evidence(entries, seen[0] if seen else None),
                "covers": covers(child), "confidence": None,
                **meta,
                "hop": 2, "via": middle["part"], "signs_yes": signs,
                # After every first hop of its bucket (whatever the order:
                # past every type rank and every prior), in its middle
                # part's order.
                "_order": (_BUCKET_RANK[bucket], _HOP2_RANK) + tuple(middle["_order"][1:]),
            })
    return out


def _join(kept: dict, other: dict) -> None:
    """In place: another link to the same part. Its parent joins
    linked_from, its photos the evidence, its diagram names oem; a sided
    name it found (a firm parent's side) replaces kept's undetermined one."""
    kept["linked_from"] = list(dict.fromkeys(kept["linked_from"] + other["linked_from"]))
    kept["oem"] = list(dict.fromkeys(kept["oem"] + other["oem"]))[:MAX_OEM]
    kept["seen_in"] = list(dict.fromkeys(kept["seen_in"] + other["seen_in"]))[:MAX_SEEN]
    kept["evidence"] = evidence.order_evidence(kept["evidence"] + other["evidence"], kept.get("photo_reference"))
    if kept["part"].endswith(UNDETERMINED_SUFFIX) and not other["part"].endswith(UNDETERMINED_SUFFIX):
        for name in ("key", "part", "covers", "side_from"):
            kept[name] = other[name]


def group_of(item: dict) -> str:
    """The Review step's group of a linked part, which its per-group cap
    counts in: its assembly (with the car map's facts), else the first
    listed part it hangs off (L1)."""
    group = item.get("assembly")
    if isinstance(group, str) and group:
        return group
    parents = item.get("linked_from") or [""]
    return str(parents[0])


def _strip_block(item: dict) -> bool:
    """A linked part for the strip-down block: in the strip-down bucket, or
    a second hop's (hop 2) that no photo shows (K1-design.md 5b: always
    there unless a photo shows it)."""
    return item.get("bucket") == "strip_down" or (item.get("hop") == 2 and item.get("bucket") != "seen")


def cap_slot(item: dict) -> tuple[str, str]:
    """The per-group cap's slot of a linked part: (its group, its bucket),
    the strip-down block's parts as "strip_down" -- and a second hop's
    (hop 2) in a slot of its own, "hop2": it sorts after every first hop
    of its bucket, so sharing the anchor group's strip-down slot it would
    never show on the heavy hits that open it (I3). At most
    assembly_walk.MAX_HOP2 a case all the same."""
    if item.get("hop") == 2:
        return group_of(item), "hop2"
    return group_of(item), "strip_down" if _strip_block(item) else str(item.get("bucket"))


def cut_reason(item: dict) -> str:
    """F2/F5: why a cap cut a linked part -- "intact" when a zone read
    listed it intact (intact_in: demoted, so last, so cut; or dropped),
    else "limit"."""
    return "intact" if item.get("intact_in") else "limit"


def _cap(kept: list[dict], opts: Options, counted: dict | None = None, cut: list | None = None) -> list[dict]:
    """The linked parts shown, in order, from `kept` (sorted already): at
    most opts.assembly_max per group (group_of) and bucket (cap_slot: the
    strip-down block's as strip-down, a second hop's in its own slot) --
    then opts.max_items in all; with
    stripdown_cap "out", the strip-down block (_strip_block) apart, after
    the others, at most opts.stripdown_max, never counted in max_items,
    each marked folded. counted: the per-(group, bucket) counts already shown
    (for_parent: the open ones of the same groups). cut (F2/F5), when
    given: the parts the caps left out are added to it, in kept's order,
    each with its cut_reason."""
    per: dict[tuple[str, str], int] = dict(counted or {})
    out_block = opts.stripdown_cap == "out"
    shown, folded = [], []
    for it in kept:
        block = _strip_block(it)
        slot = cap_slot(it)
        if per.get(slot, 0) < opts.assembly_max:
            per[slot] = per.get(slot, 0) + 1
            (folded if out_block and block else shown).append(it)
    shown = shown[:max(0, opts.max_items)]
    folded = folded[:max(0, opts.stripdown_max)]
    for it in folded:
        it["folded"] = True
    if cut is not None:
        kept_ids = {id(it) for it in shown + folded}
        cut.extend({**it, "cut_reason": cut_reason(it)} for it in kept if id(it) not in kept_ids)
    return shown + folded


def _build(parents: list[int], ctx: _Ctx, table: dict, counted: dict | None = None, cut: list | None = None
           ) -> tuple[list[dict], dict[str, dict]]:
    found = sorted(_candidates(parents, ctx, table), key=lambda c: c["_order"])
    if ctx.walk is not None:
        found = sorted(found + _hop2(found, ctx, table), key=lambda c: c["_order"])
    items: list[dict] = []
    joins: dict[str, dict] = {}
    for cand in found:
        if set(cand["covers"]) & ctx.listed_names or cand["key"] in ctx.skip or cand["part"] in ctx.skip:
            continue
        other = next((o for o in ctx.others
                      if _twins(cand["part"], str(o.get("part"))) or cand["key"] == o.get("key")), None)
        if other is not None:
            # Another suggestion is this part already (a zone read's, the
            # second look's): one item, that one, with this parent joined.
            key = str(other.get("key") or item_key(other) or other.get("part"))
            held = joins.setdefault(key, {"linked_from": [], "link_type": cand["link_type"],
                                          "link_rule": cand["link_rule"], "hit": cand["hit"]})
            held["linked_from"] = list(dict.fromkeys(held["linked_from"] + cand["linked_from"]))
            continue
        twin = next((it for it in items if _twins(cand["part"], it["part"])), None)
        if twin is not None:
            _join(twin, cand)
            continue
        items.append(cand)
    kept = []
    dropped = []
    for it in items:
        it["intact_in"] = intact_in(it["part"], ctx.intact) if ctx.opts.intact != "ignore" else []
        if it["intact_in"] and ctx.opts.intact == "drop":
            dropped.append(it)
            continue
        kept.append(it)
    kept.sort(key=lambda it: (bool(it["intact_in"]),) + it["_order"])
    capped: list[dict] | None = [] if cut is not None else None
    shown = _cap(kept, ctx.opts, counted, capped)
    for it in shown:
        del it["_order"]
    if cut is not None:
        # F2/F5: what the caps cut, then what a zone read's "intact"
        # dropped (intake_links_intact "drop"), in their order.
        dropped.sort(key=lambda it: it["_order"])
        for it in capped + [{**it, "cut_reason": "intact"} for it in dropped]:
            it.pop("_order", None)
            cut.append(it)
    return shown, joins


def _context(listed, others, photos, zone_intact, table, opts, dismissed, neighbours, walk=None,
             info=None) -> _Ctx:
    listed = [it for it in listed or [] if isinstance(it, dict) and it.get("part")]
    photos = list(photos or [])
    levels = [hit_of(it) or opts.unknown_hit for it in listed]
    if walk is not None:
        levels = assembly_walk.raise_levels(walk, listed, levels)
    crushed = _end_crush(listed, levels, photos)
    levels = ["crush" if lvl == "heavy" and end_of(str(it["part"])) in crushed else lvl
              for it, lvl in zip(listed, levels)]
    return _Ctx(opts=opts, version=str(table.get("version") or ""), listed=listed, levels=levels,
                listed_names={str(it["part"]) for it in listed}, skip={str(k) for k in dismissed or () if k},
                others=[o for o in others or [] if isinstance(o, dict) and o.get("part")], photos=photos,
                intact=zone_intact if isinstance(zone_intact, dict) else {},
                neighbours=neighbours if isinstance(neighbours, dict) else None, walk=walk,
                info=info if callable(info) else None)


def levels_of(listed: list[dict], photos: list, opts: Options, walk=None) -> dict[str, str]:
    """{a listed part's key: how hard it was hit} as build sees it (hit_of,
    Options.unknown_hit, the walk's yes signs, the end bonus): for the car
    map's "also check" words (the car map module's also_check), which look
    at the relations build never saw."""
    ctx = _context(listed, (), photos, {}, {}, opts, (), None, walk=walk)
    out: dict[str, str] = {}
    for it, level in zip(ctx.listed, ctx.levels):
        out.setdefault(item_key(it) or str(it["part"]), level)
    return out


def build_all(listed: list[dict], zone_suggestions: list[dict], photos: list, zone_intact: dict, table: dict | None,
              opts: Options, *, dismissed=(), neighbours: dict | None = None, info=None,
              walk=None, cut: list | None = None) -> tuple[list[dict], dict[str, dict]]:
    """(the linked-part suggestions, the joins). The suggestions are what
    build returns. The joins are the links whose part another suggestion
    is already (zone_suggestions: a zone read's, the second look's), by
    that suggestion's key: {"linked_from", "link_type", "link_rule",
    "hit"} for the caller to put on it, so the part shows once. walk: the
    assembly walk (assembly_walk.Walk), None for today's build. cut (F2/F5,
    settings.intake_keep_cut): when a list is given, the linked parts the
    caps cut -- and those a zone read's "intact" dropped -- are added to
    it, each with its cut_reason (_cap)."""
    if not table or not isinstance(table.get("links"), list):
        return [], {}
    ctx = _context(listed, zone_suggestions, photos, zone_intact, table, opts, dismissed, neighbours, walk=walk,
                   info=info)
    return _build(list(range(len(ctx.listed))), ctx, table, cut=cut)


def build(listed: list[dict], zone_suggestions: list[dict], photos: list, zone_intact: dict, table: dict | None,
          opts: Options, *, dismissed=(), neighbours: dict | None = None, info=None, walk=None) -> list[dict]:
    """The linked-part suggestions for one reading, as SecondLookItem dicts
    (source "link"), at most opts.max_items (and, with stripdown_cap "out",
    the folded strip-down block after them, at most opts.stripdown_max),
    in the order shown (see the module's docstring). listed: the damage
    list's items; zone_suggestions: the other suggestions (a part one of
    them is gets no item of its own); photos: the case's photos
    (IntakePhoto rows, or a stored run's dicts); zone_intact:
    ai_draft["zone_intact"]; dismissed: the keys a person marked Not
    damaged; neighbours: catalogue.diagram_neighbours' answer, used when
    opts.catalogue; info: link_rule -> the car map's facts on that link
    (the car map module's review_info), which puts the group fields on
    each item and groups the per-group cap by assembly -- None: L1, as
    before; walk: the assembly walk (None: off)."""
    return build_all(listed, zone_suggestions, photos, zone_intact, table, opts, dismissed=dismissed,
                     neighbours=neighbours, info=info, walk=walk)[0]


def for_parent(item: dict, data: dict, photos: list, table: dict | None, opts: Options, *,
               zone_intact: dict | None = None, neighbours: dict | None = None, info=None,
               walk=None, cut: list | None = None) -> list[dict]:
    """The new linked parts of one listed part -- the re-link after a person
    adds it: build from that part alone, against the case as it is (`data`,
    the part on its list or not yet): what is listed, every suggestion
    there already (open or decided), the dismissed. At most the room
    opts.max_items leaves beside the open linked parts; with stripdown_cap
    "out", the folded ones by the room opts.stripdown_max leaves beside the
    open folded ones. With the car map's facts (info), an assembly's open
    linked parts count in its per-group cap. walk: the assembly walk; its
    second hops count the case's open ones against MAX_HOP2. cut (F2/F5):
    when a list is given, what the caps and the room left out is added to
    it, each with its cut_reason, as build_all's."""
    if not table or not isinstance(table.get("links"), list) or not isinstance(item, dict) or not item.get("part"):
        return []
    data = data if isinstance(data, dict) else {}
    key = item_key(item)
    listed = [it for it in data.get("damage_summary") or [] if isinstance(it, dict) and it.get("part")]
    if not any(it is item or item_key(it) == key for it in listed):
        listed = listed + [item]
    look = data.get("second_look") if isinstance(data.get("second_look"), dict) else {}
    existing = [it for it in look.get("items") or [] if isinstance(it, dict) and it.get("part")]
    dismissed = {k for k in look.get("dismissed") or [] if isinstance(k, str)}
    if walk is not None:
        open_hop2 = sum(1 for it in existing if it.get("source") == "link" and it.get("hop") == 2
                        and str(it.get("key") or it["part"]) not in dismissed)
        walk = assembly_walk.with_room(walk, open_hop2)
    ctx = _context(listed, existing, photos, zone_intact, table, opts, dismissed, neighbours, walk=walk, info=info)
    mine = next(n for n, it in enumerate(ctx.listed) if it is item or item_key(it) == key)
    open_links = [it for it in existing if it.get("source") == "link"
                  and str(it.get("key") or it["part"]) not in dismissed
                  and not set(it.get("covers") or covers(str(it["part"]))) & ctx.listed_names]
    counted: dict[tuple[str, str], int] = {}
    if ctx.info is not None:
        for it in open_links:
            if isinstance(it.get("assembly"), str) and it["assembly"]:
                slot = cap_slot(it)
                counted[slot] = counted.get(slot, 0) + 1
    items, _ = _build([mine], ctx, table, counted, cut=cut)
    if opts.stripdown_cap != "out":
        shown = items[:max(0, opts.max_items - len(open_links))]
    else:
        room = max(0, opts.max_items - sum(1 for it in open_links if not it.get("folded")))
        fold_room = max(0, opts.stripdown_max - sum(1 for it in open_links if it.get("folded")))
        shown = [it for it in items if not it.get("folded")][:room] + [it for it in items if it.get("folded")][:fold_room]
    if cut is not None:
        # Past the room the open linked parts leave: cut too.
        kept_ids = {id(it) for it in shown}
        cut.extend({**{k: v for k, v in it.items() if k != "folded"}, "cut_reason": cut_reason(it)}
                   for it in items if id(it) not in kept_ids)
    return shown
