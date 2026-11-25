"""Detection Round 1 (owner, 25 Nov 2026: "try, see whether there is any
enhancement, then tell me"): what the photo sorter saw, compared with the
final list. Code only -- no AI call, no request changes.

The sorter (ai/triage.py) names, per photo, the damaged part it shows best
(IntakePhoto.part) and says in a few words what it sees (IntakePhoto.note:
"Damaged left front wheel arch liner"). Nothing compared that with what the
reads listed or suggested, so a part only the sorter saw was lost -- on four
practice cases 12 of 26 missed parts were on photos the main read was sent.

A1, settings.intake_sorter_hints ("off" | "shadow" | "on"): once the
reading, the second look and the linked parts are in (second_look._store),
each part the sorter saw damaged -- a photo's part tag, and a known part its
note names (note_part: the past-report wording rules, history.family_of)
-- that is not on the list, not among the suggestions shown or decided, and
not dismissed, all compared WITHOUT its side (same_component), becomes a
suggestion: source "sorter", the sorter's part (its side when it gave one,
else the "(side undetermined)" name), the note as its reason, the photo
and the sorter's box as its evidence. One per part, its best photo (a
damage close-up first), at most MAX_HINTS a case. Never from a photo of
another vehicle, a document or the scene (NOT_THE_CAR), an excluded or
unusable one, or one the sorter saw no damage on. "shadow": kept on the AI
draft only (ai_draft["sorter_suggestions"]), not shown.

A2, settings.intake_intact_rule ("old" | "strict"): with "strict", a zone
read's "intact" verdict (ai_draft["zone_intact"]) never suppresses a part
the sorter saw damaged on a photo of that part (evidence.same_part: the same
part, never the other side's twin). Where "intact" acts today -- the linked
parts (intake/links.py: demoted last, dropped, a second hop's middle part
dropped, cut as "intact") -- it acts without those parts (strict_intact);
and such a part not listed or suggested becomes a suggestion, source
"sorter", reason "Zone read said intact, but photo P07 shows damage
(sorter): <note>" -- outside A1's cap, whatever A1 says. The zone reads'
own finds were never suppressed by "intact" (intake/zones.py), and the
second look doesn't read it.

find() is the one place both are worked out, for the pipeline and for the
offline replay (scripts/bench/replay_sorter_hints.py). Photos may be
IntakePhoto rows or dicts with the same fields.
"""
import re
from dataclasses import dataclass, field

from app.ai import evidence
from app.config import settings
from app.parts.history import family_of, side_of
from app.parts.prefill import item_key
from app.reports.taxonomy import CANONICAL_PARTS, OTHER_PART, UNDETERMINED_SUFFIX, known_part, split_side, unsided_part

SOURCE = "sorter"
# A1's items a case, damage close-ups first. A2's are not counted in it.
MAX_HINTS = 6
# The photos a suggestion says the sorter saw its part in.
MAX_SEEN = 12
# The sorter's kinds that are not the insured car: the other party's
# vehicle, paperwork, the scene.
NOT_THE_CAR = frozenset({"third_party", "scene", "document", "log_card", "police_report", "vin_plate", "odometer"})
# The areas that name a side of the car, and that side (as triage._clean
# drops a part's side the photo's area disagrees with).
_AREA_SIDE = {
    "left": "Left", "front-left": "Left", "rear-left": "Left",
    "right": "Right", "front-right": "Right", "rear-right": "Right",
}
# A note names its part first and where it is after: "Rear bumper cracked
# near LH reflector" is the bumper. Read only up to the first of these.
_LOCATORS = re.compile(r"\b(?:near|beside|next to|adjacent to|close to|above|below|around|at)\b", re.I)
# B9's mark on a rescued photo's note (pipeline.RESCUE_NOTE_PREFIX): no part.
_RESCUE_PREFIX = "Rescue sort:"
REASON_MAX = evidence.REASON_MAX
INTACT_REASON = "Zone read said intact, but photo {label} shows damage (sorter)"


def hints_mode() -> str:
    """A1: settings.intake_sorter_hints -- "off", "shadow" or "on"."""
    mode = getattr(settings, "intake_sorter_hints", "off") or "off"
    return mode if mode in ("shadow", "on") else "off"


def strict() -> bool:
    """A2: settings.intake_intact_rule is "strict"."""
    return getattr(settings, "intake_intact_rule", "old") == "strict"


