/** Thin client over the bot-service FastAPI backend (apps/bot-service/app/api/main.py). */

import { redirect, unstable_rethrow } from "next/navigation";
import { isPublicPage } from "./publicPages";

const isBrowser = typeof window !== "undefined";

// Server-side (Server Components / Server Actions) reach the API directly
// -- in Docker/prod that's the private docker-network hostname (api:8000,
// via API_INTERNAL_URL, see infra/docker-compose.prod.yml), avoiding a
// pointless round trip out to the public internet and back. Falls back to
// localhost:8000 for local dev (`npm run dev`, no Docker/nginx involved).
const SERVER_BASE_URL =
  process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

// Client-side (the actual browser) must never be pointed at the API's raw
// port -- that requires the port to be open to the public internet and
// breaks silently on any network that doesn't allow it through (this is
// exactly why report creation already runs through a Server Action instead
// of a direct browser fetch -- see analyzePhotosAction's comment). In prod
// these default to same-origin relative paths that infra/nginx.conf proxies
// to the API; local dev has no nginx, so NEXT_PUBLIC_API_BASE_URL /
// NEXT_PUBLIC_FILES_BASE_URL there fall back to hitting the API port
// directly, same as before.
const CLIENT_API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const CLIENT_FILES_BASE_URL = process.env.NEXT_PUBLIC_FILES_BASE_URL ?? "http://localhost:8000";

export const API_BASE_URL = isBrowser ? CLIENT_API_BASE_URL : SERVER_BASE_URL;

export function pdfDownloadUrl(reportId: string): string {
  return `${CLIENT_API_BASE_URL}/reports/${reportId}/download`;
}

/**
 * Safe fetch wrapper that aborts after a timeout (default 8000ms) to prevent infinite hangs.
 */
