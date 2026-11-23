/** Soft checks on the Cover Page (owner-approved Phase 1, 23 Nov 2026;
 * R-report section 5, gaps 4 and 5): dates not in the future and in a
 * sensible order, a plate that reads like one, a chassis number of 17
 * characters (or a Japanese frame number), an engine number and a
 * speedometer reading that look like one, tyres below the 1.6 mm legal
 * minimum, and a point of impact that agrees with where the damaged parts
 * are. Each shows as a "check" tag beside its field. Never a block: the
 * firm's dates and readings are free text, and a frame number or a
 * special plate is right as it stands.
 *
 * Why a shared TypeScript lib and not the API: the checks follow the form
 * as it is typed. The Review step autosaves without the server answering a
 * new review, and the Cover tab saves only on Save, so a check worked out
 * by the API would show the last saved value's verdict while the person
 * fixes it. These rules are pure and run the same in both places
 * (components/CoverPageFields.tsx) and in scripts/check-cover-checks.mjs.
 * Nothing here blocks or is sent anywhere; what blocks sign-off is the
 * API's must-have list (bot-service app/reports/review.py MUST_HAVE). */
import { parseDepth, type CoverForm, type CoverFormKey } from "./coverForm";

/** Each field's checks, in words; a field with none is left out. */
export type CoverChecks = Partial<Record<CoverFormKey, string[]>>;

/** What the impact check reads of a damaged part (DamageSummaryItem). */
export type DamagedPart = { part: string; other_part_name?: string | null };

// ---- Dates -------------------------------------------------------------------

export type CoverDate = { y: number; m: number; d: number };

const MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];

/** "feb", "February", "sept" -> 2, 2, 9; null for anything else. */
function monthOf(word: string): number | null {
  if (word.length < 3) return null;
  const i = MONTH_NAMES.findIndex((name) => name.startsWith(word));
  return i >= 0 ? i + 1 : null;
}

function realDate(y: number, m: number, d: number): CoverDate | null {
  if (y < 1950 || y > 2100 || m < 1 || m > 12 || d < 1) return null;
  const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return d <= days ? { y, m, d } : null;
}

const fullYear = (text: string) => (text.length === 2 ? 2000 + Number(text) : Number(text));

/** A cover date as the firm or a person writes it, read from its start:
 * "27th February 2023 (10.15am)", "27 Feb 2023", "February 27, 2023",
 * "27/02/2023" or "27-02-23" (day first, as in Malaysia and Singapore),
 * "2023-02-27" and the AI's "2023-02-27T10:15". Null for anything else
 * ("Not applicable", a date that doesn't exist): no check then. */
export function parseCoverDate(text: string | null | undefined): CoverDate | null {
  const t = (text || "").trim().toLowerCase();
  if (!t) return null;
  let m = /^(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)/.exec(t);
  if (m) return realDate(Number(m[1]), Number(m[2]), Number(m[3]));
  m = /^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})(?!\d)/.exec(t);
  if (m) return realDate(fullYear(m[3]), Number(m[2]), Number(m[1]));
  m = /^(\d{1,2})(?:st|nd|rd|th)?[\s/-]*(?:of\s+)?([a-z]{3,9})\.?[\s,/-]*(\d{4})(?!\d)/.exec(t);
  if (m) {
    const month = monthOf(m[2]);
    return month ? realDate(Number(m[3]), month, Number(m[1])) : null;
  }
  m = /^([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)/.exec(t);
  if (m) {
    const month = monthOf(m[1]);
    return month ? realDate(Number(m[3]), month, Number(m[2])) : null;
  }
  return null;
}

const dayNumber = (d: CoverDate) => d.y * 10000 + d.m * 100 + d.d;
const todayNumber = (today: Date) => today.getFullYear() * 10000 + (today.getMonth() + 1) * 100 + today.getDate();

/** The cover's dates, by form key. */
const DATE_KEYS: CoverFormKey[] = [
  "date_of_request",
  "incident_datetime",
  "date_of_survey",
  "date_of_dismantle",
  "date_before_painting",
  "date_after_painting",
  "date_of_registration",
];
/** Done to the car after the accident: dismantling and the painting. */
const AFTER_ACCIDENT_KEYS: CoverFormKey[] = ["date_of_dismantle", "date_before_painting", "date_after_painting"];

// ---- Vehicle facts -------------------------------------------------------------

