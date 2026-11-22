"""The linked check (contract 10, plans/2026-11-17-linked-parts/
contract10.md section 3): after the linked parts are stored, one light call
per damaged area of the car -- at most settings.intake_link_check_max_calls
a case, the areas with the most linked parts first -- on
settings.gemini_link_chain (never the strong model, never a tool) with that
area's photos (at most MAX_PHOTOS, at RESOLUTION), that area's surveyor
skill file (ai/skills/<area>.md; left and right share side.md) and the
CLOSED list of its linked parts, each by a short id (C1, C2...).

For each id the model answers a cue first -- what it can see (CUES) -- then
a verdict (VERDICTS), a photo, a box and a short reason. Code reads the
answer:
  - an id not on the list is dropped; each id once (the first answer);
  - the side is never taken from the answer (the schema has no part and no
    side to give: the item keeps its name and side_from);
  - a "seen" or "likely" whose cue shows nothing (none_seen, cannot_see)
    is taken as "cannot_tell": the verdict needs a sign it can point to;
    a "seen" that names no photo it was sent is taken as "likely";
  - seen / likely / strip_down: the item's bucket follows the verdict; a
    "seen" adds its photo as evidence by "link_check" (the box cleaned as
    evidence.clean_box, on the AI-input frame; no box when it gave none);
  - "no": the item goes last in its group (after every linked part still
    open), its bucket kept -- the Review step shows it under "Probably
    not";
  - "cannot_tell": the item's bucket, evidence and place unchanged;
  - every answered item gets item.link_check (schema.LinkCheck: verdict,
    cue, model, reason cleaned as evidence.clean_reason).
It never adds or removes an item and never touches the damage list. What
the step did goes on SecondLook.link_run (a CheckRun, by "lite").

When it runs: settings.intake_link_check "lite" and settings.intake_links
"on" (the open linked parts in second_look.items) or "shadow" (those in
IntakeSession.ai_draft["link_suggestions"]; the answer and the CheckRun go
there too, ai_draft["link_run"], never into the case's data). Anything else
-- "off", the default -- is no call and no write.

Failures never fail the reading: a call makes at most MAX_TRIES requests
in all (a 503: one short wait and one retry), each through the shared rate
limiter, none started after TIME_BOX; then a timeout, a busy model, an
answer that doesn't parse, or no key or allowance leaves the linked parts
as they were, with a note. A failed call ends the
step: the next area would most likely fail the same way. The answers are
written under SQLite's write lock onto the items as they are then, so a
person's add or dismissal made during the call stands -- an item a person
settled meanwhile is left exactly as they decided on it. The run log keeps
the step as kind "link_check" (runs.LINK_CHECK_KIND).
"""
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.ai import evidence, prompt_packs, runs
from app.ai.keys import AiActor, AiUnavailable
from app.ai.second_look import _ChainUntil, _within, split_settled
from app.ai.structured import call_structured, image_part
from app.config import ANY, chain, settings, thinking_levels

logger = logging.getLogger(__name__)

# The answer on one linked part (schema.LinkCheck): the cue, then the verdict.
CUES = ("crush", "gap_misfit", "displaced", "cracked", "none_seen", "cannot_see")
VERDICTS = ("seen", "likely", "strip_down", "no", "cannot_tell")
# The cues that are a sign a person can be pointed to.
SIGN_CUES = ("crush", "gap_misfit", "displaced", "cracked")
# The verdicts that set the item's bucket (links.BUCKETS).
BUCKET_OF = {"seen": "seen", "likely": "likely", "strip_down": "strip_down"}
# The photos of one area sent in one call, and how: medium is ~560 tokens a
# photo on Gemini 3, so 8 photos and the skill file stay near 7k tokens.
MAX_PHOTOS = 8
RESOLUTION = "medium"
# The candidates of one call (intake_links_max is 12 by default).
MAX_CANDIDATES = 16
# The reason as the model is asked for it: at most this many words.
MAX_REASON_WORDS = 25
# call_structured's purpose: structured.CALL_KINDS["link"] gives it
# settings.gemini_thinking_level_link.
PURPOSE = "link"
# One area's call, however many tries it makes, and its tries in all:
# contract10.md 6's "503 -> one retry, then unchanged" (review M3). Every
# try takes a slot of the rate limiter every live user's reads share, and
# the case stays "reading" meanwhile, so a busy model costs at most two
# requests and one short wait (structured.OVERLOADED_RETRY_SECONDS), never
# call_structured's usual two a model and a second round. A try's own
# timeout is cut to what is left of the box (call_structured's deadline).
TIME_BOX = 30.0
MAX_TRIES = 2
TIMEOUT = TIME_BOX
# L1 of plans/2026-11-18-light-detection (settings.gemini_thinking_level_link
# "high" for a model of the chain): thinking takes time, so the call's box --
# and its try's timeout -- is this instead; the tries stay MAX_TRIES.
TIME_BOX_THINKING = 60.0

