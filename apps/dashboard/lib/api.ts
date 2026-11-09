/** Thin client over the bot-service FastAPI backend (apps/bot-service/app/api/main.py). */

import { cache } from "react";
import { redirect, unstable_rethrow } from "next/navigation";
import { isPublicPage } from "./publicPages";

const isBrowser = typeof window !== "undefined";

// Server-side (Server Components / Server Actions) reach the API directly
// -- in Docker/prod that's the private docker-network hostname (api:8000,
// via API_INTERNAL_URL, see infra/docker-compose.prod.yml), avoiding a
// pointless round trip out to the public internet and back. Falls back to
// localhost:8000 for local dev (`npm run dev`, no Docker/nginx involved).
const SERVER_BASE_URL =
  process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

// Client-side (the actual browser) must never be pointed at the API's raw
// port -- that requires the port to be open to the public internet and
// breaks silently on any network that doesn't allow it through (this is
// exactly why report creation already runs through a Server Action instead
// of a direct browser fetch -- see analyzePhotosAction's comment). In prod
// these default to same-origin relative paths that infra/nginx.conf proxies
// to the API; local dev has no nginx, so NEXT_PUBLIC_API_BASE_URL /
// NEXT_PUBLIC_FILES_BASE_URL there fall back to hitting the API port
// directly, same as before.
const CLIENT_API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const CLIENT_FILES_BASE_URL = process.env.NEXT_PUBLIC_FILES_BASE_URL ?? "http://localhost:8000";

export const API_BASE_URL = isBrowser ? CLIENT_API_BASE_URL : SERVER_BASE_URL;

// Download links end up in the DOM (<a href>), so they always use the
// client-safe base: same-origin in prod, where infra/nginx.conf proxies
// /reports/*/download to the API; the API's own port in local dev.
export function pdfDownloadUrl(reportId: string, opts: { photos?: boolean } = {}): string {
  const url = `${CLIENT_API_BASE_URL}/reports/${reportId}/download`;
  // photos=0: the same report without the photo pages, rendered on the fly.
  return opts.photos === false ? `${url}?photos=0` : url;
}

/** The report as the firm's four-sheet workbook (Cover Page, Annex A, B, C). */
export function xlsxDownloadUrl(reportId: string): string {
  return `${CLIENT_API_BASE_URL}/reports/${reportId}/download.xlsx`;
}

/**
 * Safe fetch wrapper that aborts after a timeout (default 8000ms) to prevent infinite hangs.
 */
