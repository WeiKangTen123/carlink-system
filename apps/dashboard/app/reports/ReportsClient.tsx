"use client";

import { useState, useMemo, useEffect, type CSSProperties } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, CarFront, FileText, Inbox, Plus, RefreshCw, Search, UserX } from "lucide-react";
import { type ReportSummary, type AnalyticsSummary, type CaseScope, fileUrl, ownerLabel } from "@/lib/api";
import { useInboxCount } from "@/components/inbox/useInboxCount";
import { DeleteButton } from "@/components/DeleteButton";
import { canViewAllCases, isAdmin, useCurrentUser } from "@/components/CurrentUser";
import { PdfPreviewModal } from "@/components/PdfPreviewModal";
import {
  daysOpen,
  formatCarlinkRef,
  formatSgtDate,
  formatSgtDateTime,
  getOperationalStage,
  inferInsurerName,
  inferVehicleName,
  inferWorkshopName,
  isAwaitingSignOff,
  matchesDateFilter,
  ourRefLabel,
  REF_TO_BE_ISSUED,
  statusKind,
  statusLabel,
  type StatusKind,
  NOT_RECORDED,
} from "@/lib/caseFields";
import { aiEnrichAllReportsAction, countUnassignedAction } from "./actions";

type Stage = ReturnType<typeof getOperationalStage>;
type StageTab = "all" | Stage;
type DateFilter = "all_time" | "today" | "yesterday" | "this_week" | "this_month";

interface Props {
  initialReports: ReportSummary[];
  /** Still loaded by app/reports/page.tsx; the list no longer shows it. */
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

/** The stage tabs, in the order a case moves through them. */
const STAGE_TABS: { key: StageTab; label: string; title: string }[] = [
  { key: "all", label: "All", title: "Every case in this list" },
  { key: "intake", label: "Intake drafts", title: "Unfinished intakes, and cases with no damage recorded yet" },
  { key: "in_survey", label: "Open", title: "Filed cases a surveyor has not signed off" },
  { key: "signed", label: "Signed off", title: "Cases a surveyor has signed off" },
];

const DATE_OPTIONS: { key: DateFilter; label: string }[] = [
  { key: "all_time", label: "All time" },
  { key: "today", label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "this_week", label: "This week" },
  { key: "this_month", label: "This month" },
];

/** Claim-type filter value for cases with none recorded. A control
 * character, so it can never equal a value someone typed. */
const NO_CLAIM_TYPE = "\u0000none";

/** Status chip colours by the recorded status's kind. An unknown status
 * keeps the chip's neutral default. */
const STATUS_PILL: Partial<Record<StatusKind, CSSProperties>> = {
  signed: { background: "var(--badge-green-bg)", color: "var(--badge-green-text)", borderColor: "var(--badge-green-border)" },
  intake: { background: "var(--badge-blue-bg)", color: "var(--badge-blue-text)", borderColor: "var(--badge-blue-border)" },
  awaiting: { background: "var(--badge-amber-bg)", color: "var(--badge-amber-text)", borderColor: "var(--badge-amber-border)" },
};

/** One look for the filter dropdowns; lit while the filter is on. */
function selectStyle(active: boolean): CSSProperties {
  return {
    padding: "4px 8px",
    borderRadius: 6,
    border: "1px solid var(--border-color)",
    background: active ? "var(--surface-elevated)" : "var(--surface-card)",
    color: active ? "var(--accent-cyan)" : "var(--text-primary)",
    fontSize: 11,
    outline: "none",
  };
}

/** "today", "1d ago", "12d ago": time since the case was filed. */
function filedAgo(createdAt: string): string {
  const d = daysOpen(createdAt);
  return d === 0 ? "today" : `${d}d ago`;
}

/** What a row shows, worked out once per list rather than on every
 * keystroke in the search box. Every value is recorded or a stated gap. */
type RowInfo = {
  stage: Stage;
  vehicle: string;
  /** cover_page.report_id, or "to be issued". */
  ourRef: string;
  /** Trimmed recorded claim type; null when none. */
  claimType: string | null;
  requestedBy: string | null;
  workshop: string;
  /** "Tan Ah Huat (@ahhuat)", or "Unassigned". */
  owner: string;
  filedOn: string;
  filedAt: string;
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
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedStage, setSelectedStage] = useState<StageTab>("all");
  const [dateFilter, setDateFilter] = useState<DateFilter>("all_time");
  const [claimTypeFilter, setClaimTypeFilter] = useState("all");
  const [previewReport, setPreviewReport] = useState<ReportSummary | null>(null);