SKILLS_DIR = Path(__file__).resolve().parent / "skills"
# The skill files by version (settings.intake_link_skill): L1, the frozen
# ones (plans/2026-11-17-linked-parts/L1-freeze.md), never edited; L2, P's
# (plans/2026-11-18-light-detection/P-prompts.md 3.4): L1 word for word
# plus worked examples and "an unseen part is never 'no' for that alone",
# each with a <name>.signs.md twin for the signs field (L3).
SKILL_DIRS = {"L1": SKILLS_DIR, "L2": SKILLS_DIR.parent / "skills-L2"}
# A group's area -> its skill file. "side": a side part whose side neither
# it nor its listed part has -- the photos of both sides go.
SKILL_OF = {"front": "front", "rear": "rear", "left": "side", "right": "side", "side": "side",
            "underbody": "underbody", "wheel": "wheel"}
# Ties between groups of as many linked parts go in this order.
GROUP_ORDER = ("rear", "front", "left", "right", "side", "underbody", "wheel")
AREA_WORDS = {"front": "the front of the car", "rear": "the rear of the car", "left": "the left side of the car",
              "right": "the right side of the car", "side": "one side of the car (which side is not known)",
              "underbody": "the underside of the car", "wheel": "the wheels of the car"}
# The zones (intake/zones.photo_zones) whose photos an area's call may send.
_AREA_ZONES = {"front": {"front"}, "rear": {"rear"}, "left": {"left"}, "right": {"right"}, "side": {"left", "right"},
               "underbody": set()}
_SIDE_OF_AREA = {"left": "left", "front-left": "left", "rear-left": "left",
                 "right": "right", "front-right": "right", "rear-right": "right"}
TYPE_WORDS = {"attached": "mounted on", "behind": "behind", "opens_with": "opens with", "crush": "in the crush path of"}
_LABEL_RE = re.compile(r"^P0*(\d+)$")

NOTE_NO_PHOTOS = "No photos of the linked parts' areas to check."
NOTE_NO_ALLOWANCE = "No AI key or allowance left for the linked check right now."
NOTE_BUSY = "The AI was busy; the linked parts are as the table placed them."
NOTE_FAILED = "The linked check didn't work this time; the linked parts are as the table placed them."
NOTE_PART = "Checked {done} of {total} areas."

SYSTEM_PROMPT = (
    "You help a motor surveyor in Singapore. Parts of the insured car are already listed as damaged: the parents. "
    "Each candidate below is linked to a parent: mounted on it, behind it, opening with it, or next in the crush "
    "path. From these photos only, and by the surveyor's notes for this area below, judge each candidate.\n"
    "Answer every candidate id once, with the fields in this order:\n"
    "- cue: what you can see on the candidate, or on its parent where the candidate sits: crush (crumpled or "
    "pushed in), gap_misfit (a gap uneven or a panel not fitting), displaced (shifted, loose, hanging or out of "
    "line), cracked (cracked, torn or broken), none_seen (that spot shows clearly and nothing points at damage), "
    "cannot_see (that spot is not visible in these photos);\n"
    "- verdict: seen, likely, strip_down, no or cannot_tell, as the notes' \"How to decide\" says;\n"
    "- photo: the photo that shows the cue, exactly as labelled (P03), or null;\n"
    "- box_2d: [y_min, x_min, y_max, x_max] on 0-1000 round the candidate's own damage in that photo, only with "
    "seen; else null;\n"
    "- reason: what you see, at most 25 words, no plate or other numbers.\n"
    "Judge only the candidates listed, by their ids. Never add a part, never change a candidate's side, never "
    "judge the other vehicle. LEFT and RIGHT are the car's own sides as its driver sees them. cannot_tell is a "
    "good answer when the photos don't show enough.\n"
    "\n"
    "The surveyor's notes for this area:\n"
)


# ------------------------------------------------------------ the switch


def active_mode() -> str | None:
    """"on" or "shadow" when the linked check runs (settings.intake_link_check
    "lite" and the linked parts not off), else None: no call, no write."""
    if getattr(settings, "intake_link_check", "off") != "lite":
        return None
    mode = getattr(settings, "intake_links", "off")
    return mode if mode in ("on", "shadow") else None


# ------------------------------------------------------------ what to ask


@dataclass
class Photo:
    photo_id: str
    label: str
    path: str  # what the AI is sent: the 1600 px preview, else the original
    raw: bool  # no preview: a box may be on a rotated frame


@dataclass
class Candidate:
    ref: str  # C1, C2... within its call
    key: str
    line: str  # how the call names it


@dataclass
class Group:
    """One call: an area's open linked parts, their listed parents, and the
    area's photos (camera order)."""
    area: str
    skill: str
    candidates: list[Candidate] = field(default_factory=list)
    parent_lines: list[str] = field(default_factory=list)
    photos: list[Photo] = field(default_factory=list)


@dataclass
class Plan:
    groups: list[Group]  # the calls to make, in order
    total: int  # the areas holding open linked parts


def _key(item: dict) -> str:
    return str(item.get("key") or item.get("part") or "")


def rule_id(link_rule) -> str | None:
    """"L1:rear-bumper>beam" -> "rear-bumper>beam" (the table's version
    before the first colon)."""
    text = str(link_rule or "").strip()
    return (text.split(":", 1)[1] if ":" in text else text) or None


def rule_areas(table: dict | None) -> dict[str, str]:
    """{link id: its area} from the link table."""
    links = table.get("links") if isinstance(table, dict) else None
    return {str(link.get("id")): str(link.get("area")) for link in links or []
            if isinstance(link, dict) and link.get("id") and link.get("area")}


