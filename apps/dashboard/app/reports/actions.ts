"use server";

import { redirect } from "next/navigation";
import { deleteReport } from "@/lib/api";

export async function deleteReportAction(formData: FormData) {
  const id = formData.get("id") as string;
  if (!id) return;
  await deleteReport(id);
  redirect("/reports");
}
