"""AI accuracy on signed-off cases, and signed-off cases teaching the parts
history (contract 8 L: app/reports/accuracy.py, app/parts/learn.py,
app/api/ai_accuracy.py).

    python scripts/test_ai_accuracy.py

Checked: compare() on crafted drafts -- damage parts kept, removed, added,
on another side, placed from "(side undetermined)", Repair/Replace and
condition changed or filled; Annex A lines kept, removed, added by where
they came from, prices changed against the machine's; cover fields kept,
changed, emptied, filled by people or by the firm's usual wording; old
cases and missing keys; no case value in the answer. A sign-off stores the
row (with the models that read the case) and a re-sign-off replaces it; a
reopen or a delete drops it; a sign-off still succeeds when the accuracy
or the learning step raises. Learning: a sign-off puts the final Annex A
into the history as "carlink:<id>" with the importer's fields and no plate,
a re-sign-off replaces it, a reopen or delete removes it; not without a
make and model, nor with the setting off; a case never gets suggestions
from its own lines (its own suggestions panel, the rebuilt Annex A, a
removal that failed), and a case whose spreadsheet was imported too counts
once. GET /admin/ai-accuracy is admin only (403 otherwise), sums per month,
names the most missed and removed parts, filters by ?since and says "no
cases" when there are none. The backfill scripts' dry runs write nothing;
--apply writes.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database, the PDF render replaced by a stub, Gemini by a fake that fails
the test if anything calls it, and every outside connection refused. Only
made-up data: no real plate, name or report value.
"""
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
_tmp = Path(tempfile.mkdtemp(prefix="ai-accuracy-test-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
os.environ["STORAGE_DIR"] = str(STORAGE)
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaSyFakeCompanyKeyForAccuracyTest0001"
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
import backfill_ai_accuracy  # noqa: E402
import backfill_learned_cases  # noqa: E402
from app.auth.core import SESSION_COOKIE, create_session  # noqa: E402
from app.config import settings  # noqa: E402
from app.parts import learn  # noqa: E402
from app.parts.history import PastCase, PastLine, import_case, past_lines, restricted, suggest  # noqa: E402
from app.reports import accuracy  # noqa: E402
from app.reports.db import SessionLocal, engine, init_db  # noqa: E402
from app.reports.models import AiAccuracy, AiRun, PastAnnexLine, Report, User, gen_id  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402


def _fake_render(data, photo_paths, output_path, report_id=None, **_kw):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(b"%PDF-1.4\n% test\n%%EOF\n")


main.render_pdf = _fake_render
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

MAKE, MODEL = "Alphamotors", "Modelone"
PLATE = "TEST 0001"


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


def case_data(damage: list[dict], annex: list[dict], *, make=MAKE, model=MODEL, ref="CL 90123") -> dict:
    data = blank()
    data["report_id"] = ref
    data["vehicle_info"].update(plate_number=PLATE, make=make, model=model, date_of_registration="12th January 2021")
    data["cover_page"]["date_of_survey"] = "3rd November 2026"
    data["damage_summary"] = damage
    data["damaged_parts"] = [it["part"] for it in damage]
    data["annex_a"] = {"items": annex}
    return data


def ai_draft(damage: list[dict]) -> dict:
    draft = blank()
    draft["vehicle_info"].update(plate_number=PLATE, make=MAKE, model=MODEL, color="Colourone")
    draft["damage_summary"] = damage
    draft["damaged_parts"] = [it["part"] for it in damage]
    return draft


def new_case(owner_id: str | None, data: dict, ai: dict | None, *, status: str = "confirmed") -> str:
    db = SessionLocal()
    try:
        r = Report(channel="manual", reporter_chat_id="dashboard", status=status, owner_id=owner_id,
                   data=data, photo_paths=[], ai_draft=ai)
        db.add(r)
        db.commit()
        return r.id
    finally:
        db.close()


def add_run(report_id: str, model: str, kind: str = "read", status: str = "ok") -> None:
    db = SessionLocal()
    try:
        db.add(AiRun(report_id=report_id, kind=kind, channel="dashboard", status=status, model_used=model,
                     started_at=datetime.now(timezone.utc)))
        db.commit()
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


def accuracy_row(report_id: str) -> AiAccuracy | None:
    db = SessionLocal()
    try:
        r = db.get(AiAccuracy, report_id)
        if r is not None:
            db.expunge(r)
        return r
    finally:
        db.close()


def signed_rows() -> int:
    """Accuracy rows of cases signed off now: what GET /admin/ai-accuracy counts."""
    db = SessionLocal()
    try:
        return db.query(AiAccuracy).join(Report, Report.id == AiAccuracy.report_id).filter(Report.status == "Signed Off").count()
    finally:
        db.close()


def count(model, *where) -> int:
    db = SessionLocal()
    try:
        return db.query(model).filter(*where).count()
    finally:
        db.close()


def learned_lines(report_id: str) -> list[PastAnnexLine]:
    db = SessionLocal()
    try:
        rows = db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == learn.case_ref(report_id)).order_by(PastAnnexLine.line_no).all()
        for r in rows:
            db.expunge(r)
        return rows
    finally:
        db.close()


def sign_off(client: TestClient, report_id: str):
    return client.post(f"/reports/{report_id}/sign-off", json={"terms_accepted": True, "open_items_ack": True})


# The firm's (made-up) past spreadsheet for this make and model: the rear
# bumper's kit, as an imported CL report would hold it.
def import_history() -> None:
    db = SessionLocal()
    try:
        case = PastCase(ref="CL 90001", plate="", make=MAKE, model=MODEL, make_model_raw=f"{MAKE} {MODEL}",
                        registered=None, surveyed="2026-01-05")
        case.lines = [
            PastLine(1, "list", "Rear bumper fascia", "Deformed", "Replace", 1.0, "1", 1000.0, 0.2, 800.0),
            PastLine(2, "list", "Rear bumper clips", "Necessary", "", 10.0, "10", 50.0, 0.2, 40.0),
        ]
        import_case(db, case, "CL 90001 made-up.xlsx")
        db.commit()
    finally:
        db.close()


# ================================================================= setup
def setup() -> None:
    init_db()
    import_history()
    W.admin_id = new_user("Ada", role="admin", may_sign_off=True)
    W.admin = signed_in(W.admin_id)
    W.plain_id = new_user("Pat")
    W.plain = signed_in(W.plain_id)
    W.staff_id = new_user("Sam", sees_all_cases=True, may_sign_off=True)
    W.staff = signed_in(W.staff_id)
    check(W.admin.get("/auth/me").status_code == 200, "an admin, a plain user and a see-all signer are signed in")


# ======================================================== compare: crafted
def compare_damage() -> None:
    ai = {"damage_summary": [
        item("Rear Bumper", remarks="Replace", condition="Deformed", ai_confidence="High", found_by=["overall", "zone:rear"]),
        item("Rear Bumper", remarks="Replace"),  # one part twice: counted once
        item("Left Tail Lamp", remarks="Replace", ai_confidence="High"),
        item("Boot Lid", remarks="Replace", ai_confidence="Low"),
        item("Door (side undetermined)"),
        item("Bonnet", ai_confidence="Medium", found_by=["zone:front"]),
        item("Other / Not Listed", other_part_name="rear air vent"),
    ]}
    final = {"damage_summary": [
        item("Rear Bumper", remarks="Repair", condition="Deformed"),
        item("Right Tail Lamp", remarks="Replace"),
        item("Left Front Door"),
        item("Boot Lid", remarks="replace", condition="Dented"),
        item("Left Headlamp"),
        item("Other / Not Listed", other_part_name="Rear air vent"),
    ]}
    d = accuracy.compare(ai, final)["damage"]
    check((d["ai_found"], d["final"]) == (6, 6), "the same part listed twice is one part", d)
    check((d["kept"], d["removed"], d["added"]) == (5, 1, 1), "kept 5, removed 1 (Bonnet), added 1 (Left Headlamp)", d)
    check(d["removed_parts"] == ["Bonnet"] and d["added_parts"] == ["Left Headlamp"], "the removed and added parts are named", d)
    check(d["side_changed"] == 1 and d["side_changed_parts"] == [["Left Tail Lamp", "Right Tail Lamp"]],
          "a tail lamp on the other side is kept, with its side changed", d)
    check(d["side_set"] == 1, "a door the AI left \"(side undetermined)\" and a person placed is kept, side set", d)
    check(d["remarks_changed"] == 1 and d["remarks_changed_parts"] == ["Rear Bumper"],
          "Replace -> Repair counts; replace vs Replace (letter case) doesn't", d)
    check(d["condition_filled"] == 1 and d["condition_changed"] == 0, "a condition the AI left blank and a person wrote is filled, not changed", d)
    check(d["by_confidence"]["High"] == {"kept": 2, "removed": 0} and d["by_confidence"]["Medium"] == {"kept": 0, "removed": 1}
          and d["by_confidence"]["Unrated"] == {"kept": 2, "removed": 0}, "kept and removed by the AI's own confidence", d["by_confidence"])
    check(d["by_read"] == {"overall": {"kept": 1, "removed": 0}, "zone:rear": {"kept": 1, "removed": 0},
                           "zone:front": {"kept": 0, "removed": 1}}, "kept and removed by which read found the part (found_by)", d.get("by_read"))

    # A pre-taxonomy case: free text in damaged_parts, no damage_summary.
    old = accuracy.compare({"damaged_parts": ["Tail lamp LH", "Bonnet"]}, {"damage_summary": [item("Left Tail Lamp")]})["damage"]
    check((old["kept"], old["removed"], old["added"]) == (1, 1, 0), "an old case's free-text parts are read as canonical ones", old)
    for ai_side, final_side in ((None, {}), ({}, None), ({"damage_summary": "nonsense", "annex_a": []}, {"damage_summary": [None, 3, {}]})):
        try:
            out = accuracy.compare(ai_side, final_side)
            ok = out["damage"]["kept"] == 0 and out["annex_a"]["final"] == 0
        except Exception as exc:  # noqa: BLE001
            ok, out = False, exc
        check(ok, f"missing or broken data counts as empty: compare({str(ai_side)[:30]}, {str(final_side)[:30]})", out)
    same = accuracy.compare(ai, ai)["damage"]
    check((same["kept"], same["removed"], same["added"], same["side_changed"]) == (6, 0, 0, 0), "the AI's draft against itself: everything kept", same)


def compare_annex() -> None:
    machine = [
        row("Rear bumper fascia", 1000, origin="history", prefilled=True, source_part="Rear Bumper", suggested_from="1 past case"),
        row("Rear bumper clips", 50, origin="history", prefilled=True, source_part="Rear Bumper"),
        row("Tail lamp", 0, origin="detection", source_part="Left Tail Lamp"),
        row("Bonnet assy", 900, origin="history", prefilled=True, source_part="Bonnet"),
    ]
    final = {"damage_summary": [item("Rear Bumper"), item("Right Tail Lamp"), item("Left Headlamp")], "annex_a": {"items": [
        row("Rear bumper fascia", 1150, source_part="Rear Bumper", suggested_from="1 past case"),  # price changed
        row("Rear bumper clips", 50, origin="history", prefilled=True, source_part="Rear Bumper"),  # untouched
        row("Tail lamp RH", 300, source_part="Right Tail Lamp"),  # same line, side now known, priced by a person
        row("Rear bumper absorber", 0, suggested_from="2 past cases"),  # picked from the suggestions panel
        row("Towing charges", 120),  # typed
        row("Headlamp LH", 0, origin="detection", source_part="Left Headlamp"),  # for a part a person added
        row("   ", 10),  # no description: not a line
    ]}}
    a = accuracy.compare({}, final, machine)["annex_a"]
    check((a["machine"], a["kept"], a["removed"], a["added"]) == (4, 3, 1, 3), "machine 4 lines: kept 3, removed 1, added 3", a)
    check((a["added_machine"], a["added_suggested"], a["added_typed"]) == (1, 1, 1),
          "added lines by where they came from: the machine's for an added part, the suggestions panel, typed", a)
    check((a["priced_kept"], a["price_changed"], a["price_filled"]) == (2, 1, 1),
          "of 2 kept lines the machine priced, 1 price changed; 1 unpriced line priced by a person", a)
    check(a["removed_lines"] == ["Bonnet assy"] and sorted(a["added_lines"]) == ["Rear bumper absorber", "Towing charges"],
          "removed and added lines are named (not the machine's lines for an added part)", a)
    check((a["final"], a["final_machine"], a["final_typed"]) == (6, 4, 2), "the signed-off lines by origin", a)
    none = accuracy.compare({}, final)["annex_a"]
    check(none["machine"] is None and none["kept"] is None and none["final"] == 6 and none["final_machine"] == 4,
          "without the machine's Annex A: only the signed-off lines' origins", none)
    legacy = accuracy.compare({}, {"damage_summary": [item("Boot Lid")], "annex_a": {"items": [
        {"part_name": "Boot Lid"}, {"part_name": "Old priced line", "workshop_est": 80}, {"part_name": "Prefilled", "prefilled": True}]}})["annex_a"]
    check((legacy["final_machine"], legacy["final_typed"]) == (2, 1), "rows from before origins: derived as prefill.origin_of does", legacy)


def compare_fields() -> None:
    ai = blank()
    ai["vehicle_info"].update(plate_number=PLATE, make="Makeone", color="Colourone", odometer_reading="001234 Km")
    ai["police_report"]["reported_to_police"] = True
    final = blank()
    final["vehicle_info"].update(plate_number=f" {PLATE} ", make="Maketwo", odometer_reading="001234 Km", paintwork_condition="Good",
                                 steering_condition="Damaged")
    final["insurance_details"]["insurer_name"] = "Insurer One"
    final["cover_page"]["date_of_survey"] = "1st November 2026"
    final["report_id"] = "CL 90999"
    f = accuracy.compare(ai, final)["fields"]
    check(f["ai_filled"] == 5, "5 fields the AI filled", f)
    check(f["by_key"].get("vehicle_info.plate_number") == "kept" and f["by_key"].get("vehicle_info.odometer_reading") == "kept",
          "the same value (spaces aside) is kept", f["by_key"])
    check(f["by_key"].get("vehicle_info.make") == "changed", "a different value is changed", f["by_key"])
    check(f["by_key"].get("vehicle_info.color") == "emptied" and f["by_key"].get("police_report.reported_to_police") == "emptied",
          "a value a person cleared (or a tick taken off) is emptied", f["by_key"])
    check(f["by_key"].get("insurance_details.insurer_name") == "filled" and f["by_key"].get("vehicle_info.steering_condition") == "filled",
          "a field the AI left blank and a person filled", f["by_key"])
    check(f["by_key"].get("vehicle_info.paintwork_condition") == "template", "the firm's usual static-check wording counts apart", f["by_key"])
    check("cover_page.date_of_survey" not in f["by_key"] and "report_id" not in f["by_key"],
          "the cover page's own section and Our Ref are not the AI's to fill", f["by_key"])
    check((f["kept"], f["changed"], f["emptied"], f["filled"], f["template"]) == (2, 1, 2, 2, 1), "the field counts add up", f)
    text = json.dumps(accuracy.compare(ai, final))
    check(not any(v in text for v in (PLATE, "Makeone", "Maketwo", "Insurer One", "Colourone", "001234")),
          "no value of the case in the answer: counts and keys only")


# ============================================================ sign-off
FINAL_DAMAGE = [item("Rear Bumper", remarks="Replace", condition="Deformed"), item("Left Tail Lamp", remarks="Replace")]
FINAL_ANNEX = [
    row("Rear bumper fascia", 1000, condition="Deformed", action="Replace", source_part="Rear Bumper", suggested_from="1 past case", main=True),
    row("Rear bumper clips", 50, condition="Necessary", qty=10.0, source_part="Rear Bumper", suggested_from="1 past case"),
    row("Tail lamp LH", 400, source_part="Left Tail Lamp", main=True),
    row("Reverse sensor", 380, qty=1.0, qty_text="1set", is_net_item=True, discount_pct=0.0, adjusted_cost=380.0),
]
AI_DAMAGE = [item("Rear Bumper", remarks="Replace", ai_confidence="High"), item("Bonnet", ai_confidence="Low")]


def signed_case(**kw) -> str:
    report_id = new_case(W.admin_id, case_data(FINAL_DAMAGE, [dict(r) for r in FINAL_ANNEX], **kw), ai_draft(AI_DAMAGE))
    add_run(report_id, "gemini-test-light", kind="sort")
    add_run(report_id, "gemini-test-light")
    add_run(report_id, "gemini-test-strong", kind="redraft")
    add_run(report_id, "gemini-test-failed", status="failed")
    return report_id


def sign_off_records() -> None:
    W.case = signed_case()
    r = sign_off(W.admin, W.case)
    check(r.status_code == 200 and stored(W.case).status == "Signed Off", "the case signs off", brief(r))
    row_ = accuracy_row(W.case)
    if not check(row_ is not None, "the sign-off stored its accuracy row"):
        return
    check(row_.version == accuracy.VERSION and row_.models == ["gemini-test-light", "gemini-test-strong"],
          "with the metric version and the models that read the case (not the sorter's, not a failed run's)", (row_.version, row_.models))
    sign_at = datetime.fromisoformat(stored(W.case).data["sign_off"]["signature_date"]).astimezone(timezone.utc).replace(tzinfo=None)
    check(abs((row_.signed_off_at - sign_at).total_seconds()) < 1, "signed_off_at is the signature's time", (row_.signed_off_at, sign_at))
    d, a = row_.metrics["damage"], row_.metrics["annex_a"]
    check((d["kept"], d["removed"], d["added"]) == (1, 1, 1) and d["removed_parts"] == ["Bonnet"] and d["added_parts"] == ["Left Tail Lamp"],
          "damage: the bumper kept, the bonnet removed, the tail lamp added", d)
    # The machine's Annex A from the AI's list: the bumper's kit from the
    # past case (fascia and clips, priced as at filing) and the bonnet's
    # unpriced row.
    check(a["machine"] == 3 and a["kept"] == 2 and a["removed"] == 1 and a["priced_kept"] == 2 and a["price_changed"] == 0,
          "Annex A: the machine's kit kept at its prices, the bonnet's line removed", a)
    check(a["added"] == 2 and a["added_machine"] == 1 and a["added_typed"] == 1, "the tail lamp's line and a typed line added", a)
    check(PLATE not in json.dumps(row_.metrics), "no plate in the stored metrics")
    W.first_signed_at = row_.signed_off_at

    # Reopen: the row goes. Edit, sign again: one row, the new answer.
    r = W.admin.post(f"/reports/{W.case}/reopen")
    check(r.status_code == 200 and accuracy_row(W.case) is None, "a reopen drops the row", brief(r))
    db = SessionLocal()
    try:
        rep = db.get(Report, W.case)
        data = dict(rep.data)
        data["damage_summary"] = [FINAL_DAMAGE[0]]
        data["annex_a"] = {"items": [dict(r) for r in FINAL_ANNEX[:2]]}
        rep.data = data
        db.commit()
    finally:
        db.close()
    r = sign_off(W.admin, W.case)
    again = accuracy_row(W.case)
    check(r.status_code == 200 and again is not None and count(AiAccuracy, AiAccuracy.report_id == W.case) == 1,
          "a re-sign-off stores one row again", brief(r))
    check(again is not None and again.metrics["damage"]["added"] == 0 and again.metrics["annex_a"]["added"] == 0
          and again.signed_off_at >= W.first_signed_at, "the row is the new sign-off's", again and again.metrics["damage"])

    # A case with no AI draft (filed before drafts were kept): signs off, no row.
    plain = new_case(W.admin_id, case_data(FINAL_DAMAGE, [dict(r) for r in FINAL_ANNEX]), None)
    r = sign_off(W.admin, plain)
    check(r.status_code == 200 and accuracy_row(plain) is None, "a case without an AI draft signs off and has no row", brief(r))
    W.no_draft = plain


def sign_off_never_fails() -> None:
    def boom(*_a, **_kw):
        raise RuntimeError("accuracy exploded")

    saved = main.accuracy.record
    main.accuracy.record = boom
    try:
        case = signed_case()
        r = sign_off(W.admin, case)
        check(r.status_code == 200 and stored(case).status == "Signed Off" and r.json().get("status") == "Signed Off",
              "the sign-off succeeds when the accuracy step raises", brief(r))
        check(accuracy_row(case) is None and len(learned_lines(case)) == 4, "no row, and the learning still ran", len(learned_lines(case)))
    finally:
        main.accuracy.record = saved
    saved = main.learn.learn_case
    main.learn.learn_case = boom
    try:
        case = signed_case()
        r = sign_off(W.admin, case)
        check(r.status_code == 200 and stored(case).status == "Signed Off" and accuracy_row(case) is not None,
              "the sign-off succeeds when the learning step raises, and the row is still stored", brief(r))
    finally:
        main.learn.learn_case = saved
    # Not this sign-off's any more (reopened meanwhile): nothing written.
    case = signed_case()
    db = SessionLocal()
    try:
        check(accuracy.record(db, case, "2026-01-01T00:00:00+00:00") is None and learn.learn_case(db, case, "x") is None,
              "a step for a sign-off that isn't the case's now writes nothing")
    finally:
        db.close()
    check(accuracy_row(case) is None and not learned_lines(case), "(no row, no lines)")


# ============================================================ learning
def learning() -> None:
    lines = learned_lines(W.case)
    check(len(lines) == 2, "the signed-off case's final Annex A is in the history", len(lines))
    first = lines[0] if lines else None
    if first is not None:
        check((first.make, first.model, first.make_model_raw, first.plate) == (MAKE, MODEL, f"{MAKE} {MODEL}", ""),
              "as the importer stores it: make and model split the importer's way, and no plate", (first.make, first.model, first.plate))
        check((first.source_file, first.surveyed, first.registered) == ("CL 90123", "2026-11-03", "2021-01-12"),
              "Our Ref as the spreadsheet would give it, the survey and registration dates as ISO", (first.source_file, first.surveyed, first.registered))
        check((first.item, first.section, first.discount, first.adjusted_cost, first.family) == ("Rear bumper fascia", "list", 0.2, 800.0, "Rear Bumper"),
              "a list line: the fraction discount and the adjusted cost (the usual price's figure)",
              (first.item, first.section, first.discount, first.adjusted_cost, first.family))
    full = signed_case(ref="CL-90124")
    sign_off(W.admin, full)
    nett = [ln for ln in learned_lines(full) if ln.section == "nett"]
    check(len(nett) == 1 and nett[0].discount is None and nett[0].adjusted_cost == 380.0 and nett[0].qty_text == "1set"
          and nett[0].line_no == 4, "a S/Nett line: no discount, the full figure, \"1set\" kept, numbered after the list items",
          [(n.item, n.discount, n.adjusted_cost, n.qty_text, n.line_no) for n in nett])
    check(learned_lines(full)[0].source_file == "CL 90124", "\"CL-90124\" is read as the importer reads a CL reference")

    # Re-sign-off replaced the lines (4 -> 2 for W.case, checked above);
    # a reopen removes them, a sign-off brings them back, a delete removes.
    r = W.admin.post(f"/reports/{full}/reopen")
    check(r.status_code == 200 and not learned_lines(full), "a reopen takes the case's lines out of the history", brief(r))
    sign_off(W.admin, full)
    check(len(learned_lines(full)) == 4, "signed off again: its lines are back, once")
    r = W.admin.delete(f"/reports/{full}")
    check(r.status_code == 200 and not learned_lines(full) and accuracy_row(full) is None,
          "a deleted case's lines and accuracy row go with it", brief(r))

    unknown = signed_case(model="")
    sign_off(W.admin, unknown)
    check(not learned_lines(unknown) and accuracy_row(unknown) is not None, "no model known: nothing learned (the accuracy row is still kept)")

    settings.learn_from_signed_off = False
    try:
        off = signed_case()
        sign_off(W.admin, off)
        check(not learned_lines(off), "learning off: a sign-off learns nothing")
        db = SessionLocal()
        try:
            check(not any(ln.case_ref.startswith("carlink:") for ln in past_lines(db)),
                  "learning off: what was learned before is left out of every suggestion")
        finally:
            db.close()
    finally:
        settings.learn_from_signed_off = True


def never_from_itself() -> None:
    # Another case of the same make and model gets the signed-off case's lines.
    db = SessionLocal()
    try:
        s = suggest(db, make=MAKE, model=MODEL, damaged_parts=["Left Tail Lamp"], existing_items=[])
        lines = [ln["name"] for g in s["groups"] for ln in g["lines"]]
        check("Tail lamp LH" in lines, "another case of the model is offered the learned \"Tail lamp LH\"", s)
        before = suggest(db, make=MAKE, model=MODEL, damaged_parts=["Rear Bumper"], existing_items=[])["cases_used"]
        with restricted(exclude_refs={learn.case_ref(W.case)}):
            without = suggest(db, make=MAKE, model=MODEL, damaged_parts=["Rear Bumper"], existing_items=[])["cases_used"]
        check(without == before - 1, "restricted() leaves a case's own lines out", (before, without))
    finally:
        db.close()

    # Its own suggestions panel never offers its own lines.
    r = W.admin.get(f"/reports/{W.case}/annex-suggestions")
    body = r.json() if r.status_code == 200 else {}
    db = SessionLocal()
    try:
        others = len({ln.case_ref for ln in past_lines(db) if ln.make == MAKE and ln.case_ref != learn.case_ref(W.case)})
    finally:
        db.close()
    check(r.status_code == 200 and body.get("cases_used") == others, "a signed-off case's own suggestions leave its lines out",
          (brief(r), others))

    # The rebuilt Annex A (the accuracy record) never takes the case's own
    # lines: a make no other case has, whose AI found what was signed off --
    # learned from itself, every line would come back priced.
    case = new_case(W.admin_id, case_data(FINAL_DAMAGE, [dict(r) for r in FINAL_ANNEX], make="Betamotors"), ai_draft(FINAL_DAMAGE))
    sign_off(W.admin, case)
    db = SessionLocal()
    try:
        machine = accuracy.machine_annex_a(db, db.get(Report, case))
        own = suggest(db, make="Betamotors", model=MODEL, damaged_parts=["Left Tail Lamp"], existing_items=[])
    finally:
        db.close()
    check(len(learned_lines(case)) == 4 and own["cases_used"] == 1, "(the case's lines are in the history, and only they for its make)", own)
    check(machine and all(float(r["workshop_est"] or 0) == 0 for r in machine) and not any(r.get("prefilled") for r in machine),
          "the machine's Annex A for a case is built without its own lines", machine)

    # A removal that failed: the lines of a case no longer signed off are left out.
    stale = signed_case()
    sign_off(W.admin, stale)
    db = SessionLocal()
    try:
        rep = db.get(Report, stale)
        rep.status = "confirmed"
        rep.data = {**rep.data, "sign_off": {**rep.data["sign_off"], "status": "Draft"}}
        db.commit()
        refs = {ln.case_ref for ln in past_lines(db)}
    finally:
        db.close()
    check(learned_lines(stale) and learn.case_ref(stale) not in refs, "lines left behind by a reopen are never suggested")
    r = W.admin.post(f"/reports/{stale}/annex-a/prefill")
    db = SessionLocal()
    try:
        data = db.get(Report, stale).data
    finally:
        db.close()
    tail = [x for x in data["annex_a"]["items"] if "tail lamp" in x["part_name"].lower()]
    check(r.status_code == 200 and tail and all(x["workshop_est"] == 400 for x in tail),
          "its own prefill keeps its own priced row and adds no copy of it", (brief(r), tail))

    # The same case imported from its spreadsheet too: counted once.
    db = SessionLocal()
    try:
        before = suggest(db, make=MAKE, model=MODEL, damaged_parts=["Rear Bumper"], existing_items=[])["cases_used"]
        case = PastCase(ref="CL 90123", plate="", make=MAKE, model=MODEL, make_model_raw=f"{MAKE} {MODEL}", registered=None, surveyed=None)
        case.lines = [PastLine(1, "list", "Rear bumper fascia", "Deformed", "Replace", 1.0, "1", 1000.0, 0.2, 800.0)]
        import_case(db, case, "CL 90123 export.xlsx")
        db.commit()
        after = suggest(db, make=MAKE, model=MODEL, damaged_parts=["Rear Bumper"], existing_items=[])["cases_used"]
        db.query(PastAnnexLine).filter(PastAnnexLine.case_ref == "CL 90123").delete()
        db.commit()
    finally:
        db.close()
    # W.case (Our Ref CL 90123) and the earlier no-model and failure cases
    # carry the same Our Ref: all of them give way to the spreadsheet.
    check(after < before + 1, "a case whose spreadsheet is imported too counts once", (before, after))


# ============================================================ endpoint
def endpoint() -> None:
    check(W.plain.get("/admin/ai-accuracy").status_code == 403, "a plain user gets 403")
    check(W.staff.get("/admin/ai-accuracy").status_code == 403, "a \"see all cases\" signer gets 403 too")
    r = W.admin.get("/admin/ai-accuracy")
    body = r.json() if r.status_code == 200 else {}
    check(r.status_code == 200, "an admin gets it", brief(r))
    signed = signed_rows()
    check(signed < count(AiAccuracy), "(a row is left over from the reopen that \"failed\" above)")
    check(body.get("totals", {}).get("cases") == signed and len(body.get("cases") or []) == signed,
          "every signed-off case with a row, in the totals and the case list; not a reopened case's left-over row",
          (body.get("totals", {}).get("cases"), signed))
    case = next((c for c in body.get("cases") or [] if c["report_id"] == W.case), None)
    check(case is not None and case["our_ref"] == "CL 90123" and case["vehicle"] == f"{MAKE} {MODEL}"
          and case["models"] == ["gemini-test-light", "gemini-test-strong"] and case["metrics"]["damage"]["kept"] == 1,
          "a case row: Our Ref, make and model, the models, the metrics", case)
    month = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m")
    check([m["month"] for m in body.get("months") or []] == [month] and body["months"][0]["cases"] == signed,
          "per month, in Singapore time", body.get("months"))
    check(body["months"] and body["months"][0].get("most_missed") == body.get("most_missed"),
          "each month names its own most missed parts", body.get("months"))
    rates = body.get("totals", {}).get("rates", {})
    d = body.get("totals", {}).get("damage", {})
    parts = d.get("kept", 0) + d.get("removed", 0) + d.get("added", 0)
    check(parts and abs(rates.get("parts_kept") - d["kept"] / parts) < 0.001 and rates.get("price_changed") is not None,
          "the rates are worked out from the sums", rates)
    check(any(m["part"] == "Bonnet" for m in body.get("most_removed") or []) and any(m["part"] == "Tail Lamp" for m in body.get("most_missed") or []),
          "most removed: Bonnet; most missed: Tail Lamp (both sides together)", (body.get("most_removed"), body.get("most_missed")))
    check(body.get("learning", {}).get("on") is True and body["learning"]["cases"] >= 1, "it says learning is on and how many cases taught it", body.get("learning"))
    check(PLATE not in r.text and "TEST0001" not in r.text, "no plate in the answer")
    tomorrow = (datetime.now(timezone(timedelta(hours=8))) + timedelta(days=1)).date().isoformat()
    later = W.admin.get(f"/admin/ai-accuracy?since={tomorrow}").json()
    check(later["totals"]["cases"] == 0 and later["cases"] == [] and later["months"] == [] and later["totals"]["rates"]["parts_kept"] is None,
          "?since filters; with no cases the totals are 0 and the rates null", later["totals"])
    check(W.admin.get("/admin/ai-accuracy?since=November").status_code == 400, "a since that isn't a date: 400")

    # A row left behind for a case that is no longer signed off isn't counted.
    db = SessionLocal()
    try:
        rep = db.get(Report, W.no_draft)
        rep.status = "confirmed"
        db.add(AiAccuracy(report_id=W.no_draft, signed_off_at=datetime.now(timezone.utc).replace(tzinfo=None), models=[], metrics={}, version=1))
        db.commit()
    finally:
        db.close()
    again = W.admin.get("/admin/ai-accuracy").json()
    check(again["totals"]["cases"] == signed, "a stray row of a reopened case isn't counted", again["totals"]["cases"])


# ============================================================ backfills
def backfills() -> None:
    # Cases signed off before this existed: no row, no lines.
    old = signed_case(ref="CL 90200")
    db = SessionLocal()
    try:
        rep = db.get(Report, old)
        rep.status = "Signed Off"
        rep.data = {**rep.data, "sign_off": {**rep.data["sign_off"], "status": "Signed Off",
                                             "signature_date": datetime.now(timezone.utc).isoformat()}}
        db.commit()
    finally:
        db.close()
    rows, lines = count(AiAccuracy), count(PastAnnexLine)
    STATEMENTS.clear()
    backfill_ai_accuracy.main([])
    backfill_learned_cases.main([])
    writes = [s for s in STATEMENTS if s.lstrip().split(" ", 1)[0].upper() in ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER")]
    check(not writes and count(AiAccuracy) == rows and count(PastAnnexLine) == lines and accuracy_row(old) is None and not learned_lines(old),
          "the dry runs write nothing", writes[:3])
    backfill_ai_accuracy.main(["--apply"])
    backfill_learned_cases.main(["--apply"])
    check(accuracy_row(old) is not None and len(learned_lines(old)) == 4, "--apply records the row and the lines")
    check(accuracy_row(W.no_draft) is None, "--apply drops the stray row of a case no longer signed off")
    check(accuracy_row(W.case).metrics["damage"]["kept"] == 1, "a case with today's row is left as it is")
    stale_learned = [ref for ref in learn.learned_cases(SessionLocal()) if stored(ref) is None or stored(ref).status != "Signed Off"]
    check(not stale_learned, "--apply takes out the lines of cases no longer signed off", stale_learned)


def no_ai() -> None:
    check(not GEMINI, "no Gemini call anywhere", GEMINI)


def main_() -> int:
    for name, fn in (
        ("setup", setup),
        ("compare: damage parts", compare_damage),
        ("compare: Annex A lines", compare_annex),
        ("compare: cover and parties fields", compare_fields),
        ("sign-off stores the row; a re-sign-off replaces it", sign_off_records),
        ("sign-off never fails for it", sign_off_never_fails),
        ("signed-off cases teach the parts history", learning),
        ("a case never gets suggestions from its own lines", never_from_itself),
        ("GET /admin/ai-accuracy", endpoint),
        ("the backfill scripts", backfills),
        ("no AI", no_ai),
    ):
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main_())
