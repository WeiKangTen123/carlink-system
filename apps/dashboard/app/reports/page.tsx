import { listReports } from "@/lib/api";
import { ReportsClient } from "./ReportsClient";

export default async function ReportsPage() {
  const reports = await listReports();
  return <ReportsClient initialReports={reports} />;
}
