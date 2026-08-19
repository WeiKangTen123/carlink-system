import type { Metadata } from "next";
import Link from "next/link";
import { ThemeToggle } from "@/components/ThemeToggle";
import { CaseSelectorDropdown } from "@/components/CaseSelectorDropdown";
import "./globals.css";

export const metadata: Metadata = {
  title: "Carlink Studio 2.0 // Intelligent Loss Adjuster & Incident System",
  description:
    "Intelligent loss adjuster studio with AI vision extraction, interactive vehicle blueprint, and official PDF sign-off workflow.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark">
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem("carlink-theme")||"dark";document.documentElement.setAttribute("data-theme",t);}catch(e){}})();`,
          }}
        />
      </head>
      <body>
        <div className="app-shell">
          <header className="top-nav">
            <Link href="/" className="brand">
              <div className="brand-icon">🚗</div>
              <span>Carlink Studio</span>
              <span className="brand-badge">v2.0</span>
            </Link>

            <Link href="/" className="nav-link">
              <span>📊</span> Overview
            </Link>
            <Link href="/reports" className="nav-link">
              <span>📁</span> Cases
            </Link>
            <Link href="/analytics" className="nav-link">
              <span>📈</span> Analytics
            </Link>
            <Link href="/settings" className="nav-link">
              <span>⚙️</span> Settings
            </Link>

            <div style={{ marginLeft: "auto", display: "flex", gap: 12, alignItems: "center" }}>
              {/* Case Selector Dropdown */}
              <CaseSelectorDropdown />

              {/* Dual Theme Switcher (Dark & White) */}
              <ThemeToggle />

              {/* Action Button */}
              <Link href="/reports/new" className="button-primary">
                <span>+</span> File Incident
              </Link>
            </div>
          </header>

          <main className="main-content" style={{ maxWidth: 1440, margin: "0 auto", padding: "24px" }}>
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
