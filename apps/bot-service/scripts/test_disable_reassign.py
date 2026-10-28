"""Tests for release P2 "Settings split", API side: an admin can't change
their own role; disabling an account says what happens to their open cases
(keep / unassign / move to someone), all in one transaction; and POST
/reports/reassign ("Move their cases" on the Users screen).

    python scripts/test_disable_reassign.py

Spec: plans/2026-10-26-roles-ai-telegram/p2_p3_build_brief.md, "P2 API
contract". Open is what GET /users counts as open (owners.case_counts):
not signed off and not an unfinished intake draft.

Offline and self-contained: a temporary STORAGE_DIR and database. Cases are
written straight into the database, so nothing renders or calls Gemini.
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
# Response bodies can hold any character; never let printing one crash the run
# (a redirected stdout on Windows is cp1252).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
_tmp = Path(tempfile.mkdtemp(prefix="disable-reassign-test-"))
(_tmp / "storage").mkdir()
# Forced, not setdefault: never a real STORAGE_DIR or database from the environment.
os.environ["STORAGE_DIR"] = str(_tmp / "storage")
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""

from fastapi.testclient import TestClient  # noqa: E402

import app.api.auth as auth_api  # noqa: E402
from app.api.main import app  # noqa: E402
from app.auth.core import utc_now  # noqa: E402
from app.reports import owners  # noqa: E402
from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import AuditEntry, AuthSession, Report, User  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

BASE = "https://carlink.test"
OWN_ROLE = "You can't change your own role."

PASSED = [0]
FAILURES: list[str] = []
_section = {"name": "setup"}


class Stop(Exception):
    """A precondition failed; the rest of the section can't run."""


def check(cond, label: str, why: str = "") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
        return True
    FAILURES.append(f"{_section['name']}: {label}" + (f"  [{why}]" if why else ""))
    print(f"    FAIL {label}" + (f"\n           got: {why}" if why else ""))
    return False


def need(cond, label: str, why: str = "") -> None:
    if not check(cond, label, why):
        raise Stop(label)


def run_section(name: str, fn) -> None:
    _section["name"] = name
    print(f"\n{name}")
    try:
        fn()
    except Stop as exc:
        print(f"    ---  rest of this section skipped: {exc}")
    except Exception as exc:
        FAILURES.append(f"{name}: section stopped by {type(exc).__name__}: {exc}")
        print(f"    FAIL section stopped by {type(exc).__name__}: {exc}")
        traceback.print_exc()


def browser() -> TestClient:
    return TestClient(app, base_url=BASE, raise_server_exceptions=False)


def jd(r) -> dict:
    try:
        body = r.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def code_of(r):
    return jd(r).get("code")


def brief(r) -> str:
    return f"{r.status_code} {(r.text or '').replace(chr(10), ' ')[:220]}"


def person(name: str, username: str, role: str = "user", **ticks):
    """A signed-in account with its own browser."""
    c = browser()
    user_id = sign_in(c, role=role, name=name, username=username, **ticks)
    return SimpleNamespace(c=c, id=user_id, name=name, username=username)


def cases(p, open: int = 0, signed: int = 0, legacy_signed: int = 0, drafts: int = 0):
    """Cases p owns, written straight into the database: open ("confirmed"),
    signed off ("Signed Off", and the older "signed_off") and unfinished
    intake drafts ("intake")."""
    statuses = ["confirmed"] * open + ["Signed Off"] * signed + ["signed_off"] * legacy_signed + ["intake"] * drafts
    ids = []
    db = SessionLocal()
    try:
        for i, status in enumerate(statuses):
            report = Report(
                channel="manual", reporter_chat_id="dashboard", status=status, owner_id=p.id,
                data={"description": f"Case {i}", "category": ["Vehicle Collision or Damage"],
                      "vehicle_info": {"plate_number": f"SKA{i:04d}A", "make": "Honda", "model": "Vezel"},
                      "sign_off": {"status": "Signed Off" if status in owners.SIGNED_OFF_STATUSES else "Draft"}},
            )
            db.add(report)
            db.flush()
            db.add(AuditEntry(report_id=report.id, user_id=p.id, user_name=p.name, action="created", created_at=utc_now()))
            ids.append(report.id)
        db.commit()
    finally:
        db.close()
    n_signed = signed + legacy_signed
    return SimpleNamespace(open=ids[:open], signed=ids[open:open + n_signed], drafts=ids[open + n_signed:], all=ids)


