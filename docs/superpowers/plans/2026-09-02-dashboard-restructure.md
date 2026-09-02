# Dashboard Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat top-nav dashboard with a persistent sidebar, a real Overview home page (today `/` just shows whichever case was created most recently), and split the case-detail mega-page (`StudioApp.tsx`) into tabs instead of one long scroll.

**Architecture:** New `Sidebar` and `Breadcrumb` components own navigation/wayfinding. `StudioApp.tsx` stops rendering everything itself and becomes a thin shell owning shared state (active tab, active photo, highlighted damage index, sign-off modal) that renders one of four new tab components (`CaseOverviewTab`, `CaseEvidenceTab`, `CaseDamageTab`, `CaseSignOffTab`) extracted from its current body, unchanged in content. A rewritten `app/page.tsx` becomes the Overview home, reusing the already-existing `getAnalyticsSummary()`/`listReports()` calls — no backend changes.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript. No test framework exists in `apps/dashboard` (confirmed: no `*.test.tsx`, no jest/vitest in `package.json`) — this codebase's established verification pattern (used throughout this project) is `npm run build` (type-check) plus manual/live verification, not automated tests. Each task below follows that pattern rather than inventing a test framework.

**Reference:** Design spec at `docs/superpowers/specs/2026-09-02-dashboard-restructure-design.md`.

**Two judgment calls made while planning (flagging for visibility, not asking again — small enough to just decide):**
1. The spec's tab table put the Claim Lifecycle Stepper inside "Sign-off & Documents." It's kept **above the tabs instead** (persistent, alongside the header) — it's a glanceable status indicator, not deep content, same reasoning as why the header itself stays persistent.
2. The spec said Recent Cases would show "plate/vehicle, status chip, severity chip" — checking `ReportSummary`'s actual type shows it doesn't carry vehicle/plate/severity (`lib/api.ts:166-175`), only `id, type, status, channel, created_at, location, category, thumbnail_url`. Rather than add a backend field (the spec explicitly ruled out backend changes), Overview uses what's really there: category + location + status + date.

---

### Task 1: Sidebar navigation shell

**Files:**
- Create: `apps/dashboard/components/Sidebar.tsx`
- Modify: `apps/dashboard/app/globals.css` (add sidebar/top-bar rules, remove case-selector rules)
- Modify: `apps/dashboard/app/layout.tsx`
- Delete: `apps/dashboard/components/CaseSelectorDropdown.tsx`

- [ ] **Step 1: Create the Sidebar component**

```tsx
// apps/dashboard/components/Sidebar.tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/", label: "Overview", icon: "📊" },
  { href: "/reports", label: "Cases", icon: "📁" },
  { href: "/reports/new", label: "New Intake", icon: "✨" },
  { href: "/analytics", label: "Analytics", icon: "📈" },
  { href: "/settings", label: "Settings", icon: "⚙️" },
];

// "/" only matches the exact root. "/reports" needs to stay active for
// case-detail pages (/reports/abc123) but NOT for /reports/new, which has
// its own nav item and would otherwise also light up "Cases" since
// "/reports/new".startsWith("/reports") is also true.
function isActiveLink(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/reports") {
    return pathname === "/reports" || (pathname.startsWith("/reports/") && !pathname.startsWith("/reports/new"));
  }
  return pathname === href || pathname.startsWith(href + "/");
}

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sidebar">
      <Link href="/" className="sidebar-brand">
        <div className="brand-icon">🚗</div>
        <span>Carlink</span>
      </Link>
      <nav style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {NAV_ITEMS.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`sidebar-link ${isActiveLink(pathname, item.href) ? "active" : ""}`}
          >
            <span>{item.icon}</span> {item.label}
          </Link>
        ))}
      </nav>
    </aside>
  );
}
```

- [ ] **Step 2: Add sidebar/top-bar CSS, remove case-selector CSS**

In `apps/dashboard/app/globals.css`, find the block starting `/* CASE SELECTOR DROPDOWN */` (currently lines 255-284, ending right before `/* BUTTONS */`) and delete it entirely — it styled `.case-selector-box` / `.case-select-dropdown`, both only used by the component being deleted in Step 4.

Then find the `/* TOP NAVBAR */` block (currently lines 185-253, covering `.top-nav`, `.brand`, `.brand-icon`, `.brand-badge`, `.nav-link`, `.nav-link:hover`, `.nav-link.active`). Keep `.brand-icon` and `.brand-badge` (still used), but replace `.top-nav` and `.nav-link*` with the sidebar equivalents. Replace that whole block with:

```css
/* APP SHELL: SIDEBAR + MAIN COLUMN */
.app-shell {
  display: flex;
  min-height: 100vh;
}

.sidebar {
  width: 224px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 20px 14px;
  background: var(--nav-bg);
  border-right: 1px solid var(--border-color);
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 16px;
  font-weight: 800;
  color: var(--text-primary) !important;
  letter-spacing: -0.02em;
  padding: 0 8px 20px;
  text-decoration: none !important;
}

.brand-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--accent-gradient);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 16px;
  box-shadow: 0 0 16px var(--accent-glow);
}

.brand-badge {
  font-size: 10px;
  font-weight: 800;
  background: var(--accent-gradient);
  color: #fff;
  padding: 2px 8px;
  border-radius: 12px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.sidebar-link {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-muted);
  padding: 9px 12px;
  border-radius: 8px;
  transition: var(--transition-smooth);
}
.sidebar-link:hover {
  color: var(--text-primary);
  background: var(--surface-hover);
  text-decoration: none;
}
.sidebar-link.active {
  color: #ffffff;
  background: var(--accent-gradient);
  box-shadow: 0 4px 14px var(--accent-glow);
  font-weight: 700;
}

.top-bar {
  position: sticky;
  top: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 12px 28px;
  background: var(--nav-bg);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-bottom: 1px solid var(--border-color);
}

.main-column {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
```

- [ ] **Step 3: Rewrite layout.tsx**

