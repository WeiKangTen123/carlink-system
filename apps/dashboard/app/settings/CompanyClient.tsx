"use client";

import { useState } from "react";
import { Building2, Check, MessageCircle, Save, Send } from "lucide-react";
import type { AppSettings } from "@/lib/api";
import { updateSettingsAction } from "./actions";
import { CompanyAiKeysCard } from "./CompanyAiKeysCard";
import { SetupCard } from "./SetupCard";

/** Only the facts this page actually displays. Deliberately not the full
 * SystemInfo -- see the note in company/page.tsx about not shipping
 * deployment internals into the page payload. */
export interface SetupStatus {
  geminiEnvKeyConfigured: boolean;
  telegramConfigured: boolean;
  whatsappConfigured: boolean;
  telegramReports: number;
  whatsappReports: number;
}

/** Report branding, saved on its own (there is no page-wide save bar: each
 * card saves itself). */
function BrandingCard({ initial }: { initial: AppSettings }) {
  const [stored, setStored] = useState(initial.company_name);
  const [companyName, setCompanyName] = useState(initial.company_name);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty = companyName.trim() !== stored.trim();

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    const result = await updateSettingsAction({ company_name: companyName });
    setSaving(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    setCompanyName(result.settings.company_name);
    setStored(result.settings.company_name);
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  return (
    <form onSubmit={handleSave}>
      <SetupCard
        id="report-branding"
        icon={<Building2 style={{ width: 22, height: 22, color: "var(--accent-primary)" }} />}
        title="Report branding"
        subtitle="Shown as the issuing organisation on PDF reports"
      >
        <label style={{ display: "block" }}>
          <span className="detail-field-label">Company / Consultancy Name</span>
          <input
            type="text"
            className="settings-input"
            value={companyName}
            placeholder="e.g. Carlink Consultancy"
            onChange={(e) => {
              setCompanyName(e.target.value);
              setSaved(false);
            }}
          />
        </label>
        {error && <div className="setup-error" style={{ marginTop: 12 }}>{error}</div>}
        <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 12, marginTop: 12 }}>
          {saved && (
            <span style={{ fontSize: 12, color: "var(--badge-green-text)", fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 4 }}>
              <Check style={{ width: 13, height: 13 }} /> Saved
            </span>
          )}
          <button type="submit" className="btn-primary-modern" disabled={saving || !dirty} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <Save style={{ width: 14, height: 14 }} /> {saving ? "Saving…" : "Save branding"}
          </button>
        </div>
      </SetupCard>
    </form>
  );
}

/** Settings -> Company (admins): the company AI key, the Telegram bot,
 * WhatsApp and report branding. */
export function CompanyClient({ settings, setup }: { settings: AppSettings; setup: SetupStatus }) {
  return (
    <>
      <CompanyAiKeysCard envKeyConfigured={setup.geminiEnvKeyConfigured} />

      {/* SLOT (P5): bot name and "accounts connected" from GET /system/info
          go in this card. */}
      <SetupCard
        id="telegram-bot"
        icon={<Send style={{ width: 22, height: 22, color: "var(--accent-cyan)" }} />}
        title="Telegram bot (company)"
        subtitle="Lets people file incident reports by chat"
        helpUrl="https://t.me/BotFather"
        helpLabel="Create a bot and get a token from @BotFather"
        testKey="telegram"
        status={{
          ok: setup.telegramConfigured,
          label: setup.telegramConfigured ? "Configured" : "Not set up",
        }}
      >
        <div className="settings-info-row">
          <span className="settings-info-label">Bot token</span>
          <span style={{ fontSize: 13, fontWeight: 600 }}>
            {setup.telegramConfigured ? "Set on the server" : "Not set"}
            {setup.telegramConfigured && (
              <span className="settings-readonly-tag" style={{ marginLeft: 8 }}>Environment</span>
            )}
          </span>
        </div>
        <div className="settings-info-row">
          <span className="settings-info-label">Reports filed via Telegram</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 600 }}>{setup.telegramReports}</span>
        </div>
      </SetupCard>

      <SetupCard
        id="whatsapp"
        icon={<MessageCircle style={{ width: 22, height: 22, color: "var(--badge-green-text)" }} />}
        title="WhatsApp"
        subtitle="Optional — file reports over WhatsApp via Twilio"
        helpUrl="https://console.twilio.com/"
        helpLabel="Get your credentials from the Twilio Console"
        testKey="whatsapp"
        status={{
          ok: setup.whatsappConfigured,
          label: setup.whatsappConfigured ? "Configured" : "Not set up",
        }}
      >
        <div className="settings-info-row">
          <span className="settings-info-label">Twilio credentials</span>
          <span style={{ fontSize: 13, fontWeight: 600 }}>
            {setup.whatsappConfigured ? "Set on the server" : "Not set"}
          </span>
        </div>
        <div className="settings-info-row">
          <span className="settings-info-label">Reports filed via WhatsApp</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 600 }}>{setup.whatsappReports}</span>
        </div>
        <p className="setup-note">
          Known limitation: drafting and confirmation work, but the finished PDF isn&apos;t sent back over
          WhatsApp — Twilio can only attach media it can fetch from a public URL, and reports are stored locally.
        </p>
      </SetupCard>

      <BrandingCard initial={settings} />
    </>
  );
}
