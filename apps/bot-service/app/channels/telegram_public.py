"""The public report form (P6): a car accident or damage report from someone
with no Carlink account, sent through the same bot. channels/telegram.py
routes every update from a Telegram that isn't connected here, after its
gate (review H1), under the chat's lock; this module never sees staff.

    anything (no form)  -> the welcome and consent, at most every 5 s;
                           a blocked sender: "can't take reports" once a day
    [I agree, start]    -> the caps (intake/public.check_can_accept, open
                           forms, this sender's day), then steps 1-6:
    (1/6) name, role  (2/6) phone: a contact shared from this Telegram
    (3/6) plate  (4/6) when and where  (5/6) what happened  (6/6) photos
    review              -> [Submit] [Edit something] [Cancel]
    Submit              -> intake/public.save_submission; the reply says
                           the reference only

What is kept, and where:
  - before consent, after [No thanks] or /cancel: nothing. Photos sent
    before consent are never fetched.
  - the form: in memory (Session.public) and its photos as tg-<uuid> temp
    files, until Submit, cancel or 24 hours idle.
  - after Submit: the intake draft save_submission writes (it copies the
    photos; the temp files are deleted here).

Photos: Telegram photos only, never files (review H4: no raw image a
stranger chose is parsed in this process). At most MAX_PHOTOS, the largest
size under MAX_PHOTO_BYTES, MAX_REPORT_BYTES a form, MAX_TEMP_BYTES for
all forms together; a repeat (file_unique_id) isn't fetched; 3 downloads at
a time; 30 photos an hour a sender, 300 an hour from everyone; and
public.DISK_HEADROOM_GB more free disk than staff need (review M1).

Replies: plain text (no parse_mode), from one budget for every public chat
(REPLIES_PER_SECOND, burst REPLY_BURST; review M3), an album answered once,
2 s after its last photo. The only link is the privacy notice. When the
admin setting public_contact_phone is empty, no reply says "call us".

Buttons are pub:<nonce>:<action>[:arg] -- untrusted: the nonce must be the
form's (or the welcome's, or the /withdraw question's), and /withdraw's
pub:<nonce>:wd:<n> is an index into this sender's own reports, looked up
here; public.withdraw matches the Telegram id again.

No AI, no PDF: nothing here imports the pipeline, the AI or rendering.
Logs: Telegram ids, report ids and references, never what anyone typed.
"""
import asyncio
import functools
import logging
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)

from app.api.limits import _RenderBudget
from app.auth.chat_links import clean_name, looks_like_code
from app.auth.core import utc_now
from app.channels.tg_common import (
    MAX_ENTRIES,
    NO_PREVIEW,
    SESSION_MAX_IDLE_SECONDS,
    _discard_temp_files,
    _drop,
    _first_in,
    _forwarded,
    _keep_temp_files,
    _lock_for,
    _new_temp_path,
    _reply_target,
    _telegram_name,
    _told_at,
)
from app.config import settings
from app.conversation.state import PublicForm, PublicStep, find_session, get_session, public_sessions
from app.intake import public
from app.intake.photos import remove_intake_dir
from app.reports.db import SessionLocal
from app.storage.files import disk_space_low

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "pub:"
# The welcome, per chat (the album that comes with it gets one); a blocked
# sender's notice; anything else they can't send (video, voice...).
WELCOME_EVERY_SECONDS = 5
BLOCKED_EVERY_SECONDS = 24 * 60 * 60
OTHER_EVERY_SECONDS = 10 * 60
FILE_EVERY_SECONDS = 10
# Forms open at once, from everyone (each holds up to MAX_REPORT_BYTES of
# temp files, and all of them together at most MAX_TEMP_BYTES).
MAX_FORMS = 300
MAX_TEMP_BYTES = 1024**3
MAX_DOWNLOADS = 3
PHOTOS_PER_SENDER_HOUR = 30
PHOTOS_PER_HOUR = 300
# Messages to the public, all chats together; a reply the form needs waits
# up to REPLY_WAIT_SECONDS for its turn, any other is skipped.
REPLIES_PER_SECOND = 8
REPLY_BURST = 20
REPLY_WAIT_SECONDS = 10
ALBUM_SECONDS = 2
# A /withdraw question's buttons work this long.
OFFER_SECONDS = 10 * 60
# The contact line (an admin setting) is read again after this long.
CONTACT_SECONDS = 60
MIN_STATEMENT = 5
MIN_NAME_LETTERS = 2


# ------------------------------------------------------------- the texts


def _privacy_url() -> str:
    return f"{settings.public_base_url.rstrip('/')}/privacy#public-reports"


def _consent_text(after_photo: bool) -> str:
    return (
        "Hello! This is Carlink, vehicle damage surveyors. You can send us a car accident or damage report here "
        "(about 3 minutes). We'll ask for your name, your phone number (shared from this Telegram account), the car "
        "plate, what happened and photos.\n\n"
        "We use these only to handle your report: to contact you, assess the damage, and deal with the insurer, "
        "workshop or others involved in the claim. Never for marketing.\n"
        "• Our staff may use Google's Gemini AI, which may process data outside Singapore, to read your photos and "
        "what you write. Messages here also pass through Telegram.\n"
        "• Reports we haven't taken up within 30 days are deleted. You can delete yours before then with /withdraw.\n"
        "• Please don't send NRIC photos, and only include other people's details if the report needs them.\n\n"
        f"Full notice and contact: {_privacy_url()}\n\n"
        + ("(I didn't keep the photos you sent. You can send them again in step 6.)\n\n" if after_photo else "")
        + "Do you agree?"
    )


