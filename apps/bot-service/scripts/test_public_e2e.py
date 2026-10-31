"""Independent end-to-end test for release P6, "public reports through the
Telegram bot" (agent P6-TEST).

    python scripts/test_public_e2e.py

Written from the spec, not from the code under test:
plans/2026-10-30-public-reporter/p6_build_brief.md (sections 1-3, and 2.8
"TEST"), p6_decisions.md (wins where it differs from the brief: the contact
line and DPO are admin settings, empty by default; company_key_billed), and
agentA_bot_report.md for the bot conversation's detail.

One stranger's whole journey, then the checks around it:
  1. an unlinked Telegram (private chat) sends a photo, gets the welcome and
     consent, agrees, gives name and role, shares their own contact, the
     plate, when and where, what happened, 3 photos (a file in between is
     refused), reviews and Submits;
  2. the reply holds a TG-YYMMDD-XXXX ref, no /reports/ link, no document;
  3. GET /inbox lists it for an admin and for see-all staff; a plain user
     gets 403, and 404 on /inbox/{id} and the photo under /files;
  4. before anyone takes it, an admin's PUT /intake/{id}/data, POST
     /process and DELETE /reports/{id} are all 403, and Gemini is not called;
  5. staff S takes it (a second take is 409 inbox_taken) and presses Start.
     The fake Gemini records every request: the sender's name and phone are
     in none, the statement is in one. With company_key_billed false, S on
     the company tick alone gets 409 ai_key_missing, S with an own key runs
     on that key; with company_key_billed true, the company key is allowed;
  6. finalize: filed_at set, telegram_* cleared, reporter_chat_id "public",
     the PDF HTML escapes a name holding <b>x</b>, and no audit entry holds
     a sender's name or phone;
  7. other bot rules: typed numbers and other people's or forwarded contact
     cards are refused, a file is refused, a linked staff member still gets
     the P5 flow, /withdraw (untaken: deleted; taken: request recorded), a
     forged withdraw callback deletes nothing, a Spam discard with
     block_sender blocks that Telegram only;
  8. caps: 3 open per sender, 3 per phone, 50 open in total ("can't take
     new reports"); the 50 are seeded straight into the database.

How it drives the system (learnt from test_telegram_link_e2e.py,
test_own_key_ai.py, test_ai_keys.py, test_case_access.py):
- the real HTTP API through TestClient, people made in the database and
  signed in with a session cookie;
- the real bot, app.channels.telegram.build_app(), fed the JSON Telegram
  would send (Update.de_json) and dispatched as python-telegram-bot does;
  a recording fake bot stands in for Telegram;
- google.genai.Client replaced by a fake that records the key and the full
  text of every request (image bytes left out);
- renderer.html_to_pdf replaced by one that keeps the HTML and writes a
  stub PDF (no Chromium).

The test paces itself well under the brief's bot limits (per-sender burst
30 at 1/s, public replies 8/s burst 20, the 5 s welcome throttle), so a
"slow down" is never the test's own doing.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database; every outside connection and DNS lookup is refused; the keys and
the bot token are made up.

Failures are assertion lines grouped by section. A section stops at its
first missing prerequisite ("rest of this section skipped"), never with a
crash in the test's own setup. Until P6-CORE, BOT and API are finished most
sections fail: that is expected.
"""
import asyncio
import hashlib
import io
import json
import logging
import os
import random
import re
import socket
import sys
import tempfile
import threading
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
_tmp = Path(tempfile.mkdtemp(prefix="public-e2e-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
SECRETS = (_tmp / "secrets").resolve()
DB_FILE = (_tmp / "test.db").resolve()


def make_key(label: str) -> str:
    """A made-up key shaped like a Gemini one, different for each label."""
    return ("AIzaSy" + label + "x" * 40)[:35] + hashlib.md5(label.encode()).hexdigest()[:4]


ENV_COMPANY_KEY = make_key("CompanyEnv")
BOT_USERNAME = "carlink_public_e2e_bot"
BOT_ID = 7000000002
BOT_TOKEN = f"{BOT_ID}:AAP6-fake-bot-token-that-must-never-be-logged"
PUBLIC_BASE_URL = "https://carlink-p6.example"

# Forced, not setdefault: never a real folder, key or token from the environment.
os.environ.update({
    "STORAGE_DIR": str(STORAGE),
    "SECRETS_DIR": str(SECRETS),
    "DATABASE_URL": f"sqlite:///{DB_FILE.as_posix()}",
    "GEMINI_API_KEY": ENV_COMPANY_KEY,
    "RUN_BACKGROUND_CLEANUP": "false",
    "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
    "TELEGRAM_ALLOWED_IDS": "",
    "PUBLIC_BASE_URL": PUBLIC_BASE_URL,
    "TWILIO_ACCOUNT_SID": "",
    "TWILIO_AUTH_TOKEN": "",
    "GEMINI_MAX_REQUESTS_PER_MINUTE": "100000",
    "GEMINI_MIN_CALL_INTERVAL_SECONDS": "0",
    # The general disk guard is not what this test is about (the public
    # flow still needs its own 2 GB of headroom on top).
    "MIN_FREE_DISK_GB": "0",
})
for _var in ("GOOGLE_API_KEY", "TELEGRAM_BOT_USERNAME", "PUBLIC_CONTACT", "PUBLIC_CONTACT_PHONE"):
    os.environ.pop(_var, None)

# ------------------------------------------------------------ every log record
LOG_RECORDS: list[tuple[int, str, str]] = []  # (level, logger, message)


class _Keep(logging.Handler):
    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        if record.exc_info and record.exc_info[1] is not None:
            msg += " | " + "".join(traceback.format_exception_only(record.exc_info[0], record.exc_info[1])).strip()
        LOG_RECORDS.append((record.levelno, record.name, msg))


logging.getLogger().addHandler(_Keep(level=logging.DEBUG))
logging.getLogger().setLevel(logging.DEBUG)

# ------------------------------------------------------------ no outside network
NET_REFUSED: list[str] = []
_LOCAL = (None, "", "127.0.0.1", "localhost", "::1", "testserver")
_real_connect = socket.socket.connect
_real_getaddrinfo = socket.getaddrinfo


def _offline_connect(sock, address):
    host = address[0] if isinstance(address, tuple) and address else address
    if host not in _LOCAL:
        NET_REFUSED.append(str(host))
        raise ConnectionRefusedError(f"test is offline: refused a connection to {host!r}")
    return _real_connect(sock, address)


def _offline_getaddrinfo(host, *args, **kwargs):
    name = host.decode() if isinstance(host, bytes) else host
    if name not in _LOCAL:
        NET_REFUSED.append(str(name))
        raise socket.gaierror(f"test is offline: refused to look up {name!r}")
    return _real_getaddrinfo(host, *args, **kwargs)


socket.socket.connect = _offline_connect
socket.getaddrinfo = _offline_getaddrinfo

_real_sleep = time.sleep


def _quick_sleep(seconds):
    # A rate limiter waiting for a slot in a worker thread: shortened.
    if seconds > 0.2 and threading.current_thread() is not threading.main_thread():
        seconds = 0.01
    _real_sleep(seconds)


time.sleep = _quick_sleep

# ================================================================ fake Google
from PIL import Image  # noqa: E402

_fake_lock = threading.Lock()
CALLS: list[dict] = []   # one per generation call: key, purpose, every string it was given
PROBES: list[str] = []   # the key of every models.list (a key check)

DRAFT_ANSWER = {
    "description": "Insured vehicle was rear-ended.",
    "category": ["Vehicle Collision or Damage"],
    "damaged_parts": ["Rear Bumper"],
    "damage_summary": [{"part": "Rear Bumper", "severity": "Moderate", "ai_confidence": "High"}],
    "vehicle_info": {"make": "Honda", "model": "Vezel", "plate_number": "SNA1234B"},
}


def _parts(kw: dict) -> list:
    parts = kw.get("input") if kw.get("input") is not None else kw.get("contents")
    return [p for p in parts if isinstance(p, dict)] if isinstance(parts, list) else []


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


def _strings(obj, out: list, depth: int = 0) -> None:
    """Every string a request carried, image bytes left out."""
    if depth > 12:
        return
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        is_image = str(obj.get("type", "")).lower() in ("image", "image_url", "inline_data")
        for k, v in obj.items():
            if is_image and k in ("data", "uri", "url"):
                continue
            if k in ("inline_data", "data") and isinstance(v, (bytes, bytearray)):
                continue
            _strings(v, out, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _strings(v, out, depth + 1)
    elif hasattr(obj, "model_dump"):
        try:
            _strings(obj.model_dump(), out, depth + 1)
        except Exception:
            pass


def _answer(purpose: str, kw: dict) -> str:
    texts = [p.get("text") or "" for p in _parts(kw) if p.get("type") == "text"]
    if purpose == "sort":
        labels = [m.group(1) for t in texts for m in [re.match(r"Photo (P\d+):", t)] if m]
        return json.dumps({"photos": [
            {"label": label, "kind": "overview" if i == 0 else "damage", "area": "rear", "quality": "good",
             "shows_damage": i != 0, "part": None if i == 0 else "Rear Bumper", "note": f"photo {label}"}
            for i, label in enumerate(labels)]})
    if purpose == "annex":
        return json.dumps({"parts": [{"part": "Rear Bumper", "lines": [{"name": "Rear Bumper clips", "qty": 2}]}]})
    if purpose == "draft":
        return json.dumps(DRAFT_ANSWER)
    return "{}"


def _generate(client, kw: dict, purpose: str) -> str:
    found: list[str] = []
    _strings(kw, found)
    with _fake_lock:
        CALLS.append({"key": client.key, "given": not client.implicit, "purpose": purpose,
                      "text": "\n".join(found), "images": sum(1 for p in _parts(kw) if p.get("type") == "image")})
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
        return iter([])

    def generate_content(self, **kw):
        return SimpleNamespace(text=_generate(self.client, kw, "generate_content"), candidates=[])


class FakeGenaiClient:
    def __init__(self, *args, api_key=None, **kwargs):
        self.implicit = not api_key
        # As the real SDK: no api_key -> GOOGLE_API_KEY / GEMINI_API_KEY.
        self.key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        self.models = _FakeModels(self)
        self.interactions = _FakeInteractions(self)

    def close(self):
        pass


import google.genai as _genai  # noqa: E402

_genai.Client = FakeGenaiClient

# ================================================================ the app
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from telegram import InlineKeyboardMarkup, Message, Update, User as TgApiUser  # noqa: E402
from telegram.ext import ApplicationHandlerStop  # noqa: E402

try:
    from telegram._utils.defaultvalue import DefaultValue  # noqa: E402
except Exception:  # pragma: no cover
    DefaultValue = ()

from app.api.main import app  # noqa: E402
from app.auth.core import SESSION_COOKIE, create_session  # noqa: E402
from app.auth.passwords import hash_password  # noqa: E402
from app.reports.db import SessionLocal, engine  # noqa: E402
from app.reports.models import IntakeSession, Report, User, gen_id  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402
from app.storage.files import preview_path_for, thumbnail_path_for, tmp_dir  # noqa: E402

try:
    import app.api.ai_keys as ai_keys_api  # noqa: E402
except Exception:  # pragma: no cover
    ai_keys_api = None

# ================================================================ harness
BASE = "https://carlink.test"
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # CODE_ALPHABET: no 0/O/1/I/L
REF_RE = re.compile(rf"\bTG-(\d{{6}})-([{ALPHABET}]{{4}})\b")
CODE_RE = re.compile(rf"[{ALPHABET}]{{4}}-[{ALPHABET}]{{4}}")
DEEP_RE = re.compile(rf"(?:https?://)?t\.me/{re.escape(BOT_USERNAME)}\?start=link-([A-Za-z0-9_-]+)")
PUBLIC_CHANNEL = "telegram_public"
CONSENT_VERSION = "2026-10-30"
SPEC_COLUMNS = {
    "public_submissions": {
        "report_id", "reference", "submitted_at", "expires_at", "consent_at", "consent_version",
        "submitter_name", "submitter_role", "submitter_phone", "phone_kind", "plate", "plate_looks_sg",
        "when_where", "statement", "photo_count_sent", "forwarded_count", "telegram_user_id",
        "telegram_username", "telegram_name", "taken_at", "taken_by", "filed_at", "withdraw_requested_at"},
    "public_blocks": {"telegram_user_id", "blocked_at", "until", "blocked_by", "reference"},
}
AI_KEY_MISSING_DETAIL = ("Public reports can only use your own (billed) AI key until an admin confirms the company "
                         "key is billed. You can still fill it in by hand.")
CANT_TAKE = re.compile(r"can(?:'|’|no)?t take (?:any )?new reports", re.I)
BLOCKED = re.compile(r"can(?:'|’|no)?t take reports from this account", re.I)
AGREE = r"\bagree\b"
NO_THANKS = r"no,? thanks"
ROLE_BUTTONS = {"driver": r"\bdriver\b", "owner": r"\bcar owner\b|\bowner\b", "workshop": r"\bworkshop\b",
                "other": r"someone else|\bother\b"}

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
INFO: list[str] = []
_section = {"name": "setup"}
RESPONSES: list[str] = []
STATUS_500: list[str] = []
BOT_ERRORS: list[str] = []
SLOW_DOWN: list[str] = []
PUBLIC_CHATS: set[int] = set()
KEY_LABEL: dict[str, str] = {ENV_COMPANY_KEY: "the company key (GEMINI_API_KEY)"}
CAPTURED_HTML: list[str] = []
SENDERS: list["Sender"] = []   # every public sender, for the leak checks
W = SimpleNamespace(app=None)


class Stop(Exception):
    pass


def mask(value) -> str:
    out = str(value)
    for k in sorted(KEY_LABEL, key=len, reverse=True):
        out = out.replace(k, f"<{KEY_LABEL[k]}>")
    return out.replace(BOT_TOKEN, "<the bot token>")


def check(cond, label: str, why: str = "") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
        return True
    why = re.sub(r"\s*\n\s*", " / ", mask(why))
    FAILURES.append((_section["name"], label + (f"  [{why}]" if why else "")))
    print(f"    FAIL {label}" + (f"\n           got: {why}" if why else ""))
    return False


def need(cond, label: str, why: str = "") -> None:
    if not check(cond, label, why):
        raise Stop(label)


def info(line: str) -> None:
    INFO.append(f"[{_section['name']}] {line}")
    print(f"    info {line}")


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
    def request(self, *args, **kwargs):
        r = super().request(*args, **kwargs)
        RESPONSES.append(r.text)
        if r.status_code >= 500:
            STATUS_500.append(f"{args[0] if args else kwargs.get('method')} {args[1] if len(args) > 1 else kwargs.get('url')}"
                              f" -> {r.status_code}")
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
    return mask(f"{r.status_code} {(r.text or '').replace(chr(10), ' ')[:260]}")


def code_of(r) -> str | None:
    body = jd(r)
    code = body.get("code")
    if code is None and isinstance(body.get("detail"), dict):
        code = body["detail"].get("code")
    return code


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sg_today() -> str:
    return (utcnow() + timedelta(hours=8)).strftime("%y%m%d")


def parse_time(value):
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


# ---------------------------------------------------------------- database
def sql(query: str, **params) -> list[dict]:
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(text(query), params)]


def sql_write(query: str, **params) -> int:
    with engine.begin() as conn:
        return conn.execute(text(query), params).rowcount


def table_exists(name: str) -> bool:
    return bool(sql("SELECT name FROM sqlite_master WHERE type='table' AND name=:n", n=name))


def columns_of(name: str) -> set[str]:
    if not table_exists(name):
        return set()
    return {r["name"] for r in sql(f"PRAGMA table_info({name})")}


def pub_row(rid: str) -> dict | None:
    if not table_exists("public_submissions"):
        return None
    rows = sql("SELECT * FROM public_submissions WHERE report_id = :r", r=rid)
    return rows[0] if rows else None


def pub_by_ref(ref: str) -> dict | None:
    if not table_exists("public_submissions"):
        return None
    rows = sql("SELECT * FROM public_submissions WHERE reference = :r", r=ref)
    return rows[0] if rows else None


def pub_rows_for(tg_id) -> list[dict]:
    if not table_exists("public_submissions"):
        return []
    return sql("SELECT * FROM public_submissions WHERE CAST(telegram_user_id AS TEXT) = :t", t=str(tg_id))


def report_row(rid: str) -> dict | None:
    rows = sql("SELECT id, status, channel, owner_id, reporter_chat_id, photo_paths, data FROM reports WHERE id = :r", r=rid)
    if not rows:
        return None
    row = rows[0]
    for k in ("data", "photo_paths"):
        if isinstance(row.get(k), str):
            try:
                row[k] = json.loads(row[k])
            except ValueError:
                pass
    return row


def intake_folder(rid: str) -> Path:
    # Not app.intake.photos.intake_dir(): that one makes the folder.
    return STORAGE / "intake" / rid


def still_held(rid: str) -> list[str]:
    """Where a report still has something: tables and its intake/ folder."""
    out = []
    for table, col in (("reports", "id"), ("intake_sessions", "report_id"), ("intake_photos", "report_id"),
                       ("public_submissions", "report_id")):
        if table_exists(table) and sql(f"SELECT COUNT(*) AS n FROM {table} WHERE {col} = :r", r=rid)[0]["n"]:
            out.append(table)
    if intake_folder(rid).exists():
        out.append(f"intake/{rid}/ folder")
    return out


def open_untaken() -> int:
    return sql("SELECT COUNT(*) AS n FROM reports WHERE channel = :c AND status = 'intake' AND owner_id IS NULL",
               c=PUBLIC_CHANNEL)[0]["n"]


def audit_rows(rid: str | None = None) -> list[dict]:
    if rid is None:
        return sql("SELECT id, report_id, user_id, user_name, action, detail FROM audit_log ORDER BY id")
    return sql("SELECT id, report_id, user_id, user_name, action, detail FROM audit_log WHERE report_id = :r ORDER BY id",
               r=rid)


def report_ids() -> set[str]:
    return {r["id"] for r in sql("SELECT id FROM reports")}


def tmp_files() -> set[str]:
    try:
        return set(os.listdir(tmp_dir()))
    except OSError:
        return set()


# ---------------------------------------------------------------- people
class Person:
    def __init__(self, name: str, username: str, *, role: str = "user", **ticks):
        self.name = name
        self.label = name.split()[0]
        self.username = username
        self.email = f"{username}@carlink-p6.test"
        self.key = None
        db = SessionLocal()
        try:
            user = User(email=self.email, username=username, name=name, role=role,
                        password_hash=hash_password("p6 meadow copper lantern"), must_change_password=False, **ticks)
            db.add(user)
            db.commit()
            self.id = user.id
        finally:
            db.close()
        self.c = None
        self.sign_in()

    def sign_in(self) -> None:
        db = SessionLocal()
        try:
            token = create_session(db, db.get(User, self.id))
            db.commit()
        finally:
            db.close()
        self.c = browser()
        self.c.cookies.set(SESSION_COOKIE, token)

    def save_key(self) -> None:
        if ai_keys_api is not None and hasattr(ai_keys_api, "reset_limits"):
            ai_keys_api.reset_limits()
        key = make_key(self.label + "Own")
        KEY_LABEL[key] = f"{self.label}'s own key"
        r = self.c.put("/auth/me/ai-key", json={"api_key": key, "billing_confirmed": True})
        need(r.status_code == 200 and jd(r).get("status") == "ok", f"(setup) {self.label} saves an own AI key, billing confirmed",
             brief(r))
        self.key = key

    def drop_key(self) -> None:
        if ai_keys_api is not None and hasattr(ai_keys_api, "reset_limits"):
            ai_keys_api.reset_limits()
        r = self.c.delete("/auth/me/ai-key")
        need(r.status_code in (200, 204), f"(setup) {self.label} removes their own AI key", brief(r))
        self.key = None


def set_billed(flag: bool) -> None:
    """p6_decisions §3: the admin tick company_key_billed (PUT /settings)."""
    last = ""
    for body in ({"company_key_billed": flag}, {"company_key_billed": "true" if flag else "false"}):
        r = W.ada.c.put("/settings", json=body)
        got = jd(W.ada.c.get("/settings")).get("company_key_billed", "<missing>")
        last = f"PUT {json.dumps(body)} -> {brief(r)}; GET /settings company_key_billed={got!r}"
        if r.status_code == 200 and got != "<missing>" and truthy(got) == flag:
            if isinstance(body["company_key_billed"], str):
                info("PUT /settings took company_key_billed as a string, not a JSON boolean")
            return
    need(False, f"an admin sets company_key_billed = {str(flag).lower()} (PUT /settings; GET /settings shows it to admins)", last)


# ---------------------------------------------------------------- fake Telegram
@dataclass
class Button:
    text: str
    data: str | None = None
    url: str | None = None
    contact: bool = False


@dataclass
class Sent:
    kind: str                 # message | edit | markup | document | photo | media | answer | get_file | download | other
    chat_id: int | None
    text: str = ""
    buttons: list = field(default_factory=list)
    raw: dict | None = None
    method: str = ""
    file_id: str = ""
    parse_mode: object = None
    at: float = 0.0


def _cid(chat_id):
    try:
        return int(chat_id)
    except (TypeError, ValueError):
        return chat_id if isinstance(chat_id, str) else None


def _buttons(markup) -> list[Button]:
    out = []
    for row in getattr(markup, "inline_keyboard", None) or ():
        for b in row:
            out.append(Button(str(getattr(b, "text", "")), getattr(b, "callback_data", None), getattr(b, "url", None)))
    for row in getattr(markup, "keyboard", None) or ():
        for b in row:
            out.append(Button(str(getattr(b, "text", b)), contact=bool(getattr(b, "request_contact", False))))
    return out


def _parse_mode(kwargs):
    pm = kwargs.get("parse_mode")
    if DefaultValue and isinstance(pm, DefaultValue):
        pm = pm.value
    return pm


def photo_bytes(seed: str) -> bytes:
    """A small JPEG, different for every seed (so no two are duplicates)."""
    h = hashlib.md5(seed.encode()).digest()
    img = Image.new("RGB", (320, 240), (h[0], h[1], h[2]))
    img.paste((h[3], h[4], h[5]), (h[6] % 200, h[7] % 150, h[6] % 200 + 40, h[7] % 150 + 40))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


_API_METHOD = re.compile(r"^(send|edit|answer|get|set|delete|copy|forward|pin|unpin|leave|ban|unban|restrict|promote|"
                         r"export|create|revoke|approve|decline|close|reopen|stop|log|refund|upload)_")


class FakeFile:
    def __init__(self, bot: "FakeTelegram", file_id: str, chat_id):
        self._bot, self._chat = bot, chat_id
        self.file_id = file_id
        self.file_unique_id = "u" + file_id
        self.bytes = photo_bytes(file_id)
        self.file_size = len(self.bytes)
        self.file_path = f"photos/{file_id}.jpg"

    async def download_to_drive(self, custom_path=None, **_kw):
        path = Path(custom_path) if custom_path else (_tmp / "stray-downloads" / f"{self.file_id}.jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.bytes)
        self._bot.log.append(Sent("download", self._chat, method="download_to_drive", file_id=self.file_id, at=time.time()))
        return path

    async def download_as_bytearray(self, buf=None, **_kw):
        self._bot.log.append(Sent("download", self._chat, method="download_as_bytearray", file_id=self.file_id, at=time.time()))
        return bytearray(self.bytes)

    async def download_to_memory(self, out=None, **_kw):
        self._bot.log.append(Sent("download", self._chat, method="download_to_memory", file_id=self.file_id, at=time.time()))
        if out is not None:
            out.write(self.bytes)


class FakeTelegram:
    """Stands in for telegram.Bot / ExtBot: records everything, sends nothing."""
    defaults = None
    callback_data_cache = None

    def __init__(self):
        self.username = BOT_USERNAME
        self.id = BOT_ID
        self.first_name = "Carlink"
        self.last_name = None
        self.name = "@" + BOT_USERNAME
        self.link = f"https://t.me/{BOT_USERNAME}"
        self.token = BOT_TOKEN
        self.me = {"id": BOT_ID, "is_bot": True, "first_name": "Carlink", "username": BOT_USERNAME}
        self.log: list[Sent] = []
        self.chats: dict = {}
        self.files: dict = {}
        self.queries: dict = {}
        self._mid = 5000

    def _message(self, chat_id, text_=None, markup=None) -> tuple[dict, Message]:
        self._mid += 1
        cid = _cid(chat_id)
        chat = self.chats.get(cid) or {"id": cid, "type": "private", "first_name": "x"}
        raw = {"message_id": self._mid, "date": int(time.time()), "chat": chat, "from": self.me}
        if text_ is not None:
            raw["text"] = str(text_)
        if isinstance(markup, InlineKeyboardMarkup):
            raw["reply_markup"] = markup.to_dict()
        return raw, Message.de_json(raw, self)

    async def get_me(self, *args, **kwargs):
        return TgApiUser.de_json(self.me, self)

    async def send_message(self, chat_id=None, text=None, *args, reply_markup=None, **kwargs):
        raw, msg = self._message(chat_id, text, reply_markup)
        self.log.append(Sent("message", _cid(chat_id), str(text or ""), _buttons(reply_markup), raw, "send_message",
                             parse_mode=_parse_mode(kwargs), at=time.time()))
        return msg

    async def edit_message_text(self, text=None, chat_id=None, message_id=None, inline_message_id=None, *args,
                                reply_markup=None, **kwargs):
        raw, msg = self._message(chat_id, text, reply_markup)
        if message_id is not None:
            raw["message_id"] = message_id
            msg = Message.de_json(raw, self)
        self.log.append(Sent("edit", _cid(chat_id), str(text or ""), _buttons(reply_markup), raw, "edit_message_text",
                             parse_mode=_parse_mode(kwargs), at=time.time()))
        return msg if chat_id is not None else True

    async def edit_message_reply_markup(self, chat_id=None, message_id=None, inline_message_id=None, reply_markup=None,
                                        *args, **kwargs):
        self.log.append(Sent("markup", _cid(chat_id), "", _buttons(reply_markup), None, "edit_message_reply_markup",
                             at=time.time()))
        return True

    async def send_document(self, chat_id=None, document=None, *args, caption=None, reply_markup=None, **kwargs):
        self.log.append(Sent("document", _cid(chat_id), str(caption or ""), _buttons(reply_markup), None, "send_document",
                             at=time.time()))
        raw, msg = self._message(chat_id, None, reply_markup)
        return msg

    async def send_photo(self, chat_id=None, photo=None, *args, caption=None, reply_markup=None, **kwargs):
        self.log.append(Sent("photo", _cid(chat_id), str(caption or ""), _buttons(reply_markup), None, "send_photo",
                             at=time.time()))
        raw, msg = self._message(chat_id, None, reply_markup)
        return msg

    async def send_media_group(self, chat_id=None, media=None, *args, **kwargs):
        self.log.append(Sent("media", _cid(chat_id), "", [], None, "send_media_group", at=time.time()))
        return []

    async def answer_callback_query(self, callback_query_id=None, text=None, *args, **kwargs):
        self.log.append(Sent("answer", self.queries.get(callback_query_id), str(text or ""), [], None,
                             "answer_callback_query", at=time.time()))
        return True

    async def get_file(self, file_id=None, *args, **kwargs):
        chat = self.files.get(file_id)
        self.log.append(Sent("get_file", chat, method="get_file", file_id=str(file_id), at=time.time()))
        return FakeFile(self, str(file_id), chat)

    def __getattr__(self, name):
        if not _API_METHOD.match(name):
            raise AttributeError(name)

        async def record(*args, **kwargs):
            self.log.append(Sent("other", _cid(kwargs.get("chat_id", args[0] if args else None)),
                                 str(kwargs.get("text") or ""), _buttons(kwargs.get("reply_markup")), None, name,
                                 at=time.time()))
            return True

        return record


FAKE = FakeTelegram()


class TgUser:
    _next = [990000100]

    def __init__(self, first: str, last: str | None = None, username: str | None = None, uid: int | None = None):
        if uid is None:
            TgUser._next[0] += 7
            uid = TgUser._next[0]
        self.id = uid
        self.first, self.last, self.username = first, last, username

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first, self.last) if p)

    @property
    def label(self) -> str:
        return f"{self.full_name} ({self.id})"

    def json(self) -> dict:
        out = {"id": self.id, "is_bot": False, "first_name": self.first}
        if self.last:
            out["last_name"] = self.last
        if self.username:
            out["username"] = self.username
        return out


def private_chat(t: TgUser) -> dict:
    out = {"id": t.id, "type": "private", "first_name": t.first}
    if t.username:
        out["username"] = t.username
    return out


class Wire:
    """Builds the updates Telegram would send."""

    def __init__(self, bot: FakeTelegram):
        self.bot = bot
        self.update_id = 1
        self.message_id = 1

    def _next(self) -> tuple[int, int]:
        self.update_id += 1
        self.message_id += 1
        return self.update_id, self.message_id

    def message(self, t: TgUser, *, text_=None, photo=False, document=None, contact=None, forwarded=False,
                via_bot=False, media_group=None, caption=None) -> tuple[Update, str]:
        uid, mid = self._next()
        chat = private_chat(t)
        self.bot.chats[chat["id"]] = chat
        msg = {"message_id": mid, "date": int(time.time()), "chat": chat, "from": t.json()}
        file_id = ""
        if text_ is not None:
            msg["text"] = text_
            if text_.startswith("/"):
                msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text_.split()[0])}]
        if photo:
            file_id = f"ph{abs(chat['id'])}m{mid}"
            for f in (file_id + "s", file_id):
                self.bot.files[f] = chat["id"]
            msg["photo"] = [
                {"file_id": file_id + "s", "file_unique_id": "u" + file_id + "s", "width": 90, "height": 68,
                 "file_size": 1200},
                {"file_id": file_id, "file_unique_id": "u" + file_id, "width": 1280, "height": 960,
                 "file_size": len(photo_bytes(file_id))},
            ]
        if document:
            file_id = f"doc{abs(chat['id'])}m{mid}"
            self.bot.files[file_id] = chat["id"]
            msg["document"] = {"file_id": file_id, "file_unique_id": "u" + file_id, "file_name": document[1],
                               "mime_type": document[0], "file_size": len(photo_bytes(file_id))}
        if contact is not None:
            msg["contact"] = contact
        if media_group:
            msg["media_group_id"] = media_group
        if caption:
            msg["caption"] = caption
        if forwarded:
            msg["forward_origin"] = {"type": "user", "date": int(time.time()) - 3600,
                                     "sender_user": {"id": 555000111, "is_bot": False, "first_name": "Someone Else"}}
        if via_bot:
            msg["via_bot"] = {"id": 6000000009, "is_bot": True, "first_name": "Inline", "username": "some_inline_bot"}
        return Update.de_json({"update_id": uid, "message": msg}, self.bot), file_id

    def tap(self, t: TgUser, sent: Sent, data: str) -> Update:
        uid, _ = self._next()
        qid = f"q{uid}"
        self.bot.queries[qid] = sent.chat_id
        raw = sent.raw or {"message_id": 1, "date": int(time.time()), "chat": self.bot.chats.get(sent.chat_id) or private_chat(t),
                           "from": self.bot.me, "text": sent.text}
        return Update.de_json({"update_id": uid, "callback_query": {
            "id": qid, "from": t.json(), "chat_instance": f"ci{sent.chat_id}", "data": data, "message": raw}}, self.bot)


