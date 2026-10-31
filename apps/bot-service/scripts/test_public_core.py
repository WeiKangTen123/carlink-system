"""Tests for release P6 "public reports through the Telegram bot", the
shared core the bot and the inbox build on: app/intake/public.py, the
public_submissions and public_blocks tables, the permission rules for
public reports, the cleanup sweep's public parts, disk_space_low(extra_gb),
and the AI key rule for public reports while the company key's billing
isn't confirmed.

    python scripts/test_public_core.py

From plans/2026-10-30-public-reporter/p6_build_brief.md (§2.1 data, §2.2
module, §2.6 retention, §2.8 CORE tests, §3 M4/M5/M8/H3) and
p6_decisions.md §1 and §3.

Offline and self-contained: a temporary STORAGE_DIR, SECRETS_DIR and
database, and every outside connection refused. "Two sessions" checks
open two database sessions the way two processes (or two tabs) would: one
looks, the other changes and commits, then the first acts on what it saw.
No sender's name, phone or words may appear in a history entry or a log
line.
"""
import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
_tmp = Path(tempfile.mkdtemp(prefix="public-core-test-"))
STORAGE = (_tmp / "storage").resolve()
STORAGE.mkdir()
DB_FILE = (_tmp / "test.db").resolve()
PHOTOS = _tmp / "photos"
PHOTOS.mkdir()
# Forced, not setdefault: never a real folder, database or key.
os.environ["STORAGE_DIR"] = str(STORAGE)
os.environ["SECRETS_DIR"] = str(_tmp / "secrets")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_FILE.as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaEnvironmentCompanyKeyForTests000000"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
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

from PIL import Image  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

import app.api.intake as api_intake  # noqa: E402
from app.ai import keys, secretbox  # noqa: E402
from app.ai.keys import AiActor, AiUnavailable  # noqa: E402
from app.auth import chat_links, permissions  # noqa: E402
from app.auth.core import utc_now  # noqa: E402
from app.auth.deps import _case_access  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import cleanup, photos as intake_photos, public  # noqa: E402
from app.reports.db import SessionLocal, engine, init_db  # noqa: E402
from app.reports.models import (  # noqa: E402
    ApiKey,
    AuditEntry,
    ChatLink,
    IntakePhoto,
    IntakeSession,
    PublicBlock,
    PublicSubmission,
    Report,
    User,
    UserAiKey,
)
from app.storage import files  # noqa: E402

P = public.PUBLIC_CHANNEL

# ---------------------------------------------------------------- harness
PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}
# Every sender's name, phone and words used: none may be in a history
# entry or a log line.
PERSONAL: set[str] = set()


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


def db_do(fn):
    """fn(db) in its own session, committed; rows it returns stay readable."""
    db = SessionLocal(expire_on_commit=False)
    try:
        out = fn(db)
        db.commit()
        return out
    finally:
        db.close()


def code_of(fn):
    """The .code fn raised (InboxError, PublicRefused, AiUnavailable), or None."""
    try:
        fn()
    except (public.InboxError, public.PublicRefused, AiUnavailable) as exc:
        return exc.code
    return None


def raised(fn):
    try:
        fn()
    except Exception as exc:
        return exc
    return None


W = SimpleNamespace()
_n = iter(range(1, 1_000_000))
T0 = datetime(2026, 9, 1, 4, 0, tzinfo=timezone.utc)  # 12:00 in Singapore


def make_user(username: str, name: str, *, role: str = "user", active: bool = True, **ticks) -> User:
    def go(db):
        u = User(email=f"{username}@example.com", username=username, name=name, role=role,
                 password_hash="!", active=active, **ticks)
        db.add(u)
        db.flush()
        return u
    return db_do(go)


def fresh_user(user_id: str) -> User:
    return db_do(lambda db: db.get(User, user_id))


def jpeg(name: str, color, size=(64, 48)) -> str:
    path = PHOTOS / name
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)
    return str(path)


def form(tg=None, *, name=None, phone=None, role="driver", plate="SJB 8762-m", statement=None,
         when_where="Yesterday about 6pm, PIE towards Changi near exit 8", consent_at=None, **extra) -> public.Form:
    """A finished public form; each made-up sender's name, phone and words
    are remembered for the leak checks."""
    n = next(_n)
    tg = tg if tg is not None else str(800_000_000 + n)
    name = name or f"Tan Ah Huat {n:04d}"
    phone = phone or f"+659{n:07d}"
    statement = statement or f"A taxi hit my rear bumper at the Bishan lights, story number {n:05d}."
    PERSONAL.update({name, phone, phone[3:], public.phone_display(phone)[4:], statement})
    return public.Form(telegram_user_id=tg, name=name, role=role, phone=phone, statement=statement,
                       consent_at=consent_at or utc_now(), plate=plate, when_where=when_where, **extra)


def submit(tg=None, *, when=None, photo_paths=(), **kw) -> tuple[str, str]:
    return public.save_submission(form(tg, **kw), list(photo_paths), now=when)


def sub(report_id: str) -> PublicSubmission | None:
    return db_do(lambda db: db.get(PublicSubmission, report_id))


def rep(report_id: str) -> Report | None:
    return db_do(lambda db: db.get(Report, report_id))


def ses(report_id: str) -> IntakeSession | None:
    return db_do(lambda db: db.get(IntakeSession, report_id))


def photo_rows(report_id: str) -> list[IntakePhoto]:
    return db_do(lambda db: db.query(IntakePhoto).filter(IntakePhoto.report_id == report_id).order_by(IntakePhoto.position).all())


def history(report_id: str) -> list[AuditEntry]:
    return db_do(lambda db: db.query(AuditEntry).filter(AuditEntry.report_id == report_id).order_by(AuditEntry.id).all())


def actions(report_id: str) -> list[str]:
    return [e.action for e in history(report_id)]


def set_stage(report_id: str, stage: str) -> None:
    db_do(lambda db: db.query(IntakeSession).filter(IntakeSession.report_id == report_id).update({"stage": stage}))


def gone_entirely(report_id: str) -> bool:
    def go(db):
        return (
            db.get(Report, report_id) is None
            and db.get(IntakeSession, report_id) is None
            and db.get(PublicSubmission, report_id) is None
            and db.query(IntakePhoto).filter(IntakePhoto.report_id == report_id).count() == 0
        )
    return db_do(go)


def folder(report_id: str) -> Path:
    return STORAGE / "intake" / report_id


def filler_drafts(user_id: str, count: int) -> None:
    """Ordinary drafts this person owns, to reach the draft cap."""
    def go(db):
        for _ in range(count):
            db.add(Report(channel="manual", reporter_chat_id="dashboard", status="intake", owner_id=user_id, data={}))
    db_do(go)


def clear_public() -> None:
    """Every public report, block and chat link, and the filler drafts:
    each section starts from an empty inbox."""
    def go(db):
        ids = [r for (r,) in db.query(Report.id).filter(Report.channel == P).all()]
        if ids:
            db.query(IntakePhoto).filter(IntakePhoto.report_id.in_(ids)).delete(synchronize_session=False)
            db.query(IntakeSession).filter(IntakeSession.report_id.in_(ids)).delete(synchronize_session=False)
        db.query(Report).filter(Report.channel == P).delete(synchronize_session=False)
        db.query(Report).filter(Report.channel == "manual").delete(synchronize_session=False)
        db.query(PublicSubmission).delete(synchronize_session=False)
        db.query(PublicBlock).delete(synchronize_session=False)
        db.query(ChatLink).delete(synchronize_session=False)
    db_do(go)


def take(report_id: str, who: User, now=None):
    db = SessionLocal()
    try:
        ref = public.take(db, report_id, who, now)
        db.commit()
        return ref
    finally:
        db.close()


# ================================================================= sections
def setup() -> None:
    init_db()
    check(secretbox.startup(create=True) == secretbox.OK, "the AI-key secret is ready", secretbox.state())
    W.admin = make_user("ada", "Ada Admin", role="admin")
    W.sam = make_user("sam", "S. Lee", sees_all_cases=True)
    W.sid = make_user("sid", "Sid Staff", sees_all_cases=True, may_sign_off=True)
    W.tia = make_user("tia", "Tia Tick", ai_company_key_allowed=True)
    W.ann = make_user("ann", "Ann Plain")
    W.dee = make_user("dee", "Dee Disabled", active=False, sees_all_cases=True)
    W.kim = make_user("kim", "Kim Ownkey")
    W.quinn = make_user("quinn", "Quinn Quota")
    W.una = make_user("una", "Una Unbilled")
    W.photos = [jpeg(f"p{i:02d}.jpg", ((i * 37) % 256, (i * 91) % 256, (i * 53) % 256)) for i in range(22)]
    need(len(set(Path(p).read_bytes() for p in W.photos)) == 22, "22 different test photos")