HELP = (
    "I can send a car accident or damage report to Carlink.\n"
    "/start: begin (or start again)\n"
    "/cancel: stop and delete what you've sent so far\n"
    "/withdraw: take back a report you've sent\n"
    "Carlink staff: connect your account with /link."
)
IN_PROGRESS = "You have a report in progress."
CANCELLED = "Stopped. Nothing was sent, and what you'd sent so far has been deleted. Send /start to begin again."
NOTHING_TO_CANCEL = "Nothing to cancel. Send /start to send us a report, or /withdraw to take back one you've sent."
FINISH_FIRST = "Finish or /cancel this report first."
EXPIRED = (
    "Your unfinished report was closed after 24 hours, and what you'd sent was deleted. Send /start to begin again."
)
# A draft from when this Telegram was connected (P5), found after its link
# ended: thrown away, never sent as a public report.
LINK_ENDED = (
    "This Telegram isn't connected to a Carlink account any more, so the draft you had open was discarded."
)
CODE_HINT = "If that was a Carlink staff link code, send it like this: /link K7M4-QX9P"
TYPE_IT = "Please type this yourself."
BAD_NAME = "Please type your name, e.g. Tan Ah Kow."
TYPED_NUMBER = (
    "Sorry, I can't take a typed number. Please tap \"Share my phone number\" below, so we know the number is "
    "really yours."
)
NOT_YOUR_CARD = (
    "That contact card wasn't shared from this Telegram account. Please tap \"Share my phone number\" below to "
    "send your own."
)
BAD_NUMBER = "I couldn't read a phone number from that. Please tap \"Share my phone number\" below."
BAD_PLATE = "That doesn't look like a plate number. Please type it like SBA1234A, or tap Not sure."
LONG_WHEN = f"Please keep it to {public.MAX_WHEN_WHERE} characters or fewer."
SHORT_STATEMENT = "Please tell us a little more (a sentence is fine)."
LONG_STATEMENT = f"That's over {public.MAX_STATEMENT} characters. Please shorten it a little."
NO_ROOM = f"That doesn't fit: what happened can be up to {public.MAX_STATEMENT} characters."
ADDED = "Added to what happened. Send more photos, or tap Done."
NO_PHOTOS = "You haven't sent any photos. Photos help us a lot."
FILES_REFUSED = (
    "Sorry, I can only take photos sent as photos, not files. Please send it again as a photo "
    "(in Telegram: choose Photo or Gallery, not File)."
)
OTHER_REFUSED = "Sorry, I can only take text and photos here, not videos, voice messages, stickers or files."
NOT_NEEDED = "I don't need that at this step."
BUTTON_EXPIRED = "That button has expired. Send /start to begin."
TOO_BIG = "One photo was too big to take."
AT_CAP = f"That's {public.MAX_PHOTOS} photos, the most I can take."
REPORT_FULL = "The photos for this report are as big as I can take."
NO_SPACE = "I can't take more photos right now, sorry."
TOO_MANY_PHOTOS = "You've sent a lot of photos in the last hour, so I didn't take the last ones."
NOT_RECEIVED = "One photo didn't come through. Please send it again."
NOT_SENT = "Sorry, something went wrong and your report wasn't sent. Please tap Submit again in a minute."
NO_WITHDRAW = "There's no report from this Telegram that can be taken back."
KEPT = "OK, nothing was deleted."
SLOW_DOWN = "Please slow down: I couldn't take all of that. Wait a little, then send the rest."


def _call_us(contact: str, words: str = "You can also call us on") -> str:
    """" You can also call us on 6123 4567." -- or nothing, when the admin
    hasn't set a public contact line (p6_decisions §1)."""
    return f" {words} {contact}." if contact else ""


def _refusal_text(code: str, contact: str) -> str:
    """Why a report can't be taken (public.PublicRefused.code), never
    anything about anyone else's reports."""
    if code == "blocked":
        return "Sorry, we can't take reports from this account here." + _call_us(contact, "Please call us on")
    if code == "linked":
        return "This Telegram is connected to a Carlink account now, so this report wasn't sent from here. Send /new to file it as staff."
    if code == "sender_limit":
        return (
            "Sorry, we can't take new reports from this Telegram account right now: you've already sent us "
            f"{public.MAX_OPEN_PER_SENDER} we haven't finished with, or {public.MAX_PER_SENDER_DAY} in the last day. "
            "Please try again later." + _call_us(contact)
        )
    if code == "phone_limit":
        return (
            "Sorry, we can't take new reports for this phone number right now: it already has "
            f"{public.MAX_OPEN_PER_SENDER} we haven't finished with, or {public.MAX_PER_SENDER_DAY} in the last day. "
            "Please try again later." + _call_us(contact)
        )
    # inbox_full, hourly_full, disk_low, or too many forms open here.
    return "Sorry, we can't take new reports here right now. Please try again later." + _call_us(contact)


def _sent_text(reference: str) -> str:
    # The reference only: no link, no name or number (brief §2.4).
    return (
        f"Thank you. Your report has been sent to Carlink. Your reference number is {reference}: please keep it. "
        "A Carlink staff member will contact you. If you sent this by mistake, send /withdraw."
    )


# ----------------------------------------------------------- budgets


class _ReplyBudget:
    """Messages to public chats, all together (review M3: the bot's
    Telegram send limit is shared with staff). A token bucket: rate a
    second, up to burst at once. Used only on the event loop."""

    def __init__(self, rate: float, burst: int):
        self.rate = rate
        self.burst = burst
        self.reset()

    def reset(self) -> None:
        self._tokens = float(self.burst)
        self._at = time.monotonic()

    def try_take(self) -> bool:
        now = time.monotonic()
        self._tokens = min(self.burst, self._tokens + (now - self._at) * self.rate)
        self._at = now
        if self._tokens < 1:
            return False
        self._tokens -= 1
        return True

    async def take(self, wait: float) -> bool:
        """try_take, waiting up to wait seconds for a token."""
        end = time.monotonic() + wait
        while not self.try_take():
            left = end - time.monotonic()
            if left <= 0:
                return False
            await asyncio.sleep(min(left, max((1 - self._tokens) / self.rate, 0.01)))
        return True


class _PhotoBudget(_RenderBudget):
    """limits._RenderBudget -- this bot's own, never limits.photo_uploads --
    that forgets senders quiet for its window once it holds MAX_ENTRIES."""

    def take(self, user_id: str, count: int = 1) -> bool:
        if len(self._times) >= MAX_ENTRIES:
            now = time.monotonic()
            with self._lock:
                for key in [k for k, t in self._times.items() if not t or now - t[-1] >= self.window]:
                    del self._times[key]
        return super().take(user_id, count)


