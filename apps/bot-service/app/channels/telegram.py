"""Telegram adapter. Runs in polling mode -- no public URL/webhook needed,
which is what makes it the fastest channel to stand up (see docs/proposal.md
section B). Get a token from @BotFather, put it in .env, and this runs.

Staff file cases here from a Telegram connected to an active Carlink
account (app/auth/chat_links.py: Settings > My Telegram, confirmed on both
sides). Anyone else can send a public report (P6: channels/
telegram_public.py), which lands in the dashboard's inbox; no AI runs and
no PDF is made for them here. Only in a private chat. Every update goes
through the same checks, in this order:

  1. Private chats only. A group gets "I only work in a private chat" at
     most once an hour, and nothing from it is kept (_private_only).
  2. The gate, before the lock (review H1: PTB holds one of its
     concurrent_updates slots while a handler waits for the chat lock, so
     a stranger's flood could stall staff): a token bucket per Telegram
     user (burst BURST, BURST_LINKED for staff, refilled REFILL_PER_SECOND),
     at most CHAT_IN_FLIGHT updates in flight per chat, then linked_user()
     read fresh (_who: once per update), and for someone not connected at
     most PUBLIC_IN_FLIGHT public updates in flight from everyone. Over a
     limit the update is dropped; "please slow down" at most every 10
     minutes, within the public reply budget.
  3. The chat's lock, held by every handler (_lock_for).
  4. The linking commands, which need no link: /start link-CODE (the deep
     link from Settings), /link CODE, a bare code (strict, and only with no
     draft or public form open: _handle_text), the [Yes, that's me]/
     [Cancel] taps, /unlink, /whoami and /help.
  4b. Nobody linked -> telegram_public, which never reaches steps 5-8.
  5. A draft started for another account is thrown away, and so is a
     public form from before this Telegram was connected, with its photos
     (_current_session).
  6. A draft's first photo: the photo cap, the disk guard, then AI on for
     this person (ensure_ai_ready); every photo: the dashboard's hourly
     photo budget for unvetted accounts; and only then the download.
  7. Drafting on this person's AI (Linked.actor), one request at a time
     for unvetted accounts, as on the dashboard; the summary starts
     "Filing as Name".
  8. Confirm, under the lock with the finalizing flag: the case row takes
     SQLite's write lock, then the link and the daily case cap are checked
     again in that transaction, and the case is saved as theirs. The chat
     gets its link -- not the PDF, which stays behind sign-in (agent6 D8).

"Unvetted": no admin role and none of the three ticks, a fresh self-signup
(api/limits._unvetted). Their prompts and drafts say so, and their
/start, /help, /whoami and /link get an answer at most every 5 seconds.
"""
import asyncio
import functools
import html
import logging
import math
import secrets
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.exc import OperationalError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.helpers import escape_markdown

from app.config import settings
from app.ai.keys import AI_KEY_MISSING, AiActor, AiUnavailable, ensure_ai_ready, resolve_keys
from app.ai.rate_limit import estimate_wait
from app.api import limits
from app.channels import telegram_public as tg_public
# Shared with the public flow; re-exported here, where they used to live.
from app.channels.tg_common import (  # noqa: F401
    MAX_ENTRIES,
    NO_PREVIEW,
    REPLY_EVERY_SECONDS,
    SESSION_MAX_IDLE_SECONDS,
    _chat_locks,
    _discard_temp_files,
    _drop,
    _first_in,
    _forwarded,
    _keep_temp_files,
    _lock_for,
    _new_temp_path,
    _reply_target,
    _stop_public,
    _telegram_name,
    _told_at,
)
from app.auth.chat_links import (
    CODE_TTL,
    DEEP_LINK_PREFIX,
    clean_name,
    code_from_start_payload,
    contest_code,
    linked_user,
    looks_like_code,
    masked_email,
    normalize_code,
    peek_code,
    telegram_said_yes,
    unlink,
    withdraw_yes,
    wrong_codes,
)
from app.auth.core import audit
from app.auth.deps import ApiError
from app.auth.permissions import is_staff
from app.conversation.flow import build_draft, build_template_prompt, combined_description, parse_template_reply
from app.conversation.state import Stage, find_session, get_session, reset_session
from app.reports.db import SessionLocal, init_db
from app.reports.models import Report, gen_id
from app.reports.schema import SignOffInfo
from app.rendering.renderer import BOT_RENDER_SLOT_WAIT_SECONDS, render_pdf
from app.storage.files import disk_space_low, remove_incident_dirs, report_pdf_path, save_photo

logger = logging.getLogger(__name__)

WELCOME_GUIDE = (
    "🚨 *Carlink AI Incident Reporting System* 🚨\n\n"
    "I will assist you in generating an official, structured **Security Incident Report PDF** from your photos & description.\n\n"
    "📋 *3 Simple Steps to File a Report:*\n\n"
    "1️⃣ *Send Photos / Documents* 📸\n"
    "   • Upload 1 or more photos of the scene, damage, log card, or repair estimate.\n\n"
    "2️⃣ *Provide Incident Particulars* 📝\n"
    "   • Reply with details or copy the short template.\n"
    "   • Or type naturally (e.g. 'SLK3063Z rear ended at PIE, Insurer Tokio Marine, Workshop Precise Auto').\n\n"
    "3️⃣ *Interactive Review & Buttons* 📄\n"
    "   • Tap the interactive buttons below the draft to select side, insurer, or confirm.\n\n"
    "----------------------------------------\n"
    "💡 *Available Commands:*\n"
    "• /new or /start — Start a new report session\n"
    "• /help — Display this step-by-step guide\n"
    "• /cancel — Reset current session\n"
    "• /whoami — The Carlink account this Telegram files as\n"
    "• /unlink — Disconnect this Telegram from Carlink\n\n"
    "👇 *Please send your first photo (or type /new) to begin!*"
)

CONFIRM_WORDS = {"confirm", "yes", "y", "ok", "okay", "looks good"}

# "AI is off": an album's photos arrive as separate updates.
AI_OFF_EVERY_SECONDS = 60
# /start, /help, /whoami and /link from an unvetted account.
COMMAND_EVERY_SECONDS = 5
MAX_PHOTOS_PER_DRAFT = 40
# Documents the drafting flow can use: they go to Gemini as images and into
# the PDF's photo pages, so only image types both handle (the dashboard's
# ALLOWED_PHOTO_EXTENSIONS). A PDF used to be passed along as an "image".
DOCUMENT_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
}
# The case history's "created" entry for a case filed here (auth/core.
# report_history shows the same words, unnamed, for cases from before P5).
FILED_VIA_TELEGRAM = "Filed through the Telegram bot"