def tables() -> None:
    with engine.connect() as conn:
        sql = {n: conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:n"), {"n": n}).scalar() or ""
               for n in ("public_submissions", "public_blocks")}
        indexes = {r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name IN ('public_submissions','public_blocks')"))}
    subs = " ".join(sql["public_submissions"].split())
    blocks = " ".join(sql["public_blocks"].split())
    check("CONSTRAINT uq_public_submissions_reference UNIQUE (reference)" in subs,
          "public_submissions: the reference's unique rule is inside CREATE TABLE", subs[-160:])
    check("PRIMARY KEY (report_id)" in subs, "public_submissions: keyed by report_id")
    for column in ("reference", "submitted_at", "expires_at", "consent_at", "consent_version", "submitter_name",
                   "submitter_role", "submitter_phone", "phone_kind", "plate", "plate_looks_sg", "when_where",
                   "statement", "photo_count_sent", "forwarded_count", "telegram_user_id", "telegram_username",
                   "telegram_name", "taken_at", "taken_by", "filed_at", "withdraw_requested_at"):
        check(column in subs, f"public_submissions has {column}")
    check("phone_verified" not in subs and "status" not in subs, "no phone_verified and no stored state column")
    check("PRIMARY KEY (telegram_user_id)" in blocks, "public_blocks: keyed by the Telegram user id")
    for column in ("blocked_at", "until", "blocked_by", "reference"):
        check(column in blocks, f"public_blocks has {column}")
    for index in ("ix_public_submissions_expires_at", "ix_public_submissions_telegram_user_id_submitted_at",
                  "ix_public_submissions_submitter_phone_submitted_at", "ix_public_blocks_until"):
        check(index in indexes, f"index {index}", str(sorted(indexes)))
    # The same reference twice is refused by the table itself.
    now = utc_now()

    def dup(db):
        for rid in ("dupref1", "dupref2"):
            db.add(PublicSubmission(report_id=rid, reference="TG-261030-AAAA", submitted_at=now, expires_at=now,
                                    consent_at=now, consent_version="x", submitter_name="x", submitter_role="other",
                                    submitter_phone="+6560000000", phone_kind="sg_other", statement="x"))
        db.flush()
    check(isinstance(raised(lambda: db_do(dup)), IntegrityError), "a second row with the same reference: IntegrityError")


def reference() -> None:
    clear_public()
    db = SessionLocal()
    try:
        refs = [public.new_reference(db, T0) for _ in range(200)]
        check(all(public.is_reference(r) for r in refs), "TG-YYMMDD-XXXX, 4 characters from CODE_ALPHABET", refs[0])
        check(all(r.startswith("TG-260901-") for r in refs), "the date is Singapore's (12:00 SGT on 1 Sep 2026)", refs[0])
        check(not any(set(r[10:]) & set("01OIL") for r in refs), "never 0, O, 1, I or L in the random part")
        late = datetime(2026, 10, 30, 16, 30, tzinfo=timezone.utc)  # 00:30 on 31 Oct in Singapore
        check(public.new_reference(db, late).startswith("TG-261031-"), "16:30 UTC on 30 Oct is already 31 Oct in Singapore")
        check(public.new_reference(db, late - timedelta(minutes=31)).startswith("TG-261030-"), "15:59 UTC is still 30 Oct")
        check(public.new_reference(db, late.replace(tzinfo=None)).startswith("TG-261031-"), "a naive time is taken as UTC")

        # A clash with a report still here: drawn again.
        rid, ref = submit(when=datetime(2026, 10, 30, 4, 0, tzinfo=timezone.utc))
        wanted = list(ref[-4:]) + list("BBBB")
        with mock.patch.object(public.secrets, "choice", side_effect=lambda alphabet: wanted.pop(0)):
            again = public.new_reference(db, datetime(2026, 10, 30, 5, 0, tzinfo=timezone.utc))
        check(again == "TG-261030-BBBB" and ref != again, "a clash with an existing reference is drawn again", f"{ref} -> {again}")
    finally:
        db.close()

    # No variant of any reference is ever a link code (every month, every day).
    variants = 0
    bad = []
    for month in range(1, 13):
        for day in (1, 9, 10, 19, 20, 28):
            now = datetime(2026, month, day, 2, 0, tzinfo=timezone.utc)
            db = SessionLocal()
            try:
                for _ in range(15):
                    r = public.new_reference(db, now)
                    flat = r.replace("-", "")
                    forms = {r, r.lower(), flat, r.replace("-", " "), r[3:], r[3:].replace("-", ""), r[3:].replace("-", " ")}
                    for i in range(len(flat) - 7):
                        w = flat[i:i + 8]
                        forms.update({w, w[:4] + "-" + w[4:], w[:4] + " " + w[4:], w.lower()})
                    for v in forms:
                        variants += 1
                        if chat_links.looks_like_code(v) or chat_links.normalize_code(v) or chat_links.code_from_start_payload("link-" + v):
                            bad.append(v)
            finally:
                db.close()
    check(not bad, f"none of {variants} dash/space/case variants of 1,080 references is a link code", str(bad[:5]))
    check(public.is_reference("TG-261030-K7M4") and not public.is_reference("K7M4-QX9P") and not public.is_reference("TG-261030-K7M0"),
          "is_reference: TG-261030-K7M4 yes; a link code or a 0 no")


def clauses() -> None:
    # A database whose reports table allows NULLs (as a legacy or hand-made
    # row may have them): the clauses must still split every row.
    loose = create_engine("sqlite://")
    rows = [
        ("legacy_null", None, "intake", None),
        ("all_null", None, None, None),
        ("null_status", P, None, None),
        ("pub_new", P, "intake", None),
        ("pub_taken", P, "intake", "u1"),
        ("pub_filed", P, "confirmed", None),
        ("tg_draft", "telegram", "intake", None),
        ("manual", "manual", "intake", "u2"),
    ]
    with loose.begin() as conn:
        conn.execute(text("CREATE TABLE reports (id TEXT PRIMARY KEY, channel TEXT, status TEXT, owner_id TEXT)"))
        for r in rows:
            conn.execute(text("INSERT INTO reports VALUES (:a, :b, :c, :d)"), {"a": r[0], "b": r[1], "c": r[2], "d": r[3]})
        untaken = {x for (x,) in conn.execute(select(Report.id).where(public.untaken_clause()))}
        not_untaken = {x for (x,) in conn.execute(select(Report.id).where(public.not_untaken_clause()))}
        naive = {x for (x,) in conn.execute(select(Report.id).where(~public.untaken_clause()))}
    check(untaken == {"pub_new"}, "untaken_clause: only the public report with no owner", str(untaken))
    check(not_untaken == {r[0] for r in rows} - {"pub_new"}, "not_untaken_clause: every other row, NULL channel and status too",
          str(sorted(not_untaken)))
    check({"legacy_null", "all_null"} <= not_untaken, "a legacy draft with a NULL channel still shows in lists")
    check("legacy_null" not in naive, "(NOT untaken_clause would have lost it: why the clause says each part itself)")


def saving() -> None:
    clear_public()
    p = W.photos
    oversize = PHOTOS / "oversize.jpg"
    oversize.write_bytes(Path(p[0]).read_bytes() + b"\0" * (5 * 1024 * 1024))
    huge = PHOTOS / "huge.jpg"
    Image.new("L", (8000, 5001), 128).save(huge, "JPEG", quality=50)
    notimage = PHOTOS / "notimage.jpg"
    notimage.write_bytes(b"this is not a photo at all")
    gif = PHOTOS / "anim.gif"
    Image.new("P", (20, 20)).save(gif, "GIF")
    cut = PHOTOS / "cut.jpg"
    cut.write_bytes(Path(jpeg("whole.jpg", (10, 200, 30), (400, 300))).read_bytes()[:700])
    png = PHOTOS / "shot.png"
    Image.new("RGB", (30, 30), (200, 10, 10)).save(png, "PNG")
    sent = [p[0], p[1], p[0], str(oversize), str(huge), str(notimage), str(gif), str(cut), str(png), str(PHOTOS / "missing.jpg"), p[2]]

    f = form(name="Tan Ah Huat", phone="6591234567", plate="sjb 8762-m", telegram_username="ahhuat", telegram_name="Ah Huat",
             photo_count_sent=11, forwarded_count=2,
             statement="A taxi hit my rear bumper at the red light on Bishan Road, nobody was hurt.")
    PERSONAL.update({"+6591234567", "91234567", "9123 4567"})
    when = datetime(2026, 10, 30, 7, 12, tzinfo=timezone.utc)
    rid, ref = public.save_submission(f, sent, now=when)
    check(public.is_reference(ref) and ref.startswith("TG-261030-"), "returns (report id, TG- reference)", ref)

    r = rep(rid)
    need(r is not None, "the Report row is there")
    check((r.channel, r.status, r.owner_id, r.reporter_chat_id, r.photo_paths, r.pdf_path) == (P, "intake", None, "public", [], None),
          "Report: telegram_public, intake, no owner, reporter_chat_id 'public' (never the Telegram id), no photos/pdf",
          str((r.channel, r.status, r.owner_id, r.reporter_chat_id)))
    d = r.data or {}
    check(d.get("description") == "" and d.get("category") == ["Vehicle Collision or Damage"], "data: empty description, the collision category")
    check((d.get("reporter_name"), d.get("reporter_role"), d.get("reporter_contact")) == ("Tan Ah Huat", "Driver", "+65 9123 4567"),
          "data: reporter_name, reporter_role (label), reporter_contact (+65 9123 4567)", str(d.get("reporter_contact")))
    check((d.get("vehicle_info") or {}).get("plate_number") == "SJB8762M", "data: the plate, cleaned (SJB8762M)")
    check(d.get("report_id") in (None, ""), "data: no Our Ref")

    s = ses(rid)
    need(s is not None, "the IntakeSession is there")
    notes = s.reporter_notes
    check(s.stage == "uploading", "IntakeSession stage 'uploading'")
    check(notes.splitlines()[0] == "Sent by a member of the public through Telegram on 30 Oct 2026, 3:12 pm (Singapore time). "
          "Their own words, not checked.", "notes: the header, with the Singapore time", notes.splitlines()[0])
    check("Sent by: the car's driver" in notes and "When and where: Yesterday about 6pm" in notes
          and "What happened: A taxi hit my rear bumper" in notes, "notes: who, when and where, what happened")
    leaked = [x for x in ("Tan Ah Huat", "6591234567", "9123 4567", "91234567", "SJB8762M", "sjb", ref, "ahhuat", "Ah Huat") if x in notes]
    check(not leaked, "notes: no name, phone, plate, reference or Telegram name (what the AI reads)", str(leaked))

    rows = photo_rows(rid)
    check(len(rows) == 4, "4 photos kept: 3 JPEGs and the PNG -- not the duplicate, >5 MB, >40 MP, not-an-image, GIF, "
          "cut-off JPEG or missing file", str([Path(x.path).name for x in rows]))
    check([x.position for x in rows] == [0, 1, 2, 3], "positions 0..n-1 in the order sent")
    check([x.original_name for x in rows] == ["telegram-1.jpg", "telegram-2.jpg", "telegram-3.png", "telegram-4.jpg"],
          "original names telegram-1.jpg, telegram-2.jpg, telegram-3.png, telegram-4.jpg", str([x.original_name for x in rows]))
    check(len({x.sha256 for x in rows}) == 4, "no sha256 twice")
    check(all(Path(x.path).parent == folder(rid) and Path(x.path).exists() for x in rows), "files copied into intake/{id}/")
    check(all(files.thumbnail_path_for(x.path).exists() and files.preview_path_for(x.path).exists() for x in rows),
          "each has its thumbnail and preview")
    check(all(Path(x).exists() for x in (p[0], p[1], str(png))), "the temp files are left for the bot to delete (copied, not moved)")
    check(sorted(q.name for q in folder(rid).iterdir()) == sorted(
        [Path(x.path).name for x in rows] + [files.thumbnail_path_for(x.path).name for x in rows]
        + [files.preview_path_for(x.path).name for x in rows]), "nothing else in the folder (refused files removed)")

    row = sub(rid)
    need(row is not None, "the PublicSubmission row is there")
    check(row.reference == ref and public.iso_utc(row.submitted_at) == when.isoformat(), "reference and submitted_at")
    check(public.as_utc(row.expires_at) - public.as_utc(row.submitted_at) == timedelta(days=30), "expires_at = submitted_at + 30 days")
    check((row.submitter_name, row.submitter_role, row.submitter_phone, row.phone_kind) == ("Tan Ah Huat", "driver", "+6591234567", "sg_mobile"),
          "submitter name, role code, E.164 phone, phone_kind sg_mobile")
    check((row.plate, row.plate_looks_sg) == ("SJB8762M", True), "plate and plate_looks_sg")
    check(row.statement == f.statement and row.when_where == f.when_where, "statement and when_where verbatim")
    check((row.photo_count_sent, row.forwarded_count) == (11, 2), "photo_count_sent and forwarded_count")
    check((row.telegram_user_id, row.telegram_username, row.telegram_name) == (f.telegram_user_id, "ahhuat", "Ah Huat"),
          "telegram_user_id, @username and name kept (until filing)")
    check(row.consent_version == public.CONSENT_VERSION == "2026-10-30" and row.consent_at is not None, "consent version and time")
    check(row.taken_at is None and row.taken_by is None and row.filed_at is None, "not taken, not filed")
    h = history(rid)
    check([e.action for e in h] == ["created"] and h[0].detail == f"Sent by the public through Telegram · {ref}" and h[0].user_id is None,
          "history: one 'created' with the reference, no account", str([(e.action, e.detail) for e in h]))
    check(permissions.in_inbox(r) and public.state_of(r) == "new", "it is in the inbox (state new)")

    # "Not sure" plate, no when-and-where, a foreign number, no photos.
    rid2, _ = submit(plate=None, when_where=None, phone="+447700900123", role="workshop")
    r2, row2 = rep(rid2), sub(rid2)
    check((row2.plate, row2.plate_looks_sg, row2.when_where, row2.phone_kind) == (None, False, None, "foreign"),
          "Not sure: no plate; no when-and-where; a foreign number is 'foreign'")
    check((r2.data.get("vehicle_info") or {}).get("plate_number") is None and r2.data.get("reporter_role") == "Workshop"
          and r2.data.get("reporter_contact") == "+447700900123", "data for that one: no plate, Workshop, E.164 contact")
    check("When and where: not given" in ses(rid2).reporter_notes and "Sent by: a workshop" in ses(rid2).reporter_notes,
          "notes say 'not given' and 'a workshop'")
    check(folder(rid2).is_dir() and not photo_rows(rid2), "no photos: an empty folder, no photo rows")

    # At most MAX_PHOTOS (20) and MAX_REPORT_BYTES together.
    rid3, _ = submit(photo_paths=p[:22])
    check(len(photo_rows(rid3)) == 20, "22 photos sent: the first 20 kept")
    size = Path(p[3]).stat().st_size
    with mock.patch.object(public, "MAX_REPORT_BYTES", size * 2 + 1):
        rid4, _ = submit(photo_paths=p[3:8])
    check(len(photo_rows(rid4)) == 2, "MAX_REPORT_BYTES together: the rest skipped")

    # A form missing what it must have: ValueError, nothing saved.
    before = db_do(lambda db: db.query(Report).filter(Report.channel == P).count())
    folders_before = set((STORAGE / "intake").iterdir())
    for label, bad in (("no phone", dict(phone="not a number")), ("an unknown role", dict(role="pilot")),
                       ("no words", dict(statement="   ")), ("no name", dict(name="@@@"))):
        exc = raised(lambda: public.save_submission(form(**bad), [p[4]]))
        check(isinstance(exc, ValueError), f"{label}: ValueError", repr(exc))
    after = db_do(lambda db: db.query(Report).filter(Report.channel == P).count())
    check(before == after and set((STORAGE / "intake").iterdir()) == folders_before, "nothing saved, no folder left")


def saving_rechecks() -> None:
    """save_submission checks the caps again inside its own transaction:
    what changed since check_can_accept counts."""
    clear_public()
    p = W.photos

    def nothing_left(tg: str) -> bool:
        return db_do(lambda db: db.query(PublicSubmission).filter(PublicSubmission.telegram_user_id == tg).count()) == 0

    folders_before = set((STORAGE / "intake").iterdir())

    # Blocked between consent and Submit.
    f = form()
    db = SessionLocal()
    try:
        public.check_can_accept(db, f.telegram_user_id, f.phone)
    finally:
        db.close()
    db_do(lambda db: public.block_sender(db, f.telegram_user_id, reference="TG-test"))
    check(code_of(lambda: public.save_submission(f, [p[0], p[1]])) == "blocked", "blocked meanwhile: PublicRefused('blocked')")
    check(nothing_left(f.telegram_user_id) and set((STORAGE / "intake").iterdir()) == folders_before,
          "...no rows, and the files already copied were removed")

    # Linked meanwhile (the person connected their Telegram to an account).
    f = form()
    db_do(lambda db: db.add(ChatLink(channel="telegram", external_id=f.telegram_user_id, user_id=W.ann.id)))
    check(code_of(lambda: public.save_submission(f, [p[2]])) == "linked", "linked meanwhile: PublicRefused('linked')")
    db_do(lambda db: db.query(User).filter(User.id == W.ann.id).update({"active": False}))
    try:
        rid, _ = public.save_submission(f, [p[2]])
        check(rep(rid) is not None, "linked to a disabled account counts as not linked (chat_links.linked_user's rule)")
    finally:
        db_do(lambda db: db.query(User).filter(User.id == W.ann.id).update({"active": True}))

    # The inbox filled meanwhile: 49 open, two people pass the check, one
    # Submit wins the 50th place.
    clear_public()
    with mock.patch.object(public, "MAX_OPEN", 3), mock.patch.object(public, "MAX_PER_HOUR", 100):
        submit()
        submit()
        a, b = form(), form()
        db = SessionLocal()
        try:
            public.check_can_accept(db, a.telegram_user_id)
            public.check_can_accept(db, b.telegram_user_id)
        finally:
            db.close()
        public.save_submission(a, [])
        check(code_of(lambda: public.save_submission(b, [p[5]])) == "inbox_full",
              "inbox full between consent and Submit: the second Submit gets inbox_full")
        check(nothing_left(b.telegram_user_id), "...and nothing of it is saved")

    # Any failure inside the transaction: rolled back, folder removed.
    clear_public()
    folders_before = set((STORAGE / "intake").iterdir())
    f = form()
    with mock.patch.object(public, "new_reference", side_effect=RuntimeError("boom")):
        exc = raised(lambda: public.save_submission(f, [p[6], p[7]]))
    check(isinstance(exc, RuntimeError), "an error mid-transaction is raised", repr(exc))
    check(nothing_left(f.telegram_user_id) and db_do(lambda db: db.query(Report).filter(Report.channel == P).count()) == 0,
          "...nothing saved")
    check(set((STORAGE / "intake").iterdir()) == folders_before, "...and its folder removed")
    with mock.patch.object(public, "disk_space_low", lambda extra_gb=0.0: extra_gb == public.DISK_HEADROOM_GB):
        check(code_of(lambda: public.save_submission(form(), [p[8]])) == "disk_low",
              "disk under min_free + 2 GB: disk_low before any file is written")
    check(set((STORAGE / "intake").iterdir()) == folders_before, "...no folder made")


def caps() -> None:
    clear_public()
    now = utc_now()

    def accept(tg, phone=None, at=None):
        db = SessionLocal()
        try:
            return code_of(lambda: public.check_can_accept(db, tg, phone, at or now))
        finally:
            db.close()

    check(accept("999") is None, "an empty inbox accepts")
    check(public.MAX_OPEN == 50 and public.MAX_PER_HOUR == 20 and public.MAX_OPEN_PER_SENDER == 3
          and public.MAX_PER_SENDER_DAY == 3, "caps: 50 open, 20 an hour, 3 open and 3 a day per sender and per phone")

    # 20 an hour, from everyone.
    for i in range(20):
        submit(when=now - timedelta(minutes=59) + timedelta(seconds=i))
    check(accept("999") == "hourly_full", "20 sent in the last hour: hourly_full")
    check(accept("999", at=now + timedelta(minutes=2)) is None, "an hour after the first: accepted again")

    # 50 untaken in the inbox (sent over the last few hours, oldest first).
    clear_public()
    for i in reversed(range(50)):
        submit(when=now - timedelta(minutes=5 * (i + 13)))
    check(accept("999") == "inbox_full", "50 untaken: inbox_full")
    one = db_do(lambda db: db.query(PublicSubmission.report_id).order_by(PublicSubmission.submitted_at).first()[0])
    take(one, W.admin)
    check(accept("999") is None, "one taken: 49 untaken, accepted again")

    # Per sender: 3 not yet filed, or 3 sent in 24 hours.
    clear_public()
    for _ in range(3):
        submit("111", when=now - timedelta(hours=30))
    check(accept("111") == "sender_limit", "3 open from this Telegram (sent 30 h ago): sender_limit")
    check(accept("112") is None, "another Telegram is fine")
    first = db_do(lambda db: db.query(PublicSubmission.report_id).filter(PublicSubmission.telegram_user_id == "111").first()[0])
    db = SessionLocal()
    try:
        check(public.delete_submission(db, first, seen_owner=None, actor=None, action="discarded", detail="Test"), "one discarded")
        db.commit()
    finally:
        db.close()
    check(accept("111") is None, "2 open, none in 24 h: accepted")
    for _ in range(3):
        submit("113", when=now - timedelta(hours=20))
    ids = [r for (r,) in db_do(lambda db: db.query(PublicSubmission.report_id).filter(PublicSubmission.telegram_user_id == "113").all())]
    for rid in ids:
        take(rid, W.sam)
    db_do(lambda db: db.query(Report).filter(Report.id.in_(ids[:2])).update({"status": "confirmed"}, synchronize_session=False))
    check(accept("113") == "sender_limit", "3 sent in 24 h (2 filed, 1 taken): sender_limit")
    check(accept("113", at=now + timedelta(hours=5)) is None, "...past 24 h, with 1 open: accepted")

    # Per phone (several Telegram accounts, one number).
    clear_public()
    for i in range(3):
        submit(str(120 + i), phone="+6581112222", when=now - timedelta(hours=1, minutes=i))
    check(accept("130", "+6581112222") == "phone_limit", "3 open from this phone: phone_limit (once the phone is shared)")
    check(accept("130") is None, "the same person before sharing a phone: accepted")
    check(accept("130", "+6581112223") is None, "another phone: accepted")

    # Linked, blocked, disk.
    db_do(lambda db: db.add(ChatLink(channel="telegram", external_id="140", user_id=W.sam.id)))
    check(accept("140") == "linked", "a Telegram connected to an active account: linked")
    db_do(lambda db: public.block_sender(db, "141", actor=fresh_user(W.admin.id), reference="TG-x", now=now))
    check(accept("141") == "blocked", "a blocked Telegram: blocked")
    check(accept("141", at=now + public.BLOCK_FOR + timedelta(seconds=1)) is None, "90 days on: accepted again")
    with mock.patch.object(public, "disk_space_low", lambda extra_gb=0.0: extra_gb >= public.DISK_HEADROOM_GB):
        check(accept("150") == "disk_low", "under min_free_disk_gb + DISK_HEADROOM_GB (2) free: disk_low")
    check(public.PublicRefused("x").code == "x" and set(public.PublicRefused.CODES) == {
        "inbox_full", "hourly_full", "sender_limit", "phone_limit", "linked", "blocked", "disk_low"}, "PublicRefused codes")


def take_section() -> None:
    clear_public()
    rid, ref = submit(when=utc_now() - timedelta(days=3))
    before = ses(rid).updated_at

    # Two sessions: B looked (untaken), A takes and commits, then B tries.
    a, b = SessionLocal(), SessionLocal()
    try:
        seen = b.get(Report, rid)
        check(seen.owner_id is None, "session B sees it untaken")
        when = utc_now()
        got = public.take(a, rid, fresh_user(W.sam.id), when)
        a.commit()
        check(got == ref, "take returns the reference")
        exc = raised(lambda: public.take(b, rid, fresh_user(W.admin.id)))
        check(isinstance(exc, public.InboxError) and exc.code == "inbox_taken" and exc.status_code == 409
              and exc.detail == "Taken by S. Lee just now.", "session B: 409 inbox_taken 'Taken by S. Lee just now.'",
              f"{getattr(exc, 'code', exc)} {getattr(exc, 'detail', '')}")
        b.commit()  # B's session is still usable
    finally:
        a.close()
        b.close()
    r, row, s = rep(rid), sub(rid), ses(rid)
    check(r.owner_id == W.sam.id and public.state_of(r) == "taken" and not permissions.in_inbox(r), "it is Sam's draft now (taken)")
    check(row.taken_by == W.sam.id and public.iso_utc(row.taken_at) == when.isoformat(), "taken_at and taken_by set")
    check(public.as_utc(s.updated_at) == when and public.as_utc(s.updated_at) > public.as_utc(before),
          "the session is touched (the 7-day clock restarts)")
    check(actions(rid) == ["created", "taken"] and history(rid)[-1].detail == f"Taken from the public inbox · {ref}"
          and history(rid)[-1].user_id == W.sam.id, "history: 'taken' by Sam, with the reference")

    # Eight takers at once: exactly one wins.
    rid, _ = submit()
    results: list = []

    def taker(user_id):
        db = SessionLocal()
        try:
            public.take(db, rid, fresh_user(user_id))
            db.commit()
            results.append("won")
        except public.InboxError as exc:
            results.append(exc.code)
        except Exception as exc:
            results.append(type(exc).__name__)
        finally:
            db.close()

    threads = [threading.Thread(target=taker, args=(uid,)) for uid in [W.admin.id, W.sid.id] * 4]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check(results.count("won") == 1 and results.count("inbox_taken") == 7, "8 takers at once: one wins, 7 get inbox_taken",
          str(results))
    check(actions(rid).count("taken") == 1, "one 'taken' entry")

    # The draft cap, counted after the take: at most 5 drafts.
    clear_public()
    rid, _ = submit()
    filler_drafts(W.sid.id, 4)
    take(rid, W.sid)
    check(rep(rid).owner_id == W.sid.id, "the 5th draft: taken")
    rid2, _ = submit()
    exc = raised(lambda: take(rid2, W.sid))
    check(getattr(exc, "code", None) == "draft_limit" and exc.status_code == 409, "a 6th: 409 draft_limit", repr(exc))
    check(rep(rid2).owner_id is None and actions(rid2) == ["created"] and sub(rid2).taken_at is None,
          "...rolled back: still untaken, no history entry, no taken_at")

    # Gone, filed, not public.
    check(code_of(lambda: take("no-such-id", W.admin)) == "inbox_gone", "a made-up id: inbox_gone")
    manual = db_do(lambda db: (lambda r: (db.add(r), db.flush(), r.id)[2])(
        Report(channel="manual", reporter_chat_id="dashboard", status="intake", owner_id=None, data={})))
    check(code_of(lambda: take(manual, W.admin)) == "inbox_gone", "a staff draft with no owner: inbox_gone (not public)")
    check(rep(manual).owner_id is None, "...and untouched")
    db_do(lambda db: db.query(Report).filter(Report.id == rid2).update({"status": "confirmed"}))
    exc = raised(lambda: take(rid2, W.admin))
    check(getattr(exc, "code", None) == "inbox_filed" and exc.status_code == 409, "a filed one: inbox_filed", repr(exc))
    exc = raised(lambda: take("no-such-id", W.admin))
    check(getattr(exc, "status_code", None) == 404, "inbox_gone is a 404")


def assign_section() -> None:
    clear_public()
    rid, ref = submit()

    def assign(target: User, by: User = None):
        db = SessionLocal()
        try:
            out = public.assign(db, rid, target, by or fresh_user(W.admin.id))
            db.commit()
            return out
        finally:
            db.close()

    check(code_of(lambda: assign(fresh_user(W.ann.id))) == "owner_unvetted", "to a self-signup with no tick: owner_unvetted")
    check(code_of(lambda: assign(fresh_user(W.dee.id))) == "owner_inactive", "to a disabled account: owner_inactive")
    # Unticked after the admin's screen loaded (a stale row): refused under the lock.
    stale = fresh_user(W.tia.id)
    db_do(lambda db: db.query(User).filter(User.id == W.tia.id).update({"ai_company_key_allowed": False}))
    check(code_of(lambda: assign(stale)) == "owner_unvetted", "unticked meanwhile: owner_unvetted, checked again under the lock")
    check(rep(rid).owner_id is None and actions(rid) == ["created"], "...rolled back: still untaken")
    db_do(lambda db: db.query(User).filter(User.id == W.tia.id).update({"ai_company_key_allowed": True}))
    stale = fresh_user(W.tia.id)
    db_do(lambda db: db.query(User).filter(User.id == W.tia.id).update({"active": False}))
    check(code_of(lambda: assign(stale)) == "owner_inactive", "disabled meanwhile: owner_inactive")
    db_do(lambda db: db.query(User).filter(User.id == W.tia.id).update({"active": True}))

    got = assign(fresh_user(W.tia.id))
    check(got == ref and rep(rid).owner_id == W.tia.id, "to a ticked account: it is theirs")
    check(sub(rid).taken_by == W.tia.id and sub(rid).taken_at is not None, "taken_by is who it was given to")
    last = history(rid)[-1]
    check(last.action == "assigned" and last.detail == f"Assigned to Tia Tick (@tia) from the public inbox · {ref}"
          and last.user_id == W.admin.id, "history: 'assigned' by the admin, to whom, with the reference", last.detail)
    check(code_of(lambda: assign(fresh_user(W.sid.id))) == "inbox_taken", "assigning it again: inbox_taken")

    rid, _ = submit()
    filler_drafts(W.sid.id, 5)
    exc = raised(lambda: public.assign(SessionLocal(), rid, fresh_user(W.sid.id), fresh_user(W.admin.id)))
    check(getattr(exc, "code", None) == "draft_limit" and "Sid Staff already has 5" in exc.detail,
          "the target already has 5 drafts: draft_limit, worded for them", repr(exc))
    check(rep(rid).owner_id is None, "...still untaken")


def release_section() -> None:
    clear_public()
    rid, ref = submit()
    take(rid, W.sam)
    expires = sub(rid).expires_at

    def release(seen, actor=None, **kw):
        db = SessionLocal()
        try:
            out = public.release(db, rid, actor or fresh_user(W.sam.id), seen, **kw)
            db.commit()
            return out
        finally:
            db.close()

    check(code_of(lambda: release(W.admin.id)) == "inbox_changed", "seen owner isn't the owner: inbox_changed")
    check(code_of(lambda: release(None)) == "inbox_changed", "seen owner none: inbox_changed")
    set_stage(rid, "finalizing")
    check(code_of(lambda: release(W.sam.id)) == "inbox_finalizing", "being finalized: inbox_finalizing")
    set_stage(rid, "sorting")
    check(code_of(lambda: release(W.sam.id)) == "ai_running", "the AI's stage: ai_running")
    check(code_of(lambda: release(W.sam.id, is_running=lambda _id: True)) == "ai_running", "...and really running: ai_running")
    check(rep(rid).owner_id == W.sam.id and ses(rid).stage == "sorting", "...each rolled back: still Sam's")
    got = release(W.sam.id, is_running=lambda _id: False)
    check(got == ref and rep(rid).owner_id is None, "a stage a restart left behind (is_running says no): released")
    check(ses(rid).stage == "failed" and ses(rid).error == public.STALE_RUN, "...that stage is now 'failed', so Retry works")
    row = sub(rid)
    check(row.taken_at is None and row.taken_by is None and row.expires_at == expires, "taken_* cleared; expires_at unchanged")
    check(permissions.in_inbox(rep(rid)) and history(rid)[-1].action == "returned_to_inbox" and history(rid)[-1].detail == ref,
          "back in the inbox; history 'returned_to_inbox' with the reference")

    # Two sessions: both looked while it was Sam's; one releases; the other
    # releases too, on what it saw.
    take(rid, W.sam)
    a, b = SessionLocal(), SessionLocal()
    try:
        seen = b.get(Report, rid).owner_id
        public.release(a, rid, fresh_user(W.sam.id), W.sam.id)
        a.commit()
        check(code_of(lambda: public.release(b, rid, fresh_user(W.admin.id), seen)) == "inbox_changed",
              "the second release, on what it saw: inbox_changed")
    finally:
        a.close()
        b.close()


def delete_section() -> None:
    clear_public()
    rid, ref = submit(photo_paths=W.photos[:2])
    admin = fresh_user(W.admin.id)

    def discard(report_id, seen, **kw):
        db = SessionLocal()
        try:
            ok = public.delete_submission(db, report_id, seen_owner=seen, actor=admin, action="discarded", **kw)
            stage = db.query(IntakeSession.stage).filter(IntakeSession.report_id == report_id).scalar()
            err = None if ok else public.refusal(db, report_id)
            db.commit()
            return ok, stage, err
        finally:
            db.close()

    ok, _, err = discard(rid, W.sam.id, detail="Spam")
    check(not ok and err.code == "inbox_changed", "seen as Sam's but untaken: False, inbox_changed")
    check(rep(rid) is not None and ses(rid).stage == "uploading", "...nothing changed (the stage claim rolled back too)")
    set_stage(rid, "sorting")
    ok, _, err = discard(rid, None, detail="Spam")
    check(not ok and err.code == "ai_running", "the AI's stage: False, ai_running")
    set_stage(rid, "finalizing")
    ok, _, err = discard(rid, None, detail="Spam")
    check(not ok and err.code == "inbox_finalizing", "being finalized: False, inbox_finalizing")
    set_stage(rid, "uploading")
    tg = sub(rid).telegram_user_id
    ok, _, _ = discard(rid, None, detail=public.discard_detail("spam"), block=True, now=utc_now())
    check(ok, "untaken, seen as untaken: True")
    check(gone_entirely(rid), "report, session, photo and submission rows gone")
    check(folder(rid).exists(), "the folder is the caller's to remove, after its commit")
    intake_photos.remove_intake_dir(rid)
    last = history(rid)[-1]
    check(last.action == "discarded" and last.detail == f"{ref} · Spam" and last.user_id == W.admin.id,
          "history: 'discarded' 'TG-... · Spam', by the admin", last.detail)
    block = db_do(lambda db: db.get(PublicBlock, tg))
    check(block is not None and block.reference == ref and block.blocked_by == W.admin.id
          and public.as_utc(block.until) - public.as_utc(block.blocked_at) == timedelta(days=90),
          "block=True: the sender is blocked for 90 days, with the reference")
    db = SessionLocal()
    try:
        check(public.refusal(db, rid).code == "inbox_gone", "afterwards: inbox_gone")
    finally:
        db.close()

    # A taken one: by its owner's id; a filed one never.
    rid, ref = submit()
    take(rid, W.sam)
    ok, _, err = discard(rid, None, detail="Test")
    check(not ok and err.code == "inbox_changed", "taken, seen as untaken: False, inbox_changed")
    ok, _, _ = discard(rid, W.sam.id, detail=public.discard_detail("duplicate", "TG-261029-Q8T2"))
    check(ok and gone_entirely(rid) and history(rid)[-1].detail == f"{ref} · Duplicate of TG-261029-Q8T2",
          "taken, seen as Sam's: deleted, 'Duplicate of TG-...'")
    rid, _ = submit()
    db_do(lambda db: db.query(Report).filter(Report.id == rid).update({"status": "confirmed"}))
    ok, _, err = discard(rid, None, detail="Test")
    check(not ok and err.code == "inbox_filed" and rep(rid) is not None, "filed: False, inbox_filed, kept")

    # Two sessions: discard on what it saw (untaken) vs a take that committed.
    rid, _ = submit()
    a, b = SessionLocal(), SessionLocal()
    try:
        seen = b.get(Report, rid).owner_id
        public.take(a, rid, fresh_user(W.sid.id))
        a.commit()
        ok = public.delete_submission(b, rid, seen_owner=seen, actor=admin, action="discarded", detail="Spam")
        b.commit()
    finally:
        a.close()
        b.close()
    check(not ok and rep(rid).owner_id == W.sid.id, "discard after a take it didn't see: False; the take stands")

    check(public.discard_detail("not_vehicle") == "Not a vehicle report" and public.discard_detail("other", note="  sent twice \n by\tmistake ")
          == "Other · sent twice by mistake", "discard_detail: labels, the note on one line")
    check(len(public.discard_detail("other", note="x" * 500)) <= len("Other · ") + public.MAX_DISCARD_NOTE, "the note at most 120 characters")
    check(isinstance(raised(lambda: public.discard_detail("rude")), ValueError), "an unknown reason: ValueError")
    check(set(public.DISCARD_REASONS) == {"spam", "duplicate", "test", "not_vehicle", "other"}, "the five reasons")


def sweep_section() -> None:
    clear_public()
    sent = utc_now() - timedelta(days=40)
    rid, ref = submit(when=sent, photo_paths=W.photos[:1])
    db = SessionLocal()
    try:
        check(public.sweep_untaken(db, sent + timedelta(days=29, hours=23)) == [], "day 29: kept")
        check(rep(rid) is not None, "...still there")
        removed = public.sweep_untaken(db, sent + timedelta(days=30))
    finally:
        db.close()
    check(removed == [rid] and gone_entirely(rid) and not folder(rid).exists(), "day 30: deleted, rows and folder (committed)")
    last = history(rid)[-1]
    check(last.action == "expired" and last.detail == f"{ref} · Not taken within 30 days" and last.user_id is None,
          "history: 'expired' with the reference only", last.detail)

    # The 7-day idle loop leaves untaken public reports alone.
    rid, _ = submit(when=utc_now() - timedelta(days=12))
    out = cleanup.sweep(now=utc_now())
    check(rep(rid) is not None and rid not in out["drafts"], "12 days old, untaken: the 7-day loop leaves it")

    # Taken on day 25: kept at day 30; then the 7-day rule from the take.
    sent = utc_now() - timedelta(days=40)
    rid, ref = submit(when=sent)
    take(rid, W.sam, sent + timedelta(days=25))
    db = SessionLocal()
    try:
        check(public.sweep_untaken(db, sent + timedelta(days=30)) == [], "taken on day 25: the 30-day sweep keeps it")
    finally:
        db.close()
    check(rid not in cleanup.sweep(now=sent + timedelta(days=31))["drafts"] and rep(rid) is not None,
          "6 days after the take: kept")
    out = cleanup.sweep(now=sent + timedelta(days=32, hours=1))
    check(rid in out["drafts"] and gone_entirely(rid) and not folder(rid).exists(), "7 days after the take: the idle rule removes it, "
          "with its submission row")
    check(history(rid)[-1].action == "expired" and history(rid)[-1].detail == f"{ref} · Not worked on for 7 days",
          "history: 'expired', idle")

    # Released after its 30 days: goes at the next sweep (L8).
    rid, _ = submit(when=utc_now() - timedelta(days=31))
    take(rid, W.sam)
    db = SessionLocal()
    try:
        public.release(db, rid, fresh_user(W.sam.id), W.sam.id)
        db.commit()
    finally:
        db.close()
    out = cleanup.sweep()
    check(rid in out["public_expired"] and gone_entirely(rid), "released after 30 days: the next sweep deletes it")

    # Take vs sweep, two sessions: the sweep found it due; a take commits; the sweep's delete finds nothing.
    rid, _ = submit(when=utc_now() - timedelta(days=31))
    a, b = SessionLocal(), SessionLocal()
    try:
        due = [x for (x,) in b.query(PublicSubmission.report_id).join(Report, Report.id == PublicSubmission.report_id)
               .filter(PublicSubmission.expires_at <= utc_now(), public.untaken_clause()).all()]
        check(rid in due, "the sweep sees it due")
        public.take(a, rid, fresh_user(W.sid.id))
        a.commit()
        ok = public.delete_submission(b, rid, seen_owner=None, actor=None, action="expired", detail=public.EXPIRED_DETAIL)
        b.commit()
    finally:
        a.close()
        b.close()
    check(not ok and rep(rid).owner_id == W.sid.id, "a take committed between: the sweep deletes nothing")

    # And racing for real: due reports, the sweep (oldest first) and a taker
    # (newest first) let go at the same moment, a few rounds.
    all_ids: list[str] = []
    taken: list[str] = []
    swept: list[str] = []
    for _ in range(4):
        clear_public()
        ids = [submit(when=utc_now() - timedelta(days=31))[0] for _ in range(5)]
        all_ids += ids
        start = threading.Barrier(2)

        def taker():
            start.wait()
            for x in reversed(ids):
                try:
                    take(x, W.sam)
                    taken.append(x)
                except public.InboxError:
                    pass

        def sweeper():
            start.wait()
            db = SessionLocal()
            try:
                swept.extend(public.sweep_untaken(db))
            finally:
                db.close()

        threads = [threading.Thread(target=taker), threading.Thread(target=sweeper)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check(all(rep(x) is not None and rep(x).owner_id == W.sam.id for x in ids if x in taken)
              and all(gone_entirely(x) for x in ids if x in swept), "every taken one is still there and Sam's; every swept one is gone")
    check(set(taken) | set(swept) == set(all_ids) and not set(taken) & set(swept),
          f"20 due reports raced: each either taken ({len(taken)}) or swept ({len(swept)}), never both", f"{taken} {swept}")


def finalize_section() -> None:
    clear_public()
    rid, ref = submit(telegram_username="someone", telegram_name="Some One")
    tg = sub(rid).telegram_user_id
    take(rid, W.sam)

    def finalize():
        db = SessionLocal()
        try:
            db.query(Report).filter(Report.id == rid).update({"status": "confirmed"})
            out = public.on_finalize(db, rid, utc_now())
            db.commit()
            return out
        finally:
            db.close()

    check(finalize() == ref, "on_finalize returns the reference")
    row, r = sub(rid), rep(rid)
    check(row.filed_at is not None, "filed_at set")
    check((row.telegram_user_id, row.telegram_username, row.telegram_name) == (None, None, None), "telegram_* cleared")
    check(r.reporter_chat_id == "public" and (row.submitter_name, row.submitter_phone, row.statement) != (None, None, None),
          "reporter_chat_id 'public'; the name, phone and words stay with the case")
    check(public.state_of(r) == "filed" and not permissions.is_public(r), "state filed; no longer a public draft")
    db = SessionLocal()
    try:
        check(public.withdraw(db, rid, tg) == "none" and public.open_for_sender(db, tg) == [], "/withdraw after filing: none, nothing listed")
        info = public.submission_info(db, r)
        check(info["state"] == "filed" and info["filed_at"] and tg not in str(info), "submission_info: filed, never the Telegram id")
        check(public.on_finalize(db, "no-such-id") is None, "on_finalize of a report that isn't public: None")
    finally:
        db.close()


def withdraw_section() -> None:
    clear_public()
    rid, ref = submit("500", photo_paths=W.photos[:1])
    rid_taken, ref_taken = submit("500")
    other, _ = submit("501")
    take(rid_taken, W.sam)

    db = SessionLocal()
    try:
        listed = public.open_for_sender(db, "500")
        check([(x["id"], x["reference"], x["taken"], x["withdraw_requested"]) for x in listed]
              == [(rid, ref, False, False), (rid_taken, ref_taken, True, False)], "open_for_sender: theirs, oldest first, taken flagged",
              str(listed))
        check(all(x["submitted_at"].tzinfo is not None for x in listed), "submitted_at in UTC")
        # A forged button: someone else's report id.
        check(public.withdraw(db, other, "500") == "none", "someone else's report: none")
        db.commit()
        check(rep(other) is not None and actions(other) == ["created"], "...nothing deleted, nothing noted")
        check(public.withdraw(db, "no-such-id", "500") == "none", "a made-up id: none")
        out = public.withdraw(db, rid, 500)
        db.commit()
    finally:
        db.close()
    check(out == "deleted" and gone_entirely(rid), "their untaken report: deleted (rows)")
    intake_photos.remove_intake_dir(rid)
    check(history(rid)[-1].action == "withdrawn" and history(rid)[-1].detail == f"{ref} · Withdrawn by the sender",
          "history: 'withdrawn' with the reference")
    db = SessionLocal()
    try:
        first = public.withdraw(db, rid_taken, "500")
        db.commit()
        second = public.withdraw(db, rid_taken, "500")
        db.commit()
        listed = public.open_for_sender(db, "500")
    finally:
        db.close()
    check(first == "taken" and second == "taken", "their taken report: taken, twice")
    check(rep(rid_taken).owner_id == W.sam.id and sub(rid_taken).withdraw_requested_at is not None, "...kept, withdraw_requested_at set")
    check(actions(rid_taken).count("withdraw_requested") == 1
          and history(rid_taken)[-1].detail == f"{ref_taken} · The sender asked to withdraw it", "...noted in its history once")
    check(listed and listed[0]["withdraw_requested"], "open_for_sender shows the request")

    # Take vs withdraw, two sessions each way.
    rid, _ = submit("502")
    a, b = SessionLocal(), SessionLocal()
    try:
        b.get(Report, rid)
        check(public.withdraw(a, rid, "502") == "deleted", "withdraw first...")
        a.commit()
        check(code_of(lambda: public.take(b, rid, fresh_user(W.sid.id))) == "inbox_gone", "...then the take: inbox_gone")
    finally:
        a.close()
        b.close()
    rid, _ = submit("503")
    a, b = SessionLocal(), SessionLocal()
    try:
        b.get(Report, rid)
        public.take(a, rid, fresh_user(W.sid.id))
        a.commit()
        check(public.withdraw(b, rid, "503") == "taken", "take first, then withdraw: taken (noted)")
        b.commit()
    finally:
        a.close()
        b.close()
    check(rep(rid).owner_id == W.sid.id and sub(rid).withdraw_requested_at is not None, "...the take stands, the request is noted")


def blocks_section() -> None:
    clear_public()
    now = utc_now()
    db = SessionLocal()
    try:
        public.block_sender(db, 600, actor=fresh_user(W.admin.id), reference="TG-260901-AAAA", now=now)
        db.commit()
        check(public.is_blocked(db, "600", now) and public.is_blocked(db, 600, now + timedelta(days=89)), "blocked, for 90 days")
        check(not public.is_blocked(db, "600", now + timedelta(days=90)) and not public.is_blocked(db, "601", now),
              "not after 90 days; not another account")
        public.block_sender(db, "600", reference="TG-260902-BBBB", now=now + timedelta(days=80))
        db.commit()
        row = db.get(PublicBlock, "600")
        check(row.reference == "TG-260902-BBBB" and public.as_utc(row.until) == now + timedelta(days=170),
              "blocked again: 90 days from the second time")
        check(public.sweep_blocks(db, now + timedelta(days=100)) == 0, "sweep_blocks keeps a block still running")
        check(public.sweep_blocks(db, now + timedelta(days=171)) == 1, "...and removes it once run out")
        db.commit()
    finally:
        db.close()


def permissions_section() -> None:
    clear_public()
    rid, _ = submit()
    r = rep(rid)
    admin, sam, sid, ann, tia = (fresh_user(u.id) for u in (W.admin, W.sam, W.sid, W.ann, W.tia))
    check(permissions.PUBLIC_CHANNEL == "telegram_public" and public.PUBLIC_CHANNEL == permissions.PUBLIC_CHANNEL,
          "PUBLIC_CHANNEL is permissions'")
    check(permissions.is_public(r) and permissions.in_inbox(r), "is_public, in_inbox")
    check(permissions.can_take(admin, r) and permissions.can_take(sam, r) and not permissions.can_take(ann, r)
          and not permissions.can_take(tia, r), "can_take: admin and see-all staff; not plain or company-key-only")
    check(not permissions.can_edit_case(admin, r) and not permissions.can_edit_case(sam, r),
          "in the inbox nobody may edit it, admins neither (no autosave, no AI)")
    check(not permissions.can_delete_case(admin, r), "...nor DELETE it (Discard, with a reason)")
    check(permissions.can_discard(admin, r) and permissions.can_discard(sam, r) and not permissions.can_discard(ann, r),
          "can_discard in the inbox: as can_take")
    check(not permissions.can_release(admin, r), "can_release: not while in the inbox")
    perms = permissions.case_permissions(admin, r)
    check(set(perms) == {"edit", "sign_off", "reopen", "delete", "assign", "release"} and perms["release"] is False
          and perms["edit"] is False and perms["delete"] is False, "case_permissions gains release", str(perms))

    take(rid, W.sam)
    r = rep(rid)
    check(permissions.can_edit_case(sam, r) and permissions.can_edit_case(admin, r) and not permissions.can_edit_case(sid, r),
          "taken: its owner and admins may edit; other see-all staff may not (a draft)")
    check(permissions.can_delete_case(sam, r) and permissions.can_delete_case(admin, r), "taken: the owner or an admin may delete")
    check(permissions.can_discard(sam, r) and permissions.can_discard(admin, r) and not permissions.can_discard(sid, r),
          "can_discard once taken: its owner or an admin")
    check(permissions.can_release(sam, r) and permissions.can_release(admin, r) and not permissions.can_release(sid, r),
          "can_release: its owner or an admin")
    check(not permissions.can_take(admin, r), "can_take: not once taken")
    check(permissions.case_permissions(sam, r)["release"] is True, "case_permissions: release for the owner")

    # in_inbox fails closed: a row without .channel raises.
    row = SimpleNamespace(id="x", owner_id=None, status="intake")
    check(isinstance(raised(lambda: permissions.in_inbox(row)), AttributeError), "in_inbox without .channel: raises (fails closed)")
    check(isinstance(raised(lambda: permissions.can_edit_case(admin, row)), AttributeError),
          "can_edit_case without .channel: raises, never passes")
    legacy = SimpleNamespace(id="y", owner_id=None, status="intake", channel=None)
    check(not permissions.in_inbox(legacy) and permissions.can_edit_case(admin, legacy), "a NULL channel is not public")
    access = _case_access(rid)
    check(access is not None and access.channel == P and access.owner_id == W.sam.id, "deps._case_access selects channel too")
    filed = SimpleNamespace(id="z", owner_id=None, status="confirmed", channel=P)
    check(not permissions.is_public(filed) and permissions.can_edit_case(admin, filed), "a filed public case is an ordinary case")


def ai_keys_section() -> None:
    clear_public()
    db_do(lambda db: db.add(ApiKey(provider="gemini", key="AIzaCompanyKeyFromTheSettingsPage000000", label="Settings row")))

    def give(user_id, key, status="ok", billing=True, checked=None):
        def go(db):
            row = keys.save_key(db, user_id, key, secretbox.fingerprint(key), status, None, billing_confirmed=billing)
            if checked is not None:
                row.last_checked_at = checked
        db_do(go)

    kim_key, tia_key, quinn_key, una_key = ("AIzaKim" + "k" * 32, "AIzaTia" + "t" * 32, "AIzaQuinn" + "q" * 30, "AIzaUna" + "u" * 32)
    give(W.kim.id, kim_key)
    give(W.tia.id, tia_key, billing=False)
    give(W.quinn.id, quinn_key, status="quota", checked=utc_now())
    give(W.una.id, una_key, status="untested")

    def labels(user_id, public_case=False, actor_flag=False):
        actor = AiActor.for_user(user_id, "dashboard", public_case=actor_flag)
        try:
            return [k.label for k in keys.resolve_keys(actor, public_case=public_case)]
        except AiUnavailable as exc:
            return (exc.code, exc.message)

    company = ["company #1", "company #2"]
    missing = ("ai_key_missing", keys.PUBLIC_KEY_ONLY)
    check(keys.PUBLIC_KEY_ONLY == "Public reports can only use your own (billed) AI key until an admin confirms the company key "
          "is billed. You can still fill it in by hand.", "the decision's exact words")
    db = SessionLocal()
    try:
        check(public.company_key_billed(db) is False, "company_key_billed: false by default")
    finally:
        db.close()

    # Not billed (the default).
    check(labels(W.kim.id, True) == [f"own ..{kim_key[-4:]}"], "public, not billed: Kim's own billed key")
    check(labels(W.admin.id, True) == missing, "public, not billed: an admin with no own key gets ai_key_missing, the public words")
    check(labels(W.admin.id) == company, "...the same admin on any other case: the company keys (unchanged)")
    check(labels(W.tia.id, True) == missing, "public: an own key not saved with billing confirmed: ai_key_missing")
    check(labels(W.tia.id) == [f"own ..{tia_key[-4:]}"] + company, "...Tia elsewhere: own, then company (unchanged)")
    check(labels(W.quinn.id, True) == missing, "public: an own key out of quota: ai_key_missing")
    give(W.quinn.id, quinn_key, status="quota", checked=utc_now() - timedelta(hours=2))
    check(labels(W.quinn.id, True) == missing, "public: an own key being retried after an hour (status quota): still missing")
    check(labels(W.quinn.id) == [f"own ..{quinn_key[-4:]}"], "...elsewhere that one is retried (unchanged)")
    check(labels(W.una.id, True) == [f"own ..{una_key[-4:]}"], "public: an untested, billed own key is used")
    check(labels(W.ann.id, True) == missing, "public: no key at all: ai_key_missing, the public words")
    check(labels(W.kim.id, actor_flag=True) == [f"own ..{kim_key[-4:]}"] and labels(W.admin.id, actor_flag=True) == missing,
          "the actor can carry it (AiActor.for_user(..., public_case=True)): every call of a run keeps the rule")
    check(labels(W.dee.id, True)[0] == "ai_account_off", "a disabled account: ai_account_off, as before")
    with mock.patch.object(secretbox, "state", lambda: secretbox.BROKEN):
        check(labels(W.kim.id, True) == missing, "own keys unreadable here: missing (never a company key)")
    check(code_of(lambda: keys.resolve_keys(AiActor.cli(), public_case=True)) == "ai_key_missing", "scripts (cli) on a public case: missing")
    check(code_of(lambda: keys.ensure_ai_ready(AiActor.for_user(W.admin.id, "dashboard"), public_case=True)) == "ai_key_missing",
          "ensure_ai_ready(actor, public_case=True) refuses the same")
    check(code_of(lambda: keys.ensure_ai_ready(AiActor.for_user(W.admin.id, "dashboard"))) is None,
          "ensure_ai_ready without it: unchanged")
    check(AiActor.for_user(W.kim.id, "dashboard") == AiActor(W.kim.id, "dashboard") and AiActor.cli().cli is True,
          "AiActor still compares as before")

    # Ticked: the normal P4 rules.
    db_do(lambda db: public.save_setting(db, public.COMPANY_KEY_BILLED, True))
    check(labels(W.admin.id, True) == company, "billed: an admin on a public case gets the company keys")
    check(labels(W.tia.id, True) == [f"own ..{tia_key[-4:]}"] + company, "billed: own first, then company, as P4")
    check(labels(W.kim.id, True) == [f"own ..{kim_key[-4:]}"], "billed: Kim (no tick) her own key")
    check(labels(W.ann.id, True)[0] == "ai_key_missing" and labels(W.ann.id, True)[1] == keys.MESSAGES["ai_key_missing"],
          "billed: no key and no tick: ai_key_missing with the usual words")
    check(labels(W.quinn.id, True) == [f"own ..{quinn_key[-4:]}"], "billed: a quota key retried after an hour, as P4")
    db_do(lambda db: public.save_setting(db, public.COMPANY_KEY_BILLED, False))
    check(labels(W.admin.id, True) == missing, "unticked again: back to own keys only")

    # The settings helpers.
    db = SessionLocal()
    try:
        check(public.settings_dict(db) == {"public_contact_phone": "", "dpo_contact": "", "company_key_billed": False},
              "settings: empty by default", str(public.settings_dict(db)))
        public.save_setting(db, public.PUBLIC_CONTACT_PHONE, "  6123\n4567  ")
        public.save_setting(db, public.DPO_CONTACT, "dpo@firm.sg")
        public.save_setting(db, public.COMPANY_KEY_BILLED, "true")
        db.commit()
        check((public.public_contact_phone(db), public.dpo_contact(db), public.company_key_billed(db)) == ("6123 4567", "dpo@firm.sg", True),
              "saved: one line, trimmed; the tick as true")
        check(isinstance(raised(lambda: public.save_setting(db, "company_name", "x")), ValueError), "another key: ValueError")
        public.save_setting(db, public.COMPANY_KEY_BILLED, "yes please")
        public.save_setting(db, public.PUBLIC_CONTACT_PHONE, "")
        db.commit()
        check(public.company_key_billed(db) is False and public.public_contact_phone(db) == "", "anything but true is false; empty clears")
        check(public.SETTING_KEYS == ("public_contact_phone", "dpo_contact", "company_key_billed"), "the three keys")
    finally:
        db.close()


def cleanup_section() -> None:
    clear_public()
    now = utc_now()
    # An orphan submission row (its report gone some other way).
    db_do(lambda db: db.add(PublicSubmission(report_id="orphanrow", reference="TG-260901-CCCC", submitted_at=now, expires_at=now,
                                             consent_at=now, consent_version="x", submitter_name="x", submitter_role="other",
                                             submitter_phone="+6560000001", phone_kind="sg_other", statement="x")))
    db_do(lambda db: public.block_sender(db, "700", now=now - timedelta(days=91)))
    db_do(lambda db: public.block_sender(db, "701", now=now))
    # intake/ folders: an old one with no draft, a new one with no draft,
    # and an old one whose draft is there.
    old, new = STORAGE / "intake" / "oldorphan", STORAGE / "intake" / "neworphan"
    for f in (old, new):
        f.mkdir(parents=True, exist_ok=True)
        (f / "x.jpg").write_bytes(b"x")
    rid, _ = submit(photo_paths=W.photos[:1])
    long_ago = time.time() - 25 * 3600
    for f in (old, folder(rid)):
        os.utime(f, (long_ago, long_ago))
    out = cleanup.sweep(now=now)
    for key in ("drafts", "tmp_files", "sessions", "links", "telegram_codes", "public_expired", "public_orphans", "public_blocks", "intake_folders"):
        check(key in out, f"sweep returns {key}")
    check(out["public_orphans"] == 1 and sub("orphanrow") is None, "an orphan submission row: removed")
    check(out["public_blocks"] == 1 and db_do(lambda db: db.get(PublicBlock, "701")) is not None, "a run-out block removed, a running one kept")
    check(not old.exists() and new.exists(), "an intake/ folder with no draft: removed after 24 h, kept before")
    check(folder(rid).exists() and out["intake_folders"] == 1, "a folder whose draft is there: kept, however old")
    shutil.rmtree(new, ignore_errors=True)


def disk_and_photos() -> None:
    gb = 1024 ** 3
    usage = SimpleNamespace(total=100 * gb, used=0, free=int((settings.min_free_disk_gb + 1) * gb))
    with mock.patch.object(files.shutil, "disk_usage", lambda _p: usage):
        check(files.disk_space_low() is False, "disk_space_low(): min_free + 1 GB free is enough")
        check(files.disk_space_low(2) is True and files.disk_space_low(extra_gb=0.5) is False, "disk_space_low(extra_gb): the bar moves up")
    check(intake_photos.is_readable_image(W.photos[0]) and not intake_photos.is_readable_image(PHOTOS / "notimage.jpg"),
          "photos.is_readable_image: a JPEG yes, text no")
    check(api_intake._is_readable_image is intake_photos.is_readable_image, "api/intake.py's name still works (re-exported)")


def helpers() -> None:
    n = public.normalize_phone
    check(n("6591234567") == "+6591234567" and n("+65 9123-4567") == "+6591234567" and n("(65) 9123 4567") == "+6591234567",
          "normalize_phone: digits with or without +, spaces and dashes")
    check(n("12") is None and n("abc") is None and n(None) is None and n("+0123456789") is None, "...not a number: None")
    check(public.is_anonymous_number("+88812345678") and not public.is_anonymous_number("+6591234567"), "+888 is anonymous")
    check([public.phone_kind(x) for x in ("+6591234567", "+6581234567", "+6561234567", "+447700900123")]
          == ["sg_mobile", "sg_mobile", "sg_other", "foreign"], "phone_kind")
    check(public.phone_display("+6591234567") == "+65 9123 4567" and public.phone_display("+447700900123") == "+447700900123",
          "phone_display")
    check(public.mask_phone("+6591234567") == "+65 •••• 4567" and public.mask_phone("+447700901180") == "+44 •••• 1180", "mask_phone")
    check(public.phone_links("+6591234567") == {"call": "tel:+6591234567", "whatsapp": "https://wa.me/6591234567"}, "phone_links")
    check(public.clean_plate(" sjb 8762-m ") == "SJB8762M" and public.clean_plate("x") is None and public.clean_plate("ＳＢＡ１２３４Ａ") == "SBA1234A",
          "clean_plate: upper case, no spaces or dashes, full-width folded; too short: None")
    check(public.plate_looks_sg("SBA1234A") and public.plate_looks_sg("E12") and not public.plate_looks_sg("12345678"), "plate_looks_sg")
    f = SimpleNamespace(role="owner", when_where=None, statement="Hit\x00 while parked.\nSecond line.")
    notes = public.reporter_notes_for(f, datetime(2026, 10, 30, 16, 5, tzinfo=timezone.utc))
    check("on 31 Oct 2026, 12:05 am (Singapore time)" in notes and "Sent by: the car's owner" in notes
          and "What happened: Hit while parked.\nSecond line." in notes, "reporter_notes_for: 12:05 am, control characters out", notes)
    check(public.ROLES == {"driver": "Driver", "owner": "Car owner", "workshop": "Workshop", "other": "Someone else"}, "ROLES")

    clear_public()
    rid, ref = submit("800", phone="+6599990000")
    rid2, ref2 = submit("801", phone="+6599990000")
    rid3, _ = submit("800")
    take(rid3, W.sam)
    db = SessionLocal()
    try:
        info = public.submission_info(db, db.get(Report, rid))
        check(set(info) == {"reference", "state", "submitted_at", "expires_at", "consent_at", "consent_version", "sender", "plate",
                            "plate_looks_sg", "when_where", "statement", "photo_count_sent", "forwarded_count", "taken_at",
                            "taken_by", "filed_at", "withdraw_requested_at"}, "submission_info: its keys", str(sorted(info)))
        check(info["sender"]["phone_display"] == "+65 9999 0000" and info["sender"]["phone_masked"] == "+65 •••• 0000"
              and info["sender"]["links"]["whatsapp"] == "https://wa.me/6599990000" and info["sender"]["phone_verified"] is True,
              "submission_info: phone shown, masked, linked, verified")
        check("800" not in str({k: v for k, v in info.items() if k not in ("submitted_at", "expires_at", "consent_at")})
              and "telegram_user_id" not in str(info), "submission_info: never the Telegram user id")
        check(info["state"] == "new" and info["taken_by"] is None, "state new, nobody has it")
        taken = public.submission_info(db, db.get(Report, rid3))
        check(taken["state"] == "taken" and taken["taken_by"]["id"] == W.sam.id, "taken: taken_by is who has it")
        check(public.submission_info(db, SimpleNamespace(id=rid, channel="manual")) is None, "not public: None")
        same = public.same_sender(db, rid)
        check([x["id"] for x in same] == [rid2, rid3] and [x["state"] for x in same] == ["new", "taken"],
              "same_sender: the same phone or the same Telegram, with states", str(same))
        check(public.references_for(db, [rid, rid2, "nope"]) == {rid: ref, rid2: ref2}, "references_for: batched")
        check(public.delete_row(db, rid2) == ref2 and db.get(PublicSubmission, rid2) is None, "delete_row returns the reference")
        db.rollback()
    finally:
        db.close()


def nothing_leaked() -> None:
    entries = db_do(lambda db: db.query(AuditEntry.detail).all())
    details = [d for (d,) in entries if d]
    leaks = sorted({p for p in PERSONAL for d in details if p and p in d})
    check(not leaks, f"no sender's name, phone or words in any of {len(details)} history entries", str(leaks[:5]))
    leaks = sorted({p for p in PERSONAL for line in LOG_LINES if p and p in line})
    check(not leaks, f"...nor in any of {len(LOG_LINES)} log lines", str(leaks[:5]))


def main() -> int:
    for name, fn in (
        ("setup", setup),
        ("tables: public_submissions and public_blocks, rules inside them", tables),
        ("reference: TG-YYMMDD-XXXX, Singapore date, never a link code", reference),
        ("clauses: untaken / not untaken, NULL-safe", clauses),
        ("save_submission: rows, files, notes, data", saving),
        ("save_submission checks again inside its transaction", saving_rechecks),
        ("caps: hourly, inbox, per sender, per phone, linked, blocked, disk", caps),
        ("take: one conditional UPDATE; two sessions; 8 at once; the draft cap", take_section),
        ("assign: vetted and active, checked again under the lock", assign_section),
        ("release: only as seen; refused while finalizing or the AI runs", release_section),
        ("delete_submission: claim the stage, then delete as seen", delete_section),
        ("sweep: day 29 kept, day 30 gone; the 7-day rule only once taken; take vs sweep", sweep_section),
        ("on_finalize: filed_at, telegram_* cleared, reporter_chat_id 'public'", finalize_section),
        ("withdraw: deleted, taken, none; forged buttons; take vs withdraw", withdraw_section),
        ("blocks: 90 days, again, swept", blocks_section),
        ("permissions: the inbox rules; fails closed", permissions_section),
        ("AI keys on public reports: own billed key only until company_key_billed", ai_keys_section),
        ("cleanup: orphan rows, blocks, folders; the return value", cleanup_section),
        ("disk_space_low(extra_gb), photos.is_readable_image", disk_and_photos),
        ("phone, plate, notes and display helpers", helpers),
        ("no name, phone or words in the history or the logs", nothing_leaked),
    ):
        run_section(name, fn)
        if name == "setup" and FAILURES:
            break
    print(f"\ntest_public_core: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  [{section}] {label}")
    if FAILURES:
        return 1
    print("\nALL PUBLIC CORE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
