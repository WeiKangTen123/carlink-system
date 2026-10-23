"use client";

import { useState } from "react";
import { Wrench, Zap, Car, Boxes, History } from "lucide-react";
import type { PartsHistory, Taxonomy } from "@/lib/api";
import { getPartsHistoryAction } from "./actions";
import { PartsHistoryTab } from "./PartsHistoryTab";

export interface PartRow {
  part: string;
  zone: string | null;
  status: "mapped" | "no-mesh" | "undecided";
  uses: number;
}

// "Parts price history" replaced an "Indicative Price List" tab: about 20
// made-up SKUs, prices and labour/spray rates seeded from a code constant
// and edited in this browser's localStorage. Prices now come only from the
// firm's own past reports.
type Tab = "price_history" | "parts" | "damage" | "vehicles";

const TABS: { id: Tab; label: string; icon: React.ComponentType<{ style?: React.CSSProperties }> }[] = [
  { id: "price_history", label: "Parts price history", icon: History },
  { id: "parts", label: "Vehicle Parts & 3D", icon: Wrench },
  { id: "damage", label: "Damage Types", icon: Zap },
  { id: "vehicles", label: "Vehicle Catalogue", icon: Car },
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

export function KnowledgeClient({
  taxonomy,
  parts,
  history: initialHistory,
  historyError: initialHistoryError,
}: {
  taxonomy: Taxonomy;
  parts: PartRow[];
  /** Imported past reports and usual prices; null when they couldn't be
   * loaded, with historyError saying why. */
  history: PartsHistory | null;
  historyError: string | null;
}) {
  const [tab, setTab] = useState<Tab>("price_history");
  const [query, setQuery] = useState("");

  // Loaded by the page, and again here after an import or a removal.
  const [history, setHistory] = useState<PartsHistory | null>(initialHistory);
  const [historyError, setHistoryError] = useState<string | null>(initialHistoryError);

  async function reloadHistory() {
    const res = await getPartsHistoryAction();
    if ("error" in res) {
      setHistoryError(res.error);
      return;
    }
    setHistory(res);
    setHistoryError(null);
  }

  const q = query.toLowerCase().trim();
  const visibleParts = parts.filter((p) => !q || p.part.toLowerCase().includes(q) || (p.zone ?? "").includes(q));

  const mapped = parts.filter((p) => p.status === "mapped").length;
  const noMesh = parts.filter((p) => p.status === "no-mesh").length;
  const seen = parts.filter((p) => p.uses > 0).length;
  const pricedLines = history ? history.lines.filter((l) => l.usual_unit_price != null).length : 0;

  const byBody = new Map<string, string[]>();
  for (const [fragment, body] of Object.entries(taxonomy.model_body_types)) {
    const list = byBody.get(body) ?? [];
    list.push(fragment);
    byBody.set(body, list);
  }

  return (
    <div>
      {/* Page Header */}
      <div className="page-header" style={{ marginBottom: 16 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>Knowledge Base</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            Parts and usual prices from the firm&apos;s past reports, plus the AI classification vocabulary
          </p>
        </div>
      </div>

      {/* KPI Ribbon */}
      <div className="kpi-grid-modern" style={{ marginBottom: 20 }}>
        <div className="kpi-card-glow">
          <div className="kpi-label">Imported Past Reports</div>
          <div className="kpi-val" style={{ color: "var(--accent-cyan)" }}>{history ? history.cases.length : "—"}</div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
            {history ? (history.cases.length ? "Report spreadsheets on file" : "None imported yet") : "Couldn't load"}
          </div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Lines With A Usual Price</div>
          <div className="kpi-val">{history ? pricedLines : "—"}</div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
            {history ? `Of ${history.lines.length} line wordings, per make and model` : "Couldn't load"}
          </div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Parts in 3D Vocabulary</div>
          <div className="kpi-val">{parts.length}</div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>{mapped} mapped to 3D mesh</div>
        </div>
      </div>

      {/* Primary Tab Bar */}
      <div className="case-tab-bar" style={{ marginBottom: 16 }}>
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button key={t.id} type="button" className={`case-tab ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)}>
              <Icon style={{ width: 14, height: 14 }} /> {t.label}
            </button>
          );
        })}
      </div>

      {/* TAB 1: PARTS PRICE HISTORY (imported past reports) */}
      {tab === "price_history" && (
        <PartsHistoryTab history={history} loadError={historyError} onReload={reloadHistory} />
      )}

      {/* TAB 2: VEHICLE PARTS & 3D */}
      {tab === "parts" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="card-glass">
            <div className="card-header">
              <div>
                <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Wrench style={{ width: 16, height: 16, color: "var(--accent-primary)" }} /> Vehicle Parts
                </div>
                <div className="card-subtitle">
                  {visibleParts.length} of {parts.length} shown — “3D zone” means damage there highlights on the blueprint
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
        </div>
      )}

      {/* TAB 3: DAMAGE TYPES */}
      {tab === "damage" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="card-glass">
            <div className="card-header">
              <div>
                <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Zap style={{ width: 16, height: 16, color: "var(--accent-primary)" }} /> Damage Types
                </div>
                <div className="card-subtitle">
                  The closed list of damage classifications the AI assigns to parts
                </div>
              </div>
              <span className="chip-severity minor" style={{ fontSize: 10 }}>
                {taxonomy.damage_types.length} types
              </span>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {taxonomy.damage_types.map((dt) => (
                <span key={dt} className="chip-severity minor" style={{ fontSize: 11 }}>
                  {dt}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* TAB 4: VEHICLE CATALOGUE */}
      {tab === "vehicles" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="card-glass">
            <div className="card-header">
              <div>
                <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Car style={{ width: 16, height: 16, color: "var(--accent-primary)" }} /> Vehicle Catalogue
                </div>
                <div className="card-subtitle">
                  Decides which 3D model a case renders — matched on make and model text
                </div>
              </div>
              <span className="chip-severity minor" style={{ fontSize: 10 }}>
                {Object.keys(taxonomy.model_body_types).length} rules
              </span>
            </div>
            <p className="setup-note" style={{ marginTop: 0 }}>
              Only a sedan model and a procedural van silhouette exist as 3D assets, so Van and MPV render the van; everything else renders the sedan. A vehicle matching no rule falls back to the sedan rather than guessing.
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
              <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Boxes style={{ width: 16, height: 16, color: "var(--accent-primary)" }} /> Body Types
              </div>
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
