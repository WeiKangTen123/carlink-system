"""API tests for release P6's Public Inbox (agent P6-API): app/api/inbox.py
and what it changes in the case routes.

    python scripts/test_public_inbox.py

From plans/2026-10-30-public-reporter: agent B's checks 1-9 (merge is
deferred to P6.1), the build brief's §2.3 and §2.8 API list, and
p6_decisions.md (the settings, the billing rule). What it checks:
- who sees the inbox (admins and "see all" staff; 403 / 404 for anyone
  else), the list's and the report page's shapes, the masked phone, the
  count; untaken reports in no Cases list, analytics or taxonomy, while a
  legacy draft with a NULL channel still lists;
- before a take an admin's PUT /data, /process and DELETE get 403, with no
  Gemini call; PATCH owner stays 409 owner_draft;
- take (and two at once), assign (vetted, active, admins only), release,
  release-then-assign, discard (a reason, the 4 tables and the folder, no
  name or phone in the history, two at once with a take), a spam discard
  blocking the sender;
- DELETE /reports/{id} of a taken public draft and of a filed public case;
- Start on a public draft: the presser's own billed key only until
  company_key_billed, then the company key; the name and phone in no
  request; the same rule for annex AI on the filed case, and for the edit
  form's analyze-photos given its report_id (404 / 403 first; a case not
  from the public, or no report_id, as before);
- finalize: filed_at, telegram_* cleared, reporter_chat_id "public",
  public_reference in GET /reports, public_submission on the case, the name
  escaped in the PDF, the inbox routes answering inbox_filed;
- 30 untaken reports don't block POST /intake; draft_limit on take;
- the sweep: kept at day 29, gone after day 30; taken on day 25 kept;
- GET/PUT /settings (the P6 keys admins only; company_key_billed as a JSON
  boolean or "true"/"false") and the public GET /privacy/contact;
- /files caches intake/ photos an hour; the history's telegram_public
  wording; no 500, no outside connection, no name or phone in a log line.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database; Gemini is a fake that records each request; the PDF renderer
writes a stub; every outside connection is refused.
"""
import hashlib
import io
import json
import logging
import os
import random
import re
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
from collections import Counter
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
_tmp = Path(tempfile.mkdtemp(prefix="public-inbox-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
SECRETS = (_tmp / "secrets").resolve()
DB_FILE = (_tmp / "test.db").resolve()


def make_key(label: str) -> str:
    return ("AIzaSy" + label + "x" * 40)[:35] + hashlib.md5(label.encode()).hexdigest()[:4]


COMPANY_KEY = make_key("CompanyEnv")
os.environ.update({
    "STORAGE_DIR": str(STORAGE),
    "SECRETS_DIR": str(SECRETS),
    "DATABASE_URL": f"sqlite:///{DB_FILE.as_posix()}",
    "GEMINI_API_KEY": COMPANY_KEY,
    "RUN_BACKGROUND_CLEANUP": "false",
    "TELEGRAM_BOT_TOKEN": "",
    "TWILIO_ACCOUNT_SID": "",
    "TWILIO_AUTH_TOKEN": "",
    "GEMINI_MAX_REQUESTS_PER_MINUTE": "100000",
    "GEMINI_MIN_CALL_INTERVAL_SECONDS": "0",
    "MIN_FREE_DISK_GB": "0",
})
os.environ.pop("GOOGLE_API_KEY", None)

LOG_RECORDS: list[tuple[int, str, str]] = []


class _Keep(logging.Handler):
    def emit(self, record):
        try:
            LOG_RECORDS.append((record.levelno, record.name, record.getMessage()))
        except Exception:
            LOG_RECORDS.append((record.levelno, record.name, str(record.msg)))


logging.getLogger().addHandler(_Keep(level=logging.DEBUG))
logging.getLogger().setLevel(logging.INFO)

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
    if seconds > 0.2 and threading.current_thread() is not threading.main_thread():
        seconds = 0.01
    _real_sleep(seconds)


time.sleep = _quick_sleep

# ================================================================ fake Google
from PIL import Image  # noqa: E402

_fake_lock = threading.Lock()
CALLS: list[dict] = []

DRAFT_ANSWER = {
    "description": "Insured vehicle was rear-ended.",
    "category": ["Vehicle Collision or Damage"],
    "damaged_parts": ["Rear Bumper"],
    "damage_summary": [{"part": "Rear Bumper", "severity": "Moderate", "ai_confidence": "High"}],
    # A plate of its own: the typed one must win (pipeline.merge_redraft).
    "vehicle_info": {"make": "Honda", "model": "Vezel", "plate_number": "SNA1234B"},
}


def _parts(kw: dict) -> list:
    parts = kw.get("input") if kw.get("input") is not None else kw.get("contents")
    return [p for p in parts if isinstance(p, dict)] if isinstance(parts, list) else []


def _purpose(kw: dict) -> str:
    fmt = kw.get("response_format")
    props = ((fmt.get("schema") if isinstance(fmt, dict) else None) or {}).get("properties") or {}
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
            if (is_image and k in ("data", "uri", "url")) or isinstance(v, (bytes, bytearray)):
                continue
            _strings(v, out, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _strings(v, out, depth + 1)


def _answer(purpose: str, kw: dict) -> str:
    texts = [p.get("text") or "" for p in _parts(kw) if p.get("type") == "text"]
    if purpose == "sort":
        labels = [m.group(1) for t in texts for m in [re.match(r"Photo (P\d+):", t)] if m]
        return json.dumps({"photos": [
            {"label": label, "kind": "damage", "area": "rear", "quality": "good", "shows_damage": True,
             "part": "Rear Bumper", "note": f"photo {label}"} for label in labels]})
    if purpose == "annex":
        return json.dumps({"parts": [{"part": "Rear Bumper", "lines": [{"name": "Rear Bumper clips", "qty": 2}]}]})
    if purpose == "draft":
        return json.dumps(DRAFT_ANSWER)
    return "{}"


class _FakeInteractions:
    def __init__(self, client):
        self.client = client

    def create(self, **kw):
        purpose = _purpose(kw)
        found: list[str] = []
        _strings(kw, found)
        with _fake_lock:
            CALLS.append({"key": self.client.key, "purpose": purpose, "text": "\n".join(found)})
        return SimpleNamespace(output_text=_answer(purpose, kw))


class FakeGenaiClient:
    def __init__(self, *args, api_key=None, **kwargs):
        self.key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        self.interactions = _FakeInteractions(self)
        self.models = SimpleNamespace(list=lambda *a, **k: iter([]))

    def close(self):
        pass


import google.genai as _genai  # noqa: E402

_genai.Client = FakeGenaiClient

# ============================================ a reports table with a nullable channel
# Made before the app's start-up (create_all leaves an existing table alone):
# a database whose older rows may have no channel, which the inbox's clauses
# must treat NULL-safely.
from sqlalchemy.dialects import sqlite as _sqlite_dialect  # noqa: E402
from sqlalchemy.schema import CreateTable  # noqa: E402

from app.reports.models import Report as _ReportModel  # noqa: E402

_ddl = str(CreateTable(_ReportModel.__table__).compile(dialect=_sqlite_dialect.dialect()))
LEGACY_TABLE = re.search(r"\bchannel VARCHAR NOT NULL\b", _ddl) is not None
if LEGACY_TABLE:
    with sqlite3.connect(DB_FILE) as _conn:
        _conn.execute(re.sub(r"\bchannel VARCHAR NOT NULL\b", "channel VARCHAR", _ddl))

# ================================================================ the app
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.api.ai_keys as ai_keys_api  # noqa: E402
import app.rendering.renderer as renderer  # noqa: E402
from app.ai.keys import PUBLIC_KEY_ONLY  # noqa: E402
from app.api.main import app  # noqa: E402
from app.auth.core import SESSION_COOKIE, create_session, report_history  # noqa: E402
from app.intake import cleanup, public  # noqa: E402
from app.reports.db import SessionLocal, engine  # noqa: E402
from app.reports.models import IntakePhoto, IntakeSession, PublicSubmission, Report, User, gen_id  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402
from app.storage.files import generate_preview, generate_thumbnail, tmp_dir  # noqa: E402

BASE = "https://carlink.test"
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PUBLIC_CHANNEL = "telegram_public"
ALL_FOUR = ["reports", "intake_sessions", "intake_photos", "public_submissions"]
PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}
STATUS_500: list[str] = []
KEY_LABEL: dict[str, str] = {COMPANY_KEY: "the company key"}
PEOPLE: list["Person"] = []
SEEDED: dict[str, dict] = {}  # report id -> name, phone, ref, tg, statement
CAPTURED_HTML: list[str] = []
W = SimpleNamespace()


class Stop(Exception):
    pass


def mask(value) -> str:
    out = str(value)
    for k in sorted(KEY_LABEL, key=len, reverse=True):
        out = out.replace(k, f"<{KEY_LABEL[k]}>")
    return out


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
        if r.status_code >= 500:
            STATUS_500.append(f"{args[0] if args else ''} {args[1] if len(args) > 1 else ''} -> {r.status_code}")
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
    return mask(f"{r.status_code} {(r.text or '').replace(chr(10), ' ')[:240]}")


def code_of(r) -> str | None:
    return jd(r).get("code")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def sql(query: str, **params) -> list[dict]:
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(text(query), params)]


def sql_write(query: str, **params) -> int:
    with engine.begin() as conn:
        return conn.execute(text(query), params).rowcount


def intake_folder(rid: str) -> Path:
    return STORAGE / "intake" / rid


def held_in(rid: str) -> list[str]:
    out = []
    for table, col in (("reports", "id"), ("intake_sessions", "report_id"), ("intake_photos", "report_id"),
                       ("public_submissions", "report_id")):
        if sql(f"SELECT COUNT(*) AS n FROM {table} WHERE {col} = :r", r=rid)[0]["n"]:
            out.append(table)
    return out


def still_held(rid: str) -> list[str]:
    return held_in(rid) + ([f"intake/{rid}/ folder"] if intake_folder(rid).exists() else [])


def pub_row(rid: str) -> dict:
    rows = sql("SELECT * FROM public_submissions WHERE report_id = :r", r=rid)
    return rows[0] if rows else {}


def report_row(rid: str) -> dict:
    rows = sql("SELECT id, status, channel, owner_id, reporter_chat_id FROM reports WHERE id = :r", r=rid)
    return rows[0] if rows else {}


def audit_for(rid: str) -> list[dict]:
    return sql("SELECT action, detail, user_id, user_name FROM audit_log WHERE report_id = :r ORDER BY id", r=rid)


def pii_in(blob: str, rid: str) -> list[str]:
    """The seeded sender's name (or a part of it) or phone, found in blob."""
    s = SEEDED.get(rid) or {}
    found = []
    for x in (s.get("name"), *(s.get("name") or "").split(), s.get("phone"), (s.get("phone") or "")[-8:]):
        if x and len(x) >= 4 and x in (blob or ""):
            found.append(x)
    return found


def first_photo_rel(rid: str) -> str:
    rows = sql("SELECT path FROM intake_photos WHERE report_id = :r ORDER BY position", r=rid)
    return Path(rows[0]["path"]).resolve().relative_to(STORAGE).as_posix() if rows else ""


def calls_since(mark: int) -> list[dict]:
    with _fake_lock:
        return list(CALLS[mark:])


def describe(calls: list[dict]) -> str:
    return ", ".join(f"{c['purpose']} on {KEY_LABEL.get(c['key'], 'an unknown key')}" for c in calls) or "no calls"


# ---------------------------------------------------------------- people
class Person:
    def __init__(self, label: str, *, role: str = "user", active: bool = True, **ticks):
        self.label = label
        self.name = f"{label} Staffer"
        self.key = None
        db = SessionLocal()
        try:
            user = User(email=f"{label.lower()}-{gen_id()}@carlink-p6.test", username=f"{label.lower()}{gen_id()[:5]}",
                        name=self.name, role=role, password_hash="!", must_change_password=False, active=active, **ticks)
            db.add(user)
            db.commit()
            self.id = user.id
        finally:
            db.close()
        self.c = None
        if active:
            self.sign_in()
            PEOPLE.append(self)

    def sign_in(self) -> None:
        db = SessionLocal()
        try:
            self.token = create_session(db, db.get(User, self.id))
            db.commit()
        finally:
            db.close()
        self.c = browser()
        self.c.cookies.set(SESSION_COOKIE, self.token)

    def tab(self) -> Browser:
        """Another browser tab on the same sign-in (for a second thread)."""
        c = browser()
        c.cookies.set(SESSION_COOKIE, self.token)
        return c

    def save_key(self) -> None:
        ai_keys_api.reset_limits()
        key = make_key(self.label + "Own")
        KEY_LABEL[key] = f"{self.label}'s own key"
        r = self.c.put("/auth/me/ai-key", json={"api_key": key, "billing_confirmed": True})
        need(r.status_code == 200, f"(setup) {self.label} saves an own AI key, billing confirmed", brief(r))
        self.key = key

    def drop_key(self) -> None:
        ai_keys_api.reset_limits()
        r = self.c.delete("/auth/me/ai-key")
        need(r.status_code in (200, 204), f"(setup) {self.label} removes their own AI key", brief(r))
        self.key = None


def staff(label: str, **ticks) -> Person:
    """"See all" staff: may take from the inbox."""
    return Person(label, sees_all_cases=True, **ticks)


def set_billed(flag) -> None:
    r = W.ada.c.put("/settings", json={"company_key_billed": flag})
    need(r.status_code == 200, f"(the admin sets company_key_billed = {flag!r})", brief(r))


# ---------------------------------------------------------------- seeding
def _photo_bytes(seed: str) -> bytes:
    h = hashlib.md5(seed.encode()).digest()
    img = Image.new("RGB", (320, 240), (h[0], h[1], h[2]))
    img.paste((h[3], h[4], h[5]), (10, 10, 60, 60))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def seed(*, submitted: datetime | None = None, photos: int = 1, phone: str | None = None, tg: str | None = None,
         label: str = "seed") -> str:
    """One untaken public report as the bot's Submit leaves it (brief §2.1):
    Report (telegram_public, intake, no owner, reporter_chat_id "public"),
    IntakeSession (uploading, public.reporter_notes_for's notes), IntakePhoto
    rows with files in intake/{id}/, and the PublicSubmission row."""
    submitted = submitted or utcnow()
    at = naive(submitted)
    rid = gen_id()
    ref = f"TG-{(submitted + timedelta(hours=8)).strftime('%y%m%d')}-" + "".join(random.choice(ALPHABET) for _ in range(4))
    tg = tg or str(880000000 + random.randrange(10 ** 7))
    first = random.choice(["Ignatius", "Perpetua", "Cornelius", "Theodora", "Bartholomew", "Euphemia"])
    name = f"{first} {label.capitalize()}{random.randrange(100, 999)}"
    phone = phone or f"+659{random.randrange(10 ** 7):07d}"
    statement = f"Seeded statement {label}: scraped in a carpark at Tampines."
    form = SimpleNamespace(role="driver", when_where="Tampines Mall carpark", statement=statement)
    data = SecurityIncidentDraft(description="", category=["Vehicle Collision or Damage"], reporter_name=name,
                                 reporter_role="Driver", reporter_contact=public.phone_display(phone)).model_dump()
    data["vehicle_info"] = {**(data.get("vehicle_info") or {}), "plate_number": "SBA1234A"}
    db = SessionLocal()
    try:
        db.add(Report(id=rid, channel=PUBLIC_CHANNEL, status="intake", owner_id=None, reporter_chat_id="public",
                      photo_paths=[], data=data, created_at=at))
        db.flush()
        db.add(IntakeSession(report_id=rid, stage="uploading", updated_at=at,
                             reporter_notes=public.reporter_notes_for(form, submitted)))
        folder = intake_folder(rid)
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(photos):
            pid = gen_id()
            path = folder / f"{pid}.jpg"
            body = _photo_bytes(f"{rid}-{i}")
            path.write_bytes(body)
            generate_thumbnail(str(path))
            generate_preview(str(path))
            db.add(IntakePhoto(id=pid, report_id=rid, position=i, original_name=f"telegram-{i + 1}.jpg", path=str(path),
                               sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body), created_at=at))
        db.add(PublicSubmission(
            report_id=rid, reference=ref, submitted_at=at, expires_at=at + public.UNTAKEN_MAX_AGE, consent_at=at,
            consent_version=public.CONSENT_VERSION, submitter_name=name, submitter_role="driver",
            submitter_phone=phone, phone_kind=public.phone_kind(phone), plate="SBA1234A", plate_looks_sg=True,
            when_where="Tampines Mall carpark", statement=statement, photo_count_sent=photos, forwarded_count=0,
            telegram_user_id=tg, telegram_username=f"tg{tg[-5:]}", telegram_name=name,
        ))
        db.commit()
    finally:
        db.close()
    SEEDED[rid] = {"name": name, "phone": phone, "ref": ref, "tg": tg, "statement": statement}
    return rid


