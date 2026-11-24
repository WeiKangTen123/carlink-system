"use server";

import { revalidatePath } from "next/cache";
import { unstable_rethrow } from "next/navigation";
import {
  getPartsHistory,
  deletePastReport,
  listUserPrices,
  promoteUserPrice,
  type PartsHistory,
  type PromoteBody,
  type UserPriceRow,
} from "@/lib/api";

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

/** Stage 1, admins only (the API refuses anyone else): everyone's own
 * prices, with the person's name and the shared default beside each. */
export async function getUserPricesAction(
  filters: { user?: string; make?: string; model?: string; q?: string } = {}
): Promise<{ prices: UserPriceRow[] } | { error: string }> {
  const clean: { user?: string; make?: string; model?: string; q?: string } = {};
  for (const key of ["user", "make", "model", "q"] as const) {
    const value = filters?.[key];
    if (typeof value === "string" && value.trim()) clean[key] = value.trim().slice(0, 120);
  }
  try {
    return { prices: await listUserPrices(clean) };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to load everyone's prices" };
  }
}

/** Stage 1, admins only: "Use this price for everyone" -- the person's
 * price, or the figure the admin typed, becomes the shared default for that
 * make, model and line (audited by the API). Only the fields the API takes
 * are passed on. The row comes back with its new default (null: reload). */
export async function promoteUserPriceAction(
  id: string | number,
  body: PromoteBody
): Promise<{ row: UserPriceRow | null } | { error: string }> {
  if ((typeof id !== "number" || !Number.isInteger(id)) && (typeof id !== "string" || !/^\d+$/.test(id))) {
    return { error: "That price can't be found. Reload the list." };
  }
  const clean: PromoteBody = {};
  const unit = body?.unit_price;
  if (unit !== undefined) {
    if (typeof unit !== "number" || !Number.isFinite(unit) || unit <= 0) return { error: "Type a price above 0." };
    clean.unit_price = unit;
  }
  if (typeof body?.reason === "string" && body.reason.trim()) clean.reason = body.reason.trim().slice(0, 500);
  try {
    const row = await promoteUserPrice(id, clean);
    revalidatePath("/knowledge");
    return { row };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to use the price for everyone" };
  }
}
