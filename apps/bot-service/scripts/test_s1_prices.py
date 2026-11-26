"""Stage 1: "My prices" and price safety (owner approved 24 Nov 2026;
plans/2026-11-23-report-and-parts/S1-contract.md and the owner's final
price model of 24 Nov 2026, app/parts/prices.py, app/api/prices.py, and
the fresh-eyes review's fixes, review-stage1.md).

    python scripts/test_s1_prices.py

Checked:
  - the database: user_prices / book_prices made, past_annex_lines.origin
    and .confirmed added; the one-time steps price_book_origin_v1 (imports
    "import" + confirmed, learned lines "carlink" and unconfirmed) and
    price_book_personal_v1 (H1: plates blanked, file names and file-name
    references cut to the CL reference, in both past tables; one history
    entry with the counts, no plate in it or in the log, idempotent); the
    importer stores no plate and no file name, a cover page without a
    reference gives the file's CL reference only, and GET /parts-history
    shows neither;
  - the price choice at filing and on "Fill prices": a person's own price
    ("mine"), then an admin-promoted one ("approved"), then the book's
    median of 2+ distinct IMPORTED cases ("book"), else none -- and who gets
    which step (price-book staff all three; anyone else their own only;
    nobody's own prices in someone else's case they may not see); side or
    side-less own prices; the line fields (price_source, price_confirmed,
    book_price, price_date, price_cases);
  - the 2-case rule (model and make fallback) over imported cases only: a
    line learned from a signed-off case -- confirmed or not -- never prices
    a case nor counts in the median (the owner's final model); learning
    keeps the lines as history, with whether each price was confirmed;
  - remember on save (remembered in the answer), Undo, PATCH, DELETE
    (history entry with the old price), GET /me/prices filters and "old",
    own rows only; a failure to remember never fails the save; a price 0
    to the cent refused or not remembered (Low 2);
  - line ids (H2): every line has one (GET gives an old case's lines the
    ids its next write keeps), a save matches lines by them -- two lines of
    one wording each keep their own price; a page without ids pairs them in
    order; a made-up id is never stored; renaming a line, or a new qty, is
    no price change (M1);
  - Confirm all prices: only the lines waiting, its count exactly those
    (M2), the PDF cleared (Low 11); the per-line confirm through the save;
  - PUT /reports/{id} and POST /reports never take the price fields as
    sent, and never confirm (Low 1);
  - the sign-off's 409 prices_unconfirmed, and one 409 with must-haves;
    old cases (lines without the fields) never blocked;
  - All users' prices (admins only): every person's prices with their
    name, the shared default beside each (approved or the book's median),
    the filters, newest first; "Use this price for everyone" (promote)
    with the person's or an edited price, audited; the old Price
    differences routes gone;
  - labour: another make's jobs unpriced;
  - "Ask the AI": light models only -- the lite models of the reading chain,
    or the light default; never a strong one (M3; the AI client stubbed).

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
from app.parts import history as hist  # noqa: E402
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
        # Named as the firm names its files: the reference, then the plate.
        import_case(db, case, f"{ref} TEST PL8.xlsx")
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
    # Whose price the admin promotes in the price choice (no case of hers).
    W.pia = new_user("Pia", sees_all_cases=True)
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
        check(marks == {"upgrade:price_book_origin_v1", "upgrade:price_book_personal_v1"}, "both one-time steps ran", repr(marks))
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
        # The importer: no plate, no file name (book() imports "<ref> TEST
        # PL8.xlsx", the firm's way of naming them), origin import, confirmed.
        lines = db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == "CL 90001").all()
        check(lines and all(ln.plate == "" for ln in lines), "the importer stores no plate (the case had one)")
        check(all(ln.source_file == "CL 90001" for ln in lines), "...and of the file's name only its CL reference",
              repr({ln.source_file for ln in lines}))
        check(all(ln.origin == "import" and ln.confirmed is True for ln in lines), "...origin import, confirmed")
    finally:
        db.close()
    r = W.a.get("/parts-history")
    cases = r.json().get("cases", []) if r.status_code == 200 else []
    check(r.status_code == 200 and cases and all(c.get("plate") == "" and c.get("file") == "" for c in cases)
          and "PL8" not in r.text, "GET /parts-history: no plate and no file name, admins too", brief(r))
    # The CL reference alone, whatever the rest of the name; none: nothing.
    check(hist.source_ref("CL 10001 TEST 123 X.xlsx") == "CL 10001" and hist.source_ref("CL10002.xlsx") == "CL 10002"
          and hist.source_ref("synthetic-1.xlsx") == "" and hist.source_ref(None) == "",
          "source_ref: the CL reference of a file name, else nothing")
    check(hist.reduced_ref("CL 91003 TEST 4444 D") == "CL 91003" and hist.reduced_ref("CL91004") == "CL91004"
          and hist.reduced_ref("CL-91005") == "CL-91005" and hist.reduced_ref("Notes") == "Notes",
          "reduced_ref: a file-name reference cut to its CL reference; a CL reference alone as written")


def _xlsx_without_ref(path: Path, plate: str) -> None:
    """A past report whose cover page gives no "Ref: CL ...": the importer
    falls back to the file's name."""
    import openpyxl

    book_ = openpyxl.Workbook()
    cover = book_.active
    cover.title = "Cover Page"
    cover.append([None, "ACCIDENT VEHICLE INSPECTION / ASSESSMENT REPORT"])
    cover.append([None, None, "Date of survey", ":", "3rd February 2026"])
    cover.append([None, None, "Registration No.", ":", plate])
    cover.append([None, None, "Make & Model", ":", "Refmotor Rho"])
    annex = book_.create_sheet("Annex A")
    annex.append([None, "Item", "Vehicle Parts Description", "Condition", "Remarks", "Qty", "W/shop Est. ($)", "Disc (%)",
                  "Adjusted Cost ($)"])
    annex.append([None, None, "List Items"])
    annex.append([None, 1.0, "Rear bumper fascia", "Torn", None, 1, 500, 0.2, 400])
    annex.append([None, None, "Total for Spare Parts :", None, None, None, 500, None, 400])
    book_.save(path)


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
    plate_a, plate_b, plate_c, plate_d, plate_e = "TEST 1111 A", "TEST 2222 B", "TEST 3333 C", "TEST 4444 D", "TEST 5555 E"
    file_a = f"CL 91001 {plate_a}.xlsx"
    db = SessionLocal()
    try:
        # As a database from before Stage 1 has them: plates, file names
        # with plates, a reference the importer took from a file name, no
        # origin.
        db.add_all([
            PastAnnexLine(case_ref="CL 91001", source_file=file_a, plate=plate_a, make="Platemake", model="Pmodel",
                          item="Rear bumper fascia", item_key="rear bumper", workshop_est=100, adjusted_cost=80, origin=None,
                          confirmed=None),
            PastAnnexLine(case_ref="CL 91001", source_file=file_a, plate=plate_a, make="Platemake", model="Pmodel",
                          item="Tail lamp LH", item_key="tail lamp", workshop_est=100, adjusted_cost=80, origin=None,
                          confirmed=None),
            PastAnnexLine(case_ref=f"CL 91003 {plate_d}", source_file=f"CL 91003 {plate_d}.xlsx", plate=plate_d,
                          make="Platemake", model="Pmodel", item="Bonnet", item_key="bonnet", workshop_est=100,
                          adjusted_cost=80, origin=None, confirmed=None),
            PastAnnexLine(case_ref="CL91004", source_file="", plate="", make="Platemake", model="Pmodel", item="Bonnet",
                          item_key="bonnet", workshop_est=100, adjusted_cost=80, origin=None, confirmed=None),
            PastAnnexLine(case_ref="CL 91005", source_file=f"notes {plate_e}.xlsx", plate="", make="Platemake",
                          model="Pmodel", item="Bonnet", item_key="bonnet", workshop_est=100, adjusted_cost=80, origin=None,
                          confirmed=None),
            PastAnnexLine(case_ref="carlink:oldcase0001", source_file="CL 91009", plate="", make="Platemake", model="Pmodel",
                          item="Rear bumper fascia", item_key="rear bumper", workshop_est=100, adjusted_cost=80, origin=None,
                          confirmed=None),
            PastLabourLine(case_ref="CL 91001", source_file=file_a, plate=plate_b, make="Platemake", model="Pmodel",
                           description="To respray", desc_key="respray", workshop_est=10, adjusted_cost=8),
            PastLabourLine(case_ref="CL 91002", source_file="", plate=plate_c, make="Platemake", model="Pmodel",
                           description="To refit", desc_key="refit", workshop_est=10, adjusted_cost=8),
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

    before = len(history(action="price_book_personal_data_dropped"))
    captured = io.StringIO()
    handler = logging.StreamHandler(captured)
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            _rerun_step("price_book_personal_v1", prices.scrub_price_book)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    plates = (plate_a, plate_b, plate_c, plate_d, plate_e, "TEST PL8")
    db = SessionLocal()
    try:
        left = (db.query(PastAnnexLine).filter(PastAnnexLine.plate != "").count()
                + db.query(PastLabourLine).filter(PastLabourLine.plate != "").count())
        stored_text = " ".join(f"{a}|{b}|{c}" for model in (PastAnnexLine, PastLabourLine)
                               for a, b, c in db.query(model.case_ref, model.source_file, model.plate).all())
        files = {ln.case_ref: ln.source_file for ln in db.query(PastAnnexLine).all()}
        labour_files = {ln.case_ref: ln.source_file for ln in db.query(PastLabourLine).all()}
        refs = {ln.case_ref for ln in db.query(PastAnnexLine).all()}
    finally:
        db.close()
    check(left == 0, "personal-data step: no plate left in either past table", str(left))
    check(not any(p in stored_text for p in plates), "...no plate anywhere in a reference, a file name or a plate column")
    check(files.get("CL 91001") == "CL 91001" and labour_files.get("CL 91001") == "CL 91001",
          "...a file name cut to its CL reference, part and labour lines", repr((files.get("CL 91001"), labour_files)))
    check(files.get("CL 91005") == "", "...a file name with no CL reference blanked", repr(files.get("CL 91005")))
    check("CL 91003" in refs and f"CL 91003 {plate_d}" not in refs and files.get("CL 91003") == "CL 91003",
          "...a reference the importer took from a file name cut to its CL reference", repr(sorted(refs)))
    check("CL91004" in refs and "carlink:oldcase0001" in refs and files.get("carlink:oldcase0001") == "CL 91009",
          "...a CL reference as a cover page writes it, and a learned case, left as they were")
    entries = history(action="price_book_personal_data_dropped")
    check(len(entries) == before + 1, "...one history entry", repr([e.detail for e in entries]))
    detail = entries[-1].detail if entries else ""
    check("Plates removed from the price book: 5 line(s) (3 part, 2 labour)" in detail
          and "File names cut to the CL reference: 5 line(s) (4 part, 1 labour)" in detail
          and "References cut to the CL reference: 1 line(s)" in detail, "...with the counts", detail)
    check(entries and entries[-1].report_id is None and entries[-1].user_id is None, "...on no case, by nobody")
    seen = detail + captured.getvalue() + out.getvalue()
    check(not any(p in seen for p in plates), "...and no plate in it or in the log")
    reports_db._once("price_book_personal_v1", prices.scrub_price_book)
    check(len(history(action="price_book_personal_data_dropped")) == before + 1,
          "...done once: a second start-up changes nothing")
    db = SessionLocal()
    try:
        again = prices.scrub_price_book(db)
        check(again == {"plate_parts": 0, "plate_labour": 0, "file_parts": 0, "file_labour": 0, "refs": 0},
              "...and run by hand again it finds nothing to change", repr(again))
        db.rollback()
    finally:
        db.close()

    # A new import of a report whose cover page gives no reference: the
    # file's CL reference only, never the plate in its name -- stored nowhere,
    # shown nowhere.
    plate_f = "TEST 6666 F"
    folder = Path(tempfile.mkdtemp(prefix="s1-import-"))
    path = folder / f"CL 91006 {plate_f}.xlsx"
    _xlsx_without_ref(path, plate_f)
    case = hist.parse_report(path)
    check(case.ref == "CL 91006", "a cover page without a reference: the file's CL reference", repr(case.ref))
    with path.open("rb") as fh:
        r = W.a.post("/parts-history/import", files=[("files", (path.name, fh.read(), "application/octet-stream"))])
    check(r.status_code == 200 and (r.json().get("results") or [{}])[0].get("ref") == "CL 91006", "imported", brief(r))
    db = SessionLocal()
    try:
        rows = db.query(PastAnnexLine).filter(PastAnnexLine.make == "Refmotor").all()
        check(rows and all(ln.case_ref == "CL 91006" and ln.source_file == "CL 91006" and ln.plate == "" for ln in rows),
              "...stored as CL 91006, file CL 91006, no plate", repr([(ln.case_ref, ln.source_file, ln.plate) for ln in rows]))
    finally:
        db.close()
    r = W.a.get("/parts-history")
    check(r.status_code == 200 and plate_f not in r.text and "TEST 6666" not in r.text, "...and GET /parts-history shows no plate")
    entry = history(action="past_reports_imported")
    check(entry and plate_f not in entry[-1].detail, "...nor the import's history entry", entry[-1].detail if entry else "")


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

    # Step 2: an admin-promoted price, before the book (staff only): Pia's
    # own 720, used for everyone.
    book("CL 92001", "Apprmotor", "Zeta", "2026-02-01", [_line(1, "Rear bumper fascia", 700)])
    book("CL 92002", "Apprmotor", "Zeta", "2026-03-01", [_line(1, "Rear bumper fascia", 800)])
    db = SessionLocal()
    try:
        pia = prices.remember(db, W.pia, {"make": "Apprmotor", "model": "Zeta"}, None, [("Rear bumper fascia", 720, 1)])
        db.commit()
    finally:
        db.close()
    r = W.a.post(f"/admin/user-prices/{pia[0]['id']}/promote", json={"reason": "new list price"})
    check(r.status_code == 200 and r.json().get("default_price") == 720.0 and r.json().get("default_source") == "approved",
          "admin uses Pia's 720 for Apprmotor Zeta's bumper for everyone", brief(r))
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


def two_cases_and_learned() -> None:
    # One imported case: never priced from it.
    book("CL 93001", "Dbfour", "Gamma", "2026-04-01", [_line(1, "Rear bumper fascia", 300)])
    case = new_case(W.staff, case_data("Dbfour", "Gamma"))
    _fill(W.s, case)
    check(row_named(rows_of(case), "Rear bumper fascia").get("workshop_est") == 0, "one past case: unpriced")
    # A signed-off Carlink case's line nobody confirmed: still one case.
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
          "an unconfirmed learned line doesn't make the second case")
    r = W.a.get("/parts-history?make=Dbfour")
    line = next((x for x in r.json().get("lines", []) if x["item"] == "Rear bumper fascia"), {})
    check(line.get("usual_unit_price") == 240.0 and line.get("cases") == 2,
          "parts history: the learned price left out of the usual price (the line still counted)", repr(line))
    # Confirmed at sign-off, it still doesn't (the owner's final model:
    # signing off never changes the shared default).
    book(learn.case_ref(learned), "Dbfour", "Gamma", "2026-05-01", [_line(1, "Rear bumper fascia", 340, confirmed=True)])
    db = SessionLocal()
    try:
        ln = db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == learn.case_ref(learned)).one()
        check(ln.origin == "carlink" and ln.confirmed is True and not hist.price_usable(ln),
              "a confirmed learned line: kept as history, never a price")
    finally:
        db.close()
    case = new_case(W.staff, case_data("Dbfour", "Gamma"))
    _fill(W.s, case)
    b = row_named(rows_of(case), "Rear bumper fascia")
    check(b and b.get("workshop_est") == 0 and b.get("price_source") is None,
          "confirmed, the learned case is still no second case: unpriced", repr(b))
    r = W.a.get("/parts-history?make=Dbfour")
    line = next((x for x in r.json().get("lines", []) if x["item"] == "Rear bumper fascia"), {})
    check(line.get("usual_unit_price") == 240.0, "...nor in the parts history's usual price", repr(line))
    # A second IMPORTED case: priced, from the imports alone (median 320).
    book("CL 93002", "Dbfour", "Gamma", "2026-06-01", [_line(1, "Rear bumper fascia", 340)])
    case = new_case(W.staff, case_data("Dbfour", "Gamma"))
    _fill(W.s, case)
    b = row_named(rows_of(case), "Rear bumper fascia")
    check(b.get("workshop_est") == 320.0 and b.get("price_cases") == 2 and b.get("price_source") == "book",
          "a second imported case: priced (median 320 of the two imports)", repr(b))
    # The make fallback: another model of the make takes the make's lines,
    # priced only from 2+ imported cases there too.
    case = new_case(W.staff, case_data("Dbfour", "Omicron"))
    _fill(W.s, case)
    b = row_named(rows_of(case), "Rear bumper fascia")
    check(b.get("workshop_est") == 320.0 and b.get("price_cases") == 2, "make fallback with 2 imported cases: priced", repr(b))
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

    # A price that is 0 to the cent (review Low 2): refused, or not kept.
    for bad in (0.004, 0.001):
        r = W.s2.patch(f"/me/prices/{pid}", json={"unit_price": bad})
        check(r.status_code == 422, f"PATCH {bad}: 422 (0.00 to the cent)", brief(r))
    db = SessionLocal()
    try:
        out = prices.remember(db, W.staff2, {"make": "Testmotor", "model": "Alpha"}, None, [("Rear bumper clips", 0.01, 10)])
        db.commit()
        kept = db.query(UserPrice).filter(UserPrice.user_id == W.staff2, UserPrice.item_key == "rear bumper clips").count()
    finally:
        db.close()
    check(out == [] and kept == 0, "an estimate of 0.01 for 10: under a cent a unit, not remembered", repr(out))


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
    before_rows = rows_of(case)
    was_waiting = {i for i, x in enumerate(before_rows) if prices.awaits_confirmation(x)}
    r = W.s2.post(f"/reports/{case}/annex-a/confirm-prices")
    body = r.json() if r.status_code == 200 else {}
    items = (body.get("annex_a") or {}).get("items") or []
    check(r.status_code == 200 and len(items) == len(before_rows) and was_waiting
          and all(items[i].get("price_confirmed") is True for i in was_waiting)
          and all(items[i].get("price_confirmed") == before_rows[i].get("price_confirmed")
                  for i in range(len(items)) if i not in was_waiting)
          and set(body) == {"annex_a", "annex_c", "confirmed"} and body["annex_c"].get("workshop_total") is not None
          and body.get("confirmed") == len(waiting) == len(was_waiting),
          "confirm-prices: the waiting lines confirmed, the rest as they were, {annex_a, annex_c} back", brief(r))
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
        check(lines and not any(hist.price_usable(ln) for ln in lines),
              "...as history only: none of its lines ever prices a case (signing off never changes the default)")
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


