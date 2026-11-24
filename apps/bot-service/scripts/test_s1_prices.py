"""Stage 1: "My prices" and price safety (owner approved 24 Nov 2026;
plans/2026-11-23-report-and-parts/S1-contract.md, app/parts/prices.py,
app/api/prices.py).

    python scripts/test_s1_prices.py

Checked:
  - the database: user_prices / book_prices made, past_annex_lines.origin
    and .confirmed added; the one-time steps price_book_origin_v1 (imports
    "import" + confirmed, learned lines "carlink" and unconfirmed) and
    price_book_plates_v1 (plates blanked in both past tables, one history
    entry with the count, no plate in it or in the log, idempotent); the
    importer stores no plate and GET /parts-history shows none;
  - the price choice at filing and on "Fill prices": a person's own price
    ("mine"), then an approved one, then the book's median of 2+ confirmed
    cases ("book"), else none -- and who gets which step (price-book staff
    all three; anyone else their own only; nobody's own prices in someone
    else's case they may not see); side or side-less own prices; the line
    fields (price_source, price_confirmed, book_price, price_date,
    price_cases);
  - the 2-case rule (model and make fallback), DB4 (an unconfirmed
    learned line never prices), learning only confirmed prices;
  - remember on save (remembered in the answer), Undo, PATCH, DELETE
    (history entry with the old price), GET /me/prices filters and "old",
    own rows only; a failure to remember never fails the save;
  - Confirm all prices; the per-line confirm through the save;
  - the sign-off's 409 prices_unconfirmed, and one 409 with must-haves;
    old cases (lines without the fields) never blocked;
  - Price differences: the grouping (10%, approved or 2-case median),
    keep / update decisions, admins only, no names;
  - labour: another make's jobs unpriced;
  - "Ask the AI": the reading's chain from settings, no fixed strong model
    (the AI client stubbed).

Offline and self-contained: a temporary database, the PDF render
captured, Gemini refused. Every make, model, plate, person and price is
made up.
"""
import contextlib
import io
import logging
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
_tmp = Path(tempfile.mkdtemp(prefix="s1-prices-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
os.environ["STORAGE_DIR"] = str(STORAGE)
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaSyFakeCompanyKeyForS1PricesTest001"
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

import app.api.intake as intake_api  # noqa: E402
import app.api.main as main  # noqa: E402
import app.api.parts as parts_api  # noqa: E402
from app.auth.core import SESSION_COOKIE, create_session  # noqa: E402
from app.config import settings  # noqa: E402
from app.parts import learn, prices  # noqa: E402
from app.parts.history import PastCase, PastLabour, PastLine, import_case, suggest_labour  # noqa: E402
from app.parts.prefill import sync_annex_a  # noqa: E402
from app.reports import db as reports_db  # noqa: E402
from app.reports import review  # noqa: E402
from app.reports.db import SessionLocal, engine, init_db  # noqa: E402
from app.reports.filing import file_case_data  # noqa: E402
from app.reports.models import (  # noqa: E402
    AppSetting, AuditEntry, BookPrice, PastAnnexLine, PastLabourLine, Report, User, UserPrice, gen_id,
)
from app.reports.schema import SecurityIncidentDraft  # noqa: E402


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
    return f"{r.status_code} {r.text[:500]}"


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


def user_row(user_id: str) -> User:
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        db.expunge(u)
        return u
    finally:
        db.close()


# The must-have facts (review.MUST_HAVE), made up.
FILLED = {
    "report_id": "TEST REF 0101",
    "cover_page.date_of_request": "1st November 2026",
    "cover_page.requested_by": "Requester One",
    "insurance_details.claim_type": "Third party",
    "insurance_details.workshop_assigned": "Workshop One",
    "cover_page.date_of_survey": "3rd November 2026",
    "vehicle_info.plate_number": "TEST 0101",
    "vehicle_info.point_of_impact": "Rear centre",
}
SIGN = {"terms_accepted": True, "open_items_ack": True}


def case_data(make: str, model: str, parts=("Rear Bumper",), filled: bool = False) -> dict:
    data = SecurityIncidentDraft(description="Made-up test case.", category=["Vehicle Collision or Damage"]).model_dump()
    data["vehicle_info"] = {**(data.get("vehicle_info") or {}), "make": make, "model": model}
    data["damage_summary"] = [{"part": p, "condition": "Torn/cracked", "remarks": "Replace", "human_verified": True}
                              for p in parts]
    data["damaged_parts"] = list(parts)
    if filled:
        for key, value in FILLED.items():
            data = review.with_value(data, key, value)
    return data


def new_case(owner_id: str | None, data: dict, status: str = "confirmed") -> str:
    db = SessionLocal()
    try:
        r = Report(channel="manual", reporter_chat_id="dashboard", status=status, owner_id=owner_id,
                   data=data, photo_paths=[], ai_draft={})
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


def rows_of(report_id: str) -> list[dict]:
    return list(((stored(report_id).data or {}).get("annex_a") or {}).get("items") or [])


def row_named(rows: list[dict], name: str) -> dict:
    return next((r for r in rows if r.get("part_name") == name), {})


def _line(n, item, est, qty=1.0, condition="", section="list", confirmed=True) -> PastLine:
    disc = None if section == "nett" else 0.2
    adjusted = est if section == "nett" else round(est * 0.8, 2)
    return PastLine(line_no=n, section=section, item=item, condition=condition, remarks="", qty=qty, qty_raw=f"{qty:g}",
                    workshop_est=est, discount=disc, adjusted_cost=adjusted, confirmed=confirmed)


def book(ref: str, make: str, model: str, surveyed: str, lines: list[PastLine], labour=()) -> None:
    db = SessionLocal()
    try:
        case = PastCase(ref=ref, plate="TEST PL8", make=make, model=model, make_model_raw=f"{make} {model}",
                        registered=None, surveyed=surveyed, lines=list(lines), labour=list(labour))
        import_case(db, case, f"{ref}.xlsx")
        db.commit()
    finally:
        db.close()


def history(report_id: str | None = None, action: str | None = None) -> list[AuditEntry]:
    db = SessionLocal()
    try:
        q = db.query(AuditEntry)
        if report_id is not None:
            q = q.filter(AuditEntry.report_id == report_id)
        if action is not None:
            q = q.filter(AuditEntry.action == action)
        out = q.order_by(AuditEntry.id).all()
        for e in out:
            db.expunge(e)
        return out
    finally:
        db.close()


def save_rows(client: TestClient, report_id: str, rows: list[dict]):
    return client.put(f"/reports/{report_id}/annex-a", json={"items": rows})


# ---------------------------------------------------------------- sections


def setup() -> None:
    init_db()
    W.admin = new_user("Admin", role="admin")
    W.staff = new_user("Stella", sees_all_cases=True, may_sign_off=True)
    W.staff2 = new_user("Sam", sees_all_cases=True, may_sign_off=True)
    W.plain = new_user("Una")
    W.plain2 = new_user("Ulf")
    W.a, W.s, W.s2, W.p, W.p2 = (signed_in(u) for u in (W.admin, W.staff, W.staff2, W.plain, W.plain2))
    check(W.a.get("/auth/me").status_code == 200, "signed in")
    # The book for Testmotor Alpha: two cases. The bumper in both (550 the
    # median unit W/shop Est.), its clips in one only, the tail lamps in
    # both, each side in its own wording.
    book("CL 90001", "Testmotor", "Alpha", "2026-01-10", [
        _line(1, "Rear bumper fascia", 500),
        _line(2, "Rear bumper clips", 50, qty=10, condition="Necessary"),
        _line(3, "Tail lamp LH", 300),
    ])
    book("CL 90002", "Testmotor", "Alpha 1.5", "2026-06-20", [
        _line(1, "Rear bumper fascia", 600),
        _line(2, "Tail lamp LH", 320),
        _line(3, "Tail lamp RH", 320),
    ])


def database() -> None:
    names = set(inspect(engine).get_table_names())
    check({"user_prices", "book_prices"} <= names, "user_prices and book_prices made by create_all")
    cols = {c["name"] for c in inspect(engine).get_columns("past_annex_lines")}
    check({"origin", "confirmed"} <= cols, "past_annex_lines gained origin and confirmed")
    with engine.connect() as conn:
        idx = [r[1] for r in conn.execute(text("PRAGMA index_list(user_prices)"))]
    check("ux_user_prices_key" in idx, "the unique key of user_prices (side-less lines too)", repr(idx))
    db = SessionLocal()
    try:
        marks = {k for (k,) in db.query(AppSetting.key).filter(AppSetting.key.like("upgrade:price_book%"))}
        check(marks == {"upgrade:price_book_origin_v1", "upgrade:price_book_plates_v1"}, "both one-time steps ran", repr(marks))
        # A side-less price twice for one person: refused by the index.
        now = datetime.now(timezone.utc)
        for _ in range(2):
            db.add(UserPrice(user_id="u-test", make="Idxmake", model_key="m", item_key="rear bumper", side=None,
                             wording="Rear bumper", unit_price=1.0, uses=1, created_at=now, updated_at=now))
        try:
            db.commit()
            check(False, "a second side-less row of one key is refused")
        except Exception:
            db.rollback()
            check(True, "a second side-less row of one key is refused")
        # The importer: no plate, origin import, confirmed.
        lines = db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == "CL 90001").all()
        check(lines and all(ln.plate == "" for ln in lines), "the importer stores no plate (the case had one)")
        check(all(ln.origin == "import" and ln.confirmed is True for ln in lines), "...origin import, confirmed")
    finally:
        db.close()
    r = W.a.get("/parts-history")
    cases = r.json().get("cases", []) if r.status_code == 200 else []
    check(r.status_code == 200 and cases and all(c.get("plate") == "" for c in cases),
          "GET /parts-history: plate always empty, admins too", brief(r))


def _rerun_step(name: str, step) -> None:
    """The step again, as on a database it never ran on: its mark removed."""
    db = SessionLocal()
    try:
        db.query(AppSetting).filter(AppSetting.key == f"upgrade:{name}").delete()
        db.commit()
    finally:
        db.close()
    reports_db._once(name, step)


def one_time_steps() -> None:
    plate_a, plate_b, plate_c = "TEST 1111 A", "TEST 2222 B", "TEST 3333 C"
    db = SessionLocal()
    try:
        # As a database from before Stage 1 has them: plates, no origin.
        db.add_all([
            PastAnnexLine(case_ref="CL 91001", plate=plate_a, make="Platemake", model="Pmodel", item="Rear bumper fascia",
                          item_key="rear bumper", workshop_est=100, adjusted_cost=80, origin=None, confirmed=None),
            PastAnnexLine(case_ref="CL 91001", plate=plate_a, make="Platemake", model="Pmodel", item="Tail lamp LH",
                          item_key="tail lamp", workshop_est=100, adjusted_cost=80, origin=None, confirmed=None),
            PastAnnexLine(case_ref="carlink:oldcase0001", plate="", make="Platemake", model="Pmodel", item="Rear bumper fascia",
                          item_key="rear bumper", workshop_est=100, adjusted_cost=80, origin=None, confirmed=None),
            PastLabourLine(case_ref="CL 91001", plate=plate_b, make="Platemake", model="Pmodel", description="To respray",
                           desc_key="respray", workshop_est=10, adjusted_cost=8),
            PastLabourLine(case_ref="CL 91002", plate=plate_c, make="Platemake", model="Pmodel", description="To refit",
                           desc_key="refit", workshop_est=10, adjusted_cost=8),
        ])
        db.commit()
    finally:
        db.close()

    _rerun_step("price_book_origin_v1", prices.mark_past_line_origins)
    db = SessionLocal()
    try:
        imp = db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == "CL 91001").all()
        old = db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == "carlink:oldcase0001").one()
        check(all(ln.origin == "import" and ln.confirmed is True for ln in imp), "origin step: an import is 'import', confirmed")
        check(old.origin == "carlink" and old.confirmed is None, "...a line learned before is 'carlink' and stays unconfirmed",
              repr((old.origin, old.confirmed)))
        untouched = db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == "CL 90001").all()
        check(all(ln.origin == "import" for ln in untouched), "...lines that had an origin keep it")
    finally:
        db.close()

    before = len(history(action="price_book_plates_dropped"))
    captured = io.StringIO()
    handler = logging.StreamHandler(captured)
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            _rerun_step("price_book_plates_v1", prices.drop_price_book_plates)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    db = SessionLocal()
    try:
        left = (db.query(PastAnnexLine).filter(PastAnnexLine.plate != "").count()
                + db.query(PastLabourLine).filter(PastLabourLine.plate != "").count())
    finally:
        db.close()
    check(left == 0, "plate step: no plate left in either past table", str(left))
    entries = history(action="price_book_plates_dropped")
    check(len(entries) == before + 1, "...one history entry", repr([e.detail for e in entries]))
    detail = entries[-1].detail if entries else ""
    check("4 line(s)" in detail and "2 part" in detail and "2 labour" in detail, "...with the count", detail)
    seen = detail + captured.getvalue() + out.getvalue()
    check(not any(p in seen for p in (plate_a, plate_b, plate_c, "TEST PL8")), "...and no plate in it or in the log")
    reports_db._once("price_book_plates_v1", prices.drop_price_book_plates)
    check(len(history(action="price_book_plates_dropped")) == before + 1, "...done once: a second start-up changes nothing")
    db = SessionLocal()
    try:
        check(prices.drop_price_book_plates(db) == 0, "...and run by hand again it finds nothing to blank")
        db.rollback()
    finally:
        db.close()