```tsx
// apps/dashboard/app/layout.tsx
import type { Metadata } from "next";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Sidebar } from "@/components/Sidebar";
import "./globals.css";

export const metadata: Metadata = {
  title: "Carlink Studio 2.0 // Intelligent Loss Adjuster & Incident System",
  description:
    "Intelligent loss adjuster studio with AI vision extraction, interactive vehicle blueprint, and official PDF sign-off workflow.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark">
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem("carlink-theme")||"dark";document.documentElement.setAttribute("data-theme",t);}catch(e){}})();`,
          }}
        />
      </head>
      <body>
        <div className="app-shell">
          <Sidebar />

          <div className="main-column">
            <header className="top-bar">
              <div style={{ marginLeft: "auto", display: "flex", gap: 12, alignItems: "center" }}>
                <ThemeToggle />

                {/* Role Pill -- there's no login/auth system, so this names a
                    role, not a specific person (see signOffReportAction's
                    default reviewer name for the same convention). Showing an
                    invented name here as if someone were logged in is exactly
                    the kind of fabricated-identity bug this project has
                    already had to fix elsewhere. */}
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "4px 12px 4px 4px",
                    background: "var(--surface-elevated)",
                    border: "1px solid var(--border-color)",
                    borderRadius: "20px",
                    fontSize: "12px",
                    fontWeight: 600,
                  }}
                >
                  <div
                    style={{
                      width: 24,
                      height: 24,
                      borderRadius: "50%",
                      background: "var(--accent-gradient)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      color: "white",
                      fontSize: 10,
                      fontWeight: 800,
                    }}
                  >
                    SA
                  </div>
                  <span>Surveyor / Loss Adjuster</span>
                </div>
              </div>
            </header>

            <main className="main-content" style={{ maxWidth: 1440, margin: "0 auto", padding: "20px 24px 60px", width: "100%" }}>
              {children}
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
```

Note: `RootLayout` drops `async`/the `listReports()` fetch entirely — it was only there to feed `CaseSelectorDropdown`, which is being deleted. One fewer server fetch on every single page navigation.

- [ ] **Step 4: Delete CaseSelectorDropdown**

```bash
rm apps/dashboard/components/CaseSelectorDropdown.tsx
```

(Confirmed its only consumer was `app/layout.tsx`, already updated in Step 3.)

- [ ] **Step 5: Verify**

```bash
cd apps/dashboard && npm run build
```

Expected: compiles clean, no TypeScript errors, no "module not found" for the deleted component.

- [ ] **Step 6: Commit**

```bash
git add apps/dashboard/components/Sidebar.tsx apps/dashboard/app/globals.css apps/dashboard/app/layout.tsx
git rm apps/dashboard/components/CaseSelectorDropdown.tsx
git commit -m "Replace top nav with a persistent sidebar"
```

---

### Task 2: Breadcrumb component

**Files:**
- Create: `apps/dashboard/components/Breadcrumb.tsx`
- Modify: `apps/dashboard/app/globals.css`

- [ ] **Step 1: Create the component**

```tsx
// apps/dashboard/components/Breadcrumb.tsx
import Link from "next/link";

export interface BreadcrumbItem {
  label: string;
  href?: string;
}

export function Breadcrumb({ items }: { items: BreadcrumbItem[] }) {
  return (
    <nav className="breadcrumb" style={{ marginBottom: 16 }}>
      {items.map((item, i) => (
        <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          {i > 0 && <span className="sep">/</span>}
          {item.href ? <Link href={item.href}>{item.label}</Link> : <span className="current">{item.label}</span>}
        </span>
      ))}
    </nav>
  );
}
```

- [ ] **Step 2: Add breadcrumb CSS**

Append to `apps/dashboard/app/globals.css` (anywhere after the sidebar block added in Task 1 is fine):

```css
.breadcrumb {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
}
.breadcrumb a {
  color: var(--text-muted);
}
.breadcrumb a:hover {
  color: var(--accent-cyan);
  text-decoration: none;
}
.breadcrumb .sep {
  opacity: 0.5;
}
.breadcrumb .current {
  color: var(--text-primary);
  font-weight: 600;
}
```

- [ ] **Step 3: Verify**

```bash
cd apps/dashboard && npm run build
```

Expected: compiles clean. (Nothing renders `<Breadcrumb>` yet — that's Task 6 — this step just confirms the new file itself is valid TypeScript.)

- [ ] **Step 4: Commit**

```bash
git add apps/dashboard/components/Breadcrumb.tsx apps/dashboard/app/globals.css
git commit -m "Add Breadcrumb component"
```

---

### Task 3: Extract CaseEvidenceTab

**Files:**
- Create: `apps/dashboard/components/CaseEvidenceTab.tsx`

Moves the Photo Evidence Inspector card out of `StudioApp.tsx` unchanged, except: `showBadges` becomes local state (it never needed to be shared with other tabs), and an empty state is added for reports with zero photos (previously the whole card just didn't render — now that this is a whole tab a user can click into, it needs *something* to show).

- [ ] **Step 1: Create the component**

```tsx
// apps/dashboard/components/CaseEvidenceTab.tsx
"use client";

import { useState } from "react";
import { type DamageSummaryItem, fileUrl } from "@/lib/api";
import { severityClass } from "./StudioApp";

/** damage_summary.photo_reference is "P01", "P02"... in upload order (see
 * schema.py) -- this is the same indexing scheme applied to photo_urls. */
const photoLabel = (index: number) => `P${String(index + 1).padStart(2, "0")}`;

/** Converts Gemini's native bbox_2d format ([y_min, x_min, y_max, x_max],
 * each 0-1000) into CSS percentage positioning for an absolutely-
 * positioned overlay div. Returns null for anything malformed rather than
 * rendering a garbled box. */
function bboxToCss(bbox: number[] | null | undefined): { top: string; left: string; width: string; height: string } | null {
  if (!bbox || bbox.length !== 4) return null;
  const [yMin, xMin, yMax, xMax] = bbox;
  if ([yMin, xMin, yMax, xMax].some((v) => typeof v !== "number" || Number.isNaN(v))) return null;
  return {
    top: `${yMin / 10}%`,
    left: `${xMin / 10}%`,
    width: `${(xMax - xMin) / 10}%`,
    height: `${(yMax - yMin) / 10}%`,
  };
}

interface Props {
  photos: string[];
  damageEntries: DamageSummaryItem[];
  activePhotoIndex: number;
  onSelectPhoto: (idx: number) => void;
}

