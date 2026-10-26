"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { KeyRound, Pencil, Plus, UserCheck, UserX, Users } from "lucide-react";
import type { AccountLinkRow, UserAccount, UserRole } from "@/lib/api";
import { ROLE_LABEL, USERNAME_HINT, formatSgtDay } from "@/lib/signup";
import { createResetLinkAction, listAccountLinksAction, listUsersAction, setSignupsOpenAction, updateUserAction } from "./actions";
import { AccountLinksTab } from "./AccountLinksTab";
import { CreateInviteDialog, LinkReadyDialog, type ReadyLink } from "./LinkDialogs";

type Tab = "users" | "links";
type Draft = {
  username: string;
  name: string;
  email: string;
  role: UserRole;
  qualifications: string;
  license_number: string;
  firm_name: string;
};

const NEW_FOR_DAYS = 7;

function lastSeen(iso: string | null): string {
  if (!iso) return "Never";
  return new Date(iso).toLocaleString("en-SG", { dateStyle: "medium", timeStyle: "short" });
}

function isNew(u: UserAccount): boolean {
  return Date.now() - new Date(u.created_at).getTime() < NEW_FOR_DAYS * 24 * 3600 * 1000;
}

function statusOf(u: UserAccount): { label: string; tone: "ok" | "warn" | "off" } {
  if (!u.active) return { label: "Disabled", tone: "off" };
  if (u.locked) return { label: "Locked (wrong passwords)", tone: "warn" };
  if (u.must_change_password) return { label: "Temporary password", tone: "warn" };
  return { label: "Active", tone: "ok" };
}

function EditUserDialog({
  initial,
  isSelf,
  onCancel,
  onSubmit,
}: {
  initial: Draft;
  isSelf: boolean;
  onCancel: () => void;
  onSubmit: (draft: Draft) => Promise<string | null>;
}) {
  const [draft, setDraft] = useState<Draft>(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    const problem = await onSubmit(draft);
    setSaving(false);
    if (problem) setError(problem);
  };

  const text = (key: keyof Draft, label: string, type = "text", hint = "") => (
    <label style={{ display: "block" }}>
      <span className="detail-field-label">{label}</span>
      <input
        className="settings-input"
        type={type}
        value={draft[key]}
        autoCapitalize={key === "username" ? "none" : undefined}
        spellCheck={key === "username" ? false : undefined}
        onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
        required={key === "email" || key === "name" || key === "username"}
      />
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );

  return (
    <div className="modal-backdrop" onClick={() => !saving && onCancel()}>
      <form className="modal-card" style={{ maxWidth: 560 }} onSubmit={submit} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ fontSize: 16, fontWeight: 800, margin: "0 0 14px" }}>Edit user</h3>
        <div className="account-grid">
          {text("username", "Username", "text", USERNAME_HINT)}
          {text("name", "Full name", "text", "Printed on reports they sign off.")}
          {text("email", "Email", "email")}
          <div style={{ gridColumn: "1 / -1" }}>
            <span className="detail-field-label">Role</span>
            <div style={{ display: "flex", gap: 18, marginTop: 8, fontSize: 13, flexWrap: "wrap" }}>
              {(["user", "admin"] as UserRole[]).map((role) => (
                <label key={role} style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: isSelf ? "default" : "pointer" }}>
                  <input
                    type="radio"
                    name="role"
                    checked={draft.role === role}
                    disabled={isSelf}
                    onChange={() => setDraft({ ...draft, role })}
                  />
                  {role === "admin" ? "Admin — also manages users, keys and settings" : "User — works on cases"}
                </label>
              ))}
            </div>
            {isSelf && <span className="field-hint">You can&apos;t change your own role.</span>}
          </div>
          <div className="detail-field-label" style={{ gridColumn: "1 / -1", marginTop: 6 }}>
            Surveyor details — printed on reports they sign off
          </div>
          {text("qualifications", "Qualifications")}
          {text("license_number", "Licence no.")}
          {text("firm_name", "Firm")}
        </div>
        {error && <div className="setup-error" style={{ marginTop: 12 }}>{error}</div>}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 14 }}>
          <button type="button" className="btn-secondary-modern" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="btn-primary-modern" disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </div>
  );
}

/** The on/off switch for the public /signup page. Invite links work
 * either way. */
