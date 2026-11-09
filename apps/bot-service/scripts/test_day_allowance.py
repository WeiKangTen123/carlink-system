"""Unit tests for the day memory of free Gemini allowances (app/ai/allowance.py),
call_structured's use of it (app/ai/structured.py), GET /auth/me/ai-allowance
(app/api/ai_allowance.py) and GET /intake/{id}'s read_by (ai/runs.read_by).

    python scripts/test_day_allowance.py

Everyone is on free-tier keys: 20 requests a day on the main model, reset
at midnight US Pacific. A daily-quota 429 (or "limit: 0") must cost one
request, no wait, and then nothing at all until the reset -- on the company
key too. A per-minute 429 keeps its one wait and retry.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database, get_client and the rate-limit slot replaced by fakes that record
every request, Google's errors made with the real SDK's error classes, and
every outside connection refused. The keys are made up; none may appear in
a log line, a stored file or a response.
"""
import hashlib
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time
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
_tmp = Path(tempfile.mkdtemp(prefix="day-allowance-test-"))
(_tmp / "storage").mkdir()


def make_key(label: str) -> str:
    """Shaped like a Gemini key: AIza + 35 characters."""
    return ("AIzaSy" + label + "x" * 40)[:35] + hashlib.md5(label.encode()).hexdigest()[:4]


COMPANY_KEY = make_key("CompanyDay")
os.environ["STORAGE_DIR"] = str((_tmp / "storage").resolve())
os.environ["SECRETS_DIR"] = str((_tmp / "secrets").resolve())
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = COMPANY_KEY
os.environ["GEMINI_MIN_CALL_INTERVAL_SECONDS"] = "0"
os.environ["GEMINI_MAX_REQUESTS_PER_MINUTE"] = "100000"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"
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


class _NoGoogle:
    def __init__(self, *args, **kwargs):
        raise AssertionError("a real Gemini client was built")


_genai.Client = _NoGoogle

import app.ai.structured as structured  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.ai import allowance, keys, runs, secretbox  # noqa: E402
from app.ai.keys import AiActor, AiKey, AiUnavailable  # noqa: E402
from app.api import ai_allowance  # noqa: E402
from app.api.main import app  # noqa: E402
from app.auth.core import SESSION_COOKIE, create_session  # noqa: E402
from app.config import settings  # noqa: E402
from app.reports.db import SessionLocal  # noqa: E402
from app.reports.models import AiRun, IntakeSession, Report, User, UserAiKey, gen_id  # noqa: E402

# ------------------------------------------------------------ the fake SDK
# Every Gemini request: (key value, model). Every rate-limit slot taken.
# How each key answers: key value -> fn(model) returning output or raising.
CALLS: list[tuple[str, str]] = []
ACQUIRED: list[str] = []
BEHAVIOUR: dict[str, object] = {}
_lock = threading.Lock()


class _FakeInteractions:
    def __init__(self, key: AiKey):
        self.key = key

    def create(self, *, model, **_kw):
        with _lock:
            CALLS.append((self.key.value, model))
        how = BEHAVIOUR.get(self.key.value)
        text = how(model) if how else json.dumps({"ok": True})
        return SimpleNamespace(output_text=text)


def fake_get_client(key):
    assert isinstance(key, AiKey)
    return SimpleNamespace(interactions=_FakeInteractions(key))


def fake_acquire(rate_limit_id):
    ACQUIRED.append(rate_limit_id)
    return 0.0


structured.get_client = fake_get_client
structured.acquire = fake_acquire


def google_error(code: int, status: str, message: str, details=None):
    body = {"error": {"code": code, "status": status, "message": message, "details": details or []}}
    return genai_errors.ClientError(code, body)


def day_429(limit: int = 20):
    """Google's answer once the day's free allowance is used up -- still
    asking for a 29.5 s wait, which can't help."""
    return google_error(
        429, "RESOURCE_EXHAUSTED",
        "You exceeded your current quota. Quota exceeded for metric: generate_content_free_tier_requests, "
        f"limit: {limit}, model: x. Please retry in 29.5s.",
        [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaMetric": "generate_content_free_tier_requests",
             "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
             "quotaDimensions": {"location": "global", "model": "x"}, "quotaValue": str(limit)}]}],
    )


def no_allowance_429():
    return google_error(
        429, "RESOURCE_EXHAUSTED",
        "Quota exceeded for metric: generate_content_free_tier_requests, limit: 0, model: x. Please retry in 10s.",
        [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}],
    )


def minute_429():
    return google_error(
        429, "RESOURCE_EXHAUSTED", "Quota exceeded for metric: requests, limit: 5. Please retry in 0.1s.",
        [{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}],
    )


