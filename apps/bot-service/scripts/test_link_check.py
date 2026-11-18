"""The linked check of the linked parts (contract 10: app/ai/link_check.py)
-- offline: Gemini is the fake create(**settings), as in test_check.py.

Covers:
  - off: intake_link_check "off" (the default), the linked parts off,
    intake_link_check_max_calls 0, no linked part: no request, nothing
    written; no photos of the areas: no request, a note;
  - the request: one call per area, the areas with the most linked parts
    first, at most intake_link_check_max_calls; gemini_link_chain, store
    off, gemini_thinking_level_link; the area's skill file in the system
    prompt (left and right: side.md); the parents with their hit level and
    the CLOSED list with short ids; the area's photos only, damage first,
    at most 8, at medium resolution, never a log card or the other car;
    the schema: cue before verdict, no part, no side;
  - the answer: ids not on the list, bad cues / verdicts and repeats
    dropped; the side never taken from it; seen / likely / strip_down set
    the bucket, "no" goes last, cannot_tell changes nothing but the
    record; a "seen" without a sign or a photo taken lower; a seen with a
    photo and box: evidence by "link_check", the box cleaned, on the
    preview's frame; the reason cut and scrubbed; never an item more or
    less; link_run and the run log;
  - a 503: one short retry answers; busy throughout: two requests in all,
    unchanged and a note, the next area not asked; a timeout and bad JSON
    the same; one area
    answered and the next busy: the first kept;
  - a person's dismissal or add made during the call stands;
  - shadow: the answer in the AI draft only.

    python scripts/test_link_check.py

Makes, parts and plates are made up; the photos are coloured squares.
"""
import copy
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
_tmp = tempfile.mkdtemp(prefix="link-check-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaFAKElinkCOMPANYkey0000000000000001"
os.environ["SECOND_LOOK_ENABLED"] = "false"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"
for name in ("INTAKE_LINKS", "INTAKE_LINK_CHECK", "GEMINI_LINK_CHAIN", "INTAKE_LINK_CHECK_MAX_CALLS",
             "GEMINI_THINKING_LEVEL_LINK", "GEMINI_STORE_OFF", "INTAKE_CHECK_MODE"):
    os.environ.pop(name, None)

from PIL import Image  # noqa: E402

import app.ai.structured as structured  # noqa: E402

REQUESTS: list[dict] = []
_lock = threading.Lock()
# The current answer: fn(kwargs, labels) -> a dict sent as JSON, a string,
# or an exception raised.
HANDLER = {"fn": None}


class Busy(Exception):
    status_code = 503

    def __init__(self):
        super().__init__("Error code: 503 - the model is currently experiencing high demand")


def _labels(parts: list[dict]) -> list[str]:
    return [m.group(1) for part in parts if part.get("type") == "text"
            for m in [re.match(r"Photo (P\d+):$", part["text"])] if m]


class FakeInteractions:
    def create(self, **kwargs):
        with _lock:
            REQUESTS.append(kwargs)
        out = HANDLER["fn"](kwargs, _labels(kwargs["input"]))
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(output_text=out if isinstance(out, str) else json.dumps(out),
                               usage=SimpleNamespace(total_input_tokens=7000, total_output_tokens=300))


structured.get_client = lambda key=None: SimpleNamespace(interactions=FakeInteractions())
structured.acquire = lambda key=None: 0.0
structured.OVERLOADED_RETRY_SECONDS = 0.0
structured.CHAIN_BUSY_RETRY_SECONDS = 0.0

from app.ai import allowance, link_check as lc, runs  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import links  # noqa: E402
from app.intake.photos import ai_input_path  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import AiRun, IntakePhoto, IntakeSession, Report  # noqa: E402
from app.reports.schema import SecondLook  # noqa: E402
from app.storage.files import generate_preview  # noqa: E402

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}
V = links.TABLE_VERSION


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
    REQUESTS.clear()
    HANDLER["fn"] = answer_all
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        allowance.clear()


class Settings:
    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        self.old = {k: getattr(settings, k) for k in self.values}
        for k, v in self.values.items():
            setattr(settings, k, v)

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(settings, k, v)


ON = {"intake_links": "on", "intake_link_check": "lite", "gemini_link_chain": "lite-x,lite-y"}


def jpeg(i: int, size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (20 * i % 256, 100, (200 - 10 * i) % 256)).save(buf, "JPEG", quality=80)
    return buf.getvalue()


# A rear and left case: a log card, a rear overview, three rear damage
# photos, a left damage photo, the other party's car, a left overview, a
# front damage photo. Labels P01..P09.
CASE = [("log_card", None, None, False), ("overview", "rear", None, False), ("damage", "rear", "Rear Bumper", True),
        ("damage", "rear", "Boot Lid", True), ("damage", "rear", None, True), ("damage", "left", "Left Front Door", True),
        ("third_party", None, None, True), ("overview", "left", None, False), ("damage", "front", "Front Bumper", True)]


