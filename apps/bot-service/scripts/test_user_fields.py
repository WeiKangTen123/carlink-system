"""The workshop and the odometer reading are the user's to type in (owner,
25 Nov 2026; settings.intake_user_fields) -- offline: Gemini is a fake, a
scratch database and storage folder, no PDF render.

  0. the setting: "off" by default, only off / on, the compose default;
  1. off: byte for byte as master ae0fde4 (MASTER below, taken there with
     --hashes before any change): what every read sends (the website's
     intake read, the Telegram bot's, v1 with the packs, v2, v2 with the
     packs and the anchor), the sort and the zone reads, the response
     schema, a parsed draft, the Telegram bot's draft, a case read end to
     end through the intake API (requests, stored data, AI draft, GET
     /intake, filing, GET /reports), the Telegram bridge's and a public
     report's case, and the field review of a case;
  2. on: no read is told about the workshop or the odometer -- no
     instruction, no example ("odometer", "workshop", the example names,
     the "Km" reading), the rest of each prompt word for word -- and the
     response schema has neither field; the sorter still sorts an
     odometer photo;
  3. on: a model answer that fills both (and a workshop address) anyway
     -> both empty in the draft, the AI draft and the case, and nothing of
     the values in the log;
  4. on: the field review shows both as "needs" (a person to fill), a
     person's value as "you", never "ai", and ai_read leaves them out;
  5. on: the Telegram bot (a typed workshop kept: a person's), the
     Telegram bridge and a public report the same way.

    python scripts/test_user_fields.py
    python scripts/test_user_fields.py --hashes   # print section 1's hashes

Makes, plates, names, workshops and readings are made up; the photos are
coloured rectangles.
"""
import hashlib
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
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

HASHES_MODE = "--hashes" in sys.argv
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="user-fields-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaFAKEuserFIELDSkey00000000000000001"
os.environ["SECOND_LOOK_ENABLED"] = "false"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"
os.environ["PUBLIC_BASE_URL"] = "https://carlink.test/"
for _name in ("INTAKE_USER_FIELDS", "GEMINI_MODEL_CHAIN", "GEMINI_TRIAGE_MODEL_CHAIN", "GEMINI_ZONE_CHAIN",
              "GEMINI_THINKING_LEVEL_READ", "GEMINI_IMAGE_RESOLUTION", "GEMINI_IMAGE_RESOLUTION_MAIN_READ",
              "GEMINI_READ_ZOOM_HINT", "GEMINI_READ_TOOLS", "INTAKE_READ_MODE", "INTAKE_READ_PACKS",
              "INTAKE_READ_PROMPT", "INTAKE_READ_ANCHOR", "INTAKE_ZONE_PACKS", "INTAKE_FIRM_WORDS", "INTAKE_LINKS",
              "INTAKE_READ_CROPS", "INTAKE_SORT_DAMAGE_BOX", "TELEGRAM_FULL_DETECTION", "CATALOGUE_ENABLED",
              "INTAKE_CAR_MAP"):
    os.environ.pop(_name, None)

from PIL import Image  # noqa: E402

import app.ai.structured as structured  # noqa: E402

# ------------------------------------------------------------- the fake Gemini

REQUESTS: list[dict] = []
_lock = threading.Lock()

# (kind, area, part, shows damage) per photo, P01 first.
CASE = [
    ("log_card", None, None, False), ("odometer", None, None, False), ("overview", "rear", None, False),
    ("damage", "rear", "Rear Bumper", True), ("damage", "rear", "Boot Lid", True),
]
# Made up: what a model answers when it fills the two fields anyway (and a
# workshop address, which it is never asked for).
AI_WORKSHOP = "M/S Madeup Motor Works"
AI_ODOMETER = "012345 Km"
AI_WORKSHOP_ADDRESS = "1 Madeup Road, Singapore 000001"
TYPED_WORKSHOP = "M/S Typed Workshop"


def kind_of(kwargs) -> str:
    props = kwargs["response_format"]["schema"].get("properties", {})
    if "photos" in props:
        return "sort"
    if "zone_side_seen" in props:
        return "zone"
    return "draft"


def _labels(parts) -> list[str]:
    return [m.group(1) for part in parts if part.get("type") == "text"
            for m in [re.match(r"Photo (P\d+):$", part["text"])] if m]


def _sort_answer(labels):
    out = []
    for label in labels:
        kind, area, part, damage = CASE[(int(label[1:]) - 1) % len(CASE)]
        out.append({"label": label, "kind": kind, "angle": None, "area": area, "quality": "good",
                    "shows_damage": damage, "part": part, "note": f"photo {label}"})
    return {"batch_sides": None, "photos": out}


def draft_answer(labels=()) -> dict:
    labels = list(labels)

    def ref(label):
        return label if label in labels else (labels[0] if labels else None)
    return {
        "description": "The insured car was hit at the rear while parked.",
        "category": ["Vehicle Collision or Damage"],
        "accident_type": "Parked vehicle hit",
        "damaged_parts": ["Rear Bumper", "Boot Lid"],
        "damage_summary": [
            {"part": "Rear Bumper", "photo_reference": ref("P04"), "ai_confidence": "High",
             "condition_tokens": ["Cracked"], "severity": "Moderate", "remarks": "Replace"},
            {"part": "Boot Lid", "photo_reference": ref("P05"), "ai_confidence": "Medium",
             "condition_tokens": ["Dented"]},
        ],
        "vehicle_info": {"make": "Gammamotors", "model": "Testa", "plate_number": "TST1001A", "color": "Grey",
                         "transmission": "Automatic", "odometer_reading": AI_ODOMETER},
        "insurance_details": {"insurer_name": "Madeup Insurance", "claim_type": "Own damage",
                              "workshop_assigned": AI_WORKSHOP},
        "cover_page": {"workshop_address": AI_WORKSHOP_ADDRESS},
        "location": "Test Carpark 1",
    }


