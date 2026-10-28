import { getAppSettings, getSystemInfo } from "@/lib/api";
import { AdminOnly } from "../AdminOnly";
import { CompanyClient } from "../CompanyClient";
import { isAdminUser, settingsUser } from "../gate";

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
  };

  return <CompanyClient settings={settings} setup={setup} />;
}
