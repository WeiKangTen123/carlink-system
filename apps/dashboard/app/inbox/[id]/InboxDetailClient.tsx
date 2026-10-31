"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  ExternalLink,
  Forward,
  Hand,
  ImageIcon,
  Pencil,
  ShieldCheck,
  Trash2,
  Undo2,
  UserPlus,
  X,
} from "lucide-react";
import {
  fileUrl,
  ownerLabel,
  publicRoleLabel,
  takenByLabel,
  type InboxDetail,
  type InboxPhoto,
  type InboxState,
} from "@/lib/api";
import { announceInboxChanged, expiryInfo, longSgt, timeAgo } from "@/lib/inbox";
import { AssignOwnerDialog } from "@/components/AssignOwnerDialog";
import { isAdmin, useCurrentUser } from "@/components/CurrentUser";
import { DiscardDialog, INBOX_MOVED_CODES } from "@/components/inbox/DiscardDialog";
import { PhoneActions } from "@/components/inbox/PhoneActions";
import { ReleaseDialog } from "@/components/inbox/ReleaseDialog";
import { takeSubmissionAction } from "../actions";

const STATE_LABELS: Record<InboxState, string> = { new: "New", taken: "Taken", filed: "Filed" };

/** The photos, as small previews; a click opens the 1600px one, with a link
 * to the original. Thumbnails only until then (release #11: no multi-MB
 * downloads just to show a page). */