def _side(part) -> str | None:
    from app.reports.taxonomy import split_side

    return split_side(str(part or ""))[1]


def area_of(item: dict, parents: list[dict], areas: dict[str, str]) -> str | None:
    """A linked part's area: its link's in the table (by link_rule), else
    the zone of the listed part it hangs off (zones.home_zone), else its
    own; a side area ("side", "left", "right") made "left" / "right" by the
    part's own side, else its parent's, else "side". None: no area."""
    from app.intake.zones import home_zone

    area = areas.get(rule_id(item.get("link_rule")) or "")
    if area not in SKILL_OF:
        parts = [p.get("part") for p in parents] + [item.get("part")]
        area = next((zone for zone in (home_zone(p) for p in parts if p) if zone in SKILL_OF), None)
    if area in ("side", "left", "right"):
        side = _side(item.get("part")) or next((s for s in (_side(p.get("part")) for p in parents) if s), None)
        area = side.lower() if side else "side"
    return area


def open_link_items(items: list[dict], data: dict, mode: str) -> list[dict]:
    """The linked parts to check: source "link"; on the Review step ("on")
    only those still open -- one a person dismissed or put on the list is
    never asked about (second_look.split_settled)."""
    linked = [it for it in items if isinstance(it, dict) and it.get("part") and it.get("source") == "link"]
    return split_settled(linked, data)[0] if mode == "on" else linked


def _refs(item: dict) -> list[str]:
    refs = [item.get("photo_reference")] + list(item.get("seen_in") or [])
    refs += [e.get("photo") for e in item.get("evidence") or [] if isinstance(e, dict)]
    return [r for r in refs if isinstance(r, str) and r]


def _photo_id(ref: str, by_label: dict[str, str]) -> str | None:
    if ref.startswith("@"):
        return ref[1:]
    match = _LABEL_RE.match(ref.strip().upper())
    return by_label.get(f"P{int(match.group(1)):02d}") if match else None


def _zones_of(area: str, sides: set[str]) -> set[str]:
    if area == "wheel":
        return {s.lower() for s in sides} or {"left", "right"}
    return _AREA_ZONES.get(area, set())


def pick_photos(area: str, sides: set[str], photos: list, own: set[str]) -> list:
    """An area's photos (IntakePhoto rows in camera order), at most
    MAX_PHOTOS, in camera order: the car photos (check.is_car_photo) the
    zone reads would read for that area (zones.photo_zones), the
    underside's (sorter area "underbody"), the wheels' (their side zones
    and tyre photos with no side), and the photos of the parts themselves
    and of their listed parents (own). Damage first -- the parents' and the
    parts' own first, then the area's, every damaged part one photo before
    any a second (pipeline._damage_order) -- then their other photos, then
    the overviews, then the rest."""
    from app.ai.check import is_car_photo
    from app.intake.pipeline import _by_tiers, _damage_order
    from app.intake.zones import photo_zones, shows_damage

    wanted = _zones_of(area, sides)
    pool = []
    for p in photos:
        if not is_car_photo(p) or not Path(p.path).exists():
            continue
        in_area = bool(set(photo_zones(p)) & wanted)
        if area == "underbody":
            in_area = p.area == "underbody"
        elif area == "wheel" and p.kind == "tyre" and _SIDE_OF_AREA.get(p.area) is None:
            in_area = True
        if in_area or p.id in own:
            pool.append(p)
    damage = [p for p in pool if shows_damage(p)]
    rest = [p for p in pool if not shows_damage(p)]
    ranked = (_damage_order([p for p in damage if p.id in own]) + _damage_order([p for p in damage if p.id not in own])
              + _by_tiers([p for p in rest if p.id in own])
              + _by_tiers([p for p in rest if p.id not in own and p.kind == "overview"])
              + _by_tiers([p for p in rest if p.id not in own and p.kind != "overview"]))
    # The five lists are apart: no photo comes twice.
    chosen = ranked[:MAX_PHOTOS]
    order = {p.id: i for i, p in enumerate(photos)}
    return sorted(chosen, key=lambda p: order[p.id])


@lru_cache(maxsize=32)
def skill_text(name: str, version: str = "L1", signs: bool = False) -> str | None:
    """ai/skills/<name>.md as written (version "L2": ai/skills-L2/, and with
    signs its <name>.signs.md), or None when there is no such file."""
    folder = SKILL_DIRS.get(version)
    if folder is None:
        return None
    try:
        return (folder / f"{name}{'.signs' if signs else ''}.md").read_text(encoding="utf-8").strip()
    except OSError:
        return None


def skill_choice() -> tuple[str, bool]:
    """(skill version, signs) as the settings say: settings.intake_link_skill
    and intake_link_signs (only with the L2 skills, whose files have it)."""
    version = getattr(settings, "intake_link_skill", "L1") or "L1"
    return version, version == "L2" and bool(getattr(settings, "intake_link_signs", False))


def thinking_high() -> bool:
    """settings.gemini_thinking_level_link gives a model of the link chain
    "high" (one time box covers the chain's tries)."""
    levels = thinking_levels(getattr(settings, "gemini_thinking_level_link", None))
    return any((levels.get(model) or levels.get(ANY)) == "high" for model in chain(settings.gemini_link_chain))


