import Link from "next/link";
import { ClipboardList, FilePlus2 } from "lucide-react";
import { listReports, getAnalyticsSummary } from "@/lib/api";
import { CommandCenterClient } from "@/components/CommandCenterClient";

export default async function OverviewPage() {
  // Parallel data fetching eliminates sequential waterfall latency
  const [reports, analytics] = await Promise.all([
    listReports(),
    getAnalyticsSummary(),
  ]);

  if (reports.length === 0) {
    return (
      <div
        className="card-glass"
        style={{
          maxWidth: 560,
          margin: "80px auto",
          textAlign: "center",
          padding: "48px 36px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "center", marginBottom: 16 }}>
          <ClipboardList style={{ width: 48, height: 48, color: "var(--accent-primary)" }} />
        </div>
        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            color: "var(--accent-cyan)",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            marginBottom: 6,
          }}
        >
          Surveyor Command Center
        </div>
        <h1 style={{ fontSize: 22, fontWeight: 800, marginBottom: 10, letterSpacing: "-0.02em" }}>
          Ready for Case Intake
        </h1>
        <p style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 24, lineHeight: 1.6 }}>
          No active vehicle incident reports currently on file. New cases submitted via the Telegram / WhatsApp intake bots or created manually will automatically appear on your Surveyor Triage Console.
        </p>
        <Link href="/reports/new" className="btn-primary-modern" style={{ padding: "10px 22px" }}>
          <FilePlus2 style={{ width: 15, height: 15 }} /> File New Case Intake
        </Link>
      </div>
    );
  }

  return <CommandCenterClient initialReports={reports} analytics={analytics} />;
}
