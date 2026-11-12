"""Reading the car one area at a time (contract 8 D2): intake/zones.py,
ai/zone_read.py, ai/grid.py and the shared fields seen_in / found_by /
grid_cells -- offline, Gemini is a fake.

Covers: the draft schema Gemini sees unchanged; old items without the
fields still load; single mode (the default) sending the same request and
saving the same data as master did (golden hashes taken on fa6e921);
zoning on synthetic photo lists (corners, tyres, close-ups, kinds no zone
reads, a part that moves its photo) and the caps (12 photos a zone, damage
first, 6 zone calls, 7 calls a case); the zone call's prompt, schema and
answer (a side zone named from the side seen, the other end dropped); the
merge rules (both reads, zone only, sides undetermined and disputed); the
agreement rule; a zone call that is busy, out of quota, times out or
answers bad JSON leaving the overall reading as it was; grid marks drawn on
a derived copy, made once; the run log's zone row; filing turning seen_in
and the zone row's photos into P-numbers; the benchmark's flags and stage.

    python scripts/test_detect_zones.py

Makes, parts and plates are made up; the photos are coloured squares.
"""
import copy
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="detect-zones-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake"
# The reading's own calls are counted exactly; the second look is its own test.
os.environ["SECOND_LOOK_ENABLED"] = "false"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"

import app.ai.structured as structured  # noqa: E402

# Every create() the fake was asked, in order (sorting, drafting, zones).
REQUESTS: list[dict] = []
_requests_lock = threading.Lock()


def _labels(parts: list[dict]) -> list[str]:
    return [m.group(1) for part in parts if part.get("type") == "text"
            for m in [re.match(r"Photo (P\d+):$", part["text"])] if m]


def golden_answer() -> dict:
    """The drafting answer the golden single-mode hashes were taken with."""
    return {
        "description": "Rear-ended.", "category": ["Vehicle Collision or Damage"],
        "damaged_parts": ["Rear Bumper"],
        "damage_summary": [
            {"part": "Rear Bumper", "photo_reference": "P03", "ai_confidence": "High", "condition_tokens": ["Cracked"]},
            {"part": "Tail Lamp (side undetermined)", "photo_reference": "P04", "ai_confidence": "Medium"},
            {"part": "Left Rear Door", "photo_reference": "P07", "ai_confidence": "High"},
            {"part": "Boot Lid", "photo_reference": "P05"},
        ],
        "vehicle_info": {"make": "Gammamotors", "model": "Vezel"},
    }


def empty_zone(kwargs, labels):
    return {"zone_side_seen": "unclear", "damage": [], "checked_intact": []}


# Each kind of call's answer: a function of (create kwargs, photo labels)
# returning a dict (sent as JSON) or a str (sent as it is), or raising.
HANDLERS = {"draft": lambda kwargs, labels: golden_answer(), "zone": empty_zone, "sort": None}


class FakeInteractions:
    def create(self, **kwargs):
        with _requests_lock:
            REQUESTS.append(kwargs)
        labels = _labels(kwargs["input"])
        assert len(labels) == len([p for p in kwargs["input"] if p.get("type") == "image"]), labels
        props = kwargs["response_format"]["schema"].get("properties", {})
        kind = "sort" if "photos" in props else "zone" if "zone_side_seen" in props else "draft"
        out = HANDLERS[kind](kwargs, labels)
        return SimpleNamespace(output_text=out if isinstance(out, str) else json.dumps(out),
                               usage=SimpleNamespace(total_input_tokens=1000, total_output_tokens=100))


structured.get_client = lambda key=None: SimpleNamespace(interactions=FakeInteractions())
structured.acquire = lambda key=None: 0.0
# The busy retries (a model, then the whole chain) without real waiting.
structured.OVERLOADED_RETRY_SECONDS = 0.0
structured.CHAIN_BUSY_RETRY_SECONDS = 0.0

from PIL import Image  # noqa: E402

from app.ai import allowance, grid, runs, zone_read  # noqa: E402
from app.ai.extraction import _response_schema  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import pipeline, zones  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import AiRun, IntakePhoto, IntakeSession, Report  # noqa: E402
from app.reports.schema import DamageSummaryItem, SecurityIncidentDraft  # noqa: E402

LITE = "gemini-3.5-flash-lite"
# Taken on master (fa6e921) with the same synthetic case, answer and
# settings: json.dumps of the draft schema; the drafting request; the saved
# form, AI draft and read photos with the three new item fields taken out.
GOLDEN_SCHEMA = "7ccb5ce425dc1fe68971672f41992753e1431b535edf941c45aa648e3d6d18d0"
GOLDEN_REQUEST = "b5df170069a5cbd04fcc231ceea2c957f18e125aa447054c85bd4526593b1b24"
GOLDEN_SAVED = "0a6566dffaa451e5f15ec1141782b6b931244fef95e2592664b249bb18588e0d"
NEW_FIELDS = ("seen_in", "found_by", "grid_cells")

# (kind, area, part, shows damage) per photo, P01 first: the golden case.
GOLDEN_CASE = [
    ("log_card", None, None, False), ("overview", "rear", None, False), ("damage", "rear", "Rear Bumper", True),
    ("damage", "rear-left", "Tail Lamp (side undetermined)", True), ("damage", "rear", "Boot Lid", True),
    ("overview", "left", None, False), ("damage", "left", "Left Rear Door", True), ("damage", None, None, True),
    ("tyre", "left", None, False), ("interior", "interior", None, False),
]

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


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def jpeg(i: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (20 * i % 256, 100, (200 - 10 * i) % 256)).save(buf, "JPEG", quality=80)
    return buf.getvalue()


def make_case(rid: str, case: list[tuple], notes: str = "Hit from behind.") -> list[Path]:
    """A draft straight in the database, photos sorted as `case` says."""
    folder = Path(_tmp, "photos", rid)
    folder.mkdir(parents=True)
    paths = []
    db = SessionLocal()
    try:
        db.add(Report(id=rid, status="intake", channel="dashboard", reporter_chat_id="test",
                      data={"description": "", "category": [], "damage_summary": []}))
        db.add(IntakeSession(report_id=rid, stage="reading", reporter_notes=notes))
        for i, (kind, area, part, damage) in enumerate(case):
            path = folder / f"IMG_{i:02d}.jpg"
            path.write_bytes(jpeg(i))
            paths.append(path)
            db.add(IntakePhoto(id=f"{rid}-{i}", report_id=rid, position=i, original_name=path.name, path=str(path),
                               sha256=f"{rid}{i}", kind=kind, area=area, part=part, shows_damage=damage, quality="good"))
        db.commit()
    finally:
        db.close()
    return paths