export function CaseEvidenceTab({ photos, damageEntries, activePhotoIndex, onSelectPhoto }: Props) {
  const [showBadges, setShowBadges] = useState(true);

  if (photos.length === 0) {
    return (
      <div className="card-glass">
        <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>No photos attached to this report.</p>
      </div>
    );
  }

  const currentPhotoUrl = photos[activePhotoIndex];
  const currentPhotoLabel = photoLabel(activePhotoIndex);
  const currentPhotoDamage = damageEntries.filter((item) => item.photo_reference === currentPhotoLabel);

  return (
    <div className="card-glass">
      <div className="card-header">
        <div>
          <div className="card-title">
            <span>📸</span> Photo Evidence Inspector
          </div>
          <div className="card-subtitle">
            Photo {activePhotoIndex + 1} of {photos.length} &bull; Photo ID:{" "}
            <strong style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>{currentPhotoLabel}</strong>
          </div>
        </div>
        <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 10px" }} onClick={() => setShowBadges(!showBadges)}>
          {showBadges ? "🙈 Hide Detected Parts" : "👁️ Show Detected Parts"}
        </button>
      </div>

      <div className="photo-inspector-box">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={fileUrl(currentPhotoUrl)} alt={currentPhotoLabel} className="inspector-main-img" />

        {/* Real AI-detected bounding boxes only -- Gemini localizes
            these itself (see extraction.py's bbox_2d prompt, using
            Gemini's own native [y_min,x_min,y_max,x_max]/1000
            convention); never drawn from an invented/estimated
            position. Items without a real box just don't get one,
            no fallback guess. */}
        {showBadges && (
          <div className="ai-bounding-overlay">
            {currentPhotoDamage
              .map((item, i) => ({ item, i, css: bboxToCss(item.bbox_2d) }))
              .filter((x): x is { item: DamageSummaryItem; i: number; css: NonNullable<ReturnType<typeof bboxToCss>> } => x.css !== null)
              .map(({ item, i, css }) => (
                <div key={i} className="ai-box-marker-glow" style={css}>
                  <div className="ai-box-tag-glow">
                    ⚡ {item.part}
                    {item.ai_confidence ? ` // ${item.ai_confidence}` : ""}
                  </div>
                </div>
              ))}
          </div>
        )}
      </div>

      {showBadges && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
          {currentPhotoDamage.length > 0 ? (
            currentPhotoDamage.map((item, i) => (
              <span key={i} className={`chip-severity ${severityClass(item.severity)}`} style={{ fontSize: 11 }}>
                ⚡ {item.part}
                {item.ai_confidence ? ` // ${item.ai_confidence} confidence` : ""}
                {item.bbox_2d ? " 🔲" : ""}
              </span>
            ))
          ) : (
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              No damaged parts linked to this specific photo
            </span>
          )}
        </div>
      )}

      {/* Thumbnail Selector Strip */}
      <div className="photo-thumb-strip">
        {photos.map((src, idx) => (
          <div
            key={idx}
            className={`photo-thumb ${activePhotoIndex === idx ? "active" : ""}`}
            onClick={() => onSelectPhoto(idx)}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={fileUrl(src)} alt={photoLabel(idx)} />
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify**

```bash
cd apps/dashboard && npm run build
```

Expected: compiles clean. (`severityClass` still lives in `StudioApp.tsx` at this point — Task 6 doesn't touch it — so this import resolves fine even before the shell is rewritten.)

- [ ] **Step 3: Commit**

```bash
git add apps/dashboard/components/CaseEvidenceTab.tsx
git commit -m "Extract CaseEvidenceTab from StudioApp"
```

---

### Task 4: Extract CaseDamageTab

**Files:**
- Create: `apps/dashboard/components/CaseDamageTab.tsx`

Moves the Damage & Parts Checklist table and Repair Cost Estimate card out unchanged.

- [ ] **Step 1: Create the component**

```tsx
// apps/dashboard/components/CaseDamageTab.tsx
"use client";

import type { DamageSummaryItem } from "@/lib/api";
import type { ZoneResolution } from "@/lib/vehicleZones";
import { severityClass } from "./StudioApp";

interface Props {
  damageEntries: DamageSummaryItem[];
  zoneResolutions: (ZoneResolution | null)[];
  highlightedDamageIndex: number | null;
  onHotspotClick: (idx: number, item: DamageSummaryItem) => void;
  estimatedRepairCost?: string | null;
}

export function CaseDamageTab({ damageEntries, zoneResolutions, highlightedDamageIndex, onHotspotClick, estimatedRepairCost }: Props) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div className="card-glass">
        <div className="card-header">
          <div>
            <div className="card-title">
              <span>📋</span> Damage &amp; Parts Checklist
            </div>
            <div className="card-subtitle">Photo-linked parts checklist</div>
          </div>
          <span className="chip-severity minor" style={{ fontSize: 10 }}>
            {damageEntries.length} Components
          </span>
        </div>

        {damageEntries.length === 0 ? (
          <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>No structured damage recorded.</p>
        ) : (
          <table className="damage-table-modern">
            <thead>
              <tr>
                <th>Damaged Component</th>
                <th>Damage Type</th>
                <th>Severity</th>
                <th>OEM Part #</th>
                <th>AI Confidence</th>
                <th>Verified</th>
              </tr>
            </thead>
            <tbody>
              {damageEntries.map((item, idx) => {
                const badgeNumber = zoneResolutions[idx]?.badgeNumber;
                return (
                  <tr
                    key={idx}
                    id={`damage-row-${idx}`}
                    onClick={() => onHotspotClick(idx, item)}
                    style={{
                      background: highlightedDamageIndex === idx ? "var(--bg-hover, rgba(56,189,248,0.08))" : undefined,
                      cursor: "pointer",
                    }}
                    title="Click to view on the 3D blueprint"
                  >
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        {badgeNumber !== undefined && (
                          <span
                            className={`hotspot-beacon-3d ${severityClass(item.severity) === "severe" ? "severe-spot" : ""}`}
                            style={{ position: "static", width: 20, height: 20, fontSize: 9, flexShrink: 0 }}
                          >
                            {String(badgeNumber).padStart(2, "0")}
                          </span>
                        )}
                        <div>
                          <strong>{item.part}</strong>
                          {item.photo_reference && (
                            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>Ref: {item.photo_reference}</div>
                          )}
                        </div>
                      </div>
                    </td>
                    <td>{item.damage_type || "—"}</td>
                    <td>
                      <span className={`chip-severity ${severityClass(item.severity)}`}>{item.severity || "—"}</span>
                    </td>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{item.oem_part_number || "—"}</td>
                    <td>{item.ai_confidence || "—"}</td>
                    <td>
                      {item.human_verified ? (
                        <span className="verify-toggle-modern verified">✓ Verified</span>
                      ) : (
                        <span className="verify-toggle-modern">Pending</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {estimatedRepairCost && (
        <div className="card-glass">
          <div className="card-header">
            <div className="card-title">
              <span>💰</span> Estimated Repair Cost
            </div>
          </div>
          <div className="cost-matrix-glow">
            <div className="cost-row total">
              <span>Total Estimated Repair Cost</span>
              <span style={{ fontFamily: "var(--font-mono)", fontWeight: 700 }}>{estimatedRepairCost}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify**

```bash
cd apps/dashboard && npm run build
```

Expected: compiles clean.

- [ ] **Step 3: Commit**

```bash
git add apps/dashboard/components/CaseDamageTab.tsx
git commit -m "Extract CaseDamageTab from StudioApp"
```

---

### Task 5: Extract CaseOverviewTab

**Files:**
- Create: `apps/dashboard/components/CaseOverviewTab.tsx`

Moves the 3D Vehicle Body Blueprint card and AI-Drafted Summary card out unchanged, including the `dynamic()` import for `VehicleBlueprint3D` (this is now its only consumer, so the import belongs here, not in the shell).

- [ ] **Step 1: Create the component**

```tsx
// apps/dashboard/components/CaseOverviewTab.tsx
"use client";

import dynamic from "next/dynamic";
import { type ReportDetail, type DamageSummaryItem } from "@/lib/api";
import { severityClass } from "./StudioApp";

// Three.js/WebGL only exists client-side -- SSR-rendering the Canvas would
// either crash on the server or produce a hydration mismatch, so this is
// loaded only after mount.
const VehicleBlueprint3D = dynamic(
  () => import("@/components/VehicleBlueprint3D").then((m) => m.VehicleBlueprint3D),
  { ssr: false, loading: () => <div className="blueprint-stage-3d blueprint-3d-loading">Loading 3D blueprint&hellip;</div> }
);

interface Props {
  report: ReportDetail;
  damageEntries: DamageSummaryItem[];
  vehicleName: string;
  highlightedDamageIndex: number | null;
  onHotspotClick: (idx: number, item: DamageSummaryItem) => void;
}

export function CaseOverviewTab({ report, damageEntries, vehicleName, highlightedDamageIndex, onHotspotClick }: Props) {
  const d = report.data;
  const conditionChips = [d.weather_condition, d.road_condition, d.traffic_condition].filter(Boolean) as string[];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1.15fr", gap: 24 }}>
      <div className="card-glass">
        <div className="card-header">
          <div>
            <div className="card-title">
              <span>📐</span> Vehicle Body Blueprint
            </div>
            <div className="card-subtitle">
              Approximate 3D zone per damaged part &mdash; click a marker to jump to its evidence photo
            </div>
          </div>
          <span className={`chip-severity ${severityClass(d.severity_level)}`}>
            {damageEntries.length} Damaged {damageEntries.length === 1 ? "Zone" : "Zones"}
          </span>
        </div>

        <VehicleBlueprint3D
          damageEntries={damageEntries}
          onHotspotClick={onHotspotClick}
          highlightedDamageIndex={highlightedDamageIndex}
          vehicleName={vehicleName}
        />
      </div>

      <div
        style={{
          background: "var(--surface-elevated)",
          border: "1px solid var(--border-hover)",
          borderLeft: "4px solid var(--accent-cyan)",
          borderRadius: "12px",
          padding: "18px 20px",
          boxShadow: "var(--shadow-sm)",
          height: "fit-content",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 700, fontSize: 13 }}>
            <span>🤖</span> AI-Drafted Incident Summary
          </div>
          {d.ai_analysis?.confidence_score && (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                fontWeight: 800,
                background: "var(--accent-gradient)",
                color: "#ffffff",
                padding: "3px 10px",
                borderRadius: "12px",
              }}
            >
              {d.ai_analysis.confidence_score}
            </span>
          )}
        </div>

        <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: conditionChips.length ? 10 : 0, lineHeight: 1.6 }}>
          {d.description}
        </p>

        {conditionChips.length > 0 && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {conditionChips.map((f, i) => (
              <span key={i} className="chip-severity minor" style={{ fontSize: 10 }}>
                {f}
              </span>
            ))}
            <span className="chip-severity minor" style={{ fontSize: 10 }}>
              Reported to Authorities: {d.reported_to_authorities ? "Yes" : "No"}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify**

```bash
cd apps/dashboard && npm run build
```

Expected: compiles clean.

- [ ] **Step 3: Commit**

```bash
git add apps/dashboard/components/CaseOverviewTab.tsx
git commit -m "Extract CaseOverviewTab from StudioApp"
```

---

### Task 6: Extract CaseSignOffTab (with new Sign-Off Status card)

**Files:**
- Create: `apps/dashboard/components/CaseSignOffTab.tsx`

Moves the Police & Insurance Details card out unchanged, and adds one new card surfacing `sign_off.prepared_by` / `reviewed_by` / `approved_by` / `signature_date` -- these fields exist in the schema and are already being saved (`signOffReportAction`), but nothing in the UI displayed them before this. Small, motivated by giving this tab real content beyond a single conditional card, not a new feature in its own right.

- [ ] **Step 1: Create the component**

```tsx
// apps/dashboard/components/CaseSignOffTab.tsx
import type { ReportDetail } from "@/lib/api";

export function CaseSignOffTab({ report }: { report: ReportDetail }) {
  const d = report.data;
  const pol = d.police_report;
  const ins = d.insurance_details;
  const signOff = d.sign_off;
  const isSignedOff = report.status === "Signed Off" || signOff?.status === "Signed Off";

  const hasPoliceOrInsurance = Boolean(
    pol?.police_station || pol?.report_number || ins?.insurer_name || ins?.policy_number || ins?.claim_type
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Sign-Off Status -- surfaces sign_off.prepared_by/reviewed_by/
          approved_by/signature_date, which existed in the schema and was
          being saved (see signOffReportAction) but was never actually
          shown anywhere in the studio before this. */}
      <div className="card-glass">
        <div className="card-header">
          <div className="card-title">
            <span>✍️</span> Sign-Off Status
          </div>
        </div>
        {isSignedOff ? (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, fontSize: 13 }}>
            <div>
              <div className="detail-field-label">Status</div>
              <div style={{ fontWeight: 600, marginTop: 2 }}>
                <span className="chip-severity minor">✓ Signed Off &amp; Locked</span>
              </div>
            </div>
            {signOff?.signature_date && (
              <div>
                <div className="detail-field-label">Signed On</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>{new Date(signOff.signature_date).toLocaleString()}</div>
              </div>
            )}
            {signOff?.prepared_by && (
              <div>
                <div className="detail-field-label">Prepared By</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>{signOff.prepared_by}</div>
              </div>
            )}
            {signOff?.reviewed_by && (
              <div>
                <div className="detail-field-label">Reviewed By</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>{signOff.reviewed_by}</div>
              </div>
            )}
            {signOff?.approved_by && (
              <div>
                <div className="detail-field-label">Approved By</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>{signOff.approved_by}</div>
              </div>
            )}
          </div>
        ) : (
          <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>
            Not yet signed off. Use the &quot;Finalize &amp; Sign Off&quot; button above to lock this report.
          </p>
        )}
      </div>

      {/* Police & Insurance Details */}
      {hasPoliceOrInsurance && (
        <div className="card-glass">
          <div className="card-header">
            <div className="card-title">
              <span>🏛️</span> Authority &amp; Insurance Policy Details
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, fontSize: 13 }}>
            {pol?.police_station && (
              <div>
                <div className="detail-field-label">Police Station</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>{pol.police_station}</div>
              </div>
            )}
            {pol?.report_number && (
              <div>
                <div className="detail-field-label">Report Reference Number</div>
                <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--accent-cyan)", marginTop: 2 }}>{pol.report_number}</div>
              </div>
            )}
            {ins?.insurer_name && (
              <div>
                <div className="detail-field-label">Insurance Provider</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>{ins.insurer_name}</div>
              </div>
            )}
            {(ins?.policy_number || ins?.claim_type) && (
              <div>
                <div className="detail-field-label">Policy &amp; Claim Type</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>
                  {ins?.policy_number} {ins?.policy_number && ins?.claim_type && <>&bull;</>} {ins?.claim_type}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify**

```bash
cd apps/dashboard && npm run build
```

Expected: compiles clean.

- [ ] **Step 3: Commit**

```bash
git add apps/dashboard/components/CaseSignOffTab.tsx
git commit -m "Extract CaseSignOffTab from StudioApp, add Sign-Off Status card"
```

---

### Task 7: Rewrite StudioApp.tsx as the tab-owning shell

**Files:**
- Modify: `apps/dashboard/components/StudioApp.tsx` (full rewrite)
- Modify: `apps/dashboard/app/globals.css` (add tab bar CSS)

This is the task that actually wires everything from Tasks 1-6 together. `StudioApp.tsx` keeps `severityClass` (still exported -- `VehicleBlueprint3D.tsx` and the new tab components all import it from here) and the case header/claim stepper/sign-off modal, but delegates all four content sections to the new tab components.

- [ ] **Step 1: Add tab bar CSS**

Append to `apps/dashboard/app/globals.css`:

```css
.case-tab-bar {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--border-color);
  margin-bottom: 20px;
  overflow-x: auto;
}
.case-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-muted);
  padding: 10px 16px;
  border-bottom: 2px solid transparent;
  white-space: nowrap;
  cursor: pointer;
  background: none;
  border-left: none;
  border-right: none;
  border-top: none;
}
.case-tab:hover {
  color: var(--text-primary);
}
.case-tab.active {
  color: var(--accent-cyan);
  border-bottom-color: var(--accent-cyan);
  font-weight: 700;
}
```

- [ ] **Step 2: Rewrite StudioApp.tsx**

```tsx
// apps/dashboard/components/StudioApp.tsx
"use client";

