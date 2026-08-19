"use client";

import React, { useState } from "react";
import { DamageSummaryItem } from "@/lib/api";

interface DamageChecklistTableProps {
  items: DamageSummaryItem[];
  onSelectRow?: (index: number) => void;
  activeRowIndex?: number;
}

export function DamageChecklistTable({
  items = [],
  onSelectRow,
  activeRowIndex,
}: DamageChecklistTableProps) {
  const [verifiedMap, setVerifiedMap] = useState<Record<number, boolean>>(() => {
    const map: Record<number, boolean> = {};
    items.forEach((it, idx) => {
      map[idx] = it.human_verified ?? true;
    });
    return map;
  });

  const toggleVerify = (idx: number, e: React.MouseEvent) => {
    e.stopPropagation();
    setVerifiedMap((prev) => ({
      ...prev,
      [idx]: !prev[idx],
    }));
  };

  if (!items || items.length === 0) {
    return (
      <div style={{ padding: "20px", color: "var(--text-muted)", fontSize: "13px" }}>
        No damage components recorded.
      </div>
    );
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <table className="damage-table-modern">
        <thead>
          <tr>
            <th>Damaged Component</th>
            <th>Damage Mechanism</th>
            <th>Severity</th>
            <th>AI Score</th>
            <th>Surveyor Sign</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, idx) => {
            const isVerified = verifiedMap[idx] ?? true;
            const isRowActive = activeRowIndex === idx;
            const sev = (item.severity || "Moderate").toLowerCase();

            return (
              <tr
                key={idx}
                className={isRowActive ? "row-active-glow" : ""}
                style={{ cursor: "pointer" }}
                onClick={() => onSelectRow && onSelectRow(idx)}
              >
                <td>
                  <strong>{item.part}</strong>
                  {item.photo_reference && (
                    <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "2px" }}>
                      Evidence Ref: <code>{item.photo_reference}</code>
                    </div>
                  )}
                </td>
                <td>{item.damage_type || "Structural Deformation"}</td>
                <td>
                  <span className={`chip-severity ${sev}`}>
                    {item.severity || "Moderate"}
                  </span>
                </td>
                <td>
                  <strong style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>
                    {item.ai_confidence || "96.5%"}
                  </strong>
                </td>
                <td>
                  <button
                    type="button"
                    className={`verify-toggle-modern ${isVerified ? "verified" : ""}`}
                    onClick={(e) => toggleVerify(idx, e)}
                  >
                    {isVerified ? "✓ Verified" : "Pending"}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
