import { notFound, redirect } from "next/navigation";
import { StudioApp } from "@/components/StudioApp";
import { getReport, isUntakenPublic } from "@/lib/api";

export default async function ReportDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ tab?: string | string[] }>;
}) {
  const [{ id }, { tab }] = await Promise.all([params, searchParams]);
  const report = await getReport(id);
  if (!report) notFound();
  // A report from the public nobody has taken yet (release P6) lives in the
  // Public Inbox, read-only, until someone takes it.
  if (isUntakenPublic(report)) redirect(`/inbox/${id}`);
  // Unfinished intake (New Intake / Draft lane): resume it in the wizard.
  if (report.status === "intake") redirect(`/reports/new?draft=${id}`);

  // ?tab=annexA (the Overview's "Needs attention" links) opens that tab;
  // StudioApp ignores anything that isn't one of its tabs.
  return <StudioApp report={report} initialTab={typeof tab === "string" ? tab : undefined} />;
}
