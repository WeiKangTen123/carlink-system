"use server";

import { redirect, unstable_rethrow } from "next/navigation";
import { revalidatePath } from "next/cache";
import {
  deleteReport,
  updateReport,
  reopenReport,
  patchDamageItem,
  addDamageItem,
  removeDamageItem,
  getTaxonomy,
  signOffReport,
  saveReportChecked,
  OpenItemsError,
  uploadReportPhotos,
  getReport,
  getAnnexSuggestions,
  getAiUsage,
  askAiAnnexSuggestions,
  saveAnnexA,
  prefillAnnexA,
  saveAnnexB,
  saveAnnexC,
  getLabourSuggestions,
  saveCoverPage,
  listReports,
  listUsers,
  setCaseOwner,
  ApiError,
  errorCode,
  limitMessage,
  messageForCode,
  type AnnexAInfo,
  type AnnexAPartItem,
  type AnnexBInfo,
  type AnnexBLabourItem,
  type AnnexCInfo,
  type AnnexCRequest,
  type AnnexSuggestionGroup,
  type AnnexSuggestions,
  type AiUsage,
  type AiUsageDays,
  type CaseCounts,
  type CoverPageRequest,
  type CoverPageResponse,
  type DamageItemPatch,
  type DamageItemsResponse,
  type NewDamageItem,
  type Taxonomy,
  type LabourSuggestions,
  type Owner,
  type SignOffRequest,
  type SignOffInfo,
  type CheckedRequest,
  type CheckedResponse,
} from "@/lib/api";
import type { Review } from "@/lib/review";
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

// analyzePhotosAction and reanalyzeExistingPhotosAction went on 2026-11-04:
// the AI reads photos in the intake wizard (POST /intake/{id}/process), not
// on the edit form.

/** "Attach photos" on the edit form (release P4): the photos go to the temp
 * dir with no analysis, and the temp_photo_paths come back for the form to
 * send on save. A refusal comes back with its code and message (disk_full,
 * rate_limited...). Runs on the server so the browser never talks to the
 * API's port directly (avoids CORS). */
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
): Promise<{ id: string; status: string; pdf_url: string; sign_off?: SignOffInfo } | SignOffRefusal> {
  try {
    const result = await signOffReport(id, payload);
    revalidatePath(`/reports/${id}`);
    revalidatePath(`/reports/${id}/edit`);
    revalidatePath("/reports");
    return result;
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    // Fields still open and no "Sign off with N items open" tick (409):
    // the count and the API's counts, so the box can offer the tick.
    if (err instanceof OpenItemsError) return { error: err.message, openItems: { open: err.open, counts: err.counts } };
    return { error: err instanceof Error ? err.message : "Sign-off failed" };
  }
}

/** A refused sign-off; openItems when the API refused it for open fields. */
export type SignOffRefusal = { error: string; openItems?: { open: number; counts: Review["counts"] | null } };

/** Confirms review fields, or takes a confirm back (PUT /reports/{id}/checked):
 * "No labour" on Annex B is {set: {annex_b: {na: true}}}. */
export async function saveReportCheckedAction(reportId: string, body: CheckedRequest): Promise<CheckedResponse | { error: string }> {
  try {
    const result = await saveReportChecked(reportId, body);
    revalidatePath(`/reports/${reportId}`);
    revalidatePath("/reports");
    return result;
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to save the confirm" };
  }
}

// Tab 1's detected parts (damage_summary), modelled on saveAnnexAAction:
// server actions so the browser never fetches the API directly (CORS), and
// the error as a value (a 409 once signed off) so the table can show it.
// Each answer carries the parts as saved and the Annex A the API re-synced
// to them, plus the Annex C totals.

/** Changes one detected part: its name (a taxonomy part), condition (the
 * firm's words), remarks, severity, photo, verified tick or OEM number. */
export async function patchDamageItemAction(reportId: string, itemIndex: number, patch: DamageItemPatch): Promise<DamageItemsResponse | { error: string }> {
  try {
    const result = await patchDamageItem(reportId, itemIndex, patch);
    revalidatePath(`/reports/${reportId}`);
    revalidatePath("/reports");
    return result;
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to update the part" };
  }
}

