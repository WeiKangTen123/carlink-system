/** Browser-side client for the New Intake wizard (apps/bot-service/app/api/intake.py).
 *
 * Unlike the rest of lib/api.ts this is called straight from the browser,
 * not through Server Actions: a real case is 40-80 photos uploaded a few at a
 * time in parallel, and Server Actions run one at a time with a 25MB body cap.
 * In prod API_BASE_URL is the same-origin /api path nginx proxies to FastAPI
 * (nginx allows 30MB per request; the API caps a photo at 25MB); in local
 * dev it's the API port, which CORS allows.
 * apiFetch sends the sign-in cookie, and if the session has ended mid-intake
 * it tells SessionGuard, which offers to sign in again without leaving the
 * page -- everything on screen is kept and the failed step can be retried. */
import {
  AI_OFF_CODES,
  API_BASE_URL,
  apiFetch,
  refusalMessage,
  type CasePermissions,
  type DamageSummaryItem,
  type Owner,
  type ReportData,
} from "./api";

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
  /** The one damaged part the sorter says this photo shows best (a
   * canonical part name), or null. Set by sorting only; regrouping a photo
   * doesn't change it. */
  part: string | null;
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
  /** When the current stage began (ISO with offset), for elapsed time and
   * the estimate of time left. Null on drafts from before it was recorded. */
  stage_started_at: string | null;
  error: string | null;
  /** Why the last run stopped, when the AI was refused for the person who
   * started it (one of AI_OFF_CODES in lib/api.ts); null otherwise, and
   * cleared on the next start. Absent from an API before release P4. */
  error_code?: string | null;
  /** Who started the run that stopped, and whether that was the signed-in
   * user. The AI runs on the key of whoever presses Start or Retry, so a
   * stop because of someone else's key is theirs to fix, not the viewer's.
   * Absent from an API before they were added: read as the viewer's own. */
  error_by?: { id: string; name: string; username: string } | null;
  error_mine?: boolean;
  reporter_notes: string;
  read_photo_ids: string[];
  photos: IntakePhoto[];
  data: ReportData;
  limits: { max_photos: number; max_photo_bytes: number };
  /** Null when the draft is Unassigned. */
  owner: Owner | null;
  /** What the signed-in user may do with this draft; the API checks again. */
  permissions: CasePermissions;
};

export const ALLOWED_PHOTO_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp", ".heic"];

/** A failed intake API call. `status` is null when no answer came back at
 * all (offline, connection dropped, or the request timed out). `code` is the
 * API's error code (AI_OFF_CODES, draft_limit, case_limit, disk_full, not_allowed...),
 * when it sent one; the message is then already in plain words. */
export class IntakeRequestError extends Error {
  readonly status: number | null;
  readonly code: string | null;
  readonly timedOut: boolean;
  /** How long the server asked us to wait (Retry-After), in ms. */
  readonly retryAfterMs: number | null;

  constructor(
    message: string,
    status: number | null,
    opts: { timedOut?: boolean; retryAfterMs?: number | null; code?: string | null } = {}
  ) {
    super(message);
    this.name = "IntakeRequestError";
    this.status = status;
    this.code = opts.code ?? null;
    this.timedOut = Boolean(opts.timedOut);
    this.retryAfterMs = opts.retryAfterMs ?? null;
  }
}

// Worth sending again unchanged: the request was fine, the connection or the
// server wasn't (nginx answers 502/504 while the API restarts).
const TRANSIENT_STATUSES = new Set([408, 429, 500, 502, 503, 504]);
// Sending the same request again can't help: an unreadable or unsupported
// file, one over the size cap, a full or already-finalized case.
const PERMANENT_STATUSES = new Set([400, 404, 409, 413, 415, 422]);
// Refusals that sending again won't fix, whatever their status (disk_full
// is 507, not_allowed 403).
const PERMANENT_CODES = new Set<string>([...AI_OFF_CODES, "draft_limit", "case_limit", "disk_full", "not_allowed"]);

/** No answer, or an answer that says "try again later". */
export function isTransientFailure(e: unknown): boolean {
  return e instanceof IntakeRequestError && (e.status === null || TRANSIENT_STATUSES.has(e.status));
}

/** The server refused the request itself; retrying it won't change that. */
export function isPermanentFailure(e: unknown): boolean {
  if (!(e instanceof IntakeRequestError)) return false;
  return (e.code !== null && PERMANENT_CODES.has(e.code)) || (e.status !== null && PERMANENT_STATUSES.has(e.status));
}