def raises(exc_for):
    def how(model):
        exc = exc_for(model)
        if exc is not None:
            raise exc
        return json.dumps({"ok": True})
    return how


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


def reset() -> None:
    CALLS.clear()
    ACQUIRED.clear()
    BEHAVIOUR.clear()
    allowance.clear()
    allowance._clock = REAL_CLOCK
    keys._touched.clear()


REAL_CLOCK = allowance._clock


def call(actor: AiActor, models=None):
    """call_structured with a run-log collector: (result or exception, attempts)."""
    with runs.collecting() as collected:
        try:
            result = structured.call_structured(
                purpose="unit test", system_instruction="", input_parts=[{"type": "text", "text": "hi"}],
                schema={}, parse=json.loads, models=models or MODELS, actor=actor,
            )
        except Exception as exc:
            result = exc
    attempts, _ = collected.drain()
    return result, attempts


def outcomes(attempts) -> list[tuple[str, str]]:
    return [(a["outcome"], a["model"]) for a in attempts]


def code_of(result) -> str | None:
    return result.code if isinstance(result, AiUnavailable) else None


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


def give_key(user_id: str, key: str, status: str = "ok") -> None:
    db = SessionLocal()
    try:
        keys.save_key(db, user_id, key, secretbox.fingerprint(key), status, None, billing_confirmed=True)
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


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


W = SimpleNamespace()
RESPONSES: list[str] = []


# ================================================================= sections
def setup() -> None:
    W.boot = TestClient(app, base_url="https://carlink.test")
    W.boot.__enter__()  # init_db, and the secret is made
    check(secretbox.state() == secretbox.OK, "the secret is ready", secretbox.state())
    W.company = keys.company_keys()[0]
    W.cli = AiActor.cli()
    W.A_KEY, W.B_KEY = make_key("OwnAlice"), make_key("OwnBob")
    W.admin = new_user("Ada", role="admin")
    W.A = new_user("Alice")  # plain, own key only
    W.B = new_user("Bob", ai_company_key_allowed=True)  # ticked, own key
    W.P = new_user("Pia")  # plain, no key
    give_key(W.A, W.A_KEY)
    give_key(W.B, W.B_KEY)
    W.ALL_KEYS = [COMPANY_KEY, W.A_KEY, W.B_KEY]


def the_day() -> None:
    cases = [
        ("2026-11-09T07:59:00Z", "2026-11-09T08:00:00Z", "2026-11-08T08:00:00Z", "a minute before midnight PST"),
        ("2026-11-09T08:00:00Z", "2026-11-10T08:00:00Z", "2026-11-09T08:00:00Z", "at midnight PST: the next one"),
        ("2026-07-01T12:00:00Z", "2026-07-02T07:00:00Z", "2026-07-01T07:00:00Z", "summer: midnight PDT is 07:00 UTC"),
        ("2026-03-08T09:00:00Z", "2026-03-09T07:00:00Z", "2026-03-08T08:00:00Z", "the day clocks go forward (23 h)"),
        ("2026-11-01T08:30:00Z", "2026-11-02T08:00:00Z", "2026-11-01T07:00:00Z", "the day clocks go back (25 h)"),
    ]
    for now, reset_at, began, label in cases:
        got_reset = allowance.iso(allowance.next_reset(utc(now)))
        got_began = allowance.iso(allowance.day_started(utc(now)))
        check((got_reset, got_began) == (reset_at, began), f"{label}: resets {reset_at}, began {began}",
              f"{got_reset} {got_began}")
    check(allowance.next_reset().tzinfo is not None and allowance.next_reset() > datetime.now(timezone.utc),
          "next_reset() is in the future, in UTC")

    print("  the allowance Google states")
    check(allowance.limit_from(day_429(20)) == 20, "a daily 429: limit 20")
    check(allowance.limit_from(no_allowance_429()) == 0, "'limit: 0': 0")
    both = ("Quota exceeded for metric: a, limit: 5, model: x\n* Quota exceeded for metric: b, limit: 250, model: x "
            "'violations': [{'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier', 'quotaValue': '5'}, "
            "{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'model': 'x'}, "
            "'quotaValue': '250'}]")
    check(allowance.limit_from(both) == 250, "per-minute and per-day both listed: the day's (250)",
          repr(allowance.limit_from(both)))
    check(allowance.limit_from(google_error(429, "RESOURCE_EXHAUSTED", "Resource exhausted.")) is None,
          "a 429 that states none: None")


