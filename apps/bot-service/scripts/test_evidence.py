"""Contract 9 D1, a finding's evidence (plans/2026-11-15-evidence/contract9.md
sections 4.1, 4.6 and 4.7) -- offline, Gemini is a fake.

Covers:
  - the requests: with intake_zone_evidence "off" the zone calls and the
    reading are byte for byte feat/c9-base's (golden), and so is the
    drafting schema; the zone schema's tail fields (reason, box_2d) after
    ai_confidence, the required list unchanged, nothing added when "off",
    and the one prompt sentence;
  - ai/evidence.py: clean_box's cases, clean_reason trimming and scrubbing
    plate / VIN / phone tokens, frame_of on a synthetic EXIF-6 JPEG and its
    preview, map_crop_box round trips (an edge window too), order_evidence;
  - the zone answer parsed into Evidence (never new item fields); the
    merge joining evidence per photo, its first reason, bbox_2d filled from
    evidence[0]; the sorter's boxes; boxes on a raw original dropped; the
    main read's bbox_2d cleaned, and dropped on a photo with a close-up;
  - option B's suggestions carrying key, name, covers, other_side_listed,
    evidence, reason, severity, remarks; second_look._store joining a twin's
    evidence with the second look off, a dismissed one staying out after a
    re-read, decisions kept; no reason in the run log;
  - an old draft without any of it.

    python scripts/test_evidence.py

Makes, parts and plates are made up; the photos are coloured rectangles.
"""
import copy
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="evidence-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake-evidence-company-key"
# The suggestions here are the zone reads'; the second look's own items are
# handed to _store directly.
os.environ["SECOND_LOOK_ENABLED"] = "false"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"
for name in ("GEMINI_THINKING_LEVEL_READ", "GEMINI_THINKING_LEVEL_SORT", "GEMINI_READ_TOOLS", "GEMINI_MODEL_CHAIN",
             "GEMINI_TRIAGE_MODEL_CHAIN", "GEMINI_ZONE_CHAIN", "GEMINI_STORE_OFF", "INTAKE_READ_MODE",
             "INTAKE_ZONE_EVIDENCE", "INTAKE_SORT_DAMAGE_BOX", "INTAKE_READ_CROPS"):
    os.environ.pop(name, None)

import app.ai.structured as structured  # noqa: E402

# Every create() the fake was asked, in order.
REQUESTS: list[dict] = []
_requests_lock = threading.Lock()


def _labels(parts: list[dict]) -> list[str]:
    return [m.group(1) for part in parts if part.get("type") == "text"
            for m in [re.match(r"Photo (P\d+):$", part["text"])] if m]


def kind_of(kwargs) -> str:
    props = kwargs["response_format"]["schema"].get("properties", {})
    return "zone" if "zone_side_seen" in props else "draft" if "damage_summary" in props else "other"


HANDLERS: dict = {}


def reset_handlers() -> None:
    HANDLERS.clear()
    HANDLERS.update(draft=lambda kwargs, labels: {"description": "x", "damage_summary": []},
                    zone=lambda kwargs, labels: {"zone_side_seen": "unclear", "damage": [], "checked_intact": []},
                    other=lambda kwargs, labels: {"ok": True})


reset_handlers()


class FakeInteractions:
    # **settings: store=False (every call, gemini_store_off), thinking.
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

from app.ai import evidence, second_look, zone_read  # noqa: E402
from app.ai.extraction import _parse_draft, _response_schema  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import crops, pipeline, zones  # noqa: E402
from app.parts.prefill import item_key  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import AiRun, IntakePhoto, IntakeSession, Report  # noqa: E402
from app.reports.schema import DamageSummaryItem, SecondLook, SecurityIncidentDraft  # noqa: E402
from app.storage.files import generate_preview  # noqa: E402

# Taken on feat/c9-base (6ed76bf), before D1, by the same code as
# zone_requests_digest / zone_shapes_digest below: the zone-mode requests of
# GOLDEN_CASE (the reading and three zone calls), and every zone's prompt
# and schema with and without grid marks.
GOLDEN_ZONE_REQUESTS = "4e1cd4a554a81ff80e5f000b0cbfccff499f42435fe60aaf1d4dd49427acf7c4"
GOLDEN_ZONE_SHAPES = "3c2118678eb09cb4299f84f3ed1692bda84ef8989aa07fa731f4788898d402e1"
# test_c9_base's: json.dumps of the drafting schema (shared with the bot).
GOLDEN_DRAFT_SCHEMA = "7ccb5ce425dc1fe68971672f41992753e1431b535edf941c45aa648e3d6d18d0"
# A reason that quotes what it must never show.
PLATE, VIN, PHONE = "SBA1234X", "WDB2030461A123456", "9123 4567"

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
    finally:
        reset_handlers()


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


def jpeg(i: int, size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (20 * i % 256, 100, (200 - 10 * i) % 256)).save(buf, "JPEG", quality=80)
    return buf.getvalue()


def make_case(rid: str, case: list[tuple], *, size=(64, 48), previews=True, data: dict | None = None,
              ai_draft: dict | None = None) -> None:
    """A sorted draft straight in the database: photos as (kind, area, part,
    shows damage[, the sorter's damage box]), each with its preview unless
    previews=False (then the AI gets the raw original)."""
    folder = Path(_tmp, "photos", rid)
    folder.mkdir(parents=True)
    db = SessionLocal()
    try:
        db.add(Report(id=rid, status="intake", channel="dashboard", reporter_chat_id="test",
                      data=data or {"description": "", "category": [], "damage_summary": []}))
        db.add(IntakeSession(report_id=rid, stage="reading", reporter_notes="Hit from behind.", ai_draft=ai_draft))
        for i, row in enumerate(case):
            kind, area, part, damage = row[:4]
            path = folder / f"IMG_{i:02d}.jpg"
            path.write_bytes(jpeg(i, size))
            if previews:
                generate_preview(str(path))
            db.add(IntakePhoto(id=f"{rid}-{i}", report_id=rid, position=i, original_name=path.name, path=str(path),
                               sha256=f"{rid}{i}", kind=kind, area=area, part=part, shows_damage=damage,
                               quality="good", damage_box=row[4] if len(row) > 4 else None))
        db.commit()
    finally:
        db.close()


def saved(rid: str) -> dict:
    db = SessionLocal()
    try:
        session = db.get(IntakeSession, rid)
        return {"data": copy.deepcopy(db.get(Report, rid).data), "ai_draft": copy.deepcopy(session.ai_draft)}
    finally:
        db.close()