def _fill(client: TestClient, report_id: str):
    return client.post(f"/reports/{report_id}/annex-a/prefill")


def price_choice() -> None:
    # Step 3: the book's median of 2+ confirmed cases, for price-book staff.
    case = new_case(W.staff, case_data("Testmotor", "Alpha"))
    r = _fill(W.s, case)
    check(r.status_code == 200, "staff: Fill prices", brief(r))
    rows = rows_of(case)
    bumper, clips = row_named(rows, "Rear bumper fascia"), row_named(rows, "Rear bumper clips")
    check(bumper.get("workshop_est") == 550.0 and bumper.get("price_source") == "book"
          and bumper.get("price_confirmed") is False and bumper.get("price_cases") == 2
          and bumper.get("book_price") == 550.0 and bumper.get("price_date") == "2026-06-20",
          "book: median of 2 cases, waiting, with its cases and newest date", repr(bumper))
    check(clips and clips.get("workshop_est") == 0 and clips.get("price_source") is None,
          "one case only: the line is listed, unpriced (the 2-case rule)", repr(clips))

    # Step 1: the person's own price wins, the book beside it.
    db = SessionLocal()
    try:
        prices.remember(db, W.staff, {"make": "Testmotor", "model": "Alpha"}, None, [("Rear bumper fascia", 450, 1)])
        prices.remember(db, W.plain, {"make": "TESTMOTOR", "model": "alpha"}, None, [("Rear bumper fascia", 400, 1)])
        db.commit()
    finally:
        db.close()
    case = new_case(W.staff, case_data("Testmotor", "Alpha 1.5 RS"))
    _fill(W.s, case)
    bumper = row_named(rows_of(case), "Rear bumper fascia")
    check(bumper.get("workshop_est") == 450.0 and bumper.get("price_source") == "mine"
          and bumper.get("price_confirmed") is False and bumper.get("book_price") == 550.0
          and bumper.get("price_date") == datetime.now(timezone.utc).date().isoformat() and bumper.get("prefilled") is True,
          "mine: their own price first, the book's beside it, dated, waiting", repr(bumper))

    # Anyone else: their own price, never the book's.
    case = new_case(W.plain, case_data("Testmotor", "Alpha"))
    r = _fill(W.p, case)
    rows = rows_of(case)
    bumper = row_named(rows, "Rear bumper fascia")
    check(r.status_code == 200 and bumper.get("workshop_est") == 400.0 and bumper.get("price_source") == "mine"
          and bumper.get("book_price") is None,
          "a self-signup: Fill prices now 200, their own price, no book figure", brief(r) + repr(bumper))
    check(not any(row.get("price_source") in ("book", "approved") for row in rows) and not row_named(rows, "Rear bumper clips"),
          "...nothing from the book (no kit lines, no book prices)", repr([(x["part_name"], x.get("price_source")) for x in rows]))

    # At filing, the same choice (file_case_data).
    db = SessionLocal()
    try:
        filed = file_case_data(db, case_data("Testmotor", "Alpha"), user_row(W.plain), ai_draft=None, photo_kinds=None)
        mine = row_named(filed["annex_a"]["items"], "Rear bumper fascia")
        check(mine.get("price_source") == "mine" and mine.get("workshop_est") == 400.0, "filing: a self-signup's own price", repr(mine))
        filed = file_case_data(db, case_data("Testmotor", "Alpha"), user_row(W.staff2), ai_draft=None, photo_kinds=None)
        b = row_named(filed["annex_a"]["items"], "Rear bumper fascia")
        check(b.get("price_source") == "book" and b.get("workshop_est") == 550.0, "filing: staff with no own price: the book", repr(b))
        # An admin finishing a self-signup's draft: neither the admin's own
        # prices nor the book go into that case.
        prices.remember(db, W.admin, {"make": "Testmotor", "model": "Alpha"}, None, [("Rear bumper fascia", 999, 1)])
        db.commit()
        filed = file_case_data(db, case_data("Testmotor", "Alpha"), user_row(W.admin), ai_draft=None, photo_kinds=None,
                               owner=user_row(W.plain))
        b = row_named(filed["annex_a"]["items"], "Rear bumper fascia")
        check(b.get("workshop_est") == 0 and b.get("price_source") is None,
              "filing for someone else who can't see the book: nobody's prices", repr(b))
    finally:
        db.close()

    # Someone else's prices never: the other self-signup gets none of Una's.
    case = new_case(W.plain2, case_data("Testmotor", "Alpha"))
    _fill(W.p2, case)
    check(row_named(rows_of(case), "Rear bumper fascia").get("workshop_est") == 0, "another person's own prices: never")
    # Unknown model: nothing looked up, nothing remembered.
    case = new_case(W.plain, case_data("Testmotor", ""))
    _fill(W.p, case)
    check(all(not x.get("price_source") for x in rows_of(case)), "no model on the case: no own price looked up")

    # Sides: an own price for the left lamp, and a side-less one.
    db = SessionLocal()
    try:
        prices.remember(db, W.plain, {"make": "Testmotor", "model": "Alpha"}, None, [("Tail lamp LH", 210, 1)])
        db.commit()
    finally:
        db.close()
    case = new_case(W.plain, case_data("Testmotor", "Alpha", parts=("Left Tail Lamp", "Right Tail Lamp")))
    _fill(W.p, case)
    rows = rows_of(case)
    left, right = row_named(rows, "Tail lamp LH"), row_named(rows, "Tail lamp RH")
    check(left.get("price_source") == "mine" and left.get("workshop_est") == 210.0, "own LH price on the LH lamp", repr(left))
    check(right and right.get("workshop_est") == 0, "...not on the RH lamp", repr(right))
    db = SessionLocal()
    try:
        prices.remember(db, W.plain, {"make": "Testmotor", "model": "Alpha"}, None, [("Tail lamp", 200, 1)])
        db.commit()
    finally:
        db.close()
    case = new_case(W.plain, case_data("Testmotor", "Alpha", parts=("Left Tail Lamp", "Right Tail Lamp")))
    _fill(W.p, case)
    rows = rows_of(case)
    check(row_named(rows, "Tail lamp LH").get("workshop_est") == 210.0 and row_named(rows, "Tail lamp RH").get("workshop_est") == 200.0,
          "a side-less own price for a side with none of its own", repr([(x["part_name"], x["workshop_est"]) for x in rows]))

    # Step 2: an approved price, before the book (staff only).
    book("CL 92001", "Apprmotor", "Zeta", "2026-02-01", [_line(1, "Rear bumper fascia", 700)])
    book("CL 92002", "Apprmotor", "Zeta", "2026-03-01", [_line(1, "Rear bumper fascia", 800)])
    r = W.a.post("/admin/price-differences/decide", json={"make": "Apprmotor", "model_key": "zeta", "item_key": "rear bumper",
                                                           "decision": "update", "unit_price": 720, "reason": "new list price"})
    check(r.status_code == 200 and r.json() == {"ok": True}, "admin approves 720 for Apprmotor Zeta's bumper", brief(r))
    case = new_case(W.staff, case_data("Apprmotor", "Zeta"))
    _fill(W.s, case)
    b = row_named(rows_of(case), "Rear bumper fascia")
    check(b.get("price_source") == "approved" and b.get("workshop_est") == 720.0 and b.get("book_price") == 720.0
          and b.get("price_confirmed") is False and b.get("price_date") == datetime.now(timezone.utc).date().isoformat(),
          "approved: before the book's 750", repr(b))
    db = SessionLocal()
    try:
        prices.remember(db, W.staff, {"make": "Apprmotor", "model": "Zeta"}, None, [("Rear bumper fascia", 690, 1)])
        db.commit()
    finally:
        db.close()
    case = new_case(W.staff, case_data("Apprmotor", "Zeta"))
    _fill(W.s, case)
    b = row_named(rows_of(case), "Rear bumper fascia")
    check(b.get("price_source") == "mine" and b.get("workshop_est") == 690.0 and b.get("book_price") == 720.0,
          "mine before approved, the approved price beside it", repr(b))
    case = new_case(W.plain, case_data("Apprmotor", "Zeta"))
    _fill(W.p, case)
    check(row_named(rows_of(case), "Rear bumper fascia").get("workshop_est") == 0, "a self-signup never gets an approved price")
    # An approved price decided after a case was filed is not the machine's
    # Annex A then (history.restricted, the AI accuracy record).
    from app.parts.history import restricted
    db = SessionLocal()
    try:
        with restricted(imported_before=datetime.now(timezone.utc) - timedelta(days=1)):
            out = sync_annex_a(db, case_data("Apprmotor", "Zeta"), price_book=True)
        b = row_named(out["annex_a"]["items"], "Rear bumper fascia")
        check(b.get("price_source") != "approved", "restricted view: no approval made after its cut-off", repr(b))
    finally:
        db.close()

    # A damage-list change on the case page: the same choice for the new rows.
    case = new_case(W.plain, case_data("Testmotor", "Alpha", parts=()))
    r = W.p.post(f"/reports/{case}/damage-items", json={"part": "Rear Bumper", "condition": "Torn/cracked"})
    b = row_named(r.json().get("annex_a", {}).get("items", []) if r.status_code == 200 else [], "Rear bumper fascia")
    check(r.status_code == 200 and b.get("price_source") == "mine", "adding a damaged part: their own price on its row",
          brief(r))