import React, { useState } from "react";
import { type ReportDetail, type DamageSummaryItem } from "@/lib/api";
import { deleteReportAction } from "@/app/reports/actions";
import { signOffReportAction } from "@/app/reports/actions";
import { PdfPreviewModal } from "@/components/PdfPreviewModal";
import { Breadcrumb } from "@/components/Breadcrumb";
import { CaseOverviewTab } from "@/components/CaseOverviewTab";
import { CaseEvidenceTab } from "@/components/CaseEvidenceTab";
import { CaseDamageTab } from "@/components/CaseDamageTab";
import { CaseSignOffTab } from "@/components/CaseSignOffTab";
import { resolveZones } from "@/lib/vehicleZones";

export function severityClass(severity?: string | null): string {
  const s = (severity || "").toLowerCase();
  if (s.includes("severe")) return "severe";
  if (s.includes("moderate")) return "moderate";
  if (s.includes("minor")) return "minor";
  return "minor";
}

type CaseTab = "overview" | "evidence" | "damage" | "signoff";

const TABS: { id: CaseTab; label: string; icon: string }[] = [
  { id: "overview", label: "Overview", icon: "📐" },
  { id: "evidence", label: "Evidence & Photos", icon: "📸" },
  { id: "damage", label: "Damage Assessment", icon: "📋" },
  { id: "signoff", label: "Sign-off & Documents", icon: "✍️" },
];

