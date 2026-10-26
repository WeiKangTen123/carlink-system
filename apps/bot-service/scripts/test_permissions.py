"""Black-box tests for release P1 "Case privacy": the decision recorded for
every API route, the sign-in rules on all of them, the admin's Users screen
(the three ticks and the case counts), GET /auth/me, and -- in their own
labelled sections -- the abuse limits agent B2 adds after B1.

    python scripts/test_permissions.py          # everything
    python scripts/test_permissions.py --b1     # only B1's part (backend core)
    python scripts/test_permissions.py --b2     # only B2's part (abuse limits)

Written from the spec, not from the code: plans/2026-10-26-roles-ai-telegram/
p1_build_brief.md (wins on any conflict), agent5_integration_report.md
sections 1, 2, 3a and 6, and agent6_security_report.md part (c).

Reading the result: every check prints ok/FAIL and the run goes on. The
summary counts B1 and B2 separately; "[B2]" failures mean B2's limits
aren't there (yet). A new endpoint fails the route table check until its
decision is added to DECISIONS below.

Offline and self-contained: a temporary STORAGE_DIR and database, Gemini and
Chromium replaced by fakes, and every outside network connection refused.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections import namedtuple
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

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
_tmp = Path(tempfile.mkdtemp(prefix="permissions-test-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
# Forced, not setdefault: never a real STORAGE_DIR from the environment.
os.environ["STORAGE_DIR"] = str(STORAGE)
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake"
os.environ["GEMINI_MIN_CALL_INTERVAL_SECONDS"] = "0"
os.environ["GEMINI_MAX_REQUESTS_PER_MINUTE"] = "100000"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
# Keeps the WhatsApp router (mounted only with Twilio credentials) out of the
# route table, so the table is the same on every machine.
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ.pop("ENABLE_API_DOCS", None)

# ---------------------------------------------------------------- offline fakes
_real_connect = socket.socket.connect


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConnectionRefusedError(f"test is offline: refused a connection to {address!r}")
    return _real_connect(sock, address)


socket.socket.connect = _offline_connect

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

# Chromium: page.pdf() writes a tiny PDF, optionally slowly, counting how
# many renders run at once (the B2 render limit).
FAKE_PDF = b"%PDF-1.4\n% fake PDF written by the test renderer\n%%EOF\n"
RENDER = {"delay": 0.0, "active": 0, "max_active": 0}
_render_lock = threading.Lock()


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
        with _render_lock:
            RENDER["active"] += 1
            RENDER["max_active"] = max(RENDER["max_active"], RENDER["active"])
        try:
            if RENDER["delay"]:
                time.sleep(RENDER["delay"])
            if path:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_bytes(FAKE_PDF)
            return FAKE_PDF
        finally:
            with _render_lock:
                RENDER["active"] -= 1


def _fake_sync_playwright():
    return _FakePW()


import playwright.sync_api as _pw  # noqa: E402

_REAL_SYNC_PLAYWRIGHT = _pw.sync_playwright
_pw.sync_playwright = _fake_sync_playwright

# Every HTML page the PDF template produces, whichever way it then reaches
# Chromium (for the unverified-owner banner).
import jinja2  # noqa: E402

TEMPLATE_HTML: list[str] = []
_real_template_render = jinja2.environment.Template.render


def _recording_render(self, *args, **kwargs):
    html = _real_template_render(self, *args, **kwargs)
    TEMPLATE_HTML.append(html)
    return html


jinja2.environment.Template.render = _recording_render

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

from app.api.main import app  # noqa: E402
from app.reports.db import SessionLocal, engine  # noqa: E402
from app.reports.models import AuditEntry, IntakeSession, PastAnnexLine  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

for _name, _mod in list(sys.modules.items()):
    if _name.startswith("app.") and getattr(_mod, "sync_playwright", None) is _REAL_SYNC_PLAYWRIGHT:
        setattr(_mod, "sync_playwright", _fake_sync_playwright)

# ================================================================= the route table
# One decision per operation in app.openapi()["paths"]. Rules:
#   public      no session needed
#   signed-in   any signed-in user (a temporary-password user is refused)
#   self        the signed-in user's own account
#   admin       admins only; anyone else gets 403
#   case-view   may see the case; anyone else gets 404, the same as a missing case
#   case-edit   case-view, then may work on it (403 code not_allowed)
#   special     its own rule, written in the note
# A new endpoint fails check_route_table() until its decision is recorded here.
DECISIONS = {
    ("GET", "/health"): ("public", "load balancer check"),
    ("POST", "/auth/login"): ("public", ""),
    ("POST", "/auth/logout"): ("public", "a stale cookie can always be cleared; also sends Clear-Site-Data (B2)"),
    ("GET", "/auth/signup"): ("public", "are signups open?"),
    ("POST", "/auth/signup"): ("public", "new accounts start with all three ticks off"),
    ("POST", "/auth/links/lookup"): ("public", ""),
    ("POST", "/auth/links/accept"): ("public", ""),

    ("GET", "/auth/me"): ("self", "adds can_view_all_cases, may_sign_off, ai_allowed; open to temporary-password users"),
    ("PUT", "/auth/me"): ("self", "profile only: never role or ticks"),
    ("POST", "/auth/password"): ("self", "open to temporary-password users"),

    ("GET", "/reports"): ("signed-in", "?scope=mine|all|unassigned; plain users mine only (else 403 scope_not_allowed)"),
    ("POST", "/reports"): ("signed-in", "owner = caller; temp_photo_paths only the caller's u{id}- files"),
    ("POST", "/intake"): ("signed-in", "owner = caller; 5 open drafts per person, staff exempt from the shared 30 (B2)"),
    ("GET", "/analytics/summary"): ("signed-in", "follows the list scope"),
    ("GET", "/taxonomy"): ("signed-in", "follows the list scope; skips drafts"),
    ("GET", "/settings"): ("signed-in", "company name"),
    ("GET", "/parts-history"): ("special", "signed-in; plates and file names admin only; prices admin or sees_all_cases (B2)"),
    ("POST", "/reports/analyze-photos"): ("special", "signed-in; AI allowed (409 ai_key_missing) before any file is saved (B2)"),

    ("GET", "/users"): ("admin", "with the three ticks and case counts"),
    ("POST", "/users"): ("admin", ""),
    ("PATCH", "/users/{user_id}"): ("admin", "sets the three ticks; each change in the audit log"),
    ("POST", "/users/{user_id}/reset-password"): ("admin", ""),
    ("POST", "/users/{user_id}/reset-link"): ("admin", ""),
    ("PUT", "/signup-settings"): ("admin", ""),
    ("GET", "/account-links"): ("admin", ""),
    ("POST", "/account-links"): ("admin", ""),
    ("DELETE", "/account-links/{link_id}"): ("admin", ""),
    ("POST", "/parts-history/import"): ("admin", ""),
    ("DELETE", "/parts-history/{case_ref}"): ("admin", ""),
    ("POST", "/reports/ai-enrich-all"): ("admin", ""),
    ("PUT", "/settings"): ("admin", ""),
    ("GET", "/system/info"): ("admin", ""),
    ("GET", "/setup/llm-keys"): ("admin", ""),
    ("POST", "/setup/llm-keys"): ("admin", ""),
    ("DELETE", "/setup/llm-keys/{key_id}"): ("admin", ""),
    ("POST", "/setup/test/{service}"): ("admin", ""),
    ("PATCH", "/reports/{report_id}/owner"): ("admin", "on a case: 404 unknown, 409 owner_inactive / owner_draft"),

    ("GET", "/reports/{report_id}"): ("case-view", "adds owner, permissions; suggested_owner for admins on Unassigned"),
    ("GET", "/reports/{report_id}/download"): ("case-view", ""),
    ("HEAD", "/reports/{report_id}/download"): ("case-view", ""),
    ("GET", "/reports/{report_id}/annex-suggestions"): ("case-view", ""),
    ("GET", "/intake/{report_id}"): ("case-view", "adds owner, permissions"),

    ("PUT", "/reports/{report_id}"): ("case-edit", "Our Ref (report_id) staff only (B2)"),
    ("PATCH", "/reports/{report_id}/damage-items/{item_index}"): ("case-edit", ""),
    ("POST", "/reports/{report_id}/ai-enrich"): ("case-edit", "not a Gemini call"),
    ("PUT", "/reports/{report_id}/annex-a"): ("case-edit", ""),
    ("POST", "/reports/{report_id}/render-pdf"): ("case-edit", "per-user rate limit 429 render_busy; 2 renders at once (B2)"),
    ("POST", "/intake/{report_id}/photos"): ("case-edit", "507 disk_full under 3 GB free (B2)"),
    ("PATCH", "/intake/{report_id}/photos/{photo_id}"): ("case-edit", ""),
    ("DELETE", "/intake/{report_id}/photos/{photo_id}"): ("case-edit", ""),
    ("PUT", "/intake/{report_id}/data"): ("case-edit", ""),
    ("POST", "/intake/{report_id}/finalize"): ("case-edit", "the owner stays whoever started the draft"),

    ("DELETE", "/reports/{report_id}"): ("special", "case-view; a draft: owner or admin; a filed case: admin only"),
    ("POST", "/reports/{report_id}/reopen"): ("special", "case-view; the signer or an admin"),
    ("POST", "/reports/{report_id}/sign-off"): ("special", "case-edit; admin or may_sign_off (B2)"),
    ("POST", "/intake/{report_id}/process"): ("special", "case-edit; then AI allowed (409 ai_key_missing, B2)"),
    ("POST", "/reports/{report_id}/annex-suggestions/ai"): ("special", "case-edit; then AI allowed (409 ai_key_missing, B2)"),
}
# Not in the OpenAPI document, so listed by hand.
HAND_LISTED = {
    ("GET", "/files/{path}"): ("case-view", "checked inside the static files handler before any thumbnail is made"),
    ("HEAD", "/files/{path}"): ("case-view", ""),
    ("POST", "/whatsapp/webhook"): ("special", "only mounted with Twilio credentials; must go through chat links before use"),
}
RULES = {"public", "signed-in", "self", "admin", "case-view", "case-edit", "special"}
# The only things a temporary-password user may do (agent5 section 1).
TEMP_PASSWORD_ALLOWED = {("GET", "/auth/me"), ("POST", "/auth/password")}

# ---------------------------------------------------------------- harness
BASE = "https://carlink.test"
PASSWORD = "purple tiger lamp bicycle"
TICKS = ("sees_all_cases", "ai_company_key_allowed", "may_sign_off")
AI_OFF_DETAIL = "AI is off for your account. Ask an admin to allow the company AI key."
BANNER_WORDS = ("unverified user", "not issued by")

PASSED = {"B1": 0, "B2": 0}
FAILURES: list[tuple[str, str, str]] = []
_section = {"tag": "B1", "name": "setup"}


class Stop(Exception):
    """A precondition failed; the rest of the section can't run."""