def _row(name: str, est: float, **kw) -> dict:
    """An Annex A line as a case stores it (before line ids)."""
    base = {"item_no": "", "part_name": name, "condition": "", "action": "", "qty": 1.0, "qty_text": None,
            "workshop_est": est, "discount_pct": 0.2, "adjusted_cost": 0.0, "is_net_item": False,
            "oem_part_number": None, "suggested_from": None, "usual_unit_price": None, "prefilled": False,
            "origin": "typed", "source_part": None, "stale": False}
    base.update(kw)
    return base


def _page_rows(client: TestClient, report_id: str) -> list[dict]:
    """The lines as the case page loads them (GET /reports/{id})."""
    r = client.get(f"/reports/{report_id}")
    return [dict(x) for x in (((r.json().get("data") or {}).get("annex_a") or {}).get("items") or [])] if r.status_code == 200 else []


def _own_prices(user_id: str, item_key: str) -> list[float]:
    db = SessionLocal()
    try:
        return [u.unit_price for u in db.query(UserPrice).filter(UserPrice.user_id == user_id, UserPrice.item_key == item_key)]
    finally:
        db.close()


def _brief_rows(rows: list[dict]) -> str:
    return repr([(x.get("part_name"), x.get("workshop_est"), x.get("price_source"), x.get("price_confirmed"), x.get("line_id"))
                 for x in rows])


