import { BACKUP_STALE_HOURS, getAppSettings, getSystemInfo, type LastBackup, type SystemInfo } from "@/lib/api";
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

/** Whether the last recorded backup is older than BACKUP_STALE_HOURS.
 * Worked out here on the server, so the red/green of the Backups card can't
 * differ between the server's render and the browser's (hydration). A date
 * that can't be read counts as too old: the screen can't vouch for it. */
function backupTooOld(backup: LastBackup | null | undefined): boolean {
  if (!backup) return false;
  const at = Date.parse(backup.at);
  if (Number.isNaN(at)) return true;
  return Date.now() - at > BACKUP_STALE_HOURS * 3600 * 1000;
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
    // Release P5. Null when the API doesn't say (older than P5).
    telegramBotUsername: system.telegram?.bot_username || null,
    telegramAccountsConnected:
      typeof system.telegram?.accounts_connected === "number" ? system.telegram.accounts_connected : null,
    // Round 3 B1b. Null: no backup recorded -- also when the API is older
    // than round 3 or couldn't be reached, which is equally "can't vouch
    // for a backup".
    lastBackup: system.last_backup ?? null,
    backupTooOld: backupTooOld(system.last_backup),
  };

  return <CompanyClient settings={settings} setup={setup} />;
}
