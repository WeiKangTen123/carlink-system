"""A second look after the intake reading: ONE Gemini call that suggests more
damaged parts for a person to tick.

Why: the reading (ai/extraction.draft_report) drafts the whole report from
up to 32 photos in one call. On four real cases it found about 19 of the 67
parts the surveyors claimed, and most misses were sub-parts and hidden or
adjacent damage around the areas it did find -- the tailgate's lock and
hinges beside the tailgate, the reflector beside the bumper (detection
design review, 2026-11-05, item 2 (a)/(b)). This call looks only there:
close-ups of the damaged areas, the parts already found, and candidate
parts -- what past cases of the same make wrote with them (parts/history.
suggest's "often replaced with" families) and the parts next to them on the
car (ZONES) -- and asks which of those show damage, in which photo.

Suggestions, never findings: the answer is kept on the draft as
data["second_look"] (reports/schema.SecondLook) and listed on the Review
step (dashboard components/intake/SecondLookPanel.tsx). It reaches
damage_summary, and from there Annex A, only through a person's tick.
Parts already listed are dropped; parts a person dismissed are never
suggested again on that draft.

Budget: free-tier keys allow few strong-model calls a day, so this is one
call per reading (a re-read runs it again) on its own chain
(settings.gemini_second_look_chain), inside TIME_BOX. A failure, a timeout
or no allowance never fails the intake: status "skipped" or "failed" with a
quiet note. The AI run log keeps it as kind "second_look".

What reaches Gemini is less than the reading sends: case photos the sorting
step has already sent (never an excluded one), the make, model and body
type, part names and the sorter's own notes -- never the reporter's notes,
names or contact details, a public sender's included. The run's actor is
the reading's, so a public report's stricter key rule (keys.resolve_keys,
public_case) applies to this call too.
"""
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TypeVar

from sqlalchemy import func

