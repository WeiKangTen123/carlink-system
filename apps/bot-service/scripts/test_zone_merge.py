"""Zone mode's merge paths, replayed on synthetic reads (contract 10 P0) --
offline, Gemini is a fake.

Why: a benchmark case lost a part in zone mode that the single read found.
These replays show what intake/zones.py and the side resolver do with each
shape of reads, through the same steps in the same order as
pipeline._read_into (side_from_sorter -> merge_reads -> split_suggestions ->
evidence.finish -> resolve_sides -> apply_agreement):

  - option B never loses a part the overall reading listed: every overall
    item stays on the list, renamed at most (a dispute makes it "(side
    undetermined)"; a zone read's side names an undetermined one) --
    checked on hand-made shapes and on 400 random ones;
  - a zone read listing a part INTACT changes nothing on the list;
  - the same part, both sides, on one photo: a dispute;
  - an undetermined overall item meeting a zone read's side, on its own
    photo and on another one;
  - the side resolver after a zone read gave an item its photo;
  - split_suggestions after a dispute;
  - side_from_sorter, before the merge, in both modes;
  - contract 10 (4.1): the zone reads' intact lists kept in
    ai_draft["zone_intact"], and an old draft without them still working.

    python scripts/test_zone_merge.py

Makes, parts and photos are made up; the photos are coloured squares.
"""
import copy
import io
import json
import os
import random
import re
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="zone-merge-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake-zone-merge-key"
os.environ["SECOND_LOOK_ENABLED"] = "false"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"
for name in ("GEMINI_MODEL_CHAIN", "GEMINI_ZONE_CHAIN", "INTAKE_READ_MODE", "INTAKE_ZONE_ONLY",
             "INTAKE_ZONE_EVIDENCE", "INTAKE_SIDE_FROM_SORTER"):
    os.environ.pop(name, None)

import app.ai.structured as structured  # noqa: E402

REQUESTS: list[dict] = []
_requests_lock = threading.Lock()


def _labels(parts: list[dict]) -> list[str]:
    return [m.group(1) for part in parts if part.get("type") == "text"
            for m in [re.match(r"Photo (P\d+):$", part["text"])] if m]


def kind_of(kwargs) -> str:
    props = kwargs["response_format"]["schema"].get("properties", {})
    return "zone" if "zone_side_seen" in props else "draft" if "damage_summary" in props else "other"


HANDLERS: dict = {}


class FakeInteractions:
    def create(self, **kwargs):
        with _requests_lock:
            REQUESTS.append(kwargs)
        out = HANDLERS[kind_of(kwargs)](kwargs, _labels(kwargs["input"]))
        return SimpleNamespace(output_text=out if isinstance(out, str) else json.dumps(out),
                               usage=SimpleNamespace(total_input_tokens=1000, total_output_tokens=100))


structured.get_client = lambda key=None: SimpleNamespace(interactions=FakeInteractions())
structured.acquire = lambda key=None: 0.0
structured.OVERLOADED_RETRY_SECONDS = 0.0
structured.CHAIN_BUSY_RETRY_SECONDS = 0.0

from PIL import Image  # noqa: E402

from app.ai import evidence, second_look  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import pipeline, zones  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import IntakePhoto, IntakeSession, Report  # noqa: E402
from app.reports.schema import DamageSummaryItem  # noqa: E402
from app.reports.taxonomy import UNDETERMINED_SUFFIX, split_side, unsided_part  # noqa: E402

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}


def check(cond, label: str, why="") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append((_section["name"], label))
        print(f"    FAIL {label}" + (f"\n         {why}" if why != "" else ""))
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


class Settings:
    """Sets settings for a block and puts them back."""

    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        self.old = {k: getattr(settings, k) for k in self.values}
        for k, v in self.values.items():
            setattr(settings, k, v)

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(settings, k, v)


# ---------------------------------------------------------------- the chain


def item(part, ref=None, conf="Medium", **kw) -> dict:
    return {**DamageSummaryItem(part=part, photo_reference=ref, ai_confidence=conf).model_dump(), **kw}


# The photos of a made-up rear-end case, camera order, with the sorter's
# area: two rear-left close-ups (@l2, @l), the rear (@a, @c), two
# rear-right close-ups (@r, @r2), a left side shot (@s) -- so that the
# resolver, which also asks a photo's neighbours, places @l Left and @r
# Right.
AREAS = [("l2", "rear-left"), ("l", "rear-left"), ("a", "rear"), ("c", "rear"), ("r", "rear-right"),
         ("r2", "rear-right"), ("s", "left")]


