"""Unit tests for release P4 "AI uses your own key" (app/ai/keys.py's
AiActor, AiKey, AiUnavailable, resolve_keys, ensure_ai_ready, record_result;
app/ai/structured.call_structured; the intake pipeline's threads).

    python scripts/test_ai_key_use.py

The builder's own checks, next to P4-T's independent test_own_key_ai.py:
resolution order and fallback, what a call records on an own key, the
AiUnavailable codes, and the actor reaching every thread of an intake run.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database, get_client replaced by a fake that records which key each call
used and from which thread, google.genai.Client replaced by one that fails
the test if anything builds it, and every outside connection refused. The
keys are made up; none may appear in a log line or an error.
"""
import hashlib
import json
import logging
import os
import re
import socket
import sys
import tempfile
import threading
import time
import traceback
from datetime import timedelta
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
_tmp = Path(tempfile.mkdtemp(prefix="ai-key-use-test-"))
(_tmp / "storage").mkdir()


def make_key(label: str) -> str:
    """Shaped like a Gemini key: AIza + 35 characters."""
    return ("AIzaSy" + label + "x" * 40)[:35] + hashlib.md5(label.encode()).hexdigest()[:4]


ENV_KEY = make_key("CompanyEnv")
DB_KEY = make_key("CompanyRow")
os.environ["STORAGE_DIR"] = str((_tmp / "storage").resolve())
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = ENV_KEY
os.environ["GEMINI_MIN_CALL_INTERVAL_SECONDS"] = "0"
os.environ["GEMINI_MAX_REQUESTS_PER_MINUTE"] = "100000"
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

# ---------------------------------------------------------------- offline
_real_connect = socket.socket.connect


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConnectionRefusedError(f"test is offline: refused a connection to {address!r}")
    return _real_connect(sock, address)


socket.socket.connect = _offline_connect

import google.genai as _genai  # noqa: E402
from google.genai import errors as genai_errors  # noqa: E402

SDK_BUILT: list[str] = []


class _NoGoogle:
    """Anything that gets past the fake get_client below would build a real
    client: record it, and fail any call."""

    def __init__(self, *args, api_key=None, **kwargs):
        SDK_BUILT.append(api_key or "")
        self.models = SimpleNamespace(list=lambda *a, **k: iter([]))
        self.interactions = SimpleNamespace(create=self._refuse)

    def _refuse(self, **_kw):
        raise AssertionError("a real Gemini client was used")


# The real one, for "the real SDK" below: it runs on a mock transport.
_REAL_GENAI_CLIENT = _genai.Client
_genai.Client = _NoGoogle

import app.ai.structured as structured  # noqa: E402
import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from google.genai import types as genai_types  # noqa: E402
from PIL import Image  # noqa: E402

from app.ai import client as ai_client  # noqa: E402
from app.ai import keys, secretbox  # noqa: E402
from app.ai.keys import AiActor, AiKey, AiUnavailable  # noqa: E402
from app.api import limits  # noqa: E402
from app.api.main import app  # noqa: E402
from app.auth.core import SESSION_COOKIE, create_session  # noqa: E402
from app.intake import pipeline  # noqa: E402
from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import ApiKey, IntakePhoto, IntakeSession, Report, User, UserAiKey, gen_id  # noqa: E402
from app.storage.files import tmp_dir  # noqa: E402

# ------------------------------------------------------------ the fake SDK
# Every get_client: (key value, thread name). Every Gemini call: (key
# value, model). How each key answers: key value -> fn(model, input,
# response_format) returning output text or raising.
GOT_CLIENT: list[tuple[str, str]] = []
CALLS: list[tuple[str, str]] = []
KINDS: list[str] = []  # "sort" or "draft" for each call to the intake's schemas, else "other"
ACQUIRED: list[str] = []
BEHAVIOUR: dict[str, object] = {}
_calls_lock = threading.Lock()


def answer(model, input, response_format):
    props = ((response_format or {}).get("schema") or {}).get("properties") or {}
    if "photos" in props:
        labels = [m.group(1) for part in input if part.get("type") == "text"
                  for m in [re.match(r"Photo (P\d+):", part["text"])] if m]
        return json.dumps({"photos": [
            {"label": lb, "kind": "damage", "area": "rear", "quality": "good", "shows_damage": True, "note": None}
            for lb in labels
        ]})
    if "description" in props:
        return json.dumps({"description": "Fake AI draft.", "category": ["Vehicle Collision or Damage"]})
    return json.dumps({"ok": True})


class _FakeInteractions:
    def __init__(self, key: AiKey):
        self.key = key

    def create(self, *, model, input=None, response_format=None, **_kw):
        props = ((response_format or {}).get("schema") or {}).get("properties") or {}
        with _calls_lock:
            CALLS.append((self.key.value, model))
            KINDS.append("sort" if "photos" in props else "draft" if "description" in props else "other")
        how = BEHAVIOUR.get(self.key.value, answer)
        return SimpleNamespace(output_text=how(model, input, response_format))


def fake_get_client(key):
    assert isinstance(key, AiKey), f"get_client got {type(key).__name__}"
    with _calls_lock:
        GOT_CLIENT.append((key.value, threading.current_thread().name))
    return SimpleNamespace(interactions=_FakeInteractions(key))


def fake_acquire(rate_limit_id):
    ACQUIRED.append(rate_limit_id)
    return 0.0


structured.get_client = fake_get_client
structured.acquire = fake_acquire


def google_error(code: int, status: str, message: str, details=None):
    body = {"error": {"code": code, "status": status, "message": message, "details": details or []}}
    return (genai_errors.ServerError if code >= 500 else genai_errors.ClientError)(code, body)


def refuses(key_text: str):
    """Google's answer to a wrong key -- with the key in it, to prove it's
    scrubbed before it's kept or logged."""
    def how(*_):
        raise google_error(400, "INVALID_ARGUMENT", f"API key not valid. Please pass a valid API key. ({key_text})",
                           [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}])
    return how


def forbidden(*_):
    """Google's answer for a suspended key: a 403 about the key itself."""
    raise google_error(403, "PERMISSION_DENIED", "Permission denied: Consumer 'api_key:AIza...' has been suspended.",
                       [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "CONSUMER_SUSPENDED"}])


def daily_quota(*_):
    raise google_error(429, "RESOURCE_EXHAUSTED",
                       "You exceeded your current quota. Quota exceeded for metric: generate_content_free_tier_requests, "
                       "limit: 250, model: x. Please retry in 29.5s.",
                       [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}])


def bad_request(*_):
    raise google_error(400, "INVALID_ARGUMENT", "Request contains an invalid argument.")


def busy(*_):
    raise google_error(503, "UNAVAILABLE", "The model is overloaded. Please try again later.")


# ---------------------------------------------------------------- harness
PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}
MODELS = ["model-a", "model-b", "model-c"]


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


def reset_fakes() -> None:
    GOT_CLIENT.clear()
    CALLS.clear()
    KINDS.clear()
    ACQUIRED.clear()
    BEHAVIOUR.clear()
    keys._touched.clear()


def code_of(fn) -> str | None:
    """The AiUnavailable code fn raises, or None."""
    try:
        fn()
    except AiUnavailable as exc:
        return exc.code
    return None


def call(actor: AiActor, models=None):
    return structured.call_structured(
        purpose="unit test", system_instruction="", input_parts=[{"type": "text", "text": "hi"}],
        schema={}, parse=json.loads, models=models or MODELS, actor=actor,
    )


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


def set_user(user_id: str, **fields) -> None:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        for k, v in fields.items():
            setattr(user, k, v)
        db.commit()
    finally:
        db.close()


def give_key(user_id: str, key: str, status: str = "ok") -> None:
    db = SessionLocal()
    try:
        keys.save_key(db, user_id, key, secretbox.fingerprint(key), status, None, billing_confirmed=True)
        db.commit()
    finally:
        db.close()


def drop_key(user_id: str) -> None:
    db = SessionLocal()
    try:
        db.query(UserAiKey).filter(UserAiKey.user_id == user_id).delete()
        db.commit()
    finally:
        db.close()


def row_of(user_id: str) -> UserAiKey | None:
    db = SessionLocal()
    try:
        return db.query(UserAiKey).filter(UserAiKey.user_id == user_id).first()
    finally:
        db.close()


