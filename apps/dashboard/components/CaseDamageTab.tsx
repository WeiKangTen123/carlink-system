"use client";

import type { DamageSummaryItem } from "@/lib/api";
import type { ZoneResolution } from "@/lib/vehicleZones";
import { severityClass } from "./StudioApp";

interface Props {
  damageEntries: DamageSummaryItem[];
  zoneResolutions: (ZoneResolution | null)[];
  highlightedDamageIndex: number | null;
  onHotspotClick: (idx: number, item: DamageSummaryItem) => void;
}

// Repair cost used to live here, but it belongs with the rest of the
// commercial/claim picture (estimated vs final approved, adjuster,
// workshop) in CaseAssessmentTab -- showing it in both places would just
// be the same number in two spots with no added context.
export function CaseDamageTab({ damageEntries, zoneResolutions, highlightedDamageIndex, onHotspotClick }: Props) {
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
    </div>
  );
}