class FakeInteractions:
    def create(self, **kwargs):
        with _lock:
            REQUESTS.append(kwargs)
        kind = kind_of(kwargs)
        labels = _labels(kwargs["input"])
        if kind == "sort":
            out = _sort_answer(labels)
        elif kind == "zone":
            out = {"zone_side_seen": "unclear", "damage": [], "checked_intact": []}
        else:
            out = draft_answer(labels)
        return SimpleNamespace(output_text=json.dumps(out),
                               usage=SimpleNamespace(total_input_tokens=1000, total_output_tokens=100))


structured.get_client = lambda key=None: SimpleNamespace(interactions=FakeInteractions())
structured.acquire = lambda key=None: 0.0
structured.OVERLOADED_RETRY_SECONDS = 0.0
structured.CHAIN_BUSY_RETRY_SECONDS = 0.0

from app.ai import extraction, runs, triage, zone_read  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import pipeline  # noqa: E402
from app.reports import review  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import IntakeSession, Report, User  # noqa: E402

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
    REQUESTS.clear()
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()


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


ON = {"intake_user_fields": "on"}


def photo(i: int) -> str:
    path = Path(_tmp, f"photo{i}.jpg")
    if not path.exists():
        path.write_bytes(jpeg(i))
    return str(path)


def jpeg(i: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (320, 240), (25 * i % 256, 110, (230 - 20 * i) % 256)).save(buf, "JPEG", quality=85)
    return buf.getvalue()


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


# ----------------------------------------------------------- the reads' requests


class Recorder:
    """Stands in for extraction.call_structured: keeps each call's kwargs
    and its run-log tag, and answers with the parsed draft_answer()."""

    def __init__(self):
        self.calls: list[dict] = []
        self.tags: list = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        self.tags.append(runs.current_prompt_tag())
        return kwargs["parse"](json.dumps(draft_answer()))


def recorded(module, fn) -> Recorder:
    rec = Recorder()
    old = module.call_structured
    module.call_structured = rec
    try:
        fn()
    finally:
        module.call_structured = old
    return rec


def _part(part: dict) -> dict:
    if part.get("type") == "image":
        return {"type": "image", "mime_type": part.get("mime_type"), "resolution": part.get("resolution")}
    return part


SENT_KEYS = ("purpose", "system_instruction", "schema", "timeout", "tools_note", "max_tries")


def sent(kwargs: dict) -> dict:
    """What a call sends: every kwarg but the parser, the actor, the chain
    and a deadline, and the input parts without image bytes."""
    out = {k: kwargs[k] for k in SENT_KEYS if k in kwargs}
    out["input_parts"] = [_part(p) for p in kwargs.get("input_parts") or []]
    out["kwargs"] = sorted(kwargs)
    return out


def sent_text(kwargs: dict) -> str:
    """Every word a call sends: the system prompt, the note, the input's
    text parts and the response schema."""
    texts = [kwargs.get("system_instruction") or "", kwargs.get("tools_note") or ""]
    texts += [p.get("text") or "" for p in kwargs.get("input_parts") or [] if p.get("type") == "text"]
    texts.append(json.dumps(kwargs.get("schema") or {}))
    return "\n".join(texts)


ACTOR = AiActor.cli()

# The read variants: (name, settings, how it is called).
READ_VARIANTS = {
    "v1": {},
    "v1_packs": {"intake_read_packs": "orient,cues,small"},
    "v2": {"intake_read_prompt": "v2"},
    "v2_packs_anchor": {"intake_read_prompt": "v2", "intake_read_packs": "orient,cues,small",
                        "intake_read_anchor": True},
}


def intake_read(**kw) -> Recorder:
    """draft_report as the intake pipeline calls it (labels, hints, 120 s)."""
    return recorded(extraction, lambda: extraction.draft_report(
        "Hit at the rear while parked.", [photo(1), photo(2), photo(3)], photo_labels=["P02", "P04", "P05"],
        photo_hints=[{"kind": "odometer"}, {"kind": "damage", "area": "rear", "part": "Rear Bumper",
                                            "note": "crack"}, None],
        timeout=120.0, actor=ACTOR, **kw))


def bot_read() -> Recorder:
    """draft_report as the Telegram bot calls it on a redraft (known facts)."""
    return recorded(extraction, lambda: extraction.draft_report(
        "Scratched my rear bumper.", [photo(1)], {"vehicle plate": "TST1001A"}, actor=ACTOR))


def read_requests() -> dict:
    out = {}
    for name, values in READ_VARIANTS.items():
        with Settings(**values):
            out[f"read_{name}"] = sent(intake_read().calls[0])
            out[f"bot_{name}"] = sent(bot_read().calls[0])
    return out


def sort_request() -> dict:
    rec = Recorder()
    old = triage.call_structured
    triage.call_structured = lambda **kw: rec.calls.append(kw) or {}
    try:
        try:
            triage.triage_photos([("P01", photo(1)), ("P02", photo(2))], actor=ACTOR)
        except Exception:
            pass  # the recorder's {} is no sort; only the request matters
    finally:
        triage.call_structured = old
    return sent(rec.calls[0])