export async function fetchWithTimeout(
  input: RequestInfo | URL,
  init?: RequestInit,
  timeoutMs = 8000
): Promise<Response> {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  if (init?.signal) {
    if (init.signal.aborted) {
      clearTimeout(timer);
      controller.abort();
    } else {
      init.signal.addEventListener("abort", () => {
        clearTimeout(timer);
        controller.abort();
      });
    }
  }

  try {
    const res = await fetch(input, {
      ...init,
      signal: controller.signal,
    });
    return res;
  } catch (err: unknown) {
    if (timedOut) {
      const urlStr =
        typeof input === "string"
          ? input
          : input instanceof URL
          ? input.toString()
          : (input as Request).url || "endpoint";
      throw new Error(`Request timed out after ${timeoutMs}ms: ${urlStr}`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// Sign-in. Every API route needs a signed-in user (bot-service app/auth).
// The browser sends the session cookie itself; server components and server
// actions call the API directly over the Docker network, so they forward the
// cookie of the request they are handling.

/** Set by POST /auth/login (apps/bot-service/app/auth/core.py): the
 * "__Host-" name over HTTPS, the bare one where AUTH_COOKIE_SECURE=false.
 * The API reads only the one it sets; both are passed on. */
export const SESSION_COOKIES = ["__Host-carlink_session", "carlink_session"] as const;

/** Fired in the browser when the API says the session has ended, so the
 * page can offer to sign in again without losing what is on screen
 * (components/SessionGuard.tsx). */
export const SIGNED_OUT_EVENT = "carlink:signed-out";

type ApiFetchOptions = {
  /** Abort after this long (see fetchWithTimeout); no limit if unset. */
  timeoutMs?: number;
  /** Server side, a signed-out response normally redirects to /login. With
   * "return" the response is handed back instead (used by getCurrentUser,
   * which also runs on the login page itself). */
  onSignedOut?: "redirect" | "return";
};

async function forwardedCookie(): Promise<string | null> {
  // Imported lazily: this module is bundled for the browser too, where
  // next/headers doesn't exist and is never reached.
  const { cookies } = await import("next/headers");
  const jar = await cookies();
  const pairs = SESSION_COOKIES.flatMap((name) => {
    const value = jar.get(name)?.value;
    return value ? [`${name}=${value}`] : [];
  });
  return pairs.length ? pairs.join("; ") : null;
}

/** /login, with ?next= back to the page being rendered. Server side only. */
export async function loginPath(): Promise<string> {
  // Set by proxy.ts on every page request.
  const { headers } = await import("next/headers");
  const here = (await headers()).get("x-carlink-path");
  // Never back to a public page (the sign-in or sign-up pages themselves).
  return here && here.startsWith("/") && !here.startsWith("//") && !isPublicPage(here)
    ? `/login?next=${encodeURIComponent(here)}`
    : "/login";
}

/** fetch() for API calls: carries the sign-in, and handles it ending. */
export async function apiFetch(input: string, init: RequestInit = {}, opts: ApiFetchOptions = {}): Promise<Response> {
  let finalInit: RequestInit;
  if (isBrowser) {
    // "include", not the default "same-origin": local dev serves the API
    // from another port. In production it is same-origin either way.
    finalInit = { credentials: "include", ...init };
  } else {
    const headers = new Headers(init.headers);
    const cookie = await forwardedCookie();
    if (cookie) headers.set("Cookie", cookie);
    finalInit = { ...init, headers };
  }
  const res = opts.timeoutMs ? await fetchWithTimeout(input, finalInit, opts.timeoutMs) : await fetch(input, finalInit);
  if (res.status === 401 || res.status === 403) await handleSignInProblem(res, opts);
  return res;
}

async function handleSignInProblem(res: Response, opts: ApiFetchOptions): Promise<void> {
  let reason: "signed_out" | "password_change_required" | null = null;
  if (res.status === 401) {
    reason = "signed_out";
  } else {
    const body = await res.clone().json().catch(() => null);
    if (body?.code === "password_change_required") reason = "password_change_required";
  }
  if (!reason) return;
  if (isBrowser) {
    window.dispatchEvent(new CustomEvent(SIGNED_OUT_EVENT, { detail: reason }));
    return;
  }
  if (opts.onSignedOut === "return") return;
  redirect(reason === "password_change_required" ? "/account" : await loginPath());
}

/** "user" was called "surveyor" until 2026-10-25. Everyone who signs up is
 * a user; only an admin makes someone an admin (Settings -> Users). */
export type UserRole = "admin" | "user";

type UserProfile = {
  id: string;
  email: string;
  /** Lower-case, unique; signs in like the email. */
  username: string;
  /** The full name printed on sign-offs; starts out as the username. */
  name: string;
  role: UserRole;
  must_change_password: boolean;
  qualifications: string;
  license_number: string;
  firm_name: string;
};

/** GET /auth/me. The three flags are what the account may do in effect
 * (admins always may), not the raw ticks an admin sets on Settings -> Users. */
export type CurrentUser = UserProfile & {
  /** Sees every case (and the My cases / All / Unassigned switch). */
  can_view_all_cases: boolean;
  may_sign_off: boolean;
  /** May run the AI right now: admins, accounts allowed the company AI key,
   * and anyone whose own AI key is working (release P4). */
  ai_allowed: boolean;
};

/** A person's cases by state (GET /users). */
export type CaseCounts = { open: number; signed_off: number; drafts: number };

/** GET /users (admin). The three ticks are as the admin set them; admins
 * have all of it whatever these say. */
export type UserAccount = UserProfile & {
  active: boolean;
  locked: boolean;
  last_login_at: string | null;
  created_at: string;
  /** "Can see and work on all cases". */
  sees_all_cases: boolean;
  /** "May use the company AI key". */
  ai_company_key_allowed: boolean;
  /** "May sign off reports". */
  may_sign_off: boolean;
  cases: CaseCounts;
  /** Their own AI key (Settings -> My AI key), or null for none. Last 4
   * characters and a status only, never the key. */
  ai_key: UserAiKeySummary | null;
  /** The Telegram they connected (release P5), or null for none. Absent
   * from an API older than P5. */
  telegram?: UserTelegramSummary | null;
};

/** The three ticks on Settings -> Users. */
export type UserTicks = Pick<UserAccount, "sees_all_cases" | "ai_company_key_allowed" | "may_sign_off">;

/** Who a case belongs to. A case with no owner (null) is Unassigned: only
 * admins and staff who see all cases see it. */
export type Owner = { id: string; name: string; username: string; active: boolean };

/** What the signed-in user may do on one case, as the API worked it out
 * (GET /reports/{id}, GET /intake/{id}). The API checks again on every call.
 * `release`: put a public report someone has taken back in the Public Inbox
 * (release P6; its owner or an admin); absent from an older API. */
export type CasePermissions = {
  edit: boolean;
  sign_off: boolean;
  reopen: boolean;
  delete: boolean;
  assign: boolean;
  release?: boolean;
};

/** Admins only, on an Unassigned case: who probably owns it, and why. */
export type SuggestedOwner = { id: string; name: string; username: string; reason: string };

/** Which cases GET /reports lists. Plain users only get "mine". */
export type CaseScope = "mine" | "all" | "unassigned";

export const CASE_SCOPES: readonly CaseScope[] = ["mine", "all", "unassigned"];

export function isCaseScope(value: unknown): value is CaseScope {
  return typeof value === "string" && (CASE_SCOPES as readonly string[]).includes(value);
}

/** "Tan Ah Huat (@ahhuat)", or "@ahhuat" when the name is just the username. */
export function personLabel(p: { name: string; username: string }): string {
  return p.name && p.name !== p.username ? `${p.name} (@${p.username})` : `@${p.username}`;
}

/** personLabel, "(disabled)" for a disabled account, "Unassigned" for none. */
export function ownerLabel(owner: Owner | null | undefined): string {
  if (!owner) return "Unassigned";
  return owner.active ? personLabel(owner) : `${personLabel(owner)} (disabled)`;
}

// ---------------------------------------------------------------------------
// Errors. The API sends {"detail": "...", "code": "..."} for refusals the
// screen should explain (like password_change_required above). The code is
// kept on the error, and the message is already the plain-words version, so
// a caller that just shows err.message shows the right thing.

/** AI refused for the person who pressed the button (release P4): no own
 * key that works, and the company key isn't theirs to use, or their account
 * is off. The API answers 409 {detail, code} before any AI runs; a
 * background intake run that stops on one keeps it in `error_code`. */
export const AI_OFF_CODES = ["ai_key_missing", "ai_key_invalid", "ai_quota_used", "ai_key_unreadable", "ai_account_off"] as const;
export type AiOffCode = (typeof AI_OFF_CODES)[number];

export function isAiOffCode(code: unknown): code is AiOffCode {
  return typeof code === "string" && (AI_OFF_CODES as readonly string[]).includes(code);
}

/** Only an admin can put these right ("Tell an admin"); the rest are fixed
 * on Settings -> My AI key ("Fix in Settings"). */
export function aiOffNeedsAdmin(code: string | null | undefined): boolean {
  return code === "ai_key_unreadable" || code === "ai_account_off";
}

/** The plain words for each: the API's (bot-service app/ai/keys.py
 * MESSAGES), for a refusal or a stopped run that came without them. */
const AI_OFF_MESSAGES: Record<AiOffCode, string> = {
  ai_key_missing:
    "AI is off for your account. Add your own AI key in Settings → My AI key (get one free from Google AI Studio), or ask an admin to allow the company key.",
  ai_key_invalid: "Your AI key stopped working (Google refused it). Check it in Settings → My AI key.",
  ai_quota_used:
    "Your AI key has used up today's free allowance. It works again after Google's daily reset, at 4 pm Malaysia time (3 pm from mid-March to early November), or turn on billing in Google Cloud.",
  ai_key_unreadable: "Your AI key can't be read on this server right now. Tell an admin (Settings → Company).",
  ai_account_off: "AI is off: your account is disabled.",
};

/** Plain words for the API's error codes. */
export const ERROR_MESSAGES: Record<string, string> = {
  // AI_OFF_CODES. ai_key_invalid is further down: My AI key uses that code
  // too, for a key Google refused while it was being saved.
  ai_key_missing: AI_OFF_MESSAGES.ai_key_missing,
  ai_quota_used: AI_OFF_MESSAGES.ai_quota_used,
  ai_key_unreadable: AI_OFF_MESSAGES.ai_key_unreadable,
  ai_account_off: AI_OFF_MESSAGES.ai_account_off,
  draft_limit: "You have too many unfinished drafts. Finish or delete one, then try again.",
  case_limit: "You've filed the most cases allowed in 24 hours for an account without staff access. Try again later, or ask an admin.",
  disk_full: "The server is almost out of space, so new photos and cases can't be saved. Please tell an admin.",
  render_busy: "The server is busy making other PDFs. Try again in a moment.",
  render_limit: "You've made a lot of PDFs in the last few minutes. Wait a few minutes and try again.",
  /** Only when the API gave no reason of its own (see refusalMessage). */
  not_allowed: "You can't change this case. Ask its owner or an admin.",
  scope_not_allowed: "You can only see your own cases.",
  owner_inactive: "That account is disabled. Pick someone else.",
  owner_draft: "This case is still an unfinished draft. Drafts can't be given to someone else.",
  // My AI key (/auth/me/ai-key). The API's own words come first for these
  // (DETAIL_FIRST_CODES); these are only for an answer without them.
  ai_key_format: "That doesn't look like a Gemini API key. It is usually 39 letters and numbers, starting with AIza.",
  ai_key_type_missing: "Choose whether this key is Free or Billed.",
  ai_key_changed: "Your AI key was changed in another tab or window. Reload the page to see it.",
  // Also an AI run's refusal (AI_OFF_CODES): there the API's words say the
  // saved key stopped working, and aiOffMessage() has them for none.
  ai_key_invalid: "Google didn't accept this key. Check you copied all of it.",
  ai_key_in_use: "This key can't be used here.",
  rate_limited: "Too many tries. Try again later.",
  ai_key_storage_off: "Saving AI keys is off on this server right now. Please tell your admin.",
  no_ai_key: "There is no AI key saved for this account.",
  ai_key_check_failed: "Couldn't reach Google to check the key. Try again in a minute.",
  // Public Inbox (release P6). inbox_taken comes with the API's own words
  // ("Taken by S. Lee just now"); this is for an answer without them.
  inbox_taken: "Someone else took this report just now.",
  inbox_gone:
    "This report isn't in the inbox any more. It may have been discarded, withdrawn by the sender, or deleted after 30 days.",
  inbox_changed: "This report changed while you were looking at it. Reload the page to see where it is now.",
  inbox_filed: "This report has already been filed as a case.",
  inbox_finalizing: "This report is being filed as a case right now, so it can't go back to the inbox.",
  owner_unvetted:
    "Public reports can only go to an admin, or to someone an admin has given access to (a tick on Settings → Users).",
  ai_running: "The AI is reading this report right now. Wait for it to finish, then try again.",
};

/** The plain-words message for an error code, if it has one. */
export function messageForCode(code: string | null | undefined): string | null {
  return (code && ERROR_MESSAGES[code]) || null;
}

/** Codes whose refusals each say why ("Only staff can set Our Ref."), so
 * the API's own words beat the one-size message above. The AI key ones too:
 * rate_limited, say, is "in an hour" for one person but "tomorrow" when the
 * whole firm has used up the day's saves. */
const DETAIL_FIRST_CODES = new Set([
  "not_allowed",
  // The API's usual words for this are the same as AI_OFF_MESSAGES'; on a
  // public report (release P6) they say only the presser's own billed key
  // may be used there (PUBLIC_AI_KEY_MISSING), or that a free key can't be,
  // which must not be replaced.
  "ai_key_missing",
  // "Taken by S. Lee just now": who, which the plain words can't say.
  "inbox_taken",
  "ai_key_format",
  "ai_key_type_missing",
  "ai_key_changed",
  "ai_key_invalid",
  "ai_key_in_use",
  "rate_limited",
  "ai_key_storage_off",
  "no_ai_key",
  "ai_key_check_failed",
]);

/** What to show for a refused call: the plain words for its code, else the
 * API's own detail (for not_allowed, the detail first). Null when there is
 * neither. FastAPI's validation errors put a list in detail, not a
 * sentence; that counts as none. */
export function refusalMessage(code: string | null, detail: unknown): string | null {
  const text = typeof detail === "string" && detail ? detail : null;
  if (code && text && DETAIL_FIRST_CODES.has(code)) return text;
  return messageForCode(code) || text;
}

/** What to say when AI was refused with one of AI_OFF_CODES. `said` is the
 * API's detail, or a message refusalMessage already made of it (an error's
 * message, an intake run's `error`). The same rule as refusalMessage, except
 * that an ai_key_invalid with no words of the API's means the saved key
 * stopped working, not that a key being saved was refused. */
export function aiOffMessage(code: AiOffCode, said?: unknown, opts: { publicCase?: boolean } = {}): string {
  const text = typeof said === "string" && said ? said : null;
  if (text && DETAIL_FIRST_CODES.has(code) && text !== ERROR_MESSAGES[code]) return text;
  // A public report with no words of the API's: the company key is kept
  // off it until an admin confirms it is billed (release P6).
  if (opts.publicCase && code === "ai_key_missing") return PUBLIC_AI_KEY_MISSING;
  return AI_OFF_MESSAGES[code];
}

/** ai_key_missing on a report sent by the public (release P6): until an admin
 * ticks "The company AI key is on a Google Cloud project with billing turned
 * on" (Settings -> Company), AI on public reports runs only on the presser's
 * own key, saved with billing confirmed. The API's words for it. */
export const PUBLIC_AI_KEY_MISSING =
  "Public reports can only use your own (billed) AI key until an admin confirms the company key is billed. You can still fill it in by hand.";

/** A refused API call. `code` is the API's error code, when it sent one;
 * `detail` its own words, when it sent a sentence. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly detail: string | null;

  constructor(message: string, status: number, code: string | null = null, detail: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

/** The error code of anything thrown by these calls, or null. */
export function errorCode(err: unknown): string | null {
  return err instanceof ApiError ? err.code : null;
}

/** For a refusal the API words for the case at hand -- a 409 (the case's
 * photo limit, a signed-off case) or a 429 (one AI request at a time, too
 * many photos this hour) -- its own words; otherwise err.message. */
export function limitMessage(err: unknown): string | null {
  if (!(err instanceof Error)) return null;
  if (err instanceof ApiError && (err.status === 409 || err.status === 429) && err.detail) return err.detail;
  return err.message;
}

/** An ApiError from a failed response: refusalMessage() for its code and
 * detail, else "<fallback> (<status>)". */
export async function apiError(res: Response, fallback: string): Promise<ApiError> {
  const body = await res.json().catch(() => null);
  const code = typeof body?.code === "string" ? body.code : null;
  const detail = typeof body?.detail === "string" && body.detail ? body.detail : null;
  return new ApiError(refusalMessage(code, body?.detail) || `${fallback} (${res.status})`, res.status, code, detail);
}

/** Profile fields a user edits on Settings -> My settings, and an admin on
 * anyone's. The name, licence and firm are printed on the PDF at sign-off. */
export type ProfileFields = {
  username?: string;
  name?: string;
  qualifications?: string;
  license_number?: string;
  firm_name?: string;
};

/** The signed-in user, or null when there is none. Never redirects.
 * Asked once per server render however many layouts and pages want it (the
 * root layout, Settings' layout and its page all do); React's cache() lasts
 * one request, so every new request still asks the API. */
export const getCurrentUser = cache(async (): Promise<CurrentUser | null> => {
  try {
    const res = await apiFetch(`${API_BASE_URL}/auth/me`, { cache: "no-store" }, { timeoutMs: 8000, onSignedOut: "return" });
    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
    unstable_rethrow(err);
    console.warn("Failed to load the signed-in user:", err);
    return null;
  }
});

export async function updateMyProfile(patch: ProfileFields): Promise<CurrentUser> {
  const res = await apiFetch(`${API_BASE_URL}/auth/me`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw await apiError(res, "Failed to save your profile");
  return res.json();
}

export async function changeMyPassword(currentPassword: string, newPassword: string): Promise<void> {
  const res = await apiFetch(`${API_BASE_URL}/auth/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
  if (!res.ok) throw await apiError(res, "Failed to change your password");
}

export async function listUsers(): Promise<UserAccount[]> {
  const res = await apiFetch(`${API_BASE_URL}/users`, { cache: "no-store" }, { timeoutMs: 8000 });
  if (!res.ok) throw await apiError(res, "Failed to load users");
  return res.json();
}

/** What happens to someone's open cases when they are disabled: "move" to
 * another active account, "unassign" (admins and staff who see all cases
 * pick them up), or "keep" them under the disabled account. Signed-off cases
 * and unfinished drafts stay with them either way. */
export type OpenCasesChoice = "keep" | "unassign" | "move";

/** What an admin may change on someone's account (PATCH /users/{id}).
 * `role` on your own account is refused (403 not_allowed), so it is only
 * sent when it changed. `open_cases` (+ `open_cases_to` for "move") go only
 * with `active: false`; the API does the whole disable or none of it. */
export type UserPatch = ProfileFields &
  Partial<UserTicks> & {
    email?: string;
    role?: UserRole;
    active?: boolean;
    open_cases?: OpenCasesChoice;
    open_cases_to?: string;
  };

/** PATCH /users/{id} answers with the account. When a disable applied an
 * open_cases choice it also says how many cases that touched (`moved`);
 * absent otherwise, and from an API that doesn't send it yet. */
export type UpdatedUser = UserAccount & { moved?: number };

export async function updateUser(id: string, patch: UserPatch): Promise<UpdatedUser> {
  const res = await apiFetch(`${API_BASE_URL}/users/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw await apiError(res, "Failed to save the user");
  return res.json();
}

// ---------------------------------------------------------------------------
// My AI key (release P3): each person's own Gemini key, on Settings -> My
// settings (bot-service app/api/ai_keys.py). Only its last 4 characters and
// a status ever come back, to anyone, admins included.
//
// Since release P4 the AI runs on the key of whoever presses the button:
// their own key when it works, else the company key if they may use it
// (admins, and accounts an admin allowed), else no AI. `source` and
// `ai_available` say which, now.

/** untested: saved, but Google couldn't be reached to check it. ok: Google
 * took it at the last check. invalid: Google refused it. quota: no
 * allowance left for now. unreadable: this server can't open it. */
export type AiKeyStatus = "untested" | "ok" | "invalid" | "quota" | "unreadable";

/** GET, PUT, PATCH and POST .../test /auth/me/ai-key. */
export type MyAiKey = {
  has_key: boolean;
  last4: string | null;
  /** Null when there is no key. */
  status: AiKeyStatus | null;
  /** Billed (true) or Free (false), as the person said. Only a Billed key
   * is used for reports sent by the public. False when there is no key. */
  billing_confirmed: boolean;
  added_at: string | null;
  last_checked_at: string | null;
  /** Why it is failing, in Google's words with any key scrubbed out. */
  last_error: string | null;
  /** Admins, and accounts ticked "May use the company AI key". */
  company_allowed: boolean;
  /** AI runs for this account: a working own key, or company_allowed. */
  ai_available: boolean;
  /** What the AI runs on for this account now; "none" when it is off. */
  source: "own" | "company" | "none";
};

/** The ai_key of a GET /users row. */
export type UserAiKeySummary = { has_key: boolean; last4: string | null; status: AiKeyStatus; added_at: string | null };

/** "Working" covers a key Google hasn't been asked about yet (untested). */
export function aiKeyWorking(status: AiKeyStatus | null | undefined): boolean {
  return status === "ok" || status === "untested";
}

export async function getMyAiKey(): Promise<MyAiKey> {
  const res = await apiFetch(`${API_BASE_URL}/auth/me/ai-key`, { cache: "no-store" }, { timeoutMs: 8000 });
  if (!res.ok) throw await apiError(res, "Failed to load your AI key");
  return res.json();
}

/** Free (false) or Billed (true) for the saved key, without pasting it
 * again; Google isn't asked. `last4`: the key the card shows, so a key
 * replaced in another tab meanwhile isn't switched (409 ai_key_changed). */
export async function setMyAiKeyBilled(billingConfirmed: boolean, last4: string | null): Promise<MyAiKey> {
  const res = await apiFetch(
    `${API_BASE_URL}/auth/me/ai-key`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(last4 ? { billing_confirmed: billingConfirmed, last4 } : { billing_confirmed: billingConfirmed }),
    },
    { timeoutMs: 8000 }
  );
  if (!res.ok) throw await apiError(res, "Failed to change your AI key's type");
  return res.json();
}

/** Adds or replaces. The API asks Google about the key first (up to ~10 s).
 * billingConfirmed: Billed (true) or Free (false). */
export async function saveMyAiKey(apiKey: string, billingConfirmed: boolean): Promise<MyAiKey> {
  const res = await apiFetch(
    `${API_BASE_URL}/auth/me/ai-key`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: apiKey, billing_confirmed: billingConfirmed }),
    },
    { timeoutMs: 30000 }
  );
  if (!res.ok) {
    const err = await apiError(res, "Failed to save your AI key");
    // Signed out meanwhile has already gone to sign-in (apiFetch). Any other
    // 403 without words of its own: say so plainly, e.g. the account was
    // disabled while the key was being checked.
    if (res.status === 403 && err.message.endsWith("(403)")) {
      throw new ApiError("Your account can't save an AI key right now; it may have been disabled. The key wasn't saved.", 403, err.code);
    }
    throw err;
  }
  return res.json();
}

export async function testMyAiKey(): Promise<MyAiKey> {
  const res = await apiFetch(`${API_BASE_URL}/auth/me/ai-key/test`, { method: "POST" }, { timeoutMs: 30000 });
  if (!res.ok) throw await apiError(res, "Failed to test your AI key");
  return res.json();
}

export async function deleteMyAiKey(): Promise<void> {
  const res = await apiFetch(`${API_BASE_URL}/auth/me/ai-key`, { method: "DELETE" }, { timeoutMs: 8000 });
  if (!res.ok) throw await apiError(res, "Failed to remove your AI key");
}

/** Admin: remove someone's own AI key (a leaver, or a key being misused). */
export async function deleteUserAiKey(id: string): Promise<void> {
  const res = await apiFetch(`${API_BASE_URL}/users/${id}/ai-key`, { method: "DELETE" }, { timeoutMs: 8000 });
  if (!res.ok) throw await apiError(res, "Failed to remove their AI key");
}

// ---------------------------------------------------------------------------
// My Telegram (release P5): each person connects their own Telegram on
// Settings -> My settings, so the Telegram bot files cases under their
// account (bot-service app/api/telegram_link.py, app/auth/chat_links.py).
//
// Linking takes both sides: Carlink makes a one-time code (10 minutes,
// shown once), the person sends it to the bot and taps Yes there, then
// confirms here ("Yes, connect"). The API keeps only the code's hash, so
// the plain code (and the deep link, which carries it) come back only from
// POST .../code; GET may leave them out.

/** Telegram usernames and names are optional and the person's own choice:
 * display only, never trusted for anything. */
export type TelegramIdentity = { username: string | null; name: string | null };

/** The open link code, if any. */
export type TelegramCode = {
  /** "K7M4-QX9P": the plain code, only in POST .../code's answer. */
  display: string | null;
  expires_at: string;
  /** t.me/<bot>?start=link-<code>, when the bot's @username is known. Like
   * the code, only in POST .../code's answer. */
  deep_link: string | null;
  /** Someone tapped "Yes, that's me" in Telegram with this code, and it now
   * waits for "Yes, connect" or "Not me" here. */
  waiting: TelegramIdentity | null;
};

/** GET /auth/me/telegram, and the answer of POST .../code and .../confirm. */
export type MyTelegram = {
  connected: boolean;
  telegram_username: string | null;
  telegram_name: string | null;
  linked_at: string | null;
  code: TelegramCode | null;
  /** The company bot's @username (without the @), once the bot has started;
   * null before that, or with no bot. */
  bot_username: string | null;
};

/** The telegram of a GET /users row. */
export type UserTelegramSummary = { username: string | null; name: string | null; linked_at: string | null };

export async function getMyTelegram(): Promise<MyTelegram> {
  const res = await apiFetch(`${API_BASE_URL}/auth/me/telegram`, { cache: "no-store" }, { timeoutMs: 8000 });
  if (!res.ok) throw await apiError(res, "Failed to load your Telegram");
  return res.json();
}

/** A new code (any older open one is cancelled). 429 after 10 an hour. */
export async function createTelegramCode(): Promise<MyTelegram> {
  const res = await apiFetch(`${API_BASE_URL}/auth/me/telegram/code`, { method: "POST" }, { timeoutMs: 8000 });
  if (!res.ok) throw await apiError(res, "Failed to make a code");
  return res.json();
}

export async function cancelTelegramCode(): Promise<void> {
  const res = await apiFetch(`${API_BASE_URL}/auth/me/telegram/code`, { method: "DELETE" }, { timeoutMs: 8000 });
  if (!res.ok) throw await apiError(res, "Failed to cancel the code");
}

/** "Yes, connect" (accept) or "Not me" for the Telegram that is waiting.
 * 409 when nothing is waiting any more, or the code has expired. */
export async function confirmTelegram(accept: boolean): Promise<MyTelegram> {
  const res = await apiFetch(
    `${API_BASE_URL}/auth/me/telegram/confirm`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ accept }) },
    { timeoutMs: 8000 }
  );
  if (!res.ok) throw await apiError(res, accept ? "Failed to connect your Telegram" : "Failed to cancel");
  return res.json();
}