/** A few words for a failed upload's tile; the full message goes in its tooltip. */
export function uploadFailureReason(e: unknown): string {
  if (!(e instanceof IntakeRequestError)) return "Upload failed";
  if (e.timedOut) return "Timed out";
  if (e.code === "disk_full") return "Server full";
  if (e.code === "not_allowed") return "Not allowed";
  switch (e.status) {
    case null:
      return "No connection";
    case 400:
      return /unsupported/i.test(e.message) ? "Unsupported type" : "Not a readable photo";
    case 401:
    case 403:
      return "Signed out";
    case 404:
      return "Draft not found";
    case 408:
    case 504:
      return "Timed out";
    case 409:
      if (/limit|at most/i.test(e.message)) return "Case is full";
      return /being turned/i.test(e.message) ? "Case is being created" : "Case already created";
    case 413:
      return "Too large";
    case 415:
      return "Unsupported type";
    case 429:
      return "Server busy";
    case 502:
    case 503:
      return "Server unavailable";
    default:
      return e.status >= 500 ? "Server error" : `Failed (${e.status})`;
  }
}

/** Plain words for an error page with no JSON detail (nginx's own pages). */
function statusMessage(res: Response): string {
  switch (res.status) {
    case 408:
    case 504:
      return `The server took too long to answer (${res.status})`;
    case 413:
      return "Too large for the server (413)";
    case 429:
      return "Too many requests at once -- the server asked to slow down (429)";
    case 502:
    case 503:
      return `The server is restarting or unavailable (${res.status}). Try again in a moment`;
    default:
      return `Request failed (${res.status} ${res.statusText})`;
  }
}

function retryAfterMs(res: Response): number | null {
  const value = res.headers.get("Retry-After");
  if (!value) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);
  const at = Date.parse(value);
  return Number.isNaN(at) ? null : Math.max(0, at - Date.now());
}

/** timeoutMs: give up (and throw a timed-out IntakeRequestError) if no
 * answer has started arriving by then. No limit if unset. */
async function call<T>(path: string, init: RequestInit = {}, opts: { timeoutMs?: number } = {}): Promise<T> {
  const controller = opts.timeoutMs ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), opts.timeoutMs) : undefined;
  let res: Response;
  try {
    res = await apiFetch(`${API_BASE_URL}/intake${path}`, {
      cache: "no-store",
      ...init,
      ...(controller ? { signal: controller.signal } : {}),
    });
  } catch {
    if (controller?.signal.aborted)
      throw new IntakeRequestError(`No answer from the server after ${Math.round((opts.timeoutMs ?? 0) / 1000)}s`, null, { timedOut: true });
    throw new IntakeRequestError("Couldn't reach the server. Check the connection", null);
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    let detail: unknown;
    let code: string | null = null;
    try {
      const body = await res.json();
      detail = body?.detail;
      code = typeof body?.code === "string" ? body.code : null;
    } catch {
      // not JSON -- fall through to the status text
    }
    // A detail that is not a sentence (FastAPI's validation errors put a
    // list there) falls through to the status text.
    const message = refusalMessage(code, detail) || statusMessage(res);
    throw new IntakeRequestError(message, res.status, { retryAfterMs: retryAfterMs(res), code });
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

/** Whether the AI runs for the signed-in user right now (GET /auth/me
 * `ai_allowed`), asked fresh: the wizard checks again when its tab comes
 * back into view, as the person may have fixed their AI key in Settings
 * meanwhile. Null when it couldn't be told. */
export async function fetchAiAllowed(): Promise<boolean | null> {
  try {
    const res = await apiFetch(`${API_BASE_URL}/auth/me`, { cache: "no-store" }, { timeoutMs: 8000 });
    if (!res.ok) return null;
    const me = await res.json();
    return typeof me?.ai_allowed === "boolean" ? me.ai_allowed : null;
  } catch {
    return null;
  }
}

/** Waits before each automatic retry of an upload that failed transiently
 * (see isTransientFailure): three retries after the first try. */
export const UPLOAD_RETRY_DELAYS_MS = [2000, 5000, 12000];

/** Per-request upload timeout: two minutes, stretched for big files so a
 * 25MB photo on a slow phone connection (~64 KB/s) isn't cut off midway. */
function uploadTimeoutMs(bytes: number): number {
  return Math.max(120_000, Math.round(bytes / 64));
}

export function uploadIntakePhoto(
  id: string,
  file: File,
  position: number
): Promise<{ photo?: IntakePhoto; duplicate_of?: string }> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("position", String(position));
  return call(`/${id}/photos`, { method: "POST", body: form }, { timeoutMs: uploadTimeoutMs(file.size) });
}

export const updateIntakePhoto = (id: string, photoId: string, patch: { kind?: PhotoKind; excluded?: boolean }) =>
  call<{ ok: boolean }>(`/${id}/photos/${photoId}`, jsonInit("PATCH", patch));

/** keepalive: sent as the page is hidden or closed (a delete still inside
 * its undo window). */
export const deleteIntakePhoto = (id: string, photoId: string, opts: { keepalive?: boolean } = {}) =>
  call<{ deleted: string }>(`/${id}/photos/${photoId}`, { method: "DELETE", keepalive: opts.keepalive });

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
