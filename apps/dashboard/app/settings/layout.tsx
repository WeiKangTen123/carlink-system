import { isAdminUser, settingsUser } from "./gate";
import { SettingsTabs } from "./SettingsTabs";

/** Settings: My settings (/settings) and My prices (/settings/my-prices)
 * for everyone; Users (/settings/users), Company (/settings/company), AI
 * usage (/settings/ai-usage) and AI accuracy (/settings/ai-accuracy) for
 * admins. Everyone gets the tab bar, with only the tabs they may open.
 * Each admin page checks the role again itself before loading anything
 * (AdminOnly.tsx), since this layout isn't drawn again when moving between
 * tabs. */
export default async function SettingsLayout({ children }: { children: React.ReactNode }) {
  const me = await settingsUser();
  const admin = isAdminUser(me);

  return (
    <div className="settings-page" style={{ maxWidth: 900 }}>
      <div className="page-header" style={{ marginBottom: 12 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>Settings</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            {admin
              ? "Your own account, the people who use Carlink, and the company's services"
              : `Your profile, password and prices · signed in as ${me.email}`}
          </p>
        </div>
      </div>

      <SettingsTabs admin={admin} />

      {children}
    </div>
  );
}
