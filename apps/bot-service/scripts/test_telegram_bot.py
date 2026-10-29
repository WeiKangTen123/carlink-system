"""Tests for the Telegram bot (app/channels/telegram.py): who it works for
(P5: a Telegram connected to an active account, in a private chat), the
linking rules on the bot's side, filing as the linked person, one case per
Confirm, temp photos, stale drafts, photo limits, and handlers that only
take new messages.

    python scripts/test_telegram_bot.py

Offline: fake Telegram updates and downloads, no AI call, no PDF render, a
temporary database and storage folder. scripts/test_telegram_link_e2e.py
drives the whole link through the real handlers and the API.
"""
import asyncio
import logging
import os
import re
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="telegram-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake-company-key"
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:fake-token-for-tests"
os.environ["PUBLIC_BASE_URL"] = "https://carlink.test/"
# The disk guard has its own test (test_abuse_limits.py).
os.environ["MIN_FREE_DISK_GB"] = "0"

from PIL import Image  # noqa: E402
from telegram import Chat, Message, MessageEntity, PhotoSize, Update, User as TgUser  # noqa: E402

import app.channels.telegram as tg  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.api import limits  # noqa: E402
from app.auth.chat_links import confirm_from_web, new_code, wrong_codes  # noqa: E402
from app.auth.core import utc_now  # noqa: E402
from app.config import settings  # noqa: E402
from app.conversation import state  # noqa: E402
from app.conversation.flow import DraftResult  # noqa: E402
from app.conversation.state import Stage  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import AuditEntry, ChatLink, ChatLinkCode, Report, User  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402
from app.storage.files import tmp_dir  # noqa: E402

BASE = "https://carlink.test"
# Telegram user ids; in a private chat the chat id is the same.
ANN_TG, BEN_TG, HANA_TG, URI_TG = 1001, 1002, 1003, 1004
PAST_REPORTER = 777  # filed a case before P5, never connected
STRANGER = 5555
_photo_bytes = Path(_tmp, "sample.jpg")
Image.new("RGB", (64, 48), (200, 30, 30)).save(_photo_bytes, "JPEG")
P = SimpleNamespace()  # account ids, filled by make_people()


def ok(label: str) -> None:
    print(f"    ok   {label}")


# --- people and links ----------------------------------------------------------

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


def relink_tg(tg_id: int, user_id: str) -> None:
    db = SessionLocal()
    try:
        db.query(ChatLink).filter(ChatLink.external_id == str(tg_id)).update({"user_id": user_id})
        db.commit()
    finally:
        db.close()


def make_code(user_id: str) -> str:
    db = SessionLocal()
    try:
        plain, _ = new_code(db, db.get(User, user_id))
        db.commit()
        return f"{plain[:4]}-{plain[4:]}"
    finally:
        db.close()


def make_people() -> None:
    # Ticked for the company key (GEMINI_API_KEY above), so AI is on.
    P.ann = make_user("Ann Lim", "ann", ai_company_key_allowed=True)
    P.ben = make_user("Ben Tan", "ben", ai_company_key_allowed=True, may_sign_off=True)
    P.cal = make_user("Cal Ong", "cal", ai_company_key_allowed=True)
    # No key of her own and no tick: AI is off for her.
    P.hana = make_user("Hana Lee", "hana")
    # No ticks at all: held to the daily case cap (api/limits.py).
    P.uri = make_user("Uri Goh", "uri")
    P.dot = make_user("Dot Lee", "dot")
    P.eve = make_user("Eve Sim", "eve")
    for user_id, tg_id in ((P.ann, ANN_TG), (P.ben, BEN_TG), (P.hana, HANA_TG), (P.uri, URI_TG)):
        link_tg(user_id, tg_id)


# --- fakes -----------------------------------------------------------------

class Outbox:
    """Everything the bot sent to one chat."""

    def __init__(self):
        self.texts: list[str] = []
        self.markups: list = []
        self.documents: list[tuple[str, bytes]] = []
        self.downloads: list[str] = []

    def buttons(self, index: int = -1) -> dict[str, str]:
        markup = self.markups[index]
        return {b.text: b.callback_data for row in (markup.inline_keyboard if markup else ()) for b in row}


class FakeMessage:
    def __init__(self, outbox: Outbox, text=None, caption=None, photo=False, document=None, forwarded=False):
        self.outbox = outbox
        self.text = text
        self.caption = caption
        self.photo = [FakePhoto(outbox)] if photo else []
        self.document = document
        self.forward_origin = SimpleNamespace(type="user") if forwarded else None

    async def reply_text(self, text, **kw):
        await asyncio.sleep(0)  # a real reply is a network round trip
        self.outbox.texts.append(text)
        self.outbox.markups.append(kw.get("reply_markup"))

    async def reply_document(self, document=None, filename=None, **_kw):
        self.outbox.documents.append((filename, document.read()))


