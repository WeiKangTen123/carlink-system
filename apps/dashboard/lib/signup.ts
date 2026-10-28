/** Helpers shared by the public sign-up pages (app/signup, app/invite),
 * Settings -> My settings and Settings -> Users, which hands out invite and
 * reset links.
 * Browser-safe: no API calls here. */

import type { UserRole } from "./api";

export const ROLE_LABEL: Record<UserRole, string> = { user: "User", admin: "Admin" };

// ---------------------------------------------------------------------------
// Usernames. The same rule as username_problem in
// apps/bot-service/app/auth/core.py, so the form can say what's wrong before
// sending; the server still decides (and also refuses reserved names).

const USERNAME_SHAPE = /^[a-z0-9][a-z0-9._-]{1,28}[a-z0-9]$/;
export const USERNAME_HINT = "3-30 letters, numbers, dots, dashes or underscores.";

export function usernameProblem(typed: string): string | null {
  const value = typed.trim().toLowerCase();
  if (!value) return "Choose a username.";
  if (!USERNAME_SHAPE.test(value)) {
    return "Use 3-30 letters, numbers, dots, dashes or underscores, starting and ending with a letter or number.";
  }
  return null;
}

/** Same rule as MIN_PASSWORD_LENGTH in apps/bot-service/app/auth/passwords.py
 * (10; 15 when the server runs with STRICT_PASSWORD_RULES=true -- change it
 * here too then). The common-password and personal-word checks, when on,
 * only run on the server. */
export const MIN_PASSWORD_LENGTH = 10;

/** Characters as the server counts them (Python's len), so an emoji counts
 * once rather than as two UTF-16 halves. */
export function passwordLength(password: string): number {
  return [...password].length;
}

/** The server's two "guessable" refusals (TOO_COMMON, TOO_PERSONAL in
 * passwords.py) both end with this advice. */
export function isGuessablePasswordError(detail: string): boolean {
  return /random words/i.test(detail);
}

// ---------------------------------------------------------------------------
// Mobile numbers, for the Open WhatsApp button on a new link. Most people
// are in Singapore, so a bare number gets +65; anyone else types their own
// "+country code".

/** What a typed number is saved as: "+65 9123 4567", or the user's own
 * "+..." as typed. */
export function fullMobile(typed: string): string {
  const value = typed.trim();
  return value.startsWith("+") ? value : `+65 ${value}`;
}

/** Digits for https://wa.me/<digits>, or null if there isn't a usable
 * number. A bare 8-digit number is taken as Singapore's. */
export function whatsAppDigits(phone: string | null | undefined): string | null {
  const value = (phone || "").trim();
  if (!value) return null;
  const digits = value.replace(/\D/g, "");
  if (value.startsWith("+")) return digits.length >= 8 ? digits : null;
  if (digits.length === 8) return `65${digits}`;
  return digits.length > 8 ? digits : null;
}

// ---------------------------------------------------------------------------
// Dates. Always Singapore time, whatever the browser's own zone.

/** "25 Oct 2026, 14:30 SGT". */
export function formatSgt(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return (
    d.toLocaleString("en-SG", {
      timeZone: "Asia/Singapore",
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      // h23, not hour12:false: Node and browsers disagree on midnight under
      // hour12:false ("24:05" vs "00:05"); see lib/caseFields.ts.
      hourCycle: "h23",
    }) + " SGT"
  );
}

/** "20 Oct" in Singapore time. */
export function formatSgtDay(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-SG", { timeZone: "Asia/Singapore", day: "numeric", month: "short" });
}

export function firstName(name: string): string {
  return name.trim().split(/\s+/)[0] || "";
}