export async function disconnectMyTelegram(): Promise<void> {
  const res = await apiFetch(`${API_BASE_URL}/auth/me/telegram`, { method: "DELETE" }, { timeoutMs: 8000 });
  if (!res.ok) throw await apiError(res, "Failed to disconnect your Telegram");
}

/** Admin: disconnect someone's Telegram (a lost phone, a leaver). */
export async function disconnectUserTelegram(id: string): Promise<void> {
  const res = await apiFetch(`${API_BASE_URL}/users/${id}/telegram`, { method: "DELETE" }, { timeoutMs: 8000 });
  if (!res.ok) throw await apiError(res, "Failed to disconnect their Telegram");
}

// ---------------------------------------------------------------------------
// Signup and one-time links. Anyone signs up on the public /signup page
// (app/signup, straight from the browser); admins can switch that off, and
// make invite links and password-reset links to send by WhatsApp. Opening a
// link runs from the browser too: app/invite.

/** Whether the public /signup page takes new accounts. Public. */
export async function getSignupsOpen(): Promise<boolean> {
  try {
    const res = await apiFetch(`${API_BASE_URL}/auth/signup`, { cache: "no-store" }, { timeoutMs: 8000, onSignedOut: "return" });
    if (!res.ok) return true;
    return Boolean((await res.json()).open);
  } catch (err) {
    unstable_rethrow(err);
    return true;
  }
}

