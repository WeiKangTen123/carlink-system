"""The re-check of a draft's suggestions (contract 9 section 4.7): after the
second look has stored them (second_look._store), one question per
suggestion -- is this part of the insured car damaged in these photos? --
on the Decisions API (ai/decisions.py: gemini-decision-1, its own free
bucket, ~1 s, no output tokens), with one batched lite call as the safety
net (settings.intake_check_mode):

    "off"                 nothing at all: no call, no write (the default)
    "decision"            the Decisions API alone
    "lite"                one call on settings.gemini_check_chain alone
    "decision_then_lite"  the lite call for what the Decisions API didn't
                          answer because it failed (a 400 "safety", a 429,
                          a 5xx, a timeout, no probabilities)

It labels and sorts the suggestions; it never adds or removes one and never
touches the damage list. Its answer goes on each suggestion
(SecondLookItem.check: "yes" / "no" / "unsure", with the Decisions API's
yes-probability p or the lite model's reason) and on the step
(SecondLook.check, a CheckRun: what ran, and the note the panel shows).
With answers, the suggestions are sorted: "yes" (p high to low), then
"unsure" and the unchecked, then "no" (p low to high), ties by the reads'
confidence; without, they keep their order.

What is sent: each suggestion's best one or two photos -- its evidence in
order, then its photo_reference, then the photos it was seen in -- and only
photos of the insured car: never a log card, a document, the odometer, the
scene or the other party's car (owner decision 10). At most 12 photos a
request and two requests on the Decisions API, one call on the lite chain;
a suggestion whose photos fit nowhere stays unchecked. The words are
neutral: a question names the part and its photos by label ("In photo P07
(or P11), is the insured car's left rear bumper reflector damaged ...?"),
never that an AI found it.

Time: the Decisions API gets decisions.TIME_BOX (20 s of Google's time),
the lite call LITE_TIME_BOX. A failure, a timeout or no allowance never
fails the intake: the suggestions keep their order and the step's note
says so. The run log keeps the step as kind "check" (runs.CHECK_KIND).
"""
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

from app.ai import decisions, runs
from app.ai.keys import AiActor, AiUnavailable
from app.ai.second_look import _ChainUntil, _within, split_settled
from app.ai.structured import call_structured, image_part
from app.config import chain, settings
from app.reports.taxonomy import UNDETERMINED_SUFFIX

logger = logging.getLogger(__name__)

# The re-check's limits (section 4.7): one question per suggestion, at most
# MAX_QUESTIONS (second_look.MAX_SUGGESTIONS); each suggestion's best
# PHOTOS_EACH photos; at most MAX_PHOTOS photos a request (12 at medium is
# ~7k tokens, well inside the free 50,000 a minute) and MAX_REQUESTS
# Decisions API requests. The lite re-check is one call.
MAX_QUESTIONS = 16
PHOTOS_EACH = 2
MAX_PHOTOS = 12
MAX_REQUESTS = 2
# The lite call: one model try, and the whole call however many models of
# the chain it tries.
LITE_TIMEOUT = 45.0
LITE_TIME_BOX = 60.0
VERDICTS = ("yes", "no", "unsure")
CONFIDENCES = ("High", "Medium", "Low")
OTHER_PART = "Other / Not Listed"
# Photos of the insured car: the damage close-ups, the walk-round shots and
# the tyres; a cabin or "other" photo only when the sorter saw damage on it
# (a boot photo filed as interior). Never a document, the scene or the
# other party's car.
_CAR_KINDS = frozenset({"damage", "overview", "tyre"})
_IF_DAMAGE_KINDS = frozenset({"interior", "other"})
_LABEL_RE = re.compile(r"^P0*(\d+)$")
# Evidence entries a suggestion keeps (schema.Evidence, section 4.1).
MAX_EVIDENCE = 4

