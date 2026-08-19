import Link from "next/link";
import { StudioApp } from "@/components/StudioApp";
import { listReports, getReport } from "@/lib/api";

export default async function DashboardHomePage() {
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

  // listReports() already orders by created_at desc, so [0] is the newest case.
  const report = await getReport(reports[0].id);
  if (!report) {
    return (
      <div className="card-glass" style={{ maxWidth: 520, margin: "80px auto", textAlign: "center", padding: "40px 32px" }}>
        <p style={{ fontSize: 13, color: "var(--text-muted)" }}>Could not load the latest report.</p>
      </div>
    );
  }

  return <StudioApp report={report} />;
}