def listed(rid: str) -> list[dict]:
    return [{"part": "Rear Bumper", "photo_reference": f"@{rid}-2", "condition_tokens": ["Dented", "Cracked"],
             "severity": "Severe", "remarks": "Replace"},
            {"part": "Boot Lid", "photo_reference": f"@{rid}-3", "condition_tokens": ["Dented"]},
            {"part": "Left Front Door", "photo_reference": f"@{rid}-5", "condition_tokens": ["Dented"]}]


def link(rid: str, part: str, rule: str, parent: str, photo: int, kind: str, bucket: str, hit="heavy", **more) -> dict:
    return {"key": part, "part": part, "name": part, "source": "link", "covers": [part], "linked_from": [parent],
            "link_type": kind, "link_rule": f"{V}:{rule}", "hit": hit, "bucket": bucket,
            "evidence": [{"photo": f"@{rid}-{photo}", "by": "link"}], "reason": f"Linked to the {parent}.", **more}


def items_of(rid: str) -> list[dict]:
    return [
        {"key": "Left Tail Lamp", "part": "Left Tail Lamp", "name": "Left Tail Lamp", "source": "zone",
         "confidence": "Medium", "photo_reference": f"@{rid}-4", "covers": ["Left Tail Lamp"],
         "evidence": [{"photo": f"@{rid}-4", "by": "zone:rear"}]},
        link(rid, "Rear Bumper Beam", "rear-bumper>beam", "Rear Bumper", 2, "behind", "strip_down", hidden=True,
             needs_strip_down=True),
        link(rid, "Parking Sensor", "rear-bumper>parking-sensor", "Rear Bumper", 2, "attached", "likely"),
        link(rid, "Rear Bumper Reflector (side undetermined)", "rear-bumper>reflector", "Rear Bumper", 2, "attached",
             "likely"),
        link(rid, "Boot Lid Lock", "boot-lid>lock", "Boot Lid", 3, "opens_with", "likely", hidden=True),
        # No such link in the table: the area of the part it hangs off.
        link(rid, "Rear End Panel", "no-such-link", "Rear Bumper", 2, "behind", "strip_down", hidden=True,
             needs_strip_down=True),
        link(rid, "Left Front Window Regulator", "front-door>front-window-regulator", "Left Front Door", 5, "behind",
             "strip_down", hit="medium", hidden=True, needs_strip_down=True, side_from="Left Front Door"),
        link(rid, "Left Wing Mirror", "front-door>wing-mirror", "Left Front Door", 5, "attached", "likely",
             hit="medium", side_from="Left Front Door"),
    ]


def make_case(rid: str, photos=CASE, items=None, *, shadow=False, previews=True) -> None:
    folder = Path(_tmp, "photos", rid)
    folder.mkdir(parents=True)
    items = items_of(rid) if items is None else items
    look_items = [it for it in items if it.get("source") != "link"] if shadow else items
    look = SecondLook.model_validate({
        "status": "skipped", "note": "The second look is switched off.", "items": look_items,
        "dismissed": ["Rear Fog Lamp"], "reads": ["overall", "zone:rear"],
        "decisions": [{"key": "Rear Fog Lamp", "part": "Rear Fog Lamp", "action": "dismissed",
                       "at": "2026-11-17T01:00:00Z"}]}).model_dump()
    ai_draft = {"damage_summary": listed(rid)}
    if shadow:
        ai_draft["link_suggestions"] = [SecondLook.model_validate({"items": [it]}).model_dump()["items"][0]
                                        for it in items if it.get("source") == "link"]
    db = SessionLocal()
    try:
        db.add(Report(id=rid, status="intake", channel="dashboard", reporter_chat_id="test",
                      data={"description": "", "category": [], "damage_summary": listed(rid),
                            "damaged_parts": [it["part"] for it in listed(rid)], "second_look": look}))
        db.add(IntakeSession(report_id=rid, stage="review", reporter_notes="Hit from behind.", ai_draft=ai_draft))
        for i, (kind, area, part, damage) in enumerate(photos):
            path = folder / f"IMG_{i:02d}.jpg"
            path.write_bytes(jpeg(i))
            if previews:
                generate_preview(str(path))
            db.add(IntakePhoto(id=f"{rid}-{i}", report_id=rid, position=i, original_name=path.name, path=str(path),
                               sha256=f"{rid}{i}", kind=kind, area=area, part=part, shows_damage=damage,
                               quality="good" if kind else None))
        db.commit()
    finally:
        db.close()