def time_box() -> float:
    """The call's time box: TIME_BOX_THINKING while thinking_high, else
    TIME_BOX."""
    return TIME_BOX_THINKING if thinking_high() else TIME_BOX


def _parent_name(item: dict) -> str:
    from app.reports.taxonomy import OTHER_PART

    part = str(item.get("part") or "")
    return str(item.get("other_part_name") or part) if part == OTHER_PART else part


_HIT_RANK = {"light": 0, "medium": 1, "heavy": 2, "crush": 3}


def parent_line(key: str, parent: dict | None, hit: str | None, labels: list[str]) -> str:
    """"- Rear Bumper: hit heavy (Dented, Cracked; Severe; Replace) --
    photos P03, P04": the listed part, how hard the links say it was hit,
    its own words, and the photos sent that show it."""
    name = _parent_name(parent) if parent else key
    said = ", ".join(str(t) for t in (parent or {}).get("condition_tokens") or [] if isinstance(t, str))
    extra = [x for x in (said, (parent or {}).get("severity"), (parent or {}).get("remarks")) if isinstance(x, str) and x]
    line = f"- {name}: hit {hit or 'not known'}" + (f" ({'; '.join(extra)})" if extra else "")
    return line + (f" -- photo{'s' if len(labels) > 1 else ''} {', '.join(labels)}" if labels else "")


def candidate_line(ref: str, item: dict, parent_names: list[str]) -> str:
    """"- C1: Rear Bumper Beam -- behind the Rear Bumper; hidden; confirm at
    strip-down": the part by its name, its side as given."""
    how = TYPE_WORDS.get(str(item.get("link_type") or ""), "linked to")
    line = f"- {ref}: {item['part']}"
    if parent_names:
        line += f" -- {how} the {' and the '.join(parent_names)}"
    flags = (["hidden"] if item.get("hidden") else []) + (["confirm at strip-down"] if item.get("needs_strip_down") else [])
    return line + "".join(f"; {f}" for f in flags)


def plan_groups(items: list[dict], data: dict, photos: list, table: dict | None, max_calls: int) -> Plan:
    """The calls for these open linked parts over the draft's photos (rows
    in camera order): one per area that has photos, the areas with the most
    linked parts first (ties in GROUP_ORDER), at most max_calls. An area
    without photos, or without a skill file, is not asked."""
    from app.intake.photos import ai_input_path, photo_label
    from app.parts.prefill import item_key

    labels = {p.id: photo_label(i) for i, p in enumerate(photos)}
    by_label = {label: photo_id for photo_id, label in labels.items()}
    listed = {item_key(it): it for it in data.get("damage_summary") or [] if isinstance(it, dict)}
    areas = rule_areas(table)
    grouped: dict[str, list[dict]] = {}
    for item in items:
        parents = [listed[k] for k in item.get("linked_from") or [] if k in listed]
        area = area_of(item, parents, areas)
        if area:
            grouped.setdefault(area, []).append(item)
    order = sorted(grouped, key=lambda a: (-len(grouped[a]), GROUP_ORDER.index(a)))
    groups: list[Group] = []
    for area in order:
        if len(groups) >= max_calls:
            break
        members = grouped[area][:MAX_CANDIDATES]
        skill = SKILL_OF[area]
        if skill_text(skill, *skill_choice()) is None:
            logger.warning("Linked check: no skill file %s.md; the %s area is not checked", skill, area)
            continue
        parent_keys = list(dict.fromkeys(k for it in members for k in it.get("linked_from") or []))
        own_refs = [r for it in members for r in _refs(it)] + [r for k in parent_keys for r in _refs(listed.get(k) or {})]
        own = {pid for pid in (_photo_id(r, by_label) for r in own_refs) if pid}
        sides = {s for it in members
                 for s in [_side(it.get("part"))] + [_side((listed.get(k) or {}).get("part")) for k in it.get("linked_from") or []]
                 if s}
        chosen = pick_photos(area, sides, photos, own)
        if not chosen:
            continue
        group = Group(area=area, skill=skill)
        for p in chosen:
            sent = ai_input_path(p.path)
            group.photos.append(Photo(p.id, labels[p.id], sent, sent == p.path))
        sent_ids = {p.photo_id: p.label for p in group.photos}
        for k in parent_keys:
            parent = listed.get(k)
            hit = max((it.get("hit") for it in members if k in (it.get("linked_from") or []) and it.get("hit") in _HIT_RANK),
                      key=lambda h: _HIT_RANK[h], default=None)
            shown = [sent_ids[pid] for pid in dict.fromkeys(_photo_id(r, by_label) for r in _refs(parent or {}))
                     if pid in sent_ids]
            group.parent_lines.append(parent_line(k, parent, hit, shown))
        for n, item in enumerate(members, 1):
            names = [_parent_name(listed[k]) if k in listed else k for k in item.get("linked_from") or []]
            group.candidates.append(Candidate(f"C{n}", _key(item), candidate_line(f"C{n}", item, names)))
        groups.append(group)
    return Plan(groups=groups, total=len(grouped))


# ------------------------------------------------------------ the call


