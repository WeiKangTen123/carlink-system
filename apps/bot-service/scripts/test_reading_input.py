"""What the intake reader is sent (contract 6, P), offline -- no Gemini call.

  - photo choice fills every slot: after the damage, the photos of the
    insured car that show damage (tyre, "other", interior), then the rest,
    the other party's car, the scene and paperwork last; a damage-showing
    "other" photo with no part is read (a removed window regulator); the
    limit is a setting (intake_max_read_photos, default 32);
  - each photo's label is followed by the sorter's hint -- kind, front or
    rear, part without its side, note without side words -- and a request
    with no hints (the bot, the wizard's analyze-photos) is as before;
  - the reading rules: door / sill / rear quarter by the shut lines, the
    panels either side of a damaged door, wheel = rim, door internals; the
    boot rules kept.

    python3 scripts/test_reading_input.py
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="reading-input-")
os.environ.setdefault("STORAGE_DIR", _tmp)
os.environ.setdefault("SECRETS_DIR", tempfile.mkdtemp(prefix="secrets-"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'test.db').as_posix()}")
os.environ.setdefault("GEMINI_API_KEY", "fake")

import app.ai.structured as structured  # noqa: E402

GEMINI: list[str] = []


def _no_gemini(key=None):
    GEMINI.append("get_client")
    raise AssertionError("no Gemini call in this test")


structured.get_client = _no_gemini

from app.ai import extraction  # noqa: E402
from app.ai.structured import image_part  # noqa: E402
from app.intake import pipeline  # noqa: E402
from app.intake.pipeline import MAX_READ_PHOTOS, select_for_reading  # noqa: E402
from app.reports.models import IntakePhoto  # noqa: E402

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}


def check(cond, label: str, why: str = "") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append((_section["name"], label))
        print(f"    FAIL {label}" + (f"\n         {why}" if why else ""))
    return bool(cond)


def run_section(name: str, fn) -> None:
    _section["name"] = name
    print(f"\n{name}")
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()


def photo(i: int, kind: str | None, area: str | None = None, part: str | None = None, damage: bool = False,
          **kw) -> IntakePhoto:
    return IntakePhoto(id=f"p{i}", report_id="r", position=i, path="", sha256="", kind=kind, area=area, part=part,
                       quality=kw.pop("quality", "good"), shows_damage=damage, excluded=kw.pop("excluded", False), **kw)


def right_side_case() -> list[IntakePhoto]:
    """41 photos shaped like the right-side case that read 28 with 4 slots
    empty: 16 damage close-ups, 6 tyres (2 showing damage), 8 "other" (3
    showing damage, no part -- the sorter can't name a part outside the
    list), 2 interior (1 showing damage), and one each of the rest."""
    parts = ["Right Rear Door"] * 6 + ["Right Front Fender"] * 4 + ["Right Sill / Rocker Panel"] * 3 + ["Right Front Wheel"] * 3
    case = [photo(0, "log_card"), photo(1, "odometer")]
    case += [photo(2 + i, "overview", area) for i, area in enumerate(("right", "front-right", "rear-right"))]
    case += [photo(5 + i, "damage", "right", part, True) for i, part in enumerate(parts)]
    case += [photo(21 + i, "tyre", "right", damage=i in (2, 4)) for i in range(6)]
    case += [photo(27 + i, "other", "right", damage=i in (1, 3, 6)) for i in range(8)]
    case += [photo(35, "interior"), photo(36, "interior", "interior", damage=True)]
    case += [photo(37, "scene"), photo(38, "third_party"), photo(39, "document"), photo(40, "vin_plate")]
    assert len(case) == 41
    return case


def ids(photos) -> set[int]:
    return {p.position for p in photos}


# ---------------------------------------------------------------- photo choice


def fills_every_slot() -> None:
    case = right_side_case()
    chosen = select_for_reading(case)
    got = ids(chosen)
    check(len(chosen) == MAX_READ_PHOTOS == 32, f"41 usable photos: all {MAX_READ_PHOTOS} slots filled", f"{len(chosen)}")
    check(set(range(5, 21)) <= got, "every damage close-up read")
    check({28, 30, 33} <= got, "the 3 'other' photos showing damage are read though the sorter named no part")
    check({23, 25, 36} <= got, "the tyres and the interior photo showing damage are read")
    check(37 not in got and not {27, 29, 31, 32, 34} & got, "no room left for the scene or the 'other' photos showing no damage")
    check([p.position for p in chosen] == sorted(got), "sent in camera order")

    pipeline.MAX_READ_PHOTOS = 30
    try:
        got = ids(select_for_reading(case))
        check(len(got) == 30 and {25, 36} <= got and not {21, 22, 24, 26} & got,
              "2 slots after the budgets: the damage-showing tyre and interior photo, not a damage-free tyre", sorted(got))
    finally:
        pipeline.MAX_READ_PHOTOS = 32


def last_kinds_last() -> None:
    case = right_side_case()
    real = pipeline.settings
    pipeline.settings = SimpleNamespace(intake_max_read_photos=40)
    try:
        chosen = select_for_reading(case)
        missing = set(range(41)) - ids(chosen)
        check(pipeline.read_limit() == 40 and len(chosen) == 40, "the intake_max_read_photos setting raises the limit to 40",
              f"{len(chosen)}")
        check(missing == {37}, "40 of 41: only the scene is left out -- the rest of the car goes before it", sorted(missing))
    finally:
        pipeline.settings = real
    check(pipeline.read_limit() == MAX_READ_PHOTOS, "no setting: MAX_READ_PHOTOS (32)")

    # A small case: everything usable goes, the scene, the other party's
    # car and every document included; excluded and unusable photos never.
    small = [photo(0, "damage", "rear", "Rear Bumper", True), photo(1, "scene"), photo(2, "third_party"),
             photo(3, "document"), photo(4, "document"), photo(5, "document"), photo(6, "vin_plate"), photo(7, "vin_plate"),
             photo(8, "tyre"), photo(9, "tyre"), photo(10, "other"), photo(11, "interior"), photo(12, None),
             photo(13, "damage", "rear", excluded=True), photo(14, "other", quality="unusable")]
    check(ids(select_for_reading(small)) == set(range(13)),
          "a 15-photo case: all 13 usable photos read (2nd and 3rd document, 2nd VIN plate, scene too)")


def other_without_part() -> None:
    # 40 damage close-ups of 4 parts and one "other" photo of an unlisted
    # part (no part from the sorter): a group of its own in the damage
    # order, so it is read even when damage fills every slot.
    parts = ["Right Rear Door", "Right Front Door", "Right Front Fender", "Right Rear Quarter Panel"]
    case = [photo(i, "damage", "right", parts[i % 4], True) for i in range(40)]
    case.append(photo(40, "other", "right", None, True))
    check(40 in ids(select_for_reading(case)), "a damage-showing 'other' photo with no part competes with the damage photos")
    case[40].shows_damage = False
    check(40 not in ids(select_for_reading(case)), "one showing no damage doesn't (damage fills the slots)")
    interior = case[:40] + [photo(40, "interior", "interior", None, True)]
    check(40 not in ids(select_for_reading(interior)), "interior still needs a part to count as damage (only 'other' changed)")


# ---------------------------------------------------------------- captions


def hints() -> None:
    hint = extraction.photo_hint({"kind": "damage", "area": "rear-right", "part": "Right Rear Door",
                                  "note": "Dent low on RH rear door near handle"})
    check(hint == "Sorter's hint: damage close-up; area: rear; part: Rear Door; note: Dent low on rear door near handle",
          "damage close-up: kind, front/rear of a corner, part without its side, note without LH/RH", hint)
    check(extraction.photo_hint({"kind": "tyre", "area": "right", "part": "Right Front Wheel", "note": "Right tyre sidewall scuffed"})
          == "Sorter's hint: tyre; part: Front Wheel; note: tyre sidewall scuffed", "a plain side area is dropped, not worded")
    check(extraction.photo_hint({"kind": "other", "area": None, "part": None, "note": "Removed window regulator, bent"})
          == "Sorter's hint: other; note: Removed window regulator, bent", "'other' with no part: kind and note")
    check(extraction.photo_hint({"kind": "log_card", "note": "Log card of SXX1234A"}) == "Sorter's hint: log card"
          and extraction.photo_hint({"kind": "third_party", "area": "left", "part": "Left Front Door", "note": "Dented"})
          == "Sorter's hint: the other party's vehicle", "documents and the other party's car: the kind only (no misread numbers)")
    check(extraction.photo_hint({"kind": None}) is None and extraction.photo_hint(None) is None, "unsorted: no hint")
    check(extraction.photo_hint({"kind": "damage", "part": "Other / Not Listed"}) == "Sorter's hint: damage close-up",
          "'Other / Not Listed' is no part to hint")
    sides = {
        "Front-left fender scratched": "Front fender scratched",
        "Scratches on the driver's side door": "Scratches on the door",
        "left-hand side mirror cracked": "mirror cracked",
        "R/H door dent, passenger side": "door dent",
        "Nearside sill scraped": "sill scraped",
        "Bright reflection on bonnet": "Bright reflection on bonnet",
        "LEFT": "",
    }
    got = {note: extraction._without_side(note) for note in sides}
    check(got == sides, "side words are taken out of the note (left/right, LH/RH, L/H, near/offside, driver/passenger side)", got)


def build_input() -> None:
    img = Path(_tmp, "a.jpg")
    img.write_bytes(b"\xff\xd8\xff\xe0fake")
    paths = [str(img)] * 3
    labels = ["P02", "P05", "P09"]
    hint_dicts = [{"kind": "log_card", "area": None, "part": None, "note": "Log card"},
                  None,  # unsorted
                  {"kind": "damage", "area": "front-left", "part": "Left Front Fender", "note": "Left fender dented"}]
    parts = extraction._build_input("Hit on the side.", paths, None, labels, hint_dicts)
    texts = [p["text"] for p in parts if p["type"] == "text"]
    check(texts[2:] == ["Photo P02:", "Sorter's hint: log card", "Photo P05:", "Photo P09:",
                        "Sorter's hint: damage close-up; area: front; part: Front Fender; note: fender dented"],
          "label part unchanged; the hint is its own text part after it; an unsorted photo has none", texts[2:])
    check([p["type"] for p in parts[-2:]] == ["text", "image"] and parts[-1] == image_part(str(img)),
          "each hint sits between its label and its image; the image part is as before (resolution hook a no-op)")
    check("sorter's hint" in texts[1] and "never gives the side" in texts[1], "the selection note explains the hints")
    check(not any("left" in t.lower() for t in texts[2:]), "no side word reaches the reader through a hint")

    # Without hints the request is exactly as before this change.
    old_intro = ("Only a selection of this case's photos is shown below. Each is labelled with its "
                 "case photo number -- use exactly that label for 'photo_reference'.")
    plain = extraction._build_input("Hit on the side.", paths, None, labels)
    expected = [{"type": "text", "text": "Reporter's description:\nHit on the side."}, {"type": "text", "text": old_intro}]
    for label in labels:
        expected += [{"type": "text", "text": f"Photo {label}:"}, image_part(str(img))]
    check(plain == expected, "no hints (intake before, any other caller): the very same parts as before")
    bot = extraction._build_input("desc", paths[:2], {"plate": "SXX1A"})
    check([p.get("text") for p in bot if p["type"] == "text"][1:] == ["Reporter's description:\ndesc", "Photo P01:", "Photo P02:"],
          "the bot's request (no labels, no hints): unchanged")
    extraction.SORTER_HINTS = False
    try:
        check(extraction._build_input("Hit on the side.", paths, None, labels, hint_dicts) == expected,
              "SORTER_HINTS off (A/B): the request is as without hints")
    finally:
        extraction.SORTER_HINTS = True
    # The no-op hook became structured.image_part(path, kind) (c80aa10).
    check(structured.image_resolution("damage") is None and image_part(str(img), kind="damage") == image_part(str(img)),
          "no gemini_image_resolution setting: a photo's kind adds nothing (Gemini's default, as before)")


def prompt_rules() -> None:
    prompt = extraction.SYSTEM_PROMPT
    check(all(s in prompt for s in ("shut lines", "A dent on a door's skin, even low down, is the door",
                                    "the sill is the panel below the doors", "behind the rear door")),
          "door vs sill vs rear quarter by the shut lines")
    # The parts the rules name are listed parts since agent T's (Wheel Arch
    # Trim, Tyre, Window Regulator).
    check("When a door is damaged, check the panels either side of it" in prompt
          and "the 'Wheel Arch Trim' (the garnish on the arch's edge) and the 'Wheel Arch Liner' (the splash shield" in prompt,
          "a damaged door: check the fender, other door, quarter, sill and the Wheel Arch Trim / Liner")
    check("'Wheel' is the rim and 'Tyre' the tyre" in prompt and "tyre damage on the Tyre" in prompt
          and "list the Tyre with 'ai_confidence' 'Low' and no 'severity'" in prompt,
          "rim damage on the Wheel, tyre damage on the Tyre; a maybe-serviceable tyre at Low confidence, no severity")
    check("list the door AND its 'Window Regulator'" in prompt
          and "the door AND 'Other / Not Listed' with its name in 'other_part_name'" in prompt
          and "'other_part_name' -- never on a listed part" in prompt,
          "a regulator: the door and the Window Regulator; other door internals named in other_part_name, only on an Other")
    check(all(s in prompt for s in ("'Boot Lid' means a boot lid, tailgate or hatch door", "'Rear End Panel'",
                                    "a reflector in the bumper is a 'Rear Bumper Reflector'")),
          "the boot rules are kept")


def no_ai() -> None:
    check(not GEMINI, "no Gemini call anywhere in this test", repr(GEMINI))


def main() -> int:
    for name, fn in (
        ("every slot filled, damage first", fills_every_slot),
        ("the scene, the other party's car and paperwork last; the limit as a setting", last_kinds_last),
        ("a damage-showing 'other' photo with no part", other_without_part),
        ("the sorter's hints, without sides", hints),
        ("the reader's input parts", build_input),
        ("the reading rules", prompt_rules),
        ("no AI", no_ai),
    ):
        run_section(name, fn)
    print(f"\ntest_reading_input: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        for section, label in FAILURES:
            print(f"  FAIL [{section}] {label}")
        return 1
    print("\nALL READING INPUT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
