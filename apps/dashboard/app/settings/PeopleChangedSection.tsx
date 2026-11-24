"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ListChecks, Loader2 } from "lucide-react";
import { areaName, changes, type ObservationCounts, type ObservationSummary } from "@/lib/observations";
import { getObservationSummaryAction } from "./ai-accuracy/actions";

const OFFLINE = "Couldn't reach the server. Check the connection and try again.";
// Rows shown per list; the API sends at most 50, most changed first.
const SHOWN = 15;

function n(value: number): string {
  return value.toLocaleString("en-SG");
}

function plural(count: number, word: string): string {
  return `${n(count)} ${word}${count === 1 ? "" : "s"}`;
}

function CountsTable<K extends "part" | "make" | "area">({
  title,
  field,
  rows,
  label,
}: {
  title: string;
  field: K;
  rows: Array<ObservationCounts & Record<K, string>>;
  label?: (name: string) => string;
}) {
  const shown = rows.filter((r) => changes(r) > 0).slice(0, SHOWN);
  return (
    <div className="card-glass setup-card">
      <div className="record-section-title" style={{ marginBottom: 12 }}>
        {title}
      </div>
      {shown.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Nothing changed by people yet</div>
      ) : (
        <div className="ai-usage-table-wrap">
          <table className="users-table">
            <thead>
              <tr>
                <th>{field === "part" ? "Part" : field === "make" ? "Make" : "Area"}</th>
                <th style={{ textAlign: "right" }} title="Put on by people: the AI missed them (of which added from a suggestion)">
                  Added
                </th>
                <th style={{ textAlign: "right" }} title="On the AI's list, taken off by people">
                  Removed
                </th>
                <th style={{ textAlign: "right" }} title="Suggested, marked Not damaged">
                  Dismissed
                </th>
                <th style={{ textAlign: "right" }} title="Signed-off cases">
                  Cases
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r[field]}>
                  <td>{label ? label(r[field]) : r[field]}</td>
                  <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", color: r.added ? "var(--badge-red-text)" : undefined }}>
                    {n(r.added)}
                    {r.added_prompted ? (
                      <span style={{ color: "var(--text-muted)" }} title="Added from a suggestion">
                        {" "}
                        ({n(r.added_prompted)})
                      </span>
                    ) : null}
                  </td>
                  <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", color: r.removed ? "var(--badge-amber-text)" : undefined }}>
                    {n(r.removed)}
                  </td>
                  <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{n(r.dismissed)}</td>
                  <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{n(r.cases)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Settings -> AI accuracy, "What people changed" (step 2B; admins -- the
 * page shows the tab to admins only and the API refuses anyone else): on
 * the signed-off cases the AI drafted, how many parts people added (the
 * AI missed them), removed and dismissed, per part, per make and per area,
 * for a range of Singapore dates (GET /admin/observations/summary through
 * getObservationSummaryAction). Counts only: no case, no name. Loads when
 * shown, and again for each range. */
export function PeopleChangedSection() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [summary, setSummary] = useState<ObservationSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (a: string, b: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await getObservationSummaryAction(a, b);
      if ("error" in res) setError(res.error);
      else setSummary(res);
    } catch {
      setError(OFFLINE);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void getObservationSummaryAction("", "")
      .then((res) => {
        if (cancelled) return;
        if ("error" in res) setError(res.error);
        else setSummary(res);
      })
      .catch(() => {
        if (!cancelled) setError(OFFLINE);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const t = summary?.totals;
  const range =
    summary && (summary.from || summary.to)
      ? `${summary.from ?? "the first case"} to ${summary.to ?? "today"}`
      : "all dates";

  return (
    <>
      <div className="card-glass setup-card">
        <div className="setup-card-head">
          <div style={{ display: "flex", alignItems: "flex-start", gap: 12, minWidth: 0 }}>
            <div className="setup-card-icon" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
              <ListChecks style={{ width: 22, height: 22, color: "var(--accent-primary)" }} />
            </div>
            <div style={{ minWidth: 0 }}>
              <div className="card-title" style={{ marginBottom: 2 }}>
                What people changed
              </div>
              <div className="card-subtitle" style={{ marginBottom: 0 }}>
                Parts people added, removed or dismissed before signing off – counts only, {range}
              </div>
            </div>
          </div>
          <div className="range-custom-row" style={{ alignItems: "flex-end" }}>
            <label className="range-custom-field">
              <span>From</span>
              <input type="date" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} aria-label="From date" />
            </label>
            <label className="range-custom-field">
              <span>To</span>
              <input type="date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} aria-label="To date" />
            </label>
            <button
              type="button"
              className="btn-secondary-modern"
              style={{ fontSize: 11, padding: "5px 10px" }}
              disabled={loading}
              onClick={() => void load(from, to)}
            >
              Show
            </button>
            {(from || to) && (
              <button
                type="button"
                className="btn-secondary-modern"
                style={{ fontSize: 11, padding: "5px 10px" }}
                disabled={loading}
                onClick={() => {
                  setFrom("");
                  setTo("");
                  void load("", "");
                }}
              >
                All dates
              </button>
            )}
            {loading && <Loader2 className="spin" style={{ width: 14, height: 14, color: "var(--text-muted)" }} />}
          </div>
        </div>

        <div className="setup-card-divider" />

        {error && (
          <div className="setup-error" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <AlertTriangle style={{ width: 14, height: 14, flexShrink: 0 }} />
            <span style={{ flex: 1 }}>{error}</span>
            <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "4px 10px" }} onClick={() => void load(from, to)} disabled={loading}>
              Try again
            </button>
          </div>
        )}

        {t && t.cases === 0 && (
          <div style={{ fontSize: 13, color: "var(--text-muted)" }}>
            <div style={{ fontWeight: 700, color: "var(--text-secondary)", marginBottom: 4 }}>No signed-off cases in this range</div>
            Each sign-off records which parts people added, took off or dismissed against the AI&apos;s draft.
          </div>
        )}

        {t && t.cases > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 13, color: "var(--text-secondary)" }}>
            <div>
              {plural(t.cases, "signed-off case")}: <strong>{plural(t.added, "part")}</strong> added by people (the AI missed them
              {t.added_prompted ? `; ${n(t.added_prompted)} from a suggestion` : ""}), <strong>{n(t.removed)}</strong> removed,{" "}
              <strong>{n(t.dismissed)}</strong> suggestion{t.dismissed === 1 ? "" : "s"} dismissed, {n(t.kept)} kept as the AI found them.
            </div>
            {t.cases_without_ai_draft > 0 && (
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                {plural(t.cases_without_ai_draft, "case")} without an AI draft left out: every part on them is a person&apos;s.
              </div>
            )}
          </div>
        )}
      </div>

      {summary && t && t.cases > 0 && (
        <div className="ai-usage-grid">
          <CountsTable title="Per part" field="part" rows={summary.by_part} />
          <CountsTable title="Per make" field="make" rows={summary.by_make} />
          <CountsTable title="Per area of the car" field="area" rows={summary.by_area} label={areaName} />
        </div>
      )}
    </>
  );
}
