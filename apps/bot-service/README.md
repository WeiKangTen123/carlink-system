# Carlink bot-service (Phase 0)

Telegram (and a WhatsApp/Twilio stub) bot: send a photo and a short
description, get back an AI-drafted, human-confirmed Security Incident
Report as a PDF. See `../../docs/proposal.md` for the full architecture and
roadmap this fits into.

## What's actually built here

- **Telegram channel** (`app/channels/telegram.py`) — runs in polling mode, no public URL needed.
- **WhatsApp channel stub** (`app/channels/whatsapp.py`) — a Twilio webhook handler with the same conversation logic. It can draft and confirm reports, but can't attach the generated PDF back over WhatsApp yet (Twilio needs a public URL to fetch media from — see the comment at the top of that file).
- **AI drafting** (`app/ai/extraction.py`) — one Gemini call (photos + free text in, a validated `SecurityIncidentDraft` out via structured outputs), falling back through `GEMINI_MODEL_CHAIN` (default: Gemini 3.5 Flash-Lite → Gemini 3.1 Flash-Lite → Antigravity) so hitting one free-tier model's RPM/TPM/RPD quota doesn't stop the bot.
- **PDF rendering** (`app/rendering/`) — Jinja2 template → Playwright (headless Chromium) → PDF.
- **Storage** — SQLite (`app/reports/`) for report records, local disk (`app/storage/files.py`) for photos/PDFs, laid out as `storage/incidents/{year}/{month}/{incident_id}/`.
- **Minimal REST API** (`app/api/main.py`) — `/health`, `/reports`, `/reports/{id}`, plus the WhatsApp webhook route. Seed of the Phase 1 dashboard API.

Deliberately **not** built yet (see `docs/proposal.md` section J for when): Postgres/Redis/S3 (SQLite + local disk are enough for Phase 0 volume), the Next.js dashboard, auth, vehicle-claim fields, part-price lookup.

## Setup

Requires Python 3.11+.

```bash
cd apps/bot-service
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
playwright install chromium     # one-time, for PDF rendering
copy .env.example .env          # then fill in TELEGRAM_BOT_TOKEN and GEMINI_API_KEY
```

**Telegram bot token**: message [@BotFather](https://t.me/BotFather) on Telegram, `/newbot`, follow the prompts, paste the token into `.env`.

**Gemini API key**: from aistudio.google.com/apikey. If calls fail with an auth error, check the key hasn't expired — a key copied from an OAuth flow (starts with something other than `AIzaSy`) is typically short-lived; get a stable one from the link above instead.

**Model fallback chain**: `GEMINI_MODEL_CHAIN` in `.env` is a comma-separated list, tried in order until one succeeds. Each model draws from its own separate RPM/TPM/RPD quota, so this is what lets the bot keep working after any single model's free-tier quota is exhausted. Edit it to match whatever's actually available in your Google AI Studio account.

## Verify the rendering pipeline (no credentials needed)

```bash
python scripts/render_sample.py
```

Renders `storage/sample_report.pdf` from fixed sample data. If this works, Jinja2 + Playwright are wired up correctly.

## Run the bot

```bash
python -m app.main
```

Message your bot on Telegram: `/new`, send a photo, then describe what happened. It'll draft, show you the summary, and generate the PDF once you reply `confirm`.

## Run the API (dashboard seed + WhatsApp webhook)

```bash
uvicorn app.api.main:app --reload
```

`GET http://localhost:8000/reports` lists everything filed so far. The WhatsApp webhook is `POST /whatsapp/webhook` — point a Twilio WhatsApp Sandbox at a tunnel to this (e.g. `ngrok http 8000`) to test it.
