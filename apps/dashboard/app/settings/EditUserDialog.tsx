"use client";

import { useState } from "react";
import type { CaseCounts, UserAccount, UserRole, UserTicks } from "@/lib/api";
import { USERNAME_HINT } from "@/lib/signup";

export type EditUserDraft = {
  username: string;
  name: string;
  email: string;
  role: UserRole;
  qualifications: string;
  license_number: string;
  firm_name: string;
} & UserTicks;

/** The three ticks, with what each one means when off. Admins have all of
 * it whatever is ticked. */
const TICKS: { key: keyof UserTicks; label: string; hint: string }[] = [
  {
    key: "sees_all_cases",
    label: "Can see and work on all cases",
    hint: "Off: only the cases they own. Either way, they can't delete or assign cases.",
  },
  {
    key: "ai_company_key_allowed",
    label: "May use the company AI key",
    hint: "Off: the AI buttons don't work for them.",
  },
  {
    key: "may_sign_off",
    label: "May sign off reports",
    hint: "Off: they can't sign off, and the PDFs of their cases say they were prepared by an unverified user.",
  },
];

export function ticksOf(u: UserAccount): UserTicks {
  return {
    sees_all_cases: Boolean(u.sees_all_cases),
    ai_company_key_allowed: Boolean(u.ai_company_key_allowed),
    may_sign_off: Boolean(u.may_sign_off),
  };
}

export function draftOf(u: UserAccount): EditUserDraft {
  return {
    username: u.username,
    name: u.name,
    email: u.email,
    role: u.role,
    qualifications: u.qualifications,
    license_number: u.license_number,
    firm_name: u.firm_name,
    ...ticksOf(u),
  };
}

/** "4 open · 11 signed off · 1 draft". */
export function CaseCountsText({ cases }: { cases: CaseCounts | null | undefined }) {
  if (!cases) return <span style={{ color: "var(--text-muted)" }}>—</span>;
  const part = (n: number, one: string, many: string) => (
    <span className={n ? undefined : "user-cases-zero"}>
      {n} {n === 1 ? one : many}
    </span>
  );
  return (
    <span className="user-cases">
      {part(cases.open, "open", "open")}
      <span aria-hidden> · </span>
      {part(cases.signed_off, "signed off", "signed off")}
      <span aria-hidden> · </span>
      {part(cases.drafts, "draft", "drafts")}
    </span>
  );
}

/** Settings -> Users -> Edit. On your own account the role can't change
 * (the API refuses it too, 403 not_allowed), so the role choice is greyed
 * out with a note.
 *
 * SLOTS (P3-UI): an "AI key" row with [Remove their key]; (P5) a
 * "Telegram" row with [Disconnect]. Both act at once after a confirm,
 * between Access and Cases (agent5 §9 layout); Save covers the rest. */
export function EditUserDialog({
  user,
  isSelf,
  onCancel,
  onSubmit,
}: {
  user: UserAccount;
  isSelf: boolean;
  onCancel: () => void;
  onSubmit: (draft: EditUserDraft) => Promise<string | null>;
}) {
  const initial = draftOf(user);
  const [draft, setDraft] = useState<EditUserDraft>(initial);
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

  const isAdminDraft = draft.role === "admin";
  // Made a user just now, with the first box off: say what that takes away.
  const losesAllCases = initial.role === "admin" && !isAdminDraft && !draft.sees_all_cases;

  const text = (key: Exclude<keyof EditUserDraft, "role" | keyof UserTicks>, label: string, type = "text", hint = "") => (
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
      <form
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-user-title"
        style={{ maxWidth: 560 }}
        onSubmit={submit}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="edit-user-title" style={{ fontSize: 16, fontWeight: 800, margin: "0 0 14px" }}>
          Edit {user.username ? `@${user.username}` : "user"}
          {isSelf && <span className="settings-readonly-tag" style={{ marginLeft: 8 }}>You</span>}
        </h3>
        <div className="account-grid">
          {text("username", "Username", "text", USERNAME_HINT)}
          {text("name", "Full name", "text", "Printed on reports they sign off.")}
          {text("email", "Email", "email")}
          <div style={{ gridColumn: "1 / -1" }}>
            <span className="detail-field-label">Role</span>
            <div style={{ display: "flex", gap: 18, marginTop: 8, fontSize: 13, flexWrap: "wrap" }}>
              {(["user", "admin"] as UserRole[]).map((role) => (
                <label
                  key={role}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    cursor: isSelf ? "default" : "pointer",
                    color: isSelf ? "var(--text-muted)" : undefined,
                  }}
                >
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
          <div style={{ gridColumn: "1 / -1" }}>
            <span className="detail-field-label">Access</span>
            <div className="user-ticks">
              {TICKS.map((t) => (
                <label key={t.key} className={`user-tick ${isAdminDraft ? "disabled" : ""}`}>
                  <input
                    type="checkbox"
                    // Admins have all three whatever is ticked.
                    checked={isAdminDraft || draft[t.key]}
                    disabled={isAdminDraft}
                    onChange={(e) => setDraft({ ...draft, [t.key]: e.target.checked })}
                  />
                  <span>
                    {t.label}
                    {!isAdminDraft && <span className="field-hint">{t.hint}</span>}
                  </span>
                </label>
              ))}
            </div>
            {isAdminDraft && <span className="field-hint">Admins always have these.</span>}
            {losesAllCases && (
              <span className="field-hint user-tick-warning">
                As a user with the first box off, they will only see the cases they own.
              </span>
            )}
          </div>
          <div style={{ gridColumn: "1 / -1" }}>
            <span className="detail-field-label">Cases</span>
            <div style={{ fontSize: 13, marginTop: 6 }}>
              <CaseCountsText cases={user.cases} />
            </div>
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
