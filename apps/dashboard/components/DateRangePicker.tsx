"use client";

import { useState } from "react";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { RANGE_PRESETS, type RangeKey } from "@/lib/dateRange";

/** Range control for the intake-volume chart.
 *
 * Writes to the URL rather than to local state so the page it controls can
 * stay a server component -- the range round-trips through the server, and
 * a chosen window is shareable and bookmarkable.
 */
export function DateRangePicker({
  activeKey,
  from,
  to,
}: {
  activeKey: RangeKey;
  from: string;
  to: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [showCustom, setShowCustom] = useState(activeKey === "custom");
  const [customFrom, setCustomFrom] = useState(from);
  const [customTo, setCustomTo] = useState(to);

  const apply = (params: Record<string, string | null>) => {
    const next = new URLSearchParams(searchParams.toString());
    for (const [k, v] of Object.entries(params)) {
      if (v === null) next.delete(k);
      else next.set(k, v);
    }
    const qs = next.toString();
    router.push(qs ? `${pathname}?${qs}` : pathname);
  };

  const selectPreset = (key: string) => {
    setShowCustom(false);
    // from/to are only meaningful for a custom range -- clearing them keeps
    // the URL honest about what's actually driving the view.
    apply({ range: key, from: null, to: null });
  };

  return (
    <div className="range-picker">
      <div className="range-picker-row">
        {RANGE_PRESETS.map((p) => (
          <button
            key={p.key}
            type="button"
            className={`range-btn ${activeKey === p.key ? "active" : ""}`}
            onClick={() => selectPreset(p.key)}
          >
            {p.label}
          </button>
        ))}
        <button
          type="button"
          className={`range-btn ${activeKey === "custom" ? "active" : ""}`}
          onClick={() => setShowCustom((v) => !v)}
          aria-expanded={showCustom}
        >
          Custom
        </button>
      </div>

      {showCustom && (
        <div className="range-custom-row">
          <label className="range-custom-field">
            <span>From</span>
            <input
              type="date"
              value={customFrom}
              max={customTo || undefined}
              onChange={(e) => setCustomFrom(e.target.value)}
            />
          </label>
          <label className="range-custom-field">
            <span>To</span>
            <input
              type="date"
              value={customTo}
              min={customFrom || undefined}
              onChange={(e) => setCustomTo(e.target.value)}
            />
          </label>
          <button
            type="button"
            className="btn-primary-modern"
            style={{ fontSize: 12, padding: "6px 14px" }}
            disabled={!customFrom || !customTo}
            onClick={() => apply({ range: "custom", from: customFrom, to: customTo })}
          >
            Apply
          </button>
        </div>
      )}
    </div>
  );
}

/** Offered from the empty state so a window with no data is never a dead
 * end -- it says why it's empty, then hands you the range that isn't. */
export function WidenRangeButton({ toKey, label }: { toKey: string; label: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  return (
    <button
      type="button"
      className="btn-secondary-modern"
      style={{ fontSize: 12, padding: "6px 14px", marginTop: 12 }}
      onClick={() => {
        const next = new URLSearchParams(searchParams.toString());
        next.set("range", toKey);
        next.delete("from");
        next.delete("to");
        router.push(`${pathname}?${next.toString()}`);
      }}
    >
      {label}
    </button>
  );
}