export async function fetchWithTimeout(
  input: RequestInfo | URL,
  init?: RequestInit,
  timeoutMs = 8000
): Promise<Response> {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  if (init?.signal) {
    if (init.signal.aborted) {
      clearTimeout(timer);
      controller.abort();
    } else {
      init.signal.addEventListener("abort", () => {
        clearTimeout(timer);
        controller.abort();
      });
    }
  }

  try {
    const res = await fetch(input, {
      ...init,
      signal: controller.signal,
    });
    return res;
  } catch (err: unknown) {
    if (timedOut) {
      const urlStr =
        typeof input === "string"
          ? input
          : input instanceof URL
          ? input.toString()
          : (input as Request).url || "endpoint";
      throw new Error(`Request timed out after ${timeoutMs}ms: ${urlStr}`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// Sign-in. Every API route needs a signed-in user (bot-service app/auth).
// The browser sends the session cookie itself; server components and server
// actions call the API directly over the Docker network, so they forward the
// cookie of the request they are handling.

/** Set by POST /auth/login (apps/bot-service/app/auth/core.py): the
 * "__Host-" name over HTTPS, the bare one where AUTH_COOKIE_SECURE=false.
 * The API reads only the one it sets; both are passed on. */
export const SESSION_COOKIES = ["__Host-carlink_session", "carlink_session"] as const;

/** Fired in the browser when the API says the session has ended, so the
 * page can offer to sign in again without losing what is on screen
 * (components/SessionGuard.tsx). */
export const SIGNED_OUT_EVENT = "carlink:signed-out";

type ApiFetchOptions = {
  /** Abort after this long (see fetchWithTimeout); no limit if unset. */
  timeoutMs?: number;
  /** Server side, a signed-out response normally redirects to /login. With
   * "return" the response is handed back instead (used by getCurrentUser,
   * which also runs on the login page itself). */
  onSignedOut?: "redirect" | "return";
};

async function forwardedCookie(): Promise<string | null> {
  // Imported lazily: this module is bundled for the browser too, where
  // next/headers doesn't exist and is never reached.
  const { cookies } = await import("next/headers");
  const jar = await cookies();
  const pairs = SESSION_COOKIES.flatMap((name) => {
    const value = jar.get(name)?.value;
    return value ? [`${name}=${value}`] : [];
  });
  return pairs.length ? pairs.join("; ") : null;
}

async function loginPath(): Promise<string> {
  // Set by proxy.ts on every page request.
  const { headers } = await import("next/headers");
  const here = (await headers()).get("x-carlink-path");
  // Never back to a public page (the sign-in or sign-up pages themselves).
  return here && here.startsWith("/") && !here.startsWith("//") && !isPublicPage(here)
    ? `/login?next=${encodeURIComponent(here)}`
    : "/login";
}

/** fetch() for API calls: carries the sign-in, and handles it ending. */
export async function apiFetch(input: string, init: RequestInit = {}, opts: ApiFetchOptions = {}): Promise<Response> {
  let finalInit: RequestInit;
  if (isBrowser) {
    // "include", not the default "same-origin": local dev serves the API
    // from another port. In production it is same-origin either way.
    finalInit = { credentials: "include", ...init };
  } else {
    const headers = new Headers(init.headers);
    const cookie = await forwardedCookie();
    if (cookie) headers.set("Cookie", cookie);
    finalInit = { ...init, headers };
  }
  const res = opts.timeoutMs ? await fetchWithTimeout(input, finalInit, opts.timeoutMs) : await fetch(input, finalInit);
  if (res.status === 401 || res.status === 403) await handleSignInProblem(res, opts);
  return res;
}

async function handleSignInProblem(res: Response, opts: ApiFetchOptions): Promise<void> {
  let reason: "signed_out" | "password_change_required" | null = null;
  if (res.status === 401) {
    reason = "signed_out";
  } else {
    const body = await res.clone().json().catch(() => null);
    if (body?.code === "password_change_required") reason = "password_change_required";
  }
  if (!reason) return;
  if (isBrowser) {
    window.dispatchEvent(new CustomEvent(SIGNED_OUT_EVENT, { detail: reason }));
    return;
  }
  if (opts.onSignedOut === "return") return;
  redirect(reason === "password_change_required" ? "/account" : await loginPath());
}

export type UserRole = "admin" | "surveyor";

export type CurrentUser = {
  id: string;
  email: string;
  name: string;
  role: UserRole;
  must_change_password: boolean;
  qualifications: string;
  license_number: string;
  firm_name: string;
  /** Requests from the public /signup page waiting for a decision. Sent
   * to admins only (GET /auth/me); drives the badge on Settings. */
  pending_access_requests?: number;
};

export type UserAccount = CurrentUser & {
  active: boolean;
  locked: boolean;
  last_login_at: string | null;
  created_at: string;
};

/** Profile fields a user edits on their own Account page, and an admin on
 * anyone's. Printed on the PDF at sign-off. */
export type ProfileFields = {
  name?: string;
  qualifications?: string;
  license_number?: string;
  firm_name?: string;
};

async function errorDetail(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  return typeof body?.detail === "string" ? body.detail : `${fallback} (${res.status})`;
}

/** The signed-in user, or null when there is none. Never redirects. */
export async function getCurrentUser(): Promise<CurrentUser | null> {
  try {
    const res = await apiFetch(`${API_BASE_URL}/auth/me`, { cache: "no-store" }, { timeoutMs: 8000, onSignedOut: "return" });
    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
    unstable_rethrow(err);
    console.warn("Failed to load the signed-in user:", err);
    return null;
  }
}

export async function updateMyProfile(patch: ProfileFields): Promise<CurrentUser> {
  const res = await apiFetch(`${API_BASE_URL}/auth/me`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(await errorDetail(res, "Failed to save your profile"));
  return res.json();
}

export async function changeMyPassword(currentPassword: string, newPassword: string): Promise<void> {
  const res = await apiFetch(`${API_BASE_URL}/auth/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
  if (!res.ok) throw new Error(await errorDetail(res, "Failed to change your password"));
}

export async function listUsers(): Promise<UserAccount[]> {
  const res = await apiFetch(`${API_BASE_URL}/users`, { cache: "no-store" }, { timeoutMs: 8000 });
  if (!res.ok) throw new Error(await errorDetail(res, "Failed to load users"));
  return res.json();
}

export async function createUser(
  fields: ProfileFields & { email: string; name: string; role: "admin" | "surveyor" }
): Promise<{ user: UserAccount; temporary_password: string }> {
  const res = await apiFetch(`${API_BASE_URL}/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
  if (!res.ok) throw new Error(await errorDetail(res, "Failed to add the user"));
  return res.json();
}

export async function updateUser(
  id: string,
  patch: ProfileFields & { email?: string; role?: "admin" | "surveyor"; active?: boolean }
): Promise<UserAccount> {
  const res = await apiFetch(`${API_BASE_URL}/users/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(await errorDetail(res, "Failed to save the user"));
  return res.json();
}

// ---------------------------------------------------------------------------
// Access requests and one-time links (admin only). Someone asks for an
// account on the public /signup page; an admin approves it, which makes an
// invite link to send them by WhatsApp. A "reset" link sets a new password
// on an existing account. The public half (sending a request, opening a
// link) runs straight from the browser: app/signup, app/invite.

export type AccessRequestRow = {
  id: string;
  reference: string;
  name: string;
  email: string;
  phone: string;
  organisation: string;
  requested_role: UserRole;
  note: string;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  decided_at: string | null;
  decided_by_name: string;
  decision_note: string;
  /** The email already belongs to an account. */
  has_account: boolean;
  /** Other pending requests with the same email. */
  same_email_pending: number;
  /** Other requests from the same IP address in the last 24 hours. */
  same_ip_24h: number;
};

export type AccountLinkRow = {
  id: string;
  purpose: "invite" | "reset";
  email: string;
  role: UserRole;
  name: string;
  created_by_name: string;
  created_at: string;
  expires_at: string;
  used_at: string | null;
  revoked_at: string | null;
  status: "open" | "used" | "expired" | "revoked";
};

/** A link just made. The token is returned this once only; the API keeps
 * just its hash. */
export type IssuedLink = { link: AccountLinkRow; token: string };

/** Hours a new link stays usable. */
export type LinkLifetime = 24 | 72 | 168;

export async function listAccessRequests(status: "pending" | "all" = "pending"): Promise<AccessRequestRow[]> {
  const res = await apiFetch(`${API_BASE_URL}/access-requests?status=${status}`, { cache: "no-store" }, { timeoutMs: 8000 });
  if (!res.ok) throw new Error(await errorDetail(res, "Failed to load access requests"));
  return res.json();
}

export async function approveAccessRequest(
  id: string,
  role: UserRole,
  expiresHours: LinkLifetime
): Promise<IssuedLink & { request: AccessRequestRow }> {
  const res = await apiFetch(`${API_BASE_URL}/access-requests/${id}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, expires_hours: expiresHours }),
  });
  if (!res.ok) throw new Error(await errorDetail(res, "Failed to approve the request"));
  return res.json();
}

export async function rejectAccessRequest(id: string, note: string): Promise<AccessRequestRow> {
  const res = await apiFetch(`${API_BASE_URL}/access-requests/${id}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(note ? { note } : {}),
  });
  if (!res.ok) throw new Error(await errorDetail(res, "Failed to reject the request"));
  return res.json();
}

/** Erases the request for good (someone asking for their details to be
 * removed, under the PDPA). */
export async function deleteAccessRequest(id: string): Promise<void> {
  const res = await apiFetch(`${API_BASE_URL}/access-requests/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await errorDetail(res, "Failed to delete the request"));
}

/** Newest first. */
export async function listAccountLinks(): Promise<AccountLinkRow[]> {
  const res = await apiFetch(`${API_BASE_URL}/account-links`, { cache: "no-store" }, { timeoutMs: 8000 });
  if (!res.ok) throw new Error(await errorDetail(res, "Failed to load links"));
  return res.json();
}

/** An invite link made directly, without a request. */
export async function createAccountLink(fields: {
  email: string;
  role: UserRole;
  name?: string;
  expires_hours?: LinkLifetime;
}): Promise<IssuedLink> {
  const res = await apiFetch(`${API_BASE_URL}/account-links`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
  if (!res.ok) throw new Error(await errorDetail(res, "Failed to create the link"));
  return res.json();
}

export async function revokeAccountLink(id: string): Promise<AccountLinkRow> {
  const res = await apiFetch(`${API_BASE_URL}/account-links/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await errorDetail(res, "Failed to cancel the link"));
  return res.json();
}

/** A one-time link for the user to choose a new password ("Reset
 * password" in Settings). */
export async function createResetLink(userId: string, expiresHours: LinkLifetime = 24): Promise<IssuedLink> {
  const res = await apiFetch(`${API_BASE_URL}/users/${userId}/reset-link`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expires_hours: expiresHours }),
  });
  if (!res.ok) throw new Error(await errorDetail(res, "Failed to create the reset link"));
  return res.json();
}

export const CATEGORY_OPTIONS = [
  "Unauthorized Access",
  "Theft or Burglary",
  "Vandalism or Property Damage",
  "Assault or Threat",
  "Harassment",
  "Cybersecurity Breach",
  "Vehicle Collision or Damage",
  "Other",
] as const;

// Kept in sync with the accident_type description in
// apps/bot-service/app/reports/schema.py -- the AI is prompted with that
// exact vocabulary, so a mismatch here means its answer silently fails to
// match any <option> and the dropdown just looks empty.
export const ACCIDENT_TYPE_OPTIONS = [
  "Collision with another vehicle",
  "Rear-end collision",
  "Side impact",
  "Front impact",
  "Parked vehicle hit",
  "Single vehicle accident",
  "Hit-and-run",
  "Scrape / minor contact",
  "Flood / weather damage",
  "Theft / vandalism damage",
  "Other",
] as const;

// Kept in sync with schema.py's field descriptions the same way as
// ACCIDENT_TYPE_OPTIONS above.
export const WEATHER_OPTIONS = ["Clear", "Rainy", "Night/Dark", "Foggy", "Wet Surface"] as const;
export const ROAD_OPTIONS = ["Dry", "Wet", "Slippery", "Gravel", "Uneven"] as const;
export const TRAFFIC_OPTIONS = ["Light", "Moderate", "Heavy", "Stationed"] as const;
export const CLAIM_TYPE_OPTIONS = [
  "Own damage",
  "Third party",
  "Third party fire and theft",
  "Comprehensive",
  "Special case",
] as const;

export type DamageSummaryItem = {
  part: string;
  damage_type?: string | null;
  severity?: string | null;
  photo_reference?: string | null;
  ai_confidence?: string | null;
  human_verified?: boolean;
  repair_required?: boolean;
  oem_part_number?: string | null;
  /** Gemini's native bounding-box convention: [y_min, x_min, y_max, x_max],
   * each normalized 0-1000 with a top-left origin. See fileUrl-adjacent
   * bboxToCss() in StudioApp.tsx for the conversion to CSS percentages. */
  bbox_2d?: number[] | null;
};

export type TyreItem = {
  tread_depth_mm?: number | null;
  brand?: string | null;
  size?: string | null;
  condition?: string | null;
};

export type TyreCondition = {
  front_nearside?: TyreItem | null;
  front_offside?: TyreItem | null;
  rear_nearside?: TyreItem | null;
  rear_offside?: TyreItem | null;
};

export type ThirdPartyInfo = {
  plate_number?: string | null;
  make_model?: string | null;
  driver_name?: string | null;
  driver_nric?: string | null;
  driver_contact?: string | null;
  insurer_name?: string | null;
  policy_number?: string | null;
  damage_description?: string | null;
};

export type VehicleInfo = {
  plate_number?: string | null;
  make?: string | null;
  model?: string | null;
  year?: string | null;
  color?: string | null;
  vin?: string | null;
  engine_number?: string | null;
  ownership_type?: string | null;
  driver_name?: string | null;
  driver_contact?: string | null;
  /** Sedan / SUV / Van / ... -- from the vehicle catalogue in taxonomy.py.
   * Absent on reports filed before the catalogue existed. */
  body_type?: string | null;
  odometer_reading?: string | null;
  transmission?: string | null;
  date_of_registration?: string | null;
  paintwork_condition?: string | null;
  steering_condition?: string | null;
  brakes_condition?: string | null;
  tyres?: TyreCondition | null;
  point_of_impact?: string | null;
};

export type PoliceReportDetails = {
  reported_to_police?: boolean;
  police_station?: string | null;
  report_number?: string | null;
  officer_name?: string | null;
  date_reported?: string | null;
  reference_number?: string | null;
};

export type InsuranceDetails = {
  insurer_name?: string | null;
  policy_number?: string | null;
  claim_number?: string | null;
  claim_type?: string | null;
  claim_status?: string | null;
  adjuster_assigned?: string | null;
  workshop_assigned?: string | null;
  estimated_repair_cost?: string | null;
  final_approved_cost?: string | null;
};

export type TimelineEvent = {
  time: string;
  event: string;
};

export type RecommendationsInfo = {
  repair_recommendation?: string | null;
  replacement_recommendation?: string | null;
  inspection_recommendation?: string | null;
  disassembly_required?: boolean;
  follow_up_action?: string | null;
  preventive_action?: string | null;
};

export type AnnexAPartItem = {
  item_no: string;
  part_name: string;
  condition: string;
  action: string;
  qty: number;
  workshop_est: number;
  discount_pct: number;
  adjusted_cost: number;
  is_net_item: boolean;
  oem_part_number?: string | null;
};

export type AnnexAInfo = {
  items: AnnexAPartItem[];
  total_workshop_est: number;
  total_adjusted_cost: number;
};

export type AnnexBLabourItem = {
  item_no: string;
  description: string;
  workshop_est: number;
  adjusted_cost: number;
  justification?: string | null;
};

export type AnnexBInfo = {
  items: AnnexBLabourItem[];
  total_workshop_est: number;
  total_adjusted_cost: number;
};

export type AnnexCInfo = {
  workshop_total: number;
  adjusted_total: number;
  agreed_lump_sum?: number | null;
  gst_rate: number;
  gst_amount?: number | null;
  total_with_gst?: number | null;
  repair_days?: number | null;
  terms?: string | null;
  without_prejudice: boolean;
};

export type BolaLiabilityAssessment = {
  scenario_number?: number | null;
  scenario_name?: string | null;
  /** Null until the surveyor records a liability finding. */
  insured_liability_pct: number | null;
  third_party_liability_pct: number | null;
  dispute_status: string;
  apportionment_rationale?: string | null;
  subrogation_prospect?: string | null;
};

/** The signer's name, qualifications, licence and firm aren't here: the API
 * takes them from the signed-in user's profile. */
export type SignOffRequest = {
  signature_data_url?: string;
  agreed_quantum?: number;
  turnaround_days?: number;
  liability_opinion?: string;
  remarks?: string;
  terms_accepted?: boolean;
};

export type SignOffInfo = {
  prepared_by?: string | null;
  reviewed_by?: string | null;
  approved_by?: string | null;
  surveyor_name?: string | null;
  qualifications?: string | null;
  license_number?: string | null;
  firm_name?: string | null;
  signature_hash?: string | null;
  signature_data_url?: string | null;
  agreed_quantum?: number | null;
  turnaround_days?: number | null;
  liability_opinion?: string | null;
  remarks?: string | null;
  status?: string;
  signature_date?: string | null;
  terms_accepted?: boolean;
  /** The account that signed (absent on sign-offs from before sign-in). */
  signed_by_user_id?: string | null;
};

export type AIAnalysisInfo = {
  summary?: string | null;
  detected_parts?: string[];
  detected_severity?: string | null;
  confidence_score?: string | null;
  suggested_category?: string | null;
  suggested_repair_notes?: string | null;
};

export type PersonInvolved = {
  name: string;
  role: string;
  department?: string | null;
  contact?: string | null;
};

export type Witness = {
  name: string;
  contact?: string | null;
  statement?: string | null;
  observation_time?: string | null;
};

export type ReportSummary = {
  id: string;
  type: string;
  status: string;
  channel: string;
  created_at: string;
  location: string | null;
  category: string[];
  thumbnail_url: string | null;
  plate_number: string | null;
  vehicle_name: string | null;
  severity_level: string | null;
  damage_count: number;
  accident_type?: string | null;
  estimated_repair_cost?: string | null;
  final_approved_cost?: string | null;
  insurer_name?: string | null;
  workshop_assigned?: string | null;
  claim_type?: string | null;
  disassembly_required?: boolean;
  reported_to_police?: boolean;
  incident_datetime?: string | null;
};

export type ReportData = {
  report_id?: string | null;
  company_name?: string | null;
  reporter_name?: string | null;
  reporter_role?: string | null;
  reporter_contact?: string | null;
  reporter_email?: string | null;
  reporter_department?: string | null;
  incident_datetime?: string | null;
  location?: string | null;
  weather_condition?: string | null;
  road_condition?: string | null;
  traffic_condition?: string | null;
  category: string[];
  accident_type?: string | null;
  damaged_parts?: string[];
  severity_level?: string | null;
  vehicle_details?: string | null;
  vehicle_info?: VehicleInfo | null;
  damage_summary?: DamageSummaryItem[];
  description: string;
  people_involved: PersonInvolved[];
  witnesses: Witness[];
  immediate_actions?: string | null;
  police_report?: PoliceReportDetails | null;
  insurance_details?: InsuranceDetails | null;
  third_party_info?: ThirdPartyInfo | null;
  ai_analysis?: AIAnalysisInfo | null;
  timeline?: TimelineEvent[];
  recommendations?: RecommendationsInfo | null;
  sign_off?: SignOffInfo | null;
  annex_a?: AnnexAInfo | null;
  annex_b?: AnnexBInfo | null;
  annex_c?: AnnexCInfo | null;
  bola_assessment?: BolaLiabilityAssessment | null;
  reported_to_authorities: boolean;
  authority_reference?: string | null;
  preventive_measures?: string | null;
  additional_comments?: string | null;
};

export type ReportDetail = {
  id: string;
  type: string;
  status: string;
  channel: string;
  created_at: string;
  data: ReportData;
  photo_urls: string[];
  /** Small versions of photo_urls, same order and length. The inspector
   * still shows the full image for the photo being examined; these are for
   * the thumbnail strip. Optional so an older API response still parses. */
  photo_thumb_urls?: string[];
  /** High-efficiency 1600px WebP versions of photo_urls. Used for
   * forensic inspection (1-Up HUD, matrix) to avoid loading multi-megabyte
   * raw camera images that crash browser tabs. */
  photo_preview_urls?: string[];
  pdf_url: string | null;
  /** Who created, edited, signed off and reopened it, oldest first.
   * Optional so an older API response still parses. */
  history?: HistoryEntry[];
};

export type HistoryEntry = {
  action: "created" | "edited" | "finalized" | "signed_off" | "reopened" | string;
  /** Null for entries with no account behind them (Telegram, or before sign-in). */
  user_name: string | null;
  detail: string | null;
  at: string;
};

export type AnalyticsSummary = {
  total_incidents: number;
  pending_review: number;
  signed_off: number;
  high_severity: number;
  category_counts: Record<string, number>;
  severity_counts: Record<string, number>;
  damaged_parts_frequency: Record<string, number>;
  recent_activity: Array<{
    id: string;
    created_at: string;
    channel: string;
    status: string;
    location: string;
    reporter: string;
    category: string[];
  }>;
  /** Null until at least one report has been signed off / has a rated
   * damage item -- see get_analytics_summary in api/main.py. Never a
   * placeholder number. */
  avg_resolution_time: string | null;
  ai_confidence_avg: string | null;
};

export const EMPTY_ANALYTICS_SUMMARY: AnalyticsSummary = {
  total_incidents: 0,
  pending_review: 0,
  signed_off: 0,
  high_severity: 0,
  category_counts: {},
  severity_counts: {},
  damaged_parts_frequency: {},
  recent_activity: [],
  avg_resolution_time: null,
  ai_confidence_avg: null,
};

export async function listReports(): Promise<ReportSummary[]> {
  const res = await apiFetch(`${API_BASE_URL}/reports`, { cache: "no-store" }, { timeoutMs: 8000 });
  if (!res.ok) throw new Error(`Failed to load reports (${res.status}: ${res.statusText})`);
  return res.json();
}

export async function getReport(id: string): Promise<ReportDetail | null> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${id}`, { cache: "no-store" }, { timeoutMs: 8000 });
  // Resilient 404 handling: return null so Next.js notFound() renders the branded 404 page instead of a 500 error
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Failed to load report ${id} (${res.status}: ${res.statusText})`);
  const body = await res.json();
  if (body && typeof body === "object" && "error" in body) return null;
  return body as ReportDetail;
}

export async function createReport(payload: ReportData, tempPhotoPaths: string[] = []): Promise<{ id: string }> {
  const res = await apiFetch(`${API_BASE_URL}/reports`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ draft: payload, temp_photo_paths: tempPhotoPaths }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to create report (${res.status}): ${text}`);
  }
  return res.json();
}

/** AI-drafted subset of ReportData returned by analyzing uploaded photos --
 * matches what the Telegram bot flow already fills in from photos+text. */
export type PhotoAnalysisDraft = Pick<
  ReportData,
  "accident_type" | "severity_level" | "damaged_parts" | "category" | "description" | "damage_summary" | "vehicle_info"
>;

export async function analyzeReportPhotos(
  description: string,
  photos: File[]
): Promise<{ draft: PhotoAnalysisDraft; temp_photo_paths: string[] }> {
  const formData = new FormData();
  formData.set("description", description);
  for (const photo of photos) formData.append("photos", photo);

  const res = await apiFetch(`${API_BASE_URL}/reports/analyze-photos`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`AI analysis failed (${res.status}): ${text}`);
  }
  return res.json();
}

/** Same analysis as analyzeReportPhotos, but against photos a report already
 * has saved rather than freshly-picked files -- must run server-side (this
 * is only ever called from a server action) since it re-fetches each photo
 * from the API's /files/ route directly, which has no CORS headers and
 * fails if a browser calls it. */
export async function analyzeExistingReportPhotos(
  photoPaths: string[],
  description: string
): Promise<{ draft: PhotoAnalysisDraft }> {
  const formData = new FormData();
  formData.set("description", description);
  for (let i = 0; i < photoPaths.length; i++) {
    const path = photoPaths[i];
    const url = path.startsWith("http") ? path : `${API_BASE_URL}${path}`;
    const res = await apiFetch(url);
    if (!res.ok) throw new Error(`Failed to fetch existing photo ${path} (${res.status})`);
    const blob = await res.blob();
    formData.append("photos", blob, `existing_${i}.jpg`);
  }

  const res = await apiFetch(`${API_BASE_URL}/reports/analyze-photos`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`AI analysis failed (${res.status}): ${text}`);
  }
  const result = await res.json();
  return { draft: result.draft };
}

