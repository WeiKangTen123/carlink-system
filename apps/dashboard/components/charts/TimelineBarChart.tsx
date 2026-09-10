"use client";

import type { ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export interface TimelinePoint {
  date: string;
  count: number;
  /** Full span for the tooltip. Matters for weekly buckets, where an axis
   * tick of "08-15" alone doesn't say it covers a whole week. */
  label?: string;
}

export function TimelineBarChart({
  data,
  emptyState,
}: {
  data: TimelinePoint[];
  /** The chart doesn't know what range produced it, so the caller supplies
   * the empty message. Previously hardcoded to "the last 14 days", which
   * became wrong the moment the window was selectable. */
  emptyState?: ReactNode;
}) {
  const total = data.reduce((sum, d) => sum + d.count, 0);
  if (total === 0) {
    return <div className="chart-empty">{emptyState ?? "No reports in this range."}</div>;
  }

  // Keep tick labels from colliding once the range gets long: show roughly
  // a dozen ticks regardless of how many bars there are.
  const tickInterval = Math.max(0, Math.ceil(data.length / 12) - 1);

  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data} margin={{ left: -16, right: 16, top: 8, bottom: 8 }}>
        <CartesianGrid vertical={false} stroke="var(--gridline)" />
        <XAxis dataKey="date" stroke="var(--muted)" fontSize={11} interval={tickInterval} />
        <YAxis allowDecimals={false} stroke="var(--muted)" fontSize={12} />
        <Tooltip
          contentStyle={{
            background: "var(--surface-1)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontSize: 12,
          }}
          cursor={{ fill: "var(--gridline)" }}
          labelFormatter={(_, payload) => payload?.[0]?.payload?.label ?? ""}
          formatter={(value) => {
            const n = typeof value === "number" ? value : Number(value ?? 0);
            return [`${n} report${n === 1 ? "" : "s"}`, ""] as [string, string];
          }}
        />
        <Bar dataKey="count" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={28} />
      </BarChart>
    </ResponsiveContainer>
  );
}
