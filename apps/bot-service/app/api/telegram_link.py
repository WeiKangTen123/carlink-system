"""Settings > My Telegram: connecting someone's Telegram to their Carlink
account, so the bot knows who is filing (rules in app/auth/chat_links.py).

    GET    /auth/me/telegram            connected or not, and the open code (never the code itself)
    POST   /auth/me/telegram/code       a new code, shown in this response only; 10 an hour
    DELETE /auth/me/telegram/code       cancel the open code
    POST   /auth/me/telegram/confirm    {accept}: Carlink's side of connecting
    DELETE /auth/me/telegram            disconnect
    DELETE /users/{id}/telegram         admin: disconnect someone's Telegram

GET, POST code and POST confirm all answer with chat_links.telegram_status.
Like every route these need a signed-in user, and a temporary password
refuses them (auth/middleware.py).

When the account confirms or turns down a Telegram that said Yes, the
person on Telegram is told ("Connected" / "Not connected") through the Bot
API, after the response has gone. httpx logs request URLs at INFO and the
Bot API's URL carries the bot token, so httpx and httpcore stay at WARNING
(api/main.py), and nothing here logs a URL or an exception's text.
"""
import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, StrictBool

from app.auth import chat_links
from app.auth.core import CurrentUser, display_name
from app.auth.deps import ApiError, current_user, require_admin
from app.config import settings
from app.reports.db import SessionLocal
from app.reports.models import User

logger = logging.getLogger(__name__)
# Also set in api/main.py and app/main.py; here too so the token stays out
# of the logs whichever imports this first.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

router = APIRouter(tags=["telegram-link"])

TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_TIMEOUT_SECONDS = 10.0


def connected_text(user) -> str:
    return (
        f"Connected. This Telegram now files reports as {display_name(user)} in Carlink. "
        "Send photos of an incident to start one."
    )


NOT_CONNECTED_TEXT = (
    "Not connected: the Carlink account didn't confirm this Telegram. "
    "To try again, make a new code in Carlink > Settings > My Telegram."
)


def tell_telegram(external_id: str, text: str) -> None:
    """Sends one plain-text message to a Telegram user (their private chat
    with the bot has the same id). Best effort: a failure is logged without
    the URL, which holds the bot token, or the exception's text, which can
    repeat the URL."""
    token = settings.telegram_bot_token
    if not token or not external_id:
        return
    try:
        r = httpx.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            json={"chat_id": external_id, "text": text},
            timeout=TELEGRAM_TIMEOUT_SECONDS,
        )
        if r.status_code != 200:
            logger.warning("Telegram didn't take a connect message (HTTP %s)", r.status_code)
    except Exception as exc:  # noqa: BLE001 - never fail anything over a courtesy message
        logger.warning("Couldn't send a connect message to Telegram (%s)", type(exc).__name__)


def _refusal(exc: chat_links.ChatLinkError) -> ApiError:
    return ApiError(exc.status, exc.message, exc.code)


@router.get("/auth/me/telegram")
def my_telegram(user: CurrentUser = Depends(current_user)) -> dict:
    db = SessionLocal()
    try:
        return chat_links.telegram_status(db, user.id)
    finally:
        db.close()


@router.post("/auth/me/telegram/code")
def new_telegram_code(user: CurrentUser = Depends(current_user)) -> dict:
    """A new code, valid 10 minutes, in code.display ("K7M4-QX9P") and
    code.deep_link -- this once: only its SHA-256 is kept. Cancels the
    account's open code. 429 rate_limited after 10 in an hour."""
    db = SessionLocal()
    try:
        try:
            plain, _ = chat_links.new_code(db, user)
        except chat_links.ChatLinkError as exc:
            db.rollback()  # the earlier code stays usable
            raise _refusal(exc)
        db.commit()
        return chat_links.telegram_status(db, user.id, plain_code=plain)
    finally:
        db.close()


@router.delete("/auth/me/telegram/code")
def cancel_telegram_code(background: BackgroundTasks, user: CurrentUser = Depends(current_user)) -> dict:
    db = SessionLocal()
    try:
        told = chat_links.cancel_codes(db, user.id)
        db.commit()
    finally:
        db.close()
    for external_id in told:
        # A Telegram had said Yes and is waiting to hear.
        background.add_task(tell_telegram, external_id, NOT_CONNECTED_TEXT)
    return {"ok": True}


class ConfirmIn(BaseModel):
    accept: StrictBool


@router.post("/auth/me/telegram/confirm")
def confirm_telegram(body: ConfirmIn, background: BackgroundTasks, user: CurrentUser = Depends(current_user)) -> dict:
    """The waiting Telegram (code.waiting): accept true connects it,
    false ("Not me") revokes the code. 409 nothing_waiting, code_expired,
    or telegram_in_use."""
    db = SessionLocal()
    try:
        try:
            link, external_id = chat_links.confirm_from_web_with_id(db, user, body.accept)
        except chat_links.ChatLinkError as exc:
            # A code that can't be used any more stops showing as waiting.
            db.commit()
            raise _refusal(exc)
        db.commit()
        background.add_task(tell_telegram, external_id, connected_text(user) if link is not None else NOT_CONNECTED_TEXT)
        return chat_links.telegram_status(db, user.id)
    finally:
        db.close()


@router.delete("/auth/me/telegram")
def disconnect_my_telegram(user: CurrentUser = Depends(current_user)) -> dict:
    db = SessionLocal()
    try:
        chat_links.unlink(db, user_id=user.id, actor=user, reason="from Settings")
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.delete("/users/{user_id}/telegram")
def disconnect_users_telegram(user_id: str, admin: CurrentUser = Depends(require_admin)) -> dict:
    db = SessionLocal()
    try:
        if db.get(User, user_id) is None:
            raise HTTPException(status_code=404, detail="User not found.")
        chat_links.unlink(db, user_id=user_id, actor=admin, reason="by an admin")
        db.commit()
        return {"ok": True}
    finally:
        db.close()
