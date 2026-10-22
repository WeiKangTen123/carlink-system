import { unstable_rethrow } from "next/navigation";
import { listReports, getAnalyticsSummary } from "@/lib/api";
import { ReportsClient } from "./ReportsClient";

export default async function ReportsPage() {
  const [reports, analytics] = await Promise.all([
    listReports().catch((err) => {
      unstable_rethrow(err);
      console.warn("Failed to load reports:", err);
      return [];
    }),
    getAnalyticsSummary().catch((err) => {
      unstable_rethrow(err);
      return null;
    }),
  ]);
  return <ReportsClient initialReports={reports} initialAnalytics={analytics} />;
}
