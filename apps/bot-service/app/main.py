"""Entry point: runs both the FastAPI backend API server (port 8000) and the Telegram bot in polling mode.

    python -m app.main
"""
import logging
import threading
import uvicorn
from app.channels.telegram import build_app

logging.basicConfig(level=logging.INFO)


def start_api_server() -> None:
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, log_level="info")


def main() -> None:
    # 1. Start FastAPI backend API server on port 8000 in a background daemon thread
    api_thread = threading.Thread(target=start_api_server, daemon=True)
    api_thread.start()
    logging.info("FastAPI backend API server running on http://localhost:8000")

    # 2. Start Telegram Bot Polling
    application = build_app()
    application.run_polling()


if __name__ == "__main__":
    main()