NOTE_NO_PHOTOS = "No photos of the suggested parts to re-check."
NOTE_NO_ALLOWANCE = "No AI key or allowance left for the re-check right now."
NOTE_FAILED = "The AI re-check didn't work this time; the suggestions are in their usual order."
NOTE_LITE = "Re-checked by the light model: the decision check didn't answer."
NOTE_PART = "Re-checked {done} of {total} suggestions."

# ------------------------------------------------------------ cleaning
#
# One cleaner for every box and reason the AI writes (section 4.6:
# app/ai/evidence.py, on the evidence-data branch). Until that branch is
# merged, the same rules stand in here.

try:
    from app.ai.evidence import clean_box, clean_reason, frame_of
except ImportError:  # replaced by app/ai/evidence.py once merged
    MAX_REASON = 160
    _THREE_DIGITS = re.compile(r"\d\D*\d\D*\d")

    def clean_box(box, raw_original: bool = False) -> list[int] | None:
        """[ymin, xmin, ymax, xmax] on 0-1000, or None: four numbers, each
        within -20..1020 (clamped to 0..1000), the right way round, no side
        under 8 and no more than 85% of the photo; never on a raw original
        (no preview: the frame may be rotated)."""
        if raw_original or not isinstance(box, (list, tuple)) or len(box) != 4:
            return None
        values = []
        for value in box:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not -20 <= value <= 1020:
                return None
            values.append(int(round(min(1000, max(0, value)))))
        ymin, xmin, ymax, xmax = values
        if ymax - ymin < 8 or xmax - xmin < 8:
            return None
        if (ymax - ymin) * (xmax - xmin) > 0.85 * 1000 * 1000:
            return None
        return values

    def clean_reason(text) -> str | None:
        """The AI's words on one line, without a token holding three digits
        or a letters-and-digits token of five characters or more (a plate, a
        VIN, a phone number), cut to 160 characters at a word; None when
        nothing is left."""
        if not isinstance(text, str):
            return None
        kept = []
        for token in text.split():
            mixed = any(c.isalpha() for c in token) and any(c.isdigit() for c in token)
            if _THREE_DIGITS.search(token) or (mixed and len(token) >= 5):
                continue
            kept.append(token)
        out = " ".join(kept)
        if len(out) > MAX_REASON:
            cut = out[:MAX_REASON + 1]
            out = cut.rsplit(" ", 1)[0] if " " in cut else out[:MAX_REASON]
        return out.strip() or None

    @lru_cache(maxsize=512)
    def frame_of(path: str) -> tuple[int, int] | None:
        """The pixel size of the image the AI was sent (its box is drawn on
        it), or None when it can't be read."""
        try:
            from PIL import Image

            with Image.open(path) as image:
                return image.size
        except Exception:
            return None


# ------------------------------------------------------------ what to ask


@dataclass
class Ask:
    """One suggestion's question: its key, the part in plain words, and its
    photos (ids), best first. ref names the question in its request (F1,
    F2...), set once the requests are packed."""
    key: str
    words: str
    photos: list[str]
    ref: str = ""


@dataclass
class Photo:
    photo_id: str
    label: str
    path: str  # what the AI is sent: the 1600 px preview, else the original
    raw: bool  # no preview: a box may be on a rotated frame


@dataclass
class Plan:
    asks: list[Ask]
    photos: dict[str, Photo]
    total: int  # the suggestions
    unasked: list[str] = field(default_factory=list)  # keys with no photo to ask about


def part_words(item: dict) -> str:
    """The part as a question names it: "left rear bumper reflector"; a
    side-undetermined part without the side; an "Other / Not Listed" part
    by its name."""
    part = str(item.get("part") or "").strip()
    if part == OTHER_PART:
        name = item.get("other_part_name") or item.get("name") or "part"
    else:
        name = part.removesuffix(UNDETERMINED_SUFFIX)
    return " ".join(str(name).split()).lower()


