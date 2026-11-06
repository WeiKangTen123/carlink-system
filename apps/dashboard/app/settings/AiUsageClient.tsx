"use client";

import { useState } from "react";
import Link from "next/link";
import { Activity, AlertTriangle, ExternalLink, Loader2 } from "lucide-react";
import { AI_USAGE_DAYS, aiKeySourceLabel, aiRunErrorLabel, aiRunKindLabel, type AiUsage, type AiUsageDays } from "@/lib/api";
import { formatSgtDateTime } from "@/lib/caseFields";
import { getAiUsageAction } from "@/app/reports/actions";

/** A count as the API gave it; "—" when it couldn't (null), never 0. */
function count(n: number | null | undefined): string {
  return typeof n === "number" && Number.isFinite(n) ? n.toLocaleString("en-SG") : "—";
}

/** Record<string, number> as rows, biggest first. */
function rowsOf(record: Record<string, number> | null | undefined): Array<[string, number]> {
  return Object.entries(record || {}).sort((a, b) => b[1] - a[1]);
}

function KpiTile({ label, value, sub }: { label: string; value: string; sub?: string | null }) {
  return (
    <div className="ai-usage-kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-val">{value}</div>
      {sub ? <div className="ai-usage-kpi-sub">{sub}</div> : null}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card-glass setup-card">
      <div className="record-section-title" style={{ marginBottom: 12 }}>
        {title}
      </div>
      {children}
    </div>
  );
}

const EMPTY = <div style={{ fontSize: 12, color: "var(--text-muted)" }}>None in this period</div>;

/** Settings -> AI usage (admins; round 3 B3): the AI run log summed over
 * the last 7, 30 or 90 days, as agentC's section 4 lays it out -- counts
 * (runs, failures by code, 429 waits, tokens), then by key, by kind and by
 * user, then the newest failures with a link to each case. Every figure is
 * the API's own (GET /ai-usage); a count it can't give shows as "—". The
 * page loads 30 days on the server; the selector fetches the rest through
 * getAiUsageAction, so the browser never calls the API directly. */
