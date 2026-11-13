"""The parts-catalogue look-up, Phase 2: putting a catalogue row on an
Annex A line (AnnexAPartItem.catalogue_ref; plans/2026-11-12-parts-catalogue/
CONTRACT.md "Phase 2").

    python scripts/test_catalogue_accept.py

Checked: PUT /annex-a takes a line's catalogue_ref only for a suggestion of
this case (another case's, or one gone: 422 catalogue_ref, nothing saved),
links only https on the parts sites, and writes the ref's facts from the
stored row whatever the client sent; the save marks the rows on lines
accepted (by whom, when), a row no line carries any more suggested again, a
hidden row put on a line accepted; the estimate is never changed by a ref,
a new catalogue line stays unpriced, an attached past-case price stays
prefilled and "check" in review, and its origin isn't turned into "typed"
by the part number the catalogue brought (nor by unlinking it); a client
that leaves catalogue_ref out keeps the line's ref, an explicit null takes
it off; a re-sync after a damage edit, the prefill and a re-run keep every
ref, also on an unpriced detection row; neither the PDF's HTML nor the
Excel workbook ever shows a part number or a reference price; GET
.../catalogue says "no_parts" (button disabled) instead of a 400 on click;
the worker and the server agree on links and caps (tools/parts_mcp/tests/
fixtures/catalogue_links.json); a null diagram key and a %-encoded car key
are taken; a name the site cut mid-bracket still finds its Annex A line.

Phase 3 (CONTRACT.md "Phase 3"): tidy_name on the live site's names (the
original "name" untouched); colour versions share a variant_key only when
callout, name, qty, side and price match and the numbers differ by a colour
code alone -- also in the answer to Hide; a diagram an earlier damaged part
shows carries same_as_part with the rows that part shows dropped (a part of
the other side keeps its own, a row on a line stays); a make outside
CATALOGUE_MAKES is "make_not_covered" (GET and a 400 on POST lookup), "Mini"
is MINI; the look-up queued by itself after the AI reading
(catalogue.maybe_auto_lookup): only with the feature on, a covered make, a
model, damaged parts and no look-up ever before, requested_by "auto (after
detection)", in the case history, once -- and pipeline._read_into still
saves the reading when the look-up breaks; a deleted or expired draft takes
its look-up along.

The review's fixes (PR #31): POST .../catalogue/lookup is capped per
person -- 3 in 24 hours without staff access, 20 an hour otherwise, 429
lookup_limit; cancelled ones count, refused and automatic ones don't; a
worker result sent in the middle of an Annex A save waits for its commit
(the save takes the write lock first) and keeps the row the line now
carries; one landing during PUT /reports/{id}'s render takes the replaced
ref off the line (200, never a 500); a new look-up never deletes a row a
saved line carries; POST /reports, an intake draft's autosave and its
filing keep only refs to the case's own rows (rewritten from the stored
row) and take any other off instead of refusing.
No Gemini, nothing outside this machine.

Offline and self-contained, as scripts/test_catalogue.py: a temporary
STORAGE_DIR, SECRETS_DIR and database, Gemini replaced by a fake that fails
the test if anything calls it, every outside connection refused. Every
plate, VIN and name below is made up.
"""
import io
import json
import logging
import os
import re
import socket
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent.parent
sys.path.insert(0, str(ROOT))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
_tmp = Path(tempfile.mkdtemp(prefix="catalogue-accept-test-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
os.environ["STORAGE_DIR"] = str(STORAGE)
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaSyFakeCompanyKeyForCatalogueTest0002"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ["MIN_FREE_DISK_GB"] = "0"
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
from openpyxl import load_workbook  # noqa: E402

import app.api.catalogue as catalogue_api  # noqa: E402
import app.api.main as main  # noqa: E402
import app.rendering.renderer as renderer  # noqa: E402
from app.ai.extraction import _response_schema  # noqa: E402
from app.api import limits  # noqa: E402
from app.auth.core import SESSION_COOKIE, create_session  # noqa: E402
from app.config import settings  # noqa: E402
from app.parts import catalogue  # noqa: E402
from app.parts.prefill import sync_annex_a  # noqa: E402
from app.rendering.xlsx_export import render_xlsx  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import (  # noqa: E402
    AuditEntry, CatalogueJob, CatalogueSuggestion, IntakeSession, Report, User, gen_id,
)
from app.reports.review import annex_a_state  # noqa: E402
from app.reports.schema import CatalogueRef, SecurityIncidentDraft  # noqa: E402

app = main.app

# ---------------------------------------------------------------- harness
PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}
BASE = "https://carlink.test"
TOKEN = "test-worker-token-" + gen_id() + gen_id()
W = SimpleNamespace()
PLATE, VIN = "TEST 0001", "TESTVIN0000000001"


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


def turn_on(flag: bool = True) -> None:
    settings.catalogue_enabled = flag
    settings.catalogue_worker_token = TOKEN if flag else ""


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


def damage(part: str, **kw) -> dict:
    return {"part": part, "damage_type": "Dent", "severity": "Moderate", **kw}


def line(name: str, **kw) -> dict:
    out = {"item_no": "1.0", "part_name": name, "condition": "", "action": "", "qty": 1.0, "qty_text": None,
           "workshop_est": 0.0, "discount_pct": 0.2, "adjusted_cost": 0.0, "is_net_item": False,
           "oem_part_number": None, "suggested_from": None, "usual_unit_price": None, "prefilled": False,
           "origin": "detection", "source_part": None, "stale": False, "main": False}
    out.update(kw)
    return out


def case_data(**over) -> dict:
    data = SecurityIncidentDraft(description="Made-up test case.", category=["Vehicle Collision or Damage"]).model_dump()
    data["report_id"] = "CL TEST 1"
    data["vehicle_info"].update({"plate_number": PLATE, "make": "Honda", "model": "Vezel", "year": "2016", "vin": VIN})
    data["damage_summary"] = [damage("Rear Bumper"), damage("Boot Lid"), damage("Left Tail Lamp"), damage("Rear Bumper Beam")]
    data["damaged_parts"] = [d["part"] for d in data["damage_summary"]]
    data["annex_a"] = {"items": [
        # Priced from past cases: the estimate is the workshop's figure the
        # past cases usually asked, prefilled (review: "check").
        line("Rear bumper fascia", workshop_est=900.0, origin="history", prefilled=True,
             suggested_from="2 past Honda Vezel cases (Honda Vezel)", usual_unit_price=720.0,
             source_part="Rear Bumper", main=True, condition="Deformed/cut"),
        line("Tail lamp LH", source_part="Left Tail Lamp", main=True),
        line("Tail gate assy", source_part="Boot Lid", main=True),
        line("Rear bumper reinforcement", source_part="Rear Bumper Beam", main=True),
    ]}
    data.update(over)
    return data


def new_case(owner_id: str | None, data: dict | None = None, status: str = "confirmed") -> str:
    db = SessionLocal()
    try:
        r = Report(channel="manual", reporter_chat_id="dashboard", status=status, owner_id=owner_id,
                   data=data if data is not None else case_data(), photo_paths=[])
        db.add(r)
        db.commit()
        return r.id
    finally:
        db.close()


def report_data(report_id: str) -> dict:
    db = SessionLocal()
    try:
        return dict(db.get(Report, report_id).data or {})
    finally:
        db.close()


def items_of(report_id: str) -> list[dict]:
    return [dict(it) for it in (report_data(report_id).get("annex_a") or {}).get("items") or []]


def suggestions(report_id: str) -> dict[int, CatalogueSuggestion]:
    db = SessionLocal()
    try:
        out = db.query(CatalogueSuggestion).filter(CatalogueSuggestion.report_id == report_id).all()
        for s in out:
            db.expunge(s)
        return {s.id: s for s in out}
    finally:
        db.close()


def by_number(report_id: str, number: str) -> CatalogueSuggestion:
    return next(s for s in suggestions(report_id).values() if s.part_number == number)


def history(report_id: str) -> list[tuple[str, str]]:
    db = SessionLocal()
    try:
        return [(e.action, e.detail or "") for e in
                db.query(AuditEntry).filter(AuditEntry.report_id == report_id).order_by(AuditEntry.id)]
    finally:
        db.close()


def row(part_number, name, amount=10.0, **kw) -> dict:
    out = {"callout": "1", "part_number": part_number, "superseded_from": None, "name": name, "name_original": None,
           "qty": "1", "side": None, "notes": None, "ref_amount": amount, "ref_currency": "EUR",
           "ref_tax_note": "excl. VAT", "source": "microfiches",
           "url": "https://www.parts-honda.uk/part/" + (part_number or "x"),
           "fetched_at": "2026-11-12T10:42:00+00:00", "stale": False}
    out.update(kw)
    return out


def diagram(key, name, rows, score=1.0) -> dict:
    return {"diagram_key": key, "diagram_name": name, "diagram_url": "https://www.parts-honda.uk/d/" + (key or "x").replace(":", "-"),
            "image_url": "https://www.parts-honda.uk/img/" + (key or "x").replace(":", "-") + ".png",
            "match_score": score, "rows": rows}


VEHICLE = {"key": "microfiches:30584", "source": "microfiches", "brand": "Honda", "model": "HR-V",
           "name": "HR-V 1.5 i-VTEC", "market": "Europe", "year_from": 2015, "year_to": 2019,
           "url": "https://www.parts-honda.uk/hr-v", "twin_of": "Vezel"}

# Reference prices chosen so they can be searched for in the exports.
FACE, CLIP, TAIL, BEAM, GATE = "04715T7WA90", "91503TG7003", "33550T7WA01", "71530T7WA00", "68100T7WA00"
PRICES = {FACE: 309.41, CLIP: 1.87, TAIL: 187.23, BEAM: 141.59, GATE: 533.77}


def the_result() -> dict:
    return {"status": "done", "error_code": None, "error_text": None, "vehicle": VEHICLE, "candidates": [VEHICLE],
            "failures": [], "parts": [
                {"source_part": "Rear Bumper", "query": "rear bumper", "side": None, "failures": [], "diagrams": [
                    diagram("microfiches:30584:rb", "REAR BUMPER", [
                        row(FACE, "FACE, RR. BUMPER", PRICES[FACE], callout="1"),
                        row(CLIP, "CLIP, BUMPER", PRICES[CLIP], callout="7", qty="4"),
                    ])]},
                {"source_part": "Boot Lid", "query": "boot lid", "side": None, "failures": [], "diagrams": [
                    diagram("microfiches:30584:tg", "TAILGATE", [row(GATE, "TAILGATE (DOOR)", PRICES[GATE])])]},
                {"source_part": "Left Tail Lamp", "query": "tail lamp", "side": "left", "failures": [], "diagrams": [
                    diagram("microfiches:30584:tl", "TAILLIGHT", [row(TAIL, "LIGHT ASSY., L. TAIL", PRICES[TAIL], side="left")])]},
                {"source_part": "Rear Bumper Beam", "query": "rear bumper beam", "side": None, "failures": [], "diagrams": [
                    diagram("microfiches:30584:rbb", "REAR BUMPER BEAM", [row(BEAM, "BEAM, RR. BUMPER", PRICES[BEAM], qty="AR")])]},
            ]}


def look_up(client: TestClient, report_id: str, body: dict | None = None, vehicle_key=None) -> None:
    """lookup -> claim -> result, the result cut to the parts the job asked."""
    r = client.post(f"/reports/{report_id}/catalogue/lookup", json={"vehicle_key": vehicle_key})
    assert r.status_code == 202, brief(r)
    job_id = r.json()["job"]["id"]
    r = W.w.post("/catalogue-worker/claim", json={"worker": "test-pc", "version": "0.1"})
    job = r.json()["job"] or {}
    assert r.status_code == 200 and job.get("job_id") == job_id, brief(r)
    body = body or the_result()
    asked = {p["source_part"] for p in job["parts"]}
    body = {**body, "parts": [p for p in body["parts"] if p["source_part"] in asked]}
    r = W.w.post(f"/catalogue-worker/jobs/{job_id}/result", json=body)
    assert r.status_code == 200, brief(r)


def ref_for(s: CatalogueSuggestion, **over) -> dict:
    out = {"suggestion_id": s.id, "source": s.source, "part_number": s.part_number, "name": s.name,
           "diagram_name": s.diagram_name, "diagram_url": s.diagram_url, "ref_amount": s.ref_amount,
           "ref_currency": s.ref_currency, "ref_tax_note": s.ref_tax_note, "fetched_at": s.fetched_at}
    out.update(over)
    return out


def new_catalogue_line(s: CatalogueSuggestion) -> dict:
    """As the dashboard's lib/catalogue.catalogueLine builds it."""
    return line(s.name, qty=float(s.qty) if (s.qty or "").replace(".", "", 1).isdigit() else 1.0, origin="lookup",
                oem_part_number=s.part_number, suggested_from="catalogue (parts-honda.uk)", source_part=s.source_part,
                catalogue_ref=ref_for(s))


def put(client: TestClient, report_id: str, rows: list[dict]):
    return client.put(f"/reports/{report_id}/annex-a", json={"items": rows})


def named(rows: list[dict], name: str) -> dict:
    return next(r for r in rows if r["part_name"] == name)


# ---------------------------------------------------------------- sections
# The real caps, for lookup_quota; every other section starts look-ups as
# freely as it needs to.
REAL_LOOKUP_CAPS = (limits.LOOKUPS_PER_DAY_UNVETTED, limits.LOOKUPS_PER_HOUR)


def lift_lookup_caps() -> None:
    limits.LOOKUPS_PER_DAY_UNVETTED = limits.LOOKUPS_PER_HOUR = 10_000


def setup() -> None:
    init_db()
    turn_on()
    lift_lookup_caps()
    W.admin = new_user("Admin", role="admin")
    W.owner = new_user("Una")
    W.other = new_user("Ben")
    W.a, W.o, W.b = (signed_in(u) for u in (W.admin, W.owner, W.other))
    W.w = TestClient(app, base_url=BASE)
    W.w.headers["Authorization"] = f"Bearer {TOKEN}"
    W.case = new_case(W.owner)
    look_up(W.o, W.case)
    W.other_case = new_case(W.other)
    look_up(W.b, W.other_case)
    check(len(suggestions(W.case)) == 5 and len(suggestions(W.other_case)) == 5, "two cases looked up, 5 rows each")


def attach_and_add() -> None:
    face, tail = by_number(W.case, FACE), by_number(W.case, TAIL)
    rows = items_of(W.case)
    bumper = named(rows, "Rear bumper fascia")
    # As the dashboard's attachToLine: the ref, and the part number the line
    # didn't have; nothing else. The client's copy of the ref is tampered
    # with: the server writes the stored row's facts.
    bumper["catalogue_ref"] = ref_for(face, ref_amount=1.0, part_number="FAKE-123", name="Made up", diagram_name="X")
    bumper["oem_part_number"] = FACE
    new = new_catalogue_line(tail)
    r = put(W.o, W.case, rows + [new])
    check(r.status_code == 200, "PUT /annex-a with one line attached and one new catalogue line: 200", brief(r))
    saved = items_of(W.case)
    b = named(saved, "Rear bumper fascia")
    check(b["catalogue_ref"] == catalogue.ref_of(face), "the ref's facts are the stored row's, not the client's",
          json.dumps(b["catalogue_ref"]))
    check(b["workshop_est"] == 900.0 and b["adjusted_cost"] == 720.0, "the attached line's W/shop Est. is unchanged (900)",
          f"{b['workshop_est']} {b['adjusted_cost']}")
    check(b["prefilled"] is True and b["origin"] == "history" and b["usual_unit_price"] == 720.0,
          "...and it is still the past cases' price: prefilled, origin history (the part number the catalogue "
          "brought doesn't make it 'typed')", f"{b['prefilled']} {b['origin']}")
    check(b["oem_part_number"] == FACE and b["part_name"] == "Rear bumper fascia" and b["qty"] == 1.0,
          "...the part number filled in; name and qty untouched")
    n = named(saved, "LIGHT ASSY., L. TAIL")
    check(n["workshop_est"] == 0.0 and n["adjusted_cost"] == 0.0 and n["origin"] == "lookup",
          "the new catalogue line is unpriced (est 0), origin lookup", json.dumps({k: n[k] for k in ("workshop_est", "origin")}))
    check(n["catalogue_ref"]["ref_amount"] == PRICES[TAIL] and n["source_part"] == "Left Tail Lamp"
          and n["suggested_from"] == "catalogue (parts-honda.uk)", "...carrying its ref, part and source")
    after = suggestions(W.case)
    check(after[face.id].status == "accepted" and after[tail.id].status == "accepted",
          "both rows on lines are accepted")
    check((after[face.id].decided_by or "").startswith("Una") and after[face.id].decided_at is not None
          and after[face.id].decided_by_id == W.owner, "...by the person who saved, with the time",
          f"{after[face.id].decided_by} {after[face.id].decided_at}")
    check(all(s.status == "suggested" for sid, s in after.items() if sid not in (face.id, tail.id)),
          "the other rows stay suggested")
    check(all(s.status == "suggested" for s in suggestions(W.other_case).values()), "the other case's rows are untouched")
    check(any(a == "annex_a_edited" and "2 with a catalogue part" in d for a, d in history(W.case)),
          "the history says how many lines carry a catalogue part", str(history(W.case)[-1:]))
    view = W.o.get(f"/reports/{W.case}/catalogue").json()
    states = {r["part_number"]: r["status"] for p in view["parts"] for d in p["diagrams"] for r in d["rows"]}
    check(states.get(FACE) == "accepted" and states.get(TAIL) == "accepted" and states.get(CLIP) == "suggested",
          "GET .../catalogue shows them accepted", json.dumps(states))
    r = W.o.post(f"/reports/{W.case}/catalogue/suggestions/{face.id}", json={"action": "reject"})
    check(r.status_code == 409 and code_of(r) == "accepted", "an accepted row can't be hidden (409 accepted)", brief(r))


def review_check() -> None:
    saved = items_of(W.case)
    b = named(saved, "Rear bumper fascia")
    damaged = {"Rear Bumper", "Boot Lid", "Left Tail Lamp", "Rear Bumper Beam"}
    check(annex_a_state(b, damaged, False) == ("check", True),
          "review: the attached past-case price is still 'check' (priced from past cases only)",
          str(annex_a_state(b, damaged, False)))
    n = named(saved, "LIGHT ASSY., L. TAIL")
    check(annex_a_state(n, damaged, False)[0] == "needs", "review: the new catalogue line needs a price")
    r = W.o.get(f"/reports/{W.case}")
    states = (r.json().get("review") or {}).get("annex_a") or []
    names = [it["part_name"] for it in r.json()["data"]["annex_a"]["items"]]
    check(r.status_code == 200 and states[names.index("Rear bumper fascia")] == "check",
          "GET /reports/{id} review says 'check' for it too", f"{states} {names}")


def refused_refs() -> None:
    before = items_of(W.case)
    foreign = next(iter(suggestions(W.other_case).values()))
    rows = items_of(W.case)
    named(rows, "Tail gate assy")["catalogue_ref"] = ref_for(foreign)
    r = put(W.o, W.case, rows)
    check(r.status_code == 422 and code_of(r) == "catalogue_ref", "another case's suggestion: 422 catalogue_ref", brief(r))
    check("line 3" in r.json().get("detail", ""), "...naming the line", r.json().get("detail", ""))
    rows = items_of(W.case)
    named(rows, "Tail gate assy")["catalogue_ref"] = ref_for(foreign, suggestion_id=999_999)
    r = put(W.o, W.case, rows)
    check(r.status_code == 422 and code_of(r) == "catalogue_ref", "a suggestion that doesn't exist: 422", brief(r))
    gate = by_number(W.case, GATE)
    for bad in ("javascript:alert(1)", "http://www.parts-honda.uk/d", "https://evil.example/d",
                "https://user:pw@www.parts-honda.uk/d", "https://www.parts-honda.uk:8443/d"):
        rows = items_of(W.case)
        named(rows, "Tail gate assy")["catalogue_ref"] = ref_for(gate, diagram_url=bad)
        r = put(W.o, W.case, rows)
        check(r.status_code == 422, f"a ref linking to {bad[:34]}: 422", brief(r))
    check(items_of(W.case) == before and suggestions(W.case)[gate.id].status == "suggested",
          "nothing was saved by any refused save")
    rows = items_of(W.case)
    named(rows, "Tail gate assy")["catalogue_ref"] = ref_for(gate)
    r = W.b.put(f"/reports/{W.case}/annex-a", json={"items": rows})
    check(r.status_code in (403, 404), "someone who can't edit the case can't attach (403/404)", brief(r))


def old_client_and_unlink() -> None:
    face, tail = by_number(W.case, FACE), by_number(W.case, TAIL)
    # An older page (no catalogue_ref field) saves the table: the refs stay.
    rows = [{k: v for k, v in it.items() if k != "catalogue_ref"} for it in items_of(W.case)]
    named(rows, "Tail lamp LH")["condition"] = "Cracked"
    r = put(W.o, W.case, rows)
    saved = items_of(W.case)
    check(r.status_code == 200 and named(saved, "Rear bumper fascia")["catalogue_ref"]
          and named(saved, "LIGHT ASSY., L. TAIL")["catalogue_ref"], "a client that leaves catalogue_ref out keeps the refs")
    check(suggestions(W.case)[face.id].status == "accepted", "...and the rows stay accepted")
    # Unlink (lib/catalogue.detachFromLine): ref null, the number it brought off.
    rows = items_of(W.case)
    b = named(rows, "Rear bumper fascia")
    b["catalogue_ref"], b["oem_part_number"] = None, None
    r = put(W.o, W.case, rows)
    b = named(items_of(W.case), "Rear bumper fascia")
    check(r.status_code == 200 and b["catalogue_ref"] is None and b["oem_part_number"] is None,
          "an explicit null unlinks the line")
    check(b["origin"] == "history" and b["prefilled"] is True and b["workshop_est"] == 900.0,
          "...still the past cases' line (unlinking isn't a person's edit), est 900", f"{b['origin']} {b['prefilled']}")
    s = suggestions(W.case)[face.id]
    check(s.status == "suggested" and s.decided_by is None and s.decided_at is None,
          "...and its row is a suggestion again (decided_* cleared)")
    check(suggestions(W.case)[tail.id].status == "accepted", "the other attached row is still accepted")
    # Removing the catalogue line takes its row back too.
    rows = [it for it in items_of(W.case) if it["part_name"] != "LIGHT ASSY., L. TAIL"]
    r = put(W.o, W.case, rows)
    check(r.status_code == 200 and suggestions(W.case)[tail.id].status == "suggested",
          "removing the line with a catalogue part: its row is suggested again")
    check(all(s.status == "suggested" for s in suggestions(W.case).values()), "nothing is accepted now")


def hidden_then_attached() -> None:
    clip = by_number(W.case, CLIP)
    r = W.o.post(f"/reports/{W.case}/catalogue/suggestions/{clip.id}", json={"action": "reject"})
    check(r.status_code == 200 and suggestions(W.case)[clip.id].status == "rejected", "a row hidden")
    r = W.o.post(f"/reports/{W.case}/catalogue/suggestions/{clip.id}", json={"action": "undo"})
    check(r.status_code == 200 and suggestions(W.case)[clip.id].status == "suggested", "...and shown again (undo)")
    W.o.post(f"/reports/{W.case}/catalogue/suggestions/{clip.id}", json={"action": "reject"})
    rows = items_of(W.case) + [new_catalogue_line(suggestions(W.case)[clip.id])]
    r = put(W.o, W.case, rows)
    added = named(items_of(W.case), "CLIP, BUMPER")
    check(r.status_code == 200 and suggestions(W.case)[clip.id].status == "accepted",
          "a hidden row put on a line is accepted")
    check(added["qty"] == 4.0 and added["workshop_est"] == 0.0, "...its new line takes the catalogue's qty (4), est 0")


def resync_keeps_refs() -> None:
    beam, gate = by_number(W.case, BEAM), by_number(W.case, GATE)
    rows = items_of(W.case)
    # Unpriced detection rows -- the kind a re-sync rebuilds -- with a ref each.
    for name, s in (("Rear bumper reinforcement", beam), ("Tail gate assy", gate)):
        it = named(rows, name)
        it["catalogue_ref"], it["oem_part_number"] = ref_for(s), s.part_number
    r = put(W.o, W.case, rows)
    saved = items_of(W.case)
    check(r.status_code == 200 and named(saved, "Tail gate assy")["origin"] == "detection",
          "two unpriced detection rows attached (origin still detection)", named(saved, "Tail gate assy")["origin"])
    # A damage edit on tab 1: Annex A re-syncs (prefill.sync_annex_a).
    data = report_data(W.case)
    idx = [d["part"] for d in data["damage_summary"]].index("Rear Bumper Beam")
    r = W.o.patch(f"/reports/{W.case}/damage-items/{idx}", json={"remarks": "Replace", "human_verified": True})
    check(r.status_code == 200, "a damage edit on tab 1 (re-sync)", brief(r))
    after = items_of(W.case)
    beam_row = named(after, "Rear bumper reinforcement")
    check(beam_row["catalogue_ref"] and beam_row["catalogue_ref"]["suggestion_id"] == beam.id
          and beam_row["oem_part_number"] == BEAM, "the attached detection row is kept with its ref")
    check(beam_row["action"] == "Replace", "...and still follows tab 1 (its remarks)", beam_row["action"])
    check(sum(1 for it in after if it["source_part"] == "Rear Bumper Beam") == 1,
          "...and no second, unpriced row was added for its part")
    check(named(after, "CLIP, BUMPER")["catalogue_ref"]["suggestion_id"] == by_number(W.case, CLIP).id,
          "the hand-added catalogue line is kept with its ref")
    # Boot Lid no longer damaged: its attached row is stale, kept, still accepted.
    data = report_data(W.case)
    idx = [d["part"] for d in data["damage_summary"]].index("Boot Lid")
    r = W.o.delete(f"/reports/{W.case}/damage-items/{idx}")
    after = items_of(W.case)
    gate_row = named(after, "Tail gate assy")
    check(r.status_code == 200 and gate_row["stale"] is True and gate_row["catalogue_ref"]["suggestion_id"] == gate.id,
          "its part removed on tab 1: the row is flagged stale, kept with its ref", brief(r))
    check(suggestions(W.case)[gate.id].status == "accepted", "...and its catalogue row stays accepted")
    # sync_annex_a itself, without the price book and with it.
    for price_book in (False, True):
        db = SessionLocal()
        try:
            out = sync_annex_a(db, report_data(W.case), price_book=price_book)
        finally:
            db.close()
        refs = sorted(it["catalogue_ref"]["suggestion_id"] for it in out["annex_a"]["items"] if it.get("catalogue_ref"))
        check(refs == sorted([beam.id, gate.id, by_number(W.case, CLIP).id]),
              f"sync_annex_a(price_book={price_book}) keeps every ref", str(refs))
    # The prefill (an admin's own case: the price book) keeps them as well.
    mine = new_case(W.admin)
    look_up(W.a, mine)
    rows = items_of(mine)
    for name, number in (("Rear bumper fascia", FACE), ("Tail lamp LH", TAIL)):
        it = named(rows, name)
        it["catalogue_ref"], it["oem_part_number"] = ref_for(by_number(mine, number)), number
    check(put(W.a, mine, rows).status_code == 200, "an admin's case with a priced and an unpriced line attached")
    r = W.a.post(f"/reports/{mine}/annex-a/prefill")
    refs = sorted(it["catalogue_ref"]["part_number"] for it in items_of(mine) if it.get("catalogue_ref"))
    check(r.status_code == 200 and refs == sorted([FACE, TAIL]), "Fill prices from past cases keeps the refs", f"{brief(r)} {refs}")
    # A re-run: rows on lines stay (and aren't added twice); the rest are replaced.
    look_up(W.o, W.case)
    rows = suggestions(W.case)
    check(sum(1 for s in rows.values() if s.part_number == BEAM) == 1 and rows[beam.id].status == "accepted",
          "a re-run keeps the accepted rows, once")
    r = put(W.o, W.case, items_of(W.case))
    check(r.status_code == 200, "...and the table still saves with their refs", brief(r))


def est_never_changes() -> None:
    rows = items_of(W.case)
    gate = by_number(W.case, GATE)
    r = put(W.o, W.case, rows)
    check(all(round(float(a["workshop_est"]), 2) == round(float(b["workshop_est"]), 2) for a, b in zip(rows, items_of(W.case))),
          "a save with refs changes no estimate")
    # Even a ref whose site price the client claims is the estimate: the
    # line's est is what the client typed, never the ref's.
    rows = items_of(W.case)
    it = named(rows, "Tail gate assy")
    it["catalogue_ref"] = ref_for(gate, ref_amount=99999.0)
    r = put(W.o, W.case, rows)
    it = named(items_of(W.case), "Tail gate assy")
    check(r.status_code == 200 and it["workshop_est"] == 0.0 and it["catalogue_ref"]["ref_amount"] == PRICES[GATE],
          "the ref's price never reaches W/shop Est. (and is the stored one)")
    data = report_data(W.case)
    total = sum(float(i["workshop_est"]) for i in data["annex_a"]["items"])
    check(round(data["annex_a"]["total_workshop_est"], 2) == round(total, 2) and data["annex_c"]["workshop_total"] >= total,
          "the totals are the estimates' sums")


def exports_never_show_it() -> None:
    # A case with refs on priced and unpriced lines, and a new catalogue line.
    data = report_data(W.case)
    refs = [it for it in data["annex_a"]["items"] if it.get("catalogue_ref")]
    numbers = sorted({it["catalogue_ref"]["part_number"] for it in refs} | {it["oem_part_number"] for it in refs if it.get("oem_part_number")})
    prices = sorted({f"{it['catalogue_ref']['ref_amount']:.2f}" for it in refs} | {f"{it['catalogue_ref']['ref_amount']:,.2f}" for it in refs})
    check(len(refs) >= 3 and numbers, f"the case carries {len(refs)} catalogue refs", str(numbers))
    names = {it["catalogue_ref"]["diagram_name"] for it in refs}
    words = ["catalogue", "parts-honda", "ex VAT", "excl. VAT", "EUR"]

    captured: list[str] = []

    def fake_pdf(html, output_path, slot_wait=None):
        captured.append(html)
        Path(output_path).write_bytes(b"%PDF-1.4 test\n")

    saved = renderer.html_to_pdf
    renderer.html_to_pdf = fake_pdf
    try:
        r = W.o.get(f"/reports/{W.case}/download")
    finally:
        renderer.html_to_pdf = saved
    check(r.status_code == 200 and captured, "the PDF is rendered (HTML captured)", brief(r))
    html = captured[-1] if captured else ""
    check(all(n not in html for n in numbers), "the PDF's HTML has no catalogue part number",
          str([n for n in numbers if n in html]))
    check(all(p not in html for p in prices), "...no reference price", str([p for p in prices if p in html]))
    check(all(n not in html for n in names) and not any(w in html for w in words[1:]),
          "...no diagram name, site or VAT note", str([w for w in words if w in html]))

    r = W.o.get(f"/reports/{W.case}/download.xlsx")
    check(r.status_code == 200, "the Excel workbook downloads", brief(r))
    wb = load_workbook(io.BytesIO(r.content))
    cells = " | ".join(str(c.value) for ws in wb.worksheets for rr in ws.iter_rows() for c in rr if c.value is not None)
    check("Rear bumper fascia" in cells, "the workbook has the Annex A lines")
    check(all(n not in cells for n in numbers), "the workbook has no catalogue part number",
          str([n for n in numbers if n in cells]))
    check(all(p not in cells for p in prices) and not any(p in cells for p in [str(v) for v in PRICES.values()]),
          "...no reference price", str([p for p in prices if p in cells]))
    check(not any(w in cells for w in words[1:]) and all(n not in cells for n in names), "...no site, diagram or VAT note")
    # The renderers straight from the data, as the bot uses them.
    out = _tmp / "direct.xlsx"
    render_xlsx(data, str(out), report_id=W.case)
    cells = " | ".join(str(c.value) for ws in load_workbook(out).worksheets for rr in ws.iter_rows() for c in rr if c.value is not None)
    check(all(n not in cells for n in numbers), "render_xlsx on the data: no part number either")


def no_parts_reason() -> None:
    check("no_parts" in catalogue.BLOCKED_REASONS, "no_parts is a blocked reason")
    bare = new_case(W.owner, case_data(damage_summary=[], damaged_parts=[]))
    view = W.o.get(f"/reports/{bare}/catalogue").json()
    check(view.get("can_request") is False and view.get("blocked_reason") == "no_parts",
          "no damaged parts: GET says can_request false, blocked_reason no_parts", json.dumps(view)[:200])
    r = W.o.post(f"/reports/{bare}/catalogue/lookup", json={})
    check(r.status_code == 400 and code_of(r) == "no_parts", "...and a click anyway still answers 400 no_parts", brief(r))
    unnamed = new_case(W.owner, case_data(damage_summary=[damage("Other / Not Listed")], damaged_parts=["Other / Not Listed"]))
    check(W.o.get(f"/reports/{unnamed}/catalogue").json().get("blocked_reason") == "no_parts",
          "only an unnamed Other / Not Listed: no_parts")
    nomake = new_case(W.owner, case_data(damage_summary=[]))
    data = report_data(nomake)
    data["vehicle_info"]["make"] = ""
    db = SessionLocal()
    try:
        db.get(Report, nomake).data = data
        db.commit()
    finally:
        db.close()
    check(W.o.get(f"/reports/{nomake}/catalogue").json().get("blocked_reason") == "no_make_model",
          "no make and no parts: no_make_model comes first")
    check(W.o.get(f"/reports/{W.case}/catalogue").json().get("blocked_reason") is None, "a case with parts: no reason")
    check(W.b.get(f"/reports/{bare}/catalogue").status_code in (403, 404), "someone else's case: not shown")


def worker_and_server_agree() -> None:
    fixture = json.loads((REPO / "tools" / "parts_mcp" / "tests" / "fixtures" / "catalogue_links.json").read_text(encoding="utf-8"))
    wrong = []
    for case in fixture["links"]:
        url = case["url"] + "a" * (case["pad_to"] - len(case["url"])) if case.get("pad_to") else case["url"]
        for model, body in ((catalogue.CatalogueRow, {"url": url}), (catalogue.CatalogueDiagram, {"diagram_url": url, "image_url": url}),
                            (CatalogueRef, {"suggestion_id": 1, "diagram_url": url})):
            try:
                model.model_validate(body)
                ok = True
            except Exception:
                ok = False
            if ok != case["ok"]:
                wrong.append((model.__name__, url[:60], ok))
    check(not wrong, f"the server takes exactly the links the worker keeps ({len(fixture['links'])} shared cases)", str(wrong))
    check(catalogue.ALLOWED_HOSTS == set(fixture["hosts"]) == {"bmw-spare-parts.com", "mini-spare-parts.com", "parts-honda.uk"},
          "the server's sites are the worker's: the three Microfiches sites", str(sorted(catalogue.ALLOWED_HOSTS)))
    caps = fixture["caps"]
    check((catalogue.MAX_PARTS, catalogue.MAX_DIAGRAMS, catalogue.MAX_ROWS, catalogue.MAX_CANDIDATES,
           catalogue.MAX_FAILURES, catalogue.MAX_PART_FAILURES, catalogue.MAX_TEXT)
          == (caps["parts"], caps["diagrams"], caps["rows"], caps["candidates"], caps["failures"], caps["part_failures"], caps["text"]),
          "the server's caps are the worker's")
    year = catalogue.CatalogueVehicle.model_fields["year_from"].metadata
    amount = catalogue.CatalogueRow.model_fields["ref_amount"].metadata
    check(any(getattr(m, "ge", None) == caps["year_from"] for m in year) and any(getattr(m, "le", None) == caps["year_to"] for m in year)
          and any(getattr(m, "le", None) == caps["ref_amount_max"] for m in amount), "...and so are its year and price bounds")
    # A key the worker had to null, and a car key with %-encoding.
    result = the_result()
    result["parts"][0]["diagrams"][0]["diagram_key"] = None
    case = new_case(W.owner)
    look_up(W.o, case, result, vehicle_key="microfiches:HR-V%20RU%2F1~x")
    check(len(suggestions(case)) == 5, "a null diagram key and a %-encoded car key are taken (no 422)")


def live_names_hint() -> None:
    """Names as the live parts-honda.uk HR-V pages print them (12 Nov 2026)."""
    lines = [{"part_name": "Rear bumper", "source_part": "Rear Bumper"},
             {"part_name": "Tail gate assy", "source_part": "Boot Lid"},
             {"part_name": "Tail lamp LH", "source_part": "Left Tail Lamp"}]
    m = catalogue.LineMatcher(lines)

    def hint(name, part, query, side=None):
        s = SimpleNamespace(part_number=None, superseded_from=None, name=name, side=None, part_side=side,
                            part_query=query, source_part=part)
        return m.hint(s)["line_name"]

    check(hint("BUMPER FACE, RR(DO", "Rear Bumper", "rear bumper") == "Rear bumper",
          "a name the site cut mid-bracket ('BUMPER FACE, RR(DO') still finds the 'Rear bumper' line")
    check(hint("TAILGATE (DOOR)", "Boot Lid", "boot lid") == "Tail gate assy", "'TAILGATE (DOOR)' finds 'Tail gate assy'")
    check(hint("FACE, R. RR. BUMPER CORNE", "Rear Bumper", "rear bumper") is None,
          "a bumper corner face is not the bumper")
    check(hint("BULB, WEDGE(12V 21W)(AMBER) (12V 21W) (AMBRE) (KOITO)", "Left Tail Lamp", "tail lamp", "left") is None,
          "a bulb is not the lamp")
    check(catalogue._wording("BULB, WEDGE(12V 21W)(AMBER) (KOITO)") == "wedge bulb", "brackets are left out of the wording")


def ai_schema_untouched() -> None:
    schema = json.dumps(_response_schema())
    check("catalogue_ref" not in schema and "CatalogueRef" not in schema, "the AI's response schema has no catalogue_ref")
    check(not GEMINI and not OUTSIDE, "no Gemini call, no outside connection", f"{GEMINI} {OUTSIDE}")


def put_reports_path() -> None:
    gate = by_number(W.case, GATE)
    foreign = next(iter(suggestions(W.other_case).values()))
    data = report_data(W.case)
    rows = [dict(it) for it in data["annex_a"]["items"]]
    named(rows, "Tail gate assy")["catalogue_ref"] = ref_for(foreign)
    draft = {"description": data["description"], "annex_a": {"items": rows}}
    r = W.o.put(f"/reports/{W.case}", json={"draft": draft})
    check(r.status_code == 422 and code_of(r) == "catalogue_ref", "PUT /reports/{id} with another case's ref: 422", brief(r))
    rows = [dict(it) for it in data["annex_a"]["items"] if it["part_name"] != "Tail gate assy"]
    renderer_saved = renderer.html_to_pdf
    renderer.html_to_pdf = lambda html, out, slot_wait=None: Path(out).write_bytes(b"%PDF-1.4 test\n")
    try:
        r = W.o.put(f"/reports/{W.case}", json={"draft": {"description": data["description"], "annex_a": {"items": rows}}})
    finally:
        renderer.html_to_pdf = renderer_saved
    check(r.status_code == 200 and suggestions(W.case)[gate.id].status == "suggested",
          "PUT /reports/{id} dropping a line: its row is suggested again", brief(r))


# ------------------------------------------------------------ Phase 3
#
# CONTRACT.md "Phase 3": tidy_name, variant_key, same_as_part,
# make_not_covered, the look-up queued by itself after detection.

# Names as the parts sites print them (parts-honda.uk; the BMW / MINI ones
# as the worker words them in English, tools/parts_mcp microfiches_glossary),
# and the line each would start as.
LIVE_NAMES = {
    "FACE, L. RR. BUMPER CORNE": "Rear bumper corner face LH",
    "BUMPER FACE, RR(DO": "Rear bumper face",
    "CLIP, RR. COMBINATION LIGHT": "Rear combination light clip",
    "SPACER, R. RR. BUMPER SIDE": "Rear bumper side spacer RH",
    "EXTENSION COMP., R. RR. BUMPER": "Rear bumper extension complete RH",
    "LAMP UNIT, L.": "Lamp unit LH",
    "FACE, RR. BUMPER": "Rear bumper face",
    "LIGHT ASSY., L. TAIL": "Tail light assy LH",
    "TAILGATE (DOOR)": "Tailgate (DOOR)",
    "BEAM, RR. BUMPER": "Rear bumper beam",
    "BOLT, FLANGE (6X16)": "Flange bolt (6X16)",
    "SCREW, TAPPING (5MM)": "Tapping screw (5MM)",
    "CLIP, 8X25": "8X25 clip",
    "SUB-ASSY., R. FR. FENDER": "Front fender sub-assy RH",
    "BRACKET, L./R. RR. BUMPER": "Rear bumper bracket LH/RH",
    "LIGHT ASSY., L. TAIL (KOITO)": "Tail light assy LH (KOITO)",
    "LAMP ASSY, RR COMBINATION, LH": "Rear combination lamp assy LH",
    "Trim panel bumper centre rear": "Trim panel bumper centre rear",
    "Trim cover, bumper, rear": "Rear bumper trim cover",
    "Sensor PDC left": "Sensor PDC LH",
    "Fillister-head screw M6X20": "Fillister-head screw M6X20",
}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9/\-]+", (text or "").lower())}


