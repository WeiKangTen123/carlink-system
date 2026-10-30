"""Telegram accounts connected to Carlink accounts (models.ChatLink), and
the one-time codes that connect them (models.ChatLinkCode).

Used by both processes on the one SQLite database: the api (Settings > My
Telegram, app/api/telegram_link.py) and the telegram-bot
(app/channels/telegram.py). Every function takes a database session and
never commits: the caller commits, so each can be part of a bigger change
(disabling an account ends its links in that same transaction). A step
that must happen at most once is one conditional UPDATE, decided by the
database alone, so two processes or two taps can't both win.

Connecting needs both sides (agent6 H2):
  1. Settings: new_code() makes "K7M4-QX9P": 10 minutes, once, and a new
     one cancels the last.
  2. Telegram: the person sends it (the deep link t.me/<bot>?start=link-
     K7M4QX9P, "/link K7M4-QX9P", or the bare code from a Telegram that
     isn't connected). peek_code() names the account for the "Is this
     you?" prompt; [Yes, that's me] calls telegram_said_yes(), and the code
     is "waiting for Carlink". The same code then sent from another
     Telegram cancels it (contest_code); /unlink and [Cancel] take a Yes
     back (withdraw_yes).
  3. Settings shows "Telegram @x (Name) wants to connect": [Yes, connect]
     calls confirm_from_web(accept=True), which makes the link; [Not me]
     revokes the code.
After that, linked_user() says who a Telegram user files as, read fresh on
every update. Links end on disconnect (unlink) and, with any open code, on
disabling the account and on every password change or reset
(end_chat_links).

The identity is the Telegram USER id (update.effective_user.id) as text,
never a chat id. Codes are stored only as their SHA-256 and never logged.
"""
import logging
import re
import secrets
import threading
import time
import unicodedata
from collections import OrderedDict, deque
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.auth.core import as_utc, audit, token_hash, utc_now
from app.reports.models import AppSetting, ChatLink, ChatLinkCode, User, iso_utc

logger = logging.getLogger(__name__)

CHANNEL = "telegram"
# 31 characters: no 0/O, 1/I or L, which are easy to misread.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CODE_TTL = timedelta(minutes=10)
CODES_PER_HOUR = 10
# Codes are deleted this long after they expire (app/intake/cleanup.py).
KEEP_EXPIRED_CODES = timedelta(days=1)
# linked_user() writes last_used_at at most this often per link.
TOUCH_EVERY = timedelta(minutes=10)
# The /start payload of the deep link: t.me/<bot>?start=link-K7M4QX9P.
DEEP_LINK_PREFIX = "link-"
# app_settings row the bot writes when it starts (its getMe @username).
# Not in api/main.ALLOWED_SETTING_KEYS, so GET /settings never shows it.
BOT_USERNAME_KEY = "telegram_bot_username"

TOO_MANY_CODES = "Too many Telegram codes in the last hour. Try again later."
ACCOUNT_OFF = "This account has been disabled."
NOTHING_WAITING = "Nothing is waiting to connect. Make a new code and send it from Telegram."
CODE_EXPIRED = "That code has expired. Make a new one and send it from Telegram again."
TELEGRAM_TAKEN = "That Telegram has just been connected to another Carlink account."

_A = CODE_ALPHABET
# A bare message: exactly XXXX-XXXX. Plates (SJB8762M, "GBH 2345 K") and
# words (STANDARD) have no dash in the middle, so they never match (M6).
_STRICT = re.compile(rf"[{_A}]{{4}}-[{_A}]{{4}}")
# Something the person means as a code (/link CODE, the deep link): the
# two halves with a dash, a space or nothing between them.
_LOOSE = re.compile(rf"([{_A}]{{4}})[- ]?([{_A}]{{4}})")
_MAX_TEXT = 32


class ChatLinkError(Exception):
    """Why a code or a confirmation was refused: .code is what the API sends
    as {"code": ...}, .status its HTTP status, str() the message to show."""

    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ------------------------------------------------------------- code text


