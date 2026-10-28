"""Black-box tests for release P1 "Case privacy": who can see and work on
which case, its photos and PDFs, the scoped lists, assigning an owner, and
the start-up upgrade (owner backfill, ticks, the init_db race).

    python scripts/test_case_access.py

Written from the spec, not from the code: plans/2026-10-26-roles-ai-telegram/
p1_build_brief.md (wins on any conflict), agent5_integration_report.md
sections 1, 2, 3a and 6, and agent6_security_report.md part (c). A FAIL
means the app differs from the spec, or that part isn't built yet.

Offline and self-contained: a temporary STORAGE_DIR and database, Gemini and
Chromium replaced by fakes, and every outside network connection refused.
Each check prints ok/FAIL and the run goes on; the summary at the end lists
every failure. Exit code 1 if anything failed.

People: A and B sign themselves up (no ticks); S is staff, made by the admin
with "Can see and work on all cases" and "May sign off"; D signed up and was
then disabled; and an admin.
"""
import asyncio
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import types
import uuid
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

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
_tmp = Path(tempfile.mkdtemp(prefix="case-access-test-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
# Forced, not setdefault: these tests delete case folders, so they must never
# run against a real STORAGE_DIR from the environment or a .env file.
os.environ["STORAGE_DIR"] = str(STORAGE)
# The start-up (with browser()) makes the AI key secret (app/ai/secretbox.py): not in the source tree.
os.environ["SECRETS_DIR"] = str(_tmp / "secrets")
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake"
os.environ["GEMINI_MIN_CALL_INTERVAL_SECONDS"] = "0"
os.environ["GEMINI_MAX_REQUESTS_PER_MINUTE"] = "100000"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ.pop("ENABLE_API_DOCS", None)

# The commit P1 is built on (p1_build_brief.md, "Where"): its models give the
# schema of a database made before the change.
BASE_COMMIT = "f4b1a66"

# ---------------------------------------------------------------- offline fakes
# 1. No outside network at all. Loopback stays allowed (asyncio's self-pipe).
_real_connect = socket.socket.connect


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConnectionRefusedError(f"test is offline: refused a connection to {address!r}")
    return _real_connect(sock, address)


socket.socket.connect = _offline_connect

# 2. Gemini: the SDK client is replaced before the app is imported, so no
# code path can build a real one. Answers are minimal valid JSON.
GEMINI_CALLS: list[str] = []


class _FakeInteractions:
    def create(self, *, model=None, input=None, response_format=None, **_kw):
        GEMINI_CALLS.append(str(model))
        props = ((response_format or {}).get("schema") or {}).get("properties") or {}
        if "photos" in props:
            labels = [m.group(1) for part in (input or []) if isinstance(part, dict) and part.get("type") == "text"
                      for m in [re.match(r"Photo (P\d+):", part.get("text", ""))] if m]
            out = [{"label": lb, "kind": "damage", "area": "rear", "quality": "good", "shows_damage": True, "note": None}
                   for lb in labels]
            return SimpleNamespace(output_text=json.dumps({"photos": out}))
        if "parts" in props:
            return SimpleNamespace(output_text=json.dumps(
                {"parts": [{"part": "Rear Bumper", "lines": [{"name": "Rear bumper retainer", "qty": 1}]}]}))
        return SimpleNamespace(output_text=json.dumps(
            {"description": "Fake AI draft.", "category": ["Vehicle Collision or Damage"]}))


class FakeGenaiClient:
    def __init__(self, *args, **kwargs):
        self.interactions = _FakeInteractions()
        self.models = SimpleNamespace(list=lambda *a, **k: iter([]))


import google.genai as _genai  # noqa: E402

_genai.Client = FakeGenaiClient

# 3. Chromium: any Playwright object is a _FakePW; page.pdf() writes a tiny
# PDF, so photos/PDFs exist on disk exactly where the app puts them.
FAKE_PDF = b"%PDF-1.4\n% fake PDF written by the test renderer\n%%EOF\n"
RENDERS: list[tuple[str, str]] = []  # (output path, html)


class _FakePW:
    def __init__(self, state=None):
        self._state = {} if state is None else state

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _FakePW(self._state)

    def __call__(self, *args, **kwargs):
        return _FakePW(self._state)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def start(self):
        return self

    def set_content(self, html, *args, **kwargs):
        self._state["html"] = html

    def content(self):
        return self._state.get("html", "")

    def pdf(self, *args, path=None, **kwargs):
        RENDERS.append((str(path or ""), self._state.get("html", "")))
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(FAKE_PDF)
        return FAKE_PDF


def _fake_sync_playwright():
    return _FakePW()


import playwright.sync_api as _pw  # noqa: E402

_REAL_SYNC_PLAYWRIGHT = _pw.sync_playwright
_pw.sync_playwright = _fake_sync_playwright

from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402
from app.auth.core import SESSION_COOKIE  # noqa: E402
from app.reports.db import SessionLocal, engine  # noqa: E402
from app.reports.models import Report  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

for _name, _mod in list(sys.modules.items()):
    if _name.startswith("app.") and getattr(_mod, "sync_playwright", None) is _REAL_SYNC_PLAYWRIGHT:
        setattr(_mod, "sync_playwright", _fake_sync_playwright)

# ---------------------------------------------------------------- harness
BASE = "https://carlink.test"
PASSWORD = "purple tiger lamp bicycle"
MADE_UP = "0000deadbeef"  # shaped like a real id (12 hex), belongs to nobody
CASE_NOT_FOUND = "Case not found."
PERMISSION_KEYS = {"edit", "sign_off", "reopen", "delete", "assign"}
TICKS = ("sees_all_cases", "ai_company_key_allowed", "may_sign_off")

PASSED = [0]
FAILURES: list[tuple[str, str, str]] = []
_section = {"tag": "B1", "name": "setup"}


class Stop(Exception):
    """A precondition failed; the rest of the section can't run."""


def check(cond, label: str, why: str = "") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
        return True
    FAILURES.append((_section["tag"], _section["name"], label + (f"  [{why}]" if why else "")))
    print(f"    FAIL {label}" + (f"\n           got: {why}" if why else ""))
    return False


def need(cond, label: str, why: str = "") -> None:
    if not check(cond, label, why):
        raise Stop(label)


def _test_frame(exc) -> int:
    """The line in this file where an unexpected exception surfaced."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if Path(f.filename).resolve() == Path(__file__).resolve()]
    return frames[-1].lineno if frames else -1


def run_section(tag: str, name: str, fn) -> None:
    _section.update(tag=tag, name=name)
    print(f"\n[{tag}] {name}")
    try:
        fn()
    except Stop as exc:
        print(f"    ---  rest of this section skipped: {exc}")
    except Exception as exc:  # a crash is reported as a failure, never hides the rest
        msg = f"section stopped by {type(exc).__name__}: {exc} (test line {_test_frame(exc)})"
        FAILURES.append((tag, name, msg))
        print(f"    FAIL {msg}")


def browser() -> TestClient:
    # raise_server_exceptions=False: a 500 from half-built code comes back as
    # a response (a clear FAIL), not a traceback out of the test.
    return TestClient(app, base_url=BASE, raise_server_exceptions=False)


def js(r):
    try:
        return r.json()
    except Exception:
        return None


def jd(r) -> dict:
    body = js(r)
    return body if isinstance(body, dict) else {}


def brief(r) -> str:
    kind = r.headers.get("content-type", "")
    if "json" in kind or kind.startswith("text/") or not r.content:
        text = (r.text or "").replace("\n", " ")[:220]
    else:
        text = f"<{len(r.content)} bytes of {kind or 'unknown type'}>"
    return f"{r.status_code} {text}"


def code_of(r):
    return jd(r).get("code")


_ips = iter(range(1, 1_000_000))


def fresh_ip() -> dict:
    n = next(_ips)
    return {"X-Real-IP": f"10.88.{(n // 256) % 256}.{n % 256}"}


def person(label, client, user_id, username, name):
    return SimpleNamespace(label=label, c=client, id=user_id, username=username, name=name)


def signup(username: str, full_name: str):
    c = browser()
    r = c.post("/auth/signup", json={"username": username, "email": f"{username}@example.com", "password": PASSWORD},
               headers=fresh_ip())
    need(r.status_code == 200 and jd(r).get("id"), f"{full_name} signs up", brief(r))
    me = jd(r)
    p = person(full_name.split()[0], c, me["id"], me.get("username") or username, me.get("name") or username)
    r = c.put("/auth/me", json={"name": full_name})
    if r.status_code == 200:
        p.name = full_name
    return p


def force_ticks(user_id: str, values: dict) -> None:
    """Fallback only, so later checks still mean something when PATCH
    /users/{id} doesn't set the ticks yet (that failure is reported)."""
    from sqlalchemy import inspect, text

    cols = {c["name"] for c in inspect(engine).get_columns("users")}
    with engine.begin() as conn:
        for k, v in values.items():
            if k in cols:
                conn.execute(text(f"UPDATE users SET {k} = :v WHERE id = :id"), {"v": v, "id": user_id})


def set_ticks(admin, p, **ticks) -> None:
    body = {k: bool(ticks.get(k, False)) for k in TICKS}
    r = admin.c.patch(f"/users/{p.id}", json=body)
    shown = jd(r)
    applied = r.status_code == 200 and all(bool(shown.get(k)) == v for k, v in body.items())
    if not check(applied, f"admin sets {p.label}'s ticks with PATCH /users/{{id}} {body}", brief(r)):
        force_ticks(p.id, body)
        print("           (set directly in the database so the remaining checks still test something)")


def admin_creates(admin, username: str, full_name: str, **ticks):
    r = admin.c.post("/users", json={"email": f"{username}@example.com", "name": full_name, "username": username, "role": "user"})
    need(r.status_code == 200, f"admin adds {full_name}", brief(r))
    user_id, temp = jd(r)["user"]["id"], jd(r)["temporary_password"]
    c = browser()
    r = c.post("/auth/login", json={"email": f"{username}@example.com", "password": temp})
    need(r.status_code == 200, f"{full_name} signs in with the temporary password", brief(r))
    r = c.post("/auth/password", json={"current_password": temp, "new_password": PASSWORD})
    need(r.status_code == 200, f"{full_name} changes the temporary password", brief(r))
    p = person(full_name.split()[0], c, user_id, username, full_name)
    set_ticks(admin, p, **ticks)
    return p


# ---------------------------------------------------------------- case fixtures
_seed = iter(range(1, 1_000_000))


def jpeg() -> bytes:
    from PIL import Image

    n = next(_seed)
    buf = BytesIO()
    Image.new("RGB", (64, 48), ((n * 53) % 256, (n * 97) % 256, (n * 31) % 256)).save(buf, "JPEG")
    return buf.getvalue()


def case_data(desc: str, plate: str, parts=(), **extra) -> dict:
    data = {
        "description": desc,
        "category": ["Vehicle Collision or Damage"],
        "vehicle_info": {"plate_number": plate, "make": "Honda", "model": "Vezel"},
        "damage_summary": [{"part": x, "severity": "Moderate"} for x in parts],
    }
    data.update(extra)
    return data


def upload(p, draft_id: str, position: int = 0):
    return p.c.post(f"/intake/{draft_id}/photos", files={"file": (f"IMG_{position:04d}.jpg", jpeg(), "image/jpeg")},
                    data={"position": str(position)})


def new_draft(p, photos: int = 0) -> tuple[str, list[str]]:
    r = p.c.post("/intake")
    need(r.status_code == 200 and jd(r).get("id"), f"{p.label} starts an intake draft", brief(r))
    draft_id, photo_ids = jd(r)["id"], []
    for i in range(photos):
        r = upload(p, draft_id, i)
        need(r.status_code == 200 and jd(r).get("photo"), f"{p.label} uploads photo {i + 1} to the draft", brief(r))
        photo_ids.append(jd(r)["photo"]["id"])
    return draft_id, photo_ids


def new_filed_case(p, photos=2, parts=("Rear Bumper",), plate="SNA1234B", desc="Rear-ended at a junction") -> str:
    draft_id, _ = new_draft(p, photos)
    r = p.c.post(f"/intake/{draft_id}/finalize", json={"data": case_data(desc, plate, parts)})
    need(r.status_code == 200, f"{p.label} files the case (intake finalize)", brief(r))
    return draft_id


def new_manual_case(p, plate="SKA1111A", parts=("Rear Bumper",), temp_paths=(), desc="Manual entry") -> str:
    r = p.c.post("/reports", json={"draft": case_data(desc, plate, parts), "temp_photo_paths": list(temp_paths)})
    need(r.status_code == 200 and jd(r).get("id"), f"{p.label} files a case through POST /reports", brief(r))
    return jd(r)["id"]


def insert_telegram_case(parts=("Front Bumper",)) -> str:
    """The bot writes to the database directly; until P5 its cases have no
    owner (brief: "cases filed through the bot stay Unassigned")."""
    db = SessionLocal()
    try:
        r = Report(channel="telegram", reporter_chat_id="5550001", status="confirmed",
                   data=case_data("Filed through the bot", "SGT5550A", parts), photo_paths=[])
        db.add(r)
        db.commit()
        return r.id
    finally:
        db.close()


def case_paths(case_id: str) -> list[Path]:
    found = [p for p in STORAGE.glob(f"incidents/*/*/{case_id}/**/*") if p.is_file()]
    found += [p for p in STORAGE.glob(f"intake/{case_id}/**/*") if p.is_file()]
    return sorted(found)


def url_of(path: Path) -> str:
    return "/files/" + path.resolve().relative_to(STORAGE).as_posix()


def disk_of(url: str) -> Path:
    return STORAGE / unquote(url[len("/files/"):])


def ids_of(r):
    body = js(r)
    if not isinstance(body, list):
        return None
    return {row.get("id") for row in body if isinstance(row, dict)}


def rows_of(r) -> list:
    body = js(r)
    return [row for row in body if isinstance(row, dict)] if isinstance(body, list) else []


def owner_id(d: dict):
    owner = d.get("owner")
    return owner.get("id") if isinstance(owner, dict) else owner


def raw_get(p, raw_path: str) -> tuple[int, bytes]:
    """A GET sent straight to the ASGI app with the path exactly as typed:
    httpx would resolve "/../" before sending, a real client need not."""
    headers = [(b"host", b"carlink.test")]
    token = p.c.cookies.get(SESSION_COOKIE) if p else None
    if token:
        headers.append((b"cookie", f"{SESSION_COOKIE}={token}".encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "https",
        "path": unquote(raw_path), "raw_path": raw_path.encode(), "root_path": "", "query_string": b"",
        "headers": headers, "client": ("127.0.0.1", 50000), "server": ("carlink.test", 443),
    }
    out = {"status": None, "body": bytearray(), "asked": False}

    async def run():
        done = asyncio.Event()

        async def receive():
            # The request once; after that, like a real server, wait and
            # report the disconnect only when the response is complete (a
            # file response listens for it while it streams).
            if not out["asked"]:
                out["asked"] = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await done.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                out["status"] = message["status"]
            elif message["type"] == "http.response.body":
                out["body"].extend(message.get("body", b""))
                if not message.get("more_body"):
                    done.set()

        await asyncio.wait_for(app(scope, receive, send), timeout=30)

    try:
        asyncio.run(run())
    except Exception:
        out["status"] = out["status"] or 500
    return out["status"], bytes(out["body"])


W = SimpleNamespace()  # people and fixtures shared by the sections


# ================================================================= setup
def setup() -> None:
    admin_client = browser()
    admin_id = sign_in(admin_client, role="admin", name="Ada Admin", username="adaboss")
    W.admin = person("admin", admin_client, admin_id, "adaboss", "Ada Admin")
    W.A = signup("alice.a", "Alice Archer")
    W.S = admin_creates(W.admin, "sam.s", "Sam Staff", sees_all_cases=True, may_sign_off=True)
    W.D = signup("dee.d", "Dee Departed")
    r = W.admin.c.patch(f"/users/{W.D.id}", json={"active": False})
    need(r.status_code == 200, "admin disables Dee", brief(r))

    W.FC = new_filed_case(W.A, photos=2, parts=("Rear Bumper", "ZZ Alice Only Part"), plate="SAL1111A")
    W.AD, W.AD_photos = new_draft(W.A, photos=1)
    r = W.A.c.put(f"/intake/{W.AD}/data", json={"data": case_data("", "SAL2222B", ("ZZ Draft Only Part",))})
    need(r.status_code == 200, "Alice autosaves her draft", brief(r))
    W.AC = new_manual_case(W.admin, plate="SAD1111A", parts=("ZZ Admin Only Part",))
    W.SC = new_manual_case(W.S, plate="SSA1111A", parts=("ZZ Sam Only Part",))
    W.TG = insert_telegram_case(parts=("ZZ Bot Only Part",))

    print("  a stranger who just signed up")
    W.B = signup("bob.b", "Bob Builder")
    r = W.B.c.get("/reports")
    check(r.status_code == 200 and js(r) == [], "B's case list is empty (the whole firm's cases exist)", brief(r))
    r = W.B.c.get("/analytics/summary")
    check(r.status_code == 200 and jd(r).get("total_incidents") == 0 and jd(r).get("recent_activity") == [],
          "B's analytics count nothing and show no recent cases", brief(r))
    r = W.B.c.get("/taxonomy")
    usage = jd(r).get("usage") or {}
    check(r.status_code == 200 and usage.get("reports_counted") == 0 and "ZZ" not in json.dumps(usage),
          "B's taxonomy usage counts no cases", brief(r))
    W.BC = new_manual_case(W.B, plate="SBO1111A", parts=("ZZ Bob Only Part",))
    W.BD, W.BD_photos = new_draft(W.B, photos=1)


# ================================================================= lists
def lists() -> None:
    A, B, S, admin = W.A, W.B, W.S, W.admin
    print("  GET /reports scopes")
    r = A.c.get("/reports")
    got = ids_of(r)
    check(got == {W.FC, W.AD}, "A's default list is exactly A's case and A's draft", f"{brief(r)} ids={got}")
    r = A.c.get("/reports?scope=mine")
    check(ids_of(r) == {W.FC, W.AD}, "A's ?scope=mine is the same", brief(r))
    for scope in ("all", "unassigned"):
        r = A.c.get(f"/reports?scope={scope}")
        check(r.status_code == 403 and code_of(r) == "scope_not_allowed",
              f"a plain user asking for ?scope={scope} gets 403 scope_not_allowed", brief(r))
    r = B.c.get("/reports")
    check(ids_of(r) == {W.BC, W.BD}, "B's default list is exactly B's own case and draft", brief(r))
    r = S.c.get("/reports")
    check(ids_of(r) == {W.SC}, "staff S's default is 'mine': only S's own case", brief(r))
    r = S.c.get("/reports?scope=all")
    got = ids_of(r) or set()
    check({W.FC, W.BC, W.SC, W.AC, W.TG} <= got, "S's ?scope=all has everyone's filed cases and the Unassigned one",
          f"{r.status_code} missing={sorted({W.FC, W.BC, W.SC, W.AC, W.TG} - got)}")
    r = S.c.get("/reports?scope=unassigned")
    got = ids_of(r) or set()
    check(r.status_code == 200 and W.TG in got and not ({W.FC, W.BC, W.SC, W.AC} & got),
          "S's ?scope=unassigned holds the Unassigned case and no owned one", brief(r))
    check(all(row.get("owner") is None for row in rows_of(r)) and rows_of(r), "every Unassigned row has owner null", brief(r))
    r = admin.c.get("/reports")
    got = ids_of(r) or set()
    check({W.FC, W.AD, W.BC, W.BD, W.SC, W.AC, W.TG} <= got, "the admin's default is 'all' (drafts included)",
          f"{r.status_code} missing={sorted({W.FC, W.AD, W.BC, W.BD, W.SC, W.AC, W.TG} - got)}")
    r = admin.c.get("/reports?scope=mine")
    got = ids_of(r)
    check(got == {W.AC}, "the admin's ?scope=mine is only the admin's own case", f"{brief(r)} ids={got}")
    r = admin.c.get("/reports?scope=unassigned")
    got = ids_of(r) or set()
    check(r.status_code == 200 and W.TG in got and not ({W.FC, W.BC, W.SC, W.AC} & got),
          "the admin's ?scope=unassigned holds only Unassigned cases", brief(r))

    print("  owner on list rows and details")
    rows = {row.get("id"): row for row in rows_of(admin.c.get("/reports"))}
    fc_owner = (rows.get(W.FC) or {}).get("owner")
    check(isinstance(fc_owner, dict) and {"id", "name", "username", "active"} <= set(fc_owner)
          and fc_owner.get("id") == A.id and fc_owner.get("username") == A.username and fc_owner.get("active") is True,
          "list rows carry owner {id, name, username, active}", f"FC row owner={fc_owner!r}")
    check(W.TG in rows and rows[W.TG].get("owner", "missing") is None, "the Unassigned row carries owner: null",
          f"TG row owner={(rows.get(W.TG) or {}).get('owner', 'missing')!r}")
    check(all("owner" in row for row in rows.values()) and rows, "every row in the admin's list has an owner key")
    r = A.c.get(f"/reports/{W.FC}")
    d = jd(r)
    check(r.status_code == 200 and owner_id(d) == A.id, "GET /reports/{id} carries owner (the person who filed it)", brief(r))
    check(isinstance(d.get("permissions"), dict) and PERMISSION_KEYS <= set(d["permissions"]),
          "GET /reports/{id} carries permissions {edit, sign_off, reopen, delete, assign}", f"{d.get('permissions')!r}")
    r = A.c.get(f"/intake/{W.AD}")
    d = jd(r)
    check(r.status_code == 200 and owner_id(d) == A.id, "GET /intake/{id} carries owner", brief(r))
    check(isinstance(d.get("permissions"), dict) and PERMISSION_KEYS <= set(d["permissions"]),
          "GET /intake/{id} carries permissions", f"{d.get('permissions')!r}")
    check(owner_id(jd(W.B.c.get(f"/reports/{W.BC}"))) == B.id, "a case made with POST /reports is owned by its maker")
    check(owner_id(jd(admin.c.get(f"/reports/{W.AC}"))) == admin.id, "the admin's own case is owned by the admin")

    print("  suggested_owner")
    d = jd(admin.c.get(f"/reports/{W.TG}"))
    sug = d.get("suggested_owner", "missing")
    check(sug != "missing" and (sug is None or (isinstance(sug, dict) and {"id", "name", "username", "reason"} <= set(sug))),
          "an admin sees suggested_owner ({id, name, username, reason} or null) on an Unassigned case", f"{sug!r}")
    d = jd(S.c.get(f"/reports/{W.TG}"))
    check(d.get("suggested_owner") is None, "staff S (not an admin) gets no suggested_owner", f"{d.get('suggested_owner')!r}")
    d = jd(admin.c.get(f"/reports/{W.FC}"))
    check(d.get("suggested_owner") is None, "no suggested_owner on a case that has an owner", f"{d.get('suggested_owner')!r}")

    print("  analytics follow the scope")

    def recent(r):
        return {x.get("id") for x in (jd(r).get("recent_activity") or []) if isinstance(x, dict)}

    r = A.c.get("/analytics/summary")
    check(jd(r).get("total_incidents") == 1 and recent(r) == {W.FC}, "A's analytics count only A's filed case",
          brief(r))
    r = B.c.get("/analytics/summary")
    check(jd(r).get("total_incidents") == 1 and recent(r) == {W.BC}, "B's analytics count only B's filed case", brief(r))
    r = S.c.get("/analytics/summary")
    check(jd(r).get("total_incidents") == 1 and recent(r) == {W.SC}, "S's default analytics ('mine') count only S's case",
          brief(r))
    r = S.c.get("/analytics/summary?scope=all")
    check(jd(r).get("total_incidents") == 5, "S's ?scope=all analytics count all 5 filed cases", brief(r))
    r = admin.c.get("/analytics/summary")
    check(jd(r).get("total_incidents") == 5, "the admin's analytics count all 5 filed cases", brief(r))
    r = A.c.get("/analytics/summary?scope=all")
    check(r.status_code == 403, "a plain user's ?scope=all on analytics is refused (403)", brief(r))

    print("  taxonomy follows the scope and skips drafts")
    names = {"A": "ZZ Alice Only Part", "B": "ZZ Bob Only Part", "S": "ZZ Sam Only Part", "TG": "ZZ Bot Only Part",
             "admin": "ZZ Admin Only Part", "draft": "ZZ Draft Only Part"}

    def seen(r) -> set:
        text = json.dumps(jd(r).get("usage") or {})
        return {k for k, v in names.items() if v in text}

    def counted(r):
        return (jd(r).get("usage") or {}).get("reports_counted")

    r = A.c.get("/taxonomy")
    check(seen(r) == {"A"} and counted(r) == 1, "A's taxonomy usage: only A's case, not A's draft", f"{seen(r)} counted={counted(r)}")
    r = B.c.get("/taxonomy")
    check(seen(r) == {"B"} and counted(r) == 1, "B's taxonomy usage: only B's case", f"{seen(r)} counted={counted(r)}")
    r = S.c.get("/taxonomy")
    check(seen(r) == {"S"}, "S's default taxonomy usage ('mine'): only S's case", f"{seen(r)}")
    r = S.c.get("/taxonomy?scope=all")
    check(seen(r) == {"A", "B", "S", "TG", "admin"}, "S's ?scope=all taxonomy: every filed case, no draft", f"{seen(r)}")
    r = admin.c.get("/taxonomy")
    check(seen(r) == {"A", "B", "S", "TG", "admin"} and counted(r) == 5, "the admin's taxonomy: all filed cases, no draft",
          f"{seen(r)} counted={counted(r)}")


# ================================================================= hidden cases
def _probe_routes(B_id: str, photo_id: str) -> tuple[list, list]:
    """agent5 section 3a's 20 case route+method pairs (main.py, parts.py,
    intake.py), plus PATCH /owner. Valid bodies, so a missing gate shows up
    as a success instead of hiding behind a 422."""
    upload_kw = lambda: {"files": {"file": ("IMG_0099.jpg", jpeg(), "image/jpeg")}, "data": {"position": "9"}}  # noqa: E731
    safe = [
        ("GET", "/reports/{id}", dict),
        ("GET", "/reports/{id}/download", dict),
        ("HEAD", "/reports/{id}/download", dict),
        ("POST", "/reports/{id}/render-pdf", dict),
        ("PUT", "/reports/{id}", lambda: {"json": {"draft": {"category": ["Other"], "description": "B was here"}}}),
        ("PATCH", "/reports/{id}/damage-items/0", lambda: {"json": {"human_verified": True, "oem_part_number": "B-1"}}),
        ("POST", "/reports/{id}/ai-enrich", dict),
        ("GET", "/reports/{id}/annex-suggestions", dict),
        ("POST", "/reports/{id}/annex-suggestions/ai", lambda: {"json": {"parts": ["Rear Bumper"]}}),
        ("PUT", "/reports/{id}/annex-a", lambda: {"json": {"items": [{"part_name": "B's line", "workshop_est": 1}]}}),
        ("POST", "/reports/{id}/sign-off", lambda: {"json": {"terms_accepted": True}}),
        ("POST", "/reports/{id}/reopen", dict),
        ("PATCH", "/reports/{id}/owner", lambda: {"json": {"owner_id": B_id}}),
        ("GET", "/intake/{id}", dict),
        ("PUT", "/intake/{id}/data", lambda: {"json": {"data": {"description": "B was here", "category": ["Other"]}}}),
        ("PATCH", "/intake/{id}/photos/{photo}", lambda: {"json": {"excluded": True}}),
        ("POST", "/intake/{id}/photos", upload_kw),
        ("POST", "/intake/{id}/process", lambda: {"json": {"mode": "all"}}),
    ]
    destructive = [
        ("DELETE", "/intake/{id}/photos/{photo}", dict),
        ("POST", "/intake/{id}/finalize", lambda: {"json": {"data": {"description": "B", "category": ["Other"]}}}),
        ("DELETE", "/reports/{id}", dict),
    ]
    return safe, destructive


def hidden_cases() -> None:
    A, B = W.A, W.B
    PF = new_filed_case(A, photos=1, parts=("Rear Bumper",), plate="SPF1111A", desc="Probe case")
    PD, pd_photos = new_draft(A, photos=1)
    pd_photo = pd_photos[0]
    calls_before = len(GEMINI_CALLS)
    safe, destructive = _probe_routes(B.id, pd_photo)

    def probe(method, tpl, kw, target, what):
        path = tpl.replace("{photo}", pd_photo)
        made_up = B.c.request(method, path.replace("{id}", MADE_UP), **kw())
        theirs = B.c.request(method, path.replace("{id}", target), **kw())
        label = f"{method} {tpl}"
        if tpl.endswith("/owner"):
            # Admin-only route: a plain user may get 403 or 404, but the same
            # answer whether or not the case exists.
            check(theirs.status_code in (403, 404) and theirs.status_code == made_up.status_code and js(theirs) == js(made_up),
                  f"B {label} on A's {what}: same refusal as a made-up id",
                  f"A's: {brief(theirs)} | made-up: {brief(made_up)}")
            return
        if method == "HEAD":
            check(made_up.status_code == 404, f"{label} on a made-up id: 404", brief(made_up))
            check(theirs.status_code == 404, f"B {label} on A's {what}: 404 like a made-up id", brief(theirs))
            return
        check(made_up.status_code == 404 and jd(made_up).get("detail") == CASE_NOT_FOUND,
              f"{label} on a made-up id: 404 '{CASE_NOT_FOUND}'", brief(made_up))
        check(theirs.status_code == 404 and js(theirs) == js(made_up),
              f"B {label} on A's {what}: 404 with the same body as a made-up id", f"A's: {brief(theirs)}")

    for routes in (safe, destructive):
        for target, what in ((PF, "filed case"), (PD, "draft")):
            for method, tpl, kw in routes:
                probe(method, tpl, kw, target, what)

    print("  nothing B tried changed A's case or draft")
    r = A.c.get(f"/reports/{PF}")
    d = jd(r)
    check(r.status_code == 200, "A's filed case still exists", brief(r))
    data = d.get("data") or {}
    check(data.get("description") == "Probe case", "its description is unchanged", repr(data.get("description")))
    check(((data.get("sign_off") or {}).get("status")) != "Signed Off", "it was not signed off by B")
    check(not any((it or {}).get("human_verified") for it in (data.get("damage_summary") or [])), "its checklist was not ticked by B")
    check("B's line" not in json.dumps(data.get("annex_a") or {}), "its Annex A has no line from B")
    check(owner_id(d) == A.id, "it is still A's", repr(d.get("owner")))
    r = A.c.get(f"/intake/{PD}")
    d = jd(r)
    check(r.status_code == 200, "A's draft is still a draft", brief(r))
    check(len(d.get("photos") or []) == 1, "the draft still has exactly its one photo (no upload or delete by B)",
          f"{len(d.get('photos') or [])} photos")
    check((d.get("data") or {}).get("description") == "", "the draft's form is unchanged", repr((d.get("data") or {}).get("description")))
    check(d.get("stage") == "uploading", "no AI run was started on it", repr(d.get("stage")))
    time.sleep(0.3)
    check(len(GEMINI_CALLS) == calls_before, "no Gemini call was made for B", f"{len(GEMINI_CALLS) - calls_before} calls")

    print("  Unassigned and other people's cases")
    for who, p in (("B", B), ("A", A)):
        r = p.c.get(f"/reports/{W.TG}")
        check(r.status_code == 404 and jd(r).get("detail") == CASE_NOT_FOUND, f"plain user {who} can't open the Unassigned case (404)", brief(r))
    r = B.c.get(f"/reports/{W.SC}")
    check(r.status_code == 404 and jd(r).get("detail") == CASE_NOT_FOUND, "B can't open staff S's case (404)", brief(r))
    for who, p in (("S", W.S), ("admin", W.admin)):
        r = p.c.get(f"/reports/{W.TG}")
        check(r.status_code == 200, f"{who} can open the Unassigned case", brief(r))


# ================================================================= who may do what
def _perm_check(p, path: str, expected: dict, label: str) -> None:
    r = p.c.get(path)
    got = jd(r).get("permissions")
    ok = r.status_code == 200 and isinstance(got, dict) and all(got.get(k) is v for k, v in expected.items())
    check(ok, f"{label}: " + ", ".join(f"{k}={v}" for k, v in expected.items()), f"{r.status_code} {got!r}")


def who_may_do_what() -> None:
    A, B, S, admin = W.A, W.B, W.S, W.admin
    MX = new_manual_case(A, plate="SMX1111A", parts=("Rear Bumper",), desc="Matrix case")
    MXD, mxd_photos = new_draft(A, photos=1)
    MXD2, _ = new_draft(A)
    edit = lambda desc: {"json": {"draft": {"category": ["Other"], "description": desc}}}  # noqa: E731

    print("  editing a filed case")
    for who, p, want in (("A (owner)", A, 200), ("S (staff)", S, 200), ("admin", admin, 200), ("B", B, 404)):
        r = p.c.put(f"/reports/{MX}", **edit(f"edited by {who}"))
        check(r.status_code == want, f"{who} PUT /reports/{{id}} -> {want}", brief(r))
    r = S.c.patch(f"/reports/{MX}/damage-items/0", json={"human_verified": True})
    check(r.status_code == 200, "S ticks the checklist on A's filed case", brief(r))
    r = S.c.put(f"/reports/{MX}/annex-a", json={"items": [{"part_name": "Rear bumper", "workshop_est": 100}]})
    check(r.status_code == 200, "S saves Annex A on A's filed case", brief(r))
    r = S.c.post(f"/reports/{MX}/render-pdf")
    check(r.status_code == 200, "S re-renders the PDF of A's filed case", brief(r))

    print("  A's unfinished draft")
    data = {"json": {"data": {"description": "", "category": ["Other"]}}}
    for who, p, want in (("A (owner)", A, 200), ("admin", admin, 200), ("B", B, 404)):
        r = p.c.put(f"/intake/{MXD}/data", **data)
        check(r.status_code == want, f"{who} autosaves A's draft -> {want}", brief(r))
    r = S.c.get(f"/intake/{MXD}")
    check(r.status_code == 200, "S can see A's draft (sees all cases)", brief(r))
    r = S.c.get(f"/reports/{MXD}")
    check(r.status_code == 200, "S can open A's draft through GET /reports/{id} too", brief(r))
    s_attempts = [
        ("PUT /intake/{id}/data", lambda: S.c.put(f"/intake/{MXD}/data", **data)),
        ("POST /intake/{id}/photos", lambda: upload(S, MXD, 5)),
        ("PATCH /intake/{id}/photos/{p}", lambda: S.c.patch(f"/intake/{MXD}/photos/{mxd_photos[0]}", json={"excluded": True})),
        ("DELETE /intake/{id}/photos/{p}", lambda: S.c.delete(f"/intake/{MXD}/photos/{mxd_photos[0]}")),
        ("POST /intake/{id}/process", lambda: S.c.post(f"/intake/{MXD}/process", json={"mode": "all"})),
        ("POST /intake/{id}/finalize", lambda: S.c.post(f"/intake/{MXD}/finalize", json={"data": {"description": "S", "category": ["Other"]}})),
    ]
    for label, call in s_attempts:
        r = call()
        check(r.status_code == 403 and code_of(r) == "not_allowed", f"S {label} on A's draft -> 403 not_allowed", brief(r))
    for label, call in (("PUT /reports/{id}", lambda: S.c.put(f"/reports/{MXD}", **edit("S"))),
                        ("POST /reports/{id}/sign-off", lambda: S.c.post(f"/reports/{MXD}/sign-off", json={"terms_accepted": True}))):
        r = call()
        check(r.status_code in (403, 409), f"S {label} on A's draft is refused (403 or 409)", brief(r))
    r = A.c.get(f"/intake/{MXD}")
    check(len(jd(r).get("photos") or []) == 1 and jd(r).get("status") == "intake", "A's draft is untouched by S", brief(r))

    print("  permissions in the details")
    _perm_check(A, f"/reports/{MX}", {"edit": True, "delete": False, "assign": False}, "A on A's filed case")
    _perm_check(S, f"/reports/{MX}", {"edit": True, "delete": False, "assign": False}, "S on A's filed case")
    _perm_check(admin, f"/reports/{MX}", {"edit": True, "delete": True, "assign": True}, "admin on A's filed case")
    _perm_check(A, f"/intake/{MXD}", {"edit": True, "delete": True, "assign": False}, "A on A's draft")
    _perm_check(S, f"/intake/{MXD}", {"edit": False, "sign_off": False, "delete": False, "assign": False}, "S on A's draft")
    _perm_check(admin, f"/intake/{MXD}", {"edit": True, "delete": True}, "admin on A's draft")

    print("  sign-off and reopen")
    r = B.c.post(f"/reports/{MX}/sign-off", json={"terms_accepted": True})
    check(r.status_code == 404, "B can't sign off A's case (404)", brief(r))
    r = S.c.post(f"/reports/{MX}/sign-off", json={"terms_accepted": True})
    check(r.status_code == 200, "S (staff, may sign off) signs off A's case", brief(r))
    _perm_check(A, f"/reports/{MX}", {"reopen": False}, "A on a case S signed")
    _perm_check(S, f"/reports/{MX}", {"reopen": True}, "S on the case S signed")
    _perm_check(admin, f"/reports/{MX}", {"reopen": True}, "admin on a case S signed")
    r = A.c.post(f"/reports/{MX}/reopen")
    check(r.status_code == 403, "A (owner, not the signer) can't reopen it (403)", brief(r))
    r = B.c.post(f"/reports/{MX}/reopen")
    check(r.status_code == 404, "B can't reopen it (404)", brief(r))
    r = S.c.post(f"/reports/{MX}/reopen")
    check(r.status_code == 200, "S, the signer, reopens it", brief(r))
    r = admin.c.post(f"/reports/{MX}/sign-off", json={"terms_accepted": True})
    check(r.status_code == 200, "the admin signs off A's case", brief(r))
    r = A.c.post(f"/reports/{MX}/reopen")
    check(r.status_code == 403, "A can't reopen the admin's sign-off either", brief(r))
    r = admin.c.post(f"/reports/{MX}/reopen")
    check(r.status_code == 200, "the admin reopens it", brief(r))

    print("  assigning")
    for who, p in (("A (owner)", A), ("S (staff)", S)):
        r = p.c.patch(f"/reports/{MX}/owner", json={"owner_id": p.id})
        check(r.status_code == 403, f"{who} can't assign a case (403)", brief(r))
    check(owner_id(jd(admin.c.get(f"/reports/{MX}"))) == A.id, "the case is still A's")

    print("  deleting")
    r = B.c.delete(f"/reports/{MXD}")
    check(r.status_code == 404, "B can't delete A's draft (404)", brief(r))
    r = S.c.delete(f"/reports/{MXD}")
    check(r.status_code == 403, "S can't delete A's draft (403)", brief(r))
    check(A.c.get(f"/intake/{MXD}").status_code == 200, "A's draft survived both")
    r = A.c.delete(f"/reports/{MXD}")
    check(r.status_code == 200, "A throws away A's own draft", brief(r))
    check(A.c.get(f"/intake/{MXD}").status_code == 404, "it is gone")
    r = admin.c.delete(f"/reports/{MXD2}")
    check(r.status_code == 200, "the admin deletes A's draft", brief(r))
    for who, p, want in (("A (owner)", A, 403), ("S (staff)", S, 403), ("B", B, 404)):
        r = p.c.delete(f"/reports/{MX}")
        check(r.status_code == want, f"{who} can't delete A's filed case ({want})", brief(r))
    check(admin.c.get(f"/reports/{MX}").status_code == 200, "the filed case survived them")
    r = admin.c.delete(f"/reports/{MX}")
    check(r.status_code == 200, "only the admin deletes a filed case", brief(r))

    print("  owner of a new case")
    MXF, _ = new_draft(A, photos=1)
    r = admin.c.post(f"/intake/{MXF}/finalize", json={"data": case_data("Finished by the admin", "SMF1111A", ("Rear Bumper",))})
    check(r.status_code == 200, "the admin finishes A's draft", brief(r))
    check(owner_id(jd(A.c.get(f"/reports/{MXF}"))) == A.id, "the case stays A's when an admin finishes it")
    W.extra_A_cases = [MXF]


# ================================================================= files
def files() -> None:
    A, B, S, admin = W.A, W.B, W.S, W.admin
    r = A.c.get(f"/reports/{W.FC}")
    d = jd(r)
    need(r.status_code == 200 and d.get("photo_urls"), "A opens A's case with photos", brief(r))
    api_urls = list(d.get("photo_urls") or []) + list(d.get("photo_thumb_urls") or []) + list(d.get("photo_preview_urls") or [])
    if d.get("pdf_url"):
        api_urls.append(d["pdf_url"])
    check(bool(d.get("pdf_url")), "the case has a PDF URL", repr(d.get("pdf_url")))
    r = A.c.get(f"/intake/{W.AD}")
    for ph in jd(r).get("photos") or []:
        api_urls += [ph.get(k) for k in ("url", "thumb_url", "preview_url") if ph.get(k)]
    case_urls = [url_of(p) for p in case_paths(W.FC)]
    draft_urls = [url_of(p) for p in case_paths(W.AD)]
    need(any(u.endswith(".pdf") for u in case_urls) and any("_thumb" in u for u in case_urls)
         and any("_preview" in u for u in case_urls) and any("_pdf.jpg" in u for u in case_urls),
         "A's case folder holds photos, thumbnails, previews, PDF images and the PDF", str(case_urls))
    need(draft_urls, "A's draft folder holds its photo files", str(draft_urls))
    all_urls = sorted(set(api_urls) | set(case_urls) | set(draft_urls))

    print(f"  {len(all_urls)} file URLs of A's case and draft")
    for url in all_urls:
        r = B.c.get(url)
        check(r.status_code == 404, f"B GET {url[7:]} -> 404", brief(r))
    for who, p in (("A", A), ("admin", admin)):
        bad = [(u, p.c.get(u).status_code) for u in all_urls]
        bad = [(u, s) for u, s in bad if s != 200]
        check(not bad, f"{who} gets 200 for every one of them", str(bad[:4]))
    bad = [(u, S.c.get(u).status_code) for u in sorted(set(case_urls))]
    bad = [(u, s) for u, s in bad if s != 200]
    check(not bad, "S gets 200 for every file of A's filed case", str(bad[:4]))
    own = [url_of(p) for p in case_paths(W.BC)]
    check(own and all(B.c.get(u).status_code == 200 for u in own), "B still gets B's own files (so the 404s mean something)", str(own))

    print("  the PDF download")
    for who, p, want in (("A", A, 200), ("admin", admin, 200), ("B", B, 404)):
        r = p.c.get(f"/reports/{W.FC}/download")
        check(r.status_code == want, f"{who} GET /reports/{{id}}/download -> {want}", brief(r))
        r = p.c.head(f"/reports/{W.FC}/download")
        check(r.status_code == want, f"{who} HEAD /reports/{{id}}/download -> {want}", str(r.status_code))

    print("  a stranger's request makes no thumbnail or preview")
    photo = next((p for p in case_paths(W.FC) if p.name == "photo_01.jpg"), None)
    need(photo is not None, "A's case has photos/photo_01.jpg on disk", str(case_paths(W.FC)))
    for derived in (photo.with_name("photo_01_thumb.jpg"), photo.with_name("photo_01_preview.webp")):
        derived.unlink(missing_ok=True)
        r = B.c.get(url_of(derived))
        check(r.status_code == 404 and not derived.exists(), f"B asking for {derived.name} gets 404 and no file is made",
              f"{r.status_code} exists={derived.exists()}")
        r = A.c.get(url_of(derived))
        check(r.status_code == 200 and derived.exists(), f"A asking for {derived.name} gets it (made on demand)", f"{r.status_code}")
    intake_photo = next((p for p in case_paths(W.AD) if "_" not in p.stem), None)
    need(intake_photo is not None, "A's draft photo is on disk", str(case_paths(W.AD)))
    thumb = intake_photo.with_name(f"{intake_photo.stem}_thumb.jpg")
    thumb.unlink(missing_ok=True)
    r = B.c.get(url_of(thumb))
    check(r.status_code == 404 and not thumb.exists(), "same for a draft photo's thumbnail", f"{r.status_code} exists={thumb.exists()}")

    print("  path tricks")
    rel = url_of(photo)[len("/files/"):]  # incidents/YYYY/MM/<FC>/photos/photo_01.jpg
    parts = rel.split("/")
    y, m = parts[1], parts[2]
    bc_dirs = list(STORAGE.glob(f"incidents/*/*/{W.BC}"))
    need(bc_dirs, "B's own case has a folder (the start of the tricks)", str(bc_dirs))
    bc_folder = bc_dirs[0].relative_to(STORAGE).as_posix()
    bd_folder = f"intake/{W.BD}"
    fc_photo = f"{W.FC}/photos/photo_00.jpg"
    secret = (STORAGE / f"incidents/{y}/{m}/{fc_photo}").read_bytes()
    ad_file = intake_photo.name
    bs = "\\"
    tricks_raw = [
        f"/files/{bc_folder}/../{fc_photo}",
        f"/files/{bd_folder}/../../incidents/{y}/{m}/{fc_photo}",
        f"/files/{bc_folder}/../../../../intake/{W.AD}/{ad_file}",
        f"/files/{bd_folder}/../{W.AD}/{ad_file}",
        f"/files/incidents/{y}/{m}/{W.FC}/photos/../photos/photo_00.jpg",
    ]
    tricks_http = [
        f"/files/{bc_folder}/%2e%2e/{fc_photo}",
        f"/files/{bc_folder}/%2e%2e%2f{fc_photo}",
        f"/files/{bc_folder}/..%2f{W.FC}%2fphotos%2fphoto_00.jpg",
        f"/files/{bc_folder}/..%5c{W.FC}%5cphotos%5cphoto_00.jpg",
        f"/files/{bc_folder}/..{bs}{W.FC}{bs}photos{bs}photo_00.jpg",
        f"/files/incidents{bs}{y}{bs}{m}{bs}{W.FC}{bs}photos{bs}photo_00.jpg",
        f"/files/incidents/{y}/{m}/{W.FC}{bs}photos{bs}photo_00.jpg",
        f"/files/{bd_folder}/%2e%2e/{W.AD}/{ad_file}",
        f"/files/%2e%2e/{STORAGE.name}/incidents/{y}/{m}/{fc_photo}",
    ]
    for path in tricks_raw:
        status, body = raw_get(B, path)
        check(status == 404 and secret not in body, f"B raw GET {path[7:]} -> 404", f"{status}")
    for path in tricks_http:
        r = B.c.get(path)
        check(r.status_code == 404 and secret not in r.content, f"B GET {path[7:]} -> 404", brief(r))
    bare = [f"/files/incidents/{y}/{m}/{W.FC}", f"/files/incidents/{y}/{m}/{W.FC}/", f"/files/incidents/{y}/{m}/{W.FC}/photos/",
            f"/files/intake/{W.AD}", f"/files/intake/{W.AD}/", f"/files/incidents/", f"/files/incidents/{y}/{m}/", "/files/intake/"]
    for path in bare:
        codes = {who: p.c.get(path).status_code for who, p in (("B", B), ("A", A), ("admin", admin))}
        check(all(c == 404 for c in codes.values()), f"bare folder {path[7:]} -> 404 for B, A and the admin", str(codes))
    thumb0 = STORAGE / f"incidents/{y}/{m}/{W.FC}/photos/photo_00_thumb.jpg"
    thumb0.unlink(missing_ok=True)
    status, _ = raw_get(B, f"/files/{bc_folder}/../{W.FC}/photos/photo_00_thumb.jpg")
    r = B.c.get(f"/files/{bc_folder}/%2e%2e/{W.FC}/photos/photo_00_thumb.jpg")
    check(status == 404 and r.status_code == 404 and not thumb0.exists(),
          "a thumbnail asked for through a trick path isn't made either", f"raw={status} encoded={r.status_code} exists={thumb0.exists()}")


# ================================================================= assigning
def assigning() -> None:
    A, B, S, admin, D = W.A, W.B, W.S, W.admin, W.D
    X1 = new_manual_case(A, plate="SAS1111A", parts=("Rear Bumper",), desc="To be assigned")

    def history(case_id):
        return [h for h in (jd(admin.c.get(f"/reports/{case_id}")).get("history") or []) if isinstance(h, dict)]

    r = admin.c.patch(f"/reports/{X1}/owner", json={"owner_id": B.id})
    check(r.status_code == 200 and owner_id(jd(r)) == B.id, "the admin assigns A's case to B -> {owner: B}", brief(r))
    check(B.c.get(f"/reports/{X1}").status_code == 200, "B can now open it")
    check(A.c.get(f"/reports/{X1}").status_code == 404, "A can't any more (404)")
    h = history(X1)
    last = h[-1] if h else {}
    want = f"Assigned to {B.name} (@{B.username})"
    check(last.get("action") == "assigned" and last.get("detail") == want, f"history: 'assigned', '{want}'", repr(last))
    n = len(h)
    r = admin.c.patch(f"/reports/{X1}/owner", json={"owner_id": B.id})
    check(r.status_code == 200 and owner_id(jd(r)) == B.id, "assigning the same owner again succeeds", brief(r))
    check(len(history(X1)) == n, "...and changes nothing (no new history entry)", f"{n} -> {len(history(X1))}")

    r = admin.c.patch(f"/reports/{X1}/owner", json={"owner_id": None})
    check(r.status_code == 200 and jd(r).get("owner", "missing") is None, "the admin unassigns it -> {owner: null}", brief(r))
    last = (history(X1) or [{}])[-1]
    check(last.get("action") == "assigned" and last.get("detail") == "Unassigned", "history: 'assigned', 'Unassigned'", repr(last))
    check(B.c.get(f"/reports/{X1}").status_code == 404, "B lost it")
    check(S.c.get(f"/reports/{X1}").status_code == 200, "staff S sees the Unassigned case")
    check(X1 in (ids_of(admin.c.get("/reports?scope=unassigned")) or set()), "it is in the admin's Unassigned list")
    sug = jd(admin.c.get(f"/reports/{X1}")).get("suggested_owner")
    check(isinstance(sug, dict) and sug.get("id") == A.id, "suggested_owner points at A, who filed and edited it", repr(sug))

    r = admin.c.patch(f"/reports/{X1}/owner", json={"owner_id": D.id})
    check(r.status_code == 409 and code_of(r) == "owner_inactive", "assigning to a disabled account -> 409 owner_inactive", brief(r))
    r = admin.c.patch(f"/reports/{X1}/owner", json={"owner_id": "nosuchuser0"})
    check(400 <= r.status_code < 500, "assigning to an account that doesn't exist is refused (4xx)", brief(r))
    check(owner_id(jd(admin.c.get(f"/reports/{X1}"))) is None, "neither refusal changed the owner")
    r = admin.c.patch(f"/reports/{MADE_UP}/owner", json={"owner_id": A.id})
    check(r.status_code == 404, "assigning a made-up case -> 404", brief(r))
    r = admin.c.patch(f"/reports/{W.AD}/owner", json={"owner_id": B.id})
    check(r.status_code == 409 and code_of(r) == "owner_draft", "a draft still in intake can't be assigned -> 409 owner_draft", brief(r))
    check(owner_id(jd(A.c.get(f"/intake/{W.AD}"))) == A.id, "the draft is still A's")
    r = S.c.patch(f"/reports/{X1}/owner", json={"owner_id": S.id})
    check(r.status_code == 403, "staff S can't assign (403)", brief(r))
    r = A.c.patch(f"/reports/{W.FC}/owner", json={"owner_id": B.id})
    check(r.status_code == 403 and owner_id(jd(A.c.get(f"/reports/{W.FC}"))) == A.id, "A can't give A's own case away (403)", brief(r))
    r = admin.c.patch(f"/reports/{X1}/owner", json={"owner_id": A.id})
    check(r.status_code == 200 and A.c.get(f"/reports/{X1}").status_code == 200, "assigned back to A, A can open it again", brief(r))


# ================================================================= temp files
def temp_files() -> None:
    A, B, admin = W.A, W.B, W.admin
    tmp = STORAGE / "tmp"
    tmp.mkdir(exist_ok=True)

    def temp_file(prefix: str) -> Path:
        path = tmp / f"{prefix}{uuid.uuid4().hex}.jpg"
        path.write_bytes(jpeg())
        return path

    a_tmp, tg_tmp, b_tmp = temp_file(f"u{A.id}-"), temp_file("tg-"), temp_file(f"u{B.id}-")
    for label, path, sent in (("A's dashboard upload (u{A}-...)", a_tmp, str(a_tmp)),
                              ("a Telegram temp photo (tg-...)", tg_tmp, str(tg_tmp)),
                              ("A's upload written with forward slashes", a_tmp, a_tmp.as_posix())):
        before = ids_of(admin.c.get("/reports")) or set()
        r = B.c.post("/reports", json={"draft": case_data("stolen", "SBT1111A"), "temp_photo_paths": [sent]})
        after = ids_of(admin.c.get("/reports")) or set()
        check(400 <= r.status_code < 500 and path.exists() and after == before,
              f"B can't attach {label} with POST /reports", f"{brief(r)} file_exists={path.exists()}")
    before = len(jd(B.c.get(f"/reports/{W.BC}")).get("photo_urls") or [])
    r = B.c.put(f"/reports/{W.BC}", json={"draft": {"category": ["Other"], "description": "x"}, "temp_photo_paths": [str(a_tmp)]})
    after = len(jd(B.c.get(f"/reports/{W.BC}")).get("photo_urls") or [])
    check(400 <= r.status_code < 500 and a_tmp.exists() and after == before,
          "B can't attach A's upload with PUT /reports/{id} either", f"{brief(r)} photos {before}->{after}")
    r = B.c.put(f"/reports/{W.BC}", json={"draft": {"category": ["Other"], "description": "x"}, "temp_photo_paths": [str(tg_tmp)]})
    check(400 <= r.status_code < 500 and tg_tmp.exists(), "...nor the Telegram temp photo", brief(r))

    r = B.c.post("/reports", json={"draft": case_data("own photo", "SBT2222A"), "temp_photo_paths": [str(b_tmp)]})
    new_id = jd(r).get("id")
    photos = len(jd(B.c.get(f"/reports/{new_id}")).get("photo_urls") or []) if new_id else 0
    check(r.status_code == 200 and photos == 1 and not b_tmp.exists(), "B attaches B's own upload (and it leaves tmp)",
          f"{brief(r)} photos={photos}")
    r = A.c.post("/reports", json={"draft": case_data("own photo", "SAT2222A"), "temp_photo_paths": [str(a_tmp)]})
    a_new = jd(r).get("id")
    photos = len(jd(A.c.get(f"/reports/{a_new}")).get("photo_urls") or []) if a_new else 0
    check(r.status_code == 200 and photos == 1, "A attaches A's own upload", f"{brief(r)} photos={photos}")
    W.extra_A_cases = getattr(W, "extra_A_cases", []) + ([a_new] if a_new else [])

    r = admin.c.post("/reports/analyze-photos", data={"description": "rear damage"},
                     files=[("photos", ("IMG_1.jpg", jpeg(), "image/jpeg"))])
    paths = jd(r).get("temp_photo_paths") or []
    check(r.status_code == 200 and paths and all(Path(p).name.startswith(f"u{admin.id}-") for p in paths),
          "uploads through analyze-photos are named u{user_id}-...", f"{brief(r)}")
    for p in paths:
        Path(p).unlink(missing_ok=True)


# ================================================================= deleting
def deleting() -> None:
    A, B, admin = W.A, W.B, W.admin
    DEL1 = new_filed_case(A, photos=2, parts=("Rear Bumper",), plate="SDL1111A", desc="To be deleted")
    folders = list(STORAGE.glob(f"incidents/*/*/{DEL1}"))
    need(len(folders) == 1, "the new case has one month folder", str(folders))
    y, m = int(folders[0].parent.parent.name), int(folders[0].parent.name)
    py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
    # A case whose files sit in two month folders: photos from one month,
    # the PDF re-rendered in a later one (agent6 M4).
    old = STORAGE / f"incidents/{py:04d}/{pm:02d}/{DEL1}/photos/photo_00.jpg"
    old.parent.mkdir(parents=True)
    shutil.copy(folders[0] / "photos/photo_00.jpg", old)
    leftover = STORAGE / f"intake/{DEL1}/left_over.jpg"
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(jpeg())
    r = A.c.get(url_of(old))
    check(r.status_code == 200, "A opens the photo in the other month's folder", brief(r))
    check(B.c.get(url_of(old)).status_code == 404, "B doesn't")
    urls = [url_of(p) for p in case_paths(DEL1)]
    need(len(urls) >= 5, "the case's files are all on disk", str(urls))
    r = admin.c.delete(f"/reports/{DEL1}")
    check(r.status_code == 200, "the admin deletes the case", brief(r))
    left = sorted(p.relative_to(STORAGE).as_posix() for p in STORAGE.glob(f"incidents/*/*/{DEL1}"))
    check(not left, "every incidents/*/*/{id} folder is gone, both months", str(left))
    check(not (STORAGE / f"intake/{DEL1}").exists(), "intake/{id} is gone too")
    for who, p in (("admin", admin), ("A", A)):
        still = [u for u in urls if p.c.get(u).status_code != 404]
        check(not still, f"every /files URL of the deleted case gives {who} 404", str(still[:3]))
    check(A.c.get(f"/reports/{DEL1}").status_code == 404, "the case itself is gone (404)")

    DEL2, _ = new_draft(A, photos=1)
    urls = [url_of(p) for p in case_paths(DEL2)]
    r = A.c.delete(f"/reports/{DEL2}")
    check(r.status_code == 200 and not (STORAGE / f"intake/{DEL2}").exists(), "A throws a draft away: its folder goes", brief(r))
    check(all(A.c.get(u).status_code == 404 for u in urls), "...and its /files URLs give 404")


# ================================================================= start-up upgrade
_BASE_MODELS = {}


def base_models():
    """The models of BASE_COMMIT, loaded on their own, so a database can be
    made exactly as it was before P1 without running any P1 code."""
    if "mod" not in _BASE_MODELS:
        r = subprocess.run(["git", "show", f"{BASE_COMMIT}:./app/reports/models.py"], cwd=ROOT, capture_output=True, text=True)
        need(r.returncode == 0, f"read app/reports/models.py at the base commit {BASE_COMMIT}", r.stderr[-300:])
        name = f"carlink_models_{BASE_COMMIT}"
        mod = types.ModuleType(name)
        sys.modules[name] = mod
        exec(compile(r.stdout, f"{BASE_COMMIT}:app/reports/models.py", "exec"), mod.__dict__)
        _BASE_MODELS["mod"] = mod
    return _BASE_MODELS["mod"]


EXPECTED_OWNERS = {
    "r_created": "xav",      # "created" by xav
    "r_finalized": "yan",    # only "finalized" by yan
    "r_both": "xav",         # created wins over finalized
    "r_disabled": "dee",     # creator now disabled stays owner
    "r_draft": "zed",        # a draft is a case row like any other
    "r_none": None,          # Telegram, no history
    "r_edited": None,        # only edited: a hint, not an owner
    "r_signed": None,        # only signed off: a hint, not an owner
    "r_anon": None,          # "created" with no account
}


def make_old_db(folder: Path, with_rows: bool = True) -> tuple[Path, dict]:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    base = base_models()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "old.db"
    eng = create_engine(f"sqlite:///{path.as_posix()}")
    base.Base.metadata.create_all(eng)
    with eng.begin() as conn:  # what the base commit's start-up adds
        # The app has always opened its database in WAL mode, and the mode is
        # stored in the file: a real pre-P1 database is already WAL.
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_intake_photos_report_sha ON intake_photos (report_id, sha256)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username ON users (username)"))
    ids: dict = {}
    if with_rows:
        with Session(eng) as s:
            def user(key, role="user", active=True):
                u = base.User(email=f"{key}@old.example.com", username=key, name=f"Old {key.title()}", role=role,
                              password_hash="!", active=active)
                s.add(u)
                s.flush()
                ids[key] = u.id

            def entry(action, who=None, case=None, detail=""):
                s.add(base.AuditEntry(report_id=ids.get(case), user_id=ids.get(who), user_name=f"Old {who.title()}" if who else "",
                                      action=action, detail=detail))

            def report(key, status="confirmed", channel="manual", sign_off=None):
                r = base.Report(channel=channel, reporter_chat_id="dashboard" if channel == "manual" else "5550001", status=status,
                                data={"description": key, "category": ["Other"], "sign_off": sign_off or {"status": "Draft"}},
                                photo_paths=[])
                s.add(r)
                s.flush()
                ids[key] = r.id

            user("adm", role="admin")                 # first admin, from the server command: no history
            user("xav")
            entry("user_created", "adm", detail="xav@old.example.com (user)")
            user("yan")
            entry("invite_accepted", "yan", detail="yan@old.example.com (user), invited by Old Adm")
            user("zed")
            entry("signed_up", "zed", detail="zed@old.example.com")
            user("dee", active=False)
            entry("user_created", "adm", detail="dee@old.example.com (user)")
            report("r_created")
            entry("created", "xav", "r_created")
            entry("edited", "yan", "r_created")
            report("r_finalized")
            entry("finalized", "yan", "r_finalized")
            report("r_both")
            entry("created", "xav", "r_both")
            entry("finalized", "yan", "r_both")
            report("r_disabled")
            entry("created", "dee", "r_disabled")
            report("r_draft", status="intake")
            entry("created", "zed", "r_draft")
            report("r_none", channel="telegram")
            report("r_edited")
            entry("edited", "yan", "r_edited")
            report("r_signed", status="Signed Off", sign_off={"status": "Signed Off", "signed_by_user_id": ids["yan"]})
            entry("signed_off", "yan", "r_signed", detail="yan@old.example.com")
            report("r_anon")
            s.add(base.AuditEntry(report_id=ids["r_anon"], user_id=None, user_name="", action="created", detail=""))
            s.commit()
    eng.dispose()
    return path, ids


def _error_line(stderr: str) -> str:
    """The exception line of a traceback (SQLAlchemy ends with a link line)."""
    lines = [ln.strip() for ln in (stderr or "").strip().splitlines() if ln.strip()]
    errors = [ln for ln in lines if re.match(r"^[\w.]+(Error|Exception):", ln)]
    return (errors[-1] if errors else (lines[-1] if lines else ""))[:300]


_INIT_SCRIPT = (
    "import sys, time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "start = float(sys.argv[2])\n"
    "from app.reports.db import init_db\n"
    "while time.time() < start:\n"
    "    time.sleep(0.002)\n"
    "init_db()\n"
    "print('init_db done')\n"
)


def _env_for(db_path: Path) -> dict:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    env["STORAGE_DIR"] = str(db_path.parent / "storage")
    return env


def init_db_on(db_path: Path, label: str) -> bool:
    r = subprocess.run([sys.executable, "-c", _INIT_SCRIPT, str(ROOT), "0"], cwd=ROOT, env=_env_for(db_path),
                       capture_output=True, text=True, timeout=180)
    return check(r.returncode == 0, label, _error_line(r.stderr) or (r.stdout or "")[-300:])


def sql(db_path: Path, statement: str, params=()):
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(statement, params)
        rows = cur.fetchall()
        con.commit()
        return rows
    finally:
        con.close()


def owners_in(db_path: Path, ids: dict) -> dict | str:
    by_id = {v: k for k, v in ids.items()}
    try:
        rows = sql(db_path, "SELECT id, owner_id FROM reports")
    except sqlite3.Error as exc:
        return f"can't read reports.owner_id: {exc}"
    return {by_id.get(rid): by_id.get(owner, owner) for rid, owner in rows}


def ticks_in(db_path: Path, ids: dict) -> dict | str:
    by_id = {v: k for k, v in ids.items()}
    try:
        rows = sql(db_path, f"SELECT id, {', '.join(TICKS)} FROM users")
    except sqlite3.Error as exc:
        return f"can't read the tick columns: {exc}"
    return {by_id.get(r[0]): dict(zip(TICKS, r[1:])) for r in rows}


def mark_exists(db_path: Path, key: str = "upgrade:case_owners_v1") -> bool:
    try:
        return bool(sql(db_path, "SELECT 1 FROM app_settings WHERE key = ?", (key,)))
    except sqlite3.Error:
        return False


def check_owners(db_path: Path, ids: dict, keys, label: str) -> None:
    got = owners_in(db_path, ids)
    if isinstance(got, str):
        check(False, label, got)
        return
    for key in keys:
        want = EXPECTED_OWNERS.get(key, key)
        check(got.get(key) == want, f"{label}: {key} -> {want or 'Unassigned'}", f"owner={got.get(key)!r}")


def upgrade_backfill() -> None:
    db_path, ids = make_old_db(_tmp / "upgrade-1")
    W.up_db, W.up_ids = db_path, ids
    need(init_db_on(db_path, "init_db upgrades a database made before P1"), "first init_db succeeded")
    check_owners(db_path, ids, EXPECTED_OWNERS, "owner backfill")
    check(mark_exists(db_path), "the done mark upgrade:case_owners_v1 is written")
    try:
        names = [row[0] for row in sql(db_path, "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'reports'")]
        indexed = {name: [c[2] for c in sql(db_path, f"PRAGMA index_info('{name}')")] for name in names}
    except sqlite3.Error as exc:
        indexed = {"error": str(exc)}
    check(any("owner_id" in cols for cols in indexed.values()), "reports.owner_id has an index", str(indexed))


def upgrade_runs_once() -> None:
    need(hasattr(W, "up_db"), "the upgraded database from the backfill section is there", "the backfill section didn't build it")
    db_path, ids = W.up_db, W.up_ids
    # An admin deliberately unassigns r_created (and unticks xav) after the upgrade.
    sql(db_path, "UPDATE reports SET owner_id = NULL WHERE id = ?", (ids["r_created"],))
    sql(db_path, "INSERT INTO audit_log (report_id, user_id, user_name, action, detail, created_at) VALUES (?, ?, 'Old Adm', 'assigned', 'Unassigned', CURRENT_TIMESTAMP)",
        (ids["r_created"], ids["adm"]))
    sql(db_path, "UPDATE users SET sees_all_cases = 0 WHERE id = ?", (ids["xav"],))
    need(init_db_on(db_path, "init_db runs again (the next start)"), "second init_db succeeded")
    got = owners_in(db_path, ids)
    check(isinstance(got, dict) and got.get("r_created") is None, "the admin's Unassigned stays Unassigned", str(got)[:200])
    t = ticks_in(db_path, ids)
    check(isinstance(t, dict) and not t.get("xav", {}).get("sees_all_cases"), "the admin's unticking stays", str(t)[:200])
    check_owners(db_path, ids, ("r_finalized", "r_both", "r_none"), "other owners untouched")

    # Deleting the mark is the way to re-run the backfill on purpose: a row
    # written meanwhile with no owner (old code mid-deploy) then gets one.
    sql(db_path, "INSERT INTO reports (id, type, status, channel, reporter_chat_id, data, photo_paths, created_at) "
                 "VALUES ('r_new0000000', 'security_incident', 'confirmed', 'manual', 'dashboard', '{}', '[]', CURRENT_TIMESTAMP)")
    sql(db_path, "INSERT INTO audit_log (report_id, user_id, user_name, action, detail, created_at) VALUES ('r_new0000000', ?, 'Old Xav', 'created', '', CURRENT_TIMESTAMP)",
        (ids["xav"],))
    ids["r_new"] = "r_new0000000"
    EXPECTED_OWNERS["r_new"] = "xav"
    sql(db_path, "DELETE FROM app_settings WHERE key = 'upgrade:case_owners_v1'")
    need(init_db_on(db_path, "init_db after deleting upgrade:case_owners_v1"), "third init_db succeeded")
    check_owners(db_path, ids, ("r_new", "r_none", "r_finalized"), "re-run backfill")
    check(mark_exists(db_path), "the done mark is written again")


def upgrade_grandfather() -> None:
    db_path, ids = make_old_db(_tmp / "upgrade-2")
    need(init_db_on(db_path, "init_db on a fresh pre-P1 database"), "init_db succeeded")
    t = ticks_in(db_path, ids)
    need(isinstance(t, dict), "the three tick columns exist", str(t))
    for key, how in (("xav", "made by an admin"), ("yan", "from an invite link"), ("dee", "made by an admin, now disabled")):
        check(all(t.get(key, {}).get(k) for k in TICKS), f"an account {how} gets all three ticks on", str(t.get(key)))
    check(not any(t.get("zed", {}).get(k) for k in TICKS), "a self-signup (signed_up entry) gets all three off", str(t.get("zed")))

    # Only NULL values change: with the tick marks deleted (a re-run on
    # purpose), an explicit 0 stays 0 while a NULL is filled in again.
    sql(db_path, "UPDATE users SET sees_all_cases = 0, may_sign_off = 0 WHERE id = ?", (ids["xav"],))
    sql(db_path, "UPDATE users SET ai_company_key_allowed = NULL, may_sign_off = NULL WHERE id = ?", (ids["yan"],))
    sql(db_path, "DELETE FROM app_settings WHERE key LIKE 'upgrade:%' AND key != 'upgrade:case_owners_v1'")
    need(init_db_on(db_path, "init_db with the tick done marks deleted"), "init_db succeeded")
    t = ticks_in(db_path, ids)
    need(isinstance(t, dict), "ticks readable", str(t))
    check(not t["xav"]["sees_all_cases"] and not t["xav"]["may_sign_off"], "an explicit 'off' is not turned back on", str(t["xav"]))
    check(t["xav"]["ai_company_key_allowed"], "xav's untouched tick is still on", str(t["xav"]))
    check(t["yan"]["ai_company_key_allowed"] and t["yan"]["may_sign_off"], "a NULL tick is filled in again", str(t["yan"]))
    check(not any(t["zed"][k] for k in TICKS), "the self-signup stays off", str(t["zed"]))


def upgrade_race() -> None:
    rounds = [("pre-P1 database", True)] * 4 + [("brand-new database", False)] * 2
    for n, (kind, old) in enumerate(rounds, start=1):
        folder = _tmp / f"race-{n}"
        if old:
            db_path, ids = make_old_db(folder)
        else:
            folder.mkdir(parents=True)
            db_path, ids = folder / "new.db", {}
        start = time.time() + 6
        procs = [subprocess.Popen([sys.executable, "-c", _INIT_SCRIPT, str(ROOT), str(start)], cwd=ROOT, env=_env_for(db_path),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
        results = []
        for p in procs:
            try:
                out, err = p.communicate(timeout=180)
            except subprocess.TimeoutExpired:
                p.kill()
                out, err = p.communicate()
            results.append((p.returncode, _error_line(err) if p.returncode else ""))
        check(all(rc == 0 for rc, _ in results), f"round {n}: init_db from 3 processes at once on a {kind}: all succeed",
              str(results))
        if old:
            got = owners_in(db_path, ids)
            check(isinstance(got, dict) and got.get("r_created") == "xav" and got.get("r_finalized") == "yan"
                  and got.get("r_none") is None and mark_exists(db_path),
                  f"round {n}: the backfill ran once and got the owners right", str(got)[:200])
            t = ticks_in(db_path, ids)
            check(isinstance(t, dict) and all(t["xav"][k] for k in TICKS) and not any(t["zed"][k] for k in TICKS),
                  f"round {n}: the ticks were grandfathered", str(t)[:200])
        else:
            try:
                cols = {r[1] for r in sql(db_path, "PRAGMA table_info(reports)")}
            except sqlite3.Error as exc:
                cols = {str(exc)}
            check("owner_id" in cols, f"round {n}: the new database has reports.owner_id", str(sorted(cols)))


# ================================================================= main
def main() -> int:
    print(f"temporary storage: {STORAGE}")
    print(f"free disk: {shutil.disk_usage(STORAGE).free / 1e9:.0f} GB (B2 refuses uploads under 3 GB)")
    with browser():  # runs the app's start-up (init_db) once
        _section.update(tag="B1", name="setup")
        print("\n[B1] setup: people and their cases")
        try:
            setup()
        except Stop as exc:
            print(f"\nSETUP FAILED: {exc}\nThe remaining sections need these people and cases, so they were not run.")
            return summary()
        except Exception as exc:
            FAILURES.append(("B1", "setup", f"setup stopped by {type(exc).__name__}: {exc} (test line {_test_frame(exc)})"))
            return summary()
        run_section("B1", "lists: scopes, owner on rows, analytics, taxonomy", lists)
        run_section("B1", "hidden cases: every case route as stranger B", hidden_cases)
        run_section("B1", "who may do what: edit, sign-off, reopen, delete, assign", who_may_do_what)
        run_section("B1", "files: photos, thumbnails, previews, PDFs, path tricks", files)
        run_section("B1", "assigning: PATCH /reports/{id}/owner", assigning)
        run_section("B1", "temp files: temp_photo_paths ownership", temp_files)
        run_section("B1", "deleting: every folder of the case goes", deleting)
    run_section("B1", "start-up upgrade: owner backfill", upgrade_backfill)
    run_section("B1", "start-up upgrade: runs once (done mark)", upgrade_runs_once)
    run_section("B1", "start-up upgrade: grandfathering the three ticks", upgrade_grandfather)
    run_section("B1", "start-up upgrade: init_db from 3 processes at once", upgrade_race)
    return summary()


def summary() -> int:
    print("\n" + "=" * 78)
    print(f"test_case_access: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    by_section: dict = {}
    for tag, section, label in FAILURES:
        by_section.setdefault(f"[{tag}] {section}", []).append(label)
    for section, labels in by_section.items():
        print(f"\n  {section}: {len(labels)} failed")
        for label in labels:
            print(f"    - {label}")
    if not FAILURES:
        print("\nALL CASE ACCESS CHECKS PASSED")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
