"""Tests for public reports through the Telegram bot (P6): the public form
(app/channels/telegram_public.py) behind telegram.py's gate. Agent A's
checks (plans/2026-10-30-public-reporter/agentA_bot_report.md §7) as the
build brief (p6_build_brief.md §2.4, §2.8) and p6_decisions.md changed them,
plus the brief's: a file is refused, staff are served while 30 strangers
flood a real concurrent_updates app, the reply budget holds, a forged
withdraw button deletes nothing, and no name, phone or statement is logged.

    python scripts/test_telegram_public.py

Offline: fake Telegram updates and downloads, a temporary database and
storage folder; a real build_app() for the flood, the error handler and the
handler filters. build_draft, render_pdf, ensure_ai_ready, resolve_keys and
draft_report RAISE if anything calls them: the public flow runs no AI and
makes no PDF. scripts/test_public_e2e.py (written from the spec) drives the
same flow through the API as well.
"""
import asyncio
import io
import itertools
import logging
import os
import re
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="telegram-public-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake-company-key"
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:fake-token-for-tests"
os.environ["PUBLIC_BASE_URL"] = "https://carlink.test/"
# The general disk guard has its own test; the public flow's headroom is
# patched where it is checked.
os.environ["MIN_FREE_DISK_GB"] = "0"

from PIL import Image  # noqa: E402
from sqlalchemy import event  # noqa: E402
from telegram import Update  # noqa: E402

import app.channels.telegram as tg  # noqa: E402
import app.channels.telegram_public as tp  # noqa: E402
from app.channels import tg_common  # noqa: E402
from app.auth.chat_links import new_code  # noqa: E402
from app.auth.core import utc_now  # noqa: E402
from app.conversation import state  # noqa: E402
from app.conversation.state import PublicForm, PublicStep  # noqa: E402
from app.intake import public  # noqa: E402
from app.reports.db import SessionLocal, engine, init_db  # noqa: E402
from app.reports.models import (  # noqa: E402
    AuditEntry,
    ChatLink,
    IntakePhoto,
    IntakeSession,
    PublicSubmission,
    Report,
    User,
)
from app.reports.schema import SecurityIncidentDraft  # noqa: E402
from app.storage.files import preview_path_for, thumbnail_path_for, tmp_dir  # noqa: E402

BASE = "https://carlink.test"
REF = re.compile(r"\bTG-\d{6}-[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{4}\b")
CANT_TAKE = re.compile(r"can't take new reports")
STATEMENT = "A grey taxi reversed into my rear bumper at the Bishan carpark, bumper cracked."
# Every name, phone and statement a sender gives here: none may be logged.
PERSONAL: set[str] = set()
_ids = itertools.count(1000)
_photo_ids = itertools.count(1)


def ok(label: str) -> None:
    print(f"    ok   {label}")