def active() -> bool:
    """Whether second_look._store has anything of Round 1 to do."""
    return hints_mode() != "off" or strict()


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


# The canonical names without their side, longest first: a note that names
# one word for word ("boot floor board torn") where no wording rule does.
_NAMES = sorted({split_side(p)[0] for p in CANONICAL_PARTS if p != OTHER_PART}, key=lambda n: (-len(n), n))


def _named(text: str) -> str | None:
    low = f" {text.lower()} "
    return next((name for name in _NAMES if re.search(rf"\b{re.escape(name.lower())}\b", low)), None)


def note_part(note, area: str | None = None) -> str | None:
    """The canonical part a sorter's note names, or None: the note up to its
    first locator word (_LOCATORS), read with the past-report wording rules
    (history.family_of, side_of), else a canonical name it holds word for
    word. A side the photo's area disagrees with is dropped (as the sorter's
    own part, triage._clean): the "(side undetermined)" name."""
    text = " ".join(str(note or "").split())
    if not text or text.startswith(_RESCUE_PREFIX):
        return None
    text = _LOCATORS.split(text, 1)[0].strip()
    if not text:
        return None
    family = family_of(text) or _named(text)
    if not family:
        return None
    side = side_of(text)
    if side and area in _AREA_SIDE and side != _AREA_SIDE[area]:
        side = None
    return _canonical(family, side)


# ------------------------------------------------------------ what the sorter saw


@dataclass
class Seen:
    """One part the sorter saw damaged on one photo."""
    part: str
    photo: object
    ref: str  # "@<photo id>"
    via: str  # "part" (its tag) or "note"
    order: int  # the photo's place in camera order


def _eligible(photo) -> bool:
    kind = _get(photo, "kind")
    return (bool(kind) and kind not in NOT_THE_CAR and not _get(photo, "excluded", False)
            and _get(photo, "quality") != "unusable" and _get(photo, "shows_damage") is True)


def seen_damage(photos: list) -> list[Seen]:
    """Every part the sorter saw damaged, per eligible photo (_eligible):
    its part tag, and the part its note names when that is another part
    (same_component), in camera order."""
    out: list[Seen] = []
    for n, photo in enumerate(photos):
        if not _eligible(photo):
            continue
        ref = f"@{_get(photo, 'id')}"
        tag = _get(photo, "part")
        tag = tag if tag and known_part(tag) and tag != OTHER_PART else None
        if tag:
            out.append(Seen(tag, photo, ref, "part", n))
        named = note_part(_get(photo, "note"), _get(photo, "area"))
        if named and not same_component(named, tag):
            out.append(Seen(named, photo, ref, "note", n))
    return out


def _rank(seen: Seen) -> tuple:
    """Best photo first: a damage close-up, a part tag before a note, sharp
    before blurry, then camera order."""
    return (_get(seen.photo, "kind") != "damage", seen.via != "part", _get(seen.photo, "quality") == "blurry",
            seen.order)


def intact_reads(part: str, zone_intact: dict) -> list[str]:
    """The zone reads that called this part intact (evidence.same_part: the
    same part, never the other side's twin)."""
    return sorted(str(read) for read, parts in (zone_intact or {}).items()
                  if isinstance(parts, list) and any(isinstance(p, str) and evidence.same_part(part, p) for p in parts))


def strict_intact(zone_intact: dict, photos: list) -> dict[str, list[str]]:
    """A2 for the linked parts: ai_draft["zone_intact"] without the parts the
    sorter saw damaged on a photo of them (seen_damage, evidence.same_part),
    each read's list in its order."""
    seen = [s.part for s in seen_damage(photos)]
    return {read: [p for p in parts if not any(evidence.same_part(s, p) for s in seen)]
            for read, parts in (zone_intact or {}).items() if isinstance(parts, list)}


def overridden(zone_intact: dict, photos: list) -> list[dict]:
    """A2: each zone read's intact verdict a sorter photo contradicts --
    {"read", "part", "photo" ("@<photo id>")}, by read then part."""
    seen = sorted(seen_damage(photos), key=_rank)
    out = []
    for read, parts in sorted((zone_intact or {}).items()):
        for part in parts if isinstance(parts, list) else []:
            hit = next((s for s in seen if isinstance(part, str) and evidence.same_part(s.part, part)), None)
            if hit is not None:
                out.append({"read": str(read), "part": part, "photo": hit.ref})
    return out


# ------------------------------------------------------------ the suggestions


