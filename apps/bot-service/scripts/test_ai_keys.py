"""Tests for release P3 "My AI key": people's own Gemini keys, sealed with
the secret in SECRETS_DIR (app/ai/secretbox.py, app/ai/keys.py,
app/api/ai_keys.py, the ai_key field of GET /users).

    python scripts/test_ai_keys.py

From plans/2026-10-26-roles-ai-telegram/p2_p3_build_brief.md ("P3 API
contract", "Storage"), agent5_integration_report.md section 6 and
agent6_security_report.md M3, L2, L3.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database, Google replaced by a fake that answers per key, and every outside
network connection refused. The test keys are made up; none is ever
printed.
"""
import hashlib
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
_tmp = Path(tempfile.mkdtemp(prefix="ai-keys-test-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
SECRETS = (_tmp / "secrets").resolve()
DB_FILE = (_tmp / "test.db").resolve()


def make_key(label: str) -> str:
    """A made-up key shaped like a Gemini one: AIza + 35 characters, the
    last 4 different for each label."""
    return ("AIzaSy" + label + "x" * 40)[:35] + hashlib.md5(label.encode()).hexdigest()[:4]


ENV_COMPANY_KEY = make_key("CompanyEnv")
# Forced, not setdefault: never a real folder or key from the environment.
os.environ["STORAGE_DIR"] = str(STORAGE)
os.environ["SECRETS_DIR"] = str(SECRETS)
os.environ["DATABASE_URL"] = f"sqlite:///{DB_FILE.as_posix()}"
os.environ["GEMINI_API_KEY"] = ENV_COMPANY_KEY
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""

# ------------------------------------------------------------ every log line
LOG_LINES: list[str] = []


class _Keep(logging.Handler):
    def emit(self, record):
        try:
            LOG_LINES.append(self.format(record))
        except Exception:
            LOG_LINES.append(str(record.msg))


_keep = _Keep(level=logging.DEBUG)
_keep.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
logging.getLogger().addHandler(_keep)
logging.getLogger().setLevel(logging.DEBUG)

# ---------------------------------------------------------------- offline fakes
_real_connect = socket.socket.connect


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConnectionRefusedError(f"test is offline: refused a connection to {address!r}")
    return _real_connect(sock, address)


socket.socket.connect = _offline_connect

import httpx  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402
from google.genai import errors as genai_errors  # noqa: E402

# How the fake Google answers models.list for a key: ok (default), refuse,
# quota, network, server. Every key it was asked about, in order.
GOOGLE: dict[str, str] = {}
PROBED: list[str] = []
# Run once while Google is "thinking" about a key: what someone else does
# meanwhile (a race).
GOOGLE_HOOKS: dict[str, object] = {}


def _google_says(code: int, status: str, message: str):
    return {"error": {"code": code, "status": status, "message": message}}


class _FakeModels:
    def __init__(self, key):
        self.key = key

    def list(self, *args, **kwargs):
        PROBED.append(self.key)
        hook = GOOGLE_HOOKS.pop(self.key, None)
        if hook:
            hook()
        how = GOOGLE.get(self.key, "ok")
        if how == "refuse":
            # Google's real text names no key; this one does, to prove it's
            # taken out before anything is stored.
            raise genai_errors.ClientError(400, _google_says(
                400, "INVALID_ARGUMENT", f"API key not valid. Please pass a valid API key. [{self.key}]"))
        if how == "denied":
            raise genai_errors.ClientError(403, _google_says(403, "PERMISSION_DENIED", "Method doesn't allow unregistered callers."))
        if how == "quota":
            raise genai_errors.ClientError(429, _google_says(429, "RESOURCE_EXHAUSTED", "Quota exceeded for project 123456."))
        if how == "server":
            raise genai_errors.ServerError(503, _google_says(503, "UNAVAILABLE", "The service is currently unavailable."))
        if how == "network":
            raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")
        return iter([])


class FakeGenaiClient:
    def __init__(self, *args, api_key=None, **kwargs):
        self.models = _FakeModels(api_key)
        self.interactions = None

    def close(self):
        pass


import google.genai as _genai  # noqa: E402

_genai.Client = FakeGenaiClient

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event, inspect, text  # noqa: E402

import app.api.ai_keys as ai_keys_api  # noqa: E402
from app.ai import keys, secretbox  # noqa: E402
from app.api.main import app  # noqa: E402
from app.auth.core import SESSION_COOKIE  # noqa: E402
from app.config import DEFAULT_SECRETS_DIR, DEFAULT_STORAGE_DIR, settings  # noqa: E402
from app.reports.db import SessionLocal, engine  # noqa: E402
from app.reports.models import ApiKey, AppSetting, AuditEntry, AuthSession, User, UserAiKey  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

# ---------------------------------------------------------------- harness
BASE = "https://carlink.test"
SHAPE = {"has_key", "last4", "status", "billing_confirmed", "added_at", "last_checked_at", "last_error",
         "company_allowed", "ai_available", "source"}
USER_KEY_SHAPE = {"has_key", "last4", "status", "added_at"}
IN_USE_BODY = {"detail": "This key can't be used here.", "code": "ai_key_in_use"}
# Every key a test sends; none may appear in a response, a log line or the
# database file.
SENT_KEYS: set[str] = {ENV_COMPANY_KEY}
RESPONSES: list[str] = []

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}


class Stop(Exception):
    pass


def check(cond, label: str, why: str = "") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
        return True
    FAILURES.append((_section["name"], label + (f"  [{why}]" if why else "")))
    print(f"    FAIL {label}" + (f"\n           got: {why}" if why else ""))
    return False


def need(cond, label: str, why: str = "") -> None:
    if not check(cond, label, why):
        raise Stop(label)


def run_section(name: str, fn) -> None:
    _section["name"] = name
    print(f"\n[{name}]")
    try:
        fn()
    except Stop as exc:
        print(f"    ---  rest of this section skipped: {exc}")
    except Exception as exc:
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if Path(f.filename).resolve() == Path(__file__).resolve()]
        msg = f"section stopped by {type(exc).__name__}: {exc} (test line {frames[-1].lineno if frames else -1})"
        FAILURES.append((name, msg))
        print(f"    FAIL {msg}")


class Browser(TestClient):
    """Keeps every response body, to look for keys in them afterwards."""

    def request(self, *args, **kwargs):
        r = super().request(*args, **kwargs)
        RESPONSES.append(r.text)
        return r


def browser() -> Browser:
    return Browser(app, base_url=BASE, raise_server_exceptions=False)


def jd(r) -> dict:
    try:
        body = r.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def brief(r) -> str:
    """Status and body, with any test key masked: failure output is printed."""
    body = (r.text or "").replace("\n", " ")[:240]
    for k in SENT_KEYS:
        body = body.replace(k, "<KEY>")
    return f"{r.status_code} {body}"


class Person:
    def __init__(self, label: str, role: str = "user", **profile):
        self.label = label
        self.c = browser()
        self.id = sign_in(self.c, role=role, name=label, username=label.lower(), **profile)

    def get(self):
        return self.c.get("/auth/me/ai-key")

    def put(self, key: str, billing=True, **extra):
        if keys.format_problem(key.strip()) is None:
            SENT_KEYS.add(key.strip())
        body = {"api_key": key, **extra}
        if billing is not None:
            body["billing_confirmed"] = billing
        return self.c.put("/auth/me/ai-key", json=body)

    def test(self):
        return self.c.post("/auth/me/ai-key/test")

    def delete(self):
        return self.c.delete("/auth/me/ai-key")

    def other_tab(self) -> "Person":
        """The same session in a second browser (for a request made while
        one of this person's is still running)."""
        tab = object.__new__(Person)
        tab.label, tab.id = self.label, self.id
        tab.c = browser()
        tab.c.cookies.set(SESSION_COOKIE, self.c.cookies.get(SESSION_COOKIE))
        return tab


def row_of(user_id: str):
    db = SessionLocal()
    try:
        return db.query(UserAiKey).filter(UserAiKey.user_id == user_id).first()
    finally:
        db.close()


