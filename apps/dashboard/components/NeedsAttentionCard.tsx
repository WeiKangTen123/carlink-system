"use client";

import { useState, type CSSProperties, type ReactNode } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowRight, Inbox, UserX } from "lucide-react";
import type { AttentionRow as Row, ReasonKind } from "@/lib/attention";

/** Rows shown before "Show all". */
const FIRST_SHOWN = 8;

const RED: CSSProperties = { background: "var(--badge-red-bg)", color: "var(--badge-red-text)", borderColor: "var(--badge-red-border)" };
const AMBER: CSSProperties = { background: "var(--badge-amber-bg)", color: "var(--badge-amber-text)", borderColor: "var(--badge-amber-border)" };

/** Red: work is lost or stuck until someone acts. Amber: the case can't be
 * signed off as it stands. Plain: it has just been open a long time. */
const REASON_TONE: Record<ReasonKind, CSSProperties> = {
  draft_deleting: RED,
  ai_failed: RED,
  filing_interrupted: RED,
  reopened: AMBER,
  unpriced: AMBER,
  no_our_ref: AMBER,
  open_long: {},
};

/** The Overview's "Needs attention" card. The list and its wording are
 * worked out on the server (lib/attention.ts); this only folds it to the
 * first rows. A row's owner is null when the viewer isn't shown owners.
 * `unassigned` (admins) and `expiringSoon` (people who see all cases) are
 * null when not theirs to see; a zero shows no line. */
export function NeedsAttentionCard({
  items,
  unassigned,
  expiringSoon,
}: {
  items: Row[];
  unassigned: number | null;
  expiringSoon: number | null;
}) {
  const [showAll, setShowAll] = useState(false);
  const noOwner = unassigned ?? 0;
  const expiring = expiringSoon ?? 0;
  if (items.length === 0 && noOwner === 0 && expiring === 0) return null;
  const shown = showAll ? items : items.slice(0, FIRST_SHOWN);

  return (
    <div className="card-glass" style={{ marginBottom: 24 }}>
      <div className="card-header">
        <div>
          <div className="card-title">
            <AlertTriangle style={{ width: 16, height: 16, color: "var(--badge-amber-text)" }} /> Needs attention
          </div>
          <div className="card-subtitle">Drafts and open cases waiting on someone, most urgent first</div>
        </div>
        {items.length > 0 && (
          <span className="status-pill" style={{ fontSize: 11, ...AMBER }}>
            {items.length}
          </span>
        )}
      </div>

      {items.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {shown.map((item) => (
            <AttentionRow key={item.id} item={item} />
          ))}
        </div>
      )}
      {items.length > FIRST_SHOWN && (
        <button type="button" className="link-button" style={{ fontSize: 12, marginTop: 10 }} onClick={() => setShowAll((v) => !v)}>
          {showAll ? `Show the first ${FIRST_SHOWN}` : `Show all ${items.length}`}
        </button>
      )}

      {(noOwner > 0 || expiring > 0) && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 6,
            marginTop: items.length > 0 ? 14 : 0,
            paddingTop: items.length > 0 ? 12 : 0,
            borderTop: items.length > 0 ? "1px solid var(--border-color)" : undefined,
          }}
        >
          {noOwner > 0 && (
            <FooterLine icon={<UserX style={{ width: 13, height: 13 }} />} href="/reports?scope=unassigned" action="Assign">
              {noOwner === 1 ? "1 case has no owner" : `${noOwner} cases have no owner`}
            </FooterLine>
          )}
          {expiring > 0 && (
            <FooterLine icon={<Inbox style={{ width: 13, height: 13 }} />} href="/inbox" action="Public Inbox">
              {expiring === 1 ? "1 public report will be deleted within 3 days" : `${expiring} public reports will be deleted within 3 days`}
            </FooterLine>
          )}
        </div>
      )}
    </div>
  );
}

/** Laid out like the Overview's OldestCaseRow: plate, vehicle, owner, then
 * why it is listed and where it opens. */
function AttentionRow({ item }: { item: Row }) {
  return (
    <Link href={item.href} prefetch={false} className="analytics-case-link" style={{ flexWrap: "wrap" }}>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 700, whiteSpace: "nowrap" }}>{item.title}</span>
      {item.vehicle && (
        <span style={{ fontSize: 12, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {item.vehicle}
        </span>
      )}
      {item.owner !== null && (
        <span style={{ fontSize: 11, color: item.unassigned ? "var(--badge-amber-text)" : "var(--text-muted)", whiteSpace: "nowrap" }}>
          {item.owner}
        </span>
      )}
      <span style={{ marginLeft: "auto", display: "inline-flex", flexWrap: "wrap", justifyContent: "flex-end", gap: 4 }}>
        {item.reasons.map((reason) => (
          <span key={reason.kind} className="status-pill" style={{ fontSize: 10, whiteSpace: "nowrap", ...REASON_TONE[reason.kind] }}>
            {reason.text}
          </span>
        ))}
      </span>
      <span style={{ fontSize: 11, fontWeight: 700, color: "var(--accent-cyan)", display: "inline-flex", alignItems: "center", gap: 3, whiteSpace: "nowrap" }}>
        {item.action} <ArrowRight style={{ width: 11, height: 11 }} />
      </span>
    </Link>
  );
}

function FooterLine({ icon, href, action, children }: { icon: ReactNode; href: string; action: string; children: ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 12, color: "var(--text-secondary)" }}>
      <span style={{ display: "inline-flex", color: "var(--badge-amber-text)" }}>{icon}</span>
      <span>{children}</span>
      <Link href={href} prefetch={false} className="setup-help-link" style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
        {action} <ArrowRight style={{ width: 11, height: 11 }} />
      </Link>
    </div>
  );
}
