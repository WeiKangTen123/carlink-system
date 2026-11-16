"""Contract 9's base (plans/2026-11-15-evidence/contract9.md, Step 1) --
offline, Gemini is a fake (and the real SDK on a mock transport).

Covers:
  - 4.1: the evidence models left out of the drafting schema, which stays
    byte for byte as it was; old drafts loading with them;
  - quick fixes: store=False on every call by default and today's request
    without it (both ways, also through the real SDK); the zone calls on
    their own light chain with the reading's thinking level and never its
    tools; the sort chain without the strong model; second_look._store
    rebuilding the suggestions from the zone reads' finds with the second
    look off (key, cap, dismissed, decisions, reads); the sweep of old
    ai_derived files; the benchmark's --zone-models / --store-on;
  - option B (intake_zone_only "suggest"): single mode unchanged whatever
    it says; a part only zone reads found never reaching the damage list
    but the suggestions, with the second look off; the agreement rule and
    the side resolver on the list only; a dismissed suggestion staying out
    after a re-read; the catalogue's auto look-up seeing the list only;
    "list" as contract 8's runs; the benchmark scoring the suggestions
    with the second look off;
  - scaffolding: section 6's settings and their checks; the re-check's
    place right after the second look, doing nothing while off; the
    dashboard's shared fixture loading through the draft model.

    python scripts/test_c9_base.py

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="c9-base-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake-c9-company-key"
# The suggestions here are the zone reads'; the second look is its own test.
os.environ["SECOND_LOOK_ENABLED"] = "false"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"
for name in ("GEMINI_THINKING_LEVEL_READ", "GEMINI_THINKING_LEVEL_SORT", "GEMINI_READ_TOOLS", "GEMINI_MODEL_CHAIN",
             "GEMINI_TRIAGE_MODEL_CHAIN", "GEMINI_ZONE_CHAIN", "GEMINI_STORE_OFF", "INTAKE_READ_MODE"):
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
    return "sort" if "photos" in props else "zone" if "zone_side_seen" in props else (
        "draft" if "damage_summary" in props else "other")


def _empty_zone(kwargs, labels):
    return {"zone_side_seen": "unclear", "damage": [], "checked_intact": []}


def _sorted(kwargs, labels):
    return {"photos": [{"label": label, "kind": "damage", "area": "rear", "quality": "good", "shows_damage": True,
                        "part": None, "note": None} for label in labels]}


# Each kind of call's answer: a function of (create kwargs, photo labels)
# returning a dict (sent as JSON) or a str (as it is), or raising.
HANDLERS: dict = {}


def reset_handlers() -> None:
    HANDLERS.clear()
    HANDLERS.update(draft=lambda kwargs, labels: {"description": "x", "damage_summary": []}, zone=_empty_zone,
                    sort=_sorted, other=lambda kwargs, labels: {"ok": True})


reset_handlers()


class FakeInteractions:
    def create(self, **kwargs):
        with _requests_lock:
            REQUESTS.append(kwargs)
        out = HANDLERS[kind_of(kwargs)](kwargs, _labels(kwargs["input"]))
        return SimpleNamespace(output_text=out if isinstance(out, str) else json.dumps(out),
                               usage=SimpleNamespace(total_input_tokens=1000, total_output_tokens=100))


def _fake_client(key=None):
    return SimpleNamespace(interactions=FakeInteractions())


structured.get_client = _fake_client
structured.acquire = lambda key=None: 0.0
# The busy retries (a model, then the whole chain) without real waiting.
structured.OVERLOADED_RETRY_SECONDS = 0.0
structured.CHAIN_BUSY_RETRY_SECONDS = 0.0

import httpx  # noqa: E402
from google.genai import types as genai_types  # noqa: E402
from PIL import Image  # noqa: E402

from app.ai import client as ai_client  # noqa: E402
from app.ai import keys, second_look, triage  # noqa: E402
from app.ai.extraction import _response_schema  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import cleanup, pipeline, zones  # noqa: E402
from app.parts.prefill import item_key  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import IntakePhoto, IntakeSession, Report  # noqa: E402
from app.reports.schema import (  # noqa: E402
    MAX_DECISIONS,
    DamageSummaryItem,
    SecondLook,
    SecurityIncidentDraft,
)

STRONG = "gemini-3-flash-preview"
LITE_A, LITE_B = "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"
# The keyword arguments call_structured sent before contract 9.
BEFORE = {"model", "system_instruction", "input", "response_format", "timeout"}
# Taken on feat/c9-base before contract 9's fields (d8dbabb, the four
# merges): json.dumps of the schema the drafting call (and the Telegram
# bot) sends, and of the whole draft model's.
GOLDEN_DRAFT_SCHEMA = "7ccb5ce425dc1fe68971672f41992753e1431b535edf941c45aa648e3d6d18d0"
GOLDEN_MODEL_SCHEMA = "b94a425945ec99d7a04605f0f48f57d920662de5ecb49491b7b38eb60dc8c1a7"
C9_ITEM_FIELDS = ("reason", "evidence", "check", "added_from")

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


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj).encode()).hexdigest()


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


def make_case(rid: str, case: list[tuple], *, data: dict | None = None, ai_draft: dict | None = None) -> None:
    """A draft straight in the database: photos as (kind, area, part, shows
    damage), already sorted (kind None: not yet)."""
    folder = Path(_tmp, "photos", rid)
    folder.mkdir(parents=True)
    db = SessionLocal()
    try:
        db.add(Report(id=rid, status="intake", channel="dashboard", reporter_chat_id="test",
                      data=data or {"description": "", "category": [], "damage_summary": []}))
        db.add(IntakeSession(report_id=rid, stage="reading", reporter_notes="Hit from behind.", ai_draft=ai_draft))
        for i, (kind, area, part, damage) in enumerate(case):
            path = folder / f"IMG_{i:02d}.jpg"
            path.write_bytes(jpeg(i))
            db.add(IntakePhoto(id=f"{rid}-{i}", report_id=rid, position=i, original_name=path.name, path=str(path),
                               sha256=f"{rid}{i}", kind=kind, area=area, part=part, shows_damage=damage,
                               quality="good" if kind else None))
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


def call(purpose: str, models=("model-a",), inputs=None):
    return structured.call_structured(
        purpose=purpose, system_instruction="test", input_parts=inputs or [{"type": "text", "text": "hi"}],
        schema={"type": "object"}, parse=json.loads, models=list(models), actor=AiActor.cli(), timeout=5.0,
    )


# A rear-end case: two rear damage photos, a left one, a log card.
CASE = [("log_card", None, None, False), ("overview", "rear", None, False), ("damage", "rear", "Rear Bumper", True),
        ("damage", "rear", "Boot Lid", True), ("damage", "left", "Left Rear Door", True)]


# ---------------------------------------------------------------- 4.1 models


def schema_models() -> None:
    check(sha(_response_schema()) == GOLDEN_DRAFT_SCHEMA, "the drafting schema is byte for byte as before (golden)",
          sha(_response_schema()))
    check(sha(SecurityIncidentDraft.model_json_schema()) == GOLDEN_MODEL_SCHEMA,
          "the draft model's whole JSON schema too: nothing new in it")
    props = DamageSummaryItem.model_json_schema()["properties"]
    check(not set(C9_ITEM_FIELDS) & set(props), "reason / evidence / check / added_from: not in the item's schema")

    old = DamageSummaryItem.model_validate({"part": "Rear Bumper", "photo_reference": "P03", "bbox_2d": [1, 2, 3, 4]})
    check((old.reason, old.evidence, old.check, old.added_from) == (None, [], None, None),
          "an item saved before them loads, empty")
    item = {"part": "Boot Lid", "photo_reference": "@p1", "reason": "AI words",
            "evidence": [{"photo": "@p1", "box": [10, 20, 300, 400], "box_kind": "ai", "by": "zone:rear",
                          "reason": "dent at the edge", "confidence": "Medium", "img_w": 1600, "img_h": 1200},
                         {"photo": "@p2"}],
            "check": {"verdict": "yes", "p": 0.91, "by": "decision", "model": "gemini-decision-1"},
            "added_from": "suggestion"}
    draft = SecurityIncidentDraft.model_validate({"description": "x", "damage_summary": [item]})
    got = draft.model_dump()["damage_summary"][0]
    check(got["evidence"][0]["box"] == [10, 20, 300, 400] and got["evidence"][1] == {
        "photo": "@p2", "box": None, "box_kind": None, "by": "overall", "reason": None, "confidence": None,
        "img_w": None, "img_h": None} and got["check"]["p"] == 0.91 and got["added_from"] == "suggestion"
          and got["reason"] == "AI words", "the wizard's autosave (through the draft model) keeps them", got)
    odd = DamageSummaryItem.model_validate({"part": "Boot Lid", "evidence": [{"photo": "@p1", "box": [900, 0, 100, 5]}]})
    check(odd.evidence[0].box == [900, 0, 100, 5], "lenient: an odd box loads as it is (cleaned where it is made)")

    look = SecondLook.model_validate({"status": "ok", "items": [{"part": "Rear Bumper", "source": "kit"}],
                                      "dismissed": ["Boot Lid Lock"]})
    dumped = look.model_dump()
    check(dumped["items"][0]["key"] == "" and dumped["items"][0]["evidence"] == [] and dumped["reads"] == []
          and dumped["decisions"] == [] and dumped["check"] is None and dumped["dismissed"] == ["Boot Lid Lock"],
          "an old second look (no key, reads, decisions, check) loads with them empty")
    many = SecondLook.model_validate({"decisions": [
        {"key": f"k{i}", "part": "Boot Lid", "action": "dismissed", "at": f"2026-11-15T00:00:{i % 60:02d}Z"}
        for i in range(MAX_DECISIONS + 5)]})
    check(len(many.decisions) == MAX_DECISIONS and many.decisions[0].key == "k5",
          f"decisions keep the newest {MAX_DECISIONS}, the oldest dropped, never refused")
    full = SecondLook.model_validate({"check": {"status": "ok", "by": "decision", "calls": 1}, "reads": ["overall"]})
    check(full.check.calls == 1 and full.reads == ["overall"], "check (CheckRun) and reads")


# ---------------------------------------------------------------- store=False


_SDK_BODIES: list[dict] = []
_SDK_RAW: list[bytes] = []


def _sdk_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content or b"{}")
    _SDK_BODIES.append(body)
    _SDK_RAW.append(bytes(request.content))
    return httpx.Response(200, json={
        "id": "int-1", "status": "completed", "model": body.get("model", ""),
        "usage": {"total_input_tokens": 10, "total_output_tokens": 2},
        "steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps({"ok": True})}]}]})


def store_off() -> None:
    check(settings.gemini_store_off is True, "gemini_store_off is on by default")
    sent = {}
    for off in (False, True):
        REQUESTS.clear()
        with Settings(gemini_store_off=off):
            call("draft")
        sent[off] = dict(REQUESTS[0])
    check(set(sent[False]) == BEFORE, "off: the keyword arguments of before, no store", sorted(sent[False]))
    check(sent[True] == {**sent[False], "store": False}, "on (the default): the same request plus store=False, nothing else")
    for purpose in ("photo sort", "draft_zone", "second look", "annex suggestions"):
        REQUESTS.clear()
        call(purpose)
        check(REQUESTS[0].get("store") is False, f"{purpose}: store=False too (every Interactions call)")
    REQUESTS.clear()
    HANDLERS["other"] = lambda kwargs, labels: "not json"
    try:
        call("draft", models=("model-a", "model-b"))
    except Exception:
        pass
    check(len(REQUESTS) == 2 and all(r.get("store") is False for r in REQUESTS), "on every model try of the chain")

    # The real SDK on a mock transport: what goes over the wire.
    saved_client = (ai_client._HTTP_OPTIONS, structured.get_client)
    ai_client._HTTP_OPTIONS = genai_types.HttpOptions(
        retry_options=saved_client[0].retry_options, httpx_client=httpx.Client(transport=httpx.MockTransport(_sdk_handler)))
    ai_client.reset_client_cache()
    structured.get_client = ai_client.get_client
    try:
        image = Path(_tmp, "sdk.jpg")
        image.write_bytes(jpeg(3))
        inputs = [{"type": "text", "text": "Photo P01:"}, structured.image_part(str(image))]
        bodies = {}
        for off in (False, True):
            _SDK_BODIES.clear()
            _SDK_RAW.clear()
            with Settings(gemini_store_off=off):
                got = call("draft", inputs=inputs)
            bodies[off] = _SDK_BODIES[0]
            if not off:
                raw_off = _SDK_RAW[0]
                # The same call made directly with the keyword arguments of before.
                direct = ai_client.get_client(keys.company_keys()[0])
                direct.interactions.create(model="model-a", system_instruction="test", input=inputs,
                                           response_format={"type": "text", "mime_type": "application/json",
                                                            "schema": {"type": "object"}}, timeout=5.0)
                check(_SDK_RAW[1] == raw_off, "off, through the SDK: byte for byte the request of before")
        check(got == {"ok": True}, "the SDK takes store and the answer parses")
        check("store" not in bodies[False] and bodies[True].get("store") is False,
              "on the wire: \"store\": false when on, no store field when off")
        check({k: v for k, v in bodies[True].items() if k != "store"} == bodies[False],
              "and nothing else differs")
    finally:
        ai_client._HTTP_OPTIONS, structured.get_client = saved_client
        ai_client.reset_client_cache()


# ---------------------------------------------------------------- the zone chain


def zone_chain() -> None:
    check(zones.zone_chain() == [LITE_A, LITE_B], "gemini_zone_chain: the two lites by default", zones.zone_chain())
    check(structured.request_settings("draft_zone", LITE_A) == {"generation_config": {"thinking_level": "high"}},
          "a zone call takes the reading's thinking level for its model (lites: high)",
          structured.request_settings("draft_zone", LITE_A))
    with Settings(gemini_read_tools="code_execution", gemini_thinking_level_read=f"low,{LITE_B}=medium"):
        check(structured.request_settings("draft_zone", LITE_A) == {"generation_config": {"thinking_level": "low"}}
              and structured.request_settings("draft_zone", LITE_B) == {"generation_config": {"thinking_level": "medium"}},
              "per model, as the reading's")
        check("tools" not in structured.request_settings("draft_zone", LITE_A)
              and structured.request_settings("draft", LITE_A).get("tools") == [{"type": "code_execution"}],
              "never the reading's tools (the reading itself keeps them)")

    def zone_answer(kwargs, labels):
        if kwargs["model"] == LITE_A:
            raise RuntimeError("503 service_unavailable: high demand")
        return _empty_zone(kwargs, labels)

    REQUESTS.clear()
    HANDLERS["zone"] = zone_answer
    with Settings(gemini_model_chain=f"{STRONG},{LITE_A}", intake_read_mode="zones",
                  gemini_read_tools="code_execution"):
        make_case("c9-zchain", CASE)
        pipeline._read("c9-zchain", AiActor.cli())
    zone_calls = [r for r in REQUESTS if kind_of(r) == "zone"]
    drafts = [r for r in REQUESTS if kind_of(r) == "draft"]
    check(drafts and drafts[0]["model"] == STRONG and drafts[0].get("tools") == [{"type": "code_execution"}],
          "the main read: the reading chain's strong model, with its tools")
    check(zone_calls and {r["model"] for r in zone_calls} == {LITE_A, LITE_B},
          "the zone calls: 3.5-lite busy, then 3.1-lite -- the zone chain", sorted({r["model"] for r in zone_calls}))
    check(all(r["model"] != STRONG for r in zone_calls) and all("tools" not in r for r in zone_calls),
          "never the strong model, never tools, even with every lite busy")


# ---------------------------------------------------------------- the sort chain


def sort_chain() -> None:
    chain = triage._triage_chain()
    check(chain == [LITE_A, LITE_B] and STRONG not in chain, "the sort chain: the two lites, no strong model", chain)
    image = Path(_tmp, "sort.jpg")
    image.write_bytes(jpeg(5))
    REQUESTS.clear()
    HANDLERS["sort"] = lambda kwargs, labels: (_ for _ in ()).throw(RuntimeError("503 service_unavailable: busy"))
    try:
        triage.triage_photos([("P01", str(image))], actor=AiActor.cli())
        failed = False
    except Exception:
        failed = True
    check(failed and REQUESTS and {r["model"] for r in REQUESTS} == {LITE_A, LITE_B},
          "both lites busy: the batch fails (Retry), and no strong call is spent", sorted({r["model"] for r in REQUESTS}))


# ---------------------------------------------------------------- second_look._store


def zone_suggestion(part: str, confidence: str = "Medium", **extra) -> dict:
    return {"part": part, "photo_reference": "@x", "condition_tokens": [], "condition": None, "damage_type": None,
            "confidence": confidence, "source": "zone", "found_by": ["zone:rear"], "seen_in": ["@x"], **extra}


def store_rebuild() -> None:
    from app.reports.taxonomy import CANONICAL_PARTS, UNDETERMINED_SUFFIX

    # 20 parts nothing lists, dismisses or twins, the last the surest.
    names = ["Left Tail Lamp", "Boot Lid Hinge", "Rear Windscreen"] + [
        p for p in CANONICAL_PARTS if not p.endswith(UNDETERMINED_SUFFIX) and "Bumper" not in p
        and "Tail Lamp" not in p and p not in ("Boot Lid Hinge", "Rear Windscreen", "Other / Not Listed")][:17]
    suggestions = [zone_suggestion(n, "High" if i == len(names) - 1 else "Medium") for i, n in enumerate(names)]
    # The undetermined twin of a sided one: one lamp seen twice.
    suggestions.append(zone_suggestion("Tail Lamp (side undetermined)", "Low"))
    # Two unlisted parts are two; one named like the listed Other is listed.
    suggestions += [zone_suggestion("Other / Not Listed", other_part_name="Rear air vent"),
                    zone_suggestion("Other / Not Listed", other_part_name="Tow hook cover")]
    decision = {"key": "Boot Lid Hinge", "part": "Boot Lid Hinge", "action": "dismissed", "by_id": "u1",
                "by_name": "Surveyor", "at": "2026-11-15T01:02:03Z", "looked": True, "where": "intake"}
    data = {"description": "x", "damage_summary": [
        {"part": "Rear Bumper", "photo_reference": "@x"},
        {"part": "Other / Not Listed", "other_part_name": "Rear air vent"}],
        "vehicle_info": {"make": "Gammamotors", "model": "Vezel"},
        "second_look": {"status": "skipped", "items": [{"part": "Rear Windscreen"}],
                        "dismissed": ["Boot Lid Hinge", "Rear Windscreen"], "decisions": [decision],
                        "check": {"status": "ok", "by": "decision", "calls": 1}}}
    ai_draft = {"damage_summary": data["damage_summary"], "zone_suggestions": suggestions,
                "zone_reads": ["overall", "zone:rear", "zone:left"]}
    make_case("c9-store", CASE, data=data, ai_draft=ai_draft)
    REQUESTS.clear()
    second_look.run_for_intake("c9-store", AiActor.cli())
    stored_data = saved("c9-store")["data"]
    look = stored_data["second_look"]
    # The dismissed ones stay in items after the open ones (split_settled:
    # "Show decided", undo); the open ones are what is suggested.
    items, settled = second_look.split_settled(look["items"], stored_data)
    keys_ = [it["key"] for it in items]
    check(not REQUESTS and look["status"] == "skipped" and look["note"] == second_look.NOTE_OFF,
          "the second look off: no call, its status says so")
    check(items and all(it["source"] == "zone" for it in items), "and the zone reads' suggestions are there all the same")
    check(all(it["key"] == item_key(it) for it in items), "each with its key (prefill.item_key)")
    check(len(items) == second_look.MAX_SUGGESTIONS == 16, "at most 16 (conflict #22)", len(items))
    check(items[0]["part"] == names[-1], "surest first")
    check("Boot Lid Hinge" not in keys_ and "Rear Windscreen" not in keys_, "dismissed keys stay out")
    check([it["key"] for it in settled] == ["Rear Windscreen", "Boot Lid Hinge"] and look["items"] == items + settled,
          "...of the open ones: kept after them, outside the 16 -- the previous read's first, as the person saw it",
          [it["key"] for it in look["items"]])
    check(look["dismissed"] == ["Boot Lid Hinge", "Rear Windscreen"] and look["decisions"] == [decision],
          "dismissed and the decisions are kept")
    check(look["reads"] == ["overall", "zone:rear", "zone:left"], "reads: the reads that answered (ai_draft)")
    check(look["check"] is None, "the re-check's answer about the old items goes")
    check("Tail Lamp (side undetermined)" not in keys_, "an undetermined twin of a sided one: one lamp, the surer kept")
    others = [it for it in items if it["part"] == "Other / Not Listed"]
    check([it.get("other_part_name") for it in others] in ([], ["Tow hook cover"]),
          "a named Other goes by its name: the listed one's twin stays out", [it.get("other_part_name") for it in others])
    check(all(it["name"] and it["covers"] for it in items), "named in the firm's words, with covers")

    # An old draft: no zone suggestions, no reads; the second look's own items.
    make_case("c9-store-old", CASE, data={"description": "x", "damage_summary": [{"part": "Rear Bumper"}],
                                          "second_look": {"dismissed": ["Boot Lid Lock"]}},
              ai_draft={"damage_summary": [{"part": "Rear Bumper"}]})
    second_look._store("c9-store-old", {"status": "ok", "items": [
        {"part": "Boot Lid Lock", "photo_reference": "@y", "confidence": "High", "source": "kit"},
        {"part": "Rear Bumper Beam", "photo_reference": "@y", "confidence": "High", "source": "nearby"}]})
    look = saved("c9-store-old")["data"]["second_look"]
    check([(it["part"], it["key"]) for it in look["items"]] == [("Rear Bumper Beam", "Rear Bumper Beam"), ("Boot Lid Lock", "Boot Lid Lock")]
          and look["reads"] == [] and look["dismissed"] == ["Boot Lid Lock"],
          "an old draft: the second look's own items, keyed; an old dismissed (part names) respected",
          look["items"])


# ---------------------------------------------------------------- cleanup


def sweep_derived() -> None:
    root = Path(settings.storage_dir) / "ai_derived"
    now = datetime.now(timezone.utc)
    files = {}
    for name, age in (("grid/old.jpg", timedelta(days=8)), ("crops/old.jpg", timedelta(days=30)),
                      ("grid/new.jpg", timedelta(days=1)), ("crops/new.jpg", timedelta(hours=1))):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        stamp = (now - age).timestamp()
        os.utime(path, (stamp, stamp))
        files[name] = path
    out = cleanup.sweep(now=now)
    check(out["ai_derived"] == 2 and not files["grid/old.jpg"].exists() and not files["crops/old.jpg"].exists(),
          f"files unchanged for {cleanup.DRAFT_MAX_IDLE.days} days go", out["ai_derived"])
    check(files["grid/new.jpg"].exists() and files["crops/new.jpg"].exists(), "newer ones stay")
    check(cleanup.sweep(now=now)["ai_derived"] == 0, "nothing more the next time")


# ---------------------------------------------------------------- the benchmark


def bench_flags() -> None:
    import benchmark_intake as bench

    args = bench.parse_args(["--read-mode", "zones", "--zone-models", f" {LITE_B} , {LITE_A} ", "--store-on"])
    check(bench.model_settings(args) == {"intake_read_mode": "zones", "gemini_zone_chain": f"{LITE_B},{LITE_A}",
                                         "gemini_store_off": False},
          "--zone-models and --store-on set their settings", bench.model_settings(args))
    try:
        bench.model_settings(bench.parse_args(["--zone-models", LITE_A]))
        check(False, "--zone-models without zones is refused")
    except SystemExit as exc:
        check("--zone-models" in str(exc.code), "--zone-models without zones stops the run", exc.code)
    plain = bench.ai_settings(settings)
    check("zone_chain" not in plain and "store_off" not in plain, "at the defaults, ai_settings is as before")
    with Settings(intake_read_mode="zones", gemini_store_off=False):
        shown = bench.ai_settings(settings)
    check(shown.get("zone_chain") == f"{LITE_A},{LITE_B}" and shown.get("store_off") is False,
          "while they act, the result file records them", shown)


# ---------------------------------------------------------------- option B

# P01 log card, P02 rear overview, P03 / P04 rear damage, P05 left damage,
# P06 a close-up whose area wasn't clear.
B_CASE = CASE + [("damage", None, None, True)]


def b_draft(kwargs, labels):
    return {"description": "Hit at the rear.", "category": ["Vehicle Collision or Damage"],
            "damage_summary": [
                {"part": "Rear Bumper", "photo_reference": "P03", "ai_confidence": "High", "condition_tokens": ["Cracked"]},
                {"part": "Boot Lid", "photo_reference": "P04", "ai_confidence": "Medium"}],
            "vehicle_info": {"make": "Gammamotors", "model": "Vezel"}}


def b_zone(kwargs, labels):
    if "P06" in labels:
        return {"zone_side_seen": "unclear", "checked_intact": [], "damage": [
            {"photo_reference": "P06", "part": "Wheel (side undetermined)", "ai_confidence": "Medium"}]}
    if "P05" in labels:
        return {"zone_side_seen": "left", "checked_intact": [], "damage": [
            {"photo_reference": "P05", "part": "Rear Door", "ai_confidence": "High", "condition_tokens": ["Dented"]}]}
    return {"zone_side_seen": "rear", "checked_intact": [], "damage": [
        {"photo_reference": "P03", "part": "Rear Bumper", "ai_confidence": "High"},
        {"photo_reference": "P04", "part": "Boot Lid Lock", "ai_confidence": "Medium", "condition_tokens": ["Bent"],
         "severity": "Minor", "remarks": "Replace"}]}


ZONE_ONLY_PARTS = {"Boot Lid Lock", "Left Rear Door", "Wheel (side undetermined)"}


def option_b_single() -> None:
    HANDLERS["draft"] = b_draft
    got, requests = {}, {}
    for mode in ("suggest", "list"):
        REQUESTS.clear()
        with Settings(intake_read_mode="single", intake_zone_only=mode):
            make_case(f"c9-b-single-{mode}", B_CASE)
            pipeline._read(f"c9-b-single-{mode}", AiActor.cli())
        requests[mode] = [json.dumps(r, sort_keys=True, default=str) for r in REQUESTS]
        # The same case under two ids: compared with the id taken out.
        got[mode] = json.loads(json.dumps(saved(f"c9-b-single-{mode}"), default=str).replace(f"c9-b-single-{mode}", "ID"))
    check(requests["suggest"] == requests["list"] and len(requests["suggest"]) == 1,
          "zone mode off: the same requests (one) whatever intake_zone_only says")
    check(got["suggest"] == got["list"], "...and the same saved list, form and AI draft")
    check(not {"zone_suggestions", "zone_reads"} & set(got["suggest"]["ai_draft"]),
          "single mode's AI draft holds nothing new")


def option_b() -> None:
    HANDLERS.update(draft=b_draft, zone=b_zone)
    seen_by_lookup: list = []
    real_lookup = pipeline.catalogue.maybe_auto_lookup

    def lookup(report_id):
        seen_by_lookup.append([it["part"] for it in saved(report_id)["data"]["damage_summary"]])
        return real_lookup(report_id)

    pipeline.catalogue.maybe_auto_lookup = lookup
    REQUESTS.clear()
    try:
        with Settings(intake_read_mode="zones"):
            check(settings.intake_zone_only == "suggest", "intake_zone_only: \"suggest\" by default")
            make_case("c9-b", B_CASE)
            pipeline._read("c9-b", AiActor.cli())
    finally:
        pipeline.catalogue.maybe_auto_lookup = real_lookup
    zone_calls = [r for r in REQUESTS if kind_of(r) == "zone"]
    got = saved("c9-b")
    data, ai_draft = got["data"], got["ai_draft"]
    listed = [it["part"] for it in data["damage_summary"]]
    check(len(zone_calls) == 3, "three zones read (rear, left, close)", len(zone_calls))
    check(listed == ["Rear Bumper", "Boot Lid"] and data["damaged_parts"] == listed,
          "the list: what the overall reading found", listed)
    check(not ZONE_ONLY_PARTS & set(listed + data["damaged_parts"] + [it["part"] for it in ai_draft["damage_summary"]]),
          "a part only zone reads found never reaches damage_summary (form or AI draft)")
    bumper = data["damage_summary"][0]
    check(bumper["found_by"] == ["overall", "zone:rear"] and bumper["ai_confidence"] == "High",
          "a part both found: the zone read's agreement on the list item")
    check(data["damage_summary"][1]["ai_confidence"] == "Low",
          "the agreement rule on the list: found once, on one photo, is Low (Check)")
    sugg = {s["part"]: s for s in ai_draft["zone_suggestions"]}
    check(set(sugg) == ZONE_ONLY_PARTS and all(s["source"] == "zone" for s in sugg.values()),
          "the zone-only finds: ai_draft[\"zone_suggestions\"], source zone", sorted(sugg))
    lock = sugg["Boot Lid Lock"]
    check(lock["key"] == "Boot Lid Lock" and lock["found_by"] == ["zone:rear"] and lock["seen_in"] == ["@c9-b-3"]
          and lock["photo_reference"] == "@c9-b-3" and lock["confidence"] == "Medium" and lock["severity"] == "Minor"
          and lock["remarks"] == "Replace" and lock["condition_tokens"] == ["Bent"],
          "as SecondLookItem dicts: key, the read and photo ('@<photo id>'), the zone's confidence and words", lock)
    check(sugg["Wheel (side undetermined)"]["confidence"] == "Medium",
          "a suggestion keeps the model's confidence and side: no agreement rule, no side resolver")
    check(ai_draft["zone_reads"] == ["overall", "zone:rear", "zone:left", "zone:close"],
          "ai_draft[\"zone_reads\"]: the reads that answered", ai_draft["zone_reads"])
    check("zone_suggestions" not in data and "zone_reads" not in data, "never in Report.data")
    look = data["second_look"]
    keys_ = {it["key"] for it in look["items"]}
    check(look["status"] == "skipped" and keys_ == ZONE_ONLY_PARTS and look["reads"] == ai_draft["zone_reads"],
          "with the second look off, the suggestions are second_look.items after _store", sorted(keys_))
    check(seen_by_lookup == [listed], "the catalogue's auto look-up runs after the commit and sees the list only",
          seen_by_lookup)

    # A person marks one Not damaged; the re-read keeps it out.
    db = SessionLocal()
    try:
        report = db.get(Report, "c9-b")
        look = dict(report.data["second_look"])
        look["dismissed"] = ["Boot Lid Lock"]
        report.data = {**report.data, "second_look": look}
        db.commit()
    finally:
        db.close()
    with Settings(intake_read_mode="zones"):
        pipeline._read("c9-b", AiActor.cli(), kind="redraft")
    data = saved("c9-b")["data"]
    open_items, settled = second_look.split_settled(data["second_look"]["items"], data)
    keys_ = {it["key"] for it in open_items}
    check(keys_ == ZONE_ONLY_PARTS - {"Boot Lid Lock"} and data["second_look"]["dismissed"] == ["Boot Lid Lock"]
          and [it["key"] for it in settled] == ["Boot Lid Lock"],
          "a dismissed suggestion isn't suggested again after a re-read (kept as decided); dismissed is kept", sorted(keys_))
    check([it["part"] for it in data["damage_summary"]] == ["Rear Bumper", "Boot Lid"], "and the list is still the list")

    # "list": contract 8's runs -- every find in the list.
    with Settings(intake_read_mode="zones", intake_zone_only="list"):
        make_case("c9-b-list", B_CASE)
        pipeline._read("c9-b-list", AiActor.cli())
    got = saved("c9-b-list")
    listed = {it["part"] for it in got["data"]["damage_summary"]}
    check(ZONE_ONLY_PARTS - {"Wheel (side undetermined)"} <= listed and "zone_suggestions" not in got["ai_draft"]
          and got["ai_draft"]["zone_reads"] == ["overall", "zone:rear", "zone:left", "zone:close"]
          and not got["data"]["second_look"]["items"],
          "\"list\": the zone-only finds in the list, no suggestions", sorted(listed))


def option_b_bench() -> None:
    import benchmark_intake as bench

    args = bench.parse_args(["--read-mode", "zones", "--zone-only", "list"])
    check(bench.model_settings(args) == {"intake_read_mode": "zones", "intake_zone_only": "list"},
          "--zone-only sets intake_zone_only")
    try:
        bench.model_settings(bench.parse_args(["--zone-only", "suggest"]))
        check(False, "--zone-only without zones is refused")
    except SystemExit as exc:
        check("--zone-only" in str(exc.code), "--zone-only without zones stops the run", exc.code)
    with Settings(intake_read_mode="zones"):
        check(bench.ai_settings(settings).get("intake_zone_only") == "suggest", "recorded in zone mode")

    # A whole case through the benchmark's run_case, the real pipeline on the
    # fake Gemini, the second look off (--no-second-look): the suggestions
    # are the zone reads', scored as if all ticked.
    folder = Path(_tmp, "bench-cases", "case_b")
    folder.mkdir(parents=True)
    for i in range(3):
        Image.new("RGB", (64, 48), ("red", "green", "blue")[i]).save(folder / f"p{i + 1}.jpg", "JPEG")

    def draft(kwargs, labels):
        return {"description": "Rear-ended.", "damage_summary": [
            {"part": "Rear Bumper", "photo_reference": "P01", "ai_confidence": "High"}]}

    def zone(kwargs, labels):
        return {"zone_side_seen": "rear", "checked_intact": [], "damage": [
            {"photo_reference": "P01", "part": "Rear Bumper", "ai_confidence": "High"},
            {"photo_reference": "P02", "part": "Boot Lid Lock", "ai_confidence": "Medium"}]}

    HANDLERS.update(draft=draft, zone=zone)
    vehicle = {"make": "Gammamotors", "model": "Vezel", "plate_number": "TEST-B", "color": "White",
               "vin": "TESTVIN0000000002", "engine_number": "TESTENG2", "date_of_registration": "2017-01-12"}
    case = {"name": "TEST CASE B", "folder": "case_b", "vehicle": vehicle, "parts": [
        {"part": "Rear Bumper"}, {"part": "Boot Lid Lock"}, {"part": "Left Tail Lamp"}]}
    with Settings(intake_read_mode="zones", second_look_enabled=False):
        out = bench.run_case(case, folder.parent)
    check("error" not in out, "the run answered", out.get("error"))
    check(out["drafted_parts"] == ["Rear Bumper"] and out["parts"]["found"] == 1,
          "the list scores the overall reading's parts", out.get("drafted_parts"))
    check(out.get("second_look", {}).get("suggested") == ["Boot Lid Lock"] and out["second_look"]["calls"] == 0,
          "the suggestions are the zone-only find; no second-look call", out.get("second_look"))
    ls = out.get("parts_with_second_look") or {}
    check((ls.get("found"), ls.get("added"), ls.get("extra")) == (2, 1, 0),
          "score_second_look scores them (found 2, added 1, extra 0)", ls)


# ---------------------------------------------------------------- scaffolding


def settings_block() -> None:
    from pydantic import ValidationError

    from app import config

    s = config.Settings()
    want = {"gemini_zone_chain": f"{LITE_A},{LITE_B}", "gemini_triage_model_chain": f"{LITE_A},{LITE_B}",
            "gemini_store_off": True, "intake_zone_only": "suggest", "intake_read_mode": "single",
            "intake_zone_evidence": "off", "intake_sort_damage_box": False, "intake_check_mode": "off",
            "gemini_decision_model": "gemini-decision-1", "gemini_check_chain": f"{LITE_A},{LITE_B}",
            "gemini_thinking_level_check": None, "intake_check_resolution": "medium", "intake_check_yes": 0.7,
            "intake_check_no": 0.3, "intake_read_crops": False, "second_look_enabled": False}
    got = {k: getattr(s, k) for k in want}
    check(got == want, "section 6's settings and defaults", {k: v for k, v in got.items() if want[k] != v})
    check(STRONG not in s.gemini_zone_chain + s.gemini_triage_model_chain + s.gemini_check_chain,
          "no light chain holds the strong model")
    written = config.Settings(intake_zone_only=" LIST ", intake_zone_evidence="Reason_Box", intake_check_mode="",
                              gemini_check_chain=" a ,b,a ", gemini_thinking_level_check=" LOW ",
                              intake_check_resolution="", intake_check_yes="0.8", intake_check_no=0)
    check((written.intake_zone_only, written.intake_zone_evidence, written.intake_check_mode, written.gemini_check_chain,
           written.gemini_thinking_level_check, written.intake_check_resolution, written.intake_check_yes,
           written.intake_check_no) == ("list", "reason_box", "off", "a,b", "low", "medium", 0.8, 0.0),
          "each written the one way; blank is the default")
    for field, bad in (("intake_zone_only", "both"), ("intake_zone_evidence", "box"), ("intake_check_mode", "on"),
                       ("gemini_zone_chain", " , "), ("gemini_check_chain", ""), ("gemini_decision_model", " "),
                       ("gemini_thinking_level_check", "extreme"), ("intake_check_resolution", "4k"),
                       ("intake_check_yes", 1.2), ("intake_check_no", -0.1)):
        try:
            config.Settings(**{field: bad})
            check(False, f"{field}={bad!r} stops the app at start-up")
        except ValidationError:
            check(True, f"{field}={bad!r} stops the app at start-up")
    try:
        config.Settings(intake_check_yes=0.4, intake_check_no=0.6)
        check(False, "the cut-offs the wrong way round stop the app")
    except ValidationError:
        check(True, "the cut-offs the wrong way round stop the app")


def check_step() -> None:
    from app.ai import check as recheck

    order: list[str] = []
    real = (pipeline.second_look.run_for_intake, pipeline.check.run_for_intake)
    pipeline.second_look.run_for_intake = lambda rid, actor: (order.append("second_look"), real[0](rid, actor))
    pipeline.check.run_for_intake = lambda rid, actor: (order.append("check"), real[1](rid, actor))
    try:
        make_case("c9-check", CASE)
        REQUESTS.clear()
        pipeline._read("c9-check", AiActor.cli())
    finally:
        pipeline.second_look.run_for_intake, pipeline.check.run_for_intake = real
    check(order == ["second_look", "check"], "pipeline._read: the re-check right after the second look", order)
    before = saved("c9-check")
    REQUESTS.clear()
    for mode in ("off", "decision"):
        with Settings(intake_check_mode=mode):
            check(recheck.run_for_intake("c9-check", AiActor.cli()) is None and not REQUESTS
                  and saved("c9-check") == before, f"mode {mode}: no call, nothing written (Step 1's stub)")


def fixture() -> None:
    path = ROOT.parent / "dashboard" / "scripts" / "fixtures" / "evidence-sample.json"
    if not path.exists():
        check(True, "the dashboard isn't beside this checkout: skipped")
        return
    doc = json.loads(path.read_text(encoding="utf-8"))
    draft = SecurityIncidentDraft.model_validate(doc["data"])
    look = draft.second_look
    check(len(doc["photos"]) == 12 and sum(1 for p in doc["photos"] if p.get("deleted")) == 1
          and any(p["h"] > p["w"] for p in doc["photos"]), "12 photos, one portrait, one deleted")
    check(len(draft.damage_summary) == 6 and len(look.items) == 5 and len(look.decisions) == 3,
          "6 list items, 5 suggestions, 3 decisions -- loading through the draft model")
    kinds = {e.box_kind for it in draft.damage_summary for e in it.evidence}
    check({"ai", "sort", None} <= kinds and any(not it.evidence for it in draft.damage_summary),
          "list items with an AI box, a sorter box, no box, and one old-style (no evidence)")
    check({it.check.verdict for it in look.items if it.check} == {"yes", "no", "unsure"}
          and any(it.other_side_listed for it in look.items) and any(it.key in look.dismissed for it in look.items),
          "suggestions: each re-check verdict, one with other_side_listed, one dismissed")
    from app.reports.taxonomy import CANONICAL_PARTS
    check(all(it.part in CANONICAL_PARTS for it in list(draft.damage_summary) + list(look.items)),
          "canonical parts only")
    check(all(it.key == item_key(it.model_dump()) for it in look.items), "each suggestion's key is prefill.item_key")
    text = path.read_text(encoding="utf-8")
    check(not re.search(r"\b[A-Z]{1,3}\d{1,4}[A-Z]\b", text.replace("data:image/svg+xml;base64,", "")),
          "nothing shaped like a plate")


def main() -> int:
    init_db()
    for name, fn in (
        ("4.1: the evidence models, out of the drafting schema", schema_models),
        ("store=False on every call (gemini_store_off), and today's request without it", store_off),
        ("the zone calls: their own light chain, the reading's thinking, no tools", zone_chain),
        ("the sort chain: no strong model", sort_chain),
        ("second_look._store: rebuilt from the zone reads, the second look off", store_rebuild),
        ("cleanup: old ai_derived files", sweep_derived),
        ("the benchmark: --zone-models, --store-on", bench_flags),
        ("option B: zone mode off is unchanged", option_b_single),
        ("option B: the list, the suggestions, a re-read, the catalogue", option_b),
        ("option B in the benchmark: --zone-only, the suggestions scored", option_b_bench),
        ("section 6: the Contract 9 settings", settings_block),
        ("the re-check's place (ai/check.py, Step 1's stub)", check_step),
        ("the dashboard's shared fixture", fixture),
    ):
        run_section(name, fn)
    print(f"\ntest_c9_base: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAILED [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
