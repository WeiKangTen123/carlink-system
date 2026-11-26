"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  FolderArchive,
  BarChart3,
  BookOpen,
  Settings,
  CarFront,
  Inbox,
  X,
} from "lucide-react";
import { canViewAllCases, useCurrentUser } from "@/components/CurrentUser";
import { useInboxCount } from "@/components/inbox/useInboxCount";
import { SIDE_MENU_ID } from "@/lib/sideMenu";

// How often the Public Inbox badge asks for its count while the tab is in
// view (release P6); every route change asks too.
const INBOX_POLL_MS = 60_000;

const NAV_ITEMS = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/reports", label: "Cases", icon: FolderArchive },
  // Release P6: reports the public sends through the Telegram bot. Only for
  // people who see all cases (admins and staff ticked for it).
  { href: "/inbox", label: "Public Inbox", icon: Inbox, seeAllOnly: true },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/knowledge", label: "Knowledge Base", icon: BookOpen },
  // Everyone: My settings (profile, password) and My prices. Admins also
  // get the Users, Company and AI tabs there (app/settings/layout.tsx).
  { href: "/settings", label: "Settings", icon: Settings },
];

function isActiveLink(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/reports") {
    return pathname === "/reports" || pathname.startsWith("/reports/");
  }
  return pathname === href || pathname.startsWith(href + "/");
}

/** locked: signed in with a temporary password. Nothing opens until it's
 * replaced on /account, so there are no links away from it: following one
 * would only bounce back (see AppShell).
 *
 * open / onClose: on narrow screens the menu is a drawer over the page
 * (lib/sideMenu.ts), opened from the top bar's menu button in AppShell.
 * Open, it takes the focus and the rest of the page is inert (AppShell),
 * so it acts as a modal; any link in it, its close button, the backdrop or
 * Esc (AppShell) closes it. It stays an <aside> (role="dialog" isn't
 * allowed on one), labelled "Menu" while open. Wider screens never open
 * it, so there it stays the plain side menu. */
export function Sidebar({
  locked = false,
  open = false,
  onClose,
}: {
  locked?: boolean;
  open?: boolean;
  onClose?: () => void;
}) {
  const pathname = usePathname();
  const user = useCurrentUser();
  const seesAll = canViewAllCases(user);
  const items = locked ? [] : NAV_ITEMS.filter((item) => !item.seeAllOnly || seesAll);
  const inbox = useInboxCount(seesAll && !locked, { pollMs: INBOX_POLL_MS, refreshKey: pathname });
  const closeRef = useRef<HTMLButtonElement | null>(null);

  // Focus into the drawer as it opens (AppShell puts it back on the menu
  // button when it closes).
  useEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);

  return (
    <aside
      id={SIDE_MENU_ID}
      className={open ? "sidebar open" : "sidebar"}
      aria-label={open ? "Menu" : undefined}
    >
      <Link href={locked ? "/account" : "/"} className="sidebar-brand" onClick={onClose}>
        <div className="brand-icon">
          <CarFront style={{ width: 18, height: 18 }} />
        </div>
        <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.1 }}>
          <span style={{ fontWeight: 800, letterSpacing: "0.04em", fontSize: "14px" }}>CARLINK</span>
          <span style={{ fontSize: "10px", color: "var(--accent-primary)", fontWeight: 700, letterSpacing: "0.08em" }}>STUDIO 2.0</span>
        </div>
      </Link>

      {/* Only shown while the menu is a drawer (globals.css). */}
      <button ref={closeRef} type="button" className="sidebar-close" onClick={onClose} aria-label="Close menu" title="Close (Esc)">
        <X aria-hidden="true" style={{ width: 18, height: 18 }} />
      </button>

      <nav style={{ display: "flex", flexDirection: "column", gap: 3, marginTop: 12 }}>
        {items.map((item) => {
          const Icon = item.icon;
          const active = isActiveLink(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`sidebar-link ${active ? "active" : ""}`}
              onClick={onClose}
            >
              <Icon style={{ width: 16, height: 16, flexShrink: 0 }} />
              <span>{item.label}</span>
              {item.href === "/inbox" && inbox && inbox.new > 0 && (
                <span
                  className={`sidebar-inbox-badge ${inbox.expiring_soon > 0 ? "expiring" : ""}`}
                  title={
                    `${inbox.new} new report${inbox.new === 1 ? "" : "s"} from the public` +
                    (inbox.expiring_soon > 0 ? `, ${inbox.expiring_soon} close to being deleted` : "")
                  }
                >
                  {inbox.new > 99 ? "99+" : inbox.new}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      <div style={{ marginTop: "auto", paddingTop: 16, borderTop: "1px solid var(--border-color)" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 10px",
            borderRadius: "8px",
            background: "var(--surface-hover)",
            fontSize: "11px",
            color: "var(--text-muted)",
          }}
        >
          <div
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: "var(--badge-green-text)",
              boxShadow: "0 0 6px var(--badge-green-text)",
            }}
          />
          <span style={{ fontWeight: 600 }}>API Connected</span>
        </div>
      </div>
    </aside>
  );
}