def zone_requests() -> dict:
    out = {}
    photos = [("P03", photo(3), "overview"), ("P04", photo(4), "damage")]
    for zone in zone_read.ZONE_NAMES:
        rec = Recorder()
        old = zone_read.call_structured
        zone_read.call_structured = lambda **kw: rec.calls.append(kw) or None
        try:
            try:
                zone_read.read_zone(zone, photos, actor=ACTOR, models=["lite-z"])
            except Exception:
                pass
        finally:
            zone_read.call_structured = old
        out[f"zone_{zone}"] = sent(rec.calls[0])
    return out


# ------------------------------------------------------------ a case end to end


_TIME = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?")
# A public report's "Sent ... on 25 Nov 2026, 3:12 pm (Singapore time)".
_SG_TIME = re.compile(r"\b\d{1,2} [A-Z][a-z]{2} \d{4}, \d{1,2}:\d{2} [ap]m\b")
_DURATIONS = {"seconds", "ms", "waited_s", "signature_hash"}
# Differ with the Pillow version (the photos' bytes), not with the code.
_BYTES = {"input_bytes"}


def normalised(obj, ids: dict[str, str]):
    """obj as JSON with the run's ids as placeholders, times as "<t>",
    durations as null and image bytes as "<image>"."""
    def walk(o):
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                if k in _DURATIONS or k in _BYTES:
                    out[k] = None
                elif k == "data" and isinstance(v, str) and len(v) > 200:
                    out[k] = "<image>"
                else:
                    out[k] = walk(v)
            return out
        if isinstance(o, (list, tuple)):
            return [walk(v) for v in o]
        return o
    text = json.dumps(walk(obj), sort_keys=True, default=str)
    for real, alias in sorted(ids.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(real, alias)
    text = text.replace(Path(_tmp).as_posix(), "<tmp>").replace(str(Path(_tmp)).replace("\\", "\\\\"), "<tmp>")
    return json.loads(_TIME.sub("<t>", _SG_TIME.sub("<t>", text)))


def _fake_render(*_a, output_path=None, **_kw):
    path = output_path or (_a[2] if len(_a) > 2 else None)
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"%PDF-1.4\n% test\n%%EOF\n")


def _app():
    import app.api.intake as intake_api
    import app.api.main as main_api

    main_api.render_pdf = _fake_render
    intake_api.render_pdf = _fake_render
    return main_api.app


def db_row(model, key):
    db = SessionLocal()
    try:
        row = db.get(model, key)
        if row is not None:
            db.expunge(row)
        return row
    finally:
        db.close()


def run_rows(rid: str) -> list[dict]:
    from app.reports.models import AiRun

    db = SessionLocal()
    try:
        rows = [{c: getattr(r, c) for c in ("kind", "channel", "status", "error_code", "attempts", "model_used",
                                             "photos_sent")} for r in db.query(AiRun).filter(AiRun.report_id == rid)]
    finally:
        db.close()
    for r in rows:
        r["attempts"] = sorted(r["attempts"] or [], key=lambda a: json.dumps(a, sort_keys=True, default=str))
    return sorted(rows, key=lambda r: (r["kind"], json.dumps(r, sort_keys=True, default=str)))


def wait_done(client, rid: str, timeout: float = 90) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        state = client.get(f"/intake/{rid}").json()
        if state["stage"] in ("ready", "failed") and not pipeline.is_running(rid):
            return state
        time.sleep(0.1)
    raise AssertionError("the reading timed out")


def _requests(ids: dict[str, str]) -> list:
    """The fake Gemini's requests so far, normalised, in a stable order."""
    return sorted((normalised(dict(r), ids) for r in REQUESTS), key=lambda r: json.dumps(r, sort_keys=True))


def _pick(d: dict, keys) -> dict:
    return {k: d.get(k) for k in keys}


GET_KEYS = ("stage", "data", "review", "ai_read", "ai_read_photos")


def new_draft(client) -> tuple[str, list[str]]:
    rid = client.post("/intake").json()["id"]
    ids = []
    for i in range(len(CASE)):
        r = client.post(f"/intake/{rid}/photos", files={"file": (f"IMG_{i:04d}.jpg", jpeg(i), "image/jpeg")},
                        data={"position": str(i)})
        assert r.status_code == 200 and "photo" in r.json(), r.text
        ids.append(r.json()["photo"]["id"])
    return rid, ids


