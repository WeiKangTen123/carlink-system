import { listReports, getAnalyticsSummary } from "@/lib/api";
import { CategoryBarChart } from "@/components/charts/CategoryBarChart";
import { TimelineBarChart } from "@/components/charts/TimelineBarChart";

const SEVERITY_COLORS: Record<string, string> = {
  Minor: "var(--badge-green-text)",
  Moderate: "var(--badge-amber-text)",
  Severe: "var(--badge-red-text)",
};

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
    const count = reports.filter((r) => r.created_at.slice(0, 10) === key).length;
    days.push({ date: key.slice(5), count });
  }

  const byCategory = Object.entries(analytics.category_counts)
    .map(([category, count]) => ({ category, count }))
    .sort((a, b) => b.count - a.count);

  const partsFrequency = Object.entries(analytics.damaged_parts_frequency).sort((a, b) => b[1] - a[1]);
  const maxPartCount = partsFrequency[0]?.[1] ?? 0;
  const topParts = partsFrequency.slice(0, 6);

  const severityTotal = Object.values(analytics.severity_counts).reduce((a, b) => a + b, 0);

  return (
    <div>
      {/* Header */}
      <div className="page-header" style={{ marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>Incident Analytics</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            System-wide incident volume, review pipeline, and damaged-component frequency across all filed reports
          </p>
        </div>
      </div>

      {/* KPI Cards -- every value here comes straight from /analytics/summary,
          computed from real reports; null fields show "--" rather than a
          placeholder number (see AnalyticsSummary's comment in lib/api.ts). */}
      <div className="kpi-grid-modern" style={{ marginBottom: 24 }}>
        <div className="kpi-card-glow">
          <div className="kpi-label">Total Incidents</div>
          <div className="kpi-val">{analytics.total_incidents}</div>
        </div>

        <div className="kpi-card-glow">
          <div className="kpi-label">Pending Review</div>
          <div className="kpi-val" style={{ color: "var(--badge-amber-text)" }}>
            {analytics.pending_review}
          </div>
        </div>

        <div className="kpi-card-glow">
          <div className="kpi-label">Signed Off</div>
          <div className="kpi-val" style={{ color: "var(--badge-green-text)" }}>
            {analytics.signed_off}
          </div>
        </div>

        <div className="kpi-card-glow">
          <div className="kpi-label">Avg. Filing-to-Sign-Off</div>
          <div className="kpi-val" style={{ color: "var(--accent-cyan)" }}>
            {analytics.avg_resolution_time ?? "—"}
          </div>
          {!analytics.avg_resolution_time && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>No signed-off reports yet</div>
          )}
        </div>

        <div className="kpi-card-glow">
          <div className="kpi-label">AI High-Confidence Rate</div>
          <div className="kpi-val" style={{ color: "var(--accent-cyan)" }}>
            {analytics.ai_confidence_avg ?? "—"}
          </div>
          {!analytics.ai_confidence_avg && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>No rated detections yet</div>
          )}
        </div>
      </div>

      {/* Charts Grid */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24, marginBottom: 24 }}>
        <div className="card-glass">
          <div className="card-header">
            <div className="card-title">
              <span>📈</span> Incident Frequency (Last 14 Days)
            </div>
          </div>
          <TimelineBarChart data={days} />
        </div>

        <div className="card-glass">
          <div className="card-header">
            <div className="card-title">
              <span>📊</span> Incidents by Category
            </div>
          </div>
          <CategoryBarChart data={byCategory} />
        </div>
      </div>

      {/* Parts Frequency & Severity Distribution */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
        <div className="card-glass">
          <div className="card-header">
            <div className="card-title">
              <span>🔥</span> Most Frequent Damaged Components
            </div>
          </div>
          {topParts.length === 0 ? (
            <div className="chart-empty" style={{ height: "auto", padding: "24px 0" }}>
              No structured damage recorded yet.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 14, fontSize: 13 }}>
              {topParts.map(([part, count]) => (
                <div key={part}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 600, marginBottom: 4 }}>
                    <span>{part}</span>
                    <span>
                      {count} {count === 1 ? "case" : "cases"}
                    </span>
                  </div>
                  <div style={{ height: 8, background: "var(--surface-elevated)", borderRadius: 4, overflow: "hidden" }}>
                    <div
                      style={{
                        width: `${maxPartCount ? (count / maxPartCount) * 100 : 0}%`,
                        height: "100%",
                        background: "var(--accent-cyan)",
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card-glass">
          <div className="card-header">
            <div className="card-title">
              <span>⚡</span> Severity Breakdown
            </div>
          </div>
          {severityTotal === 0 ? (
            <div className="chart-empty" style={{ height: "auto", padding: "24px 0" }}>
              No reports yet.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 14, fontSize: 13, padding: "8px 0" }}>
              {Object.entries(analytics.severity_counts).map(([sev, count]) => (
                <div key={sev}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 600, marginBottom: 4 }}>
                    <span>{sev}</span>
                    <span>
                      {Math.round((count / severityTotal) * 100)}% ({count} {count === 1 ? "case" : "cases"})
                    </span>
                  </div>
                  <div style={{ height: 8, background: "var(--surface-elevated)", borderRadius: 4, overflow: "hidden" }}>
                    <div
                      style={{
                        width: `${(count / severityTotal) * 100}%`,
                        height: "100%",
                        background: SEVERITY_COLORS[sev] || "var(--accent-cyan)",
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
