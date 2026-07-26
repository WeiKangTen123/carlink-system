# Carlink System

AI-assisted incident reporting for Carlink Consultancy, starting with a
WhatsApp/Telegram bot and growing into the full vehicle-claims (TP/OD/SJE)
pipeline. See `docs/proposal.md` for the full architecture, tech-stack
decisions, data model, and roadmap.

## Layout

```
carlink system/
├── apps/
│   ├── bot-service/     # FastAPI + Telegram/WhatsApp bot + JSON API (Phase 0) — see its README
│   └── dashboard/       # Next.js dashboard: overview/charts, reports, manual entry — see its README
├── docs/
│   └── proposal.md      # system proposal: architecture, stack, roadmap, open questions
├── infra/
│   └── docker-compose.yml  # Postgres/Redis/MinIO — Phase 1, not needed to run Phase 0
└── Resource/             # source material: Carlink brief, sample report templates
```

## Getting started

Two apps, both need to be running:

1. `apps/bot-service` — the bot + backend API. `uvicorn app.api.main:app --port 8000` (see its README for the Telegram/Gemini setup).
2. `apps/dashboard` — the web UI. `npm run dev`, then open http://localhost:3000.

See each folder's README for setup details.
