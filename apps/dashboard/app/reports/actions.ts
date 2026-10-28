"use server";

import { redirect, unstable_rethrow } from "next/navigation";
import { revalidatePath } from "next/cache";
import {
  deleteReport,
  updateReport,
  reopenReport,
  reviewDamageItem,
  signOffReport,
  analyzeReportPhotos,
  analyzeExistingReportPhotos,
  uploadReportPhotos,
  getReport,
  getAnnexSuggestions,
  askAiAnnexSuggestions,
  saveAnnexA,
  listReports,
  listUsers,
  setCaseOwner,
  ApiError,
  errorCode,
  limitMessage,
  messageForCode,
  type AnnexAInfo,
  type AnnexAPartItem,
  type AnnexCInfo,
  type AnnexSuggestionGroup,
  type AnnexSuggestions,
  type CaseCounts,
  type Owner,
  type PhotoAnalysisDraft,
  type SignOffRequest,
  type SignOffInfo,
} from "@/lib/api";
import { buildReportPayload } from "./form-payload";

export async function deleteReportAction(formData: FormData) {
  const id = formData.get("id") as string;
  if (!id) return;
  await deleteReport(id);
  revalidatePath("/reports");
  redirect("/reports");
}

export async function getReportDetailAction(id: string) {
  try {
    return await getReport(id);
  } catch (err) {
    unstable_rethrow(err);
    return null;
  }
}

/** A failed AI call (or photo attach), with the API's error code and status
 * when it sent them -- so the screen can tell AI being off for this person
 * (AI_OFF_CODES, release P4) and a limit (409, 429: in the API's own words,
 * see limitMessage) from the AI failing, and say what to do. */
export type AiActionError = { error: string; code: string | null; status?: number | null };

function aiActionError(err: unknown, fallback: string): AiActionError {
  // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
  unstable_rethrow(err);
  return {
    error: limitMessage(err) || fallback,
    code: errorCode(err),
    status: err instanceof ApiError ? err.status : null,
  };
}

/** Runs on the server so the browser never talks to the API's port
 * directly (avoids CORS) -- called imperatively from the client component,
 * not via <form action>, since the result drives React state (auto-filling
 * fields) rather than a redirect. Shared by both the New Report and Edit
 * Report forms. */
export async function analyzePhotosAction(
  formData: FormData
): Promise<{ draft: PhotoAnalysisDraft; temp_photo_paths: string[] } | AiActionError> {
  try {
    const description = (formData.get("description") as string) || "";
    const photos = formData.getAll("photos").filter((p): p is File => p instanceof File && p.size > 0);
    return await analyzeReportPhotos(description, photos);
  } catch (err) {
    return aiActionError(err, "AI analysis failed");
  }
}

/** Lets "Re-analyze with AI" work in edit mode without the reporter having
 * to re-pick files -- looks up the report's already-saved photo paths fresh
 * (rather than trusting whatever the client passed in) and re-fetches them
 * server-side, sidestepping the CORS error a direct browser fetch of the
 * API's /files/ route hits. */
export async function reanalyzeExistingPhotosAction(
  reportId: string,
  description: string
): Promise<{ draft: PhotoAnalysisDraft } | AiActionError> {
  try {
    const report = await getReport(reportId);
    if (!report) return { error: "Report not found", code: null };
    if (report.photo_urls.length === 0) return { error: "This report has no saved photos to analyze", code: null };
    return await analyzeExistingReportPhotos(report.photo_urls, description);
  } catch (err) {
    return aiActionError(err, "AI analysis failed");
  }
}

/** "Attach photos without AI" on the edit form (release P4), for people
 * whose AI is off: the photos go to the temp dir with no analysis, and the
 * temp_photo_paths come back for the form to send on save, exactly as an
 * analysis's do. A refusal comes back with its code and message (disk_full,
 * rate_limited...). Same CORS reason as analyzePhotosAction. */