from app.ai import allowance, runs
from app.ai.keys import AiActor, AiUnavailable
from app.ai.structured import call_structured, image_part
from app.config import settings
from app.reports.taxonomy import (
    CANONICAL_PARTS,
    FIRM_CONDITION_TOKENS,
    UNDETERMINED_SUFFIX,
    damage_type_from_tokens,
    firm_condition,
    unsided_part,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# The whole step, from the call's start to its answer, however many models
# the chain tries: the reading is saved already and the wizard waits on
# this before it shows the draft.
TIME_BOX = 90.0
# One model try. The reading's 32-photo call answers in about 20 s.
CALL_TIMEOUT = 60.0
# Photos per call: half the reading's 32, all close-ups of the damaged
# areas -- ~4 MB of previews, one request, one free-tier call.
MAX_PHOTOS = 16
# The part enum's length. A schema Gemini finds too complex fails every call
# with 400 (2ae2183); the sorter's 87-name enum works, this stays well under.
MAX_CANDIDATES = 40
# What the Review step lists: past this, more ticks to read than parts found.
MAX_SUGGESTIONS = 10
# Past wordings shown per kit candidate ("Tail gate hinge LH").
MAX_HINTS = 3
CONFIDENCES = ["High", "Medium", "Low"]

NOTE_OFF = "The second look is switched off."
NOTE_NOTHING_FOUND = "The reading found no damaged parts to look around."
NOTE_NO_CANDIDATES = "No other parts near the damage found to check."
NOTE_NO_PHOTOS = "No photos of the damaged areas to look at again."
NOTE_NO_ALLOWANCE = "No AI key or allowance left for a second look right now."
NOTE_TIMEOUT = f"The second look got no answer within {TIME_BOX:.0f} s. Re-read to try again."
NOTE_FAILED = "The second look didn't work this time. Re-read to try again."

# ------------------------------------------------------------ the car's zones
#
# Which parts sit together, for "close taxonomy neighbours": a part found in
# a zone makes the zone's other parts candidates. The sides hold only what
# is on the side itself -- a rear lamp is "rear" -- so a damaged tail lamp
# doesn't make every door a candidate.
ZONES: dict[str, list[str]] = {
    "rear": [
        "Rear Bumper", "Rear Bumper Beam", "Left Rear Bumper Reflector", "Right Rear Bumper Reflector",
        "Boot Lid", "Boot Lid Lock", "Boot Lid Hinge", "Boot Lid Weatherstrip", "Boot Lid Inner Trim",
        "Left Boot Lid Lamp", "Right Boot Lid Lamp", "Left Tail Lamp", "Right Tail Lamp", "Rear End Panel",
        "Rear Windscreen", "Rear Number Plate", "Boot Floor Board", "Left Boot Side Trim", "Right Boot Side Trim",
        "Left Rear Quarter Panel", "Right Rear Quarter Panel", "Left Rear Wheel Arch Liner",
        "Right Rear Wheel Arch Liner", "Left Rear Wheel Arch Trim", "Right Rear Wheel Arch Trim", "Rear Floor Panel",
        "Parking Sensor", "Reversing Camera", "Exhaust System",
    ],
    "front": [
        "Front Bumper", "Front Bumper Beam", "Front Grille", "Bonnet", "Left Headlamp", "Right Headlamp",
        "Left Fog Lamp", "Right Fog Lamp", "Front Number Plate", "Left Front Fender", "Right Front Fender",
        "Left Front Wheel Arch Liner", "Right Front Wheel Arch Liner", "Left Front Wheel Arch Trim",
        "Right Front Wheel Arch Trim", "Front Windscreen", "Engine Undercover", "Radiator",
        "Air Conditioning Condenser", "Front Subframe", "Parking Sensor",
    ],
    # The window regulators are inside the doors, the tyres on the wheels,
    # the arch trims around them: a dented door or a kerbed rim makes them
    # candidates (the regulator, tyre and arch trim are priced apart).
    "left": [
        "Left Front Fender", "Left Front Door", "Left Rear Door", "Left Front Door Glass", "Left Rear Door Glass",
        "Left Front Window Regulator", "Left Rear Window Regulator", "Left Wing Mirror", "Left Sill / Rocker Panel",
        "Left Rear Quarter Panel", "Left Front Wheel", "Left Rear Wheel", "Left Front Tyre", "Left Rear Tyre",
        "Left Front Wheel Arch Liner", "Left Rear Wheel Arch Liner", "Left Front Wheel Arch Trim",
        "Left Rear Wheel Arch Trim",
    ],
    "right": [
        "Right Front Fender", "Right Front Door", "Right Rear Door", "Right Front Door Glass", "Right Rear Door Glass",
        "Right Front Window Regulator", "Right Rear Window Regulator", "Right Wing Mirror",
        "Right Sill / Rocker Panel", "Right Rear Quarter Panel", "Right Front Wheel", "Right Rear Wheel",
        "Right Front Tyre", "Right Rear Tyre", "Right Front Wheel Arch Liner", "Right Rear Wheel Arch Liner",
        "Right Front Wheel Arch Trim", "Right Rear Wheel Arch Trim",
    ],
}
_SIDE_ZONES = {"left", "right"}
# The sorter's photo areas (ai/triage.AREAS) -> zones.
_AREA_ZONES: dict[str, set[str]] = {
    "front": {"front"}, "front-left": {"front", "left"}, "front-right": {"front", "right"},
    "rear": {"rear"}, "rear-left": {"rear", "left"}, "rear-right": {"rear", "right"},
    "left": {"left"}, "right": {"right"},
}
# Kinds whose photo counts as damage evidence when it shows damage and names
# a part (pipeline._DAMAGE_EVIDENCE_KINDS: a boot photo the sorter called
# interior); an "other" photo showing damage counts with no part, as in the
# reading (pipeline._is_damage_evidence): the sorter can't name a part off
# the list, so a removed window regulator's photo was never looked at again.
_EVIDENCE_KINDS = {"interior", "other"}
_NOT_A_PART = "Other / Not Listed"
_LABEL_RE = re.compile(r"^P0*(\d+)$")


def _zones_of(part: str) -> set[str]:
    """The zones a canonical part is in. A "(side undetermined)" name takes
    its sided names' end of the car (rear, front), or the sides when that is
    all they have (doors)."""
    zones = {zone for zone, parts in ZONES.items() if part in parts}
    if zones or not part.endswith(UNDETERMINED_SUFFIX):
        return zones
    for sided in CANONICAL_PARTS:
        if sided != part and unsided_part(sided) == part:
            zones |= _zones_of(sided)
    return (zones - _SIDE_ZONES) or zones


# ------------------------------------------------------------ already listed


def covers_of(part: str) -> list[str]:
    """The damage-list names that mean `part` is listed already: the part
    itself, and its family's "(side undetermined)" name -- or, for a
    side-undetermined part, every sided name of its family. "Left Tail
    Lamp" is listed by "Tail Lamp (side undetermined)" (the reading saw a
    tail lamp it couldn't place) but not by "Right Tail Lamp"."""
    family = unsided_part(part) or part
    if part.endswith(UNDETERMINED_SUFFIX):
        return [p for p in CANONICAL_PARTS if (unsided_part(p) or p) == family]
    return [part] if family == part else [part, family]


def listed_parts(data: dict) -> set[str]:
    """The draft's damaged parts as canonical names: damage_summary, else
    the older damaged_parts list. Free text from a case filed before the
    taxonomy is read with the past-report rules (history.canonical_part)."""
    from app.parts.history import canonical_part

    items = [it.get("part") for it in (data.get("damage_summary") or []) if isinstance(it, dict)]
    names = [p for p in items if p] or [p for p in (data.get("damaged_parts") or []) if p]
    return {canonical_part(str(p)) or str(p) for p in names}


def is_listed(part: str, listed: set[str]) -> bool:
    return bool(set(covers_of(part)) & listed)


# ------------------------------------------------------------ candidates


@dataclass
class Candidate:
    part: str
    source: str  # "kit" or "nearby"
    reason: str  # for the prompt
    hints: list[str] = field(default_factory=list)


def _sided_names(family: str) -> list[str]:
    """Canonical names of a past-case family (history.family_of's names):
    "Tail Lamp" -> Left, Right and the "(side undetermined)" name."""
    from app.parts.history import family_and_side

    names: list[str] = []
    for part in CANONICAL_PARTS:
        if part.endswith(UNDETERMINED_SUFFIX) or family_and_side(part)[0] != family:
            continue
        names.append(part)
        undetermined = unsided_part(part)
        if undetermined and undetermined != part and undetermined not in names:
            names.append(undetermined)
    return names


def _same_make_history(db, make: str | None) -> bool:
    """Whether the firm has done a case of this make before. history.suggest
    falls back to every imported case; a Toyota's kit is no guide to a
    Honda's, so the kit candidates are same-make only."""
    from app.reports.models import PastAnnexLine

    if not (make or "").strip():
        return False
    wanted = make.strip().lower()
    return db.query(PastAnnexLine.id).filter(func.lower(func.trim(PastAnnexLine.make)) == wanted).first() is not None


def candidates(db, *, make: str | None, model: str | None, listed: set[str], dismissed: set[str],
               sorter_parts: list[str]) -> tuple[list[Candidate], str | None]:
    """The parts to ask about, most likely first, at most MAX_CANDIDATES
    (the answer's enum), and the history scope when past cases of the same
    make supplied some ("past Honda Vezel cases"), else None.

    In order: the families past same-make cases had damaged with the parts
    found here (history.suggest's companions); then the parts the photo
    sorter named on a photo near the damage (it saw them; the reading didn't
    list them); then the other parts of each damaged zone. A sided family
    comes with its "(side undetermined)" name, the honest answer for a
    close-up that shows no side. Parts listed or dismissed are left out."""
    from app.parts.history import suggest

    out: dict[str, Candidate] = {}

    def add(part: str, source: str, reason: str, hints: list[str] | None = None) -> None:
        if part in out or part not in CANONICAL_PARTS or part == _NOT_A_PART:
            return
        if part in dismissed or is_listed(part, listed):
            return
        out[part] = Candidate(part, source, reason, list(hints or []))

    scope = None
    if _same_make_history(db, make):
        kit = suggest(db, make=make, model=model, damaged_parts=sorted(listed), existing_items=[])
        scope = kit["scope"]
        for group in kit["groups"]:
            if group["reason"] == "damaged":
                continue  # the found part's own lines: no new part
            lines = group["lines"]
            hints = [ln["name"] for ln in lines if ln.get("kind") == "part"] or [ln["name"] for ln in lines]
            for name in _sided_names(group["part"]):
                add(name, "kit", group["reason"], hints[:MAX_HINTS])

    zones: set[str] = set()
    for part in sorted(listed):
        zones |= _zones_of(part)
    for part in sorter_parts:
        if _zones_of(part) & zones:
            add(part, "nearby", "the photo sorter named it on a photo of the damaged area")
            undetermined = unsided_part(part)
            if undetermined:
                add(undetermined, "nearby", "the photo sorter named it on a photo of the damaged area")
    for zone in ("rear", "front", "left", "right"):
        if zone not in zones:
            continue
        for part in ZONES[zone]:
            add(part, "nearby", f"next to the damage found ({zone})")
            undetermined = unsided_part(part)
            if undetermined:
                add(undetermined, "nearby", f"next to the damage found ({zone})")
    return list(out.values())[:MAX_CANDIDATES], scope


# ------------------------------------------------------------ photos


def _photo_zones(photo) -> set[str]:
    zones = set(_AREA_ZONES.get(photo.area or "", ()))
    if photo.part and photo.part != _NOT_A_PART:
        zones |= _zones_of(photo.part)
    return zones


def _is_evidence(photo) -> bool:
    if photo.kind == "damage":
        return True
    if not photo.shows_damage or photo.kind not in _EVIDENCE_KINDS:
        return False
    return photo.kind == "other" or bool(photo.part)


def pick_photos(photos: list, *, zones: set[str], read_ids: set[str], candidate_parts: set[str],
                item_photo_ids: set[str]) -> list:
    """Up to MAX_PHOTOS damage photos of the damaged zones, in camera order.

    Taken in tiers: photos the sorter tagged with a candidate part (it saw
    the part; the reading didn't list it -- the likeliest misses); photos
    showing damage the reading was never sent; those it was sent; then the
    zone's other close-ups (the sorter misses slight damage too). Within a
    tier, one photo per sorter part (or area) in turn, so one well-covered
    part can't take every slot. Never an excluded or unusable photo."""
    tiers: dict[int, list] = {}
    for p in photos:
        if p.excluded or p.quality == "unusable":
            continue
        own_zones = _photo_zones(p)
        evidence = _is_evidence(p) or p.id in item_photo_ids
        if not evidence or not ((own_zones & zones) or (not own_zones and p.shows_damage)):
            continue
        if p.part in candidate_parts:
            tier = 0
        elif p.shows_damage and p.id not in read_ids:
            tier = 1
        elif p.shows_damage or p.id in item_photo_ids:
            tier = 2
        else:
            tier = 3
        tiers.setdefault(tier, []).append(p)

    picked: list = []
    for tier in sorted(tiers):
        groups: dict[str, list] = {}
        for p in tiers[tier]:
            groups.setdefault(p.part or f"area:{p.area}", []).append(p)
        queues = list(groups.values())
        while any(queues) and len(picked) < MAX_PHOTOS:
            for queue in queues:
                if queue and len(picked) < MAX_PHOTOS:
                    picked.append(queue.pop(0))
    order = {p.id: i for i, p in enumerate(photos)}
    return sorted(picked, key=lambda p: order[p.id])


# ------------------------------------------------------------ the call

SYSTEM_PROMPT = (
    "You take a second look at a damaged car's photos for a Singapore motor insurance loss adjuster. A first reading "
    "already listed some damaged parts. Surveyors usually also claim parts around those that a first reading misses: "
    "locks, hinges, seals, trims, brackets, reflectors, lamps and the panels next to or behind the damaged ones.\n"
    "Examine every photo closely for damage to the candidate parts (and any other allowed part): bent or distorted "
    "metal, a cracked or chipped lens, a torn or crushed seal, trim pushed out of line, paint cracked at a fold.\n"
    "Return one entry per part you can actually SEE damaged in one of these photos:\n"
    "- part: from the allowed list. LEFT and RIGHT are the vehicle's own sides as its driver sees them; use the "
    "'(side undetermined)' name when the photo doesn't show which side.\n"
    "- photo: the label of the photo that shows the damage best, exactly as given (e.g. P12).\n"
    "- note: at most 12 words on where and what the damage is.\n"
    "- condition_tokens: the surveyor's words for the damage, 1 to 3 of the allowed values -- Grazed for surface "
    "scuffs; Dented, Bent, Buckled, Deformed, Distorted, Crumpled, Warped for shape; Shifted or Slack for pushed out of "
    "place or loose; Cracked, Chipped, Torn, Cut for split or broken; Jammed, Faulty, Leaked, Rattle for no longer works.\n"
    "- confidence: High, Medium or Low -- how clearly the photo shows this damage.\n"
    "Never list a part only because past cases had it or because it is near the damage: the photo must show it "
    "damaged. Never list the parts already listed. An empty list is a good answer when nothing more is damaged."
)


def response_schema(parts: list[str]) -> dict:
    """Small on purpose and apart from the drafting schema (which the
    Telegram bot shares): one array of five fields, two short enums and the
    candidates' enum. No minItems / maxItems, no anyOf, no nulls -- each adds
    to what Gemini counts against a schema's limit (the 2ae2183 400s)."""
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "part": {"type": "string", "enum": list(parts)},
                        "photo": {"type": "string"},
                        "note": {"type": "string"},
                        "condition_tokens": {"type": "array", "items": {"type": "string", "enum": FIRM_CONDITION_TOKENS}},
                        "confidence": {"type": "string", "enum": CONFIDENCES},
                    },
                    "required": ["part", "photo", "condition_tokens", "confidence"],
                },
            },
        },
        "required": ["items"],
    }