def by_part(items: list[dict]) -> dict:
    return {it["part"]: it for it in items}


# ---------------------------------------------------------------- the requests

GOLDEN_CASE = [("log_card", None, None, False), ("overview", "rear", None, False),
               ("damage", "rear", "Rear Bumper", True), ("damage", "rear", "Boot Lid", True),
               ("damage", "left", "Left Rear Door", True), ("damage", None, None, True)]


def zone_requests_digest(rid: str) -> tuple[str, list[dict]]:
    make_case(rid, GOLDEN_CASE)
    REQUESTS.clear()
    with Settings(intake_read_mode="zones"):
        pipeline._read(rid, AiActor.cli())
    dumps = sorted(json.dumps(r, sort_keys=True, default=str) for r in REQUESTS)
    return hashlib.sha256("\n".join(dumps).encode()).hexdigest(), list(REQUESTS)


def zone_shapes_digest(mode=None) -> str:
    extra = {} if mode is None else {"evidence_mode": mode}
    shapes = {f"{zone}/{grid}": [zone_read.system_prompt(zone, grid, **extra),
                                 zone_read.response_schema(zone, ["P01", "P07"], grid, **extra)]
              for zone in zone_read.ZONE_NAMES for grid in (False, True)}
    return hashlib.sha256(json.dumps(shapes, sort_keys=True).encode()).hexdigest()


def _keys(node) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in _keys(v)}
    return set()


def requests_unchanged() -> None:
    check(settings.intake_zone_evidence == "off", "intake_zone_evidence is \"off\" by default")
    check(hashlib.sha256(json.dumps(_response_schema()).encode()).hexdigest() == GOLDEN_DRAFT_SCHEMA,
          "the drafting schema (shared with the Telegram bot) is byte for byte as before")
    check(zone_shapes_digest() == GOLDEN_ZONE_SHAPES and zone_shapes_digest("off") == GOLDEN_ZONE_SHAPES,
          "\"off\": every zone's prompt and schema, grid or not, as on feat/c9-base (golden)")
    digest, sent = zone_requests_digest("ev-golden")
    check(digest == GOLDEN_ZONE_REQUESTS and len(sent) == 4,
          "\"off\": the zone-mode reading's requests (one reading, three zones) byte for byte (golden)", digest)

    labels = ["P03", "P07"]
    for grid in (False, True):
        off = zone_read.response_schema("rear", labels, grid)
        for mode, tail in (("reason", ["reason"]), ("reason_box", ["reason", "box_2d"])):
            schema = zone_read.response_schema("rear", labels, grid, evidence_mode=mode)
            item, off_item = schema["properties"]["damage"]["items"], off["properties"]["damage"]["items"]
            names = list(item["properties"])
            check(names == list(off_item["properties"]) + tail and names.index("reason") > names.index("ai_confidence"),
                  f"{mode}{' + grid' if grid else ''}: {', '.join(tail)} after ai_confidence, last", names)
            check(item["required"] == off_item["required"], f"{mode}{' + grid' if grid else ''}: required unchanged")
            check(item["properties"]["reason"] == {"type": "string"}
                  and (mode != "reason_box" or item["properties"]["box_2d"] == {"type": "array",
                                                                                  "items": {"type": "integer"}}),
                  f"{mode}: reason a string, box_2d an integer array")
            check(not {"minItems", "maxItems"} & _keys(schema), f"{mode}: no minItems / maxItems anywhere")
            stripped = copy.deepcopy(schema)
            for name in tail:
                stripped["properties"]["damage"]["items"]["properties"].pop(name)
            check(stripped == off, f"{mode}: nothing else differs from \"off\"")
    sentence = zone_read.EVIDENCE_SENTENCE["reason_box"]
    full = zone_read.system_prompt("rear", evidence_mode="reason_box")
    check(full == zone_read.system_prompt("rear").replace("In 'checked_intact'", sentence + "In 'checked_intact'")
          and full.count(sentence) == 1 and "at most 15 words, no plate or other numbers" in full,
          "reason_box: the one sentence, before checked_intact")
    check(sentence.startswith("For each damage item also give reason: what you can see that shows this part is "
                              "damaged, at most 15 words, no plate or other numbers; and box_2d: [y_min, x_min, "
                              "y_max, x_max] on 0-1000 round the damage in its photo_reference photo -- leave box_2d "
                              "out if you can't place it."), "the contract's sentence, word for word")
    reason_only = zone_read.system_prompt("left", evidence_mode="reason")
    check("reason:" in reason_only and "box_2d" not in reason_only, "reason: the sentence without its box half")
    check(zone_read.system_prompt("rear", evidence_mode="nonsense") == zone_read.system_prompt("rear"),
          "an unknown mode asks for nothing")

    # Through the pipeline: only the zone calls change, and only so.
    make_case("ev-asked", GOLDEN_CASE)
    REQUESTS.clear()
    with Settings(intake_read_mode="zones", intake_zone_evidence="reason_box"):
        pipeline._read("ev-asked", AiActor.cli())
    asked = sorted((r for r in REQUESTS), key=lambda r: json.dumps(r, sort_keys=True, default=str))
    drafts = [r for r in REQUESTS if kind_of(r) == "draft"]
    zone_calls = [r for r in asked if kind_of(r) == "zone"]
    check(len(drafts) == 1 and drafts[0] == next(r for r in sent if kind_of(r) == "draft"),
          "reason_box: the reading's own request is the same")
    check(len(zone_calls) == 3 and all(
        "box_2d" in r["response_format"]["schema"]["properties"]["damage"]["items"]["properties"]
        and sentence in r["system_instruction"] for r in zone_calls), "and each zone call asks for reason and box_2d")


# ---------------------------------------------------------------- cleaning