export function AiUsageClient({
  initial,
  initialError,
  initialDays,
}: {
  initial: AiUsage | null;
  initialError: string | null;
  initialDays: AiUsageDays;
}) {
  const [days, setDays] = useState<AiUsageDays>(initialDays);
  const [usage, setUsage] = useState<AiUsage | null>(initial);
  const [error, setError] = useState<string | null>(initialError);
  const [loading, setLoading] = useState(false);

  const load = async (next: AiUsageDays) => {
    setLoading(true);
    setError(null);
    try {
      const result = await getAiUsageAction(next);
      if ("error" in result) {
        setError(result.error);
        return;
      }
      setUsage(result);
      // Only once the figures for that period are in: after a failure the
      // header must still name the period the figures on screen are for.
      setDays(next);
    } catch {
      // The server action itself failed (connection lost): without this the
      // buttons stayed disabled until a page reload.
      setError("Couldn't load the AI usage. Check the connection and try again.");
    } finally {
      setLoading(false);
    }
  };

  const failedByCode = rowsOf(usage?.failed_by_code);
  const byKind = rowsOf(usage?.by_kind);
  const byKey = usage?.by_key || [];
  const byUser = usage?.by_user || [];
  const failures = usage?.failures || [];

  return (
    <>
      <div className="card-glass setup-card">
        <div className="setup-card-head">
          <div style={{ display: "flex", alignItems: "flex-start", gap: 12, minWidth: 0 }}>
            <div className="setup-card-icon" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Activity style={{ width: 22, height: 22, color: "var(--accent-primary)" }} />
            </div>
            <div style={{ minWidth: 0 }}>
              <div className="card-title" style={{ marginBottom: 2 }}>AI usage</div>
              <div className="card-subtitle" style={{ marginBottom: 0 }}>
                AI calls for photo sorting and reading, the Telegram bot and Annex A suggestions in the last {days} days: how many, how many failed and why, and on whose key
              </div>
            </div>
          </div>
          {/* Buttons, not a select: three choices, and the current one must
              read at a glance. */}
          <div className="ai-usage-days" role="group" aria-label="Period">
            {AI_USAGE_DAYS.map((d) => (
              <button
                key={d}
                type="button"
                className="btn-secondary-modern"
                style={{ fontSize: 11, padding: "5px 12px" }}
                aria-pressed={d === days}
                disabled={loading}
                onClick={() => void load(d)}
              >
                {d} days
              </button>
            ))}
            {loading && <Loader2 className="spin" style={{ width: 14, height: 14, color: "var(--text-muted)" }} />}
          </div>
        </div>

        <div className="setup-card-divider" />

        {error && (
          <div className="setup-error" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <AlertTriangle style={{ width: 14, height: 14, flexShrink: 0 }} />
            <span style={{ flex: 1 }}>{error}</span>
            <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "4px 10px" }} onClick={() => void load(days)} disabled={loading}>
              Try again
            </button>
          </div>
        )}

        {usage && (
          <>
            <div className="ai-usage-kpis">
              <KpiTile label="Runs" value={count(usage.runs)} />
              <KpiTile label="Failed" value={count(usage.failed)} sub={usage.runs ? `${Math.round((100 * usage.failed) / usage.runs)}% of runs` : null} />
              <KpiTile
                label="429 waits"
                value={count(usage.waits_429)}
                sub={typeof usage.waited_s === "number" ? `waited ${count(Math.round(usage.waited_s))} s in all` : "wait time not recorded"}
              />
              <KpiTile label="Tokens in" value={count(usage.tokens_in)} sub={usage.tokens_in === null ? "not reported by the SDK" : null} />
              <KpiTile label="Tokens out" value={count(usage.tokens_out)} sub={usage.tokens_out === null ? "not reported by the SDK" : null} />
            </div>

            <div style={{ marginTop: 16 }}>
              <div className="record-section-title">Failures by code</div>
              {failedByCode.length === 0 ? (
                EMPTY
              ) : (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {failedByCode.map(([code, n]) => (
                    <span key={code} className="ai-run-status danger" style={{ fontSize: 11, padding: "3px 10px" }} title={code}>
                      {aiRunErrorLabel(code)} · {count(n)}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </>
        )}

        {!usage && !error && EMPTY}
      </div>

      {usage && (
        <>
          <div className="ai-usage-grid">
            <Section title="By key">
              {byKey.length === 0 ? (
                EMPTY
              ) : (
                <div className="ai-usage-table-wrap">
                  <table className="users-table">
                    <thead>
                      <tr>
                        <th>Key</th>
                        <th style={{ textAlign: "right" }}>Runs</th>
                        <th style={{ textAlign: "right" }}>Failed</th>
                      </tr>
                    </thead>
                    <tbody>
                      {byKey.map((k, i) => (
                        <tr key={`${k.key_source}-${k.key_last4 ?? ""}-${i}`}>
                          <td>{aiKeySourceLabel(k.key_source, k.key_last4)}</td>
                          <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{count(k.runs)}</td>
                          <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", color: k.failed ? "var(--badge-red-text)" : undefined }}>{count(k.failed)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Section>

            <Section title="By kind">
              {byKind.length === 0 ? (
                EMPTY
              ) : (
                <div className="ai-usage-table-wrap">
                  <table className="users-table">
                    <thead>
                      <tr>
                        <th>What for</th>
                        <th style={{ textAlign: "right" }}>Runs</th>
                      </tr>
                    </thead>
                    <tbody>
                      {byKind.map(([kind, n]) => (
                        <tr key={kind}>
                          <td>{aiRunKindLabel(kind)}</td>
                          <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{count(n)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Section>

            <Section title="By user">
              {byUser.length === 0 ? (
                EMPTY
              ) : (
                <div className="ai-usage-table-wrap">
                  <table className="users-table">
                    <thead>
                      <tr>
                        <th>Who</th>
                        <th style={{ textAlign: "right" }}>Runs</th>
                        <th style={{ textAlign: "right" }}>Failed</th>
                      </tr>
                    </thead>
                    <tbody>
                      {byUser.map((u, i) => (
                        <tr key={`${u.name ?? "none"}-${i}`}>
                          {/* No account behind it: the bot's or the server's own runs. */}
                          <td>{u.name || <span style={{ color: "var(--text-muted)" }}>No account (bot or server)</span>}</td>
                          <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{count(u.runs)}</td>
                          <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", color: u.failed ? "var(--badge-red-text)" : undefined }}>{count(u.failed)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Section>
          </div>

          <Section title={`Newest failures (${failures.length})`}>
            {failures.length === 0 ? (
              EMPTY
            ) : (
              <div className="ai-usage-table-wrap">
                <table className="users-table">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Case</th>
                      <th>What for</th>
                      <th>Why</th>
                      <th>Who</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {failures.map((f, i) => (
                      <tr key={`${f.report_id}-${f.at}-${i}`}>
                        <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, whiteSpace: "nowrap" }} suppressHydrationWarning>
                          {formatSgtDateTime(f.at)}
                        </td>
                        {/* The plate or Our Ref from the case's own data; "—"
                            when it has neither yet. Never a public sender's details. */}
                        <td style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}>{f.plate_or_ref?.trim() || "—"}</td>
                        <td>{aiRunKindLabel(f.kind)}</td>
                        <td title={f.error_code ?? undefined}>{aiRunErrorLabel(f.error_code) || "—"}</td>
                        <td>{f.actor_name || <span style={{ color: "var(--text-muted)" }}>—</span>}</td>
                        <td style={{ textAlign: "right" }}>
                          {/* A draft that failed before filing shares the id,
                              and /reports/{id} sends a draft on to the wizard. */}
                          {f.report_id ? (
                            <Link href={`/reports/${f.report_id}`} prefetch={false} className="btn-secondary-modern" style={{ fontSize: 11, padding: "4px 10px", display: "inline-flex", alignItems: "center", gap: 4 }}>
                              open case <ExternalLink style={{ width: 11, height: 11 }} />
                            </Link>
                          ) : (
                            <span style={{ color: "var(--text-muted)" }}>—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>
        </>
      )}
    </>
  );
}