function SignupSwitch({ open, onChange }: { open: boolean; onChange: (open: boolean) => void }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const flip = async () => {
    const next = !open;
    if (!next && !confirm("Turn off new signups? Nobody new can create an account until you turn it back on. Invite links still work.")) return;
    setSaving(true);
    setError(null);
    const result = await setSignupsOpenAction(next);
    setSaving(false);
    if ("error" in result) setError(result.error);
    else onChange(result.open);
  };

  return (
    <div className={`signup-switch ${open ? "on" : "off"}`}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 800, fontSize: 13 }}>New signups: {open ? "on" : "off"}</div>
        <div className="setup-note" style={{ margin: 0 }}>
          {open
            ? "Anyone with the site address can create an account on the sign-in page. They join as a User."
            : "Nobody new can create an account. Invite links still work."}
        </div>
        {error && <div className="setup-error" style={{ margin: "8px 0 0" }}>{error}</div>}
      </div>
      <button type="button" className="btn-secondary-modern" style={{ fontSize: 12, padding: "6px 12px", flexShrink: 0 }} onClick={flip} disabled={saving}>
        {saving ? "Saving…" : open ? "Turn off" : "Turn on"}
      </button>
    </div>
  );
}

function UsersTable({
  users,
  currentUserId,
  busyId,
  error,
  onEdit,
  onResetPassword,
  onSetActive,
}: {
  users: UserAccount[];
  currentUserId: string;
  busyId: string | null;
  error: string | null;
  onEdit: (u: UserAccount) => void;
  onResetPassword: (u: UserAccount) => void;
  onSetActive: (u: UserAccount, active: boolean) => void;
}) {
  return (
    <>
      {error && <div className="setup-error">{error}</div>}

      <div style={{ overflowX: "auto" }}>
        <table className="users-table">
          <thead>
            <tr>
              <th>Username</th>
              <th>Email</th>
              <th>Role</th>
              <th>Joined</th>
              <th>Last sign-in</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const status = statusOf(u);
              const isSelf = u.id === currentUserId;
              const busy = busyId === u.id;
              return (
                <tr key={u.id} style={{ opacity: u.active ? 1 : 0.6 }}>
                  <td style={{ fontWeight: 700 }}>
                    {u.username || u.name}
                    {isSelf && <span className="settings-readonly-tag" style={{ marginLeft: 6 }}>You</span>}
                    {!isSelf && isNew(u) && <span className="user-new-tag">New</span>}
                    {u.name && u.name !== u.username && (
                      <div style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 500 }}>{u.name}</div>
                    )}
                  </td>
                  <td style={{ wordBreak: "break-all" }}>{u.email}</td>
                  <td>
                    <span className={`role-chip ${u.role}`}>{ROLE_LABEL[u.role] ?? u.role}</span>
                  </td>
                  <td style={{ whiteSpace: "nowrap" }}>{formatSgtDay(u.created_at)}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{lastSeen(u.last_login_at)}</td>
                  <td>
                    <span className={`user-status ${status.tone}`}>{status.label}</span>
                  </td>
                  <td>
                    <div style={{ display: "flex", gap: 6, justifyContent: "flex-end", flexWrap: "wrap" }}>
                      <button type="button" className="btn-secondary-modern users-action" onClick={() => onEdit(u)} disabled={busy}>
                        <Pencil style={{ width: 11, height: 11 }} /> Edit
                      </button>
                      {!isSelf && (
                        <button type="button" className="btn-secondary-modern users-action" onClick={() => onResetPassword(u)} disabled={busy}>
                          <KeyRound style={{ width: 11, height: 11 }} /> Reset password
                        </button>
                      )}
                      {!isSelf &&
                        (u.active ? (
                          <button type="button" className="btn-secondary-modern users-action danger" onClick={() => onSetActive(u, false)} disabled={busy}>
                            <UserX style={{ width: 11, height: 11 }} /> Disable
                          </button>
                        ) : (
                          <button type="button" className="btn-secondary-modern users-action" onClick={() => onSetActive(u, true)} disabled={busy}>
                            <UserCheck style={{ width: 11, height: 11 }} /> Enable
                          </button>
                        ))}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="setup-note" style={{ marginTop: 12, marginBottom: 0 }}>
        To make someone an admin, use Edit. Forgot password? Use Reset password and send them the link. Users are
        disabled rather than deleted, so the history on their cases still shows who did what.
      </p>
    </>
  );
}

/** Users, the signup switch, and the one-time invite and password reset
 * links an admin sends. */
