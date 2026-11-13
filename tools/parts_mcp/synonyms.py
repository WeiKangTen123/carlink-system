"""Synonym table and name scoring: damage wording -> catalogue diagram names.

Brands name the same panel differently ("tailgate", "trunk lid", "back door",
Italian "portellone"). Each concept lists:

- `phrases`: words that mean the area. A query containing one of them selects
  the concept, and every phrase is then looked for in diagram names.
- `also`: diagram names that usually hold parts of the area without naming it
  (Honda files the stabilizer under "KNUCKLE", the spare-tyre well under
  "FLOOR / INNER PANELS"). They are looked for, a little lower, but never
  select the concept. Earlier entries win ties.
- `group_hints`: words in the catalogue *group* names that usually hold the
  area, so only likely groups are opened.

`score` rates one diagram name. Only the diagram's own name counts; the group
it sits in decides which groups are opened and breaks ties (see
`service.find_diagrams`).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Sequence


@dataclass(frozen=True)
class Concept:
    name: str
    phrases: tuple[str, ...]
    group_hints: tuple[str, ...]
    also: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rating:
    score: float  # 0..1
    why: str
    rank: int  # index of the phrase that matched in `expand(query)`; lower = closer to the query


# "upholstery": Honda's catalogue files the bumpers under UPHOLSTERY.
_BODY = ("body", "bodywork", "body equipment", "vehicle trim", "exterior trim", "carrozzeria",
         "equipaggiamento vettura", "rivestimento vettura", "upholstery")
_LIGHTS = ("lighting", "lights", "electrical", "illuminazione", "impianto elettrico", "body")
_CHASSIS = ("suspension", "front axle", "axle", "chassis", "steering", "sospensione", "assale")
_GLASS = _BODY + ("glass", "glazing", "vetri")

CONCEPTS: tuple[Concept, ...] = (
    # BMW / MINI call the bumper covers "Trim panel, rear" / "Trim panel, front"; some Honda
    # catalogues have one "BUMPERS" diagram for both ends (a direction clash still demotes
    # a diagram named for the other end).
    Concept("rear bumper", ("rear bumper", "bumper rear", "rear bumper cover", "rear fascia",
                            "paraurti posteriore", "trim panel rear", "bumpers"), _BODY),
    Concept("front bumper", ("front bumper", "bumper front", "front bumper cover", "front fascia",
                             "paraurti anteriore", "trim panel front", "bumpers"), _BODY),
    # The beam (reinforcement) behind the cover is drawn on the bumper diagram (Honda "REAR
    # BUMPER", "BUMPERS ('08)"). Its own concept keeps "rear bumper beam" from selecting
    # "rear beam" of the rear suspension (REAR AXLE, REAR SHOCK ABSORBER).
    Concept("rear bumper beam", ("rear bumper beam", "bumper beam rear", "rear bumper reinforcement",
                                 "rear bumper support", "rear bumper carrier", "rear impact beam"), _BODY,
            also=("rear bumper", "bumper rear", "trim panel rear", "paraurti posteriore", "bumpers")),
    Concept("front bumper beam", ("front bumper beam", "bumper beam front", "front bumper reinforcement",
                                  "front bumper support", "front bumper carrier", "front impact beam"), _BODY,
            also=("front bumper", "bumper front", "trim panel front", "paraurti anteriore", "bumpers")),
    # The badges sit on the tailgate, so its emblem diagram comes along.
    Concept("tailgate", ("tailgate", "tail gate", "trunk lid", "boot lid", "back door", "back door panel",
                         "tailgate panel", "luggage compartment lid", "luggage compartment door", "deck lid",
                         "rear lid", "liftgate", "lift gate", "hatchback door", "portellone",
                         "coperchio bagagliaio", "cofano posteriore"), _BODY, also=("emblem",)),
    Concept("bonnet", ("bonnet", "hood", "engine hood", "front lid", "cofano motore",
                       "cofano anteriore"), _BODY),
    Concept("front fender", ("front fender", "fender", "front wing", "wing panel", "side panel front",
                             "parafango"), _BODY, also=("inner fender", "fender liner", "wheel arch")),
    # Honda: "OUTER PANELS / REAR PANEL"; Mazda "SIDE PANELS"; Nissan "BODY SIDE PANEL; REAR".
    Concept("rear quarter", ("quarter panel", "rear quarter", "rear fender", "side panel rear",
                             "rear wing", "fiancata posteriore", "rear quarter panel", "outer panel"),
            _BODY, also=("side panel", "body side panel", "mudguard", "wheel arch")),
    # Glass, regulator, lock and lining are separate diagrams; the shell ("door panel") comes first.
    Concept("door", ("front door", "rear door", "door shell", "door panel", "door skin",
                     "porta anteriore", "porta posteriore"), _BODY,
            also=("door glass", "door regulator", "window regulator", "door window", "door lock",
                  "door lining", "door trim", "door molding")),
    Concept("side sill", ("side sill", "rocker panel", "side skirt", "sill panel", "longherone",
                          "side sill garnish"), _BODY),
    Concept("grille", ("grille", "radiator grille", "kidney grille", "front grille", "griglia",
                       "mascherina"), _BODY, also=("emblem",)),
    Concept("roof", ("roof", "roof panel", "tetto"), _BODY),
    Concept("windscreen", ("windscreen", "windshield", "front window glass", "front glass",
                           "parabrezza"), _GLASS),
    Concept("rear window", ("rear window", "back glass", "rear windshield", "lunotto", "back window",
                            "back window glass", "rear glass", "rear screen"), _GLASS),
    Concept("side glass", ("quarter glass", "quarter window", "side window", "side glass", "fixed glass"),
            _GLASS),
    Concept("headlamp", ("headlamp", "headlight", "head lamp", "head light", "front light",
                         "proiettore", "faro anteriore"), _LIGHTS),
    Concept("tail lamp", ("tail lamp", "taillight", "tail light", "rear lamp", "rear light",
                          "rear combination lamp", "combination lamp rear", "fanale posteriore",
                          "luce posteriore"), _LIGHTS),
    Concept("license lamp", ("license light", "license plate light", "number plate light", "plate light",
                             "registration plate light", "targa"), _LIGHTS),
    Concept("fog lamp", ("fog lamp", "fog light", "foglamp", "fendinebbia"), _LIGHTS),
    Concept("mirror", ("outside mirror", "door mirror", "wing mirror", "exterior mirror",
                       "side mirror", "retrovisore esterno", "specchio retrovisore"),
            _BODY + ("mirror", "retrovisore")),
    Concept("radiator", ("radiator", "engine cooling", "cooling system", "condenser",
                         "radiatore", "raffreddamento"), ("engine", "cooling", "radiator",
                                                           "motore", "raffreddamento", "heater")),
    Concept("airbag", ("airbag", "air bag", "srs"), ("airbag", "restraint", "body equipment",
                                                     "electrical", "safety", "equipaggiamento")),
    Concept("wheel", ("wheel", "rim", "disc wheel", "alloy wheel", "cerchio", "tire", "wheel disk",
                      "wheel rim", "road wheel", "pneumatico"),
            ("wheel", "wheels", "ruote", "tire", "chassis", "suspension")),
    Concept("steering wheel", ("steering wheel", "volante"), ("steering", "sterzo")),
    Concept("exhaust", ("exhaust", "exhaust pipe", "exhaust system", "silencer", "muffler", "tail pipe",
                        "tailpipe", "centre pipe", "center pipe", "marmitta", "scarico"),
            ("exhaust", "exhaust system", "engine", "scarico")),
    # Engine-side; listed so that "exhaust" does not pick an exhaust *manifold* diagram.
    Concept("manifold", ("exhaust manifold", "intake manifold", "manifold", "collettore"),
            ("engine", "motore")),
    # Honda puts the lower arm and stabilizer under "KNUCKLE"; the sub frame and the strut
    # are separate diagrams that often come with a front-suspension repair.
    Concept("front suspension", ("front suspension", "lower arm", "front lower arm", "control arm",
                                 "track control arm", "lower control arm", "wishbone", "suspension arm",
                                 "knuckle", "steering knuckle", "stabilizer", "stabilizer bar",
                                 "anti roll bar", "sway bar", "stabilizer link", "stabilizer spring",
                                 "ball joint", "front axle", "braccio oscillante", "barra stabilizzatrice"),
            _CHASSIS + _BODY, also=("front sub frame", "suspension crossmember", "front crossmember",
                            "front shock absorber", "front strut", "spring strut front")),
    Concept("rear suspension", ("rear suspension", "rear lower arm", "trailing arm", "rear axle",
                                "rear shock absorber", "rear knuckle", "rear stabilizer", "rear beam",
                                "torsion beam", "rear upper arm", "rear control arm"), _CHASSIS),
    Concept("crossmember", ("cross member", "crossmember", "sub frame", "subframe", "front sub frame",
                            "suspension crossmember", "front crossmember", "axle carrier",
                            "front axle carrier", "front axle support"), _CHASSIS + _BODY),
    # Honda: "P.S. GEAR BOX"; Toyota "STEERING GEAR & LINK"; BMW "Steering gear".
    Concept("steering gear", ("steering gear", "steering rack", "steering gearbox", "steering gear box",
                              "steering box", "rack and pinion", "rack pinion", "power steering gear",
                              "p s gear box", "tie rod", "scatola sterzo"),
            ("steering", "chassis", "sterzo", "suspension")),
    # Honda: "FLOOR / INNER PANELS", "REAR TRAY / TRUNK LINING", HR-V "CARGO FLOOR";
    # Toyota "FLOOR PAN & LOWER BACK PANEL"; BMW "Trim panel, trunk floor".
    Concept("rear floor", ("rear floor", "boot floor", "trunk floor", "luggage floor", "cargo floor",
                           "floor pan", "rear floor pan", "floor panel", "rear floor panel",
                           "spare tire panel", "spare tire well", "spare wheel well", "spare wheel pan",
                           "luggage compartment floor"), _BODY,
            also=("floor inner panel", "rear tray", "trunk lining", "luggage compartment mat")),
    Concept("spare tire", ("spare tire", "spare wheel", "jack", "tool kit", "tool tray", "tool box",
                           "spare tire cover", "spare tire lid", "spare wheel carrier"),
            _BODY + ("tool", "wheel"),
            also=("floor inner panel", "rear tray", "trunk lining", "cargo floor")),
    Concept("emblem", ("emblem", "badge", "logo", "lettering", "emblem lettering", "ornament",
                       "name plate", "nameplate", "stemma", "scritta"), _BODY),
    Concept("mudguard", ("mudguard", "mud guard", "mudflap", "mud flap", "splash guard", "paraspruzzi"),
            _BODY),
    Concept("molding", ("molding", "body side molding", "side molding", "door molding", "roof molding",
                        "window molding", "belt molding", "modanatura"), _BODY),
    Concept("rear panel", ("rear panel", "back panel", "rear end panel", "end panel", "lower back panel",
                           "rear body panel", "rear apron"), _BODY),
    # Splash shield / engine under cover: Honda keeps the inner fender under FRONT FENDERS.
    Concept("under cover", ("under cover", "undercover", "engine under cover", "engine undercover",
                            "splash shield", "engine splash shield", "underbody cover", "engine guard",
                            "skid plate", "lower cover", "underbonnet screen", "underfloor coating"),
            _BODY + ("engine",), also=("front fender", "inner fender", "fender liner", "side sill garnish")),
    Concept("wheel arch", ("wheel arch", "wheelarch", "fender liner", "wheel arch liner", "arch liner",
                           "inner fender", "wheel house", "wheelhouse", "wheel arch garnish",
                           "wheel arch protector", "arch garnish", "fender garnish", "over fender",
                           "wheel arch trim", "wheel arch molding", "passaruota"),
            _BODY, also=("front fender", "mudguard", "under cover")),
    # Sound / heat insulation pads (Toyota "ROOF HEADLINING & SILENCER PAD" is not an exhaust part).
    Concept("insulation", ("insulator", "insulation", "insulator pad", "silencer pad", "sound insulation",
                           "heat insulator", "floor insulator", "dash insulator", "isolamento"), _BODY),
    # Toyota "CLEARANCE SONAR", Honda "PARKING SENSOR", BMW "Park Distance Control (PDC)".
    Concept("parking sensor", ("parking sensor", "reverse sensor", "reversing sensor", "back sonar",
                               "clearance sonar", "corner sonar", "sonar", "pdc", "park distance control",
                               "parking aid", "parking assist", "ultrasonic sensor", "backup sensor"),
            ("distance system", "parking", "electrical", "electronic", "body electrical", "body")),
)

# Abbreviations, British spellings and Italian direction words -> one English token.
# ("back" is deliberately not "rear": a Toyota "back door" is a tailgate, not a rear door.)
TOKEN_ALIASES = {
    "fr": "front", "frt": "front", "anteriore": "front", "ant": "front",
    "rr": "rear", "posteriore": "rear",
    "lh": "left", "sinistro": "left", "sinistra": "left", "sx": "left",
    "rh": "right", "destro": "right", "destra": "right", "dx": "right",
    "light": "lamp", "headlight": "headlamp", "taillight": "taillamp",
    "assy": "", "assembly": "",
    "moulding": "molding", "tyre": "tire", "licence": "license", "stabiliser": "stabilizer",
    "disc": "disk", "windscreen": "windshield", "aluminium": "aluminum",
}

STOPWORDS = {"the", "a", "an", "of", "and", "for", "with", "to", "in", "on", "di", "del", "della",
             "e", "per", "con", "il", "la", "kit", "set", "compl", "complete"}

DIRECTIONS = {"front", "rear", "left", "right"}

# Words that say nothing about which part a diagram shows ("... COMPONENTS", "... & FITTING").
FILLER = {"part", "component", "fitting", "related", "accessory", "accessorie", "misc", "miscellaneous",
          "other", "unit", "comp", "small", "type", "w", "o"}

# How much a diagram name that also lists other things keeps: 1.0 when every word of the
# name (segment) belongs to the area, SPECIFICITY_FLOOR when none but the matched word does.
SPECIFICITY_FLOOR = 0.3
OTHER_AREA = 0.25  # the name is about a more specific area ("STEERING WHEEL" for "wheel")
DIRECTION_CLASH = 0.25

_BRACKETS = re.compile(r"[(\[]([^)\]]*)[)\]]")
_SEGMENT_SPLIT = re.compile(r"\s*(?:/|;|&|\+|\|)\s*|\s+and\s+", re.I)


def normalize_text(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


def tokens(text: str) -> list[str]:
    out = []
    for tok in normalize_text(text).split():
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]  # crude plural folding: bumpers -> bumper
        tok = TOKEN_ALIASES.get(tok, tok)
        if tok and tok not in STOPWORDS:
            out.append(tok)
    return out


def _contains(phrase_tokens: list[str], text_tokens: set[str]) -> bool:
    return bool(phrase_tokens) and all(t in text_tokens for t in phrase_tokens)


def _words(toks: Sequence[str]) -> frozenset[str]:
    """Tokens that name a thing: not a direction, filler word, number or single letter."""
    return frozenset(t for t in toks if t not in DIRECTIONS and t not in FILLER
                     and len(t) > 1 and not any(c.isdigit() for c in t))


@dataclass(frozen=True)
class _Name:
    main: frozenset[str]  # tokens outside brackets
    qualifiers: frozenset[str]  # words inside brackets: "(1.5L)", "(HALOGENE)", "(4-DOOR)", "(CONDENSER)"
    segments: tuple[frozenset[str], ...]  # words of each listed thing: "EXHAUST PIPE / SILENCER" -> 2


@lru_cache(maxsize=4096)
def _parse_name(text: str) -> _Name:
    main_text = _BRACKETS.sub(" ", text)
    main = frozenset(tokens(main_text))
    # Directions in brackets are dropped: Honda's "(LH)" / "(RH)" is the steering side, not the part's.
    qualifiers = _words(tokens(" ".join(_BRACKETS.findall(text))))
    segs = tuple(s for s in (_words(tokens(p)) for p in _SEGMENT_SPLIT.split(main_text)) if s)
    return _Name(main, qualifiers, segs)


def segments(text: str) -> list[frozenset[str]]:
    """Words of each thing a name lists: "EXHAUST PIPE / SILENCER (1.5L)" ->
    [{exhaust, pipe}, {silencer}]. Bracket text qualifies the name and is left out."""
    return list(_parse_name(text).segments)


def _directions(toks: Sequence[str]) -> set[str]:
    return DIRECTIONS & set(toks)


def _concept_matches(concept: Concept, qset: set[str]) -> list[frozenset[str]]:
    return [frozenset(tokens(p)) for p in concept.phrases if _contains(tokens(p), qset)]


@lru_cache(maxsize=512)
def _matched(query: str) -> tuple[Concept, ...]:
    qset = set(tokens(query))
    qdirs = _directions(qset)
    found: dict[str, list[frozenset[str]]] = {}
    for c in CONCEPTS:
        hits = _concept_matches(c, qset)
        cdirs = _directions(tokens(c.name))
        if hits and not (qdirs and cdirs and not (qdirs & cdirs)):  # "rear fender" is not a front fender
            found[c.name] = hits
    # The longest match wins: "spare tire" is not the wheel concept, "rear windshield" not the windscreen.
    def covered(hit: frozenset[str], own: str) -> bool:
        return any(hit < other for name, hits in found.items() if name != own for other in hits)

    keep = {name for name, hits in found.items() if not all(covered(h, name) for h in hits)}
    return tuple(c for c in CONCEPTS if c.name in keep)


def matched_concepts(query: str) -> list[Concept]:
    return list(_matched(query))


def expand(query: str) -> list[tuple[str, float, str]]:
    """(phrase, weight, why) alternatives for a query: the query itself, then synonyms
    (0.9), then the diagrams that usually hold parts of the area (0.8)."""
    out: list[tuple[str, float, str]] = [(query, 1.0, "words of the query")]
    seen = {normalize_text(query)}
    concepts = matched_concepts(query)
    for concept in concepts:
        for phrase in concept.phrases:
            if normalize_text(phrase) not in seen:
                seen.add(normalize_text(phrase))
                out.append((phrase, 0.9, f'synonym "{phrase}" ({concept.name})'))
    for concept in concepts:
        for phrase in concept.also:
            if normalize_text(phrase) not in seen:
                seen.add(normalize_text(phrase))
                out.append((phrase, 0.8, f'"{phrase}" usually holds {concept.name} parts'))
    return out


@dataclass(frozen=True)
class _Phrase:
    toks: tuple[str, ...]
    words: frozenset[str]
    weight: float
    why: str
    dirs: frozenset[str]  # the query's directions, else those of the phrase's concept ("front suspension")


@dataclass(frozen=True)
class _Plan:
    phrases: tuple[_Phrase, ...]
    vocab: frozenset[str]  # words of the query and of the selected concepts' phrases
    other_areas: tuple[tuple[frozenset[str], frozenset[str], str], ...]  # (tokens, words, concept)


@lru_cache(maxsize=256)
def _plan(query: str, expansion: tuple[tuple[str, float, str], ...]) -> _Plan:
    qdirs = frozenset(_directions(tokens(query)))
    concepts = matched_concepts(query)
    owner: dict[str, Concept] = {}
    for c in concepts:
        for p in (*c.phrases, *c.also):
            owner.setdefault(normalize_text(p), c)
    phrases = []
    for i, (p, w, why) in enumerate(expansion):
        toks = tuple(tokens(p))
        concept = owner.get(normalize_text(p)) if i else None
        dirs = qdirs or (frozenset(_directions(tokens(concept.name))) if concept else frozenset())
        phrases.append(_Phrase(toks, _words(toks), w, why, dirs))
    vocab = set(tokens(query))
    for c in concepts:
        for p in c.phrases:
            vocab.update(tokens(p))
    chosen = {c.name for c in concepts}
    others = tuple((frozenset(tokens(p)), _words(tokens(p)), c.name)
                   for c in CONCEPTS if c.name not in chosen for p in (*c.phrases, *c.also))
    return _Plan(tuple(phrases), frozenset(vocab), others)


def rate(query: str, text: str, expansion: list[tuple[str, float, str]] | None = None,
         *, specific: bool = True) -> Rating:
    """How well a diagram name (or, with specific=False, a group name) matches `query`.

    A phrase found whole scores its weight; a partial match half of its share of
    words. Bracket text only qualifies a name: a phrase found only there counts
    half. With `specific`, the score is scaled by how much of the listed thing
    is about the area: "EXHAUST PIPE / SILENCER" keeps all of it for "exhaust",
    "SOLENOID VALVE EXHAUST CONTROL" less than half, and a name about a more
    specific area ("STEERING WHEEL" for "wheel") a quarter. A front/rear or
    left/right clash with the query (or with a synonym's concept, so "lower
    arm" from "front suspension" does not pick "REAR LOWER ARM") cuts it to a
    quarter.
    """
    plan = _plan(query, tuple(expansion or expand(query)))
    name = _parse_name(text)
    present = name.main | name.qualifiers
    tdirs = _directions(name.main)
    best = Rating(0.0, "", 0)
    for rank, p in enumerate(plan.phrases):
        if not p.toks:
            continue
        hit = [t for t in p.toks if t in present]
        if not hit:
            continue
        in_main = p.words <= name.main
        whole = len(hit) == len(p.toks) and (in_main or p.words <= name.qualifiers)
        if whole:
            value, why = p.weight, p.why
            if not in_main:
                value *= 0.5
                why += " (only in the bracket text)"
        else:
            frac = len([t for t in hit if t in name.main]) / len(p.toks)
            value, why = p.weight * frac * 0.5, f"some words of {p.why}"
        if specific and value > 0:
            vocab = plan.vocab | set(p.toks)
            words = _words(hit)
            touched = [s for s in name.segments if s & words] if in_main else []
            if not touched:  # matched in the bracket text: judge the whole name
                touched = [s for s in (_words(present),) if s]
            cover = max(len(s & vocab) / len(s) for s in touched) if touched else 1.0
            if cover < 1.0:
                value *= 1.0 - (1.0 - SPECIFICITY_FLOOR) * (1.0 - cover)
                why += "; the name also lists other parts"
            if whole and in_main and p.words:
                # every listed thing holding the match is about a more specific area
                other = next((area for otoks, owords, area in plan.other_areas
                              if p.words < owords and otoks <= present
                              and all(owords <= s for s in touched)), None)
                if other is not None:
                    value *= OTHER_AREA
                    why += f"; the name is about {other}"
        if p.dirs and tdirs and not (p.dirs & tdirs):
            value *= DIRECTION_CLASH
        if value > best.score:
            best = Rating(value, why, rank)
    return best


def score(query: str, text: str, expansion: list[tuple[str, float, str]] | None = None,
          *, specific: bool = True) -> tuple[float, str]:
    """(score 0..1, reason) for a diagram name; see `rate`."""
    r = rate(query, text, expansion, specific=specific)
    return r.score, r.why


def group_hint_score(query: str, group_text: str) -> float:
    """About 0.6 if the group name looks like where the query's concept lives, else 0.

    Earlier hints are the more specific ones, so they score a hair higher: for
    "tail lamp" a "Lighting" group is opened before "Vehicle electrical system"
    or "Bodywork" when only a few groups may be opened.
    """
    gtoks = set(tokens(group_text))
    best = 0.0
    for concept in matched_concepts(query):
        for i, hint in enumerate(concept.group_hints):
            if _contains(tokens(hint), gtoks):
                best = max(best, 0.6 - 0.001 * i)
                break
    return best


def source_hint_score(hints: Sequence[str], group_text: str) -> float:
    """0.9..1.0 if a group is one a source named for the query (`Source.group_hints`),
    earlier hints higher; 0 otherwise. Leading numbers ("5. UPHOLSTERY") are ignored."""
    name = normalize_text(re.sub(r"^\s*\d+\s*[.)]\s*", "", group_text))
    gtoks = set(tokens(name))
    for i, hint in enumerate(hints):
        if normalize_text(hint) == name:
            return max(0.9, 1.0 - 0.01 * i)
        if _contains(tokens(hint), gtoks):
            return max(0.85, 0.95 - 0.01 * i)
    return 0.0