def intake_case(client, uid: str, typed: dict | None = None) -> tuple[dict, dict]:
    """A case through the New Report wizard: read, filed, opened. typed:
    values a person typed on the cover before the read ({dotted key:
    value}). (normalised artefacts, the raw ones)."""
    rid, pids = new_draft(client)
    ids = {rid: "<rid>", uid: "<uid>", db_row(User, uid).email: "<email>",
           **{pid: f"<p{i + 1:02d}>" for i, pid in enumerate(pids)}}
    REQUESTS.clear()
    data = client.get(f"/intake/{rid}").json()["data"]
    data["vehicle_info"] = {**(data.get("vehicle_info") or {}), "plate_number": "TST1001A"}
    for key, value in (typed or {}).items():
        data = review.with_value(data, key, value)
    r = client.put(f"/intake/{rid}/data", json={"data": data, "reporter_notes": "Hit at the rear while parked."})
    assert r.status_code == 200, r.text
    r = client.post(f"/intake/{rid}/process", json={"mode": "all"})
    assert r.status_code == 200, r.text
    state = wait_done(client, rid)
    session = db_row(IntakeSession, rid)
    out = {
        "requests": sorted((normalised(dict(r), ids) for r in REQUESTS), key=lambda r: json.dumps(r, sort_keys=True)),
        "read": {"data": db_row(Report, rid).data, "ai_draft": session.ai_draft,
                 "read_photo_ids": session.read_photo_ids,
                 "runs": run_rows(rid)},
        "intake_get": _pick(state, GET_KEYS),
    }
    out["finalize"] = client.post(f"/intake/{rid}/finalize", json={"data": state["data"]}).json()
    filed = db_row(Report, rid)
    out["filed"] = {"data": filed.data, "ai_draft": filed.ai_draft, "status": filed.status}
    out["report_get"] = _pick(client.get(f"/reports/{rid}").json(), ("review", "ai_read", "ai_read_photos", "data"))
    raw = {"requests": list(REQUESTS), **out}
    return normalised(out, ids), raw


def bot_case(workshop: str | None) -> tuple[dict, dict]:
    """The Telegram bot's draft (conversation/flow.build_draft) on its first
    pass, the reporter's template answers in the session."""
    from app.conversation import flow
    from app.conversation.state import Session

    REQUESTS.clear()
    session = Session(description="Scratched my rear bumper at a carpark.", vehicle_plate="TST1001A",
                      location="Test Carpark 1", insurer_name="Madeup Insurance", workshop_name=workshop)
    result = flow.build_draft(session.description, [photo(1), photo(2)], session, actor=ACTOR)
    out = {"draft": result.draft.model_dump(), "ai_draft": result.ai_draft, "summary": result.summary_text,
           "requests": [normalised(dict(r), {}) for r in REQUESTS]}
    return normalised(out, {}), {"requests": list(REQUESTS), **out}


def bridge_case(client, uid: str) -> tuple[dict, dict]:
    """A staff report from the Telegram bot read through the website's
    pipeline (intake/telegram_bridge.py), the workshop typed into the bot's
    template."""
    from app.intake import telegram_bridge as bridge

    folder = Path(_tmp, f"tg-{time.time_ns()}")
    folder.mkdir()
    paths = []
    for i in range(len(CASE)):
        path = folder / f"tg-{i}.jpg"
        path.write_bytes(jpeg(i))
        paths.append(str(path))
    REQUESTS.clear()
    created = bridge.create_draft(external_id="3001", photo_paths=paths,
                                  facts={"vehicle_plate": "TST1001A", "workshop_name": TYPED_WORKSHOP},
                                  notes="Hit at the rear.", who=lambda db: (db.get(User, uid), None))
    assert created.report_id, created.problem
    rid = created.report_id
    assert bridge.start(rid, AiActor.for_user(uid, "telegram"))
    state = wait_done(client, rid)
    session = db_row(IntakeSession, rid)
    ids = {rid: "<rid>", uid: "<uid>", db_row(User, uid).email: "<email>"}
    for i, pid in enumerate(session.read_photo_ids or []):
        ids[pid] = f"<b{i:02d}>"
    out = {"data": db_row(Report, rid).data, "ai_draft": session.ai_draft, "intake_get": _pick(state, GET_KEYS),
           "requests": _requests(ids)}
    raw = {"requests": list(REQUESTS), **out}
    return normalised(out, ids), raw


def public_case(client, uid: str) -> tuple[dict, dict]:
    """A report from the public (intake/public.py), taken by staff and read
    on the website."""
    from app.intake import public
    from app.reports.models import AppSetting

    db = SessionLocal()
    try:
        db.merge(AppSetting(key=public.COMPANY_KEY_BILLED, value="true"))
        db.commit()
    finally:
        db.close()
    form = public.Form(telegram_user_id="777001", name="Test Sender", role="driver", phone="+6580000001",
                       statement="A test lorry reversed into my rear bumper.", consent_at=datetime.now(timezone.utc),
                       plate="TST1001A", telegram_username="testsender001", telegram_name="Test S")
    rid, reference = public.save_submission(form, [photo(i) for i in range(len(CASE))])
    assert client.post(f"/inbox/{rid}/take").status_code == 200
    REQUESTS.clear()
    assert client.post(f"/intake/{rid}/process", json={"mode": "all"}).status_code == 200
    state = wait_done(client, rid)
    session = db_row(IntakeSession, rid)
    ids = {rid: "<rid>", uid: "<uid>", db_row(User, uid).email: "<email>", reference: "<ref>"}
    for i, pid in enumerate(session.read_photo_ids or []):
        ids[pid] = f"<q{i:02d}>"
    out = {"data": db_row(Report, rid).data, "ai_draft": session.ai_draft, "intake_get": _pick(state, GET_KEYS),
           "requests": _requests(ids)}
    raw = {"requests": list(REQUESTS), **out}
    return normalised(out, ids), raw


