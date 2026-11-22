"""The assembly walk (plans/2026-11-18-car-knowledge/K1-design.md 5a):
short yes/no signs about each damaged anchor panel, answered inside the
zone reads it is already in (+0 calls), and the code that turns them into
the linked parts' hit level, position gates, bucket lifts and a second hop
(0 calls).

settings.intake_assembly_walk ("off" by default) switches the zone side;
intake_walk_raise ("one") and intake_link_hops (1) tune the code side. Off,
nothing here runs: every zone request and every linked part is today's.

The zone side (ai/zone_read.py calls it):
  - zone_spec: the panels a zone may walk -- the car map's anchors this
    zone's checklist offers, as the checklist names them (a side zone's
    without their side) -- each with its assemblies' sign ids;
  - prompt_text / schema_field: one paragraph and a line per panel; the
    `assembly_walk` field after `damage`;
  - parse_zone: one zone's answer, kept only for the panels it listed as
    damaged, the side the zone saw (never the walk's), the first answer of
    each sign, a "yes" without a photo sent as cannot_see;
  - merge_all: every zone's into ai_draft["assembly_walk"] = {panel:
    {hit_at, signs: {id: {answer, photo, zone}}}} -- yes beats no beats
    cannot_see; centre against corner is cannot_see; whole wins.

The code side (intake/links.py calls it, through a Walk): raise_levels
(signs never lower a level; capped one step above the words, or "full"),
position_ok (a corner relation fails on a centre hit and back; whole,
cannot_see or no walk pass), lift (a yes sign pointing at a strip-down
relation makes it likely, recorded in signs_yes) and, for links' own
second hop, chains / unlocked.

Pure: no database, no Gemini. links imports this module, so it never
imports the car map module at its top (car_map imports links): only
index_for does, lazily, to check a map. Every reader is total: junk in a
map or a stored walk is no walk, never an exception.
"""
import logging
from dataclasses import dataclass, field, replace

from app.config import HIT_LEVELS
from app.reports.taxonomy import UNDETERMINED_SUFFIX, split_side, unsided_part

logger = logging.getLogger(__name__)

# What the walk may answer (car_map.HIT_AT / SIGN_ANSWERS, the same values:
# scripts/test_assembly_walk.py checks they stay so). hit_at never names a
# side, nor which corner.
HIT_AT = ("centre", "corner", "whole", "cannot_see")
ANSWERS = ("yes", "no", "cannot_see")
RAISES = ("one", "full")
# Second-hop items per case (K1 5a).
MAX_HOP2 = 4
SIDE = "{side}"
CANNOT_SEE = "cannot_see"
_ANSWER_RANK = {"yes": 2, "no": 1, CANNOT_SEE: 0}
_RANK = {level: n for n, level in enumerate(HIT_LEVELS)}

WALK_INTRO = (
    "ASSEMBLY WALK: for each panel below that you listed in 'damage', add one 'assembly_walk' entry. hit_at: where "
    "on that panel the damage is -- 'centre', 'corner' (at an end or a corner of the panel), 'whole' (most of it) "
    "or 'cannot_see'. signs: each of that panel's signs by its id -- 'yes' only when a photo shows it, with that "
    "photo's label; 'no' when the photos show it is not so; otherwise 'cannot_see', photo null. Never a side."
)


# ------------------------------------------------------------ the map


@dataclass(frozen=True)
class MapIndex:
    """What the walk needs of a car map, read once (index)."""
    version: str
    # sign id -> {"ask", "level", "points_to", "unlocks", "assembly"}
    signs: dict
    # assembly id -> its sign ids, map order
    asm_signs: dict
    # (assembly id, anchor map name), map order: anchors of assemblies with signs
    anchors: tuple
    # a listed part's name (both sides, and the "(side undetermined)" name) ->
    # the assemblies it is an anchor of
    by_part: dict
    # relation id -> "any" | "centre" | "corner"
    positions: dict
    # chains: ({"id", "first", "then", "min_hit", "reason"}, ...), map order
    chains: tuple