def tidy_names() -> None:
    wrong = [(n, catalogue.tidy_name(n), want) for n, want in LIVE_NAMES.items() if catalogue.tidy_name(n) != want]
    check(not wrong, f"tidy_name on {len(LIVE_NAMES)} catalogue names (the live site's cut ones among them)", repr(wrong))
    check(catalogue.tidy_name(None) is None and catalogue.tidy_name("  ") is None and catalogue.tidy_name("(DO") is None,
          "no name, or nothing but a cut-off bracket: None")
    # Never a word of its own: every word out is in the name, or written
    # out from the tables.
    table = set()
    for words in (catalogue.TIDY_WORDS, catalogue.TIDY_SIDES, catalogue.TIDY_CUT_WORDS):
        for value in words.values():
            table |= _words(value)
    invented = {n: sorted(_words(catalogue.tidy_name(n)) - _words(n) - table) for n in LIVE_NAMES}
    invented = {n: w for n, w in invented.items() if w}
    check(not invented, "no word that isn't in the name or the expansion tables", repr(invented))
    check(all(catalogue.tidy_name(catalogue.tidy_name(n)) == catalogue.tidy_name(n) for n in LIVE_NAMES
              if "," not in catalogue.tidy_name(n)),
          "tidying a tidy name changes nothing")

    case = new_case(W.owner)
    look_up(W.o, case)
    view = W.o.get(f"/reports/{case}/catalogue").json()
    rows = [r for p in view["parts"] for d in p["diagrams"] for r in d["rows"]]
    stored = {s.id: s.name for s in suggestions(case).values()}
    check(rows and all(r["name"] == stored[r["id"]] for r in rows), "GET: every row's name is the catalogue's, untouched")
    check(all(r["tidy_name"] == catalogue.tidy_name(r["name"]) for r in rows),
          "...with tidy_name beside it", repr([(r["name"], r["tidy_name"]) for r in rows]))
    face = next(r for r in rows if r["part_number"] == FACE)
    check(face["tidy_name"] == "Rear bumper face", "FACE, RR. BUMPER -> 'Rear bumper face'", repr(face["tidy_name"]))

    # A new line named by tidy_name: its ref keeps the catalogue's name.
    s = by_number(case, FACE)
    new = new_catalogue_line(s)
    new["part_name"] = catalogue.tidy_name(s.name)
    r = put(W.o, case, items_of(case) + [new])
    saved = named(items_of(case), "Rear bumper face") if r.status_code == 200 else {}
    check(r.status_code == 200 and saved.get("catalogue_ref", {}).get("name") == "FACE, RR. BUMPER",
          "a new line named 'Rear bumper face' saves; its catalogue_ref.name stays 'FACE, RR. BUMPER'", brief(r))