export async function updateReport(
  id: string,
  payload: ReportData,
  tempPhotoPaths: string[] = []
): Promise<{ id: string; status: string; pdf_url: string }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ draft: payload, temp_photo_paths: tempPhotoPaths }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to update report (${res.status}): ${text}`);
  }
  return res.json();
}

/** Patches just the two reviewer-owned fields on one damage line item.
 * Deliberately not updateReport(): that replaces the whole report and
 * re-renders the PDF, which is far too heavy for a checkbox toggle. */
export async function reviewDamageItem(
  reportId: string,
  itemIndex: number,
  patch: { human_verified?: boolean; oem_part_number?: string }
): Promise<{ id: string; item_index: number; item: DamageSummaryItem }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${reportId}/damage-items/${itemIndex}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to update damage item (${res.status}): ${text}`);
  }
  return res.json();
}

export async function reopenReport(id: string): Promise<{ id: string; status: string }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${id}/reopen`, { method: "POST" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to reopen report (${res.status}): ${text}`);
  }
  return res.json();
}

/** Signs as the signed-in user, with their own profile (Account page) --
 * the API ignores any name, licence or firm in the request. */
export async function signOffReport(
  id: string,
  payload: SignOffRequest = {}
): Promise<{ id: string; status: string; pdf_url: string; sign_off?: SignOffInfo }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${id}/sign-off`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to sign off report (${res.status}): ${text}`);
  }
  return res.json();
}