def saved(rid: str) -> dict:
    db = SessionLocal()
    try:
        return {"data": copy.deepcopy(db.get(Report, rid).data), "ai_draft": copy.deepcopy(db.get(IntakeSession, rid).ai_draft)}
    finally:
        db.close()


def look_of(rid: str) -> dict:
    return saved(rid)["data"]["second_look"]


def by_key(items: list[dict]) -> dict[str, dict]:
    return {it["key"]: it for it in items}


def refs_of(kwargs) -> list[str]:
    return kwargs["response_format"]["schema"]["properties"]["answers"]["items"]["properties"]["id"]["enum"]


def answer_all(kwargs, labels):
    """Every id: cannot_see / cannot_tell."""
    return {"answers": [{"id": ref, "cue": "cannot_see", "verdict": "cannot_tell", "photo": None, "box_2d": None,
                         "reason": "not visible"} for ref in refs_of(kwargs)]}


REAR_ANSWER = {"answers": [
    # C1 the beam: a sign, likely.
    {"id": "C1", "cue": "crush", "verdict": "likely", "photo": "P03", "box_2d": None,
     "reason": "bumper cover pushed in deep at the centre"},
    # C2 the sensor: seen, its photo as "P3", a box; a plate in the reason.
    {"id": "C2", "cue": "cracked", "verdict": "seen", "photo": "P3", "box_2d": [100, 200, 300, 400],
     "reason": "sensor housing cracked near plate SBA1234K"},
    # C3 the reflector: no -- and a side it has no field for.
    {"id": "C3", "cue": "none_seen", "verdict": "no", "photo": "P03", "box_2d": None, "reason": "reflector intact",
     "part": "Left Rear Bumper Reflector", "side": "Left"},
    # C4 the lock: first a cue that isn't one of ours (dropped), then cannot_tell.
    {"id": "C4", "cue": "bent", "verdict": "seen", "photo": "P04", "box_2d": None, "reason": "x"},
    {"id": "C4", "cue": "cannot_see", "verdict": "cannot_tell", "photo": None, "box_2d": None, "reason": "boot shut"},
    # C5 the end panel: seen with no photo -> likely.
    {"id": "C5", "cue": "cracked", "verdict": "seen", "photo": None, "box_2d": None, "reason": "panel looks buckled"},
    # Not on the list, a repeat, a verdict that isn't one: dropped.
    {"id": "C9", "cue": "crush", "verdict": "seen", "photo": "P03", "box_2d": [1, 1, 500, 500], "reason": "x"},
    {"id": "C1", "cue": "none_seen", "verdict": "no", "photo": None, "box_2d": None, "reason": "x"},
    {"id": "C2", "cue": "cracked", "verdict": "maybe", "photo": None, "box_2d": None, "reason": "x"},
]}
LEFT_ANSWER = {"answers": [
    {"id": "C1", "cue": "cannot_see", "verdict": "strip_down", "photo": None, "box_2d": None,
     "reason": "door dented low; regulator inside cannot be seen " + "word " * 40},
    # Seen with no sign: taken as cannot_tell.
    {"id": "C2", "cue": "none_seen", "verdict": "seen", "photo": "P06", "box_2d": [100, 100, 300, 300],
     "reason": "mirror"},
]}


def by_area(kwargs, labels):
    return REAR_ANSWER if "skill: rear" in kwargs["system_instruction"] else LEFT_ANSWER


def strip(it: dict, *names) -> dict:
    return {k: v for k, v in it.items() if k not in names}


# ---------------------------------------------------------------- off


def mode_off() -> None:
    check(settings.intake_link_check == "off" and settings.intake_links == "off"
          and settings.intake_link_check_max_calls == 2, "off by default; at most 2 calls a case")
    make_case("lk-off")
    before = saved("lk-off")
    cases = [{}, {"intake_links": "on"}, {"intake_link_check": "lite"},
             {**ON, "intake_link_check_max_calls": 0}, {**ON, "intake_links": "off"}]
    for values in cases:
        with Settings(**values):
            lc.run_for_intake("lk-off", AiActor.cli())
    check(not REQUESTS and saved("lk-off") == before,
          "off, links off, check off, 0 calls: no request, nothing written (data and AI draft)")
    rid = "lk-none"
    make_case(rid, items=items_of(rid)[:1])
    before = saved(rid)
    with Settings(**ON):
        lc.run_for_intake(rid, AiActor.cli())
    check(not REQUESTS and saved(rid) == before, "no linked part: no request, nothing written")
    rid = "lk-nophoto"
    make_case(rid, photos=CASE[:1] + [("third_party", None, None, True)])
    before = saved(rid)
    with Settings(**ON):
        lc.run_for_intake(rid, AiActor.cli())
    look = look_of(rid)
    check(not REQUESTS and look["link_run"]["status"] == "skipped" and look["link_run"]["note"] == lc.NOTE_NO_PHOTOS
          and look["items"] == before["data"]["second_look"]["items"],
          "no photo of the areas: no request, the items as they were, a note", look.get("link_run"))


