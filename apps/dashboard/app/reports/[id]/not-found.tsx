import { CaseNotFound } from "@/components/CaseNotFound";

/** notFound() from /reports/[id] and /reports/[id]/edit: a missing case and
 * someone else's case look the same (the API gives both the same 404). */
export default function ReportNotFound() {
  return <CaseNotFound />;
}