WIRE = Wire(FAKE)
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

_TEXT_KINDS = ("message", "edit", "answer", "document", "photo", "other")
_OUT_KINDS = ("message", "edit", "markup", "document", "photo", "media", "other")


class Reply:
    """What the bot did in answer to one update, in that chat."""

    def __init__(self, entries: list[Sent], error: str | None, chat_id, file_id: str = ""):
        self.chat_id = chat_id
        self.everything = entries
        self.entries = [e for e in entries if e.chat_id == chat_id]
        self.error = error
        self.file_id = file_id

    @property
    def texts(self) -> list[str]:
        return [e.text for e in self.entries if e.text and e.kind in _TEXT_KINDS]

    @property
    def text(self) -> str:
        return "\n".join(self.texts)

    @property
    def buttons(self) -> list[Button]:
        return [b for e in self.entries for b in e.buttons]

    def button(self, pattern: str):
        for e in reversed(self.entries):
            for b in e.buttons:
                if re.search(pattern, b.text, re.I):
                    return e, b
        return None

    @property
    def downloads(self) -> int:
        return sum(1 for e in self.entries if e.kind in ("get_file", "download"))

    def fetched(self, file_id: str) -> bool:
        return any(e.kind in ("get_file", "download") and e.file_id == file_id for e in self.everything)

    @property
    def documents(self) -> list[Sent]:
        return [e for e in self.entries if e.kind in ("document", "photo", "media")]

    def urls(self) -> list[str]:
        found = re.findall(r"https?://[^\s<>()\[\]\"'\\]+", self.text)
        return found + [b.url for b in self.buttons if b.url]

    def why(self) -> str:
        parts = []
        if self.texts:
            parts.append("bot said: " + " | ".join(t.replace("\n", " ")[:170] for t in self.texts[:4]))
        if self.buttons:
            parts.append("buttons: " + ", ".join(f"[{b.text}]" for b in self.buttons[:10]))
        kinds = Counter(e.kind for e in self.entries if e.kind not in ("message", "edit", "answer"))
        if kinds:
            parts.append("also: " + ", ".join(f"{n} x {k}" for k, n in kinds.items()))
        if self.error:
            parts.append(f"BOT RAISED {self.error}")
        return "; ".join(parts) if parts else "the bot sent nothing"


