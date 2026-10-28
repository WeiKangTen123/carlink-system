import type { Metadata } from "next";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { getCurrentUser } from "@/lib/api";
import { isPublicPage } from "@/lib/publicPages";
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

  // A temporary password: straight to the forced change (/account), the only
  // page that works until it's replaced. Done here, before anything is sent,
  // so the browser gets a plain redirect. Left to the page, the redirect
  // arrives mid-stream while AppShell's own router.replace("/account") also
  // runs, and the two navigations together crash Next's router (React error
  // #310, "Rendered more hooks than during the previous render") and leave
  // the browser on the wrong page. x-carlink-path is set by proxy.ts.
  if (user?.must_change_password) {
    const here = ((await headers()).get("x-carlink-path") || "").split(/[?#]/, 1)[0];
    if (here.startsWith("/") && here !== "/account" && !isPublicPage(here)) redirect("/account");
  }

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