export function UsersCard({
  initialUsers,
  initialLinks,
  initialSignupsOpen,
  currentUserId,
}: {
  initialUsers: UserAccount[];
  /** Null when they couldn't be loaded. */
  initialLinks: AccountLinkRow[] | null;
  initialSignupsOpen: boolean;
  currentUserId: string;
}) {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("users");
  const [users, setUsers] = useState(initialUsers);
  const [links, setLinks] = useState(initialLinks);
  const [signupsOpen, setSignupsOpen] = useState(initialSignupsOpen);
  const [editing, setEditing] = useState<UserAccount | null>(null);
  const [inviting, setInviting] = useState(false);
  const [ready, setReady] = useState<ReadyLink | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    const result = await listUsersAction();
    if ("users" in result) setUsers(result.users);
  };

  const refreshLinks = async () => {
    const result = await listAccountLinksAction();
    setLinks("links" in result ? result.links : null);
  };

  /** A new link to show once. It also appears in Invites & links. */
  const issued = (link: ReadyLink) => {
    setReady(link);
    refreshLinks();
  };

  const saveUser = async (draft: Draft): Promise<string | null> => {
    if (!editing) return null;
    const result = await updateUserAction(editing.id, draft);
    if ("error" in result) return result.error;
    // Your own name is in the top bar.
    if (editing.id === currentUserId) router.refresh();
    setEditing(null);
    await refresh();
    return null;
  };

  const resetPassword = async (u: UserAccount) => {
    const who = u.username || u.name;
    if (!confirm(`Create a password reset link for ${who}? You send it to them, and they choose a new password with it.`)) return;
    setBusyId(u.id);
    setError(null);
    const result = await createResetLinkAction(u.id);
    setBusyId(null);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    issued({
      purpose: "reset",
      token: result.token,
      name: u.name,
      email: u.email,
      expiresAt: result.link.expires_at,
      phone: null,
    });
    await refresh();
  };

  const setActive = async (u: UserAccount, active: boolean) => {
    const who = u.username || u.name;
    if (!active && !confirm(`Disable ${who}? They are signed out at once and can't sign in until enabled again.`)) return;
    setBusyId(u.id);
    setError(null);
    const result = await updateUserAction(u.id, { active });
    setBusyId(null);
    if ("error" in result) setError(result.error);
    else await refresh();
  };

  const activeCount = users.filter((u) => u.active).length;
  const tabs: { id: Tab; label: string }[] = [
    { id: "users", label: `Users (${users.length})` },
    { id: "links", label: "Invites & links" },
  ];

  return (
    <div className="card-glass setup-card">
      <div className="setup-card-head">
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12, minWidth: 0 }}>
          <div className="setup-card-icon" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Users style={{ width: 22, height: 22, color: "var(--accent-primary)" }} />
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="card-title" style={{ marginBottom: 2 }}>Users</div>
            <div className="card-subtitle" style={{ marginBottom: 0 }}>Who can sign in, and what they can do</div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
          <span className="setup-status">
            <span style={{ color: "var(--badge-green-text)" }}>●</span> {activeCount} active
          </span>
          <button
            type="button"
            className="btn-primary-modern"
            style={{ fontSize: 12, padding: "6px 12px", display: "inline-flex", alignItems: "center", gap: 4 }}
            onClick={() => setInviting(true)}
            title="A one-time link for one email -- works even while signups are off"
          >
            <Plus style={{ width: 13, height: 13 }} /> Create invite link
          </button>
        </div>
      </div>

      <SignupSwitch open={signupsOpen} onChange={setSignupsOpen} />

      <div className="case-tab-bar" role="tablist" aria-label="Users" style={{ marginBottom: 16 }}>
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={`case-tab ${tab === t.id ? "active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "links" && (
        <AccountLinksTab
          links={links}
          onRetry={refreshLinks}
          onChanged={(changed) => setLinks((all) => all?.map((l) => (l.id === changed.id ? changed : l)) ?? all)}
        />
      )}

      {tab === "users" && (
        <UsersTable
          users={users}
          currentUserId={currentUserId}
          busyId={busyId}
          error={error}
          onEdit={setEditing}
          onResetPassword={resetPassword}
          onSetActive={setActive}
        />
      )}

      {editing && (
        <EditUserDialog
          initial={{
            username: editing.username,
            name: editing.name,
            email: editing.email,
            role: editing.role,
            qualifications: editing.qualifications,
            license_number: editing.license_number,
            firm_name: editing.firm_name,
          }}
          isSelf={editing.id === currentUserId}
          onCancel={() => setEditing(null)}
          onSubmit={saveUser}
        />
      )}

      {inviting && (
        <CreateInviteDialog
          onCancel={() => setInviting(false)}
          onCreated={(link) => {
            setInviting(false);
            issued(link);
          }}
        />
      )}

      {ready && <LinkReadyDialog link={ready} onClose={() => setReady(null)} />}
    </div>
  );
}
