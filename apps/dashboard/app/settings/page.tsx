import { getAppSettings, getSystemInfo } from "@/lib/api";
import { SettingsClient } from "./SettingsClient";

export default async function SettingsPage() {
  // Both are real, live reads: settings from the app_settings table,
  // system info measured/read from actual config at request time. The
  // previous version of this page displayed hardcoded values (including a
  // model chain that didn't match the real one) and three invented users.
  const [settings, system] = await Promise.all([getAppSettings(), getSystemInfo()]);
  return <SettingsClient settings={settings} system={system} />;
}