# Linking (app/auth/chat_links.py). Names and emails go out as escaped
# HTML or plain text: a "_" in a username breaks Telegram's Markdown.
LINK_YES = "link_yes:"
LINK_NO = "link_no:"
NOT_A_CODE = (
    "That doesn't look like a link code. It's 8 letters and numbers, like K7M4-QX9P: "
    "make one in Carlink → Settings → My Telegram."
)
CODE_GONE = (
    "That code can't be used any more: it expired, was used, or a newer one was made. "
    "Make a new one in Carlink → Settings → My Telegram."
)
FORWARDED_CODE = (
    "I don't take codes from forwarded messages. If it's your own code, type it in yourself, "
    "or tap Open Telegram in Carlink → Settings → My Telegram."
)
CODE_CONTESTED = (
    "This code was already answered by another Telegram, so it has been cancelled to keep your account safe. "
    "Make a new code in Carlink → Settings → My Telegram."
)
PROMPT_EXPIRED = "That question has expired. Please send the code again."
LINK_CANCELLED = "Cancelled: nothing was connected."
YES_WITHDRAWN = "Your earlier Yes is withdrawn."
NO_LONGER_CONNECTED = "This Telegram isn't connected to that Carlink account any more, so the report wasn't saved."
# On the link prompt and the draft, for an unvetted account (review M2): a
# look-alike signup is how someone would be talked into connecting to it.
UNVETTED_WARNING = "⚠ This is a self-registered account that Carlink hasn't checked. Only continue if it's yours."
STILL_DRAFTING = "Your earlier report is still being drafted. Wait for it to finish, then try again."


def _base_url() -> str:
    return settings.public_base_url.rstrip("/")


def _settings_url(card: str = "my-telegram") -> str:
    return f"{_base_url()}/settings#{card}"


def _ai_off_text(exc: AiUnavailable) -> str:
    if exc.code == AI_KEY_MISSING:
        text = (
            "AI is off for your account – add your key in Carlink → Settings → My AI key, "
            "or ask an admin to allow the company key."
        )
    else:
        text = exc.message
    return f"{text}\n{_settings_url('my-ai-key')}"


@dataclass(frozen=True)
class Linked:
    """The Carlink account a Telegram user files as, read fresh for every
    update (_who). Plain values, so no database session is held across an
    await or an AI call."""
    id: str
    label: str  # "Name (@username)", cleaned (_account_names)
    staff: bool  # an admin or "May sign off": their PDFs carry no unverified banner
    unvetted: bool  # no admin role and no ticks: the dashboard's limits apply
    # "@username (Name)" for "Filing as": the unique @username first, as in
    # the link prompt (review M2). Empty falls back to label.
    filing_label: str = ""

    @property
    def actor(self) -> AiActor:
        return AiActor.for_user(self.id, "telegram")


def _account_names(user) -> tuple[str | None, str | None]:
    """(name, @username without the "@") of a Carlink account, cleaned the
    way Telegram names are (chat_links.clean_name): signup lets anyone type
    a name, look-alike and invisible characters included (review L6)."""
    return clean_name(user.name), clean_name(getattr(user, "username", None), 64)


def _filing_label(user) -> str:
    """"Name (@username)", as /whoami and "Filing as" say it."""
    name, username = _account_names(user)
    if name and username:
        return f"{name} (@{username})"
    return name or (f"@{username}" if username else "a Carlink account")


def _prompt_label(user) -> str:
    """"@username (Name)" for the link prompt: the @username, which is
    unique, first (review M2); the name anyone can copy after it."""
    name, username = _account_names(user)
    if name and username:
        return f"@{username} ({name})"
    return f"@{username}" if username else (name or "a Carlink account")


@dataclass
class _LinkPrompt:
    """An open "Connect this Telegram to ...?" prompt, in memory only: the
    code never goes into a button. nonce ties a tap to the prompt it was
    on, so [Yes] on an older prompt can't say Yes to a newer code."""
    code: str
    nonce: str
    at: float


# Telegram user id -> their open prompt (one each; a new one replaces it).
_prompts: dict[str, _LinkPrompt] = {}
PROMPT_SECONDS = CODE_TTL.total_seconds()


def _args(context) -> list[str]:
    return list(getattr(context, "args", None) or [])


# ------------------------------------------------- database (in threads)


def _commit_quietly(db) -> None:
    """linked_user's only write is a last_used_at stamp: on a busy database
    it is skipped, never the answer."""
    try:
        db.commit()
    except OperationalError:
        db.rollback()
        logger.debug("Busy database: a Telegram link's last-used time wasn't saved this time")


def _lookup(external_id, username, name) -> Linked | None:
    db = SessionLocal()
    try:
        user = linked_user(db, external_id, username=username, name=name)
        found = None
        if user is not None:
            found = Linked(id=user.id, label=_filing_label(user), staff=is_staff(user), unvetted=limits._unvetted(user),
                           filing_label=_prompt_label(user))
        _commit_quietly(db)
        return found
    finally:
        db.close()


def _peek(code: str) -> tuple[str, str, bool] | None:
    """(the account's "@username (Name)", its masked email, whether it is
    unvetted) for an open code."""
    db = SessionLocal()
    try:
        user = peek_code(db, code)
        if user is None:
            return None
        return _prompt_label(user), masked_email(user.email), limits._unvetted(user)
    finally:
        db.close()


def _contest(code: str, external_id: str) -> bool:
    db = SessionLocal()
    try:
        contested = contest_code(db, code, external_id)
        db.commit()
        return contested
    finally:
        db.close()


def _said_yes(code: str, external_id: str, username, name) -> bool:
    db = SessionLocal()
    try:
        user = telegram_said_yes(db, code, external_id, username, name)
        db.commit()
        return user is not None
    finally:
        db.close()


def _withdraw(external_id: str) -> int:
    db = SessionLocal()
    try:
        withdrawn = withdraw_yes(db, external_id)
        db.commit()
        return withdrawn
    finally:
        db.close()


def _unlink_telegram(external_id: str) -> tuple[bool, str | None, int]:
    """/unlink: (whether a link was removed, the account it was to, how many
    Yes answers to open codes were withdrawn)."""
    db = SessionLocal()
    try:
        user = linked_user(db, external_id)
        label = _filing_label(user) if user is not None else None
        gone = unlink(db, external_id=external_id, actor=user, reason="with /unlink in Telegram")
        withdrawn = withdraw_yes(db, external_id)
        db.commit()
        return gone, label, withdrawn
    finally:
        db.close()


def _may_file(db, external_id: str, user_id: str):
    """Step 8: (the account, None) when this Telegram may file a case as
    user_id now. Else (the account or None, why not): None when it isn't
    connected to that account any more; the account when it is, but has
    filed its day's worth of cases (api/limits.py, as on the dashboard)."""
    user = linked_user(db, external_id)
    if user is None or user.id != user_id:
        return None, NO_LONGER_CONNECTED
    try:
        limits.check_new_cases_today(db, user)
    except ApiError as exc:
        return user, exc.detail
    return user, None


def _check_filing(external_id: str, user_id: str) -> tuple[bool, str | None]:
    db = SessionLocal()
    try:
        user, problem = _may_file(db, external_id, user_id)
        _commit_quietly(db)
        return user is not None, problem
    finally:
        db.close()


