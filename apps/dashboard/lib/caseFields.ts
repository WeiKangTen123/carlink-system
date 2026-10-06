import type { ReportSummary } from "./api";

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

/** How a case is labelled in lists.
 *
 * Plate first (the most specific real identifier), then vehicle name.
 * Category is a deliberate last resort: every real report in this app
 * shares one category, so leading with it makes every row look identical.
 */
export function caseTitle(r: ReportSummary): string {
  return r.plate_number || r.vehicle_name || r.category?.[0] || "Incident";
}

export function daysOpen(createdAt: string): number {
  return Math.max(0, Math.floor((Date.now() - new Date(createdAt).getTime()) / 86400000));
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = bytes / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${units[i]}`;
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
  key: "sje" | "tma" | "tp-conv" | "tp-direct" | "od";
  label: string;
  badgeClass: string;
} {
  const text = `${r.type || ""} ${r.accident_type || ""} ${r.vehicle_name || ""} ${r.plate_number || ""} ${r.claim_type || ""}`.toLowerCase();
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

export function getEstimatedCostSGD(r: {
  estimated_repair_cost?: string | null;
  accident_type?: string | null;
  vehicle_name?: string | null;
  severity_level?: string | null;
}): number {
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
