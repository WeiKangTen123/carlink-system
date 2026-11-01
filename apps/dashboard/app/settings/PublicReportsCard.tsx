"use client";

import { useState } from "react";
import { AlertTriangle, Check, Inbox, Save } from "lucide-react";
import type { AppSettings } from "@/lib/api";
import { updateSettingsAction } from "./actions";
import { SetupCard } from "./SetupCard";

/** Settings -> Company: the three settings for reports the public sends
 * through the Telegram bot (release P6). Saved through PUT /settings like
 * the company name. Missing from `initial` when the API is older than P6. */
export function PublicReportsCard({ initial }: { initial: AppSettings }) {
  const [stored, setStored] = useState({
    phone: initial.public_contact_phone ?? "",
    dpo: initial.dpo_contact ?? "",
    billed: Boolean(initial.company_key_billed),
  });
  const [phone, setPhone] = useState(stored.phone);
  const [dpo, setDpo] = useState(stored.dpo);
  const [billed, setBilled] = useState(stored.billed);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty = phone.trim() !== stored.phone.trim() || dpo.trim() !== stored.dpo.trim() || billed !== stored.billed;
  const dpoMissing = !stored.dpo.trim();

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    const result = await updateSettingsAction({
      public_contact_phone: phone.trim(),
      dpo_contact: dpo.trim(),
      company_key_billed: billed,
    });
    setSaving(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    const next = {
      phone: result.settings.public_contact_phone ?? phone.trim(),
      dpo: result.settings.dpo_contact ?? dpo.trim(),
      billed: result.settings.company_key_billed ?? billed,
    };
    setStored(next);
    setPhone(next.phone);
    setDpo(next.dpo);
    setBilled(next.billed);
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  return (
    <form onSubmit={handleSave}>
      <SetupCard
        id="public-reports"
        icon={<Inbox style={{ width: 22, height: 22, color: "var(--badge-amber-text)" }} />}
        title="Reports from the public"
        subtitle="Anyone can send a report through the Telegram bot; it waits in the Public Inbox until someone takes it"
        status={dpoMissing ? { ok: false, label: "No data-protection contact" } : { ok: true, label: "Contacts set" }}
      >
        {dpoMissing && (
          <div className="inbox-banner warn" role="status" style={{ marginBottom: 12 }}>
            <AlertTriangle style={{ width: 14, height: 14, flexShrink: 0 }} />
            <span>Set a data-protection contact before taking public reports.</span>
          </div>
        )}

        <label style={{ display: "block" }}>
          <span className="detail-field-label">Public contact number</span>
          <input
            type="text"
            className="settings-input"
            value={phone}
            maxLength={40}
            placeholder="e.g. 6123 4567"
            autoComplete="off"
            onChange={(e) => {
              setPhone(e.target.value);
              setSaved(false);
            }}
          />
          <span className="field-hint">
            The bot tells the public to call this number (&quot;You can also call us on …&quot;). Leave it empty and the bot
            leaves those lines out.
          </span>
        </label>

        <label style={{ display: "block", marginTop: 12 }}>
          <span className="detail-field-label">Data-protection contact</span>
          <input
            type="text"
            className="settings-input"
            value={dpo}
            maxLength={200}
            placeholder="e.g. dpo@firm.sg"
            autoComplete="off"
            aria-invalid={dpoMissing && !dpo.trim() ? true : undefined}
            onChange={(e) => {
              setDpo(e.target.value);
              setSaved(false);
            }}
          />
          <span className="field-hint">
            Who the public can ask about their personal data. Shown on the privacy notice the bot links to
            (/privacy#public-reports).
          </span>
        </label>

        <label className="user-tick" style={{ marginTop: 14 }}>
          <input
            type="checkbox"
            checked={billed}
            disabled={saving}
            onChange={(e) => {
              setBilled(e.target.checked);
              setSaved(false);
            }}
          />
          <span>
            The company AI key is on a Google Cloud project with billing turned on
            <span className="field-hint">
              Until this is ticked, the AI runs on a public report only with the key of the person who presses the button,
              and only if they saved it with billing confirmed (Settings → My AI key). On free keys, Google may use what is
              sent to it to improve its products.
            </span>
          </span>
        </label>

        {error && <div className="setup-error" style={{ marginTop: 12 }}>{error}</div>}
        <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 12, marginTop: 12 }}>
          {saved && (
            <span style={{ fontSize: 12, color: "var(--badge-green-text)", fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 4 }}>
              <Check style={{ width: 13, height: 13 }} /> Saved
            </span>
          )}
          <button type="submit" className="btn-primary-modern" disabled={saving || !dirty} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <Save style={{ width: 14, height: 14 }} /> {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </SetupCard>
    </form>
  );
}
