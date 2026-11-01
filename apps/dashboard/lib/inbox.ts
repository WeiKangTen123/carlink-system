/** Times for the Public Inbox (release P6): how long an untaken report has
 * left before it is deleted, and how long ago something happened. Pure
 * functions of `now`, which the page passes in (the server's clock on the
 * first render, then the browser's), so the server's HTML and the
 * browser's first render say the same. */

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;

/** ok: 3 days or more left. soon: under 3 days (amber). urgent: under 1
 * day (red). due: past its 30 days; the next clean-up deletes it (red). */
export type ExpiryTone = "ok" | "soon" | "urgent" | "due";

export function expiryInfo(expiresAt: string | null | undefined, now: number): { tone: ExpiryTone; short: string; long: string } | null {
  if (!expiresAt) return null;
  const at = new Date(expiresAt).getTime();
  if (Number.isNaN(at)) return null;
  const left = at - now;
  if (left <= 0) return { tone: "due", short: "Due", long: "Due for deletion" };
  if (left < DAY) {
    const hours = Math.max(1, Math.floor(left / HOUR));
    return { tone: "urgent", short: `${hours} h`, long: `${hours} hour${hours === 1 ? "" : "s"} left` };
  }
  const days = Math.floor(left / DAY);
  return { tone: left < 3 * DAY ? "soon" : "ok", short: `${days} d`, long: `${days} day${days === 1 ? "" : "s"} left` };
}

/** "just now", "5 min ago", "3 h ago", "2 days ago". */
export function timeAgo(iso: string | null | undefined, now: number): string {
  if (!iso) return "";
  const at = new Date(iso).getTime();
  if (Number.isNaN(at)) return "";
  const ago = Math.max(0, now - at);
  if (ago < 60 * 1000) return "just now";
  if (ago < HOUR) return `${Math.floor(ago / 60000)} min ago`;
  if (ago < DAY) return `${Math.floor(ago / HOUR)} h ago`;
  const days = Math.floor(ago / DAY);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

/** "30 Oct 14:02" (Singapore time), for the list. */
export function shortSgt(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-SG", {
    timeZone: "Asia/Singapore",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
}

/** "30 Oct 2026, 14:02" (Singapore time). */
export function longSgt(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-SG", {
    timeZone: "Asia/Singapore",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
}

/** Tells the sidebar badge, the Cases chip and the Command Center card to
 * count again now (after a take, release or discard), instead of on their
 * next 60-second poll. */
export const INBOX_CHANGED_EVENT = "carlink:inbox-changed";

export function announceInboxChanged(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(INBOX_CHANGED_EVENT));
}