# Light-model detection (P-prompts.md 3.4), each behind its setting:
# base prompt v2 (settings.intake_link_prompt), ai/prompts/link-v2.txt --
# work in order, the parents' hit first; its step 2 only with the signs
# field (L3), the next step renumbered without it.
_V2_SIGNS_STEP = ("2. In 'signs', answer each of the notes' \"Signs of hidden damage\" once: yes, no or cannot_see, "
                  "with the photo that shows a yes.\n")
SIGN_ANSWERS = ("yes", "no", "cannot_see")
# The signs a skill file numbers (at most 12 in each).
SIGN_NUMBERS = tuple(str(n) for n in range(1, 13))
# L4 (settings.intake_link_order "photos_first"): the closed list after the
# photos, as the last text part.
ORDERS = ("candidates_first", "photos_first")
CANDIDATES_LAST = "Based on the photos above, judge every candidate as the notes say."


def link_prompt() -> str:
    return getattr(settings, "intake_link_prompt", "v1") or "v1"


def link_order() -> str:
    order = getattr(settings, "intake_link_order", ORDERS[0])
    return order if order in ORDERS else ORDERS[0]


def base_prompt(prompt: str = "v1", signs: bool = False) -> str:
    """The prompt before the skill file: SYSTEM_PROMPT (v1), or link-v2's."""
    if prompt != "v2":
        return SYSTEM_PROMPT
    text = prompt_packs.template("link-v2") + "\n"
    if not signs:
        text = prompt_packs.once(text, _V2_SIGNS_STEP, "")
        text = prompt_packs.once(text, "3. Then answer every", "2. Then answer every")
    return text


def system_prompt(group: Group, prompt: str | None = None, version: str | None = None,
                  signs: bool | None = None) -> str:
    """The base prompt and the group's skill file (each None: as the
    settings say). At the defaults: SYSTEM_PROMPT + the L1 skill, as
    before."""
    if version is None or signs is None:
        chosen = skill_choice()
        version = chosen[0] if version is None else version
        signs = chosen[1] if signs is None else signs
    prompt = link_prompt() if prompt is None else prompt
    return base_prompt(prompt, signs) + (skill_text(group.skill, version, signs) or "")


def response_schema(refs: list[str], labels: list[str], signs: bool = False) -> dict:
    """One array; per candidate id the cue BEFORE the verdict (fields are
    filled in schema order: what it sees, then what it makes of it), then
    the photo, the box and the reason. No part and no side: neither is the
    model's to give. No minItems / maxItems.

    signs (L3): a `signs` array before `answers` -- each of the skill's
    signs by number, yes / no / cannot_see, the photo of a yes -- so they
    are answered before any candidate."""
    schema = _answers_schema(refs, labels)
    if not signs:
        return schema
    field = {"type": "array", "items": {"type": "object", "properties": {
        "n": {"type": "string", "enum": list(SIGN_NUMBERS)},
        "answer": {"type": "string", "enum": list(SIGN_ANSWERS)},
        "photo": {"anyOf": [{"type": "string", "enum": list(labels)}, {"type": "null"}]},
    }, "required": ["n", "answer", "photo"]}}
    return {"type": "object", "properties": {"signs": field, **schema["properties"]}, "required": ["signs", "answers"]}


def _answers_schema(refs: list[str], labels: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "answers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "enum": list(refs)},
                        "cue": {"type": "string", "enum": list(CUES)},
                        "verdict": {"type": "string", "enum": list(VERDICTS)},
                        "photo": {"anyOf": [{"type": "string", "enum": list(labels)}, {"type": "null"}]},
                        "box_2d": {"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "null"}]},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "cue", "verdict", "photo", "box_2d", "reason"],
                },
            },
        },
        "required": ["answers"],
    }


def build_input(group: Group, order: str = "candidates_first") -> list[dict]:
    """The area, the listed parents, the closed list, then the photos --
    "Photo P07:" before each, at RESOLUTION. order "photos_first" (L4): the
    closed list goes after the photos instead, in a last text part that
    ends with CANDIDATES_LAST."""
    candidates = ["Candidates to judge, by id (the closed list):", *[c.line for c in group.candidates]]
    last = order == "photos_first"
    lines = [f"Area: {AREA_WORDS[group.area]}.",
             "Parts already listed as damaged (the parents), and how hard each was hit:", *group.parent_lines,
             *([] if last else candidates),
             f"{len(group.photos)} photos follow, labelled with the case photo number."]
    parts: list[dict] = [{"type": "text", "text": "\n".join(lines)}]
    for photo in group.photos:
        parts.append({"type": "text", "text": f"Photo {photo.label}:"})
        image = image_part(photo.path)
        image["resolution"] = RESOLUTION
        parts.append(image)
    if last:
        parts.append({"type": "text", "text": "\n".join(candidates + [CANDIDATES_LAST])})
    return parts


def _short(text) -> str | None:
    """The reason in at most MAX_REASON_WORDS words, cleaned
    (evidence.clean_reason: no plate, VIN or photo label; 160 characters)."""
    if not isinstance(text, str):
        return None
    return evidence.clean_reason(" ".join(text.split()[:MAX_REASON_WORDS]))