@dataclass
class SentPhoto:
    label: str
    photo_id: str
    path: str
    area: str | None = None
    part: str | None = None
    note: str | None = None


def build_input(vehicle: dict, listed: set[str], cands: list[Candidate], sent: list[SentPhoto]) -> list[dict]:
    """The call's text and photos. Only the make, model and body type of the
    vehicle (never its plate, owner or the reporter's words), part names,
    and per photo where the sorter saw it taken, the part it named and its
    note (a model's words about a photo it was sent; scrubbed of anything
    plate- or number-like all the same)."""
    car = " ".join(str(vehicle.get(k)).strip() for k in ("make", "model") if vehicle.get(k))
    body = vehicle.get("body_type")
    lines = [f"Vehicle: {car or 'not known'}" + (f" ({body})" if body else "") + "."]
    lines.append("Already listed by the first reading -- do not list these: " + "; ".join(sorted(listed)) + ".")
    lines.append("Candidate parts to check, most likely first:")
    for c in cands:
        lines.append(f"- {c.part}: {c.reason}" + (f" (past wording: {'; '.join(c.hints)})" if c.hints else ""))
    lines.append(f"{len(sent)} photos follow, labelled with the case photo number. What the photo sorter noted on each:")
    for s in sent:
        said = [x for x in (s.area, s.part, runs.scrub_error(s.note) if s.note else None) if x]
        lines.append(f"- {s.label}: {', '.join(said) if said else 'nothing'}")
    parts: list[dict] = [{"type": "text", "text": "\n".join(lines)}]
    for s in sent:
        parts.append({"type": "text", "text": f"Photo {s.label}:"})
        parts.append(image_part(s.path))
    return parts