def key_rows() -> int:
    db = SessionLocal()
    try:
        return db.query(UserAiKey).count()
    finally:
        db.close()


def audits(action: str) -> list[AuditEntry]:
    db = SessionLocal()
    try:
        return db.query(AuditEntry).filter(AuditEntry.action == action).order_by(AuditEntry.id).all()
    finally:
        db.close()


def users_by_id(admin) -> dict:
    r = admin.c.get("/users")
    rows = r.json() if r.status_code == 200 else []
    return {u["id"]: u for u in rows}


def restart_secretbox(create: bool = True) -> str:
    """As if the process started again: reads the secret from scratch."""
    secretbox.forget()
    return secretbox.startup(create=create)


def stored_statuses() -> set:
    db = SessionLocal()
    try:
        return {s for (s,) in db.query(UserAiKey.status).distinct().all()}
    finally:
        db.close()


def canary_value() -> str | None:
    db = SessionLocal()
    try:
        row = db.get(AppSetting, secretbox.CANARY_KEY)
        return row.value if row else None
    finally:
        db.close()


def set_canary(value: str | None) -> None:
    db = SessionLocal()
    try:
        row = db.get(AppSetting, secretbox.CANARY_KEY)
        if value is None:
            if row:
                db.delete(row)
        elif row:
            row.value = value
        else:
            db.add(AppSetting(key=secretbox.CANARY_KEY, value=value))
        db.commit()
    finally:
        db.close()


def storage_info() -> dict:
    r = W.admin.c.get("/system/info")
    return (jd(r).get("ai_key_storage") or {}) if r.status_code == 200 else {"error": brief(r)}


def error_lines(*words) -> list[str]:
    return [line for line in LOG_LINES if "app.ai.secretbox ERROR" in line and all(w in line for w in words)]


W = type("World", (), {})()


# ================================================================= sections
def setup() -> None:
    # The real start-up (lifespan): init_db, then the api makes the secret.
    W.boot = browser()
    W.boot.__enter__()
    need(secretbox.state() == "ok", "the api's start-up makes the secret and the canary", secretbox.state())
    W.admin = Person("Ada", role="admin")
    # Ticked to see all cases, so they get the ordinary per-person limits
    # (an account with no ticks gets 3 saves an hour); neither may use the
    # company key, which is what matters to their AI key.
    W.A = Person("Alice", sees_all_cases=True)
    W.B = Person("Bob", sees_all_cases=True)
    W.V = Person("Vic", ai_company_key_allowed=True)


def secret_file() -> None:
    path = SECRETS / "ai-keys.secret"
    need(path.is_file(), "ai-keys.secret is made in SECRETS_DIR")
    check(sorted(p.name for p in SECRETS.iterdir()) == ["ai-keys.secret"], "and nothing else is left there (no temp files)",
          str([p.name for p in SECRETS.iterdir()]))
    check(not str(path).startswith(str(STORAGE)), "the secret isn't under STORAGE_DIR (which /files serves)")
    check(not str(DEFAULT_SECRETS_DIR).startswith(str(DEFAULT_STORAGE_DIR) + os.sep),
          "by default either: secrets/ is beside storage/, not in it", str(DEFAULT_SECRETS_DIR))
    if os.name == "posix":
        check(path.stat().st_mode & 0o777 == 0o600, "the secret file is mode 0600", oct(path.stat().st_mode & 0o777))
    else:
        print("    --   mode 0600 is checked on Linux only")
    W.good_secret = path.read_bytes()
    db = SessionLocal()
    try:
        canary = db.get(AppSetting, secretbox.CANARY_KEY)
    finally:
        db.close()
    check(canary is not None and canary.value.startswith("gAAAA"), "app_settings ai_keys_canary holds a sealed value")
    r = W.A.c.get("/settings")
    check(r.status_code == 200 and secretbox.CANARY_KEY not in r.text, "GET /settings doesn't show the canary", brief(r))
    print("  the bot only reads it")
    check(restart_secretbox(create=False) == "ok", "a reader (the bot) opens the canary with the same secret")
    check(path.read_bytes() == W.good_secret, "...and changes nothing")
    restart_secretbox(create=True)


def seal_round_trip() -> None:
    key = make_key("RoundTrip")
    SENT_KEYS.add(key)
    sealed = secretbox.seal(key)
    check(secretbox.unseal(sealed) == key, "seal then unseal gives the key back")
    check(key not in sealed and key[4:20] not in sealed, "the sealed value doesn't contain the key")
    check(secretbox.seal(key) != sealed, "sealing twice gives two different values (random IV)")
    check(secretbox.unseal(secretbox.seal("Grüße ✓")) == "Grüße ✓", "any text survives the round trip")
    tampered = sealed[:-6] + ("A" if sealed[-6] != "A" else "B") + sealed[-5:]
    try:
        secretbox.unseal(tampered)
        check(False, "a changed sealed value is refused")
    except secretbox.Unreadable:
        check(True, "a changed sealed value is refused (Unreadable)")
    fp = secretbox.fingerprint(key)
    check(len(fp) == 64 and all(ch in "0123456789abcdef" for ch in fp), "the fingerprint is 64 hex characters")
    check(fp == secretbox.fingerprint(key), "the same key always has the same fingerprint")
    check(fp != hashlib.sha256(key.encode()).hexdigest(), "the fingerprint is not a plain SHA-256 of the key (HMAC)")
    check(fp != secretbox.fingerprint(key + "x"), "different keys, different fingerprints")


def table_rules() -> None:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT type, name, sql FROM sqlite_master WHERE tbl_name = 'user_ai_keys'")).all()
    table_sql = next((sql for kind, _, sql in rows if kind == "table"), "") or ""
    need(table_sql, "create_all made the user_ai_keys table")
    flat = " ".join(table_sql.split())
    check("UNIQUE (user_id)" in flat, "user_id is unique, inside CREATE TABLE", flat[-200:])
    check("UNIQUE (fingerprint)" in flat, "fingerprint is unique, inside CREATE TABLE", flat[-200:])
    separate = [name for kind, name, sql in rows if kind == "index" and sql]
    check(not separate, "no separately created index the table relies on", str(separate))
    cols = {c["name"] for c in inspect(engine).get_columns("user_ai_keys")}
    want = {"id", "user_id", "provider", "key_enc", "last4", "fingerprint", "status", "last_error", "billing_confirmed",
            "last_checked_at", "last_used_at", "created_at", "updated_at"}
    check(want <= cols, "every column the brief lists is there", str(sorted(want - cols)))


def get_without_key() -> None:
    r = W.A.get()
    body = jd(r)
    need(r.status_code == 200, "GET /auth/me/ai-key -> 200", brief(r))
    check(set(body) == SHAPE, "the response has exactly the brief's fields", str(sorted(set(body) ^ SHAPE)))
    check(body.get("has_key") is False and body.get("last4") is None and body.get("status") is None,
          "no key yet: has_key false, last4 and status null", brief(r))
    check(body.get("company_allowed") is False and body.get("ai_available") is False and body.get("source") == "none",
          "plain user without a key: no company key, AI not available, source none", brief(r))
    body = jd(W.V.get())
    check(body.get("company_allowed") is True and body.get("ai_available") is True and body.get("source") == "company",
          "ticked user without a key: company key allowed, source company", str(body))
    body = jd(W.admin.get())
    check(body.get("company_allowed") is True and body.get("source") == "company", "admin without a key: source company", str(body))


def put_refusals() -> None:
    ai_keys_api.reset_limits()
    for bad, why in (("", "empty"), ("short", "too short"), ("AIza has spaces in the middle of it", "spaces inside"),
                     ("AIza" + "!" * 35, "symbols"), ("A" * 61, "61 characters"), ("ключ" * 10, "not ASCII")):
        r = W.A.put(bad)
        check(r.status_code == 400 and jd(r).get("code") == "ai_key_format", f"PUT with a bad key ({why}) -> 400 ai_key_format", brief(r))
    key = make_key("AliceOne")
    for billing, label in ((None, "missing"), (False, "false")):
        r = W.A.put(key, billing=billing)
        check(r.status_code == 400 and jd(r).get("code") == "billing_not_confirmed",
              f"PUT with billing_confirmed {label} -> 400 billing_not_confirmed", brief(r))
    check(row_of(W.A.id) is None and not PROBED, "nothing was stored, and Google wasn't asked")


