"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export function TimelineBarChart({ data }: { data: { date: string; count: number }[] }) {
  const total = data.reduce((sum, d) => sum + d.count, 0);
  if (total === 0) {
    return <div className="chart-empty">No reports in the last 14 days.</div>;
  }
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data} margin={{ left: -16, right: 16, top: 8, bottom: 8 }}>
        <CartesianGrid vertical={false} stroke="var(--gridline)" />
        <XAxis
          dataKey="date"
          stroke="var(--muted)"
          fontSize={11}
          tickFormatter={(d: string) => d.slice(5)}
          interval={1}
        />
        <YAxis allowDecimals={false} stroke="var(--muted)" fontSize={12} />
        <Tooltip
          contentStyle={{
            background: "var(--surface-1)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontSize: 12,
          }}
          cursor={{ fill: "var(--gridline)" }}
        />
        <Bar dataKey="count" fill="var(--series-1)" radius={[4, 4, 0, 0]} barSize={18} />
      </BarChart>
    </ResponsiveContainer>
  );
}