def jpeg(seed: int) -> bytes:
    img = Image.new("RGB", (64, 48), (seed * 37 % 256, seed * 91 % 256, seed * 53 % 256))
    img.putpixel((seed % 64, seed % 48), (255 - seed % 256, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


# --- the database -------------------------------------------------------------

def make_user(name: str, username: str, **ticks) -> str:
    db = SessionLocal()
    try:
        user = User(email=f"{username}@example.com", username=username, name=name, role="user", password_hash="!", **ticks)
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


def link_tg(user_id: str, tg_id: int) -> None:
    db = SessionLocal()
    try:
        db.add(ChatLink(channel="telegram", external_id=str(tg_id), user_id=user_id, linked_at=utc_now()))
        db.commit()
    finally:
        db.close()


def unlink_tg(tg_id: int) -> None:
    db = SessionLocal()
    try:
        db.query(ChatLink).filter(ChatLink.external_id == str(tg_id)).delete()
        db.commit()
    finally:
        db.close()


def link_code(name: str, username: str) -> str:
    """A new account's open link code, plain (K7M4QX9P)."""
    user_id = make_user(name, username, ai_company_key_allowed=True)
    db = SessionLocal()
    try:
        plain, _ = new_code(db, db.get(User, user_id))
        db.commit()
        return plain
    finally:
        db.close()


class Writes:
    """The INSERT, UPDATE and DELETE statements sent to the database while
    it is open (any thread)."""

    def __enter__(self):
        self.statements: list[str] = []
        event.listen(engine, "before_cursor_execute", self._sent)
        return self

    def _sent(self, _conn, _cursor, statement, *_args):
        if statement.lstrip().split(None, 1)[0].upper() in ("INSERT", "UPDATE", "DELETE"):
            self.statements.append(statement.split("\n", 1)[0])

    def __exit__(self, *_exc):
        event.remove(engine, "before_cursor_execute", self._sent)


def submissions(tg_id=None) -> list[PublicSubmission]:
    db = SessionLocal()
    try:
        q = db.query(PublicSubmission)
        if tg_id is not None:
            q = q.filter(PublicSubmission.telegram_user_id == str(tg_id))
        return q.all()
    finally:
        db.close()


def by_ref(ref: str) -> PublicSubmission | None:
    db = SessionLocal()
    try:
        return db.query(PublicSubmission).filter(PublicSubmission.reference == ref).first()
    finally:
        db.close()


def report_ids() -> set[str]:
    db = SessionLocal()
    try:
        return {r for (r,) in db.query(Report.id)}
    finally:
        db.close()


def held(rid: str) -> list[str]:
    """Where a report still has something: its rows and its folder."""
    db = SessionLocal()
    try:
        out = [name for name, model, col in (
            ("reports", Report, Report.id), ("intake_sessions", IntakeSession, IntakeSession.report_id),
            ("intake_photos", IntakePhoto, IntakePhoto.report_id),
            ("public_submissions", PublicSubmission, PublicSubmission.report_id),
        ) if db.query(model).filter(col == rid).count()]
    finally:
        db.close()
    if Path(_tmp, "intake", rid).exists():
        out.append("intake folder")
    return out


def age_submissions(hours: float = 2) -> None:
    """Every submission so far moved back in time, so later checks aren't
    held by the 20-an-hour cap (or the senders' 24 hours, past 24)."""
    db = SessionLocal()
    try:
        for row in db.query(PublicSubmission):
            row.submitted_at = row.submitted_at - timedelta(hours=hours)
        db.commit()
    finally:
        db.close()


def seed(n: int, *, tg_id=None, phone=None, ago=timedelta(hours=2)) -> list[str]:
    """n untaken public reports straight into the database (for the caps)."""
    ids = []
    db = SessionLocal()
    try:
        when = utc_now() - ago
        for i in range(n):
            rid = f"seed{next(_ids)}"
            db.add(Report(id=rid, channel=public.PUBLIC_CHANNEL, status="intake", owner_id=None,
                          reporter_chat_id="public", photo_paths=[], created_at=when,
                          data=SecurityIncidentDraft(description="").model_dump()))
            db.flush()
            db.add(IntakeSession(report_id=rid, stage="uploading", reporter_notes="seeded", updated_at=when))
            db.add(PublicSubmission(
                report_id=rid, reference=f"TG-000000-{rid[-4:]}", submitted_at=when, expires_at=when + timedelta(days=30),
                consent_at=when, consent_version=public.CONSENT_VERSION, submitter_name="Seed", submitter_role="driver",
                submitter_phone=phone or f"+65{90000000 + next(_ids)}", phone_kind="sg_mobile", plate=None,
                plate_looks_sg=False, when_where=None, statement="seeded", photo_count_sent=0, forwarded_count=0,
                telegram_user_id=str(tg_id if tg_id is not None else 800000000 + next(_ids)),
            ))
            ids.append(rid)
        db.commit()
    finally:
        db.close()
    return ids


def unseed(ids: list[str]) -> None:
    db = SessionLocal()
    try:
        for rid in ids:
            for model, col in ((IntakePhoto, IntakePhoto.report_id), (IntakeSession, IntakeSession.report_id),
                               (PublicSubmission, PublicSubmission.report_id), (Report, Report.id)):
                db.query(model).filter(col == rid).delete()
        db.commit()
    finally:
        db.close()


def set_contact(value: str) -> None:
    db = SessionLocal()
    try:
        public.save_setting(db, public.PUBLIC_CONTACT_PHONE, value)
        db.commit()
    finally:
        db.close()
    tp._contact["at"] = None


def tg_temp_files() -> set[str]:
    return {p.name for p in tmp_dir().glob("tg-*")}


# --- fake Telegram ---------------------------------------------------------------

class Outbox:
    """Everything the bot sent to one chat."""

    def __init__(self):
        self.texts: list[str] = []
        self.markups: list = []
        self.modes: list = []  # parse_mode of each
        self.downloads: list[str] = []

    @property
    def text(self) -> str:
        return "\n".join(self.texts)

    def buttons(self, index: int = -1) -> dict:
        markup = self.markups[index] if self.markups else None
        out = {b.text: b.callback_data for row in (getattr(markup, "inline_keyboard", None) or ()) for b in row}
        for row in getattr(markup, "keyboard", None) or ():
            for b in row:
                out[b.text] = "contact" if getattr(b, "request_contact", False) else None
        return out

    def data_for(self, label: str) -> str:
        for markup in reversed(self.markups):
            for row in getattr(markup, "inline_keyboard", None) or ():
                for b in row:
                    if b.text == label:
                        return b.callback_data
        raise AssertionError(f"no [{label}] button was sent; last: {self.texts[-2:]}")


class FakeFile:
    def __init__(self, outbox: Outbox, data: bytes):
        self.outbox = outbox
        self.data = data

    async def download_to_drive(self, path):
        Path(path).write_bytes(self.data)
        self.outbox.downloads.append(path)


class FakeSize:
    """A PhotoSize: what Telegram says about it, and what its download gives."""

    def __init__(self, outbox: Outbox, data: bytes | None = None, *, uid=None, size=None, width=1280, height=960):
        self.outbox = outbox
        self.data = data if data is not None else jpeg(next(_photo_ids))
        self.file_unique_id = uid or f"u{next(_photo_ids)}"
        self.file_size = len(self.data) if size is None else size
        self.width, self.height = width, height

    async def get_file(self):
        return FakeFile(self.outbox, self.data)


class FakeMessage:
    def __init__(self, outbox: Outbox, chat_id: int, text=None, photo=None, document=None, contact=None,
                 location=None, forwarded=False, via_bot=None, business=None):
        self.outbox = outbox
        self.chat_id = chat_id
        self.text = text
        self.caption = None
        self.photo = photo or []
        self.document = document
        self.contact = contact
        self.location = location
        self.forward_origin = SimpleNamespace(type="user") if forwarded else None
        self.via_bot = via_bot
        self.business_connection_id = business

    async def reply_text(self, text, **kw):
        await asyncio.sleep(0)  # a real reply is a network round trip
        self.outbox.texts.append(text)
        self.outbox.markups.append(kw.get("reply_markup"))
        self.outbox.modes.append(kw.get("parse_mode"))
        SENT_AT.append((self.chat_id, time.monotonic()))


class FakeQuery:
    def __init__(self, data: str, message: FakeMessage):
        self.data = data
        self.message = message
        self.answers: list = []

    async def answer(self, text=None, **_kw):
        await asyncio.sleep(0)
        self.answers.append(text)

    async def edit_message_reply_markup(self, reply_markup=None, **_kw):
        pass


STAFF_CHATS: set[int] = set()
SENT_AT: list[tuple[int, float]] = []


def sender(uid: int):
    return SimpleNamespace(id=uid, username=f"tg{uid}", full_name=f"Phone {uid}", first_name="Phone")


def message_update(uid: int, outbox: Outbox, **kw):
    msg = FakeMessage(outbox, uid, **kw)
    return SimpleNamespace(update_id=next(_ids), effective_user=sender(uid),
                           effective_chat=SimpleNamespace(id=uid, type="private"),
                           effective_message=msg, message=msg, callback_query=None)


def tap_update(uid: int, outbox: Outbox, data: str):
    query = FakeQuery(data, FakeMessage(outbox, uid))
    return SimpleNamespace(update_id=next(_ids), effective_user=sender(uid),
                           effective_chat=SimpleNamespace(id=uid, type="private"),
                           effective_message=query.message, message=None, callback_query=query)


def card(phone: str, user_id, vcard: str | None = None):
    return SimpleNamespace(phone_number=phone, user_id=user_id, first_name="X", last_name=None, vcard=vcard)


async def settle(seconds: float = 0.15) -> None:
    """Lets the album reply (tp.ALBUM_SECONDS, shortened here) go out."""
    await asyncio.sleep(seconds)


class Person:
    """A Telegram user in a private chat with the bot."""

    def __init__(self, name: str = "Tan Ah Kow", phone: str = "+6591234567", statement: str = STATEMENT, uid=None):
        self.uid = uid or next(_ids) + 500000
        self.out = Outbox()
        self.name, self.phone, self.statement = name, phone, statement
        PERSONAL.update({name, phone, phone.lstrip("+"), phone[-8:], statement})

    @property
    def session(self):
        return state.find_session(str(self.uid))

    @property
    def form(self) -> PublicForm | None:
        return self.session.public if self.session else None

    async def text(self, words: str, **kw) -> None:
        await tg.handle_text(message_update(self.uid, self.out, text=words, **kw), None)

    async def command(self, handler, words: str = "/x", *args: str) -> None:
        await handler(message_update(self.uid, self.out, text=words), SimpleNamespace(args=list(args)))

    async def photo(self, *sizes: FakeSize, **kw) -> None:
        await tg.handle_photo(message_update(self.uid, self.out, photo=list(sizes) or [FakeSize(self.out)], **kw), None)

    async def contact(self, phone: str | None = None, user_id="self", **kw) -> None:
        contact = card(phone or self.phone, self.uid if user_id == "self" else user_id, kw.pop("vcard", None))
        await tg.handle_contact(message_update(self.uid, self.out, contact=contact, **kw), None)

    async def tap(self, label: str | None = None, data: str | None = None) -> FakeQuery:
        update = tap_update(self.uid, self.out, data or self.out.data_for(label))
        await tg.handle_callback(update, None)
        return update.callback_query

    def unhurried(self) -> None:
        tg._buckets._b.pop(str(self.uid), None)

    async def welcome(self) -> None:
        tg._told_at.pop(f"pub-welcome:{self.uid}", None)
        await self.command(tg.start, "/start")

    async def agree(self) -> None:
        await self.welcome()
        await self.tap("I agree, start")

    async def fill(self, *, photos: int = 2, plate: str = "SBA 1234-a", when: str | None = "Yesterday 6pm, PIE exit 8",
                   role: str = "Driver") -> None:
        """Consent and steps 1-6, to the review (in a fresh burst of the
        gate's bucket: a person types slower than this)."""
        self.unhurried()
        await self.agree()
        assert self.form is not None and self.form.step == PublicStep.NAME, self.out.texts[-1]
        await self.text(self.name)
        await self.tap(role)
        await self.contact()
        await self.text(plate)
        if when is None:
            await self.tap("Skip")
        else:
            await self.text(when)
        await self.text(self.statement)
        for _ in range(photos):
            await self.photo()
        await settle()
        await self.tap("Done")
        if not photos:
            await self.tap("Send without photos")
        assert self.form.step == PublicStep.REVIEW, self.out.texts[-3:]

    async def submit(self) -> str | None:
        await self.tap("Submit")
        m = REF.search(self.out.texts[-1])
        return m.group(0) if m else None


async def withdraw(person: "Person", ref: str, yes: str = "Yes, delete it") -> None:
    """/withdraw, the report's button if there is a list, then yes."""
    person.unhurried()
    await person.command(tg.withdraw_command, "/withdraw")
    pick = next((data for label, data in person.out.buttons().items() if label.startswith(ref)), None)
    if pick:
        await person.tap(data=pick)
    await person.tap(yes)


def fresh() -> None:
    """Budgets and throttles back to empty between checks; the reply budget
    is opened wide (it has its own check)."""
    tg._buckets.reset()
    tp.reset_limits()
    tp.replies.rate, tp.replies.burst = 10**6, 10**6
    tp.replies.reset()


# --- AI and PDF must never run ----------------------------------------------------

AI_CALLS: list[str] = []


def _no_ai(name):
    def boom(*_a, **_kw):
        AI_CALLS.append(name)
        raise AssertionError(f"{name} called in the public flow")
    return boom


NO_AI = [
    mock.patch.object(tg, "build_draft", _no_ai("build_draft")),
    mock.patch.object(tg, "render_pdf", _no_ai("render_pdf")),
    mock.patch.object(tg, "ensure_ai_ready", _no_ai("ensure_ai_ready")),
    mock.patch.object(tg, "resolve_keys", _no_ai("resolve_keys")),
    mock.patch("app.conversation.flow.build_draft", _no_ai("build_draft")),
    mock.patch("app.rendering.renderer.render_pdf", _no_ai("render_pdf")),
    mock.patch("app.ai.keys.ensure_ai_ready", _no_ai("ensure_ai_ready")),
    mock.patch("app.ai.keys.resolve_keys", _no_ai("resolve_keys")),
    mock.patch("app.ai.extraction.draft_report", _no_ai("draft_report")),
]


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        msg = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            msg += f" | {record.exc_info[1]}"
        self.lines.append(f"{record.name}: {msg}")


LOG = LogCapture()


# --- 1. before consent --------------------------------------------------------------

async def check_before_consent() -> None:
    print("1. before consent, and No thanks")
    fresh()
    p = Person()
    before_rows, before_tmp = report_ids(), tg_temp_files()
    for _ in range(3):  # an album, before any consent
        await p.photo()
    await p.text("hello, my car got hit")
    assert len(p.out.texts) == 1 and f"{BASE}/privacy#public-reports" in p.out.texts[0], p.out.texts
    assert "I didn't keep the photos you sent" in p.out.texts[0]
    assert set(p.out.buttons()) == {"I agree, start", "No thanks"}
    assert all(d.startswith("pub:") for d in p.out.buttons().values())
    assert p.out.downloads == [] and p.session is None and str(p.uid) not in state._sessions
    assert report_ids() == before_rows and tg_temp_files() == before_tmp
    ok("an album and text before consent: one welcome (privacy notice, Gemini, 30 days, /withdraw); "
       "nothing downloaded, no session, no rows, no temp file")

    for words in ("gemini", "outside singapore", "30 days", "/withdraw", "nric", "telegram"):
        assert words in p.out.texts[0].lower(), words
    await p.tap("No thanks")
    assert p.out.texts[-1].startswith("Nothing saved.") and "call" not in p.out.texts[-1].lower(), p.out.texts[-1]
    assert p.session is None and report_ids() == before_rows
    await p.tap(data=p.out.buttons(0)["I agree, start"])  # the old welcome, after No thanks
    assert p.session is None and len(p.out.texts) == 2
    ok("[No thanks]: 'Nothing saved', no 'call us' while no contact line is set; the welcome's buttons are then dead")

    set_contact("6123 4567")
    q = Person()
    await q.welcome()
    await q.tap("No thanks")
    assert q.out.texts[-1] == "Nothing saved. Send /start if you change your mind. You can also call us on 6123 4567."
    set_contact("")
    ok("with a public contact line set, [No thanks] adds 'You can also call us on 6123 4567'")

    r = Person()
    await r.welcome()
    first = r.out.buttons()["I agree, start"]
    await r.welcome()
    await r.tap(data=first)  # the older welcome's [I agree]
    assert r.session is None, "an older welcome's [I agree] started a form"
    await r.tap("I agree, start")
    assert r.form is not None and r.form.step == PublicStep.NAME and "(1/6)" in r.out.texts[-1]
    ok("only the latest welcome's [I agree] starts the form: '(1/6) What's your name?'")


# --- 2. the happy path ----------------------------------------------------------------

async def check_happy_path() -> None:
    print("2. a whole report")
    fresh()
    p = Person("Lim Bee Hoon", "+6598761234", "Scraped by a lorry on the CTE near Braddell, left door dented.")
    before = report_ids()
    await p.agree()
    await p.text(p.name)
    assert set(p.out.buttons()) == set(public.ROLES.values()), p.out.buttons()
    await p.tap("Car owner")
    assert "(2/6)" in p.out.texts[-1] and p.out.buttons().get("Share my phone number") == "contact"
    await p.contact()
    assert p.out.texts[-2] == "Thanks, got +65 9876 1234." and "(3/6)" in p.out.texts[-1]
    await p.text("sjb 8762-m")
    assert "(4/6)" in p.out.texts[-1]
    await tg.handle_location(message_update(p.uid, p.out, location=SimpleNamespace(latitude=1.3401, longitude=103.8461)), None)
    assert "(5/6)" in p.out.texts[-1] and "number" not in p.out.texts[-1], p.out.texts[-1]
    await p.text("Bad")
    assert p.out.texts[-1] == tp.SHORT_STATEMENT
    await p.text(p.statement)
    assert "(6/6)" in p.out.texts[-1] and "20" in p.out.texts[-1]
    for _ in range(3):
        await p.photo()
    assert not any("Got" in t for t in p.out.texts[-3:])
    await settle()
    assert p.out.texts[-1] == "Got 3 photos (20 max). Send more, or tap Done.", p.out.texts[-1]
    temp = list(p.session.photo_paths)
    assert len(temp) == 3 and all(re.fullmatch(r"tg-[0-9a-f]{32}\.jpg", Path(t).name) for t in temp)
    ok("steps 1-6: name, role buttons, 'Share my phone number' (a reply keyboard), plate, a location pin, what "
       "happened (no 'another number'), photos to tg-<hex> temp files; the album answered once, 2 s after")

    await p.tap("Done")
    review = p.out.texts[-1]
    for line in ("Name: Lim Bee Hoon (Car owner)", "Phone: +65 9876 1234", "Plate: SJB8762M",
                 "When & where: Location pin: 1.34010, 103.84610", "Photos: 3"):
        assert line in review, (line, review)
    assert set(p.out.buttons()) == {"Submit", "Edit something", "Cancel"}
    assert report_ids() == before
    ref = await p.submit()
    reply = p.out.texts[-1]
    assert ref and ref[3:9] == (utc_now() + timedelta(hours=8)).strftime("%y%m%d"), reply
    assert "/reports/" not in reply and "http" not in reply and p.name not in reply and "9876" not in reply, reply
    assert "/withdraw" in reply
    assert set(p.out.modes) == {None}, set(p.out.modes)
    ok("every reply plain text (no parse_mode); the review; Submit answers with the reference only (TG-YYMMDD-XXXX, today in Singapore): no link, name "
       "or number")

    row = by_ref(ref)
    rid = row.report_id
    db = SessionLocal()
    try:
        report = db.get(Report, rid)
        session = db.get(IntakeSession, rid)
        photos = db.query(IntakePhoto).filter(IntakePhoto.report_id == rid).order_by(IntakePhoto.position).all()
        history = [(a.action, a.user_id, a.detail) for a in db.query(AuditEntry).filter(AuditEntry.report_id == rid)]
    finally:
        db.close()
    assert (report.channel, report.status, report.owner_id, report.reporter_chat_id) == \
        ("telegram_public", "intake", None, "public")
    assert report.data["reporter_name"] == p.name and report.data["reporter_role"] == "Car owner"
    assert report.data["reporter_contact"] == "+65 9876 1234" and report.data["vehicle_info"]["plate_number"] == "SJB8762M"
    assert report.data["description"] == "" and report.photo_paths == []
    notes = session.reporter_notes
    assert session.stage == "uploading" and p.statement in notes and "Location pin: 1.34010, 103.84610" in notes
    assert not any(x in notes for x in (p.name, "9876", "SJB8762M", ref)), notes
    assert [ph.position for ph in photos] == [0, 1, 2]
    assert all(Path(ph.path).exists() and thumbnail_path_for(ph.path).exists() and preview_path_for(ph.path).exists()
               for ph in photos)
    assert (row.submitter_name, row.submitter_role, row.submitter_phone, row.phone_kind) == \
        (p.name, "owner", "+6598761234", "sg_mobile")
    assert row.plate == "SJB8762M" and row.plate_looks_sg and row.statement == p.statement
    assert row.telegram_user_id == str(p.uid) and row.telegram_username == f"tg{p.uid}"
    assert row.consent_version == public.CONSENT_VERSION and row.photo_count_sent == 3 and row.forwarded_count == 0
    assert history == [("created", None, f"{public.CREATED_DETAIL} · {ref}")], history
    assert not any(Path(t).exists() for t in temp) and p.session is None
    ok("saved as an intake draft: the report (telegram_public, no owner, 'public'), the reporter fields, notes "
       "without name, phone, plate or ref, 3 photos with thumbnails and previews, the submission row, one "
       "'created' entry with the ref and no account; temp files and the form gone")


# --- 4. contact cards -------------------------------------------------------------

async def check_contacts() -> None:
    print("4. the phone must be the sender's own")
    fresh()
    p = Person("Ottilie Varga", "+6581112233")
    await p.agree()
    await p.text(p.name)
    await p.contact()  # before the phone step
    assert p.out.texts[-1].startswith(tp.NOT_NEEDED) and p.form.phone is None
    await p.tap("Driver")

    async def refused(label: str, expect: str, run) -> None:
        said = len(p.out.texts)
        await run()
        assert len(p.out.texts) == said + 1 and p.out.texts[-1].startswith(expect), (label, p.out.texts[-1])
        assert p.form.phone is None and p.form.step == PublicStep.PHONE, label
        assert p.out.buttons().get("Share my phone number") == "contact", label

    await refused("typed", tp.TYPED_NUMBER, lambda: p.text("91234567"))
    await refused("typed +65", tp.TYPED_NUMBER, lambda: p.text("+65 9123 4567"))
    await refused("someone else's", tp.NOT_YOUR_CARD, lambda: p.contact("+6587776655", user_id=p.uid + 1))
    await refused("no user id", tp.NOT_YOUR_CARD, lambda: p.contact("+6587776655", user_id=None))
    await refused("forwarded own", tp.NOT_YOUR_CARD, lambda: p.contact(forwarded=True))
    await refused("via a bot", tp.NOT_YOUR_CARD, lambda: p.contact(via_bot=SimpleNamespace(id=6, username="inline")))
    await refused("business", tp.NOT_YOUR_CARD, lambda: p.contact(business="bc1"))
    await refused("not a number", tp.BAD_NUMBER, lambda: p.contact("12"))
    await refused("+888", "That's an anonymous +888 number", lambda: p.contact("+88812345678"))
    assert "call" not in p.out.texts[-1].replace("can't call back", ""), p.out.texts[-1]
    assert not submissions(p.uid)
    ok("typed numbers, someone else's card, a card with no user id, forwarded, via a bot, from a business "
       "chat, unreadable, +888: refused and asked again (no 'call us' with no contact line)")

    await p.contact(vcard="BEGIN:VCARD\nTEL:+6500000000\nEND:VCARD")
    assert p.form.phone == "+6581112233" and p.form.forwarded_count == 1 and "(3/6)" in p.out.texts[-1]
    await p.command(tg.cancel_command, "/cancel")
    ok("their own card is taken as E.164 from phone_number (its vCard ignored); the forwarded one was counted")


# --- 5. staff buttons, and pub: buttons from staff ----------------------------------------

async def check_buttons_stay_apart() -> None:
    print("5. buttons are untrusted")
    fresh()
    p = Person("Wong Mei Ling", "+6582223344")
    await p.fill(photos=0)
    said, step, rev = len(p.out.texts), p.form.step, p.form.rev
    for data in ("confirm", "side_left", "ins_aig", "body_SUV", "pub:x", "pub::", "pub:ffffffff:submit:0",
                 f"pub:{p.form.nonce}:role:driver", f"pub:{p.form.nonce}:nonsense", f"pub:{p.form.nonce}:wdy:0"):
        await p.tap(data=data)
    assert len(p.out.texts) == said and (p.form.step, p.form.rev) == (step, rev), p.out.texts[said:]
    assert not submissions(p.uid)
    ok("staff draft buttons (confirm, side_, ins_, body_), malformed pub: data, another form's nonce, a step "
       "already answered: nothing happens to the public form")

    staff = make_user("Sam Staff", "samstaff", ai_company_key_allowed=True)
    s = Person(uid=next(_ids) + 600000)
    STAFF_CHATS.add(s.uid)
    link_tg(staff, s.uid)
    query = await s.tap(data=f"pub:{p.form.nonce}:submit:{p.form.rev}")
    assert s.out.texts == [] and query.answers == [None] and not submissions(p.uid) and p.form is not None
    ok("a linked staff member's tap on a pub: button is answered and does nothing")
    await p.command(tg.cancel_command, "/cancel")


# --- 6. with P5 ------------------------------------------------------------------------

async def check_with_p5() -> None:
    print("6. with P5's linking")
    fresh()
    p = Person("Rajesh Kumar", "+6583334455")
    await p.agree()
    await p.text(p.name)
    await p.tap("Driver")
    await p.photo()
    await settle()
    temp = list(p.session.photo_paths)
    assert len(temp) == 1 and p.out.texts[-1].startswith("Thanks, I've kept 1 photo for step 6.")
    assert "(2/6)" in p.out.texts[-1]
    ok("a photo sent before step 6 (after consent) is kept for it, and the step is asked again")

    await p.text("K7M4-QX9P")
    assert p.out.texts[-1].startswith(tp.CODE_HINT) and p.form.phone is None and p.form.step == PublicStep.PHONE
    ok("a bare link code mid-form: the /link hint, nothing kept")

    joe = make_user("Joe Lee", "joelee", ai_company_key_allowed=True)
    db = SessionLocal()
    try:
        plain, _ = new_code(db, db.get(User, joe))
        db.commit()
    finally:
        db.close()
    code = f"{plain[:4]}-{plain[4:]}"
    await p.command(tg.link_command, f"/link {code}", code)
    assert "Connect this Telegram to <b>@joelee (Joe Lee)</b>" in p.out.texts[-1] and p.form is not None
    await p.command(tg.start, f"/start link-{plain}", f"link-{plain}")
    assert "Connect this Telegram to" in p.out.texts[-1] and p.form is not None and p.session.photo_paths == temp
    ok("/link CODE and /start link-CODE mid-form: P5's prompt, the form kept")

    link_tg(joe, p.uid)
    STAFF_CHATS.add(p.uid)
    await p.text("hello")
    assert any("public report form you'd started here was discarded" in t for t in p.out.texts[-2:]), p.out.texts[-2:]
    assert p.out.texts[-1].startswith("Please send at least one incident photo"), p.out.texts[-1]
    assert p.session.public is None and p.session.photo_paths == [] and p.session.carlink_user_id == joe
    assert not any(Path(t).exists() for t in temp) and not submissions(p.uid)
    ok("connected mid-form: the form and its photos are discarded at the next update, never joining a staff "
       "draft; the update goes on as staff")


# --- 7. caps ------------------------------------------------------------------------------

async def check_caps() -> None:
    print("7. caps")
    fresh()
    q = Person("Quentin Fairweather", "+6586667788")
    seeded = seed(3, tg_id=q.uid)
    await q.agree()
    assert CANT_TAKE.search(q.out.texts[-1]) and "this Telegram account" in q.out.texts[-1] and q.form is None
    unseed(seeded)
    ok("3 open from this Telegram: refused at consent, 'can't take new reports', no form")

    r = Person("Rosalind Pemberton", "+6586667700")
    seeded = seed(3, phone=r.phone)
    await r.agree()
    await r.text(r.name)
    await r.tap("Driver")
    await r.photo()
    temp = list(r.session.photo_paths)
    await r.contact()
    assert CANT_TAKE.search(r.out.texts[-1]) and "phone number" in r.out.texts[-1], r.out.texts[-1]
    assert r.session is None and not any(Path(t).exists() for t in temp)
    unseed(seeded)
    ok("3 open for this phone number: refused once the contact is shared; the form and its photos dropped")

    g = Person("Gideon Ashcombe", "+6581230001")
    seeded = seed(public.MAX_OPEN)
    await g.agree()
    assert CANT_TAKE.search(g.out.texts[-1]) and "here right now" in g.out.texts[-1] and g.form is None
    unseed(seeded)
    seeded = seed(public.MAX_PER_HOUR, ago=timedelta(minutes=10))
    await g.agree()
    assert CANT_TAKE.search(g.out.texts[-1]) and g.form is None
    unseed(seeded)
    with mock.patch.object(public, "disk_space_low", lambda extra_gb=0.0: True):
        await g.agree()
    assert CANT_TAKE.search(g.out.texts[-1]) and g.form is None
    ok("50 open in all, 20 sent in the hour, the disk under its public headroom: refused at consent")

    await g.fill(photos=1)
    seeded = seed(public.MAX_OPEN)
    await g.tap("Submit")
    assert CANT_TAKE.search(g.out.texts[-1]) and g.form is not None and g.form.step == PublicStep.REVIEW
    assert not submissions(g.uid)
    unseed(seeded)
    ref = await g.submit()
    assert ref and by_ref(ref) is not None
    ok("the inbox fills between consent and Submit: refused inside save_submission's transaction, the form "
       "kept; Submit again once there is room")

    with mock.patch.object(tp, "MAX_FORMS", 2):
        a, b, c = Person("Ann Teo", "+6581230011"), Person("Ben Teo", "+6581230012"), Person("Cai Teo", "+6581230013")
        stale = [chat for chat, _ in state.public_sessions()]
        for chat in stale:
            tg_common._drop(chat)
        await a.agree()
        await b.agree()
        await c.agree()
        assert c.form is None and CANT_TAKE.search(c.out.texts[-1])
        a.session.last_active -= tg.SESSION_MAX_IDLE_SECONDS + 1
        await c.agree()
        assert c.form is not None and a.session is None
        for x in (b, c):
            await x.command(tg.cancel_command, "/cancel")
    ok("at most MAX_FORMS forms open in memory; one idle past a day makes room")

    w = Person("Wilma Ong", "+6581230021")
    refs = []
    for _ in range(public.MAX_PER_SENDER_DAY):
        await w.fill(photos=0)
        refs.append(await w.submit())
    for ref in refs:
        await withdraw(w, ref)
        assert by_ref(ref) is None
    await w.agree()
    assert CANT_TAKE.search(w.out.texts[-1]) and w.form is None
    ok("3 sent and withdrawn in a day: a 4th is still refused (the bot remembers what the database forgot)")

    x = Person("Spammy Mcspamface", "+6585556677")
    db = SessionLocal()
    try:
        public.block_sender(db, x.uid)
        db.commit()
    finally:
        db.close()
    await x.photo()
    assert "can't take reports from this account" in x.out.texts[-1] and "call" not in x.out.texts[-1]
    assert x.out.downloads == [] and not x.out.buttons()
    await x.welcome()
    assert len(x.out.texts) == 1
    ok("a blocked sender: 'can't take reports from this account' once a day, nothing fetched, no consent")
    age_submissions()


# --- 8. photos ------------------------------------------------------------------------------

async def check_photos() -> None:
    print("8. photo rules")
    fresh()
    p = Person("Pauline Ng", "+6587770001")
    await p.agree()
    for step in ("name", "role", "phone", "plate", "when", "what"):
        if step == "name":
            await p.text(p.name)
        elif step == "role":
            await p.tap("Workshop")
        elif step == "phone":
            await p.contact()
        elif step == "plate":
            await p.tap("Not sure")
        elif step == "when":
            await p.tap("Skip")
        else:
            await p.text(p.statement)
    assert p.form.step == PublicStep.PHOTOS and p.form.plate is None and p.form.when_where is None

    doc = SimpleNamespace(mime_type="image/jpeg", file_name="IMG_0042.jpg",
                          get_file=_no_ai("a file's get_file"))
    await tg.handle_document(message_update(p.uid, p.out, document=doc), None)
    assert p.out.texts[-1] == tp.FILES_REFUSED and p.out.downloads == []
    ok("a file (even a JPEG) is refused unread: 'photos sent as photos, not files'")

    big = FakeSize(p.out, size=6 * 1024 * 1024)
    await p.photo(big)
    small, large = FakeSize(p.out, size=90_000, width=320, height=240), FakeSize(p.out, size=7 * 1024 * 1024)
    await p.photo(small, large)
    liar = FakeSize(p.out, b"\xff" * (public.MAX_PHOTO_BYTES + 10), size=1000)
    await p.photo(liar)
    await settle()
    assert len(p.out.downloads) == 2 and p.form.photo_ids == [small.file_unique_id], p.form.photo_ids
    assert tp.TOO_BIG in p.out.texts[-1] and "Got 1 photo" in p.out.texts[-1], p.out.texts[-1]
    ok("over 5 MB: refused unread; the largest size under 5 MB is taken instead; a download over 5 MB "
       "whatever Telegram said is deleted")

    again = FakeSize(p.out, uid=small.file_unique_id)
    await p.photo(again)
    twin_data = jpeg(4242)
    await p.photo(FakeSize(p.out, twin_data))
    await p.photo(FakeSize(p.out, twin_data))
    await p.photo(FakeSize(p.out, b"this is not an image at all"))
    await settle()
    assert len(p.out.downloads) == 5 and len(p.form.photo_ids) == 4, (len(p.out.downloads), p.form.photo_ids)
    ok("a repeat (file_unique_id) isn't fetched again")

    tp.sender_photos.limit = len(tp.sender_photos._times[str(p.uid)]) + 1  # one more
    try:
        await p.photo()
        await p.photo()
        await settle()
        assert tp.TOO_MANY_PHOTOS in p.out.texts[-1] and len(p.form.photo_ids) == 5
    finally:
        tp.sender_photos.limit = tp.PHOTOS_PER_SENDER_HOUR
    tp.all_photos.limit = 0
    try:
        await p.photo()
        await settle()
        assert tp.NO_SPACE in p.out.texts[-1] and len(p.form.photo_ids) == 5
    finally:
        tp.all_photos.limit = tp.PHOTOS_PER_HOUR
    with mock.patch.object(tp, "disk_space_low", lambda extra_gb=0.0: extra_gb == public.DISK_HEADROOM_GB):
        await p.photo()
    with mock.patch.object(tp, "MAX_TEMP_BYTES", p.form.photo_bytes + 10):
        await p.photo()
    await settle()
    assert tp.NO_SPACE in p.out.texts[-1] and len(p.form.photo_ids) == 5 and len(p.out.downloads) == 6
    ok("30 photos an hour a sender, 300 from everyone (the bot's own budgets), the 2 GB public disk headroom, "
       "1 GB of public temp files: past any, not fetched")

    tg._buckets.reset()
    while len(p.form.photo_ids) < public.MAX_PHOTOS:
        await p.photo()
    fetched = len(p.out.downloads)
    await p.photo()
    await settle()
    assert len(p.out.downloads) == fetched and len(p.form.photo_ids) == public.MAX_PHOTOS
    assert p.out.texts[-1] == f"{tp.AT_CAP} Got 20 photos (20 max). Tap Done to continue.", p.out.texts[-1]
    ok("the 21st photo isn't fetched: 'That's 20 photos, the most I can take'")

    await p.tap("Done")
    ref = await p.submit()
    row = by_ref(ref)
    db = SessionLocal()
    try:
        kept = db.query(IntakePhoto).filter(IntakePhoto.report_id == row.report_id).count()
    finally:
        db.close()
    # 20 in the form: the garbage file and the second twin aren't stored.
    assert kept == public.MAX_PHOTOS - 2 and row.photo_count_sent >= public.MAX_PHOTOS, (kept, row.photo_count_sent)
    ok("Submit: a file that isn't an image and the same bytes twice are skipped by the save (no IntegrityError)")
    age_submissions()


# --- 10. commands, Submit, expiry ------------------------------------------------------------

async def check_commands() -> None:
    print("10. commands in every state; Submit; expiry")
    fresh()
    p = Person("Mohd Faizal", "+6587770002")
    await p.command(tg.cancel_command, "/cancel")
    assert p.out.texts[-1] == tp.NOTHING_TO_CANCEL
    await p.command(tg.withdraw_command, "/withdraw")
    assert p.out.texts[-1] == tp.NO_WITHDRAW
    await p.command(tg.help_command, "/help")
    assert p.out.texts[-1] == tp.HELP
    await p.agree()
    await p.text(p.name)
    await p.command(tg.start, "/start")
    assert p.out.texts[-1] == tp.IN_PROGRESS and set(p.out.buttons()) == {"Continue", "Start again"}
    await p.tap("Continue")
    assert p.out.texts[-1].startswith("Thanks, Mohd Faizal. Are you")
    await p.command(tg.help_command, "/help")
    assert p.out.texts[-1].startswith(tp.HELP) and "Are you" in p.out.texts[-1]
    await p.command(tg.withdraw_command, "/withdraw")
    assert p.out.texts[-1] == tp.FINISH_FIRST
    await p.tap("Driver")
    await p.photo()
    temp = list(p.session.photo_paths)
    await p.command(tg.start, "/start")
    await p.tap("Start again")
    assert p.form.step == PublicStep.NAME and p.form.name is None and not any(Path(t).exists() for t in temp)
    await p.text(p.name)
    await p.tap("Driver")
    await p.text("Cancel")  # the phone step's keyboard button
    assert p.out.texts[-1] == tp.CANCELLED and p.session is None
    ok("/cancel, /withdraw, /help and /start before, during and after a form; [Continue], [Start again] "
       "(its photos deleted), the keyboard's Cancel")

    await p.fill(photos=1)
    old_submit = p.out.data_for("Submit")
    await p.tap("Edit something")
    await p.tap("Plate")
    await p.text("SKP 77 Z")
    assert p.form.step == PublicStep.REVIEW and "Plate: SKP77Z" in p.out.texts[-1]
    await p.tap(data=old_submit)
    assert "Please check your report" in p.out.texts[-1] and not submissions(p.uid)
    await p.tap("Edit something")
    await p.tap("Name")
    await p.text("Mohd Faizal bin Ali")
    await p.tap("Driver")
    assert p.form.step == PublicStep.REVIEW and "Name: Mohd Faizal bin Ali (Driver)" in p.out.texts[-1]
    PERSONAL.add("Mohd Faizal bin Ali")
    ok("Edit something -> a field -> back to the review; a Submit from the older review shows it again, sends "
       "nothing")

    submit = p.out.data_for("Submit")
    first, second = tap_update(p.uid, p.out, submit), tap_update(p.uid, p.out, submit)
    await asyncio.gather(tg.handle_callback(first, None), tg.handle_callback(second, None))
    assert len(submissions(p.uid)) == 1, submissions(p.uid)
    assert second.callback_query.answers == ["Already sending your report -- one moment."], second.callback_query.answers
    assert sum(bool(REF.search(t)) for t in p.out.texts) == 1
    ok("two Submit taps at once: one report; the second only answered (claimed before the first await)")

    await p.fill(photos=2)
    temp = list(p.session.photo_paths)
    p.session.last_active -= tg.SESSION_MAX_IDLE_SECONDS + 60
    await p.tap("Submit")
    assert p.out.texts[-1] == tp.EXPIRED and p.session is None and not any(Path(t).exists() for t in temp)
    assert len(submissions(p.uid)) == 1
    ok("a form idle over 24 hours: closed with its photos at the next update, nothing sent")
    age_submissions()


# --- 11. /withdraw -----------------------------------------------------------------------------

async def check_withdraw() -> None:
    print("11. /withdraw")
    fresh()
    v, t2, mal = Person("Victoria Seah", "+6588880001"), Person("Terence Goh", "+6588880002"), \
        Person("Mallory Brightwater", "+6588880003")
    for x in (v, t2, mal):
        await x.fill(photos=1)
        x.ref = await x.submit()
        x.rid = by_ref(x.ref).report_id
    staff = make_user("Tina Staff", "tinastaff", sees_all_cases=True)

    def take(rid: str) -> None:
        db = SessionLocal()
        try:
            public.take(db, rid, db.get(User, staff))
            db.commit()
        finally:
            db.close()

    await v.command(tg.withdraw_command, "/withdraw")
    assert v.out.texts[-1].startswith(f"Take back report {v.ref}, sent ") and set(v.out.buttons()) == {"Yes, delete it", "Keep it"}
    victim_yes = v.out.buttons()["Yes, delete it"]
    assert re.fullmatch(r"pub:[0-9a-f]{8}:wdy:0", victim_yes), victim_yes
    await mal.command(tg.withdraw_command, "/withdraw")
    mine = set(mal.out.buttons().values())
    before = {s.report_id for s in submissions()}
    nonce = mine.pop().split(":")[1]
    # "²" and "٣" are digits to str.isdigit(), but int() can't read "²".
    assert tp._index("²") is None and tp._index("٣") is None and tp._index("１") is None and tp._index("1") == 1
    for data in (victim_yes, f"pub:{nonce}:wdy:{v.rid}", f"pub:{nonce}:wd:{v.ref}", f"pub:{nonce}:wd:yes:{v.rid}",
                 f"pub:{nonce}:wdy:5", f"pub:{nonce}:wdy:-1", f"pub:{nonce}:wd:²", f"pub:{nonce}:wdy:²",
                 f"pub:{nonce}:wdy:٣", f"pub:{nonce}:wd:1", f"pub:{nonce}:wd:0"):
        await mal.tap(data=data)
    assert {s.report_id for s in submissions()} == before and held(v.rid)
    assert mal.out.texts[-1].startswith(f"Take back report {mal.ref}")  # wd:0 only asks again, about her own
    ok("forged withdraw buttons (the victim's own, made-up indices, '²' and other non-ASCII digits, a report id or "
       "ref in the data) delete nothing and raise nothing; wd:<n> only asks, and only about the sender's own reports")

    await v.tap("Keep it")
    assert v.out.texts[-1] == tp.KEPT and by_ref(v.ref) is not None
    await v.tap(data=victim_yes)  # the question is gone once answered
    assert by_ref(v.ref) is not None
    await v.command(tg.withdraw_command, "/withdraw")
    await v.tap("Yes, delete it")
    assert v.out.texts[-1] == f"Done. Report {v.ref} and its photos have been deleted.", v.out.texts[-1]
    assert held(v.rid) == [], held(v.rid)
    db = SessionLocal()
    try:
        last = db.query(AuditEntry).filter(AuditEntry.report_id == v.rid).order_by(AuditEntry.id.desc()).first()
    finally:
        db.close()
    assert last.action == "withdrawn" and v.ref in last.detail and v.name not in last.detail
    ok("[Keep it] keeps it; [Yes, delete it] deletes the untaken report (4 tables and its folder), the history "
       "keeping only the ref")

    take(t2.rid)
    await t2.command(tg.withdraw_command, "/withdraw")
    assert "already started" in t2.out.texts[-1] and set(t2.out.buttons()) == {"Yes, tell them", "Keep it"}
    await t2.tap("Yes, tell them")
    assert "already started" in t2.out.texts[-1] and "call" not in t2.out.texts[-1]
    assert by_ref(t2.ref).withdraw_requested_at is not None and held(t2.rid)
    ok("a taken report: not deleted, the request recorded for staff (no 'call us' with no contact line)")

    await mal.command(tg.withdraw_command, "/withdraw")
    take(mal.rid)  # staff take it while she reads the question
    await mal.tap("Yes, delete it")
    assert "already started" in mal.out.texts[-1] and by_ref(mal.ref).withdraw_requested_at is not None
    ok("staff take it between the question and [Yes, delete it]: it stays, the request is recorded")

    s = Person(uid=next(_ids) + 700000)
    STAFF_CHATS.add(s.uid)
    link_tg(staff, s.uid)
    await s.command(tg.withdraw_command, "/withdraw")
    assert "for reports sent by the public" in s.out.texts[-1]
    ok("staff's /withdraw: 'for reports sent by the public'")
    age_submissions()


# --- the gate and the reply budget ----------------------------------------------------------

async def check_gate() -> None:
    print("the gate (H1) and the reply budget (M3)")
    fresh()
    p = Person("Flood Person", "+6589990001")
    for _ in range(tg.BURST + 10):
        await p.text("spam")
    slow = [t for t in p.out.texts if t == tp.SLOW_DOWN]
    assert len(slow) == 1 and tg._buckets._b[str(p.uid)][0] < 1, (len(slow), p.out.texts[:3])
    ok(f"a stranger's {tg.BURST + 10} messages at once: {tg.BURST} let through, the rest dropped, "
       "'Please slow down' once")

    staff = make_user("Gus Staff", "gusstaff", ai_company_key_allowed=True)
    s = Person(uid=next(_ids) + 700000)
    STAFF_CHATS.add(s.uid)
    link_tg(staff, s.uid)
    for _ in range(tg.BURST + 15):
        await s.command(tg.whoami_command, "/whoami")
    assert len(s.out.texts) == tg.BURST + 15 and tp.SLOW_DOWN not in s.out.texts
    ok(f"linked staff: a burst of {tg.BURST_LINKED} ({tg.BURST + 15} commands all answered)")

    # One stranger gets at most half the public slots, and one album (10
    # photos at most, in Telegram) always fits.
    assert tg.PUBLIC_CHAT_IN_FLIGHT == 12 and 10 <= tg.PUBLIC_CHAT_IN_FLIGHT <= tg.PUBLIC_IN_FLIGHT // 2
    assert (tg.BURST, tg.BURST_LINKED, tg.CHAT_IN_FLIGHT, tg.PUBLIC_IN_FLIGHT) == (45, 60, 48, 24)
    q = Person("Queue Person", "+6589990002")
    other = Person("Other Person", "+6589990003")
    lock = tg._lock_for(str(q.uid))
    await lock.acquire()
    try:
        tasks = [asyncio.create_task(q.text("x")) for _ in range(tg.PUBLIC_IN_FLIGHT + 6)]
        await asyncio.sleep(0.3)
        held = tg._in_flight.get(str(q.uid), 0)
        assert held == tg.PUBLIC_CHAT_IN_FLIGHT == tg._public_in_flight[0], (held, tg._public_in_flight[0])
        assert sum(t.done() for t in tasks) == len(tasks) - held
        await asyncio.wait_for(other.welcome(), 2)  # while q still holds all of theirs
        assert any("Do you agree?" in t for t in other.out.texts), other.out.texts
    finally:
        lock.release()
    await asyncio.gather(*tasks)
    assert str(q.uid) not in tg._in_flight and tg._public_in_flight[0] == 0
    ok(f"a stranger: at most {tg.PUBLIC_CHAT_IN_FLIGHT} updates in flight per chat (waiting for its lock), half "
       f"the {tg.PUBLIC_IN_FLIGHT} public slots; the rest dropped at once, and another stranger still gets in")

    # Every photo in an album is its own update, and the gate runs before
    # it knows who's staff: a linked staff member's 40-photo send right
    # after a restart (fresh buckets) must not lose any.
    tg._buckets.reset()
    big = Person(uid=next(_ids) + 700000)
    STAFF_CHATS.add(big.uid)
    link_tg(make_user("Ida Staff", "idastaff", ai_company_key_allowed=True), big.uid)
    lock = tg._lock_for(str(big.uid))
    await lock.acquire()
    try:
        tasks = [asyncio.create_task(big.command(tg.whoami_command, "/whoami")) for _ in range(40)]
        await asyncio.sleep(0.3)
        assert tg._in_flight.get(str(big.uid)) == 40, tg._in_flight.get(str(big.uid))
        assert not any(t.done() for t in tasks)
    finally:
        lock.release()
    await asyncio.gather(*tasks)
    assert len(big.out.texts) == 40 and tp.SLOW_DOWN not in big.out.texts, len(big.out.texts)
    ok("linked staff: 40 updates at once on fresh buckets (a full staff draft) are all kept")

    # The public reply budget, and PUBLIC_IN_FLIGHT, with 40 new strangers
    # at once: every welcome waits its turn.
    tp.replies.rate, tp.replies.burst = tp.REPLIES_PER_SECOND, tp.REPLY_BURST
    tp.replies.reset()
    crowd = [Person(f"Crowd {i}", f"+659999{i:04d}") for i in range(40)]
    SENT_AT.clear()
    peak = [0]
    real = tp._welcome

    async def counting(*args, **kwargs):
        peak[0] = max(peak[0], tg._public_in_flight[0])
        await asyncio.sleep(0.3)  # holds its slot until every one has been through the gate
        await real(*args, **kwargs)

    with mock.patch.object(tp, "_welcome", counting):
        t0 = time.monotonic()
        await asyncio.gather(*(c.command(tg.start, "/start") for c in crowd))
    times = sorted(t for chat, t in SENT_AT if chat in {c.uid for c in crowd})
    welcomed = sum(1 for c in crowd if any("Do you agree?" in t for t in c.out.texts))
    slowed = sum(1 for c in crowd if tp.SLOW_DOWN in c.out.texts)
    assert peak[0] <= tg.PUBLIC_IN_FLIGHT and welcomed == tg.PUBLIC_IN_FLIGHT, (peak[0], welcomed, slowed)
    worst = max(j - i + 1 - (tp.REPLY_BURST + tp.REPLIES_PER_SECOND * (times[j] - times[i]))
                for i in range(len(times)) for j in range(i, len(times)))
    assert worst <= 1, worst
    assert tg._public_in_flight[0] == 0
    ok(f"40 strangers at once: {tg.PUBLIC_IN_FLIGHT} public updates in flight at most ({welcomed} welcomed, "
       f"{slowed} told to slow down), and {len(times)} replies in {times[-1] - t0:.1f}s never beat "
       f"{tp.REPLY_BURST} + {tp.REPLIES_PER_SECOND}/s")
    fresh()


# --- strangers' linking commands: the public budget, and their own throttle -------------------

async def check_strangers_link_commands() -> None:
    print("strangers' /whoami, /link, /unlink and link codes: the public reply budget, throttled")
    fresh()
    tp.replies.rate, tp.replies.burst = tp.REPLIES_PER_SECOND, tp.REPLY_BURST
    tp.replies.reset()
    taken = []
    real_take = tp.replies.try_take

    def counted() -> bool:
        got = real_take()
        taken.append(got)
        return got

    with mock.patch.object(tp.replies, "try_take", counted):
        crowd = [Person(f"Whoami {i}", f"+65877700{i:02d}") for i in range(3)]
        await asyncio.gather(*(c.command(tg.whoami_command, "/whoami") for c in crowd for _ in range(tg.BURST)))
        sent = sum(len(c.out.texts) for c in crowd)
        assert all(sum(t.startswith("Not connected") for t in c.out.texts) == 1 for c in crowd), \
            [c.out.texts for c in crowd]
        assert sent <= 2 * len(crowd) and sum(taken) == sent, (sent, sum(taken))
        ok(f"3 strangers sending {tg.BURST} /whoami each at once: {sent} replies (one 'Not connected' each, and "
           f"'slow down'), every one taken from the public reply budget")

        for handler, words, expect in ((tg.whoami_command, "/whoami", "Not connected"),
                                       (tg.link_command, "/link", "Send me your code like this"),
                                       (tg.unlink_command, "/unlink", "This Telegram isn't connected")):
            s = Person()
            del taken[:]
            for _ in range(10):
                await s.command(handler, words)
            assert len(s.out.texts) == 1 and s.out.texts[0].startswith(expect), s.out.texts
            assert sum(taken) == 1
            tg._told_at[f"pub-command:{s.uid}"] -= tg.COMMAND_EVERY_SECONDS
            await s.command(handler, words)
            assert len(s.out.texts) == 2, s.out.texts
        ok("a stranger's /whoami, /link and /unlink: answered at most every 5 s (like an unvetted account's "
           "commands), from the budget; 10 at once get one answer")

        w = Person()
        for i in range(5):
            await w.text("ZZZZ-ZZZ" + "23456"[i])
        assert w.out.texts[-1] == "Too many wrong codes. Try again in 15 minutes.", w.out.texts[-2:]
        assert sum("didn't work" in t for t in w.out.texts) == 4, w.out.texts
        said = len(w.out.texts)
        for _ in range(10):
            await w.text("ZZZZ-ZZZ7")
            await w.command(tg.start, "/start link-ZZZZZZZ8", "link-ZZZZZZZ8")
        assert len(w.out.texts) == said, w.out.texts[said:]
        tg._told_at[f"pub-code:{w.uid}"] -= tg.CODE_NOTICE_EVERY_SECONDS
        for _ in range(3):
            await w.text("ZZZZ-ZZZ9")
        assert w.out.texts[said:] == ["Too many wrong codes. Try again in 15 minutes."], w.out.texts[said:]
        g = Person()
        for _ in range(5):
            await g.command(tg.start, "/start link-garbage", "link-garbage")
        assert g.out.texts == [tg.NOT_A_CODE], g.out.texts
        ok("wrong codes: 4 'didn't work', then 'Too many wrong codes' once; while blocked, bare codes and deep links "
           "get that notice at most once a minute (and 'not a code' likewise), not once per message")

        # A staff member about to link: their first deep link, even right
        # after a /whoami, and with the budget just drained by a flood.
        k = Person()
        plain = link_code("Kai Staff", "kaistaff")
        await k.command(tg.whoami_command, "/whoami")
        tp.replies._tokens, tp.replies._at = 0.0, time.monotonic()
        del taken[:]
        t0 = time.monotonic()
        await k.command(tg.start, f"/start link-{plain}", f"link-{plain}")
        took = time.monotonic() - t0
        assert "Connect this Telegram to <b>@kaistaff (Kai Staff)</b>" in k.out.texts[-1], k.out.texts
        assert took < 1.0 and any(taken), took
        b = Person()
        plain = link_code("Bea Staff", "beastaff")
        await b.text(f"{plain[:4]}-{plain[4:]}")
        assert "Connect this Telegram to <b>@beastaff (Bea Staff)</b>" in b.out.texts[-1], b.out.texts
        await b.tap("✅ Yes, that's me")
        assert b.out.texts[-1].startswith("Almost done"), b.out.texts[-1]
        ok(f"a staff member's first /start link-CODE gets its prompt from the budget in {took:.2f}s (just after "
           "their /whoami, the budget empty); a bare code too, and [Yes] -> 'Almost done'")

    # /unlink and [Cancel] read before they write: nothing to undo, nothing
    # written.
    fresh()
    u = Person()
    with Writes() as writes:
        await u.command(tg.unlink_command, "/unlink")
        await u.tap(data="link_no:deadbeef")
    assert u.out.texts == ["This Telegram isn't connected to a Carlink account.", tg.LINK_CANCELLED], u.out.texts
    assert writes.statements == [], writes.statements
    with Writes() as writes:  # b said Yes above: /unlink takes it back
        await b.command(tg.unlink_command, "/unlink")
    assert b.out.texts[-1] == "This Telegram isn't connected to a Carlink account.\n" + tg.YES_WITHDRAWN, b.out.texts[-1]
    assert any(s.startswith("UPDATE chat_link_codes") for s in writes.statements), writes.statements
    m = Person("Mid Form", "+6587770077")
    await m.agree()
    await m.text(m.name)
    await m.command(tg.unlink_command, "/unlink")
    assert m.form is not None and m.form.name == m.name, m.out.texts[-1]
    await m.command(tg.cancel_command, "/cancel")
    ok("a stranger's /unlink and [Cancel] with nothing linked or waiting write nothing to the database; with a Yes "
       "waiting it's still taken back; a public form being filled in isn't thrown away by /unlink")


# --- idle forms are closed, and their bytes freed --------------------------------------------

async def check_idle_forms() -> None:
    print("idle public forms are closed, on a timer too")
    fresh()
    for chat, _ in state.public_sessions():
        tg_common._drop(chat)
    # The new sender first: consent itself closes idle forms (_room_for_a_form).
    p = Person("Fresh Sender", "+6587771100")
    await p.fill(photos=0)
    await p.tap("Edit something")
    await p.tap("Photos")
    idle = [Person(f"Idle {i}", f"+65877710{i:02d}") for i in range(2)]
    files = []
    for x in idle:
        await x.agree()
        await x.text(x.name)
        path = tg_common._new_temp_path(".jpg")
        path.write_bytes(b"\xff" * 1000)
        x.session.photo_paths.append(str(path))
        x.form.photo_bytes = 30 * 1024 * 1024  # what Telegram said they were
        x.session.last_active -= tg.SESSION_MAX_IDLE_SECONDS + 60
        files.append(path)
    # Room for one more photo, but not with the idle forms' bytes counted.
    with mock.patch.object(tp, "MAX_TEMP_BYTES", 60 * 1024 * 1024):
        await p.photo()
    await settle()
    assert len(p.form.photo_ids) == 1, p.out.texts[-1]
    assert all(x.session is None for x in idle) and not any(f.exists() for f in files)
    assert tp._temp_bytes() == p.form.photo_bytes
    ok("forms idle over 24 hours (60 MB between them) are closed before the temp-space check: their files deleted, "
       "their bytes freed, a new sender's photo taken")

    x = idle[0]
    await x.text("hello again")
    assert x.out.texts[-1] == tp.EXPIRED, x.out.texts[-1]
    tg._told_at.pop(f"pub-welcome:{x.uid}", None)
    await x.text("hello again")
    assert "Do you agree?" in x.out.texts[-1] and x.out.texts.count(tp.EXPIRED) == 1
    ok("the sender of a form closed that way is told it was closed after 24 hours, once, at their next message")

    with mock.patch.object(tp, "start_pruning", wraps=tp.start_pruning) as started:
        await tg._post_init(SimpleNamespace(bot=mock.AsyncMock()))
    loop = asyncio.get_running_loop()
    assert started.call_count == 1 and tp._pruning is not None and tp._pruning[0] is loop
    assert 0 < tp._pruning[1].when() - loop.time() <= tp.PRUNE_EVERY_SECONDS
    app = tg.build_app()
    assert app.post_init is tg._post_init and app.post_shutdown is tg._post_shutdown
    await app.post_shutdown(app)
    assert tp._pruning is None
    with mock.patch.object(tp, "PRUNE_EVERY_SECONDS", 0.05):
        tp.start_pruning()
        y = Person("Idle Again", "+6587771101")
        await y.agree()
        y.session.last_active -= tg.SESSION_MAX_IDLE_SECONDS + 60
        await asyncio.sleep(0.2)
        assert y.session is None and tp._pruning is not None and not tp._pruning[1].cancelled()
        tp.stop_pruning()
    assert tp._pruning is None
    await p.command(tg.cancel_command, "/cancel")
    ok(f"post_init starts a timer on the bot's loop that closes idle forms every {tp.PRUNE_EVERY_SECONDS // 60} "
       "minutes, whether or not anyone writes (no JobQueue here: PTB's needs APScheduler); post_shutdown stops it")


# --- who someone is: read again under the chat lock ------------------------------------------

async def until_waiting(lock: asyncio.Lock) -> None:
    """Until an update is past the gate and waiting for the chat lock."""
    for _ in range(200):
        if getattr(lock, "_waiters", None):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the update never reached the chat lock")


async def check_lookup_under_the_lock() -> None:
    print("who someone is: read again under the chat lock")
    fresh()

    async def queued(person: Person, meanwhile) -> None:
        lock = tg._lock_for(str(person.uid))
        await lock.acquire()
        try:
            task = asyncio.create_task(person.command(tg.whoami_command, "/whoami"))
            await until_waiting(lock)
            meanwhile()
        finally:
            lock.release()
        await task

    lou = make_user("Lou Staff", "loustaff", ai_company_key_allowed=True)
    s = Person(uid=next(_ids) + 700000)
    STAFF_CHATS.add(s.uid)
    link_tg(lou, s.uid)
    await queued(s, lambda: unlink_tg(s.uid))
    assert len(s.out.texts) == 1 and s.out.texts[0].startswith("Not connected"), s.out.texts
    ok("an admin ends a link while the sender's update waits for the chat lock: it's handled as someone not "
       "connected, not as staff")

    r = Person()
    await queued(r, lambda: link_tg(lou, r.uid))
    assert r.out.texts[-1].startswith("Connected as Lou Staff"), r.out.texts
    unlink_tg(r.uid)
    ok("and a link made meanwhile counts too: the update is handled as staff")


# --- 9. the flood, on a real concurrent_updates app ------------------------------------------

class FloodFile:
    running = 0
    peak = 0

    def __init__(self, data: bytes):
        self.data = data

    async def download_to_drive(self, custom_path=None, **_kw):
        FloodFile.running += 1
        FloodFile.peak = max(FloodFile.peak, FloodFile.running)
        try:
            await asyncio.sleep(0.4)
            Path(custom_path).write_bytes(self.data)
        finally:
            FloodFile.running -= 1


class FloodBot:
    """Stands in for telegram.Bot under a real Application."""
    username = "carlink_test_bot"
    id = 42
    first_name = "Carlink"
    defaults = None
    callback_data_cache = None

    def __init__(self):
        self.sent: list[tuple[int, str, float]] = []

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def send_message(self, chat_id=None, text=None, *args, **kwargs):
        self.sent.append((int(chat_id), str(text), time.monotonic()))

    async def get_file(self, file_id=None, *args, **kwargs):
        return FloodFile(jpeg(abs(hash(file_id)) % 1000))

    async def answer_callback_query(self, *args, **kwargs):
        return True


def wire(bot, uid: int, **message) -> Update:
    msg = {"message_id": next(_ids), "date": int(time.time()), "chat": {"id": uid, "type": "private", "first_name": "x"},
           "from": {"id": uid, "is_bot": False, "first_name": "x"}, **message}
    return Update.de_json({"update_id": next(_ids), "message": msg}, bot)


async def check_flood() -> None:
    print("9. staff are served while 30 strangers flood (real app, concurrent_updates)")
    fresh()
    app = tg.build_app()
    assert app.update_processor.max_concurrent_updates == tg.CONCURRENT_UPDATES == 64
    bot = FloodBot()
    app.bot = bot
    app._initialized = True  # initialize() would call Telegram's getMe
    strangers = [next(_ids) + 900000 for _ in range(30)]
    for uid in strangers:
        session = state.get_session(str(uid))
        session.public = PublicForm(nonce="feedf00d", consent_at=utc_now(), step=PublicStep.PHOTOS, name="F",
                                    role="driver", phone="+6590000000", statement="flood")
    staff = make_user("Fay Staff", "faystaff", ai_company_key_allowed=True)
    staff_tg = next(_ids) + 950000
    link_tg(staff, staff_tg)

    def run(update):
        return app.update_processor.process_update(update, app.process_update(update))

    flood = []
    for uid in strangers:
        for i in range(10):
            flood.append(wire(bot, uid, photo=[{"file_id": f"f{uid}-{i}", "file_unique_id": f"u{uid}-{i}",
                                                "width": 1280, "height": 960, "file_size": 1000}]))
    tasks = [asyncio.create_task(run(u)) for u in flood]
    for _ in range(40):
        await asyncio.sleep(0.05)
        if tg._public_in_flight[0] == tg.PUBLIC_IN_FLIGHT:
            break
    assert tg._public_in_flight[0] == tg.PUBLIC_IN_FLIGHT, tg._public_in_flight[0]
    t0 = time.monotonic()
    whoami = wire(bot, staff_tg, text="/whoami", entities=[{"type": "bot_command", "offset": 0, "length": 7}])
    await asyncio.wait_for(run(whoami), 5)
    took = time.monotonic() - t0
    pending = sum(not t.done() for t in tasks)
    assert any(chat == staff_tg and text.startswith("Connected as Fay Staff") for chat, text, _ in bot.sent), bot.sent[-3:]
    assert took < 1.5 and pending > 0, (took, pending)
    await asyncio.gather(*tasks)
    assert FloodFile.peak <= tp.MAX_DOWNLOADS, FloodFile.peak
    kept = sum(len(state.find_session(str(uid)).public.photo_ids) for uid in strangers)
    assert 0 < kept <= tg.PUBLIC_IN_FLIGHT + 10, kept
    for uid in strangers:
        tg_common._drop(str(uid))
    assert tg._public_in_flight[0] == 0 and not tg._in_flight
    ok(f"300 photos from 30 strangers: {tg.PUBLIC_IN_FLIGHT} public updates in flight, the rest dropped; staff's "
       f"/whoami answered in {took:.2f}s while {pending} were still pending; {FloodFile.peak} downloads at once at most")

    mark = len(LOG.lines)
    secret = wire(bot, strangers[0], text="my name is Hidden Person, call +6590001111")
    await tg._on_error(secret, SimpleNamespace(error=ValueError("Hidden Person +6590001111")))
    line = "\n".join(LOG.lines[mark:])
    assert f"Telegram update {secret.update_id} (message) failed: ValueError" in line, line
    assert "Hidden" not in line and "6590001111" not in line, line
    assert tg._on_error in app.error_handlers and logging.getLogger("telegram").getEffectiveLevel() >= logging.INFO
    ok("the error handler logs the update's id and kind and the error's type, never the update or the error's "
       "text (L3); PTB's own logs stay at INFO or above")

    edited = [
        Update.de_json({"update_id": next(_ids), "edited_message": wire(bot, 1, **body).message.to_dict()}, bot)
        for body in ({"contact": {"phone_number": "+6591234567", "first_name": "x", "user_id": 1}},
                     {"location": {"latitude": 1.3, "longitude": 103.8}},
                     {"text": "/withdraw", "entities": [{"type": "bot_command", "offset": 0, "length": 9}]},
                     {"sticker": {"file_id": "s", "file_unique_id": "su", "width": 1, "height": 1, "is_animated": False,
                                  "is_video": False, "type": "regular"}})
    ]
    assert not any(h.check_update(u) for u in edited for h in app.handlers[0])
    ok("edited contact cards, location pins, /withdraw and stickers reach no handler")


def check_lock_pruning() -> None:
    print("L5: chat locks")
    tg_common._chat_locks.clear()
    for i in range(tg_common.MAX_ENTRIES):
        tg_common._chat_locks[f"old{i}"] = asyncio.Lock()
    held_lock = tg_common._chat_locks["old7"]
    held_lock._locked = True  # held by a handler
    tg_common._lock_for("new")
    assert set(tg_common._chat_locks) == {"old7", "new"}, len(tg_common._chat_locks)
    tg_common._chat_locks.clear()
    ok("past 5,000 chats, the locks nobody holds or waits for are forgotten")


# --- 13. logs ----------------------------------------------------------------------------------

def check_logs() -> None:
    print("13. logs")
    found = [(line[:120], x) for line in LOG.lines for x in PERSONAL if x and x in line]
    assert not found, found[:3]
    assert any(re.search(r"Telegram user \d+ sent public report TG-", line) for line in LOG.lines)
    assert not AI_CALLS, AI_CALLS
    ok(f"{len(LOG.lines)} log lines: Telegram ids and references, never a sender's name, phone or statement; "
       "no AI or PDF call was ever made")


async def run_async_checks() -> None:
    await check_before_consent()
    await check_happy_path()
    await check_contacts()
    await check_buttons_stay_apart()
    await check_with_p5()
    await check_caps()
    await check_photos()
    await check_commands()
    await check_withdraw()
    await check_gate()
    await check_strangers_link_commands()
    await check_idle_forms()
    await check_lookup_under_the_lock()
    await check_flood()


def main() -> int:
    init_db()
    app_log = logging.getLogger("app")
    app_log.addHandler(LOG)
    app_log.setLevel(logging.DEBUG)
    for patch in NO_AI:
        patch.start()
    try:
        with mock.patch.object(tp, "ALBUM_SECONDS", 0.05):
            asyncio.run(run_async_checks())
        check_lock_pruning()
        check_logs()
    finally:
        for patch in NO_AI:
            patch.stop()
    print("\nALL TELEGRAM PUBLIC CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