def saved(rid: str) -> dict:
    db = SessionLocal()
    try:
        session = db.get(IntakeSession, rid)
        return {"data": copy.deepcopy(db.get(Report, rid).data), "ai_draft": copy.deepcopy(session.ai_draft),
                "read": session.read_photo_ids, "progress": (session.progress_done, session.progress_total)}
    finally:
        db.close()


def strip_new(data: dict | None) -> dict | None:
    for item in (data or {}).get("damage_summary") or []:
        for key in NEW_FIELDS:
            item.pop(key, None)
    return data


def run_rows(rid: str) -> list[AiRun]:
    db = SessionLocal()
    try:
        return db.query(AiRun).filter(AiRun.report_id == rid).order_by(AiRun.started_at).all()
    finally:
        db.close()


def zone_requests() -> list[dict]:
    return [r for r in REQUESTS if "zone_side_seen" in r["response_format"]["schema"].get("properties", {})]


def draft_requests() -> list[dict]:
    return [r for r in REQUESTS if r["system_instruction"].startswith("You are an expert")]


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


def photo(i: int, kind, area=None, part=None, damage=False, **kw):
    return IntakePhoto(id=f"p{i}", report_id="r", position=i, path="", sha256="", kind=kind, area=area, part=part,
                       quality=kw.pop("quality", "good"), shows_damage=damage, excluded=kw.pop("excluded", False), **kw)


# ---------------------------------------------------------------- schema


def schema_unchanged() -> None:
    check(hashlib.sha256(json.dumps(_response_schema()).encode()).hexdigest() == GOLDEN_SCHEMA,
          "the draft schema Gemini sees is byte for byte master's")
    props = DamageSummaryItem.model_json_schema()["properties"]
    check(not set(NEW_FIELDS) & set(props), "seen_in / found_by / grid_cells are not in DamageSummaryItem's schema")
    old = DamageSummaryItem.model_validate({"part": "Rear Bumper", "photo_reference": "P03"})
    check(old.seen_in == [] and old.found_by == [] and old.grid_cells == [], "an item saved before them loads, empty")
    draft = SecurityIncidentDraft.model_validate({"description": "x", "damage_summary": [
        {"part": "Boot Lid", "seen_in": ["@a", "@b"], "found_by": ["overall", "zone:rear"], "grid_cells": ["B2"]}]})
    item = draft.model_dump()["damage_summary"][0]
    check(item["seen_in"] == ["@a", "@b"] and item["found_by"] == ["overall", "zone:rear"] and item["grid_cells"] == ["B2"],
          "the wizard's autosave (validated through the draft model) keeps them")


# ---------------------------------------------------------------- single mode


def single_mode_identical() -> None:
    REQUESTS.clear()
    with Settings(gemini_model_chain=LITE, intake_read_mode="single"):
        make_case("zt-single", GOLDEN_CASE)
        pipeline._read("zt-single", AiActor.cli())
    check(len(REQUESTS) == 1, "one call, the overall reading", f"{len(REQUESTS)} calls")
    check(digest(dict(REQUESTS[0])) == GOLDEN_REQUEST, "the request is byte for byte master's (golden hash)")
    got = saved("zt-single")
    items = got["data"]["damage_summary"] + got["ai_draft"]["damage_summary"]
    check(all(it.get(k) == [] for it in items for k in NEW_FIELDS), "the new fields are there, empty")
    check(digest({"data": strip_new(got["data"]), "ai_draft": strip_new(got["ai_draft"]), "read": got["read"]})
          == GOLDEN_SAVED, "the saved form, AI draft and read photos are master's (golden hash)")
    check([r.kind for r in run_rows("zt-single")] == ["read"], "no zone row in the run log")


# ---------------------------------------------------------------- zoning


def zoning() -> None:
    z = zones.photo_zones
    check(z(photo(1, "damage", "rear", "Rear Bumper", True)) == ["rear"], "a rear close-up: rear")
    check(z(photo(2, "damage", "rear-left", "Tail Lamp (side undetermined)", True)) == ["rear"],
          "a corner close-up: its end, where both sides' lamps are on the checklist")
    check(z(photo(3, "damage", "rear-left", None, True)) == ["rear"], "a corner close-up naming no part: its end")
    check(z(photo(4, "damage", "rear-left", "Left Rear Door", True)) == ["left"],
          "a corner close-up of a part only a side has (a door): the side")
    check(z(photo(5, "overview", "front-right")) == ["front", "right"], "a corner wide shot: both its end and its side")
    check(z(photo(6, "tyre", "front-left")) == ["left"] and z(photo(7, "tyre", "right")) == ["right"],
          "tyres: their side, where wheels and tyres are")
    check(z(photo(8, "tyre", "rear")) == [] and z(photo(9, "tyre", None, None, True)) == ["close"],
          "a tyre with no side: not read unless it shows damage, then as a close-up")
    check(z(photo(10, "tyre", "rear", "Left Rear Tyre", True)) == ["left"], "a tyre with no side but a sided part: its side")
    check(z(photo(11, "damage", None, None, True)) == ["close"], "a damage close-up with no area: close")
    check(z(photo(12, "damage", "underbody", "Door (side undetermined)", True)) == ["close"],
          "a part that names no side or end: close")
    check(z(photo(13, "damage", "left", "Rear Bumper", True)) == ["rear"],
          "the sorter's part, not on its area's checklist, moves the photo to that part's zone")
    check(z(photo(14, "damage", "interior", "Boot Floor Board", True)) == ["rear"], "a boot part seen 'inside': rear")
    for kind in ("interior", "log_card", "vin_plate", "police_report", "document", "odometer", "scene", "third_party"):
        if not check(z(photo(20, kind, "rear", "Rear Bumper", True)) == [], f"{kind}: no zone"):
            break
    check(z(photo(21, "other", "rear")) == [] and z(photo(22, "other", "rear", None, True)) == ["rear"],
          "an 'other' photo only when it shows damage")
    check(z(photo(23, "damage", "rear", "Rear Bumper", True, excluded=True)) == []
          and z(photo(24, "damage", "rear", "Rear Bumper", True, quality="unusable")) == [],
          "excluded and unusable photos: none")
    check(z(photo(25, None, "rear")) == [], "an unsorted photo: none")
    check(z(photo(26, "overview", None)) == [], "a wide shot with no area: none")