def boxes() -> None:
    cb = evidence.clean_box
    check(cb([100, 200, 300, 400]) == [100, 200, 300, 400], "a box as it is")
    check(cb((100.4, 199.6, 300, 400.7)) == [100, 200, 300, 401], "rounded to whole numbers")
    check(cb([-20, 500, 300, 1020]) == [0, 500, 300, 1000] and cb([-3.5, 0, 1004, 600]) == [0, 0, 1000, 600],
          "within -20..1020: pulled into 0..1000")
    check(cb([-21, 500, 300, 900]) is None and cb([0, 0, 500, 1021]) is None, "further out: no box")
    check(cb([500, 100, 300, 400]) is None and cb([100, 400, 300, 100]) is None, "inverted: no box")
    check(cb([100, 100, 100, 400]) is None, "no height: no box")
    check(cb([100, 100, 107, 400]) is None and cb([100, 100, 108, 400]) == [100, 100, 108, 400]
          and cb([100, 100, 400, 107]) is None, "a side under 8/1000 is a sliver: no box; 8 is a box")
    check(cb([0, 0, 1000, 851]) is None and cb([0, 0, 1000, 850]) == [0, 0, 1000, 850]
          and cb([-10, -10, 1010, 1010]) is None, "over 85% of the photo: no box; 85% is one")
    for bad in ([1, 2, 3], [1, 2, 3, 4, 5], "1,2,3,4", None, {"y": 1}, [True, 100, 200, 300],
                ["100", "100", "300", "300"], [math.nan, 1, 300, 300], [math.inf, 1, 300, 300]):
        check(cb(bad) is None, f"not four numbers: {bad!r} -> no box")
    check(cb([100, 200, 300, 400], raw_original=True) is None,
          "the AI got the raw original (no preview, its frame may be turned): no box")


def reasons() -> None:
    cr = evidence.clean_reason
    check(cr("  lens\n cracked\t at the   lower edge ") == "lens cracked at the lower edge", "whitespace collapsed")
    check(cr(f"Bumper cracked below plate {PLATE}.") == "Bumper cracked below plate", "a plate is left out", cr(
        f"Bumper cracked below plate {PLATE}."))
    check(cr(f"Sill dented near VIN {VIN} stamp") == "Sill dented near VIN stamp", "a VIN is left out")
    check(cr(f"Owner {PHONE} said door jams") == "Owner said door jams", "a phone number is left out")
    check(cr("Code AB12C on the lamp") == "Code on the lamp" and cr("dent 10cm wide on P07") == "dent 10cm wide on P07",
          "letters+digits of 5 characters or more go; a size or a photo label stays")
    check(cr("Dented 2026-11-15") == "Dented" and cr("Tail lamp (SBA-123-X) broken") == "Tail lamp broken",
          "3+ digits however written go")
    check(cr('"lens cracked at the edge"') == "lens cracked at the edge" and cr("panel 'pushed in'") == "panel 'pushed in'",
          "quotes round the whole reason dropped, inner ones kept")
    long = " ".join(["scratch"] * 40)
    out = cr(long)
    check(out is not None and len(out) <= 160 and long.startswith(out) and long[len(out)] == " ",
          "cut to 160 characters at a word", len(out or ""))
    check(len(cr("x" * 300)) == 160, "one long word: cut at 160")
    check(cr(PLATE) is None and cr("   ") is None and cr("") is None and cr(None) is None and cr(42) is None,
          "nothing left (or not text): None")
    check(cr(cr(f"Lamp {PLATE} cracked")) == "Lamp cracked", "cleaning twice changes nothing")


def frames() -> None:
    folder = Path(_tmp, "frames")
    folder.mkdir()
    photo = folder / "turned.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # stored lying on its side: shown turned a quarter
    Image.new("RGB", (400, 300), "red").save(photo, "JPEG", exif=exif.tobytes())
    with Image.open(photo) as im:
        check(im.size == (400, 300) and im.getexif().get(0x0112) == 6, "a synthetic EXIF-6 JPEG, stored 400x300")
    check(evidence.frame_of(photo) == (300, 400), "frame_of: as it stands upright, 300x400",
          evidence.frame_of(photo))
    preview = generate_preview(str(photo))
    check(preview and evidence.frame_of(preview) == (300, 400),
          "its preview (what the AI is sent) has the same upright frame", evidence.frame_of(preview))
    with Image.open(preview) as im:
        check(im.size == (300, 400), "the preview is stored upright, no turn left in it")
    flat = folder / "flat.jpg"
    flat.write_bytes(jpeg(1, (640, 480)))
    check(evidence.frame_of(flat) == (640, 480), "no EXIF turn: the size as stored")
    flat.write_bytes(jpeg(1, (320, 200)))
    os.utime(flat, (1_700_000_000, 1_700_000_000))
    check(evidence.frame_of(flat) == (320, 200), "a file replaced: read again (cached per file version)")
    check(evidence.frame_of(folder / "gone.jpg") is None and evidence.frame_of(folder) is None,
          "no file, or not an image: None")

    db_photo = IntakePhoto(id="t", report_id="r", position=0, path=str(photo), sha256="", kind="damage",
                           part="Boot Lid", damage_box=[100, 100, 300, 300])
    facts = pipeline.photo_facts(db_photo)
    check(not facts.raw and facts.ai_path == preview and facts.sort_box == [100, 100, 300, 300]
          and facts.sort_part == "Boot Lid", "pipeline.photo_facts: the preview, the sorter's box and part")
    raw = IntakePhoto(id="u", report_id="r", position=1, path=str(flat), sha256="", kind="damage",
                      damage_box=[0, 0, 1000, 1000])
    facts = pipeline.photo_facts(raw)
    check(facts.raw and facts.ai_path == str(flat) and facts.sort_box is None,
          "no preview: the raw original (raw); a whole-photo sorter box is none")


def crop_round_trip() -> None:
    w, h = 1600, 1200

    def to_crop(box, window):
        left, top, right, bottom = window
        ymin, xmin, ymax, xmax = box
        return [(ymin * h / 1000 - top) / (bottom - top) * 1000, (xmin * w / 1000 - left) / (right - left) * 1000,
                (ymax * h / 1000 - top) / (bottom - top) * 1000, (xmax * w / 1000 - left) / (right - left) * 1000]

    cases = {"the middle": ([400, 400, 600, 600], [450, 450, 550, 560]),
             "an edge window (moved inside the photo)": ([0, 940, 60, 1000], [10, 950, 50, 995]),
             "a corner": ([930, 0, 1000, 50], [940, 5, 990, 45])}
    for name, (sorter, damage) in cases.items():
        window = crops.crop_window(sorter, w, h)
        crop_box = [int(round(v)) for v in to_crop(damage, window)]
        back = evidence.map_crop_box(crop_box, window, (w, h))
        check(back is not None and all(abs(a - b) <= 1 for a, b in zip(back, damage)),
              f"{name}: a box on the close-up maps back onto the photo", (window, crop_box, back, damage))
    window = crops.crop_window([0, 940, 60, 1000], w, h)
    check(window[2] == w and window[1] == 0, "the edge window touches the photo's edge", window)
    # The close-up is cut from the original; its preview is the same picture.
    big = crops.crop_window([400, 400, 600, 600], 4000, 3000)
    small = crops.crop_window([400, 400, 600, 600], 1600, 1200)
    check(evidence.map_crop_box([100, 100, 900, 900], big, (4000, 3000))
          == evidence.map_crop_box([100, 100, 900, 900], small, (1600, 1200)),
          "any copy's frame gives the same box on the photo")
    check(evidence.map_crop_box([0, 0, 1000, 1000], small, (1600, 1200)) is None,
          "the whole close-up is no box (85% rule)")
    check(evidence.map_crop_box([900, 100, 100, 900], small, (1600, 1200)) is None
          and evidence.map_crop_box([100, 100, 300, 300], None, (1600, 1200)) is None
          and evidence.map_crop_box([100, 100, 300, 300], (10, 10, 5, 50), (1600, 1200)) is None
          and evidence.map_crop_box([100, 100, 300, 300], small, (0, 1200)) is None,
          "a bad box, window or frame: None")