def line_ids() -> None:
    # The review's repro A (H2): two lines of one wording, stored before
    # line ids -- a typed 100 and the book's 40, waiting.
    data = case_data("Linemotor", "Sigma", filled=True)
    data["annex_a"] = {"items": [
        _row("Bracket", 100.0, price_source="typed", price_confirmed=True),
        _row("Bracket", 40.0, prefilled=True, origin="history", suggested_from="2 past cases", usual_unit_price=32.0,
             price_source="book", price_confirmed=False, book_price=40.0, price_cases=2),
    ]}
    case = new_case(W.staff, data)
    check(all("line_id" not in x for x in rows_of(case)), "(an old case: its lines stored without ids)")
    page = _page_rows(W.s, case)
    ids = [x.get("line_id") for x in page]
    check(len(page) == 2 and all(ids) and len(set(ids)) == 2, "GET: each line its own id", _brief_rows(page))
    check(all("line_id" not in x for x in rows_of(case)) and [x.get("line_id") for x in _page_rows(W.s, case)] == ids,
          "...worked out, never written; the same on every load")
    for n in (1, 2):
        r = save_rows(W.s, case, page)
        body = r.json() if r.status_code == 200 else {}
        after = (body.get("annex_a") or {}).get("items") or []
        check(r.status_code == 200 and body.get("remembered") == [] and [x.get("line_id") for x in after] == ids
              and after[0].get("price_source") == "typed" and after[1].get("price_source") == "book"
              and after[1].get("price_confirmed") is False and after[1].get("workshop_est") == 40.0,
              f"save {n}, nothing changed: each line keeps its own price; the book's 40 still waits, nothing remembered",
              brief(r) if r.status_code != 200 else _brief_rows(after))
        page = [dict(x) for x in after]
    check([x.get("line_id") for x in rows_of(case)] == ids, "...the ids the page was given are the ones stored")
    check(len(review.unconfirmed_prices(stored(case).data)) == 1, "...the sign-off still waits for the 40")
    check(_own_prices(W.staff, "bracket") == [], "...and nothing is Stella's own price")

    # Typing X on the first: that line alone is typed, and X is hers; two
    # more saves change nothing.
    page[0]["workshop_est"] = 120.0
    r = save_rows(W.s, case, page)
    body = r.json() if r.status_code == 200 else {}
    after = (body.get("annex_a") or {}).get("items") or []
    rem = body.get("remembered") or []
    check(r.status_code == 200 and len(rem) == 1 and rem[0]["unit_price"] == 120.0 and after[1].get("price_source") == "book"
          and after[1].get("price_confirmed") is False, "typing 120 on the first: remembered; the second still the book's, waiting",
          brief(r))
    page = [dict(x) for x in after]
    for n in (1, 2):
        r = save_rows(W.s, case, page)
        after = (r.json().get("annex_a") or {}).get("items") or [] if r.status_code == 200 else []
        check(r.status_code == 200 and r.json().get("remembered") == [] and after[1].get("price_confirmed") is False,
              f"...saved again ({n}): nothing remembered, the 40 still waits", brief(r))
        page = [dict(x) for x in after]
    check(_own_prices(W.staff, "bracket") == [120.0], "...her own price stays 120, never the book's 40",
          repr(_own_prices(W.staff, "bracket")))

    # The page sends them in the other order: each line is still itself.
    r = save_rows(W.s, case, [page[1], page[0]])
    after = (r.json().get("annex_a") or {}).get("items") or [] if r.status_code == 200 else []
    check(r.status_code == 200 and r.json().get("remembered") == [] and [x.get("line_id") for x in after] == [ids[1], ids[0]]
          and after[0].get("workshop_est") == 40.0 and after[0].get("price_source") == "book"
          and after[0].get("price_confirmed") is False and after[1].get("price_source") == "typed",
          "the lines in another order: matched by id, each keeps its price", _brief_rows(after))
    # An older page, without ids: paired by wording IN ORDER.
    no_ids = [{k: v for k, v in x.items() if k != "line_id"} for x in after]
    r = save_rows(W.s, case, no_ids)
    again = (r.json().get("annex_a") or {}).get("items") or [] if r.status_code == 200 else []
    check(r.status_code == 200 and r.json().get("remembered") == [] and [x.get("line_id") for x in again] == [ids[1], ids[0]]
          and again[0].get("price_source") == "book" and again[0].get("price_confirmed") is False,
          "a page without ids: the k-th 'Bracket' is the k-th stored one -- the 40 still the book's, waiting",
          _brief_rows(again))
    # A new line with an id a page made up: a new line, with an id of the
    # server's.
    sent = [dict(x) for x in again] + [_row("Bracket", 0.0, line_id="Lmadeup00001")]
    r = save_rows(W.s, case, sent)
    after = (r.json().get("annex_a") or {}).get("items") or [] if r.status_code == 200 else []
    new_id = after[-1].get("line_id") if len(after) == 3 else None
    check(r.status_code == 200 and new_id and new_id not in ("Lmadeup00001", ids[0], ids[1])
          and after[0].get("price_confirmed") is False, "a new line: an id of its own, never one the page made up",
          _brief_rows(after))

    # M1 (repro B): renaming a filled-in line is no price change.
    data = case_data("Linemotor", "Sigma", filled=True)
    data["annex_a"] = {"items": [
        _row("Rear bumper fascia", 550.0, prefilled=True, origin="history", suggested_from="2 past cases",
             usual_unit_price=440.0, price_source="book", price_confirmed=False, book_price=550.0, price_cases=2,
             source_part="Rear Bumper"),
    ]}
    case = new_case(W.staff2, data)
    page = _page_rows(W.s2, case)
    lid = page[0].get("line_id") if page else None
    page[0]["part_name"], page[0]["origin"] = "Rear bumper cover", "typed"
    r = save_rows(W.s2, case, page)
    row = ((r.json().get("annex_a") or {}).get("items") or [{}])[0] if r.status_code == 200 else {}
    check(r.status_code == 200 and r.json().get("remembered") == [] and row.get("part_name") == "Rear bumper cover"
          and row.get("line_id") == lid and row.get("price_source") == "book" and row.get("price_confirmed") is False
          and row.get("book_price") == 550.0 and row.get("origin") == "typed",
          "a rename: the same line (its id), still the book's price, waiting, nothing remembered", brief(r))
    check(_own_prices(W.staff2, "rear bumper cover") == [], "...not Sam's own price")
    page = [dict(row)]
    page[0]["qty"] = 2.0
    r = save_rows(W.s2, case, page)
    row = ((r.json().get("annex_a") or {}).get("items") or [{}])[0] if r.status_code == 200 else {}
    check(r.status_code == 200 and r.json().get("remembered") == [] and row.get("price_source") == "book"
          and row.get("price_confirmed") is False and row.get("workshop_est") == 550.0,
          "a new qty, the estimate untouched: no price change", brief(r))
    # An older page renames it and sends no id: still the machine's price.
    old_page = [{k: v for k, v in row.items() if k != "line_id"}]
    old_page[0]["part_name"] = "Rear bumper skin"
    r = save_rows(W.s2, case, old_page)
    row = ((r.json().get("annex_a") or {}).get("items") or [{}])[0] if r.status_code == 200 else {}
    check(r.status_code == 200 and r.json().get("remembered") == [] and row.get("price_source") == "book"
          and row.get("price_confirmed") is False and row.get("line_id"),
          "a rename from a page without ids: still the book's price, waiting, not remembered", brief(r))
    check(_own_prices(W.staff2, "rear bumper skin") == [], "...not Sam's own price")
    # Typing the estimate is: typed, confirmed, remembered per unit.
    page = [dict(row)]
    page[0]["workshop_est"] = 600.0
    r = save_rows(W.s2, case, page)
    row = ((r.json().get("annex_a") or {}).get("items") or [{}])[0] if r.status_code == 200 else {}
    rem = r.json().get("remembered") or [] if r.status_code == 200 else []
    check(row.get("price_source") == "typed" and row.get("price_confirmed") is True and rem and rem[0]["unit_price"] == 300.0,
          "typing the estimate: typed, confirmed, remembered (600 / 2)", brief(r))