def caps() -> None:
    case = [photo(0, "log_card")]
    case += [photo(1 + i, "overview", "rear") for i in range(4)]
    parts = ["Rear Bumper"] * 8 + ["Boot Lid"] * 6 + ["Left Tail Lamp"] * 2 + ["Boot Lid Lock"]
    case += [photo(5 + i, "damage", "rear", part, True) for i, part in enumerate(parts)]
    case += [photo(22 + i, "overview", "left") for i in range(3)]  # a side with no damage photo
    case += [photo(25, "damage", "front", "Front Bumper", True), photo(26, "overview", "front")]
    plans = zones.plan_zones(case)
    check([p.zone for p in plans] == ["rear", "front"], "only zones with a photo showing damage; most damage first",
          [p.zone for p in plans])
    rear = plans[0]
    check(len(rear.photos) == zones.MAX_ZONE_PHOTOS == 12 and rear.damage_photos == 17,
          "a zone sends at most 12 photos", f"{len(rear.photos)} of {rear.damage_photos}")
    check(all(p.shows_damage for p in rear.photos), "damage photos first: 17 of them fill the 12")
    check({p.part for p in rear.photos} == set(parts), "every damaged part gets a photo before any gets a second")
    check([p.position for p in rear.photos] == sorted(p.position for p in rear.photos), "sent in camera order")
    front = plans[1]
    check([p.position for p in front.photos] == [25, 26], "then the overviews")
    every = [photo(0, "damage", "front", None, True), photo(1, "damage", "rear", None, True),
             photo(2, "damage", "left", None, True), photo(3, "damage", "right", None, True),
             photo(4, "damage", None, None, True)]
    plans = zones.plan_zones(every)
    check(len(plans) == 5 <= zones.MAX_ZONE_CALLS and 1 + len(plans) <= zones.MAX_READ_CALLS == 7,
          "five zones at most: five zone calls, six calls a case with the overall reading")
    old = zones.MAX_ZONE_CALLS
    zones.MAX_ZONE_CALLS = 2
    try:
        check(len(zones.plan_zones(every)) == 2, "the zone-call cap holds")
    finally:
        zones.MAX_ZONE_CALLS = old


# ---------------------------------------------------------------- the zone call


def zone_call_shape() -> None:
    labels = ["P03", "P07"]
    schema = zone_read.response_schema("left", labels)
    item = schema["properties"]["damage"]["items"]["properties"]
    check(list(schema["properties"]) == ["zone_side_seen", "damage", "checked_intact"]
          and schema["properties"]["zone_side_seen"]["enum"] == ["left", "right", "unclear"],
          "zone_side_seen first, then damage and checked_intact")
    check(set(item) <= set(DamageSummaryItem.model_fields) and "grid_cells" not in item,
          "the item fields are DamageSummaryItem's (no grid without grid marks)")
    check(item["photo_reference"]["enum"] == labels, "photo_reference: only the photos sent")
    check(item["part"]["enum"][-1] == "Other / Not Listed" and "Front Door" in item["part"]["enum"]
          and not any(p.startswith(("Left ", "Right ")) for p in item["part"]["enum"]),
          "a side zone offers its parts without a side")
    rear = zone_read.checklist("rear")
    check("Left Tail Lamp" in rear and "Right Tail Lamp" in rear and "Front Bumper" not in rear,
          "an end zone offers both sides' parts, and only its end's")
    close = zone_read.checklist("close")
    check("Tail Lamp (side undetermined)" in close and not any(p.startswith(("Left ", "Right ")) for p in close),
          "close-ups: every outside part, without a side")
    gschema = zone_read.response_schema("rear", labels, grid=True)
    gitem = gschema["properties"]["damage"]["items"]
    check(list(gitem["properties"])[:3] == ["photo_reference", "grid_cells", "part"] and "grid_cells" in gitem["required"]
          and gitem["properties"]["grid_cells"]["items"]["enum"] == grid.CELLS,
          "grid marks: grid_cells (A1..C3) after the photo and before the part")
    prompt, gprompt = zone_read.system_prompt("rear"), zone_read.system_prompt("rear", grid=True)
    check(len(prompt) < 2500 and "A1 to C3" not in prompt and gprompt.count("A1 to C3") == 1,
          "a short prompt; grid marks add one sentence", f"{len(prompt)} characters")

    def parse(zone, answer, grid_on=False):
        return zone_read.parse_answer(zone, json.dumps(answer), labels, grid_on)

    a = parse("left", {"zone_side_seen": "right", "damage": [
        {"photo_reference": "P07", "part": "Rear Door", "ai_confidence": "High", "condition_tokens": ["Dented"]},
        {"photo_reference": "P99", "part": "Wing Mirror", "ai_confidence": "Low"},
        {"photo_reference": "P03", "part": "Bonnet", "ai_confidence": "High"},
        {"photo_reference": "P03", "part": "Other / Not Listed", "ai_confidence": "High"},
        {"photo_reference": "P3", "part": "Other / Not Listed", "other_part_name": "door moulding", "ai_confidence": "Low"},
    ], "checked_intact": ["Front Door", "Bonnet"]})
    check(a.side_seen == "right" and [i["part"] for i in a.items] == ["Right Rear Door", "Right Wing Mirror", "Other / Not Listed"],
          "a side zone's parts take the side seen (the sorter said left); a part off the checklist and a "
          "nameless Other are dropped", [i["part"] for i in a.items])
    check(a.items[0]["condition"] == "Dented" and a.items[0]["damage_type"] == "Dent" and a.items[1]["photo_reference"] is None
          and a.items[2]["photo_reference"] == "P03" and a.items[2]["other_part_name"] == "Door moulding",
          "the overall reading's clean-up; a photo not sent is no photo; P3 is P03")
    check(a.intact == ["Right Front Door"], "checked_intact in canonical names")
    b = parse("left", {"zone_side_seen": "unclear", "damage": [{"photo_reference": "P07", "part": "Rear Door"}]})
    check([i["part"] for i in b.items] == ["Door (side undetermined)"], "side unclear: the undetermined name")
    c = parse("front", {"zone_side_seen": "rear", "damage": [{"photo_reference": "P07", "part": "Front Bumper"}]})
    check(c.items == [] and c.note and "rear" in c.note, "an end zone whose photos show the other end: finds dropped")
    d = parse("rear", {"zone_side_seen": "rear", "damage": [
        {"photo_reference": "P07", "grid_cells": ["b2", "B2", "D9", "C3"], "part": "Left Tail Lamp", "ai_confidence": "Medium"},
        {"grid_cells": ["A1"], "part": "Boot Lid", "ai_confidence": "Medium"}]}, grid_on=True)
    check(d.items[0]["grid_cells"] == ["B2", "C3"] and d.items[1]["grid_cells"] == [],
          "grid cells kept when valid, and only with a photo")
    for bad in ("not json {", "[1, 2]"):
        try:
            zone_read.parse_answer("rear", bad, labels)
            check(False, f"bad answer {bad!r} raises")
        except ValueError:
            check(True, f"bad answer {bad!r} raises ValueError (the chain moves on)")


