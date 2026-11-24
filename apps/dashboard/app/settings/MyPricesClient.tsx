"use client";

import { useState } from "react";
import Link from "next/link";
import { Check, Pencil, Search, Tags, Trash2, X } from "lucide-react";
import type { MyPrice } from "@/lib/api";
import { filterMyPrices, makeModelOptions, parseUnitPrice, priceText, shortDate, vehicleKeyLabel } from "@/lib/prices";
import { deleteMyPriceAction, listMyPricesAction, updateMyPriceAction } from "./actions";

const selectStyle: React.CSSProperties = {
  padding: "6px 10px",
  borderRadius: 6,
  border: "1px solid var(--border-color)",
  background: "var(--surface-card)",
  color: "var(--text-primary)",
  fontSize: 12,
  outline: "none",
};

const keyOf = (p: MyPrice) => String(p.id);

/** Settings -> My prices (Stage 1): the person's own prices per unit, one
 * per make, model and line, from the W/shop Est. they typed on Annex A. A
 * table with search and a make / model filter; a price is changed in place
 * (PATCH /me/prices/{id}) or deleted after a confirm (DELETE, audited);
 * "old" after 12 months. Nobody else sees them; the firm's book changes only
 * when an admin approves a difference (Knowledge -> Price differences). */
export function MyPricesClient({ initial, initialError }: { initial: MyPrice[] | null; initialError: string | null }) {
  const [prices, setPrices] = useState<MyPrice[] | null>(initial);
  const [loadError, setLoadError] = useState<string | null>(initialError);
  const [loading, setLoading] = useState(false);

  const [q, setQ] = useState("");
  const [make, setMake] = useState("");
  const [model, setModel] = useState("");

  // One row at a time: being edited, or busy saving / deleting.
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ key: string; text: string } | null>(null);

  const all = prices ?? [];
  const { makes, models } = makeModelOptions(all, make);
  const rows = filterMyPrices(all, { q, make, model });

  const reload = async () => {
    setLoading(true);
    try {
      const res = await listMyPricesAction();
      if ("error" in res) setLoadError(res.error);
      else {
        setPrices(res.prices);
        setLoadError(null);
      }
    } catch {
      setLoadError("Couldn't reach the server. Check the connection and try again.");
    } finally {
      setLoading(false);
    }
  };

  const startEdit = (p: MyPrice) => {
    setEditing(keyOf(p));
    setEditText(String(p.unit_price));
    setRowError(null);
  };

  const cancelEdit = () => {
    setEditing(null);
    setRowError(null);
  };

  const saveEdit = async (p: MyPrice) => {
    const key = keyOf(p);
    const price = parseUnitPrice(editText);
    if (price == null) {
      setRowError({ key, text: "Type the price per unit, a number above 0." });
      return;
    }
    if (price === p.unit_price) {
      cancelEdit();
      return;
    }
    setBusy(key);
    setRowError(null);
    try {
      const res = await updateMyPriceAction(p.id, price);
      if ("error" in res) {
        setRowError({ key, text: res.error });
        return;
      }
      setEditing(null);
      if (res.price) {
        const saved = res.price;
        setPrices((prev) => (prev ?? []).map((x) => (keyOf(x) === key ? saved : x)));
      } else {
        await reload();
      }
    } catch {
      setRowError({ key, text: "Couldn't reach the server. Check the connection and try again." });
    } finally {
      setBusy(null);
    }
  };

  const remove = async (p: MyPrice) => {
    const key = keyOf(p);
    const what = [vehicleKeyLabel(p.make, p.model_key), p.wording].filter(Boolean).join(" · ");
    if (!confirm(`Delete your price for ${what} (${priceText(p.unit_price)} per unit)? Your next case of this car won't fill it in.`)) return;
    setBusy(key);
    setRowError(null);
    try {
      const res = await deleteMyPriceAction(p.id);
      if ("error" in res) {
        setRowError({ key, text: res.error });
        return;
      }
      setPrices((prev) => (prev ?? []).filter((x) => keyOf(x) !== key));
      if (editing === key) setEditing(null);
    } catch {
      setRowError({ key, text: "Couldn't reach the server. Check the connection and try again." });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="card-glass setup-card s1-my-prices">
      <div className="card-header" style={{ alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 320px" }}>
          <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Tags style={{ width: 16, height: 16, color: "var(--accent-primary)" }} /> My prices
          </div>
          <div className="card-subtitle">
            Your own prices per unit, by make and model. Only you see and use them. They fill in on your next case of the same car, marked &quot;Your price&quot;, and
            you confirm them before sign-off.
          </div>
        </div>
      </div>

      {loadError ? (
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <div className="setup-error" style={{ margin: 0 }}>
            {loadError}
          </div>
          <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} disabled={loading} onClick={() => void reload()}>
            {loading ? "Loading…" : "Try again"}
          </button>
        </div>
      ) : all.length === 0 ? (
        <div className="s1-empty">
          <Tags style={{ width: 28, height: 28, marginBottom: 8 }} aria-hidden="true" />
          <div className="s1-empty-title">No prices remembered yet</div>
          <p>
            When you type or change a price (W/shop Est.) on a case&apos;s Annex A and save, Carlink remembers it here as your price per unit for that make and model.
            Your next case of the same car fills it in for you, and you can undo it right after saving.
          </p>
          <p>Only you see these. The firm&apos;s price book changes only when an admin approves a difference.</p>
        </div>
      ) : (
        <>
          <div className="s1-filters">
            <div style={{ position: "relative", flex: "1 1 220px", maxWidth: 280 }}>
              <Search
                aria-hidden="true"
                style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", width: 13, height: 13, color: "var(--text-muted)" }}
              />
              <input
                type="search"
                className="settings-input"
                style={{ marginTop: 0, paddingLeft: 28, fontSize: 12 }}
                placeholder="Search part or car…"
                aria-label="Search your prices"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            </div>
            <select
              value={make}
              aria-label="Filter by make"
              style={selectStyle}
              onChange={(e) => {
                const next = e.target.value;
                setMake(next);
                // Keep the model only if that make has it.
                if (model && !all.some((p) => (!next || p.make.toLowerCase() === next.toLowerCase()) && p.model_key.toLowerCase() === model.toLowerCase())) setModel("");
              }}
            >
              <option value="">All makes</option>
              {makes.map((m) => (
                <option key={m} value={m}>
                  {vehicleKeyLabel(m, "")}
                </option>
              ))}
            </select>
            <select value={model} aria-label="Filter by model" style={selectStyle} onChange={(e) => setModel(e.target.value)}>
              <option value="">All models</option>
              {models.map((m) => (
                <option key={m} value={m}>
                  {vehicleKeyLabel("", m)}
                </option>
              ))}
            </select>
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              {rows.length} of {all.length} price{all.length === 1 ? "" : "s"}
            </span>
          </div>

          {rows.length === 0 ? (
            <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>No prices match these filters.</p>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table className="damage-table-modern s1-prices-table">
                <thead>
                  <tr>
                    <th>Car</th>
                    <th>Line</th>
                    <th style={{ textAlign: "right" }}>Price per unit</th>
                    <th>Last changed</th>
                    <th style={{ textAlign: "right" }} title="How many times it was saved or filled in">
                      Uses
                    </th>
                    <th aria-label="Actions" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((p) => {
                    const key = keyOf(p);
                    const isEditing = editing === key;
                    const isBusy = busy === key;
                    const what = [vehicleKeyLabel(p.make, p.model_key), p.wording].filter(Boolean).join(" · ");
                    return (
                      <tr key={key}>
                        <td style={{ whiteSpace: "nowrap" }}>{vehicleKeyLabel(p.make, p.model_key) || "—"}</td>
                        <td>
                          <strong>{p.wording || "—"}</strong>
                          {p.side && <span className="annex-suggest-tag">{p.side}</span>}
                          {p.old && (
                            <span className="s1-price-old" style={{ marginLeft: 6 }} title="Over 12 months old: it still fills in, but check it is still right.">
                              old
                            </span>
                          )}
                          {rowError?.key === key && (
                            <div className="setup-error" style={{ margin: "6px 0 0" }}>
                              {rowError.text}
                            </div>
                          )}
                        </td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", whiteSpace: "nowrap" }}>
                          {isEditing ? (
                            <form
                              className="s1-inline-edit"
                              onSubmit={(e) => {
                                e.preventDefault();
                                void saveEdit(p);
                              }}
                            >
                              <input
                                type="text"
                                inputMode="decimal"
                                className="annex-cell-input"
                                style={{ width: 90, textAlign: "right", fontFamily: "var(--font-mono)" }}
                                value={editText}
                                aria-label={`New price per unit for ${what}`}
                                aria-invalid={rowError?.key === key ? true : undefined}
                                disabled={isBusy}
                                autoFocus
                                onChange={(e) => setEditText(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === "Escape") cancelEdit();
                                }}
                              />
                              <button type="submit" className="s1-icon-btn" disabled={isBusy} aria-label={`Save the price for ${what}`} title="Save">
                                <Check style={{ width: 14, height: 14 }} />
                              </button>
                              <button type="button" className="s1-icon-btn" disabled={isBusy} onClick={cancelEdit} aria-label="Cancel" title="Cancel">
                                <X style={{ width: 14, height: 14 }} />
                              </button>
                            </form>
                          ) : (
                            <button
                              type="button"
                              className="s1-price-edit"
                              disabled={busy !== null}
                              onClick={() => startEdit(p)}
                              aria-label={`Change your price for ${what}, now ${priceText(p.unit_price)}`}
                              title="Change this price"
                            >
                              {priceText(p.unit_price)} <Pencil aria-hidden="true" style={{ width: 11, height: 11 }} />
                            </button>
                          )}
                        </td>
                        <td style={{ whiteSpace: "nowrap" }} suppressHydrationWarning>
                          {shortDate(p.updated_at) || "—"}
                          {p.last_case_id && (
                            <>
                              {" · "}
                              <Link href={`/reports/${encodeURIComponent(p.last_case_id)}?tab=annexA`} prefetch={false} className="setup-help-link">
                                last case
                              </Link>
                            </>
                          )}
                        </td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{p.uses || "—"}</td>
                        <td style={{ textAlign: "right" }}>
                          <button
                            type="button"
                            className="btn-secondary-modern"
                            style={{ fontSize: 11, padding: "4px 10px", color: "var(--danger, #ef4444)" }}
                            disabled={busy !== null}
                            onClick={() => void remove(p)}
                            aria-label={`Delete your price for ${what}`}
                          >
                            <Trash2 style={{ width: 12, height: 12 }} /> {isBusy && !isEditing ? "Deleting…" : "Delete"}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