def parse_answer(raw: str, allowed: set[str], label_to_id: dict[str, str]) -> list[dict]:
    """The model's answer as suggestion dicts. Raises on a malformed answer
    (call_structured then tries the next model). Kept to the vocabulary:
    a part outside the candidates, or a photo it wasn't sent -- no evidence
    -- drops the entry; the firm's words only, at most three
    (taxonomy.firm_condition); each part once."""
    data = json.loads(raw)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("the answer has no items list")
    out: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        part = item.get("part")
        if part not in allowed or part in seen:
            continue
        match = _LABEL_RE.match(str(item.get("photo") or "").strip().upper())
        label = f"P{int(match.group(1)):02d}" if match else None
        photo_id = label_to_id.get(label) if label else None
        if not photo_id:
            continue
        tokens, condition = firm_condition(item.get("condition_tokens"), None)
        note = " ".join(str(item.get("note") or "").split())[:120] or None
        confidence = item.get("confidence")
        seen.add(part)
        out.append({
            "part": part,
            "photo_reference": f"@{photo_id}",
            "note": note,
            "condition_tokens": tokens,
            "condition": condition,
            "damage_type": damage_type_from_tokens(tokens),
            "confidence": confidence if confidence in CONFIDENCES else "Low",
        })
    return out


class _ChainUntil:
    """The model chain, cut short once `stop` is set. call_structured walks
    it once per key; a call the time box gave up on must not go on to spend
    another model's allowance in the background."""

    def __init__(self, models: list[str], stop: threading.Event):
        self._models = list(models)
        self._stop = stop

    def __iter__(self):
        for model in self._models:
            if self._stop.is_set():
                return
            yield model


