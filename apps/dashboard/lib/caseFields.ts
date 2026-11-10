import type { ReportSummary } from "./api";

/** Shared derivations over report/damage data.
 *
 * These were previously copy-pasted across pages and components
 * (severityClass in 2 files, caseTitle in 2, daysOpen in 3, the
 * awaiting-sign-off status set in 2), which meant a fix in one place
 * silently left the others wrong. They live here so every surface agrees
 * by construction.
 *
 * Everything here reads a recorded value or says it is missing. The
 * helpers that used to guess (a 48h "GIA SLA" countdown, an indicative
 * repair cost from damage counts, a claim "archetype", a body type and
 * make from keywords) were deleted in Nov 2026: each put an invented
 * number or label on screens that reach insurers and courts.
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

/** Whole days since createdAt. `now` (epoch ms) is for callers that work
 * several dates out against one moment (lib/attention.ts). */
export function daysOpen(createdAt: string, now: number = Date.now()): number {
  return Math.max(0, Math.floor((now - new Date(createdAt).getTime()) / 86400000));
}

/** The system's own reference for a case, from its id and filing year.
 *
 * SEARCH TEXT ONLY -- never displayed. The firm's real file number is the
 * cover page's report_id ("CL 12330", see ourRefLabel), and a generated
 * code shown beside a plate used to pass for an issued one. This stays so
 * a "REF-2026-ABC123" someone noted from an older screen still finds the
 * case in the list's search box. */
export function formatCarlinkRef(id: string, _plate?: string | null, createdAt?: string | null): string {
  const year = createdAt ? new Date(createdAt).getFullYear() : NaN;
  const short = id.slice(0, 6).toUpperCase();
  return Number.isNaN(year) ? `REF-${short}` : `REF-${year}-${short}`;
}

/** Shown as Our Ref while the cover page has no file number. */
export const REF_TO_BE_ISSUED = "to be issued";

/** The firm's file number as recorded on the cover page ("CL 12330"), or
 * "to be issued". Never a generated code: the cover page is the only place
 * a file number comes from. */
export function ourRefLabel(reportId?: string | null): string {
  const v = (reportId || "").trim();
  return v || REF_TO_BE_ISSUED;
}

/** The recorded status, grouped the way the firm reads it: an unfinished
 * intake, a filed case waiting for a surveyor, or a signed-off one. "other"
 * is a status this code doesn't know, shown as recorded rather than
 * mapped to a guess. */
export type StatusKind = "intake" | "awaiting" | "signed" | "other";

export function statusKind(status: string): StatusKind {
  if (status === "intake") return "intake";
  if (getOperationalStage({ status }) === "signed") return "signed";
  if (isAwaitingSignOff(status)) return "awaiting";
  return "other";
}

/** The status in plain words, for a chip. */
export function statusLabel(status: string): string {
  switch (statusKind(status)) {
    case "intake":
      return "Intake draft";
    case "signed":
      return "Signed off";
    case "awaiting":
      return "Awaiting sign-off";
    default:
      return status || NOT_RECORDED;
  }
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
      // h23, not hour12:false -- Node's ICU renders midnight as "24:56"
      // under hour12:false while browsers render "00:56", which made the
      // server and browser text differ (React hydration error #418).
      hourCycle: "h23",
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