def is_car_photo(photo) -> bool:
    """Whether the re-check may send this photo (the kinds above)."""
    if photo.excluded or photo.quality == "unusable" or not photo.kind:
        return False
    return photo.kind in _CAR_KINDS or (photo.kind in _IF_DAMAGE_KINDS and bool(photo.shows_damage))


def photo_refs(item: dict) -> list[str]:
    """A suggestion's photos, best first: its evidence in order (the
    evidence-data branch orders it best first), its photo_reference, the
    photos it was seen in. Once each."""
    refs = [e.get("photo") for e in item.get("evidence") or [] if isinstance(e, dict)]
    refs += [item.get("photo_reference")] + list(item.get("seen_in") or [])
    return list(dict.fromkeys(r for r in refs if isinstance(r, str) and r))


def _photo_id(ref: str, by_label: dict[str, str]) -> str | None:
    """"@<photo id>" in a draft; a "P07" label just in case."""
    if ref.startswith("@"):
        return ref[1:]
    match = _LABEL_RE.match(ref.strip().upper())
    return by_label.get(f"P{int(match.group(1)):02d}") if match else None


def plan_asks(items: list[dict], photos: list) -> Plan:
    """The questions for these suggestions over the draft's photos (rows in
    camera order): each suggestion with its best PHOTOS_EACH car photos still
    there, the first MAX_QUESTIONS of them."""
    from app.intake.photos import ai_input_path, photo_label

    labels = {p.id: photo_label(i) for i, p in enumerate(photos)}
    by_label = {label: photo_id for photo_id, label in labels.items()}
    usable = {p.id: p for p in photos if is_car_photo(p) and Path(p.path).exists()}
    plan = Plan(asks=[], photos={}, total=len(items))
    for item in items:
        key = str(item.get("key") or item.get("part") or "")
        chosen: list[str] = []
        for ref in photo_refs(item):
            photo_id = _photo_id(ref, by_label)
            if photo_id in usable and photo_id not in chosen:
                chosen.append(photo_id)
            if len(chosen) == PHOTOS_EACH:
                break
        if not key or not chosen or len(plan.asks) >= MAX_QUESTIONS:
            plan.unasked.append(key)
            continue
        plan.asks.append(Ask(key=key, words=part_words(item), photos=chosen))
        for photo_id in chosen:
            if photo_id not in plan.photos:
                original = usable[photo_id].path
                sent = ai_input_path(original)
                plan.photos[photo_id] = Photo(photo_id, labels[photo_id], sent, sent == original)
    return plan


def _room(held: list[set[str]], photo: str, max_photos: int) -> int | None:
    """The request a photo goes in: one that holds it already, else the
    first with room; None when none has."""
    for i, photos in enumerate(held):
        if photo in photos:
            return i
    return next((i for i, photos in enumerate(held) if len(photos) < max_photos), None)


def pack(asks: list[Ask], *, max_photos: int = MAX_PHOTOS, max_requests: int = MAX_REQUESTS
         ) -> tuple[list[list[Ask]], list[Ask]]:
    """(requests, left out). Every ask gets its best photo first, in its
    order (the surest first): in the request that holds that photo already,
    else the first with room, else a new request while there may be one more
    (max_requests). Then each, in the same order, gets its second photo where
    its request has room: one photo for every suggestion before two for
    some. An ask whose best photo fits nowhere is left out (unchecked). The
    questions are named F1, F2... request by request."""
    held: list[set[str]] = []
    placed: list[tuple[Ask, int, list[str]]] = []
    left_out: list[Ask] = []
    for ask in asks:
        best = ask.photos[0]
        where = _room(held, best, max_photos)
        if where is None and len(held) < max_requests:
            held.append(set())
            where = len(held) - 1
        if where is None:
            left_out.append(ask)
            continue
        held[where].add(best)
        placed.append((ask, where, [best]))
    for ask, where, photos in placed:
        for extra in ask.photos[1:]:
            if extra in held[where] or len(held[where]) < max_photos:
                held[where].add(extra)
                photos.append(extra)
    out: list[list[Ask]] = [[] for _ in held]
    for ask, where, photos in placed:
        out[where].append(replace(ask, photos=photos))
    n = 0
    for request in out:
        for i, ask in enumerate(request):
            n += 1
            request[i] = replace(ask, ref=f"F{n}")
    return out, left_out