async def _dispatch(update: Update) -> None:
    """python-telegram-bot's Application.process_update, minus the error
    handlers: an exception reaches the test as a failure."""
    application = W.app
    context = None
    for group in sorted(application.handlers):
        for handler in list(application.handlers[group]):
            check_result = handler.check_update(update)
            if check_result is None or check_result is False:
                continue
            if context is None:
                context = application.context_types.context.from_update(update, application)
                await context.refresh_data()
            try:
                await handler.handle_update(update, application, check_result, context)
            except ApplicationHandlerStop:
                return
            break


# ---- pacing: well under the brief's limits, so a refusal is never the test's doing
SENDER_BURST, SENDER_RATE = 24.0, 0.9   # the bot: burst 30, 1 a second, per Telegram user
REPLY_BURST, REPLY_RATE = 14.0, 5.0     # the bot: public replies 8 a second, burst 20
WELCOME_GAP = 5.4                       # the bot: the welcome at most every 5 s per chat
_SENDER_BUCKET: dict = {}
_REPLY_BUCKET = [REPLY_BURST, time.monotonic()]
WELCOMED: dict = {}


def _pace(sender_id) -> None:
    now = time.monotonic()
    if sender_id is not None:
        tokens, t0 = _SENDER_BUCKET.get(sender_id, (SENDER_BURST, now))
        tokens = min(SENDER_BURST, tokens + (now - t0) * SENDER_RATE)
        if tokens < 1:
            _real_sleep((1 - tokens) / SENDER_RATE)
            now, tokens = time.monotonic(), 1.0
        _SENDER_BUCKET[sender_id] = (tokens - 1, now)
    tokens, t0 = _REPLY_BUCKET
    tokens = min(REPLY_BURST, tokens + (now - t0) * REPLY_RATE)
    if tokens < 3:
        _real_sleep((3 - tokens) / REPLY_RATE)
        now, tokens = time.monotonic(), 3.0
    _REPLY_BUCKET[:] = [tokens, now]


def _spend(entries: list[Sent]) -> None:
    _REPLY_BUCKET[0] -= sum(1 for e in entries if e.kind in _OUT_KINDS)


def _after(rep: Reply, what: str) -> Reply:
    _spend(rep.everything)
    if any(re.search(r"slow down", t, re.I) for t in rep.texts):
        SLOW_DOWN.append(what)
    if any(b.text and re.search(AGREE, b.text, re.I) for b in rep.buttons):
        WELCOMED[rep.chat_id] = time.monotonic()
    return rep


def deliver(update: Update, what: str, chat_id, sender_id=None, file_id: str = "") -> Reply:
    if W.app is None:
        return Reply([], "the bot app was not built (see [setup])", chat_id)
    _pace(sender_id)
    mark = len(FAKE.log)
    error = None
    try:
        LOOP.run_until_complete(asyncio.wait_for(_dispatch(update), 90))
    except Exception as exc:
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if "app" in Path(f.filename).parts]
        where = f" at {Path(frames[-1].filename).name}:{frames[-1].lineno}" if frames else ""
        error = f"{type(exc).__name__}: {exc}{where}"
        BOT_ERRORS.append(f"{what}: {mask(error)}")
    return _after(Reply(FAKE.log[mark:], error, chat_id, file_id), what)


def settle(seconds: float, chat_id) -> Reply:
    """Lets the bot's own timers run (the album reply comes 2 s after the
    last photo)."""
    mark = len(FAKE.log)
    LOOP.run_until_complete(asyncio.sleep(seconds))
    return _after(Reply(FAKE.log[mark:], None, chat_id), "timers")


def say(t: TgUser, words: str, **kw) -> Reply:
    u, fid = WIRE.message(t, text_=words, **kw)
    return deliver(u, f"{t.label} says {words!r}", t.id, t.id, fid)