class FakeFile:
    def __init__(self, outbox: Outbox):
        self.outbox = outbox

    async def download_to_drive(self, path):
        Path(path).write_bytes(_photo_bytes.read_bytes())
        self.outbox.downloads.append(path)


class FakePhoto:
    def __init__(self, outbox: Outbox):
        self.outbox = outbox

    async def get_file(self):
        return FakeFile(self.outbox)


class FakeDocument(FakePhoto):
    def __init__(self, outbox: Outbox, mime_type: str, file_name: str):
        super().__init__(outbox)
        self.mime_type = mime_type
        self.file_name = file_name


class FakeQuery:
    def __init__(self, data: str, message: FakeMessage):
        self.data = data
        self.message = message
        self.answers: list = []
        self.buttons_removed = False

    async def answer(self, text=None, **_kw):
        await asyncio.sleep(0)
        self.answers.append(text)

    async def edit_message_reply_markup(self, reply_markup=None, **_kw):
        self.buttons_removed = reply_markup is None


def sender(user_id: int):
    return SimpleNamespace(id=user_id, username=f"tg{user_id}", full_name=f"Phone {user_id}", first_name="Phone")


def message_update(chat_id: int, outbox: Outbox, user_id=None, chat_type="private", **message_kw):
    msg = FakeMessage(outbox, **message_kw)
    return SimpleNamespace(
        effective_user=sender(user_id or chat_id),
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_message=msg,
        message=msg,
        callback_query=None,
    )


def tap(chat_id: int, outbox: Outbox, data: str = "confirm", user_id=None, chat_type="private"):
    query = FakeQuery(data, FakeMessage(outbox))
    return SimpleNamespace(
        effective_user=sender(user_id or chat_id),
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_message=query.message,
        message=None,
        callback_query=query,
    )


async def send_photo(chat_id: int, outbox: Outbox, user_id=None, chat_type="private"):
    await tg.handle_photo(message_update(chat_id, outbox, user_id, chat_type, photo=True), None)


async def send_text(chat_id: int, outbox: Outbox, text: str, user_id=None, chat_type="private", forwarded=False):
    await tg.handle_text(message_update(chat_id, outbox, user_id, chat_type, text=text, forwarded=forwarded), None)


async def command(handler, chat_id: int, outbox: Outbox, *args: str):
    words = " ".join(args)
    await handler(message_update(chat_id, outbox, text=f"/x {words}".strip()), SimpleNamespace(args=list(args)))


# --- patched AI and PDF ------------------------------------------------------

DRAFT_CALLS: list[list[str]] = []
ACTORS: list = []
RENDERS: list = []  # the unverified flag of each render
DURING_RENDER: list = []  # callables run while the "PDF" renders
RENDER_STARTED = threading.Event()
RENDER_SECONDS = 0.0


def fake_build_draft(description, photo_paths, session=None, **kw):
    ACTORS.append(kw.get("actor"))
    DRAFT_CALLS.append(list(photo_paths))
    for p in photo_paths:  # the real call reads every photo
        assert Path(p).exists(), f"drafted with a missing photo {p}"
    return DraftResult(draft=SecurityIncidentDraft(description=description), summary_text="Draft summary")


def fake_render_pdf(data, photo_paths, output_path, report_id=None, unverified=None, slot_wait=None):
    RENDERS.append(unverified)
    RENDER_STARTED.set()
    while DURING_RENDER:
        DURING_RENDER.pop()()
    time.sleep(RENDER_SECONDS)
    Path(output_path).write_bytes(b"%PDF-1.4 fake")


def cases_for(chat_id: int) -> list[Report]:
    db = SessionLocal()
    try:
        return db.query(Report).filter(Report.reporter_chat_id == str(chat_id)).all()
    finally:
        db.close()


def add_case(chat_id: str, channel: str, owner_id=None) -> None:
    db = SessionLocal()
    try:
        db.add(Report(channel=channel, reporter_chat_id=chat_id, data={}, status="confirmed", owner_id=owner_id))
        db.commit()
    finally:
        db.close()


def incident_dirs() -> set[str]:
    root = Path(_tmp, "incidents")
    return {p.name for p in root.glob("*/*/*")} if root.is_dir() else set()


async def ready_to_confirm(chat_id: int, outbox: Outbox, photos: int = 1):
    for _ in range(photos):
        await send_photo(chat_id, outbox)
    await send_text(chat_id, outbox, "Rear-ended at PIE exit 12")
    session = state.get_session(str(chat_id))
    assert session.stage == Stage.AWAITING_CONFIRMATION and session.draft is not None, outbox.texts[-3:]
    return session


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def messages(self, level=logging.INFO) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno == level]


