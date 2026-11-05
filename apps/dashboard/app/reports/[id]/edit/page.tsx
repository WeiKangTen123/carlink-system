import { notFound, redirect } from "next/navigation";
import { getReport, isUntakenPublic, messageForCode } from "@/lib/api";
import { ReportForm } from "../../ReportForm";
import { updateReportAction } from "../../actions";
import { ReopenButton } from "./ReopenButton";

export default async function EditReportPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ error?: string }>;
}) {
  const { id } = await params;
  const { error } = await searchParams;
  const report = await getReport(id);
  if (!report) notFound();
  // Not taken from the Public Inbox yet (release P6): read-only there.
  if (isUntakenPublic(report)) redirect(`/inbox/${id}`);
  if (report.status === "intake") redirect(`/reports/new?draft=${id}`);

  if (report.status === "Signed Off") {
    return (
      <div className="new-report-form">
        <h1>Case signed off</h1>
        <p className="form-hint">
          This case has been signed off and is locked against further edits. Reopen it to make
          changes -- it then needs signing off again.
        </p>
        <ReopenButton id={id} allowed={report.permissions.reopen} />
      </div>
    );
  }

  if (!report.permissions.edit) {
    return (
      <div className="new-report-form">
        <h1>View only</h1>
        <p className="form-hint">You can open this case, but you can&apos;t change it. Ask its owner or an admin.</p>
      </div>
    );
  }

  const boundUpdate = updateReportAction.bind(null, id);
  // Set by updateReportAction when the API refused the save with a code.
  const saveError = messageForCode(error);

  return (
    <>
      {saveError && (
        <div className="setup-error" role="alert" style={{ marginBottom: 12 }}>
          Not saved: {saveError}
        </div>
      )}
      <ReportForm
        initialData={report.data}
        existingPhotoUrls={report.photo_urls}
        existingPhotoThumbUrls={report.photo_thumb_urls}
        reportId={id}
        action={boundUpdate}
        submitLabel="Save changes"
      />
    </>
  );
}
