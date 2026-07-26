"use client";

import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

// Fixed categorical order, matching the schema's category list -- color
// identity stays consistent across dashboard visits regardless of which
// categories happen to have reports. See the dataviz skill's palette.
const CATEGORY_COLORS: Record<string, string> = {
  "Unauthorized Access": "var(--series-1)",
  "Theft or Burglary": "var(--series-2)",
  "Vandalism or Property Damage": "var(--series-3)",
  "Assault or Threat": "var(--series-4)",
  "Harassment": "var(--series-5)",
  "Vehicle Collision or Damage": "var(--series-6)",
  "Cybersecurity Breach": "var(--series-7)",
  "Other": "var(--series-8)",
};

export function CategoryBarChart({ data }: { data: { category: string; count: number }[] }) {
  if (data.length === 0) {
    return <div className="chart-empty">No categorized reports yet.</div>;
  }
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16, top: 8, bottom: 8 }}>
        <CartesianGrid horizontal={false} stroke="var(--gridline)" />
        <XAxis type="number" allowDecimals={false} stroke="var(--muted)" fontSize={12} />
        <YAxis type="category" dataKey="category" width={170} stroke="var(--muted)" fontSize={11} />
        <Tooltip
          contentStyle={{
            background: "var(--surface-1)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontSize: 12,
          }}
          cursor={{ fill: "var(--gridline)" }}
        />
        <Bar dataKey="count" radius={[0, 4, 4, 0]} barSize={16}>
          {data.map((entry) => (
            <Cell key={entry.category} fill={CATEGORY_COLORS[entry.category] ?? "var(--series-8)"} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