def _label_number(label: str) -> int:
    match = _LABEL_RE.match(label)
    return int(match.group(1)) if match else 0


def sent_photos(request: list[Ask], photos: dict[str, Photo]) -> list[Photo]:
    """A request's photos, once each, in camera order."""
    ids = {photo_id for ask in request for photo_id in ask.photos}
    return sorted((photos[i] for i in ids), key=lambda p: _label_number(p.label))


# ------------------------------------------------------------ the Decisions API


def question(ask: Ask, photos: dict[str, Photo]) -> str:
    """Neutral words that name the photos by label -- never that an AI found
    the part."""
    labels = [photos[i].label for i in ask.photos]
    where = labels[0] + (f" (or {' or '.join(labels[1:])})" if len(labels) > 1 else "")
    return (f"In photo {where}, is the insured car's {ask.words} damaged -- cracked, broken, dented, scratched "
            f"or missing? Only the insured car.")


def decision_input(request: list[Ask], photos: dict[str, Photo]) -> tuple[list[dict], dict[str, dict]]:
    """One Decisions API request's input and questions (section 2's shape):
    "Photo P07:" before each photo, sent at settings.intake_check_resolution."""
    parts: list[dict] = []
    for photo in sent_photos(request, photos):
        parts.append({"type": "text", "text": f"Photo {photo.label}:"})
        image = image_part(photo.path)
        image["resolution"] = settings.intake_check_resolution
        parts.append(image)
    questions = {ask.ref: {"type": "noul", "instructions": question(ask, photos)} for ask in request}
    return parts, questions


def verdict(p: float) -> str:
    """"yes" at settings.intake_check_yes or more, "no" at intake_check_no
    or less, "unsure" between."""
    if p >= settings.intake_check_yes:
        return "yes"
    if p <= settings.intake_check_no:
        return "no"
    return "unsure"


class _Calls:
    """The calls made (a Decisions API request, the lite call): CheckRun.calls."""

    def __init__(self) -> None:
        self.n = 0


def ask_decisions(requests: list[list[Ask]], photos: dict[str, Photo], actor: AiActor, calls: _Calls
                  ) -> tuple[dict[str, dict], list[Ask], str | None]:
    """The Decisions API on each request in turn: ({suggestion key: its
    check}, the asks left unanswered by a failed request, why it failed or
    None). A failure ends the step -- the next request would most likely
    fail the same way -- and what came back before it is kept. Raises
    AiUnavailable (no key to try)."""
    answers: dict[str, dict] = {}
    left = decisions.TIME_BOX
    for i, request in enumerate(requests):
        parts, questions = decision_input(request, photos)
        calls.n += 1
        try:
            got = decisions.decide(purpose=decisions.PURPOSE, input_parts=parts, questions=questions, actor=actor,
                                   model=settings.gemini_decision_model, budget=left)
        except decisions.DecisionFailed as exc:
            logger.warning("Re-check: the Decisions API failed (%s)", exc.why)
            return answers, [ask for later in requests[i:] for ask in later], exc.why
        left -= got.get("seconds") or 0.0
        for ask in request:
            p = got["answers"].get(ask.ref)
            if p is not None:
                answers[ask.key] = {"verdict": verdict(p), "p": round(p, 3), "by": "decision",
                                    "model": got.get("model"), "reason": None}
    return answers, [], None


# ------------------------------------------------------------ the lite re-check

