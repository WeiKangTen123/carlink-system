"""The re-check of the suggestions (contract 9 section 4.7: app/ai/check.py,
app/ai/decisions.py) -- offline: the Decisions API is an httpx.MockTransport
and the lite re-check the fake create(**settings).

Covers:
  - intake_check_mode "off" (the default), and a draft with no
    suggestions: no call, nothing written;
  - the Decisions API request byte for byte in section 2's shape: the URL,
    the key in the x-goog-api-key header only, {model, input, questions},
    "Photo P07:" before each photo at intake_check_resolution, one neutral
    question per suggestion naming its photos by label; only car photos
    (never a log card), a suggestion with no photo left unasked;
  - p to a verdict at the cut-offs; the answers on the suggestions, the
    CheckRun, the new order -- never an item more or less, the damage list
    and what a person decided untouched; the run log; the key in no log;
  - more than 12 photos: two requests, never three; one photo each before
    two; at most 16 questions;
  - a 429 (the next key; a day's used up: remembered, skipped after), a 503,
    a 5xx, a timeout (the transport's and the wall clock's), a "safety" 400,
    an answer without probabilities: to the lite re-check
    ("decision_then_lite"), or the end ("decision"); the lite call failing
    too: the suggestions as they were, a note;
  - the lite re-check: its request (the check chain, its thinking level,
    store=False, the schema), its parse (bad JSON refused, the reason
    scrubbed), its box as evidence by "check" only on a photo with none;
  - the sort order;
  - the whole intake: a zone-only find re-checked right after the second
    look stored it.

    python scripts/test_check.py

Makes, parts and plates are made up; the photos are coloured squares.
"""
import base64
import copy
import io
import json
import logging
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
_tmp = tempfile.mkdtemp(prefix="check-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
COMPANY_KEY = "AIzaFAKEcheckCOMPANYkey000000000000001"
os.environ["GEMINI_API_KEY"] = COMPANY_KEY
os.environ["SECOND_LOOK_ENABLED"] = "false"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"
for name in ("INTAKE_CHECK_MODE", "GEMINI_DECISION_MODEL", "GEMINI_CHECK_CHAIN", "GEMINI_THINKING_LEVEL_CHECK",
             "INTAKE_CHECK_RESOLUTION", "INTAKE_CHECK_YES", "INTAKE_CHECK_NO", "INTAKE_READ_MODE", "GEMINI_STORE_OFF"):
    os.environ.pop(name, None)

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

import app.ai.structured as structured  # noqa: E402

# Every create() the fake was asked (the lite re-check, and the reading's
# calls in the whole-intake test), in order.
REQUESTS: list[dict] = []
_lock = threading.Lock()


def kind_of(kwargs) -> str:
    props = kwargs["response_format"]["schema"].get("properties", {})
    if "findings" in props:
        return "check"
    if "photos" in props:
        return "sort"
    if "zone_side_seen" in props:
        return "zone"
    return "draft" if "damage_summary" in props else "other"


def _labels(parts: list[dict]) -> list[str]:
    return [m.group(1) for part in parts if part.get("type") == "text"
            for m in [re.match(r"Photo (P\d+):$", part["text"])] if m]


HANDLERS: dict = {}


def reset_handlers() -> None:
    HANDLERS.clear()
    HANDLERS.update(
        check=lambda kwargs, labels: {"findings": []},
        draft=lambda kwargs, labels: {"description": "x", "damage_summary": []},
        zone=lambda kwargs, labels: {"zone_side_seen": "unclear", "damage": [], "checked_intact": []},
        sort=lambda kwargs, labels: {"photos": []},
        other=lambda kwargs, labels: {"ok": True},
    )


reset_handlers()


class FakeInteractions:
    def create(self, **kwargs):
        with _lock:
            REQUESTS.append(kwargs)
        out = HANDLERS[kind_of(kwargs)](kwargs, _labels(kwargs["input"]))
        return SimpleNamespace(output_text=out if isinstance(out, str) else json.dumps(out),
                               usage=SimpleNamespace(total_input_tokens=1000, total_output_tokens=100))


structured.get_client = lambda key=None: SimpleNamespace(interactions=FakeInteractions())
structured.acquire = lambda key=None: 0.0
structured.OVERLOADED_RETRY_SECONDS = 0.0
structured.CHAIN_BUSY_RETRY_SECONDS = 0.0

from app.ai import allowance, check as recheck, decisions  # noqa: E402
from app.ai.keys import AiActor, last4  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import pipeline  # noqa: E402
from app.intake.photos import ai_input_path  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import AiRun, ApiKey, IntakePhoto, IntakeSession, Report  # noqa: E402
from app.reports.schema import SecondLook  # noqa: E402
from app.storage.files import generate_preview  # noqa: E402

decisions.acquire = lambda key=None: 0.0

# The Decisions API, faked on the wire: every request, and the answer the
# current DECIDE gives (an httpx.Response, a dict sent as JSON, or raising).
HTTP: list[httpx.Request] = []
DECIDE = {"fn": None}


def _transport(request: httpx.Request) -> httpx.Response:
    HTTP.append(request)
    out = DECIDE["fn"](request, json.loads(request.content))
    return out if isinstance(out, httpx.Response) else httpx.Response(200, json=out)


decisions.TRANSPORT = httpx.MockTransport(_transport)


def answer_all(p_of=None):
    """A Decisions API answering every question: p_of(question key, its
    instructions) or 0.9."""
    def fn(request, body):
        return {"answers": {k: {"noul": (p_of(k, q["instructions"]) if p_of else 0.9)}
                            for k, q in body["questions"].items()},
                "usage": {"total_input_tokens": 532, "total_output_tokens": 0}}
    return fn


# Everything logged, to look for the key in.
LOGGED: list[str] = []


class _Keep(logging.Handler):
    def emit(self, record):
        try:
            LOGGED.append(record.getMessage())
        except Exception:
            LOGGED.append(str(record.msg))


logging.getLogger().addHandler(_Keep())
logging.getLogger().setLevel(logging.DEBUG)

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
    HTTP.clear()
    REQUESTS.clear()
    DECIDE["fn"] = answer_all()
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        reset_handlers()
        try:
            allowance.clear()
        except Exception:
            pass


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


def jpeg(i: int, size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (20 * i % 256, 100, (200 - 10 * i) % 256)).save(buf, "JPEG", quality=80)
    return buf.getvalue()


def make_case(rid: str, photos: list[tuple], items: list[dict], *, previews=True, data: dict | None = None) -> None:
    """A draft with sorted photos (kind, area, part, shows damage) and its
    second look holding these suggestions."""
    folder = Path(_tmp, "photos", rid)
    folder.mkdir(parents=True)
    db = SessionLocal()
    try:
        base = data or {"description": "", "category": [],
                        "damage_summary": [{"part": "Rear Bumper", "photo_reference": f"@{rid}-2"}],
                        "damaged_parts": ["Rear Bumper"]}
        look = {"status": "skipped", "note": "The second look is switched off.", "items": items,
                "dismissed": ["Rear Fog Lamp"], "reads": ["overall", "zone:rear"],
                "decisions": [{"key": "Rear Fog Lamp", "part": "Rear Fog Lamp", "action": "dismissed",
                               "at": "2026-11-15T01:00:00Z"}]}
        # As second_look._store writes it: every field there.
        look = SecondLook.model_validate(look).model_dump()
        db.add(Report(id=rid, status="intake", channel="dashboard", reporter_chat_id="test",
                      data={**base, "second_look": look}))
        db.add(IntakeSession(report_id=rid, stage="reading", reporter_notes="Hit from behind."))
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
        return copy.deepcopy(db.get(Report, rid).data)
    finally:
        db.close()


def sug(rid: str, part: str, photo: int | None, *, evidence=(), seen=(), confidence="Medium", **more) -> dict:
    return {"key": more.pop("key", part), "part": part, "name": part, "source": "zone", "confidence": confidence,
            "photo_reference": f"@{rid}-{photo}" if photo is not None else None,
            "seen_in": [f"@{rid}-{i}" for i in seen], "found_by": ["zone:rear"],
            "evidence": [dict(e, photo=f"@{rid}-{e['photo']}") for e in evidence], "covers": [part], **more}


# A rear-end case: a log card, a rear overview, three rear damage close-ups,
# a left damage photo, the other party's car.
CASE = [("log_card", None, None, False), ("overview", "rear", None, False), ("damage", "rear", "Rear Bumper", True),
        ("damage", "rear", "Boot Lid", True), ("damage", "rear", None, True), ("damage", "left", "Left Rear Door", True),
        ("third_party", None, None, True)]


def base_items(rid: str) -> list[dict]:
    return [
        sug(rid, "Left Rear Bumper Reflector", 3, evidence=[{"photo": 3, "by": "zone:rear", "reason": "lens cracked"},
                                                          {"photo": 2, "by": "zone:rear"}], confidence="Medium"),
        # Its photo is the log card: the photos it was seen in instead.
        sug(rid, "Boot Lid Lock", 0, seen=(0, 2), confidence="High"),
        # Only the log card and the other party's car: nothing to ask about.
        sug(rid, "Rear End Panel", 0, seen=(6,), confidence="Low"),
        sug(rid, "Other / Not Listed", 4, key="Rear air vent", other_part_name="Rear air vent", confidence="Medium"),
    ]


def b64_of(rid: str, i: int) -> str:
    return base64.b64encode(Path(ai_input_path(str(Path(_tmp, "photos", rid, f"IMG_{i:02d}.jpg")))).read_bytes()).decode()


def keys_of(data: dict) -> list[str]:
    return [it["key"] for it in data["second_look"]["items"]]


# ---------------------------------------------------------------- off


def mode_off() -> None:
    check(settings.intake_check_mode == "off", "intake_check_mode is off by default")
    make_case("ck-off", CASE, base_items("ck-off"))
    before = saved("ck-off")
    recheck.run_for_intake("ck-off", AiActor.cli())
    check(not HTTP and not REQUESTS and saved("ck-off") == before, "off: no call, nothing written")
    make_case("ck-none", CASE, [])
    before = saved("ck-none")
    for mode in ("decision", "lite", "decision_then_lite"):
        with Settings(intake_check_mode=mode):
            recheck.run_for_intake("ck-none", AiActor.cli())
    check(not HTTP and not REQUESTS and saved("ck-none") == before,
          "no suggestions: no call and nothing written, whatever the mode")


# ---------------------------------------------------------------- the request


QUESTION_1 = ("In photo P04 (or P03), is the insured car's left rear bumper reflector damaged -- cracked, broken, "
              "dented, scratched or missing? Only the insured car.")


def decision_request() -> None:
    rid = "ck-req"
    make_case(rid, CASE, base_items(rid))
    before = saved(rid)
    LOGGED.clear()
    p_by_part = {"left rear bumper reflector": 0.91, "boot lid lock": 0.5, "rear air vent": 0.12}

    def p_of(name, instructions):
        return next(p for words, p in p_by_part.items() if f"car's {words} damaged" in instructions)

    DECIDE["fn"] = answer_all(p_of)
    with Settings(intake_check_mode="decision"):
        recheck.run_for_intake(rid, AiActor.cli())
    check(len(HTTP) == 1 and not REQUESTS, "one Decisions API request, no lite call", (len(HTTP), len(REQUESTS)))
    request = HTTP[0]
    body = json.loads(request.content)
    check(request.method == "POST" and str(request.url) == "https://generativelanguage.googleapis.com/v1beta/decisions",
          "POST to v1beta/decisions", str(request.url))
    check(request.headers.get("x-goog-api-key") == COMPANY_KEY and COMPANY_KEY not in str(request.url)
          and request.headers.get("content-type") == "application/json",
          "the key in the x-goog-api-key header, never in the URL")
    expected = {
        "model": "gemini-decision-1",
        "input": [
            {"type": "text", "text": "Photo P03:"},
            {"type": "image", "data": b64_of(rid, 2), "mime_type": "image/webp", "resolution": "medium"},
            {"type": "text", "text": "Photo P04:"},
            {"type": "image", "data": b64_of(rid, 3), "mime_type": "image/webp", "resolution": "medium"},
            {"type": "text", "text": "Photo P05:"},
            {"type": "image", "data": b64_of(rid, 4), "mime_type": "image/webp", "resolution": "medium"},
        ],
        "questions": {
            "F1": {"type": "noul", "instructions": QUESTION_1},
            "F2": {"type": "noul", "instructions": "In photo P03, is the insured car's boot lid lock damaged -- "
                                                  "cracked, broken, dented, scratched or missing? Only the insured car."},
            "F3": {"type": "noul", "instructions": "In photo P05, is the insured car's rear air vent damaged -- "
                                                  "cracked, broken, dented, scratched or missing? Only the insured car."},
        },
    }
    check(body == expected, "the body is section 2's shape exactly: model, input (the photos' previews at medium, "
                            "each after \"Photo Pnn:\"), one neutral question per suggestion", json.dumps(body)[:600])
    check(list(body) == ["model", "input", "questions"], "...and nothing else")
    words = " ".join(q["instructions"] for q in body["questions"].values()).lower()
    check("ai" not in re.findall(r"\b\w+\b", words) and "found" not in words and "suggest" not in words,
          "neutral wording: never that an AI found it")
    check("Photo P01:" not in json.dumps(body["input"]) and "Photo P07:" not in json.dumps(body["input"]),
          "never the log card (P01) or the other party's car (P07)")

    data = saved(rid)
    look = data["second_look"]
    by_key = {it["key"]: it for it in look["items"]}
    check(by_key["Left Rear Bumper Reflector"]["check"] == {"verdict": "yes", "p": 0.91, "by": "decision",
                                                            "model": "gemini-decision-1", "reason": None},
          "p 0.91: yes, with p, by decision, the model", by_key["Left Rear Bumper Reflector"]["check"])
    check(by_key["Boot Lid Lock"]["check"]["verdict"] == "unsure" and by_key["Rear air vent"]["check"]["verdict"] == "no",
          "p 0.5: unsure; p 0.12: no")
    check(by_key["Rear End Panel"]["check"] is None, "no photo to ask about: unchecked")
    check(keys_of(data) == ["Left Rear Bumper Reflector", "Boot Lid Lock", "Rear End Panel", "Rear air vent"],
          "the order: yes, then unsure and unchecked (by confidence), then no", keys_of(data))
    run = look["check"]
    check(run["status"] == "ok" and run["by"] == "decision" and run["model"] == "gemini-decision-1" and run["calls"] == 1
          and run["note"] == "Re-checked 3 of 4 suggestions." and isinstance(run["seconds"], float),
          "the step's CheckRun: ok, by decision, 1 call, a plain note", run)
    check(sorted(keys_of(data)) == sorted(keys_of(before)) and len(look["items"]) == 4,
          "never an item more or less")
    check(data["damage_summary"] == before["damage_summary"] and data["damaged_parts"] == before["damaged_parts"],
          "the damage list untouched")
    check((look["dismissed"], look["decisions"], look["reads"]) == (
        before["second_look"]["dismissed"], before["second_look"]["decisions"], before["second_look"]["reads"]),
        "dismissed, decisions and reads kept")
    strip = lambda it: {k: v for k, v in it.items() if k != "check"}  # noqa: E731
    before_items = {it["key"]: it for it in before["second_look"]["items"]}
    check(all(strip(it) == strip(before_items[it["key"]]) for it in look["items"]),
          "each suggestion as it was, plus its check")

    db = SessionLocal()
    try:
        rows = db.query(AiRun).filter(AiRun.report_id == rid).all()
    finally:
        db.close()
    # P03-P05 as "@<photo id>", as the zone rows: filing turns them into
    # the case's P-numbers (runs.relabel_check_runs).
    check(len(rows) == 1 and rows[0].kind == "check" and rows[0].status == "ok"
          and rows[0].photos_sent == [f"@{rid}-2", f"@{rid}-3", f"@{rid}-4"]
          and rows[0].parts_found == 1 and rows[0].model_used == "gemini-decision-1",
          "the run log: one row, kind check, the photos (by id, in camera order), the yes count, the model",
          [(r.kind, r.status, r.photos_sent, r.parts_found, r.model_used) for r in rows])
    attempt = rows[0].attempts[0]
    check(attempt["outcome"] == "ok" and attempt["http_status"] == 200 and attempt["key_last4"] == last4(COMPANY_KEY)
          and attempt["key_source"] == "company" and attempt["prompt_tokens"] == 532 and attempt["output_tokens"] == 0,
          "its try: ok, 200, the key's last 4 only, the tokens Google reported", attempt)
    check(COMPANY_KEY not in json.dumps(rows[0].attempts) and all(COMPANY_KEY not in m for m in LOGGED),
          "the key is in no log line and not on the run log")

    # The cut-offs.
    with Settings(intake_check_yes=0.7, intake_check_no=0.3):
        got = [recheck.verdict(p) for p in (0.99, 0.7, 0.69, 0.5, 0.31, 0.3, 0.01)]
    check(got == ["yes", "yes", "unsure", "unsure", "unsure", "no", "no"], "verdict: yes at 0.7+, no at 0.3-", got)
    with Settings(intake_check_yes=0.85, intake_check_no=0.15):
        got = [recheck.verdict(p) for p in (0.9, 0.7, 0.3, 0.1)]
    check(got == ["yes", "unsure", "unsure", "no"], "...at the cut-offs set (intake_check_yes / _no)", got)
    check(decisions.probabilities({"answers": {"F1": {"noul": 0.4}, "F2": {"noul": "0.8"}, "F3": {"noul": 1.4},
                                               "F4": {"noul": True}, "F5": 0.2}}, ["F1", "F2", "F3", "F4", "F5", "F6"])
          == {"F1": 0.4, "F2": 0.8, "F5": 0.2}, "the probabilities read leniently; out of 0..1 or missing: left out")

    # Another resolution and model; a raw original (no preview) sent as it is.
    HTTP.clear()
    make_case("ck-res", CASE, base_items("ck-res"), previews=False)
    with Settings(intake_check_mode="decision", intake_check_resolution="low", gemini_decision_model="gemini-decision-2"):
        recheck.run_for_intake("ck-res", AiActor.cli())
    body = json.loads(HTTP[0].content)
    images = [p for p in body["input"] if p["type"] == "image"]
    check(body["model"] == "gemini-decision-2" and {p["resolution"] for p in images} == {"low"}
          and {p["mime_type"] for p in images} == {"image/jpeg"},
          "intake_check_resolution and gemini_decision_model reach the request; an original without preview as JPEG")


# ---------------------------------------------------------------- many photos


def many_photos() -> None:
    rid = "ck-many"
    photos = [("damage", "rear", None, True)] * 40
    items = [sug(rid, f"Part {n}", 2 * n, seen=(2 * n + 1,), key=f"k{n:02d}") for n in range(18)]
    make_case(rid, photos, items)
    with Settings(intake_check_mode="decision"):
        recheck.run_for_intake(rid, AiActor.cli())
    bodies = [json.loads(r.content) for r in HTTP]
    images = [sum(1 for p in b["input"] if p["type"] == "image") for b in bodies]
    asked = [list(b["questions"]) for b in bodies]
    check(len(bodies) == 2 and max(images) <= 12, "more than 12 photos: two requests, at most 12 photos each", images)
    check(asked[0] == [f"F{n}" for n in range(1, 13)] and asked[1] == [f"F{n}" for n in range(13, 17)],
          "16 questions at most (F1-F12, then F13-F16)", asked)
    check(images == [12, 8], "one photo for every suggestion before a second for some", images)
    look = saved(rid)["second_look"]
    checked = [it["key"] for it in look["items"] if it.get("check")]
    check(len(checked) == 16 and look["check"]["calls"] == 2 and look["check"]["note"] == "Re-checked 16 of 18 suggestions.",
          "16 answered; the two past the cap left unchecked; 2 calls", look["check"])

    # Packing alone: never a third request.
    asks = [recheck.Ask(f"k{n}", "part", [f"a{n}", f"b{n}"]) for n in range(40)]
    requests, left_out = recheck.pack(asks)
    check(len(requests) == 2 and sum(len(r) for r in requests) == 24 and len(left_out) == 16,
          "pack: two requests at most, whatever is asked; the rest left out", (len(requests), len(left_out)))
    shared = [recheck.Ask(f"k{n}", "part", ["p1", f"p{n + 2}"]) for n in range(5)]
    requests, _ = recheck.pack(shared)
    check(len(requests) == 1 and [a.photos for a in requests[0]] == [["p1", f"p{n + 2}"] for n in range(5)],
          "a photo shared by suggestions is sent once")
    requests, left_out = recheck.pack(asks[:20], max_requests=1)
    check(len(requests) == 1 and len(requests[0]) == 12 and len(left_out) == 8, "the lite call: one request of 12")


# ---------------------------------------------------------------- failures


def google_error(status: int, code, message: str, status_name: str = "") -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message, "status": status_name}})


