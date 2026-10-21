import type { ReportSummary } from "./api";
import { FACTORY_LABOR_TARIFFS } from "./factoryPricing";

/** Shared derivations over report/damage data.
 *
 * These were previously copy-pasted across pages and components
 * (severityClass in 2 files, caseTitle in 2, daysOpen in 3, the
 * awaiting-sign-off status set in 2), which meant a fix in one place
 * silently left the others wrong. They live here so every surface agrees
 * by construction.
 */

/** Which statuses count as "still awaiting sign-off".
 *
 * Mirrors the same bucket get_analytics_summary uses server-side for
 * pending_review -- kept deliberately identical so the Overview count and
 * the API's own count can never disagree. If one changes, change both.
 */
const AWAITING_SIGN_OFF = new Set(["confirmed", "draft", "pending", "Under Review"]);

export function isAwaitingSignOff(status: string): boolean {
  return AWAITING_SIGN_OFF.has(status);
}

/** CSS modifier for a severity value.
 *
 * Returns "unrated" -- not "minor" -- when severity is missing. The
 * schema allows a null severity on purpose (extraction.py tells the model
 * to leave it null rather than guess), and rendering an unassessed part
 * as a green "Minor" chip would quietly assert an assessment nobody made.
 */
export function severityClass(severity?: string | null): "severe" | "moderate" | "minor" | "unrated" {
  const s = (severity || "").toLowerCase();
  if (!s.trim()) return "unrated";
  if (s.includes("severe")) return "severe";
  if (s.includes("moderate")) return "moderate";
  if (s.includes("minor")) return "minor";
  return "unrated";
}

/** Ranking for "worst severity wins" comparisons. Unrated sorts lowest so
 * it never outranks a real assessment. */
export const SEVERITY_RANK: Record<string, number> = { unrated: 0, minor: 1, moderate: 2, severe: 3 };

// Shown wherever a fact simply wasn't recorded. These screens and the PDF
// can reach insurers and courts, so a gap must read as a gap: until
// 2026-10-21 the helpers below filled gaps with guesses made from plate
// prefixes and keywords ("SLK..." -> Honda Vezel, default insurer Tokio
// Marine, default workshop ComfortDelGro), which read as recorded facts.
export const NOT_RECORDED = "Not recorded";

/** The vehicle as recorded (make + model, from the API's vehicle_name), or
 * "Vehicle not recorded". Never guessed from the plate. */
export function inferVehicleName(r: {
  vehicle_name?: string | null;
  plate_number?: string | null;
  accident_type?: string | null;
  category?: string[];
}): string {
  const current = (r.vehicle_name || "").trim();
  if (current && !["Motor Vehicle", "Vehicle", "None", "None None"].includes(current)) {
    return current;
  }
  return "Vehicle not recorded";
}

/** How a case is labelled in lists.
 *
 * Plate first (the most specific real identifier), then vehicle name.
 */
export function caseTitle(r: ReportSummary): string {
  return r.plate_number || inferVehicleName(r) || r.category?.[0] || "Incident";
}

export function daysOpen(createdAt: string): number {
  return Math.max(0, Math.floor((Date.now() - new Date(createdAt).getTime()) / 86400000));
}


/** The system's own reference for a case, from its id and filing year.
 * "REF-", not "CL ...": Carlink's real file numbers are "CL 11900" style,
 * and a generated one must not pass for an issued file number (this used to
 * hand SLK 3063 Z the real CL 11900 and everything else "CL-2026-xxxx"). */
export function formatCarlinkRef(id: string, _plate?: string | null, createdAt?: string | null): string {
  const year = createdAt ? new Date(createdAt).getFullYear() : NaN;
  const short = id.slice(0, 6).toUpperCase();
  return Number.isNaN(year) ? `REF-${short}` : `REF-${year}-${short}`;
}

/** The claim archetype from the recorded claim type only; "Pending
 * evaluation" when none is recorded. It used to be guessed from the
 * accident type, vehicle and plate (and "od" matched any word containing
 * those letters, e.g. "Model"), defaulting everything to TP Conventional. */
export function getClaimArchetype(r: {
  type?: string;
  accident_type?: string | null;
  vehicle_name?: string | null;
  plate_number?: string | null;
  claim_type?: string | null;
}): {
  key: "sje" | "tma" | "tp-conv" | "tp-direct" | "od" | "pending";
  label: string;
  badgeClass: string;
} {
  const ct = (r.claim_type || "").toLowerCase();
  if (ct.includes("sje") || ct.includes("court")) return { key: "sje", label: "SJE Court", badgeClass: "sje" };
  if (ct.includes("tma") || ct.includes("attenuator")) return { key: "tma", label: "TMA Expressway", badgeClass: "tma" };
  if (ct.includes("own damage") || /\bod\b/.test(ct) || ct.includes("comprehensive")) return { key: "od", label: "OD Panel", badgeClass: "od" };
  if (ct.includes("direct")) return { key: "tp-direct", label: "TP Direct", badgeClass: "tp-direct" };
  if (ct.includes("third party") || /\btp\b/.test(ct)) return { key: "tp-conv", label: "TP Conventional", badgeClass: "tp-conv" };
  return { key: "pending", label: "Pending Evaluation", badgeClass: "unrated" };
}

