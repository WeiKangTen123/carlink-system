import { unstable_rethrow } from "next/navigation";
import { API_BASE_URL, apiFetch } from "./api";

/** GET /auth/me/ai-allowance: how much of each key's daily Gemini allowance
 * has gone today, per model, for the keys the AI may use for this person
 * (their own, then the company's if they may use it). Shown on Settings ->
 * My AI key ("Today"). Google's daily allowances start again at midnight US
 * Pacific time, which is `day_resets_at`.
 *
 * The panel is extra: an older API without the route (404), or any other
 * failure, just leaves it out, so everything here answers null rather than
 * throwing. */

/** What a model does in a run: the photo reader, the one tried when it is
 * busy or used up, the sorting of photos, a second reading. */
export type AllowanceRole = "main" | "backup" | "sorting" | "second_look";

export type AllowanceModel = {
  model: string;
  /** A role this screen doesn't know yet is shown by its own name. */
  role: AllowanceRole | string;
  used_today: number;
  /** Google's daily limit for this key and model, when known. */
  limit: number | null;
  /** Used up (Google said so) until this time, or null. */
  spent_until: string | null;
};

export type AllowanceKey = {
  source: "own" | "company";
  /** Own keys only; never the company key's. */
  last4: string | null;
  /** Own keys: Billed (true) or Free (false). */
  billed: boolean | null;
  models: AllowanceModel[];
};

export type AiAllowance = {
  /** The next midnight US Pacific, in UTC. */
  day_resets_at: string;
  keys: AllowanceKey[];
};

function isCount(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v) && v >= 0;
}

function parseModel(m: unknown): AllowanceModel | null {
  if (!m || typeof m !== "object") return null;
  const o = m as Record<string, unknown>;
  if (typeof o.model !== "string" || !o.model || typeof o.role !== "string" || !isCount(o.used_today)) return null;
  return {
    model: o.model,
    role: o.role,
    used_today: o.used_today,
    limit: isCount(o.limit) ? o.limit : null,
    spent_until: typeof o.spent_until === "string" && o.spent_until ? o.spent_until : null,
  };
}

function parseKey(k: unknown): AllowanceKey | null {
  if (!k || typeof k !== "object") return null;
  const o = k as Record<string, unknown>;
  if ((o.source !== "own" && o.source !== "company") || !Array.isArray(o.models)) return null;
  const models = o.models.map(parseModel).filter((m): m is AllowanceModel => m !== null);
  if (models.length === 0) return null;
  return {
    source: o.source,
    last4: o.source === "own" && typeof o.last4 === "string" ? o.last4 : null,
    billed: typeof o.billed === "boolean" ? o.billed : null,
    models,
  };
}

/** The answer, checked field by field: built alongside this screen, so a
 * shape it doesn't expect hides the panel instead of breaking the card. */
export function parseAllowance(body: unknown): AiAllowance | null {
  if (!body || typeof body !== "object") return null;
  const o = body as Record<string, unknown>;
  if (typeof o.day_resets_at !== "string" || isNaN(new Date(o.day_resets_at).getTime()) || !Array.isArray(o.keys)) {
    return null;
  }
  const keys = o.keys.map(parseKey).filter((k): k is AllowanceKey => k !== null);
  return { day_resets_at: o.day_resets_at, keys };
}

/** Server side (the settings actions). Null when there is nothing to show:
 * the route isn't there, it failed, or the answer didn't make sense. A
 * signed-out redirect still reaches Next. */
export async function getMyAiAllowance(): Promise<AiAllowance | null> {
  try {
    const res = await apiFetch(`${API_BASE_URL}/auth/me/ai-allowance`, { cache: "no-store" }, { timeoutMs: 8000 });
    if (!res.ok) return null;
    return parseAllowance(await res.json().catch(() => null));
  } catch (err) {
    unstable_rethrow(err);
    return null;
  }
}

/** The panel's words for a role. */
export const ROLE_LABELS: Record<AllowanceRole, string> = {
  main: "Main reader",
  backup: "Backup",
  sorting: "Sorting",
  second_look: "Second look",
};

export function roleLabel(role: string): string {
  return ROLE_LABELS[role as AllowanceRole] ?? role.replace(/_/g, " ");
}

function sameLocalDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** "4:00 pm" in the viewer's own time zone, "tomorrow 4:00 pm" when it's
 * not today there. In the browser only: the server's zone isn't the
 * viewer's. */
export function localClock(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", hour12: true }).formatToParts(d);
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  const clock = `${part("hour")}:${part("minute")} ${part("dayPeriod").toLowerCase()}`.trim();
  if (sameLocalDay(d, now)) return clock;
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  return sameLocalDay(d, tomorrow) ? `tomorrow ${clock}` : `${d.toLocaleDateString("en-US", { day: "numeric", month: "short" })} ${clock}`;
}

/** The viewer's time zone by name ("Malaysia Time"), or "" if the browser
 * won't say. */
export function localZoneName(at: Date = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", { timeZoneName: "long" }).formatToParts(at);
    return parts.find((p) => p.type === "timeZoneName")?.value ?? "";
  } catch {
    return "";
  }
}

/** One model's line: "used up until 4:00 pm" while Google says it is spent,
 * "7 of 20 used" when the limit is known, else "41 used". `spent` colours
 * it as used up. */
export function usageWords(m: AllowanceModel, now: Date = new Date()): { text: string; spent: boolean } {
  if (m.spent_until) {
    const until = new Date(m.spent_until);
    if (!isNaN(until.getTime()) && until.getTime() > now.getTime()) {
      return { text: `used up until ${localClock(m.spent_until, now)}`, spent: true };
    }
  }
  // None at all: a model a free key can't use (Google's "limit: 0").
  if (m.limit === 0) return { text: "no allowance on this key", spent: true };
  if (m.limit !== null) {
    return { text: `${m.used_today} of ${m.limit} used`, spent: m.used_today >= m.limit };
  }
  return { text: `${m.used_today} used`, spent: false };
}
