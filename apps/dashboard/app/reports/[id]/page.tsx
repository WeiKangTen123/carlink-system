import { notFound } from "next/navigation";
import { StudioApp } from "@/components/StudioApp";
import { getReport } from "@/lib/api";

export default async function ReportDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const report = await getReport(id);
  if (!report) notFound();

  return <StudioApp report={report} />;
}