/** Inferred Vehicle Body Style for Factory Operations */
export function inferBodyType(r: {
  vehicle_name?: string | null;
  plate_number?: string | null;
  accident_type?: string | null;
}): "SUV" | "Sedan" | "Van / Commercial" | "MPV" | "Hatchback" | "Other" {
  const name = (inferVehicleName(r) || "").toLowerCase();
  const text = `${name} ${r.plate_number || ""} ${r.accident_type || ""}`.toLowerCase();

  if (text.includes("vezel") || text.includes("hr-v") || text.includes("hrv") || text.includes("cr-v") || text.includes("crv") || text.includes("rav4") || text.includes("suv") || text.includes("harrier") || text.includes("cx-5") || text.includes("atto")) {
    return "SUV";
  }
  if (text.includes("hiace") || text.includes("van") || text.includes("hino") || text.includes("truck") || text.includes("attenuator") || text.includes("lorry") || text.includes("commuter") || text.includes("commercial")) {
    return "Van / Commercial";
  }
  if (text.includes("alphard") || text.includes("vellfire") || text.includes("sienta") || text.includes("noah") || text.includes("voxy") || text.includes("serena") || text.includes("mpv") || text.includes("stepwgn")) {
    return "MPV";
  }
  if (text.includes("golf") || text.includes("jazz") || text.includes("swift") || text.includes("hatchback") || text.includes("yaris")) {
    return "Hatchback";
  }
  if (text.includes("sedan") || text.includes("altis") || text.includes("civic") || text.includes("camry")) {
    return "Sedan";
  }
  return "Other";
}

/** Inferred Vehicle Make */
export function inferVehicleMake(r: {
  vehicle_name?: string | null;
  plate_number?: string | null;
}): string {
  const name = inferVehicleName(r);
  const text = `${name} ${r.plate_number || ""}`.toLowerCase();
  if (text.includes("honda")) return "Honda";
  if (text.includes("toyota")) return "Toyota";
  if (text.includes("mercedes") || text.includes("c200") || text.includes("cla")) return "Mercedes-Benz";
  if (text.includes("bmw") || text.includes("320i")) return "BMW";
  if (text.includes("volkswagen") || text.includes("golf")) return "Volkswagen";
  if (text.includes("hyundai") || text.includes("avante")) return "Hyundai";
  if (text.includes("hino") || text.includes("truck") || text.includes("tma")) return "Commercial Fleet";
  return "Other";
}

/** Operational Stage for Car Factory Workflow */
export function getOperationalStage(r: {
  status?: string | null;
  damage_count?: number;
}): "intake" | "in_survey" | "signed" {
  const s = (r.status || "").toLowerCase();
  if (s === "signed off" || s === "certified" || s === "signed") return "signed";
  if (s === "draft" || s === "intake" || (r.damage_count === 0 && s !== "confirmed")) return "intake";
  return "in_survey";
}

/** Date filter evaluation */
export function matchesDateFilter(
  createdAtStr: string | null | undefined,
  filter: "all_time" | "today" | "yesterday" | "this_week" | "this_month"
): boolean {
  if (filter === "all_time" || !createdAtStr) return true;
  const createdMs = new Date(createdAtStr).getTime();
  if (isNaN(createdMs)) return true;
  const now = Date.now();
  const diffHours = (now - createdMs) / (1000 * 60 * 60);

  if (filter === "today") return diffHours <= 24;
  if (filter === "yesterday") return diffHours > 24 && diffHours <= 48;
  if (filter === "this_week") return diffHours <= 24 * 7;
  if (filter === "this_month") return diffHours <= 24 * 30;
  return true;
}

/** The recorded insurer, or "Insurer not recorded". Never guessed (it used
 * to default to Tokio Marine and pick others from the plate prefix). */
export function inferInsurerName(r: {
  insurer_name?: string | null;
  type?: string;
  accident_type?: string | null;
  plate_number?: string | null;
  claim_type?: string | null;
}): string {
  if (r.insurer_name && r.insurer_name.trim()) return r.insurer_name;
  return "Insurer not recorded";
}

/** The recorded workshop, or "Workshop not recorded". Pass the case's
 * workshop_assigned only -- callers used to pass the incident location,
 * which then displayed as the workshop, and an empty value was filled with a
 * guessed workshop picked from location keywords. */
export function inferWorkshopName(workshop?: string | null): string {
  if (workshop && workshop.trim()) return workshop;
  return "Workshop not recorded";
}

