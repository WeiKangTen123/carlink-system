"use server";

import { revalidatePath } from "next/cache";
import { updateAppSettings, type AppSettings } from "@/lib/api";

/** Server action so the browser never talks to the API directly -- same
 * CORS reason every other mutation in this app goes through one. */
export async function updateSettingsAction(
  patch: Partial<AppSettings>
): Promise<{ settings: AppSettings } | { error: string }> {
  try {
    const settings = await updateAppSettings(patch);
    revalidatePath("/settings");
    return { settings };
  } catch (err) {
    return { error: err instanceof Error ? err.message : "Failed to save settings" };
  }
}