/** Admin: switch new signups on or off. */
export async function setSignupsOpen(open: boolean): Promise<boolean> {
  const res = await apiFetch(`${API_BASE_URL}/signup-settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ open }),
  });
  if (!res.ok) throw await apiError(res, "Failed to change signups");
  return Boolean((await res.json()).open);
}

export type AccountLinkRow = {
  id: string;
  purpose: "invite" | "reset";
  email: string;
  role: UserRole;
  name: string;
  created_by_name: string;
  created_at: string;
  expires_at: string;
  used_at: string | null;
  revoked_at: string | null;
  status: "open" | "used" | "expired" | "revoked";
};

/** A link just made. The token is returned this once only; the API keeps
 * just its hash. */
export type IssuedLink = { link: AccountLinkRow; token: string };

/** Hours a new link stays usable. */
export type LinkLifetime = 24 | 72 | 168;

/** Newest first. */
export async function listAccountLinks(): Promise<AccountLinkRow[]> {
  const res = await apiFetch(`${API_BASE_URL}/account-links`, { cache: "no-store" }, { timeoutMs: 8000 });
  if (!res.ok) throw await apiError(res, "Failed to load links");
  return res.json();
}

/** An invite link for one email (works even while signups are off). */
export async function createAccountLink(fields: {
  email: string;
  role: UserRole;
  name?: string;
  expires_hours?: LinkLifetime;
}): Promise<IssuedLink> {
  const res = await apiFetch(`${API_BASE_URL}/account-links`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
  if (!res.ok) throw await apiError(res, "Failed to create the link");
  return res.json();
}

export async function revokeAccountLink(id: string): Promise<AccountLinkRow> {
  const res = await apiFetch(`${API_BASE_URL}/account-links/${id}`, { method: "DELETE" });
  if (!res.ok) throw await apiError(res, "Failed to cancel the link");
  return res.json();
}

/** A one-time link for the user to choose a new password ("Reset
 * password" in Settings). */
export async function createResetLink(userId: string, expiresHours: LinkLifetime = 24): Promise<IssuedLink> {
  const res = await apiFetch(`${API_BASE_URL}/users/${userId}/reset-link`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expires_hours: expiresHours }),
  });
  if (!res.ok) throw await apiError(res, "Failed to create the reset link");
  return res.json();
}

export const CATEGORY_OPTIONS = [
  "Unauthorized Access",
  "Theft or Burglary",
  "Vandalism or Property Damage",
  "Assault or Threat",
  "Harassment",
  "Cybersecurity Breach",
  "Vehicle Collision or Damage",
  "Other",
] as const;

// Kept in sync with the accident_type description in
// apps/bot-service/app/reports/schema.py -- the AI is prompted with that
// exact vocabulary, so a mismatch here means its answer silently fails to
// match any <option> and the dropdown just looks empty.
export const ACCIDENT_TYPE_OPTIONS = [
  "Collision with another vehicle",
  "Rear-end collision",
  "Side impact",
  "Front impact",
  "Parked vehicle hit",
  "Single vehicle accident",
  "Hit-and-run",
  "Scrape / minor contact",
  "Flood / weather damage",
  "Theft / vandalism damage",
  "Other",
] as const;

// Kept in sync with schema.py's field descriptions the same way as
// ACCIDENT_TYPE_OPTIONS above.
export const WEATHER_OPTIONS = ["Clear", "Rainy", "Night/Dark", "Foggy", "Wet Surface"] as const;
export const ROAD_OPTIONS = ["Dry", "Wet", "Slippery", "Gravel", "Uneven"] as const;
export const TRAFFIC_OPTIONS = ["Light", "Moderate", "Heavy", "Stationed"] as const;
export const CLAIM_TYPE_OPTIONS = [
  "Own damage",
  "Third party",
  "Third party fire and theft",
  "Comprehensive",
  "Special case",
] as const;

/** The Annex A Remarks a detected part can carry (printed on its main line);
 * blank is null. The API refuses anything else (400). */
export const DAMAGE_REMARKS = ["Replace", "Repair"] as const;
export type DamageRemarks = (typeof DAMAGE_REMARKS)[number];
/** A select's text as a remark: anything but the two words is blank, so a
 * stray value never reaches the API. */
export const toDamageRemarks = (value: string | null | undefined): DamageRemarks | null =>
  value === "Replace" || value === "Repair" ? value : null;

export type DamageSummaryItem = {
  part: string;
  damage_type?: string | null;
  severity?: string | null;
  photo_reference?: string | null;
  ai_confidence?: string | null;
  human_verified?: boolean;
  repair_required?: boolean;
  oem_part_number?: string | null;
  /** Gemini's native bounding-box convention: [y_min, x_min, y_max, x_max],
   * each normalized 0-1000 with a top-left origin. See fileUrl-adjacent
   * bboxToCss() in StudioApp.tsx for the conversion to CSS percentages. */
  bbox_2d?: number[] | null;
  /** The firm's condition words the AI picked (1-3 of
   * Taxonomy.condition_tokens) that `condition` is built from. Optional
   * here, like the three fields below: parts drafted before 2026-11 have
   * none of them. */
  condition_tokens?: string[];
  /** The Condition as the firm prints it ("Grazed/cut", "Distorted/jammed"),
   * or a whole value (Necessary / Serviceable / Repair). A person may change
   * it; damage_type stays the AI's category. When it is missing, show
   * damage_type (what older cases have). */
  condition?: string | null;
  /** Annex A Remarks for this part's main line; null is blank. */
  remarks?: DamageRemarks | null;
  /** The AI could not see the side on this part's own photo and took it
   * from nearby photos. The API clears it once a person edits the part. */
  side_inferred?: boolean;
};

export type TyreItem = {
  tread_depth_mm?: number | null;
  brand?: string | null;
  size?: string | null;
  condition?: string | null;
};

export type TyreCondition = {
  front_nearside?: TyreItem | null;
  front_offside?: TyreItem | null;
  rear_nearside?: TyreItem | null;
  rear_offside?: TyreItem | null;
};

export type ThirdPartyInfo = {
  plate_number?: string | null;
  make_model?: string | null;
  driver_name?: string | null;
  driver_nric?: string | null;
  driver_contact?: string | null;
  insurer_name?: string | null;
  policy_number?: string | null;
  damage_description?: string | null;
};

export type VehicleInfo = {
  plate_number?: string | null;
  make?: string | null;
  model?: string | null;
  year?: string | null;
  color?: string | null;
  vin?: string | null;
  engine_number?: string | null;
  ownership_type?: string | null;
  driver_name?: string | null;
  driver_contact?: string | null;
  /** Sedan / SUV / Van / ... -- from the vehicle catalogue in taxonomy.py.
   * Absent on reports filed before the catalogue existed. */
  body_type?: string | null;
  odometer_reading?: string | null;
  transmission?: string | null;
  date_of_registration?: string | null;
  paintwork_condition?: string | null;
  steering_condition?: string | null;
  brakes_condition?: string | null;
  tyres?: TyreCondition | null;
  point_of_impact?: string | null;
};

export type PoliceReportDetails = {
  reported_to_police?: boolean;
  police_station?: string | null;
  report_number?: string | null;
  officer_name?: string | null;
  date_reported?: string | null;
  reference_number?: string | null;
};

export type InsuranceDetails = {
  insurer_name?: string | null;
  policy_number?: string | null;
  claim_number?: string | null;
  claim_type?: string | null;
  claim_status?: string | null;
  adjuster_assigned?: string | null;
  workshop_assigned?: string | null;
  estimated_repair_cost?: string | null;
  final_approved_cost?: string | null;
};

export type TimelineEvent = {
  time: string;
  event: string;
};

export type RecommendationsInfo = {
  repair_recommendation?: string | null;
  replacement_recommendation?: string | null;
  inspection_recommendation?: string | null;
  disassembly_required?: boolean;
  follow_up_action?: string | null;
  preventive_action?: string | null;
};

export type AnnexAPartItem = {
  item_no: string;
  part_name: string;
  condition: string;
  action: string;
  qty: number;
  workshop_est: number;
  discount_pct: number;
  adjusted_cost: number;
  is_net_item: boolean;
  oem_part_number?: string | null;
  /** Set on lines added from a suggestion: where it came from ("2 past
   * Honda Vezel cases", "AI suggestion"). Not printed. */
  suggested_from?: string | null;
  /** What past cases' lines were adjusted to per unit (median). A
   * reference for the surveyor only -- never the workshop's estimate. */
  usual_unit_price?: number | null;
  /** The quantity as the surveyor writes it ("1set"), printed instead of
   * qty when set; qty stays a number for the statistics. */
  qty_text?: string | null;
  /** The estimate came from past cases (POST annex-a/prefill), not from a
   * person. Shown as a tag, never printed; cleared when someone types an
   * estimate. */
  prefilled?: boolean;
  /** Where the row came from: "detection" (one unpriced row per detected
   * part), "history" (wording and usual price from past cases), "lookup"
   * (a price lookup, Phase 3), "typed" (a person wrote or priced it). Null
   * on rows from before 2026-11-04; the API derives it on read. Shown as a
   * tag, never printed. The dashboard sends "typed" on any row a person
   * changed, so a later detection re-sync leaves it alone. */
  origin?: AnnexAOrigin | null;
  /** The damage_summary.part this row was made for; null for a line added
   * by hand. How tab 1 counts "Annex A: n lines, m priced" per part. */
  source_part?: string | null;
  /** A kept row whose source_part is no longer detected: the machine never
   * deletes it, the surveyor keeps or removes it. */
  stale?: boolean;
};

export type AnnexAOrigin = "detection" | "history" | "lookup" | "typed";

export type AnnexAInfo = {
  items: AnnexAPartItem[];
  total_workshop_est: number;
  total_adjusted_cost: number;
};

export type AnnexBLabourItem = {
  item_no: string;
  description: string;
  workshop_est: number;
  adjusted_cost: number;
  /** Kept in the data but no longer shown or printed. */
  justification?: string | null;
  /** The line was added from the usual labour lines with its usual price;
   * cleared when someone types a price. Not printed. */
  prefilled?: boolean;
  /** Where a suggested line came from ("3 past Honda Vezel cases"). */
  suggested_from?: string | null;
  /** What past cases adjusted this line to (median). A reference only. */
  usual_price?: number | null;
};

/** GET /reports/{id}/labour-suggestions: the Annex B lines past reports
 * wrote for this make and model (else any), with their usual prices. */
export type LabourSuggestionLine = {
  description: string;
  /** Median adjusted cost in past cases; null when none had one. */
  usual_price: number | null;
  usual_workshop_est: number | null;
  /** How many past cases had this line. */
  cases: number;
  /** Where it comes from, e.g. "3 past Honda Vezel cases". */
  source: string;
};

export type LabourSuggestionGroup = {
  reason: string;
  lines: LabourSuggestionLine[];
};

export type LabourSuggestions = {
  vehicle: string | null;
  groups: LabourSuggestionGroup[];
  /** How many past cases the lines come from. */
  cases_used?: number;
  /** Past-report prices are the firm's price book, for admins and "see
   * all cases" staff: anyone else gets no groups and hidden: true. */
  hidden?: boolean;
};

/** The Cover Page fields that have no home elsewhere on the report
 * (report.cover_page). Dates are free text, printed as typed ("27th
 * February 2023 (10.15am)"). Anything unset prints as "Not recorded". */
export type CoverPageInfo = {
  date_of_request?: string | null;
  requested_by?: string | null;
  /** "C/o Blk 3023A, Ubi Road 1, #01-60, Singapore 408717" */
  requested_by_address?: string | null;
  workshop_address?: string | null;
  date_of_survey?: string | null;
  date_of_dismantle?: string | null;
  date_before_painting?: string | null;
  date_after_painting?: string | null;
  handbrake_condition?: string | null;
  modification?: string | null;
  general_condition?: string | null;
  /** Printed under 5 POINT OF IMPACT. */
  point_of_impact_note?: string | null;
};

/** PUT /reports/{id}/cover-page: merged key by key -- a key left out is
 * untouched, null clears it; tyres are replaced whole. */
export type CoverPageRequest = {
  report_id?: string | null;
  incident_datetime?: string | null;
  cover_page?: CoverPageInfo;
  vehicle_info?: Partial<VehicleInfo>;
  insurance_details?: Partial<InsuranceDetails>;
};

export type CoverPageResponse = {
  report_id: string | null;
  incident_datetime: string | null;
  cover_page: CoverPageInfo;
  vehicle_info: VehicleInfo;
  insurance_details: InsuranceDetails;
};

export type AnnexBInfo = {
  items: AnnexBLabourItem[];
  total_workshop_est: number;
  total_adjusted_cost: number;
};

export type AnnexCInfo = {
  /** Annex A + Annex B, both columns, as the API last worked them out. */
  workshop_total: number;
  adjusted_total: number;
  agreed_lump_sum?: number | null;
  /** Left over from when the report printed GST; no longer computed or shown. */
  gst_rate?: number;
  gst_amount?: number | null;
  total_with_gst?: number | null;
  repair_days?: number | null;
  terms?: string | null;
  without_prejudice?: boolean;
};

export type BolaLiabilityAssessment = {
  scenario_number?: number | null;
  scenario_name?: string | null;
  /** Null until the surveyor records a liability finding. */
  insured_liability_pct: number | null;
  third_party_liability_pct: number | null;
  dispute_status: string;
  apportionment_rationale?: string | null;
  subrogation_prospect?: string | null;
};

/** The signer's name, qualifications, licence and firm aren't here: the API
 * takes them from the signed-in user's profile. */
export type SignOffRequest = {
  signature_data_url?: string;
  /** Left out by the dashboard since the Annex C tab saves these itself
   * (PUT annex-c); the API then keeps annex_c's values. */
  agreed_quantum?: number;
  turnaround_days?: number;
  /** No longer sent (the liability tab went); the API still takes it. */
  liability_opinion?: string;
  remarks?: string;
  /** How the signer is described under their name ("Automotive Engineer Assessor"). */
  signatory_title?: string;
  terms_accepted?: boolean;
};

export type SignOffInfo = {
  prepared_by?: string | null;
  reviewed_by?: string | null;
  approved_by?: string | null;
  surveyor_name?: string | null;
  qualifications?: string | null;
  license_number?: string | null;
  firm_name?: string | null;
  signature_hash?: string | null;
  signature_data_url?: string | null;
  agreed_quantum?: number | null;
  turnaround_days?: number | null;
  liability_opinion?: string | null;
  remarks?: string | null;
  /** Printed under the signer's name; "Automotive Engineer Assessor" when unset. */
  signatory_title?: string | null;
  status?: string;
  signature_date?: string | null;
  terms_accepted?: boolean;
  /** The account that signed (absent on sign-offs from before sign-in). */
  signed_by_user_id?: string | null;
};

export type AIAnalysisInfo = {
  summary?: string | null;
  detected_parts?: string[];
  detected_severity?: string | null;
  confidence_score?: string | null;
  suggested_category?: string | null;
  suggested_repair_notes?: string | null;
};

export type PersonInvolved = {
  name: string;
  role: string;
  department?: string | null;
  contact?: string | null;
};

export type Witness = {
  name: string;
  contact?: string | null;
  statement?: string | null;
  observation_time?: string | null;
};

export type ReportSummary = {
  id: string;
  type: string;
  status: string;
  channel: string;
  created_at: string;
  location: string | null;
  category: string[];
  thumbnail_url: string | null;
  plate_number: string | null;
  vehicle_name: string | null;
  severity_level: string | null;
  damage_count: number;
  accident_type?: string | null;
  estimated_repair_cost?: string | null;
  final_approved_cost?: string | null;
  insurer_name?: string | null;
  workshop_assigned?: string | null;
  claim_type?: string | null;
  disassembly_required?: boolean;
  reported_to_police?: boolean;
  incident_datetime?: string | null;
  /** Null when the case is Unassigned. */
  owner: Owner | null;
  /** "TG-261030-K7M4" on a case a member of the public sent through the
   * Telegram bot (release P6); null or absent otherwise. */
  public_reference?: string | null;
  /** Our Ref (the firm's file number), or null until issued. */
  report_id?: string | null;
  /** cover_page.requested_by, or null. */
  requested_by?: string | null;
  // What the Overview's "Needs attention" card reads (lib/attention.ts).
  // Optional: an older API sends none of them, and the card then only
  // shows what it can tell from the fields above.
  /** An intake draft's stage (lib/intake.ts IntakeStage), exactly as the
   * wizard (GET /intake/{id}) presents it: a run or a filing a restart cut
   * off reads "failed". Null on a filed case. */
  intake_stage?: string | null;
  /** Why a "failed" draft stopped: an AI_OFF_CODES code, timeout,
   * "filing_interrupted" (lib/intake.ts FILING_INTERRUPTED)... Null when
   * the draft has not failed, or failed without a code. */
  intake_error_code?: string | null;
  /** When the idle-draft clean-up will delete this draft (ISO UTC), unless
   * someone works on it first. Null when it won't (a filed case, a busy
   * draft, a public report nobody has taken). */
  draft_deletes_at?: string | null;
  /** Filed cases: Annex A rows still without a workshop estimate (rows for
   * a part no longer detected left out). 0 on a draft. */
  unpriced_rows?: number;
  /** Signed off, reopened since, and not signed off again. */
  reopened?: boolean;
};

export type ReportData = {
  report_id?: string | null;
  company_name?: string | null;
  reporter_name?: string | null;
  reporter_role?: string | null;
  reporter_contact?: string | null;
  reporter_email?: string | null;
  reporter_department?: string | null;
  incident_datetime?: string | null;
  location?: string | null;
  weather_condition?: string | null;
  road_condition?: string | null;
  traffic_condition?: string | null;
  category: string[];
  accident_type?: string | null;
  damaged_parts?: string[];
  severity_level?: string | null;
  vehicle_details?: string | null;
  vehicle_info?: VehicleInfo | null;
  damage_summary?: DamageSummaryItem[];
  description: string;
  people_involved: PersonInvolved[];
  witnesses: Witness[];
  immediate_actions?: string | null;
  police_report?: PoliceReportDetails | null;
  insurance_details?: InsuranceDetails | null;
  third_party_info?: ThirdPartyInfo | null;
  ai_analysis?: AIAnalysisInfo | null;
  timeline?: TimelineEvent[];
  recommendations?: RecommendationsInfo | null;
  sign_off?: SignOffInfo | null;
  /** Absent on reports saved before the Cover Page tab existed. */
  cover_page?: CoverPageInfo | null;
  annex_a?: AnnexAInfo | null;
  annex_b?: AnnexBInfo | null;
  annex_c?: AnnexCInfo | null;
  bola_assessment?: BolaLiabilityAssessment | null;
  reported_to_authorities: boolean;
  authority_reference?: string | null;
  preventive_measures?: string | null;
  additional_comments?: string | null;
};

export type ReportDetail = {
  id: string;
  type: string;
  status: string;
  channel: string;
  created_at: string;
  data: ReportData;
  photo_urls: string[];
  /** Small versions of photo_urls, same order and length. The inspector
   * still shows the full image for the photo being examined; these are for
   * the thumbnail strip. Optional so an older API response still parses. */
  photo_thumb_urls?: string[];
  /** High-efficiency 1600px WebP versions of photo_urls. Used for
   * forensic inspection (1-Up HUD, matrix) to avoid loading multi-megabyte
   * raw camera images that crash browser tabs. */
  photo_preview_urls?: string[];
  pdf_url: string | null;
  /** Who created, edited, signed off and reopened it, oldest first.
   * Optional so an older API response still parses. */
  history?: HistoryEntry[];
  /** Null when the case is Unassigned. */
  owner: Owner | null;
  permissions: CasePermissions;
  /** Sent to admins only, and only while the case is Unassigned. */
  suggested_owner?: SuggestedOwner | null;
  /** Who sent it, on a report from the public (release P6); null or absent
   * otherwise. */
  public_submission?: PublicSubmissionInfo | null;
  /** What the AI last read for the cover fields (from Report.ai_draft):
   * the non-empty values only, so the Cover tab can tag a field "(P03)"
   * while it still says what the AI said. Null when nothing was read.
   * Never printed. */
  ai_read?: AiReadInfo | null;
  /** The photo each ai_read field came from ("P03"), when the draft can
   * tell; {} otherwise. */
  ai_read_photos?: Record<string, string>;
  /** Every AI call made on this case or its draft, newest first, at most 50
   * (the ai_runs table, round 3 B3). Absent from an older API. Shown in
   * the Case record drawer; never printed. */
  ai_runs?: AiRunSummary[];
  /** The last "read" run that finished, for tab 1's status line. Null when
   * the AI never read this case; absent from an older API. */
  ai_last_read?: AiLastRead | null;
};

export type AiReadInfo = {
  vehicle_info?: Record<string, string | number | null | undefined> | null;
  incident_datetime?: string | null;
};

// The AI run log (round 3, B3): one row per Gemini call, written by the
// API. GET /reports/{id} sends the case's rows; GET /ai-usage (admins)
// sums them. The rows never hold an API key or anything a member of the
// public typed; error_text is scrubbed of digits and plates by the API.

/** What the call was for. Kept open (string) so a kind a newer API adds
 * still shows, under its raw name. */
export type AiRunKind = "sort" | "read" | "redraft" | "annex_suggest" | "redetect" | "lookup" | "second_look";
export type AiRunStatus = "running" | "ok" | "failed" | "refused" | "aborted";
export type AiRunChannel = "dashboard" | "telegram" | "whatsapp" | "system";
export type AiKeySource = "own" | "company" | "none";

export type AiRunSummary = {
  id: string;
  kind: AiRunKind | (string & {});
  channel: AiRunChannel | (string & {});
  started_at: string;
  /** Null while the run is still going (or the server died with it). */
  finished_at: string | null;
  status: AiRunStatus | (string & {});
  /** The existing codes (ai_key_missing, ai_quota_used, timeout...) on a
   * failed or refused run; null otherwise. */
  error_code: string | null;
  /** The scrubbed error, when the API sends it with the per-case list
   * (the contract lists only error_code there; shown when present). */
  error_text?: string | null;
  /** The model that answered, null when none did. */
  model_used: string | null;
  /** P-labels of the photos sent ("P03"), or {count} for a sort batch,
   * or null when the run sent no photos (a redraft, a price lookup). */
  photos_sent: string[] | { count: number } | null;
  /** How many photos went, worked out by the API from photos_sent. */
  photos_count: number | null;
  /** Damaged parts the run found; null for a run that doesn't find parts. */
  parts_found: number | null;
  /** Who pressed the button; null for the bot's own runs (system). */
  actor_name: string | null;
  key_source: AiKeySource | (string & {});
  /** Last 4 characters of the key used, when the API sends them. Never
   * more of the key than that. */
  key_last4?: string | null;
};

export type AiLastRead = {
  at: string;
  photos_read: number;
  photos_total: number;
  /** Null when the run never finished (no finished_at). */
  seconds: number | null;
  model: string | null;
};

/** GET /ai-usage?days=N (admins): the run log summed over the last N days.
 * A count the API can't give (tokens the SDK didn't report) is null, and
 * the screen shows "—", never 0. */
export type AiUsage = {
  days?: number;
  runs: number;
  failed: number;
  failed_by_code: Record<string, number>;
  /** How many model tries hit a 429 and waited, and for how long in all. */
  waits_429: number;
  waited_s: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
  by_key: Array<{ key_source: AiKeySource | (string & {}); key_last4: string | null; runs: number; failed: number }>;
  by_kind: Record<string, number>;
  by_user: Array<{ name: string | null; runs: number; failed: number }>;
  /** Newest 50 failed or refused runs. plate_or_ref comes from the case's
   * own data (plate, else Our Ref / id), never from a public sender. */
  failures: Array<{
    at: string;
    report_id: string;
    plate_or_ref: string | null;
    kind: AiRunKind | (string & {});
    error_code: string | null;
    actor_name: string | null;
  }>;
};

/** A failed run's error code in a few words, for the run log (the Case
 * record drawer, Settings -> AI usage). Not AI_OFF_MESSAGES: those tell the
 * viewer what to do about their own key, and a logged run may be someone
 * else's. A code not listed here shows as itself, never as a guess. */
const AI_RUN_ERROR_LABELS: Record<string, string> = {
  ai_key_missing: "No AI key to use",
  ai_key_invalid: "AI key refused by Google",
  ai_quota_used: "AI key quota used up",
  ai_key_unreadable: "AI key couldn't be read",
  ai_account_off: "Account disabled",
  timeout: "Timed out",
  rate_limited: "Too many requests",
  // Every model left had used up its free daily allowance on the key (tries
  // skipped, "day_spent", or refused for it); comes back at midnight US Pacific.
  day_allowance_used: "Free daily allowance used up",
};

export function aiRunErrorLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return AI_RUN_ERROR_LABELS[code] || code;
}

/** Each run kind in plain words. An unknown kind shows under its raw name. */
const AI_RUN_KIND_LABELS: Record<string, string> = {
  sort: "Sort photos",
  read: "Read case",
  redraft: "Re-draft",
  annex_suggest: "Annex A suggestions",
  redetect: "Re-run detection",
  lookup: "Price lookup",
  second_look: "Second look",
};

export function aiRunKindLabel(kind: string | null | undefined): string {
  if (!kind) return "—";
  return AI_RUN_KIND_LABELS[kind] || kind;
}

/** Which key a run used, in words: "company key", "own key …1a2b", "no key".
 * Never more of the key than its last 4 characters, which is all the API
 * sends. */
export function aiKeySourceLabel(source: string | null | undefined, last4?: string | null): string {
  if (source === "company") return last4 ? `company key …${last4}` : "company key";
  if (source === "own") return last4 ? `own key …${last4}` : "own key";
  if (source === "none") return "no key";
  return source || "—";
}

export const AI_USAGE_DAYS = [7, 30, 90] as const;
export type AiUsageDays = (typeof AI_USAGE_DAYS)[number];

export function isAiUsageDays(value: unknown): value is AiUsageDays {
  return typeof value === "number" && (AI_USAGE_DAYS as readonly number[]).includes(value);
}

/** Admin only: the API refuses everyone else (403, like /system/info). */
export async function getAiUsage(days: AiUsageDays = 30): Promise<AiUsage> {
  const res = await apiFetch(`${API_BASE_URL}/ai-usage?days=${days}`, { cache: "no-store" }, { timeoutMs: 8000 });
  if (!res.ok) throw await apiError(res, "Failed to load AI usage");
  return res.json();
}

export type HistoryEntry = {
  /** "assigned": the owner changed; detail is "Assigned to Name (@username)" or "Unassigned".
   * Public reports (release P6) add taken, returned_to_inbox, discarded,
   * expired, withdrawn and withdraw_requested; their detail carries the
   * reference, never a name or phone. */
  action: "created" | "edited" | "finalized" | "signed_off" | "reopened" | "assigned" | string;
  /** Null for entries with no account behind them (Telegram, or before sign-in). */
  user_name: string | null;
  detail: string | null;
  at: string;
};

export type AnalyticsSummary = {
  total_incidents: number;
  pending_review: number;
  signed_off: number;
  high_severity: number;
  category_counts: Record<string, number>;
  severity_counts: Record<string, number>;
  damaged_parts_frequency: Record<string, number>;
  recent_activity: Array<{
    id: string;
    created_at: string;
    channel: string;
    status: string;
    location: string;
    reporter: string;
    category: string[];
  }>;
  /** Null until at least one report has been signed off / has a rated
   * damage item -- see get_analytics_summary in api/main.py. Never a
   * placeholder number. */
  avg_resolution_time: string | null;
  ai_confidence_avg: string | null;
};

export const EMPTY_ANALYTICS_SUMMARY: AnalyticsSummary = {
  total_incidents: 0,
  pending_review: 0,
  signed_off: 0,
  high_severity: 0,
  category_counts: {},
  severity_counts: {},
  damaged_parts_frequency: {},
  recent_activity: [],
  avg_resolution_time: null,
  ai_confidence_avg: null,
};

/** No scope: the API's default (All for admins, My cases for everyone
 * else). Asking a plain user's list for anything but "mine" is refused
 * (ApiError code scope_not_allowed). */
export async function listReports(scope?: CaseScope): Promise<ReportSummary[]> {
  const query = scope ? `?scope=${scope}` : "";
  const res = await apiFetch(`${API_BASE_URL}/reports${query}`, { cache: "no-store" }, { timeoutMs: 8000 });
  if (!res.ok) throw await apiError(res, "Failed to load cases");
  return res.json();
}

/** The scope for the firm-wide screens (Overview, Analytics): "all" for
 * anyone who sees every case, since the API's default is My cases for
 * ticked staff. Undefined (the default: their own cases) for everyone
 * else. */
export async function firmWideScope(): Promise<CaseScope | undefined> {
  const me = await getCurrentUser();
  return me?.can_view_all_cases ? "all" : undefined;
}

/** Null when there is no such case, and equally when it isn't one the
 * signed-in user may open: the API answers both with the same 404. */
export async function getReport(id: string): Promise<ReportDetail | null> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${id}`, { cache: "no-store" }, { timeoutMs: 8000 });
  // Resilient 404 handling: return null so Next.js notFound() renders the branded 404 page instead of a 500 error
  if (res.status === 404) return null;
  if (!res.ok) throw await apiError(res, `Failed to load case ${id}`);
  const body = await res.json();
  if (body && typeof body === "object" && "error" in body) return null;
  return body as ReportDetail;
}

