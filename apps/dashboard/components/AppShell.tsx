"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";
import { ThemeToggle } from "@/components/ThemeToggle";
import { UserMenu } from "@/components/UserMenu";
import { SessionGuard } from "@/components/SessionGuard";
import { CurrentUserProvider } from "@/components/CurrentUser";
import type { CurrentUser } from "@/lib/api";
import { isPublicPage } from "@/lib/publicPages";

/** Sidebar + top bar around every page except the public ones (sign-in,
 * request access, invite links, privacy notice; lib/publicPages.ts), which
 * are drawn on their own. A client component because it depends on the
 * current path. */
export function AppShell({ user, children }: { user: CurrentUser | null; children: React.ReactNode }) {
  const pathname = usePathname();
  const bare = isPublicPage(pathname);
  const mustChangePassword = Boolean(user?.must_change_password);

  // A temporary password has to be replaced before anything else; the API
  // refuses everything else meanwhile (app/auth/middleware.py). /account is
  // now only that forced change (everyone else is sent on to /settings).
  // The root layout already redirects every other page on the server, and
  // the shell offers no links away from /account (Sidebar, UserMenu), so
  // this is only a fallback. It's a full page load, never router.replace():
  // a client navigation racing the server's own redirect crashes Next's
  // router (React error #310) and strands the browser on the wrong page.
  useEffect(() => {
    if (!bare && mustChangePassword && pathname !== "/account") window.location.replace("/account");
  }, [bare, mustChangePassword, pathname]);

  if (bare) return <>{children}</>;

  return (
    <CurrentUserProvider value={user}>
      <div className="app-shell">
        <Sidebar locked={mustChangePassword} />

        <div className="main-column">
          <header className="top-bar">
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", color: "var(--text-muted)", textTransform: "uppercase" }}>
                Autonomous Claims & Loss Adjusting Workstation
              </span>
            </div>

            <div style={{ marginLeft: "auto", display: "flex", gap: 12, alignItems: "center" }}>
              <ThemeToggle />
              {user && <UserMenu user={user} />}
            </div>
          </header>

          <main className="main-content">{children}</main>
        </div>
      </div>
      {user && <SessionGuard user={user} />}
    </CurrentUserProvider>
  );
}
