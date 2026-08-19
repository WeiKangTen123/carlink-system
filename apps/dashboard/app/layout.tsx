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
            <div style={{ display: "flex", alignItems: "center", gap: 28 }}>
              {/* Brand Logo */}
              <Link href="/" className="brand">
                <div className="brand-icon">🚗</div>
                <span>Carlink Studio</span>
                <span className="brand-badge">v2.0</span>
              </Link>

              {/* Universal Top Navigation */}
              <nav style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <Link href="/" className="nav-link">
                  <span>🔍</span> Loss Adjuster Studio
                </Link>
                <Link href="/reports" className="nav-link">
                  <span>📁</span> Cases Repository
                </Link>
                <Link href="/reports/new" className="nav-link">
                  <span>✨</span> Incident Intake
                </Link>
                <Link href="/analytics" className="nav-link">
                  <span>📈</span> Analytics
                </Link>
                <Link href="/settings" className="nav-link">
                  <span>⚙️</span> Settings
                </Link>
              </nav>
            </div>

            {/* Right Action Area */}
            <div style={{ marginLeft: "auto", display: "flex", gap: 12, alignItems: "center" }}>
              {/* Active Case Selector Dropdown */}
              <CaseSelectorDropdown />

              {/* Dual Theme Switcher (Dark & White) */}
              <ThemeToggle />

              {/* Surveyor Profile Pill */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "4px 12px 4px 4px",
                  background: "var(--surface-elevated)",
                  border: "1px solid var(--border-color)",
                  borderRadius: "20px",
                  fontSize: "12px",
                  fontWeight: 600,
                }}
              >
                <div
                  style={{
                    width: 24,
                    height: 24,
                    borderRadius: "50%",
                    background: "var(--accent-gradient)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "white",
                    fontSize: 10,
                    fontWeight: 800,
                  }}
                >
                  AW
                </div>
                <span>Alex Wong</span>
              </div>
            </div>
          </header>

          <main className="main-content" style={{ maxWidth: 1440, margin: "0 auto", padding: "20px 24px 60px" }}>
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