/** Admin: give a case to someone, or (null) make it Unassigned. Refused
 * for a disabled account (owner_inactive) and for a draft (owner_draft). */
export async function setCaseOwner(reportId: string, ownerId: string | null): Promise<{ owner: Owner | null }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${reportId}/owner`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ owner_id: ownerId }),
  });
  if (!res.ok) throw await apiError(res, "Failed to change the owner");
  return res.json();
}

/** Admin: moves everything one person owns that isn't signed off (plus their
 * signed-off cases with includeSigned) to toUserId, or makes it Unassigned
 * (null). Unfinished drafts stay. Each case gets an "assigned" history
 * entry. Refused for a disabled target (owner_inactive). */
export async function reassignCases(
  fromUserId: string,
  toUserId: string | null,
  includeSigned = false
): Promise<{ moved: number }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/reassign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ from_user_id: fromUserId, to_user_id: toUserId, include_signed: includeSigned }),
  });
  if (!res.ok) throw await apiError(res, "Failed to move the cases");
  return res.json();
}

// createReport, analyzeReportPhotos and analyzeExistingReportPhotos went on
// 2026-11-04: cases are only created by the intake wizard (POST /intake)
// and the bots now, and the AI reads photos in the wizard, not on the edit
// form.

/** Attaches photos to a case without the AI (release P4: for people whose
 * AI is off). They are saved to the temp dir and come back as
 * temp_photo_paths, which PUT /reports/{id} then moves into the case on save. Refusals keep their code: disk_full
 * (507), rate_limited (429, accounts without staff access: a number of
 * photos an hour), not_allowed (403). */
export async function uploadReportPhotos(reportId: string, photos: File[]): Promise<{ temp_photo_paths: string[] }> {
  const formData = new FormData();
  for (const photo of photos) formData.append("photos", photo);
  const res = await apiFetch(`${API_BASE_URL}/reports/${reportId}/upload-photos`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) throw await apiError(res, "Failed to attach the photos");
  const result = await res.json();
  return { temp_photo_paths: Array.isArray(result?.temp_photo_paths) ? result.temp_photo_paths : [] };
}

/** PUT /reports/{id}: the API merges the sent keys over the record
 * (exclude_unset), so the edit form sends only what it edits (description,
 * location, police, insurer, people, witnesses, third party, photos) and
 * never the sections other tabs own. */
export async function updateReport(
  id: string,
  payload: Partial<ReportData>,
  tempPhotoPaths: string[] = []
): Promise<{ id: string; status: string; pdf_url: string }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ draft: payload, temp_photo_paths: tempPhotoPaths }),
  });
  if (!res.ok) throw await apiError(res, "Failed to update report");
  return res.json();
}

// Deliberately not updateReport() for the parts: that replaces the whole
// report and re-renders the PDF, which is far too heavy for a checkbox
// toggle. The damage-items endpoints change one line at a time.

/** PATCH /reports/{id}/damage-items/{i}: what tab 1's detected-parts table
 * changes on one line. `part` must be a taxonomy part; `photo_reference`
 * "P07" or null (the AI box is dropped when the photo changes).
 * `condition` must be the firm's words joined with "/" or one whole value
 * (400 otherwise); any change clears side_inferred. */
export type DamageItemPatch = {
  part?: string;
  damage_type?: string | null;
  severity?: string | null;
  photo_reference?: string | null;
  human_verified?: boolean;
  oem_part_number?: string | null;
  condition?: string | null;
  remarks?: DamageRemarks | null;
};

/** POST /reports/{id}/damage-items: what the add row sends. */
export type NewDamageItem = {
  part: string;
  damage_type?: string | null;
  condition?: string | null;
  remarks?: DamageRemarks | null;
  photo_reference?: string | null;
};

/** Every damage-items call answers with the parts as saved and the Annex A
 * the API re-synced to them (priced and typed rows kept, stale ones
 * flagged), plus the Annex C totals, so the page updates without a
 * reload. */
export type DamageItemsResponse = {
  damage_summary: DamageSummaryItem[];
  /** The plain part names, kept in step with damage_summary (the page
   * falls back to them when damage_summary is missing). */
  damaged_parts?: string[];
  annex_a: AnnexAInfo;
  annex_c: AnnexCInfo;
};

export async function patchDamageItem(reportId: string, itemIndex: number, patch: DamageItemPatch): Promise<DamageItemsResponse> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${reportId}/damage-items/${itemIndex}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw await apiError(res, "Failed to update the part");
  return res.json();
}

/** POST /reports/{id}/damage-items: a part the surveyor adds by hand
 * (appended, verified). */
export async function addDamageItem(reportId: string, body: NewDamageItem): Promise<DamageItemsResponse> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${reportId}/damage-items`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await apiError(res, "Failed to add the part");
  return res.json();
}