def confirm_only_waiting() -> None:
    # The review's repro D (M2): a line from before Stage 1 (a 1-case price,
    # prefilled, no price fields) and a book price waiting.
    data = case_data("Testmotor", "Alpha", filled=True)
    data["annex_a"] = {"items": [
        _row("Rear bumper clips", 50.0, qty=10.0, condition="Necessary", suggested_from="1 past case (Testmotor Alpha)",
             usual_unit_price=4.0, prefilled=True, origin="history", source_part="Rear Bumper"),
        _row("Rear bumper fascia", 550.0, suggested_from="2 past cases", usual_unit_price=440.0, prefilled=True,
             origin="history", source_part="Rear Bumper", price_source="book", price_confirmed=False, book_price=550.0,
             price_cases=2),
    ]}
    case = new_case(W.staff, data)
    db = SessionLocal()
    try:
        db.get(Report, case).pdf_path = str(STORAGE / "old-render.pdf")
        db.commit()
    finally:
        db.close()
    check(len(review.unconfirmed_prices(stored(case).data)) == 1, "(one price waiting)")
    r = W.s.post(f"/reports/{case}/annex-a/confirm-prices")
    body = r.json() if r.status_code == 200 else {}
    rows = rows_of(case)
    legacy, fascia = row_named(rows, "Rear bumper clips"), row_named(rows, "Rear bumper fascia")
    check(r.status_code == 200 and body.get("confirmed") == 1, "Confirm all: confirmed 1, the lines it changed", brief(r))
    check(fascia.get("price_confirmed") is True and legacy.get("price_confirmed") is None and legacy.get("prefilled") is True,
          "...the waiting line confirmed; the old line untouched", _brief_rows(rows))
    learned = learn.past_case_from(stored(case))
    got = {ln.item: ln.confirmed for ln in (learned.lines if learned else [])}
    check(got == {"Rear bumper clips": False, "Rear bumper fascia": True},
          "...so the old 1-case price is never learned as confirmed", repr(got))
    counts = review.review_state(stored(case).data, {}, [], signed_off=False)["counts"]["annex_a"]
    check(counts.get("history_price") == 1, "...and the review still shows it as a past-case price to check", repr(counts))
    check(stored(case).pdf_path is None, "...the PDF rendered again on the next download (Low 11)")
    entry = history(case, "annex_a_prices_confirmed")
    check(entry and entry[-1].detail.startswith("1 price confirmed"), "...its history entry: 1", repr([e.detail for e in entry]))
    r = W.s.post(f"/reports/{case}/annex-a/confirm-prices")
    check(r.status_code == 200 and r.json().get("confirmed") == 0 and rows_of(case) == rows, "pressed again: 0, nothing changed",
          brief(r))


