"""Entry point: runs the FastAPI backend API server (port 8000) and optionally the Telegram bot.

    python -m app.main
"""
import logging
import sys
import threading
import uvicorn
from app.config import settings

logging.basicConfig(level=logging.INFO)


def start_api_server() -> None:
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, log_level="info")


def main() -> None:
    api_only = "--api-only" in sys.argv
    bot_only = "--bot-only" in sys.argv

    if bot_only:
        if not settings.telegram_bot_token:
            logging.error("TELEGRAM_BOT_TOKEN is not configured; cannot start bot worker.")
            sys.exit(1)
        from app.channels.telegram import build_app
        application = build_app()
        application.run_polling()
        return

    if api_only or not settings.telegram_bot_token:
        if not settings.telegram_bot_token:
            logging.info("TELEGRAM_BOT_TOKEN is not configured. Running FastAPI backend server on http://0.0.0.0:8000")
        else:
            logging.info("Starting API server in foreground (--api-only).")
        start_api_server()
        return

    # Start FastAPI backend API server in background daemon thread
    api_thread = threading.Thread(target=start_api_server, daemon=True)
    api_thread.start()
    logging.info("FastAPI backend API server running on http://localhost:8000")

    # Start Telegram Bot Polling in foreground
    from app.channels.telegram import build_app
    try:
        application = build_app()
        application.run_polling()
    except Exception as exc:
        logging.error("Telegram bot crashed or could not start: %s. Keeping API server alive.", exc)
        api_thread.join()


if __name__ == "__main__":
    main()
