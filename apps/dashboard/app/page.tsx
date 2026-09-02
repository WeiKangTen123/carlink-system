import Link from "next/link";
import { listReports, getAnalyticsSummary, type ReportSummary } from "@/lib/api";

const PENDING_STATUSES = new Set(["confirmed", "draft", "pending", "Under Review"]);

// Plate is the most specific real identifier a case has; vehicle name is
// the next best thing; category is the last resort -- and a weak one here,
// since every real report in this app shares the same category ("Vehicle
// Collision or Damage"), so falling back to it makes every row look
// identical instead of actually telling cases apart.
function caseTitle(r: ReportSummary): string {
  return r.plate_number || r.vehicle_name || r.category[0] || "Incident";
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
  const recentCases = reports.slice(0, 6);
  const needsAttention = reports.filter((r) => PENDING_STATUSES.has(r.status)).slice(0, 6);

  return (
    <div>
      <div className="page-header" style={{ marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>Overview</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            Command center for all filed incident reports
          </p>
        </div>
        <Link href="/reports/new" className="btn-primary-modern">
          <span>✨</span> File New Incident
        </Link>
      </div>

      {/* KPI tiles -- same real fields the Analytics page's own KPI cards
          use (get_analytics_summary in api/main.py), just the four most
          relevant for an at-a-glance overview rather than the full set. */}
      <div className="kpi-grid-modern" style={{ marginBottom: 24 }}>
        <div className="kpi-card-glow">
          <div className="kpi-label">Open Cases</div>
          <div className="kpi-val" style={{ color: "var(--badge-amber-text)" }}>{analytics.pending_review}</div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Signed Off</div>
          <div className="kpi-val" style={{ color: "var(--badge-green-text)" }}>{analytics.signed_off}</div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">High Severity</div>
          <div className="kpi-val" style={{ color: "var(--badge-red-text)" }}>{analytics.high_severity}</div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Avg. Filing-to-Sign-Off</div>
          <div className="kpi-val" style={{ color: "var(--accent-cyan)" }}>{analytics.avg_resolution_time ?? "—"}</div>
          {!analytics.avg_resolution_time && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>No signed-off reports yet</div>
          )}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        {/* Needs Attention -- same "not yet signed off" bucket pending_review
            already counts, filtered from the real report list, not a
            separate query. */}
        <div className="card-glass">
          <div className="card-header">
            <div className="card-title">
              <span>⏳</span> Needs Attention
            </div>
            <span className="chip-severity minor" style={{ fontSize: 10 }}>{needsAttention.length} shown</span>
          </div>
          {needsAttention.length === 0 ? (
            <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>Nothing pending review right now.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {needsAttention.map((r) => (
                <Link
                  key={r.id}
                  href={`/reports/${r.id}`}
                  style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 10px", borderRadius: 8, border: "1px solid var(--border-color)", textDecoration: "none" }}
                >
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>
                      {caseTitle(r)}{r.location ? ` — ${r.location}` : ""}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                      {new Date(r.created_at).toLocaleDateString()} &bull; {r.channel}
                    </div>
                  </div>
                  <span className="chip-severity minor" style={{ fontSize: 10 }}>{r.status}</span>
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* Recent Cases -- newest first, listReports() already sorts this way. */}
        <div className="card-glass">
          <div className="card-header">
            <div className="card-title">
              <span>🕒</span> Recent Cases
            </div>
            <Link href="/reports" style={{ fontSize: 11 }}>View all &rarr;</Link>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {recentCases.map((r) => (
              <Link
                key={r.id}
                href={`/reports/${r.id}`}
                style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 10px", borderRadius: 8, border: "1px solid var(--border-color)", textDecoration: "none" }}
              >
                <div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>
                    {caseTitle(r)}{r.location ? ` — ${r.location}` : ""}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                    {new Date(r.created_at).toLocaleDateString()} &bull; {r.channel}
                  </div>
                </div>
                <span className="chip-severity minor" style={{ fontSize: 10 }}>{r.status}</span>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
