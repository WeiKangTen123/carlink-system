"use client";

import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const CHANNEL_ORDER = ["telegram", "whatsapp", "manual"];
const CHANNEL_COLORS: Record<string, string> = {
  telegram: "var(--series-1)",
  whatsapp: "var(--series-3)",
  manual: "var(--series-4)",
};
const CHANNEL_LABELS: Record<string, string> = {
  telegram: "Telegram",
  whatsapp: "WhatsApp",
  manual: "Manual entry",
};

export function ChannelBarChart({ data }: { data: { channel: string; count: number }[] }) {
  if (data.length === 0) {
    return <div className="chart-empty">No reports yet.</div>;
  }
  const sorted = [...data].sort(
    (a, b) => CHANNEL_ORDER.indexOf(a.channel) - CHANNEL_ORDER.indexOf(b.channel)
  );
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={sorted} layout="vertical" margin={{ left: 8, right: 16, top: 8, bottom: 8 }}>
        <CartesianGrid horizontal={false} stroke="var(--gridline)" />
        <XAxis type="number" allowDecimals={false} stroke="var(--muted)" fontSize={12} />
        <YAxis
          type="category"
          dataKey="channel"
          width={100}
          stroke="var(--muted)"
          fontSize={12}
          tickFormatter={(c: string) => CHANNEL_LABELS[c] ?? c}
        />
        <Tooltip
          contentStyle={{
            background: "var(--surface-1)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontSize: 12,
          }}
          cursor={{ fill: "var(--gridline)" }}
          formatter={(value: any) => [value, "Reports"]}
          labelFormatter={(c: any) => CHANNEL_LABELS[String(c)] ?? String(c)}
        />
        <Bar dataKey="count" radius={[0, 4, 4, 0]} barSize={20}>
          {sorted.map((entry) => (
            <Cell key={entry.channel} fill={CHANNEL_COLORS[entry.channel] ?? "var(--series-8)"} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