def parse_answer(raw: str, refs: list[str], labels: list[str]) -> dict[str, dict]:
    """{ref: {"cue", "verdict", "photo" (a label sent, or None), "box",
    "reason"}}. Raises on a malformed answer (call_structured then asks the
    next model). An entry whose id is not on the list, or whose cue or
    verdict isn't one of ours, is dropped; each id once, the first kept;
    anything else the model wrote (a part, a side) ignored."""
    data = json.loads(raw)
    found = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(found, list):
        raise ValueError("the answer has no answers list")
    out: dict[str, dict] = {}
    for entry in found:
        if not isinstance(entry, dict):
            continue
        ref, cue, verdict = entry.get("id"), entry.get("cue"), entry.get("verdict")
        if ref not in refs or ref in out or cue not in CUES or verdict not in VERDICTS:
            continue
        match = _LABEL_RE.match(str(entry.get("photo") or "").strip().upper())
        label = f"P{int(match.group(1)):02d}" if match else None
        label = label if label in labels else None
        out[ref] = {"cue": cue, "verdict": verdict, "photo": label, "box": entry.get("box_2d") if label else None,
                    "reason": _short(entry.get("reason"))}
    return out


def parse_signs(raw: str, labels: list[str]) -> list[dict]:
    """L3's signs as [{"n", "answer", "photo" (a label sent, or None)}], by
    number, each once (the first kept); an entry with a number or answer not
    ours dropped. For the CheckRun's log only: no verdict depends on them."""
    data = json.loads(raw)
    found = data.get("signs") if isinstance(data, dict) else None
    out: dict[str, dict] = {}
    for entry in found if isinstance(found, list) else []:
        if not isinstance(entry, dict):
            continue
        n, answer = str(entry.get("n") or "").strip(), entry.get("answer")
        if n not in SIGN_NUMBERS or n in out or answer not in SIGN_ANSWERS:
            continue
        match = _LABEL_RE.match(str(entry.get("photo") or "").strip().upper())
        label = f"P{int(match.group(1)):02d}" if match else None
        out[n] = {"n": n, "answer": answer, "photo": label if label in labels else None}
    return sorted(out.values(), key=lambda s: int(s["n"]))


def code_verdict(answer: dict) -> str:
    """The verdict as code takes it: a seen or likely needs a sign it can
    point to (SIGN_CUES), else cannot_tell; a seen needs a photo it was
    sent, else likely."""
    verdict = answer["verdict"]
    if verdict in ("seen", "likely") and answer["cue"] not in SIGN_CUES:
        return "cannot_tell"
    if verdict == "seen" and not answer.get("photo"):
        return "likely"
    return verdict


def _answered_by(attempts: runs.Attempts, models: list[str]) -> str | None:
    ok = [a.get("model") for a in attempts.snapshot() if a.get("outcome") == "ok" and a.get("model") in models]
    return ok[-1] if ok else None


def ask(group: Group, actor: AiActor, attempts: runs.Attempts, signs_out: list | None = None) -> dict[str, dict]:
    """One area's call: {item key: {"link_check": LinkCheck dict, "entry":
    its Evidence by "link_check" or None}}. At most MAX_TRIES requests, none
    started after the time box (time_box: TIME_BOX, or TIME_BOX_THINKING
    with thinking "high"). Raises as call_structured does, or TimeoutError
    after the box. With the signs field on (L3), its answers are added to
    signs_out."""
    refs = [c.ref for c in group.candidates]
    labels = [p.label for p in group.photos]
    models = chain(settings.gemini_link_chain)
    # Read once: the box, the prompt and the schema of one call agree.
    box = time_box()
    version, signs = skill_choice()
    prompt, order = link_prompt(), link_order()
    system = system_prompt(group, prompt, version, signs)
    deadline = time.monotonic() + box
    tag = None
    if (prompt, version, signs, order) != ("v1", "L1", False, ORDERS[0]):
        named = [f"skill-{version}"] + (["signs"] if signs else []) + ([order] if order != ORDERS[0] else [])
        tag = prompt_packs.tag("link", prompt, named, system)

    def parse(raw: str):
        answers = parse_answer(raw, refs, labels)
        return (answers, parse_signs(raw, labels)) if signs else answers

    def work(stop: threading.Event):
        with runs.prompt_tag(tag):
            return call_structured(
                purpose=PURPOSE, system_instruction=system, input_parts=build_input(group, order),
                schema=response_schema(refs, labels, signs), parse=parse,
                models=_ChainUntil(models, stop), actor=actor, timeout=TIMEOUT if box == TIME_BOX else box,
                # Its own chain running out must not switch the person's key off
                # for the reading (structured.call_structured).
                judge_key=False, max_tries=MAX_TRIES, deadline=deadline,
            )

    got = _within(box, work, attempts)
    if signs:
        got, said = got
        if signs_out is not None:
            signs_out.extend(said)
    model = _answered_by(attempts, models)
    by_label = {p.label: p for p in group.photos}
    out: dict[str, dict] = {}
    for candidate in group.candidates:
        answer = got.get(candidate.ref)
        if answer is None:
            continue
        verdict = code_verdict(answer)
        entry = None
        photo = by_label.get(answer["photo"]) if answer["photo"] else None
        if verdict == "seen" and photo is not None:
            box = evidence.clean_box(answer["box"], raw_original=photo.raw)
            width, height = (evidence.frame_of(photo.path) or (None, None)) if box else (None, None)
            entry = {"photo": f"@{photo.photo_id}", "box": box, "box_kind": "ai" if box else None, "by": "link_check",
                     "reason": answer["reason"], "confidence": None, "img_w": width, "img_h": height}
        out[candidate.key] = {"link_check": {"verdict": verdict, "cue": answer["cue"], "model": model,
                                             "reason": answer["reason"]},
                              "entry": entry}
    return out