def zone_chain(overall: list[dict], reads: list[tuple[str, list[dict]]], areas=AREAS,
               sorter_parts: dict | None = None) -> tuple[list[dict], list[dict], set[str]]:
    """What pipeline._read_into does in zone mode with option B, on items
    already in "@<photo id>" form: (the list, the suggestions, the
    disputed names). Inputs are not changed."""
    overall = copy.deepcopy(overall)
    pipeline.side_from_sorter(overall, sorter_parts or {})
    merged, disputed = zones.merge_reads(overall, reads)
    listed, suggested = zones.split_suggestions(merged)
    evidence.finish(listed, suggested, {})
    listed = pipeline.resolve_sides(listed, areas, keep_undetermined=disputed)
    zones.apply_agreement(listed)
    return listed, suggested, disputed


def single_chain(overall: list[dict], areas=AREAS, sorter_parts: dict | None = None) -> list[dict]:
    """The same reading in single mode: side_from_sorter, then resolve_sides."""
    overall = copy.deepcopy(overall)
    pipeline.side_from_sorter(overall, sorter_parts or {})
    return pipeline.resolve_sides(overall, areas)


def names(items: list[dict]) -> list[str]:
    return [str(i.get("part")) for i in items]


# ---------------------------------------------------------------- replays


def never_loses() -> None:
    # The stored run's shape: the overall reading listed five rear parts and
    # no tail lamp; the rear zone agreed on four and saw both lamps intact.
    overall = [item("Rear Bumper", "@a"), item("Boot Lid", "@a"), item("Left Rear Quarter Panel", "@a"),
               item("Left Rear Wheel Arch Trim", None), item("Left Rear Bumper Reflector", "@l")]
    rear = [item("Rear Bumper", "@a"), item("Boot Lid", "@a"), item("Left Rear Quarter Panel", "@a"),
            item("Left Rear Bumper Reflector", "@l")]
    listed, suggested, _ = zone_chain(overall, [("zone:rear", rear), ("zone:left", [item("Left Rear Quarter Panel", "@s")])])
    check(names(listed) == names(overall) and not suggested,
          "no lamp in the overall reading -> none on the list and none suggested (the zones' intact lists go nowhere)",
          names(listed))

    # The same, with the overall reading's Left Tail Lamp: it stays.
    with_lamp = overall + [item("Left Tail Lamp", "@l", "High")]
    listed, suggested, _ = zone_chain(with_lamp, [("zone:rear", rear)])
    lamp = next((i for i in listed if "Tail Lamp" in i["part"]), None)
    check(lamp is not None and lamp["part"] == "Left Tail Lamp" and lamp["found_by"] == ["overall"]
          and lamp["ai_confidence"] == "Low",
          "the overall reading's Left Tail Lamp stays on the list, found by it alone: Low (agreement rule)", lamp)
    check(names(single_chain(with_lamp)) == names(with_lamp), "single mode: the same list")

    # Option B's invariant on random reads: every overall item is on the
    # list (in order), as it was, its "(side undetermined)" name (a
    # dispute), or a side for its undetermined name (a zone saw it).
    vocab = ["Left Tail Lamp", "Right Tail Lamp", "Tail Lamp (side undetermined)", "Left Rear Bumper Reflector",
             "Right Rear Bumper Reflector", "Rear Bumper Reflector (side undetermined)", "Rear Bumper", "Boot Lid",
             "Left Rear Quarter Panel", "Right Rear Quarter Panel", "Quarter Panel (side undetermined)"]
    photos = ["@a", "@l", "@r", "@c", None]
    rng = random.Random(10)
    broken = []
    for trial in range(400):
        def some(n):
            return [item(rng.choice(vocab), rng.choice(photos)) for _ in range(rng.randint(0, n))]

        overall = some(5)
        reads = [(f"zone:{z}", some(4)) for z in ("rear", "left", "close")[:rng.randint(1, 3)]]
        merged, _ = zones.merge_reads(overall, reads)
        listed, _ = zones.split_suggestions(merged)
        ok = len(listed) == len(overall) and all("overall" in i["found_by"] for i in listed)
        for before, after in zip(overall, listed):
            b, a = before["part"], after["part"]
            ok = ok and (a == b or a == unsided_part(b)
                         or (b.endswith(UNDETERMINED_SUFFIX) and split_side(a)[1] and unsided_part(a) == b))
        if not ok:
            broken.append((trial, names(overall), names(listed)))
    check(not broken, "400 random shapes: every overall item on the list, renamed at most by a side rule",
          broken[:2])


