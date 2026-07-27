import { ReportForm } from "../ReportForm";
import { createReportAction } from "./actions";

export default function NewReportPage() {
  return <ReportForm mode="create" action={createReportAction} submitLabel="Save Report & Generate PDF" />;
}
