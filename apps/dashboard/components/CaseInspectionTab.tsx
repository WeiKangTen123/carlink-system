"use client";

import React, { Component, useMemo, type ErrorInfo, type ReactNode } from "react";
import dynamic from "next/dynamic";
import { Box, AlertTriangle, RotateCcw, Wrench } from "lucide-react";
import { type ReportDetail, type DamageSummaryItem } from "@/lib/api";
import type { ZoneResolution } from "@/lib/vehicleZones";
import { severityClass } from "@/lib/caseFields";
import { CaseEvidenceTab } from "./CaseEvidenceTab";
import { CaseDamageTab } from "./CaseDamageTab";

interface BlueprintErrorBoundaryProps {
  children: ReactNode;
}

interface BlueprintErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class BlueprintErrorBoundary extends Component<
  BlueprintErrorBoundaryProps,
  BlueprintErrorBoundaryState
> {
  constructor(props: BlueprintErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): BlueprintErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("BlueprintErrorBoundary caught a WebGL/Three.js render exception:", error, errorInfo);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div
          className="blueprint-stage-3d"
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            padding: "24px",
            textAlign: "center",
            gap: 12,
            background: "var(--surface-elevated)",
            border: "1px dashed rgba(239, 68, 68, 0.4)",
          }}
        >
          <div
            style={{
              width: 44,
              height: 44,
              borderRadius: "50%",
              background: "rgba(239, 68, 68, 0.12)",
              border: "1px solid rgba(239, 68, 68, 0.3)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#ef4444",
            }}
          >
            <AlertTriangle style={{ width: 20, height: 20 }} />
          </div>

          <div style={{ maxWidth: 380 }}>
            <div
              style={{
                fontSize: 13,
                fontWeight: 700,
                color: "var(--text-primary)",
                marginBottom: 6,
              }}
            >
              3D Blueprint Engine Offline
            </div>
            <p
              style={{
                fontSize: 12,
                color: "var(--text-muted)",
                lineHeight: 1.5,
                margin: 0,
              }}
            >
              3D Blueprint rendering unavailable on this device. Damage items are available in the parts checklist below.
            </p>
          </div>

          <button
            type="button"
            onClick={this.handleRetry}
            className="btn-secondary-modern"
            style={{
              fontSize: 11,
              padding: "6px 14px",
              marginTop: 4,
            }}
          >
            <RotateCcw style={{ width: 12, height: 12 }} /> Retry 3D Engine
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

// Three.js/WebGL only exists client-side -- SSR-rendering the Canvas would
// either crash on the server or produce a hydration mismatch, so this is
// loaded only after mount.
const VehicleBlueprint3D = dynamic(
  () => import("@/components/VehicleBlueprint3D").then((m) => m.VehicleBlueprint3D),
  { ssr: false, loading: () => <div className="blueprint-stage-3d blueprint-3d-loading">Loading 3D blueprint&hellip;</div> }
);

interface Props {
  report: ReportDetail;
  isSignedOff: boolean;
  damageEntries: DamageSummaryItem[];
  zoneResolutions: (ZoneResolution | null)[];
  vehicleName: string;
  photos: string[];
  photoThumbs?: string[];
  activePhotoIndex: number;
  highlightedDamageIndex: number | null;
  selectedZone: string | null;
  onSelectPhoto: (idx: number) => void;
  onHotspotClick: (idx: number, item: DamageSummaryItem) => void;
  onSelectZone: (zoneId: string | null) => void;
}

/** The three views of a case's damage -- 3D blueprint, evidence photos,
 * and the parts checklist -- are all views of the SAME damage_summary
 * data, and reviewing a case means constantly cross-referencing between
 * them ("where is it → what does it look like → is it on the list").
 *
 * They were briefly split across three separate tabs, which forced
 * clicking a blueprint part to silently jump you to a different tab just
 * to show the photo it selected. That tab-switch was the tell: these are
 * linked views, not separate sections, so they live on one screen and
 * cross-highlight instead. Tabs are still used for the genuinely
 * different mode (sign-off/paperwork), just not to separate these three.
 */
