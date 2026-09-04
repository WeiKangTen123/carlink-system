"use client";

import { useEffect, useState } from "react";
import type { AppSettings, LlmKey, SystemInfo } from "@/lib/api";
import {
  updateSettingsAction,
  listLlmKeysAction,
  addLlmKeyAction,
  deleteLlmKeyAction,
  testServiceAction,
} from "./actions";

type TestState = { ok: boolean; message: string } | null;

function TestResult({ result }: { result: TestState }) {
  if (!result) return null;
  return (
    <span className={`setup-test-result ${result.ok ? "ok" : "fail"}`}>
      {result.ok ? "✓" : "✕"} {result.message}
    </span>
  );
}

/** Where to actually GO to get the credential this card asks for --
 * shown as a real link, not just described in prose. */
function HelpLink({ url, label }: { url: string; label: string }) {
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className="setup-help-link">
      {label} ↗
    </a>
  );
}

function SetupCard({
  icon,
  title,
  subtitle,
  helpUrl,
  helpLabel,
  testKey,
  status,
  children,
}: {
  icon: string;
  title: string;
  subtitle: string;
  helpUrl?: string;
  helpLabel?: string;
  testKey?: string;
  status?: { ok: boolean; label: string };
  children: React.ReactNode;
}) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<TestState>(null);

  const runTest = async () => {
    if (!testKey) return;
    setTesting(true);
    setResult(null);
    setResult(await testServiceAction(testKey));
    setTesting(false);
  };

  return (
    <div className="card-glass setup-card">
      <div className="setup-card-head">
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12, minWidth: 0 }}>
          <div className="setup-card-icon">{icon}</div>
          <div style={{ minWidth: 0 }}>
            <div className="card-title" style={{ marginBottom: 2 }}>{title}</div>
            <div className="card-subtitle" style={{ marginBottom: 0 }}>{subtitle}</div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0, flexWrap: "wrap" }}>
          <TestResult result={result} />
          {status && !result && (
            <span className="setup-status">
              <span style={{ color: status.ok ? "var(--badge-green-text)" : "var(--text-muted)" }}>●</span> {status.label}
            </span>
          )}
          {testKey && (
            <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} onClick={runTest} disabled={testing}>
              {testing ? "Testing…" : "⚡ Test"}
            </button>
          )}
        </div>
      </div>

      {helpUrl && helpLabel && (
        <div style={{ marginBottom: 14 }}>
          <HelpLink url={helpUrl} label={helpLabel} />
        </div>
      )}

      <div className="setup-card-divider" />
      {children}
    </div>
  );
}

function GeminiKeysCard({ envKeyConfigured }: { envKeyConfigured: boolean }) {
  const [keys, setKeys] = useState<LlmKey[] | null>(null);
  const [newKey, setNewKey] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [error, setError] = useState("");

  const refresh = async () => {
    const result = await listLlmKeysAction();
    setKeys("error" in result ? [] : result.keys);
  };

  useEffect(() => {
    refresh();
  }, []);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newKey.trim()) return;
    setAdding(true);
    setError("");
    const result = await addLlmKeyAction(newKey.trim(), newLabel.trim() || undefined);
    setAdding(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    setNewKey("");
    setNewLabel("");
    setShowKey(false);
    refresh();
  };

  const handleRemove = async (id: string) => {
    setRemoving(id);
    setError("");
    const result = await deleteLlmKeyAction(id);
    setRemoving(null);
    if ("error" in result) setError(result.error);
    else refresh();
  };

  const hasAny = (keys?.length ?? 0) > 0 || envKeyConfigured;

  return (
    <SetupCard
      icon="🤖"
      title="Gemini AI"
      subtitle="Reads incident photos and detects damaged parts"
      helpUrl="https://aistudio.google.com/apikey"
      helpLabel="Get a Gemini API key at Google AI Studio"
      testKey="llm"
      status={{ ok: hasAny, label: hasAny ? "Configured" : "Not set up" }}
    >
      <p className="setup-note">
        Add more than one key for extra headroom — when one key&apos;s quota runs out, the next is used
        automatically. Nothing else to configure.
      </p>

      {error && <div className="setup-error">{error}</div>}

      {keys === null ? (
        <p className="setup-note" style={{ marginTop: 0 }}>Loading keys…</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 14 }}>
          {keys.map((k) => (
            <div key={k.id} className="setup-key-row">
              <span style={{ fontSize: 15 }}>🔑</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 700 }}>
                ••••••••••••{k.last4}
              </span>
              {k.label && <span className="chip-severity minor" style={{ fontSize: 10 }}>{k.label}</span>}
              <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-muted)" }}>
                added {new Date(k.created_at).toLocaleDateString()}
              </span>
              <button
                type="button"
                className="btn-secondary-modern"
                style={{ fontSize: 11, padding: "4px 10px", color: "var(--danger, #ef4444)" }}
                onClick={() => handleRemove(k.id)}
                disabled={removing === k.id}
              >
                {removing === k.id ? "…" : "Remove"}
              </button>
            </div>
          ))}

          {keys.length === 0 && envKeyConfigured && (
            <div className="setup-key-row" style={{ opacity: 0.75 }}>
              <span style={{ fontSize: 15 }}>🔒</span>
              <span style={{ fontSize: 12 }}>Using the key set on the server</span>
              <span className="settings-readonly-tag" style={{ marginLeft: "auto" }}>Environment</span>
            </div>
          )}

          {keys.length === 0 && !envKeyConfigured && (
            <p className="setup-note" style={{ marginTop: 0, color: "var(--badge-amber-text)" }}>
              No API key configured — photo analysis will fail until one is added.
            </p>
          )}
        </div>
      )}

      <form onSubmit={handleAdd} className="setup-add-key">
        <div style={{ position: "relative", flex: 1, minWidth: 220 }}>
          <input
            type={showKey ? "text" : "password"}
            className="settings-input"
            style={{ marginTop: 0, paddingRight: 38 }}
            placeholder="Paste a Gemini API key"
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
            autoComplete="new-password"
          />
          <button
            type="button"
            className="setup-reveal-btn"
            onClick={() => setShowKey(!showKey)}
            title={showKey ? "Hide" : "Reveal"}
          >
            {showKey ? "🙈" : "👁"}
          </button>
        </div>
        <input
          type="text"
          className="settings-input"
          style={{ marginTop: 0, width: 150, flexShrink: 0 }}
          placeholder="Label (optional)"
          value={newLabel}
          onChange={(e) => setNewLabel(e.target.value)}
        />
        <button type="submit" className="btn-primary-modern" disabled={adding || !newKey.trim()}>
          {adding ? "Adding…" : "+ Add key"}
        </button>
      </form>
    </SetupCard>
  );
}