def put_and_get() -> None:
    ai_keys_api.reset_limits()
    key = make_key("AliceOne")
    W.A_key = key
    r = W.A.put(f"  {key}\n")
    body = jd(r)
    need(r.status_code == 200, "PUT a good key (with spaces around it) -> 200", brief(r))
    check(set(body) == SHAPE, "the response has exactly the brief's fields", str(sorted(set(body) ^ SHAPE)))
    check(body.get("has_key") is True and body.get("last4") == key[-4:] and body.get("status") == "ok",
          "has_key, the last 4 characters, status ok", brief(r))
    check(body.get("billing_confirmed") is True and body.get("added_at") and body.get("last_checked_at"),
          "billing_confirmed, added_at and last_checked_at are set", brief(r))
    check(body.get("ai_available") is True and body.get("source") == "own" and body.get("company_allowed") is False,
          "a plain user with a working key: AI available on their own key", brief(r))
    check(PROBED[-1:] == [key], "Google was asked about exactly that key (trimmed)")
    check(jd(W.A.get()) == body, "GET says the same")
    row = row_of(W.A.id)
    need(row is not None, "the key is stored")
    check(row.key_enc != key and key not in row.key_enc and secretbox.unseal(row.key_enc) == key,
          "stored sealed, and it unseals to the key")
    check(row.fingerprint == secretbox.fingerprint(key) and row.provider == "gemini" and row.last4 == key[-4:],
          "fingerprint, provider and last4 stored")
    saved = audits("ai_key_saved")
    check(saved and saved[-1].user_id == W.A.id and key[-4:] in saved[-1].detail and key not in saved[-1].detail,
          "history entry ai_key_saved, with the last 4 only", saved[-1].detail if saved else "none")

    print("  replacing it")
    old_fp = row.fingerprint
    key2 = make_key("AliceTwo")
    r = W.A.put(key2)
    body2 = jd(r)
    check(r.status_code == 200 and body2.get("last4") == key2[-4:], "PUT another key -> 200, the new last 4", brief(r))
    check(key_rows() == 1, "still one row: the new key replaced the old one", str(key_rows()))
    check(row_of(W.A.id).fingerprint != old_fp, "...with its own fingerprint")
    check(body2.get("added_at", "") >= body.get("added_at", ""), "added_at is the replacement's")
    W.A_key = key2
    r = W.B.put(key)
    check(r.status_code == 200, "the replaced key is free again: Bob can save it", brief(r))
    W.B_key = key
    r = W.A.put(key2)
    check(r.status_code == 200, "saving your own key again is fine (not 'in use')", brief(r))

    print("  never in the database file")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA wal_checkpoint(FULL)"))
    blobs = b"".join(p.read_bytes() for p in _tmp.iterdir() if p.name.startswith("test.db"))
    leaked = [k for k in SENT_KEYS if k.encode() in blobs]
    check(not leaked, "no key sent so far is anywhere in the SQLite files (db, -wal, -shm)", f"{len(leaked)} found")
    check(not any(k[4:24].encode() in blobs for k in (key, key2)), "not even 20 characters of one")


def in_use() -> None:
    ai_keys_api.reset_limits()
    before = row_of(W.V.id)
    r = W.V.put(W.A_key)
    check(r.status_code == 409 and jd(r) == IN_USE_BODY, "another account's key -> 409 ai_key_in_use, generic detail", brief(r))
    first = r.json() if r.status_code == 409 else None
    check(before is None and row_of(W.V.id) is None, "nothing stored for the second account")
    check(row_of(W.A.id).fingerprint == secretbox.fingerprint(W.A_key), "the owner keeps it")
    refused = audits("ai_key_refused")
    check(refused and refused[-1].user_id == W.V.id and "another account" in refused[-1].detail
          and W.A_key not in refused[-1].detail, "history entry ai_key_refused (for the admins)", refused[-1].detail if refused else "none")

    r = W.V.put(f" {ENV_COMPANY_KEY} ")
    check(r.status_code == 409 and r.json() == first, "the GEMINI_API_KEY company key -> the same 409, word for word", brief(r))
    company = make_key("CompanyRow")
    SENT_KEYS.add(company)
    # Company keys from Settings are stored as they are (api_keys, as
    # before this release); only own keys are sealed.
    W.stored_plain = {company}
    r = W.admin.c.post("/setup/llm-keys", json={"apiKey": company, "label": "Firm"})
    need(r.status_code == 200, "the admin adds a company key in Settings", brief(r))
    r = W.V.put(company)
    check(r.status_code == 409 and r.json() == first, "a company key from Settings -> the same 409", brief(r))
    r = W.admin.put(company)
    check(r.status_code == 409, "even for an admin", brief(r))
    check(row_of(W.V.id) is None and row_of(W.admin.id) is None, "nothing stored")
    check(audits("ai_key_refused")[-1].detail.endswith("a company key"), "the history says which (company key)")


def google_refuses() -> None:
    ai_keys_api.reset_limits()
    for how in ("refuse", "denied"):
        bad = make_key(f"Refused{how}")
        GOOGLE[bad] = how
        r = W.V.put(bad)
        check(r.status_code == 400 and jd(r).get("code") == "ai_key_invalid", f"Google refuses it ({how}) -> 400 ai_key_invalid", brief(r))
        check(PROBED[-1:] == [bad], "...after asking Google (the fake)")
        check(row_of(W.V.id) is None, "...and it isn't stored")
    r = W.V.get()
    check(jd(r).get("has_key") is False, "GET: still no key", brief(r))
    bad = make_key("RefusedAgain")
    GOOGLE[bad] = "refuse"
    r = W.A.put(bad)
    check(r.status_code == 400 and secretbox.unseal(row_of(W.A.id).key_enc) == W.A_key,
          "someone with a key who tries a refused one keeps the old one", brief(r))
    check(r.status_code == 400 and bad not in r.text and "API key not valid" not in r.text,
          "the 400 is our own words, not Google's", brief(r))


def no_answer() -> None:
    ai_keys_api.reset_limits()
    for how in ("network", "server"):
        k = make_key(f"NoAnswer{how}")
        GOOGLE[k] = how
        r = W.V.put(k)
        body = jd(r)
        check(r.status_code == 200 and body.get("status") == "untested",
              f"Google can't be asked ({how}) -> saved as untested", brief(r))
        check(body.get("last_checked_at") is None, "...not checked, so no last_checked_at", brief(r))
        check(body.get("ai_available") is True and body.get("source") == "own", "...and counts as working", brief(r))
    W.V_key = k
    q = make_key("Quota")
    GOOGLE[q] = "quota"
    r = W.B.put(q)
    body = jd(r)
    check(r.status_code == 200 and body.get("status") == "quota", "Google says out of quota -> saved, status quota", brief(r))
    check(body.get("ai_available") is False and body.get("source") == "none",
          "a plain user whose key is out of quota: AI not available", brief(r))
    check("Quota exceeded" in (body.get("last_error") or ""), "last_error says why (to the owner only)", brief(r))
    W.B_key = q
    GOOGLE[q] = "ok"


