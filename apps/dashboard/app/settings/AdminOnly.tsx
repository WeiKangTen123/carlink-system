import Link from "next/link";
import { Lock } from "lucide-react";

/** What a non-admin sees on /settings/users or /settings/company. The page
 * checks the role on the server and shows this INSTEAD of loading anything
 * (the API would refuse them anyway). */
export function AdminOnly() {
  return (
    <div className="card-glass settings-lock-card">
      <div className="settings-lock-icon">
        <Lock style={{ width: 20, height: 20 }} />
      </div>
      <div style={{ minWidth: 0 }}>
        <h2 className="settings-lock-title">Only admins can open this part of Settings</h2>
        <p className="setup-note" style={{ margin: "0 0 14px" }}>
          Ask an admin if something here needs changing. Your own profile and password are on My settings.
        </p>
        <Link href="/settings" prefetch={false} className="btn-primary-modern settings-lock-link">
          Go to my settings
        </Link>
      </div>
    </div>
  );
}
