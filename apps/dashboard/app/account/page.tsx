import { redirect } from "next/navigation";
import { getCurrentUser } from "@/lib/api";
import { AccountClient } from "./AccountClient";

/** Only for the forced change after signing in with a temporary password
 * (AppShell, SessionGuard, lib/api.ts and the sign-in and sign-up pages send
 * those accounts here). The page calls nothing but GET /auth/me and POST
 * /auth/password, the two the API allows until the password is changed.
 * Everyone else, and old links to "My account", go to Settings, where the
 * profile and password now live. */
export default async function AccountPage() {
  const user = await getCurrentUser();
  if (!user) redirect("/login?next=/account");
  if (!user.must_change_password) redirect("/settings");
  return <AccountClient user={user} />;
}
