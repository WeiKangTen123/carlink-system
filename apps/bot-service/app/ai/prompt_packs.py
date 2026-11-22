"""Instruction packs and prompt versions for the light models (plans/
2026-11-18-light-detection/P-prompts.md sections 2 and 3).

A pack is a block of instructions in a text file of its own,
app/ai/packs/<name>.txt, versioned by its name: orient-v1 (telling the
car's left from its right), cues-v1 (what counts as damage), small-v1 (the
small parts on a damaged panel) and intact-v1 (when a part may be called
intact). One wording, used word for word by every call that takes it, so
two arms of a benchmark never mix wordings and a changed wording is a new
file (orient-v2), never an edit of this one. The prompt templates that
place them, app/ai/prompts/<name>.txt (read-v2, sort-v3, link-v2), are kept
the same way. Both are read once, line endings LF whatever the checkout.

Every switch that uses them is off by default (config.py: intake_read_packs,
intake_read_prompt, intake_zone_packs, intake_sort_prompt, intake_link_*):
each call then sends what it sent before they existed, byte for byte
(scripts/test_light_packs.py).

While one is on, the call's tries carry a "prompt" tag in the run log
(runs.prompt_tag, tag below): the call, its prompt version and packs, and
the sha256 of what it sent, so a run can be told apart from another long
after the setting was changed back.
"""
import hashlib
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKS_DIR = HERE / "packs"
PROMPTS_DIR = HERE / "prompts"

# A pack's short name, as the settings write it, and the file it means.
PACK_FILES = {"orient": "orient-v1", "cues": "cues-v1", "small": "small-v1", "intact": "intact-v1"}
# The rows of small-v1, by the panel each starts with, that each kind of
# zone read gets (P-prompts.md 3.3); a close-up zone gets them all.
SMALL_ROWS = {
    "rear": ("Rear Bumper", "Boot Lid or tailgate", "Rear Quarter Panel"),
    "front": ("Front Bumper", "Bonnet", "Front Fender"),
    "left": ("Front Fender", "Front Door", "Rear Quarter Panel", "Wheel (the rim)"),
    "right": ("Front Fender", "Front Door", "Rear Quarter Panel", "Wheel (the rim)"),
}


@lru_cache(maxsize=32)
def _read(folder: Path, name: str) -> str:
    # read_text turns CRLF into LF (universal newlines).
    return (folder / f"{name}.txt").read_text(encoding="utf-8").strip()


def pack(short: str) -> str:
    """A pack's text by its short name ("orient"), as the file has it."""
    return _read(PACKS_DIR, PACK_FILES[short])


def template(name: str) -> str:
    """A prompt template's text ("read-v2")."""
    return _read(PROMPTS_DIR, name)


def sha(text: str, n: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def packs_sha256() -> dict[str, str]:
    """{file name: sha256} of every pack and template, for a benchmark's
    result file."""
    out = {}
    for folder in (PACKS_DIR, PROMPTS_DIR):
        for path in sorted(folder.glob("*.txt")):
            out[f"{folder.name}/{path.name}"] = sha(_read(folder, path.stem), 64)
    return out


def once(text: str, old: str, new: str) -> str:
    """text with old, which must be there exactly once, replaced by new: a
    template or prompt edited out from under a switch fails loudly (the
    tests run every switch) rather than sending a half-built prompt."""
    count = text.count(old)
    if count != 1:
        raise ValueError(f"prompt build: {old[:60]!r} is there {count} times, not once")
    return text.replace(old, new)


def small_rows(zone: str) -> str:
    """small-v1 for one zone read: its two opening lines and the rows of
    the panels that zone's checklist has (SMALL_ROWS), in the pack's order;
    every row for a close-up zone."""
    lines = pack("small").split("\n")
    head, rows = lines[:2], lines[2:]
    wanted = SMALL_ROWS.get(zone)
    if wanted is None:
        return "\n".join(lines)
    by_panel = {row[2:].split(":", 1)[0]: row for row in rows}
    missing = [panel for panel in wanted if panel not in by_panel]
    if missing:
        raise ValueError(f"small-v1 has no row for {missing}")
    return "\n".join(head + [row for row in rows if row[2:].split(":", 1)[0] in wanted])


def tag(call: str, version: str, packs, text: str) -> str:
    """The run log's tag of one call: "read-v2+orient-v1+cues-v1@<sha of
    what was sent>"."""
    names = "".join(f"+{PACK_FILES.get(p, p)}" for p in packs)
    return f"{call}-{version}{names}@{sha(text)}"