def owners_of(ids) -> dict:
    db = SessionLocal()
    try:
        return dict(db.query(Report.id, Report.owner_id).filter(Report.id.in_(list(ids))).all())
    finally:
        db.close()


def assigned_entries(ids) -> list:
    """(case, who, detail) of every "assigned" history entry on these cases."""
    db = SessionLocal()
    try:
        return db.query(AuditEntry.report_id, AuditEntry.user_name, AuditEntry.detail).filter(
            AuditEntry.report_id.in_(list(ids)), AuditEntry.action == "assigned").all()
    finally:
        db.close()


def last_disabled_detail(user_id: str) -> str | None:
    db = SessionLocal()
    try:
        email = db.get(User, user_id).email
        row = (db.query(AuditEntry).filter(AuditEntry.action == "user_disabled", AuditEntry.detail.startswith(email))
               .order_by(AuditEntry.id.desc()).first())
        return row.detail if row else None
    finally:
        db.close()


def email_of(p) -> str:
    db = SessionLocal()
    try:
        return db.get(User, p.id).email
    finally:
        db.close()


def snapshot(p, ids) -> tuple:
    """Everything disabling or moving could change: the account, its
    sessions, who owns each case, and the history (any new entry at all)."""
    db = SessionLocal()
    try:
        user = db.get(User, p.id)
        return (
            bool(user.active),
            user.role,
            db.query(AuthSession).filter(AuthSession.user_id == p.id).count(),
            tuple(sorted(dict(db.query(Report.id, Report.owner_id).filter(Report.id.in_(list(ids))).all()).items())),
            db.query(AuditEntry).count(),
        )
    finally:
        db.close()


def signed_in(p) -> bool:
    return p.c.get("/auth/me").status_code == 200


def counts_of(p) -> dict | None:
    rows = W.admin.c.get("/users").json()
    return next((u.get("cases") for u in rows if u.get("id") == p.id), None)


def session_count(p) -> int:
    db = SessionLocal()
    try:
        return db.query(AuthSession).filter(AuthSession.user_id == p.id).count()
    finally:
        db.close()


W = SimpleNamespace()


# ================================================================= setup
def setup() -> None:
    W.admin = person("Ada Admin", "adaboss", role="admin")
    W.admin_name = "Ada Admin (@adaboss)"  # how the history names her
    W.admin2 = person("Ben Backup", "benboss", role="admin")
    W.target = person("Tina Target", "tina.t")
    W.to_tina = "Assigned to Tina Target (@tina.t)"
    W.plain = person("Pat Plain", "pat.p")
    W.staff = person("Sue Staff", "sue.s", sees_all_cases=True, may_sign_off=True, ai_company_key_allowed=True)
    W.disabled = person("Dan Disabled", "dan.d")
    r = W.admin.c.patch(f"/users/{W.disabled.id}", json={"active": False})
    need(r.status_code == 200 and jd(r).get("active") is False, "Dan is disabled, for the checks that need a disabled account", brief(r))


# ================================================================= own role
def own_role() -> None:
    A, B = W.admin, W.admin2
    print("  with another active admin about, which used to make it allowed")
    before = snapshot(A, [])
    r = A.c.patch(f"/users/{A.id}", json={"role": "user"})
    check(r.status_code == 403 and code_of(r) == "not_allowed" and jd(r).get("detail") == OWN_ROLE,
          f"an admin changing their own role -> 403 not_allowed, \"{OWN_ROLE}\"", brief(r))
    check(jd(A.c.get("/auth/me")).get("role") == "admin" and snapshot(A, []) == before, "...and they are still an admin, nothing recorded")
    r = A.c.patch(f"/users/{A.id}", json={"role": "user", "qualifications": "Sneaky change"})
    check(r.status_code == 403 and jd(A.c.get("/auth/me")).get("qualifications") != "Sneaky change",
          "an own-role change with a profile change in the same request: 403, the profile change isn't saved either", brief(r))
    r = A.c.patch(f"/users/{A.id}", json={"role": "admin", "qualifications": "MIMI"})
    check(r.status_code == 200 and jd(r).get("qualifications") == "MIMI" and jd(r).get("role") == "admin",
          "their own role sent back unchanged (the Edit dialog's Save) is fine", brief(r))
    r = B.c.patch(f"/users/{B.id}", json={"role": "user"})
    check(r.status_code == 403 and code_of(r) == "not_allowed", "the second admin can't change their own role either", brief(r))
    r = A.c.patch(f"/users/{B.id}", json={"role": "user"})
    check(r.status_code == 200 and jd(r).get("role") == "user", "an admin still changes ANOTHER admin's role", brief(r))
    r = A.c.patch(f"/users/{B.id}", json={"role": "admin"})
    check(r.status_code == 200 and jd(r).get("role") == "admin", "...and back", brief(r))


