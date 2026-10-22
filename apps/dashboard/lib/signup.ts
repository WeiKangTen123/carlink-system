/** Helpers shared by the public sign-up pages (app/signup, app/invite) and
 * Settings -> Users, which approves requests and hands out the links.
 * Browser-safe: no API calls here. */

import type { UserRole } from "./api";

export const ROLE_LABEL: Record<UserRole, string> = { surveyor: "Surveyor", admin: "Admin" };

/** Same rule as MIN_PASSWORD_LENGTH in apps/bot-service/app/auth/passwords.py.
 * The common-password and personal-word checks only run on the server. */
export const MIN_PASSWORD_LENGTH = 15;

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
// Mobile numbers. Most people are in Singapore, so a bare number gets +65;
// anyone else types their own "+country code".

/** What a typed number is saved as: "+65 9123 4567", or the user's own
 * "+..." as typed. */
export function fullMobile(typed: string): string {
  const value = typed.trim();
  return value.startsWith("+") ? value : `+65 ${value}`;
}

/** Why a typed number won't do, or null. Same wording as the server where
 * the server has a message for it. */
export function mobileProblem(typed: string): string | null {
  const value = typed.trim();
  if (!value) return "Enter a mobile number, e.g. 9123 4567.";
  if (/[^\d\s()+-]/.test(value) || value.indexOf("+") > 0) return "Enter a mobile number, e.g. 9123 4567.";
  const digits = value.replace(/\D/g, "");
  if (value.startsWith("+")) {
    return digits.length >= 8 && digits.length <= 15 ? null : "Enter the full number after the country code.";
  }
  return digits.length === 8
    ? null
    : "Enter an 8-digit number, e.g. 9123 4567. For a number outside Singapore, start with + and the country code.";
}

/** "+65 **** 4567", or "your number ending 4567" for a number from
 * elsewhere, whose country code we can't split off reliably. */
export function maskMobile(full: string): string {
  const digits = full.replace(/\D/g, "");
  const last4 = digits.slice(-4);
  if (full.trim().startsWith("+65") || !full.trim().startsWith("+")) return `+65 **** ${last4}`;
  return `your number ending ${last4}`;
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