def ordering() -> None:
    e = evidence.entry
    entries = [e("@b", "zone:rear", confidence="High"),
               e("@c", "zone:rear", box=[100, 100, 300, 300], kind="sort", confidence="Low"),
               e("@d", "zone:left", box=[100, 100, 300, 300], confidence="Low"),
               e("@a", "overall", confidence="Low"),
               e("@d", "zone:close", box=[200, 200, 400, 400], confidence="Medium"),
               e("@e", "zone:rear", box=[100, 100, 300, 300], confidence="High"),
               {"photo": "", "by": "x"}, {"box": [1, 2, 3, 4]}, "junk"]
    out = evidence.order_evidence(entries, "@a")
    check([x["photo"] for x in out] == ["@a", "@e", "@d", "@c"],
          "photo_reference first, then AI box > sorter's box > none, High > Medium > Low; at most 4",
          [x["photo"] for x in out])
    check(out[2]["by"] == "zone:close", "one per photo: the better of two on @d kept")
    check(len(evidence.order_evidence(entries, None)) == 4 and evidence.order_evidence(entries, None)[0]["photo"] == "@e",
          "no photo_reference: best first")
    odd = evidence.order_evidence([{"photo": "@a", "box": None, "box_kind": "ai"},
                                   {"photo": "@b", "box": [1, 2, 300, 400]}], None)
    check(odd[1]["box_kind"] is None and odd[0]["box_kind"] == "ai",
          "box_kind None exactly when there is no box (a box without one is the AI's)")
    check(e("@a", "overall", box=[900, 0, 100, 5])["box"] is None and e("@a", "overall")["box_kind"] is None
          and e("@a", "zone:rear", reason=f"cracked {PLATE}")["reason"] == "cracked",
          "an entry is made clean: box and reason")


# ---------------------------------------------------------------- the zone answer


def zone_parse() -> None:
    labels = ["P03", "P04"]
    answer = {"zone_side_seen": "rear", "checked_intact": [], "damage": [
        {"photo_reference": "P03", "part": "Rear Bumper", "ai_confidence": "High",
         "reason": f"  Cover cracked under plate {PLATE} ", "box_2d": [420, 180, 760, 820]},
        {"photo_reference": "P04", "part": "Boot Lid Lock", "ai_confidence": "Medium", "reason": "Lock bent",
         "box_2d": [600, 100, 300, 400]},
        {"photo_reference": "P99", "part": "Left Tail Lamp", "ai_confidence": "Low", "reason": "Lens chipped",
         "box_2d": [10, 10, 200, 200]}]}
    got = zone_read.parse_answer("rear", json.dumps(answer), labels, evidence_mode="reason_box")
    bumper, lock, lamp = got.items
    check(bumper["evidence"] == [{"photo": "P03", "box": [420, 180, 760, 820], "box_kind": "ai", "by": "zone:rear",
                                  "reason": "Cover cracked under plate", "confidence": "High", "img_w": None,
                                  "img_h": None}] and bumper["reason"] == "Cover cracked under plate",
          "reason_box: an Evidence entry on its photo, by zone:rear; the reason scrubbed, the item's too", bumper)
    check(lock["evidence"][0]["box"] is None and lock["evidence"][0]["box_kind"] is None and lock["reason"] == "Lock bent",
          "an inverted box is no box -- the answer still stands")
    check(lamp["photo_reference"] is None and lamp["evidence"] == [] and lamp["reason"] == "Lens chipped",
          "a photo not sent: no entry (no photo to be on), the reason kept on the item")
    check(all("box_2d" not in it and it["bbox_2d"] is None for it in got.items),
          "box_2d never becomes a field of the item, nor its bbox_2d")
    check(all(DamageSummaryItem.model_validate(it).model_dump() == it for it in got.items),
          "the items are DamageSummaryItem dumps as before")
    reason = zone_read.parse_answer("rear", json.dumps(answer), labels, evidence_mode="reason")
    check(reason.items[0]["evidence"][0]["box"] is None and reason.items[0]["reason"] == "Cover cracked under plate",
          "reason: the box isn't read (it wasn't asked for)")
    off = zone_read.parse_answer("rear", json.dumps(answer), labels)
    check(off.items[0]["reason"] is None and off.items[0]["evidence"] == [
        {"photo": "P03", "box": None, "box_kind": None, "by": "zone:rear", "reason": None, "confidence": "High",
         "img_w": None, "img_h": None}], "off: neither read; the entry says which read saw it on which photo")


# ---------------------------------------------------------------- merging


def item(part, ref=None, conf=None, **kw) -> dict:
    return {**DamageSummaryItem(part=part, photo_reference=ref, ai_confidence=conf).model_dump(), **kw}


def zitem(part, ref, conf, read="zone:rear", box=None, reason=None) -> dict:
    return item(part, ref, conf, reason=evidence.clean_reason(reason),
                evidence=[evidence.entry(ref, read, box=box, reason=reason, confidence=conf)] if ref else [])


