"use client";

import React from "react";
import { useRouter } from "next/navigation";

export type CaseOption = {
  id: string;
  plate: string;
  vehicle: string;
};

interface CaseSelectorDropdownProps {
  currentCaseId?: string;
  cases?: CaseOption[];
}

const DEFAULT_CASES: CaseOption[] = [
  { id: "slk-3063-z", plate: "SLK 3063 Z", vehicle: "Honda Vezel 1.5 Hybrid" },
  { id: "vay-4821", plate: "VAY 4821", vehicle: "Honda Civic 1.5 RS" },
  { id: "wx-8888-a", plate: "WX 8888 A", vehicle: "Toyota Hilux 2.8" },
];

export function CaseSelectorDropdown({
  currentCaseId,
  cases = DEFAULT_CASES,
}: CaseSelectorDropdownProps) {
  const router = useRouter();

  const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const selectedId = e.target.value;
    if (selectedId) {
      router.push(`/reports/${selectedId}`);
    }
  };

  return (
    <div className="case-selector-box" title="Select Case / Incident">
      <span style={{ color: "var(--accent-cyan)", fontSize: "11px", fontWeight: 800 }}>CASE:</span>
      <select
        className="case-select-dropdown"
        value={currentCaseId || (cases[0]?.id ?? "")}
        onChange={handleChange}
      >
        {cases.map((c) => (
          <option key={c.id} value={c.id}>
            {c.plate} &bull; {c.vehicle}
          </option>
        ))}
      </select>
    </div>
  );
}
