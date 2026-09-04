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
