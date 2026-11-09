"use client";

import { useEffect, useState } from "react";
import { Gauge } from "lucide-react";
import { localClock, localZoneName, roleLabel, usageWords, type AiAllowance, type AllowanceKey } from "@/lib/aiAllowance";
import { getMyAiAllowanceAction } from "./actions";
import { maskedKey } from "./aiKeyWords";

/** "Today" on My AI key: how much of each key's daily Gemini allowance has
 * gone, per model (GET /auth/me/ai-allowance, lib/aiAllowance.ts). Free keys
 * have small daily limits, so this is where someone sees why the AI stopped
 * and when it comes back.
 *
 * Loaded in the browser after the card shows, and again whenever
 * `refreshOn` changes (a key saved, switched or removed). The reset time is
 * the viewer's own clock, so it can only be worked out here. Nothing to
 * show, or no such route on this API: no panel at all. */
export function AiAllowancePanel({ refreshOn }: { refreshOn: string }) {
  const [data, setData] = useState<AiAllowance | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMyAiAllowanceAction()
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch(() => {
        if (!cancelled) setData(null);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshOn]);

  if (!data || data.keys.length === 0) return null;
  const now = new Date();
  const zone = localZoneName(now);
  return (
    <section className="ai-allowance" aria-label="Today's AI allowance">
      <div className="ai-allowance-head">
        <Gauge style={{ width: 14, height: 14, flexShrink: 0 }} />
        <span>
          Today{" "}
          <span className="ai-allowance-reset">
            (resets {localClock(data.day_resets_at, now)}
            {zone ? ` ${zone}` : ""})
          </span>
        </span>
      </div>
      {data.keys.map((k, i) => (
        <KeyAllowance key={`${k.source}-${k.last4 ?? i}`} k={k} now={now} />
      ))}
    </section>
  );
}

function keyTitle(k: AllowanceKey): string {
  if (k.source === "company") return "Company key";
  const type = k.billed === true ? " · Billed" : k.billed === false ? " · Free" : "";
  return `Your key ${maskedKey(k.last4)}${type}`;
}

function KeyAllowance({ k, now }: { k: AllowanceKey; now: Date }) {
  return (
    <div className="ai-allowance-key">
      <div className="ai-allowance-key-title">{keyTitle(k)}</div>
      <div className="ai-allowance-rows">
        {k.models.map((m) => {
          const used = usageWords(m, now);
          return (
            <div key={`${m.role}-${m.model}`} className="ai-allowance-row">
              <span className="ai-allowance-role">{roleLabel(m.role)}</span>
              <span className="ai-allowance-model">{m.model}</span>
              <span className={`ai-allowance-used${used.spent ? " spent" : ""}`}>{used.text}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
