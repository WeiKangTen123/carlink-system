"""Tests for release P5 "Telegram connect", the API side: the link tables,
app/auth/chat_links.py (what the bot builds on), the six endpoints in
app/api/telegram_link.py, `telegram` on GET /users and GET /system/info,
the places links end (disable, password resets, own password change), the
code sweep, and the "sent from their Telegram" owner hint.

    python scripts/test_telegram_link.py

From plans/2026-10-26-roles-ai-telegram/p5_build_brief.md ("Owner
decisions in force", "Data", "Shared module", "API"), agent3_telegram_report
.md and agent6_security_report.md H2, M6, D5, D8.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database, every outside connection refused, Chromium replaced, and the
Telegram Bot API played by a small HTTP server on 127.0.0.1 that records
what it is sent. The bot token is made up; no code or token may appear in
a log line, and no plain code in the database.
"""
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time
import traceback
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
_tmp = Path(tempfile.mkdtemp(prefix="telegram-link-test-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
DB_FILE = (_tmp / "test.db").resolve()
BOT_TOKEN = "7000000001:AAFakeTokenForTheTelegramLinkTestsOnly"
# Forced, not setdefault: never a real folder, database or token.
os.environ["STORAGE_DIR"] = str(STORAGE)
os.environ["SECRETS_DIR"] = str(_tmp / "secrets")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_FILE.as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = BOT_TOKEN
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ["PUBLIC_BASE_URL"] = "https://carlink.example.test"

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


# ------------------------------------------- the Telegram Bot API, played locally
class FakeTelegram:
    def __init__(self):
        self.sent: list[dict] = []
        self.status = 200
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length)
                with outer._lock:
                    outer.sent.append({"path": self.path, "body": json.loads(body or b"{}")})
                    status = outer.status
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": status == 200}).encode())

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def take(self) -> list[dict]:
        with self._lock:
            out, self.sent = self.sent, []
        return out


TELEGRAM = FakeTelegram()

# Chromium: GET /reports/{id} may try to render a PDF; here it fails at
# once (the case still opens).
import app.rendering.renderer as _renderer  # noqa: E402


def _no_chromium():
    raise RuntimeError("no Chromium in this test")


_renderer.sync_playwright = _no_chromium

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

import app.api.auth as auth_api  # noqa: E402
import app.api.telegram_link as telegram_api  # noqa: E402
from app.api.main import app  # noqa: E402
from app.auth import chat_links  # noqa: E402
from app.auth.core import CurrentUser, SESSION_COOKIE, utc_now  # noqa: E402
from app.auth.deps import ApiError  # noqa: E402
from app.intake import cleanup  # noqa: E402
from app.reports import owners  # noqa: E402
from app.reports.db import SessionLocal, engine, init_db  # noqa: E402
from app.reports.models import AppSetting, AuditEntry, ChatLink, ChatLinkCode, Report, User, gen_id  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

telegram_api.TELEGRAM_API = TELEGRAM.url

# ---------------------------------------------------------------- harness
BASE = "https://carlink.test"
PASSWORD = "purple tiger lamp bicycle"
STATUS_KEYS = {"connected", "telegram_username", "telegram_name", "linked_at", "code", "bot_username"}
CODE_KEYS = {"display", "expires_at", "deep_link", "waiting"}
# Every plain code a response handed out; none may be in a log line or the
# database, and each in exactly one response.
CODES: set[str] = set()
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
    """Keeps every response body, to count where codes show up."""

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
    return f"{r.status_code} {(r.text or '').replace(chr(10), ' ')[:260]}"


_ips = iter(range(1, 1_000_000))


def fresh_ip() -> dict:
    n = next(_ips)
    return {"X-Real-IP": f"10.77.{(n // 256) % 256}.{n % 256}"}


class Person:
    """A self-signup with a real password (so it can change it)."""

    def __init__(self, username: str):
        self.c = browser()
        self.password = PASSWORD
        r = self.c.post("/auth/signup", json={"username": username, "email": f"{username}@example.com",
                                               "password": PASSWORD}, headers=fresh_ip())
        need(r.status_code == 200, f"{username} signs up", brief(r))
        me = jd(r)
        self.id, self.username, self.email, self.name = me["id"], me["username"], me["email"], me["name"]
        self.label = username

    def status(self) -> dict:
        r = self.c.get("/auth/me/telegram")
        return jd(r) if r.status_code == 200 else {"error": brief(r)}

    def new_code(self):
        r = self.c.post("/auth/me/telegram/code")
        display = ((jd(r).get("code") or {}).get("display")) if r.status_code == 200 else None
        if display:
            CODES.add(chat_links.normalize_code(display))
        return r, display

    def confirm(self, accept: bool = True):
        return self.c.post("/auth/me/telegram/confirm", json={"accept": accept})


class Admin:
    def __init__(self, username: str):
        self.c = browser()
        self.id = sign_in(self.c, role="admin", name=f"Ada {username.title()}", username=username)
        self.label = username


def db_do(fn):
    """fn(db) in its own session, committed; rows it returns stay readable."""
    db = SessionLocal(expire_on_commit=False)
    try:
        out = fn(db)
        db.commit()
        return out
    finally:
        db.close()


def said_yes(code, tg_id, username="tg_user", name="TG User"):
    """The bot's [Yes, that's me] tap."""
    return db_do(lambda db: (lambda u: u.id if u else None)(chat_links.telegram_said_yes(db, code, tg_id, username, name)))


def peek(code):
    """The bot's peek_code, as the account's id."""
    return db_do(lambda db: (lambda u: u.id if u else None)(chat_links.peek_code(db, code)))


def connect(p: Person, tg_id, username="tg_user", name="TG User") -> str:
    r, code = p.new_code()
    need(r.status_code == 200 and code, f"{p.label} makes a code", brief(r))
    need(said_yes(code, tg_id, username, name) == p.id, f"Telegram {tg_id} says Yes to {p.label}'s code")
    r = p.confirm(True)
    need(r.status_code == 200 and jd(r).get("connected") is True, f"{p.label} confirms in Settings", brief(r))
    return code


def link_of(user_id: str):
    return db_do(lambda db: db.query(ChatLink).filter(ChatLink.user_id == user_id).first())


def open_codes(user_id: str) -> list:
    now = utc_now()
    return db_do(lambda db: db.query(ChatLinkCode).filter(
        ChatLinkCode.user_id == user_id, ChatLinkCode.linked_at.is_(None),
        ChatLinkCode.revoked_at.is_(None), ChatLinkCode.expires_at > now).all())


def audits(action: str) -> list[AuditEntry]:
    return db_do(lambda db: db.query(AuditEntry).filter(AuditEntry.action == action).order_by(AuditEntry.id).all())


def forget_codes(user_id: str) -> None:
    """Moves this person's codes out of the last hour (the 10-an-hour
    limit), leaving whether they work alone."""
    with engine.begin() as conn:
        conn.execute(text("UPDATE chat_link_codes SET created_at = datetime(created_at, '-2 hours') WHERE user_id = :u"),
                     {"u": user_id})


def expire_codes(user_id: str, minutes_ago: int = 1) -> None:
    with engine.begin() as conn:
        conn.execute(text("UPDATE chat_link_codes SET expires_at = :t WHERE user_id = :u"),
                     {"t": (utc_now() - timedelta(minutes=minutes_ago)).replace(tzinfo=None), "u": user_id})


def users_by_id(admin) -> dict:
    r = admin.c.get("/users")
    return {u["id"]: u for u in (r.json() if r.status_code == 200 else [])}


def current(user_id: str) -> CurrentUser:
    u = db_do(lambda db: db.get(User, user_id))
    return CurrentUser(id=u.id, email=u.email, username=u.username or "", name=u.name, role=u.role,
                       must_change_password=bool(u.must_change_password), qualifications="", license_number="",
                       firm_name="", session_hash="")


W = SimpleNamespace()


# ================================================================= sections
def setup() -> None:
    init_db()
    W.admin = Admin("adalink")
    W.admin2 = Admin("adalink2")
    for name in ("alice", "bob", "carol", "dave", "erin", "frank", "gina", "hank", "ivy", "jack", "kim", "liam"):
        setattr(W, name, Person(f"{name}.tg"))


def tables() -> None:
    with engine.connect() as conn:
        sql = {n: conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:n"), {"n": n}).scalar() or ""
               for n in ("chat_links", "chat_link_codes")}
        indexes = {r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name IN ('chat_links','chat_link_codes')"))}
    links_sql = " ".join(sql["chat_links"].split())
    codes_sql = " ".join(sql["chat_link_codes"].split())
    check("UNIQUE (channel, external_id)" in links_sql, "chat_links: UNIQUE(channel, external_id) is inside CREATE TABLE", links_sql[-200:])
    check("UNIQUE (channel, user_id)" in links_sql, "chat_links: UNIQUE(channel, user_id) is inside CREATE TABLE", links_sql[-200:])
    check("UNIQUE (code_hash)" in codes_sql, "chat_link_codes: code_hash UNIQUE inside CREATE TABLE", codes_sql[-200:])
    for column in ("id", "channel", "external_id", "user_id", "external_username", "external_name", "linked_at", "last_used_at"):
        check(column in links_sql, f"chat_links has {column}")
    for column in ("id", "channel", "user_id", "code_hash", "created_at", "expires_at", "used_by_external_id",
                   "used_by_username", "used_by_name", "waiting_since", "linked_at", "revoked_at"):
        check(column in codes_sql, f"chat_link_codes has {column}")
    check("ix_chat_link_codes_user_id" in indexes, "chat_link_codes.user_id is indexed", str(indexes))

    def dup(fields):
        def go(db):
            db.add(ChatLink(channel="telegram", **fields))
            db.flush()
        try:
            db_do(go)
            return False
        except IntegrityError:
            return True

    db_do(lambda db: db.add(ChatLink(channel="telegram", external_id="900001", user_id="u-table-1")))
    check(dup({"external_id": "900001", "user_id": "u-table-2"}), "one account per Telegram: a second link for the Telegram is refused")
    check(dup({"external_id": "900002", "user_id": "u-table-1"}), "one Telegram per account: a second link for the account is refused")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM chat_links WHERE user_id LIKE 'u-table-%'"))


def code_text() -> None:
    alphabet = chat_links.CODE_ALPHABET
    check(len(alphabet) == 31 and not set("0O1IL") & set(alphabet), "the alphabet has 31 characters and no 0, O, 1, I or L", alphabet)
    for text_, want in (("K7M4-QX9P", "K7M4QX9P"), ("k7m4-qx9p", "K7M4QX9P"), ("  K7M4QX9P ", "K7M4QX9P"),
                        ("K7M4 QX9P", "K7M4QX9P"), ("K7M0-QX9P", None), ("K7M4-QX9", None), ("K7M4--QX9P", None),
                        ("GBH 2345 K", None), ("", None), (None, None), ("A" * 200, None)):
        check(chat_links.normalize_code(text_) == want, f"normalize_code({text_!r}) -> {want!r}", repr(chat_links.normalize_code(text_)))
    for text_, want in (("K7M4-QX9P", True), ("k7m4-qx9p", True), (" K7M4-QX9P\n", True),
                        ("K7M4QX9P", False), ("SJB8762M", False), ("STANDARD", False), ("GBH 2345 K", False),
                        ("SJB-8762M", False), ("K7M4 QX9P", False), ("K7M4-QX9P please", False), ("K1M4-QX9P", False)):
        check(chat_links.looks_like_code(text_) is want, f"looks_like_code({text_!r}) -> {want}")
    check(chat_links.code_from_start_payload("link-K7M4QX9P") == "K7M4QX9P", "the deep link's /start payload link-K7M4QX9P gives the code")
    check(chat_links.code_from_start_payload("K7M4QX9P") is None, "a /start payload without link- isn't a code")
    check(chat_links.display_code("K7M4QX9P") == "K7M4-QX9P", "display_code shows XXXX-XXXX")
    check(chat_links.masked_email("weikang@example.com") == "w***@example.com", "masked_email -> w***@example.com")


def endpoint_shapes() -> None:
    A = W.alice
    s = A.status()
    check(set(s) == STATUS_KEYS, "GET /auth/me/telegram has exactly the brief's keys", str(sorted(s)))
    check(s.get("connected") is False and s.get("code") is None and s.get("bot_username") is None,
          "not connected, no code, bot @username not known yet", str(s))
    r, code = A.new_code()
    body = jd(r)
    need(r.status_code == 200 and set(body) == STATUS_KEYS, "POST /auth/me/telegram/code -> the status shape", brief(r))
    c = body.get("code") or {}
    check(set(c) == CODE_KEYS, "code has exactly display, expires_at, deep_link, waiting", str(sorted(c)))
    check(chat_links.looks_like_code(code or ""), "code.display is XXXX-XXXX from the alphabet", str(code))
    check(c.get("waiting") is None, "nobody is waiting yet")
    check(c.get("deep_link") is None, "no deep link while the bot's @username is unknown", str(c.get("deep_link")))
    row = open_codes(A.id)[0]
    check(row.expires_at - row.created_at == timedelta(minutes=10), "valid for 10 minutes", str(row.expires_at - row.created_at))
    s = A.status()
    check(s.get("code") and s["code"]["display"] is None and s["code"]["deep_link"] is None,
          "GET afterwards: the code is open but its text isn't shown again", str(s.get("code")))
    check(s.get("code", {}).get("expires_at") == c.get("expires_at"), "GET shows the same expiry")

    # The bot has started and saved its @username.
    from app.channels.telegram import _save_bot_username

    _save_bot_username("@carlink_test_bot")
    r, code2 = A.new_code()
    c = jd(r).get("code") or {}
    want = f"https://t.me/carlink_test_bot?start=link-{chat_links.normalize_code(code2 or '')}"
    check(c.get("deep_link") == want, "with the bot's @username: deep_link t.me/<bot>?start=link-CODE", str(c.get("deep_link")))
    payload = (c.get("deep_link") or "").split("start=")[-1]
    check(len(payload) <= 64 and all(ch.isalnum() or ch in "_-" for ch in payload), "the /start payload fits Telegram's rules (<=64, A-Za-z0-9_-)")
    check(jd(r).get("bot_username") == "carlink_test_bot", "bot_username is shown", str(jd(r).get("bot_username")))
    check(code2 != code, "a second code is a different code")
    check(peek(code) is None, "...and the first one stopped working (a new code cancels the old)")
    check(len(open_codes(A.id)) == 1, "one open code per account", str(len(open_codes(A.id))))

    r = A.c.delete("/auth/me/telegram/code")
    check(r.status_code == 200 and jd(r) == {"ok": True}, "DELETE /auth/me/telegram/code -> {ok: true}", brief(r))
    check(not open_codes(A.id) and A.status().get("code") is None, "the open code is cancelled")
    check(TELEGRAM.take() == [], "cancelling a code nobody answered tells no one")
    r = A.c.delete("/auth/me/telegram")
    check(r.status_code == 200 and jd(r) == {"ok": True}, "DELETE /auth/me/telegram with nothing connected -> {ok: true}", brief(r))
    r = A.confirm(True)
    check(r.status_code == 409 and jd(r).get("code") == "nothing_waiting", "POST confirm with no code -> 409 nothing_waiting", brief(r))
    for bad in ({}, {"accept": "yes"}, {"accept": 1}, {"accept": None}):
        r = A.c.post("/auth/me/telegram/confirm", json=bad)
        check(r.status_code == 422, f"POST confirm {bad} -> 422", brief(r))
    s = db_do(lambda db: db.query(AppSetting).filter(AppSetting.key == "telegram_bot_username").first())
    check(s is not None and s.value == "carlink_test_bot", "the bot's @username is kept in app_settings (no @)", str(s and s.value))
    r = W.admin.c.get("/settings")
    check(r.status_code == 200 and "telegram_bot_username" not in jd(r) and "carlink_test_bot" not in r.text,
          "GET /settings never shows telegram_bot_username", brief(r))


def two_sided_link() -> None:
    B = W.bob
    r, code = B.new_code()
    need(code, "Bob makes a code", brief(r))
    db = SessionLocal()
    try:
        before = db.query(ChatLinkCode).filter(ChatLinkCode.user_id == B.id).all()
        before = [(c.id, c.used_by_external_id, c.waiting_since, c.linked_at, c.revoked_at) for c in before]
        who = chat_links.peek_code(db, code.lower())
        check(who is not None and who.id == B.id, "peek_code names the account (any case)", str(who and who.id))
        check(chat_links.peek_code(db, "ABCD-EFGH") is None, "peek_code on a made-up code -> None")
        db.rollback()
        after = [(c.id, c.used_by_external_id, c.waiting_since, c.linked_at, c.revoked_at)
                 for c in db.query(ChatLinkCode).filter(ChatLinkCode.user_id == B.id).all()]
        check(before == after, "peek_code changes nothing")
    finally:
        db.close()
    r = B.confirm(True)
    check(r.status_code == 409 and jd(r).get("code") == "nothing_waiting",
          "Settings can't confirm before Telegram has said Yes -> 409 nothing_waiting", brief(r))
    check(link_of(B.id) is None, "...and nothing is linked")

    got = said_yes(code, 5550001, "@bob_tg", "Bob On Telegram")
    check(got == B.id, "Telegram says Yes -> telegram_said_yes returns Bob's account")
    check(said_yes(code, 5550001) is None, "the same tap again -> None (used once)")
    check(said_yes(code, 5550002) is None, "another Telegram with the same code -> None")
    check(peek(code) is None, "peek_code on a code Telegram has answered -> None")
    check(link_of(B.id) is None, "Telegram's Yes alone links nothing")
    s = B.status()
    check(s.get("connected") is False and (s.get("code") or {}).get("waiting") == {"username": "bob_tg", "name": "Bob On Telegram"},
          "Settings shows waiting: {username, name} (no @)", str(s.get("code")))

    TELEGRAM.take()
    r = B.confirm(True)
    s = jd(r)
    need(r.status_code == 200 and s.get("connected") is True, "Bob confirms in Settings -> connected", brief(r))
    check(s.get("telegram_username") == "bob_tg" and s.get("telegram_name") == "Bob On Telegram" and s.get("linked_at"),
          "the status names the Telegram and when", str(s))
    check(s.get("code") is None, "no code is open any more")
    link = link_of(B.id)
    check(link is not None and link.external_id == "5550001", "the link holds the Telegram USER id as text", str(link and link.external_id))
    u = db_do(lambda db: chat_links.linked_user(db, 5550001))
    check(u is not None and u.id == B.id, "linked_user(Telegram id) -> Bob")
    sent = TELEGRAM.take()
    check(len(sent) == 1 and sent[0]["path"] == f"/bot{BOT_TOKEN}/sendMessage" and sent[0]["body"].get("chat_id") == "5550001"
          and "Connected" in sent[0]["body"].get("text", ""),
          "the person on Telegram is told \"Connected\" through the Bot API", str(sent))
    check(sent and "parse_mode" not in sent[0]["body"], "...in plain text (names with _ can't break it)")
    entries = [e for e in audits("telegram_linked") if B.email in e.detail]
    check(len(entries) == 1 and entries[0].user_id == B.id and "5550001" in entries[0].detail and "@bob_tg" in entries[0].detail,
          "history: telegram_linked by Bob, naming the Telegram", str([(e.user_id, e.detail) for e in entries]))
    r = B.confirm(True)
    check(r.status_code == 409 and jd(r).get("code") == "nothing_waiting", "confirming again -> 409 nothing_waiting", brief(r))


def not_me() -> None:
    C = W.carol
    r, code = C.new_code()
    need(said_yes(code, 5550101, "mallory", "Not Carol") == C.id, "a stranger's Telegram says Yes to Carol's code")
    TELEGRAM.take()
    r = C.confirm(False)
    check(r.status_code == 200 and jd(r).get("connected") is False and jd(r).get("code") is None,
          "Carol taps Not me -> not connected, the code is gone", brief(r))
    check(link_of(C.id) is None and db_do(lambda db: chat_links.linked_user(db, 5550101)) is None, "nothing is linked")
    check(said_yes(code, 5550102) is None, "the code can't be used again")
    sent = TELEGRAM.take()
    check(len(sent) == 1 and sent[0]["body"].get("chat_id") == "5550101" and "Not connected" in sent[0]["body"].get("text", ""),
          "the Telegram is told \"Not connected\"", str(sent))
    entries = [e for e in audits("telegram_link_refused") if C.email in e.detail]
    check(len(entries) == 1 and "mallory" in entries[0].detail, "history: telegram_link_refused names the Telegram", str([e.detail for e in entries]))

    r, code = C.new_code()
    said_yes(code, 5550103, "mallory2", "Also Not Carol")
    TELEGRAM.take()
    r = C.c.delete("/auth/me/telegram/code")
    check(r.status_code == 200, "Cancel while a Telegram is waiting -> 200", brief(r))
    sent = TELEGRAM.take()
    check(len(sent) == 1 and sent[0]["body"].get("chat_id") == "5550103" and "Not connected" in sent[0]["body"].get("text", ""),
          "...and that Telegram is told \"Not connected\"", str(sent))
    check(C.confirm(True).status_code == 409, "a cancelled code can't be confirmed")


def refusals() -> None:
    D, E = W.dave, W.erin
    r, code = D.new_code()
    expire_codes(D.id)
    check(said_yes(code, 5550201) is None, "an expired code -> telegram_said_yes None")
    check(D.status().get("code") is None, "...and Settings shows no code")
    forget_codes(D.id)

    r, code = D.new_code()
    need(said_yes(code, 5550202) == D.id, "Telegram says Yes in time")
    expire_codes(D.id)
    r = D.confirm(True)
    check(r.status_code == 409 and jd(r).get("code") == "code_expired", "Settings confirms after the 10 minutes -> 409 code_expired", brief(r))
    check(link_of(D.id) is None, "...nothing linked")
    forget_codes(D.id)

    r, old = D.new_code()
    r, new = D.new_code()
    check(said_yes(old, 5550203) is None, "a code replaced by a newer one -> None")
    check(said_yes(new, 5550203) == D.id, "the newer one works")
    r, newest = D.new_code()
    check(D.status().get("code", {}).get("waiting") is None, "a new code also cancels one Telegram had said Yes to")
    check(D.confirm(True).status_code == 409, "...which then can't be confirmed")
    forget_codes(D.id)

    # A Telegram that is connected already can't take another code.
    connect(E, 5550210, "erin_tg", "Erin")
    r, dcode = D.new_code()
    check(said_yes(dcode, 5550210) is None, "a code from a Telegram connected to someone else -> None (send /unlink first)")
    r, ecode = E.new_code()
    check(said_yes(ecode, 5550210) is None, "...or connected to the same account -> None")
    forget_codes(D.id)
    forget_codes(E.id)

    # A disabled account's code.
    F = W.frank
    r, fcode = F.new_code()
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET active = 0 WHERE id = :u"), {"u": F.id})
    check(peek(fcode) is None, "peek_code on a disabled account's code -> None")
    check(said_yes(fcode, 5550220) is None, "telegram_said_yes on a disabled account's code -> None")
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET active = 1 WHERE id = :u"), {"u": F.id})
    db = SessionLocal()
    try:
        try:
            with engine.begin() as conn:
                conn.execute(text("UPDATE users SET active = 0 WHERE id = :u"), {"u": F.id})
            chat_links.new_code(db, current(F.id))
            check(False, "new_code for a disabled account raises")
        except chat_links.ChatLinkError as exc:
            check(exc.code == "account_disabled" and exc.status == 403, "new_code for a disabled account -> account_disabled (403)", exc.code)
        finally:
            db.rollback()
            with engine.begin() as conn:
                conn.execute(text("UPDATE users SET active = 1 WHERE id = :u"), {"u": F.id})
    finally:
        db.close()


def rate_limit() -> None:
    G = W.gina
    codes = []
    for i in range(10):
        r, code = G.new_code()
        codes.append((r.status_code, code))
    check(all(s == 200 and c for s, c in codes), "10 codes in an hour -> all 200", str([s for s, _ in codes]))
    r, code = G.new_code()
    check(r.status_code == 429 and jd(r).get("code") == "rate_limited" and code is None, "the 11th -> 429 rate_limited", brief(r))
    check(said_yes(codes[-1][1], 5550301) == G.id, "...and the 10th code still works (the refusal changed nothing)")
    forget_codes(G.id)
    r, code = G.new_code()
    check(r.status_code == 200 and code, "an hour later codes can be made again", brief(r))
    forget_codes(G.id)

    # Several tabs at once at the 10th: only one gets it.
    for _ in range(9):
        G.new_code()
    results = []
    barrier = threading.Barrier(4)

    def tab():
        db = SessionLocal()
        try:
            barrier.wait()
            try:
                chat_links.new_code(db, current(G.id))
                db.commit()
                results.append("made")
            except chat_links.ChatLinkError as exc:
                db.rollback()
                results.append(exc.code)
        finally:
            db.close()

    threads = [threading.Thread(target=tab) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check(sorted(results) == ["made", "rate_limited", "rate_limited", "rate_limited"],
          "4 tabs at once with 9 made: exactly one makes the 10th", str(results))
    count = db_do(lambda db: db.query(ChatLinkCode).filter(ChatLinkCode.user_id == G.id,
                                                           ChatLinkCode.created_at > utc_now() - timedelta(hours=1)).count())
    check(count == 10, "10 codes in the last hour, not more", str(count))
    forget_codes(G.id)


def races() -> None:
    H = W.hank
    r, code = H.new_code()
    winners = []
    barrier = threading.Barrier(6)

    def tap(i):
        db = SessionLocal()
        try:
            barrier.wait()
            u = chat_links.telegram_said_yes(db, code, 5550400 + i, f"racer{i}", f"Racer {i}")
            db.commit()
            if u is not None:
                winners.append(i)
        finally:
            db.close()

    threads = [threading.Thread(target=tap, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check(len(winners) == 1, "6 Telegrams tapping Yes on one code at once: exactly one wins", str(winners))
    waiting = (H.status().get("code") or {}).get("waiting") or {}
    check(winners and waiting.get("username") == f"racer{winners[0]}", "Settings shows the one that won", str(waiting))

    results = []
    barrier2 = threading.Barrier(2)
    cookie = H.c.cookies.get(SESSION_COOKIE)

    def press():
        c = browser()
        c.cookies.set(SESSION_COOKIE, cookie)
        barrier2.wait()
        r = c.post("/auth/me/telegram/confirm", json={"accept": True})
        results.append((r.status_code, jd(r).get("code")))

    threads = [threading.Thread(target=press) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check(sorted(results, key=str) == sorted([(200, None), (409, "nothing_waiting")], key=str),
          "Yes, connect pressed twice at once: one 200, one 409 nothing_waiting", str(results))
    n = db_do(lambda db: db.query(ChatLink).filter(ChatLink.user_id == H.id).count())
    check(n == 1, "one link made", str(n))
    TELEGRAM.take()

    # One Telegram waiting on two accounts' codes: the second confirm loses.
    I, J = W.ivy, W.jack
    r, icode = I.new_code()
    r, jcode = J.new_code()
    check(said_yes(icode, 5550500, "both", "Both") == I.id and said_yes(jcode, 5550500, "both", "Both") == J.id,
          "one Telegram (not connected yet) says Yes to two accounts' codes")
    check(I.confirm(True).status_code == 200, "Ivy confirms first -> connected")
    r = J.confirm(True)
    check(r.status_code == 409 and jd(r).get("code") == "telegram_in_use", "Jack confirms second -> 409 telegram_in_use", brief(r))
    check(link_of(J.id) is None and J.status().get("code") is None, "...Jack isn't linked, and his code is closed (not left waiting)")
    check(db_do(lambda db: chat_links.linked_user(db, 5550500)).id == I.id, "the Telegram stays Ivy's")
    TELEGRAM.take()


def relink() -> None:
    K = W.kim
    connect(K, 5550600, "kim_old", "Kim Old Phone")
    forget_codes(K.id)
    r, code = K.new_code()
    check(r.status_code == 200 and jd(r).get("connected") is True and code, "a connected account can make a code (a new phone)", brief(r))
    said_yes(code, 5550601, "kim_new", "Kim New Phone")
    r = K.confirm(True)
    check(r.status_code == 200 and jd(r).get("telegram_username") == "kim_new", "confirming connects the new Telegram", brief(r))
    check(db_do(lambda db: chat_links.linked_user(db, 5550600)) is None, "the old Telegram isn't connected any more")
    n = db_do(lambda db: db.query(ChatLink).filter(ChatLink.user_id == K.id).count())
    check(n == 1, "still one Telegram per account", str(n))
    entries = [e for e in audits("telegram_relinked") if K.email in e.detail]
    check(len(entries) == 1 and "kim_new" in entries[0].detail and "kim_old" in entries[0].detail,
          "history: telegram_relinked names both", str([e.detail for e in entries]))
    TELEGRAM.take()


def linked_user_rules() -> None:
    B = W.bob  # connected to 5550001 in two_sided_link
    db = SessionLocal()
    try:
        check(chat_links.linked_user(db, 424242) is None, "linked_user of an unknown Telegram -> None")
        check(chat_links.linked_user(db, None) is None, "linked_user(None) -> None")
    finally:
        db.close()
    with engine.begin() as conn:
        conn.execute(text("UPDATE chat_links SET last_used_at = NULL WHERE user_id = :u"), {"u": B.id})
    db_do(lambda db: chat_links.linked_user(db, "5550001"))
    first = link_of(B.id).last_used_at
    check(first is not None, "linked_user writes last_used_at", str(first))
    writes = {"n": 0}

    def count(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE CHAT_LINKS"):
            writes["n"] += 1

    event.listen(engine, "before_cursor_execute", count)
    try:
        for _ in range(5):
            db_do(lambda db: chat_links.linked_user(db, "5550001"))
    finally:
        event.remove(engine, "before_cursor_execute", count)
    check(writes["n"] == 0 and link_of(B.id).last_used_at == first, "...then not again within 10 minutes (no write per message)", str(writes))
    with engine.begin() as conn:
        conn.execute(text("UPDATE chat_links SET last_used_at = :t WHERE user_id = :u"),
                     {"t": (utc_now() - timedelta(minutes=11)).replace(tzinfo=None), "u": B.id})
    db_do(lambda db: chat_links.linked_user(db, "5550001"))
    check(link_of(B.id).last_used_at > first, "...and again after 10 minutes")
    db_do(lambda db: chat_links.linked_user(db, "5550001", username="bob_renamed", name="Bob‮Renamed"))
    link = link_of(B.id)
    check(link.external_username == "bob_renamed" and link.external_name == "BobRenamed",
          "a new @username / name is kept (invisible characters dropped)", f"{link.external_username!r} {link.external_name!r}")
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET active = 0 WHERE id = :u"), {"u": B.id})
    check(db_do(lambda db: chat_links.linked_user(db, "5550001")) is None, "a disabled account -> None, even with a link row left")
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET active = 1 WHERE id = :u"), {"u": B.id})


def disconnect() -> None:
    I = W.ivy  # connected to 5550500
    r = I.c.delete("/auth/me/telegram")
    check(r.status_code == 200 and jd(r) == {"ok": True}, "DELETE /auth/me/telegram -> {ok: true}", brief(r))
    check(link_of(I.id) is None and db_do(lambda db: chat_links.linked_user(db, 5550500)) is None, "Ivy's Telegram is disconnected")
    entries = [e for e in audits("telegram_unlinked") if I.email in e.detail]
    check(len(entries) == 1 and entries[0].user_id == I.id and "from Settings" in entries[0].detail,
          "history: telegram_unlinked by Ivy (from Settings)", str([(e.user_id, e.detail) for e in entries]))
    check(I.status().get("connected") is False, "GET says not connected")

    K = W.kim  # connected to 5550601
    r = W.admin.c.delete(f"/users/{K.id}/telegram")
    check(r.status_code == 200 and jd(r) == {"ok": True}, "admin: DELETE /users/{id}/telegram -> {ok: true}", brief(r))
    check(link_of(K.id) is None, "Kim's Telegram is disconnected")
    entries = [e for e in audits("telegram_unlinked") if K.email in e.detail]
    check(entries and entries[-1].user_id == W.admin.id and "by an admin" in entries[-1].detail,
          "history: telegram_unlinked by the admin", str([(e.user_id, e.detail) for e in entries]))
    r = W.admin.c.delete("/users/nobody-here/telegram")
    check(r.status_code == 404, "admin: an unknown account -> 404", brief(r))
    r = W.admin.c.delete(f"/users/{K.id}/telegram")
    check(r.status_code == 200, "admin: nothing connected -> still {ok: true}", brief(r))
    r = W.liam.c.delete(f"/users/{K.id}/telegram")
    check(r.status_code == 403, "a plain user: DELETE /users/{id}/telegram -> 403", brief(r))
    db = SessionLocal()
    try:
        try:
            chat_links.unlink(db, actor=None, reason="x")
            check(False, "unlink with neither id raises")
        except ValueError:
            check(True, "unlink needs exactly one of user_id / external_id")
    finally:
        db.close()
    # The bot's /unlink: by Telegram id.
    connect(I, 5550700, "ivy_tg", "Ivy")
    forget_codes(I.id)
    gone = db_do(lambda db: chat_links.unlink(db, external_id=5550700, actor=db.get(User, I.id), reason="from Telegram"))
    check(gone is True and link_of(I.id) is None, "unlink(external_id=...) (the bot's /unlink) disconnects")
    check(db_do(lambda db: chat_links.unlink(db, external_id=5550700, actor=None, reason="x")) is False, "...and again -> False")


def users_and_system() -> None:
    admin = W.admin
    users = users_by_id(admin)
    check(all("telegram" in u for u in users.values()), "every GET /users row has telegram")
    check(users[W.alice.id]["telegram"] is None, "not connected -> telegram: null")
    connect(W.liam, 5550800, "liam_tg", "Liam On TG")
    forget_codes(W.liam.id)
    row = users_by_id(admin)[W.liam.id]["telegram"]
    check(isinstance(row, dict) and set(row) == {"username", "name", "linked_at"} and row["username"] == "liam_tg",
          "connected -> telegram: {username, name, linked_at}", str(row))

    selects = {"n": 0}

    def count(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects["n"] += 1

    event.listen(engine, "before_cursor_execute", count)
    try:
        admin.c.get("/users")
        selects["n"] = 0
        admin.c.get("/users")
        before = selects["n"]
        for i, p in enumerate((W.alice, W.dave, W.frank)):
            event.remove(engine, "before_cursor_execute", count)
            connect(p, 5550810 + i, f"batch{i}", f"Batch {i}")
            forget_codes(p.id)
            event.listen(engine, "before_cursor_execute", count)
        selects["n"] = 0
        admin.c.get("/users")
        after = selects["n"]
    finally:
        event.remove(engine, "before_cursor_execute", count)
    check(after == before, "3 more connected accounts add no queries to GET /users (batched)", f"{before} -> {after}")
    TELEGRAM.take()

    r = admin.c.get("/system/info")
    tg = jd(r).get("telegram")
    want = db_do(lambda db: db.query(ChatLink).count())
    check(r.status_code == 200 and tg == {"bot_username": "carlink_test_bot", "accounts_connected": want},
          "GET /system/info: telegram {bot_username, accounts_connected}", str(tg))
    r = W.liam.c.get("/system/info")
    check(r.status_code == 403, "GET /system/info stays admin-only", brief(r))


def disable_hook() -> None:
    A = W.alice  # connected (users_and_system)
    need(link_of(A.id) is not None, "Alice is connected")
    forget_codes(A.id)
    r, code = A.new_code()
    need(code, "Alice also has a code open")

    # A disable that is refused part-way changes nothing: the link is in its transaction.
    real = auth_api.recipient_still_active

    def refuse(db, recipient):
        db.rollback()
        raise ApiError(409, "That account is disabled.", "owner_inactive")

    auth_api.recipient_still_active = refuse
    try:
        r = W.admin.c.patch(f"/users/{A.id}", json={"active": False})
    finally:
        auth_api.recipient_still_active = real
    check(r.status_code == 409, "a disable refused at its last check -> 409", brief(r))
    check(link_of(A.id) is not None and open_codes(A.id), "...and the link and code are still there (same transaction)")

    r = W.admin.c.patch(f"/users/{A.id}", json={"active": False})
    check(r.status_code == 200 and jd(r).get("telegram") is None, "disable -> 200; the row shows telegram: null", brief(r))
    check(link_of(A.id) is None, "disabling ends the Telegram link")
    check(not open_codes(A.id), "...and the open code")
    check(said_yes(code, 5550900) is None, "...which Telegram can't use")
    entries = [e for e in audits("telegram_unlinked") if A.email in e.detail]
    check(entries and "account disabled" in entries[-1].detail and entries[-1].user_id == W.admin.id,
          "history: telegram_unlinked (account disabled) by the admin", str([e.detail for e in entries]))
    r = W.admin.c.patch(f"/users/{A.id}", json={"active": True})
    check(r.status_code == 200 and jd(r).get("telegram") is None, "enabling again doesn't reconnect", brief(r))
    check(db_do(lambda db: chat_links.linked_user(db, 5550810)) is None, "the old Telegram stays disconnected")


def password_hooks() -> None:
    # Admin reset to a temporary password.
    D = W.dave  # connected (users_and_system)
    need(link_of(D.id) is not None, "Dave is connected")
    forget_codes(D.id)
    D.new_code()
    r = W.admin.c.post(f"/users/{D.id}/reset-password")
    check(r.status_code == 200, "admin resets Dave's password", brief(r))
    check(link_of(D.id) is None and not open_codes(D.id), "an admin's password reset ends the link and open codes")
    entries = [e for e in audits("telegram_unlinked") if D.email in e.detail]
    check(entries and "password reset by an admin" in entries[-1].detail, "history says why", str([e.detail for e in entries]))
    temp = jd(r).get("temporary_password")
    t = browser()
    r = t.post("/auth/login", json={"email": D.email, "password": temp}, headers=fresh_ip())
    need(r.status_code == 200, "Dave signs in with the temporary password", brief(r))
    for method, path in (("GET", "/auth/me/telegram"), ("POST", "/auth/me/telegram/code"), ("DELETE", "/auth/me/telegram/code"),
                         ("POST", "/auth/me/telegram/confirm"), ("DELETE", "/auth/me/telegram")):
        kw = {"json": {"accept": True}} if path.endswith("confirm") else {}
        r = t.request(method, path, **kw)
        check(r.status_code == 403 and jd(r).get("code") == "password_change_required",
              f"temporary password: {method} {path} -> 403 password_change_required", brief(r))
    r = t.post("/auth/password", json={"current_password": temp, "new_password": "a brand new long passphrase"})
    check(r.status_code == 200, "Dave changes the temporary password (nothing to end; no error)", brief(r))
    r = t.get("/auth/me/telegram")
    check(r.status_code == 200 and jd(r).get("connected") is False, "...then GET works: not connected", brief(r))

    # A reset link being used.
    F = W.frank  # connected
    need(link_of(F.id) is not None, "Frank is connected")
    r = W.admin.c.post(f"/users/{F.id}/reset-link")
    token = jd(r).get("token")
    need(r.status_code == 200 and token, "admin makes Frank a reset link", brief(r))
    r = browser().post("/auth/links/accept", json={"token": token, "password": "another long new passphrase"}, headers=fresh_ip())
    check(r.status_code == 200, "Frank uses the reset link", brief(r))
    check(link_of(F.id) is None, "using a reset link ends the link")
    entries = [e for e in audits("telegram_unlinked") if F.email in e.detail]
    check(entries and "password reset with a link" in entries[-1].detail, "history says why", str([e.detail for e in entries]))

    # Changing your own password.
    L = W.liam  # connected
    need(link_of(L.id) is not None, "Liam is connected")
    r = L.c.post("/auth/password", json={"current_password": "wrong one entirely", "new_password": "yet another long passphrase"})
    check(r.status_code == 400 and link_of(L.id) is not None, "a wrong current password changes nothing (still connected)", brief(r))
    r = L.c.post("/auth/password", json={"current_password": PASSWORD, "new_password": "yet another long passphrase"})
    check(r.status_code == 200, "Liam changes his own password", brief(r))
    check(link_of(L.id) is None, "changing your own password ends the link (H2)")
    entries = [e for e in audits("telegram_unlinked") if L.email in e.detail]
    check(entries and "password changed" in entries[-1].detail and entries[-1].user_id == L.id, "history says why, by Liam",
          str([e.detail for e in entries]))
    r = L.c.get("/auth/me/telegram")
    check(r.status_code == 200 and jd(r).get("connected") is False, "Liam's own session still works; not connected", brief(r))

    # The server command's reset.
    from app.auth import manage

    E = W.erin  # connected to 5550210
    need(link_of(E.id) is not None, "Erin is connected")
    real = manage._read_password
    manage._read_password = lambda *a, **k: "server side new passphrase"
    try:
        manage.reset_password(SimpleNamespace(email=E.email, password_stdin=True))
    finally:
        manage._read_password = real
    check(link_of(E.id) is None, "python -m app.auth.manage reset-password ends the link too")


def sweep() -> None:
    J = W.jack
    now = utc_now()
    ids = {}
    for label, expired_hours in (("old", 25), ("recent", 23), ("open", None)):
        def add(db, label=label, expired_hours=expired_hours):
            expires = now + timedelta(minutes=10) if expired_hours is None else now - timedelta(hours=expired_hours)
            row = ChatLinkCode(user_id=J.id, code_hash=f"sweep-{label}-{gen_id()}", created_at=expires - timedelta(minutes=10),
                               expires_at=expires, linked_at=now if label == "old" else None)
            db.add(row)
            db.flush()
            return row.id
        ids[label] = db_do(add)
    result = cleanup.sweep()
    left = {r.id for r in db_do(lambda db: db.query(ChatLinkCode).filter(ChatLinkCode.id.in_(list(ids.values()))).all())}
    check(ids["old"] not in left, "the sweep deletes a code that expired more than a day ago")
    check(ids["recent"] in left and ids["open"] in left, "...and keeps one expired under a day ago, and an open one")
    check(result.get("telegram_codes", 0) >= 1, "cleanup.sweep() reports telegram_codes", str(result))
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM chat_link_codes WHERE code_hash LIKE 'sweep-%'"))


def owner_hint() -> None:
    H = W.hank  # connected; find the Telegram id
    link = link_of(H.id)
    need(link is not None, "Hank is connected")
    tg = link.external_id

    def case(channel, chat_id, **extra):
        def add(db):
            r = Report(channel=channel, reporter_chat_id=chat_id, status="confirmed", owner_id=None,
                       data={"description": "x", "category": ["Other"], "vehicle_info": {"plate_number": "SJB8762M"}}, **extra)
            db.add(r)
            db.flush()
            return r.id
        return db_do(add)

    mine = case("telegram", tg)
    other = case("telegram", "999999999")
    group = case("telegram", f"-{tg}")
    whatsapp = case("whatsapp", tg)
    r = W.admin.c.get(f"/reports/{mine}")
    so = jd(r).get("suggested_owner") or {}
    check(r.status_code == 200 and so.get("id") == H.id and so.get("reason") == "sent from their Telegram",
          "an Unassigned Telegram case from Hank's Telegram suggests Hank, \"sent from their Telegram\"", brief(r))
    check(jd(r).get("owner") is None, "...and it stays Unassigned (nobody claims earlier reports, D5)")
    check(db_do(lambda db: db.get(Report, mine).owner_id) is None, "...in the database too")
    for rid, what in ((other, "another Telegram"), (group, "a group chat id"), (whatsapp, "a WhatsApp case")):
        so = db_do(lambda db, rid=rid: owners.suggested_owner(db, db.get(Report, rid)))
        check(not so or so.get("reason") != "sent from their Telegram", f"{what}: no Telegram hint", str(so))

    # Order: who created / finished it comes first; the sender before who signed off or edited.
    def add_audit(rid, uid, action):
        db_do(lambda db: db.add(AuditEntry(report_id=rid, user_id=uid, user_name="x", action=action, detail="")))

    edited = case("telegram", tg)
    add_audit(edited, W.admin.id, "edited")
    so = db_do(lambda db: owners.suggested_owner(db, db.get(Report, edited)))
    check(so and so.get("id") == H.id, "the sender comes before whoever edited it most", str(so))
    created = case("telegram", tg)
    add_audit(created, W.jack.id, "created")
    so = db_do(lambda db: owners.suggested_owner(db, db.get(Report, created)))
    check(so and so.get("id") == W.jack.id and so.get("reason") == "created it", "...after whoever created it", str(so))
    r = W.hank.c.get(f"/reports/{mine}")
    check(r.status_code == 404, "Hank himself can't open the earlier case (it isn't his until an admin assigns it)", brief(r))
    # After disconnecting, no hint.
    db_do(lambda db: chat_links.unlink(db, user_id=H.id, actor=None, reason="test"))
    so = db_do(lambda db: owners.suggested_owner(db, db.get(Report, mine)))
    check(not so or so.get("reason") != "sent from their Telegram", "a disconnected Telegram gives no hint", str(so))


def limiter() -> None:
    clock = {"t": 1000.0}
    lim = chat_links.WrongCodeLimiter(clock=lambda: clock["t"])
    left = [lim.wrong("tg1") for _ in range(4)]
    check(left == [4, 3, 2, 1] and lim.blocked_for("tg1") is None, "4 wrong codes: 4, 3, 2, 1 tries left, not blocked", str(left))
    check(lim.wrong("tg1") == 0, "the 5th wrong code in a row -> 0 left")
    b = lim.blocked_for("tg1")
    check(b is not None and abs(b - 900) < 1, "...blocked for 15 minutes", str(b))
    check(lim.blocked_for("tg2") is None, "someone else isn't blocked")
    clock["t"] += 901
    check(lim.blocked_for("tg1") is None, "15 minutes later: not blocked")
    for _ in range(3):
        lim.wrong("tg3")
    lim.right("tg3")
    check([lim.wrong("tg3") for _ in range(4)] == [4, 3, 2, 1], "a code that works resets the count (5 wrong IN A ROW)")

    before = len(LOG_LINES)
    lim2 = chat_links.WrongCodeLimiter(clock=lambda: clock["t"])
    for i in range(50):
        lim2.wrong(f"g{i}")
    warn = [l for l in LOG_LINES[before:] if "wrong Telegram link codes" in l]
    check(not warn, "50 wrong codes in an hour across everyone: no alarm yet", str(warn))
    lim2.wrong("g50")
    for i in range(100):
        lim2.wrong(f"h{i}")
    warn = [l for l in LOG_LINES[before:] if "wrong Telegram link codes" in l]
    check(len(warn) == 1 and " WARNING " in warn[0], "the 51st -> one WARNING line, and no more within the hour", str(warn))
    clock["t"] += 3601
    for i in range(52):
        lim2.wrong(f"k{i}")
    warn = [l for l in LOG_LINES[before:] if "wrong Telegram link codes" in l]
    check(len(warn) == 2, "an hour later, another flood -> one more", str(len(warn)))

    lim3 = chat_links.WrongCodeLimiter(clock=lambda: clock["t"], max_entries=3)
    for _ in range(5):
        lim3.wrong("blocked")
    for name in ("a", "b"):
        lim3.wrong(name)
    check(lim3.blocked_for("blocked") is not None, "at the size limit, entries are kept")
    lim3.wrong("c")
    check(lim3.blocked_for("blocked") is None and len(lim3._people) == 3, "past it, the longest-quiet one goes (not everyone)",
          str(list(lim3._people)))
    check(isinstance(chat_links.wrong_codes, chat_links.WrongCodeLimiter), "chat_links.wrong_codes: the bot's limiter")


def telegram_messages() -> None:
    check(logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
          and logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING,
          "httpx and httpcore log at WARNING or above")
    M = W.jack
    forget_codes(M.id)
    r, code = M.new_code()
    said_yes(code, 5551000, "jack_tg", "Jack")
    TELEGRAM.status = 500
    before = len(LOG_LINES)
    try:
        r = M.confirm(True)
    finally:
        TELEGRAM.status = 200
    check(r.status_code == 200 and jd(r).get("connected") is True, "Telegram refusing the message doesn't stop connecting", brief(r))
    lines = LOG_LINES[before:]
    check(any("HTTP 500" in l for l in lines), "...it's logged as a warning", str(lines[-3:]))
    TELEGRAM.take()
    db_do(lambda db: chat_links.unlink(db, user_id=M.id, actor=None, reason="test"))
    forget_codes(M.id)
    r, code = M.new_code()
    said_yes(code, 5551001, "jack_tg", "Jack")
    real = telegram_api.TELEGRAM_API
    telegram_api.TELEGRAM_API = "http://127.0.0.1:9"  # nothing listens there
    before = len(LOG_LINES)
    try:
        r = M.confirm(True)
    finally:
        telegram_api.TELEGRAM_API = real
    check(r.status_code == 200, "Telegram unreachable doesn't stop connecting either", brief(r))
    check(any("Couldn't send a connect message" in l for l in LOG_LINES[before:]), "...logged by type only", str(LOG_LINES[before:][-3:]))
    no_token = telegram_api.settings.telegram_bot_token
    telegram_api.settings.telegram_bot_token = ""
    try:
        db_do(lambda db: chat_links.unlink(db, user_id=M.id, actor=None, reason="test"))
        forget_codes(M.id)
        r, code = M.new_code()
        said_yes(code, 5551002)
        TELEGRAM.take()
        M.confirm(True)
        check(TELEGRAM.take() == [], "no bot token: nothing is sent")
    finally:
        telegram_api.settings.telegram_bot_token = no_token


def nothing_leaked() -> None:
    check(CODES, f"{len(CODES)} codes were handed out")
    leaks = [c for c in CODES if any(c in line or chat_links.display_code(c) in line for line in LOG_LINES)]
    check(not leaks, f"no code is in any of the {len(LOG_LINES)} log lines", f"{len(leaks)} leaked")
    check(not any(BOT_TOKEN in line or BOT_TOKEN.split(":")[1] in line for line in LOG_LINES), "the bot token is in no log line")
    shown = {c: sum(1 for body in RESPONSES if chat_links.display_code(c) in body or c in body) for c in CODES}
    check(all(n == 1 for n in shown.values()), "each code was in exactly one response: the POST that made it",
          str(sorted(set(shown.values()))))
    with engine.begin() as conn:
        conn.execute(text("PRAGMA wal_checkpoint(FULL)"))
    blobs = b"".join(p.read_bytes() for p in _tmp.iterdir() if p.is_file() and p.name.startswith("test.db"))
    leaks = [c for c in CODES if c.encode() in blobs or chat_links.display_code(c).encode() in blobs]
    check(not leaks, "no plain code is in the SQLite files (only SHA-256)", f"{len(leaks)} found")
    check(not any(body.startswith("Internal Server Error") for body in RESPONSES), "no response was a 500")


def signed_out() -> None:
    anon = browser()
    for method, path in (("GET", "/auth/me/telegram"), ("POST", "/auth/me/telegram/code"), ("DELETE", "/auth/me/telegram/code"),
                         ("POST", "/auth/me/telegram/confirm"), ("DELETE", "/auth/me/telegram"), ("DELETE", f"/users/{W.bob.id}/telegram")):
        r = anon.request(method, path)
        check(r.status_code == 401, f"signed out: {method} {path.replace(W.bob.id, '{id}')} -> 401", brief(r))


def main() -> int:
    for name, fn in (
        ("setup", setup),
        ("the tables: unique rules inside them", tables),
        ("codes: alphabet, normalize_code, looks_like_code (plates and words aren't codes)", code_text),
        ("endpoint shapes; the code shown once; deep link; cancel", endpoint_shapes),
        ("the two-sided link: Telegram Yes, then Settings Yes", two_sided_link),
        ("Not me, and Cancel while waiting", not_me),
        ("refusals: expired, replaced, connected Telegram, disabled account", refusals),
        ("10 codes an hour (429 rate_limited), also with tabs racing", rate_limit),
        ("races: one Yes wins; one confirm wins; telegram_in_use", races),
        ("a connected account connects a new Telegram (relinked)", relink),
        ("linked_user: active only; last_used_at at most every 10 minutes", linked_user_rules),
        ("disconnect: self, admin, the bot's unlink", disconnect),
        ("GET /users telegram (batched), GET /system/info telegram", users_and_system),
        ("disabling ends the link, in the disable's transaction; enabling doesn't reconnect", disable_hook),
        ("password resets and changes end the link; temporary passwords refused", password_hooks),
        ("the cleanup sweep removes day-old codes", sweep),
        ("suggested_owner: sent from their Telegram", owner_hint),
        ("WrongCodeLimiter: 5 in a row, 15 minutes, the global alarm", limiter),
        ("telling the person on Telegram: failures don't matter, token never logged", telegram_messages),
        ("signed out: 401", signed_out),
        ("no code in a log line, a second response or the database", nothing_leaked),
    ):
        run_section(name, fn)
        if name == "setup" and FAILURES:
            break
    TELEGRAM.server.shutdown()
    print(f"\ntest_telegram_link: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  [{section}] {label}")
    if FAILURES:
        return 1
    print("\nALL TELEGRAM LINK CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
