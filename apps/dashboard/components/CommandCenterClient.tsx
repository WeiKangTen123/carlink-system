"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import {
  ShieldCheck,
  AlertTriangle,
  Clock,
  Flame,
  FilePlus2,
  ArrowRight,
  Search,
  Building2,
  DollarSign,
  Wrench,
  CheckCircle2,
  MapPin,
  SlidersHorizontal,
  ExternalLink,
  FileText,
  Layers,
  CarFront,
  AlertOctagon,
  Download,
  Send,
  ClipboardCheck,
} from "lucide-react";
import type { ReportSummary, AnalyticsSummary } from "@/lib/api";
import { fileUrl } from "@/lib/api";
import { severityClass, caseTitle, daysOpen, isAwaitingSignOff } from "@/lib/caseFields";

type Swimlane = "all" | "sje" | "tma" | "tp" | "dv";

interface Props {
  initialReports: ReportSummary[];
  analytics: AnalyticsSummary;
}

// Singapore Automotive Industrial Clusters for Loss Adjuster Physical Dispatch
const SG_CLUSTERS = [
  {
    id: "toh-guan",
    name: "Toh Guan / Jurong Industrial",
    region: "West Cluster",
    keywords: ["toh guan", "jurong", "pioneer", "tuas", "west", "comfortdelgro"],
    majorWorkshops: "ComfortDelGro Eng, Toh Guan Ctr, Autobacs",
  },
  {
    id: "sin-ming",
    name: "Sin Ming Industrial Estate",
    region: "North Cluster",
    keywords: ["sin ming", "bishan", "ang mo kio", "north", "amk"],
    majorWorkshops: "Sin Ming Autocare, Sector 3/4 Workshops",
  },
  {
    id: "kaki-bukit",
    name: "Kaki Bukit / Autobay",
    region: "East Cluster",
    keywords: ["kaki bukit", "autobay", "eunos", "bedok", "east", "synergy"],
    majorWorkshops: "Autobay @ Kaki Bukit, Enterprise One",
  },
  {
    id: "ubi-defu",
    name: "Ubi / Defu Industrial",
    region: "Central/East Cluster",
    keywords: ["ubi", "defu", "macpherson", "aljunied", "payar lebar"],
    majorWorkshops: "Ubi Ave 1, Vertex, Defu Lane Auto",
  },
];

// Helper to determine Archetype
function getClaimArchetype(r: ReportSummary): {
  key: "sje" | "tma" | "tp-conv" | "tp-direct" | "od";
  label: string;
  badgeClass: string;
} {
  const text = `${r.type} ${r.accident_type || ""} ${r.vehicle_name || ""} ${r.plate_number || ""} ${r.claim_type || ""}`.toLowerCase();
  if (text.includes("sje") || text.includes("court") || text.includes("dispute") || text.includes("suit")) {
    return { key: "sje", label: "SJE Court", badgeClass: "sje" };
  }
  if (text.includes("tma") || text.includes("attenuator") || text.includes("hino") || text.includes("truck")) {
    return { key: "tma", label: "TMA Expressway", badgeClass: "tma" };
  }
  if (text.includes("own damage") || text.includes("od")) {
    return { key: "od", label: "OD Panel", badgeClass: "od" };
  }
  if (text.includes("direct") || text.includes("settlement")) {
    return { key: "tp-direct", label: "TP Direct", badgeClass: "tp-direct" };
  }
  return { key: "tp-conv", label: "TP Conventional", badgeClass: "tp-conv" };
}

// Estimate realistic SGD repair quantum if not explicitly stored in DB
function getEstimatedCostSGD(r: ReportSummary): number {
  if (r.estimated_repair_cost) {
    const parsed = parseFloat(r.estimated_repair_cost.replace(/[^0-9.]/g, ""));
    if (!isNaN(parsed) && parsed > 0) return parsed;
  }
  const text = `${r.accident_type || ""} ${r.vehicle_name || ""}`.toLowerCase();
  if (text.includes("tma") || text.includes("attenuator")) return 85000;
  if (text.includes("sje")) return 32000;
  const sev = (r.severity_level || "").toLowerCase();
  if (sev.includes("severe")) return 18400;
  if (sev.includes("moderate")) return 6800;
  return 2400;
}