def intact_changes_nothing() -> None:
    overall = [item("Left Tail Lamp", "@l", "High"), item("Rear Bumper", "@a", "High")]
    zones_said = [("zone:rear", [item("Rear Bumper", "@a", "High")])]
    listed, _, _ = zone_chain(overall, zones_said)
    check(names(listed) == ["Left Tail Lamp", "Rear Bumper"],
          "merge_reads takes no intact list: a zone that saw the lamp intact can't take it off", names(listed))


def both_sides_one_photo() -> None:
    overall = [item("Left Tail Lamp", "@l", "High")]
    listed, suggested, disputed = zone_chain(overall, [("zone:rear", [item("Right Tail Lamp", "@l", "High")])])
    lamp = listed[0]
    check(names(listed) == ["Tail Lamp (side undetermined)"] and lamp["ai_confidence"] == "Low"
          and lamp["found_by"] == ["overall", "zone:rear"] and not lamp["side_inferred"] and not suggested,
          "overall Left, zone Right, one photo: one list item, side undetermined, Low", lamp)
    check(disputed == {"Tail Lamp (side undetermined)"}, "held out of the resolver (not placed from @l's area)")
    check(names(single_chain(overall)) == ["Left Tail Lamp"], "single mode keeps the overall reading's Left")

    # Two reads agreeing first outvote a third.
    listed, _, disputed = zone_chain(overall, [("zone:rear", [item("Left Tail Lamp", "@l")]),
                                               ("zone:close", [item("Right Tail Lamp", "@l")])])
    check(names(listed) == ["Left Tail Lamp"] and not disputed, "overall + rear agree on Left: close's Right is dropped")
    # A dispute first, then a read agreeing with either side: still disputed.
    listed, _, disputed = zone_chain(overall, [("zone:rear", [item("Right Tail Lamp", "@l")]),
                                               ("zone:close", [item("Left Tail Lamp", "@l")])])
    check(names(listed) == ["Tail Lamp (side undetermined)"] and listed[0]["found_by"] == ["overall", "zone:rear", "zone:close"],
          "Right then Left on the disputed photo: the third read joins the undetermined item", names(listed))
    # Different photos: both sides stay (both lamps may be broken).
    listed, suggested, _ = zone_chain(overall, [("zone:rear", [item("Right Tail Lamp", "@r")])])
    check(names(listed) == ["Left Tail Lamp"] and names(suggested) == ["Right Tail Lamp"]
          and suggested[0]["other_side_listed"] is None,
          "Right on another photo: the list keeps Left, Right is a zone suggestion", names(suggested))


def undetermined_meets_side() -> None:
    lamp = [item("Tail Lamp (side undetermined)", "@l")]
    listed, _, _ = zone_chain(lamp, [("zone:rear", [item("Left Tail Lamp", "@l")])])
    check(names(listed) == ["Left Tail Lamp"] and listed[0]["found_by"] == ["overall", "zone:rear"]
          and not listed[0]["side_inferred"], "same photo: the zone's side, credited to both reads")
    # On ANOTHER photo the zone's side is taken too (zones._merge_one:
    # `_sharing(...) or undetermined[0]`): here it is the other side from
    # the one the overall item's own photo would give it in single mode.
    listed, _, _ = zone_chain(lamp, [("zone:rear", [item("Right Tail Lamp", "@r")])])
    single = single_chain(lamp)
    check(names(listed) == ["Right Tail Lamp"] and listed[0]["seen_in"] == ["@l", "@r"]
          and names(single) == ["Left Tail Lamp"] and single[0]["side_inferred"],
          "another photo: zone mode names it Right (the zone's), single mode places it Left (its photo's area)",
          (names(listed), names(single)))


def resolver_after_credit() -> None:
    # An overall item with no photo stays undetermined in single mode; a
    # zone read finding it on a photo gives it one, and the resolver then
    # places it from that photo's area.
    lamp = [item("Tail Lamp (side undetermined)", None)]
    check(names(single_chain(lamp)) == ["Tail Lamp (side undetermined)"], "single mode: no photo, not placed")
    listed, _, _ = zone_chain(lamp, [("zone:close", [item("Tail Lamp (side undetermined)", "@r")])])
    check(names(listed) == ["Right Tail Lamp"] and listed[0]["side_inferred"] and listed[0]["ai_confidence"] == "Low",
          "zone mode: the zone's photo (rear-right) lets the resolver place it -- inferred, Low", listed[0])
    # And when the placed name is already listed, the resolver drops it.
    both = lamp + [item("Right Tail Lamp", "@a", "High")]
    listed, _, _ = zone_chain(both, [("zone:close", [item("Tail Lamp (side undetermined)", "@r")])])
    check(names(listed) == ["Right Tail Lamp"] and names(single_chain(both)) == names(both),
          "...and dropped beside the same name (single mode keeps both)", names(listed))


