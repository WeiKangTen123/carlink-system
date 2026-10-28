import { unstable_rethrow } from "next/navigation";
import { getSignupsOpen, listAccountLinks, listUsers } from "@/lib/api";
import { AdminOnly } from "../AdminOnly";
import { isAdminUser, settingsUser } from "../gate";
import { UsersCard } from "../UsersCard";

/** Null instead of an error page when one part's data can't be loaded; the
 * card says so and offers to retry. */
async function orNull<T>(load: Promise<T>): Promise<T | null> {
  try {
    return await load;
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next.
    unstable_rethrow(err);
    console.warn("Settings: couldn't load part of the page:", err);
    return null;
  }
}

/** Settings -> Users (admins). The role is checked here, before any
 * admin-only call: everyone else gets the lock card and nothing loads. */
export default async function UsersSettingsPage() {
  const me = await settingsUser();
  if (!isAdminUser(me)) return <AdminOnly />;

  const [users, accountLinks, signupsOpen] = await Promise.all([
    listUsers(),
    orNull(listAccountLinks()),
    getSignupsOpen(),
  ]);

  return (
    <UsersCard
      initialUsers={users}
      initialLinks={accountLinks}
      initialSignupsOpen={signupsOpen}
      currentUserId={me.id}
    />
  );
}