export async function renderReportPdf(id: string): Promise<{ id: string; status: string; pdf_url: string }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${id}/render-pdf`, {
    method: "POST",
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to render PDF (${res.status}): ${text}`);
  }
  return res.json();
}

export async function getAnalyticsSummary(): Promise<AnalyticsSummary> {
  const res = await apiFetch(`${API_BASE_URL}/analytics/summary`, { cache: "no-store" }, { timeoutMs: 8000 });
  if (!res.ok) throw new Error(`Failed to load analytics (${res.status}: ${res.statusText})`);
  return res.json();
}

export type AppSettings = {
  company_name: string;
};

export const DEFAULT_APP_SETTINGS: AppSettings = {
  company_name: "Carlink Loss Adjuster",
};

/** Live read-only facts about the running deployment. Never contains the
 * API key or bot token themselves -- only whether they're configured. */
export type SystemInfo = {
  ai: {
    model_chain: string[];
    min_call_interval_seconds: number;
    request_timeout_seconds: number;
    api_key_configured: boolean;
  };
  channels: {
    telegram_configured: boolean;
    whatsapp_configured: boolean;
    reports_by_channel: Record<string, number>;
  };
  storage: { reports: number; photos: number; pdfs: number; bytes_used: number; storage_dir: string };
  database: { engine: string };
  auth: { configured: boolean };
};

