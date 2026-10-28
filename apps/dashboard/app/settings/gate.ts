import { redirect } from "next/navigation";
import { getCurrentUser, loginPath, type CurrentUser } from "@/lib/api";

/** Who is opening Settings, checked on the server before anything loads.
 * Signed out: to sign-in, then back. Still on a temporary password: to the
 * forced change (/account), the only page that works until it's replaced.
 * getCurrentUser is cached per request, so the layout and the page asking
 * both cost one GET /auth/me. */
export async function settingsUser(): Promise<CurrentUser> {
  const me = await getCurrentUser();
  if (!me) redirect(await loginPath());
  if (me.must_change_password) redirect("/account");
  return me;
}

export function isAdminUser(me: CurrentUser): boolean {
  return me.role === "admin";
}