# ---------------------------------------------------------------- the request and the answer


def request_and_answer() -> None:
    rid = "lk-main"
    make_case(rid)
    before = saved(rid)
    HANDLER["fn"] = by_area
    with Settings(**ON):
        lc.run_for_intake(rid, AiActor.cli())
    check(len(REQUESTS) == 2, "two areas, two calls", len(REQUESTS))
    rear, left = REQUESTS[0], REQUESTS[1]
    rear_skill = lc.skill_text("rear")
    check("skill: rear" in rear["system_instruction"] and rear["system_instruction"].endswith(rear_skill)
          and rear["system_instruction"].startswith(lc.SYSTEM_PROMPT)
          and "skill: side" in left["system_instruction"] and left["system_instruction"].endswith(lc.skill_text("side")),
          "the rear's skill file in the first call (5 linked parts), side.md in the left's (2)")
    check(rear["model"] == "lite-x" and rear.get("store") is False and "generation_config" not in rear
          and "tools" not in rear, "on gemini_link_chain, store off, no thinking level unset, no tools", sorted(rear))
    item = rear["response_format"]["schema"]["properties"]["answers"]["items"]
    props = list(item["properties"])
    check(props == ["id", "cue", "verdict", "photo", "box_2d", "reason"] and props.index("cue") < props.index("verdict"),
          "the schema: id, cue BEFORE verdict, photo, box, reason -- no part, no side", props)
    check(item["properties"]["id"]["enum"] == ["C1", "C2", "C3", "C4", "C5"]
          and item["properties"]["cue"]["enum"] == list(lc.CUES)
          and item["properties"]["verdict"]["enum"] == ["seen", "likely", "strip_down", "no", "cannot_tell"]
          and item["properties"]["photo"]["anyOf"][0]["enum"] == ["P02", "P03", "P04", "P05"]
          and "minItems" not in json.dumps(rear["response_format"]["schema"]),
          "the closed list's ids, the cues, the verdicts, the photos sent; no minItems", item["properties"])
    images = [p for p in rear["input"] if p["type"] == "image"]
    check(_labels(rear["input"]) == ["P02", "P03", "P04", "P05"] and _labels(left["input"]) == ["P06", "P08"]
          and len(images) == 4 and {p["resolution"] for p in images} == {"medium"},
          "the area's photos only (never the log card, the other car or the front), at medium",
          (_labels(rear["input"]), _labels(left["input"])))
    text = rear["input"][0]["text"]
    check("- Rear Bumper: hit heavy (Dented, Cracked; Severe; Replace) -- photo P03" in text
          and "- Boot Lid: hit heavy (Dented) -- photo P04" in text
          and "- C1: Rear Bumper Beam -- behind the Rear Bumper; hidden; confirm at strip-down" in text
          and "- C3: Rear Bumper Reflector (side undetermined) -- mounted on the Rear Bumper" in text
          and "- C4: Boot Lid Lock -- opens with the Boot Lid; hidden" in text and "Area: the rear of the car." in text,
          "the parents with their hit level and photos; the closed list by id, sides as given", text)
    check("- C1: Left Front Window Regulator -- behind the Left Front Door; hidden; confirm at strip-down"
          in left["input"][0]["text"], "the left's own list", left["input"][0]["text"])

    data = saved(rid)
    look = data["data"]["second_look"]
    got = by_key(look["items"])
    check(sorted(got) == sorted(by_key(before["data"]["second_look"]["items"])) and len(look["items"]) == 8,
          "never an item more or less")
    check([it["key"] for it in look["items"]] == [
        "Left Tail Lamp", "Rear Bumper Beam", "Parking Sensor", "Boot Lid Lock", "Rear End Panel",
        "Left Front Window Regulator", "Left Wing Mirror", "Rear Bumper Reflector (side undetermined)"],
          "the \"no\" last among the linked parts; the rest in their places; the zone find first, untouched",
          [it["key"] for it in look["items"]])
    check(got["Left Tail Lamp"] == by_key(before["data"]["second_look"]["items"])["Left Tail Lamp"],
          "a suggestion that isn't a linked part: byte for byte as it was")
    beam, sensor, reflector = got["Rear Bumper Beam"], got["Parking Sensor"], got["Rear Bumper Reflector (side undetermined)"]
    lock, panel = got["Boot Lid Lock"], got["Rear End Panel"]
    regulator, mirror = got["Left Front Window Regulator"], got["Left Wing Mirror"]
    check(beam["bucket"] == "likely" and beam["link_check"] == {"verdict": "likely", "cue": "crush", "model": "lite-x",
                                                                 "reason": "bumper cover pushed in deep at the centre"}
          and beam["evidence"] == before["data"]["second_look"]["items"][1]["evidence"],
          "likely: the bucket from strip_down to likely, no evidence added", beam)
    w, h = Image.open(ai_input_path(str(Path(_tmp, "photos", rid, "IMG_02.jpg")))).size
    check(sensor["bucket"] == "seen" and sensor["link_check"]["verdict"] == "seen"
          and sensor["link_check"]["reason"] == "sensor housing cracked near plate"
          and sensor["evidence"] == [{"photo": f"@{rid}-2", "box": [100, 200, 300, 400], "box_kind": "ai",
                                      "by": "link_check", "reason": "sensor housing cracked near plate",
                                      "confidence": None, "img_w": w, "img_h": h}]
          and sensor["reason"] == "Linked to the Rear Bumper.",
          "seen with a photo and box: evidence by link_check (boxed over the parent's boxless one, on the "
          "preview's frame), the plate scrubbed, the code's reason kept", sensor)
    check(reflector["part"] == "Rear Bumper Reflector (side undetermined)" and reflector["bucket"] == "likely"
          and "side_from" not in reflector and reflector["link_check"]["verdict"] == "no"
          and regulator["side_from"] == "Left Front Door" and mirror["part"] == "Left Wing Mirror",
          "the side never from the answer: names and side_from as they were; \"no\" keeps its bucket", reflector)
    check(lock["link_check"] == {"verdict": "cannot_tell", "cue": "cannot_see", "model": "lite-x", "reason": "boot shut"}
          and strip(lock, "link_check") == strip(by_key(before["data"]["second_look"]["items"])["Boot Lid Lock"]),
          "cannot_tell: the item unchanged but for the record (a bad cue before it dropped)", lock)
    check(panel["bucket"] == "likely" and panel["link_check"]["verdict"] == "likely" and len(panel["evidence"]) == 1,
          "a seen naming no photo: taken as likely, no evidence", panel)
    check(regulator["bucket"] == "strip_down" and regulator["link_check"]["verdict"] == "strip_down"
          and len(regulator["link_check"]["reason"].split()) == 25,
          "strip_down: its bucket; the reason cut to 25 words", regulator["link_check"])
    check(mirror["bucket"] == "likely" and mirror["link_check"]["verdict"] == "cannot_tell"
          and [e["by"] for e in mirror["evidence"]] == ["link"],
          "a seen with no sign (none_seen): cannot_tell, no box", mirror)
    run = look["link_run"]
    check(run["status"] == "ok" and run["by"] == "lite" and run["model"] == "lite-x" and run["calls"] == 2
          and run["note"] is None and isinstance(run["seconds"], float),
          "link_run: ok, by lite, the model, 2 calls, no note", run)
    check(data["data"]["damage_summary"] == before["data"]["damage_summary"]
          and (look["dismissed"], look["decisions"], look["reads"]) == (
              before["data"]["second_look"]["dismissed"], before["data"]["second_look"]["decisions"],
              before["data"]["second_look"]["reads"]) and data["ai_draft"] == before["ai_draft"],
          "the damage list, dismissed, decisions, reads and the AI draft untouched")
    db = SessionLocal()
    try:
        rows = db.query(AiRun).filter(AiRun.report_id == rid).all()
        check(len(rows) == 1 and rows[0].kind == "link_check" and rows[0].status == "ok"
              and rows[0].photos_sent == [f"@{rid}-{i}" for i in (1, 2, 3, 4, 5, 7)] and rows[0].parts_found == 1
              and rows[0].model_used == "lite-x",
              "the run log: one row, kind link_check, the photos by id in camera order, the seen count",
              [(r.kind, r.status, r.photos_sent, r.parts_found, r.model_used) for r in rows])
        runs.relabel_check_runs(db, rid, {f"{rid}-{i}": f"P{i:02d}" for i in (1, 2, 3, 4, 5)})
        check(rows[0].photos_sent == ["P01", "P02", "P03", "P04", "P05"],
              "filing relabels its photos with the re-check's (a deleted photo drops out)", rows[0].photos_sent)
        db.rollback()
    finally:
        db.close()

    # Thinking level and store_off.
    rid = "lk-settings"
    make_case(rid)
    REQUESTS.clear()
    with Settings(**ON, gemini_thinking_level_link="low", gemini_store_off=False):
        lc.run_for_intake(rid, AiActor.cli())
    check(REQUESTS and all(r.get("generation_config") == {"thinking_level": "low"} and "store" not in r for r in REQUESTS),
          "gemini_thinking_level_link on its tries; store_off off: no store field",
          [(r.get("generation_config"), r.get("store")) for r in REQUESTS])


