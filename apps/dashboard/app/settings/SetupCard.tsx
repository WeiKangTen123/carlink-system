"use client";

import { useState } from "react";
import { Check, ExternalLink, X, Zap } from "lucide-react";
import { testServiceAction } from "./actions";

/** The card every Settings section sits in: icon, title, a one-line status
 * and, for a service, a Test button. Shared by all three tabs, and by the
 * cards later releases plug in (My AI key, My Telegram), so they all read
 * the same way. */

export type TestState = { ok: boolean; message: string } | null;

export function TestResult({ result }: { result: TestState }) {
  if (!result) return null;
  return (
    <span className={`setup-test-result ${result.ok ? "ok" : "fail"}`} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      {result.ok ? <Check style={{ width: 12, height: 12 }} /> : <X style={{ width: 12, height: 12 }} />} {result.message}
    </span>
  );
}

/** Where to actually GO to get the credential this card asks for --
 * shown as a real link, not just described in prose. */
export function HelpLink({ url, label }: { url: string; label: string }) {
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className="setup-help-link" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      {label} <ExternalLink style={{ width: 12, height: 12 }} />
    </a>
  );
}

export function SetupCard({
  id,
  icon,
  title,
  subtitle,
  helpUrl,
  helpLabel,
  testKey,
  status,
  children,
}: {
  /** An anchor, so the setup checklist and other pages can link here. */
  id?: string;
  icon: React.ReactNode;
  title: string;
  subtitle: string;
  helpUrl?: string;
  helpLabel?: string;
  /** The service POST /setup/test/{service} checks (admins only). */
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
    <div id={id} className="card-glass setup-card">
      <div className="setup-card-head">
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12, minWidth: 0 }}>
          <div className="setup-card-icon" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>{icon}</div>
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
            <button
              type="button"
              className="btn-secondary-modern"
              style={{ fontSize: 11, padding: "5px 12px", display: "inline-flex", alignItems: "center", gap: 4 }}
              onClick={runTest}
              disabled={testing}
            >
              <Zap style={{ width: 12, height: 12 }} /> {testing ? "Testing…" : "Test"}
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