def merging() -> None:
    overall = [item("Rear Bumper", "@a", "High", bbox_2d=[420, 180, 760, 820]), item("Boot Lid", None, "Medium"),
               item("Left Tail Lamp", "@d", "Medium"),
               item("Right Rear Door", "@p1", "High")]
    rear = [zitem("Rear Bumper", "@a", "Medium", box=[400, 150, 700, 800], reason="cover cracked"),
            zitem("Rear Bumper", "@b", "High", box=[100, 100, 300, 300], reason="split at the corner"),
            zitem("Boot Lid", "@c", "Medium", box=[50, 100, 400, 900], reason="dented above the lock"),
            zitem("Left Tail Lamp", "@d", "Low", box=[300, 50, 500, 200], reason="lens cracked"),
            zitem("Boot Lid Lock", "@c", "Medium", box=[600, 450, 700, 550], reason="striker bent")]
    side = [zitem("Right Rear Door", ref, "Medium", "zone:right", box=[100, 100, 400, 400])
            for ref in ("@p2", "@p3", "@p4", "@p5")]
    before = copy.deepcopy((overall, rear, side))
    out, _ = zones.merge_reads(overall, [("zone:rear", rear), ("zone:right", side)])
    check((overall, rear, side) == before, "pure: no input changed (their evidence lists included)")
    got = by_part(out)
    bumper = got["Rear Bumper"]
    check([(e["photo"], e["by"], e["box"]) for e in bumper["evidence"]]
          == [("@a", "overall", [420, 180, 760, 820]), ("@b", "zone:rear", [100, 100, 300, 300])],
          "one entry per photo: the overall's own box on @a (High beats the zone's Medium), the zone's on @b",
          bumper["evidence"])
    check(bumper["bbox_2d"] == [420, 180, 760, 820] and bumper["reason"] == "cover cracked",
          "bbox_2d stays the overall's; the first reason a read gave is the item's")
    lid = got["Boot Lid"]
    check(lid["photo_reference"] == "@c" and lid["bbox_2d"] == [50, 100, 400, 900]
          and lid["evidence"][0]["photo"] == "@c" and lid["reason"] == "dented above the lock",
          "no photo of its own: the zone's photo, and now its box too (it used to be dropped)", lid)
    lamp = got["Left Tail Lamp"]
    check(len(lamp["evidence"]) == 1 and lamp["evidence"][0]["by"] == "zone:rear"
          and lamp["bbox_2d"] == [300, 50, 500, 200],
          "its own photo without a box, a zone's box there: the boxed entry, and bbox_2d filled from evidence[0]")
    door = got["Right Rear Door"]
    check(len(door["evidence"]) == 4 and door["evidence"][0]["photo"] == "@p1" and door["seen_in"] == [
        "@p1", "@p2", "@p3", "@p4", "@p5"], "at most 4 entries, photo_reference first (seen_in keeps every photo)")
    lock = got["Boot Lid Lock"]
    check(lock["found_by"] == ["zone:rear"] and lock["evidence"][0]["box"] == [600, 450, 700, 550]
          and lock["bbox_2d"] == [600, 450, 700, 550] and lock["reason"] == "striker bent",
          "a zone-only find keeps its entry; its bbox_2d from it")
    # Items from before (no evidence key at all) still merge.
    bare = [{"part": "Rear Bumper", "photo_reference": "@a", "ai_confidence": "High"}]
    out, _ = zones.merge_reads(bare, [("zone:rear", [{"part": "Rear Bumper", "photo_reference": "@b",
                                                      "ai_confidence": "Low"}])])
    check([e["photo"] for e in out[0]["evidence"]] == ["@a", "@b"] and out[0]["evidence"][1]["by"] == "zone:rear",
          "items without evidence: each read's photo becomes its entry")

    listed, suggested = zones.split_suggestions(zones.merge_reads(overall, [("zone:rear", rear)])[0])
    sugg = suggested[0]
    check([s["part"] for s in suggested] == ["Boot Lid Lock"] and sugg["reason"] == "striker bent"
          and sugg["evidence"][0]["box"] == [600, 450, 700, 550] and sugg["key"] == "Boot Lid Lock"
          and sugg["covers"] == ["Boot Lid Lock"], "split: the suggestion carries its reason, evidence, key, covers")


def sorter_boxes() -> None:
    def facts(part=None, box=(100, 100, 300, 300), raw=False):
        return evidence.PhotoFacts(ai_path=str(_frame_file), raw=raw, sort_box=list(box) if box else None,
                                   sort_part=part)

    photos = {"x": facts("Boot Lid"), "y": facts("Rear Bumper"), "z": facts("Right Tail Lamp"),
              "u": facts("Tail Lamp (side undetermined)"), "r": facts("Boot Lid", raw=True), "n": facts(box=None)}
    listed = [item("Boot Lid", "@x", "Medium"), item("Rear Bumper", "@x", "High", bbox_2d=[500, 500, 700, 700]),
              item("Left Tail Lamp", "@z", "Medium"), item("Rear End Panel", "@z", "Low"),
              item("Left Tail Lamp", "@u", "Medium", seen_in=["@u"]), item("Boot Lid Hinge", "@u", "Low"),
              item("Boot Lid Weatherstrip", "@r", "Low", bbox_2d=[100, 100, 300, 300]),
              item("Rear Windscreen", "@n", "Low")]
    for it in listed:
        it["evidence"] = evidence.entries_of(it, "overall")
    suggested = [{"key": "Boot Lid Lock", "part": "Boot Lid Lock", "photo_reference": "@y", "confidence": "Medium",
                  "evidence": [evidence.entry("@y", "zone:rear", reason="lock bent", confidence="Medium")]}]
    evidence.finish(listed, suggested, photos)
    lid, bumper, lamp_z, panel, lamp_u, hinge, strip, glass = listed
    check(lid["evidence"] == [{"photo": "@x", "box": [100, 100, 300, 300], "box_kind": "sort", "by": "sort",
                               "reason": None, "confidence": "Medium", "img_w": 640, "img_h": 480}],
          "the sorter named the same part: its box, by sort, box_kind sort, the finding's confidence, the frame",
          lid["evidence"])
    check(lid["bbox_2d"] is None, "bbox_2d is never filled from a sorter's box (it is the AI's own mark)")
    check(bumper["evidence"][0]["box_kind"] == "ai" and bumper["evidence"][0]["img_w"] == 640,
          "a finding with its own AI box there: no sorter box; its box carries the frame")
    check(suggested[0]["evidence"][0]["box_kind"] == "sort" and suggested[0]["evidence"][0]["box"] == [100, 100, 300, 300],
          "the only finding on its photo: the sorter's box whatever part it named (a suggestion too)")
    check(not any(e.get("box") for e in lamp_z["evidence"] + panel["evidence"]),
          "the other side's twin, beside another finding: no box (a rear photo shows both lamps)")
    check(lamp_u["evidence"][0]["box_kind"] == "sort" and not hinge["evidence"][0]["box"],
          "a sorter's part with no side matches the sided finding, not the other part beside it")
    check(strip["evidence"][0]["box"] is None and strip["bbox_2d"] is None,
          "a raw original: the main read's box and the sorter's both dropped")
    check(glass["evidence"][0]["box"] is None, "no sorter box: nothing added")
    check(suggested[0]["reason"] == "lock bent", "the finding's reason stays its own")
    again = copy.deepcopy((listed, suggested))
    evidence.finish(listed, suggested, photos)
    check((listed, suggested) == again, "settling twice changes nothing")


