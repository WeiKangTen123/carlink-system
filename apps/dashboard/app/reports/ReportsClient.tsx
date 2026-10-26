"use client";

import { useState, useMemo, useEffect, useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
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
  RefreshCw,
  Send,
  SlidersHorizontal,
  ExternalLink,
  UserX,
} from "lucide-react";
import {
  type ReportSummary,
  type ReportDetail,
  type AnalyticsSummary,
  type CaseScope,
  type DamageSummaryItem,
  fileUrl,
  ownerLabel,
} from "@/lib/api";
import { DeleteButton } from "@/components/DeleteButton";
import { canViewAllCases, isAdmin, useCurrentUser } from "@/components/CurrentUser";
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
  inferBodyType,
  inferVehicleMake,
  getOperationalStage,
  matchesDateFilter,
  formatSgtDateTime,
  formatIncidentTime,
  getGiaSla,
  NOT_RECORDED,
} from "@/lib/caseFields";
import {
  getReportDetailAction,
  aiEnrichReportAction,
  aiEnrichAllReportsAction,
  countUnassignedAction,
} from "./actions";

type ViewMode = "split" | "matrix";
type StageTab = "all" | "intake" | "in_survey" | "signed";
type DateFilter = "all_time" | "today" | "yesterday" | "this_week" | "this_month";
type BodyFilter = "all" | "SUV" | "Sedan" | "Van / Commercial" | "MPV" | "Hatchback";
type MakeFilter = "all" | "Honda" | "Toyota" | "Mercedes-Benz" | "BMW" | "Volkswagen" | "Hyundai" | "Commercial Fleet";
type ArchetypeFilter = "all" | "pending" | "tp-conv" | "od" | "sje" | "tma" | "tp-direct";
type QuickFilter = "all" | "urgent_sla" | "strip_down" | "severe" | "high_quantum";

interface Props {
  initialReports: ReportSummary[];
  initialAnalytics?: AnalyticsSummary | null;
  /** The ?scope= the list was loaded with; null for the API's default. */
  scope?: CaseScope | null;
  /** Why the list isn't the one asked for (e.g. scope_not_allowed). */
  notice?: string | null;
}

/** The My cases / All / Unassigned switch, for admins and staff who see all
 * cases. Each is its own address so the server lists just those. */
const SCOPE_TABS: { scope: CaseScope; label: string; title: string }[] = [
  { scope: "mine", label: "My cases", title: "Cases you own" },
  { scope: "all", label: "All", title: "Every case" },
  { scope: "unassigned", label: "Unassigned", title: "Cases nobody owns yet" },
];

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

// Inline chip colours per severity. A part nobody rated stays neutral grey
// and reads "Unrated" -- it used to fall back to a green "Minor" chip.
const SEVERITY_CHIP: Record<ReturnType<typeof severityClass>, { bg: string; fg: string }> = {
  severe: { bg: "var(--badge-red-bg)", fg: "var(--badge-red-text)" },
  moderate: { bg: "var(--badge-amber-bg)", fg: "var(--badge-amber-text)" },
  minor: { bg: "var(--badge-green-bg)", fg: "var(--badge-green-text)" },
  unrated: { bg: "var(--surface-hover)", fg: "var(--text-muted)" },
};

/** Where the case came in from (ReportSummary.channel). */
function channelLabel(channel?: string | null): string {
  if (channel === "telegram") return "Telegram Bot (@carlink_bot)";
  if (channel === "whatsapp") return "WhatsApp";
  if (channel === "manual") return "Surveyor Console";
  return channel || NOT_RECORDED;
}

/** A recorded SGD amount for display, or null when none is recorded. */
function formatRecordedSGD(value?: string | null): string | null {
  if (!value || !value.trim()) return null;
  const n = parseFloat(value.replace(/[^0-9.]/g, ""));
  return Number.isNaN(n) ? value : `$${n.toLocaleString()}`;
}

/** Damage items that came from the AI's photo analysis (they carry its
 * confidence / photo link / box); hand-typed parts carry none of these. */
function isAiDetected(d: DamageSummaryItem): boolean {
  return !!(d.ai_confidence || d.photo_reference || (d.bbox_2d && d.bbox_2d.length));
}

/** Photo i as a smaller copy (400px thumbnail or 1600px preview) when the
 * API sent one, else the original. Both lists follow photo_urls' order.
 * The originals are camera files of a few MB each. */
function photoCopyUrl(detail: ReportDetail, copy: "photo_thumb_urls" | "photo_preview_urls", i: number): string {
  return detail[copy]?.[i] || detail.photo_urls[i];
}

/** What the queue and matrix show for a case. Pure functions of the case,
 * so worked out once per list; the SLA moves with the clock, so it isn't
 * here. */
type RowInfo = {
  stage: ReturnType<typeof getOperationalStage>;
  arch: ReturnType<typeof getClaimArchetype>;
  body: ReturnType<typeof inferBodyType>;
  make: string;
  cost: number;
  ref: string;
  vehicle: string;
  insurer: string;
  workshop: string;
  filedAt: string;
  /** "Tan Ah Huat (@ahhuat)", or "Unassigned". */
  owner: string;
  searchText: string;
};

