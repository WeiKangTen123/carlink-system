"""Detection Round 1 (owner, 25 Nov 2026: "try, see whether there is any
enhancement, then tell me"): what the photo sorter saw, compared with the
final list. Code only -- no AI call, no request changes.

The sorter (ai/triage.py) names, per photo, the damaged part it shows best
(IntakePhoto.part) and says in a few words what it sees (IntakePhoto.note:
"Damaged left front wheel arch liner"). Nothing compared that with what the
reads listed or suggested, so a part only the sorter saw was lost -- on four
practice cases 12 of 26 missed parts were on photos the main read was sent.

What the sorter saw damaged (seen_damage): on each photo of the insured
car it saw damage on, its part tag, and the parts its note names
(note_parts) that are other parts. A note is read with care -- the
review's M1: up to its first locator word ("near", "behind", "by"...),
clause by clause, the parts in the order mentioned (the past-report
wording rules, history.family_of's, every word whole, then a canonical
name word for word), never one negated in its clause ("tyre ok", "no
damage on bonnet"), never a sub-part we have no name for ("door handle"),
and never one at the other end of the car from the photo's area
("Windscreen shattered" on a rear photo is no Front Windscreen).

A1, settings.intake_sorter_hints ("off" | "shadow" | "on"): once the
reading, the second look and the linked parts are in (second_look._store),
each part the sorter saw damaged that is not on the list, not among the
suggestions any read made (shown, cut by the limit, or decided), and not
dismissed -- all compared WITHOUT its side (same_component) -- becomes a
suggestion: source "sorter", found_by ["sorter_seen"] (SEEN), the sorter's
part (its side when it gave one, else the "(side undetermined)" name), its
cleaned note as the reason, the photo and the sorter's box as its evidence
(evidence.finish: none on a raw original, the frame set). One per part, its
best photo (a damage close-up first), at most MAX_HINTS a case. Never from
a photo of another vehicle, a document or the scene (NOT_THE_CAR), an
excluded or unusable one, or one the sorter saw no damage on. "shadow":
kept on the AI draft only (ai_draft["sorter_suggestions"]), not shown.

A2, settings.intake_intact_rule ("old" | "shadow" | "strict"): a zone
read's "intact" verdict (ai_draft["zone_intact"]) against a part the
sorter saw damaged on a photo of it -- the same part, and never across
sides or ends when both are known (a_two_match; a "(side undetermined)"
part takes the side of its photo's area, the review's M2). "strict": where
"intact" acts today -- the linked parts (intake/links.py: demoted last,
dropped, a second hop's middle part dropped, cut as "intact") -- it acts
without those parts (strict_intact), and such a part not listed or
suggested becomes a suggestion: found_by ["sorter_intact"] (INTACT),
intact_in the zone reads that called it intact, the sorter's note as its
reason, at most MAX_INTACT a case, outside A1's cap -- the dashboard words
"a zone read said intact, but the photo sorter saw damage" from intact_in;
no photo label is ever stored in the text (filing renumbers photos).
"shadow": those suggestions on the AI draft only, the linked parts as
before. The zone reads' own finds were never suppressed by "intact"
(intake/zones.py), and the second look doesn't read it.

find() is the one place both are worked out, for the pipeline and for the
offline replay (scripts/bench/replay_sorter_hints.py). Photos may be
IntakePhoto rows or dicts with the same fields.
"""
import re
from dataclasses import dataclass, field

from app.ai import evidence
from app.config import settings
from app.parts.history import _FAMILY_RULES, side_of
from app.parts.prefill import item_key
from app.reports.taxonomy import CANONICAL_PARTS, OTHER_PART, UNDETERMINED_SUFFIX, known_part, split_side, unsided_part