# ------------------------------------------------------------ the answers on the items


def _is_no(item: dict) -> bool:
    check = item.get("link_check")
    return isinstance(check, dict) and check.get("verdict") == "no"


def apply(items: list[dict], results: dict[str, dict], skip: set[str]) -> list[dict]:
    """The answers on these items (copies), by key: link_check, the bucket
    the verdict gives, a "seen" photo joined to the evidence (one entry per
    photo, a boxed one over a boxless one: evidence.join). Then each open
    linked part answered "no" goes after the other open linked parts --
    last in its group, whatever the grouping -- the rest keeping their
    places. Never an item more or less; `skip` (keys a person settled) and
    anything not a linked part left exactly as they are."""
    out: list[dict] = []
    open_at: list[int] = []
    for it in items:
        it = dict(it)
        key = _key(it)
        is_open = it.get("source") == "link" and key not in skip
        got = results.get(key) if is_open else None
        if got is not None:
            it["link_check"] = dict(got["link_check"])
            bucket = BUCKET_OF.get(got["link_check"]["verdict"])
            if bucket:
                it["bucket"] = bucket
                if bucket != "strip_down" and it.get("folded"):
                    # Lifted out of the strip-down block (a cue seen):
                    # no longer folded, so the counts agree with the screen.
                    it.pop("folded")
            if got.get("entry"):
                evidence.join(it, [got["entry"]])
        if is_open:
            open_at.append(len(out))
        out.append(it)
    moved = [out[i] for i in open_at if not _is_no(out[i])] + [out[i] for i in open_at if _is_no(out[i])]
    for i, it in zip(open_at, moved):
        out[i] = it
    return out


# ------------------------------------------------------------ the intake step


def _check_run(status: str, note: str | None = None, **fields) -> dict:
    return {"status": status, "by": None, "model": None, "note": note, "seconds": None, "calls": 0, **fields}


def _plan(report_id: str, mode: str, max_calls: int) -> Plan | None:
    """What to ask, from the draft as the linked parts left it. None when
    there is nothing to say: the draft is gone or filed, or has no open
    linked part."""
    from app.intake.links import TABLE
    from app.intake.photos import ordered_photos
    from app.reports.db import SessionLocal
    from app.reports.models import IntakeSession, Report

    db = SessionLocal()
    try:
        report = db.get(Report, report_id)
        if report is None or report.status != "intake":
            return None
        data = report.data or {}
        if mode == "on":
            items = (data.get("second_look") or {}).get("items") or []
        else:
            session = db.get(IntakeSession, report_id)
            items = ((session.ai_draft if session else None) or {}).get("link_suggestions") or []
        items = open_link_items(list(items), data, mode)
        if not items:
            return None
        table = TABLE
        if getattr(settings, "intake_car_map", "off") == "on":
            # The car map (K4): the areas are those of the table the linked
            # parts were built from (L1 when the map can't be used).
            from app.intake import car_map

            vehicle = data.get("vehicle_info") if isinstance(data.get("vehicle_info"), dict) else {}
            table = car_map.link_tables(TABLE, vehicle, mode="on").shown
        return plan_groups(items, data, ordered_photos(db, report_id), table, max_calls)
    finally:
        db.close()


def _all_busy(attempts: list[dict]) -> bool:
    tried = [a for a in attempts if a.get("outcome") != "day_spent"]
    return bool(tried) and all(a.get("busy") for a in tried)