/** Letters, digits, then maybe a letter: "WXY 1234", "WXY 1234 A", "SLK 3063 Z". */
const PLATE = /^[A-Z]{1,3} ?\d{1,4} ?[A-Z]?$/;
/** A VIN: 17 letters and digits, never I, O or Q. */
const VIN = /^[A-HJ-NPR-Z0-9]{17}$/;
/** A Japanese import's frame number: the model code, a dash, the serial ("RU1-1234567"). */
const FRAME_NUMBER = /^[A-Z0-9]{2,7}-\d{5,8}$/;
/** Words written instead of a number. */
const NOT_A_NUMBER = /^(n\/?a|nil|none|unknown|not (visible|seen|available|recorded|readable|known)|tbc|tba|-+|\?+)$/i;
/** Digits (leading zeros kept), then Km: "058615 Km", "58,615 km". */
const ODOMETER = /^\d[\d,. ]*\s?km$/i;

export const LEGAL_TREAD_MM = 1.6;
const TYRE_DEPTHS: [CoverFormKey, string][] = [
  ["front_nearside_mm", "Front nearside"],
  ["front_offside_mm", "Front offside"],
  ["rear_nearside_mm", "Rear nearside"],
  ["rear_offside_mm", "Rear offside"],
];

// ---- Point of impact against the damaged parts -------------------------------

export type End = "front" | "rear";
export type Side = "lh" | "rh";
export type Areas = { end: End | null; side: Side | null };

const LEFT = /\bleft\b|\blh\b|\bl\/h\b|\bnearside\b|\bn\/s\b/;
const RIGHT = /\bright\b|\brh\b|\br\/h\b|\boffside\b|\bo\/s\b/;

/** Where the point of impact says the car was hit: "Rear centre" -> rear;
 * "Front LH quarter" -> front, LH. Both ends or both sides named ("Front
 * and rear") say neither. */
export function impactAreas(text: string | null | undefined): Areas {
  const t = (text || "").toLowerCase();
  const front = /\bfront\b|\bbonnet\b|\bhead/.test(t);
  const rear = /\brear\b|\bback\b|\bboot\b|\btail/.test(t);
  const left = LEFT.test(t);
  const right = RIGHT.test(t);
  return {
    end: front && !rear ? "front" : rear && !front ? "rear" : null,
    side: left && !right ? "lh" : right && !left ? "rh" : null,
  };
}

/** Parts that sit along the car, at neither end: a front or rear door is
 * still the side of the car. */
const NO_END = /\bdoors?\b|\bregulator\b|\bsill\b|\brocker\b|\broof\b|\bmirror\b|\bchassis\b|\bunderbody\b|\bpillar\b|\bexhaust\b|\bdashboard\b|\bseat/;
const FRONT_PARTS = /\bbonnet\b|\bhood\b|\bhead ?lamp|\bgrille\b|\bradiator\b|\bcondenser\b|\bfog\b|\bengine\b|\bwindscreen\b|\bfender\b|\bwing panel\b/;
const REAR_PARTS = /\bboot\b|\btail ?gate\b|\btail ?lamp|\breverse\b|\breversing\b|\bquarter\b|\bend panel\b|\bspare\b|\bhigh mount\b|\bparking sensor\b|\bfuel filler\b/;

/** Where a damaged part is, from its name (the list's, a car map name, or
 * an "Other" part's typed name): "Right Front Fender" -> front, RH; "Rear
 * Windscreen" -> rear; "Left Front Door" -> LH only (a door is the side). */
export function partAreas(name: string | null | undefined): Areas {
  const t = (name || "").toLowerCase();
  const side: Side | null = LEFT.test(t) ? "lh" : RIGHT.test(t) ? "rh" : null;
  if (NO_END.test(t)) return { end: null, side };
  const front = /\bfront\b/.test(t);
  const rear = /\brear\b|\bback\b/.test(t);
  let end: End | null = front && !rear ? "front" : rear && !front ? "rear" : null;
  if (!end && !front && !rear) end = REAR_PARTS.test(t) ? "rear" : FRONT_PARTS.test(t) ? "front" : null;
  return { end, side };
}

const partName = (p: DamagedPart) => (p.part === "Other / Not Listed" ? p.other_part_name || "" : p.part);

const END_WORDS: Record<End, string> = { front: "front", rear: "rear" };
const SIDE_WORDS: Record<Side, string> = { lh: "LH", rh: "RH" };

/** The impact's check against the parts' areas: a warning when it names an
 * end (or a side) and every listed part that has one is at the other. */
function impactChecks(impact: string, damage: DamagedPart[]): string[] {
  const said = impactAreas(impact);
  if (!impact.trim() || (!said.end && !said.side)) return [];
  const parts = damage.map((p) => partAreas(partName(p)));
  const ends = parts.map((p) => p.end).filter((e): e is End => e !== null);
  const sides = parts.map((p) => p.side).filter((s): s is Side => s !== null);
  const out: string[] = [];
  if (said.end && ends.length > 0 && !ends.includes(said.end)) {
    const other = said.end === "front" ? "rear" : "front";
    out.push(`Says ${END_WORDS[said.end]}, but every damaged part listed with an end is at the ${END_WORDS[other]}`);
  }
  if (said.side && sides.length > 0 && !sides.includes(said.side)) {
    const other = said.side === "lh" ? "rh" : "lh";
    out.push(`Says ${SIDE_WORDS[said.side]}, but every damaged part listed with a side is on the ${SIDE_WORDS[other]}`);
  }
  return out;
}