FAILURES_OF = {
    "a 429 (per minute)": lambda request, body: google_error(429, 429, "Resource exhausted. Please retry in 3s.",
                                                              "RESOURCE_EXHAUSTED"),
    "a 503": lambda request, body: google_error(503, 503, "The model is overloaded.", "UNAVAILABLE"),
    "a 500": lambda request, body: google_error(500, 500, "Internal error.", "INTERNAL"),
    "a safety 400": lambda request, body: google_error(400, "safety", "The input was blocked."),
    "a timeout": lambda request, body: (_ for _ in ()).throw(httpx.ReadTimeout("timed out", request=request)),
    "no probabilities": lambda request, body: {"answers": {}},
    "not JSON": lambda request, body: httpx.Response(200, text="<html>busy</html>"),
}


def lite_answer(kwargs, labels):
    refs = kwargs["response_format"]["schema"]["properties"]["findings"]["items"]["properties"]["ref"]["enum"]
    return {"findings": [{"ref": ref, "damaged": "yes" if ref == "F1" else "no",
                          "reason": "lens cracked at the edge"} for ref in refs]}


def failures() -> None:
    for n, (what, fail) in enumerate(FAILURES_OF.items()):
        rid = f"ck-fail-{n}"
        make_case(rid, CASE, base_items(rid))
        before = saved(rid)
        HTTP.clear()
        REQUESTS.clear()
        DECIDE["fn"] = fail
        HANDLERS["check"] = lite_answer
        with Settings(intake_check_mode="decision_then_lite"):
            recheck.run_for_intake(rid, AiActor.cli())
        look = saved(rid)["second_look"]
        lite = [r for r in REQUESTS if kind_of(r) == "check"]
        by_key = {it["key"]: it for it in look["items"]}
        check(len(HTTP) == 1 and len(lite) == 1 and look["check"]["status"] == "ok" and look["check"]["by"] == "lite"
              and look["check"]["note"] == "Re-checked 3 of 4 suggestions. " + recheck.NOTE_LITE
              and by_key["Left Rear Bumper Reflector"]["check"]["by"] == "lite"
              and look["check"]["calls"] == 2,
              f"{what}: decision_then_lite falls to the lite call, which answers", look["check"])
        check(sorted(keys_of(saved(rid))) == sorted(keys_of(before)), f"{what}: never an item more or less")

        # "decision" alone: the end of the step.
        rid2 = f"ck-fail-{n}-d"
        make_case(rid2, CASE, base_items(rid2))
        before = saved(rid2)
        REQUESTS.clear()
        with Settings(intake_check_mode="decision"):
            recheck.run_for_intake(rid2, AiActor.cli())
        look = saved(rid2)["second_look"]
        check(not REQUESTS and look["check"]["status"] == "failed" and look["check"]["note"] == recheck.NOTE_FAILED
              and keys_of(saved(rid2)) == keys_of(before) and not any(it.get("check") for it in look["items"]),
              f"{what}: \"decision\" alone ends there -- failed, the suggestions as they were", look["check"])

    # Both fail: the suggestions as they were, and a note.
    rid = "ck-fail-both"
    make_case(rid, CASE, base_items(rid))
    before = saved(rid)
    REQUESTS.clear()
    DECIDE["fn"] = FAILURES_OF["a 503"]
    HANDLERS["check"] = lambda kwargs, labels: "not json at all"
    with Settings(intake_check_mode="decision_then_lite"):
        recheck.run_for_intake(rid, AiActor.cli())
    look = saved(rid)["second_look"]
    lite = [r for r in REQUESTS if kind_of(r) == "check"]
    check(len(lite) == 2 and look["check"]["status"] == "failed" and look["check"]["note"] == recheck.NOTE_FAILED
          and keys_of(saved(rid)) == keys_of(before) and not any(it.get("check") for it in look["items"]),
          "the lite call failing too (bad JSON on both models): failed, the suggestions as they were", look["check"])
    db = SessionLocal()
    try:
        row = db.query(AiRun).filter(AiRun.report_id == rid).one()
    finally:
        db.close()
    check(row.kind == "check" and row.status == "failed" and [a["outcome"] for a in row.attempts] == ["error", "schema", "schema"]
          and row.attempts[0].get("busy") is True and row.attempts[0]["http_status"] == 503,
          "the run log: failed, the 503 (busy) and the two bad answers", [(a["outcome"], a.get("busy")) for a in row.attempts])

    # A day used up: remembered, and skipped after with nothing sent.
    allowance.clear()
    rid = "ck-day"
    make_case(rid, CASE, base_items(rid))
    HTTP.clear()
    DECIDE["fn"] = lambda request, body: google_error(
        429, 429, "Quota exceeded for metric: generate_requests_per_model_per_day, limit: 10000 "
                  "GenerateRequestsPerDayPerProjectPerModel", "RESOURCE_EXHAUSTED")
    HANDLERS["check"] = lite_answer
    with Settings(intake_check_mode="decision_then_lite"):
        recheck.run_for_intake(rid, AiActor.cli())
        first = len(HTTP)
        from app.ai.keys import company_keys
        fingerprint = company_keys()[0].fingerprint
        spent = allowance.spent_until(fingerprint, "gemini-decision-1")
        recheck.run_for_intake(rid, AiActor.cli())
    look = saved(rid)["second_look"]
    check(first == 1 and spent is not None and allowance.limit(fingerprint, "gemini-decision-1") == 10000,
          "a day's 429: the model remembered as used up until the reset, its limit kept")
    check(len(HTTP) == 1 and look["check"]["by"] == "lite", "the next re-check sends nothing to it: straight to lite")

    # A per-minute 429 on one key: the next key answers.
    allowance.clear()
    db = SessionLocal()
    try:
        db.add(ApiKey(provider="gemini", key="AIzaFAKEcheckSECONDkey000000000000002", label="test second"))
        db.commit()
    finally:
        db.close()
    try:
        rid = "ck-keys"
        make_case(rid, CASE, base_items(rid))
        HTTP.clear()

        def by_key(request, body):
            if request.headers["x-goog-api-key"] != COMPANY_KEY:
                return google_error(429, 429, "Resource exhausted. Please retry in 3s.", "RESOURCE_EXHAUSTED")
            return answer_all()(request, body)

        DECIDE["fn"] = by_key
        REQUESTS.clear()
        with Settings(intake_check_mode="decision_then_lite"):
            recheck.run_for_intake(rid, AiActor.cli())
        look = saved(rid)["second_look"]
        check(len(HTTP) == 2 and HTTP[1].headers["x-goog-api-key"] == COMPANY_KEY and not REQUESTS
              and look["check"]["by"] == "decision",
              "a 429 on one key: the next key is asked (the same keys as call_structured), no lite call",
              [r.headers["x-goog-api-key"][-4:] for r in HTTP])
    finally:
        db = SessionLocal()
        try:
            db.query(ApiKey).delete()
            db.commit()
        finally:
            db.close()

    # The wall clock: a request that hangs past the box.
    rid = "ck-hang"
    make_case(rid, CASE, base_items(rid))
    old = (decisions.TIME_BOX, decisions.DEADLINE_MARGIN)
    decisions.TIME_BOX, decisions.DEADLINE_MARGIN = 0.3, 0.1

    def hang(request, body):
        time.sleep(1.5)
        return answer_all()(request, body)

    DECIDE["fn"] = hang
    REQUESTS.clear()
    started = time.monotonic()
    try:
        with Settings(intake_check_mode="decision_then_lite"):
            recheck.run_for_intake(rid, AiActor.cli())
    finally:
        decisions.TIME_BOX, decisions.DEADLINE_MARGIN = old
    took = time.monotonic() - started
    look = saved(rid)["second_look"]
    check(took < 1.4 and look["check"]["by"] == "lite", "a request past the time box is given up on the wall clock",
          round(took, 2))


