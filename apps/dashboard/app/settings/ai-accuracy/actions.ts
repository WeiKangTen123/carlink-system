"use server";

import { unstable_rethrow } from "next/navigation";
import { API_BASE_URL, apiError, apiFetch, getAiAccuracy, type AiAccuracy } from "@/lib/api";
import { normalizeObservationSummary, rangeQuery, type ObservationSummary } from "@/lib/observations";

/** GET /admin/ai-accuracy for the tab's Try again (admins only; contract 8
 * L). A server action so the browser never fetches the API directly
 * (CORS), the error as a value so the tab can show it -- as
 * getAiUsageAction does. */
export async function getAiAccuracyAction(): Promise<AiAccuracy | { error: string }> {
  try {
    return await getAiAccuracy();
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to load AI accuracy" };
  }
}

/** GET /admin/observations/summary for "What people changed" (step 2B;
 * admins only -- the API refuses anyone else): counts of the parts people
 * added, removed and dismissed on signed-off cases, from and to a
 * Singapore date (YYYY-MM-DD, either may be blank). The dates are checked
 * here and sent as query values only. */
export async function getObservationSummaryAction(from?: string, to?: string): Promise<ObservationSummary | { error: string }> {
  const range = rangeQuery(typeof from === "string" ? from : "", typeof to === "string" ? to : "");
  if ("error" in range) return range;
  try {
    const res = await apiFetch(`${API_BASE_URL}/admin/observations/summary${range.query}`, { cache: "no-store" }, { timeoutMs: 15000 });
    if (res.status === 404) return { error: "This server doesn't count what people changed yet." };
    if (!res.ok) throw await apiError(res, "Failed to load what people changed");
    return normalizeObservationSummary(await res.json());
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to load what people changed" };
  }
}
