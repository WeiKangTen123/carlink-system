"use client";

import React from "react";
import { useRouter, usePathname } from "next/navigation";
import { type ReportSummary } from "@/lib/api";

export function CaseSelectorDropdown({ reports }: { reports: ReportSummary[] }) {
  const router = useRouter();
  const pathname = usePathname();

  if (reports.length === 0) return null;

  // "/" shows the newest report (see app/page.tsx), so treat its id as
  // selected there too -- otherwise the dropdown would show nothing chosen
  // on the home page even though a real case is on screen.
  const match = pathname.match(/^\/reports\/([^/]+)/);
  const currentId = match ? match[1] : reports[0].id;

  const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const selectedId = e.target.value;
    if (selectedId) router.push(`/reports/${selectedId}`);
  };

  return (
    <div className="case-selector-box" title="Select Case / Incident">
      <span style={{ color: "var(--accent-cyan)", fontSize: "11px", fontWeight: 800 }}>CASE:</span>
      <select className="case-select-dropdown" value={currentId} onChange={handleChange}>
        {reports.map((r) => (
          <option key={r.id} value={r.id}>
            {r.location ? `${r.location} — ` : ""}
            {r.id} {r.category?.length ? `• ${r.category[0]}` : ""}
          </option>
        ))}
      </select>
    </div>
  );
}