LITE_PROMPT = (
    "You check photos of a damaged car for a Singapore motor insurance loss adjuster. For each part listed "
    "(F1, F2, ...), look at the photos named for it and say whether that part of the insured car is damaged: "
    "cracked, broken, dented, scratched, bent, torn or missing.\n"
    "Some of these parts may be undamaged, and some may not show clearly. Answer yes only when a photo shows the "
    "damage, no when the part is clearly visible and undamaged, and unsure otherwise. Only the insured car: never "
    "another vehicle in the photo. LEFT and RIGHT are the car's own sides as its driver sees them.\n"
    "For each part give: ref; damaged (yes, no or unsure); photo_reference: the photo that shows it best, exactly "
    "as labelled; reason: what you can see, at most 15 words, no plate or other numbers; and box_2d: [y_min, x_min, "
    "y_max, x_max] on 0-1000 round the damage in its photo_reference photo -- leave box_2d out if the part is not "
    "damaged or you can't place it."
)


def lite_schema(refs: list[str], labels: list[str]) -> dict:
    """Small, and apart from every other schema: one array, three enums, no
    minItems / maxItems (section 4.7)."""
    return {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string", "enum": list(refs)},
                        "damaged": {"type": "string", "enum": list(VERDICTS)},
                        "photo_reference": {"type": "string", "enum": list(labels)},
                        "reason": {"type": "string"},
                        "box_2d": {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": ["ref", "damaged"],
                },
            },
        },
        "required": ["findings"],
    }


def lite_input(request: list[Ask], photos: dict[str, Photo]) -> list[dict]:
    """The parts with their photos' labels, then the photos (at Gemini's
    default resolution, as the second look sends them)."""
    lines = ["Parts to check, each with the photos to look at:"]
    for ask in request:
        labels = [photos[i].label for i in ask.photos]
        lines.append(f"- {ask.ref}: the {ask.words} -- photo{'s' if len(labels) > 1 else ''} {', '.join(labels)}")
    sent = sent_photos(request, photos)
    lines.append(f"{len(sent)} photos follow, labelled with the case photo number.")
    parts: list[dict] = [{"type": "text", "text": "\n".join(lines)}]
    for photo in sent:
        parts.append({"type": "text", "text": f"Photo {photo.label}:"})
        parts.append(image_part(photo.path))
    return parts


def parse_lite(raw: str, refs: list[str], labels: list[str]) -> dict[str, dict]:
    """{ref: {"damaged", "photo" (a label or None), "reason", "box"}}. Raises
    on a malformed answer (call_structured then tries the next model). An
    entry with a ref or a verdict outside the lists is dropped; each ref
    once; the reason cleaned; a box only with its photo (cleaned later,
    against that photo)."""
    data = json.loads(raw)
    found = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(found, list):
        raise ValueError("the answer has no findings list")
    out: dict[str, dict] = {}
    for entry in found:
        if not isinstance(entry, dict):
            continue
        ref, damaged = entry.get("ref"), entry.get("damaged")
        if ref not in refs or ref in out or damaged not in VERDICTS:
            continue
        match = _LABEL_RE.match(str(entry.get("photo_reference") or "").strip().upper())
        label = f"P{int(match.group(1)):02d}" if match else None
        label = label if label in labels else None
        out[ref] = {"damaged": damaged, "photo": label, "reason": clean_reason(entry.get("reason")),
                    "box": entry.get("box_2d") if label else None}
    return out


def _answered_by(attempts: runs.Attempts, models: list[str]) -> str | None:
    """The chain's model whose try answered, from the run's collector."""
    ok = [a.get("model") for a in attempts.snapshot() if a.get("outcome") == "ok" and a.get("model") in models]
    return ok[-1] if ok else None