# ---------------------------------------------------------------- lite


def lite() -> None:
    rid = "ck-lite"
    items = base_items(rid)
    # As the evidence-data branch settles a zone find: its reason from its evidence.
    items[0]["reason"] = "lens cracked"
    items[1]["evidence"] = [{"photo": f"@{rid}-2", "box": [100, 100, 300, 300], "box_kind": "ai", "by": "zone:rear"}]
    make_case(rid, CASE, items)

    def answer(kwargs, labels):
        return {"findings": [
            # The reflector: damaged, boxed on P04 (its evidence there has no box).
            {"ref": "F1", "damaged": "yes", "photo_reference": "P4", "reason": "lens cracked near plate SBA1234K",
             "box_2d": [400, 380, 520, 610]},
            # The lock: damaged, boxed on P03 -- where it has a box already.
            {"ref": "F2", "damaged": "yes", "photo_reference": "P03", "reason": "latch bent", "box_2d": [10, 10, 90, 90]},
            # The vent: not damaged, a box all the same (ignored).
            {"ref": "F3", "damaged": "no", "photo_reference": "P05", "reason": "looks intact", "box_2d": [10, 10, 90, 90]},
            {"ref": "F9", "damaged": "yes"}, {"ref": "F1", "damaged": "no"}, {"ref": "F2", "damaged": "maybe"},
        ]}

    HANDLERS["check"] = answer
    with Settings(intake_check_mode="lite", gemini_check_chain="lite-x,lite-y"):
        recheck.run_for_intake(rid, AiActor.cli())
    check(not HTTP and len(REQUESTS) == 1, "lite: one call, no Decisions API request")
    sent = REQUESTS[0]
    check(sent["model"] == "lite-x" and sent.get("store") is False and "generation_config" not in sent,
          "on gemini_check_chain, with store=False and, unset, no thinking level", sorted(sent))
    schema = sent["response_format"]["schema"]["properties"]["findings"]["items"]
    check(schema["properties"]["ref"]["enum"] == ["F1", "F2", "F3"]
          and schema["properties"]["photo_reference"]["enum"] == ["P03", "P04", "P05"]
          and schema["properties"]["damaged"]["enum"] == ["yes", "no", "unsure"] and schema["required"] == ["ref", "damaged"]
          and "minItems" not in json.dumps(sent["response_format"]["schema"]),
          "the schema: refs, the photos' labels, yes/no/unsure; ref and damaged required; no minItems", schema)
    text = sent["input"][0]["text"]
    check("F1: the left rear bumper reflector -- photos P04, P03" in text and "AI" not in text
          and _labels(sent["input"]) == ["P03", "P04", "P05"]
          and all("resolution" not in p for p in sent["input"] if p["type"] == "image"),
          "the parts with their photos by label, then the car photos, none of them the log card", text)
    check("Some of these parts may be undamaged" in sent["system_instruction"], "a neutral prompt")
    look = saved(rid)["second_look"]
    by_key = {it["key"]: it for it in look["items"]}
    reflector = by_key["Left Rear Bumper Reflector"]
    check(reflector["check"] == {"verdict": "yes", "p": None, "by": "lite", "model": "lite-x",
                                 "reason": "lens cracked near plate"},
          "a lite verdict: no p, the model that answered, the reason with the plate scrubbed", reflector["check"])
    entry = reflector["evidence"][0]
    w, h = Image.open(ai_input_path(str(Path(_tmp, "photos", rid, "IMG_03.jpg")))).size
    check(entry["photo"] == f"@{rid}-3" and entry["box"] == [400, 380, 520, 610] and entry["by"] == "check"
          and entry["box_kind"] == "ai" and (entry["img_w"], entry["img_h"]) == (w, h) and len(reflector["evidence"]) == 2
          and reflector["evidence"][1]["photo"] == f"@{rid}-2",
          "its box: evidence by \"check\" in place of that photo's boxless entry, on the preview's frame", reflector["evidence"])
    check(reflector["reason"] == "lens cracked" and by_key["Boot Lid Lock"]["reason"] == "latch bent",
          "the check's reason goes on a suggestion that had none; the reflector keeps its read's")
    lock = by_key["Boot Lid Lock"]
    check(lock["evidence"] == items[1]["evidence"] or lock["evidence"][0]["box"] == [100, 100, 300, 300],
          "a photo with a box already keeps it", lock["evidence"])
    check(by_key["Rear air vent"]["check"]["verdict"] == "no" and by_key["Rear air vent"]["evidence"] == []
          and by_key["Rear air vent"].get("reason") is None,
          "a \"no\": no box, and its reason stays on the check alone", by_key["Rear air vent"])
    check(look["check"]["by"] == "lite" and look["check"]["model"] == "lite-x" and look["check"]["note"] == "Re-checked 3 of 4 suggestions.",
          "the CheckRun: by lite, its model", look["check"])
    check(keys_of(saved(rid))[-1] == "Rear air vent", "the \"no\" sorts last")

    # Its thinking level; the second model when the first answers badly.
    rid = "ck-lite-2"
    make_case(rid, CASE, base_items(rid))
    REQUESTS.clear()
    answers = iter(["{not json", json.dumps({"findings": [{"ref": "F2", "damaged": "unsure"}]})])
    HANDLERS["check"] = lambda kwargs, labels: next(answers)
    with Settings(intake_check_mode="lite", gemini_check_chain="lite-x,lite-y", gemini_thinking_level_check="low"):
        recheck.run_for_intake(rid, AiActor.cli())
    check([r["model"] for r in REQUESTS] == ["lite-x", "lite-y"]
          and all(r.get("generation_config") == {"thinking_level": "low"} for r in REQUESTS),
          "gemini_thinking_level_check on its tries; bad JSON refused, the next model asked",
          [(r["model"], r.get("generation_config")) for r in REQUESTS])
    look = saved(rid)["second_look"]
    check(look["check"]["model"] == "lite-y" and {it["key"]: (it.get("check") or {}).get("verdict") for it in look["items"]}
          == {"Left Rear Bumper Reflector": None, "Boot Lid Lock": "unsure", "Rear End Panel": None, "Rear air vent": None},
          "the answer of the model that answered", look["check"])

    # parse_lite on its own.
    try:
        recheck.parse_lite("{\"items\": []}", ["F1"], ["P01"])
        check(False, "an answer without findings is refused")
    except ValueError:
        check(True, "an answer without findings is refused (the next model is asked)")
    got = recheck.parse_lite(json.dumps({"findings": [{"ref": "F1", "damaged": "yes", "photo_reference": "P99",
                                                       "box_2d": [1, 2, 3, 4], "reason": "  dent   VIN JTDKB20U123456789 "}]}),
                             ["F1"], ["P01"])
    check(got == {"F1": {"damaged": "yes", "photo": None, "reason": "dent VIN", "box": None}},
          "a photo not sent: no photo, no box; the reason one line, the VIN gone", got)
    check(recheck.clean_box([5, 5, 1015, 1010]) is None and recheck.clean_box([-10, 0, 500, 500]) == [0, 0, 500, 500]
          and recheck.clean_box([500, 0, 400, 200]) is None and recheck.clean_box([0, 0, 5, 500]) is None
          and recheck.clean_box([10, 10, 300, 300], raw_original=True) is None,
          "boxes cleaned (section 4.6): clamped, inverted / thin / whole-photo / raw-original dropped")
    check(recheck.with_check_box([{"photo": "@a", "box": [1, 1, 50, 50]}], {"photo": "@a", "box": [5, 5, 90, 90]})
          == [{"photo": "@a", "box": [1, 1, 50, 50]}]
          and len(recheck.with_check_box([{"photo": f"@{i}"} for i in range(4)], {"photo": "@x", "box": [1, 1, 50, 50]})) == 4,
          "a check box never replaces a box, nor makes a fifth entry")