def set_row(user_id: str, **fields) -> None:
    db = SessionLocal()
    try:
        row = db.query(UserAiKey).filter(UserAiKey.user_id == user_id).first()
        for k, v in fields.items():
            setattr(row, k, v)
        db.commit()
    finally:
        db.close()


def labels(found: list[AiKey]) -> list[str]:
    return [k.label for k in found]


W = SimpleNamespace()


# ================================================================= sections
def setup() -> None:
    W.boot = TestClient(app, base_url="https://carlink.test")
    W.boot.__enter__()  # the api's start-up: init_db, and the secret is made
    check(secretbox.state() == secretbox.OK, "the secret is ready", secretbox.state())
    db = SessionLocal()
    try:
        db.add(ApiKey(provider="gemini", key=DB_KEY, label="Settings row"))
        db.commit()
    finally:
        db.close()
    W.A_KEY, W.B_KEY, W.D_KEY = make_key("OwnAlice"), make_key("OwnBob"), make_key("OwnDora")
    W.admin = new_user("Ada", role="admin")
    W.A = new_user("Alice")  # plain, own key
    W.B = new_user("Bob", ai_company_key_allowed=True)  # ticked, own key
    W.P = new_user("Pia")  # plain, no key
    W.V = new_user("Vic", ai_company_key_allowed=True)  # ticked, no key
    W.D = new_user("Dora")  # will be disabled, own key
    give_key(W.A, W.A_KEY)
    give_key(W.B, W.B_KEY)
    give_key(W.D, W.D_KEY)
    set_user(W.D, active=False)
    W.ALL_KEYS = {ENV_KEY, DB_KEY, W.A_KEY, W.B_KEY, W.D_KEY}


def actor_and_key() -> None:
    cli, a = AiActor.cli(), AiActor.for_user(W.A, "dashboard")
    check(cli.cli is True and cli.user_id is None, "AiActor.cli() is the cli actor, with no account")
    check(a.cli is False and a.user_id == W.A and a.channel == "dashboard", "AiActor.for_user(...) is that person, not cli")
    check(AiActor(W.A, "dashboard") == a and AiActor.cli() == cli and a != cli, "actors compare by who they are")
    check(AiActor(None, "script", cli=True).cli is True, "AiActor(..., cli=True) still works")
    k = AiKey(value=W.A_KEY, fingerprint="f" * 64, source="own", label="own ..abcd")
    shown = [repr(k), str(k), f"{k}", "%s %r" % (k, k), repr([k]), repr({"key": k})]
    check(all(W.A_KEY not in s for s in shown), "AiKey's repr, str and format never show the key", repr(shown))
    check(str(k) == "own ..abcd" and repr(k) == "AiKey(own ..abcd)", "...they show its label")
    check(k.rate_limit_id == "f" * 64, "an own key's rate-limit window is named after its fingerprint")
    c = keys.company_keys()[0]
    check(c.rate_limit_id == c.value, "a company key's window is named after the key, as before P4")


def unavailable_codes() -> None:
    want = {
        "ai_key_missing": "AI is off for your account. Add your own AI key in Settings → My AI key, or ask an admin to allow the company key.",
        "ai_key_invalid": "Your AI key stopped working (Google refused it). Check it in Settings → My AI key.",
        "ai_quota_used": "Your AI key has used up its quota for now. Try again in about an hour, or check billing in Google Cloud.",
        "ai_key_unreadable": "Your AI key can't be read on this server right now. Tell an admin (Settings → Company).",
    }
    for code, message in want.items():
        exc = AiUnavailable(code)
        check(exc.code == code and exc.message == message and str(exc) == message, f"{code}: the brief's message", exc.message)
    off = AiUnavailable("ai_account_off")
    check(off.code == "ai_account_off" and off.message, "ai_account_off has a message too", off.message)
    check(isinstance(off, Exception), "AiUnavailable is an Exception")


def resolution_order() -> None:
    company = ["company #1", "company #2"]
    found = keys.company_keys()
    check([k.value for k in found] == [DB_KEY, ENV_KEY], "company keys: Settings rows first, then GEMINI_API_KEY")
    check(labels(found) == company and all(k.source == "company" for k in found), "...labelled company #1, #2")
    check(labels(keys.resolve_keys(AiActor.cli())) == company, "cli: the company keys")

    a_keys = keys.resolve_keys(AiActor.for_user(W.A, "dashboard"))
    check([k.value for k in a_keys] == [W.A_KEY] and a_keys[0].source == "own" and a_keys[0].label == f"own ..{W.A_KEY[-4:]}",
          "plain user with a working own key: only their own key", repr(a_keys))
    check(a_keys[0].fingerprint == row_of(W.A).fingerprint, "...with the row's fingerprint")
    b_keys = keys.resolve_keys(AiActor.for_user(W.B, "dashboard"))
    check([k.value for k in b_keys] == [W.B_KEY, DB_KEY, ENV_KEY], "ticked user with an own key: own first, then company", repr(b_keys))
    check(labels(keys.resolve_keys(AiActor.for_user(W.V, "dashboard"))) == company, "ticked user without a key: company keys")
    check(labels(keys.resolve_keys(AiActor.for_user(W.admin, "dashboard"))) == company, "admin without a key: company keys")
    check(code_of(lambda: keys.resolve_keys(AiActor.for_user(W.P, "dashboard"))) == "ai_key_missing",
          "plain user without a key: ai_key_missing")
    check(all(W.A_KEY not in [k.value for k in keys.resolve_keys(AiActor.for_user(u, "dashboard"))] for u in (W.B, W.V, W.admin)),
          "nobody else ever gets Alice's key")

    set_row(W.A, status="untested")
    check(labels(keys.resolve_keys(AiActor.for_user(W.A, "dashboard"))) == [f"own ..{W.A_KEY[-4:]}"], "an untested own key is used")
    for status, code in (("invalid", "ai_key_invalid"), ("quota", "ai_quota_used")):
        set_row(W.A, status=status)
        check(code_of(lambda: keys.resolve_keys(AiActor.for_user(W.A, "dashboard"))) == code,
              f"plain user whose own key is {status}: {code}, no fallback")
        set_row(W.B, status=status)
        check(labels(keys.resolve_keys(AiActor.for_user(W.B, "dashboard"))) == company,
              f"ticked user whose own key is {status}: falls back to the company keys")
    set_row(W.A, status="ok")
    set_row(W.B, status="ok")

    real_state = secretbox.state
    secretbox.state = lambda: secretbox.BROKEN
    try:
        check(code_of(lambda: keys.resolve_keys(AiActor.for_user(W.A, "dashboard"))) == "ai_key_unreadable",
              "secret storage not ok: plain user gets ai_key_unreadable")
        check(labels(keys.resolve_keys(AiActor.for_user(W.B, "dashboard"))) == company,
              "...and a ticked user the company keys")
    finally:
        secretbox.state = real_state
    good = row_of(W.A).key_enc
    set_row(W.A, key_enc="not-a-sealed-value")
    check(code_of(lambda: keys.resolve_keys(AiActor.for_user(W.A, "dashboard"))) == "ai_key_unreadable",
          "a row that won't open: ai_key_unreadable")
    set_row(W.A, key_enc=good)

    check(code_of(lambda: keys.resolve_keys(AiActor.for_user(W.D, "dashboard"))) == "ai_account_off",
          "disabled account (with a working own key): ai_account_off")
    check(code_of(lambda: keys.resolve_keys(AiActor.for_user("no-such-user", "dashboard"))) == "ai_account_off",
          "missing account: ai_account_off")
    check(code_of(lambda: keys.resolve_keys(AiActor.for_user(None, "whatsapp"))) == "ai_account_off",
          "no account at all (WhatsApp): ai_account_off")

    print("  read afresh on every call")
    set_user(W.V, ai_company_key_allowed=False)
    check(code_of(lambda: keys.resolve_keys(AiActor.for_user(W.V, "dashboard"))) == "ai_key_missing",
          "an untick applies at the next call")
    set_user(W.V, ai_company_key_allowed=True)
    drop_key(W.B)
    check(labels(keys.resolve_keys(AiActor.for_user(W.B, "dashboard"))) == company, "a removed key applies at the next call")
    give_key(W.B, W.B_KEY)

    real_env = keys.settings.gemini_api_key
    db = SessionLocal()
    try:
        db.query(ApiKey).delete()
        db.commit()
        keys.settings.gemini_api_key = ""
        check(code_of(lambda: keys.resolve_keys(AiActor.cli())) == "ai_key_missing", "cli with no company key at all: ai_key_missing")
        check(labels(keys.resolve_keys(AiActor.for_user(W.B, "dashboard"))) == [f"own ..{W.B_KEY[-4:]}"],
              "...while a ticked user's own key still works")
    finally:
        keys.settings.gemini_api_key = real_env
        db.add(ApiKey(provider="gemini", key=DB_KEY, label="Settings row"))
        db.commit()
        db.close()


