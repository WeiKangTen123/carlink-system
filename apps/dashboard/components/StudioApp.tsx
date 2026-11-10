"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, ChevronDown, Eye, FileSpreadsheet, FileText, FolderOpen, Trash2, UserRound } from "lucide-react";
import { channelLabel, ownerLabel, pdfDownloadUrl, personLabel, xlsxDownloadUrl, type DamageSummaryItem, type Owner, type ReportDetail } from "@/lib/api";
import { deleteReportAction } from "@/app/reports/actions";
import { PdfPreviewModal } from "@/components/PdfPreviewModal";
import { Breadcrumb } from "@/components/Breadcrumb";
import { AssignOwnerDialog } from "@/components/AssignOwnerDialog";
import { DamageDetectionTab } from "@/components/DamageDetectionTab";
import { CoverTab } from "@/components/CoverTab";
import { AnnexATab } from "@/components/AnnexATab";
import { AnnexBTab } from "@/components/AnnexBTab";
import { AnnexCTab, ReopenCaseButton } from "@/components/AnnexCTab";
import { CaseRecordDrawer } from "@/components/CaseRecordDrawer";
import { useCaseState, type CaseSection } from "@/lib/caseState";
import { resolveZones, zoneKeyFor } from "@/lib/vehicleZones";
import { formatSgtDateTime } from "@/lib/caseFields";

export { severityClass } from "@/lib/caseFields";

/** The case page's five tabs mirror the firm's workbook (Cover Page, Annex
 * A, Annex B, Annex C) plus the detection that feeds them. What the report
 * never prints lives in the Case record drawer, not a sixth tab. */
type CaseTab = "detection" | "cover" | "annexA" | "annexB" | "annexC";

const TABS: { id: CaseTab; no: number; label: string; section?: CaseSection }[] = [
  { id: "detection", no: 1, label: "Damage detection" },
  { id: "cover", no: 2, label: "Cover page", section: "cover" },
  { id: "annexA", no: 3, label: "Annex A", section: "annexA" },
  { id: "annexB", no: 4, label: "Annex B", section: "annexB" },
  { id: "annexC", no: 5, label: "Annex C", section: "annexC" },
];

/** `value` as one of the five tabs, or null for anything else (a ?tab= from
 * a link is checked here before it opens anything). */
function asCaseTab(value: string | null | undefined): CaseTab | null {
  return TABS.find((t) => t.id === value)?.id ?? null;
}

/** The fields of a detected part the 3D blueprint draws from. */
const blueprintKey = (entries: DamageSummaryItem[]) => JSON.stringify(entries.map((e) => [e.part, e.severity ?? null]));