def _expand(name: str) -> list[str]:
    return [name.replace(SIDE, side) for side in ("Left", "Right")] if SIDE in name else [name]


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _strs(value) -> list[str]:
    return [v for v in _list(value) if isinstance(v, str)]


def index(cm) -> MapIndex | None:
    """A car map's walk index, or None when it has no sign to walk (none
    at all, or not a map). Total: a junk entry is left out."""
    if not isinstance(cm, dict):
        return None
    version = cm.get("version")
    if not isinstance(version, str) or not version.strip():
        return None
    signs, asm_signs, anchors, by_part = {}, {}, [], {}
    for asm in _list(cm.get("assemblies")):
        if not isinstance(asm, dict) or not isinstance(asm.get("id"), str):
            continue
        aid = asm["id"]
        own = []
        for sign in _list(asm.get("signs")):
            if not isinstance(sign, dict):
                continue
            sid, ask, level = sign.get("id"), sign.get("ask"), sign.get("level")
            if not (isinstance(sid, str) and sid and isinstance(ask, str) and ask.strip()) or sid in signs:
                continue
            signs[sid] = {"ask": " ".join(ask.split()), "level": level if level in _RANK else None,
                          "points_to": tuple(_strs(sign.get("points_to"))), "unlocks": tuple(_strs(sign.get("unlocks"))),
                          "assembly": aid}
            own.append(sid)
        if not own:
            continue
        asm_signs[aid] = tuple(own)
        for anchor in _strs(asm.get("anchors")):
            anchors.append((aid, anchor))
            for full in _expand(anchor):
                by_part.setdefault(full, set()).add(aid)
                twin = unsided_part(full)
                if twin and twin != full:
                    by_part.setdefault(twin, set()).add(aid)
    if not signs:
        return None
    positions = {}
    for rel in _list(cm.get("relations")):
        if isinstance(rel, dict) and isinstance(rel.get("id"), str):
            pos = rel.get("position", "any")
            positions[rel["id"]] = pos if pos in ("centre", "corner") else "any"
    chains = []
    for chain in _list(cm.get("chains")):
        if isinstance(chain, dict) and all(isinstance(chain.get(k), str) for k in ("id", "first", "then", "reason")):
            level = chain.get("min_hit", "heavy")
            chains.append({"id": chain["id"], "first": chain["first"], "then": chain["then"],
                           "min_hit": level if level in _RANK else "heavy", "reason": chain["reason"]})
    return MapIndex(version=version, signs=signs, asm_signs=asm_signs, anchors=tuple(anchors),
                    by_part={k: frozenset(v) for k, v in by_part.items()}, positions=positions, chains=tuple(chains))


_CHECKED: list = [None, None]  # [the map last checked, its index]


def index_for(cm) -> MapIndex | None:
    """The walk index of a car map the app may use: it must pass
    car_map.validate and have a sign; else None, with a log line (the walk
    is then skipped, never an error). The last map checked is remembered
    (by identity), so a reading checks it once."""
    if cm is None:
        logger.info("Assembly walk: no car map loaded; the walk is skipped")
        return None
    if _CHECKED[0] is cm:
        return _CHECKED[1]
    found = None
    try:
        from app.intake import car_map

        problems = car_map.validate(cm)
        if problems:
            logger.warning("Assembly walk: the car map fails its check (%s); the walk is skipped", problems[0])
        else:
            found = index(cm)
            if found is None:
                logger.info("Assembly walk: the car map has no signs; the walk is skipped")
    except Exception as exc:  # validate and index are total; this is in case they ever aren't
        logger.warning("Assembly walk: couldn't read the car map (%s); the walk is skipped", type(exc).__name__)
        found = None
    _CHECKED[:] = [cm, found]
    return found


