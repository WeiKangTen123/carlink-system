"use client";

import { AlertTriangle } from "lucide-react";
import type { CurrentUser } from "@/lib/api";
import { PasswordCard } from "@/app/settings/MyAccountCards";

/** The forced password change (page.tsx sends everyone else to Settings). */
export function AccountClient({ user }: { user: CurrentUser }) {
  return (
    <div style={{ maxWidth: 900 }}>
      <div className="page-header" style={{ marginBottom: 20 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>Set your password</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            Signed in as {user.email}
          </p>
        </div>
      </div>

      <div className="setup-warning-banner" style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
        <AlertTriangle style={{ width: 18, height: 18, color: "var(--badge-amber-text)", flexShrink: 0, marginTop: 2 }} />
        <div>
          <strong>Choose your own password to continue.</strong> You signed in with a temporary password from your
          admin; the rest of Carlink opens once you&apos;ve replaced it.
        </div>
      </div>

      <PasswordCard forced />
    </div>
  );
}
