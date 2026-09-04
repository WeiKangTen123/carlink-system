import Link from "next/link";
import { listReports, getAnalyticsSummary } from "@/lib/api";
import { caseTitle, daysOpen, isAwaitingSignOff } from "@/lib/caseFields";
import { TimelineBarChart } from "@/components/charts/TimelineBarChart";

const SEVERITY_META: { key: string; label: string; color: string }[] = [
  { key: "Severe", label: "Severe", color: "var(--chart-severe)" },
  { key: "Moderate", label: "Moderate", color: "var(--chart-moderate)" },
  { key: "Minor", label: "Minor", color: "var(--chart-minor)" },
];

export default async function AnalyticsPage() {
  const reports = await listReports();
  const analytics = await getAnalyticsSummary();

  // Real day-by-day count from actual report creation dates, not a random
  // walk -- each report's created_at (UTC ISO string) bucketed by its date.
  const now = new Date();
  const days: { date: string; count: number }[] = [];
  for (let i = 13; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    days.push({ date: key.slice(5), count: reports.filter((r) => r.created_at.slice(0, 10) === key).length });
  }

  const partsFrequency = Object.entries(analytics.damaged_parts_frequency).sort((a, b) => b[1] - a[1]);
  const topParts = partsFrequency.slice(0, 8);
  const maxPartCount = topParts[0]?.[1] ?? 0;
  const totalDamageItems = partsFrequency.reduce((sum, [, n]) => sum + n, 0);

  const severityTotal = SEVERITY_META.reduce((sum, s) => sum + (analytics.severity_counts[s.key] ?? 0), 0);

  // Pipeline ageing -- only cases still awaiting sign-off, since a
  // signed-off case isn't "waiting" for anything.
  const open = reports.filter((r) => isAwaitingSignOff(r.status));
  const buckets = [
    { label: "0–3 days", min: 0, max: 3 },
    { label: "4–7 days", min: 4, max: 7 },
    { label: "8–14 days", min: 8, max: 14 },
    { label: "15+ days", min: 15, max: Infinity },
  ].map((b) => ({
    ...b,
    cases: open.filter((r) => {
      const d = daysOpen(r.created_at);
      return d >= b.min && d <= b.max;
    }),
  }));
  const maxBucket = Math.max(1, ...buckets.map((b) => b.cases.length));
  const oldest = [...open].sort((a, b) => daysOpen(b.created_at) - daysOpen(a.created_at)).slice(0, 5);

  // Stated plainly rather than hidden: with a handful of reports, a
  // confident-looking distribution chart would imply a pattern the data
  // can't support. Shown as a note instead of suppressing the panels,
  // since the counts themselves are still real and useful.
  const lowVolume = reports.length < 20;

  return (
    <div>
      <div className="page-header" style={{ marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>Analytics</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            Workload, parts demand, and where cases are getting stuck
          </p>
        </div>
      </div>

      {lowVolume && reports.length > 0 && (
        <div className="analytics-lowvolume-note">
          Based on <strong>{reports.length} report{reports.length === 1 ? "" : "s"}</strong> and{" "}
          <strong>{totalDamageItems} damage item{totalDamageItems === 1 ? "" : "s"}</strong>. Counts below are exact,
          but there isn&apos;t enough history yet for the proportions to indicate a reliable trend.
        </div>
      )}

      <div className="kpi-grid-modern" style={{ marginBottom: 24 }}>
        <div className="kpi-card-glow">
          <div className="kpi-label">Total Cases</div>
          <div className="kpi-val">{analytics.total_incidents}</div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Awaiting Sign-Off</div>
          <div className="kpi-val" style={{ color: "var(--badge-amber-text)" }}>{analytics.pending_review}</div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Severe Cases</div>
          <div className="kpi-val" style={{ color: "var(--badge-red-text)" }}>{analytics.high_severity}</div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Damage Items Logged</div>
          <div className="kpi-val" style={{ color: "var(--accent-cyan)" }}>{totalDamageItems}</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, marginBottom: 20 }}>
        {/* Decision: what parts should I keep in stock? */}
        <div className="card-glass">
          <div className="card-header">
            <div>
              <div className="card-title">
                <span>🔧</span> Parts Demand
              </div>
              <div className="card-subtitle">
                Most frequently damaged components{partsFrequency.length > 8 ? ` — top 8 of ${partsFrequency.length}` : ""}
              </div>
            </div>
          </div>
          {topParts.length === 0 ? (
            <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>No structured damage recorded yet.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {topParts.map(([part, count]) => (
                <div key={part}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 12, marginBottom: 4 }}>
                    <span style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{part}</span>
                    <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)", flexShrink: 0 }}>{count}</span>
                  </div>
                  <div className="severity-bar-track">
                    <div
                      className="readiness-bar-fill"
                      style={{ width: `${maxPartCount ? (count / maxPartCount) * 100 : 0}%`, background: "var(--accent-cyan)" }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Decision: what's going stale and needs chasing? */}
        <div className="card-glass">
          <div className="card-header">
            <div>
              <div className="card-title">
                <span>⏱</span> Pipeline Age
              </div>
              <div className="card-subtitle">How long open cases have been awaiting sign-off</div>
            </div>
          </div>
          {open.length === 0 ? (
            <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>Nothing awaiting sign-off.</p>
          ) : (
            <>
              <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 16 }}>
                {buckets.map((b) => (
                  <div key={b.label}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 4 }}>
                      <span style={{ fontWeight: 600 }}>{b.label}</span>
                      <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{b.cases.length}</span>
                    </div>
                    <div className="severity-bar-track">
                      <div
                        className="readiness-bar-fill"
                        style={{
                          width: `${(b.cases.length / maxBucket) * 100}%`,
                          background: b.min >= 15 ? "var(--chart-severe)" : b.min >= 8 ? "var(--chart-moderate)" : "var(--accent-cyan)",
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>

              <div className="detail-field-label" style={{ marginBottom: 8 }}>Longest waiting</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {oldest.map((r) => (
                  <Link key={r.id} href={`/reports/${r.id}`} className="analytics-case-link">
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 700 }}>{caseTitle(r)}</span>
                    <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                      {daysOpen(r.created_at)}d
                    </span>
                  </Link>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 20 }}>
        <div className="card-glass">
          <div className="card-header">
            <div>
              <div className="card-title">
                <span>📈</span> Intake Volume
              </div>
              <div className="card-subtitle">Reports filed per day, last 14 days</div>
            </div>
          </div>
          <TimelineBarChart data={days} />
        </div>

        <div className="card-glass">
          <div className="card-header">
            <div>
              <div className="card-title">
                <span>⚡</span> Severity Mix
              </div>
              <div className="card-subtitle">Across all filed cases</div>
            </div>
          </div>
          {severityTotal === 0 ? (
            <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>No severity ratings yet.</p>
          ) : (
            <>
              {/* Part-to-whole -> one stacked bar rather than a pie. */}
              <div className="stacked-severity-bar" style={{ marginBottom: 12 }}>
                {SEVERITY_META.map((s) => {
                  const count = analytics.severity_counts[s.key] ?? 0;
                  if (count === 0) return null;
                  return (
                    <div key={s.key} style={{ width: `${(count / severityTotal) * 100}%`, background: s.color }} title={`${s.label}: ${count}`} />
                  );
                })}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {SEVERITY_META.map((s) => {
                  const count = analytics.severity_counts[s.key] ?? 0;
                  return (
                    <div key={s.key} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
                      <span style={{ width: 8, height: 8, borderRadius: 2, background: s.color, flexShrink: 0 }} />
                      <span style={{ fontWeight: 600 }}>{s.label}</span>
                      <span style={{ marginLeft: "auto", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                        {count} · {severityTotal ? Math.round((count / severityTotal) * 100) : 0}%
                      </span>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