def other_routes() -> None:
    # Low 1 (repro C): PUT /reports/{id} with the lines' price fields as a
    # client likes them.
    data = case_data("Linemotor", "Sigma", filled=True)
    data["annex_a"] = {"items": [
        _row("Tail lamp LH", 310.0, prefilled=True, origin="history", price_source="book", price_confirmed=False,
             book_price=310.0, price_cases=2),
    ]}
    case = new_case(W.staff, data)
    items = _page_rows(W.s, case)
    lid = items[0].get("line_id") if items else None
    items[0].update(price_confirmed=True, price_source="typed", book_price=1.0, prefilled=False)
    draft = {"description": "Made-up test case.", "category": ["Vehicle Collision or Damage"], "annex_a": {"items": items}}
    r = W.s.put(f"/reports/{case}", json={"draft": draft})
    row = rows_of(case)[0] if rows_of(case) else {}
    check(r.status_code == 200 and row.get("price_source") == "book" and row.get("price_confirmed") is False
          and row.get("book_price") == 310.0 and row.get("line_id") == lid,
          "PUT /reports/{id}: the price fields the stored line's, never confirmed there", brief(r) + _brief_rows([row]))
    check(len(review.unconfirmed_prices(stored(case).data)) == 1, "...the sign-off still waits for it")
    items = _page_rows(W.s, case)
    items[0]["workshop_est"] = 333.0
    r = W.s.put(f"/reports/{case}", json={"draft": {**draft, "annex_a": {"items": items}}})
    row = rows_of(case)[0] if rows_of(case) else {}
    check(r.status_code == 200 and row.get("price_source") == "typed" and row.get("price_confirmed") is True
          and row.get("workshop_est") == 333.0 and _own_prices(W.staff, "tail lamp") == [],
          "...a new estimate there is the person's own figure ('typed'), not remembered", _brief_rows([row]))

    # POST /reports: new lines, so nothing arrives confirmed as some other
    # source; each gets an id.
    draft = {"description": "Made-up test case.", "category": ["Vehicle Collision or Damage"],
             "vehicle_info": {"make": "Linemotor", "model": "Sigma"},
             "annex_a": {"items": [
                 _row("Bonnet", 100.0, prefilled=True, price_source="approved", price_confirmed=True, book_price=1.0),
                 _row("Front bumper", 50.0, price_source="book", price_confirmed=True),
                 _row("Grille", 0.0, price_source="mine", price_confirmed=False, line_id="Lmadeup00002"),
             ]}}
    r = W.s.post("/reports", json={"draft": draft})
    rows = rows_of(r.json()["id"]) if r.status_code == 200 else []
    got = {x["part_name"]: (x.get("price_source"), x.get("price_confirmed"), x.get("book_price")) for x in rows}
    check(r.status_code == 200 and got == {"Bonnet": ("approved", False, None), "Front bumper": ("typed", True, None),
                                           "Grille": (None, None, None)},
          "POST /reports: a filled-in price waits, a typed one is 'typed', none comes confirmed as sent", brief(r) + repr(got))
    ids = [x.get("line_id") for x in rows]
    check(rows and all(ids) and len(set(ids)) == len(ids) and "Lmadeup00002" not in ids, "...each line an id of the server's",
          repr(ids))