# ---------------------------------------------------------------- merging


def item(part, ref=None, conf=None, **kw) -> dict:
    return {**DamageSummaryItem(part=part, photo_reference=ref, ai_confidence=conf).model_dump(), **kw}


def merging() -> None:
    overall = [item("Rear Bumper", "@a", "High", damage_type="Crack"), item("Boot Lid", None, "Medium"),
               item("Tail Lamp (side undetermined)", "@c", "Medium"), item("Left Rear Door", "@d", "High"),
               item("Left Front Fender", "@e", "High")]
    before = copy.deepcopy(overall)
    rear = [item("Rear Bumper", "@a", "Low", damage_type="Scuff / Graze", grid_cells=["B2"]),
            item("Boot Lid", "@b", "High", grid_cells=["A1"]),
            item("Right Tail Lamp", "@c", "High"), item("Boot Lid Lock", "@b", "Medium")]
    side = [item("Right Rear Door", "@d", "High"), item("Right Front Fender", "@f", "High"),
            item("Boot Lid Lock", "@g", "Low")]
    close = [item("Door (side undetermined)", "@x", "Low"), item("Wheel (side undetermined)", "@w", "Low")]
    out, disputed = zones.merge_reads(overall, [("zone:rear", rear), ("zone:right", side), ("zone:close", close)])
    by = {i["part"]: i for i in out}
    check(overall == before and rear[0]["grid_cells"] == ["B2"], "pure: the inputs are not changed")
    bumper = by["Rear Bumper"]
    check(bumper["found_by"] == ["overall", "zone:rear"] and bumper["seen_in"] == ["@a"]
          and bumper["damage_type"] == "Crack" and bumper["ai_confidence"] == "High" and bumper["grid_cells"] == ["B2"],
          "both found it: the overall item's details, both reads, the zone's cells for the same photo")
    lid = by["Boot Lid"]
    check(lid["photo_reference"] == "@b" and lid["seen_in"] == ["@b"] and lid["grid_cells"] == ["A1"],
          "an overall item with no photo takes the zone's")
    check("Tail Lamp (side undetermined)" not in by and by["Right Tail Lamp"]["found_by"] == ["overall", "zone:rear"]
          and by["Right Tail Lamp"]["ai_confidence"] == "Medium",
          "undetermined in one read, sided in the other: the side the zone saw, credited to both")
    door = by.get("Door (side undetermined)")
    check(door is not None and "Left Rear Door" not in by and "Right Rear Door" not in by
          and door["ai_confidence"] == "Low" and door["found_by"][:2] == ["overall", "zone:right"],
          "opposite sides on the same photo: one item, side undetermined, Low", door)
    check(disputed == {"Door (side undetermined)"}, "and its name is held out of the resolver", disputed)
    check("Left Front Fender" in by and "Right Front Fender" in by,
          "opposite sides on different photos: both kept (both sides may be damaged)")
    lock = by["Boot Lid Lock"]
    check(lock["found_by"] == ["zone:rear", "zone:right"] and lock["seen_in"] == ["@b", "@g"]
          and lock["ai_confidence"] == "Medium", "a zone-only part, found by two zones: one item, the first one's details")
    wheel = by["Wheel (side undetermined)"]
    check(wheel["found_by"] == ["zone:close"] and wheel["seen_in"] == ["@w"], "a zone-only part is added as read")
    check(door["found_by"] == ["overall", "zone:right", "zone:close"] and door["seen_in"] == ["@d", "@x"],
          "an undetermined find meeting a disputed item of its name is the same part")

    agreed = [item("Left Tail Lamp", "@c", "High")]
    out, disputed = zones.merge_reads(agreed, [("zone:rear", [item("Left Tail Lamp", "@c", "High")]),
                                               ("zone:right", [item("Right Tail Lamp", "@c", "High")])])
    check([i["part"] for i in out] == ["Left Tail Lamp"] and not disputed,
          "two reads that agreed on a side outvote a third on the same photo")
    two_sided = [item("Left Tail Lamp", "@c", "High"), item("Right Tail Lamp", "@k", "High")]
    out, _ = zones.merge_reads(two_sided, [("zone:close", [item("Tail Lamp (side undetermined)", "@q", "Low")])])
    check([i["part"] for i in out] == ["Left Tail Lamp", "Right Tail Lamp"]
          and out[0]["found_by"] == ["overall"] and out[1]["found_by"] == ["overall"],
          "an undetermined find beside both sides, on neither's photo: covered, credited to neither")
    others = [item("Other / Not Listed", "@a", "High", other_part_name="Rear air vent")]
    out, _ = zones.merge_reads(others, [("zone:rear", [
        item("Other / Not Listed", "@a", "Low", other_part_name="rear AIR vent"),
        item("Other / Not Listed", "@a", "Low", other_part_name="Tow hook cover")])])
    check(len(out) == 2 and out[0]["found_by"] == ["overall", "zone:rear"],
          "unlisted parts match by their name: the same name merges, another is another part")
    out, _ = zones.merge_reads([item("Left Rear Door", "@d", "High")], [("zone:rear", [item("Boot Lid", "@p", "High")]),
                                                                       ("zone:rear", [item("Boot Lid", "@p2", "High")])])
    check(out[1]["found_by"] == ["zone:rear"] and out[1]["seen_in"] == ["@p", "@p2"],
          "the same part twice from one read: one read, two photos")


