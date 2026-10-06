import { listReports, getAnalyticsSummary } from "@/lib/api";
import { ReportsClient } from "./ReportsClient";

export default async function ReportsPage() {
  const [reports, analytics] = await Promise.all([
    listReports(),
    getAnalyticsSummary().catch(() => null),
  ]);
  return <ReportsClient initialReports={reports} initialAnalytics={analytics} />;
}