# ------------------------------------------------------------ one zone's request


@dataclass(frozen=True)
class ZoneSpec:
    """The panels one zone call may walk, as its checklist names them."""
    panels: tuple
    # panel (as offered) -> its sign ids
    signs: dict
    # every sign id offered, the schema's enum
    sign_ids: tuple
    lines: tuple


def _offered(full: str, kind: str, names: set[str]) -> str | None:
    """A canonical anchor name as a zone's checklist names it: an end zone
    as it is; a side zone without its side ("Front Door"); a close-up zone
    by its "(side undetermined)" name. None when the zone doesn't offer it."""
    if kind == "side":
        name = split_side(full)[0] if split_side(full)[1] else full
    elif kind == "close":
        name = unsided_part(full) or full
    else:
        name = full
    return name if name in names else None


def zone_spec(walk: MapIndex | None, kind: str, checklist: list[str]) -> ZoneSpec | None:
    """What one zone call walks (kind: "end" | "side" | "close", checklist:
    zone_read.checklist(zone)): each anchor the checklist offers, once, with
    the signs of every assembly it anchors, in map order. None when the
    zone offers no anchor with a sign: that call is asked nothing new."""
    if not isinstance(walk, MapIndex):
        return None
    names = set(checklist or [])
    signs: dict[str, list[str]] = {}
    for aid, anchor in walk.anchors:
        for full in _expand(anchor):
            panel = _offered(full, kind, names)
            if panel is None:
                continue
            held = signs.setdefault(panel, [])
            held.extend(s for s in walk.asm_signs.get(aid, ()) if s not in held)
    if not signs:
        return None
    # Panels with the same signs (a lamp's two sides) share one line.
    lines, grouped = [], {}
    for panel, ids in signs.items():
        grouped.setdefault(tuple(ids), []).append(panel)
    for ids, panels in grouped.items():
        asks = " | ".join(f"{sid} {walk.signs[sid]['ask']}" for sid in ids)
        lines.append(f"{', '.join(panels)}: {asks}")
    sign_ids = tuple(dict.fromkeys(s for ids in signs.values() for s in ids))
    return ZoneSpec(panels=tuple(signs), signs={p: tuple(ids) for p, ids in signs.items()}, sign_ids=sign_ids,
                    lines=tuple(lines))


def prompt_text(spec: ZoneSpec | None) -> str:
    """The paragraph and the panels' lines, for zone_read.system_prompt
    (after the packs, before the closing line); "" without a spec."""
    if spec is None:
        return ""
    return "\n".join((WALK_INTRO,) + spec.lines) + "\n"


def schema_field(spec: ZoneSpec, labels: list[str]) -> dict:
    """The `assembly_walk` field (K1 5a): panel, hit_at, then each sign's
    answer and the photo that shows a yes. No minItems / maxItems."""
    return {"type": "array", "items": {"type": "object", "properties": {
        "panel": {"type": "string", "enum": list(spec.panels)},
        "hit_at": {"type": "string", "enum": list(HIT_AT)},
        "signs": {"type": "array", "items": {"type": "object", "properties": {
            "sign": {"type": "string", "enum": list(spec.sign_ids)},
            "answer": {"type": "string", "enum": list(ANSWERS)},
            "photo": {"anyOf": [{"type": "string", "enum": list(labels)}, {"type": "null"}]},
        }, "required": ["sign", "answer", "photo"]}},
    }, "required": ["panel", "hit_at", "signs"]}}


# ------------------------------------------------------------ one zone's answer


def _word(value, allowed) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in allowed else None