def the_store() -> None:
    reset()
    fp = W.company.fingerprint
    check(allowance.spent_until(fp, "model-a") is None and allowance.limit(fp, "model-a") is None, "nothing remembered at first")
    until = allowance.mark_spent(fp, "model-a", 20)
    check(until == allowance.next_reset(), "mark_spent: until the next reset", allowance.iso(until))
    check(allowance.spent_until(fp, "model-a") == until and allowance.limit(fp, "model-a") == 20,
          "spent_until and the learned limit read back")
    check(allowance.spent_until(fp, "model-b") is None and allowance.spent_until(W.A_KEY, "model-a") is None,
          "...for that key and model only")
    state = allowance.key_state(fp, ["model-a", "model-b"])
    check(state == {"model-a": {"spent_until": until, "limit": 20}, "model-b": {"spent_until": None, "limit": None}},
          "key_state: both in one read", repr(state))

    stored = (Path(os.environ["STORAGE_DIR"]) / ".allowance" / "days.json").read_text()
    check(all(k not in stored for k in W.ALL_KEYS) and fp not in stored and "AIza" not in stored,
          "the file holds no key, nor its fingerprint", stored[:200])

    allowance._clock = lambda: until + timedelta(seconds=1)
    check(allowance.spent_until(fp, "model-a") is None, "a second after the reset: not spent any more")
    check(allowance.limit(fp, "model-a") == 20, "...the learned limit stays (it's the model's daily allowance)")
    allowance.mark_spent(fp, "model-b")
    data = json.loads(stored_text())
    check(len(data["spent"]) == 1, "expired entries are dropped on the next write", repr(data["spent"]))
    allowance._clock = REAL_CLOCK

    allowance.mark_spent(fp, "model-a")
    allowance.forget(fp)
    check(allowance.spent_until(fp, "model-a") is None and allowance.limit(fp, "model-a") == 20,
          "forget(): that key's spent models go, its limits stay")

    path = Path(os.environ["STORAGE_DIR"]) / ".allowance" / "days.json"
    path.write_text("{not json")
    check(allowance.spent_until(fp, "model-a") is None, "a corrupt file: nothing remembered, no crash")
    allowance.mark_spent(fp, "model-a")
    check(allowance.spent_until(fp, "model-a") is not None, "...and the next mark writes a good one")


def stored_text() -> str:
    return (Path(os.environ["STORAGE_DIR"]) / ".allowance" / "days.json").read_text()


def company_key() -> None:
    reset()
    print("  the company key's daily 429")
    BEHAVIOUR[COMPANY_KEY] = raises(lambda m: day_429() if m == "model-a" else None)
    started = time.monotonic()
    result, attempts = call(W.cli)
    took = time.monotonic() - started
    check(result == {"ok": True}, "the next model answers", repr(result))
    check(CALLS == [(COMPANY_KEY, "model-a"), (COMPANY_KEY, "model-b")],
          "ONE request on model-a (no retry), then model-b", repr([m for _, m in CALLS]))
    check(took < 5, "...no 29.5s wait", f"{took:.1f}s")
    until = allowance.spent_until(W.company.fingerprint, "model-a")
    check(until == allowance.next_reset(), "model-a remembered as spent until the reset", repr(until))
    check(allowance.limit(W.company.fingerprint, "model-a") == 20, "...with Google's limit (20)")
    check(outcomes(attempts) == [("429", "model-a"), ("ok", "model-b")], "run log: 429 then ok", repr(outcomes(attempts)))
    check(attempts[0].get("spent_until") == allowance.iso(until) and attempts[0]["waited_s"] == 0.0,
          "...the 429 carries spent_until and no wait", repr(attempts[0]))

    print("  the next call skips it")
    CALLS.clear()
    ACQUIRED.clear()
    result, attempts = call(W.cli)
    check(result == {"ok": True} and CALLS == [(COMPANY_KEY, "model-b")], "no request on model-a: straight to model-b",
          repr(CALLS))
    check(len(ACQUIRED) == 1, "...and no rate-limit slot for the skipped one", repr(len(ACQUIRED)))
    skip = attempts[0] if attempts else {}
    check(outcomes(attempts) == [("day_spent", "model-a"), ("ok", "model-b")], "run log: day_spent then ok",
          repr(outcomes(attempts)))
    check(skip.get("waited_s") == 0.0 and skip.get("ms") == 0 and skip.get("spent_until") == allowance.iso(until)
          and skip.get("key_source") == "company" and skip.get("key_last4") == COMPANY_KEY[-4:],
          "...waited 0, ms 0, spent_until, key source and last 4", repr(skip))

    print("  after the reset")
    allowance._clock = lambda: until + timedelta(minutes=1)
    CALLS.clear()
    BEHAVIOUR.clear()
    result, attempts = call(W.cli)
    check(result == {"ok": True} and CALLS == [(COMPANY_KEY, "model-a")], "model-a is tried again, and answers", repr(CALLS))
    allowance._clock = REAL_CLOCK

    print("  every model spent")
    reset()
    BEHAVIOUR[COMPANY_KEY] = raises(lambda m: day_429())
    result, _ = call(W.cli)
    check(isinstance(result, RuntimeError) and len(CALLS) == len(MODELS), "the usual RuntimeError, one request per model",
          repr(result))
    CALLS.clear()
    result, attempts = call(W.cli)
    check(isinstance(result, RuntimeError) and CALLS == [], "the next call: no request at all", repr(CALLS))
    cause = result.__cause__ if isinstance(result, RuntimeError) else None
    check(isinstance(cause, allowance.DaySpent) and "quota" in str(cause),
          "...its cause says the day's quota is used up (the intake's 'quota' message)", repr(cause))
    check(runs.error_code_for(result, attempts) == runs.DAY_ALLOWANCE_USED,
          "...the run's error code: day_allowance_used", runs.error_code_for(result, attempts))
    check(runs.error_code_for(RuntimeError("x"), [{"outcome": "429", "model": "m"}]) == "rate_limited",
          "a plain 429 is still rate_limited")
    check(runs.error_code_for(RuntimeError("x"), [{"outcome": "429", "model": "m", "spent_until": "2026-11-10T08:00:00Z"}])
          == runs.DAY_ALLOWANCE_USED, "a day's 429: day_allowance_used")

    print("  'limit: 0' on the company key")
    reset()
    BEHAVIOUR[COMPANY_KEY] = raises(lambda m: no_allowance_429() if m == "model-a" else None)
    result, _ = call(W.cli)
    check(result == {"ok": True} and CALLS == [(COMPANY_KEY, "model-a"), (COMPANY_KEY, "model-b")],
          "one request, then the next model", repr(CALLS))
    check(allowance.spent_until(W.company.fingerprint, "model-a") is not None
          and allowance.limit(W.company.fingerprint, "model-a") == 0, "remembered, with limit 0")
    CALLS.clear()
    call(W.cli)
    check(CALLS == [(COMPANY_KEY, "model-b")], "...and skipped next time", repr(CALLS))


