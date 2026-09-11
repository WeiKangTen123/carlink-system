"use client";

import { useState } from "react";
import type { Taxonomy } from "@/lib/api";

export interface PartRow {
  part: string;
  zone: string | null;
  status: "mapped" | "no-mesh" | "undecided";
  uses: number;
}

type Tab = "parts" | "damage" | "vehicles";

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: "parts", label: "Vehicle Parts", icon: "🔧" },
  { id: "damage", label: "Damage Types", icon: "⚡" },
  { id: "vehicles", label: "Vehicle Catalogue", icon: "🚘" },
];

const STATUS_META: Record<PartRow["status"], { label: string; cls: string; title: string }> = {
  mapped: { label: "3D zone", cls: "minor", title: "Highlights on the 3D blueprint" },
  "no-mesh": {
    label: "no mesh",
    cls: "unrated",
    title: "Real part, but the vehicle model has no geometry for it — still listed in the damage checklist, just without a marker",
  },
  undecided: { label: "unmapped", cls: "moderate", title: "No zone decision recorded — run check_taxonomy_sync.py" },
};

export function KnowledgeClient({ taxonomy, parts }: { taxonomy: Taxonomy; parts: PartRow[] }) {
  const [tab, setTab] = useState<Tab>("parts");
  const [query, setQuery] = useState("");

  const q = query.toLowerCase().trim();
  const visibleParts = parts.filter((p) => !q || p.part.toLowerCase().includes(q) || (p.zone ?? "").includes(q));

  const mapped = parts.filter((p) => p.status === "mapped").length;
  const noMesh = parts.filter((p) => p.status === "no-mesh").length;
  const seen = parts.filter((p) => p.uses > 0).length;
  const legacy = Object.entries(taxonomy.usage.legacy_terms).sort((a, b) => b[1] - a[1]);

  // Group the make/model rules by the body type they resolve to — the
  // catalogue's job is "which 3D model do we show", so that's the useful axis.
  const byBody = new Map<string, string[]>();
  for (const [fragment, body] of Object.entries(taxonomy.model_body_types)) {
    const list = byBody.get(body) ?? [];
    list.push(fragment);
    byBody.set(body, list);
  }

  return (
    <div>
      <div className="page-header" style={{ marginBottom: 20 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>Knowledge Base</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            The fixed vocabulary the AI must choose from when assessing damage
          </p>
        </div>
        <span className="settings-readonly-tag">Read-only</span>
      </div>

      <div className="analytics-lowvolume-note" style={{ marginBottom: 20 }}>
        These lists are supplied to the AI as an enforced vocabulary, so extracted damage uses these exact
        terms instead of free text. They&apos;re defined in <code>taxonomy.py</code> and change by deployment —
        which is why this page shows them rather than letting them be edited here.
      </div>

      <div className="kpi-grid-modern" style={{ marginBottom: 24 }}>
        <div className="kpi-card-glow">
          <div className="kpi-label">Parts in Vocabulary</div>
          <div className="kpi-val">{parts.length}</div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Shown on 3D Model</div>
          <div className="kpi-val" style={{ color: "var(--accent-cyan)" }}>{mapped}</div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>{noMesh} have no mesh</div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Seen in Real Cases</div>
          <div className="kpi-val" style={{ color: "var(--badge-green-text)" }}>{seen}</div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
            across {taxonomy.usage.reports_counted} report{taxonomy.usage.reports_counted === 1 ? "" : "s"}
          </div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Damage Types</div>
          <div className="kpi-val">{taxonomy.damage_types.length}</div>
        </div>
      </div>

      <div className="case-tab-bar">
        {TABS.map((t) => (
          <button key={t.id} type="button" className={`case-tab ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)}>
            <span>{t.icon}</span> {t.label}
          </button>
        ))}
      </div>

      {tab === "parts" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="card-glass">
            <div className="card-header">
              <div>
                <div className="card-title"><span>🔧</span> Vehicle Parts</div>
                <div className="card-subtitle">
                  {visibleParts.length} of {parts.length} shown &mdash; &ldquo;3D zone&rdquo; means damage there
                  highlights on the blueprint
                </div>
              </div>
              <input
                type="text"
                className="settings-input"
                style={{ marginTop: 0, width: 220 }}
                placeholder="Filter parts…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Filter vehicle parts"
              />
            </div>

            {visibleParts.length === 0 ? (
              <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>No parts match “{query}”.</p>
            ) : (
              <table className="damage-table-modern">
                <thead>
                  <tr>
                    <th>Canonical Part Name</th>
                    <th>3D Blueprint</th>
                    <th style={{ textAlign: "right" }}>Used in Cases</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleParts.map((p) => {
                    const meta = STATUS_META[p.status];
                    return (
                      <tr key={p.part}>
                        <td><strong>{p.part}</strong></td>
                        <td>
                          <span className={`chip-severity ${meta.cls}`} style={{ fontSize: 10 }} title={meta.title}>
                            {meta.label}
                          </span>
                          {p.zone && (
                            <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)", marginLeft: 8 }}>
                              {p.zone}
                            </span>
                          )}
                        </td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 12, color: p.uses ? "var(--text-primary)" : "var(--text-muted)" }}>
                          {p.uses || "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>

          {legacy.length > 0 && (
            <div className="card-glass">
              <div className="card-header">
                <div>
                  <div className="card-title"><span>🗂</span> Legacy Terms Still in the Data</div>
                  <div className="card-subtitle">
                    Free-text part names from reports filed before the vocabulary existed
                  </div>
                </div>
                <span className="chip-severity moderate" style={{ fontSize: 10 }}>{legacy.length} terms</span>
              </div>
              <p className="setup-note">
                These still resolve to 3D zones through the older pattern-matching rules, so those cases keep
                rendering correctly. They aren&apos;t rewritten, because editing stored assessments to match a
                later vocabulary would change what an assessor actually recorded.
              </p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {legacy.map(([term, n]) => (
                  <span key={term} className="chip-severity unrated" style={{ fontSize: 10 }}>
                    {term}{n > 1 ? ` ×${n}` : ""}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {tab === "damage" && (
        <div className="case-file-split">
          <div className="card-glass">
            <div className="card-header">
              <div>
                <div className="card-title"><span>⚡</span> Damage Types</div>
                <div className="card-subtitle">Compound damage picks the dominant type; nuance goes in the description</div>
              </div>
            </div>
            <table className="mini-table">
              <tbody>
                {taxonomy.damage_types.map((t) => {
                  const n = taxonomy.usage.damage_types[t] ?? 0;
                  return (
                    <tr key={t}>
                      <td style={{ fontWeight: 600 }}>{t}</td>
                      <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", color: n ? "var(--text-primary)" : "var(--text-muted)" }}>
                        {n || "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="card-glass">
            <div className="card-header">
              <div className="card-title"><span>📊</span> Severity Levels</div>
            </div>
            <p className="setup-note" style={{ marginTop: 0 }}>
              Severity may be left unset when the evidence doesn&apos;t support a judgement — an unrated part
              shows as neutral rather than being assumed Minor.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {taxonomy.severities.map((s) => (
                <div key={s} style={{ display: "flex", alignItems: "center", gap: 9 }}>
                  <span
                    style={{
                      width: 10, height: 10, borderRadius: 3, flexShrink: 0,
                      background:
                        s === "Severe" ? "var(--chart-severe)" : s === "Moderate" ? "var(--chart-moderate)" : "var(--chart-minor)",
                    }}
                  />
                  <span style={{ fontSize: 13, fontWeight: 600 }}>{s}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {tab === "vehicles" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="card-glass">
            <div className="card-header">
              <div>
                <div className="card-title"><span>🚘</span> Vehicle Catalogue</div>
                <div className="card-subtitle">
                  Decides which 3D model a case renders &mdash; matched on make and model text
                </div>
              </div>
              <span className="chip-severity minor" style={{ fontSize: 10 }}>
                {Object.keys(taxonomy.model_body_types).length} rules
              </span>
            </div>
            <p className="setup-note" style={{ marginTop: 0 }}>
              Only a sedan model and a procedural van silhouette exist as 3D assets, so Van and MPV render the
              van; everything else renders the sedan. A vehicle matching no rule falls back to the sedan rather
              than guessing.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {[...byBody.entries()].sort().map(([body, fragments]) => (
                <div key={body}>
                  <div className="detail-field-label" style={{ marginBottom: 6 }}>
                    {body} <span style={{ color: "var(--text-muted)" }}>({fragments.length})</span>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {fragments.sort().map((f) => (
                      <span key={f} className="chip-severity minor" style={{ fontSize: 10, fontFamily: "var(--font-mono)" }}>
                        {f}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="card-glass">
            <div className="card-header">
              <div className="card-title"><span>🧩</span> Body Types</div>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {taxonomy.body_types.map((b) => (
                <span key={b} className="chip-severity unrated" style={{ fontSize: 10 }}>{b}</span>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
