"use server";

import { revalidatePath } from "next/cache";
import { unstable_rethrow } from "next/navigation";
import {
  ApiError,
  assignSubmission,
  discardSubmission,
  errorCode,
  listUsers,
  releaseSubmission,
  takeSubmission,
  type DiscardRequest,
  type Owner,
} from "@/lib/api";
import type { AssignableUser } from "@/app/reports/actions";

/** Public Inbox actions (release P6). Server actions, like every other
 * mutation in the dashboard: the browser never calls the API's port itself.
 * Each returns its error as a value (with the API's code), so the screen can
 * say what happened -- someone else took the report, it was withdrawn --
 * and reload. */

export type InboxActionError = { error: string; code: string | null; status: number | null };

function actionError(err: unknown, fallback: string): InboxActionError {
  // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
  unstable_rethrow(err);
  return {
    error: err instanceof Error && err.message ? err.message : fallback,
    code: errorCode(err),
    status: err instanceof ApiError ? err.status : null,
  };
}

function revalidateInbox(id?: string) {
  revalidatePath("/inbox");
  if (id) revalidatePath(`/inbox/${id}`);
  revalidatePath("/reports");
  revalidatePath("/");
}

export async function takeSubmissionAction(id: string): Promise<{ owner: Owner | null } | InboxActionError> {
  try {
    const res = await takeSubmission(id);
    revalidateInbox(id);
    return { owner: res.owner ?? null };
  } catch (err) {
    return actionError(err, "Failed to take the report");
  }
}

export async function assignSubmissionAction(id: string, ownerId: string): Promise<{ owner: Owner | null } | InboxActionError> {
  try {
    const res = await assignSubmission(id, ownerId);
    revalidateInbox(id);
    return { owner: res.owner ?? null };
  } catch (err) {
    return actionError(err, "Failed to assign the report");
  }
}

export async function releaseSubmissionAction(id: string): Promise<{ ok: true } | InboxActionError> {
  try {
    await releaseSubmission(id);
    revalidateInbox(id);
    revalidatePath(`/reports/${id}`);
    return { ok: true };
  } catch (err) {
    return actionError(err, "Failed to return the report to the inbox");
  }
}

export async function discardSubmissionAction(id: string, body: DiscardRequest): Promise<{ ok: true } | InboxActionError> {
  try {
    await discardSubmission(id, body);
    revalidateInbox(id);
    return { ok: true };
  } catch (err) {
    return actionError(err, "Failed to discard the report");
  }
}

/** "Discard selected as spam": each report through POST /inbox/{id}/discard,
 * one after another, so one that changed meanwhile (taken, withdrawn) fails
 * on its own and the rest still go. */
export async function discardSubmissionsAction(
  ids: string[],
  body: DiscardRequest
): Promise<{ discarded: string[]; failed: { id: string; error: string; code: string | null }[] }> {
  const discarded: string[] = [];
  const failed: { id: string; error: string; code: string | null }[] = [];
  // The page offers at most what it shows; this keeps one call bounded.
  for (const id of ids.slice(0, 200)) {
    try {
      await discardSubmission(id, body);
      discarded.push(id);
    } catch (err) {
      const e = actionError(err, "Failed to discard the report");
      failed.push({ id, error: e.error, code: e.code });
    }
  }
  if (discarded.length) revalidateInbox();
  return { discarded, failed };
}

/** Who a public report can be given to (Assign to…, admins): active
 * accounts an admin has vetted -- admins, and anyone with one of the ticks
 * on Settings -> Users (the API's owners._vetted). The API checks again
 * (409 owner_unvetted). */
export async function listVettedUsersAction(): Promise<{ users: AssignableUser[] } | { error: string }> {
  try {
    const users = await listUsers();
    return {
      users: users
        .filter((u) => u.active && (u.role === "admin" || u.sees_all_cases || u.ai_company_key_allowed || u.may_sign_off))
        .map((u) => ({ id: u.id, name: u.name, username: u.username, cases: u.cases ?? null })),
    };
  } catch (err) {
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to load users" };
  }
}
