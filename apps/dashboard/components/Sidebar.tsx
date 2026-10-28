"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  FolderArchive,
  BarChart3,
  BookOpen,
  Settings,
  CarFront,
} from "lucide-react";

const NAV_ITEMS = [
  { href: "/", label: "Command Center", icon: LayoutDashboard },
  { href: "/reports", label: "Cases Repository", icon: FolderArchive },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/knowledge", label: "Knowledge Base", icon: BookOpen },
  // Everyone: My settings (profile, password). Admins also get the Users
  // and Company tabs there (app/settings/layout.tsx).
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
 * would only bounce back (see AppShell). */
export function Sidebar({ locked = false }: { locked?: boolean }) {
  const pathname = usePathname();
  const items = locked ? [] : NAV_ITEMS;

  return (
    <aside className="sidebar">
      <Link href={locked ? "/account" : "/"} className="sidebar-brand">
        <div className="brand-icon">
          <CarFront style={{ width: 18, height: 18 }} />
        </div>
        <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.1 }}>
          <span style={{ fontWeight: 800, letterSpacing: "0.04em", fontSize: "14px" }}>CARLINK</span>
          <span style={{ fontSize: "10px", color: "var(--accent-primary)", fontWeight: 700, letterSpacing: "0.08em" }}>STUDIO 2.0</span>
        </div>
      </Link>

      <nav style={{ display: "flex", flexDirection: "column", gap: 3, marginTop: 12 }}>
        {items.map((item) => {
          const Icon = item.icon;
          const active = isActiveLink(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`sidebar-link ${active ? "active" : ""}`}
            >
              <Icon style={{ width: 16, height: 16, flexShrink: 0 }} />
              <span>{item.label}</span>
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