def per_minute() -> None:
    reset()
    tries = {"n": 0}

    def how(model):
        tries["n"] += 1
        if tries["n"] == 1:
            raise minute_429()
        return json.dumps({"ok": True})

    BEHAVIOUR[COMPANY_KEY] = how
    result, attempts = call(W.cli)
    check(result == {"ok": True} and CALLS == [(COMPANY_KEY, "model-a")] * 2,
          "a per-minute 429: waits as told and retries the same model", repr(CALLS))
    check(outcomes(attempts) == [("429", "model-a"), ("ok", "model-a")] and attempts[1]["waited_s"] > 0
          and "spent_until" not in attempts[0], "...the retry's wait recorded, nothing marked", repr(attempts))
    check(allowance.spent_until(W.company.fingerprint, "model-a") is None, "model-a is not remembered as spent")

    reset()
    BEHAVIOUR[COMPANY_KEY] = raises(lambda m: minute_429() if m == "model-a" else None)
    result, attempts = call(W.cli)
    check(result == {"ok": True} and CALLS == [(COMPANY_KEY, "model-a")] * 2 + [(COMPANY_KEY, "model-b")],
          "per-minute twice: the one retry, then the next model, as before", repr(CALLS))
    check(allowance.spent_until(W.company.fingerprint, "model-a") is None, "...still nothing marked")


