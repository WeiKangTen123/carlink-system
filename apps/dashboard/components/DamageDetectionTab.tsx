"use client";

import React, { Component, useMemo, useState, useEffect, type ErrorInfo, type ReactNode } from "react";
import dynamic from "next/dynamic";
import { Box, AlertTriangle, RotateCcw, Wrench } from "lucide-react";
import { damagePartLabel, type AnnexAPartItem, type DamageItemsResponse, type ReportDetail, type DamageSummaryItem } from "@/lib/api";
import type { ZoneResolution } from "@/lib/vehicleZones";
import { severityClass } from "@/lib/caseFields";
import { CaseEvidenceTab } from "./CaseEvidenceTab";
import { DetectedPartsTable } from "./DetectedPartsTable";

/** "27 Feb, 10:16" in Singapore time, for the one-line status strip (the
 * Case record drawer has the full date). h23 for the same reason as
 * caseFields.formatSgtDateTime: Node and browsers disagree on midnight
 * under hour12:false. */
function formatSgtShort(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-SG", {
    timeZone: "Asia/Singapore",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
}

interface BlueprintErrorBoundaryProps {
  children: ReactNode;
}

interface BlueprintErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class BlueprintErrorBoundary extends Component<BlueprintErrorBoundaryProps, BlueprintErrorBoundaryState> {
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
            <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)", marginBottom: 6 }}>3D blueprint unavailable</div>
            <p style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5, margin: 0 }}>
              The 3D model can&apos;t be drawn on this device. The detected parts are in the table below.
            </p>
          </div>

          <button type="button" onClick={this.handleRetry} className="btn-secondary-modern" style={{ fontSize: 11, padding: "6px 14px", marginTop: 4 }}>
            <RotateCcw style={{ width: 12, height: 12 }} /> Try again
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
const VehicleBlueprint3D = dynamic(() => import("@/components/VehicleBlueprint3D").then((m) => m.VehicleBlueprint3D), {
  ssr: false,
  loading: () => <div className="blueprint-stage-3d blueprint-3d-loading">Loading 3D blueprint…</div>,
});

interface Props {
  report: ReportDetail;
  isSignedOff: boolean;
  canEdit: boolean;
  /** The detected parts as the store holds them (fresh after every change). */
  damageEntries: DamageSummaryItem[];
  /** The same parts, with an identity that only changes when a part, its
   * severity or its photo changes: the 3D blueprint resets its camera and
   * rebuilds its materials on a new array, which must not happen on every
   * verified tick or Annex A save. */
  blueprintEntries: DamageSummaryItem[];
  zoneResolutions: (ZoneResolution | null)[];
  vehicleName: string;
  photos: string[];
  photoThumbs?: string[];
  photoPreviews?: string[];
  activePhotoIndex: number;
  highlightedDamageIndex: number | null;
  selectedZone: string | null;
  onSelectPhoto: (idx: number) => void;
  onHotspotClick: (idx: number, item: DamageSummaryItem) => void;
  onSelectZone: (zoneId: string | null) => void;
  /** Annex A as saved, for the "n lines, m priced" cell per part. */
  annexAItems: AnnexAPartItem[];
  onDamageItemsApplied: (res: DamageItemsResponse) => void;
  onPartRemoved?: (idx: number) => void;
  onGoToAnnexA: () => void;
  /** This tab is the one on screen (it stays mounted behind the others). */
  isActive: boolean;
}

/** Tab 1, Damage detection: the three views of a case's damage -- 3D
 * blueprint, photos, and the detected parts table -- are all views of the
 * SAME damage_summary, and reviewing a case means constantly
 * cross-referencing between them ("where is it -> what does it look like
 * -> is it on the list"), so they live on one screen and cross-highlight.
 * Owns damage_summary and the photo links; never shows a price. From
 * CaseInspectionTab.tsx, 2026-11-04. */
