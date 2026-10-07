"""Telegram adapter. Runs in polling mode -- no public URL/webhook needed,
which is what makes it the fastest channel to stand up (see docs/proposal.md
section B). Get a token from @BotFather, put it in .env, and this runs.
"""
import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import settings
from app.ai.client import available_api_keys
from app.ai.rate_limit import estimate_wait
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reset_session(str(update.effective_chat.id))
    await update.message.reply_text(WELCOME_GUIDE, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_GUIDE, parse_mode="Markdown")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reset_session(str(update.effective_chat.id))
    await update.message.reply_text("🔄 Current report session cancelled and reset. Send /new or a photo to start a fresh report.")


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

    # Insurer options if missing
    if session.draft and session.draft.insurance_details and not session.draft.insurance_details.insurer_name:
        buttons.append([
            InlineKeyboardButton("🏢 Tokio Marine", callback_data="ins_tokio"),
            InlineKeyboardButton("🏢 NTUC Income", callback_data="ins_income"),
            InlineKeyboardButton("🏢 AIG", callback_data="ins_aig"),
        ])
    return InlineKeyboardMarkup(buttons)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _lock_for(str(update.effective_chat.id)):
        await _handle_photo(update, context)


async def _handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    session = get_session(chat_id)

    photo = update.message.photo[-1]
    file = await photo.get_file()
    dest = tmp_dir() / f"{chat_id}_{len(session.photo_paths)}.jpg"
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


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _lock_for(str(update.effective_chat.id)):
        chat_id = str(update.effective_chat.id)
        session = get_session(chat_id)
        doc = update.message.document
        if not doc:
            return
        mime = doc.mime_type or ""
        ext = ".pdf" if "pdf" in mime else ".jpg"
        file = await doc.get_file()
        dest = tmp_dir() / f"{chat_id}_{len(session.photo_paths)}{ext}"
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


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = str(update.effective_chat.id)
    session = get_session(chat_id)
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
        ins_name = insurer_map.get(data, "Tokio Marine Singapore")
        session.insurer_name = ins_name
        session.pending_edits.append(f"insurer is {ins_name}")
        await draft_and_reply(update, session, combined_description(session), redrafting=True)


async def finalize_report(update: Update, session, chat_id: str) -> None:
    msg_target = update.message or (update.callback_query.message if update.callback_query else None)
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

    if msg_target:
        with open(pdf_path, "rb") as f:
            await msg_target.reply_document(document=f, filename="security_incident_report.pdf")
        rep_code = report.id[:8].upper()
        await msg_target.reply_text(
            f"✅ Report *CIR-{rep_code}* finalized and saved to Carlink System!\n\n"
            f"🖥️ *Open in Loss Adjuster Studio:*\n"
            f"https://carlink.34-45-253-162.sslip.io/reports/{report.id}\n\n"
            f"Send /new or a photo to file another report.",
            parse_mode="Markdown"
        )
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
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return application