def _save_case(report_id: str, external_id: str, user_id: str, data: dict, photos: list[str], pdf_path) -> tuple[bool, str | None]:
    """Saves the case as this person's, with step 8's check made once more
    inside the same transaction: a long render could outlast the link.

    The row goes in first, which takes SQLite's write lock (as api/auth.
    recipient_still_active relies on): nothing -- a disable, an unlink,
    another case -- can commit between the check and this commit (review
    L3). It goes in as a draft ("intake"), so the daily cap counts only the
    cases before it, and is filed once the check passes."""
    db = SessionLocal()
    try:
        report = Report(
            id=report_id,
            channel="telegram",
            # The Telegram USER id, which is what links name (in a private
            # chat it is also the chat id).
            reporter_chat_id=str(external_id),
            data=data,
            status="intake",
            photo_paths=photos,
            pdf_path=pdf_path,
            owner_id=user_id,
        )
        db.add(report)
        db.flush()
        owner, problem = _may_file(db, external_id, user_id)
        if problem:
            db.rollback()
            return owner is not None, problem
        report.status = "confirmed"
        audit(db, owner, "created", report_id, detail=FILED_VIA_TELEGRAM)
        db.commit()
        return True, None
    finally:
        db.close()


# The update the gate looked its sender up for, and the answer: handlers
# use it rather than ask the database twice (_who).
_gate_seen: ContextVar = ContextVar("telegram_gate_seen", default=None)


async def _lookup_who(update: Update) -> Linked | None:
    tg_user = update.effective_user
    if tg_user is None:
        return None
    return await asyncio.to_thread(_lookup, tg_user.id, getattr(tg_user, "username", None), _telegram_name(tg_user))


async def _who(update: Update) -> Linked | None:
    """Step 2: who this Telegram user files as, from the database for every
    update -- the only check of who someone is. Read by the gate; a handler
    asking again for the same update gets that answer."""
    seen = _gate_seen.get()
    if seen is not None and seen[0] is update:
        return seen[1]
    return await _lookup_who(update)


def _seen(update: Update) -> tuple[bool, Linked | None]:
    """(whether the gate looked this update's sender up, who it found),
    without waiting: for decisions made before a handler's first await."""
    seen = _gate_seen.get()
    if seen is not None and seen[0] is update:
        return True, seen[1]
    return False, None


# ------------------------------------------------------------ the checks

# Step 2, the gate (review H1). Per Telegram user: a token bucket.
# Every photo in an album is its own update, and these limits are checked
# before _who knows whether the sender is staff -- so both sit above a
# staff draft's MAX_PHOTOS_PER_DRAFT (40), or a staff member's first big
# send after a restart would lose photos. Strangers are held tighter once
# known (PUBLIC_CHAT_IN_FLIGHT, PUBLIC_IN_FLIGHT, the public budgets).
BURST = 45
BURST_LINKED = 60  # a staff member's few albums at once
REFILL_PER_SECOND = 1.0
# In flight (handled now, or waiting for the chat lock): per chat, and
# public updates from everyone, out of the 64 PTB runs at once
# (build_app), so staff always find a free one.
CHAT_IN_FLIGHT = 48
PUBLIC_CHAT_IN_FLIGHT = 24  # a public form takes at most 20 photos
PUBLIC_IN_FLIGHT = 24
CONCURRENT_UPDATES = 64
DEFAULT_THREADS = 32
SLOW_DOWN_EVERY_SECONDS = 10 * 60


class _Buckets:
    """Token buckets per Telegram user, in memory. One not seen as linked
    yet gets BURST; once _who finds them linked, BURST_LINKED (topped up
    by the difference, so staff have it from their first update on)."""

    def __init__(self):
        self._b: dict[str, tuple[float, float, int]] = {}  # id -> (tokens, at, burst)

    def _now(self, key: str, now: float) -> tuple[float, int]:
        tokens, at, burst = self._b.get(key, (BURST, now, BURST))
        return min(burst, tokens + (now - at) * REFILL_PER_SECOND), burst

    def take(self, key: str) -> bool:
        now = time.monotonic()
        tokens, burst = self._now(key, now)
        taken = tokens >= 1
        if key not in self._b and len(self._b) >= MAX_ENTRIES:
            # Full buckets are the same as none.
            for old in [k for k in self._b if self._now(k, now)[0] >= self._b[k][2]]:
                del self._b[old]
        self._b[key] = (tokens - 1 if taken else tokens, now, burst)
        return taken

    def linked(self, key: str, linked: bool) -> None:
        if key not in self._b:
            return
        tokens, at, burst = self._b[key]
        new = BURST_LINKED if linked else BURST
        if new != burst:
            self._b[key] = (min(new, tokens + max(new - burst, 0)), at, new)

    def reset(self) -> None:
        self._b.clear()


_buckets = _Buckets()
_in_flight: dict[str, int] = {}  # chat id -> updates in flight
_public_in_flight = [0]


async def _slow_down(update: Update, chat_id: str) -> None:
    """An update the gate dropped: "please slow down", at most every 10
    minutes a chat, and only if the public reply budget has room."""
    said = _told_at.get(f"slow:{chat_id}")
    if said is not None and time.monotonic() - said < SLOW_DOWN_EVERY_SECONDS:
        return
    if not tg_public.replies.try_take():
        return
    _first_in(f"slow:{chat_id}", SLOW_DOWN_EVERY_SECONDS)
    logger.info("Telegram chat %s sent updates too fast: some were dropped", chat_id)
    target = _reply_target(update)
    if target:
        await target.reply_text(tg_public.SLOW_DOWN)


async def _gated(handler, update: Update, context, claim=None) -> None:
    chat_id = str(update.effective_chat.id)
    ext = str(update.effective_user.id)
    if _in_flight.get(chat_id, 0) >= CHAT_IN_FLIGHT or not _buckets.take(ext):
        await _slow_down(update, chat_id)
        return
    # Before the first await, so a second tap can't slip in (_claim_tap).
    busy, release = claim(update) if claim is not None else (None, None)
    if busy is not None:
        await _answer_quietly(update.callback_query, busy)
        return
    _in_flight[chat_id] = _in_flight.get(chat_id, 0) + 1
    public = False
    seen = None
    try:
        user = await _lookup_who(update)
        _buckets.linked(ext, user is not None)
        if user is None:
            if _public_in_flight[0] >= PUBLIC_IN_FLIGHT or _in_flight.get(chat_id, 0) > PUBLIC_CHAT_IN_FLIGHT:
                await _slow_down(update, chat_id)
                return
            _public_in_flight[0] += 1
            public = True
        seen = _gate_seen.set((update, user))
        await handler(update, context)
    finally:
        if release is not None:
            release()
        if seen is not None:
            _gate_seen.reset(seen)
        if public:
            _public_in_flight[0] -= 1
        left = _in_flight.get(chat_id, 1) - 1
        if left > 0:
            _in_flight[chat_id] = left
        else:
            _in_flight.pop(chat_id, None)