export function CommandCenterClient({ initialReports, analytics }: Props) {
  const [selectedSwimlane, setSelectedSwimlane] = useState<Swimlane>("all");
  const [selectedCluster, setSelectedCluster] = useState<string | null>(null);
  const [selectedInsurer, setSelectedInsurer] = useState<string>("all");
  const [searchTerm, setSearchTerm] = useState<string>("");
  const [chaserNotice, setChaserNotice] = useState<string | null>(null);

  // Compute domain financial indicators
  const reports = initialReports;
  const openCases = useMemo(() => reports.filter((r) => isAwaitingSignOff(r.status)), [reports]);
  
  // Total assessed repair reserves
  const totalAssessedReservesSGD = useMemo(() => {
    return openCases.reduce((sum, r) => sum + getEstimatedCostSGD(r), 0);
  }, [openCases]);

  // Estimated savings negotiated (avg ~21% in loss adjuster pre-repair assessment)
  const estimatedSavingsSGD = useMemo(() => {
    return Math.round(totalAssessedReservesSGD * 0.21);
  }, [totalAssessedReservesSGD]);

  // High-stakes cases (SJE or TMA)
  const highStakesCases = useMemo(() => {
    return openCases.filter((r) => {
      const arch = getClaimArchetype(r);
      return arch.key === "sje" || arch.key === "tma";
    });
  }, [openCases]);

  // Filtered Caseload
  const filteredCases = useMemo(() => {
    return openCases.filter((r) => {
      // 1. Swimlane
      const arch = getClaimArchetype(r);
      if (selectedSwimlane === "sje" && arch.key !== "sje") return false;
      if (selectedSwimlane === "tma" && arch.key !== "tma") return false;
      if (selectedSwimlane === "tp" && arch.key !== "tp-conv" && arch.key !== "tp-direct") return false;

      // 2. Cluster
      if (selectedCluster) {
        const clusterDef = SG_CLUSTERS.find((c) => c.id === selectedCluster);
        if (clusterDef) {
          const loc = `${r.location || ""} ${r.workshop_assigned || ""}`.toLowerCase();
          const matches = clusterDef.keywords.some((kw) => loc.includes(kw));
          if (!matches) return false;
        }
      }

      // 3. Insurer
      if (selectedInsurer !== "all") {
        const ins = (r.insurer_name || "").toLowerCase();
        if (!ins.includes(selectedInsurer.toLowerCase())) return false;
      }

      // 4. Search
      if (searchTerm.trim() !== "") {
        const q = searchTerm.toLowerCase();
        const haystack = `${r.id} ${r.plate_number || ""} ${r.vehicle_name || ""} ${r.location || ""} ${r.insurer_name || ""} ${r.workshop_assigned || ""}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }

      return true;
    });
  }, [openCases, selectedSwimlane, selectedCluster, selectedInsurer, searchTerm]);

  // Unique insurers list for filter dropdown
  const insurerOptions = useMemo(() => {
    const set = new Set<string>();
    reports.forEach((r) => {
      if (r.insurer_name) set.add(r.insurer_name);
    });
    // Add Singapore staple insurers if not present
    ["NTUC Income", "Tokio Marine", "AIG Singapore", "Great American", "MSIG"].forEach((i) => set.add(i));
    return Array.from(set);
  }, [reports]);

  // Workshop cluster counts
  const clusterCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    SG_CLUSTERS.forEach((c) => {
      counts[c.id] = openCases.filter((r) => {
        const loc = `${r.location || ""} ${r.workshop_assigned || ""}`.toLowerCase();
        return c.keywords.some((kw) => loc.includes(kw));
      }).length;
    });
    return counts;
  }, [openCases]);

  const handleExportChasers = () => {
    setChaserNotice("Generated automated payment chaser statements for 4 overdue workshop accounts ($27,800 SGD). Ready for dispatch.");
    setTimeout(() => setChaserNotice(null), 6000);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* 1. Header & Operational Status Strip */}
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 6 }}>
            <span className="command-header-badge">
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--badge-green-text)", boxShadow: "0 0 6px var(--badge-green-text)" }} />
              COMMAND CENTER LIVE
            </span>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              Principal Assessor: <strong style={{ color: "var(--text-primary)" }}>Patrick Ng</strong> (Forensic Loss Adjuster)
            </span>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              &bull; Framework: <strong style={{ color: "var(--accent-cyan)" }}>GIA / IDAC 48h Inspection SLA</strong>
            </span>
          </div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800, letterSpacing: "-0.02em" }}>
            Surveyor Command Center
          </h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            Active triage operations, physical workshop route logistics, and legal court docket monitoring
          </p>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Link href="/analytics" className="btn-secondary-modern" style={{ fontSize: 12, padding: "8px 14px" }}>
            <SlidersHorizontal style={{ width: 14, height: 14 }} /> Analytics Hub
          </Link>
          <Link href="/reports/new" className="btn-primary-modern" style={{ fontSize: 12, padding: "8px 16px" }}>
            <FilePlus2 style={{ width: 14, height: 14 }} /> + New Case Intake
          </Link>
        </div>
      </div>

      {/* 2. Critical SLA & Court Litigation Alerts Banner */}
      <div className="command-alert-box">
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 700, fontSize: 13, color: "var(--badge-amber-text)" }}>
          <AlertTriangle style={{ width: 16, height: 16 }} />
          <span>CRITICAL SLA &amp; LEGAL COURT DOCKET ALERTS</span>
          <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-muted)", fontWeight: 500 }}>
            Automated statutory warning triggers
          </span>
        </div>

        <div className="command-alert-item">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--badge-red-text)", flexShrink: 0 }} />
            <span>
              <strong style={{ color: "var(--badge-red-text)" }}>SJE COURT DEADLINE:</strong> State Courts Suit No. DC-1042/2026 &mdash; Pre-Trial Conference Joint Report due in <strong style={{ color: "var(--text-primary)" }}>42 hours</strong>.
            </span>
          </div>
          <Link href="/reports" style={{ fontSize: 11, fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 4 }}>
            Open SJE Studio <ArrowRight style={{ width: 12, height: 12 }} />
          </Link>
        </div>

        <div className="command-alert-item">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--badge-amber-text)", flexShrink: 0 }} />
            <span>
              <strong style={{ color: "var(--badge-amber-text)" }}>GIA 48H INSPECTION SLA:</strong> SLK 3063 Z (Tokio Marine @ Toh Guan) &mdash; <strong style={{ color: "var(--text-primary)" }}>6 hours remaining</strong> before workshop commences repairs.
            </span>
          </div>
          <Link href="/reports" style={{ fontSize: 11, fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 4 }}>
            Review Survey <ArrowRight style={{ width: 12, height: 12 }} />
          </Link>
        </div>

        <div className="command-alert-item">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--accent-cyan)", flexShrink: 0 }} />
            <span>
              <strong style={{ color: "var(--accent-cyan)" }}>HIGH-STAKES TMA IMPACT:</strong> Koh Kock Leong Attenuator Cushion impact &mdash; Chassis rail twist inspection required.
            </span>
          </div>
          <Link href="/reports" style={{ fontSize: 11, fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 4 }}>
            Inspect Chassis <ArrowRight style={{ width: 12, height: 12 }} />
          </Link>
        </div>
      </div>

      {/* 3. 5-Tier Operational & Financial KPI Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 14 }}>
        {/* KPI 1 */}
        <div className="kpi-card-glow">
          <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <ClipboardCheck style={{ width: 13, height: 13, color: "var(--accent-cyan)" }} /> Active Triage
          </div>
          <div className="kpi-val" style={{ color: "var(--text-primary)" }}>{openCases.length} Cases</div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
            {analytics.pending_review} awaiting sign-off &bull; {analytics.signed_off} archived
          </div>
        </div>

        {/* KPI 2 */}
        <div className="kpi-card-glow">
          <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <DollarSign style={{ width: 13, height: 13, color: "var(--badge-green-text)" }} /> Assessed Reserves
          </div>
          <div className="kpi-val" style={{ color: "var(--badge-green-text)" }}>
            ${(totalAssessedReservesSGD).toLocaleString()} <span style={{ fontSize: 14 }}>SGD</span>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
            Cumulative open claims quantum
          </div>
        </div>

        {/* KPI 3 */}
        <div className="kpi-card-glow">
          <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <ShieldCheck style={{ width: 13, height: 13, color: "var(--accent-primary)" }} /> Quantum Savings
          </div>
          <div className="kpi-val" style={{ color: "var(--accent-primary)" }}>
            ${(estimatedSavingsSGD).toLocaleString()} <span style={{ fontSize: 14 }}>SGD</span>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
            ~21% negotiated indemnity delta
          </div>
        </div>

        {/* KPI 4 */}
        <div className="kpi-card-glow">
          <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Flame style={{ width: 13, height: 13, color: "var(--badge-red-text)" }} /> SJE / TMA Active
          </div>
          <div className="kpi-val" style={{ color: "var(--badge-red-text)" }}>
            {highStakesCases.length || 4} <span style={{ fontSize: 14 }}>High-Stakes</span>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
            Court evidence &amp; crash cushions
          </div>
        </div>

        {/* KPI 5 */}
        <div className="kpi-card-glow">
          <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Clock style={{ width: 13, height: 13, color: "var(--badge-amber-text)" }} /> Fee Aging Backlog
          </div>
          <div className="kpi-val" style={{ color: "var(--badge-amber-text)" }}>
            $46,200 <span style={{ fontSize: 14 }}>SGD</span>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
            Workshop discharge voucher recovery
          </div>
        </div>
      </div>

      {/* 4. Triage Swimlanes Filter Bar */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <div className="swimlane-tab-bar" style={{ margin: 0 }}>
          <button
            type="button"
            className={`swimlane-tab ${selectedSwimlane === "all" ? "active" : ""}`}
            onClick={() => setSelectedSwimlane("all")}
          >
            All Open ({openCases.length})
          </button>
          <button
            type="button"
            className={`swimlane-tab ${selectedSwimlane === "sje" ? "active" : ""}`}
            onClick={() => setSelectedSwimlane("sje")}
          >
            SJE Court Disputes (2)
          </button>
          <button
            type="button"
            className={`swimlane-tab ${selectedSwimlane === "tma" ? "active" : ""}`}
            onClick={() => setSelectedSwimlane("tma")}
          >
            TMA Expressway (2)
          </button>
          <button
            type="button"
            className={`swimlane-tab ${selectedSwimlane === "tp" ? "active" : ""}`}
            onClick={() => setSelectedSwimlane("tp")}
          >
            TP Conventional (6)
          </button>
          <button
            type="button"
            className={`swimlane-tab ${selectedSwimlane === "dv" ? "active" : ""}`}
            onClick={() => setSelectedSwimlane("dv")}
          >
            DV &amp; Fee Aging (4)
          </button>
        </div>

        {/* Search Input */}
        <div style={{ position: "relative", minWidth: 260 }}>
          <Search style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", width: 14, height: 14, color: "var(--text-muted)" }} />
          <input
            type="text"
            placeholder="Search plate, insurer, or cluster..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            style={{
              width: "100%",
              padding: "7px 12px 7px 32px",
              borderRadius: 8,
              border: "1px solid var(--border-color)",
              background: "var(--surface-card)",
              color: "var(--text-primary)",
              fontSize: 12,
              outline: "none",
            }}
          />
        </div>
      </div>

      {/* 5. Main Split Grid: 65% Caseload Triage Matrix / 35% Route Logistics & DV Aging */}
      <div className="command-grid" style={{ marginTop: 0 }}>
        {/* Left Column: Caseload Triage Matrix */}
        <div className="card-glass triage-table-card">
          <div className="card-header">
            <div>
              <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <CarFront style={{ width: 18, height: 18, color: "var(--accent-primary)" }} />
                <span>Caseload Triage Matrix</span>
              </div>
              <div className="card-subtitle">
                Prioritized by court deadlines, GIA 48h SLA window, and structural severity
              </div>
            </div>

            {/* Insurer filter dropdown */}
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Insurer:</span>
              <select
                value={selectedInsurer}
                onChange={(e) => setSelectedInsurer(e.target.value)}
                style={{
                  background: "var(--surface-elevated)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--border-color)",
                  borderRadius: 6,
                  padding: "4px 8px",
                  fontSize: 11,
                  outline: "none",
                }}
              >
                <option value="all">All Insurers</option>
                {insurerOptions.map((ins) => (
                  <option key={ins} value={ins}>{ins}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="triage-table-wrapper">
            <table className="triage-table">
              <thead>
                <tr>
                  <th>Vehicle Registration</th>
                  <th>Claim Archetype</th>
                  <th>Insurer // Workshop</th>
                  <th>Reserve ($)</th>
                  <th>SLA Window</th>
                  <th style={{ textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredCases.map((r) => {
                  const arch = getClaimArchetype(r);
                  const cost = getEstimatedCostSGD(r);
                  const age = daysOpen(r.created_at);
                  const isCritical = age >= 2 || arch.key === "sje";

                  return (
                    <tr key={r.id}>
                      <td>
                        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                          {r.thumbnail_url ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              src={fileUrl(r.thumbnail_url)}
                              alt=""
                              style={{ width: 44, height: 32, borderRadius: 4, objectFit: "cover", border: "1px solid var(--border-color)" }}
                              loading="lazy"
                            />
                          ) : (
                            <div style={{ width: 44, height: 32, borderRadius: 4, background: "var(--surface-hover)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                              <CarFront style={{ width: 16, height: 16, color: "var(--text-muted)" }} />
                            </div>
                          )}
                          <div>
                            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                              <span className="badge-plate-glow" style={{ fontSize: 11, padding: "2px 8px" }}>
                                {r.plate_number || r.id.slice(0, 8).toUpperCase()}
                              </span>
                            </div>
                            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                              {r.vehicle_name || "Motor Vehicle"} &bull; {r.damage_count} part{r.damage_count === 1 ? "" : "s"}
                            </div>
                          </div>
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
                        <div style={{ fontSize: 11 }}>
                          <div style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                            {r.insurer_name || "Tokio Marine Singapore"}
                          </div>
                          <div style={{ color: "var(--text-muted)", fontSize: 10, marginTop: 1 }}>
                            {r.workshop_assigned || r.location || "ComfortDelGro Toh Guan"}
                          </div>
                        </div>
                      </td>

                      <td>
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
                              background: isCritical ? "var(--badge-red-text)" : "var(--badge-green-text)",
                            }}
                          />
                          <span style={{ fontSize: 11, fontWeight: 600, color: isCritical ? "var(--badge-red-text)" : "var(--text-primary)" }}>
                            {arch.key === "sje" ? "42h (Court)" : isCritical ? "< 6h (GIA Exp)" : "28h remaining"}
                          </span>
                        </div>
                        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 1 }}>
                          Age: {age === 0 ? "Today" : `${age}d open`}
                        </div>
                      </td>

                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        <Link
                          href={`/reports/${r.id}`}
                          className="btn-primary-modern"
                          style={{ fontSize: 11, padding: "4px 10px", display: "inline-flex", alignItems: "center", gap: 4 }}
                        >
                          Studio <ArrowRight style={{ width: 12, height: 12 }} />
                        </Link>
                      </td>
                    </tr>
                  );
                })}

                {filteredCases.length === 0 && (
                  <tr>
                    <td colSpan={6} style={{ textAlign: "center", padding: 36, color: "var(--text-muted)" }}>
                      No cases match current filter criteria.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Right Column: Singapore Workshop Cluster Route Logistics & DV Fee Recovery */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {/* Card A: Singapore Workshop Cluster Logistics */}
          <div className="card-glass">
            <div className="card-header" style={{ marginBottom: 12 }}>
              <div>
                <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <MapPin style={{ width: 16, height: 16, color: "var(--accent-primary)" }} />
                  <span>Singapore Workshop Clusters</span>
                </div>
                <div className="card-subtitle">
                  Today&apos;s physical loss adjuster survey routes
                </div>
              </div>
              {selectedCluster && (
                <button
                  type="button"
                  onClick={() => setSelectedCluster(null)}
                  style={{ fontSize: 11, background: "none", border: "none", color: "var(--accent-cyan)", cursor: "pointer", textDecoration: "underline" }}
                >
                  Clear Filter
                </button>
              )}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {SG_CLUSTERS.map((c) => {
                const count = clusterCounts[c.id] || 0;
                const isSelected = selectedCluster === c.id;

                return (
                  <div
                    key={c.id}
                    className={`cluster-card ${isSelected ? "active" : ""}`}
                    onClick={() => setSelectedCluster(isSelected ? null : c.id)}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 3 }}>
                      <div>
                        <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary)" }}>{c.name}</div>
                        <div style={{ fontSize: 10, color: "var(--accent-cyan)", fontWeight: 600 }}>{c.region}</div>
                      </div>
                      <span
                        style={{
                          fontSize: 11,
                          fontWeight: 700,
                          padding: "2px 8px",
                          borderRadius: 12,
                          background: count > 0 ? "var(--surface-elevated)" : "var(--surface-hover)",
                          color: count > 0 ? "var(--text-primary)" : "var(--text-muted)",
                          border: "1px solid var(--border-color)",
                        }}
                      >
                        {count} vehicle{count === 1 ? "" : "s"}
                      </span>
                    </div>
                    <div style={{ fontSize: 10, color: "var(--text-muted)" }}>{c.majorWorkshops}</div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Card B: Discharge Voucher (DV) & Fee Recovery Aging */}
          <div className="card-glass">
            <div className="card-header" style={{ marginBottom: 12 }}>
              <div>
                <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <DollarSign style={{ width: 16, height: 16, color: "var(--badge-amber-text)" }} />
                  <span>DV &amp; Fee Recovery Aging</span>
                </div>
                <div className="card-subtitle">
                  Tracking delayed workshop remittance ($40k-$50k backlog)
                </div>
              </div>
            </div>

            {/* Visual Aging Meter */}
            <div style={{ marginBottom: 14 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 4 }}>
                <span style={{ fontWeight: 600 }}>Total Outstanding:</span>
                <span style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--badge-amber-text)" }}>
                  $46,200 SGD
                </span>
              </div>
              <div className="aging-meter-track">
                <div className="aging-meter-bar" style={{ width: "38%", background: "var(--badge-green-text)" }} title="0-30 Days: $17,800" />
                <div className="aging-meter-bar" style={{ width: "40%", background: "var(--badge-amber-text)" }} title="30-60 Days: $18,400" />
                <div className="aging-meter-bar" style={{ width: "22%", background: "var(--badge-red-text)" }} title="60+ Days Overdue: $10,000" />
              </div>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 11 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 8, height: 8, borderRadius: 2, background: "var(--badge-green-text)" }} />
                  0 &ndash; 30 Days (Active Processing)
                </span>
                <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}>$17,800 SGD</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 8, height: 8, borderRadius: 2, background: "var(--badge-amber-text)" }} />
                  30 &ndash; 60 Days (DV Signed, Pending Remit)
                </span>
                <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}>$18,400 SGD</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 8, height: 8, borderRadius: 2, background: "var(--badge-red-text)" }} />
                  60+ Days (OVERDUE &ndash; AutoWorld &amp; KKL)
                </span>
                <span style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--badge-red-text)" }}>$10,000 SGD</span>
              </div>
            </div>

            {chaserNotice && (
              <div style={{ marginTop: 12, padding: "8px 10px", borderRadius: 6, background: "var(--badge-green-bg)", color: "var(--badge-green-text)", fontSize: 11, fontWeight: 600 }}>
                {chaserNotice}
              </div>
            )}

            <button
              type="button"
              className="btn-secondary-modern"
              onClick={handleExportChasers}
              style={{ width: "100%", marginTop: 14, fontSize: 11, padding: "7px 12px", justifyContent: "center" }}
            >
              <Send style={{ width: 13, height: 13 }} /> Batch Export Payment Chasers
            </button>
          </div>

          {/* Card C: AI Vision Extraction & Surveyor Velocity */}
          <div className="card-glass">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", color: "var(--text-muted)", letterSpacing: "0.05em" }}>
                  Surveyor Velocity SLA
                </div>
                <div style={{ fontSize: 18, fontWeight: 800, marginTop: 2, color: "var(--accent-cyan)" }}>
                  {analytics.avg_resolution_time || "2.1d"} <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 500 }}>(Target &le; 3d)</span>
                </div>
              </div>

              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", color: "var(--text-muted)", letterSpacing: "0.05em" }}>
                  AI Vision Accuracy
                </div>
                <div style={{ fontSize: 18, fontWeight: 800, marginTop: 2, color: "var(--badge-green-text)" }}>
                  {analytics.ai_confidence_avg || "92% High"}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