replies = _ReplyBudget(REPLIES_PER_SECOND, REPLY_BURST)
sender_photos = _PhotoBudget(PHOTOS_PER_SENDER_HOUR, 60 * 60)
all_photos = _PhotoBudget(PHOTOS_PER_HOUR, 60 * 60)
# Reports sent per Telegram account in 24 hours, kept here too: the
# database forgets one that is withdrawn or discarded.
sent_today = _PhotoBudget(public.MAX_PER_SENDER_DAY, 24 * 60 * 60)
ALL = "all"

# Saves (Pillow) and every other database call here: their own threads,
# so strangers never queue in front of staff drafts on the default ones.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="public")
_slots: tuple | None = None  # (event loop, the download semaphore)


async def _in_thread(fn, *args, **kwargs):
    return await asyncio.get_running_loop().run_in_executor(_executor, functools.partial(fn, *args, **kwargs))


def _download_slots() -> asyncio.Semaphore:
    global _slots
    loop = asyncio.get_running_loop()
    if _slots is None or _slots[0] is not loop:
        _slots = (loop, asyncio.Semaphore(MAX_DOWNLOADS))
    return _slots[1]


def _temp_bytes() -> int:
    return sum(s.public.photo_bytes for _, s in public_sessions())


# ------------------------------------------------------ database (threads)


def _blocked_and_contact(ext: str) -> tuple[bool, str]:
    db = SessionLocal()
    try:
        return public.is_blocked(db, ext), public.public_contact_phone(db)
    finally:
        db.close()


def _read_contact() -> str:
    db = SessionLocal()
    try:
        return public.public_contact_phone(db)
    finally:
        db.close()


def _refusal(ext: str, phone: str | None = None) -> str | None:
    db = SessionLocal()
    try:
        public.check_can_accept(db, ext, phone)
        return None
    except public.PublicRefused as exc:
        return exc.code
    finally:
        db.close()


def _open_reports(ext: str) -> list[dict]:
    db = SessionLocal()
    try:
        return public.open_for_sender(db, ext)
    finally:
        db.close()


def _withdraw_one(report_id: str, ext: str) -> str:
    db = SessionLocal()
    try:
        outcome = public.withdraw(db, report_id, ext)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    if outcome == "deleted":
        remove_intake_dir(report_id)
    return outcome


_contact = {"value": "", "at": None}


async def _contact_line() -> str:
    """The admin's public contact line, "" when unset (cached a minute)."""
    now = time.monotonic()
    if _contact["at"] is None or now - _contact["at"] > CONTACT_SECONDS:
        _contact["value"] = await _in_thread(_read_contact)
        _contact["at"] = now
    return _contact["value"]


# --------------------------------------------------------------- replies


async def _say(target, text: str, *, essential: bool = True, **kw) -> bool:
    """A reply from the public budget: one the form needs waits for its
    turn, any other is skipped when the budget is spent. Plain text."""
    if target is None:
        return False
    if not await replies.take(REPLY_WAIT_SECONDS if essential else 0):
        logger.info("Public reply budget spent: a reply to chat %s wasn't sent", getattr(target, "chat_id", "?"))
        return False
    await target.reply_text(text, **kw)
    return True


async def answer_tap(query, text: str | None = None) -> None:
    """Stops the button's spinner, if the budget allows (it stops by itself
    otherwise)."""
    if not replies.try_take():
        return
    try:
        await query.answer(text)
    except Exception:
        pass  # too old to answer: only tidying


def _buttons(form_nonce: str, *rows) -> InlineKeyboardMarkup:
    """rows of (label, action) -> pub:<nonce>:<action> buttons."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"{CALLBACK_PREFIX}{form_nonce}:{action}") for label, action in row]
        for row in rows
    ])


def _phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("Share my phone number", request_contact=True)], [KeyboardButton("Cancel")]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )


def _review_text(form: PublicForm, photos: int) -> str:
    return (
        "Please check your report:\n\n"
        f"Name: {form.name} ({public.ROLES.get(form.role, public.ROLES['other'])})\n"
        f"Phone: {public.phone_display(form.phone or '')}\n"
        f"Plate: {form.plate or 'Not sure'}\n"
        f"When & where: {form.when_where or 'Not given'}\n"
        f"What happened: {form.statement}\n"
        f"Photos: {photos}\n\n"
        "Is everything correct?"
    )


def _question(form: PublicForm, photos: int) -> tuple[str, object]:
    """The current step's question and its buttons."""
    n = form.nonce
    step = form.step
    if step == PublicStep.NAME:
        return "(1/6) What's your name?", None
    if step == PublicStep.ROLE:
        roles = [(label, f"role:{key}") for key, label in public.ROLES.items()]
        return f"Thanks, {form.name}. Are you…", _buttons(n, roles[:2], roles[2:])
    if step == PublicStep.PHONE:
        return (
            "(2/6) Please tap \"Share my phone number\" below so we can call you back. It sends the number of this "
            "Telegram account. (No button? Open this chat in the Telegram app on your phone.)",
            _phone_keyboard(),
        )
    if step == PublicStep.PLATE:
        return "(3/6) What's the car's plate number? e.g. SBA1234A", _buttons(n, [("Not sure", "notsure")])
    if step == PublicStep.WHEN_WHERE:
        return (
            "(4/6) When and where did it happen? e.g. \"Yesterday about 6pm, PIE towards Changi near exit 8\". "
            "You can also send a location pin.",
            _buttons(n, [("Skip", "skip")]),
        )
    if step == PublicStep.WHAT:
        return (
            "(5/6) What happened? Tell us in your own words, e.g. \"A taxi hit my rear bumper at a red light.\" "
            "You can also add the other driver's plate, your insurer or your workshop.",
            None,
        )
    if step == PublicStep.PHOTOS:
        if form.editing and photos:
            return (
                f"(6/6) You have {photos} photo{'' if photos == 1 else 's'}. Send more, or tap Done. "
                "To send them all again, tap Remove all photos.",
                _buttons(n, [("Done", "done")], [("Remove all photos", "clearphotos")]),
            )
        return (
            f"(6/6) Please send photos of the damage, up to {public.MAX_PHOTOS}: close up and from further away, the "
            "whole car, the plate, and the other car if there was one. Please send them as photos, not files, and "
            "don't send photos of NRIC cards. Tap Done when you've sent them all.",
            _buttons(n, [("Done", "done")]),
        )
    if step == PublicStep.EDIT_MENU:
        return "What would you like to change?", _buttons(
            n,
            [("Name", "ed:name"), ("Phone", "ed:phone"), ("Plate", "ed:plate")],
            [("When & where", "ed:when"), ("What happened", "ed:what")],
            [("Photos", "ed:photos"), ("Back", "back")],
        )
    return _review_text(form, photos), _buttons(
        n, [("Submit", f"submit:{form.rev}")], [("Edit something", "edit"), ("Cancel", "cancel")]
    )