def test_endpoint() -> None:
    ai_keys_api.reset_limits()
    nobody = Person("Nora")
    r = nobody.test()
    check(r.status_code == 404 and jd(r).get("code") == "no_ai_key", "test without a key -> 404 no_ai_key", brief(r))
    r = W.B.test()
    body = jd(r)
    check(r.status_code == 200 and set(body) == SHAPE and body.get("status") == "ok",
          "test -> the same shape, status ok now", brief(r))
    check(body.get("last_error") is None and body.get("source") == "own", "the old error is gone", brief(r))
    check(PROBED[-1:] == [W.B_key], "it asked Google about Bob's own key, nobody else's")
    GOOGLE[W.B_key] = "refuse"
    r = W.B.test()
    body = jd(r)
    check(r.status_code == 200 and body.get("status") == "invalid", "Google refuses it now -> status invalid", brief(r))
    err = body.get("last_error") or ""
    check("API key not valid" in err and W.B_key not in err and len(err) <= 200,
          "last_error: Google's words, the key taken out, at most 200 characters", err.replace(W.B_key, "<KEY>"))
    check(keys.scrub("x AIzaSyAnotherKeyLooking123 y") == "x AIza... y", "anything else key-like is taken out too")
    check(body.get("ai_available") is False and body.get("source") == "none", "an invalid key: AI not available", brief(r))
    stamp = row_of(W.B.id).last_checked_at
    GOOGLE[W.B_key] = "network"
    r = W.B.test()
    check(r.status_code == 503 and jd(r).get("code") == "ai_key_check_failed", "Google can't be reached -> 503 ai_key_check_failed", brief(r))
    after = row_of(W.B.id)
    check(after.status == "invalid" and after.last_checked_at == stamp, "...and the stored verdict is left alone")
    GOOGLE[W.B_key] = "ok"
    r = W.A.test()
    check(PROBED[-1:] == [W.A_key] and jd(r).get("status") == "ok", "Alice's test asks about Alice's key", brief(r))


def deleting() -> None:
    ai_keys_api.reset_limits()
    k = make_key("DeleteMe")
    r = W.V.put(k)
    need(r.status_code == 200, "Vic has a key", brief(r))
    r = W.V.delete()
    check(r.status_code == 200 and r.json() == {"ok": True}, "DELETE /auth/me/ai-key -> {ok: true}", brief(r))
    check(row_of(W.V.id) is None and jd(W.V.get()).get("has_key") is False, "the key is gone")
    removed = audits("ai_key_removed")
    check(removed and removed[-1].user_id == W.V.id and k[-4:] in removed[-1].detail and k not in removed[-1].detail,
          "history entry ai_key_removed, last 4 only", removed[-1].detail if removed else "none")
    n = len(removed)
    r = W.V.delete()
    check(r.status_code == 200 and r.json() == {"ok": True} and len(audits("ai_key_removed")) == n,
          "deleting again: still {ok: true}, no second entry", brief(r))
    r = W.V.put(k)
    check(r.status_code == 200, "the key can be saved again afterwards", brief(r))

    print("  an admin removes someone's key")
    r = W.A.c.delete(f"/users/{W.V.id}/ai-key")
    check(r.status_code == 403, "a plain user: DELETE /users/{id}/ai-key -> 403", brief(r))
    check(row_of(W.V.id) is not None, "...and the key is still there")
    r = W.admin.c.delete(f"/users/{W.V.id}/ai-key")
    check(r.status_code == 200 and r.json() == {"ok": True}, "the admin: DELETE /users/{id}/ai-key -> {ok: true}", brief(r))
    check(row_of(W.V.id) is None, "the key is gone")
    by_admin = audits("ai_key_removed_by_admin")
    check(by_admin and by_admin[-1].user_id == W.admin.id and k[-4:] in by_admin[-1].detail and "@example.com" in by_admin[-1].detail,
          "history entry ai_key_removed_by_admin, whose and which (last 4)", by_admin[-1].detail if by_admin else "none")
    r = W.admin.c.delete(f"/users/{W.V.id}/ai-key")
    check(r.status_code == 200, "no key to remove: still {ok: true}", brief(r))
    r = W.admin.c.delete("/users/nosuchuser00/ai-key")
    check(r.status_code == 404, "an unknown account -> 404", brief(r))

    print("  keys.delete_user_key, for the disable transaction (P2)")
    r = W.V.put(k)
    need(r.status_code == 200, "Vic saves the key again", brief(r))
    db = SessionLocal()
    try:
        admin_row = db.get(User, W.admin.id)
        check(keys.delete_user_key(db, W.V.id, admin_row, reason="account disabled") is True, "returns True when there was a key")
        db.rollback()
    finally:
        db.close()
    check(row_of(W.V.id) is not None, "the caller's rollback keeps the key: it's part of the caller's transaction")
    db = SessionLocal()
    try:
        keys.delete_user_key(db, W.V.id, db.get(User, W.admin.id), reason="account disabled")
        check(keys.delete_user_key(db, "nosuchuser00", None) is False, "returns False when there is none")
        db.commit()
    finally:
        db.close()
    check(row_of(W.V.id) is None, "the caller's commit deletes it")
    last = audits("ai_key_removed")[-1]
    check(last.user_id == W.admin.id and "account disabled" in last.detail, "with a history entry naming the actor and reason", last.detail)


def disabling() -> None:
    ai_keys_api.reset_limits()
    dee = Person("Dee")
    k = make_key("Disabled")
    r = dee.put(k)
    need(r.status_code == 200 and row_of(dee.id) is not None, "Dee has a key", brief(r))

    print("  a refused disable keeps the key")
    r = W.admin.c.patch(f"/users/{dee.id}", json={"active": False, "open_cases": "move", "open_cases_to": "nosuchuser00"})
    check(r.status_code >= 400 and row_of(dee.id) is not None, "disable refused (unknown recipient): the key is still there", brief(r))

    print("  disabling deletes it, in the same change")
    r = W.admin.c.patch(f"/users/{dee.id}", json={"active": False})
    check(r.status_code == 200 and r.json().get("active") is False, "the admin disables Dee", brief(r))
    check(row_of(dee.id) is None, "her key is gone")
    entry = audits("ai_key_removed_by_admin")[-1]
    check(entry.user_id == W.admin.id and "account disabled" in entry.detail and k[-4:] in entry.detail and k not in entry.detail,
          "history: removed by the admin, because the account was disabled, last 4 only", entry.detail)
    u = users_by_id(W.admin).get(dee.id, {})
    check(u.get("ai_key") is None, "GET /users shows no key for her", str(u.get("ai_key")))
    r = W.admin.c.patch(f"/users/{dee.id}", json={"active": True})
    check(r.status_code == 200 and row_of(dee.id) is None, "enabling her again doesn't bring the key back", brief(r))


