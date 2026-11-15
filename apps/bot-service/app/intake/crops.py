"""Close-ups of the damage the sorter boxed, for the reading (contract 8 D1).

The light reader misses parts it could name up close: a reading sends up to
32 photos, each at Gemini's one budget per image (~1120 tokens on Gemini 3),
so a cracked reflector in the corner of a wide shot is a few dozen pixels to
it. With settings.intake_sort_damage_box the sorter boxes the damage on each
photo that shows some (IntakePhoto.damage_box); with intake_read_crops the
reading gets, right after such a photo, that box cut from the largest copy
of the photo we keep: "Close-up of photo P07:" and the image
(extraction._build_input; pipeline.read_crops picks which).

The stored photo and its AI copy (photos.ai_input_path) are never changed. A
close-up is a JPEG of its own under STORAGE_DIR/ai_derived/crops/, named by
the sha256 of the image it was cut from and how it was cut, and used again
when it is there -- a re-read or the benchmark's repeats cut nothing twice.
That folder is not under intake/ or incidents/, so /files never serves it
(storage/files.case_id_for_file).

Nothing here raises: a close-up that can't be made is left out and the
reading goes on without it.
"""
import hashlib
import io
import json
import logging
import os
import threading
from pathlib import Path

from app.ai.triage import clean_box
from app.config import settings
# Imported for its side effect too: it registers Pillow's HEIC opener, and
# an iPhone original is the largest copy of its photo.
from app.storage import files as _files  # noqa: F401

logger = logging.getLogger(__name__)

# The box grows by this share of its own width on the left and on the right
# (and of its height above and below): the reader names a part by what is
# round the damage -- a dent alone doesn't say door or wing.
GROW = 0.30
# Each way, never less than this share of the photo's shorter side: a box
# round a chip would otherwise be a crop of a few pixels.
MIN_SHARE = 0.25
# A close-up over half the photo is at most ~1.4 times closer than the photo
# itself: one more image's tokens for nearly nothing. Left out.
MAX_AREA_SHARE = 0.5
# As large as the previews the sorter and the reader see (storage/files.py
# PREVIEW_MAX_EDGE): Gemini scales every image to its budget anyway.
MAX_EDGE = 1600
QUALITY = 85
# Part of each file's name: changed when the way a close-up is cut changes,
# so one cut the old way is never used again.
CROP_VERSION = "v1"


def crop_dir() -> Path:
    return Path(settings.storage_dir) / "ai_derived" / "crops"


def _span(lo: float, hi: float, minimum: float, limit: int) -> tuple[float, float]:
    """lo..hi widened about its middle to at least `minimum`, then moved
    inside 0..limit -- moved rather than cut, so a box at the photo's edge
    keeps its size -- and cut only when longer than the photo."""
    size = max(hi - lo, minimum)
    middle = (lo + hi) / 2
    lo, hi = middle - size / 2, middle + size / 2
    if lo < 0:
        lo, hi = 0.0, hi - lo
    if hi > limit:
        lo, hi = lo - (hi - limit), float(limit)
    return max(0.0, lo), min(float(limit), hi)


def crop_window(box, width: int, height: int) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) in pixels of the close-up of `box`
    ([ymin, xmin, ymax, xmax] on 0-1000) on an image this size: grown by
    GROW each way, at least MIN_SHARE of the shorter side, inside the
    image. None for a box that makes no sense (triage.clean_box), an empty
    image, or a close-up over MAX_AREA_SHARE of the photo."""
    box = clean_box(box)
    if box is None or width < 1 or height < 1:
        return None
    ymin, xmin, ymax, xmax = box
    left, right = xmin * width / 1000, xmax * width / 1000
    top, bottom = ymin * height / 1000, ymax * height / 1000
    grow_x, grow_y = (right - left) * GROW, (bottom - top) * GROW
    minimum = MIN_SHARE * min(width, height)
    left, right = _span(left - grow_x, right + grow_x, minimum, width)
    top, bottom = _span(top - grow_y, bottom + grow_y, minimum, height)
    window = (round(left), round(top), round(right), round(bottom))
    w, h = window[2] - window[0], window[3] - window[1]
    if w < 1 or h < 1 or w * h > MAX_AREA_SHARE * width * height:
        return None
    return window


def _name(data: bytes, box: list[int]) -> str:
    """The close-up's file name: sha256 of the source image and everything
    that decides how it is cut."""
    how = json.dumps({"box": box, "grow": GROW, "min": MIN_SHARE, "max_area": MAX_AREA_SHARE, "edge": MAX_EDGE,
                      "quality": QUALITY, "version": CROP_VERSION}, sort_keys=True)
    return hashlib.sha256(data + b"\0" + how.encode("utf-8")).hexdigest() + ".jpg"


def _cut(data: bytes, box: list[int], dest: Path) -> bool:
    """Writes the close-up to dest; False when this box gives none
    (crop_window). The box is on the image the sorter was sent -- the
    preview, turned upright by its EXIF (storage/files.generate_preview) --
    so the original is turned the same way before it is cut."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as opened:
        image = ImageOps.exif_transpose(opened)
        window = crop_window(box, image.width, image.height)
        if window is None:
            return False
        piece = image.crop(window)
        if piece.mode != "RGB":
            piece = piece.convert("RGB")
        piece.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Written aside and moved into place: two reads cutting the same
        # close-up at once never hand Gemini half a file.
        tmp = dest.with_name(f"{dest.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            piece.save(tmp, "JPEG", quality=QUALITY)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
    return True


def make_crop(sources: list[str], box) -> str | None:
    """The path of the close-up of `box`, cut from the first of `sources`
    (the largest copy first: the original, then the AI copy) that Pillow
    can open -- or None: no sensible box, no copy that opens, or a box too
    large to be worth a close-up. A close-up made before is used as it is.
    Never raises."""
    box = clean_box(box)
    if box is None:
        return None
    for source in dict.fromkeys(s for s in sources if s):
        try:
            data = Path(source).read_bytes()
        except OSError:
            continue
        dest = crop_dir() / _name(data, box)
        try:
            if dest.is_file() and dest.stat().st_size > 0:
                return str(dest)
            # Same picture at every size: a box too large on one copy is
            # too large on the others.
            return str(dest) if _cut(data, box, dest) else None
        except Exception as exc:
            logger.warning("Close-up of %s not made: %s", Path(source).name, exc)
    return None
