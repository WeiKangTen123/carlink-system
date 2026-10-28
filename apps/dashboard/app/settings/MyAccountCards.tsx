"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check, KeyRound, Save, UserCog } from "lucide-react";
import type { CurrentUser } from "@/lib/api";
import { MIN_PASSWORD_LENGTH, ROLE_LABEL, USERNAME_HINT } from "@/lib/signup";
import { changeMyPasswordAction, updateMyProfileAction } from "./actions";

/** Settings -> My settings: your own profile and password. Moved here from
 * the old My account page; /account now shows only PasswordCard, for the
 * forced change after a temporary password. */

export function ProfileCard({ user }: { user: CurrentUser }) {
  const router = useRouter();
  const fromUser = (u: CurrentUser) => ({
    username: u.username,
    name: u.name,
    qualifications: u.qualifications,
    license_number: u.license_number,
    firm_name: u.firm_name,
  });
  const [stored, setStored] = useState(fromUser(user));
  const [fields, setFields] = useState(fromUser(user));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty = (Object.keys(fields) as (keyof typeof fields)[]).some((key) => fields[key] !== stored[key]);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    const result = await updateMyProfileAction(fields);
    setSaving(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    // What the server stored (the username comes back lower-cased).
    setFields(fromUser(result.user));
    setStored(fromUser(result.user));
    setSaved(true);
    // The top bar shows the name, and the setup checklist follows the profile.
    router.refresh();
  };

  const field = (key: keyof typeof fields, label: string, placeholder = "", hint = "") => (
    <label style={{ display: "block" }}>
      <span className="detail-field-label">{label}</span>
      <input
        className="settings-input"
        value={fields[key]}
        placeholder={placeholder}
        autoCapitalize={key === "username" ? "none" : undefined}
        spellCheck={key === "username" ? false : undefined}
        onChange={(e) => setFields({ ...fields, [key]: e.target.value })}
      />
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );

  return (
    <form id="my-profile" className="card-glass setup-card" onSubmit={save}>
      <div className="setup-card-head">
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
          <div className="setup-card-icon" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
            <UserCog style={{ width: 22, height: 22, color: "var(--accent-primary)" }} />
          </div>
          <div>
            <div className="card-title" style={{ marginBottom: 2 }}>My profile</div>
            <div className="card-subtitle" style={{ marginBottom: 0 }}>Your full name, licence and firm are printed on reports you sign off</div>
          </div>
        </div>
      </div>
      <div className="setup-card-divider" />

      <div className="account-grid">
        {field("username", "Username", "", `Signs you in, like your email. ${USERNAME_HINT}`)}
        {field("name", "Full name", "e.g. Tan Wei Ming", "Printed on reports you sign off.")}
        <label style={{ display: "block" }}>
          <span className="detail-field-label">Email</span>
          <input className="settings-input" value={user.email} readOnly disabled />
        </label>
        {field("qualifications", "Qualifications", "e.g. MIMI, MIRTE, LCGI")}
        {field("license_number", "Licence no.")}
        {field("firm_name", "Firm")}
        <label style={{ display: "block" }}>
          <span className="detail-field-label">Role</span>
          <input className="settings-input" value={ROLE_LABEL[user.role] ?? user.role} readOnly disabled />
        </label>
      </div>
      <p className="setup-note" style={{ marginTop: 12 }}>
        Your email and role can only be changed by an admin.
      </p>

      {error && <div className="setup-error">{error}</div>}
      <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 12 }}>
        {saved && !dirty && (
          <span style={{ fontSize: 12, color: "var(--badge-green-text)", fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 4 }}>
            <Check style={{ width: 13, height: 13 }} /> Saved
          </span>
        )}
        <button type="submit" className="btn-primary-modern" disabled={saving || !dirty} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Save style={{ width: 14, height: 14 }} /> {saving ? "Saving…" : "Save profile"}
        </button>
      </div>
    </form>
  );
}

/** forced: the first sign-in with a temporary password (/account). The rest
 * of Carlink opens once it's replaced. */
export function PasswordCard({ forced }: { forced: boolean }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const problem =
    next && next.length < MIN_PASSWORD_LENGTH
      ? `Use at least ${MIN_PASSWORD_LENGTH} characters.`
      : again && again !== next
        ? "The two new passwords don't match."
        : null;

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (problem) return;
    setSaving(true);
    setError(null);
    const result = await changeMyPasswordAction(current, next);
    setSaving(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    if (forced) {
      // A full load, so the layout stops treating the account as
      // needing a new password.
      window.location.assign("/");
      return;
    }
    setCurrent("");
    setNext("");
    setAgain("");
    setDone(true);
  };

  return (
    <form id="my-password" className="card-glass setup-card" onSubmit={save}>
      <div className="setup-card-head">
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
          <div className="setup-card-icon" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
            <KeyRound style={{ width: 22, height: 22, color: "var(--accent-cyan)" }} />
          </div>
          <div>
            <div className="card-title" style={{ marginBottom: 2 }}>{forced ? "Choose your password" : "Password"}</div>
            <div className="card-subtitle" style={{ marginBottom: 0 }}>
              At least {MIN_PASSWORD_LENGTH} characters; 3 or 4 random words are easy to remember. Changing it signs
              out your other devices.
            </div>
          </div>
        </div>
      </div>
      <div className="setup-card-divider" />

      <div className="account-grid">
        <label style={{ display: "block" }}>
          <span className="detail-field-label">{forced ? "Temporary password" : "Current password"}</span>
          <input className="settings-input" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
        </label>
        <div />
        <label style={{ display: "block" }}>
          <span className="detail-field-label">New password</span>
          <input className="settings-input" type="password" autoComplete="new-password" value={next} onChange={(e) => { setNext(e.target.value); setDone(false); }} required />
        </label>
        <label style={{ display: "block" }}>
          <span className="detail-field-label">New password again</span>
          <input className="settings-input" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required />
        </label>
      </div>

      {(problem || error) && <div className="setup-error" style={{ marginTop: 12 }}>{problem || error}</div>}
      <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 12, marginTop: 12 }}>
        {done && (
          <span style={{ fontSize: 12, color: "var(--badge-green-text)", fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 4 }}>
            <Check style={{ width: 13, height: 13 }} /> Password changed
          </span>
        )}
        <button type="submit" className="btn-primary-modern" disabled={saving || !current || !next || !again || Boolean(problem)}>
          {saving ? "Saving…" : forced ? "Set password and continue" : "Change password"}
        </button>
      </div>
    </form>
  );
}
