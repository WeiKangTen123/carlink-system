"""Independent end-to-end tests for release P5, "Telegram connect" (agent P5-T).

    python scripts/test_telegram_link_e2e.py

Written from the spec, not from the code under test:
plans/2026-10-26-roles-ai-telegram/p5_build_brief.md ("Owner decisions in
force", "Data", "API", "Bot", "Independent tests"; the brief wins on any
conflict), with agent6_security_report.md H2, M6, D5 and D8 as background.

What it drives:
- The real HTTP API, through TestClient, with people made straight in the
  database and signed in with a session cookie.
- The real bot handlers, from app.channels.telegram.build_app(). Every
  incoming update is the JSON Telegram would send (Update.de_json). It is
  dispatched the way python-telegram-bot's Application does it: groups in
  order, the first matching handler in each group, and ApplicationHandlerStop
  ends it. A recording fake bot stands in for Telegram. It keeps every
  message, edit, button, document, callback answer, get_file and download.
- google.genai.Client is replaced by a fake that records which key each call
  was made with.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database. Every outside connection and DNS lookup is refused. The keys and
the bot token are made up.

Failures are assertion lines grouped by section ([api], [bot: ...],
[filing], ...). A section stops at its first missing prerequisite
("rest of this section skipped"), never with a crash in the test's setup.
The bot sections fail until P5-BOT has finished: that is expected.
"""
import asyncio
import hashlib
import html
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
REPO = ROOT.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
_tmp = Path(tempfile.mkdtemp(prefix="tg-link-e2e-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
SECRETS = (_tmp / "secrets").resolve()
DB_FILE = (_tmp / "test.db").resolve()


def make_key(label: str) -> str:
    """A made-up key shaped like a Gemini one, different for each label."""
    return ("AIzaSy" + label + "x" * 40)[:35] + hashlib.md5(label.encode()).hexdigest()[:4]


ENV_COMPANY_KEY = make_key("CompanyEnv")
BOT_USERNAME = "carlink_e2e_bot"
BOT_ID = 7000000001
BOT_TOKEN = f"{BOT_ID}:AAE2E-fake-bot-token-that-must-never-be-logged"
PUBLIC_BASE_URL = "https://carlink-e2e.example"
OLD_HOST = "carlink.34-45-253-162.sslip.io"  # the hard-coded link before P5
OLD_ALLOWED_ID = 880000001  # listed in the old P0 TELEGRAM_ALLOWED_IDS: must mean nothing now

# Forced, not setdefault: never a real folder, key or token from the environment.
os.environ.update({
    "STORAGE_DIR": str(STORAGE),
    "SECRETS_DIR": str(SECRETS),
    "DATABASE_URL": f"sqlite:///{DB_FILE.as_posix()}",
    "GEMINI_API_KEY": ENV_COMPANY_KEY,
    "RUN_BACKGROUND_CLEANUP": "false",
    "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
    "TELEGRAM_ALLOWED_IDS": str(OLD_ALLOWED_ID),
    "PUBLIC_BASE_URL": PUBLIC_BASE_URL,
    "TWILIO_ACCOUNT_SID": "",
    "TWILIO_AUTH_TOKEN": "",
    "GEMINI_MAX_REQUESTS_PER_MINUTE": "100000",
    "GEMINI_MIN_CALL_INTERVAL_SECONDS": "0",
    # The disk guard is not what this test is about.
    "MIN_FREE_DISK_GB": "0",
})
for _var in ("GOOGLE_API_KEY", "TELEGRAM_BOT_USERNAME"):
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

# A rate limiter waiting for a slot in a worker thread: shortened.
_real_sleep = time.sleep


def _quick_sleep(seconds):
    if seconds > 0.2 and threading.current_thread() is not threading.main_thread():
        seconds = 0.01
    _real_sleep(seconds)


time.sleep = _quick_sleep

# ================================================================ fake Google
from PIL import Image  # noqa: E402

_fake_lock = threading.Lock()
CALLS: list[dict] = []   # one per generation call: the key its client was made with
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
    with _fake_lock:
        CALLS.append({"key": client.key, "given": not client.implicit, "purpose": purpose,
                      "thread": threading.current_thread().name})
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

from app.api.main import app  # noqa: E402
from app.auth.core import SESSION_COOKIE, create_session  # noqa: E402
from app.auth.passwords import hash_password  # noqa: E402
from app.config import settings  # noqa: E402
from app.conversation import state as conv  # noqa: E402
from app.reports.db import SessionLocal, engine  # noqa: E402
from app.reports.models import Report, User  # noqa: E402
from app.storage.files import tmp_dir  # noqa: E402

try:
    import app.api.ai_keys as ai_keys_api  # noqa: E402
except Exception:  # pragma: no cover - P3 is live, so this is there
    ai_keys_api = None

# ================================================================ harness
BASE = "https://carlink.test"
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # 31: no 0/O/1/I/L
CODE_RE = re.compile(rf"[{ALPHABET}]{{4}}-[{ALPHABET}]{{4}}")
DEEP_RE = re.compile(rf"(?:https?://)?t\.me/{re.escape(BOT_USERNAME)}\?start=link-([A-Za-z0-9_-]+)")
STATUS_KEYS = {"connected", "telegram_username", "telegram_name", "linked_at", "code", "bot_username"}
CODE_KEYS = {"display", "expires_at", "deep_link", "waiting"}
AI_OFF = "AI is off for your account"
HISTORY_DETAIL = "Filed through the Telegram bot"
HINT_REASON = "sent from their Telegram"

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
INFO: list[str] = []
_section = {"name": "setup"}
RESPONSES: list[str] = []
STATUS_500: list[str] = []
BOT_ERRORS: list[str] = []
CODES: set[str] = set()   # every plain code the API handed out
KEY_LABEL: dict[str, str] = {ENV_COMPANY_KEY: "the company key (GEMINI_API_KEY)"}
W = SimpleNamespace(app=None, bot_built=False)


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
            STATUS_500.append(f"{args[0] if args else kwargs.get('method')} {args[1] if len(args) > 1 else kwargs.get('url')} -> {r.status_code}")
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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def flat(code: str) -> str:
    return (code or "").replace("-", "").replace(" ", "").upper()


# ---------------------------------------------------------------- database
def sql(query: str, **params) -> list[dict]:
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(text(query), params)]


def sql_write(query: str, **params) -> int:
    with engine.begin() as conn:
        return conn.execute(text(query), params).rowcount


def table_exists(name: str) -> bool:
    return bool(sql("SELECT name FROM sqlite_master WHERE type='table' AND name=:n", n=name))


def code_rows(user_id: str) -> list[dict]:
    if not table_exists("chat_link_codes"):
        return []
    return sql("SELECT * FROM chat_link_codes WHERE user_id = :u", u=user_id)


def link_rows(*, user_id: str | None = None, external_id: str | None = None) -> list[dict]:
    if not table_exists("chat_links"):
        return []
    if user_id is not None:
        return sql("SELECT * FROM chat_links WHERE user_id = :u", u=user_id)
    return sql("SELECT * FROM chat_links WHERE external_id = :e", e=str(external_id))


def still_open(rows: list[dict]) -> list[dict]:
    """Code rows still usable: not revoked, not linked, not expired."""
    now = utcnow()
    out = []
    for row in rows:
        exp = parse_time(row.get("expires_at"))
        if row.get("revoked_at") is None and row.get("linked_at") is None and exp is not None and exp > now:
            out.append(row)
    return out


def _db_time_like(sample, dt: datetime) -> str:
    """dt written the way the column already holds its values."""
    sample = sample if isinstance(sample, str) else "2000-01-01 00:00:00.000000"
    out = dt.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d" + ("T" if "T" in sample else " ") + "%H:%M:%S")
    if "." in sample:
        out += f".{dt.microsecond:06d}"
    if sample.endswith("Z"):
        out += "Z"
    elif re.search(r"[+-]\d\d:\d\d$", sample):
        out += "+00:00"
    return out


def expire_codes(user_id: str) -> int:
    """Moves the person's codes' expiry 5 minutes into the past."""
    rows = code_rows(user_id)
    if not rows:
        return 0
    past = _db_time_like(rows[0].get("expires_at"), utcnow() - timedelta(minutes=5))
    return sql_write("UPDATE chat_link_codes SET expires_at = :t WHERE user_id = :u", t=past, u=user_id)


def report_ids() -> set[str]:
    return {r["id"] for r in sql("SELECT id FROM reports")}


def new_reports(before: set[str], **where) -> list[dict]:
    rows = sql("SELECT id, owner_id, channel, reporter_chat_id, status FROM reports")
    out = [r for r in rows if r["id"] not in before]
    for k, v in where.items():
        out = [r for r in out if r.get(k) == v]
    return out


def audit_mark() -> int:
    rows = sql("SELECT MAX(id) AS m FROM audit_log")
    return rows[0]["m"] or 0


def audit_since(mark: int) -> list[dict]:
    return sql("SELECT id, report_id, user_id, user_name, action, detail FROM audit_log WHERE id > :m ORDER BY id", m=mark)


def mentions_telegram(entries: list[dict]) -> list[dict]:
    return [e for e in entries if "telegram" in f"{e.get('action')} {e.get('detail')}".lower()]


# ---------------------------------------------------------------- people
class Person:
    def __init__(self, name: str, username: str, *, role: str = "user", password: str | None = None, **ticks):
        self.name = name
        self.label = name.split()[0]
        self.username = username
        self.email = f"{username}.{name.split()[-1].lower()}@carlink-e2e.test"
        self.password = password
        self.key = None
        db = SessionLocal()
        try:
            user = User(email=self.email, username=username, name=name, role=role,
                        password_hash=hash_password(password) if password else "!", must_change_password=False, **ticks)
            db.add(user)
            db.commit()
            self.id = user.id
        finally:
            db.close()
        self.c = None
        self.sign_in()

    def sign_in(self) -> None:
        """A fresh session, as if they signed in again (the old one may have ended)."""
        db = SessionLocal()
        try:
            token = create_session(db, db.get(User, self.id))
            db.commit()
        finally:
            db.close()
        self.c = browser()
        self.c.cookies.set(SESSION_COOKIE, token)

    @property
    def masked_email(self) -> str:
        local, domain = self.email.split("@")
        return f"{local[0]}***@{domain}"

    def status(self) -> dict:
        return jd(self.c.get("/auth/me/telegram"))

    def save_key(self) -> None:
        if ai_keys_api is not None and hasattr(ai_keys_api, "reset_limits"):
            ai_keys_api.reset_limits()
        key = make_key(self.label + "Own")
        KEY_LABEL[key] = f"{self.label}'s own key"
        r = self.c.put("/auth/me/ai-key", json={"api_key": key, "billing_confirmed": True})
        need(r.status_code == 200 and jd(r).get("status") == "ok", f"(setup) {self.label} saves an own AI key (P3)", brief(r))
        self.key = key


def anon() -> Browser:
    return browser()


# ---------------------------------------------------------------- fake Telegram
@dataclass
class Button:
    text: str
    data: str | None = None
    url: str | None = None


@dataclass
class Sent:
    kind: str                 # message | edit | markup | document | photo | media | answer | get_file | download | other
    chat_id: int | None
    text: str = ""
    buttons: list = field(default_factory=list)
    raw: dict | None = None   # the message as Telegram returns it (for taps on its buttons)
    method: str = ""
    filename: str = ""
    path: str = ""


def _cid(chat_id):
    try:
        return int(chat_id)
    except (TypeError, ValueError):
        return chat_id


def _buttons(markup) -> list[Button]:
    out = []
    for row in getattr(markup, "inline_keyboard", None) or ():
        for b in row:
            out.append(Button(str(getattr(b, "text", "")), getattr(b, "callback_data", None), getattr(b, "url", None)))
    for row in getattr(markup, "keyboard", None) or ():
        for b in row:
            out.append(Button(str(getattr(b, "text", b))))
    return out


PHOTO_BYTES = io.BytesIO()
Image.new("RGB", (320, 240), (200, 40, 40)).save(PHOTO_BYTES, "JPEG", quality=90)
PHOTO_BYTES = PHOTO_BYTES.getvalue()

_API_METHOD = re.compile(r"^(send|edit|answer|get|set|delete|copy|forward|pin|unpin|leave|ban|unban|restrict|promote|"
                         r"export|create|revoke|approve|decline|close|reopen|stop|log|refund|upload)_")


class FakeFile:
    def __init__(self, bot: "FakeTelegram", file_id: str, chat_id):
        self._bot, self._chat = bot, chat_id
        self.file_id = file_id
        self.file_unique_id = "u" + file_id
        self.file_size = len(PHOTO_BYTES)
        self.file_path = f"photos/{file_id}.jpg"

    async def download_to_drive(self, custom_path=None, **_kw):
        path = Path(custom_path) if custom_path else (_tmp / "stray-downloads" / f"{self.file_id}.jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PHOTO_BYTES)
        self._bot.log.append(Sent("download", self._chat, method="download_to_drive", path=str(path)))
        return path

    async def download_as_bytearray(self, buf=None, **_kw):
        self._bot.log.append(Sent("download", self._chat, method="download_as_bytearray"))
        return bytearray(PHOTO_BYTES)

    async def download_to_memory(self, out=None, **_kw):
        self._bot.log.append(Sent("download", self._chat, method="download_to_memory"))
        if out is not None:
            out.write(PHOTO_BYTES)


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
        chat = self.chats.get(cid) or {"id": cid, "type": "private" if isinstance(cid, int) and cid > 0 else "group",
                                       **({"first_name": "x"} if isinstance(cid, int) and cid > 0 else {"title": "x"})}
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
        self.log.append(Sent("message", _cid(chat_id), str(text or ""), _buttons(reply_markup), raw, "send_message"))
        return msg

    async def edit_message_text(self, text=None, chat_id=None, message_id=None, inline_message_id=None, *args,
                                reply_markup=None, **kwargs):
        raw, msg = self._message(chat_id, text, reply_markup)
        if message_id is not None:
            raw["message_id"] = message_id
            msg = Message.de_json(raw, self)
        self.log.append(Sent("edit", _cid(chat_id), str(text or ""), _buttons(reply_markup), raw, "edit_message_text"))
        return msg if chat_id is not None else True

    async def edit_message_reply_markup(self, chat_id=None, message_id=None, inline_message_id=None, reply_markup=None,
                                        *args, **kwargs):
        self.log.append(Sent("markup", _cid(chat_id), "", _buttons(reply_markup), None, "edit_message_reply_markup"))
        return True

    async def send_document(self, chat_id=None, document=None, *args, filename=None, caption=None, reply_markup=None, **kwargs):
        name = filename or getattr(document, "name", "") or ""
        self.log.append(Sent("document", _cid(chat_id), str(caption or ""), _buttons(reply_markup), None, "send_document",
                             filename=str(name)))
        raw, msg = self._message(chat_id, None, reply_markup)
        return msg

    async def send_photo(self, chat_id=None, photo=None, *args, caption=None, reply_markup=None, **kwargs):
        self.log.append(Sent("photo", _cid(chat_id), str(caption or ""), _buttons(reply_markup), None, "send_photo"))
        raw, msg = self._message(chat_id, None, reply_markup)
        return msg

    async def send_media_group(self, chat_id=None, media=None, *args, **kwargs):
        self.log.append(Sent("media", _cid(chat_id), "", [], None, "send_media_group"))
        return []

    async def answer_callback_query(self, callback_query_id=None, text=None, *args, **kwargs):
        self.log.append(Sent("answer", self.queries.get(callback_query_id), str(text or ""), [], None, "answer_callback_query"))
        return True

    async def get_file(self, file_id=None, *args, **kwargs):
        chat = self.files.get(file_id)
        self.log.append(Sent("get_file", chat, method="get_file"))
        return FakeFile(self, str(file_id), chat)

    def __getattr__(self, name):
        if not _API_METHOD.match(name):
            raise AttributeError(name)

        async def record(*args, **kwargs):
            self.log.append(Sent("other", _cid(kwargs.get("chat_id", args[0] if args else None)),
                                 str(kwargs.get("text") or ""), _buttons(kwargs.get("reply_markup")), None, name))
            return True

        return record


FAKE = FakeTelegram()


class TgUser:
    _next = [880000100]

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


def group_chat(chat_id: int, kind: str = "group") -> dict:
    return {"id": chat_id, "type": kind, "title": "Workshop crew"}


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

    def message(self, t: TgUser, *, chat=None, text_=None, photo=False, document=None, caption=None, forwarded=False) -> Update:
        uid, mid = self._next()
        chat = chat or private_chat(t)
        self.bot.chats[chat["id"]] = chat
        msg = {"message_id": mid, "date": int(time.time()), "chat": chat, "from": t.json()}
        if text_ is not None:
            msg["text"] = text_
            if text_.startswith("/"):
                msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text_.split()[0])}]
        if photo:
            fid = f"ph{abs(chat['id'])}m{mid}"
            for f in (fid + "s", fid):
                self.bot.files[f] = chat["id"]
            msg["photo"] = [
                {"file_id": fid + "s", "file_unique_id": "u" + fid + "s", "width": 90, "height": 68, "file_size": 1200},
                {"file_id": fid, "file_unique_id": "u" + fid, "width": 1280, "height": 960, "file_size": len(PHOTO_BYTES)},
            ]
        if document:
            fid = f"doc{abs(chat['id'])}m{mid}"
            self.bot.files[fid] = chat["id"]
            msg["document"] = {"file_id": fid, "file_unique_id": "u" + fid, "file_name": document[1],
                               "mime_type": document[0], "file_size": len(PHOTO_BYTES)}
        if caption:
            msg["caption"] = caption
        if forwarded:
            msg["forward_origin"] = {"type": "user", "date": int(time.time()) - 3600,
                                     "sender_user": {"id": 555000111, "is_bot": False, "first_name": "Someone Else"}}
        return Update.de_json({"update_id": uid, "message": msg}, self.bot)

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


