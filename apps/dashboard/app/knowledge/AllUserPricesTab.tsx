"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { BookCheck, RefreshCw, Search, Users } from "lucide-react";
import type { UserPriceRow } from "@/lib/api";
import {
  defaultSourceWord,
  filterUserPrices,
  parseUnitPrice,
  priceGapPct,
  priceText,
  promoteBody,
  shortDate,
  userOptions,
  userPriceCarOptions,
  vehicleKeyLabel,
} from "@/lib/prices";
import { getUserPricesAction, promoteUserPriceAction } from "./actions";

type Loaded = { rows: UserPriceRow[] | null; error: string | null };

const OFFLINE = "Couldn't reach the server. Check the connection and try again.";

const selectStyle: React.CSSProperties = {
  padding: "6px 10px",
  borderRadius: 6,
  border: "1px solid var(--border-color)",
  background: "var(--surface-card)",
  color: "var(--text-primary)",
  fontSize: 12,
  outline: "none",
};

/** Everyone's prices, or why they couldn't be loaded. */
async function fetchRows(): Promise<Loaded> {
  try {
    const res = await getUserPricesAction();
    return "error" in res ? { rows: null, error: res.error } : { rows: res.prices, error: null };
  } catch {
    return { rows: null, error: OFFLINE };
  }
}

const keyOf = (r: UserPriceRow) => String(r.id);

/** Knowledge -> "All users' prices" (Stage 1, the owner's final price
 * model; admins only -- the page leaves the tab out for anyone else and the
 * API refuses them). Prices are personal: each person's own price for a
 * line (GET /admin/user-prices, newest first, with their name), and beside
 * it the shared default everyone else gets -- an admin-promoted price, else
 * the book's median of 2+ imported cases. Filters: person, make / model,
 * search. [Use this price for everyone] opens an editable price (the
 * person's own to start), an optional reason and a confirm; it then becomes
 * the shared default for that make, model and line (POST /admin/
 * user-prices/{id}/promote, audited). Signing a case off never does. */
