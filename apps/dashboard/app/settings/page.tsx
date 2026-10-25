import Link from "next/link";
import { unstable_rethrow } from "next/navigation";
import { Lock } from "lucide-react";
import {
  getAppSettings,
  getCurrentUser,
  getSignupsOpen,
  getSystemInfo,
  listAccountLinks,
  listUsers,
} from "@/lib/api";
import { SettingsClient } from "./SettingsClient";

/** Null instead of an error page when one card's data can't be loaded; the
 * card says so and offers to retry. */
async function orNull<T>(load: Promise<T>): Promise<T | null> {
  try {
    return await load;
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next.
    unstable_rethrow(err);
    console.warn("Settings: couldn't load part of the page:", err);
    return null;
  }
}

export default async function SettingsPage() {
  // Settings is admin-only (the API refuses everyone else); the sidebar
  // hides the link, this covers typing the address.
  const me = await getCurrentUser();
  if (me?.role !== "admin") {
    return (
      <div className="card-glass" style={{ maxWidth: 560, padding: 28, display: "flex", gap: 14, alignItems: "flex-start" }}>
        <Lock style={{ width: 22, height: 22, color: "var(--text-muted)", flexShrink: 0 }} />
        <div>
          <h1 style={{ margin: "0 0 6px", fontSize: 18, fontWeight: 800 }}>Settings are for admins</h1>
          <p className="setup-note" style={{ margin: 0 }}>
            Ask an admin if something here needs changing. Your own name, licence and password are on{" "}
            <Link href="/account" className="setup-help-link">My account</Link>.
          </p>
        </div>
      </div>
    );
  }

  // All real, live reads: settings from the app_settings table, system info
  // measured/read from actual config at request time, users from the users
  // table. The previous version of this page displayed hardcoded values
  // (including a model chain that didn't match the real one) and three
  // invented users.
  const [settings, system, users, accountLinks, signupsOpen] = await Promise.all([
    getAppSettings(),
    getSystemInfo(),
    listUsers(),
    orNull(listAccountLinks()),
    getSignupsOpen(),
  ]);

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

  return (
    <SettingsClient
      settings={settings}
      setup={setup}
      users={users}
      accountLinks={accountLinks}
      signupsOpen={signupsOpen}
      currentUserId={me.id}
    />
  );
}
