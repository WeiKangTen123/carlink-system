"use client";

import { useState, useMemo, useEffect, useRef } from "react";
import Link from "next/link";
import {
  Search,
  CheckCircle2,
  Clock,
  ArrowRight,
  ShieldCheck,
  Layers,
  Filter,
  Wrench,
  Flame,
  DollarSign,
  CarFront,
  FileText,
  Columns3,
  LayoutGrid,
  AlertTriangle,
  Calendar,
  Building2,
  Plus,
  Sparkles,
  RefreshCw,
  Send,
  SlidersHorizontal,
} from "lucide-react";
import { type ReportSummary, type ReportDetail, type AnalyticsSummary, fileUrl } from "@/lib/api";
import { DeleteButton } from "@/components/DeleteButton";
import { PdfPreviewModal } from "@/components/PdfPreviewModal";
import {
  caseTitle,
  daysOpen,
  isAwaitingSignOff,
  getClaimArchetype,
  getEstimatedCostSGD,
  formatCarlinkRef,
  severityClass,
  inferVehicleName,
  inferInsurerName,
  inferWorkshopName,
  formatSgtDateTime,
  formatIncidentTime,
  getGiaSla,
} from "@/lib/caseFields";
import {
  getReportDetailAction,
  aiEnrichReportAction,
  aiEnrichAllReportsAction,
} from "./actions";

type ViewMode = "split" | "matrix";
type Swimlane = "all" | "sje" | "tma" | "tp" | "od" | "signed";
type QuickFilter = "all" | "urgent_sla" | "strip_down" | "severe" | "high_quantum";

interface Props {
  initialReports: ReportSummary[];
  initialAnalytics?: AnalyticsSummary | null;
}

const PART_PRICES_MAP: Record<string, number> = {
  "Front Bumper": 1250,
  "Rear Bumper": 1150,
  "Bonnet": 1850,
  "Boot Lid": 1650,
  "Front Grille": 650,
  "Radiator": 1400,
  "Front Subframe": 3800,
  "Left Headlamp": 1450,
  "Right Headlamp": 1450,
  "Left Tail Lamp": 950,
  "Right Tail Lamp": 950,
  "Front Windscreen": 1600,
  "Rear Windscreen": 1400,
  "Left Front Fender": 1350,
  "Right Front Fender": 1350,
  "Left Rear Quarter Panel": 2200,
  "Right Rear Quarter Panel": 2200,
  "Left Front Door": 1950,
  "Right Front Door": 1950,
  "Left Rear Door": 1950,
  "Right Rear Door": 1950,
  "Left Wing Mirror": 850,
  "Right Wing Mirror": 850,
  "Roof Panel": 2600,
  "Underbody Shield": 750,
};

