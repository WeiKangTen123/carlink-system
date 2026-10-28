"""Tests for the Telegram bot's safety fixes (app/channels/telegram.py): the
allow-list, one case per Confirm, temp photos, stale drafts, photo limits,
and handlers that only take new messages.

    python scripts/test_telegram_bot.py

Offline: fake Telegram updates and downloads, no AI call, no PDF render, a
temporary database and storage folder.
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
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ.setdefault("GEMINI_API_KEY", "fake")
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:fake-token-for-tests"
os.environ["TELEGRAM_ALLOWED_IDS"] = " 1001, 1002 ,"

from PIL import Image  # noqa: E402
from telegram import Chat, Message, MessageEntity, PhotoSize, Update, User  # noqa: E402

import app.channels.telegram as tg  # noqa: E402
from app.config import settings  # noqa: E402
from app.conversation import state  # noqa: E402
from app.conversation.flow import DraftResult  # noqa: E402
from app.conversation.state import Stage  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import Report  # noqa: E402
from app.reports.schema import SecurityIncidentDraft  # noqa: E402
from app.storage.files import tmp_dir  # noqa: E402

ALLOWED = 1001  # in TELEGRAM_ALLOWED_IDS above
PAST_REPORTER = 777  # filed a case before; allowed via the database
STRANGER = 5555
_photo_bytes = Path(_tmp, "sample.jpg")
Image.new("RGB", (64, 48), (200, 30, 30)).save(_photo_bytes, "JPEG")


def ok(label: str) -> None:
    print(f"    ok   {label}")


# --- fakes -----------------------------------------------------------------

class Outbox:
    """Everything the bot sent to one chat."""

    def __init__(self):
        self.texts: list[str] = []
        self.documents: list[tuple[str, bytes]] = []
        self.downloads: list[str] = []


class FakeMessage:
    def __init__(self, outbox: Outbox, text=None, caption=None, photo=False, document=None):
        self.outbox = outbox
        self.text = text
        self.caption = caption
        self.photo = [FakePhoto(outbox)] if photo else []
        self.document = document

    async def reply_text(self, text, **_kw):
        await asyncio.sleep(0)  # a real reply is a network round trip
        self.outbox.texts.append(text)

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

    async def answer(self, text=None, **_kw):
        await asyncio.sleep(0)
        self.answers.append(text)


def message_update(chat_id: int, outbox: Outbox, user_id=None, **message_kw):
    msg = FakeMessage(outbox, **message_kw)
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id or chat_id),
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=msg,
        message=msg,
        callback_query=None,
    )


def tap(chat_id: int, outbox: Outbox, data: str = "confirm", user_id=None):
    query = FakeQuery(data, FakeMessage(outbox))
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id or chat_id),
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=query.message,
        message=None,
        callback_query=query,
    )


async def send_photo(chat_id: int, outbox: Outbox, user_id=None):
    await tg.handle_photo(message_update(chat_id, outbox, user_id, photo=True), None)


async def send_text(chat_id: int, outbox: Outbox, text: str, user_id=None):
    await tg.handle_text(message_update(chat_id, outbox, user_id, text=text), None)


# --- patched AI and PDF ------------------------------------------------------

DRAFT_CALLS: list[list[str]] = []
RENDER_STARTED = threading.Event()
RENDER_SECONDS = 0.0


def fake_build_draft(description, photo_paths, session=None):
    DRAFT_CALLS.append(list(photo_paths))
    for p in photo_paths:  # the real call reads every photo
        assert Path(p).exists(), f"drafted with a missing photo {p}"
    return DraftResult(draft=SecurityIncidentDraft(description=description), summary_text="Draft summary")


def fake_render_pdf(data, photo_paths, output_path, report_id=None, slot_wait=None):
    RENDER_STARTED.set()
    time.sleep(RENDER_SECONDS)
    Path(output_path).write_bytes(b"%PDF-1.4 fake")


def cases_for(chat_id: int) -> list[Report]:
    db = SessionLocal()
    try:
        return db.query(Report).filter(Report.reporter_chat_id == str(chat_id)).all()
    finally:
        db.close()


def add_case(chat_id: str, channel: str) -> None:
    db = SessionLocal()
    try:
        db.add(Report(channel=channel, reporter_chat_id=chat_id, data={}, status="confirmed"))
        db.commit()
    finally:
        db.close()


async def ready_to_confirm(chat_id: int, outbox: Outbox, photos: int = 1):
    for _ in range(photos):
        await send_photo(chat_id, outbox)
    await send_text(chat_id, outbox, "Rear-ended at PIE exit 12")
    session = state.get_session(str(chat_id))
    assert session.stage == Stage.AWAITING_CONFIRMATION and session.draft is not None
    return session


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def messages(self, level=logging.INFO) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno == level]


# --- checks --------------------------------------------------------------------

async def check_allow_list_load(log: LogCapture) -> None:
    print("allow-list")
    with mock.patch.object(settings, "telegram_allowed_ids", ""):
        await tg._post_init(SimpleNamespace(bot=mock.AsyncMock()))
    assert not tg._allowed_ids
    assert any("allow-list is empty" in m for m in log.messages(logging.WARNING)), log.messages(logging.WARNING)
    ok("no ids at all: start-up warns, and the set is empty (everyone refused)")

    add_case(str(PAST_REPORTER), "telegram")
    add_case("dashboard", "manual")
    add_case("whatsapp:+6590000000", "whatsapp")
    await tg._post_init(SimpleNamespace(bot=mock.AsyncMock()))
    assert tg._allowed_ids == {"1001", "1002", str(PAST_REPORTER)}, tg._allowed_ids
    assert any("3 id(s)" in m for m in log.messages()), log.messages()
    ok("TELEGRAM_ALLOWED_IDS plus past Telegram reporters only; start-up logs the count")


async def check_allowed_users() -> None:
    out = Outbox()
    await send_photo(-4242, out, user_id=ALLOWED)  # a group chat, allowed by the user's id
    assert len(out.downloads) == 1 and str(-4242) in state._sessions, out.texts
    ok("a user id from TELEGRAM_ALLOWED_IDS is served, even in an unlisted group chat")

    out = Outbox()
    await send_photo(PAST_REPORTER, out)
    assert len(out.downloads) == 1 and str(PAST_REPORTER) in state._sessions
    ok("a chat that filed a Telegram case before is served")

    out = Outbox()
    await send_photo(1002, out, user_id=999)  # chat listed, user not
    assert len(out.downloads) == 1
    ok("a listed chat id is served whoever sends the message")


async def check_stranger_refused(log: LogCapture) -> None:
    out = Outbox()
    secret = "my plate is SECRET123"
    before = len(cases_for(STRANGER))
    with mock.patch.object(tg, "build_draft", side_effect=AssertionError("AI called for a stranger")):
        await send_photo(STRANGER, out)
        await send_text(STRANGER, out, secret)
        await tg.handle_callback(tap(STRANGER, out), None)
        await tg.start(message_update(STRANGER, out, text="/start"), None)
    assert out.texts == [tg.REFUSAL_TEXT], out.texts
    assert out.downloads == [] and str(STRANGER) not in state._sessions
    assert str(STRANGER) not in tg._chat_locks
    assert len(cases_for(STRANGER)) == before
    refused = [m for m in log.messages() if "Refused" in m]
    assert len(refused) == 1 and str(STRANGER) in refused[0], refused
    assert not any("SECRET123" in r.getMessage() for r in log.records)
    ok("a stranger gets one refusal, no download, no session, no AI, no case; ids logged, not the text")

    tg._refused_at[str(STRANGER)] -= tg.REFUSAL_EVERY_SECONDS + 1
    await send_text(STRANGER, out, "hello?")
    assert out.texts == [tg.REFUSAL_TEXT] * 2
    ok("refused again only once the hour has passed")


async def check_one_confirm() -> None:
    out = Outbox()
    chat = 1001
    session = await ready_to_confirm(chat, out, photos=2)
    temp_files = list(session.photo_paths)
    assert all(re.fullmatch(r"tg-[0-9a-f]{32}\.jpg", Path(p).name) for p in temp_files), temp_files
    assert all(Path(p).parent == tmp_dir() for p in temp_files)
    ok("temp photos are named storage/tmp/tg-<uuid4 hex>.jpg")

    query_update = tap(chat, out)
    await tg.handle_callback(query_update, None)
    cases = cases_for(chat)
    assert len(cases) == 1, cases
    assert len(out.documents) == 1 and out.documents[0][1].startswith(b"%PDF")
    assert out.documents[0][0] == f"CIR_{cases[0].id[:8].upper()}.pdf"
    assert any("finalized and saved" in t for t in out.texts)
    assert len(cases[0].photo_paths) == 2 and all(Path(p).exists() for p in cases[0].photo_paths)
    ok("one Confirm saves exactly one case and sends its PDF")

    assert not any(Path(p).exists() for p in temp_files), [p for p in temp_files if Path(p).exists()]
    fresh = state.get_session(str(chat))
    assert fresh.draft is None and fresh.photo_paths == [] and not fresh.finalizing
    ok("the temp files are deleted once the case is saved, and the session is reset")


async def check_double_confirm() -> None:
    global RENDER_SECONDS
    out = Outbox()
    chat = 1002
    state.reset_session(str(chat))
    await ready_to_confirm(chat, out)
    first, second = tap(chat, out), tap(chat, out)
    await asyncio.gather(tg.handle_callback(first, None), tg.handle_callback(second, None))
    assert len(cases_for(chat)) == 1, cases_for(chat)
    assert len(out.documents) == 1
    assert second.callback_query.answers == ["Already saving this report -- one moment."], second.callback_query.answers
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
    chat = PAST_REPORTER
    state.reset_session(str(chat))
    await ready_to_confirm(chat, out)
    before = len(cases_for(chat))
    with mock.patch.object(tg, "SessionLocal", side_effect=RuntimeError("disk full")):
        try:
            await tg.handle_callback(tap(chat, out), None)
            raise AssertionError("save error was swallowed")
        except RuntimeError:
            pass
    session = state.get_session(str(chat))
    assert not session.finalizing and session.draft is not None
    assert all(Path(p).exists() for p in session.photo_paths)
    await tg.handle_callback(tap(chat, out), None)
    assert len(cases_for(chat)) == before + 1
    ok("a save that fails clears the flag and keeps the draft and photos, so a retry works")


async def check_handlers_share_the_lock() -> None:
    out = Outbox()
    chat = 1001
    state.reset_session(str(chat))
    await send_photo(chat, out)
    temp = state.get_session(str(chat)).photo_paths[0]
    lock = tg._lock_for(str(chat))
    await lock.acquire()
    try:
        cancel = asyncio.create_task(tg.cancel_command(message_update(chat, out, text="/cancel"), None))
        # An unknown insurer button: takes the lock, then does nothing.
        button = asyncio.create_task(tg.handle_callback(tap(chat, out, data="ins_unknown"), None))
        await asyncio.sleep(0.05)
        assert not cancel.done() and not button.done()
        assert Path(temp).exists() and state.get_session(str(chat)).photo_paths == [temp]
    finally:
        lock.release()
    await asyncio.gather(cancel, button)
    assert not Path(temp).exists()
    ok("commands and button taps wait for the chat lock; /cancel deletes the draft's temp files")

    await send_photo(chat, out)
    temp = state.get_session(str(chat)).photo_paths[0]
    await tg.start(message_update(chat, out, text="/new"), None)
    assert not Path(temp).exists() and state.get_session(str(chat)).photo_paths == []
    ok("/new deletes the old draft's temp files too")


async def check_photo_limit() -> None:
    out = Outbox()
    chat = 1001
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
    chat = 1002
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
    assert any("finalized and saved" in t for t in out.texts[-2:])
    ok("a draft idle for under 24h still confirms")


async def check_live_draft_keeps_photos() -> None:
    out = Outbox()
    chat = 1002
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
    chat = 1001
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
    user = User(id=ALLOWED, first_name="Tester", is_bot=False)
    chat = Chat(id=ALLOWED, type="private")
    now = datetime.now(timezone.utc)

    def message(**kw) -> Message:
        msg = Message(message_id=1, date=now, chat=kw.pop("chat", chat), from_user=user, **kw)
        # CommandHandler reads the bot's username; a real one needs get_me.
        msg.set_bot(SimpleNamespace(username="carlink_test_bot"))
        return msg

    samples = {
        "text": message(text="rear-ended at PIE"),
        "command": message(text="/start", entities=[MessageEntity(type="bot_command", offset=0, length=6)]),
        "photo": message(photo=[PhotoSize(file_id="f", file_unique_id="u", width=10, height=10)]),
    }
    for name, msg in samples.items():
        assert any(h.check_update(Update(update_id=1, message=msg)) for h in handlers), f"new {name} not handled"
        edited = Update(update_id=2, edited_message=msg)
        assert not any(h.check_update(edited) for h in handlers), f"edited {name} reached a handler"
    channel = Chat(id=-100500, type="channel")
    post = Update(update_id=3, channel_post=message(chat=channel, text="news"))
    assert not any(h.check_update(post) for h in handlers), "channel post reached a handler"
    ok("edited messages and channel posts reach no handler; new ones still do")


async def run_async_checks() -> None:
    log = LogCapture()
    logging.getLogger("app.channels.telegram").addHandler(log)
    logging.getLogger("app.channels.telegram").setLevel(logging.INFO)
    with mock.patch.object(tg, "build_draft", fake_build_draft), \
            mock.patch.object(tg, "render_pdf", fake_render_pdf), \
            mock.patch.object(tg, "available_api_keys", lambda: []), \
            mock.patch.object(tg, "estimate_wait", lambda _key=None: 0.0):
        await check_allow_list_load(log)
        await check_allowed_users()
        await check_stranger_refused(log)
        print("confirm")
        await check_one_confirm()
        await check_double_confirm()
        await check_failed_save_can_retry()
        await check_handlers_share_the_lock()
        print("photos")
        await check_photo_limit()
        print("stale drafts")
        await check_stale_draft()
        await check_live_draft_keeps_photos()
        await check_missing_temp_file()


def main() -> int:
    init_db()
    asyncio.run(run_async_checks())
    check_new_messages_only()
    print("\nALL TELEGRAM BOT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