def plain_html(s: str) -> str:
    """Text as the person reads it: no HTML tags, entities or MarkdownV2 escapes."""
    s = re.sub(r"<[^>]+>", "", s or "")
    s = html.unescape(s)
    return re.sub(r"\\([_*\[\]()~`>#+\-=|{}.!])", r"\1", s)


def plain(s: str) -> str:
    return plain_html(s).replace("*", "").replace("`", "")


class Reply:
    """What the bot did in answer to one update, in that chat."""

    def __init__(self, entries: list[Sent], error: str | None, chat_id):
        self.chat_id = chat_id
        self.everything = entries
        self.entries = [e for e in entries if e.chat_id == chat_id]
        self.error = error

    @property
    def texts(self) -> list[str]:
        return [plain(e.text) for e in self.entries if e.text and e.kind in _TEXT_KINDS]

    @property
    def raw_texts(self) -> list[str]:
        return [plain_html(e.text) for e in self.entries if e.text and e.kind in _TEXT_KINDS]

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
    def prompt(self):
        """The [Yes, that's me] button of a link prompt, if one was shown."""
        return self.button(r"\byes\b")

    @property
    def asked(self) -> bool:
        return self.prompt is not None or "connect this telegram" in self.text.lower()

    @property
    def downloads(self) -> int:
        return sum(1 for e in self.entries if e.kind in ("get_file", "download"))

    @property
    def documents(self) -> list[Sent]:
        return [e for e in self.entries if e.kind in ("document", "photo", "media")]

    @property
    def source(self) -> str:
        """Everything sent, as sent (HTML hrefs included) and as read."""
        sent = [e.text for e in self.entries if e.text and e.kind in _TEXT_KINDS]
        return "\n".join(sent + self.raw_texts)

    def urls(self) -> list[str]:
        found = re.findall(r"https?://[^\s<>()\[\]\"'\\]+", self.source)
        return found + [b.url for b in self.buttons if b.url]

    @property
    def code_talk(self) -> bool:
        """Answered as if a link code had been sent."""
        t = self.text.lower()
        return self.asked or bool(re.search(
            r"(wrong|invalid|expired|didn.t work|not valid|unknown|used).{0,30}\bcodes?\b|\bcodes?\b.{0,30}"
            r"(wrong|invalid|expired|didn.t work|not valid|isn.t valid|unknown|already used)", t))

    def why(self) -> str:
        parts = []
        if self.texts:
            parts.append("bot said: " + " | ".join(t.replace("\n", " ")[:150] for t in self.texts[:4]))
        if self.buttons:
            parts.append("buttons: " + ", ".join(f"[{b.text}]" for b in self.buttons[:8]))
        kinds = Counter(e.kind for e in self.entries if e.kind not in ("message", "edit", "answer"))
        if kinds:
            parts.append("also: " + ", ".join(f"{n} x {k}" for k, n in kinds.items()))
        others = [e for e in self.everything if e.chat_id != self.chat_id]
        if others:
            parts.append(f"(+{len(others)} to other chats)")
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


