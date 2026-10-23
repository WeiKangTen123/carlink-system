"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Copy, KeyRound, Pencil, Plus, UserCheck, UserX, Users } from "lucide-react";
import type { UserAccount } from "@/lib/api";
import { createUserAction, listUsersAction, resetUserPasswordAction, updateUserAction } from "./actions";

type Role = "admin" | "surveyor";
type Draft = { email: string; name: string; role: Role; qualifications: string; license_number: string; firm_name: string };

const EMPTY_DRAFT: Draft = { email: "", name: "", role: "surveyor", qualifications: "", license_number: "", firm_name: "" };

function lastSeen(iso: string | null): string {
  if (!iso) return "Never";
  return new Date(iso).toLocaleString("en-SG", { dateStyle: "medium", timeStyle: "short" });
}

function statusOf(u: UserAccount): { label: string; tone: "ok" | "warn" | "off" } {
  if (!u.active) return { label: "Disabled", tone: "off" };
  if (u.locked) return { label: "Locked (wrong passwords)", tone: "warn" };
  if (u.must_change_password) return { label: "Temporary password", tone: "warn" };
  return { label: "Active", tone: "ok" };
}

/** Shown once after adding a user or resetting a password -- the API never
 * returns it again. */
function TemporaryPassword({ name, password, onClose }: { name: string; password: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="temp-password-panel">
      <div style={{ fontWeight: 800, marginBottom: 4 }}>Temporary password for {name}</div>
      <p className="setup-note" style={{ marginBottom: 10 }}>
        Give this to them yourself (in person or by message). They&apos;ll be asked to choose their own password when
        they sign in. It isn&apos;t shown again.
      </p>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <code className="temp-password-value">{password}</code>
        <button
          type="button"
          className="btn-secondary-modern"
          style={{ fontSize: 11, padding: "5px 12px", display: "inline-flex", alignItems: "center", gap: 4 }}
          onClick={() => {
            navigator.clipboard?.writeText(password);
            setCopied(true);
          }}
        >
          {copied ? <Check style={{ width: 12, height: 12 }} /> : <Copy style={{ width: 12, height: 12 }} />} {copied ? "Copied" : "Copy"}
        </button>
        <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px", marginLeft: "auto" }} onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}

function UserForm({
  initial,
  editing,
  isSelf,
  onCancel,
  onSubmit,
}: {
  initial: Draft;
  editing: boolean;
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

  const text = (key: keyof Draft, label: string, type = "text", placeholder = "") => (
    <label style={{ display: "block" }}>
      <span className="detail-field-label">{label}</span>
      <input
        className="settings-input"
        type={type}
        value={draft[key]}
        placeholder={placeholder}
        onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
        required={key === "email" || key === "name"}
      />
    </label>
  );

  return (
    <div className="modal-backdrop" onClick={() => !saving && onCancel()}>
      <form className="modal-card" style={{ maxWidth: 560 }} onSubmit={submit} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ fontSize: 16, fontWeight: 800, margin: "0 0 14px" }}>{editing ? "Edit user" : "Add user"}</h3>
        <div className="account-grid">
          {text("name", "Name")}
          {text("email", "Email", "email")}
          <div style={{ gridColumn: "1 / -1" }}>
            <span className="detail-field-label">Role</span>
            <div style={{ display: "flex", gap: 18, marginTop: 8, fontSize: 13 }}>
              {(["surveyor", "admin"] as Role[]).map((role) => (
                <label key={role} style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: isSelf ? "default" : "pointer" }}>
                  <input
                    type="radio"
                    name="role"
                    checked={draft.role === role}
                    disabled={isSelf}
                    onChange={() => setDraft({ ...draft, role })}
                  />
                  {role === "admin" ? "Admin — also manages users, keys and settings" : "Surveyor — works on cases"}
                </label>
              ))}
            </div>
          </div>
          <div className="detail-field-label" style={{ gridColumn: "1 / -1", marginTop: 6 }}>
            Surveyor profile — printed on reports they sign off
          </div>
          {text("qualifications", "Qualifications", "text", "e.g. MIMI, MIRTE")}
          {text("license_number", "Licence no.")}
          {text("firm_name", "Firm")}
        </div>
        {!editing && (
          <p className="setup-note" style={{ marginTop: 12 }}>
            A temporary password is made for them; you&apos;ll see it once, after saving.
          </p>
        )}
        {error && <div className="setup-error" style={{ marginTop: 12 }}>{error}</div>}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 14 }}>
          <button type="button" className="btn-secondary-modern" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="btn-primary-modern" disabled={saving}>
            {saving ? "Saving…" : editing ? "Save" : "Add user"}
          </button>
        </div>
      </form>
    </div>
  );
}

