"use server";

import { revalidatePath } from "next/cache";
import { unstable_rethrow } from "next/navigation";
import { getPartsHistory, deletePastReport, type PartsHistory } from "@/lib/api";

/** The parts price history from imported past reports. Importing runs
 * straight from the browser (importPastReports in lib/api.ts); these are
 * the rest. Errors are returned so the Knowledge page can show them inline. */

export async function getPartsHistoryAction(): Promise<PartsHistory | { error: string }> {
  try {
    return await getPartsHistory();
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to load the parts price history" };
  }
}

/** Admin only; the API refuses anyone else. */
export async function deletePastReportAction(ref: string): Promise<{ lines: number } | { error: string }> {
  try {
    const result = await deletePastReport(ref);
    revalidatePath("/knowledge");
    return { lines: result.lines };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to remove the report" };
  }
}