def deliver(update: Update, what: str, chat_id) -> Reply:
    if W.app is None:
        return Reply([], "the bot app was not built (see [setup])", chat_id)
    mark = len(FAKE.log)
    error = None
    try:
        LOOP.run_until_complete(asyncio.wait_for(_dispatch(update), 90))
    except Exception as exc:
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if "app" in Path(f.filename).parts]
        where = f" at {Path(frames[-1].filename).name}:{frames[-1].lineno}" if frames else ""
        error = f"{type(exc).__name__}: {exc}{where}"
        BOT_ERRORS.append(f"{what}: {mask(error)}")
    return Reply(FAKE.log[mark:], error, chat_id)


def say(t: TgUser, words: str, *, chat=None, forwarded=False) -> Reply:
    chat = chat or private_chat(t)
    return deliver(WIRE.message(t, chat=chat, text_=words, forwarded=forwarded), f"{t.label} says {words!r}", chat["id"])


def photo(t: TgUser, *, chat=None, caption=None) -> Reply:
    chat = chat or private_chat(t)
    return deliver(WIRE.message(t, chat=chat, photo=True, caption=caption), f"{t.label} sends a photo", chat["id"])


def document(t: TgUser, mime: str, name: str, *, chat=None) -> Reply:
    chat = chat or private_chat(t)
    return deliver(WIRE.message(t, chat=chat, document=(mime, name)), f"{t.label} sends {name}", chat["id"])


def tap(t: TgUser, hit, what: str = "taps a button") -> Reply:
    sent, button = hit
    if button.data is None:  # a reply-keyboard button: tapping it sends its text
        return say(t, button.text)
    return deliver(WIRE.tap(t, sent, button.data), f"{t.label} {what} [{button.text}]", sent.chat_id)


def sessions_for(*ids) -> list[str]:
    keys = list(getattr(conv, "_sessions", {}).keys())
    return [k for k in keys if any(str(i) in str(k) for i in ids)]


def session_photos(t: TgUser) -> list[str]:
    out = []
    for k in sessions_for(t.id):
        out += list(getattr(conv._sessions[k], "photo_paths", []) or [])
    return out


def tmp_files() -> set[str]:
    try:
        return set(os.listdir(tmp_dir()))
    except OSError:
        return set()


def calls_since(mark: int) -> list[dict]:
    with _fake_lock:
        return list(CALLS[mark:])


def describe(calls: list[dict]) -> str:
    if not calls:
        return "no Gemini call was made"
    counts = Counter(f"{c['purpose']} on {KEY_LABEL.get(c['key'], 'a key nobody saved') if c['key'] else 'no key'}" for c in calls)
    return "; ".join(f"{n} x {w}" for w, n in counts.most_common(5))


def all_on(calls: list[dict], allowed: set) -> bool:
    return bool(calls) and all(c["key"] in allowed for c in calls)


def random_code() -> str:
    rng = random.SystemRandom()
    while True:
        c = "".join(rng.choice(ALPHABET) for _ in range(8))
        c = f"{c[:4]}-{c[4:]}"
        if c not in CODES:
            return c


# ---------------------------------------------------------------- linking steps
def make_code(p: Person, what: str = "") -> tuple[str, dict]:
    r = p.c.post("/auth/me/telegram/code")
    need(r.status_code == 200, f"{what or '(setup)'} {p.label} makes a link code: POST /auth/me/telegram/code -> 200", brief(r))
    body = jd(r)
    display = str(((body.get("code") or {}).get("display")) or "").strip()
    need(CODE_RE.fullmatch(display), f"{what or '(setup)'} the code is shown as XXXX-XXXX", repr(display))
    CODES.add(display)
    return display, body


def start_payload(body: dict, display: str) -> str:
    deep = str(((body.get("code") or {}).get("deep_link")) or "")
    m = DEEP_RE.fullmatch(deep)
    return f"link-{m.group(1)}" if m else f"link-{display}"


def waiting_of(p: Person) -> dict | None:
    s = p.status()
    return (s.get("code") or {}).get("waiting")


def telegram_says_yes(p: Person, t: TgUser, display: str, body: dict, what: str = "(setup)") -> None:
    rep = say(t, f"/start {start_payload(body, display)}")
    need(rep.prompt is not None, f"{what} the bot asks {t.full_name} to confirm {p.label}'s code", rep.why())
    rep = tap(t, rep.prompt, "taps")
    need(isinstance(waiting_of(p), dict), f"{what} after [Yes] in Telegram, {p.label}'s code is waiting for Carlink",
         f"code {p.status().get('code')!r}; {rep.why()}")


def link_up(p: Person, t: TgUser, what: str = "(setup)") -> None:
    """The full two-sided link, with every step required."""
    display, body = make_code(p, what)
    telegram_says_yes(p, t, display, body, what)
    r = p.c.post("/auth/me/telegram/confirm", json={"accept": True})
    need(r.status_code == 200 and jd(r).get("connected") is True,
         f"{what} {p.label} confirms in Carlink: connected to {t.full_name}", brief(r))


def whoami(t: TgUser) -> Reply:
    return say(t, "/whoami")


def connected_as(rep: Reply, p: Person) -> bool:
    return "connected as" in rep.text.lower() and p.name in rep.text and "not connected" not in rep.text.lower()


def not_connected(rep: Reply) -> bool:
    return "not connected" in rep.text.lower()


def users_row(admin: Person, p: Person) -> dict | None:
    r = admin.c.get("/users")
    rows = r.json() if r.status_code == 200 and isinstance(r.json(), list) else []
    return next((u for u in rows if u.get("id") == p.id), None)


def confirm_button(replies: list[Reply]):
    for rep in reversed(replies):
        hit = rep.button(r"confirm")
        if hit:
            return hit
    return None


# ================================================================= sections
def setup() -> None:
    W.boot = browser()
    W.boot.__enter__()  # the api's start-up: tables, the AI key secret
    W.ada = Person("Ada Admin", "ada", role="admin", password="admin harbour violet lantern")
    W.ben = Person("Ben Boss", "ben", role="admin")

    import app.channels.telegram as tg
    W.tg = tg

    # No Chromium: wherever a PDF gets made, a fake one is written instead.
    def fake_render(*args, **kwargs):
        out = kwargs.get("output_path") or (args[2] if len(args) > 2 else None)
        if out:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(b"%PDF-1.4 fake")
    W.patches = []
    import app.api.main as api_main
    import app.rendering.renderer as renderer
    for module in (tg, api_main, renderer):
        if hasattr(module, "render_pdf"):
            p = mock.patch.object(module, "render_pdf", fake_render)
            p.start()
            W.patches.append(p)
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


def config() -> None:
    check(getattr(settings, "public_base_url", None) == PUBLIC_BASE_URL,
          "settings.public_base_url comes from PUBLIC_BASE_URL", repr(getattr(settings, "public_base_url", "<missing>")))
    check(not hasattr(settings, "telegram_allowed_ids"),
          "the P0 TELEGRAM_ALLOWED_IDS setting is gone from config", repr(getattr(settings, "telegram_allowed_ids", None)))
    prod = REPO / "infra" / "docker-compose.prod.yml"
    need(prod.exists(), "infra/docker-compose.prod.yml is there", str(prod))
    body = prod.read_text(encoding="utf-8")
    check("TELEGRAM_ALLOWED_IDS" not in body, "prod compose no longer passes TELEGRAM_ALLOWED_IDS")
    want = "PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-https://carlink.34-45-253-162.sslip.io}"
    try:
        import yaml
        services = (yaml.safe_load(body) or {}).get("services") or {}

        def env_of(name):
            env = (services.get(name) or {}).get("environment") or []
            return [str(e) for e in env] if isinstance(env, list) else [f"{k}={v}" for k, v in env.items()]
        for svc in ("api", "telegram-bot"):
            check(want in env_of(svc), f"prod compose passes {want} to {svc}", str([e for e in env_of(svc) if "PUBLIC" in e]))
    except ImportError:
        check(want in body, f"prod compose has {want}")


def data() -> None:
    need(table_exists("chat_links"), "table chat_links exists")
    need(table_exists("chat_link_codes"), "table chat_link_codes exists")
    want = {
        "chat_links": {"id", "channel", "external_id", "user_id", "external_username", "external_name", "linked_at", "last_used_at"},
        "chat_link_codes": {"id", "channel", "user_id", "code_hash", "created_at", "expires_at", "used_by_external_id",
                            "used_by_username", "used_by_name", "waiting_since", "linked_at", "revoked_at"},
    }
    for table, cols in want.items():
        have = {r["name"] for r in sql(f"PRAGMA table_info({table})")}
        check(cols <= have, f"{table} has the brief's columns", f"missing {sorted(cols - have)}")
    ddl = re.sub(r"\s+", " ", sql("SELECT sql FROM sqlite_master WHERE type='table' AND name='chat_links'")[0]["sql"] or "")

    def unique_inside(a, b):
        q = r'["`\[]?'
        pat = rf"UNIQUE \( ?{q}({a}|{b}){q} ?, ?{q}({a}|{b}){q} ?\)"
        return any({m.group(1), m.group(2)} == {a, b} for m in re.finditer(pat, ddl, re.I))
    check(unique_inside("channel", "external_id"), "UNIQUE(channel, external_id) is inside the CREATE TABLE", ddl[:300])
    check(unique_inside("channel", "user_id"), "UNIQUE(channel, user_id) is inside the CREATE TABLE", ddl[:300])
    indexes = sql("PRAGMA index_list(chat_link_codes)")
    cols_of = {ix["name"]: [c["name"] for c in sql(f"PRAGMA index_info('{ix['name']}')")] for ix in indexes}
    check(any(ix["unique"] and cols_of[ix["name"]] == ["code_hash"] for ix in indexes), "chat_link_codes.code_hash is unique",
          str({ix["name"]: (bool(ix["unique"]), cols_of[ix["name"]]) for ix in indexes}))
    check(any(cols_of[ix["name"]][:1] == ["user_id"] for ix in indexes), "chat_link_codes.user_id is indexed",
          str(cols_of))
    rows = sql("SELECT value FROM app_settings WHERE key = 'telegram_bot_username'") if table_exists("app_settings") else []
    check(rows and rows[0]["value"] == BOT_USERNAME,
          "the bot's start-up (getMe) wrote app setting telegram_bot_username", str(rows))


