"""Independent tests for release P4, "AI uses your own key" (agent P4-T).

    python scripts/test_own_key_ai.py

Written from the spec, not from the code under test:
plans/2026-10-26-roles-ai-telegram/p4_build_brief.md ("Rules", "Backend
contract", "Independent tests"; the brief wins on any conflict) and
agent5_integration_report.md section 4.3 ("who pays: whoever presses or
sends").

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database, and the app's real HTTP API through TestClient. google.genai.Client
is replaced by a fake that records, for every call, the key its client was
made with, the thread that made the call and the threads that started that
one (so a call made in a sort-pool worker is known as one). It answers ok, or
fails per key: auth refused (400/401/403), quota (429, for the day or for the
minute) or a server error (503). Every outside connection and DNS lookup is
refused. The keys are made up; failure output names them ("Alice's own key"),
never prints them.
"""
import asyncio
import base64
import concurrent.futures.thread as _pool_module
import hashlib
import io
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
import uuid
from collections import Counter
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
_tmp = Path(tempfile.mkdtemp(prefix="own-key-ai-test-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
SECRETS = (_tmp / "secrets").resolve()
DB_FILE = (_tmp / "test.db").resolve()


def make_key(label: str) -> str:
    """A made-up key shaped like a Gemini one: AIza + 35 characters, the
    last 4 different for each label."""
    return ("AIzaSy" + label + "x" * 40)[:35] + hashlib.md5(label.encode()).hexdigest()[:4]


ENV_COMPANY_KEY = make_key("CompanyEnv")
SETTINGS_COMPANY_KEY = make_key("CompanyRow")
# Forced, not setdefault: never a real folder or key from the environment.
os.environ.update({
    "STORAGE_DIR": str(STORAGE),
    "SECRETS_DIR": str(SECRETS),
    "DATABASE_URL": f"sqlite:///{DB_FILE.as_posix()}",
    "GEMINI_API_KEY": ENV_COMPANY_KEY,
    "RUN_BACKGROUND_CLEANUP": "false",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_ALLOWED_IDS": "",
    # Made up, only so the WhatsApp webhook is mounted (it is only on a
    # deployment with Twilio configured). Nothing is ever sent to Twilio.
    "TWILIO_ACCOUNT_SID": "ACtest00000000000000000000000000",
    "TWILIO_AUTH_TOKEN": "test-twilio-token",
    # This test makes many calls a minute on one key; no waiting for windows.
    "GEMINI_MAX_REQUESTS_PER_MINUTE": "100000",
    "GEMINI_MIN_CALL_INTERVAL_SECONDS": "0",
})
os.environ.pop("GOOGLE_API_KEY", None)

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

# ------------------------------------------------------------ no outside network
NET_REFUSED: list[str] = []
_LOCAL = (None, "", "127.0.0.1", "localhost", "::1", "testserver")
_real_connect = socket.socket.connect
_real_getaddrinfo = socket.getaddrinfo


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in _LOCAL:
        NET_REFUSED.append(f"connect to {host!r}")
        raise ConnectionRefusedError(f"test is offline: refused a connection to {host!r}")
    return _real_connect(sock, address)


def _offline_getaddrinfo(host, *args, **kwargs):
    name = host.decode() if isinstance(host, bytes) else host
    if name not in _LOCAL:
        NET_REFUSED.append(f"look up {name!r}")
        raise socket.gaierror(f"test is offline: refused to look up {name!r}")
    return _real_getaddrinfo(host, *args, **kwargs)


socket.socket.connect = _offline_connect
socket.getaddrinfo = _offline_getaddrinfo

# ------------------------------------------- no long sleeps in background threads
# A 429 makes the app wait as Google says (up to 30s), and the rate limiter
# may wait for a slot. Not what this test is about: shortened, in every
# thread but this one.
_real_sleep = time.sleep


def _quick_sleep(seconds):
    if seconds > 0.2 and threading.current_thread() is not threading.main_thread():
        seconds = 0.01
    _real_sleep(seconds)


time.sleep = _quick_sleep

# --------------------------------------- which thread started which thread
_real_thread_start = threading.Thread.start


def _thread_start(self, *args, **kwargs):
    self._p4t_parent = threading.current_thread()
    target = getattr(self, "_target", None)
    # A ThreadPoolExecutor worker (the sort pool, asyncio.to_thread, ...).
    self._p4t_pool = getattr(target, "__module__", None) == _pool_module.__name__
    return _real_thread_start(self, *args, **kwargs)


threading.Thread.start = _thread_start


def _lineage() -> list[threading.Thread]:
    out, t = [], threading.current_thread()
    while t is not None and len(out) < 10:
        out.append(t)
        t = getattr(t, "_p4t_parent", None)
    return out


# ================================================================ fake Google
import httpx  # noqa: E402
from PIL import Image  # noqa: E402
from google.genai import errors as genai_errors  # noqa: E402

_fake_lock = threading.Lock()
CALLS: list[dict] = []        # one per generation call
PROBES: list = []             # the key of every models.list (a key check)
CLIENTS: list[dict] = []      # every client made: was it given a key?
MODE: dict[str, str] = {}     # key -> how Google answers it (default "ok")
ONCE: dict[str, str] = {}     # key -> an answer for the next call only
AFTER: dict[str, tuple] = {}  # key -> (n, how): ok for n calls, then how
ON_PURPOSE: dict[tuple, str] = {}  # (key, "sort"|"draft"|"annex") -> how
HOOKS: dict[str, object] = {}      # key -> fn(call record), run inside the call
HOOK_ERRORS: list[str] = []
PROBE_MODE: dict[str, str] = {}    # key -> how models.list answers it
_OKS: Counter = Counter()
COLOUR_OF: dict[str, tuple] = {}   # case tag -> the colour of its photos
FAKE_DELAY = 0.03  # a call takes a moment, so the sort batches overlap

DRAFT_ANSWER = {
    "description": "Insured vehicle was rear-ended.",
    "category": ["Vehicle Collision or Damage"],
    "damaged_parts": ["Rear Bumper"],
    "damage_summary": [{"part": "Rear Bumper", "severity": "Moderate", "ai_confidence": "High"}],
    "vehicle_info": {"make": "Honda", "model": "Vezel", "plate_number": "SNA1234B"},
}


def _google_says(code: int, status: str, message: str, details=None) -> dict:
    error = {"code": code, "status": status, "message": message}
    if details:
        error["details"] = details
    return {"error": error}


def _quota_details(per: str, delay: str) -> list:
    return [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{
            "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
            "quotaId": f"GenerateRequestsPer{per}PerProjectPerModel-FreeTier",
            "quotaDimensions": {"location": "global", "model": "gemini-3-flash"},
            "quotaValue": "250" if per == "Day" else "10"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay},
    ]


def google_error(how: str, key: str) -> Exception:
    """What Google's SDK raises, in Google's own shapes."""
    if how == "refuse":  # a deleted or wrong key
        return genai_errors.ClientError(400, _google_says(
            400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.",
            [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID", "domain": "googleapis.com",
              "metadata": {"service": "generativelanguage.googleapis.com"}}]))
    if how == "unauth":
        return genai_errors.ClientError(401, _google_says(
            401, "UNAUTHENTICATED", "Request had invalid authentication credentials. Expected OAuth 2 access token, "
            "login cookie or other valid authentication credential."))
    if how == "suspended":
        # Google's real wording for a suspended key quotes the key itself.
        return genai_errors.ClientError(403, _google_says(
            403, "PERMISSION_DENIED", f"Permission denied: Consumer 'api_key:{key}' has been suspended.",
            [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "CONSUMER_SUSPENDED", "domain": "googleapis.com",
              "metadata": {"consumer": "projects/123456", "service": "generativelanguage.googleapis.com"}}]))
    if how == "quota":  # the day's allowance is spent
        return genai_errors.ClientError(429, _google_says(
            429, "RESOURCE_EXHAUSTED",
            "You exceeded your current quota, please check your plan and billing details. For more information on "
            "this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits.\n* Quota exceeded for metric: "
            "generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 250, model: gemini-3-flash\n"
            "Please retry in 70210.5s.", _quota_details("Day", "70210s")))
    if how == "minute":  # too many this minute; fine again in seconds
        return genai_errors.ClientError(429, _google_says(
            429, "RESOURCE_EXHAUSTED",
            "You exceeded your current quota, please check your plan and billing details.\n* Quota exceeded for "
            "metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 10, model: "
            "gemini-3-flash\nPlease retry in 4.2s.", _quota_details("Minute", "4s")))
    if how == "server":
        return genai_errors.ServerError(503, _google_says(503, "UNAVAILABLE", "The model is overloaded. Please try again later."))
    if how == "network":
        return httpx.ConnectError("[Errno 11001] getaddrinfo failed")
    return RuntimeError(f"test error: unknown fake answer {how!r}")


