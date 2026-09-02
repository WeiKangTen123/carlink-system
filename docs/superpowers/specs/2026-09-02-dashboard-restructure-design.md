# Dashboard restructure: sidebar navigation + case-detail tabs

## Problem

The dashboard reads as a set of pages, not a system. Specifically:

1. **No real home.** `/` doesn't show an overview -- it loads whichever case was created most recently (`app/page.tsx`) and renders its full detail view. There's no answer anywhere to "how many open cases are there" or "what needs my attention."
2. **No wayfinding.** Once inside a case there's no breadcrumb back to the cases list.
3. **The case-detail page is one long scroll.** `StudioApp.tsx` (541 lines) renders the 3D blueprint, AI summary, photo inspector, damage table, repair cost estimate, claim stepper, and sign-off panel all stacked on one page.
4. **A global case switcher competes with the actual case list.** `CaseSelectorDropdown` sits in the header on every page, duplicating what the Cases Repository table already does.

Out of scope: visual theme (colors, glassmorphic cards, cyan-glow accents). That's already established and validated (dataviz-skill accessibility pass earlier this project). This is a structural/information-architecture change only.

## Approaches considered

**Navigation shell** -- persistent left sidebar vs. keeping the horizontal top nav and only adding an Overview page + breadcrumbs. Chose the **sidebar**: reads as the more established "real product" pattern (Linear/Stripe/Vercel), and gives room to grow without crowding a top bar that's already carrying brand + case switcher + theme + role.

**Case-detail structure** -- in-page tabs vs. a second nested sidebar specific to the open case. Chose **tabs**: familiar (GitHub PR tabs, Stripe object pages), and avoids stacking two sidebars on screen at once for what is fundamentally one case's own sub-sections.

Both decisions were validated with the user via wireframe-level mockups (superpowers brainstorming visual companion) before this doc was written, not assumed.

## Design

### A. Navigation shell

Persistent left sidebar on every page: brand mark, then **Overview** · **Cases** · **New Intake** · **Analytics** · **Settings**, current section highlighted. Top bar shrinks to: breadcrumb (left), theme toggle + role pill (right).

`CaseSelectorDropdown` is removed entirely (confirmed: only consumer is `app/layout.tsx`, safe to delete the component along with its usage). Case switching now happens through the Cases list in the sidebar, not a second parallel mechanism in the header.

### B. Overview page (new home, replaces "show latest case")

Route: `/` (replaces current `app/page.tsx` behavior). Server component, same data-fetching shape as today's page but calling the existing `/api/analytics/summary` endpoint (`get_analytics_summary()` in `api/main.py`) instead of `getReport(latestId)`.

Stat tiles, using fields that endpoint **already computes** -- no new backend work:
- Open Cases -- `pending_review`
- Signed Off -- `signed_off`
- High Severity -- `high_severity`
- Avg Resolution Time -- the existing `avg_resolution_time` field

Below the tiles:
- **Recent Cases** -- last 5-8 reports (`listReports()`, already sorted newest-first, just sliced), each showing plate/vehicle, status chip, severity chip, linking to `/reports/[id]`.
- **Needs Attention** -- reports whose status is in the "confirmed/pending/Under Review" bucket (same bucket `pending_review` already counts), so it's a filtered view of the same real data, not a new query.
- Quick action button: "✨ File New Incident" -> `/reports/new`.

Empty state (no reports at all) keeps today's existing empty-state card and copy from `app/page.tsx`.

### C. Case detail page (tabs)

Route stays `/reports/[id]`. The case header (plate badge, severity chip, case ID, Edit/PDF/Sign-off/Delete actions) is unchanged -- it already works. Below it: a breadcrumb (`Home > Cases > {plate or vehicle name}`) and four tabs:

| Tab | Content (moved from StudioApp.tsx as-is) |
|---|---|
| Overview | 3D vehicle blueprint (`VehicleBlueprint3D`) + AI-drafted incident summary |
| Evidence & Photos | Photo inspector / gallery, AI bounding-box overlay |
| Damage Assessment | Damage & Parts Checklist table, repair cost estimate |
| Sign-off & Documents | Claim lifecycle stepper, sign-off panel/modal, PDF preview |

Component breakdown: `StudioApp.tsx` (currently one 541-line component owning everything) splits into:
- `CaseDetailPage` (or keep the name `StudioApp`) -- owns shared state that spans tabs: `activeTab`, `damageEntries`, `activePhotoIndex`, `highlightedDamageIndex`, sign-off modal state. Renders the header, breadcrumb, tab bar, and the active tab's content component.
- `CaseOverviewTab`, `CaseEvidenceTab`, `CaseDamageTab`, `CaseSignOffTab` -- one component each, receiving only the props/state they actually need (e.g. `CaseEvidenceTab` doesn't need `damageEntries` beyond what's already photo-filtered, `CaseDamageTab` doesn't need `activePhotoIndex`). This mirrors the same "smaller, well-bounded units" principle already used for `VehicleBlueprint3D`/`lib/vehicleZones.ts` earlier in this project.

Clicking a 3D blueprint hotspot or a damage-table row (in the Damage tab) that's meant to jump to its evidence photo needs to also switch `activeTab` to Evidence & Photos, not just scroll -- `handleHotspotClick` gains one line for that.

### D. Unchanged

Cases Repository (`/reports`), Analytics (`/analytics`), Settings (`/settings`), Incident Intake (`/reports/new`) -- no structural changes. Cases Repository already reads as a proper system table.

## Data flow

No new backend endpoints. Overview reuses `GET /analytics/summary` (already exists, already real) and `GET /reports` (already exists). No schema changes.

## Testing

Manual verification against real live data (per this project's established practice) after `npm run build` passes locally:
- Overview tiles match what `/analytics` already shows for the same underlying counts (cross-check, not a new source of truth).
- Each case-detail tab renders its moved content correctly for a report that has real data in that area (e.g. verify Sign-off tab against an already-signed-off case, Damage tab against SLK 3063 Z).
- Hotspot click from Overview tab correctly switches to Evidence tab and scrolls/highlights the right photo.
- Breadcrumb and sidebar active-state correct on every route.