def review_sample() -> dict:
    """The field review and ai_read of a case whose cover still says what
    the AI read (the workshop and the odometer among it), and of one a
    person typed them on."""
    from app.reports.filing import ai_read_for

    ai = extraction._parse_draft(json.dumps(draft_answer(["P02", "P04"]))).model_dump()
    index = [{"label": "P01", "kind": "log_card"}, {"label": "P02", "kind": "odometer"}]
    typed = review.with_value(review.with_value(ai, "insurance_details.workshop_assigned", TYPED_WORKSHOP),
                              "vehicle_info.odometer_reading", "054321 Km")
    blank = review.with_value(review.with_value(ai, "insurance_details.workshop_assigned", None),
                              "vehicle_info.odometer_reading", None)
    return {
        "ai_values": review.review_state(ai, ai, index, False),
        "typed": review.review_state(typed, ai, index, False),
        "blank": review.review_state(blank, ai, index, False),
        "no_ai_draft": review.review_state(typed, None, index, False),
        "ai_read": ai_read_for(ai, index),
    }


def snapshot() -> dict:
    """Section 1's artefacts at today's settings, by name."""
    from fastapi.testclient import TestClient

    from auth_helpers import sign_in

    out = {**read_requests(), "sort": sort_request(), **zone_requests(),
           "schema": extraction._response_schema(),
           "parsed": extraction._parse_draft(json.dumps(draft_answer(["P04", "P05"]))).model_dump(),
           "bot_case": bot_case(None)[0], "bot_case_typed": bot_case(TYPED_WORKSHOP)[0],
           "review": review_sample()}
    with TestClient(_app()) as client:
        uid = sign_in(client, role="admin", may_sign_off=True)
        out["intake_case"] = intake_case(client, uid)[0]
        out["bridge_case"] = bridge_case(client, uid)[0]
        out["public_case"] = public_case(client, uid)[0]
    return out


# Taken on master ae0fde4 with --hashes, before any change (python 3.11 and
# 3.13 gave the same).
MASTER = {
    "read_v1": "185a5ef3ded2d5bd0c2e3e4e5f1d5da0390e6124639a892d738b2f8e1af4b7b3",
    "bot_v1": "2b29a50e957752ca0738d7c27d9c7b574b6323a3a7cbaab21ab29b0fec1149d8",
    "read_v1_packs": "40789ae5725f503fbdc83654120081cbc12462aa192db9442f1832c9e38c3808",
    "bot_v1_packs": "f5104170f3d147411d94497ef67e8e067f9227100a1c896e18006634470dda00",
    "read_v2": "fd9872080267193c51ffed91fcac042989a2479d45f057deb1ad13f75e03e8b8",
    "bot_v2": "56341236068868f4ea0614359f507a9b23cd5e04cf58a55c8681d60ba7b1f0d6",
    "read_v2_packs_anchor": "5cb87de178da7337f25a1129e3eace26b59b935604e66c7d7615915ab6b2bdf1",
    "bot_v2_packs_anchor": "d498c179545066364cfeb0a0e2a3460c5b0b43aabba3d4ee7c2340d66aa96da0",
    "sort": "282f8c9dba362aee81bb3ece32998b710ef0c09762754f0853cfd028c9c8a1a3",
    "zone_front": "be0ea7d8572a09345687a7874c8d73d486d2b8c9b3cef0990470e6b9b0bbdb3a",
    "zone_rear": "8fdd9d08a23848d21e842d2970a08d07dbfbff64185cd67097988b9b780a341b",
    "zone_left": "4c563f80187e9ec2524a03d59a8f6afb9113cf24bdef38f6c4c3c2ccc273fb09",
    "zone_right": "4c563f80187e9ec2524a03d59a8f6afb9113cf24bdef38f6c4c3c2ccc273fb09",
    "zone_close": "970b8ed3862228926c8801f9dbf52f16f0c0986213bfc812c25c07ebca11a7d8",
    "schema": "d4b157b2e86d37d66933954cc972b7451519261d5fc7fcb0fefbb879dfb40c14",
    "parsed": "14460a14ddb207a8e6dcb7edfd70a2a56e0c9e187bf79ca7ddc11e86f4a5c242",
    "bot_case": "4ab59e1e101a87d7282834978a6baf8d148feecba421f72e85f1ce621edd004a",
    "bot_case_typed": "5a6721a85e5a26d22b08fb1227dae965b9fba65563f25782ee7bbd7c431ad5c6",
    "review": "8d43ae0ed647cf3d36f21cb59ce258e79ea009d3a61f485d46e33ae7eacbf2df",
    "intake_case": "4f882335efa3dbcff321a86cbb6eb9d8520096949f7635a004cdcc47cda870b6",
    "bridge_case": "740d8eae9cd7fb478da1d91693ccaddf1ac86e01616f6dbb9d848687b0fe4f64",
    "public_case": "73ec44ea896e373292bdce281ac7f7e9eaa790c5de8c0b71a5e6be959899a129",
}


# ------------------------------------------------------------------ sections


