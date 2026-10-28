import { aiKeyWorking, type AiKeyStatus } from "@/lib/api";

/** The words every screen uses for an own AI key (agent5 §4.6), so My AI
 * key, the setup checklist and Users all say the same thing:
 *   no key                      -> "Not set up"
 *   ok, untested                -> "Working" (untested: "not checked yet")
 *   invalid, quota, unreadable  -> "Failing" */

export type AiKeyWord = "Not set up" | "Working" | "Failing";

export function aiKeyWord(hasKey: boolean, status: AiKeyStatus | null | undefined): AiKeyWord {
  if (!hasKey) return "Not set up";
  return aiKeyWorking(status) ? "Working" : "Failing";
}

/** "****1234", the only part of a key anyone ever sees. */
export function maskedKey(last4: string | null | undefined): string {
  return `****${last4 || "????"}`;
}

/** What went wrong, when the API kept no message of Google's. */
export const FAILING_WHAT: Record<"invalid" | "quota" | "unreadable", string> = {
  invalid: "Google refused this key.",
  quota: "Google accepted this key, but it has no allowance left for now.",
  unreadable: "This server can't read the saved key.",
};

/** What to do about it, on My AI key. */
export const FAILING_FIX: Record<"invalid" | "quota" | "unreadable", string> = {
  invalid: "Replace it with a key Google accepts, or remove it.",
  quota: "Check that billing is on for its Google Cloud project, then Test it again, or replace it.",
  unreadable: "Replace it: paste the key again, or a new one.",
};

export function isFailingStatus(status: AiKeyStatus | null | undefined): status is "invalid" | "quota" | "unreadable" {
  return status === "invalid" || status === "quota" || status === "unreadable";
}

/** The same shape and rule as the API's format check (app/ai/keys.py
 * _FORMAT), so a typo is caught here instead of using up one of the
 * person's 10 saves an hour. The API still decides. */
const KEY_SHAPE = /^[A-Za-z0-9_-]{20,60}$/;
export const KEY_FORMAT_PROBLEM =
  "That doesn't look like a Gemini API key. It is usually 39 letters and numbers, starting with AIza.";

export function keyFormatProblem(typed: string): string | null {
  return KEY_SHAPE.test(typed.trim()) ? null : KEY_FORMAT_PROBLEM;
}

/** Google AI Studio's API keys page, where a key is made. */
export const AI_STUDIO_KEYS_URL = "https://aistudio.google.com/apikey";