/** Formats an ISO timestamp to exact Singapore Time (SGT / UTC+8) */
export function formatSgtDateTime(isoString?: string | null): string {
  if (!isoString) return "—";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    return d.toLocaleString("en-SG", {
      timeZone: "Asia/Singapore",
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }) + " SGT";
  } catch {
    return isoString;
  }
}

/** Formats date only in SGT */
export function formatSgtDate(isoString?: string | null): string {
  if (!isoString) return "—";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    return d.toLocaleDateString("en-SG", {
      timeZone: "Asia/Singapore",
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  } catch {
    return isoString;
  }
}

/** Formats incident datetime description */
export function formatIncidentTime(incidentDt?: string | null, createdAt?: string | null): string {
  if (incidentDt && incidentDt.trim()) {
    // Only a timestamp that states its own offset ("...Z", "+08:00") is
    // converted. A typed wall-clock time like "2026-10-18 08:40" is already
    // Singapore time: run through new Date() it was read as the server's
    // local time (UTC) and shown 8 hours late, and differently in the
    // browser (a hydration mismatch, React #418).
    if (/T\d{2}:\d{2}.*(Z|[+-]\d{2}:?\d{2})$/.test(incidentDt.trim())) {
      return formatSgtDateTime(incidentDt);
    }
    return incidentDt;
  }
  // Not the filing date: a case filed days after the accident would show
  // the wrong date as if recorded.
  void createdAt;
  return NOT_RECORDED;
}

/** Real calculated GIA 48-Hour Inspection SLA */
export function getGiaSla(createdAt: string, isSignedOff: boolean): {
  status: "certified" | "critical" | "urgent" | "active" | "expired";
  badgeText: string;
  remainingText: string;
  pillColor: string;
} {
  if (isSignedOff) {
    return {
      status: "certified",
      badgeText: "Certified",
      remainingText: "Sign-Off Complete",
      pillColor: "var(--badge-green-text)",
    };
  }

  const createdMs = new Date(createdAt).getTime();
  if (isNaN(createdMs)) {
    return {
      status: "active",
      badgeText: "—",
      remainingText: "SLA unknown",
      pillColor: "var(--text-muted)",
    };
  }

  const elapsedHours = (Date.now() - createdMs) / (1000 * 60 * 60);
  const remainingHours = Math.round(48 - elapsedHours);

  if (remainingHours <= 0) {
    return {
      status: "expired",
      badgeText: "SLA Overdue",
      remainingText: `Overdue by ${Math.abs(remainingHours)}h`,
      pillColor: "var(--badge-red-text)",
    };
  }
  if (remainingHours <= 6) {
    return {
      status: "critical",
      badgeText: `≤${remainingHours}h Left`,
      remainingText: "Statutory Escalation",
      pillColor: "var(--badge-red-text)",
    };
  }
  if (remainingHours <= 18) {
    return {
      status: "urgent",
      badgeText: `${remainingHours}h Left`,
      remainingText: "Inspection Window",
      pillColor: "var(--badge-amber-text)",
    };
  }
  return {
    status: "active",
    badgeText: `${remainingHours}h Left`,
    remainingText: "GIA 48h Window",
    pillColor: "var(--accent-cyan)",
  };
}

/** An INDICATIVE repair cost in SGD: the recorded estimate when there is
 * one, otherwise a rough figure from the number of damaged parts, severity
 * and the factory labour tariffs. It is a planning estimate, not an
 * assessment -- wherever it is displayed it must be labelled "Est." (it used
 * to appear as "Assessed" totals). Returns 0 when no damage is recorded. */
export function getEstimatedCostSGD(r: {
  estimated_repair_cost?: string | null;
  accident_type?: string | null;
  vehicle_name?: string | null;
  plate_number?: string | null;
  severity_level?: string | null;
  damage_count?: number;
}): number {
  if (r.estimated_repair_cost) {
    const parsed = parseFloat(r.estimated_repair_cost.replace(/[^0-9.]/g, ""));
    if (!isNaN(parsed) && parsed > 0) return parsed;
  }
  if (!r.damage_count) return 0;

  // Factory dynamic parts & labor calculation
  const count = r.damage_count || 1;
  const sev = (r.severity_level || "").toLowerCase();
  
  // Factory standard parts rate & labor schedules (derived from Knowledge Base master tariffs)
  const basePartCost = sev.includes("severe") ? 1020 : sev.includes("moderate") ? 680 : 420;
  const netPartCost = basePartCost * 0.8; // 20% GIA tariff discount
  const rrLaborHours = sev.includes("severe") ? 1.8 : sev.includes("moderate") ? 1.2 : 0.6;
  const laborCost = rrLaborHours * FACTORY_LABOR_TARIFFS.bodyLaborRateHourly;
  const sprayCost = 180; // Standard single component paint allocation

  const totalPerComponent = netPartCost + laborCost + sprayCost;
  const factoryBasePrep = FACTORY_LABOR_TARIFFS.sprayBoothPerPanel; // Base booth oven cycle
  const computed = Math.round(count * totalPerComponent + factoryBasePrep);

  return computed;
}
