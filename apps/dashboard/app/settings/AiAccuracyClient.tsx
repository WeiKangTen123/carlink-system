"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, ExternalLink, Gauge, Loader2 } from "lucide-react";
import type { AiAccuracy, AiAccuracySums, AiAccuracyTop } from "@/lib/api";
import { formatSgtDate } from "@/lib/caseFields";
import { getAiAccuracyAction } from "./ai-accuracy/actions";

const ALL = "all";

// Spelled out rather than toLocaleDateString: the server and the browser
// must print the same text (React hydration error #418).
const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** "2026-11" -> "November 2026". */
function monthName(month: string): string {
  const [year, m] = month.split("-").map(Number);
  return year && m >= 1 && m <= 12 ? `${MONTH_NAMES[m - 1]} ${year}` : month;
}

/** A share as the API gave it (0..1); "—" when it couldn't (null). */
function pct(share: number | null | undefined): string {
  return typeof share === "number" && Number.isFinite(share) ? `${Math.round(share * 100)}%` : "—";
}

function num(n: number | null | undefined): string {
  return typeof n === "number" && Number.isFinite(n) ? n.toLocaleString("en-SG") : "—";
}

/** Our Ref, else the REF- name the report files use until one is issued. */
function caseName(reportId: string, ourRef: string | null): string {
  return ourRef?.trim() || `REF-${reportId.slice(0, 6).toUpperCase()}`;
}

function ShareBar({ label, share, tone, title }: { label: string; share: number | null; tone: "minor" | "moderate" | "severe"; title: string }) {
  const width = typeof share === "number" && Number.isFinite(share) ? Math.max(0, Math.min(100, Math.round(share * 100))) : 0;
  return (
    <div
      title={title}
      style={{ display: "grid", gridTemplateColumns: "minmax(0, 200px) 44px minmax(60px, 1fr)", alignItems: "center", gap: 10, fontSize: 13 }}
    >
      <span style={{ minWidth: 0 }}>{label}</span>
      <span style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{pct(share)}</span>
      <div className="severity-bar-track" role="img" aria-label={`${label}: ${pct(share)}`}>
        <div className={`severity-bar-fill ${tone}`} style={{ width: `${width}%` }} />
      </div>
    </div>
  );
}