def two_cases_and_db4() -> None:
    # One imported case: never priced from it.
    book("CL 93001", "Dbfour", "Gamma", "2026-04-01", [_line(1, "Rear bumper fascia", 300)])
    case = new_case(W.staff, case_data("Dbfour", "Gamma"))
    _fill(W.s, case)
    check(row_named(rows_of(case), "Rear bumper fascia").get("workshop_est") == 0, "one past case: unpriced")
    # A signed-off Carlink case's line nobody confirmed (DB4): still one.
    learned = new_case(W.staff, case_data("Dbfour", "Gamma"), status="Signed Off")
    book(learn.case_ref(learned), "Dbfour", "Gamma", "2026-05-01", [_line(1, "Rear bumper fascia", 340, confirmed=False)])
    db = SessionLocal()
    try:
        ln = db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == learn.case_ref(learned)).one()
        check(ln.origin == "carlink" and ln.confirmed is False, "a learned line: origin carlink, unconfirmed")
    finally:
        db.close()
    case = new_case(W.staff, case_data("Dbfour", "Gamma"))
    _fill(W.s, case)
    check(row_named(rows_of(case), "Rear bumper fascia").get("workshop_est") == 0,
          "DB4: an unconfirmed learned line doesn't make the second case")
    r = W.a.get("/parts-history?make=Dbfour")
    line = next((x for x in r.json().get("lines", []) if x["item"] == "Rear bumper fascia"), {})
    check(line.get("usual_unit_price") == 240.0 and line.get("cases") == 2,
          "parts history: the unconfirmed price left out of the usual price (the line still counted)", repr(line))
    # Confirmed, it does.
    book(learn.case_ref(learned), "Dbfour", "Gamma", "2026-05-01", [_line(1, "Rear bumper fascia", 340, confirmed=True)])
    case = new_case(W.staff, case_data("Dbfour", "Gamma"))
    _fill(W.s, case)
    b = row_named(rows_of(case), "Rear bumper fascia")
    check(b.get("workshop_est") == 320.0 and b.get("price_cases") == 2 and b.get("price_source") == "book",
          "confirmed, the learned case is the second: priced (median 320)", repr(b))
    # The make fallback: another model of the make takes the make's lines,
    # priced only from 2+ cases there too.
    case = new_case(W.staff, case_data("Dbfour", "Omicron"))
    _fill(W.s, case)
    b = row_named(rows_of(case), "Rear bumper fascia")
    check(b.get("workshop_est") == 320.0 and b.get("price_cases") == 2, "make fallback with 2 cases: priced", repr(b))
    book("CL 93101", "Solomake", "Mu", "2026-04-01", [_line(1, "Rear bumper fascia", 300)])
    case = new_case(W.staff, case_data("Solomake", "Nu"))
    _fill(W.s, case)
    b = row_named(rows_of(case), "Rear bumper fascia")
    check(b and b.get("workshop_est") == 0 and b.get("price_source") is None, "make fallback with 1 case: unpriced", repr(b))