def _colours(letters="BCDEFGHJ", **over) -> list[dict]:
    return [{"part_number": f"04718T7JH00Z{c}", "callout": "5", "name": "FACE, L. RR. BUMPER CORNE", "qty": "1",
             "side": None, "ref_amount": 52.10, "ref_currency": "EUR", **over} for c in letters]


def colour_versions() -> None:
    keys = catalogue.variant_keys(_colours())
    check(keys[0] and len(set(keys)) == 1, "8 colour versions (…ZB to …ZJ, same callout, name, price): one variant_key",
          repr(keys))
    vk = catalogue.variant_keys
    rows = _colours()
    rows[3] = {**rows[3], "ref_amount": 55.0}
    out = vk(rows)
    check(out[3] is None and len({k for i, k in enumerate(out) if i != 3}) == 1,
          "one at another price: left out of the group", repr(out))
    for label, change in (("callout", {"callout": "6"}), ("name", {"name": "FACE, R. RR. BUMPER CORNE"}),
                          ("currency", {"ref_currency": "GBP"}), ("side", {"side": "right"}), ("qty", {"qty": "2"})):
        two = _colours("BC")
        two[1] = {**two[1], **change}
        check(vk(two) == [None, None], f"a different {label}: not folded", repr(vk(two)))
    check(vk(_colours("B")) == [None], "a single colour version: no key")
    plain = [{**_colours("B")[0], "part_number": "04718T7JH00"}, _colours("B")[0]]
    out = vk(plain)
    check(out[0] and out[0] == out[1], "the number without a colour code and one with it, all else the same: folded", repr(out))
    middle = [{**_colours("B")[0], "part_number": "04718ZBJH00"}, {**_colours("B")[0], "part_number": "04718ZCJH00"}]
    check(vk(middle) == [None, None], "numbers differing in the middle (not a trailing colour code): not folded")
    other_base = [_colours("B")[0], {**_colours("C")[0], "part_number": "04718T7JH01ZC"}]
    check(vk(other_base) == [None, None], "another base number: not folded")
    bmw = [{"part_number": "51127424443", "callout": "2", "name": "Trim cover", "ref_amount": 10.0},
           {"part_number": "51127424444", "callout": "2", "name": "Trim cover", "ref_amount": 10.0}]
    check(vk(bmw) == [None, None], "BMW / MINI numbers (digits only): never folded")
    dashed = [{**_colours("B")[0], "part_number": "04718-T7J-H00ZB"}, {**_colours("C")[0], "part_number": "04718 T7J H00 ZC"}]
    out = vk(dashed)
    check(out[0] and out[0] == out[1], "written with dashes or spaces: the same numbers", repr(out))
    two_prices = _colours("BC") + _colours("DE", ref_amount=60.0)
    out = vk(two_prices)
    check(out[0] == out[1] and out[2] == out[3] and out[0] != out[2] and None not in out,
          "two groups of the same part at two prices: two keys", repr(out))

    case = new_case(W.owner, case_data(damage_summary=[damage("Rear Bumper")], damaged_parts=["Rear Bumper"]))
    colour_rows = [row(f"04718T7JH00Z{c}", "FACE, L. RR. BUMPER CORNE", 52.10, callout="5") for c in "BCDEFGHJ"]
    result = the_result()
    result["parts"][0]["diagrams"][0]["rows"] += colour_rows
    look_up(W.o, case, result)
    view = W.o.get(f"/reports/{case}/catalogue").json()
    rows = view["parts"][0]["diagrams"][0]["rows"]
    groups = {}
    for r in rows:
        groups.setdefault(r["variant_key"], []).append(r["part_number"])
    keyed = {k: v for k, v in groups.items() if k}
    check(len(keyed) == 1 and len(next(iter(keyed.values()))) == 8, "GET: the 8 colour rows share one variant_key", repr(groups))
    check(sorted(groups.get(None) or []) == sorted([FACE, CLIP]), "the face and the clip: none", repr(groups.get(None)))
    key = next(iter(keyed))
    one = next(r for r in rows if r["part_number"] == "04718T7JH00ZD")
    r = W.o.post(f"/reports/{case}/catalogue/suggestions/{one['id']}", json={"action": "reject"})
    check(r.status_code == 200 and r.json()["suggestion"]["variant_key"] == key and r.json()["suggestion"]["status"] == "rejected",
          "hiding one: the answer keeps its variant_key (the page keeps it in its group)", brief(r))
    rows = W.o.get(f"/reports/{case}/catalogue").json()["parts"][0]["diagrams"][0]["rows"]
    check(sum(1 for r in rows if r["variant_key"] == key) == 8, "GET after: still 8 in the group (one of them hidden)")


