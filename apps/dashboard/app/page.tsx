import { unstable_rethrow } from "next/navigation";
import Link from "next/link";
import { ArrowRight, BarChart3, ClipboardList, Clock, FilePlus2, FolderOpen, Inbox, UserX, type LucideIcon } from "lucide-react";
import { listReports, firmWideScope, countInbox, getCurrentUser, ownerLabel, type InboxCount, type ReportSummary } from "@/lib/api";
import { caseTitle, daysOpen, getOperationalStage, inferVehicleName, isAwaitingSignOff, statusKind, statusLabel } from "@/lib/caseFields";
import { attentionFor, attentionRow } from "@/lib/attention";
import { NeedsAttentionCard } from "@/components/NeedsAttentionCard";

/** How many of the oldest open cases the overview lists. */
const OLDEST_SHOWN = 6;

/** The overview: four real counts, the open cases that have waited
 * longest, and the three places to go next. Everything on it is counted
 * from the same list the Cases page shows, so the two can never disagree.
 * (It replaced a "Command Center" of invented reserves, a 48h SLA and
 * hard-coded workshop clusters.) */
export default async function OverviewPage() {
  // The whole firm for anyone who sees every case; plain users their own.
  const scope = await firmWideScope();
  const me = await getCurrentUser();
  const admin = me?.role === "admin";
  // The Public Inbox tile (release P6): for people who see all cases.
  const seesAll = Boolean(me && (admin || me.can_view_all_cases));
  // Each request fails on its own so one outage doesn't blank the page.
  const [reports, inbox] = await Promise.all([
    listReports(scope).catch((err): ReportSummary[] => {
      unstable_rethrow(err);
      console.warn("Failed to load reports for overview:", err);
      return [];
    }),
    seesAll
      ? countInbox().catch((err): InboxCount | null => {
          unstable_rethrow(err);
          console.warn("Failed to count the Public Inbox for overview:", err);
          return null;
        })
      : Promise.resolve(null),
  ]);

  if (reports.length === 0) {
    return (
      <div
        className="card-glass"
        style={{
          maxWidth: 560,
          margin: "80px auto",
          textAlign: "center",
          padding: "48px 36px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "center", marginBottom: 16 }}>
          <ClipboardList style={{ width: 48, height: 48, color: "var(--accent-primary)" }} />
        </div>
        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            color: "var(--accent-cyan)",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            marginBottom: 6,
          }}
        >
          Overview
        </div>
        <h1 style={{ fontSize: 22, fontWeight: 800, marginBottom: 10, letterSpacing: "-0.02em" }}>No cases yet</h1>
        <p style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 24, lineHeight: 1.6 }}>
          Cases filed through the Telegram or WhatsApp bots, or started here, appear on this page.
        </p>
        <Link href="/reports/new" className="btn-primary-modern" style={{ padding: "10px 22px" }}>
          <FilePlus2 style={{ width: 15, height: 15 }} /> New intake
        </Link>
        {inbox && inbox.new > 0 && (
          <p style={{ fontSize: 13, color: "var(--text-muted)", margin: "18px 0 0" }}>
            <Link href="/inbox" prefetch={false} className="setup-help-link" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <Inbox style={{ width: 14, height: 14 }} /> {inbox.new} report{inbox.new === 1 ? "" : "s"} from the public
              waiting in the Public Inbox
            </Link>
          </p>
        )}
      </div>
    );
  }

  // Open: not signed off, unfinished intakes included. Awaiting sign-off:
  // the status bucket the API's own analytics count uses (see caseFields).
  const open = reports.filter((r) => getOperationalStage(r) !== "signed");
  const awaiting = reports.filter((r) => isAwaitingSignOff(r.status)).length;
  const unassigned = reports.filter((r) => !r.owner).length;
  const oldest = [...open]
    .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
    .slice(0, OLDEST_SHOWN);
  // Drafts and cases someone should act on, worst first (lib/attention.ts),
  // over the same list the counts come from.
  const attention = attentionFor(reports, { viewer: me ? { id: me.id, admin } : undefined }).map((item) =>
    attentionRow(item, seesAll),
  );
  // The counts above are firm-wide for anyone who sees all cases, so their
  // links must open the same list: plain /reports defaults a non-admin to
  // "My cases", which would show fewer rows than the tile just counted.
  const casesHref = seesAll ? "/reports?scope=all" : "/reports";

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>Overview</h1>
          <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--text-muted)" }}>{seesAll ? "Every case on file" : "Your cases"}</p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Link href="/reports/new" className="btn-primary-modern" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <FilePlus2 style={{ width: 14, height: 14 }} /> New intake
          </Link>
          <Link href={casesHref} prefetch={false} className="btn-secondary-modern" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <FolderOpen style={{ width: 14, height: 14 }} /> All cases <ArrowRight style={{ width: 12, height: 12 }} />
          </Link>
          <Link href="/analytics" prefetch={false} className="btn-secondary-modern" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <BarChart3 style={{ width: 14, height: 14 }} /> Analytics <ArrowRight style={{ width: 12, height: 12 }} />
          </Link>
        </div>
      </div>

      <div className="kpi-grid-modern">
        <Tile icon={FolderOpen} label="Open cases" value={open.length} href={casesHref} hint="Not yet signed off, intake drafts included" />
        <Tile
          icon={Clock}
          label="Awaiting sign-off"
          value={awaiting}
          href={casesHref}
          color="var(--badge-amber-text)"
          hint="Filed cases a surveyor has not signed off"
        />
        {admin && (
          <Tile
            icon={UserX}
            label="Unassigned"
            value={unassigned}
            href="/reports?scope=unassigned"
            color={unassigned > 0 ? "var(--badge-amber-text)" : undefined}
            hint="Cases nobody owns yet"
          />
        )}
        {seesAll && (
          <Tile
            icon={Inbox}
            label="Public inbox"
            value={inbox ? inbox.new : "—"}
            href="/inbox"
            color={inbox && inbox.new > 0 ? "var(--accent-cyan)" : undefined}
            hint={
              inbox === null
                ? "Count unavailable right now"
                : inbox.expiring_soon > 0
                  ? `${inbox.expiring_soon} close to being deleted`
                  : "Reports from the public nobody has taken"
            }
          />
        )}
      </div>

      <NeedsAttentionCard
        items={attention}
        unassigned={admin ? unassigned : null}
        expiringSoon={seesAll && inbox ? inbox.expiring_soon : null}
      />

      <div className="card-glass">
        <div className="card-header">
          <div>
            <div className="card-title">
              <Clock style={{ width: 16, height: 16, color: "var(--badge-amber-text)" }} /> Oldest open cases
            </div>
            <div className="card-subtitle">Longest since filing and not yet signed off</div>
          </div>
        </div>
        {oldest.length === 0 ? (
          <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>Nothing open. Every case is signed off.</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {oldest.map((r) => (
              <OldestCaseRow key={r.id} r={r} showOwner={seesAll} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/** One count, as a card that opens the list it was counted from. */
function Tile({
  icon: Icon,
  label,
  value,
  href,
  hint,
  color,
}: {
  icon: LucideIcon;
  label: string;
  value: number | string;
  href: string;
  hint: string;
  color?: string;
}) {
  return (
    <Link href={href} prefetch={false} className="kpi-card-glow" style={{ display: "block", textDecoration: "none", color: "inherit" }}>
      <div className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <Icon style={{ width: 13, height: 13 }} /> {label}
      </div>
      <div className="kpi-val" style={color ? { color } : undefined}>
        {value}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>{hint}</div>
    </Link>
  );
}

/** Plate, vehicle, owner, age and status, as recorded. An unfinished
 * intake opens in the wizard (it has no case page yet). */
function OldestCaseRow({ r, showOwner }: { r: ReportSummary; showOwner: boolean }) {
  const draft = r.status === "intake";
  const days = daysOpen(r.created_at);
  const kind = statusKind(r.status);
  return (
    <Link href={draft ? `/reports/new?draft=${r.id}` : `/reports/${r.id}`} prefetch={false} className="analytics-case-link">
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 700, whiteSpace: "nowrap" }}>{caseTitle(r)}</span>
      {/* caseTitle already falls back to the vehicle when there is no
          plate, so the vehicle is only repeated beside a real plate. */}
      {r.plate_number && (
        <span style={{ fontSize: 12, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {inferVehicleName(r)}
        </span>
      )}
      {showOwner && (
        <span style={{ fontSize: 11, color: r.owner ? "var(--text-muted)" : "var(--badge-amber-text)", whiteSpace: "nowrap" }}>
          {ownerLabel(r.owner)}
        </span>
      )}
      <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)", whiteSpace: "nowrap" }}>
        filed {days === 0 ? "today" : `${days}d ago`}
      </span>
      <span
        className="status-pill"
        style={{
          fontSize: 10,
          whiteSpace: "nowrap",
          ...(kind === "awaiting"
            ? { background: "var(--badge-amber-bg)", color: "var(--badge-amber-text)", borderColor: "var(--badge-amber-border)" }
            : kind === "intake"
              ? { background: "var(--badge-blue-bg)", color: "var(--badge-blue-text)", borderColor: "var(--badge-blue-border)" }
              : {}),
        }}
      >
        {statusLabel(r.status)}
      </span>
      <span style={{ fontSize: 11, fontWeight: 700, color: "var(--accent-cyan)", display: "inline-flex", alignItems: "center", gap: 3, whiteSpace: "nowrap" }}>
        {draft ? "Resume" : "Open"} <ArrowRight style={{ width: 11, height: 11 }} />
      </span>
    </Link>
  );
}
