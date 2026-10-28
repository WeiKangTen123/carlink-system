"""Telegram adapter. Runs in polling mode -- no public URL/webhook needed,
which is what makes it the fastest channel to stand up (see docs/proposal.md
section B). Get a token from @BotFather, put it in .env, and this runs.
"""
import asyncio
import functools
import logging
import os
import time
import uuid
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import settings
from app.ai.client import available_api_keys
from app.ai.rate_limit import estimate_wait
from app.conversation.flow import build_draft, build_template_prompt, combined_description, parse_template_reply
from app.conversation.state import Stage, get_session, reset_session
from app.reports.db import SessionLocal, init_db
from app.reports.models import Report, gen_id
from app.rendering.renderer import BOT_RENDER_SLOT_WAIT_SECONDS, render_pdf
from app.storage.files import disk_space_low, report_pdf_path, save_photo, tmp_dir

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
    "• /cancel — Reset current session\n\n"
    "👇 *Please send your first photo (or type /new) to begin!*"
)

CONFIRM_WORDS = {"confirm", "yes", "y", "ok", "okay", "looks good"}

REFUSAL_TEXT = "This Carlink bot is for registered staff only. Please ask your Carlink admin for access."
REFUSAL_EVERY_SECONDS = 60 * 60
# The api's cleanup deletes storage/tmp files after a day (intake/cleanup.py
# TMP_MAX_AGE), so a draft idle longer than that may have lost its photos.
SESSION_MAX_IDLE_SECONDS = 24 * 60 * 60
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

# Until account linking ships, only today's users may use the bot: the ids
# in TELEGRAM_ALLOWED_IDS plus every chat that has filed a case through
# Telegram. Filled once by _post_init -- only allowed chats can file new
# cases, so it never needs refreshing. Empty until then, which refuses all.
_allowed_ids: set[str] = set()
_refused_at: dict[str, float] = {}


def _load_allowed_ids() -> set[str]:
    ids = {part.strip() for part in settings.telegram_allowed_ids.split(",") if part.strip()}
    try:
        db = SessionLocal()
        try:
            rows = db.query(Report.reporter_chat_id).filter(Report.channel == "telegram").distinct().all()
        finally:
            db.close()
        ids.update(str(chat_id).strip() for (chat_id,) in rows if chat_id)
    except Exception:
        # Carries on with TELEGRAM_ALLOWED_IDS alone: past reporters are
        # refused until a restart, but nobody unknown gets in.
        logger.exception("Could not read past Telegram reporters for the allow-list")
    return ids


def _is_allowed(update: Update) -> bool:
    return any(
        who is not None and str(who.id) in _allowed_ids
        for who in (update.effective_user, update.effective_chat)
    )


def _staff_only(handler):
    """Runs handler only for an allowed user or chat. Anyone else gets
    REFUSAL_TEXT at most once an hour per chat and nothing more: no
    download, no session, no AI call, no database write."""

    @functools.wraps(handler)
    async def guarded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if _is_allowed(update):
            await handler(update, context)
            return
        chat_key = str(update.effective_chat.id) if update.effective_chat else ""
        now = time.monotonic()
        last = _refused_at.get(chat_key)
        if last is not None and now - last < REFUSAL_EVERY_SECONDS:
            return
        _refused_at[chat_key] = now
        # Ids only, never the message: enough for an admin to add them to
        # TELEGRAM_ALLOWED_IDS. Logged with the reply, so a flood of
        # messages can't flood the log either.
        logger.info(
            "Refused Telegram user %s in chat %s: not on the allow-list",
            update.effective_user.id if update.effective_user else None,
            update.effective_chat.id if update.effective_chat else None,
        )
        if update.effective_message:
            await update.effective_message.reply_text(REFUSAL_TEXT)

    return guarded


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


def _reset(chat_id: str) -> None:
    _discard_temp_files(get_session(chat_id))
    reset_session(chat_id)