def suggestions_after_dispute() -> None:
    # Two zones disputing a part the overall reading didn't list: one
    # suggestion, side undetermined, never a list item.
    overall = [item("Rear Bumper", "@a")]
    listed, suggested, disputed = zone_chain(overall, [("zone:rear", [item("Left Rear Bumper Reflector", "@c")]),
                                                       ("zone:close", [item("Right Rear Bumper Reflector", "@c")])])
    check(names(listed) == ["Rear Bumper"] and names(suggested) == ["Rear Bumper Reflector (side undetermined)"]
          and suggested[0]["found_by"] == ["zone:rear", "zone:close"] and suggested[0]["source"] == "zone",
          "zone-only, disputed: one suggestion, side undetermined", names(suggested))
    check(disputed == {"Rear Bumper Reflector (side undetermined)"}, "its name is reported disputed too")
    # An overall item disputed stays on the list (found_by has "overall").
    listed, suggested, _ = zone_chain([item("Left Rear Bumper Reflector", "@c")],
                                      [("zone:rear", [item("Right Rear Bumper Reflector", "@c")])])
    check(names(listed) == ["Rear Bumper Reflector (side undetermined)"] and not suggested,
          "an overall item disputed: still on the list, undetermined")


def sorter_side_first() -> None:
    # side_from_sorter runs before the merge, in both modes: a Left read on
    # a photo the sorter tagged with the Right lamp becomes Right.
    overall = [item("Left Tail Lamp", "@r", "High")]
    tags = {"r": "Right Tail Lamp"}
    listed, _, _ = zone_chain(overall, [], sorter_parts=tags)
    check(names(listed) == ["Right Tail Lamp"] and names(single_chain(overall, sorter_parts=tags)) == ["Right Tail Lamp"],
          "the sorter's side on its own photo wins in both modes, with no trace on the item", names(listed))


# ---------------------------------------------------------------- zone_intact (contract 10, 4.1)


def jpeg(i: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (30 * i % 256, 90, (180 - 12 * i) % 256)).save(buf, "JPEG", quality=80)
    return buf.getvalue()


# P01 rear overview, P02 / P03 rear damage, P04 a left close-up.
CASE = [("overview", "rear", None, False), ("damage", "rear-left", "Left Tail Lamp", True),
        ("damage", "rear", "Rear Bumper", True), ("damage", "left", "Left Rear Door", True)]


def make_case(rid: str, ai_draft: dict | None = None) -> None:
    folder = Path(_tmp, "photos", rid)
    folder.mkdir(parents=True)
    db = SessionLocal()
    try:
        db.add(Report(id=rid, status="intake", channel="dashboard", reporter_chat_id="test",
                      data={"description": "", "category": [], "damage_summary": []}))
        db.add(IntakeSession(report_id=rid, stage="reading", reporter_notes="Hit from behind.", ai_draft=ai_draft))
        for i, (kind, area, part, damage) in enumerate(CASE):
            path = folder / f"IMG_{i:02d}.jpg"
            path.write_bytes(jpeg(i))
            db.add(IntakePhoto(id=f"{rid}-{i}", report_id=rid, position=i, original_name=path.name, path=str(path),
                               sha256=f"{rid}{i}", kind=kind, area=area, part=part, shows_damage=damage, quality="good"))
        db.commit()
    finally:
        db.close()


def saved(rid: str) -> dict:
    db = SessionLocal()
    try:
        return {"data": copy.deepcopy(db.get(Report, rid).data),
                "ai_draft": copy.deepcopy(db.get(IntakeSession, rid).ai_draft)}
    finally:
        db.close()


def draft_answer(kwargs, labels):
    return {"description": "Hit at the rear.", "category": ["Vehicle Collision or Damage"],
            "damage_summary": [{"part": "Left Tail Lamp", "photo_reference": "P02", "ai_confidence": "High"},
                               {"part": "Rear Bumper", "photo_reference": "P03", "ai_confidence": "High"}],
            "vehicle_info": {"make": "Gammamotors", "model": "Testa"}}


def zone_answer(kwargs, labels):
    if "P04" in labels:
        return {"zone_side_seen": "left", "damage": [], "checked_intact": ["Rear Door", "Wing Mirror"]}
    # The rear zone calls the lamp the overall reading found broken intact.
    return {"zone_side_seen": "rear", "checked_intact": ["Left Tail Lamp", "Rear Windscreen", "Left Tail Lamp",
                                                         "Right Tail Lamp"],
            "damage": [{"photo_reference": "P03", "part": "Rear Bumper", "ai_confidence": "High"}]}


