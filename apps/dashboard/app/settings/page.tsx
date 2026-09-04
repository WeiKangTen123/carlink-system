import { getAppSettings, getSystemInfo } from "@/lib/api";
import { SettingsClient } from "./SettingsClient";

export default async function SettingsPage() {
  // Both are real, live reads: settings from the app_settings table,
  // system info measured/read from actual config at request time. The
  // previous version of this page displayed hardcoded values (including a
  // model chain that didn't match the real one) and three invented users.
  const [settings, system] = await Promise.all([getAppSettings(), getSystemInfo()]);

  // Narrowed deliberately: passing the whole system object would serialize
  // the model chain, rate-limit interval and storage paths into the page
  // payload, where they'd be readable in the HTML source even though
  // nothing renders them. Those are deployment internals an operator
  // can't act on -- this page is a setup surface, so it only receives the
  // handful of facts it actually shows.
  const setup = {
    authConfigured: system.auth.configured,
    geminiEnvKeyConfigured: system.ai.api_key_configured,
    telegramConfigured: system.channels.telegram_configured,
    whatsappConfigured: system.channels.whatsapp_configured,
    telegramReports: system.channels.reports_by_channel.telegram ?? 0,
    whatsappReports: system.channels.reports_by_channel.whatsapp ?? 0,
  };

  return <SettingsClient settings={settings} setup={setup} />;
}
