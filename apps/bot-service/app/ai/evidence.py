"""A finding's evidence: which photo shows it, where on that photo, and in
whose words (contract 9, plans/2026-11-15-evidence/contract9.md sections
4.1 and 4.6).

Every read says where it saw a part: the main read its photo_reference and
bbox_2d, each zone read its photo_reference and -- with
settings.intake_zone_evidence -- a reason and a box_2d (ai/zone_read.py),
the sorter one damage box per photo (IntakePhoto.damage_box,
settings.intake_sort_damage_box). They are kept per finding as
reports/schema.Evidence entries: one per photo, at most MAX_ENTRIES, the
photo_reference's first. The Review step shows them as "AI box" /
"Sorter's box" and "AI says: ...".

Everything here is code over what the models already answered: no call,
no request changes. The data is cleaned where it is made, so the models in
reports/schema.py stay lenient (an old draft or a whole-draft save never
fails on them):
  - a box (clean_box) is four numbers on 0-1000, a sensible size, and only
    on the frame the reader saw -- a photo with no preview went to Gemini as
    its raw original, which may be turned by its EXIF, so its box is
    dropped (raw_original);
  - a reason (clean_reason) is the model's words, short, without anything
    shaped like a plate, a VIN or a phone number: reasons are shown to
    people and kept on the case, never in logs or AI-accuracy rows.
"""
import functools
import logging
import math
import os
import re
from dataclasses import dataclass

from app.reports.taxonomy import UNDETERMINED_SUFFIX, split_side, unsided_part

logger = logging.getLogger(__name__)

# Per finding: the slides a person goes through before deciding.
MAX_ENTRIES = 4
REASON_MAX = 160
# A box may overshoot the 0-1000 frame by this much (a model rounding at
# the photo's edge) and is pulled back in; further out it is not a box on
# this photo at all.
SLACK = 20
# A side under this many thousandths is a line, not a box round damage.
MIN_SIDE = 8
# A box over this share of the photo marks nothing: the photo itself.
MAX_AREA_SHARE = 0.85
CONFIDENCES = ("High", "Medium", "Low")
BOX_KINDS = ("ai", "sort", "part")
SORT = "sort"
# Detection Round 2 (settings.intake_box_every; intake/zones.place_boxes):
# a zone read's box round a listed part it saw, its entry by
# "locate:zone:<zone>" with box_kind "part" -- where the part is, never a
# read's word on damage: no reason, no confidence, never the item's
# bbox_2d. order_evidence keeps it beside the reads' own entry on its
# photo and after every one of them, so it only ever takes a free slot.
PART = "part"
LOCATED = "locate:"


def located(e) -> bool:
    """An evidence entry that is a Round 2 located part box."""
    return isinstance(e, dict) and str(e.get("by") or "").startswith(LOCATED)

# A token with three or more digits (a plate's number, a phone number, a
# date), or letters and digits mixed over five characters or more (a
# plate, a VIN, a part or policy number): the reason is shown without it.
_DIGITS = re.compile(r"\d")
_LETTERS = re.compile(r"[A-Za-z]")
# A photo's label as the model was sent it ("P07", "P07/P08"): numbered as
# the draft was then, which filing renumbers when a photo was deleted -- a
# reason quoting one could point at another photo. Left out like a plate;
# the evidence entry already says which photo it is.
_PHOTO_LABEL = re.compile(r"(?:P\d{1,3}[/,&+-]?)+", re.IGNORECASE)
_QUOTES = "\"'`“”‘’"


# ------------------------------------------------------------ cleaning


def clean_box(box, raw_original: bool = False) -> list[int] | None:
    """[ymin, xmin, ymax, xmax] in whole numbers on 0-1000, or None.

    Four numbers, each within -SLACK..1000+SLACK (pulled into 0..1000);
    ymin < ymax and xmin < xmax; each side at least MIN_SIDE; at most
    MAX_AREA_SHARE of the photo. raw_original: the model was sent the photo
    as uploaded (no preview), whose frame may be turned by its EXIF -- the
    box may be on a picture lying on its side, so there is none. A wrong
    box misleads a person more than no box (contract 9, decision 24: the
    main read's inverted boxes drew with a negative size)."""
    if raw_original or not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    values = []
    for v in box:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        if v < -SLACK or v > 1000 + SLACK:
            return None
        values.append(min(1000, max(0, int(round(v)))))
    ymin, xmin, ymax, xmax = values
    if ymax - ymin < MIN_SIDE or xmax - xmin < MIN_SIDE:
        return None  # inverted, empty or a sliver
    if (ymax - ymin) * (xmax - xmin) > MAX_AREA_SHARE * 1000 * 1000:
        return None
    return values