function TopList({ label, top }: { label: string; top: AiAccuracyTop | undefined }) {
  return (
    <div style={{ fontSize: 13 }}>
      <span style={{ fontWeight: 700 }}>{label}:</span>{" "}
      {top && top.length > 0 ? (
        top.slice(0, 5).map((t, i) => (
          <span key={t.part} title={`${t.cases} case${t.cases === 1 ? "" : "s"}`}>
            {i > 0 ? ", " : ""}
            {t.part}
            <span style={{ color: "var(--text-muted)" }}> ({t.cases})</span>
          </span>
        ))
      ) : (
        <span style={{ color: "var(--text-muted)" }}>none</span>
      )}
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

/** Settings -> AI accuracy (admins; contract 8 L). On a real case there is
 * no answer sheet: the signed-off report is the answer, and each sign-off
 * records how much of the AI's draft it kept (bot-service app/reports/
 * accuracy.py). The summary is one month's (the newest by default) or all
 * of them, picked on the card; the table lists that month's cases with a
 * link to each. Every figure is the API's own (GET /admin/ai-accuracy); a
 * share it can't give shows as "—". */
export function AiAccuracyClient({ initial, initialError }: { initial: AiAccuracy | null; initialError: string | null }) {
  const [accuracy, setAccuracy] = useState<AiAccuracy | null>(initial);
  const [error, setError] = useState<string | null>(initialError);
  const [loading, setLoading] = useState(false);
  const [month, setMonth] = useState<string>(initial?.months[0]?.month ?? ALL);

  const reload = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getAiAccuracyAction();
      if ("error" in result) {
        setError(result.error);
        return;
      }
      setAccuracy(result);
      if (month !== ALL && !result.months.some((m) => m.month === month)) setMonth(result.months[0]?.month ?? ALL);
    } catch {
      setError("Couldn't load the AI accuracy. Check the connection and try again.");
    } finally {
      setLoading(false);
    }
  };

  const picked = month === ALL ? null : accuracy?.months.find((m) => m.month === month) ?? null;
  const sums: AiAccuracySums | null = picked ?? accuracy?.totals ?? null;
  const missed = picked ? picked.most_missed : accuracy?.most_missed;
  const removed = picked ? picked.most_removed : accuracy?.most_removed;
  const cases = (accuracy?.cases ?? []).filter((c) => month === ALL || c.month === month);
  const empty = accuracy !== null && accuracy.totals.cases === 0;
  const r = sums?.rates;
  const annex = sums?.annex_a;
  const fields = sums?.fields;

  return (
    <>
      <div className="card-glass setup-card">
        <div className="setup-card-head">
          <div style={{ display: "flex", alignItems: "flex-start", gap: 12, minWidth: 0 }}>
            <div className="setup-card-icon" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Gauge style={{ width: 22, height: 22, color: "var(--accent-primary)" }} />
            </div>
            <div style={{ minWidth: 0 }}>
              <div className="card-title" style={{ marginBottom: 2 }}>
                AI accuracy{empty ? "" : ` – ${month === ALL ? "all months" : monthName(month)}`}
              </div>
              <div className="card-subtitle" style={{ marginBottom: 0 }}>
                What surveyors kept, removed, added or changed of the AI&apos;s draft before signing off
              </div>
            </div>
          </div>
          {accuracy && !empty && (
            <div className="ai-usage-days">
              <select
                aria-label="Month"
                className="btn-secondary-modern"
                style={{ fontSize: 11, padding: "5px 10px" }}
                value={month}
                onChange={(e) => setMonth(e.target.value)}
              >
                {accuracy.months.map((m) => (
                  <option key={m.month} value={m.month}>
                    {monthName(m.month)}
                  </option>
                ))}
                <option value={ALL}>All months</option>
              </select>
              <span style={{ fontSize: 12, color: "var(--text-muted)", whiteSpace: "nowrap" }}>
                {num(sums?.cases)} signed-off case{sums?.cases === 1 ? "" : "s"}
              </span>
            </div>
          )}
        </div>

        <div className="setup-card-divider" />

        {error && (
          <div className="setup-error" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <AlertTriangle style={{ width: 14, height: 14, flexShrink: 0 }} />
            <span style={{ flex: 1 }}>{error}</span>
            <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "4px 10px" }} onClick={() => void reload()} disabled={loading}>
              Try again
            </button>
            {loading && <Loader2 className="spin" style={{ width: 14, height: 14, color: "var(--text-muted)" }} />}
          </div>
        )}

        {empty && (
          <div style={{ fontSize: 13, color: "var(--text-muted)" }}>
            <div style={{ fontWeight: 700, color: "var(--text-secondary)", marginBottom: 4 }}>No signed-off cases yet</div>
            Each case the AI drafted is measured when it is signed off: the parts, Annex A lines and fields the surveyor kept, removed, added or changed.
          </div>
        )}

        {sums && !empty && r && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <ShareBar label="Parts AI found and kept" share={r.parts_kept} tone="minor" title="Of every part on the AI's list or the signed-off one" />
            <ShareBar label="Removed by people" share={r.parts_removed} tone="moderate" title="On the AI's list, not on the signed-off one" />
            <ShareBar label="Added by people (missed)" share={r.parts_added} tone="severe" title="On the signed-off list, not on the AI's" />

            <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
              <span title="Of the parts kept: placed on the other side">Wrong side {pct(r.wrong_side)}</span>
              {" · "}
              <span title="Of the parts kept: Repair / Replace changed">Repair/Replace changed {pct(r.remarks_changed)}</span>
              {" · "}
              <span title="Of the parts kept: the condition words changed">Condition changed {pct(r.condition_changed)}</span>
              {" · "}
              <span title="Of the Annex A lines kept that the machine priced from past cases: the workshop estimate changed">
                Price changed {pct(r.price_changed)}
              </span>
            </div>
            <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
              <span title="Of the Annex A lines the machine writes from the AI's damage list">
                Annex A lines kept {pct(r.lines_kept)} · removed {pct(r.lines_removed)}
              </span>
              {" · "}
              <span title="Of the signed-off Annex A lines">added by people {pct(r.lines_added)}</span>
              {annex && annex.cases_compared !== undefined && annex.cases_compared < sums.cases && (
                <span style={{ color: "var(--text-muted)" }}> (on {num(annex.cases_compared)} of the cases)</span>
              )}
            </div>
            <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
              <span title="Of the cover and parties fields the AI filled">
                Fields the AI filled: kept {pct(r.fields_kept)} · changed {pct(r.fields_changed)} · emptied {pct(r.fields_emptied)}
              </span>
              {fields && (
                <span title="Fields the AI left blank that a surveyor filled"> · {num(fields.filled)} filled by people</span>
              )}
            </div>

            <TopList label="Most missed" top={missed} />
            <TopList label="Most removed" top={removed} />

            {accuracy && (
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                {accuracy.learning.on
                  ? `Signed-off cases teach the past-case parts lists: ${num(accuracy.learning.cases)} case${accuracy.learning.cases === 1 ? "" : "s"} so far.`
                  : "Signed-off cases don't teach the past-case parts lists (switched off)."}
              </div>
            )}
          </div>
        )}
      </div>

      {accuracy && !empty && (
        <>
          <Section title={`Cases${month === ALL ? "" : ` – ${monthName(month)}`} (${cases.length})`}>
            <div className="ai-usage-table-wrap">
              <table className="users-table">
                <thead>
                  <tr>
                    <th>Case</th>
                    <th>Car</th>
                    <th>Signed off</th>
                    <th style={{ textAlign: "right" }} title="Parts on the AI's list">
                      Found
                    </th>
                    <th style={{ textAlign: "right" }}>Kept</th>
                    <th style={{ textAlign: "right" }}>Removed</th>
                    <th style={{ textAlign: "right" }} title="Parts people added: the AI missed them">
                      Added
                    </th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {cases.map((c) => {
                    const d = c.metrics.damage ?? {};
                    return (
                      <tr key={c.report_id}>
                        <td style={{ fontFamily: "var(--font-mono)", fontWeight: 600, whiteSpace: "nowrap" }}>{caseName(c.report_id, c.our_ref)}</td>
                        <td title={c.models.length ? `Read by ${c.models.join(", ")}` : undefined}>
                          {c.vehicle || <span style={{ color: "var(--text-muted)" }}>Vehicle not recorded</span>}
                        </td>
                        <td style={{ whiteSpace: "nowrap" }} suppressHydrationWarning>
                          {formatSgtDate(c.signed_off_at)}
                        </td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{num(d.ai_found)}</td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                          {num(d.kept)}
                          {d.side_changed ? (
                            <span style={{ color: "var(--badge-amber-text)" }} title="Kept, but on the other side">
                              {" "}
                              ({d.side_changed} side)
                            </span>
                          ) : null}
                        </td>
                        <td
                          style={{ textAlign: "right", fontFamily: "var(--font-mono)", color: d.removed ? "var(--badge-amber-text)" : undefined }}
                          title={d.removed_parts?.length ? d.removed_parts.join(", ") : undefined}
                        >
                          {num(d.removed)}
                        </td>
                        <td
                          style={{ textAlign: "right", fontFamily: "var(--font-mono)", color: d.added ? "var(--badge-red-text)" : undefined }}
                          title={d.added_parts?.length ? d.added_parts.join(", ") : undefined}
                        >
                          {num(d.added)}
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <Link
                            href={`/reports/${c.report_id}`}
                            prefetch={false}
                            className="btn-secondary-modern"
                            style={{ fontSize: 11, padding: "4px 10px", display: "inline-flex", alignItems: "center", gap: 4 }}
                          >
                            open case <ExternalLink style={{ width: 11, height: 11 }} />
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Section>

          <div className="ai-usage-grid">
            <Section title="Annex A lines people add most (all months)">
              {accuracy.lines_added_most.length === 0 ? (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>None yet</div>
              ) : (
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                  {accuracy.lines_added_most.map((l) => (
                    <li key={l.line}>
                      {l.line} <span style={{ color: "var(--text-muted)" }}>({l.cases})</span>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
            <Section title="Annex A lines people remove most (all months)">
              {accuracy.lines_removed_most.length === 0 ? (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>None yet</div>
              ) : (
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                  {accuracy.lines_removed_most.map((l) => (
                    <li key={l.line}>
                      {l.line} <span style={{ color: "var(--text-muted)" }}>({l.cases})</span>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
          </div>
        </>
      )}
    </>
  );
}
