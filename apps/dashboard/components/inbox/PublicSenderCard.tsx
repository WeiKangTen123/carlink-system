"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Send, ShieldCheck, Undo2 } from "lucide-react";
import { publicRoleLabel, type PublicSubmissionInfo } from "@/lib/api";
import { announceInboxChanged, longSgt } from "@/lib/inbox";
import { PhoneActions } from "./PhoneActions";
import { ReleaseDialog } from "./ReleaseDialog";

// Longer statements start folded, so the card doesn't push the step down.
const STATEMENT_FOLD = 320;

/** "SENT BY THE PUBLIC" (release P6): who sent a public report and what they
 * said, above wizard steps 1 and 4 and on the case page. Everything here is
 * the sender's own words, shown as plain text: nothing is checked, and the
 * banner says to check every field the AI fills from it. [Return to inbox]
 * puts a taken report back in the Public Inbox (its owner or an admin). */
export function PublicSenderCard({
  reportId,
  info,
  canRelease = false,
  releaseBlocked = null,
  beforeRelease,
  showAiCheck = true,
}: {
  reportId: string;
  info: PublicSubmissionInfo;
  canRelease?: boolean;
  /** Why Return to inbox can't be pressed right now (uploads running...). */
  releaseBlocked?: string | null;
  beforeRelease?: () => Promise<boolean>;
  showAiCheck?: boolean;
}) {
  const router = useRouter();
  const [releasing, setReleasing] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const sender = info.sender;
  const phone = sender?.phone_display || sender?.phone || "";
  const role = publicRoleLabel(sender);
  const statement = (info.statement || "").trim();
  const folded = statement.length > STATEMENT_FOLD && !showAll;

  return (
    <section className="card public-sender-card" aria-label="Sent by the public">
      <div className="public-sender-head">
        <span className="public-sender-kicker">
          <Send style={{ width: 12, height: 12 }} /> Sent by the public
        </span>
        <span className="public-sender-ref">{info.reference}</span>
        {info.submitted_at && <span className="public-sender-sent">Sent {longSgt(info.submitted_at)}</span>}
        {canRelease && (
          <button
            type="button"
            className="btn-secondary-modern public-sender-release"
            onClick={() => setReleasing(true)}
            disabled={Boolean(releaseBlocked)}
            title={releaseBlocked || "Put it back in the Public Inbox for someone else to take"}
          >
            <Undo2 style={{ width: 13, height: 13 }} /> Return to inbox
          </button>
        )}
      </div>

      <div className="public-sender-who">
        <span>
          <strong className="inbox-as-given">{sender?.name || "No name given"}</strong>{" "}
          <span className="inbox-muted">(as given)</span>
          {role && <> · {role}</>}
        </span>
        {phone && (
          <span className="public-sender-phone">
            <span className="inbox-mono">{phone}</span>
            <ShieldCheck
              style={{ width: 13, height: 13, color: "var(--badge-green-text)", flexShrink: 0 }}
              aria-label="Shared from their own Telegram account"
            />
            <PhoneActions phone={sender.phone || phone} kind={sender.phone_kind} compact />
          </span>
        )}
      </div>

      {(info.plate !== undefined || info.when_where) && (
        <div className="public-sender-facts">
          <span>
            <span className="inbox-label">Plate</span> <span className="inbox-mono inbox-as-given">{info.plate || "Not sure"}</span>
          </span>
          {info.when_where && (
            <span>
              <span className="inbox-label">When and where</span> <span className="inbox-as-given">{info.when_where}</span>
            </span>
          )}
        </div>
      )}

      {statement && (
        <div className="public-sender-statement">
          <span className="inbox-label">What happened (their words)</span>
          <p className="inbox-as-given inbox-statement">{folded ? `${statement.slice(0, STATEMENT_FOLD).trimEnd()}…` : statement}</p>
          {statement.length > STATEMENT_FOLD && (
            <button type="button" className="intake-link" style={{ paddingLeft: 0 }} onClick={() => setShowAll((v) => !v)}>
              {showAll ? "Show less" : "Show all"}
            </button>
          )}
        </div>
      )}

      {info.withdraw_requested_at && (
        <div className="inbox-banner warn" role="status">
          <AlertTriangle style={{ width: 14, height: 14, flexShrink: 0 }} />
          <span>
            Withdrawal requested: the sender sent /withdraw on {longSgt(info.withdraw_requested_at)}, after this was taken.
            Call them before going on, and discard it if they still want it withdrawn.
          </span>
        </div>
      )}

      {showAiCheck && (
        <div className="inbox-banner info">
          <AlertTriangle style={{ width: 14, height: 14, flexShrink: 0 }} />
          <span>
            <strong>Public report: check every field the AI fills.</strong> The sender&apos;s words and photos haven&apos;t been
            checked by anyone, and the AI takes them as given.
          </span>
        </div>
      )}

      {releasing && (
        <ReleaseDialog
          reportId={reportId}
          reference={info.reference}
          expiresAt={info.expires_at}
          beforeRelease={beforeRelease}
          onClose={() => setReleasing(false)}
          onMoved={() => router.refresh()}
          onReleased={() => {
            announceInboxChanged();
            // No router.refresh() after this: it cancels the push. The
            // action's revalidatePath already makes the page fresh.
            router.push(`/inbox/${reportId}`);
          }}
        />
      )}
    </section>
  );
}