def _chain() -> list[str]:
    return [m.strip() for m in settings.gemini_second_look_chain.split(",") if m.strip()]


def look(*, actor: AiActor, vehicle: dict, listed: set[str], cands: list[Candidate], sent: list[SentPhoto],
         stop: threading.Event | None = None) -> list[dict]:
    """The one Gemini call: suggestion dicts (parse_answer), each with the
    candidate's source. Raises as call_structured does."""
    allowed = [c.part for c in cands]
    source = {c.part: c.source for c in cands}
    label_to_id = {s.label: s.photo_id for s in sent}

    def parse(raw: str) -> list[dict]:
        found = parse_answer(raw, set(allowed), label_to_id)
        for item in found:
            item["source"] = source.get(item["part"])
        return found

    return call_structured(
        purpose="second look",
        system_instruction=SYSTEM_PROMPT,
        input_parts=build_input(vehicle, listed, cands, sent),
        schema=response_schema(allowed),
        parse=parse,
        models=_ChainUntil(_chain(), stop or threading.Event()),
        actor=actor,
        timeout=CALL_TIMEOUT,
        # Its own two models being used up must not switch the person's key
        # off for the reading (structured.call_structured).
        judge_key=False,
    )


def _within(seconds: float, work: Callable[[threading.Event], T], attempts: runs.Attempts) -> T:
    """work(stop) on a thread of its own, given up after `seconds` (raises
    TimeoutError and sets stop). A thread can't be killed: one given up on
    finishes its current try, unrecorded, and stops (_ChainUntil). The run
    log's collector goes with it -- thread-locals don't."""
    stop = threading.Event()
    done = threading.Event()
    box: dict = {}

    def run() -> None:
        try:
            with runs.collecting(attempts):
                box["result"] = work(stop)
        except BaseException as exc:  # handed to the caller below
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=run, name="second-look", daemon=True).start()
    if not done.wait(seconds):
        stop.set()
        raise TimeoutError(f"no answer within {seconds:.0f}s")
    if "error" in box:
        raise box["error"]
    return box["result"]


