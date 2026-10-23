import type { Metadata } from "next";
import { AppShell } from "@/components/AppShell";
import { getCurrentUser } from "@/lib/api";
import "./globals.css";

export const metadata: Metadata = {
  title: "Carlink Studio 2.0 // Intelligent Loss Adjuster & Incident System",
  description:
    "Intelligent loss adjuster studio with AI vision extraction, interactive vehicle blueprint, and official PDF sign-off workflow.",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Null on /login (and whenever the session has ended -- the page's own
  // API calls then redirect to /login, see lib/api.ts).
  const user = await getCurrentUser();

  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem("carlink-theme")||"dark";document.documentElement.setAttribute("data-theme",t);}catch(e){}})();`,
          }}
        />
      </head>
      <body>
        <AppShell user={user}>{children}</AppShell>
      </body>
    </html>
  );
}