# ---------------------------------------------------------------- order


def order() -> None:
    def it(key, verdict=None, p=None, confidence="Medium"):
        return {"key": key, "confidence": confidence,
                "check": {"verdict": verdict, "p": p, "by": "decision"} if verdict else None}

    items = [it("none-low", confidence="Low"), it("no-.25", "no", 0.25), it("yes-.8", "yes", 0.8),
             it("unsure-high", "unsure", 0.5, "High"), it("no-.05", "no", 0.05), it("yes-.95", "yes", 0.95),
             it("yes-lite", "yes"), it("none-high", confidence="High"), it("no-lite", "no")]
    got = [x["key"] for x in recheck.sort_items(items)]
    check(got == ["yes-.95", "yes-.8", "yes-lite", "unsure-high", "none-high", "none-low", "no-.25", "no-.05", "no-lite"],
          "yes, then unsure / unchecked by confidence, then no -- p high to low throughout; no p after p", got)


# ---------------------------------------------------------------- the whole intake


def b_draft(kwargs, labels):
    return {"description": "Hit at the rear.", "category": ["Vehicle Collision or Damage"],
            "damage_summary": [{"part": "Rear Bumper", "photo_reference": "P03", "ai_confidence": "High"}],
            "vehicle_info": {"make": "Gammamotors", "model": "Vezel"}}


