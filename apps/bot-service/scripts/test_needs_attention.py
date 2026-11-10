"""The Overview's "Needs attention" fields on GET /reports (contract 5,
B2-API): intake_stage, intake_error_code, draft_deletes_at, unpriced_rows
and reopened.

    python scripts/test_needs_attention.py

Checked: every row carries all five; a draft's stage as the wizard shows it
(GET /intake/{id}) -- a run and a filing a restart cut off read as failed
there and here, one under way doesn't -- with its error code only when it
failed, and never the free-text error or whose run it was;
draft_deletes_at = last activity (session row, newest photo, draft made) +
7 days, None while a run or filing is under way, and the idle sweep
deleting at exactly that moment, not before; Annex A lines unpriced from
the stored rows, stale, blank and priced ones left out; reopened through
sign-off -> reopen -> sign-off again, and not for a reopen nobody signed
before; a plain user seeing only their own cases; an untaken public report
in no list, a taken one listed without its sender's details; draft
thumbnails in case photo order; and the same number of queries for a
longer list.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database, the PDF render replaced by a stub, Gemini by a fake that fails the
test if anything calls it, and every outside connection refused.
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
_tmp = Path(tempfile.mkdtemp(prefix="needs-attention-test-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
os.environ["STORAGE_DIR"] = str(STORAGE)
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaSyFakeCompanyKeyForNeedsAttention01"
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
from app.auth.core import SESSION_COOKIE, audit, create_session  # noqa: E402
from app.intake import cleanup, pipeline  # noqa: E402
from app.reports.db import SessionLocal, engine, init_db  # noqa: E402
from app.reports.models import AuditEntry, IntakePhoto, IntakeSession, PublicSubmission, Report, User, gen_id  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402


def _fake_render(data, photo_paths, output_path, report_id=None, **_kw):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(b"%PDF-1.4\n% test\n%%EOF\n")


main.render_pdf = _fake_render
app = main.app

# Every SQL statement the app sends, for the query count.
STATEMENTS: list[str] = []


@event.listens_for(engine, "before_cursor_execute")
def _seen(conn, cursor, statement, parameters, context, executemany):
    STATEMENTS.append(statement)


# ---------------------------------------------------------------- harness
PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}
BASE = "https://carlink.test"
FIELDS = ("intake_stage", "intake_error_code", "draft_deletes_at", "unpriced_rows", "reopened")
NOW = datetime.now(timezone.utc)
W = SimpleNamespace()  # people and fixtures shared by the sections


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
    return f"{r.status_code} {r.text[:200]}"


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


def case_data(**extra) -> dict:
    data = SecurityIncidentDraft(description="Rear-ended.", category=["Vehicle Collision or Damage"]).model_dump()
    data.update(extra)
    return data


def new_case(owner_id: str | None, **extra) -> str:
    """A filed case (status "confirmed")."""
    db = SessionLocal()
    try:
        r = Report(channel="manual", reporter_chat_id="dashboard", status="confirmed", owner_id=owner_id,
                   data=case_data(**extra), photo_paths=[])
        db.add(r)
        db.commit()
        return r.id
    finally:
        db.close()


def new_draft(owner_id: str | None, *, channel: str = "manual", session: bool = True, photos=(), data=None,
              **fields) -> str:
    """A draft (status "intake") with its progress row; `fields` go on the
    IntakeSession, `photos` are (position, created_at) pairs."""
    db = SessionLocal()
    try:
        r = Report(channel=channel, reporter_chat_id="dashboard", status="intake", owner_id=owner_id,
                   data=data or {}, photo_paths=[])
        db.add(r)
        db.flush()
        if session:
            db.add(IntakeSession(report_id=r.id, **fields))
        for i, (position, at) in enumerate(photos):
            db.add(IntakePhoto(report_id=r.id, position=position, original_name=f"p{i}.jpg",
                               path=str(STORAGE / "intake" / r.id / f"photo_{position}_{i}.jpg"), sha256=gen_id(),
                               created_at=at))
        db.commit()
        return r.id
    finally:
        db.close()


def set_times(report_id: str, *, created=None, updated=None) -> None:
    db = SessionLocal()
    try:
        if created is not None:
            db.query(Report).filter(Report.id == report_id).update({"created_at": created})
        if updated is not None:
            # Set explicitly, so the column's onupdate leaves it as given.
            db.query(IntakeSession).filter(IntakeSession.report_id == report_id).update({"updated_at": updated})
        db.commit()
    finally:
        db.close()


def listed(c: TestClient, scope: str | None = None) -> dict[str, dict]:
    r = c.get("/reports" + (f"?scope={scope}" if scope else ""))
    assert r.status_code == 200, brief(r)
    return {row["id"]: row for row in r.json()}


def at(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


# ---------------------------------------------------------------- sections
def setup() -> None:
    init_db()
    W.admin = new_user("Admin", role="admin")
    W.u1 = new_user("Una")
    W.u2 = new_user("Ben")
    W.a = signed_in(W.admin)
    W.p1 = signed_in(W.u1)
    W.p2 = signed_in(W.u2)
    check(W.a.get("/auth/me").status_code == 200, "admin signed in")


def every_row_has_fields() -> None:
    new_case(W.u1)
    new_draft(W.u1, stage="uploading")
    new_case(W.u2)
    rows = listed(W.a)
    check(len(rows) >= 3, f"the admin lists {len(rows)} cases")
    missing = [(rid, f) for rid, row in rows.items() for f in FIELDS if f not in row]
    check(not missing, "all five fields on every row", repr(missing[:5]))
    for rid, row in rows.items():
        if row["status"] != "intake":
            ok = (row["intake_stage"] is None and row["intake_error_code"] is None and row["draft_deletes_at"] is None
                  and isinstance(row["unpriced_rows"], int) and row["reopened"] is False)
            if not check(ok, "a filed case: no stage, code or delete date; unpriced_rows an int; not reopened", repr(row)):
                break
    leaked = [k for row in rows.values() for k in ("error", "error_by", "error_mine", "public_submission") if k in row]
    check(not leaked, "no free-text error, error_by or public submission keys in the list", repr(leaked))


def stages() -> None:
    # Failed with a code: the code shows; the free text and whose run it
    # was never do (the run was the admin's, so their id has no other
    # reason to be in this row).
    secret = "Google said: key AIzaSyQuoteOfAGoogleError is invalid, ask Mr Lim"
    failed = new_draft(W.u1, stage="failed", error=secret, error_code="ai_key_invalid", error_by=W.admin)
    # Ready, with a code left over: not failed, so no code.
    ready = new_draft(W.u1, stage="ready", error_code="ai_key_invalid")
    # An AI run a restart cut off: the row says "sorting", no thread runs it.
    cut_run = new_draft(W.u1, stage="sorting", stage_started_at=NOW - timedelta(minutes=3))
    # An AI run under way.
    running = new_draft(W.u1, stage="reading", stage_started_at=NOW)
    pipeline.reserve(running)
    # A filing a restart cut off (finalizing for over FINALIZE_STALE_AFTER).
    cut_filing = new_draft(W.u1, stage="finalizing", stage_started_at=NOW - timedelta(minutes=11))
    # A filing under way.
    filing = new_draft(W.u1, stage="finalizing", stage_started_at=NOW - timedelta(minutes=1))
    # A draft with no progress row (none should exist; it must not break the list).
    bare = new_draft(W.u1, session=False)
    try:
        rows = listed(W.p1)
        wizard = {rid: W.p1.get(f"/intake/{rid}").json() for rid in (failed, ready, cut_run, running, cut_filing, filing)}

        row = rows[failed]
        check(row["intake_stage"] == "failed" and row["intake_error_code"] == "ai_key_invalid",
              "a failed run: stage failed, its error code", repr(row))
        dumped = json.dumps(row)
        check(secret not in dumped and "AIzaSyQuote" not in dumped and W.admin not in dumped,
              "...never the free-text error or whose run it was", dumped[:300])
        check(rows[ready]["intake_stage"] == "ready" and rows[ready]["intake_error_code"] is None,
              "ready with a code left over: stage ready, no code", repr(rows[ready]))

        for rid, what in ((failed, "failed"), (ready, "ready"), (cut_run, "cut-off run"), (running, "running"),
                          (cut_filing, "cut-off filing"), (filing, "filing")):
            check(rows[rid]["intake_stage"] == wizard[rid]["stage"],
                  f"{what}: the stage the wizard shows ({wizard[rid]['stage']})", f"list {rows[rid]['intake_stage']!r}")

        check(rows[cut_run]["intake_stage"] == "failed" and rows[cut_run]["intake_error_code"] is None
              and wizard[cut_run]["error_code"] is None,
              "a run a restart cut off reads as failed with no code (Retry), as in the wizard", repr(rows[cut_run]))
        check(rows[cut_run]["draft_deletes_at"] is not None, "...and the idle sweep applies to it")
        check(rows[running]["intake_stage"] == "reading" and rows[running]["draft_deletes_at"] is None,
              "a run under way: reading, never deleted meanwhile", repr(rows[running]))
        check(rows[cut_filing]["intake_stage"] == "failed"
              and rows[cut_filing]["intake_error_code"] == pipeline.FILING_INTERRUPTED == wizard[cut_filing]["error_code"],
              "a filing a restart cut off: failed, code filing_interrupted, as in the wizard", repr(rows[cut_filing]))
        check(rows[cut_filing]["draft_deletes_at"] is not None, "...and the idle sweep applies to it")
        check(rows[filing]["intake_stage"] == "finalizing" and rows[filing]["intake_error_code"] is None
              and rows[filing]["draft_deletes_at"] is None,
              "a filing under way: finalizing, no code, never deleted meanwhile", repr(rows[filing]))
        check(rows[bare]["intake_stage"] is None and rows[bare]["draft_deletes_at"] is None,
              "a draft with no progress row: no stage, no delete date", repr(rows[bare]))
        check(all(rows[rid]["unpriced_rows"] == 0 and rows[rid]["reopened"] is False
                  for rid in (failed, ready, cut_run, running, cut_filing, filing, bare)),
              "drafts: unpriced_rows 0, reopened False")
    finally:
        pipeline.release(running)
    check(listed(W.p1)[running]["intake_stage"] == "failed", "once that run's thread is gone it reads as failed")


def deletes_at() -> None:
    # Last activity from the newest photo.
    W.by_photo = new_draft(W.u1, stage="ready", photos=[(0, NOW - timedelta(days=20, hours=1)), (1, NOW - timedelta(days=20))])
    set_times(W.by_photo, created=NOW - timedelta(days=22), updated=NOW - timedelta(days=21))
    # From the session row (an autosave, a run).
    by_session = new_draft(W.u1, stage="ready", photos=[(0, NOW - timedelta(days=11))])
    set_times(by_session, created=NOW - timedelta(days=12), updated=NOW - timedelta(days=10))
    # From when the draft was made.
    by_created = new_draft(W.u1, stage="uploading")
    set_times(by_created, created=NOW - timedelta(days=5), updated=NOW - timedelta(days=6))
    rows = listed(W.p1)
    W.photo_due = NOW - timedelta(days=20) + timedelta(days=7)
    check(at(rows[W.by_photo]["draft_deletes_at"]) == W.photo_due,
          "newest photo + 7 days", f"{rows[W.by_photo]['draft_deletes_at']} != {W.photo_due.isoformat()}")
    check(at(rows[by_session]["draft_deletes_at"]) == NOW - timedelta(days=3),
          "session row changed + 7 days", rows[by_session]["draft_deletes_at"])
    check(at(rows[by_created]["draft_deletes_at"]) == NOW + timedelta(days=2),
          "draft made + 7 days", rows[by_created]["draft_deletes_at"])
    check(rows[by_created]["draft_deletes_at"].endswith("+00:00"), "an ISO UTC string", rows[by_created]["draft_deletes_at"])
    check(cleanup.DRAFT_MAX_IDLE == timedelta(days=7), "the sweep's DRAFT_MAX_IDLE is 7 days")

    # The sweep keeps it until that moment and deletes it then.
    gone = cleanup.sweep(now=W.photo_due - timedelta(seconds=1))["drafts"]
    check(W.by_photo not in gone, "the sweep a second before draft_deletes_at keeps it", repr(gone))
    gone = cleanup.sweep(now=W.photo_due)["drafts"]
    check(gone == [W.by_photo], "the sweep at draft_deletes_at deletes it (and nothing else here)", repr(gone))
    check(W.by_photo not in listed(W.p1), "...and it has left the list")


def unpriced() -> None:
    items = [
        {"part_name": "Rear bumper", "workshop_est": 450.0, "origin": "typed", "stale": False},
        {"part_name": "Boot lid", "workshop_est": 0.0, "origin": "detection", "source_part": "Boot Lid", "stale": False},
        {"part_name": "Bumper clips", "workshop_est": None, "origin": "history", "stale": False},
        # Its part is no longer damaged: waits on Keep or Remove, not a price.
        {"part_name": "Tail lamp LH", "workshop_est": 0.0, "origin": "typed", "source_part": "Tail Lamp", "stale": True},
        # A blank line, as the Annex A tab leaves it out.
        {"part_name": "  ", "workshop_est": 0.0, "origin": "typed"},
        # Older rows with no origin or stale flag at all.
        {"part_name": "Rear panel", "workshop_est": 0},
        {"part_name": "Rear panel paint", "workshop_est": 300},
    ]
    W.priced_case = new_case(W.u1, annex_a={"items": items})
    clean = new_case(W.u1, annex_a={"items": [{"part_name": "Rear bumper", "workshop_est": 120.0}]})
    empty = new_case(W.u1)
    odd = new_case(W.u1, annex_a=["not", "a", "table"])
    bad_value = new_case(W.u1, annex_a={"items": [{"part_name": "Rear bumper", "workshop_est": "tbc"}, "junk"]})
    draft = new_draft(W.u1, stage="ready", data={"annex_a": {"items": [{"part_name": "Boot lid", "workshop_est": 0}]}})
    db = SessionLocal()
    try:
        before = json.dumps(db.get(Report, W.priced_case).data, sort_keys=True)
    finally:
        db.close()
    rows = listed(W.p1)
    check(rows[W.priced_case]["unpriced_rows"] == 3, "3 unpriced: stale, blank and priced lines left out",
          repr(rows[W.priced_case]["unpriced_rows"]))
    check(rows[clean]["unpriced_rows"] == 0, "every line priced: 0")
    check(rows[empty]["unpriced_rows"] == 0, "no Annex A lines: 0")
    check(rows[odd]["unpriced_rows"] == 0, "an Annex A that isn't a table: 0, the list still answers")
    check(rows[bad_value]["unpriced_rows"] == 1, "an estimate that isn't a number counts as none", repr(rows[bad_value]))
    check(rows[draft]["unpriced_rows"] == 0, "a draft: 0 whatever its data holds")
    db = SessionLocal()
    try:
        after = json.dumps(db.get(Report, W.priced_case).data, sort_keys=True)
    finally:
        db.close()
    check(before == after, "listing leaves the stored rows as they were (no sync)")


def reopened() -> None:
    case = new_case(W.u1)

    def flag() -> bool:
        return listed(W.a)[case]["reopened"]

    check(flag() is False, "never signed off: not reopened")
    r = W.a.post(f"/reports/{case}/sign-off", json={})
    check(r.status_code == 200, "signed off", brief(r))
    check(flag() is False, "signed off: not reopened")
    r = W.a.post(f"/reports/{case}/reopen")
    check(r.status_code == 200, "reopened", brief(r))
    check(flag() is True, "signed off then reopened: reopened")
    check(listed(W.p1)[case]["reopened"] is True, "...the owner's list says so too")
    r = W.a.post(f"/reports/{case}/sign-off", json={})
    check(r.status_code == 200, "signed off again", brief(r))
    check(flag() is False, "reopen -> sign off again: not reopened")
    W.a.post(f"/reports/{case}/reopen")
    check(flag() is True, "reopened a second time: reopened")

    never_signed = new_case(W.u1)
    r = W.a.post(f"/reports/{never_signed}/reopen")
    check(r.status_code == 200, "a case nobody signed can be 'reopened' too", brief(r))
    check(listed(W.a)[never_signed]["reopened"] is False, "...which isn't a reopen: nothing was signed before it")

    # Newer by entry, not by timestamp: entries written in the same moment
    # still count in the order they were written.
    same_second = new_case(W.u1)
    stamp = datetime(2026, 11, 1, 9, 0, 0, tzinfo=timezone.utc)

    def add_entry(action: str) -> None:
        db = SessionLocal()
        try:
            db.add(AuditEntry(report_id=same_second, user_id=W.admin, user_name="Admin", action=action, created_at=stamp))
            # Other history after it changes nothing.
            audit(db, None, "edited", same_second)
            db.commit()
        finally:
            db.close()

    add_entry("signed_off")
    add_entry("reopened")
    check(listed(W.a)[same_second]["reopened"] is True, "signed off and reopened in the same second: reopened")
    add_entry("signed_off")
    check(listed(W.a)[same_second]["reopened"] is False, "...and signed off again in that second: not reopened")


def own_cases_only() -> None:
    mine_case = new_case(W.u1)
    theirs = new_case(W.u2)
    theirs_draft = new_draft(W.u2, stage="failed", error_code="ai_key_invalid")
    rows = listed(W.p1)
    check(mine_case in rows and theirs not in rows and theirs_draft not in rows,
          "a plain user lists their own cases and drafts, nobody else's")
    check(all((row["owner"] or {}).get("id") == W.u1 for row in rows.values()), "every row is theirs")
    check(W.p1.get("/reports?scope=all").status_code == 403, "?scope=all refused (403)")
    rows2 = listed(W.p2)
    check(theirs_draft in rows2 and rows2[theirs_draft]["intake_error_code"] == "ai_key_invalid"
          and mine_case not in rows2, "the other user: their own, with the fields")


def public_reports() -> None:
    sender = {"submitter_name": "Ah Seng Tan", "submitter_phone": "+6591234567",
              "statement": "Lorry hit my car at Jurong West St 42", "telegram_name": "Seng T",
              "telegram_username": "ahseng_42", "when_where": "Jurong West St 42, 8am"}

    def submission(report_id: str, taken: bool) -> None:
        db = SessionLocal()
        try:
            db.add(PublicSubmission(
                report_id=report_id, reference=f"TG-261109-{gen_id()[:4].upper()}", submitted_at=NOW,
                expires_at=NOW + timedelta(days=30), consent_at=NOW, consent_version="v1",
                submitter_role="driver", phone_kind="sg_mobile", plate="SKA1234Z", telegram_user_id="777000",
                taken_at=NOW if taken else None, taken_by=W.u1 if taken else None, **sender,
            ))
            db.commit()
        finally:
            db.close()

    untaken = new_draft(None, channel="telegram_public", stage="ready", photos=[(0, NOW)])
    submission(untaken, taken=False)
    taken = new_draft(W.u1, channel="telegram_public", stage="ready", photos=[(0, NOW)])
    submission(taken, taken=True)
    for scope in (None, "all", "mine", "unassigned"):
        check(untaken not in listed(W.a, scope), f"an untaken public report is in no list (admin, scope={scope})")
    check(untaken not in listed(W.p1), "...nor a plain user's")
    rows = listed(W.p1)
    check(taken in rows and rows[taken]["draft_deletes_at"] is not None,
          "a taken public draft is listed, on the 7-day idle rule", repr(rows.get(taken)))
    dumped = json.dumps(rows[taken])
    leaked = [v for v in sender.values() if v in dumped] + [v for v in ("777000", "+65") if v in dumped]
    check(not leaked, "...without anything of its sender's", repr(leaked))


def thumbnails() -> None:
    # Uploaded out of order: the first photo is position 0, not the oldest.
    rid = new_draft(W.u1, stage="ready", photos=[(2, NOW - timedelta(minutes=9)), (0, NOW - timedelta(minutes=1)),
                                                  (1, NOW - timedelta(minutes=5))])
    url = listed(W.p1)[rid]["thumbnail_url"] or ""
    check(url.startswith(f"/files/intake/{rid}/photo_0_"), "a draft's thumbnail is its first photo in case order", url)
    empty = new_draft(W.u1, stage="uploading")
    check(listed(W.p1)[empty]["thumbnail_url"] is None, "no photo: no thumbnail")


def query_count() -> None:
    def statements_for_list() -> list[str]:
        STATEMENTS.clear()
        listed(W.a, "all")
        # The sign-in check (and its last-seen write, at most once a minute)
        # is the middleware's, not the list's.
        return [s for s in STATEMENTS if "auth_sessions" not in s]

    small = statements_for_list()
    for i in range(4):
        new_draft(W.u1 if i % 2 else W.u2, stage="failed" if i % 2 else "ready", error_code="ai_key_invalid",
                  photos=[(0, NOW), (1, NOW)])
        case = new_case(W.u2, annex_a={"items": [{"part_name": "Door", "workshop_est": 0}]})
        W.a.post(f"/reports/{case}/sign-off", json={})
        W.a.post(f"/reports/{case}/reopen")
    taken = new_draft(W.u2, channel="telegram_public", stage="ready")
    db = SessionLocal()
    try:
        db.add(PublicSubmission(report_id=taken, reference="TG-261109-QQQQ", submitted_at=NOW, expires_at=NOW + timedelta(days=30),
                                consent_at=NOW, consent_version="v1", submitter_name="X", submitter_role="driver",
                                submitter_phone="+6590000000", phone_kind="sg_mobile", statement="x"))
        db.commit()
    finally:
        db.close()
    large = statements_for_list()
    total = len(listed(W.a, "all"))
    check(len(small) == len(large), f"the same number of queries for a longer list ({len(small)} -> {len(large)}, {total} cases)",
          "\n         ".join(s.split("\n")[0][:100] for s in large))
    check(len(large) <= 8, f"{len(large)} queries in all for the list", "\n         ".join(s[:100] for s in large))
    photo_queries = [s for s in large if "intake_photos" in s]
    check(len(photo_queries) == 2, "drafts' photos: two queries (first photo, newest photo), not one per draft",
          repr(len(photo_queries)))


def no_ai() -> None:
    check(not GEMINI, "no Gemini call anywhere in this test", repr(GEMINI))


def main_() -> int:
    for name, fn in (
        ("setup", setup),
        ("all five fields on every row", every_row_has_fields),
        ("intake_stage and intake_error_code as the wizard shows them", stages),
        ("draft_deletes_at: the idle sweep's own rule", deletes_at),
        ("unpriced_rows from the stored Annex A", unpriced),
        ("reopened: sign-off -> reopen -> sign-off", reopened),
        ("a plain user sees only their own cases", own_cases_only),
        ("public reports", public_reports),
        ("draft thumbnails in case photo order", thumbnails),
        ("a constant number of queries", query_count),
        ("no AI", no_ai),
    ):
        run_section(name, fn)
    print(f"\ntest_needs_attention: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        for section, label in FAILURES:
            print(f"  FAIL [{section}] {label}")
        return 1
    print("\nALL NEEDS-ATTENTION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
