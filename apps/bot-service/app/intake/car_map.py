"""The car map (plans/2026-11-18-car-knowledge/K1-design.md): how a car is
built, as data, for the linked parts. Pure: no database, no Gemini, no web
request -- the only I/O is reading the map files.

A map (intake/car_map.json, format in car_map.schema.json) holds:
  - assemblies: parts built and hit together -- the front bumper assembly
    is the cover, absorber, beam, brackets, fog lamps, sensors ... Each has
    its members (part, role, hidden, strip_down, "if fitted", the bodies
    it is on), its anchors (the panels whose damage starts the assembly
    walk), its signs (short yes/no questions a light model answers about a
    damaged anchor, each naming the relations it points at and the second
    hops it unlocks) and its catalogue hooks (OEM diagram names and
    numbers, for layer 2 and part numbers later);
  - relations: L1's links (intake/link_table.json) with an assembly, and
    optionally where on the parent the hit must be (centre / corner), a
    body gate and a prior (how often a surveyor lists the part: 1 most
    often .. 5, with its source);
  - chains: a second hop, parent -> part -> part, opened only by a sign
    answered yes. Never more than two hops.

links.build reads a link table, not a map: links_table() compiles a map
into one, from_l1() turns L1 into a map that compiles back into L1 exactly
(scripts/test_car_map.py: links.build gives the same output on either),
and table_for_links() is the seam -- settings.intake_car_map, "off" by
default: L1 as it is, the very same object. links never imports this
module (no cycle when the seam is wired).

The Review step's groups (K4): link_tables() is the seam as the app calls
it (second_look._store, suggestions.relinked, the linked check's areas),
with review_lookup() -- each relation's facts by link_rule, which
links.build takes as `info` -- and add_also_check(), the words for the new
parts a names-off table leaves out.

New part names below today's taxonomy (car_map_new_names.json, owner
option a) are proposed, not applied: a map may name them, and the
compiled table leaves out every relation that does until the taxonomy
holds them (names=True, after the integrator's change).

Everything that reads a file is total: load() and load_new_names() answer
None (and log) for a missing, unreadable or invalid file, and validate()
answers lines, never an exception, for any JSON value.
"""
import copy
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import HIT_LEVELS, LINK_TYPES, settings
from app.intake import links
from app.reports.taxonomy import (
    BODY_TYPES,
    CANONICAL_PARTS,
    FIRM_CONDITION_TOKENS,
    OTHER_PART,
    UNDETERMINED_SUFFIX,
    body_class,
    body_type_for,
)

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
MAP_PATH = HERE / "car_map.json"
NEW_NAMES_PATH = HERE / "car_map_new_names.json"
SCHEMA_PATH = HERE / "car_map.schema.json"

FORMAT = 1
SIDE = links.SIDE
# An assembly's area: the end or side of the car it is walked from.
AREAS = ("front", "rear", "side", "underbody", "wheel")
# A relation's area: its skill file and the linked check's end (links).
RELATION_AREAS = links.TABLE_AREAS
LINK_SIDES = links.LINK_SIDES
ROLES = ("panel", "structure", "mount", "absorber", "mechanism", "seal", "trim", "lamp", "glass", "electrical",
         "mechanical", "safety", "wheel", "other")
# Where on the parent the hit has to be for a relation to show; "any":
# wherever (and every relation when no walk says where).
POSITIONS = ("any", "centre", "corner")
# What the assembly walk may answer for "where on the panel" (K3b). It
# never says which corner: a side never comes from the model.
HIT_AT = ("centre", "corner", "whole", "cannot_see")
SIGN_ANSWERS = ("yes", "no", "cannot_see")
FITTED = ("always", "if_fitted")
# A body gate: taxonomy BODY_TYPES ("Other" says nothing) or a body class
# (taxonomy.body_class: hatch -- a tailgate -- or sedan -- a boot lid).
BODY_GATES = tuple(b for b in BODY_TYPES if b != "Other") + ("hatch", "sedan")
PRIOR_RANKS = (1, 2, 3, 4, 5)
MAX_HOPS = 2
MAX_REASON = links.MAX_REASON
MAX_ASK = 100
MAX_NAME = 60
MAX_NOTES = 300
MAX_DIAGRAMS = 12
# What a compiled link carries, in L1's order (link_table.json).
LINK_KEYS = ("id", "parent", "child", "type", "side", "min_hit", "trigger_tokens", "hidden", "strip_down", "area",
             "reason", "sources")

# The keys each object may have (car_map.schema.json's additionalProperties):
# a typo ("strip-down") is a line, not a field quietly ignored.
KEYS = {
    "map": {"version", "format", "built_from", "from_l1", "assemblies", "relations", "chains"},
    "assembly": {"id", "name", "area", "anchors", "members", "signs", "catalogue", "notes", "sources"},
    "member": {"part", "role", "hidden", "strip_down", "fitted", "body", "new"},
    "sign": {"id", "ask", "level", "points_to", "unlocks"},
    "relation": {"assembly", "position", "body", "prior"} | set(LINK_KEYS),
    "chain": {"id", "first", "then", "min_hit", "reason"},
    "catalogue": {"oem_diagrams", "oem_numbers"},
    "prior": {"rank", "source"},
}

_ID = re.compile(r"^[a-z0-9][a-z0-9>._-]{0,63}$")  # no colon: link_rule is "<version>:<id>"
_SLUG = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
_SIGN_ID = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
_SCHEME = re.compile(r"^[a-z]{2,20}$")
_NUMBER = re.compile(r"^\d{3,5}$")
_DIGITS3 = re.compile(r"\d{3,}")
# The practice cars' makes and models (and the catalogue cars read for
# diagram-pairs) must not leak into the knowledge (K5 fair test).
MAKE_WORDS = re.compile(r"\b(honda|civic|hr-?v|vezel|bmw|x1)\b", re.I)


# ------------------------------------------------------------ names


def expand(name: str) -> list[str]:
    """"{side} Fog Lamp" -> ["Left Fog Lamp", "Right Fog Lamp"]; any other
    name as it is."""
    return [name.replace(SIDE, side) for side in ("Left", "Right")] if SIDE in name else [name]