# ================================================================= disabling
def disable_keep() -> None:
    admin = W.admin
    K = person("Kim Keep", "kim.k")
    c = cases(K, open=2, signed=1, legacy_signed=1, drafts=1)
    want = {"open": 2, "signed_off": 2, "drafts": 1}
    need(counts_of(K) == want, f"GET /users counts Kim's cases {want} (what the Disable dialog shows)", repr(counts_of(K)))
    r = admin.c.patch(f"/users/{K.id}", json={"active": False, "open_cases": "keep"})
    check(r.status_code == 200 and jd(r).get("active") is False and jd(r).get("cases") == want,
          "disable with open_cases \"keep\" -> 200, inactive, the same counts", brief(r))
    check(all(o == K.id for o in owners_of(c.all).values()), "...every case is still Kim's", repr(owners_of(c.all)))
    check(not assigned_entries(c.all), "...no case history entries", repr(assigned_entries(c.all)))
    check(not signed_in(K) and session_count(K) == 0, "...and her sessions ended at once (401)", f"{session_count(K)} sessions left")
    check(last_disabled_detail(K.id) == email_of(K), "...the user_disabled entry is just her email", repr(last_disabled_detail(K.id)))
    W.kept = (K, c)

    print("  no open_cases at all: the same as keep")
    D = person("Del Default", "del.d")
    cd = cases(D, open=1, drafts=1)
    r = admin.c.patch(f"/users/{D.id}", json={"active": False})
    check(r.status_code == 200 and all(o == D.id for o in owners_of(cd.all).values()) and not signed_in(D),
          "disable with just {active: false} -> 200, cases stay, signed out", brief(r))


def disable_unassign() -> None:
    admin = W.admin
    U = person("Una Unassign", "una.u")
    c = cases(U, open=3, signed=1, drafts=1)
    r = admin.c.patch(f"/users/{U.id}", json={"active": False, "open_cases": "unassign"})
    check(r.status_code == 200 and jd(r).get("active") is False and jd(r).get("cases") == {"open": 0, "signed_off": 1, "drafts": 1},
          "disable with open_cases \"unassign\" -> 200, inactive, open count 0", brief(r))
    got = owners_of(c.all)
    check(all(got[i] is None for i in c.open), "...her 3 open cases are Unassigned", repr(got))
    check(all(got[i] == U.id for i in c.signed + c.drafts), "...the signed-off case and the draft stay hers", repr(got))
    entries = assigned_entries(c.all)
    check(sorted(e.report_id for e in entries) == sorted(c.open) and all(e.detail == "Unassigned" and e.user_name == W.admin_name for e in entries),
          "...each moved case got one history entry: assigned, \"Unassigned\", by the admin", repr(entries))
    check(not signed_in(U) and session_count(U) == 0, "...and her sessions ended", f"{session_count(U)} sessions left")
    check(last_disabled_detail(U.id) == f"{email_of(U)}; 3 open cases: Unassigned",
          "...the user_disabled entry says what happened to her cases", repr(last_disabled_detail(U.id)))
    listed = {row["id"] for row in admin.c.get("/reports?scope=unassigned").json()}
    check(set(c.open) <= listed, "...and the admin finds them in the Unassigned list")


