import { getAppSettings, getSystemInfo, type SystemInfo } from "@/lib/api";
import type { AiKeyStorageView } from "../AiKeyStorageStatus";
import { AdminOnly } from "../AdminOnly";
import { CompanyClient } from "../CompanyClient";
import { isAdminUser, settingsUser } from "../gate";

/** ai_key_storage as the page shows it, or null when the API didn't send
 * a usable one (older APIs don't send it at all). */
function aiKeyStorageOf(system: SystemInfo): AiKeyStorageView | null {
  const s = system.ai_key_storage;
  if (!s || typeof s.state !== "string" || !s.state) return null;
  return { state: s.state, detail: typeof s.detail === "string" && s.detail.trim() ? s.detail : null };
}

/** Settings -> Company (admins). The role is checked here, before any
 * admin-only call: everyone else gets the lock card and nothing loads. */
export default async function CompanySettingsPage() {
  const me = await settingsUser();
  if (!isAdminUser(me)) return <AdminOnly />;

  // Real, live reads: the company name from the app_settings table, system
  // info read from the running config.
  const [settings, system] = await Promise.all([getAppSettings(), getSystemInfo()]);

  // Narrowed deliberately: passing the whole system object would serialize
  // the model chain, rate-limit interval and storage paths into the page
  // payload, where they'd be readable in the HTML source even though
  // nothing renders them. Those are deployment internals an operator
  // can't act on -- this page is a setup surface, so it only receives the
  // handful of facts it actually shows.
  const setup = {
    geminiEnvKeyConfigured: system.ai.api_key_configured,
    telegramConfigured: system.channels.telegram_configured,
    whatsappConfigured: system.channels.whatsapp_configured,
    telegramReports: system.channels.reports_by_channel.telegram ?? 0,
    whatsappReports: system.channels.reports_by_channel.whatsapp ?? 0,
    aiKeyStorage: aiKeyStorageOf(system),
  };

  return <CompanyClient settings={settings} setup={setup} />;
}
