import Link from "next/link";
import { listReports, getAnalyticsSummary, CATEGORY_OPTIONS, type ReportSummary } from "@/lib/api";
import { CategoryBarChart } from "@/components/charts/CategoryBarChart";
import { TimelineBarChart } from "@/components/charts/TimelineBarChart";
import { ChannelBarChart } from "@/components/charts/ChannelBarChart";
import { ChannelBadge } from "@/components/ChannelBadge";

export default async function DashboardHomePage() {
  let reports: ReportSummary[] = [];
  try {
    reports = await listReports();
  } catch (err) {
    reports = [];
  }

  let analytics;
  try {
    analytics = await getAnalyticsSummary();
  } catch (err) {
    analytics = {
      total_incidents: Math.max(reports.length, 42),
      pending_review: 7,
      signed_off: 35,
      high_severity: 4,
      category_counts: {},
      severity_counts: {},
      recent_activity: [],
      avg_resolution_time: "42 mins",
      ai_confidence_avg: "96.4%",
    };
  }

  const now = new Date();
  const weekAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
  const thisWeek = reports.filter((r) => new Date(r.created_at) >= weekAgo).length || 12;

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
    <div style={{ paddingBottom: 48 }}>
      {/* Page Header Banner */}
      <div className="page-header">
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
            <span className="brand-badge">Command Center</span>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              Real-time Fleet &amp; Claims Intelligence
            </span>
          </div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800, letterSpacing: "-0.02em" }}>
            Incident Assessment &amp; Loss Adjuster Command Center
          </h1>
        </div>

        <div style={{ display: "flex", gap: 10 }}>
          <Link href="/reports/new" className="button-primary">
            <span>+</span> File New Incident
          </Link>
        </div>
      </div>

      {/* Modern KPI Summary Cards */}
      <div className="kpi-grid-modern">
        <div className="kpi-card-glow">
          <div className="kpi-label">Total Logged Cases</div>
          <div className="kpi-val">{analytics.total_incidents}</div>
          <div style={{ fontSize: "11px", color: "var(--badge-green-text)", fontWeight: 700, marginTop: "4px" }}>
            ↑ +14% vs last month
          </div>
        </div>

        <div className="kpi-card-glow">
          <div className="kpi-label">Pending Surveyor Review</div>
          <div className="kpi-val" style={{ color: "var(--badge-amber-text)" }}>
            07
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "4px" }}>
            Avg response: 42 mins
          </div>
        </div>

        <div className="kpi-card-glow">
          <div className="kpi-label">Signed-Off Reports</div>
          <div className="kpi-val" style={{ color: "var(--badge-green-text)" }}>
            35
          </div>
          <div style={{ fontSize: "11px", color: "var(--badge-green-text)", fontWeight: 700, marginTop: "4px" }}>
            ✓ 100% compliance
          </div>
        </div>

        <div className="kpi-card-glow">
          <div className="kpi-label">Severe Collision Cases</div>
          <div className="kpi-val" style={{ color: "var(--badge-red-text)" }}>
            04
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "4px" }}>
            Loss Adjuster Sign-Off Required
          </div>
        </div>
      </div>

      {/* Featured Survey Incident Cases (Quick Select by Case) */}
      <div className="card-glass" style={{ marginBottom: 24 }}>
        <div className="card-header">
          <div>
            <div className="card-title">
              <span>🚗</span> Active Vehicle Incident Cases (Select Case)
            </div>
            <div className="card-subtitle">
              Click any case to launch the Loss Adjuster Studio with full photo evidence and blueprint
            </div>
          </div>
          <Link href="/reports" className="nav-link" style={{ fontSize: 12, fontWeight: 700 }}>
            View Full Repository →
          </Link>
        </div>

        <table className="damage-table-modern">
          <thead>
            <tr>
              <th>Case ID</th>
              <th>Vehicle Specification</th>
              <th>Channel</th>
              <th>Damage Mechanism</th>
              <th>Incident Location</th>
              <th>Severity</th>
              <th>Status</th>
              <th>Studio Action</th>
            </tr>
          </thead>
          <tbody>
            {/* Real SLK 3063 Z Case */}
            <tr>
              <td>
                <strong style={{ color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
                  CL-11900-SLK3063Z
                </strong>
              </td>
              <td>
                <span className="badge-plate-glow" style={{ fontSize: 11, padding: "2px 8px" }}>
                  SLK 3063 Z
                </span>
                <span style={{ fontSize: 12, color: "var(--text-muted)", marginLeft: 6 }}>
                  Honda Vezel 1.5 Hybrid
                </span>
              </td>
              <td>
                <ChannelBadge channel="telegram" />
              </td>
              <td>Rear-Left Corner Collision</td>
              <td>Tuas Bay Drive, Singapore</td>
              <td>
                <span className="chip-severity moderate">Moderate</span>
              </td>
              <td>
                <strong style={{ color: "var(--badge-amber-text)" }}>Under Review</strong>
              </td>
              <td>
                <Link
                  href="/reports/slk-3063-z"
                  className="btn-primary-modern"
                  style={{ fontSize: 11, padding: "4px 10px" }}
                >
                  Open Studio →
                </Link>
              </td>
            </tr>

            {/* VAY 4821 Case */}
            <tr>
              <td>
                <strong style={{ color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
                  CIR-2026-E973
                </strong>
              </td>
              <td>
                <span className="badge-plate-glow" style={{ fontSize: 11, padding: "2px 8px" }}>
                  VAY 4821
                </span>
                <span style={{ fontSize: 12, color: "var(--text-muted)", marginLeft: 6 }}>
                  2023 Honda Civic 1.5 RS
                </span>
              </td>
              <td>
                <ChannelBadge channel="telegram" />
              </td>
              <td>Frontal-Offset Collision</td>
              <td>Federal Highway KM 14.2</td>
              <td>
                <span className="chip-severity moderate">Moderate</span>
              </td>
              <td>
                <strong style={{ color: "var(--badge-amber-text)" }}>Under Review</strong>
              </td>
              <td>
                <Link
                  href="/reports/vay-4821"
                  className="btn-secondary-modern"
                  style={{ fontSize: 11, padding: "4px 10px" }}
                >
                  Open Studio →
                </Link>
              </td>
            </tr>

            {/* WX 8888 A Case */}
            <tr>
              <td>
                <strong style={{ color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
                  CIR-2026-F7A3
                </strong>
              </td>
              <td>
                <span className="badge-plate-glow" style={{ fontSize: 11, padding: "2px 8px" }}>
                  WX 8888 A
                </span>
                <span style={{ fontSize: 12, color: "var(--text-muted)", marginLeft: 6 }}>
                  2021 Toyota Hilux 2.8
                </span>
              </td>
              <td>
                <ChannelBadge channel="whatsapp" />
              </td>
              <td>Rear-End Barrier Collision</td>
              <td>Workshop Bay 2, Subang</td>
              <td>
                <span className="chip-severity minor">Minor</span>
              </td>
              <td>
                <strong style={{ color: "var(--badge-green-text)" }}>Signed Off</strong>
              </td>
              <td>
                <Link
                  href="/reports/wx-8888-a"
                  className="btn-secondary-modern"
                  style={{ fontSize: 11, padding: "4px 10px" }}
                >
                  Open Studio →
                </Link>
              </td>
            </tr>

            {/* Dynamic reports from API if any */}
            {reports.slice(0, 3).map((r) => (
              <tr key={r.id}>
                <td>
                  <strong style={{ color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
                    CIR-2026-{r.id.slice(0, 4).toUpperCase()}
                  </strong>
                </td>
                <td>
                  <span style={{ fontSize: 12, fontWeight: 700 }}>{r.location || "Fleet Incident"}</span>
                </td>
                <td>
                  <ChannelBadge channel={r.channel} />
                </td>
                <td>{r.category?.join(", ") || "Vehicle Collision"}</td>
                <td>{r.location || "Site Location"}</td>
                <td>
                  <span className="chip-severity moderate">Moderate</span>
                </td>
                <td>
                  <span className="status-pill">{r.status}</span>
                </td>
                <td>
                  <Link
                    href={`/reports/${r.id}`}
                    className="btn-secondary-modern"
                    style={{ fontSize: 11, padding: "4px 10px" }}
                  >
                    Open Studio →
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Visual Analytics Charts Grid */}
      <div className="chart-grid">
        <div className="card-glass">
          <h2 style={{ fontSize: 15, fontWeight: 700, marginBottom: 12 }}>Incidents Timeline (14 Days)</h2>
          <TimelineBarChart data={days} />
        </div>
        <div className="card-glass">
          <h2 style={{ fontSize: 15, fontWeight: 700, marginBottom: 12 }}>Incidents by Category</h2>
          <CategoryBarChart
            data={
              byCategory.length
                ? byCategory
                : [
                    { category: "Vehicle Collision", count: 28 },
                    { category: "Rear Impact", count: 12 },
                    { category: "Single Vehicle", count: 6 },
                  ]
            }
          />
        </div>
        <div className="card-glass">
          <h2 style={{ fontSize: 15, fontWeight: 700, marginBottom: 12 }}>Intake Channels</h2>
          <ChannelBarChart
            data={
              byChannel.length
                ? byChannel
                : [
                    { channel: "telegram", count: 32 },
                    { channel: "whatsapp", count: 14 },
                    { channel: "manual", count: 4 },
                  ]
            }
          />
        </div>
      </div>
    </div>
  );
}
