import { notFound, redirect } from "next/navigation";
import { StudioApp } from "@/components/StudioApp";
import { getReport, isUntakenPublic } from "@/lib/api";

export default async function ReportDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const report = await getReport(id);
  if (!report) notFound();
  // A report from the public nobody has taken yet (release P6) lives in the
  // Public Inbox, read-only, until someone takes it.
  if (isUntakenPublic(report)) redirect(`/inbox/${id}`);
  // Unfinished intake (New Intake / Draft lane): resume it in the wizard.
  if (report.status === "intake") redirect(`/reports/new?draft=${id}`);

  return <StudioApp report={report} />;
}