def parse_zone(spec: ZoneSpec | None, raw, canonical, damaged, label) -> dict:
    """One zone's assembly_walk answer as {panel: {"hit_at", "signs": {id:
    {"answer", "photo"}}}}, photos as labels. canonical(name): the zone's
    zone_read.canonical (the side the zone saw, never the walk's);
    damaged: the canonical parts this zone listed in `damage`; label(value):
    a photo label this call sent, or None.
      - only for a panel this zone listed as damaged (a sided panel whose
        damage item lost its side, Z5, goes under that undetermined name);
      - a sign only of that panel's assemblies, its first answer;
      - "yes" with no photo that was sent is cannot_see;
      - hit_at one of HIT_AT, else cannot_see.
    Total: junk is left out, never an error."""
    out: dict = {}
    if spec is None or not isinstance(raw, list):
        return out
    damaged = set(damaged or ())
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        offered = str(entry.get("panel") or "").strip()
        if offered not in spec.signs:
            continue
        panel = canonical(offered)
        if not panel:
            continue
        if panel not in damaged:
            twin = unsided_part(panel)
            if not (twin and twin != panel and twin in damaged):
                continue
            panel = twin
        rec = out.setdefault(panel, {"hit_at": _word(entry.get("hit_at"), HIT_AT) or CANNOT_SEE, "signs": {}})
        allowed = spec.signs[offered]
        for sign in _list(entry.get("signs")):
            if not isinstance(sign, dict):
                continue
            sid = str(sign.get("sign") or "").strip()
            answer = _word(sign.get("answer"), ANSWERS)
            if sid not in allowed or answer is None or sid in rec["signs"]:
                continue
            photo = label(sign.get("photo"))
            if answer == "yes" and not photo:
                answer = CANNOT_SEE
            rec["signs"][sid] = {"answer": answer, "photo": photo}
    return out


def with_photo_ids(zone_walk: dict, label_to_id: dict) -> dict:
    """A parsed zone walk with each photo as "@<photo id>" (as the draft's
    items name photos); a label not sent is no photo, and a yes without
    one is cannot_see."""
    out = {}
    for panel, rec in (zone_walk or {}).items():
        signs = {}
        for sid, got in rec.get("signs", {}).items():
            pid = label_to_id.get(got.get("photo") or "")
            answer = got.get("answer")
            signs[sid] = {"answer": CANNOT_SEE if answer == "yes" and not pid else answer,
                          "photo": f"@{pid}" if pid else None}
        out[panel] = {"hit_at": rec.get("hit_at", CANNOT_SEE), "signs": signs}
    return out


def _hit_at(seen: list[str]) -> str:
    """Several reads' hit_at as one: whole wins; centre against corner is
    cannot_see; else the one place seen."""
    places = set(seen)
    if "whole" in places:
        return "whole"
    if {"centre", "corner"} <= places:
        return CANNOT_SEE
    return next((p for p in ("centre", "corner") if p in places), CANNOT_SEE)


def merge_all(walks: list[tuple[str, dict]]) -> dict:
    """Every zone's walk, (read name, walk) in read order, as
    ai_draft["assembly_walk"]: per panel, each sign's best answer (yes > no
    > cannot_see; the first read's on a tie) with its photo and read, and
    one hit_at (_hit_at)."""
    out: dict = {}
    places: dict[str, list[str]] = {}
    for read, walk in walks:
        for panel, rec in (walk or {}).items():
            held = out.setdefault(panel, {"hit_at": CANNOT_SEE, "signs": {}})
            places.setdefault(panel, []).append(rec.get("hit_at", CANNOT_SEE))
            for sid, got in (rec.get("signs") or {}).items():
                old = held["signs"].get(sid)
                if old is None or _ANSWER_RANK.get(got.get("answer"), -1) > _ANSWER_RANK.get(old["answer"], -1):
                    held["signs"][sid] = {"answer": got.get("answer"), "photo": got.get("photo"),
                                          "zone": got.get("zone") or read}
    for panel, seen in places.items():
        out[panel]["hit_at"] = _hit_at(seen)
    return out


