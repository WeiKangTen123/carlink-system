import { StudioApp } from "@/components/StudioApp";
import { getReport, listReports } from "@/lib/api";

export default async function ReportDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let caseKey = "SLK-3063-Z";
  const upperId = id.toUpperCase();
  if (upperId.includes("VAY") || upperId.includes("CIVIC") || upperId.includes("E973")) {
    caseKey = "VAY-4821";
  } else if (upperId.includes("WX") || upperId.includes("HILUX") || upperId.includes("F7A3")) {
    caseKey = "WX-8888-A";
  } else {
    caseKey = "SLK-3063-Z";
  }

  let reports = [];
  try {
    reports = await listReports();
  } catch (err) {
    reports = [];
  }

  return <StudioApp initialReports={reports} initialCaseKey={caseKey} />;
}