def own_key() -> None:
    a = AiActor.for_user(W.A, "dashboard")
    own_fp = row_of(W.A).fingerprint

    print("  some models spent")
    reset()
    allowance.mark_spent(own_fp, "model-a", 20)
    result, attempts = call(a)
    check(result == {"ok": True} and CALLS == [(W.A_KEY, "model-b")], "the spent one skipped, the next answers", repr(CALLS))
    check(outcomes(attempts)[0] == ("day_spent", "model-a") and attempts[0].get("key_source") == "own",
          "...run log: day_spent on her own key", repr(outcomes(attempts)))
    check(row_of(W.A).status == "ok", "her key stays ok")

    print("  every model spent through 429s")
    reset()
    BEHAVIOUR[W.A_KEY] = raises(lambda m: day_429())
    result, _ = call(a)
    check(code_of(result) == "ai_quota_used" and len(CALLS) == len(MODELS), "ai_quota_used, one request per model",
          repr(result))
    row = row_of(W.A)
    check(row.status == "quota" and row.last_error and row.last_error.startswith("Google said: 429")
          and W.A_KEY not in row.last_error, "stored quota, with Google's words (scrubbed)", f"{row.status} {row.last_error!r}")
    check(all(allowance.spent_until(own_fp, m) for m in MODELS), "every model remembered as spent")

    print("  every model already spent: no request at all")
    set_row(W.A, status="ok", last_error=None)
    CALLS.clear()
    ACQUIRED.clear()
    result, attempts = call(a)
    check(code_of(result) == "ai_quota_used" and CALLS == [] and ACQUIRED == [],
          "ai_quota_used with no request and no slot", f"{result!r} {CALLS}")
    check([o for o, _ in outcomes(attempts)] == ["day_spent"] * len(MODELS), "run log: day_spent on each model")
    check(row_of(W.A).status == "quota", "...and her key is stored quota again")

    print("  tried again an hour on (retrying), still spent")
    set_row(W.A, status="quota", last_error="Google said: 429 earlier words",
            last_checked_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2))
    found = keys.resolve_keys(a)
    check(found and found[0].retrying, "an hour on, her key is being retried")
    CALLS.clear()
    result, _ = call(a)
    row = row_of(W.A)
    check(code_of(result) == "ai_quota_used" and CALLS == [], "every model skipped: ai_quota_used, no request")
    check(row.status == "quota" and row.last_error == "Google said: 429 earlier words",
          "...the row keeps Google's earlier words", repr(row.last_error))

    print("  after the reset")
    allowance._clock = lambda: allowance.next_reset(REAL_CLOCK()) + timedelta(minutes=1)
    CALLS.clear()
    BEHAVIOUR.clear()
    result, _ = call(a)
    check(result == {"ok": True} and CALLS == [(W.A_KEY, "model-a")], "model-a asked again, and it answers", repr(CALLS))
    check(row_of(W.A).status == "ok", "her key is back to ok")
    allowance._clock = REAL_CLOCK

    print("  'limit: 0' on every model")
    reset()
    set_row(W.A, status="ok", last_error=None)
    BEHAVIOUR[W.A_KEY] = raises(lambda m: no_allowance_429())
    result, _ = call(a)
    row = row_of(W.A)
    check(code_of(result) == "ai_key_invalid" and row.status == "invalid" and row.last_error == keys.NO_ALLOWANCE_ERROR,
          "ai_key_invalid, stored invalid: 'turn on billing' (as before)", f"{result!r} {row.status}")
    set_row(W.A, status="ok", last_error=None)
    CALLS.clear()
    result, _ = call(a)
    check(code_of(result) == "ai_key_invalid" and CALLS == [], "...skipped next time with no request, still no_allowance",
          f"{result!r} {CALLS}")
    set_row(W.A, status="ok", last_error=None)

    print("  a ticked user's own key spent: the company key")
    reset()
    b = AiActor.for_user(W.B, "dashboard")
    for m in MODELS:
        allowance.mark_spent(row_of(W.B).fingerprint, m, 20)
    result, attempts = call(b)
    check(result == {"ok": True} and CALLS == [(COMPANY_KEY, "model-a")], "no request on his key; the company's answers",
          repr(CALLS))
    check(row_of(W.B).status == "quota", "his key is stored quota")
    set_row(W.B, status="ok", last_error=None)


def make_draft(owner_id: str) -> str:
    db = SessionLocal()
    try:
        report = Report(channel="manual", reporter_chat_id="dashboard", status="intake", owner_id=owner_id, data={})
        db.add(report)
        db.flush()
        db.add(IntakeSession(report_id=report.id))
        db.commit()
        return report.id
    finally:
        db.close()


def read_by() -> None:
    reset()
    chain = [m.strip() for m in settings.gemini_model_chain.split(",") if m.strip()]
    main, backup = chain[0], chain[1]
    alice = signed_in(W.A)
    a = AiActor.for_user(W.A, "dashboard")

    def read(report_id: str, how=None) -> dict:
        BEHAVIOUR[W.A_KEY] = how or (lambda m: json.dumps({"ok": True}))
        with runs.recorded(report_id, "read", a):
            structured.call_structured(
                purpose="draft", system_instruction="", input_parts=[{"type": "text", "text": "hi"}],
                schema={}, parse=json.loads, models=chain, actor=a,
            )
        r = alice.get(f"/intake/{report_id}")
        RESPONSES.append(r.text)
        return r.json()

    rid = make_draft(W.A)
    state = read(rid)
    check("read_by" in state and state["read_by"] is None, "read by the main model: read_by null", repr(state.get("read_by")))

    state = read(rid, raises(lambda m: day_429() if m == main else None))
    want = {"model": backup, "main_model": main, "main_spent_until": allowance.iso(allowance.next_reset())}
    check(state.get("read_by") == want, "the main model's day used up (a 429): read by the backup, until the reset",
          repr(state.get("read_by")))
    state = read(rid)
    check(state.get("read_by") == want, "the next reading skips it (day_spent): still the backup", repr(state.get("read_by")))

    reset()
    rid2 = make_draft(W.A)
    state = read(rid2, raises(lambda m: TimeoutError("slow") if m == main else None))
    check(state.get("read_by") is None, "the main model timed out: no notice (not about the allowance)",
          repr(state.get("read_by")))
    CALLS.clear()
    state = read(rid2, raises(lambda m: minute_429() if m == main else None))
    check(state.get("read_by") is None and CALLS == [(W.A_KEY, main)] * 2 + [(W.A_KEY, backup)],
          "per-minute 429s: the main model retried once, then the backup -- no notice", repr(CALLS))

    print("  a failed reading after a backup one: the notice is about the reading shown")
    reset()
    rid3 = make_draft(W.A)
    read(rid3, raises(lambda m: day_429() if m == main else None))
    BEHAVIOUR[W.A_KEY] = raises(lambda m: RuntimeError("boom"))
    try:
        with runs.recorded(rid3, "redraft", a):
            structured.call_structured(purpose="draft", system_instruction="", input_parts=[], schema={},
                                       parse=json.loads, models=chain, actor=a)
    except Exception:
        pass
    r = alice.get(f"/intake/{rid3}")
    check((r.json().get("read_by") or {}).get("model") == backup, "the newest reading that finished is the one told",
          repr(r.json().get("read_by")))
    set_row(W.A, status="ok", last_error=None)