def ask_lite(asks: list[Ask], photos: dict[str, Photo], actor: AiActor, attempts: runs.Attempts, calls: _Calls
             ) -> tuple[dict[str, dict], dict[str, dict], list[str]]:
    """The one lite call: ({suggestion key: its check}, {suggestion key: its
    evidence by "check"}, the labels sent). Raises as call_structured does,
    or TimeoutError after LITE_TIME_BOX."""
    requests, _ = pack(asks, max_requests=1)
    if not requests:
        return {}, {}, []
    request = requests[0]
    sent = sent_photos(request, photos)
    refs = [ask.ref for ask in request]
    labels = [p.label for p in sent]
    models = chain(settings.gemini_check_chain)

    def work(stop: threading.Event) -> dict[str, dict]:
        return call_structured(
            purpose="check", system_instruction=LITE_PROMPT, input_parts=lite_input(request, photos),
            schema=lite_schema(refs, labels), parse=lambda raw: parse_lite(raw, refs, labels),
            models=_ChainUntil(models, stop), actor=actor, timeout=LITE_TIMEOUT,
            # Its own chain running out must not switch the person's key off
            # for the reading (structured.call_structured).
            judge_key=False,
        )

    calls.n += 1
    got = _within(LITE_TIME_BOX, work, attempts)
    model = _answered_by(attempts, models)
    by_label = {p.label: p for p in sent}
    checks: dict[str, dict] = {}
    boxes: dict[str, dict] = {}
    for ask in request:
        answer = got.get(ask.ref)
        if answer is None:
            continue
        checks[ask.key] = {"verdict": answer["damaged"], "p": None, "by": "lite", "model": model,
                           "reason": answer["reason"]}
        photo = by_label.get(answer["photo"]) if answer["photo"] else None
        # A box only round damage it says is there, on a photo it was asked
        # to look at for this part.
        if answer["damaged"] == "yes" and photo is not None and photo.photo_id in ask.photos:
            box = clean_box(answer["box"], raw_original=photo.raw)
            if box:
                width, height = frame_of(photo.path) or (None, None)
                boxes[ask.key] = {"photo": f"@{photo.photo_id}", "box": box, "box_kind": "ai", "by": "check",
                                  "reason": answer["reason"], "confidence": None, "img_w": width, "img_h": height}
    return checks, boxes, labels


# ------------------------------------------------------------ the order

_RANK = {c: i for i, c in enumerate(CONFIDENCES)}


def _order(item: dict) -> tuple:
    """"yes", then "unsure" and the unchecked, then "no" -- within "yes" and
    "no", p high to low, so the whole list runs from most to least likely
    damaged; a verdict without p after those with one; ties by confidence,
    then as stored (the sort is stable)."""
    check = item.get("check") if isinstance(item.get("check"), dict) else {}
    found, p = check.get("verdict"), check.get("p")
    confidence = _RANK.get(item.get("confidence"), len(CONFIDENCES))
    has_p = isinstance(p, (int, float)) and not isinstance(p, bool)
    if found == "yes":
        return (0, 0 if has_p else 1, -p if has_p else 0.0, confidence)
    if found == "no":
        return (2, 0 if has_p else 1, -p if has_p else 0.0, confidence)
    return (1, 0, 0.0, confidence)


def sort_items(items: list[dict]) -> list[dict]:
    return sorted(items, key=_order)


def add_check_box(item: dict, entry: dict) -> None:
    """In place: the lite re-check's box on the suggestion's evidence, only on
    a photo where none of its entries has a box (the AI's or the sorter's).
    The evidence-data branch's join and settle when they are there (one
    entry per photo, a boxed one over a boxless one, ordered, the reason
    filled); with_check_box otherwise."""
    if any(isinstance(e, dict) and e.get("photo") == entry["photo"] and e.get("box") for e in item.get("evidence") or []):
        return
    try:
        from app.ai.evidence import join, settle
    except ImportError:
        item["evidence"] = with_check_box(item.get("evidence") or [], entry)
        return
    join(item, [entry])
    settle(item)