def _private_only(handler=None, *, claim=None):
    """Steps 1 and 2: runs handler only in a private chat, through the gate.
    A group or channel gets "I only work in a private chat" at most once an
    hour, and nothing more: no lock, no session, no download, no code
    looked at. claim(update) -> (busy answer or None, release or None)
    runs before the gate's first await (_claim_tap)."""
    if handler is None:
        return functools.partial(_private_only, claim=claim)

    @functools.wraps(handler)
    async def guarded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if chat is not None and chat.type == ChatType.PRIVATE and update.effective_user is not None:
            await _gated(handler, update, context, claim)
            return
        if update.callback_query is not None:
            try:
                await update.callback_query.answer()
            except Exception:
                pass
        if chat is None or not _first_in(f"group:{chat.id}", REPLY_EVERY_SECONDS):
            return
        logger.info("Ignored %s chat %s: the bot works in private chats only", chat.type, chat.id)
        if update.effective_message:
            bot_name = getattr(getattr(context, "bot", None), "username", None)
            await update.effective_message.reply_text(
                "I only work in a private chat." + (f" Message me directly: @{bot_name}" if bot_name else "")
            )

    return guarded


def _reset(chat_id: str) -> None:
    session = get_session(chat_id)
    _stop_public(session)
    _discard_temp_files(session)
    reset_session(chat_id)


async def _current_session(update: Update, chat_id: str, user: Linked):
    """The chat's session for this account, and whether the update was meant
    for a draft that was just thrown away: one started for another Carlink
    account (the Telegram was connected to someone else meanwhile, step 5),
    or one idle past SESSION_MAX_IDLE_SECONDS, since confirming it could
    find its photos already deleted.

    A public report form from before this Telegram was connected is thrown
    away first, with its photos: they never join a staff draft (P6)."""
    session = get_session(chat_id)
    now = time.monotonic()
    if session.public is not None:
        started = session.public.name is not None or bool(session.photo_paths)
        _reset(chat_id)
        session = get_session(chat_id)
        logger.info("Telegram user %s is connected now: their public report form was discarded", update.effective_user.id)
        if started and update.effective_message:
            await update.effective_message.reply_text(
                "This Telegram is connected to Carlink now, so the public report form you'd started here was "
                "discarded. You can file it as staff instead."
            )
    has_draft = bool(session.photo_paths) or session.draft is not None
    stale = False
    if session.carlink_user_id not in (None, user.id):
        _discard_temp_files(session)
        session = reset_session(chat_id)
        stale = has_draft
        if has_draft and update.effective_message:
            await update.effective_message.reply_text(
                f"This Telegram now files reports as {user.label}, so the draft you had open for another "
                "account was discarded. Please start again: send /new or your photos."
            )
    elif has_draft and now - session.last_active > SESSION_MAX_IDLE_SECONDS:
        _discard_temp_files(session)
        session = reset_session(chat_id)
        stale = True
        if update.effective_message:
            await update.effective_message.reply_text(
                "⌛ Your previous draft expired after 24 hours without activity and was discarded. "
                "Please start again: send /new or your photos."
            )
    else:
        _keep_temp_files(session)
    session.carlink_user_id = user.id
    session.last_active = now
    return session, stale


async def _photo_limit_reached(update: Update, session, limit: int = MAX_PHOTOS_PER_DRAFT) -> bool:
    if len(session.photo_paths) < limit:
        return False
    await update.message.reply_text(
        f"{limit} photos is the limit for one report, so this one wasn't added. "
        "Please confirm the report as it is, or send /new to start again."
    )
    return True


async def _disk_full(update: Update) -> bool:
    """Before a download: the dashboard refuses uploads too once the server's
    disk is nearly full (storage/files.disk_space_low)."""
    if not disk_space_low():
        return False
    await update.message.reply_text(
        "Sorry, the server is low on storage, so I can't take photos right now. Please tell your Carlink admin."
    )
    return True


async def _ai_ready(update: Update, chat_id: str, user: Linked) -> bool:
    """Step 6, before a draft's first download: whether AI can run for this
    person (their own key, or the company's if they may use it). If not,
    they're told how to turn it on, and nothing is downloaded."""
    try:
        await asyncio.to_thread(ensure_ai_ready, user.actor)
        return True
    except AiUnavailable as exc:
        if _first_in(f"ai-off:{chat_id}", AI_OFF_EVERY_SECONDS):
            await update.message.reply_text(_ai_off_text(exc), link_preview_options=NO_PREVIEW)
        return False


async def _photo_budget_spent(update: Update, user: Linked) -> bool:
    """Before each download from an unvetted account: the dashboard's
    hourly photo budget (api/limits.charge_photo_uploads; this process's
    own count). Counted here, given back if the download fails."""
    if not user.unvetted or limits.photo_uploads.take(user.id):
        return False
    await update.message.reply_text(limits.PHOTO_LIMIT)
    return True


async def _download(source, dest: Path, user: Linked) -> None:
    """source: the message's PhotoSize or Document."""
    try:
        file = await source.get_file()
        await file.download_to_drive(str(dest))
    except BaseException:
        if user.unvetted:
            limits.photo_uploads.refund(user.id)
        raise


def _too_soon(chat_id: str, user: Linked | None) -> bool:
    """/start, /help, /whoami and /link from an unvetted account: answered
    at most every COMMAND_EVERY_SECONDS per chat; anything sooner is
    dropped."""
    return user is not None and user.unvetted and not _first_in(f"command:{chat_id}", COMMAND_EVERY_SECONDS)


# --------------------------------------------------------------- linking