TAIL_R, CLIP2, SPACER = "33500T7WA01", "90650TX6A01", "71598T7WA01"


def same_diagram_once() -> None:
    parts = ["Rear Bumper", "Rear Bumper Beam", "Left Tail Lamp", "Right Tail Lamp", "Boot Lid"]
    data = case_data(damage_summary=[damage(p) for p in parts], damaged_parts=parts)
    case = new_case(W.owner, data)
    rb = lambda: [row(FACE, "FACE, RR. BUMPER", PRICES[FACE], callout="1"),  # noqa: E731
                  row(CLIP, "CLIP, BUMPER", PRICES[CLIP], callout="7"),
                  row(SPACER, "SPACER, R. RR. BUMPER SIDE", 3.0, callout="9", side="right")]
    result = {"status": "done", "error_code": None, "error_text": None, "vehicle": VEHICLE, "candidates": [VEHICLE],
              "failures": [], "parts": [
                  {"source_part": "Rear Bumper", "query": "rear bumper", "side": None, "failures": [],
                   "diagrams": [diagram("microfiches:30584:rb", "REAR BUMPER", rb())]},
                  {"source_part": "Rear Bumper Beam", "query": "rear bumper beam", "side": None, "failures": [], "diagrams": [
                      diagram("microfiches:30584:rbb", "REAR BUMPER BEAM", [row(BEAM, "BEAM, RR. BUMPER", PRICES[BEAM])]),
                      diagram("microfiches:30584:rb", "REAR BUMPER", rb()[:2], score=0.6),
                      diagram(None, "UNNAMED", [row("99999T7WA00", "PLATE, RR.", 1.0)], score=0.5)]},
                  {"source_part": "Left Tail Lamp", "query": "tail lamp", "side": "left", "failures": [], "diagrams": [
                      diagram("microfiches:30584:tl", "TAILLIGHT", [row(TAIL, "LIGHT ASSY., L. TAIL", PRICES[TAIL], side="left"),
                                                                     row(CLIP2, "CLIP, RR. COMBINATION LIGHT", 4.96, callout="26")])]},
                  {"source_part": "Right Tail Lamp", "query": "tail lamp", "side": "right", "failures": [], "diagrams": [
                      diagram("microfiches:30584:tl", "TAILLIGHT", [row(TAIL_R, "LIGHT ASSY., R. TAIL", 187.23, side="right"),
                                                                     row(CLIP2, "CLIP, RR. COMBINATION LIGHT", 4.96, callout="26")])]},
                  {"source_part": "Boot Lid", "query": "boot lid", "side": None, "failures": [], "diagrams": [
                      diagram(None, "UNNAMED", [row(GATE, "TAILGATE (DOOR)", PRICES[GATE])])]},
              ]}
    look_up(W.o, case, result)
    view = W.o.get(f"/reports/{case}/catalogue").json()
    by_part = {p["source_part"]: {d["diagram_key"] or "(none)": d for d in p["diagrams"]} for p in view["parts"]}
    numbers = lambda d: [r["part_number"] for r in d["rows"]]  # noqa: E731
    check(all("same_as_part" in d for p in view["parts"] for d in p["diagrams"]), "every diagram says same_as_part")
    first = by_part["Rear Bumper"]["microfiches:30584:rb"]
    check(first["same_as_part"] is None and numbers(first) == [FACE, CLIP, SPACER],
          "the first part (damage-list order) keeps the diagram and its rows", repr((first["same_as_part"], numbers(first))))
    again = by_part["Rear Bumper Beam"]["microfiches:30584:rb"]
    check(again["same_as_part"] == "Rear Bumper" and again["rows"] == [],
          "the Rear Bumper Beam's REAR BUMPER: same_as_part 'Rear Bumper', rows []", repr((again["same_as_part"], numbers(again))))
    check(again["diagram_name"] == "REAR BUMPER" and again["diagram_url"], "...still named and linked")
    own = by_part["Rear Bumper Beam"]["microfiches:30584:rbb"]
    check(own["same_as_part"] is None and numbers(own) == [BEAM], "its own diagram: as it was")
    left, right = by_part["Left Tail Lamp"]["microfiches:30584:tl"], by_part["Right Tail Lamp"]["microfiches:30584:tl"]
    check(left["same_as_part"] is None and numbers(left) == [TAIL, CLIP2], "Left Tail Lamp: TAILLIGHT with the left lamp and the clip")
    check(right["same_as_part"] == "Left Tail Lamp" and numbers(right) == [TAIL_R],
          "Right Tail Lamp: same_as_part 'Left Tail Lamp', keeping the right lamp (the left part doesn't show it), not the clip",
          repr((right["same_as_part"], numbers(right))))
    check(by_part["Rear Bumper Beam"]["(none)"]["same_as_part"] is None and by_part["Boot Lid"]["(none)"]["same_as_part"] is None
          and numbers(by_part["Boot Lid"]["(none)"]) == [GATE], "diagrams with no key are never taken for one another")

    # A row of the later part a person put on a line stays shown there.
    beam_face = next(s for s in suggestions(case).values() if s.source_part == "Rear Bumper Beam" and s.part_number == FACE)
    rows = items_of(case)
    named(rows, "Rear bumper reinforcement")["catalogue_ref"] = ref_for(beam_face)
    r = put(W.o, case, rows)
    again = {p["source_part"]: {d["diagram_key"]: d for d in p["diagrams"]} for p in
             W.o.get(f"/reports/{case}/catalogue").json()["parts"]}["Rear Bumper Beam"]["microfiches:30584:rb"]
    check(r.status_code == 200 and [x["id"] for x in again["rows"]] == [beam_face.id] and again["rows"][0]["status"] == "accepted",
          "a row of the later part on an Annex A line stays listed under it (accepted)", brief(r))


