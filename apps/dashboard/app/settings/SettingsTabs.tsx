"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Building2, UserCog, Users } from "lucide-react";

/** The three Settings tabs, each its own address. Admins only: the layout
 * doesn't draw this for anyone else, who only have My settings. A client
 * component because the layout isn't drawn again when moving between tabs,
 * so the highlighted tab has to follow the address here. */
const TABS = [
  { href: "/settings", label: "My settings", icon: UserCog },
  { href: "/settings/users", label: "Users", icon: Users },
  { href: "/settings/company", label: "Company", icon: Building2 },
] as const;

function isActive(pathname: string, href: string): boolean {
  if (href === "/settings") return pathname === "/settings";
  return pathname === href || pathname.startsWith(href + "/");
}

export function SettingsTabs() {
  const pathname = usePathname();
  return (
    <nav className="case-tab-bar settings-tabs" aria-label="Settings">
      {TABS.map((tab) => {
        const Icon = tab.icon;
        const active = isActive(pathname, tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            prefetch={false}
            className={`case-tab ${active ? "active" : ""}`}
            aria-current={active ? "page" : undefined}
          >
            <Icon style={{ width: 14, height: 14 }} /> {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
