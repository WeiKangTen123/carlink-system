/** Thin client over the bot-service FastAPI backend (apps/bot-service/app/api/main.py). */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export function pdfDownloadUrl(reportId: string): string {
  return `${API_BASE_URL}/reports/${reportId}/download`;
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

export const ACCIDENT_TYPE_OPTIONS = [
  "Frontal Collision",
  "Rear-End Collision",
  "Side Impact (T-Bone)",
  "Single Vehicle Object Impact",
  "Parked Hit & Run",
] as const;

export type DamageSummaryItem = {
  part: string;
  damage_type: string;
  severity: string;
  photo_reference?: string | null;
  ai_confidence?: string | null;
  human_verified?: boolean;
  repair_required?: boolean;
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
  recent_activity: Array<{
    id: string;
    created_at: string;
    channel: string;
    status: string;
    location: string;
    reporter: string;
    category: string[];
  }>;
  avg_resolution_time: string;
  ai_confidence_avg: string;
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

export async function createReport(payload: ReportData): Promise<{ id: string }> {
  const res = await fetch(`${API_BASE_URL}/reports`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to create report (${res.status}): ${text}`);
  }
  return res.json();
}

export async function updateReport(id: string, payload: ReportData): Promise<{ id: string; status: string; pdf_url: string }> {
  const res = await fetch(`${API_BASE_URL}/reports/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to update report (${res.status}): ${text}`);
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

export function fileUrl(path: string): string {
  return path.startsWith("http") ? path : `${API_BASE_URL}${path}`;
}

