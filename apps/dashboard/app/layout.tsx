import type { Metadata } from "next";
import Link from "next/link";
import { ShieldCheck, Plus } from "lucide-react";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Sidebar } from "@/components/Sidebar";
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
          <Sidebar />

          <div className="main-column">
            <header className="top-bar">
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", color: "var(--text-muted)", textTransform: "uppercase" }}>
                  Autonomous Claims &amp; Loss Adjusting Workstation
                </span>
              </div>

              <div style={{ marginLeft: "auto", display: "flex", gap: 12, alignItems: "center" }}>
                <ThemeToggle />

                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "4px 12px 4px 6px",
                    background: "var(--surface-elevated)",
                    border: "1px solid var(--border-color)",
                    borderRadius: "20px",
                    fontSize: "12px",
                    fontWeight: 600,
                  }}
                >
                  <div
                    style={{
                      width: 22,
                      height: 22,
                      borderRadius: "50%",
                      background: "var(--accent-primary)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      color: "white",
                    }}
                  >
                    <ShieldCheck style={{ width: 13, height: 13 }} />
                  </div>
                  <span>Surveyor / Loss Adjuster</span>
                </div>
              </div>
            </header>

            <main className="main-content">
              {children}
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