def _ask_all(report_id: str, actor: AiActor, plan: Plan) -> tuple[dict, dict[str, dict]]:
    """(the CheckRun, {item key: its answer}) for one draft, as one run of
    the log. A failed call ends the step; the areas answered before it are
    kept."""
    results: dict[str, dict] = {}
    answered: list[Group] = []
    calls = 0
    failure: tuple[str, BaseException] | None = None
    models: list[str] = []
    # L3: each area's signs, for the CheckRun (only while the field is on).
    signs_on = skill_choice()[1]
    signs: dict[str, list[dict]] = {}
    try:
        with runs.recorded(report_id, runs.LINK_CHECK_KIND, actor) as run:
            sent: dict[str, str] = {}
            for group in plan.groups:
                before = len(run.attempts.snapshot())
                calls += 1
                for p in group.photos:
                    sent.setdefault(p.label, p.photo_id)
                said: list[dict] = []
                try:
                    got = ask(group, actor, run.attempts, said if signs_on else None)
                except AiUnavailable as exc:
                    failure = ("skipped", exc)
                    break
                except Exception as exc:
                    failure = ("busy" if _all_busy(run.attempts.snapshot()[before:]) else "failed", exc)
                    logger.warning("Linked check for %s: the %s call failed (%s)", report_id, group.area,
                                   runs.scrub_error(f"{type(exc).__name__}: {exc}"))
                    break
                answered.append(group)
                results.update(got)
                if signs_on:
                    signs[group.area] = said
                models += [r["link_check"]["model"] for r in got.values() if r["link_check"].get("model")]
            # As "@<photo id>", in camera order, as the re-check's rows:
            # filing turns them into the case's P-numbers
            # (runs.relabel_check_runs).
            run.photos_sent = [f"@{sent[label]}" for label in sorted(sent, key=lambda lb: int(lb[1:]))]
            run.parts_found = sum(1 for r in results.values() if r["link_check"]["verdict"] == "seen")
            if not answered and failure is not None:
                raise failure[1]
    except AiUnavailable as exc:
        logger.info("Linked check for %s skipped: %s", report_id, exc.code)
        return _check_run("skipped", NOTE_NO_ALLOWANCE, calls=calls), {}
    except Exception as exc:
        if failure is None:  # not a call's failure: the run log's, or ours
            logger.warning("Linked check for %s failed: %s", report_id, runs.scrub_error(f"{type(exc).__name__}: {exc}"))
        return _check_run("failed", NOTE_BUSY if failure and failure[0] == "busy" else NOTE_FAILED, calls=calls), {}
    notes = []
    if len(answered) < plan.total:
        notes.append(NOTE_PART.format(done=len(answered), total=plan.total))
    if failure is not None:
        notes.append({"skipped": NOTE_NO_ALLOWANCE, "busy": NOTE_BUSY}.get(failure[0], NOTE_FAILED))
    extra = {"signs": signs} if signs_on else {}
    return _check_run("ok", " ".join(notes) or None, by="lite", model=models[0] if models else None, calls=calls,
                      **extra), results


def _hold_write_lock(db, report_id: str) -> None:
    """SQLite's write lock for the rest of the transaction, taken with a
    write that changes nothing before the case is read -- as
    api/intake.hold_write_lock, which every decide endpoint takes: a
    decision committed meanwhile is read here, and one made after waits
    for this commit."""
    from sqlalchemy import update

    from app.reports.models import Report

    db.execute(update(Report).where(Report.id == report_id).values(pdf_path=Report.pdf_path)
               .execution_options(synchronize_session=False))


def _store(report_id: str, mode: str, outcome: dict, results: dict[str, dict]) -> None:
    """Writes the answers onto the linked parts as they are now (apply) and
    the step's CheckRun: on "on", into data["second_look"] (items and
    link_run), what a person settled meanwhile left as they decided it; on
    "shadow", into the AI draft only (ai_draft["link_suggestions"] and
    ai_draft["link_run"]). Under the write lock, read after taking it."""
    from app.reports.db import SessionLocal
    from app.reports.models import IntakeSession, Report
    from app.reports.schema import CheckRun, LinkCheck, SecondLook

    db = SessionLocal()
    try:
        _hold_write_lock(db, report_id)
        report = db.get(Report, report_id)
        if report is None or report.status != "intake":
            return
        data = dict(report.data or {})
        if mode == "on":
            look = data.get("second_look")
            if not isinstance(look, dict):
                return
            items = [dict(it) for it in look.get("items") or [] if isinstance(it, dict)]
            settled = {_key(it) for it in split_settled([it for it in items if it.get("part")], data)[1]}
            stored = SecondLook.model_validate({**look, "items": apply(items, results, settled), "link_run": outcome})
            report.data = {**data, "second_look": stored.model_dump()}
        else:
            session = db.get(IntakeSession, report_id)
            if session is None:
                return
            ai_draft = dict(session.ai_draft or {})
            items = apply([dict(it) for it in ai_draft.get("link_suggestions") or [] if isinstance(it, dict)],
                          results, set())
            for it in items:
                if isinstance(it.get("link_check"), dict):
                    it["link_check"] = LinkCheck.model_validate(it["link_check"]).model_dump()
            session.ai_draft = {**ai_draft, "link_suggestions": items,
                                "link_run": CheckRun.model_validate(outcome).model_dump()}
        db.commit()
    finally:
        db.close()


def run_for_intake(report_id: str, actor: AiActor) -> None:
    """The linked check for a draft whose suggestions have just been stored
    (intake/pipeline._read: after second_look.run_for_intake, before
    check.run_for_intake). Never raises: a failed check leaves the linked
    parts as they are. Nothing at all -- no call, no write -- unless
    active_mode() says so, while intake_link_check_max_calls is 0, or when
    the draft has no open linked part."""
    mode = active_mode()
    max_calls = getattr(settings, "intake_link_check_max_calls", 0) or 0
    if mode is None or max_calls <= 0:
        return
    started = time.monotonic()
    try:
        plan = _plan(report_id, mode, max_calls)
    except Exception:
        logger.exception("Linked check for %s: couldn't read the draft", report_id)
        return
    if plan is None:
        return
    if not plan.groups:
        outcome, results = _check_run("skipped", NOTE_NO_PHOTOS), {}
    else:
        try:
            outcome, results = _ask_all(report_id, actor, plan)
        except Exception:
            logger.exception("Linked check for %s failed", report_id)
            outcome, results = _check_run("failed", NOTE_FAILED), {}
    outcome["seconds"] = round(time.monotonic() - started, 1)
    try:
        _store(report_id, mode, outcome, results)
    except Exception:
        logger.exception("Linked check for %s: couldn't save its answer", report_id)
