"use server";

import { revalidatePath } from "next/cache";
import { unstable_rethrow } from "next/navigation";
import { changeMyPassword, updateMyProfile, type CurrentUser, type ProfileFields } from "@/lib/api";

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