export const DEFAULT_SYSTEM_INFO: SystemInfo = {
  ai: {
    model_chain: ["gemini-2.5-flash"],
    min_call_interval_seconds: 4,
    request_timeout_seconds: 30,
    api_key_configured: false,
  },
  channels: {
    telegram_configured: false,
    whatsapp_configured: false,
    reports_by_channel: {},
  },
  storage: { reports: 0, photos: 0, pdfs: 0, bytes_used: 0, storage_dir: "" },
  database: { engine: "sqlite" },
  auth: { configured: false },
};

export async function getAppSettings(): Promise<AppSettings> {
  try {
    const res = await apiFetch(`${API_BASE_URL}/settings`, { cache: "no-store" }, { timeoutMs: 8000 });
    if (!res.ok) throw new Error(`Failed to load settings (${res.status}: ${res.statusText})`);
    return await res.json();
  } catch (err) {
    unstable_rethrow(err);
    console.warn("Failed to load settings, using fallback default:", err);
    return DEFAULT_APP_SETTINGS;
  }
}

export async function updateAppSettings(patch: Partial<AppSettings>): Promise<AppSettings> {
  const res = await apiFetch(`${API_BASE_URL}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to save settings (${res.status}): ${text}`);
  }
  return res.json();
}

/** Never contains key material -- only enough to tell keys apart. */
export type LlmKey = { id: string; label: string; last4: string; created_at: string };