def photo(t: TgUser, **kw) -> Reply:
    u, fid = WIRE.message(t, photo=True, **kw)
    return deliver(u, f"{t.label} sends a photo", t.id, t.id, fid)


def document(t: TgUser, mime: str, name: str) -> Reply:
    u, fid = WIRE.message(t, document=(mime, name))
    return deliver(u, f"{t.label} sends the file {name}", t.id, t.id, fid)


def contact(t: TgUser, phone: str, *, user_id="self", forwarded=False, via_bot=False) -> Reply:
    card = {"phone_number": phone, "first_name": t.first}
    if t.last:
        card["last_name"] = t.last
    uid = t.id if user_id == "self" else user_id
    if uid is not None:
        card["user_id"] = uid
    u, fid = WIRE.message(t, contact=card, forwarded=forwarded, via_bot=via_bot)
    return deliver(u, f"{t.label} shares a contact card", t.id, t.id, fid)


def tap(t: TgUser, hit, what: str = "taps") -> Reply:
    sent, button = hit
    if button.data is None:  # a reply-keyboard button: tapping it sends its text
        return say(t, button.text)
    return deliver(WIRE.tap(t, sent, button.data), f"{t.label} {what} [{button.text}]", sent.chat_id, t.id)


def tap_data(t: TgUser, sent: Sent, data: str, what: str) -> Reply:
    return deliver(WIRE.tap(t, sent, data), f"{t.label} {what}", t.id, t.id)


def last_sent(chat_id) -> Sent | None:
    for e in reversed(FAKE.log):
        if e.chat_id == chat_id and e.kind in ("message", "edit") and e.raw:
            return e
    return None


def ever_fetched(file_id: str) -> bool:
    """Whether the bot ever asked Telegram for this file (get_file or a
    download), at any time so far: also from a background task."""
    return bool(file_id) and any(e.kind in ("get_file", "download") and e.file_id == file_id for e in FAKE.log)


def find_button(replies: list[Reply], pattern: str):
    for rep in reversed(replies):
        hit = rep.button(pattern)
        if hit:
            return hit
    return None


def wait_welcome_gap(t: TgUser) -> None:
    wait = WELCOMED.get(t.id, -1e9) + WELCOME_GAP - time.monotonic()
    if wait > 0:
        _real_sleep(wait)


def calls_since(mark: int) -> list[dict]:
    with _fake_lock:
        return list(CALLS[mark:])


def describe(calls: list[dict]) -> str:
    if not calls:
        return "no Gemini call was made"
    counts = Counter(f"{c['purpose']} on {KEY_LABEL.get(c['key'], 'a key nobody saved') if c['key'] else 'no key'}"
                     for c in calls)
    return "; ".join(f"{n} x {w}" for w, n in counts.most_common(5))


def all_on(calls: list[dict], allowed: set) -> bool:
    return bool(calls) and all(c["key"] in allowed for c in calls)


# ---------------------------------------------------------------- public senders
@dataclass
class Sender:
    t: TgUser
    name: str
    phone: str            # as the contact card carries it
    statement: str = ""
    when_where: str = ""
    plate: str = ""
    ref: str = ""
    rid: str = ""

    @property
    def local(self) -> str:
        return digits(self.phone)[-8:]

    def pii(self) -> list[str]:
        """The strings that must never leak: the name (and its words of 4+
        letters) and the phone, in the forms it could be written."""
        words = [w for w in re.findall(r"[A-Za-z]{4,}", self.name)]
        d = digits(self.phone)
        return [self.name, *words, d, "+" + d, self.local, f"{self.local[:4]} {self.local[4:]}"]


def leaks_in(textblob: str, s: Sender) -> list[str]:
    compact = re.sub(r"[\s\-()]", "", textblob or "")
    found = [x for x in s.pii() if x and x in (textblob or "")]
    if s.local in compact and s.local not in found:
        found.append(s.local + " (with spaces or dashes)")
    return found


def new_sender(first: str, last: str, username: str, phone: str, *, name: str | None = None, statement: str = "",
               when_where: str = "", plate: str = "") -> Sender:
    s = Sender(TgUser(first, last, username), name or f"{first} {last}", phone, statement, when_where, plate)
    SENDERS.append(s)
    PUBLIC_CHATS.add(s.t.id)
    return s


def start_form(s: Sender, *, first: str = "/start", what: str = "") -> Reply:
    """The first message and the consent tap; returns the reply to the tap
    (or to the first message when there was no [I agree] to tap)."""
    wait_welcome_gap(s.t)
    rep = photo(s.t) if first == "photo" else say(s.t, first)
    hit = rep.button(AGREE)
    if hit is None:
        return rep
    return tap(s.t, hit, "agrees")


def send_report(s: Sender, *, photos: int = 1, role: str = "driver", what: str = "") -> Reply:
    """Steps 1-6, the review and Submit, as in the brief and agent A's
    conversation, for the senders around the main journey. Each step is a
    `need`, labelled with `what`; the content checks are the main journey's."""
    w = f"{what} " if what else ""
    wait_welcome_gap(s.t)
    rep = say(s.t, "/start")
    hit = rep.button(AGREE)
    need(hit is not None, f"{w}/start from a stranger gets the welcome with [I agree, start]", rep.why())
    rep = tap(s.t, hit, "agrees")
    need(re.search(r"\bname\b", rep.text, re.I) and not CANT_TAKE.search(rep.text),
         f"{w}after [I agree], the bot asks for the name (step 1/6)", rep.why())
    rep = say(s.t, s.name)
    need(rep.button(ROLE_BUTTONS[role]) is not None, f"{w}after the name, the bot asks the role: [Driver] ...", rep.why())
    rep = tap(s.t, rep.button(ROLE_BUTTONS[role]), "picks a role")
    need(re.search(r"phone", rep.text, re.I), f"{w}after the role, the bot asks for the phone number (step 2/6)", rep.why())
    rep = contact(s.t, s.phone)
    need(re.search(r"plate", rep.text, re.I) and not CANT_TAKE.search(rep.text),
         f"{w}the sender's own contact card is accepted and the bot asks for the plate (step 3/6)", rep.why())
    rep = say(s.t, s.plate or "SBA1234A")
    need(re.search(r"when|where", rep.text, re.I), f"{w}after the plate, the bot asks when and where (step 4/6)", rep.why())
    rep = say(s.t, s.when_where or "Yesterday about 6pm, PIE towards Changi near exit 8")
    need(re.search(r"what happened", rep.text, re.I), f"{w}then it asks what happened (step 5/6)", rep.why())
    rep = say(s.t, s.statement or "A taxi hit my rear bumper at a red light.")
    need(re.search(r"photo", rep.text, re.I), f"{w}then it asks for photos (step 6/6)", rep.why())
    replies = [rep]
    album = f"album-{s.t.id}-{random.randrange(10 ** 6)}"
    for _ in range(photos):
        replies.append(photo(s.t, media_group=album))
    if photos:
        replies.append(settle(2.6, s.t.id))
    done = find_button(replies, r"^\W*done\b")
    need(done is not None, f"{w}a [Done] button is offered at the photo step", replies[-1].why())
    rep = tap(s.t, done, "taps")
    if photos == 0:
        hit = rep.button(r"without photos")
        need(hit is not None, f"{w}[Done] with no photos offers [Send without photos]", rep.why())
        rep = tap(s.t, hit, "taps")
    submit = rep.button(r"\bsubmit\b")
    need(submit is not None, f"{w}the review is shown with [Submit]", rep.why())
    rep = tap(s.t, submit, "taps")
    m = REF_RE.search(rep.text)
    need(m is not None, f"{w}Submit is answered with a reference in TG-YYMMDD-XXXX form", rep.why())
    s.ref = m.group(0)
    row = pub_by_ref(s.ref)
    need(row is not None, f"{w}a public_submissions row holds reference {s.ref}",
         "no such row" if table_exists("public_submissions") else "no public_submissions table")
    s.rid = row["report_id"]
    return rep


# ---------------------------------------------------------------- seeding
def _pub_model():
    try:
        from app.reports.models import PublicSubmission
        return PublicSubmission
    except Exception:
        return None


def seed_public(n: int, *, submitted: datetime) -> list[str]:
    """n untaken public reports straight into the database (the brief's
    §2.1 rows: Report + IntakeSession + PublicSubmission), for the caps."""
    model = _pub_model()
    if model is None:
        need(False, "(seeding) app.reports.models has PublicSubmission (brief §2.1)")
    cols = {c.name: c for c in model.__table__.columns}
    tg_int = "telegram_user_id" in cols and getattr(cols["telegram_user_id"].type, "python_type", str) is int
    naive = submitted.astimezone(timezone.utc).replace(tzinfo=None)
    ids = []
    db = SessionLocal()
    try:
        for i in range(n):
            rid = gen_id()
            ref = f"TG-{(submitted + timedelta(hours=8)).strftime('%y%m%d')}-" + "".join(random.choice(ALPHABET) for _ in range(4))
            tg = 770000000 + random.randrange(10 ** 7)
            phone = f"+659{random.randrange(10 ** 7):07d}"
            data = SecurityIncidentDraft(description="", category=["Vehicle Collision or Damage"], reporter_name=f"Seed {i}",
                                         reporter_role="Driver", reporter_contact=phone).model_dump()
            db.add(Report(id=rid, channel=PUBLIC_CHANNEL, status="intake", owner_id=None, reporter_chat_id="public",
                          photo_paths=[], data=data, created_at=naive))
            db.flush()
            db.add(IntakeSession(report_id=rid, stage="uploading", reporter_notes="Seeded.\nWhat happened: seeded",
                                 updated_at=naive))
            values = {
                "report_id": rid, "reference": ref, "submitted_at": naive, "expires_at": naive + timedelta(days=30),
                "consent_at": naive, "consent_version": CONSENT_VERSION, "submitter_name": f"Seed {i}",
                "submitter_role": "driver", "submitter_phone": phone, "phone_kind": "sg_mobile", "plate": None,
                "plate_looks_sg": False, "when_where": "", "statement": "seeded", "photo_count_sent": 0,
                "forwarded_count": 0, "telegram_user_id": tg if tg_int else str(tg), "telegram_username": None,
                "telegram_name": f"Seed {i}",
            }
            kwargs = {k: v for k, v in values.items() if k in cols}
            for name, col in cols.items():
                if name in kwargs or col.nullable or col.default is not None or col.server_default is not None \
                        or col.primary_key:
                    continue
                py = getattr(col.type, "python_type", str)
                kwargs[name] = naive if py is datetime else (0 if py in (int, float) else (False if py is bool else ""))
            db.add(model(**kwargs))
            ids.append(rid)
        db.commit()
    finally:
        db.close()
    return ids


def unseed(ids: list[str]) -> None:
    for rid in ids:
        for table, col in (("intake_photos", "report_id"), ("intake_sessions", "report_id"),
                           ("public_submissions", "report_id"), ("reports", "id")):
            if table_exists(table):
                sql_write(f"DELETE FROM {table} WHERE {col} = :r", r=rid)


# ---------------------------------------------------------------- inbox helpers
def inbox_items(p: Person, view: str | None = None) -> tuple[int, list[dict], dict]:
    r = p.c.get("/inbox" + (f"?view={view}" if view else ""))
    body = r.json() if r.status_code == 200 else None
    items = body.get("items", []) if isinstance(body, dict) else (body if isinstance(body, list) else [])
    return r.status_code, [i for i in items if isinstance(i, dict)], body if isinstance(body, dict) else {}


def in_items(items: list[dict], rid: str) -> dict | None:
    return next((i for i in items if i.get("id") == rid), None)


def wait_done(p: Person, rid: str, timeout: float = 45.0) -> dict:
    end = time.time() + timeout
    state: dict = {}
    while time.time() < end:
        state = jd(p.c.get(f"/intake/{rid}"))
        if state.get("stage") in ("ready", "failed"):
            return state
        _real_sleep(0.15)
    return {**state, "stage": f"still {state.get('stage')!r} after {timeout:.0f}s"}


