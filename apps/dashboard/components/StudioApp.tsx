"use client";

import React, { useState } from "react";
import dynamic from "next/dynamic";
import { type ReportDetail, type DamageSummaryItem, fileUrl } from "@/lib/api";
import { deleteReportAction } from "@/app/reports/actions";
import { signOffReportAction } from "@/app/reports/actions";
import { PdfPreviewModal } from "@/components/PdfPreviewModal";

// Three.js/WebGL only exists client-side -- SSR-rendering the Canvas would
// either crash on the server or produce a hydration mismatch, so this is
// loaded only after mount, same as the rest of the studio's client-only UI.
const VehicleBlueprint3D = dynamic(
  () => import("@/components/VehicleBlueprint3D").then((m) => m.VehicleBlueprint3D),
  { ssr: false, loading: () => <div className="blueprint-stage-3d blueprint-3d-loading">Loading 3D blueprint&hellip;</div> }
);

/** damage_summary.photo_reference is "P01", "P02"... in upload order (see
 * schema.py) -- this is the same indexing scheme applied to photo_urls. */
const photoLabel = (index: number) => `P${String(index + 1).padStart(2, "0")}`;

/** Converts Gemini's native bbox_2d format ([y_min, x_min, y_max, x_max],
 * each 0-1000) into CSS percentage positioning for an absolutely-
 * positioned overlay div. Returns null for anything malformed rather than
 * rendering a garbled box. */
function bboxToCss(bbox: number[] | null | undefined): { top: string; left: string; width: string; height: string } | null {
  if (!bbox || bbox.length !== 4) return null;
  const [yMin, xMin, yMax, xMax] = bbox;
  if ([yMin, xMin, yMax, xMax].some((v) => typeof v !== "number" || Number.isNaN(v))) return null;
  return {
    top: `${yMin / 10}%`,
    left: `${xMin / 10}%`,
    width: `${(xMax - xMin) / 10}%`,
    height: `${(yMax - yMin) / 10}%`,
  };
}

export function severityClass(severity?: string | null): string {
  const s = (severity || "").toLowerCase();
  if (s.includes("severe")) return "severe";
  if (s.includes("moderate")) return "moderate";
  if (s.includes("minor")) return "minor";
  return "minor";
}