# ---------------------------------------------------------------- max calls, order, photos


def calls_and_photos() -> None:
    rid = "lk-max"
    make_case(rid)
    before = look_of(rid)
    with Settings(**ON, intake_link_check_max_calls=1):
        lc.run_for_intake(rid, AiActor.cli())
    look = look_of(rid)
    check(len(REQUESTS) == 1 and "skill: rear" in REQUESTS[0]["system_instruction"]
          and look["link_run"]["note"] == "Checked 1 of 2 areas." and look["link_run"]["calls"] == 1,
          "max 1 call: the area with the most linked parts only, a note", look["link_run"])
    check(all("link_check" not in it for it in look["items"] if it["key"].startswith("Left "))
          and by_key(look["items"])["Left Wing Mirror"] == by_key(before["items"])["Left Wing Mirror"],
          "the area not asked: as it was")

    # More on the left than at the rear: the left first.
    rid = "lk-order"
    items = items_of(rid)
    left_more = [items[0], items[1]] + items[6:] + [
        link(rid, "Left Front Door Glass", "front-door>glass", "Left Front Door", 5, "attached", "likely")]
    make_case(rid, items=left_more)
    REQUESTS.clear()
    with Settings(**ON, intake_link_check_max_calls=1):
        lc.run_for_intake(rid, AiActor.cli())
    check(len(REQUESTS) == 1 and "skill: side" in REQUESTS[0]["system_instruction"]
          and refs_of(REQUESTS[0]) == ["C1", "C2", "C3"],
          "the group order is by how many linked parts, not by area", [refs_of(r) for r in REQUESTS])

    # 12 rear damage photos, two overviews: 8 sent, damage only, the
    # parent's photo among them though it is the last.
    rid = "lk-photos"
    many = [("log_card", None, None, False), ("overview", "rear", None, False), ("overview", "rear-left", None, False)]
    many += [("damage", "rear", None, True)] * 11 + [("damage", "rear", "Rear Bumper", True)]
    items = [link(rid, "Rear Bumper Beam", "rear-bumper>beam", "Rear Bumper", 14, "behind", "strip_down")]
    make_case(rid, photos=many, items=items)
    db = SessionLocal()
    try:
        report = db.get(Report, rid)
        data = copy.deepcopy(report.data)
        data["damage_summary"][0]["photo_reference"] = f"@{rid}-14"
        report.data = data
        db.commit()
    finally:
        db.close()
    REQUESTS.clear()
    with Settings(**ON):
        lc.run_for_intake(rid, AiActor.cli())
    sent = _labels(REQUESTS[0]["input"]) if REQUESTS else []
    images = [p for p in REQUESTS[0]["input"] if p["type"] == "image"] if REQUESTS else []
    check(len(REQUESTS) == 1 and len(sent) == lc.MAX_PHOTOS == 8 and "P15" in sent
          and not {"P01", "P02", "P03"} & set(sent) and sent == sorted(sent)
          and len(images) == 8 and {p["resolution"] for p in images} == {"medium"},
          "at most 8 photos, damage first (the parent's first), in camera order, all at medium", sent)