def learning() -> None:
    data = case_data("Learnmake", "Delta", filled=True)
    data["annex_a"] = {"items": [
        {"part_name": "Rear bumper fascia", "qty": 1, "workshop_est": 500, "discount_pct": 0.2, "origin": "typed",
         "price_source": "typed", "price_confirmed": True},
        {"part_name": "Rear bumper clips", "qty": 10, "workshop_est": 50, "discount_pct": 0.2, "origin": "history",
         "prefilled": True, "price_source": "book", "price_confirmed": False},
        {"part_name": "Tail lamp LH", "qty": 1, "workshop_est": 300, "discount_pct": 0.2, "origin": "history",
         "prefilled": True},
        {"part_name": "Tail lamp RH", "qty": 1, "workshop_est": 310, "discount_pct": 0.2, "origin": "history",
         "prefilled": False},
        {"part_name": "Rear bumper retainer", "qty": 1, "workshop_est": 40, "discount_pct": 0.2, "origin": "history",
         "prefilled": True, "price_source": "mine", "price_confirmed": True},
    ]}
    data["sign_off"] = {"status": "Signed Off", "signature_date": "2026-11-20T00:00:00+00:00"}
    rid = new_case(W.staff, data, status="Signed Off")
    db = SessionLocal()
    try:
        n = learn.learn_case(db, rid)
        lines = {ln.item: ln for ln in db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == learn.case_ref(rid))}
    finally:
        db.close()
    check(n == 5 and all(ln.origin == "carlink" for ln in lines.values()), "learned: every priced line, origin carlink",
          repr(n))
    want = {"Rear bumper fascia": True, "Rear bumper clips": False, "Tail lamp LH": False, "Tail lamp RH": True,
            "Rear bumper retainer": True}
    got = {k: v.confirmed for k, v in lines.items()}
    check(got == want, "confirmed: a typed or confirmed price yes; a waiting one, or an old prefilled one nobody touched, no",
          repr(got))