export function DamageDetectionTab({
  report,
  isSignedOff,
  canEdit,
  damageEntries,
  blueprintEntries,
  zoneResolutions,
  vehicleName,
  photos,
  photoThumbs,
  photoPreviews,
  activePhotoIndex,
  highlightedDamageIndex,
  selectedZone,
  onSelectPhoto,
  onHotspotClick,
  onSelectZone,
  annexAItems,
  onDamageItemsApplied,
  onPartRemoved,
  onGoToAnnexA,
  isActive,
}: Props) {
  const d = report.data;

  const nonExteriorItems = useMemo(() => {
    const rx = /airbag|seat|dashboard|interior|radiator|condenser|exhaust|fuel\s*tank|suspension|steering|subframe|battery|engine|transmission|brake|catalytic/i;
    return damageEntries.map((item, idx) => ({ item, idx })).filter(({ item, idx }) => zoneResolutions[idx] === null || rx.test(item.part));
  }, [damageEntries, zoneResolutions]);

  // Defer Three.js / WebGL canvas mount until after initial DOM paint and hydration.
  // This guarantees opening the case transitions instantaneously while the
  // heavy WebGL shader compilation and 3D asset parsing occur asynchronously.
  const [shouldMount3D, setShouldMount3D] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      setShouldMount3D(true);
    }, 60);
    return () => clearTimeout(timer);
  }, []);

  // The last AI read as the run log recorded it (round 3, B3): which photos
  // went and when. Null when the AI never read this case (filled in by
  // hand, or filed before the log existed), and absent from an older API.
  const lastRead = report.ai_last_read ?? null;
  const partsCount = damageEntries.length;
  const verifiedCount = damageEntries.filter((e) => e.human_verified).length;
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* What the AI read and found. The parts are counted from the record
          as it is now (the surveyor may have added or removed some since);
          the read itself comes from the run log, so with no log entry the
          line falls back to counts alone, with no time invented. */}
      <div className="detection-status-strip">
        <span suppressHydrationWarning>
          {lastRead ? (
            <>
              AI read {lastRead.photos_read} of {plural(lastRead.photos_total, "photo")} on {formatSgtShort(lastRead.at)}
              {typeof lastRead.seconds === "number" ? ` (${Math.round(lastRead.seconds)} s)` : ""}. {plural(partsCount, "part")} found,{" "}
              {verifiedCount} verified.
            </>
          ) : (
            <>
              {plural(partsCount, "part")} detected from {plural(photos.length, "photo")}.
            </>
          )}
        </span>
        {/* Phase 2: [Add photos] (POST /reports/{id}/photos) and [Re-run
            detection] (POST /reports/{id}/redetect) go here, with the
            redetect status line. Not built in this PR. */}
      </div>

      {/* Top row: the two visual views, side by side, always both visible */}
      <div className="inspection-split">
        <div className="card-glass">
          <div className="card-header">
            <div>
              <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Box style={{ width: 16, height: 16, color: "var(--accent-primary)" }} /> Vehicle blueprint
              </div>
              <div className="card-subtitle">Click a marker to highlight it in the photos and the parts table below</div>
            </div>
            <span className={`chip-severity ${severityClass(d.severity_level)}`}>
              {damageEntries.length} damaged {damageEntries.length === 1 ? "part" : "parts"}
            </span>
          </div>

          <BlueprintErrorBoundary>
            {shouldMount3D ? (
              <VehicleBlueprint3D
                damageEntries={blueprintEntries}
                onHotspotClick={onHotspotClick}
                highlightedDamageIndex={highlightedDamageIndex}
                vehicleName={vehicleName}
                bodyType={report.data.vehicle_info?.body_type}
                selectedZone={selectedZone}
                onZoneSelect={onSelectZone}
              />
            ) : (
              <div className="blueprint-stage-3d blueprint-3d-loading">Loading 3D blueprint…</div>
            )}
          </BlueprintErrorBoundary>

          {/* Parts the model has no panel for */}
          {nonExteriorItems.length > 0 && (
            <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--border-color)", display: "flex", flexDirection: "column", gap: 10 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", letterSpacing: "0.02em" }}>
                  <Wrench style={{ width: 14, height: 14, color: "var(--accent-primary)" }} />
                  <span>Not on the model</span>
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
                  {nonExteriorItems.length} {nonExteriorItems.length === 1 ? "part" : "parts"}
                </span>
              </div>

              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {nonExteriorItems.map(({ item, idx }) => {
                  const isHighlighted = highlightedDamageIndex === idx;
                  const sevClass = severityClass(item.severity);
                  const res = zoneResolutions[idx];
                  const badgeNumber = res?.badgeNumber ?? idx + 1;

                  return (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => {
                        onHotspotClick(idx, item);
                        const rowEl = document.getElementById(`damage-row-${idx}`);
                        rowEl?.scrollIntoView({ behavior: "smooth", block: "center" });
                      }}
                      title={`Click to highlight ${damagePartLabel(item)} in the parts table`}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "6px 10px",
                        borderRadius: 8,
                        fontSize: 12,
                        background: isHighlighted ? "var(--accent-glow, rgba(37, 99, 235, 0.15))" : "var(--surface-elevated, rgba(255, 255, 255, 0.04))",
                        border: isHighlighted ? "1px solid var(--accent-primary)" : "1px solid var(--border-color)",
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
                      <span style={{ fontWeight: 600 }}>{damagePartLabel(item)}</span>
                      <span className={`chip-severity ${sevClass}`} style={{ fontSize: 9, padding: "1px 6px" }}>
                        {item.severity || "—"}
                      </span>
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
          photoPreviews={photoPreviews}
          damageEntries={damageEntries}
          activePhotoIndex={activePhotoIndex}
          selectedZone={selectedZone}
          onSelectPhoto={onSelectPhoto}
          onSelectZone={onSelectZone}
          keyboardActive={isActive}
        />
      </div>

      {/* Full width: the table needs the room for its columns */}
      <DetectedPartsTable
        reportId={report.id}
        readOnly={isSignedOff || !canEdit}
        damageEntries={damageEntries}
        zoneResolutions={zoneResolutions}
        highlightedDamageIndex={highlightedDamageIndex}
        onHotspotClick={onHotspotClick}
        photos={photos}
        photoThumbs={photoThumbs}
        onSelectPhoto={onSelectPhoto}
        annexAItems={annexAItems}
        onApplied={onDamageItemsApplied}
        onRemoved={onPartRemoved}
        onGoToAnnexA={onGoToAnnexA}
      />
    </div>
  );
}
