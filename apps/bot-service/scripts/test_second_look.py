"""The intake's second look (app/ai/second_look.py): one Gemini call after the
reading that suggests more damaged parts, which become damage items only
when a person ticks them.

Covers: the candidates (same-make past kits, the parts beside the damage,
nothing listed or dismissed) and the photos sent; the call's small schema;
one call per reading, suggestions stored and never added by the machine;
parts already listed filtered out; a failure, a timeout, no allowance or the
switch off leaving the intake as the reading made it; a tick through the
wizard's own save path reaching Annex A at filing; a re-read running it
again and keeping what a person added and dismissed; and a public report's
call going through the same key rule with nothing of the sender's in it.

    python scripts/test_second_look.py

Offline: Gemini is a fake. Makes, references and plates are made up.
"""
import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="second-look-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = tempfile.mkdtemp(prefix="secrets-")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
# Off by default in the app until the benchmark gate passes; on for its tests.
os.environ["SECOND_LOOK_ENABLED"] = "true"
os.environ.setdefault("GEMINI_API_KEY", "fake")

import app.ai.structured as structured  # noqa: E402

# Every marker below is the sender's or the reporter's: none may reach Gemini
# through the second look.
SECRET_NOTES = "NOTES-MARKER rear-ended at the carpark, call me on 91234567"
SECRET_NAME = "NAME-MARKER Tan"
SECRET_CONTACT = "CONTACT-MARKER 98765432"
SECRET_PLATE = "ZZT9999Q"
MAKE, MODEL = "Gammamotors", "Vezel"

# The sort's answer per photo label: kind, area, part, shows damage.
SORT = {
    "P01": ("log_card", None, None, False),
    "P02": ("overview", "rear", None, False),
    "P03": ("damage", "rear", "Rear Bumper", True),
    "P04": ("damage", "rear", "Boot Lid", True),
    "P05": ("damage", "rear", "Boot Lid Lock", True),
    "P06": ("damage", "rear", None, True),
    "P07": ("damage", "rear-left", "Tail Lamp (side undetermined)", True),
    "P08": ("damage", "front", "Front Bumper", True),
}
PHOTO_COUNT = len(SORT)

calls = {"sort": 0, "draft": 0, "look": 0}
looks: list[dict] = []  # each second-look request: model, schema, texts, photo labels
behaviour = {"look": "ok", "delay": 0.0}


def _labels(parts: list[dict]) -> list[str]:
    return [m.group(1) for part in parts if part.get("type") == "text"
            for m in [re.match(r"Photo (P\d+):$", part["text"])] if m]


def _look_answer(enum: list[str], labels: list[str]) -> dict:
    """Two real finds; a listed part (not in the enum), a part on a photo
    that wasn't sent, and stray words -- all three must be dropped."""
    items = []
    if "Boot Lid Lock" in enum and "P05" in labels:
        items.append({"part": "Boot Lid Lock", "photo": "P5", "note": "Striker bent", "condition_tokens": ["Bent", "Necessary"],
                      "confidence": "High"})
    if "Rear End Panel" in enum and "P06" in labels:
        items.append({"part": "Rear End Panel", "photo": "P06", "note": "Dent at the latch", "condition_tokens": ["Dented"],
                      "confidence": "Medium"})
    if "Boot Lid Hinge" in enum and "P04" in labels:
        items.append({"part": "Boot Lid Hinge", "photo": "P04", "note": "Hinge pushed in", "condition_tokens": ["Shifted", "Bent"],
                      "confidence": "Low"})
    items.append({"part": "Rear Bumper", "photo": "P03", "condition_tokens": ["Cracked"], "confidence": "High"})
    items.append({"part": "Left Rear Bumper Reflector", "photo": "P99", "condition_tokens": ["Cracked"], "confidence": "High"})
    return {"items": items}