/** DELETE /reports/{id}/damage-items/{i}: the part is remembered as
 * dismissed, so a later detection doesn't bring it back. */
export async function removeDamageItem(reportId: string, itemIndex: number): Promise<DamageItemsResponse> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${reportId}/damage-items/${itemIndex}`, { method: "DELETE" });
  if (!res.ok) throw await apiError(res, "Failed to remove the part");
  return res.json();
}

export async function reopenReport(id: string): Promise<{ id: string; status: string }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${id}/reopen`, { method: "POST" });
  if (!res.ok) throw await apiError(res, "Failed to reopen report");
  return res.json();
}

/** Signs as the signed-in user, with their own profile (My settings) --
 * the API ignores any name, licence or firm in the request. */
export async function signOffReport(
  id: string,
  payload: SignOffRequest = {}
): Promise<{ id: string; status: string; pdf_url: string; sign_off?: SignOffInfo }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${id}/sign-off`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw await apiError(res, "Failed to sign off report");
  return res.json();
}

export async function renderReportPdf(id: string): Promise<{ id: string; status: string; pdf_url: string }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${id}/render-pdf`, {
    method: "POST",
  });
  if (!res.ok) throw await apiError(res, "Failed to render PDF");
  return res.json();
}

// ---------------------------------------------------------------------------
// Public Inbox (release P6): reports members of the public send through the
// company Telegram bot (bot-service app/api/inbox.py, app/intake/public.py).
// Each is a draft with no owner (channel "telegram_public", status "intake")
// until someone takes it; nobody can edit it or run AI on it before then.
// Reports nobody takes are deleted 30 days after they were sent.
//
// What the sender typed is their own words and was not checked: show it as
// plain text, marked "as given", never as links or HTML. The phone number is
// the one exception: it came from a contact shared from their own Telegram.

export const PUBLIC_CHANNEL = "telegram_public";

/** Where a case came in from (Report.channel), in words. */
export function channelLabel(channel?: string | null): string {
  if (channel === "telegram") return "Telegram Bot (@carlink_bot)";
  if (channel === PUBLIC_CHANNEL) return "Telegram (public)";
  if (channel === "whatsapp") return "WhatsApp";
  if (channel === "manual") return "Surveyor Console";
  return channel || "Not recorded";
}

/** A report from the public that nobody has taken yet: it lives in the
 * inbox (/inbox/{id}), not in the wizard or on a case page. */
export function isUntakenPublic(r: { channel?: string | null; status: string; owner: Owner | null }): boolean {
  return r.channel === PUBLIC_CHANNEL && r.status === "intake" && !r.owner;
}

/** new: nobody has taken it. taken: someone has, and it isn't filed yet.
 * filed: it is a case now. Worked out by the API from the report's owner
 * and status. */
export type InboxState = "new" | "taken" | "filed";
export type InboxView = "new" | "taken";

/** sg_mobile: +65 8/9xxx xxxx. sg_other: another +65 number (a landline).
 * foreign: anything else. */
export type PhoneKind = "sg_mobile" | "sg_other" | "foreign";

/** What the sender said they are. */
export type PublicRole = "driver" | "owner" | "workshop" | "other";

export const PUBLIC_ROLE_LABELS: Record<PublicRole, string> = {
  driver: "Driver",
  owner: "Car owner",
  workshop: "Workshop",
  other: "Someone else",
};

/** The API's own words for the role (role_label) when it sent them; else
 * "Driver" for "driver", and anything else as sent. */
export function publicRoleLabel(sender: { role?: string | null; role_label?: string | null } | null | undefined): string {
  if (sender?.role_label) return sender.role_label;
  const role = sender?.role;
  if (!role) return "";
  return (PUBLIC_ROLE_LABELS as Record<string, string>)[role] ?? role;
}

/** A row of GET /inbox: the phone masked ("+65 •••• 4567"). */
export type InboxSenderSummary = {
  name: string;
  role: PublicRole | (string & {});
  /** "Car owner": the role in words. */
  role_label?: string;
  phone_masked: string;
  phone_kind: PhoneKind | (string & {});
  telegram_username: string | null;
};

export type InboxItem = {
  id: string;
  /** "TG-261030-K7M4". */
  reference: string;
  submitted_at: string;
  /** When it is deleted if nobody has taken it (submitted_at + 30 days). */
  expires_at: string;
  sender: InboxSenderSummary;
  /** Null when the sender wasn't sure. */
  plate: string | null;
  photo_count: number;
  statement_preview: string;
  /** The sender sent /withdraw after it was taken. */
  withdraw_requested: boolean;
  /** Who took it (Taken tab); null on the New tab. */
  owner: Owner | null;
  taken_at: string | null;
};

export type InboxList = {
  items: InboxItem[];
  counts: { new: number; taken: number };
  /** max_open: how many untaken reports the bot takes in all. accepting:
   * false while the inbox is full, so the bot is turning the public away. */
  limits: { max_open: number; accepting: boolean };
};

/** GET /inbox/count: for the sidebar badge, the Cases chip and the Command
 * Center card. expiring_soon: untaken reports close to their 30 days. */
export type InboxCount = { new: number; oldest_submitted_at: string | null; expiring_soon: number };

/** The sender in full (GET /inbox/{id}, and a case's public_submission).
 * phone is E.164 ("+6591234567"); phone_display is for reading
 * ("+65 9123 4567"). The telegram_* fields are cleared once the case is
 * filed. */
export type PublicSender = {
  name: string;
  role: PublicRole | (string & {});
  role_label?: string;
  phone: string;
  phone_display: string;
  phone_kind: PhoneKind | (string & {});
  telegram_username?: string | null;
  telegram_name?: string | null;
};

/** A photo the sender sent (the intake photo's URLs, see lib/intake.ts). */
export type InboxPhoto = {
  id: string;
  label?: string;
  name?: string;
  size_bytes?: number;
  url: string;
  thumb_url: string;
  preview_url: string;
};

/** The account that took a report: an account, or its name already in words. */
export type TakenBy = Pick<Owner, "id" | "name" | "username"> | string;

export function takenByLabel(by: TakenBy | null | undefined): string | null {
  if (!by) return null;
  if (typeof by === "string") return by;
  return personLabel(by);
}

/** What the signed-in user may do with one inbox report, as the API worked
 * it out; it checks again on every call. */
export type InboxPermissions = { take: boolean; assign: boolean; release: boolean; discard: boolean };

/** GET /inbox/{id}. */
export type InboxDetail = {
  id: string;
  reference: string;
  state: InboxState;
  submitted_at: string;
  expires_at: string;
  sender: PublicSender;
  plate: string | null;
  /** In their words, or "Location pin: 1.3521, 103.8198". */
  when_where: string | null;
  /** Their own words, verbatim. */
  statement: string;
  photos: InboxPhoto[];
  /** How many of the photos were forwarded from another chat. */
  forwarded_count?: number;
  /** When the sender sent /withdraw on it after it was taken; null if not. */
  withdraw_requested_at: string | null;
  /** Their other reports still here (by Telegram account or phone). */
  same_sender: { id: string; reference: string; state: InboxState }[];
  owner: Owner | null;
  taken_at?: string | null;
  taken_by?: TakenBy | null;
  permissions: InboxPermissions;
};

/** A case's public_submission (GET /intake/{id}, GET /reports/{id}): who sent
 * it, kept with the case. The same fields as GET /inbox/{id}'s, less the
 * photos (the case has them) and the actions. */
export type PublicSubmissionInfo = {
  reference: string;
  state?: InboxState;
  submitted_at: string;
  expires_at?: string | null;
  sender: PublicSender;
  plate?: string | null;
  when_where?: string | null;
  statement?: string | null;
  forwarded_count?: number;
  withdraw_requested_at?: string | null;
  taken_at?: string | null;
  taken_by?: TakenBy | null;
  filed_at?: string | null;
};

export const DISCARD_REASONS = ["spam", "duplicate", "test", "not_vehicle", "other"] as const;
export type DiscardReason = (typeof DISCARD_REASONS)[number];

export const DISCARD_REASON_LABELS: Record<DiscardReason, string> = {
  spam: "Spam",
  duplicate: "Duplicate",
  test: "Test",
  not_vehicle: "Not a vehicle report",
  other: "Other",
};

/** The note goes in the case history: a few words, no personal details. */
export const DISCARD_NOTE_MAX = 120;

export type DiscardRequest = {
  reason: DiscardReason;
  /** "TG-261029-Q8T2", with reason "duplicate". */
  duplicate_of?: string;
  note?: string;
  /** Block the sender's Telegram account for 90 days (on by default for spam). */
  block_sender?: boolean;
};

/** Phone links for an E.164 number: tel: for Call, wa.me for WhatsApp (not
 * offered for a Singapore landline). Null for anything that isn't a plain
 * +digits number, so a stored value is never turned into some other link. */
