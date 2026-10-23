/** Pages anyone can open without signing in. Drawn on their own, without
 * the sidebar (components/AppShell.tsx), let through proxy.ts with no
 * session cookie, and never used as the page to return to after signing in
 * (loginPath() in lib/api.ts, safeNext() in app/login/page.tsx).
 *
 * Kept free of imports: proxy.ts loads it too. */
export const PUBLIC_PAGES = ["/login", "/signup", "/invite", "/privacy"] as const;

/** True for a public page or anything under it. Takes a bare pathname or
 * one with ?query / #fragment still attached. */
export function isPublicPage(path: string): boolean {
  const pathname = path.split(/[?#]/, 1)[0];
  return PUBLIC_PAGES.some((page) => pathname === page || pathname.startsWith(page + "/"));
}