async def _ask(target, session, before: str = "") -> None:
    form = session.public
    text, markup = _question(form, len(form.photo_ids))
    await _say(target, f"{before}\n\n{text}" if before else text, reply_markup=markup)


def _go(form: PublicForm, step: PublicStep) -> None:
    """To step, or back to the review if this answer was an edit."""
    _cancel_ack(form)
    form.rev += 1
    if form.editing and step not in (PublicStep.ROLE,):
        step = PublicStep.REVIEW
    form.step = step
    if step == PublicStep.REVIEW:
        form.editing = False


def _cancel_ack(form: PublicForm) -> None:
    if form.ack_task is not None and not form.ack_task.done():
        form.ack_task.cancel()
    form.ack_task = None


# ------------------------------------------------------- the welcome


@dataclass
class _Offer:
    """A /withdraw question: the sender's own reports, by index (the
    buttons carry only the index), in memory for OFFER_SECONDS."""
    nonce: str
    ids: list[str]
    refs: list[str]
    taken: list[bool]
    sent: list[str]
    at: float


# Telegram user id -> (the welcome's nonce, when) and their /withdraw question.
_welcomes: dict[str, tuple[str, float]] = {}
_offers: dict[str, _Offer] = {}


def _remember(table: dict, key: str, value) -> None:
    table.pop(key, None)
    while len(table) >= MAX_ENTRIES:
        del table[next(iter(table))]
    table[key] = value


async def _welcome(update: Update, chat_id: str, ext: str, *, after_photo: bool = False) -> None:
    """The welcome and consent, at most every WELCOME_EVERY_SECONDS a chat;
    a blocked sender gets "can't take reports" instead, once a day."""
    if not _first_in(f"pub-welcome:{chat_id}", WELCOME_EVERY_SECONDS):
        return
    blocked, contact = await _in_thread(_blocked_and_contact, ext)
    target = _reply_target(update)
    if blocked:
        if _first_in(f"pub-blocked:{chat_id}", BLOCKED_EVERY_SECONDS):
            logger.info("Telegram user %s is blocked from sending public reports: told so", ext)
            await _say(target, _refusal_text("blocked", contact))
        return
    nonce = secrets.token_hex(4)
    _remember(_welcomes, ext, (nonce, time.monotonic()))
    await _say(
        target,
        _consent_text(after_photo),
        reply_markup=_buttons(nonce, [("I agree, start", "agree"), ("No thanks", "no")]),
        link_preview_options=NO_PREVIEW,
    )


def _room_for_a_form() -> bool:
    """Under MAX_FORMS open, once forms idle past a day are dropped."""
    forms = public_sessions()
    if len(forms) < MAX_FORMS:
        return True
    now = time.monotonic()
    for chat_id, session in forms:
        if now - session.last_active > SESSION_MAX_IDLE_SECONDS:
            _drop(chat_id)
    return len(public_sessions()) < MAX_FORMS


async def _agree(update: Update, chat_id: str, ext: str) -> None:
    target = _reply_target(update)
    code = None
    if not _room_for_a_form():
        code = "inbox_full"
    elif not sent_today.take(ext):
        code = "sender_limit"
    else:
        sent_today.refund(ext)  # only looked: counted at Submit
        code = await _in_thread(_refusal, ext)
    if code:
        logger.info("Telegram user %s can't start a public report now (%s)", ext, code)
        await _say(target, _refusal_text(code, await _contact_line()))
        return
    session = get_session(chat_id)
    session.public = PublicForm(nonce=secrets.token_hex(4), consent_at=utc_now())
    session.last_active = time.monotonic()
    logger.info("Telegram user %s agreed: public report form started", ext)
    await _ask(target, session)


# ----------------------------------------------------------- each update


async def _begin(update: Update):
    """(chat id, Telegram id, the chat's session or None, stale). Under the
    chat lock, before anything else: a staff draft left from when this
    Telegram was connected is thrown away (stale: the update was meant for
    it), and so is a form idle past a day."""
    chat_id = str(update.effective_chat.id)
    ext = str(update.effective_user.id)
    session = find_session(chat_id)
    target = _reply_target(update)
    if session is not None and session.public is None:
        had_draft = bool(session.photo_paths) or session.draft is not None
        _drop(chat_id)
        if had_draft:
            logger.info("Telegram user %s isn't connected any more: their staff draft was discarded", ext)
            await _say(target, LINK_ENDED)
            return chat_id, ext, None, True
        session = None
    if session is not None and time.monotonic() - session.last_active > SESSION_MAX_IDLE_SECONDS:
        _drop(chat_id)
        await _say(target, EXPIRED)
        return chat_id, ext, None, True
    if session is not None:
        session.last_active = time.monotonic()
        _keep_temp_files(session)
    return chat_id, ext, session, False


