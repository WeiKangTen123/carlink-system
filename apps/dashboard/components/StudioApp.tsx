"use client";

import React, { useState } from "react";
import { type ReportDetail, type DamageSummaryItem } from "@/lib/api";
import { deleteReportAction } from "@/app/reports/actions";
import { signOffReportAction } from "@/app/reports/actions";
import { PdfPreviewModal } from "@/components/PdfPreviewModal";
import { Breadcrumb } from "@/components/Breadcrumb";
import { CaseOverviewTab } from "@/components/CaseOverviewTab";
import { CaseEvidenceTab } from "@/components/CaseEvidenceTab";
import { CaseDamageTab } from "@/components/CaseDamageTab";
import { CaseSignOffTab } from "@/components/CaseSignOffTab";
import { resolveZones } from "@/lib/vehicleZones";

export function severityClass(severity?: string | null): string {
  const s = (severity || "").toLowerCase();
  if (s.includes("severe")) return "severe";
  if (s.includes("moderate")) return "moderate";
  if (s.includes("minor")) return "minor";
  return "minor";
}

type CaseTab = "overview" | "evidence" | "damage" | "signoff";

const TABS: { id: CaseTab; label: string; icon: string }[] = [
  { id: "overview", label: "Overview", icon: "📐" },
  { id: "evidence", label: "Evidence & Photos", icon: "📸" },
  { id: "damage", label: "Damage Assessment", icon: "📋" },
  { id: "signoff", label: "Sign-off & Documents", icon: "✍️" },
];

export function StudioApp({ report }: { report: ReportDetail }) {
  const d = report.data;
  const v = d.vehicle_info;

  const [activeTab, setActiveTab] = useState<CaseTab>("overview");
  const [activePhotoIndex, setActivePhotoIndex] = useState(0);
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

  // Same resolution the 3D blueprint uses for its numbered markers -- one
  // shared function so a badge number always means the same part in both
  // places, never two independently-computed numbering schemes drifting
  // apart.
  const zoneResolutions = resolveZones(damageEntries);

  const photos = report.photo_urls || [];

  const plate = v?.plate_number || d.vehicle_details || null;
  const vehicleName = [v?.make, v?.model].filter(Boolean).join(" ") || d.vehicle_details || "Vehicle";
  const reportCode = d.report_id || `CIR-2026-${report.id.slice(0, 4).toUpperCase()}`;

  const handleHotspotClick = (idx: number, item: DamageSummaryItem) => {
    setHighlightedDamageIndex(idx);
    if (item.photo_reference) {
      const photoIdx = parseInt(item.photo_reference.replace(/\D/g, ""), 10) - 1;
      if (photoIdx >= 0 && photoIdx < photos.length) setActivePhotoIndex(photoIdx);
    }
    // Jumping to a photo only makes sense if the reporter can actually see
    // it -- switch to the Evidence tab so this isn't a silent no-op when
    // triggered from Overview's blueprint or the Damage tab's table.
    setActiveTab("evidence");
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
      <Breadcrumb items={[{ label: "Home", href: "/" }, { label: "Cases", href: "/reports" }, { label: plate || vehicleName }]} />

      {/* 3-Stage Claim Lifecycle Stepper -- matches what the system actually
          tracks (Report.status / sign_off.status), not an invented 5-stage
          pipeline with stages nothing here updates. Kept visible above the
          tabs (not inside one) since it's a glanceable status indicator,
          same reasoning as why the header below it stays persistent too. */}
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

      {/* Case Section Tabs */}
      <div className="case-tab-bar">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`case-tab ${activeTab === tab.id ? "active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
          >
            <span>{tab.icon}</span> {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "overview" && (
        <CaseOverviewTab
          report={report}
          damageEntries={damageEntries}
          vehicleName={vehicleName}
          highlightedDamageIndex={highlightedDamageIndex}
          onHotspotClick={handleHotspotClick}
        />
      )}
      {activeTab === "evidence" && (
        <CaseEvidenceTab
          photos={photos}
          damageEntries={damageEntries}
          activePhotoIndex={activePhotoIndex}
          onSelectPhoto={setActivePhotoIndex}
        />
      )}
      {activeTab === "damage" && (
        <CaseDamageTab
          damageEntries={damageEntries}
          zoneResolutions={zoneResolutions}
          highlightedDamageIndex={highlightedDamageIndex}
          onHotspotClick={handleHotspotClick}
          estimatedRepairCost={d.insurance_details?.estimated_repair_cost}
        />
      )}
      {activeTab === "signoff" && <CaseSignOffTab report={report} />}

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