export function StudioApp({ report }: { report: ReportDetail }) {
  const d = report.data;
  const v = d.vehicle_info;
  const pol = d.police_report;
  const ins = d.insurance_details;

  const [activePhotoIndex, setActivePhotoIndex] = useState(0);
  const [showBadges, setShowBadges] = useState(true);
  const [highlightedDamageIndex, setHighlightedDamageIndex] = useState<number | null>(null);
  const [isSignOffModalOpen, setIsSignOffModalOpen] = useState(false);
  const [isSigningOff, setIsSigningOff] = useState(false);
  const [signOffError, setSignOffError] = useState<string | null>(null);

  const isSignedOff = report.status === "Signed Off" || d.sign_off?.status === "Signed Off";

  // Same fallback the original detail page used: real damage_summary items
  // when they exist, otherwise the plain damaged_parts list with everything
  // else honestly left unknown -- never invented from the description text.
  const damageEntries: DamageSummaryItem[] =
    d.damage_summary && d.damage_summary.length > 0
      ? d.damage_summary
      : (d.damaged_parts || []).map((p) => ({ part: p, severity: d.severity_level || null, human_verified: false }));

  const photos = report.photo_urls || [];
  const currentPhotoUrl = photos[activePhotoIndex];
  const currentPhotoLabel = photoLabel(activePhotoIndex);
  const currentPhotoDamage = damageEntries.filter((item) => item.photo_reference === currentPhotoLabel);

  const plate = v?.plate_number || d.vehicle_details || null;
  const vehicleName = [v?.make, v?.model].filter(Boolean).join(" ") || d.vehicle_details || "Vehicle";
  const reportCode = d.report_id || `CIR-2026-${report.id.slice(0, 4).toUpperCase()}`;

  const conditionChips = [
    d.weather_condition,
    d.road_condition,
    d.traffic_condition,
  ].filter(Boolean) as string[];

  const handleHotspotClick = (idx: number, item: DamageSummaryItem) => {
    setHighlightedDamageIndex(idx);
    if (item.photo_reference) {
      const photoIdx = parseInt(item.photo_reference.replace(/\D/g, ""), 10) - 1;
      if (photoIdx >= 0 && photoIdx < photos.length) setActivePhotoIndex(photoIdx);
    }
    document.getElementById(`damage-row-${idx}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  const handleSignOff = async () => {
    setIsSigningOff(true);
    setSignOffError(null);
    const result = await signOffReportAction(report.id, "Surveyor Sign-Off");
    if ("error" in result) {
      setSignOffError(result.error);
      setIsSigningOff(false);
      return;
    }
    // Same Next.js Router Cache staleness the original SignOffButton worked
    // around -- a full reload is what actually shows the new status here.
    window.location.reload();
  };

  return (
    <div>
      {/* 3-Stage Claim Lifecycle Stepper -- matches what the system actually
          tracks (Report.status / sign_off.status), not an invented 5-stage
          pipeline with stages nothing here updates. */}
      <div className="claim-stepper-glass" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
        <div className="step-node completed">
          <div className="step-circle">✓</div>
          <div>
            <div className="step-title">1. Filed</div>
            <div className="step-desc" style={{ textTransform: "capitalize" }}>{report.channel} intake</div>
          </div>
        </div>
        <div className={`step-node ${isSignedOff ? "completed" : "active"}`}>
          <div className="step-circle">{isSignedOff ? "✓" : "2"}</div>
          <div>
            <div className="step-title">2. Under Review</div>
            <div className="step-desc">{isSignedOff ? "Reviewed" : "Awaiting surveyor sign-off"}</div>
          </div>
        </div>
        <div className={`step-node ${isSignedOff ? "completed" : ""}`}>
          <div className="step-circle">{isSignedOff ? "✓" : "3"}</div>
          <div>
            <div className="step-title">3. Signed Off</div>
            <div className="step-desc">{isSignedOff ? "Locked" : "Pending"}</div>
          </div>
        </div>
      </div>

      {/* Studio Header Strip */}
      <div className="card-header" style={{ marginBottom: 20 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6, flexWrap: "wrap" }}>
            {plate && <span className="badge-plate-glow">{plate}</span>}
            <span className={`chip-severity ${severityClass(d.severity_level)}`}>
              {isSignedOff ? "✓ SIGNED OFF & LOCKED" : d.severity_level ? `⚡ ${d.severity_level}` : "Severity unassessed"}
            </span>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              CASE_ID: <code>{report.id}</code>
              {d.location && <> &bull; Incident at {d.location}</>}
            </span>
          </div>
          <h1 style={{ fontSize: 24, fontWeight: 800, letterSpacing: "-0.02em", margin: 0 }}>
            {vehicleName}
            {d.accident_type && <> &mdash; {d.accident_type}</>}
          </h1>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <a href={`/reports/${report.id}/edit`} className="btn-secondary-modern">
            <span>✏️</span> {isSignedOff ? "Reopen & Edit" : "Edit Report"}
          </a>
          <PdfPreviewModal reportId={report.id} pdfUrl={report.pdf_url} reportCode={reportCode} />
          {!isSignedOff && (
            <button type="button" className="btn-primary-modern" onClick={() => setIsSignOffModalOpen(true)}>
              <span>✍️</span> Finalize &amp; Sign Off
            </button>
          )}
          <form
            action={deleteReportAction}
            onSubmit={(e) => {
              if (!confirm("Delete this report? This deletes its photos and PDF too, and can't be undone.")) {
                e.preventDefault();
              }
            }}
          >
            <input type="hidden" name="id" value={report.id} />
            <button type="submit" className="btn-secondary-modern" style={{ color: "var(--danger, #ef4444)" }}>
              🗑️ Delete
            </button>
          </form>
        </div>
      </div>

      {/* Split Studio Layout */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.15fr", gap: 24 }}>
        {/* Left Column: Blueprint & Photo Inspector */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {/* Vehicle Body Blueprint */}
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
              onHotspotClick={handleHotspotClick}
              highlightedDamageIndex={highlightedDamageIndex}
              vehicleName={vehicleName}
            />
          </div>

          {/* Photo Evidence Inspector */}
          {photos.length > 0 && (
            <div className="card-glass">
              <div className="card-header">
                <div>
                  <div className="card-title">
                    <span>📸</span> Photo Evidence Inspector
                  </div>
                  <div className="card-subtitle">
                    Photo {activePhotoIndex + 1} of {photos.length} &bull; Photo ID:{" "}
                    <strong style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>{currentPhotoLabel}</strong>
                  </div>
                </div>
                <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 10px" }} onClick={() => setShowBadges(!showBadges)}>
                  {showBadges ? "🙈 Hide Detected Parts" : "👁️ Show Detected Parts"}
                </button>
              </div>

              <div className="photo-inspector-box">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={fileUrl(currentPhotoUrl)} alt={currentPhotoLabel} className="inspector-main-img" />

                {/* Real AI-detected bounding boxes only -- Gemini localizes
                    these itself (see extraction.py's bbox_2d prompt, using
                    Gemini's own native [y_min,x_min,y_max,x_max]/1000
                    convention); never drawn from an invented/estimated
                    position. Items without a real box just don't get one,
                    no fallback guess. */}
                {showBadges && (
                  <div className="ai-bounding-overlay">
                    {currentPhotoDamage
                      .map((item, i) => ({ item, i, css: bboxToCss(item.bbox_2d) }))
                      .filter((x): x is { item: DamageSummaryItem; i: number; css: NonNullable<ReturnType<typeof bboxToCss>> } => x.css !== null)
                      .map(({ item, i, css }) => (
                        <div key={i} className="ai-box-marker-glow" style={css}>
                          <div className="ai-box-tag-glow">
                            ⚡ {item.part}
                            {item.ai_confidence ? ` // ${item.ai_confidence}` : ""}
                          </div>
                        </div>
                      ))}
                  </div>
                )}
              </div>

              {showBadges && (
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
                  {currentPhotoDamage.length > 0 ? (
                    currentPhotoDamage.map((item, i) => (
                      <span key={i} className={`chip-severity ${severityClass(item.severity)}`} style={{ fontSize: 11 }}>
                        ⚡ {item.part}
                        {item.ai_confidence ? ` // ${item.ai_confidence} confidence` : ""}
                        {item.bbox_2d ? " 🔲" : ""}
                      </span>
                    ))
                  ) : (
                    <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                      No damaged parts linked to this specific photo
                    </span>
                  )}
                </div>
              )}

              {/* Thumbnail Selector Strip */}
              <div className="photo-thumb-strip">
                {photos.map((src, idx) => (
                  <div
                    key={idx}
                    className={`photo-thumb ${activePhotoIndex === idx ? "active" : ""}`}
                    onClick={() => setActivePhotoIndex(idx)}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={fileUrl(src)} alt={photoLabel(idx)} />
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right Column: AI Summary & Tables */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {/* AI-Drafted Summary */}
          <div
            style={{
              background: "var(--surface-elevated)",
              border: "1px solid var(--border-hover)",
              borderLeft: "4px solid var(--accent-cyan)",
              borderRadius: "12px",
              padding: "18px 20px",
              boxShadow: "var(--shadow-sm)",
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

          {/* Damage Verification Table */}
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
                  {damageEntries.map((item, idx) => (
                    <tr
                      key={idx}
                      id={`damage-row-${idx}`}
                      style={{ background: highlightedDamageIndex === idx ? "var(--bg-hover, rgba(56,189,248,0.08))" : undefined }}
                    >
                      <td>
                        <strong>{item.part}</strong>
                        {item.photo_reference && (
                          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>Ref: {item.photo_reference}</div>
                        )}
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
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Repair Cost Estimate */}
          {ins?.estimated_repair_cost && (
            <div className="card-glass">
              <div className="card-header">
                <div className="card-title">
                  <span>💰</span> Estimated Repair Cost
                </div>
              </div>
              <div className="cost-matrix-glow">
                <div className="cost-row total">
                  <span>Total Estimated Repair Cost</span>
                  <span style={{ fontFamily: "var(--font-mono)", fontWeight: 700 }}>{ins.estimated_repair_cost}</span>
                </div>
              </div>
            </div>
          )}

          {/* Police & Insurance Details */}
          {(pol?.police_station || pol?.report_number || ins?.insurer_name || ins?.policy_number || ins?.claim_type) && (
            <div className="card-glass">
              <div className="card-header">
                <div className="card-title">
                  <span>🏛️</span> Authority &amp; Insurance Policy Details
                </div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, fontSize: 13 }}>
                {pol?.police_station && (
                  <div>
                    <div className="detail-field-label">Police Station</div>
                    <div style={{ fontWeight: 600, marginTop: 2 }}>{pol.police_station}</div>
                  </div>
                )}
                {pol?.report_number && (
                  <div>
                    <div className="detail-field-label">Report Reference Number</div>
                    <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--accent-cyan)", marginTop: 2 }}>{pol.report_number}</div>
                  </div>
                )}
                {ins?.insurer_name && (
                  <div>
                    <div className="detail-field-label">Insurance Provider</div>
                    <div style={{ fontWeight: 600, marginTop: 2 }}>{ins.insurer_name}</div>
                  </div>
                )}
                {(ins?.policy_number || ins?.claim_type) && (
                  <div>
                    <div className="detail-field-label">Policy &amp; Claim Type</div>
                    <div style={{ fontWeight: 600, marginTop: 2 }}>
                      {ins?.policy_number} {ins?.policy_number && ins?.claim_type && <>&bull;</>} {ins?.claim_type}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Sign-Off Confirmation Modal */}
      {isSignOffModalOpen && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            width: "100vw",
            height: "100vh",
            background: "rgba(0, 0, 0, 0.8)",
            backdropFilter: "blur(10px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 999,
          }}
          onClick={() => !isSigningOff && setIsSignOffModalOpen(false)}
        >
          <div
            style={{
              background: "var(--surface-card)",
              border: "1px solid var(--border-color)",
              borderRadius: "16px",
              width: "90%",
              maxWidth: "500px",
              padding: "24px",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ fontSize: 16, fontWeight: 800, marginBottom: 10 }}>Surveyor Digital Sign-Off</h3>
            <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 16 }}>
              By signing off, you certify that the damage items and photos for report <strong>{report.id}</strong>
              {plate && <> ({plate})</>} have been verified. This locks the report against further edits until
              reopened.
            </p>

            {signOffError && (
              <p style={{ fontSize: 12, color: "var(--danger, #ef4444)", marginBottom: 12 }}>{signOffError}</p>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button type="button" className="btn-secondary-modern" onClick={() => setIsSignOffModalOpen(false)} disabled={isSigningOff}>
                Cancel
              </button>
              <button type="button" className="btn-primary-modern" onClick={handleSignOff} disabled={isSigningOff}>
                <span>✍️</span> {isSigningOff ? "Signing Off..." : "Confirm & Lock"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