async def on_start(update: Update, context=None) -> None:
    chat_id, ext, session, stale = await _begin(update)
    if stale:
        return
    if session is None:
        await _welcome(update, chat_id, ext)
        return
    await _say(_reply_target(update), IN_PROGRESS,
               reply_markup=_buttons(session.public.nonce, [("Continue", "continue"), ("Start again", "restart")]))


async def on_help(update: Update, context=None) -> None:
    chat_id, ext, session, stale = await _begin(update)
    if stale:
        return
    target = _reply_target(update)
    if session is None:
        await _say(target, HELP)
        return
    await _ask(target, session, HELP)


async def on_cancel(update: Update, context=None) -> None:
    chat_id, ext, session, stale = await _begin(update)
    if stale:
        return
    await _cancel(update, chat_id, ext, session)


async def _cancel(update: Update, chat_id: str, ext: str, session) -> None:
    target = _reply_target(update)
    if session is None:
        await _say(target, NOTHING_TO_CANCEL)
        return
    _drop(chat_id)
    logger.info("Telegram user %s cancelled their public report form", ext)
    await _say(target, CANCELLED, reply_markup=ReplyKeyboardRemove())


async def on_text(update: Update, context=None) -> None:
    chat_id, ext, session, stale = await _begin(update)
    if stale:
        return
    if session is None:
        await _welcome(update, chat_id, ext)
        return
    message = update.message
    form = session.public
    target = _reply_target(update)
    text = (message.text or "").strip()
    forwarded = _forwarded(message)
    if forwarded:
        form.forwarded_count += 1
    if text.lower() == "cancel":  # the phone step's keyboard button
        await _cancel(update, chat_id, ext, session)
        return
    if looks_like_code(text):
        # Never kept: a link code belongs in /link (agent6 M6).
        await _ask(target, session, CODE_HINT)
        return
    step = form.step
    if step in (PublicStep.NAME, PublicStep.PLATE, PublicStep.WHEN_WHERE) and forwarded:
        await _ask(target, session, TYPE_IT)
        return
    if step == PublicStep.NAME:
        name = clean_name(text, public.MAX_NAME)
        lowered = text.lower()
        if (
            not name
            or len(text) > public.MAX_NAME
            or sum(ch.isalpha() for ch in name) < MIN_NAME_LETTERS
            or any(bad in lowered for bad in ("http", "t.me", "@", "＠"))
        ):
            await _say(target, BAD_NAME)
            return
        form.name = name
        _go(form, PublicStep.ROLE)
        await _ask(target, session)
    elif step == PublicStep.PHONE:
        await _say(target, TYPED_NUMBER, reply_markup=_phone_keyboard())
    elif step == PublicStep.PLATE:
        if text.lower().replace(" ", "") in ("notsure", "dontknow", "unknown"):
            form.plate, form.plate_unknown = None, True
        else:
            plate = public.clean_plate(text)
            if plate is None:
                await _say(target, BAD_PLATE, reply_markup=_buttons(form.nonce, [("Not sure", "notsure")]))
                return
            form.plate, form.plate_unknown = plate, False
        _go(form, PublicStep.WHEN_WHERE)
        await _ask(target, session)
    elif step == PublicStep.WHEN_WHERE:
        if len(text) > public.MAX_WHEN_WHERE:
            await _say(target, LONG_WHEN)
            return
        form.when_where = text if text.lower() != "skip" else None
        _go(form, PublicStep.WHAT)
        await _ask(target, session)
    elif step == PublicStep.WHAT:
        if len(text) < MIN_STATEMENT:
            await _say(target, SHORT_STATEMENT)
            return
        if len(text) > public.MAX_STATEMENT:
            await _say(target, LONG_STATEMENT)
            return
        form.statement = text
        _go(form, PublicStep.PHOTOS)
        await _ask(target, session)
    elif step == PublicStep.PHOTOS:
        # More of what happened, sent while adding photos.
        joined = f"{form.statement}\n{text}" if form.statement else text
        if len(joined) > public.MAX_STATEMENT:
            await _say(target, NO_ROOM)
            return
        form.statement = joined
        form.rev += 1
        await _say(target, ADDED, reply_markup=_buttons(form.nonce, [("Done", "done")]))
    else:  # ROLE, the review, the edit menu: their buttons
        await _ask(target, session)


async def on_contact(update: Update, context=None) -> None:
    """Step 2: a contact card, taken only when it is this Telegram's own
    number, shared from this account -- not someone else's card, not
    forwarded, not via a bot, not a business message, not +888 -- whatever
    its vCard says (ignored)."""
    chat_id, ext, session, stale = await _begin(update)
    if stale:
        return
    if session is None:
        await _welcome(update, chat_id, ext)
        return
    message = update.message
    form = session.public
    target = _reply_target(update)
    forwarded = _forwarded(message)
    if forwarded:
        form.forwarded_count += 1
    if form.step != PublicStep.PHONE:
        await _ask(target, session, NOT_NEEDED)
        return
    card = message.contact
    own = (
        card is not None
        and card.user_id is not None
        and card.user_id == update.effective_user.id
        and not forwarded
        and getattr(message, "via_bot", None) is None
        and not getattr(message, "business_connection_id", None)
    )
    if not own:
        await _say(target, NOT_YOUR_CARD, reply_markup=_phone_keyboard())
        return
    phone = public.normalize_phone(card.phone_number)
    if phone is None:
        await _say(target, BAD_NUMBER, reply_markup=_phone_keyboard())
        return
    if public.is_anonymous_number(phone):
        contact = await _contact_line()
        await _say(
            target,
            "That's an anonymous +888 number, which we can't call back. Please use a Telegram account with a normal "
            "phone number" + (f", or call us on {contact}." if contact else "."),
            reply_markup=_phone_keyboard(),
        )
        return
    # The caps for this number, now that it is known (and again at Submit).
    code = await _in_thread(_refusal, ext, phone)
    if code:
        _drop(chat_id)
        logger.info("Telegram user %s: public report form ended at the phone step (%s)", ext, code)
        await _say(target, _refusal_text(code, await _contact_line()), reply_markup=ReplyKeyboardRemove())
        return
    form.phone = phone
    await _say(target, f"Thanks, got {public.phone_display(phone)}.", reply_markup=ReplyKeyboardRemove())
    _go(form, PublicStep.PLATE)
    await _ask(target, session)