def log_entry(zone_walk: dict) -> dict:
    """A zone's walk, short, for its run-log entry: {panel: {hit_at, yes}}."""
    return {panel: {"hit_at": rec.get("hit_at"),
                    "yes": [sid for sid, got in rec.get("signs", {}).items() if got.get("answer") == "yes"]}
            for panel, rec in (zone_walk or {}).items()}


# ------------------------------------------------------------ the code side (links)


@dataclass(frozen=True)
class Walk:
    """What links.build gets with the walk on: the stored answers
    (ai_draft["assembly_walk"]), the map's index, and the knobs
    (settings.intake_walk_raise, intake_link_hops). hop2_max: the second-
    hop items still allowed in this case (for_parent takes off the open
    ones)."""
    answers: dict
    index: MapIndex
    raise_mode: str = "one"
    hops: int = 1
    hop2_max: int = MAX_HOP2
    # caches: part -> its walk record (or None)
    _records: dict = field(default_factory=dict, compare=False, repr=False)


def clean_answers(raw) -> dict:
    """A stored ai_draft["assembly_walk"] as the shape the code reads;
    anything else is left out ({} for junk)."""
    out = {}
    for panel, rec in raw.items() if isinstance(raw, dict) else ():
        if not isinstance(panel, str) or not isinstance(rec, dict):
            continue
        signs = {}
        for sid, got in rec.get("signs").items() if isinstance(rec.get("signs"), dict) else ():
            if isinstance(sid, str) and isinstance(got, dict) and got.get("answer") in ANSWERS:
                photo = got.get("photo") if isinstance(got.get("photo"), str) and got.get("photo") else None
                answer = CANNOT_SEE if got["answer"] == "yes" and not photo else got["answer"]
                signs[sid] = {"answer": answer, "photo": photo, "zone": got.get("zone")}
        out[panel] = {"hit_at": rec.get("hit_at") if rec.get("hit_at") in HIT_AT else CANNOT_SEE, "signs": signs}
    return out


def for_links(answers, cm_index: MapIndex | None, raise_mode: str = "one", hops: int = 1) -> Walk | None:
    """A Walk for links.build, or None (the linked parts as today) when
    there is no map index."""
    if not isinstance(cm_index, MapIndex):
        return None
    return Walk(answers=clean_answers(answers), index=cm_index, raise_mode=raise_mode if raise_mode in RAISES else "one",
                hops=hops if hops in (1, 2) else 1)


def from_settings(ai_draft, settings) -> Walk | None:
    """The Walk second_look gives links.build: only with
    settings.intake_assembly_walk "on", a stored walk and a usable map
    (car_map.MAP, index_for); else None."""
    if getattr(settings, "intake_assembly_walk", "off") != "on" or not isinstance(ai_draft, dict):
        return None
    answers = ai_draft.get("assembly_walk")
    if not isinstance(answers, dict):
        return None
    from app.intake import car_map

    return for_links(answers, index_for(car_map.MAP), getattr(settings, "intake_walk_raise", "one"),
                     getattr(settings, "intake_link_hops", 1))


def with_room(walk: Walk | None, open_hop2: int) -> Walk | None:
    """for_parent: the walk with the case's open second-hop items taken off
    its MAX_HOP2."""
    return replace(walk, hop2_max=max(0, MAX_HOP2 - open_hop2)) if isinstance(walk, Walk) else walk


def _merged(recs: list[dict]) -> dict:
    """Several panels' records as one, by merge_all's rules."""
    return merge_all([("", {"panel": rec}) for rec in recs])["panel"]


def record(walk: Walk, part: str) -> dict | None:
    """The walk of a listed part: its own panel's, else its "(side
    undetermined)" twin's -- or, for an undetermined part, its sided
    panels' merged. None when no zone walked it."""
    if part in walk._records:
        return walk._records[part]
    answers = walk.answers
    if part in answers:
        found = answers[part]
    elif part.endswith(UNDETERMINED_SUFFIX):
        recs = [rec for p, rec in answers.items() if p != part and unsided_part(p) == part]
        found = _merged(recs) if recs else None
    else:
        twin = unsided_part(part)
        found = answers.get(twin) if twin and twin != part else None
    walk._records[part] = found
    return found