def makes_covered() -> None:
    check("make_not_covered" in catalogue.BLOCKED_REASONS, "make_not_covered is a blocked reason")
    check(catalogue.covered_makes() == ["Honda", "BMW", "MINI"], "CATALOGUE_MAKES default: Honda, BMW, MINI",
          repr(catalogue.covered_makes()))
    yes = [m for m in ("Honda", "HONDA", "BMW", "bmw", " Bmw ", "MINI", "Mini") if catalogue.make_covered(m)]
    no = [m for m in ("Toyota", "Hond", "Mini Cooper", "", None) if not catalogue.make_covered(m)]
    check(len(yes) == 7 and len(no) == 5, "any case and spacing ('Mini' is MINI); other makes not", repr((yes, no)))

    vi = case_data()["vehicle_info"]
    toyota = new_case(W.owner, case_data(vehicle_info={**vi, "make": "Toyota", "model": "Corolla Altis"}))
    view = W.o.get(f"/reports/{toyota}/catalogue").json()
    check(view.get("blocked_reason") == "make_not_covered" and view.get("can_request") is False,
          "a Toyota: blocked make_not_covered, can_request false", repr(view.get("blocked_reason")))
    check(view.get("covered_makes") == ["Honda", "BMW", "MINI"], "GET lists the covered makes", repr(view.get("covered_makes")))
    r = W.o.post(f"/reports/{toyota}/catalogue/lookup", json={})
    check(r.status_code == 400 and code_of(r) == "make_not_covered"
          and r.json().get("detail") == "No parts catalogue for Toyota yet (Honda, BMW and MINI are covered).",
          "...and POST lookup -> 400 make_not_covered, in the page's words", brief(r))
    nomodel = new_case(W.owner, case_data(vehicle_info={**vi, "make": "Toyota", "model": ""}))
    check(W.o.get(f"/reports/{nomodel}/catalogue").json().get("blocked_reason") == "make_not_covered",
          "a Toyota with no model: make_not_covered first (adding the model wouldn't help)")
    mini = new_case(W.owner, case_data(vehicle_info={**vi, "make": "Mini", "model": "Cooper S"}))
    check(W.o.get(f"/reports/{mini}/catalogue").json().get("blocked_reason") is None, "a 'Mini' Cooper S: covered")
    bmw = new_case(W.owner, case_data(vehicle_info={**vi, "make": "bmw", "model": "320i"}))
    saved = settings.catalogue_makes
    try:
        settings.catalogue_makes = "Honda"
        view = W.o.get(f"/reports/{bmw}/catalogue").json()
        r = W.o.post(f"/reports/{bmw}/catalogue/lookup", json={})
        check(view.get("blocked_reason") == "make_not_covered" and view.get("covered_makes") == ["Honda"]
              and r.json().get("detail") == "No parts catalogue for bmw yet (Honda is covered).",
              "CATALOGUE_MAKES=Honda: a BMW is not covered", brief(r))
    finally:
        settings.catalogue_makes = saved
    check(W.o.get(f"/reports/{bmw}/catalogue").json().get("blocked_reason") is None, "back to the default: the BMW is covered")