# ---------------------------------------------------------------- failures


def failures() -> None:
    # Busy once: the short retry answers.
    rid = "lk-503-once"
    make_case(rid)
    tries = iter([Busy()])

    def once(kwargs, labels):
        return next(tries, None) or by_area(kwargs, labels)

    HANDLER["fn"] = once
    with Settings(**ON):
        lc.run_for_intake(rid, AiActor.cli())
    look = look_of(rid)
    check([r["model"] for r in REQUESTS] == ["lite-x", "lite-x", "lite-x"] and look["link_run"]["status"] == "ok"
          and by_key(look["items"])["Parking Sensor"]["bucket"] == "seen",
          "a 503: one short retry on the same model answers", [r["model"] for r in REQUESTS])

    # Busy throughout: unchanged, a note, the next area not asked.
    rid = "lk-503"
    make_case(rid)
    before = look_of(rid)
    REQUESTS.clear()
    HANDLER["fn"] = lambda kwargs, labels: Busy()
    with Settings(**ON):
        lc.run_for_intake(rid, AiActor.cli())
    look = look_of(rid)
    check(look["items"] == before["items"] and look["link_run"]["status"] == "failed"
          and look["link_run"]["note"] == lc.NOTE_BUSY and look["link_run"]["calls"] == 1,
          "busy throughout: the linked parts as they were, a note, failed", look["link_run"])
    check(len(REQUESTS) == lc.MAX_TRIES == 2 and all("skill: rear" in r["system_instruction"] for r in REQUESTS),
          "one try and one retry in all (review M3) -- then the step ends: the left area never asked",
          [r["model"] for r in REQUESTS])
    db = SessionLocal()
    try:
        row = db.query(AiRun).filter(AiRun.report_id == rid).one()
        check(row.kind == "link_check" and row.status == "failed" and all(a.get("busy") for a in row.attempts),
              "the run log: failed, every try busy", [a.get("busy") for a in row.attempts])
    finally:
        db.close()

    # Bad JSON on both models.
    rid = "lk-json"
    make_case(rid)
    before = look_of(rid)
    REQUESTS.clear()
    HANDLER["fn"] = lambda kwargs, labels: "{not json"
    with Settings(**ON):
        lc.run_for_intake(rid, AiActor.cli())
    look = look_of(rid)
    check(len(REQUESTS) == 2 and look["items"] == before["items"] and look["link_run"]["status"] == "failed"
          and look["link_run"]["note"] == lc.NOTE_FAILED,
          "bad JSON from both models: unchanged, a note", look["link_run"])
    try:
        lc.parse_answer(json.dumps({"items": []}), ["C1"], ["P01"])
        check(False, "an answer without its list is refused")
    except ValueError:
        check(True, "an answer without its list is refused (the next model is asked)")

    # The time box.
    rid = "lk-hang"
    make_case(rid)
    before = look_of(rid)
    old = lc.TIME_BOX
    lc.TIME_BOX = 0.3

    def hang(kwargs, labels):
        time.sleep(1.5)
        return by_area(kwargs, labels)

    HANDLER["fn"] = hang
    started = time.monotonic()
    try:
        with Settings(**ON):
            lc.run_for_intake(rid, AiActor.cli())
    finally:
        lc.TIME_BOX = old
    took = time.monotonic() - started
    look = look_of(rid)
    check(took < 1.4 and look["items"] == before["items"] and look["link_run"]["status"] == "failed"
          and look["link_run"]["note"] == lc.NOTE_FAILED,
          "a call past the time box: given up on the wall clock, unchanged, a note", round(took, 2))
    time.sleep(1.5)  # the hung call ends before the next section counts requests

    # The rear answered, the left busy: the rear's answer kept.
    rid = "lk-part"
    make_case(rid)
    before = look_of(rid)
    REQUESTS.clear()
    HANDLER["fn"] = lambda kwargs, labels: REAR_ANSWER if "skill: rear" in kwargs["system_instruction"] else Busy()
    with Settings(**ON):
        lc.run_for_intake(rid, AiActor.cli())
    look = look_of(rid)
    got = by_key(look["items"])
    check(look["link_run"]["status"] == "ok" and look["link_run"]["note"] == "Checked 1 of 2 areas. " + lc.NOTE_BUSY
          and got["Parking Sensor"]["bucket"] == "seen"
          and got["Left Wing Mirror"] == by_key(before["items"])["Left Wing Mirror"],
          "one area answered, the next busy: the first's answer kept, the other as it was", look["link_run"])