export function AllUserPricesTab() {
  const [loaded, setLoaded] = useState<Loaded>({ rows: null, error: null });
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState("");
  const [make, setMake] = useState("");
  const [model, setModel] = useState("");
  const [q, setQ] = useState("");
  // The row whose "Use for everyone" form is open, its price and reason.
  const [open, setOpen] = useState<string | null>(null);
  const [priceTyped, setPriceTyped] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  // A failed reload keeps the list on screen, with the error above it.
  const apply = useCallback((next: Loaded) => {
    setLoaded((prev) => (next.error && prev.rows ? { rows: prev.rows, error: next.error } : next));
    setLoading(false);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void fetchRows().then((next) => {
      if (!cancelled) apply(next);
    });
    return () => {
      cancelled = true;
    };
  }, [apply]);

  const load = async () => apply(await fetchRows());

  const reload = () => {
    setLoading(true);
    void load();
  };

  const all = useMemo(() => loaded.rows ?? [], [loaded.rows]);
  const people = useMemo(() => userOptions(all), [all]);
  const { makes, models } = useMemo(() => userPriceCarOptions(all, make), [all, make]);
  const shown = useMemo(() => filterUserPrices(all, { user, make, model, q }), [all, user, make, model, q]);

  const openForm = (r: UserPriceRow) => {
    setOpen(keyOf(r));
    setPriceTyped(String(r.unit_price));
    setReason("");
    setError(null);
  };

  const closeForm = () => {
    setOpen(null);
    setError(null);
  };

  const promote = async (r: UserPriceRow) => {
    const body = promoteBody(r, priceTyped, reason);
    if ("error" in body) {
      setError(body.error);
      return;
    }
    const price = body.unit_price ?? r.unit_price;
    const what = [vehicleKeyLabel(r.make, r.model_key), r.wording].filter(Boolean).join(" · ");
    if (!confirm(`Use ${priceText(price)} per unit for ${what} for everyone?\n\nEach person's own price stays theirs; everyone else gets this one.`)) return;
    setBusy(true);
    setError(null);
    setNote(null);
    let res: { row: UserPriceRow | null } | { error: string };
    try {
      res = await promoteUserPriceAction(r.id, body);
    } catch {
      res = { error: OFFLINE };
    }
    setBusy(false);
    if ("error" in res) {
      setError(res.error);
      return;
    }
    setOpen(null);
    setNote(`Everyone now gets ${priceText(price)} per unit for ${what}${r.user_name ? ` (from ${r.user_name}'s price)` : ""}.`);
    // Every row of that line shows the new default: the list again.
    setLoading(true);
    await load();
  };

  return (
    <div className="card-glass s1-diffs">
      <div className="card-header" style={{ alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 320px" }}>
          <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Users style={{ width: 16, height: 16, color: "var(--accent-primary)" }} /> All users&apos; prices
          </div>
          <div className="card-subtitle">
            Each person&apos;s own prices, per unit, beside the price everyone else gets. A person&apos;s price stays theirs; the shared price changes only when
            you use one for everyone here. Signing a case off never changes it.
          </div>
        </div>
        <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} disabled={loading || busy} onClick={reload}>
          <RefreshCw style={{ width: 13, height: 13 }} /> {loading ? "Loading…" : "Reload"}
        </button>
      </div>

      {note && (
        <div className="s1-note" role="status">
          <BookCheck aria-hidden="true" style={{ width: 14, height: 14, flexShrink: 0 }} /> {note}
        </div>
      )}
      {loaded.error && (
        <div className="setup-error" style={{ marginBottom: 10 }}>
          {loaded.error}
        </div>
      )}

      {loaded.rows === null ? (
        loading ? <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>Loading…</p> : null
      ) : all.length === 0 ? (
        <div className="s1-empty">
          <Users style={{ width: 28, height: 28, marginBottom: 8 }} aria-hidden="true" />
          <div className="s1-empty-title">Nobody has a price of their own yet</div>
          <p>When someone types a price (W/shop Est.) on a case&apos;s Annex A and saves, it is kept as theirs and shows here.</p>
        </div>
      ) : (
        <>
          <div className="s1-filters">
            <select value={user} aria-label="Filter by person" style={selectStyle} onChange={(e) => setUser(e.target.value)}>
              <option value="">Everyone</option>
              {people.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <select
              value={make}
              aria-label="Filter by make"
              style={selectStyle}
              onChange={(e) => {
                const next = e.target.value;
                setMake(next);
                // Keep the model only if that make has it.
                if (model && !all.some((r) => (!next || r.make.toLowerCase() === next.toLowerCase()) && r.model_key.toLowerCase() === model.toLowerCase())) setModel("");
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
            <div style={{ position: "relative", flex: "1 1 220px", maxWidth: 280 }}>
              <Search
                aria-hidden="true"
                style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", width: 13, height: 13, color: "var(--text-muted)" }}
              />
              <input
                type="search"
                className="settings-input"
                style={{ marginTop: 0, paddingLeft: 28, fontSize: 12 }}
                placeholder="Search part, car or person…"
                aria-label="Search everyone's prices"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            </div>
          </div>

          {shown.length === 0 ? (
            <p style={{ fontSize: 13, color: "var(--text-muted)", margin: "8px 0 0" }}>No prices match these filters.</p>
          ) : (
            <ul className="s1-diff-list">
              {shown.map((r) => {
                const key = keyOf(r);
                const car = vehicleKeyLabel(r.make, r.model_key);
                const gap = priceGapPct(r);
                const isOpen = open === key;
                const typedPrice = isOpen ? parseUnitPrice(priceTyped) : null;
                const updated = shortDate(r.updated_at);
                return (
                  <li key={key} className="s1-diff-row">
                    <div className="s1-diff-head">
                      <div style={{ minWidth: 0 }}>
                        <div className="s1-diff-what">
                          <strong>{r.wording}</strong>
                          {r.part && r.part.toLowerCase() !== r.wording.toLowerCase() && <span className="s1-diff-car">{r.part}</span>}
                          {car && <span className="s1-diff-car">{car}</span>}
                        </div>
                        <div className="s1-diff-facts">
                          <span>
                            <strong>{r.user_name || "A removed account"}</strong>: {priceText(r.unit_price)} per unit
                          </span>
                          <span>
                            {defaultSourceWord(r.default_source)}
                            {r.default_price != null ? (
                              <>
                                : <strong>{priceText(r.default_price)}</strong>
                                {gap ? ` (theirs ${gap})` : ""}
                              </>
                            ) : (
                              ""
                            )}
                          </span>
                          <span>
                            used {r.uses} time{r.uses === 1 ? "" : "s"}
                            {updated ? <span suppressHydrationWarning> · last set {updated}</span> : null}
                          </span>
                        </div>
                      </div>
                    </div>
                    {isOpen ? (
                      <div className="s1-diff-actions">
                        <label className="s1-diff-price">
                          <span>Price per unit for everyone</span>
                          <input
                            type="text"
                            inputMode="decimal"
                            className="annex-cell-input"
                            value={priceTyped}
                            disabled={busy}
                            aria-label={`Price per unit for everyone for ${car} ${r.wording}`}
                            aria-invalid={priceTyped !== "" && typedPrice == null ? true : undefined}
                            onChange={(e) => setPriceTyped(e.target.value)}
                          />
                        </label>
                        <label className="s1-diff-reason">
                          <span>Reason (optional)</span>
                          <input
                            type="text"
                            className="annex-cell-input"
                            maxLength={500}
                            value={reason}
                            disabled={busy}
                            placeholder="e.g. new supplier price"
                            onChange={(e) => setReason(e.target.value)}
                          />
                        </label>
                        <div className="s1-diff-buttons">
                          <button
                            type="button"
                            className="btn-primary-modern"
                            style={{ fontSize: 11, padding: "6px 12px" }}
                            disabled={busy || typedPrice == null}
                            onClick={() => void promote(r)}
                          >
                            {busy ? "Saving…" : typedPrice != null ? `Use ${priceText(typedPrice)} for everyone` : "Use for everyone"}
                          </button>
                          <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "6px 12px" }} disabled={busy} onClick={closeForm}>
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="s1-diff-actions">
                        <div className="s1-diff-buttons">
                          <button
                            type="button"
                            className="btn-secondary-modern"
                            style={{ fontSize: 11, padding: "6px 12px" }}
                            disabled={busy || open !== null}
                            onClick={() => openForm(r)}
                          >
                            Use this price for everyone
                          </button>
                        </div>
                      </div>
                    )}
                    {isOpen && error && (
                      <div className="setup-error" style={{ margin: "8px 0 0" }}>
                        {error}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
