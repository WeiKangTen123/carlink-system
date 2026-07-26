# Carlink dashboard (Phase 1, started early)

Next.js app over the `bot-service` FastAPI backend — an overview with charts,
a reports list, a report detail view, and a form to file/correct a report by
hand instead of through Telegram/WhatsApp.

## What's here

- **Overview** (`/`) — stat tiles + three charts (reports over the last 14
  days, by category, by channel), computed client-side from the same data
  the reports list uses.
- **Reports** (`/reports`) — table of everything filed so far.
- **Report detail** (`/reports/[id]`) — full drafted data, photos (if any),
  PDF download link.
- **New report** (`/reports/new`) — manual entry form with dynamic
  people-involved/witness rows, submitted via a Next.js Server Action
  straight to the backend (no separate API route, no CORS needed).

Charts use the dataviz skill's validated default palette (light mode only —
see the note below).

## Setup

Requires Node.js 20+ (tested against Node 24) and the `bot-service` backend
running (`uvicorn app.api.main:app --port 8000` from `apps/bot-service`,
after `apps/bot-service/README.md`'s setup).

```bash
cd apps/dashboard
npm install
npm run dev
```

Opens on http://localhost:3000. `.env.local` points it at the backend
(`NEXT_PUBLIC_API_BASE_URL`, defaults to `http://localhost:8000`).

**One environment quirk worth knowing:** `npm install typescript@latest` may
resolve to TypeScript 7, which Next.js's current build doesn't support yet
(it needs the older compiler API). If `npm run dev` fails with an error about
`useTypeScriptCli`, run `npm install -D "typescript@^6"`.

## Deliberate Phase-0/1 simplifications

- **No auth.** Anyone who can reach the dev server can view and file
  reports. Fine for internal testing, not for anything public — add
  Supabase Auth or similar before this goes further than a laptop.
- **No photo upload in the manual form.** The chat bots capture photos;
  the manual form is for text-only entry/correction. Worth adding if manual
  filing turns out to be a primary path rather than a backup one.
- **Light mode only.** The dataviz palette has a validated dark-mode ramp
  too; skipped here to keep scope bounded for an internal tool spun up
  quickly. Straightforward to add later — see `references/palette.md` in
  the dataviz skill.
- **No editing of AI-drafted reports.** The detail page is view-only. Editing
  is really the same underlying feature as manual entry, so it's a natural
  next addition once the form above proves out.