def normalize_code(text) -> str | None:
    """The code as stored (8 characters, upper case, no dash) if text is
    one -- K7M4-QX9P, K7M4QX9P or K7M4 QX9P, any case, spaces around --
    else None. For text the person means as a code: /link CODE and the
    deep link. A bare message must pass looks_like_code first."""
    if not isinstance(text, str) or len(text) > _MAX_TEXT:
        return None
    m = _LOOSE.fullmatch(text.strip().upper())
    return m.group(1) + m.group(2) if m else None


def looks_like_code(text) -> bool:
    """Whether a bare message is a code: strictly XXXX-XXXX from the
    alphabet (any case, spaces around allowed). The bot also takes one only
    from a Telegram that isn't connected, with no draft open, and never from
    a forwarded message."""
    return isinstance(text, str) and len(text) <= _MAX_TEXT and _STRICT.fullmatch(text.strip().upper()) is not None


def code_from_start_payload(payload) -> str | None:
    """The code in a /start payload ("link-K7M4QX9P"), or None."""
    if not isinstance(payload, str) or not payload.startswith(DEEP_LINK_PREFIX):
        return None
    return normalize_code(payload[len(DEEP_LINK_PREFIX):])


def display_code(code: str) -> str:
    """K7M4QX9P -> K7M4-QX9P, the way Settings shows it."""
    canonical = normalize_code(code) or code
    return f"{canonical[:4]}-{canonical[4:]}"


def deep_link(bot_username: str | None, code: str) -> str | None:
    """https://t.me/<bot>?start=link-K7M4QX9P, or None while the bot's
    @username isn't known yet."""
    canonical = normalize_code(code)
    if not bot_username or not canonical:
        return None
    return f"https://t.me/{bot_username}?start={DEEP_LINK_PREFIX}{canonical}"


def masked_email(email: str | None) -> str:
    """w***@example.com, for the bot's "Is this you?" prompt."""
    local, _, domain = (email or "").partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


def telegram_label(username: str | None, name: str | None) -> str:
    """How a Telegram is named in the history: the @username and the name
    as separate parts -- "@ada_tg · Ada T", "@ada_tg", "no @username · Ada
    T", or "a Telegram account" -- so a name can't pass for an @username."""
    username, name = _clean(username, 64), _clean(name, 128)
    if username and name:
        return f"@{username} · {name}"
    if username:
        return f"@{username}"
    if name:
        return f"no @username · {name}"
    return "a Telegram account"


# Characters that read as "@". A Telegram first name "＠weikang (Wei Kang)"
# (full-width ＠) passed for an @username (review M1).
_AT_SIGNS = "@＠﹫"


def _clean(value, limit: int) -> str | None:
    """A Telegram @username or name, or a Carlink account's, as shown:
    NFKC-normalised (full-width and other compatibility forms become plain
    letters), no "@" anywhere, no invisible, format or control characters
    (a right-to-left mark or a zero-width space could make one read as
    another), spaces collapsed, at most limit characters."""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value))
    visible = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C" and ch not in _AT_SIGNS)
    text = " ".join(visible.split())[:limit].strip()
    return text or None


def clean_name(value, limit: int = 128) -> str | None:
    """_clean, for the bot: an account's name or @username in its prompts."""
    return _clean(value, limit)


# ------------------------------------------------------------ bot @username


def bot_username(db: Session) -> str | None:
    row = db.get(AppSetting, BOT_USERNAME_KEY)
    return (row.value or None) if row else None


def save_bot_username(db: Session, username: str | None) -> None:
    """The bot's own @username (getMe), written by the bot when it starts,
    so Settings can offer the t.me deep link."""
    value = _clean(username, 64) if isinstance(username, str) else None
    if not value:
        return
    row = db.get(AppSetting, BOT_USERNAME_KEY)
    if row is None:
        db.add(AppSetting(key=BOT_USERNAME_KEY, value=value))
    elif row.value != value:
        row.value = value


# ------------------------------------------------------------------ codes


def _open(now: datetime):
    """A code that can still be used: not linked, not revoked, unexpired."""
    return (
        ChatLinkCode.channel == CHANNEL,
        ChatLinkCode.linked_at.is_(None),
        ChatLinkCode.revoked_at.is_(None),
        ChatLinkCode.expires_at > now,
    )


