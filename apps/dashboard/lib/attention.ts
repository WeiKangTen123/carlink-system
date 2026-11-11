/** The Overview's "Needs attention" list: which drafts and open cases
 * someone should act on, and why, worked out from GET /reports alone.
 *
 * Pure (no React, no fetching), so the rules can be checked on their own.
 * Every reason reads a value the API recorded; none is a guess. A field an
 * older API doesn't send yet simply adds no reason. */
import { aiRunErrorLabel, isUntakenPublic, ownerLabel, type ReportSummary } from "./api";
import { caseTitle, daysOpen, inferVehicleName, ourRefLabel, REF_TO_BE_ISSUED, statusKind } from "./caseFields";
import { FILING_INTERRUPTED } from "./intake";

/** Why a case is listed, worst first: the order of this list is the order
 * the card sorts by. */
export const REASON_KINDS = [
  "draft_deleting",
  "ai_failed",
  "filing_interrupted",
  "reopened",
  "unpriced",
  "no_our_ref",
  "needs_you",
  "open_long",
] as const;
export type ReasonKind = (typeof REASON_KINDS)[number];

export type Reason = { kind: ReasonKind; text: string };

/** The case page's tabs (components/StudioApp.tsx CaseTab). */
export type CaseTabId = "detection" | "cover" | "annexA" | "annexB" | "annexC";

export type AttentionItem = {
  report: ReportSummary;
  /** Worst first; never empty. */
  reasons: Reason[];
  /** A draft opens in the wizard; a case on the tab of its worst reason. */
  href: string;
  action: "Resume" | "Open";
};

export type AttentionOptions = {
  /** Epoch ms to judge "today" and ages against; defaults to now. */
  now?: number;
  /** Who is looking. Someone else's draft is left out unless they are an
   * admin; left unset, every draft is listed. */
  viewer?: { id: string; admin: boolean };
};

/** One listed report as the card prints it, and nothing more: the page
 * hands these to the browser, not whole case summaries for rows the card
 * may fold away. */
export type AttentionRow = {
  id: string;
  title: string;
  /** Only beside a real plate: caseTitle already falls back to the vehicle. */
  vehicle: string | null;
  /** ownerLabel(owner); null when the viewer isn't shown owners. */
  owner: string | null;
  unassigned: boolean;
  reasons: Reason[];
  href: string;
  action: "Resume" | "Open";
};

/** A draft is listed once the idle clean-up will delete it within this many
 * calendar days (Singapore time): today, tomorrow or the day after. */
export const DELETE_WARN_DAYS = 2;
/** A filed case not signed off is listed once open longer than this. */
export const OPEN_LONG_DAYS = 14;

/** Where a case opens, by its worst reason. Reasons not here open the case
 * on its default tab. */
const REASON_TAB: Partial<Record<ReasonKind, CaseTabId>> = {
  reopened: "annexC",
  unpriced: "annexA",
  no_our_ref: "cover",
  // GET /reports says how many, not where: most are cover-page fields
  // (Our Ref, dates, requester), so the case opens there.
  needs_you: "cover",
};

const DAY_MS = 86_400_000;
/** Singapore and Malaysia are UTC+8 all year (no daylight saving). */
const SGT_OFFSET_MS = 8 * 3_600_000;

/** The Singapore calendar day of an epoch-ms moment, as a day number. */
function sgtDay(ms: number): number {
  return Math.floor((ms + SGT_OFFSET_MS) / DAY_MS);
}