# --- who may use the bot -------------------------------------------------------

async def check_not_connected(log: LogCapture) -> None:
    print("who may use the bot")
    assert not hasattr(settings, "telegram_allowed_ids")
    assert not any(hasattr(tg, name) for name in ("_staff_only", "_allowed_ids", "_load_allowed_ids", "REFUSAL_TEXT"))
    out = Outbox()
    secret = "my plate is SECRET123"
    before = len(cases_for(STRANGER))
    with mock.patch.object(tg, "build_draft", side_effect=AssertionError("AI called for someone not connected")):
        await send_photo(STRANGER, out)
        await send_text(STRANGER, out, secret)
        await tg.handle_callback(tap(STRANGER, out), None)
        await tg.handle_document(message_update(STRANGER, out, document=FakeDocument(out, "image/png", "scan.png")), None)
        await tg.cancel_command(message_update(STRANGER, out, text="/cancel"), None)
    assert len(out.texts) == 1 and "connect" in out.texts[0].lower(), out.texts
    assert f"{BASE}/settings#my-telegram" in out.texts[0], out.texts[0]
    assert out.downloads == [] and str(STRANGER) not in state._sessions
    assert len(cases_for(STRANGER)) == before
    told = [m for m in log.messages() if "connect first" in m]
    assert len(told) == 1 and str(STRANGER) in told[0], told
    assert not any("SECRET123" in r.getMessage() for r in log.records)
    ok("not connected: one 'connect first' with the Settings link; no download, session, AI or case; ids logged, not the text")

    await tg.start(message_update(STRANGER, out, text="/start"), None)
    await tg.help_command(message_update(STRANGER, out, text="/help"), None)
    assert len(out.texts) == 3 and all(f"{BASE}/settings#my-telegram" in t for t in out.texts[1:]), out.texts
    assert str(STRANGER) not in state._sessions
    ok("/start and /help always answer, with how to connect")

    tg._told_at[f"connect:{STRANGER}"] -= tg.REPLY_EVERY_SECONDS + 1
    await send_text(STRANGER, out, "hello?")
    assert len(out.texts) == 4 and "connect" in out.texts[-1].lower()
    ok("told again only once the hour has passed")

    add_case(str(PAST_REPORTER), "telegram")
    await tg._post_init(SimpleNamespace(bot=mock.AsyncMock()))
    out = Outbox()
    await send_photo(PAST_REPORTER, out)
    assert out.downloads == [] and "connect" in out.texts[-1].lower() and str(PAST_REPORTER) not in state._sessions
    ok("no allow-list: a chat that filed a case before P5 isn't served until it connects")


async def check_group_chat() -> None:
    out = Outbox()
    for _ in range(2):
        await send_photo(-4242, out, user_id=ANN_TG, chat_type="group")
        await send_text(-4242, out, "rear-ended at PIE", user_id=ANN_TG, chat_type="supergroup")
    await tg.link_command(message_update(-4242, out, ANN_TG, "group", text="/link ABCD-2345"), SimpleNamespace(args=["ABCD-2345"]))
    group_tap = tap(-4242, out, user_id=ANN_TG, chat_type="group")
    await tg.handle_callback(group_tap, None)
    assert len(out.texts) == 1 and "private chat" in out.texts[0], out.texts
    assert out.downloads == [] and "-4242" not in state._sessions and "-4242" not in tg._chat_locks
    assert group_tap.callback_query.answers == [None]
    ok("a group chat, even from a connected person: one 'private chat only' an hour; no download, session or lock")


async def check_link_ends_mid_draft() -> None:
    out = Outbox()
    session = await ready_to_confirm(ANN_TG, out)
    temp = list(session.photo_paths)
    unlink_tg(ANN_TG)
    before = len(cases_for(ANN_TG))
    await tg.handle_callback(tap(ANN_TG, out), None)
    assert len(cases_for(ANN_TG)) == before
    assert "connect" in out.texts[-1].lower(), out.texts[-1]
    assert str(ANN_TG) not in state._sessions and not any(Path(p).exists() for p in temp)
    link_tg(P.ann, ANN_TG)
    ok("the link ends mid-draft: Confirm files nothing; the draft and its photos are thrown away")

    session = await ready_to_confirm(BEN_TG, out)
    temp = list(session.photo_paths)
    relink_tg(BEN_TG, P.cal)
    before = len(cases_for(BEN_TG))
    await tg.handle_callback(tap(BEN_TG, out), None)
    assert len(cases_for(BEN_TG)) == before
    assert "discarded" in out.texts[-1] and "Cal Ong (@cal)" in out.texts[-1], out.texts[-1]
    fresh = state.get_session(str(BEN_TG))
    assert fresh.draft is None and fresh.photo_paths == [] and fresh.carlink_user_id == P.cal
    assert not any(Path(p).exists() for p in temp)
    relink_tg(BEN_TG, P.ben)
    state.reset_session(str(BEN_TG))
    ok("connected to another account mid-draft: the old draft and its photos are thrown away, not filed as the new one")