def crops_drop_overall_box() -> None:
    case = [("damage", "rear", "Rear Bumper", True, [400, 400, 600, 600]),
            ("damage", "rear", "Boot Lid", True, None),
            ("damage", "rear", "Boot Lid Lock", True, [300, 300, 450, 450])]

    def draft(kwargs, labels):
        return {"description": "x", "damage_summary": [
            {"part": "Rear Bumper", "photo_reference": "P01", "bbox_2d": [380, 380, 620, 640], "ai_confidence": "High"},
            {"part": "Boot Lid", "photo_reference": "P02", "bbox_2d": [100, 100, 400, 400], "ai_confidence": "High"},
            {"part": "Rear Windscreen", "photo_reference": "P03", "bbox_2d": [10, 10, 200, 900],
             "ai_confidence": "Low"}]}

    HANDLERS["draft"] = draft
    make_case("ev-crops", case, size=(640, 480))
    REQUESTS.clear()
    with Settings(intake_read_crops=True):
        pipeline._read("ev-crops", AiActor.cli())
    sent = [p["text"] for r in REQUESTS if kind_of(r) == "draft" for p in r["input"] if p.get("type") == "text"]
    check("Close-up of photo P01:" in sent and "Close-up of photo P03:" in sent, "close-ups were sent for P01 and P03")
    got = by_part(saved("ev-crops")["data"]["damage_summary"])
    check(got["Rear Bumper"]["bbox_2d"] is None and got["Rear Windscreen"]["bbox_2d"] is None,
          "a photo with a close-up: the main read's box is dropped (P07 names both)")
    check(got["Boot Lid"]["bbox_2d"] == [100, 100, 400, 400] and got["Boot Lid"]["evidence"][0]["img_w"] == 640,
          "a photo without one keeps its box, with its frame")
    check(got["Rear Bumper"]["evidence"][0]["box_kind"] == "sort"
          and got["Rear Windscreen"]["evidence"][0]["box_kind"] == "sort",
          "...and the sorter's box (the close-up's own) is its evidence there instead")


def extraction_box() -> None:
    raw = {"description": "x", "damage_summary": [
        {"part": "Rear Bumper", "photo_reference": "P01", "bbox_2d": [700, 100, 300, 400]},
        {"part": "Boot Lid", "photo_reference": "P02", "bbox_2d": [0, 0, 1000, 1000]},
        {"part": "Bonnet", "photo_reference": "P03", "bbox_2d": [100, -5, 300, 1012]},
        {"part": "Roof Panel", "photo_reference": "P04", "bbox_2d": [100, 100, 300, 300]}]}
    draft = _parse_draft(json.dumps(raw))
    check([it.bbox_2d for it in draft.damage_summary] == [None, None, [100, 0, 300, 1000], [100, 100, 300, 300]],
          "the main read's bbox_2d cleaned where it is parsed (also the bot's): inverted and whole-photo dropped",
          [it.bbox_2d for it in draft.damage_summary])


# ---------------------------------------------------------------- the pipeline

# P01 log card, P02 rear overview, P03 / P04 rear damage, P05 left damage,
# P06 a close-up whose area wasn't clear. The sorter boxed P04.
E2E_CASE = [("log_card", None, None, False), ("overview", "rear", None, False),
            ("damage", "rear", "Rear Bumper", True), ("damage", "rear", "Boot Lid", True, [550, 350, 700, 650]),
            ("damage", "left", "Left Rear Door", True), ("damage", None, None, True)]


def e2e_draft(kwargs, labels):
    return {"description": "Hit at the rear.", "damage_summary": [
        {"part": "Rear Bumper", "photo_reference": "P03", "ai_confidence": "High", "bbox_2d": [420, 180, 760, 820]},
        {"part": "Boot Lid", "photo_reference": "P04", "ai_confidence": "Medium"},
        # On another photo than the zone's Left Tail Lamp: both sides may be
        # damaged (on the same photo the reads would dispute the side).
        {"part": "Right Tail Lamp", "photo_reference": "P03", "ai_confidence": "Medium"}],
        "vehicle_info": {"make": "Gammamotors", "model": "Vezel"}}


def e2e_zone(kwargs, labels):
    if "P06" in labels:
        return {"zone_side_seen": "unclear", "checked_intact": [], "damage": [
            {"photo_reference": "P06", "part": "Boot Lid Lock", "ai_confidence": "Low",
             "reason": "lock pushed in", "box_2d": [100, 100, 400, 400]}]}
    if "P05" in labels:
        return {"zone_side_seen": "left", "checked_intact": [], "damage": [
            {"photo_reference": "P05", "part": "Rear Door", "ai_confidence": "High", "condition_tokens": ["Dented"],
             "reason": f"Door skin dented, owner {PHONE}", "box_2d": [200, 200, 600, 700]}]}
    return {"zone_side_seen": "rear", "checked_intact": [], "damage": [
        {"photo_reference": "P03", "part": "Rear Bumper", "ai_confidence": "High",
         "reason": f"Bumper cover cracked below plate {PLATE}", "box_2d": [400, 150, 700, 800]},
        {"photo_reference": "P04", "part": "Boot Lid", "ai_confidence": "Medium", "reason": "Lid dented above lock"},
        {"photo_reference": "P04", "part": "Boot Lid Lock", "ai_confidence": "Medium", "condition_tokens": ["Bent"],
         "severity": "Minor", "remarks": "Replace", "reason": "Lock striker bent"},
        {"photo_reference": "P02", "part": "Left Tail Lamp", "ai_confidence": "High", "reason": "Lens smashed",
         "box_2d": [300, 50, 450, 250]}]}