export function SettingsClient({
  settings,
  system,
}: {
  settings: AppSettings;
  system: SystemInfo;
}) {
  const [companyName, setCompanyName] = useState(settings.company_name);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty = companyName.trim() !== settings.company_name.trim();

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
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  const tgCount = system.channels.reports_by_channel.telegram ?? 0;
  const waCount = system.channels.reports_by_channel.whatsapp ?? 0;

  return (
    <div style={{ maxWidth: 900 }}>
      <div className="page-header" style={{ marginBottom: 20 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>Settings</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            Connect the services Carlink needs, and check they&apos;re working
          </p>
        </div>
      </div>

      {!system.auth.configured && (
        <div className="setup-warning-banner">
          <strong>⚠️ This dashboard has no login.</strong> Anyone who can reach its URL can view and change
          everything here, including adding or removing API keys. Keys themselves are never displayed back —
          only their last 4 characters — but treat this address as sensitive until access control is added.
        </div>
      )}

      <GeminiKeysCard envKeyConfigured={system.ai.api_key_configured} />

      <SetupCard
        icon="✈️"
        title="Telegram Bot"
        subtitle="Lets people file incident reports by chat"
        helpUrl="https://t.me/BotFather"
        helpLabel="Create a bot and get a token from @BotFather"
        testKey="telegram"
        status={{
          ok: system.channels.telegram_configured,
          label: system.channels.telegram_configured ? "Configured" : "Not set up",
        }}
      >
        <div className="settings-info-row">
          <span className="settings-info-label">Bot token</span>
          <span style={{ fontSize: 13, fontWeight: 600 }}>
            {system.channels.telegram_configured ? "Set on the server" : "Not set"}
            {system.channels.telegram_configured && (
              <span className="settings-readonly-tag" style={{ marginLeft: 8 }}>Environment</span>
            )}
          </span>
        </div>
        <div className="settings-info-row">
          <span className="settings-info-label">Reports filed via Telegram</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 600 }}>{tgCount}</span>
        </div>
      </SetupCard>

      <SetupCard
        icon="💬"
        title="WhatsApp"
        subtitle="Optional — file reports over WhatsApp via Twilio"
        helpUrl="https://console.twilio.com/"
        helpLabel="Get your credentials from the Twilio Console"
        testKey="whatsapp"
        status={{
          ok: system.channels.whatsapp_configured,
          label: system.channels.whatsapp_configured ? "Configured" : "Not set up",
        }}
      >
        <div className="settings-info-row">
          <span className="settings-info-label">Twilio credentials</span>
          <span style={{ fontSize: 13, fontWeight: 600 }}>
            {system.channels.whatsapp_configured ? "Set on the server" : "Not set"}
          </span>
        </div>
        <div className="settings-info-row">
          <span className="settings-info-label">Reports filed via WhatsApp</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 600 }}>{waCount}</span>
        </div>
        <p className="setup-note">
          Known limitation: drafting and confirmation work, but the finished PDF isn&apos;t sent back over
          WhatsApp — Twilio can only attach media it can fetch from a public URL, and reports are stored locally.
        </p>
      </SetupCard>

      <form onSubmit={handleSave}>
        <SetupCard icon="🏢" title="Report Branding" subtitle="Shown as the issuing organisation on PDF reports">
          <label style={{ display: "block" }}>
            <span className="detail-field-label">Company / Consultancy Name</span>
            <input
              type="text"
              className="settings-input"
              value={companyName}
              placeholder="e.g. Carlink Consultancy"
              onChange={(e) => setCompanyName(e.target.value)}
            />
          </label>
          {error && <div className="setup-error" style={{ marginTop: 12 }}>{error}</div>}
        </SetupCard>

        <div className="setup-save-bar">
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            Keys are hidden by default — click 👁 to reveal what you&apos;re typing
          </span>
          {saved && <span style={{ fontSize: 12, color: "var(--badge-green-text)", fontWeight: 700 }}>✓ Saved</span>}
          <button type="submit" className="btn-primary-modern" disabled={saving || !dirty}>
            {saving ? "Saving…" : "💾 Save settings"}
          </button>
        </div>
      </form>
    </div>
  );
}