# --- linking -------------------------------------------------------------------

def code_row(user_id: str) -> ChatLinkCode | None:
    db = SessionLocal()
    try:
        return db.query(ChatLinkCode).filter(ChatLinkCode.user_id == user_id).order_by(ChatLinkCode.id.desc()).first()
    finally:
        db.close()


async def check_linking() -> None:
    print("linking")
    wrong_codes.reset()
    dot_tg = 2001
    code = make_code(P.dot)
    out = Outbox()
    for words in ("SJB8762M", "STANDARD", "GBH 2345 K", "sjb8-762"):
        await send_text(dot_tg, out, words)
    assert len(out.texts) == 1 and "connect" in out.texts[0].lower(), out.texts
    await send_text(dot_tg, out, code, forwarded=True)
    assert len(out.texts) == 1, out.texts[-1]  # just more text from someone not connected
    forwarded_link = message_update(dot_tg, out, text=f"/link {code}", forwarded=True)
    await tg.link_command(forwarded_link, SimpleNamespace(args=[code]))
    assert "forwarded" in out.texts[-1] and not out.buttons(), out.texts[-1]
    assert code_row(P.dot).waiting_since is None
    ok("plates and words aren't codes; a code in a forwarded message (bare or /link) is ignored")

    for _ in range(4):
        await command(tg.link_command, dot_tg, out, "ZZZZ-ZZZZ")
    assert "1 try left" in out.texts[-1], out.texts[-1]
    await send_text(dot_tg, out, code)
    prompt = out.texts[-1]
    assert "Connect this Telegram to <b>Dot Lee (@dot)</b>, d***@example.com?" in prompt, prompt
    assert "If someone sent you this code, tap Cancel" in prompt
    old_buttons = out.buttons()
    assert set(old_buttons) == {"✅ Yes, that's me", "Cancel"}, old_buttons
    assert not any(code.replace("-", "") in (data or "") or code in (data or "") for data in old_buttons.values())
    ok("4 wrong then a bare code: the prompt names Name (@username) and the masked email; the code isn't in the buttons")

    await command(tg.start, dot_tg, out, f"link-{code.replace('-', '')}")
    new_buttons = out.buttons()
    assert "Connect this Telegram to <b>Dot Lee" in out.texts[-1] and new_buttons != old_buttons
    stale = tap(dot_tg, out, old_buttons["✅ Yes, that's me"])
    await tg.handle_callback(stale, None)
    assert out.texts[-1] == tg.PROMPT_EXPIRED and stale.callback_query.buttons_removed
    assert code_row(P.dot).waiting_since is None
    ok("the deep link prompts too; [Yes] on the older prompt does nothing")

    await tg.handle_callback(tap(dot_tg, out, new_buttons["✅ Yes, that's me"]), None)
    assert out.texts[-1].startswith("Almost done: confirm in Carlink → Settings → My Telegram"), out.texts[-1]
    row = code_row(P.dot)
    assert row.waiting_since is not None and row.used_by_external_id == str(dot_tg)
    assert row.used_by_username == f"tg{dot_tg}" and row.used_by_name == f"Phone {dot_tg}"
    await command(tg.whoami_command, dot_tg, out)
    assert out.texts[-1].startswith("Not connected"), out.texts[-1]
    ok("[Yes]: 'Almost done', the code waits for Carlink with the Telegram's id and names; not connected yet")

    db = SessionLocal()
    try:
        confirm_from_web(db, db.get(User, P.dot), True)
        db.commit()
    finally:
        db.close()
    await command(tg.whoami_command, dot_tg, out)
    assert out.texts[-1].startswith("Connected as Dot Lee (@dot)."), out.texts[-1]
    eve_code = make_code(P.eve)
    await send_text(dot_tg, out, eve_code)
    assert "photo" in out.texts[-1] and not out.buttons(), out.texts[-1]
    await command(tg.link_command, dot_tg, out, eve_code)
    assert "already connected to Dot Lee (@dot)" in out.texts[-1] and "/unlink" in out.texts[-1]
    assert code_row(P.eve).waiting_since is None
    ok("once connected (web side too): /whoami names the account; another code is refused, a bare one is just text")

    await send_photo(dot_tg, out)  # Dot has no AI: nothing downloaded, but a session exists
    await command(tg.unlink_command, dot_tg, out)
    assert out.texts[-1].startswith("Disconnected: this Telegram no longer files reports as Dot Lee (@dot)."), out.texts[-1]
    assert str(dot_tg) not in state._sessions
    db = SessionLocal()
    try:
        assert db.query(ChatLink).filter(ChatLink.external_id == str(dot_tg)).count() == 0
        last = db.query(AuditEntry).filter(AuditEntry.action == "telegram_unlinked").order_by(AuditEntry.id.desc()).first()
        assert last.user_id == P.dot and "/unlink" in last.detail, (last.user_id, last.detail)
    finally:
        db.close()
    await command(tg.unlink_command, dot_tg, out)
    assert out.texts[-1] == "This Telegram isn't connected to a Carlink account."
    ok("/unlink: 'Disconnected', the link gone with a history entry, the chat's session dropped")

    # A bare code from a Telegram with a draft still open (its link ended
    # mid-draft) is part of that draft, not a code.
    out = Outbox()
    link_tg(P.eve, 2002)
    db = SessionLocal()
    try:
        db.query(User).filter(User.id == P.eve).update({"ai_company_key_allowed": True})
        db.commit()
    finally:
        db.close()
    await send_photo(2002, out)
    unlink_tg(2002)
    fresh_code = make_code(P.eve)
    await send_text(2002, out, fresh_code)
    assert not out.buttons() and "connect" in out.texts[-1].lower(), out.texts[-1]
    await send_text(2002, out, fresh_code)
    assert "Connect this Telegram to <b>Eve Sim" in out.texts[-1], out.texts[-1]
    await tg.handle_callback(tap(2002, out, out.buttons()["Cancel"]), None)
    assert out.texts[-1] == tg.LINK_CANCELLED and code_row(P.eve).waiting_since is None
    ok("a bare code isn't taken while a draft is open; [Cancel] connects nothing")