def agreement() -> None:
    items = [
        {"part": "A", "ai_confidence": "High", "found_by": ["overall"], "seen_in": ["@a"]},
        {"part": "B", "ai_confidence": "High", "found_by": ["overall", "zone:rear"], "seen_in": ["@b"]},
        {"part": "C", "ai_confidence": "Medium", "found_by": ["zone:rear"], "seen_in": ["@c", "@d"]},
        {"part": "D", "ai_confidence": "High", "found_by": ["zone:front"], "seen_in": []},
        {"part": "E", "ai_confidence": "High", "found_by": ["overall"], "seen_in": ["@e"], "human_verified": True},
        {"part": "F", "ai_confidence": None, "found_by": ["zone:left", "zone:left"], "seen_in": ["@f", "@f"]},
    ]
    zones.apply_agreement(items)
    check([i["ai_confidence"] for i in items] == ["Low", "High", "Medium", "Low", "High", "Low"],
          "one read and one photo (or none): Low, the Review step's Check; two reads or two photos keep theirs; "
          "a part a person checked is left alone", [i["ai_confidence"] for i in items])


# ---------------------------------------------------------------- the pipeline in zone mode

# P01..P14: the zones are rear (P02 P03 P04 P05), left (P02 P06 P07 P08 P09),
# front (P11) and close (P10).
ZONE_CASE = [
    ("log_card", None, None, False), ("overview", "rear-left", None, False), ("damage", "rear", "Rear Bumper", True),
    ("damage", "rear-left", "Tail Lamp (side undetermined)", True), ("damage", "rear", "Boot Lid", True),
    ("damage", "rear-left", "Left Rear Door", True), ("overview", "left", None, False),
    ("damage", "left", "Left Rear Quarter Panel", True), ("tyre", "left", None, False), ("damage", None, None, True),
    ("damage", "front", "Front Bumper", True), ("third_party", None, None, False), ("interior", "interior", None, False),
    ("scene", None, None, False),
]
ZONE_OF = {"P03": "rear", "P06": "left", "P11": "front", "P10": "close"}


def zone_draft(kwargs, labels) -> dict:
    return {
        "description": "Hit at the rear.", "category": ["Vehicle Collision or Damage"],
        "damage_summary": [
            {"part": "Rear Bumper", "photo_reference": "P03", "ai_confidence": "High", "condition_tokens": ["Cracked"]},
            {"part": "Tail Lamp (side undetermined)", "photo_reference": "P04", "ai_confidence": "Medium"},
            {"part": "Left Rear Door", "photo_reference": "P06", "ai_confidence": "High"},
            {"part": "Boot Lid", "photo_reference": "P05"},
        ],
        "vehicle_info": {"make": "Gammamotors", "model": "Vezel", "plate_number": "ZZT9999Q"},
    }


ZONE_ANSWERS = {
    "rear": {"zone_side_seen": "rear", "damage": [
        {"photo_reference": "P03", "part": "Rear Bumper", "ai_confidence": "High", "condition_tokens": ["Grazed"]},
        {"photo_reference": "P04", "part": "Right Tail Lamp", "ai_confidence": "High"},
        {"photo_reference": "P05", "part": "Boot Lid Lock", "ai_confidence": "Medium", "condition_tokens": ["Bent"]},
    ], "checked_intact": ["Rear Windscreen", "Left Tail Lamp"]},
    # The sorter called these photos left; the model sees the right.
    "left": {"zone_side_seen": "right", "damage": [
        {"photo_reference": "P06", "part": "Rear Door", "ai_confidence": "High"},
        {"photo_reference": "P08", "part": "Rear Quarter Panel", "ai_confidence": "Medium"},
    ], "checked_intact": ["Wing Mirror"]},
    "front": {"zone_side_seen": "front", "damage": [
        {"photo_reference": "P11", "part": "Front Bumper", "ai_confidence": "Medium"},
    ], "checked_intact": []},
    "close": {"zone_side_seen": "unclear", "damage": [
        {"photo_reference": "P10", "part": "Wheel (side undetermined)", "ai_confidence": "Low"},
    ], "checked_intact": []},
}


def which_zone(labels: list[str]) -> str:
    return next(z for label, z in ZONE_OF.items() if label in labels)


def answer_zones(kwargs, labels):
    return ZONE_ANSWERS[which_zone(labels)]


