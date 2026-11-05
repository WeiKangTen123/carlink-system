"use client";

import { usePathname } from "next/navigation";
import { PageSkeleton } from "@/components/PageSkeleton";

/** app/loading.tsx only covers moving between sections; this covers moves
 * inside Cases (the list, New intake, a case), which would otherwise leave
 * the old page up until the server answered. The list gets a skeleton
 * shaped like its table; a case page gets the wide-beside-narrow one (its
 * own app/reports/[id]/loading.tsx takes over once inside it). app/loading.tsx
 * renders this for any /reports address reached from another section. */
export default function Loading() {
  const pathname = usePathname();
  if (pathname === "/reports") return <CasesListSkeleton />;
  if (pathname.startsWith("/reports/") && pathname !== "/reports/new") return <PageSkeleton variant="case" />;
  return <PageSkeleton />;
}

/** The title row, the filter row and a few table rows, as ReportsClient
 * lays them out. */
function CasesListSkeleton() {
  return (
    <div className="page-skeleton" role="status" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }} aria-hidden="true">
        <div className="skeleton-bar skeleton-title" style={{ width: 90 }} />
        <div className="skeleton-bar" style={{ width: 180 }} />
        <div className="skeleton-bar" style={{ width: 120, marginLeft: "auto" }} />
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }} aria-hidden="true">
        <div className="skeleton-bar" style={{ width: 260, height: 22 }} />
        <div className="skeleton-bar" style={{ width: 220, marginLeft: "auto", height: 22 }} />
      </div>
      <div className="card-glass page-skeleton-card">
        <div className="skeleton-bar skeleton-short" aria-hidden="true" />
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="skeleton-bar skeleton-block" aria-hidden="true" style={{ height: 44 }} />
        ))}
        <span className="page-skeleton-label">Loading cases…</span>
      </div>
    </div>
  );
}