export function CaseInspectionTab({
  report,
  isSignedOff,
  damageEntries,
  zoneResolutions,
  vehicleName,
  photos,
  photoThumbs,
  activePhotoIndex,
  highlightedDamageIndex,
  selectedZone,
  onSelectPhoto,
  onHotspotClick,
  onSelectZone,
}: Props) {
  const d = report.data;

  const nonExteriorItems = useMemo(() => {
    const rx = /airbag|seat|dashboard|interior|radiator|condenser|exhaust|fuel\s*tank|suspension|steering|subframe|battery|engine|transmission|brake|catalytic/i;
    return damageEntries
      .map((item, idx) => ({ item, idx }))
      .filter(({ item, idx }) => zoneResolutions[idx] === null || rx.test(item.part));
  }, [damageEntries, zoneResolutions]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Top row: the two visual views, side by side, always both visible */}
      <div className="inspection-split">
        <div className="card-glass">
          <div className="card-header">
            <div>
              <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Box style={{ width: 16, height: 16, color: "var(--accent-primary)" }} /> Vehicle Body Blueprint
              </div>
              <div className="card-subtitle">
                Click a marker to highlight it in the photo and parts list below
              </div>
            </div>
            <span className={`chip-severity ${severityClass(d.severity_level)}`}>
              {damageEntries.length} Damaged {damageEntries.length === 1 ? "Zone" : "Zones"}
            </span>
          </div>

          <BlueprintErrorBoundary>
            <VehicleBlueprint3D
              damageEntries={damageEntries}
              onHotspotClick={onHotspotClick}
              highlightedDamageIndex={highlightedDamageIndex}
              vehicleName={vehicleName}
              bodyType={report.data.vehicle_info?.body_type}
              selectedZone={selectedZone}
              onZoneSelect={onSelectZone}
            />
          </BlueprintErrorBoundary>

          {/* Non-Exterior & Mechanical Components tray */}
          {nonExteriorItems.length > 0 && (
            <div
              style={{
                marginTop: 16,
                paddingTop: 14,
                borderTop: "1px solid var(--border-color)",
                display: "flex",
                flexDirection: "column",
                gap: 10,
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 8,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--text-secondary)",
                    letterSpacing: "0.02em",
                  }}
                >
                  <Wrench style={{ width: 14, height: 14, color: "var(--accent-primary)" }} />
                  <span>Non-Exterior &amp; Mechanical Components</span>
                </div>
                <span
                  style={{
                    fontSize: 11,
                    color: "var(--text-muted)",
                    background: "var(--surface-elevated, rgba(255,255,255,0.05))",
                    padding: "2px 8px",
                    borderRadius: 12,
                    border: "1px solid var(--border-color)",
                  }}
                >
                  {nonExteriorItems.length} {nonExteriorItems.length === 1 ? "component" : "components"}
                </span>
              </div>

              <div
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: 8,
                }}
              >
                {nonExteriorItems.map(({ item, idx }) => {
                  const isHighlighted = highlightedDamageIndex === idx;
                  const sevClass = severityClass(item.severity);
                  const res = zoneResolutions[idx];
                  const badgeNumber = res?.badgeNumber ?? (idx + 1);

                  return (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => {
                        onHotspotClick(idx, item);
                        const rowEl = document.getElementById(`damage-row-${idx}`);
                        rowEl?.scrollIntoView({ behavior: "smooth", block: "center" });
                      }}
                      title={`Click to highlight ${item.part} in the checklist`}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "6px 10px",
                        borderRadius: 8,
                        fontSize: 12,
                        background: isHighlighted
                          ? "var(--accent-glow, rgba(37, 99, 235, 0.15))"
                          : "var(--surface-elevated, rgba(255, 255, 255, 0.04))",
                        border: isHighlighted
                          ? "1px solid var(--accent-primary)"
                          : "1px solid var(--border-color)",
                        color: "var(--text-primary)",
                        cursor: "pointer",
                        transition: "all 0.15s ease",
                        boxShadow: isHighlighted ? "0 0 10px rgba(37, 99, 235, 0.25)" : "none",
                      }}
                    >
                      <span
                        className={`hotspot-beacon-3d ${sevClass === "severe" ? "severe-spot" : ""}`}
                        style={{
                          position: "static",
                          width: 18,
                          height: 18,
                          fontSize: 9,
                          flexShrink: 0,
                          animation: "none",
                          display: "inline-flex",
                          alignItems: "center",
                          justifyContent: "center",
                        }}
                      >
                        {String(badgeNumber).padStart(2, "0")}
                      </span>
                      <span style={{ fontWeight: 600 }}>{item.part}</span>
                      <span
                        className={`chip-severity ${sevClass}`}
                        style={{ fontSize: 9, padding: "1px 6px" }}
                      >
                        {item.severity || "—"}
                      </span>
                      {item.ai_confidence && (
                        <span
                          style={{
                            fontSize: 10,
                            color: "var(--text-muted)",
                            fontFamily: "var(--font-mono, monospace)",
                          }}
                        >
                          {typeof item.ai_confidence === "number"
                            ? `${Math.round(item.ai_confidence * 100)}%`
                            : item.ai_confidence}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        <CaseEvidenceTab
          photos={photos}
          photoThumbs={photoThumbs}
          damageEntries={damageEntries}
          activePhotoIndex={activePhotoIndex}
          selectedZone={selectedZone}
          onSelectPhoto={onSelectPhoto}
          onSelectZone={onSelectZone}
        />
      </div>

      {/* Full width: the checklist needs the room for its six columns */}
      <CaseDamageTab
        reportId={report.id}
        isSignedOff={isSignedOff}
        damageEntries={damageEntries}
        zoneResolutions={zoneResolutions}
        highlightedDamageIndex={highlightedDamageIndex}
        onHotspotClick={onHotspotClick}
      />
    </div>
  );
}