def _purpose(kw: dict) -> str:
    fmt = kw.get("response_format")
    schema = fmt.get("schema") if isinstance(fmt, dict) else None
    props = (schema or {}).get("properties") or {}
    if "photos" in props:
        return "sort"
    if "parts" in props:
        return "annex"
    if "damage_summary" in props or "description" in props:
        return "draft"
    return "other"


def _parts(kw: dict) -> list:
    parts = kw.get("input")
    return [p for p in parts if isinstance(p, dict)] if isinstance(parts, list) else []


def _case_of(kw: dict):
    """Which case a call is about: the colour of its first photo."""
    for part in _parts(kw):
        if part.get("type") == "image" and part.get("data"):
            try:
                img = Image.open(io.BytesIO(base64.b64decode(part["data"]))).convert("RGB")
                px = img.getpixel((img.width // 2, img.height // 2))
            except Exception:
                return None
            best, dist = None, None
            for tag, colour in list(COLOUR_OF.items()):
                d = sum((a - b) ** 2 for a, b in zip(colour, px))
                if dist is None or d < dist:
                    best, dist = tag, d
            return best if dist is not None and dist < 3 * 35 ** 2 else None
    return None


def _answer(purpose: str, kw: dict) -> str:
    texts = [p.get("text") or "" for p in _parts(kw) if p.get("type") == "text"]
    if purpose == "sort":
        labels = [m.group(1) for t in texts for m in [re.match(r"Photo (P\d+):", t)] if m]
        return json.dumps({"photos": [
            {"label": label, "kind": "overview" if i == 0 else "damage", "area": "rear", "quality": "good",
             "shows_damage": i != 0, "part": None if i == 0 else "Rear Bumper", "note": f"photo {label}"}
            for i, label in enumerate(labels)]})
    if purpose == "annex":
        parts = ["Rear Bumper"]
        for t in texts:
            m = re.search(r"Damaged parts: (\[.*\])", t)
            if m:
                try:
                    parts = json.loads(m.group(1))
                except ValueError:
                    pass
        return json.dumps({"parts": [{"part": p, "lines": [{"name": f"{p} clips", "qty": 2}]} for p in parts]})
    if purpose == "draft":
        return json.dumps(DRAFT_ANSWER)
    return "{}"


def _decide(key, purpose: str) -> str:
    if key in ONCE:
        how = ONCE.pop(key)
    else:
        how = ON_PURPOSE.get((key, purpose))
        if how is None and key in AFTER:
            n, then = AFTER[key]
            how = "ok" if _OKS[key] < n else then
        how = how or MODE.get(key, "ok")
    if how == "ok":
        _OKS[key] += 1
    return how


def _generate(client, kw: dict, purpose: str) -> str:
    lineage = _lineage()
    pool = next((t for t in lineage if getattr(t, "_p4t_pool", False)), None)
    record = {
        "key": client.key, "implicit": client.implicit, "purpose": purpose, "model": kw.get("model"),
        "thread": threading.current_thread().name, "lineage": [t.name for t in lineage],
        "pool": (pool.name, id(pool)) if pool is not None else None,
        "case": _case_of(kw), "images": sum(1 for p in _parts(kw) if p.get("type") == "image"),
        "outcome": None, "at": time.monotonic(),
    }
    with _fake_lock:
        CALLS.append(record)
        how = _decide(client.key, purpose)
    hook = HOOKS.get(client.key)
    if hook:
        try:
            hook(record)
        except Exception as exc:  # reported at the end
            HOOK_ERRORS.append(f"{type(exc).__name__}: {exc}")
    _real_sleep(FAKE_DELAY)
    record["outcome"] = how
    if how != "ok":
        raise google_error(how, client.key)
    return _answer(purpose, kw)


class _FakeInteractions:
    def __init__(self, client):
        self.client = client

    def create(self, **kw):
        return SimpleNamespace(output_text=_generate(self.client, kw, _purpose(kw)))


class _FakeModels:
    def __init__(self, client):
        self.client = client

    def list(self, *args, **kwargs):
        with _fake_lock:
            PROBES.append(self.client.key)
        how = PROBE_MODE.get(self.client.key, "ok")
        if how != "ok":
            raise google_error(how, self.client.key)
        return iter([])

    def generate_content(self, **kw):
        return SimpleNamespace(text=_generate(self.client, kw, "generate_content"), candidates=[])


class FakeGenaiClient:
    def __init__(self, *args, api_key=None, **kwargs):
        self.implicit = not api_key
        # As the real SDK does: no api_key -> GOOGLE_API_KEY or GEMINI_API_KEY
        # from the environment, i.e. silently the company key.
        self.key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        with _fake_lock:
            CLIENTS.append({"given": bool(api_key), "key": self.key})
        self.models = _FakeModels(self)
        self.interactions = _FakeInteractions(self)

    def close(self):
        pass


import google.genai as _genai  # noqa: E402

_genai.Client = FakeGenaiClient

from cryptography.fernet import Fernet  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.api.ai_keys as ai_keys_api  # noqa: E402
from app.ai import keys, secretbox  # noqa: E402
from app.api.main import app  # noqa: E402
from app.auth.core import SESSION_COOKIE  # noqa: E402
from app.reports.db import SessionLocal, engine  # noqa: E402
from app.reports.models import Report, UserAiKey  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402
from app.storage.files import tmp_dir  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

try:
    from app.ai.triage import BATCH_SIZE  # noqa: E402
except Exception:
    BATCH_SIZE = 12

logging.getLogger().setLevel(logging.DEBUG)

# ================================================================ harness
BASE = "https://carlink.test"
COMPANY = {ENV_COMPANY_KEY, SETTINGS_COMPANY_KEY}
# The brief's table, word for word.
MESSAGES = {
    "ai_key_missing": "AI is off for your account. Add your own AI key in Settings → My AI key, or ask an admin to allow the company key.",
    "ai_key_invalid": "Your AI key stopped working (Google refused it). Check it in Settings → My AI key.",
    # Reworded once quota keys came back by themselves after an hour.
    "ai_quota_used": "Your AI key has used up its quota for now. Try again in about an hour, or check billing in Google Cloud.",
    "ai_key_unreadable": "Your AI key can't be read on this server right now. Tell an admin (Settings → Company).",
}
# Every key the test uses; none may appear in a response, a log line or the
# database file (the company keys are stored plain in api_keys, as before).
SENT_KEYS: set[str] = set(COMPANY)
KEY_LABEL: dict[str, str] = {ENV_COMPANY_KEY: "the company key (GEMINI_API_KEY)",
                             SETTINGS_COMPANY_KEY: "the company key (Settings)"}
RESPONSES: list[str] = []
BOT_TEXTS: list[str] = []
DRAFT_PHOTOS: dict[str, int] = {}

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}

_LEVELS = (20, 90, 160, 230)
PALETTE = [(r, g, b) for r in _LEVELS for g in _LEVELS for b in _LEVELS if not r == g == b]


class Stop(Exception):
    pass


def mask(value) -> str:
    out = str(value)
    for k in sorted(SENT_KEYS, key=len, reverse=True):
        out = out.replace(k, f"<{KEY_LABEL.get(k, 'a key')}>")
    return out


def check(cond, label: str, why: str = "") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
        return True
    why = mask(why)
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
        msg = mask(f"section stopped by {type(exc).__name__}: {exc} (test line {frames[-1].lineno if frames else -1})")
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
    body = (r.text or "").replace("\n", " ")[:240]
    return mask(f"{r.status_code} {body}")


def reset_key_limits() -> None:
    reset = getattr(ai_keys_api, "reset_limits", None)
    if reset:
        reset()


def who_key(key) -> str:
    return KEY_LABEL.get(key, "a key nobody saved") if key else "no key at all"


def snapshot(mark: int = 0) -> list[dict]:
    with _fake_lock:
        return list(CALLS[mark:])


def activity() -> int:
    """Everything the fake Google was asked: calls and key checks."""
    with _fake_lock:
        return len(CALLS) + len(PROBES)


def describe(calls: list[dict]) -> str:
    if not calls:
        return "no Gemini call was made"
    counts = Counter(
        f"{c['purpose']} on {who_key(c['key'])}" + (f" (answered {c['outcome']})" if c["outcome"] not in ("ok", None) else "")
        for c in calls)
    return "; ".join(f"{n} x {what}" for what, n in counts.most_common(6))


def off_key(calls: list[dict], allowed: set) -> str:
    """Why `every call used an allowed key` failed, with the threads."""
    if not calls:
        return "no Gemini call was made"
    bad = [c for c in calls if c["key"] not in allowed]
    if not bad:
        return describe(calls)
    shown = "; ".join(f"{c['purpose']} on {who_key(c['key'])} from {' <- '.join(c['lineage'][:4])}" for c in bad[:3])
    return f"{len(bad)} of {len(calls)} call(s) on another key: {shown}"


def all_on(calls: list[dict], allowed: set) -> bool:
    return bool(calls) and all(c["key"] in allowed for c in calls)


def stage_line(s: dict) -> str:
    return (f"stage {s.get('stage')!r}, error_code {s.get('error_code')!r}"
            f"{'' if 'error_code' in s else ' (no error_code field)'}, error {(s.get('error') or '')[:160]!r}")


def photo_bytes(colour: tuple, i: int) -> bytes:
    """A photo of one colour (which case it is), with a corner patch that
    differs per photo (so none is a duplicate)."""
    img = Image.new("RGB", (320, 240), colour)
    img.paste(((i * 37) % 256, (i * 91) % 256, (i * 53) % 256), (0, 0, 14, 14))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def new_colour(tag: str) -> tuple:
    COLOUR_OF[tag] = PALETTE[len(COLOUR_OF) % len(PALETTE)]
    return COLOUR_OF[tag]


class Person:
    def __init__(self, label: str, role: str = "user", **profile):
        self.label = label
        self.c = browser()
        self.id = sign_in(self.c, role=role, name=label, username=label.lower(), **profile)
        self.key = None

    def save_key(self, key: str, probe: str = "ok", expect: str = "ok") -> dict:
        """PUT /auth/me/ai-key (P3), with the fake Google's key check
        answering `probe`."""
        reset_key_limits()
        SENT_KEYS.add(key)
        KEY_LABEL[key] = f"{self.label}'s own key"
        PROBE_MODE[key] = probe
        r = self.c.put("/auth/me/ai-key", json={"api_key": key, "billing_confirmed": True})
        need(r.status_code == 200 and jd(r).get("status") == expect,
             f"{self.label} saves an own key (P3): status {expect}", brief(r))
        self.key = key
        return jd(r)

    def recheck(self, probe: str, expect: str) -> None:
        """POST /auth/me/ai-key/test (P3) with Google now answering `probe`."""
        reset_key_limits()
        PROBE_MODE[self.key] = probe
        r = self.c.post("/auth/me/ai-key/test")
        need(r.status_code == 200 and jd(r).get("status") == expect,
             f"{self.label}'s key is checked again: status {expect}", brief(r))

    def key_status(self) -> dict:
        return jd(self.c.get("/auth/me/ai-key"))

    def me(self) -> dict:
        return jd(self.c.get("/auth/me"))

    def other_tab(self) -> "Person":
        tab = object.__new__(Person)
        tab.label, tab.id, tab.key = self.label, self.id, self.key
        tab.c = browser()
        tab.c.cookies.set(SESSION_COOKIE, self.c.cookies.get(SESSION_COOKIE))
        return tab


def new_draft(p: Person, n: int) -> str:
    r = p.c.post("/intake")
    need(r.status_code == 200 and jd(r).get("id"), f"{p.label} starts a draft (POST /intake)", brief(r))
    rid = r.json()["id"]
    new_colour(rid)
    DRAFT_PHOTOS[rid] = 0
    if n:
        add_photos(p, rid, n)
    return rid


def add_photos(p: Person, rid: str, n: int) -> None:
    problem = ""
    for _ in range(n):
        i = DRAFT_PHOTOS[rid]
        r = p.c.post(f"/intake/{rid}/photos", files={"file": (f"IMG_{i:04d}.jpg", photo_bytes(COLOUR_OF[rid], i), "image/jpeg")},
                     data={"position": str(i)})
        if not (r.status_code == 200 and "photo" in jd(r)):
            problem = brief(r)
            break
        DRAFT_PHOTOS[rid] += 1
    need(not problem, f"{p.label} uploads {n} photo(s) to the draft", problem)


def wait_done(p: Person, rid: str, timeout: float = 45.0) -> dict:
    end = time.time() + timeout
    state: dict = {}
    while time.time() < end:
        state = jd(p.c.get(f"/intake/{rid}"))
        if state.get("stage") in ("ready", "failed"):
            return state
        _real_sleep(0.15)
    return {**state, "stage": f"still {state.get('stage')!r} after {timeout:.0f}s"}


def stage_of(p: Person, rid: str) -> dict:
    s = jd(p.c.get(f"/intake/{rid}"))
    return {k: s.get(k) for k in ("stage", "error", "error_code", "progress", "stage_started_at")}


def calls_for(rid: str, mark: int) -> list[dict]:
    return [c for c in snapshot(mark) if c["case"] == rid]


def press(p: Person, rid: str, mode: str = "all"):
    mark = len(snapshot())
    return p.c.post(f"/intake/{rid}/process", json={"mode": mode}), mark


def run(p: Person, rid: str, what: str, mode: str = "all") -> tuple[dict, list[dict]]:
    """Presses Process/Retry and waits for the run; its state and calls."""
    r, mark = press(p, rid, mode)
    need(r.status_code == 200, f"{what} -> 200", brief(r))
    state = wait_done(p, rid)
    return state, calls_for(rid, mark)


def sorted_count(state: dict) -> int:
    return sum(1 for ph in state.get("photos") or [] if ph.get("kind"))


def filed_case(owner: Person) -> str:
    """A filed case owned by `owner` (set up directly, not through the API)."""
    data = SecurityIncidentDraft(description="Insured vehicle was rear-ended.", category=["Vehicle Collision or Damage"]).model_dump()
    data["vehicle_info"] = {**(data.get("vehicle_info") or {}), "make": "Honda", "model": "Vezel"}
    db = SessionLocal()
    try:
        report = Report(channel="manual", reporter_chat_id="dashboard", status="confirmed", owner_id=owner.id, data=data)
        db.add(report)
        db.commit()
        return report.id
    finally:
        db.close()


def annex(p: Person, case_id: str):
    mark = len(snapshot())
    r = p.c.post(f"/reports/{case_id}/annex-suggestions/ai", json={"parts": ["Rear Bumper", "Boot Lid"]})
    return r, snapshot(mark)


def analyze(p: Person):
    """POST /reports/analyze-photos with one photo: the response, the
    calls, and the files it left in storage/tmp."""
    tag = f"analyze-{uuid.uuid4().hex[:6]}"
    colour = new_colour(tag)
    before = set(os.listdir(tmp_dir()))
    mark = len(snapshot())
    r = p.c.post("/reports/analyze-photos", data={"description": "Rear-ended at the carpark."},
                 files=[("photos", ("a.jpg", photo_bytes(colour, 0), "image/jpeg"))])
    return r, snapshot(mark), set(os.listdir(tmp_dir())) - before


def ai_key_row(user_id: str):
    db = SessionLocal()
    try:
        return db.query(UserAiKey).filter(UserAiKey.user_id == user_id).first()
    finally:
        db.close()


def restart_secretbox() -> str:
    """As if the api started again: reads the secret from scratch (P3)."""
    secretbox.forget()
    return secretbox.startup(create=True)


W = type("World", (), {})()


# ================================================================= sections
def setup() -> None:
    # The real start-up (lifespan): init_db, then the api makes the secret.
    W.boot = browser()
    W.boot.__enter__()
    need(secretbox.state() == "ok", "the api starts: AI key storage ok", secretbox.state())
    W.ada = Person("Ada", role="admin")
    r = W.ada.c.post("/setup/llm-keys", json={"apiKey": SETTINGS_COMPANY_KEY, "label": "Firm"})
    need(r.status_code == 200, "the admin adds a company key in Settings (beside GEMINI_API_KEY)", brief(r))
    W.abe = Person("Abe", role="admin")
    W.abe.save_key(make_key("Abe"))
    # No ticks at all: AI only ever on her own key.
    W.alice = Person("Alice")
    W.alice.save_key(make_key("Alice"))
    # Sees (and may work on) all filed cases; not allowed the company key.
    W.bob = Person("Bob", sees_all_cases=True)
    W.bob.save_key(make_key("Bob"))
    W.tia = Person("Tia", ai_company_key_allowed=True)
    W.fay = Person("Fay", ai_company_key_allowed=True)
    W.fay.save_key(make_key("Fay"))
    W.vic = Person("Vic", ai_company_key_allowed=True)
    W.vic.save_key(make_key("Vic"))
    W.uma = Person("Uma")
    W.sid = Person("Sid", sees_all_cases=True)
    W.una = Person("Una")
    W.una.save_key(make_key("Una"), probe="network", expect="untested")
    W.ian = Person("Ian")
    W.ian.save_key(make_key("Ian"))
    W.ian.recheck("refuse", "invalid")
    W.quentin = Person("Quentin")
    W.quentin.save_key(make_key("Quentin"))
    W.quentin.recheck("quota", "quota")
    W.uri = Person("Uri")
    W.uri.save_key(make_key("Uri"))
    for name in ("Mia", "Quinn", "Pete", "Rex", "Max"):
        p = Person(name)
        p.save_key(make_key(name))
        setattr(W, name.lower(), p)
    W.dee = Person("Dee")
    r = W.ada.c.patch(f"/users/{W.dee.id}", json={"active": False})
    check(r.status_code == 200, "Dee's account is disabled by the admin", brief(r))
    check(not NET_REFUSED, "nothing tried to reach the internet", str(NET_REFUSED[:3]))
    print(f"  ({len(PROBES)} key checks went to the fake Google)")


def contract() -> None:
    names = ("AiActor", "AiKey", "AiUnavailable", "resolve_keys", "ensure_ai_ready", "record_result")
    missing = [n for n in names if not hasattr(keys, n)]
    need(not missing, "app.ai.keys has " + ", ".join(names), "missing: " + ", ".join(missing))
    AiActor = keys.AiActor

    def resolved(actor):
        try:
            return list(keys.resolve_keys(actor)), None
        except Exception as exc:
            return None, exc

    def summary(ks, err) -> str:
        if err is not None:
            return f"{type(err).__name__}(code={getattr(err, 'code', None)!r}, message={getattr(err, 'message', str(err))!r})"
        return "[" + ", ".join(f"{getattr(k, 'source', '?')}: {who_key(getattr(k, 'value', None))}" for k in ks or []) + "]"

    def sources(ks):
        return [getattr(k, "source", None) for k in ks or []]

    def values(ks):
        return [getattr(k, "value", None) for k in ks or []]

    actor = AiActor.for_user(W.alice.id, "dashboard")
    check(getattr(actor, "user_id", None) == W.alice.id and getattr(actor, "channel", None) == "dashboard",
          "AiActor.for_user(id, 'dashboard') carries that user id and channel", repr(actor))
    before = activity()

    ks, err = resolved(AiActor.for_user(W.alice.id, "dashboard"))
    check(ks and sources(ks) == ["own"] and values(ks) == [W.alice.key],
          "Alice (no tick, working own key): her own key, and no company key", summary(ks, err))
    if ks:
        k = ks[0]
        check(W.alice.key not in repr(k) and W.alice.key not in str(k), "repr() and str() of an AiKey don't show the key",
              f"repr {repr(k)!r}")
        label = str(getattr(k, "label", ""))
        check(label.startswith("own") and label.endswith(W.alice.key[-4:]) and W.alice.key[:-4] not in label,
              "its label looks like 'own ..1234' (the last 4 only)", repr(label))
        check(bool(getattr(k, "fingerprint", None)), "it has a fingerprint")
    ks, err = resolved(AiActor.for_user(W.vic.id, "dashboard"))
    check(ks and sources(ks)[:1] == ["own"] and values(ks)[0] == W.vic.key and len(ks) >= 2
          and set(sources(ks)[1:]) == {"company"} and set(values(ks)[1:]) <= COMPANY,
          "Vic (ticked, working own key): his own key first, then the company keys", summary(ks, err))
    ks, err = resolved(AiActor.for_user(W.tia.id, "dashboard"))
    check(ks and set(sources(ks)) == {"company"} and set(values(ks)) <= COMPANY,
          "Tia (ticked, no own key): the company keys", summary(ks, err))
    if ks:
        labels = [str(getattr(k, "label", "")) for k in ks]
        check(all(re.match(r"company\b", lb) for lb in labels) and not any(v in lb for v in COMPANY for lb in labels),
              "company labels look like 'company #1'", str(labels))
    ks, err = resolved(AiActor.for_user(W.ada.id, "dashboard"))
    check(ks and set(sources(ks)) == {"company"} and set(values(ks)) <= COMPANY,
          "Ada (admin, no own key): the company keys", summary(ks, err))
    ks, err = resolved(AiActor.for_user(W.abe.id, "dashboard"))
    check(ks and values(ks)[:1] == [W.abe.key] and set(values(ks)[1:]) <= COMPANY,
          "Abe (admin with an own key): his own key first", summary(ks, err))
    ks, err = resolved(AiActor.for_user(W.una.id, "dashboard"))
    check(ks and values(ks) == [W.una.key], "Una (no tick, own key untested): her own key counts", summary(ks, err))
    ks, err = resolved(AiActor.cli())
    check(ks and set(values(ks)) == COMPANY and set(sources(ks)) == {"company"},
          "AiActor.cli() (scripts, the bot): the company keys, as today", summary(ks, err))

    for p, code, who in ((W.uma, "ai_key_missing", "Uma (no tick, no key)"),
                         (W.ian, "ai_key_invalid", "Ian (no tick, key refused by Google)"),
                         (W.quentin, "ai_quota_used", "Quentin (no tick, key out of quota)")):
        ks, err = resolved(AiActor.for_user(p.id, "dashboard"))
        check(isinstance(err, keys.AiUnavailable) and getattr(err, "code", None) == code
              and getattr(err, "message", None) == MESSAGES[code],
              f"{who}: AiUnavailable {code}, with the brief's message", summary(ks, err))
    for uid, who in ((W.dee.id, "Dee (account disabled)"), ("nosuchuser00", "an account that doesn't exist")):
        ks, err = resolved(AiActor.for_user(uid, "dashboard"))
        check(isinstance(err, keys.AiUnavailable) and getattr(err, "code", None) == "ai_account_off",
              f"{who}: AiUnavailable ai_account_off", summary(ks, err))

    try:
        keys.ensure_ai_ready(AiActor.for_user(W.alice.id, "dashboard"))
        check(True, "ensure_ai_ready passes for Alice")
    except Exception as exc:
        check(False, "ensure_ai_ready passes for Alice", f"{type(exc).__name__}: {exc}")
    try:
        keys.ensure_ai_ready(AiActor.for_user(W.uma.id, "dashboard"))
        check(False, "ensure_ai_ready raises ai_key_missing for Uma", "it passed")
    except Exception as exc:
        check(getattr(exc, "code", None) == "ai_key_missing", "ensure_ai_ready raises ai_key_missing for Uma",
              f"{type(exc).__name__}: {exc}")
    check(activity() == before, "none of this asked Google anything (no call, no key check)",
          f"{activity() - before} request(s) to the fake Google")

    print("  read fresh every time")
    with engine.begin() as conn:
        conn.execute(text("UPDATE user_ai_keys SET status = 'invalid' WHERE user_id = :id"), {"id": W.alice.id})
    try:
        ks, err = resolved(AiActor.for_user(W.alice.id, "dashboard"))
        check(getattr(err, "code", None) == "ai_key_invalid",
              "Alice's key marked invalid a moment ago: resolve_keys says ai_key_invalid at once", summary(ks, err))
    finally:
        with engine.begin() as conn:
            conn.execute(text("UPDATE user_ai_keys SET status = 'ok' WHERE user_id = :id"), {"id": W.alice.id})
    ks, err = resolved(AiActor.for_user(W.alice.id, "dashboard"))
    check(values(ks) == [W.alice.key], "and back to ok: her key again", summary(ks, err))

    print("  call_structured needs the actor")
    from app.ai import structured

    mark = len(snapshot())
    try:
        structured.call_structured(purpose="contract probe", system_instruction="x", input_parts=[{"type": "text", "text": "x"}],
                                   schema={"type": "object"}, parse=json.loads, models=["gemini-3-flash-preview"])
        check(False, "call_structured(...) without actor= is a TypeError", "it ran")
    except TypeError:
        check(True, "call_structured(...) without actor= is a TypeError")
    except Exception as exc:
        check(False, "call_structured(...) without actor= is a TypeError", f"{type(exc).__name__}: {exc}")
    check(not snapshot(mark), "...and no Gemini call was made", describe(snapshot(mark)))


def alice_run() -> None:
    n = 2 * BATCH_SIZE + 3
    batches = -(-n // BATCH_SIZE)
    W.a_rid = new_draft(W.alice, n)
    W.b_rid = new_draft(W.bob, n)
    mark = len(snapshot())
    ra = W.alice.c.post(f"/intake/{W.a_rid}/process", json={"mode": "all"})
    rb = W.bob.c.post(f"/intake/{W.b_rid}/process", json={"mode": "all"})
    need(ra.status_code == 200, "Alice (no tick, working own key) presses Process on her draft -> 200", brief(ra))
    check(rb.status_code == 200, "Bob (own key, no company tick) presses Process on his own draft at the same time -> 200", brief(rb))
    sa = wait_done(W.alice, W.a_rid)
    sb = wait_done(W.bob, W.b_rid) if rb.status_code == 200 else {}
    need(sa.get("stage") == "ready", "Alice's run finishes: ready", stage_line(sa))

    calls = calls_for(W.a_rid, mark)
    sort = [c for c in calls if c["purpose"] == "sort"]
    read = [c for c in calls if c["purpose"] == "draft"]
    check(len(sort) == batches and all(c["outcome"] == "ok" for c in sort),
          f"her {n} photos were sorted in {batches} calls", describe(sort))
    check(read and all(c["outcome"] == "ok" for c in read), "and the case was read (the reading step)", describe(read))
    check(all_on(calls, {W.alice.key}), f"EVERY one of the run's {len(calls)} Gemini calls used Alice's own key",
          off_key(calls, {W.alice.key}))
    pools = sorted({c["pool"][0] for c in sort if c["pool"]})
    check(len(pools) >= 2, f"the sort calls came from {len(pools)} sort-pool threads (the pool really ran)", str(pools))
    check(all_on(sort, {W.alice.key}), "every sort-pool thread's call used her key", off_key(sort, {W.alice.key}))
    check(all_on(read, {W.alice.key}), "the reading step used her key", off_key(read, {W.alice.key}))
    if sort:
        print(f"  (sort calls from {', '.join(pools)}; reading from {' <- '.join(read[0]['lineage'][:3]) if read else '-'})")
    check(not any(c["implicit"] for c in calls), "no call went through a client made without a key")
    check("error_code" in sa and sa.get("error_code") is None, "GET /intake/{id} has error_code, null after a good run", stage_line(sa))
    row = ai_key_row(W.alice.id)
    check(row is not None and row.last_used_at is not None, "a success touches last_used_at on her own key",
          f"last_used_at {getattr(row, 'last_used_at', None)!r}")
    check(W.alice.key_status().get("status") == "ok", "her key's status is still ok")

    print("  Bob's run, at the same time")
    if rb.status_code == 200:
        check(sb.get("stage") == "ready", "Bob's run finishes: ready", stage_line(sb))
        bcalls = calls_for(W.b_rid, mark)
        check(all_on(bcalls, {W.bob.key}), f"every one of his run's {len(bcalls)} calls used Bob's own key (none Alice's or the company's)",
              off_key(bcalls, {W.bob.key}))
        if calls and bcalls:
            overlap = min(c["at"] for c in calls) < max(c["at"] for c in bcalls) and min(c["at"] for c in bcalls) < max(c["at"] for c in calls)
            print(f"  (the two runs {'overlapped' if overlap else 'did not overlap'} in time)")
    stray = [c for c in snapshot(mark) if c["case"] not in (W.a_rid, W.b_rid)]
    check(not stray, "no other Gemini call was made meanwhile", describe(stray))


def others_press() -> None:
    rid = getattr(W, "a_rid", None)
    need(rid, "Alice's draft from the previous section is there")
    print("  a Google outage (503) on Alice's key: fails as today, and isn't blamed on the key")
    add_photos(W.alice, rid, 3)
    MODE[W.alice.key] = "server"
    try:
        r, mark = press(W.alice, rid)
        need(r.status_code == 200, "Alice presses Process -> 200", brief(r))
        s = wait_done(W.alice, rid)
    finally:
        MODE.pop(W.alice.key, None)
    calls = calls_for(rid, mark)
    check(s.get("stage") == "failed", "the run fails (Google 503 on every model)", stage_line(s))
    check("error_code" in s and s.get("error_code") is None, "error_code stays null: an outage isn't a key problem", stage_line(s))
    check(W.alice.key_status().get("status") == "ok", "her key is still ok (not invalid, not quota)", str(W.alice.key_status().get("status")))
    check(all_on(calls, {W.alice.key}), "it was tried on her key only (no tick: no company fallback)", off_key(calls, {W.alice.key}))

    print("  Abe, an admin with an own key, presses Retry on Alice's draft")
    s, calls = run(W.abe, rid, "an admin may edit any draft: Abe presses Retry")
    check(s.get("stage") == "ready", "his Retry finishes: ready", stage_line(s))
    check(any(c["purpose"] == "sort" for c in calls) and any(c["purpose"] == "draft" for c in calls),
          "it sorted the 3 new photos and read the case", describe(calls))
    check(all_on(calls, {W.abe.key}), "every call of Abe's Retry used ABE's own key: the presser's, not Alice's (who started the case)",
          off_key(calls, {W.abe.key}))

    print("  Ada, an admin without an own key, presses Process on Alice's draft")
    add_photos(W.alice, rid, 3)
    s, calls = run(W.ada, rid, "Ada presses Process")
    check(s.get("stage") == "ready", "it finishes: ready", stage_line(s))
    check(any(c["purpose"] == "sort" for c in calls) and any(c["purpose"] == "draft" for c in calls),
          "it sorted the 3 new photos and read the case", describe(calls))
    check(all_on(calls, COMPANY), "every call used the company keys (Ada's resolution), never Alice's key", off_key(calls, COMPANY))

    print("  Bob sees all cases, but a draft is its owner's")
    before = activity()
    r = W.bob.c.post(f"/intake/{rid}/process", json={"mode": "all"})
    check(r.status_code == 403, "Bob pressing Retry on Alice's draft -> 403", brief(r))
    check(activity() == before, "...and no Gemini call")

    print("  Annex A 'Ask AI' on Alice's filed case: whoever asks pays")
    W.a_case = filed_case(W.alice)
    for p, allowed, who in (
        (W.alice, {W.alice.key}, "Alice, the owner: her own key"),
        (W.bob, {W.bob.key}, "Bob (sees all cases, so he may edit a filed case): HIS own key, not Alice's"),
        (W.abe, {W.abe.key}, "Abe (admin with an own key): his own key, not Alice's"),
        (W.ada, COMPANY, "Ada (admin, no own key): the company keys, not Alice's"),
    ):
        r, calls = annex(p, W.a_case)
        check(r.status_code == 200 and all_on(calls, allowed), who, f"{brief(r)} | {off_key(calls, allowed)}")


def ticked() -> None:
    print("  Tia: ticked for the company key, no own key")
    rid = new_draft(W.tia, 3)
    s, calls = run(W.tia, rid, "Tia presses Process")
    check(s.get("stage") == "ready", "it finishes: ready", stage_line(s))
    check(all_on(calls, COMPANY), "every call used the company keys", off_key(calls, COMPANY))

    print("  Vic: ticked, with a working own key")
    rid = new_draft(W.vic, 3)
    s, calls = run(W.vic, rid, "Vic presses Process")
    check(s.get("stage") == "ready", "it finishes: ready", stage_line(s))
    check(all_on(calls, {W.vic.key}), "every call used his own key, not the company's (own key first)", off_key(calls, {W.vic.key}))

    print("  Fay: ticked; Google refuses her own key (400 API_KEY_INVALID)")
    rid = new_draft(W.fay, 3)
    MODE[W.fay.key] = "refuse"
    try:
        s, calls = run(W.fay, rid, "Fay presses Process")
        check(s.get("stage") == "ready", "the run still finishes: ready (the company key took over)", stage_line(s))
        check("error_code" in s and s.get("error_code") is None, "no error_code: the run didn't stop", stage_line(s))
        check(calls and calls[0]["key"] == W.fay.key, "her own key was tried first", describe(calls[:1]))
        own_sort = [c for c in calls if c["key"] == W.fay.key and c["purpose"] == "sort"]
        own_read = [c for c in calls if c["key"] == W.fay.key and c["purpose"] == "draft"]
        check(len(own_sort) == 1, "once: after the refusal the call moved on to the next key, not the next model on her key",
              f"{len(own_sort)} sort call(s) on her key")
        check(not own_read, "the reading step didn't try her refused key again", describe(own_read))
        rest = [c for c in calls if c["key"] != W.fay.key]
        check(all_on(rest, COMPANY) and all(c["outcome"] == "ok" for c in rest), "everything else ran on the company keys",
              off_key(rest, COMPANY))
        st = W.fay.key_status()
        check(st.get("status") == "invalid", "the refusal is stored: her key's status is now invalid", str(st.get("status")))
        check(st.get("source") == "company" and st.get("ai_available") is True,
              "GET /auth/me/ai-key: AI available, on the company key", f"source {st.get('source')!r}, ai_available {st.get('ai_available')!r}")
        print("  Fay again: her failing key isn't used at all")
        add_photos(W.fay, rid, 2)
        s, calls = run(W.fay, rid, "Fay presses Process again")
        check(s.get("stage") == "ready", "it finishes: ready", stage_line(s))
        check(all_on(calls, COMPANY), "every call used the company keys; none her invalid key", off_key(calls, COMPANY))
    finally:
        MODE.pop(W.fay.key, None)


def more_paths() -> None:
    print("  Una: no tick, own key saved while Google couldn't be asked (untested)")
    rid = new_draft(W.una, 3)
    s, calls = run(W.una, rid, "Una presses Process")
    check(s.get("stage") == "ready", "it finishes: ready", stage_line(s))
    check(all_on(calls, {W.una.key}), "every call used her own key", off_key(calls, {W.una.key}))

    print("  POST /reports/analyze-photos (the report form's Analyze)")
    for p, allowed, who in ((W.alice, {W.alice.key}, "Alice (no tick, own key): her own key"),
                            (W.tia, COMPANY, "Tia (ticked, no key): the company keys"),
                            (W.abe, {W.abe.key}, "Abe (admin, own key): his own key")):
        r, calls, _ = analyze(p)
        check(r.status_code == 200 and all_on(calls, allowed), who, f"{brief(r)} | {off_key(calls, allowed)}")


def no_ai() -> None:
    for p, code, who in ((W.uma, "ai_key_missing", "Uma: no tick, no key"),
                         (W.ian, "ai_key_invalid", "Ian: no tick, his key refused by Google (stored invalid)"),
                         (W.quentin, "ai_quota_used", "Quentin: no tick, his key out of quota (stored quota)")):
        print(f"  {who}")
        rid = new_draft(p, 2)
        before = stage_of(p, rid)
        act = activity()
        r = p.c.post(f"/intake/{rid}/process", json={"mode": "all"})
        body = jd(r)
        check(r.status_code == 409 and body.get("code") == code, f"POST /intake/{{id}}/process -> 409 {code}", brief(r))
        check(body.get("detail") == MESSAGES[code], "...detail is the brief's message", repr(body.get("detail")))
        check(activity() == act, "...and no Gemini call (nor a key check)", f"{activity() - act} request(s) to the fake Google")
        _real_sleep(0.3)
        after = stage_of(p, rid)
        check(after == before, "the stage is unchanged", f"{before} -> {after}")
        check(activity() == act, "and nothing started in the background")
        r, calls, saved = analyze(p)
        check(r.status_code == 409 and jd(r).get("code") == code and not calls,
              f"POST /reports/analyze-photos -> 409 {code}, no Gemini call", f"{brief(r)} | {describe(calls)}")
        check(not saved, "...checked before any photo is saved", f"{len(saved)} new file(s) in storage/tmp")
        check(p.me().get("ai_allowed") is False, "GET /auth/me: ai_allowed false", str(p.me().get("ai_allowed")))
    print("  Annex A 'Ask AI' on Uma's own filed case")
    case = filed_case(W.uma)
    r, calls = annex(W.uma, case)
    check(r.status_code == 409 and jd(r).get("code") == "ai_key_missing" and not calls,
          "-> 409 ai_key_missing, no Gemini call", f"{brief(r)} | {describe(calls)}")


def unreadable() -> None:
    path = SECRETS / "ai-keys.secret"
    need(path.is_file(), "the AI key secret file is there")
    good = path.read_bytes()
    rid = new_draft(W.uri, 2)
    v_rid = new_draft(W.vic, 2)
    state = None
    try:
        path.write_bytes(Fernet.generate_key())  # a valid secret, but the wrong one
        state = restart_secretbox()
        need(state != "ok", "the secret file swapped for a wrong one: key storage not ok", state)
        before = stage_of(W.uri, rid)
        act = activity()
        r = W.uri.c.post(f"/intake/{rid}/process", json={"mode": "all"})
        body = jd(r)
        check(r.status_code == 409 and body.get("code") == "ai_key_unreadable",
              "Uri (no tick, own key): POST /intake/{id}/process -> 409 ai_key_unreadable", brief(r))
        check(body.get("detail") == MESSAGES["ai_key_unreadable"], "...detail is the brief's message", repr(body.get("detail")))
        check(activity() == act, "...and no Gemini call")
        check(stage_of(W.uri, rid) == before, "the stage is unchanged")
        check(W.uri.me().get("ai_allowed") is False, "GET /auth/me: ai_allowed false for Uri")
        print("  Vic (ticked, own key) meanwhile: the company keys")
        s, calls = run(W.vic, v_rid, "Vic presses Process")
        check(s.get("stage") == "ready", "it finishes: ready", stage_line(s))
        check(all_on(calls, COMPANY), "every call used the company keys (his own can't be read)", off_key(calls, COMPANY))
        check(W.vic.me().get("ai_allowed") is True, "GET /auth/me: ai_allowed true for Vic")
    finally:
        path.write_bytes(good)
        state = restart_secretbox()
    check(state == "ok", "the right secret back: key storage ok again", state)


def route_order() -> None:
    a_rid = new_draft(W.alice, 1)
    case = filed_case(W.alice)
    act = activity()
    r = W.uma.c.post(f"/intake/{a_rid}/process", json={"mode": "all"})
    check(r.status_code == 404, "Uma (no AI) on Alice's draft, which she can't see -> 404, not 409 (the view gate first)", brief(r))
    r = W.sid.c.post(f"/intake/{a_rid}/process", json={"mode": "all"})
    check(r.status_code == 403, "Sid (sees all cases, no AI) on Alice's draft -> 403, not 409 (the edit gate next)", brief(r))
    r = W.uma.c.post("/intake")
    need(r.status_code == 200, "Uma starts a draft and uploads nothing", brief(r))
    empty = r.json()["id"]
    r = W.uma.c.post(f"/intake/{empty}/process", json={"mode": "all"})
    check(r.status_code == 400, "Uma presses Process with no photos and no notes -> 400, not 409 (photos before AI)", brief(r))
    r, _ = annex(W.uma, case)
    check(r.status_code == 404, "Uma asks AI on Alice's filed case -> 404", brief(r))
    r, _ = annex(W.sid, case)
    check(r.status_code == 409 and jd(r).get("code") == "ai_key_missing",
          "Sid may edit Alice's filed case but has no AI -> 409 ai_key_missing (seeing all cases isn't the company key)", brief(r))
    check(activity() == act, "none of these asked Google anything", f"{activity() - act} request(s) to the fake Google")


def refused_mid_run() -> None:
    n = 2 * BATCH_SIZE + 3
    rid = new_draft(W.mia, n)
    # The first call on her key works; then Google suspends it (403, quoting the key).
    AFTER[W.mia.key] = (1, "suspended")
    try:
        s, calls = run(W.mia, rid, "Mia (no tick, own key) presses Process")
    finally:
        AFTER.pop(W.mia.key, None)
    check(s.get("stage") == "failed", "the run stops: failed", stage_line(s))
    check(s.get("error_code") == "ai_key_invalid", "GET /intake/{id}: error_code ai_key_invalid", stage_line(s))
    st = W.mia.key_status()
    check(st.get("status") == "invalid", "her key's stored status is now invalid", str(st.get("status")))
    kept = sorted_count(s)
    check(0 < kept < n, f"photos sorted before the refusal are kept ({kept} of {n})", f"{kept} of {n} sorted")
    check(all_on(calls, {W.mia.key}), "only her key was used (no tick: no company fallback)", off_key(calls, {W.mia.key}))
    check(not any(c["purpose"] == "draft" for c in calls), "the reading step never ran", describe(calls))
    check(W.mia.me().get("ai_allowed") is False, "GET /auth/me: ai_allowed false now")

    print("  Mia saves a new key and presses Retry")
    W.mia.save_key(make_key("MiaTwo"))
    r, mark = press(W.mia, rid)
    body = jd(r)
    check(r.status_code == 200 and "error_code" in body and body.get("error_code") is None,
          "Retry -> 200, and error_code is cleared on the new start", brief(r))
    if r.status_code == 200:
        s = wait_done(W.mia, rid)
        calls = calls_for(rid, mark)
        check(s.get("stage") == "ready" and s.get("error_code") is None, "it finishes: ready, error_code null", stage_line(s))
        check(all_on(calls, {W.mia.key}), "every call used her new key", off_key(calls, {W.mia.key}))
        resorted = sum(c["images"] for c in calls if c["purpose"] == "sort")
        check(resorted == n - kept, f"only the {n - kept} photos left unsorted were sorted again", f"{resorted} sent for sorting")


def quota_mid_run() -> None:
    rid = new_draft(W.quinn, 3)
    # Sorting works; the reading step gets Google's daily-quota 429.
    ON_PURPOSE[(W.quinn.key, "draft")] = "quota"
    try:
        s, calls = run(W.quinn, rid, "Quinn (no tick, own key) presses Process")
    finally:
        ON_PURPOSE.pop((W.quinn.key, "draft"), None)
    check(any(c["purpose"] == "draft" and c["outcome"] == "quota" for c in calls), "(Google said: daily quota used up)", describe(calls))
    check(s.get("stage") == "failed", "the run stops: failed", stage_line(s))
    check(s.get("error_code") == "ai_quota_used", "GET /intake/{id}: error_code ai_quota_used", stage_line(s))
    check(W.quinn.key_status().get("status") == "quota", "her key's stored status is now quota", str(W.quinn.key_status().get("status")))
    check(sorted_count(s) == 3, "her sorted photos are kept (3 of 3)", f"{sorted_count(s)} of 3")
    check(all_on(calls, {W.quinn.key}), "only her key was used", off_key(calls, {W.quinn.key}))


def minute_limit() -> None:
    rid = new_draft(W.pete, 3)
    ONCE[W.pete.key] = "minute"
    s, calls = run(W.pete, rid, "Pete (no tick, own key) presses Process; the first call gets a per-minute 429")
    ONCE.pop(W.pete.key, None)
    check(s.get("stage") == "ready" and s.get("error_code") is None, "it finishes: ready, no error_code", stage_line(s))
    check(W.pete.key_status().get("status") == "ok", "his key is still ok: a per-minute 429 isn't the daily quota",
          str(W.pete.key_status().get("status")))
    check(all_on(calls, {W.pete.key}), "every call used his key", off_key(calls, {W.pete.key}))


def unauth_mid_run() -> None:
    rid = new_draft(W.pete, 2)
    MODE[W.pete.key] = "unauth"
    try:
        s, calls = run(W.pete, rid, "Pete presses Process; Google now answers 401 for his key")
    finally:
        MODE.pop(W.pete.key, None)
    check(s.get("stage") == "failed" and s.get("error_code") == "ai_key_invalid",
          "the run stops: failed, error_code ai_key_invalid", stage_line(s))
    check(W.pete.key_status().get("status") == "invalid", "his key's stored status is now invalid",
          str(W.pete.key_status().get("status")))
    check(all_on(calls, {W.pete.key}), "only his key was used", off_key(calls, {W.pete.key}))


def removed_mid_run() -> None:
    n = BATCH_SIZE + 1  # two sort batches, then reading
    rid = new_draft(W.rex, n)
    tab = W.rex.other_tab()
    seen = {"sort": 0}
    removed: list[int] = []
    lock = threading.Lock()

    def remove_on_last_sort(record):
        if record["purpose"] != "sort":
            return
        with lock:
            seen["sort"] += 1
            last = seen["sort"] == 2
        if last:
            # Rex removes his key in another tab while the AI is sorting.
            removed.append(tab.c.delete("/auth/me/ai-key").status_code)

    HOOKS[W.rex.key] = remove_on_last_sort
    try:
        s, calls = run(W.rex, rid, "Rex (no tick, own key) presses Process")
    finally:
        HOOKS.pop(W.rex.key, None)
    check(removed == [200], "(Rex removed his key in another tab during the last sort call)", str(removed))
    check(s.get("stage") == "failed", "the run stops: failed", stage_line(s))
    check(s.get("error_code") == "ai_key_missing", "GET /intake/{id}: error_code ai_key_missing (the key is looked up again)", stage_line(s))
    check(sorted_count(s) == n, f"the photos sorted before are kept ({n} of {n})", f"{sorted_count(s)} of {n}")
    check(not any(c["purpose"] == "draft" for c in calls), "the reading step made no Gemini call with the removed key", describe(calls))
    check(all_on(calls, {W.rex.key}), "no call used the company keys", off_key(calls, {W.rex.key}))


def replaced_mid_run() -> None:
    rid = new_draft(W.max, 3)
    old = W.max.key
    new = make_key("MaxTwo")
    SENT_KEYS.add(new)
    KEY_LABEL[new] = "Max's new key"
    PROBE_MODE[new] = "ok"
    tab = W.max.other_tab()
    replaced: list[int] = []

    def replace_then_refuse(record):
        if replaced:
            return
        # Max saves a new key in another tab while Google is refusing the old one.
        reset_key_limits()
        replaced.append(tab.c.put("/auth/me/ai-key", json={"api_key": new, "billing_confirmed": True}).status_code)

    MODE[old] = "unauth"
    HOOKS[old] = replace_then_refuse
    try:
        r, mark = press(W.max, rid)
        need(r.status_code == 200, "Max (no tick, own key) presses Process -> 200", brief(r))
        s = wait_done(W.max, rid)
    finally:
        MODE.pop(old, None)
        HOOKS.pop(old, None)
    check(replaced == [200], "(the new key was saved while Google refused the old one with a 401)", str(replaced))
    st = W.max.key_status()
    check(st.get("last4") == new[-4:] and st.get("status") == "ok",
          "the old key's verdict isn't stamped on the new key: still ok", f"status {st.get('status')!r}, last4 matches new: {st.get('last4') == new[-4:]}")
    print(f"  (that run ended {s.get('stage')!r}, error_code {s.get('error_code')!r}; either is fine)")


def channels() -> None:
    import app.channels.telegram as tg
    from app.conversation import state as conv

    print("  Telegram: drafting as the bot does it (draft_and_reply -> build_draft)")
    colour = new_colour("telegram")
    photo = tmp_dir() / f"tg-{uuid.uuid4().hex}.jpg"
    photo.write_bytes(photo_bytes(colour, 0))
    chat = "990001"
    conv.reset_session(chat)
    session = conv.get_session(chat)
    session.photo_paths.append(str(photo))

    class _Message:
        async def reply_text(self, text_, **_kw):
            BOT_TEXTS.append(str(text_))

    msg = _Message()
    update = SimpleNamespace(message=msg, callback_query=None, effective_message=msg,
                             effective_chat=SimpleNamespace(id=int(chat)), effective_user=SimpleNamespace(id=int(chat)))
    mark = len(snapshot())
    texts_before = len(BOT_TEXTS)
    asyncio.run(tg.draft_and_reply(update, session, "Rear-ended at PIE exit 12, rear bumper dented."))
    calls = snapshot(mark)
    replies = BOT_TEXTS[texts_before:]
    check(session.draft is not None and not any("went wrong" in t for t in replies),
          "the bot drafted the report", " | ".join(replies)[:200])
    check(all_on(calls, COMPANY), "every call used the company keys (AiActor.cli(): as today, until P5)", off_key(calls, COMPANY))

    print("  WhatsApp: no linked account, so no AI")
    wa_photo = tmp_dir() / f"wa-{uuid.uuid4().hex}.jpg"
    wa_photo.write_bytes(photo_bytes(colour, 1))
    sender = "+6590000001"
    wa_chat = f"whatsapp:{sender}"
    conv.reset_session(wa_chat)
    wa_session = conv.get_session(wa_chat)
    wa_session.photo_paths.append(str(wa_photo))
    wa_session.template_sent = True
    act = activity()
    # Signed in as an admin, who has AI on the dashboard: WhatsApp still has none.
    r = W.ada.c.post("/whatsapp/webhook", data={"From": sender, "Body": "Rear-ended at the Jurong carpark.", "NumMedia": "0"})
    check(r.status_code != 404, "(the webhook is mounted)", brief(r))
    check(r.status_code == 200 and "<Message>" in r.text, "a polite reply: 200 with a TwiML message", brief(r))
    check(activity() == act, "no Gemini call", f"{activity() - act} request(s) to the fake Google")
    check(wa_session.draft is None, "no draft was made")

    print("  /setup/test/llm (admin) keeps testing the company keys")
    before = len(PROBES)
    r = W.abe.c.post("/setup/test/llm")
    probed = PROBES[before:]
    check(r.status_code == 200 and jd(r).get("ok") is True, "Abe (admin with an own key): POST /setup/test/llm -> ok", brief(r))
    check(probed and set(probed) <= COMPANY, "it checked the company keys, not his own",
          ", ".join(who_key(k) for k in probed) or "no key checked")


def me_allowed() -> None:
    cases = (
        (W.ada, True, "an admin"),
        (W.abe, True, "an admin with an own key"),
        (W.tia, True, "a user ticked for the company key, no own key"),
        (W.fay, True, "a ticked user whose own key fails (company fallback)"),
        (W.alice, True, "a user with a working own key, no tick"),
        (W.una, True, "a user whose own key is untested, no tick"),
        (W.uma, False, "a user with no key and no tick"),
        (W.sid, False, "a user ticked only to see all cases, no key"),
        (W.ian, False, "a user whose own key is invalid, no tick"),
        (W.quentin, False, "a user whose own key is out of quota, no tick"),
    )
    disagree = []
    for p, want, who in cases:
        got = p.me().get("ai_allowed")
        check(got is want, f"{who}: ai_allowed {str(want).lower()}", repr(got))
        available = p.key_status().get("ai_available")
        if available is not got:
            disagree.append(f"{p.label}: ai_allowed {got!r}, ai_available {available!r}")
    check(not disagree, "GET /auth/me/ai-key's ai_available agrees for each of them", "; ".join(disagree))


def leaks() -> None:
    lines = [line for line in LOG_LINES if any(k in line for k in SENT_KEYS)]
    sample = mask(lines[0])[:300] if lines else ""
    check(not lines, f"no key in any of the {len(LOG_LINES)} log lines (captured at DEBUG)",
          f"{len(lines)} line(s), e.g. {sample}")
    bodies = [b for b in RESPONSES if any(k in b for k in SENT_KEYS)]
    check(not bodies, f"no key in any of the {len(RESPONSES)} API responses",
          f"{len(bodies)} response(s), e.g. {mask(bodies[0])[:300]}" if bodies else "")
    check(not any(k in t for t in BOT_TEXTS for k in SENT_KEYS), "no key in any bot reply")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA wal_checkpoint(FULL)"))
    blobs = b"".join(p.read_bytes() for p in _tmp.iterdir() if p.is_file() and p.name.startswith("test.db"))
    own = [k for k in SENT_KEYS - COMPANY if k.encode() in blobs]
    check(not own, "no own key in the SQLite files (error columns included)", f"{len(own)} found: {', '.join(who_key(k) for k in own)}")
    keyless = [c for c in CLIENTS if not c["given"]]
    check(not keyless, f"none of the {len(CLIENTS)} Gemini clients was made without a key (the SDK would use GEMINI_API_KEY)",
          f"{len(keyless)} made without one")
    unknown = [c for c in snapshot() if c["key"] not in SENT_KEYS]
    check(not unknown, f"all {len(snapshot())} Gemini calls used a key the test knows", describe(unknown))
    check(not NET_REFUSED, "nothing tried to reach the internet", "; ".join(NET_REFUSED[:3]))
    check(not any(b.startswith("Internal Server Error") for b in RESPONSES), "no response was a 500")
    check(not HOOK_ERRORS, "the test's own hooks ran cleanly", "; ".join(HOOK_ERRORS[:3]))


def main() -> int:
    for name, fn in (
        ("setup: people, company keys, own keys (P3)", setup),
        ("contract: app.ai.keys AiActor, AiKey, AiUnavailable, resolve_keys", contract),
        ("who pays: Alice's intake run, every call and thread (Bob's at the same time)", alice_run),
        ("who pays: someone else presses Retry, or asks AI on Alice's case", others_press),
        ("who pays: ticked users -- no own key, a working one, a failing one", ticked),
        ("who pays: an untested own key; the report form's Analyze", more_paths),
        ("no AI: 409 with the right code, no Gemini call, stage unchanged", no_ai),
        ("no AI: the key storage can't be read", unreadable),
        ("order of checks: 404, then 403, then 400, then 409", route_order),
        ("failures: own key refused mid-run", refused_mid_run),
        ("failures: daily quota at the reading step", quota_mid_run),
        ("failures: a per-minute 429 is not the quota", minute_limit),
        ("failures: a 401 marks the key invalid too", unauth_mid_run),
        ("failures: own key removed mid-run", removed_mid_run),
        ("failures: own key replaced while Google refuses the old one", replaced_mid_run),
        ("the Telegram bot, WhatsApp, the admin's company-key test", channels),
        ("GET /auth/me ai_allowed", me_allowed),
        ("leaks: no key in logs, responses or the database", leaks),
    ):
        run_section(name, fn)
        if name.startswith("setup") and FAILURES:
            break
    try:
        W.boot.__exit__(None, None, None)
    except Exception:
        pass
    print(f"\ntest_own_key_ai: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  [{section}] {label}")
    if FAILURES:
        return 1
    print("\nALL OWN-KEY AI CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
