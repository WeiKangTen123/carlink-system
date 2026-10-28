"use server";

import { revalidatePath } from "next/cache";
import { unstable_rethrow } from "next/navigation";
import {
  changeMyPassword,
  reassignCases,
  updateMyProfile,
  updateAppSettings,
  listLlmKeys,
  addLlmKey,
  deleteLlmKey,
  testService,
  listUsers,
  updateUser,
  listAccountLinks,
  createAccountLink,
  revokeAccountLink,
  createResetLink,
  setSignupsOpen,
  type AccountLinkRow,
  type AppSettings,
  type CurrentUser,
  type IssuedLink,
  type LinkLifetime,
  type LlmKey,
  type ProfileFields,
  type UserAccount,
  type UpdatedUser,
  type UserPatch,
  type UserRole,
} from "@/lib/api";

/** All of these are server actions so the browser never talks to the API
 * directly -- same CORS reason every other mutation in this app goes
 * through one. Errors are returned rather than thrown so the settings UI
 * can show them inline instead of blowing up the page. */

// My settings (everyone, on their own account). The forced temporary
// password change on /account uses changeMyPasswordAction too: it calls only
// POST /auth/password, which the API allows before the password is changed.

export async function updateMyProfileAction(patch: ProfileFields): Promise<{ user: CurrentUser } | { error: string }> {
  try {
    const user = await updateMyProfile(patch);
    // The name is shown in the top bar, which the root layout renders.
    revalidatePath("/", "layout");
    return { user };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to save your profile" };
  }
}

export async function changeMyPasswordAction(
  currentPassword: string,
  newPassword: string
): Promise<{ ok: true } | { error: string }> {
  try {
    await changeMyPassword(currentPassword, newPassword);
    revalidatePath("/", "layout");
    return { ok: true };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to change your password" };
  }
}

// Company (admin only -- the API refuses anyone else).

export async function updateSettingsAction(
  patch: Partial<AppSettings>
): Promise<{ settings: AppSettings } | { error: string }> {
  try {
    const settings = await updateAppSettings(patch);
    revalidatePath("/settings", "layout");
    return { settings };
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to save settings" };
  }
}

export async function listLlmKeysAction(): Promise<
  { keys: LlmKey[]; env_key_configured: boolean } | { error: string }
> {
  try {
    return await listLlmKeys();
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to load API keys" };
  }
}

export async function addLlmKeyAction(
  apiKey: string,
  label?: string
): Promise<{ key: LlmKey } | { error: string }> {
  try {
    const key = await addLlmKey(apiKey, label);
    revalidatePath("/settings", "layout");
    return { key };
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to add key" };
  }
}

export async function deleteLlmKeyAction(id: string): Promise<{ ok: true } | { error: string }> {
  try {
    await deleteLlmKey(id);
    revalidatePath("/settings", "layout");
    return { ok: true };
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to remove key" };
  }
}

export async function testServiceAction(
  service: string
): Promise<{ ok: boolean; message: string }> {
  try {
    return await testService(service);
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { ok: false, message: err instanceof Error ? err.message : "Test failed" };
  }
}

// Users (admin only -- the API refuses anyone else).

export async function listUsersAction(): Promise<{ users: UserAccount[] } | { error: string }> {
  try {
    return { users: await listUsers() };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to load users" };
  }
}

/** Also disables, with what happens to their open cases (patch.open_cases);
 * the API does all of that or none of it. */
export async function updateUserAction(id: string, patch: UserPatch): Promise<{ user: UpdatedUser } | { error: string }> {
  try {
    const user = await updateUser(id, patch);
    // Disabling can move or unassign their cases, which the case lists show.
    if (patch.active === false) revalidatePath("/reports");
    return { user };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to save the user" };
  }
}

/** "Move their cases": everything they own that isn't signed off (and,
 * with includeSigned, what is) goes to toUserId, or becomes Unassigned. */
export async function reassignCasesAction(
  fromUserId: string,
  toUserId: string | null,
  includeSigned: boolean
): Promise<{ moved: number } | { error: string }> {
  try {
    const result = await reassignCases(fromUserId, toUserId, includeSigned);
    // The case lists show owners.
    revalidatePath("/reports");
    revalidatePath("/settings", "layout");
    return { moved: result.moved };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to move the cases" };
  }
}

/** A one-time link for the user to set a new password, instead of a
 * temporary password the admin reads out. */
export async function createResetLinkAction(id: string): Promise<IssuedLink | { error: string }> {
  try {
    return await createResetLink(id);
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to create the reset link" };
  }
}

/** The on/off switch for the public /signup page. */
export async function setSignupsOpenAction(open: boolean): Promise<{ open: boolean } | { error: string }> {
  try {
    return { open: await setSignupsOpen(open) };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to change signups" };
  }
}

// One-time invite and reset links. The token comes back once, in the
// result, for the admin to copy; the API keeps only its hash.

export async function listAccountLinksAction(): Promise<{ links: AccountLinkRow[] } | { error: string }> {
  try {
    return { links: await listAccountLinks() };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to load links" };
  }
}

export async function createAccountLinkAction(fields: {
  email: string;
  role: UserRole;
  name?: string;
  expires_hours?: LinkLifetime;
}): Promise<IssuedLink | { error: string }> {
  try {
    return await createAccountLink(fields);
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to create the link" };
  }
}

export async function revokeAccountLinkAction(id: string): Promise<{ link: AccountLinkRow } | { error: string }> {
  try {
    return { link: await revokeAccountLink(id) };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to cancel the link" };
  }
}