def add_run(actor_id: str | None, attempts: list[dict], *, started: datetime | None = None, kind: str = "read") -> None:
    db = SessionLocal()
    try:
        db.add(AiRun(report_id=None, kind=kind, channel="dashboard", actor_user_id=actor_id, status="ok",
                     started_at=(started or datetime.now(timezone.utc)).replace(tzinfo=None), attempts=attempts,
                     key_source=attempts[-1]["key_source"], key_last4=attempts[-1]["key_last4"]))
        db.commit()
    finally:
        db.close()


def endpoint() -> None:
    reset()
    real_settings = ai_allowance.settings
    ai_allowance.settings = SimpleNamespace(
        gemini_model_chain="main-m, backup-1,backup-2",
        gemini_triage_model_chain="sort-1,backup-1",
        gemini_second_look_chain="look-1,main-m",
    )
    try:
        roles = [("main-m", "main"), ("backup-1", "backup"), ("backup-2", "backup"), ("sort-1", "sorting"),
                 ("look-1", "second_look")]
        check(ai_allowance.model_roles() == roles, "roles: main, backups, sorting, second look -- the first role wins",
              repr(ai_allowance.model_roles()))
        ai_allowance.settings = SimpleNamespace(gemini_model_chain="main-m,backup-1", gemini_triage_model_chain="sort-1")
        check(ai_allowance.model_roles() == roles[:2] + [("sort-1", "sorting")], "no second-look setting: left out")
        ai_allowance.settings = SimpleNamespace(gemini_model_chain="main-m, backup-1,backup-2",
                                                gemini_triage_model_chain="sort-1,backup-1",
                                                gemini_second_look_chain=["look-1"])
        check(ai_allowance.model_roles() == roles, "...and a list for it works too")

        own_tail, company_tail = W.A_KEY[-4:], COMPANY_KEY[-4:]
        A_own = lambda model, outcome: {"model": model, "key_source": "own", "key_last4": own_tail, "outcome": outcome}  # noqa: E731
        Co = lambda model, outcome: {"model": model, "key_source": "company", "key_last4": company_tail, "outcome": outcome}  # noqa: E731
        add_run(W.A, [A_own("main-m", "ok")])
        add_run(W.A, [A_own("main-m", "429"), A_own("main-m", "schema"), A_own("backup-1", "timeout"), A_own("backup-1", "ok")])
        add_run(W.A, [A_own("main-m", "day_spent"), A_own("backup-1", "ok")])
        # Yesterday (Google's day): not counted.
        add_run(W.A, [A_own("main-m", "ok")], started=allowance.day_started() - timedelta(minutes=5))
        # Someone else's own key with the same last 4: not hers.
        add_run(W.P, [A_own("main-m", "ok")])
        # The company key: everyone's tries count.
        add_run(W.B, [Co("main-m", "ok"), Co("sort-1", "ok")])
        add_run(None, [Co("main-m", "ok")], kind="sort")
        add_run(W.A, [Co("main-m", "429"), Co("main-m", "day_spent")])
        allowance.mark_spent(row_of(W.A).fingerprint, "main-m", 20)
        allowance.mark_spent(W.company.fingerprint, "backup-2", 0)

        print("  a plain user with her own key")
        r = signed_in(W.A).get("/auth/me/ai-allowance")
        RESPONSES.append(r.text)
        body = r.json()
        check(r.status_code == 200 and set(body) == {"day_resets_at", "keys"}, "200 {day_resets_at, keys}", r.text[:200])
        check(body.get("day_resets_at") == allowance.iso(allowance.next_reset()), "day_resets_at: the next midnight Pacific",
              repr(body.get("day_resets_at")))
        check([k["source"] for k in body["keys"]] == ["own"], "only her own key: she may not use the company's")
        own = body["keys"][0]
        check(own["last4"] == own_tail and own["billed"] is True and set(own) == {"source", "last4", "billed", "models"},
              "her key's last 4 (it's hers), billed", repr({k: own[k] for k in ("last4", "billed")}))
        by_model = {m["model"]: m for m in own["models"]}
        check([m["model"] for m in own["models"]] == [m for m, _ in roles] and all(
            by_model[m]["role"] == role for m, role in roles), "every model with its role, in order")
        check(by_model["main-m"]["used_today"] == 2, "main-m used 2 today: ok and schema (not the 429, the skip, "
              "yesterday's or another person's)", repr(by_model["main-m"]))
        check(by_model["backup-1"]["used_today"] == 3, "backup-1 used 3 (a timeout counts: it reached Google)",
              repr(by_model["backup-1"]))
        check(by_model["main-m"]["limit"] == 20 and by_model["main-m"]["spent_until"] == allowance.iso(allowance.next_reset()),
              "main-m: limit 20 and spent until the reset")
        check(by_model["backup-2"] == {"model": "backup-2", "role": "backup", "used_today": 0, "limit": None,
                                       "spent_until": None}, "a model never used: 0, limit and spent_until null",
              repr(by_model["backup-2"]))

        print("  a ticked user: his own key, then the company's (no last 4)")
        r = signed_in(W.B).get("/auth/me/ai-allowance")
        RESPONSES.append(r.text)
        found = r.json()["keys"]
        check([k["source"] for k in found] == ["own", "company"], "own, then company", repr([k["source"] for k in found]))
        company = found[1]
        check(company["last4"] is None and company["billed"] is False, "the company key: no last 4 for him; billed false",
              repr(company))
        co = {m["model"]: m for m in company["models"]}
        check(co["main-m"]["used_today"] == 2 and co["sort-1"]["used_today"] == 1,
              "company tries counted from everyone's runs (main-m 2, sort-1 1)", repr(co["main-m"]))
        check(co["backup-2"]["limit"] == 0 and co["backup-2"]["spent_until"], "backup-2: limit 0, spent")
        check(found[0]["models"][0]["used_today"] == 0, "his own key: none of Alice's tries", repr(found[0]["models"][0]))

        print("  an admin sees the company key's last 4")
        r = signed_in(W.admin).get("/auth/me/ai-allowance")
        RESPONSES.append(r.text)
        found = r.json()["keys"]
        check([k["source"] for k in found] == ["company"] and found[0]["last4"] == company_tail,
              "admin without an own key: the company key, with its last 4", repr(found))

        print("  no key at all")
        r = signed_in(W.P).get("/auth/me/ai-allowance")
        check(r.status_code == 200 and r.json()["keys"] == [], "a plain user without a key: keys []", r.text[:200])
        r = TestClient(app, base_url="https://carlink.test").get("/auth/me/ai-allowance")
        check(r.status_code == 401, "signed out: 401", str(r.status_code))
    finally:
        ai_allowance.settings = real_settings


