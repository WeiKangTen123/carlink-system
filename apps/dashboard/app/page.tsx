import { unstable_rethrow } from "next/navigation";
import Link from "next/link";
import { ClipboardList, FilePlus2, Inbox } from "lucide-react";
import {
  listReports,
  getAnalyticsSummary,
  firmWideScope,
  countInbox,
  getCurrentUser,
  EMPTY_ANALYTICS_SUMMARY,
  type InboxCount,
} from "@/lib/api";
import { CommandCenterClient } from "@/components/CommandCenterClient";

export default async function OverviewPage() {
  // The whole firm for anyone who sees every case; plain users their own.
  const scope = await firmWideScope();
  // The Public Inbox card (release P6): for people who see all cases.
  const me = await getCurrentUser();
  const seesInbox = Boolean(me && (me.role === "admin" || me.can_view_all_cases));
  // Parallel resilient data fetching to prevent cascading downtime
  const [reports, analytics, inbox] = await Promise.all([
    listReports(scope).catch((err) => {
      unstable_rethrow(err);
      console.warn("Failed to load reports for overview:", err);
      return [];
    }),
    getAnalyticsSummary(scope).catch((err) => {
      unstable_rethrow(err);
      console.warn("Failed to load analytics for overview:", err);
      return EMPTY_ANALYTICS_SUMMARY;
    }),
    seesInbox
      ? countInbox().catch((err): InboxCount | null => {
          unstable_rethrow(err);
          console.warn("Failed to count the Public Inbox for overview:", err);
          return null;
        })
      : Promise.resolve(null),
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
        {inbox && inbox.new > 0 && (
          <p style={{ fontSize: 13, color: "var(--text-muted)", margin: "18px 0 0" }}>
            <Link href="/inbox" prefetch={false} className="setup-help-link" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <Inbox style={{ width: 14, height: 14 }} /> {inbox.new} report{inbox.new === 1 ? "" : "s"} from the public
              waiting in the Public Inbox
            </Link>
          </p>
        )}
      </div>
    );
  }

  return <CommandCenterClient initialReports={reports} analytics={analytics} inbox={inbox} />;
}
