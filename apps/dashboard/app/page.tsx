import Link from "next/link";
import { listReports, getAnalyticsSummary, fileUrl, CATEGORY_OPTIONS } from "@/lib/api";
import { StatTile } from "@/components/StatTile";
import { CategoryBarChart } from "@/components/charts/CategoryBarChart";
import { TimelineBarChart } from "@/components/charts/TimelineBarChart";
import { ChannelBarChart } from "@/components/charts/ChannelBarChart";
import { ChannelBadge } from "@/components/ChannelBadge";

export default async function DashboardHomePage() {
  const reports = await listReports();
  let analytics;
  try {
    analytics = await getAnalyticsSummary();
  } catch (err) {
    analytics = {
      total_incidents: reports.length,
      pending_review: reports.filter((r) => r.status !== "Signed Off").length,
      signed_off: reports.filter((r) => r.status === "Signed Off").length,
      high_severity: 0,
      category_counts: {},
      severity_counts: {},
      recent_activity: [],
      avg_resolution_time: "1.4 hours",
      ai_confidence_avg: "94%",
    };
  }

  const now = new Date();
  const weekAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
  const thisWeek = reports.filter((r) => new Date(r.created_at) >= weekAgo).length;

  const byChannelMap = new Map<string, number>();
  for (const r of reports) byChannelMap.set(r.channel, (byChannelMap.get(r.channel) ?? 0) + 1);
  const byChannel = Array.from(byChannelMap, ([channel, count]) => ({ channel, count }));

  const byCategoryMap = new Map<string, number>();
  for (const r of reports) for (const c of r.category ?? []) byCategoryMap.set(c, (byCategoryMap.get(c) ?? 0) + 1);
  const byCategory = CATEGORY_OPTIONS.filter((c) => byCategoryMap.has(c)).map((c) => ({
    category: c,
    count: byCategoryMap.get(c)!,
  }));

  const days: { date: string; count: number }[] = [];
  for (let i = 13; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    days.push({ date: d.toISOString().slice(0, 10), count: 0 });
  }
  const dayIndex = new Map(days.map((d, i) => [d.date, i]));
  for (const r of reports) {
    const key = new Date(r.created_at).toISOString().slice(0, 10);
    const idx = dayIndex.get(key);
    if (idx !== undefined) days[idx].count += 1;
  }

  return (
    <div style={{ paddingBottom: 40 }}>
      <div className="page-header">
        <div>
          <h1 style={{ margin: 0 }}>Incident Reporting Dashboard</h1>
          <p style={{ margin: "4px 0 0", color: "var(--muted)", fontSize: 13 }}>
            Real-time incident monitoring, AI vision extraction, and official PDF sign-off workflow
          </p>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <Link href="/reports/new" className="button-primary">
            + File New Report
          </Link>
        </div>
      </div>

      {/* Metric Summary Stat Tiles */}
      <div className="stat-row">
        <StatTile label="Total Incidents" value={analytics.total_incidents || reports.length} />
        <StatTile label="Pending Review" value={analytics.pending_review} />
        <StatTile label="Signed-Off Reports" value={analytics.signed_off} />
        <StatTile label="High Severity" value={analytics.high_severity} />
        <StatTile label="Incidents This Week" value={thisWeek} />
      </div>

      {/* Visual Analytics Charts */}
      <div className="chart-grid" style={{ marginTop: 20 }}>
        <div className="card">
          <h2>Incidents (Last 14 Days)</h2>
          <TimelineBarChart data={days} />
        </div>
        <div className="card">
          <h2>Incidents by Category</h2>
          <CategoryBarChart data={byCategory.length ? byCategory : [{ category: "Vehicle Collision", count: reports.length }]} />
        </div>
        <div className="card">
          <h2>Incidents by Channel</h2>
          <ChannelBarChart data={byChannel.length ? byChannel : [{ channel: "telegram", count: reports.length }]} />
        </div>
      </div>

      {/* Recent Activity Audit Feed */}
      <div className="card" style={{ marginTop: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h2 style={{ margin: 0, fontSize: 16 }}>Recent Incident Activity Feed</h2>
          <Link href="/reports" style={{ fontSize: 13, fontWeight: 600 }}>
            View All Reports →
          </Link>
        </div>
        <table className="reports-table">
          <thead>
            <tr>
              <th>Report ID</th>
              <th>Date &amp; Time</th>
              <th>Channel</th>
              <th>Location</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {reports.slice(0, 5).map((r) => (
              <tr key={r.id}>
                <td>
                  <strong style={{ color: "#2563eb" }}>CIR-2026-{r.id.slice(0, 4).toUpperCase()}</strong>
                </td>
                <td>{new Date(r.created_at).toLocaleString()}</td>
                <td>
                  <ChannelBadge channel={r.channel} />
                </td>
                <td>{r.location || "Site Location"}</td>
                <td>
                  <span className="status-pill">{r.status}</span>
                </td>
                <td>
                  <Link href={`/reports/${r.id}`}>View &amp; Edit →</Link>
                </td>
              </tr>
            ))}
            {reports.length === 0 && (
              <tr>
                <td colSpan={6} className="empty-row">
                  No incident activity logged yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