function PhotoGrid({ photos }: { photos: InboxPhoto[] }) {
  const [open, setOpen] = useState<number | null>(null);
  const photo = open !== null ? photos[open] : null;

  useEffect(() => {
    if (open === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(null);
      if (e.key === "ArrowRight") setOpen((i) => (i === null ? i : Math.min(photos.length - 1, i + 1)));
      if (e.key === "ArrowLeft") setOpen((i) => (i === null ? i : Math.max(0, i - 1)));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, photos.length]);

  if (photos.length === 0) return <p className="setup-note">No photos.</p>;

  return (
    <>
      <div className="inbox-photo-grid">
        {photos.map((p, i) => (
          <button
            key={p.id}
            type="button"
            className="inbox-photo"
            onClick={() => setOpen(i)}
            aria-label={`Open photo ${i + 1} of ${photos.length}`}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={fileUrl(p.thumb_url || p.preview_url)} alt="" loading="lazy" decoding="async" />
          </button>
        ))}
      </div>

      {photo && open !== null && (
        <div className="modal-backdrop" onClick={() => setOpen(null)}>
          <div className="inbox-lightbox" role="dialog" aria-modal="true" aria-label={`Photo ${open + 1} of ${photos.length}`} onClick={(e) => e.stopPropagation()}>
            <div className="inbox-lightbox-bar">
              <span>
                Photo {open + 1} of {photos.length}
              </span>
              <span style={{ display: "inline-flex", gap: 8 }}>
                <button type="button" className="btn-secondary-modern inbox-phone-btn" disabled={open === 0} onClick={() => setOpen(open - 1)}>
                  Previous
                </button>
                <button
                  type="button"
                  className="btn-secondary-modern inbox-phone-btn"
                  disabled={open === photos.length - 1}
                  onClick={() => setOpen(open + 1)}
                >
                  Next
                </button>
                <a href={fileUrl(photo.url)} target="_blank" rel="noopener noreferrer" className="btn-secondary-modern inbox-phone-btn">
                  Open original <ExternalLink style={{ width: 11, height: 11 }} />
                </a>
                <button type="button" className="intake-icon-btn" onClick={() => setOpen(null)} aria-label="Close">
                  <X style={{ width: 16, height: 16 }} />
                </button>
              </span>
            </div>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={fileUrl(photo.preview_url || photo.url)} alt={`Photo ${open + 1}`} className="inbox-lightbox-img" />
          </div>
        </div>
      )}
    </>
  );
}

export function InboxDetailClient({ item, renderedAt }: { item: InboxDetail; renderedAt: number }) {
  const router = useRouter();
  const user = useCurrentUser();
  const [now, setNow] = useState(renderedAt);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<null | "assign" | "release" | "discard">(null);

  useEffect(() => {
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, []);

  const perms = item.permissions ?? { take: false, assign: false, release: false, discard: false };
  const sender = item.sender;
  const left = item.state === "new" ? expiryInfo(item.expires_at, now) : null;
  const takenBy = takenByLabel(item.taken_by) || (item.owner ? ownerLabel(item.owner) : null);
  // The taker or an admin works on it in the wizard (the API's can_edit_case).
  const canOpenDraft = item.state === "taken" && Boolean(item.owner) && (item.owner?.id === user?.id || isAdmin(user));

  /** After a change someone else made (taken, withdrawn, filed): show where
   * the report is now. */
  function moved() {
    announceInboxChanged();
    router.refresh();
  }

  async function take() {
    setBusy(true);
    setError(null);
    const res = await takeSubmissionAction(item.id);
    if ("error" in res) {
      setBusy(false);
      setError(res.error);
      if (res.code && INBOX_MOVED_CODES.has(res.code)) moved();
      return;
    }
    announceInboxChanged();
    // Straight into the wizard, as the taker's draft (step 1).
    router.push(`/reports/new?draft=${item.id}`);
  }

  return (
    <div className="inbox-page">
      <div className="inbox-detail-head">
        <Link href={item.state === "taken" ? "/inbox?view=taken" : "/inbox"} prefetch={false} className="inbox-back">
          <ArrowLeft style={{ width: 13, height: 13 }} /> Public Inbox
        </Link>
        <h1 className="inbox-title inbox-mono">{item.reference}</h1>
        <span className={`intake-chip ${item.state === "new" ? "warn" : item.state === "filed" ? "ok" : ""}`}>
          {STATE_LABELS[item.state] ?? item.state}
        </span>
        <span className="inbox-sub inline" suppressHydrationWarning>
          Sent {longSgt(item.submitted_at)} ({timeAgo(item.submitted_at, now)})
          {left && (
            <>
              {" · "}
              <span className={`inbox-left ${left.tone}`}>{left.long}</span>
            </>
          )}
          {item.state === "taken" && takenBy && (
            <>
              {" · "}Taken by <strong>{takenBy}</strong>
              {item.taken_at ? ` ${timeAgo(item.taken_at, now)}` : ""}
            </>
          )}
        </span>
        {item.withdraw_requested_at && <span className="intake-chip danger">Withdrawal requested</span>}
      </div>

      {item.withdraw_requested_at && (
        <div className="inbox-banner warn" role="status">
          <AlertTriangle style={{ width: 14, height: 14, flexShrink: 0 }} />
          <span>
            The sender sent /withdraw on {longSgt(item.withdraw_requested_at)}, after this report was taken, so it
            wasn&apos;t deleted. Call them, and discard it if they still want it withdrawn.
          </span>
        </div>
      )}

      {error && (
        <div className="intake-alert danger" role="alert">
          <AlertTriangle style={{ width: 16, height: 16, flexShrink: 0 }} />
          <div style={{ flex: 1 }}>{error}</div>
          <button type="button" className="intake-icon-btn" onClick={() => setError(null)} aria-label="Dismiss">
            <X style={{ width: 14, height: 14 }} />
          </button>
        </div>
      )}

      <div className="inbox-detail-grid">
        <section className="card inbox-detail-panel">
          <div className="inbox-section-title">
            <ImageIcon style={{ width: 14, height: 14 }} /> Photos ({item.photos?.length ?? 0})
          </div>
          {(item.forwarded_count ?? 0) > 0 && (
            <p className="inbox-sub" style={{ margin: "0 0 8px", display: "flex", alignItems: "center", gap: 5 }}>
              <Forward style={{ width: 12, height: 12, flexShrink: 0 }} />
              {item.forwarded_count} {item.forwarded_count === 1 ? "photo was" : "photos were"} forwarded from another chat, so
              the sender may not have taken {item.forwarded_count === 1 ? "it" : "them"}.
            </p>
          )}
          <PhotoGrid photos={item.photos ?? []} />
        </section>

        <section className="card inbox-detail-panel inbox-sender-panel">
          <div className="inbox-section-title">Sender (as given)</div>
          <div className="inbox-sender-line">
            <strong className="inbox-as-given" style={{ fontSize: 15 }}>
              {sender?.name || "No name given"}
            </strong>
            {sender?.role && <span className="intake-chip">{publicRoleLabel(sender)}</span>}
          </div>

          {(sender?.phone_display || sender?.phone) && (
            <div className="inbox-phone-block">
              <span className="inbox-mono" style={{ fontSize: 14, fontWeight: 700 }}>
                {sender.phone_display || sender.phone}
              </span>
              <span className="inbox-verified">
                <ShieldCheck style={{ width: 13, height: 13 }} /> Shared from their own Telegram account
              </span>
              <PhoneActions phone={sender.phone || sender.phone_display} kind={sender.phone_kind} />
            </div>
          )}

          {(sender?.telegram_username || sender?.telegram_name) && (
            <div className="inbox-field">
              <span className="inbox-label">Telegram</span>
              <span className="inbox-as-given">
                {sender.telegram_username ? `@${sender.telegram_username}` : ""}
                {sender.telegram_username && sender.telegram_name ? " · " : ""}
                {sender.telegram_name || ""}
              </span>
            </div>
          )}

          <div className="inbox-field">
            <span className="inbox-label">Plate</span>
            <span className="inbox-mono inbox-as-given">{item.plate || "Not sure"}</span>
          </div>

          {item.when_where && (
            <div className="inbox-field">
              <span className="inbox-label">When and where</span>
              <span className="inbox-as-given">{item.when_where}</span>
            </div>
          )}

          <div className="inbox-field">
            <span className="inbox-label">What happened (their words)</span>
            <p className="inbox-as-given inbox-statement">{item.statement || "Nothing written."}</p>
          </div>

          {item.same_sender?.length > 0 && (
            <div className="inbox-field">
              <span className="inbox-label">Also from this sender</span>
              <span className="inbox-same-sender">
                {item.same_sender.map((o) => (
                  <Link key={o.id} href={o.state === "filed" ? `/reports/${o.id}` : `/inbox/${o.id}`} prefetch={false} className="inbox-ref">
                    {o.reference} <span className="inbox-sub inline">({(STATE_LABELS[o.state] ?? o.state).toLowerCase()})</span>
                  </Link>
                ))}
              </span>
            </div>
          )}

          <p className="inbox-sub" style={{ marginTop: 12 }}>
            A reference isn&apos;t proof of who someone is. When someone calls about this report, check they&apos;re calling from
            this number.
          </p>
        </section>
      </div>

      <div className="card inbox-actions">
        {perms.take && (
          <button type="button" className="btn-primary-modern" onClick={() => void take()} disabled={busy}>
            <Hand style={{ width: 14, height: 14 }} /> {busy ? "Taking…" : "Take it"}
          </button>
        )}
        {perms.assign && (
          <button type="button" className="btn-secondary-modern" onClick={() => setDialog("assign")} disabled={busy}>
            <UserPlus style={{ width: 14, height: 14 }} /> Assign to…
          </button>
        )}
        {canOpenDraft && (
          <Link href={`/reports/new?draft=${item.id}`} prefetch={false} className="btn-primary-modern">
            <Pencil style={{ width: 14, height: 14 }} /> Open the draft
          </Link>
        )}
        {perms.release && (
          <button type="button" className="btn-secondary-modern" onClick={() => setDialog("release")} disabled={busy}>
            <Undo2 style={{ width: 14, height: 14 }} /> Return to inbox
          </button>
        )}
        {perms.discard && (
          <button type="button" className="btn-secondary-modern inbox-danger-outline" onClick={() => setDialog("discard")} disabled={busy}>
            <Trash2 style={{ width: 14, height: 14 }} /> Discard…
          </button>
        )}
        {!perms.take && !perms.assign && !perms.release && !perms.discard && !canOpenDraft && (
          <span className="setup-note" style={{ margin: 0 }}>
            {item.state === "taken" && takenBy
              ? `Taken by ${takenBy}. Only they or an admin can work on it.`
              : "Nothing you can do with this report."}
          </span>
        )}
        {item.state === "new" && perms.take && (
          <span className="inbox-sub inline" style={{ marginLeft: "auto" }}>
            Taking it makes it your draft and opens it in New Case Intake.
          </span>
        )}
      </div>

      {dialog === "assign" && (
        <AssignOwnerDialog
          mode="inbox"
          reportId={item.id}
          reference={item.reference}
          owner={null}
          onClose={() => setDialog(null)}
          onAssigned={() => {
            setDialog(null);
            moved();
          }}
        />
      )}
      {dialog === "release" && (
        <ReleaseDialog
          reportId={item.id}
          reference={item.reference}
          expiresAt={item.expires_at}
          onClose={() => setDialog(null)}
          onMoved={moved}
          onReleased={() => {
            setDialog(null);
            moved();
          }}
        />
      )}
      {dialog === "discard" && (
        <DiscardDialog
          reportId={item.id}
          reference={item.reference}
          onClose={() => setDialog(null)}
          onMoved={moved}
          onDiscarded={() => {
            announceInboxChanged();
            router.push(`/inbox?discarded=${encodeURIComponent(item.reference)}`);
            router.refresh();
          }}
        />
      )}
    </div>
  );
}
