"""The photo sort (app/ai/triage.py) and the car's sides.

Why this exists: on a Honda Vezel hit on its right side the sort called the
right-side photos "Left Front Door" and "Left Front Wheel Arch Liner" --
read off the frame, with the photographer in front of the car facing it.
The prompt now says how to turn the frame into the car's own sides (angle
and batch_sides come first in the answer), and _clean drops a part's side
when it disagrees with the photo's area, keeping the family.

Covers: _clean's side rule (a disagreeing side dropped, an agreeing one
kept, an area with no side never drops one, "Other / Not Listed" still
blanked, a sided part with no side-undetermined name dropped); the request
(one call per batch, the schema's field order, the prompt's side cues) and
parsing a model's answer with and without the new fields.

    python scripts/test_triage.py

Offline: the model call is a fake; no Gemini.
"""
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = Path(tempfile.mkdtemp(prefix="triage-test-"))
os.environ.setdefault("STORAGE_DIR", str(_tmp))
os.environ.setdefault("SECRETS_DIR", tempfile.mkdtemp(prefix="secrets-"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(_tmp / 'test.db').as_posix()}")
os.environ.setdefault("GEMINI_API_KEY", "fake")

import app.ai.triage as triage  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.ai.triage import ANGLES, BATCH_SIZE, _clean  # noqa: E402
from app.reports.taxonomy import CANONICAL_PARTS  # noqa: E402

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": ""}


class Stop(Exception):
    pass


def check(cond, label: str, why="") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
        return True
    FAILURES.append((_section["name"], label + (f"  [{why}]" if why else "")))
    print(f"    FAIL {label}" + (f"\n           got: {why}" if why else ""))
    return False


def need(cond, label: str, why="") -> None:
    if not check(cond, label, why):
        raise Stop(label)


def run_section(name: str, fn) -> None:
    _section["name"] = name
    print(f"\n[{name}]")
    try:
        fn()
    except Stop as exc:
        print(f"    ---  rest of this section skipped: {exc}")
    except Exception as exc:
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if Path(f.filename).resolve() == Path(__file__).resolve()]
        msg = f"section stopped by {type(exc).__name__}: {exc} (test line {frames[-1].lineno if frames else -1})"
        FAILURES.append((name, msg))
        print(f"    FAIL {msg}")


def entry(part, area, kind="damage", **extra) -> dict:
    return {"kind": kind, "area": area, "quality": "good", "shows_damage": True, "part": part, "note": "x", **extra}


def part_of(part, area) -> str | None:
    return _clean(entry(part, area))["part"]


def clean_sides() -> None:
    # The Vezel's photos: the car's right, named from the frame.
    for part, area, family in (
        ("Left Front Door", "right", "Door (side undetermined)"),
        ("Left Front Wheel Arch Liner", "front-right", "Wheel Arch Liner (side undetermined)"),
        ("Left Front Wheel", "front-right", "Wheel (side undetermined)"),
        ("Right Tail Lamp", "rear-left", "Tail Lamp (side undetermined)"),
        ("Right Rear Quarter Panel", "left", "Quarter Panel (side undetermined)"),
        ("Right Headlamp", "front-left", "Headlamp (side undetermined)"),
    ):
        got = part_of(part, area)
        check(got == family, f"{part} on a {area} photo -> {family}", got)
    for part, area in (("Right Rear Door", "right"), ("Right Front Door", "front-right"), ("Left Tail Lamp", "rear-left"),
                       ("Left Front Fender", "left"), ("Right Rear Bumper Reflector", "rear-right")):
        got = part_of(part, area)
        check(got == part, f"{part} on a {area} photo is kept", got)
    for area in ("rear", "front", "roof", "underbody", "interior", None):
        got = part_of("Left Tail Lamp", area)
        check(got == "Left Tail Lamp", f"an area with no side ({area}) never drops a side", got)
    for part in ("Rear Bumper", "Door (side undetermined)", "Boot Lid"):
        got = part_of(part, "rear-right")
        check(got == part, f"a part with no side ({part}) is untouched by a sided area", got)
    # Unchanged: the escape hatch groups nothing for reading, so it is blanked.
    for area in ("right", "rear", None):
        got = part_of("Other / Not Listed", area)
        check(got is None, f"'Other / Not Listed' is still blanked ({area})", got)
    got = _clean(entry("Left Front Doorr", "right"))
    check(got["part"] is None, "a name off the list is still dropped", got)
    got = _clean(entry("Left Front Door", "the right side"))
    check(got["area"] is None and got["part"] == "Left Front Door",
          "an area off the list is dropped and can't contradict the part", got)
    # A sided part with no "(side undetermined)" name to fall back on.
    CANONICAL_PARTS.append("Left Test Bracket")
    try:
        got = part_of("Left Test Bracket", "right")
        check(got is None, "a disagreeing sided part with no side-undetermined name is dropped", got)
        got = part_of("Left Test Bracket", "rear-left")
        check(got == "Left Test Bracket", "... and kept when the area agrees", got)
    finally:
        CANONICAL_PARTS.remove("Left Test Bracket")
    got = _clean(entry("Right Rear Door", "right", angle="front to photo right"))
    check(set(got) == {"kind", "area", "quality", "shows_damage", "part", "note"},
          "angle only steers the model: not in the stored result", sorted(got))