def ready_and_allowed() -> None:
    for who, user_id, ok in (("admin", W.admin, True), ("ticked, no key", W.V, True), ("plain, own key", W.A, True),
                             ("plain, no key", W.P, False)):
        actor = AiActor.for_user(user_id, "dashboard")
        passed = code_of(lambda: keys.ensure_ai_ready(actor)) is None
        db = SessionLocal()
        try:
            user = db.get(User, user_id)
        finally:
            db.close()
        check(passed is ok and keys.ai_allowed(user) is ok, f"{who}: ensure_ai_ready {'passes' if ok else 'raises'}, ai_allowed {ok}")
    before = len(CALLS) + len(GOT_CLIENT)
    check(before == 0, "ensure_ai_ready made no Gemini call and built no client", f"{before}")
    db = SessionLocal()
    try:
        alice = db.get(User, W.A)
    finally:
        db.close()
    set_row(W.A, status="invalid")
    check(keys.ai_allowed(alice) is False, "a refused own key: ai_allowed false")
    set_row(W.A, status="ok")
    real_state = secretbox.state
    secretbox.state = lambda: secretbox.LOST
    try:
        check(keys.ai_allowed(alice) is False, "own keys unreadable here: ai_allowed false")
    finally:
        secretbox.state = real_state


def call_fallback() -> None:
    a, b, cli = AiActor.for_user(W.A, "dashboard"), AiActor.for_user(W.B, "dashboard"), AiActor.cli()

    reset_fakes()
    check(call(a) == {"ok": True} and CALLS == [(W.A_KEY, "model-a")], "Alice's call uses her own key", repr(len(CALLS)))
    check(ACQUIRED == [row_of(W.A).fingerprint], "...in her own rate-limit window (by fingerprint)")

    reset_fakes()
    call(cli)
    check(CALLS == [(DB_KEY, "model-a")] and ACQUIRED == [DB_KEY], "cli: company #1, in its usual window")

    print("  own key refused")
    reset_fakes()
    BEHAVIOUR[W.B_KEY] = refuses(W.B_KEY)
    check(call(b) == {"ok": True}, "a ticked user's refused key falls back to the company key")
    check(CALLS == [(W.B_KEY, "model-a"), (DB_KEY, "model-a")],
          "...after ONE call on their key (a refused key isn't tried on every model)", repr([m for _, m in CALLS]))
    row = row_of(W.B)
    check(row.status == "invalid" and row.last_checked_at is not None, "...and their key is stored as invalid", row.status)
    check(row.last_error and W.B_KEY not in row.last_error and "API key not valid" in row.last_error,
          "...with Google's words, key scrubbed out", row.last_error)
    reset_fakes()
    call(b)
    check(CALLS == [(DB_KEY, "model-a")], "the next call goes straight to the company key")
    set_row(W.B, status="ok", last_error=None)

    reset_fakes()
    BEHAVIOUR[W.A_KEY] = forbidden
    try:
        call(a)
        raised = None
    except AiUnavailable as exc:
        raised = exc
    check(raised is not None and raised.code == "ai_key_invalid", "a plain user's refused key (403): AiUnavailable ai_key_invalid",
          repr(raised))
    check(raised is not None and raised.__cause__ is None and W.A_KEY not in str(raised), "...not chained, and no key in it")
    check(len(CALLS) == 1 and row_of(W.A).status == "invalid", "...after one call, and stored as invalid")
    check(code_of(lambda: call(a)) == "ai_key_invalid" and len(CALLS) == 1, "the next call is refused before any Gemini call")
    set_row(W.A, status="ok", last_error=None)

    print("  own key out of quota")
    reset_fakes()
    BEHAVIOUR[W.A_KEY] = daily_quota
    started = time.monotonic()
    code = code_of(lambda: call(a))
    elapsed = time.monotonic() - started
    check(code == "ai_quota_used", "every model's daily quota used up: ai_quota_used", repr(code))
    check(len(CALLS) == len(MODELS) and elapsed < 5, "...after one call per model and no 30s wait", f"{len(CALLS)} calls, {elapsed:.1f}s")
    check(row_of(W.A).status == "quota", "...and stored as quota", row_of(W.A).status)
    set_row(W.A, status="ok", last_error=None)

    reset_fakes()
    BEHAVIOUR[W.B_KEY] = daily_quota
    check(call(b) == {"ok": True} and CALLS[-1] == (DB_KEY, "model-a"), "a ticked user out of quota falls back to the company key")
    check(row_of(W.B).status == "quota", "...and their key is stored as quota")
    set_row(W.B, status="ok", last_error=None)

    reset_fakes()
    BEHAVIOUR[W.A_KEY] = lambda model, *_: daily_quota() if model != "model-c" else json.dumps({"ok": True})
    check(call(a) == {"ok": True} and row_of(W.A).status == "ok", "one model with allowance left: it answers, nothing marked")

    print("  other failures behave as before")
    reset_fakes()
    tries = {"n": 0}

    def per_minute(model, *_):
        tries["n"] += 1
        if tries["n"] == 1:
            raise google_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded per minute. Please retry in 0.1s.",
                               [{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}]}])
        return json.dumps({"ok": True})

    BEHAVIOUR[W.A_KEY] = per_minute
    check(call(a) == {"ok": True} and CALLS == [(W.A_KEY, "model-a")] * 2 and row_of(W.A).status == "ok",
          "a per-minute 429 waits and retries the same model, and marks nothing")
    for name, how in (("a 400 about the request", bad_request), ("a 503", busy)):
        reset_fakes()
        BEHAVIOUR[W.A_KEY] = how
        try:
            call(a)
            outcome = "answered"
        except AiUnavailable as exc:
            outcome = exc.code
        except RuntimeError as exc:
            outcome = "RuntimeError" if "fallback models" in str(exc) else repr(exc)
        check(outcome == "RuntimeError" and len(CALLS) == len(MODELS) and row_of(W.A).status == "ok",
              f"{name} on every model: the usual RuntimeError, every model tried, key not marked", f"{outcome}, {len(CALLS)} calls")
    reset_fakes()

    def busy_naming_key(*_):
        raise google_error(503, "UNAVAILABLE", f"Overloaded, key {W.A_KEY}")

    BEHAVIOUR[W.A_KEY] = busy_naming_key
    try:
        call(a)
        cause = None
    except RuntimeError as exc:
        cause = exc.__cause__
    check(cause is not None and W.A_KEY not in str(cause) and "503" in str(cause),
          "an own key's error is chained scrubbed (it is shown and logged), keeping Google's words", repr(str(cause))[:120])
    reset_fakes()
    BEHAVIOUR[DB_KEY] = forbidden
    BEHAVIOUR[ENV_KEY] = forbidden
    try:
        call(cli)
        outcome = "answered"
    except AiUnavailable as exc:
        outcome = exc.code
    except RuntimeError:
        outcome = "RuntimeError"
    check(outcome == "RuntimeError" and len(CALLS) == 2 * len(MODELS),
          "company keys refused: every model on every key, then the usual RuntimeError (as before P4)", f"{outcome}, {len(CALLS)}")
    BEHAVIOUR.clear()
    lines = [line for line in LOG_LINES if any(k in line for k in W.ALL_KEYS)]
    check(not lines, "no key in any log line so far", lines[0][:200] if lines else "")