async def on_location(update: Update, context=None) -> None:
    """Step 4 also takes a location pin."""
    chat_id, ext, session, stale = await _begin(update)
    if stale:
        return
    if session is None:
        await _welcome(update, chat_id, ext)
        return
    form = session.public
    target = _reply_target(update)
    if _forwarded(update.message):
        form.forwarded_count += 1
    place = update.message.location
    if form.step != PublicStep.WHEN_WHERE or place is None:
        await _ask(target, session, NOT_NEEDED)
        return
    form.when_where = f"Location pin: {place.latitude:.5f}, {place.longitude:.5f}"
    _go(form, PublicStep.WHAT)
    await _ask(target, session)


async def on_document(update: Update, context=None) -> None:
    """Files are never taken, not even images (review H4)."""
    chat_id, ext, session, stale = await _begin(update)
    if stale:
        return
    if session is None:
        await _welcome(update, chat_id, ext, after_photo=True)
        return
    if _first_in(f"pub-file:{chat_id}", FILE_EVERY_SECONDS):
        await _say(_reply_target(update), FILES_REFUSED)


async def on_other(update: Update, context=None) -> None:
    """Video, voice, stickers, polls... and commands this bot doesn't have."""
    chat_id, ext, session, stale = await _begin(update)
    if stale:
        return
    message = update.message
    if (message.text or "").startswith("/"):
        if _first_in(f"pub-other:{chat_id}", FILE_EVERY_SECONDS):
            await _say(_reply_target(update), HELP)
        return
    if session is None:
        await _welcome(update, chat_id, ext, after_photo=True)
        return
    if _first_in(f"pub-other:{chat_id}", OTHER_EVERY_SECONDS):
        await _say(_reply_target(update), OTHER_REFUSED, essential=False)


async def on_staff_button(update: Update, context=None) -> None:
    """A staff draft's button (confirm, side_*, ...) from a Telegram that
    isn't connected: does nothing, beyond dropping a draft left from when
    it was."""
    await _begin(update)


# ----------------------------------------------------------------- photos


async def on_photo(update: Update, context=None) -> None:
    chat_id, ext, session, stale = await _begin(update)
    if stale:
        return
    if session is None:
        # Nothing sent before consent is fetched, or kept.
        await _welcome(update, chat_id, ext, after_photo=True)
        return
    form = session.public
    form.photo_count_sent += 1
    if _forwarded(update.message):
        form.forwarded_count += 1
    note = await _take_photo(update, chat_id, ext, session)
    if note and note not in form.ack_notes:
        form.ack_notes.append(note)
    if note is None and form.step not in (PublicStep.PHOTOS,):
        form.rev += 1  # an open review is out of date
    _cancel_ack(form)
    form.ack_task = asyncio.create_task(_ack_later(chat_id, session, form, _reply_target(update)))


async def _take_photo(update: Update, chat_id: str, ext: str, session) -> str | None:
    """Downloads the photo into the form; None if kept (or a repeat), else
    why not, for the album's reply."""
    form = session.public
    fits = [p for p in (update.message.photo or ()) if (p.file_size or 0) <= public.MAX_PHOTO_BYTES]
    if not fits:
        return TOO_BIG
    size = max(fits, key=lambda p: (p.width or 0) * (p.height or 0))
    if size.file_unique_id in form.photo_ids:
        return None
    if len(form.photo_ids) >= public.MAX_PHOTOS:
        return AT_CAP
    expected = size.file_size or public.MAX_PHOTO_BYTES
    if form.photo_bytes + expected > public.MAX_REPORT_BYTES:
        return REPORT_FULL
    if _temp_bytes() + expected > MAX_TEMP_BYTES or disk_space_low(public.DISK_HEADROOM_GB):
        logger.info("Public photo from Telegram user %s not taken: temp space or disk headroom", ext)
        return NO_SPACE
    if not sender_photos.take(ext):
        return TOO_MANY_PHOTOS
    if not all_photos.take(ALL):
        sender_photos.refund(ext)
        logger.info("Public photo from Telegram user %s not taken: the hourly photo budget is spent", ext)
        return NO_SPACE
    dest = _new_temp_path(".jpg")
    try:
        async with _download_slots():
            file = await size.get_file()
            await file.download_to_drive(str(dest))
        got = dest.stat().st_size
    except Exception as exc:
        sender_photos.refund(ext)
        all_photos.refund(ALL)
        dest.unlink(missing_ok=True)
        logger.warning("Public photo from Telegram user %s didn't download (%s)", ext, type(exc).__name__)
        return NOT_RECEIVED
    if got > public.MAX_PHOTO_BYTES or form.photo_bytes + got > public.MAX_REPORT_BYTES or session.public is not form:
        dest.unlink(missing_ok=True)
        return TOO_BIG if session.public is form else None
    session.photo_paths.append(str(dest))
    form.photo_ids.append(size.file_unique_id)
    form.photo_bytes += got
    return None


async def _ack_later(chat_id: str, session, form: PublicForm, target) -> None:
    """One reply for an album: ALBUM_SECONDS after its last photo (each new
    one starts this again)."""
    try:
        await asyncio.sleep(ALBUM_SECONDS)
    except asyncio.CancelledError:
        return
    if find_session(chat_id) is not session or session.public is not form:
        return
    form.ack_task = None
    notes = " ".join(form.ack_notes)
    form.ack_notes = []
    count = len(form.photo_ids)
    got = f"Got {count} photo{'' if count == 1 else 's'} ({public.MAX_PHOTOS} max)."
    if form.step == PublicStep.PHOTOS:
        if count >= public.MAX_PHOTOS:
            text = f"{notes} {got} Tap Done to continue.".strip()
        else:
            text = f"{notes} {got} Send more, or tap Done.".strip()
        await _say(target, text, reply_markup=_buttons(form.nonce, [("Done", "done")]))
        return
    # Photos sent before step 6 are kept for it; then this step again.
    kept = f"Thanks, I've kept {count} photo{'' if count == 1 else 's'} for step 6." if count else ""
    await _ask(target, session, " ".join(p for p in (notes, kept) if p))


