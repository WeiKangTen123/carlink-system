"""Grid marks for the zone reads (contract 8 D2, settings.intake_grid_marks).

The light models name a panel without saying where on the photo they saw
it, and never give a bounding box (extraction's bbox_2d: only the strong
model localises). A light 3x3 grid drawn on the photo, its cells labelled
A1..C3, gives them something to point at: the zone call (ai/zone_read.py)
asks for the cells the damage covers first, then the panel in those cells
-- looking before naming. The cells are kept on the item (grid_cells) so a
person can see where the AI looked.

Drawn on a derived copy, never on the stored photo or the AI input copy
(intake/photos.ai_input_path): STORAGE_DIR/ai_derived/grid/<sha256 of the
input and the drawing's parameters>.jpg, made once and reused -- a photo is
read again on every re-read, and an overview can sit in two zones.
"""
import hashlib
import logging
import os
import threading
from io import BytesIO
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

ROWS = "ABC"
COLUMNS = 3
# Row letter from the top, column number from the left: A1 top left, C3
# bottom right -- what the prompt tells the model.
CELLS = [f"{row}{col}" for row in ROWS for col in range(1, COLUMNS + 1)]
# Part of every derived file's name: a change to the drawing below must
# change it, or an old copy would be served for the new drawing.
DRAWING = "grid-v1-3x3-a1c3"
JPEG_QUALITY = 88

_lock = threading.Lock()


def grid_dir() -> Path:
    return Path(settings.storage_dir) / "ai_derived" / "grid"


def grid_path(source: str) -> str:
    """The copy of `source` with grid marks, made when it isn't there yet.
    Raises when the image can't be read (the caller sends the plain photo)."""
    data = Path(source).read_bytes()
    digest = hashlib.sha256(data + b"\0" + DRAWING.encode("utf-8")).hexdigest()
    out = grid_dir() / f"{digest}.jpg"
    if out.exists() and out.stat().st_size > 0:
        return str(out)
    # Two zone calls running at once can want the same overview: one draws
    # it, the other then finds it. Written aside and moved into place, so
    # no one reads a half-written copy.
    with _lock:
        if not (out.exists() and out.stat().st_size > 0):
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(f"{out.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
            draw_grid(data).save(tmp, "JPEG", quality=JPEG_QUALITY)
            try:
                os.replace(tmp, out)
            except OSError:
                # Another process made it meanwhile (Windows won't replace
                # a file someone is reading): theirs is the same drawing.
                tmp.unlink(missing_ok=True)
                if not out.exists():
                    raise
    return str(out)


def draw_grid(data: bytes):
    """The image in `data` (upright, as the preview is: exif_transpose) with
    two light lines each way and each cell's label in its top-left corner.
    White lines over a faint dark edge, so they read on paint of any colour
    without hiding the scratch under them."""
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    with Image.open(BytesIO(data)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    width, height = image.size
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    line = max(2, round(min(width, height) / 300))
    xs = [round(width * i / COLUMNS) for i in range(1, COLUMNS)]
    ys = [round(height * i / len(ROWS)) for i in range(1, len(ROWS))]
    for x in xs:
        draw.line([(x + line, 0), (x + line, height)], fill=(0, 0, 0, 70), width=line)
        draw.line([(x, 0), (x, height)], fill=(255, 255, 255, 150), width=line)
    for y in ys:
        draw.line([(0, y + line), (width, y + line)], fill=(0, 0, 0, 70), width=line)
        draw.line([(0, y), (width, y)], fill=(255, 255, 255, 150), width=line)
    size = max(12, round(min(width, height) / 22))
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:  # Pillow before 10.1: the small bitmap font
        font = ImageFont.load_default()
    pad = max(2, size // 5)
    lefts = [0] + xs
    tops = [0] + ys
    for r, row in enumerate(ROWS):
        for c in range(COLUMNS):
            label = f"{row}{c + 1}"
            x, y = lefts[c] + pad + line, tops[r] + pad + line
            box = draw.textbbox((x, y), label, font=font)
            draw.rectangle((box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad), fill=(0, 0, 0, 120))
            draw.text((x, y), label, font=font, fill=(255, 255, 255, 230))
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
