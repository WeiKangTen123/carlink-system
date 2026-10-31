"""Helpers both Telegram flows use: the staff bot (channels/telegram.py,
which re-exports them, so older code and tests still find them there) and
the public report form (channels/telegram_public.py). Kept here so the
public module never imports telegram.py, which imports it.
"""
import asyncio
import logging
import os
import time
import uuid
from pathlib import Path

from telegram import LinkPreviewOptions, Update

from app.conversation.state import drop_session
from app.storage.files import tmp_dir

logger = logging.getLogger("app.channels.telegram")

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
# "Connect first" and "private chat only": at most this often per chat, so
# a stranger's album (or a busy group) gets one answer, not one per message.
REPLY_EVERY_SECONDS = 60 * 60
# The api's cleanup deletes storage/tmp files after a day (intake/cleanup.py
# TMP_MAX_AGE), so a draft idle longer than that may have lost its photos.
SESSION_MAX_IDLE_SECONDS = 24 * 60 * 60
# In-memory tables keyed by chat or Telegram user (prompts, "said at"
# times, chat locks) are pruned past this many entries.
MAX_ENTRIES = 5000

# "connect:<chat>", "group:<chat>", "ai-off:<chat>", "pub-welcome:<chat>"...
# -> when last said.
_told_at: dict[str, float] = {}


def _first_in(key: str, seconds: float) -> bool:
    """Whether to say the thing keyed `key` now, at most once per `seconds`
    (and if so, it counts as said)."""
    now = time.monotonic()
    last = _told_at.get(key)
    if last is not None and now - last < seconds:
        return False
    if len(_told_at) >= MAX_ENTRIES:
        # Day-long keys (a blocked sender's notice) outlive the hour: kept
        # until a day has passed, everything else after an hour.
        for old in [k for k, at in _told_at.items() if now - at >= max(REPLY_EVERY_SECONDS, _keep_for(k))]:
            del _told_at[old]
    _told_at[key] = now
    return True


def _keep_for(key: str) -> float:
    return SESSION_MAX_IDLE_SECONDS if key.startswith("pub-blocked:") else 0


def _forwarded(message) -> bool:
    return getattr(message, "forward_origin", None) is not None


def _telegram_name(tg_user) -> str | None:
    return getattr(tg_user, "full_name", None) or getattr(tg_user, "first_name", None)


def _reply_target(update: Update):
    return update.message or (update.callback_query.message if update.callback_query else None)


# One per chat, held by every handler, so updates from one chat run one at
# a time: a /cancel can't delete photos a Confirm is still copying.
_chat_locks: dict[str, asyncio.Lock] = {}


def _lock_for(chat_id: str) -> asyncio.Lock:
    """The chat's lock. Past MAX_ENTRIES chats, the locks nobody holds or
    waits for are forgotten first (review L5): a lock is taken in the same
    step as it is looked up here, so a free one is never in use."""
    lock = _chat_locks.get(chat_id)
    if lock is None:
        if len(_chat_locks) >= MAX_ENTRIES:
            for key in [k for k, v in _chat_locks.items() if not v.locked() and not getattr(v, "_waiters", None)]:
                del _chat_locks[key]
        lock = _chat_locks[chat_id] = asyncio.Lock()
    return lock


def _new_temp_path(ext: str) -> Path:
    # Random, not {chat_id}_{n}: the dashboard API takes any storage/tmp
    # file by name (temp_photo_paths), and chat ids are easy to guess.
    return tmp_dir() / f"tg-{uuid.uuid4().hex}{ext}"


def _discard_temp_files(session) -> None:
    """Deletes a session's downloads from storage/tmp once its case is saved
    or its draft is dropped, rather than leaving them there for a day."""
    for path in session.photo_paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not delete temp file %s: %s", path, exc)


def _keep_temp_files(session) -> None:
    """Marks a live draft's downloads as fresh. The api's cleanup deletes
    storage/tmp files by age (mtime), so a draft worked on for over a day
    would otherwise lose its earliest photos; an idle one expires first."""
    for path in session.photo_paths:
        try:
            os.utime(path)
        except OSError:
            pass


def _stop_public(session) -> None:
    """A public form's pending album reply, so it isn't sent for a form
    that is gone."""
    form = getattr(session, "public", None)
    task = getattr(form, "ack_task", None)
    if task is not None and not task.done():
        task.cancel()


def _drop(chat_id: str) -> None:
    """Forgets the chat's session and its downloads, keeping nothing."""
    session = drop_session(chat_id)
    if session is not None:
        _stop_public(session)
        _discard_temp_files(session)