export function ReportsClient({ initialReports, scope = null, notice = null }: Props) {
  const user = useCurrentUser();
  const admin = isAdmin(user);
  // Owners only matter to someone who sees other people's cases.
  const showOwner = canViewAllCases(user);
  // The API's default when no scope was asked for.
  const activeScope: CaseScope = scope ?? (admin ? "all" : "mine");
  const [reportsList, setReportsList] = useState<ReportSummary[]>(initialReports || []);
  const [viewMode, setViewMode] = useState<ViewMode>("split");
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedInsurer, setSelectedInsurer] = useState("all");
  const [selectedStage, setSelectedStage] = useState<StageTab>("all");
  const [dateFilter, setDateFilter] = useState<DateFilter>("all_time");
  const [bodyFilter, setBodyFilter] = useState<BodyFilter>("all");
  const [makeFilter, setMakeFilter] = useState<MakeFilter>("all");
  const [archetypeFilter, setArchetypeFilter] = useState<ArchetypeFilter>("all");
  const [quickFilter, setQuickFilter] = useState<QuickFilter>("all");
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null);
  const [previewReport, setPreviewReport] = useState<ReportSummary | null>(null);

  const router = useRouter();

  // Detail cache & state for active selected report
  const detailCache = useRef<Map<string, ReportDetail>>(new Map());
  const [activeDetail, setActiveDetail] = useState<ReportDetail | null>(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [activePhotoIdx, setActivePhotoIdx] = useState(0);

  // "Fill derived fields" state (the /ai-enrich endpoints; not an AI call)
  const [isEnrichingCurrent, setIsEnrichingCurrent] = useState(false);
  const [isEnrichingAll, setIsEnrichingAll] = useState(false);
  const [enrichNotice, setEnrichNotice] = useState<string | null>(null);

  // Global Hotkey: Pressing 'N' navigates directly to New Intake if not focused on an input
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const activeEl = document.activeElement;
      const isInput =
        activeEl &&
        (activeEl.tagName === "INPUT" ||
          activeEl.tagName === "TEXTAREA" ||
          (activeEl as HTMLElement).isContentEditable);
      if (isInput) return;

      if ((e.key === "n" || e.key === "N") && !e.ctrlKey && !e.altKey && !e.metaKey) {
        e.preventDefault();
        router.push("/reports/new");
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [router]);

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

  // Executive Metric Counters & Operational Stage Counts
  const totalCount = reportsList.length;
  const intakeCount = useMemo(() => reportsList.filter((r) => getOperationalStage(r) === "intake").length, [reportsList]);
  const inSurveyCount = useMemo(() => reportsList.filter((r) => getOperationalStage(r) === "in_survey").length, [reportsList]);
  const signedCount = useMemo(() => reportsList.filter((r) => getOperationalStage(r) === "signed").length, [reportsList]);
  const pendingCount = useMemo(() => reportsList.filter((r) => isAwaitingSignOff(r.status)).length, [reportsList]);
  const draftCount = intakeCount;
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

  // Admins' Unassigned card. The All and Unassigned lists show it directly;
  // My cases doesn't, so it is counted separately then.
  const [unassignedElsewhere, setUnassignedElsewhere] = useState<number | null>(null);
  useEffect(() => {
    if (!admin || activeScope !== "mine") return;
    let live = true;
    countUnassignedAction().then((n) => {
      if (live) setUnassignedElsewhere(n);
    });
    return () => {
      live = false;
    };
  }, [admin, activeScope, initialReports]);
  const unassignedCount = useMemo(() => {
    if (activeScope === "unassigned") return reportsList.length;
    if (activeScope === "all") return reportsList.filter((r) => !r.owner).length;
    return unassignedElsewhere;
  }, [activeScope, reportsList, unassignedElsewhere]);

  // Per-case values for the filter and the rows, once per list instead of
  // on every keystroke and j/k press (formatSgtDateTime is the slow one).
  const rowInfo = useMemo(() => {
    const map = new Map<ReportSummary, RowInfo>();
    for (const r of reportsList) {
      const ref = formatCarlinkRef(r.id, r.plate_number, r.created_at);
      const vehicle = inferVehicleName(r);
      const insurer = inferInsurerName(r);
      const workshop = inferWorkshopName(r.workshop_assigned);
      const body = inferBodyType(r);
      const make = inferVehicleMake(r);
      // Owner name and username, so the search finds someone's cases.
      const ownerText = r.owner ? `${r.owner.name} ${r.owner.username}` : "unassigned";
      map.set(r, {
        stage: getOperationalStage(r),
        arch: getClaimArchetype(r),
        body,
        make,
        cost: getEstimatedCostSGD(r),
        ref,
        vehicle,
        insurer,
        workshop,
        filedAt: formatSgtDateTime(r.created_at),
        owner: ownerLabel(r.owner),
        searchText: `${r.id} ${ref} ${r.plate_number || ""} ${vehicle} ${r.location || ""} ${insurer} ${workshop} ${body} ${make} ${ownerText}`.toLowerCase(),
      });
    }
    return map;
  }, [reportsList]);

  // Filtered reports logic
  const filteredReports = useMemo(() => {
    return reportsList.filter((r) => {
      const { stage, arch, body, make, cost, insurer, searchText } = rowInfo.get(r)!;
      const isSigned = r.status === "Signed Off";
      const sla = getGiaSla(r.created_at, isSigned);

      // 1. Operational Stage Tab
      if (selectedStage === "intake" && stage !== "intake") return false;
      if (selectedStage === "in_survey" && stage !== "in_survey") return false;
      if (selectedStage === "signed" && stage !== "signed") return false;

      // 2. Date Filter
      if (!matchesDateFilter(r.created_at, dateFilter)) return false;

      // 3. Body Style Filter
      if (bodyFilter !== "all" && body !== bodyFilter) return false;

      // 4. Vehicle Make Filter
      if (makeFilter !== "all" && make !== makeFilter) return false;

      // 5. Claim Archetype Filter
      if (archetypeFilter !== "all") {
        if (archetypeFilter === "pending" && arch.key !== "pending") return false;
        if (archetypeFilter === "tp-conv" && arch.key !== "tp-conv") return false;
        if (archetypeFilter === "od" && arch.key !== "od") return false;
        if (archetypeFilter === "sje" && arch.key !== "sje") return false;
        if (archetypeFilter === "tma" && arch.key !== "tma") return false;
        if (archetypeFilter === "tp-direct" && arch.key !== "tp-direct") return false;
      }

      // 6. Quick filter
      if (quickFilter === "urgent_sla" && sla.status !== "critical" && sla.status !== "expired") return false;
      if (quickFilter === "strip_down" && !r.disassembly_required) return false;
      if (quickFilter === "severe" && !(r.severity_level || "").toLowerCase().includes("severe")) return false;
      if (quickFilter === "high_quantum" && cost < 15000) return false;

      // 7. Insurer
      if (selectedInsurer !== "all") {
        if (!insurer.toLowerCase().includes(selectedInsurer.toLowerCase())) return false;
      }

      // 8. Omni-Search
      if (searchTerm.trim() !== "") {
        const q = searchTerm.toLowerCase();
        if (!searchText.includes(q)) return false;
      }

      return true;
    });
  }, [reportsList, rowInfo, selectedStage, dateFilter, bodyFilter, makeFilter, archetypeFilter, quickFilter, selectedInsurer, searchTerm]);

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
    // Drop the previous case's detail first, so its photos and damage rows
    // are never shown under this case while loading (or if the fetch fails).
    setActiveDetail(null);
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

  // "Fill derived fields" (POST /reports/{id}/ai-enrich). Despite the
  // endpoint name it is not an AI call and invents nothing: it only derives
  // the vehicle summary from make/model, the body type from the vehicle
  // catalogue, and unpriced Annex A rows from the recorded damage list.
  const handleEnrichCurrent = async () => {
    if (!selectedSummary) return;
    setIsEnrichingCurrent(true);
    setEnrichNotice("Filling derived fields...");
    try {
      const res = await aiEnrichReportAction(selectedSummary.id);
      if ("error" in res) {
        setEnrichNotice(`Fill derived fields failed: ${res.error}`);
      } else {
        setEnrichNotice("Derived fields filled from this case's recorded data.");
        detailCache.current.delete(selectedSummary.id);
        // Refresh detail
        const updated = await getReportDetailAction(selectedSummary.id);
        if (updated) {
          detailCache.current.set(selectedSummary.id, updated);
          setActiveDetail(updated);
        }
      }
    } catch (e) {
      setEnrichNotice("Fill derived fields failed.");
    } finally {
      setIsEnrichingCurrent(false);
      setTimeout(() => setEnrichNotice(null), 4000);
    }
  };

  // Same derivation for every case (POST /reports/ai-enrich-all)
  const handleEnrichAll = async () => {
    setIsEnrichingAll(true);
    setEnrichNotice("Filling derived fields on all cases...");
    try {
      const res = await aiEnrichAllReportsAction();
      if ("error" in res) {
        setEnrichNotice(`Fill derived fields failed: ${res.error}`);
      } else {
        setEnrichNotice(`Derived fields filled on ${res.count ?? 0} case${res.count === 1 ? "" : "s"}.`);
        detailCache.current.clear();
        if (selectedSummary) {
          const updated = await getReportDetailAction(selectedSummary.id);
          if (updated) setActiveDetail(updated);
        }
      }
    } catch {
      setEnrichNotice("Fill derived fields failed.");
    } finally {
      setIsEnrichingAll(false);
      setTimeout(() => setEnrichNotice(null), 4000);
    }
  };

  // Selected case's recorded damage, and how much of it came from the AI
  const damageItems = activeDetail?.data?.damage_summary ?? [];
  const aiDetectedCount = damageItems.filter(isAiDetected).length;
  const recordedParts = activeDetail?.data?.damaged_parts ?? [];

  // The photo on show; the first if the index is left from another case.
  const heroIdx = activeDetail?.photo_urls?.[activePhotoIdx] ? activePhotoIdx : 0;

  // An empty list says whether it is empty or just filtered.
  const emptyListText =
    reportsList.length > 0
      ? "No cases match current filter criteria."
      : activeScope === "unassigned"
        ? "No unassigned cases. Every case has an owner."
        : activeScope === "mine"
          ? "You have no cases yet. Cases you start, or an admin gives you, appear here."
          : "No cases yet.";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {/* 1. Header with Title, Inline Metrics Strip & Action Suite */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap", minHeight: 36 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <h1 style={{ margin: 0, fontSize: 17, fontWeight: 800, letterSpacing: "-0.02em", whiteSpace: "nowrap" }}>
            Claims Dossier & Workstation
          </h1>
          <span className="command-header-badge" style={{ fontSize: 10, padding: "2px 8px" }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--badge-green-text)", boxShadow: "0 0 5px var(--badge-green-text)" }} />
            {user?.name ?? "Surveyor"} • GIA SLA 48h
          </span>

          {showOwner && (
            <div className="view-mode-toggle case-scope-switch" style={{ padding: 2 }} role="tablist" aria-label="Which cases">
              {SCOPE_TABS.map((t) => (
                <Link
                  key={t.scope}
                  href={`/reports?scope=${t.scope}`}
                  prefetch={false}
                  role="tab"
                  aria-selected={activeScope === t.scope}
                  className={`view-mode-btn ${activeScope === t.scope ? "active" : ""}`}
                  style={{ padding: "4px 8px", fontSize: 11 }}
                  title={t.title}
                >
                  {t.label}
                </Link>
              ))}
            </div>
          )}

          {/* Inline Compact Metric Chips */}
          <div style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <span className="kpi-chip-compact" title="Total incident files on record">
              <Layers style={{ width: 11, height: 11, color: "var(--accent-cyan)" }} />
              <strong>{totalCount}</strong> Cases
            </span>
            <span className="kpi-chip-compact" title="Files awaiting surveyor certification">
              <Clock style={{ width: 11, height: 11, color: "var(--badge-amber-text)" }} />
              <strong style={{ color: "var(--badge-amber-text)" }}>{pendingCount}</strong> Pending
            </span>
            <span className="kpi-chip-compact" title="Cases nearing or exceeding statutory 48h SLA">
              <AlertTriangle style={{ width: 11, height: 11, color: "var(--badge-red-text)" }} />
              <strong style={{ color: "var(--badge-red-text)" }}>{urgentCount}</strong> Urgent SLA
            </span>
            <span className="kpi-chip-compact" title="Indicative estimated reserves for open cases in SGD -- not an assessed quantum">
              <DollarSign style={{ width: 11, height: 11, color: "var(--badge-green-text)" }} />
              <strong style={{ color: "var(--badge-green-text)" }}>${totalReservesSGD.toLocaleString()}</strong> SGD Est.
            </span>
            <span className="kpi-chip-compact" title="High-stakes State Court SJE dockets & TMA collisions">
              <Flame style={{ width: 11, height: 11, color: "var(--accent-primary)" }} />
              <strong style={{ color: "var(--accent-primary)" }}>{highStakesCount}</strong> SJE/TMA
            </span>
            {admin && (
              <Link
                href="/reports?scope=unassigned"
                prefetch={false}
                className={`kpi-chip-compact case-unassigned-chip ${unassignedCount ? "has-cases" : ""}`}
                title="Cases nobody owns yet. Open one and use Assign to give it to someone."
              >
                <UserX style={{ width: 11, height: 11 }} />
                <strong>{unassignedCount ?? "—"}</strong> Unassigned
              </Link>
            )}
          </div>
        </div>

        {/* Right Actions */}
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
          {enrichNotice && (
            <div style={{ fontSize: 11, fontWeight: 700, color: "var(--accent-cyan)", display: "flex", alignItems: "center", gap: 4 }}>
              <RefreshCw style={{ width: 12, height: 12 }} /> {enrichNotice}
            </div>
          )}

          {/* Every case at once: admins only (the API refuses others). */}
          {isAdmin(user) && (
            <button
              type="button"
              onClick={handleEnrichAll}
              disabled={isEnrichingAll}
              className="btn-secondary-modern"
              style={{ fontSize: 11, padding: "5px 10px", display: "inline-flex", alignItems: "center", gap: 5 }}
              title="On every case: vehicle summary from the recorded make/model, body type from the vehicle catalogue, and unpriced Annex A rows from the recorded damage list. Not AI; adds no new facts."
            >
              <RefreshCw style={{ width: 12, height: 12, color: "var(--accent-cyan)" }} />
              <span>{isEnrichingAll ? "Filling..." : "Fill Derived (All)"}</span>
            </button>
          )}

          <div className="view-mode-toggle" style={{ padding: 2 }}>
            <button
              type="button"
              className={`view-mode-btn ${viewMode === "split" ? "active" : ""}`}
              onClick={() => handleSetViewMode("split")}
              style={{ padding: "4px 8px", fontSize: 11 }}
            >
              <Columns3 style={{ width: 12, height: 12 }} />
              <span>Split</span>
            </button>
            <button
              type="button"
              className={`view-mode-btn ${viewMode === "matrix" ? "active" : ""}`}
              onClick={() => handleSetViewMode("matrix")}
              style={{ padding: "4px 8px", fontSize: 11 }}
            >
              <LayoutGrid style={{ width: 12, height: 12 }} />
              <span>Matrix</span>
            </button>
          </div>

          <Link
            href="/reports/new"
            className="btn-primary-modern"
            style={{ fontSize: 11, padding: "5px 12px", display: "inline-flex", alignItems: "center", gap: 5 }}
            title="Create New Case Intake (Shortcut: N)"
          >
            <Plus style={{ width: 13, height: 13 }} />
            <span>New Intake</span>
            <kbd style={{ fontSize: 9, padding: "1px 4px", background: "rgba(255,255,255,0.2)", borderRadius: 3, fontFamily: "monospace" }}>N</kbd>
          </Link>
        </div>
      </div>

      {notice && (
        <div className="case-scope-notice" role="status">
          {notice}
        </div>
      )}

      {/* 2. Unified Navigation & Operational Filter Toolbar */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        {/* Stage Tabs */}
        <div className="swimlane-tab-bar" style={{ margin: 0, padding: 2, overflowX: "auto", flexShrink: 0 }}>
          <button
            type="button"
            className={`swimlane-tab ${selectedStage === "all" ? "active" : ""}`}
            onClick={() => setSelectedStage("all")}
            style={{ padding: "4px 10px", fontSize: 11 }}
          >
            All Cases ({totalCount})
          </button>
          <button
            type="button"
            className={`swimlane-tab ${selectedStage === "intake" ? "active" : ""}`}
            onClick={() => setSelectedStage("intake")}
            style={{ padding: "4px 10px", fontSize: 11 }}
            title="Fresh intakes and draft filings awaiting surveyor survey"
          >
            New Intake / Draft ({intakeCount})
          </button>
          <button
            type="button"
            className={`swimlane-tab ${selectedStage === "in_survey" ? "active" : ""}`}
            onClick={() => setSelectedStage("in_survey")}
            style={{ padding: "4px 10px", fontSize: 11 }}
            title="Active survey, 3D zone mapping & quantum audit"
          >
            In Survey / Review ({inSurveyCount})
          </button>
          <button
            type="button"
            className={`swimlane-tab ${selectedStage === "signed" ? "active" : ""}`}
            onClick={() => setSelectedStage("signed")}
            style={{ padding: "4px 10px", fontSize: 11 }}
            title="Certified by surveyor & legally locked"
          >
            Certified / Signed ({signedCount})
          </button>
        </div>

        {/* Operational Filters Suite: Date, Car Body, Make, Claim Type, Search */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", flexShrink: 0 }}>
          {/* Date Range Filter */}
          <select
            value={dateFilter}
            onChange={(e) => setDateFilter(e.target.value as DateFilter)}
            style={{
              padding: "4px 8px",
              borderRadius: 6,
              border: "1px solid var(--border-color)",
              background: dateFilter !== "all_time" ? "var(--surface-elevated)" : "var(--surface-card)",
              color: dateFilter !== "all_time" ? "var(--accent-cyan)" : "var(--text-primary)",
              fontSize: 11,
              outline: "none",
            }}
            title="Filter by case intake filing date"
          >
            <option value="all_time">📅 All Time</option>
            <option value="today">📅 Today (24h)</option>
            <option value="yesterday">📅 Yesterday</option>
            <option value="this_week">📅 This Week (7d)</option>
            <option value="this_month">📅 This Month</option>
          </select>

          {/* Body Style Filter */}
          <select
            value={bodyFilter}
            onChange={(e) => setBodyFilter(e.target.value as BodyFilter)}
            style={{
              padding: "4px 8px",
              borderRadius: 6,
              border: "1px solid var(--border-color)",
              background: bodyFilter !== "all" ? "var(--surface-elevated)" : "var(--surface-card)",
              color: bodyFilter !== "all" ? "var(--accent-cyan)" : "var(--text-primary)",
              fontSize: 11,
              outline: "none",
            }}
            title="Filter by vehicle body style"
          >
            <option value="all">🚗 All Bodies</option>
            <option value="Sedan">Sedan</option>
            <option value="SUV">SUV / Crossover</option>
            <option value="Van / Commercial">Van / Commercial</option>
            <option value="MPV">MPV</option>
            <option value="Hatchback">Hatchback</option>
          </select>

          {/* Vehicle Make Filter */}
          <select
            value={makeFilter}
            onChange={(e) => setMakeFilter(e.target.value as MakeFilter)}
            style={{
              padding: "4px 8px",
              borderRadius: 6,
              border: "1px solid var(--border-color)",
              background: makeFilter !== "all" ? "var(--surface-elevated)" : "var(--surface-card)",
              color: makeFilter !== "all" ? "var(--accent-cyan)" : "var(--text-primary)",
              fontSize: 11,
              outline: "none",
            }}
            title="Filter by vehicle manufacturer"
          >
            <option value="all">🏷️ All Makes</option>
            <option value="Honda">Honda</option>
            <option value="Toyota">Toyota</option>
            <option value="Mercedes-Benz">Mercedes-Benz</option>
            <option value="BMW">BMW</option>
            <option value="Volkswagen">Volkswagen</option>
            <option value="Hyundai">Hyundai</option>
            <option value="Commercial Fleet">Commercial Fleet</option>
          </select>

          {/* Claim Archetype Filter */}
          <select
            value={archetypeFilter}
            onChange={(e) => setArchetypeFilter(e.target.value as ArchetypeFilter)}
            style={{
              padding: "4px 8px",
              borderRadius: 6,
              border: "1px solid var(--border-color)",
              background: archetypeFilter !== "all" ? "var(--surface-elevated)" : "var(--surface-card)",
              color: archetypeFilter !== "all" ? "var(--accent-cyan)" : "var(--text-primary)",
              fontSize: 11,
              outline: "none",
            }}
            title="Filter by legal claim archetype"
          >
            <option value="all">⚖️ All Claims</option>
            <option value="pending">Pending Evaluation</option>
            <option value="tp-conv">TP Conventional</option>
            <option value="od">OD Panel</option>
            <option value="sje">SJE Court</option>
            <option value="tma">TMA Expressway</option>
            <option value="tp-direct">TP Direct</option>
          </select>

          {/* Omni-Search */}
          <div style={{ position: "relative", width: 160 }}>
            <Search style={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)", width: 12, height: 12, color: "var(--text-muted)" }} />
            <input
              type="text"
              placeholder="Filter plate, ref..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              style={{
                width: "100%",
                padding: "4px 8px 4px 26px",
                borderRadius: 6,
                border: "1px solid var(--border-color)",
                background: "var(--surface-card)",
                color: "var(--text-primary)",
                fontSize: 11,
                outline: "none",
              }}
            />
          </div>

          {(selectedStage !== "all" || dateFilter !== "all_time" || bodyFilter !== "all" || makeFilter !== "all" || archetypeFilter !== "all" || selectedInsurer !== "all" || searchTerm) && (
            <button
              type="button"
              onClick={() => {
                setSelectedStage("all");
                setDateFilter("all_time");
                setBodyFilter("all");
                setMakeFilter("all");
                setArchetypeFilter("all");
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
                whiteSpace: "nowrap",
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
                const { cost, ref, vehicle, insurer, workshop, filedAt, owner } = rowInfo.get(r)!;
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

                      <span
                        style={{ fontFamily: "var(--font-mono)", fontWeight: 800, fontSize: 12, color: cost > 0 ? "var(--accent-cyan)" : "var(--text-muted)" }}
                        title="Indicative estimate, not an assessment"
                      >
                        {cost > 0 ? `Est. $${cost.toLocaleString()}` : "—"}
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
                      {insurer} • {workshop.split("(")[0].trim()}
                    </div>

                    {showOwner && (
                      <div className={`case-queue-owner ${r.owner ? "" : "unassigned"}`}>
                        Owner: {owner}
                      </div>
                    )}

                    {/* Bottom Row: Exact Filing Time & SLA Badge */}
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, fontSize: 10, marginTop: 2, paddingTop: 4, borderTop: "1px solid var(--border-color)" }}>
                      <span style={{ color: "var(--text-muted)", fontSize: 10 }}>
                        📥 {filedAt}
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
                  {selectedStage === "intake" ? (
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
                      <FileText style={{ width: 28, height: 28, opacity: 0.4 }} />
                      <div>No pending intakes or drafts in repository.</div>
                      <Link href="/reports/new" className="btn-primary-modern" style={{ fontSize: 12, padding: "6px 14px", display: "inline-flex", alignItems: "center", gap: 6 }}>
                        <Plus style={{ width: 13, height: 13 }} /> Start New Intake
                      </Link>
                    </div>
                  ) : (
                    emptyListText
                  )}
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
                        {formatCarlinkRef(selectedSummary.id, selectedSummary.plate_number, selectedSummary.created_at)}
                      </span>

                      {/* Recorded claim type, read-only. This used to be a
                          dropdown whose change was never saved (and whose
                          options didn't match the recorded values), so it
                          showed a guess as if it had been set. */}
                      <span
                        style={{
                          fontSize: 10,
                          fontWeight: 700,
                          padding: "3px 8px",
                          borderRadius: 4,
                          textTransform: "uppercase",
                          background: "var(--surface-hover)",
                          color: selectedSummary.claim_type ? "var(--accent-cyan)" : "var(--text-muted)",
                          border: `1px solid ${selectedSummary.claim_type ? "var(--accent-cyan)" : "var(--border-color)"}`,
                        }}
                        title="Claim type as recorded on the case (read-only here -- change it by editing the case)"
                      >
                        {selectedSummary.claim_type?.trim() ? `⚖️ ${selectedSummary.claim_type}` : "⏳ Pending evaluation"}
                      </span>

                      {selectedSummary.status === "Signed Off" ? (
                        <span className="status-pill" style={{ background: "var(--badge-green-bg)", color: "var(--badge-green-text)", borderColor: "var(--badge-green-border)", fontSize: 10 }}>
                          Certified & Signed Off
                        </span>
                      ) : selectedSummary.status === "intake" ? (
                        <span className="status-pill" style={{ background: "var(--badge-blue-bg)", color: "var(--badge-blue-text)", borderColor: "var(--badge-blue-border)", fontSize: 10 }}>
                          Intake Draft
                        </span>
                      ) : (
                        <span className="status-pill" style={{ background: "var(--badge-amber-bg)", color: "var(--badge-amber-text)", borderColor: "var(--badge-amber-border)", fontSize: 10 }}>
                          Pending Review
                        </span>
                      )}
                    </div>

                    <div style={{ fontSize: 15, fontWeight: 800, color: "var(--text-primary)", marginTop: 6 }}>
                      {inferVehicleName(selectedSummary)} • {selectedSummary.damage_count} Damaged Components
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                      Insurer: <strong style={{ color: "var(--text-primary)" }}>{inferInsurerName(selectedSummary)}</strong> • Workshop: <strong style={{ color: "var(--text-primary)" }}>{inferWorkshopName(selectedSummary.workshop_assigned)}</strong>
                      {showOwner && (
                        <>
                          {" "}
                          • Owner: <strong style={{ color: selectedSummary.owner ? "var(--text-primary)" : "var(--badge-amber-text)" }}>{ownerLabel(selectedSummary.owner)}</strong>
                        </>
                      )}
                    </div>
                  </div>

                  {/* Dossier Quick Action Buttons */}
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    {/* An unfinished intake has no confirmed facts to enrich or
                        print yet -- it can only be resumed in the wizard. */}
                    {selectedSummary.status === "intake" ? (
                      <Link
                        href={`/reports/new?draft=${selectedSummary.id}`}
                        className="btn-primary-modern"
                        style={{ fontSize: 11, padding: "6px 14px", display: "inline-flex", alignItems: "center", gap: 5 }}
                        title="Continue this intake where it was left"
                      >
                        <span>Resume intake</span>
                        <ArrowRight style={{ width: 12, height: 12 }} />
                      </Link>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={handleEnrichCurrent}
                          disabled={isEnrichingCurrent}
                          className="btn-secondary-modern"
                          style={{ fontSize: 11, padding: "6px 12px", display: "inline-flex", alignItems: "center", gap: 5 }}
                          title="Vehicle summary from the recorded make/model, body type from the vehicle catalogue, and unpriced Annex A rows from the recorded damage list. Not AI; adds no new facts."
                        >
                          <RefreshCw style={{ width: 12, height: 12, color: "var(--accent-cyan)" }} />
                          <span>{isEnrichingCurrent ? "Filling..." : "Fill derived fields"}</span>
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
                      </>
                    )}

                    <DeleteButton id={selectedSummary.id} status={selectedSummary.status} ownerId={selectedSummary.owner?.id ?? null} />
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
                      Origin: {channelLabel(selectedSummary.channel)}
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
                      Location: {selectedSummary.location || NOT_RECORDED}
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

                {/* 2-Column Split Hero: Photo Viewer (Left) + Quantum Reserves & Damage Assemblies (Right) */}
                <div className="dossier-split-hero">
                  {/* Left: Evidence Photo Viewer */}
                  <div className="dossier-media-card">
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                      <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "var(--text-muted)", letterSpacing: "0.05em" }}>
                        Photographic Forensic Evidence
                      </span>
                      <span style={{ fontSize: 10, color: "var(--text-muted)", display: "inline-flex", alignItems: "center", gap: 8 }}>
                        {isLoadingDetail
                          ? ""
                          : activeDetail?.photo_urls?.length
                            ? `${activeDetail.photo_urls.length} Evidence Photo${activeDetail.photo_urls.length === 1 ? "" : "s"}`
                            : selectedSummary.thumbnail_url
                              ? "Thumbnail only"
                              : "No photos"}
                        {/* The viewer shows a 1600px copy; the camera file
                            as uploaded is one click away. */}
                        {!isLoadingDetail && activeDetail?.photo_urls?.length ? (
                          <a
                            href={fileUrl(activeDetail.photo_urls[heroIdx])}
                            target="_blank"
                            rel="noopener"
                            className="setup-help-link"
                            style={{ fontSize: 10, display: "inline-flex", alignItems: "center", gap: 3 }}
                            title="Full-size photo as uploaded, in a new tab"
                          >
                            Open original <ExternalLink style={{ width: 10, height: 10 }} />
                          </a>
                        ) : null}
                      </span>
                    </div>

                    <div className="dossier-hero-frame">
                      {isLoadingDetail ? (
                        <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Loading evidence photos...</div>
                      ) : activeDetail?.photo_urls?.length ? (
                        <>
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            src={fileUrl(photoCopyUrl(activeDetail, "photo_preview_urls", heroIdx))}
                            alt="Evidence"
                            className="dossier-hero-img"
                            decoding="async"
                          />
                          <div className="dossier-photo-badge">
                            #{heroIdx + 1} / {activeDetail.photo_urls.length}
                          </div>
                        </>
                      ) : selectedSummary.thumbnail_url ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={fileUrl(selectedSummary.thumbnail_url)}
                          alt="Evidence"
                          className="dossier-hero-img"
                        />
                      ) : (
                        <div style={{ textAlign: "center", color: "var(--text-muted)" }}>
                          <CarFront style={{ width: 28, height: 28, margin: "0 auto 4px", opacity: 0.5 }} />
                          <div style={{ fontSize: 11 }}>No photographic evidence uploaded</div>
                        </div>
                      )}
                    </div>

                    {/* Thumbnail Strip */}
                    {activeDetail?.photo_urls && activeDetail.photo_urls.length > 1 && (
                      <div className="dossier-thumb-strip" style={{ marginTop: 6 }}>
                        {activeDetail.photo_urls.map((url, idx) => (
                          <button
                            key={url}
                            type="button"
                            className={`dossier-thumb-btn ${idx === activePhotoIdx ? "active" : ""}`}
                            onClick={() => setActivePhotoIdx(idx)}
                            title={`Photo ${idx + 1}`}
                          >
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img
                              src={fileUrl(photoCopyUrl(activeDetail, "photo_thumb_urls", idx))}
                              alt=""
                              className="dossier-thumb-img"
                              loading="lazy"
                              decoding="async"
                            />
                          </button>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Right: Quantum Deck & Damage Assemblies */}
                  <div className="dossier-quantum-card">
                    {/* Financial Reserves Cards */}
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, background: "var(--surface-hover)", padding: "10px 12px", borderRadius: 8, border: "1px solid var(--border-color)" }}>
                      <div title="The recorded repair estimate if there is one, otherwise indicative from the damaged parts and labour tariffs -- not an assessment">
                        <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 700 }}>
                          Est. Reserve{selectedSummary.estimated_repair_cost ? "" : " (Indicative)"}
                        </div>
                        <div style={{ fontSize: 16, fontWeight: 800, color: "var(--accent-cyan)", fontFamily: "var(--font-mono)", marginTop: 2 }}>
                          {getEstimatedCostSGD(selectedSummary) > 0 ? (
                            <>${getEstimatedCostSGD(selectedSummary).toLocaleString()} <span style={{ fontSize: 9 }}>SGD</span></>
                          ) : (
                            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>—</span>
                          )}
                        </div>
                      </div>
                      {/* Recorded approved cost. Replaces "Negotiated Savings",
                          which was a flat 21% of the estimate. */}
                      <div>
                        <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 700 }}>Approved Cost</div>
                        <div style={{ fontSize: 16, fontWeight: 800, color: "var(--badge-green-text)", fontFamily: "var(--font-mono)", marginTop: 2 }}>
                          {formatRecordedSGD(selectedSummary.final_approved_cost) ? (
                            <>{formatRecordedSGD(selectedSummary.final_approved_cost)} <span style={{ fontSize: 9 }}>SGD</span></>
                          ) : (
                            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{NOT_RECORDED}</span>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Cluster Workshop & SLA Countdown */}
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                      <div style={{ padding: "8px 10px", background: "var(--surface-card)", border: "1px solid var(--border-color)", borderRadius: 6 }}>
                        <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 700 }}>Cluster Workshop</div>
                        <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-primary)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {inferWorkshopName(selectedSummary.workshop_assigned)}
                        </div>
                      </div>

                      <div style={{ padding: "8px 10px", background: "var(--surface-card)", border: "1px solid var(--border-color)", borderRadius: 6 }}>
                        <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 700 }}>Statutory SLA</div>
                        <div style={{ fontSize: 11, fontWeight: 700, color: getGiaSla(selectedSummary.created_at, selectedSummary.status === "Signed Off").pillColor, marginTop: 2 }}>
                          {getGiaSla(selectedSummary.created_at, selectedSummary.status === "Signed Off").remainingText}
                        </div>
                      </div>
                    </div>

                    {/* Impacted Assemblies Tag Cloud */}
                    <div style={{ padding: "8px 10px", background: "var(--surface-card)", border: "1px solid var(--border-color)", borderRadius: 6, flex: 1, display: "flex", flexDirection: "column" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                        <span style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 700 }}>
                          Impacted Assemblies ({selectedSummary.damage_count} Parts)
                        </span>
                        {aiDetectedCount > 0 && (
                          <span style={{ fontSize: 9, color: "var(--text-muted)" }}>
                            {aiDetectedCount} of {damageItems.length} AI-detected
                          </span>
                        )}
                      </div>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 5, maxHeight: 110, overflowY: "auto", scrollbarWidth: "thin" }}>
                        {damageItems.length > 0 ? (
                          damageItems.map((d, i) => (
                            <span
                              key={i}
                              style={{
                                display: "inline-flex",
                                alignItems: "center",
                                gap: 4,
                                padding: "2px 6px",
                                borderRadius: 4,
                                fontSize: 10,
                                background: "var(--surface-hover)",
                                border: "1px solid var(--border-color)",
                                color: "var(--text-primary)",
                              }}
                            >
                              <span style={{ fontWeight: 600 }}>{d.part}</span>
                              <span
                                style={{
                                  fontSize: 8,
                                  fontWeight: 700,
                                  padding: "1px 4px",
                                  borderRadius: 3,
                                  textTransform: "uppercase",
                                  background: SEVERITY_CHIP[severityClass(d.severity)].bg,
                                  color: SEVERITY_CHIP[severityClass(d.severity)].fg,
                                }}
                              >
                                {d.severity || "Unrated"}
                              </span>
                            </span>
                          ))
                        ) : (
                          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                            {isLoadingDetail
                              ? "Loading..."
                              : recordedParts.length > 0
                                ? recordedParts.join(", ")
                                : !activeDetail && selectedSummary.damage_count > 0
                                  ? "Damage detail unavailable"
                                  : "No damage recorded"}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Damaged Parts Anatomy Matrix */}
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <span style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", color: "var(--text-muted)", letterSpacing: "0.05em" }}>
                      Damage Anatomy & Line-Item Estimations
                    </span>
                    {aiDetectedCount > 0 && (
                      <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                        {aiDetectedCount} of {damageItems.length} AI-detected
                      </span>
                    )}
                  </div>

                  {damageItems.length > 0 ? (
                    <div style={{ border: "1px solid var(--border-color)", borderRadius: 8, overflow: "hidden" }}>
                      <table className="triage-table">
                        <thead>
                          <tr>
                            <th>Identified Component</th>
                            <th>Damage Classification</th>
                            <th>Severity</th>
                            <th title="Indicative list price per part, not an assessment">Est. Quantum (Indicative)</th>
                            <th>Action</th>
                          </tr>
                        </thead>
                        <tbody>
                          {damageItems.map((d, i) => {
                            const partPrice = PART_PRICES_MAP[d.part] || 1200;
                            return (
                              <tr key={i}>
                                <td style={{ fontWeight: 600, color: "var(--text-primary)" }}>{d.part}</td>
                                <td style={{ color: "var(--text-secondary)" }}>{d.damage_type || "—"}</td>
                                <td>
                                  <span
                                    style={{
                                      fontSize: 10,
                                      fontWeight: 700,
                                      padding: "2px 6px",
                                      borderRadius: 4,
                                      textTransform: "uppercase",
                                      background: SEVERITY_CHIP[severityClass(d.severity)].bg,
                                      color: SEVERITY_CHIP[severityClass(d.severity)].fg,
                                    }}
                                  >
                                    {d.severity || "Unrated"}
                                  </span>
                                </td>
                                <td style={{ fontFamily: "var(--font-mono)", fontWeight: 700, fontSize: 11, color: "var(--accent-cyan)" }}>
                                  Est. ${partPrice.toLocaleString()} SGD
                                </td>
                                {/* repair_required defaults to false server-side, so
                                    false means "not decided", not "replace". */}
                                <td style={{ fontSize: 11, color: "var(--text-muted)" }}>
                                  {d.repair_required ? "Repair / Rectify" : "—"}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div style={{ padding: 12, borderRadius: 8, background: "var(--surface-hover)", border: "1px solid var(--border-color)", fontSize: 11, color: "var(--text-muted)" }}>
                      {selectedSummary.damage_count > 0 ? (
                        <>
                          Damage summary: {selectedSummary.damage_count} component{selectedSummary.damage_count === 1 ? "" : "s"} recorded. Severity: <strong>{selectedSummary.severity_level || "Unrated"}</strong>.
                        </>
                      ) : (
                        "No damage recorded."
                      )}
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
                  <th>Case Ref & Intake Time</th>
                  <th>Claim Archetype</th>
                  <th>Insurer // Workshop</th>
                  {showOwner && <th>Owner</th>}
                  <th className="col-reserve">Est. Reserve ($ SGD)</th>
                  <th>SLA Window</th>
                  <th className="col-actions">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredReports.map((r) => {
                  const { arch, cost, ref, vehicle, insurer, workshop, filedAt, owner } = rowInfo.get(r)!;
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
                          📥 {filedAt}
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

                      {showOwner && (
                        <td title={owner}>
                          <div
                            className="triage-cell-truncate"
                            style={{ fontSize: 11, fontWeight: 700, color: r.owner ? "var(--text-primary)" : "var(--badge-amber-text)" }}
                          >
                            {r.owner ? r.owner.name || `@${r.owner.username}` : "Unassigned"}
                          </div>
                          {r.owner && (
                            <div className="triage-cell-truncate" style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 1 }}>
                              @{r.owner.username}
                              {!r.owner.active && " (disabled)"}
                            </div>
                          )}
                        </td>
                      )}

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

                          {/* No prefetch: one per row would queue a request
                              for every case in view. */}
                          <Link
                            href={`/reports/${r.id}`}
                            prefetch={false}
                            className="btn-primary-modern"
                            style={{ fontSize: 11, padding: "4px 9px", display: "inline-flex", alignItems: "center", gap: 3 }}
                            title="Open full Loss Adjuster Studio"
                          >
                            <span>Studio</span>
                            <ArrowRight style={{ width: 12, height: 12 }} />
                          </Link>

                          <DeleteButton id={r.id} status={r.status} ownerId={r.owner?.id ?? null} />
                        </div>
                      </td>
                    </tr>
                  );
                })}

                {filteredReports.length === 0 && (
                  <tr>
                    <td colSpan={showOwner ? 9 : 8} style={{ textAlign: "center", padding: 36, color: "var(--text-muted)" }}>
                      {selectedStage === "intake" ? (
                        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
                          <FileText style={{ width: 28, height: 28, opacity: 0.4 }} />
                          <div>No pending drafts in repository.</div>
                          <Link href="/reports/new" className="btn-primary-modern" style={{ fontSize: 12, padding: "6px 14px", display: "inline-flex", alignItems: "center", gap: 6 }}>
                            <Plus style={{ width: 13, height: 13 }} /> Start New Intake
                          </Link>
                        </div>
                      ) : (
                        emptyListText
                      )}
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