def api() -> None:
    dot = W.dot = Person("Dot Lee", "dot")
    r = anon().get("/auth/me/telegram")
    check(r.status_code == 401, "signed out: GET /auth/me/telegram -> 401", brief(r))
    r = dot.c.get("/auth/me/telegram")
    need(r.status_code == 200, "GET /auth/me/telegram -> 200", brief(r))
    s = jd(r)
    check(STATUS_KEYS <= set(s), "the status has connected, telegram_username, telegram_name, linked_at, code, bot_username",
          f"keys {sorted(s)}")
    check(s.get("connected") is False and s.get("code") is None and s.get("telegram_username") is None
          and s.get("telegram_name") is None and s.get("linked_at") is None,
          "at first: not connected, no code", json.dumps(s)[:300])
    check(s.get("bot_username") == BOT_USERNAME, "bot_username is what the bot's start-up wrote", repr(s.get("bot_username")))

    print("  POST /auth/me/telegram/code")
    r = dot.c.post("/auth/me/telegram/code")
    need(r.status_code == 200, "POST /auth/me/telegram/code -> 200", brief(r))
    s = jd(r)
    code = s.get("code") if isinstance(s.get("code"), dict) else {}
    check(STATUS_KEYS <= set(s) and s.get("connected") is False, "it answers with the status shape", f"keys {sorted(s)}")
    check(CODE_KEYS <= set(code), "code has display, expires_at, deep_link, waiting", f"code keys {sorted(code)}")
    display = str(code.get("display") or "").strip()
    need(CODE_RE.fullmatch(display), "code.display is XXXX-XXXX from the 31-character alphabet (no 0/O/1/I/L)", repr(display))
    CODES.add(display)
    exp = parse_time(code.get("expires_at"))
    minutes = (exp - utcnow()).total_seconds() / 60 if exp else None
    check(minutes is not None and 9.0 <= minutes <= 10.5, "the code is valid for 10 minutes",
          f"expires_at {code.get('expires_at')!r} ({minutes and round(minutes, 2)} min)")
    check(code.get("waiting") is None, "nothing is waiting yet", repr(code.get("waiting")))
    m = DEEP_RE.fullmatch(str(code.get("deep_link") or ""))
    check(m and flat(m.group(1)) == flat(display), f"deep_link is t.me/{BOT_USERNAME}?start=link-<that code>",
          repr(code.get("deep_link")))

    rows = code_rows(dot.id)
    check(len(rows) == 1, "one chat_link_codes row for the person", f"{len(rows)} rows")
    if rows:
        h = str(rows[0].get("code_hash") or "")
        forms = {display, flat(display), display.lower(), flat(display).lower()}
        check(re.fullmatch(r"[0-9a-f]{64}", h) and any(hashlib.sha256(f.encode()).hexdigest() == h for f in forms),
              "code_hash is the SHA-256 of the code", f"code_hash {h[:16]}...")
        check(rows[0].get("channel") == "telegram", "the row's channel is 'telegram'", repr(rows[0].get("channel")))
    with engine.begin() as conn:
        conn.execute(text("PRAGMA wal_checkpoint(FULL)"))
    blob = b"".join(p.read_bytes() for p in _tmp.iterdir() if p.is_file() and p.name.startswith("test.db"))
    check(display.encode() not in blob and flat(display).encode() not in blob, "the plain code is nowhere in the database files")

    r = dot.c.get("/auth/me/telegram")
    g = jd(r)
    gcode = g.get("code") if isinstance(g.get("code"), dict) else None
    check(gcode is not None and parse_time(gcode.get("expires_at")) == exp,
          "GET shows the open code (same expiry) while it lasts", json.dumps(g)[:300])
    check(display not in r.text and flat(display) not in r.text, "GET never shows the plain code again (shown once)",
          json.dumps(g)[:300])

    r = dot.c.post("/auth/me/telegram/code")
    need(r.status_code == 200, "a second POST /auth/me/telegram/code -> 200", brief(r))
    second = str(((jd(r).get("code") or {}).get("display")) or "").strip()
    CODES.add(second)
    check(second and second != display, "the new code is different", f"{display} then {second}")
    first_row = [row for row in code_rows(dot.id) if row.get("code_hash") == (rows[0].get("code_hash") if rows else None)]
    check(not still_open(first_row), "a new code cancels the old one (no longer usable in the database)", str(first_row)[:300])
    check(len(still_open(code_rows(dot.id))) == 1, "exactly one open code per person", f"{len(still_open(code_rows(dot.id)))} open")

    print("  DELETE /auth/me/telegram/code")
    r = dot.c.delete("/auth/me/telegram/code")
    check(r.status_code == 200 and jd(r).get("ok") is True, "DELETE /auth/me/telegram/code -> {ok: true}", brief(r))
    check(dot.status().get("code") is None, "the status shows no code afterwards", json.dumps(dot.status())[:200])
    check(not still_open(code_rows(dot.id)), "and no open code is left in the database")

    print("  POST /auth/me/telegram/confirm with nothing waiting")
    for accept in (True, False):
        r = dot.c.post("/auth/me/telegram/confirm", json={"accept": accept})
        check(r.status_code == 409, f"no code at all: confirm {{accept: {str(accept).lower()}}} -> 409", brief(r))
    make_code(dot, "[confirm 409]")
    r = dot.c.post("/auth/me/telegram/confirm", json={"accept": True})
    check(r.status_code == 409, "an open code nobody said Yes to in Telegram: confirm -> 409", brief(r))
    check(dot.status().get("connected") is False, "and still not connected")
    dot.c.delete("/auth/me/telegram/code")

    print("  10 codes an hour per person")
    quinn = W.quinn = Person("Quinn Ong", "quinn")
    got = [quinn.c.post("/auth/me/telegram/code") for _ in range(10)]
    check(all(x.status_code == 200 for x in got), "10 codes in an hour -> all 200", str([x.status_code for x in got]))
    for x in got:
        d = str(((jd(x).get("code") or {}).get("display")) or "")
        if d:
            CODES.add(d)
    tenth = str(((jd(got[-1]).get("code") or {}).get("display")) or "")
    r = quinn.c.post("/auth/me/telegram/code")
    check(r.status_code == 429, "the 11th in the hour -> 429", brief(r))
    s = quinn.status()
    check(s.get("code") is not None and len(still_open(code_rows(quinn.id))) == 1,
          "the refused 11th leaves the 10th code open", json.dumps(s)[:200])
    if tenth and W.app is not None:
        t = TgUser("Quinn", "Phone", "quinnphone")
        rep = say(t, f"/link {tenth}")
        check(rep.prompt is not None, "(bot) the 10th code still works in Telegram", rep.why())
        if rep.prompt:
            hit = rep.button(r"cancel")
            if hit:
                tap(t, hit)

    print("  admin views")
    r = W.ada.c.get("/users")
    rows = r.json() if r.status_code == 200 and isinstance(r.json(), list) else []
    need(rows, "GET /users -> 200 with rows", brief(r))
    check(all("telegram" in u for u in rows), "every GET /users row has a telegram field",
          str([u.get("username") for u in rows if "telegram" not in u][:5]))
    mine = next((u for u in rows if u.get("id") == dot.id), {})
    check(mine.get("telegram", "missing") is None, "telegram is null for someone not connected", repr(mine.get("telegram", "missing")))
    r = W.ada.c.get("/system/info")
    tel = jd(r).get("telegram")
    check(isinstance(tel, dict) and tel.get("bot_username") == BOT_USERNAME and isinstance(tel.get("accounts_connected"), int),
          "GET /system/info has telegram {bot_username, accounts_connected}", repr(tel))
    r = W.ada.c.get("/settings")
    check(r.status_code == 200 and "telegram_bot_username" not in r.text and BOT_USERNAME not in r.text,
          "GET /settings never returns telegram_bot_username", brief(r))
    r = dot.c.delete(f"/users/{W.ada.id}/telegram")
    check(r.status_code == 403, "a user calling DELETE /users/{id}/telegram -> 403", brief(r))
    r = anon().delete("/auth/me/telegram")
    check(r.status_code == 401, "signed out: DELETE /auth/me/telegram -> 401", brief(r))