// ---- All of them -------------------------------------------------------------

/** Every soft check on the form. `damage`: the case's damaged parts (the
 * impact check is skipped without them); `today`: for "in the future". */
export function coverChecks(form: CoverForm, damage: DamagedPart[] = [], today: Date = new Date()): CoverChecks {
  const out: CoverChecks = {};
  const add = (key: CoverFormKey, text: string) => {
    (out[key] ??= []).push(text);
  };

  // Dates: none in the future; accident <= request <= survey; the
  // dismantling and painting after the accident, the painting in order;
  // the registration before the accident.
  const dates: Partial<Record<CoverFormKey, number>> = {};
  for (const key of DATE_KEYS) {
    const d = parseCoverDate(form[key]);
    if (d) dates[key] = dayNumber(d);
  }
  const now = todayNumber(today);
  for (const key of DATE_KEYS) {
    const d = dates[key];
    if (d !== undefined && d > now) add(key, "In the future");
  }
  const accident = dates.incident_datetime;
  const request = dates.date_of_request;
  const survey = dates.date_of_survey;
  if (accident !== undefined && request !== undefined && request < accident) add("date_of_request", "Before the accident date");
  if (request !== undefined && survey !== undefined && survey < request) add("date_of_survey", "Before the date of request");
  else if (accident !== undefined && survey !== undefined && survey < accident) add("date_of_survey", "Before the accident date");
  for (const key of AFTER_ACCIDENT_KEYS) {
    const d = dates[key];
    if (accident !== undefined && d !== undefined && d < accident) add(key, "Before the accident date");
  }
  const before = dates.date_before_painting;
  const after = dates.date_after_painting;
  if (before !== undefined && after !== undefined && after < before) add("date_after_painting", "Before the date of before painting");
  const registered = dates.date_of_registration;
  if (accident !== undefined && registered !== undefined && registered > accident) add("date_of_registration", "After the accident date");

  // Registration No.: Malaysian (and Singapore) style; a special plate is fine as it stands.
  const plate = form.plate_number.trim().toUpperCase().replace(/\s+/g, " ");
  if (plate && !PLATE.test(plate)) add("plate_number", "Doesn't read like a plate: letters, digits, then maybe a letter (WXY 1234 A)");

  // Chassis No.: a VIN, or a frame number.
  const chassis = form.vin.trim().toUpperCase().replace(/\s+/g, "");
  if (chassis && !VIN.test(chassis) && !FRAME_NUMBER.test(chassis)) {
    add(
      "vin",
      chassis.length === 17
        ? "17 characters, but a VIN never has I, O or Q"
        : `${chassis.length} character${chassis.length === 1 ? "" : "s"}: a VIN has 17 (a frame number reads like RU1-1234567)`
    );
  }

  // Engine No.: letters and digits, not a word written in its place.
  const engine = form.engine_number.trim();
  if (engine && (NOT_A_NUMBER.test(engine) || !/\d/.test(engine) || engine.replace(/[^A-Za-z0-9]/g, "").length < 5)) {
    add("engine_number", "Doesn't look like an engine number (letters and digits)");
  }

  // Speedometer reading: the digits, then Km.
  const odometer = form.odometer_reading.trim();
  if (odometer && !ODOMETER.test(odometer)) {
    add("odometer_reading", /\d/.test(odometer) ? "Write the digits, then Km (e.g. 058615 Km)" : "No digits: write the reading, then Km (e.g. 058615 Km)");
  }

  // Tyres: below the legal minimum, or not a number (saved as blank).
  for (const [key, label] of TYRE_DEPTHS) {
    const typed = form[key].trim();
    if (!typed) continue;
    const depth = parseDepth(typed);
    if (depth === null) add(key, `${label}: "${typed}" is not a depth in mm, so it saves as blank`);
    else if (depth < LEGAL_TREAD_MM) add(key, `${label} ${depth} mm: below the ${LEGAL_TREAD_MM} mm legal minimum`);
  }

  for (const text of impactChecks(form.point_of_impact, damage)) add("point_of_impact", text);
  return out;
}

/** The tyre table's checks, in the table's order: shown under it. */
export function tyreChecks(checks: CoverChecks): string[] {
  return TYRE_DEPTHS.flatMap(([key]) => checks[key] ?? []);
}
