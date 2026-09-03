"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import {
  deleteReport,
  updateReport,
  reopenReport,
  reviewDamageItem,
  signOffReport,
  analyzeReportPhotos,
  analyzeExistingReportPhotos,
  getReport,
  type PhotoAnalysisDraft,
} from "@/lib/api";
import { buildReportPayload } from "./form-payload";

export async function deleteReportAction(formData: FormData) {
  const id = formData.get("id") as string;
  if (!id) return;
  await deleteReport(id);
  revalidatePath("/reports");
  redirect("/reports");
}

/** Runs on the server so the browser never talks to the API's port
 * directly (avoids CORS) -- called imperatively from the client component,
 * not via <form action>, since the result drives React state (auto-filling
 * fields) rather than a redirect. Shared by both the New Report and Edit
 * Report forms. */
export async function analyzePhotosAction(
  formData: FormData
): Promise<{ draft: PhotoAnalysisDraft; temp_photo_paths: string[] } | { error: string }> {
  try {
    const description = (formData.get("description") as string) || "";
    const photos = formData.getAll("photos").filter((p): p is File => p instanceof File && p.size > 0);
    return await analyzeReportPhotos(description, photos);
  } catch (err) {
    return { error: err instanceof Error ? err.message : "AI analysis failed" };
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
): Promise<{ draft: PhotoAnalysisDraft } | { error: string }> {
  try {
    const report = await getReport(reportId);
    if (!report) return { error: "Report not found" };
    if (report.photo_urls.length === 0) return { error: "This report has no saved photos to analyze" };
    return await analyzeExistingReportPhotos(report.photo_urls, description);
  } catch (err) {
    return { error: err instanceof Error ? err.message : "AI analysis failed" };
  }
}

export async function updateReportAction(id: string, formData: FormData) {
  const { payload, tempPhotoPaths } = buildReportPayload(formData);
  await updateReport(id, payload, tempPhotoPaths);
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
  reviewerName: string
): Promise<{ id: string; status: string; pdf_url: string } | { error: string }> {
  try {
    const result = await signOffReport(id, reviewerName);
    revalidatePath(`/reports/${id}`);
    revalidatePath(`/reports/${id}/edit`);
    revalidatePath("/reports");
    return result;
  } catch (err) {
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
    return { error: err instanceof Error ? err.message : "Failed to update damage item" };
  }
}

export async function reopenReportAction(id: string) {
  await reopenReport(id);
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
}