def two_sided_link() -> None:
    need(W.app is not None, "the bot app is built")
    ann = W.ann = Person("Ann Lim", "ann")
    t = W.t_ann = TgUser("Tom", "Tan", "tomtan88")
    info0 = jd(W.ada.c.get("/system/info")).get("telegram") or {}
    display, body = make_code(ann, "[link]")

    rep = say(t, f"/start {start_payload(body, display)}")
    need(rep.prompt is not None, "Telegram user T sends /start link-CODE (the deep link): the bot asks to confirm", rep.why())
    prompt_text = rep.text
    raw_text = "\n".join(rep.raw_texts)
    check("Ann Lim" in prompt_text and "@ann" in prompt_text, "the prompt names the account: Ann Lim (@ann)", prompt_text[:300])
    check(ann.masked_email in raw_text, f"the prompt shows the masked email {ann.masked_email}", raw_text[:300])
    check(ann.email not in raw_text and ann.email.split("@")[0] not in raw_text, "and never the full email", raw_text[:300])
    check("cancel" in prompt_text.lower() and "someone sent you" in prompt_text.lower(),
          "it warns: 'If someone sent you this code, tap Cancel'", prompt_text[:300])
    check(rep.button(r"cancel") is not None, "buttons [Yes, that's me] and [Cancel]", rep.why())
    check(waiting_of(ann) is None, "just showing the prompt changes nothing on the web side", repr(waiting_of(ann)))

    yes = tap(t, rep.prompt, "taps")
    check("settings" in yes.text.lower() and "my telegram" in yes.text.lower(),
          "after [Yes]: 'Almost done: confirm in Carlink -> Settings -> My Telegram'", yes.why())
    s = ann.status()
    w = (s.get("code") or {}).get("waiting")
    need(isinstance(w, dict), "GET /auth/me/telegram now shows code.waiting", json.dumps(s)[:300])
    check(str(w.get("username") or "").lstrip("@") == "tomtan88" and w.get("name") in ("Tom Tan", "Tom"),
          "waiting = {username: tomtan88, name: Tom Tan}", repr(w))
    check(s.get("connected") is False, "still not connected: one side alone isn't enough", json.dumps(s)[:200])
    rows = code_rows(ann.id)
    row = next((x for x in rows if x.get("waiting_since")), None)
    check(row is not None and str(row.get("used_by_external_id")) == str(t.id),
          "the code row records T's Telegram user id, with waiting_since set", str(rows)[:300])

    rep = whoami(t)
    check(not_connected(rep), "before the web side confirms, /whoami still says 'Not connected'", rep.why())
    rep = photo(t)
    check(rep.downloads == 0, "and a photo isn't downloaded yet", rep.why())

    r = ann.c.post("/auth/me/telegram/confirm", json={"accept": True})
    need(r.status_code == 200, "POST /auth/me/telegram/confirm {accept: true} -> 200", brief(r))
    s = jd(r)
    check(s.get("connected") is True and str(s.get("telegram_username") or "").lstrip("@") == "tomtan88"
          and s.get("telegram_name") in ("Tom Tan", "Tom") and parse_time(s.get("linked_at")) is not None and s.get("code") is None,
          "it answers connected, with T's username, name and linked_at, and no code", json.dumps(s)[:300])
    links = link_rows(user_id=ann.id)
    check(len(links) == 1 and str(links[0].get("external_id")) == str(t.id) and links[0].get("channel") == "telegram",
          "chat_links holds Ann <-> T's Telegram user id", str(links)[:300])
    row = users_row(W.ada, ann) or {}
    tel = row.get("telegram")
    check(isinstance(tel, dict) and str(tel.get("username") or "").lstrip("@") == "tomtan88" and tel.get("name") in ("Tom Tan", "Tom")
          and tel.get("linked_at"), "GET /users shows Ann's telegram {username, name, linked_at}", repr(tel))
    info1 = jd(W.ada.c.get("/system/info")).get("telegram") or {}
    if isinstance(info0.get("accounts_connected"), int):
        check(info1.get("accounts_connected") == info0["accounts_connected"] + 1,
              "GET /system/info accounts_connected went up by one", f"{info0.get('accounts_connected')} -> {info1.get('accounts_connected')}")

    rep = whoami(t)
    check(connected_as(rep, ann) and "@ann" in rep.text, "the bot now treats T as Ann: /whoami 'Connected as Ann Lim (@ann)'", rep.why())

    print("  the other ways to send a code (each on a new code)")
    val = W.val = Person("Val Goh", "val")
    for how in ("/link XXXX-XXXX", "/link XXXXXXXX", "the bare code XXXX-XXXX"):
        tv = TgUser("Vera", "Koh", None)
        display, _ = make_code(val, "[link]")
        words = {"/link XXXX-XXXX": f"/link {display}", "/link XXXXXXXX": f"/link {flat(display)}",
                 "the bare code XXXX-XXXX": display}[how]
        rep = say(tv, words)
        check(rep.prompt is not None and "Val Goh" in rep.text, f"{how} from an unlinked Telegram: the prompt for Val Goh", rep.why())
        hit = rep.button(r"cancel")
        if hit:
            rep = tap(tv, hit)
            check(waiting_of(val) is None and val.status().get("connected") is False,
                  "  [Cancel] in Telegram: nothing waiting, not connected", rep.why())


def refusals() -> None:
    need(W.app is not None, "the bot app is built")

    print("  an expired code")
    pat = Person("Pat Wong", "pat")
    t = TgUser("Eve", "Expired", "evexp")
    display, _ = make_code(pat, "[expired]")
    need(expire_codes(pat.id) >= 1, "(the test moves the code's expires_at into the past)")
    rep = say(t, f"/link {display}")
    check(not rep.asked, "an expired code: no prompt", rep.why())
    check("Pat" not in rep.text and pat.masked_email not in rep.text, "and the account isn't named", rep.why())
    display, body = make_code(pat, "[expired]")
    rep = say(t, f"/start {start_payload(body, display)}")
    need(rep.prompt is not None, "(a fresh code gets the prompt)", rep.why())
    expire_codes(pat.id)
    rep = tap(t, rep.prompt, "taps")
    check(waiting_of(pat) is None, "the code expires while the prompt is open: [Yes] doesn't make it wait", rep.why())
    display, body = make_code(pat, "[expired]")
    telegram_says_yes(pat, t, display, body, "[expired]")
    expire_codes(pat.id)
    r = pat.c.post("/auth/me/telegram/confirm", json={"accept": True})
    check(r.status_code == 409, "waiting, then expired: web confirm -> 409", brief(r))
    check(pat.status().get("connected") is False and not link_rows(user_id=pat.id), "and no link was made")

    print("  a reused code")
    rob = Person("Rob Chan", "rob")
    t1, t2, t3 = TgUser("Rae", "One", "raeone"), TgUser("Ray", "Two", "raytwo"), TgUser("Rex", "Three", "rexthree")
    display, body = make_code(rob, "[reused]")
    p1 = say(t1, f"/link {display}")
    p2 = say(t2, f"/link {display}")
    need(p1.prompt is not None and p2.prompt is not None, "(two Telegrams both see the prompt: showing it changes nothing)",
         p1.why() + " || " + p2.why())
    tap(t1, p1.prompt, "taps")
    rep = tap(t2, p2.prompt, "taps")
    w = waiting_of(rob) or {}
    check(str(w.get("username") or "").lstrip("@") == "raeone", "two [Yes] taps on one code: only the first counts",
          f"waiting {w!r}; second tap: {rep.why()}")
    rep = say(t3, f"/link {display}")
    check(not rep.asked, "a code already waiting can't be used again from another Telegram", rep.why())
    r = rob.c.post("/auth/me/telegram/confirm", json={"accept": True})
    check(r.status_code == 200 and jd(r).get("connected") is True, "(Rob confirms Rae's Telegram)", brief(r))
    for who in (t2, t3):
        rep = say(who, f"/link {display}")
        check(not rep.asked and "Rob" not in rep.text, f"the used code from {who.first}: no prompt, nobody named", rep.why())
    check(len(link_rows(user_id=rob.id)) == 1 and not link_rows(external_id=t2.id), "Rob has one link, to Rae only")

    print("  an old code after a new one is made")
    pia = Person("Pia Sim", "pia")
    t = TgUser("Olly", "Old", "ollyold")
    old, _ = make_code(pia, "[old code]")
    new, _ = make_code(pia, "[old code]")
    rep = say(t, f"/link {old}")
    check(not rep.asked, "the old code: no prompt", rep.why())
    rep = say(t, f"/link {new}")
    check(rep.prompt is not None, "the new one works", rep.why())
    if rep.prompt:
        newer, _ = make_code(pia, "[old code]")
        rep = tap(t, rep.prompt, "taps")
        check(waiting_of(pia) is None, "a prompt left open while a newer code is made: [Yes] on it does nothing", rep.why())
        rep = say(t, f"/link {newer}")
        hit = rep.button(r"cancel")
        if hit:
            tap(t, hit)

    print("  5 wrong codes in a row")
    wes = Person("Wes Tay", "wes")
    tw = TgUser("Will", "Wrong", "willwrong")
    for i in range(5):
        say(tw, f"/link {random_code()}")
    display, body = make_code(wes, "[5 wrong]")
    for words in (f"/link {display}", f"/start {start_payload(body, display)}", display):
        rep = say(tw, words)
        check(not rep.asked, f"after 5 wrong codes, even the right one ({words.split()[0] if words.startswith('/') else 'bare'}) is refused",
              rep.why())
    check(waiting_of(wes) is None, "and nothing is waiting")
    other = TgUser("Wendy", "Right", "wendyright")
    rep = say(other, f"/link {display}")
    check(rep.prompt is not None, "the block is per Telegram user: someone else's same code still works", rep.why())
    hit = rep.button(r"cancel")
    if hit:
        tap(other, hit)
    t4 = TgUser("Fay", "Four", "fayfour")
    for _ in range(4):
        say(t4, f"/link {random_code()}")
    display, _ = make_code(wes, "[5 wrong]")
    rep = say(t4, f"/link {display}")
    check(rep.prompt is not None, "4 wrong then the right code: still allowed (the block starts at 5)", rep.why())
    hit = rep.button(r"cancel")
    if hit:
        tap(t4, hit)

    print("  a forwarded message holding a code")
    fen = Person("Fen Yap", "fen")
    tf = TgUser("Fred", "Forward", "fredfwd")
    display, _ = make_code(fen, "[forwarded]")
    for words in (display, f"/link {display}"):
        rep = say(tf, words, forwarded=True)
        check(not rep.asked, f"forwarded {'bare code' if words == display else '/link CODE'}: ignored, no prompt", rep.why())
    check(waiting_of(fen) is None, "nothing waiting")
    rep = say(tf, display)
    check(rep.prompt is not None, "the same code typed (not forwarded) from that unlinked Telegram: the prompt", rep.why())
    hit = rep.button(r"cancel")
    if hit:
        tap(tf, hit)

    print("  a code sent from an already-linked Telegram")
    need(link_rows(user_id=rob.id), "(Rob is linked to Rae's Telegram, from above)")
    bea = W.bea = Person("Bea Ho", "bea")
    display, body = make_code(bea, "[already linked]")
    for words in (f"/link {display}", f"/start {start_payload(body, display)}", display):
        rep = say(t1, words)
        check(not rep.asked, f"from Rae (linked to Rob): {words.split()[0] if words.startswith('/') else 'bare code'} -> no prompt",
              rep.why())
    check(waiting_of(bea) is None, "Bea's code isn't waiting for anyone")
    check(len(link_rows(external_id=t1.id)) == 1 and link_rows(external_id=t1.id)[0].get("user_id") == rob.id,
          "Rae's Telegram is still linked to Rob only")
    fresh = TgUser("Bo", "Fresh", "bofresh")
    rep = say(fresh, f"/link {display}")
    check(rep.prompt is not None, "Bea's code is untouched: an unlinked Telegram still gets the prompt", rep.why())
    hit = rep.button(r"cancel")
    if hit:
        tap(fresh, hit)

    print("  plates and words are not codes")
    pete = Person("Pete Lim", "pete")
    tp = TgUser("Pip", "Plate", "pipplate")
    words_said = []
    for _ in range(3):
        for words in ("SJB8762M", "STANDARD"):
            rep = say(tp, words)
            words_said.append(rep)
    check(not any(r.asked for r in words_said), "an unlinked Telegram typing SJB8762M / STANDARD gets no link prompt",
          next((r.why() for r in words_said if r.asked), ""))
    display, _ = make_code(pete, "[plates]")
    rep = say(tp, f"/link {display}")
    check(rep.prompt is not None,
          "6 of them weren't counted as wrong codes: that Telegram isn't blocked from a real code (M6)", rep.why())
    hit = rep.button(r"cancel")
    if hit:
        tap(tp, hit)

    print("  web 'Not me' revokes")
    nora = Person("Nora Teo", "nora")
    tn, tn2 = TgUser("Nat", "Phisher", "natphish"), TgUser("Ned", "Other", "nedother")
    display, body = make_code(nora, "[not me]")
    telegram_says_yes(nora, tn, display, body, "[not me]")
    r = nora.c.post("/auth/me/telegram/confirm", json={"accept": False})
    check(r.status_code == 200 and jd(r).get("connected") is False and jd(r).get("code") is None,
          "confirm {accept: false} ('Not me') -> 200, not connected, no code", brief(r))
    check(not link_rows(user_id=nora.id) and not link_rows(external_id=tn.id), "no link was made")
    check(not still_open(code_rows(nora.id)), "the code is revoked in the database", str(code_rows(nora.id))[:300])
    for who in (tn, tn2):
        rep = say(who, f"/link {display}")
        check(not rep.asked, f"the revoked code from {who.first}: no prompt", rep.why())
    r = nora.c.post("/auth/me/telegram/confirm", json={"accept": True})
    check(r.status_code == 409, "confirm afterwards -> 409 (nothing waiting)", brief(r))
    rep = whoami(tn)
    check(not_connected(rep), "Nat's Telegram: /whoami 'Not connected'", rep.why())


