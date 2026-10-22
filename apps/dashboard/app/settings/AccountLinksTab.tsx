"use client";

import { useState } from "react";
import { Ban } from "lucide-react";
import type { AccountLinkRow } from "@/lib/api";
import { ROLE_LABEL, formatSgt, formatSgtDay } from "@/lib/signup";
import { revokeAccountLinkAction } from "./actions";

/** "2 d", "5 h", "30 min". */
function timeLeft(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  const days = Math.floor(ms / 86_400_000);
  if (days >= 1) return `${days} d`;
  const hours = Math.floor(ms / 3_600_000);
  if (hours >= 1) return `${hours} h`;
  return `${Math.max(1, Math.ceil(ms / 60_000))} min`;
}

function statusOf(l: AccountLinkRow): { label: string; tone: "ok" | "off" } {
  switch (l.status) {
    case "open":
      return { label: `Expires in ${timeLeft(l.expires_at)}`, tone: "ok" };
    case "used":
      return { label: l.used_at ? `Used ${formatSgtDay(l.used_at)}` : "Used", tone: "off" };
    case "expired":
      return { label: "Expired", tone: "off" };
    default:
      return { label: "Revoked", tone: "off" };
  }
}

export function AccountLinksTab({
  links,
  onRetry,
  onChanged,
}: {
  /** Null when they couldn't be loaded. */
  links: AccountLinkRow[] | null;
  onRetry: () => void;
  onChanged: (link: AccountLinkRow) => void;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const revoke = async (l: AccountLinkRow) => {
    const what = l.purpose === "reset" ? "password reset link" : "invite link";
    if (!confirm(`Revoke the ${what} for ${l.email}? It stops working at once.`)) return;
    setBusyId(l.id);
    setError(null);
    const result = await revokeAccountLinkAction(l.id);
    setBusyId(null);
    if ("error" in result) setError(result.error);
    else onChanged(result.link);
  };

  if (links === null) {
    return (
      <div className="setup-error" style={{ display: "flex", alignItems: "center", gap: 10, justifyContent: "space-between" }}>
        Couldn&apos;t load the links.
        <button type="button" className="btn-secondary-modern users-action" onClick={onRetry}>
          Try again
        </button>
      </div>
    );
  }

  return (
    <div>
      {error && <div className="setup-error">{error}</div>}
      {links.length === 0 ? (
        <p className="setup-note" style={{ margin: "8px 0" }}>
          No links yet. Invite and password reset links you create show up here.
        </p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="users-table">
            <thead>
              <tr>
                <th>Link</th>
                <th>Role</th>
                <th>Status</th>
                <th>Created by</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {links.map((l) => {
                const status = statusOf(l);
                return (
                  <tr key={l.id} style={{ opacity: l.status === "open" ? 1 : 0.6 }}>
                    <td>
                      <div style={{ fontWeight: 700 }}>{l.purpose === "reset" ? "Password reset" : "Invite"}</div>
                      <div style={{ wordBreak: "break-all" }}>{l.email}</div>
                      {l.name && <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{l.name}</div>}
                    </td>
                    <td>
                      <span className={`role-chip ${l.role}`}>{ROLE_LABEL[l.role] ?? l.role}</span>
                    </td>
                    <td>
                      <span className={`user-status ${status.tone}`} title={`Expires ${formatSgt(l.expires_at)}`} suppressHydrationWarning>
                        {status.label}
                      </span>
                    </td>
                    <td>
                      {l.created_by_name || "—"}
                      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{formatSgtDay(l.created_at)}</div>
                    </td>
                    <td>
                      {l.status === "open" && (
                        <div style={{ display: "flex", justifyContent: "flex-end" }}>
                          <button
                            type="button"
                            className="btn-secondary-modern users-action danger"
                            onClick={() => revoke(l)}
                            disabled={busyId === l.id}
                          >
                            <Ban style={{ width: 11, height: 11 }} /> Revoke
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="setup-note" style={{ marginTop: 12, marginBottom: 0 }}>
        Links are shown once. Create a new one if it&apos;s lost.
      </p>
    </div>
  );
}