# ================================================================= sections
def setup() -> None:
    W.boot = browser()
    W.boot.__enter__()  # the api's start-up: tables, the AI key secret
    W.ada = Person("Ada Admin", "ada", role="admin")
    # S: see-all staff, allowed the company key, no own key (yet).
    W.sam = Person("Sam Staff", "sam", sees_all_cases=True, ai_company_key_allowed=True)
    W.vera = Person("Vera Seeall", "vera", sees_all_cases=True)
    W.pat = Person("Pat Plain", "pat")
    W.lin = Person("Lin Linked", "lin", ai_company_key_allowed=True)

    import app.channels.telegram as tg
    import app.rendering.renderer as renderer
    W.tg = tg

    def fake_html_to_pdf(html, output_path, slot_wait=None, *args, **kwargs):
        CAPTURED_HTML.append(html)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"%PDF-1.4 fake")

    W.patches = [mock.patch.object(renderer, "html_to_pdf", fake_html_to_pdf)]
    for p in W.patches:
        p.start()
    try:
        W.app = tg.build_app()
    except Exception as exc:
        W.app = None
        need(False, "the bot app builds (app.channels.telegram.build_app)", f"{type(exc).__name__}: {exc}")
    W.app.bot = FAKE  # context.bot is the recording fake
    check(True, "the bot app builds; Telegram is a recording fake")
    if getattr(W.app, "post_init", None):
        try:
            LOOP.run_until_complete(W.app.post_init(W.app))
            check(True, "the bot's start-up step (post_init) runs against the fake Telegram")
        except Exception as exc:
            check(False, "the bot's start-up step (post_init) runs against the fake Telegram", f"{type(exc).__name__}: {exc}")
    check(not NET_REFUSED, "nothing tried to reach the internet during start-up", ", ".join(NET_REFUSED[:3]))


def data_model() -> None:
    for table, want in SPEC_COLUMNS.items():
        have = columns_of(table)
        if not check(bool(have), f"table {table} exists (brief §2.1)"):
            continue
        missing = sorted(want - have)
        check(not missing, f"{table} has the brief's columns", f"missing: {', '.join(missing)}")
    if table_exists("public_submissions"):
        idx = sql("PRAGMA index_list(public_submissions)")
        uniq = []
        for i in idx:
            if i.get("unique"):
                cols = [c["name"] for c in sql(f"PRAGMA index_info('{i['name']}')")]
                uniq.append(cols)
        check(["reference"] in uniq, "public_submissions.reference is unique", f"unique indexes {uniq}")


def journey() -> None:
    need(W.app is not None, "the bot app is built")
    t1 = new_sender("Zeb", "Q", "zebq_tg", "+6598761234", name="Zebulon <b>x</b> Quayle",
                    statement="A grey taxi reversed into my rear bumper at the Tampines carpark, bumper cracked.",
                    when_where="Yesterday about 6pm, Tampines Mall carpark level 3", plate="sjb 8762-m")
    W.t1 = t1
    W.mark_calls, W.html_mark = len(CALLS), len(CAPTURED_HTML)
    W.before_reports = report_ids()
    W.tmp_at_start = tmp_files()
    wait_welcome_gap(t1.t)
    first = photo(t1.t)
    hit = first.button(AGREE)
    need(hit is not None, "a stranger's first message (a photo) gets the welcome with [I agree, start]", first.why())
    W.pre_consent_photo = first.file_id
    welcome = first.text
    check(f"{PUBLIC_BASE_URL}/privacy#public-reports" in welcome,
          f"the welcome links the notice: {PUBLIC_BASE_URL}/privacy#public-reports", welcome[:400])
    check(re.search(r"gemini", welcome, re.I) and re.search(r"30 days", welcome) and "/withdraw" in welcome,
          "the welcome says: Gemini AI, deleted if not taken up within 30 days, /withdraw", welcome[:400])
    check(re.search(r"outside singapore", welcome, re.I) is not None and re.search(r"nric", welcome, re.I) is not None,
          "  and: may be processed outside Singapore; please don't send NRIC photos", welcome[:400])
    check(first.button(NO_THANKS) is not None, "  with [No thanks] beside [I agree, start]", first.why())
    modes = {e.parse_mode for e in first.entries if e.kind == "message" and e.text}
    check(modes <= {None}, "  sent as plain text (no parse_mode)", f"parse_mode {modes}")
    check(not report_ids() - W.before_reports, "nothing is stored before consent")

    rep = tap(t1.t, hit, "agrees")
    need(re.search(r"\bname\b", rep.text, re.I) and not CANT_TAKE.search(rep.text),
         "after [I agree], the bot asks for the name (step 1/6)", rep.why())
    check(re.search(r"1\s*/\s*6", rep.text) is not None, "  the name question is numbered (1/6)", rep.why())

    rep = say(t1.t, t1.name)
    if rep.button(ROLE_BUTTONS["driver"]) is None and re.search(r"\bname\b", rep.text, re.I):
        info(f"the bot would not take the name {t1.name!r} (it asked again); using 'Zebulon Quayle' and putting "
             "<b>x</b> into the staff-editable name before finalize instead")
        t1.name = "Zebulon Quayle"
        rep = say(t1.t, t1.name)
    need(rep.button(ROLE_BUTTONS["driver"]) is not None, "after the name, the bot asks the role: [Driver] ...", rep.why())
    missing = [k for k, pat in ROLE_BUTTONS.items() if rep.button(pat) is None]
    check(not missing, "  [Driver] [Car owner] [Workshop] [Someone else] are all offered", f"missing {missing}; {rep.why()}")
    rep = tap(t1.t, rep.button(ROLE_BUTTONS["driver"]), "picks")
    need(re.search(r"phone", rep.text, re.I), "after the role, the bot asks for the phone number (step 2/6)", rep.why())
    share = [b for b in rep.buttons if b.contact]
    check(bool(share) and re.search(r"share", share[0].text, re.I) is not None,
          "  with a reply-keyboard button that shares the contact ('Share my phone number')", rep.why())

    replies = [rep]
    rep = contact(t1.t, t1.phone)
    replies.append(rep)
    need(re.search(r"plate", rep.text, re.I) and not CANT_TAKE.search(rep.text),
         "the sender's own contact card (contact.user_id = the sender) is accepted; the bot asks for the plate", rep.why())
    check(t1.local[-4:] in digits(rep.text), "  the bot confirms the number it got", rep.why())
    rep = say(t1.t, t1.plate)
    replies.append(rep)
    need(re.search(r"when|where", rep.text, re.I), "after the plate, the bot asks when and where (step 4/6)", rep.why())
    rep = say(t1.t, t1.when_where)
    replies.append(rep)
    need(re.search(r"what happened", rep.text, re.I), "then it asks what happened (step 5/6)", rep.why())
    check(not re.search(r"another number|other number", rep.text, re.I),
          "  step 5 no longer asks for another number to call (brief §1 rule 3)", rep.why())
    rep = say(t1.t, t1.statement)
    replies.append(rep)
    need(re.search(r"photo", rep.text, re.I), "then it asks for photos (step 6/6)", rep.why())
    check(not REF_RE.search(" ".join(r.text for r in replies)), "(no reference is handed out before Submit)")

    album = f"album-{t1.t.id}"
    photo_replies = []
    for i in range(3):
        rp = photo(t1.t, media_group=album)
        photo_replies.append(rp)
        if i == 0:
            doc = document(t1.t, "image/jpeg", "IMG_0042.jpg")
            photo_replies.append(doc)
            check(bool(doc.texts) and re.search(r"photo", doc.text, re.I) is not None,
                  "a file (a JPEG sent as a document) is answered: photos only", doc.why())
    photo_replies.append(settle(2.6, t1.t.id))
    album_ids = [r.file_id for r in photo_replies if r.file_id.startswith("ph")]
    check(len(album_ids) == 3 and all(ever_fetched(f) for f in album_ids), "the album's 3 photos are downloaded",
          f"{sum(ever_fetched(f) for f in album_ids)} of {len(album_ids)}")
    check(not ever_fetched(doc.file_id), "  the file is not downloaded")
    check(not ever_fetched(W.pre_consent_photo), "  the photo sent before consent never was (nothing before consent is kept)")
    acks = [x for r in photo_replies for x in r.texts if re.search(r"\b3\b", x) and re.search(r"photo", x, re.I)]
    check(len([x for r in photo_replies for x in r.texts if re.search(r"got \d+ photo", x, re.I)]) <= 1,
          "the album gets at most one 'Got N photos' reply (debounced)",
          " | ".join(x[:80] for r in photo_replies for x in r.texts))
    if not acks:
        info("no 'Got 3 photos' reply was seen within 2.6 s of the album")
    done = find_button([rep] + photo_replies, r"^\W*done\b")
    need(done is not None, "a [Done] button is offered at the photo step", " / ".join(r.why() for r in photo_replies[-2:]))
    rep = tap(t1.t, done, "taps")
    submit = rep.button(r"\bsubmit\b")
    need(submit is not None, "[Done] shows the review with [Submit]", rep.why())
    review = rep.text
    check(re.search(r"photos?\W+3\b", review, re.I) is not None, "  the review counts 3 photos (the file was not taken)",
          review[:300])
    check(t1.local[-4:] in digits(review) and "SJB8762M" in review.upper().replace(" ", ""),
          "  the review shows the phone and the plate (SJB8762M)", review[:300])
    check(re.search(r"edit", " ".join(b.text for b in rep.buttons), re.I) is not None and rep.button(r"cancel") is not None,
          "  with [Edit something] and [Cancel]", rep.why())
    check(not report_ids() - W.before_reports, "nothing is stored until Submit")
    W.tmp_before_submit = tmp_files()
    rep = tap(t1.t, submit, "taps")
    W.submit_reply = rep
    m = REF_RE.search(rep.text)
    need(m is not None, "Submit is answered with a reference in TG-YYMMDD-XXXX form", rep.why())
    t1.ref = m.group(0)
    row = pub_by_ref(t1.ref)
    need(row is not None, f"a public_submissions row holds reference {t1.ref}")
    t1.rid = row["report_id"]