def yes_signs(walk: Walk, part: str) -> list[tuple[str, dict, str | None]]:
    """(sign id, the map's sign, photo) for each sign answered yes about
    this listed part, of an assembly it anchors, in answer order."""
    rec = record(walk, part)
    if not rec:
        return []
    mine = walk.index.by_part.get(part, frozenset())
    out = []
    for sid, got in rec.get("signs", {}).items():
        sign = walk.index.signs.get(sid)
        if got.get("answer") == "yes" and sign is not None and sign["assembly"] in mine:
            out.append((sid, sign, got.get("photo")))
    return out


def raised(walk: Walk, part: str, level: str) -> str:
    """Step S2: the hit level of a listed part with its yes signs -- the
    highest of `level` (the words', or unknown_hit) and each yes sign's,
    capped one step above `level` ("one") or not ("full"). Never lower."""
    if level not in _RANK:
        return level
    top = max((_RANK[s["level"]] for _, s, _ in yes_signs(walk, part) if s.get("level") in _RANK), default=-1)
    if top <= _RANK[level]:
        return level
    if walk.raise_mode == "one":
        top = min(top, _RANK[level] + 1)
    return HIT_LEVELS[top]


def raise_levels(walk: Walk | None, listed: list[dict], levels: list[str]) -> list[str]:
    """raised for each listed part (links._context, before the end-crush
    bonus); the levels as they are without a walk."""
    if not isinstance(walk, Walk):
        return levels
    return [raised(walk, str(it.get("part") or ""), lvl) for it, lvl in zip(listed, levels)]


def _same_map(walk: Walk | None, version: str) -> bool:
    return isinstance(walk, Walk) and walk.index.version == version


def position_ok(walk: Walk | None, part: str, link_id, version: str) -> bool:
    """Step S3a: a corner relation fails on a centre hit, a centre one on a
    corner hit; "any", whole, cannot_see, no walk of the part, or a table
    that isn't the map's (another version) all pass. Only ever removes."""
    if not _same_map(walk, version):
        return True
    position = walk.index.positions.get(link_id, "any")
    if position == "any":
        return True
    rec = record(walk, part)
    hit_at = rec.get("hit_at") if rec else None
    return not ((position == "corner" and hit_at == "centre") or (position == "centre" and hit_at == "corner"))


def signs_yes(walk: Walk, part: str, pick) -> list[dict]:
    """[{id, ask, photo}] of the yes signs about `part` that pick(sign)."""
    return [{"id": sid, "ask": sign["ask"], "photo": photo} for sid, sign, photo in yes_signs(walk, part) if pick(sign)]


def lift(walk: Walk | None, part: str, link_id, version: str, bucket: str) -> tuple[str, list[dict]]:
    """Step S3b: (the bucket, signs_yes) of one relation from `part`. Each
    yes sign pointing at the relation is recorded; with one, strip_down
    becomes likely (seen stays seen). A "no" changes nothing."""
    if not _same_map(walk, version):
        return bucket, []
    found = signs_yes(walk, part, lambda sign: link_id in sign["points_to"])
    return ("likely" if found and bucket == "strip_down" else bucket), found


def chains(walk: Walk | None, version: str) -> tuple:
    """The map's chains for the second hop: only with intake_link_hops 2
    and the table the map's own."""
    if not _same_map(walk, version) or walk.hops < 2 or walk.hop2_max <= 0:
        return ()
    return walk.index.chains


def unlocked(walk: Walk, part: str, chain_id: str) -> list[dict]:
    """The yes signs about `part` that unlock the chain ([]: it stays shut)."""
    return signs_yes(walk, part, lambda sign: chain_id in sign["unlocks"])