def recording() -> None:
    a = AiActor.for_user(W.A, "dashboard")
    own = keys.resolve_keys(a)[0]
    reset_fakes()
    set_row(W.A, last_used_at=None)

    keys.record_result(a, own, "ok")
    first = row_of(W.A).last_used_at
    check(first is not None, "a success stamps last_used_at")
    keys.record_result(a, own, "ok")
    check(row_of(W.A).last_used_at == first, "...not again within 10 minutes (this process remembers)")
    keys._touched.clear()
    set_row(W.A, last_used_at=first - timedelta(minutes=5))
    keys.record_result(a, own, "ok")
    check(row_of(W.A).last_used_at == first - timedelta(minutes=5),
          "...nor when another process stamped it 5 minutes ago (the database says so)")
    keys._touched.clear()
    set_row(W.A, last_used_at=first - timedelta(minutes=11))
    keys.record_result(a, own, "ok")
    check(row_of(W.A).last_used_at > first - timedelta(minutes=1), "after 10 minutes it is stamped again")
    check(row_of(W.A).status == "ok", "a success leaves the status alone")

    err = google_error(401, "UNAUTHENTICATED", f"Request had invalid credentials {W.A_KEY}")
    keys.record_result(a, own, "invalid", err)
    row = row_of(W.A)
    check(row.status == "invalid" and row.last_error and W.A_KEY not in row.last_error and "401" in row.last_error,
          "invalid: stored, with Google's scrubbed words", row.last_error)
    keys.record_result(a, own, "quota", google_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded PerDay"))
    check(row_of(W.A).status == "quota", "quota: stored")
    set_row(W.A, status="ok", last_error=None)

    print("  a key replaced meanwhile")
    stale = AiKey(value=make_key("OldAlice"), fingerprint="0" * 64, source="own", label="own ..old0")
    keys._touched.clear()
    set_row(W.A, last_used_at=None)
    keys.record_result(a, stale, "invalid", err)
    keys.record_result(a, stale, "ok")
    row = row_of(W.A)
    check(row.status == "ok" and row.last_used_at is None, "an old key's verdict and use never land on the new key", row.status)

    print("  company keys and others")
    company = keys.company_keys()[0]
    before = {r.user_id: (r.status, r.last_used_at) for r in _all_rows()}
    keys.record_result(AiActor.for_user(W.V, "dashboard"), company, "invalid", err)
    keys.record_result(AiActor.cli(), company, "ok")
    check({r.user_id: (r.status, r.last_used_at) for r in _all_rows()} == before, "a company key's result changes no row")
    b_key = keys.resolve_keys(AiActor.for_user(W.B, "dashboard"))[0]
    keys.record_result(a, b_key, "invalid", err)
    check(row_of(W.B).status == "ok", "a result is only kept on the actor's own row")
    keys.record_result(a, own, "something else")
    check(row_of(W.A).status == "ok", "an unknown outcome changes nothing")

    real = keys.record_check

    def broken(*_a, **_k):
        raise RuntimeError("database is locked")

    keys.record_check = broken
    try:
        keys.record_result(a, own, "invalid", err)
        check(True, "a failed write doesn't raise into the AI call")
    except Exception as exc:
        check(False, "a failed write doesn't raise into the AI call", repr(exc))
    finally:
        keys.record_check = real


def _all_rows() -> list[UserAiKey]:
    db = SessionLocal()
    try:
        return db.query(UserAiKey).all()
    finally:
        db.close()


def quota_recovery() -> None:
    """A key out of quota is tried again an hour after it was marked, with
    the clock moved on (keys._clock), not by sleeping."""
    a, b = AiActor.for_user(W.A, "dashboard"), AiActor.for_user(W.B, "dashboard")
    own = keys.resolve_keys(a)[0]
    real_clock = keys._clock
    later = lambda minutes: (lambda: real_clock() + timedelta(minutes=minutes))  # noqa: E731
    alice_web = signed_in(W.A)

    def web() -> tuple:
        me = alice_web.get("/auth/me").json()
        mine = alice_web.get("/auth/me/ai-key").json()
        return me.get("ai_allowed"), mine.get("ai_available"), mine.get("source"), mine.get("status")

    try:
        keys.record_result(a, own, "quota", google_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded PerDay"))
        marked = row_of(W.A).last_checked_at
        check(row_of(W.A).status == "quota" and marked is not None, "her key is marked quota (last_checked_at set)")
        check(code_of(lambda: keys.resolve_keys(a)) == "ai_quota_used", "just marked: ai_quota_used")
        check(web() == (False, False, "none", "quota"), "...and /auth/me ai_allowed, /auth/me/ai-key ai_available say no", repr(web()))

        keys._clock = later(59)
        check(code_of(lambda: keys.resolve_keys(a)) == "ai_quota_used", "59 minutes on: still left out")
        check(web()[:2] == (False, False), "...and still no AI on /auth/me and /auth/me/ai-key", repr(web()))

        keys._clock = later(61)
        found = keys.resolve_keys(a)
        check([k.value for k in found] == [W.A_KEY] and found[0].retrying, "61 minutes on: her key is tried again (retrying)",
              repr(found))
        check(web() == (True, True, "own", "quota"),
              ".../auth/me ai_allowed and /auth/me/ai-key ai_available follow (status still shows quota)", repr(web()))
        check(keys.ai_key_status(SessionLocal(), SimpleNamespace(id=W.A, role="user", ai_company_key_allowed=False))
              ["ai_available"] is True, "ai_key_status agrees")

        print("  still out of quota: marked again, for another hour")
        reset_fakes()
        BEHAVIOUR[W.A_KEY] = daily_quota
        check(code_of(lambda: call(a)) == "ai_quota_used" and len(CALLS) == len(MODELS),
              "the retry hits the daily quota on every model: ai_quota_used", f"{len(CALLS)} calls")
        again = row_of(W.A).last_checked_at
        check(row_of(W.A).status == "quota" and again >= marked, "...marked quota again, from now", f"{marked} -> {again}")
        keys._clock = lambda: keys.as_utc(again) + timedelta(minutes=30)
        check(code_of(lambda: keys.resolve_keys(a)) == "ai_quota_used", "...so 30 minutes after that, left out again")

        print("  it works again: back to ok")
        keys._clock = lambda: keys.as_utc(again) + timedelta(minutes=61)
        reset_fakes()
        check(call(a) == {"ok": True} and CALLS == [(W.A_KEY, "model-a")], "an hour on, the retry answers on her key")
        row = row_of(W.A)
        check(row.status == "ok" and row.last_error is None and row.last_checked_at >= again,
              "...and her key is back to ok, the error cleared", f"{row.status} {row.last_error!r}")
        keys._clock = real_clock
        check(keys.resolve_keys(a)[0].retrying is False and web()[:3] == (True, True, "own"), "...used as normal from then on")

        print("  a verdict that came in meanwhile stands")
        keys.record_result(a, own, "quota", google_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded PerDay"))
        keys._clock = later(61)
        retrying = keys.resolve_keys(a)[0]
        set_row(W.A, status="invalid")
        keys.record_result(a, retrying, "ok")
        check(row_of(W.A).status == "invalid", "a success on the retried key doesn't undo an 'invalid' recorded since")
        set_row(W.A, status="ok", last_error=None)

        print("  a ticked user")
        b_own = keys.resolve_keys(b)[0]
        keys._clock = real_clock
        keys.record_result(b, b_own, "quota", google_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded PerDay"))
        check(labels(keys.resolve_keys(b)) == ["company #1", "company #2"], "within the hour: the company keys only")
        keys._clock = later(61)
        check(labels(keys.resolve_keys(b)) == [f"own ..{W.B_KEY[-4:]}", "company #1", "company #2"],
              "an hour on: their own key first again, then the company's")
    finally:
        keys._clock = real_clock
        set_row(W.A, status="ok", last_error=None)
        set_row(W.B, status="ok", last_error=None)
        BEHAVIOUR.clear()


# ------------------------------------------------------- the real SDK
# google-genai 2.22.0's client.interactions raises its own error family
# (BadRequestError, RateLimitError... with .status_code), not
# google.genai.errors.APIError, and retried 429/5xx once by itself. The
# fakes above raise the other family; this drives the REAL genai.Client --
# built by the app's own get_client, with its own retry options -- over an
# httpx MockTransport answering in Google's shapes.
SDK_REQUESTS: list[tuple[str, str]] = []  # (key, model) of every HTTP request
SDK_ANSWER: dict[str, object] = {}  # key -> fn(model, n) -> httpx.Response; n counts this key's requests
_sdk_lock = threading.Lock()


def _google_json(code: int, status: str, message: str, details=None) -> dict:
    error = {"code": code, "status": status, "message": message}
    if details:
        error["details"] = details
    return {"error": error}


def sdk_ok(model, n=0):
    return httpx.Response(200, json={"id": "int-1", "status": "completed", "model": model, "steps": [
        {"type": "model_output", "content": [{"type": "text", "text": json.dumps({"ok": True})}]}]})


def sdk_error(code, status, message, details=None, headers=None):
    return lambda model, n=0: httpx.Response(code, json=_google_json(code, status, message, details), headers=headers)


def sdk_quota(per: str, limit: int, delay: str):
    return sdk_error(429, "RESOURCE_EXHAUSTED",
                     "You exceeded your current quota, please check your plan and billing details. For more information "
                     "on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits.\n* Quota exceeded for "
                     f"metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: {limit}, "
                     f"model: gemini-3-flash\nPlease retry in {delay}.",
                     [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{
                         "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                         "quotaId": f"GenerateRequestsPer{per}PerProjectPerModel-FreeTier"}]},
                      {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}])


def _sdk_shapes():
    return {
        "400 API_KEY_INVALID": sdk_error(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.",
                                         [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID",
                                           "domain": "googleapis.com"}]),
        "401": sdk_error(401, "UNAUTHENTICATED", "Request had invalid authentication credentials. Expected OAuth 2 access "
                                                 "token, login cookie or other valid authentication credential."),
        "403 CONSUMER_SUSPENDED": sdk_error(403, "PERMISSION_DENIED",
                                            f"Permission denied: Consumer 'api_key:{W.A_KEY}' has been suspended.",
                                            [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "CONSUMER_SUSPENDED"}]),
        "403 API_KEY_SERVICE_BLOCKED": sdk_error(403, "PERMISSION_DENIED",
                                                 "Requests to this API generativelanguage.googleapis.com method "
                                                 "google.ai.generativelanguage.v1beta.InteractionService.CreateInteraction "
                                                 "are blocked.",
                                                 [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                                   "reason": "API_KEY_SERVICE_BLOCKED"}]),
        "403 one model": sdk_error(403, "PERMISSION_DENIED",
                                   "Permission denied on resource 'models/gemini-x' (or it may not exist)."),
        "400 bad request": sdk_error(400, "INVALID_ARGUMENT", "Request contains an invalid argument."),
        "429 per day": sdk_quota("Day", 250, "70210.5s"),
        "429 limit 0": sdk_quota("Day", 0, "30s"),
        "429 per minute": sdk_quota("Minute", 10, "0.2s"),
        "503": sdk_error(503, "UNAVAILABLE", "The model is overloaded. Please try again later."),
    }


def _sdk_handler(request: httpx.Request) -> httpx.Response:
    key = request.headers.get("x-goog-api-key", "")
    model = json.loads(request.content or b"{}").get("model", "")
    with _sdk_lock:
        n = sum(1 for k, _ in SDK_REQUESTS if k == key)
        SDK_REQUESTS.append((key, model))
    return SDK_ANSWER.get(key, sdk_ok)(model, n)


def sdk_raise(shape) -> Exception:
    """The exception the real client raises for one of Google's answers."""
    client = _REAL_GENAI_CLIENT(api_key=make_key("ShapeProbe"), http_options=genai_types.HttpOptions(
        retry_options=ai_client._HTTP_OPTIONS.retry_options,
        httpx_client=httpx.Client(transport=httpx.MockTransport(lambda req: shape("gemini-x")))))
    try:
        client.interactions.create(model="gemini-x", input=[{"type": "text", "text": "x"}], timeout=5)
    except Exception as exc:
        return exc
    raise AssertionError("expected an error")


def real_sdk() -> None:
    a, b, cli = AiActor.for_user(W.A, "dashboard"), AiActor.for_user(W.B, "dashboard"), AiActor.cli()
    shapes = _sdk_shapes()
    saved = (ai_client.genai.Client, ai_client._HTTP_OPTIONS, structured.get_client)
    ai_client.genai.Client = _REAL_GENAI_CLIENT
    ai_client._HTTP_OPTIONS = genai_types.HttpOptions(
        retry_options=saved[1].retry_options, httpx_client=httpx.Client(transport=httpx.MockTransport(_sdk_handler)))
    ai_client.reset_client_cache()
    structured.get_client = ai_client.get_client

    def fresh(*rows):
        reset_fakes()
        SDK_REQUESTS.clear()
        SDK_ANSWER.clear()
        for user_id in rows:
            set_row(user_id, status="ok", last_error=None, last_checked_at=None)

    def outcome(fn):
        started = time.monotonic()
        try:
            got = fn()
        except AiUnavailable as exc:
            got = exc.code
        except RuntimeError as exc:
            got = "RuntimeError" if "fallback models" in str(exc) else repr(exc)
        return got, time.monotonic() - started

    try:
        print("  what the real SDK raises")
        for name, status, verdict in (("400 API_KEY_INVALID", 400, "invalid"), ("401", 401, "invalid"),
                                      ("403 CONSUMER_SUSPENDED", 403, "invalid"), ("403 API_KEY_SERVICE_BLOCKED", 403, "invalid"),
                                      ("403 one model", 403, None), ("400 bad request", 400, None),
                                      ("429 per day", 429, "quota"), ("429 limit 0", 429, "no_allowance"),
                                      ("429 per minute", 429, None), ("503", 503, None)):
            exc = sdk_raise(shapes[name])
            check(keys.http_status(exc) == status and keys.refusal(exc) == verdict,
                  f"{name}: {type(exc).__name__} (not errors.APIError: {not isinstance(exc, genai_errors.APIError)}) "
                  f"-> status {status}, verdict {verdict}", f"{keys.http_status(exc)} {keys.refusal(exc)!r}")
        said = keys._google_said(sdk_raise(shapes["400 API_KEY_INVALID"]))
        check(said == "Google said: 400 API key not valid. Please pass a valid API key.", "...and Google's own words are kept", said)

        print("  through call_structured, on a real client")
        fresh(W.A)
        got, _ = outcome(lambda: call(a))
        check(got == {"ok": True} and SDK_REQUESTS == [(W.A_KEY, "model-a")], "success: her key, one request", repr(SDK_REQUESTS))
        check(ai_client._clients and len(SDK_REQUESTS) == 1, "...through the app's own get_client")

        for name, code in (("400 API_KEY_INVALID", "ai_key_invalid"), ("401", "ai_key_invalid"),
                           ("403 CONSUMER_SUSPENDED", "ai_key_invalid"), ("403 API_KEY_SERVICE_BLOCKED", "ai_key_invalid")):
            fresh(W.A)
            SDK_ANSWER[W.A_KEY] = shapes[name]
            got, _ = outcome(lambda: call(a))
            row = row_of(W.A)
            check(got == code and len(SDK_REQUESTS) == 1 and row.status == "invalid",
                  f"{name}: {code}, after ONE request, key stored as invalid", f"{got} {len(SDK_REQUESTS)} {row.status}")
            check(row.last_error and row.last_error.startswith("Google said: ") and W.A_KEY not in row.last_error,
                  "...with Google's words, scrubbed", row.last_error)

        fresh(W.A)
        SDK_ANSWER[W.A_KEY] = lambda model, n=0: shapes["403 one model"](model) if model == "model-a" else sdk_ok(model)
        got, _ = outcome(lambda: call(a))
        check(got == {"ok": True} and len(SDK_REQUESTS) == 2 and row_of(W.A).status == "ok",
              "a 403 for one model: the next model answers, nothing marked", f"{got} {len(SDK_REQUESTS)}")
        fresh(W.A)
        SDK_ANSWER[W.A_KEY] = shapes["403 one model"]
        got, _ = outcome(lambda: call(a))
        check(got == "RuntimeError" and len(SDK_REQUESTS) == len(MODELS) and row_of(W.A).status == "ok",
              "a 403 for every model: the usual RuntimeError, key not marked", f"{got} {len(SDK_REQUESTS)}")

        fresh(W.A)
        SDK_ANSWER[W.A_KEY] = shapes["429 per day"]
        got, took = outcome(lambda: call(a))
        check(got == "ai_quota_used" and row_of(W.A).status == "quota", "429 per day on every model: ai_quota_used, stored quota",
              repr(got))
        check(len(SDK_REQUESTS) == len(MODELS) and took < 5,
              "...ONE request per model: no SDK retry, no 30s wait (Google said 70210s)", f"{len(SDK_REQUESTS)} in {took:.1f}s")

        fresh(W.A)
        SDK_ANSWER[W.A_KEY] = shapes["429 limit 0"]
        got, took = outcome(lambda: call(a))
        row = row_of(W.A)
        check(got == "ai_key_invalid" and row.status == "invalid" and row.last_error == keys.NO_ALLOWANCE_ERROR,
              "'limit: 0' on every model (no billing): ai_key_invalid, stored invalid, 'turn on billing'", f"{got} {row.last_error!r}")
        check(len(SDK_REQUESTS) == len(MODELS) and took < 5, "...one request per model, no wait", f"{len(SDK_REQUESTS)} in {took:.1f}s")
        fresh(W.A)
        SDK_ANSWER[W.A_KEY] = lambda model, n=0: (shapes["429 per day"] if model == "model-c" else shapes["429 limit 0"])(model)
        got, _ = outcome(lambda: call(a))
        check(got == "ai_quota_used" and row_of(W.A).status == "quota",
              "'limit: 0' on some models, the day's allowance spent on the rest: quota", repr(got))

        fresh(W.A)
        SDK_ANSWER[W.A_KEY] = lambda model, n=0: shapes["429 per minute"](model) if n == 0 else sdk_ok(model)
        got, took = outcome(lambda: call(a))
        check(got == {"ok": True} and SDK_REQUESTS == [(W.A_KEY, "model-a")] * 2 and row_of(W.A).status == "ok",
              "429 per minute: our one wait, the same model again, nothing marked (2 requests, none from the SDK)",
              f"{got} {SDK_REQUESTS}")

        fresh(W.A)
        SDK_ANSWER[W.A_KEY] = shapes["503"]
        got, _ = outcome(lambda: call(a))
        check(got == "RuntimeError" and len(SDK_REQUESTS) == len(MODELS) and row_of(W.A).status == "ok",
              "503 on every model: the usual RuntimeError, one request per model (the SDK retried 503s before)",
              f"{got} {len(SDK_REQUESTS)}")

        print("  a ticked user falls back")
        fresh(W.B)
        SDK_ANSWER[W.B_KEY] = shapes["400 API_KEY_INVALID"]
        got, _ = outcome(lambda: call(b))
        check(got == {"ok": True} and SDK_REQUESTS == [(W.B_KEY, "model-a"), (DB_KEY, "model-a")] and row_of(W.B).status == "invalid",
              "own key refused: one request on it, then company #1 answers; their key stored invalid",
              repr([(k[-4:], m) for k, m in SDK_REQUESTS]))
        fresh(W.B)
        SDK_ANSWER[W.B_KEY] = shapes["429 per day"]
        got, _ = outcome(lambda: call(b))
        check(got == {"ok": True} and len(SDK_REQUESTS) == len(MODELS) + 1 and SDK_REQUESTS[-1][0] == DB_KEY
              and row_of(W.B).status == "quota", "own key out of quota: one request per model on it, then the company key",
              repr([(k[-4:], m) for k, m in SDK_REQUESTS]))

        print("  a company key's error never reaches a traceback")
        fresh()
        SDK_ANSWER[DB_KEY] = sdk_error(403, "PERMISSION_DENIED", f"Permission denied: Consumer 'api_key:{DB_KEY}' has been suspended.",
                                       [{"reason": "CONSUMER_SUSPENDED"}])
        SDK_ANSWER[ENV_KEY] = sdk_error(503, "UNAVAILABLE", f"Overloaded for {ENV_KEY}")
        try:
            call(cli)
            cause = None
        except RuntimeError as exc:
            cause = exc.__cause__
        check(cause is not None and DB_KEY not in str(cause) and ENV_KEY not in str(cause) and "503" in str(cause),
              "the RuntimeError's cause is a scrubbed copy, company keys too", repr(str(cause))[:150])
        check(len(SDK_REQUESTS) == 2 * len(MODELS), "company keys: every model on each, as before P4", str(len(SDK_REQUESTS)))

        print("  an intake run on a real client")
        fresh(W.A)
        SDK_ANSWER[W.A_KEY] = shapes["403 CONSUMER_SUSPENDED"]
        rid = new_draft(W.A, photos=3)
        s = run_intake(rid, a)
        check(s.stage == "failed" and s.error_code == "ai_key_invalid" and s.error_by == W.A,
              "Google suspends her key: the run fails with error_code ai_key_invalid, error_by her", f"{s.stage} {s.error_code}")
        check(len(SDK_REQUESTS) == 1 and row_of(W.A).status == "invalid", "...after one request", str(len(SDK_REQUESTS)))
    finally:
        ai_client.genai.Client, ai_client._HTTP_OPTIONS, structured.get_client = saved
        ai_client.reset_client_cache()
        set_row(W.A, status="ok", last_error=None)
        set_row(W.B, status="ok", last_error=None)
        SDK_ANSWER.clear()


def one_at_a_time() -> None:
    """One AI request in flight per account: analyze-photos, Annex A "Ask
    AI" and an intake run (for as long as it runs) -> 429 ai_busy."""
    a_web, v_web = signed_in(W.A), signed_in(W.V)
    a_case, v_case = new_case(W.A), new_case(W.V)
    rid = new_draft(W.A, photos=2)
    gate, inside = threading.Event(), threading.Event()

    def held(model, input, response_format):
        inside.set()
        gate.wait(20)
        return answer(model, input, response_format)

    def busy_checks(what: str) -> None:
        r = a_web.post("/reports/analyze-photos", data={"description": "x"})
        check(r.status_code == 429 and r.json() == {"detail": limits.AI_BUSY, "code": "ai_busy"},
              f"while {what}: analyze-photos -> 429 ai_busy, plain detail", r.text[:200])
        r = a_web.post(f"/reports/{a_case}/annex-suggestions/ai", json={"parts": ["Rear Bumper"]})
        check(r.status_code == 429 and r.json().get("code") == "ai_busy", f"...Annex A 'Ask AI' -> 429 ai_busy", r.text[:200])
        r = a_web.post(f"/intake/{rid}/process", json={"mode": "all"})
        check(r.status_code == 429 and r.json().get("code") == "ai_busy" and session_of(rid).stage in ("uploading", "ready"),
              "...intake process -> 429 ai_busy, the stage untouched", r.text[:200])
        r = v_web.post(f"/reports/{v_case}/annex-suggestions/ai", json={"parts": ["Rear Bumper"]})
        check(r.status_code == 200, "...while someone else's AI runs as usual", r.text[:200])

    try:
        reset_fakes()
        BEHAVIOUR[W.A_KEY] = held
        box = {}
        t = threading.Thread(target=lambda: box.update(r=a_web.post("/reports/analyze-photos", data={"description": "x"})))
        t.start()
        check(inside.wait(10), "(her analyze-photos is waiting on Gemini)")
        busy_checks("her analyze-photos runs")
        gate.set()
        t.join(30)
        check(box.get("r") is not None and box["r"].status_code == 200, "it finishes", str(getattr(box.get("r"), "status_code", None)))
        r = a_web.post(f"/reports/{a_case}/annex-suggestions/ai", json={"parts": ["Rear Bumper"]})
        check(r.status_code == 200, "then her next request runs", r.text[:200])

        print("  an intake run holds it until it ends")
        gate.clear()
        inside.clear()
        other = new_draft(W.A, photos=2)
        r = a_web.post(f"/intake/{other}/process", json={"mode": "all"})
        check(r.status_code == 200 and inside.wait(10), "her intake run starts", r.text[:200])
        busy_checks("her intake run runs")
        gate.set()
        deadline = time.monotonic() + 30
        while pipeline.is_running(other) and time.monotonic() < deadline:
            time.sleep(0.05)
        r = a_web.post("/reports/analyze-photos", data={"description": "x"})
        check(session_of(other).stage == "ready" and r.status_code == 200, "when it ends, she can ask again", r.text[:200])
    finally:
        gate.set()
        BEHAVIOUR.clear()


def analyze_keeps_nothing() -> None:
    """analyze-photos: photos count towards the hourly limit (unvetted
    accounts), and go when the AI fails."""
    a_web = signed_in(W.A)
    prefix = f"u{W.A}-"

    def mine():
        return sorted(p.name for p in tmp_dir().iterdir() if p.name.startswith(prefix))

    def photos(n):
        return [("photos", (f"IMG_{i}.jpg", jpeg(), "image/jpeg")) for i in range(n)]

    limits.photo_uploads.reset()
    before = mine()
    reset_fakes()
    BEHAVIOUR[W.A_KEY] = busy
    r = a_web.post("/reports/analyze-photos", data={"description": "x"}, files=photos(2))
    check(r.status_code == 502 and W.A_KEY not in r.text, "the AI fails -> 502", r.text[:200])
    check(mine() == before, "...and the 2 photos it saved are removed")
    check(len(limits.photo_uploads._times.get(W.A, ())) == 0, "...and given back to her hourly count")
    BEHAVIOUR[W.A_KEY] = refuses(W.A_KEY)
    r = a_web.post("/reports/analyze-photos", data={"description": "x"}, files=photos(2))
    check(r.status_code == 409 and r.json().get("code") == "ai_key_invalid" and mine() == before,
          "her key refused mid-call -> 409 ai_key_invalid, photos removed too", r.text[:200])
    set_row(W.A, status="ok", last_error=None)
    BEHAVIOUR.clear()
    r = a_web.post("/reports/analyze-photos", data={"description": "x"}, files=photos(2))
    kept = r.json().get("temp_photo_paths") or []
    check(r.status_code == 200 and len(kept) == 2 and len(limits.photo_uploads._times.get(W.A, ())) == 2,
          "it works: 2 photos kept, counted for her (an unvetted account)", r.text[:200])
    limits.photo_uploads.take(W.A, limits.PHOTOS_PER_HOUR - 3)
    reset_fakes()
    r = a_web.post("/reports/analyze-photos", data={"description": "x"}, files=photos(2))
    check(r.status_code == 429 and r.json().get("code") == "rate_limited" and not CALLS,
          "past 60 photos an hour (shared with upload-photos) -> 429 rate_limited, no Gemini call", r.text[:200])
    r = a_web.post("/reports/analyze-photos", data={"description": "x"},
                   files=photos(1) + [("photos", ("notes.pdf", b"%PDF-1.4", "application/pdf"))])
    check(r.status_code == 400 and not CALLS, "a .pdf among them -> 400 before anything is saved", r.text[:200])
    for p in kept:
        Path(p).unlink(missing_ok=True)
    limits.photo_uploads.reset()


def client_cache() -> None:
    built: list[str] = []

    class Counting:
        def __init__(self, *args, api_key=None, **kwargs):
            built.append(api_key)

    real = ai_client.genai.Client
    ai_client.genai.Client = Counting
    ai_client.reset_client_cache()
    try:
        k1 = AiKey(value=W.A_KEY, fingerprint="1" * 64, source="own", label="own ..1")
        same = AiKey(value=W.A_KEY, fingerprint="1" * 64, source="own", label="own ..1")
        k2 = AiKey(value=W.B_KEY, fingerprint="2" * 64, source="own", label="own ..2")
        c1 = ai_client.get_client(k1)
        check(ai_client.get_client(same) is c1 and built == [W.A_KEY], "one client per fingerprint")
        check(ai_client.get_client(k2) is not c1 and built == [W.A_KEY, W.B_KEY], "another key, another client")
        check(all(W.A_KEY not in fp for fp in ai_client._clients), "the cache is keyed by fingerprint, not the key")
        for bad in (None, AiKey(value="", fingerprint="3" * 64, source="own", label="x")):
            try:
                ai_client.get_client(bad)
                check(False, f"get_client({bad!r}) refuses")
            except ValueError as exc:
                check(len(built) == 2 and W.A_KEY not in str(exc), f"get_client({bad!r}) refuses, building nothing")
        for n in range(ai_client.MAX_CLIENTS + 5):
            ai_client.get_client(AiKey(value=f"k{n}", fingerprint=f"{n:064d}", source="company", label=f"company #{n}"))
        check(len(ai_client._clients) == ai_client.MAX_CLIENTS, "the cache stays at MAX_CLIENTS", str(len(ai_client._clients)))
    finally:
        ai_client.genai.Client = real
        ai_client.reset_client_cache()


# --------------------------------------------------------- intake threads
def jpeg() -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (90, 40, 200)).save(buf, "JPEG")
    return buf.getvalue()


def new_case(owner_id: str) -> str:
    """A filed case (status "confirmed"), for Annex A."""
    from app.reports.schema import SecurityIncidentDraft

    db = SessionLocal()
    try:
        data = SecurityIncidentDraft(description="Rear-ended.", category=["Vehicle Collision or Damage"]).model_dump()
        report = Report(channel="manual", reporter_chat_id="dashboard", status="confirmed", owner_id=owner_id, data=data)
        db.add(report)
        db.commit()
        return report.id
    finally:
        db.close()


def new_draft(owner_id: str, photos: int) -> str:
    db = SessionLocal()
    try:
        report = Report(channel="manual", reporter_chat_id="dashboard", status="intake", owner_id=owner_id, data={})
        db.add(report)
        db.flush()
        db.add(IntakeSession(report_id=report.id))
        folder = Path(os.environ["STORAGE_DIR"]) / "intake" / report.id
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(photos):
            path = folder / f"p{i:03d}.jpg"
            Image.new("RGB", (32, 24), (i * 7 % 255, 40, 90)).save(path, "JPEG")
            db.add(IntakePhoto(id=gen_id(), report_id=report.id, position=i, original_name=path.name, path=str(path),
                               sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size_bytes=path.stat().st_size))
        db.commit()
        return report.id
    finally:
        db.close()


def session_of(report_id: str) -> IntakeSession:
    db = SessionLocal()
    try:
        return db.get(IntakeSession, report_id)
    finally:
        db.close()


def sorted_count(report_id: str) -> int:
    db = SessionLocal()
    try:
        return db.query(IntakePhoto).filter(IntakePhoto.report_id == report_id, IntakePhoto.kind.isnot(None)).count()
    finally:
        db.close()


def run_intake(report_id: str, actor: AiActor, mode: str = "all") -> IntakeSession:
    check(pipeline.reserve(report_id), "the run is reserved")
    pipeline.start_reserved(report_id, mode, actor)
    deadline = time.monotonic() + 60
    while pipeline.is_running(report_id) and time.monotonic() < deadline:
        time.sleep(0.05)
    return session_of(report_id)


def pool_threads() -> None:
    a = AiActor.for_user(W.A, "dashboard")
    rid = new_draft(W.A, photos=30)  # 3 batches of 12, 12, 6
    reset_fakes()
    s = run_intake(rid, a)
    check(s.stage == "ready" and s.error_code is None, "Alice's run finishes", f"{s.stage} {s.error}")
    threads = [name for _, name in GOT_CLIENT]
    sort_threads = [n for n in threads if n.startswith(f"sort-{rid}")]
    check(len(GOT_CLIENT) == 4 and len(sort_threads) == 3, "3 sorting calls from pool workers + 1 reading call", repr(threads))
    check(all(value == W.A_KEY for value, _ in GOT_CLIENT) and all(value == W.A_KEY for value, _ in CALLS),
          "EVERY call -- each sort-pool worker and the reading step -- used Alice's own key")
    check(f"intake-{rid}" in threads, "the reading step ran on the run's own thread with her key", repr(threads))
    check(len(set(sort_threads)) >= 2, "...and the sorting really ran on several pool threads", repr(sorted(set(sort_threads))))
    check(sorted_count(rid) == 30, "every photo sorted")

    print("  someone else presses Retry")
    reset_fakes()
    db = SessionLocal()
    try:
        db.query(IntakePhoto).filter(IntakePhoto.report_id == rid).update({"kind": None})
        db.commit()
    finally:
        db.close()
    s = run_intake(rid, AiActor.for_user(W.admin, "dashboard"))
    check(s.stage == "ready" and {v for v, _ in GOT_CLIENT} == {DB_KEY},
          "an admin's run on Alice's draft uses the admin's resolution (company key), never Alice's", repr({v[-4:] for v, _ in GOT_CLIENT}))

    print("  her key refused mid-run")
    rid = new_draft(W.A, photos=30)
    reset_fakes()
    batch_one_answered = threading.Event()

    def mid_run(model, input, response_format):
        texts = [p.get("text", "") for p in input if p.get("type") == "text"]
        if any("Photo P01:" in t for t in texts):
            out = answer(model, input, response_format)
            batch_one_answered.set()
            return out
        batch_one_answered.wait(10)  # the first batch has answered first
        raise google_error(403, "PERMISSION_DENIED", f"Permission denied: Consumer 'api_key:{W.A_KEY}' has been suspended.",
                           [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "CONSUMER_SUSPENDED"}])

    BEHAVIOUR[W.A_KEY] = mid_run
    s = run_intake(rid, a)
    check(s.stage == "failed" and s.error_code == "ai_key_invalid", "the run stops with error_code ai_key_invalid",
          f"{s.stage} {s.error_code} {s.error}")
    check(s.error == keys.MESSAGES["ai_key_invalid"], "...and the plain-English message as the error", s.error)
    check(row_of(W.A).status == "invalid", "her key is stored as invalid")
    check(sorted_count(rid) >= 12, "photos already sorted are kept", str(sorted_count(rid)))
    check(all(v == W.A_KEY for v, _ in CALLS), "only her key was ever called")
    check("draft" not in KINDS, "the reading step never ran", repr(KINDS))

    print("  the code is cleared on the next start")
    set_row(W.A, status="ok", last_error=None)
    BEHAVIOUR.clear()
    c = signed_in(W.A)
    r = c.get(f"/intake/{rid}")
    body = r.json() if r.status_code == 200 else {}
    check(body.get("error_code") == "ai_key_invalid", "GET /intake/{id} shows error_code", r.text[:200])
    db = SessionLocal()
    try:
        alice = db.get(User, W.A)
        want = {"id": W.A, "name": alice.name, "username": alice.username}
    finally:
        db.close()
    check(body.get("error_by") == want and body.get("error_mine") is True,
          "...error_by {id, name, username} of who started the run, error_mine true for her", repr(body.get("error_by")))
    r = signed_in(W.admin).get(f"/intake/{rid}")
    check(r.status_code == 200 and r.json().get("error_by") == want and r.json().get("error_mine") is False,
          "...the admin sees the same error_by, error_mine false", r.text[:200])
    r = c.post(f"/intake/{rid}/process", json={"mode": "all"})
    check(r.status_code == 200 and r.json().get("error_code") is None and r.json().get("error_by") is None
          and r.json().get("error_mine") is False, "POST /process clears error_code and error_by", r.text[:200])
    deadline = time.monotonic() + 60
    while pipeline.is_running(rid) and time.monotonic() < deadline:
        time.sleep(0.05)
    check(session_of(rid).stage == "ready" and sorted_count(rid) == 30, "...and Retry sorts what was left")


