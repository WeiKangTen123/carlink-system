import { aiKeyWorking, type AiKeyStatus, type MyAiKey } from "@/lib/api";

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

/** Which key the AI uses for this person right now (release P4), from GET
 * /auth/me/ai-key's `source` and `ai_available`:
 *   own                      -> "AI uses your own key"
 *   company, with a key      -> "AI uses the company key (your own key isn't working)"
 *   company, no key          -> "AI uses the company key"
 *   none / not available     -> "AI is off for you"
 * `text` says why, and what would change it. */
export function aiRightNowWords(
  ai: Pick<MyAiKey, "has_key" | "status" | "billing_confirmed" | "company_allowed" | "ai_available" | "source">,
  isAdmin: boolean
): { off: boolean; which: string; text: string } {
  const companyWhy = isAdmin ? "Admins can always use it." : "An admin has allowed you to use it.";
  // Out of allowance mends itself (about an hour); a refused key doesn't.
  const quota = ai.has_key && ai.status === "quota";
  if (!ai.ai_available || ai.source === "none") {
    const why = quota
      ? "Your own key has used up its allowance for now; it should work again within about an hour, so try again later."
      : ai.has_key
        ? "Your own key isn't working, and the company key isn't yours to use. Fix your key above, or ask an admin to let you use the company key."
        : "Add your own key above (get one free from Google AI Studio), or ask an admin to let you use the company key.";
    return { off: true, which: "AI is off for you.", text: `${why} You can still fill cases in by hand.` };
  }
  if (ai.source === "own") {
    const fallback = ai.company_allowed ? " If your key stops working, AI uses the company key instead." : "";
    return {
      off: false,
      which: "AI uses your own key.",
      text: ai.billing_confirmed
        ? `Whatever you run with AI, on any case, is billed to its Google Cloud project.${fallback}`
        : `Whatever you run with AI, on any case, uses its free daily allowance.${fallback}`,
    };
  }
  if (ai.has_key) {
    return {
      off: false,
      which: "AI uses the company key (your own key isn't working).",
      text: quota
        ? `${companyWhy} Your key has used up its allowance for now; AI goes back to it once it works again, usually within about an hour.`
        : `${companyWhy} Fix your key above to use it again.`,
    };
  }
  return { off: false, which: "AI uses the company key.", text: `${companyWhy} Add your own key above to use that instead.` };
}

/** Free or Billed (the key's `billing_confirmed`), under the choice on My
 * AI key. Free states Google's terms for unpaid use as they are; Billed
 * keeps the words the billing tick had before free keys were allowed. Only
 * a Billed key reads reports sent by the public (bot-service app/ai/keys.py). */
export const KEY_TYPE_WORDS: Record<"free" | "billed", string> = {
  free:
    "Free: Google may use what Carlink sends with this key, case photos included, to improve its products, and people at Google may read it. Turning on billing for the key's Google Cloud project stops that. A free key can't be used for reports sent by the public.",
  billed:
    "Billed: this key's Google Cloud project has billing turned on. With billing on, Google doesn't use what Carlink sends it to improve its products. Pick this only if billing really is on: a billed key is also used for reports sent by the public.",
};

/** What went wrong, when the API kept no message of Google's. */
export const FAILING_WHAT: Record<"invalid" | "quota" | "unreadable", string> = {
  invalid: "Google refused this key.",
  quota: "Google accepted this key, but it has no allowance left for now.",
  unreadable: "This server can't read the saved key.",
};

/** What to do about it, on My AI key. */
export const FAILING_FIX: Record<"invalid" | "quota" | "unreadable", string> = {
  invalid: "Replace it with a key Google accepts, or remove it.",
  quota:
    "It should work again by itself within about an hour, so try again later; a free key's daily allowance starts again each day. If it keeps running out, turn on billing for its Google Cloud project, or replace it.",
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

/** Google Cloud's credentials page, where a key is restricted to one API
 * and to this server's address. */
export const CLOUD_CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials";
