"""Reference examples inside the reading request: "fine-tuning without
training" (2026-11-10).

The light reader (gemini-3.5-flash-lite, the main reader on the free tier)
flips sides on some cases: a car damaged on its right, photographed facing
its front corner, read as "Left" all through. Rules in the prompt didn't
cure it. So the intake reading can carry a few labelled photos from OTHER
cases before the case's own, each with the lesson it teaches in plain
words, and the model learns the firm's conventions from them.

settings.gemini_read_examples names a JSON manifest:

    {"intro": "<optional text>",
     "examples": [{"id": "e1", "image": "<path, relative to the manifest>",
                   "case": "<case tag, optional>", "caption": "<the lesson>"}]}

It is read with every reading (load): each image there, each caption
saying something, at most MAX_EXAMPLES. A bad manifest never stops a
reading: a warning in the log, and no examples (for_reading). Unset, the
request is byte for byte what it was.

Only the intake reading sends them (intake/pipeline._read_into, the
"draft" call): the sorter, the second look, the Telegram/WhatsApp drafts
and the wizard's analyze-photos never do. The photos are the integrator's
choice and stay out of git: a case photo shows a plate.
"""
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

MAX_EXAMPLES = 6
# What one example adds to a reading: its image at Gemini's default
# resolution ("high", 1120 tokens on Gemini 3) -- its caption is a few
# dozen more. Logged with each reading that sends them.
IMAGE_TOKENS = 1120
# The image types the reading call sends (structured.image_part's
# mimetypes): anything else would go out labelled image/jpeg.
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".heic")

DEFAULT_INTRO = (
    "Reference examples from OTHER cases come first, each labelled with the lesson it shows about how this firm "
    "names parts and sides. They are not part of this case: never report their damage."
)

# Cases whose own examples are left out: scripts/benchmark_intake.py sets
# the case it is reading (leave-one-out), so a case is never helped by its
# own photos. Matched on letters and digits only, any case. Empty in the app.
HELD_OUT: tuple[str, ...] = ()


class ManifestError(ValueError):
    """What is wrong with a manifest, in words for the log."""


@dataclass(frozen=True)
class Example:
    id: str
    image: str  # absolute path
    caption: str
    case: str | None = None


@dataclass(frozen=True)
class Examples:
    intro: str
    items: tuple[Example, ...]

    def __len__(self) -> int:
        return len(self.items)

    @property
    def ids(self) -> list[str]:
        return [e.id for e in self.items]

    def without_cases(self, cases) -> "Examples":
        """These examples minus those tagged with one of `cases`."""
        held = {case_key(c) for c in cases if c}
        return Examples(self.intro, tuple(e for e in self.items if not (e.case and case_key(e.case) in held)))


def case_key(text) -> str:
    """A case tag as compared: "SLC 2502 Y", "slc2502y" and "SLC-2502-Y" are
    one case."""
    return re.sub(r"[^0-9a-z]", "", str(text or "").lower())


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def load(path) -> Examples:
    """The manifest at `path`, checked. Raises ManifestError saying what is
    wrong: not JSON, no examples or more than MAX_EXAMPLES, an example
    without an id (or one used twice), without a caption, or whose image
    isn't there or isn't a photo."""
    manifest = Path(path)
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"can't read {manifest}: {exc.strerror or exc}") from None
    except ValueError as exc:
        raise ManifestError(f"{manifest} is not JSON: {exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("examples"), list):
        raise ManifestError('needs {"examples": [...]}')
    entries = data["examples"]
    if not entries:
        raise ManifestError("no examples")
    if len(entries) > MAX_EXAMPLES:
        raise ManifestError(f"{len(entries)} examples: at most {MAX_EXAMPLES}")
    intro = data.get("intro")
    if intro is not None and not isinstance(intro, str):
        raise ManifestError("intro: must be text")
    items: list[Example] = []
    seen: set[str] = set()
    for n, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ManifestError(f"example {n}: must be an object")
        example_id = _text(entry.get("id"))
        if not example_id:
            raise ManifestError(f"example {n}: no id")
        where = f"example {example_id!r}"
        if example_id in seen:
            raise ManifestError(f"{where}: id used twice")
        seen.add(example_id)
        caption = _text(entry.get("caption"))
        if not caption:
            raise ManifestError(f"{where}: no caption")
        image = _text(entry.get("image"))
        if not image:
            raise ManifestError(f"{where}: no image")
        # Relative to the manifest, so the folder can move as a whole; an
        # absolute path stays as it is.
        image_path = (manifest.parent / image).resolve()
        if image_path.suffix.lower() not in IMAGE_SUFFIXES:
            raise ManifestError(f"{where}: {image_path.name} is not a photo ({', '.join(IMAGE_SUFFIXES)})")
        if not image_path.is_file():
            raise ManifestError(f"{where}: no image at {image_path}")
        case = entry.get("case")
        if case is not None and not isinstance(case, str):
            raise ManifestError(f"{where}: case must be text")
        items.append(Example(example_id, str(image_path), caption, _text(case) or None))
    return Examples(_text(intro) or DEFAULT_INTRO, tuple(items))


def for_reading() -> Examples | None:
    """The examples an intake reading sends now, or None: the setting
    unset, the manifest unusable (logged, never raised -- a reading goes on
    without them), or every example held out (HELD_OUT)."""
    path = settings.gemini_read_examples
    if not path:
        return None
    try:
        examples = load(path)
    except Exception as exc:
        logger.warning("Reading without reference examples: the manifest %s is not usable: %s", path, exc)
        return None
    if HELD_OUT:
        examples = examples.without_cases(HELD_OUT)
    return examples if examples.items else None
