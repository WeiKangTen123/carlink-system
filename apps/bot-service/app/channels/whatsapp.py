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
import asyncio
import logging

from fastapi import APIRouter, Request, Response

import httpx

from app.ai.keys import AiActor, AiUnavailable, ensure_ai_ready
from app.api.limits import RenderRefused, render_turn
from app.auth.deps import current_user
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

# Whoever presses the button pays for the AI (ai/keys.py), and a WhatsApp
# number isn't linked to a Carlink account, so there is nobody to pay:
# resolve_keys refuses an actor with no account before any Gemini call.
AI_ACTOR = AiActor.for_user(None, "whatsapp")
_NO_AI_MESSAGE = "Sorry, AI drafting isn't available over WhatsApp yet. Please file this report on the Carlink dashboard."

# For when drafting is switched back on (AI_ACTOR linked to accounts):
# media only from Twilio's API host, and no bigger than a dashboard photo.
# Twilio's own SDK isn't installed, so X-Twilio-Signature isn't checked;
# do that before turning this path on.
MEDIA_HOSTS = {"api.twilio.com"}
MAX_MEDIA_BYTES = 25 * 1024 * 1024


def _draft(description: str, session):
    """build_draft for AI_ACTOR. Readiness first: drafting reads and
    encodes every photo before the call itself looks for a key."""
    ensure_ai_ready(AI_ACTOR)
    return build_draft(description, session.photo_paths, session, actor=AI_ACTOR)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _twiml(*messages: str) -> Response:
    body = "".join(f"<Message>{_escape(m)}</Message>" for m in messages)
    xml = f"<?xml version='1.0' encoding='UTF-8'?><Response>{body}</Response>"
    return Response(content=xml, media_type="application/xml")


def _media_url_ok(url: str) -> bool:
    """Only Twilio's own media URLs (https://api.twilio.com/...): a
    MediaUrl is whatever the request says, and _download_media sends the
    Twilio account's SID and token with it."""
    try:
        parsed = httpx.URL(url)
    except Exception:
        return False
    return parsed.scheme == "https" and parsed.host in MEDIA_HOSTS and parsed.port in (None, 443)


async def _download_media(chat_id: str, url: str, index: int) -> str:
    """One photo from Twilio, at most MAX_MEDIA_BYTES. Raises ValueError for
    a URL that isn't Twilio's or a file that's too big (nothing kept).
    Twilio answers with a redirect to its CDN; httpx drops the credentials
    when a redirect leaves the host they were sent to."""
    if not _media_url_ok(url):
        raise ValueError("not a Twilio media URL")
    dest = tmp_dir() / f"{chat_id.replace(':', '_')}_{index}.jpg"
    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    size = 0
    async with httpx.AsyncClient(follow_redirects=True, max_redirects=3, timeout=30.0) as client:
        async with client.stream("GET", url, auth=auth) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as out:
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_MEDIA_BYTES:
                        break
                    out.write(chunk)
    if size > MAX_MEDIA_BYTES:
        dest.unlink(missing_ok=True)
        raise ValueError("media over the size limit")
    return str(dest)


def _finalize(chat_id: str, session, user) -> str:
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
        try:
            # In the api process, so the api's rules: a short wait for a
            # Chromium slot, and one render at a time per account.
            with render_turn(user):
                render_pdf(report.data, stored_photos, pdf_path, report_id=report.id)
            report.pdf_path = pdf_path
        except RenderRefused:
            report.pdf_path = None
        except Exception as exc:
            logger.error("Failed to render PDF for report %s in WhatsApp: %s", report.id, exc)
            report.pdf_path = None
        db.commit()
        return report.id
    finally:
        db.close()


@router.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request) -> Response:
    # Behind AuthMiddleware only a signed-in user gets here. No AI for
    # anyone (AI_ACTOR), so every message gets _NO_AI_MESSAGE at once:
    # before the form is read, so no media is ever fetched.
    user = current_user(request)
    try:
        await asyncio.to_thread(ensure_ai_ready, AI_ACTOR)
    except AiUnavailable:
        return _twiml(_NO_AI_MESSAGE)
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
            try:
                dest = await _download_media(chat_id, media_url, len(session.photo_paths))
            except (ValueError, httpx.HTTPError) as exc:
                logger.warning("WhatsApp media %d skipped: %s", i, type(exc).__name__)
                continue
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
            report_id = await asyncio.to_thread(_finalize, chat_id, session, user)
            reset_session(chat_id)
            rep_code = report_id[:8].upper()
            return _twiml(
                f"✅ Report CIR-{rep_code} finalized and saved to Carlink System!\n\n"
                f"🖥️ Loss Adjuster Studio:\n"
                f"https://carlink.34-45-253-162.sslip.io/reports/{report_id}\n\n"
                "Send /new or a photo to file another report."
            )
        session.pending_edits.append(body)
        description = combined_description(session)
        try:
            # See the asyncio.to_thread note below -- this runs inside the
            # `api` FastAPI process, the same one serving the whole
            # dashboard, so a blocking call here freezes the entire
            # dashboard for every user, not just this WhatsApp reply.
            result = await asyncio.to_thread(_draft, description, session)
        except AiUnavailable:
            return _twiml(_NO_AI_MESSAGE)
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
        session.reporter_name = parsed["reporter_name"]
        session.reporter_role = parsed["reporter_role"]
        session.reporter_contact = parsed["reporter_contact"]
        session.vehicle_plate = parsed["vehicle_plate"]
        session.damaged_side = parsed["damaged_side"]
        description = parsed["description"]
    else:
        description = body

    session.description = description
    try:
        # build_draft() makes a blocking Gemini network call (up to 45s per
        # model, x5 fallback models worst case). Called directly (as this
        # used to be), it runs ON this FastAPI process's event loop --
        # freezing the whole api container, dashboard included, for the
        # full duration. render_pdf() (see _finalize above) already gets
        # this right via asyncio.to_thread; this call was just missed.
        result = await asyncio.to_thread(_draft, description, session)
    except AiUnavailable:
        return _twiml(_NO_AI_MESSAGE)
    except Exception:
        logger.exception("AI drafting failed")
        return _twiml(_DRAFT_ERROR_MESSAGE)
    session.draft = result.draft
    session.stage = Stage.AWAITING_CONFIRMATION
    return _twiml(result.summary_text)
