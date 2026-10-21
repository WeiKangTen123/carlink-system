/** Browser-side client for the New Intake wizard (apps/bot-service/app/api/intake.py).
 *
 * Unlike the rest of lib/api.ts this is called straight from the browser,
 * not through Server Actions: a real case is 40-80 photos uploaded a few at a
 * time in parallel, and Server Actions run one at a time with a 25MB body cap.
 * In prod API_BASE_URL is the same-origin /api path nginx proxies to FastAPI
 * (100MB body limit); in local dev it's the API port, which CORS allows. */
import { API_BASE_URL, type DamageSummaryItem, type ReportData } from "./api";

export type IntakeStage = "uploading" | "sorting" | "reading" | "ready" | "failed";

/** Mirrors PHOTO_KINDS in apps/bot-service/app/ai/triage.py. */
export const PHOTO_KINDS = [
  "damage",
  "overview",
  "log_card",
  "vin_plate",
  "police_report",
  "document",
  "odometer",
  "tyre",
  "interior",
  "scene",
  "third_party",
  "other",
] as const;
export type PhotoKind = (typeof PHOTO_KINDS)[number];

export const PHOTO_KIND_LABELS: Record<PhotoKind, string> = {
  damage: "Damage close-ups",
  overview: "Overall / corners",
  log_card: "Log card",
  vin_plate: "Chassis / VIN plate",
  police_report: "Police report",
  document: "Other documents",
  odometer: "Odometer",
  tyre: "Tyres",
  interior: "Interior",
  scene: "Scene / road",
  third_party: "Third-party vehicle",
  other: "Other",
};

/** Photo kinds whose content is vehicle/claim facts rather than damage --
 * the review screen lists these as where the facts were read from. */
export const DOCUMENT_KINDS: PhotoKind[] = ["log_card", "vin_plate", "police_report", "document", "odometer"];

export type IntakePhoto = {
  id: string;
  /** P01, P02... in case order -- shifts if an earlier photo is deleted. */
  label: string;
  position: number;
  name: string;
  size_bytes: number;
  url: string;
  thumb_url: string;
  preview_url: string;
  kind: PhotoKind | null;
  area: string | null;
  quality: "good" | "blurry" | "unusable" | null;
  shows_damage: boolean;
  note: string | null;
  kind_source: "ai" | "surveyor";
  excluded: boolean;
};

export type IntakeState = {
  id: string;
  status: string;
  created_at: string;
  stage: IntakeStage;
  progress: { done: number; total: number };
  error: string | null;
  reporter_notes: string;
  read_photo_ids: string[];
  photos: IntakePhoto[];
  data: ReportData;
  limits: { max_photos: number; max_photo_bytes: number };
};

export const ALLOWED_PHOTO_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp", ".heic"];

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}/intake${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    let detail: string | undefined;
    try {
      detail = (await res.json()).detail;
    } catch {
      // not JSON -- fall through to the status text
    }
    throw new Error(detail || `Request failed (${res.status} ${res.statusText})`);
  }
  return res.json();
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const createIntake = () => call<{ id: string }>("", { method: "POST" });

export const getIntake = (id: string) => call<IntakeState>(`/${id}`);

export function uploadIntakePhoto(
  id: string,
  file: File,
  position: number
): Promise<{ photo?: IntakePhoto; duplicate_of?: string }> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("position", String(position));
  return call(`/${id}/photos`, { method: "POST", body: form });
}

export const updateIntakePhoto = (id: string, photoId: string, patch: { kind?: PhotoKind; excluded?: boolean }) =>
  call<{ ok: boolean }>(`/${id}/photos/${photoId}`, jsonInit("PATCH", patch));

export const deleteIntakePhoto = (id: string, photoId: string) =>
  call<{ deleted: string }>(`/${id}/photos/${photoId}`, { method: "DELETE" });

/** keepalive lets the save outlive the page -- used when the tab is being
 * hidden or closed (the browser caps such bodies at 64KB, ample for a form). */
export const saveIntakeData = (id: string, data: ReportData, reporterNotes?: string, opts: { keepalive?: boolean } = {}) =>
  call<{ ok: boolean }>(`/${id}/data`, {
    ...jsonInit("PUT", { data: prepareForSave(data), reporter_notes: reporterNotes }),
    keepalive: opts.keepalive,
  });

export const processIntake = (id: string, mode: "all" | "read") =>
  call<IntakeState>(`/${id}/process`, jsonInit("POST", { mode }));

export const finalizeIntake = (id: string, data: ReportData) =>
  call<{ id: string }>(`/${id}/finalize`, jsonInit("POST", { data: prepareForSave(data) }));

/** The same derivations app/reports/form-payload.ts applies to the old form,
 * so a case filed through the wizard reads identically everywhere else:
 * blank people rows dropped, police flags mirrored, vehicle_details built. */
export function prepareForSave(data: ReportData): ReportData {
  const v = data.vehicle_info || {};
  const police = data.police_report || { reported_to_police: false };
  const reported = Boolean(police.reported_to_police);
  const damage: DamageSummaryItem[] = (data.damage_summary || []).filter((d) => d.part && d.part.trim());
  return {
    ...data,
    description: data.description || "",
    category: data.category?.length ? data.category : ["Vehicle Collision or Damage"],
    people_involved: (data.people_involved || []).filter((p) => p.name && p.name.trim()),
    witnesses: (data.witnesses || []).filter((w) => w.name && w.name.trim()),
    damage_summary: damage,
    damaged_parts: damage.map((d) => d.part),
    reported_to_authorities: reported,
    authority_reference: police.report_number || null,
    police_report: { ...police, reported_to_police: reported },
    vehicle_details:
      [v.plate_number, [v.make, v.model].filter(Boolean).join(" ") || null].filter(Boolean).join(" - ") || null,
  };
}

/** Damage items point at a photo as "@<photo id>" until the case is created
 * (P-numbers shift while photos can still be added or deleted). */
export function photoForReference(ref: string | null | undefined, photos: IntakePhoto[]): IntakePhoto | undefined {
  if (!ref || !ref.startsWith("@")) return undefined;
  return photos.find((p) => p.id === ref.slice(1));
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}