def _jobs(report_id: str) -> list[CatalogueJob]:
    db = SessionLocal()
    try:
        out = db.query(CatalogueJob).filter(CatalogueJob.report_id == report_id).order_by(CatalogueJob.id).all()
        for j in out:
            db.expunge(j)
        return out
    finally:
        db.close()


class _Logged(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def _draft(**over) -> str:
    return new_case(W.owner, case_data(**over), status="intake")


def auto_lookup() -> None:
    turn_on()
    vi = case_data()["vehicle_info"]
    rid = _draft()
    job_id = catalogue.maybe_auto_lookup(rid)
    jobs = _jobs(rid)
    check(job_id and [j.id for j in jobs] == [job_id], "a covered draft with a model and damaged parts: one job queued", repr(jobs))
    j = jobs[0] if jobs else SimpleNamespace(status=None, requested_by=None, requested_by_id=1, payload={}, vehicle_key=1)
    check(j.status == "pending" and j.requested_by == "auto (after detection)" and j.requested_by_id is None,
          "pending, requested_by 'auto (after detection)', no person's id", repr((j.status, j.requested_by, j.requested_by_id)))
    check([p["source_part"] for p in (j.payload or {}).get("parts", [])] == ["Rear Bumper", "Boot Lid", "Left Tail Lamp", "Rear Bumper Beam"]
          and j.vehicle_key is None, "the job a person's click would make: the case's parts, no car picked")
    check(PLATE not in json.dumps(j.payload) and VIN not in json.dumps(j.payload), "...with no plate or VIN in it")
    check(("catalogue_lookup_requested", "4 part(s) · automatically after detection") in history(rid),
          "the case history: catalogue_lookup_requested, 'automatically after detection'", repr(history(rid)))
    view = W.o.get(f"/reports/{rid}/catalogue").json()
    check((view.get("job") or {}).get("requested_by") == "auto (after detection)", "GET: job.requested_by is the auto value")
    check(catalogue.maybe_auto_lookup(rid) is None and len(_jobs(rid)) == 1, "called again (a re-reading): nothing more")

    def none_for(label, report_id):
        out = catalogue.maybe_auto_lookup(report_id)
        check(out is None and not _jobs(report_id), f"{label}: none queued", repr(out))

    turn_on(False)
    none_for("feature off", _draft())
    turn_on()
    none_for("a Toyota (make not covered)", _draft(vehicle_info={**vi, "make": "Toyota", "model": "Corolla"}))
    none_for("no model", _draft(vehicle_info={**vi, "model": " "}))
    none_for("no make", _draft(vehicle_info={**vi, "make": ""}))
    none_for("no damaged parts", _draft(damage_summary=[], damaged_parts=[]))
    none_for("only an unnamed Other / Not Listed", _draft(damage_summary=[damage("Other / Not Listed")], damaged_parts=[]))
    none_for("signed off", new_case(W.owner, case_data(sign_off={"status": "Signed Off"})))
    check(catalogue.maybe_auto_lookup("no-such-case") is None, "a case that doesn't exist: None")

    filed = new_case(W.owner)
    r = W.o.post(f"/reports/{filed}/catalogue/lookup", json={})
    W.o.post(f"/reports/{filed}/catalogue/cancel")
    check(r.status_code == 202 and catalogue.maybe_auto_lookup(filed) is None and len(_jobs(filed)) == 1,
          "a case that had a look-up (here one a person cancelled): never another by itself")

    logged = _Logged()
    logging.getLogger("app.parts.catalogue").addHandler(logged)
    real = catalogue.job_payload
    try:
        catalogue.job_payload = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("made-up failure"))
        broken = _draft()
        out = catalogue.maybe_auto_lookup(broken)
    finally:
        catalogue.job_payload = real
        logging.getLogger("app.parts.catalogue").removeHandler(logged)
    check(out is None and not _jobs(broken), "a failure inside: None, nothing queued, nothing raised")
    check(any(r.exc_info and "made-up failure" in str(r.exc_info[1]) for r in logged.records), "...and it is logged")

    # The worker gets it as any other job.
    pending = [j for j in _jobs(rid) if j.status == "pending"]
    r = W.w.post("/catalogue-worker/claim", json={"worker": "test-pc"})
    check(pending and (r.json().get("job") or {}).get("job_id") == pending[0].id, "the worker claims it like any other", brief(r))


def _intake(notes: str = "Rear-ended at a junction; made-up notes.") -> str:
    rid = new_case(W.owner, {"description": "", "category": [], "damage_summary": [], "vehicle_info": {}}, status="intake")
    db = SessionLocal()
    try:
        db.add(IntakeSession(report_id=rid, stage="reading", reporter_notes=notes))
        db.commit()
    finally:
        db.close()
    return rid


def _fake_reading(*args, **kwargs):
    return SecurityIncidentDraft.model_validate({
        "description": "Made-up reading.", "category": ["Vehicle Collision or Damage"],
        "vehicle_info": {"make": "Honda", "model": "Vezel", "plate_number": PLATE},
        "damage_summary": [{"part": "Rear Bumper", "damage_type": "Dent", "severity": "Moderate"},
                           {"part": "Left Tail Lamp", "damage_type": "Crack", "severity": "Moderate"}],
    })