# --- filing ---------------------------------------------------------------------

async def check_drafting_as_the_person() -> None:
    print("filing")
    out = Outbox()
    state.reset_session(str(ANN_TG))
    await ready_to_confirm(ANN_TG, out)
    assert ACTORS[-1] == AiActor.for_user(P.ann, "telegram"), ACTORS[-1]
    assert out.texts[-1].startswith("👤 Filing as Ann Lim (@ann)\n\nDraft summary"), out.texts[-1]
    ok("drafting runs on the linked person's AI (AiActor.for_user), and the summary starts 'Filing as Name'")

    out = Outbox()
    for _ in range(3):  # an album
        await send_photo(HANA_TG, out)
    assert out.downloads == [] and len(out.texts) == 1, out.texts
    assert out.texts[0].startswith("AI is off for your account – add your key in Carlink → Settings → My AI key")
    assert f"{BASE}/settings#my-ai-key" in out.texts[0]
    assert state.get_session(str(HANA_TG)).photo_paths == []
    ok("AI off for the person: one plain 'add your key' message for an album, nothing downloaded")


async def check_one_confirm() -> None:
    out = Outbox()
    chat = ANN_TG
    state.reset_session(str(chat))
    session = await ready_to_confirm(chat, out, photos=2)
    temp_files = list(session.photo_paths)
    assert all(re.fullmatch(r"tg-[0-9a-f]{32}\.jpg", Path(p).name) for p in temp_files), temp_files
    assert all(Path(p).parent == tmp_dir() for p in temp_files)
    ok("temp photos are named storage/tmp/tg-<uuid4 hex>.jpg")

    earlier = {r.id for r in cases_for(chat)}
    RENDERS.clear()
    await tg.handle_callback(tap(chat, out), None)
    new = [r for r in cases_for(chat) if r.id not in earlier]
    assert len(new) == 1, new
    case = new[0]
    assert case.owner_id == P.ann and case.channel == "telegram" and case.reporter_chat_id == str(ANN_TG)
    db = SessionLocal()
    try:
        history = [(e.action, e.user_id, e.user_name, e.detail) for e in db.query(AuditEntry).filter(AuditEntry.report_id == case.id)]
    finally:
        db.close()
    assert history == [("created", P.ann, "Ann Lim (@ann)", "Filed through the Telegram bot")], history
    assert out.documents == []
    assert "saved to your cases" in out.texts[-1] and f"\n{BASE}/reports/{case.id}\n" in out.texts[-1], out.texts[-1]
    assert len(case.photo_paths) == 2 and all(Path(p).exists() for p in case.photo_paths)
    assert case.pdf_path and Path(case.pdf_path).exists() and RENDERS == [True]
    ok("one Confirm saves one case as Ann's ('created - Filed through the Telegram bot' by her) and replies "
       "with its link, no PDF file; her PDF has the unverified banner")

    assert not any(Path(p).exists() for p in temp_files), [p for p in temp_files if Path(p).exists()]
    fresh = state.get_session(str(chat))
    assert fresh.draft is None and fresh.photo_paths == [] and not fresh.finalizing
    ok("the temp files are deleted once the case is saved, and the session is reset")


