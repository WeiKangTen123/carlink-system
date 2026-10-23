import { NextResponse, type NextRequest } from "next/server";
import { isPublicPage } from "@/lib/publicPages";

// Kept in step with SESSION_COOKIES in lib/api.ts (not imported: this file
// stays free of that module's server/browser branches).
const SESSION_COOKIES = ["__Host-carlink_session", "carlink_session"];

/**
 * A quick check, not the real one: with no session cookie at all, go to
 * /login instead of rendering a page whose every API call would fail
 * (except the public pages in lib/publicPages.ts: sign-in, request access,
 * invite links, the privacy notice). The
 * API checks the session itself on every request (bot-service app/auth);
 * a cookie whose session has ended is caught there, and lib/api.ts then
 * redirects to /login the same way.
 *
 * Also passes the page's path on to server code (x-carlink-path), so that
 * redirect can bring the user back to where they were after signing in.
 */
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  if (!isPublicPage(pathname) && !SESSION_COOKIES.some((name) => request.cookies.has(name))) {
    const login = request.nextUrl.clone();
    login.pathname = "/login";
    login.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(login);
  }

  const headers = new Headers(request.headers);
  headers.set("x-carlink-path", pathname + search);
  return NextResponse.next({ request: { headers } });
}

export const config = {
  // Everything except Next's own bundles and the static 3D models/icons,
  // which hold no case data and are needed to draw the login page itself.
  matcher: ["/((?!_next/static|_next/image|assets/|favicon.ico).*)"],
};