@dataclass
class Found:
    """find()'s answer: A2's suggestions (intact), A1's (hints), why the
    other parts the sorter saw were not suggested (skipped: listed,
    suggested, dismissed, cap -- a count each), and per suggestion's key
    where it came from (origin: {"via": "part" | "note", "reads": the zone
    reads that called it intact}) -- for the replay; never stored."""
    intact: list[dict] = field(default_factory=list)
    hints: list[dict] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    origin: dict[str, dict] = field(default_factory=dict)


def _clean_note(note) -> str | None:
    return evidence.clean_reason(note) if not str(note or "").startswith(_RESCUE_PREFIX) else None


def _cut(text: str) -> str:
    if len(text) <= REASON_MAX:
        return text
    cut = text[:REASON_MAX + 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else text[:REASON_MAX]).rstrip(" ,;:-")


def _box_of(seen: Seen):
    """The sorter's box on the photo, when it is round this part: the box is
    drawn round the damage on the part the photo is tagged with, so a part
    only its note names gets it only on a photo with no tag."""
    tag = _get(seen.photo, "part")
    return _get(seen.photo, "damage_box") if seen.via == "part" or not tag else None


def _item(seen: Seen, all_seen: list[Seen], reason: str | None) -> dict:
    """The suggestion (a SecondLookItem dump) for a part on its best photo:
    the sorter's box there (_box_of), and the other photos it saw the same
    part on (evidence.same_part) as seen_in and evidence, best first."""
    from app.ai.second_look import covers_of
    from app.reports.schema import SecondLookItem

    same = [seen] + [s for s in sorted(all_seen, key=_rank)
                     if s.ref != seen.ref and evidence.same_part(s.part, seen.part)]
    refs = list(dict.fromkeys(s.ref for s in same))[:MAX_SEEN]
    boxes = {}
    for s in same:
        boxes.setdefault(s.ref, _box_of(s))
    entries = [evidence.entry(ref, evidence.SORT, box=boxes.get(ref), kind=evidence.SORT, confidence="Low")
               for ref in refs]
    return SecondLookItem(
        key=item_key({"part": seen.part}) or seen.part, part=seen.part, photo_reference=seen.ref,
        confidence="Low", source=SOURCE, covers=covers_of(seen.part), reason=reason,
        evidence=evidence.order_evidence(entries, seen.ref), seen_in=refs,
    ).model_dump()


def find(photos: list, labels: dict[str, str], *, listed, shown: list[dict], dismissed, zone_intact: dict | None,
         hints: bool, strict_rule: bool, cap: int = MAX_HINTS) -> Found:
    """A1 and A2's suggestions for one case. photos: the case's, in camera
    order; labels: {photo id: "P07"} (A2's reason); listed: the list's part
    names (second_look.listed_parts); shown: the suggestions shown or
    decided (dicts with "part" and "key"); dismissed: the keys (or parts) a
    person dismissed; zone_intact: ai_draft["zone_intact"]. hints: A1 on (or
    in shadow); strict_rule: A2 "strict".

    A2 first, over every part the sorter saw in best-photo order: one that
    a zone read called intact is suggested with INTACT_REASON, on the photo
    that contradicts it. Then A1: every other part, one per part
    (same_component), at most `cap`. A part listed, shown or dismissed --
    compared without its side -- is neither; each part is counted once in
    skipped."""
    seen = sorted(seen_damage(photos), key=_rank)
    listed = [str(p) for p in listed or () if p]
    others = [str(it.get("part")) for it in shown or () if isinstance(it, dict) and it.get("part")]
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
            reads = intact_reads(s.part, zone_intact or {})
            if not reads or any(same_component(s.part, p) for p in done):
                continue
            if decided(s, skip(s)):
                continue
            label = labels.get(str(_get(s.photo, "id"))) or "?"
            note = _clean_note(_get(s.photo, "note"))
            found.intact.append(_item(s, seen, _cut(INTACT_REASON.format(label=label) + (f": {note}" if note else ""))))
            found.origin[found.intact[-1]["key"]] = {"via": s.via, "reads": reads}
    if hints:
        for s in seen:
            if any(same_component(s.part, p) for p in done):
                continue
            if decided(s, skip(s) or ("cap" if len(found.hints) >= cap else None)):
                continue
            found.hints.append(_item(s, seen, _clean_note(_get(s.photo, "note"))))
            found.origin[found.hints[-1]["key"]] = {"via": s.via, "reads": intact_reads(s.part, zone_intact or {})}
    return found
