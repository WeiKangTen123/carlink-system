import { unstable_rethrow } from "next/navigation";
import { getAiUsage, type AiUsage, type AiUsageDays } from "@/lib/api";
import { AdminOnly } from "../AdminOnly";
import { AiUsageClient } from "../AiUsageClient";
import { isAdminUser, settingsUser } from "../gate";

/** The range the tab opens on; the selector on the tab fetches the others. */
const DEFAULT_DAYS: AiUsageDays = 30;

/** Settings -> AI usage (admins; round 3 B3): the AI run log summed over
 * the last 30 days. The role is checked here, before the admin-only call:
 * everyone else gets the lock card and nothing loads. A failed load shows
 * the error on the tab with Try again, not an error page. */
export default async function AiUsageSettingsPage() {
  const me = await settingsUser();
  if (!isAdminUser(me)) return <AdminOnly />;

  let usage: AiUsage | null = null;
  let error: string | null = null;
  try {
    usage = await getAiUsage(DEFAULT_DAYS);
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next.
    unstable_rethrow(err);
    console.warn("Settings: couldn't load AI usage:", err);
    error = err instanceof Error ? err.message : "Failed to load AI usage";
  }

  return <AiUsageClient initial={usage} initialError={error} initialDays={DEFAULT_DAYS} />;
}