export function StudioApp({ report, initialTab }: { report: ReportDetail; initialTab?: string }) {
  const router = useRouter();
  // What this account may do here, as the API worked it out. The buttons
  // follow it; the API checks again on every call.
  const perms = report.permissions;
  const isSignedOff = report.status === "Signed Off" || report.data.sign_off?.status === "Signed Off";
  // Nothing can be changed once signed off, nor by someone who may only
  // look at the case.
  const readOnly = isSignedOff || !perms.edit;

  // One store for the page: server truth plus the drafts of the four
  // editable tabs (lib/caseState.ts).
  const store = useCaseState(report, readOnly);
  const d = store.data;
  const v = d.vehicle_info;

  // Set by Assign, so the header shows the new owner before the refreshed
  // page arrives with it.
  const [assignedOwner, setAssignedOwner] = useState<Owner | null | undefined>(undefined);
  const owner = assignedOwner !== undefined ? assignedOwner : report.owner;
  const [assigning, setAssigning] = useState(false);
  const [recordOpen, setRecordOpen] = useState(false);
  const [reportMenuOpen, setReportMenuOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);

  // Damage detection, unless the link asked for another tab (?tab=annexA).
  const [activeTab, setActiveTab] = useState<CaseTab>(() => asCaseTab(initialTab) ?? "detection");
  // A tab is mounted on first open and then kept (display: none), so what
  // was fetched there (suggested lines) or typed stays on coming back.
  const [opened, setOpened] = useState<Record<CaseTab, boolean>>(() => ({
    detection: false,
    cover: false,
    annexA: false,
    annexB: false,
    annexC: false,
    [asCaseTab(initialTab) ?? "detection"]: true,
  }));
  const showTab = (tab: CaseTab) => {
    setActiveTab(tab);
    setOpened((o) => (o[tab] ? o : { ...o, [tab]: true }));
  };

  const [activePhotoIndex, setActivePhotoIndex] = useState(0);
  const [highlightedDamageIndex, setHighlightedDamageIndex] = useState<number | null>(null);
  const [selectedZone, setSelectedZone] = useState<string | null>(null);

  // Same fallback the original detail page used: real damage_summary items
  // when they exist, otherwise the plain damaged_parts list with everything
  // else honestly left unknown -- never invented from the description text.
  const damageSummary = d.damage_summary;
  const damagedParts = d.damaged_parts;
  const severityLevel = d.severity_level;
  // The fallback only when there is no damage_summary at all: an empty list
  // is a real answer (every part removed), not a reason to fall back.
  const damageEntries = useMemo<DamageSummaryItem[]>(
    () =>
      Array.isArray(damageSummary)
        ? damageSummary
        : (damagedParts || []).map((p) => ({ part: p, severity: severityLevel || null, human_verified: false })),
    [damageSummary, damagedParts, severityLevel]
  );

  // The 3D blueprint resets its camera and rebuilds its materials whenever
  // it gets a new damageEntries array, so it gets one whose identity only
  // changes when a part or its severity changes -- not on a verified tick,
  // a photo link or an Annex A save (design 5.4).
  const blueprintContent = blueprintKey(damageEntries);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the content string on purpose
  const blueprintEntries = useMemo(() => damageEntries, [blueprintContent]);

  // Same resolution the 3D blueprint uses for its numbered markers -- one
  // shared function so a badge number always means the same part in both
  // places, never two independently-computed numbering schemes drifting
  // apart.
  const zoneResolutions = useMemo(() => resolveZones(damageEntries), [damageEntries]);

  const photos = report.photo_urls || [];
  const photoThumbs = report.photo_thumb_urls;

  const plate = v?.plate_number || d.vehicle_details || null;
  const vehicleName = [v?.make, v?.model].filter(Boolean).join(" ") || d.vehicle_details || "Vehicle not recorded";
  // Our Ref is the firm's file number; until issued the report prints "(to
  // be issued)" and nothing here makes one up.
  const ourRef = d.report_id?.trim() || null;
  // The preview's file name, as the API names the download: "<Our Ref>
  // <plate>", else the plate, else plain "report" (PdfPreviewModal's own
  // fallback) -- never an id fragment.
  const previewCode = [ourRef, plate].filter(Boolean).join(" ");
  const signOff = d.sign_off;

  // Selecting a damage item from anywhere -- a blueprint marker, a table
  // row -- highlights it everywhere at once and synchronizes with the 3D
  // camera and the photo strip. The CURRENT entry is read by index: the 3D
  // blueprint holds an older copy of the parts (it only gets a new array
  // when a part or severity changes), so a photo re-linked since would not
  // open from its copy.
  const handleHotspotClick = (idx: number, clicked: DamageSummaryItem) => {
    const item = damageEntries[idx] ?? clicked;
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

  // The Report menu closes on a click anywhere else.
  const menuRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!reportMenuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setReportMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [reportMenuOpen]);

  const tabPanelStyle = (tab: CaseTab): React.CSSProperties | undefined => (activeTab === tab ? undefined : { display: "none" });

  return (
    <div>
      <Breadcrumb items={[{ label: "Home", href: "/" }, { label: "Cases", href: "/reports" }, { label: plate || vehicleName }]} />

      {/* Header: the case as the report names it. The stepper, severity
          chip, case uuid, weather / road / traffic chips, "Authorities"
          chip and AI confidence pill went on 2026-11-04: none of them is on
          the firm's report, and the last two printed default values as
          facts. */}
      <div className="card-header case-head">
        <div style={{ minWidth: 0 }}>
          <div className="case-head-title">
            {plate && <span className="badge-plate-glow">{plate}</span>}
            <h1 style={{ fontSize: 22, fontWeight: 800, letterSpacing: "-0.02em", margin: 0 }}>{vehicleName}</h1>
            {isSignedOff ? (
              <span className="case-status-chip signed">
                <CheckCircle2 style={{ width: 12, height: 12 }} /> Signed off
                {signOff?.signature_date && <span suppressHydrationWarning> {new Date(signOff.signature_date).toLocaleDateString("en-SG")}</span>}
                {signOff?.surveyor_name ? ` by ${signOff.surveyor_name}` : ""}
              </span>
            ) : (
              <span className="case-status-chip">Draft — awaiting sign-off</span>
            )}
          </div>
          <div className="case-head-ref" style={{ marginTop: 6 }}>
            Our Ref: {ourRef ? <strong>{ourRef}</strong> : <span className="unissued">Ref to be issued</span>}
          </div>
          <div className="case-head-meta">
            <span suppressHydrationWarning>
              Filed {formatSgtDateTime(report.created_at)} via {channelLabel(report.channel)}
            </span>
            {report.public_submission?.reference && (
              <span className="case-public-chip" title="Sent by a member of the public through the Telegram bot">
                Public {report.public_submission.reference}
              </span>
            )}
          </div>
          <div className="case-owner-line">
            <UserRound style={{ width: 13, height: 13, color: "var(--text-muted)" }} />
            <span>
              Owner: <strong className={owner && !owner.active ? "case-owner-disabled" : undefined}>{ownerLabel(owner)}</strong>
            </span>
            {!owner && report.suggested_owner && <span className="case-owner-hint">Suggested: {personLabel(report.suggested_owner)}</span>}
            {perms.assign && (
              <button type="button" className="btn-secondary-modern case-owner-assign" onClick={() => setAssigning(true)}>
                Assign
              </button>
            )}
          </div>
        </div>

        <div className="case-head-actions">
          <button type="button" className="btn-secondary-modern" onClick={() => setRecordOpen(true)} title="Intake facts, police, insurer, other party, people, witnesses and the case history">
            <FolderOpen style={{ width: 13, height: 13 }} /> Case record
          </button>

          {/* The report as the firm sends it. Unsigned, it prints as a draft. */}
          <div className="report-menu" ref={menuRef}>
            <button type="button" className="btn-secondary-modern" onClick={() => setReportMenuOpen((o) => !o)} aria-haspopup="menu" aria-expanded={reportMenuOpen}>
              <FileText style={{ width: 13, height: 13 }} /> Report <ChevronDown style={{ width: 12, height: 12 }} />
            </button>
            {reportMenuOpen && (
              <div className="report-menu-list" role="menu">
                {store.anyDirty && <div className="report-menu-note">Save first: downloads use what is saved.</div>}
                <a href={pdfDownloadUrl(report.id)} className="report-menu-item" role="menuitem" title="The report with the photo pages">
                  <FileText style={{ width: 13, height: 13 }} /> PDF
                </a>
                <a href={pdfDownloadUrl(report.id, { photos: false })} className="report-menu-item" role="menuitem" title="The four pages only">
                  <FileText style={{ width: 13, height: 13 }} /> PDF without photos
                </a>
                <a href={xlsxDownloadUrl(report.id)} className="report-menu-item" role="menuitem" title="Cover Page, Annex A, Annex B and Annex C as a workbook">
                  <FileSpreadsheet style={{ width: 13, height: 13 }} /> Excel
                </a>
                <button
                  type="button"
                  className="report-menu-item"
                  role="menuitem"
                  onClick={() => {
                    setReportMenuOpen(false);
                    setPreviewOpen(true);
                  }}
                >
                  <Eye style={{ width: 13, height: 13 }} /> Preview PDF
                </button>
              </div>
            )}
          </div>

          {/* Goes to Annex C rather than signing here: a sign-off from this
              button used to record only the declaration tick, so the Annex C
              letter printed "(to be agreed)" for the lump sum and working
              days on a signed report. */}
          {!isSignedOff && perms.sign_off && (
            <button type="button" className="btn-primary-modern" onClick={() => showTab("annexC")}>
              <CheckCircle2 style={{ width: 14, height: 14 }} /> Sign off →
            </button>
          )}
          {/* A signed-off case is reopened by the signer or an admin. */}
          {isSignedOff && perms.reopen && <ReopenCaseButton reportId={report.id} />}

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

      <PdfPreviewModal reportId={report.id} pdfUrl={report.pdf_url} reportCode={previewCode} isOpen={previewOpen} onClose={() => setPreviewOpen(false)} trigger={null} />

      {/* The five tabs; a dot marks a tab with unsaved changes. */}
      <div className="case-tab-bar" role="tablist">
        {TABS.map((tab) => {
          const dirty = tab.section ? store.dirty[tab.section] : false;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              className={`case-tab ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => showTab(tab.id)}
              title={dirty ? `${tab.label}: unsaved changes` : undefined}
            >
              <span className="case-tab-no">{tab.no}</span> {tab.label}
              {dirty && <span className="case-tab-dirty-dot" aria-label="unsaved changes" />}
            </button>
          );
        })}
      </div>

      {opened.detection && (
        <div style={tabPanelStyle("detection")}>
          <DamageDetectionTab
            report={report}
            isSignedOff={isSignedOff}
            canEdit={perms.edit}
            damageEntries={damageEntries}
            blueprintEntries={blueprintEntries}
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
            annexAItems={store.annexABaseline}
            onDamageItemsApplied={store.applyDamageItems}
            // Indices shift when a row goes, so the highlight is dropped
            // rather than left on the wrong part.
            onPartRemoved={() => setHighlightedDamageIndex(null)}
            onGoToAnnexA={() => showTab("annexA")}
            isActive={activeTab === "detection"}
          />
        </div>
      )}
      {opened.cover && (
        <div style={tabPanelStyle("cover")}>
          <CoverTab report={report} store={store} canEdit={perms.edit} isSignedOff={isSignedOff} damageEntries={damageEntries} />
        </div>
      )}
      {opened.annexA && (
        <div style={tabPanelStyle("annexA")}>
          <AnnexATab report={report} store={store} canEdit={perms.edit} isSignedOff={isSignedOff} />
        </div>
      )}
      {opened.annexB && (
        <div style={tabPanelStyle("annexB")}>
          <AnnexBTab report={report} store={store} canEdit={perms.edit} isSignedOff={isSignedOff} active={opened.annexB} />
        </div>
      )}
      {opened.annexC && (
        <div style={tabPanelStyle("annexC")}>
          <AnnexCTab
            report={report}
            store={store}
            canEdit={perms.edit}
            canSignOff={perms.sign_off}
            canReopen={perms.reopen}
            isSignedOff={isSignedOff}
            onOpenTab={showTab}
            onOpenRecord={() => setRecordOpen(true)}
          />
        </div>
      )}

      <CaseRecordDrawer report={report} open={recordOpen} onClose={() => setRecordOpen(false)} isSignedOff={isSignedOff} canEdit={perms.edit} />

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