def zone_mode_pipeline() -> None:
    REQUESTS.clear()
    HANDLERS.update(draft=zone_draft, zone=answer_zones)
    with Settings(gemini_model_chain=LITE, intake_read_mode="zones"):
        make_case("zt-zones", ZONE_CASE)
        pipeline._read("zt-zones", AiActor.cli())
    drafts, zcalls = draft_requests(), zone_requests()
    check(len(drafts) == 1 and len(zcalls) == 4 and len(REQUESTS) == 5,
          "calls per case: the overall reading and one per zone with damage (4) -- 5, at most 7",
          f"{len(drafts)} + {len(zcalls)}")
    sent = {which_zone(_labels(r["input"])): _labels(r["input"]) for r in zcalls}
    check(sent == {"rear": ["P02", "P03", "P04", "P05"], "left": ["P02", "P06", "P07", "P08", "P09"],
                   "front": ["P11"], "close": ["P10"]},
          "each zone's photos: the corner wide shot in both its zones, the tyre with its side, nothing of the "
          "paperwork, cabin, scene or other party", sent)
    check(all(r["model"] == LITE and r["generation_config"] == {"thinking_level": "high"} for r in zcalls)
          and all(r["timeout"] == zone_read.ZONE_TIMEOUT for r in zcalls),
          "the reading's chain and thinking level (structured.request_settings by purpose)")
    check(all(r["input"][0]["text"].endswith("photos follow.") for r in zcalls)
          and all(len(r["system_instruction"]) < 2500 for r in zcalls), "a short prompt")
    got = saved("zt-zones")
    items = got["data"]["damage_summary"]
    by = {i["part"]: i for i in items}
    ids = {f"P{i + 1:02d}": f"@zt-zones-{i}" for i in range(len(ZONE_CASE))}
    check(by["Rear Bumper"]["found_by"] == ["overall", "zone:rear"] and by["Rear Bumper"]["seen_in"] == [ids["P03"]]
          and by["Rear Bumper"]["condition"] == "Cracked" and by["Rear Bumper"]["ai_confidence"] == "High",
          "found by both: the overall item's details, both reads, its photo as '@<photo id>'")
    check("Right Tail Lamp" in by and by["Right Tail Lamp"]["found_by"] == ["overall", "zone:rear"]
          and not by["Right Tail Lamp"]["side_inferred"],
          "the zone's side on the overall reading's undetermined lamp (not the sorter's rear-left)")
    door = by.get("Door (side undetermined)")
    check(door is not None and door["ai_confidence"] == "Low" and "Left Rear Door" not in by
          and "Right Rear Door" not in by and not door["side_inferred"],
          "the sides disputed on one photo: side undetermined, Low, not placed by the resolver", door)
    check(by["Right Rear Quarter Panel"]["found_by"] == ["zone:left"]
          and by["Right Rear Quarter Panel"]["seen_in"] == [ids["P08"]], "the side the zone saw, not the sorter's")
    check(by["Boot Lid"]["ai_confidence"] == "Low" and by["Boot Lid Lock"]["ai_confidence"] == "Low"
          and by["Front Bumper"]["ai_confidence"] == "Low",
          "the agreement rule: one read on one photo is Low (Check)")
    check(got["data"]["damaged_parts"] == [i["part"] for i in items], "damaged_parts follows the merged list")
    check(got["ai_draft"]["damage_summary"] == items, "the AI draft keeps the merged list too")
    check(got["progress"] == (5, 5), "progress over 1 + 4 zone calls", got["progress"])
    rows = run_rows("zt-zones")
    check([r.kind for r in rows] == ["read", runs.ZONES_KIND] and all(r.status == "ok" for r in rows),
          "the run log: the reading, and one row for the zone calls", [(r.kind, r.status) for r in rows])
    read_row = next(r for r in rows if r.kind == "read")
    zrow = next(r for r in rows if r.kind == runs.ZONES_KIND)
    check(isinstance(read_row.photos_sent, list) and read_row.prompt_tokens == 1000
          and len(got["read"]) == len(read_row.photos_sent) == len(_labels(drafts[0]["input"])),
          "the reading's row and read_photo_ids are the overall call's alone (its tokens, its photos)")
    detail = zrow.photos_sent
    by_zone = {z["zone"]: z for z in detail["zones"]}
    check(detail["count"] == 11 and zrow.prompt_tokens == 4000 and zrow.parts_found == 7,
          "the zone row: photos sent, its 4 calls' tokens, parts found", (detail["count"], zrow.prompt_tokens, zrow.parts_found))
    check(by_zone["rear"]["intact"] == ["Rear Windscreen", "Left Tail Lamp"] and by_zone["left"]["side_seen"] == "right"
          and by_zone["left"]["photos"][1] == ids["P06"] and all(z["status"] == "ok" for z in detail["zones"]),
          "per zone: photos ('@<photo id>'), the side seen, found, and the parts seen intact (only here)")
    check(not any("intact" in key for i in items for key in i), "checked_intact is in the run log only")


def zone_mode_keeps_overall_request() -> None:
    REQUESTS.clear()
    HANDLERS.update(draft=lambda kwargs, labels: golden_answer(), zone=empty_zone)
    with Settings(gemini_model_chain=LITE, intake_read_mode="zones"):
        make_case("zt-zones-golden", GOLDEN_CASE)
        pipeline._read("zt-zones-golden", AiActor.cli())
    check(digest(dict(draft_requests()[0])) == GOLDEN_REQUEST,
          "zone mode: the overall reading's request is still master's, byte for byte")
    check(len(zone_requests()) == 3, "and the zone calls come after it (rear, left, close)", len(zone_requests()))


# ---------------------------------------------------------------- failures