# ---------------------------------------------------------------- a decision meanwhile


def decision_meanwhile() -> None:
    rid = "lk-race"
    make_case(rid)
    before = look_of(rid)

    def decide_during(kwargs, labels):
        # A person dismisses the sensor and adds the lock while the call runs.
        db = SessionLocal()
        try:
            report = db.get(Report, rid)
            data = copy.deepcopy(report.data)
            look = data["second_look"]
            if "Parking Sensor" not in look["dismissed"]:
                look["dismissed"].append("Parking Sensor")
                look["decisions"] += [
                    {"key": "Parking Sensor", "part": "Parking Sensor", "action": "dismissed", "at": "2026-11-17T02:00:00Z"},
                    {"key": "Boot Lid Lock", "part": "Boot Lid Lock", "action": "added", "at": "2026-11-17T02:00:01Z"}]
                data["damage_summary"].append({"part": "Boot Lid Lock", "added_from": "suggestion"})
                report.data = data
                db.commit()
        finally:
            db.close()
        return by_area(kwargs, labels)

    HANDLER["fn"] = decide_during
    with Settings(**ON):
        lc.run_for_intake(rid, AiActor.cli())
    data = saved(rid)["data"]
    look = data["second_look"]
    got = by_key(look["items"])
    old = by_key(before["items"])
    check("Parking Sensor" in look["dismissed"] and [d["key"] for d in look["decisions"]][-2:] == ["Parking Sensor", "Boot Lid Lock"]
          and [it["part"] for it in data["damage_summary"]][-1] == "Boot Lid Lock",
          "the dismissal and the add made during the call stand", look["dismissed"])
    check(got["Parking Sensor"] == old["Parking Sensor"] and got["Boot Lid Lock"] == old["Boot Lid Lock"],
          "the two parts a person settled meanwhile: exactly as they decided on them", got["Parking Sensor"])
    check(got["Rear Bumper Beam"]["bucket"] == "likely" and look["link_run"]["status"] == "ok",
          "the others answered")