def _promote(client: TestClient, price_id, **body):
    return client.post(f"/admin/user-prices/{price_id}/promote", json=body)


FIELDS = {"id", "user_id", "user_name", "make", "model_key", "item_key", "part", "wording", "side", "unit_price",
          "default_price", "default_source", "updated_at", "uses", "last_case_id"}


def all_user_prices() -> None:
    book("CL 94001", "Diffmotor", "Epsilon", "2026-01-01", [_line(1, "Rear bumper fascia", 500), _line(2, "Tail lamp LH", 200)])
    book("CL 94002", "Diffmotor", "Epsilon", "2026-02-01", [_line(1, "Rear bumper fascia", 520), _line(2, "Tail lamp LH", 210)])
    car = {"make": "Diffmotor", "model": "Epsilon"}
    made = {}
    db = SessionLocal()
    try:
        for key, who, case, wording, est in (("stella_bumper", W.staff, "case-a", "Rear bumper fascia", 700),
                                             ("sam_bumper", W.staff2, "case-b", "Rear bumper fascia", 520),
                                             ("stella_lamp", W.staff, "case-a", "Tail lamp LH", 212),
                                             ("stella_spoiler", W.staff, None, "Rear spoiler", 300),
                                             ("una_bumper", W.plain, "case-c", "Rear bumper fascia", 600)):
            made[key] = prices.remember(db, who, car, case, [(wording, est, 1)])[0]["id"]
        db.commit()
        # Newest last made: Una's bumper first in the list.
        now = datetime.now(timezone.utc)
        for n, key in enumerate(("stella_bumper", "sam_bumper", "stella_lamp", "stella_spoiler", "una_bumper")):
            db.query(UserPrice).filter(UserPrice.id == made[key]).update({UserPrice.updated_at: now - timedelta(minutes=10 - n)})
        db.commit()
    finally:
        db.close()

    names = {u: user_row(u) for u in (W.staff, W.staff2, W.plain)}
    for who, c in (("a self-signup", W.p), ("staff", W.s)):
        r = c.get("/admin/user-prices")
        check(r.status_code == 403 and not any(u.name in r.text for u in names.values()), f"{who}: GET the list 403, no names")
        check(_promote(c, made["una_bumper"]).status_code == 403, f"{who}: promote 403")
    r = W.a.get("/admin/user-prices?make=Diffmotor")
    rows = r.json().get("prices") or [] if r.status_code == 200 else []
    check(r.status_code == 200 and [x["id"] for x in rows] == [made[k] for k in ("una_bumper", "stella_spoiler", "stella_lamp",
                                                                                   "sam_bumper", "stella_bumper")],
          "admin: everyone's prices of the make, newest first", brief(r))
    check(rows and all(set(x) == FIELDS for x in rows), "...each with exactly its fields", repr(rows[:1]))
    by = {x["id"]: x for x in rows}
    una, lamp, spoiler = by.get(made["una_bumper"], {}), by.get(made["stella_lamp"], {}), by.get(made["stella_spoiler"], {})
    check(una.get("user_id") == W.plain and (una.get("user_name") or "").startswith("Una")
          and (by.get(made["stella_bumper"], {}).get("user_name") or "").startswith("Stella"),
          "...with the person's name (admins only)", repr((una.get("user_name"), una.get("user_id"))))
    check(una.get("default_price") == 510.0 and una.get("default_source") == "book" and una.get("unit_price") == 600.0
          and una.get("wording") == "Rear bumper fascia" and una.get("part") == hist.canonical_part("Rear bumper fascia")
          and una.get("part") and una.get("make") == "Diffmotor" and una.get("model_key") == "epsilon"
          and una.get("item_key") == "rear bumper" and una.get("last_case_id") == "case-c" and una.get("uses") == 1,
          "...the shared default beside it: the book's median of 2 imported cases", repr(una))
    check(lamp.get("default_price") == 205.0 and lamp.get("side") == "Left" and lamp.get("part") == hist.canonical_part("Tail lamp LH"),
          "...a sided line, its standard name", repr(lamp))
    check(spoiler.get("default_price") is None and spoiler.get("default_source") is None, "...no default when the book has none",
          repr(spoiler))

    def listed(query: str) -> list:
        res = W.a.get(f"/admin/user-prices?{query}")
        return [x["id"] for x in res.json().get("prices") or []] if res.status_code == 200 else ["status", res.status_code]

    check(listed(f"user={W.plain}&make=Diffmotor") == [made["una_bumper"]], "?user= an account id")
    check(sorted(listed("user=STEL&make=diffmotor")) == sorted(made[k] for k in ("stella_bumper", "stella_lamp", "stella_spoiler")),
          "?user= words of a name, any case")
    check(listed("user=nobody-like-this") == [], "?user= nobody: none")
    check(len(listed("make=Diffmotor&model=Epsilon 1.5")) == 5 and listed("make=Diffmotor&model=Zeta") == [],
          "?model= as typed on a case")
    check(listed("make=Diffmotor&q=SPOILER") == [made["stella_spoiler"]], "?q= words of the wording")
    part = hist.canonical_part("Tail lamp LH") or "Tail Lamp"
    check(made["stella_lamp"] in listed(f"make=Diffmotor&q={part}"), "?q= the standard part name")

    # Use Una's price for everyone, edited to 590, with a reason.
    r = _promote(W.a, made["una_bumper"], unit_price=590, reason="supplier list")
    body = r.json() if r.status_code == 200 else {}
    check(r.status_code == 200 and set(body) == FIELDS and body.get("id") == made["una_bumper"] and body.get("unit_price") == 600.0
          and body.get("default_price") == 590.0 and body.get("default_source") == "approved",
          "promote, edited to 590: the row back, the default now 590 (approved), Una's own 600 untouched", brief(r))
    db = SessionLocal()
    try:
        rows_bp = db.query(BookPrice).filter(BookPrice.make == "Diffmotor").order_by(BookPrice.id).all()
        last = rows_bp[-1] if rows_bp else None
        check(last is not None and last.decision == "update" and last.active and last.unit_price == 590.0
              and last.book_price_before == 510.0 and last.decided_by == W.admin and last.reason == "supplier list"
              and last.item_key == "rear bumper" and last.model_key == "epsilon",
              "...a book_prices row: update, active, the default before kept",
              repr([(x.decision, x.active, x.unit_price, x.book_price_before) for x in rows_bp]))
    finally:
        db.close()
    entry = history(action="book_price_promoted")[-1] if history(action="book_price_promoted") else None
    check(entry is not None and entry.user_id == W.admin and entry.report_id is None and "510.00 (book) -> 590.00" in entry.detail
          and "Una" in entry.detail and "600.00" in entry.detail and "supplier list" in entry.detail,
          "...in the history: who, the default before and after, whose price", entry.detail if entry else "")
    # Sam's as it is: the 590 row inactive now.
    r = _promote(W.a, made["sam_bumper"])
    check(r.status_code == 200 and r.json().get("default_price") == 520.0, "promote Sam's as it is: 520", brief(r))
    db = SessionLocal()
    try:
        rows_bp = db.query(BookPrice).filter(BookPrice.make == "Diffmotor").order_by(BookPrice.id).all()
        check([(x.decision, x.active, x.unit_price) for x in rows_bp] == [("update", False, 590.0), ("update", True, 520.0)],
              "...the older promotion on the line inactive", repr([(x.decision, x.active, x.unit_price) for x in rows_bp]))
        # A "keep" row of the retired Price differences list is never read.
        db.add(BookPrice(make="Diffmotor", model_key="epsilon", item_key="rear bumper", wording="Rear bumper fascia",
                         decision="keep", unit_price=None, decided_by=W.admin, decided_at=datetime.now(timezone.utc),
                         active=True))
        db.commit()
    finally:
        db.close()
    case = new_case(W.admin, case_data("Diffmotor", "Epsilon"))
    _fill(W.a, case)
    b = row_named(rows_of(case), "Rear bumper fascia")
    check(b.get("price_source") == "approved" and b.get("workshop_est") == 520.0 and b.get("price_confirmed") is False,
          "an admin with no own price: the promoted 520, waiting (an old 'keep' row ignored)", repr(b))
    case = new_case(W.staff, case_data("Diffmotor", "Epsilon"))
    _fill(W.s, case)
    b = row_named(rows_of(case), "Rear bumper fascia")
    check(b.get("price_source") == "mine" and b.get("workshop_est") == 700.0 and b.get("book_price") == 520.0,
          "Stella: her own 700 still first, the default beside it", repr(b))
    db = SessionLocal()
    try:
        una_own = db.get(UserPrice, made["una_bumper"])
        check(una_own is not None and una_own.unit_price == 600.0 and una_own.previous_unit_price is None,
              "Una's own price untouched by the promotion")
    finally:
        db.close()

    for bad, label in (({"unit_price": 0}, "0"), ({"unit_price": 0.004}, "0.004"), ({"reason": "x" * 501}, "a reason over 500")):
        check(_promote(W.a, made["una_bumper"], **bad).status_code == 422, f"promote with {label}: 422")
    r = _promote(W.a, 987654321)
    check(r.status_code == 404 and r.json().get("code") == "price_not_found", "promote a price that doesn't exist: 404", brief(r))
    # The retired routes.
    check(W.a.get("/admin/price-differences").status_code == 404, "GET /admin/price-differences: gone (404)")
    check(W.a.post("/admin/price-differences/decide", json={"make": "Diffmotor", "model_key": "epsilon", "item_key": "rear bumper",
                                                            "decision": "keep"}).status_code in (404, 405),
          "POST /admin/price-differences/decide: gone")


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

    check(not parts_api.is_light_model("gemini-3-flash-preview") and not parts_api.is_light_model("gemini-3.6-flash")
          and parts_api.is_light_model("gemini-3.5-flash-lite") and parts_api.is_light_model("gemini-2.0-flash-lite-001")
          and not parts_api.is_light_model("gemini-flashlite"), "a light model: 'lite' one of its name's words")
    real_call, real_chain = parts_api.call_structured, settings.gemini_model_chain
    parts_api.call_structured = fake_call
    try:
        case = new_case(W.admin, case_data("Testmotor", "Alpha"))
        for chain_text, want, label in (
            ("gemini-3-flash-preview,gemini-3.6-flash,gemini-3.7-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite",
             ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"], "a strong-first chain: its lite models only, in order"),
            ("gemini-3-flash-preview,gemini-3.6-flash", ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
             "a chain with no lite model: the light default"),
            ("gemini-lite-one, gemini-lite-two", ["gemini-lite-one", "gemini-lite-two"], "a light chain: as configured"),
        ):
            seen.clear()
            settings.gemini_model_chain = chain_text
            r = W.a.post(f"/reports/{case}/annex-suggestions/ai", json={"parts": ["Rear Bumper"]})
            check(r.status_code == 200 and seen.get("models") == want, f"Ask the AI, {label}", brief(r) + repr(seen.get("models")))
            check(not any(m in (seen.get("models") or []) for m in ("gemini-3-flash-preview", "gemini-3.6-flash", "gemini-3.7-flash")),
                  "...never a strong model")
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
        ("one-time steps: origins, plates and file names", one_time_steps),
        ("the price choice and who gets which step", price_choice),
        ("the 2-case rule: imported cases only", two_cases_and_learned),
        ("learning keeps history, with what was confirmed", learning),
        ("remember, Undo, PATCH, DELETE, GET /me/prices", remember_undo_delete),
        ("Confirm all prices and the sign-off", confirm_and_sign_off),
        ("Confirm all prices: only the lines waiting", confirm_only_waiting),
        ("line ids: a save matches lines by id; a rename is no price change", line_ids),
        ("PUT /reports and POST /reports: the price fields the server's", other_routes),
        ("All users' prices and Use this price for everyone", all_user_prices),
        ("labour: another make's jobs unpriced", labour),
        ("Ask the AI: light models only", ask_ai),
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