def no_keys_anywhere() -> None:
    files = [p for p in Path(os.environ["STORAGE_DIR"]).rglob("*") if p.is_file()]
    stored = "".join(p.read_text(errors="replace") for p in files if p.suffix in (".json", ".lock", ".tmp", ""))
    for name, text in (("a log line", "\n".join(LOG_LINES)), ("a stored file", stored), ("a response", "\n".join(RESPONSES))):
        leaked = [k[-6:] for k in W.ALL_KEYS if k in text]
        check(not leaked, f"no key in {name}", f"found keys ending {leaked}")
    check(W.company.fingerprint not in stored, "the company key's fingerprint isn't stored either")
    check(any("skipped on" in line and "free daily allowance" in line for line in LOG_LINES),
          "the skip is logged, by the key's label")


def not_judged() -> None:
    """judge_key=False (the second look): its chain used up stores nothing
    on the person's key; the call still ends in ai_quota_used."""
    reset()
    a = AiActor.for_user(W.A, "dashboard")
    set_row(W.A, status="ok", last_error=None)
    BEHAVIOUR[W.A_KEY] = raises(lambda m: day_429())
    with runs.collecting():
        try:
            structured.call_structured(
                purpose="unit test", system_instruction="", input_parts=[{"type": "text", "text": "hi"}],
                schema={}, parse=json.loads, models=MODELS[:2], actor=a, judge_key=False,
            )
            result = None
        except Exception as exc:
            result = exc
    check(code_of(result) == "ai_quota_used", "the call still ends in ai_quota_used", repr(result))
    check(row_of(W.A).status == "ok", "her key stays ok: the reading may still use it", row_of(W.A).status)
    BEHAVIOUR[W.A_KEY] = raises(lambda m: no_allowance_429())
    reset()
    with runs.collecting():
        try:
            structured.call_structured(
                purpose="unit test", system_instruction="", input_parts=[{"type": "text", "text": "hi"}],
                schema={}, parse=json.loads, models=MODELS[:2], actor=a, judge_key=False,
            )
        except Exception:
            pass
    check(row_of(W.A).status == "ok", "'limit: 0' on its models doesn't store the key invalid either", row_of(W.A).status)
    BEHAVIOUR.clear()
    check(allowance.day_spent_error(allowance.DaySpent("model-a")), "day_spent_error: a skip")
    wrapped = RuntimeError("All Gemini fallback models (a) failed")
    wrapped.__cause__ = RuntimeError("Error code: 429 - limit: 20 requests per day on Free Tier")
    check(allowance.day_spent_error(wrapped), "day_spent_error: Google's per-day 429 as the cause")
    busy = RuntimeError("All Gemini fallback models (a) failed")
    busy.__cause__ = RuntimeError("Error code: 503 - high demand")
    check(not allowance.day_spent_error(busy) and not allowance.day_spent_error(minute_429()),
          "day_spent_error: not a 503, not a per-minute 429")
    from app.intake.pipeline import _friendly_error
    check("4 pm Malaysia time" in _friendly_error(wrapped) and "few minutes" not in _friendly_error(wrapped),
          "the reading's message names the reset, not 'a few minutes'", _friendly_error(wrapped))
    check("few minutes" in _friendly_error(busy), "...a busy model still says Retry in a few minutes")
    reset()
    BEHAVIOUR[COMPANY_KEY] = raises(lambda m: day_429())
    result, _ = call(AiActor.cli())
    check(isinstance(result, allowance.DayUsedUp) and "fallback models" in str(result),
          "company key, every model's day used up: DayUsedUp (still the usual failure text)", repr(result))
    check(allowance.day_spent_error(result) and "4 pm Malaysia time" in _friendly_error(result),
          "...and the reading says when it comes back", _friendly_error(result))
    reset()
    BEHAVIOUR[COMPANY_KEY] = raises(lambda m: day_429() if m == MODELS[0] else minute_429())
    result, _ = call(AiActor.cli(), models=MODELS[:2])
    check(type(result) is RuntimeError, "one model busy for the minute: the usual RuntimeError", repr(result))
    BEHAVIOUR.clear()