def check(cond, label: str, why: str = "") -> bool:
    tag = _section["tag"]
    if cond:
        PASSED[tag] += 1
        print(f"    ok   {label}")
        return True
    FAILURES.append((tag, _section["name"], label + (f"  [{why}]" if why else "")))
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
    except Exception as exc:
        msg = f"section stopped by {type(exc).__name__}: {exc} (test line {_test_frame(exc)})"
        FAILURES.append((tag, name, msg))
        print(f"    FAIL {msg}")


def browser() -> TestClient:
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
    return {"X-Real-IP": f"10.99.{(n // 256) % 256}.{n % 256}"}


def person(label, client, user_id, username, name):
    return SimpleNamespace(label=label, c=client, id=user_id, username=username, name=name)


def signup(username: str, full_name: str, **extra):
    c = browser()
    body = {"username": username, "email": f"{username}@example.com", "password": PASSWORD, **extra}
    r = c.post("/auth/signup", json=body, headers=fresh_ip())
    need(r.status_code == 200 and jd(r).get("id"), f"{full_name} signs up", brief(r))
    me = jd(r)
    p = person(full_name.split()[0], c, me["id"], me.get("username") or username, me.get("name") or username)
    if c.put("/auth/me", json={"name": full_name}).status_code == 200:
        p.name = full_name
    return p


def force_ticks(user_id: str, values: dict) -> None:
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
    if not check(applied, f"admin sets {p.label}'s ticks {body}", brief(r)):
        force_ticks(p.id, body)
        print("           (set directly in the database so the remaining checks still test something)")


def admin_creates(admin, username: str, full_name: str, change_password: bool = True, **ticks):
    r = admin.c.post("/users", json={"email": f"{username}@example.com", "name": full_name, "username": username, "role": "user"})
    need(r.status_code == 200, f"admin adds {full_name}", brief(r))
    user_id, temp = jd(r)["user"]["id"], jd(r)["temporary_password"]
    c = browser()
    r = c.post("/auth/login", json={"email": f"{username}@example.com", "password": temp})
    need(r.status_code == 200, f"{full_name} signs in with the temporary password", brief(r))
    if change_password:
        r = c.post("/auth/password", json={"current_password": temp, "new_password": PASSWORD})
        need(r.status_code == 200, f"{full_name} changes the temporary password", brief(r))
    p = person(full_name.split()[0], c, user_id, username, full_name)
    if change_password:
        set_ticks(admin, p, **ticks)
    return p