def detection_hook() -> None:
    import inspect

    import app.intake.pipeline as pipeline
    from app.ai import runs
    from app.ai.keys import AiActor

    turn_on()
    src = inspect.getsource(pipeline._read_into)
    hook = src.rfind("catalogue.maybe_auto_lookup(report_id)")
    check(src.count("catalogue.maybe_auto_lookup(") == 1 and hook > src.rfind("db.commit()") > 0 and hook > src.rfind("db.close()"),
          "pipeline._read_into calls maybe_auto_lookup once, after the reading's commit")
    actor = AiActor.cli()
    real = pipeline.draft_report
    pipeline.draft_report = _fake_reading
    try:
        rid = _intake()
        pipeline._read_into(rid, actor, runs.Run(rid, "read", actor, None))
        data = report_data(rid)
        jobs = _jobs(rid)
        check([it["part"] for it in data.get("damage_summary") or []] == ["Rear Bumper", "Left Tail Lamp"],
              "the reading is saved", repr(data.get("damage_summary")))
        check(len(jobs) == 1 and jobs[0].requested_by == "auto (after detection)"
              and [p["source_part"] for p in jobs[0].payload["parts"]] == ["Rear Bumper", "Left Tail Lamp"],
              "...and a look-up of the parts it found is queued", repr([(j.requested_by, j.payload) for j in jobs]))
        pipeline._read_into(rid, actor, runs.Run(rid, "redraft", actor, None))
        check(len(_jobs(rid)) == 1, "a re-reading queues no second one")

        turn_on(False)
        off = _intake()
        pipeline._read_into(off, actor, runs.Run(off, "read", actor, None))
        check(report_data(off).get("damage_summary") and not _jobs(off), "feature off: read and saved, nothing queued")
        turn_on()

        real_payload = catalogue.job_payload
        catalogue.job_payload = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("made-up failure"))
        try:
            broken = _intake()
            raised = None
            try:
                pipeline._read_into(broken, actor, runs.Run(broken, "read", actor, None))
            except Exception as exc:  # noqa: BLE001
                raised = exc
        finally:
            catalogue.job_payload = real_payload
        check(raised is None and [it["part"] for it in report_data(broken).get("damage_summary") or []] == ["Rear Bumper", "Left Tail Lamp"]
              and not _jobs(broken), "the look-up failing inside: the reading still saved, no error, nothing queued", repr(raised))
    finally:
        pipeline.draft_report = real


def _count(report_id: str) -> tuple[int, int]:
    db = SessionLocal()
    try:
        return (db.query(CatalogueJob).filter(CatalogueJob.report_id == report_id).count(),
                db.query(CatalogueSuggestion).filter(CatalogueSuggestion.report_id == report_id).count())
    finally:
        db.close()


def drafts_deleted() -> None:
    from app.auth.permissions import PUBLIC_CHANNEL
    from app.intake import cleanup

    turn_on()
    # Thrown away by its owner (DELETE /reports/{id}).
    rid = _intake()
    set_vehicle_and_parts(rid)
    catalogue.maybe_auto_lookup(rid)
    check(_count(rid)[0] == 1, "a draft with its look-up")
    r = W.o.delete(f"/reports/{rid}")
    check(r.status_code == 200 and _count(rid) == (0, 0), "thrown away by its owner: the look-up goes too", brief(r))

    # A taken public report thrown away (public.delete_submission).
    pub = new_case(W.owner, case_data(), status="intake")
    db = SessionLocal()
    try:
        db.get(Report, pub).channel = PUBLIC_CHANNEL
        db.add(IntakeSession(report_id=pub, stage="ready", reporter_notes=""))
        db.commit()
    finally:
        db.close()
    catalogue.maybe_auto_lookup(pub)
    check(_count(pub)[0] == 1, "a taken public report with its look-up")
    r = W.o.delete(f"/reports/{pub}")
    check(r.status_code == 200 and _count(pub) == (0, 0), "thrown away: the look-up goes with it", brief(r))

    # Not worked on for a week: the sweep deletes the draft, and its look-up.
    idle = _intake()
    set_vehicle_and_parts(idle)
    catalogue.maybe_auto_lookup(idle)
    check(_count(idle)[0] == 1, "an idle draft with its look-up")
    from datetime import datetime, timedelta, timezone

    cleanup.sweep(now=datetime.now(timezone.utc) + timedelta(days=30))
    db = SessionLocal()
    try:
        gone = db.get(Report, idle) is None
    finally:
        db.close()
    check(gone and _count(idle) == (0, 0), "swept after a week idle: the look-up goes too", repr(_count(idle)))


# ------------------------------------------------------ the review's fixes


def _lookups(client: TestClient, report_id: str):
    return client.post(f"/reports/{report_id}/catalogue/lookup", json={})


def _claim(job_id: int) -> dict:
    """The worker's claim, until it hands out job_id: any older job still
    waiting from earlier sections is ended (failed) on the way."""
    for _ in range(50):
        job = W.w.post("/catalogue-worker/claim", json={"worker": "test-pc"}).json()["job"] or {}
        if job.get("job_id") == job_id or not job:
            return job
        W.w.post(f"/catalogue-worker/jobs/{job['job_id']}/result", json={"status": "failed", "error_code": "internal"})
    return {}


def _drain() -> None:
    """No job left waiting, so the next claim is the next look-up's."""
    _claim(-1)


def lookup_quota() -> None:
    """POST .../catalogue/lookup is capped per person (limits.
    check_catalogue_lookups): 3 in 24 hours without staff access, 20 in an
    hour otherwise; cancelled look-ups count, refused ones and the one
    queued by itself after detection don't."""
    # Signed in afresh: the idle-draft sweep above ran 30 days ahead, which
    # ended these sessions too.
    W.a, W.o, W.b = (signed_in(u) for u in (W.admin, W.owner, W.other))
    limits.LOOKUPS_PER_DAY_UNVETTED, limits.LOOKUPS_PER_HOUR = REAL_LOOKUP_CAPS
    try:
        check(REAL_LOOKUP_CAPS == (3, 20), "the caps: 3 a day without staff access, 20 an hour otherwise",
              repr(REAL_LOOKUP_CAPS))
        fay = new_user("Fay")
        f = signed_in(fay)
        case, other = new_case(fay), new_case(fay)
        # Her draft's look-up after detection: nobody's, never counted.
        draft = new_case(fay, case_data(), status="intake")
        check(catalogue.maybe_auto_lookup(draft) is not None, "an automatic look-up on her draft (not counted)")
        for i in range(3):
            r = _lookups(f, case)
            check(r.status_code == 202, f"look-up {i + 1} of 3: 202", brief(r))
            if i == 0:
                r = _lookups(f, case)
                check(r.status_code == 409 and code_of(r) == "job_open",
                      "a second click meanwhile: 409 job_open (not counted)", brief(r))
            r = f.post(f"/reports/{case}/catalogue/cancel")
            check(r.status_code == 200, "...cancelled (still counted)", brief(r))
        before = len(_jobs(case))
        r = _lookups(f, case)
        check(r.status_code == 429 and code_of(r) == "lookup_limit", "the 4th in 24 hours: 429 lookup_limit", brief(r))
        detail = r.json().get("detail", "")
        check("3 catalogue look-ups in the last 24 hours" in detail and "staff access" in detail,
              "...in plain words, with the number", detail)
        check(len(_jobs(case)) == before and sum(a == "catalogue_lookup_requested" for a, _ in history(case)) == 3,
              "...and nothing queued or written to the history", repr(history(case)[-2:]))
        r = _lookups(f, other)
        check(r.status_code == 429 and code_of(r) == "lookup_limit", "the count is the person's: another case of hers too",
              brief(r))
        gil = new_user("Gil")
        r = _lookups(signed_in(gil), new_case(gil))
        check(r.status_code == 202, "someone else meanwhile: 202", brief(r))
        db = SessionLocal()
        try:
            from datetime import timedelta

            for j in db.query(CatalogueJob).filter(CatalogueJob.requested_by_id == fay):
                j.created_at = j.created_at - timedelta(hours=25)
            db.commit()
        finally:
            db.close()
        r = _lookups(f, other)
        check(r.status_code == 202, "a day later: 202 again", brief(r))

        sam = new_user("Sam", sees_all_cases=True)
        s = signed_in(sam)
        mine = new_case(sam)
        codes = []
        for _ in range(20):
            codes.append(_lookups(s, mine).status_code)
            s.post(f"/reports/{mine}/catalogue/cancel")
        check(codes == [202] * 20, "staff (a tick): more than 3, up to 20 in an hour", repr(sorted(set(codes))))
        r = _lookups(s, mine)
        check(r.status_code == 429 and code_of(r) == "lookup_limit"
              and "20 catalogue look-ups in the last hour" in r.json().get("detail", ""),
              "the 21st in an hour: 429 lookup_limit", brief(r))
        r = _lookups(W.a, new_case(W.admin))
        check(r.status_code == 202, "an admin with none lately: 202", brief(r))
    finally:
        lift_lookup_caps()


def _claimed_lookup(client: TestClient, report_id: str) -> tuple[int, dict]:
    """A look-up queued and claimed by the worker, its result not sent yet:
    (job id, the result it would send)."""
    r = client.post(f"/reports/{report_id}/catalogue/lookup", json={})
    assert r.status_code == 202, brief(r)
    job_id = r.json()["job"]["id"]
    job = _claim(job_id)
    assert job.get("job_id") == job_id, job
    asked = {p["source_part"] for p in job["parts"]}
    body = the_result()
    return job_id, {**body, "parts": [p for p in body["parts"] if p["source_part"] in asked]}


def _send_result(job_id: int, body: dict, out: dict) -> None:
    """The worker's POST .../result, called straight (on another thread)."""
    try:
        out["result"] = catalogue_api.worker_result(job_id, body)
    except Exception as exc:  # noqa: BLE001 -- reported by the checks
        out["result"] = exc


def _refs_all_there(report_id: str) -> bool:
    there = suggestions(report_id)
    return all(
        (it.get("catalogue_ref") or {}).get("suggestion_id") in there
        for it in items_of(report_id) if it.get("catalogue_ref")
    )