def forgotten_on_change() -> None:
    """Saving again, switching Free/Billed, or a passing Test: what was
    remembered as used up for the key no longer holds."""
    reset()
    uid = new_user("forget-me")
    key = make_key("forget")
    give_key(uid, key)
    fp = secretbox.fingerprint(key)
    client = signed_in(uid)
    allowance.mark_spent(fp, "model-a", 0)
    r = client.patch("/auth/me/ai-key", json={"billing_confirmed": False})
    RESPONSES.append(r.text)
    check(r.status_code == 200, "PATCH to Free answers 200", r.text[:200])
    check(allowance.spent_until(fp, "model-a") is None and allowance.limit(fp, "model-a") is None,
          "switching Free/Billed forgets the spent day and the learnt limit")
    allowance.mark_spent(fp, "model-a", 20)
    real_probe = keys.probe_key
    keys.probe_key = lambda k: ("ok", None)
    try:
        r = client.post("/auth/me/ai-key/test")
        RESPONSES.append(r.text)
    finally:
        keys.probe_key = real_probe
    check(r.status_code == 200, "Test answers 200", r.text[:200])
    check(allowance.spent_until(fp, "model-a") is None and allowance.limit(fp, "model-a") == 20,
          "a passing Test forgets the spent day, keeps the limit")
    other = make_key("forget-other")
    give_key(new_user("forget-other"), other)
    allowance.mark_spent(secretbox.fingerprint(other), "model-a", 20)
    client.patch("/auth/me/ai-key", json={"billing_confirmed": True})
    check(allowance.spent_until(secretbox.fingerprint(other), "model-a") is not None,
          "another person's key is left alone")


def main() -> int:
    for name, fn in (
        ("setup", setup),
        ("the day and the limit", the_day),
        ("the store", the_store),
        ("the company key", company_key),
        ("a per-minute 429 is unchanged", per_minute),
        ("an own key", own_key),
        ("read_by on GET /intake/{id}", read_by),
        ("GET /auth/me/ai-allowance", endpoint),
        ("forgotten when the key changes", forgotten_on_change),
        ("a call that doesn't judge the key", not_judged),
        ("no key anywhere", no_keys_anywhere),
    ):
        run_section(name, fn)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAIL [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
