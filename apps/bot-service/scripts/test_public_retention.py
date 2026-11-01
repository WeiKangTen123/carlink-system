"""Independent retention and race tests for release P6, "public reports
through the Telegram bot" (agent P6-TEST).

    python scripts/test_public_retention.py

Written from the spec, not from the code under test:
plans/2026-10-30-public-reporter/p6_build_brief.md (§1 State, §2.1 data
model, §2.2 conditional transitions, §2.3 API, §2.6 retention, §2.8 TEST,
§3 H2/H3/M4/L8) and p6_decisions.md (company_key_billed false by default:
public reports run only on the presser's own billed key).

What it checks:
- the sweep (intake/cleanup.sweep(now=...)): an untaken public report is
  kept on day 29 and deleted after day 30, files and all 4 tables
  (reports, intake_sessions, intake_photos, public_submissions), with an
  "expired" history entry holding the ref and no name or phone; the 7-day
  rule still removes ordinary idle drafts;
- a report taken on day 25 is kept at day 30, then follows the 7-day idle
  rule from the take; one released after its 30 days goes at the next sweep;
- races, both as interleaved sessions (each order) and as two threads
  started together: take vs sweep, take vs withdraw, discard vs finalize,
  release while the AI runs;
- 30 untaken public reports don't block a plain user's POST /intake (H2);
- untaken reports are in no GET /reports scope, analytics or taxonomy, while
  legacy rows with a NULL channel still show (the clause is NULL-safe).

Public reports are seeded straight into the database as brief §2.1
describes them (Report + IntakeSession + IntakePhoto files + PublicSubmission),
so this test doesn't need the bot. The database's reports table is made
first with a nullable channel, as a database from before the column was
ever filled could have it; everything else is made by the app's own
start-up.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database; Gemini is a fake; the PDF renderer writes a stub; every outside
connection is refused. Failures are assertion lines grouped by section;
until P6-CORE and P6-API are finished most sections fail: that is expected.
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
_tmp = Path(tempfile.mkdtemp(prefix="public-retention-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
SECRETS = (_tmp / "secrets").resolve()
DB_FILE = (_tmp / "test.db").resolve()


def make_key(label: str) -> str:
    return ("AIzaSy" + label + "x" * 40)[:35] + hashlib.md5(label.encode()).hexdigest()[:4]


ENV_COMPANY_KEY = make_key("CompanyEnv")
os.environ.update({
    "STORAGE_DIR": str(STORAGE),
    "SECRETS_DIR": str(SECRETS),
    "DATABASE_URL": f"sqlite:///{DB_FILE.as_posix()}",
    "GEMINI_API_KEY": ENV_COMPANY_KEY,
    "RUN_BACKGROUND_CLEANUP": "false",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_ALLOWED_IDS": "",
    "PUBLIC_BASE_URL": "https://carlink-p6.example",
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
FAKE = SimpleNamespace(delay=0.0)  # seconds each Gemini call takes (to hold a run open)

DRAFT_ANSWER = {
    "description": "Insured vehicle was rear-ended.",
    "category": ["Vehicle Collision or Damage"],
    "damaged_parts": ["Rear Bumper"],
    "damage_summary": [{"part": "Rear Bumper", "severity": "Moderate", "ai_confidence": "High"}],
    "vehicle_info": {"make": "Honda", "model": "Vezel"},
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
            {"label": label, "kind": "damage", "area": "rear", "quality": "good", "shows_damage": True,
             "part": "Rear Bumper", "note": f"photo {label}"} for label in labels]})
    if purpose == "draft":
        return json.dumps(DRAFT_ANSWER)
    return "{}"


class _FakeInteractions:
    def __init__(self, client):
        self.client = client

    def create(self, **kw):
        purpose = _purpose(kw)
        with _fake_lock:
            CALLS.append({"key": self.client.key, "purpose": purpose, "at": time.monotonic()})
        if FAKE.delay:
            _real_sleep(FAKE.delay)
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
# Made before the app's start-up (create_all leaves an existing table alone),
# from the model's own DDL with only "channel" relaxed: a database whose
# rows may have no channel, which the brief's clauses must treat NULL-safely.
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

import app.rendering.renderer as renderer  # noqa: E402
from app.api.main import app  # noqa: E402
from app.auth.core import SESSION_COOKIE, create_session  # noqa: E402
from app.intake import cleanup  # noqa: E402
from app.reports.db import SessionLocal, engine  # noqa: E402
from app.reports.models import IntakePhoto, IntakeSession, Report, User, gen_id  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402
from app.storage.files import generate_preview, generate_thumbnail  # noqa: E402

try:
    from app.intake import public  # noqa: E402
except Exception as _exc:  # reported in [data]
    public = None
    PUBLIC_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"
else:
    PUBLIC_IMPORT_ERROR = ""

try:
    import app.api.ai_keys as ai_keys_api  # noqa: E402
except Exception:  # pragma: no cover
    ai_keys_api = None

# ================================================================ harness
BASE = "https://carlink.test"
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PUBLIC_CHANNEL = "telegram_public"
CONSENT_VERSION = "2026-10-30"
PASSED = [0]
FAILURES: list[tuple[str, str]] = []
INFO: list[str] = []
_section = {"name": "setup"}
STATUS_500: list[str] = []
KEY_LABEL: dict[str, str] = {ENV_COMPANY_KEY: "the company key"}
PEOPLE: list["Person"] = []
SEEDED: dict[str, dict] = {}  # report id -> what was seeded (name, phone, ref, tg)
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
    return mask(f"{r.status_code} {(r.text or '').replace(chr(10), ' ')[:240]}")


def code_of(r) -> str | None:
    body = jd(r)
    code = body.get("code")
    if code is None and isinstance(body.get("detail"), dict):
        code = body["detail"].get("code")
    return code


def route_missing(r) -> bool:
    """FastAPI's own answer for a route that isn't there (not built yet)."""
    return getattr(r, "status_code", None) in (404, 405) and jd(r).get("detail") in ("Not Found", "Method Not Allowed")


def turned_away(r, statuses=(404, 409)) -> bool:
    """A real refusal from the app (gone / conflict), not a missing route."""
    return getattr(r, "status_code", None) in statuses and not route_missing(r)


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


def table_exists(name: str) -> bool:
    return bool(sql("SELECT name FROM sqlite_master WHERE type='table' AND name=:n", n=name))


def intake_folder(rid: str) -> Path:
    return STORAGE / "intake" / rid


def held_in(rid: str) -> list[str]:
    """The tables that still hold a row for this report."""
    out = []
    for table, col in (("reports", "id"), ("intake_sessions", "report_id"), ("intake_photos", "report_id"),
                       ("public_submissions", "report_id")):
        if table_exists(table) and sql(f"SELECT COUNT(*) AS n FROM {table} WHERE {col} = :r", r=rid)[0]["n"]:
            out.append(table)
    return out


def still_held(rid: str) -> list[str]:
    return held_in(rid) + ([f"intake/{rid}/ folder"] if intake_folder(rid).exists() else [])


ALL_FOUR = ["reports", "intake_sessions", "intake_photos", "public_submissions"]


def pub_row(rid: str) -> dict:
    if not table_exists("public_submissions"):
        return {}
    rows = sql("SELECT * FROM public_submissions WHERE report_id = :r", r=rid)
    return rows[0] if rows else {}


def report_row(rid: str) -> dict:
    rows = sql("SELECT id, status, channel, owner_id FROM reports WHERE id = :r", r=rid)
    return rows[0] if rows else {}


def audit_for(rid: str) -> list[dict]:
    return sql("SELECT action, detail, user_id, user_name FROM audit_log WHERE report_id = :r ORDER BY id", r=rid)


def pii_in(textblob: str, rid: str) -> list[str]:
    s = SEEDED.get(rid) or {}
    found = []
    for x in (s.get("name"), *(s.get("name") or "").split(), s.get("phone"), (s.get("phone") or "")[-8:]):
        if x and len(x) >= 4 and x in (textblob or ""):
            found.append(x)
    return found


# ---------------------------------------------------------------- people
class Person:
    def __init__(self, label: str, *, role: str = "user", **ticks):
        self.label = label
        self.key = None
        db = SessionLocal()
        try:
            user = User(email=f"{label.lower()}-{gen_id()}@carlink-p6.test", username=f"{label.lower()}{gen_id()[:5]}",
                        name=f"{label} Person", role=role, password_hash="!", must_change_password=False, **ticks)
            db.add(user)
            db.commit()
            self.id = user.id
        finally:
            db.close()
        self.c = None
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
        if ai_keys_api is not None and hasattr(ai_keys_api, "reset_limits"):
            ai_keys_api.reset_limits()
        key = make_key(self.label + "Own")
        KEY_LABEL[key] = f"{self.label}'s own key"
        r = self.c.put("/auth/me/ai-key", json={"api_key": key, "billing_confirmed": True})
        need(r.status_code == 200, f"(setup) {self.label} saves an own AI key, billing confirmed", brief(r))
        self.key = key


def staff(label: str, *, own_key: bool = False) -> Person:
    """See-all staff (may take from the inbox)."""
    p = Person(label, sees_all_cases=True)
    if own_key:
        p.save_key()
    return p


def sweep_at(now: datetime) -> dict:
    """One cleanup pass at `now`. A pass in the future also ends every
    sign-in session, so everyone signs in again afterwards."""
    out = cleanup.sweep(now=now)
    if now > utcnow() + timedelta(hours=1):
        for p in PEOPLE:
            p.sign_in()
    return out if isinstance(out, dict) else {}


# ---------------------------------------------------------------- seeding (brief §2.1)
def _pub_model():
    try:
        from app.reports.models import PublicSubmission
        return PublicSubmission
    except Exception:
        return None


def _photo_bytes(seed: str) -> bytes:
    h = hashlib.md5(seed.encode()).digest()
    img = Image.new("RGB", (320, 240), (h[0], h[1], h[2]))
    img.paste((h[3], h[4], h[5]), (10, 10, 60, 60))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def seed(*, submitted: datetime | None = None, photos: int = 1, damage_part: str | None = None,
         label: str = "seed") -> str:
    """One untaken public report as the bot's Submit leaves it: Report
    (channel telegram_public, status intake, no owner, reporter_chat_id
    "public"), IntakeSession (uploading, the notes), IntakePhoto rows with
    their files in intake/{id}/, and the PublicSubmission row."""
    model = _pub_model()
    if model is None:
        need(False, "(seeding) app.reports.models has PublicSubmission (brief §2.1)")
    submitted = submitted or utcnow()
    at = naive(submitted)
    cols = {c.name: c for c in model.__table__.columns}
    tg_int = "telegram_user_id" in cols and getattr(cols["telegram_user_id"].type, "python_type", str) is int
    rid = gen_id()
    ref = f"TG-{(submitted + timedelta(hours=8)).strftime('%y%m%d')}-" + "".join(random.choice(ALPHABET) for _ in range(4))
    tg = 880000000 + random.randrange(10 ** 7)
    first = random.choice(["Ignatius", "Perpetua", "Cornelius", "Theodora", "Bartholomew", "Euphemia"])
    name = f"{first} {label.capitalize()}{random.randrange(100, 999)}"
    phone = f"+659{random.randrange(10 ** 7):07d}"
    statement = f"Seeded statement {label}: scraped in a carpark."
    data = SecurityIncidentDraft(description="", category=["Vehicle Collision or Damage"], reporter_name=name,
                                 reporter_role="Driver", reporter_contact=f"+65 {phone[3:7]} {phone[7:]}").model_dump()
    data["vehicle_info"] = {**(data.get("vehicle_info") or {}), "plate_number": "SBA1234A"}
    if damage_part:
        data["damage_summary"] = [{"part": damage_part, "severity": "Minor"}]
    db = SessionLocal()
    try:
        db.add(Report(id=rid, channel=PUBLIC_CHANNEL, status="intake", owner_id=None, reporter_chat_id="public",
                      photo_paths=[], data=data, created_at=at))
        db.flush()
        db.add(IntakeSession(report_id=rid, stage="uploading", updated_at=at,
                             reporter_notes=("Sent by a member of the public through Telegram. Their own words, not checked.\n"
                                             f"When and where: Tampines\nWhat happened: {statement}")))
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
        values = {
            "report_id": rid, "reference": ref, "submitted_at": at, "expires_at": at + timedelta(days=30),
            "consent_at": at, "consent_version": CONSENT_VERSION, "submitter_name": name, "submitter_role": "driver",
            "submitter_phone": phone, "phone_kind": "sg_mobile", "plate": "SBA1234A", "plate_looks_sg": True,
            "when_where": "Tampines", "statement": statement, "photo_count_sent": photos, "forwarded_count": 0,
            "telegram_user_id": tg if tg_int else str(tg), "telegram_username": None, "telegram_name": name,
        }
        kwargs = {k: v for k, v in values.items() if k in cols}
        for cname, col in cols.items():
            if cname in kwargs or col.nullable or col.default is not None or col.server_default is not None or col.primary_key:
                continue
            py = getattr(col.type, "python_type", str)
            kwargs[cname] = at if py is datetime else (0 if py in (int, float) else (False if py is bool else ""))
        db.add(model(**kwargs))
        db.commit()
    finally:
        db.close()
    SEEDED[rid] = {"name": name, "phone": phone, "ref": ref, "tg": tg if tg_int else str(tg), "tg_other":
                   (tg + 1) if tg_int else str(tg + 1), "submitted": submitted}
    return rid


def unseed(ids) -> None:
    for rid in ids:
        for table, col in (("intake_photos", "report_id"), ("intake_sessions", "report_id"),
                           ("public_submissions", "report_id"), ("reports", "id")):
            if table_exists(table):
                sql_write(f"DELETE FROM {table} WHERE {col} = :r", r=rid)


def take(p: Person, rid: str, c=None):
    return (c or p.c).post(f"/inbox/{rid}/take")


def call_withdraw(rid: str, tg) -> tuple[object, str]:
    """public.withdraw(db, id, tg_id, now) (brief §2.2), committed."""
    if public is None or not hasattr(public, "withdraw"):
        return None, "app.intake.public.withdraw is missing"
    db = SessionLocal()
    try:
        try:
            out = public.withdraw(db, rid, tg, utcnow())
        except TypeError as exc:
            return None, f"public.withdraw(db, id, tg_id, now) -> TypeError: {exc}"
        except Exception as exc:
            return None, f"public.withdraw raised {type(exc).__name__}: {exc}"
        try:
            db.commit()
        except Exception:
            db.rollback()
        return out, ""
    finally:
        db.close()


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


def wait_idle(p: Person, rid: str, timeout: float = 45.0) -> dict:
    end = time.time() + timeout
    state: dict = {}
    while time.time() < end:
        r = p.c.get(f"/intake/{rid}")
        state = jd(r)
        if r.status_code != 200 or state.get("stage") not in ("sorting", "reading"):
            return state
        _real_sleep(0.15)
    return {**state, "stage": f"still {state.get('stage')!r} after {timeout:.0f}s"}


def inbox_ids(p: Person, view: str | None = None) -> tuple[int, set]:
    r = p.c.get("/inbox" + (f"?view={view}" if view else ""))
    body = r.json() if r.status_code == 200 else None
    items = body.get("items", []) if isinstance(body, dict) else (body if isinstance(body, list) else [])
    return r.status_code, {i.get("id") for i in items if isinstance(i, dict)}


def list_ids(p: Person, scope: str | None = None) -> tuple[int, set]:
    r = p.c.get("/reports" + (f"?scope={scope}" if scope else ""))
    rows = r.json() if r.status_code == 200 and isinstance(r.json(), list) else []
    return r.status_code, {row.get("id") for row in rows if isinstance(row, dict)}


# ================================================================= sections
def setup() -> None:
    W.boot = browser()
    W.boot.__enter__()  # the api's start-up: tables, the AI key secret
    W.ada = Person("Ada", role="admin")
    W.uma = Person("Uma")   # a plain user: no ticks
    W.ulf = Person("Ulf")   # another plain user

    def fake_html_to_pdf(html, output_path, slot_wait=None, *args, **kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"%PDF-1.4 fake")

    W.patches = [mock.patch.object(renderer, "html_to_pdf", fake_html_to_pdf)]
    for p in W.patches:
        p.start()
    check(LEGACY_TABLE, "(the reports table was made with a nullable channel before start-up)",
          "the model's DDL has no 'channel VARCHAR NOT NULL' to relax")
    check(not NET_REFUSED, "nothing tried to reach the internet during start-up", ", ".join(NET_REFUSED[:3]))


def data() -> None:
    check(public is not None, "app.intake.public imports (brief §2.2)", PUBLIC_IMPORT_ERROR)
    if public is not None:
        names = ("untaken_clause", "not_untaken_clause", "take", "release", "delete_submission", "withdraw",
                 "sweep_untaken", "on_finalize")
        missing = [n for n in names if not hasattr(public, n)]
        check(not missing, "it has " + ", ".join(names), "missing: " + ", ".join(missing))
    need(_pub_model() is not None, "app.reports.models has PublicSubmission")
    rid = seed(photos=2, label="probe")
    need(set(held_in(rid)) == set(ALL_FOUR) and intake_folder(rid).exists(),
         "(a seeded public report: a row in each of the 4 tables and an intake/ folder)", str(still_held(rid)))
    s, ids = inbox_ids(W.ada)
    check(s == 200 and rid in ids, "the seeded report is in the admin's GET /inbox (so the seeding is the real shape)",
          f"status {s}, {len(ids)} item(s)")
    unseed([rid])


def sweep_untaken() -> None:
    now = utcnow()
    r = W.uma.c.post("/intake")
    need(r.status_code == 200, "(Uma starts an ordinary draft: POST /intake)", brief(r))
    plain_draft = jd(r).get("id")
    rid = seed(submitted=now, photos=2, label="expiry")
    ref = SEEDED[rid]["ref"]

    out = sweep_at(now + timedelta(days=29))
    check(set(held_in(rid)) == set(ALL_FOUR) and intake_folder(rid).exists(),
          "day 29: the untaken public report is kept (not the 7-day idle rule)", f"left: {still_held(rid)}; sweep {out}")
    check(not held_in(plain_draft), "  the same pass still removes an ordinary draft idle for 29 days (7-day rule unchanged)",
          f"left: {held_in(plain_draft)}")

    out = sweep_at(now + timedelta(days=30, minutes=5))
    check(not still_held(rid), "just after day 30: deleted, files and all 4 tables", f"still in {still_held(rid)}; sweep {out}")
    entries = audit_for(rid)
    expired = [e for e in entries if "expired" in f"{e['action']} {e['detail']}".lower()]
    check(len(expired) == 1, "  one 'expired' history entry", str(entries)[:300])
    if expired:
        e = expired[0]
        check(ref in f"{e['action']} {e['detail']}", f"  it carries the ref {ref}", str(e))
        check(not pii_in(f"{e['action']} {e['detail']} {e['user_name']}", rid), "  and no name or phone", str(e))
        check(e["user_id"] is None, "  with no account behind it (the sweep did it)", str(e))


def sweep_taken() -> None:
    now = utcnow()
    s1 = staff("Tara")
    kept = seed(submitted=now - timedelta(days=25), photos=1, label="taken25")
    released = seed(submitted=now - timedelta(days=25), photos=1, label="released25")
    r = take(s1, kept)
    need(r.status_code == 200, "staff take a 25-day-old public report -> 200", brief(r))
    r = take(s1, released)
    need(r.status_code == 200, "  and another one", brief(r))
    r = s1.c.post(f"/inbox/{released}/release")
    need(r.status_code == 200, "  and release that second one back to the inbox -> 200", brief(r))
    check(report_row(released).get("owner_id") is None, "  (released: no owner again)")

    out = sweep_at(now + timedelta(days=5, hours=1))   # day 30 + 1 h after submission
    check(set(held_in(kept)) == set(ALL_FOUR) and intake_folder(kept).exists(),
          "day 30: the report taken on day 25 is kept (taking it touched the session)", f"left: {still_held(kept)}; {out}")
    check(not still_held(released), "  the one released again after its 30 days is deleted at this sweep (L8)",
          f"still in {still_held(released)}")
    check(any("expired" in f"{e['action']} {e['detail']}".lower() for e in audit_for(released)),
          "  with an 'expired' history entry", str(audit_for(released))[:200])

    sweep_at(now + timedelta(days=6, hours=20))
    check(set(held_in(kept)) == set(ALL_FOUR), "6 days 20 h after the take: still kept", f"left: {still_held(kept)}")
    sweep_at(now + timedelta(days=7, hours=1))
    check(not still_held(kept), "7 days after the take, idle: removed by the 7-day rule, files and all 4 tables",
          f"still in {still_held(kept)}")


def race_take_sweep() -> None:
    now = utcnow()
    print("  interleaved: sweep first, then take")
    e1 = seed(submitted=now - timedelta(days=31), photos=1, label="sweepfirst")
    sx = staff("Sven")
    sweep_at(now)
    check(not still_held(e1), "an expired untaken report is deleted by the sweep", f"still in {still_held(e1)}")
    r = take(sx, e1)
    check(turned_away(r), "take afterwards -> 404/409 (gone), not 200", brief(r))

    print("  interleaved: take first, then sweep")
    e2 = seed(submitted=now - timedelta(days=31), photos=1, label="takefirst")
    r = take(sx, e2)
    if r.status_code == 200:
        sweep_at(now)
        check(set(held_in(e2)) == set(ALL_FOUR) and report_row(e2).get("owner_id") == sx.id,
              "taken (past its 30 days but before the sweep): the sweep keeps it", f"left: {still_held(e2)}")
    else:
        info(f"take of a report past its 30 days (not yet swept) answered {r.status_code} {code_of(r)}")
        check(turned_away(r), "take of a report past its 30 days -> 200, or a clean 404/409", brief(r))
        sweep_at(now)
        check(not still_held(e2), "  and the sweep deletes it", f"still in {still_held(e2)}")

    print("  two threads, released together")
    wins = Counter()
    for i in range(6):
        rid = seed(submitted=now - timedelta(days=31), photos=1, label=f"tvs{i}")
        sp = staff(f"Race{i}")
        r_take, r_sweep = race(lambda: take(sp, rid, sp.tab()), lambda: cleanup.sweep(now=utcnow()))
        left = held_in(rid)
        st = status(r_take)
        if st == 200:
            wins["take"] += 1
            ok = set(left) == set(ALL_FOUR) and report_row(rid).get("owner_id") == sp.id and \
                pub_row(rid).get("taken_by") == sp.id
        else:
            wins["sweep"] += 1
            ok = turned_away(r_take) and not left
        check(ok, f"round {i + 1}: take {st} -> {'taken and kept' if st == 200 else 'swept, all 4 tables gone'}",
              f"take {st} {brief(r_take) if hasattr(r_take, 'status_code') else ''}; sweep {r_sweep!r}; rows left {left}")
    info(f"take vs sweep, 6 rounds: {dict(wins)}")


def race_take_withdraw() -> None:
    need(public is not None and hasattr(public, "withdraw"), "app.intake.public.withdraw exists (brief §2.2)")
    sw = staff("Wynn")
    print("  interleaved: withdraw first, then take")
    w1 = seed(photos=1, label="wdfirst")
    out, err = call_withdraw(w1, SEEDED[w1]["tg"])
    need(not err, "public.withdraw(db, id, tg_id, now) runs", err)
    check(out == "deleted", "withdraw by the sender of an untaken report -> 'deleted'", repr(out))
    check(not held_in(w1), "  all 4 tables are gone", f"still in {held_in(w1)}")
    if intake_folder(w1).exists():
        info("withdraw left the intake/ folder for its caller to remove")
    r = take(sw, w1)
    check(turned_away(r), "take afterwards -> 404/409, not 200", brief(r))

    print("  interleaved: take first, then withdraw")
    w2 = seed(photos=1, label="takefirst")
    r = take(sw, w2)
    need(r.status_code == 200, "staff take it -> 200", brief(r))
    out, err = call_withdraw(w2, SEEDED[w2]["tg"])
    check(out == "taken", "withdraw afterwards -> 'taken'", repr(out) + err)
    check(set(held_in(w2)) == set(ALL_FOUR) and report_row(w2).get("owner_id") == sw.id,
          "  nothing is deleted; still the staff member's", f"left {held_in(w2)}")
    check(pub_row(w2).get("withdraw_requested_at") is not None, "  withdraw_requested_at is recorded")

    print("  someone else's Telegram id")
    w3 = seed(photos=1, label="forged")
    out, err = call_withdraw(w3, SEEDED[w3]["tg_other"])
    check(out == "none" and set(held_in(w3)) == set(ALL_FOUR),
          "withdraw with another Telegram id -> 'none', nothing deleted", f"{out!r} {err}; left {held_in(w3)}")
    unseed([w3])

    print("  two threads, released together")
    wins = Counter()
    for i in range(6):
        rid = seed(photos=1, label=f"tvw{i}")
        sp = staff(f"Rival{i}")
        r_take, (out, err) = race(lambda: take(sp, rid, sp.tab()), lambda: call_withdraw(rid, SEEDED[rid]["tg"]))
        st, left = status(r_take), held_in(rid)
        if st == 200:
            wins["take"] += 1
            ok = out == "taken" and set(left) == set(ALL_FOUR) and report_row(rid).get("owner_id") == sp.id
        else:
            wins["withdraw"] += 1
            ok = out == "deleted" and turned_away(r_take) and not left
        check(ok, f"round {i + 1}: take {st}, withdraw {out!r} -> exactly one wins, consistently",
              f"rows left {left}; {err}")
    info(f"take vs withdraw, 6 rounds: {dict(wins)}")


def race_discard_finalize() -> None:
    sf = staff("Fern")

    def taken_draft(label: str) -> tuple[str, dict]:
        rid = seed(photos=1, label=label)
        r = take(sf, rid)
        need(r.status_code == 200, f"({label}: staff take it)", brief(r))
        state = jd(sf.c.get(f"/intake/{rid}"))
        need(bool(state.get("data")), f"({label}: GET /intake/{{id}} gives the draft's data)")
        return rid, state["data"]

    print("  interleaved: finalize first, then discard")
    rid, data = taken_draft("finfirst")
    r = sf.c.post(f"/intake/{rid}/finalize", json={"data": data})
    need(r.status_code == 200, "staff finalize it -> 200", brief(r))
    r = sf.c.post(f"/inbox/{rid}/discard", json={"reason": "test"})
    check(r.status_code == 409 and code_of(r) == "inbox_filed", "discard afterwards -> 409 inbox_filed", brief(r))
    check(report_row(rid).get("status") not in (None, "intake"), "  the filed case is untouched")

    print("  interleaved: discard first, then finalize")
    rid, data = taken_draft("discfirst")
    r = sf.c.post(f"/inbox/{rid}/discard", json={"reason": "test"})
    need(r.status_code == 200, "the owner discards it -> 200", brief(r))
    check(not still_held(rid), "  everything is gone", f"still in {still_held(rid)}")
    r = sf.c.post(f"/intake/{rid}/finalize", json={"data": data})
    check(turned_away(r), "finalize afterwards -> 404/409, no case", brief(r))
    check(not held_in(rid), "  and no case appeared", f"{held_in(rid)}")

    print("  two threads, released together")
    wins = Counter()
    for i in range(5):
        rid, data = taken_draft(f"dvf{i}")
        a, b = sf.tab(), sf.tab()
        r_disc, r_fin = race(lambda: a.post(f"/inbox/{rid}/discard", json={"reason": "test"}),
                             lambda: b.post(f"/intake/{rid}/finalize", json={"data": data}))
        sd, sfin = status(r_disc), status(r_fin)
        left, row = held_in(rid), report_row(rid)
        if sfin == 200 and sd != 200:
            wins["finalize"] += 1
            ok = turned_away(r_disc) and row.get("status") not in (None, "intake") and pub_row(rid).get("filed_at") is not None
        elif sd == 200 and sfin != 200:
            wins["discard"] += 1
            ok = turned_away(r_fin) and not left
        else:
            ok = False
        check(ok, f"round {i + 1}: discard {sd}, finalize {sfin} -> exactly one wins, and the rows agree",
              f"rows left {left}, status {row.get('status')!r}")
    info(f"discard vs finalize, 5 rounds: {dict(wins)}")


def race_release_ai() -> None:
    sr = staff("Remy", own_key=True)
    rid = seed(photos=2, label="aiheld")
    r = take(sr, rid)
    need(r.status_code == 200, "staff (own billed key) take a public report", brief(r))
    FAKE.delay = 0.8
    try:
        r = sr.c.post(f"/intake/{rid}/process", json={"mode": "all"})
        need(r.status_code == 200, "and press Start -> 200 (company_key_billed false: their own key)", brief(r))
        r = sr.c.post(f"/inbox/{rid}/release")
        check(r.status_code == 409 and code_of(r) == "ai_running", "release while the AI runs -> 409 ai_running", brief(r))
        r = W.ada.c.post(f"/inbox/{rid}/release")
        check(r.status_code == 409 and code_of(r) == "ai_running", "an admin's release while it runs -> 409 ai_running",
              brief(r))
        r = sr.c.post(f"/inbox/{rid}/discard", json={"reason": "test"})
        check(r.status_code == 409 and code_of(r) == "ai_running", "discard while it runs -> 409 ai_running", brief(r))
        check(report_row(rid).get("owner_id") == sr.id and set(held_in(rid)) == set(ALL_FOUR),
              "  still taken, nothing deleted")
    finally:
        FAKE.delay = 0.0
    state = wait_idle(sr, rid)
    check(state.get("stage") == "ready", "the run finishes: ready", f"stage {state.get('stage')!r}, {state.get('error')!r}")
    _real_sleep(0.5)  # the run hands back the person's one AI turn as it ends
    r = sr.c.post(f"/inbox/{rid}/release")
    check(r.status_code == 200 and jd(r).get("owner") is None, "after it finishes, release -> 200, no owner", brief(r))
    check(report_row(rid).get("owner_id") is None, "  reports.owner_id is NULL again")
    _, ids = inbox_ids(W.ada, "new")
    check(rid in ids, "  and it is back in the inbox's 'new' view")
    mark = len(CALLS)
    r = sr.c.post(f"/intake/{rid}/process", json={"mode": "read"})
    check(r.status_code == 403, "Start after the release -> 403 (it's in the inbox again)", brief(r))
    _real_sleep(0.3)
    check(len(CALLS) == mark, "  no Gemini call")

    print("  two threads: Start and release released together")
    for i in range(3):
        rid = seed(photos=1, label=f"svr{i}")
        r = take(sr, rid)
        need(r.status_code == 200, f"(round {i + 1}: staff take it)", brief(r))
        FAKE.delay = 0.4
        a, b = sr.tab(), sr.tab()
        try:
            r_proc, r_rel = race(lambda: a.post(f"/intake/{rid}/process", json={"mode": "all"}),
                                 lambda: b.post(f"/inbox/{rid}/release"))
            sp, srel = status(r_proc), status(r_rel)
            wait_idle(W.ada, rid)
        finally:
            FAKE.delay = 0.0
        _real_sleep(0.5)  # the run hands back the person's one AI turn as it ends
        owner = report_row(rid).get("owner_id")
        if srel == 200:
            ok = turned_away(r_proc, (403, 404, 409)) and owner is None
        else:
            ok = sp == 200 and srel == 409 and code_of(r_rel) == "ai_running" and owner == sr.id
        check(ok, f"round {i + 1}: Start {sp}, release {srel} -> never both (no AI on a report back in the inbox)",
              f"owner {owner!r}; release {brief(r_rel) if hasattr(r_rel, 'status_code') else r_rel}")
        if owner == sr.id:
            sr.c.post(f"/inbox/{rid}/discard", json={"reason": "test"})


def shared_draft_cap() -> None:
    ids = [seed(photos=0, label=f"cap{i}") for i in range(30)]
    untaken = sql("SELECT COUNT(*) AS n FROM reports WHERE channel = :c AND status = 'intake' AND owner_id IS NULL",
                  c=PUBLIC_CHANNEL)[0]["n"]
    check(untaken >= 30, f"({untaken} untaken public reports in the database)")
    r = W.ulf.c.post("/intake")
    check(r.status_code == 200, "a plain user's POST /intake is not blocked by them -> 200 (H2)", brief(r))
    W.cap_ids = ids
    r = W.uma.c.post("/intake")
    check(r.status_code == 200, "  nor another plain user's", brief(r))
    unseed(ids)


def lists() -> None:
    need(LEGACY_TABLE, "the reports table allows a NULL channel (see [setup])")
    st = staff("Lise")
    untaken = seed(photos=1, label="listed", damage_part="Zz Public Only Part")
    taken_one = seed(photos=1, label="listedtaken")
    r = take(st, taken_one)
    need(r.status_code == 200, "(staff take one public report)", brief(r))
    blank = json.dumps(SecurityIncidentDraft(description="", category=["Vehicle Collision or Damage"]).model_dump())
    filed = SecurityIncidentDraft(description="Old case.", category=["Vehicle Collision or Damage"]).model_dump()
    filed["damage_summary"] = [{"part": "Zz Legacy Bumper", "severity": "Minor"}]
    legacy = {}
    stamp = naive(utcnow()).strftime("%Y-%m-%d %H:%M:%S.%f")
    for key, status_, owner, body in (("draft", "intake", None, blank), ("own_draft", "intake", W.uma.id, blank),
                                      ("filed", "confirmed", None, json.dumps(filed))):
        rid = gen_id()
        sql_write("INSERT INTO reports (id, type, status, channel, reporter_chat_id, data, photo_paths, created_at, owner_id) "
                  "VALUES (:id, 'security_incident', :st, NULL, 'dashboard', :data, '[]', :at, :owner)",
                  id=rid, st=status_, data=body, at=stamp, owner=owner)
        if status_ == "intake":
            sql_write("INSERT INTO intake_sessions (report_id, stage, progress_done, progress_total, reporter_notes, "
                      "read_photo_ids, updated_at) VALUES (:id, 'uploading', 0, 0, '', '[]', :at)", id=rid, at=stamp)
        legacy[key] = rid
    check(sql("SELECT COUNT(*) AS n FROM reports WHERE channel IS NULL")[0]["n"] == 3, "(3 legacy rows with a NULL channel)")

    views = {
        "admin (default)": list_ids(W.ada), "admin ?scope=all": list_ids(W.ada, "all"),
        "admin ?scope=unassigned": list_ids(W.ada, "unassigned"), "admin ?scope=mine": list_ids(W.ada, "mine"),
        "see-all staff (default)": list_ids(st), "see-all staff ?scope=all": list_ids(st, "all"),
        "see-all staff ?scope=unassigned": list_ids(st, "unassigned"), "plain user": list_ids(W.uma),
    }
    bad = [k for k, (s, ids) in views.items() if s != 200]
    check(not bad, "every GET /reports view answers 200", str({k: views[k][0] for k in bad}))
    shown = [k for k, (s, ids) in views.items() if untaken in ids]
    check(not shown, "the untaken public report is in NO GET /reports scope", f"shown in: {shown}")
    for key, where in (("draft", ("admin ?scope=all", "admin ?scope=unassigned", "see-all staff ?scope=all",
                                  "see-all staff ?scope=unassigned")),
                       ("own_draft", ("plain user", "admin ?scope=all")),
                       ("filed", ("admin ?scope=all", "admin ?scope=unassigned"))):
        missing = [w for w in where if legacy[key] not in views[w][1]]
        check(not missing, f"a legacy {key.replace('_', ' ')} with a NULL channel still shows in {', '.join(where)}",
              f"missing from: {missing}")
    check(taken_one in views["admin ?scope=all"][1], "a taken public report shows like any draft (admin, all)")
    check(taken_one in list_ids(st, "mine")[1], "  and in its taker's 'mine'")

    expected = sql("SELECT COUNT(*) AS n FROM reports WHERE status != 'intake'")[0]["n"]
    for who, p in (("admin", W.ada), ("see-all staff", st)):
        r = p.c.get("/analytics/summary?scope=all")
        check(r.status_code == 200 and jd(r).get("total_incidents") == expected,
              f"analytics ({who}, all): total_incidents = every non-draft case, the NULL-channel one included ({expected})",
              brief(r))
        r = p.c.get("/taxonomy?scope=all")
        usage = jd(r).get("usage") or {}
        check(r.status_code == 200 and usage.get("reports_counted") == expected,
              f"taxonomy ({who}, all): reports_counted = {expected}", brief(r))
        names = set((usage.get("parts") or {})) | set((usage.get("legacy_terms") or {}))
        check("Zz Legacy Bumper" in names and "Zz Public Only Part" not in names,
              "  the legacy case's part is counted; the untaken public report's is not", str(sorted(names))[:200])
    unseed([untaken])


def errors() -> None:
    check(not STATUS_500, "no API response was a 500", "; ".join(STATUS_500[:4]))
    hosts = {h for h in NET_REFUSED}
    check(not hosts, "nothing tried to reach an outside host", str(sorted(hosts))[:200])
    found = []
    for rid, s in SEEDED.items():
        for lvl, name, msg in LOG_RECORDS:
            if lvl >= logging.INFO and pii_in(msg, rid):
                found.append(f"{name}: {msg[:120]!r}")
                break
    check(not found, "no log line (INFO and up) holds a seeded sender's name or phone", "; ".join(found[:3]))
    entries = sql("SELECT report_id, action, detail, user_name FROM audit_log")
    bad = [f"{e['action']}: {e['detail']}" for e in entries for rid in SEEDED
           if pii_in(f"{e['action']} {e['detail']} {e['user_name']}", rid)]
    check(not bad, f"no audit entry (of {len(entries)}) holds a seeded sender's name or phone", "; ".join(bad[:3]))


def main() -> int:
    for name, fn in (
        ("setup: the api, a legacy-shaped reports table", setup),
        ("data: the public module and a seeded report", data),
        ("sweep: 30 days for untaken reports", sweep_untaken),
        ("sweep: taken and released reports", sweep_taken),
        ("race: take vs sweep", race_take_sweep),
        ("race: take vs withdraw", race_take_withdraw),
        ("race: discard vs finalize", race_discard_finalize),
        ("race: release while the AI runs", race_release_ai),
        ("30 untaken reports and POST /intake", shared_draft_cap),
        ("lists, analytics and taxonomy", lists),
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
    print(f"\ntest_public_retention: {PASSED[0]} checks passed, {len(FAILURES)} failed")
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
    print("\nALL PUBLIC RETENTION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
