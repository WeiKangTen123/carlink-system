"use server";

import { revalidatePath } from "next/cache";
import { unstable_rethrow } from "next/navigation";
import {
  getPartsHistory,
  deletePastReport,
  getPriceDifferences,
  decidePriceDifference,
  type PartsHistory,
  type PriceDecision,
  type PriceDifference,
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

/** Stage 1, admins only (the API refuses anyone else): where people's own
 * prices differ from the book's by more than 10%. Counts, never names. */
export async function getPriceDifferencesAction(): Promise<{ groups: PriceDifference[] } | { error: string }> {
  try {
    return { groups: await getPriceDifferences() };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to load the price differences" };
  }
}

/** Stage 1, admins only: update the book to a price, or keep the book's.
 * Only the fields the API takes are passed on. */
export async function decidePriceDifferenceAction(body: PriceDecision): Promise<{ ok: true } | { error: string }> {
  if (!body || typeof body.make !== "string" || typeof body.item_key !== "string" || typeof body.model_key !== "string") {
    return { error: "That line can't be found. Reload the list." };
  }
  if (body.decision !== "update" && body.decision !== "keep") return { error: "Choose Update or Keep." };
  const unit = body.unit_price;
  if (body.decision === "update" && (typeof unit !== "number" || !Number.isFinite(unit) || unit <= 0)) return { error: "Type a price above 0." };
  const clean: PriceDecision = { make: body.make, model_key: body.model_key, item_key: body.item_key, decision: body.decision };
  if (body.decision === "update") clean.unit_price = unit;
  if (typeof body.reason === "string" && body.reason.trim()) clean.reason = body.reason.trim().slice(0, 500);
  try {
    await decidePriceDifference(clean);
    revalidatePath("/knowledge");
    return { ok: true };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to save the decision" };
  }
}