  // "Fill derived (all)" state (the /ai-enrich-all endpoint; not an AI call)
  const [isEnrichingAll, setIsEnrichingAll] = useState(false);
  const [enrichNotice, setEnrichNotice] = useState<string | null>(null);

  const router = useRouter();

  // Hotkey: N opens New intake unless something is being typed.
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const activeEl = document.activeElement;
      const isInput =
        activeEl &&
        (activeEl.tagName === "INPUT" ||
          activeEl.tagName === "TEXTAREA" ||
          activeEl.tagName === "SELECT" ||
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

  // A fresh list from the server (another scope, a delete) replaces ours.
  useEffect(() => {
    if (initialReports) {
      setReportsList(initialReports);
    }
  }, [initialReports]);

  // Admins' Unassigned chip. The All and Unassigned lists show it directly;
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
  // The Public Inbox chip (release P6): reports from the public nobody has
  // taken, for people who see all cases. Asked again with every new list.
  const inboxCount = useInboxCount(showOwner, { refreshKey: initialReports });

  const unassignedCount = useMemo(() => {
    if (activeScope === "unassigned") return reportsList.length;
    if (activeScope === "all") return reportsList.filter((r) => !r.owner).length;
    return unassignedElsewhere;
  }, [activeScope, reportsList, unassignedElsewhere]);

  const rowInfo = useMemo(() => {
    const map = new Map<ReportSummary, RowInfo>();
    for (const r of reportsList) {
      const vehicle = inferVehicleName(r);
      const workshop = inferWorkshopName(r.workshop_assigned);
      const claimType = (r.claim_type || "").trim() || null;
      // report_id and requested_by are the two fields GET /reports gained
      // this round (contract2, section 1): null until the cover page
      // records them, so the row says "to be issued" / "Not recorded".
      const requestedBy = (r.requested_by || "").trim() || null;
      // Owner name and username, so the search finds someone's cases.
      const ownerText = r.owner ? `${r.owner.name} ${r.owner.username}` : "unassigned";
      map.set(r, {
        stage: getOperationalStage(r),
        vehicle,
        ourRef: ourRefLabel(r.report_id),
        claimType,
        requestedBy,
        workshop,
        owner: ownerLabel(r.owner),
        filedOn: formatSgtDate(r.created_at),
        filedAt: formatSgtDateTime(r.created_at),
        // The insurer and the old REF- code are searchable though no longer
        // shown; public_reference is the "TG-261030-K7M4" a member of the
        // public was given (release P6).
        searchText: [
          r.id,
          formatCarlinkRef(r.id, r.plate_number, r.created_at),
          r.plate_number || "",
          vehicle,
          r.location || "",
          inferInsurerName(r),
          workshop,
          ownerText,
          r.public_reference || "",
          r.report_id || "",
          requestedBy || "",
          claimType || "",
        ]
          .join(" ")
          .toLowerCase(),
      });
    }
    return map;
  }, [reportsList]);

  // The claim-type dropdown lists only values recorded on cases in this
  // list -- it used to offer fixed "archetypes" that matched nothing.
  const claimTypeOptions = useMemo(() => {
    const values = new Set<string>();
    let unrecorded = false;
    for (const r of reportsList) {
      const ct = rowInfo.get(r)!.claimType;
      if (ct) values.add(ct);
      else unrecorded = true;
    }
    return { values: Array.from(values).sort((a, b) => a.localeCompare(b)), unrecorded };
  }, [reportsList, rowInfo]);

  // A new list (another scope) may no longer offer the claim type chosen
  // for the old one; a filter the dropdown can't show would hide rows with
  // no way to see why, so it falls back to All.
  useEffect(() => {
    if (claimTypeFilter === "all") return;
    const offered = claimTypeFilter === NO_CLAIM_TYPE ? claimTypeOptions.unrecorded : claimTypeOptions.values.includes(claimTypeFilter);
    if (!offered) setClaimTypeFilter("all");
  }, [claimTypeOptions, claimTypeFilter]);

  const stageCounts = useMemo(() => {
    const counts: Record<StageTab, number> = { all: reportsList.length, intake: 0, in_survey: 0, signed: 0 };
    for (const r of reportsList) counts[rowInfo.get(r)!.stage] += 1;
    return counts;
  }, [reportsList, rowInfo]);
  const pendingCount = useMemo(() => reportsList.filter((r) => isAwaitingSignOff(r.status)).length, [reportsList]);

  const filteredReports = useMemo(() => {
    const q = searchTerm.trim().toLowerCase();
    return reportsList.filter((r) => {
      const { stage, claimType, searchText } = rowInfo.get(r)!;
      if (selectedStage !== "all" && stage !== selectedStage) return false;
      if (!matchesDateFilter(r.created_at, dateFilter)) return false;
      if (claimTypeFilter === NO_CLAIM_TYPE) {
        if (claimType !== null) return false;
      } else if (claimTypeFilter !== "all" && claimType !== claimTypeFilter) {
        return false;
      }
      if (q && !searchText.includes(q)) return false;
      return true;
    });
  }, [reportsList, rowInfo, selectedStage, dateFilter, claimTypeFilter, searchTerm]);

  // Filters other than the stage tab. Used for Reset and to tell "no
  // intake drafts at all" from "none matching these filters".
  const narrowed = dateFilter !== "all_time" || claimTypeFilter !== "all" || searchTerm.trim() !== "";
  const hasFilters = selectedStage !== "all" || narrowed;
  const resetFilters = () => {
    setSelectedStage("all");
    setDateFilter("all_time");
    setClaimTypeFilter("all");
    setSearchTerm("");
  };

  // "Fill derived (all)" (POST /reports/ai-enrich-all). Despite the endpoint
  // name it is not an AI call and invents nothing: it only derives the
  // vehicle summary from make/model, the body type from the vehicle
  // catalogue, and unpriced Annex A rows from the recorded damage list.
  const handleEnrichAll = async () => {
    setIsEnrichingAll(true);
    setEnrichNotice("Filling derived fields on all cases...");
    try {
      const res = await aiEnrichAllReportsAction();
      if ("error" in res) {
        setEnrichNotice(`Fill derived fields failed: ${res.error}`);
      } else {
        setEnrichNotice(`Derived fields filled on ${res.count ?? 0} case${res.count === 1 ? "" : "s"}.`);
        // The vehicle names in the rows may have changed: ask for the list again.
        router.refresh();
      }
    } catch {
      setEnrichNotice("Fill derived fields failed.");
    } finally {
      setIsEnrichingAll(false);
      setTimeout(() => setEnrichNotice(null), 4000);
    }
  };

  // An empty list says whether it is empty or just filtered.
  const emptyListText =
    reportsList.length > 0
      ? "No cases match these filters."
      : activeScope === "unassigned"
        ? "No unassigned cases. Every case has an owner."
        : activeScope === "mine"
          ? "You have no cases yet. Cases you start, or an admin gives you, appear here."
          : "No cases yet.";
  const columnCount = showOwner ? 8 : 7;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {/* Title, scope, counts, actions */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap", minHeight: 36 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <h1 style={{ margin: 0, fontSize: 17, fontWeight: 800, letterSpacing: "-0.02em", whiteSpace: "nowrap" }}>Cases</h1>

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

          <div style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap", fontSize: 11, color: "var(--text-muted)" }}>
            <span>
              <strong style={{ color: "var(--text-primary)" }}>{stageCounts.all}</strong> case{stageCounts.all === 1 ? "" : "s"}
              {" · "}
              <strong style={{ color: "var(--badge-amber-text)" }}>{pendingCount}</strong> awaiting sign-off
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
            {showOwner && (
              <Link
                href="/inbox"
                prefetch={false}
                className={`kpi-chip-compact case-unassigned-chip ${inboxCount?.new ? "has-cases" : ""}`}
                title="Reports the public sent through the Telegram bot that nobody has taken yet"
              >
                <Inbox style={{ width: 11, height: 11 }} />
                Inbox <strong>{inboxCount ? inboxCount.new : "—"}</strong>
              </Link>
            )}
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
          {enrichNotice && (
            <div style={{ fontSize: 11, fontWeight: 700, color: "var(--accent-cyan)", display: "flex", alignItems: "center", gap: 4 }} role="status">
              <RefreshCw style={{ width: 12, height: 12 }} /> {enrichNotice}
            </div>
          )}

          {/* Every case at once: admins only (the API refuses others). */}
          {admin && (
            <button
              type="button"
              onClick={handleEnrichAll}
              disabled={isEnrichingAll}
              className="btn-secondary-modern"
              style={{ fontSize: 11, padding: "5px 10px", display: "inline-flex", alignItems: "center", gap: 5 }}
              title="On every case: vehicle summary from the recorded make/model, body type from the vehicle catalogue, and unpriced Annex A rows from the recorded damage list. Not AI; adds no new facts."
            >
              <RefreshCw style={{ width: 12, height: 12, color: "var(--accent-cyan)" }} />
              <span>{isEnrichingAll ? "Filling..." : "Fill derived (all)"}</span>
            </button>
          )}

          <Link
            href="/reports/new"
            className="btn-primary-modern"
            style={{ fontSize: 11, padding: "5px 12px", display: "inline-flex", alignItems: "center", gap: 5 }}
            title="Start a new intake (shortcut: N)"
          >
            <Plus style={{ width: 13, height: 13 }} />
            <span>New intake</span>
            <kbd style={{ fontSize: 9, padding: "1px 4px", background: "rgba(255,255,255,0.2)", borderRadius: 3, fontFamily: "monospace" }}>N</kbd>
          </Link>
        </div>
      </div>

      {notice && (
        <div className="case-scope-notice" role="status">
          {notice}
        </div>
      )}

      {/* Stage tabs and filters */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <div className="view-mode-toggle" style={{ padding: 2, overflowX: "auto" }} role="tablist" aria-label="Stage">
          {STAGE_TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={selectedStage === t.key}
              className={`view-mode-btn ${selectedStage === t.key ? "active" : ""}`}
              onClick={() => setSelectedStage(t.key)}
              style={{ padding: "4px 10px", fontSize: 11, whiteSpace: "nowrap" }}
              title={t.title}
            >
              {t.label} ({stageCounts[t.key]})
            </button>
          ))}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          <select
            value={dateFilter}
            onChange={(e) => setDateFilter(e.target.value as DateFilter)}
            style={selectStyle(dateFilter !== "all_time")}
            title="Filed within"
            aria-label="Filed within"
          >
            {DATE_OPTIONS.map((o) => (
              <option key={o.key} value={o.key}>
                {o.label}
              </option>
            ))}
          </select>

          <select
            value={claimTypeFilter}
            onChange={(e) => setClaimTypeFilter(e.target.value)}
            style={selectStyle(claimTypeFilter !== "all")}
            title="Claim type, as recorded on the cases"
            aria-label="Claim type"
          >
            <option value="all">All claim types</option>
            {claimTypeOptions.values.map((ct) => (
              <option key={ct} value={ct}>
                {ct}
              </option>
            ))}
            {claimTypeOptions.unrecorded && <option value={NO_CLAIM_TYPE}>Claim type not recorded</option>}
          </select>

          <div style={{ position: "relative", width: 220 }}>
            <Search style={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)", width: 12, height: 12, color: "var(--text-muted)" }} />
            <input
              type="search"
              placeholder="Search plate, ref, owner, public ref"
              aria-label="Search cases"
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

          {hasFilters && (
            <button
              type="button"
              onClick={resetFilters}
              style={{ fontSize: 11, background: "none", border: "none", color: "var(--accent-cyan)", cursor: "pointer", textDecoration: "underline", whiteSpace: "nowrap" }}
            >
              Reset
            </button>
          )}
        </div>
      </div>

      {/* The table */}
      <div className="card-glass triage-table-card" style={{ padding: 0 }}>
        <div className="triage-table-wrapper" style={{ margin: 0 }}>
          <table className="triage-table">
            <thead>
              <tr>
                <th>Photo</th>
                <th>Plate - vehicle</th>
                <th>Our Ref</th>
                <th>Claim type</th>
                <th>Requested by / workshop</th>
                {showOwner && <th>Owner</th>}
                <th>Status - filed</th>
                <th className="col-actions">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredReports.map((r) => {
                const info = rowInfo.get(r)!;
                const hasRef = info.ourRef !== REF_TO_BE_ISSUED;
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
                        <div
                          style={{ width: 48, height: 36, borderRadius: 4, background: "var(--surface-hover)", display: "flex", alignItems: "center", justifyContent: "center" }}
                          title="No photos"
                        >
                          <CarFront style={{ width: 16, height: 16, color: "var(--text-muted)" }} />
                        </div>
                      )}
                    </td>

                    <td>
                      {/* No plate: say so. An id fragment in the plate badge
                          used to read as a registration number. */}
                      {r.plate_number ? (
                        <span className="badge-plate-command">{r.plate_number}</span>
                      ) : (
                        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Plate not recorded</span>
                      )}
                      <div className="triage-cell-truncate" style={{ fontSize: 11, color: "var(--text-primary)", fontWeight: 700, marginTop: 2 }}>
                        {info.vehicle}
                      </div>
                    </td>

                    <td>
                      <span
                        style={{ fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 700, color: hasRef ? "var(--text-primary)" : "var(--text-muted)" }}
                        title={hasRef ? "The firm's file number, from the cover page" : "No file number on the cover page yet"}
                      >
                        {info.ourRef}
                      </span>
                    </td>

                    <td>
                      <span style={{ fontSize: 11, color: info.claimType ? "var(--text-primary)" : "var(--text-muted)" }}>{info.claimType ?? NOT_RECORDED}</span>
                    </td>

                    <td>
                      <div className="triage-cell-truncate" style={{ fontSize: 11, fontWeight: 700, color: info.requestedBy ? "var(--text-primary)" : "var(--text-muted)" }}>
                        {info.requestedBy ?? NOT_RECORDED}
                      </div>
                      <div className="triage-cell-truncate" style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 1 }}>
                        {info.workshop}
                      </div>
                    </td>

                    {showOwner && (
                      <td title={info.owner}>
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

                    <td>
                      <span className="status-pill" style={{ ...STATUS_PILL[statusKind(r.status)], fontSize: 10, whiteSpace: "nowrap" }}>
                        {statusLabel(r.status)}
                      </span>
                      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 3, whiteSpace: "nowrap" }} title={`Filed ${info.filedAt}`}>
                        {info.filedOn} · {filedAgo(r.created_at)}
                      </div>
                    </td>

                    <td className="col-actions">
                      <div style={{ display: "inline-flex", alignItems: "center", gap: 6, justifyContent: "flex-end" }}>
                        {/* An unfinished intake has nothing to print yet; it
                            can only be resumed in the wizard. */}
                        {r.status === "intake" ? (
                          <Link
                            href={`/reports/new?draft=${r.id}`}
                            prefetch={false}
                            className="btn-primary-modern"
                            style={{ fontSize: 11, padding: "4px 9px", display: "inline-flex", alignItems: "center", gap: 3 }}
                            title="Continue this intake where it was left"
                          >
                            <span>Resume intake</span>
                            <ArrowRight style={{ width: 12, height: 12 }} />
                          </Link>
                        ) : (
                          <>
                            <button
                              type="button"
                              onClick={() => setPreviewReport(r)}
                              className="btn-secondary-modern"
                              style={{ fontSize: 11, padding: "4px 8px", display: "inline-flex", alignItems: "center", gap: 4 }}
                              title="Preview the report PDF"
                            >
                              <FileText style={{ width: 12, height: 12, color: "var(--accent-primary)" }} />
                              <span>PDF</span>
                            </button>

                            {/* No prefetch: one per row would queue a request
                                for every case in view. */}
                            <Link
                              href={`/reports/${r.id}`}
                              prefetch={false}
                              className="btn-primary-modern"
                              style={{ fontSize: 11, padding: "4px 9px", display: "inline-flex", alignItems: "center", gap: 3 }}
                              title="Open the case"
                            >
                              <span>Open</span>
                              <ArrowRight style={{ width: 12, height: 12 }} />
                            </Link>
                          </>
                        )}

                        <DeleteButton id={r.id} status={r.status} ownerId={r.owner?.id ?? null} />
                      </div>
                    </td>
                  </tr>
                );
              })}

              {filteredReports.length === 0 && (
                <tr>
                  <td colSpan={columnCount} style={{ textAlign: "center", padding: 36, color: "var(--text-muted)" }}>
                    {selectedStage === "intake" && !narrowed ? (
                      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
                        <FileText style={{ width: 28, height: 28, opacity: 0.4 }} />
                        <div>No intake drafts.</div>
                        <Link href="/reports/new" className="btn-primary-modern" style={{ fontSize: 12, padding: "6px 14px", display: "inline-flex", alignItems: "center", gap: 6 }}>
                          <Plus style={{ width: 13, height: 13 }} /> Start a new intake
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

      {/* The PDF is named after the file number when there is one, else the
          plate, else just "report": never "to be issued", an id fragment
          or any other generated code. */}
      {previewReport && (
        <PdfPreviewModal
          reportId={previewReport.id}
          reportCode={(previewReport.report_id || "").trim() || previewReport.plate_number || "report"}
          isOpen={true}
          onClose={() => setPreviewReport(null)}
          trigger={null}
        />
      )}
    </div>
  );
}