def _client_rows(rows: list[dict]) -> list[dict]:
    """The lines as the page sends them back."""
    return [dict(r) for r in rows]


def remember_undo_delete() -> None:
    case = new_case(W.staff2, case_data("Testmotor", "Alpha"))
    _fill(W.s2, case)
    rows = _client_rows(rows_of(case))
    bumper = row_named(rows, "Rear bumper fascia")
    check(bumper.get("price_source") == "book", "Sam's case: the book's bumper price", repr(bumper))
    bumper["workshop_est"] = 900.0
    bumper["qty"] = 2
    r = save_rows(W.s2, case, rows)
    body = r.json() if r.status_code == 200 else {}
    rem = body.get("remembered") or []
    check(r.status_code == 200 and len(rem) == 1 and rem[0]["unit_price"] == 450.0 and rem[0]["previous_unit_price"] is None
          and rem[0]["wording"] == "Rear bumper fascia" and rem[0]["make"] == "Testmotor" and rem[0]["model_key"] == "alpha"
          and set(rem[0]) == {"id", "wording", "make", "model_key", "unit_price", "previous_unit_price"},
          "save: the typed estimate remembered per unit (900 / 2)", brief(r))
    saved = row_named(body.get("annex_a", {}).get("items", []), "Rear bumper fascia")
    check(saved.get("price_source") == "typed" and saved.get("price_confirmed") is True and saved.get("book_price") == 550.0,
          "...the line: typed, confirmed, the book's figure kept for comparison", repr(saved))
    check(all(not x.get("price_source") or x["part_name"] == "Rear bumper fascia" for x in body.get("annex_a", {}).get("items", [])),
          "...other lines as they were")
    pid = rem[0]["id"] if rem else 0

    # Saved again unchanged: nothing remembered. Changed: the price before.
    rows = _client_rows(rows_of(case))
    r = save_rows(W.s2, case, rows)
    check(r.status_code == 200 and r.json().get("remembered") == [], "an unchanged save remembers nothing", brief(r))
    row_named(rows, "Rear bumper fascia")["workshop_est"] = 1000.0
    r = save_rows(W.s2, case, rows)
    rem = r.json().get("remembered") or []
    check(rem and rem[0]["id"] == pid and rem[0]["unit_price"] == 500.0 and rem[0]["previous_unit_price"] == 450.0,
          "changed again: the same row, the price before kept for Undo", brief(r))

    r = W.s2.post(f"/me/prices/{pid}/undo")
    lst = W.s2.get("/me/prices").json()["prices"]
    check(r.status_code == 200 and r.json() == {"ok": True} and lst and lst[0]["unit_price"] == 450.0,
          "Undo: the price before back", repr(lst))
    r = W.s2.post(f"/me/prices/{pid}/undo")
    lst = W.s2.get("/me/prices").json()["prices"]
    check(r.status_code == 200 and lst and lst[0]["unit_price"] == 450.0, "Undo again: nothing more to undo, the row stays")
    undone = history(action="my_price_undone")
    check(len(undone) == 1 and undone[0].user_id == W.staff2 and undone[0].detail.endswith("500.00 -> 450.00"),
          "...the Undo in the history once (the second did nothing)", repr([e.detail for e in undone]))

    # A new line typed: remembered as new; Undo removes it.
    rows = _client_rows(rows_of(case))
    rows.append({"part_name": "Rear bumper side bracket LH", "qty": 1, "workshop_est": 80, "origin": "typed"})
    r = save_rows(W.s2, case, rows)
    rem = r.json().get("remembered") or []
    new = next((x for x in rem if x["wording"] == "Rear bumper side bracket LH"), {})
    check(new.get("unit_price") == 80.0 and new.get("previous_unit_price") is None, "a new typed line: remembered as new", brief(r))
    r = W.s2.post(f"/me/prices/{new.get('id')}/undo")
    ids = [x["id"] for x in W.s2.get("/me/prices").json()["prices"]]
    check(r.status_code == 200 and new.get("id") not in ids, "Undo of a new price: removed")

    # Another person's price: 404 for all of it, never listed.
    for method, path, kw in (("patch", f"/me/prices/{pid}", {"json": {"unit_price": 1}}), ("delete", f"/me/prices/{pid}", {}),
                             ("post", f"/me/prices/{pid}/undo", {})):
        r = getattr(W.s, method)(path, **kw)
        check(r.status_code == 404 and r.json().get("code") == "price_not_found", f"someone else's price: {method.upper()} 404",
              brief(r))
    check(all(x["id"] != pid for x in W.s.get("/me/prices").json()["prices"]), "...and not in their list")

    # PATCH: the row back; a bad price 422.
    r = W.s2.patch(f"/me/prices/{pid}", json={"unit_price": 475.5})
    body = r.json() if r.status_code == 200 else {}
    check(r.status_code == 200 and body.get("unit_price") == 475.5 and body.get("id") == pid
          and set(body) == {"id", "make", "model_key", "wording", "side", "unit_price", "updated_at", "uses", "last_case_id", "old"},
          "PATCH: the row as listed", brief(r))
    check(W.s2.patch(f"/me/prices/{pid}", json={"unit_price": 0}).status_code == 422, "PATCH 0: 422")
    check(history(action="my_price_changed")[-1].detail.endswith("450.00 -> 475.50"), "...in the history")

    # GET filters and "old".
    db = SessionLocal()
    try:
        prices.remember(db, W.staff2, {"make": "Othermake", "model": "Theta"}, None, [("Front bumper fascia", 300, 1)])
        db.commit()
        old_id = db.query(UserPrice.id).filter(UserPrice.user_id == W.staff2, UserPrice.make == "Othermake").scalar()
        db.query(UserPrice).filter(UserPrice.id == old_id).update(
            {UserPrice.updated_at: datetime.now(timezone.utc) - timedelta(days=400)})
        db.commit()
    finally:
        db.close()
    every = W.s2.get("/me/prices").json()["prices"]
    check(len(every) == 2, "GET: Sam's own two", repr(every))
    check([x["make"] for x in W.s2.get("/me/prices?make=othermake").json()["prices"]] == ["Othermake"], "?make=, any case")
    check([x["model_key"] for x in W.s2.get("/me/prices?model=Alpha 1.5 RS").json()["prices"]] == ["alpha"], "?model= as typed")
    check([x["wording"] for x in W.s2.get("/me/prices?q=FRONT").json()["prices"]] == ["Front bumper fascia"], "?q= words")
    old = {x["id"]: x["old"] for x in every}
    check(old.get(old_id) is True and old.get(pid) is False, "old: over 12 months", repr(old))

    # DELETE: gone, with a history entry giving the old price.
    r = W.s2.delete(f"/me/prices/{old_id}")
    check(r.status_code == 200 and r.json() == {"ok": True}, "DELETE", brief(r))
    entry = history(action="my_price_deleted")[-1]
    check(entry.user_id == W.staff2 and entry.detail.endswith("was 300.00") and "Othermake theta" in entry.detail,
          "DB3: the history says who, when and the old price", entry.detail)
    check(all(x["id"] != old_id for x in W.s2.get("/me/prices").json()["prices"]), "...and it's gone")

    # Remembering fails: the save doesn't.
    real = prices.remember

    def broken(*a, **k):
        raise RuntimeError("remember broke")
    prices.remember = broken
    try:
        rows = _client_rows(rows_of(case))
        row_named(rows, "Rear bumper fascia")["workshop_est"] = 1234.0
        r = save_rows(W.s2, case, rows)
        check(r.status_code == 200 and r.json().get("remembered") == []
              and row_named(rows_of(case), "Rear bumper fascia").get("workshop_est") == 1234.0,
              "remembering fails: the save still saved, remembered []", brief(r))
    finally:
        prices.remember = real

    # No make or model on the case: nothing remembered.
    case = new_case(W.staff2, case_data("", "", parts=()))
    r = save_rows(W.s2, case, [{"part_name": "Rear bumper fascia", "qty": 1, "workshop_est": 100}])
    check(r.status_code == 200 and r.json().get("remembered") == [], "no make or model: nothing remembered", brief(r))