const rank = (kind: ReasonKind) => REASON_KINDS.indexOf(kind);

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The reasons one report is listed for, worst first (empty: not listed). */
export function reasonsFor(r: ReportSummary, now: number = Date.now()): Reason[] {
  const reasons: Reason[] = [];

  if (r.status === "intake") {
    // An untaken public report belongs to the Public Inbox (its own line).
    if (isUntakenPublic(r)) return reasons;

    const deletesAt = r.draft_deletes_at ? Date.parse(r.draft_deletes_at) : NaN;
    if (!Number.isNaN(deletesAt)) {
      // Calendar days in Singapore time, so "tomorrow" means tomorrow. A
      // date already past waits for the next clean-up run: still "today".
      const inDays = Math.max(0, sgtDay(deletesAt) - sgtDay(now));
      if (inDays <= DELETE_WARN_DAYS) {
        const when = inDays === 0 ? "today" : inDays === 1 ? "tomorrow" : `in ${inDays} days`;
        reasons.push({ kind: "draft_deleting", text: `Deleted ${when} unless worked on` });
      }
    }

    if (r.intake_stage === "failed") {
      if (r.intake_error_code === FILING_INTERRUPTED) {
        reasons.push({ kind: "filing_interrupted", text: "Filing stopped before it finished" });
      } else {
        // The run log's few words for the code (lib/api.ts). A code it
        // doesn't word would show as itself, so it is left off instead.
        const label = aiRunErrorLabel(r.intake_error_code);
        const plain = label && label !== r.intake_error_code ? label : null;
        reasons.push({ kind: "ai_failed", text: plain ? `AI reading stopped: ${plain}` : "AI reading stopped" });
      }
    }
    return reasons.sort((a, b) => rank(a.kind) - rank(b.kind));
  }

  // Filed. Nothing is left to do on a signed-off case.
  if (statusKind(r.status) === "signed") return reasons;

  if (r.reopened === true) {
    reasons.push({ kind: "reopened", text: "Reopened, not signed off again" });
  }
  const unpriced = typeof r.unpriced_rows === "number" ? r.unpriced_rows : 0;
  if (unpriced > 0) {
    reasons.push({ kind: "unpriced", text: `${plural(unpriced, "Annex A row", "Annex A rows")} unpriced` });
  }
  if (ourRefLabel(r.report_id) === REF_TO_BE_ISSUED) {
    reasons.push({ kind: "no_our_ref", text: "No Our Ref yet" });
  }
  // Field review (contract 7): fields only a person can fill, still empty.
  const needsYou = typeof r.needs_you === "number" ? r.needs_you : 0;
  if (needsYou > 0) {
    reasons.push({ kind: "needs_you", text: `${plural(needsYou, "field needs", "fields need")} you` });
  }
  const days = daysOpen(r.created_at, now);
  if (days > OPEN_LONG_DAYS) {
    reasons.push({ kind: "open_long", text: `Open ${days} days` });
  }
  return reasons.sort((a, b) => rank(a.kind) - rank(b.kind));
}

/** Where a listed report opens: a draft in the wizard, a case on the tab of
 * its worst reason (or its default tab). */
export function attentionHref(r: ReportSummary, worst: ReasonKind): string {
  if (r.status === "intake") return `/reports/new?draft=${r.id}`;
  const tab = REASON_TAB[worst];
  return `/reports/${r.id}${tab ? `?tab=${tab}` : ""}`;
}

/** Every report that needs someone, worst reason first, then oldest first. */
export function attentionFor(reports: ReportSummary[], opts: AttentionOptions = {}): AttentionItem[] {
  const now = opts.now ?? Date.now();
  const items: AttentionItem[] = [];
  for (const report of reports) {
    // A draft is its owner's until it is filed: "see all" staff may look,
    // not change it (permissions.can_edit_case), so "Resume" would only
    // open it read-only. Admins may.
    const viewer = opts.viewer;
    if (viewer && !viewer.admin && report.status === "intake" && report.owner?.id !== viewer.id) continue;
    const reasons = reasonsFor(report, now);
    if (reasons.length === 0) continue;
    items.push({
      report,
      reasons,
      href: attentionHref(report, reasons[0].kind),
      action: report.status === "intake" ? "Resume" : "Open",
    });
  }
  const created = (r: ReportSummary) => Date.parse(r.created_at) || 0;
  return items.sort((a, b) => rank(a.reasons[0].kind) - rank(b.reasons[0].kind) || created(a.report) - created(b.report));
}

/** An item cut down to what the card prints (AttentionRow). */
export function attentionRow(item: AttentionItem, showOwner: boolean): AttentionRow {
  const r = item.report;
  return {
    id: r.id,
    title: caseTitle(r),
    vehicle: r.plate_number ? inferVehicleName(r) : null,
    owner: showOwner ? ownerLabel(r.owner) : null,
    unassigned: !r.owner,
    reasons: item.reasons,
    href: item.href,
    action: item.action,
  };
}