# ---------------------------------------------------------------- buttons


def _parse(data: str):
    """pub:<nonce>:<action>[:<arg>] -> (nonce, action, arg) or None."""
    parts = (data or "").split(":")
    if len(parts) not in (3, 4) or parts[0] != CALLBACK_PREFIX[:-1] or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2], (parts[3] if len(parts) == 4 else None)


def _index(arg) -> int | None:
    return int(arg) if isinstance(arg, str) and arg.isdigit() and len(arg) <= 2 else None


def _offer_for(ext: str, nonce: str) -> _Offer | None:
    offer = _offers.get(ext)
    if offer is None or offer.nonce != nonce or time.monotonic() - offer.at > OFFER_SECONDS:
        return None
    return offer


def _current(chat_id: str, ext: str, nonce: str, action: str):
    """Whether a tap is for something still open: the welcome, this
    sender's /withdraw question, or the chat's form."""
    if action in ("agree", "no"):
        seen = _welcomes.get(ext)
        session = find_session(chat_id)
        return (seen is not None and seen[0] == nonce) or (session is not None and session.public is not None)
    if action in ("wd", "wdy", "wdk"):
        return _offer_for(ext, nonce) is not None
    session = find_session(chat_id)
    return session is not None and session.public is not None and session.public.nonce == nonce


def claim_submit(update: Update):
    """A Submit tap claims its form (submitting) -- called by telegram.py's
    gate before its first await, as staff's Confirm is claimed -- so a
    second tap is only answered and sends nothing. (busy answer or None,
    release or None)."""
    parsed = _parse(update.callback_query.data)
    if parsed is None or parsed[1] != "submit":
        return None, None
    session = find_session(str(update.effective_chat.id))
    form = session.public if session is not None else None
    if form is None or form.nonce != parsed[0]:
        return None, None
    if form.submitting:
        return "Already sending your report -- one moment.", None
    form.submitting = True

    def release() -> None:
        form.submitting = False

    return None, release


async def on_callback(update: Update, context=None) -> None:
    """A pub: button from someone not connected (a Submit already claimed:
    claim_submit)."""
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    ext = str(update.effective_user.id)
    parsed = _parse(query.data)
    if parsed is None:
        await answer_tap(query)
        return
    nonce, action, arg = parsed
    await answer_tap(query, None if _current(chat_id, ext, nonce, action) else BUTTON_EXPIRED)
    async with _lock_for(chat_id):
        await _tap(update, chat_id, ext, nonce, action, arg)


async def _tap(update: Update, chat_id: str, ext: str, nonce: str, action: str, arg) -> None:
    target = _reply_target(update)
    if action in ("wd", "wdy", "wdk"):
        await _withdraw_tap(update, ext, nonce, action, arg)
        return
    _, _, session, stale = await _begin(update)
    if stale:
        return
    if action in ("agree", "no"):
        seen = _welcomes.get(ext)
        if session is not None:
            if action == "agree":
                await _ask(target, session)  # agreed already: carry on
            return
        if seen is None or seen[0] != nonce:
            return
        del _welcomes[ext]
        if action == "agree":
            await _agree(update, chat_id, ext)
        else:
            logger.info("Telegram user %s said no to the public report consent", ext)
            await _say(target, "Nothing saved. Send /start if you change your mind." + _call_us(await _contact_line()))
        return
    if session is None or session.public.nonce != nonce:
        return  # an older form's button, or someone else's
    form = session.public
    if action == "role" and form.step == PublicStep.ROLE and arg in public.ROLES:
        form.role = arg
        _go(form, PublicStep.PHONE)
        await _ask(target, session)
    elif action == "notsure" and form.step == PublicStep.PLATE:
        form.plate, form.plate_unknown = None, True
        _go(form, PublicStep.WHEN_WHERE)
        await _ask(target, session)
    elif action == "skip" and form.step == PublicStep.WHEN_WHERE:
        form.when_where = None
        _go(form, PublicStep.WHAT)
        await _ask(target, session)
    elif action == "done" and form.step == PublicStep.PHOTOS:
        _cancel_ack(form)
        if not form.photo_ids:
            await _say(target, NO_PHOTOS, reply_markup=_buttons(
                form.nonce, [("Send without photos", "nophotos"), ("Add photos", "addphotos")]))
            return
        _go(form, PublicStep.REVIEW)
        await _ask(target, session)
    elif action == "nophotos" and form.step == PublicStep.PHOTOS:
        _go(form, PublicStep.REVIEW)
        await _ask(target, session)
    elif action == "addphotos" and form.step == PublicStep.PHOTOS:
        await _ask(target, session)
    elif action == "clearphotos" and form.step == PublicStep.PHOTOS:
        _cancel_ack(form)
        _discard_temp_files(session)
        session.photo_paths = []
        form.photo_ids, form.photo_bytes = [], 0
        form.rev += 1
        await _ask(target, session)
    elif action == "submit" and form.step == PublicStep.REVIEW:
        if arg != str(form.rev):
            await _ask(target, session)  # an older review: this one instead
            return
        await _submit(update, chat_id, ext, session)
    elif action == "edit" and form.step == PublicStep.REVIEW:
        form.step = PublicStep.EDIT_MENU
        await _ask(target, session)
    elif action == "back" and form.step == PublicStep.EDIT_MENU:
        form.step = PublicStep.REVIEW
        await _ask(target, session)
    elif action == "ed" and form.step == PublicStep.EDIT_MENU and arg in _EDITS:
        form.editing = True
        form.step = _EDITS[arg]
        await _ask(target, session)
    elif action == "cancel":
        await _cancel(update, chat_id, ext, session)
    elif action == "continue":
        await _ask(target, session)
    elif action == "restart":
        _cancel_ack(form)
        _discard_temp_files(session)
        session.photo_paths = []
        session.public = PublicForm(nonce=secrets.token_hex(4), consent_at=form.consent_at)
        await _ask(target, session)
    # Anything else (a step already answered, a made-up action): nothing.