def confirm_and_sign_off() -> None:
    data = case_data("Testmotor", "Alpha")
    data["vehicle_info"]["model"] = "Alpha"
    case = new_case(W.staff2, data)
    _fill(W.s2, case)  # Sam's own 475.50 bumper, the book's lamp... waiting
    waiting = review.unconfirmed_prices(stored(case).data)
    check(waiting and waiting[0]["price_source"] == "mine" and set(waiting[0]) == {"index", "wording", "price_source", "unit_price"},
          "lines waiting for confirmation", repr(waiting))

    # Must-haves missing too: one 409 with both lists.
    r = W.s2.post(f"/reports/{case}/sign-off", json=SIGN)
    body = r.json()
    check(r.status_code == 409 and body.get("code") == "must_have" and body.get("missing") and body.get("lines") == waiting
          and "not confirmed yet" in body.get("detail", ""), "both problems: one 409, both listed", brief(r))

    for key, value in FILLED.items():
        data = review.with_value(stored(case).data, key, value)
        db = SessionLocal()
        try:
            db.get(Report, case).data = data
            db.commit()
        finally:
            db.close()
    r = W.s2.post(f"/reports/{case}/sign-off", json=SIGN)
    body = r.json()
    line = body.get("lines", [{}])[0] if body.get("lines") else {}
    check(r.status_code == 409 and body.get("code") == "prices_unconfirmed" and body.get("missing") == []
          and line.get("wording") == "Rear bumper fascia" and line.get("unit_price") == 475.5,
          "prices only: 409 prices_unconfirmed with the lines", brief(r))
    check(stored(case).status == "confirmed", "...nothing signed")
    rv = W.s2.get(f"/reports/{case}").json()["review"]
    idx = line.get("index")
    check(idx is not None and rv["annex_a"][idx] == "check", "review: the waiting line is 'check'")
    open_before, check_before = rv["open"], rv["counts"]["annex_a"]["check"]

    # Confirm all prices.
    check(W.p.post(f"/reports/{case}/annex-a/confirm-prices").status_code == 404, "confirm-prices: a stranger 404")
    r = W.s2.post(f"/reports/{case}/annex-a/confirm-prices")
    body = r.json() if r.status_code == 200 else {}
    items = (body.get("annex_a") or {}).get("items") or []
    check(r.status_code == 200 and items and all(x.get("price_confirmed") is True for x in items)
          and set(body) == {"annex_a", "annex_c", "confirmed"} and body["annex_c"].get("workshop_total") is not None
          and body.get("confirmed") == len(waiting),
          "confirm-prices: every line confirmed, {annex_a, annex_c} back as the save answers", brief(r))
    check(review.unconfirmed_prices(stored(case).data) == [], "...none waiting")
    entry = history(case, "annex_a_prices_confirmed")
    check(entry and entry[-1].detail.startswith(f"{len(waiting)} price"), "...a history entry", repr([e.detail for e in entry]))
    rv = W.s2.get(f"/reports/{case}").json()["review"]
    check(rv["annex_a"][idx] == "you", "review: a confirmed price is 'you'", repr(rv["annex_a"]))
    check(rv["counts"]["annex_a"]["check"] == check_before - len(waiting) and rv["open"] == open_before - len(waiting),
          "...each waiting price was one open item (the sign-off's 'N items open'), none now",
          repr((open_before, rv["open"], check_before, rv["counts"]["annex_a"]["check"])))
    r = W.s2.post(f"/reports/{case}/sign-off", json=SIGN)
    check(r.status_code == 200, "signs off once confirmed", brief(r))
    check(W.s2.post(f"/reports/{case}/annex-a/confirm-prices").status_code == 409, "confirm-prices on a signed-off case: 409")
    draft = new_case(W.staff2, case_data("Testmotor", "Alpha"), status="intake")
    check(W.s2.post(f"/reports/{draft}/annex-a/confirm-prices").status_code == 409, "...on an intake draft: 409")
    # The background learning step ran: Sam's confirmed own price learned.
    db = SessionLocal()
    try:
        lines = db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == learn.case_ref(case)).all()
        check(lines and all(ln.confirmed is True for ln in lines if ln.item == "Rear bumper fascia"),
              "learned at sign-off with confirmed prices", repr([(ln.item, ln.confirmed) for ln in lines]))
    finally:
        db.close()

    # The per-line Confirm (prefilled off) through the save confirms too;
    # a page can never un-confirm.
    case = new_case(W.staff2, case_data("Testmotor", "Alpha", filled=True))
    _fill(W.s2, case)
    rows = _client_rows(rows_of(case))
    target = next(x for x in rows if x.get("price_confirmed") is False)
    target["prefilled"], target["origin"] = False, "typed"
    r = save_rows(W.s2, case, rows)
    after = row_named(r.json()["annex_a"]["items"], target["part_name"])
    check(after.get("price_confirmed") is True and after.get("price_source") == target["price_source"]
          and r.json().get("remembered") == [], "the per-line Confirm: confirmed, its source kept, nothing remembered", repr(after))
    rows = _client_rows(rows_of(case))
    for x in rows:
        x["price_confirmed"] = False
        x["price_source"] = "typed"
    r = save_rows(W.s2, case, rows)
    after = row_named(r.json()["annex_a"]["items"], target["part_name"])
    check(after.get("price_confirmed") is True and after.get("price_source") == target["price_source"],
          "a page sending false / another source changes nothing", repr(after))
    # The dashboard's per-line [check] Confirm: price_confirmed true on a
    # line whose estimate didn't change (nothing else changed).
    case = new_case(W.staff2, case_data("Testmotor", "Alpha", filled=True))
    _fill(W.s2, case)
    rows = _client_rows(rows_of(case))
    line = row_named(rows, "Rear bumper fascia")
    check(line.get("price_confirmed") is False and line.get("price_source") == "mine", "(a waiting line)", repr(line))
    line["price_confirmed"] = True
    r = save_rows(W.s2, case, rows)
    after = row_named(r.json()["annex_a"]["items"], "Rear bumper fascia") if r.status_code == 200 else {}
    check(after.get("price_confirmed") is True and after.get("price_source") == "mine" and after.get("workshop_est") == 475.5
          and after.get("book_price") == line.get("book_price") and r.json().get("remembered") == [],
          "a page sending price_confirmed true on an unchanged line: stored, its source and figures kept", repr(after))
    check(review.unconfirmed_prices(stored(case).data) == [], "...nothing waiting")

    # Old cases: lines with no price fields are never held up.
    data = case_data("Testmotor", "Alpha", filled=True)
    data["annex_a"] = {"items": [
        {"part_name": "Rear bumper fascia", "qty": 1, "workshop_est": 550, "discount_pct": 0.2, "prefilled": True,
         "origin": "history", "suggested_from": "2 past cases (Testmotor Alpha)", "source_part": "Rear Bumper"},
    ]}
    old = new_case(W.staff2, data)
    check(review.unconfirmed_prices(stored(old).data) == [], "an old case: nothing waiting")
    rv = W.s2.get(f"/reports/{old}").json()["review"]
    check(rv["annex_a"] == ["check"] and rv["counts"]["annex_a"]["history_price"] == 1, "...its review as before ('check')")
    r = W.s2.post(f"/reports/{old}/sign-off", json=SIGN)
    check(r.status_code == 200, "...and it signs off (with the open-items tick, as before)", brief(r))


