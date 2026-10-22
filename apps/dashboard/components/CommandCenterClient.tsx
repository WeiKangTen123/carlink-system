"use client";

import { useState, useMemo, useEffect } from "react";
import Link from "next/link";
import {
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
  FileText,
  Layers,
  CarFront,
  AlertOctagon,
  Download,
  ClipboardCheck,
  Navigation,
  X,
  Filter,
} from "lucide-react";
import type { ReportSummary, AnalyticsSummary } from "@/lib/api";
import { useCurrentUser } from "@/components/CurrentUser";
import { fileUrl } from "@/lib/api";
import {
  severityClass,
  caseTitle,
  daysOpen,
  isAwaitingSignOff,
  getClaimArchetype,
  getEstimatedCostSGD,
  getGiaSla,
  inferVehicleName,
  inferInsurerName,
  inferWorkshopName,
  formatCarlinkRef,
} from "@/lib/caseFields";
import { PdfPreviewModal } from "@/components/PdfPreviewModal";

type Swimlane = "all" | "sje" | "tma" | "tp" | "dv";
type QuickFilter = "all" | "urgent_sla" | "strip_down" | "sje_court" | "severe" | "high_quantum";

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

// Claim archetype and the indicative cost come from the shared caseFields
// helpers. This file used to carry its own copies that defaulted every case
// to "TP Conventional" and priced it from keywords (TMA = $85,000).

/** The real GIA 48h window, computed from intake time. */
function slaOf(r: ReportSummary) {
  return getGiaSla(r.created_at, r.status === "Signed Off");
}

/** 6h or less left in the GIA 48h window, or already past it. */
function isSlaUrgent(r: ReportSummary): boolean {
  const s = slaOf(r).status;
  return s === "critical" || s === "expired";
}

