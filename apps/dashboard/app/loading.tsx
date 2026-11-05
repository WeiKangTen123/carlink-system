"use client";

import { usePathname } from "next/navigation";
import { PageSkeleton, PublicPageSkeleton } from "@/components/PageSkeleton";
import { isPublicPage } from "@/lib/publicPages";
import ReportsLoading from "@/app/reports/loading";

/** Shown while the server renders a page reached from another section
 * (Overview, Cases, Analytics...). AppShell draws the public pages without
 * the sidebar, so they get a skeleton in their own style; the same path
 * check as AppShell keeps the two in step. The Overview and the Cases
 * pages get skeletons shaped like them, so the page doesn't jump when the
 * real one arrives. */
export default function Loading() {
  const pathname = usePathname();
  if (isPublicPage(pathname)) return <PublicPageSkeleton />;
  if (pathname === "/") return <OverviewSkeleton />;
  if (pathname.startsWith("/reports")) return <ReportsLoading />;
  return <PageSkeleton />;
}

/** The four tiles and the oldest-open-cases card, as app/page.tsx lays
 * them out. */
function OverviewSkeleton() {
  return (
    <div className="page-skeleton" role="status">
      <div className="skeleton-bar skeleton-title" aria-hidden="true" style={{ marginBottom: 24 }} />
      <div className="kpi-grid-modern" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="kpi-card-glow">
            <div className="skeleton-bar skeleton-short" />
            <div className="skeleton-bar skeleton-title" style={{ marginTop: 12, height: 28 }} />
          </div>
        ))}
      </div>
      <div className="card-glass page-skeleton-card">
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className="skeleton-bar" aria-hidden="true" />
        ))}
        <span className="page-skeleton-label">Loading…</span>
      </div>
    </div>
  );
}