# ------------------------------------------------------------ the intake step


def _result(status: str, note: str | None = None, **fields) -> dict:
    return {"status": status, "note": note, "items": [], **fields}


def _plan(report_id: str) -> dict | None:
    """What the call needs, read from the draft as the reading left it.
    None when the draft is gone (deleted or filed meanwhile)."""
    from app.intake.photos import ai_input_path, ordered_photos, photo_label
    from app.reports.db import SessionLocal
    from app.reports.models import IntakeSession, Report

    db = SessionLocal()
    try:
        report = db.get(Report, report_id)
        session = db.get(IntakeSession, report_id)
        if report is None or session is None or report.status != "intake":
            return None
        data = report.data or {}
        listed = listed_parts(data)
        if not listed:
            return {"result": _result("skipped", NOTE_NOTHING_FOUND)}
        vehicle = data.get("vehicle_info") or {}
        dismissed = set(((data.get("second_look") or {}).get("dismissed")) or [])
        photos = ordered_photos(db, report_id)
        usable = [p for p in photos if not p.excluded and p.quality != "unusable"]
        cands, scope = candidates(
            db, make=vehicle.get("make"), model=vehicle.get("model"), listed=listed, dismissed=dismissed,
            sorter_parts=[p.part for p in usable if p.part],
        )
        if not cands:
            return {"result": _result("skipped", NOTE_NO_CANDIDATES, scope=scope)}
        zones: set[str] = set()
        for part in listed:
            zones |= _zones_of(part)
        item_photo_ids = {
            str(it.get("photo_reference"))[1:] for it in (data.get("damage_summary") or [])
            if isinstance(it, dict) and str(it.get("photo_reference") or "").startswith("@")
        }
        picked = pick_photos(
            photos, zones=zones, read_ids=set(session.read_photo_ids or []),
            candidate_parts={c.part for c in cands}, item_photo_ids=item_photo_ids,
        )
        labels = {p.id: photo_label(i) for i, p in enumerate(photos)}
        sent = [
            SentPhoto(labels[p.id], p.id, ai_input_path(p.path), p.area, p.part, p.note)
            for p in picked if Path(p.path).exists()
        ]
        if not sent:
            return {"result": _result("skipped", NOTE_NO_PHOTOS, scope=scope)}
        return {
            "vehicle": {k: vehicle.get(k) for k in ("make", "model", "body_type")},
            "listed": listed, "cands": cands, "scope": scope, "sent": sent,
        }
    finally:
        db.close()


