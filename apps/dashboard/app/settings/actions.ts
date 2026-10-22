"use server";

import { revalidatePath } from "next/cache";
import { unstable_rethrow } from "next/navigation";
import {
  updateAppSettings,
  listLlmKeys,
  addLlmKey,
  deleteLlmKey,
  testService,
  listUsers,
  createUser,
  updateUser,
  resetUserPassword,
  type AppSettings,
  type LlmKey,
  type ProfileFields,
  type UserAccount,
} from "@/lib/api";

/** All of these are server actions so the browser never talks to the API
 * directly -- same CORS reason every other mutation in this app goes
 * through one. Errors are returned rather than thrown so the settings UI
 * can show them inline instead of blowing up the page. */

export async function updateSettingsAction(
  patch: Partial<AppSettings>
): Promise<{ settings: AppSettings } | { error: string }> {
  try {
    const settings = await updateAppSettings(patch);
    revalidatePath("/settings");
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
    revalidatePath("/settings");
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
    revalidatePath("/settings");
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

export async function createUserAction(
  fields: ProfileFields & { email: string; name: string; role: "admin" | "surveyor" }
): Promise<{ user: UserAccount; temporaryPassword: string } | { error: string }> {
  try {
    const result = await createUser(fields);
    return { user: result.user, temporaryPassword: result.temporary_password };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to add the user" };
  }
}

export async function updateUserAction(
  id: string,
  patch: ProfileFields & { email?: string; role?: "admin" | "surveyor"; active?: boolean }
): Promise<{ user: UserAccount } | { error: string }> {
  try {
    return { user: await updateUser(id, patch) };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to save the user" };
  }
}

export async function resetUserPasswordAction(
  id: string
): Promise<{ user: UserAccount; temporaryPassword: string } | { error: string }> {
  try {
    const result = await resetUserPassword(id);
    return { user: result.user, temporaryPassword: result.temporary_password };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to reset the password" };
  }
}
