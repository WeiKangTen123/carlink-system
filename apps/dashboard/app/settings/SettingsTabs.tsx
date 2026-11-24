"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, Building2, Gauge, Tags, UserCog, Users } from "lucide-react";

/** The Settings tabs, each its own address. Everyone has My settings and
 * My prices (Stage 1: every signed-in staff account keeps its own prices);
 * the other four are for admins, and are left out for anyone else. A
 * client component because the layout isn't drawn again when moving
 * between tabs, so the highlighted tab has to follow the address here. */
const TABS = [
  { href: "/settings", label: "My settings", icon: UserCog, adminOnly: false },
  // The person's own remembered prices (GET /me/prices, own rows only).
  { href: "/settings/my-prices", label: "My prices", icon: Tags, adminOnly: false },
  { href: "/settings/users", label: "Users", icon: Users, adminOnly: true },
  { href: "/settings/company", label: "Company", icon: Building2, adminOnly: true },
  // The AI run log summed (round 3, B3): GET /ai-usage is admin only.
  { href: "/settings/ai-usage", label: "AI usage", icon: Activity, adminOnly: true },
  // What surveyors kept of the AI's drafts (contract 8 L): GET
  // /admin/ai-accuracy is admin only.
  { href: "/settings/ai-accuracy", label: "AI accuracy", icon: Gauge, adminOnly: true },
] as const;

function isActive(pathname: string, href: string): boolean {
  if (href === "/settings") return pathname === "/settings";
  return pathname === href || pathname.startsWith(href + "/");
}

export function SettingsTabs({ admin }: { admin: boolean }) {
  const pathname = usePathname();
  return (
    <nav className="case-tab-bar settings-tabs" aria-label="Settings">
      {TABS.filter((tab) => admin || !tab.adminOnly).map((tab) => {
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