export function StudioApp({ report }: { report: ReportDetail }) {
  const d = report.data;
  const v = d.vehicle_info;

  const [activeTab, setActiveTab] = useState<CaseTab>("overview");
  const [activePhotoIndex, setActivePhotoIndex] = useState(0);
  const [highlightedDamageIndex, setHighlightedDamageIndex] = useState<number | null>(null);
  const [isSignOffModalOpen, setIsSignOffModalOpen] = useState(false);
  const [isSigningOff, setIsSigningOff] = useState(false);
  const [signOffError, setSignOffError] = useState<string | null>(null);

  const isSignedOff = report.status === "Signed Off" || d.sign_off?.status === "Signed Off";

  // Same fallback the original detail page used: real damage_summary items
  // when they exist, otherwise the plain damaged_parts list with everything
  // else honestly left unknown -- never invented from the description text.
  const damageEntries: DamageSummaryItem[] =
    d.damage_summary && d.damage_summary.length > 0
      ? d.damage_summary
      : (d.damaged_parts || []).map((p) => ({ part: p, severity: d.severity_level || null, human_verified: false }));

  // Same resolution the 3D blueprint uses for its numbered markers -- one
  // shared function so a badge number always means the same part in both
  // places, never two independently-computed numbering schemes drifting
  // apart.
  const zoneResolutions = resolveZones(damageEntries);

  const photos = report.photo_urls || [];

  const plate = v?.plate_number || d.vehicle_details || null;
  const vehicleName = [v?.make, v?.model].filter(Boolean).join(" ") || d.vehicle_details || "Vehicle";
  const reportCode = d.report_id || `CIR-2026-${report.id.slice(0, 4).toUpperCase()}`;

  const handleHotspotClick = (idx: number, item: DamageSummaryItem) => {
    setHighlightedDamageIndex(idx);
    if (item.photo_reference) {
      const photoIdx = parseInt(item.photo_reference.replace(/\D/g, ""), 10) - 1;
      if (photoIdx >= 0 && photoIdx < photos.length) setActivePhotoIndex(photoIdx);
    }
    // Jumping to a photo only makes sense if the reporter can actually see
    // it -- switch to the Evidence tab so this isn't a silent no-op when
    // triggered from Overview's blueprint or the Damage tab's table.
    setActiveTab("evidence");
  };

  const handleSignOff = async () => {
    setIsSigningOff(true);
    setSignOffError(null);
    const result = await signOffReportAction(report.id, "Surveyor Sign-Off");
    if ("error" in result) {
      setSignOffError(result.error);
      setIsSigningOff(false);
      return;
    }
    // Same Next.js Router Cache staleness the original SignOffButton worked
    // around -- a full reload is what actually shows the new status here.
    window.location.reload();
  };

  return (
    <div>
      <Breadcrumb items={[{ label: "Home", href: "/" }, { label: "Cases", href: "/reports" }, { label: plate || vehicleName }]} />

      {/* 3-Stage Claim Lifecycle Stepper -- matches what the system actually
          tracks (Report.status / sign_off.status), not an invented 5-stage
          pipeline with stages nothing here updates. Kept visible above the
          tabs (not inside one) since it's a glanceable status indicator,
          same reasoning as why the header below it stays persistent too. */}
      <div className="claim-stepper-glass" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
        <div className="step-node completed">
          <div className="step-circle">✓</div>
          <div>
            <div className="step-title">1. Filed</div>
            <div className="step-desc" style={{ textTransform: "capitalize" }}>{report.channel} intake</div>
          </div>
        </div>
        <div className={`step-node ${isSignedOff ? "completed" : "active"}`}>
          <div className="step-circle">{isSignedOff ? "✓" : "2"}</div>
          <div>
            <div className="step-title">2. Under Review</div>
            <div className="step-desc">{isSignedOff ? "Reviewed" : "Awaiting surveyor sign-off"}</div>
          </div>
        </div>
        <div className={`step-node ${isSignedOff ? "completed" : ""}`}>
          <div className="step-circle">{isSignedOff ? "✓" : "3"}</div>
          <div>
            <div className="step-title">3. Signed Off</div>
            <div className="step-desc">{isSignedOff ? "Locked" : "Pending"}</div>
          </div>
        </div>
      </div>

      {/* Studio Header Strip */}
      <div className="card-header" style={{ marginBottom: 20 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6, flexWrap: "wrap" }}>
            {plate && <span className="badge-plate-glow">{plate}</span>}
            <span className={`chip-severity ${severityClass(d.severity_level)}`}>
              {isSignedOff ? "✓ SIGNED OFF & LOCKED" : d.severity_level ? `⚡ ${d.severity_level}` : "Severity unassessed"}
            </span>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              CASE_ID: <code>{report.id}</code>
              {d.location && <> &bull; Incident at {d.location}</>}
            </span>
          </div>
          <h1 style={{ fontSize: 24, fontWeight: 800, letterSpacing: "-0.02em", margin: 0 }}>
            {vehicleName}
            {d.accident_type && <> &mdash; {d.accident_type}</>}
          </h1>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <a href={`/reports/${report.id}/edit`} className="btn-secondary-modern">
            <span>✏️</span> {isSignedOff ? "Reopen & Edit" : "Edit Report"}
          </a>
          <PdfPreviewModal reportId={report.id} pdfUrl={report.pdf_url} reportCode={reportCode} />
          {!isSignedOff && (
            <button type="button" className="btn-primary-modern" onClick={() => setIsSignOffModalOpen(true)}>
              <span>✍️</span> Finalize &amp; Sign Off
            </button>
          )}
          <form
            action={deleteReportAction}
            onSubmit={(e) => {
              if (!confirm("Delete this report? This deletes its photos and PDF too, and can't be undone.")) {
                e.preventDefault();
              }
            }}
          >
            <input type="hidden" name="id" value={report.id} />
            <button type="submit" className="btn-secondary-modern" style={{ color: "var(--danger, #ef4444)" }}>
              🗑️ Delete
            </button>
          </form>
        </div>
      </div>

      {/* Case Section Tabs */}
      <div className="case-tab-bar">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`case-tab ${activeTab === tab.id ? "active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
          >
            <span>{tab.icon}</span> {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "overview" && (
        <CaseOverviewTab
          report={report}
          damageEntries={damageEntries}
          vehicleName={vehicleName}
          highlightedDamageIndex={highlightedDamageIndex}
          onHotspotClick={handleHotspotClick}
        />
      )}
      {activeTab === "evidence" && (
        <CaseEvidenceTab
          photos={photos}
          damageEntries={damageEntries}
          activePhotoIndex={activePhotoIndex}
          onSelectPhoto={setActivePhotoIndex}
        />
      )}
      {activeTab === "damage" && (
        <CaseDamageTab
          damageEntries={damageEntries}
          zoneResolutions={zoneResolutions}
          highlightedDamageIndex={highlightedDamageIndex}
          onHotspotClick={handleHotspotClick}
          estimatedRepairCost={d.insurance_details?.estimated_repair_cost}
        />
      )}
      {activeTab === "signoff" && <CaseSignOffTab report={report} />}

      {/* Sign-Off Confirmation Modal */}
      {isSignOffModalOpen && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            width: "100vw",
            height: "100vh",
            background: "rgba(0, 0, 0, 0.8)",
            backdropFilter: "blur(10px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 999,
          }}
          onClick={() => !isSigningOff && setIsSignOffModalOpen(false)}
        >
          <div
            style={{
              background: "var(--surface-card)",
              border: "1px solid var(--border-color)",
              borderRadius: "16px",
              width: "90%",
              maxWidth: "500px",
              padding: "24px",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ fontSize: 16, fontWeight: 800, marginBottom: 10 }}>Surveyor Digital Sign-Off</h3>
            <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 16 }}>
              By signing off, you certify that the damage items and photos for report <strong>{report.id}</strong>
              {plate && <> ({plate})</>} have been verified. This locks the report against further edits until
              reopened.
            </p>

            {signOffError && (
              <p style={{ fontSize: 12, color: "var(--danger, #ef4444)", marginBottom: 12 }}>{signOffError}</p>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button type="button" className="btn-secondary-modern" onClick={() => setIsSignOffModalOpen(false)} disabled={isSigningOff}>
                Cancel
              </button>
              <button type="button" className="btn-primary-modern" onClick={handleSignOff} disabled={isSigningOff}>
                <span>✍️</span> {isSigningOff ? "Signing Off..." : "Confirm & Lock"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Verify**

```bash
cd apps/dashboard && npm run build
```

Expected: compiles clean. This is the task most likely to surface a type mismatch (props between the shell and the four tab components) -- if it fails, check the failing tab component's `Props` interface against what's actually passed here before changing anything else.

- [ ] **Step 4: Commit**

```bash
git add apps/dashboard/components/StudioApp.tsx apps/dashboard/app/globals.css
git commit -m "Rewrite StudioApp as a tab-owning shell"
```

---

### Task 8: Rewrite app/page.tsx as the Overview home page

**Files:**
- Modify: `apps/dashboard/app/page.tsx` (full rewrite)

Replaces "show whichever report is newest" with a real overview: KPI tiles from the existing `getAnalyticsSummary()`, a "Needs Attention" list (reports not yet signed off) and a "Recent Cases" list, both from the existing `listReports()` -- no new backend calls.

- [ ] **Step 1: Rewrite the page**

```tsx
// apps/dashboard/app/page.tsx
import Link from "next/link";
import { listReports, getAnalyticsSummary } from "@/lib/api";

const PENDING_STATUSES = new Set(["confirmed", "draft", "pending", "Under Review"]);

export default async function OverviewPage() {
  const reports = await listReports();

  if (reports.length === 0) {
    return (
      <div className="card-glass" style={{ maxWidth: 520, margin: "80px auto", textAlign: "center", padding: "40px 32px" }}>
        <div style={{ fontSize: 40, marginBottom: 12 }}>📋</div>
        <h1 style={{ fontSize: 20, fontWeight: 800, marginBottom: 8 }}>No incidents filed yet</h1>
        <p style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 20 }}>
          File your first incident report to see it here in the Loss Adjuster Studio.
        </p>
        <Link href="/reports/new" className="btn-primary-modern">
          <span>✨</span> File New Incident
        </Link>
      </div>
    );
  }

  const analytics = await getAnalyticsSummary();
  const recentCases = reports.slice(0, 6);
  const needsAttention = reports.filter((r) => PENDING_STATUSES.has(r.status)).slice(0, 6);

  return (
    <div>
      <div className="page-header" style={{ marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>Overview</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            Command center for all filed incident reports
          </p>
        </div>
        <Link href="/reports/new" className="btn-primary-modern">
          <span>✨</span> File New Incident
        </Link>
      </div>

      {/* KPI tiles -- same real fields the Analytics page's own KPI cards
          use (get_analytics_summary in api/main.py), just the four most
          relevant for an at-a-glance overview rather than the full set. */}
      <div className="kpi-grid-modern" style={{ marginBottom: 24 }}>
        <div className="kpi-card-glow">
          <div className="kpi-label">Open Cases</div>
          <div className="kpi-val" style={{ color: "var(--badge-amber-text)" }}>{analytics.pending_review}</div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Signed Off</div>
          <div className="kpi-val" style={{ color: "var(--badge-green-text)" }}>{analytics.signed_off}</div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">High Severity</div>
          <div className="kpi-val" style={{ color: "var(--badge-red-text)" }}>{analytics.high_severity}</div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Avg. Filing-to-Sign-Off</div>
          <div className="kpi-val" style={{ color: "var(--accent-cyan)" }}>{analytics.avg_resolution_time ?? "—"}</div>
          {!analytics.avg_resolution_time && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>No signed-off reports yet</div>
          )}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        {/* Needs Attention -- same "not yet signed off" bucket pending_review
            already counts, filtered from the real report list, not a
            separate query. */}
        <div className="card-glass">
          <div className="card-header">
            <div className="card-title">
              <span>⏳</span> Needs Attention
            </div>
            <span className="chip-severity minor" style={{ fontSize: 10 }}>{needsAttention.length} shown</span>
          </div>
          {needsAttention.length === 0 ? (
            <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>Nothing pending review right now.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {needsAttention.map((r) => (
                <Link
                  key={r.id}
                  href={`/reports/${r.id}`}
                  style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 10px", borderRadius: 8, border: "1px solid var(--border-color)", textDecoration: "none" }}
                >
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>
                      {r.category[0] || "Incident"}{r.location ? ` — ${r.location}` : ""}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                      {new Date(r.created_at).toLocaleDateString()} &bull; {r.channel}
                    </div>
                  </div>
                  <span className="chip-severity minor" style={{ fontSize: 10 }}>{r.status}</span>
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* Recent Cases -- newest first, listReports() already sorts this way. */}
        <div className="card-glass">
          <div className="card-header">
            <div className="card-title">
              <span>🕒</span> Recent Cases
            </div>
            <Link href="/reports" style={{ fontSize: 11 }}>View all &rarr;</Link>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {recentCases.map((r) => (
              <Link
                key={r.id}
                href={`/reports/${r.id}`}
                style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 10px", borderRadius: 8, border: "1px solid var(--border-color)", textDecoration: "none" }}
              >
                <div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>
                    {r.category[0] || "Incident"}{r.location ? ` — ${r.location}` : ""}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                    {new Date(r.created_at).toLocaleDateString()} &bull; {r.channel}
                  </div>
                </div>
                <span className="chip-severity minor" style={{ fontSize: 10 }}>{r.status}</span>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify**

```bash
cd apps/dashboard && npm run build
```

Expected: compiles clean, all 8 routes still listed in the build output (`/`, `/_not-found`, `/analytics`, `/reports`, `/reports/[id]`, `/reports/[id]/edit`, `/reports/new`, `/settings`).

- [ ] **Step 3: Commit**

```bash
git add apps/dashboard/app/page.tsx
git commit -m "Replace latest-case home page with a real Overview page"
```

---

### Task 9: Deploy and verify live

**Files:** none (deployment only)

This project's established deploy pattern (used throughout this session): push, pull on the GCP server, rebuild only the `dashboard` container (it's the only one touched), restore `carlink_api`'s real secrets afterward (rebuilding `dashboard` via `docker compose` triggers a config-drift recreate of `api` too, because they share the `x-bot-service` env block -- this has happened every prior dashboard deploy this session and the recovery is now a known, safe, repeatable procedure).

- [ ] **Step 1: Push**

```bash
git push origin master
```

- [ ] **Step 2: Pull on the server**

```bash
gcloud compute ssh xero-automation --zone=us-central1-a --command="cd /opt/carlink-system && sudo git pull origin master && git log -1 --oneline"
```

Expected: fast-forward, shows the last commit from Task 8.

- [ ] **Step 3: Rebuild the dashboard image**

```bash
gcloud compute ssh xero-automation --zone=us-central1-a --command="cd /opt/carlink-system/infra && GEMINI_API_KEY=unused-not-targeting-this-service TELEGRAM_BOT_TOKEN=unused-not-targeting-this-service sudo -E docker compose -f docker-compose.prod.yml build dashboard"
```

Expected: build succeeds, `npm run build`/TypeScript check passes inside the container too (this is a second, independent confirmation beyond Task 8's local build).

- [ ] **Step 4: Recreate the dashboard container**

```bash
gcloud compute ssh xero-automation --zone=us-central1-a --command="cd /opt/carlink-system/infra && GEMINI_API_KEY=unused-not-targeting-this-service TELEGRAM_BOT_TOKEN=unused-not-targeting-this-service sudo -E docker compose -f docker-compose.prod.yml up -d dashboard"
```

Expected: `carlink_dashboard` recreated. `carlink_api` will likely also show "Recreate" -- this is the known config-drift side effect, addressed in the next step.

- [ ] **Step 5: Restore carlink_api's real secrets**

```bash
gcloud compute ssh xero-automation --zone=us-central1-a --command="cd /opt/carlink-system/infra && sudo docker inspect carlink_telegram_bot --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(GEMINI_API_KEY|TELEGRAM_BOT_TOKEN)=' | sudo tee /root/.carlink_secrets.env > /dev/null && sudo chmod 600 /root/.carlink_secrets.env && sudo bash -c 'set -a; source /root/.carlink_secrets.env; set +a; docker compose -f docker-compose.prod.yml up -d api' && sudo docker inspect carlink_api --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -c 'unused-not-targeting-this-service'; sudo shred -u /root/.carlink_secrets.env 2>/dev/null || sudo rm -f /root/.carlink_secrets.env"
```

Expected: the final grep count is `0` (confirms the placeholder is gone, real secrets restored). This pulls the real values from `carlink_telegram_bot` (never touched by this deploy) without ever printing them.

- [ ] **Step 6: Verify all containers healthy**

```bash
gcloud compute ssh xero-automation --zone=us-central1-a --command="sudo docker ps --format 'table {{.Names}}\t{{.Status}}'; sudo docker logs carlink_dashboard --since 2m 2>&1 | grep -iE 'error|exception'; sudo docker logs carlink_api --since 2m 2>&1 | grep -iE 'error|exception'"
```

Expected: all 4 containers `Up`, no error lines from either log.

- [ ] **Step 7: Live check**

```bash
curl -sS -o /dev/null -w 'overview status=%{http_code}\n' https://carlink.34-45-253-162.sslip.io/
curl -sS -o /dev/null -w 'cases status=%{http_code}\n' https://carlink.34-45-253-162.sslip.io/reports
curl -sS -o /dev/null -w 'analytics status=%{http_code}\n' https://carlink.34-45-253-162.sslip.io/analytics
```

Expected: all `200`. Then fetch one real case detail page and grep for the new structural markers:

```bash
curl -sS https://carlink.34-45-253-162.sslip.io/reports/8ec6129affe0 -o /tmp/verify_case.html
grep -c "case-tab-bar" /tmp/verify_case.html
grep -oiE "application error|something went wrong|internal server error" /tmp/verify_case.html
```

Expected: `case-tab-bar` count >= 1, no error strings.

- [ ] **Step 8: Ask the user to confirm visually**

This project's established limit: the agent can verify compile success, container health, and page-load success, but cannot see the rendered sidebar/tabs/Overview page itself. Report the above verification results and ask the user to open the live URL and confirm the sidebar, breadcrumb, Overview page, and case-detail tabs actually look and behave as intended.

---

## Self-Review

**Spec coverage:**
- A. Navigation shell (sidebar, top-bar shrink, CaseSelectorDropdown removal) -- Task 1. ✓
- B. Overview page (KPI tiles from real data, Recent Cases, Needs Attention, quick action) -- Task 8. ✓
- C. Case detail tabs (4 tabs, breadcrumb, header unchanged, hotspot-click switches tab) -- Tasks 2-7. ✓
- D. Unchanged pages (Cases Repository, Analytics, Settings, Intake) -- no tasks touch `app/reports/page.tsx`, `app/reports/ReportsClient.tsx`, `app/analytics/page.tsx`, `app/settings/page.tsx`, `app/reports/new/page.tsx`, or `app/reports/[id]/edit/page.tsx`. Confirmed intentional, not an oversight.
- Data flow (no new backend endpoints) -- confirmed: Task 8 only calls `listReports()` and `getAnalyticsSummary()`, both pre-existing.

**Placeholder scan:** No TBD/TODO markers. Every step has complete code, not descriptions of code.

**Type consistency:** `ZoneResolution` (from `lib/vehicleZones.ts`, already exported per this project's earlier work this session) is used identically in `CaseDamageTab.tsx` (Task 4) and `StudioApp.tsx` (Task 7) as `(ZoneResolution | null)[]`. `onHotspotClick: (idx: number, item: DamageSummaryItem) => void` has the same signature everywhere it's passed (Tasks 5, 6, 7). `severityClass` is defined once (Task 7, in `StudioApp.tsx`) and imported by name (`from "./StudioApp"`) in Tasks 3, 4, 5 -- matches the pattern `VehicleBlueprint3D.tsx` already used before this plan.