class FakeInteractions:
    def create(self, *, model, system_instruction, input, response_format, timeout):
        schema = response_format["schema"]
        props = schema.get("properties", {})
        labels = _labels(input)
        assert len(labels) == len([p for p in input if p.get("type") == "image"])
        if "photos" in props:
            calls["sort"] += 1
            out = []
            for label in labels:
                kind, area, part, damage = SORT[label]
                out.append({"label": label, "kind": kind, "area": area, "quality": "good", "shows_damage": damage,
                            "part": part, "note": f"close-up {label}"})
            return SimpleNamespace(output_text=json.dumps({"photos": out}))
        if "items" in props:
            calls["look"] += 1
            texts = [p["text"] for p in input if p.get("type") == "text"]
            looks.append({"model": model, "schema": schema, "texts": texts, "labels": labels,
                          "system": system_instruction})
            if behaviour["delay"]:
                time.sleep(behaviour["delay"])
            if behaviour["look"] == "fail":
                raise RuntimeError("503 service_unavailable: high demand")
            if behaviour["look"] == "day":
                # Google's own error for a used-up free day (the SDK's type,
                # so keys.refusal reads it as Google would send it).
                from google.genai import errors as genai_errors
                raise genai_errors.ClientError(429, {"error": {
                    "code": 429, "status": "RESOURCE_EXHAUSTED",
                    "message": "Quota exceeded for metric: generate_content_free_tier_requests, limit: 20, model: x",
                    "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
                        {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})
            enum = props["items"]["items"]["properties"]["part"]["enum"]
            return SimpleNamespace(output_text=json.dumps(_look_answer(enum, labels)))
        calls["draft"] += 1
        draft = {
            "description": "Insured vehicle was hit at the rear.",
            "category": ["Vehicle Collision or Damage"],
            "damaged_parts": ["Rear Bumper", "Boot Lid", "Tail Lamp (side undetermined)"],
            "damage_summary": [
                {"part": "Rear Bumper", "photo_reference": "P03", "condition_tokens": ["Cracked"], "ai_confidence": "High"},
                {"part": "Boot Lid", "photo_reference": "P04", "condition_tokens": ["Buckled"], "ai_confidence": "High"},
                {"part": "Tail Lamp (side undetermined)", "photo_reference": "P07", "condition_tokens": ["Cracked"]},
            ],
            "vehicle_info": {"make": MAKE, "model": MODEL, "plate_number": SECRET_PLATE},
        }
        return SimpleNamespace(output_text=json.dumps(draft))


structured.get_client = lambda key=None: SimpleNamespace(interactions=FakeInteractions())
structured.acquire = lambda key=None: 0.0

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from auth_helpers import sign_in  # noqa: E402

import app.ai.keys as keys  # noqa: E402
from app.ai import second_look as sl  # noqa: E402
from app.ai.extraction import _response_schema  # noqa: E402
from app.ai.keys import AiActor, AiUnavailable  # noqa: E402
from app.api.main import app  # noqa: E402
from app.auth.permissions import PUBLIC_CHANNEL  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import pipeline  # noqa: E402
from app.parts.history import PastCase, PastLine, import_case  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import AiRun, IntakePhoto, IntakeSession, Report, gen_id  # noqa: E402
from app.reports.taxonomy import CANONICAL_PARTS, FIRM_CONDITION_TOKENS  # noqa: E402

CHAIN = sl._chain()


def ok(msg: str) -> None:
    print(f"    ok   {msg}")


def photo_files() -> list[Path]:
    out = Path(_tmp, "photos")
    out.mkdir(exist_ok=True)
    files = []
    for i in range(PHOTO_COUNT):
        path = out / f"IMG_{i:04d}.jpg"
        Image.new("RGB", (64, 48), (30 * i % 255, 120, 255 - 25 * i)).save(path, "JPEG")
        files.append(path)
    return files


def line(n: int, item: str, condition: str, adjusted: float = 100.0) -> PastLine:
    return PastLine(line_no=n, section="list", item=item, condition=condition, remarks="", qty=1, qty_raw="1",
                    workshop_est=adjusted * 1.25, discount=None, adjusted_cost=adjusted)


def import_history() -> None:
    """Two made-up past cases of the same make: a tailgate job is the
    tailgate, its hinges and its lock -- so the hinge and the lock are kit
    candidates for a case with a damaged boot lid. One case of another make
    with a rear windscreen, which must never be a kit candidate here."""
    cases = [
        PastCase(ref="CL 91001", plate="TESTA1", make=MAKE, model=MODEL, make_model_raw=f"{MAKE} {MODEL}",
                 registered=None, surveyed="2025-01-01",
                 lines=[line(1, "Tail gate assy", "Buckled", 1200), line(2, "Tail gate hinge LH", "Bent", 90),
                        line(3, "Tail gate lock", "Distorted", 150), line(4, "Rear bumper fascia", "Cracked", 500)]),
        PastCase(ref="CL 91002", plate="TESTA2", make=MAKE, model=MODEL, make_model_raw=f"{MAKE} {MODEL}",
                 registered=None, surveyed="2025-02-01",
                 lines=[line(1, "Tail gate assy", "Buckled", 1300), line(2, "Tail gate hinge RH", "Bent", 95),
                        line(3, "Tail gate lock", "Bent", 160)]),
        PastCase(ref="CL 91003", plate="TESTB1", make="Omegamotors", model="Saloon", make_model_raw="Omegamotors Saloon",
                 registered=None, surveyed="2025-03-01",
                 lines=[line(1, "Boot lid assy", "Buckled", 1000), line(2, "Rear windscreen", "Cracked", 400)]),
        PastCase(ref="CL 91004", plate="TESTB2", make="Omegamotors", model="Saloon", make_model_raw="Omegamotors Saloon",
                 registered=None, surveyed="2025-04-01",
                 lines=[line(1, "Boot lid assy", "Buckled", 1000), line(2, "Rear windscreen", "Cracked", 400)]),
    ]
    db = SessionLocal()
    try:
        for i, case in enumerate(cases):
            import_case(db, case, f"synthetic-{i}.xlsx")
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------ offline parts


def test_vocabulary() -> None:
    print("\n  vocabulary and schema")
    for zone, parts in sl.ZONES.items():
        assert all(p in CANONICAL_PARTS for p in parts), zone
    ok("every zone part is a canonical part")
    schema = sl.response_schema(CANONICAL_PARTS[: sl.MAX_CANDIDATES])
    text = json.dumps(schema)
    assert len(text) < 2000, len(text)
    for banned in ("minItems", "maxItems", "anyOf", "null", "$ref", "$defs"):
        assert banned not in text, banned
    item = schema["properties"]["items"]["items"]["properties"]
    assert item["condition_tokens"]["items"]["enum"] == FIRM_CONDITION_TOKENS and item["confidence"]["enum"] == ["High", "Medium", "Low"]
    ok(f"second-look schema: {len(text)} characters at {sl.MAX_CANDIDATES} candidates, no minItems/maxItems/anyOf/null")
    draft = json.dumps(_response_schema())
    assert "second_look" not in draft and "SecondLook" not in draft and "items" not in json.loads(draft)["properties"]
    ok("the drafting schema (shared with the Telegram bot) is unchanged: no second_look in it")

    assert sl.covers_of("Left Tail Lamp") == ["Left Tail Lamp", "Tail Lamp (side undetermined)"]
    assert set(sl.covers_of("Tail Lamp (side undetermined)")) == {"Left Tail Lamp", "Right Tail Lamp", "Tail Lamp (side undetermined)"}
    assert sl.covers_of("Boot Lid Lock") == ["Boot Lid Lock"]
    listed = {"Tail Lamp (side undetermined)", "Right Rear Quarter Panel"}
    assert sl.is_listed("Left Tail Lamp", listed) and sl.is_listed("Quarter Panel (side undetermined)", listed)
    assert not sl.is_listed("Left Rear Quarter Panel", listed) and not sl.is_listed("Left Front Wheel", {"Left Rear Wheel"})
    ok("listed by canonical part and side: an undetermined side covers both, a sided one only its own")

    sent = {"P05": "id5", "P06": "id6"}
    raw = json.dumps({"items": [
        {"part": "Boot Lid Lock", "photo": "p5", "note": "  bent \n striker ", "condition_tokens": ["Bent", "Serviceable", "Cut", "Torn", "Dented"],
         "confidence": "Sure"},
        {"part": "Boot Lid Lock", "photo": "P06", "condition_tokens": [], "confidence": "High"},
        {"part": "Bonnet", "photo": "P05", "condition_tokens": ["Dented"], "confidence": "High"},
        {"part": "Rear End Panel", "photo": "P44", "condition_tokens": ["Dented"], "confidence": "High"},
    ]})
    got = sl.parse_answer(raw, {"Boot Lid Lock", "Rear End Panel"}, sent)
    assert got == [{"part": "Boot Lid Lock", "photo_reference": "@id5", "note": "bent striker",
                    "condition_tokens": ["Dented", "Bent", "Torn"], "condition": "Dented/bent/torn",
                    "damage_type": "Dent", "confidence": "Low"}], got
    for bad in ("[]", "{}", '{"items": "none"}', "not json"):
        try:
            sl.parse_answer(bad, {"Boot Lid Lock"}, sent)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should fail the call")
    ok("answers kept to the candidates, a photo it was sent, the firm's words (3 at most), each part once")


def test_candidates_and_photos() -> None:
    print("\n  candidates and photos")
    db = SessionLocal()
    try:
        listed = {"Boot Lid", "Rear Bumper"}
        cands, scope = sl.candidates(db, make=MAKE, model=MODEL, listed=listed, dismissed={"Rear End Panel"},
                                     sorter_parts=["Front Bumper", "Left Rear Bumper Reflector"])
        names = [c.part for c in cands]
        kit = [c.part for c in cands if c.source == "kit"]
        assert scope == f"past {MAKE} {MODEL} cases", scope
        assert kit[:2] == ["Boot Lid Hinge", "Boot Lid Lock"] or set(kit) == {"Boot Lid Hinge", "Boot Lid Lock"}, kit
        hinge = next(c for c in cands if c.part == "Boot Lid Hinge")
        assert "often replaced with Boot Lid" in hinge.reason and any("hinge" in h.lower() for h in hinge.hints), hinge
        assert "Rear Windscreen" not in kit, kit
        assert not (listed & set(names)) and "Rear End Panel" not in names and "Front Bumper" not in names
        assert names.index("Left Rear Bumper Reflector") < names.index("Rear Number Plate"), names
        assert "Rear Bumper Reflector (side undetermined)" in names and len(names) <= sl.MAX_CANDIDATES
        ok("kit (same make) first, then what the sorter saw near the damage, then the zone; nothing listed, "
           "dismissed or in another zone")
        other, scope = sl.candidates(db, make="Unknownmotors", model="X", listed=listed, dismissed=set(), sorter_parts=[])
        assert scope is None and all(c.source == "nearby" for c in other) and "Rear Windscreen" in [c.part for c in other]
        ok("no history of the make: no kit candidates (another make's kit is no guide), only the parts nearby")
        front, _ = sl.candidates(db, make=None, model=None, listed={"Left Front Door"}, dismissed=set(), sorter_parts=[])
        assert "Left Wing Mirror" in [c.part for c in front] and "Right Front Door" not in [c.part for c in front]
        ok("a side hit's candidates are that side's parts")
    finally:
        db.close()

    def photo(i, kind="damage", area="rear", part=None, damage=True, **kw):
        return SimpleNamespace(id=f"x{i}", kind=kind, area=area, part=part, shows_damage=damage,
                               quality=kw.get("quality", "good"), excluded=kw.get("excluded", False))

    photos = [photo(i, part="Rear Bumper") for i in range(20)]
    photos += [photo(20, part="Boot Lid Lock", damage=False), photo(21, area="front", part="Front Bumper"),
               photo(22, kind="overview"), photo(23, excluded=True), photo(24, quality="unusable"),
               photo(25, kind="log_card", area=None), photo(26, part="Boot Lid", damage=False)]
    picked = sl.pick_photos(photos, zones={"rear"}, read_ids={f"x{i}" for i in range(10)},
                            candidate_parts={"Boot Lid Lock"}, item_photo_ids={"x0"})
    ids = [p.id for p in picked]
    assert len(picked) == sl.MAX_PHOTOS and ids == sorted(ids, key=lambda i: int(i[1:]))
    assert "x20" in ids and not {"x21", "x22", "x23", "x24", "x25"} & set(ids)
    assert {f"x{i}" for i in range(10, 20)} <= set(ids), ids
    ok(f"at most {sl.MAX_PHOTOS} photos of the damaged zone, in camera order: a candidate's photo first, then "
       "damage the reading never saw; never excluded, unusable, document or other-zone photos")


# ------------------------------------------------------------ through the API


def wait_done(client: TestClient, rid: str, timeout: float = 60) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        state = client.get(f"/intake/{rid}").json()
        if state["stage"] in ("ready", "failed"):
            return state
        time.sleep(0.1)
    raise AssertionError("processing timed out")


def new_draft(client: TestClient, files: list[Path]) -> str:
    rid = client.post("/intake").json()["id"]
    for i, f in enumerate(files):
        with open(f, "rb") as fh:
            r = client.post(f"/intake/{rid}/photos", files={"file": (f.name, fh, "image/jpeg")}, data={"position": str(i)})
        assert r.status_code == 200, r.text
    client.put(f"/intake/{rid}/data", json={"data": {"description": "", "reporter_name": SECRET_NAME,
                                                       "reporter_contact": SECRET_CONTACT},
                                              "reporter_notes": SECRET_NOTES})
    return rid


def tick(data: dict, suggestion: dict) -> dict:
    """What SecondLookPanel's "Add ticked parts" does to the wizard's form:
    the damage item a person adds, condition from the suggestion's words."""
    item = {
        "part": suggestion["part"], "damage_type": suggestion.get("damage_type"),
        "condition_tokens": suggestion["condition_tokens"], "condition": suggestion.get("condition"),
        "severity": None, "remarks": None, "photo_reference": suggestion["photo_reference"], "bbox_2d": None,
        "ai_confidence": suggestion.get("confidence"), "human_verified": True,
    }
    return {**data, "damage_summary": [*(data.get("damage_summary") or []), item]}


def run_rows(rid: str) -> list[tuple[str, str]]:
    db = SessionLocal()
    try:
        rows = db.query(AiRun).filter(AiRun.report_id == rid).order_by(AiRun.started_at, AiRun.id).all()
        return [(r.kind, r.status) for r in rows]
    finally:
        db.close()


def test_flow(client: TestClient, files: list[Path]) -> None:
    print("\n  one call per reading, suggestions only")
    rid = new_draft(client, files)
    assert client.post(f"/intake/{rid}/process", json={"mode": "all"}).status_code == 200
    state = wait_done(client, rid)
    assert state["stage"] == "ready", state
    assert calls["look"] == 1 and len(looks) == 1, calls
    data = state["data"]
    look = data["second_look"]
    assert look["status"] == "ok" and look["model"] == CHAIN[0] and look["note"] is None, look
    assert look["scope"] == f"past {MAKE} {MODEL} cases" and look["photos_sent"] == len(looks[0]["labels"])
    parts = [it["part"] for it in look["items"]]
    assert parts == ["Boot Lid Lock", "Rear End Panel", "Boot Lid Hinge"], parts
    lock = look["items"][0]
    p05 = next(p for p in state["photos"] if p["label"] == "P05")
    assert lock["photo_reference"] == f"@{p05['id']}" and lock["condition"] == "Bent" and lock["source"] == "kit"
    assert lock["name"] and lock["name"] != "Boot Lid Lock" and lock["covers"] == ["Boot Lid Lock"], lock
    assert next(it for it in look["items"] if it["part"] == "Rear End Panel")["source"] == "nearby"
    ok("one second-look call on the second-look chain; 3 suggestions, surest first, in the firm's words, "
       "with photo and source")
    assert [it["part"] for it in data["damage_summary"]] == ["Rear Bumper", "Boot Lid", "Left Tail Lamp"], data["damage_summary"]
    assert data["damaged_parts"] == ["Rear Bumper", "Boot Lid", "Left Tail Lamp"]
    ok("never added by the machine: the damage list is the reading's")
    enum = looks[0]["schema"]["properties"]["items"]["items"]["properties"]["part"]["enum"]
    assert not {"Rear Bumper", "Boot Lid", "Left Tail Lamp", "Tail Lamp (side undetermined)"} & set(enum), enum
    assert "Rear Bumper" not in parts and "Left Rear Bumper Reflector" not in parts
    ok("listed parts are not asked about, and an answer naming one (or a photo not sent) is dropped")
    sent = looks[0]["labels"]
    assert sent and "P01" not in sent and "P02" not in sent and "P08" not in sent and len(sent) <= sl.MAX_PHOTOS, sent
    ok(f"photos sent: the rear's damage photos {sent}, not the log card, the overview or the front")
    assert ("second_look", "ok") in run_rows(rid) and run_rows(rid)[-1] == ("second_look", "ok"), run_rows(rid)
    ok("the AI run log keeps it as its own row, kind second_look")

    print("\n  a tick adds the part, as a person's")
    data = tick(data, lock)
    data["second_look"] = {**look, "dismissed": ["Boot Lid Hinge"]}
    r = client.put(f"/intake/{rid}/data", json={"data": data})
    assert r.status_code == 200, r.text
    saved = client.get(f"/intake/{rid}").json()["data"]
    assert saved["second_look"]["dismissed"] == ["Boot Lid Hinge"] and len(saved["second_look"]["items"]) == 3
    added = saved["damage_summary"][-1]
    assert added["part"] == "Boot Lid Lock" and added["human_verified"] is True and added["condition"] == "Bent"
    ok("the wizard's autosave keeps the suggestions and the dismissed list; the ticked part is a checked damage item")

    print("\n  a re-read runs it again and keeps what a person did")
    behaviour["look"] = "ok"
    assert client.post(f"/intake/{rid}/process", json={"mode": "read"}).status_code == 200
    state = wait_done(client, rid)
    assert state["stage"] == "ready" and calls["look"] == 2, (state["stage"], calls)
    data = state["data"]
    lock_items = [it for it in data["damage_summary"] if it["part"] == "Boot Lid Lock"]
    assert len(lock_items) == 1 and lock_items[0]["human_verified"], data["damage_summary"]
    look = data["second_look"]
    assert look["status"] == "ok" and look["dismissed"] == ["Boot Lid Hinge"], look
    assert [it["part"] for it in look["items"]] == ["Rear End Panel"], look["items"]
    enum = looks[-1]["schema"]["properties"]["items"]["items"]["properties"]["part"]["enum"]
    assert "Boot Lid Lock" not in enum and "Boot Lid Hinge" not in enum
    assert [k for k, _ in run_rows(rid)][-2:] == ["redraft", "second_look"], run_rows(rid)
    ok("one more call; the ticked part stays (checked); a dismissed part is neither asked about nor listed")

    print("\n  filing: Annex A follows the tick")
    r = client.post(f"/intake/{rid}/finalize", json={"data": data})
    assert r.status_code == 200, r.text
    detail = client.get(f"/reports/{rid}").json()
    rows = detail["data"]["annex_a"]["items"]
    sources = {row.get("source_part") for row in rows}
    assert "Boot Lid Lock" in sources, sorted(s for s in sources if s)
    assert "Rear End Panel" not in sources and "Boot Lid Hinge" not in sources
    ok("the ticked part has its Annex A rows; parts only suggested have none")
    assert "second_look" not in detail["data"], "the filed case keeps no second-look suggestions"
    ok("the filed case keeps no second-look suggestions (their photo links were the intake's)")


def test_failures(client: TestClient, files: list[Path]) -> None:
    print("\n  failures leave the intake as the reading made it")
    for mode, status, note in (("fail", "failed", sl.NOTE_FAILED), ("day", "skipped", sl.NOTE_NO_ALLOWANCE)):
        behaviour["look"] = mode
        rid = new_draft(client, files)
        before = calls["look"]
        client.post(f"/intake/{rid}/process", json={"mode": "all"})
        state = wait_done(client, rid)
        assert state["stage"] == "ready" and state["error"] is None, state
        assert [it["part"] for it in state["data"]["damage_summary"]] == ["Rear Bumper", "Boot Lid", "Left Tail Lamp"]
        look = state["data"]["second_look"]
        assert (look["status"], look["note"], look["items"]) == (status, note, []), look
        assert calls["look"] - before == len(CHAIN), calls
        assert run_rows(rid)[-1] == ("second_look", "failed"), run_rows(rid)
        ok(f"every model failing ({mode}): the draft is ready, the second look '{status}' with a quiet note, its run failed")
        if mode == "day":
            # The day is remembered as used up (ai/allowance.py): forgotten
            # here so the checks below are asked again.
            from app.ai import allowance
            from app.ai.keys import company_keys
            for k in company_keys():
                allowance.forget(k.fingerprint, limits=True)
    behaviour["look"] = "ok"

    saved_box = sl.TIME_BOX
    sl.TIME_BOX, behaviour["delay"] = 1.0, 2.5
    try:
        rid = new_draft(client, files)
        before = calls["look"]
        started = time.monotonic()
        client.post(f"/intake/{rid}/process", json={"mode": "all"})
        state = wait_done(client, rid)
        took = time.monotonic() - started
        assert state["stage"] == "ready" and took < 2.4, (state["stage"], took)
        look = state["data"]["second_look"]
        assert look["status"] == "failed" and look["note"] == sl.NOTE_TIMEOUT and look["items"] == []
        time.sleep(2.0)  # the call given up on finishes, and must not try the next model
        assert calls["look"] - before == 1, calls
        ok(f"no answer within the time box: ready after {took:.1f}s, 'failed', and the chain stops there (1 call)")
    finally:
        sl.TIME_BOX, behaviour["delay"] = saved_box, 0.0

    real_look = sl.look

    def no_allowance(**_kw):
        raise AiUnavailable("ai_quota_used")

    sl.look = no_allowance
    try:
        rid = new_draft(client, files)
        client.post(f"/intake/{rid}/process", json={"mode": "all"})
        state = wait_done(client, rid)
        look = state["data"]["second_look"]
        assert state["stage"] == "ready" and look["status"] == "skipped" and look["note"] == sl.NOTE_NO_ALLOWANCE, look
        assert run_rows(rid)[-1] == ("second_look", "refused"), run_rows(rid)
        ok("no AI allowance: 'skipped', the draft ready")
    finally:
        sl.look = real_look

    settings.second_look_enabled = False
    try:
        rid = new_draft(client, files)
        before = calls["look"]
        client.post(f"/intake/{rid}/process", json={"mode": "all"})
        state = wait_done(client, rid)
        look = state["data"]["second_look"]
        assert state["stage"] == "ready" and calls["look"] == before and look["status"] == "skipped" and look["note"] == sl.NOTE_OFF
        assert "second_look" not in [k for k, _ in run_rows(rid)]
        ok("switched off: no call, no run row, 'skipped'")
    finally:
        settings.second_look_enabled = True

    # Parts listed between the call and the save (the store checks the form
    # as it is then) are dropped too.
    rid = new_draft(client, files)
    client.post(f"/intake/{rid}/process", json={"mode": "all"})
    wait_done(client, rid)
    sl._store(rid, {"status": "ok", "items": [
        {"part": "Left Tail Lamp", "photo_reference": None, "condition_tokens": [], "confidence": "High", "source": "nearby"},
        {"part": "Right Tail Lamp", "photo_reference": None, "condition_tokens": [], "confidence": "High", "source": "nearby"},
        {"part": "Rear Bumper", "photo_reference": None, "condition_tokens": [], "confidence": "High", "source": "nearby"},
        {"part": "Quarter Panel (side undetermined)", "photo_reference": None, "condition_tokens": [], "confidence": "Low",
         "source": "nearby"},
        {"part": "Left Rear Quarter Panel", "photo_reference": None, "condition_tokens": [], "confidence": "Medium",
         "source": "nearby"},
    ]})
    look = client.get(f"/intake/{rid}").json()["data"]["second_look"]
    assert [it["part"] for it in look["items"]] == ["Right Tail Lamp", "Left Rear Quarter Panel"], look["items"]
    ok("at saving, parts already listed (by canonical part and side) are dropped, and one part seen twice "
       "(a side and side undetermined) is kept once, the surer")

    nothing = gen_id()
    db = SessionLocal()
    try:
        db.add(Report(id=nothing, status="intake", channel="dashboard", reporter_chat_id="test",
                      data={"description": "", "category": [], "damage_summary": []}))
        db.add(IntakeSession(report_id=nothing, stage="reading"))
        db.commit()
    finally:
        db.close()
    before = calls["look"]
    sl.run_for_intake(nothing, AiActor.cli())
    look = _data(nothing)["second_look"]
    assert calls["look"] == before
    assert look["status"] == "skipped" and look["note"] == sl.NOTE_NOTHING_FOUND
    ok("no damage found: nothing to look around, no call")


def test_public_case(files: list[Path]) -> None:
    print("\n  a public report: the same key rule, nothing of the sender's")
    rid = gen_id()
    db = SessionLocal()
    try:
        # As a staff member's first read would have left it: the sender's
        # words in the description, their name and number, a plate.
        db.add(Report(id=rid, status="intake", channel=PUBLIC_CHANNEL, reporter_chat_id="public",
                      data={"description": SECRET_NOTES, "category": [], "reporter_name": SECRET_NAME,
                            "reporter_contact": SECRET_CONTACT,
                            "vehicle_info": {"make": MAKE, "model": MODEL, "plate_number": SECRET_PLATE},
                            "damage_summary": [{"part": "Boot Lid", "photo_reference": f"@{rid}-3"}]}))
        db.add(IntakeSession(report_id=rid, stage="reading", reporter_notes=SECRET_NOTES))
        folder = Path(_tmp, "public", rid)
        folder.mkdir(parents=True)
        for i, f in enumerate(files):
            target = folder / f.name
            target.write_bytes(f.read_bytes())
            kind, area, part, damage = SORT[f"P{i + 1:02d}"]
            db.add(IntakePhoto(id=f"{rid}-{i}", report_id=rid, position=i, original_name=f.name, path=str(target),
                               sha256=f"{rid}{i}", kind=kind, area=area, part=part, shows_damage=damage, quality="good",
                               note=f"close-up near {SECRET_PLATE}"))
        db.commit()
    finally:
        db.close()

    seen: list[tuple[str, bool]] = []
    real_resolve = structured.resolve_keys

    def recording(actor, **kw):
        seen.append((threading.current_thread().name, bool(actor.public_case)))
        return real_resolve(actor, **kw)

    structured.resolve_keys = recording
    actor = AiActor(user_id=None, channel="cli", cli=True, public_case=True)
    try:
        before = calls["look"]
        try:
            pipeline._read(rid, actor)
            raise AssertionError("the read should be refused without billing")
        except AiUnavailable:
            pass
        sl.run_for_intake(rid, actor)
        look = _data(rid)["second_look"]
        assert calls["look"] == before and look["status"] == "skipped" and look["note"] == sl.NOTE_NO_ALLOWANCE, look
        assert seen and seen[-1] == ("second-look", True), seen
        ok("company key not billed: the reading is refused and so is the second look (no call)")

        real_billed = keys._company_billed
        keys._company_billed = lambda db: True
        try:
            seen.clear()
            pipeline._read(rid, actor)
        finally:
            keys._company_billed = real_billed
        assert calls["look"] == before + 1 and seen and all(public for _, public in seen), seen
        assert any(name == "second-look" for name, _ in seen), seen
        ok("billed: the reading and the second look both resolve keys for the public case")
        text = "\n".join(looks[-1]["texts"]) + looks[-1]["system"]
        for secret in (SECRET_NOTES, "NOTES-MARKER", SECRET_NAME, SECRET_CONTACT, SECRET_PLATE, "91234567", "98765432"):
            assert secret not in text, secret
        assert MAKE in text and "Already listed" in text
        ok("its text holds the make, model and part names -- no notes, name, contact, plate or number")
    finally:
        structured.resolve_keys = real_resolve


def _data(rid: str) -> dict:
    db = SessionLocal()
    try:
        return db.get(Report, rid).data or {}
    finally:
        db.close()


def main() -> int:
    init_db()
    # This file opens more drafts than one person may keep open (each
    # failure mode is a draft of its own); the limit isn't what it tests.
    from app.api import limits
    limits.MAX_OPEN_DRAFTS_PER_PERSON = 50
    import_history()
    test_vocabulary()
    test_candidates_and_photos()
    files = photo_files()
    with TestClient(app) as client:
        sign_in(client)
        test_flow(client, files)
        test_failures(client, files)
    test_public_case(files)
    print("\nALL SECOND LOOK CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
