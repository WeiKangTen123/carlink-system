import { unstable_rethrow } from "next/navigation";
import { getAiAccuracy, type AiAccuracy } from "@/lib/api";
import { AdminOnly } from "../AdminOnly";
import { AiAccuracyClient } from "../AiAccuracyClient";
import { isAdminUser, settingsUser } from "../gate";

/** Settings -> AI accuracy (admins; contract 8 L): what surveyors kept,
 * removed, added or changed of the AI's drafts before signing off, per
 * month and per case. The role is checked here, before the admin-only
 * call: everyone else gets the lock card and nothing loads. A failed load
 * shows the error on the tab with Try again, not an error page. */
export default async function AiAccuracySettingsPage() {
  const me = await settingsUser();
  if (!isAdminUser(me)) return <AdminOnly />;

  let accuracy: AiAccuracy | null = null;
  let error: string | null = null;
  try {
    accuracy = await getAiAccuracy();
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next.
    unstable_rethrow(err);
    console.warn("Settings: couldn't load AI accuracy:", err);
    error = err instanceof Error ? err.message : "Failed to load AI accuracy";
  }

  return <AiAccuracyClient initial={accuracy} initialError={error} />;
}
