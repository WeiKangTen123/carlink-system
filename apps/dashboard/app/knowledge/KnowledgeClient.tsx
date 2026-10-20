"use client";

import { useState, useEffect } from "react";
import {
  Wrench,
  Zap,
  Car,
  Boxes,
  Database,
  Plus,
  Download,
  Save,
  Search,
  CheckCircle2,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";
import type { Taxonomy } from "@/lib/api";
import {
  type FactoryPriceItem,
  FACTORY_LABOR_TARIFFS,
  INITIAL_FACTORY_PRICE_CATALOG,
} from "@/lib/factoryPricing";

export interface PartRow {
  part: string;
  zone: string | null;
  status: "mapped" | "no-mesh" | "undecided";
  uses: number;
}

type Tab = "factory_pricing" | "parts" | "damage" | "vehicles";

const TABS: { id: Tab; label: string; icon: React.ComponentType<{ style?: React.CSSProperties }> }[] = [
  { id: "factory_pricing", label: "Factory Price List (DB)", icon: Database },
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

export function KnowledgeClient({ taxonomy, parts }: { taxonomy: Taxonomy; parts: PartRow[] }) {
  const [tab, setTab] = useState<Tab>("factory_pricing");
  const [query, setQuery] = useState("");

  // Factory Price List State
  const [catalog, setCatalog] = useState<FactoryPriceItem[]>(INITIAL_FACTORY_PRICE_CATALOG);
  const [catalogFilterCategory, setCatalogFilterCategory] = useState<string>("all");
  const [catalogFilterTier, setCatalogFilterTier] = useState<string>("all");
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  // New Part Modal State
  const [showAddModal, setShowAddModal] = useState(false);
  const [newPart, setNewPart] = useState<Partial<FactoryPriceItem>>({
    part: "",
    sku: "",
    category: "Front",
    tier: "Tier 1 (Japanese)",
    baseOemPrice: 500,
    laborHours: 1.5,
    paintCost: 200,
    insurerDiscountPct: 20,
  });

  // Load persisted factory catalog from localStorage
  useEffect(() => {
    try {
      const saved = localStorage.getItem("carlink_factory_price_list");
      if (saved) {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed) && parsed.length > 0) {
          setCatalog(parsed);
        }
      }
    } catch {
      // Ignore read errors
    }
  }, []);

  const handleSaveCatalog = () => {
    try {
      localStorage.setItem("carlink_factory_price_list", JSON.stringify(catalog));
      setSaveMessage("Factory Price List saved & active across Case Repo!");
      setTimeout(() => setSaveMessage(null), 3500);
    } catch {
      // Ignore write errors
    }
  };

  const handleUpdateItem = (id: string, field: keyof FactoryPriceItem, value: any) => {
    setCatalog((prev) =>
      prev.map((item) => (item.id === id ? { ...item, [field]: value } : item))
    );
  };

  const handleDeleteItem = (id: string) => {
    setCatalog((prev) => prev.filter((item) => item.id !== id));
  };

  const handleAddNewPart = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newPart.part || !newPart.sku) return;

    const created: FactoryPriceItem = {
      id: `part-${Date.now()}`,
      part: newPart.part,
      sku: newPart.sku,
      category: (newPart.category as any) || "Front",
      tier: (newPart.tier as any) || "Tier 1 (Japanese)",
      baseOemPrice: Number(newPart.baseOemPrice) || 0,
      laborHours: Number(newPart.laborHours) || 0,
      paintCost: Number(newPart.paintCost) || 0,
      insurerDiscountPct: Number(newPart.insurerDiscountPct) || 0,
    };

    const nextCatalog = [created, ...catalog];
    setCatalog(nextCatalog);
    try {
      localStorage.setItem("carlink_factory_price_list", JSON.stringify(nextCatalog));
    } catch {}

    setShowAddModal(false);
    setNewPart({
      part: "",
      sku: "",
      category: "Front",
      tier: "Tier 1 (Japanese)",
      baseOemPrice: 500,
      laborHours: 1.5,
      paintCost: 200,
      insurerDiscountPct: 20,
    });
    setSaveMessage("New SKU added successfully!");
    setTimeout(() => setSaveMessage(null), 3000);
  };

  const handleExportCSV = () => {
    const headers = ["Canonical Part", "OEM SKU", "Category", "Tier", "Base OEM Price (SGD)", "R&R Labor Hours", "Paint Cost (SGD)", "Discount (%)", "Net Claimable (SGD)"];
    const rows = catalog.map((c) => {
      const net = (c.baseOemPrice * (1 - c.insurerDiscountPct / 100)).toFixed(2);
      return [
        `"${c.part}"`,
        `"${c.sku}"`,
        `"${c.category}"`,
        `"${c.tier}"`,
        c.baseOemPrice,
        c.laborHours,
        c.paintCost,
        c.insurerDiscountPct,
        net,
      ].join(",");
    });
    const csvContent = "data:text/csv;charset=utf-8," + [headers.join(","), ...rows].join("\n");
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `carlink_factory_master_price_list_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  // Filtered catalog
  const filteredCatalog = catalog.filter((item) => {
    if (query) {
      const q = query.toLowerCase();
      if (!item.part.toLowerCase().includes(q) && !item.sku.toLowerCase().includes(q)) return false;
    }
    if (catalogFilterCategory !== "all" && item.category !== catalogFilterCategory) return false;
    if (catalogFilterTier !== "all" && item.tier !== catalogFilterTier) return false;
    return true;
  });

  const q = query.toLowerCase().trim();
  const visibleParts = parts.filter((p) => !q || p.part.toLowerCase().includes(q) || (p.zone ?? "").includes(q));

  const mapped = parts.filter((p) => p.status === "mapped").length;
  const noMesh = parts.filter((p) => p.status === "no-mesh").length;
  const seen = parts.filter((p) => p.uses > 0).length;

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
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>Factory Master Knowledge Base</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            Living database of factory parts pricing, labor tariffs, paint rates, and AI classification vocabulary
          </p>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <span className="status-pill" style={{ background: "var(--badge-green-bg)", color: "var(--badge-green-text)", borderColor: "var(--badge-green-border)", fontSize: 11 }}>
            Living Factory Master Database
          </span>
        </div>
      </div>

      {/* KPI Ribbon */}
      <div className="kpi-grid-modern" style={{ marginBottom: 20 }}>
        <div className="kpi-card-glow">
          <div className="kpi-label">Factory Catalog SKUs</div>
          <div className="kpi-val" style={{ color: "var(--accent-cyan)" }}>{catalog.length}</div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>Master OEM Part Database</div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Body Labor Rate</div>
          <div className="kpi-val">${FACTORY_LABOR_TARIFFS.bodyLaborRateHourly.toFixed(2)}<span style={{ fontSize: 12 }}>/hr</span></div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>Panel Beating Standard</div>
        </div>
        <div className="kpi-card-glow">
          <div className="kpi-label">Spray Booth Cycle</div>
          <div className="kpi-val">${FACTORY_LABOR_TARIFFS.sprayBoothPerPanel.toFixed(2)}<span style={{ fontSize: 12 }}>/pnl</span></div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>2K Oven Baked Respray</div>
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

      {/* TAB 1: FACTORY MASTER PRICE LIST (DATABASE) */}
      {tab === "factory_pricing" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Banner & Controls Toolbar */}
          <div className="card-glass" style={{ padding: "14px 18px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <div style={{ position: "relative", width: 220 }}>
                  <Search style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", width: 13, height: 13, color: "var(--text-muted)" }} />
                  <input
                    type="text"
                    className="settings-input"
                    style={{ marginTop: 0, paddingLeft: 28, fontSize: 11 }}
                    placeholder="Search Part or SKU…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </div>

                <select
                  value={catalogFilterCategory}
                  onChange={(e) => setCatalogFilterCategory(e.target.value)}
                  style={{
                    padding: "6px 10px",
                    borderRadius: 6,
                    border: "1px solid var(--border-color)",
                    background: "var(--surface-card)",
                    color: "var(--text-primary)",
                    fontSize: 11,
                    outline: "none",
                  }}
                >
                  <option value="all">All Categories</option>
                  <option value="Front">Front</option>
                  <option value="Rear">Rear</option>
                  <option value="Side">Side</option>
                  <option value="Structure">Structure</option>
                  <option value="Glass">Glass</option>
                  <option value="Commercial">Commercial</option>
                </select>

                <select
                  value={catalogFilterTier}
                  onChange={(e) => setCatalogFilterTier(e.target.value)}
                  style={{
                    padding: "6px 10px",
                    borderRadius: 6,
                    border: "1px solid var(--border-color)",
                    background: "var(--surface-card)",
                    color: "var(--text-primary)",
                    fontSize: 11,
                    outline: "none",
                  }}
                >
                  <option value="all">All Tiers</option>
                  <option value="Tier 1 (Japanese)">Tier 1 (Japanese / Mass)</option>
                  <option value="Tier 2 (Continental)">Tier 2 (Continental / Luxury)</option>
                  <option value="Tier 3 (Commercial)">Tier 3 (Commercial / Heavy)</option>
                </select>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {saveMessage && (
                  <span style={{ fontSize: 11, fontWeight: 700, color: "var(--badge-green-text)", display: "flex", alignItems: "center", gap: 4 }}>
                    <CheckCircle2 style={{ width: 13, height: 13 }} /> {saveMessage}
                  </span>
                )}

                <button
                  type="button"
                  onClick={() => setShowAddModal(true)}
                  className="btn-secondary-modern"
                  style={{ fontSize: 11, padding: "6px 12px", display: "inline-flex", alignItems: "center", gap: 5 }}
                >
                  <Plus style={{ width: 13, height: 13 }} />
                  <span>Add SKU</span>
                </button>

                <button
                  type="button"
                  onClick={handleExportCSV}
                  className="btn-secondary-modern"
                  style={{ fontSize: 11, padding: "6px 12px", display: "inline-flex", alignItems: "center", gap: 5 }}
                  title="Export complete factory price list to CSV"
                >
                  <Download style={{ width: 13, height: 13 }} />
                  <span>Export CSV</span>
                </button>

                <button
                  type="button"
                  onClick={handleSaveCatalog}
                  className="btn-primary-modern"
                  style={{ fontSize: 11, padding: "6px 14px", display: "inline-flex", alignItems: "center", gap: 5 }}
                  title="Persist changes to factory master catalog"
                >
                  <Save style={{ width: 13, height: 13 }} />
                  <span>Save Changes</span>
                </button>
              </div>
            </div>
          </div>

          {/* Master Parts Table */}
          <div className="card-glass" style={{ padding: 0, overflow: "hidden" }}>
            <table className="damage-table-modern" style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ background: "var(--surface-hover)" }}>
                  <th style={{ padding: "10px 14px", textAlign: "left", fontSize: 11 }}>CANONICAL PART</th>
                  <th style={{ padding: "10px 14px", textAlign: "left", fontSize: 11 }}>OEM SKU</th>
                  <th style={{ padding: "10px 14px", textAlign: "left", fontSize: 11 }}>CATEGORY &amp; TIER</th>
                  <th style={{ padding: "10px 14px", textAlign: "right", fontSize: 11 }}>BASE OEM ($)</th>
                  <th style={{ padding: "10px 14px", textAlign: "right", fontSize: 11 }}>R&amp;R LABOR</th>
                  <th style={{ padding: "10px 14px", textAlign: "right", fontSize: 11 }}>PAINT ($)</th>
                  <th style={{ padding: "10px 14px", textAlign: "right", fontSize: 11 }}>DISC %</th>
                  <th style={{ padding: "10px 14px", textAlign: "right", fontSize: 11 }}>NET CLAIMABLE</th>
                  <th style={{ padding: "10px 14px", textAlign: "center", fontSize: 11 }}>ACTION</th>
                </tr>
              </thead>
              <tbody>
                {filteredCatalog.map((item) => {
                  const net = item.baseOemPrice * (1 - item.insurerDiscountPct / 100);
                  return (
                    <tr key={item.id} style={{ borderBottom: "1px solid var(--border-color)" }}>
                      <td style={{ padding: "9px 14px", fontWeight: 700, color: "var(--text-primary)" }}>
                        {item.part}
                      </td>
                      <td style={{ padding: "9px 14px", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--accent-cyan)" }}>
                        {item.sku}
                      </td>
                      <td style={{ padding: "9px 14px" }}>
                        <span className="chip-severity minor" style={{ fontSize: 9, marginRight: 5 }}>
                          {item.category}
                        </span>
                        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                          {item.tier.split(" ")[0]}
                        </span>
                      </td>
                      <td style={{ padding: "9px 14px", textAlign: "right" }}>
                        <input
                          type="number"
                          value={item.baseOemPrice}
                          onChange={(e) => handleUpdateItem(item.id, "baseOemPrice", Number(e.target.value) || 0)}
                          style={{
                            width: 85,
                            padding: "3px 6px",
                            textAlign: "right",
                            fontFamily: "var(--font-mono)",
                            fontSize: 11,
                            background: "var(--surface-base)",
                            border: "1px solid var(--border-color)",
                            borderRadius: 4,
                            color: "var(--text-primary)",
                          }}
                        />
                      </td>
                      <td style={{ padding: "9px 14px", textAlign: "right" }}>
                        <input
                          type="number"
                          step="0.1"
                          value={item.laborHours}
                          onChange={(e) => handleUpdateItem(item.id, "laborHours", Number(e.target.value) || 0)}
                          style={{
                            width: 55,
                            padding: "3px 6px",
                            textAlign: "right",
                            fontFamily: "var(--font-mono)",
                            fontSize: 11,
                            background: "var(--surface-base)",
                            border: "1px solid var(--border-color)",
                            borderRadius: 4,
                            color: "var(--text-primary)",
                          }}
                        />
                        <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 3 }}>h</span>
                      </td>
                      <td style={{ padding: "9px 14px", textAlign: "right" }}>
                        <input
                          type="number"
                          value={item.paintCost}
                          onChange={(e) => handleUpdateItem(item.id, "paintCost", Number(e.target.value) || 0)}
                          style={{
                            width: 70,
                            padding: "3px 6px",
                            textAlign: "right",
                            fontFamily: "var(--font-mono)",
                            fontSize: 11,
                            background: "var(--surface-base)",
                            border: "1px solid var(--border-color)",
                            borderRadius: 4,
                            color: "var(--text-primary)",
                          }}
                        />
                      </td>
                      <td style={{ padding: "9px 14px", textAlign: "right" }}>
                        <input
                          type="number"
                          value={item.insurerDiscountPct}
                          onChange={(e) => handleUpdateItem(item.id, "insurerDiscountPct", Number(e.target.value) || 0)}
                          style={{
                            width: 50,
                            padding: "3px 6px",
                            textAlign: "right",
                            fontFamily: "var(--font-mono)",
                            fontSize: 11,
                            background: "var(--surface-base)",
                            border: "1px solid var(--border-color)",
                            borderRadius: 4,
                            color: "var(--text-primary)",
                          }}
                        />
                        <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 2 }}>%</span>
                      </td>
                      <td style={{ padding: "9px 14px", textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 800, color: "var(--badge-green-text)", fontSize: 12 }}>
                        ${net.toFixed(2)}
                      </td>
                      <td style={{ padding: "9px 14px", textAlign: "center" }}>
                        <button
                          type="button"
                          onClick={() => handleDeleteItem(item.id)}
                          style={{
                            background: "none",
                            border: "none",
                            color: "var(--text-muted)",
                            cursor: "pointer",
                            padding: 4,
                          }}
                          title="Delete SKU"
                        >
                          <Trash2 style={{ width: 12, height: 12 }} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Add SKU Modal */}
          {showAddModal && (
            <div
              style={{
                position: "fixed",
                inset: 0,
                background: "rgba(0, 0, 0, 0.7)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                zIndex: 1000,
                padding: 16,
              }}
            >
              <div
                style={{
                  background: "var(--surface-card)",
                  border: "1px solid var(--border-color)",
                  borderRadius: 12,
                  padding: 24,
                  maxWidth: 480,
                  width: "100%",
                }}
              >
                <h3 style={{ margin: "0 0 16px", fontSize: 16, fontWeight: 800, color: "var(--text-primary)" }}>
                  Add New Factory Part SKU
                </h3>
                <form onSubmit={handleAddNewPart} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <div>
                    <label style={{ fontSize: 11, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
                      Part Name
                    </label>
                    <input
                      type="text"
                      required
                      value={newPart.part}
                      onChange={(e) => setNewPart({ ...newPart, part: e.target.value })}
                      placeholder="e.g. Front Bumper Fascia"
                      className="settings-input"
                      style={{ marginTop: 0 }}
                    />
                  </div>

                  <div>
                    <label style={{ fontSize: 11, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
                      OEM SKU Code
                    </label>
                    <input
                      type="text"
                      required
                      value={newPart.sku}
                      onChange={(e) => setNewPart({ ...newPart, sku: e.target.value })}
                      placeholder="e.g. 71101-T7A-J00ZA"
                      className="settings-input"
                      style={{ marginTop: 0, fontFamily: "var(--font-mono)" }}
                    />
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                    <div>
                      <label style={{ fontSize: 11, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
                        Category
                      </label>
                      <select
                        value={newPart.category}
                        onChange={(e) => setNewPart({ ...newPart, category: e.target.value as any })}
                        className="settings-input"
                        style={{ marginTop: 0 }}
                      >
                        <option value="Front">Front</option>
                        <option value="Rear">Rear</option>
                        <option value="Side">Side</option>
                        <option value="Structure">Structure</option>
                        <option value="Glass">Glass</option>
                        <option value="Commercial">Commercial</option>
                      </select>
                    </div>

                    <div>
                      <label style={{ fontSize: 11, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
                        Vehicle Tier
                      </label>
                      <select
                        value={newPart.tier}
                        onChange={(e) => setNewPart({ ...newPart, tier: e.target.value as any })}
                        className="settings-input"
                        style={{ marginTop: 0 }}
                      >
                        <option value="Tier 1 (Japanese)">Tier 1 (Japanese / Mass)</option>
                        <option value="Tier 2 (Continental)">Tier 2 (Continental / Luxury)</option>
                        <option value="Tier 3 (Commercial)">Tier 3 (Commercial / Heavy)</option>
                      </select>
                    </div>
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
                    <div>
                      <label style={{ fontSize: 11, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
                        Base OEM ($)
                      </label>
                      <input
                        type="number"
                        required
                        value={newPart.baseOemPrice}
                        onChange={(e) => setNewPart({ ...newPart, baseOemPrice: Number(e.target.value) || 0 })}
                        className="settings-input"
                        style={{ marginTop: 0 }}
                      />
                    </div>

                    <div>
                      <label style={{ fontSize: 11, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
                        Labor (hrs)
                      </label>
                      <input
                        type="number"
                        step="0.1"
                        required
                        value={newPart.laborHours}
                        onChange={(e) => setNewPart({ ...newPart, laborHours: Number(e.target.value) || 0 })}
                        className="settings-input"
                        style={{ marginTop: 0 }}
                      />
                    </div>

                    <div>
                      <label style={{ fontSize: 11, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
                        Paint ($)
                      </label>
                      <input
                        type="number"
                        value={newPart.paintCost}
                        onChange={(e) => setNewPart({ ...newPart, paintCost: Number(e.target.value) || 0 })}
                        className="settings-input"
                        style={{ marginTop: 0 }}
                      />
                    </div>
                  </div>

                  <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 12 }}>
                    <button
                      type="button"
                      onClick={() => setShowAddModal(false)}
                      className="btn-secondary-modern"
                      style={{ fontSize: 12, padding: "7px 14px" }}
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      className="btn-primary-modern"
                      style={{ fontSize: 12, padding: "7px 16px" }}
                    >
                      Add Part SKU
                    </button>
                  </div>
                </form>
              </div>
            </div>
          )}
        </div>
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