def pipeline_e2e() -> None:
    HANDLERS.update(draft=e2e_draft, zone=e2e_zone)
    rid = "ev-e2e"
    make_case(rid, E2E_CASE, size=(640, 480))
    with Settings(intake_read_mode="zones", intake_zone_evidence="reason_box"):
        pipeline._read(rid, AiActor.cli())
    got = saved(rid)
    data, ai_draft = got["data"], got["ai_draft"]
    pid = {f"P{i + 1:02d}": f"@{rid}-{i}" for i in range(len(E2E_CASE))}
    listed = by_part(data["damage_summary"])
    check(set(listed) == {"Rear Bumper", "Boot Lid", "Right Tail Lamp"}, "the list: the main read's parts", sorted(listed))
    bumper = listed["Rear Bumper"]
    check([(e["photo"], e["by"], e["box_kind"]) for e in bumper["evidence"]] == [(pid["P03"], "overall", "ai")]
          and bumper["evidence"][0]["img_w"] == 640 and bumper["evidence"][0]["img_h"] == 480
          and bumper["bbox_2d"] == [420, 180, 760, 820],
          "Rear Bumper: one photo, the overall's box kept (High), with the preview's frame", bumper["evidence"])
    check(bumper["reason"] == "Bumper cover cracked below plate", "its reason: the zone read's, the plate left out")
    lid = listed["Boot Lid"]
    check(lid["evidence"][0]["box_kind"] == "sort" and lid["evidence"][0]["box"] == [550, 350, 700, 650]
          and lid["bbox_2d"] is None and lid["reason"] == "Lid dented above lock",
          "Boot Lid: no AI box on P04, the sorter named it there: the sorter's box; the zone's reason", lid["evidence"])

    sugg = by_part(ai_draft["zone_suggestions"])
    check(set(sugg) == {"Boot Lid Lock", "Left Tail Lamp", "Left Rear Door"},
          "the zone-only finds are suggestions", sorted(sugg))
    lock = sugg["Boot Lid Lock"]
    want = {"key": "Boot Lid Lock", "covers": ["Boot Lid Lock"], "severity": "Minor", "remarks": "Replace",
            "reason": "Lock striker bent", "other_side_listed": None, "source": "zone",
            "found_by": ["zone:rear", "zone:close"], "seen_in": [pid["P04"], pid["P06"]]}
    check({k: lock[k] for k in want} == want and lock["name"],
          "a suggestion: key, name, covers, other_side_listed, reason, severity, remarks", lock)
    check([(e["photo"], e["by"], e["box"]) for e in lock["evidence"]]
          == [(pid["P04"], "zone:rear", None), (pid["P06"], "zone:close", [100, 100, 400, 400])],
          "its evidence by photo id, one per read's photo; on P04 two findings and the sorter named the other: "
          "no box there", lock["evidence"])
    tail = sugg["Left Tail Lamp"]
    check(tail["other_side_listed"] == "Right Tail Lamp" and tail["evidence"][0]["box"] == [300, 50, 450, 250]
          and tail["evidence"][0]["img_w"] == 640, "other_side_listed: the list's Right Tail Lamp; its AI box", tail)
    door = sugg["Left Rear Door"]
    check(door["reason"] == "Door skin dented, owner" and door["evidence"][0]["box_kind"] == "ai",
          "a phone number in a reason is left out")
    look = data["second_look"]
    items = by_part(look["items"])
    check(look["status"] == "skipped" and set(items) == {"Boot Lid Lock", "Left Tail Lamp", "Left Rear Door"},
          "second look off: _store shows them", sorted(items))
    check(items["Boot Lid Lock"]["evidence"] == lock["evidence"] and items["Boot Lid Lock"]["seen_in"] == lock["seen_in"],
          "with their evidence as the reading left it")
    lamp = items["Left Tail Lamp"]
    check(lamp["other_side_listed"] == "Right Tail Lamp" and lamp["name"] and lamp["reason"] == "Lens smashed",
          "named, other_side_listed against the form")
    check(all(SecondLook.model_validate(look).model_dump()["items"][i] == look["items"][i]
              for i in range(len(look["items"]))), "stored as SecondLook dumps")

    db = SessionLocal()
    try:
        rows = [{c.name: getattr(r, c.name) for c in r.__table__.columns}
                for r in db.query(AiRun).filter(AiRun.report_id == rid).all()]
    finally:
        db.close()
    logged = json.dumps(rows, default=str)
    check(rows and not any(w in logged for w in ("Lock striker", "Lens smashed", "dented", PLATE)),
          "no reason (nor the plate it quoted) reaches the run log")

    # A person marks one Not damaged; a decision is on record. Re-read.
    decision = {"key": "Left Rear Door", "part": "Left Rear Door", "action": "dismissed", "by_id": "u1",
                "by_name": "Surveyor", "at": "2026-11-15T01:02:03Z", "looked": True, "where": "intake"}
    db = SessionLocal()
    try:
        report = db.get(Report, rid)
        look = dict(report.data["second_look"])
        look.update(dismissed=["Left Rear Door"], decisions=[decision])
        report.data = {**report.data, "second_look": look}
        db.commit()
    finally:
        db.close()
    with Settings(intake_read_mode="zones", intake_zone_evidence="reason_box"):
        pipeline._read(rid, AiActor.cli(), kind="redraft")
    look = saved(rid)["data"]["second_look"]
    keys_ = {it["key"] for it in look["items"]}
    check(keys_ == {"Boot Lid Lock", "Left Tail Lamp"} and look["dismissed"] == ["Left Rear Door"]
          and look["decisions"] == [decision], "after a re-read: the dismissed one stays out, the decision is kept")
    check(all(it["evidence"] for it in look["items"]), "and the others keep their evidence")


def raw_originals() -> None:
    HANDLERS.update(draft=e2e_draft, zone=e2e_zone)
    make_case("ev-raw", E2E_CASE, size=(640, 480), previews=False)
    with Settings(intake_read_mode="zones", intake_zone_evidence="reason_box"):
        pipeline._read("ev-raw", AiActor.cli())
    got = saved("ev-raw")
    boxes_ = [e["box"] for it in got["data"]["damage_summary"] + got["ai_draft"]["zone_suggestions"]
              for e in it["evidence"]]
    check(boxes_ and all(b is None for b in boxes_)
          and all(it["bbox_2d"] is None for it in got["data"]["damage_summary"]),
          "no previews (the AI got the raw originals): every box dropped, bbox_2d too")
    check(by_part(got["data"]["damage_summary"])["Rear Bumper"]["reason"] == "Bumper cover cracked below plate",
          "the reasons stay")


