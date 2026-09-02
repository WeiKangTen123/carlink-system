"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/", label: "Overview", icon: "📊" },
  { href: "/reports", label: "Cases", icon: "📁" },
  { href: "/reports/new", label: "New Intake", icon: "✨" },
  { href: "/analytics", label: "Analytics", icon: "📈" },
  { href: "/settings", label: "Settings", icon: "⚙️" },
];

// "/" only matches the exact root. "/reports" needs to stay active for
// case-detail pages (/reports/abc123) but NOT for /reports/new, which has
// its own nav item and would otherwise also light up "Cases" since
// "/reports/new".startsWith("/reports") is also true.
function isActiveLink(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/reports") {
    return pathname === "/reports" || (pathname.startsWith("/reports/") && !pathname.startsWith("/reports/new"));
  }
  return pathname === href || pathname.startsWith(href + "/");
}

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sidebar">
      <Link href="/" className="sidebar-brand">
        <div className="brand-icon">🚗</div>
        <span>Carlink</span>
      </Link>
      <nav style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {NAV_ITEMS.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`sidebar-link ${isActiveLink(pathname, item.href) ? "active" : ""}`}
          >
            <span>{item.icon}</span> {item.label}
          </Link>
        ))}
      </nav>
    </aside>
  );
}