SOURCE = "sorter"
# The two rules apart (found_by): A1's -- a part only the sorter saw -- and
# A2's -- a part the sorter saw damaged that a zone read called intact.
SEEN = "sorter_seen"
INTACT = "sorter_intact"
MARKS = (SEEN, INTACT)
# A1's items a case, damage close-ups first; A2's (outside A1's cap).
MAX_HINTS = 6
MAX_INTACT = 4
# The photos a suggestion says the sorter saw its part in.
MAX_SEEN = 12
# The sorter's kinds that are not the insured car: the other party's
# vehicle, paperwork, the scene.
NOT_THE_CAR = frozenset({"third_party", "scene", "document", "log_card", "police_report", "vin_plate", "odometer"})
# The areas that name a side of the car, and an end of it (as triage._clean
# drops a part's side the photo's area disagrees with).
_AREA_SIDE = {
    "left": "Left", "front-left": "Left", "rear-left": "Left",
    "right": "Right", "front-right": "Right", "rear-right": "Right",
}
_AREA_END = {
    "front": "front", "front-left": "front", "front-right": "front",
    "rear": "rear", "rear-left": "rear", "rear-right": "rear",
}
# B9's mark on a rescued photo's note (pipeline.RESCUE_NOTE_PREFIX): no part.
_RESCUE_PREFIX = "Rescue sort:"


def hints_mode() -> str:
    """A1: settings.intake_sorter_hints -- "off", "shadow" or "on"."""
    mode = getattr(settings, "intake_sorter_hints", "off") or "off"
    return mode if mode in ("shadow", "on") else "off"


def intact_rule() -> str:
    """A2: settings.intake_intact_rule -- "old", "shadow" or "strict"."""
    rule = getattr(settings, "intake_intact_rule", "old") or "old"
    return rule if rule in ("shadow", "strict") else "old"


def strict() -> bool:
    """A2 acts: settings.intake_intact_rule is "strict"."""
    return intact_rule() == "strict"


def active() -> bool:
    """Whether second_look._store has anything of Round 1 to do."""
    return hints_mode() != "off" or intact_rule() != "old"


