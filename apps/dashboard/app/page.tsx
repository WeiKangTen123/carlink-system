import Link from "next/link";
import { listReports, getAnalyticsSummary, fileUrl, type ReportSummary } from "@/lib/api";
import { caseTitle, daysOpen, severityClass, isAwaitingSignOff, SEVERITY_RANK } from "@/lib/caseFields";
import { TimelineBarChart } from "@/components/charts/TimelineBarChart";

/** A case row shared by both lists. Shows the real photo thumbnail when the
 * report actually has one -- reports filed without photos just get a
 * neutral placeholder rather than a stand-in image of some other vehicle. */
function CaseRow({ report, showAge }: { report: ReportSummary; showAge?: boolean }) {
  const age = daysOpen(report.created_at);
  return (
    <Link href={`/reports/${report.id}`} className="case-row">
      {report.thumbnail_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={fileUrl(report.thumbnail_url)} alt="" className="case-row-thumb" />
      ) : (
        <div className="case-row-thumb case-row-thumb-empty">🚗</div>
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="case-row-title">{caseTitle(report)}</div>
        <div className="case-row-meta">
          {report.vehicle_name && report.plate_number ? `${report.vehicle_name} · ` : ""}
          {report.damage_count > 0 ? `${report.damage_count} damaged part${report.damage_count === 1 ? "" : "s"}` : "No damage recorded"}
          {report.location ? ` · ${report.location}` : ""}
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
        {report.severity_level && (
          <span className={`chip-severity ${severityClass(report.severity_level)}`} style={{ fontSize: 10 }}>
            {report.severity_level}
          </span>
        )}
        {showAge && <span className="case-row-age">{age === 0 ? "today" : `${age}d`}</span>}
      </div>
    </Link>
  );
}

export default async function OverviewPage() {
  const reports = await listReports();

  if (reports.length === 0) {
    return (
      <div className="card-glass" style={{ maxWidth: 520, margin: "80px auto", textAlign: "center", padding: "40px 32px" }}>
        <div style={{ fontSize: 40, marginBottom: 12 }}>📋</div>
        <h1 style={{ fontSize: 20, fontWeight: 800, marginBottom: 8 }}>No incidents filed yet</h1>
        <p style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 20 }}>
          File your first incident report to see it here in the Loss Adjuster Studio.
        </p>
        <Link href="/reports/new" className="btn-primary-modern">
          <span>✨</span> File New Incident
        </Link>
      </div>
    );
  }

  const analytics = await getAnalyticsSummary();

  // The priority queue: everything still awaiting sign-off, worst first,
  // then oldest first within the same severity. This is the page's whole
  // reason to exist -- "which case do I open right now?" -- so it's the
  // hero, not a sidebar afterthought.
  const priorityQueue = reports
    .filter((r) => isAwaitingSignOff(r.status))
    .sort((a, b) => {
      const sevDiff = (SEVERITY_RANK[severityClass(b.severity_level)] ?? 0) - (SEVERITY_RANK[severityClass(a.severity_level)] ?? 0);
      if (sevDiff !== 0) return sevDiff;
      return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
    });

  const recentCases = reports.slice(0, 5);

  // Real day-by-day count from actual report creation dates -- same
  // derivation the Analytics page already uses, no random walk.
  const now = new Date();
  const days: { date: string; count: number }[] = [];
  for (let i = 13; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    days.push({ date: key.slice(5), count: reports.filter((r) => r.created_at.slice(0, 10) === key).length });
  }

  const severityTotal = Object.values(analytics.severity_counts).reduce((a, b) => a + b, 0);

  return (
    <div>
      {/* Greeting strip -- states the actual situation in one line rather
          than a generic page title. */}
      <div className="page-header" style={{ marginBottom: 20 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>Overview</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            {priorityQueue.length > 0 ? (
              <>
                <strong style={{ color: "var(--text-primary)" }}>{priorityQueue.length}</strong> case
                {priorityQueue.length === 1 ? "" : "s"} awaiting sign-off
                {analytics.high_severity > 0 && (
                  <> &bull; <strong style={{ color: "var(--badge-red-text)" }}>{analytics.high_severity}</strong> rated severe</>
                )}
              </>
            ) : (
              "All cases signed off — nothing awaiting review"
            )}
          </p>
        </div>
        <Link href="/reports/new" className="btn-primary-modern">
          <span>✨</span> File New Incident
        </Link>
      </div>

      {/* Compact KPI strip -- every value from /analytics/summary, computed
          from real reports; null fields show "—" rather than a placeholder. */}
      <div className="kpi-grid-modern" style={{ marginBottom: 24 }}>
        <div className="kpi-card-glow">
          <div className="kpi-label">Awaiting Sign-Off</div>
          <div className="kpi-val" style={{ color: "var(--badge-amber-text)" }}>{analytics.pending_review}</div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Severe</div>
          <div className="kpi-val" style={{ color: "var(--badge-red-text)" }}>{analytics.high_severity}</div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Signed Off</div>
          <div className="kpi-val" style={{ color: "var(--badge-green-text)" }}>{analytics.signed_off}</div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Avg. Filing-to-Sign-Off</div>
          <div className="kpi-val" style={{ color: "var(--accent-cyan)" }}>{analytics.avg_resolution_time ?? "—"}</div>
          {!analytics.avg_resolution_time && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>No signed-off reports yet</div>
          )}
        </div>
      </div>

      {/* Priority Queue -- the hero. */}
      <div className="card-glass priority-queue-card" style={{ marginBottom: 20 }}>
        <div className="card-header">
          <div>
            <div className="card-title">
              <span>🔥</span> Priority Queue
            </div>
            <div className="card-subtitle">Awaiting sign-off &mdash; most severe first, then longest open</div>
          </div>
          <span className="chip-severity minor" style={{ fontSize: 10 }}>{priorityQueue.length} pending</span>
        </div>
        {priorityQueue.length === 0 ? (
          <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>
            Nothing awaiting sign-off. Every filed case has been reviewed.
          </p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {priorityQueue.map((r) => (
              <CaseRow key={r.id} report={r} showAge />
            ))}
          </div>
        )}
      </div>

      {/* Supporting context -- volume trend + severity mix + recent filings. */}
      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 20 }}>
        <div className="card-glass">
          <div className="card-header">
            <div>
              <div className="card-title">
                <span>📈</span> Incident Volume
              </div>
              <div className="card-subtitle">Reports filed per day, last 14 days</div>
            </div>
          </div>
          <TimelineBarChart data={days} />
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="card-glass">
            <div className="card-header">
              <div className="card-title">
                <span>⚠️</span> Severity Mix
              </div>
            </div>
            {severityTotal === 0 ? (
              <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>No severity ratings yet.</p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {(["Severe", "Moderate", "Minor"] as const).map((label) => {
                  const count = analytics.severity_counts[label] ?? 0;
                  const pct = severityTotal > 0 ? Math.round((count / severityTotal) * 100) : 0;
                  return (
                    <div key={label}>
                      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 4 }}>
                        <span style={{ fontWeight: 600 }}>{label}</span>
                        <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                          {count} &bull; {pct}%
                        </span>
                      </div>
                      <div className="severity-bar-track">
                        <div className={`severity-bar-fill ${severityClass(label)}`} style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div className="card-glass">
            <div className="card-header">
              <div className="card-title">
                <span>🕒</span> Recently Filed
              </div>
              <Link href="/reports" style={{ fontSize: 11 }}>View all &rarr;</Link>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {recentCases.map((r) => (
                <CaseRow key={r.id} report={r} />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
