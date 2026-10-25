import { PageSkeleton } from "@/components/PageSkeleton";

/** Opening a case (and its Edit page) while the server loads the report. */
export default function Loading() {
  return <PageSkeleton variant="case" label="Loading case…" />;
}
