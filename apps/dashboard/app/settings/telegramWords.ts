import type { TelegramIdentity } from "@/lib/api";

/** How Telegram accounts and the company bot are shown on Settings (My
 * Telegram, Users, Company). Telegram usernames and names are whatever the
 * person chose there, so they are shown as plain text only, and a link is
 * made only from a username in Telegram's own format.
 *
 * The @username and the name are always shown apart, each for what it is,
 * never merged into one label (security review M1): anyone can copy a name
 * exactly, and an account with no @username shows nothing else to check,
 * so neither may pass for the other. */

function cleanUsername(username: string | null | undefined): string {
  return (username || "").trim().replace(/^@/, "");
}

/** "@ahhuat_sg", or "" when the account has no @username. */
export function telegramUsername(t: TelegramIdentity | null | undefined): string {
  const username = cleanUsername(t?.username);
  return username ? `@${username}` : "";
}

/** The name on the Telegram account, or "". */
export function telegramName(t: TelegramIdentity | null | undefined): string {
  return (t?.name || "").trim();
}

/** Both, labelled, for plain text (a confirm box, a tooltip):
 * `@ahhuat_sg, name on Telegram "Tan Ah Huat"`, or `no @username, name on
 * Telegram "Tan Ah Huat"`. */
export function telegramPlain(t: TelegramIdentity | null | undefined): string {
  const username = telegramUsername(t) || "no @username";
  const name = telegramName(t);
  return name ? `${username}, name on Telegram "${name}"` : username;
}

/** Telegram's rules for a bot's @username: 5-32 letters, digits and
 * underscores (checked loosely). */
const BOT_USERNAME = /^[A-Za-z0-9_]{3,64}$/;

/** "@carlink_reporter_bot", or null when it isn't known. */
export function botHandle(botUsername: string | null | undefined): string | null {
  const name = cleanUsername(botUsername);
  return BOT_USERNAME.test(name) ? `@${name}` : null;
}

/** https://t.me/<bot>: opens the bot in Telegram. */
export function botUrl(botUsername: string | null | undefined): string | null {
  const name = cleanUsername(botUsername);
  return BOT_USERNAME.test(name) ? `https://t.me/${name}` : null;
}

/** The API's deep link, only if it really opens Telegram (t.me or
 * telegram.me over https; "t.me/..." without a scheme is given one). */
export function safeDeepLink(link: string | null | undefined): string | null {
  const raw = (link || "").trim();
  if (!raw) return null;
  const withScheme = /^(t|telegram)\.me\//i.test(raw) ? `https://${raw}` : raw;
  try {
    const url = new URL(withScheme);
    if (url.protocol !== "https:") return null;
    if (url.hostname !== "t.me" && url.hostname !== "telegram.me") return null;
    return url.toString();
  } catch {
    return null;
  }
}

/** "9:41" for what is left of a code. */
export function countdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/** "12 Nov 2026" in Singapore time, like the rest of Carlink. */
export function sgtDate(iso: string | null | undefined): string {
  const t = apiTime(iso);
  if (isNaN(t)) return "";
  return new Date(t).toLocaleDateString("en-SG", { timeZone: "Asia/Singapore", day: "numeric", month: "short", year: "numeric" });
}

/** A time from the API in milliseconds, NaN if it isn't one. The API sends
 * UTC with its offset (iso_utc); one without an offset is read as UTC too,
 * not as the browser's local time, so a countdown is never 8 hours out. */
export function apiTime(iso: string | null | undefined): number {
  if (!iso) return NaN;
  const s = iso.trim();
  const zoned = /(?:[zZ]|[+-]\d\d:?\d\d)$/.test(s);
  return Date.parse(zoned || !/\dT\d/.test(s) ? s : `${s}Z`);
}

/** True when two times from the API are the same moment (the same code). */
export function sameMoment(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  if (a === b) return true;
  const ta = apiTime(a);
  return !isNaN(ta) && ta === apiTime(b);
}