def _photo_label(token: str) -> bool:
    core = token.strip(".,;:!?()[]{}<>\"'`#").removesuffix("'s")
    return bool(core) and _PHOTO_LABEL.fullmatch(core) is not None


def _identifying(token: str) -> bool:
    digits = len(_DIGITS.findall(token))
    if digits >= 3:
        return True
    core = token.strip(".,;:!?()[]{}<>\"'")
    return digits > 0 and bool(_LETTERS.search(core)) and len(core) >= 5


def clean_reason(text) -> str | None:
    """The model's reason as it may be shown: whitespace collapsed, quotes
    round it dropped, any plate-, VIN- or phone-shaped token left out (a
    reason can quote the plate it saw), and any photo label ("P07": the
    draft's numbering), at most REASON_MAX characters cut at a word. None
    when nothing is left."""
    if not isinstance(text, str):
        return None
    words = [w for w in text.split() if not _identifying(w) and not _photo_label(w)]
    out = " ".join(words)
    if len(out) >= 2 and out[0] in _QUOTES and out[-1] in _QUOTES:
        out = out[1:-1].strip()
    if len(out) > REASON_MAX:
        cut = out[:REASON_MAX + 1]
        space = cut.rfind(" ")
        out = (cut[:space] if space > 0 else out[:REASON_MAX]).rstrip(" ,;:-")
    return out or None


@functools.lru_cache(maxsize=1024)
def _frame(path: str, mtime_ns: int, size: int) -> tuple[int, int] | None:
    from PIL import Image

    with Image.open(path) as image:
        width, height = image.size
        try:
            orientation = image.getexif().get(0x0112)
        except Exception:
            orientation = None
    # 5 to 8 turn the picture a quarter: the upright frame is the other way.
    return (height, width) if orientation in (5, 6, 7, 8) else (width, height)


def frame_of(path) -> tuple[int, int] | None:
    """(width, height) of the image a box was drawn on -- the AI-input copy
    (photos.ai_input_path), as it stands upright -- or None when it can't
    be read. Cached per file version: the reading asks once per photo with
    a box, the file's header only."""
    try:
        stat = os.stat(path)
        return _frame(str(path), stat.st_mtime_ns, stat.st_size)
    except Exception:
        return None


def map_crop_box(box, window, frame) -> list[int] | None:
    """A box drawn on a close-up ([ymin, xmin, ymax, xmax], 0-1000 of the
    close-up) on its photo instead: window is (left, top, right, bottom) in
    pixels of the close-up on an image of size frame (width, height), as
    intake/crops.crop_window gives it. None when either makes no sense.
    The close-up and its photo are the same picture at another size, so
    the frame may be any copy's (the preview's, the original's)."""
    box = clean_box(box)
    if box is None or not isinstance(window, (list, tuple)) or len(window) != 4:
        return None
    try:
        width, height = (float(v) for v in frame)
        left, top, right, bottom = (float(v) for v in window)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0 or right <= left or bottom <= top:
        return None
    ymin, xmin, ymax, xmax = box
    span_x, span_y = right - left, bottom - top
    return clean_box([
        (top + ymin / 1000 * span_y) / height * 1000,
        (left + xmin / 1000 * span_x) / width * 1000,
        (top + ymax / 1000 * span_y) / height * 1000,
        (left + xmax / 1000 * span_x) / width * 1000,
    ])


# ------------------------------------------------------------ entries


def entry(photo: str, by: str, *, box=None, kind: str = "ai", reason=None, confidence=None) -> dict:
    """One reports/schema.Evidence as a dict: box_kind None exactly when
    there is no box."""
    box = clean_box(box)
    return {"photo": photo, "box": box, "box_kind": (kind if kind in BOX_KINDS else "ai") if box else None, "by": by,
            "reason": clean_reason(reason), "confidence": confidence if confidence in CONFIDENCES else None,
            "img_w": None, "img_h": None}


