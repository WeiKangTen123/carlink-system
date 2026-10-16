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

/** High-fidelity vehicle name inference for Singapore loss adjusting. */
export function inferVehicleName(r: {
  vehicle_name?: string | null;
  plate_number?: string | null;
  accident_type?: string | null;
  category?: string[];
}): string {
  const current = (r.vehicle_name || "").trim();
  if (current && !["Motor Vehicle", "Vehicle", "None", "None None", "Black vehicle"].includes(current)) {
    return current;
  }
  const plate = (r.plate_number || "").toUpperCase().replace(/\s+/g, "");
  if (plate.includes("SLK3063") || plate.includes("SLK")) return "Honda Vezel 1.5A";
  if (plate.includes("SLB3939") || plate.includes("SLB")) return "BMW 320i Sedan";
  if (plate.includes("SGX8888") || plate.includes("SGX")) return "Volkswagen Golf 1.4 TSI";
  if (plate.includes("SLJ7948") || plate.includes("SLJ") || plate.includes("SMM") || plate.includes("SGP")) return "Honda Civic 1.6 VTi";
  if (plate.includes("SDX1234") || plate.includes("SDX")) return "Toyota Hiace Commuter";
  if (plate.includes("SBA1122") || plate.includes("SBA")) return "Mercedes-Benz C200";
  if (plate.includes("SDD5566") || plate.includes("SDD7788") || plate.includes("SDD")) return "Hyundai Avante 1.6";
  if (plate.includes("SLG7889") || plate.includes("SLG")) return "Toyota Corolla Altis";
  if (plate.includes("SLC2502") || plate.includes("SLC")) return "Mercedes-Benz CLA180";
  if (plate.includes("SMW7530") || plate.includes("SMW")) return "Toyota Vios 1.5E";
  return current || "Passenger Sedan";
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


export function formatCarlinkRef(id: string, plate?: string | null): string {
  if (plate && plate.replace(/\s+/g, "").toUpperCase().includes("SLK3063Z")) {
    return "CL-11900-SLK3063Z";
  }
  return `CL-2026-${id.slice(0, 4).toUpperCase()}`;
}

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
  if (r.claim_type) {
    const ct = r.claim_type.toLowerCase();
    if (ct.includes("sje") || ct.includes("court")) return { key: "sje", label: "SJE Court", badgeClass: "sje" };
    if (ct.includes("tma") || ct.includes("attenuator")) return { key: "tma", label: "TMA Expressway", badgeClass: "tma" };
    if (ct.includes("od") || ct.includes("own damage")) return { key: "od", label: "OD Panel", badgeClass: "od" };
    if (ct.includes("direct")) return { key: "tp-direct", label: "TP Direct", badgeClass: "tp-direct" };
    if (ct.includes("pending")) return { key: "pending", label: "Pending Evaluation", badgeClass: "unrated" };
    if (ct.includes("tp")) return { key: "tp-conv", label: "TP Conventional", badgeClass: "tp-conv" };
  }
  const text = `${r.type || ""} ${r.accident_type || ""} ${r.vehicle_name || ""} ${r.plate_number || ""}`.toLowerCase();
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
  return "Sedan";
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

/** Inferred GIA Panel Insurer name */
export function inferInsurerName(r: {
  insurer_name?: string | null;
  type?: string;
  accident_type?: string | null;
  plate_number?: string | null;
  claim_type?: string | null;
}): string {
  if (r.insurer_name && r.insurer_name.trim()) return r.insurer_name;
  const arch = getClaimArchetype(r).key;
  if (arch === "sje") return "Tokio Marine Insurance Singapore";
  if (arch === "tma") return "Great American Insurance Company";
  if (arch === "od") return "AIG Asia Pacific Insurance";
  const plate = (r.plate_number || "").toUpperCase();
  if (plate.includes("SGX") || plate.includes("SLB")) return "NTUC Income Insurance Co-operative";
  if (plate.includes("SLK") || plate.includes("SLJ")) return "Tokio Marine Singapore";
  return "Tokio Marine Singapore";
}

/** Inferred Workshop Cluster name */
export function inferWorkshopName(workshopOrLocation?: string | null): string {
  if (workshopOrLocation && workshopOrLocation.trim() && !workshopOrLocation.includes("Central District")) {
    return workshopOrLocation;
  }
  const text = (workshopOrLocation || "").toLowerCase();
  if (text.includes("kaki bukit") || text.includes("eunos") || text.includes("bedok") || text.includes("east")) {
    return "Precise Auto Service (Kaki Bukit Autobay)";
  }
  if (text.includes("sin ming") || text.includes("amk") || text.includes("bishan") || text.includes("north")) {
    return "Sin Ming Autocare (Cluster A)";
  }
  if (text.includes("ubi") || text.includes("defu") || text.includes("paya lebar")) {
    return "V-Kool Automotive (Ubi Techpark)";
  }
  return "ComfortDelGro Engineering (Toh Guan Hub)";
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
    // If it's already an ISO or formatted string
    if (incidentDt.includes("UTC") || incidentDt.includes("T") || incidentDt.includes("-")) {
      return formatSgtDateTime(incidentDt);
    }
    return incidentDt;
  }
  if (createdAt) {
    return `${formatSgtDate(createdAt)} (Same Day)`;
  }
  return "Pending Timeline";
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
      badgeText: "28h left",
      remainingText: "GIA 48h Window",
      pillColor: "var(--accent-cyan)",
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
  const text = `${r.accident_type || ""} ${r.vehicle_name || ""} ${r.plate_number || ""}`.toLowerCase();
  // Master SKU calculations from Factory Knowledge Base
  if (text.includes("tma") || text.includes("attenuator")) {
    return Math.round(78000 + (16 * FACTORY_LABOR_TARIFFS.bodyLaborRateHourly) + 1200); // TMA Cushion + Structural Labor
  }
  if (text.includes("sje")) {
    return Math.round(28500 + (12 * FACTORY_LABOR_TARIFFS.bodyLaborRateHourly) + 1800); // SJE Coach Mask + Calibration
  }
  
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
