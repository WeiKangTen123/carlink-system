"""Phase 1 of the report-and-parts plan, the report part (owner "proceed",
23 Nov 2026; plans R-report sections 1, 2.5-2.8, 5, 6 and P-parts 2.4, 5).

    python scripts/test_p1_report_checks.py

Checked:
  - sign-off's must-have list (review.MUST_HAVE): refused (409 must_have,
    the list in words, admins too, no tick gets past it) until each fact is
    filled in or marked N/A; Make & Model asks for whichever half is
    missing; blanks of spaces are blank; an N/A counts while the field is
    blank; N/A through PUT /reports/{id}/checked and the wizard's PUT
    /intake/{id}/checked on fields with any rule; GET /reports/{id}'s
    review says must_have and before_sign_off; filing a draft never asks;
    a blank workshop or point of impact is "needs";
  - another model's lines in the past-case kit (history.lines_for_car,
    R-report 2.8, review M6): only a line naming another model of the
    case's make is left out (whole words, two-word models, no make names,
    no ordinary words like "step" / "fit" / "new" unless an emblem's
    capitals), the case's own model and twins untouched also when typed
    with the make, tail gate vs boot lid by body type (unknown: as
    written) -- in suggest() and in the Annex A sync, on made-up lines;
  - the optional dates (dismantle, before / after painting) left out of
    the PDF and the Excel workbook when empty, printed when recorded;
  - a must-have fact marked N/A and left blank prints "N/A" in the PDF and
    the Excel workbook (not "Not recorded" / "(to be issued)"); unmarked
    blanks print as before;
  - the point-of-impact note as a usual value (template, "check");
  - INTAKE_FIRM_WORDS (default off): off, a reading parsed offline is
    saved byte for byte as master's; on, the static-check / transmission
    words in the firm's words by meaning, a finding or unclear word kept as
    the AI wrote it (never blank, never "Good") and still to check, the
    AI's own word kept as a note (ai_words, ai_read.notes);
  - Necessary and Serviceable in the one-click condition list, and a
    person's word order kept ("Torn/cracked") through PATCH damage-items,
    the wizard's save and Annex A.

The soft format checks of the cover page (dates, plate, chassis, engine
no., speedometer, tyres, impact against the parts) are the dashboard's
lib/coverChecks.ts: scripts/check-cover-checks.mjs checks them.

Offline and self-contained: a temporary database, the PDF render captured
before Chromium, Gemini refused. Every value is made up (plates like
"TEST 1234"); the past lines use the firm's part wording only.
"""
import contextlib
import copy
import html as html_lib
import json
import os
import re
import socket
import sys
import tempfile
import traceback
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
_tmp = Path(tempfile.mkdtemp(prefix="p1-report-checks-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
os.environ["STORAGE_DIR"] = str(STORAGE)
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaSyFakeCompanyKeyForP1ReportChecks01"
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

import app.api.intake as intake_api  # noqa: E402
import app.api.main as main  # noqa: E402
import app.rendering.renderer as renderer  # noqa: E402
from app import config  # noqa: E402
from app.ai.extraction import _parse_draft  # noqa: E402
from app.auth.core import SESSION_COOKIE, create_session  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake.pipeline import merge_redraft  # noqa: E402
from app.parts.history import (  # noqa: E402
    PastCase, PastLine, import_case, kit_wording, lines_for_car, other_models, past_lines, suggest,
)
from app.parts.prefill import sync_annex_a  # noqa: E402
from app.rendering.xlsx_export import render_xlsx  # noqa: E402
from app.reports import firm_words, review  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.filing import ai_read_for, file_case_data  # noqa: E402
from app.reports.models import AuditEntry, PastAnnexLine, PastLabourLine, Report, User, gen_id  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402
from app.reports.taxonomy import FIRM_CONDITION_COMMON, FIRM_WHOLE_CONDITIONS, parse_condition  # noqa: E402

_real_html_to_pdf = renderer.html_to_pdf


def _fake_render(data, photo_paths, output_path, report_id=None, **_kw):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(b"%PDF-1.4\n% test\n%%EOF\n")


main.render_pdf = _fake_render
intake_api.render_pdf = _fake_render
app = main.app

# ---------------------------------------------------------------- harness
PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}
BASE = "https://carlink.test"
W = SimpleNamespace()


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
    return f"{r.status_code} {r.text[:400]}"


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


def blank() -> dict:
    return SecurityIncidentDraft(description="Made-up test case.", category=["Vehicle Collision or Damage"]).model_dump()


def with_values(data: dict, values: dict) -> dict:
    for key, value in values.items():
        data = review.with_value(data, key, value)
    return data


def new_case(owner_id: str | None, data: dict | None = None, status: str = "confirmed") -> str:
    db = SessionLocal()
    try:
        r = Report(channel="manual", reporter_chat_id="dashboard", status=status, owner_id=owner_id,
                   data=data if data is not None else blank(), photo_paths=[], ai_draft={})
        db.add(r)
        db.commit()
        return r.id
    finally:
        db.close()


def history(report_id: str) -> list[tuple[str, str]]:
    db = SessionLocal()
    try:
        return [(e.action, e.detail or "") for e in
                db.query(AuditEntry).filter(AuditEntry.report_id == report_id).order_by(AuditEntry.id)]
    finally:
        db.close()


def stored(report_id: str) -> Report:
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        db.expunge(r)
        return r
    finally:
        db.close()


# The must-have facts, made up.
FILLED = {
    "report_id": "TEST REF 0001",
    "cover_page.date_of_request": "1st November 2026",
    "cover_page.requested_by": "Requester One",
    "insurance_details.claim_type": "Third party",
    "insurance_details.workshop_assigned": "Workshop One",
    "cover_page.date_of_survey": "3rd November 2026",
    "vehicle_info.plate_number": "TEST 1234",
    "vehicle_info.make": "Makeone",
    "vehicle_info.model": "Modelone",
    "vehicle_info.point_of_impact": "Rear centre",
}
SIGN = {"terms_accepted": True, "open_items_ack": True}


# ---------------------------------------------------------------- sections


def setup() -> None:
    init_db()
    W.admin = new_user("Admin", role="admin")
    W.signer = new_user("Sid", may_sign_off=True)
    W.owner = new_user("Una")
    W.a = signed_in(W.admin)
    W.s = signed_in(W.signer)
    W.o = signed_in(W.owner)
    check(W.a.get("/auth/me").status_code == 200, "signed in")


def must_have_rules() -> None:
    labels = [label for label, _ in review.MUST_HAVE]
    check(labels == ["Our Ref", "Date of request", "Requested by", "Type of claim", "Workshop", "Date of survey",
                     "Registration No.", "Make & Model", "Point of impact"],
          "the owner's nine facts, in the cover form's order", repr(labels))
    check(review.MUST_HAVE_KEYS <= set(review.FIELD_RULES), "every must-have key is a reviewed field (N/A-able)")
    check(review.missing_before_sign_off(with_values(blank(), FILLED)) == [], "all filled: nothing missing")
    missing = review.missing_before_sign_off(blank())
    check([m["label"] for m in missing] == labels, "a blank case misses all nine")
    half = with_values(blank(), {**FILLED, "vehicle_info.model": "  "})
    check(review.missing_before_sign_off(half) == [{"label": "Make & Model", "keys": ["vehicle_info.model"]}],
          "Make & Model with only spaces in the model: that half missing", repr(review.missing_before_sign_off(half)))
    na = with_values(blank(), {k: v for k, v in FILLED.items() if k != "insurance_details.workshop_assigned"})
    na["checked"] = {"insurance_details.workshop_assigned": {"by": "Admin", "by_id": "x", "at": "t", "na": True}}
    check(review.missing_before_sign_off(na) == [], "the workshop marked N/A counts")
    confirmed_blank = dict(na)
    confirmed_blank["checked"] = {"insurance_details.workshop_assigned": {"by": "Admin", "by_id": "x", "at": "t"}}
    check([m["label"] for m in review.missing_before_sign_off(confirmed_blank)] == ["Workshop"],
          "a confirm without N/A on a blank field does not")
    text = review.must_have_message(review.missing_before_sign_off(with_values(blank(), {
        **FILLED, "cover_page.requested_by": None, "cover_page.date_of_survey": "",
        "insurance_details.workshop_assigned": None})))
    check(text.startswith("3 things before sign-off: Requested by, Workshop, Date of survey.") and "admins too" in text,
          "the words: how many, which, and that admins are no exception", text)

    rv = review.review_state(blank(), {}, [], signed_off=False)
    check(rv["fields"]["insurance_details.workshop_assigned"]["state"] == "needs"
          and rv["fields"]["vehicle_info.point_of_impact"]["state"] == "needs",
          "a blank workshop or point of impact needs a person (on the list)")
    check(rv["fields"]["vehicle_info.color"]["state"] == "none", "another blank guess still says nothing")
    check([m["label"] for m in rv["must_have"]] == labels and [m["label"] for m in rv["before_sign_off"]] == labels,
          "review: must_have and before_sign_off")
    check(review.review_state(blank(), {}, [], signed_off=True)["before_sign_off"] == [], "signed off: nothing asked")


def sign_off_block() -> None:
    case = new_case(W.owner)
    r = W.a.post(f"/reports/{case}/sign-off", json=SIGN)
    body = r.json()
    check(r.status_code == 409 and body.get("code") == "must_have", "an admin, a blank case, with the ack: 409 must_have",
          brief(r))
    check([m["label"] for m in body.get("missing", [])][:3] == ["Our Ref", "Date of request", "Requested by"]
          and len(body["missing"]) == 9 and body["detail"].startswith("9 things before sign-off: Our Ref,"),
          "...the list, in words and as keys", repr(body)[:300])
    check(stored(case).status == "confirmed", "...nothing signed")
    r = W.a.post(f"/reports/{case}/sign-off", json={})
    check(r.status_code == 400 and r.json().get("code") == "declaration_required", "no declaration: still 400 first", brief(r))

    some = new_case(W.signer, with_values(blank(), {**FILLED, "cover_page.requested_by": None, "cover_page.date_of_survey": " ",
                                                     "insurance_details.workshop_assigned": None}))
    r = W.s.post(f"/reports/{some}/sign-off", json=SIGN)
    check(r.status_code == 409
          and r.json()["detail"].startswith("3 things before sign-off: Requested by, Workshop, Date of survey."),
          "a 'may sign off' account: 3 things", brief(r))
    detail = W.s.get(f"/reports/{some}").json()["review"]
    check([m["label"] for m in detail["before_sign_off"]] == ["Requested by", "Workshop", "Date of survey"],
          "GET /reports/{id}: review.before_sign_off says the same")

    # The workshop and the survey date don't apply: N/A. Requested by is typed.
    r = W.s.put(f"/reports/{some}/checked", json={"set": {"insurance_details.workshop_assigned": {"na": True},
                                                          "cover_page.date_of_survey": {"na": True}}})
    check(r.status_code == 200 and [m["label"] for m in r.json()["review"]["before_sign_off"]] == ["Requested by"],
          "N/A on a guess field and an office field: one thing left", brief(r))
    check(("fields_confirmed", "confirmed 2 fields (N/A: Workshop, Date of survey)") in history(some),
          "the history names the must-haves marked N/A (review L4)", repr(history(some)))
    r = W.s.put(f"/reports/{some}/cover-page", json={"cover_page": {"requested_by": "Requester Two"}})
    check(r.status_code == 200, "Requested by typed on the cover page", brief(r))
    r = W.s.post(f"/reports/{some}/sign-off", json={"terms_accepted": True})
    check(r.status_code == 409 and r.json().get("code") == "open_items", "every fact there: the open-items warning is next",
          brief(r))
    r = W.s.post(f"/reports/{some}/sign-off", json=SIGN)
    check(r.status_code == 200 and stored(some).status == "Signed Off", "...and with the ack: signed off", brief(r))
    check(W.s.get(f"/reports/{some}").json()["review"]["before_sign_off"] == [], "signed off: before_sign_off is empty")

    # Our Ref N/A is staff's (the signer may); a value given after an N/A is the value.
    ref = new_case(W.owner, with_values(blank(), {**FILLED, "report_id": None}))
    r = W.o.put(f"/reports/{ref}/checked", json={"set": {"report_id": {"na": True}}})
    check(r.status_code == 403, "the owner (not staff) can't mark Our Ref N/A", brief(r))
    r = W.a.put(f"/reports/{ref}/checked", json={"set": {"report_id": {"na": True}}})
    check(r.status_code == 200 and r.json()["review"]["before_sign_off"] == [], "an admin may: nothing missing", brief(r))
    r = W.a.post(f"/reports/{ref}/sign-off", json=SIGN)
    check(r.status_code == 200, "signed off with Our Ref N/A", brief(r))


def filing_never_asks() -> None:
    r = W.o.post("/intake")
    check(r.status_code == 200, "a new draft", brief(r))
    draft = r.json()["id"]
    state_ = W.o.get(f"/intake/{draft}").json()
    check([m["label"] for m in state_["review"]["before_sign_off"]][:2] == ["Our Ref", "Date of request"],
          "GET /intake/{id}: the draft's review lists what sign-off will ask")
    check(review.value_at(state_["data"], "cover_page.point_of_impact_note") == review.POINT_OF_IMPACT_NOTE
          and state_["review"]["fields"]["cover_page.point_of_impact_note"]["state"] == "check",
          "a new draft has the firm's impact note, as a usual value to check")
    r = W.o.put(f"/intake/{draft}/checked", json={"set": {"vehicle_info.point_of_impact": {"na": True}}})
    check(r.status_code == 200 and r.json()["review"]["fields"]["vehicle_info.point_of_impact"]["state"] == "na",
          "the wizard: N/A on the point of impact (a guess field)", brief(r))
    data = dict(W.o.get(f"/intake/{draft}").json()["data"])
    r = W.o.post(f"/intake/{draft}/finalize", json={"data": data})
    check(r.status_code == 200 and stored(draft).status == "confirmed", "filed with every must-have empty: filing never asks",
          brief(r))
    db = SessionLocal()
    try:
        out = file_case_data(db, blank(), None, ai_draft=None, photo_kinds=None)
    finally:
        db.close()
    check(out["cover_page"]["point_of_impact_note"] == review.POINT_OF_IMPACT_NOTE
          and "cover_page.point_of_impact_note" in out["template_fields"],
          "filing fills the impact note as a usual value too")


# ------------------------------------------------------------ the kit


def _line(n, item, condition, est, section="list") -> PastLine:
    return PastLine(line_no=n, section=section, item=item, condition=condition, remarks="", qty=1, qty_raw="1",
                    workshop_est=est, discount=0.0 if section == "nett" else 0.2, adjusted_cost=round(est * 0.8, 2))


def _case(ref, model, lines, make="Honda") -> PastCase:
    return PastCase(ref=ref, plate=f"TEST {ref[-4:]}", make=make, model=model, make_model_raw=f"{make} {model}",
                    registered=None, surveyed="2026-01-01", lines=lines)


SUV_CASE = _case("CL 90001", "Vezel", [
    _line(1, "Tail gate assy", "Bent/cut", 900),
    _line(2, "Tail gate emblem VEZEL", "Necessary", 60),
    _line(3, "Tail gate hinges", "Bent", 120),
    _line(4, "Tail gate weatherstripe", "Necessary", 80),
    _line(5, "Tail gate garnish VEZEL", "Grazed", 70),
])
SEDAN_CASE = _case("CL 90002", "Civic", [
    _line(1, "Boot lid assy", "Buckled/cut", 800),
    _line(2, "Boot lid emblem CIVIC", "Necessary", 50),
    _line(3, "Boot lid emblem 1.6", "Necessary", 40),
    _line(4, "Boot lid hinges", "Bent", 100),
    _line(5, "Boot lid badge", "Necessary", 45),
])


def _reimport(*cases) -> None:
    db = SessionLocal()
    try:
        db.query(PastAnnexLine).delete()
        db.query(PastLabourLine).delete()
        for c in cases:
            import_case(db, c, f"{c.ref}.xlsx")
        db.commit()
    finally:
        db.close()


def _kit(model, body_type=None, make="Honda") -> list[str]:
    db = SessionLocal()
    try:
        out = suggest(db, make=make, model=model, damaged_parts=["Boot Lid", "Boot Lid Hinge"], existing_items=[],
                      body_type=body_type)
        return [ln["name"] for g in out["groups"] if g["reason"] == "damaged" for ln in g["lines"]]
    finally:
        db.close()


def _probe(item, model, make="Honda") -> SimpleNamespace:
    """A past line as lines_for_car reads one (made-up wording)."""
    return SimpleNamespace(item=item, model=model, make=make, item_key=item.lower())


# The fresh-eyes review's probe (M6): a history whose models include
# ordinary words (Step, Fit, New), one typed with the make ("Honda Civic"),
# another make's model (Toyota Yaris), and twins (HR-V / Vezel, Jazz / Fit).
PROBE = [
    _probe("Tail gate emblem VEZEL", "Vezel"), _probe("Tail gate garnish VEZEL", "Vezel 1.5X"),
    _probe("Tail gate emblem HR-V", "HR-V"), _probe("Boot lid emblem CIVIC", "Civic"),
    _probe("Front bumper re-fit clips", "Civic"), _probe("Side step LH", "Odyssey"),
    _probe("Front grille Honda badge", "Honda Civic"), _probe("Rear bumper (new type)", "New"),
    _probe("Rear bumper", "Step"), _probe("Tail gate assy", "Step WGN"), _probe("Rear bumper fit clip", "Fit"),
    _probe("Tail gate emblem FIT", "Fit"), _probe("Rear bumper garnish YARIS", "Civic"),
    _probe("Tail gate emblem YARIS", "Yaris", make="Toyota"), _probe("Boot lid emblem 1.6", "Civic"),
    _probe("Boot lid assy", "Civic"),
]


def _worded(case_make, case_model, item, line_model, body=None):
    return kit_wording(item, line_model=line_model, model=case_model, body=body,
                       others=other_models(PROBE, case_make, case_model))


def kit_filter() -> None:
    jazz_others = other_models(PROBE, "Honda", "Jazz")
    check(("vezel",) in jazz_others and ("civic",) in jazz_others and ("step", "wgn") in jazz_others,
          "the make's other models: Vezel (and its twin HR-V as one), Civic, Step WGN as two words", repr(sorted(jazz_others)))
    check(("fit",) not in jazz_others and ("jazz",) not in jazz_others,
          "...never the case's own model or its twin (Jazz is a Fit)", repr(sorted(jazz_others)))
    check(("honda",) not in jazz_others and ("yaris",) not in jazz_others,
          "...never a make's name ('Honda Civic' is a Civic), never another make's model (Toyota Yaris)",
          repr(sorted(jazz_others)))

    # The review's probe lines: ordinary words that are also a model's name somewhere.
    kept = [("Side step LH", "Odyssey"), ("Front bumper re-fit clips", "Civic"),
            ("Front grille Honda badge", "Honda Civic"), ("Rear bumper (new type)", "New")]
    for case_model in ("Freed", "City", "Jazz"):
        wrong = [(item, _worded("Honda", case_model, item, line)) for item, line in kept
                 if _worded("Honda", case_model, item, line) != item]
        check(not wrong, f"a {case_model}: 'Side step LH', 'Front bumper re-fit clips', 'Front grille Honda badge',"
                         " 'Rear bumper (new type)' are kept as written", repr(wrong))
    check(_worded("Honda", "City", "Rear bumper fit clip", "Fit") == "Rear bumper fit clip",
          "'fit' in lower case is a word, not the Fit")

    check(_worded("Honda", "Jazz", "Tail gate emblem VEZEL", "Vezel", "hatch") is None,
          "a Jazz: 'Tail gate emblem VEZEL' is another model's: left out")
    check(_worded("Honda", "Jazz", "Tail gate garnish VEZEL", "Vezel 1.5X", "hatch") is None
          and _worded("Honda", "Jazz", "Tail gate emblem HR-V", "HR-V", "hatch") is None,
          "...so are 'Tail gate garnish VEZEL' and the twin's 'Tail gate emblem HR-V'")
    check(_worded("Honda", "Jazz", 'Tail gate emblem "vezel"', "Vezel", "hatch") is None,
          "any letter case, in quotes too")
    check(_worded("Honda", "Jazz", "Tail gate emblem STEP WGN", "Odyssey", "hatch") is None
          and _worded("Honda", "Jazz", "Tail gate emblem STEPWGN", "Odyssey", "hatch") is None,
          "a two-word model: the two words together, or run into one")
    check(_worded("Honda", "City", "Tail gate emblem FIT", "Fit", "sedan") is None,
          "an emblem in capitals naming an ordinary-word model (FIT) on a City: left out")
    check(_worded("Honda", "Jazz", "Tail gate emblem FIT", "Fit", "hatch") == "Tail gate emblem FIT",
          "...but on a Jazz it is its own twin's line: as it is")

    for case_model in ("Vezel", "Honda Vezel", "HONDA VEZEL 1.5X", "HR-V"):
        mine = [ln.item for ln in lines_for_car(PROBE, "Honda", case_model)]
        check({"Tail gate emblem VEZEL", "Tail gate garnish VEZEL", "Tail gate emblem HR-V"} <= set(mine)
              and "Boot lid emblem CIVIC" not in mine,
              f"a '{case_model}' case keeps its own Vezel / HR-V lines and leaves the Civic's emblem out", repr(mine))
    jazz = [ln.item for ln in lines_for_car(PROBE, "Honda", "Jazz")]
    check("Rear bumper garnish YARIS" in jazz and "Tail gate emblem YARIS" in jazz,
          "another make's model names (Toyota Yaris) don't affect a Honda", repr(jazz))
    check("Tail gate emblem 1.6" in jazz and "Tail gate assy" in jazz,
          "an emblem naming no model (a variant's '1.6') is kept, worded for the hatchback", repr(jazz))
    check(kit_wording("Front tyre x1", line_model="Civic", model="Jazz", body="hatch", others=frozenset({("x1",)}))
          == "Front tyre x1", "a word under 3 characters (or a number) is never a model name")

    nothing = [ln.item for ln in lines_for_car(PROBE, "Honda", None)]
    check(nothing == [ln.item for ln in PROBE], "no model recorded: nothing left out by name (any might be its own)",
          repr(nothing))
    check(len(lines_for_car(PROBE, None, "Jazz")) == len(PROBE), "no make recorded: nothing left out by name either")
    freed = [ln.item for ln in lines_for_car(PROBE, "Honda", "Freed")]
    check("Boot lid assy" in freed and "Tail gate assy" in freed,
          "a body nobody knows (a Freed, none recorded): tail gate / boot lid as written", repr(freed))
    stated = [ln.item for ln in lines_for_car(PROBE, "Honda", "Freed", "MPV")]
    check("Boot lid assy" not in stated and stated.count("Tail gate assy") == 2,
          "...recorded as an MPV: a tail gate", repr(stated))
    check(kit_wording("Tail gate hinges", line_model="Vezel", model="City", body="sedan") == "Boot lid hinges",
          "a sedan: tail gate -> boot lid")
    check(kit_wording("Boot lid badge", line_model="Civic", model="Jazz", body="hatch") == "Tail gate badge",
          "a badge (the make's logo) stays, worded for a hatchback")

    _reimport(SUV_CASE, SEDAN_CASE)
    jazz = _kit("Jazz")
    check(jazz and not any(re.search(r"vezel|civic|boot lid", n, re.I) for n in jazz),
          "a Jazz (hatchback, no past cases): no other model's name, no boot lid", repr(jazz))
    check("Tail gate assy" in jazz and "Tail gate hinges" in jazz and "Tail gate badge" in jazz
          and sum(1 for n in jazz if n.lower().endswith("assy")) == 1,
          "...one tail gate assy (the two models' panels as one line), hinges and badge", repr(jazz))
    city = _kit("City")
    check(city and not any(re.search(r"vezel|civic|tail ?gate", n, re.I) for n in city)
          and "Boot lid assy" in city and "Boot lid hinges" in city and "Boot lid badge" in city,
          "a City (sedan): boot lid wording, no tail gate, no other model's name", repr(city))
    vezel = _kit("Vezel")
    check("Tail gate emblem VEZEL" in vezel and "Tail gate garnish VEZEL" in vezel and not any("Boot lid" in n for n in vezel),
          "a Vezel: its own model's lines as they are, no Civic line", repr(vezel))
    typed = _kit("Honda Vezel")
    check("Tail gate emblem VEZEL" in typed and "Tail gate garnish VEZEL" in typed
          and not any(re.search(r"civic", n, re.I) for n in typed),
          "a 'Honda Vezel' (typed with the make): its own lines as they are", repr(typed))
    unknown = _kit(None)
    check(unknown and any("VEZEL" in n for n in unknown) and any("CIVIC" in n for n in unknown),
          "no model recorded: nothing left out by name", repr(unknown))
    stated = _kit("Modelx", body_type="Hatchback")
    check("Tail gate assy" in stated and not any("Boot lid" in n for n in stated),
          "a model the catalogue doesn't know: the recorded body type decides", repr(stated))

    db = SessionLocal()
    try:
        lines = past_lines(db)
        before = sorted((ln.item, ln.item_key) for ln in lines)
        lines_for_car(lines, "Honda", "Jazz")
        db.flush()
        after = sorted((ln.item, ln.item_key) for ln in past_lines(db))
        check(before == after and not db.dirty, "the stored past lines are never changed (a stand-in carries the new wording)")
        data = with_values(blank(), {"vehicle_info.make": "Honda", "vehicle_info.model": "Jazz"})
        data["damage_summary"] = [{"part": "Boot Lid", "condition": "Bent/cut", "remarks": "Replace", "human_verified": True}]
        rows = [r["part_name"] for r in sync_annex_a(db, data, price_book=True)["annex_a"]["items"]]
    finally:
        db.rollback()
        db.close()
    check(rows and not any(re.search(r"vezel|civic|boot lid", n, re.I) for n in rows)
          and sum(1 for n in rows if n.lower().startswith("tail gate assy")) == 1,
          "Annex A for the Jazz: priced kit rows without another model's lines, one tail gate", repr(rows))
    _reimport()


# ---------------------------------------------------- PDF and Excel


def _render_html(report: dict) -> str:
    captured: list[str] = []
    renderer.html_to_pdf = lambda html, path, slot_wait=None: captured.append(html)
    try:
        renderer.render_pdf(report, [], str(_tmp / "captured.pdf"), report_id="unsaved", unverified=False)
    finally:
        renderer.html_to_pdf = _real_html_to_pdf
    return captured[0]


def _text(html: str) -> str:
    body = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", body)).split())


def _cover_labels(report: dict) -> dict:
    import openpyxl

    path = _tmp / f"export-{gen_id()[:6]}.xlsx"
    render_xlsx(copy.deepcopy(report), str(path), report_id="unsaved", unverified=False)
    cover = openpyxl.load_workbook(path)["Cover Page"]
    return {cover.cell(r, 3).value: cover.cell(r, 5).value for r in range(1, cover.max_row + 1) if cover.cell(r, 4).value == ":"}


OPTIONAL = ("Date of dismantle parts", "Date of before painting", "Date of after painting")


def optional_dates() -> None:
    report = review.apply_template_defaults(with_values(blank(), {**FILLED, "incident_datetime": "31st October 2026"}))
    text = _text(_render_html(copy.deepcopy(report)))
    check(not any(label in text for label in OPTIONAL) and "Date of survey" in text,
          "PDF: no optional date recorded, none of their rows (the survey's stays)")
    labels = _cover_labels(report)
    check(not any(label in labels for label in OPTIONAL) and labels.get("Date of survey") == "3rd November 2026",
          "Excel: the same", repr(list(labels))[:300])

    one = with_values(copy.deepcopy(report), {"cover_page.date_after_painting": "9th November 2026",
                                              "cover_page.date_of_dismantle": "   "})
    text = _text(_render_html(copy.deepcopy(one)))
    check("Date of after painting : 9th November 2026" in text
          and "Date of dismantle parts" not in text and "Date of before painting" not in text,
          "PDF: only the recorded one (spaces are not a date)")
    labels = _cover_labels(one)
    check(labels.get("Date of after painting") == "9th November 2026"
          and "Date of dismantle parts" not in labels and "Date of before painting" not in labels,
          "Excel: only the recorded one, as in the PDF")
    check("See Annex(es) and Motor Accident Reports" in text, "the impact note prints under 5 POINT OF IMPACT")
    check("Not recorded" not in text.split("DETAILS OF VEHICLE")[0],
          "section 1 prints no 'Not recorded' when its facts are there")


def _na_marks(*keys) -> dict:
    return {key: {"by": "Admin", "by_id": "x", "at": "t", "na": True} for key in keys}


def _workbook(report: dict):
    import openpyxl

    path = _tmp / f"export-{gen_id()[:6]}.xlsx"
    render_xlsx(copy.deepcopy(report), str(path), report_id="unsaved", unverified=False)
    return openpyxl.load_workbook(path)


def _impact_lines(text: str) -> str:
    return text.split("POINT OF IMPACT", 1)[1].split(" 6 REMARK", 1)[0].strip()


# Each must-have row as the PDF and the Excel cover label it.
MUST_ROWS = ("Date of request", "Requested by", "Type of claim", "Workshop", "Date of survey", "Registration No.",
             "Make & Model")


def na_prints() -> None:
    # Every must-have blank and marked N/A (what an admin does to get past 409 must_have).
    report = review.apply_template_defaults(blank())
    report["cover_page"]["point_of_impact_note"] = None
    report["checked"] = _na_marks(*sorted(review.MUST_HAVE_KEYS))
    check(review.na_must_haves(report) == review.MUST_HAVE_KEYS, "na_must_haves: every must-have key marked N/A and blank")
    text = _text(_render_html(copy.deepcopy(report)))
    wrong = [label for label in MUST_ROWS if f"{label} : N/A" not in text]
    check(not wrong, "PDF: each must-have row marked N/A prints 'N/A'", repr(wrong))
    check("Our Ref: N/A" in text and "(to be issued)" not in text, "PDF: 'Our Ref: N/A', not '(to be issued)'")
    check(_impact_lines(text) == "N/A", "PDF: the point of impact prints 'N/A'", _impact_lines(text))
    check("Not recorded" not in text.split("Accident date", 1)[0] and "Not recorded" not in text.split("DETAILS OF VEHICLE", 1)[1]
          .split("Date of registration", 1)[0], "PDF: no 'Not recorded' on those rows")
    check("Annex A : Assessment / Adjustment on Spare Parts N/A" in text
          and "Annex B : Adjustment on Labour and Spray Painting N/A" in text,
          "PDF: the annex pages head with the plate's 'N/A'")

    book = _workbook(report)
    labels = _cover_labels(report)
    wrong = {label: labels.get(label) for label in MUST_ROWS if labels.get(label) != "N/A"}
    check(not wrong, "Excel: each must-have cell marked N/A says 'N/A'", repr(wrong))
    cover = book["Cover Page"]
    check(cover.cell(8, 2).value == "Our Ref: N/A", "Excel: 'Our Ref: N/A'", repr(cover.cell(8, 2).value))
    cells = [cover.cell(r, 3).value for r in range(1, cover.max_row + 1)]
    impact = cells[cells.index("POINT OF IMPACT") + 2] if "POINT OF IMPACT" in cells else None
    check(impact == "N/A", "Excel: the point of impact says 'N/A'", repr(impact))
    check(book["Annex A"].cell(3, 3).value == "N/A" and book["Annex B"].cell(3, 3).value == "N/A"
          and book["Annex C"].cell(3, 2).value == "N/A", "Excel: the annex sheets head with 'N/A' too")

    # The same blanks, nobody marked them: printed as before.
    unmarked = copy.deepcopy(report)
    unmarked["checked"] = None
    text = _text(_render_html(copy.deepcopy(unmarked)))
    wrong = [label for label in MUST_ROWS if f"{label} : Not recorded" not in text]
    check(not wrong and "Our Ref: (to be issued)" in text and _impact_lines(text) == "Not recorded" and " N/A" not in text,
          "PDF: unmarked blanks still print 'Not recorded' / '(to be issued)'", repr(wrong))
    labels = _cover_labels(unmarked)
    book = _workbook(unmarked)
    check(all(labels.get(label) is None for label in MUST_ROWS) and book["Cover Page"].cell(8, 2).value == "Our Ref: (to be issued)"
          and book["Annex A"].cell(3, 3).value is None,
          "Excel: unmarked blanks stay blank, as before", repr({label: labels.get(label) for label in MUST_ROWS}))

    # A value given after the N/A is the value; half of Make & Model N/A.
    mixed = with_values(copy.deepcopy(report), {"insurance_details.workshop_assigned": "Workshop One",
                                                "vehicle_info.make": "Makeone"})
    mixed["checked"] = _na_marks("insurance_details.workshop_assigned", "vehicle_info.model", "cover_page.requested_by")
    text = _text(_render_html(copy.deepcopy(mixed)))
    check("Workshop : Workshop One" in text and "Make & Model : Makeone" in text and "Requested by : N/A" in text
          and "Date of request : Not recorded" in text,
          "PDF: a value given since prints itself; Make with the model N/A prints the make; the rest as marked")
    half = copy.deepcopy(report)
    half["checked"] = _na_marks("vehicle_info.make")
    text = _text(_render_html(half))
    check("Make & Model : Not recorded" in text, "PDF: only the make N/A, the model blank: still 'Not recorded'")
    labels = _cover_labels(mixed)
    check(labels.get("Workshop") == "Workshop One" and labels.get("Make & Model") == "Makeone"
          and labels.get("Requested by") == "N/A" and labels.get("Date of request") is None,
          "Excel: the same", repr(labels))


# ------------------------------------------------------------ the words


def _reading(**vehicle) -> str:
    """A made-up reading as the model's JSON (no code-only fields)."""
    raw = blank()
    raw["vehicle_info"].update(plate_number="TEST 1234", **vehicle)
    raw["damage_summary"] = [{"part": "Rear Bumper", "damage_type": "Dent", "condition_tokens": ["Dented"],
                              "severity": "Moderate", "remarks": "Replace"}]
    raw = {k: v for k, v in raw.items() if k not in ("cover_page", "checked", "template_fields", "second_look", "ai_words")}
    return json.dumps(raw)


def _master_parse(raw_output: str) -> dict:
    """origin/master's (641b857) _parse_draft, step by step: what a reading
    saved before Phase 1."""
    from app.ai import extraction

    draft = SecurityIncidentDraft.model_validate_json(raw_output)
    draft.report_id = None
    draft = extraction._strip_placeholder_people(draft)
    draft = extraction._sanitize_damage_summary(draft)
    draft = extraction._backfill_damage_summary(draft)
    return extraction._firm_condition(draft).model_dump()


# Representative readings: the words the drafting schema offers, and some
# it doesn't (free text).
READINGS = [
    dict(transmission="CVT", steering_condition="Operable", brakes_condition="Impaired", paintwork_condition="Resprayed"),
    dict(transmission="6-Speed Automatic", steering_condition="Good", brakes_condition="Operable", paintwork_condition="fair"),
    dict(transmission="Semi-automatic", steering_condition="Damaged", brakes_condition="see annex(es)",
         paintwork_condition="Original"),
    dict(transmission="5-speed manual", steering_condition="Faulty", brakes_condition="Not working",
         paintwork_condition="Scratched"),
    dict(transmission=None, steering_condition=None, brakes_condition="", paintwork_condition="Good"),
]


@contextlib.contextmanager
def firm_words_switch(value: str):
    before = settings.intake_firm_words
    settings.intake_firm_words = value
    try:
        yield
    finally:
        settings.intake_firm_words = before


def firm_words_setting() -> None:
    check(config.Settings().intake_firm_words == "off" and settings.intake_firm_words == "off",
          "INTAKE_FIRM_WORDS defaults to off, and this process runs at it")
    check(config.Settings(intake_firm_words="ON").intake_firm_words == "on"
          and config.Settings(intake_firm_words="").intake_firm_words == "off", "written off / on, any case; blank is off")
    try:
        config.Settings(intake_firm_words="maybe")
        check(False, "a typo is refused")
    except Exception:
        check(True, "a typo is refused")
    compose = (ROOT.parent.parent / "infra" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    anchor, services = compose.index("x-bot-service: &bot-service"), compose.index("\nservices:")
    lines = [ln for ln in compose.splitlines() if ln.startswith("    - INTAKE_FIRM_WORDS=")]
    check(lines == ["    - INTAKE_FIRM_WORDS=${INTAKE_FIRM_WORDS:-off}"]
          and anchor < compose.find("- INTAKE_FIRM_WORDS=") < services,
          "docker-compose.prod.yml: once, in the shared block, at config's default (off)", repr(lines))


def firm_words_off() -> None:
    with firm_words_switch("off"):
        for vehicle in READINGS:
            raw = _reading(**vehicle)
            got = _parse_draft(raw).model_dump()
            want = _master_parse(raw)
            check(json.dumps(got, sort_keys=True) == json.dumps(want, sort_keys=True) and "ai_words" not in got,
                  f"off: {vehicle['transmission']!r} / {vehicle['paintwork_condition']!r} -- the reading saved as master's,"
                  " byte for byte (no ai_words key)")
            v = got["vehicle_info"]
            check(all(v[k] == w for k, w in vehicle.items() if isinstance(w, str) and w.strip()),
                  "...each word as the AI wrote it", repr(v))
        injected = json.loads(_reading(transmission="CVT"))
        injected["ai_words"] = {"transmission": "made up by the model"}
        check("ai_words" not in _parse_draft(json.dumps(injected)).model_dump(), "the model can't write ai_words itself")
        draft = _parse_draft(_reading(**READINGS[0])).model_dump()
        merged = merge_redraft(review.apply_template_defaults(blank()), None, draft)
        check(merged["vehicle_info"]["paintwork_condition"] == "Resprayed"
              and merged["vehicle_info"]["brakes_condition"] == "Impaired",
              "off: a new draft read -- the AI's 'Resprayed' / 'Impaired' replace the usual wording, as before",
              repr(merged["vehicle_info"]))


def ai_words() -> None:
    cases = [
        # Transmission, by meaning.
        ("transmission", "CVT", "Automatic", "CVT"), ("transmission", "Auto", "Automatic", "Auto"),
        ("transmission", "A/T", "Automatic", "A/T"), ("transmission", "A / T", "Automatic", "A / T"),
        ("transmission", "e-CVT", "Automatic", "e-CVT"), ("transmission", "Automatic (CVT)", "Automatic", "Automatic (CVT)"),
        ("transmission", "6-Speed Automatic", "Automatic", "6-Speed Automatic"),
        ("transmission", "Automatic (6-speed)", "Automatic", "Automatic (6-speed)"),
        ("transmission", "Automatic CVT transmission", "Automatic", "Automatic CVT transmission"),
        ("transmission", "Continuously Variable Transmission", "Automatic", "Continuously Variable Transmission"),
        ("transmission", "Automatic / CVT", "Automatic", "Automatic / CVT"), ("transmission", "DCT", "Automatic", "DCT"),
        ("transmission", "AMT", "Automatic", "AMT"), ("transmission", "6AT", "Automatic", "6AT"),
        ("transmission", "Automatic with manual mode", "Automatic", "Automatic with manual mode"),
        ("transmission", "Automatic", "Automatic", None),
        ("transmission", "5-speed manual", "Manual", "5-speed manual"), ("transmission", "Manual (MT)", "Manual", "Manual (MT)"),
        ("transmission", "MT", "Manual", "MT"), ("transmission", "M/T", "Manual", "M/T"), ("transmission", "Manual", "Manual", None),
        # Unclear: the AI's own word stays, as a note too.
        ("transmission", "Semi-automatic", "Semi-automatic", "Semi-automatic"),
        ("transmission", "Automatic or manual", "Automatic or manual", "Automatic or manual"),
        ("transmission", "Unknown", "Unknown", "Unknown"),
        # Static check: plainly positive -> Good; a finding stays as written.
        ("steering_condition", "Operable", "Good", "Operable"), ("brakes_condition", "operable", "Good", "operable"),
        ("steering_condition", "In good working order", "Good", "In good working order"),
        ("steering_condition", "see annex(es)", "see annex(es)", None),
        ("brakes_condition", "See Annexes", "see annex(es)", "See Annexes"),
        ("steering_condition", "Impaired", "Impaired", "Impaired"), ("brakes_condition", "Damaged", "Damaged", "Damaged"),
        ("brakes_condition", "Faulty", "Faulty", "Faulty"), ("steering_condition", "Not working", "Not working", "Not working"),
        ("steering_condition", "in operable", "in operable", "in operable"),
        ("steering_condition", "In-operable", "In-operable", "In-operable"),
        ("paintwork_condition", "Good", "Good", None), ("paintwork_condition", "good condition", "Good", "good condition"),
        ("paintwork_condition", "Scratched", "Scratched", "Scratched"), ("paintwork_condition", "Resprayed", "Resprayed", "Resprayed"),
        ("paintwork_condition", "Faded", "Faded", "Faded"), ("paintwork_condition", "Original", "Original", "Original"),
        ("paintwork_condition", "Fair", "Fair", "Fair"), ("paintwork_condition", "Good, but scratched", "Good, but scratched",
                                                          "Good, but scratched"),
        # Other fields and blanks: untouched.
        ("color", "Pearl White", "Pearl White", None), ("transmission", None, None, None), ("transmission", "  ", "  ", None),
    ]
    wrong = [(f, v, firm_words.firm_word(f, v), (want, note)) for f, v, want, note in cases
             if firm_words.firm_word(f, v) != (want, note)]
    check(not wrong, "firm_word: by meaning; a finding or an unclear word kept as written (never blank, never Good);"
                     " the AI's word as a note", repr(wrong))
    check(all(firm_words.firm_word(f, v)[0] not in (None, "") for f, v, _, _ in cases if isinstance(v, str) and v.strip()),
          "no word is ever blanked")
    vehicle, notes = firm_words.firm_vehicle_words({"transmission": "CVT", "paintwork_condition": "Scratched", "make": "Makeone"})
    check(vehicle == {"transmission": "Automatic", "paintwork_condition": "Scratched", "make": "Makeone"}
          and notes == {"transmission": "CVT", "paintwork_condition": "Scratched"}, "firm_vehicle_words on a dict",
          repr((vehicle, notes)))

    with firm_words_switch("on"):
        draft = _parse_draft(_reading(**READINGS[0])).model_dump()
    v = draft["vehicle_info"]
    check(v["transmission"] == "Automatic" and v["steering_condition"] == "Good" and v["brakes_condition"] == "Impaired"
          and v["paintwork_condition"] == "Resprayed" and v["plate_number"] == "TEST 1234",
          "on: a reading parsed offline (_parse_draft) -- firm words by meaning, the findings as the AI wrote them",
          repr(v))
    check(draft["ai_words"] == {"transmission": "CVT", "steering_condition": "Operable", "brakes_condition": "Impaired",
                                "paintwork_condition": "Resprayed"},
          "...and the AI's own words kept as ai_words", repr(draft["ai_words"]))
    check("ai_words" not in json.dumps(SecurityIncidentDraft.model_json_schema()), "ai_words is not in the schema the AI sees")
    read, _ = ai_read_for(draft, [])
    check(read["notes"]["transmission"] == "CVT" and read["notes"]["paintwork_condition"] == "Resprayed"
          and read["vehicle_info"]["transmission"] == "Automatic", "ai_read: the notes beside the fields",
          repr(read.get("notes")))
    plain, _ = ai_read_for(with_values(blank(), {"vehicle_info.transmission": "Automatic"}), [])
    check("notes" not in plain, "ai_read: no notes key when there are none")

    current = review.apply_template_defaults(blank())
    merged = merge_redraft(current, None, draft)
    mv = merged["vehicle_info"]
    check(mv["paintwork_condition"] == "Resprayed" and mv["brakes_condition"] == "Impaired" and mv["steering_condition"] == "Good"
          and mv["transmission"] == "Automatic",
          "on: a new draft read -- a finding replaces the usual wording (never printed as 'Good')", repr(mv))
    rv = review.review_state(merged, draft, [], signed_off=False)
    states = {k: rv["fields"][f"vehicle_info.{k}"]["state"] for k in firm_words.MAPPED_FIELDS}
    check(set(states.values()) == {"check"}, "...each an open item to check, none 'none'", repr(states))
    with firm_words_switch("on"):
        unclear = _parse_draft(_reading(transmission="Semi-automatic", paintwork_condition=None)).model_dump()
    rv = review.review_state(merge_redraft(review.apply_template_defaults(blank()), None, unclear), unclear, [], False)
    check(unclear["vehicle_info"]["transmission"] == "Semi-automatic"
          and rv["fields"]["vehicle_info.transmission"]["state"] == "check"
          and rv["fields"]["vehicle_info.paintwork_condition"]["state"] == "check",
          "on: an unclear transmission stays as the AI wrote it, to check (was 'Not recorded' and 'none');"
          " an empty paintwork keeps the usual 'Good', to check", repr(rv["fields"]["vehicle_info.transmission"]))


# ------------------------------------------------------- conditions


def conditions() -> None:
    check(FIRM_CONDITION_COMMON[-2:] == ["Necessary", "Serviceable"]
          and set(FIRM_CONDITION_COMMON[-2:]) <= set(FIRM_WHOLE_CONDITIONS),
          "Necessary and Serviceable in the one-click list, last")
    tax = W.a.get("/taxonomy").json()
    check(tax["condition_common"] == FIRM_CONDITION_COMMON, "GET /taxonomy sends them")
    for typed, firm in (("Torn/cracked", "Torn/cracked"), ("slack/SHIFTED", "Slack/shifted"),
                        ("Shifted/bent/cut", "Shifted/bent/cut")):
        tokens, condition = parse_condition(typed)
        check(condition == firm and tokens == [t.capitalize() for t in firm.split("/")],
              f"parse_condition keeps the person's order: {typed} -> {firm}")

    data = with_values(blank(), FILLED)
    data["damage_summary"] = [{"part": "Rear Bumper", "condition": "Grazed/cut", "remarks": "Replace", "human_verified": True}]
    case = new_case(W.owner, data)
    r = W.o.patch(f"/reports/{case}/damage-items/0", json={"condition": "Torn/cracked"})
    item = r.json().get("item", {}) if r.status_code == 200 else {}
    check(r.status_code == 200 and item.get("condition") == "Torn/cracked"
          and item.get("condition_tokens") == ["Torn", "Cracked"],
          "PATCH damage-items: Torn/cracked stays Torn/cracked", brief(r))
    rows = r.json().get("annex_a", {}).get("items", []) if r.status_code == 200 else []
    check(any(row.get("condition") == "Torn/cracked" for row in rows), "...and Annex A's main row says it so",
          repr([(x.get("part_name"), x.get("condition")) for x in rows]))
    r = W.o.patch(f"/reports/{case}/damage-items/0", json={"condition": "necessary"})
    check(r.status_code == 200 and r.json()["item"]["condition"] == "Necessary", "a quick 'Necessary' is accepted", brief(r))

    r = W.o.post("/intake")
    draft = r.json()["id"]
    data = dict(W.o.get(f"/intake/{draft}").json()["data"])
    data["damage_summary"] = [{"part": "Rear Bumper", "condition": "Slack/shifted", "condition_tokens": ["Slack", "Shifted"],
                               "human_verified": True}]
    r = W.o.put(f"/intake/{draft}/data", json={"data": data})
    saved = stored(draft).data["damage_summary"][0]
    check(r.status_code == 200 and saved["condition"] == "Slack/shifted" and saved["condition_tokens"] == ["Slack", "Shifted"],
          "the wizard's save keeps Slack/shifted", brief(r))


def no_ai() -> None:
    check(not GEMINI, "no Gemini call anywhere in this test", repr(GEMINI))


def main_() -> int:
    for name, fn in (
        ("setup", setup),
        ("the must-have list (review.MUST_HAVE)", must_have_rules),
        ("sign-off refused until filled in or N/A", sign_off_block),
        ("filing never asks; the impact note", filing_never_asks),
        ("another model's kit lines (R-report 2.8)", kit_filter),
        ("optional dates left out of the PDF and Excel", optional_dates),
        ("a must-have marked N/A prints N/A (PDF and Excel)", na_prints),
        ("INTAKE_FIRM_WORDS: the switch", firm_words_setting),
        ("INTAKE_FIRM_WORDS off: every reading as master's", firm_words_off),
        ("INTAKE_FIRM_WORDS on: the AI's words in the firm's words", ai_words),
        ("conditions: one click, the person's order", conditions),
        ("no AI", no_ai),
    ):
        run_section(name, fn)
    print(f"\ntest_p1_report_checks: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        for section, label in FAILURES:
            print(f"  FAIL [{section}] {label}")
        return 1
    print("\nALL P1 REPORT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