def _as_dict(value) -> dict | None:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, dict) or not isinstance(value.get("photo"), str) or not value["photo"]:
        return None
    out = dict(value)
    if out.get("box") is None:
        out["box"], out["box_kind"] = None, None
    elif out.get("box_kind") not in BOX_KINDS:
        out["box_kind"] = "ai"
    return out


def _rank(e: dict) -> tuple[int, int]:
    """Better first: an AI box, then the sorter's, then none; then High,
    Medium, Low, unknown."""
    kind = BOX_KINDS.index(e["box_kind"]) if e.get("box") and e.get("box_kind") in BOX_KINDS else len(BOX_KINDS)
    confidence = e.get("confidence")
    return kind, CONFIDENCES.index(confidence) if confidence in CONFIDENCES else len(CONFIDENCES)


def order_evidence(entries, primary_ref: str | None) -> list[dict]:
    """At most one entry per photo (the better one: _rank, the first on a
    tie) and at most MAX_ENTRIES: the photo_reference's first -- the photo
    the list shows, and the one bbox_2d is on -- then the rest best first.
    A Round 2 located part box (located) is kept apart from the reads'
    entry on its photo and after every read's entry: it never takes a
    read's place. Without one, the order is as it always was."""
    best: dict[tuple, dict] = {}
    for value in entries or []:
        e = _as_dict(value)
        if e is None:
            continue
        key = (e["photo"], located(e))
        held = best.get(key)
        if held is None or _rank(e) < _rank(held):
            best[key] = e
    kept = sorted(best.values(), key=lambda e: (located(e), e["photo"] != primary_ref, _rank(e)))
    return kept[:MAX_ENTRIES]


def own_entry(item: dict, by: str) -> dict | None:
    """The entry an item's own photo_reference (and box, a main-read item's
    bbox_2d) makes, or None without a photo."""
    ref = item.get("photo_reference")
    if not ref:
        return None
    return entry(ref, by, box=item.get("bbox_2d"), reason=item.get("reason"),
                 confidence=item.get("ai_confidence", item.get("confidence")))


def entries_of(item: dict, by: str) -> list[dict]:
    """One read's item's entries: those it carries, else its own photo's."""
    carried = [e for e in (_as_dict(v) for v in item.get("evidence") or []) if e]
    if carried:
        return carried
    own = own_entry(item, by)
    return [own] if own else []


def settle(item: dict, *, fill_box: bool = False) -> dict:
    """In place: the item's evidence ordered (order_evidence), its reason
    the first an entry has when it has none, and -- a damage item's
    (fill_box) -- bbox_2d from evidence[0] when empty and evidence[0] is on
    its photo with an AI box, so today's list keeps drawing it. Never the
    sorter's box: bbox_2d is the AI's own mark wherever it is drawn."""
    item["evidence"] = order_evidence(item.get("evidence"), item.get("photo_reference"))
    if not item.get("reason"):
        item["reason"] = next((e["reason"] for e in item["evidence"] if e.get("reason")), None)
    if fill_box and not item.get("bbox_2d") and item["evidence"]:
        first = item["evidence"][0]
        if first["photo"] == item.get("photo_reference") and first.get("box") and first.get("box_kind") == "ai":
            item["bbox_2d"] = list(first["box"])
    return item


def join(item: dict, entries) -> dict:
    """In place: entries added to the item's evidence (copies), one per
    photo kept, then settled without touching bbox_2d."""
    item["evidence"] = [e for e in (_as_dict(v) for v in item.get("evidence") or []) if e] + [
        e for e in (_as_dict(v) for v in entries or []) if e]
    item["evidence"] = order_evidence(item["evidence"], item.get("photo_reference"))
    return item


# ------------------------------------------------------------ the case's photos


@dataclass
class PhotoFacts:
    """What the reading knows of one photo, for its findings' evidence."""
    # The image Gemini was sent (photos.ai_input_path).
    ai_path: str
    # No preview: the AI got the photo as uploaded (clean_box raw_original).
    raw: bool = False
    # The sorter's box (IntakePhoto.damage_box, cleaned) and the part it
    # named on the photo, which the box is round.
    sort_box: list[int] | None = None
    sort_part: str | None = None


def photo_id(ref) -> str | None:
    ref = str(ref or "")
    return ref[1:] if ref.startswith("@") and len(ref) > 1 else None


def _photos_of(item: dict) -> list[str]:
    refs = [item.get("photo_reference")] + list(item.get("seen_in") or []) + [
        e.get("photo") for e in item.get("evidence") or [] if isinstance(e, dict)]
    return list(dict.fromkeys(pid for pid in map(photo_id, refs) if pid))