def the_reply() -> None:
    t1 = W.t1
    need(bool(t1.rid), "the main journey was submitted (see the section above)")
    rep = W.submit_reply
    m = REF_RE.search(rep.text)
    check(m.group(1) == sg_today(), "the ref's date is today's Singapore date (YYMMDD)", f"{m.group(0)} vs {sg_today()}")
    check(not any("/reports/" in u for u in rep.urls()) and "/reports/" not in rep.text,
          "the reply holds no /reports/ link", f"links {rep.urls()}")
    check(not rep.documents and not any(e.kind in ("document", "photo", "media") for e in FAKE.log if e.chat_id == t1.t.id),
          "and no document, photo or PDF is ever sent to the sender", rep.why())
    check(not calls_since(W.mark_calls), "the bot never called Gemini for the public form", describe(calls_since(W.mark_calls)))
    check(len(CAPTURED_HTML) == W.html_mark, "and never rendered a PDF")
    left = {f for f in tmp_files() - W.tmp_at_start if f.startswith("tg-")}
    check(not left, "the form's temp files are deleted on Submit (brief §2.6)", f"{len(left)} tg-* file(s) left")

    rep_row = report_row(t1.rid)
    need(rep_row is not None, "the report row exists")
    check(rep_row["channel"] == PUBLIC_CHANNEL and rep_row["status"] == "intake" and rep_row["owner_id"] is None,
          "reports: channel 'telegram_public', status 'intake', no owner (new)", str({k: rep_row[k] for k in
                                                                                    ("channel", "status", "owner_id")}))
    check(rep_row["reporter_chat_id"] == "public", "reports.reporter_chat_id is 'public', never the Telegram id (M5)",
          repr(rep_row["reporter_chat_id"]))
    check(rep_row["photo_paths"] in ([], None), "reports.photo_paths is empty (photos are intake photos)",
          repr(rep_row["photo_paths"]))
    data = rep_row["data"] or {}
    check(data.get("description") in ("", None), "data.description is empty (their words go to the notes)",
          repr(data.get("description")))
    check(data.get("reporter_name") == t1.name, "data.reporter_name is the name as given", repr(data.get("reporter_name")))
    check(re.search(r"driver", str(data.get("reporter_role") or ""), re.I) is not None, "data.reporter_role is the Driver label",
          repr(data.get("reporter_role")))
    contact_ = str(data.get("reporter_contact") or "")
    check(digits(contact_) == digits(t1.phone), "data.reporter_contact is the shared number", repr(contact_))
    check(re.fullmatch(r"\+65 \d{4} \d{4}", contact_) is not None, "  written like '+65 9123 4567'", repr(contact_))
    check((data.get("vehicle_info") or {}).get("plate_number") == "SJB8762M",
          "data.vehicle_info.plate_number is the plate, upper case, spaces and dashes removed",
          repr((data.get("vehicle_info") or {}).get("plate_number")))

    sess = sql("SELECT stage, reporter_notes FROM intake_sessions WHERE report_id = :r", r=t1.rid)
    need(sess, "an intake_sessions row exists")
    notes = sess[0]["reporter_notes"] or ""
    check(sess[0]["stage"] == "uploading", "intake_sessions.stage is 'uploading'", repr(sess[0]["stage"]))
    check(t1.statement in notes and t1.when_where in notes, "the notes hold 'when and where' and 'what happened'",
          notes[:300])
    check(re.search(r"not checked", notes, re.I) is not None and re.search(r"member of the public", notes, re.I) is not None,
          "  under a header: sent by a member of the public, their own words, not checked", notes[:200])
    bad = leaks_in(notes, t1) + [x for x in (t1.ref, "SJB8762M") if x in notes.upper()]
    check(not bad, "the notes hold no name, phone, ref or plate (brief §1 rule 2)", f"found {bad}")

    photos = sql("SELECT id, position, path FROM intake_photos WHERE report_id = :r ORDER BY position", r=t1.rid)
    check(len(photos) == 3, "3 intake_photos rows (the file was refused)", f"{len(photos)} row(s)")
    check([p["position"] for p in photos] == list(range(len(photos))), "  positions 0..n-1",
          str([p["position"] for p in photos]))
    on_disk = [p for p in photos if Path(p["path"]).exists()]
    check(len(on_disk) == len(photos) and all(intake_folder(t1.rid) in Path(p["path"]).parents for p in photos),
          f"  the files are in intake/{t1.rid}/", str([p["path"] for p in photos])[:300])
    check(all(thumbnail_path_for(p["path"]).exists() and preview_path_for(p["path"]).exists() for p in on_disk),
          "  each with its thumbnail and preview")

    row = pub_row(t1.rid) or {}
    check(row.get("submitter_name") == t1.name and str(row.get("submitter_role")) == "driver",
          "public_submissions: submitter_name as given, submitter_role 'driver'",
          f"{row.get('submitter_name')!r}, {row.get('submitter_role')!r}")
    check(row.get("submitter_phone") == "+" + digits(t1.phone) and row.get("phone_kind") == "sg_mobile",
          "  submitter_phone in E.164, phone_kind 'sg_mobile'", f"{row.get('submitter_phone')!r}, {row.get('phone_kind')!r}")
    check(row.get("plate") == "SJB8762M" and truthy(row.get("plate_looks_sg")), "  plate SJB8762M, plate_looks_sg",
          f"{row.get('plate')!r}, {row.get('plate_looks_sg')!r}")
    check(row.get("statement") == t1.statement and row.get("when_where") == t1.when_where,
          "  statement and when_where verbatim")
    check(str(row.get("telegram_user_id")) == str(t1.t.id) and row.get("telegram_username") == t1.t.username,
          "  telegram_user_id and telegram_username of the sender",
          f"{row.get('telegram_user_id')!r}, {row.get('telegram_username')!r}")
    check(row.get("consent_version") == CONSENT_VERSION and row.get("consent_at") is not None,
          f"  consent_version '{CONSENT_VERSION}' and consent_at", f"{row.get('consent_version')!r}")
    check(str(row.get("photo_count_sent")) == "3" and str(row.get("forwarded_count")) == "0",
          "  photo_count_sent 3, forwarded_count 0", f"{row.get('photo_count_sent')!r}, {row.get('forwarded_count')!r}")
    sub, exp = parse_time(row.get("submitted_at")), parse_time(row.get("expires_at"))
    check(sub is not None and exp is not None and abs((exp - sub) - timedelta(days=30)) < timedelta(minutes=1),
          "  expires_at = submitted_at + 30 days", f"{row.get('submitted_at')!r} -> {row.get('expires_at')!r}")
    check(all(row.get(k) is None for k in ("taken_at", "taken_by", "filed_at", "withdraw_requested_at")),
          "  taken_at, taken_by, filed_at, withdraw_requested_at all empty")
    created = [a for a in audit_rows(t1.rid) if a["action"] == "created"]
    check(len(created) == 1 and created[0]["user_id"] is None, "one 'created' history entry, with no account behind it",
          str(created))


def inbox_who() -> None:
    t1 = W.t1
    need(bool(t1.rid), "the main journey was submitted")
    for p, who in ((W.ada, "an admin"), (W.vera, "see-all staff")):
        status, items, body = inbox_items(p)
        item = in_items(items, t1.rid)
        need(status == 200, f"{who} ({p.label}): GET /inbox -> 200", brief(p.c.get("/inbox")))
        check(item is not None and item.get("reference") == t1.ref, f"  it lists {t1.ref}",
              f"{len(items)} item(s): {[i.get('reference') for i in items][:6]}")
        if item:
            masked = str((item.get("sender") or {}).get("phone_masked") or "")
            check(masked and t1.local not in digits(masked), "  the list shows the phone masked", repr(masked))
        check(isinstance(body.get("counts"), dict) and int((body.get("counts") or {}).get("new") or 0) >= 1,
              "  counts.new >= 1", repr(body.get("counts")))
    r = W.vera.c.get(f"/inbox/{t1.rid}")
    check(r.status_code == 200 and jd(r).get("reference") == t1.ref and (jd(r).get("sender") or {}).get("name") == t1.name,
          "see-all staff open GET /inbox/{id}: the reference and the sender's name", brief(r))
    r = W.pat.c.get("/inbox")
    check(r.status_code == 403, "a plain user: GET /inbox -> 403", brief(r))
    r = W.pat.c.get("/inbox/count")
    check(r.status_code == 403, "a plain user: GET /inbox/count -> 403", brief(r))
    r = W.pat.c.get(f"/inbox/{t1.rid}")
    check(r.status_code == 404 and jd(r).get("detail") != "Not Found", "a plain user: GET /inbox/{id} -> 404 (as for a "
          "case they can't see)", brief(r))
    photos = sql("SELECT path FROM intake_photos WHERE report_id = :r ORDER BY position", r=t1.rid)
    need(photos, "the report has intake photos")
    rel = Path(photos[0]["path"]).resolve().relative_to(STORAGE).as_posix()
    r = W.ada.c.get(f"/files/{rel}")
    check(r.status_code == 200, f"(the admin gets the photo: /files/{rel} -> 200)", brief(r))
    r = W.pat.c.get(f"/files/{rel}")
    check(r.status_code == 404, "a plain user: the photo under /files -> 404", brief(r))
    if W.ada.c.get(f"/files/{rel}").status_code == 200:
        cache = W.ada.c.get(f"/files/{rel}").headers.get("cache-control", "")
        check("max-age=3600" in cache and "private" in cache, "  intake/ files are cached 'private, max-age=3600' (L4)",
              repr(cache))


def before_take() -> None:
    t1 = W.t1
    need(bool(t1.rid), "the main journey was submitted")
    mark = len(CALLS)
    data = (report_row(t1.rid) or {}).get("data") or {}
    r = W.ada.c.put(f"/intake/{t1.rid}/data", json={"data": data})
    check(r.status_code == 403, "an admin's PUT /intake/{id}/data on an untaken report -> 403", brief(r))
    r = W.ada.c.post(f"/intake/{t1.rid}/process", json={"mode": "all"})
    check(r.status_code == 403, "an admin's POST /intake/{id}/process on an untaken report -> 403", brief(r))
    r = W.ada.c.delete(f"/reports/{t1.rid}")
    check(r.status_code == 403, "an admin's DELETE /reports/{id} on an untaken report -> 403 (use Discard)", brief(r))
    r = W.sam.c.post(f"/intake/{t1.rid}/process", json={"mode": "all"})
    check(r.status_code == 403, "see-all staff's POST /process before taking it -> 403", brief(r))
    _real_sleep(0.3)
    check(not calls_since(mark), "no Gemini call was made", describe(calls_since(mark)))
    check(report_row(t1.rid) is not None and (report_row(t1.rid) or {}).get("owner_id") is None,
          "the report is still there, untaken")


def take_and_start() -> None:
    t1 = W.t1
    need(bool(t1.rid), "the main journey was submitted")
    set_billed(False)
    r = W.sam.c.post(f"/inbox/{t1.rid}/take")
    need(r.status_code == 200, f"{W.sam.label} (see-all staff) takes it: POST /inbox/{{id}}/take -> 200", brief(r))
    owner = jd(r).get("owner")
    check((owner or {}).get("id") == W.sam.id if isinstance(owner, dict) else owner == W.sam.id,
          f"  the answer names {W.sam.label} as owner", brief(r))
    r = W.vera.c.post(f"/inbox/{t1.rid}/take")
    check(r.status_code == 409 and code_of(r) == "inbox_taken", "a second take (other staff) -> 409 inbox_taken", brief(r))
    r = W.ada.c.post(f"/inbox/{t1.rid}/take")
    check(r.status_code == 409 and code_of(r) == "inbox_taken", "an admin's take after that -> 409 inbox_taken", brief(r))
    row, rep_row = pub_row(t1.rid) or {}, report_row(t1.rid) or {}
    check(rep_row.get("owner_id") == W.sam.id and rep_row.get("status") == "intake", "reports.owner_id is S; still a draft",
          str(rep_row.get("owner_id")))
    check(row.get("taken_at") is not None and row.get("taken_by") == W.sam.id, "public_submissions: taken_at and taken_by S",
          f"{row.get('taken_at')!r}, {row.get('taken_by')!r}")
    _, items, _ = inbox_items(W.ada, "new")
    check(in_items(items, t1.rid) is None, "it leaves the 'new' view")
    _, items, _ = inbox_items(W.ada, "taken")
    check(in_items(items, t1.rid) is not None, "and is in the 'taken' view")

    print("  billing rule (p6_decisions §3), company_key_billed = false")
    mark = len(CALLS)
    r = W.sam.c.post(f"/intake/{t1.rid}/process", json={"mode": "all"})
    check(r.status_code == 409 and code_of(r) == "ai_key_missing",
          "S with only the company tick presses Start -> 409 ai_key_missing", brief(r))
    check(jd(r).get("detail") == AI_KEY_MISSING_DETAIL, "  with the decision's wording", repr(jd(r).get("detail")))
    _real_sleep(0.3)
    check(not calls_since(mark), "  and Gemini was not called", describe(calls_since(mark)))

    W.sam.save_key()
    mark = len(CALLS)
    r = W.sam.c.post(f"/intake/{t1.rid}/process", json={"mode": "all"})
    need(r.status_code == 200, "S, now with an own key (billing confirmed), presses Start -> 200", brief(r))
    state = wait_done(W.sam, t1.rid)
    need(state.get("stage") == "ready", "the run finishes: ready", f"stage {state.get('stage')!r}, {state.get('error')!r}")
    calls = calls_since(mark)
    W.own_calls = calls
    check(all_on(calls, {W.sam.key}), f"every one of the {len(calls)} Gemini calls used S's own key", describe(calls))
    check(any(c["purpose"] == "sort" for c in calls) and any(c["purpose"] == "draft" for c in calls),
          "  photos were sorted and the case was read", describe(calls))
    leaked = {f"{c['purpose']} call": leaks_in(c["text"], t1) for c in calls if leaks_in(c["text"], t1)}
    check(not leaked, "the sender's NAME and PHONE are in NO Gemini request", str(leaked)[:300])
    check(any(t1.statement in c["text"] for c in calls), "the statement IS in the requests (the reading step)",
          describe(calls))
    check(not any(t1.statement in c["text"] for c in calls if c["purpose"] == "sort"),
          "  but not in the sorting calls (triage sends only photos)")
    data = state.get("data") or {}
    check(data.get("reporter_name") == t1.name and digits(data.get("reporter_contact")) == digits(t1.phone),
          "after the AI run the reporter's name and contact are kept", f"{data.get('reporter_name')!r}, "
                                                                        f"{data.get('reporter_contact')!r}")
    check((data.get("vehicle_info") or {}).get("plate_number") == "SJB8762M", "  and the typed plate is kept",
          repr((data.get("vehicle_info") or {}).get("plate_number")))

    W.sam.drop_key()
    mark = len(CALLS)
    r = W.sam.c.post(f"/intake/{t1.rid}/process", json={"mode": "read"})
    check(r.status_code == 409 and code_of(r) == "ai_key_missing",
          "S without the own key again (company tick only): Retry -> 409 ai_key_missing", brief(r))
    _real_sleep(0.3)
    check(not calls_since(mark), "  no Gemini call", describe(calls_since(mark)))

    print("  company_key_billed = true")
    set_billed(True)
    mark = len(CALLS)
    r = W.sam.c.post(f"/intake/{t1.rid}/process", json={"mode": "read"})
    need(r.status_code == 200, "S (company tick, no own key) presses Retry -> 200: the company key is allowed now", brief(r))
    state = wait_done(W.sam, t1.rid)
    check(state.get("stage") == "ready", "the run finishes: ready", f"stage {state.get('stage')!r}, {state.get('error')!r}")
    calls = calls_since(mark)
    check(all_on(calls, {ENV_COMPANY_KEY}), "every call used the company key", describe(calls))
    leaked = {f"{c['purpose']} call": leaks_in(c["text"], t1) for c in calls if leaks_in(c["text"], t1)}
    check(not leaked, "  and again no name or phone in any request", str(leaked)[:300])
    W.started = True


