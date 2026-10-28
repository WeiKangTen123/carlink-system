"use client";

import { useEffect, useState } from "react";
import { Bot, Eye, EyeOff, Key, Lock } from "lucide-react";
import type { LlmKey } from "@/lib/api";
import { addLlmKeyAction, deleteLlmKeyAction, listLlmKeysAction } from "./actions";
import { SetupCard } from "./SetupCard";

/** Settings -> Company: the company's own Gemini keys (/setup/llm-keys).
 * Called "Company AI key (backup)" on screen, the pair of each person's
 * "My AI key" on My settings: the AI runs on these for admins and for
 * accounts an admin has allowed the company AI key. Only the last 4
 * characters ever come back from the API. */
export function CompanyAiKeysCard({ envKeyConfigured }: { envKeyConfigured: boolean }) {
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
      id="company-ai-key"
      icon={<Bot style={{ width: 22, height: 22, color: "var(--accent-primary)" }} />}
      title="Company AI key (backup)"
      subtitle="Gemini reads incident photos for admins and for people allowed the company AI key"
      helpUrl="https://aistudio.google.com/apikey"
      helpLabel="Get a Gemini API key at Google AI Studio"
      testKey="llm"
      status={{ ok: hasAny, label: hasAny ? "Configured" : "Not set up" }}
    >
      <p className="setup-note">
        Add more than one key for extra headroom — when one key&apos;s quota runs out, the next is used
        automatically. Nothing else to configure. Who may use these is the &quot;May use the company AI key&quot; tick
        on Users.
      </p>

      {error && <div className="setup-error">{error}</div>}

      {keys === null ? (
        <p className="setup-note" style={{ marginTop: 0 }}>Loading keys…</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 14 }}>
          {keys.map((k) => (
            <div key={k.id} className="setup-key-row">
              <Key style={{ width: 15, height: 15, color: "var(--accent-cyan)" }} />
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
              <Lock style={{ width: 15, height: 15, color: "var(--text-muted)" }} />
              <span style={{ fontSize: 12 }}>Using the key set on the server</span>
              <span className="settings-readonly-tag" style={{ marginLeft: "auto" }}>Environment</span>
            </div>
          )}

          {keys.length === 0 && !envKeyConfigured && (
            <p className="setup-note" style={{ marginTop: 0, color: "var(--badge-amber-text)" }}>
              No company key configured — photo analysis fails for anyone relying on it until one is added.
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
            aria-label={showKey ? "Hide API key" : "Reveal API key"}
            title={showKey ? "Hide" : "Reveal"}
            style={{ display: "flex", alignItems: "center", justifyContent: "center" }}
          >
            {showKey ? <EyeOff style={{ width: 14, height: 14 }} /> : <Eye style={{ width: 14, height: 14 }} />}
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
      <p className="setup-note" style={{ margin: "10px 0 0" }}>
        Keys are hidden while you type; the eye shows what you&apos;ve pasted.
      </p>
    </SetupCard>
  );
}
