"use client";

import { useState } from "react";
import { AlertTriangle, Check, Trash2, X } from "lucide-react";
import type { AccessRequestRow, LinkLifetime, UserRole } from "@/lib/api";
import { ROLE_LABEL, formatSgt, formatSgtDay } from "@/lib/signup";
import { approveAccessRequestAction, deleteAccessRequestAction, rejectAccessRequestAction } from "./actions";
import { LifetimeChoice, RoleChoice, type ReadyLink } from "./LinkDialogs";

/** "just now", "5 min ago", "3 h ago", "2 d ago", then the date. */
export function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (isNaN(ms)) return "";
  const min = Math.floor(ms / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const hours = Math.floor(min / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} d ago`;
  return formatSgtDay(iso);
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Warnings that help an admin spot a request that isn't what it seems:
 * the same email asked before (only one of them may be the real person),
 * or many requests from one network. */
function Flags({ r }: { r: AccessRequestRow }) {
  const flags: { text: string; strong?: boolean }[] = [];
  if (r.has_account) flags.push({ text: "Already has an account", strong: true });
  if (r.same_email_pending > 0) flags.push({ text: `Asked ${plural(r.same_email_pending, "more time", "more times")}` });
  if (r.same_ip_24h > 0) {
    flags.push({ text: `${plural(r.same_ip_24h, "other request", "other requests")} from the same network today` });
  }
  if (!flags.length) return null;
  return (
    <div className="request-flags">
      {flags.map((f) => (
        <span key={f.text} className={`request-flag ${f.strong ? "strong" : ""}`}>
          <AlertTriangle style={{ width: 11, height: 11 }} aria-label="Warning" /> {f.text}
        </span>
      ))}
    </div>
  );
}

function RequestDetails({ r }: { r: AccessRequestRow }) {
  return (
    <>
      <div className="request-meta">
        {r.email}
        {r.phone && <> · {r.phone}</>}
      </div>
      {r.organisation && <div className="request-meta">{r.organisation}</div>}
      {r.note && <blockquote className="request-note">“{r.note}”</blockquote>}
    </>
  );
}

function ApproveDialog({
  request,
  onCancel,
  onApproved,
}: {
  request: AccessRequestRow;
  onCancel: () => void;
  onApproved: (link: ReadyLink) => void;
}) {
  const [role, setRole] = useState<UserRole>(request.requested_role);
  const [hours, setHours] = useState<LinkLifetime>(72);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    const result = await approveAccessRequestAction(request.id, role, hours);
    setSaving(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    onApproved({
      purpose: "invite",
      token: result.token,
      name: result.link.name || request.name,
      email: result.link.email,
      expiresAt: result.link.expires_at,
      phone: request.phone || null,
    });
  };

  return (
    <div className="modal-backdrop" onClick={() => !saving && onCancel()}>
      <form
        className="modal-card"
        style={{ maxWidth: 520 }}
        onSubmit={submit}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="approve-title"
      >
        <h3 id="approve-title" style={{ fontSize: 16, fontWeight: 800, margin: "0 0 12px" }}>
          Approve request
        </h3>
        <div className="request-summary">
          <div style={{ fontWeight: 700 }}>{request.name}</div>
          <RequestDetails r={request} />
          <div className="request-meta">
            Asked for {ROLE_LABEL[request.requested_role] ?? request.requested_role} · {formatSgt(request.created_at)} ·{" "}
            {request.reference}
          </div>
          <Flags r={request} />
        </div>
        {request.has_account && (
          <p className="setup-note" style={{ color: "var(--badge-amber-text)" }}>
            This email already has an account. If they&apos;ve lost their password, use Reset password on the Active
            users tab instead.
          </p>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div>
            <span className="detail-field-label">Role</span>
            <RoleChoice name="approve-role" value={role} onChange={setRole} />
          </div>
          <div>
            <span className="detail-field-label">Link expires in</span>
            <LifetimeChoice name="approve-hours" value={hours} onChange={setHours} />
          </div>
        </div>
        <p className="setup-note" style={{ marginTop: 14 }}>
          Carlink doesn&apos;t message them: you get a link to send them yourself.
        </p>

        {error && <div className="setup-error">{error}</div>}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <button type="button" className="btn-secondary-modern" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="btn-primary-modern" disabled={saving}>
            {saving ? "Approving…" : "Approve and create link"}
          </button>
        </div>
      </form>
    </div>
  );
}

function RejectDialog({
  request,
  onCancel,
  onRejected,
}: {
  request: AccessRequestRow;
  onCancel: () => void;
  onRejected: () => void;
}) {
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    const result = await rejectAccessRequestAction(request.id, note.trim());
    setSaving(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    onRejected();
  };

  return (
    <div className="modal-backdrop" onClick={() => !saving && onCancel()}>
      <form
        className="modal-card"
        onSubmit={submit}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="reject-title"
      >
        <h3 id="reject-title" style={{ fontSize: 16, fontWeight: 800, margin: "0 0 4px" }}>
          Reject request from {request.name}?
        </h3>
        <p className="setup-note">They won&apos;t be told automatically.</p>
        <label style={{ display: "block" }}>
          <span className="detail-field-label">Reason (optional, only admins see it)</span>
          <textarea
            className="settings-input"
            rows={3}
            maxLength={500}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            style={{ resize: "vertical" }}
            autoFocus
          />
        </label>
        {error && (
          <div className="setup-error" style={{ marginTop: 12 }}>
            {error}
          </div>
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 14 }}>
          <button type="button" className="btn-secondary-modern" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="btn-primary-modern" disabled={saving}>
            {saving ? "Rejecting…" : "Reject request"}
          </button>
        </div>
      </form>
    </div>
  );
}

function decision(r: AccessRequestRow): string {
  const by = r.decided_by_name ? ` by ${r.decided_by_name}` : "";
  const when = r.decided_at ? `, ${timeAgo(r.decided_at)}` : "";
  const why = r.status === "rejected" && r.decision_note ? `: ${r.decision_note}` : "";
  return `${r.status === "approved" ? "Approved" : "Rejected"}${by}${when}${why}`;
}

export function AccessRequestsTab({
  requests,
  showDecided,
  onShowDecided,
  onRetry,
  onIssued,
  onChanged,
}: {
  /** Null when they couldn't be loaded. */
  requests: AccessRequestRow[] | null;
  showDecided: boolean;
  onShowDecided: (show: boolean) => void;
  onRetry: () => void;
  onIssued: (link: ReadyLink) => void;
  /** After a decision or a delete: reload the lists. */
  onChanged: () => void;
}) {
  const [approving, setApproving] = useState<AccessRequestRow | null>(null);
  const [rejecting, setRejecting] = useState<AccessRequestRow | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const remove = async (r: AccessRequestRow) => {
    if (!confirm(`Delete ${r.name}'s request? It's erased for good. Use this when someone asks for their details to be removed.`)) return;
    setBusyId(r.id);
    setError(null);
    const result = await deleteAccessRequestAction(r.id);
    setBusyId(null);
    if ("error" in result) setError(result.error);
    else onChanged();
  };

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 10 }}>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-muted)", cursor: "pointer" }}>
          <input type="checkbox" checked={showDecided} onChange={(e) => onShowDecided(e.target.checked)} />
          Show decided
        </label>
      </div>

      {error && <div className="setup-error">{error}</div>}

      {requests === null ? (
        <div className="setup-error" style={{ display: "flex", alignItems: "center", gap: 10, justifyContent: "space-between" }}>
          Couldn&apos;t load access requests.
          <button type="button" className="btn-secondary-modern users-action" onClick={onRetry}>
            Try again
          </button>
        </div>
      ) : requests.length === 0 ? (
        <p className="setup-note" style={{ margin: "8px 0" }}>
          {showDecided ? "No requests yet." : "No requests waiting."} New requests from the Request access page show up
          here.
        </p>
      ) : (
        <ul className="request-list">
          {requests.map((r) => {
            const pending = r.status === "pending";
            const busy = busyId === r.id;
            return (
              <li key={r.id} className="request-row" style={{ opacity: pending ? 1 : 0.7 }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <span style={{ fontWeight: 700 }}>{r.name}</span>
                    <span className={`role-chip ${r.requested_role}`} title="The role they asked for">
                      {ROLE_LABEL[r.requested_role] ?? r.requested_role}
                    </span>
                    <span className="request-meta" title={formatSgt(r.created_at)} suppressHydrationWarning>
                      {timeAgo(r.created_at)}
                    </span>
                  </div>
                  <RequestDetails r={r} />
                  {pending ? (
                    <Flags r={r} />
                  ) : (
                    <div className="request-meta" style={{ marginTop: 4 }} suppressHydrationWarning>
                      {decision(r)}
                    </div>
                  )}
                </div>
                <div className="request-actions">
                  {pending && (
                    <>
                      <button type="button" className="btn-primary-modern users-action" onClick={() => setApproving(r)} disabled={busy}>
                        <Check style={{ width: 11, height: 11 }} /> Approve…
                      </button>
                      <button type="button" className="btn-secondary-modern users-action" onClick={() => setRejecting(r)} disabled={busy}>
                        <X style={{ width: 11, height: 11 }} /> Reject
                      </button>
                    </>
                  )}
                  <button type="button" className="btn-secondary-modern users-action danger" onClick={() => remove(r)} disabled={busy}>
                    <Trash2 style={{ width: 11, height: 11 }} /> Delete
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {approving && (
        <ApproveDialog
          request={approving}
          onCancel={() => setApproving(null)}
          onApproved={(link) => {
            setApproving(null);
            onIssued(link);
            onChanged();
          }}
        />
      )}
      {rejecting && (
        <RejectDialog
          request={rejecting}
          onCancel={() => setRejecting(null)}
          onRejected={() => {
            setRejecting(null);
            onChanged();
          }}
        />
      )}
    </div>
  );
}