def result_mid_annex_save() -> None:
    """The worker's result is sent at the point where PUT /annex-a has
    checked its refs and is about to mark them accepted, and given 1.5 s.
    The save took SQLite's write lock with its first statement, so the
    result waits for the save's commit, then keeps the row the line now
    carries. (Before: the result went through at once and deleted that
    row, leaving the line pointing at nothing -- or failed the save with
    StaleDataError when it landed after sync_accepted.)"""
    _drain()
    case = new_case(W.owner)
    look_up(W.o, case)
    gate = by_number(case, GATE)
    check(gate.status == "suggested", "a suggested row to put on a line")
    job_id, body = _claimed_lookup(W.o, case)
    rows = items_of(case)
    named(rows, "Tail gate assy").update(catalogue_ref=ref_for(gate), oem_part_number=GATE)
    out: dict = {}
    real = catalogue.sync_accepted

    def mid_save(*args, **kwargs):
        t = threading.Thread(target=_send_result, args=(job_id, body, out), daemon=True)
        t.start()
        t.join(1.5)
        out["thread"], out["done_mid_save"] = t, not t.is_alive()
        return real(*args, **kwargs)

    catalogue.sync_accepted = mid_save
    try:
        r = put(W.o, case, rows)
    finally:
        catalogue.sync_accepted = real
    if "thread" in out:
        out["thread"].join(10)
    check(r.status_code == 200, "the save: 200", brief(r))
    check(out.get("done_mid_save") is False, "the result waited for the save's commit (the write lock)",
          repr(out.get("done_mid_save")))
    check(isinstance(out.get("result"), dict) and out["result"].get("ok"), "...then was stored", repr(out.get("result")))
    after = suggestions(case)
    check(gate.id in after and after[gate.id].status == "accepted", "the line's row is still there, accepted",
          repr(after.get(gate.id) and after[gate.id].status))
    check(sum(s.part_number == GATE for s in after.values()) == 1, "...and the result didn't add it again")
    check(_refs_all_there(case), "every ref on a line points at a row of the case")
    r = put(W.o, case, items_of(case))
    check(r.status_code == 200, "the next save goes through (no 422)", brief(r))


def result_mid_put_reports() -> None:
    """PUT /reports/{id} with Annex A renders the PDF without the write
    lock; a worker's result landing then replaces a row only this save put
    on a line. The refs are checked again under the lock: that one comes
    off the line, and the save goes through (no StaleDataError, no 500)."""
    _drain()
    case = new_case(W.owner)
    look_up(W.o, case)
    gate = by_number(case, GATE)
    job_id, body = _claimed_lookup(W.o, case)
    data = report_data(case)
    rows = [dict(it) for it in data["annex_a"]["items"]]
    named(rows, "Tail gate assy")["catalogue_ref"] = ref_for(gate)
    out: dict = {}
    real = main._render_after_change

    def render_meanwhile(user, r, what):
        _send_result(job_id, body, out)
        return r.pdf_path

    main._render_after_change = render_meanwhile
    try:
        r = W.o.put(f"/reports/{case}", json={"draft": {"description": data["description"], "annex_a": {"items": rows}}})
    finally:
        main._render_after_change = real
    check(isinstance(out.get("result"), dict) and out["result"].get("ok"), "the result landed during the render",
          repr(out.get("result")))
    check(r.status_code == 200, "the save: 200", brief(r))
    check(gate.id not in suggestions(case), "the row it replaced is gone")
    check(named(items_of(case), "Tail gate assy").get("catalogue_ref") is None and _refs_all_there(case),
          "...and its ref came off the line (no line points at nothing)")


def line_rows_survive() -> None:
    """store_result never deletes a row a saved Annex A line carries, even
    one whose status says "suggested" (stored that way by any path)."""
    _drain()
    case = new_case(W.owner)
    look_up(W.o, case)
    gate = by_number(case, GATE)
    rows = items_of(case)
    named(rows, "Tail gate assy")["catalogue_ref"] = ref_for(gate)
    check(put(W.o, case, rows).status_code == 200, "a row on a line")
    db = SessionLocal()
    try:
        db.get(CatalogueSuggestion, gate.id).status = "suggested"
        db.commit()
    finally:
        db.close()
    look_up(W.o, case)
    after = suggestions(case)
    check(gate.id in after, "a new look-up keeps it, whatever its status")
    check(sum(s.part_number == GATE for s in after.values()) == 1, "...and doesn't add it again")
    check(_refs_all_there(case), "every ref on a line points at a row of the case")


def _draft_with_rows() -> str:
    """A draft (with its intake session) whose look-up after detection has
    come back."""
    rid = new_case(W.owner, case_data(), status="intake")
    db = SessionLocal()
    try:
        db.add(IntakeSession(report_id=rid, stage="ready", reporter_notes=""))
        db.commit()
    finally:
        db.close()
    job_id = catalogue.maybe_auto_lookup(rid)
    job = _claim(job_id)
    assert job.get("job_id") == job_id, job
    asked = {p["source_part"] for p in job["parts"]}
    body = the_result()
    r = W.w.post(f"/catalogue-worker/jobs/{job_id}/result",
                 json={**body, "parts": [p for p in body["parts"] if p["source_part"] in asked]})
    assert r.status_code == 200, brief(r)
    return rid


def refs_on_other_paths() -> None:
    """POST /reports, PUT /intake/{id}/data and filing an intake check a
    line's catalogue_ref as PUT /annex-a does -- this case's own row, its
    facts from the stored row -- but take a bad one off the line instead of
    refusing the save."""
    foreign = next(iter(suggestions(W.other_case).values()))
    fake = {**ref_for(foreign), "suggestion_id": 999_999, "part_number": "MADE-UP-1", "ref_amount": 1.0}

    def with_refs(data: dict, refs: dict) -> dict:
        rows = [dict(it) for it in data["annex_a"]["items"]]
        for name, ref in refs.items():
            named(rows, name)["catalogue_ref"] = ref
        return {**data, "annex_a": {"items": rows}}

    renderer_saved = renderer.html_to_pdf
    renderer.html_to_pdf = lambda html, out, slot_wait=None: Path(out).write_bytes(b"%PDF-1.4 test\n")
    try:
        # POST /reports (by an admin: the owner here is at the 10-new-cases cap).
        draft = with_refs(case_data(), {"Tail gate assy": fake, "Tail lamp LH": ref_for(foreign)})
        r = W.a.post("/reports", json={"draft": draft})
        check(r.status_code == 200, "POST /reports with made-up refs: saved (never refused over one)", brief(r))
        new = r.json().get("id") if r.status_code == 200 else None
        lines = items_of(new) if new else []
        check(bool(lines) and all(it.get("catalogue_ref") is None for it in lines),
              "...with every ref taken off (a new case has no catalogue rows)",
              json.dumps([it.get("catalogue_ref") for it in lines])[:200])
        check(len(lines) == 4 and named(lines, "Tail gate assy")["part_name"] == "Tail gate assy",
              "...the lines themselves kept")

        # An intake draft's autosave.
        rid = _draft_with_rows()
        own = by_number(rid, GATE)
        data = with_refs(case_data(), {"Tail gate assy": ref_for(own, ref_amount=1.0, part_number="FAKE-9"),
                                       "Tail lamp LH": ref_for(foreign), "Rear bumper reinforcement": fake})
        r = W.o.put(f"/intake/{rid}/data", json={"data": data})
        check(r.status_code == 200, "PUT /intake/{id}/data with good and bad refs: 200", brief(r))
        lines = items_of(rid)
        check(named(lines, "Tail gate assy")["catalogue_ref"] == catalogue.ref_of(own),
              "...the draft's own row kept, its facts the stored row's",
              json.dumps(named(lines, "Tail gate assy")["catalogue_ref"]))
        check(named(lines, "Tail lamp LH")["catalogue_ref"] is None
              and named(lines, "Rear bumper reinforcement")["catalogue_ref"] is None,
              "...another case's and a made-up one taken off")

        # Filing it (an admin finishing it: the owner is at the new-case cap).
        r = W.a.post(f"/intake/{rid}/finalize", json={"data": data})
        check(r.status_code == 200, "filing the draft with bad refs: 200", brief(r))
        filed_items = (report_data(rid).get("annex_a") or {}).get("items") or []
        check(bool(filed_items) and all(
            it.get("catalogue_ref") is None or it["catalogue_ref"] == catalogue.ref_of(own) for it in filed_items
        ) and _refs_all_there(rid), "...the filed case carries no made-up or foreign ref",
              json.dumps([it.get("catalogue_ref") for it in filed_items])[:200])
    finally:
        renderer.html_to_pdf = renderer_saved

    # PUT /reports/{id} still refuses one (Phase 2), and so does PUT /annex-a.
    data = report_data(W.case)
    rows = [dict(it) for it in data["annex_a"]["items"]]
    rows[0]["catalogue_ref"] = fake
    r = W.o.put(f"/reports/{W.case}", json={"draft": {"description": data["description"], "annex_a": {"items": rows}}})
    check(r.status_code == 422 and code_of(r) == "catalogue_ref", "PUT /reports/{id} with a made-up ref: still 422", brief(r))
    rows = items_of(W.case)
    rows[0]["catalogue_ref"] = fake
    r = put(W.o, W.case, rows)
    check(r.status_code == 422 and code_of(r) == "catalogue_ref", "PUT /annex-a with a made-up ref: still 422", brief(r))


def set_vehicle_and_parts(report_id: str) -> None:
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        r.data = case_data()
        db.commit()
    finally:
        db.close()


def main_() -> int:
    run_section("setup", setup)
    run_section("attach to a line, add a new line", attach_and_add)
    run_section("review", review_check)
    run_section("refused refs", refused_refs)
    run_section("an older client, unlink, remove", old_client_and_unlink)
    run_section("a hidden row put on a line", hidden_then_attached)
    run_section("re-sync, prefill and re-run keep the refs", resync_keeps_refs)
    run_section("the estimate is never the ref's", est_never_changes)
    run_section("the PDF and the workbook never show it", exports_never_show_it)
    run_section("no_parts", no_parts_reason)
    run_section("worker and server agree", worker_and_server_agree)
    run_section("PUT /reports/{id} too", put_reports_path)
    run_section("match hints on the live site's names", live_names_hint)
    run_section("Phase 3: tidy names", tidy_names)
    run_section("Phase 3: colour versions", colour_versions)
    run_section("Phase 3: a diagram shown once", same_diagram_once)
    run_section("Phase 3: makes the catalogues cover", makes_covered)
    run_section("Phase 3: the look-up after detection", auto_lookup)
    run_section("Phase 3: the hook in the AI reading", detection_hook)
    run_section("Phase 3: a deleted draft takes its look-up", drafts_deleted)
    run_section("review fixes: the look-up cap", lookup_quota)
    run_section("review fixes: a worker result in the middle of an Annex A save", result_mid_annex_save)
    run_section("review fixes: a worker result during PUT /reports/{id}", result_mid_put_reports)
    run_section("review fixes: a row on a line survives a new look-up", line_rows_survive)
    run_section("review fixes: refs on the other save paths", refs_on_other_paths)
    run_section("no AI, no network", ai_schema_untouched)
    total = PASSED[0] + len(FAILURES)
    print(f"\ntest_catalogue_accept: {PASSED[0]} checks passed, {len(FAILURES)} failed (of {total})")
    for section, label in FAILURES:
        print(f"  FAILED [{section}] {label}")
    if not FAILURES:
        print("\nALL CATALOGUE ACCEPT CHECKS PASSED")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main_())