def take(p: Person, rid: str, c=None):
    return (c or p.c).post(f"/inbox/{rid}/take")


def race(a, b) -> tuple:
    """Runs a() and b() in two threads released together."""
    gate = threading.Barrier(2)
    out = [None, None]

    def run(i, fn):
        try:
            gate.wait(timeout=10)
            out[i] = fn()
        except Exception as exc:
            out[i] = exc

    threads = [threading.Thread(target=run, args=(0, a)), threading.Thread(target=run, args=(1, b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return tuple(out)


def status(r) -> object:
    return r.status_code if hasattr(r, "status_code") else f"{type(r).__name__}: {r}"


def wait_done(p: Person, rid: str, timeout: float = 45.0) -> dict:
    end = time.time() + timeout
    state: dict = {}
    while time.time() < end:
        state = jd(p.c.get(f"/intake/{rid}"))
        if state.get("stage") in ("ready", "failed"):
            return state
        _real_sleep(0.15)
    return {**state, "stage": f"still {state.get('stage')!r} after {timeout:.0f}s"}


def inbox(p: Person, view: str = "new") -> tuple[int, dict]:
    r = p.c.get(f"/inbox?view={view}")
    return r.status_code, jd(r)


def inbox_ids(p: Person, view: str = "new") -> set:
    return {i.get("id") for i in inbox(p, view)[1].get("items", [])}


def list_ids(p: Person, scope: str | None = None) -> set:
    r = p.c.get("/reports" + (f"?scope={scope}" if scope else ""))
    return {row.get("id") for row in (r.json() if r.status_code == 200 else [])}


# ================================================================= sections
def setup() -> None:
    W.boot = browser()
    W.boot.__enter__()  # the api's start-up: tables, the AI key secret
    W.ada = Person("Ada", role="admin")
    W.pat = Person("Pat")  # plain
    W.bob = Person("Bob")  # plain
    W.tia = Person("Tia", ai_company_key_allowed=True)  # vetted by a tick, not "see all"
    W.dan = Person("Dan", active=False, sees_all_cases=True)  # disabled
    W.vera = staff("Vera")

    def fake_html_to_pdf(html, output_path, slot_wait=None, *args, **kwargs):
        CAPTURED_HTML.append(html)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"%PDF-1.4 fake")

    W.patches = [mock.patch.object(renderer, "html_to_pdf", fake_html_to_pdf)]
    for p in W.patches:
        p.start()
    check(LEGACY_TABLE, "(the reports table was made with a nullable channel before start-up)")
    check(not NET_REFUSED, "nothing tried to reach the internet during start-up", ", ".join(NET_REFUSED[:3]))


def settings_section() -> None:
    p6_keys = {"public_contact_phone", "dpo_contact", "company_key_billed"}
    r = W.pat.c.get("/settings")
    check(r.status_code == 200 and set(jd(r)) == {"company_name"}, "a plain user's GET /settings: company_name only", brief(r))
    r = W.vera.c.get("/settings")
    check(r.status_code == 200 and not (set(jd(r)) & p6_keys), "see-all staff's GET /settings: none of the P6 keys", brief(r))
    r = W.ada.c.get("/settings")
    body = jd(r)
    check(r.status_code == 200 and p6_keys <= set(body), "an admin's GET /settings has the P6 keys", brief(r))
    check(body.get("company_key_billed") is False and body.get("dpo_contact") == "" and body.get("public_contact_phone") == "",
          "  unset: company_key_billed false (a JSON boolean), the contacts empty", brief(r))
    r = W.pat.c.put("/settings", json={"company_key_billed": True})
    check(r.status_code == 403, "a plain user's PUT /settings -> 403", brief(r))
    r = W.vera.c.put("/settings", json={"dpo_contact": "x@y.z"})
    check(r.status_code == 403, "see-all staff's PUT /settings -> 403", brief(r))

    for value, want in ((True, True), ("false", False), ("TRUE", True), (False, False)):
        r = W.ada.c.put("/settings", json={"company_key_billed": value})
        got = jd(W.ada.c.get("/settings")).get("company_key_billed")
        check(r.status_code == 200 and jd(r).get("company_key_billed") is want and got is want,
              f"PUT company_key_billed {json.dumps(value)} -> stored {want}", f"{brief(r)}; GET {got!r}")
    for bad in ("maybe", 1, "yes"):
        r = W.ada.c.put("/settings", json={"company_key_billed": bad})
        check(r.status_code == 422, f"PUT company_key_billed {json.dumps(bad)} -> 422", brief(r))
    actions = [e["action"] for e in sql("SELECT action FROM audit_log WHERE action LIKE 'company_key_billed%' ORDER BY id")]
    check(actions == ["company_key_billed_on", "company_key_billed_off", "company_key_billed_on", "company_key_billed_off"],
          "each change of company_key_billed is in the history (who said the company key is billed)", str(actions))

    anon = browser()
    r = anon.get("/privacy/contact")
    check(r.status_code == 200 and jd(r) == {"dpo_contact": "", "public_contact_phone": ""},
          "signed out: GET /privacy/contact -> 200, both empty while unset", brief(r))
    r = W.ada.c.put("/settings", json={"dpo_contact": "  dpo@firm.sg ", "public_contact_phone": "6123\n4567",
                                        "company_name": "Carlink Test"})
    body = jd(r)
    check(r.status_code == 200 and body.get("dpo_contact") == "dpo@firm.sg" and body.get("public_contact_phone") == "6123 4567"
          and body.get("company_name") == "Carlink Test", "the admin saves both contacts (one line, trimmed) and the company name",
          brief(r))
    r = anon.get("/privacy/contact")
    check(r.status_code == 200 and jd(r) == {"dpo_contact": "dpo@firm.sg", "public_contact_phone": "6123 4567"},
          "signed out: GET /privacy/contact -> exactly {dpo_contact, public_contact_phone}", brief(r))
    r = W.pat.c.get("/privacy/contact")
    check(r.status_code == 200 and set(jd(r)) == {"dpo_contact", "public_contact_phone"}, "  signed in too", brief(r))
    r = anon.post("/privacy/contact")
    check(r.status_code == 405, "  GET only (POST -> 405)", brief(r))
    r = W.ada.c.put("/settings", json={"dpo_contact": "x" * (public.MAX_SETTING_TEXT + 1)})
    check(r.status_code == 422, f"a contact over {public.MAX_SETTING_TEXT} characters -> 422", brief(r))
    r = W.ada.c.put("/settings", json={"dpo_contact": "", "public_contact_phone": ""})
    check(r.status_code == 200 and jd(anon.get("/privacy/contact")) == {"dpo_contact": "", "public_contact_phone": ""},
          "an empty value clears a contact", brief(r))


def visibility() -> None:
    W.analytics_before = jd(W.ada.c.get("/analytics/summary?scope=all")).get("total_incidents")
    W.taxonomy_before = (jd(W.ada.c.get("/taxonomy?scope=all")).get("usage") or {}).get("reports_counted")
    shared_phone = f"+659{random.randrange(10 ** 7):07d}"
    rid = seed(photos=2, phone=shared_phone, label="main")
    other = seed(photos=1, phone=shared_phone, label="sibling")
    old = seed(photos=1, submitted=utcnow() - timedelta(days=28), label="old")
    W.main = rid
    s = SEEDED[rid]

    # The bot's own write path, once (the rest are seeded as it leaves them).
    src = _tmp / "bot-photo.jpg"
    src.write_bytes(_photo_bytes("bot"))
    form = public.Form(telegram_user_id="770000001", name="Rosalind Botwright", role="owner", phone="+6581230099",
                       statement="Saved by save_submission.", consent_at=utcnow(), plate="SJB 8762-M",
                       when_where="CTE exit 5", telegram_username="rosabot", photo_count_sent=1)
    saved, saved_ref = public.save_submission(form, [str(src)])
    SEEDED[saved] = {"name": "Rosalind Botwright", "phone": "+6581230099", "ref": saved_ref, "tg": "770000001"}

    for p, who in ((W.ada, "an admin"), (W.vera, "see-all staff")):
        st, body = inbox(p)
        ids = {i.get("id") for i in body.get("items", [])}
        check(st == 200 and {rid, other, old, saved} <= ids, f"{who}: GET /inbox -> 200, the new reports listed",
              f"{st} {len(ids)} item(s)")
    st, body = inbox(W.ada)
    items = body.get("items", [])
    item = next((i for i in items if i.get("id") == rid), {})
    want = {"id", "reference", "submitted_at", "expires_at", "sender", "plate", "photo_count", "statement_preview",
            "withdraw_requested", "owner", "taken_at"}
    check(want <= set(item), "a list item has the brief's fields", f"missing {sorted(want - set(item))}")
    sender = item.get("sender") or {}
    check({"name", "role", "phone_masked", "phone_kind", "telegram_username"} <= set(sender) and "phone" not in sender,
          "  sender: name, role, phone_masked, phone_kind, telegram_username (no full phone)", str(sorted(sender)))
    check(sender.get("phone_masked") == f"+65 •••• {s['phone'][-4:]}", "  the phone masked: +65 •••• 1234",
          repr(sender.get("phone_masked")))
    raw = W.ada.c.get("/inbox").text
    check(s["phone"] not in raw and public.phone_display(s["phone"]) not in raw,
          "  the full phone is nowhere in the list response")
    check(s["tg"] not in raw and "telegram_user_id" not in raw, "  nor the Telegram user id")
    check(item.get("photo_count") == 2 and item.get("plate") == "SBA1234A" and item.get("owner") is None
          and item.get("reference") == s["ref"] and item.get("withdraw_requested") is False,
          "  photo_count 2, plate, reference, no owner, no withdrawal asked", str(item)[:300])
    check(item.get("statement_preview", "").startswith("Seeded statement main"), "  statement_preview", repr(item.get("statement_preview")))
    order = [i.get("id") for i in items]
    check(order.index(old) < order.index(rid), "  oldest first")
    check(next((i for i in items if i.get("id") == saved), {}).get("photo_count") == 1,
          "  the report save_submission wrote is listed with its photo")
    check(body.get("counts", {}).get("new") == len(items) and body.get("counts", {}).get("taken") == 0,
          "counts: {new, taken}", str(body.get("counts")))
    check(body.get("limits") == {"max_open": public.MAX_OPEN, "accepting": True}, "limits: {max_open: 50, accepting: true}",
          str(body.get("limits")))
    r = W.ada.c.get("/inbox?view=bogus")
    check(r.status_code == 422, "GET /inbox?view=bogus -> 422", brief(r))

    r = W.vera.c.get("/inbox/count")
    body = jd(r)
    check(r.status_code == 200 and body.get("new") == len(items) and body.get("expiring_soon") == 1
          and str(body.get("oldest_submitted_at", "")).endswith(("Z", "+00:00")),
          "GET /inbox/count: {new, oldest_submitted_at (UTC), expiring_soon (the 28-day-old one)}", brief(r))

    for p, who in ((W.pat, "a plain user"), (W.tia, "a ticked user who doesn't see all cases")):
        r = p.c.get("/inbox")
        check(r.status_code == 403 and code_of(r) == "not_allowed", f"{who}: GET /inbox -> 403 not_allowed", brief(r))
        r = p.c.get("/inbox/count")
        check(r.status_code == 403, f"{who}: GET /inbox/count -> 403", brief(r))
        r = p.c.get(f"/inbox/{rid}")
        check(r.status_code == 404, f"{who}: GET /inbox/{{id}} -> 404", brief(r))
        r = p.c.get(f"/files/{first_photo_rel(rid)}")
        check(r.status_code == 404, f"{who}: its photo under /files -> 404", brief(r))

    r = W.ada.c.get(f"/files/{first_photo_rel(rid)}")
    check(r.status_code == 200 and r.headers.get("cache-control") == "private, max-age=3600",
          "the admin gets the photo; intake/ files are cached 'private, max-age=3600' (L4)", r.headers.get("cache-control", ""))

    r = W.vera.c.get(f"/inbox/{rid}")
    body = jd(r)
    check(r.status_code == 200 and body.get("state") == "new" and body.get("reference") == s["ref"],
          "see-all staff: GET /inbox/{id} -> 200, state new", brief(r))
    snd = body.get("sender") or {}
    check(snd.get("name") == s["name"] and snd.get("phone") == s["phone"] and snd.get("phone_display") == public.phone_display(s["phone"]),
          "  the sender in full: name, phone (E.164), phone_display", str(snd)[:200])
    check("telegram_user_id" not in json.dumps(body) and s["tg"] not in json.dumps(body), "  never the Telegram user id")
    check(len(body.get("photos") or []) == 2 and all(p.get("thumb_url") for p in body["photos"]), "  its 2 photos, with URLs")
    want = {"id", "reference", "state", "submitted_at", "expires_at", "sender", "plate", "when_where", "statement", "photos",
            "forwarded_count", "withdraw_requested_at", "same_sender", "owner", "taken_at", "taken_by", "permissions"}
    check(want <= set(body), "  the brief's fields", f"missing {sorted(want - set(body))}")
    check([x.get("id") for x in body.get("same_sender") or []] == [other] and body["same_sender"][0].get("state") == "new",
          "  same_sender: the other report from the same phone, {id, reference, state}", str(body.get("same_sender")))
    check(body.get("permissions") == {"take": True, "assign": False, "release": False, "discard": True},
          "  permissions for see-all staff: take, discard", str(body.get("permissions")))
    check(jd(W.ada.c.get(f"/inbox/{rid}")).get("permissions", {}).get("assign") is True, "  an admin may also assign")

    r = W.pat.c.post("/intake")
    need(r.status_code == 200, "(Pat starts an ordinary draft)", brief(r))
    W.pat_draft = jd(r)["id"]
    r = W.ada.c.get(f"/inbox/{W.pat_draft}")
    check(r.status_code == 404 and code_of(r) == "inbox_gone", "GET /inbox/{id} of a report not from the public -> 404 inbox_gone",
          brief(r))
    r = W.ada.c.post(f"/inbox/{W.pat_draft}/take")
    check(r.status_code == 404 and code_of(r) == "inbox_gone", "  and take -> 404 inbox_gone", brief(r))
    check(report_row(W.pat_draft).get("owner_id") == W.pat.id, "  (Pat's draft untouched)")

    shown = [scope for scope in (None, "all", "unassigned", "mine") if rid in list_ids(W.ada, scope)]
    shown += [f"staff {scope}" for scope in (None, "all", "unassigned") if rid in list_ids(W.vera, scope)]
    check(not shown, "the untaken report is in no GET /reports scope (admin, see-all staff)", str(shown))
    check(jd(W.ada.c.get("/analytics/summary?scope=all")).get("total_incidents") == W.analytics_before,
          "analytics unchanged by untaken reports")
    check((jd(W.ada.c.get("/taxonomy?scope=all")).get("usage") or {}).get("reports_counted") == W.taxonomy_before,
          "taxonomy unchanged by untaken reports")

    r = W.ada.c.get(f"/reports/{rid}")
    body = jd(r)
    check(r.status_code == 200 and (body.get("public_submission") or {}).get("reference") == s["ref"],
          "GET /reports/{id}: public_submission with the reference", brief(r))
    check(body.get("permissions", {}).get("edit") is False and body["permissions"].get("delete") is False
          and body["permissions"].get("release") is False, "  permissions: no edit, delete or release in the inbox",
          str(body.get("permissions")))
    r = W.vera.c.get(f"/intake/{rid}")
    body = jd(r)
    check(r.status_code == 200 and (body.get("public_submission") or {}).get("state") == "new" and body.get("owner") is None,
          "GET /intake/{id}: public_submission (state new), no owner (the wizard sends it to the inbox)", brief(r))
    r = W.ada.c.get(f"/reports/{W.pat_draft}")
    check("public_submission" in jd(r) and jd(r)["public_submission"] is None, "  null on a report not from the public", brief(r))
    W.sibling, W.old, W.saved = other, old, saved


def before_take() -> None:
    rid = W.main
    mark = len(CALLS)
    data = jd(W.ada.c.get(f"/intake/{rid}")).get("data") or {}
    r = W.ada.c.put(f"/intake/{rid}/data", json={"data": data})
    check(r.status_code == 403 and code_of(r) == "not_allowed", "an admin's PUT /intake/{id}/data -> 403", brief(r))
    r = W.ada.c.post(f"/intake/{rid}/process", json={"mode": "all"})
    check(r.status_code == 403, "an admin's POST /intake/{id}/process -> 403", brief(r))
    r = W.vera.c.post(f"/intake/{rid}/process", json={"mode": "all"})
    check(r.status_code == 403, "see-all staff's POST /process before taking it -> 403", brief(r))
    r = W.ada.c.post(f"/intake/{rid}/finalize", json={"data": data})
    check(r.status_code == 403, "an admin's POST /intake/{id}/finalize -> 403", brief(r))
    r = W.ada.c.post(f"/intake/{rid}/photos", files={"file": ("x.jpg", _photo_bytes("x"), "image/jpeg")})
    check(r.status_code == 403, "an admin's photo upload -> 403", brief(r))
    # The damage-item routes of the case page's tab 1 (2026-11-04) take the
    # same editable_case gate, so an untaken report is no one's to edit.
    for method, path, kw in (("patch", "/damage-items/0", {"json": {"human_verified": True}}),
                             ("post", "/damage-items", {"json": {"part": "Rear Bumper"}}),
                             ("delete", "/damage-items/0", {})):
        r = W.ada.c.request(method, f"/reports/{rid}{path}", **kw)
        check(r.status_code == 403 and code_of(r) == "not_allowed", f"an admin's {method.upper()} /reports/{{id}}{path} -> 403 not_allowed", brief(r))
    r = W.ada.c.delete(f"/reports/{rid}")
    check(r.status_code == 403 and code_of(r) == "not_allowed" and "Discard" in jd(r).get("detail", ""),
          "an admin's DELETE /reports/{id} -> 403 (use Discard)", brief(r))
    r = W.ada.c.patch(f"/reports/{rid}/owner", json={"owner_id": W.vera.id})
    check(r.status_code == 409 and code_of(r) == "owner_draft", "PATCH /reports/{id}/owner -> 409 owner_draft", brief(r))
    r = W.ada.c.patch(f"/reports/{W.pat_draft}/owner", json={"owner_id": W.vera.id})
    check(r.status_code == 409 and code_of(r) == "owner_draft", "  as for a staff draft", brief(r))
    _real_sleep(0.3)
    check(not calls_since(mark), "no Gemini call was made", describe(calls_since(mark)))
    check(set(held_in(rid)) == set(ALL_FOUR) and report_row(rid).get("owner_id") is None, "still there, untaken")


def take_section() -> None:
    rid, s = W.main, SEEDED[W.main]
    W.sam = staff("Sam", ai_company_key_allowed=True)
    r = W.pat.c.post(f"/inbox/{rid}/take")
    check(r.status_code == 404, "a plain user's take -> 404 (can't see it)", brief(r))
    r = take(W.sam, rid)
    need(r.status_code == 200 and jd(r).get("id") == rid and (jd(r).get("owner") or {}).get("id") == W.sam.id,
         "Sam (see-all staff) takes it -> 200 {id, owner}", brief(r))
    check(report_row(rid).get("owner_id") == W.sam.id and report_row(rid).get("status") == "intake",
          "reports.owner_id is Sam; still a draft")
    row = pub_row(rid)
    check(row.get("taken_by") == W.sam.id and row.get("taken_at") is not None, "public_submissions: taken_by, taken_at")
    taken = [e for e in audit_for(rid) if e["action"] == "taken"]
    check(len(taken) == 1 and s["ref"] in taken[0]["detail"] and taken[0]["user_id"] == W.sam.id,
          "history: 'taken' with the ref, by Sam", str(taken))
    check(not pii_in(str(taken), rid), "  with no name or phone")
    check(rid in list_ids(W.sam, "mine"), "it is in Sam's own cases (GET /reports?scope=mine)")
    check(rid not in inbox_ids(W.ada, "new") and rid in inbox_ids(W.ada, "taken"), "it moves from 'new' to 'taken'")
    item = next((i for i in inbox(W.ada, "taken")[1].get("items", []) if i.get("id") == rid), {})
    check((item.get("owner") or {}).get("id") == W.sam.id and item.get("taken_at"), "  the taken view shows the owner and taken_at",
          str(item)[:200])

    r = take(W.vera, rid)
    check(r.status_code == 409 and code_of(r) == "inbox_taken" and W.sam.name in jd(r).get("detail", ""),
          "a second take -> 409 inbox_taken, 'Taken by Sam Staffer just now'", brief(r))
    r = take(W.ada, rid)
    check(r.status_code == 409 and code_of(r) == "inbox_taken", "an admin's take after that -> 409 inbox_taken", brief(r))

    body = jd(W.sam.c.get(f"/intake/{rid}"))
    sub = body.get("public_submission") or {}
    check(sub.get("state") == "taken" and (sub.get("taken_by") or {}).get("id") == W.sam.id,
          "GET /intake/{id}: public_submission state taken, taken_by Sam", str(sub)[:200])
    check(body.get("permissions", {}).get("release") is True and body["permissions"].get("edit") is True,
          "  Sam may edit and release it", str(body.get("permissions")))
    check(jd(W.vera.c.get(f"/intake/{rid}")).get("permissions", {}).get("release") is False, "  other staff may not release it")
    body = jd(W.vera.c.get(f"/inbox/{rid}"))
    check(body.get("state") == "taken" and (body.get("taken_by") or {}).get("id") == W.sam.id and body.get("taken_at"),
          "GET /inbox/{id}: state taken, taken_by {id, name, ...}, taken_at", str(body)[:200])
    check(body.get("permissions") == {"take": False, "assign": False, "release": False, "discard": False},
          "  other staff can do nothing with it", str(body.get("permissions")))

    print("  two takes at once")
    wins = Counter()
    for i in range(4):
        x = seed(label=f"race{i}")
        a, b = staff(f"Racer{i}a"), staff(f"Racer{i}b")
        ra, rb = race(lambda: take(a, x, a.tab()), lambda: take(b, x, b.tab()))
        codes = sorted([status(ra), status(rb)], key=str)
        winner = a if status(ra) == 200 else b
        loser = rb if status(ra) == 200 else ra
        ok = codes == [200, 409] and code_of(loser) == "inbox_taken" and report_row(x).get("owner_id") == winner.id
        wins["first" if status(ra) == 200 else "second"] += 1
        check(ok, f"round {i + 1}: exactly one take wins, the other 409 inbox_taken", f"{codes}; owner {report_row(x).get('owner_id')}")
        check(len([e for e in audit_for(x) if e["action"] == "taken"]) == 1, "  one 'taken' history entry")
    print(f"    info two takes at once: {dict(wins)}")


def ai_section() -> None:
    rid, s = W.main, SEEDED[W.main]
    need(report_row(rid).get("owner_id") == W.sam.id, "(Sam has taken the main report)")
    set_billed(False)
    mark = len(CALLS)
    r = W.sam.c.post(f"/intake/{rid}/process", json={"mode": "all"})
    check(r.status_code == 409 and code_of(r) == "ai_key_missing" and jd(r).get("detail") == PUBLIC_KEY_ONLY,
          "company_key_billed false, Sam (company tick, no own key) presses Start -> 409 ai_key_missing, the decision's words",
          brief(r))
    _real_sleep(0.3)
    check(not calls_since(mark), "  no Gemini call", describe(calls_since(mark)))

    W.sam.save_key()
    mark = len(CALLS)
    r = W.sam.c.post(f"/intake/{rid}/process", json={"mode": "all"})
    need(r.status_code == 200, "Sam with an own billed key presses Start -> 200", brief(r))
    state = wait_done(W.sam, rid)
    need(state.get("stage") == "ready", "the run finishes: ready", f"{state.get('stage')!r} {state.get('error')!r}")
    calls = calls_since(mark)
    check(calls and all(c["key"] == W.sam.key for c in calls), f"every one of the {len(calls)} calls used Sam's own key",
          describe(calls))
    leaked = [c["purpose"] for c in calls if pii_in(c["text"], rid)]
    check(not leaked, "the sender's name and phone are in no Gemini request", str(leaked))
    check(any(s["statement"] in c["text"] for c in calls if c["purpose"] == "draft"), "  their statement is (the reading step)")
    data = state.get("data") or {}
    check(data.get("reporter_name") == s["name"] and (data.get("vehicle_info") or {}).get("plate_number") == "SBA1234A",
          "after the run the reporter's name and the typed plate are kept",
          f"{data.get('reporter_name')!r} {(data.get('vehicle_info') or {}).get('plate_number')!r}")
    _real_sleep(0.5)  # the run hands back Sam's one AI turn as it ends

    W.sam.drop_key()
    set_billed(True)
    mark = len(CALLS)
    r = W.sam.c.post(f"/intake/{rid}/process", json={"mode": "read"})
    need(r.status_code == 200, "company_key_billed true: Sam (company tick, no own key) presses Retry -> 200", brief(r))
    state = wait_done(W.sam, rid)
    calls = calls_since(mark)
    check(state.get("stage") == "ready" and calls and all(c["key"] == COMPANY_KEY for c in calls),
          "  the run used the company key", describe(calls))
    _real_sleep(0.5)
    set_billed(False)
    r = W.vera.c.post(f"/intake/{rid}/process", json={"mode": "read"})
    check(r.status_code == 403, "other staff's Start on Sam's public draft -> 403", brief(r))


def assign_section() -> None:
    rid = seed(label="assign")
    ref = SEEDED[rid]["ref"]
    r = W.vera.c.post(f"/inbox/{rid}/assign", json={"owner_id": W.tia.id})
    check(r.status_code == 403 and code_of(r) == "not_allowed", "see-all staff's assign -> 403 (admins only)", brief(r))
    r = W.ada.c.post(f"/inbox/{rid}/assign", json={"owner_id": W.bob.id})
    check(r.status_code == 409 and code_of(r) == "owner_unvetted", "assign to a plain account -> 409 owner_unvetted", brief(r))
    r = W.ada.c.post(f"/inbox/{rid}/assign", json={"owner_id": W.dan.id})
    check(r.status_code == 409 and code_of(r) == "owner_inactive", "assign to a disabled account -> 409 owner_inactive", brief(r))
    r = W.ada.c.post(f"/inbox/{rid}/assign", json={"owner_id": "nobody0000"})
    check(r.status_code == 404, "assign to an unknown account -> 404", brief(r))
    r = W.ada.c.post(f"/inbox/{rid}/assign", json={})
    check(r.status_code == 422, "assign without owner_id -> 422", brief(r))
    check(report_row(rid).get("owner_id") is None, "  (still untaken after all of those)")
    r = W.ada.c.post(f"/inbox/{rid}/assign", json={"owner_id": W.tia.id})
    need(r.status_code == 200 and (jd(r).get("owner") or {}).get("id") == W.tia.id,
         "assign to Tia (company-AI tick: vetted) -> 200 {id, owner}", brief(r))
    entry = [e for e in audit_for(rid) if e["action"] == "assigned"]
    check(len(entry) == 1 and "public inbox" in entry[0]["detail"] and ref in entry[0]["detail"],
          "history: 'assigned ... from the public inbox · TG-...'", str(entry))
    check(pub_row(rid).get("taken_by") == W.tia.id, "  taken_by is Tia")
    r = W.ada.c.post(f"/inbox/{rid}/assign", json={"owner_id": W.vera.id})
    check(r.status_code == 409 and code_of(r) == "inbox_taken", "assign again -> 409 inbox_taken (release it first)", brief(r))
    r = W.tia.c.get(f"/inbox/{rid}")
    body = jd(r)
    check(r.status_code == 200 and body.get("state") == "taken" and body.get("same_sender") == []
          and body.get("permissions", {}).get("release") is True,
          "Tia (its owner, not see-all) opens it: state taken, may release, no other reports listed", brief(r))
    r = W.tia.c.get("/inbox")
    check(r.status_code == 403, "  but still can't list the inbox", brief(r))


def release_section() -> None:
    rid = seed(label="release")
    ref = SEEDED[rid]["ref"]
    expires = pub_row(rid).get("expires_at")
    rs = staff("Remy")
    r = W.ada.c.post(f"/inbox/{rid}/release")
    check(r.status_code == 409 and code_of(r) == "inbox_changed", "release of an untaken report -> 409 inbox_changed", brief(r))
    need(take(rs, rid).status_code == 200, "(Remy takes it)")
    r = W.vera.c.post(f"/inbox/{rid}/release")
    check(r.status_code == 403 and code_of(r) == "not_allowed", "other staff's release -> 403", brief(r))
    r = W.ada.c.post(f"/inbox/{rid}/release")
    need(r.status_code == 200 and jd(r) == {"id": rid, "owner": None}, "an admin's release -> 200 {id, owner: null}", brief(r))
    check(report_row(rid).get("owner_id") is None and pub_row(rid).get("taken_by") is None and pub_row(rid).get("taken_at") is None,
          "  no owner; taken_by and taken_at cleared")
    check(rid in inbox_ids(W.ada, "new"), "  back in the 'new' view")
    entry = [e for e in audit_for(rid) if e["action"] == "returned_to_inbox"]
    check(len(entry) == 1 and entry[0]["detail"] == ref, "history: 'returned_to_inbox' with the ref", str(entry))
    check(pub_row(rid).get("expires_at") == expires, "  its 30 days are unchanged (expires_at as it was)")
    r = W.ada.c.post(f"/inbox/{rid}/release")
    check(r.status_code == 409 and code_of(r) == "inbox_changed", "release again -> 409 inbox_changed", brief(r))
    r = rs.c.put(f"/intake/{rid}/data", json={"data": {"description": "", "category": ["Other"]}})
    check(r.status_code == 403, "Remy can't autosave it any more -> 403", brief(r))
    r = W.ada.c.post(f"/inbox/{rid}/assign", json={"owner_id": W.vera.id})
    check(r.status_code == 200 and report_row(rid).get("owner_id") == W.vera.id, "release, then assign to Vera -> 200", brief(r))
    r = W.vera.c.post(f"/inbox/{rid}/release")
    check(r.status_code == 200, "Vera releases her own -> 200", brief(r))


def discard_section() -> None:
    rid = seed(photos=2, label="discard")
    ref, rel = SEEDED[rid]["ref"], first_photo_rel(rid)
    for body, why in (({}, "no reason"), ({"reason": "bogus"}, "an unknown reason"),
                      ({"reason": "other", "note": "x" * (public.MAX_DISCARD_NOTE + 1)}, "a note over 120 characters")):
        r = W.vera.c.post(f"/inbox/{rid}/discard", json=body)
        check(r.status_code == 422, f"discard with {why} -> 422", brief(r))
    r = W.pat.c.post(f"/inbox/{rid}/discard", json={"reason": "test"})
    check(r.status_code == 404, "a plain user's discard -> 404", brief(r))
    r = W.vera.c.post(f"/inbox/{rid}/discard", json={"reason": "other", "note": "  wrong  number\nof photos "})
    need(r.status_code == 200 and jd(r) == {"deleted": rid}, "see-all staff discard it (Other, a note) -> 200 {deleted}", brief(r))
    check(not still_held(rid), "the 4 tables and the folder are gone", str(still_held(rid)))
    r = W.ada.c.get(f"/files/{rel}")
    check(r.status_code == 404, "its photo under /files -> 404", brief(r))
    entry = [e for e in audit_for(rid) if e["action"] == "discarded"]
    check(len(entry) == 1 and entry[0]["detail"] == f"{ref} · Other · wrong number of photos",
          "history: 'discarded' · 'TG-... · Other · wrong number of photos'", str(entry))
    check(not pii_in(str(audit_for(rid)), rid), "  no name or phone in any of its history")
    check(not sql("SELECT * FROM public_blocks WHERE telegram_user_id=:t", t=SEEDED[rid]["tg"]), "  nobody blocked (not spam)")
    r = W.vera.c.post(f"/inbox/{rid}/discard", json={"reason": "test"})
    check(r.status_code == 404, "discard it again -> 404", brief(r))

    dup = seed(label="dupe")
    r = W.vera.c.post(f"/inbox/{dup}/discard", json={"reason": "duplicate", "duplicate_of": SEEDED[W.sibling]["ref"]})
    check(r.status_code == 200 and f"Duplicate of {SEEDED[W.sibling]['ref']}" in str(audit_for(dup)),
          "discard as a duplicate: 'Duplicate of TG-...' in the history", brief(r))

    print("  taken: only its owner or an admin")
    td = seed(label="takendiscard")
    dee = staff("Dee")
    need(take(dee, td).status_code == 200, "(Dee takes one)")
    r = W.vera.c.post(f"/inbox/{td}/discard", json={"reason": "test"})
    check(r.status_code == 403 and code_of(r) == "not_allowed", "other staff's discard -> 403", brief(r))
    r = dee.c.post(f"/inbox/{td}/discard", json={"reason": "test"})
    check(r.status_code == 200 and not still_held(td), "Dee (its owner) discards it -> 200, all gone", brief(r))
    td2 = seed(label="admindiscard")
    need(take(dee, td2).status_code == 200, "(Dee takes another)")
    r = W.ada.c.post(f"/inbox/{td2}/discard", json={"reason": "not_vehicle"})
    check(r.status_code == 200 and "Not a vehicle report" in str(audit_for(td2)), "an admin discards Dee's -> 200", brief(r))

    print("  spam blocks the sender")
    sp = seed(label="spam")
    tg = SEEDED[sp]["tg"]
    r = W.vera.c.post(f"/inbox/{sp}/discard", json={"reason": "spam"})
    need(r.status_code == 200, "discard as Spam (block_sender left out: on) -> 200", brief(r))
    blocks = sql("SELECT * FROM public_blocks WHERE telegram_user_id = :t", t=tg)
    check(len(blocks) == 1 and blocks[0]["reference"] == SEEDED[sp]["ref"] and blocks[0]["blocked_by"] == W.vera.id,
          "public_blocks: the sender's Telegram id, with the ref and who blocked it", str(blocks))
    if blocks:
        until = datetime.fromisoformat(str(blocks[0]["until"]))
        at = datetime.fromisoformat(str(blocks[0]["blocked_at"]))
        check(abs((until - at) - timedelta(days=90)) < timedelta(minutes=1), "  for 90 days", f"{at} -> {until}")
    db = SessionLocal()
    try:
        check(public.is_blocked(db, tg), "  public.is_blocked says so")
        try:
            public.check_can_accept(db, tg)
            check(False, "  and the bot's check refuses them (blocked)")
        except public.PublicRefused as exc:
            check(exc.code == "blocked", "  and the bot's check refuses them (blocked)", exc.code)
    finally:
        db.close()
    check("Spam" in str(audit_for(sp)) and SEEDED[sp]["ref"] in str(audit_for(sp)), "  history: the ref and 'Spam'")
    nb = seed(label="spamnoblock")
    r = W.vera.c.post(f"/inbox/{nb}/discard", json={"reason": "spam", "block_sender": False})
    check(r.status_code == 200 and not sql("SELECT * FROM public_blocks WHERE telegram_user_id=:t", t=SEEDED[nb]["tg"]),
          "Spam with block_sender false -> no block", brief(r))
    nt = seed(label="testblock")
    r = W.vera.c.post(f"/inbox/{nt}/discard", json={"reason": "test", "block_sender": True})
    check(r.status_code == 200 and not sql("SELECT * FROM public_blocks WHERE telegram_user_id=:t", t=SEEDED[nt]["tg"]),
          "block_sender with another reason -> no block (spam only)", brief(r))

    print("  discard and take at once")
    wins = Counter()
    for i in range(4):
        x = seed(label=f"dvt{i}")
        a, b = staff(f"Disc{i}"), staff(f"Took{i}")
        rd, rt = race(lambda: a.tab().post(f"/inbox/{x}/discard", json={"reason": "test"}), lambda: take(b, x, b.tab()))
        sd, st = status(rd), status(rt)
        left = held_in(x)
        if sd == 200 and st != 200:
            wins["discard"] += 1
            ok = st in (404, 409) and not left
        elif st == 200 and sd != 200:
            wins["take"] += 1
            ok = sd == 409 and code_of(rd) == "inbox_changed" and set(left) == set(ALL_FOUR) and report_row(x).get("owner_id") == b.id
        else:
            ok = False
        check(ok, f"round {i + 1}: discard {sd}, take {st} -> exactly one wins, and the rows agree", f"left {left}")
    print(f"    info discard vs take: {dict(wins)}")


def delete_section() -> None:
    rid = seed(photos=1, label="deleteme")
    ref = SEEDED[rid]["ref"]
    owner = staff("Otto")
    need(take(owner, rid).status_code == 200, "(Otto takes one)")
    r = W.vera.c.delete(f"/reports/{rid}")
    check(r.status_code == 403, "other staff's DELETE /reports/{id} of Otto's public draft -> 403", brief(r))
    r = owner.c.delete(f"/reports/{rid}")
    need(r.status_code == 200 and jd(r) == {"deleted": rid}, "Otto deletes his taken public draft -> 200", brief(r))
    check(not still_held(rid), "  the 4 tables and the folder are gone", str(still_held(rid)))
    entry = [e for e in audit_for(rid) if e["action"] == "deleted"]
    check(len(entry) == 1 and entry[0]["detail"] == f"{ref} · Unfinished intake", "  history: 'deleted' · 'TG-... · Unfinished intake'",
          str(entry))
    check(not pii_in(str(audit_for(rid)), rid), "  no name or phone in its history")
    r = W.ada.c.delete(f"/reports/{W.pat_draft}")
    check(r.status_code == 200 and not held_in(W.pat_draft), "an ordinary draft still deletes as before", brief(r))


def finalize_section() -> None:
    rid = seed(photos=2, label="finalize")
    s = SEEDED[rid]
    fin = staff("Finn", ai_company_key_allowed=True)
    need(take(fin, rid).status_code == 200, "(Finn takes one)")
    data = jd(fin.c.get(f"/intake/{rid}")).get("data") or {}
    need(data, "(Finn reads the draft)")
    data["reporter_name"] = "Zebulon <b>x</b> Quayle"
    # The firm's report prints no reporter name; the cover page's "Requested by"
    # (typed by staff) is the printed field, so it carries the same hostile text.
    data["cover_page"] = {**(data.get("cover_page") or {}), "requested_by": "Zebulon <b>x</b> Quayle"}
    mark = len(CAPTURED_HTML)
    r = fin.c.post(f"/intake/{rid}/finalize", json={"data": data})
    need(r.status_code == 200, "Finn finishes it -> 200", brief(r))
    row, rep = pub_row(rid), report_row(rid)
    check(rep.get("status") == "confirmed" and rep.get("channel") == PUBLIC_CHANNEL and rep.get("reporter_chat_id") == "public",
          "filed: status confirmed, channel telegram_public, reporter_chat_id 'public'", str(rep))
    check(row.get("filed_at") is not None and all(row.get(k) is None for k in ("telegram_user_id", "telegram_username", "telegram_name")),
          "public_submissions: filed_at set, telegram_* cleared", str({k: row.get(k) for k in ("filed_at", "telegram_user_id")}))
    check(row.get("submitter_name") == s["name"] and row.get("submitter_phone") == s["phone"], "  the submitter_* record is kept")
    fz = [e for e in audit_for(rid) if e["action"] == "finalized"]
    check(len(fz) == 1 and fz[0]["detail"] == s["ref"], "history: 'finalized' with the ref", str(fz))
    if len(CAPTURED_HTML) == mark:
        fin.c.get(f"/reports/{rid}/download")
    page = CAPTURED_HTML[-1] if len(CAPTURED_HTML) > mark else ""
    check(page and "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt;" in page, "the name is escaped in the PDF HTML")

    rows = W.ada.c.get("/reports?scope=all").json()
    mine = next((x for x in rows if x.get("id") == rid), {})
    check(mine.get("public_reference") == s["ref"], "GET /reports rows: public_reference", str(mine.get("public_reference")))
    other = next((x for x in rows if x.get("channel") != PUBLIC_CHANNEL), None)
    check(other is None or other.get("public_reference") is None, "  null on other cases")
    body = jd(fin.c.get(f"/reports/{rid}"))
    sub = body.get("public_submission") or {}
    check(sub.get("state") == "filed" and sub.get("filed_at") and (sub.get("sender") or {}).get("telegram_username") is None,
          "GET /reports/{id}: public_submission state filed, filed_at, no Telegram details", str(sub)[:200])
    check(any(h.get("action") == "taken" for h in body.get("history") or []), "  its history shows 'taken'")
    r = fin.c.get(f"/inbox/{rid}")
    check(r.status_code == 200 and jd(r).get("state") == "filed", "GET /inbox/{id} of a filed one: state filed", brief(r))
    for action, kw in (("take", {}), ("release", {}), ("discard", {"json": {"reason": "test"}})):
        r = W.ada.c.post(f"/inbox/{rid}/{action}", **kw)
        check(r.status_code == 409 and code_of(r) == "inbox_filed", f"  {action} -> 409 inbox_filed", brief(r))

    print("  annex AI on the filed public case")
    set_billed(False)
    mark = len(CALLS)
    r = fin.c.post(f"/reports/{rid}/annex-suggestions/ai", json={"parts": ["Rear Bumper"]})
    check(r.status_code == 409 and code_of(r) == "ai_key_missing" and jd(r).get("detail") == PUBLIC_KEY_ONLY,
          "company_key_billed false: Finn (company tick only) -> 409 ai_key_missing", brief(r))
    check(not calls_since(mark), "  no Gemini call", describe(calls_since(mark)))
    set_billed(True)
    mark = len(CALLS)
    r = fin.c.post(f"/reports/{rid}/annex-suggestions/ai", json={"parts": ["Rear Bumper"]})
    check(r.status_code == 200 and calls_since(mark) and all(c["key"] == COMPANY_KEY for c in calls_since(mark)),
          "company_key_billed true: -> 200 on the company key", f"{brief(r)}; {describe(calls_since(mark))}")
    set_billed(False)

    r = W.ada.c.delete(f"/reports/{rid}")
    check(r.status_code == 200 and not held_in(rid), "an admin deletes the filed public case -> 200, its submission row too",
          f"{brief(r)}; {held_in(rid)}")
    entry = [e for e in audit_for(rid) if e["action"] == "deleted"]
    check(len(entry) == 1 and entry[0]["detail"].startswith(s["ref"]), "  history: 'deleted' with the ref", str(entry))


def _analyze(p: Person, rid: str | None, photos: int = 1):
    """POST /reports/analyze-photos as the dashboard sends it: the edit form
    with report_id (Analyze on new photos, Re-analyze on the saved ones),
    New Report without."""
    form = {"description": "Scraped in a carpark."}
    if rid is not None:
        form["report_id"] = rid
    files = [("photos", (f"p{i}.jpg", _photo_bytes(f"analyze-{i}"), "image/jpeg")) for i in range(photos)]
    return p.c.post("/reports/analyze-photos", data=form, files=files)


def _temp_photos(p: Person) -> list[str]:
    """This person's uploads waiting in storage/tmp (main._temp_prefix)."""
    return sorted(x.name for x in tmp_dir().glob(f"u{p.id}-*"))


def _manual_case(owner_id: str) -> str:
    """A filed case typed in on the dashboard: not from the public."""
    db = SessionLocal()
    try:
        data = SecurityIncidentDraft(description="Rear-ended.", category=["Vehicle Collision or Damage"]).model_dump()
        report = Report(channel="manual", reporter_chat_id="dashboard", status="confirmed", owner_id=owner_id, data=data)
        db.add(report)
        db.commit()
        return report.id
    finally:
        db.close()


def analyze_section() -> None:
    """The edit form's AI on a case the public sent (security review): the
    same billing rule as Start and annex AI, for new photos and Re-analyze."""
    rid = seed(photos=1, label="analyze")
    gil = staff("Gil", ai_company_key_allowed=True)
    need(take(gil, rid).status_code == 200, "(Gil takes one)")
    data = jd(gil.c.get(f"/intake/{rid}")).get("data") or {}
    need(data, "(Gil reads the draft)")
    r = gil.c.post(f"/intake/{rid}/finalize", json={"data": data})
    need(r.status_code == 200 and report_row(rid).get("channel") == PUBLIC_CHANNEL, "(Gil files it: a filed public case)",
         brief(r))

    set_billed(False)
    before = _temp_photos(gil)
    mark = len(CALLS)
    r = _analyze(gil, rid, photos=2)
    check(r.status_code == 409 and code_of(r) == "ai_key_missing" and jd(r).get("detail") == PUBLIC_KEY_ONLY,
          "company_key_billed false: Gil (company tick, no own key) analyzes photos for the filed public case -> 409 "
          "ai_key_missing, the decision's words", brief(r))
    check(not calls_since(mark), "  no Gemini call", describe(calls_since(mark)))
    check(_temp_photos(gil) == before, "  no photo kept", str(_temp_photos(gil)))

    gil.save_key()
    mark = len(CALLS)
    r = _analyze(gil, rid)
    calls = calls_since(mark)
    check(r.status_code == 200 and "draft" in jd(r) and calls and all(c["key"] == gil.key for c in calls),
          "with an own billed key -> 200, every call on Gil's own key", f"{brief(r)}; {describe(calls)}")
    gil.drop_key()

    set_billed(True)
    mark = len(CALLS)
    r = _analyze(gil, rid)
    calls = calls_since(mark)
    check(r.status_code == 200 and calls and all(c["key"] == COMPANY_KEY for c in calls),
          "company_key_billed true: Gil (company tick, no own key) -> 200 on the company key", f"{brief(r)}; {describe(calls)}")
    set_billed(False)

    print("  a case not from the public, and no report_id: as before")
    case = _manual_case(gil.id)
    mark = len(CALLS)
    r = _analyze(gil, case)
    calls = calls_since(mark)
    check(r.status_code == 200 and calls and all(c["key"] == COMPANY_KEY for c in calls),
          "company_key_billed false, Gil's own (manual) case -> 200 on the company key", f"{brief(r)}; {describe(calls)}")
    mark = len(CALLS)
    r = _analyze(gil, None)
    calls = calls_since(mark)
    check(r.status_code == 200 and calls and all(c["key"] == COMPANY_KEY for c in calls) and jd(r).get("temp_photo_paths"),
          "no report_id (New Report) -> 200 on the company key, the photos kept for the save", f"{brief(r)}; {describe(calls)}")

    print("  a case they can't see, or can't change")
    untaken = seed(label="analyzeinbox")
    before = _temp_photos(W.pat)
    mark = len(CALLS)
    r = _analyze(W.pat, rid)
    check(r.status_code == 404 and jd(r).get("detail") == "Case not found.",
          "a plain user (no AI either) with Gil's case -> 404, before any AI check", brief(r))
    check(_temp_photos(W.pat) == before, "  no photo kept")
    r = _analyze(gil, "nosuchcase00")
    check(r.status_code == 404 and jd(r).get("detail") == "Case not found.", "a made-up report_id -> 404", brief(r))
    for p, who in ((W.vera, "see-all staff"), (W.ada, "an admin")):
        r = _analyze(p, untaken)
        check(r.status_code == 403 and code_of(r) == "not_allowed",
              f"{who} with an untaken public report -> 403 not_allowed (no AI on one in the inbox)", brief(r))
    _real_sleep(0.3)
    check(not calls_since(mark), "  no Gemini call for any of those", describe(calls_since(mark)))
    r = W.vera.c.post(f"/inbox/{untaken}/discard", json={"reason": "test"})
    need(r.status_code == 200, "(the untaken one is discarded again)", brief(r))


def caps_section() -> None:
    ids = [seed(photos=0, label=f"cap{i}") for i in range(30)]
    r = W.pat.c.post("/intake")
    check(r.status_code == 200, "30 untaken public reports don't block a plain user's POST /intake (H2)", brief(r))
    r = W.bob.c.post("/intake")
    check(r.status_code == 200, "  nor another's", brief(r))
    cara = staff("Cara")
    for _ in range(5):
        need(cara.c.post("/intake").status_code == 200, "(Cara starts a draft)")
    r = take(cara, ids[0])
    check(r.status_code == 409 and code_of(r) == "draft_limit", "Cara with 5 drafts takes one -> 409 draft_limit", brief(r))
    check(report_row(ids[0]).get("owner_id") is None and pub_row(ids[0]).get("taken_by") is None, "  it stays untaken")
    r = W.ada.c.post(f"/inbox/{ids[0]}/assign", json={"owner_id": cara.id})
    check(r.status_code == 409 and code_of(r) == "draft_limit" and cara.name in jd(r).get("detail", ""),
          "assigning it to Cara -> 409 draft_limit, naming her", brief(r))
    st, body = inbox(W.ada)
    check(body.get("limits", {}).get("accepting") is (body.get("counts", {}).get("new", 0) < public.MAX_OPEN),
          "limits.accepting follows the 50-report cap", str(body.get("limits")))
    for rid in ids:
        db = SessionLocal()
        try:
            if public.delete_submission(db, rid, seen_owner=None, actor=None, action="discarded", detail="cleanup"):
                db.commit()
        finally:
            db.close()

    print("  a legacy draft with a NULL channel")
    need(LEGACY_TABLE, "(the reports table allows a NULL channel)")
    rid = gen_id()
    blank = json.dumps(SecurityIncidentDraft(description="", category=["Vehicle Collision or Damage"]).model_dump())
    stamp = naive(utcnow()).strftime("%Y-%m-%d %H:%M:%S.%f")
    sql_write("INSERT INTO reports (id, type, status, channel, reporter_chat_id, data, photo_paths, created_at, owner_id) "
              "VALUES (:id, 'security_incident', 'intake', NULL, 'dashboard', :data, '[]', :at, NULL)", id=rid, data=blank, at=stamp)
    sql_write("INSERT INTO intake_sessions (report_id, stage, progress_done, progress_total, reporter_notes, read_photo_ids, "
              "updated_at) VALUES (:id, 'uploading', 0, 0, '', '[]', :at)", id=rid, at=stamp)
    check(rid in list_ids(W.ada, "all") and rid in list_ids(W.ada, "unassigned") and rid in list_ids(W.vera, "unassigned"),
          "it still lists (admin all and unassigned, see-all staff unassigned)")
    r = W.ada.c.get(f"/reports/{rid}")
    check(r.status_code == 200 and jd(r).get("public_submission") is None, "  GET /reports/{id} -> 200, no public_submission",
          brief(r))
    r = W.ada.c.get(f"/inbox/{rid}")
    check(r.status_code == 404 and code_of(r) == "inbox_gone", "  GET /inbox/{id} -> 404 inbox_gone", brief(r))
    r = W.ada.c.delete(f"/reports/{rid}")
    check(r.status_code == 200, "  and an admin can still delete it", brief(r))


def sweep_section() -> None:
    now = utcnow()
    day29 = seed(submitted=now - timedelta(days=29), label="day29")
    day30 = seed(submitted=now - timedelta(days=30, minutes=5), label="day30")
    day25 = seed(submitted=now - timedelta(days=25), label="day25")
    need(take(W.vera, day25).status_code == 200, "(Vera takes the 25-day-old one)")
    out = cleanup.sweep(now=now)
    for p in PEOPLE:
        p.sign_in()
    check(set(held_in(day29)) == set(ALL_FOUR) and intake_folder(day29).exists(), "day 29: kept (not the 7-day idle rule)",
          str(still_held(day29)))
    check(not still_held(day30), "day 30: deleted, files and all 4 tables", f"{still_held(day30)}; {out}")
    expired = [e for e in audit_for(day30) if e["action"] == "expired"]
    check(len(expired) == 1 and SEEDED[day30]["ref"] in expired[0]["detail"] and expired[0]["user_id"] is None
          and not pii_in(str(expired), day30), "  one 'expired' entry with the ref, no account, no name or phone", str(expired))
    check(set(held_in(day25)) == set(ALL_FOUR), "taken on day 25: kept (the take touched the session)", str(still_held(day25)))
    cleanup.sweep(now=now + timedelta(days=7, hours=1))
    for p in PEOPLE:
        p.sign_in()
    check(not still_held(day25), "  removed 7 days after the take, idle", str(still_held(day25)))
    check(not still_held(day29), "the day-29 one goes once its 30 days are up", str(still_held(day29)))


def history_wording() -> None:
    rid = seed(label="history")
    db = SessionLocal()
    try:
        history = report_history(db, db.get(Report, rid))
    finally:
        db.close()
    check(history and history[0]["action"] == "created" and history[0]["detail"] == "Sent by the public through the Telegram bot",
          "a public report with no 'created' entry: 'Sent by the public through the Telegram bot'", str(history[:1]))


def errors() -> None:
    check(not STATUS_500, "no API response was a 500", "; ".join(STATUS_500[:4]))
    check(not NET_REFUSED, "nothing tried to reach an outside host", str(sorted(set(NET_REFUSED)))[:200])
    found = [f"{name}: {msg[:120]!r}" for rid in SEEDED for lvl, name, msg in LOG_RECORDS
             if lvl >= logging.INFO and pii_in(msg, rid)]
    check(not found, "no log line (INFO and up) holds a sender's name or phone", "; ".join(found[:3]))
    entries = sql("SELECT report_id, action, detail, user_name FROM audit_log")
    bad = [f"{e['action']}: {e['detail']}" for e in entries for rid in SEEDED
           if pii_in(f"{e['action']} {e['detail']} {e['user_name']}", rid)]
    check(not bad, f"no history entry (of {len(entries)}) holds a sender's name or phone", "; ".join(bad[:3]))


def main() -> int:
    for name, fn in (
        ("setup", setup),
        ("settings and GET /privacy/contact", settings_section),
        ("1. who sees the inbox; the list, the count, the report", visibility),
        ("2. before anyone takes it", before_take),
        ("3. take", take_section),
        ("3b. Start on a public draft: the billing rule", ai_section),
        ("4. assign", assign_section),
        ("6. release, then assign", release_section),
        ("5. discard, spam block", discard_section),
        ("DELETE /reports/{id} of a public draft", delete_section),
        ("9. finalize; annex AI on the filed case", finalize_section),
        ("9b. analyze-photos for one case (report_id): the billing rule", analyze_section),
        ("7. caps; a NULL-channel draft", caps_section),
        ("8. sweep", sweep_section),
        ("history wording", history_wording),
        ("errors and leaks", errors),
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
    print(f"\ntest_public_inbox: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  [{section}] {label}")
    if FAILURES:
        return 1
    print("\nALL PUBLIC INBOX CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
