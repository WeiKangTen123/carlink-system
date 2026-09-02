import type { Metadata } from "next";
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
              <div style={{ marginLeft: "auto", display: "flex", gap: 12, alignItems: "center" }}>
                <ThemeToggle />

                {/* Role Pill -- there's no login/auth system, so this names a
                    role, not a specific person (see signOffReportAction's
                    default reviewer name for the same convention). Showing an
                    invented name here as if someone were logged in is exactly
                    the kind of fabricated-identity bug this project has
                    already had to fix elsewhere. */}
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
                    SA
                  </div>
                  <span>Surveyor / Loss Adjuster</span>
                </div>
              </div>
            </header>

            <main className="main-content" style={{ maxWidth: 1440, margin: "0 auto", padding: "20px 24px 60px", width: "100%" }}>
              {children}
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
