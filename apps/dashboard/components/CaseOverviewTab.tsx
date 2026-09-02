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