def _model_used(run_id: str | None) -> str | None:
    """The model that answered, as the run log recorded it."""
    if not run_id:
        return None
    from app.reports.db import SessionLocal
    from app.reports.models import AiRun

    db = SessionLocal()
    try:
        row = db.get(AiRun, run_id)
        return row.model_used if row else None
    finally:
        db.close()


def _ask(report_id: str, actor: AiActor) -> dict | None:
    if not settings.second_look_enabled:
        return _result("skipped", NOTE_OFF)
    plan = _plan(report_id)
    if plan is None:
        return None
    if "result" in plan:
        return plan["result"]  # nothing to ask about
    sent, cands = plan["sent"], plan["cands"]
    meta = {"scope": plan["scope"], "photos_sent": len(sent), "candidates": len(cands)}
    run_id = None
    try:
        with runs.recorded(report_id, "second_look", actor) as run:
            run_id = run.id
            run.photos_sent = [s.label for s in sent]
            items = _within(
                TIME_BOX,
                lambda stop: look(actor=actor, vehicle=plan["vehicle"], listed=plan["listed"], cands=cands,
                                  sent=sent, stop=stop),
                run.attempts,
            )
            run.parts_found = len(items)
    except AiUnavailable as exc:
        logger.info("Second look for %s skipped: %s", report_id, exc.code)
        return _result("skipped", NOTE_NO_ALLOWANCE, **meta)
    except TimeoutError:
        logger.warning("Second look for %s: no answer within %.0fs", report_id, TIME_BOX)
        return _result("failed", NOTE_TIMEOUT, **meta)
    except Exception as exc:
        if allowance.day_spent_error(exc):
            # Today's allowance on its models is gone: a re-read would only
            # spend a reading for nothing before Google's reset.
            logger.info("Second look for %s skipped: its models' free day is used up", report_id)
            return _result("skipped", NOTE_NO_ALLOWANCE, **meta)
        # Scrubbed: the error can quote the model's answer.
        logger.warning("Second look for %s failed: %s", report_id, runs.scrub_error(str(exc)))
        return _result("failed", NOTE_FAILED, **meta)
    return {"status": "ok", "note": None, "items": items, "model": _model_used(run_id), **meta}


