"""Who may throw a case away through DELETE /reports/{id} (api/main.
delete_report, permissions.can_delete_case), with real accounts signed in
through the TestClient -- the live report of 10 Nov 2026: a plain user
(role "user", company-key tick) got 403 "Only an admin can delete a case."
on DELETE of the draft they had made through the API.

    python scripts/test_delete_draft.py

Checked: one route answers DELETE /reports/{id}, and it is delete_report;
a plain owner deletes their own draft -- just made, with a photo, and after
an AI run -- and it and its photos are gone; once it is filed it is an
admin's to delete, and the refusal says so ("filed"), not that drafts are
admin-only; "see all" staff can't delete someone else's draft (403, the
draft's own wording) and a stranger can't see it (404); the admin deletes
anything. The case's permissions.delete agrees with DELETE every time.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database, the PDF render replaced by a stub, Gemini by a fake that answers
the sort and the reading, and every outside connection refused.
"""
import json
import os
import re
import socket
import sys
import tempfile
import time
import traceback
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
_tmp = Path(tempfile.mkdtemp(prefix="delete-draft-test-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
# Forced, not setdefault: these tests delete case folders.
os.environ["STORAGE_DIR"] = str(STORAGE)
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaSyFakeCompanyKeyForDeleteDraftTest01"
os.environ["GEMINI_MIN_CALL_INTERVAL_SECONDS"] = "0"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ["MIN_FREE_DISK_GB"] = "0"
# One reading call per run; the second look has its own test.
os.environ["SECOND_LOOK_ENABLED"] = "false"

_real_connect = socket.socket.connect


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConnectionRefusedError(f"test is offline: refused a connection to {address!r}")
    return _real_connect(sock, address)


socket.socket.connect = _offline_connect

import app.ai.structured as structured  # noqa: E402

GEMINI: list[str] = []


class _FakeInteractions:
    """Sorts every photo as damage and reads one damaged part."""

    def create(self, *, model=None, input=None, response_format=None, **_kw):
        GEMINI.append(str(model))
        labels = [m.group(1) for part in (input or []) if isinstance(part, dict) and part.get("type") == "text"
                  for m in [re.match(r"Photo (P\d+):", part.get("text", ""))] if m]
        if "photos" in ((response_format or {}).get("schema") or {}).get("properties", {}):
            out = [{"label": lb, "kind": "damage", "area": "rear", "quality": "good", "shows_damage": True, "note": None}
                   for lb in labels]
            return SimpleNamespace(output_text=json.dumps({"photos": out}))
        return SimpleNamespace(output_text=json.dumps({
            "description": "Rear-ended.", "category": ["Vehicle Collision or Damage"],
            "damage_summary": [{"part": "Rear Bumper", "condition_tokens": ["Deformed"]}],
        }))


structured.get_client = lambda key=None: SimpleNamespace(interactions=_FakeInteractions())
structured.acquire = lambda key=None: 0.0

from fastapi.testclient import TestClient  # noqa: E402

import app.api.intake as intake_api  # noqa: E402
import app.api.main as main  # noqa: E402
from app.auth.core import SESSION_COOKIE, create_session  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import AuditEntry, Report, User, gen_id  # noqa: E402
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
FILED = "This case has been filed: only an admin can delete it."
NOT_YOURS = "Only the person filing this draft, or an admin, can throw it away."


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


def new_user(name: str, *, role: str = "user", **ticks) -> str:
    db = SessionLocal()
    try:
        user = User(email=f"{name.lower()}-{gen_id()}@example.com", username=f"{name.lower()}{gen_id()[:6]}",
                    name=name, role=role, password_hash="!", **ticks)
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


def jpeg(i: int) -> bytes:
    """A different picture each time: a draft skips duplicates."""
    out = BytesIO()
    Image.new("RGB", (640, 480), (20 * i, 110, 200)).save(out, "JPEG")
    return out.getvalue()


def new_draft(c: TestClient, photos: int = 0) -> str:
    """Made through the API, as New Intake (and the live check) does."""
    r = c.post("/intake")
    assert r.status_code == 200, brief(r)
    rid = r.json()["id"]
    for i in range(photos):
        r = c.post(f"/intake/{rid}/photos", files={"file": (f"p{i}.jpg", jpeg(i), "image/jpeg")},
                   data={"position": str(i)})
        assert r.status_code == 200 and "photo" in r.json(), brief(r)
    return rid


def row(rid: str):
    db = SessionLocal()
    try:
        return db.query(Report.status, Report.owner_id).filter(Report.id == rid).first()
    finally:
        db.close()


W = SimpleNamespace()


# ---------------------------------------------------------------- sections


def setup() -> None:
    init_db()
    W.owner_id = new_user("Plain", ai_company_key_allowed=True)
    W.owner = signed_in(W.owner_id)
    W.staff = signed_in(new_user("Staff", sees_all_cases=True, may_sign_off=True))
    W.stranger = signed_in(new_user("Stranger"))
    W.admin = signed_in(new_user("Admin", role="admin"))
    me = W.owner.get("/auth/me").json()
    check(me.get("role") == "user", "the owner is a plain user (role 'user')", json.dumps(me)[:200])


def route_table() -> None:
    from starlette.routing import Match

    scope = {"type": "http", "method": "DELETE", "path": "/reports/abc123def456", "root_path": "", "query_string": b"",
             "headers": []}

    def leaves(routes):
        for r in routes:
            inner = getattr(r, "original_router", None)
            if inner is not None:
                prefix = r.include_context.prefix
                for leaf in leaves(inner.routes):
                    yield prefix, leaf
            else:
                yield "", r

    answering = []
    for prefix, route in leaves(app.routes):
        methods = getattr(route, "methods", None) or set()
        path = prefix + getattr(route, "path", "")
        if "DELETE" in methods and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", path), scope["path"]):
            answering.append(f"{route.endpoint.__module__}.{route.endpoint.__name__}")
    check(answering == ["app.api.main.delete_report"], "exactly one route answers DELETE /reports/{id}: main.delete_report",
          str(answering))
    match = [r for r in app.router.routes if r.matches(scope)[0] == Match.FULL]
    check(match and getattr(match[0], "endpoint", None) is main.delete_report, "and it is the one the app dispatches to",
          str([getattr(r, "path", r) for r in match]))


def owner_deletes_own_draft() -> None:
    rid = new_draft(W.owner)
    state = W.owner.get(f"/intake/{rid}").json()
    check(state["status"] == "intake" and state["permissions"]["delete"] is True,
          "a fresh draft: GET /intake says status 'intake' and delete allowed", json.dumps(state.get("permissions")))
    r = W.owner.delete(f"/reports/{rid}")
    check(r.status_code == 200 and r.json() == {"deleted": rid}, "the plain owner deletes their own fresh draft", brief(r))
    check(W.owner.get(f"/intake/{rid}").status_code == 404 and row(rid) is None, "it is gone")

    rid = new_draft(W.owner, photos=2)
    folder = STORAGE / "intake" / rid
    check(folder.exists() and any(folder.iterdir()), "a draft with photos keeps them in its intake folder")
    r = W.owner.delete(f"/reports/{rid}")
    check(r.status_code == 200, "the plain owner deletes their draft with photos", brief(r))
    check(not folder.exists(), "and its photos go with it")


def owner_deletes_after_ai_run() -> None:
    rid = new_draft(W.owner, photos=2)
    r = W.owner.post(f"/intake/{rid}/process", json={"mode": "all"})
    check(r.status_code == 200, "the owner starts the AI on the company key", brief(r))
    end = time.time() + 60
    state = {}
    while time.time() < end:
        state = W.owner.get(f"/intake/{rid}").json()
        if state.get("stage") in ("ready", "failed"):
            break
        time.sleep(0.2)
    check(state.get("stage") == "ready" and state.get("status") == "intake" and GEMINI,
          "the run finishes; the draft is still status 'intake'", f"{state.get('stage')} {state.get('status')} {state.get('error')}")
    check(state.get("permissions", {}).get("delete") is True, "GET /intake still says the owner may delete it")
    r = W.owner.delete(f"/reports/{rid}")
    check(r.status_code == 200, "the plain owner deletes the draft the AI read", brief(r))
    check(row(rid) is None, "it is gone")


def filed_case_is_admins() -> None:
    rid = new_draft(W.owner, photos=1)
    data = SecurityIncidentDraft(description="Rear-ended.", category=["Vehicle Collision or Damage"]).model_dump()
    r = W.owner.post(f"/intake/{rid}/finalize", json={"data": data})
    check(r.status_code == 200, "the owner files the draft", brief(r))
    check(row(rid).status != "intake", "it is a case now", str(row(rid)))
    check(W.owner.get(f"/intake/{rid}").status_code == 409, "GET /intake says it was turned into a case")
    perms = W.owner.get(f"/reports/{rid}").json().get("permissions") or {}
    check(perms.get("delete") is False, "GET /reports/{id} says the owner may not delete the filed case", json.dumps(perms))
    r = W.owner.delete(f"/reports/{rid}")
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    check(r.status_code == 403 and body.get("code") == "not_allowed" and body.get("detail") == FILED,
          "the owner's DELETE of the filed case: 403 that says it was filed, not that drafts are admin-only", brief(r))
    r = W.admin.delete(f"/reports/{rid}")
    check(r.status_code == 200, "the admin deletes the filed case", brief(r))
    db = SessionLocal()
    try:
        entry = db.query(AuditEntry).filter(AuditEntry.report_id == rid, AuditEntry.action == "deleted").first()
        check(entry is not None and "Unfinished intake" not in (entry.detail or ""),
              "its history says a case was deleted, not an unfinished intake", entry.detail if entry else "no entry")
    finally:
        db.close()


def others_drafts() -> None:
    rid = new_draft(W.owner)
    r = W.staff.delete(f"/reports/{rid}")
    body = r.json() if r.status_code == 403 else {}
    check(r.status_code == 403 and body.get("detail") == NOT_YOURS,
          "'see all' staff can't throw away someone else's draft (403, the draft's wording)", brief(r))
    r = W.stranger.delete(f"/reports/{rid}")
    check(r.status_code == 404, "a stranger can't see it (404)", brief(r))
    check(row(rid) is not None and row(rid).status == "intake", "the draft survived both")
    r = W.admin.delete(f"/reports/{rid}")
    check(r.status_code == 200, "the admin deletes someone else's draft", brief(r))


def main_() -> int:
    for name, fn in (
        ("setup", setup),
        ("the route", route_table),
        ("a plain owner and their own draft", owner_deletes_own_draft),
        ("after an AI run on the company key", owner_deletes_after_ai_run),
        ("a filed case is an admin's to delete", filed_case_is_admins),
        ("someone else's draft", others_drafts),
    ):
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main_())