export async function attachPhotosAction(
  reportId: string,
  formData: FormData
): Promise<{ temp_photo_paths: string[] } | AiActionError> {
  try {
    const photos = formData.getAll("photos").filter((p): p is File => p instanceof File && p.size > 0);
    if (photos.length === 0) return { error: "Pick the photos to attach first.", code: null };
    return await uploadReportPhotos(reportId, photos);
  } catch (err) {
    return aiActionError(err, "Failed to attach the photos");
  }
}

/** Saves the edit form. A 409 or 429 (the case's photo limit, a case
 * signed off meanwhile, too many requests) comes back as the API's own
 * words, for the form to show where it is, with nothing typed or attached
 * lost. */
export async function updateReportAction(id: string, formData: FormData): Promise<{ error: string } | void> {
  const { payload, tempPhotoPaths } = buildReportPayload(formData);
  try {
    await updateReport(id, payload, tempPhotoPaths);
  } catch (err) {
    unstable_rethrow(err);
    if (err instanceof ApiError && (err.status === 409 || err.status === 429)) {
      return { error: limitMessage(err) || "The case couldn't be saved. Try again." };
    }
    // A refusal with a code (not_allowed, disk_full) goes back to the form
    // with its message instead of the error page.
    const code = errorCode(err);
    if (code && messageForCode(code)) redirect(`/reports/${id}/edit?error=${encodeURIComponent(code)}`);
    throw err;
  }
  // Without these, Next.js's client-side Router Cache can serve the stale
  // pre-edit RSC payload for these routes on the next visit -- e.g. if the
  // user was already on the detail page before clicking Edit, navigating
  // back to it after saving would show the old data until an unrelated
  // full refresh happened to bust the cache.
  revalidatePath(`/reports/${id}`);
  revalidatePath(`/reports/${id}/edit`);
  revalidatePath("/reports");
  // ?preview=1 auto-opens the PDF preview/download modal on arrival, so
  // saving edits and getting the file are one motion instead of two.
  redirect(`/reports/${id}?preview=1`);
}

/** Wraps signOffReport so the browser never fetches localhost:8000
 * directly -- SignOffButton is a client component and the API has no CORS
 * headers, so calling signOffReport() straight from it fails with a CORS
 * error in any real browser (confirmed via a live test: the confirm()
 * dialog fired, then the fetch was blocked and the button's own catch
 * showed "Sign-off failed: Failed to fetch"). Every other mutation in this
 * file already goes through a server action for the same reason; this one
 * had been missed. */
export async function signOffReportAction(
  id: string,
  payload: SignOffRequest = {}
): Promise<{ id: string; status: string; pdf_url: string; sign_off?: SignOffInfo } | { error: string }> {
  try {
    const result = await signOffReport(id, payload);
    revalidatePath(`/reports/${id}`);
    revalidatePath(`/reports/${id}/edit`);
    revalidatePath("/reports");
    return result;
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Sign-off failed" };
  }
}

/** Server action wrapper so the browser never fetches the API directly --
 * same CORS reason every other mutation here is a server action (see
 * signOffReportAction's note). Returns the error instead of throwing so
 * the checklist can revert its optimistic state and show what went wrong
 * (e.g. a 409 when the report is signed off and locked). */
export async function reviewDamageItemAction(
  reportId: string,
  itemIndex: number,
  patch: { human_verified?: boolean; oem_part_number?: string }
): Promise<{ ok: true } | { error: string }> {
  try {
    await reviewDamageItem(reportId, itemIndex, patch);
    revalidatePath(`/reports/${reportId}`);
    revalidatePath("/reports");
    return { ok: true };
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to update damage item" };
  }
}

/** Returns the error (e.g. 403: only the signer or an admin may reopen)
 * instead of throwing, so ReopenButton can show it. */