def finalize() -> None:
    t1 = W.t1
    need(getattr(W, "started", False), "S took the report and the AI ran (see the section above)")
    state = jd(W.sam.c.get(f"/intake/{t1.rid}"))
    data = state.get("data") or {}
    need(bool(data), "S reads the draft: GET /intake/{id}")
    hostile = "<b>x</b>"
    if hostile not in str(data.get("reporter_name") or ""):
        info("the stored name holds no <b>x</b>; S types 'Zebulon <b>x</b> Quayle' into the name before finishing")
        data["reporter_name"] = "Zebulon <b>x</b> Quayle"
    html_mark = len(CAPTURED_HTML)
    r = W.sam.c.post(f"/intake/{t1.rid}/finalize", json={"data": data})
    need(r.status_code == 200, "S finishes the case: POST /intake/{id}/finalize -> 200", brief(r))
    rep_row = report_row(t1.rid) or {}
    check(rep_row.get("status") not in (None, "intake"), "the report is filed (status no longer 'intake')",
          repr(rep_row.get("status")))
    check(rep_row.get("reporter_chat_id") == "public", "reports.reporter_chat_id == 'public'",
          repr(rep_row.get("reporter_chat_id")))
    check(rep_row.get("channel") == PUBLIC_CHANNEL and rep_row.get("owner_id") == W.sam.id,
          "  channel still 'telegram_public', owner S")
    row = pub_row(t1.rid) or {}
    check(row.get("filed_at") is not None, "public_submissions.filed_at is set", repr(row.get("filed_at")))
    check(all(row.get(k) is None for k in ("telegram_user_id", "telegram_username", "telegram_name")),
          "telegram_user_id, telegram_username and telegram_name are NULL",
          str({k: row.get(k) for k in ("telegram_user_id", "telegram_username", "telegram_name")}))
    check(row.get("submitter_name") == t1.name and row.get("submitter_phone") == "+" + digits(t1.phone),
          "  the submitter_* record is kept", f"{row.get('submitter_name')!r}, {row.get('submitter_phone')!r}")
    if len(CAPTURED_HTML) == html_mark:
        W.sam.c.get(f"/reports/{t1.rid}/download")  # rendered on demand when finalize left it
    pages = CAPTURED_HTML[html_mark:]
    need(pages, "a PDF was rendered for the case (finalize or the first download)")
    page = pages[-1]
    check(hostile not in page, "the PDF HTML does not carry the name's <b>x</b> as markup")
    check("&lt;b&gt;x&lt;/b&gt;" in page, "  it is printed escaped: &lt;b&gt;x&lt;/b&gt;")
    entries = audit_rows()
    bad = [f"{e['action']}: {e['detail']}" for e in entries
           if leaks_in(f"{e['action']} {e['detail']} {e['user_name']}", t1)]
    check(not bad, f"no audit entry (of {len(entries)}) holds the sender's name or phone", "; ".join(bad[:3]))
    created = [e for e in audit_rows(t1.rid) if e["action"] in ("taken", "finalized")]
    check({e["action"] for e in created} >= {"taken", "finalized"}, "the history has 'taken' and 'finalized'",
          str([e["action"] for e in audit_rows(t1.rid)]))

    print("  annex AI on the filed public case follows the same billing rule")
    set_billed(False)
    mark = len(CALLS)
    r = W.sam.c.post(f"/reports/{t1.rid}/annex-suggestions/ai", json={"parts": ["Rear Bumper"]})
    check(r.status_code == 409 and code_of(r) == "ai_key_missing",
          "company_key_billed false: S (company tick only) asks for annex AI -> 409 ai_key_missing", brief(r))
    check(not calls_since(mark), "  no Gemini call", describe(calls_since(mark)))
    set_billed(True)
    mark = len(CALLS)
    r = W.sam.c.post(f"/reports/{t1.rid}/annex-suggestions/ai", json={"parts": ["Rear Bumper"]})
    check(r.status_code == 200 and all_on(calls_since(mark), {ENV_COMPANY_KEY}),
          "company_key_billed true: annex AI -> 200 on the company key", f"{brief(r)}; {describe(calls_since(mark))}")


def phone_rules() -> None:
    need(W.app is not None, "the bot app is built")
    u = new_sender("Ottilie", "Varga", "ottiev", "+6581112233")
    before = report_ids()
    rep = start_form(u)
    need(re.search(r"\bname\b", rep.text, re.I), "a new stranger agrees and is asked the name", rep.why())
    rep = say(u.t, u.name)
    need(rep.button(ROLE_BUTTONS["owner"]) is not None, "  then the role", rep.why())
    rep = tap(u.t, rep.button(ROLE_BUTTONS["owner"]), "picks")
    need(re.search(r"phone", rep.text, re.I), "  then the phone", rep.why())

    def refused(rep: Reply, label: str, pattern: str | None = None) -> None:
        ok = bool(rep.texts) and not re.search(r"plate", rep.text, re.I)
        if pattern:
            ok = ok and re.search(pattern, rep.text, re.I) is not None
        check(ok, label, rep.why())

    refused(say(u.t, "91234567"), "a typed phone number is refused, and the bot asks again (Share my phone number)",
            r"share")
    refused(say(u.t, "+65 9123 4567"), "  also typed with +65 and a space")
    refused(contact(u.t, "+6587776655", user_id=u.t.id + 1), "someone else's contact card is refused")
    refused(contact(u.t, "+6587776655", user_id=None), "a contact card with no user id is refused")
    refused(contact(u.t, u.phone, forwarded=True), "a forwarded card (even the sender's own) is refused")
    refused(contact(u.t, u.phone, via_bot=True), "a card sent via an inline bot is refused")
    refused(contact(u.t, "+88812345678"), "an anonymous +888 number is refused")
    rep = contact(u.t, u.phone)
    check(re.search(r"plate", rep.text, re.I) is not None, "the sender's own card is then accepted (asks the plate)",
          rep.why())
    check(not pub_rows_for(u.t.id) and report_ids() == before, "nothing was stored")
    rep = say(u.t, "/cancel")
    check(re.search(r"stopped|deleted|cancel", rep.text, re.I) is not None, "/cancel stops the form", rep.why())
    check(not pub_rows_for(u.t.id) and report_ids() == before, "  and still nothing is stored")


def linked_staff() -> None:
    need(W.app is not None, "the bot app is built")
    lin = W.lin
    tl = TgUser("Lin", "Phone", "linphone_tg")
    r = lin.c.post("/auth/me/telegram/code")
    need(r.status_code == 200, f"{lin.label} makes a link code (P5)", brief(r))
    body = jd(r)
    display = str(((body.get("code") or {}).get("display")) or "").strip()
    deep = DEEP_RE.fullmatch(str(((body.get("code") or {}).get("deep_link")) or ""))
    payload = f"link-{deep.group(1)}" if deep else f"link-{display}"
    rep = say(tl, f"/start {payload}")
    yes = rep.button(r"\byes\b")
    need(yes is not None, "the bot asks the Telegram to confirm the code (P5 unchanged)", rep.why())
    tap(tl, yes, "taps")
    r = lin.c.post("/auth/me/telegram/confirm", json={"accept": True})
    need(r.status_code == 200 and jd(r).get("connected") is True, f"{lin.label} confirms in Carlink: linked", brief(r))

    before, mark = report_ids(), len(CALLS)
    rep = photo(tl)
    check(ever_fetched(rep.file_id), "the linked staff member's photo is downloaded at once (P5 staff flow)", rep.why())
    check(rep.button(AGREE) is None and not re.search(r"privacy|consent", rep.text, re.I),
          "  no public welcome or consent", rep.why())
    rep2 = say(tl, "Rear-ended at PIE exit 12, rear bumper dented.")
    texts = rep.texts + rep2.texts
    check(any(re.match(r"\W*Filing as\b", x) for x in texts), "  the P5 draft summary 'Filing as ...' comes back",
          rep2.why())
    hit = rep2.button(r"confirm") or rep.button(r"confirm")
    need(hit is not None, "  with a Confirm button", rep2.why())
    tap(tl, hit, "taps")
    new = [report_row(i) for i in report_ids() - before]
    need(len(new) == 1, "Confirm files exactly one case", f"{len(new)} new")
    case = new[0]
    check(case["channel"] == "telegram" and case["owner_id"] == lin.id and case["reporter_chat_id"] == str(tl.id),
          "  channel 'telegram', owner Lin, reporter_chat_id the Telegram id (as in P5)", str({k: case[k] for k in (
              "channel", "owner_id", "reporter_chat_id")}))
    check(all_on(calls_since(mark), {ENV_COMPANY_KEY}), "  the draft ran on the company key (Lin's tick)",
          describe(calls_since(mark)))
    check(not pub_rows_for(tl.id), "no public_submissions row for the staff member")


def withdraw_flow() -> None:
    need(W.app is not None, "the bot app is built")
    v1 = new_sender("Wilhelmina", "Okafor", "willo_tg", "+6582223344",
                    statement="Scraped by a lorry on the CTE, left door dented.")
    mal = new_sender("Mallory", "Brightwater", "mallory_tg", "+6583334455",
                     statement="Hit a bollard reversing, rear bumper scuffed.")
    t2 = new_sender("Barnaby", "Ellsworth", "barnaby_tg", "+6584445566",
                    statement="Rear-ended at a junction in Bedok, boot dented.")
    send_report(v1, photos=1, what="(victim)")
    send_report(mal, photos=0, what="(mallory)")
    send_report(t2, photos=1, what="(T2)")
    W.withdraw = SimpleNamespace(v1=v1, mal=mal, t2=t2)
    r = W.sam.c.post(f"/inbox/{t2.rid}/take")
    need(r.status_code == 200, "staff takes T2's report", brief(r))

    def pub_datas(replies: list[Reply]) -> list[tuple[Sent, Button]]:
        return [(e, b) for rep in replies for e in rep.entries for b in e.buttons if b.data and b.data.startswith("pub:")]

    print("  the victim opens /withdraw, then keeps the report")
    rep = say(v1.t, "/withdraw")
    seen = [rep]
    hit = rep.button(re.escape(v1.ref)) or rep.button(r"take back|withdraw|delete|\byes\b")
    need(hit is not None, "/withdraw offers the sender's own report as a button", rep.why())
    if not re.search(r"\byes\b|delete", hit[1].text, re.I):
        rep = tap(v1.t, hit, "picks")
        seen.append(rep)
    yes = rep.button(r"\byes\b|delete")
    need(yes is not None, "  and asks to confirm: [Yes, delete it]", rep.why())
    keep = rep.button(r"\bkeep\b")
    forged = [(e, b) for e, b in pub_datas(seen) if not re.search(r"\bkeep\b", b.text, re.I)]
    check(bool(forged) and all(re.match(r"^pub:[^:]+:", b.data) for _, b in forged),
          "  the buttons carry pub:<nonce>:... callback data", str([b.data for _, b in pub_datas(seen)]))
    if keep:
        tap(v1.t, keep, "taps")
    check(report_row(v1.rid) is not None, "  kept for now (not deleted without [Yes])")

    print("  mallory replays the victim's buttons from her own chat")
    rep_m = say(mal.t, "/withdraw")
    m_datas = [b.data for _, b in pub_datas([rep_m])]
    m_sent = last_sent(mal.t.id)
    need(m_sent is not None, "Mallory's /withdraw is answered", rep_m.why())
    before = {r["report_id"] for r in sql("SELECT report_id FROM public_submissions")} if table_exists("public_submissions") else set()
    for _, b in forged:
        tap_data(mal.t, m_sent, b.data, f"forges the victim's [{b.text}]")
    nonce_m = next((re.match(r"^pub:([^:]+):", d).group(1) for d in m_datas if re.match(r"^pub:([^:]+):", d)), "x")
    tried = []
    for n in range(0, 6):
        d = f"pub:{nonce_m}:wd:{n}"
        if d not in m_datas:
            tap_data(mal.t, m_sent, d, f"forges {d}")
            tried.append(d)
    for d in (f"pub:{nonce_m}:wd:{v1.rid}", f"pub:{nonce_m}:wd:{v1.ref}", f"pub:{nonce_m}:wd:yes:{v1.rid}"):
        tap_data(mal.t, m_sent, d, f"forges {d}")
    check(report_row(v1.rid) is not None and pub_row(v1.rid) is not None,
          f"forged withdraw callbacks ({len(forged)} replayed, {len(tried) + 3} made up) delete nothing of the victim's")
    after = {r["report_id"] for r in sql("SELECT report_id FROM public_submissions")} if table_exists("public_submissions") else set()
    check(before == after, "  nothing at all was deleted", f"gone: {sorted(before - after)}")

    print("  the victim withdraws for real")
    rep = say(v1.t, "/withdraw")
    for _ in range(3):
        hit = rep.button(re.escape(v1.ref)) or rep.button(r"\byes\b|delete|take back")
        if hit is None:
            break
        rep = tap(v1.t, hit, "taps")
        if report_row(v1.rid) is None:
            break
    check(not still_held(v1.rid), "/withdraw deletes the untaken report: all 4 tables and its photos",
          f"still in {still_held(v1.rid)}")
    check(re.search(r"deleted", rep.text, re.I) is not None and v1.ref in rep.text, "  the bot says it was deleted",
          rep.why())
    entries = [e for e in audit_rows(v1.rid)]
    check(any(v1.ref in f"{e['action']} {e['detail']}" for e in entries), "  the history keeps the ref",
          str(entries)[:300])

    print("  T2's report was taken: /withdraw only records the request")
    rep = say(t2.t, "/withdraw")
    for _ in range(3):
        hit = rep.button(re.escape(t2.ref)) or rep.button(r"\byes\b|delete|take back")
        if hit is None:
            break
        rep = tap(t2.t, hit, "taps")
    row, rep_row = pub_row(t2.rid) or {}, report_row(t2.rid)
    check(rep_row is not None and rep_row.get("owner_id") == W.sam.id, "the taken report is not deleted",
          f"{still_held(t2.rid)}")
    check(row.get("withdraw_requested_at") is not None, "  withdraw_requested_at is recorded",
          repr(row.get("withdraw_requested_at")))
    check(re.search(r"already|started|staff", rep.text, re.I) is not None, "  the bot says staff have already started on it",
          rep.why())
    r = W.sam.c.get(f"/inbox/{t2.rid}")
    check(r.status_code == 200 and jd(r).get("withdraw_requested_at"), "  and GET /inbox/{id} shows the request", brief(r))