export function phoneLinks(phone: string | null | undefined, kind?: string | null): { tel: string; whatsapp: string | null } | null {
  const e164 = (phone || "").replace(/[\s-]/g, "");
  if (!/^\+[1-9]\d{6,14}$/.test(e164)) return null;
  return {
    tel: `tel:${e164}`,
    whatsapp: kind === "sg_other" ? null : `https://wa.me/${e164.slice(1)}`,
  };
}

/** The view to list (default New). Refused for anyone who doesn't see all
 * cases (403 not_allowed). */
export async function listInbox(view: InboxView = "new"): Promise<InboxList> {
  const res = await apiFetch(`${API_BASE_URL}/inbox?view=${view}`, { cache: "no-store" }, { timeoutMs: 8000 });
  if (!res.ok) throw await apiError(res, "Failed to load the inbox");
  const body = await res.json();
  return {
    items: Array.isArray(body?.items) ? body.items : [],
    counts: { new: Number(body?.counts?.new) || 0, taken: Number(body?.counts?.taken) || 0 },
    limits: { max_open: Number(body?.limits?.max_open) || 0, accepting: body?.limits?.accepting !== false },
  };
}

export async function countInbox(): Promise<InboxCount> {
  const res = await apiFetch(`${API_BASE_URL}/inbox/count`, { cache: "no-store" }, { timeoutMs: 8000 });
  if (!res.ok) throw await apiError(res, "Failed to count the inbox");
  const body = await res.json();
  return {
    new: Number(body?.new) || 0,
    oldest_submitted_at: typeof body?.oldest_submitted_at === "string" ? body.oldest_submitted_at : null,
    expiring_soon: Number(body?.expiring_soon) || 0,
  };
}

/** Null for 404: no such report, not a public one (inbox_gone), or not one
 * this account may open. */
export async function getInboxItem(id: string): Promise<InboxDetail | null> {
  const res = await apiFetch(`${API_BASE_URL}/inbox/${encodeURIComponent(id)}`, { cache: "no-store" }, { timeoutMs: 8000 });
  if (res.status === 404) return null;
  if (!res.ok) throw await apiError(res, "Failed to load the report");
  return res.json();
}

async function inboxPost<T>(id: string, action: string, body: unknown, fallback: string): Promise<T> {
  const res = await apiFetch(`${API_BASE_URL}/inbox/${encodeURIComponent(id)}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) throw await apiError(res, fallback);
  return res.json();
}

/** Takes it as your own draft. 409 inbox_taken (someone was quicker),
 * draft_limit, inbox_filed; 404 inbox_gone. */
export function takeSubmission(id: string): Promise<{ id: string; owner: Owner | null }> {
  return inboxPost(id, "take", {}, "Failed to take the report");
}

/** Admin: gives an untaken report to someone vetted. 409 owner_inactive,
 * owner_unvetted, draft_limit, inbox_taken. */
export function assignSubmission(id: string, ownerId: string): Promise<{ id: string; owner: Owner | null }> {
  return inboxPost(id, "assign", { owner_id: ownerId }, "Failed to assign the report");
}

/** Back to the inbox (its owner or an admin). Its 30 days don't restart.
 * 409 ai_running, inbox_finalizing, inbox_changed. */
export function releaseSubmission(id: string): Promise<{ id: string; owner: null }> {
  return inboxPost(id, "release", {}, "Failed to return the report to the inbox");
}

/** Deletes it with its photos and the sender's details for good; the case
 * history keeps the reference and the reason. 409 inbox_changed,
 * ai_running, inbox_filed. */
export function discardSubmission(id: string, body: DiscardRequest): Promise<{ deleted: string | boolean }> {
  return inboxPost(id, "discard", body, "Failed to discard the report");
}

// ---------------------------------------------------------------------------
// Annex A and past reports (apps/bot-service/app/api/parts.py). Past report
// spreadsheets are imported on the Knowledge page; their Annex A lines
// suggest what a new case's Annex A needs and what each line usually costs.
// Nothing is written into a case until the surveyor saves its Annex A.

/** One line past cases wrote for a damaged part. */
export type AnnexSuggestionLine = {
  name: string;
  family?: string | null;
  qty: number;
  /** Median of what past lines were adjusted to per unit; null when none
   * of them had a price. */
  usual_unit_price: number | null;
  /** How many cases the usual price comes from. */
  price_from_cases?: number;
  price_range?: [number, number] | null;
  /** How many past cases had this line (0 for AI lines). */
  cases: number;
  /** Past cases always listed it among the nett items. */
  nett?: boolean;
  conditions?: string[];
  /** "fitting": clips, seals and the like renewed on refit. */
  kind: "part" | "fitting";
  last_seen?: string | null;
  /** "AI suggestion" on lines from askAiAnnexSuggestions. */
  source?: string;
};

export type AnnexSuggestionGroup = {
  part: string;
  /** "damaged", "often replaced with Boot Lid (2 of 2 cases)" or
   * "AI suggestion (no past case)". */
  reason: string;
  lines: AnnexSuggestionLine[];
};

export type AnnexSuggestions = {
  /** "past Honda Vezel cases", "past Honda cases" or "all imported cases". */
  scope: string;
  cases_used: number;
  vehicle: string | null;
  damaged_parts: string[];
  /** Damaged parts no past case covers. */
  without_history: string[];
  /** Lines already in the case's Annex A are left out. */
  groups: AnnexSuggestionGroup[];
  /** Past-report prices are for staff only: no groups, cases_used 0. */
  hidden?: boolean;
};

export async function getAnnexSuggestions(reportId: string): Promise<AnnexSuggestions> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${reportId}/annex-suggestions`, { cache: "no-store" }, { timeoutMs: 15000 });
  if (!res.ok) throw await apiError(res, "Failed to load suggested lines");
  return res.json();
}

/** Gemini's lines for parts no past case covers: names and quantities only,
 * no prices. Spends AI quota, so only on the surveyor's click. */
export async function askAiAnnexSuggestions(reportId: string, parts: string[]): Promise<{ groups: AnnexSuggestionGroup[] }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${reportId}/annex-suggestions/ai`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parts }),
  });
  if (!res.ok) throw await apiError(res, "The AI couldn't suggest lines");
  return res.json();
}

/** Saves the whole Annex A table. The API numbers the rows, works out each
 * adjusted cost and the Annex A/C totals, and drops rows with no part name;
 * a signed-off case is refused (409). The PDF is rendered again on the next
 * download. */
export async function saveAnnexA(
  reportId: string,
  items: AnnexAPartItem[]
): Promise<{ annex_a: AnnexAInfo; annex_c: AnnexCInfo }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${reportId}/annex-a`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  if (!res.ok) throw await apiError(res, "Failed to save Annex A");
  return res.json();
}

/** Fills the workshop estimates on Annex A from past cases of the same
 * make and model (POST /reports/{id}/annex-a/prefill). Rows a person
 * priced are kept; prefilled and unpriced rows are replaced. The whole
 * table comes back, saved. */
export async function prefillAnnexA(
  reportId: string
): Promise<{ annex_a: AnnexAInfo; annex_c: AnnexCInfo; filled: number; unpriced: number }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${reportId}/annex-a/prefill`, { method: "POST" }, { timeoutMs: 30000 });
  if (!res.ok) throw await apiError(res, "Failed to fill prices from past cases");
  return res.json();
}

/** Saves the whole Annex B table (PUT /reports/{id}/annex-b). The API
 * numbers the rows, drops rows with no job description, keeps the adjusted
 * costs as typed and works out the Annex B/C totals. 409 once signed off. */
export async function saveAnnexB(
  reportId: string,
  items: AnnexBLabourItem[]
): Promise<{ annex_b: AnnexBInfo; annex_c: AnnexCInfo }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${reportId}/annex-b`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  if (!res.ok) throw await apiError(res, "Failed to save Annex B");
  return res.json();
}

/** The two figures the Annex C letter takes from the surveyor: the
 * contract lump sum and the working days. Null clears one. */
export type AnnexCRequest = { agreed_lump_sum?: number | null; repair_days?: number | null };

export async function saveAnnexC(reportId: string, body: AnnexCRequest): Promise<{ annex_c: AnnexCInfo }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${reportId}/annex-c`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await apiError(res, "Failed to save Annex C");
  return res.json();
}

/** The Annex B lines past reports wrote for this vehicle, with usual prices. */
export async function getLabourSuggestions(reportId: string): Promise<LabourSuggestions> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${reportId}/labour-suggestions`, { cache: "no-store" }, { timeoutMs: 15000 });
  if (!res.ok) throw await apiError(res, "Failed to load the usual labour lines");
  return res.json();
}

/** Saves the Cover Page fields (PUT /reports/{id}/cover-page). Only the
 * keys sent change; null clears a value. 409 once signed off. */