_seed = iter(range(1, 1_000_000))


def jpeg() -> bytes:
    from PIL import Image

    n = next(_seed)
    buf = BytesIO()
    Image.new("RGB", (64, 48), ((n * 53) % 256, (n * 97) % 256, (n * 31) % 256)).save(buf, "JPEG")
    return buf.getvalue()


def case_data(desc: str = "Rear-ended", plate: str = "SKA1111A", **extra) -> dict:
    data = {"description": desc, "category": ["Vehicle Collision or Damage"],
            "vehicle_info": {"plate_number": plate, "make": "Honda", "model": "Vezel"},
            "damage_summary": [{"part": "Rear Bumper", "severity": "Moderate"}]}
    data.update(extra)
    return data


def new_case(p, **extra) -> str:
    r = p.c.post("/reports", json={"draft": case_data(**extra)})
    need(r.status_code == 200 and jd(r).get("id"), f"{p.label} files a case", brief(r))
    return jd(r)["id"]


def new_draft(p, photos: int = 0) -> str:
    r = p.c.post("/intake")
    need(r.status_code == 200 and jd(r).get("id"), f"{p.label} starts a draft", brief(r))
    draft_id = jd(r)["id"]
    for i in range(photos):
        r = p.c.post(f"/intake/{draft_id}/photos", files={"file": (f"IMG_{i:04d}.jpg", jpeg(), "image/jpeg")},
                     data={"position": str(i)})
        need(r.status_code == 200, f"{p.label} uploads a photo", brief(r))
    return draft_id


def fill(path: str, **values) -> str:
    """A concrete URL for an OpenAPI path, whatever its parameters are called."""
    defaults = {"item_index": "0", "service": "llm", "case_ref": "CL 1"}

    def repl(m):
        name = m.group(1)
        for key, value in values.items():
            if key == name or (key == "report_id" and name in ("id", "case_id")):
                return value
        return defaults.get(name, "x0000000000")

    return re.sub(r"\{([^}]+)\}", repl, path)


def norm(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}


def openapi_ops() -> set:
    paths = app.openapi().get("paths") or {}
    return {(m.upper(), p) for p, item in paths.items() for m in item if m in HTTP_METHODS}


def decided_ops(rule: str | None = None) -> list:
    """The OpenAPI operations, each with its recorded rule (None if missing)."""
    by_norm = {(m, norm(p)): rule_note[0] for (m, p), rule_note in DECISIONS.items()}
    out = []
    for m, p in sorted(openapi_ops()):
        r = by_norm.get((m, norm(p)))
        if rule is None or r == rule:
            out.append((m, p, r))
    return out


W = SimpleNamespace()


# ================================================================= setup
def setup() -> None:
    admin_client = browser()
    admin_id = sign_in(admin_client, role="admin", name="Ada Admin", username="adaboss")
    W.admin = person("admin", admin_client, admin_id, "adaboss", "Ada Admin")
    W.A = signup("alice.p", "Alice Plain")
    W.B = signup("bob.p", "Bob Plain")
    W.S = admin_creates(W.admin, "sam.p", "Sam Seesall", sees_all_cases=True)
    W.M = admin_creates(W.admin, "mia.p", "Mia Signer", may_sign_off=True)
    W.V = admin_creates(W.admin, "vic.p", "Vic Aiuser", ai_company_key_allowed=True)
    W.T = admin_creates(W.admin, "tess.p", "Tess Temporary", change_password=False)
    # A owns 1 draft, 2 open cases and 1 signed-off case (signed by the admin).
    W.A_D1 = new_draft(W.A, photos=1)
    W.A_F1 = new_case(W.A, plate="SAP1111A")
    W.A_F2 = new_case(W.A, plate="SAP2222A")
    W.A_F3 = new_case(W.A, plate="SAP3333A")
    r = W.admin.c.post(f"/reports/{W.A_F3}/sign-off", json={"terms_accepted": True})
    need(r.status_code == 200, "the admin signs off one of A's cases", brief(r))


# ================================================================= B1: route table
def route_table() -> None:
    ops = openapi_ops()
    need(ops, "app.openapi() lists the operations (works with /docs off)", f"{len(ops)} operations")
    have = {(m, norm(p)) for m, p in ops}
    recorded = {(m, norm(p)): (m, p) for m, p in DECISIONS}
    missing = sorted(f"{m} {p}" for m, p in ops if (m, norm(p)) not in recorded)
    stale = sorted(f"{m} {p}" for (m, n), (_, p) in recorded.items() if (m, n) not in have)
    check(not missing, "every API operation has a recorded decision", "no decision for: " + "; ".join(missing))
    check(not stale, "every recorded decision is a real operation", "not in app.openapi(): " + "; ".join(stale))
    bad = sorted(f"{m} {p}: {r}" for (m, p), (r, _) in {**DECISIONS, **HAND_LISTED}.items() if r not in RULES)
    check(not bad, "every decision uses one of the seven rules", "; ".join(bad))
    check(("HEAD", "/reports/{report_id}/download") in {(m, p) for m, p in ops} or
          any(m == "HEAD" and norm(p) == "/reports/{}/download" for m, p in ops),
          "HEAD on the PDF download is in the table (it needs the gate too)")
    check(any(norm(p) == "/reports/{}/owner" and m == "PATCH" for m, p in ops), "PATCH /reports/{id}/owner exists")


# ================================================================= B1: status codes
def signed_out() -> None:
    anon = browser()
    for method, path, rule in decided_ops():
        r = anon.request(method, fill(path))
        if rule == "public":
            check(r.status_code != 401, f"{method} {path} is public (not 401)", brief(r))
        else:
            check(r.status_code == 401, f"signed out: {method} {path} -> 401", brief(r))
    for method in ("GET", "HEAD"):
        r = anon.request(method, "/files/incidents/2026/10/x0000000000/photos/photo_00.jpg")
        check(r.status_code == 401, f"signed out: {method} /files/... -> 401", str(r.status_code))