def differences() -> None:
    book("CL 94001", "Diffmotor", "Epsilon", "2026-01-01", [_line(1, "Rear bumper fascia", 500), _line(2, "Tail lamp LH", 200)])
    book("CL 94002", "Diffmotor", "Epsilon", "2026-02-01", [_line(1, "Rear bumper fascia", 520), _line(2, "Tail lamp LH", 210)])
    car = {"make": "Diffmotor", "model": "Epsilon"}
    db = SessionLocal()
    try:
        prices.remember(db, W.staff, car, "case-a", [("Rear bumper fascia", 700, 1)])   # 37% over the book's 510
        prices.remember(db, W.staff2, car, "case-b", [("Rear bumper fascia", 520, 1)])  # within 10%
        prices.remember(db, W.plain, car, "case-c", [("Rear bumper fascia", 600, 1)])   # 18% over
        prices.remember(db, W.staff, car, "case-a", [("Tail lamp LH", 212, 1)])         # within 10%
        prices.remember(db, W.staff, car, "case-a", [("Rear spoiler", 300, 1)])         # no book at all
        db.commit()
    finally:
        db.close()

    for who, c in (("a self-signup", W.p), ("staff", W.s)):
        check(c.get("/admin/price-differences").status_code == 403, f"{who}: GET the list 403")
        check(c.post("/admin/price-differences/decide", json={"make": "Diffmotor", "model_key": "epsilon",
                                                              "item_key": "rear bumper", "decision": "keep"}).status_code == 403,
              f"{who}: decide 403")
    r = W.a.get("/admin/price-differences")
    groups = [g for g in (r.json().get("groups") or []) if g["make"] == "Diffmotor"] if r.status_code == 200 else []
    g = groups[0] if groups else {}
    check(r.status_code == 200 and len(groups) == 1 and g.get("item_key") == "rear bumper", "one group: the bumper", brief(r))
    check(g.get("book_price") == 510.0 and g.get("book_cases") == 2 and g.get("approved") is False
          and g.get("users_count") == 3 and g.get("cases_count") == 3 and g.get("min_price") == 520.0
          and g.get("max_price") == 700.0 and g.get("median_user_price") == 600.0 and g.get("wording") == "Rear bumper fascia"
          and g.get("last_used") and set(g) == {"make", "model_key", "item_key", "wording", "book_price", "book_cases", "approved",
                                                "users_count", "cases_count", "min_price", "max_price", "median_user_price",
                                                "last_used"},
          "the group's figures (book median of 2 cases, counts, min / max / median)", repr(g))
    raw = r.text
    names = [user_row(u) for u in (W.staff, W.staff2, W.plain)]
    check(not any(x in raw for u in names for x in (u.id, u.name, u.email, u.username)), "no names, emails or ids in the list")

    r = W.a.post("/admin/price-differences/decide", json={"make": "Diffmotor", "model_key": "epsilon", "item_key": "rear bumper",
                                                           "decision": "update"})
    check(r.status_code == 422, "update without unit_price: 422", brief(r))
    r = W.a.post("/admin/price-differences/decide", json={"make": "Diffmotor", "model_key": "epsilon", "item_key": "rear bumper",
                                                           "decision": "keep", "reason": "one-off quote"})
    check(r.status_code == 200 and r.json() == {"ok": True}, "keep", brief(r))
    listed = [g for g in W.a.get("/admin/price-differences").json()["groups"] if g["make"] == "Diffmotor"]
    check(listed == [], "after keep: off the list", repr(listed))
    db = SessionLocal()
    try:
        keep = db.query(BookPrice).filter(BookPrice.make == "Diffmotor").one()
        check(keep.decision == "keep" and keep.unit_price is None and keep.book_price_before == 510.0 and keep.users_count == 3
              and keep.cases_count == 3 and keep.decided_by == W.admin and keep.active and keep.reason == "one-off quote",
              "the keep row", repr(vars(keep)))
    finally:
        db.close()
    entry = history(action="book_price_decided")[-1]
    check(entry.user_id == W.admin and "keep" in entry.detail and "3 people" in entry.detail, "...in the history", entry.detail)

    # A price changed after the keep: back on the list.
    db = SessionLocal()
    try:
        prices.remember(db, W.plain, car, "case-d", [("Rear bumper fascia", 640, 1)])
        db.commit()
    finally:
        db.close()
    listed = [g for g in W.a.get("/admin/price-differences").json()["groups"] if g["make"] == "Diffmotor"]
    check(len(listed) == 1 and listed[0]["max_price"] == 700.0, "a price changed since the keep: listed again", repr(listed))

    # Update: the approved price is the reference and fills the book's lines.
    r = W.a.post("/admin/price-differences/decide", json={"make": "Diffmotor", "model_key": "epsilon", "item_key": "rear bumper",
                                                           "decision": "update", "unit_price": 640})
    check(r.status_code == 200, "update to 640", brief(r))
    db = SessionLocal()
    try:
        rows = db.query(BookPrice).filter(BookPrice.make == "Diffmotor").order_by(BookPrice.id).all()
        check([(x.decision, x.active) for x in rows] == [("keep", False), ("update", True)] and rows[-1].unit_price == 640.0
              and rows[-1].book_price_before == 510.0, "the update: active, the keep before it inactive",
              repr([(x.decision, x.active, x.unit_price) for x in rows]))
    finally:
        db.close()
    listed = [g for g in W.a.get("/admin/price-differences").json()["groups"] if g["make"] == "Diffmotor"]
    check(len(listed) == 1 and listed[0]["approved"] is True and listed[0]["book_price"] == 640.0,
          "the reference is now the approved 640 (520 and 700 still off)", repr(listed))
    case = new_case(W.staff2, case_data("Diffmotor", "Epsilon"))
    _fill(W.s2, case)
    b = row_named(rows_of(case), "Rear bumper fascia")
    check(b.get("price_source") == "mine", "Sam's own 520 still first", repr(b))
    case = new_case(W.admin, case_data("Diffmotor", "Epsilon"))
    _fill(W.a, case)
    b = row_named(rows_of(case), "Rear bumper fascia")
    check(b.get("price_source") == "approved" and b.get("workshop_est") == 640.0, "an admin with no own price: approved 640",
          repr(b))
    # Keep after an update: the approved price stays the book's.
    W.a.post("/admin/price-differences/decide", json={"make": "Diffmotor", "model_key": "epsilon", "item_key": "rear bumper",
                                                      "decision": "keep"})
    case = new_case(W.admin, case_data("Diffmotor", "Epsilon"))
    _fill(W.a, case)
    check(row_named(rows_of(case), "Rear bumper fascia").get("price_source") == "approved",
          "keep after an update: the approved price stays")