async def check_double_confirm() -> None:
    global RENDER_SECONDS
    out = Outbox()
    chat = BEN_TG
    state.reset_session(str(chat))
    await ready_to_confirm(chat, out)
    RENDERS.clear()
    first, second = tap(chat, out), tap(chat, out)
    await asyncio.gather(tg.handle_callback(first, None), tg.handle_callback(second, None))
    assert len(cases_for(chat)) == 1, cases_for(chat)
    assert sum("saved to your cases" in t for t in out.texts) == 1
    assert second.callback_query.answers == ["Already saving this report -- one moment."], second.callback_query.answers
    assert RENDERS == [False]  # Ben may sign off: no unverified banner
    ok("two concurrent Confirm taps save one case; the second is just answered")

    # A tap that comes while the first one is rendering the PDF.
    await ready_to_confirm(chat, out)
    RENDER_STARTED.clear()
    RENDER_SECONDS = 0.5
    try:
        first_task = asyncio.create_task(tg.handle_callback(tap(chat, out), None))
        assert await asyncio.to_thread(RENDER_STARTED.wait, 5)
        late = tap(chat, out)
        await tg.handle_callback(late, None)
        assert late.callback_query.answers == ["Already saving this report -- one moment."]
        await first_task
    finally:
        RENDER_SECONDS = 0.0
    assert len(cases_for(chat)) == 2  # one from each round
    assert not state.get_session(str(chat)).finalizing
    ok("a tap during the save is answered without saving; the flag clears afterwards")

    # A typed "confirm" right after a tapped one waits for the lock, then
    # finds the session already reset.
    await ready_to_confirm(chat, out)
    await asyncio.gather(tg.handle_callback(tap(chat, out), None), send_text(chat, out, "confirm"))
    assert len(cases_for(chat)) == 3
    ok("a typed confirm racing a tapped one also saves only one case")


async def check_failed_save_can_retry() -> None:
    out = Outbox()
    chat = ANN_TG
    state.reset_session(str(chat))
    await ready_to_confirm(chat, out)
    before, folders = len(cases_for(chat)), incident_dirs()
    with mock.patch.object(tg, "audit", side_effect=RuntimeError("disk full")):
        try:
            await tg.handle_callback(tap(chat, out), None)
            raise AssertionError("save error was swallowed")
        except RuntimeError:
            pass
    session = state.get_session(str(chat))
    assert not session.finalizing and session.draft is not None
    assert all(Path(p).exists() for p in session.photo_paths)
    assert len(cases_for(chat)) == before and incident_dirs() == folders
    await tg.handle_callback(tap(chat, out), None)
    assert len(cases_for(chat)) == before + 1
    ok("a save that fails leaves no case or files behind, clears the flag and keeps the draft, so a retry works")


async def check_confirm_rechecks() -> None:
    out = Outbox()
    chat = URI_TG
    for _ in range(limits.MAX_NEW_CASES_PER_DAY):
        add_case("dashboard", "manual", owner_id=P.uri)
    # AI on for him here: the cap is what's being checked.
    with mock.patch.object(tg, "ensure_ai_ready", lambda actor: None):
        await ready_to_confirm(chat, out)
    before, folders = len(cases_for(chat)), incident_dirs()
    await tg.handle_callback(tap(chat, out), None)
    assert len(cases_for(chat)) == before and incident_dirs() == folders
    assert f"{limits.MAX_NEW_CASES_PER_DAY} cases in the last 24 hours" in out.texts[-1], out.texts[-1]
    assert state.get_session(str(chat)).draft is not None
    ok("an account without staff ticks past its daily case cap: told so, nothing saved, the draft kept")

    out = Outbox()
    session = await ready_to_confirm(ANN_TG, out)
    temp = list(session.photo_paths)
    before, folders = len(cases_for(ANN_TG)), incident_dirs()
    DURING_RENDER.append(lambda: unlink_tg(ANN_TG))
    await tg.handle_callback(tap(ANN_TG, out), None)
    assert len(cases_for(ANN_TG)) == before and incident_dirs() == folders
    assert out.texts[-1] == tg.NO_LONGER_CONNECTED, out.texts[-1]
    assert str(ANN_TG) not in state._sessions and not any(Path(p).exists() for p in temp)
    link_tg(P.ann, ANN_TG)
    ok("the link ends while the PDF renders: nothing filed, the case's files removed, the draft dropped")


