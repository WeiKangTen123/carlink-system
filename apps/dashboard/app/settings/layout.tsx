import { isAdminUser, settingsUser } from "./gate";
import { SettingsTabs } from "./SettingsTabs";

/** Settings: My settings (/settings, everyone), Users (/settings/users) and
 * Company (/settings/company), the last two for admins. Only admins get the
 * tab bar; everyone else just has My settings. Each admin page checks the
 * role again itself before loading anything (AdminOnly.tsx), since this
 * layout isn't drawn again when moving between tabs. */
export default async function SettingsLayout({ children }: { children: React.ReactNode }) {
  const me = await settingsUser();
  const admin = isAdminUser(me);

  return (
    <div className="settings-page" style={{ maxWidth: 900 }}>
      <div className="page-header" style={{ marginBottom: admin ? 12 : 20 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>Settings</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            {admin
              ? "Your own account, the people who use Carlink, and the company's services"
              : `Your profile and password · signed in as ${me.email}`}
          </p>
        </div>
      </div>

      {admin && <SettingsTabs />}

      {children}
    </div>
  );
}
