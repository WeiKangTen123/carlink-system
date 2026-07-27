"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import {
  deleteReport,
  updateReport,
  reopenReport,
  analyzeReportPhotos,
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
  redirect(`/reports/${id}`);
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