def disable_move() -> None:
    admin, T = W.admin, W.target
    M = person("Mo Mover", "mo.m")
    c = cases(M, open=2, signed=1, drafts=1)
    t_open = (counts_of(T) or {}).get("open", 0)
    need(T.c.get(f"/reports/{c.open[0]}").status_code == 404, "before: Tina can't open Mo's case (404)")
    r = admin.c.patch(f"/users/{M.id}", json={"active": False, "open_cases": "move", "open_cases_to": T.id})
    check(r.status_code == 200 and jd(r).get("active") is False and jd(r).get("cases") == {"open": 0, "signed_off": 1, "drafts": 1},
          "disable with open_cases \"move\" to Tina -> 200, inactive, open count 0", brief(r))
    got = owners_of(c.all)
    check(all(got[i] == T.id for i in c.open), "...his 2 open cases are Tina's", repr(got))
    check(all(got[i] == M.id for i in c.signed + c.drafts), "...the signed-off case and the draft stay his", repr(got))
    entries = assigned_entries(c.all)
    check(sorted(e.report_id for e in entries) == sorted(c.open) and all(e.detail == W.to_tina and e.user_name == W.admin_name for e in entries),
          f"...each moved case got one history entry: assigned, \"{W.to_tina}\", by the admin", repr(entries))
    history = jd(admin.c.get(f"/reports/{c.open[0]}")).get("history") or [{}]
    check(history[-1].get("action") == "assigned" and history[-1].get("detail") == W.to_tina,
          "...and the case screen's history shows it", repr(history[-1]))
    check(T.c.get(f"/reports/{c.open[0]}").status_code == 200, "...Tina can open it now")
    check((counts_of(T) or {}).get("open") == t_open + 2, "...GET /users counts 2 more open cases for Tina", repr(counts_of(T)))
    check(not signed_in(M) and session_count(M) == 0, "...and his sessions ended", f"{session_count(M)} sessions left")
    check(last_disabled_detail(M.id) == f"{email_of(M)}; 2 open cases: {W.to_tina}",
          "...the user_disabled entry says where his cases went", repr(last_disabled_detail(M.id)))


def disable_refusals() -> None:
    admin, T, D = W.admin, W.target, W.disabled
    R = person("Rae Refused", "rae.r")
    c = cases(R, open=2, signed=1, drafts=1)
    move = {"active": False, "open_cases": "move"}
    attempts = [
        ("move without open_cases_to", move, 422, "owner_required"),
        ("move with an empty open_cases_to", {**move, "open_cases_to": ""}, 422, "owner_required"),
        ("move to the person being disabled", {**move, "open_cases_to": R.id}, 422, "same_owner"),
        ("move to an unknown account", {**move, "open_cases_to": "nosuchuser0"}, 404, None),
        ("move to a disabled account", {**move, "open_cases_to": D.id}, 409, "owner_inactive"),
        ("open_cases without active: false", {"open_cases": "unassign"}, 422, None),
        ("open_cases with active: true", {"active": True, "open_cases": "move", "open_cases_to": T.id}, 422, None),
        ("an unknown open_cases choice", {"active": False, "open_cases": "delete"}, 422, None),
        ("a valid move, but an email that's taken", {**move, "open_cases_to": T.id, "email": email_of(T)}, 409, None),
    ]
    for label, body, status, code in attempts:
        before = snapshot(R, c.all)
        r = admin.c.patch(f"/users/{R.id}", json=body)
        check(r.status_code == status and (code is None or code_of(r) == code),
              f"{label} -> {status}{' ' + code if code else ''}", brief(r))
        check(snapshot(R, c.all) == before and signed_in(R),
              "...and nothing changed: still active and signed in, cases where they were, no history")


def last_admin() -> None:
    """The caller is an active admin, so another admin is never the last one
    over HTTP; the count is faked to 1 to show the refusal moves nothing."""
    admin, B, T = W.admin, W.admin2, W.target
    c = cases(B, open=2, signed=1)
    before = snapshot(B, c.all)
    with mock.patch.object(auth_api, "active_admin_count", return_value=1):
        r = admin.c.patch(f"/users/{B.id}", json={"active": False, "open_cases": "move", "open_cases_to": T.id})
    check(r.status_code == 409 and jd(r).get("detail") == "Keep at least one active admin.",
          "disabling the last active admin, with a move -> 409 \"Keep at least one active admin.\"", brief(r))
    check(snapshot(B, c.all) == before and signed_in(B), "...and nothing moved: still active and signed in, cases his, no history")