def with_check_box(evidence: list[dict], entry: dict) -> list[dict]:
    """The lite re-check's box as evidence (by "check"), only on a photo no
    entry has a box on: it takes that photo's boxless entry's place (one
    entry per photo; its confidence, and its reason when the check gave
    none, kept), else goes last while there is room (MAX_EVIDENCE)."""
    out = [dict(e) for e in evidence if isinstance(e, dict)]
    same = [i for i, e in enumerate(out) if e.get("photo") == entry["photo"]]
    if any(out[i].get("box") for i in same):
        return out
    if same:
        old = out[same[0]]
        out[same[0]] = {**entry, "reason": entry.get("reason") or old.get("reason"),
                        "confidence": old.get("confidence")}
    elif len(out) < MAX_EVIDENCE:
        out.append(dict(entry))
    return out


# ------------------------------------------------------------ the intake step


def _plan(report_id: str) -> Plan | None:
    """What to ask, from the draft as the second look left it: its open
    suggestions -- one a person dismissed or put on the list stays in
    items (second_look._store) but is never asked about. None when there
    is nothing to say: the draft is gone, or has no open suggestions."""
    from app.intake.photos import ordered_photos
    from app.reports.db import SessionLocal
    from app.reports.models import Report

    db = SessionLocal()
    try:
        report = db.get(Report, report_id)
        if report is None or report.status != "intake":
            return None
        data = report.data or {}
        look = data.get("second_look")
        items, _ = split_settled(
            [it for it in (look or {}).get("items") or [] if isinstance(it, dict) and it.get("part")], data)
        if not items:
            return None
        return plan_asks(items, ordered_photos(db, report_id))
    finally:
        db.close()


def _check_run(status: str, note: str | None = None, **fields) -> dict:
    return {"status": status, "by": None, "model": None, "note": note, "seconds": None, "calls": 0, **fields}


def _ask(report_id: str, actor: AiActor, mode: str, plan: Plan) -> tuple[dict, dict[str, dict], dict[str, dict]]:
    """(the CheckRun, {key: check}, {key: evidence by "check"}) for one draft,
    as one run of the log."""
    calls = _Calls()
    checks: dict[str, dict] = {}
    boxes: dict[str, dict] = {}
    why = None
    try:
        with runs.recorded(report_id, runs.CHECK_KIND, actor) as run:
            sent: list[str] = []
            todo = plan.asks
            if mode in ("decision", "decision_then_lite"):
                requests, _ = pack(plan.asks)
                sent += [p.label for request in requests for p in sent_photos(request, plan.photos)]
                checks, failed, why = ask_decisions(requests, plan.photos, actor, calls)
                todo = failed if mode == "decision_then_lite" else []
            if todo:
                try:
                    lite_checks, boxes, labels = ask_lite(todo, plan.photos, actor, run.attempts, calls)
                except AiUnavailable:
                    raise
                except Exception as exc:
                    if not checks:
                        raise
                    # The Decisions API's answers stand; the rest stay unchecked.
                    logger.warning("Re-check for %s: the lite call failed (%s)", report_id,
                                   runs.scrub_error(f"{type(exc).__name__}: {exc}"))
                    lite_checks, labels = {}, []
                checks.update(lite_checks)
                sent += labels
            # As "@<photo id>", in camera order, as the zone rows keep
            # theirs: the labels shift when a photo is deleted before
            # filing, which turns these into the case's P-numbers
            # (runs.relabel_check_runs).
            id_of = {p.label: photo_id for photo_id, p in plan.photos.items()}
            run.photos_sent = [f"@{id_of[label]}" if label in id_of else label
                               for label in sorted(set(sent), key=_label_number)]
            run.parts_found = sum(1 for c in checks.values() if c["verdict"] == "yes")
            if not checks and why is not None:
                raise decisions.DecisionFailed(why)
    except AiUnavailable as exc:
        logger.info("Re-check for %s skipped: %s", report_id, exc.code)
        return _check_run("skipped", NOTE_NO_ALLOWANCE, calls=calls.n), {}, {}
    except Exception as exc:
        # Scrubbed: the error can quote the model's answer.
        logger.warning("Re-check for %s failed: %s", report_id, runs.scrub_error(f"{type(exc).__name__}: {exc}"))
        return _check_run("failed", NOTE_FAILED, calls=calls.n), {}, {}
    if not checks:
        return _check_run("failed", NOTE_FAILED, calls=calls.n), {}, {}
    by = "decision" if any(c["by"] == "decision" for c in checks.values()) else "lite"
    model = next((c["model"] for c in checks.values() if c["by"] == by and c.get("model")), None)
    note = NOTE_LITE if by == "lite" and why is not None else None
    if len(checks) < plan.total:
        note = NOTE_PART.format(done=len(checks), total=plan.total) + (f" {note}" if note else "")
    return _check_run("ok", note, by=by, model=model, calls=calls.n), checks, boxes