export async function listLlmKeys(): Promise<{ keys: LlmKey[]; env_key_configured: boolean }> {
  try {
    const res = await apiFetch(`${API_BASE_URL}/setup/llm-keys`, { cache: "no-store" }, { timeoutMs: 8000 });
    if (!res.ok) throw new Error(`Failed to load API keys (${res.status}: ${res.statusText})`);
    return await res.json();
  } catch (err) {
    unstable_rethrow(err);
    console.warn("Failed to load API keys, using fallback default:", err);
    return { keys: [], env_key_configured: false };
  }
}

export async function addLlmKey(apiKey: string, label?: string): Promise<LlmKey> {
  const res = await apiFetch(`${API_BASE_URL}/setup/llm-keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apiKey, label }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Failed to add key (${res.status})`);
  }
  return res.json();
}

export async function deleteLlmKey(id: string): Promise<void> {
  const res = await apiFetch(`${API_BASE_URL}/setup/llm-keys/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Failed to remove key (${res.status})`);
}

export async function testService(service: string): Promise<{ ok: boolean; message: string }> {
  const res = await apiFetch(`${API_BASE_URL}/setup/test/${service}`, { method: "POST" });
  if (!res.ok) throw new Error(`Test failed (${res.status})`);
  return res.json();
}

export type Taxonomy = {
  parts: string[];
  damage_types: string[];
  severities: string[];
  body_types: string[];
  model_body_types: Record<string, string>;
  usage: {
    parts: Record<string, number>;
    damage_types: Record<string, number>;
    /** Free-text names from reports filed before the vocabulary existed. */
    legacy_terms: Record<string, number>;
    reports_counted: number;
  };
};