def atomic() -> None:
    admin, T = W.admin, W.target
    P = person("Al Atomic", "al.a")
    c = cases(P, open=3, signed=1)

    print("  a failure after the first case has moved")
    real_set_owner = owners.set_owner
    calls = {"n": 0}

    def fails_second_time(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated failure part-way through moving the cases")
        return real_set_owner(*args, **kwargs)

    before = snapshot(P, c.all)
    with mock.patch.object(owners, "set_owner", fails_second_time):
        r = admin.c.patch(f"/users/{P.id}", json={"active": False, "open_cases": "move", "open_cases_to": T.id})
    check(r.status_code == 500 and calls["n"] == 2, "the disable fails on the second case (500)", f"{brief(r)} after {calls['n']} set_owner calls")
    check(snapshot(P, c.all) == before, "...and nothing changed: still active, sessions kept, every case his, no history",
          f"{before} -> {snapshot(P, c.all)}")
    check(signed_in(P), "...and he is still signed in")

    print("  a failure at the very end, after the sessions ended and every case moved")
    real_audit = auth_api.audit

    def fails_on_disabled(db, actor, action, *args, **kwargs):
        if action == "user_disabled":
            raise RuntimeError("simulated failure writing the user_disabled entry")
        return real_audit(db, actor, action, *args, **kwargs)

    with mock.patch.object(auth_api, "audit", fails_on_disabled):
        r = admin.c.patch(f"/users/{P.id}", json={"active": False, "open_cases": "unassign"})
    check(r.status_code == 500, "the disable fails at its last step (500)", brief(r))
    check(snapshot(P, c.all) == before and signed_in(P), "...and nothing changed", f"{before} -> {snapshot(P, c.all)}")

    r = admin.c.patch(f"/users/{P.id}", json={"active": False, "open_cases": "move", "open_cases_to": T.id})
    got = owners_of(c.all)
    check(r.status_code == 200 and all(got[i] == T.id for i in c.open) and got[c.signed[0]] == P.id and not signed_in(P),
          "the same request without the fault then works: 3 open cases moved, the signed-off one stays", brief(r))


# ================================================================= POST /reports/reassign
def reassign() -> None:
    admin, T = W.admin, W.target
    R = person("Rob Reassign", "rob.r")
    c = cases(R, open=2, signed=1, legacy_signed=1, drafts=1)

    print("  without include_signed: only the open cases")
    r = admin.c.post("/reports/reassign", json={"from_user_id": R.id, "to_user_id": T.id})
    check(r.status_code == 200 and jd(r) == {"moved": 2}, "POST /reports/reassign Rob -> Tina -> {moved: 2}", brief(r))
    got = owners_of(c.all)
    check(all(got[i] == T.id for i in c.open), "...his open cases are Tina's", repr(got))
    check(all(got[i] == R.id for i in c.signed + c.drafts), "...both signed-off cases (old and new spelling) and the draft stay his", repr(got))
    entries = assigned_entries(c.all)
    check(sorted(e.report_id for e in entries) == sorted(c.open) and all(e.detail == W.to_tina and e.user_name == W.admin_name for e in entries),
          f"...each moved case got one history entry: assigned, \"{W.to_tina}\", by the admin", repr(entries))
    check(signed_in(R) and counts_of(R) == {"open": 0, "signed_off": 2, "drafts": 1}, "...Rob is still active and signed in", repr(counts_of(R)))
    n = len(entries)
    r = admin.c.post("/reports/reassign", json={"from_user_id": R.id, "to_user_id": T.id})
    check(r.status_code == 200 and jd(r) == {"moved": 0} and len(assigned_entries(c.all)) == n,
          "the same call again -> {moved: 0}, no new history", brief(r))

    print("  with include_signed: the signed-off ones too, never the draft")
    r = admin.c.post("/reports/reassign", json={"from_user_id": R.id, "to_user_id": T.id, "include_signed": True})
    got = owners_of(c.all)
    check(r.status_code == 200 and jd(r) == {"moved": 2}, "include_signed: true -> {moved: 2}", brief(r))
    check(all(got[i] == T.id for i in c.signed) and all(got[i] == R.id for i in c.drafts),
          "...the signed-off cases are Tina's, the draft stays Rob's", repr(got))

    print("  to_user_id null: Unassigned")
    N = person("Ned Nobody", "ned.n")
    cn = cases(N, open=2, signed=1)
    r = admin.c.post("/reports/reassign", json={"from_user_id": N.id, "to_user_id": None})
    got = owners_of(cn.all)
    check(r.status_code == 200 and jd(r) == {"moved": 2} and all(got[i] is None for i in cn.open) and got[cn.signed[0]] == N.id,
          "to_user_id null -> {moved: 2}: open cases Unassigned, the signed-off one stays", f"{brief(r)} {got!r}")
    entries = assigned_entries(cn.all)
    check(len(entries) == 2 and all(e.detail == "Unassigned" for e in entries), "...each with an \"Unassigned\" history entry", repr(entries))
    r = admin.c.post("/reports/reassign", json={"from_user_id": N.id, "to_user_id": None, "include_signed": True})
    check(r.status_code == 200 and jd(r) == {"moved": 1} and owners_of(cn.signed)[cn.signed[0]] is None,
          "to_user_id null with include_signed -> {moved: 1}", brief(r))

    print("  from a disabled account: \"Move their cases\" after disabling with keep")
    K, ck = W.kept
    r = admin.c.post("/reports/reassign", json={"from_user_id": K.id, "to_user_id": T.id})
    got = owners_of(ck.all)
    check(r.status_code == 200 and jd(r) == {"moved": 2} and all(got[i] == T.id for i in ck.open),
          "Kim (disabled) -> Tina -> {moved: 2}", f"{brief(r)} {got!r}")


def reassign_refusals() -> None:
    admin, T, D = W.admin, W.target, W.disabled
    F = person("Fay Fixed", "fay.f")
    c = cases(F, open=2, signed=1)
    attempts = [
        ("an unknown from_user_id", {"from_user_id": "nosuchuser0", "to_user_id": T.id}, 404, None),
        ("an unknown to_user_id", {"from_user_id": F.id, "to_user_id": "nosuchuser0"}, 404, None),
        ("to a disabled account", {"from_user_id": F.id, "to_user_id": D.id}, 409, "owner_inactive"),
        ("to the same person", {"from_user_id": F.id, "to_user_id": F.id}, 422, "same_owner"),
        ("to_user_id left out (null must be sent, not implied)", {"from_user_id": F.id}, 422, None),
    ]
    for label, body, status, code in attempts:
        before = snapshot(F, c.all)
        r = admin.c.post("/reports/reassign", json=body)
        check(r.status_code == status and (code is None or code_of(r) == code),
              f"{label} -> {status}{' ' + code if code else ''}", brief(r))
        check(snapshot(F, c.all) == before, "...and nothing moved, no history")

    print("  only admins")
    for who, p in (("a plain user", W.plain), ("\"see all cases\" staff", W.staff), ("the owner herself", F)):
        before = snapshot(F, c.all)
        r = p.c.post("/reports/reassign", json={"from_user_id": F.id, "to_user_id": p.id, "include_signed": True})
        check(r.status_code == 403, f"{who}: POST /reports/reassign -> 403", brief(r))
        r = p.c.patch(f"/users/{F.id}", json={"active": False, "open_cases": "move", "open_cases_to": p.id})
        check(r.status_code == 403, f"{who}: PATCH /users/{{id}} disable with a move -> 403", brief(r))
        check(snapshot(F, c.all) == before, "...and nothing changed")


def main() -> int:
    run_section("setup", setup)
    if FAILURES:
        return summary()
    for name, fn in (
        ("own role: refused on your own account", own_role),
        ("disable: keep their open cases", disable_keep),
        ("disable: leave their open cases unassigned", disable_unassign),
        ("disable: move their open cases to someone", disable_move),
        ("disable: refusals change nothing", disable_refusals),
        ("disable: the last-admin refusal moves nothing", last_admin),
        ("disable: one transaction (a failure part-way changes nothing)", atomic),
        ("POST /reports/reassign", reassign),
        ("POST /reports/reassign: refusals and non-admins", reassign_refusals),
    ):
        run_section(name, fn)
    return summary()


def summary() -> int:
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for line in FAILURES:
        print(f"  FAIL {line}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