def who_it_talks_to() -> None:
    need(W.app is not None, "the bot app is built")
    rob_t = None
    links = sql("SELECT external_id FROM chat_links") if table_exists("chat_links") else []
    if links:
        rob_t = TgUser("Linked", "Member", "linkedmember", uid=int(links[0]["external_id"]))

    print("  group chats")
    bea = Person("Gail Png", "gail")
    display, _ = make_code(bea, "[group]")
    before_reports, before_tmp = report_ids(), tmp_files()
    g1 = group_chat(-100777001, "supergroup")
    sender = rob_t or TgUser("Gina", "Group", "ginagroup")
    replies = [photo(sender, chat=g1), say(sender, "rear-ended at PIE", chat=g1),
               say(sender, f"/link {display}", chat=g1), say(sender, "/start", chat=g1), photo(sender, chat=g1)]
    texts = [t for r in replies for t in r.texts]
    check(len(texts) <= 1 and all("private chat" in t.lower() for t in texts),
          "five messages in a group (even from a linked person): at most one reply, 'I only work in a private chat'",
          " | ".join(texts)[:400])
    if not texts:
        info("the group got no reply at all (the brief allows 'at most one'; the owner may expect one)")
    check(sum(r.downloads for r in replies) == 0, "no photo from the group was downloaded")
    check(not sessions_for(g1["id"]), "no session for the group", str(sessions_for(g1["id"])))
    check(report_ids() == before_reports and tmp_files() == before_tmp, "no case, no temp file")
    check(waiting_of(bea) is None and len(still_open(code_rows(bea.id))) == 1, "a /link CODE in a group isn't used: still open")
    g2 = group_chat(-4555002, "group")
    replies = [say(TgUser("Gus", "Grouper"), "hello", chat=g2), say(TgUser("Gil", "Grouper"), "anyone?", chat=g2)]
    texts = [t for r in replies for t in r.texts]
    check(len(texts) <= 1 and all("private chat" in t.lower() for t in texts),
          "another group, two people: at most one reply there too", " | ".join(texts)[:300])
    bea.c.delete("/auth/me/telegram/code")

    print("  an unconnected person")
    t = TgUser("Uma", "Unlinked", "umaunlinked")
    mark_calls, before_reports, before_tmp = len(CALLS), report_ids(), tmp_files()
    replies = [photo(t), say(t, "Rear-ended at PIE, plate SBA1234A"), photo(t, caption="more"),
               document(t, "image/png", "scan.png")]
    texts = [x for r in replies for x in r.texts]
    check(len(texts) == 1 and "connect" in texts[0].lower(),
          "photo, text, photo, image file: one polite 'connect first' (once an hour per chat)", " | ".join(texts)[:400] or "nothing")
    urls = [u for r in replies for u in r.urls()]
    check(any(u.startswith(PUBLIC_BASE_URL) for u in urls), f"it links to Carlink Settings at {PUBLIC_BASE_URL}", str(urls))
    check(sum(r.downloads for r in replies) == 0, "nothing was downloaded", next((r.why() for r in replies if r.downloads), ""))
    check(not sessions_for(t.id), "no session was kept", str(sessions_for(t.id)))
    check(report_ids() == before_reports, "no case")
    check(tmp_files() == before_tmp, "no temp file")
    check(not calls_since(mark_calls), "no AI ran", describe(calls_since(mark_calls)))
    rep = whoami(t)
    check(not_connected(rep), "/whoami works without a link: 'Not connected'", rep.why())
    rep = say(t, "/help")
    check(bool(rep.texts), "/help works without a link", rep.why())

    print("  the P0 allow-list is gone")
    past = TgUser("Paul", "Past", "paulpast")
    db = SessionLocal()
    try:
        db.add(Report(channel="telegram", reporter_chat_id=str(past.id), data={}, status="confirmed"))
        db.commit()
    finally:
        db.close()
    if getattr(W.app, "post_init", None):
        try:
            LOOP.run_until_complete(W.app.post_init(W.app))  # as on a restart, with the past case in the database
        except Exception as exc:
            info(f"post_init raised on the second run: {type(exc).__name__}: {exc}")
    for who, why in ((past, "a past Telegram reporter who isn't linked"),
                     (TgUser("Al", "Listed", "allisted", uid=OLD_ALLOWED_ID), "an id in the old TELEGRAM_ALLOWED_IDS")):
        before_reports = report_ids()
        rep = photo(who)
        check(rep.downloads == 0 and "connect" in rep.text.lower(), f"{why}: 'connect first', no download", rep.why())
        check(not sessions_for(who.id) and report_ids() == before_reports, "  no session, no case")


def filing() -> None:
    need(W.app is not None, "the bot app is built")
    fio = W.fiona = Person("Fiona Ng", "fiona")
    fio.save_key()
    gus = W.gus = Person("Gus Tan", "gus", ai_company_key_allowed=True)
    hana = W.hana = Person("Hana Lee", "hana")
    tf, tg_, th = TgUser("Fi", "Phone", "fiphone"), TgUser("Gus", "Phone", "gusphone"), TgUser("Hana", "Phone", "hanaphone")
    for p, t in ((fio, tf), (gus, tg_), (hana, th)):
        link_up(p, t)

    def file_case(p: Person, t: TgUser, allowed: set, who_pays: str, typed=()) -> None:
        mark_calls, before = len(CALLS), report_ids()
        replies = [photo(t)]
        check(replies[0].downloads >= 1, f"{p.label}'s first photo is downloaded", replies[0].why())
        replies.append(say(t, "Rear-ended at PIE exit 12, rear bumper dented."))
        texts = [x for r in replies for x in r.texts]
        check(any(re.match(r"\W*Filing as:?\s+" + re.escape(p.name), x) for x in texts),
              f"the draft summary starts 'Filing as {p.name}'", " | ".join(x[:80] for x in texts[-3:]))
        for words in typed:
            mark = len(CALLS)
            rep = say(t, words)
            replies.append(rep)
            check(not rep.code_talk, f"'{words}' typed during the draft isn't taken as a link code", rep.why())
            check(any(c["purpose"] == "draft" for c in calls_since(mark)), f"  '{words}' went into the draft (it was redrafted)",
                  describe(calls_since(mark)))
        hit = confirm_button(replies)
        need(hit is not None, "the draft has a Confirm button", replies[-1].why())
        done = tap(t, hit, "taps")
        new = new_reports(before)
        need(len(new) == 1, "Confirm files exactly one case", f"{len(new)} new case(s); {done.why()}")
        case = new[0]
        check(case["owner_id"] == p.id, f"the case owner is {p.label}", f"owner_id {case['owner_id']!r}")
        check(case["channel"] == "telegram" and case["reporter_chat_id"] == str(t.id),
              "channel 'telegram', reporter_chat_id = the Telegram user id", f"{case['channel']!r}, {case['reporter_chat_id']!r}")
        created = [e for e in sql("SELECT user_id, user_name, action, detail FROM audit_log WHERE report_id = :r", r=case["id"])
                   if e["action"] == "created"]
        check(len(created) == 1 and created[0]["user_id"] == p.id and HISTORY_DETAIL in (created[0]["detail"] or ""),
              f"the history has 'created - {HISTORY_DETAIL}' recorded by {p.label}", str(created))
        r = p.c.get(f"/reports/{case['id']}")
        hist = jd(r).get("history") or []
        first = next((h for h in hist if h.get("action") == "created"), {})
        check(r.status_code == 200 and p.name in str(first.get("user_name") or "") and HISTORY_DETAIL in str(first.get("detail") or ""),
              f"{p.label} opens it: the history reads '{HISTORY_DETAIL}' by {p.name}", f"{r.status_code} {first!r}")
        url = f"{PUBLIC_BASE_URL}/reports/{case['id']}"
        check(any(u == url or u.rstrip(".,)") == url for u in done.urls()),
              f"the reply holds the case link {PUBLIC_BASE_URL}/reports/<id>", f"links {done.urls()}; {done.why()}")
        check(OLD_HOST not in done.source, "and not the old hard-coded address", done.source[:200])
        check(not done.documents and not any(e.kind in ("document", "media") for e in FAKE.log if e.chat_id == t.id),
              "no PDF (or any file) is sent to Telegram (D8)", done.why())
        calls = calls_since(mark_calls)
        check(all_on(calls, allowed), f"every Gemini call ran on {who_pays}", describe(calls))
        rep = say(t, "hello again")
        check(not rep.asked and confirm_button([rep]) is None, "the session was reset after filing (no Confirm on the old draft)",
              rep.why())

    print(f"  {fio.label}: her own key, no company tick")
    file_case(fio, tf, {fio.key}, "Fiona's own key", typed=("SJB8762M", "STANDARD"))
    print(f"  {gus.label}: no own key, ticked for the company key")
    file_case(gus, tg_, {ENV_COMPANY_KEY}, "the company key")
    print(f"  {hana.label}: no own key, no tick -> AI off")
    mark_calls, before, before_tmp = len(CALLS), report_ids(), tmp_files()
    rep = photo(th)
    check(AI_OFF in rep.text and "My AI key" in rep.text,
          "a photo gets 'AI is off for your account - add your key in Carlink -> Settings -> My AI key'", rep.why())
    check(rep.downloads == 0, "and isn't downloaded", rep.why())
    rep2 = say(th, "Rear-ended at PIE exit 12.")
    check(not session_photos(th) and tmp_files() == before_tmp, "nothing is kept for the draft")
    check(not calls_since(mark_calls), "no AI ran", describe(calls_since(mark_calls)))
    check(report_ids() == before, "no case", rep2.why())