def temp_password() -> None:
    T = W.T
    for method, path, rule in decided_ops():
        if rule == "public":
            continue
        r = T.c.request(method, fill(path, report_id=W.A_F1))
        if (method, path) in TEMP_PASSWORD_ALLOWED:
            check(r.status_code != 403, f"temporary password: {method} {path} still works", brief(r))
        elif method == "HEAD":
            check(r.status_code == 403, f"temporary password: {method} {path} -> 403", str(r.status_code))
        else:
            check(r.status_code == 403 and code_of(r) == "password_change_required",
                  f"temporary password: {method} {path} -> 403 password_change_required", brief(r))
    r = T.c.get("/files/incidents/2026/10/x0000000000/photos/photo_00.jpg")
    check(r.status_code == 403 and code_of(r) == "password_change_required", "temporary password: /files -> 403", brief(r))


# A valid body for admin-only operations that need one, so the check is "a
# real request from a plain user is refused", not "a malformed one is".
ADMIN_BODIES = {
    ("PATCH", "/reports/{}/owner"): lambda: {"json": {"owner_id": None}},
    ("POST", "/users"): lambda: {"json": {"email": "nope@example.com", "name": "Nope Person", "role": "admin"}},
    ("PATCH", "/users/{}"): lambda: {"json": {"sees_all_cases": True}},
    ("PUT", "/signup-settings"): lambda: {"json": {"open": False}},
    ("POST", "/account-links"): lambda: {"json": {"email": "nope@example.com", "role": "admin"}},
    ("PUT", "/settings"): lambda: {"json": {"company_name": "Not Carlink"}},
    ("POST", "/setup/llm-keys"): lambda: {"json": {"apiKey": "AIza-not-a-key"}},
}


def admin_only() -> None:
    A = W.A
    ops = decided_ops("admin")
    need(ops, "the table has admin-only operations", str(len(ops)))
    for method, path, _ in ops:
        kw = ADMIN_BODIES.get((method, norm(path)), dict)()
        r = A.c.request(method, fill(path, report_id=W.A_F1, user_id=W.B.id), **kw)
        check(r.status_code == 403, f"plain user: {method} {path} -> 403", brief(r))
    for path in ("/users", "/account-links", "/system/info", "/setup/llm-keys"):
        r = W.admin.c.get(path)
        check(r.status_code == 200, f"the admin: GET {path} -> 200", brief(r))


def plain_user_allowed() -> None:
    A = W.A
    for path in ("/reports", "/analytics/summary", "/taxonomy", "/settings", "/parts-history", "/auth/me"):
        r = A.c.get(path)
        check(r.status_code == 200, f"plain user: GET {path} -> 200 (signed-in / self)", brief(r))
    r = A.c.put("/auth/me", json={"qualifications": "MIMI"})
    check(r.status_code == 200, "plain user: PUT /auth/me -> 200 (self)", brief(r))
    for method, path in (("GET", f"/reports/{W.A_F1}"), ("GET", f"/reports/{W.A_F1}/download"), ("HEAD", f"/reports/{W.A_F1}/download"),
                         ("GET", f"/reports/{W.A_F1}/annex-suggestions"), ("GET", f"/intake/{W.A_D1}")):
        r = A.c.request(method, path)
        check(r.status_code == 200, f"plain user on own case: {method} {path.replace(W.A_F1, '{id}').replace(W.A_D1, '{id}')} -> 200 (case-view)", brief(r))
    r = A.c.put(f"/reports/{W.A_F1}", json={"draft": {"category": ["Other"], "description": "own edit"}})
    check(r.status_code == 200, "plain user on own case: PUT /reports/{id} -> 200 (case-edit)", brief(r))
    r = A.c.put(f"/intake/{W.A_D1}/data", json={"data": {"description": "", "category": ["Other"]}})
    check(r.status_code == 200, "plain user on own draft: PUT /intake/{id}/data -> 200 (case-edit)", brief(r))
    for method, path in (("GET", f"/reports/{W.A_F1}"), ("PUT", f"/reports/{W.A_F1}"), ("GET", f"/intake/{W.A_D1}")):
        kw = {"json": {"draft": {"category": ["Other"], "description": "B"}}} if method == "PUT" else {}
        r = W.B.c.request(method, path, **kw)
        check(r.status_code == 404, f"another plain user: {method} on A's case -> 404 (case-view/case-edit)", brief(r))


# ================================================================= B1: users admin
def _users_by_id() -> dict:
    r = W.admin.c.get("/users")
    rows = js(r) if isinstance(js(r), list) else []
    return {u.get("id"): u for u in rows if isinstance(u, dict)}