def _store(report_id: str, outcome: dict, checks: dict[str, dict], boxes: dict[str, dict]) -> None:
    """Writes the answers onto the suggestions as they are now (by key), the
    open ones in the new order and the settled ones after them, as
    second_look._store left them (split_settled), and the step's CheckRun.
    The same suggestions, never one more or less; the damage list is not
    touched."""
    from app.reports.db import SessionLocal
    from app.reports.models import Report
    from app.reports.schema import SecondLook

    db = SessionLocal()
    try:
        report = db.get(Report, report_id)
        if report is None or report.status != "intake":
            return
        data = dict(report.data or {})
        look = data.get("second_look")
        if not isinstance(look, dict):
            return
        items = []
        for it in look.get("items") or []:
            it = dict(it)
            key = str(it.get("key") or it.get("part") or "")
            found = checks.get(key)
            if found is not None:
                it["check"] = found
                said = it.get("reason") or any(isinstance(e, dict) and e.get("reason") for e in it.get("evidence") or [])
                if found["verdict"] == "yes" and found.get("reason") and not said:
                    # The AI's words for the damage when the reads gave none
                    # (section 4.1). A "no" or "unsure" reason stays on the
                    # check alone.
                    it["reason"] = found["reason"]
            if key in boxes:
                add_check_box(it, boxes[key])
            items.append(it)
        # Sorted only on an answer: a failed re-check leaves the order as it
        # was (the second look's, surest first).
        open_items, settled = split_settled(items, data)
        items = (sort_items(open_items) if checks else open_items) + settled
        stored = SecondLook.model_validate({**look, "items": items, "check": outcome})
        report.data = {**data, "second_look": stored.model_dump()}
        db.commit()
    finally:
        db.close()


def run_for_intake(report_id: str, actor: AiActor) -> None:
    """The re-check for a draft the reading and the second look have just
    saved (intake/pipeline._read, right after second_look.run_for_intake).
    Never raises. Nothing at all while settings.intake_check_mode is "off"
    (the default), nor when the draft has no suggestions: no call, no
    write."""
    mode = getattr(settings, "intake_check_mode", "off")
    if mode == "off":
        return
    started = time.monotonic()
    try:
        plan = _plan(report_id)
    except Exception:
        logger.exception("Re-check for %s: couldn't read the draft", report_id)
        return
    if plan is None:
        return
    if not plan.asks:
        outcome, checks, boxes = _check_run("skipped", NOTE_NO_PHOTOS), {}, {}
    else:
        try:
            outcome, checks, boxes = _ask(report_id, actor, mode, plan)
        except Exception:
            logger.exception("Re-check for %s failed", report_id)
            outcome, checks, boxes = _check_run("failed", NOTE_FAILED), {}, {}
    outcome["seconds"] = round(time.monotonic() - started, 1)
    try:
        _store(report_id, outcome, checks, boxes)
    except Exception:
        logger.exception("Re-check for %s: couldn't save its answer", report_id)