def setting() -> None:
    from pydantic import ValidationError

    from app.config import Settings as AppSettings

    check(settings.intake_user_fields == "off", "intake_user_fields is off by default")
    for raw, want in (("on", "on"), ("ON ", "on"), ("off", "off"), ("", "off")):
        check(AppSettings(intake_user_fields=raw).intake_user_fields == want, f"{raw!r} -> {want!r}")
    try:
        AppSettings(intake_user_fields="yes")
        check(False, "'yes' is refused")
    except ValidationError:
        check(True, "'yes' is refused")
    compose = (ROOT.parent.parent / "infra" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    check("- INTAKE_USER_FIELDS=${INTAKE_USER_FIELDS:-off}" in compose,
          "compose passes INTAKE_USER_FIELDS through, at config.py's default (off)")


def off_byte_for_byte() -> None:
    got = snapshot()
    for name, want in MASTER.items():
        check(digest(got.get(name)) == want, f"{name}: byte for byte as master ae0fde4", digest(got.get(name)))
    check(set(got) == set(MASTER), "every artefact compared", sorted(set(got) ^ set(MASTER)))


# What "on" takes out of each read's text, as it is taken out. The rest of
# the prompt stays word for word: the "off" text with these edits is the
# "on" text.
EDITS_V1 = (
    ("police report, or workshop estimate). A human", "police report, or repair estimate). A human"),
    ("4. Insurance & Workshop Details ('insurance_details'):", "4. Insurance Details ('insurance_details'):"),
)
LINES_V1 = ("     * 'odometer_reading':", "   - Extract 'workshop_assigned'")
EDITS_V2 = (
    ("(LTA log card, police report, workshop estimate)", "(LTA log card, police report, repair estimate)"),
)
SPANS_V2 = (("'odometer_reading' exactly as read", "; "), (", 'workshop_assigned'", ")"))
# Never in a read's words with "on" (the photo kind is the sorter's own):
# in any letter case, and the example values as they were written.
NEVER_WORDS = ("odometer", "workshop", "speedometer", "mileage")
NEVER_VALUES = ("Precise Auto", "Sin Ming", "EM-1", "ComfortDelGro", "058615", " Km'", "Km)")


def _drop_line(text: str, start: str) -> str:
    at = text.index(start)
    return text[:at] + text[text.index("\n", at) + 1:]


def _cut(text: str, start: str, end: str) -> str:
    at = text.index(start)
    return text[:at] + text[text.index(end, at) + len(end):]


def expected_on(text: str, version: str) -> str:
    if version == "v2":
        for old, new in EDITS_V2:
            text = text.replace(old, new)
        for start, end in SPANS_V2:
            text = _cut(text, start, end)
        return text
    for old, new in EDITS_V1:
        text = text.replace(old, new)
    for start in LINES_V1:
        text = _drop_line(text, start)
    return text


def on_instructions() -> None:
    for name, values in READ_VARIANTS.items():
        version = values.get("intake_read_prompt", "v1")
        for how, call in (("intake", intake_read), ("bot", bot_read)):
            with Settings(**values):
                off = call()
            with Settings(**values, **ON):
                on = call()
            off_kw, on_kw = off.calls[0], on.calls[0]
            words = sent_text(on_kw)
            found = [w for w in NEVER_WORDS if w in words.lower()] + [v for v in NEVER_VALUES if v in words]
            check(not found, f"{name} ({how}): nothing of the workshop or the odometer is sent", found)
            check(on_kw["system_instruction"] == expected_on(off_kw["system_instruction"], version),
                  f"{name} ({how}): the rest of the prompt is word for word")
            check(on_kw["input_parts"] == off_kw["input_parts"], f"{name} ({how}): the input parts are as off")
            props = json.dumps(on_kw["schema"])
            check("odometer_reading" not in props and "workshop_assigned" not in props
                  and "odometer_reading" in json.dumps(off_kw["schema"]),
                  f"{name} ({how}): neither field is in the response schema (off: both are)")
            vehicle = on_kw["schema"]["$defs"]["VehicleInfo"]["properties"]
            check("plate_number" in vehicle and "point_of_impact" in vehicle and "tyres" in vehicle,
                  f"{name} ({how}): the vehicle's other fields stay in the schema")
            check({k: v for k, v in sent(on_kw).items() if k not in ("system_instruction", "schema")}
                  == {k: v for k, v in sent(off_kw).items() if k not in ("system_instruction", "schema")},
                  f"{name} ({how}): everything else sent is as off")
            check(on.tags[0] and on.tags[0].startswith(f"read-{version}") and "+user-fields@" in on.tags[0],
                  f"{name} ({how}): the run log's tag says user-fields", on.tags[0])
    with Settings(**ON):
        sort = sort_request()
    check("odometer" in sort["system_instruction"], "the sorter still sorts an odometer photo (its photo kind)")
    check(sort == sort_request(), "the sort sends the same with on as off")
    with Settings(**ON):
        zones = zone_requests()
    check(zones == zone_requests(), "the zone reads send the same with on as off")
    check(not any(w.lower() in json.dumps(z).lower() for z in zones.values() for w in ("odometer", "workshop")),
          "no zone read says odometer or workshop")
    with Settings(**ON), Settings(**{}):
        from app.reports import knowledge

        check(knowledge.stamp(["m"])["prompt"] == "v1+user-fields", "the knowledge stamp's prompt says user-fields")
    from app.reports import knowledge

    check(knowledge.stamp(["m"])["prompt"] == "v1", "off: the knowledge stamp's prompt is v1, as before")


class _Logs(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def _empty(draft: dict) -> bool:
    return (not (draft.get("vehicle_info") or {}).get("odometer_reading")
            and not (draft.get("insurance_details") or {}).get("workshop_assigned")
            and not (draft.get("cover_page") or {}).get("workshop_address"))


def on_dropped() -> None:
    logs = _Logs()
    root = logging.getLogger()
    root.addHandler(logs)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    try:
        with Settings(**ON):
            parsed = extraction._parse_draft(json.dumps(draft_answer(["P04"]))).model_dump()
    finally:
        root.removeHandler(logs)
        root.setLevel(old_level)
    check(_empty(parsed), "a model answer with both (and a workshop address) parses to none of them")
    check(parsed["vehicle_info"]["plate_number"] == "TST1001A" and parsed["insurance_details"]["insurer_name"]
          == "Madeup Insurance" and parsed["vehicle_info"]["transmission"] == "Automatic",
          "the answer's other facts stay")
    said = "\n".join(logs.lines)
    check(not any(v in said for v in (AI_WORKSHOP, AI_ODOMETER, AI_WORKSHOP_ADDRESS, "012345", "Madeup")),
          "nothing of the values is logged")
    check(any("odometer_reading" in line and "workshop_assigned" in line for line in logs.lines),
          "the log names the fields dropped (only their names)", logs.lines)
    parsed_off = extraction._parse_draft(json.dumps(draft_answer(["P04"]))).model_dump()
    check(not _empty(parsed_off), "off: the model's values are kept, as before")


def on_case() -> None:
    from fastapi.testclient import TestClient

    from auth_helpers import sign_in

    with Settings(**ON), TestClient(_app()) as client:
        uid = sign_in(client, role="admin", may_sign_off=True)
        _, raw = intake_case(client, uid)
        drafts = [r for r in raw["requests"] if kind_of(r) == "draft"]
        check(drafts and not any(w.lower() in json.dumps(r["input"]).lower() + r["system_instruction"].lower()
                                 + json.dumps(r["response_format"]).lower()
                                 for r in drafts for w in ("odometer", "workshop")),
              "the case's read requests: no odometer or workshop anywhere")
        sorts = [r for r in raw["requests"] if kind_of(r) == "sort"]
        check(sorts and "odometer" in sorts[0]["system_instruction"], "the case's sort: the odometer photo kind stays")
        for where, data in (("read: the case", raw["read"]["data"]), ("read: the AI draft", raw["read"]["ai_draft"]),
                            ("filed: the case", raw["filed"]["data"]),
                            ("filed: the AI draft", raw["filed"]["ai_draft"]),
                            ("GET /reports data", raw["report_get"]["data"])):
            check(_empty(data), f"{where}: no workshop, odometer or workshop address")
        check(raw["read"]["data"]["vehicle_info"].get("make") == "Gammamotors"
              and raw["read"]["data"]["damage_summary"], "the case keeps the AI's other facts and its damage list")
        check(any(p.get("kind") == "odometer" for p in raw["filed"]["data"].get("photo_index") or []),
              "the dashboard photo is still sorted as an odometer photo")
        for where, got in (("GET /intake", raw["intake_get"]), ("GET /reports", raw["report_get"])):
            fields, rules = got["review"]["fields"], got["review"]["rules"]
            check(fields["insurance_details.workshop_assigned"]["state"] == "needs"
                  and fields["vehicle_info.odometer_reading"]["state"] == "needs",
                  f"{where}: the review shows both as 'Needs you'",
                  (fields["insurance_details.workshop_assigned"], fields["vehicle_info.odometer_reading"]))
            check(rules["insurance_details.workshop_assigned"] == "office"
                  and rules["vehicle_info.odometer_reading"] == "office",
                  f"{where}: their rule is a person's (office), so the form keeps them 'Needs you' while editing")
            read = got["ai_read"] or {}
            check("odometer_reading" not in (read.get("vehicle_info") or {})
                  and "workshop_assigned" not in (read.get("insurance_details") or {})
                  and "odometer_reading" not in (got["ai_read_photos"] or {}),
                  f"{where}: ai_read has neither (no AI tag beside them)")
        runs_ = raw["read"]["runs"]
        tags = {a.get("prompt") for r in runs_ if r["kind"] == "read" for a in r["attempts"]}
        check(any(t and "+user-fields@" in t for t in tags), "the run log's read tries carry the user-fields tag", tags)

        # A person typed both before the read: theirs, kept, "you".
        _, typed = intake_case(client, uid, {"insurance_details.workshop_assigned": TYPED_WORKSHOP,
                                             "vehicle_info.odometer_reading": "054321 Km"})
        data = typed["read"]["data"]
        check(data["insurance_details"]["workshop_assigned"] == TYPED_WORKSHOP
              and data["vehicle_info"]["odometer_reading"] == "054321 Km", "a person's typed values are kept")
        fields = typed["intake_get"]["review"]["fields"]
        check(fields["insurance_details.workshop_assigned"]["state"] == "you"
              and fields["vehicle_info.odometer_reading"]["state"] == "you", "and shown as 'You'")


def on_review() -> None:
    from app.reports.filing import ai_read_for

    off = review_sample()
    with Settings(**ON):
        on = review_sample()
        ai = extraction._parse_draft(json.dumps(draft_answer(["P02"]))).model_dump()
    keys = ("insurance_details.workshop_assigned", "vehicle_info.odometer_reading")
    for key in keys:
        check(on["blank"]["fields"][key]["state"] == "needs", f"{key}: empty -> 'needs'")
        check(on["typed"]["fields"][key]["state"] == "you", f"{key}: a person's value -> 'you'")
        check(on["ai_values"]["fields"][key]["state"] not in ("ai",) and "photo" not in on["ai_values"]["fields"][key],
              f"{key}: a value the AI read before the switch went on is never 'AI', nor tagged with a photo",
              on["ai_values"]["fields"][key])
    check(off["blank"]["fields"]["vehicle_info.odometer_reading"]["state"] == "none",
          "off: an empty odometer is 'none', as before")
    check(off["ai_values"]["fields"]["vehicle_info.odometer_reading"].get("photo") == "P02",
          "off: the odometer the AI read is tagged with its photo, as before")
    ai_keys = [k for k, f in on["ai_values"]["fields"].items() if f["state"] == "ai"]
    check(not set(keys) & set(ai_keys) and "vehicle_info.plate_number" in ai_keys,
          "the 'Confirm N AI-read fields' keys never include them (the plate still is)", ai_keys)
    read, photos = on["ai_read"]
    check("odometer_reading" not in read["vehicle_info"] and "workshop_assigned" not in read["insurance_details"]
          and "odometer_reading" not in photos, "ai_read leaves them out (no AI tag)")
    check(read["vehicle_info"].get("plate_number") == "TST1001A", "ai_read keeps the other AI-read fields")
    check(_empty(ai), "and a draft parsed with on has neither to show")
    off_read = ai_read_for(extraction._parse_draft(json.dumps(draft_answer(["P02"]))).model_dump(),
                           [{"label": "P02", "kind": "odometer"}])
    check(off_read[0]["vehicle_info"].get("odometer_reading") == AI_ODOMETER, "off: ai_read as before")
    check(on["blank"]["before_sign_off"] == off["blank"]["before_sign_off"]
          and any(item["label"] == "Workshop" for item in on["blank"]["before_sign_off"]),
          "the workshop is still on the sign-off's must-have list")


def on_telegram_public() -> None:
    from fastapi.testclient import TestClient

    from auth_helpers import sign_in

    with Settings(**ON):
        _, bot = bot_case(None)
        _, bot_typed = bot_case(TYPED_WORKSHOP)
    for where, got in (("the bot", bot), ("the bot, workshop typed", bot_typed)):
        check(not (got["ai_draft"]["vehicle_info"] or {}).get("odometer_reading")
              and not (got["ai_draft"]["insurance_details"] or {}).get("workshop_assigned"),
              f"{where}: the AI's draft has neither")
        check(not got["draft"]["vehicle_info"].get("odometer_reading"), f"{where}: the draft has no odometer")
        check(not any(w in json.dumps(r["input"]).lower() + r["system_instruction"].lower()
                      + json.dumps(r["response_format"]).lower()
                      for r in got["requests"] for w in ("odometer", "workshop")),
              f"{where}: its request says nothing of either")
    check(not bot["draft"]["insurance_details"].get("workshop_assigned"), "the bot, nothing typed: no workshop")
    check(bot_typed["draft"]["insurance_details"].get("workshop_assigned") == TYPED_WORKSHOP
          and TYPED_WORKSHOP in bot_typed["summary"], "the bot: the workshop the reporter typed is kept (a person's)")

    with Settings(**ON), TestClient(_app()) as client:
        uid = sign_in(client, role="admin", may_sign_off=True)
        _, bridge = bridge_case(client, uid)
        _, pub = public_case(client, uid)
    check(bridge["data"]["insurance_details"].get("workshop_assigned") == TYPED_WORKSHOP
          and not bridge["data"]["vehicle_info"].get("odometer_reading"),
          "the Telegram bridge: the typed workshop kept, no odometer")
    check(_empty(bridge["ai_draft"]), "the Telegram bridge: the AI draft has neither")
    check(_empty(pub["data"]) and _empty(pub["ai_draft"]), "a public report: neither, in the case or the AI draft")
    for where, got in (("the Telegram bridge", bridge), ("a public report", pub)):
        drafts = [r for r in got["requests"] if kind_of(r) == "draft"]
        check(drafts and not any(w in json.dumps(r["input"]).lower() + r["system_instruction"].lower()
                                 + json.dumps(r["response_format"]).lower()
                                 for r in drafts for w in ("odometer", "workshop")),
              f"{where}: its read says nothing of either")
        fields = got["intake_get"]["review"]["fields"]
        check(fields["vehicle_info.odometer_reading"]["state"] == "needs", f"{where}: the odometer 'Needs you'")
    check(pub["intake_get"]["review"]["fields"]["insurance_details.workshop_assigned"]["state"] == "needs",
          "a public report: the workshop 'Needs you'")


def hashes() -> int:
    """Prints section 1's hashes; --dump DIR also writes what they hash (to
    tell two trees' runs apart: diff the files)."""
    dump = Path(sys.argv[sys.argv.index("--dump") + 1]) if "--dump" in sys.argv else None
    init_db()
    for name, obj in snapshot().items():
        print(f'    "{name}": "{digest(obj)}",')
        if dump:
            dump.mkdir(parents=True, exist_ok=True)
            (dump / f"{name}.json").write_text(json.dumps(obj, sort_keys=True, indent=1, default=str), encoding="utf-8")
    return 0


def main() -> int:
    init_db()
    for name, fn in (
        ("0. the setting", setting),
        ("1. off: byte for byte as master ae0fde4", off_byte_for_byte),
        ("2. on: no read is told about the workshop or the odometer", on_instructions),
        ("3. on: a value the model returns anyway is dropped", on_dropped),
        ("3b. on: a case end to end (New Report)", on_case),
        ("4. on: the field review", on_review),
        ("5. on: the Telegram bot, the Telegram bridge, a public report", on_telegram_public),
    ):
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(hashes() if HASHES_MODE else main())