export const DEFAULT_TAXONOMY: Taxonomy = {
  parts: [],
  damage_types: [],
  severities: [],
  body_types: [],
  model_body_types: {},
  usage: {
    parts: {},
    damage_types: {},
    legacy_terms: {},
    reports_counted: 0,
  },
};

export async function getTaxonomy(): Promise<Taxonomy> {
  try {
    const res = await apiFetch(`${API_BASE_URL}/taxonomy`, { cache: "no-store" }, { timeoutMs: 8000 });
    if (!res.ok) throw new Error(`Failed to load taxonomy (${res.status}: ${res.statusText})`);
    return await res.json();
  } catch (err) {
    unstable_rethrow(err);
    console.warn("Failed to load taxonomy, using fallback default:", err);
    return DEFAULT_TAXONOMY;
  }
}

export async function getSystemInfo(): Promise<SystemInfo> {
  try {
    const res = await apiFetch(`${API_BASE_URL}/system/info`, { cache: "no-store" }, { timeoutMs: 8000 });
    if (!res.ok) throw new Error(`Failed to load system info (${res.status}: ${res.statusText})`);
    return await res.json();
  } catch (err) {
    unstable_rethrow(err);
    console.warn("Failed to load system info, using fallback default:", err);
    return DEFAULT_SYSTEM_INFO;
  }
}

export async function deleteReport(id: string): Promise<void> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${id}`, { method: "DELETE" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to delete report (${res.status}): ${text}`);
  }
}

// Always browser-facing (an <img>/<a> src ends up in the DOM regardless of
// whether this ran during SSR or in the client) -- so this always uses the
// client-safe base, never SERVER_BASE_URL.
export function fileUrl(path: string): string {
  return path.startsWith("http") ? path : `${CLIENT_FILES_BASE_URL}${path}`;
}

export async function aiEnrichReport(id: string): Promise<{ id: string; status: string; data: ReportData }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${id}/ai-enrich`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`AI enrich failed: ${res.statusText}`);
  return res.json();
}

export async function aiEnrichAllReports(): Promise<{ status: string; enriched_count: number }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/ai-enrich-all`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Batch AI enrich failed: ${res.statusText}`);
  return res.json();
}