_RANK = {c: i for i, c in enumerate(CONFIDENCES)}


def _store(report_id: str, result: dict) -> None:
    """Writes the answer onto the draft as data["second_look"], against the
    form as it is now: suggestions for parts listed (or dismissed) by then
    are dropped, the rest named in the firm's words, surest first, at most
    MAX_SUGGESTIONS. The dismissed list is kept (pipeline.merge_redraft
    carries it over a re-read)."""
    from app.parts.prefill import row_name
    from app.reports.db import SessionLocal
    from app.reports.models import IntakeSession, Report
    from app.reports.schema import SecondLook

    db = SessionLocal()
    try:
        report = db.get(Report, report_id)
        session = db.get(IntakeSession, report_id)
        if report is None or session is None or report.status != "intake":
            return
        data = dict(report.data or {})
        dismissed = list(((data.get("second_look") or {}).get("dismissed")) or [])
        listed = listed_parts(data)
        vehicle = data.get("vehicle_info") or {}
        items = [
            it for it in result.get("items") or []
            if it["part"] not in dismissed and not is_listed(it["part"], listed)
        ]
        items.sort(key=lambda it: (_RANK.get(it.get("confidence"), len(CONFIDENCES)), it.get("source") != "kit"))
        kept: list[dict] = []
        for it in items:
            # "Left Tail Lamp" and "Tail Lamp (side undetermined)" both: one
            # lamp seen twice, the surer kept -- ticking both would list it twice.
            if is_listed(it["part"], {k["part"] for k in kept}):
                continue
            it["name"] = row_name(db, it["part"], vehicle)
            it["covers"] = covers_of(it["part"])
            kept.append(it)
        items = kept
        stored = SecondLook(
            status=result["status"], model=result.get("model"), note=result.get("note"),
            scope=result.get("scope"), seconds=result.get("seconds"),
            photos_sent=result.get("photos_sent") or 0, candidates=result.get("candidates") or 0,
            items=items[:MAX_SUGGESTIONS], dismissed=dismissed,
        )
        report.data = {**data, "second_look": stored.model_dump()}
        db.commit()
    finally:
        db.close()


def run_for_intake(report_id: str, actor: AiActor) -> None:
    """The second look for a draft the reading has just saved
    (intake/pipeline._read). Never raises and never takes much longer than
    TIME_BOX: whatever happens, the draft keeps the reading, and
    data["second_look"] says what became of this."""
    started = time.monotonic()
    try:
        result = _ask(report_id, actor)
    except Exception:
        logger.exception("Second look for %s failed", report_id)
        result = _result("failed", NOTE_FAILED)
    if result is None:
        return  # the draft is gone
    result["seconds"] = round(time.monotonic() - started, 1)
    try:
        _store(report_id, result)
    except Exception:
        logger.exception("Second look for %s: couldn't save its answer", report_id)
