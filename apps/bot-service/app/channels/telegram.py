"""Telegram adapter. Runs in polling mode -- no public URL/webhook needed,
which is what makes it the fastest channel to stand up (see docs/proposal.md
section B). Get a token from @BotFather, put it in .env, and this runs.
"""
import asyncio
import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import settings
from app.conversation.flow import build_draft, build_template_prompt, combined_description, parse_template_reply
from app.conversation.state import Stage, get_session, reset_session
from app.reports.db import SessionLocal, init_db
from app.reports.models import Report
from app.rendering.renderer import render_pdf
from app.storage.files import report_pdf_path, save_photo, tmp_dir

logger = logging.getLogger(__name__)

WELCOME_GUIDE = (
    "🚨 *Carlink AI Incident Reporting System* 🚨\n\n"
    "I will assist you in generating an official, structured **Security Incident Report PDF** from your photos & description.\n\n"
    "📋 *3 Simple Steps to File a Report:*\n\n"
    "1️⃣ *Send Photos* 📸\n"
    "   • Upload 1 or more photos of the scene or damage.\n\n"
    "2️⃣ *Fill In the Template* 📝\n"
    "   • I'll send you a short template (Name, Role/Position, Contact Number, Vehicle Plate, Location, Date/Time, Description, Reported to Authorities).\n"
    "   • Copy it, fill in the blanks, and send it back. Category, Damaged Parts, and Severity are worked out automatically from your photos.\n\n"
    "3️⃣ *Review & Confirm* 📄\n"
    "   • Carlink AI will draft a formatted summary for you.\n"
    "   • Reply *'confirm'* to generate your PDF report, or reply with edits to modify it.\n\n"
    "----------------------------------------\n"
    "💡 *Available Commands:*\n"
    "• /new or /start — Start a new report session\n"
    "• /help — Display this step-by-step guide\n"
    "• /cancel — Reset current session\n\n"
    "👇 *Please send your first photo (or type /new) to begin!*"
)

CONFIRM_WORDS = {"confirm", "yes", "y", "ok", "okay", "looks good"}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reset_session(str(update.effective_chat.id))
    await update.message.reply_text(WELCOME_GUIDE, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_GUIDE, parse_mode="Markdown")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reset_session(str(update.effective_chat.id))
    await update.message.reply_text("🔄 Current report session cancelled and reset. Send /new or a photo to start a fresh report.")



# Raising concurrent_updates (see build_app()) lets DIFFERENT chats' updates
# be handled at the same time, which is safe -- they mutate different
# Session objects. It does NOT stop the SAME chat's updates from
# overlapping (e.g. a user double-texting while their photo upload is
# still being handled), which would race on that chat's shared in-memory
# Session. These per-chat locks serialize same-chat updates against each
# other without blocking unrelated chats.
_chat_locks: dict[str, asyncio.Lock] = {}


def _lock_for(chat_id: str) -> asyncio.Lock:
    return _chat_locks.setdefault(chat_id, asyncio.Lock())


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _lock_for(str(update.effective_chat.id)):
        await _handle_photo(update, context)


async def _handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    session = get_session(chat_id)
    if session.stage != Stage.AWAITING_PHOTOS:
        session = reset_session(chat_id)

    photo = update.message.photo[-1]
    file = await photo.get_file()
    dest = tmp_dir() / f"{chat_id}_{len(session.photo_paths)}.jpg"
    await file.download_to_drive(str(dest))
    session.photo_paths.append(str(dest))

    await update.message.reply_text(
        f"Got it -- {len(session.photo_paths)} photo(s) received. Send more if you have any."
    )
    if not session.template_sent:
        session.template_sent = True
        await update.message.reply_text(build_template_prompt(), parse_mode="Markdown")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _lock_for(str(update.effective_chat.id)):
        await _handle_text(update, context)


async def _handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    session = get_session(chat_id)
    text = update.message.text.strip()

    if session.stage == Stage.AWAITING_CONFIRMATION:
        if text.lower() in CONFIRM_WORDS:
            await finalize_report(update, session, chat_id)
            return
        session.pending_edits.append(text)
        await update.message.reply_text("Redrafting with your changes...")
        await draft_and_reply(update, session, combined_description(session))
        return

    if not session.photo_paths:
        await update.message.reply_text(
            "Please send at least one photo first, then describe what happened."
        )
        return

    parsed = parse_template_reply(text)
    if parsed:
        session.location = parsed["location"]
        session.incident_datetime = parsed["incident_datetime"]
        session.reported_to_authorities = parsed["reported_to_authorities"]
        session.reporter_name = parsed["reporter_name"]
        session.reporter_role = parsed["reporter_role"]
        session.reporter_contact = parsed["reporter_contact"]
        session.vehicle_plate = parsed["vehicle_plate"]
        description = parsed["description"]
    else:
        description = text

    session.description = description
    await draft_and_reply(update, session, description)


async def draft_and_reply(update: Update, session, description: str) -> None:
    await update.message.reply_text("Drafting your report...")
    try:
        # build_draft() makes a blocking Gemini network call (up to 45s per
        # model, x5 fallback models worst case) -- called directly (as this
        # used to) it runs ON the bot's single asyncio event loop, freezing
        # the ENTIRE bot for EVERY user, not just this conversation, for the
        # whole duration. render_pdf() below already gets this right via
        # asyncio.to_thread(); this call was just missed.
        result = await asyncio.to_thread(build_draft, description, session.photo_paths, session)
    except Exception:
        logger.exception("AI drafting failed")
        await update.message.reply_text(
            "Something went wrong while drafting the report (check the server's "
            "GEMINI_API_KEY and connectivity). Please try again."
        )
        return
    session.draft = result.draft
    session.stage = Stage.AWAITING_CONFIRMATION
    await update.message.reply_text(result.summary_text)


async def finalize_report(update: Update, session, chat_id: str) -> None:
    db = SessionLocal()
    try:
        report = Report(
            channel="telegram",
            reporter_chat_id=chat_id,
            data=session.draft.model_dump(),
            status="confirmed",
        )
        db.add(report)
        db.flush()

        stored_photos = [
            save_photo(report.id, p, i) for i, p in enumerate(session.photo_paths, start=1)
        ]
        report.photo_paths = stored_photos

        pdf_path = report_pdf_path(report.id)
        await asyncio.to_thread(render_pdf, report.data, stored_photos, pdf_path, report_id=report.id)
        report.pdf_path = pdf_path

        db.commit()
    finally:
        db.close()

    with open(pdf_path, "rb") as f:
        await update.message.reply_document(document=f, filename="security_incident_report.pdf")
    await update.message.reply_text("Report saved. Send /new to file another.")
    reset_session(chat_id)


async def _post_init(app: Application) -> None:
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
    # Default is effectively 1 (python-telegram-bot's own default, never
    # overridden here before) -- every update, from every chat, was handled
    # one at a time end-to-end. Safe to raise now that per-chat locks above
    # protect same-chat state, and the Gemini call itself is both off the
    # event loop (asyncio.to_thread) and separately rate-gated
    # (extraction.py's _wait_for_rate_limit) -- so this only affects how
    # many DIFFERENT chats can be mid-conversation at once, not how fast
    # Gemini gets hit.
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .concurrent_updates(8)
        .post_init(_post_init)
        .build()
    )
    application.add_handler(CommandHandler(["start", "new"], start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return application

