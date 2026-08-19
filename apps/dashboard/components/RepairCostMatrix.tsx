"use client";

import React from "react";

export type CostItem = {
  label: string;
  amount: string;
  total?: boolean;
};

interface RepairCostMatrixProps {
  items?: CostItem[];
  currency?: string;
  totalAmount?: string;
}

const DEFAULT_COSTS: CostItem[] = [
  { label: "OEM Replacement Parts (Bumper Cover + Tailgate)", amount: "SGD 1,850.00" },
  { label: "Panel Beating & Realignment Labor (6.0 hrs @ $85/hr)", amount: "SGD 510.00" },
  { label: "Spray Painting (Pearl Metallic • 3 Panels)", amount: "SGD 750.00" },
  { label: "Parking Sensor Calibration & Diagnostics", amount: "SGD 180.00" },
  { label: "Total Estimated Insurance Claim", amount: "SGD 3,290.00", total: true },
];

export function RepairCostMatrix({
  items = DEFAULT_COSTS,
}: RepairCostMatrixProps) {
  return (
    <div className="cost-matrix-glow">
      {items.map((item, idx) => (
        <div key={idx} className={`cost-row ${item.total ? "total" : ""}`}>
          <span>{item.label}</span>
          <span style={{ fontFamily: "var(--font-mono)", fontWeight: 700 }}>
            {item.amount}
          </span>
        </div>
      ))}
    </div>
  );
}
