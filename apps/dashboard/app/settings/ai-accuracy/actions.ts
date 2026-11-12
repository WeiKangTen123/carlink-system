"use server";

import { unstable_rethrow } from "next/navigation";
import { getAiAccuracy, type AiAccuracy } from "@/lib/api";

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
