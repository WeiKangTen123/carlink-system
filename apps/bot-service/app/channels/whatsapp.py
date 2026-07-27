"""WhatsApp adapter (Twilio webhook), mounted into app/api/main.py.

Needs a Twilio account with the WhatsApp Sandbox (or a verified WhatsApp
Business number) pointed at POST /whatsapp/webhook on this service --
see docs/proposal.md sections B and M for why Twilio's sandbox is the way
to unblock this before Meta's business verification clears.

Known Phase 0 limitation: Twilio can only attach media it can fetch from a
public URL, and this service's generated PDFs are local-only. So this
sends the draft summary and a confirmation message over WhatsApp same as
Telegram, but doesn't attach the PDF back yet -- that needs either a public
storage bucket (Phase 1, see docs/proposal.md section E) or a tunnel for
local testing.
"""
import logging

from fastapi import APIRouter, Request, Response

import httpx

from app.config import settings
from app.conversation.flow import build_draft, build_template_prompt, combined_description, parse_template_reply
from app.conversation.state import Stage, get_session, reset_session
from app.reports.db import SessionLocal
from app.reports.models import Report
from app.rendering.renderer import render_pdf
from app.storage.files import report_pdf_path, save_photo, tmp_dir

logger = logging.getLogger(__name__)

router = APIRouter()

_DRAFT_ERROR_MESSAGE = (
    "Something went wrong while drafting the report (check the server's "
    "GEMINI_API_KEY and connectivity). Please try again."
)

CONFIRM_WORDS = {"confirm", "yes", "y", "ok", "okay", "looks good"}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _twiml(*messages: str) -> Response:
    body = "".join(f"<Message>{_escape(m)}</Message>" for m in messages)
    xml = f"<?xml version='1.0' encoding='UTF-8'?><Response>{body}</Response>"
    return Response(content=xml, media_type="application/xml")


async def _download_media(chat_id: str, url: str, index: int) -> str:
    dest = tmp_dir() / f"{chat_id.replace(':', '_')}_{index}.jpg"
    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, auth=auth)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return str(dest)


def _finalize(chat_id: str, session) -> str:
    db = SessionLocal()
    try:
        report = Report(
            channel="whatsapp",
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
        render_pdf(report.data, stored_photos, pdf_path, report_id=report.id)
        report.pdf_path = pdf_path
        db.commit()
        return pdf_path
    finally:
        db.close()


@router.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request) -> Response:
    form = await request.form()
    chat_id = f"whatsapp:{form.get('From', 'unknown')}"
    body = (form.get("Body") or "").strip()
    num_media = int(form.get("NumMedia", "0") or 0)

    session = get_session(chat_id)

    if num_media > 0:
        for i in range(num_media):
            media_url = form.get(f"MediaUrl{i}")
            if not media_url:
                continue
            dest = await _download_media(chat_id, media_url, len(session.photo_paths))
            session.photo_paths.append(dest)
        got_it = f"Got it -- {len(session.photo_paths)} photo(s) received. Send more if you have any."
        if not session.template_sent:
            session.template_sent = True
            return _twiml(got_it, build_template_prompt())
        return _twiml(got_it)

    if not body:
        return _twiml("Send a photo or a short description to start a report.")

    if session.stage == Stage.AWAITING_CONFIRMATION:
        if body.lower() in CONFIRM_WORDS:
            pdf_path = _finalize(chat_id, session)
            reset_session(chat_id)
            return _twiml(
                f"Report saved and rendered to {pdf_path}. "
                "PDF delivery over WhatsApp needs a public file URL for Twilio to "
                "fetch -- wire that up before this goes to real users; for now, "
                "pull the file from the dashboard/API or the local storage folder."
            )
        session.pending_edits.append(body)
        description = combined_description(session)
        try:
            result = build_draft(description, session.photo_paths, session)
        except Exception:
            logger.exception("AI drafting failed")
            return _twiml(_DRAFT_ERROR_MESSAGE)
        session.draft = result.draft
        return _twiml(result.summary_text)

    if not session.photo_paths:
        return _twiml("Please send at least one photo first, then describe what happened.")

    parsed = parse_template_reply(body)
    if parsed:
        session.location = parsed["location"]
        session.incident_datetime = parsed["incident_datetime"]
        session.reported_to_authorities = parsed["reported_to_authorities"]
        description = parsed["description"]
    else:
        description = body

    session.description = description
    try:
        result = build_draft(description, session.photo_paths, session)
    except Exception:
        logger.exception("AI drafting failed")
        return _twiml(_DRAFT_ERROR_MESSAGE)
    session.draft = result.draft
    session.stage = Stage.AWAITING_CONFIRMATION
    return _twiml(result.summary_text)