def labour() -> None:
    book("CL 95001", "Labmotor", "Kappa", "2026-01-01", [_line(1, "Rear bumper fascia", 100)], labour=[
        PastLabour(line_no=1, description="To respray rear bumper", workshop_est=300, adjusted_cost=250),
        PastLabour(line_no=2, description="To remove and refit tail lamps", workshop_est=80, adjusted_cost=60),
    ])
    book("CL 95002", "Othermotor", "Omega", "2026-01-01", [_line(1, "Rear bumper fascia", 100)], labour=[
        PastLabour(line_no=1, description="To respray rear bumper", workshop_est=900, adjusted_cost=800),
        PastLabour(line_no=2, description="To realign chassis", workshop_est=500, adjusted_cost=450),
    ])
    db = SessionLocal()
    try:
        own = suggest_labour(db, "Labmotor", "Kappa")
        first = {ln["description"]: ln for ln in own["groups"][0]["lines"]}
        check(first.get("To respray rear bumper", {}).get("usual_price") == 250.0, "same make and model: priced as before")
        other = suggest_labour(db, "Labmotor", "Lambda")
        lines = {ln["description"]: ln for g in other["groups"] for ln in g["lines"]}
        respray, chassis = lines.get("To respray rear bumper", {}), lines.get("To realign chassis", {})
        check(respray.get("usual_price") == 250.0 and respray.get("usual_workshop_est") == 300.0 and respray.get("cases") == 2,
              "a job the make wrote too: priced from the make's lines only", repr(respray))
        check(chassis and chassis.get("usual_price") is None and chassis.get("usual_workshop_est") is None,
              "another make's job: listed, unpriced", repr(chassis))
        nomake = suggest_labour(db, None, None)
        check(all(ln["usual_price"] is None for g in nomake["groups"] for ln in g["lines"]), "no make on the case: all unpriced")
    finally:
        db.close()


def ask_ai() -> None:
    seen = {}

    def fake_call(**kw):
        seen.update(kw)
        return {"parts": [{"part": "Rear Bumper", "lines": [{"name": "Rear bumper side retainer LH", "qty": 1}]}]}

    real_call, real_chain = parts_api.call_structured, settings.gemini_model_chain
    parts_api.call_structured = fake_call
    try:
        settings.gemini_model_chain = "gemini-lite-one, gemini-lite-two"
        case = new_case(W.admin, case_data("Testmotor", "Alpha"))
        r = W.a.post(f"/reports/{case}/annex-suggestions/ai", json={"parts": ["Rear Bumper"]})
        check(r.status_code == 200 and seen.get("models") == ["gemini-lite-one", "gemini-lite-two"],
              "Ask the AI: the reading's chain from settings", brief(r) + repr(seen.get("models")))
        check("gemini-3-flash-preview" not in (seen.get("models") or []), "...no fixed strong model")
        line = (r.json().get("groups") or [{}])[0].get("lines", [{}])[0] if r.status_code == 200 else {}
        check(line.get("usual_unit_price") is None and line.get("price_source") == "ai", "...its lines unpriced, source ai",
              repr(line))
    finally:
        parts_api.call_structured, settings.gemini_model_chain = real_call, real_chain


def no_ai() -> None:
    check(not GEMINI, "no Gemini call anywhere in this test", repr(GEMINI))


def main_() -> int:
    for name, fn in (
        ("setup", setup),
        ("the database: tables, columns, the importer", database),
        ("one-time steps: origins, plates", one_time_steps),
        ("the price choice and who gets which step", price_choice),
        ("the 2-case rule and DB4", two_cases_and_db4),
        ("learning only confirmed prices", learning),
        ("remember, Undo, PATCH, DELETE, GET /me/prices", remember_undo_delete),
        ("Confirm all prices and the sign-off", confirm_and_sign_off),
        ("Price differences", differences),
        ("labour: another make's jobs unpriced", labour),
        ("Ask the AI: the chain from settings", ask_ai),
        ("no AI", no_ai),
    ):
        run_section(name, fn)
    print(f"\ntest_s1_prices: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        for section, label in FAILURES:
            print(f"  FAIL [{section}] {label}")
        return 1
    print("\nALL S1 PRICE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