export function ReportsClient({ initialReports, initialAnalytics }: Props) {
  const [reportsList, setReportsList] = useState<ReportSummary[]>(initialReports || []);
  const [viewMode, setViewMode] = useState<ViewMode>("split");
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedInsurer, setSelectedInsurer] = useState("all");
  const [selectedSwimlane, setSelectedSwimlane] = useState<Swimlane>("all");
  const [quickFilter, setQuickFilter] = useState<QuickFilter>("all");
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null);
  const [previewReport, setPreviewReport] = useState<ReportSummary | null>(null);

  // Detail cache & state for active selected report
  const detailCache = useRef<Map<string, ReportDetail>>(new Map());
  const [activeDetail, setActiveDetail] = useState<ReportDetail | null>(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [activePhotoIdx, setActivePhotoIdx] = useState(0);

  // AI Enrichment state
  const [isEnrichingCurrent, setIsEnrichingCurrent] = useState(false);
  const [isEnrichingAll, setIsEnrichingAll] = useState(false);
  const [enrichNotice, setEnrichNotice] = useState<string | null>(null);

  // Sync initialReports if updated
  useEffect(() => {
    if (initialReports) {
      setReportsList(initialReports);
    }
  }, [initialReports]);

  // Load view mode preference from localStorage
  useEffect(() => {
    try {
      const saved = localStorage.getItem("carlink_cases_view_mode");
      if (saved === "split" || saved === "matrix") {
        setViewMode(saved);
      }
    } catch {
      // Ignore localStorage read errors
    }
  }, []);

  const handleSetViewMode = (mode: ViewMode) => {
    setViewMode(mode);
    try {
      localStorage.setItem("carlink_cases_view_mode", mode);
    } catch {
      // Ignore localStorage write errors
    }
  };

  // Insurer Options list
  const insurerOptions = useMemo(() => {
    const set = new Set<string>();
    reportsList.forEach((r) => {
      const ins = inferInsurerName(r);
      if (ins) set.add(ins);
    });
    ["Tokio Marine Insurance Singapore", "NTUC Income Insurance Co-operative", "AIG Asia Pacific Insurance", "Great American Insurance Company"].forEach((i) => set.add(i));
    return Array.from(set);
  }, [reportsList]);

  // Executive Metric Counters
  const totalCount = reportsList.length;
  const pendingCount = useMemo(() => reportsList.filter((r) => isAwaitingSignOff(r.status)).length, [reportsList]);
  const signedCount = useMemo(() => reportsList.filter((r) => r.status === "Signed Off").length, [reportsList]);
  const totalReservesSGD = useMemo(
    () => reportsList.filter((r) => isAwaitingSignOff(r.status)).reduce((sum, r) => sum + getEstimatedCostSGD(r), 0),
    [reportsList]
  );
  const urgentCount = useMemo(
    () =>
      reportsList.filter((r) => {
        const sla = getGiaSla(r.created_at, r.status === "Signed Off");
        return sla.status === "critical" || sla.status === "expired";
      }).length,
    [reportsList]
  );
  const highStakesCount = useMemo(
    () => reportsList.filter((r) => {
      const arch = getClaimArchetype(r).key;
      return arch === "sje" || arch === "tma";
    }).length,
    [reportsList]
  );

  // Filtered reports logic
  const filteredReports = useMemo(() => {
    return reportsList.filter((r) => {
      const arch = getClaimArchetype(r);
      const cost = getEstimatedCostSGD(r);
      const isSigned = r.status === "Signed Off";
      const sla = getGiaSla(r.created_at, isSigned);
      const vehicle = inferVehicleName(r);
      const insurer = inferInsurerName(r);
      const workshop = inferWorkshopName(r.workshop_assigned || r.location);

      // 1. Swimlane
      if (selectedSwimlane === "sje" && arch.key !== "sje") return false;
      if (selectedSwimlane === "tma" && arch.key !== "tma") return false;
      if (selectedSwimlane === "tp" && arch.key !== "tp-conv" && arch.key !== "tp-direct") return false;
      if (selectedSwimlane === "od" && arch.key !== "od") return false;
      if (selectedSwimlane === "signed" && !isSigned) return false;

      // 2. Quick filter
      if (quickFilter === "urgent_sla" && sla.status !== "critical" && sla.status !== "expired") return false;
      if (quickFilter === "strip_down" && !r.disassembly_required) return false;
      if (quickFilter === "severe" && !(r.severity_level || "").toLowerCase().includes("severe")) return false;
      if (quickFilter === "high_quantum" && cost < 15000) return false;

      // 3. Insurer
      if (selectedInsurer !== "all") {
        if (!insurer.toLowerCase().includes(selectedInsurer.toLowerCase())) return false;
      }

      // 4. Omni-Search
      if (searchTerm.trim() !== "") {
        const q = searchTerm.toLowerCase();
        const refStr = formatCarlinkRef(r.id, r.plate_number).toLowerCase();
        const haystack = `${r.id} ${refStr} ${r.plate_number || ""} ${vehicle} ${r.location || ""} ${insurer} ${workshop}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }

      return true;
    });
  }, [reportsList, selectedSwimlane, quickFilter, selectedInsurer, searchTerm]);

  // Set default selected report if current is null or not in list
  useEffect(() => {
    if (filteredReports.length > 0) {
      if (!selectedReportId || !filteredReports.some((r) => r.id === selectedReportId)) {
        setSelectedReportId(filteredReports[0].id);
      }
    } else {
      setSelectedReportId(null);
    }
  }, [filteredReports, selectedReportId]);

  // Fetch full report detail when selectedReportId changes
  useEffect(() => {
    if (!selectedReportId) {
      setActiveDetail(null);
      return;
    }

    if (detailCache.current.has(selectedReportId)) {
      setActiveDetail(detailCache.current.get(selectedReportId)!);
      setActivePhotoIdx(0);
      return;
    }

    let isMounted = true;
    setIsLoadingDetail(true);
    getReportDetailAction(selectedReportId).then((res) => {
      if (!isMounted) return;
      setIsLoadingDetail(false);
      if (res) {
        detailCache.current.set(selectedReportId, res);
        setActiveDetail(res);
        setActivePhotoIdx(0);
      }
    });

    return () => {
      isMounted = false;
    };
  }, [selectedReportId]);

  // Keyboard Navigation: Up/Down arrow keys in Split Mode
  useEffect(() => {
    if (viewMode !== "split" || filteredReports.length === 0) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "ArrowDown" || e.key === "j") {
        e.preventDefault();
        const currentIndex = filteredReports.findIndex((r) => r.id === selectedReportId);
        if (currentIndex < filteredReports.length - 1) {
          setSelectedReportId(filteredReports[currentIndex + 1].id);
        }
      } else if (e.key === "ArrowUp" || e.key === "k") {
        e.preventDefault();
        const currentIndex = filteredReports.findIndex((r) => r.id === selectedReportId);
        if (currentIndex > 0) {
          setSelectedReportId(filteredReports[currentIndex - 1].id);
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [viewMode, filteredReports, selectedReportId]);

  // Active selected summary record
  const selectedSummary = useMemo(() => {
    return filteredReports.find((r) => r.id === selectedReportId) || filteredReports[0] || null;
  }, [filteredReports, selectedReportId]);

  // Handle 1-Click AI Enrich for currently selected report
  const handleEnrichCurrent = async () => {
    if (!selectedSummary) return;
    setIsEnrichingCurrent(true);
    setEnrichNotice("Running Gemini Loss Adjuster Auto-Enrichment...");
    try {
      const res = await aiEnrichReportAction(selectedSummary.id);
      if ("error" in res) {
        setEnrichNotice(`Enrichment failed: ${res.error}`);
      } else {
        setEnrichNotice("Case successfully enriched with vehicle specs & quantum reserves!");
        detailCache.current.delete(selectedSummary.id);
        // Refresh detail
        const updated = await getReportDetailAction(selectedSummary.id);
        if (updated) {
          detailCache.current.set(selectedSummary.id, updated);
          setActiveDetail(updated);
        }
      }
    } catch (e) {
      setEnrichNotice("Enrichment error encountered.");
    } finally {
      setIsEnrichingCurrent(false);
      setTimeout(() => setEnrichNotice(null), 4000);
    }
  };

  // Handle Batch AI Enrich for all reports
  const handleEnrichAll = async () => {
    setIsEnrichingAll(true);
    setEnrichNotice("Enriching all incident files across Singapore GIA panel...");
    try {
      const res = await aiEnrichAllReportsAction();
      if ("error" in res) {
        setEnrichNotice(`Batch enrich failed: ${res.error}`);
      } else {
        setEnrichNotice(`Successfully enriched ${res.count || "all"} cases in database!`);
        detailCache.current.clear();
        if (selectedSummary) {
          const updated = await getReportDetailAction(selectedSummary.id);
          if (updated) setActiveDetail(updated);
        }
      }
    } catch {
      setEnrichNotice("Batch enrich request completed.");
    } finally {
      setIsEnrichingAll(false);
      setTimeout(() => setEnrichNotice(null), 4000);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* 1. Header with View Switcher & Action Strip */}
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 4 }}>
            <span className="command-header-badge">
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--badge-green-text)", boxShadow: "0 0 6px var(--badge-green-text)" }} />
              CASES WORKSTATION
            </span>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              Principal Assessor: <strong style={{ color: "var(--text-primary)" }}>Patrick Ng</strong> (Forensic Loss Adjuster)
            </span>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              &bull; Framework: <strong style={{ color: "var(--accent-cyan)" }}>GIA / IDAC 48h Inspection SLA</strong>
            </span>
          </div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.02em" }}>
            Claims Dossier &amp; Workstation
          </h1>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          {/* Batch Auto-Map All Button */}
          <button
            type="button"
            onClick={handleEnrichAll}
            disabled={isEnrichingAll}
            className="btn-secondary-modern"
            style={{ fontSize: 12, padding: "7px 14px", display: "inline-flex", alignItems: "center", gap: 6 }}
            title="Auto-map vehicle models, repair quantum, and insurers across all cases"
          >
            <Sparkles style={{ width: 13, height: 13, color: "var(--accent-cyan)" }} />
            <span>{isEnrichingAll ? "Enriching All..." : "Auto-Map All (Gemini)"}</span>
          </button>

          {/* View Mode Switcher Toggle */}
          <div className="view-mode-toggle" title="Switch between Split Dossier Workstation and Dense Spreadsheet Matrix">
            <button
              type="button"
              className={`view-mode-btn ${viewMode === "split" ? "active" : ""}`}
              onClick={() => handleSetViewMode("split")}
            >
              <Columns3 style={{ width: 13, height: 13 }} />
              <span>Split Dossier</span>
            </button>
            <button
              type="button"
              className={`view-mode-btn ${viewMode === "matrix" ? "active" : ""}`}
              onClick={() => handleSetViewMode("matrix")}
            >
              <LayoutGrid style={{ width: 13, height: 13 }} />
              <span>Dense Matrix</span>
            </button>
          </div>

          <Link href="/reports/new" className="btn-primary-modern" style={{ fontSize: 12, padding: "7px 14px" }}>
            <Plus style={{ width: 14, height: 14 }} /> File Incident
          </Link>
        </div>
      </div>

      {/* 2. Compact Single-Line Executive Metric Strip (Saves ~180px vertical height) */}
      <div className="kpi-strip-command">
        <div className="kpi-chip-metric" title="Total incident files on record">
          <Layers style={{ width: 13, height: 13, color: "var(--accent-cyan)" }} />
          <span>Total Caseload:</span>
          <span className="metric-val" style={{ color: "var(--text-primary)" }}>{totalCount}</span>
        </div>

        <div className="kpi-chip-metric" title="Files awaiting surveyor certification">
          <Clock style={{ width: 13, height: 13, color: "var(--badge-amber-text)" }} />
          <span>Pending Review:</span>
          <span className="metric-val" style={{ color: "var(--badge-amber-text)" }}>{pendingCount}</span>
        </div>

        <div className="kpi-chip-metric" title="Cases nearing or exceeding statutory 48h SLA">
          <AlertTriangle style={{ width: 13, height: 13, color: "var(--badge-red-text)" }} />
          <span>Urgent GIA SLA:</span>
          <span className="metric-val" style={{ color: "var(--badge-red-text)" }}>{urgentCount}</span>
        </div>

        <div className="kpi-chip-metric" title="Total assessed repair indemnity reserves in SGD">
          <DollarSign style={{ width: 13, height: 13, color: "var(--badge-green-text)" }} />
          <span>Assessed Reserves:</span>
          <span className="metric-val" style={{ color: "var(--badge-green-text)" }}>
            ${totalReservesSGD.toLocaleString()} SGD
          </span>
        </div>

        <div className="kpi-chip-metric" title="High-stakes State Court SJE dockets & TMA collisions">
          <Flame style={{ width: 13, height: 13, color: "var(--accent-primary)" }} />
          <span>High-Stakes:</span>
          <span className="metric-val" style={{ color: "var(--accent-primary)" }}>{highStakesCount} SJE/TMA</span>
        </div>

        {enrichNotice && (
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--accent-cyan)", marginLeft: "auto", display: "flex", alignItems: "center", gap: 5 }}>
            <Sparkles style={{ width: 12, height: 12 }} /> {enrichNotice}
          </div>
        )}
      </div>

      {/* 3. Streamlined Toolbar: Search, Swimlanes, and Filter Controls */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        {/* Swimlane Tabs */}
        <div className="swimlane-tab-bar" style={{ margin: 0 }}>
          <button
            type="button"
            className={`swimlane-tab ${selectedSwimlane === "all" ? "active" : ""}`}
            onClick={() => setSelectedSwimlane("all")}
          >
            All Files ({reportsList.length})
          </button>
          <button
            type="button"
            className={`swimlane-tab ${selectedSwimlane === "sje" ? "active" : ""}`}
            onClick={() => setSelectedSwimlane("sje")}
          >
            SJE Court ({reportsList.filter((r) => getClaimArchetype(r).key === "sje").length})
          </button>
          <button
            type="button"
            className={`swimlane-tab ${selectedSwimlane === "tma" ? "active" : ""}`}
            onClick={() => setSelectedSwimlane("tma")}
          >
            TMA Expressway ({reportsList.filter((r) => getClaimArchetype(r).key === "tma").length})
          </button>
          <button
            type="button"
            className={`swimlane-tab ${selectedSwimlane === "tp" ? "active" : ""}`}
            onClick={() => setSelectedSwimlane("tp")}
          >
            TP Conventional ({reportsList.filter((r) => { const k = getClaimArchetype(r).key; return k === "tp-conv" || k === "tp-direct"; }).length})
          </button>
          <button
            type="button"
            className={`swimlane-tab ${selectedSwimlane === "od" ? "active" : ""}`}
            onClick={() => setSelectedSwimlane("od")}
          >
            OD Panel ({reportsList.filter((r) => getClaimArchetype(r).key === "od").length})
          </button>
          <button
            type="button"
            className={`swimlane-tab ${selectedSwimlane === "signed" ? "active" : ""}`}
            onClick={() => setSelectedSwimlane("signed")}
          >
            Signed Off ({signedCount})
          </button>
        </div>

        {/* Omni-Search & Insurer Filter */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <div style={{ position: "relative", minWidth: 240 }}>
            <Search style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", width: 13, height: 13, color: "var(--text-muted)" }} />
            <input
              type="text"
              placeholder="Search plate, Ref, vehicle..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              style={{
                width: "100%",
                padding: "6px 10px 6px 30px",
                borderRadius: 8,
                border: "1px solid var(--border-color)",
                background: "var(--surface-card)",
                color: "var(--text-primary)",
                fontSize: 12,
                outline: "none",
              }}
            />
          </div>

          <select
            value={selectedInsurer}
            onChange={(e) => setSelectedInsurer(e.target.value)}
            style={{
              background: "var(--surface-elevated)",
              color: "var(--text-primary)",
              border: "1px solid var(--border-color)",
              borderRadius: 8,
              padding: "5px 10px",
              fontSize: 12,
              outline: "none",
            }}
          >
            <option value="all">All Insurers</option>
            {insurerOptions.map((ins) => (
              <option key={ins} value={ins}>{ins}</option>
            ))}
          </select>

          {(quickFilter !== "all" || selectedInsurer !== "all" || searchTerm || selectedSwimlane !== "all") && (
            <button
              type="button"
              onClick={() => {
                setQuickFilter("all");
                setSelectedInsurer("all");
                setSearchTerm("");
                setSelectedSwimlane("all");
              }}
              style={{
                fontSize: 11,
                background: "none",
                border: "none",
                color: "var(--accent-cyan)",
                cursor: "pointer",
                textDecoration: "underline",
              }}
            >
              Reset
            </button>
          )}
        </div>
      </div>

      {/* 4. MAIN WORKSTATION VIEW (MODE A: VIEWPORT-FITTED SPLIT DOSSIER vs MODE B: DENSE MATRIX) */}
      {viewMode === "split" ? (
        <div className="workstation-split-container">
          {/* LEFT COLUMN: Case Queue List */}
          <div className="case-queue-pane">
            <div className="case-queue-header">
              <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary)" }}>
                Queue ({filteredReports.length})
              </span>
              <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                &uarr; &darr; navigate
              </span>
            </div>

            <div className="case-queue-scrollable">
              {filteredReports.map((r) => {
                const isSelected = r.id === selectedReportId;
                const arch = getClaimArchetype(r);
                const cost = getEstimatedCostSGD(r);
                const ref = formatCarlinkRef(r.id, r.plate_number);
                const vehicle = inferVehicleName(r);
                const insurer = inferInsurerName(r);
                const workshop = inferWorkshopName(r.workshop_assigned || r.location);
                const sla = getGiaSla(r.created_at, r.status === "Signed Off");

                return (
                  <div
                    key={r.id}
                    className={`case-queue-item ${isSelected ? "active" : ""}`}
                    onClick={() => setSelectedReportId(r.id)}
                  >
                    {/* Top Row: Plate, Ref, Reserve */}
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span className="badge-plate-command">
                          {r.plate_number || r.id.slice(0, 8).toUpperCase()}
                        </span>
                        <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-muted)", fontWeight: 600 }}>
                          {ref}
                        </span>
                      </div>

                      <span style={{ fontFamily: "var(--font-mono)", fontWeight: 800, fontSize: 12, color: "var(--accent-cyan)" }}>
                        ${cost.toLocaleString()}
                      </span>
                    </div>

                    {/* Mid Row: Vehicle Identity & Parts */}
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 11 }}>
                      <span style={{ fontWeight: 700, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {vehicle}
                      </span>
                      <span style={{ fontSize: 10, color: "var(--text-muted)", flexShrink: 0 }}>
                        {r.damage_count} part{r.damage_count === 1 ? "" : "s"}
                      </span>
                    </div>

                    {/* Insurer & Workshop */}
                    <div style={{ fontSize: 10, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {insurer} &bull; {workshop.split("(")[0].trim()}
                    </div>

                    {/* Bottom Row: Exact Filing Time & SLA Badge */}
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, fontSize: 10, marginTop: 2, paddingTop: 4, borderTop: "1px solid var(--border-color)" }}>
                      <span style={{ color: "var(--text-muted)", fontSize: 10 }}>
                        📥 {formatSgtDateTime(r.created_at)}
                      </span>

                      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                        <span style={{ width: 6, height: 6, borderRadius: "50%", background: sla.pillColor }} />
                        <span style={{ fontWeight: 700, color: sla.pillColor }}>
                          {sla.badgeText}
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })}

              {filteredReports.length === 0 && (
                <div style={{ textAlign: "center", padding: 36, color: "var(--text-muted)", fontSize: 12 }}>
                  No cases match current filter criteria.
                </div>
              )}
            </div>
          </div>

          {/* RIGHT COLUMN: Interactive Active Case Dossier */}
          <div className="case-dossier-pane">
            {selectedSummary ? (
              <>
                {/* Dossier Header */}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap", paddingBottom: 12, borderBottom: "1px solid var(--border-color)" }}>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <span className="badge-plate-command" style={{ fontSize: 14, padding: "3px 10px" }}>
                        {selectedSummary.plate_number || selectedSummary.id.slice(0, 8).toUpperCase()}
                      </span>
                      <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 700, color: "var(--accent-cyan)" }}>
                        {formatCarlinkRef(selectedSummary.id, selectedSummary.plate_number)}
                      </span>
                      <span
                        style={{
                          fontSize: 10,
                          fontWeight: 700,
                          padding: "3px 8px",
                          borderRadius: 4,
                          textTransform: "uppercase",
                          background: "var(--surface-hover)",
                          color: "var(--text-primary)",
                          border: "1px solid var(--border-color)",
                        }}
                      >
                        {getClaimArchetype(selectedSummary).label}
                      </span>
                      {selectedSummary.status === "Signed Off" ? (
                        <span className="status-pill" style={{ background: "var(--badge-green-bg)", color: "var(--badge-green-text)", borderColor: "var(--badge-green-border)", fontSize: 10 }}>
                          Certified &amp; Signed Off
                        </span>
                      ) : (
                        <span className="status-pill" style={{ background: "var(--badge-amber-bg)", color: "var(--badge-amber-text)", borderColor: "var(--badge-amber-border)", fontSize: 10 }}>
                          Pending Review
                        </span>
                      )}
                    </div>

                    <div style={{ fontSize: 15, fontWeight: 800, color: "var(--text-primary)", marginTop: 6 }}>
                      {inferVehicleName(selectedSummary)} &bull; {selectedSummary.damage_count} Damaged Components
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                      Insurer: <strong style={{ color: "var(--text-primary)" }}>{inferInsurerName(selectedSummary)}</strong> &bull; Workshop: <strong style={{ color: "var(--text-primary)" }}>{inferWorkshopName(selectedSummary.workshop_assigned || selectedSummary.location)}</strong>
                    </div>
                  </div>

                  {/* Dossier Quick Action Buttons */}
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <button
                      type="button"
                      onClick={handleEnrichCurrent}
                      disabled={isEnrichingCurrent}
                      className="btn-secondary-modern"
                      style={{ fontSize: 11, padding: "6px 12px", display: "inline-flex", alignItems: "center", gap: 5 }}
                      title="Auto-enrich vehicle specifications, quantum and insurer details"
                    >
                      <Sparkles style={{ width: 12, height: 12, color: "var(--accent-cyan)" }} />
                      <span>{isEnrichingCurrent ? "Enriching..." : "AI Auto-Map"}</span>
                    </button>

                    <button
                      type="button"
                      onClick={() => setPreviewReport(selectedSummary)}
                      className="btn-secondary-modern"
                      style={{ fontSize: 11, padding: "6px 12px", display: "inline-flex", alignItems: "center", gap: 5 }}
                      title="Preview generated loss adjuster report PDF"
                    >
                      <FileText style={{ width: 12, height: 12, color: "var(--accent-primary)" }} />
                      <span>PDF</span>
                    </button>

                    <Link
                      href={`/reports/${selectedSummary.id}`}
                      className="btn-primary-modern"
                      style={{ fontSize: 11, padding: "6px 14px", display: "inline-flex", alignItems: "center", gap: 5 }}
                      title="Open full Forensic Inspection Studio"
                    >
                      <span>Studio</span>
                      <ArrowRight style={{ width: 12, height: 12 }} />
                    </Link>

                    <DeleteButton id={selectedSummary.id} />
                  </div>
                </div>

                {/* Statutory Incident & Filing Audit Strip */}
                <div className="dossier-audit-strip">
                  <div className="dossier-audit-item">
                    <span className="dossier-audit-label">
                      <Calendar style={{ width: 11, height: 11, color: "var(--accent-cyan)" }} /> Intake Filed
                    </span>
                    <span className="dossier-audit-value">
                      {formatSgtDateTime(selectedSummary.created_at)}
                    </span>
                    <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                      Origin: {selectedSummary.channel === "telegram" ? "Telegram Bot (@carlink_bot)" : "Surveyor Console"}
                    </span>
                  </div>

                  <div className="dossier-audit-item">
                    <span className="dossier-audit-label">
                      <Clock style={{ width: 11, height: 11, color: "var(--badge-amber-text)" }} /> Incident Occurred
                    </span>
                    <span className="dossier-audit-value">
                      {formatIncidentTime(selectedSummary.incident_datetime, selectedSummary.created_at)}
                    </span>
                    <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                      Location: {selectedSummary.location || "Singapore Island-Wide"}
                    </span>
                  </div>

                  <div className="dossier-audit-item">
                    <span className="dossier-audit-label">
                      <ShieldCheck style={{ width: 11, height: 11, color: "var(--badge-green-text)" }} /> Statutory SLA Window
                    </span>
                    <span className="dossier-audit-value" style={{ color: getGiaSla(selectedSummary.created_at, selectedSummary.status === "Signed Off").pillColor }}>
                      {getGiaSla(selectedSummary.created_at, selectedSummary.status === "Signed Off").remainingText}
                    </span>
                    <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                      Framework: GIA / IDAC 48h Audit
                    </span>
                  </div>
                </div>

                {/* Evidence Photo HUD */}
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <span style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", color: "var(--text-muted)", letterSpacing: "0.05em" }}>
                      Photographic Forensic Evidence
                    </span>
                    <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                      {activeDetail?.photo_urls?.length ? `${activeDetail.photo_urls.length} Evidence Photos on File` : "1 Thumbnail Available"}
                    </span>
                  </div>

                  {/* Main Hero Viewer Frame */}
                  <div className="dossier-hero-frame">
                    {isLoadingDetail ? (
                      <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Loading high-resolution evidence...</div>
                    ) : activeDetail?.photo_urls?.length ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={fileUrl(activeDetail.photo_urls[activePhotoIdx] || activeDetail.photo_urls[0])}
                        alt="Evidence"
                        className="dossier-hero-img"
                      />
                    ) : selectedSummary.thumbnail_url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={fileUrl(selectedSummary.thumbnail_url)}
                        alt="Evidence"
                        className="dossier-hero-img"
                      />
                    ) : (
                      <div style={{ textAlign: "center", color: "var(--text-muted)" }}>
                        <CarFront style={{ width: 32, height: 32, margin: "0 auto 6px", opacity: 0.5 }} />
                        <div style={{ fontSize: 11 }}>No photographic evidence uploaded</div>
                      </div>
                    )}
                  </div>

                  {/* Thumbnail Strip */}
                  {activeDetail?.photo_urls && activeDetail.photo_urls.length > 1 && (
                    <div className="dossier-thumb-strip" style={{ marginTop: 8 }}>
                      {activeDetail.photo_urls.map((url, idx) => (
                        <button
                          key={url}
                          type="button"
                          className={`dossier-thumb-btn ${idx === activePhotoIdx ? "active" : ""}`}
                          onClick={() => setActivePhotoIdx(idx)}
                          title={`Photo ${idx + 1}`}
                        >
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={fileUrl(url)} alt="" className="dossier-thumb-img" />
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {/* Financial Reserves & Settlement Assessment Deck */}
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, background: "var(--surface-hover)", padding: 12, borderRadius: 8, border: "1px solid var(--border-color)" }}>
                  <div>
                    <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 700 }}>Assessed Reserve</div>
                    <div style={{ fontSize: 15, fontWeight: 800, color: "var(--accent-cyan)", fontFamily: "var(--font-mono)", marginTop: 2 }}>
                      ${getEstimatedCostSGD(selectedSummary).toLocaleString()} <span style={{ fontSize: 10 }}>SGD</span>
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 700 }}>Negotiated Savings</div>
                    <div style={{ fontSize: 15, fontWeight: 800, color: "var(--badge-green-text)", fontFamily: "var(--font-mono)", marginTop: 2 }}>
                      ${Math.round(getEstimatedCostSGD(selectedSummary) * 0.21).toLocaleString()} <span style={{ fontSize: 10 }}>SGD (~21%)</span>
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 700 }}>Cluster Workshop</div>
                    <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary)", marginTop: 4 }}>
                      {inferWorkshopName(selectedSummary.workshop_assigned || selectedSummary.location)}
                    </div>
                  </div>
                </div>

                {/* Damaged Parts Anatomy Matrix */}
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <span style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", color: "var(--text-muted)", letterSpacing: "0.05em" }}>
                      Damage Anatomy &amp; Line-Item Estimations
                    </span>
                    <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                      AI Vision Audited
                    </span>
                  </div>

                  {activeDetail?.data?.damage_summary && activeDetail.data.damage_summary.length > 0 ? (
                    <div style={{ border: "1px solid var(--border-color)", borderRadius: 8, overflow: "hidden" }}>
                      <table className="triage-table">
                        <thead>
                          <tr>
                            <th>Identified Component</th>
                            <th>Damage Classification</th>
                            <th>Severity</th>
                            <th>Est. Quantum</th>
                            <th>Action</th>
                          </tr>
                        </thead>
                        <tbody>
                          {activeDetail.data.damage_summary.map((d, i) => {
                            const partPrice = PART_PRICES_MAP[d.part] || 1200;
                            return (
                              <tr key={i}>
                                <td style={{ fontWeight: 600, color: "var(--text-primary)" }}>{d.part}</td>
                                <td style={{ color: "var(--text-secondary)" }}>{d.damage_type || "Impact Damage"}</td>
                                <td>
                                  <span
                                    style={{
                                      fontSize: 10,
                                      fontWeight: 700,
                                      padding: "2px 6px",
                                      borderRadius: 4,
                                      textTransform: "uppercase",
                                      background:
                                        d.severity?.toLowerCase() === "severe" ? "var(--badge-red-bg)" :
                                        d.severity?.toLowerCase() === "moderate" ? "var(--badge-amber-bg)" :
                                        "var(--badge-green-bg)",
                                      color:
                                        d.severity?.toLowerCase() === "severe" ? "var(--badge-red-text)" :
                                        d.severity?.toLowerCase() === "moderate" ? "var(--badge-amber-text)" :
                                        "var(--badge-green-text)",
                                    }}
                                  >
                                    {d.severity || "Assessed"}
                                  </span>
                                </td>
                                <td style={{ fontFamily: "var(--font-mono)", fontWeight: 700, fontSize: 11, color: "var(--accent-cyan)" }}>
                                  ${partPrice.toLocaleString()} SGD
                                </td>
                                <td style={{ fontSize: 11, color: "var(--text-muted)" }}>
                                  {d.repair_required ? "Repair / Rectify" : "Replace Component"}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div style={{ padding: 12, borderRadius: 8, background: "var(--surface-hover)", border: "1px solid var(--border-color)", fontSize: 11, color: "var(--text-muted)" }}>
                      Damage summary: {selectedSummary.damage_count} component{selectedSummary.damage_count === 1 ? "" : "s"} detected. Severity: <strong>{selectedSummary.severity_level || "Assessed"}</strong>.
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div style={{ textAlign: "center", padding: 60, color: "var(--text-muted)" }}>
                Select a case from the queue to view complete loss adjuster dossier.
              </div>
            )}
          </div>
        </div>
      ) : (
        /* MODE B: DENSE SPREADSHEET MATRIX (Full Table View) */
        <div className="card-glass triage-table-card" style={{ padding: 0 }}>
          <div className="triage-table-wrapper" style={{ margin: 0, maxHeight: 680 }}>
            <table className="triage-table">
              <thead>
                <tr>
                  <th>Evidence</th>
                  <th>Vehicle Registration</th>
                  <th>Case Ref &amp; Intake Time</th>
                  <th>Claim Archetype</th>
                  <th>Insurer // Workshop</th>
                  <th className="col-reserve">Reserve ($ SGD)</th>
                  <th>SLA Window</th>
                  <th className="col-actions">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredReports.map((r) => {
                  const arch = getClaimArchetype(r);
                  const cost = getEstimatedCostSGD(r);
                  const ref = formatCarlinkRef(r.id, r.plate_number);
                  const vehicle = inferVehicleName(r);
                  const insurer = inferInsurerName(r);
                  const workshop = inferWorkshopName(r.workshop_assigned || r.location);
                  const sla = getGiaSla(r.created_at, r.status === "Signed Off");

                  return (
                    <tr key={r.id}>
                      <td>
                        {r.thumbnail_url ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img
                            src={fileUrl(r.thumbnail_url)}
                            alt=""
                            style={{ width: 48, height: 36, borderRadius: 4, objectFit: "cover", border: "1px solid var(--border-color)" }}
                            loading="lazy"
                          />
                        ) : (
                          <div style={{ width: 48, height: 36, borderRadius: 4, background: "var(--surface-hover)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                            <CarFront style={{ width: 16, height: 16, color: "var(--text-muted)" }} />
                          </div>
                        )}
                      </td>

                      <td>
                        <div>
                          <span className="badge-plate-command">
                            {r.plate_number || r.id.slice(0, 8).toUpperCase()}
                          </span>
                          <div className="triage-cell-truncate" style={{ fontSize: 11, color: "var(--text-primary)", fontWeight: 700, marginTop: 2 }}>
                            {vehicle}
                          </div>
                        </div>
                      </td>

                      <td>
                        <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 700, color: "var(--accent-cyan)" }}>
                          {ref}
                        </div>
                        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
                          📥 {formatSgtDateTime(r.created_at)}
                        </div>
                      </td>

                      <td>
                        <div>
                          <span
                            style={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 4,
                              fontSize: 10,
                              fontWeight: 700,
                              padding: "3px 8px",
                              borderRadius: 4,
                              textTransform: "uppercase",
                              background:
                                arch.key === "sje" ? "rgba(168, 85, 247, 0.15)" :
                                arch.key === "tma" ? "var(--badge-red-bg)" :
                                arch.key === "tp-direct" ? "rgba(56, 189, 248, 0.15)" :
                                "var(--badge-amber-bg)",
                              color:
                                arch.key === "sje" ? "#c084fc" :
                                arch.key === "tma" ? "var(--badge-red-text)" :
                                arch.key === "tp-direct" ? "var(--accent-cyan)" :
                                "var(--badge-amber-text)",
                              border: `1px solid ${
                                arch.key === "sje" ? "rgba(168, 85, 247, 0.3)" :
                                arch.key === "tma" ? "var(--badge-red-border)" :
                                arch.key === "tp-direct" ? "rgba(56, 189, 248, 0.3)" :
                                "var(--badge-amber-border)"
                              }`,
                            }}
                          >
                            {arch.label}
                          </span>
                          {r.disassembly_required && (
                            <div style={{ fontSize: 10, color: "var(--badge-amber-text)", marginTop: 2, display: "flex", alignItems: "center", gap: 3 }}>
                              <Wrench style={{ width: 10, height: 10 }} /> Strip-Down Req
                            </div>
                          )}
                        </div>
                      </td>

                      <td>
                        <div className="triage-cell-truncate" style={{ fontSize: 11, fontWeight: 700, color: "var(--text-primary)" }}>
                          {insurer}
                        </div>
                        <div className="triage-cell-truncate" style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 1 }}>
                          {workshop}
                        </div>
                      </td>

                      <td className="col-reserve">
                        <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, fontSize: 12, color: "var(--accent-cyan)" }}>
                          ${cost.toLocaleString()}
                        </div>
                        <div style={{ fontSize: 10, color: "var(--text-muted)" }}>SGD Assessed</div>
                      </td>

                      <td>
                        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                          <span
                            style={{
                              width: 7,
                              height: 7,
                              borderRadius: "50%",
                              background: sla.pillColor,
                            }}
                          />
                          <span style={{ fontSize: 11, fontWeight: 700, color: sla.pillColor }}>
                            {sla.badgeText}
                          </span>
                        </div>
                        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 1 }}>
                          {r.damage_count} part{r.damage_count === 1 ? "" : "s"}
                        </div>
                      </td>

                      <td className="col-actions">
                        <div style={{ display: "inline-flex", alignItems: "center", gap: 6, justifyContent: "flex-end" }}>
                          <button
                            type="button"
                            onClick={() => setPreviewReport(r)}
                            className="btn-secondary-modern"
                            style={{ fontSize: 11, padding: "4px 8px", display: "inline-flex", alignItems: "center", gap: 4 }}
                            title="Instant in-console PDF preview"
                          >
                            <FileText style={{ width: 12, height: 12, color: "var(--accent-primary)" }} />
                            <span>Preview</span>
                          </button>

                          <Link
                            href={`/reports/${r.id}`}
                            className="btn-primary-modern"
                            style={{ fontSize: 11, padding: "4px 9px", display: "inline-flex", alignItems: "center", gap: 3 }}
                            title="Open full Loss Adjuster Studio"
                          >
                            <span>Studio</span>
                            <ArrowRight style={{ width: 12, height: 12 }} />
                          </Link>

                          <DeleteButton id={r.id} />
                        </div>
                      </td>
                    </tr>
                  );
                })}

                {filteredReports.length === 0 && (
                  <tr>
                    <td colSpan={8} style={{ textAlign: "center", padding: 36, color: "var(--text-muted)" }}>
                      No cases match current filter criteria.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* In-Console Lightbox PDF Preview Modal */}
      {previewReport && (
        <PdfPreviewModal
          reportId={previewReport.id}
          reportCode={previewReport.plate_number || previewReport.id.slice(0, 8).toUpperCase()}
          isOpen={true}
          onClose={() => setPreviewReport(null)}
          trigger={null}
        />
      )}
    </div>
  );
}