def links_ending() -> None:
    need(W.app is not None, "the bot app is built")
    ada = W.ada

    old_pw, new_pw = "meadow copper lantern forty two", "entirely different harbour ninety nine"

    def three(tag: str, password: bool = False):
        pw = old_pw if password else None
        lin = Person(f"Lin {tag}", f"lin{tag.lower()}", password=pw)
        ola = Person(f"Ola {tag}", f"ola{tag.lower()}", password=pw)
        wen = Person(f"Wen {tag}", f"wen{tag.lower()}", password=pw)
        tl, tw = TgUser("Lin", tag, f"lin{tag.lower()}tg"), TgUser("Wen", tag, f"wen{tag.lower()}tg")
        link_up(lin, tl, f"[{tag}]")
        ola_code, _ = make_code(ola, f"[{tag}]")
        display, body = make_code(wen, f"[{tag}]")
        telegram_says_yes(wen, tw, display, body, f"[{tag}]")
        return lin, ola, wen, tl, tw, ola_code

    def ended(how: str, lin, ola, wen, tl, tw, ola_code, *, wen_client=None, after=""):
        sfx = f" {after}" if after else ""
        row = users_row(ada, lin) or {}
        check(row.get("telegram", "missing") is None and not link_rows(user_id=lin.id),
              f"{how}{sfx}: Lin's Telegram link is gone (GET /users telegram null, no chat_links row)",
              f"telegram {row.get('telegram', 'missing')!r}, rows {link_rows(user_id=lin.id)}")
        rep = whoami(tl)
        check(not_connected(rep), f"{how}{sfx}: Lin's Telegram /whoami -> 'Not connected'", rep.why())
        rep = photo(tl)
        check(rep.downloads == 0, f"{how}{sfx}: a photo from Lin's Telegram isn't downloaded", rep.why())
        check(not still_open(code_rows(ola.id)), f"{how}{sfx}: Ola's open code ended", str(code_rows(ola.id))[:200])
        rep = say(TgUser("Stranger", how[:6]), f"/link {ola_code}")
        check(not rep.asked, f"{how}{sfx}: Ola's old code gets no prompt in Telegram", rep.why())
        check(not still_open(code_rows(wen.id)) and not link_rows(user_id=wen.id),
              f"{how}{sfx}: Wen's waiting code ended (no link)", str(code_rows(wen.id))[:200])
        if wen_client is not None:
            r = wen_client.post("/auth/me/telegram/confirm", json={"accept": True})
            check(r.status_code == 409, f"{how}{sfx}: Wen confirming in Carlink -> 409", brief(r))
        rep = whoami(tw)
        check(not_connected(rep), f"{how}{sfx}: Wen's Telegram is not connected", rep.why())

    print("  disable, then enable again")
    lin, ola, wen, tl, tw, oc = three("Dis")
    for p in (lin, ola, wen):
        r = ada.c.patch(f"/users/{p.id}", json={"active": False})
        need(r.status_code == 200, f"the admin disables {p.label} {p.name.split()[-1]}", brief(r))
    ended("disabled", lin, ola, wen, tl, tw, oc)
    for p in (lin, ola, wen):
        r = ada.c.patch(f"/users/{p.id}", json={"active": True})
        need(r.status_code == 200, f"the admin enables {p.label} {p.name.split()[-1]} again", brief(r))
    wen.sign_in()
    ended("disabled", lin, ola, wen, tl, tw, oc, wen_client=wen.c, after="then enabled again (no relink)")

    print("  admin password reset")
    lin, ola, wen, tl, tw, oc = three("Rst")
    for p in (lin, ola, wen):
        r = ada.c.post(f"/users/{p.id}/reset-password")
        need(r.status_code == 200, f"the admin resets {p.label}'s password", brief(r))
    ended("admin password reset", lin, ola, wen, tl, tw, oc)

    print("  a reset link is used")
    lin, ola, wen, tl, tw, oc = three("Lnk")
    wen_client = None
    for p in (lin, ola, wen):
        r = ada.c.post(f"/users/{p.id}/reset-link")
        need(r.status_code == 200 and jd(r).get("token"), f"the admin makes {p.label} a reset link", brief(r))
        c = anon()
        r = c.post("/auth/links/accept", json={"token": jd(r)["token"], "password": "brand new copper meadow lantern 77"})
        need(r.status_code == 200, f"{p.label} uses the reset link", brief(r))
        if p is wen:
            wen_client = c
    ended("reset link used", lin, ola, wen, tl, tw, oc, wen_client=wen_client)

    print("  changing your own password")
    lin, ola, wen, tl, tw, oc = three("Pwd", password=True)
    for p in (lin, ola, wen):
        r = p.c.post("/auth/password", json={"current_password": old_pw, "new_password": new_pw})
        need(r.status_code == 200, f"{p.label} changes their own password", brief(r))
    ended("own password changed", lin, ola, wen, tl, tw, oc, wen_client=wen.c)

    print("  disconnect: from Settings, by an admin, and /unlink in Telegram")
    ivy, jay = Person("Ivy Chua", "ivy"), Person("Jay Koh", "jay")
    ti, tj = TgUser("Ivy", "Phone", "ivyphone"), TgUser("Jay", "Phone", "jayphone")
    link_up(ivy, ti, "[disconnect]")
    link_up(jay, tj, "[disconnect]")
    mark = audit_mark()
    r = ivy.c.delete("/auth/me/telegram")
    check(r.status_code == 200 and jd(r).get("ok") is True, "DELETE /auth/me/telegram -> {ok: true}", brief(r))
    check(ivy.status().get("connected") is False and not link_rows(user_id=ivy.id), "Ivy is disconnected")
    check(mentions_telegram(audit_since(mark)), "it's in the history", str(audit_since(mark))[:300])
    check(not_connected(whoami(ti)), "Ivy's Telegram: 'Not connected'")
    mark = audit_mark()
    r = ivy.c.delete(f"/users/{jay.id}/telegram")
    check(r.status_code == 403, "a user can't disconnect someone else (DELETE /users/{id}/telegram -> 403)", brief(r))
    r = ada.c.delete(f"/users/{jay.id}/telegram")
    check(r.status_code == 200 and jd(r).get("ok") is True, "the admin: DELETE /users/{id}/telegram -> {ok: true}", brief(r))
    check(not link_rows(user_id=jay.id) and (users_row(ada, jay) or {}).get("telegram", "missing") is None, "Jay is disconnected")
    check(mentions_telegram(audit_since(mark)), "it's in the history", str(audit_since(mark))[:300])
    check(not_connected(whoami(tj)), "Jay's Telegram: 'Not connected'")
    ann, ta = getattr(W, "ann", None), getattr(W, "t_ann", None)
    if ann is not None and link_rows(user_id=ann.id):
        rep = say(ta, "/unlink")
        if "disconnected" not in rep.text.lower():
            hit = rep.button(r"yes|disconnect|unlink")
            if hit:
                rep = tap(ta, hit, "taps")
        check("disconnected" in rep.text.lower(), "/unlink in Telegram -> 'Disconnected'", rep.why())
        check(not link_rows(user_id=ann.id) and ann.status().get("connected") is False, "and Ann's account is no longer connected")
        check(not_connected(whoami(ta)), "/whoami -> 'Not connected'")
    else:
        check(False, "/unlink in Telegram -> 'Disconnected'", "Ann wasn't linked ([bot: the two-sided link] failed)")

    print("  the link ends while a draft is open")
    mia = Person("Mia Ong", "mia", ai_company_key_allowed=True)
    tm = TgUser("Mia", "Phone", "miaphone")
    link_up(mia, tm, "[mid-draft]")
    replies = [photo(tm), say(tm, "Scraped at the Jurong carpark.")]
    hit = confirm_button(replies)
    need(hit is not None, "(Mia's draft has a Confirm button)", replies[-1].why())
    r = ada.c.patch(f"/users/{mia.id}", json={"active": False})
    need(r.status_code == 200, "the admin disables Mia mid-draft", brief(r))
    before = report_ids()
    rep = tap(tm, hit, "taps")
    check(not new_reports(before), "Confirm after the link ended files nothing", rep.why())

    print("  the Telegram is relinked to someone else mid-draft")
    kai, kim = Person("Kai Lim", "kai", ai_company_key_allowed=True), Person("Kim Seah", "kim", ai_company_key_allowed=True)
    tk = TgUser("Kit", "Shared", "kitshared")
    link_up(kai, tk, "[relink]")
    dl_mark = len(FAKE.log)
    replies = [photo(tk), say(tk, "Dented door at the office carpark.")]
    temp = [e.path for e in FAKE.log[dl_mark:] if e.kind == "download" and e.path]
    hit = confirm_button(replies)
    need(hit is not None, "(Kai's draft has a Confirm button)", replies[-1].why())
    rep = say(tk, "/unlink")
    if "disconnected" not in rep.text.lower():
        h2 = rep.button(r"yes|disconnect|unlink")
        if h2:
            tap(tk, h2, "taps")
    need(not link_rows(user_id=kai.id), "(Kai's Telegram is unlinked with /unlink)")
    link_up(kim, tk, "[relink]")
    before = report_ids()
    rep = tap(tk, hit, "taps")
    check(not new_reports(before), "Confirm on Kai's old draft, now linked to Kim, files nothing", rep.why())
    check(temp and not any(Path(p).exists() for p in temp), "Kai's draft and its temp files are discarded",
          f"{sum(Path(p).exists() for p in temp)} of {len(temp)} temp file(s) still there")


