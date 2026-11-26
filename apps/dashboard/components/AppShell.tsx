"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { Menu } from "lucide-react";
import { Sidebar } from "@/components/Sidebar";
import { ThemeToggle } from "@/components/ThemeToggle";
import { UserMenu } from "@/components/UserMenu";
import { SessionGuard } from "@/components/SessionGuard";
import { CurrentUserProvider } from "@/components/CurrentUser";
import type { CurrentUser } from "@/lib/api";
import { isPublicPage } from "@/lib/publicPages";
import { SIDE_MENU_DRAWER_QUERY, SIDE_MENU_ID } from "@/lib/sideMenu";

/** Sidebar + top bar around every page except the public ones (sign-in,
 * request access, invite links, privacy notice; lib/publicPages.ts), which
 * are drawn on their own. A client component because it depends on the
 * current path. */
export function AppShell({ user, children }: { user: CurrentUser | null; children: React.ReactNode }) {
  const pathname = usePathname();
  const bare = isPublicPage(pathname);
  const mustChangePassword = Boolean(user?.must_change_password);

  // The side menu as a drawer, on narrow screens only (lib/sideMenu.ts):
  // the top bar's menu button opens it; a link, the backdrop or Esc closes
  // it. Wider screens never show the button, so this stays false there.
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement | null>(null);
  const menuWasOpen = useRef(false);
  // Any page change closes it too (a link in it, or the browser's Back).
  const [menuPath, setMenuPath] = useState(pathname);
  if (menuPath !== pathname) {
    setMenuPath(pathname);
    setMenuOpen(false);
  }

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

  // While the drawer is open: Esc closes it, the page behind it doesn't
  // scroll, and widening the window past the breakpoint (turning a tablet)
  // closes it, since the side menu is then back in its place.
  useEffect(() => {
    if (!menuOpen) return;
    const narrow = window.matchMedia(SIDE_MENU_DRAWER_QUERY);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    const onResize = () => {
      if (!narrow.matches) setMenuOpen(false);
    };
    document.addEventListener("keydown", onKey);
    narrow.addEventListener("change", onResize);
    document.body.classList.add("side-menu-open");
    return () => {
      document.removeEventListener("keydown", onKey);
      narrow.removeEventListener("change", onResize);
      document.body.classList.remove("side-menu-open");
    };
  }, [menuOpen]);

  // Focus goes back to the menu button when the drawer closes (the drawer
  // moves it in when it opens: components/Sidebar.tsx).
  useEffect(() => {
    if (menuWasOpen.current && !menuOpen) menuButtonRef.current?.focus();
    menuWasOpen.current = menuOpen;
  }, [menuOpen]);

  if (bare) return <>{children}</>;

  const closeMenu = () => setMenuOpen(false);

  return (
    <CurrentUserProvider value={user}>
      <div className="app-shell">
        <Sidebar locked={mustChangePassword} open={menuOpen} onClose={closeMenu} />
        {menuOpen && <div className="side-menu-backdrop" onClick={closeMenu} aria-hidden="true" />}

        {/* inert while the drawer is open: the page behind it can't be
            clicked or tabbed into, so focus stays in the drawer. */}
        <div className="main-column" inert={menuOpen}>
          <header className="top-bar">
            <button
              ref={menuButtonRef}
              type="button"
              className="side-menu-button"
              aria-label="Open menu"
              aria-controls={SIDE_MENU_ID}
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
            >
              <Menu aria-hidden="true" style={{ width: 20, height: 20 }} />
            </button>

            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span className="top-bar-tagline" style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", color: "var(--text-muted)", textTransform: "uppercase" }}>
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