export async function reopenReportAction(id: string): Promise<{ ok: true } | { error: string }> {
  try {
    await reopenReport(id);
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to reopen the report" };
  }
  // Deliberately no redirect() here: the caller is already sitting on
  // /reports/{id}/edit (that's the only place this button renders), and
  // Next.js's client Router Cache does not reliably re-render a redirect
  // that targets the exact URL the browser is already on -- even with
  // revalidatePath, the "Report Locked" view kept showing after reopen.
  // revalidatePath still runs so any OTHER open tab/route gets fresh data;
  // the caller (ReopenButton) does router.refresh() to update this view,
  // same pattern SignOffButton already uses successfully.
  revalidatePath(`/reports/${id}/edit`);
  revalidatePath(`/reports/${id}`);
  revalidatePath("/reports");
  return { ok: true };
}

// Annex A: suggested lines from past reports, and saving the table. Errors
// come back as values so the Annex A tab can show them inline.

export async function getAnnexSuggestionsAction(reportId: string): Promise<AnnexSuggestions | { error: string }> {
  try {
    return await getAnnexSuggestions(reportId);
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to load suggested lines" };
  }
}

/** Spends AI quota (the surveyor's own key, or the company key) -- called
 * only when the surveyor clicks for it. A 503 (the AI is unavailable) comes
 * back as its message; AI off for them, with its code too. */
export async function askAiAnnexSuggestionsAction(
  reportId: string,
  parts: string[]
): Promise<{ groups: AnnexSuggestionGroup[] } | AiActionError> {
  try {
    return await askAiAnnexSuggestions(reportId, parts);
  } catch (err) {
    return aiActionError(err, "The AI couldn't suggest lines");
  }
}

/** Returns the saved table as the API worked it out (numbering, adjusted
 * costs, totals), or the error -- e.g. the 409 when the case is signed off. */
export async function saveAnnexAAction(
  reportId: string,
  items: AnnexAPartItem[]
): Promise<{ annex_a: AnnexAInfo; annex_c: AnnexCInfo } | { error: string }> {
  try {
    const result = await saveAnnexA(reportId, items);
    revalidatePath(`/reports/${reportId}`);
    revalidatePath(`/reports/${reportId}/edit`);
    return result;
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to save Annex A" };
  }
}

export async function aiEnrichReportAction(id: string) {
  try {
    const { aiEnrichReport } = await import("@/lib/api");
    const res = await aiEnrichReport(id);
    revalidatePath("/reports");
    revalidatePath(`/reports/${id}`);
    return { ok: true, report: res };
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to enrich report" };
  }
}

export async function aiEnrichAllReportsAction() {
  try {
    const { aiEnrichAllReports } = await import("@/lib/api");
    const res = await aiEnrichAllReports();
    revalidatePath("/reports");
    revalidatePath("/");
    return { ok: true, count: res.enriched_count };
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to batch enrich reports" };
  }
}


// Case owners (admin only -- the API refuses anyone else).

/** One person an admin can give a case to. */
export type AssignableUser = Pick<Owner, "id" | "name" | "username"> & { cases: CaseCounts | null };

/** Active accounts, for the Assign dialog (GET /users is admin only). */
export async function listAssignableUsersAction(): Promise<{ users: AssignableUser[] } | { error: string }> {
  try {
    const users = await listUsers();
    return {
      users: users
        .filter((u) => u.active)
        .map((u) => ({ id: u.id, name: u.name, username: u.username, cases: u.cases ?? null })),
    };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to load users" };
  }
}

/** Gives the case to ownerId, or makes it Unassigned (null). */
export async function setCaseOwnerAction(
  reportId: string,
  ownerId: string | null
): Promise<{ owner: Owner | null } | { error: string }> {
  try {
    const result = await setCaseOwner(reportId, ownerId);
    revalidatePath(`/reports/${reportId}`);
    revalidatePath("/reports");
    return { owner: result.owner ?? null };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to change the owner" };
  }
}

/** How many cases have no owner (for the admin's Unassigned card while the
 * list shows only their own cases). Null if it couldn't be counted. */
export async function countUnassignedAction(): Promise<number | null> {
  try {
    return (await listReports("unassigned")).length;
  } catch (err) {
    unstable_rethrow(err);
    return null;
  }
}