def pairing() -> None:
    need(W.app is not None, "the bot app is built")
    pam = Person("Pam Yeo", "pam")
    t1, t2 = TgUser("Pam", "Old Phone", "pamold"), TgUser("Pam", "New Phone", "pamnew")
    link_up(pam, t1, "[pairing]")

    print("  one account per Telegram")
    sam = Person("Sam Low", "sam")
    display, body = make_code(sam, "[pairing]")
    rep = say(t1, f"/start {start_payload(body, display)}")
    check(not rep.asked, "Pam's linked Telegram can't take Sam's code", rep.why())
    check(waiting_of(sam) is None and len(link_rows(external_id=t1.id)) == 1, "Sam has nothing waiting; the Telegram has one link")
    sam.c.delete("/auth/me/telegram/code")

    print("  one Telegram per account")
    r = pam.c.post("/auth/me/telegram/code")
    if r.status_code != 200:
        info(f"a connected account asking for a new code got {r.status_code} (refused while connected)")
    else:
        display = str(((jd(r).get("code") or {}).get("display")) or "")
        CODES.add(display)
        rep = say(t2, f"/start {start_payload(jd(r), display)}")
        if rep.prompt is not None:
            tap(t2, rep.prompt, "taps")
            r = pam.c.post("/auth/me/telegram/confirm", json={"accept": True})
            info(f"connecting a second Telegram to a connected account: confirm -> {r.status_code}"
                 f"{' (it replaced the old one)' if r.status_code == 200 else ''}")
        else:
            info("connecting a second Telegram to a connected account: the bot gave no prompt")
    rows = link_rows(user_id=pam.id)
    w1, w2 = whoami(t1), whoami(t2)
    as_pam = [t.first + " " + t.last for t, w in ((t1, w1), (t2, w2)) if connected_as(w, pam)]
    check(len(rows) == 1 and len(as_pam) == 1, "Pam ends up with exactly one Telegram (database and bot agree)",
          f"{len(rows)} chat_links row(s); connected as Pam: {as_pam}; {w1.why()} || {w2.why()}")
    if rows and as_pam:
        which = t1 if str(rows[0]["external_id"]) == str(t1.id) else t2
        check(which.first + " " + which.last == as_pam[0], "the bot's answer matches the stored link", f"{rows[0]} vs {as_pam}")

    print("  the database refuses a second row either way")
    lr = link_rows(user_id=pam.id)
    need(lr, "(Pam has a chat_links row)")
    cols = sql("PRAGMA table_info(chat_links)")
    other = Person("Oz Extra", "oz")

    def try_insert(external_id: str, user_id: str) -> str:
        values = {"channel": "telegram", "external_id": external_id, "user_id": user_id,
                  "linked_at": _db_time_like(lr[0].get("linked_at"), utcnow())}
        for c in cols:
            if c["name"] in values:
                continue
            if c["pk"] and "INT" in (c["type"] or "").upper():
                continue  # INTEGER PRIMARY KEY fills itself
            if c["pk"] or (c["notnull"] and c["dflt_value"] is None):
                values[c["name"]] = os.urandom(6).hex() if "INT" not in (c["type"] or "").upper() else random.randint(10 ** 8, 10 ** 9)
        names = ", ".join(values)
        marks = ", ".join(f":{k}" for k in values)
        try:
            with engine.begin() as conn:
                conn.execute(text(f"INSERT INTO chat_links ({names}) VALUES ({marks})"), values)
        except Exception as exc:
            return type(exc).__name__ + ": " + str(exc).split("\n")[0]
        sql_write("DELETE FROM chat_links WHERE external_id = :e AND user_id = :u", e=external_id, u=user_id)
        return "inserted"
    got = try_insert(str(lr[0]["external_id"]), other.id)
    check("UNIQUE" in got.upper(), "a second account on the same Telegram: UNIQUE refuses it", got)
    got = try_insert("999000111", pam.id)
    check("UNIQUE" in got.upper(), "a second Telegram on the same account: UNIQUE refuses it", got)


def earlier_reports() -> None:
    erin = Person("Erin Kwok", "erin")
    t = TgUser("Erin", "Phone", "erinphone")
    db = SessionLocal()
    try:
        old = Report(channel="telegram", reporter_chat_id=str(t.id), data={"description": "old"}, status="confirmed")
        db.add(old)
        db.commit()
        old_id = old.id
    finally:
        db.close()
    r = W.ada.c.get(f"/reports/{old_id}")
    need(r.status_code == 200, "(the admin opens the earlier Telegram case)", brief(r))
    sug = jd(r).get("suggested_owner")
    check(not (isinstance(sug, dict) and sug.get("reason") == HINT_REASON), "before T is linked: no Telegram hint", repr(sug))
    need(W.app is not None, "the bot app is built")
    link_up(erin, t, "[earlier]")
    rep_all = [e for e in FAKE.log if e.chat_id == t.id]
    check(not any(re.search(r"earlier|add (them|my)", b.text, re.I) for e in rep_all for b in e.buttons)
          and not any("earlier report" in plain(e.text).lower() for e in rep_all),
          "the bot doesn't offer to claim earlier reports (D5)", "; ".join(plain(e.text)[:80] for e in rep_all[-3:]))
    rows = sql("SELECT owner_id FROM reports WHERE id = :i", i=old_id)
    check(rows and rows[0]["owner_id"] is None, "linking leaves the earlier case Unassigned", str(rows))
    r = erin.c.get(f"/reports/{old_id}")
    check(r.status_code in (403, 404), "Erin can't open it (it's not hers until an admin assigns it)", brief(r))
    listed = erin.c.get("/reports")
    check(old_id not in listed.text, "it isn't in Erin's case list")
    r = W.ada.c.get(f"/reports/{old_id}")
    body = jd(r)
    sug = body.get("suggested_owner")
    check(body.get("owner") is None, "the admin sees it Unassigned", repr(body.get("owner")))
    check(isinstance(sug, dict) and sug.get("id") == erin.id and sug.get("reason") == HINT_REASON,
          f"the admin's GET /reports/{{id}}: suggested_owner = Erin, reason '{HINT_REASON}'", repr(sug))
    r = erin.c.delete("/auth/me/telegram")
    check(r.status_code == 200, "(Erin disconnects)", brief(r))
    sug = jd(W.ada.c.get(f"/reports/{old_id}")).get("suggested_owner")
    check(not (isinstance(sug, dict) and sug.get("reason") == HINT_REASON), "once disconnected, the hint goes", repr(sug))


def wrong_code_alarm() -> None:
    need(W.app is not None, "the bot app is built")
    noise = ("telegram", "httpx", "httpcore", "asyncio", "py.warnings", "PIL", "urllib3")

    def warnings_since(mark: int) -> list[str]:
        return [f"{name}: {msg}" for level, name, msg in LOG_RECORDS[mark:]
                if level >= logging.WARNING and not name.startswith(noise)]
    mark = len(LOG_RECORDS)
    for i in range(15):
        t = TgUser("Guess", f"Er{i}")
        for _ in range(4):  # 4 each: nobody is blocked
            say(t, f"/link {random_code()}")
    found = warnings_since(mark)
    check(len(found) == 1, "more than 50 wrong codes in an hour across everyone (60 here): exactly one WARNING line",
          f"{len(found)} warning(s): " + " | ".join(found[:4]))
    mark = len(LOG_RECORDS)
    for i in range(5):
        t = TgUser("Guess", f"More{i}")
        for _ in range(4):
            say(t, f"/link {random_code()}")
    found = warnings_since(mark)
    check(not found, "20 more in the same hour: no second warning", " | ".join(found[:4]))


def leaks() -> None:
    secret = BOT_TOKEN.split(":", 1)[1]
    lines = [f"{name}: {msg}" for level, name, msg in LOG_RECORDS if level >= logging.INFO and secret in msg]
    check(not lines, f"the bot token is in none of the {len(LOG_RECORDS)} log records (INFO and up)",
          mask(lines[0])[:300] if lines else "")
    in_logs = sorted({c for c in CODES for _lvl, _n, msg in LOG_RECORDS if c in msg or flat(c) in msg})
    check(not in_logs, "no plain link code in any log line", f"{len(in_logs)} code(s) logged")
    check(not any(secret in b for b in RESPONSES), "the bot token is in no API response")
    hosts = Counter(NET_REFUSED)
    others = {h: n for h, n in hosts.items() if "telegram.org" not in h}
    check(not others, "nothing tried to reach any outside host but Telegram's", str(others))
    if hosts and not others:
        info(f"the api tried api.telegram.org {sum(hosts.values())} time(s) (the optional 'Connected'/'Cancelled' notice); refused")
    check(not STATUS_500, "no API response was a 500", "; ".join(STATUS_500[:4]))
    check(not BOT_ERRORS, "the bot never raised while handling an update", "; ".join(BOT_ERRORS[:3]) +
          (f" (+{len(BOT_ERRORS) - 3} more)" if len(BOT_ERRORS) > 3 else ""))


def main() -> int:
    for name, fn in (
        ("setup: the api, the bot, a recording fake Telegram", setup),
        ("config: PUBLIC_BASE_URL, no allow-list", config),
        ("data: chat_links, chat_link_codes", data),
        ("api: /auth/me/telegram, codes, confirm, admin views", api),
        ("bot: the two-sided link", two_sided_link),
        ("bot: refusals", refusals),
        ("bot: who it talks to", who_it_talks_to),
        ("filing: as the linked person", filing),
        ("links ending", links_ending),
        ("pairing: one Telegram per account, one account per Telegram", pairing),
        ("earlier reports: stay Unassigned, admin hint", earlier_reports),
        ("bot: the wrong-code alarm", wrong_code_alarm),
        ("leaks and errors", leaks),
    ):
        run_section(name, fn)
    for p in getattr(W, "patches", []):
        p.stop()
    try:
        W.boot.__exit__(None, None, None)
    except Exception:
        pass
    print(f"\ntest_telegram_link_e2e: {PASSED[0]} checks passed, {len(FAILURES)} failed")
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
    print("\nALL TELEGRAM LINK E2E CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