# ---------------------------------------------------------------- shadow


def shadow() -> None:
    rid = "lk-shadow"
    make_case(rid, shadow=True)
    before = saved(rid)
    HANDLER["fn"] = by_area
    with Settings(**{**ON, "intake_links": "shadow"}):
        lc.run_for_intake(rid, AiActor.cli())
    after = saved(rid)
    got = by_key(after["ai_draft"]["link_suggestions"])
    check(len(REQUESTS) == 2 and after["data"] == before["data"],
          "shadow: asked, and the case's data byte for byte as it was")
    check(got["Parking Sensor"]["bucket"] == "seen" and got["Parking Sensor"]["evidence"][0]["by"] == "link_check"
          and got["Rear Bumper Reflector (side undetermined)"]["link_check"]["verdict"] == "no"
          and [it["key"] for it in after["ai_draft"]["link_suggestions"]][-1] == "Rear Bumper Reflector (side undetermined)"
          and after["ai_draft"]["link_run"]["status"] == "ok" and after["ai_draft"]["link_run"]["calls"] == 2
          and after["ai_draft"]["damage_summary"] == before["ai_draft"]["damage_summary"],
          "the answer in the AI draft only: link_suggestions and link_run", after["ai_draft"].get("link_run"))


# ---------------------------------------------------------------- pieces


def pieces() -> None:
    table_areas = set(lc.rule_areas(links.TABLE).values())
    check(links.TABLE is not None and table_areas and table_areas <= set(lc.SKILL_OF),
          "every area of the link table has a skill file to go with", table_areas)
    for name in sorted(set(lc.SKILL_OF.values())):
        text = lc.skill_text(name) or ""
        head = re.match(r"skill: (\w+)\s+version: (\S+)", text)
        check(head is not None and head.group(1) == name and head.group(2) == V,
              f"skills/{name}.md: its header names it, at the table's version", text[:40])
    check(lc.code_verdict({"verdict": "seen", "cue": "cracked", "photo": "P01"}) == "seen"
          and lc.code_verdict({"verdict": "seen", "cue": "cracked", "photo": None}) == "likely"
          and lc.code_verdict({"verdict": "likely", "cue": "cannot_see", "photo": None}) == "cannot_tell"
          and lc.code_verdict({"verdict": "no", "cue": "none_seen", "photo": None}) == "no"
          and lc.code_verdict({"verdict": "strip_down", "cue": "cannot_see", "photo": None}) == "strip_down",
          "code's reading of a verdict: a sign for seen / likely, a photo for seen")
    got = lc.parse_answer(json.dumps({"answers": [
        {"id": "C1", "cue": "crush", "verdict": "seen", "photo": "P99", "box_2d": [1, 2, 300, 400], "reason": "dent P03"}]}),
        ["C1"], ["P01"])
    check(got == {"C1": {"cue": "crush", "verdict": "seen", "photo": None, "box": None, "reason": "dent"}},
          "a photo not sent: no photo, no box; a photo label out of the reason", got)
    check(lc.rule_id("L1:rear-bumper>beam") == "rear-bumper>beam" and lc.rule_id(None) is None,
          "link_rule -> the table's link id")
    items = [{"key": "a", "source": "link"}, {"key": "z", "source": "zone"}, {"key": "b", "source": "link"},
             {"key": "c", "source": "link"}, {"key": "d", "source": "link"}]
    out = lc.apply(items, {"a": {"link_check": {"verdict": "no", "cue": "none_seen"}, "entry": None},
                           "d": {"link_check": {"verdict": "no", "cue": "none_seen"}, "entry": None},
                           "b": {"link_check": {"verdict": "no", "cue": "none_seen"}, "entry": None}}, {"b"})
    check([it["key"] for it in out] == ["c", "z", "b", "a", "d"] and "link_check" not in out[2],
          "apply: the \"no\"s after the other open linked parts, in their order; a settled one untouched and in place",
          [it["key"] for it in out])


def main() -> int:
    init_db()
    for name, fn in (
        ("off", mode_off),
        ("the request and the answer", request_and_answer),
        ("max calls, the order, the photos", calls_and_photos),
        ("failures: a 503, busy, bad JSON, the time box", failures),
        ("a decision made during the call", decision_meanwhile),
        ("shadow", shadow),
        ("pieces", pieces),
    ):
        run_section(name, fn)
    print(f"\ntest_link_check: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAILED [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