def failures() -> None:
    from google.genai import errors as genai_errors

    def day_used(*_):
        raise genai_errors.ClientError(429, {"error": {
            "code": 429, "status": "RESOURCE_EXHAUSTED",
            "message": "Quota exceeded for metric: generate_content_free_tier_requests, limit: 20, model: x",
            "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
                {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})

    def busy(*_):
        raise RuntimeError("503 service_unavailable: high demand")

    def timeout(*_):
        raise TimeoutError("no answer in time")

    def bad_json(*_):
        return "not json {"

    def scenario(rid: str, zone_handler, expect: dict, label: str, *, zone_status: str) -> None:
        REQUESTS.clear()
        HANDLERS.update(draft=zone_draft, zone=zone_handler)
        old_workers = zones.ZONE_WORKERS
        zones.ZONE_WORKERS = 1  # one at a time: which zones are skipped is then fixed
        try:
            with Settings(gemini_model_chain=LITE, intake_read_mode="zones"):
                make_case(rid, ZONE_CASE)
                pipeline._read(rid, AiActor.cli())
        finally:
            zones.ZONE_WORKERS = old_workers
            allowance.clear()
        got = saved(rid)
        parts = [i["part"] for i in got["data"]["damage_summary"]]
        rows = {r.kind: r for r in run_rows(rid)}
        statuses = {z["zone"]: (z["status"], z.get("error_code") or z.get("note"))
                    for z in rows[runs.ZONES_KIND].photos_sent["zones"]}
        check(rows["read"].status == "ok" and got["data"]["description"] == "Hit at the rear."
              and {"Rear Bumper", "Boot Lid"} <= set(parts), f"{label}: the reading is kept", parts)
        check(statuses == expect, f"{label}: each zone noted in the run log", statuses)
        check(rows[runs.ZONES_KIND].status == zone_status, f"{label}: the zone row is {zone_status}")

    def mixed(kwargs, labels):
        return {"rear": lambda: ZONE_ANSWERS["rear"], "left": busy, "front": bad_json, "close": timeout}[which_zone(labels)]()

    scenario("zt-fail-mixed", mixed, {"rear": ("ok", None), "left": ("failed", "ai_error"),
                                      "front": ("failed", "bad_answer"), "close": ("failed", "timeout")},
             "busy, bad JSON and a timeout", zone_status="ok")
    got = saved("zt-fail-mixed")
    parts = [i["part"] for i in got["data"]["damage_summary"]]
    check("Boot Lid Lock" in parts and "Right Tail Lamp" in parts and "Left Rear Door" in parts
          and "Right Rear Quarter Panel" not in parts, "what the zone that answered found is merged; the rest isn't")
    scenario("zt-fail-day", day_used, {"rear": ("failed", "day_allowance_used"), "left": ("skipped", "no AI allowance left"),
                                       "front": ("skipped", "no AI allowance left"),
                                       "close": ("skipped", "no AI allowance left")},
             "the day's allowance used up", zone_status="failed")
    got = saved("zt-fail-day")
    items = got["data"]["damage_summary"]
    check([i["part"] for i in items][:1] == ["Rear Bumper"] and all(i["found_by"] == ["overall"] for i in items),
          "no zone answered: the overall reading's items, found by it alone")
    old_box = zones.TIME_BOX
    zones.TIME_BOX = -1.0
    try:
        scenario("zt-fail-time", answer_zones, {z: ("skipped", "out of time") for z in ("rear", "left", "front", "close")},
                 "out of time", zone_status="failed")
    finally:
        zones.TIME_BOX = old_box
    check(not zone_requests(), "out of time: no zone call made")


# ---------------------------------------------------------------- grid marks


def grid_marks() -> None:
    REQUESTS.clear()

    def with_cells(kwargs, labels):
        answer = copy.deepcopy(ZONE_ANSWERS[which_zone(labels)])
        for it in answer["damage"]:
            it["grid_cells"] = ["B2", "B3"]
        return answer

    HANDLERS.update(draft=zone_draft, zone=with_cells)
    folder = grid.grid_dir()
    with Settings(gemini_model_chain=LITE, intake_read_mode="zones", intake_grid_marks=True):
        paths = make_case("zt-grid", ZONE_CASE)
        originals = {p: p.read_bytes() for p in paths}
        pipeline._read("zt-grid", AiActor.cli())
        made = sorted(folder.glob("*.jpg"))
        stamps = {p: p.stat().st_mtime_ns for p in made}
        first = len(zone_requests())
        pipeline._read("zt-grid", AiActor.cli(), kind="redraft")
    check(made and len(made) == 11 - 1, "a derived copy per zone photo (the wide shot in two zones made once)",
          len(made))
    check(sorted(folder.glob("*.jpg")) == made and all(p.stat().st_mtime_ns == stamps[p] for p in made),
          "a re-read reuses them: none drawn again")
    check(all(p.read_bytes() == data for p, data in originals.items()), "the stored photos are untouched")
    check(folder == Path(settings.storage_dir) / "ai_derived" / "grid"
          and all(re.fullmatch(r"[0-9a-f]{64}\.jpg", p.name) for p in made),
          "STORAGE_DIR/ai_derived/grid/<sha256>.jpg")
    req = zone_requests()[0]
    texts = [p["text"] for p in req["input"] if p.get("type") == "text"]
    images = [p for p in req["input"] if p.get("type") == "image"]
    import base64

    check(any(t.endswith("with grid marks:") for t in texts) and all(
        re.fullmatch(r"Photo P\d+:", t) or t.endswith("with grid marks:") or t.endswith("photos follow.") for t in texts),
        "'Photo P07:' stays as it is; the grid copy has its own text part")
    derived = {base64.b64encode(p.read_bytes()).decode() for p in made}
    every = zone_requests()
    check(all(img["data"] in derived for r in every for img in r["input"] if img.get("type") == "image")
          and all(sum(1 for p in r["input"] if p.get("type") == "text" and p["text"].endswith("with grid marks:"))
                  == len(_labels(r["input"])) for r in every),
          "every zone call sends only grid copies (the wide shot both zones wanted, drawn once)")
    check("grid_cells" in req["response_format"]["schema"]["properties"]["damage"]["items"]["properties"]
          and "A1 to C3" in req["system_instruction"], "the schema asks for grid_cells; the prompt says how")
    items = saved("zt-grid")["data"]["damage_summary"]
    lock = next(i for i in items if i["part"] == "Boot Lid Lock")
    bumper = next(i for i in items if i["part"] == "Rear Bumper")
    check(lock["grid_cells"] == ["B2", "B3"] and bumper["grid_cells"] == ["B2", "B3"],
          "grid_cells stored on the item (for its own photo)")
    check(not any("grid" in r["system_instruction"] for r in draft_requests()) and first == 4,
          "the overall reading gets no grid")
    with Settings(intake_grid_marks=False):
        plain = zone_read.build_input([("P01", str(paths[2]), "damage")])
    check(len(plain) == 3 and plain[2]["data"] == base64.b64encode(paths[2].read_bytes()).decode(),
          "grid marks off: the photo as it is")
    data = paths[3].read_bytes()
    image = grid.draw_grid(data)
    check(image.size == Image.open(io.BytesIO(data)).size, "the copy keeps the photo's size")


# ---------------------------------------------------------------- filing


def filing() -> None:
    from fastapi.testclient import TestClient

    from app.api.main import app
    from auth_helpers import sign_in

    REQUESTS.clear()
    sort = {f"P{i + 1:02d}": row for i, row in enumerate(ZONE_CASE)}

    def sort_handler(kwargs, labels):
        return {"batch_sides": None, "photos": [
            {"label": label, "kind": sort[label][0], "angle": None, "area": sort[label][1], "quality": "good",
             "shows_damage": sort[label][3], "part": sort[label][2], "note": None} for label in labels]}

    HANDLERS.update(sort=sort_handler, draft=zone_draft, zone=answer_zones)
    with Settings(gemini_model_chain=LITE, intake_read_mode="zones"), TestClient(app) as client:
        sign_in(client)
        rid = client.post("/intake").json()["id"]
        for i in range(len(ZONE_CASE)):
            r = client.post(f"/intake/{rid}/photos", files={"file": (f"IMG_{i:02d}.jpg", jpeg(i + 40), "image/jpeg")},
                            data={"position": str(i)})
            assert r.status_code == 200, r.text
        assert client.post(f"/intake/{rid}/process", json={"mode": "all"}).status_code == 200
        end = time.time() + 60
        while time.time() < end:
            state = client.get(f"/intake/{rid}").json()
            if state["stage"] in ("ready", "failed"):
                break
            time.sleep(0.1)
        check(state["stage"] == "ready", "the wizard's run in zone mode ends ready", state.get("error"))
        purposes = [("sort" if "photos" in r["response_format"]["schema"]["properties"] else
                     "zone" if "zone_side_seen" in r["response_format"]["schema"]["properties"] else "draft")
                    for r in REQUESTS]
        check(purposes.count("draft") + purposes.count("zone") == 5, "5 reading calls (1 + 4 zones)", purposes)
        ids = {p["label"]: p["id"] for p in state["photos"]}
        data = state["data"]
        bumper = next(i for i in data["damage_summary"] if i["part"] == "Rear Bumper")
        check(bumper["seen_in"] == [f"@{ids['P03']}"], "a draft's seen_in is '@<photo id>', as photo_reference is")
        # P02 is deleted before filing: every later photo moves up one.
        assert client.delete(f"/intake/{rid}/photos/{ids['P02']}").status_code == 200
        assert client.post(f"/intake/{rid}/finalize", json={"data": data}).status_code == 200
        detail = client.get(f"/reports/{rid}").json()
    items = detail["data"]["damage_summary"]
    bumper = next(i for i in items if i["part"] == "Rear Bumper")
    door = next(i for i in items if i["part"] == "Door (side undetermined)")
    check(bumper["photo_reference"] == "P02" and bumper["seen_in"] == ["P02"],
          "filed: seen_in in the case's P-numbers, as photo_reference (P03 became P02)", bumper["seen_in"])
    check(door["seen_in"] == ["P05"] and door["found_by"] == ["overall", "zone:left"], "found_by kept at filing")
    db = SessionLocal()
    try:
        ai_items = db.get(Report, rid).ai_draft["damage_summary"]
        zrow = db.query(AiRun).filter(AiRun.report_id == rid, AiRun.kind == runs.ZONES_KIND).one()
        zone_photos = {z["zone"]: z["photos"] for z in zrow.photos_sent["zones"]}
    finally:
        db.close()
    check(next(i for i in ai_items if i["part"] == "Rear Bumper")["seen_in"] == ["P02"],
          "the kept AI draft's seen_in too")
    check(zone_photos["rear"] == ["P02", "P03", "P04"] and zone_photos["left"] == ["P05", "P06", "P07", "P08"],
          "the zone row's photos in P-numbers, the deleted photo gone", zone_photos)


# ---------------------------------------------------------------- the benchmark


def benchmark() -> None:
    import benchmark_intake as bench

    args = bench.parse_args(["--read-mode", "zones", "--grid-marks"])
    check(bench.model_settings(args) == {"intake_read_mode": "zones", "intake_grid_marks": True},
          "--read-mode zones --grid-marks set the two settings", bench.model_settings(args))
    check(bench.model_settings(bench.parse_args([])) == {}, "without them nothing is set (the defaults)")
    shown = bench.ai_settings(settings)
    check(shown.get("intake_read_mode") == "single" and shown.get("intake_grid_marks") is False,
          "the result file records them under ai_settings")
    calls = [{"purpose": "draft", "seconds": 20.0, "ok": True}, {"purpose": "draft_zone", "seconds": 9.0, "ok": True},
             {"purpose": "draft_zone", "seconds": 4.0, "ok": False}]
    tries = [{"purpose": "draft", "model": LITE, "outcome": "ok", "seconds": 20.0, "slot_wait_s": 0.0, "wait_429_s": 0.0,
              "prompt_tokens": 36000},
             {"purpose": "draft_zone", "model": LITE, "outcome": "ok", "seconds": 9.0, "slot_wait_s": 0.0,
              "wait_429_s": 0.0, "prompt_tokens": 13000, "output_tokens": 400},
             {"purpose": "draft_zone", "model": LITE, "outcome": "timeout", "seconds": 4.0, "slot_wait_s": 0.0,
              "wait_429_s": 0.0}]
    accounting = bench.accounting_summary(calls, tries)
    check(accounting["draft_zone"]["calls"] == 2 and accounting["draft"]["calls"] == 1
          and accounting["draft_zone"]["prompt_tokens"] == 13000, "the zone calls are a stage of their own")
    run = {"parts": {"found": 3, "expected": 9, "visible_found": 3, "visible_expected": 8, "extra": [],
                     "wrong_side": [], "found_parts": {}, "missed": []},
           "accounting": accounting,
           "zones": {"zones": [{"zone": "left", "side_seen": "right"}, {"zone": "rear", "side_seen": "rear"},
                               {"zone": "front", "side_seen": "rear"}]},
           "drafted_items": [{"part": "A", "found_by": ["overall"]}, {"part": "B", "found_by": ["zone:rear"]},
                             {"part": "C", "found_by": ["overall", "zone:rear"]}]}
    m = bench.run_metrics(run)
    check((m.get("zone_calls"), m.get("zone_failed"), m.get("zone_s"), m.get("zone_tok_in")) == (2, 1, 13.0, 13000)
          and (m.get("zone_side_flips"), m.get("zone_other_end")) == (1, 1)
          and (m.get("zone_only_parts"), m.get("both_parts")) == (1, 1), "run metrics: zone calls, seconds, tokens, "
          "side flips, other end, parts found by zones only and by both", m)
    check("found_by" in bench._ITEM_FIELDS and "seen_in" in bench._ITEM_FIELDS, "drafted items keep found_by / seen_in")
    table = "\n".join(bench.summary_table(bench.summarize([{**run, "case": "c1", "rep": 1}])))
    check("zone calls" in table and "side flips" in table, "the summary has a zone table")
    detail = bench.detail_lines({**run, "parts": {**run["parts"], "missed_visible": [], "side_undetermined": []},
                                 "vehicle": {}, "parts_with_checklist": run["parts"] | {"extra": [], "wrong_side": []},
                                 "checklist": [], "sorter": {"tagged_in_key": 0, "tagged_photos": 0, "label_precision": None,
                                                             "key_parts_photographed": 0, "key_parts": 0,
                                                             "key_photos_read": 0, "key_photos": 0,
                                                             "selection_coverage": None}})
    check(any(line.strip().startswith("zones:") for line in detail), "and each run a zones line")
    bench._app()
    check(getattr(zone_read.call_structured, "_bench_wrapped", False), "the benchmark counts zone calls (wrapped)")


def main() -> int:
    init_db()
    for name, fn in (
        ("the draft schema and the new fields", schema_unchanged),
        ("single mode (default): the same request and saved data as master", single_mode_identical),
        ("zoning", zoning),
        ("caps: photos per zone, zone calls, calls per case", caps),
        ("the zone call: prompt, schema, answer", zone_call_shape),
        ("merging the reads", merging),
        ("the agreement rule", agreement),
        ("zone mode through the pipeline", zone_mode_pipeline),
        ("zone mode keeps the overall reading's request", zone_mode_keeps_overall_request),
        ("a zone call failing never fails the reading", failures),
        ("grid marks", grid_marks),
        ("filing: seen_in and the zone row in P-numbers", filing),
        ("the benchmark: flags and the zone stage", benchmark),
    ):
        run_section(name, fn)
    print(f"\ntest_detect_zones: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAILED [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