def photos(n: int) -> list[tuple[str, str]]:
    out = []
    for i in range(1, n + 1):
        path = _tmp / f"IMG_{i:04d}.jpg"
        path.write_bytes(b"\xff\xd8\xff\xe0 not a real jpeg")
        out.append((f"P{i:02d}", str(path)))
    return out


class FakeCall:
    """Stands in for structured.call_structured: keeps each request and
    answers with the next canned answer through the request's own parse."""

    def __init__(self):
        self.requests: list[dict] = []
        self.answers: list[dict] = []

    def __call__(self, **kwargs):
        self.requests.append(kwargs)
        return kwargs["parse"](json.dumps(self.answers.pop(0)))


def request_and_answer() -> None:
    fake = FakeCall()
    real = triage.call_structured
    triage.call_structured = fake
    try:
        batch = photos(BATCH_SIZE)
        rows = [
            {"label": "P01", "kind": "overview", "angle": "front toward camera", "area": "front-right", "part": None},
            # Named off the frame: the part says left, the area right.
            {"label": "P02", "kind": "damage", "angle": None, "area": "right", "part": "Left Front Door"},
            {"label": "P03", "kind": "damage", "angle": "front toward camera", "area": "front-right",
             "part": "Left Front Wheel Arch Liner"},
            {"label": "P04", "kind": "damage", "angle": "front to photo right", "area": "right", "part": "Right Rear Door"},
            {"label": "P05", "kind": "damage", "angle": None, "area": "right", "part": "Other / Not Listed"},
            {"label": "P06", "kind": "log_card", "angle": None, "area": None, "part": None},
            {"label": "p07", "kind": "damage", "angle": "front away from camera", "area": "rear-right", "part": "Right Tail Lamp"},
        ] + [{"label": f"P{i:02d}", "kind": "overview", "angle": "sideways", "area": "right", "part": None}
             for i in range(8, BATCH_SIZE + 1)]
        rows.append({"label": "P99", "kind": "damage", "angle": None, "area": "right", "part": "Right Front Door"})
        for row in rows:
            row.update(quality="good", shows_damage=row["part"] is not None, note=f"photo {row['label']}")
        fake.answers.append({"batch_sides": "From the front: dent on the photo's left = the car's right front",
                             "photos": rows})
        result = triage.triage_photos(batch, actor=AiActor.cli())

        need(len(fake.requests) == 1, f"one call for a batch of {BATCH_SIZE}", len(fake.requests))
        req = fake.requests[0]
        check(req["models"] == triage._triage_chain(), "the sort's own (light) model chain", req["models"])
        images = [p for p in req["input_parts"] if p.get("type") == "image"]
        check(len(images) == BATCH_SIZE, "every photo sent, nothing else added", len(images))

        schema = req["schema"]
        check(list(schema["properties"]) == ["batch_sides", "photos"] and schema["required"] == ["batch_sides", "photos"],
              "batch_sides is asked for, before the photo entries", list(schema["properties"]))
        item = schema["properties"]["photos"]["items"]
        order = list(item["properties"])
        check(order.index("angle") < order.index("area") < order.index("part"),
              "each entry says its angle before its area and part", order)
        check(set(item["required"]) == set(order), "every entry field is required", item["required"])
        check(sorted(schema["properties"])[0] == "batch_sides" and sorted(order).index("angle") < sorted(order).index("area"),
              "... also when a model fills keys alphabetically", sorted(order))
        check(item["properties"]["angle"]["anyOf"][0]["enum"] == ANGLES and len(ANGLES) == 4,
              "angle is one of the four ways the front can point, or null")

        prompt = req["system_instruction"]
        for cue in ("insured car's OWN sides", "never the left or right of the photo", "right-hand drive",
                    "the steering wheel is on the car's right",
                    "toward the camera: the car's RIGHT side is on the photo's LEFT",
                    "to the photo's left: you are looking at the car's LEFT side",
                    "'(side undetermined)' name: a wrong side is worse than none"):
            check(cue in prompt, f"prompt: {cue!r}")
        check("as seen by its driver facing forward" not in prompt, "the old one-line side rule is replaced")

        check(set(result) == {f"P{i:02d}" for i in range(1, BATCH_SIZE + 1)},
              "every asked label answered (a lower-case one too); one not asked is dropped", sorted(result))
        check(result["P02"]["part"] == "Door (side undetermined)" and result["P02"]["area"] == "right",
              "the side that disagrees with the area is dropped; the area stays", result["P02"])
        check(result["P03"]["part"] == "Wheel Arch Liner (side undetermined)", "the wheel arch liner too", result["P03"])
        check(result["P04"]["part"] == "Right Rear Door", "an agreeing side is kept", result["P04"])
        check(result["P07"]["part"] == "Right Tail Lamp" and result["P07"]["area"] == "rear-right",
              "an agreeing corner is kept", result["P07"])
        check(result["P05"]["part"] is None and result["P06"]["part"] is None, "'Other / Not Listed' still blanked")
        check(all("angle" not in r and "batch_sides" not in r for r in result.values()),
              "angle and batch_sides aren't kept")

        # A model that leaves the new fields out (a fallback that ignores
        # them) is answered as before.
        fake.answers.append({"photos": [
            {"label": label, "kind": "damage", "area": "left", "quality": "good", "shows_damage": True,
             "part": "Left Rear Door", "note": None} for label, _ in batch[:4]]})
        result = triage.triage_photos(batch[:4], actor=AiActor.cli())
        check(len(result) == 4 and all(r["part"] == "Left Rear Door" for r in result.values()),
              "an answer without batch_sides or angle still parses", result)

        # Unchanged: under half the photos answered fails the call (the chain
        # moves on to another model).
        fake.answers.append({"batch_sides": None, "photos": [
            {"label": "P01", "kind": "damage", "angle": None, "area": None, "quality": "good", "shows_damage": False,
             "part": None, "note": None}]})
        try:
            triage.triage_photos(batch[:4], actor=AiActor.cli())
            check(False, "an answer for 1 of 4 photos is a failed call")
        except ValueError:
            check(True, "an answer for 1 of 4 photos is a failed call")
    finally:
        triage.call_structured = real


def version() -> None:
    # Sorts cached by the benchmark (--sort-cache) are keyed by it: answers
    # to the v1 question must not be served for this one.
    check(triage.SORT_PROMPT_VERSION not in ("v0", "v1-corner-areas"), "SORT_PROMPT_VERSION was bumped",
          triage.SORT_PROMPT_VERSION)


def main() -> int:
    for name, fn in (
        ("_clean: a part's side against the photo's area", clean_sides),
        ("triage_photos: the request and the answer", request_and_answer),
        ("sort prompt version", version),
    ):
        run_section(name, fn)
    print(f"\ntest_triage: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  [{section}] {label}")
    if FAILURES:
        return 1
    print("\nALL TRIAGE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