export function UsersCard({ initialUsers, currentUserId }: { initialUsers: UserAccount[]; currentUserId: string }) {
  const router = useRouter();
  const [users, setUsers] = useState(initialUsers);
  const [form, setForm] = useState<{ user: UserAccount | null } | null>(null);
  const [temp, setTemp] = useState<{ name: string; password: string } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    const result = await listUsersAction();
    if ("users" in result) setUsers(result.users);
  };

  const submitForm = async (draft: Draft): Promise<string | null> => {
    if (form?.user) {
      const result = await updateUserAction(form.user.id, draft);
      if ("error" in result) return result.error;
      // Your own name is in the top bar.
      if (form.user.id === currentUserId) router.refresh();
    } else {
      const result = await createUserAction(draft);
      if ("error" in result) return result.error;
      setTemp({ name: result.user.name, password: result.temporaryPassword });
    }
    setForm(null);
    await refresh();
    return null;
  };

  const resetPassword = async (u: UserAccount) => {
    if (!confirm(`Reset ${u.name}'s password? They are signed out everywhere and get a new temporary password.`)) return;
    setBusyId(u.id);
    setError(null);
    const result = await resetUserPasswordAction(u.id);
    setBusyId(null);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    setTemp({ name: u.name, password: result.temporaryPassword });
    await refresh();
  };

  const setActive = async (u: UserAccount, active: boolean) => {
    if (!active && !confirm(`Disable ${u.name}? They are signed out at once and can't sign in until enabled again.`)) return;
    setBusyId(u.id);
    setError(null);
    const result = await updateUserAction(u.id, { active });
    setBusyId(null);
    if ("error" in result) setError(result.error);
    else await refresh();
  };

  const activeCount = users.filter((u) => u.active).length;

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
            onClick={() => setForm({ user: null })}
          >
            <Plus style={{ width: 13, height: 13 }} /> Add user
          </button>
        </div>
      </div>
      <div className="setup-card-divider" />

      {temp && <TemporaryPassword name={temp.name} password={temp.password} onClose={() => setTemp(null)} />}
      {error && <div className="setup-error">{error}</div>}

      <div style={{ overflowX: "auto" }}>
        <table className="users-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Role</th>
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
                    {u.name}
                    {isSelf && <span className="settings-readonly-tag" style={{ marginLeft: 6 }}>You</span>}
                    {u.license_number && (
                      <div style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 500, fontFamily: "var(--font-mono)" }}>
                        {u.license_number}
                      </div>
                    )}
                  </td>
                  <td style={{ wordBreak: "break-all" }}>{u.email}</td>
                  <td>
                    <span className={`role-chip ${u.role}`}>{u.role === "admin" ? "Admin" : "Surveyor"}</span>
                  </td>
                  <td style={{ whiteSpace: "nowrap" }}>{lastSeen(u.last_login_at)}</td>
                  <td>
                    <span className={`user-status ${status.tone}`}>{status.label}</span>
                  </td>
                  <td>
                    <div style={{ display: "flex", gap: 6, justifyContent: "flex-end", flexWrap: "wrap" }}>
                      <button type="button" className="btn-secondary-modern users-action" onClick={() => setForm({ user: u })} disabled={busy}>
                        <Pencil style={{ width: 11, height: 11 }} /> Edit
                      </button>
                      {!isSelf && (
                        <button type="button" className="btn-secondary-modern users-action" onClick={() => resetPassword(u)} disabled={busy}>
                          <KeyRound style={{ width: 11, height: 11 }} /> Reset password
                        </button>
                      )}
                      {!isSelf &&
                        (u.active ? (
                          <button type="button" className="btn-secondary-modern users-action danger" onClick={() => setActive(u, false)} disabled={busy}>
                            <UserX style={{ width: 11, height: 11 }} /> Disable
                          </button>
                        ) : (
                          <button type="button" className="btn-secondary-modern users-action" onClick={() => setActive(u, true)} disabled={busy}>
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
        Forgot password? Use Reset password and pass the temporary one on. Users are disabled rather than deleted, so
        the history on their cases still shows who did what.
      </p>

      {form && (
        <UserForm
          initial={
            form.user
              ? {
                  email: form.user.email,
                  name: form.user.name,
                  role: form.user.role,
                  qualifications: form.user.qualifications,
                  license_number: form.user.license_number,
                  firm_name: form.user.firm_name,
                }
              : EMPTY_DRAFT
          }
          editing={Boolean(form.user)}
          isSelf={form.user?.id === currentUserId}
          onCancel={() => setForm(null)}
          onSubmit={submitForm}
        />
      )}
    </div>
  );
}
