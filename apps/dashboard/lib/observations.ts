/** Settings -> AI accuracy, "What people changed" (step 2B): the counts of
 * GET /admin/observations/summary (bot-service app/api/observations.py) --
 * the parts people added (the AI missed them), removed and dismissed on
 * signed-off cases, per part, per make and per area. Counts only: no case,
 * no name.
 *
 * Pure (no React, no fetching), so scripts/check-observations.mjs can
 * check it with Node alone. The answer is read so that a field an older
 * server leaves out never breaks the section. */

export type ObservationCounts = {
  /** Signed-off cases behind the counts. */
  cases: number;
  /** On the AI's list and on the signed-off one. */
  kept: number;
  /** On the AI's list, taken off by people. */
  removed: number;
  /** Put on by people: the AI missed them. */
  added: number;
  /** Of those added, added from a suggestion. */
  added_prompted: number;
  /** Suggested, marked Not damaged. */
  dismissed: number;
  /** Suggested, never decided. */
  left: number;
};

export type ObservationTotals = ObservationCounts & {
  /** Part decisions counted. */
  parts: number;
  /** Signed-off cases with no AI draft to compare with (left out). */
  cases_without_ai_draft: number;
};

export type ObservationSummary = {
  from: string | null;
  to: string | null;
  totals: ObservationTotals;
  by_part: Array<ObservationCounts & { part: string }>;
  by_make: Array<ObservationCounts & { make: string }>;
  by_area: Array<ObservationCounts & { area: string }>;
};

const COUNT_KEYS = ["cases", "kept", "removed", "added", "added_prompted", "dismissed", "left"] as const;

function isObj(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

function counts(raw: Record<string, unknown>): ObservationCounts {
  const out = {} as ObservationCounts;
  for (const key of COUNT_KEYS) out[key] = count(raw[key]);
  return out;
}

function named<K extends "part" | "make" | "area">(list: unknown, key: K): Array<ObservationCounts & Record<K, string>> {
  if (!Array.isArray(list)) return [];
  return list.flatMap((row: unknown) => {
    if (!isObj(row)) return [];
    const name = typeof row[key] === "string" ? (row[key] as string).trim() : "";
    if (!name) return [];
    return [{ ...counts(row), [key]: name } as ObservationCounts & Record<K, string>];
  });
}

/** The API's answer, every field checked: a missing or odd value is 0 or
 * an empty list, never an error. */
export function normalizeObservationSummary(body: unknown): ObservationSummary {
  const raw = isObj(body) ? body : {};
  const totals = isObj(raw.totals) ? raw.totals : {};
  return {
    from: typeof raw.from === "string" ? raw.from : null,
    to: typeof raw.to === "string" ? raw.to : null,
    totals: { ...counts(totals), parts: count(totals.parts), cases_without_ai_draft: count(totals.cases_without_ai_draft) },
    by_part: named(raw.by_part, "part"),
    by_make: named(raw.by_make, "make"),
    by_area: named(raw.by_area, "area"),
  };
}

/** A real calendar date written YYYY-MM-DD. */
export function isIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [y, m, d] = value.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d;
}

/** "?from=...&to=..." for the range (either may be blank: "" for none),
 * or an error to show when a date isn't one or from is after to. */
export function rangeQuery(from: string | undefined, to: string | undefined): { query: string } | { error: string } {
  const a = (from ?? "").trim();
  const b = (to ?? "").trim();
  if (a && !isIsoDate(a)) return { error: "The From date isn't a date." };
  if (b && !isIsoDate(b)) return { error: "The To date isn't a date." };
  if (a && b && a > b) return { error: "The From date is after the To date." };
  const params = new URLSearchParams();
  if (a) params.set("from", a);
  if (b) params.set("to", b);
  const text = params.toString();
  return { query: text ? `?${text}` : "" };
}

/** What people changed of a group, for sorting and "nothing changed". */
export function changes(c: ObservationCounts): number {
  return c.added + c.removed + c.dismissed;
}

const AREA_NAMES: Record<string, string> = {
  front: "Front",
  rear: "Rear",
  side: "Side (doors, mirrors, sills)",
  roof: "Roof",
  underbody: "Underbody",
  cabin: "Cabin",
  wheel: "Wheels",
  unknown: "Not placed",
};

/** An area as the section names it ("side" -> "Side (doors, mirrors, sills)"). */
export function areaName(area: string): string {
  return AREA_NAMES[area] ?? area;
}