def zone_intact() -> None:
    HANDLERS.update(draft=draft_answer, zone=zone_answer)
    REQUESTS.clear()
    with Settings(intake_read_mode="zones", intake_zone_only="suggest"):
        make_case("zm-intact")
        pipeline._read("zm-intact", AiActor.cli())
    got = saved("zm-intact")
    ai_draft, data = got["ai_draft"], got["data"]
    check(len([r for r in REQUESTS if kind_of(r) == "zone"]) == 2, "two zones read (rear, left)")
    check(ai_draft.get("zone_intact") == {"zone:rear": ["Left Tail Lamp", "Rear Windscreen", "Right Tail Lamp"],
                                          "zone:left": ["Left Rear Door", "Left Wing Mirror"]},
          "ai_draft[\"zone_intact\"]: each zone read's parts seen intact, canonical names, by read",
          ai_draft.get("zone_intact"))
    check([i["part"] for i in data["damage_summary"]] == ["Left Tail Lamp", "Rear Bumper"],
          "the list is unchanged by them: the lamp the rear zone called intact stays")
    check("zone_intact" not in data and not any("intact" in k for i in data["damage_summary"] for k in i),
          "never in Report.data, never on an item")
    check(zones.intact_of(ai_draft) == ai_draft["zone_intact"], "zones.intact_of reads it back")

    # Every zone call failing: the reading stands, and the dict is empty.
    HANDLERS["zone"] = lambda kwargs, labels: "not json"
    with Settings(intake_read_mode="zones"):
        make_case("zm-intact-failed")
        pipeline._read("zm-intact-failed", AiActor.cli())
    got = saved("zm-intact-failed")
    check(got["ai_draft"].get("zone_intact") == {} and len(got["data"]["damage_summary"]) == 2,
          "no zone answered: zone_intact {} and the overall reading's list", got["ai_draft"].get("zone_intact"))

    # Single mode: no zone read, nothing new in the AI draft.
    HANDLERS["zone"] = zone_answer
    with Settings(intake_read_mode="single"):
        make_case("zm-intact-single")
        pipeline._read("zm-intact-single", AiActor.cli())
    check("zone_intact" not in saved("zm-intact-single")["ai_draft"], "single mode: no zone_intact")

    # A draft saved before contract 10 (zone mode, no zone_intact): read
    # back as {}, and the suggestions are rebuilt from it as before.
    old = {"damage_summary": [item("Rear Bumper", "@zm-old-2", "High", found_by=["overall", "zone:rear"])],
           "zone_reads": ["overall", "zone:rear"],
           "zone_suggestions": [zones.as_suggestion({**item("Boot Lid Lock", "@zm-old-2", "Medium"),
                                                     "found_by": ["zone:rear"], "seen_in": ["@zm-old-2"]})]}
    check(zones.intact_of(old) == {} and zones.intact_of(None) == {} and zones.intact_of({"zone_intact": "x"}) == {}
          and zones.intact_of({"zone_intact": {"zone:rear": ["Left Tail Lamp", 3, None]}}) == {"zone:rear": ["Left Tail Lamp"]},
          "intact_of: {} for an old draft, nothing, or junk; strings only")
    make_case("zm-old", ai_draft=old)
    db = SessionLocal()
    try:
        report = db.get(Report, "zm-old")
        report.data = {**report.data, "damage_summary": copy.deepcopy(old["damage_summary"])}
        db.commit()
    finally:
        db.close()
    second_look.run_for_intake("zm-old", AiActor.cli())
    look = saved("zm-old")["data"].get("second_look") or {}
    check([it["part"] for it in look.get("items") or []] == ["Boot Lid Lock"],
          "an old zone-mode draft without zone_intact: the second look's store still rebuilds its suggestions",
          look.get("items"))


def main() -> int:
    init_db()
    for name, fn in (
        ("option B never loses an overall item", never_loses),
        ("a zone's intact list changes nothing on the list", intact_changes_nothing),
        ("the same part, both sides, one photo", both_sides_one_photo),
        ("an undetermined overall item meets a zone's side", undetermined_meets_side),
        ("the side resolver after a zone gave an item its photo", resolver_after_credit),
        ("split_suggestions after a dispute", suggestions_after_dispute),
        ("side_from_sorter, before the merge", sorter_side_first),
        ("ai_draft[\"zone_intact\"] (contract 10)", zone_intact),
    ):
        run_section(name, fn)
    print(f"\ntest_zone_merge: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAILED [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
