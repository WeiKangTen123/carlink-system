import { notFound } from "next/navigation";
import { getReport } from "@/lib/api";
import { ReportForm } from "../../ReportForm";
import { updateReportAction } from "../../actions";
import { ReopenButton } from "./ReopenButton";

export default async function EditReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const report = await getReport(id);
  if (!report) notFound();

  if (report.status === "Signed Off") {
    return (
      <div className="new-report-form">
        <h1>Report Locked</h1>
        <p className="form-hint">
          This report has been signed off and is locked against further edits. Reopen it to make
          changes -- this resets its status to Draft until it's signed off again.
        </p>
        <ReopenButton id={id} />
      </div>
    );
  }

  const boundUpdate = updateReportAction.bind(null, id);

  return (
    <ReportForm
      mode="edit"
      initialData={report.data}
      existingPhotoUrls={report.photo_urls}
      reportId={id}
      action={boundUpdate}
      submitLabel="Save Changes & Regenerate PDF"
    />
  );
}
