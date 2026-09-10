import type { ReportSummary } from "./api";

export type RangeKey = "7d" | "14d" | "30d" | "90d" | "custom";

export const RANGE_PRESETS: { key: Exclude<RangeKey, "custom">; label: string; days: number }[] = [
  { key: "7d", label: "7 days", days: 7 },
  { key: "14d", label: "14 days", days: 14 },
  // Default. With the filing patterns actually on file, 7d and 14d windows
  // are frequently empty -- defaulting to one of those is how the Intake
  // Volume panel ended up rendering "No reports in the last 14 days".
  { key: "30d", label: "30 days", days: 30 },
  { key: "90d", label: "90 days", days: 90 },
];

export const DEFAULT_RANGE: RangeKey = "30d";

export interface ResolvedRange {
  key: RangeKey;
  /** Inclusive UTC date-only bounds, as YYYY-MM-DD. */
  from: string;
  to: string;
  days: number;
  label: string;
}

const DAY_MS = 86400000;

/** YYYY-MM-DD in UTC. created_at is a UTC ISO string, so all bucketing is
 * done in UTC to avoid a report near midnight landing in the wrong day for
 * viewers in a different timezone than the server. */
function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function parseIsoDate(s: string | undefined): string | null {
  if (!s || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return null;
  const t = Date.parse(`${s}T00:00:00Z`);
  return Number.isNaN(t) ? null : s;
}

/** Turns URL search params into a concrete range.
 *
 * Kept in the URL rather than component state on purpose: it keeps the
 * Analytics page a server component (no client-side refetch), and makes a
 * chosen range shareable and bookmarkable.
 *
 * Anything invalid falls back to the default rather than throwing -- a
 * hand-edited or stale URL should degrade to a sensible view, not an error.
 */
export function resolveRange(params: { range?: string; from?: string; to?: string }): ResolvedRange {
  const today = new Date();
  const todayIso = isoDate(today);

  if (params.range === "custom") {
    const from = parseIsoDate(params.from);
    const to = parseIsoDate(params.to);
    if (from && to) {
      // Tolerate a backwards range instead of rendering nothing.
      const [lo, hi] = from <= to ? [from, to] : [to, from];
      const days = Math.round((Date.parse(`${hi}T00:00:00Z`) - Date.parse(`${lo}T00:00:00Z`)) / DAY_MS) + 1;
      return { key: "custom", from: lo, to: hi, days, label: `${lo} → ${hi}` };
    }
  }

  const preset =
    RANGE_PRESETS.find((p) => p.key === params.range) ??
    RANGE_PRESETS.find((p) => p.key === DEFAULT_RANGE)!;

  const fromDate = new Date(today.getTime() - (preset.days - 1) * DAY_MS);
  return {
    key: preset.key,
    from: isoDate(fromDate),
    to: todayIso,
    days: preset.days,
    label: `last ${preset.label}`,
  };
}

export interface Bucket {
  /** Axis tick text. */
  date: string;
  count: number;
  /** Full human-readable span, used in the tooltip. */
  label: string;
}

/** Buckets reports into chart columns.
 *
 * Daily up to ~5 weeks, weekly beyond: 90 daily bars renders as 90
 * unreadable slivers, while 13 weekly bars stays legible. The switch is on
 * span length, not on the preset, so a custom 6-month range groups too.
 */
export function bucketReports(reports: ReportSummary[], range: ResolvedRange): Bucket[] {
  const fromMs = Date.parse(`${range.from}T00:00:00Z`);
  const toMs = Date.parse(`${range.to}T00:00:00Z`);
  const dayCount = Math.round((toMs - fromMs) / DAY_MS) + 1;
  const weekly = dayCount > 35;

  const inRange = reports.filter((r) => {
    const d = r.created_at.slice(0, 10);
    return d >= range.from && d <= range.to;
  });

  const buckets: Bucket[] = [];
  const step = weekly ? 7 : 1;

  for (let offset = 0; offset < dayCount; offset += step) {
    const startMs = fromMs + offset * DAY_MS;
    const endMs = Math.min(startMs + (step - 1) * DAY_MS, toMs);
    const startIso = isoDate(new Date(startMs));
    const endIso = isoDate(new Date(endMs));

    const count = inRange.filter((r) => {
      const d = r.created_at.slice(0, 10);
      return d >= startIso && d <= endIso;
    }).length;

    buckets.push({
      date: weekly ? startIso.slice(5) : startIso.slice(5),
      count,
      label: weekly ? `${startIso} → ${endIso}` : startIso,
    });
  }

  return buckets;
}

/** How many days since the most recent report, or null if there are none.
 * Powers the empty-state message that tells you WHY a window is empty
 * instead of leaving you at a dead end. */
export function daysSinceMostRecent(reports: ReportSummary[]): number | null {
  if (reports.length === 0) return null;
  const newest = reports.reduce((max, r) => (r.created_at > max ? r.created_at : max), reports[0].created_at);
  const diff = Date.now() - Date.parse(newest);
  return Math.max(0, Math.floor(diff / DAY_MS));
}