def same_part(sorter_part: str | None, part: str | None) -> bool:
    """Whether the sorter's box, round the part it named, can be this
    finding's: the same part without its side ("Tail Lamp (side
    undetermined)" and "Left Tail Lamp" -- the sorter rarely tells a
    close-up's side), but never the other side's twin (a rear photo shows
    both tail lamps, and the box is round one)."""
    if not sorter_part or not part:
        return False
    (base_a, side_a), (base_b, side_b) = split_side(sorter_part), split_side(part)
    if side_a and side_b and side_a != side_b:
        return False
    if base_a == base_b:
        return True
    if sorter_part.endswith(UNDETERMINED_SUFFIX) or part.endswith(UNDETERMINED_SUFFIX):
        return (unsided_part(sorter_part) or sorter_part) == (unsided_part(part) or part)
    return False


def _has_ai_box(item: dict, pid: str) -> bool:
    return any(photo_id(e.get("photo")) == pid and e.get("box") and e.get("box_kind") == "ai"
               for e in item.get("evidence") or [])


def finish(listed: list[dict], suggested: list[dict], photos: dict[str, PhotoFacts]) -> None:
    """In place, after a reading (intake/pipeline._read_into): the list's
    items and the suggestions with their evidence made final against the
    case's photos ("@<photo id>" refs).

      - A box on a photo the AI got raw is dropped (clean_box), the main
        read's bbox_2d too; every other box carries its frame (img_w /
        img_h: frame_of the AI-input copy), so the UI can hide a box once
        the photo it shows is another shape.
      - The sorter's box (PhotoFacts.sort_box) becomes a finding's entry on
        that photo, by "sort", box_kind "sort" -- only where the finding has
        no AI box there, and only when the sorter named the same part
        (same_part) or the finding is the only one on that photo; it takes
        the place of a box-less entry there, with its confidence.
      - Then each is settled (settle): ordered, its reason, and a list
        item's bbox_2d from evidence[0]."""
    findings = [(item, True) for item in listed] + [(item, False) for item in suggested]
    for item, is_item in findings:
        entries = []
        for value in item.get("evidence") or []:
            e = _as_dict(value)
            if e is None:
                continue
            facts = photos.get(photo_id(e["photo"]))
            if e.get("box"):
                e["box"] = clean_box(e["box"], raw_original=facts is None or facts.raw)
                frame = frame_of(facts.ai_path) if e["box"] and facts else None
                if e["box"] is None:
                    e["box_kind"] = None
                e["img_w"], e["img_h"] = frame if frame else (None, None)
            else:
                e["box"], e["box_kind"], e["img_w"], e["img_h"] = None, None, None, None
            entries.append(e)
        item["evidence"] = entries
        # Taken now: a sorter's box below may take the place of the entry
        # that said why.
        if not item.get("reason"):
            item["reason"] = next((e["reason"] for e in entries if e.get("reason")), None)
        if is_item and item.get("bbox_2d") is not None:
            facts = photos.get(photo_id(item.get("photo_reference")))
            item["bbox_2d"] = clean_box(item["bbox_2d"], raw_original=facts is None or facts.raw)

    on_photo: dict[str, list[dict]] = {}
    for item, _ in findings:
        for pid in _photos_of(item):
            on_photo.setdefault(pid, []).append(item)
    for pid, here in on_photo.items():
        facts = photos.get(pid)
        if facts is None or facts.raw or not facts.sort_box:
            continue
        for item in here:
            if _has_ai_box(item, pid):
                continue
            if len(here) > 1 and not same_part(facts.sort_part, item.get("part")):
                continue
            ref = f"@{pid}"
            others = [e for e in item["evidence"] if e["photo"] != ref]
            held = next((e for e in item["evidence"] if e["photo"] == ref), None)
            confidence = (held or {}).get("confidence") or item.get("ai_confidence", item.get("confidence"))
            sort = entry(ref, SORT, box=facts.sort_box, kind=SORT, confidence=confidence)
            frame = frame_of(facts.ai_path) if sort["box"] else None
            sort["img_w"], sort["img_h"] = frame if frame else (None, None)
            if sort["box"]:
                item["evidence"] = others + [sort]
    for item, is_item in findings:
        settle(item, fill_box=is_item)
