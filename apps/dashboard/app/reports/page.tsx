import { unstable_rethrow } from "next/navigation";
import {
  listReports,
  getAnalyticsSummary,
  errorCode,
  isCaseScope,
  messageForCode,
  type CaseScope,
  type ReportSummary,
} from "@/lib/api";
import { ReportsClient } from "./ReportsClient";

type LoadedList = { reports: ReportSummary[]; scope: CaseScope | null; notice: string | null };

/** The cases for ?scope= (null: the API's default -- All for admins, My
 * cases for everyone else). Someone who may only see their own cases and
 * asks for more gets their own, with a note saying so. */
async function loadReports(scope: CaseScope | null): Promise<LoadedList> {
  try {
    return { reports: await listReports(scope ?? undefined), scope, notice: null };
  } catch (err) {
    unstable_rethrow(err);
    if (scope && scope !== "mine" && errorCode(err) === "scope_not_allowed") {
      const own = await loadReports("mine");
      return { ...own, notice: messageForCode("scope_not_allowed") };
    }
    console.warn("Failed to load reports:", err);
    return { reports: [], scope, notice: null };
  }
}

export default async function ReportsPage({ searchParams }: { searchParams: Promise<{ scope?: string }> }) {
  const { scope } = await searchParams;
  const [list, analytics] = await Promise.all([
    loadReports(isCaseScope(scope) ? scope : null),
    getAnalyticsSummary().catch((err) => {
      unstable_rethrow(err);
      return null;
    }),
  ]);
  return <ReportsClient initialReports={list.reports} initialAnalytics={analytics} scope={list.scope} notice={list.notice} />;
}
