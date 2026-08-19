import { StudioApp } from "@/components/StudioApp";
import { listReports, type ReportSummary } from "@/lib/api";

export default async function DashboardHomePage() {
  let reports: ReportSummary[] = [];
  try {
    reports = await listReports();
  } catch (err) {
    reports = [];
  }

  return <StudioApp initialReports={reports} initialCaseKey="SLK-3063-Z" />;
}