def users_screen() -> None:
    ai_keys_api.reset_limits()
    users = users_by_id(W.admin)
    need(W.A.id in users, "GET /users lists Alice")
    a = users[W.A.id].get("ai_key")
    check(isinstance(a, dict) and set(a) == USER_KEY_SHAPE, "a user with a key: ai_key has has_key, last4, status, added_at",
          str(sorted(a) if isinstance(a, dict) else a))
    check(a and a["has_key"] is True and a["last4"] == W.A_key[-4:] and a["status"] == "ok", "...with the right values", str(a))
    check("ai_key" in users[W.V.id] and users[W.V.id]["ai_key"] is None, "a user without one: ai_key null")
    text_all = W.admin.c.get("/users").text
    check(not any(k in text_all for k in SENT_KEYS) and "key_enc" not in text_all and "fingerprint" not in text_all,
          "no key, sealed key or fingerprint in GET /users")
    r = W.admin.c.patch(f"/users/{W.A.id}", json={"qualifications": "MIMI"})
    check(r.status_code == 200 and (jd(r).get("ai_key") or {}).get("last4") == W.A_key[-4:], "PATCH /users/{id} returns ai_key too", brief(r))

    print("  one query for everyone's keys, not one per user")
    selects = {"n": 0}

    def count(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects["n"] += 1

    # The secret's every-10-seconds look at the canary is one SELECT; not
    # while counting.
    recheck, secretbox._RECHECK_SECONDS = secretbox._RECHECK_SECONDS, 1e9
    event.listen(engine, "before_cursor_execute", count)
    try:
        W.admin.c.get("/users")
        selects["n"] = 0
        W.admin.c.get("/users")
        before = selects["n"]
        for i in range(5):
            p = Person(f"Extra{i}")
            r = p.put(make_key(f"Extra{i}"))
            check(r.status_code == 200, f"extra account {i + 1} saves a key", brief(r))
        selects["n"] = 0
        users = users_by_id(W.admin)
        after = selects["n"]
    finally:
        event.remove(engine, "before_cursor_execute", count)
        secretbox._RECHECK_SECONDS = recheck
    check(sum(1 for u in users.values() if u.get("ai_key")) >= 7, "GET /users shows the new keys")
    check(after == before, "5 more accounts with keys add no queries to GET /users", f"{before} -> {after} SELECTs")


def rate_limits() -> None:
    ai_keys_api.reset_limits()
    clock = [10_000.0]
    ai_keys_api._clock = lambda: clock[0]
    try:
        _rate_limits(clock)
    finally:
        ai_keys_api._clock = time.monotonic
        ai_keys_api.reset_limits()


def _limited(p) -> list[AuditEntry]:
    return [a for a in audits("ai_key_rate_limited") if a.user_id == p.id]


def _rate_limits(clock) -> None:
    print("  someone with a tick: 10 an hour")
    p = Person("Rita", sees_all_cases=True)
    codes = [p.put("bad").status_code for _ in range(ai_keys_api.SAVES_PER_HOUR)]
    check(codes == [400] * ai_keys_api.SAVES_PER_HOUR, f"{ai_keys_api.SAVES_PER_HOUR} tries in an hour are answered", str(codes))
    r = p.put(make_key("RitaGood"))
    check(r.status_code == 429 and jd(r).get("code") == "rate_limited", "the next one -> 429 rate_limited, even a good key", brief(r))
    check(row_of(p.id) is None, "...and it isn't saved")
    r = W.B.put("bad")
    check(r.status_code == 400, "someone else isn't affected", brief(r))

    print("  an account with no ticks: 3 an hour, 10 a day")
    u = Person("Uma")
    codes = [u.put("bad").status_code for _ in range(3)]
    check(codes == [400] * 3, "3 tries are answered", str(codes))
    for _ in range(3):
        r = u.put(make_key("UmaGood"))
    check(r.status_code == 429 and "hour" in jd(r).get("detail", ""), "the 4th within the hour -> 429 rate_limited", brief(r))
    check(len(_limited(u)) == 1, "three 429s, one history entry (at most one an hour each)", str(len(_limited(u))))
    answered = 3
    for _hour in range(5):
        clock[0] += ai_keys_api._HOUR + 1
        for _ in range(3):
            if u.put("bad").status_code == 400:
                answered += 1
    check(answered == ai_keys_api.UNVETTED_SAVES_PER_DAY, f"over the day: {ai_keys_api.UNVETTED_SAVES_PER_DAY} answered in all",
          str(answered))
    r = u.put("bad")
    check(r.status_code == 429 and "today" in jd(r).get("detail", ""), "then 429 rate_limited for the day", brief(r))
    check(len(_limited(u)) >= 2, "an hour on, a 429 is in the history again", str(len(_limited(u))))
    clock[0] += ai_keys_api._DAY
    check(u.put("bad").status_code == 400, "a day later it is answered again")

    print("  the day's total: only checked keys count")
    ai_keys_api.reset_limits()
    staff = [Person(f"Many{i}", sees_all_cases=True) for i in range(ai_keys_api.SAVES_PER_DAY_ALL // ai_keys_api.SAVES_PER_HOUR)]
    for q in staff:
        for i in range(ai_keys_api.SAVES_PER_HOUR):
            if i % 2:
                q.put("bad")
            else:
                q.put(make_key("NoBilling"), billing=None)
    check(ai_keys_api.saves_all.used("all") == 0, "100 tries with a bad format or no billing tick use none of it",
          str(ai_keys_api.saves_all.used("all")))
    clock[0] += ai_keys_api._HOUR + 1
    refused = make_key("RefusedForCap")
    GOOGLE[refused] = "refuse"
    codes = [q.put(refused).status_code for q in staff for _ in range(ai_keys_api.SAVES_PER_HOUR)]
    check(codes.count(400) == ai_keys_api.SAVES_PER_DAY_ALL, f"{ai_keys_api.SAVES_PER_DAY_ALL} checked tries across accounts are answered",
          f"{codes.count(400)} answered")
    late = Person("Late", sees_all_cases=True)
    r = late.put(make_key("LateGood"))
    check(r.status_code == 429 and jd(r).get("code") == "rate_limited" and "today" in jd(r).get("detail", ""),
          "the next, from another account -> 429 rate_limited (the day's total)", brief(r))
    for who, p in (("an admin", Person("Ann", role="admin")),
                   ("someone ticked for the company key", Person("Cara", ai_company_key_allowed=True)),
                   ("someone ticked to sign off", Person("Sam", may_sign_off=True))):
        r = p.put(make_key(f"Exempt{p.label}"))
        check(r.status_code == 200, f"{who} isn't held to it", brief(r))

    print("  one stranger can't use it up")
    ai_keys_api.reset_limits()
    stranger = Person("Stan")
    for _hour in range(24):
        for _ in range(5):
            stranger.put(refused)
        clock[0] += ai_keys_api._HOUR + 1
    used = ai_keys_api.saves_all.used("all")
    check(used <= ai_keys_api.UNVETTED_SAVES_PER_DAY, f"a day of trying uses {used} of the {ai_keys_api.SAVES_PER_DAY_ALL}")
    check(len(_limited(stranger)) <= 24, "and the history has at most one 429 an hour from them", str(len(_limited(stranger))))

    print("  re-checking")
    ai_keys_api.reset_limits()
    codes = [W.A.test().status_code for _ in range(ai_keys_api.TESTS_PER_HOUR)]
    check(codes == [200] * ai_keys_api.TESTS_PER_HOUR, f"{ai_keys_api.TESTS_PER_HOUR} tests in an hour", str(codes))
    r = W.A.test()
    check(r.status_code == 429 and jd(r).get("code") == "rate_limited", "the next -> 429 rate_limited", brief(r))


def company_keys() -> None:
    ai_keys_api.reset_limits()
    before = audits("ai_key_refused")
    r = W.admin.c.post("/setup/llm-keys", json={"apiKey": f"  {W.A_key} ", "label": "Not Alice's"})
    check(r.status_code == 409 and jd(r) == IN_USE_BODY, "an admin adding someone's own key as a company key -> the same generic 409", brief(r))
    db = SessionLocal()
    try:
        stored = db.query(ApiKey).filter(ApiKey.key == W.A_key).count()
    finally:
        db.close()
    check(stored == 0, "it isn't added")
    entry = audits("ai_key_refused")[len(before):]
    check(entry and "@alice" in entry[-1].detail and "own key" in entry[-1].detail and W.A_key not in entry[-1].detail,
          "the history says whose (for the admins)", entry[-1].detail if entry else "none")
    fresh = make_key("CompanyFresh")
    SENT_KEYS.add(fresh)
    W.stored_plain.add(fresh)
    r = W.admin.c.post("/setup/llm-keys", json={"apiKey": fresh, "label": "Fresh"})
    check(r.status_code == 200, "a key nobody has is added as usual", brief(r))
    r = W.B.put(fresh)
    check(r.status_code == 409, "...and then can't be saved as someone's own", brief(r))


def save_vs_disable() -> None:
    ai_keys_api.reset_limits()
    done: list[int] = []

    def disable(p):
        def hook():
            done.append(W.admin.c.patch(f"/users/{p.id}", json={"active": False}).status_code)
        return hook

    print("  the account is disabled while Google checks the key")
    for label, has_key in (("Rae", False), ("Rex", True)):
        p = Person(label, sees_all_cases=True)
        if has_key:
            need(p.put(make_key(f"{label}Old")).status_code == 200, f"{label} has a key")
        k = make_key(f"{label}Race")
        saved_before = len([a for a in audits("ai_key_saved") if a.user_id == p.id])
        GOOGLE_HOOKS[k] = disable(p)
        r = p.put(k)
        check(done[-1:] == [200], f"the admin disabled {label} meanwhile", str(done))
        check(r.status_code == 401 and jd(r).get("code") == "signed_out", f"{label}'s save is refused: 401 signed_out", brief(r))
        check(row_of(p.id) is None,
              f"no key is left on {label}'s disabled account" + (" (the old one went with the disable)" if has_key else ""))
        check(len([a for a in audits("ai_key_saved") if a.user_id == p.id]) == saved_before, "and no ai_key_saved entry for it")

    print("  signed out meanwhile, or switched off without it")
    p = Person("Ria", sees_all_cases=True)
    k = make_key("RiaRace")

    def sign_out():
        db = SessionLocal()
        try:
            db.query(AuthSession).filter(AuthSession.user_id == p.id).delete()
            db.commit()
        finally:
            db.close()

    GOOGLE_HOOKS[k] = sign_out
    r = p.put(k)
    check(r.status_code == 401 and jd(r).get("code") == "signed_out" and row_of(p.id) is None,
          "the session ended meanwhile -> 401 signed_out, nothing saved", brief(r))
    q = Person("Roy", sees_all_cases=True)
    k = make_key("RoyRace")

    def switch_off():
        with engine.begin() as conn:
            conn.execute(text("UPDATE users SET active = 0 WHERE id = :id"), {"id": q.id})

    GOOGLE_HOOKS[k] = switch_off
    r = q.put(k)
    check(r.status_code == 403 and jd(r).get("code") == "account_disabled" and row_of(q.id) is None,
          "the account switched off (session left) -> 403 account_disabled, nothing saved", brief(r))

    print("  keys.delete_user_key holds the write lock from the moment it runs")
    t = Person("Tia", sees_all_cases=True)
    need(t.put(make_key("TiaKey")).status_code == 200, "Tia has a key")
    import sqlite3

    db = SessionLocal()
    try:
        check(keys.delete_user_key(db, t.id, None, reason="test") is True, "it deletes her key (not committed yet)")
        other = sqlite3.connect(str(DB_FILE), timeout=0.2)
        try:
            other.execute("UPDATE app_settings SET value = value WHERE key = 'nothing'")
            other.commit()
            locked = False
        except sqlite3.OperationalError as exc:
            locked = "locked" in str(exc).lower()
        finally:
            other.close()
        check(locked, "another writer has to wait: no key can be saved between its read and its delete")
        db.rollback()
    finally:
        db.close()
    check(row_of(t.id) is not None, "(rolled back: she keeps it)")

    print("  an admin removing a key stops being one meanwhile")
    ann = Person("Ana", role="admin")
    real = keys.delete_user_key

    def demoted_first(db, user_id, actor, **kw):
        with engine.begin() as conn:
            conn.execute(text("UPDATE users SET role = 'user' WHERE id = :id"), {"id": ann.id})
        return real(db, user_id, actor, **kw)

    keys.delete_user_key = demoted_first
    try:
        r = ann.c.delete(f"/users/{t.id}/ai-key")
    finally:
        keys.delete_user_key = real
    check(r.status_code == 403 and row_of(t.id) is not None, "-> 403, and Tia keeps her key", brief(r))


def test_vs_replace() -> None:
    ai_keys_api.reset_limits()
    sid = Person("Sid", sees_all_cases=True)
    old, new = make_key("SidOld"), make_key("SidNew")
    need(sid.put(old).status_code == 200, "Sid has a key")
    tab = sid.other_tab()
    replaced: list[int] = []

    def replace():
        replaced.append(tab.put(new).status_code)

    GOOGLE[old] = "refuse"  # the old key's verdict, once Google answers
    GOOGLE_HOOKS[old] = replace
    r = sid.test()
    check(replaced == [200], "Sid saved a new key in another tab while the old one was being re-checked", str(replaced))
    body = jd(r)
    check(r.status_code == 200 and body.get("last4") == new[-4:] and body.get("status") == "ok",
          "the test answers about the key he has now: ok", brief(r))
    row = row_of(sid.id)
    check(row.status == "ok" and row.fingerprint == secretbox.fingerprint(new) and row.last_error is None,
          "the old key's verdict (invalid) wasn't stamped on the new key")



ROUTES = (("GET", "/auth/me/ai-key"), ("PUT", "/auth/me/ai-key"), ("POST", "/auth/me/ai-key/test"),
          ("DELETE", "/auth/me/ai-key"), ("DELETE", "/users/{id}/ai-key"))


def who_may() -> None:
    body = {"api_key": make_key("Nobody"), "billing_confirmed": True}
    anon = browser()
    temp = Person("Tess", must_change_password=True)
    temp_admin = Person("Tom", role="admin", must_change_password=True)
    for method, path in ROUTES:
        url = path.replace("{id}", W.A.id)
        kw = {"json": body} if method == "PUT" else {}
        r = anon.request(method, url, **kw)
        check(r.status_code == 401, f"signed out: {method} {path} -> 401", brief(r))
        for p in (temp, temp_admin):
            r = p.c.request(method, url, **kw)
            check(r.status_code == 403 and jd(r).get("code") == "password_change_required",
                  f"temporary password ({p.label}): {method} {path} -> 403 password_change_required", brief(r))
    check(row_of(temp.id) is None and row_of(W.A.id) is not None, "none of that saved or removed a key")
    r = W.B.c.delete(f"/users/{W.A.id}/ai-key")
    check(r.status_code == 403, "a plain user can't remove someone's key", brief(r))
    # Only your own: there is no route that takes someone else's key.
    me_a, me_b = jd(W.A.get()), jd(W.B.get())
    check(me_a.get("last4") == W.A_key[-4:] and me_b.get("last4") == W.B_key[-4:], "each person's GET shows their own key")


def bad_secret() -> None:
    path = SECRETS / "ai-keys.secret"
    need(restart_secretbox() == "ok", "the right secret opens the canary")
    a_status = jd(W.A.get()).get("status")
    check(storage_info().get("state") == "ok", "GET /system/info: ai_key_storage ok", str(storage_info()))

    def unreadable_everywhere(what: str, state: str):
        r = W.A.get()
        body = jd(r)
        check(body.get("status") == "unreadable" and body.get("has_key") is True and body.get("last4") == W.A_key[-4:],
              f"{what}: GET says unreadable (last 4 still shown)", brief(r))
        check(body.get("source") == "none" and body.get("ai_available") is False, f"{what}: Alice's own key isn't available", brief(r))
        check(jd(W.V.get()).get("source") == "company", f"{what}: company keys are unaffected (Vic still has the company key)")
        check((users_by_id(W.admin).get(W.A.id, {}).get("ai_key") or {}).get("status") == "unreadable",
              f"{what}: GET /users says unreadable")
        probes = len(PROBED)
        r = W.A.test()
        check(r.status_code == 200 and jd(r).get("status") == "unreadable" and len(PROBED) == probes,
              f"{what}: test says unreadable, without asking Google", brief(r))
        r = W.B.put(make_key("WhileBroken"))
        check(r.status_code == 503 and jd(r).get("code") == "ai_key_storage_off", f"{what}: saving -> 503 ai_key_storage_off", brief(r))
        info = storage_info()
        check(info.get("state") == state and "reset --confirm" in info.get("detail", "") and "ai-keys.secret" in info.get("detail", ""),
              f"{what}: /system/info says {state}, and names both fixes", str(info))
        check("unreadable" not in stored_statuses(), f"{what}: nothing written into the key rows", str(stored_statuses()))

    other = Fernet.generate_key()  # a valid secret, but the wrong one
    for label, content in (("the wrong secret", other), ("a damaged file", b"this is not a secret\n"), ("an empty file", b"")):
        path.write_bytes(content)
        state = restart_secretbox(create=True)
        want = "canary_failed" if label == "the wrong secret" else "broken"
        check(state == want, f"{label}: the start-up check says {want}", state)
        check(path.read_bytes() == content, f"{label}: the file is NOT replaced")
        check(sorted(p.name for p in SECRETS.iterdir()) == ["ai-keys.secret"], f"{label}: no new file made")
        unreadable_everywhere(label, want)
    check(error_lines("NOT replaced", "reset --confirm") and error_lines("canary", "reset --confirm"),
          "each is logged as an error that names the fixes")
    company = make_key("CompanyWhileBroken")
    SENT_KEYS.add(company)
    W.stored_plain.add(company)
    r = W.admin.c.post("/setup/llm-keys", json={"apiKey": company})
    check(r.status_code == 200, "adding a company key (the backup) still works meanwhile", brief(r))

    print("  a secret that doesn't open the saved keys is never blessed with a new canary")
    saved = canary_value()
    set_canary(None)
    path.write_bytes(other)
    check(restart_secretbox(create=True) == "canary_failed" and canary_value() is None,
          "canary row gone + the wrong secret: canary_failed, and no canary is sealed with it")
    set_canary(saved)

    path.unlink()
    path.mkdir()
    state = restart_secretbox(create=True)
    check(state == "broken" and path.is_dir(), "something at the path that can't be read (a folder): broken, left alone", state)
    unreadable_everywhere("an unreadable path", "broken")
    path.rmdir()

    path.write_bytes(W.good_secret)
    check(restart_secretbox(create=True) == "ok", "the right file back: ok again")
    check(jd(W.A.get()).get("status") == a_status, "Alice's key reads as it did, with no step in between",
          str(jd(W.A.get()).get("status")))

    print("  noticed while running, without a restart")
    recheck, secretbox._RECHECK_SECONDS = secretbox._RECHECK_SECONDS, 0
    try:
        path.write_bytes(other)
        check(secretbox.state() == "canary_failed", "the file swapped under a running process: canary_failed at the next look")
        path.write_bytes(W.good_secret)
        check(secretbox.state() == "ok" and jd(W.A.get()).get("status") == a_status, "the right one back: ok, and Alice's key with it")
        foreign = secretbox._make_box(other).fernet.encrypt(b"carlink ai-keys canary v1").decode()
        set_canary(foreign)
        check(secretbox.state() == "canary_failed", "a database from elsewhere restored under it: canary_failed")
        set_canary(saved)
        check(secretbox.state() == "ok", "the right database back: ok")
    finally:
        secretbox._RECHECK_SECONDS = recheck

    print("  a reset elsewhere while a save is under way")
    ai_keys_api.reset_limits()
    recheck, secretbox._RECHECK_SECONDS = secretbox._RECHECK_SECONDS, 1e9
    try:
        k = make_key("DuringReset")
        fp = secretbox.fingerprint(k)
        GOOGLE_HOOKS[k] = lambda: set_canary(secretbox._make_box(Fernet.generate_key()).fernet.encrypt(b"x").decode())
        r = W.B.put(k)
        check(r.status_code == 503 and jd(r).get("code") == "ai_key_storage_off" and row_of(W.B.id).fingerprint != fp,
              "the canary changed before the save could commit -> 503, nothing sealed with the old secret", brief(r))
    finally:
        set_canary(saved)
        secretbox._RECHECK_SECONDS = recheck
    check(secretbox.state() == "ok", "and the next use looks again: ok")


def lost_secret() -> None:
    print("  the volume isn't there (a missing mount, or a new server)")
    fresh = (_tmp / "secrets-new-server").resolve()
    settings.secrets_dir = str(fresh)
    rows_before, statuses_before = key_rows(), stored_statuses()
    logged = len(error_lines("is missing", "reset --confirm", "Copy the old ai-keys.secret"))
    state = restart_secretbox(create=True)
    check(state == "lost", "the api's start-up: lost (the database has keys sealed with a secret)", state)
    check(not (fresh / "ai-keys.secret").exists(), "and NO new secret is made")
    check(len(error_lines("is missing", "reset --confirm", "Copy the old ai-keys.secret")) == logged + 1,
          "logged as an error naming both fixes")
    restart_secretbox(create=True)
    check(len(error_lines("is missing", "reset --confirm")) == logged + 2, "...at every start")
    r = W.A.get()
    check(jd(r).get("status") == "unreadable" and jd(r).get("source") == "none", "Alice's key reads as unreadable", brief(r))
    ai_keys_api.reset_limits()
    r = W.B.put(make_key("WhileLost"))
    check(r.status_code == 503 and jd(r).get("code") == "ai_key_storage_off", "saving -> 503 ai_key_storage_off", brief(r))
    info = storage_info()
    check(info.get("state") == "lost" and "reset --confirm" in info.get("detail", "") and "ai-keys.secret" in info.get("detail", ""),
          "/system/info: lost, naming both fixes", str(info))
    check(restart_secretbox(create=False) == "lost" and not fresh.exists(), "the bot: lost too, and it makes nothing")
    check(key_rows() == rows_before and stored_statuses() == statuses_before, "the key rows are left exactly as they were")

    print("  putting the old file back mends everything")
    restart_secretbox(create=True)
    fresh.mkdir(parents=True, exist_ok=True)
    (fresh / "ai-keys.secret").write_bytes(W.good_secret)
    recheck, secretbox._RECHECK_SECONDS = secretbox._RECHECK_SECONDS, 0
    try:
        check(secretbox.state() == "ok", "noticed at the next use, no restart needed")
    finally:
        secretbox._RECHECK_SECONDS = recheck
    check(restart_secretbox(create=True) == "ok" and jd(W.A.get()).get("status") == "ok", "and after a restart: Alice's key is ok")

    print("  or starting over: python -m app.ai.secretbox reset")
    gone = (_tmp / "secrets-reset").resolve()
    settings.secrets_dir = str(gone)
    check(restart_secretbox(create=True) == "lost", "(another server with no secret file: lost)")
    env = dict(os.environ, SECRETS_DIR=str(gone))
    cli = [sys.executable, "-m", "app.ai.secretbox"]
    status = subprocess.run(cli + ["status"], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    check(status.returncode == 1 and "lost" in status.stdout, "`status` says lost (exit 1)", status.stdout[-200:] + status.stderr[-200:])
    canary_before, rows_before = canary_value(), key_rows()
    dry = subprocess.run(cli + ["reset"], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    check(dry.returncode == 1 and "--confirm" in dry.stdout and f"delete the {rows_before} saved" in dry.stdout,
          "`reset` without --confirm only explains (how many keys it would delete)", dry.stdout[-300:])
    check(not (gone / "ai-keys.secret").exists() and key_rows() == rows_before and canary_value() == canary_before,
          "...and changes nothing")
    done = subprocess.run(cli + ["reset", "--confirm"], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    check(done.returncode == 0 and "Done" in done.stdout, "`reset --confirm` -> exit 0", done.stdout[-300:] + done.stderr[-300:])
    check((gone / "ai-keys.secret").is_file() and key_rows() == 0, "a new secret, and every saved own key deleted", str(key_rows()))
    check(canary_value() != canary_before, "the canary is sealed again")
    entry = audits("ai_keys_reset")
    check(entry and f"{rows_before} saved own key(s) deleted" in entry[-1].detail, "a history entry says so",
          entry[-1].detail if entry else "none")
    check(not any(k in done.stdout + done.stderr + dry.stdout + status.stdout for k in SENT_KEYS), "it prints no key")
    recheck, secretbox._RECHECK_SECONDS = secretbox._RECHECK_SECONDS, 0
    try:
        check(secretbox.state() == "ok", "the running api picks the new secret up at its next use")
    finally:
        secretbox._RECHECK_SECONDS = recheck
    body = jd(W.A.get())
    check(body.get("has_key") is False and body.get("source") == "none", "Alice has no key now", str(body))
    ai_keys_api.reset_limits()
    r = W.A.put(W.A_key)
    check(r.status_code == 200 and jd(r).get("status") == "ok", "and adds hers again: ok", brief(r))
    settings.secrets_dir = str(SECRETS)
    check(restart_secretbox(create=True) == "canary_failed", "the old secret now fails the new canary (and isn't re-made)")
    check((SECRETS / "ai-keys.secret").read_bytes() == W.good_secret, "...and is left as it was")
    settings.secrets_dir = str(gone)
    restart_secretbox(create=True)


def uvicorn_logging() -> None:
    folder = (_tmp / "uvicorn").resolve()
    folder.mkdir()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, SECRETS_DIR=str(folder / "secrets"), STORAGE_DIR=str(folder),
               DATABASE_URL=f"sqlite:///{(folder / 'uv.db').as_posix()}")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.api.main:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines: list[str] = []
    import threading as _threading

    reader = _threading.Thread(target=lambda: lines.extend(iter(proc.stdout.readline, "")), daemon=True)
    reader.start()
    try:
        deadline = time.time() + 90
        while time.time() < deadline and not any("Application startup complete" in line for line in lines):
            time.sleep(0.2)
        need(any("Application startup complete" in line for line in lines), "uvicorn starts the api", "".join(lines)[-400:])
        r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=10)
        check(r.status_code == 200, "GET /health", str(r.status_code))
        time.sleep(0.5)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        reader.join(timeout=5)
    out = "".join(lines)
    check(out.count("AI key secret ready") == 1 and out.count("Made a new AI key secret") == 1,
          "the app's own INFO lines now show under uvicorn, once each", out[-600:])
    check(out.count("Application startup complete") == 1 and out.count("Uvicorn running on") == 1,
          "uvicorn's own lines aren't doubled", out[-600:])
    check(out.count('"GET /health HTTP/1.1" 200') == 1, "nor its access log line", out[-600:])


# ------------------------------------------------------------- other processes
_CHILD = r"""
import hashlib, json, logging, os, sys, time
sys.path.insert(0, os.environ["CARLINK_ROOT"])
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
mode, go = sys.argv[1], float(sys.argv[2])
from app.ai import secretbox
if mode == "init":
    from app.reports.db import init_db
    init_db()
    print(json.dumps({"ok": True}))
    sys.exit(0)
if mode == "loser":
    # Another process links its secret in between our write and our link.
    winner = sys.argv[3].encode()
    real_link = os.link
    def link(src, dst):
        with open(dst, "xb") as f:
            f.write(winner + b"\n")
        return real_link(src, dst)
    os.link = link
while time.time() < go - 0.05:
    time.sleep(0.01)
while time.time() < go:
    pass
state = secretbox.startup(create=(mode in ("api", "loser")))
out = {"state": state, "described": secretbox.describe()["state"]}
if state == "ok":
    out["fp"] = secretbox.fingerprint("race probe")
    out["sealed"] = secretbox.seal("hello from %d" % os.getpid())
    out["file"] = hashlib.sha256(secretbox.secret_path().read_bytes().strip()).hexdigest()
print(json.dumps(out))
"""


def _children(modes: list[str], folder: Path, extra: list[str] | None = None, delay: float = 4.0) -> list[tuple[dict, str]]:
    env = dict(os.environ)
    env.update(CARLINK_ROOT=str(ROOT), SECRETS_DIR=str(folder / "secrets"), STORAGE_DIR=str(folder),
               DATABASE_URL=f"sqlite:///{(folder / 'race.db').as_posix()}")
    go = str(time.time() + delay)
    procs = [subprocess.Popen([sys.executable, "-c", _CHILD, m, go] + (extra or []), cwd=str(ROOT), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for m in modes]
    results = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        try:
            results.append((json.loads(out.strip().splitlines()[-1]), err))
        except Exception:
            results.append(({"state": f"crashed: {err[-300:]}"}, err))
    return results


def racing_processes() -> None:
    for round_no in range(1, 4):
        folder = (_tmp / f"race{round_no}").resolve()
        folder.mkdir()
        _children(["init"], folder, delay=0)
        if round_no == 1:
            early = _children(["bot"], folder, delay=0)[0][0]
            check(early.get("state") == "off" and early.get("described") == "off" and not (folder / "secrets").exists(),
                  "a brand-new database, the bot first: off (waiting for the api), and it makes nothing", str(early))
        results = _children(["api"] * 4, folder)
        states = [r["state"] for r, _ in results]
        check(states == ["ok"] * 4, f"round {round_no}: 4 api processes start at the same moment, all ok", str(states))
        fps = {r.get("fp") for r, _ in results}
        files = {r.get("file") for r, _ in results}
        made = sum(err.count("Made a new AI key secret") for _, err in results)
        check(len(fps) == 1 and len(files) == 1, f"round {round_no}: all four use the same secret", f"{len(fps)} fingerprints")
        check(made == 1, f"round {round_no}: exactly one of them made it", f"{made} made one")
        secret = (folder / "secrets" / "ai-keys.secret")
        check(sorted(p.name for p in secret.parent.iterdir()) == ["ai-keys.secret"], f"round {round_no}: no temp files left")
        check(hashlib.sha256(secret.read_bytes().strip()).hexdigest() in files, f"round {round_no}: and it's the file on disk")
        reader = _children(["bot"], folder, delay=0)[0][0]
        check(reader.get("state") == "ok" and reader.get("fp") in fps, f"round {round_no}: the bot then reads the same secret",
              str(reader.get("state")))

    print("  losing the race between writing and linking")
    folder = (_tmp / "race-loser").resolve()
    folder.mkdir()
    _children(["init"], folder, delay=0)
    winner = Fernet.generate_key().decode()
    result = _children(["loser"], folder, extra=[winner], delay=0)[0]
    out, err = result
    expected = secretbox._make_box(winner.encode())
    import hmac as _hmac

    want_fp = _hmac.new(expected.mac_key, b"race probe", hashlib.sha256).hexdigest()
    check(out.get("state") == "ok" and out.get("fp") == want_fp, "it uses the winner's secret", str(out.get("state")))
    check("Made a new AI key secret" not in err, "...and doesn't claim to have made it")
    check(expected.fernet.decrypt(out.get("sealed", "").encode()).decode().startswith("hello from"),
          "what it seals opens with the winner's secret")


def nothing_leaked() -> None:
    leaks = [k for k in SENT_KEYS if any(k in line for line in LOG_LINES)]
    check(not leaks, f"none of the {len(SENT_KEYS)} keys is in any of the {len(LOG_LINES)} log lines", f"{len(leaks)} leaked")
    leaks = [k for k in SENT_KEYS if any(k in body for body in RESPONSES)]
    check(not leaks, f"none of them is in any of the {len(RESPONSES)} API responses", f"{len(leaks)} leaked")
    check(not any(body.startswith("Internal Server Error") for body in RESPONSES), "no response was a 500")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA wal_checkpoint(FULL)"))
    blobs = b"".join(p.read_bytes() for p in _tmp.iterdir() if p.is_file() and p.name.startswith("test.db"))
    leaks = [k for k in SENT_KEYS - getattr(W, "stored_plain", set()) if k.encode() in blobs]
    check(not leaks, "no own key is in the SQLite files at the end", f"{len(leaks)} found")


def main() -> int:
    for name, fn in (
        ("setup", setup),
        ("the secret file", secret_file),
        ("seal, unseal, fingerprint", seal_round_trip),
        ("user_ai_keys: unique rules inside the table", table_rules),
        ("GET /auth/me/ai-key without a key", get_without_key),
        ("PUT refusals: format, billing", put_refusals),
        ("PUT, GET, replace; never in the database file", put_and_get),
        ("one person per key: 409 ai_key_in_use", in_use),
        ("Google refuses: 400 ai_key_invalid, not stored", google_refuses),
        ("Google can't answer: untested; quota", no_answer),
        ("POST /auth/me/ai-key/test", test_endpoint),
        ("deleting: self, admin, the disable helper", deleting),
        ("disabling an account deletes its key", disabling),
        ("GET /users: ai_key, batched", users_screen),
        ("rate limits: order, exemptions, accounts with no ticks", rate_limits),
        ("company keys: never someone's own", company_keys),
        ("a save racing a disable", save_vs_disable),
        ("a re-check racing a new key", test_vs_replace),
        ("signed out, temporary password, plain user", who_may),
        ("a wrong or broken secret: unreadable, never re-made, mends", bad_secret),
        ("a lost secret: never re-made; put back, or reset", lost_secret),
        ("processes racing to make the secret", racing_processes),
        ("logging under uvicorn", uvicorn_logging),
        ("no key in any log line, response or the database", nothing_leaked),
    ):
        run_section(name, fn)
        if name == "setup" and FAILURES:
            break
    try:
        W.boot.__exit__(None, None, None)
    except Exception:
        pass
    print(f"\ntest_ai_keys: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  [{section}] {label}")
    if FAILURES:
        return 1
    print("\nALL AI KEY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
