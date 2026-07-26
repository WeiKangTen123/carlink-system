import type { Metadata } from "next";
import Link from "next/link";
import { ThemeToggle } from "@/components/ThemeToggle";
import "./globals.css";

export const metadata: Metadata = {
  title: "Carlink Incident Reporting & Damage System",
  description: "Incident reporting dashboard with AI vision extraction, vehicle damage summary, and PDF generation.",
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
          <nav className="top-nav">
            <Link href="/" className="brand">
              <span>🚗</span> <span>Carlink System</span>
            </Link>
            <Link href="/" className="nav-link">
              Overview
            </Link>
            <Link href="/reports" className="nav-link">
              Reports
            </Link>
            <Link href="/analytics" className="nav-link">
              Analytics
            </Link>
            <Link href="/settings" className="nav-link">
              Settings
            </Link>
            <div style={{ marginLeft: "auto", display: "flex", gap: 12, alignItems: "center" }}>
              <ThemeToggle />
              <Link href="/reports/new" className="button-primary">
                + File Report
              </Link>
            </div>
          </nav>
          <main className="main-content" style={{ maxWidth: 1280, margin: "0 auto", padding: "28px 24px" }}>
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