def _active_user_ids():
    return select(User.id).where(User.active.is_(True))


def new_code(db: Session, user) -> tuple[str, ChatLinkCode]:
    """A new code for this account (a User row or CurrentUser), with its
    row: the only time the plain code exists outside the person's screen.
    Cancels the account's open codes first, a waiting one too.

    Raises ChatLinkError rate_limited (429) after CODES_PER_HOUR codes in
    an hour, or account_disabled (403) -- AFTER writing, so roll back then
    (the earlier code stays usable)."""
    now = utc_now()
    # The first statement, and a write: it takes SQLite's write lock, so
    # the count below still holds at the caller's commit -- two tabs at
    # once can't both make the tenth.
    db.execute(
        update(ChatLinkCode)
        .where(ChatLinkCode.channel == CHANNEL, ChatLinkCode.user_id == user.id,
               ChatLinkCode.linked_at.is_(None), ChatLinkCode.revoked_at.is_(None))
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    if not db.query(User.active).filter(User.id == user.id).scalar():
        raise ChatLinkError("account_disabled", ACCOUNT_OFF, 403)
    made = (
        db.query(func.count(ChatLinkCode.id))
        .filter(ChatLinkCode.channel == CHANNEL, ChatLinkCode.user_id == user.id,
                ChatLinkCode.created_at > now - timedelta(hours=1))
        .scalar()
    )
    if made >= CODES_PER_HOUR:
        raise ChatLinkError("rate_limited", TOO_MANY_CODES, 429)
    while True:
        plain = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        hashed = token_hash(plain)
        # 31^8 codes: a clash is all but impossible, but costs one lookup.
        if db.query(ChatLinkCode.id).filter(ChatLinkCode.code_hash == hashed).first() is None:
            break
    row = ChatLinkCode(channel=CHANNEL, user_id=user.id, code_hash=hashed, created_at=now, expires_at=now + CODE_TTL)
    db.add(row)
    db.flush()
    return plain, row


def peek_code(db: Session, code) -> User | None:
    """The active account an open, not yet answered code belongs to, for
    the Telegram prompt ("Connect this Telegram to Name (@user), w***@...?").
    Changes nothing; None for anything else (wrong, used, expired,
    revoked, or not a code at all)."""
    canonical = normalize_code(code)
    if canonical is None:
        return None
    row = (
        db.query(ChatLinkCode.user_id)
        .filter(ChatLinkCode.code_hash == token_hash(canonical), ChatLinkCode.used_by_external_id.is_(None), *_open(utc_now()))
        .first()
    )
    if row is None:
        return None
    user = db.get(User, row.user_id)
    return user if user is not None and user.active else None


def telegram_said_yes(db: Session, code, external_id, username=None, name=None) -> User | None:
    """[Yes, that's me] in Telegram: in one statement, an open, unexpired,
    unanswered code of an active account becomes "waiting for Carlink",
    recording who tapped (Telegram user id, @username, name). Returns that
    account, or None if the code can't be used -- or this Telegram is
    connected already (to anyone: send /unlink first). Two taps, or two
    Telegrams, on one code: exactly one gets the account."""
    canonical = normalize_code(code)
    if canonical is None or external_id is None:
        return None
    ext = str(external_id)
    now = utc_now()
    already_linked = select(ChatLink.id).where(ChatLink.channel == CHANNEL, ChatLink.external_id == ext).exists()
    claimed = db.execute(
        update(ChatLinkCode)
        .where(
            ChatLinkCode.code_hash == token_hash(canonical),
            ChatLinkCode.used_by_external_id.is_(None),
            *_open(now),
            ChatLinkCode.user_id.in_(_active_user_ids()),
            ~already_linked,
        )
        .values(
            used_by_external_id=ext,
            used_by_username=_clean(username, 64),
            used_by_name=_clean(name, 128),
            waiting_since=now,
        )
        .returning(ChatLinkCode.user_id)
        # Decided by the database alone (times come back from SQLite
        # without a timezone; see auth/links.claim).
        .execution_options(synchronize_session=False)
    ).first()
    if claimed is None:
        return None
    return db.get(User, claimed.user_id)


def contest_code(db: Session, code, external_id) -> bool:
    """A code sent from this Telegram after a DIFFERENT Telegram has said
    Yes to it: more than one Telegram has the code, so the first to answer
    may be someone it leaked to, perhaps named to look like the owner
    (review M1). In one statement the code is revoked if it is still
    waiting and was answered by another Telegram -- neither can connect
    with it, and Settings stops showing the other one. True if revoked
    (with a history entry). The bot asks when peek_code finds nothing."""
    canonical = normalize_code(code)
    if canonical is None or external_id is None:
        return False
    ext = str(external_id)
    now = utc_now()
    revoked = db.execute(
        update(ChatLinkCode)
        .where(
            ChatLinkCode.code_hash == token_hash(canonical),
            ChatLinkCode.waiting_since.isnot(None),
            ChatLinkCode.used_by_external_id.isnot(None),
            ChatLinkCode.used_by_external_id != ext,
            *_open(now),
        )
        .values(revoked_at=now)
        .returning(ChatLinkCode.user_id, ChatLinkCode.used_by_external_id, ChatLinkCode.used_by_username, ChatLinkCode.used_by_name)
        .execution_options(synchronize_session=False)
    ).first()
    if revoked is None:
        return False
    owner = db.get(User, revoked.user_id)
    first = f"Telegram {telegram_label(revoked.used_by_username, revoked.used_by_name)}, id {revoked.used_by_external_id}"
    audit(db, None, "telegram_code_contested",
          detail=f"{owner.email if owner else revoked.user_id}: {first} had said Yes to a code that Telegram id {ext} "
                 "then sent too; the code was cancelled")
    return True


def withdraw_yes(db: Session, external_id) -> int:
    """Revokes the open codes this Telegram has said Yes to, so Settings
    can't connect it after all: on /unlink and [Cancel] in Telegram (review
    L2). Returns how many."""
    if external_id is None:
        return 0
    now = utc_now()
    result = db.execute(
        update(ChatLinkCode)
        .where(ChatLinkCode.used_by_external_id == str(external_id), *_open(now))
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


def confirm_from_web(db: Session, user, accept: bool) -> ChatLink | None:
    """The second side, in Settings, for this account's waiting code (the
    one a Telegram said Yes to). In one statement the code is taken --
    still waiting, unexpired, the account still active -- and then:

      accept=True   the link is made and returned. It replaces any other
                    Telegram this account had (history: telegram_relinked).
      accept=False  "Not me": the code is revoked; returns None.

    Raises ChatLinkError (409): nothing_waiting, code_expired, or
    telegram_in_use (that Telegram was connected to another account
    meanwhile; the code is then revoked). Commit after an error too, so a
    code that can't be used any more stops showing as waiting."""
    return confirm_from_web_with_id(db, user, accept)[0]


def confirm_from_web_with_id(db: Session, user, accept: bool) -> tuple[ChatLink | None, str]:
    """confirm_from_web, also returning the Telegram user id that had said
    Yes (for telling them the outcome)."""
    now = utc_now()
    waiting = (
        ChatLinkCode.user_id == user.id,
        ChatLinkCode.waiting_since.isnot(None),
        ChatLinkCode.used_by_external_id.isnot(None),
        *_open(now),
        ChatLinkCode.user_id.in_(_active_user_ids()),
    )
    claimed = db.execute(
        update(ChatLinkCode)
        .where(*waiting)
        .values(**({"linked_at": now} if accept else {"revoked_at": now}))
        .returning(ChatLinkCode.id, ChatLinkCode.used_by_external_id, ChatLinkCode.used_by_username, ChatLinkCode.used_by_name)
        .execution_options(synchronize_session=False)
    ).first()
    if claimed is None:
        # The UPDATE holds the write lock, so this read is current.
        stale = (
            db.query(ChatLinkCode.id)
            .filter(ChatLinkCode.channel == CHANNEL, ChatLinkCode.user_id == user.id,
                    ChatLinkCode.waiting_since.isnot(None), ChatLinkCode.linked_at.is_(None),
                    ChatLinkCode.revoked_at.is_(None))
            .first()
        )
        if stale is not None:
            raise ChatLinkError("code_expired", CODE_EXPIRED)
        raise ChatLinkError("nothing_waiting", NOTHING_WAITING)

    ext = claimed.used_by_external_id
    who = f"Telegram {telegram_label(claimed.used_by_username, claimed.used_by_name)}, id {ext}"
    if not accept:
        audit(db, user, "telegram_link_refused", detail=f"{user.email}: {who} wanted to connect; the account said Not me")
        return None, ext

    holder = db.query(ChatLink).filter(ChatLink.channel == CHANNEL, ChatLink.external_id == ext).first()
    if holder is not None and holder.user_id != user.id:
        db.execute(
            update(ChatLinkCode)
            .where(ChatLinkCode.id == claimed.id)
            .values(linked_at=None, revoked_at=now)
            .execution_options(synchronize_session=False)
        )
        raise ChatLinkError("telegram_in_use", TELEGRAM_TAKEN)
    if holder is not None:
        # This Telegram is this account's already: just its new names.
        holder.external_username = claimed.used_by_username
        holder.external_name = claimed.used_by_name
        db.flush()
        return holder, ext

    replaced = db.execute(
        delete(ChatLink)
        .where(ChatLink.channel == CHANNEL, ChatLink.user_id == user.id)
        .returning(ChatLink.external_id, ChatLink.external_username, ChatLink.external_name)
        .execution_options(synchronize_session=False)
    ).first()
    link = ChatLink(
        channel=CHANNEL,
        external_id=ext,
        user_id=user.id,
        external_username=claimed.used_by_username,
        external_name=claimed.used_by_name,
        linked_at=now,
    )
    db.add(link)
    db.flush()
    if replaced is not None:
        old = f"Telegram {telegram_label(replaced.external_username, replaced.external_name)}, id {replaced.external_id}"
        audit(db, user, "telegram_relinked", detail=f"{user.email}: {who}, replacing {old}")
    else:
        audit(db, user, "telegram_linked", detail=f"{user.email}: {who}")
    return link, ext


def cancel_codes(db: Session, user_id: str) -> list[str]:
    """Revokes this account's open codes (Settings' Cancel). Returns the
    Telegram user ids that had said Yes to one still in time, so they can
    be told."""
    now = utc_now()
    rows = db.execute(
        update(ChatLinkCode)
        .where(ChatLinkCode.channel == CHANNEL, ChatLinkCode.user_id == user_id,
               ChatLinkCode.linked_at.is_(None), ChatLinkCode.revoked_at.is_(None))
        .values(revoked_at=now)
        .returning(ChatLinkCode.used_by_external_id, ChatLinkCode.expires_at)
        .execution_options(synchronize_session=False)
    ).all()
    return [ext for ext, expires in rows if ext and as_utc(expires) > now]


# ------------------------------------------------------------------ links


def linked_user(db: Session, external_id, *, username=None, name=None) -> User | None:
    """The active account this Telegram user is connected to, or None (not
    connected, or the account is disabled). Ask on every update: it is the
    only check of who someone is.

    Writes last_used_at at most every TOUCH_EVERY -- and the @username and
    name when given and changed -- so commit afterwards (when the database
    is busy that write is skipped, never the answer)."""
    if external_id is None:
        return None
    ext = str(external_id)
    row = (
        db.query(ChatLink.id, ChatLink.user_id, ChatLink.last_used_at, ChatLink.external_username, ChatLink.external_name)
        .filter(ChatLink.channel == CHANNEL, ChatLink.external_id == ext)
        .first()
    )
    if row is None:
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.active:
        return None
    now = utc_now()
    changes = {}
    if row.last_used_at is None or now - as_utc(row.last_used_at) >= TOUCH_EVERY:
        changes["last_used_at"] = now
    for column, value, limit in (("external_username", username, 64), ("external_name", name, 128)):
        cleaned = _clean(value, limit)
        if cleaned and cleaned != getattr(row, column):
            changes[column] = cleaned
    if changes:
        try:
            db.execute(
                update(ChatLink)
                .where(ChatLink.id == row.id, ChatLink.user_id == row.user_id)
                .values(**changes)
                .execution_options(synchronize_session=False)
            )
        except OperationalError:
            logger.debug("Busy database: a chat link's last_used_at wasn't written this time")
    return user


def unlink(db: Session, *, user_id: str | None = None, external_id=None, actor, reason: str) -> bool:
    """Deletes a link, found by the Carlink account or by the Telegram user
    id, with a history entry (telegram_unlinked, "... (reason)"). False if
    there was none."""
    if (user_id is None) == (external_id is None):
        raise ValueError("unlink needs exactly one of user_id and external_id")
    which = ChatLink.user_id == user_id if user_id is not None else ChatLink.external_id == str(external_id)
    gone = db.execute(
        delete(ChatLink)
        .where(ChatLink.channel == CHANNEL, which)
        .returning(ChatLink.user_id, ChatLink.external_id, ChatLink.external_username, ChatLink.external_name)
        .execution_options(synchronize_session=False)
    ).first()
    if gone is None:
        return False
    owner = db.get(User, gone.user_id)
    who = f"Telegram {telegram_label(gone.external_username, gone.external_name)}, id {gone.external_id}"
    audit(db, actor, "telegram_unlinked", detail=f"{owner.email if owner else gone.user_id}: {who} ({reason})")
    return True


def end_chat_links(db: Session, user_id: str, actor, reason: str) -> bool:
    """Ends this account's Telegram link and open codes (a waiting one too):
    on disabling it, an admin's password reset, a reset link being used,
    and the person changing their own password. Enabling the account again
    doesn't bring the link back. True if a link was removed."""
    cancel_codes(db, user_id)
    return unlink(db, user_id=user_id, actor=actor, reason=reason)


def telegram_status(db: Session, user_id: str, plain_code: str | None = None) -> dict:
    """GET /auth/me/telegram:

        {connected, telegram_username, telegram_name, linked_at,
         code: {display, expires_at, deep_link, waiting: {username, name} | null} | null,
         bot_username}

    code is the open code, if any. display and deep_link hold the plain
    code only when plain_code is given -- in the response that made it
    (POST /auth/me/telegram/code); it isn't stored, so after that they are
    null. waiting is the Telegram that said Yes to it."""
    now = utc_now()
    link = db.query(ChatLink).filter(ChatLink.channel == CHANNEL, ChatLink.user_id == user_id).first()
    code = (
        db.query(ChatLinkCode)
        .filter(ChatLinkCode.user_id == user_id, *_open(now))
        .order_by(ChatLinkCode.created_at.desc())
        .first()
    )
    bot = bot_username(db)
    code_dict = None
    if code is not None:
        canonical = normalize_code(plain_code) if plain_code else None
        plain = canonical if canonical and token_hash(canonical) == code.code_hash else None
        code_dict = {
            "display": display_code(plain) if plain else None,
            "expires_at": iso_utc(code.expires_at),
            "deep_link": deep_link(bot, plain) if plain else None,
            "waiting": (
                {"username": code.used_by_username, "name": code.used_by_name}
                if code.waiting_since is not None and code.used_by_external_id else None
            ),
        }
    return {
        "connected": link is not None,
        "telegram_username": link.external_username if link else None,
        "telegram_name": link.external_name if link else None,
        "linked_at": iso_utc(link.linked_at) if link else None,
        "code": code_dict,
        "bot_username": bot,
    }


def links_by_user(db: Session, user_ids) -> dict[str, dict]:
    """{user id: {username, name, linked_at}} for the accounts with a
    Telegram connected -- one query for all of them (GET /users)."""
    ids = list(user_ids)
    if not ids:
        return {}
    rows = (
        db.query(ChatLink.user_id, ChatLink.external_username, ChatLink.external_name, ChatLink.linked_at)
        .filter(ChatLink.channel == CHANNEL, ChatLink.user_id.in_(ids))
        .all()
    )
    return {
        r.user_id: {"username": r.external_username, "name": r.external_name, "linked_at": iso_utc(r.linked_at)}
        for r in rows
    }


def accounts_connected(db: Session) -> int:
    """How many active accounts have a Telegram connected (System info)."""
    return (
        db.query(func.count(ChatLink.id))
        .filter(ChatLink.channel == CHANNEL, ChatLink.user_id.in_(_active_user_ids()))
        .scalar()
    ) or 0


def telegram_sender(db: Session, report) -> str | None:
    """The account whose connected Telegram sent this case (from a private
    chat, where the chat id is the Telegram user id), for suggesting an
    owner. Earlier Telegram cases are never claimed by the person (D5): an
    admin assigns them, with this as the hint."""
    if getattr(report, "channel", None) != CHANNEL or not getattr(report, "reporter_chat_id", None):
        return None
    return (
        db.query(ChatLink.user_id)
        .filter(ChatLink.channel == CHANNEL, ChatLink.external_id == str(report.reporter_chat_id))
        .scalar()
    )


def sweep_codes(db: Session, now: datetime | None = None) -> int:
    """Deletes codes that expired more than KEEP_EXPIRED_CODES ago,
    whatever became of them (app/intake/cleanup.py). Returns how many."""
    now = now or utc_now()
    result = db.execute(
        delete(ChatLinkCode)
        .where(ChatLinkCode.expires_at < now - KEEP_EXPIRED_CODES)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


# ------------------------------------------------------------ wrong codes


class WrongCodeLimiter:
    """Wrong codes sent from Telegram, kept in the bot's memory.

    Per Telegram user id: MAX_WRONG wrong in a row blocks that person for
    BLOCK seconds (a code that works resets the count). Across everyone:
    more than ALARM_PER_HOUR wrong codes within an hour writes one WARNING
    line, at most one an hour -- someone guessing from many Telegram
    accounts. 31^8 codes make guessing hopeless anyway; this keeps it so.

    Holds at most MAX_ENTRIES people, dropping the longest-quiet first,
    rather than forgetting everyone at once. Never sees a code. Thread-safe.
    The clock is time.monotonic unless a test passes its own."""

    MAX_WRONG = 5
    BLOCK = 15 * 60
    ALARM_PER_HOUR = 50
    MAX_ENTRIES = 5000

    def __init__(self, clock=time.monotonic, *, max_entries: int | None = None):
        self._clock = clock
        self._max = max_entries or self.MAX_ENTRIES
        self._lock = threading.Lock()
        # Telegram user id -> [wrong in a row, blocked until]
        self._people: OrderedDict[str, list] = OrderedDict()
        self._recent: deque = deque()
        self._alarmed_at: float | None = None

    def blocked_for(self, external_id) -> float | None:
        """Seconds this Telegram user is still blocked for, or None."""
        now = self._clock()
        with self._lock:
            entry = self._people.get(str(external_id))
            if entry and entry[1] > now:
                return entry[1] - now
            return None

    def wrong(self, external_id) -> int:
        """Records a wrong code. Returns the tries left before the block;
        0 means this one blocked them for BLOCK seconds."""
        now = self._clock()
        key = str(external_id)
        with self._lock:
            entry = self._people.pop(key, None) or [0, 0.0]
            self._people[key] = entry  # now the most recent
            while len(self._people) > self._max:
                self._people.popitem(last=False)
            entry[0] += 1
            left = self.MAX_WRONG - entry[0]
            if left <= 0:
                entry[0], entry[1], left = 0, now + self.BLOCK, 0
            self._recent.append(now)
            while self._recent and now - self._recent[0] > 3600:
                self._recent.popleft()
            alarm = len(self._recent) > self.ALARM_PER_HOUR and (
                self._alarmed_at is None or now - self._alarmed_at >= 3600
            )
            if alarm:
                self._alarmed_at = now
            count = len(self._recent)
        if alarm:
            logger.warning(
                "%d wrong Telegram link codes in the last hour, from everyone together: someone may be guessing codes",
                count,
            )
        return left

    def right(self, external_id) -> None:
        """A code worked: the count of wrong ones in a row starts again."""
        with self._lock:
            entry = self._people.get(str(external_id))
            if entry:
                entry[0] = 0

    def reset(self) -> None:
        """Tests."""
        with self._lock:
            self._people.clear()
            self._recent.clear()
            self._alarmed_at = None


# The bot's limiter (one process, so one instance).
wrong_codes = WrongCodeLimiter()