/** A part the surveyor adds by hand (appended, verified). */
export async function addDamageItemAction(reportId: string, body: NewDamageItem): Promise<DamageItemsResponse | { error: string }> {
  try {
    const result = await addDamageItem(reportId, body);
    revalidatePath(`/reports/${reportId}`);
    revalidatePath("/reports");
    return result;
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to add the part" };
  }
}

/** Removes a detected part; the API remembers it as dismissed so a later
 * detection doesn't bring it back. */
export async function removeDamageItemAction(reportId: string, itemIndex: number): Promise<DamageItemsResponse | { error: string }> {
  try {
    const result = await removeDamageItem(reportId, itemIndex);
    revalidatePath(`/reports/${reportId}`);
    revalidatePath("/reports");
    return result;
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to remove the part" };
  }
}

/** The controlled vocabulary (GET /taxonomy) for tab 1's part select, the
 * condition editor and Annex A's condition suggestions. getTaxonomy falls
 * back to an empty vocabulary (but the built-in condition lists) when the
 * API can't be reached, so the tables still render and stay editable. */
export async function getTaxonomyAction(): Promise<Taxonomy> {
  return getTaxonomy();
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

/** The AI run log summed over the last `days` days (GET /ai-usage, admins
 * only; round 3 B3), for Settings -> AI usage's days selector. Modelled on
 * getAnnexSuggestionsAction: a server action so the browser never fetches
 * the API directly (CORS), the error as a value so the tab can show it. */
export async function getAiUsageAction(days: AiUsageDays): Promise<AiUsage | { error: string }> {
  try {
    return await getAiUsage(days);
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to load AI usage" };
  }
}

/** Prices the unpriced Annex A rows from past cases and saves the table;
 * the whole table comes back with how many rows were filled and how many
 * still have no price. Same error handling as saveAnnexAAction. */
export async function prefillAnnexAAction(
  reportId: string
): Promise<{ annex_a: AnnexAInfo; annex_c: AnnexCInfo; filled: number; unpriced: number } | { error: string }> {
  try {
    const result = await prefillAnnexA(reportId);
    revalidatePath(`/reports/${reportId}`);
    revalidatePath(`/reports/${reportId}/edit`);
    return result;
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to fill prices from past cases" };
  }
}

/** Annex B as saveAnnexAAction: the saved table as the API worked it out
 * (numbering, totals, blank descriptions dropped), or the error. */
export async function saveAnnexBAction(
  reportId: string,
  items: AnnexBLabourItem[]
): Promise<{ annex_b: AnnexBInfo; annex_c: AnnexCInfo } | { error: string }> {
  try {
    const result = await saveAnnexB(reportId, items);
    revalidatePath(`/reports/${reportId}`);
    revalidatePath(`/reports/${reportId}/edit`);
    return result;
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to save Annex B" };
  }
}

/** The contract lump sum and working days of the Annex C letter. */
export async function saveAnnexCAction(reportId: string, body: AnnexCRequest): Promise<{ annex_c: AnnexCInfo } | { error: string }> {
  try {
    const result = await saveAnnexC(reportId, body);
    revalidatePath(`/reports/${reportId}`);
    revalidatePath(`/reports/${reportId}/edit`);
    return result;
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to save Annex C" };
  }
}

export async function getLabourSuggestionsAction(reportId: string): Promise<LabourSuggestions | { error: string }> {
  try {
    return await getLabourSuggestions(reportId);
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to load the usual labour lines" };
  }
}

/** The Cover Page as saved (the API merges key by key). */
export async function saveCoverPageAction(reportId: string, body: CoverPageRequest): Promise<CoverPageResponse | { error: string }> {
  try {
    const result = await saveCoverPage(reportId, body);
    revalidatePath(`/reports/${reportId}`);
    revalidatePath(`/reports/${reportId}/edit`);
    revalidatePath("/reports");
    return result;
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to save the cover page" };
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
