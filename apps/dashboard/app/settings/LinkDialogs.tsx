"use client";

import { useState } from "react";
import { AlertTriangle, Check, Copy, MessageCircle } from "lucide-react";
import type { LinkLifetime, UserRole } from "@/lib/api";
import { firstName, formatSgt, fullMobile, whatsAppDigits } from "@/lib/signup";
import { createAccountLinkAction } from "./actions";

/** A link just made, for the "link ready" dialog. The token is only ever
 * in memory here: the API returns it once and keeps just its hash. */
export type ReadyLink = {
  purpose: "invite" | "reset";
  token: string;
  name: string;
  email: string;
  expiresAt: string;
  /** For the Open WhatsApp button; null hides it. */
  phone: string | null;
};

export const LIFETIMES: { hours: LinkLifetime; label: string }[] = [
  { hours: 24, label: "1 day" },
  { hours: 72, label: "3 days" },
  { hours: 168, label: "7 days" },
];

export function RoleChoice({ name, value, onChange }: { name: string; value: UserRole; onChange: (role: UserRole) => void }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8, fontSize: 13 }}>
      {(["user", "admin"] as UserRole[]).map((role) => (
        <label key={role} style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input type="radio" name={name} checked={value === role} onChange={() => onChange(role)} />
          {role === "admin" ? "Admin — also manages users, keys and settings" : "User — works on cases"}
        </label>
      ))}
    </div>
  );
}

export function LifetimeChoice({ name, value, onChange }: { name: string; value: LinkLifetime; onChange: (hours: LinkLifetime) => void }) {
  return (
    <div style={{ display: "flex", gap: 18, marginTop: 8, fontSize: 13, flexWrap: "wrap" }}>
      {LIFETIMES.map(({ hours, label }) => (
        <label key={hours} style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input type="radio" name={name} checked={value === hours} onChange={() => onChange(hours)} />
          {label}
        </label>
      ))}
    </div>
  );
}