async def _offer_link(update: Update, code: str | None, user: Linked | None) -> None:
    """A code sent from Telegram (/start link-CODE, /link CODE, a bare
    code): the "Is this you?" prompt for its account, which [Yes, that's
    me] answers (_link_tap). user is who this Telegram files as already."""
    message = update.effective_message
    ext = str(update.effective_user.id)
    if _forwarded(message):
        # Someone else's code passed along is how a link gets phished (H2).
        await message.reply_text(FORWARDED_CODE, link_preview_options=NO_PREVIEW)
        return
    if user is not None:
        await message.reply_text(
            f"This Telegram is already connected to {user.label}. To connect a different account, send /unlink first."
        )
        return
    blocked = wrong_codes.blocked_for(ext)
    if blocked:
        await message.reply_text(f"Too many wrong codes. Try again in {math.ceil(blocked / 60)} minutes.")
        return
    if code is None:
        await message.reply_text(NOT_A_CODE)
        return
    owner = await asyncio.to_thread(_peek, code)
    if owner is None:
        if await asyncio.to_thread(_contest, code, ext):
            # Another Telegram said Yes to it first (review M1). Not a
            # wrong code: this one had it too.
            logger.info("Telegram user %s sent a code another Telegram had said Yes to: the code is cancelled", ext)
            await message.reply_text(CODE_CONTESTED, link_preview_options=NO_PREVIEW)
            return
        left = wrong_codes.wrong(ext)
        if left == 0:
            logger.info("Telegram user %s is blocked for a while after too many wrong link codes", ext)
            await message.reply_text(f"Too many wrong codes. Try again in {wrong_codes.BLOCK // 60} minutes.")
        else:
            await message.reply_text(
                "That code didn't work. Codes work once and only for 10 minutes, and a new one replaces the last: "
                f"make a new one in Carlink → Settings → My Telegram. ({left} {'try' if left == 1 else 'tries'} left)"
            )
        return
    # The wrong-code count starts again only once a Yes goes through
    # (_link_tap), not here: a stranger mixing their own good code in with
    # guesses would never be blocked otherwise (review L1).
    label, masked, unvetted = owner
    now = time.monotonic()
    for old in [k for k, p in _prompts.items() if now - p.at > PROMPT_SECONDS]:
        del _prompts[old]
    _prompts.pop(ext, None)
    while len(_prompts) >= MAX_ENTRIES:
        del _prompts[next(iter(_prompts))]
    prompt = _prompts[ext] = _LinkPrompt(code=code, nonce=secrets.token_hex(4), at=now)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, that's me", callback_data=LINK_YES + prompt.nonce),
        InlineKeyboardButton("Cancel", callback_data=LINK_NO + prompt.nonce),
    ]])
    await message.reply_text(
        f"Connect this Telegram to <b>{html.escape(label)}</b>, {html.escape(masked)}?\n\n"
        + (f"{UNVETTED_WARNING}\n\n" if unvetted else "")
        + "Only tap Yes if this is your own Carlink account. If someone sent you this code, tap Cancel.",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def _remove_buttons(query) -> None:
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass  # too old to edit, or already without them: only tidying


async def _link_tap(update: Update, data: str) -> None:
    """[Yes, that's me] / [Cancel] on a link prompt. Yes marks the code
    "waiting for Carlink" (telegram_said_yes, atomic); the link is made
    only once the person confirms in Settings too (agent6 H2). Cancel, on
    any prompt however old, also takes back a Yes this Telegram gave to a
    code that is still open (review L2)."""
    query = update.callback_query
    tg_user = update.effective_user
    ext = str(tg_user.id)
    target = _reply_target(update)
    nonce = data.split(":", 1)[1]
    prompt = _prompts.get(ext)
    current = prompt is not None and prompt.nonce == nonce and time.monotonic() - prompt.at <= PROMPT_SECONDS
    await _remove_buttons(query)
    if data.startswith(LINK_NO):
        if current:
            del _prompts[ext]
        withdrawn = await asyncio.to_thread(_withdraw, ext)
        await target.reply_text(LINK_CANCELLED + (f" {YES_WITHDRAWN}" if withdrawn else ""))
        return
    if not current:
        await target.reply_text(PROMPT_EXPIRED)
        return
    del _prompts[ext]
    user = await _who(update)
    if user is not None:
        await target.reply_text(
            f"This Telegram is already connected to {user.label}. To connect a different account, send /unlink first."
        )
        return
    if not await asyncio.to_thread(_said_yes, prompt.code, ext, getattr(tg_user, "username", None), _telegram_name(tg_user)):
        await target.reply_text(CODE_GONE)
        return
    wrong_codes.right(ext)
    await target.reply_text(
        f"Almost done: confirm in Carlink → Settings → My Telegram.\n{_settings_url()}", link_preview_options=NO_PREVIEW
    )


# ---------------------------------------------------------------- commands


@_private_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    async with _lock_for(chat_id):
        args = _args(context)
        user = await _who(update)
        if _too_soon(chat_id, user):
            return
        if args and args[0].startswith(DEEP_LINK_PREFIX):
            # The deep link from Settings: t.me/<bot>?start=link-K7M4QX9P.
            await _offer_link(update, code_from_start_payload(args[0]), user)
            return
        if user is None:
            await tg_public.on_start(update, context)
            return
        _reset(chat_id)
        await update.message.reply_text(WELCOME_GUIDE, parse_mode="Markdown")


@_private_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    async with _lock_for(chat_id):
        user = await _who(update)
        if user is None:
            await tg_public.on_help(update, context)
            return
        if _too_soon(chat_id, user):
            return
        await update.message.reply_text(WELCOME_GUIDE, parse_mode="Markdown")


@_private_only
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    async with _lock_for(chat_id):
        if await _who(update) is None:
            await tg_public.on_cancel(update, context)
            return
        _reset(chat_id)
        await update.message.reply_text("🔄 Current report session cancelled and reset. Send /new or a photo to start a fresh report.")


@_private_only
async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/withdraw: a public sender takes back a report they sent (P6)."""
    chat_id = str(update.effective_chat.id)
    async with _lock_for(chat_id):
        if await _who(update) is None:
            await tg_public.on_withdraw(update, context)
            return
        await update.message.reply_text(
            "/withdraw is for reports sent by the public. Your cases are in Carlink, where you can delete a draft."
        )


@_private_only
async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    async with _lock_for(chat_id):
        typed = " ".join(_args(context)).strip()
        user = await _who(update)
        if _too_soon(chat_id, user):
            return
        if not typed and user is None:
            await update.message.reply_text(
                "Send me your code like this: /link K7M4-QX9P\n"
                f"Make one in Carlink → Settings → My Telegram:\n{_settings_url()}",
                link_preview_options=NO_PREVIEW,
            )
            return
        await _offer_link(update, normalize_code(typed) if typed else None, user)


@_private_only
async def unlink_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    async with _lock_for(chat_id):
        # Also takes back a Yes to a code still waiting for Carlink (L2).
        gone, label, withdrawn = await asyncio.to_thread(_unlink_telegram, str(update.effective_user.id))
        # Any draft was that account's.
        _drop(chat_id)
        withdrawn_note = f"\n{YES_WITHDRAWN}" if withdrawn else ""
        if not gone:
            await update.message.reply_text("This Telegram isn't connected to a Carlink account." + withdrawn_note)
            return
        await update.message.reply_text(
            f"Disconnected: this Telegram no longer files reports{f' as {label}' if label else ''}.{withdrawn_note}\n"
            f"To connect again, open Carlink → Settings → My Telegram:\n{_settings_url()}",
            link_preview_options=NO_PREVIEW,
        )


@_private_only
async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    async with _lock_for(chat_id):
        user = await _who(update)
        if _too_soon(chat_id, user):
            return
        if user is None:
            await update.message.reply_text(
                f"Not connected to a Carlink account.\nTo connect, open Carlink → Settings → My Telegram:\n{_settings_url()}",
                link_preview_options=NO_PREVIEW,
            )
            return
        await update.message.reply_text(f"Connected as {user.label}.\nSend /unlink to disconnect this Telegram.")


def _build_inline_keyboard(session) -> InlineKeyboardMarkup:
    buttons = []
    # Primary confirmation button
    buttons.append([InlineKeyboardButton("✅ Confirm & save to my cases", callback_data="confirm")])

    # Side placement options if undetermined
    if session.draft and any("(side undetermined)" in (item.part or "") for item in (session.draft.damage_summary or [])):
        buttons.append([
            InlineKeyboardButton("⬅️ Left Side", callback_data="side_left"),
            InlineKeyboardButton("➡️ Right Side", callback_data="side_right"),
            InlineKeyboardButton("⬆️ Front", callback_data="side_front"),
            InlineKeyboardButton("⬇️ Rear", callback_data="side_rear"),
        ])

    # Body type selector if undetermined or to override
    if session.draft and session.draft.vehicle_info and not getattr(session.draft.vehicle_info, "body_type", None):
        buttons.append([
            InlineKeyboardButton("🚙 SUV", callback_data="body_SUV"),
            InlineKeyboardButton("🚗 Sedan", callback_data="body_Sedan"),
            InlineKeyboardButton("🚐 Van", callback_data="body_Van"),
            InlineKeyboardButton("🚐 MPV", callback_data="body_MPV"),
            InlineKeyboardButton("🚗 Hatch", callback_data="body_Hatchback"),
        ])

    return InlineKeyboardMarkup(buttons)


# ------------------------------------------------------- photos and text


@_private_only
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _lock_for(str(update.effective_chat.id)):
        await _handle_photo(update, context)


async def _handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    user = await _who(update)
    if user is None:
        await tg_public.on_photo(update, context)
        return
    # A photo that finds an expired draft starts the new one, so the rest of
    # an album sent with it isn't split across two drafts.
    session, _ = await _current_session(update, chat_id, user)
    if await _photo_limit_reached(update, session) or await _disk_full(update):
        return
    if not session.photo_paths and not await _ai_ready(update, chat_id, user):
        return
    if await _photo_budget_spent(update, user):
        return

    dest = _new_temp_path(".jpg")
    await _download(update.message.photo[-1], dest, user)
    session.photo_paths.append(str(dest))

    caption = (update.message.caption or "").strip()
    if caption:
        parsed = parse_template_reply(caption)
        if parsed:
            if parsed.get("location"): session.location = parsed["location"]
            if parsed.get("incident_datetime"): session.incident_datetime = parsed["incident_datetime"]
            if parsed.get("reporter_name"): session.reporter_name = parsed["reporter_name"]
            if parsed.get("vehicle_plate"): session.vehicle_plate = parsed["vehicle_plate"]
            if parsed.get("insurer_name"): session.insurer_name = parsed["insurer_name"]
            if parsed.get("workshop_name"): session.workshop_name = parsed["workshop_name"]
            if parsed.get("damaged_side"): session.damaged_side = parsed["damaged_side"]
            session.description = parsed.get("description") or caption
        else:
            session.pending_edits.append(caption)

    if session.stage == Stage.AWAITING_CONFIRMATION:
        await update.message.reply_text(
            f"📸 Additional photo received ({len(session.photo_paths)} total). Updating draft..."
        )
        await draft_and_reply(update, session, combined_description(session), redrafting=True, user=user)
        return

    await update.message.reply_text(
        f"Got it -- {len(session.photo_paths)} photo(s) received. Send more or describe what happened."
    )
    if not session.template_sent:
        session.template_sent = True
        await update.message.reply_text(build_template_prompt(), parse_mode="Markdown")


@_private_only
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _lock_for(str(update.effective_chat.id)):
        chat_id = str(update.effective_chat.id)
        user = await _who(update)
        if user is None:
            await tg_public.on_document(update, context)  # files: never from the public
            return
        session, _ = await _current_session(update, chat_id, user)
        doc = update.message.document
        if not doc:
            return
        ext = DOCUMENT_EXTENSIONS.get((doc.mime_type or "").lower())
        if not ext:
            await update.message.reply_text(
                "Sorry, I can only use photos (JPG, PNG, WebP or HEIC). Please send this as a photo or an image file."
            )
            return
        if await _photo_limit_reached(update, session) or await _disk_full(update):
            return
        if not session.photo_paths and not await _ai_ready(update, chat_id, user):
            return
        if await _photo_budget_spent(update, user):
            return
        dest = _new_temp_path(ext)
        await _download(doc, dest, user)
        session.photo_paths.append(str(dest))

        caption = (update.message.caption or "").strip()
        if caption:
            session.pending_edits.append(caption)

        await update.message.reply_text(
            f"📄 Evidence document received: {doc.file_name or 'file'}. Added to case."
        )
        if session.stage == Stage.AWAITING_CONFIRMATION:
            await draft_and_reply(update, session, combined_description(session), redrafting=True, user=user)
        elif not session.template_sent:
            session.template_sent = True
            await update.message.reply_text(build_template_prompt(), parse_mode="Markdown")


@_private_only
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _lock_for(str(update.effective_chat.id)):
        await _handle_text(update, context)


def _public_only(public_handler):
    """A message only the public form takes (a contact card, a location
    pin, anything else that isn't text, a photo or a file): from staff it is
    ignored, as before P6."""

    @_private_only
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        async with _lock_for(str(update.effective_chat.id)):
            if await _who(update) is None:
                await public_handler(update, context)

    handler.__name__ = handler.__qualname__ = f"handle_{public_handler.__name__.removeprefix('on_')}"
    return handler


handle_contact = _public_only(tg_public.on_contact)
handle_location = _public_only(tg_public.on_location)
handle_other = _public_only(tg_public.on_other)


async def _handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    text = (update.message.text or "").strip()
    user = await _who(update)
    if user is None:
        # A bare code, only in the exact XXXX-XXXX shape -- a plate
        # (SJB8762M) or a word (STANDARD) never is one (agent6 M6) -- only
        # from a Telegram with no account, no staff draft and no public form
        # open, and never in a forwarded message (H2).
        existing = find_session(chat_id)
        drafting = existing is not None and (
            existing.public is not None or bool(existing.photo_paths) or existing.draft is not None
        )
        if looks_like_code(text) and not _forwarded(update.message) and not drafting:
            await _offer_link(update, normalize_code(text), None)
            return
        await tg_public.on_text(update, context)
        return
    session, stale = await _current_session(update, chat_id, user)
    if stale:
        return  # meant for the draft just thrown away

    if session.stage == Stage.AWAITING_CONFIRMATION:
        if text.lower() in CONFIRM_WORDS:
            await finalize_report(update, session, chat_id, user)
            return
        session.pending_edits.append(text)
        await draft_and_reply(update, session, combined_description(session), redrafting=True, user=user)
        return

    if not session.photo_paths:
        await update.message.reply_text(
            "Please send at least one incident photo or document first, then describe what happened."
        )
        return

    parsed = parse_template_reply(text)
    if parsed:
        if parsed.get("location"): session.location = parsed["location"]
        if parsed.get("incident_datetime"): session.incident_datetime = parsed["incident_datetime"]
        if parsed.get("reported_to_authorities") is not None: session.reported_to_authorities = parsed["reported_to_authorities"]
        if parsed.get("reporter_name"): session.reporter_name = parsed["reporter_name"]
        if parsed.get("reporter_role"): session.reporter_role = parsed["reporter_role"]
        if parsed.get("reporter_contact"): session.reporter_contact = parsed["reporter_contact"]
        if parsed.get("vehicle_plate"): session.vehicle_plate = parsed["vehicle_plate"]
        if parsed.get("damaged_side"): session.damaged_side = parsed["damaged_side"]
        if parsed.get("insurer_name"): session.insurer_name = parsed["insurer_name"]
        if parsed.get("workshop_name"): session.workshop_name = parsed["workshop_name"]
        description = parsed.get("description") or text
    else:
        description = text

    session.description = description
    await draft_and_reply(update, session, description, user=user)


async def draft_and_reply(update: Update, session, description: str, redrafting: bool = False, *, user: Linked) -> None:
    """Step 7: drafts on this person's AI (user.actor: their own key, or the
    company's if they may use it). An unvetted account gets one AI request
    at a time, as on the dashboard (api/limits.ai_running, this process's
    own record: the chat lock already keeps one Telegram to one at a time)."""
    notice = "Redrafting with your changes..." if redrafting else "Drafting your report..."
    msg_target = _reply_target(update)
    actor = user.actor
    if user.unvetted and not limits.ai_running.claim(user.id):
        if msg_target:
            await msg_target.reply_text(STILL_DRAFTING)
        return

    def _queued_seconds() -> float:
        try:
            first = resolve_keys(actor)[0]
        except AiUnavailable:
            return 0.0  # no key at all: the drafting call below says so
        return estimate_wait(first.rate_limit_id)

    try:
        queued = await asyncio.to_thread(_queued_seconds)
        if msg_target:
            if queued >= 5:
                await msg_target.reply_text(f"{notice} (busy right now -- about {round(queued)}s in queue)")
            else:
                await msg_target.reply_text(notice)
        result = await asyncio.to_thread(build_draft, description, session.photo_paths, session, actor=actor)
    except AiUnavailable as exc:
        # Their AI went off mid-draft (key removed or failing, tick taken
        # away): said plainly, and the draft and photos stay for a retry.
        if msg_target:
            await msg_target.reply_text(_ai_off_text(exc), link_preview_options=NO_PREVIEW)
        return
    except Exception:
        logger.exception("AI drafting failed")
        if msg_target:
            await msg_target.reply_text(
                "Something went wrong while drafting the report. Please try again."
            )
        return
    finally:
        if user.unvetted:
            limits.ai_running.release(user.id)

    session.draft = result.draft
    session.stage = Stage.AWAITING_CONFIRMATION
    keyboard = _build_inline_keyboard(session)

    if msg_target:
        # The account is named, and an unchecked one said to be so (M2):
        # filing into a look-alike signup is what a phished link is for.
        warning = f"\n{UNVETTED_WARNING}" if user.unvetted else ""
        filing_as = user.filing_label or user.label
        try:
            await msg_target.reply_text(
                f"👤 Filing as {escape_markdown(filing_as)}{warning}\n\n{result.summary_text}",
                parse_mode="Markdown", reply_markup=keyboard,
            )
        except BadRequest:
            logger.warning("Draft summary failed to parse as Markdown; sending unformatted")
            await msg_target.reply_text(f"👤 Filing as {filing_as}{warning}\n\n{result.summary_text}", reply_markup=keyboard)


# ------------------------------------------------------- buttons and Confirm


async def _answer_quietly(query, text: str | None = None) -> None:
    try:
        await query.answer(text)
    except Exception:
        pass  # too old to answer: only tidying


def _claim_tap(update: Update):
    """Run by the gate before its first await: a Confirm tap (staff) claims
    the chat's draft with its finalizing flag, a Submit tap (public) its
    form with submitting, so a second tap, even a near-simultaneous one, is
    only answered -- here, rather than queued behind the chat lock -- and
    saves nothing. (busy answer or None, release or None)."""
    data = update.callback_query.data or ""
    if data.startswith(tg_public.CALLBACK_PREFIX):
        return tg_public.claim_submit(update)
    if data != "confirm":
        return None, None
    claimed = find_session(str(update.effective_chat.id))
    if claimed is None:
        return None, None
    if claimed.finalizing:
        return "Already saving this report -- one moment.", None
    claimed.finalizing = True

    def release() -> None:
        claimed.finalizing = False

    return None, release


@_private_only(claim=_claim_tap)
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    data = query.data or ""
    looked, user = _seen(update)
    # Buttons are untrusted: a public form's (pub:...) do something only for
    # someone not connected, and staff drafts' only for staff.
    if data.startswith(tg_public.CALLBACK_PREFIX):
        if looked and user is None:
            await tg_public.on_callback(update, context)
        else:
            await _answer_quietly(query)
        return
    if looked and user is None and not data.startswith((LINK_YES, LINK_NO)):
        await tg_public.answer_tap(query)
        async with _lock_for(chat_id):
            await tg_public.on_staff_button(update, context)
        return
    await query.answer()
    async with _lock_for(chat_id):
        await _handle_callback(update, context)


async def _handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    data = query.data or ""
    if data.startswith((LINK_YES, LINK_NO)):
        await _link_tap(update, data)
        return
    user = await _who(update)
    if user is None:
        await tg_public.on_staff_button(update, context)
        return
    session, stale = await _current_session(update, chat_id, user)
    if stale:
        return  # a tap on the draft just thrown away

    if data == "confirm":
        if session.draft:
            await finalize_report(update, session, chat_id, user)
        else:
            await query.message.reply_text("No active report draft to confirm. Send photos to begin.")
        return
    elif data.startswith("side_"):
        side = data.replace("side_", "")
        session.damaged_side = side
        session.pending_edits.append(f"the damage is on the {side} side")
        await draft_and_reply(update, session, combined_description(session), redrafting=True, user=user)
    elif data.startswith("ins_"):
        insurer_map = {
            "ins_tokio": "Tokio Marine Insurance Singapore",
            "ins_income": "NTUC Income Insurance Co-operative",
            "ins_aig": "AIG Asia Pacific Insurance",
        }
        ins_name = insurer_map.get(data)
        if not ins_name:
            return  # unknown button: don't record an insurer nobody chose
        session.insurer_name = ins_name
        session.pending_edits.append(f"insurer is {ins_name}")
        await draft_and_reply(update, session, combined_description(session), redrafting=True, user=user)
    elif data.startswith("body_"):
        b_type = data.replace("body_", "")
        session.vehicle_body_type = b_type
        if session.draft and session.draft.vehicle_info:
            session.draft.vehicle_info.body_type = b_type
        session.pending_edits.append(f"vehicle body type is {b_type}")
        await draft_and_reply(update, session, combined_description(session), redrafting=True, user=user)


async def finalize_report(update: Update, session, chat_id: str, user: Linked) -> None:
    # While this runs a second Confirm tap is answered, not saved (see
    # handle_callback). Cleared on failure too, so the user can retry.
    session.finalizing = True
    try:
        await _finalize_report(update, session, chat_id, user)
    finally:
        session.finalizing = False


async def _finalize_report(update: Update, session, chat_id: str, user: Linked) -> None:
    msg_target = _reply_target(update)
    external_id = str(update.effective_user.id)

    # Step 8, before anything is written: still this person's Telegram, and
    # inside the daily case cap for accounts without staff ticks.
    still_linked, problem = await asyncio.to_thread(_check_filing, external_id, user.id)
    if problem:
        if not still_linked:
            _drop(chat_id)  # the draft was for an account this Telegram no longer files as
        if msg_target:
            await msg_target.reply_text(problem)
        return

    # Backstop: _keep_temp_files keeps a live draft's photos from the api's
    # day-old cleanup, but if any are gone anyway, save what's left rather
    # than crash.
    present = [p for p in session.photo_paths if Path(p).exists()]
    if len(present) < len(session.photo_paths):
        logger.warning(
            "Confirm in chat %s: %d of %d temp photos are gone; saving without them",
            chat_id, len(session.photo_paths) - len(present), len(session.photo_paths),
        )
        session.photo_paths = present
    if not present:
        if msg_target:
            await msg_target.reply_text(
                "The photos for this draft are no longer on the server. Please send them again, then confirm."
            )
        return

    # Photos and PDF before the database row, so no SQLite write lock is held
    # while Chromium renders: flushing the row first held it for the whole
    # render, and every other writer (dashboard uploads, autosaves) failed
    # with "database is locked" meanwhile.
    data = session.draft.model_dump()
    # As POST /reports does (api/main.blank_sign_off, limits.checked_our_ref):
    # filing signs nothing off, and Our Ref is staff's to set (review L4).
    data["sign_off"] = SignOffInfo().model_dump()
    if not user.staff:
        data["report_id"] = None
    report_id = gen_id()
    stored_photos = [save_photo(report_id, p, i) for i, p in enumerate(session.photo_paths, start=1)]
    pdf_path: str | None = report_pdf_path(report_id)
    try:
        # The long wait for a Chromium slot (renderer.py): the bot renders
        # seldom, on its own threads. Not saved yet, so the renderer can't
        # look the owner up: told whether their PDF needs the unverified
        # banner (as POST /reports does).
        await asyncio.to_thread(
            render_pdf, data, stored_photos, pdf_path,
            report_id=report_id, unverified=not user.staff, slot_wait=BOT_RENDER_SLOT_WAIT_SECONDS,
        )
    except Exception as exc:
        logger.error("Failed to render PDF for report %s in Telegram: %s", report_id, exc)
        pdf_path = None

    try:
        still_linked, problem = await asyncio.to_thread(
            _save_case, report_id, external_id, user.id, data, stored_photos, pdf_path
        )
    except Exception:
        remove_incident_dirs(report_id)  # no case row, so no files for one
        raise
    if problem:
        remove_incident_dirs(report_id)
        if not still_linked:
            _drop(chat_id)
        if msg_target:
            await msg_target.reply_text(problem)
        return
    # Saved: drop the draft before replying, so a reply that fails can't
    # leave it there to be confirmed a second time.
    _discard_temp_files(session)
    reset_session(chat_id)

    if msg_target:
        # The link, not the PDF file: the PDF stays behind sign-in (D8).
        await msg_target.reply_text(
            f"✅ Report CIR-{report_id[:8].upper()} is saved to your cases in Carlink.\n\n"
            f"Open it (sign in to see the PDF):\n{_base_url()}/reports/{report_id}\n\n"
            "Send /new or a photo to file another report.",
            link_preview_options=NO_PREVIEW,
        )


# ------------------------------------------------------------- the app


def _save_bot_username(username: str | None) -> None:
    from app.auth.chat_links import save_bot_username

    db = SessionLocal()
    try:
        save_bot_username(db, username)
        db.commit()
    finally:
        db.close()


async def _post_init(app: Application) -> None:
    # The default threads serve every _who lookup and staff's AI drafts and
    # renders; asyncio's own default (cpu + 4) ran out first under load. The
    # public form has its own two (telegram_public._executor).
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=DEFAULT_THREADS, thread_name_prefix="telegram")
    )
    # Settings > My Telegram's "Open Telegram" deep link needs the bot's
    # @username, which only the bot knows (initialize() ran getMe).
    try:
        await asyncio.to_thread(_save_bot_username, app.bot.username)
    except Exception as exc:
        logger.warning("Could not save the bot's @username for Settings (%s)", type(exc).__name__)
    try:
        await app.bot.set_my_name("Carlink Car Incident Reporter")
        # What anyone sees before the first message: the public form first.
        await app.bot.set_my_description(
            "Carlink, vehicle damage surveyors.\n\n"
            "Had a car accident or damage? Send us a report here: your name, your phone number, the car plate, "
            "what happened and photos. It takes about 3 minutes.\n\n"
            "Carlink staff: connect your account with /link (Carlink → Settings → My Telegram)."
        )
        await app.bot.set_my_short_description("Send Carlink a car accident or damage report")
        await app.bot.set_my_commands([
            ("start", "Send a car accident or damage report"),
            ("help", "What I can do"),
            ("cancel", "Stop the report you're filling in"),
            ("withdraw", "Take back a report you've sent"),
            ("new", "Staff: start a new report"),
            ("link", "Staff: connect this Telegram with a code from Carlink"),
            ("whoami", "Staff: the Carlink account this Telegram files as"),
            ("unlink", "Staff: disconnect this Telegram from Carlink"),
        ])
    except Exception as exc:
        logger.warning("Failed to set bot profile info (%s)", type(exc).__name__)


def _update_kind(update) -> str:
    for kind in ("message", "edited_message", "callback_query", "channel_post", "edited_channel_post",
                 "business_message", "my_chat_member", "chat_member", "inline_query"):
        if getattr(update, kind, None) is not None:
            return kind
    return type(update).__name__


async def _on_error(update, context) -> None:
    """A handler raised. Logged with the update's id and kind and where it
    happened -- never the update itself, which holds names, phone numbers
    and what people typed (review L3), nor the error's text."""
    error = getattr(context, "error", None)
    where = "".join(traceback.format_tb(error.__traceback__)) if error is not None else ""
    logger.error(
        "Telegram update %s (%s) failed: %s\n%s",
        getattr(update, "update_id", None), _update_kind(update), type(error).__name__, where,
    )


def build_app() -> Application:
    init_db()
    # Own AI keys: the bot only reads the secret the api makes, and checks
    # the canary (ai/secretbox.py). Each draft runs on the linked person's
    # key, or the company's if they may use it (Linked.actor).
    from app.ai import secretbox

    secretbox.startup(create=False)
    # PTB's own DEBUG lines print whole updates -- names, phone numbers,
    # contact cards (review L3): never below INFO.
    ptb_log = logging.getLogger("telegram")
    if ptb_log.getEffectiveLevel() < logging.INFO:
        ptb_log.setLevel(logging.INFO)
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .concurrent_updates(CONCURRENT_UPDATES)
        .post_init(_post_init)
        .build()
    )
    # New messages only. Edited messages and channel posts matched these
    # too, and with no update.message they crashed the handlers.
    new_message = filters.UpdateType.MESSAGE
    application.add_handler(CommandHandler(["start", "new"], start, filters=new_message))
    application.add_handler(CommandHandler("help", help_command, filters=new_message))
    application.add_handler(CommandHandler("cancel", cancel_command, filters=new_message))
    application.add_handler(CommandHandler("link", link_command, filters=new_message))
    application.add_handler(CommandHandler("unlink", unlink_command, filters=new_message))
    application.add_handler(CommandHandler("whoami", whoami_command, filters=new_message))
    application.add_handler(CommandHandler("withdraw", withdraw_command, filters=new_message))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.PHOTO & new_message, handle_photo))
    application.add_handler(MessageHandler(filters.Document.ALL & new_message, handle_document))
    application.add_handler(MessageHandler(filters.CONTACT & new_message, handle_contact))
    application.add_handler(MessageHandler(filters.LOCATION & new_message, handle_location))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & new_message, handle_text))
    # Last: anything else in a new message (video, voice, a sticker, a
    # command this bot doesn't have). One handler per update, the first
    # that matches.
    application.add_handler(MessageHandler(new_message, handle_other))
    application.add_error_handler(_on_error)
    return application
