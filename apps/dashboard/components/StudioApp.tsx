"use client";

import React, { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Search,
  FileText,
  FileCheck2,
  Pencil,
  Trash2,
  Bot,
  CheckCircle2,
  Lock,
  UserRound,
} from "lucide-react";
import { ownerLabel, personLabel, PUBLIC_CHANNEL, type ReportDetail, type DamageSummaryItem, type Owner } from "@/lib/api";
import { deleteReportAction } from "@/app/reports/actions";
import { PdfPreviewModal } from "@/components/PdfPreviewModal";
import { Breadcrumb } from "@/components/Breadcrumb";
import { CaseInspectionTab } from "@/components/CaseInspectionTab";
import { CaseFileTab } from "@/components/CaseFileTab";
import { CaseAssessmentTab } from "@/components/CaseAssessmentTab";
import { AssignOwnerDialog } from "@/components/AssignOwnerDialog";
import { PublicSenderCard } from "@/components/inbox/PublicSenderCard";
import { resolveZones, zoneKeyFor } from "@/lib/vehicleZones";
import { severityClass } from "@/lib/caseFields";

export { severityClass } from "@/lib/caseFields";

type CaseTab = "inspection" | "file" | "assessment";

const TABS: { id: CaseTab; label: string; icon: React.ComponentType<{ style?: React.CSSProperties }> }[] = [
  { id: "inspection", label: "Damage Inspection", icon: Search },
  { id: "file", label: "Case File", icon: FileText },
  { id: "assessment", label: "Assessment & Sign-off", icon: FileCheck2 },
];