def signed_in(user_id: str) -> TestClient:
    c = TestClient(app, base_url="https://carlink.test")
    db = SessionLocal()
    try:
        token = create_session(db, db.get(User, user_id))
        db.commit()
    finally:
        db.close()
    c.cookies.set(SESSION_COOKIE, token)
    return c


def routes() -> None:
    reset_fakes()
    p = signed_in(W.P)
    rid = new_draft(W.P, photos=2)
    r = p.post(f"/intake/{rid}/process", json={"mode": "all"})
    check(r.status_code == 409 and r.json() == {"detail": keys.MESSAGES["ai_key_missing"], "code": "ai_key_missing"},
          "plain user with no key: POST /process -> 409 {detail, code}", r.text[:200])
    check(session_of(rid).stage == "uploading" and not CALLS, "...the stage unchanged, no Gemini call")
    empty = new_draft(W.P, photos=0)
    r = p.post(f"/intake/{empty}/process", json={"mode": "all"})
    check(r.status_code == 400, "nothing to read is still 400, asked before the AI check", str(r.status_code))
    me = p.get("/auth/me").json()
    check(me.get("ai_allowed") is False, "GET /auth/me ai_allowed false for them")
    give_key(W.P, make_key("OwnPia"))
    check(p.get("/auth/me").json().get("ai_allowed") is True, "...true once they save a working own key")
    r = p.post(f"/intake/{rid}/process", json={"mode": "all"})
    check(r.status_code == 200, "...and AI runs for them", r.text[:200])
    deadline = time.monotonic() + 60
    while pipeline.is_running(rid) and time.monotonic() < deadline:
        time.sleep(0.05)
    check(all(v == make_key("OwnPia") for v, _ in CALLS) and CALLS, "...on their own key")
    drop_key(W.P)

    reset_fakes()
    BEHAVIOUR[W.A_KEY] = refuses(W.A_KEY)
    a = signed_in(W.A)
    r = a.post("/reports/analyze-photos", data={"description": "x"})
    check(r.status_code == 409 and r.json().get("code") == "ai_key_invalid" and W.A_KEY not in r.text,
          "own key refused during analyze-photos: 409 ai_key_invalid, no key in the answer", r.text[:200])
    check(row_of(W.A).status == "invalid", "...and her key is stored as invalid")
    r = a.post("/reports/analyze-photos", data={"description": "x"})
    check(r.status_code == 409 and r.json().get("code") == "ai_key_invalid" and len(CALLS) == 1,
          "the next press is refused up front, before any Gemini call", r.text[:200])
    r = a.post(f"/reports/{rid}/annex-suggestions/ai", json={"parts": ["Rear Bumper"]})
    check(r.status_code == 404, "Annex AI on someone else's case is still 404 first", str(r.status_code))
    set_row(W.A, status="ok", last_error=None)
    BEHAVIOUR.clear()


