"""Field review (contract 7, app/reports/review.py): what the AI got right,
what to check, what only a person knows.

    python scripts/test_field_review.py

Checked: FIELD_RULES -- every key a real field of the case data, every
cover-form field covered, the six static-check fields "template" with the
firm's usual wording; each state a field, damage item and Annex row can be
in, and the counts and `open` they add up to; old cases with no AI draft
(filled says nothing, an empty office or template field needs a person) and
a draft the AI never read (every value a person's); the template wording
fills a new draft and a case being filed, and never overwrites a value or
a field a person marked; PUT /reports/{id}/checked and PUT
/intake/{id}/checked -- stamped by the server as the signed-in person, N/A,
clear, unknown keys refused, the owner and an admin may, "see all" staff
only look at a draft, a stranger sees nothing, nobody touches a public
report in the inbox or a signed-off case, and the history says "confirmed
N fields"; the wizard's autosave and finalize keep data.checked (stamped
here, never as the browser sent it) and template_fields (the server's);
merge_redraft keeps the marks, and a re-read never changes a confirmed
field; ai_read gains the guessed parties facts from the AI's own draft
only; GET /reports gains needs_you / to_check with no query of its own;
sign-off needs the declaration, answers 409 open_items while anything is
open unless open_items_ack is sent, and its history entry says what was
open either way.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database, the PDF render replaced by a stub, Gemini by a fake that fails
the test if anything calls it, and every outside connection refused. No
real plate, name or report value: every value below is made up.
"""
import json
import os
import re
import socket
import sys
import tempfile
import traceback
from datetime import datetime, timezone
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
_tmp = Path(tempfile.mkdtemp(prefix="field-review-test-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
os.environ["STORAGE_DIR"] = str(STORAGE)
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaSyFakeCompanyKeyForFieldReviewTest01"
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
from sqlalchemy import event  # noqa: E402

import app.api.main as main  # noqa: E402
from app.auth.core import SESSION_COOKIE, create_session  # noqa: E402
from app.intake.pipeline import merge_redraft  # noqa: E402
from app.reports import review  # noqa: E402
from app.reports.db import SessionLocal, engine, init_db  # noqa: E402
from app.reports.filing import ai_read_for  # noqa: E402
from app.reports.models import AuditEntry, IntakeSession, Report, User, gen_id  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402


def _fake_render(data, photo_paths, output_path, report_id=None, **_kw):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(b"%PDF-1.4\n% test\n%%EOF\n")


main.render_pdf = _fake_render
import app.api.intake as intake_api  # noqa: E402

intake_api.render_pdf = _fake_render
app = main.app

STATEMENTS: list[str] = []


@event.listens_for(engine, "before_cursor_execute")
def _seen(conn, cursor, statement, parameters, context, executemany):
    STATEMENTS.append(statement)


# ---------------------------------------------------------------- harness
PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}
BASE = "https://carlink.test"
W = SimpleNamespace()
STATES = {"ai", "check", "needs", "you", "na", "none"}


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


# The AI's own draft for the made-up case: what it "read".
AI_SAID = {
    "vehicle_info.plate_number": "TEST 0001",
    "vehicle_info.make": "Makeone",
    "vehicle_info.model": "Modelone",
    "vehicle_info.color": "Colourone",
    "vehicle_info.odometer_reading": "001234 Km",
    "vehicle_info.tyres": {"front_nearside": {"tread_depth_mm": 6, "brand": "Brandone", "size": None, "condition": None}},
    "insurance_details.insurer_name": "Insurer One",
    "insurance_details.claim_type": "Own damage",
    "police_report.reported_to_police": True,
    "location": "Street One",
}


def ai_draft() -> dict:
    return with_values(blank(), AI_SAID)


PHOTOS = [{"label": "P01", "kind": "overview"}, {"label": "P02", "kind": "log_card"}, {"label": "P03", "kind": "odometer"}]


def state(rv: dict, key: str) -> str:
    return rv["fields"][key]["state"]


def new_case(owner_id: str | None, data: dict | None = None, ai: dict | None = None, status: str = "confirmed") -> str:
    db = SessionLocal()
    try:
        r = Report(channel="manual", reporter_chat_id="dashboard", status=status, owner_id=owner_id,
                   data=data if data is not None else blank(), photo_paths=[], ai_draft=ai)
        db.add(r)
        db.commit()
        return r.id
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


def history(report_id: str) -> list[tuple[str, str]]:
    db = SessionLocal()
    try:
        return [(e.action, e.detail or "") for e in
                db.query(AuditEntry).filter(AuditEntry.report_id == report_id).order_by(AuditEntry.id)]
    finally:
        db.close()


# ---------------------------------------------------------------- sections
def setup() -> None:
    init_db()
    W.admin = new_user("Admin", role="admin")
    W.owner = new_user("Una")
    W.other = new_user("Ben")
    W.staff = new_user("Sam", sees_all_cases=True)
    W.a = signed_in(W.admin)
    W.o = signed_in(W.owner)
    W.b = signed_in(W.other)
    W.s = signed_in(W.staff)
    check(W.o.get("/auth/me").status_code == 200, "signed in")


def rules() -> None:
    allowed = {"doc", "guess", "office", "template", "optional"}
    check(set(review.FIELD_RULES.values()) <= allowed, "every rule is one of doc/guess/office/template/optional")
    # Every key a real field of the case data: it resolves on a full dump.
    dump = blank()

    def resolves(key: str) -> bool:
        node = dump
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        return True

    missing = [k for k in review.FIELD_RULES if not resolves(k)]
    check(not missing, f"all {len(review.FIELD_RULES)} keys are fields of SecurityIncidentDraft", repr(missing))
    check({k for k, v in review.FIELD_RULES.items() if v == "template"} == set(review.TEMPLATE_DEFAULTS),
          "the template fields are exactly the six static-check fields with a usual wording")
    check(set(review.TEMPLATE_DEFAULTS.values()) == {"see annex(es)", "Good", "Nil"},
          "the usual wording: standard words only (see annex(es), Good, Nil)")
    expected = {
        "vehicle_info.plate_number": "doc", "vehicle_info.make": "doc", "vehicle_info.model": "doc",
        "vehicle_info.color": "guess", "vehicle_info.vin": "guess", "vehicle_info.tyres": "guess",
        "insurance_details.claim_type": "guess", "police_report.reported_to_police": "guess",
        "report_id": "office", "cover_page.date_of_survey": "office", "cover_page.requested_by": "office",
        "cover_page.general_condition": "template", "vehicle_info.brakes_condition": "template",
        "cover_page.date_after_painting": "optional", "cover_page.point_of_impact_note": "optional",
        "police_report.report_number": "optional",
    }
    wrong = {k: review.FIELD_RULES.get(k) for k, v in expected.items() if review.FIELD_RULES.get(k) != v}
    check(not wrong, "the contract's rules for a sample of keys", repr(wrong))
    check(set(review.PARTIES_KEYS) <= set(review.FIELD_RULES), "every parties key is a reviewed field")

    # The dashboard's cover form: every field it shows has a rule.
    cover_ts = ROOT.parent / "dashboard" / "lib" / "coverForm.ts"
    if cover_ts.exists():
        block = re.search(r"export type CoverForm = \{(.*?)\};", cover_ts.read_text(encoding="utf-8"), re.S).group(1)
        form_keys = re.findall(r"^\s*(\w+): string;", block, re.M)
        tyre = re.compile(r"^(front|rear)_(nearside|offside)_(mm|brand)$|^(front|rear)_size$")
        prefixes = ("", "vehicle_info.", "cover_page.", "insurance_details.")
        uncovered = [k for k in form_keys if not tyre.match(k) and not any(p + k in review.FIELD_RULES for p in prefixes)]
        check(len(form_keys) > 30 and not uncovered, f"every one of the cover form's {len(form_keys)} fields has a rule", repr(uncovered))
    else:
        check(True, "(no dashboard checkout beside this service: cover form not compared)")


def states() -> None:
    ai = ai_draft()
    data = review.apply_template_defaults(blank())
    data = with_values(data, {
        "vehicle_info.plate_number": "TEST 0001",       # = AI, doc -> ai, from the log card
        "vehicle_info.make": "Makeone",
        "vehicle_info.model": "Modeltwo",               # changed by a person -> you
        "vehicle_info.color": "Colourone",              # = AI, guess -> check
        "vehicle_info.odometer_reading": "001234 Km",
        "vehicle_info.tyres": {"front_nearside": {"tread_depth_mm": 6.0, "brand": "Brandone", "size": "", "condition": None},
                               "rear_offside": {"tread_depth_mm": None, "brand": None, "size": None, "condition": None}},
        "insurance_details.insurer_name": "Insurer One",
        "police_report.reported_to_police": True,
        "location": " Street One ",                     # the AI's, with spaces: still the AI's
        "cover_page.date_of_request": "1st January",    # office, typed -> you
        "cover_page.date_after_painting": "2nd January",  # optional, typed -> you
        "cover_page.modification": "Fitted",            # template changed -> you
        "cover_page.general_condition": "",             # template cleared -> needs
    })
    rv = first = review.review_state(data, ai, PHOTOS, signed_off=False)
    check(state(rv, "vehicle_info.plate_number") == "ai" and rv["fields"]["vehicle_info.plate_number"].get("photo") == "P02",
          "doc = the AI's: ai, with the log-card photo it was read from", repr(rv["fields"]["vehicle_info.plate_number"]))
    check(state(rv, "vehicle_info.model") == "you", "doc changed by a person: you")
    check(state(rv, "vehicle_info.color") == "check", "guess = the AI's: check")
    check(rv["fields"]["vehicle_info.odometer_reading"] == {"state": "check", "photo": "P03"},
          "guess = the AI's with its photo: check, from the odometer photo", repr(rv["fields"]["vehicle_info.odometer_reading"]))
    check(state(rv, "vehicle_info.tyres") == "check", "the tyre table = the AI's (6 and 6.0 alike): check")
    check(state(rv, "insurance_details.insurer_name") == "check", "insurer = the AI's: check")
    check(state(rv, "police_report.reported_to_police") == "check", "reported to police, as the AI said: check")
    check(state(rv, "location") == "check", "a value differing from the AI's only in spaces is still the AI's")
    check(state(rv, "insurance_details.claim_type") == "needs", "claim type empty: needs (a guess the case can't go without)")
    check(state(rv, "insurance_details.workshop_assigned") == "none", "another guess empty: none")
    check(state(rv, "vehicle_info.vin") == "none", "VIN empty: none (only from a VIN-plate photo)")
    check(state(rv, "report_id") == "needs" and state(rv, "cover_page.date_of_survey") == "needs", "office fields empty: needs")
    check(state(rv, "cover_page.date_of_request") == "you", "an office field typed: you")
    check(state(rv, "cover_page.date_of_dismantle") == "none", "optional empty: none")
    check(state(rv, "cover_page.date_after_painting") == "you", "optional typed: you")
    check(state(rv, "vehicle_info.steering_condition") == "check" and state(rv, "cover_page.handbrake_condition") == "check",
          "the firm's usual wording, unchanged: check")
    check(state(rv, "cover_page.modification") == "you", "a template field changed: you")
    check(state(rv, "cover_page.general_condition") == "needs", "a template field cleared: needs")
    check(all(f["state"] in STATES for f in rv["fields"].values()), "every state is one of the six")
    check(rv["rules"] == {**review.FIELD_RULES, **review.ANNEX_RULES}, "rules: FIELD_RULES and the Annex keys")

    # Confirmed: you / na with who and when -- also once signed off.
    data["checked"] = {
        "vehicle_info.color": {"by": "Una", "by_id": "u1", "at": "2026-11-11T09:00:00+00:00"},
        "cover_page.date_of_survey": {"by": "Una", "by_id": "u1", "at": "2026-11-11T09:01:00+00:00", "na": True},
    }
    rv = review.review_state(data, ai, PHOTOS, signed_off=False)
    check(rv["fields"]["vehicle_info.color"] == {"state": "you", "by": "Una", "at": "2026-11-11T09:00:00+00:00"},
          "confirmed: you, by and at", repr(rv["fields"]["vehicle_info.color"]))
    check(rv["fields"]["cover_page.date_of_survey"]["state"] == "na", "marked N/A: na")
    signed = review.review_state(data, ai, PHOTOS, signed_off=True)
    others = {k: f["state"] for k, f in signed["fields"].items() if k not in data["checked"]}
    check(set(others.values()) == {"none"} and signed["open"] == 0, "signed off: none for everything not confirmed, nothing open",
          repr({k: v for k, v in others.items() if v != "none"}))
    check(state(signed, "vehicle_info.color") == "you" and state(signed, "cover_page.date_of_survey") == "na",
          "...a confirmed field still says who confirmed it")

    # Old case: no AI draft at all.
    old = with_values(blank(), {"vehicle_info.plate_number": "TEST 0002", "vehicle_info.steering_condition": "see annex(es)",
                                 "cover_page.requested_by": "Requester One"})
    rv = review.review_state(old, None, None, signed_off=False)
    check(state(rv, "vehicle_info.plate_number") == "none" and state(rv, "vehicle_info.steering_condition") == "none"
          and state(rv, "cover_page.requested_by") == "none", "an old case with no AI draft: a filled field says nothing (none)")
    check(state(rv, "cover_page.date_of_survey") == "needs" and state(rv, "cover_page.general_condition") == "needs",
          "...an empty office or template field: needs")
    check(all("photo" not in f for f in rv["fields"].values()), "...and no photo anywhere")
    rv = review.review_state(old, {}, None, signed_off=False)
    check(state(rv, "vehicle_info.plate_number") == "you", "a draft the AI never read ({}): every value is a person's (you)")

    # Damage items.
    items = [
        {"part": "Rear Bumper", "ai_confidence": "High"},
        {"part": "Left Tail Lamp", "ai_confidence": "Medium", "side_inferred": True},
        {"part": "Boot Lid", "ai_confidence": "Low"},
        {"part": "Front Bumper", "ai_confidence": "Low", "human_verified": True},
        {"part": "Right Tail Lamp", "side_inferred": True, "human_verified": True},
    ]
    rv = review.review_state({**blank(), "damage_summary": items}, ai, None, signed_off=False)
    check(rv["damage"] == ["ai", "check", "check", "you", "you"],
          "damage: AI-found ai, inferred side check, Low check, a person's check you", repr(rv["damage"]))
    check(rv["counts"]["damage"] == {"total": 5, "you": 2, "ai": 1, "check": 2, "needs": 0, "na": 0}, "damage counts",
          repr(rv["counts"]["damage"]))

    # Annex A rows.
    rows = [
        {"part_name": "Rear bumper", "workshop_est": 0, "origin": "detection", "source_part": "Rear Bumper"},
        {"part_name": "Tail lamp", "workshop_est": 180, "prefilled": True, "origin": "history", "suggested_from": "3 past cases"},
        {"part_name": "Bracket", "workshop_est": 40, "origin": "history", "suggested_from": "2 past cases"},
        {"part_name": "Clips", "workshop_est": 20, "origin": "typed"},
        {"part_name": "Grille", "workshop_est": 90, "origin": "lookup"},
        {"part_name": "Old part", "workshop_est": 0, "origin": "typed", "stale": True},
        {"part_name": "Rear Bumper", "workshop_est": 0, "condition": "Serviceable", "origin": "detection"},
        {"part_name": "Mirror", "workshop_est": 0, "condition": "Serviceable", "origin": "typed"},
        {"part_name": "Tail lamp", "workshop_est": 180, "prefilled": False, "origin": "typed", "suggested_from": "3 past cases"},
        {"part_name": "Legacy line", "workshop_est": 75},  # no origin (before 2026-11-04): typed
        # A past-case price whose Condition a person edited: still the past cases' price.
        {"part_name": "Boot lid", "workshop_est": 300, "prefilled": True, "origin": "typed", "condition": "Dented"},
        {"part_name": "  ", "workshop_est": 0, "origin": "typed"},  # no description: the save drops it
        {"part_name": "Hinge", "workshop_est": 0, "condition": " serviceable ", "origin": "detection"},
    ]
    rv = review.review_state({**blank(), "damage_summary": [{"part": "Rear Bumper"}], "annex_a": {"items": rows}}, ai, None, False)
    check(rv["annex_a"] == ["needs", "check", "check", "you", "check", "check", "check", "you", "you", "you", "check", "none", "check"],
          "Annex A: unpriced needs; past-case price, suggestion, looked-up price, stale check; typed you; "
          "Serviceable unpriced (any letter case): the machine's check, a person's you; confirmed (prefilled false) you; "
          "an old priced row you; a past-case price with only its Condition edited still check; no description none",
          repr(rv["annex_a"]))
    check(rv["counts"]["annex_a"] == {"needs_price": 1, "history_price": 2, "check": 7, "you": 4},
          "Annex A counts (check includes the past-case prices)", repr(rv["counts"]["annex_a"]))

    # Annex B and C.
    base = blank()
    rv = review.review_state(base, ai, None, False)
    check(state(rv, "annex_b") == "needs" and rv["counts"]["annex_b"]["needs"] == 1, "Annex B with no lines: needs")
    check(state(rv, "annex_c.agreed_lump_sum") == "needs" and state(rv, "annex_c.repair_days") == "needs",
          "Annex C lump sum and repair days empty: needs")
    na = {**base, "checked": {"annex_b": {"by": "Una", "by_id": "u1", "at": "x", "na": True}},
          "annex_c": {"agreed_lump_sum": 1000.0, "repair_days": 0}}
    rv = review.review_state(na, ai, None, False)
    check(rv["fields"]["annex_b"] == {"state": "na", "by": "Una", "at": "x"}, "no labour (N/A): na", repr(rv["fields"]["annex_b"]))
    check(state(rv, "annex_c.agreed_lump_sum") == "you" and state(rv, "annex_c.repair_days") == "you",
          "a lump sum and repair days typed (0 days too): you")
    lines = {**base, "annex_b": {"items": [{"description": "Respray", "prefilled": True}, {"description": "Labour", "prefilled": False}]}}
    rv = review.review_state(lines, ai, None, False)
    check(state(rv, "annex_b") == "check" and rv["counts"]["annex_b"]["check"] == 1 and rv["counts"]["annex_b"]["you"] == 1,
          "Annex B: a prefilled line check, a typed one you", repr(rv["counts"]["annex_b"]))
    lines["checked"] = {"annex_b": {"by": "Una", "by_id": "u1", "at": "y"}}
    lines["annex_b"]["items"].append({"description": "  ", "prefilled": True})
    rv = review.review_state(lines, ai, None, False)
    check(state(rv, "annex_b") == "check" and rv["counts"]["annex_b"]["total"] == 2,
          "Annex B: a line with no description doesn't count; a plain confirm of annex_b changes nothing", repr(rv["counts"]["annex_b"]))
    lines["annex_b"]["items"][0]["prefilled"] = False
    rv = review.review_state(lines, ai, None, False)
    check(state(rv, "annex_b") == "you", "Annex B: the past-case line edited (prefilled false): you")

    # An N/A given a value since is the person's value.
    later = with_values(blank(), {"cover_page.date_of_survey": "3rd January"})
    later["checked"] = {"cover_page.date_of_survey": {"by": "Una", "by_id": "u1", "at": "z", "na": True},
                        "annex_c.repair_days": {"by": "Una", "by_id": "u1", "at": "z", "na": True}}
    later["annex_c"] = {"repair_days": 5}
    rv = review.review_state(later, ai, None, False)
    check(state(rv, "cover_page.date_of_survey") == "you" and state(rv, "annex_c.repair_days") == "you",
          "a field marked N/A that has a value since: you, not na")

    # open = needs + check over every section.
    rv = first
    counts = rv["counts"]
    total = sum(counts[s]["needs"] + counts[s]["check"] for s in ("cover", "parties", "damage", "annex_b", "annex_c"))
    total += counts["annex_a"]["needs_price"] + counts["annex_a"]["check"]
    check(rv["open"] == total and rv["open"] > 0, f"open = needs + check over the sections ({rv['open']})")
    check(set(counts) == {"cover", "parties", "damage", "annex_a", "annex_b", "annex_c"}, "counts has the six sections")
    check(counts["cover"]["total"] + counts["parties"]["total"] == len(review.FIELD_RULES), "cover + parties = every field")
    check(set(rv) == {"fields", "rules", "damage", "annex_a", "counts", "open"}, "the Review shape (lib/review.ts)")


def template_defaults() -> None:
    filled = review.apply_template_defaults(blank())
    check(all(review.value_at(filled, k) == v for k, v in review.TEMPLATE_DEFAULTS.items())
          and sorted(filled["template_fields"]) == sorted(review.TEMPLATE_DEFAULTS), "an empty draft: all six filled and listed")
    typed = with_values(blank(), {"vehicle_info.steering_condition": "Impaired", "cover_page.modification": "Fitted"})
    typed["checked"] = {"cover_page.general_condition": {"by": "Una", "by_id": "u1", "at": "x", "na": True}}
    out = review.apply_template_defaults(typed)
    check(review.value_at(out, "vehicle_info.steering_condition") == "Impaired" and review.value_at(out, "cover_page.modification") == "Fitted",
          "never overwrites a value")
    check(review.value_at(out, "cover_page.general_condition") is None and "cover_page.general_condition" not in out["template_fields"],
          "never fills a field a person marked")
    check("vehicle_info.steering_condition" not in out["template_fields"], "a field it didn't fill isn't listed")
    check(review.apply_template_defaults(out) == out, "a second run changes nothing")
    check(blank()["vehicle_info"]["steering_condition"] is None, "the stored dicts are never changed in place")

    # A new draft through the API.
    r = W.o.post("/intake")
    check(r.status_code == 200, "POST /intake", brief(r))
    W.draft = r.json()["id"]
    state_ = W.o.get(f"/intake/{W.draft}").json()
    check(review.value_at(state_["data"], "cover_page.handbrake_condition") == "see annex(es)"
          and len(state_["data"]["template_fields"]) == 6, "a new draft starts with the usual wording, listed")
    rv = state_["review"]
    check(rv and state(rv, "cover_page.handbrake_condition") == "check", "GET /intake/{id} review: the usual wording is check")
    check(state(rv, "report_id") == "needs" and rv["open"] > 0, "...Our Ref needs a person")


def checked_endpoints() -> None:
    case = new_case(W.owner, review.apply_template_defaults(blank()), ai_draft())
    W.case = case
    r = W.o.put(f"/reports/{case}/checked", json={"set": {"cover_page.general_condition": {}, "cover_page.date_of_dismantle": {"na": True}}})
    check(r.status_code == 200, "the owner confirms two fields", brief(r))
    body = r.json()
    mark = body["checked"].get("cover_page.general_condition") or {}
    check(mark.get("by", "").startswith("Una") and mark.get("by_id") == W.owner and mark.get("at", "").endswith("+00:00")
          and "na" not in mark, "stamped by the server: the owner's name, id, now", repr(mark))
    check(body["checked"]["cover_page.date_of_dismantle"].get("na") is True, "N/A: na true")
    check(state(body["review"], "cover_page.general_condition") == "you" and state(body["review"], "cover_page.date_of_dismantle") == "na",
          "the answer's review: you, na")
    check(("fields_confirmed", "confirmed 2 fields") in history(case), "history: fields_confirmed, confirmed 2 fields", repr(history(case)))
    detail = W.o.get(f"/reports/{case}").json()
    check(detail["data"]["checked"] == body["checked"] and state(detail["review"], "cover_page.general_condition") == "you",
          "GET /reports/{id}: data.checked and review")

    r = W.o.put(f"/reports/{case}/checked", json={"clear": ["cover_page.date_of_dismantle"]})
    check(r.status_code == 200 and "cover_page.date_of_dismantle" not in r.json()["checked"], "clear forgets a mark", brief(r))
    r = W.o.put(f"/reports/{case}/checked", json={"set": {"vehicle_info.not_a_field": {}}})
    check(r.status_code == 400 and r.json().get("code") == "unknown_field", "an unknown key: 400 unknown_field", brief(r))
    r = W.o.put(f"/reports/{case}/checked", json={"set": {"annex_b": {"na": True}, "annex_c.repair_days": {}}})
    check(r.status_code == 200 and state(r.json()["review"], "annex_b") == "na", "annex_b N/A and annex_c keys accepted", brief(r))
    r = W.o.put(f"/reports/{case}/checked", json={"set": {"annex_c.gst": {}}})
    check(r.status_code == 400, "an Annex C key that isn't reviewed: 400", brief(r))
    r = W.o.put(f"/reports/{case}/checked", json={"set": {"location": {}}, "clear": ["location"]})
    check(r.status_code == 400, "set and clear the same key: 400", brief(r))
    r = W.a.put(f"/reports/{case}/checked", json={"set": {"location": {}}})
    check(r.status_code == 200 and r.json()["checked"]["location"]["by_id"] == W.admin, "an admin confirms on someone's case", brief(r))
    r = W.b.put(f"/reports/{case}/checked", json={"set": {"location": {}}})
    check(r.status_code == 404, "a stranger: 404", brief(r))
    signed = new_case(W.owner, {**blank(), "sign_off": {"status": "Signed Off"}}, None, status="Signed Off")
    r = W.o.put(f"/reports/{signed}/checked", json={"set": {"location": {}}})
    check(r.status_code == 409, "a signed-off case: 409 (reopen first)", brief(r))
    r = W.o.put(f"/reports/{W.draft}/checked", json={"set": {"location": {}}})
    check(r.status_code == 409, "a draft through the case route: 409", brief(r))

    # The draft route.
    r = W.o.put(f"/intake/{W.draft}/checked", json={"set": {"cover_page.modification": {}}})
    check(r.status_code == 200 and state(r.json()["review"], "cover_page.modification") == "you", "the owner confirms a draft field", brief(r))
    r = W.a.put(f"/intake/{W.draft}/checked", json={"set": {"cover_page.general_condition": {}}})
    check(r.status_code == 200, "an admin may too", brief(r))
    r = W.s.put(f"/intake/{W.draft}/checked", json={"set": {"cover_page.general_condition": {}}})
    check(r.status_code == 403, "\"see all\" staff only look at someone's draft: 403", brief(r))
    check(W.s.get(f"/intake/{W.draft}").status_code == 200, "...and may read it")
    r = W.b.put(f"/intake/{W.draft}/checked", json={"set": {"location": {}}})
    check(r.status_code == 404, "a stranger: 404", brief(r))
    db = SessionLocal()
    try:
        inbox = Report(channel="telegram_public", reporter_chat_id="public", status="intake", owner_id=None, data=blank(), photo_paths=[])
        db.add(inbox)
        db.flush()
        db.add(IntakeSession(report_id=inbox.id, stage="uploading"))
        db.commit()
        inbox_id = inbox.id
    finally:
        db.close()
    for who, c in (("an admin", W.a), ("\"see all\" staff", W.s)):
        r = c.put(f"/intake/{inbox_id}/checked", json={"set": {"location": {}}})
        check(r.status_code in (403, 404), f"a public report in the inbox: {who} can't ({r.status_code})", brief(r))
    check(not review.checked_of(stored(inbox_id).data), "...and it carries no marks")


def wizard_saves() -> None:
    before = W.o.get(f"/intake/{W.draft}").json()["data"]
    kept = before["checked"]["cover_page.modification"]
    data = dict(before)
    data["checked"] = {
        "cover_page.modification": {"by": "Someone Else", "by_id": "x", "at": "1999-01-01T00:00:00+00:00"},
        "location": {"na": True, "by": "Forged", "at": "1999-01-01"},
        "vehicle_info.no_such": {},
    }
    data["template_fields"] = ["location"]
    r = W.o.put(f"/intake/{W.draft}/data", json={"data": data})
    check(r.status_code == 200, "autosave with data.checked", brief(r))
    after = stored(W.draft).data
    check(after["checked"]["cover_page.modification"] == kept, "a mark already stored stays as it was (who, when)",
          repr(after["checked"]["cover_page.modification"]))
    loc = after["checked"].get("location") or {}
    check(loc.get("by_id") == W.owner and loc.get("na") is True and not loc.get("at", "").startswith("1999"),
          "a new mark is the signed-in person's, now -- never the browser's by/at", repr(loc))
    check("vehicle_info.no_such" not in after["checked"] and "cover_page.general_condition" not in after["checked"],
          "unknown keys dropped; a key not sent is unconfirmed")
    check(after["template_fields"] == before["template_fields"], "template_fields stays the server's", repr(after["template_fields"]))
    no_marks = {k: v for k, v in data.items() if k != "checked"}
    W.o.put(f"/intake/{W.draft}/data", json={"data": no_marks})
    check(stored(W.draft).data["checked"] == after["checked"], "a save that sends no checked keeps the stored marks")

    # Finalize (after an AI reading): the marks and the template list go
    # into the case; an empty template field is filled again, a typed one
    # kept.
    db = SessionLocal()
    try:
        db.get(IntakeSession, W.draft).ai_draft = ai_draft()
        db.commit()
    finally:
        db.close()
    marks = stored(W.draft).data["checked"]
    final = dict(W.o.get(f"/intake/{W.draft}").json()["data"])
    final = with_values(final, {"cover_page.handbrake_condition": "", "vehicle_info.paintwork_condition": "Resprayed"})
    r = W.o.post(f"/intake/{W.draft}/finalize", json={"data": final})
    check(r.status_code == 200, "finalize", brief(r))
    case = stored(W.draft)
    check(case.status == "confirmed" and case.data["checked"] == marks and "location" in marks, "the case keeps data.checked",
          repr(case.data.get("checked")))
    check(case.data["template_fields"] == before["template_fields"], "...and template_fields")
    check(review.value_at(case.data, "cover_page.handbrake_condition") == "see annex(es)",
          "filing fills a template field still empty")
    check(review.value_at(case.data, "vehicle_info.paintwork_condition") == "Resprayed", "...and never overwrites one")
    rv = W.o.get(f"/reports/{W.draft}").json()["review"]
    check(state(rv, "vehicle_info.paintwork_condition") == "you" and state(rv, "cover_page.handbrake_condition") == "check",
          "the case's review: changed wording you, usual wording check")


def redraft() -> None:
    previous = ai_draft()
    current = review.apply_template_defaults(ai_draft())
    current = with_values(current, {"vehicle_info.color": "Colourtwo", "location": "Street One"})
    current["checked"] = {
        "location": {"by": "Una", "by_id": "u1", "at": "x"},
        "police_report.reported_to_police": {"by": "Una", "by_id": "u1", "at": "x"},
        "vehicle_info.vin": {"by": "Una", "by_id": "u1", "at": "x", "na": True},
    }
    new_ai = with_values(ai_draft(), {
        "location": "Street Two", "vehicle_info.color": "Colourthree", "police_report.reported_to_police": False,
        "vehicle_info.vin": "TESTVIN0", "vehicle_info.make": "Maketwo",
    })
    new_ai["checked"] = None
    new_ai["template_fields"] = None
    merged = merge_redraft(current, previous, new_ai)
    check(merged["checked"] == current["checked"] and merged["template_fields"] == current["template_fields"],
          "a re-read keeps data.checked and template_fields")
    check(merged["location"] == "Street One", "a confirmed field the AI read the same last time keeps its value", repr(merged["location"]))
    check(merged["police_report"]["reported_to_police"] is True, "a confirmed tick stays")
    check(merged["vehicle_info"]["vin"] is None, "a field marked N/A stays blank whatever the AI reads now")
    check(merged["vehicle_info"]["make"] == "Maketwo", "an unconfirmed field the person never changed takes the new reading")
    check(merged["vehicle_info"]["color"] == "Colourtwo", "a person's edit stays (as before)")
    check(new_ai["vehicle_info"]["vin"] == "TESTVIN0" and new_ai["location"] == "Street Two",
          "the AI's own draft is left as it said")
    plain = merge_redraft({**current, "checked": None}, previous, new_ai)
    check(plain["location"] == "Street Two" and plain["vehicle_info"]["vin"] == "TESTVIN0",
          "without the marks the same re-read would have changed both")
    check(merged["cover_page"]["general_condition"] == "Good", "the usual wording stays")


def ai_read() -> None:
    read, photos = ai_read_for(ai_draft(), PHOTOS)
    check(read["insurance_details"] == {"insurer_name": "Insurer One", "claim_type": "Own damage"},
          "ai_read.insurance_details: the AI's guesses", repr(read["insurance_details"]))
    check(read["police_report"] == {"reported_to_police": True} and read["location"] == "Street One" and read["accident_type"] is None,
          "...police, location; accident type None when the AI left it empty", repr(read))
    check(read["vehicle_info"]["tyres"]["front_nearside"]["brand"] == "Brandone", "...the tyre table")
    check(photos == {"plate_number": "P02", "make": "P02", "model": "P02", "color": "P02", "odometer_reading": "P03"},
          "photos only for what a document photo states", repr(photos))
    check(ai_read_for(None, PHOTOS) == (None, {}), "no AI draft: (None, {})")
    bare, _ = ai_read_for(blank(), None)
    check(bare["insurance_details"] == {} and bare["police_report"] == {} and bare["vehicle_info"] == {},
          "an empty AI draft: nothing read (an unticked police box is not a reading)", repr(bare))
    detail = W.o.get(f"/reports/{W.case}").json()
    check(detail["ai_read"]["insurance_details"]["insurer_name"] == "Insurer One", "GET /reports/{id}: the widened ai_read")


def list_counts() -> None:
    rows = {row["id"]: row for row in W.o.get("/reports").json()}
    case = rows[W.case]
    rv = W.o.get(f"/reports/{W.case}").json()["review"]
    needs, check_ = review.open_items(rv["counts"])
    check(case["needs_you"] == needs and case["to_check"] == check_ and needs + check_ == rv["open"],
          f"GET /reports: needs_you {case['needs_you']}, to_check {case['to_check']} as the case's review", repr(case))
    draft = W.o.post("/intake").json()["id"]
    rows = {row["id"]: row for row in W.o.get("/reports").json()}
    check(rows[draft]["needs_you"] == 0 and rows[draft]["to_check"] == 0, "a draft: 0 and 0 (the wizard shows its own)")
    signed = new_case(W.owner, {**blank(), "sign_off": {"status": "Signed Off"}}, ai_draft(), status="Signed Off")
    rows = {row["id"]: row for row in W.o.get("/reports").json()}
    check(rows[signed]["needs_you"] == 0 and rows[signed]["to_check"] == 0, "a signed-off case: 0 and 0")
    odd = new_case(W.owner, {**blank(), "annex_a": ["not", "a", "table"], "damage_summary": "junk", "checked": "junk"}, ai_draft())
    rows = {row["id"]: row for row in W.o.get("/reports").json()}
    check(odd in rows and isinstance(rows[odd]["needs_you"], int), "odd stored data: the list still answers")

    def statements() -> list[str]:
        STATEMENTS.clear()
        assert W.o.get("/reports").status_code == 200
        return [s for s in STATEMENTS if "auth_sessions" not in s]

    small = len(statements())
    for _ in range(4):
        new_case(W.owner, review.apply_template_defaults(blank()), ai_draft())
    check(len(statements()) == small, f"no extra query per case ({small} for the list either way)")


def sign_off() -> None:
    case = new_case(W.owner, review.apply_template_defaults(blank()), ai_draft())
    r = W.a.post(f"/reports/{case}/sign-off", json={})
    check(r.status_code == 400 and r.json().get("code") == "declaration_required", "no declaration: 400 declaration_required", brief(r))
    r = W.a.post(f"/reports/{case}/sign-off", json={"terms_accepted": True})
    body = r.json()
    check(r.status_code == 409 and body.get("code") == "open_items", "open items, no ack: 409 open_items", brief(r))
    rv = W.a.get(f"/reports/{case}").json()["review"]
    check(body.get("open") == rv["open"] > 0 and body.get("counts") == rv["counts"] and "still open" in body.get("detail", ""),
          f"...with open ({body.get('open')}), the counts and a plain detail", repr({k: body.get(k) for k in ("open", "detail")}))
    check(stored(case).status == "confirmed" and not any(a == "signed_off" for a, _ in history(case)), "...and nothing is signed")
    r = W.a.post(f"/reports/{case}/sign-off", json={"terms_accepted": True, "open_items_ack": True})
    check(r.status_code == 200 and stored(case).status == "Signed Off", "with open_items_ack: signed off", brief(r))
    entry = next((d for a, d in history(case) if a == "signed_off"), "")
    check(f"{rv['open']} items open: cover" in entry and "Annex C" in entry, "the history entry says what was open", repr(entry))
    check(stored(case).data["sign_off"]["terms_accepted"] is True, "the declaration is recorded")
    rv = W.a.get(f"/reports/{case}").json()["review"]
    check(rv["open"] == 0, "signed off: nothing open")

    # Everything confirmed: no ack needed.
    done = new_case(W.owner, blank(), ai_draft())
    keys = {k: {} for k in review.CHECKABLE_KEYS if k != "annex_b"}
    keys["annex_b"] = {"na": True}
    r = W.o.put(f"/reports/{done}/checked", json={"set": keys})
    check(r.status_code == 200 and r.json()["review"]["open"] == 0, "everything confirmed: open 0", brief(r))
    r = W.a.post(f"/reports/{done}/sign-off", json={"terms_accepted": True})
    check(r.status_code == 200, "nothing open: signs off without the ack", brief(r))
    entry = next((d for a, d in history(done) if a == "signed_off"), "")
    check(entry.endswith("; no items open"), "...history: no items open", repr(entry))


def no_ai() -> None:
    check(not GEMINI, "no Gemini call anywhere in this test", repr(GEMINI))


def main_() -> int:
    for name, fn in (
        ("setup", setup),
        ("FIELD_RULES", rules),
        ("each state, the counts and open", states),
        ("the firm's usual wording", template_defaults),
        ("PUT /reports/{id}/checked and PUT /intake/{id}/checked", checked_endpoints),
        ("the wizard's autosave and finalize keep the marks", wizard_saves),
        ("merge_redraft keeps the marks", redraft),
        ("ai_read: the AI's guesses too", ai_read),
        ("GET /reports: needs_you and to_check", list_counts),
        ("sign-off: the declaration and open items", sign_off),
        ("no AI", no_ai),
    ):
        run_section(name, fn)
    print(f"\ntest_field_review: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        for section, label in FAILURES:
            print(f"  FAIL [{section}] {label}")
        return 1
    print("\nALL FIELD-REVIEW CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
