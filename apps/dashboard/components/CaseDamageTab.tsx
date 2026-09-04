"use client";

import { useState } from "react";
import type { DamageSummaryItem } from "@/lib/api";
import type { ZoneResolution } from "@/lib/vehicleZones";
import { reviewDamageItemAction } from "@/app/reports/actions";
import { severityClass } from "@/lib/caseFields";

interface Props {
  reportId: string;
  isSignedOff: boolean;
  damageEntries: DamageSummaryItem[];
  zoneResolutions: (ZoneResolution | null)[];
  highlightedDamageIndex: number | null;
  onHotspotClick: (idx: number, item: DamageSummaryItem) => void;
}

// Repair cost used to live here, but it belongs with the rest of the
// commercial/claim picture (estimated vs final approved, adjuster,
// workshop) in CaseAssessmentTab -- showing it in both places would just
// be the same number in two spots with no added context.
export function CaseDamageTab({
  reportId,
  isSignedOff,
  damageEntries,
  zoneResolutions,
  highlightedDamageIndex,
  onHotspotClick,
}: Props) {
  // Local overlay on top of the server data so a toggle feels instant.
  // Reverted on failure (see runPatch) rather than left showing a change
  // that never actually persisted.
  const [verifiedOverride, setVerifiedOverride] = useState<Record<number, boolean>>({});
  const [oemOverride, setOemOverride] = useState<Record<number, string>>({});
  const [savingIdx, setSavingIdx] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isVerified = (idx: number, item: DamageSummaryItem) =>
    verifiedOverride[idx] ?? Boolean(item.human_verified);
  const oemValue = (idx: number, item: DamageSummaryItem) =>
    oemOverride[idx] ?? (item.oem_part_number || "");

  const toggleVerified = async (idx: number, item: DamageSummaryItem) => {
    if (isSignedOff) return;
    const next = !isVerified(idx, item);
    setVerifiedOverride((s) => ({ ...s, [idx]: next }));
    setSavingIdx(idx);
    setError(null);
    const result = await reviewDamageItemAction(reportId, idx, { human_verified: next });
    setSavingIdx(null);
    if ("error" in result) {
      setVerifiedOverride((s) => ({ ...s, [idx]: !next }));
      setError(result.error);
    }
  };

  const saveOem = async (idx: number, item: DamageSummaryItem, value: string) => {
    if (isSignedOff) return;
    const original = item.oem_part_number || "";
    if (value.trim() === original.trim()) return; // nothing actually changed
    setSavingIdx(idx);
    setError(null);
    const result = await reviewDamageItemAction(reportId, idx, { oem_part_number: value });
    setSavingIdx(null);
    if ("error" in result) {
      setOemOverride((s) => ({ ...s, [idx]: original }));
      setError(result.error);
    }
  };

  const verifiedCount = damageEntries.filter((item, idx) => isVerified(idx, item)).length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div className="card-glass">
        <div className="card-header">
          <div>
            <div className="card-title">
              <span>📋</span> Damage &amp; Parts Checklist
            </div>
            <div className="card-subtitle">
              {isSignedOff
                ? "Locked — reopen the report to change verification"
                : "Tick each part you've verified, and record its OEM number"}
            </div>
          </div>
          <span
            className={`chip-severity ${verifiedCount === damageEntries.length && damageEntries.length > 0 ? "minor" : "moderate"}`}
            style={{ fontSize: 10 }}
          >
            {verifiedCount} / {damageEntries.length} Verified
          </span>
        </div>

        {error && (
          <p style={{ fontSize: 12, color: "var(--danger, #ef4444)", marginTop: 0, marginBottom: 12 }}>{error}</p>
        )}

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
                const verified = isVerified(idx, item);
                return (
                  <tr
                    key={idx}
                    id={`damage-row-${idx}`}
                    onClick={() => onHotspotClick(idx, item)}
                    style={{
                      background: highlightedDamageIndex === idx ? "var(--bg-hover, rgba(56,189,248,0.08))" : undefined,
                      cursor: "pointer",
                      opacity: savingIdx === idx ? 0.6 : 1,
                    }}
                    title="Click to highlight on the 3D blueprint"
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
                    {/* stopPropagation throughout: the row itself is a click
                        target (highlights the part on the blueprint), so
                        without this, typing a part number or ticking the box
                        would also fire the row's own handler. */}
                    <td onClick={(e) => e.stopPropagation()}>
                      {isSignedOff ? (
                        <span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{item.oem_part_number || "—"}</span>
                      ) : (
                        <input
                          type="text"
                          className="oem-input"
                          placeholder="—"
                          value={oemValue(idx, item)}
                          onChange={(e) => setOemOverride((s) => ({ ...s, [idx]: e.target.value }))}
                          onBlur={(e) => saveOem(idx, item, e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                          }}
                        />
                      )}
                    </td>
                    <td>{item.ai_confidence || "—"}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <button
                        type="button"
                        className={`verify-toggle-modern ${verified ? "verified" : ""}`}
                        onClick={() => toggleVerified(idx, item)}
                        disabled={isSignedOff || savingIdx === idx}
                        style={{ cursor: isSignedOff ? "default" : "pointer", border: "none" }}
                        title={isSignedOff ? "Report is locked" : verified ? "Mark as unverified" : "Mark as verified"}
                      >
                        {verified ? "✓ Verified" : "Pending"}
                      </button>
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