def users_list() -> None:
    users = _users_by_id()
    need(W.A.id in users, "GET /users lists A", str(list(users)[:3]))
    for key, p in (("A", W.A), ("S", W.S), ("M", W.M), ("V", W.V), ("admin", W.admin)):
        row = users.get(p.id) or {}
        check(all(k in row for k in TICKS), f"GET /users: {key}'s row has {', '.join(TICKS)}", str({k: row.get(k, "missing") for k in TICKS}))
    want = {"A": set(), "S": {"sees_all_cases"}, "M": {"may_sign_off"}, "V": {"ai_company_key_allowed"}}
    for key, on in want.items():
        row = users.get(getattr(W, key).id) or {}
        got = {k for k in TICKS if row.get(k)}
        check(got == on, f"GET /users: {key}'s ticks on = {sorted(on) or 'none'}", str({k: row.get(k) for k in TICKS}))
    row = users.get(W.admin.id) or {}
    check(not any(row.get(k) for k in TICKS), "GET /users shows raw ticks: the admin's own are off (admins don't need them)",
          str({k: row.get(k) for k in TICKS}))
    for key, p, counts in (("A", W.A, {"open": 2, "signed_off": 1, "drafts": 1}), ("B", W.B, {"open": 0, "signed_off": 0, "drafts": 0})):
        got = (users.get(p.id) or {}).get("cases")
        check(isinstance(got, dict) and all(got.get(k) == v for k, v in counts.items()),
              f"GET /users: {key}'s cases = {counts}", repr(got))

    print("  counted in one batch, not one query per user")
    selects = {"n": 0}

    def count(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects["n"] += 1

    event.listen(engine, "before_cursor_execute", count)
    try:
        W.admin.c.get("/users")
        selects["n"] = 0
        W.admin.c.get("/users")
        before = selects["n"]
        for i in range(5):
            r = W.admin.c.post("/users", json={"email": f"extra{i}@example.com", "name": f"Extra Person{i}", "role": "user"})
            check(r.status_code == 200, f"admin adds extra account {i + 1}", brief(r))
        selects["n"] = 0
        W.admin.c.get("/users")
        after = selects["n"]
    finally:
        event.remove(engine, "before_cursor_execute", count)
    check(after - before < 5, "5 more accounts don't add 5 more queries to GET /users", f"{before} -> {after} SELECTs")


def _audit_count() -> int:
    db = SessionLocal()
    try:
        return db.query(AuditEntry).count()
    finally:
        db.close()


def _latest_audit_details(n: int) -> list[str]:
    db = SessionLocal()
    try:
        rows = db.query(AuditEntry).order_by(AuditEntry.id.desc()).limit(n).all()
        return [f"{e.action}: {e.detail}" for e in rows]
    finally:
        db.close()


TICK_WORDS = {
    "sees_all_cases": ("sees_all_cases", "all cases"),
    "ai_company_key_allowed": ("ai_company_key_allowed", "company ai key", "company key"),
    "may_sign_off": ("may_sign_off", "sign off", "sign-off"),
}
ME_FIELD = {"sees_all_cases": "can_view_all_cases", "ai_company_key_allowed": "ai_allowed", "may_sign_off": "may_sign_off"}


def users_patch() -> None:
    admin, V = W.admin, W.V
    for tick in TICKS:
        original = bool((_users_by_id().get(V.id) or {}).get(tick))
        others = {k: bool((_users_by_id().get(V.id) or {}).get(k)) for k in TICKS if k != tick}
        for value in (not original, original):
            before = _audit_count()
            r = admin.c.patch(f"/users/{V.id}", json={tick: value})
            row = _users_by_id().get(V.id) or {}
            check(r.status_code == 200 and bool(row.get(tick)) == value, f"PATCH /users/{{id}} {{{tick}: {value}}} is saved", brief(r))
            check(all(bool(row.get(k)) == v for k, v in others.items()), "...and leaves the other two ticks alone",
                  str({k: row.get(k) for k in TICKS}))
            me = jd(V.c.get("/auth/me"))
            check(me.get(ME_FIELD[tick]) is value, f"...and V's /auth/me {ME_FIELD[tick]} is {value} at once", repr(me.get(ME_FIELD[tick])))
            details = _latest_audit_details(max(_audit_count() - before, 0))
            check(any(any(w in d.lower() for w in TICK_WORDS[tick]) for d in details),
                  "...and the change is in the audit log", str(details) or "no new audit entry")

    print("  only an admin sets ticks")
    for who, p, target in (("plain user A", W.A, W.B), ("plain user A (own account)", W.A, W.A), ("staff S", W.S, W.A)):
        r = p.c.patch(f"/users/{target.id}", json={"sees_all_cases": True, "may_sign_off": True, "ai_company_key_allowed": True})
        check(r.status_code == 403, f"{who} can't PATCH /users/{{id}} (403)", brief(r))
    r = W.A.c.put("/auth/me", json={"sees_all_cases": True, "may_sign_off": True, "ai_company_key_allowed": True, "role": "admin"})
    row = _users_by_id().get(W.A.id) or {}
    me = jd(W.A.c.get("/auth/me"))
    check(not any(row.get(k) for k in TICKS) and not me.get("can_view_all_cases") and not me.get("may_sign_off") and not me.get("ai_allowed"),
          "ticks sent to PUT /auth/me are ignored", f"{brief(r)} | users row {({k: row.get(k) for k in TICKS})}")
    p = signup("sly.p", "Sly Signup", sees_all_cases=True, may_sign_off=True, ai_company_key_allowed=True, role="admin")
    me = jd(p.c.get("/auth/me"))
    check(me.get("role") == "user" and not me.get("can_view_all_cases") and not me.get("may_sign_off") and not me.get("ai_allowed"),
          "ticks sent with a signup are ignored: a new signup starts with all three off", repr(me))


def auth_me() -> None:
    want = {
        "admin": (W.admin, True, True, True),
        "A (self-signup)": (W.A, False, False, False),
        "S (sees all cases)": (W.S, True, False, False),
        "M (may sign off)": (W.M, False, True, False),
        "V (company AI key)": (W.V, False, False, True),
    }
    for who, (p, view_all, sign, ai) in want.items():
        r = p.c.get("/auth/me")
        me = jd(r)
        got = (me.get("can_view_all_cases"), me.get("may_sign_off"), me.get("ai_allowed"))
        check(r.status_code == 200 and got == (view_all, sign, ai),
              f"GET /auth/me for {who}: can_view_all_cases={view_all}, may_sign_off={sign}, ai_allowed={ai}", str(got))


# ================================================================= B2: abuse limits
def b2_ai_allowed() -> None:
    A, B, S, V, admin = W.A, W.B, W.S, W.V, W.admin
    a_draft = new_draft(A, photos=1)
    tmp = STORAGE / "tmp"
    tmp.mkdir(exist_ok=True)
    before_calls, before_tmp = len(GEMINI_CALLS), len(list(tmp.iterdir()))
    r = A.c.post(f"/intake/{a_draft}/process", json={"mode": "all"})
    check(r.status_code == 409 and code_of(r) == "ai_key_missing" and jd(r).get("detail") == AI_OFF_DETAIL,
          "A (no AI tick) POST /intake/{id}/process -> 409 ai_key_missing, with the plain-English detail", brief(r))
    check(jd(A.c.get(f"/intake/{a_draft}")).get("stage") == "uploading", "...and the draft's stage is unchanged",
          repr(jd(A.c.get(f"/intake/{a_draft}")).get("stage")))
    r = A.c.post(f"/reports/{W.A_F1}/annex-suggestions/ai", json={"parts": ["Rear Bumper"]})
    check(r.status_code == 409 and code_of(r) == "ai_key_missing", "A POST /reports/{id}/annex-suggestions/ai -> 409 ai_key_missing", brief(r))
    r = A.c.post("/reports/analyze-photos", data={"description": "x"}, files=[("photos", ("IMG_1.jpg", jpeg(), "image/jpeg"))])
    check(r.status_code == 409 and code_of(r) == "ai_key_missing", "A POST /reports/analyze-photos -> 409 ai_key_missing", brief(r))
    check(len(list(tmp.iterdir())) == before_tmp, "...refused before any file is saved", f"{before_tmp} -> {len(list(tmp.iterdir()))} temp files")
    time.sleep(0.5)
    check(len(GEMINI_CALLS) == before_calls, "no Gemini call was made for A", f"{len(GEMINI_CALLS) - before_calls} calls")

    print("  the order: can see (404), can work on it (403), then AI (409)")
    r = B.c.post(f"/intake/{a_draft}/process", json={"mode": "all"})
    check(r.status_code == 404, "B (no AI) on A's draft gets 404, not 409", brief(r))
    r = S.c.post(f"/intake/{a_draft}/process", json={"mode": "all"})
    check(r.status_code == 403 and code_of(r) == "not_allowed", "S (sees all, no AI) on A's draft gets 403 not_allowed", brief(r))
    r = S.c.post(f"/reports/{W.A_F1}/annex-suggestions/ai", json={"parts": ["Rear Bumper"]})
    check(r.status_code == 409 and code_of(r) == "ai_key_missing", "S on A's filed case (may work on it) gets 409 ai_key_missing", brief(r))

    print("  allowed people run AI")
    v_draft, v_case = new_draft(V, photos=1), new_case(V, plate="SVA1111A")
    r = V.c.post(f"/intake/{v_draft}/process", json={"mode": "all"})
    check(r.status_code == 200, "V (company AI key tick) starts AI on V's draft", brief(r))
    r = V.c.post(f"/reports/{v_case}/annex-suggestions/ai", json={"parts": ["Rear Bumper"]})
    check(r.status_code == 200, "V asks the AI for Annex A lines", brief(r))
    r = V.c.post("/reports/analyze-photos", data={"description": "x"}, files=[("photos", ("IMG_1.jpg", jpeg(), "image/jpeg"))])
    check(r.status_code == 200, "V analyzes photos", brief(r))
    for p in jd(r).get("temp_photo_paths") or []:
        Path(p).unlink(missing_ok=True)
    r = admin.c.post(f"/reports/{W.A_F1}/annex-suggestions/ai", json={"parts": ["Rear Bumper"]})
    check(r.status_code == 200, "the admin asks the AI on A's case", brief(r))


def b2_sign_off() -> None:
    A, S, M, admin = W.A, W.S, W.M, W.admin
    # One case per attempt, so a wrong success can't turn the next check into a 409.
    a_case1, a_case2, a_case3 = (new_case(A, plate=f"SSO111{n}A") for n in (1, 2, 3))
    m_case = new_case(M, plate="SSO2222A")

    def sign_perm(p, case_id):
        return (jd(p.c.get(f"/reports/{case_id}")).get("permissions") or {}).get("sign_off")

    check(sign_perm(A, a_case1) is False, "permissions.sign_off is false for A on A's own case", repr(sign_perm(A, a_case1)))
    check(sign_perm(S, a_case1) is False, "...and for S (sees all, no sign-off tick)", repr(sign_perm(S, a_case1)))
    check(sign_perm(M, m_case) is True, "...true for M (may sign off) on M's case", repr(sign_perm(M, m_case)))
    check(sign_perm(admin, a_case1) is True, "...true for the admin", repr(sign_perm(admin, a_case1)))
    r = A.c.post(f"/reports/{a_case1}/sign-off", json={"terms_accepted": True})
    check(r.status_code == 403, "A (self-signup) can't sign off A's own case (403)", brief(r))
    r = S.c.post(f"/reports/{a_case2}/sign-off", json={"terms_accepted": True})
    check(r.status_code == 403, "S (sees all, no sign-off tick) can't sign off A's case (403)", brief(r))
    r = M.c.post(f"/reports/{a_case2}/sign-off", json={"terms_accepted": True})
    check(r.status_code == 404, "M can't sign off a case M can't see (404)", brief(r))
    r = M.c.post(f"/reports/{m_case}/sign-off", json={"terms_accepted": True})
    check(r.status_code == 200, "M (may sign off) signs off M's own case", brief(r))
    r = admin.c.post(f"/reports/{a_case3}/sign-off", json={"terms_accepted": True})
    check(r.status_code == 200, "the admin signs off A's case", brief(r))


def b2_our_ref() -> None:
    A, M, admin = W.A, W.M, W.admin

    def our_ref(p, case_id, kind="reports"):
        return (jd(p.c.get(f"/{kind}/{case_id}")).get("data") or {}).get("report_id")

    a_case, m_case = new_case(A, plate="SOR1111A"), new_case(M, plate="SOR2222A")
    r = A.c.put(f"/reports/{a_case}", json={"draft": {"category": ["Other"], "description": "x", "report_id": "CL 77701"}})
    check(our_ref(A, a_case) != "CL 77701", "A (not staff) can't set Our Ref with PUT /reports/{id}", f"{brief(r)} -> {our_ref(A, a_case)!r}")
    r = A.c.post("/reports", json={"draft": case_data(plate="SOR3333A", report_id="CL 77702")})
    made = jd(r).get("id")
    check(r.status_code != 200 or our_ref(A, made) != "CL 77702", "...nor with POST /reports",
          f"{brief(r)} -> {our_ref(A, made) if made else None!r}")
    draft = new_draft(A)
    r = A.c.put(f"/intake/{draft}/data", json={"data": {"description": "", "category": ["Other"], "report_id": "CL 77703"}})
    check(our_ref(A, draft, "intake") != "CL 77703", "...nor in a draft's autosave", f"{brief(r)} -> {our_ref(A, draft, 'intake')!r}")
    r = A.c.post(f"/intake/{draft}/finalize", json={"data": case_data(plate="SOR4444A", report_id="CL 77704")})
    check(r.status_code != 200 or our_ref(A, draft) != "CL 77704", "...nor when filing the draft",
          f"{brief(r)} -> {our_ref(A, draft)!r}")
    r = M.c.put(f"/reports/{m_case}", json={"draft": {"category": ["Other"], "description": "x", "report_id": "CL 77705"}})
    check(r.status_code == 200 and our_ref(M, m_case) == "CL 77705", "M (may sign off = staff) sets Our Ref", brief(r))
    r = admin.c.put(f"/reports/{a_case}", json={"draft": {"category": ["Other"], "description": "x", "report_id": "CL 77706"}})
    check(r.status_code == 200 and our_ref(A, a_case) == "CL 77706", "the admin sets Our Ref on A's case", brief(r))
    r = A.c.put(f"/reports/{a_case}", json={"draft": {"category": ["Other"], "description": "A edits", "report_id": "CL 77706"}})
    data = jd(A.c.get(f"/reports/{a_case}")).get("data") or {}
    check(r.status_code == 200 and data.get("description") == "A edits" and data.get("report_id") == "CL 77706",
          "A can still save the form when it carries the unchanged Our Ref", brief(r))
    r = A.c.put(f"/reports/{a_case}", json={"draft": {"category": ["Other"], "description": "A edits", "report_id": "CL 99999"}})
    check(our_ref(A, a_case) == "CL 77706", "A can't change the Our Ref the admin set", f"{brief(r)} -> {our_ref(A, a_case)!r}")


def b2_banner() -> None:
    A, S, M, admin = W.A, W.S, W.M, W.admin

    def html_for(p, case_id) -> tuple[int, str]:
        n = len(TEMPLATE_HTML)
        r = p.c.post(f"/reports/{case_id}/render-pdf")
        return r.status_code, "\n".join(TEMPLATE_HTML[n:])

    def has_banner(html: str) -> bool:
        low = html.lower()
        return all(w in low for w in BANNER_WORDS)

    for who, p, staff in (("A (self-signup)", A, False), ("S (sees all, no sign-off tick)", S, False),
                          ("M (may sign off)", M, True), ("the admin", admin, True)):
        case_id = new_case(p, plate="SBN1111A")
        status, html = html_for(p, case_id)
        if not check(status == 200 and html, f"{who}'s case renders", f"{status}, {len(html)} chars of HTML"):
            continue
        if staff:
            check(not has_banner(html), f"no 'unverified user' banner on {who}'s case PDF")
        else:
            check(has_banner(html), f"{who}'s case PDF carries 'Prepared by an unverified user, not issued by ...'",
                  "banner text not in the PDF HTML")


def b2_knowledge() -> None:
    plate, source = "SGX4321Q", "past-report-cl11900.xlsx"
    db = SessionLocal()
    try:
        for i, (item, cost) in enumerate((("Tail gate", 1160.0), ("Tail lamp LH", 521.6)), start=1):
            db.add(PastAnnexLine(case_ref="CL 11900", source_file=source, plate=plate, make="Honda", model="Vezel",
                                 make_model_raw="Honda Vezel", surveyed="2018-01-27", line_no=i, section="list", item=item,
                                 item_key=item.lower(), family=None, condition="Dented", qty=1.0, workshop_est=cost * 1.25,
                                 discount=0.2, adjusted_cost=cost))
        db.commit()
    finally:
        db.close()
    for who, p, sees_prices, sees_plates in (("the admin", W.admin, True, True), ("S (sees all cases)", W.S, True, False),
                                             ("M (may sign off only)", W.M, False, False), ("A (self-signup)", W.A, False, False)):
        r = p.c.get("/parts-history")
        body, text = jd(r), r.text or ""
        need(r.status_code == 200, f"{who} opens Knowledge", brief(r))
        check((plate in text and source in text) == sees_plates,
              f"{who}: plates and file names {'shown' if sees_plates else 'hidden'}", f"plate shown={plate in text}, file shown={source in text}")
        lines = body.get("lines") if "lines" in body else body.get("items")
        if sees_prices:
            check(isinstance(lines, list) and any(isinstance(x, dict) and x.get("usual_unit_price") for x in lines) and not body.get("hidden"),
                  f"{who}: prices shown", f"hidden={body.get('hidden')!r}, {len(lines or [])} rows")
        else:
            check(body.get("hidden") is True and lines == [] and not body.get("items") and "1160" not in text,
                  f"{who}: prices hidden -> empty list and hidden: true", f"hidden={body.get('hidden')!r}, rows={lines!r}"[:200])


def b2_docs_off() -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = W.admin.c.get(path)
        check(r.status_code == 404, f"GET {path} -> 404 (API docs off), even for an admin", brief(r))
    probe = "import sys; sys.path.insert(0, sys.argv[1]); from app.api.main import app; print(app.docs_url, app.redoc_url, app.openapi_url)"
    for value, want in (("true", "/docs /redoc /openapi.json"), (None, "None None None")):
        env = os.environ.copy()
        env.pop("ENABLE_API_DOCS", None)
        if value:
            env["ENABLE_API_DOCS"] = value
        r = subprocess.run([sys.executable, "-c", probe, str(ROOT)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        got = (r.stdout or "").strip().splitlines()[-1:] or [r.stderr[-200:]]
        check(got[0] == want, f"ENABLE_API_DOCS={value or 'unset'}: docs {'on' if value else 'off'} ({want})", got[0])


def b2_clear_site_data() -> None:
    p = signup("leah.p", "Leah Leaving")
    r = p.c.post("/auth/logout")
    header = r.headers.get("clear-site-data", "")
    check(r.status_code == 200 and '"cache"' in header and '"storage"' in header,
          'POST /auth/logout sends Clear-Site-Data: "cache", "storage"', f"{r.status_code} header={header!r}")


def b2_render_limits() -> None:
    A = W.A
    case_id = new_case(A, plate="SRL1111A")
    codes = []
    for _ in range(25):
        r = A.c.post(f"/reports/{case_id}/render-pdf")
        codes.append((r.status_code, code_of(r)))
    check((429, "render_busy") in codes, "25 render-pdf calls in a row from one person hit 429 render_busy",
          str(sorted(set(codes), key=str)))

    print("  at most 2 renders at once")
    people = [W.admin, W.S, W.M, W.V, W.B, signup("rita.p", "Rita Render")]
    cases = [(p, new_case(p, plate="SRL2222A")) for p in people]
    results = []
    RENDER.update(delay=0.6, max_active=0)
    try:
        threads = [threading.Thread(target=lambda p=p, c=c: results.append(p.c.post(f"/reports/{c}/render-pdf").status_code))
                   for p, c in cases]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
    finally:
        RENDER["delay"] = 0.0
    check(RENDER["max_active"] <= 2, "6 people rendering at the same moment: no more than 2 Chromium renders run at once",
          f"{RENDER['max_active']} ran at once")
    check(all(s in (200, 429) for s in results) and len(results) == 6, "...each one renders or gets 429, none fails",
          str(sorted(results)))


def b2_disk_guard() -> None:
    A, V = W.A, W.V
    real = shutil.disk_usage
    usage = namedtuple("usage", "total used free")

    def low_disk(path):
        return usage(100 * 2**30, 99 * 2**30, 1 * 2**30)

    patched = [shutil]
    shutil.disk_usage = low_disk
    for name, mod in list(sys.modules.items()):
        if name.startswith("app.") and getattr(mod, "disk_usage", None) is real:
            mod.disk_usage = low_disk
            patched.append(mod)
    try:
        r = A.c.post("/intake")
        check(r.status_code == 507 and code_of(r) == "disk_full", "1 GB free: POST /intake -> 507 disk_full", brief(r))
        made = jd(r).get("id") if r.status_code == 200 else None
        r = A.c.post(f"/intake/{W.A_D1}/photos", files={"file": ("IMG_9.jpg", jpeg(), "image/jpeg")}, data={"position": "9"})
        check(r.status_code == 507 and code_of(r) == "disk_full", "1 GB free: POST /intake/{id}/photos -> 507 disk_full", brief(r))
        r = V.c.post("/reports/analyze-photos", data={"description": "x"}, files=[("photos", ("IMG_1.jpg", jpeg(), "image/jpeg"))])
        check(r.status_code == 507 and code_of(r) == "disk_full", "1 GB free: POST /reports/analyze-photos -> 507 disk_full", brief(r))
        r = A.c.post("/reports", json={"draft": case_data(plate="SDF1111A")})
        check(r.status_code == 507 and code_of(r) == "disk_full", "1 GB free: POST /reports -> 507 disk_full", brief(r))
    finally:
        for mod in patched:
            mod.disk_usage = real
    if made:
        A.c.delete(f"/reports/{made}")
    r = A.c.post("/intake")
    check(r.status_code == 200, "with room on the disk again, a draft can be started", brief(r))
    if r.status_code == 200:
        A.c.delete(f"/reports/{jd(r)['id']}")


def _drafts_left() -> int:
    db = SessionLocal()
    try:
        return db.query(IntakeSession).count()
    finally:
        db.close()


def _delete_all_drafts() -> None:
    time.sleep(1.0)  # let any background AI run on a draft finish
    rows = js(W.admin.c.get("/reports?scope=all")) or js(W.admin.c.get("/reports")) or []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("status") == "intake":
            W.admin.c.delete(f"/reports/{row['id']}")


def b2_draft_limits() -> None:
    admin, S = W.admin, W.S
    _delete_all_drafts()
    need(_drafts_left() == 0, "start with no open drafts", f"{_drafts_left()} left")
    try:
        for p in (admin, admin, S, S):
            r = p.c.post("/intake")
            check(r.status_code == 200, f"{p.label} starts a draft (staff drafts don't count toward the shared 30)", brief(r))
        plain = [signup(f"plain{i}.p", f"Plain Person{i}") for i in range(1, 8)]
        first = plain[0]
        codes = [first.c.post("/intake").status_code for _ in range(5)]
        check(codes == [200] * 5, "a plain user starts 5 drafts", str(codes))
        r = first.c.post("/intake")
        check(r.status_code == 409 and code_of(r) == "draft_limit", "...the 6th -> 409 draft_limit", brief(r))
        if r.status_code == 200:  # keep the shared count at 5 per person for the checks below
            W.admin.c.delete(f"/reports/{jd(r).get('id')}")
        for p in plain[1:6]:
            codes = [p.c.post("/intake").status_code for _ in range(5)]
            check(codes == [200] * 5, f"{p.label} starts 5 drafts (30 from plain users in all, plus 4 from staff)", str(codes))
        r = plain[6].c.post("/intake")
        check(r.status_code == 409, "a 7th plain user hits the shared 30 -> 409", brief(r))
        for p in (admin, S):
            r = p.c.post("/intake")
            check(r.status_code == 200, f"{p.label} still starts a draft with the shared 30 full (staff are exempt)", brief(r))
    finally:
        _delete_all_drafts()


# ================================================================= main
B1_SECTIONS = [
    ("route table: DECISIONS equals app.openapi()", route_table),
    ("signed out: 401 on every non-public operation", signed_out),
    ("temporary password: 403 password_change_required", temp_password),
    ("admin-only operations: 403 for a plain user", admin_only),
    ("signed-in, self and own-case operations work for a plain user", plain_user_allowed),
    ("GET /users: ticks and case counts", users_list),
    ("PATCH /users/{id}: the three ticks", users_patch),
    ("GET /auth/me: can_view_all_cases, may_sign_off, ai_allowed", auth_me),
]
B2_SECTIONS = [
    ("AI only for admins and the company-AI-key tick (409 ai_key_missing)", b2_ai_allowed),
    ("sign-off only for admins and the may-sign-off tick", b2_sign_off),
    ("Our Ref (report_id) set by staff only", b2_our_ref),
    ("unverified-owner banner in the PDF HTML", b2_banner),
    ("Knowledge: plates, file names and prices (hidden: true)", b2_knowledge),
    ("/docs, /redoc, /openapi.json off", b2_docs_off),
    ("Clear-Site-Data on logout", b2_clear_site_data),
    ("render limits: per person (429 render_busy) and 2 at once", b2_render_limits),
    ("disk guard: 507 disk_full under 3 GB free", b2_disk_guard),
    ("draft limits: 5 per person (409 draft_limit), staff exempt from the shared 30", b2_draft_limits),
]


def main() -> int:
    only = {a.lstrip("-").upper() for a in sys.argv[1:]} & {"B1", "B2"}
    print(f"temporary storage: {STORAGE}")
    with browser():
        print("\n[B1] setup: people and their cases")
        try:
            setup()
        except Stop as exc:
            print(f"\nSETUP FAILED: {exc}\nThe sections need these people, so they were not run.")
            return summary()
        except Exception as exc:
            FAILURES.append(("B1", "setup", f"setup stopped by {type(exc).__name__}: {exc} (test line {_test_frame(exc)})"))
            return summary()
        if not only or "B1" in only:
            for name, fn in B1_SECTIONS:
                run_section("B1", name, fn)
        if not only or "B2" in only:
            print("\n" + "-" * 78 + "\nB2 SECTIONS: abuse limits agent B2 adds after B1. Failures here mean B2 isn't done yet.\n" + "-" * 78)
            for name, fn in B2_SECTIONS:
                run_section("B2", name, fn)
    return summary()


def summary() -> int:
    print("\n" + "=" * 78)
    fails = {"B1": [f for f in FAILURES if f[0] == "B1"], "B2": [f for f in FAILURES if f[0] == "B2"]}
    print(f"test_permissions  B1 (backend core): {PASSED['B1']} passed, {len(fails['B1'])} failed")
    print(f"                  B2 (abuse limits, built after B1): {PASSED['B2']} passed, {len(fails['B2'])} failed"
          + ("   <- expected until B2 is done" if fails["B2"] else ""))
    for tag in ("B1", "B2"):
        by_section: dict = {}
        for _, section, label in fails[tag]:
            by_section.setdefault(section, []).append(label)
        for section, labels in by_section.items():
            print(f"\n  [{tag}] {section}: {len(labels)} failed" + ("  (B2 not done yet?)" if tag == "B2" else ""))
            for label in labels:
                print(f"    - {label}")
    if not FAILURES:
        print("\nALL PERMISSION CHECKS PASSED")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