export async function saveCoverPage(reportId: string, body: CoverPageRequest): Promise<CoverPageResponse> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${reportId}/cover-page`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await apiError(res, "Failed to save the cover page");
  return res.json();
}

/** One imported past report. */
export type PastReportCase = {
  /** The firm's case reference, e.g. "CL 12330". */
  ref: string;
  /** Admins only; empty for everyone else. */
  plate: string;
  make: string;
  model: string;
  /** ISO date of the survey, when the report gave one. */
  surveyed: string | null;
  lines: number;
  /** Admins only; empty for everyone else. */
  file: string;
};

/** One line wording for one make and model, across the past reports. */
export type PartsHistoryLine = {
  item: string;
  family: string | null;
  make: string;
  model: string;
  cases: number;
  /** Median of what the lines were adjusted to per unit. */
  usual_unit_price: number | null;
  min_price: number | null;
  max_price: number | null;
  /** Clips, seals and the like renewed on refit. */
  fitting: boolean;
  last_seen: string | null;
};

export type PartsHistory = {
  cases: PastReportCase[];
  lines: PartsHistoryLine[];
  /** True when prices are for staff only (admins and accounts that see all
   * cases) and this account isn't one: the lists come back empty. */
  hidden: boolean;
};

export async function getPartsHistory(): Promise<PartsHistory> {
  const res = await apiFetch(`${API_BASE_URL}/parts-history`, { cache: "no-store" }, { timeoutMs: 15000 });
  if (!res.ok) throw await apiError(res, "Failed to load the parts price history");
  const body = await res.json();
  return { cases: body?.cases ?? [], lines: body?.lines ?? [], hidden: Boolean(body?.hidden) };
}

export type PastReportImportResult =
  | { file: string; ref: string; make: string; model: string; lines: number }
  | { file: string; error: string };

/** Upload limit per import (the API refuses more). */
export const MAX_PAST_REPORT_FILES = 50;

/** Admin only. Called straight from the browser, like the intake photo
 * uploads (lib/intake.ts): files don't suit a server action's body limit.
 * Each file is one past report; one that can't be read is listed with the
 * reason and the rest still import. */
export async function importPastReports(files: File[]): Promise<{ results: PastReportImportResult[] }> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  const res = await apiFetch(`${API_BASE_URL}/parts-history/import`, { method: "POST", body: form });
  if (res.status === 413) throw new Error("Too large for one upload. Import fewer files at a time.");
  if (!res.ok) throw await apiError(res, "Failed to import the reports");
  return res.json();
}

/** Admin only. Removes every line imported from that report. */
export async function deletePastReport(ref: string): Promise<{ ok: boolean; lines: number }> {
  const res = await apiFetch(`${API_BASE_URL}/parts-history/${encodeURIComponent(ref)}`, { method: "DELETE" });
  if (!res.ok) throw await apiError(res, "Failed to remove the report");
  return res.json();
}

/** Over the same cases as listReports(scope), with the same default and
 * the same refusal. */
export async function getAnalyticsSummary(scope?: CaseScope): Promise<AnalyticsSummary> {
  const query = scope ? `?scope=${scope}` : "";
  const res = await apiFetch(`${API_BASE_URL}/analytics/summary${query}`, { cache: "no-store" }, { timeoutMs: 8000 });
  if (!res.ok) throw await apiError(res, "Failed to load analytics");
  return res.json();
}

/** GET /settings. The three public-report settings (release P6) come back
 * to admins only; everyone else gets company_name alone. */
export type AppSettings = {
  company_name: string;
  /** "6123 4567": the number the Telegram bot gives the public to call.
   * Empty: the bot leaves every "call us on ..." out. */
  public_contact_phone?: string;
  /** Who to ask about personal data ("dpo@firm.sg"), shown on /privacy. */
  dpo_contact?: string;
  /** An admin has confirmed the company AI key is on a billed Google Cloud
   * project. Until then AI on public reports runs only on the presser's own
   * billed key. */
  company_key_billed?: boolean;
};

export const DEFAULT_APP_SETTINGS: AppSettings = {
  company_name: "Carlink Loss Adjuster",
};

/** The settings as the screens use them. app_settings stores text, so a
 * tick may come back as "true"/"1"/"" rather than a boolean; the P6 keys are
 * left out when the API didn't send them (not an admin, or an older API). */
function normalizeSettings(body: unknown): AppSettings {
  const raw = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
  const settings: AppSettings = {
    company_name: typeof raw.company_name === "string" ? raw.company_name : DEFAULT_APP_SETTINGS.company_name,
  };
  if (typeof raw.public_contact_phone === "string") settings.public_contact_phone = raw.public_contact_phone;
  if (typeof raw.dpo_contact === "string") settings.dpo_contact = raw.dpo_contact;
  if ("company_key_billed" in raw) {
    const v = raw.company_key_billed;
    settings.company_key_billed = v === true || v === 1 || (typeof v === "string" && ["true", "1", "yes"].includes(v.trim().toLowerCase()));
  }
  return settings;
}

/** Live read-only facts about the running deployment. Never contains the
 * API key or bot token themselves -- only whether they're configured. */
export type SystemInfo = {
  ai: {
    model_chain: string[];
    min_call_interval_seconds: number;
    request_timeout_seconds: number;
    api_key_configured: boolean;
  };
  channels: {
    telegram_configured: boolean;
    whatsapp_configured: boolean;
    reports_by_channel: Record<string, number>;
  };
  storage: { reports: number; photos: number; pdfs: number; bytes_used: number; storage_dir: string };
  database: { engine: string };
  auth: { configured: boolean };
  /** Whether this server can read people's own AI keys (the secret that
   * seals them). `detail` says what's wrong and how to fix it. Absent from
   * an API older than release P3's security follow-up. */
  ai_key_storage?: AiKeyStorage;
  /** The company bot's @username (null until the bot has started) and how
   * many accounts have connected their Telegram. Absent from an API older
   * than release P5. */
  telegram?: { bot_username: string | null; accounts_connected: number };
  /** What infra/backup.sh last wrote to storage/.last_backup (round 3,
   * B1b): null when no backup has ever been recorded on this server.
   * Absent from an API older than round 3, which the screen treats the
   * same way -- it can't vouch for a backup it can't see. */
  last_backup?: LastBackup | null;
};

export type LastBackup = {
  at: string;
  /** How many cases the backup held. */
  reports: number;
  /** Whether the offsite copy (rclone to the bucket) went through. The
   * local copy is kept either way. */
  uploaded: boolean;
  bucket: string | null;
};

/** A backup older than this counts as missing on Settings -> Company: the
 * nightly job should have run since. */
export const BACKUP_STALE_HOURS = 36;

/** "ok", or why people's own AI keys can't be read right now. Kept open
 * (string) so a state a newer API adds still shows, with its detail. */
export type AiKeyStorageState = "ok" | "lost" | "broken" | "canary_failed" | "off";
export type AiKeyStorage = { state: AiKeyStorageState | (string & {}); detail?: string | null };

export const DEFAULT_SYSTEM_INFO: SystemInfo = {
  ai: {
    model_chain: ["gemini-2.5-flash"],
    min_call_interval_seconds: 4,
    request_timeout_seconds: 30,
    api_key_configured: false,
  },
  channels: {
    telegram_configured: false,
    whatsapp_configured: false,
    reports_by_channel: {},
  },
  storage: { reports: 0, photos: 0, pdfs: 0, bytes_used: 0, storage_dir: "" },
  database: { engine: "sqlite" },
  auth: { configured: false },
};

export async function getAppSettings(): Promise<AppSettings> {
  try {
    const res = await apiFetch(`${API_BASE_URL}/settings`, { cache: "no-store" }, { timeoutMs: 8000 });
    if (!res.ok) throw new Error(`Failed to load settings (${res.status}: ${res.statusText})`);
    return normalizeSettings(await res.json());
  } catch (err) {
    unstable_rethrow(err);
    console.warn("Failed to load settings, using fallback default:", err);
    return DEFAULT_APP_SETTINGS;
  }
}

/** Admin only. Sends only the keys in the patch; company_key_billed goes as
 * a JSON true/false. */
export async function updateAppSettings(patch: Partial<AppSettings>): Promise<AppSettings> {
  const res = await apiFetch(`${API_BASE_URL}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw await apiError(res, "Failed to save settings");
  return normalizeSettings(await res.json());
}

/** What the public privacy notice (/privacy#public-reports) shows: the
 * data-protection contact and the public contact line (release P6). Read
 * without signing in, from GET /privacy/contact; empty strings when they
 * aren't set, or when they couldn't be read. */
export type PublicContact = { dpo_contact: string; public_contact_phone: string };

export async function getPublicContact(): Promise<PublicContact> {
  const none: PublicContact = { dpo_contact: "", public_contact_phone: "" };
  try {
    const res = await apiFetch(`${API_BASE_URL}/privacy/contact`, { cache: "no-store" }, { timeoutMs: 5000, onSignedOut: "return" });
    if (!res.ok) return none;
    const body = await res.json();
    return {
      dpo_contact: typeof body?.dpo_contact === "string" ? body.dpo_contact.trim() : "",
      public_contact_phone: typeof body?.public_contact_phone === "string" ? body.public_contact_phone.trim() : "",
    };
  } catch (err) {
    unstable_rethrow(err);
    return none;
  }
}

/** Never contains key material -- only enough to tell keys apart. */
export type LlmKey = { id: string; label: string; last4: string; created_at: string };

export async function listLlmKeys(): Promise<{ keys: LlmKey[]; env_key_configured: boolean }> {
  try {
    const res = await apiFetch(`${API_BASE_URL}/setup/llm-keys`, { cache: "no-store" }, { timeoutMs: 8000 });
    if (!res.ok) throw new Error(`Failed to load API keys (${res.status}: ${res.statusText})`);
    return await res.json();
  } catch (err) {
    unstable_rethrow(err);
    console.warn("Failed to load API keys, using fallback default:", err);
    return { keys: [], env_key_configured: false };
  }
}

export async function addLlmKey(apiKey: string, label?: string): Promise<LlmKey> {
  const res = await apiFetch(`${API_BASE_URL}/setup/llm-keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apiKey, label }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Failed to add key (${res.status})`);
  }
  return res.json();
}

export async function deleteLlmKey(id: string): Promise<void> {
  const res = await apiFetch(`${API_BASE_URL}/setup/llm-keys/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Failed to remove key (${res.status})`);
}

export async function testService(service: string): Promise<{ ok: boolean; message: string }> {
  const res = await apiFetch(`${API_BASE_URL}/setup/test/${service}`, { method: "POST" });
  if (!res.ok) throw new Error(`Test failed (${res.status})`);
  return res.json();
}

export type Taxonomy = {
  parts: string[];
  damage_types: string[];
  severities: string[];
  body_types: string[];
  model_body_types: Record<string, string>;
  usage: {
    parts: Record<string, number>;
    damage_types: Record<string, number>;
    /** Free-text names from reports filed before the vocabulary existed. */
    legacy_terms: Record<string, number>;
    reports_counted: number;
  };
  /** The firm's condition words in print order (taxonomy.py
   * FIRM_CONDITION_TOKENS). A Condition is 1-3 of them joined with "/",
   * first one capitalised: "Distorted/cut/faulty". */
  condition_tokens: string[];
  /** The firm's most used Conditions (FIRM_CONDITION_COMMON), offered first. */
  condition_common: string[];
};

/** Built-in copies of the two condition lists, for an API from before they
 * existed (GET /taxonomy without them) or one that can't be reached: the
 * condition editor must never be left with nothing to offer. The words are
 * the firm's 18 in print order; the common list is the most used Conditions
 * in the firm's four example Annex A sheets (detection audit, 2026-11-04).
 * Keep in step with taxonomy.py. */
export const FALLBACK_CONDITION_TOKENS: readonly string[] = [
  "Grazed",
  "Dented",
  "Bent",
  "Buckled",
  "Deformed",
  "Distorted",
  "Crumpled",
  "Warped",
  "Shifted",
  "Slack",
  "Cracked",
  "Chipped",
  "Torn",
  "Cut",
  "Jammed",
  "Faulty",
  "Leaked",
  "Rattle",
];
export const FALLBACK_CONDITION_COMMON: readonly string[] = [
  "Bent",
  "Deformed/cut",
  "Grazed/cut",
  "Bent/cut",
  "Distorted/jammed",
  "Dented/bent",
  "Grazed/cracked",
  "Buckled",
  "Distorted/cut",
  "Cracked",
  "Crumpled",
];

/** Conditions the API takes whole, never joined with a word
 * (FIRM_WHOLE_CONDITIONS): Necessary (a fitting renewed on refit),
 * Serviceable (a part kept) and Repair (repaired rather than renewed: the
 * line is priced, then disallowed in full -- the firm's convention). */
export const CONDITION_WHOLE_VALUES = ["Necessary", "Serviceable", "Repair"] as const;

export type ConditionWords = { tokens: string[]; common: string[] };

/** The condition lists from a taxonomy answer, each falling back to the
 * built-in copy when missing or empty. */
export function conditionWordsOf(t: { condition_tokens?: unknown; condition_common?: unknown } | null | undefined): ConditionWords {
  const list = (v: unknown, fallback: readonly string[]): string[] =>
    Array.isArray(v) && v.length > 0 && v.every((x) => typeof x === "string") ? (v as string[]) : [...fallback];
  return {
    tokens: list(t?.condition_tokens, FALLBACK_CONDITION_TOKENS),
    common: list(t?.condition_common, FALLBACK_CONDITION_COMMON),
  };
}

export const DEFAULT_TAXONOMY: Taxonomy = {
  parts: [],
  damage_types: [],
  severities: [],
  body_types: [],
  model_body_types: {},
  usage: {
    parts: {},
    damage_types: {},
    legacy_terms: {},
    reports_counted: 0,
  },
  condition_tokens: [...FALLBACK_CONDITION_TOKENS],
  condition_common: [...FALLBACK_CONDITION_COMMON],
};

export async function getTaxonomy(): Promise<Taxonomy> {
  try {
    const res = await apiFetch(`${API_BASE_URL}/taxonomy`, { cache: "no-store" }, { timeoutMs: 8000 });
    if (!res.ok) throw new Error(`Failed to load taxonomy (${res.status}: ${res.statusText})`);
    const body = (await res.json()) as Omit<Taxonomy, "condition_tokens" | "condition_common"> & Partial<Taxonomy>;
    const words = conditionWordsOf(body);
    return { ...body, condition_tokens: words.tokens, condition_common: words.common };
  } catch (err) {
    unstable_rethrow(err);
    console.warn("Failed to load taxonomy, using fallback default:", err);
    return DEFAULT_TAXONOMY;
  }
}

export async function getSystemInfo(): Promise<SystemInfo> {
  try {
    const res = await apiFetch(`${API_BASE_URL}/system/info`, { cache: "no-store" }, { timeoutMs: 8000 });
    if (!res.ok) throw new Error(`Failed to load system info (${res.status}: ${res.statusText})`);
    return await res.json();
  } catch (err) {
    unstable_rethrow(err);
    console.warn("Failed to load system info, using fallback default:", err);
    return DEFAULT_SYSTEM_INFO;
  }
}

export async function deleteReport(id: string): Promise<void> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${id}`, { method: "DELETE" });
  if (!res.ok) throw await apiError(res, "Failed to delete report");
}

// Always browser-facing (an <img>/<a> src ends up in the DOM regardless of
// whether this ran during SSR or in the client) -- so this always uses the
// client-safe base, never SERVER_BASE_URL.
export function fileUrl(path: string): string {
  return path.startsWith("http") ? path : `${CLIENT_FILES_BASE_URL}${path}`;
}

export async function aiEnrichReport(id: string): Promise<{ id: string; status: string; data: ReportData }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/${id}/ai-enrich`, {
    method: "POST",
  });
  if (!res.ok) throw await apiError(res, "Fill derived fields failed");
  return res.json();
}

export async function aiEnrichAllReports(): Promise<{ status: string; enriched_count: number }> {
  const res = await apiFetch(`${API_BASE_URL}/reports/ai-enrich-all`, {
    method: "POST",
  });
  if (!res.ok) throw await apiError(res, "Fill derived fields failed");
  return res.json();
}