def _get(obj, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# ------------------------------------------------------------ parts


def same_component(a: str | None, b: str | None) -> bool:
    """The same part whatever its side: "Left Tail Lamp" and "Right Tail
    Lamp" (and "Tail Lamp (side undetermined)"), "Left Front Door" and
    "Right Front Door" -- never "Left Front Door" and "Left Rear Door"; a
    "(side undetermined)" name is any part of its family ("Door (side
    undetermined)" and "Left Rear Door"). How the lead counts a part found."""
    a, b = str(a or ""), str(b or "")
    if not a or not b:
        return False
    if a == b or split_side(a)[0] == split_side(b)[0]:
        return True
    if a.endswith(UNDETERMINED_SUFFIX) or b.endswith(UNDETERMINED_SUFFIX):
        return (unsided_part(a) or a) == (unsided_part(b) or b)
    return False


def end_of(name: str | None) -> str | None:
    """"front" | "rear" | None: the end of the car a part (or a family:
    "Front Door") is at -- its name's own "Front" / "Rear", else
    links.FRONT_END / REAR_END (a headlamp is front, a boot lid rear)."""
    from app.intake.links import FRONT_END, REAR_END

    base = split_side(str(name or ""))[0]
    if base.startswith("Front ") or base in FRONT_END:
        return "front"
    if base.startswith("Rear ") or base in REAR_END:
        return "rear"
    return None


def _canonical(family: str, side: str | None) -> str | None:
    """A family (history.family_of's names) as a canonical part: with its
    side when it has one, else the part itself, else its "(side
    undetermined)" name; None when there is none ("Wheel Nut")."""
    if side:
        name = f"{side} {family}"
        if name in CANONICAL_PARTS:
            return name
    if family in CANONICAL_PARTS and family != OTHER_PART:
        return family
    twin = unsided_part(f"Left {family}")
    return twin if twin and twin.endswith(UNDETERMINED_SUFFIX) and twin in CANONICAL_PARTS else None


# ------------------------------------------------------------ reading a note

# A note names its part first and where it is after: "Rear bumper cracked
# near LH reflector" is the bumper. Read only up to the first of these.
_LOCATORS = re.compile(r"\b(?:near|beside|next to|adjacent(?: to)?|close to|above|below|around|at|behind|by|from|"
                       r"under|over|between)\b", re.I)
# A note's clauses: one part's words each, as a rule ("Wheel scuffed, tyre ok").
_CLAUSES = re.compile(r"[,;:]|\b(?:and|but|while|with)\b", re.I)
# A part said to be undamaged in its clause; or "no" / "not" before it.
_NEGATION = re.compile(r"\b(?:ok|okay|undamaged|no damage|not damaged|intact|fine|good)\b", re.I)
_NOT_BEFORE = re.compile(r"\b(?:no|not)\b", re.I)
# A part's name followed by one of these is a sub-part we have no name for
# ("door handle", "number plate lamp"): not that part.
_SUB_PART = re.compile(r"\s*(?:handle|emblem|badge|logo|garnish|moulding|molding|seal|holder|bracket|lamp|light|"
                       r"sensor|camera|absorber|clip|hinge|lock|striker|strut|spoiler|washer|wiper|nozzle|"
                       r"support)s?\b", re.I)
# Inside a rule: a word's start -- the pattern's, an alternative's, a
# group's, or after ".*" -- where a whole word must begin (the review's M1
# e: "hood" in "neighbourhood" is no bonnet).
_WORD_START = re.compile(r"(^|\(|\||\.\*)(?=[a-z])")
# Words the rules have no family for, as a note writes them.
_EXTRA_RULES = [(r"\bsills?\b", "Sill / Rocker Panel")]


def _whole_words(pattern: str) -> str:
    return _WORD_START.sub(lambda m: m.group(1) + r"\b", pattern)


# The canonical names without their side, longest first: a note that names
# one word for word ("boot floor board torn") where no wording rule does.
_NAMES = sorted({split_side(p)[0] for p in CANONICAL_PARTS if p != OTHER_PART}, key=lambda n: (-len(n), n))
# Every way a note names a family, most specific first: history's rules
# (whole words), these few more, then the canonical names themselves.
_RULES: list[tuple[re.Pattern, str]] = (
    [(re.compile(_whole_words(p), re.I), family) for p, family in _FAMILY_RULES]
    + [(re.compile(p, re.I), family) for p, family in _EXTRA_RULES]
    + [(re.compile(rf"\b{re.escape(name)}\b", re.I), name) for name in _NAMES]
)


def _mentions(clause: str) -> list[tuple[int, int, str]]:
    """(start, end, family) of each part a clause mentions, in the order
    mentioned: of the rules matching words that overlap, the most specific
    (the first in _RULES: "boot lid striker" is the lock, not the lid)."""
    found = []
    for n, (rx, family) in enumerate(_RULES):
        m = rx.search(clause)
        if m:
            found.append((m.start(), m.end(), n, family))
    found.sort()
    out: list[list] = []
    for start, end, n, family in found:
        if out and start < out[-1][1]:
            held = out[-1]
            held[1] = max(held[1], end)
            if n < held[2]:
                held[2], held[3], held[4] = n, family, end
            continue
        out.append([start, end, n, family, end])
    return [(start, own_end, family) for start, _, _, family, own_end in out]


def _before_locator(text: str) -> str:
    """The note up to its first locator word; a clause that starts with one
    ("Under the front bumper, undercover hanging") is a place, not a part:
    left out, and the rest read the same way."""
    while text:
        m = _LOCATORS.search(text)
        if m is None:
            return text
        if text[:m.start()].strip():
            return text[:m.start()].strip()
        sep = _CLAUSES.search(text, m.end())
        text = text[sep.end():].strip() if sep else ""
    return text


def note_parts(note, area: str | None = None, tag: str | None = None) -> list[tuple[str, str]]:
    """Every part a sorter's note names, in the order mentioned, as
    (canonical part, family) -- the family keeps the end the canonical
    name may drop ("Front Door" -> "Door (side undetermined)"). Read up to
    the first locator word (_LOCATORS), clause by clause (_CLAUSES); a part
    negated in its clause (_NEGATION, or "no" / "not" before it), a
    sub-part we have no name for (_SUB_PART), or one at the other end of
    the car from the photo's area (else from its tag) is left out; a side
    the area disagrees with is dropped (as the sorter's own part,
    triage._clean): the "(side undetermined)" name."""
    text = " ".join(note.split()) if isinstance(note, str) else ""
    if not text or text.startswith(_RESCUE_PREFIX):
        return []
    text = _before_locator(text)
    photo_end = _AREA_END.get(area or "") or end_of(tag)
    out: list[tuple[str, str]] = []
    for clause in _CLAUSES.split(text):
        clause = clause.strip()
        if not clause:
            continue
        negated = bool(_NEGATION.search(clause))
        for start, end, family in _mentions(clause):
            if negated or _NOT_BEFORE.search(clause[:start]) or _SUB_PART.match(clause[end:]):
                continue
            if photo_end and end_of(family) not in (None, photo_end):
                continue
            side = side_of(clause[max(0, start - 12):end]) or side_of(clause)
            if side and area in _AREA_SIDE and side != _AREA_SIDE[area]:
                side = None
            part = _canonical(family, side)
            if part and part not in [p for p, _ in out]:
                out.append((part, family))
    return out


def note_part(note, area: str | None = None, tag: str | None = None) -> str | None:
    """The part a sorter's note names first (note_parts), or None."""
    parts = note_parts(note, area, tag)
    return parts[0][0] if parts else None


# ------------------------------------------------------------ what the sorter saw


@dataclass
class Seen:
    """One part the sorter saw damaged on one photo."""
    part: str
    photo: object
    ref: str  # "@<photo id>"
    via: str  # "part" (its tag) or "note"
    order: int  # the photo's place in camera order
    # A2's match (a_two_match): its side -- the name's, else its photo's
    # area's -- and its end -- the name's, else the note's word for the
    # same part ("Front door dented"), else the area's.
    side: str | None = None
    end: str | None = None


def _eligible(photo) -> bool:
    kind = _get(photo, "kind")
    return (isinstance(kind, str) and bool(kind) and kind not in NOT_THE_CAR and not _get(photo, "excluded", False)
            and _get(photo, "quality") != "unusable" and _get(photo, "shows_damage") is True)


def _placed(part: str, area, families: list[tuple[str, str]]) -> tuple[str | None, str | None]:
    """(side, end) of a part the sorter saw, for A2: its name's, else the
    photo's area's side, and the end of a note family naming the same part
    (the note may say "front door" where the name is "Door (side
    undetermined)"), else the area's end."""
    area = area if isinstance(area, str) else ""
    side = split_side(part)[1] or _AREA_SIDE.get(area)
    end = end_of(part)
    if end is None:
        end = next((end_of(f) for p, f in families if same_component(p, part) and end_of(f)), None)
    return side, end or _AREA_END.get(area)


def seen_damage(photos: list) -> list[Seen]:
    """Every part the sorter saw damaged, per eligible photo (_eligible):
    its part tag, and the first part its note names (note_parts) that is
    another part (same_component) -- "Boot lid striker bent" on a boot lid
    photo is the lock -- in camera order."""
    out: list[Seen] = []
    for n, photo in enumerate(photos or []):
        if not _eligible(photo):
            continue
        ref = f"@{_get(photo, 'id')}"
        area = _get(photo, "area")
        area = area if isinstance(area, str) else None
        tag = _get(photo, "part")
        tag = tag if isinstance(tag, str) and known_part(tag) and tag != OTHER_PART else None
        families = note_parts(_get(photo, "note"), area, tag)
        if tag:
            out.append(Seen(tag, photo, ref, "part", n, *_placed(tag, area, families)))
        other = next((pf for pf in families if not same_component(pf[0], tag)), None)
        if other is not None:
            out.append(Seen(other[0], photo, ref, "note", n, *_placed(other[0], area, [other])))
    return out


def _rank(seen: Seen) -> tuple:
    """Best photo first: a damage close-up, a part tag before a note, sharp
    before blurry, then camera order."""
    return (_get(seen.photo, "kind") != "damage", seen.via != "part", _get(seen.photo, "quality") == "blurry",
            seen.order)


def a_two_match(seen: Seen, part) -> bool:
    """A2: whether a zone read's intact `part` is the part the sorter saw
    damaged -- the same family, and never another side or end where both
    are known (Seen.side / Seen.end; the intact name's own)."""
    if not isinstance(part, str) or not part:
        return False
    if (unsided_part(seen.part) or seen.part) != (unsided_part(part) or part):
        return False
    side, end = split_side(part)[1], end_of(part)
    if seen.side and side and seen.side != side:
        return False
    return not (seen.end and end and seen.end != end)


def intact_reads(seen: Seen, zone_intact: dict) -> list[str]:
    """The zone reads that called the part the sorter saw intact
    (a_two_match)."""
    return sorted(str(read) for read, parts in (zone_intact or {}).items()
                  if isinstance(parts, list) and any(a_two_match(seen, p) for p in parts))


def strict_intact(zone_intact: dict, photos: list) -> dict[str, list[str]]:
    """A2 for the linked parts: ai_draft["zone_intact"] without the parts the
    sorter saw damaged on a photo of them (a_two_match), each read's list in
    its order; anything not a name left out."""
    seen = seen_damage(photos)
    return {read: [p for p in parts if isinstance(p, str) and not any(a_two_match(s, p) for s in seen)]
            for read, parts in (zone_intact or {}).items() if isinstance(parts, list)}


def overridden(zone_intact: dict, photos: list) -> list[dict]:
    """A2: each zone read's intact verdict a sorter photo contradicts --
    {"read", "part", "photo" ("@<photo id>")}, by read then part."""
    seen = sorted(seen_damage(photos), key=_rank)
    out = []
    for read, parts in sorted((zone_intact or {}).items(), key=lambda kv: str(kv[0])):
        for part in parts if isinstance(parts, list) else []:
            hit = next((s for s in seen if a_two_match(s, part)), None)
            if hit is not None:
                out.append({"read": str(read), "part": part, "photo": hit.ref})
    return out


# ------------------------------------------------------------ the suggestions


@dataclass
class Found:
    """find()'s answer: A2's suggestions (intact), A1's (hints), why the
    other parts the sorter saw were not suggested (skipped: listed,
    suggested, dismissed, cap, intact_cap -- a count each), and per
    suggestion's key where it came from (origin: {"via": "part" | "note",
    "reads": the zone reads that called it intact}) -- for the replay; never
    stored."""
    intact: list[dict] = field(default_factory=list)
    hints: list[dict] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    origin: dict[str, dict] = field(default_factory=dict)


def _clean_note(note) -> str | None:
    if not isinstance(note, str) or note.startswith(_RESCUE_PREFIX):
        return None
    return evidence.clean_reason(note)


def default_facts(photos: list) -> dict:
    """{photo id: evidence.PhotoFacts} from the photos alone (the replay's:
    no image to read a frame from; "raw" only where a photo says so)."""
    out = {}
    for photo in photos or []:
        pid = _get(photo, "id")
        if pid:
            out[str(pid)] = evidence.PhotoFacts(ai_path=str(_get(photo, "ai_path") or ""),
                                                raw=bool(_get(photo, "raw", False)),
                                                sort_box=evidence.clean_box(_get(photo, "damage_box")),
                                                sort_part=_get(photo, "part"))
    return out


def _item(seen: Seen, all_seen: list[Seen], mark: str, reads: list[str], facts: dict) -> dict:
    """The suggestion (a SecondLookItem dump) for a part on its best photo,
    and the other photos it saw the same part on (evidence.same_part) as
    seen_in and evidence, best first. Its evidence goes through
    evidence.finish, as every finding's: the sorter's box on each photo
    where it is round this part (the photo's tag, or a photo with none --
    a part read from a note on a tagged photo gets none), none on a photo
    the AI got raw, each with its frame."""
    from app.ai.second_look import covers_of
    from app.reports.schema import SecondLookItem

    same = [seen] + [s for s in sorted(all_seen, key=_rank)
                     if s.ref != seen.ref and evidence.same_part(s.part, seen.part)]
    refs = list(dict.fromkeys(s.ref for s in same))[:MAX_SEEN]
    item = SecondLookItem(
        key=item_key({"part": seen.part}) or seen.part, part=seen.part, photo_reference=seen.ref,
        confidence="Low", source=SOURCE, covers=covers_of(seen.part), reason=_clean_note(_get(seen.photo, "note")),
        evidence=[evidence.entry(ref, evidence.SORT, confidence="Low") for ref in refs], seen_in=refs,
        found_by=[mark], intact_in=list(reads),
    ).model_dump()
    own: dict = {}
    for s in same:
        pid = s.ref[1:]
        held = facts.get(pid)
        if held is None or pid in own:
            continue
        boxed = s.via == "part" or not _get(s.photo, "part")
        own[pid] = held if boxed else evidence.PhotoFacts(ai_path=held.ai_path, raw=held.raw)
    evidence.finish([], [item], own)
    return item


def find(photos: list, *, listed, shown: list[dict], dismissed, zone_intact: dict | None, hints: bool,
         strict_rule: bool, cap: int = MAX_HINTS, intact_cap: int = MAX_INTACT, facts: dict | None = None) -> Found:
    """A1 and A2's suggestions for one case. photos: the case's, in camera
    order; listed: the list's part names (second_look.listed_parts); shown:
    every suggestion a read made (shown, cut by the limit, or decided:
    dicts with "part"); dismissed: the keys (or parts) a person dismissed;
    zone_intact: ai_draft["zone_intact"]; facts: {photo id:
    evidence.PhotoFacts} (pipeline.photo_facts; None: default_facts). hints:
    A1 on (or in shadow); strict_rule: A2's suggestions (strict, or in
    shadow).

    A2 first, over every part the sorter saw in best-photo order: one that
    a zone read called intact (a_two_match) is suggested, intact_in those
    reads, at most `intact_cap`. Then A1: every other part, one per part
    (same_component), at most `cap`. A part listed, suggested or dismissed
    -- compared without its side -- is neither; each part is counted once
    in skipped."""
    seen = sorted(seen_damage(photos), key=_rank)
    facts = default_facts(photos) if facts is None else facts
    listed = [str(p) for p in listed or () if p]
    others = [str(it.get("part")) for it in shown or () if isinstance(it, dict) and isinstance(it.get("part"), str)]
    gone = {str(k) for k in dismissed or () if k}
    found = Found()
    # The parts decided on already: suggested, or skipped (and counted).
    done: list[str] = []

    def skip(s: Seen) -> str | None:
        if any(same_component(s.part, p) for p in listed):
            return "listed"
        if any(same_component(s.part, p) for p in others):
            return "suggested"
        key = item_key({"part": s.part}) or s.part
        if key in gone or s.part in gone or any(same_component(s.part, p) for p in gone if known_part(p)):
            return "dismissed"
        return None

    def decided(s: Seen, why: str | None) -> bool:
        """Marks s's part as decided on; True when it is skipped (why)."""
        done.append(s.part)
        if why:
            found.skipped[why] = found.skipped.get(why, 0) + 1
        return bool(why)

    if strict_rule:
        for s in seen:
            reads = intact_reads(s, zone_intact or {})
            if not reads or any(same_component(s.part, p) for p in done):
                continue
            if decided(s, skip(s) or ("intact_cap" if len(found.intact) >= intact_cap else None)):
                continue
            found.intact.append(_item(s, seen, INTACT, reads, facts))
            found.origin[found.intact[-1]["key"]] = {"via": s.via, "reads": reads}
    if hints:
        for s in seen:
            if any(same_component(s.part, p) for p in done):
                continue
            if decided(s, skip(s) or ("cap" if len(found.hints) >= cap else None)):
                continue
            found.hints.append(_item(s, seen, SEEN, [], facts))
            found.origin[found.hints[-1]["key"]] = {"via": s.via, "reads": intact_reads(s, zone_intact or {})}
    return found