async def _current_session(update: Update, chat_id: str):
    """The chat's session, and whether a stale draft was just expired: one
    idle past SESSION_MAX_IDLE_SECONDS is dropped and the user told to start
    again, since confirming it could find its photos already deleted."""
    session = get_session(chat_id)
    now = time.monotonic()
    has_draft = bool(session.photo_paths) or session.draft is not None
    expired = has_draft and now - session.last_active > SESSION_MAX_IDLE_SECONDS
    if expired:
        _discard_temp_files(session)
        session = reset_session(chat_id)
        if update.effective_message:
            await update.effective_message.reply_text(
                "⌛ Your previous draft expired after 24 hours without activity and was discarded. "
                "Please start again: send /new or your photos."
            )
    else:
        _keep_temp_files(session)
    session.last_active = now
    return session, expired


async def _photo_limit_reached(update: Update, session) -> bool:
    if len(session.photo_paths) < MAX_PHOTOS_PER_DRAFT:
        return False
    await update.message.reply_text(
        f"{MAX_PHOTOS_PER_DRAFT} photos is the limit for one report, so this one wasn't added. "
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


@_staff_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    async with _lock_for(chat_id):
        _reset(chat_id)
        await update.message.reply_text(WELCOME_GUIDE, parse_mode="Markdown")


@_staff_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _lock_for(str(update.effective_chat.id)):
        await update.message.reply_text(WELCOME_GUIDE, parse_mode="Markdown")


@_staff_only
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    async with _lock_for(chat_id):
        _reset(chat_id)
        await update.message.reply_text("🔄 Current report session cancelled and reset. Send /new or a photo to start a fresh report.")


# One per chat, held by every handler, so updates from one chat run one at
# a time: a /cancel can't delete photos a Confirm is still copying.
_chat_locks: dict[str, asyncio.Lock] = {}


def _lock_for(chat_id: str) -> asyncio.Lock:
    return _chat_locks.setdefault(chat_id, asyncio.Lock())


def _build_inline_keyboard(session) -> InlineKeyboardMarkup:
    buttons = []
    # Primary confirmation button
    buttons.append([InlineKeyboardButton("✅ Confirm & Save to Studio", callback_data="confirm")])

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


@_staff_only
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _lock_for(str(update.effective_chat.id)):
        await _handle_photo(update, context)


async def _handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    # A photo that finds an expired draft starts the new one, so the rest of
    # an album sent with it isn't split across two drafts.
    session, _ = await _current_session(update, chat_id)
    if await _photo_limit_reached(update, session) or await _disk_full(update):
        return

    photo = update.message.photo[-1]
    file = await photo.get_file()
    dest = _new_temp_path(".jpg")
    await file.download_to_drive(str(dest))
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
        await draft_and_reply(update, session, combined_description(session), redrafting=True)
        return

    await update.message.reply_text(
        f"Got it -- {len(session.photo_paths)} photo(s) received. Send more or describe what happened."
    )
    if not session.template_sent:
        session.template_sent = True
        await update.message.reply_text(build_template_prompt(), parse_mode="Markdown")


@_staff_only
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _lock_for(str(update.effective_chat.id)):
        chat_id = str(update.effective_chat.id)
        session, _ = await _current_session(update, chat_id)
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
        file = await doc.get_file()
        dest = _new_temp_path(ext)
        await file.download_to_drive(str(dest))
        session.photo_paths.append(str(dest))

        caption = (update.message.caption or "").strip()
        if caption:
            session.pending_edits.append(caption)

        await update.message.reply_text(
            f"📄 Evidence document received: {doc.file_name or 'file'}. Added to case."
        )
        if session.stage == Stage.AWAITING_CONFIRMATION:
            await draft_and_reply(update, session, combined_description(session), redrafting=True)
        elif not session.template_sent:
            session.template_sent = True
            await update.message.reply_text(build_template_prompt(), parse_mode="Markdown")


@_staff_only
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _lock_for(str(update.effective_chat.id)):
        await _handle_text(update, context)


async def _handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    session, expired = await _current_session(update, chat_id)
    if expired:
        return  # meant for the expired draft
    text = update.message.text.strip()

    if session.stage == Stage.AWAITING_CONFIRMATION:
        if text.lower() in CONFIRM_WORDS:
            await finalize_report(update, session, chat_id)
            return
        session.pending_edits.append(text)
        await draft_and_reply(update, session, combined_description(session), redrafting=True)
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
    await draft_and_reply(update, session, description)


async def draft_and_reply(update: Update, session, description: str, redrafting: bool = False) -> None:
    notice = "Redrafting with your changes..." if redrafting else "Drafting your report..."
    msg_target = update.message or (update.callback_query.message if update.callback_query else None)

    def _queued_seconds() -> float:
        keys = available_api_keys()
        return estimate_wait(keys[0] if keys else None)

    queued = await asyncio.to_thread(_queued_seconds)
    if msg_target:
        if queued >= 5:
            await msg_target.reply_text(f"{notice} (busy right now -- about {round(queued)}s in queue)")
        else:
            await msg_target.reply_text(notice)

    try:
        result = await asyncio.to_thread(build_draft, description, session.photo_paths, session)
    except Exception:
        logger.exception("AI drafting failed")
        if msg_target:
            await msg_target.reply_text(
                "Something went wrong while drafting the report. Please try again."
            )
        return

    session.draft = result.draft
    session.stage = Stage.AWAITING_CONFIRMATION
    keyboard = _build_inline_keyboard(session)

    if msg_target:
        try:
            await msg_target.reply_text(result.summary_text, parse_mode="Markdown", reply_markup=keyboard)
        except BadRequest:
            logger.warning("Draft summary failed to parse as Markdown; sending unformatted")
            await msg_target.reply_text(result.summary_text, reply_markup=keyboard)


@_staff_only
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    claimed = None
    if query.data == "confirm":
        claimed = get_session(chat_id)
        if claimed.finalizing:
            # A second tap while the first is still saving. Answered here
            # rather than queued behind the chat lock, and saves nothing.
            await query.answer("Already saving this report -- one moment.")
            return
        # Claimed before the first await, so even a near-simultaneous second
        # tap is turned away above.
        claimed.finalizing = True
    try:
        await query.answer()
        async with _lock_for(chat_id):
            await _handle_callback(update, context)
    finally:
        if claimed is not None:
            claimed.finalizing = False


async def _handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    session, expired = await _current_session(update, chat_id)
    if expired:
        return  # a tap on the expired draft
    data = query.data

    if data == "confirm":
        if session.draft:
            await finalize_report(update, session, chat_id)
        else:
            await query.message.reply_text("No active report draft to confirm. Send photos to begin.")
        return
    elif data.startswith("side_"):
        side = data.replace("side_", "")
        session.damaged_side = side
        session.pending_edits.append(f"the damage is on the {side} side")
        await draft_and_reply(update, session, combined_description(session), redrafting=True)
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
        await draft_and_reply(update, session, combined_description(session), redrafting=True)
    elif data.startswith("body_"):
        b_type = data.replace("body_", "")
        session.vehicle_body_type = b_type
        if session.draft and session.draft.vehicle_info:
            session.draft.vehicle_info.body_type = b_type
        session.pending_edits.append(f"vehicle body type is {b_type}")
        await draft_and_reply(update, session, combined_description(session), redrafting=True)


async def finalize_report(update: Update, session, chat_id: str) -> None:
    # While this runs a second Confirm tap is answered, not saved (see
    # handle_callback). Cleared on failure too, so the user can retry.
    session.finalizing = True
    try:
        await _finalize_report(update, session, chat_id)
    finally:
        session.finalizing = False


async def _finalize_report(update: Update, session, chat_id: str) -> None:
    msg_target = update.message or (update.callback_query.message if update.callback_query else None)
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
    report_id = gen_id()
    stored_photos = [save_photo(report_id, p, i) for i, p in enumerate(session.photo_paths, start=1)]
    pdf_path: str | None = report_pdf_path(report_id)
    try:
        # The long wait for a Chromium slot (renderer.py): the bot renders
        # seldom, on its own threads, and the PDF is what the chat gets back.
        await asyncio.to_thread(
            render_pdf, data, stored_photos, pdf_path, report_id=report_id, slot_wait=BOT_RENDER_SLOT_WAIT_SECONDS
        )
    except Exception as exc:
        logger.error("Failed to render PDF for report %s in Telegram: %s", report_id, exc)
        pdf_path = None

    db = SessionLocal()
    try:
        report = Report(
            id=report_id,
            channel="telegram",
            reporter_chat_id=chat_id,
            data=data,
            status="confirmed",
            photo_paths=stored_photos,
            pdf_path=pdf_path,
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        db.expunge(report)
    finally:
        db.close()
    # Saved: drop the draft before replying, so a reply that fails can't
    # leave it there to be confirmed a second time.
    _discard_temp_files(session)
    reset_session(chat_id)

    if msg_target:
        rep_code = report.id[:8].upper()
        if report.pdf_path and Path(report.pdf_path).exists():
            try:
                with open(report.pdf_path, "rb") as f:
                    await msg_target.reply_document(document=f, filename=f"CIR_{rep_code}.pdf")
            except Exception as send_err:
                logger.warning("Could not send PDF document in Telegram: %s", send_err)
        await msg_target.reply_text(
            f"✅ Report *CIR-{rep_code}* finalized and saved to Carlink System!\n\n"
            f"🖥️ *Open in Loss Adjuster Studio:*\n"
            f"https://carlink.34-45-253-162.sslip.io/reports/{report.id}\n\n"
            f"Send /new or a photo to file another report.",
            parse_mode="Markdown"
        )


async def _post_init(app: Application) -> None:
    _allowed_ids.clear()
    _allowed_ids.update(_load_allowed_ids())
    if _allowed_ids:
        logger.info("Telegram allow-list: %d id(s) may use the bot", len(_allowed_ids))
    else:
        logger.warning("Telegram allow-list is empty: the bot will refuse everyone. Set TELEGRAM_ALLOWED_IDS.")
    try:
        await app.bot.set_my_name("Carlink Car Incident Reporter")
        await app.bot.set_my_description(
            "🚗 Carlink AI Car Incident Reporting Bot 🚨\n\n"
            "Upload incident photos and describe what happened to generate "
            "an official, structured incident report PDF instantly."
        )
        await app.bot.set_my_short_description("AI-powered vehicle & car incident report generator")
        await app.bot.set_my_commands([
            ("start", "Start incident report flow"),
            ("new", "Start a new report"),
            ("help", "Show instructions & guide"),
            ("cancel", "Cancel current report drafting"),
        ])
    except Exception as e:
        logger.warning(f"Failed to set bot profile info: {e}")


def build_app() -> Application:
    init_db()
    # Own AI keys: the bot only reads the secret the api makes, and checks
    # the canary (ai/secretbox.py). Not used for AI until P4.
    from app.ai import secretbox

    secretbox.startup(create=False)
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .concurrent_updates(8)
        .post_init(_post_init)
        .build()
    )
    # New messages only. Edited messages and channel posts matched these
    # too, and with no update.message they crashed the handlers.
    new_message = filters.UpdateType.MESSAGE
    application.add_handler(CommandHandler(["start", "new"], start, filters=new_message))
    application.add_handler(CommandHandler("help", help_command, filters=new_message))
    application.add_handler(CommandHandler("cancel", cancel_command, filters=new_message))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.PHOTO & new_message, handle_photo))
    application.add_handler(MessageHandler(filters.Document.ALL & new_message, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & new_message, handle_text))
    return application

