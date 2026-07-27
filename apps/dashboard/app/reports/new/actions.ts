"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { createReport } from "@/lib/api";
import { buildReportPayload } from "../form-payload";

export async function createReportAction(formData: FormData) {
  const { payload, tempPhotoPaths } = buildReportPayload(formData);
  const result = await createReport(payload, tempPhotoPaths);
  revalidatePath("/reports");
  redirect(`/reports/${result.id}?preview=1`);
}