async def check_handlers_share_the_lock() -> None:
    out = Outbox()
    chat = ANN_TG
    state.reset_session(str(chat))
    await send_photo(chat, out)
    temp = state.get_session(str(chat)).photo_paths[0]
    lock = tg._lock_for(str(chat))
    await lock.acquire()
    try:
        cancel = asyncio.create_task(tg.cancel_command(message_update(chat, out, text="/cancel"), None))
        # An unknown insurer button: takes the lock, then does nothing.
        button = asyncio.create_task(tg.handle_callback(tap(chat, out, data="ins_unknown"), None))
        whoami = asyncio.create_task(tg.whoami_command(message_update(chat, out, text="/whoami"), None))
        await asyncio.sleep(0.05)
        assert not cancel.done() and not button.done() and not whoami.done()
        assert Path(temp).exists() and state.get_session(str(chat)).photo_paths == [temp]
    finally:
        lock.release()
    await asyncio.gather(cancel, button, whoami)
    assert not Path(temp).exists()
    ok("commands and button taps wait for the chat lock; /cancel deletes the draft's temp files")

    await send_photo(chat, out)
    temp = state.get_session(str(chat)).photo_paths[0]
    await tg.start(message_update(chat, out, text="/new"), None)
    assert not Path(temp).exists() and state.get_session(str(chat)).photo_paths == []
    ok("/new deletes the old draft's temp files too")


async def check_photo_limit() -> None:
    out = Outbox()
    chat = ANN_TG
    state.reset_session(str(chat))
    for _ in range(tg.MAX_PHOTOS_PER_DRAFT):
        await send_photo(chat, out)
    assert len(state.get_session(str(chat)).photo_paths) == 40
    downloads = len(out.downloads)
    await send_photo(chat, out)
    assert len(out.downloads) == downloads and len(state.get_session(str(chat)).photo_paths) == 40
    assert "40 photos is the limit" in out.texts[-1], out.texts[-1]
    doc = FakeDocument(out, "image/png", "scan.png")
    await tg.handle_document(message_update(chat, out, document=doc), None)
    assert len(out.downloads) == downloads and "40 photos is the limit" in out.texts[-1]
    ok("the 41st photo (or image file) is refused without downloading")

    state.reset_session(str(chat))
    await tg.handle_document(message_update(chat, out, document=FakeDocument(out, "application/pdf", "estimate.pdf")), None)
    await tg.handle_document(message_update(chat, out, document=FakeDocument(out, "image/gif", "anim.gif")), None)
    assert len(out.downloads) == downloads and state.get_session(str(chat)).photo_paths == []
    assert "only use photos" in out.texts[-1]
    await tg.handle_document(message_update(chat, out, document=FakeDocument(out, "image/png", "scan.png")), None)
    paths = state.get_session(str(chat)).photo_paths
    assert len(paths) == 1 and re.fullmatch(r"tg-[0-9a-f]{32}\.png", Path(paths[0]).name), paths
    ok("documents: PDFs and other non-photo types refused unread; a PNG is kept as tg-<hex>.png")


async def check_stale_draft() -> None:
    out = Outbox()
    chat = BEN_TG
    state.reset_session(str(chat))
    session = await ready_to_confirm(chat, out)
    temp = list(session.photo_paths)
    session.last_active -= tg.SESSION_MAX_IDLE_SECONDS + 60
    before = len(cases_for(chat))
    await tg.handle_callback(tap(chat, out), None)
    assert "expired" in out.texts[-1], out.texts[-1]
    assert len(cases_for(chat)) == before
    fresh = state.get_session(str(chat))
    assert fresh is not session and fresh.draft is None and fresh.photo_paths == []
    assert not any(Path(p).exists() for p in temp)
    ok("Confirm on a draft idle over 24h: told it expired, nothing saved, temp files deleted")

    session = await ready_to_confirm(chat, out)
    session.last_active -= tg.SESSION_MAX_IDLE_SECONDS + 60
    drafts = len(DRAFT_CALLS)
    await send_text(chat, out, "the plate is SAB1234X")
    assert "expired" in out.texts[-1] and len(DRAFT_CALLS) == drafts
    ok("a text edit to an expired draft is not drafted")

    session = await ready_to_confirm(chat, out)
    session.last_active -= tg.SESSION_MAX_IDLE_SECONDS + 60
    await send_photo(chat, out)
    assert any("expired" in t for t in out.texts[-3:])
    assert len(state.get_session(str(chat)).photo_paths) == 1
    ok("a photo after expiry starts the new draft")

    state.reset_session(str(chat))
    state.get_session(str(chat)).last_active -= tg.SESSION_MAX_IDLE_SECONDS + 60
    count = len(out.texts)
    await send_photo(chat, out)
    assert not any("expired" in t for t in out.texts[count:])
    ok("an empty session left idle isn't reported as an expired draft")

    session = await ready_to_confirm(chat, out)
    session.last_active -= tg.SESSION_MAX_IDLE_SECONDS - 3600
    await tg.handle_callback(tap(chat, out), None)
    assert any("saved to your cases" in t for t in out.texts[-2:])
    ok("a draft idle for under 24h still confirms")