def store_merge() -> None:
    def suggestion(part, ref, conf, read, box=None, reason=None):
        return {"key": part, "part": part, "photo_reference": ref, "confidence": conf, "source": "zone",
                "found_by": [read], "seen_in": [ref], "reason": evidence.clean_reason(reason),
                "evidence": [evidence.entry(ref, read, box=box, reason=reason, confidence=conf)]}

    mirror = suggestion("Left Wing Mirror", "@a", "High", "zone:left", box=[100, 100, 300, 300])
    # The same mirror read without a side: one part, two reads.
    twin = suggestion("Wing Mirror (side undetermined)", "@b", "Low", "zone:close", reason="glass cracked")
    lamp = suggestion("Left Tail Lamp", "@e", "Medium", "zone:rear", reason="lens chipped")
    lock = suggestion("Boot Lid Lock", "@c", "High", "zone:rear", reason="striker bent")
    data = {"description": "x", "damage_summary": [{"part": "Right Tail Lamp", "photo_reference": "@z"}],
            "vehicle_info": {"make": "Gammamotors", "model": "Vezel"}}
    make_case("ev-store", [("damage", "rear", None, True)], data=data,
              ai_draft={"damage_summary": data["damage_summary"], "zone_suggestions": [twin, mirror, lamp, lock],
                        "zone_reads": ["overall", "zone:rear", "zone:left", "zone:close"]})
    second_look._store("ev-store", {"status": "ok", "items": [
        {"part": "Boot Lid Lock", "photo_reference": "@d", "confidence": "Medium", "source": "nearby",
         "note": f"lock bent, plate {PLATE}"},
        {"part": "Boot Lid Hinge", "photo_reference": "@d", "confidence": "Low", "source": "kit",
         "note": "hinge pushed in"}]})
    items = by_part(saved("ev-store")["data"]["second_look"]["items"])
    check(set(items) == {"Left Wing Mirror", "Left Tail Lamp", "Boot Lid Lock", "Boot Lid Hinge"}, "one per part",
          sorted(items))
    got = items["Left Wing Mirror"]
    check(got["confidence"] == "High" and got["found_by"] == ["zone:left", "zone:close"] and got["seen_in"] == ["@a", "@b"]
          and [e["photo"] for e in got["evidence"]] == ["@a", "@b"] and got["evidence"][0]["box"] == [100, 100, 300, 300]
          and got["reason"] == "glass cracked",
          "twins: the surer kept; the other's reads, photos, evidence and reason joined", got)
    check(items["Left Tail Lamp"]["other_side_listed"] == "Right Tail Lamp" and got["other_side_listed"] is None,
          "other_side_listed against the form's list")
    lock_ = items["Boot Lid Lock"]
    check(lock_["source"] == "zone" and lock_["confidence"] == "High" and lock_["reason"] == "striker bent"
          and [(e["photo"], e["by"]) for e in lock_["evidence"]] == [("@c", "zone:rear"), ("@d", "second_look")]
          and lock_["evidence"][1]["reason"] == "lock bent, plate",
          "the second look naming a zone's part: the surer kept, its photo joined as by second_look", lock_)
    hinge = items["Boot Lid Hinge"]
    check(hinge["reason"] == "hinge pushed in" and hinge["evidence"][0]["by"] == "second_look"
          and hinge["seen_in"] == ["@d"], "a second-look find: its note its reason, its photo its evidence")


def old_drafts() -> None:
    old_item = {"part": "Rear Bumper", "photo_reference": "P03", "bbox_2d": [100, 100, 300, 300],
                "ai_confidence": "High"}
    draft = SecurityIncidentDraft.model_validate({"description": "x", "damage_summary": [old_item]})
    check(draft.damage_summary[0].evidence == [] and draft.damage_summary[0].reason is None,
          "an old item loads: no evidence, no reason")
    settled = evidence.settle(dict(old_item), fill_box=True)
    check(settled["evidence"] == [] and settled["bbox_2d"] == [100, 100, 300, 300] and settled["reason"] is None,
          "settled with no evidence: nothing invented, its box kept")
    old_sugg = {"part": "Boot Lid Lock", "photo_reference": "@x", "confidence": "Medium", "source": "zone",
                "condition_tokens": []}
    data = {"description": "x", "damage_summary": [{"part": "Rear Bumper"}],
            "second_look": {"status": "ok", "items": [{"part": "Boot Lid Hinge"}], "dismissed": ["Boot Lid Hinge"]}}
    make_case("ev-old", [("damage", "rear", None, True)], data=data,
              ai_draft={"damage_summary": data["damage_summary"], "zone_suggestions": [old_sugg]})
    second_look.run_for_intake("ev-old", AiActor.cli())
    look = saved("ev-old")["data"]["second_look"]
    check([(it["key"], it["evidence"], it["reason"]) for it in look["items"]] == [("Boot Lid Lock", [], None)]
          and look["dismissed"] == ["Boot Lid Hinge"],
          "an old draft's zone suggestion (no key, evidence or reason): kept, keyed, nothing invented", look["items"])
    evidence.finish([{"part": "Rear Bumper"}], [{"part": "Boot Lid Lock"}], {})
    check(True, "finish on items with no photo or evidence at all doesn't fail")
    check(item_key({"part": "Boot Lid Lock"}) == "Boot Lid Lock", "keys as before")


_frame_file = Path(_tmp, "frame-640x480.jpg")


def main() -> int:
    init_db()
    _frame_file.write_bytes(jpeg(9, (640, 480)))
    for name, fn in (
        ("the requests: \"off\" is byte for byte the base's; the tail fields when asked", requests_unchanged),
        ("clean_box", boxes),
        ("clean_reason: trimmed and scrubbed", reasons),
        ("frame_of: a synthetic EXIF-6 JPEG keeps its frame", frames),
        ("map_crop_box: close-up boxes back onto the photo", crop_round_trip),
        ("order_evidence", ordering),
        ("the zone answer: tail fields into Evidence", zone_parse),
        ("merging: evidence per photo, the first reason, bbox_2d filled", merging),
        ("the sorter's boxes, frames, raw originals (evidence.finish)", sorter_boxes),
        ("close-ups on: the main read's box dropped on their photos", crops_drop_overall_box),
        ("the main read's bbox_2d cleaned", extraction_box),
        ("the pipeline: list, suggestions, _store, a re-read, the run log", pipeline_e2e),
        ("raw originals through the pipeline", raw_originals),
        ("second_look._store: twins' evidence joined, the second look's own", store_merge),
        ("old drafts", old_drafts),
    ):
        run_section(name, fn)
    print(f"\ntest_evidence: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAILED [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
