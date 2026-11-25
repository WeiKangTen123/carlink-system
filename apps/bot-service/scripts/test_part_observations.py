"""Part observations and the knowledge stamp (step 2B: app/reports/
observations.py, app/reports/knowledge.py, app/api/observations.py,
scripts/backfill_part_observations.py).

    python scripts/test_part_observations.py

Checked: observe() on a crafted case -- an AI-found part kept (with its
fittings, no price), one removed, one added by hand, one added from a kit
suggestion and one from a linked part (prompted), a suggestion dismissed,
two left (a link and a parts-catalogue link), a typed "Other" name
scrubbed; the area, side, hit, condition words, Repair/Replace; the car's
impact from where the signed-off parts are. A sign-off writes the rows with
the car's fields (make, model key, body, year band; vehicle and impact
confirmed only when a person confirmed them) and the knowledge stamp, and
NO plate, VIN, name, note, photo or price anywhere in them; a re-sign-off
replaces them; a reopen and a delete remove them; a failing capture (or a
failing stamp) never fails the sign-off. Annex A lines as Stage 1 keeps
them (a line id, price source, confirm and book price on each) give the
same fittings, and none of those fields reaches a row; a waiting price
blocks the sign-off, so nothing is captured. The backfill: a dry run writes
nothing and prints counts only; --apply writes, again changes nothing, and
takes out the rows of a case no longer signed off. Retention: rows over 7
years old and rows without a signed-off case go, from the cleanup sweep
too. The stamp on ai_runs (at the start; the answering models at the end)
and on the case. GET /admin/observations/summary: admins only, the counts,
the date range, no case or name. An old database gets the table and the
column from init_db.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database, the PDF render replaced by a stub, Gemini by a fake that fails the
test if anything calls it, and every outside connection refused. Only
made-up data: no real plate, name or report value.
"""
import contextlib
import hashlib
import io
import json
import os
import socket
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
_tmp = Path(tempfile.mkdtemp(prefix="part-obs-test-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
os.environ["STORAGE_DIR"] = str(STORAGE)
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
DB_FILE = _tmp / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{DB_FILE.as_posix()}"
os.environ["GEMINI_API_KEY"] = ""
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ["MIN_FREE_DISK_GB"] = "0"

_real_connect = socket.socket.connect


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConnectionRefusedError(f"test is offline: refused a connection to {address!r}")
    return _real_connect(sock, address)


socket.socket.connect = _offline_connect

import app.ai.structured as structured  # noqa: E402

GEMINI: list[str] = []


def _no_gemini(key=None):
    GEMINI.append("get_client")
    raise AssertionError("Gemini was called")


structured.get_client = _no_gemini

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402

import app.api.main as main  # noqa: E402
import backfill_part_observations  # noqa: E402
from app.ai import runs  # noqa: E402
from app.auth.core import SESSION_COOKIE, create_session  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import car_map, cleanup, links  # noqa: E402
from app.reports import db as db_module  # noqa: E402
from app.reports import knowledge, observations  # noqa: E402
from app.reports.db import SessionLocal, engine, init_db  # noqa: E402
from app.reports.models import AiAccuracy, AiRun, PartObservation, Report, User, gen_id  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402
from app.reports.taxonomy import OTHER_PART  # noqa: E402


def _fake_render(data, photo_paths, output_path, report_id=None, **_kw):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(b"%PDF-1.4\n% test\n%%EOF\n")


main.render_pdf = _fake_render
app = main.app

# ---------------------------------------------------------------- harness
PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}
BASE = "https://carlink.test"
W = SimpleNamespace()
SG = timezone(timedelta(hours=8))

# Made-up identifiers: none of them may reach a row.
MAKE, MODEL = "Alphamotors", "Modelone Turbo"
PLATE = "SZZ 9876 Q"
VIN = "ZZTESTVIN00000001"
ENGINE = "ENGZZ55501"
DRIVER = "Quentin Zarkowski"
REPORTER = "Ophelia Brannigan"
OWNER = "Mortimer Fenwick"
WITNESS = "Prudence Okonkwo"
SIGNER = "Adalbert Testsignerson"
PHONE = "+65 9000 0001"
NOTE_WORDS = ("Kumquat", "Gooseberry", "Xylophone")
PRICE = 1234.56
FORBIDDEN_TEXT = [PLATE.lower(), "szz9876q", VIN.lower(), ENGINE.lower(), "9000 0001", "90000001", "1234.56",
                  '"p01"', '"p02"'] + [w.lower() for w in NOTE_WORDS] \
    + [w.lower() for n in (DRIVER, REPORTER, OWNER, WITNESS, SIGNER) for w in n.split()]
FORBIDDEN_KEYS = {"plate", "plate_number", "vin", "engine_number", "chassis_number", "name", "driver_name", "note",
                  "notes", "remarks", "reason", "photo", "photo_reference", "evidence", "seen_in", "price",
                  "workshop_est", "unit_price", "adjusted_cost", "discount_pct", "email"}


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


def brief(r) -> str:
    return f"{r.status_code} {r.text[:300]}"


def new_user(name: str, *, role: str = "user", **profile) -> str:
    db = SessionLocal()
    try:
        user = User(email=f"{name.split()[0].lower()}-{gen_id()}@example.com", username=f"u{gen_id()[:8]}",
                    name=name, role=role, password_hash="!", **profile)
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


def signed_in(user_id: str) -> TestClient:
    c = TestClient(app, base_url=BASE)
    db = SessionLocal()
    try:
        token = create_session(db, db.get(User, user_id))
        db.commit()
    finally:
        db.close()
    c.cookies.set(SESSION_COOKIE, token)
    return c


def item(part: str, **extra) -> dict:
    return {"part": part, **extra}


def row(name: str, est: float = 0.0, **extra) -> dict:
    out = {"part_name": name, "condition": "", "action": "", "qty": 1.0, "qty_text": None, "workshop_est": est,
           "discount_pct": 0.2, "adjusted_cost": round(est * 0.8, 2), "is_net_item": False, "prefilled": False,
           "origin": "typed", "source_part": None, "stale": False}
    out.update(extra)
    return out


def blank() -> dict:
    return SecurityIncidentDraft(description="Made-up test case.", category=["Vehicle Collision or Damage"]).model_dump()


OTHER_TYPED = "Rear air vent SZZ9876Q Zarkowski"
AI_DAMAGE = [
    item("Rear Bumper", remarks="Replace", condition_tokens=["Deformed"], condition="Deformed",
         found_by=["overall", "zone:rear"], reason="AI reason Kumquat", photo_reference="P01", seen_in=["P02"]),
    item("Bonnet", condition_tokens=["Dented"], found_by=["zone:front"], photo_reference="P02"),
]
FINAL_DAMAGE = [
    item("Rear Bumper", remarks="Replace", condition_tokens=["Deformed"], condition="Deformed", human_verified=True,
         found_by=["overall", "zone:rear"], reason="AI reason Kumquat", photo_reference="P01"),
    item("Right Tail Lamp", remarks="Replace", condition_tokens=["Cracked"], condition="Cracked", human_verified=True),
    item("Rear Bumper Beam", added_from="suggestion", linked_from=["Rear Bumper"], link_type="behind", human_verified=True),
    item("Boot Lid", added_from="suggestion", condition_tokens=["Dented"], condition="Dented", human_verified=True),
    item(OTHER_PART, other_part_name=OTHER_TYPED, remarks="Replace", human_verified=True),
]
SECOND_LOOK = {
    "items": [
        {"key": "Rear Bumper Beam", "part": "Rear Bumper Beam", "source": "link", "link_rule": "L1:rear-bumper>beam",
         "note": "Gooseberry note", "reason": "Kumquat reason", "photo_reference": "P01"},
        {"key": "Boot Lid", "part": "Boot Lid", "source": "kit", "found_by": ["overall"]},
        {"key": "Left Rear Bumper Reflector", "part": "Left Rear Bumper Reflector", "source": "zone",
         "found_by": ["zone:rear"], "condition_tokens": ["Cracked"]},
        {"key": "Rear End Panel", "part": "Rear End Panel", "source": "link",
         "link_rule": "L1:rear-bumper>rear-end-panel"},
        {"key": "Left Rear Wheel Arch Liner", "part": "Left Rear Wheel Arch Liner", "source": "link",
         "link_rule": "L1:oem>rear-wheel-arch-liner"},
    ],
    "decisions": [
        {"key": "Rear Bumper Beam", "part": "Rear Bumper Beam", "action": "added", "by_name": SIGNER, "at": "2026-11-20T01:00:00+00:00"},
        {"key": "Boot Lid", "part": "Boot Lid", "action": "added", "by_name": SIGNER, "at": "2026-11-20T01:01:00+00:00"},
        {"key": "Left Rear Bumper Reflector", "part": "Left Rear Bumper Reflector", "action": "dismissed",
         "by_name": SIGNER, "at": "2026-11-20T01:02:00+00:00"},
    ],
    "dismissed": ["Left Rear Bumper Reflector"],
}
OTHER_SOURCE = f"{OTHER_PART}: {OTHER_TYPED}"
ANNEX = [
    row("Rear bumper fascia", PRICE, condition="Deformed", action="Replace", source_part="Rear Bumper", main=True),
    row("Rear bumper clips", 50.0, condition="Necessary", qty=10.0, source_part="Rear Bumper"),
    row("Rear bumper side retainer RH", 40.0, qty=2.0, is_net_item=True, source_part="Rear Bumper"),
    row("Tail lamp RH", 400.0, source_part="Right Tail Lamp", main=True),
    row("Rear air vent", 80.0, source_part=OTHER_SOURCE, main=True),
    row("Vent clip SZZ9876Q", 5.0, condition="Necessary", qty=4.0, source_part=OTHER_SOURCE),
    row("Towing charges", 120.0),
]


def case_data(damage=None, *, ref="CL 90321") -> dict:
    data = blank()
    data["report_id"] = ref
    data["reporter_name"] = REPORTER
    data["reporter_contact"] = PHONE
    data["additional_comments"] = "Xylophone remark typed by a person"
    data["vehicle_info"].update(plate_number=PLATE, make=MAKE, model=MODEL, vin=VIN, engine_number=ENGINE,
                                driver_name=DRIVER, driver_contact=PHONE, date_of_registration="12th January 2021",
                                body_type="SUV", point_of_impact="Rear centre")
    data["people_involved"] = [{"name": OWNER, "role": "owner", "contact": PHONE}]
    data["witnesses"] = [{"name": WITNESS, "statement": "Saw it, Gooseberry"}]
    data["cover_page"]["date_of_survey"] = "3rd November 2026"
    data["damage_summary"] = [dict(it) for it in (FINAL_DAMAGE if damage is None else damage)]
    data["damaged_parts"] = [it["part"] for it in data["damage_summary"]]
    data["annex_a"] = {"items": [dict(r) for r in ANNEX]}
    data["second_look"] = json.loads(json.dumps(SECOND_LOOK))
    return data


def ai_draft() -> dict:
    draft = blank()
    draft["vehicle_info"].update(plate_number=PLATE, make=MAKE, model=MODEL, point_of_impact="Rear centre")
    draft["damage_summary"] = [dict(it) for it in AI_DAMAGE]
    draft["damaged_parts"] = [it["part"] for it in AI_DAMAGE]
    return draft


def new_case(data: dict, ai: dict | None, *, status: str = "confirmed") -> str:
    db = SessionLocal()
    try:
        r = Report(channel="manual", reporter_chat_id="dashboard", status=status, owner_id=W.admin_id,
                   data=data, photo_paths=[], ai_draft=ai)
        db.add(r)
        db.commit()
        return r.id
    finally:
        db.close()


_LAST_RUN_AT = [datetime.min.replace(tzinfo=timezone.utc)]


def add_run(report_id: str, model: str, kind: str = "read", status: str = "ok", knowledge=None) -> None:
    started = max(datetime.now(timezone.utc), _LAST_RUN_AT[0] + timedelta(milliseconds=1))
    _LAST_RUN_AT[0] = started
    db = SessionLocal()
    try:
        db.add(AiRun(report_id=report_id, kind=kind, channel="dashboard", status=status, model_used=model,
                     started_at=started, knowledge=knowledge))
        db.commit()
    finally:
        db.close()


# The versions a case's runs were made under ("stamp A"): never what is
# live when it is filed or signed off.
STAMP_A = {"library": None, "links": "L1:aaaaaaaa", "car_map": None, "prompt": "v1", "models": ["gemini-test-strong"]}
STAMP_SORT = {"library": None, "links": "L1:aaaaaaaa", "car_map": None, "prompt": None, "models": ["gemini-test-light"]}
WANT_A = {**STAMP_A, "models": ["gemini-test-light", "gemini-test-strong"]}


def drafted_case(**kw) -> str:
    report_id = new_case(case_data(**kw), ai_draft())
    add_run(report_id, "gemini-test-light", kind="sort", knowledge=STAMP_SORT)
    add_run(report_id, "gemini-test-light", knowledge={**STAMP_A, "links": "L1:00000000", "models": ["gemini-test-light"]})
    add_run(report_id, "gemini-test-strong", kind="redraft", knowledge=STAMP_A)
    add_run(report_id, "gemini-test-failed", status="failed", knowledge={**STAMP_A, "links": "L1:ffffffff"})
    return report_id


def sign_off(client: TestClient, report_id: str):
    from auth_helpers import mark_must_haves_na

    mark_must_haves_na(client, report_id)
    return client.post(f"/reports/{report_id}/sign-off", json={"terms_accepted": True, "open_items_ack": True})


_COLUMNS = [c.key for c in PartObservation.__table__.columns]


def obs_rows(report_id: str | None = None) -> list[dict]:
    db = SessionLocal()
    try:
        q = db.query(PartObservation)
        if report_id is not None:
            q = q.filter(PartObservation.report_id == report_id)
        return [{c: getattr(r, c) for c in _COLUMNS} for r in q.order_by(PartObservation.id)]
    finally:
        db.close()


def stored(report_id: str) -> Report | None:
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if r is not None:
            db.expunge(r)
        return r
    finally:
        db.close()


def by_part(rows: list[dict]) -> dict:
    return {(r["other_name"] if r["part"] == OTHER_PART else r["part"]): r for r in rows}


def lf_fingerprint() -> str:
    return hashlib.sha256(links.TABLE_PATH.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:8]


def leaks(rows: list[dict]) -> list[str]:
    """What of the case's identifiers, names, notes, photos or prices is in
    the rows, as text."""
    blob = json.dumps(rows, default=str).lower()
    return [t for t in FORBIDDEN_TEXT if t in blob]


# ================================================================= setup
def setup() -> None:
    init_db()
    W.admin_id = new_user(SIGNER, role="admin", may_sign_off=True)
    W.admin = signed_in(W.admin_id)
    W.plain_id = new_user("Pat Plainuser")
    # An account on no case: its name must still never be stored (M1).
    W.other_id = new_user("Barnaby Whitlock", sees_all_cases=True)
    W.plain = signed_in(W.plain_id)
    W.links_before, W.car_map_before = settings.intake_links, settings.intake_car_map
    check(W.admin.get("/auth/me").status_code == 200, "an admin signer and a plain user are signed in")


# ============================================================ observe()
def observe_outcomes() -> None:
    secrets = ["szz9876q", "quentinzarkowski"]
    rows = observations.observe(case_data(), ai_draft(), secrets, {"quentin", "zarkowski"})
    got = by_part(rows)
    check(len(rows) == 9, "one row per part decision: 1 kept, 1 removed, 4 added, 1 dismissed, 2 left",
          [(r["part"], r["outcome"]) for r in rows])
    bumper = got.get("Rear Bumper") or {}
    check((bumper.get("outcome"), bumper.get("how"), bumper.get("found_by"), bumper.get("prompted"))
          == ("kept", "ai_read", ["overall", "zone:rear"], False), "an AI-found part kept: by the overall read", bumper)
    check((bumper.get("family"), bumper.get("side"), bumper.get("area"), bumper.get("hit"), bumper.get("action"),
           bumper.get("condition_words")) == ("Rear Bumper", None, "rear", "heavy", "Replace", ["Deformed"]),
          "its family, side, area, hit (links.hit_of), Repair/Replace and condition words", bumper)
    check(bumper.get("fittings") == [
        {"wording": "rear bumper clips", "qty": 10.0, "section": "list", "necessary": True},
        {"wording": "rear bumper side retainer", "qty": 2.0, "section": "nett", "necessary": False},
    ], "its fittings: the Annex A lines with it as source_part, not its own main row; no price", bumper.get("fittings"))
    bonnet = got.get("Bonnet") or {}
    check((bonnet.get("outcome"), bonnet.get("how"), bonnet.get("found_by"), bonnet.get("area"), bonnet.get("fittings"))
          == ("removed", "zone", ["zone:front"], "front", []), "an AI-found part removed: only a zone read found it", bonnet)
    lamp = got.get("Right Tail Lamp") or {}
    check((lamp.get("outcome"), lamp.get("how"), lamp.get("prompted"), lamp.get("family"), lamp.get("side"),
           lamp.get("fittings")) == ("added", "hand_added", False, "Tail Lamp", "Right", []),
          "a part a person added by hand: unprompted, its side, no fitting but its own main row", lamp)
    beam = got.get("Rear Bumper Beam") or {}
    check((beam.get("outcome"), beam.get("how"), beam.get("source"), beam.get("link_rule"), beam.get("prompted"))
          == ("added", "link", "link", "L1:rear-bumper>beam", True), "a linked part added: prompted, with its rule", beam)
    boot = got.get("Boot Lid") or {}
    check((boot.get("outcome"), boot.get("how"), boot.get("source"), boot.get("prompted"), boot.get("found_by"))
          == ("added", "suggestion", "kit", True, ["overall"]), "a kit suggestion accepted: prompted, its source", boot)
    other = got.get("rear air vent") or {}
    check((other.get("part"), other.get("other_name"), other.get("family"), other.get("area"), other.get("how"))
          == (OTHER_PART, "rear air vent", "Rear Bumper", "rear", "hand_added"),
          "a typed Other: normalised and scrubbed (no plate, no name), placed by its family", other)
    check(other.get("fittings") == [{"wording": "vent clip", "qty": 4.0, "section": "list", "necessary": True}],
          "the Other's fitting, its wording scrubbed too", other.get("fittings"))
    reflector = got.get("Left Rear Bumper Reflector") or {}
    check((reflector.get("outcome"), reflector.get("how"), reflector.get("source"), reflector.get("side"),
           reflector.get("prompted")) == ("dismissed", "suggestion", "zone", "Left", False),
          "a zone suggestion dismissed (Not damaged)", reflector)
    panel = got.get("Rear End Panel") or {}
    liner = got.get("Left Rear Wheel Arch Liner") or {}
    check((panel.get("outcome"), panel.get("how"), panel.get("link_rule")) == ("left", "link", "L1:rear-bumper>rear-end-panel"),
          "a linked part nobody decided: left", panel)
    check((liner.get("outcome"), liner.get("how"), liner.get("source")) == ("left", "catalogue", "link"),
          "a link from a parts-catalogue diagram (rule oem>...): how catalogue", liner)
    check(all(r["kind"] == "part" and r["component_key"] is None for r in rows), "kind part; component_key empty (3B fills it)")
    check(all(r["how"] in observations.HOWS and r["outcome"] in observations.OUTCOMES for r in rows), "known hows and outcomes only")
    check(not leaks(rows), "no identifier, name, note, photo or price in the rows", leaks(rows))
    check(all(not (set(r) & FORBIDDEN_KEYS) for r in rows), "no such field either", [set(r) & FORBIDDEN_KEYS for r in rows])

    placed = [(r["area"], r["side"]) for r in rows if r["outcome"] in ("kept", "added")]
    check(observations.impact_of(placed) == ("rear", "right"), "the impact from the signed-off parts: rear, right", placed)
    check(observations.impact_of([("front", None), ("rear", "Left"), ("rear", "Right")]) == ("both", "both")
          and observations.impact_of([("side", "Left")]) == ("side", "left")
          and observations.impact_of([("roof", None)]) == ("other", "centre")
          and observations.impact_of([]) == (None, None), "both ends, the side, elsewhere, nothing")
    check(observations.year_band({"date_of_registration": "12th January 2021"}) == "2020-24"
          and observations.year_band({"date_of_registration": "2019-03-01"}) == "2015-19"
          and observations.year_band({"year": "2016"}) == "2015-19"
          and observations.year_band({"date_of_registration": "not known"}) is None, "year bands from the registration date")
    check([observations.area_of(p) for p in ("Left Front Door", "Roof", "Seat", "Wheel (side undetermined)",
                                              "Underbody / Chassis", OTHER_PART, None)]
          == ["side", "roof", "cabin", "wheel", "underbody", None, None], "the areas of parts at neither end")

    # Scrubbing on its own; a pre-taxonomy free-text part.
    check(observations.scrub_words("Wing mirror cover Lee Wing Kei abc@x.com +65 9123 4567", [], {"lee", "kei"})
          == "wing mirror cover wing", "a name's words go, a part word (Wing) stays; e-mails and numbers go")
    check(observations.scrub_words("SZZ 9876 Q", ["szz9876q"]) is None, "nothing left: None")
    old = observations.observe({"damage_summary": [item("Tail lamp LH"), item("Sticker SZZ9876Q")]}, {}, ["szz9876q"])
    check([(r["part"], r["other_name"], r["side"]) for r in old] == [("Left Tail Lamp", None, "Left"), (OTHER_PART, "sticker", None)],
          "free text from before the taxonomy: its canonical part, else a scrubbed Other", old)
    for ai, final in ((None, None), ({}, {}), ({"damage_summary": "x"}, {"damage_summary": [None, 3, {}], "second_look": "y"})):
        try:
            ok = observations.observe(final, ai) == []
        except Exception as exc:  # noqa: BLE001
            ok = exc
        check(ok is True, f"missing or broken data: no rows, no error ({str(final)[:30]})", ok)


# ============================================================ sign-off
def sign_off_writes() -> None:
    # What is live at sign-off differs from what the case was read under
    # (STAMP_A): the stamp must still say A.
    settings.intake_links = "on"
    settings.intake_car_map = "shadow"
    W.case = drafted_case()
    r = sign_off(W.admin, W.case)
    check(r.status_code == 200 and stored(W.case).status == "Signed Off", "the case signs off", brief(r))
    rows = obs_rows(W.case)
    check(len(rows) == 9, "the sign-off wrote its 9 rows", len(rows))
    if not rows:
        return
    first = rows[0]
    data = stored(W.case).data
    check((first["make"], first["model_key"], first["body_type"], first["year_band"]) == (MAKE, "modelone", "SUV", "2020-24"),
          "the car: make, the price book's model key, body type, year band", (first["make"], first["model_key"]))
    check(first["vehicle_confirmed"] is False and first["impact_confirmed"] is False,
          "make, model and point of impact still the AI's: not confirmed (recorded, filtered later)")
    check((first["impact_end"], first["impact_side"]) == ("rear", "right") and first["ai_drafted"] is True,
          "the impact from where the signed-off parts are", (first["impact_end"], first["impact_side"]))
    check(first["signed_by"] == W.admin_id and first["report_id"] == W.case and first["version"] == observations.VERSION,
          "signer's account id, the case id, the definitions' version")
    sign_at = datetime.fromisoformat(data["sign_off"]["signature_date"]).astimezone(timezone.utc).replace(tzinfo=None)
    check(abs((first["signed_off_at"] - sign_at).total_seconds()) < 1, "signed_off_at is the signature's time")
    live = knowledge.stamp()
    check(live["links"] == f"{links.TABLE_VERSION}:{lf_fingerprint()}" and live["car_map"],
          "(live now: another link table fingerprint, the car map in shadow)", live)
    check(data.get("knowledge") == WANT_A,
          "the case data carries the stamp it was READ under (its newest reading run's), not what is live at sign-off; "
          "the models that read it", data.get("knowledge"))
    check(all(r["knowledge"] == WANT_A for r in rows), "and every row the same stamp")
    got = by_part(rows)
    check({k: v["outcome"] for k, v in got.items()} == {
        "Rear Bumper": "kept", "Bonnet": "removed", "Right Tail Lamp": "added", "Rear Bumper Beam": "added",
        "Boot Lid": "added", "rear air vent": "added", "Left Rear Bumper Reflector": "dismissed",
        "Rear End Panel": "left", "Left Rear Wheel Arch Liner": "left"}, "the outcomes as stored",
          {k: v["outcome"] for k, v in got.items()})
    check(not leaks(rows), "NO plate, VIN, engine no., name, phone, note, photo or price in any stored row", leaks(rows))
    check(not (set(_COLUMNS) & FORBIDDEN_KEYS), "and no column for one", set(_COLUMNS) & FORBIDDEN_KEYS)
    check(all(set(f) == {"wording", "qty", "section", "necessary"} for r in rows for f in r["fittings"]),
          "fittings hold wording, qty, section and Necessary only")
    check(db_has_accuracy(W.case), "the AI accuracy record is written beside them")

    # Confirmed by a person: make, model and the point of impact ticked.
    case = drafted_case(ref="CL 90322")
    keys = ("vehicle_info.make", "vehicle_info.model", "vehicle_info.point_of_impact")
    r = W.admin.put(f"/reports/{case}/checked", json={"set": {k: {"na": False} for k in keys}})
    check(r.status_code == 200, "make, model and point of impact confirmed", brief(r))
    r = sign_off(W.admin, case)
    rows = obs_rows(case)
    check(r.status_code == 200 and rows and all(x["vehicle_confirmed"] and x["impact_confirmed"] for x in rows),
          "then vehicle_confirmed and impact_confirmed are recorded true", brief(r))
    W.confirmed_case = case

    # No AI draft: every part is a person's.
    plain = new_case(case_data(ref="CL 90323"), None)
    r = sign_off(W.admin, plain)
    rows = obs_rows(plain)
    check(r.status_code == 200 and rows and all(not x["ai_drafted"] and x["outcome"] in ("added", "dismissed", "left") for x in rows)
          and all(x["how"] == "hand_added" for x in rows if x["outcome"] == "added" and not x["prompted"]),
          "a case without an AI draft: rows marked ai_drafted false, its parts added", [(x["part"], x["outcome"]) for x in rows])
    W.no_draft = plain
    check(stored(plain).data.get("knowledge") == knowledge.empty() and all(x["knowledge"] == knowledge.empty() for x in rows),
          "no AI run at all: every field of the stamp null, never today's versions", stored(plain).data.get("knowledge"))

    # Runs from before the stamp existed: the models are known, nothing else.
    old = new_case(case_data(ref="CL 90329"), ai_draft())
    add_run(old, "gemini-test-old")
    r = sign_off(W.admin, old)
    want = {"library": None, "links": None, "car_map": None, "prompt": None, "models": ["gemini-test-old"]}
    check(r.status_code == 200 and stored(old).data.get("knowledge") == want
          and all(x["knowledge"] == want for x in obs_rows(old)),
          "a case read before the stamp existed: null fields (its models only)", stored(old).data.get("knowledge"))

    # Only a sort and a linked check were stamped: the newest of those, no
    # reading prompt.
    part = new_case(case_data(ref="CL 90330"), ai_draft())
    add_run(part, "gemini-test-light", kind="sort", knowledge=STAMP_SORT)
    add_run(part, "gemini-test-light")
    add_run(part, "gemini-test-light", kind="link_check", knowledge={**STAMP_SORT, "links": "L1:bbbbbbbb"})
    db = SessionLocal()
    try:
        got = knowledge.case_stamp(db, part)
    finally:
        db.close()
    check(got == {"library": None, "links": "L1:bbbbbbbb", "car_map": None, "prompt": None,
                  "models": ["gemini-test-light"]},
          "no stamped reading run: the newest stamped run of the draft's other steps, without a prompt", got)
    settings.intake_links = W.links_before
    settings.intake_car_map = W.car_map_before


def db_has_accuracy(report_id: str) -> bool:
    db = SessionLocal()
    try:
        return db.get(AiAccuracy, report_id) is not None
    finally:
        db.close()


def resign_and_remove() -> None:
    r = W.admin.post(f"/reports/{W.case}/reopen")
    check(r.status_code == 200 and obs_rows(W.case) == [], "a reopen removes the case's rows", brief(r))
    check("knowledge" not in (stored(W.case).data or {}), "and the case's stamp (made again at the next sign-off; L6)")
    db = SessionLocal()
    try:
        rep = db.get(Report, W.case)
        data = dict(rep.data)
        data["damage_summary"] = [d for d in data["damage_summary"] if d["part"] != "Boot Lid"]
        rep.data = data
        db.commit()
    finally:
        db.close()
    r = sign_off(W.admin, W.case)
    rows = obs_rows(W.case)
    got = by_part(rows)
    check(r.status_code == 200 and len(rows) == 9 and got.get("Boot Lid", {}).get("outcome") == "left"
          and len({x["part"] for x in rows}) == len(rows), "a re-sign-off replaces them: one set, the new answer "
          "(Boot Lid taken off the list: a suggestion shown, not on the list, not dismissed -- left)",
          [(x["part"], x["outcome"]) for x in rows])
    r = W.admin.delete(f"/reports/{W.confirmed_case}")
    check(r.status_code == 200 and obs_rows(W.confirmed_case) == [], "a delete removes them", brief(r))


def never_fails() -> None:
    def boom(*_a, **_kw):
        raise RuntimeError("capture exploded")

    saved = main.observations.capture
    main.observations.capture = boom
    try:
        case = drafted_case(ref="CL 90324")
        r = sign_off(W.admin, case)
        check(r.status_code == 200 and stored(case).status == "Signed Off" and obs_rows(case) == [] and db_has_accuracy(case),
              "a sign-off succeeds when the capture raises (logged); the accuracy record still written", brief(r))
    finally:
        main.observations.capture = saved
    saved = main.knowledge.case_stamp
    main.knowledge.case_stamp = boom
    try:
        case = drafted_case(ref="CL 90325")
        r = sign_off(W.admin, case)
        check(r.status_code == 200 and "knowledge" not in (stored(case).data or {}) and len(obs_rows(case)) == 9,
              "a sign-off succeeds when the stamp can't be made; the rows are still captured", brief(r))
    finally:
        main.knowledge.case_stamp = saved
    db = SessionLocal()
    try:
        out = observations.capture(db, W.case, "2026-01-01T00:00:00+00:00")
    finally:
        db.close()
    check(out is None and len(obs_rows(W.case)) == 9, "a capture for a sign-off that isn't the case's now writes nothing")


# ======================================================== M1: the scrubber
# The review's examples (review-2a-2b.md, 2B M1): each name or identifier
# typed into an "Other" name or an Annex A wording, and what must be stored.
REVIEW_PEOPLE = {
    "vehicle_info": {"driver_name": "Ng Wei Ming", "plate_number": "SZZ 9876 Q", "vin": VIN},
    "police_report": {"officer_name": "Azman bin Ismail"},
    "sign_off": {"prepared_by": "Kamal Hassan"},
    "third_party_info": {"driver_name": "Kok Wing", "plate_number": "BKA 9"},
    "insurance_details": {"workshop_assigned": "Zephyrine Motor Works"},
    "people_involved": [{"name": "Lucinda Featherstone", "role": "owner", "contact": PHONE}],
}
REVIEW_EXAMPLES = [
    ("Rear bumper Azman", "rear bumper"),          # the police officer
    ("Rear bumper Kamal", "rear bumper"),          # sign_off.prepared_by
    ("Hassan bumper", "bumper"),                   # sign_off.prepared_by
    ("Rear bumper Jason", "rear bumper"),          # a decision by another account
    ("Rear bumper Ng", "rear bumper"),             # a 2-letter surname (driver Ng Wei Ming)
    ("rear bumper per Mr Lim", "rear bumper"),     # a title, and a name on nobody's case
    ("Rear bumper Muñoz", "rear bumper"),      # a letter outside a-z
    ("rear bumper BKA", "rear bumper"),            # a short plate's letters
    ("rear bumper SGX 1234 A", "rear bumper"),     # another car's plate
    ("Door visor Kok Wing", "door visor"),         # a person named with a part word
    ("Rear bumper – Mr Lim’s quote", "rear bumper"),
    ("Side step (Ng)", "side step"),
    ("Encik Ahmad bumper bracket", "bumper bracket"),
    ("Puan Siti rear lamp", "rear lamp"),
    ("Mdm Tan Siew Lan rear bumper", "rear bumper"),
    ("Dr. Wong mirror", "mirror"),
    ("Bracket Halim bin Yusof", "bracket"),
    ("Zephyrine garnish", "garnish"),              # the workshop's name
    ("Featherstone bumper clip", "bumper clip"),   # a person involved
]
# What must stay: real part words, the trade's abbreviations.
KEPT_EXAMPLES = [
    ("Rear air vent LH", "rear air vent"),
    ("Front bumper ABS sensor", "front bumper abs sensor"),
    ("A pillar trim", "a pillar trim"),
    ("Low beam bulb", "low beam bulb"),
    ("Mudflap bracket", "mudflap bracket"),
    ("Tail gate garnish", "tailgate garnish"),
    ("Fog lamp cover", "fog lamp cover"),
    ("Wing mirror cover", "wing mirror cover"),
]


def review_case_data(ref: str) -> dict:
    data = case_data(damage=[item("Rear Bumper", remarks="Replace", condition_tokens=["Deformed"])]
                     + [item(OTHER_PART, other_part_name=typed) for typed, _ in REVIEW_EXAMPLES], ref=ref)
    for key, value in REVIEW_PEOPLE.items():
        data[key] = {**(data.get(key) or {}), **value} if isinstance(value, dict) else value
    data["second_look"]["decisions"][0]["by_name"] = "Jason Teo"
    # Annex A rows with a name typed in: their wordings scrubbed too.
    data["annex_a"]["items"] = [dict(r) for r in ANNEX] + [
        row("Clip per Mr Lim", 5.0, source_part=f"{OTHER_PART}: {REVIEW_EXAMPLES[0][0]}"),
        row("Retainer Azman", 5.0, source_part="Rear Bumper"),
    ]
    return data


def scrubber_m1() -> None:
    report = SimpleNamespace(id="reviewcase01", data=review_case_data("CL 90340"))
    db = SessionLocal()
    try:
        secrets, words = observations.case_secrets(report, db)
        _, words_alone = observations.case_secrets(report)
    finally:
        db.close()
    for typed, want in REVIEW_EXAMPLES:
        got = observations.scrub_words(typed, secrets, words)
        check(got == want, f"{typed!r} -> {want!r}", got)
    for typed, want in KEPT_EXAMPLES:
        got = observations.scrub_words(typed, secrets, words)
        check(got == want, f"a real part name stays: {typed!r} -> {want!r}", got)
    check("jason" in words_alone and "barnaby" in words and "barnaby" not in words_alone
          and observations.scrub_words("Rear bumper Barnaby", secrets, words) == "rear bumper",
          "a decision's name counts, and every account's name (Barnaby Whitlock, on no case)")
    check(observations.scrub_words("Rear bumper Jason") == "rear bumper jason"
          and observations.scrub_words("Door visor Kok Wing") == "door visor wing",
          "(without those sources a long name word would stay: the sources matter; a lone part word stays)")

    # End to end: signed off, every typed name scrubbed in the stored rows.
    case = new_case(review_case_data("CL 90340"), ai_draft())
    add_run(case, "gemini-test-strong", knowledge=STAMP_A)
    r = sign_off(W.admin, case)
    rows = obs_rows(case)
    others = sorted({x["other_name"] for x in rows if x["part"] == OTHER_PART})
    check(r.status_code == 200 and others == sorted({want for _, want in REVIEW_EXAMPLES}),
          "signed off: the stored Other names are the scrubbed ones only", others)
    names = {"azman", "ismail", "kamal", "hassan", "jason", "teo", "ng", "wei", "ming", "kok", "lim", "bka", "sgx",
             "mr", "mdm", "encik", "puan", "siti", "ahmad", "tan", "siew", "lan", "dr", "wong", "halim", "yusof",
             "zephyrine", "lucinda", "featherstone", "munoz", "mu", "oz", "quote", "per"}
    stored_words = {w for x in rows for text in [x["other_name"] or ""] + [f["wording"] for f in x["fittings"]]
                    for w in text.split()}
    check(not (stored_words & names), "no name word, title or plate letters in any stored name or fitting wording",
          stored_words & names)
    bumper = by_part(rows).get("Rear Bumper") or {}
    check([f["wording"] for f in bumper.get("fittings") or []]
          == ["rear bumper clips", "rear bumper side retainer", "retainer"],
          "a fitting's wording scrubbed the same way ('Retainer Azman' -> 'retainer')", bumper.get("fittings"))
    check(all(x["version"] == observations.VERSION == 2 for x in rows), "rows of the new definitions: version 2")


# ============================================================ L1, L8
def fittings_and_race() -> None:
    rows_ = [
        {"part_name": "Rear bumper fascia", "source_part": "Rear Bumper"},       # its own line, before "main"
        {"part_name": "Rear Bumper", "source_part": "Rear Bumper"},              # the old scaffold row
        {"part_name": "Rear bumper clips", "source_part": "Rear Bumper", "qty": 10, "condition": "Necessary"},
        {"part_name": "Tail gate assy", "source_part": "Boot Lid"},              # the hatch wording of Boot Lid
        {"part_name": "Tail gate gas stay", "source_part": "Boot Lid"},
        {"part_name": "Rear air vent", "source_part": f"{OTHER_PART}: Rear air vent"},
    ]
    check([f["wording"] for f in observations.fittings_of("Rear Bumper", rows_)] == ["rear bumper clips"],
          "a part's own line is no fitting, marked main or not (L1)")
    check([f["wording"] for f in observations.fittings_of("Boot Lid", rows_)] == ["tailgate gas stay"],
          "on a hatch too")
    check(observations.fittings_of(f"{OTHER_PART}: rear air vent", rows_) == [], "nor a named Other's own line")

    # A capture that finds the case changed between its first read and the
    # write lock (another sign-off, a reopen) writes nothing, and leaves
    # what is there (L8).
    case = drafted_case(ref="CL 90341")
    check(sign_off(W.admin, case).status_code == 200, "a case signed off")
    before = [r["id"] for r in obs_rows(case)]
    saved = observations.case_rows

    def meanwhile(db, report):
        out = saved(db, report)
        other = SessionLocal()
        try:
            rep = other.get(Report, case)
            data = dict(rep.data)
            data["sign_off"] = {**data["sign_off"], "signature_date": "2026-11-24T09:00:00+00:00"}
            rep.data = data
            other.commit()
        finally:
            other.close()
        return out

    observations.case_rows = meanwhile
    try:
        db = SessionLocal()
        try:
            out = observations.capture(db, case)
        finally:
            db.close()
    finally:
        observations.case_rows = saved
    check(out is None and before and [r["id"] for r in obs_rows(case)] == before,
          "a sign-off committed meanwhile wins: nothing written, the rows there kept (its DELETE rolled back)", out)


# ============================================================ Stage 1's lines
# What Stage 1 ("My prices") keeps on every Annex A line: its line id
# (app/reports/annex_lines.py) and where its price came from. None of it is
# a fitting's (wording, qty, section, Necessary), so none may reach a row.
STAGE1_KEYS = {"line_id", "price_source", "price_confirmed", "book_price", "price_date", "price_cases"}


def with_stage1_fields(data: dict, *, confirmed: bool = True) -> dict:
    """data with each Annex A line as Stage 1 leaves it: a line id (kept if
    it has one), and a price the machine filled in (a person's own, an
    approved or a book price) with the book's price beside it -- PRICE,
    which no row may hold."""
    for n, it in enumerate(data["annex_a"]["items"]):
        it.setdefault("line_id", f"L{n:012d}")
        it.update(price_source=("mine", "approved", "book")[n % 3], price_confirmed=confirmed,
                  book_price=PRICE, price_date="2026-11-20", price_cases=3)
    return data


def stage1_lines() -> None:
    plain = observations.observe(case_data(), ai_draft())
    staged = observations.observe(with_stage1_fields(case_data()), ai_draft())
    check(staged == plain and len(by_part(staged).get("Rear Bumper", {}).get("fittings") or []) == 2,
          "lines with Stage 1's line id and price fields: the same rows, the same fittings",
          [(r["part"], r["fittings"]) for r in staged if r["fittings"]])

    # Through the routes, as a page does it: GET gives every line its id,
    # a save keeps them; machine prices still waiting block the sign-off
    # (nothing captured); confirmed, the case signs off and is captured.
    case = new_case(case_data(ref="CL 90351"), ai_draft())
    add_run(case, "gemini-test-strong", knowledge=STAMP_A)
    r = W.admin.get(f"/reports/{case}")
    lines = (((r.json() if r.status_code == 200 else {}).get("data") or {}).get("annex_a") or {}).get("items") or []
    ids = [ln.get("line_id") for ln in lines]
    check(r.status_code == 200 and len(ids) == len(ANNEX) and all(ids) and len(set(ids)) == len(ids),
          "GET /reports/{id} gives every Annex A line its id", ids)
    r = W.admin.put(f"/reports/{case}/annex-a", json={"items": lines})
    saved = (stored(case).data.get("annex_a") or {}).get("items") or []
    check(r.status_code == 200 and [ln.get("line_id") for ln in saved] == ids,
          "PUT /reports/{id}/annex-a keeps them", brief(r))
    db = SessionLocal()
    try:
        rep = db.get(Report, case)
        rep.data = with_stage1_fields(json.loads(json.dumps(rep.data)), confirmed=False)
        db.commit()
    finally:
        db.close()
    r = sign_off(W.admin, case)
    body = r.json() if r.status_code == 409 else {}
    check(r.status_code == 409 and body.get("code") == "prices_unconfirmed" and len(body.get("lines") or []) == len(ANNEX)
          and obs_rows(case) == [], "machine prices waiting: the sign-off refused (Stage 1), nothing captured", brief(r))
    r = W.admin.post(f"/reports/{case}/annex-a/confirm-prices")
    check(r.status_code == 200 and r.json().get("confirmed") == len(ANNEX), "Confirm all prices", brief(r))
    r = sign_off(W.admin, case)
    rows = obs_rows(case)
    check(r.status_code == 200 and len(rows) == len(plain), "then it signs off and its rows are written", brief(r))
    got = {k: v["fittings"] for k, v in by_part(rows).items() if v["fittings"]}
    check(got == {
        "Rear Bumper": [{"wording": "rear bumper clips", "qty": 10.0, "section": "list", "necessary": True},
                        {"wording": "rear bumper side retainer", "qty": 2.0, "section": "nett", "necessary": False}],
        "rear air vent": [{"wording": "vent clip", "qty": 4.0, "section": "list", "necessary": True}],
    }, "the fittings as on lines without ids or price fields (by source_part and the main mark)", got)
    blob = json.dumps(rows, default=str)
    check(not leaks(rows) and not [k for k in STAGE1_KEYS if k in blob]
          and all(set(f) == {"wording", "qty", "section", "necessary"} for x in rows for f in x["fittings"]),
          "no price, line id or Stage 1 price field in any row", (leaks(rows), [k for k in STAGE1_KEYS if k in blob]))
    signed = (stored(case).data.get("annex_a") or {}).get("items") or []
    check([ln.get("line_id") for ln in signed] == ids and all(ln.get("price_confirmed") is True for ln in signed),
          "the signed-off case keeps its line ids and confirmed prices (the capture writes nothing back)",
          [(ln.get("line_id"), ln.get("price_confirmed")) for ln in signed])


# ============================================================ backfill
def backfill() -> None:
    signed_at = datetime.now(timezone.utc).isoformat()
    data = case_data(ref="CL 90326")
    data["sign_off"] = {**(data.get("sign_off") or {}), "status": "Signed Off", "signature_date": signed_at,
                        "signed_by_user_id": W.admin_id, "surveyor_name": SIGNER}
    case = new_case(data, ai_draft(), status="Signed Off")
    db = SessionLocal()
    try:
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(PartObservation(report_id="gonecase0001", signed_off_at=now_naive, part="Bonnet", how="ai_read",
                               outcome="kept", version=observations.VERSION))
        # A row an older definition wrote (version 1: the scrubber before
        # the review), on a case still signed off.
        db.add(PartObservation(report_id=W.case, signed_off_at=now_naive, part=OTHER_PART,
                               other_name="rear bumper azman", how="hand_added", outcome="added", version=1))
        db.commit()
    finally:
        db.close()
    before = len(obs_rows())
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = backfill_part_observations.main([])
    text_out = out.getvalue()
    check(code == 0 and len(obs_rows()) == before and obs_rows(case) == [], "a dry run writes nothing", text_out)
    check("Dry run" in text_out and "would be written" in text_out and "1 case(s) no longer signed off" in text_out
          and f"1 row(s) from older definitions (version < {observations.VERSION})" in text_out,
          "and says what it would do, in counts (an older-version row to make again)", text_out)
    lowered = text_out.lower()
    check(not any(t in lowered for t in (case, W.case, MAKE.lower(), "cl 903", "szz")) and not leaks([text_out]),
          "counts only: no case id, Our Ref, make, plate or name", text_out)
    with contextlib.redirect_stdout(io.StringIO()):
        backfill_part_observations.main(["--apply"])
    rows = obs_rows(case)
    check(len(rows) == 9 and obs_rows("gonecase0001") == [], "--apply writes the case's rows and takes out a gone case's",
          len(rows))
    check(all(r["version"] == observations.VERSION for r in obs_rows()) and len(obs_rows(W.case)) == 9
          and not any(r["other_name"] == "rear bumper azman" for r in obs_rows()),
          "and makes again the rows an older definition wrote: none below the current version left")
    snapshot = sorted(json.dumps({k: v for k, v in r.items() if k not in ("id", "captured_at")}, default=str, sort_keys=True)
                      for r in obs_rows())
    with contextlib.redirect_stdout(io.StringIO()):
        backfill_part_observations.main(["--apply"])
    again = sorted(json.dumps({k: v for k, v in r.items() if k not in ("id", "captured_at")}, default=str, sort_keys=True)
                   for r in obs_rows())
    check(snapshot == again, "run again: the same rows (idempotent)", (len(snapshot), len(again)))
    check(not leaks(rows), "the backfilled rows hold no identifier either", leaks(rows))
    W.backfilled = case


# ============================================================ retention
def retention() -> None:
    now = datetime.now(timezone.utc)
    naive = now.replace(tzinfo=None)
    db = SessionLocal()
    try:
        old = PartObservation(report_id=W.backfilled, signed_off_at=naive - timedelta(days=365 * 7 + 10), part="Roof",
                              how="hand_added", outcome="added")
        young = PartObservation(report_id=W.backfilled, signed_off_at=naive - timedelta(days=365 * 6), part="Seat",
                                how="hand_added", outcome="added")
        orphan = PartObservation(report_id="nosuchcase01", signed_off_at=naive, part="Bonnet", how="ai_read", outcome="kept")
        db.add_all([old, young, orphan])
        db.commit()
        ids = {"old": old.id, "young": young.id, "orphan": orphan.id}
    finally:
        db.close()
    before = {r["id"] for r in obs_rows()}
    db = SessionLocal()
    try:
        n = observations.sweep_old(db, now)
        db.commit()
    finally:
        db.close()
    after = {r["id"] for r in obs_rows()}
    check(n == 2 and before - after == {ids["old"], ids["orphan"]} and ids["young"] in after,
          "rows over 7 years old and rows without a signed-off case go; a 6-year-old row stays", (n, before - after))
    db = SessionLocal()
    try:
        db.add(PartObservation(report_id=W.backfilled, signed_off_at=naive - timedelta(days=365 * 8), part="Roof",
                               how="hand_added", outcome="added"))
        db.commit()
    finally:
        db.close()
    result = cleanup.sweep(now)
    check(result.get("part_observations") == 1, "the periodic cleanup sweep runs it", result.get("part_observations"))
    check(observations._years_before(datetime(2028, 2, 29), 7) == datetime(2021, 2, 28), "29 February: the 28th, 7 years back")
    # Leave the backfilled case's own rows only.
    db = SessionLocal()
    try:
        db.query(PartObservation).filter(PartObservation.id == ids["young"]).delete()
        db.commit()
    finally:
        db.close()


# ============================================================ the stamp
def stamp() -> None:
    settings.intake_links = "on"
    check(knowledge.links_version() == f"{links.TABLE_VERSION}:{lf_fingerprint()}" and len(lf_fingerprint()) == 8,
          "links: the table's version and the first 8 of its LF sha256", knowledge.links_version())
    settings.intake_links = "off"
    check(knowledge.links_version() is None, "links off: none")
    settings.intake_car_map = "shadow"
    want_map = car_map.MAP.get("version") if isinstance(car_map.MAP, dict) else None
    check(knowledge.car_map_version() == want_map and want_map, "car map in shadow: its version", knowledge.car_map_version())
    settings.intake_car_map = "off"
    check(knowledge.car_map_version() is None, "car map off: none")
    settings.intake_links = "on"

    db = SessionLocal()
    try:
        started = runs.start_run(db, report_id="stampcase001", kind="read", channel=None, actor=None)
        check(isinstance(started.knowledge, dict) and started.knowledge.get("models") == []
              and started.knowledge.get("links") == f"{links.TABLE_VERSION}:{lf_fingerprint()}"
              and started.knowledge.get("prompt") == settings.intake_read_prompt and "library" in started.knowledge
              and started.knowledge.get("library") is None,
              "an AI run's row has the stamp from its start (no models yet)", started.knowledge)
        done = runs.finish_run(db, started, "ok", attempts=[
            {"model": "gemini-test-light", "outcome": "429"}, {"model": "gemini-test-strong", "outcome": "ok"}])
        check(done.knowledge.get("models") == ["gemini-test-strong"], "and the model that answered once it ends", done.knowledge)
        sort = runs.start_run(db, report_id="stampcase001", kind="sort", channel=None, actor=None)
        check(sort.knowledge.get("prompt") is None and sort.knowledge.get("links") == started.knowledge.get("links"),
              "the reading prompt only on a reading run: none on the sorter's (L5)", sort.knowledge)
    finally:
        db.close()

    # Filing stamps the case from its own runs too (D.3), never in its way.
    from app.reports.filing import file_case_data

    case = new_case(case_data(ref="CL 90331"), ai_draft())
    add_run(case, "gemini-test-strong", knowledge=STAMP_A)
    db = SessionLocal()
    try:
        user = db.get(User, W.admin_id)
        filed = file_case_data(db, case_data(ref="CL 90331"), user, ai_draft=None, photo_kinds=None, report_id=case)
        unnamed = file_case_data(db, case_data(ref="CL 90331"), user, ai_draft=None, photo_kinds=None)
    finally:
        db.close()
    check(filed.get("knowledge") == STAMP_A, "filing writes data.knowledge from the case's runs", filed.get("knowledge"))
    check("knowledge" not in unnamed, "(no case id given: none)")
    with runs.recorded("stampcase002", "read", None) as run:
        runs.note_attempt({"model": "gemini-test-light", "outcome": "ok"})
    db = SessionLocal()
    try:
        row_ = db.get(AiRun, run.id)
        check(row_ is not None and row_.status == "ok" and row_.knowledge["models"] == ["gemini-test-light"],
              "runs.recorded() stamps its row the same way", row_ and row_.knowledge)
    finally:
        db.close()

    def boom():
        raise RuntimeError("stamp exploded")

    saved = knowledge.links_version
    knowledge.links_version = boom
    try:
        check(knowledge.stamp() is None, "a stamp that can't be made is None, never an error")
        with runs.recorded("stampcase003", "read", None) as run:
            runs.note_attempt({"model": "gemini-test-light", "outcome": "ok"})
        db = SessionLocal()
        try:
            row_ = db.get(AiRun, run.id)
            check(row_ is not None and row_.status == "ok" and row_.knowledge is None,
                  "and the run is still logged, without one", row_ and (row_.status, row_.knowledge))
        finally:
            db.close()
    finally:
        knowledge.links_version = saved
    check(knowledge.clean({"links": "L1:abc", "models": ["m", 3, "m"], "extra": "x", "prompt": True})
          == {"library": None, "links": "L1:abc", "car_map": None, "prompt": None, "models": ["m"]}
          and knowledge.clean("x") is None, "a stored stamp read back: known keys only")
    settings.intake_links = W.links_before
    settings.intake_car_map = W.car_map_before


# ============================================================ summary
def summary_api() -> None:
    db = SessionLocal()
    try:
        db.query(PartObservation).delete()
        db.commit()
    finally:
        db.close()
    case = drafted_case(ref="CL 90327")
    check(sign_off(W.admin, case).status_code == 200, "a drafted case signed off")
    plain = new_case(case_data(ref="CL 90328"), None)
    check(sign_off(W.admin, plain).status_code == 200, "and one without an AI draft")

    r = W.admin.get("/admin/observations/summary")
    check(r.status_code == 200, "an admin gets the summary", brief(r))
    body = r.json() if r.status_code == 200 else {}
    t = body.get("totals") or {}
    check((t.get("cases"), t.get("cases_without_ai_draft"), t.get("parts"), t.get("kept"), t.get("removed"), t.get("added"),
           t.get("added_prompted"), t.get("dismissed"), t.get("left")) == (1, 1, 9, 1, 1, 4, 2, 1, 2),
          "totals over the AI-drafted case; the case without a draft counted apart", t)
    parts = {p["part"]: p for p in body.get("by_part") or []}
    check(parts.get("Tail Lamp", {}).get("added") == 1 and parts.get("Bonnet", {}).get("removed") == 1
          and parts.get(f"{OTHER_PART}: rear air vent", {}).get("added") == 1
          and parts.get("Rear Bumper Beam", {}).get("added_prompted") == 1
          and parts.get("Rear Bumper Reflector", {}).get("dismissed") == 1,
          "per part: by family (both sides together), an Other by its scrubbed name", sorted(parts))
    makes = body.get("by_make") or []
    check(makes and makes[0]["make"] == MAKE and makes[0]["cases"] == 1 and makes[0]["added"] == 4, "per make", makes)
    areas = {a["area"]: a for a in body.get("by_area") or []}
    check(areas.get("rear", {}).get("added") == 4 and areas.get("rear", {}).get("left") == 2
          and areas.get("front", {}).get("removed") == 1, "per area", areas)
    text_out = r.text.lower()
    check(case not in text_out and plain not in text_out and "cl 9032" not in text_out and not leaks([r.text]),
          "counts only: no case id, Our Ref, plate or name in the answer")
    keys = {k for group in ("by_part", "by_make", "by_area") for row_ in body.get(group) or [] for k in row_}
    check(keys <= {"part", "make", "area", "cases", "kept", "removed", "added", "added_prompted", "dismissed", "left"},
          "each group row: its name and counts", keys)

    today = datetime.now(SG).date()
    for query, n in ((f"?from={today}&to={today}", 1), (f"?from={today + timedelta(days=1)}", 0),
                     (f"?to={today - timedelta(days=1)}", 0), (f"?from={today - timedelta(days=30)}", 1)):
        r = W.admin.get(f"/admin/observations/summary{query}")
        got = r.json().get("totals", {}).get("cases") if r.status_code == 200 else None
        check(got == n, f"the date range {query}: {n} case(s)", brief(r))
    r = W.admin.get(f"/admin/observations/summary?from={today}&to={today}")
    check(r.status_code == 200 and r.json().get("from") == str(today) and r.json().get("to") == str(today), "the range is echoed")
    for query in ("?from=2026-13-01", "?to=yesterday", "?from=2026-11-10&to=2026-11-01"):
        r = W.admin.get(f"/admin/observations/summary{query}")
        check(r.status_code == 400, f"400 for {query}", brief(r))
    r = W.plain.get("/admin/observations/summary")
    check(r.status_code == 403, "403 for anyone not an admin", brief(r))
    r = TestClient(app, base_url=BASE).get("/admin/observations/summary")
    check(r.status_code == 401, "401 signed out", brief(r))

    # A case reopened: out of the counts (its rows go, and only signed-off cases count).
    check(W.admin.post(f"/reports/{case}/reopen").status_code == 200, "the drafted case reopened")
    t = W.admin.get("/admin/observations/summary").json().get("totals") or {}
    check(t.get("cases") == 0 and t.get("parts") == 0, "then it is no longer counted", t)


# ============================================================ old database
def old_database() -> None:
    check(("ai_runs", "knowledge", "JSON") in db_module._ADDED_COLUMNS, "ai_runs.knowledge is in db._ADDED_COLUMNS")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE part_observations"))
        conn.execute(text("ALTER TABLE ai_runs DROP COLUMN knowledge"))
    insp = inspect(engine)
    gone = "part_observations" not in insp.get_table_names() and "knowledge" not in {c["name"] for c in insp.get_columns("ai_runs")}
    runs_before = count_runs()
    init_db()
    insp = inspect(engine)
    tables = insp.get_table_names()
    columns = {c["name"] for c in insp.get_columns("ai_runs")}
    indexes = {i["name"] for i in insp.get_indexes("part_observations")} if "part_observations" in tables else set()
    check(gone and "part_observations" in tables and "knowledge" in columns,
          "a database from before: init_db makes the table and adds the column", (gone, columns))
    check({"ix_part_observations_make_model_key", "ix_part_observations_part", "ix_part_observations_report_id",
           "ix_part_observations_signed_off_at"} <= indexes, "with its indexes", indexes)
    db = SessionLocal()
    try:
        old_rows = db.query(AiRun).all()
        check(len(old_rows) == runs_before and all(r.knowledge is None for r in old_rows),
              "the runs already there stay, their stamp empty (NULL)")
    finally:
        db.close()
    init_db()
    check(set(inspect(engine).get_table_names()) == set(tables), "init_db again changes nothing")


def count_runs() -> int:
    """Rows of ai_runs, by SQL: the table may lack a column the model has."""
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM ai_runs")).scalar()


def main_() -> int:
    for name, fn in (
        ("setup", setup),
        ("observe(): outcomes, fields, scrubbing", observe_outcomes),
        ("sign-off writes the rows, de-identified", sign_off_writes),
        ("re-sign-off replaces; reopen and delete remove", resign_and_remove),
        ("the sign-off never fails for them", never_fails),
        ("M1: the scrubber, the review's examples", scrubber_m1),
        ("L1 fittings and L8 the capture race", fittings_and_race),
        ("Stage 1's Annex A lines: line ids and price fields", stage1_lines),
        ("backfill: dry run, apply, idempotent", backfill),
        ("retention: 7 years, and with the case", retention),
        ("the knowledge stamp", stamp),
        ("GET /admin/observations/summary", summary_api),
        ("an old database", old_database),
    ):
        run_section(name, fn)
    _section["name"] = "end"
    check(not GEMINI, "no Gemini call anywhere", GEMINI)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main_())
