"use client";

import { useState } from "react";
import type { AppSettings, SystemInfo } from "@/lib/api";
import { updateSettingsAction } from "./actions";

type Tab = "general" | "ai" | "channels" | "access" | "system";

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: "general", label: "General", icon: "⚙️" },
  { id: "ai", label: "AI Engine", icon: "🤖" },
  { id: "channels", label: "Channels", icon: "🔌" },
  { id: "access", label: "Access Control", icon: "🔒" },
  { id: "system", label: "System", icon: "ℹ️" },
];

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = bytes / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

/** A labelled read-only row. Used throughout the read-only sections so
 * deploy-time config is visually distinct from the editable form fields
 * in General -- the old page styled hardcoded values as editable inputs
 * with a Save button that did nothing. */
function InfoRow({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="settings-info-row">
      <span className="settings-info-label">{label}</span>
      <span style={{ fontFamily: mono ? "var(--font-mono)" : undefined, fontSize: 13, fontWeight: 600 }}>{value}</span>
    </div>
  );
}

function StatusDot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, fontWeight: 600 }}>
      <span style={{ color: ok ? "var(--badge-green-text)" : "var(--text-muted)", fontSize: 15, lineHeight: 1 }}>●</span>
      {label}
    </span>
  );
}

export function SettingsClient({ settings, system }: { settings: AppSettings; system: SystemInfo }) {
  const [activeTab, setActiveTab] = useState<Tab>("general");
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

  return (
    <div>
      <div className="page-header" style={{ marginBottom: 20 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>Settings</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            Deployment configuration, connected channels, and system status
          </p>
        </div>
      </div>

      <div className="case-tab-bar">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`case-tab ${activeTab === tab.id ? "active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
          >
            <span>{tab.icon}</span> {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "general" && (
        <form onSubmit={handleSave} className="card-glass" style={{ maxWidth: 640 }}>
          <div className="card-header">
            <div>
              <div className="card-title">
                <span>🏢</span> Report Branding
              </div>
              <div className="card-subtitle">Shown as the issuing organisation on generated PDF reports</div>
            </div>
          </div>

          <label style={{ display: "block", marginBottom: 16 }}>
            <span className="detail-field-label">Company / Consultancy Name</span>
            <input
              type="text"
              className="settings-input"
              value={companyName}
              placeholder="e.g. Carlink Consultancy"
              onChange={(e) => setCompanyName(e.target.value)}
            />
          </label>

          {error && <p style={{ fontSize: 12, color: "var(--danger, #ef4444)", marginBottom: 12 }}>{error}</p>}

          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <button type="submit" className="btn-primary-modern" disabled={saving || !dirty}>
              {saving ? "Saving…" : "Save Changes"}
            </button>
            {saved && (
              <span style={{ fontSize: 12, color: "var(--badge-green-text)", fontWeight: 700 }}>✓ Saved</span>
            )}
            {dirty && !saving && !saved && (
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Unsaved changes</span>
            )}
          </div>
        </form>
      )}

      {activeTab === "ai" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="card-glass">
            <div className="card-header">
              <div>
                <div className="card-title">
                  <span>🧠</span> Model Fallback Chain
                </div>
                <div className="card-subtitle">
                  Tried in order — each model has its own quota, so hitting one limit doesn&apos;t stop drafting
                </div>
              </div>
              <span className="settings-readonly-tag">Read-only</span>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {system.ai.model_chain.map((model, i) => (
                <div key={model} className="model-chain-row">
                  <span className="model-chain-index">{i + 1}</span>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 700, flex: 1 }}>{model}</span>
                  {i === 0 && <span className="chip-severity minor" style={{ fontSize: 10 }}>Primary</span>}
                  {model.includes("lite") && (
                    <span className="chip-severity moderate" style={{ fontSize: 10 }} title="Reliably drafts text, but has not produced damage bounding boxes in testing">
                      No bounding boxes
                    </span>
                  )}
                </div>
              ))}
            </div>

            <p className="settings-hint">
              Set via the <code>GEMINI_MODEL_CHAIN</code> environment variable — changing it requires a redeploy,
              so it isn&apos;t editable here.
            </p>
          </div>

          <div className="card-glass" style={{ maxWidth: 640 }}>
            <div className="card-header">
              <div className="card-title">
                <span>⏱</span> Rate Limiting &amp; Timeouts
              </div>
              <span className="settings-readonly-tag">Read-only</span>
            </div>
            <InfoRow label="Minimum interval between AI calls" value={`${system.ai.min_call_interval_seconds}s`} mono />
            <InfoRow label="Request timeout per model" value={`${system.ai.request_timeout_seconds}s`} mono />
            <InfoRow
              label="API key"
              value={<StatusDot ok={system.ai.api_key_configured} label={system.ai.api_key_configured ? "Configured" : "Not configured"} />}
            />
            <p className="settings-hint">
              The interval is applied per process. The Telegram bot and the dashboard API run separately and share
              one Gemini key, so their limits are independent.
            </p>
          </div>
        </div>
      )}

      {activeTab === "channels" && (
        <div className="settings-split">
          <div className="card-glass">
            <div className="card-header">
              <div className="card-title">
                <span>✈️</span> Telegram
              </div>
              <StatusDot ok={system.channels.telegram_configured} label={system.channels.telegram_configured ? "Configured" : "Not configured"} />
            </div>
            <InfoRow label="Bot token" value={system.channels.telegram_configured ? "Set" : "Missing"} />
            <InfoRow label="Mode" value="Long-polling (no public webhook needed)" />
            <InfoRow label="Reports filed" value={system.channels.reports_by_channel.telegram ?? 0} mono />
          </div>

          <div className="card-glass">
            <div className="card-header">
              <div className="card-title">
                <span>💬</span> WhatsApp
              </div>
              <StatusDot ok={system.channels.whatsapp_configured} label={system.channels.whatsapp_configured ? "Configured" : "Not configured"} />
            </div>
            <InfoRow label="Twilio credentials" value={system.channels.whatsapp_configured ? "Set" : "Missing"} />
            <InfoRow label="Reports filed" value={system.channels.reports_by_channel.whatsapp ?? 0} mono />
            <p className="settings-hint">
              Known limitation: drafting and confirmation work, but the finished PDF isn&apos;t attached back over
              WhatsApp — Twilio can only send media it can fetch from a public URL, and generated PDFs are stored
              locally.
            </p>
          </div>

          <div className="card-glass">
            <div className="card-header">
              <div className="card-title">
                <span>🖥</span> Dashboard (manual entry)
              </div>
              <StatusDot ok label="Available" />
            </div>
            <InfoRow label="Reports filed" value={system.channels.reports_by_channel.manual ?? 0} mono />
          </div>
        </div>
      )}

      {activeTab === "access" && (
        <div className="card-glass settings-warning-card" style={{ maxWidth: 760 }}>
          <div className="card-header">
            <div className="card-title">
              <span>⚠️</span> No authentication configured
            </div>
          </div>
          <p style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.7, margin: "0 0 14px" }}>
            This dashboard has no login. Anyone who can reach its URL can view, edit, sign off and delete every
            case — including owner names, VINs, engine numbers, plate numbers, claim numbers and workshop details.
          </p>
          <p style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.7, margin: 0 }}>
            The role shown in the top bar (&ldquo;Surveyor / Loss Adjuster&rdquo;) is a fixed label describing the
            intended user of this tool. It is not an identity, an account, or a permission level, and nothing is
            checked against it.
          </p>
          <p className="settings-hint" style={{ marginTop: 16 }}>
            Adding real accounts and roles is a deliberate scope decision, not something this page can toggle.
          </p>
        </div>
      )}

      {activeTab === "system" && (
        <div className="settings-split">
          <div className="card-glass">
            <div className="card-header">
              <div className="card-title">
                <span>💾</span> Stored Data
              </div>
            </div>
            <InfoRow label="Reports" value={system.storage.reports} mono />
            <InfoRow label="Photos" value={system.storage.photos} mono />
            <InfoRow label="Generated PDFs" value={system.storage.pdfs} mono />
            <InfoRow label="Disk used" value={formatBytes(system.storage.bytes_used)} mono />
          </div>

          <div className="card-glass">
            <div className="card-header">
              <div className="card-title">
                <span>🗄</span> Infrastructure
              </div>
              <span className="settings-readonly-tag">Read-only</span>
            </div>
            <InfoRow label="Database engine" value={system.database.engine} mono />
            <InfoRow label="Storage directory" value={system.storage.storage_dir} mono />
            <InfoRow label="Authentication" value={<StatusDot ok={system.auth.configured} label={system.auth.configured ? "Enabled" : "None"} />} />
          </div>
        </div>
      )}
    </div>
  );
}