def cli_never_from_http() -> None:
    places = list((ROOT / "app" / "api").glob("*.py")) + list((ROOT / "app" / "intake").glob("*.py")) + \
        list((ROOT / "app" / "auth").glob("*.py")) + [ROOT / "app" / "channels" / "whatsapp.py"]
    found = [str(p.relative_to(ROOT)) for p in places if re.search(r"AiActor\.cli\(|cli=True", p.read_text(encoding="utf-8"))]
    check(not found, "AiActor.cli() appears in no API route, intake or auth module, nor WhatsApp", repr(found))
    tg = (ROOT / "app" / "channels" / "telegram.py").read_text(encoding="utf-8")
    check("AiActor.cli()" in tg and "actor=AI_ACTOR" in tg, "the Telegram bot drafts as AiActor.cli()")


def nothing_leaked() -> None:
    lines = [line for line in LOG_LINES if any(k in line for k in W.ALL_KEYS | {make_key("OwnPia"), make_key("OldAlice")})]
    check(not lines, "no key in any log line", lines[0][:200] if lines else "")
    check(not SDK_BUILT, "no real Gemini client was ever built", repr(len(SDK_BUILT)))


def main() -> int:
    for name, fn in (
        ("setup", setup),
        ("AiActor and AiKey", actor_and_key),
        ("AiUnavailable codes", unavailable_codes),
        ("resolution order and fallback", resolution_order),
        ("ensure_ai_ready and ai_allowed", ready_and_allowed),
        ("call_structured: fallback and what's recorded", call_fallback),
        ("record_result", recording),
        ("quota clears by itself after an hour", quota_recovery),
        ("get_client cache", client_cache),
        ("the real SDK (google-genai's own errors) through call_structured", real_sdk),
        ("one AI request at a time", one_at_a_time),
        ("analyze-photos keeps nothing when the AI fails", analyze_keeps_nothing),
        ("the actor reaches every thread of an intake run", pool_threads),
        ("routes", routes),
        ("AiActor.cli() is never reachable from HTTP", cli_never_from_http),
        ("nothing leaked", nothing_leaked),
    ):
        run_section(name, fn)
    print(f"\ntest_ai_key_use: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        for section, label in FAILURES:
            print(f"  FAIL [{section}] {label}")
        return 1
    print("\nALL AI KEY USE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