def base(name: str) -> str:
    """A map name without its {side}: "{side} Fog Lamp" -> "Fog Lamp"."""
    return name.replace(SIDE + " ", "").replace(SIDE, "").strip()


def _canonical_names() -> set[str]:
    """Today's names a map may use: canonical, never a "(side
    undetermined)" one, never Other."""
    return {p for p in CANONICAL_PARTS if not p.endswith(UNDETERMINED_SUFFIX) and p != OTHER_PART}


def _text(value, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _is_in(value, names) -> bool:
    """value is a name in `names` (a set): False for anything that isn't a
    string, so a list where a name should be is a line, never a TypeError."""
    return isinstance(value, str) and value in names


def _str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _extra(obj: dict, kind: str) -> list[str]:
    """A line naming the keys `obj` may not have (KEYS[kind]), else []."""
    unknown = sorted(str(k) for k in obj if k not in KEYS[kind])
    return [f"unknown key(s) {', '.join(unknown)}"] if unknown else []


# ------------------------------------------------------------ the new names


def validate_new_names(doc) -> list[str]:
    """What is wrong with car_map_new_names.json, as lines ([] when
    nothing). Each name is new (no expansion is a canonical part), once;
    a sided one ({side} at its start) has the "(side undetermined)" twin
    taxonomy.unsided_part would find; area, role, fitted, body as a map's;
    the firm wording is not itself a part name; a sided zone names Left,
    Right and twin. Total, as validate."""
    if not isinstance(doc, dict):
        return ["new names: not a JSON object"]
    errors: list[str] = []
    if not _text(doc.get("version"), 20):
        errors.append("new names: no version")
    entries = doc.get("names")
    if not isinstance(entries, list) or not entries:
        return errors + ["new names: no list of names"]
    canonical = set(CANONICAL_PARTS)
    twins = {e.get("twin") for e in entries if isinstance(e, dict) and isinstance(e.get("twin"), str)}
    names: set[str] = set()
    expanded: set[str] = set()
    for n, entry in enumerate(entries):
        where = f"new name {n}"
        if not isinstance(entry, dict):
            errors.append(f"{where}: not an object")
            continue
        name = entry.get("name")
        where = f"new name {n} ({name!r})"
        if not _text(name, MAX_NAME):
            errors.append(f"{where}: no name")
            continue
        sided = entry.get("sided")
        if not isinstance(sided, bool) or sided != name.startswith(SIDE + " ") or name.count(SIDE) > (1 if sided else 0):
            errors.append(f"{where}: sided must be true exactly when the name starts with {SIDE}")
            continue
        if name in names:
            errors.append(f"{where}: listed twice")
        names.add(name)
        for full in expand(name):
            if full in canonical:
                errors.append(f"{where}: {full!r} is already a canonical part")
            if full in expanded:
                errors.append(f"{where}: {full!r} twice")
            expanded.add(full)
        twin = entry.get("twin")
        if sided:
            family = base(name)
            tries = [family, family.removeprefix("Front ").removeprefix("Rear ")]
            found = next((t + UNDETERMINED_SUFFIX for t in dict.fromkeys(tries)
                          if t + UNDETERMINED_SUFFIX in canonical or t + UNDETERMINED_SUFFIX in twins), None)
            if not (isinstance(twin, str) and twin.endswith(UNDETERMINED_SUFFIX)) or twin in canonical:
                errors.append(f"{where}: a sided name needs a new \"(side undetermined)\" twin")
            elif found != twin:
                errors.append(f"{where}: twin {twin!r} is not the one taxonomy.unsided_part would find ({found!r})")
        elif twin is not None:
            errors.append(f"{where}: an unsided name has no twin")
        if entry.get("area") not in AREAS:
            errors.append(f"{where}: area {entry.get('area')!r}")
        if not (isinstance(entry.get("assembly"), str) and _SLUG.match(entry["assembly"])):
            errors.append(f"{where}: assembly is not an assembly id")
        if entry.get("role") not in ROLES:
            errors.append(f"{where}: role {entry.get('role')!r}")
        for flag in ("hidden", "strip_down"):
            if not isinstance(entry.get(flag), bool):
                errors.append(f"{where}: {flag} is not true or false")
        if entry.get("fitted") not in FITTED:
            errors.append(f"{where}: fitted {entry.get('fitted')!r}")
        errors += [f"{where}: {e}" for e in _body_errors(entry.get("body"))]
        firm = entry.get("firm")
        if not _text(firm, MAX_NAME) or firm in canonical or any(firm == x for x in expand(name)):
            errors.append(f"{where}: firm wording missing, too long, or a part name itself")
        hatch = entry.get("firm_hatch")
        if hatch is not None and (not _text(hatch, MAX_NAME) or hatch in canonical):
            errors.append(f"{where}: firm_hatch")
        zone = entry.get("zone")
        if sided:
            if not (isinstance(zone, dict) and set(zone) == {"Left", "Right", "twin"}
                    and all(isinstance(z, str) and z for z in zone.values())):
                errors.append(f"{where}: a sided name's zone names Left, Right and twin")
        elif not (isinstance(zone, str) and zone):
            errors.append(f"{where}: zone")
        stand_in = entry.get("stand_in")
        if stand_in is not None and not _text(stand_in, 50):
            errors.append(f"{where}: stand_in")
        coarse = entry.get("coarse")
        if coarse is not None and not _is_in(coarse, canonical):
            errors.append(f"{where}: coarse {coarse!r} is not a canonical part")
        if not _text(entry.get("also_check"), 30):
            errors.append(f"{where}: also_check (the words shown when the names are off), at most 30 characters")
        why = entry.get("why")
        if not _text(why, 200) or _DIGITS3.search(why) or MAKE_WORDS.search(why):
            errors.append(f"{where}: why missing, over 200 characters, with a 3+ digit number or a make")
    return errors


def load_new_names(path: Path | str | None = None) -> dict | None:
    """car_map_new_names.json (NEW_NAMES_PATH by default), or None -- and a
    log line -- when it is missing, can't be read or fails
    validate_new_names. Never raises."""
    path = Path(path) if path else NEW_NAMES_PATH
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.info("Car map: no new names at %s; a map may use today's names only", path)
        return None
    except (OSError, ValueError) as exc:
        logger.warning("Car map: the new names at %s can't be read (%s)", path, type(exc).__name__)
        return None
    try:
        errors = validate_new_names(doc)
    except Exception as exc:  # validate_new_names is total; this is in case it ever isn't
        logger.warning("Car map: the new names at %s couldn't be checked (%s)", path, type(exc).__name__)
        return None
    if errors:
        logger.warning("Car map: the new names at %s have %d problem(s), the first: %s", path, len(errors), errors[0])
        return None
    return doc


def new_name_set(doc: dict | None) -> frozenset[str]:
    """The names a map may use from the new-names file, {side} expanded
    (never their twins: a map names no undetermined part)."""
    if not isinstance(doc, dict):
        return frozenset()
    return frozenset(full for e in doc.get("names") or [] if isinstance(e, dict) and isinstance(e.get("name"), str)
                     for full in expand(e["name"]))


@lru_cache(maxsize=1)
def _default_new_names() -> frozenset[str]:
    return new_name_set(load_new_names())


@lru_cache(maxsize=1)
def _default_new_names_doc() -> dict | None:
    return load_new_names()


# ------------------------------------------------------------ the check


def _body_errors(body) -> list[str]:
    if body is None:
        return []
    if not isinstance(body, list) or not body:
        return ["body is a list of body types (or null for every body)"]
    bad = [b for b in body if not isinstance(b, str) or b not in BODY_GATES]
    if bad:
        return [f"body {bad[0]!r}: use any of {', '.join(BODY_GATES)}"]
    if len(set(body)) != len(body):
        return ["body lists a type twice"]
    return []


def _name_errors(name, known: set[str], what: str) -> list[str]:
    if not isinstance(name, str) or not name.strip():
        return [f"{what} is not a name"]
    if name.count(SIDE) > 1 or (SIDE in name and not name.startswith(SIDE + " ")):
        return [f"{what} {name!r}: {SIDE} only once, at the start"]
    return [f"{what} {full!r} is neither a canonical part nor a new name" for full in expand(name) if full not in known]


def _sided(name) -> bool:
    return isinstance(name, str) and SIDE in name


def validate(car_map, names=None) -> list[str]:
    """What is wrong with a car map, as plain lines; [] when nothing is.
    names: the new names it may use (new_name_set), by default those of
    car_map_new_names.json. The rules (K1-design.md, the validator):
      - version (no colon) and format 1; assemblies, relations, chains;
      - ids: assemblies, relations + chains (one space), signs -- each once;
      - every name, {side} expanded, a canonical part (never undetermined,
        never Other) or a new name; a member's "new" says which;
      - a relation's side matches where {side} is (so {side} links are
        symmetric), its parent a member of its own assembly, its child a
        member of some assembly, each parent/child pair once;
      - a part listed in more than one assembly is fitted / body-gated the
        same way in each;
      - signs point at relations from one of their assembly's anchors and
        unlock chains starting there; every chain is unlocked by a sign;
      - a chain is two relations, the second starting where the first
        ends -- never a chain of chains (at most MAX_HOPS) and never back
        to where it began (no cycles);
      - enums, flags, priors 1-5 with a source, reasons at most MAX_REASON
        characters with no 3+ digit number, no practice make anywhere.
    Total: any JSON value anywhere is a line here, never an exception."""
    if not isinstance(car_map, dict):
        return ["not a JSON object"]
    known = _canonical_names() | set(_default_new_names() if names is None else names)
    canonical = _canonical_names()
    errors: list[str] = list(_extra(car_map, "map"))
    version = car_map.get("version")
    if not (_text(version, 20) and ":" not in version):
        errors.append("no version (at most 20 characters, no colon)")
    if car_map.get("format") != FORMAT or isinstance(car_map.get("format"), bool):
        errors.append(f"format is not {FORMAT}")
    if not _str_list(car_map.get("built_from", [])):
        errors.append("built_from is not a list of words")
    assemblies = car_map.get("assemblies")
    relations = car_map.get("relations")
    chains = car_map.get("chains", [])
    if not isinstance(assemblies, list) or not assemblies:
        return errors + ["no list of assemblies"]
    if not isinstance(relations, list):
        return errors + ["no list of relations"]
    if not isinstance(chains, list):
        return errors + ["chains is not a list"]

    # Assemblies, their members and anchors (signs once relations are known).
    asm_by_id: dict[str, dict] = {}
    members_of: dict[str, set[str]] = {}
    anchors_of: dict[str, set[str]] = {}
    facts: dict[str, set] = {}
    for n, asm in enumerate(assemblies):
        where = f"assembly {n}"
        if not isinstance(asm, dict):
            errors.append(f"{where}: not an object")
            continue
        aid = asm.get("id")
        where = f"assembly {n} ({aid!r})"
        if not (isinstance(aid, str) and _SLUG.match(aid)):
            errors.append(f"{where}: no id (lower case, digits and dashes)")
            continue
        if aid in asm_by_id:
            errors.append(f"{where}: the id is used twice")
            continue
        asm_by_id[aid] = asm
        errors += [f"{where}: {p}" for p in _extra(asm, "assembly")]
        if not _text(asm.get("name"), MAX_NAME):
            errors.append(f"{where}: no name (at most {MAX_NAME} characters)")
        if asm.get("area") not in AREAS:
            errors.append(f"{where}: area {asm.get('area')!r}")
        members = asm.get("members")
        here: set[str] = set()
        if not isinstance(members, list) or not members:
            errors.append(f"{where}: no list of members")
            members = []
        for m, member in enumerate(members):
            mwhere = f"{where} member {m}"
            if not isinstance(member, dict):
                errors.append(f"{mwhere}: not an object")
                continue
            errors += [f"{mwhere}: {p}" for p in _extra(member, "member")]
            part = member.get("part")
            problems = _name_errors(part, known, "part")
            errors += [f"{mwhere}: {p}" for p in problems]
            if problems:
                continue
            if part in here:
                errors.append(f"{mwhere}: {part!r} twice in this assembly")
            here.add(part)
            if member.get("role") not in ROLES:
                errors.append(f"{mwhere}: role {member.get('role')!r}")
            for flag in ("hidden", "strip_down"):
                if flag in member and not isinstance(member[flag], bool):
                    errors.append(f"{mwhere}: {flag} is not true or false")
            fitted = member.get("fitted", "always")
            if fitted not in FITTED:
                errors.append(f"{mwhere}: fitted {fitted!r}")
            body_problems = _body_errors(member.get("body"))
            errors += [f"{mwhere}: {p}" for p in body_problems]
            is_new = any(full not in canonical for full in expand(part))
            if "new" in member and member["new"] is not is_new:
                errors.append(f"{mwhere}: new is {member['new']!r} but {part!r} is {'not ' if not is_new else ''}a new name")
            if not body_problems and fitted in FITTED:
                body = member.get("body")
                facts.setdefault(part, set()).add((fitted, tuple(sorted(body)) if isinstance(body, list) else None))
        members_of[aid] = here
        anchors = asm.get("anchors", [])
        if not _str_list(anchors):
            errors.append(f"{where}: anchors is not a list of names")
            anchors = []
        for anchor in anchors:
            if anchor not in here:
                errors.append(f"{where}: anchor {anchor!r} is not one of its members")
        anchors_of[aid] = {a for a in anchors if a in here}
        errors += [f"{where}: {p}" for p in _catalogue_errors(asm.get("catalogue", {}))]
        if "notes" in asm and not _text(asm["notes"], MAX_NOTES):
            errors.append(f"{where}: notes over {MAX_NOTES} characters")
        if not _str_list(asm.get("sources", [])):
            errors.append(f"{where}: sources is not a list")
        elif any(MAKE_WORDS.search(s) for s in asm.get("sources", [])):
            errors.append(f"{where}: a source names a make or model")
    for part, seen in facts.items():
        if len(seen) > 1:
            errors.append(f"part {part!r}: fitted / body differ between its assemblies")
    all_members = set().union(*members_of.values()) if members_of else set()

    # Relations.
    rel_by_id: dict[str, dict] = {}
    pairs: set[tuple[str, str]] = set()
    for n, rel in enumerate(relations):
        where = f"relation {n}"
        if not isinstance(rel, dict):
            errors.append(f"{where}: not an object")
            continue
        rid = rel.get("id")
        where = f"relation {n} ({rid!r})"
        if not (isinstance(rid, str) and _ID.match(rid)):
            errors.append(f"{where}: no id (lower case, digits, > . _ -; no colon)")
            continue
        if rid in rel_by_id:
            errors.append(f"{where}: the id is used twice")
            continue
        rel_by_id[rid] = rel
        errors += [f"{where}: {p}" for p in _extra(rel, "relation")]
        aid = rel.get("assembly")
        if not isinstance(aid, str) or aid not in asm_by_id:
            errors.append(f"{where}: assembly {aid!r} is not an assembly of this map")
        names_ok = True
        for field in ("parent", "child"):
            problems = _name_errors(rel.get(field), known, field)
            errors += [f"{where}: {p}" for p in problems]
            names_ok = names_ok and not problems
        if names_ok:
            parent, child = rel["parent"], rel["child"]
            if parent == child or base(parent) == base(child):
                errors.append(f"{where}: a part linked to itself")
            if (parent, child) in pairs:
                errors.append(f"{where}: the same parent and child twice")
            pairs.add((parent, child))
            want = "same" if _sided(parent) and _sided(child) else "impact" if _sided(child) else "none"
            if rel.get("side") != want:
                errors.append(f"{where}: side {rel.get('side')!r} where the names say {want!r}")
            if isinstance(aid, str) and aid in members_of and parent not in members_of[aid]:
                errors.append(f"{where}: parent {parent!r} is not a member of assembly {aid!r}")
            if child not in all_members:
                errors.append(f"{where}: child {child!r} is a member of no assembly")
        if rel.get("type") not in LINK_TYPES:
            errors.append(f"{where}: type {rel.get('type')!r}")
        if rel.get("side") not in LINK_SIDES:
            errors.append(f"{where}: side {rel.get('side')!r}")
        if rel.get("min_hit") not in HIT_LEVELS:
            errors.append(f"{where}: min_hit {rel.get('min_hit')!r}")
        triggers = rel.get("trigger_tokens", [])
        if not _str_list(triggers) or any(t not in FIRM_CONDITION_TOKENS for t in triggers):
            errors.append(f"{where}: trigger_tokens are not all firm condition words")
        for flag in ("hidden", "strip_down"):
            if flag in rel and not isinstance(rel[flag], bool):
                errors.append(f"{where}: {flag} is not true or false")
        if "area" in rel and rel["area"] not in RELATION_AREAS:
            errors.append(f"{where}: area {rel['area']!r}")
        if rel.get("position", "any") not in POSITIONS:
            errors.append(f"{where}: position {rel.get('position')!r}")
        errors += [f"{where}: {p}" for p in _body_errors(rel.get("body"))]
        errors += [f"{where}: {p}" for p in _prior_errors(rel.get("prior"))]
        errors += [f"{where}: {p}" for p in _reason_errors(rel.get("reason"))]
        sources = rel.get("sources", [])
        if not _str_list(sources):
            errors.append(f"{where}: sources is not a list")
        elif any(MAKE_WORDS.search(s) for s in sources):
            errors.append(f"{where}: a source names a make or model")

    # Chains: two hops, never more, never back.
    chain_by_id: dict[str, dict] = {}
    for n, chain in enumerate(chains):
        where = f"chain {n}"
        if not isinstance(chain, dict):
            errors.append(f"{where}: not an object")
            continue
        cid = chain.get("id")
        where = f"chain {n} ({cid!r})"
        if not (isinstance(cid, str) and _ID.match(cid)):
            errors.append(f"{where}: no id")
            continue
        if cid in chain_by_id or cid in rel_by_id:
            errors.append(f"{where}: the id is used twice (chains and relations share one set)")
            continue
        chain_by_id[cid] = chain
        errors += [f"{where}: {p}" for p in _extra(chain, "chain")]
        first, then = chain.get("first"), chain.get("then")
        for field, ref in (("first", first), ("then", then)):
            if isinstance(ref, str) and ref in chains_ids(chains) and ref not in rel_by_id:
                errors.append(f"{where}: {field} is a chain: at most {MAX_HOPS} hops")
            elif not (isinstance(ref, str) and ref in rel_by_id):
                errors.append(f"{where}: {field} {ref!r} is not a relation of this map")
        if isinstance(first, str) and isinstance(then, str) and first in rel_by_id and then in rel_by_id:
            a, b = rel_by_id[first], rel_by_id[then]
            if first == then:
                errors.append(f"{where}: first and then are one relation")
            elif a.get("child") != b.get("parent"):
                errors.append(f"{where}: then starts at {b.get('parent')!r}, not where first ends ({a.get('child')!r})")
            elif isinstance(a.get("parent"), str) and isinstance(b.get("child"), str) and (
                    b["child"] == a["parent"] or base(b["child"]) == base(a["parent"])):
                errors.append(f"{where}: a cycle -- it ends where it began ({a['parent']!r})")
        if chain.get("min_hit", "heavy") not in HIT_LEVELS:
            errors.append(f"{where}: min_hit {chain.get('min_hit')!r}")
        errors += [f"{where}: {p}" for p in _reason_errors(chain.get("reason"))]

    # Signs: what they point at and unlock exists, from this assembly's anchors.
    sign_ids: set[str] = set()
    unlocked: set[str] = set()
    for aid, asm in asm_by_id.items():
        signs = asm.get("signs", [])
        if not isinstance(signs, list):
            errors.append(f"assembly {aid!r}: signs is not a list")
            continue
        for s, sign in enumerate(signs):
            where = f"assembly {aid!r} sign {s}"
            if not isinstance(sign, dict):
                errors.append(f"{where}: not an object")
                continue
            sid = sign.get("id")
            where = f"assembly {aid!r} sign {s} ({sid!r})"
            errors += [f"{where}: {p}" for p in _extra(sign, "sign")]
            if not (isinstance(sid, str) and _SIGN_ID.match(sid)):
                errors.append(f"{where}: no id")
            elif sid in sign_ids:
                errors.append(f"{where}: the id is used twice")
            else:
                sign_ids.add(sid)
            ask = sign.get("ask")
            if not (_text(ask, MAX_ASK) and ask.rstrip().endswith("?")) or _DIGITS3.search(ask) or MAKE_WORDS.search(ask):
                errors.append(f"{where}: ask is a yes/no question of at most {MAX_ASK} characters, no 3+ digit number")
            if sign.get("level") not in HIT_LEVELS:
                errors.append(f"{where}: level {sign.get('level')!r}")
            if not anchors_of.get(aid):
                errors.append(f"{where}: its assembly has no anchor to walk")
            points, unlocks = sign.get("points_to", []), sign.get("unlocks", [])
            if not _str_list(points) or not _str_list(unlocks):
                errors.append(f"{where}: points_to and unlocks are lists of ids")
                continue
            if not points and not unlocks:
                errors.append(f"{where}: points at nothing and unlocks nothing")
            for rid in points:
                rel = rel_by_id.get(rid)
                if rel is None:
                    errors.append(f"{where}: points_to {rid!r} is not a relation")
                elif not _is_in(rel.get("parent"), anchors_of.get(aid, set())):
                    errors.append(f"{where}: points_to {rid!r} starts at {rel.get('parent')!r}, not an anchor here")
            for cid in unlocks:
                chain = chain_by_id.get(cid)
                first = rel_by_id.get(chain.get("first")) if chain and isinstance(chain.get("first"), str) else None
                if chain is None:
                    errors.append(f"{where}: unlocks {cid!r} is not a chain")
                elif first is not None and not _is_in(first.get("parent"), anchors_of.get(aid, set())):
                    errors.append(f"{where}: unlocks {cid!r}, which starts at {first.get('parent')!r}, not an anchor here")
                else:
                    unlocked.add(cid)
    for cid in chain_by_id:
        if cid not in unlocked:
            errors.append(f"chain {cid!r}: no sign unlocks it (a second hop needs a yes)")
    return errors


def chains_ids(chains) -> set[str]:
    """The ids of a map's chains (for the "chain of chains" line)."""
    return {c["id"] for c in chains if isinstance(c, dict) and isinstance(c.get("id"), str)} if isinstance(chains, list) else set()


def _catalogue_errors(hooks) -> list[str]:
    if not isinstance(hooks, dict):
        return ["catalogue is not an object"]
    errors = [f"catalogue: {p}" for p in _extra(hooks, "catalogue")]
    diagrams = hooks.get("oem_diagrams", [])
    if not _str_list(diagrams) or len(diagrams) > MAX_DIAGRAMS or any(not _text(d, MAX_NAME) for d in diagrams):
        errors.append(f"catalogue.oem_diagrams: at most {MAX_DIAGRAMS} names of at most {MAX_NAME} characters")
    elif any(MAKE_WORDS.search(d) for d in diagrams):
        errors.append("catalogue.oem_diagrams: a diagram name names a make or model")
    numbers = hooks.get("oem_numbers", {})
    if not isinstance(numbers, dict) or any(
            not (isinstance(k, str) and _SCHEME.match(k)) or not _str_list(v) or any(not _NUMBER.match(x) for x in v)
            for k, v in numbers.items()):
        errors.append("catalogue.oem_numbers: {numbering: [3-5 digit diagram numbers]}")
    return errors


def _prior_errors(prior) -> list[str]:
    if prior is None:
        return []
    if not isinstance(prior, dict):
        return ["prior is {rank, source} or null"]
    rank = prior.get("rank")
    errors = [f"prior: {p}" for p in _extra(prior, "prior")]
    if isinstance(rank, bool) or rank not in PRIOR_RANKS:
        errors.append(f"prior rank {rank!r}: 1 (listed most often) to 5")
    source = prior.get("source")
    if not _text(source, 80) or MAKE_WORDS.search(source):
        errors.append("prior needs its source (at most 80 characters, no make or model)")
    return errors


def _reason_errors(reason) -> list[str]:
    if not _text(reason, 10_000):
        return ["no reason"]
    if len(reason) > MAX_REASON or _DIGITS3.search(reason):
        return [f"the reason is over {MAX_REASON} characters or has a 3+ digit number"]
    if MAKE_WORDS.search(reason):
        return ["the reason names a make or model"]
    return []


def load(path: Path | str | None = None, *, names=None) -> dict | None:
    """The car map at `path` (MAP_PATH by default), or None -- and a log
    line -- when the file is missing, can't be read or fails validate.
    Never raises: a bad map is no map, and the linked parts keep L1."""
    path = Path(path) if path else MAP_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.info("Car map: none at %s; the linked parts keep the link table", path)
        return None
    except (OSError, ValueError) as exc:
        logger.warning("Car map: the map at %s can't be read (%s); the linked parts keep the link table", path,
                       type(exc).__name__)
        return None
    try:
        errors = validate(data, names=names)
    except Exception as exc:  # validate is total; this is in case it ever isn't
        logger.warning("Car map: the map at %s couldn't be checked (%s); the linked parts keep the link table", path,
                       type(exc).__name__)
        return None
    if errors:
        logger.warning("Car map: the map at %s has %d problem(s), the first: %s; the linked parts keep the link table",
                       path, len(errors), errors[0])
        return None
    return data


def sha256_lf(path: Path | str | None = None) -> str | None:
    """sha256 of a text file as git keeps it (line endings LF): a Windows
    checkout has CRLF on disk, the server LF, and a freeze line (K5) is
    taken of git's copy. None when it can't be read."""
    try:
        return hashlib.sha256(Path(path or MAP_PATH).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    except OSError:
        return None


# ------------------------------------------------------------ L1 <-> car map


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", base(name).lower()).strip("-")[:40] or "part"


def from_l1(table: dict) -> dict:
    """A link table (L1) as a car map: one assembly per parent, in the
    order the table first names it (its anchor; members: the parent, then
    its children), each link a relation of its parent's assembly with
    every field as it is, in table order. No signs, chains or priors: L1
    has none. links_table() of it is the table again, field for field."""
    raw = [link for link in (table or {}).get("links") or []
           if isinstance(link, dict) and isinstance(link.get("parent"), str) and isinstance(link.get("child"), str)]
    assemblies: dict[str, dict] = {}
    for link in raw:
        parent, child = link.get("parent"), link.get("child")
        asm = assemblies.get(parent)
        if asm is None:
            area = link.get("area") if link.get("area") in AREAS else "side"
            asm = assemblies[parent] = {
                "id": _slug(parent), "name": f"{base(parent)} (L1)", "area": area, "anchors": [parent],
                "members": [{"part": parent, "role": "panel"}], "signs": [], "catalogue": {}, "sources": ["L1"],
            }
        if child not in {m["part"] for m in asm["members"]}:
            asm["members"].append({"part": child, "role": "other", "hidden": bool(link.get("hidden")),
                                   "strip_down": bool(link.get("strip_down"))})
    relations = [{"assembly": assemblies[link.get("parent")]["id"], **copy.deepcopy(link)} for link in raw]
    return {"version": (table or {}).get("version"), "format": FORMAT,
            "built_from": list((table or {}).get("built_from") or []), "from_l1": True,
            "assemblies": list(assemblies.values()), "relations": relations, "chains": []}


def _body_ok(gate, body_type: str | None) -> bool:
    """A body gate passes for an unknown body (L1 shows the link, its
    reason says "tailgate only"), and for a known one it names -- or whose
    class (taxonomy.body_class) it names."""
    if not gate or body_type not in BODY_TYPES or body_type == "Other":
        return True
    return body_type in gate or body_class(body_type) in gate


def _members(car_map: dict) -> tuple[dict, dict]:
    """({(assembly id, part): member}, {part: its first member})."""
    by_pair, first = {}, {}
    for asm in car_map.get("assemblies") or []:
        for member in asm.get("members") or []:
            by_pair[(asm.get("id"), member.get("part"))] = member
            first.setdefault(member.get("part"), member)
    return by_pair, first


def links_table(car_map: dict, *, body_type: str | None = None, names: bool = False) -> dict:
    """The car map as a link table links.build reads (L1's shape,
    LINK_KEYS, relations in map order). A relation goes when a body gate
    -- its own, its child's or its parent's member -- excludes this car's
    body (taxonomy BODY_TYPES; unknown keeps it), or, names False, when
    it names a part today's taxonomy hasn't. hidden / strip_down: the
    relation's own, else the child's member's (in the relation's
    assembly, else its first); area: the relation's, else its assembly's.
    The version is the map's: links put it in each link_rule."""
    by_pair, first = _members(car_map)
    areas = {a.get("id"): a.get("area") for a in car_map.get("assemblies") or []}
    allowed = set(CANONICAL_PARTS)
    out = []
    for rel in car_map.get("relations") or []:
        parent, child, aid = rel["parent"], rel["child"], rel.get("assembly")
        if not names and any(full not in allowed for full in expand(parent) + expand(child)):
            continue
        child_member = by_pair.get((aid, child)) or first.get(child) or {}
        parent_member = by_pair.get((aid, parent)) or {}
        if not all(_body_ok(g, body_type) for g in (rel.get("body"), child_member.get("body"),
                                                    parent_member.get("body"))):
            continue
        link = {
            "id": rel["id"], "parent": parent, "child": child, "type": rel["type"], "side": rel["side"],
            "min_hit": rel["min_hit"], "trigger_tokens": list(rel.get("trigger_tokens") or []),
            "hidden": rel["hidden"] if "hidden" in rel else bool(child_member.get("hidden", False)),
            "strip_down": rel["strip_down"] if "strip_down" in rel else bool(child_member.get("strip_down", False)),
            "area": rel.get("area") or areas.get(aid), "reason": rel["reason"],
            "sources": list(rel.get("sources") or []),
        }
        out.append({k: link[k] for k in LINK_KEYS})
    return {"version": car_map.get("version"), "built_from": list(car_map.get("built_from") or []), "links": out}


def relation_info(car_map: dict | None, link_rule) -> dict | None:
    """What the Review step's groups need for one linked part (K4): its
    relation's assembly id and name, prior rank, position and its child's
    "if fitted", from link_rule "<version>:<relation id>"; None when the
    rule is not this map's."""
    if not isinstance(car_map, dict) or not isinstance(link_rule, str) or ":" not in link_rule:
        return None
    version, rid = link_rule.split(":", 1)
    if version != car_map.get("version"):
        return None
    rel = next((r for r in car_map.get("relations") or [] if r.get("id") == rid), None)
    if rel is None:
        return None
    asm = next((a for a in car_map.get("assemblies") or [] if a.get("id") == rel.get("assembly")), {})
    by_pair, first = _members(car_map)
    child = by_pair.get((rel.get("assembly"), rel.get("child"))) or first.get(rel.get("child")) or {}
    prior = rel.get("prior") if isinstance(rel.get("prior"), dict) else {}
    return {"assembly": asm.get("id"), "assembly_name": asm.get("name"), "prior": prior.get("rank"),
            "position": rel.get("position", "any"), "fitted": child.get("fitted", "always"), "hop": 1}


def _sided_assembly(asm: dict) -> bool:
    """An assembly that is one per side of the car: every anchor a {side}
    name (the front doors), never the front bumper with its {side} fog
    lamps."""
    anchors = asm.get("anchors") if isinstance(asm, dict) else None
    return bool(anchors) and all(isinstance(a, str) and a.startswith(SIDE + " ") for a in anchors)


def review_info(car_map: dict | None, link_rule) -> dict | None:
    """relation_info plus "sided" (the assembly is one per side: _sided_
    assembly) -- what links.build's `info` reads (K4): the group, its name,
    the prior that orders it, the hop and "if fitted". None as
    relation_info."""
    info = relation_info(car_map, link_rule)
    if info is None:
        return None
    asm = next((a for a in car_map.get("assemblies") or [] if isinstance(a, dict) and a.get("id") == info["assembly"]), {})
    return {**info, "sided": _sided_assembly(asm)}


def chain_review_info(car_map: dict | None, link_rule) -> dict | None:
    """review_info for a second hop's link_rule "<version>:<chain id>"
    (assembly_walk, links._hop2; a chain is not a relation, so
    relation_info knows none): the group of the chain's `first` relation
    -- the anchor's assembly, where its first hop sits -- with hop 2, no
    prior, and "if fitted" from the `then` relation's child (the end
    part). None when the rule is no chain of this map or its first
    relation is unknown."""
    if not isinstance(car_map, dict) or not isinstance(link_rule, str) or ":" not in link_rule:
        return None
    version, cid = link_rule.split(":", 1)
    if version != car_map.get("version"):
        return None
    chain = next((c for c in car_map.get("chains") or [] if isinstance(c, dict) and c.get("id") == cid), None)
    if chain is None:
        return None
    first = review_info(car_map, f"{version}:{chain.get('first')}")
    if first is None:
        return None
    then = relation_info(car_map, f"{version}:{chain.get('then')}") or {}
    return {**first, "prior": None, "position": "any", "fitted": then.get("fitted", "always"), "hop": 2}


def review_lookup(car_map: dict | None):
    """A link_rule -> review_info function over this map, worked out once
    for every relation and chain (a build asks for each of its
    candidates; a second hop's rule names its chain: chain_review_info).
    None when there is no map. Never raises: a bad map gives a lookup that
    knows nothing."""
    if not isinstance(car_map, dict):
        return None
    table: dict[str, dict] = {}
    try:
        version = car_map.get("version")
        for rel in car_map.get("relations") or []:
            if isinstance(rel, dict) and isinstance(rel.get("id"), str):
                rule = f"{version}:{rel['id']}"
                info = review_info(car_map, rule)
                if info is not None:
                    table.setdefault(rule, info)
        for chain in car_map.get("chains") or []:
            if isinstance(chain, dict) and isinstance(chain.get("id"), str):
                rule = f"{version}:{chain['id']}"
                info = chain_review_info(car_map, rule)
                if info is not None:
                    table.setdefault(rule, info)
    except Exception as exc:
        logger.warning("Car map: its relations can't be looked up (%s); the linked parts are not grouped",
                       type(exc).__name__)
        table = {}

    def lookup(link_rule) -> dict | None:
        found = table.get(link_rule) if isinstance(link_rule, str) else None
        return dict(found) if found is not None else None

    return lookup


def _also_words(new_names: dict | None) -> dict[str, str]:
    """{a new name as a map writes it ("{side} Front Bumper Bracket"): its
    also_check words}."""
    out: dict[str, str] = {}
    for entry in (new_names or {}).get("names") or []:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str) and isinstance(entry.get("also_check"), str):
            out.setdefault(entry["name"], entry["also_check"].strip())
    return out


def add_also_check(items: list[dict], car_map: dict | None, levels: dict[str, str], *, body_type: str | None = None,
                   names: bool = False, new_names: dict | None = None) -> list[dict]:
    """In place (and returned): K1-design.md section 2, the names off. Each
    group (an item's "assembly") gets also_check -- the words of the new
    parts its relations name that the compiled table left out ("absorber",
    "side brackets") -- when such a relation's parent is a listed part the
    group hangs off, hit hard enough for it (its min_hit against `levels`,
    links.levels_of; trigger words and positions are not looked at), and
    its body gate passes. Every item of the group carries the same words;
    the Review step shows them once, on the group's header. names True (a
    taxonomy that holds them), no map or no words: nothing changes."""
    if names or not isinstance(car_map, dict) or not items:
        return items
    try:
        words = _also_words(_default_new_names_doc() if new_names is None else new_names)
        if not words:
            return items
        allowed = set(CANONICAL_PARTS)
        by_pair, first = _members(car_map)
        rank = {level: n for n, level in enumerate(HIT_LEVELS)}
        groups: dict[str, list[dict]] = {}
        for it in items:
            if isinstance(it, dict) and isinstance(it.get("assembly"), str) and it["assembly"]:
                groups.setdefault(it["assembly"], []).append(it)
        for group, members in groups.items():
            aid = group.split(".", 1)[0]
            parents = {str(k) for it in members for k in it.get("linked_from") or []}
            found: list[str] = []
            for rel in car_map.get("relations") or []:
                if not isinstance(rel, dict) or rel.get("assembly") != aid:
                    continue
                parent, child = rel.get("parent"), rel.get("child")
                if not (isinstance(parent, str) and isinstance(child, str)):
                    continue
                new = [n for n in (parent, child) if any(full not in allowed for full in expand(n))]
                if not new:
                    continue
                hit = [levels.get(p) for p in expand(parent) if p in parents]
                if not any(lvl in rank and rank[lvl] >= rank.get(rel.get("min_hit"), len(rank)) for lvl in hit):
                    continue
                gates = (rel.get("body"), (by_pair.get((aid, child)) or first.get(child) or {}).get("body"),
                         (by_pair.get((aid, parent)) or {}).get("body"))
                if not all(_body_ok(g, body_type) for g in gates):
                    continue
                found += [words[n] for n in new if words.get(n)]
            found = list(dict.fromkeys(w for w in found if w))
            if found:
                for it in members:
                    it["also_check"] = found
    except Exception as exc:
        logger.warning("Car map: no \"also check\" words this time (%s)", type(exc).__name__)
    return items


# ------------------------------------------------------------ the seam

_UNSET = object()
# The map as the app started with it (None: there is none yet -- K1b
# writes it -- or it failed the check).
MAP = load()
# The frozen car map versions (plans/2026-11-18-car-knowledge/K1-freeze.md,
# written by the lead before any scoring): version -> sha256 of git's copy
# (LF). A run or replay on a map says whether it is that version's file.
FROZEN = {"K1.0": "21298e9ef6ced02a8c8b24fb8222f74e104cc79f4d7f5281623ae38719ee39ba"}


def frozen_as(version, sha) -> bool | None:
    """True: `sha` is the frozen file of `version`; False: that version is
    frozen but this is another file (a changed map under a frozen name);
    None: no freeze line for that version (L1 converted, a test map)."""
    want = FROZEN.get(version) if isinstance(version, str) else None
    return None if want is None else sha == want


def table_for_links(l1_table: dict | None, *, mode: str | None = None, car_map=_UNSET, body_type: str | None = None,
                    names: bool = False) -> tuple[dict | None, dict | None]:
    """(the table to build the shown linked parts from, the table to build
    the shadow ones from -- None for none), by settings.intake_car_map
    (or `mode`): "off" -> (l1_table itself, None): today, untouched;
    "shadow" -> (l1_table, the map's table); "on" -> (the map's table,
    None). The map's table is links_table(car_map) when it passes
    links.validate_table; otherwise none -- "on" then keeps l1_table, with
    a log line. Never raises."""
    mode = mode if mode is not None else getattr(settings, "intake_car_map", "off")
    if mode not in ("shadow", "on"):
        return l1_table, None
    cm = MAP if car_map is _UNSET else car_map
    compiled = None
    if isinstance(cm, dict):
        try:
            compiled = links_table(cm, body_type=body_type, names=names)
            problems = links.validate_table(compiled)
        except Exception as exc:
            problems = [type(exc).__name__]
        if problems:
            logger.warning("Car map: its link table fails the check (%s); the linked parts keep L1", problems[0])
            compiled = None
    if mode == "shadow":
        return l1_table, compiled
    if compiled is None:
        logger.warning("Car map: intake_car_map is on but there is no usable map; the linked parts keep L1")
        return l1_table, None
    return compiled, None


@dataclass(frozen=True)
class LinkTables:
    """What the linked parts are built from for one case (K4, the seam
    wired): the table shown and the car map's facts for it (None: L1, no
    group fields), the shadow table and its facts (None: no shadow), and
    the body the gates went by."""
    shown: dict | None
    shown_info: object = None
    shadow: dict | None = None
    shadow_info: object = None
    body_type: str | None = None
    car_map: dict | None = None


def link_tables(l1_table: dict | None, vehicle: dict | None = None, *, mode: str | None = None,
                car_map=_UNSET) -> LinkTables:
    """table_for_links for one case's vehicle, with each table's facts
    (review_lookup) when it is the map's. The body gate goes by the make
    and model only (taxonomy.body_type_for without the reading's own guess,
    K1-design.md 8.2: on a guess the link stays). Never raises: anything
    wrong gives L1 alone, with a log line."""
    mode = mode if mode is not None else getattr(settings, "intake_car_map", "off")
    if mode not in ("shadow", "on"):
        return LinkTables(shown=l1_table)
    try:
        cm = MAP if car_map is _UNSET else car_map
        v = vehicle if isinstance(vehicle, dict) else {}
        body = body_type_for(v.get("make"), v.get("model"), None)
        shown, shadow = table_for_links(l1_table, mode=mode, car_map=cm, body_type=body)
        lookup = review_lookup(cm) if (shown is not l1_table or shadow is not None) else None
        return LinkTables(shown=shown, shown_info=lookup if shown is not l1_table else None, shadow=shadow,
                          shadow_info=lookup if shadow is not None else None, body_type=body,
                          car_map=cm if isinstance(cm, dict) else None)
    except Exception as exc:
        logger.warning("Car map: no tables this time (%s); the linked parts keep L1", type(exc).__name__)
        return LinkTables(shown=l1_table)