def spam_block() -> None:
    need(W.app is not None, "the bot app is built")
    x = new_sender("Spammy", "Mcspamface", "spammy_tg", "+6585556677", statement="Buy cheap watches now now now.")
    send_report(x, photos=1, what="(spammer)")
    r = W.ada.c.post(f"/inbox/{x.rid}/discard", json={"reason": "spam", "block_sender": True})
    need(r.status_code == 200, "an admin discards it as Spam with block_sender -> 200", brief(r))
    check(not still_held(x.rid), "the report is deleted (all 4 tables and its photos)", f"still in {still_held(x.rid)}")
    blocks = sql("SELECT * FROM public_blocks WHERE CAST(telegram_user_id AS TEXT) = :t", t=str(x.t.id)) \
        if table_exists("public_blocks") else []
    check(len(blocks) == 1, "public_blocks holds the sender's Telegram id", f"{len(blocks)} row(s)")
    if blocks:
        until, at = parse_time(blocks[0].get("until")), parse_time(blocks[0].get("blocked_at"))
        check(until is not None and at is not None and abs((until - at) - timedelta(days=90)) < timedelta(hours=1),
              "  blocked for 90 days (until = blocked_at + 90 days)",
              f"{blocks[0].get('blocked_at')!r} -> {blocks[0].get('until')!r}")
        check(blocks[0].get("reference") == x.ref and blocks[0].get("blocked_by") in (W.ada.id, W.ada.name, W.ada.username),
              "  with the ref and who blocked it", str({k: blocks[0].get(k) for k in ("reference", "blocked_by")}))
    entries = audit_rows(x.rid)
    check(any(x.ref in f"{e['action']} {e['detail']}" and re.search(r"spam", f"{e['action']} {e['detail']}", re.I)
              for e in entries), "the history keeps the ref and the reason (spam)", str(entries)[:300])
    wait_welcome_gap(x.t)
    rep = say(x.t, "/start")
    check(BLOCKED.search(rep.text) is not None, "the blocked Telegram gets 'We can't take reports from this account here'",
          rep.why())
    check(rep.button(AGREE) is None, "  and no consent", rep.why())
    rep = photo(x.t)
    settle(0.5, x.t.id)
    check(not ever_fetched(rep.file_id), "  its photos are not downloaded", rep.why())
    y = new_sender("Yolanda", "Fitzgerald", "yolanda_tg", "+6589990011")
    rep = say(y.t, "/start")
    check(rep.button(AGREE) is not None, "another sender is unaffected: the welcome with [I agree, start]", rep.why())
    hit = rep.button(NO_THANKS)
    if check(hit is not None, "  [No thanks] is offered", rep.why()):
        rep = tap(y.t, hit, "taps")
        check(re.search(r"nothing (is |was |has been )?saved", rep.text, re.I) is not None,
              "[No thanks]: 'Nothing saved'", rep.why())
    check(not pub_rows_for(y.t.id), "  and nothing is stored for them")


def caps() -> None:
    need(W.app is not None, "the bot app is built")
    print("  per sender: 3 open")
    q = new_sender("Quentin", "Fairweather", "quentin_tg", "+6586667788",
                   statement="Side mirror knocked off in a narrow lane.")
    for i in range(3):
        send_report(q, photos=0, what=f"(Q's report {i + 1})")
    check(len(pub_rows_for(q.t.id)) == 3, "the sender has 3 open reports", f"{len(pub_rows_for(q.t.id))}")
    before = report_ids()
    rep = start_form(q)
    check(CANT_TAKE.search(rep.text) is not None, "a 4th from the same sender: 'can't take new reports'", rep.why())
    check(not re.search(r"what'?s your name|1\s*/\s*6", rep.text, re.I), "  the form doesn't start (no name question)",
          rep.why())
    check(report_ids() == before, "  nothing stored")

    print("  per phone: 3 open")
    r_ = new_sender("Rosalind", "Pemberton", "rosalind_tg", q.phone)
    rep = start_form(r_)
    need(re.search(r"\bname\b", rep.text, re.I) and not CANT_TAKE.search(rep.text),
         "another sender can start (their own count is 0)", rep.why())
    rep = say(r_.t, r_.name)
    need(rep.button(ROLE_BUTTONS["driver"]) is not None, "  name taken, role asked", rep.why())
    rep = tap(r_.t, rep.button(ROLE_BUTTONS["driver"]), "picks")
    rep = contact(r_.t, q.phone)
    check(CANT_TAKE.search(rep.text) is not None,
          "their own contact card carries a number with 3 open reports: 'can't take new reports'", rep.why())
    check(not re.search(r"plate", rep.text, re.I), "  and the form goes no further", rep.why())
    say(r_.t, "/cancel")
    check(report_ids() == before, "  nothing stored")

    print("  50 open in total")
    have = open_untaken()
    W.seeded = []
    if have < 49:
        W.seeded += seed_public(49 - have, submitted=utcnow() - timedelta(hours=2))
    check(open_untaken() == 49, "(seeded straight into the database up to 49 open untaken reports)", f"{open_untaken()}")
    g = new_sender("Gideon", "Ashcombe", "gideon_tg", "+6581230001")
    rep = start_form(g)
    check(re.search(r"\bname\b", rep.text, re.I) is not None and not CANT_TAKE.search(rep.text),
          "at 49 open, a new sender can still start", rep.why())
    say(g.t, "/cancel")
    W.seeded += seed_public(1, submitted=utcnow() - timedelta(hours=2))
    check(open_untaken() == 50, "(now 50 open untaken reports)", f"{open_untaken()}")
    h = new_sender("Hester", "Lockwood", "hester_tg", "+6581230002")
    rep = start_form(h)
    check(CANT_TAKE.search(rep.text) is not None, "at 50 open, a new sender is told 'can't take new reports'", rep.why())
    check(not pub_rows_for(h.t.id), "  nothing stored")
    unseed(W.seeded)
    check(open_untaken() < 50, "(the seeded rows are removed again)")


def wording_and_leaks() -> None:
    public_texts = [e.text for e in FAKE.log if e.chat_id in PUBLIC_CHATS and e.text and e.kind in _TEXT_KINDS]
    check(public_texts, f"(the bot sent {len(public_texts)} messages to public senders)")
    bad = [t for t in public_texts if re.search(r"call us|please call", t, re.I)]
    check(not bad, "public_contact_phone is empty: no reply says 'call us on ...' (p6_decisions §1)",
          " | ".join(b[:120] for b in bad[:3]))
    bad = [t for t in public_texts if re.search(r"\{|\}|\bNone\b", t)]
    check(not bad, "no reply holds an unfilled {placeholder} or 'None'", " | ".join(b[:120] for b in bad[:3]))
    links = [u for t in public_texts for u in re.findall(r"https?://\S+", t)]
    check(all(u.startswith(f"{PUBLIC_BASE_URL}/privacy") for u in links),
          "the only link ever sent to the public is the privacy notice", str(links[:4]))
    check(not SLOW_DOWN, "the bot never told a test sender to slow down (the test paces well under the limits)",
          "; ".join(SLOW_DOWN[:3]))

    entries = audit_rows()
    bad = [f"{s.name}: {e['action']} {e['detail']}" for e in entries for s in SENDERS
           if leaks_in(f"{e['action']} {e['detail']} {e['user_name']}", s)]
    check(not bad, f"no audit entry holds any public sender's name or phone ({len(SENDERS)} senders)", "; ".join(bad[:3]))
    logged = [(lvl, n, m) for lvl, n, m in LOG_RECORDS if lvl >= logging.INFO or n.startswith("app")]
    found = []
    for s in SENDERS:
        for lvl, n, m in logged:
            said = s.statement and s.statement[:30] in m and (lvl >= logging.INFO or n.startswith("app.channels"))
            hit = leaks_in(m, s) + (["the statement"] if said else [])
            if hit:
                found.append(f"{n}: {hit} in {m[:120]!r}")
                break
    check(not found, "no log line holds a sender's name, phone or statement", "; ".join(found[:3]))
    secret = BOT_TOKEN.split(":", 1)[1]
    # INFO and up, as in P5's e2e: python-telegram-bot's own DEBUG lines print the API URL.
    check(not any(secret in m for lvl, _n, m in LOG_RECORDS if lvl >= logging.INFO), "the bot token is in no log line (INFO+)")
    hosts = Counter(NET_REFUSED)
    others = {h: n for h, n in hosts.items() if "telegram.org" not in h}
    check(not others, "nothing tried to reach any outside host but Telegram's", str(others))
    check(not STATUS_500, "no API response was a 500", "; ".join(STATUS_500[:4]))
    check(not BOT_ERRORS, "the bot never raised while handling an update",
          "; ".join(BOT_ERRORS[:3]) + (f" (+{len(BOT_ERRORS) - 3} more)" if len(BOT_ERRORS) > 3 else ""))


def main() -> int:
    for name, fn in (
        ("setup: the api, the bot, a recording fake Telegram", setup),
        ("data: public_submissions, public_blocks", data_model),
        ("1. a stranger sends a report", journey),
        ("2. the reply and what was stored", the_reply),
        ("3. inbox: who sees it", inbox_who),
        ("4. before anyone takes it", before_take),
        ("5. take, Start, and what Gemini sees", take_and_start),
        ("6. finalize", finalize),
        ("7a. bot: the phone must be the sender's own", phone_rules),
        ("7b. bot: linked staff still get the P5 flow", linked_staff),
        ("7c. bot: /withdraw and a forged callback", withdraw_flow),
        ("7d. spam discard and block", spam_block),
        ("8. caps", caps),
        ("wording, leaks and errors", wording_and_leaks),
    ):
        run_section(name, fn)
    for p in getattr(W, "patches", []):
        try:
            p.stop()
        except RuntimeError:
            pass
    try:
        W.boot.__exit__(None, None, None)
    except Exception:
        pass
    print(f"\ntest_public_e2e: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    by_section = Counter(s for s, _ in FAILURES)
    for section, n in by_section.items():
        print(f"  {n:3d} failed in [{section}]")
    for section, label in FAILURES:
        print(f"  [{section}] {label}")
    if INFO:
        print("\nnotes:")
        for line in INFO:
            print(f"  {line}")
    if FAILURES:
        return 1
    print("\nALL PUBLIC E2E CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
