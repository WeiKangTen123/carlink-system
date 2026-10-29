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
  getMyAiKey,
  saveMyAiKey,
  testMyAiKey,
  deleteMyAiKey,
  deleteUserAiKey,
  ApiError,
  errorCode,
  type AccountLinkRow,
  type AppSettings,
  type CurrentUser,
  type IssuedLink,
  type LinkLifetime,
  type LlmKey,
  type MyAiKey,
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

// My AI key (everyone, their own; GET/PUT/DELETE /auth/me/ai-key). The key
// goes from the form to the API through here and is never sent back: every
// answer has only its last 4 characters and a status. Errors keep the API's
// code and status, so the card can tell "no key any more" (no_ai_key) and
// "this account may not" (403) from the rest. Signed out, or still on a
// temporary password: lib/api.ts redirects, and unstable_rethrow lets that
// through to Next.
//
// Saving, testing or removing the key can turn the AI on or off for this
// person (release P4), and GET /auth/me's `ai_allowed` (read by the root
// layout, for the intake wizard's "AI is off" and Fill in by hand) follows
// it, so these revalidate everything, not only Settings.

/** Everything under the root layout, so the signed-in user it reads (and
 * its ai_allowed) is asked for again. */
function revalidateAiAllowed() {
  revalidatePath("/", "layout");
}

export type AiKeyError = { error: string; code: string | null; status: number | null };
export type AiKeyResult = { key: MyAiKey } | AiKeyError;

function aiKeyError(err: unknown, fallback: string): AiKeyError {
  unstable_rethrow(err);
  return {
    error: err instanceof Error ? err.message : fallback,
    code: errorCode(err),
    status: err instanceof ApiError ? err.status : null,
  };
}

export async function getMyAiKeyAction(): Promise<AiKeyResult> {
  try {
    return { key: await getMyAiKey() };
  } catch (err) {
    return aiKeyError(err, "Failed to load your AI key");
  }
}

export async function saveMyAiKeyAction(apiKey: string, billingConfirmed: boolean): Promise<AiKeyResult> {
  try {
    const key = await saveMyAiKey(apiKey, billingConfirmed);
    // The setup checklist on My settings follows the key, and ai_allowed.
    revalidateAiAllowed();
    return { key };
  } catch (err) {
    return aiKeyError(err, "Failed to save your AI key");
  }
}

export async function testMyAiKeyAction(): Promise<AiKeyResult> {
  try {
    const key = await testMyAiKey();
    revalidateAiAllowed();
    return { key };
  } catch (err) {
    return aiKeyError(err, "Failed to test your AI key");
  }
}

/** Removes it, then reads what is left (whether the company key covers
 * them), for the card to show. */
export async function deleteMyAiKeyAction(): Promise<AiKeyResult> {
  try {
    await deleteMyAiKey();
  } catch (err) {
    return aiKeyError(err, "Failed to remove your AI key");
  }
  revalidateAiAllowed();
  try {
    return { key: await getMyAiKey() };
  } catch (err) {
    unstable_rethrow(err);
    // Removed; only the read after it failed. The card works out the rest.
    return { error: "", code: "removed", status: null };
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

/** "Remove their key" on Settings -> Users -> Edit (DELETE
 * /users/{id}/ai-key). They can add a new one; disabling the account is what
 * stops that. */
export async function deleteUserAiKeyAction(id: string): Promise<{ ok: true } | { error: string }> {
  try {
    await deleteUserAiKey(id);
    // Your own key, if it was yours, is on My settings.
    revalidatePath("/settings", "layout");
    return { ok: true };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to remove their AI key" };
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