async function copyText(text: string): Promise<boolean> {
  try {
    // Missing outside HTTPS, and refused without permission.
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

function whatsAppMessage(link: ReadyLink, url: string): string {
  const first = firstName(link.name);
  const hi = first ? `Hi ${first}` : "Hi";
  const expires = formatSgt(link.expiresAt);
  return link.purpose === "reset"
    ? `${hi}, here's a link to set a new Carlink Studio password: ${url} It expires on ${expires}.`
    : `${hi}, here's your Carlink Studio invite. Open it to set your password: ${url} It expires on ${expires}.`;
}

/** Shown once, straight after a link is made: the API never returns the
 * token again. Only Done closes it, so a stray click can't lose the link. */
export function LinkReadyDialog({ link, onClose }: { link: ReadyLink; onClose: () => void }) {
  // Built here, from the address the admin is using, so it always points
  // at this dashboard. The token goes after "#": browsers never send that
  // part to a server, so it stays out of logs on the way.
  const url = `${window.location.origin}/invite#${link.token}`;
  const message = whatsAppMessage(link, url);
  const digits = whatsAppDigits(link.phone);
  const [copied, setCopied] = useState<"link" | "message" | null>(null);
  const [passedOn, setPassedOn] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);

  const copy = async (what: "link" | "message") => {
    const ok = await copyText(what === "link" ? url : message);
    setCopyFailed(!ok);
    setCopied(ok ? what : null);
    if (ok) setPassedOn(true);
  };

  const done = () => {
    if (!passedOn && !confirm("You haven't copied the link. Close anyway? It isn't shown again.")) return;
    onClose();
  };

  const who = link.name ? `${link.name} (${link.email})` : link.email;

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="link-ready-title">
      <div className="modal-card" style={{ maxWidth: 520 }}>
        <h3 id="link-ready-title" style={{ fontSize: 16, fontWeight: 800, margin: "0 0 4px" }}>
          {link.purpose === "reset" ? "Password reset link ready" : "Invite link ready"}
        </h3>
        <p className="setup-note">
          For {who}. Send it to them yourself, e.g. by WhatsApp. It works once and expires on {formatSgt(link.expiresAt)}.
        </p>

        <label style={{ display: "block" }}>
          <span className="detail-field-label">Link</span>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
            <input
              className="settings-input link-ready-url"
              style={{ marginTop: 0 }}
              value={url}
              readOnly
              onFocus={(e) => e.currentTarget.select()}
            />
            <button type="button" className="btn-secondary-modern" style={{ flexShrink: 0 }} onClick={() => copy("link")}>
              {copied === "link" ? <Check style={{ width: 13, height: 13 }} /> : <Copy style={{ width: 13, height: 13 }} />}
              {copied === "link" ? "Copied" : "Copy"}
            </button>
          </div>
        </label>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
          <button type="button" className="btn-secondary-modern" onClick={() => copy("message")}>
            {copied === "message" ? <Check style={{ width: 13, height: 13 }} /> : <MessageCircle style={{ width: 13, height: 13 }} />}
            {copied === "message" ? "Message copied" : "Copy WhatsApp message"}
          </button>
          {digits && (
            <a
              className="btn-secondary-modern"
              href={`https://wa.me/${digits}?text=${encodeURIComponent(message)}`}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => setPassedOn(true)}
            >
              Open WhatsApp →
            </a>
          )}
        </div>
        {copyFailed && (
          <div className="setup-error" role="alert" style={{ marginTop: 12, marginBottom: 0 }}>
            Couldn&apos;t copy here. Click the link above, then copy it with Ctrl+C (⌘C on a Mac).
          </div>
        )}

        <div className="link-ready-warning">
          <AlertTriangle style={{ width: 14, height: 14, flexShrink: 0 }} /> This link isn&apos;t shown again.
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button type="button" className="btn-primary-modern" onClick={done}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

/** An invite made directly, without anyone asking on /signup first. */
export function CreateInviteDialog({ onCancel, onCreated }: { onCancel: () => void; onCreated: (link: ReadyLink) => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<UserRole>("user");
  const [mobile, setMobile] = useState("");
  const [hours, setHours] = useState<LinkLifetime>(72);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    const phone = mobile.trim() ? fullMobile(mobile) : null;
    if (phone && !whatsAppDigits(phone)) {
      setError("Enter a mobile number, e.g. 9123 4567, or leave it blank.");
      return;
    }
    setSaving(true);
    const result = await createAccountLinkAction({
      email: email.trim(),
      role,
      name: name.trim() || undefined,
      expires_hours: hours,
    });
    setSaving(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    onCreated({
      purpose: "invite",
      token: result.token,
      name: result.link.name || name.trim(),
      email: result.link.email,
      expiresAt: result.link.expires_at,
      phone,
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
        aria-labelledby="create-invite-title"
      >
        <h3 id="create-invite-title" style={{ fontSize: 16, fontWeight: 800, margin: "0 0 4px" }}>
          Create invite link
        </h3>
        <p className="setup-note">They open the link, choose their own password and are signed in. You send it to them.</p>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <label style={{ display: "block" }}>
            <span className="detail-field-label">Email</span>
            <input className="settings-input" type="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label style={{ display: "block" }}>
            <span className="detail-field-label">Name (optional)</span>
            <input className="settings-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Filled in for them; they can change it" />
          </label>
          <div>
            <span className="detail-field-label">Role</span>
            <RoleChoice name="invite-role" value={role} onChange={setRole} />
          </div>
          <label style={{ display: "block" }}>
            <span className="detail-field-label">Mobile (optional)</span>
            <div className="phone-field">
              {!mobile.trim().startsWith("+") && <span className="phone-prefix">+65</span>}
              <input
                className="settings-input"
                type="tel"
                inputMode="tel"
                placeholder="9123 4567"
                value={mobile}
                onChange={(e) => setMobile(e.target.value)}
              />
            </div>
            <span className="field-hint">Only for the Open WhatsApp button. Not sent to the server or saved.</span>
          </label>
          <div>
            <span className="detail-field-label">Expires in</span>
            <LifetimeChoice name="invite-hours" value={hours} onChange={setHours} />
          </div>
        </div>

        {error && (
          <div className="setup-error" style={{ marginTop: 14 }}>
            {error}
          </div>
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 14 }}>
          <button type="button" className="btn-secondary-modern" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="btn-primary-modern" disabled={saving || !email.trim()}>
            {saving ? "Creating…" : "Create link"}
          </button>
        </div>
      </form>
    </div>
  );
}
