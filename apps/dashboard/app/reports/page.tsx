import { listReports, getAnalyticsSummary } from "@/lib/api";
import { ReportsClient } from "./ReportsClient";

export default async function ReportsPage() {
  const [reports, analytics] = await Promise.all([
    listReports().catch((err) => {
      if (err?.digest === "DYNAMIC_SERVER_USAGE") throw err;
      console.warn("Failed to load reports:", err);
      return [];
    }),
    getAnalyticsSummary().catch((err) => {
      if (err?.digest === "DYNAMIC_SERVER_USAGE") throw err;
      return null;
    }),
  ]);
  return <ReportsClient initialReports={reports} initialAnalytics={analytics} />;
}