def b_zone(kwargs, labels):
    return {"zone_side_seen": "rear", "checked_intact": [], "damage": [
        {"photo_reference": "P03", "part": "Rear Bumper", "ai_confidence": "High"},
        {"photo_reference": "P04", "part": "Boot Lid Lock", "ai_confidence": "Medium"}]}


def whole_intake() -> None:
    rid = "ck-intake"
    folder = Path(_tmp, "photos", rid)
    folder.mkdir(parents=True)
    db = SessionLocal()
    try:
        db.add(Report(id=rid, status="intake", channel="dashboard", reporter_chat_id="test",
                      data={"description": "", "category": [], "damage_summary": []}))
        db.add(IntakeSession(report_id=rid, stage="reading", reporter_notes="Hit from behind."))
        for i, (kind, area, part, damage) in enumerate(CASE[:5]):
            path = folder / f"IMG_{i:02d}.jpg"
            path.write_bytes(jpeg(i))
            generate_preview(str(path))
            db.add(IntakePhoto(id=f"{rid}-{i}", report_id=rid, position=i, original_name=path.name, path=str(path),
                               sha256=f"{rid}{i}", kind=kind, area=area, part=part, shows_damage=damage, quality="good"))
        db.commit()
    finally:
        db.close()
    HANDLERS.update(draft=b_draft, zone=b_zone)
    DECIDE["fn"] = answer_all(lambda name, words: 0.83)
    with Settings(intake_read_mode="zones", intake_check_mode="decision"):
        pipeline._read(rid, AiActor.cli())
    data = saved(rid)
    look = data["second_look"]
    check([it["part"] for it in data["damage_summary"]] == ["Rear Bumper"], "the list: the overall reading's")
    check([it["key"] for it in look["items"]] == ["Boot Lid Lock"] and look["items"][0]["check"]["verdict"] == "yes"
          and look["check"]["status"] == "ok",
          "the zone-only find, stored by the second look, re-checked right after", look)
    body = json.loads(HTTP[-1].content)
    check(list(body["questions"]) == ["F1"] and "boot lid lock" in body["questions"]["F1"]["instructions"]
          and _labels(body["input"]) == ["P04"], "asked about on its own photo", body["questions"])


def main() -> int:
    init_db()
    for name, fn in (
        ("off, and nothing to re-check", mode_off),
        ("the Decisions API: the request, the verdicts, the order, the run log", decision_request),
        ("more than 12 photos", many_photos),
        ("failures: to the lite re-check, or the end", failures),
        ("the lite re-check", lite),
        ("the order", order),
        ("the whole intake: the re-check after the second look", whole_intake),
    ):
        run_section(name, fn)
    print(f"\ntest_check: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAILED [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