_PASSING = ("inbox_full", "hourly_full", "disk_low")
_EDITS = {
    "name": PublicStep.NAME,
    "phone": PublicStep.PHONE,
    "plate": PublicStep.PLATE,
    "when": PublicStep.WHEN_WHERE,
    "what": PublicStep.WHAT,
    "photos": PublicStep.PHOTOS,
}


async def _submit(update: Update, chat_id: str, ext: str, session) -> None:
    form = session.public
    target = _reply_target(update)
    if not (form.name and form.role and form.phone and form.statement):
        await _ask(target, session)
        return
    if not sent_today.take(ext):
        _drop(chat_id)
        await _say(target, _refusal_text("sender_limit", await _contact_line()))
        return
    tg_user = update.effective_user
    submission = public.Form(
        telegram_user_id=ext,
        name=form.name,
        role=form.role,
        phone=form.phone,
        statement=form.statement,
        consent_at=form.consent_at,
        plate=form.plate,
        when_where=form.when_where,
        telegram_username=getattr(tg_user, "username", None),
        telegram_name=_telegram_name(tg_user),
        photo_count_sent=form.photo_count_sent,
        forwarded_count=form.forwarded_count,
    )
    photos = [p for p in session.photo_paths if Path(p).exists()]
    try:
        _, reference = await _in_thread(public.save_submission, submission, photos)
    except public.PublicRefused as exc:
        sent_today.refund(ext)
        # A cap reached since consent. One that passes (the inbox, the hour,
        # the disk) keeps the form for a later Submit; this sender's or
        # number's own, a block or a link end it.
        if exc.code not in _PASSING:
            _drop(chat_id)
        logger.info("Telegram user %s: public report refused at Submit (%s)", ext, exc.code)
        await _say(target, _refusal_text(exc.code, await _contact_line()))
        return
    except Exception as exc:
        sent_today.refund(ext)
        logger.error("Telegram user %s: public report not saved (%s)", ext, type(exc).__name__)
        await _say(target, NOT_SENT)
        return
    # Saved (its photos copied): the form and its temp files go before the
    # reply, so a reply that fails can't leave it to be sent twice.
    _drop(chat_id)
    logger.info("Telegram user %s sent public report %s", ext, reference)
    await _say(target, _sent_text(reference))


# ---------------------------------------------------------------- /withdraw


def _sent_on(when) -> str:
    local = when.astimezone(public.SG_TIME) if when is not None else None
    return f"{local.day} {local.strftime('%b')}" if local else "earlier"


async def on_withdraw(update: Update, context=None) -> None:
    """/withdraw: the sender's own reports not yet filed, as buttons that
    carry only an index (pub:<nonce>:wd:<n>)."""
    chat_id, ext, session, stale = await _begin(update)
    if stale:
        return
    target = _reply_target(update)
    if session is not None:
        await _say(target, FINISH_FIRST)
        return
    rows = await _in_thread(_open_reports, ext)
    if not rows:
        _offers.pop(ext, None)
        await _say(target, NO_WITHDRAW)
        return
    rows = rows[:10]
    offer = _Offer(
        nonce=secrets.token_hex(4),
        ids=[r["id"] for r in rows],
        refs=[r["reference"] for r in rows],
        taken=[bool(r["taken"]) for r in rows],
        sent=[_sent_on(r["submitted_at"]) for r in rows],
        at=time.monotonic(),
    )
    _remember(_offers, ext, offer)
    if len(rows) == 1:
        await _confirm_withdraw(target, offer, 0)
        return
    await _say(target, "Which report would you like to take back?", reply_markup=_buttons(
        offer.nonce, *[[(f"{ref} (sent {sent})", f"wd:{i}")] for i, (ref, sent) in enumerate(zip(offer.refs, offer.sent))]))


async def _confirm_withdraw(target, offer: _Offer, i: int) -> None:
    ref, sent = offer.refs[i], offer.sent[i]
    if offer.taken[i]:
        text = (f"Our staff have already started on {ref}, so it can't be deleted here. "
                "Shall I tell them you'd like to withdraw it?")
        yes = "Yes, tell them"
    else:
        text = f"Take back report {ref}, sent {sent}? We'll delete it and its photos."
        yes = "Yes, delete it"
    await _say(target, text, reply_markup=_buttons(offer.nonce, [(yes, f"wdy:{i}"), ("Keep it", "wdk")]))


async def _withdraw_tap(update: Update, ext: str, nonce: str, action: str, arg) -> None:
    target = _reply_target(update)
    offer = _offer_for(ext, nonce)
    if offer is None:
        return  # an older question, or another sender's buttons
    if action == "wdk":
        _offers.pop(ext, None)
        await _say(target, KEPT)
        return
    i = _index(arg)
    if i is None or i >= len(offer.ids):
        return
    if action == "wd":
        await _confirm_withdraw(target, offer, i)
        return
    _offers.pop(ext, None)
    ref = offer.refs[i]
    outcome = await _in_thread(_withdraw_one, offer.ids[i], ext)
    logger.info("Telegram user %s withdrew public report %s: %s", ext, ref, outcome)
    if outcome == "deleted":
        await _say(target, f"Done. Report {ref} and its photos have been deleted.")
    elif outcome == "taken":
        await _say(target, f"Our staff have already started on {ref}, so I can't delete it here. "
                           "We've told them you asked to withdraw it." + _call_us(await _contact_line(), "Please call us on"))
    else:
        await _say(target, NO_WITHDRAW)


# ------------------------------------------------------- for tests and ops


def reset_limits() -> None:
    """Every in-memory budget and question back to empty (tests)."""
    replies.reset()
    for budget in (sender_photos, all_photos, sent_today):
        budget.reset()
    _welcomes.clear()
    _offers.clear()
    _contact["at"] = None
    for key in [k for k in _told_at if k.startswith("pub-")]:
        del _told_at[key]