export function CommandCenterClient({ initialReports, analytics }: Props) {
  const user = useCurrentUser();
  const [selectedSwimlane, setSelectedSwimlane] = useState<Swimlane>("all");
  const [quickFilter, setQuickFilter] = useState<QuickFilter>("all");
  const [selectedCluster, setSelectedCluster] = useState<string | null>(null);
  const [selectedInsurer, setSelectedInsurer] = useState<string>("all");
  const [searchTerm, setSearchTerm] = useState<string>("");
  const [previewReport, setPreviewReport] = useState<ReportSummary | null>(null);
  const [dispatchNotice, setDispatchNotice] = useState<string | null>(null);
  const [sgtTime, setSgtTime] = useState<string>("");

  // Live SGT (Asia/Singapore, UTC+8) Real-Time Clock Hook
  useEffect(() => {
    const updateTime = () => {
      try {
        const now = new Date();
        const formatted = new Intl.DateTimeFormat("en-SG", {
          timeZone: "Asia/Singapore",
          day: "2-digit",
          month: "short",
          year: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hourCycle: "h23",
        }).format(now);
        setSgtTime(formatted);
      } catch {
        setSgtTime(new Date().toLocaleTimeString());
      }
    };
    updateTime();
    const interval = setInterval(updateTime, 1000);
    return () => clearInterval(interval);
  }, []);

  const reports = initialReports;
  const openCases = useMemo(() => reports.filter((r) => isAwaitingSignOff(r.status)), [reports]);

  // Total indicative (estimated, not assessed) repair reserves
  const totalEstReservesSGD = useMemo(() => {
    return openCases.reduce((sum, r) => sum + getEstimatedCostSGD(r), 0);
  }, [openCases]);

  // Alerts come only from real data: open cases at <=6h or past their GIA
  // 48h window, most overdue first. (This banner used to show three
  // hard-coded alerts naming a made-up court suit, plate and company.)
  const slaAlerts = useMemo(
    () =>
      openCases
        .filter(isSlaUrgent)
        .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()),
    [openCases]
  );

  // High-stakes cases (SJE or TMA)
  const highStakesCases = useMemo(() => {
    return openCases.filter((r) => {
      const arch = getClaimArchetype(r);
      return arch.key === "sje" || arch.key === "tma";
    });
  }, [openCases]);

  // Dynamic swimlane counts
  const sjeCount = useMemo(() => openCases.filter((r) => getClaimArchetype(r).key === "sje").length, [openCases]);
  const tmaCount = useMemo(() => openCases.filter((r) => getClaimArchetype(r).key === "tma").length, [openCases]);
  const tpCount = useMemo(() => openCases.filter((r) => {
    const k = getClaimArchetype(r).key;
    return k === "tp-conv" || k === "tp-direct";
  }).length, [openCases]);
  const dvAgingCount = useMemo(() => openCases.filter((r) => daysOpen(r.created_at) >= 2).length, [openCases]);

  // Quick filter counts
  const quickFilterCounts = useMemo(() => {
    return {
      urgent_sla: slaAlerts.length,
      strip_down: openCases.filter((r) => !!r.disassembly_required).length,
      sje_court: sjeCount,
      severe: openCases.filter((r) => (r.severity_level || "").toLowerCase().includes("severe")).length,
      high_quantum: openCases.filter((r) => getEstimatedCostSGD(r) >= 15000).length,
    };
  }, [openCases, sjeCount, slaAlerts]);

  // Insurer Exposure distribution and visual share bar
  const insurerExposure = useMemo(() => {
    const map = new Map<string, { count: number; totalCost: number }>();
    openCases.forEach((r) => {
      const ins = inferInsurerName(r);
      const cost = getEstimatedCostSGD(r);
      const curr = map.get(ins) || { count: 0, totalCost: 0 };
      map.set(ins, { count: curr.count + 1, totalCost: curr.totalCost + cost });
    });

    const colors = [
      "#38bdf8", // Cyan / Sky
      "#f59e0b", // Amber
      "#10b981", // Emerald
      "#a855f7", // Purple
      "#ec4899", // Pink
      "#64748b", // Slate
    ];

    const totalCount = openCases.length || 1;
    const list: Array<{
      name: string;
      count: number;
      totalCost: number;
      pct: number;
      color: string;
    }> = [];

    let colorIdx = 0;
    map.forEach((val, key) => {
      list.push({
        name: key,
        count: val.count,
        totalCost: val.totalCost,
        pct: Math.round((val.count / totalCount) * 100),
        color: colors[colorIdx % colors.length],
      });
      colorIdx++;
    });

    return list.sort((a, b) => b.count - a.count);
  }, [openCases]);

  // Filtered Caseload Matrix
  const filteredCases = useMemo(() => {
    return openCases.filter((r) => {
      const arch = getClaimArchetype(r);
      const cost = getEstimatedCostSGD(r);
      const age = daysOpen(r.created_at);

      // 1. Swimlane
      if (selectedSwimlane === "sje" && arch.key !== "sje") return false;
      if (selectedSwimlane === "tma" && arch.key !== "tma") return false;
      if (selectedSwimlane === "tp" && arch.key !== "tp-conv" && arch.key !== "tp-direct") return false;
      if (selectedSwimlane === "dv" && age < 2) return false;

      // 2. Quick filter chip
      if (quickFilter === "urgent_sla" && !isSlaUrgent(r)) return false;
      if (quickFilter === "strip_down" && !r.disassembly_required) return false;
      if (quickFilter === "sje_court" && arch.key !== "sje") return false;
      if (quickFilter === "severe" && !(r.severity_level || "").toLowerCase().includes("severe")) return false;
      if (quickFilter === "high_quantum" && cost < 15000) return false;

      // 3. Cluster
      if (selectedCluster) {
        const clusterDef = SG_CLUSTERS.find((c) => c.id === selectedCluster);
        if (clusterDef) {
          const loc = `${r.location || ""} ${r.workshop_assigned || ""}`.toLowerCase();
          const matches = clusterDef.keywords.some((kw) => loc.includes(kw));
          if (!matches) return false;
        }
      }

      // 4. Insurer ("Insurer not recorded" is selectable from the exposure bar)
      if (selectedInsurer !== "all") {
        const ins = inferInsurerName(r).toLowerCase();
        if (!ins.includes(selectedInsurer.toLowerCase())) return false;
      }

      // 5. Search
      if (searchTerm.trim() !== "") {
        const q = searchTerm.toLowerCase();
        const haystack = `${r.id} ${r.plate_number || ""} ${r.vehicle_name || ""} ${r.location || ""} ${r.insurer_name || ""} ${r.workshop_assigned || ""}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }

      return true;
    });
  }, [openCases, selectedSwimlane, quickFilter, selectedCluster, selectedInsurer, searchTerm]);

  // Unique insurers list for filter dropdown
  const insurerOptions = useMemo(() => {
    const set = new Set<string>();
    reports.forEach((r) => set.add(inferInsurerName(r)));
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

  const handleExportDispatchSheet = () => {
    // Recorded values only: a blank cell means "not recorded" (this file used
    // to fill gaps with "Motor Vehicle", Tokio Marine and ComfortDelGro).
    const csvText = (v?: string | null) => `"${(v || "").replace(/"/g, '""')}"`;
    const header = ["Case ID", "Plate Number", "Vehicle Model", "Archetype", "Insurer", "Incident Location", "Workshop", "Est Reserve (SGD, indicative)", "Days Open"];
    const rows = filteredCases.map((r) => {
      const est = getEstimatedCostSGD(r);
      return [
        csvText(r.id),
        csvText(r.plate_number),
        csvText(r.vehicle_name),
        csvText(getClaimArchetype(r).label),
        csvText(r.insurer_name),
        csvText(r.location),
        csvText(r.workshop_assigned),
        est > 0 ? est : "",
        daysOpen(r.created_at),
      ];
    });

    const csvContent = "data:text/csv;charset=utf-8," + [header.join(","), ...rows.map((e) => e.join(","))].join("\n");
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    const dateStr = new Date().toISOString().slice(0, 10);
    link.setAttribute("download", `carlink_surveyor_dispatch_${dateStr}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    setDispatchNotice(`Exported ${filteredCases.length} vehicle dispatch manifest to CSV.`);
    setTimeout(() => setDispatchNotice(null), 5000);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* 1. Header & Operational Status Strip with Live SGT Clock */}
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 6 }}>
            <span className="command-header-badge">
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--badge-green-text)", boxShadow: "0 0 6px var(--badge-green-text)" }} />
              COMMAND CENTER LIVE
            </span>

            {/* Live Singapore Standard Time (SGT) Clock */}
            <span className="live-clock-badge">
              <Clock style={{ width: 12, height: 12, color: "var(--accent-cyan)" }} />
              <span>{sgtTime ? `${sgtTime} SGT` : "Singapore (UTC+8)"}</span>
              <span className="live-clock-pulse" />
            </span>

            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              Signed in: <strong style={{ color: "var(--text-primary)" }}>{user?.name ?? "—"}</strong>
            </span>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              • Framework: <strong style={{ color: "var(--accent-cyan)" }}>GIA / IDAC 48h Inspection SLA</strong>
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
          <button
            type="button"
            onClick={handleExportDispatchSheet}
            className="btn-secondary-modern"
            style={{ fontSize: 12, padding: "8px 14px" }}
            title="Download CSV manifest for today's workshop inspection route"
          >
            <Download style={{ width: 14, height: 14 }} /> Export Dispatch Manifest
          </button>
          <Link href="/analytics" className="btn-secondary-modern" style={{ fontSize: 12, padding: "8px 14px" }}>
            <SlidersHorizontal style={{ width: 14, height: 14 }} /> Analytics Hub
          </Link>
          <Link href="/reports/new" className="btn-primary-modern" style={{ fontSize: 12, padding: "8px 16px" }}>
            <FilePlus2 style={{ width: 14, height: 14 }} /> + New Case Intake
          </Link>
        </div>
      </div>

      {dispatchNotice && (
        <div style={{ padding: "8px 14px", borderRadius: 8, background: "var(--badge-green-bg)", border: "1px solid var(--badge-green-border)", color: "var(--badge-green-text)", fontSize: 12, fontWeight: 600, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span>{dispatchNotice}</span>
          <button type="button" onClick={() => setDispatchNotice(null)} style={{ background: "none", border: "none", color: "inherit", cursor: "pointer" }}>
            <X style={{ width: 14, height: 14 }} />
          </button>
        </div>
      )}

      {/* 2. GIA 48h SLA Alerts Banner -- real cases only, hidden when none */}
      {slaAlerts.length > 0 && (
        <div className="command-alert-box">
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 700, fontSize: 13, color: "var(--badge-amber-text)" }}>
            <AlertTriangle style={{ width: 16, height: 16 }} />
            <span>GIA 48H INSPECTION SLA ALERTS</span>
            <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-muted)", fontWeight: 500 }}>
              {slaAlerts.length} open case{slaAlerts.length === 1 ? "" : "s"} at &le;6h or overdue • from intake time
            </span>
          </div>

          {slaAlerts.slice(0, 3).map((r) => {
            const sla = slaOf(r);
            const overdue = sla.status === "expired";
            return (
              <div key={r.id} className="command-alert-item">
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: sla.pillColor, flexShrink: 0 }} />
                  <span>
                    <strong style={{ color: sla.pillColor }}>{overdue ? "GIA 48H SLA OVERDUE:" : "GIA 48H SLA CRITICAL:"}</strong>{" "}
                    {r.plate_number || formatCarlinkRef(r.id, null, r.created_at)}
                    {r.insurer_name ? ` (${r.insurer_name})` : ""} —{" "}
                    <strong style={{ color: "var(--text-primary)" }}>{overdue ? sla.remainingText : sla.badgeText}</strong>
                  </span>
                </div>
                <Link href={`/reports/${r.id}`} style={{ fontSize: 11, fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 4 }}>
                  Review Case <ArrowRight style={{ width: 12, height: 12 }} />
                </Link>
              </div>
            );
          })}

          {slaAlerts.length > 3 && (
            <div className="command-alert-item" style={{ color: "var(--text-muted)", fontSize: 11 }}>
              +{slaAlerts.length - 3} more — use the &quot;Urgent SLA&quot; quick filter below.
            </div>
          )}
        </div>
      )}

      {/* 3. KPI Cards. Auto-fit columns: "Quantum Savings" (a flat 21% of
          reserves) and "Fee Aging Backlog" (a hard-coded $46,200) were
          removed, so the 5-column grid would leave empty slots. */}
      <div className="kpi-grid-command" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
        {/* KPI 1 */}
        <div className="kpi-card-command" style={{ "--kpi-border": "var(--accent-cyan)" } as React.CSSProperties}>
          <div>
            <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <ClipboardCheck style={{ width: 13, height: 13, color: "var(--accent-cyan)" }} /> Active Triage
            </div>
            <div className="kpi-val" style={{ color: "var(--text-primary)", marginTop: 4 }}>{openCases.length} Cases</div>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>
            {analytics.pending_review} awaiting sign-off • {analytics.signed_off} archived
          </div>
        </div>

        {/* KPI 2 */}
        <div className="kpi-card-command" style={{ "--kpi-border": "var(--badge-green-text)" } as React.CSSProperties}>
          <div>
            <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <DollarSign style={{ width: 13, height: 13, color: "var(--badge-green-text)" }} /> Est. Reserves
            </div>
            <div className="kpi-val" style={{ color: "var(--badge-green-text)", marginTop: 4 }}>
              ${(totalEstReservesSGD).toLocaleString()} <span style={{ fontSize: 13, fontWeight: 600 }}>SGD</span>
            </div>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>
            Indicative, open cases — not an assessed quantum
          </div>
        </div>

        {/* KPI 3 */}
        <div className="kpi-card-command" style={{ "--kpi-border": "var(--badge-red-text)" } as React.CSSProperties}>
          <div>
            <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Flame style={{ width: 13, height: 13, color: "var(--badge-red-text)" }} /> SJE / TMA Active
            </div>
            <div className="kpi-val" style={{ color: "var(--badge-red-text)", marginTop: 4 }}>
              {highStakesCases.length} <span style={{ fontSize: 13, fontWeight: 600 }}>High-Stakes</span>
            </div>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>
            Court evidence & crash cushions
          </div>
        </div>
      </div>

      {/* 4. Triage Swimlanes & Quick Triage Filter Chips Bar */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
          {/* Main Swimlane Tabs with dynamic database counters */}
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
              SJE Court ({sjeCount})
            </button>
            <button
              type="button"
              className={`swimlane-tab ${selectedSwimlane === "tma" ? "active" : ""}`}
              onClick={() => setSelectedSwimlane("tma")}
            >
              TMA Expressway ({tmaCount})
            </button>
            <button
              type="button"
              className={`swimlane-tab ${selectedSwimlane === "tp" ? "active" : ""}`}
              onClick={() => setSelectedSwimlane("tp")}
            >
              TP Conventional ({tpCount})
            </button>
            <button
              type="button"
              className={`swimlane-tab ${selectedSwimlane === "dv" ? "active" : ""}`}
              onClick={() => setSelectedSwimlane("dv")}
            >
              Aging 2d+ ({dvAgingCount})
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

        {/* Quick Triage Filter Chips */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", display: "flex", alignItems: "center", gap: 4 }}>
            <Filter style={{ width: 12, height: 12 }} /> Quick Filter:
          </span>

          <button
            type="button"
            className={`quick-filter-chip ${quickFilter === "all" ? "active" : ""}`}
            onClick={() => setQuickFilter("all")}
          >
            All Active
          </button>

          <button
            type="button"
            className={`quick-filter-chip ${quickFilter === "urgent_sla" ? "active" : ""}`}
            onClick={() => setQuickFilter(quickFilter === "urgent_sla" ? "all" : "urgent_sla")}
          >
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--badge-red-text)" }} />
            Urgent SLA &le;6h / Overdue ({quickFilterCounts.urgent_sla})
          </button>

          <button
            type="button"
            className={`quick-filter-chip ${quickFilter === "strip_down" ? "active" : ""}`}
            onClick={() => setQuickFilter(quickFilter === "strip_down" ? "all" : "strip_down")}
          >
            <Wrench style={{ width: 11, height: 11, color: "var(--badge-amber-text)" }} />
            Strip-Down Req ({quickFilterCounts.strip_down})
          </button>

          <button
            type="button"
            className={`quick-filter-chip ${quickFilter === "sje_court" ? "active" : ""}`}
            onClick={() => setQuickFilter(quickFilter === "sje_court" ? "all" : "sje_court")}
          >
            SJE Court Docket ({quickFilterCounts.sje_court})
          </button>

          <button
            type="button"
            className={`quick-filter-chip ${quickFilter === "severe" ? "active" : ""}`}
            onClick={() => setQuickFilter(quickFilter === "severe" ? "all" : "severe")}
          >
            Severe Structural ({quickFilterCounts.severe})
          </button>

          <button
            type="button"
            className={`quick-filter-chip ${quickFilter === "high_quantum" ? "active" : ""}`}
            onClick={() => setQuickFilter(quickFilter === "high_quantum" ? "all" : "high_quantum")}
          >
            High Est. &gt;$15k ({quickFilterCounts.high_quantum})
          </button>

          {(quickFilter !== "all" || selectedCluster || selectedInsurer !== "all" || searchTerm) && (
            <button
              type="button"
              onClick={() => {
                setQuickFilter("all");
                setSelectedCluster(null);
                setSelectedInsurer("all");
                setSearchTerm("");
              }}
              style={{
                fontSize: 11,
                background: "none",
                border: "none",
                color: "var(--accent-cyan)",
                cursor: "pointer",
                textDecoration: "underline",
                marginLeft: 4,
              }}
            >
              Reset Filters
            </button>
          )}
        </div>
      </div>

      {/* 5. Main Split Grid: Caseload Triage Matrix / Route Logistics & DV Aging */}
      <div className="command-grid" style={{ marginTop: 0 }}>
        {/* Left Column: Caseload Triage Matrix */}
        <div className="card-glass triage-table-card">
          <div className="card-header" style={{ marginBottom: 12 }}>
            <div>
              <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <CarFront style={{ width: 18, height: 18, color: "var(--accent-primary)" }} />
                <span>Caseload Triage Matrix</span>
                <span style={{ fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 10, background: "var(--surface-hover)", color: "var(--text-secondary)" }}>
                  {filteredCases.length} {filteredCases.length === 1 ? "case" : "cases"}
                </span>
              </div>
              <div className="card-subtitle">
                Open cases awaiting sign-off • SLA is the GIA 48h window from intake time
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

          {/* Interactive Insurer Exposure Share Mini-Bar */}
          <div style={{ marginBottom: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 11, marginBottom: 5 }}>
              <span style={{ color: "var(--text-muted)", fontWeight: 600 }}>Insurer Portfolio Exposure:</span>
              <span style={{ fontSize: 10, color: "var(--text-muted)" }}>Click segment to isolate caseload</span>
            </div>
            <div className="insurer-exposure-track">
              {insurerExposure.map((item) => (
                <div
                  key={item.name}
                  className="insurer-exposure-segment"
                  style={{
                    width: `${Math.max(item.pct, 4)}%`,
                    background: item.color,
                    opacity: selectedInsurer === "all" || selectedInsurer.toLowerCase().includes(item.name.toLowerCase()) ? 1 : 0.35,
                  }}
                  title={`${item.name}: ${item.count} cases (${item.pct}%) - Est. $${item.totalCost.toLocaleString()} SGD (indicative)`}
                  onClick={() => setSelectedInsurer(selectedInsurer.toLowerCase().includes(item.name.toLowerCase()) ? "all" : item.name)}
                />
              ))}
            </div>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 6, fontSize: 10 }}>
              {insurerExposure.slice(0, 4).map((item) => (
                <span
                  key={item.name}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    color: selectedInsurer.toLowerCase().includes(item.name.toLowerCase()) ? "var(--accent-cyan)" : "var(--text-muted)",
                    cursor: "pointer",
                    fontWeight: selectedInsurer.toLowerCase().includes(item.name.toLowerCase()) ? 700 : 500,
                  }}
                  onClick={() => setSelectedInsurer(selectedInsurer.toLowerCase().includes(item.name.toLowerCase()) ? "all" : item.name)}
                >
                  <span style={{ width: 7, height: 7, borderRadius: "50%", background: item.color }} />
                  {item.name} ({item.count})
                </span>
              ))}
            </div>
          </div>

          {/* Table Container with Sticky Headers & No Overflow Issues */}
          <div className="triage-table-wrapper">
            <table className="triage-table">
              <thead>
                <tr>
                  <th>Vehicle Registration</th>
                  <th>Claim Archetype</th>
                  <th>Insurer // Workshop</th>
                  <th className="col-reserve">Est. Reserve ($ SGD)</th>
                  <th>SLA Window</th>
                  <th className="col-actions">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredCases.map((r) => {
                  const arch = getClaimArchetype(r);
                  const cost = getEstimatedCostSGD(r);
                  const age = daysOpen(r.created_at);
                  const sla = slaOf(r);

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
                              <span className="badge-plate-command">
                                {r.plate_number || r.id.slice(0, 8).toUpperCase()}
                              </span>
                            </div>
                            <div className="triage-cell-truncate" style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                              {inferVehicleName(r)} • {r.damage_count} part{r.damage_count === 1 ? "" : "s"}
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
                                arch.key === "pending" ? "var(--surface-hover)" :
                                "var(--badge-amber-bg)",
                              color:
                                arch.key === "sje" ? "#c084fc" :
                                arch.key === "tma" ? "var(--badge-red-text)" :
                                arch.key === "tp-direct" ? "var(--accent-cyan)" :
                                arch.key === "pending" ? "var(--text-muted)" :
                                "var(--badge-amber-text)",
                              border: `1px solid ${
                                arch.key === "sje" ? "rgba(168, 85, 247, 0.3)" :
                                arch.key === "tma" ? "var(--badge-red-border)" :
                                arch.key === "tp-direct" ? "rgba(56, 189, 248, 0.3)" :
                                arch.key === "pending" ? "var(--border-color)" :
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
                          <div className="triage-cell-truncate" style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                            {inferInsurerName(r)}
                          </div>
                          <div className="triage-cell-truncate" style={{ color: "var(--text-muted)", fontSize: 10, marginTop: 1 }}>
                            {inferWorkshopName(r.workshop_assigned)}
                          </div>
                        </div>
                      </td>

                      <td className="col-reserve" title="Indicative estimate, not an assessment">
                        <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, fontSize: 12, color: cost > 0 ? "var(--accent-cyan)" : "var(--text-muted)" }}>
                          {cost > 0 ? `$${cost.toLocaleString()}` : "—"}
                        </div>
                        <div style={{ fontSize: 10, color: "var(--text-muted)" }}>{cost > 0 ? "SGD Est." : "No damage recorded"}</div>
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
                          <span style={{ fontSize: 11, fontWeight: 600, color: sla.pillColor }}>
                            {sla.badgeText}
                          </span>
                        </div>
                        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 1 }}>
                          Age: {age === 0 ? "Today" : `${age}d open`}
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
                            title="Open Full Loss Adjuster Studio"
                          >
                            <span>Studio</span>
                            <ArrowRight style={{ width: 12, height: 12 }} />
                          </Link>
                        </div>
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

        {/* Right Column: Singapore Workshop Cluster Route Logistics & Velocity */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {/* Card A: Singapore Workshop Cluster Logistics & Route Circuit */}
          <div className="card-glass">
            <div className="card-header" style={{ marginBottom: 12 }}>
              <div>
                <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <MapPin style={{ width: 16, height: 16, color: "var(--accent-primary)" }} />
                  <span>Singapore Workshop Clusters</span>
                </div>
                <div className="card-subtitle">
                  Physical surveyor inspection route order
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

            {/* Surveyor Route Order Circuit */}
            <div style={{ marginBottom: 12, padding: "10px 12px", borderRadius: 8, background: "var(--surface-hover)", border: "1px solid var(--border-color)" }}>
              <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "var(--text-muted)", letterSpacing: "0.05em", marginBottom: 6, display: "flex", alignItems: "center", gap: 4 }}>
                <Navigation style={{ width: 12, height: 12, color: "var(--accent-cyan)" }} /> Recommended Route Order
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", fontSize: 11 }}>
                <span className="route-circuit-step">1. West (Toh Guan)</span>
                <span className="route-circuit-arrow">&rarr;</span>
                <span className="route-circuit-step">2. North (Sin Ming)</span>
                <span className="route-circuit-arrow">&rarr;</span>
                <span className="route-circuit-step">3. East (Kaki Bukit)</span>
              </div>
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

          {/* The "DV & Fee Recovery Aging" card was removed: every figure in
              it ($46,200 outstanding, named overdue workshops, the payment
              chaser toast) was hard-coded -- no fee or DV data is recorded. */}

          {/* Card C: AI Confidence & Surveyor Velocity */}
          <div className="card-glass">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", color: "var(--text-muted)", letterSpacing: "0.05em" }}>
                  Surveyor Velocity SLA
                </div>
                <div style={{ fontSize: 18, fontWeight: 800, marginTop: 2, color: "var(--accent-cyan)" }}>
                  {analytics.avg_resolution_time || "—"} <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 500 }}>(Target &le; 3d)</span>
                </div>
              </div>

              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", color: "var(--text-muted)", letterSpacing: "0.05em" }}>
                  AI Confidence (self-reported)
                </div>
                <div
                  style={{ fontSize: 18, fontWeight: 800, marginTop: 2, color: analytics.ai_confidence_avg ? "var(--badge-green-text)" : "var(--text-muted)" }}
                  title="Share of AI damage findings the model itself rated High confidence -- not a measured accuracy"
                >
                  {analytics.ai_confidence_avg || "—"}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

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
