"""The parts-catalogue look-up, server side (app/parts/catalogue.py,
app/api/catalogue.py; contract plans/2026-11-12-parts-catalogue/CONTRACT.md).

    python scripts/test_catalogue.py

Checked: off by default -- GET .../catalogue answers {"enabled": false},
every other route 404, the worker's too, with the flag on but no token as
well; the worker's token (missing, wrong, another scheme: 401; the session
cookie is no substitute, the worker token opens no dashboard route), and
its routes skip the session and the other-site check, that prefix only;
the job the worker gets carries the car and the part names only, never the
plate, VIN, engine number, Our Ref, the case id or a name, even one typed
into a part's name; who may start one and why not (no make or model, an
intake draft, signed off, read-only); claim -> progress -> result, and the
page's view of it grouped by part with not_found; one open job per case;
cancel (the worker hears "cancelled", its result 409); a job gone quiet for
45 minutes is claimed again; the result's caps and its https links on the
four parts sites only (a refused result fails the job); a re-run keeps
accepted and rejected rows and adds no duplicate; reject / undo; the match
hint finds the Annex A line by part number and by its words, never across
sides or front/rear; deleting a case takes its look-ups along; no Gemini,
nothing outside this machine.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database, Gemini replaced by a fake that fails the test if anything calls
it, and every outside connection refused. Every plate, VIN and name below
is made up.
"""
import json
import os
import re
import socket
import sys
import tempfile
import traceback
from datetime import timedelta
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
_tmp = Path(tempfile.mkdtemp(prefix="catalogue-test-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
os.environ["STORAGE_DIR"] = str(STORAGE)
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaSyFakeCompanyKeyForCatalogueTest0001"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ["MIN_FREE_DISK_GB"] = "0"
# Off, as in production until the owner turns it on.
os.environ.pop("CATALOGUE_ENABLED", None)
os.environ.pop("CATALOGUE_WORKER_TOKEN", None)

OUTSIDE: list[str] = []
_real_connect = socket.socket.connect


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        OUTSIDE.append(str(address))
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
from sqlalchemy.exc import IntegrityError  # noqa: E402

import app.api.main as main  # noqa: E402
from app.auth.core import SESSION_COOKIE, create_session, utc_now  # noqa: E402
from app.config import settings  # noqa: E402
from app.parts import catalogue  # noqa: E402
from app.reports.db import SessionLocal, engine, init_db  # noqa: E402
from app.reports.models import (  # noqa: E402
    AuditEntry, CatalogueJob, CatalogueSuggestion, CatalogueWorker, Report, User, gen_id,
)
from app.reports.schema import SecurityIncidentDraft  # noqa: E402

app = main.app

# ---------------------------------------------------------------- harness
PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}
BASE = "https://carlink.test"
TOKEN = "test-worker-token-" + gen_id() + gen_id()
W = SimpleNamespace()

# The made-up identifying facts of the main case: none may reach the worker.
PLATE, VIN, ENGINE, OUR_REF = "SXX 1234 A", "JHMRU1850HX000123", "L15B71234567", "CL 99001"
DRIVER, POLICY, CLAIM = "Tan Ah Kow", "POL-778899", "CLM-445566"


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


def brief(r) -> str:
    return f"{r.status_code} {r.text[:300]}"


def code_of(r):
    try:
        return r.json().get("code")
    except Exception:
        return None


def turn_on(flag: bool = True, token: str | None = TOKEN) -> None:
    settings.catalogue_enabled = flag
    settings.catalogue_worker_token = token or ""


def new_user(name: str, *, role: str = "user", **profile) -> str:
    db = SessionLocal()
    try:
        user = User(email=f"{name.lower()}-{gen_id()}@example.com", username=f"{name.lower()}{gen_id()[:6]}",
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


def worker(token: str | None = TOKEN, **headers) -> TestClient:
    c = TestClient(app, base_url=BASE)
    if token is not None:
        c.headers["Authorization"] = f"Bearer {token}"
    c.headers.update(headers)
    return c


def damage(part: str, **kw) -> dict:
    return {"part": part, "damage_type": "Dent", "severity": "Moderate", **kw}


def case_data(**over) -> dict:
    data = SecurityIncidentDraft(description="Made-up test case.", category=["Vehicle Collision or Damage"]).model_dump()
    data["report_id"] = OUR_REF
    data["vehicle_info"].update({
        "plate_number": PLATE, "make": "Honda", "model": "Vezel Hybrid", "year": "2017", "vin": VIN,
        "engine_number": ENGINE, "driver_name": DRIVER, "driver_contact": "91234567",
        "date_of_registration": "12 Mar 2018",
    })
    data["insurance_details"].update({"insurer_name": "Made-up Insurance", "policy_number": POLICY, "claim_number": CLAIM})
    data["damage_summary"] = [
        damage("Rear Bumper"),
        damage("Left Tail Lamp"),
        damage("Rear Number Plate"),
    ]
    data["annex_a"] = {"items": []}
    data.update(over)
    return data


def new_case(owner_id: str | None, data: dict | None = None, status: str = "confirmed", channel: str = "manual") -> str:
    db = SessionLocal()
    try:
        r = Report(channel=channel, reporter_chat_id="dashboard", status=status, owner_id=owner_id,
                   data=data if data is not None else case_data(), photo_paths=[])
        db.add(r)
        db.commit()
        return r.id
    finally:
        db.close()


def set_data(report_id: str, **changes) -> None:
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        r.data = {**(r.data or {}), **changes}
        db.commit()
    finally:
        db.close()


def job_row(job_id: int) -> CatalogueJob | None:
    db = SessionLocal()
    try:
        j = db.get(CatalogueJob, job_id)
        if j is not None:
            db.expunge(j)
        return j
    finally:
        db.close()


def rows_of(report_id: str) -> list[CatalogueSuggestion]:
    db = SessionLocal()
    try:
        out = db.query(CatalogueSuggestion).filter(CatalogueSuggestion.report_id == report_id).order_by(CatalogueSuggestion.id).all()
        for s in out:
            db.expunge(s)
        return out
    finally:
        db.close()


def history(report_id: str) -> list[tuple[str, str]]:
    db = SessionLocal()
    try:
        return [(e.action, e.detail or "") for e in
                db.query(AuditEntry).filter(AuditEntry.report_id == report_id).order_by(AuditEntry.id)]
    finally:
        db.close()


def row(part_number, name, **kw) -> dict:
    out = {"callout": "1", "part_number": part_number, "superseded_from": None, "name": name, "name_original": None,
           "qty": "1", "side": None, "notes": None, "ref_amount": 10.0, "ref_currency": "EUR", "ref_tax_note": "excl. VAT",
           "source": "microfiches", "url": "https://www.parts-honda.uk/part/" + (part_number or "x"),
           "fetched_at": "2026-11-12T10:42:00+00:00", "stale": False}
    out.update(kw)
    return out


def diagram(key, name, rows, score=1.0) -> dict:
    return {"diagram_key": key, "diagram_name": name, "diagram_url": "https://www.parts-honda.uk/d/" + key.replace(":", "-"),
            "image_url": "https://www.parts-honda.uk/img/" + key.replace(":", "-") + ".png", "match_score": score, "rows": rows}


VEHICLE = {"key": "microfiches:30584", "source": "microfiches", "brand": "Honda", "model": "HR-V",
           "name": "HR-V 1.5 i-VTEC", "market": "Europe", "year_from": 2015, "year_to": 2019,
           "url": "https://www.parts-honda.uk/hr-v", "twin_of": "Vezel"}


def result(parts: list[dict], **over) -> dict:
    out = {"status": "done", "error_code": None, "error_text": None, "vehicle": VEHICLE,
           "candidates": [VEHICLE], "parts": parts,
           "failures": [{"source": "partsouq", "code": "needs_human", "message": "made-up: a check page"}]}
    out.update(over)
    return out


def first_result() -> dict:
    return result([
        {"source_part": "Rear Bumper", "query": "rear bumper", "side": None, "diagrams": [
            diagram("microfiches:30584:rb", "REAR BUMPER", [
                row("04715T7WA90", "FACE, RR. BUMPER", callout="1"),
                row("91503TG7003", "CLIP, BUMPER", callout="7", qty=4),
            ]),
            # The same clip on a second diagram: stored once.
            diagram("microfiches:30584:rb2", "REAR BUMPER (2)", [
                row("91503-TG7-003", "CLIP, BUMPER", callout="3"),
                row("71598T7WA01", "SPACER, R. RR. BUMPER SIDE", side="right"),
            ], score=0.7),
        ], "failures": []},
        {"source_part": "Left Tail Lamp", "query": "tail lamp", "side": "left", "diagrams": [
            diagram("microfiches:30584:tl", "TAILLIGHT", [row("33550T7WA01", "LIGHT ASSY., L. TAIL", side="left")]),
        ], "failures": []},
        {"source_part": "Rear Number Plate", "query": "rear number plate", "side": None, "diagrams": [], "failures": []},
    ])


def run_job(client: TestClient, report_id: str, body: dict, *, vehicle_key=None) -> tuple[int, dict]:
    """lookup -> claim -> result; the job's id and the result's answer."""
    r = client.post(f"/reports/{report_id}/catalogue/lookup", json={"vehicle_key": vehicle_key})
    assert r.status_code == 202, brief(r)
    job_id = r.json()["job"]["id"]
    r = W.w.post("/catalogue-worker/claim", json={"worker": "test-pc", "version": "0.1"})
    assert r.status_code == 200 and (r.json()["job"] or {}).get("job_id") == job_id, brief(r)
    r = W.w.post(f"/catalogue-worker/jobs/{job_id}/result", json=body)
    return job_id, {"status": r.status_code, **(r.json() if r.headers.get("content-type", "").startswith("application/json") else {})}


# ---------------------------------------------------------------- sections
def setup() -> None:
    init_db()
    W.admin = new_user("Admin", role="admin")
    W.owner = new_user("Una")
    W.other = new_user("Ben")
    W.staff = new_user("Sam", sees_all_cases=True)
    W.a, W.o, W.b, W.s = (signed_in(u) for u in (W.admin, W.owner, W.other, W.staff))
    W.w = worker()
    W.case = new_case(W.owner)
    check(W.o.get("/auth/me").status_code == 200, "signed in")
    names = set(inspect(engine).get_table_names())
    check({"catalogue_jobs", "catalogue_suggestions", "catalogue_workers"} <= names, "init_db made the three tables",
          str(sorted(n for n in names if "catalogue" in n)))
    with engine.connect() as conn:
        idx = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'index'"))}
    check("ux_catalogue_jobs_open" in idx and "ix_catalogue_jobs_status_created_at" in idx,
          "and their indexes, the one-open-job-per-case rule among them", str(sorted(i for i in idx if "catalogue" in i)))


def feature_off() -> None:
    turn_on(False, None)
    check(settings.catalogue_enabled is False and not settings.catalogue_worker_token,
          "off by default: CATALOGUE_ENABLED false, no token")
    for flag, token, label in ((False, None, "off"), (True, None, "flag on, no token"), (False, TOKEN, "token, flag off")):
        turn_on(flag, token)
        r = W.o.get(f"/reports/{W.case}/catalogue")
        check(r.status_code == 200 and r.json() == {"enabled": False}, f"{label}: GET .../catalogue -> {{\"enabled\": false}} only", brief(r))
        for path, body in ((f"/reports/{W.case}/catalogue/lookup", {"vehicle_key": None}),
                           (f"/reports/{W.case}/catalogue/cancel", None),
                           (f"/reports/{W.case}/catalogue/suggestions/1", {"action": "reject"})):
            r = W.o.post(path, json=body)
            check(r.status_code == 404, f"{label}: POST {path.replace(W.case, '{id}')} -> 404", brief(r))
        for path, body in (("/catalogue-worker/claim", {"worker": "test-pc"}),
                           ("/catalogue-worker/jobs/1/progress", {"done_parts": 0, "total_parts": 1}),
                           ("/catalogue-worker/jobs/1/result", {"status": "done"})):
            r = worker(TOKEN).post(path, json=body)
            check(r.status_code == 404, f"{label}: worker POST {path} -> 404 (even with the token)", brief(r))
    turn_on(False, None)
    r = worker(TOKEN).post("/catalogue-worker/claim", content=b"{not json")
    check(r.status_code == 404, "off: even a malformed body gets 404, not a 422 saying the route exists", brief(r))
    r = W.b.get(f"/reports/{W.case}/catalogue")
    check(r.status_code == 404, "off: a stranger's GET is 404 (the case's own rule first)", brief(r))
    r = TestClient(app, base_url=BASE).get(f"/reports/{W.case}/catalogue")
    check(r.status_code == 401, "signed out: GET .../catalogue -> 401", brief(r))
    check(catalogue.enabled() is False, "catalogue.enabled() is False")


def token() -> None:
    turn_on()
    check(catalogue.enabled(), "on with the flag and a token")
    for label, c in (("no Authorization", worker(None)), ("a wrong token", worker("not-the-token")),
                     ("the token with another scheme", worker(None, Authorization=f"Basic {TOKEN}")),
                     ("the token's first half", worker(TOKEN[: len(TOKEN) // 2]))):
        r = c.post("/catalogue-worker/claim", json={"worker": "test-pc"})
        check(r.status_code == 401 and code_of(r) == "worker_token", f"{label} -> 401 worker_token", brief(r))
    c = worker(None)
    c.cookies.set(SESSION_COOKIE, W.o.cookies.get(SESSION_COOKIE))
    r = c.post("/catalogue-worker/claim", json={"worker": "test-pc"})
    check(r.status_code == 401, "a signed-in person's session cookie is no worker token -> 401", brief(r))
    r = worker().post("/catalogue-worker/claim", json={"worker": "test-pc", "version": "0.1"})
    check(r.status_code == 200 and r.json() == {"job": None, "poll_seconds": 20},
          "the right token: 200 {job: null, poll_seconds: 20} (no session needed)", brief(r))
    r = worker(Origin="https://evil.example").post("/catalogue-worker/claim", json={"worker": "test-pc"})
    check(r.status_code == 200, "the worker's routes skip the other-site (Origin) check: the token guards them", brief(r))
    r = W.o.post(f"/reports/{W.case}/catalogue/cancel", headers={"Origin": "https://evil.example"})
    check(r.status_code == 403, "...the dashboard's routes keep it (403 from another site)", brief(r))
    r = worker().get(f"/reports/{W.case}/catalogue")
    check(r.status_code == 401, "the worker token opens no dashboard route (401: not signed in)", brief(r))
    for path in ("/catalogue-workers", "/catalogue-worker"):
        r = worker().post(path, json={})
        check(r.status_code == 401, f"only the /catalogue-worker/ prefix skips sign-in: POST {path} -> 401", brief(r))
    r = worker().post("/catalogue-worker/claim", json={"worker": "bad name with spaces"})
    check(r.status_code == 422, "a malformed claim (worker name) -> 422", brief(r))
    db = SessionLocal()
    try:
        seen = db.get(CatalogueWorker, "test-pc")
        check(seen is not None and seen.version == "0.1", "every claim records the worker as seen, with its version",
              repr(seen and (seen.name, seen.version)))
    finally:
        db.close()
    r = W.o.get(f"/reports/{W.case}/catalogue")
    check(r.status_code == 200 and r.json().get("worker", {}).get("online") is True,
          "the page says the worker is online (seen in the last 2 minutes)", brief(r))
    db = SessionLocal()
    try:
        db.get(CatalogueWorker, "test-pc").last_seen_at = utc_now() - timedelta(minutes=3)
        db.commit()
    finally:
        db.close()
    r = W.o.get(f"/reports/{W.case}/catalogue")
    w = r.json().get("worker", {})
    check(w.get("online") is False and w.get("last_seen_at"), "seen 3 minutes ago: offline, with when it was last seen", repr(w))


def payload_privacy() -> None:
    turn_on()
    data = case_data(damage_summary=[
        damage("Rear Bumper"),
        damage("Left Tail Lamp"),
        damage("Right Tail Lamp", side_inferred=True),
        damage("Tail Lamp (side undetermined)"),
        damage("Left Front Fender", side_inferred=True, human_verified=True),
        damage("Other / Not Listed", other_part_name="Rear air vent LH"),
        damage("Other / Not Listed"),
        # A name someone typed with the plate, the VIN and Our Ref in it.
        damage("Other / Not Listed", other_part_name=f"Sticker {PLATE} {VIN} {OUR_REF}"),
        damage("Rear Bumper"),
    ])
    rid = new_case(W.owner, data)
    r = W.o.post(f"/reports/{rid}/catalogue/lookup", json={})
    check(r.status_code == 202, "a person who may change the case starts a look-up -> 202", brief(r))
    job = r.json().get("job") or {}
    check(job.get("status") == "pending" and job.get("requested_by") and job.get("progress", {}).get("total_parts") == 7,
          "the job: pending, who asked, 7 parts to do", repr(job))
    r = W.w.post("/catalogue-worker/claim", json={"worker": "test-pc"})
    got = (r.json() or {}).get("job") or {}
    check(set(got) == {"job_id", "kind", "brand", "model", "model_key", "year", "vehicle_key", "parts"},
          "the claimed job has the contract's keys and nothing else", repr(sorted(got)))
    check(got.get("job_id") == job.get("id") and got.get("kind") == "lookup", "known by its job id only", repr(got.get("job_id")))
    check((got.get("brand"), got.get("model"), got.get("model_key"), got.get("year")) == ("Honda", "Vezel Hybrid", "Vezel", 2017),
          "brand, model, model_key (first word) and year as on the case", repr(got))
    check(all(set(p) == {"source_part", "query", "side"} for p in got.get("parts") or []), "each part: source_part, query, side")
    flat = re.sub(r"[^a-z0-9]", "", json.dumps(got).lower())
    for label, value in (("plate", PLATE), ("VIN", VIN), ("engine number", ENGINE), ("Our Ref", OUR_REF),
                         ("case id", rid), ("driver's name", DRIVER), ("driver's phone", "91234567"),
                         ("policy number", POLICY), ("claim number", CLAIM), ("owner's name", "una"),
                         ("insurer", "madeupinsurance")):
        check(re.sub(r"[^a-z0-9]", "", value.lower()) not in flat, f"no {label} anywhere in the job")
    parts = {p["source_part"]: p for p in got.get("parts") or []}
    want = {
        "Rear Bumper": ("rear bumper", None),
        "Left Tail Lamp": ("tail lamp", "left"),
        "Right Tail Lamp": ("tail lamp", None),
        "Tail Lamp (side undetermined)": ("tail lamp", None),
        "Left Front Fender": ("front fender", "left"),
        "Other / Not Listed: Rear air vent LH": ("rear air vent", "left"),
    }
    for source, (query, side) in want.items():
        p = parts.get(source) or {}
        check((p.get("query"), p.get("side")) == (query, side), f"{source!r} -> query {query!r}, side {side}", repr(p))
    check(list(parts)[:2] == ["Rear Bumper", "Left Tail Lamp"] and sum(1 for p in got["parts"] if p["source_part"] == "Rear Bumper") == 1,
          "the damage list's order, each part once", repr(list(parts)))
    check(not any(p["source_part"] == "Other / Not Listed" for p in got["parts"]), "an unnamed Other is left out (nothing to look up)")
    sticker = parts.get("Other / Not Listed: Sticker")
    check(sticker is not None and sticker["query"] == "sticker",
          "a typed name keeps the part, the plate / VIN / Our Ref taken out of its source_part and query", repr(list(parts)))
    r = W.o.post(f"/reports/{rid}/catalogue/cancel")
    check(r.status_code == 200 and r.json()["job"]["status"] == "cancelled", "cancelled again", brief(r))

    # Year: vehicle_info.year, else the registration year, else none.
    rid2 = new_case(W.owner, case_data())

    def payload_with(**vehicle) -> dict:
        set_data(rid2, vehicle_info={**case_data()["vehicle_info"], **vehicle})
        db = SessionLocal()
        try:
            return catalogue.job_payload(db.get(Report, rid2))
        finally:
            db.close()

    p2 = payload_with(year=None, date_of_registration="2016-05-04")
    p3 = payload_with(year="", date_of_registration=None)
    check(p2["year"] == 2016, "no year on the case: the registration year", repr(p2["year"]))
    check(p3["year"] is None, "neither: year null (never invented)", repr(p3["year"]))
    r = W.o.post(f"/reports/{rid2}/catalogue/lookup", json={"vehicle_key": "microfiches:30584"})
    job2 = r.json().get("job") or {}
    claimed = W.w.post("/catalogue-worker/claim", json={"worker": "test-pc"}).json().get("job") or {}
    check(r.status_code == 202 and claimed.get("vehicle_key") == "microfiches:30584",
          "the car picked with \"Change car\" goes to the worker as vehicle_key", repr(claimed.get("vehicle_key")))
    W.o.post(f"/reports/{rid2}/catalogue/cancel")
    r = W.o.post(f"/reports/{rid2}/catalogue/lookup", json={"vehicle_key": "bad key with spaces"})
    check(r.status_code == 422, "a malformed vehicle_key -> 422", brief(r))
    check(job2.get("vehicle_key") == "microfiches:30584", "and the job says so", repr(job2))


def refusals() -> None:
    turn_on()
    view = W.o.get(f"/reports/{W.case}/catalogue").json()
    check(view.get("enabled") is True and view.get("can_request") is True and view.get("blocked_reason") is None,
          "the owner on a filed case with make and model: can_request, no blocked_reason", repr(view.get("blocked_reason")))
    check(view.get("vehicle_hint") == {"brand": "Honda", "model": "Vezel Hybrid", "year": 2017, "confirmed": False},
          "vehicle_hint: as on the case, not confirmed", repr(view.get("vehicle_hint")))
    set_data(W.case, checked={"vehicle_info.make": {"by": "Una", "at": "t"}, "vehicle_info.model": {"by": "Una", "at": "t"}})
    hint = W.o.get(f"/reports/{W.case}/catalogue").json().get("vehicle_hint") or {}
    check(hint.get("confirmed") is True, "confirmed once a person confirmed make and model (field review)", repr(hint))
    set_data(W.case, checked={"vehicle_info.make": {"by": "Una", "at": "t"}})
    hint = W.o.get(f"/reports/{W.case}/catalogue").json().get("vehicle_hint") or {}
    check(hint.get("confirmed") is False, "the make alone isn't the car confirmed", repr(hint))

    nomodel = new_case(W.owner, case_data())
    set_data(nomodel, vehicle_info={**case_data()["vehicle_info"], "model": "  "})
    view = W.o.get(f"/reports/{nomodel}/catalogue").json()
    check(view.get("can_request") is False and view.get("blocked_reason") == "no_make_model", "no model: blocked no_make_model",
          repr(view.get("blocked_reason")))
    r = W.o.post(f"/reports/{nomodel}/catalogue/lookup", json={})
    check(r.status_code == 400 and code_of(r) == "no_make_model", "...and lookup -> 400 no_make_model", brief(r))

    noparts = new_case(W.owner, case_data(damage_summary=[damage("Other / Not Listed")]))
    r = W.o.post(f"/reports/{noparts}/catalogue/lookup", json={})
    check(r.status_code == 400 and code_of(r) == "no_parts", "no damaged part with a name -> 400 no_parts", brief(r))

    draft = new_case(W.owner, case_data(), status="intake")
    view = W.o.get(f"/reports/{draft}/catalogue").json()
    check(view.get("blocked_reason") == "draft" and view.get("can_request") is False, "an intake draft: blocked draft",
          repr(view.get("blocked_reason")))
    r = W.o.post(f"/reports/{draft}/catalogue/lookup", json={})
    check(r.status_code == 409 and code_of(r) == "draft", "...and lookup -> 409 draft", brief(r))
    view = W.s.get(f"/reports/{draft}/catalogue").json()
    check(view.get("blocked_reason") == "readonly", "\"see all\" staff on someone else's draft: blocked readonly",
          repr(view.get("blocked_reason")))
    r = W.s.post(f"/reports/{draft}/catalogue/lookup", json={})
    check(r.status_code == 403 and code_of(r) == "not_allowed", "...and lookup -> 403 not_allowed (deps.editable_case)", brief(r))
    inbox = new_case(None, case_data(), status="intake", channel="telegram_public")
    view = W.a.get(f"/reports/{inbox}/catalogue").json()
    check(view.get("blocked_reason") == "readonly", "a public report in the inbox: readonly, admins too", repr(view.get("blocked_reason")))

    signed = new_case(W.owner, case_data(sign_off={"status": "Signed Off", "signed_by_user_id": W.admin}))
    view = W.o.get(f"/reports/{signed}/catalogue").json()
    check(view.get("blocked_reason") == "signed_off", "a signed-off case: blocked signed_off", repr(view.get("blocked_reason")))
    r = W.o.post(f"/reports/{signed}/catalogue/lookup", json={})
    check(r.status_code == 409 and code_of(r) == "signed_off", "...and lookup -> 409 signed_off", brief(r))

    r = W.b.get(f"/reports/{W.case}/catalogue")
    check(r.status_code == 404, "a stranger: GET -> 404, as for a case that doesn't exist", brief(r))
    for path, body in ((f"/reports/{W.case}/catalogue/lookup", {}), (f"/reports/{W.case}/catalogue/cancel", None),
                       (f"/reports/{W.case}/catalogue/suggestions/1", {"action": "reject"})):
        r = W.b.post(path, json=body)
        check(r.status_code == 404, f"a stranger: POST {path.replace(W.case, '{id}')} -> 404", brief(r))
    r = W.s.get(f"/reports/{W.case}/catalogue")
    check(r.status_code == 200 and r.json().get("can_request") is True, "\"see all\" staff on a filed case may start one", brief(r))


def happy_path() -> None:
    turn_on()
    rid = W.case
    r = W.o.post(f"/reports/{rid}/catalogue/lookup", json={"vehicle_key": None})
    check(r.status_code == 202 and r.json()["job"]["status"] == "pending", "lookup -> 202, pending", brief(r))
    job_id = r.json()["job"]["id"]
    check(("catalogue_lookup_requested", "3 part(s)") in history(rid), "the history says a look-up was asked for", repr(history(rid)[-2:]))
    r = W.o.post(f"/reports/{rid}/catalogue/lookup", json={})
    check(r.status_code == 409 and code_of(r) == "job_open", "a second while one is pending -> 409 job_open", brief(r))
    view = W.o.get(f"/reports/{rid}/catalogue").json()
    check((view.get("job") or {}).get("id") == job_id and view["job"]["status"] == "pending", "GET shows it pending", repr(view.get("job")))

    r = W.w.post("/catalogue-worker/claim", json={"worker": "test-pc", "version": "0.1"})
    claimed = r.json().get("job") or {}
    check(r.status_code == 200 and claimed.get("job_id") == job_id, "the worker claims it", brief(r))
    check([p["source_part"] for p in claimed["parts"]] == ["Rear Bumper", "Left Tail Lamp", "Rear Number Plate"],
          "with the case's three parts", repr(claimed.get("parts")))
    r = W.w.post("/catalogue-worker/claim", json={"worker": "test-pc"})
    check(r.json().get("job") is None, "nothing else to claim", brief(r))
    view = W.o.get(f"/reports/{rid}/catalogue").json()
    check(view["job"]["status"] == "running" and view["job"]["claimed_at"], "GET: running, with claimed_at", repr(view["job"]))
    r = W.o.post(f"/reports/{rid}/catalogue/lookup", json={})
    check(r.status_code == 409 and code_of(r) == "job_open", "...still 409 job_open while it runs", brief(r))

    r = W.w.post(f"/catalogue-worker/jobs/{job_id}/progress", json={"done_parts": 2, "total_parts": 3, "message": "Rear Bumper: REAR BUMPER"})
    check(r.status_code == 200 and r.json() == {"cancelled": False}, "progress -> {cancelled: false}", brief(r))
    prog = W.o.get(f"/reports/{rid}/catalogue").json()["job"]["progress"]
    check(prog == {"done_parts": 2, "total_parts": 3, "message": "Rear Bumper: REAR BUMPER"}, "GET shows the progress", repr(prog))

    r = W.w.post(f"/catalogue-worker/jobs/{job_id}/result", json=first_result())
    check(r.status_code == 200 and r.json() == {"ok": True, "suggestions": 4},
          "result -> {ok: true, suggestions: 4} (the clip on two diagrams stored once)", brief(r))
    r = W.w.post(f"/catalogue-worker/jobs/{job_id}/result", json=first_result())
    check(r.status_code == 409 and code_of(r) == "job_not_running", "the same result again -> 409 job_not_running", brief(r))
    r = W.w.post(f"/catalogue-worker/jobs/{job_id}/progress", json={"done_parts": 3, "total_parts": 3})
    check(r.json() == {"cancelled": True}, "progress on a finished job -> cancelled: true (stop)", brief(r))

    view = W.o.get(f"/reports/{rid}/catalogue").json()
    check(view["job"]["status"] == "done" and view["job"]["finished_at"], "GET: done, with finished_at", repr(view["job"]))
    check(view.get("vehicle") == VEHICLE, "vehicle: the catalogue car the worker used", repr(view.get("vehicle")))
    check(view.get("candidates") == [VEHICLE] and len(view.get("failures") or []) == 1, "candidates and failures as sent",
          repr((view.get("candidates"), view.get("failures"))))
    parts = view.get("parts") or []
    check([p["source_part"] for p in parts] == ["Rear Bumper", "Left Tail Lamp"], "parts grouped by damaged part, in the job's order",
          repr([p["source_part"] for p in parts]))
    check(view.get("not_found") == ["Rear Number Plate"], "not_found: the part no catalogue had", repr(view.get("not_found")))
    bumper = parts[0] if parts else {}
    check((bumper.get("query"), bumper.get("side")) == ("rear bumper", None), "each part keeps its query and side", repr(bumper))
    diagrams = bumper.get("diagrams") or []
    check([d["diagram_name"] for d in diagrams] == ["REAR BUMPER", "REAR BUMPER (2)"] and diagrams[1]["match_score"] == 0.7,
          "then by diagram, best first, with its name, links and score", repr([(d["diagram_name"], d["match_score"]) for d in diagrams]))
    check([s["part_number"] for s in diagrams[0]["rows"]] == ["04715T7WA90", "91503TG7003"]
          and [s["part_number"] for s in diagrams[1]["rows"]] == ["71598T7WA01"],
          "rows in the catalogue's order; the duplicate clip only once", repr([[s["part_number"] for s in d["rows"]] for d in diagrams]))
    s = diagrams[0]["rows"][1] if diagrams and len(diagrams[0]["rows"]) > 1 else {}
    want_keys = {"id", "callout", "part_number", "superseded_from", "name", "name_original", "qty", "side", "notes",
                 "ref_amount", "ref_currency", "ref_tax_note", "source", "url", "fetched_at", "stale", "status",
                 "decided_by", "decided_at", "match_hint"}
    check(want_keys <= set(s), "a row has the contract's Suggestion fields", repr(sorted(want_keys - set(s))))
    check(s.get("qty") == "4" and s.get("status") == "suggested" and s.get("decided_by") is None,
          "a numeric qty kept as text; status suggested, nobody decided", repr(s))
    check(s.get("match_hint") == {"kind": None, "line_index": None, "line_name": None}, "no Annex A yet: no match hint", repr(s.get("match_hint")))
    db = SessionLocal()
    try:
        r_ = db.get(Report, rid)
        check(not ((r_.data or {}).get("annex_a") or {}).get("items"), "nothing was written into Annex A")
    finally:
        db.close()
    W.happy_job = job_id


def cancelling() -> None:
    turn_on()
    rid = new_case(W.owner)
    r = W.o.post(f"/reports/{rid}/catalogue/lookup", json={})
    pending = r.json()["job"]["id"]
    r = W.o.post(f"/reports/{rid}/catalogue/cancel")
    check(r.status_code == 200 and r.json()["job"]["status"] == "cancelled" and r.json()["job"]["finished_at"],
          "cancelling a pending job -> 200, cancelled", brief(r))
    check(("catalogue_lookup_cancelled", "was pending") in history(rid), "the history says so", repr(history(rid)[-1:]))
    r = W.w.post("/catalogue-worker/claim", json={"worker": "test-pc"})
    check(r.json().get("job") is None, "a cancelled job is never handed out", brief(r))
    r = W.o.post(f"/reports/{rid}/catalogue/cancel")
    check(r.status_code == 200 and r.json()["job"]["id"] == pending, "cancel pressed again: 200, the same job", brief(r))

    r = W.o.post(f"/reports/{rid}/catalogue/lookup", json={})
    check(r.status_code == 202, "a new look-up after a cancelled one is allowed", brief(r))
    job_id = r.json()["job"]["id"]
    W.w.post("/catalogue-worker/claim", json={"worker": "test-pc"})
    r = W.o.post(f"/reports/{rid}/catalogue/cancel")
    check(r.status_code == 200 and r.json()["job"]["status"] == "cancelled", "cancelling a running job", brief(r))
    r = W.w.post(f"/catalogue-worker/jobs/{job_id}/progress", json={"done_parts": 1, "total_parts": 3})
    check(r.status_code == 200 and r.json() == {"cancelled": True}, "the worker's next progress -> {cancelled: true}", brief(r))
    r = W.w.post(f"/catalogue-worker/jobs/{job_id}/result", json=first_result())
    check(r.status_code == 409 and code_of(r) == "job_not_running", "its result -> 409 job_not_running (dropped)", brief(r))
    check(not rows_of(rid), "and nothing of it is stored", str(len(rows_of(rid))))
    check(job_row(job_id).status == "cancelled", "the job stays cancelled")
    r = W.o.post(f"/reports/{W.case}/catalogue/cancel")
    check(r.status_code == 409 and code_of(r) == "job_not_open", "cancel when the latest look-up is done -> 409 job_not_open", brief(r))
    fresh = new_case(W.owner)
    r = W.o.post(f"/reports/{fresh}/catalogue/cancel")
    check(r.status_code == 409 and code_of(r) == "job_not_open", "cancel with no look-up at all -> 409 job_not_open", brief(r))
    for path in ("/catalogue-worker/jobs/999999/progress",):
        r = W.w.post(path, json={"done_parts": 0, "total_parts": 1})
        check(r.json() == {"cancelled": True}, "progress on a job that doesn't exist -> cancelled: true", brief(r))
    r = W.w.post("/catalogue-worker/jobs/999999/result", json=first_result())
    check(r.status_code == 409, "a result for a job that doesn't exist -> 409", brief(r))


def stale_and_open_rule() -> None:
    turn_on()
    rid = new_case(W.owner)
    r = W.o.post(f"/reports/{rid}/catalogue/lookup", json={})
    job_id = r.json()["job"]["id"]
    W.w.post("/catalogue-worker/claim", json={"worker": "test-pc"})
    W.w.post(f"/catalogue-worker/jobs/{job_id}/progress", json={"done_parts": 1, "total_parts": 3, "message": "x"})
    db = SessionLocal()
    try:
        db.get(CatalogueJob, job_id).last_activity_at = utc_now() - timedelta(minutes=44)
        db.commit()
    finally:
        db.close()
    r = W.w.post("/catalogue-worker/claim", json={"worker": "other-pc"})
    check(r.json().get("job") is None and job_row(job_id).status == "running", "quiet for 44 minutes: still running, not handed out",
          brief(r))
    db = SessionLocal()
    try:
        db.get(CatalogueJob, job_id).last_activity_at = utc_now() - timedelta(minutes=46)
        db.commit()
    finally:
        db.close()
    r = W.w.post("/catalogue-worker/claim", json={"worker": "other-pc"})
    got = r.json().get("job") or {}
    j = job_row(job_id)
    check(got.get("job_id") == job_id and j.status == "running" and j.claimed_by == "other-pc",
          "quiet for 46 minutes: back to pending, and the next claim gets it", repr((got.get("job_id"), j.status, j.claimed_by)))
    check(j.progress_done == 0 and j.progress_message is None, "its progress starts again", repr((j.progress_done, j.progress_message)))
    db = SessionLocal()
    try:
        db.add(CatalogueJob(report_id=rid, status="pending", payload={"parts": []}))
        try:
            db.commit()
            ok = False
        except IntegrityError:
            db.rollback()
            ok = True
    finally:
        db.close()
    check(ok, "the table itself refuses a second open job for one case (a double click)")
    r = W.w.post(f"/catalogue-worker/jobs/{job_id}/result", json=result([], status="failed", error_code="daily_cap_reached",
                                                                        error_text="made-up: the day's cap"))
    check(r.status_code == 200 and r.json() == {"ok": True, "suggestions": 0}, "a failed result -> 200, nothing stored", brief(r))
    view = W.o.get(f"/reports/{rid}/catalogue").json()
    check(view["job"]["status"] == "failed" and view["job"]["error_code"] == "daily_cap_reached"
          and view["job"]["error_text"] == "made-up: the day's cap", "GET: failed, with the worker's code and words", repr(view["job"]))
    check(view.get("vehicle") is None and view.get("candidates") == [VEHICLE],
          "no done look-up: vehicle null; the failed one's candidates still offered", repr((view.get("vehicle"), view.get("candidates"))))
    r = W.o.post(f"/reports/{rid}/catalogue/lookup", json={})
    check(r.status_code == 202, "after a failed one a new look-up may start", brief(r))
    W.o.post(f"/reports/{rid}/catalogue/cancel")


def validation() -> None:
    turn_on()
    rid = new_case(W.owner)
    good_row = row("04715T7WA90", "FACE, RR. BUMPER")

    def one_part(**diagram_over) -> dict:
        d = {**diagram("microfiches:1:rb", "REAR BUMPER", [good_row]), **diagram_over}
        return result([{"source_part": "Rear Bumper", "query": "rear bumper", "side": None, "diagrams": [d], "failures": []}])

    bad = {
        "a link over http": one_part(diagram_url="http://www.parts-honda.uk/d/1"),
        "a link on another site": one_part(image_url="https://evil.example/x.png"),
        "a look-alike host": one_part(diagram_url="https://parts-honda.uk.evil.example/d"),
        "a sub-domain other than www.": one_part(image_url="https://img.partsouq.com/x.png"),
        "a link with a user name in it": one_part(diagram_url="https://parts-honda.uk@evil.example/d"),
        "a link on another port": one_part(diagram_url="https://parts-honda.uk:8443/d"),
        "a javascript: link": one_part(diagram_url="javascript:alert(1)"),
        "a row's link off the sites": one_part(rows=[{**good_row, "url": "https://evil.example/p"}]),
        "the vehicle's link off the sites": result([], vehicle={**VEHICLE, "url": "https://evil.example/car"}),
        "251 rows on a diagram": one_part(rows=[good_row] * 251),
        "a 401-character name": one_part(rows=[{**good_row, "name": "x" * 401}]),
        "4 diagrams for a part": result([{"source_part": "Rear Bumper", "diagrams": [diagram(f"k{i}", "D", [good_row]) for i in range(4)]}]),
        "41 parts": result([{"source_part": "Rear Bumper", "diagrams": []}] * 41),
        "11 candidates": result([], candidates=[VEHICLE] * 11),
        "a match score over 1": one_part(match_score=1.5),
        "a negative price": one_part(rows=[{**good_row, "ref_amount": -1}]),
        "an unknown status": result([], status="maybe"),
        "an unknown error code": result([], status="failed", error_code="made_up_code"),
        "a part the job never asked about": result([{"source_part": "Bonnet", "diagrams": []}]),
    }
    for label, body in bad.items():
        r = W.o.post(f"/reports/{rid}/catalogue/lookup", json={})
        job_id = r.json()["job"]["id"]
        W.w.post("/catalogue-worker/claim", json={"worker": "test-pc"})
        r = W.w.post(f"/catalogue-worker/jobs/{job_id}/result", json=body)
        j = job_row(job_id)
        check(r.status_code == 422 and code_of(r) == "bad_result" and j.status == "failed" and j.error_code == "internal",
              f"{label} -> 422 bad_result, and the job ends failed", f"{brief(r)} | job {j.status} {j.error_code}")
    check(not rows_of(rid), "nothing of a refused result is stored", str(len(rows_of(rid))))
    r = W.o.post(f"/reports/{rid}/catalogue/lookup", json={})
    job_id = r.json()["job"]["id"]
    W.w.post("/catalogue-worker/claim", json={"worker": "test-pc"})
    okay = one_part(diagram_url="https://partsouq.com/d/1", image_url="https://www.bmw-spare-parts.com/i.png",
                    rows=[{**good_row, "url": "https://mini-spare-parts.com/p", "name": "x" * 400, "callout": 7, "stale": None}])
    r = W.w.post(f"/catalogue-worker/jobs/{job_id}/result", json=okay)
    check(r.status_code == 200 and r.json()["suggestions"] == 1,
          "the four sites with or without www., 400 characters, a numeric callout, stale null: accepted", brief(r))
    s = rows_of(rid)[0] if rows_of(rid) else None
    check(s is not None and s.callout == "7" and s.stale is False, "stored as text and false", repr(s and (s.callout, s.stale)))
    r = W.w.post("/catalogue-worker/claim", json={"worker": "test-pc"})
    check(r.json().get("job") is None, "no refused job comes round again", brief(r))


def rerun() -> None:
    turn_on()
    rid = new_case(W.owner)
    _, out = run_job(W.o, rid, first_result())
    check(out.get("suggestions") == 4, "first look-up: 4 rows", repr(out))
    by_number = {s.part_number: s for s in rows_of(rid)}
    face, clip, spacer = by_number["04715T7WA90"], by_number["91503TG7003"], by_number["71598T7WA01"]
    r = W.o.post(f"/reports/{rid}/catalogue/suggestions/{face.id}", json={"action": "reject"})
    check(r.status_code == 200 and r.json()["suggestion"]["status"] == "rejected", "the bumper face rejected", brief(r))
    db = SessionLocal()
    try:
        # Phase 2 accepts through PUT /annex-a; here the row is marked as that will.
        db.get(CatalogueSuggestion, clip.id).status = "accepted"
        db.commit()
    finally:
        db.close()
    second = result([
        {"source_part": "Rear Bumper", "diagrams": [
            diagram("microfiches:30584:rb", "REAR BUMPER", [
                row("04715-T7W-A90", "FACE, RR. BUMPER"),          # rejected before: not again
                row("91503TG7003", "CLIP, BUMPER"),                # accepted before: not again
                row("71598T7WA01", "SPACER, R. RR. BUMPER SIDE"),  # suggested before: replaced
                row("71530T7WA00", "BEAM, RR. BUMPER"),            # new
                row(None, "LABEL, CAUTION"),                       # no number: kept as it is
            ]),
        ]},
        {"source_part": "Left Tail Lamp", "diagrams": []},
        {"source_part": "Rear Number Plate", "diagrams": []},
    ])
    _, out = run_job(W.o, rid, second)
    check(out.get("suggestions") == 3, "second look-up: 3 rows stored (the rejected and the accepted ones not again)", repr(out))
    rows = rows_of(rid)
    numbers = [s.part_number for s in rows]
    check(all(numbers.count(n) == 1 for n in ("04715T7WA90", "91503TG7003", "71598T7WA01", "71530T7WA00")),
          "no part number twice for one part (spelling with dashes counts as the same)", repr(numbers))
    kept = {s.id: s.status for s in rows}
    check(kept.get(face.id) == "rejected" and kept.get(clip.id) == "accepted", "the rejected and accepted rows stay, as they were",
          repr(kept))
    check(spacer.id not in kept, "the old suggested row is replaced, not kept beside the new one", repr(sorted(kept)))
    check(not any(s.part_number == "33550T7WA01" for s in rows), "the old tail lamp suggestion is gone with it",
          repr(numbers))
    view = W.o.get(f"/reports/{rid}/catalogue").json()
    check(view.get("not_found") == ["Left Tail Lamp", "Rear Number Plate"], "not_found follows the latest look-up",
          repr(view.get("not_found")))
    statuses = sorted(s["status"] for p in view["parts"] for d in p["diagrams"] for s in d["rows"])
    check(statuses == ["accepted", "rejected", "suggested", "suggested", "suggested"], "GET lists the kept rows with the new ones",
          repr(statuses))
    W.rerun_case = rid


def reject_undo() -> None:
    turn_on()
    rid = W.case
    rows = rows_of(rid)
    sid = next(s.id for s in rows if s.status == "suggested")
    r = W.o.post(f"/reports/{rid}/catalogue/suggestions/{sid}", json={"action": "reject"})
    s = r.json().get("suggestion") or {}
    check(r.status_code == 200 and s.get("status") == "rejected" and s.get("decided_by") and s.get("decided_at"),
          "reject -> rejected, by whom and when", brief(r))
    r = W.o.post(f"/reports/{rid}/catalogue/suggestions/{sid}", json={"action": "reject"})
    check(r.status_code == 200 and r.json()["suggestion"]["decided_at"] == s.get("decided_at"), "rejecting again changes nothing", brief(r))
    r = W.o.post(f"/reports/{rid}/catalogue/suggestions/{sid}", json={"action": "undo"})
    s = r.json().get("suggestion") or {}
    check(r.status_code == 200 and s.get("status") == "suggested" and s.get("decided_by") is None and s.get("decided_at") is None,
          "undo -> suggested again, nobody's decision", brief(r))
    r = W.o.post(f"/reports/{rid}/catalogue/suggestions/{sid}", json={"action": "accept"})
    check(r.status_code == 422, "accept isn't this route's (Phase 2: PUT /annex-a) -> 422", brief(r))
    accepted = next(s.id for s in rows_of(W.rerun_case) if s.status == "accepted")
    r = W.o.post(f"/reports/{W.rerun_case}/catalogue/suggestions/{accepted}", json={"action": "reject"})
    check(r.status_code == 409 and code_of(r) == "accepted", "an accepted row -> 409 accepted (it's in Annex A)", brief(r))
    r = W.o.post(f"/reports/{W.rerun_case}/catalogue/suggestions/{sid}", json={"action": "reject"})
    check(r.status_code == 404, "a row of another case -> 404", brief(r))
    r = W.b.post(f"/reports/{rid}/catalogue/suggestions/{sid}", json={"action": "reject"})
    check(r.status_code == 404, "a stranger -> 404", brief(r))
    set_data(rid, sign_off={"status": "Signed Off", "signed_by_user_id": W.admin})
    r = W.o.post(f"/reports/{rid}/catalogue/suggestions/{sid}", json={"action": "reject"})
    check(r.status_code == 409 and code_of(r) == "signed_off", "on a signed-off case -> 409 signed_off", brief(r))
    view = W.o.get(f"/reports/{rid}/catalogue").json()
    check(view.get("blocked_reason") == "signed_off" and view.get("parts"), "a signed-off case still shows its rows", repr(view.get("blocked_reason")))
    set_data(rid, sign_off={"status": "Draft"})


def match_hints() -> None:
    turn_on()
    rid = W.case
    annex = [
        {"part_name": "Rear bumper fascia", "source_part": "Rear Bumper", "workshop_est": 300},
        {"part_name": "Tail lamp LH", "source_part": "Left Tail Lamp", "workshop_est": 120},
        {"part_name": "Rear bumper side spacer LH", "source_part": "Rear Bumper", "workshop_est": 5},
        {"part_name": "Rear bumper clips", "source_part": "Rear Bumper", "oem_part_number": "91503-TG7-003", "workshop_est": 8},
        {"part_name": "Front bumper reinforcement", "source_part": "Front Bumper", "workshop_est": 200},
    ]
    set_data(rid, annex_a={"items": annex})
    view = W.o.get(f"/reports/{rid}/catalogue").json()
    hints = {s["part_number"]: s["match_hint"] for p in view["parts"] for d in p["diagrams"] for s in d["rows"]}
    check(hints.get("04715T7WA90") == {"kind": "line", "line_index": 0, "line_name": "Rear bumper fascia"},
          "\"FACE, RR. BUMPER\" is the \"Rear bumper fascia\" line (index 0)", repr(hints.get("04715T7WA90")))
    check(hints.get("33550T7WA01") == {"kind": "line", "line_index": 1, "line_name": "Tail lamp LH"},
          "\"LIGHT ASSY., L. TAIL\" is \"Tail lamp LH\"", repr(hints.get("33550T7WA01")))
    check(hints.get("91503TG7003") == {"kind": "line", "line_index": 3, "line_name": "Rear bumper clips"},
          "a line carrying the same part number (dashes aside) is the match", repr(hints.get("91503TG7003")))
    check((hints.get("71598T7WA01") or {}).get("kind") is None,
          "\"SPACER, R. RR. BUMPER SIDE\" (right) is not the LH spacer line", repr(hints.get("71598T7WA01")))

    lines = catalogue.LineMatcher(annex)

    def hint(name, source="Rear Bumper", query="rear bumper", side=None, part_side=None, number=None):
        return lines.hint(CatalogueSuggestion(name=name, source_part=source, part_query=query, side=side,
                                              part_side=part_side, part_number=number))

    check(hint("FACE, FR. BUMPER", source="Front Bumper", query="front bumper")["kind"] is None,
          "a front bumper face never matches the rear bumper fascia", repr(hint("FACE, FR. BUMPER", source="Front Bumper")))
    check(hint("LAMP ASSY, RR COMBINATION, RH", source="Right Tail Lamp", query="tail lamp", part_side="right")["kind"] is None,
          "a right tail lamp never matches the LH line", "")
    check(hint("LAMP ASSY, RR COMBINATION, LH", source="Left Tail Lamp", query="tail lamp")["line_index"] == 1,
          "\"LAMP ASSY, RR COMBINATION, LH\" (Toyota's wording) is \"Tail lamp LH\"", repr(hint("LAMP ASSY, RR COMBINATION, LH")))
    check(hint("Trim cover, bumper, rear")["line_index"] == 0, "\"Trim cover, bumper, rear\" (BMW's wording) is the fascia",
          repr(hint("Trim cover, bumper, rear")))
    check(hint("BRACKET, RR. BUMPER SIDE")["kind"] is None, "a bracket is no fascia nor spacer: no hint",
          repr(hint("BRACKET, RR. BUMPER SIDE")))
    check(hint("REINFORCEMENT, FR. BUMPER", source="Front Bumper Beam", query="front bumper beam")["line_index"] == 4,
          "a line made for another part still matches when nothing of the same part does",
          repr(hint("REINFORCEMENT, FR. BUMPER", source="Front Bumper Beam", query="front bumper beam")))
    check(hint(None)["kind"] is None and hint("")["kind"] is None, "no name: no hint")
    set_data(rid, annex_a={"items": []})


def deleting() -> None:
    turn_on()
    rid = new_case(W.owner)
    run_job(W.o, rid, first_result())
    r = W.o.post(f"/reports/{rid}/catalogue/lookup", json={})
    open_job = r.json()["job"]["id"]
    W.w.post("/catalogue-worker/claim", json={"worker": "test-pc"})
    check(len(rows_of(rid)) == 4, "a case with rows and a running look-up")
    r = W.a.delete(f"/reports/{rid}")
    check(r.status_code == 200, "an admin deletes the case", brief(r))
    db = SessionLocal()
    try:
        left = (db.query(CatalogueJob).filter(CatalogueJob.report_id == rid).count(),
                db.query(CatalogueSuggestion).filter(CatalogueSuggestion.report_id == rid).count())
    finally:
        db.close()
    check(left == (0, 0), "its look-ups and suggestions go with it", repr(left))
    r = W.w.post(f"/catalogue-worker/jobs/{open_job}/progress", json={"done_parts": 1, "total_parts": 3})
    check(r.json() == {"cancelled": True}, "the worker still busy with it hears cancelled", brief(r))
    r = W.w.post(f"/catalogue-worker/jobs/{open_job}/result", json=first_result())
    check(r.status_code == 409, "...and its result is dropped (409)", brief(r))


def no_ai_no_network() -> None:
    check(not GEMINI, "no Gemini call anywhere in this test", repr(GEMINI))
    check(not OUTSIDE, "no connection outside this machine", repr(OUTSIDE))
    src = "\n".join((ROOT / "app" / sub / "catalogue.py").read_text(encoding="utf-8") for sub in ("parts", "api"))
    imports = re.findall(r"^\s*(?:import|from)\s+([\w.]+)", src, flags=re.M)
    outside = [m for m in imports if m.split(".")[0] in ("httpx", "requests", "urllib3", "aiohttp", "socket", "google", "http")
               or m in ("urllib.request", "app.ai.structured", "app.ai.client", "app.ai.extraction")]
    check(imports and not outside, "the look-up's code imports nothing that talks to the network or the AI", repr(outside))


def main_() -> int:
    for name, fn in (
        ("setup", setup),
        ("off by default", feature_off),
        ("the worker's token and the middleware", token),
        ("the job carries the car and part names only", payload_privacy),
        ("who may start a look-up, and why not", refusals),
        ("claim -> progress -> result, and the page's view", happy_path),
        ("cancelling", cancelling),
        ("a job gone quiet, and one open job per case", stale_and_open_rule),
        ("the result's checks", validation),
        ("a re-run keeps accepted and rejected rows", rerun),
        ("reject and undo", reject_undo),
        ("match hints against Annex A", match_hints),
        ("deleting a case", deleting),
        ("no AI, no network", no_ai_no_network),
    ):
        run_section(name, fn)
    turn_on(False, None)
    print(f"\ntest_catalogue: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        for section, label in FAILURES:
            print(f"  FAIL [{section}] {label}")
        return 1
    print("\nALL CATALOGUE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