async def check_live_draft_keeps_photos() -> None:
    out = Outbox()
    chat = BEN_TG
    state.reset_session(str(chat))
    session = await ready_to_confirm(chat, out, photos=2)
    day_old = time.time() - 23 * 3600
    for p in session.photo_paths:
        os.utime(p, (day_old, day_old))
    session.last_active -= 23 * 3600  # still inside the 24h idle limit
    await send_text(chat, out, "the plate is SAB1234X")
    cutoff = time.time() - 60
    assert all(Path(p).stat().st_mtime > cutoff for p in session.photo_paths)
    ok("activity on a live draft refreshes its photos, so the day-old cleanup skips them")


async def check_missing_temp_file() -> None:
    out = Outbox()
    chat = ANN_TG
    state.reset_session(str(chat))
    session = await ready_to_confirm(chat, out, photos=3)
    Path(session.photo_paths[1]).unlink()
    earlier = {r.id for r in cases_for(chat)}
    before = len(earlier)
    await tg.handle_callback(tap(chat, out), None)
    new = [r for r in cases_for(chat) if r.id not in earlier]
    assert len(new) == 1, new
    assert len(new[0].photo_paths) == 2, new[0].photo_paths
    ok("one temp photo gone at Confirm: saved with the other two, no crash")

    session = await ready_to_confirm(chat, out, photos=2)
    for p in session.photo_paths:
        Path(p).unlink()
    await tg.handle_callback(tap(chat, out), None)
    assert len(cases_for(chat)) == before + 1
    assert "send them again" in out.texts[-1], out.texts[-1]
    kept = state.get_session(str(chat))
    assert kept.draft is not None and kept.photo_paths == [] and not kept.finalizing
    await send_photo(chat, out)  # resent: redrafts with only the new photo
    assert len(DRAFT_CALLS[-1]) == 1
    await tg.handle_callback(tap(chat, out), None)
    assert len(cases_for(chat)) == before + 2
    ok("every temp photo gone: asked to resend, nothing saved; after resending it confirms")


def check_new_messages_only() -> None:
    print("handler filters")
    app = tg.build_app()
    handlers = app.handlers[0]
    user = TgUser(id=ANN_TG, first_name="Tester", is_bot=False)
    chat = Chat(id=ANN_TG, type="private")
    now = datetime.now(timezone.utc)

    def message(**kw) -> Message:
        msg = Message(message_id=1, date=now, chat=kw.pop("chat", chat), from_user=user, **kw)
        # CommandHandler reads the bot's username; a real one needs get_me.
        msg.set_bot(SimpleNamespace(username="carlink_test_bot"))
        return msg

    def command_message(text: str) -> Message:
        return message(text=text, entities=[MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))])

    samples = {
        "text": message(text="rear-ended at PIE"),
        "command": command_message("/start"),
        "/link": command_message("/link K7M4-QX9P"),
        "/whoami": command_message("/whoami"),
        "/unlink": command_message("/unlink"),
        "photo": message(photo=[PhotoSize(file_id="f", file_unique_id="u", width=10, height=10)]),
    }
    for name, msg in samples.items():
        assert any(h.check_update(Update(update_id=1, message=msg)) for h in handlers), f"new {name} not handled"
        edited = Update(update_id=2, edited_message=msg)
        assert not any(h.check_update(edited) for h in handlers), f"edited {name} reached a handler"
    channel = Chat(id=-100500, type="channel")
    post = Update(update_id=3, channel_post=message(chat=channel, text="news"))
    assert not any(h.check_update(post) for h in handlers), "channel post reached a handler"
    ok("edited messages and channel posts reach no handler; new ones (and the link commands) still do")


async def run_async_checks() -> None:
    log = LogCapture()
    logging.getLogger("app.channels.telegram").addHandler(log)
    logging.getLogger("app.channels.telegram").setLevel(logging.INFO)
    with mock.patch.object(tg, "build_draft", fake_build_draft), \
            mock.patch.object(tg, "render_pdf", fake_render_pdf), \
            mock.patch.object(tg, "estimate_wait", lambda _key=None: 0.0):
        await check_not_connected(log)
        await check_group_chat()
        await check_link_ends_mid_draft()
        await check_linking()
        await check_drafting_as_the_person()
        print("confirm")
        await check_one_confirm()
        await check_double_confirm()
        await check_failed_save_can_retry()
        await check_confirm_rechecks()
        await check_handlers_share_the_lock()
        print("photos")
        await check_photo_limit()
        print("stale drafts")
        await check_stale_draft()
        await check_live_draft_keeps_photos()
        await check_missing_temp_file()


def main() -> int:
    init_db()
    make_people()
    asyncio.run(run_async_checks())
    check_new_messages_only()
    print("\nALL TELEGRAM BOT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
