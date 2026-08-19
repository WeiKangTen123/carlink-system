/** Thin client over the bot-service FastAPI backend (apps/bot-service/app/api/main.py). */

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

export type SignOffInfo = {
  prepared_by?: string | null;
  reviewed_by?: string | null;
  approved_by?: string | null;
  status?: string;
  signature_date?: string | null;
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
  ai_analysis?: AIAnalysisInfo | null;
  timeline?: TimelineEvent[];
  recommendations?: RecommendationsInfo | null;
  sign_off?: SignOffInfo | null;
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
  pdf_url: string | null;
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

export async function listReports(): Promise<ReportSummary[]> {
  const res = await fetch(`${API_BASE_URL}/reports`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load reports (${res.status})`);
  return res.json();
}

export async function getReport(id: string): Promise<ReportDetail | null> {
  const res = await fetch(`${API_BASE_URL}/reports/${id}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load report (${res.status})`);
  const body = await res.json();
  if (body && typeof body === "object" && "error" in body) return null;
  return body as ReportDetail;
}

export async function createReport(payload: ReportData, tempPhotoPaths: string[] = []): Promise<{ id: string }> {
  const res = await fetch(`${API_BASE_URL}/reports`, {
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
  "accident_type" | "severity_level" | "damaged_parts" | "category" | "description" | "damage_summary"
>;

export async function analyzeReportPhotos(
  description: string,
  photos: File[]
): Promise<{ draft: PhotoAnalysisDraft; temp_photo_paths: string[] }> {
  const formData = new FormData();
  formData.set("description", description);
  for (const photo of photos) formData.append("photos", photo);

  const res = await fetch(`${API_BASE_URL}/reports/analyze-photos`, {
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
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Failed to fetch existing photo ${path} (${res.status})`);
    const blob = await res.blob();
    formData.append("photos", blob, `existing_${i}.jpg`);
  }

  const res = await fetch(`${API_BASE_URL}/reports/analyze-photos`, {
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
  const res = await fetch(`${API_BASE_URL}/reports/${id}`, {
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

export async function reopenReport(id: string): Promise<{ id: string; status: string }> {
  const res = await fetch(`${API_BASE_URL}/reports/${id}/reopen`, { method: "POST" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to reopen report (${res.status}): ${text}`);
  }
  return res.json();
}

export async function signOffReport(id: string, reviewerName: string = "Surveyor / Loss Adjuster"): Promise<{ id: string; status: string; pdf_url: string }> {
  const res = await fetch(`${API_BASE_URL}/reports/${id}/sign-off?reviewer_name=${encodeURIComponent(reviewerName)}`, {
    method: "POST",
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to sign off report (${res.status}): ${text}`);
  }
  return res.json();
}

export async function getAnalyticsSummary(): Promise<AnalyticsSummary> {
  const res = await fetch(`${API_BASE_URL}/analytics/summary`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load analytics (${res.status})`);
  return res.json();
}

export async function deleteReport(id: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/reports/${id}`, { method: "DELETE" });
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