export function StudioApp({ report }: { report: ReportDetail }) {
  const d = report.data;
  const v = d.vehicle_info;
  const router = useRouter();
  // What this account may do here, as the API worked it out. The buttons
  // follow it; the API checks again on every call.
  const perms = report.permissions;
  // Set by Assign, so the header shows the new owner before the refreshed
  // page arrives with it.
  const [assignedOwner, setAssignedOwner] = useState<Owner | null | undefined>(undefined);
  const owner = assignedOwner !== undefined ? assignedOwner : report.owner;
  const [assigning, setAssigning] = useState(false);

  const [activeTab, setActiveTab] = useState<CaseTab>("inspection");
  // Once opened, the Assessment tab stays mounted (hidden) while another tab
  // is shown, so unsaved Annex A edits and its suggested lines survive.
  const [assessmentOpened, setAssessmentOpened] = useState(false);
  const [activePhotoIndex, setActivePhotoIndex] = useState(0);
  const [highlightedDamageIndex, setHighlightedDamageIndex] = useState<number | null>(null);
  const [selectedZone, setSelectedZone] = useState<string | null>(null);

  const isSignedOff = report.status === "Signed Off" || d.sign_off?.status === "Signed Off";

  // Same fallback the original detail page used: real damage_summary items
  // when they exist, otherwise the plain damaged_parts list with everything
  // else honestly left unknown -- never invented from the description text.
  // Memoised on the report data so the array keeps its identity across
  // re-renders: the 3D blueprint resets its camera and rebuilds materials
  // whenever it gets a new damageEntries array.
  const damageSummary = d.damage_summary;
  const damagedParts = d.damaged_parts;
  const severityLevel = d.severity_level;
  const damageEntries = useMemo<DamageSummaryItem[]>(
    () =>
      damageSummary && damageSummary.length > 0
        ? damageSummary
        : (damagedParts || []).map((p) => ({ part: p, severity: severityLevel || null, human_verified: false })),
    [damageSummary, damagedParts, severityLevel],
  );

  // Same resolution the 3D blueprint uses for its numbered markers -- one
  // shared function so a badge number always means the same part in both
  // places, never two independently-computed numbering schemes drifting
  // apart.
  const zoneResolutions = useMemo(() => resolveZones(damageEntries), [damageEntries]);

  const photos = report.photo_urls || [];
  const photoThumbs = report.photo_thumb_urls;

  const plate = v?.plate_number || d.vehicle_details || null;
  const vehicleName = [v?.make, v?.model].filter(Boolean).join(" ") || d.vehicle_details || "Vehicle";
  // The case's own filing year (this was a hard-coded 2026).
  const filedYear = new Date(report.created_at).getFullYear();
  const reportCode =
    d.report_id || `CIR-${Number.isNaN(filedYear) ? "" : `${filedYear}-`}${report.id.slice(0, 4).toUpperCase()}`;

  const conditionChips = [d.weather_condition, d.road_condition, d.traffic_condition].filter(Boolean) as string[];

  // Selecting a damage item from anywhere -- a blueprint marker, a
  // checklist row -- highlights it everywhere at once and synchronizes
  // with the 3D camera and photo evidence strip.
  const handleHotspotClick = (idx: number, item: DamageSummaryItem) => {
    setHighlightedDamageIndex(idx);
    const zKey = zoneKeyFor(item.part);
    if (zKey) setSelectedZone(zKey);
    if (item.photo_reference) {
      const photoIdx = parseInt(item.photo_reference.replace(/\D/g, ""), 10) - 1;
      if (photoIdx >= 0 && photoIdx < photos.length) setActivePhotoIndex(photoIdx);
    }
  };

  const handleSelectZone = (zoneId: string | null) => {
    setSelectedZone(zoneId);
    if (zoneId) {
      const matchingIdx = damageEntries.findIndex((item) => {
        const z = zoneKeyFor(item.part);
        return z === zoneId || (zoneId === "skeleton" && z === "underbody");
      });
      if (matchingIdx >= 0) {
        setHighlightedDamageIndex(matchingIdx);
      }
    }
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
            {report.channel === PUBLIC_CHANNEL ? (
              <div className="step-desc">Telegram (public) intake</div>
            ) : (
              <div className="step-desc" style={{ textTransform: "capitalize" }}>{report.channel} intake</div>
            )}
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
              {d.location && <> • Incident at {d.location}</>}
            </span>
          </div>
          <h1 style={{ fontSize: 24, fontWeight: 800, letterSpacing: "-0.02em", margin: 0 }}>
            {vehicleName}
            {d.accident_type && <> — {d.accident_type}</>}
          </h1>
          <div className="case-owner-line">
            <UserRound style={{ width: 13, height: 13, color: "var(--text-muted)" }} />
            <span>
              Owner: <strong className={owner && !owner.active ? "case-owner-disabled" : undefined}>{ownerLabel(owner)}</strong>
            </span>
            {!owner && report.suggested_owner && (
              <span className="case-owner-hint">Suggested: {personLabel(report.suggested_owner)}</span>
            )}
            {perms.assign && (
              <button type="button" className="btn-secondary-modern case-owner-assign" onClick={() => setAssigning(true)}>
                Assign
              </button>
            )}
          </div>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          {/* A signed-off case is edited by reopening it, which only the
              signer or an admin may do. */}
          {(isSignedOff ? perms.reopen : perms.edit) && (
            <a href={`/reports/${report.id}/edit`} className="btn-secondary-modern">
              <Pencil style={{ width: 13, height: 13 }} /> {isSignedOff ? "Reopen & Edit" : "Edit Report"}
            </a>
          )}
          <PdfPreviewModal reportId={report.id} pdfUrl={report.pdf_url} reportCode={reportCode} />
          {/* Goes to the Assessment tab rather than signing here: a sign-off
              from this button used to record only the declaration tick, so
              the Annex C letter printed "(to be agreed)" for the lump sum
              and working days on a signed report. */}
          {!isSignedOff && perms.sign_off && (
            <button
              type="button"
              className="btn-primary-modern"
              onClick={() => {
                setActiveTab("assessment");
                setAssessmentOpened(true);
              }}
            >
              <CheckCircle2 style={{ width: 14, height: 14 }} /> Sign off
            </button>
          )}
          {/* Admins only for a filed case; the API refuses anyone else. */}
          {perms.delete && (
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
                <Trash2 style={{ width: 13, height: 13 }} /> Delete
              </button>
            </form>
          )}
        </div>
      </div>

      {/* Who sent it, on a report from the public (release P6). Filed, so
          it can't go back to the inbox from here. */}
      {report.public_submission && (
        <div style={{ marginBottom: 16 }}>
          <PublicSenderCard reportId={report.id} info={report.public_submission} showAiCheck={!isSignedOff} />
        </div>
      )}

      {/* AI-Drafted Summary -- context you read once when opening the case,
          not something cross-referenced item-by-item, so it sits as a strip
          under the header rather than competing for space inside the
          inspection workspace. */}
      <div className="ai-summary-strip">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6, flexWrap: "wrap", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 700, fontSize: 13 }}>
            <Bot style={{ width: 15, height: 15, color: "var(--accent-primary)" }} /> AI-Drafted Incident Summary
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            {conditionChips.map((f, i) => (
              <span key={i} className="chip-severity minor" style={{ fontSize: 10 }}>
                {f}
              </span>
            ))}
            <span className="chip-severity minor" style={{ fontSize: 10 }}>
              Authorities: {d.reported_to_authorities ? "Yes" : "No"}
            </span>
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
        </div>
        <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: 0, lineHeight: 1.6 }}>{d.description}</p>
      </div>

      {/* Case Section Tabs */}
      <div className="case-tab-bar">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              type="button"
              className={`case-tab ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => {
                setActiveTab(tab.id);
                if (tab.id === "assessment") setAssessmentOpened(true);
              }}
            >
              <Icon style={{ width: 15, height: 15 }} /> {tab.label}
            </button>
          );
        })}
      </div>

      {activeTab === "inspection" && (
        <CaseInspectionTab
          report={report}
          isSignedOff={isSignedOff}
          damageEntries={damageEntries}
          zoneResolutions={zoneResolutions}
          vehicleName={vehicleName}
          photos={photos}
          photoThumbs={photoThumbs}
          photoPreviews={report.photo_preview_urls}
          activePhotoIndex={activePhotoIndex}
          highlightedDamageIndex={highlightedDamageIndex}
          selectedZone={selectedZone}
          onSelectPhoto={setActivePhotoIndex}
          onHotspotClick={handleHotspotClick}
          onSelectZone={handleSelectZone}
        />
      )}
      {activeTab === "file" && <CaseFileTab report={report} />}
      {(activeTab === "assessment" || assessmentOpened) && (
        <div style={{ display: activeTab === "assessment" ? undefined : "none" }}>
          <CaseAssessmentTab report={report} damageEntries={damageEntries} canEdit={perms.edit} canSignOff={perms.sign_off} />
        </div>
      )}

      {assigning && (
        <AssignOwnerDialog
          reportId={report.id}
          owner={owner}
          suggested={report.suggested_owner}
          onClose={() => setAssigning(false)}
          onAssigned={(next) => {
            setAssignedOwner(next);
            setAssigning(false);
            // Brings in the "assigned" line in the case history.
            router.refresh();
          }}
        />
      )}

    </div>
  );
}
