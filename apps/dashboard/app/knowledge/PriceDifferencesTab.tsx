"use client";

import { useCallback, useEffect, useState } from "react";
import { BookCheck, RefreshCw, Scale } from "lucide-react";
import type { PriceDifference } from "@/lib/api";
import { decisionBody, differenceKey, differencePct, parseUnitPrice, priceText, shortDate, vehicleKeyLabel } from "@/lib/prices";
import { decidePriceDifferenceAction, getPriceDifferencesAction } from "./actions";

type Loaded = { groups: PriceDifference[] | null; error: string | null };

const OFFLINE = "Couldn't reach the server. Check the connection and try again.";

/** The list, or why it couldn't be loaded. */
async function fetchDifferences(): Promise<Loaded> {
  try {
    const res = await getPriceDifferencesAction();
    return "error" in res ? { groups: null, error: res.error } : { groups: res.groups, error: null };
  } catch {
    return { groups: null, error: OFFLINE };
  }
}

/** Knowledge -> "Price differences" (Stage 1, admins only; the page leaves
 * the tab out for anyone else and the API refuses them): the lines where
 * people's own prices differ from the book's by more than 10%
 * (GET /admin/price-differences) -- counts only, never who. Each line takes
 * a price, prefilled with the people's median, and [Update book to $X] or
 * [Keep book price], with an optional reason
 * (POST /admin/price-differences/decide); the list is then loaded again.
 * The shared book changes only here. */
export function PriceDifferencesTab() {
  const [loaded, setLoaded] = useState<Loaded>({ groups: null, error: null });
  const [loading, setLoading] = useState(true);
  // Per line (differenceKey): the price typed, the reason, an error.
  const [priceTyped, setPriceTyped] = useState<Record<string, string>>({});
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  // A failed reload keeps the list on screen, with the error above it.
  const apply = useCallback((next: Loaded) => {
    setLoaded((prev) => (next.error && prev.groups ? { groups: prev.groups, error: next.error } : next));
    setLoading(false);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void fetchDifferences().then((next) => {
      if (!cancelled) apply(next);
    });
    return () => {
      cancelled = true;
    };
  }, [apply]);

  const load = async () => apply(await fetchDifferences());

  const reload = () => {
    setLoading(true);
    void load();
  };

  const decide = async (g: PriceDifference, decision: "update" | "keep") => {
    const key = differenceKey(g);
    const typed = priceTyped[key] ?? (g.median_user_price != null ? String(g.median_user_price) : "");
    const body = decisionBody(g, decision, typed, reasons[key] ?? "");
    if ("error" in body) {
      const said = body.error;
      setErrors((prev) => ({ ...prev, [key]: said }));
      return;
    }
    setBusy(key);
    setErrors((prev) => ({ ...prev, [key]: "" }));
    setNote(null);
    let res: { ok: true } | { error: string };
    try {
      res = await decidePriceDifferenceAction(body);
    } catch {
      res = { error: OFFLINE };
    }
    setBusy(null);
    if ("error" in res) {
      const said = res.error;
      setErrors((prev) => ({ ...prev, [key]: said }));
      return;
    }
    const what = [vehicleKeyLabel(g.make, g.model_key), g.wording].filter(Boolean).join(" · ");
    setNote(decision === "update" ? `Book updated: ${what} is now ${priceText(body.unit_price)} per unit.` : `Kept the book price for ${what}.`);
    setLoading(true);
    await load();
  };

  const groups = loaded.groups ?? [];

  return (
    <div className="card-glass s1-diffs">
      <div className="card-header" style={{ alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 320px" }}>
          <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Scale style={{ width: 16, height: 16, color: "var(--accent-primary)" }} /> Price differences
          </div>
          <div className="card-subtitle">
            Lines where people&apos;s own prices differ from the price book by more than 10%. Each person&apos;s price stays theirs; the book everyone gets changes only
            when you update it here. Counts only, no names.
          </div>
        </div>
        <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} disabled={loading || busy !== null} onClick={reload}>
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

      {loaded.groups === null ? (
        loading ? <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>Loading…</p> : null
      ) : groups.length === 0 ? (
        <div className="s1-empty">
          <Scale style={{ width: 28, height: 28, marginBottom: 8 }} aria-hidden="true" />
          <div className="s1-empty-title">No price differences right now</div>
          <p>When people&apos;s own prices for a line differ from the book&apos;s by more than 10%, the line shows here for an admin to decide.</p>
        </div>
      ) : (
        <ul className="s1-diff-list">
          {groups.map((g) => {
            const key = differenceKey(g);
            const typed = priceTyped[key] ?? (g.median_user_price != null ? String(g.median_user_price) : "");
            const typedPrice = parseUnitPrice(typed);
            const pct = differencePct(g);
            const isBusy = busy === key;
            const car = vehicleKeyLabel(g.make, g.model_key);
            const range = g.min_price != null && g.max_price != null ? (g.min_price === g.max_price ? priceText(g.min_price) : `${priceText(g.min_price)}–${priceText(g.max_price)}`) : null;
            return (
              <li key={key} className="s1-diff-row">
                <div className="s1-diff-head">
                  <div style={{ minWidth: 0 }}>
                    <div className="s1-diff-what">
                      <strong>{g.wording}</strong>
                      {car && <span className="s1-diff-car">{car}</span>}
                    </div>
                    <div className="s1-diff-facts">
                      <span>
                        Book:{" "}
                        {g.book_price != null ? (
                          <>
                            <strong>{priceText(g.book_price)}</strong>
                            {g.approved ? " (approved)" : g.book_cases ? ` (${g.book_cases} case${g.book_cases === 1 ? "" : "s"})` : ""}
                          </>
                        ) : (
                          "none"
                        )}
                      </span>
                      <span>
                        People: <strong>{g.median_user_price != null ? priceText(g.median_user_price) : "—"}</strong> middle
                        {pct ? ` (${pct})` : ""}
                        {range ? ` · ${range}` : ""}
                      </span>
                      <span>
                        {g.users_count} {g.users_count === 1 ? "person" : "people"} · {g.cases_count} case{g.cases_count === 1 ? "" : "s"}
                      </span>
                      {g.last_used && <span suppressHydrationWarning>last used {shortDate(g.last_used)}</span>}
                    </div>
                  </div>
                </div>
                <div className="s1-diff-actions">
                  <label className="s1-diff-price">
                    <span>Price per unit</span>
                    <input
                      type="text"
                      inputMode="decimal"
                      className="annex-cell-input"
                      value={typed}
                      disabled={isBusy}
                      aria-label={`New book price per unit for ${car} ${g.wording}`}
                      aria-invalid={typed !== "" && typedPrice == null ? true : undefined}
                      onChange={(e) => {
                        const value = e.target.value;
                        setPriceTyped((prev) => ({ ...prev, [key]: value }));
                      }}
                    />
                  </label>
                  <label className="s1-diff-reason">
                    <span>Reason (optional)</span>
                    <input
                      type="text"
                      className="annex-cell-input"
                      maxLength={500}
                      value={reasons[key] ?? ""}
                      disabled={isBusy}
                      placeholder="e.g. new supplier price"
                      onChange={(e) => {
                        const value = e.target.value;
                        setReasons((prev) => ({ ...prev, [key]: value }));
                      }}
                    />
                  </label>
                  <div className="s1-diff-buttons">
                    <button
                      type="button"
                      className="btn-primary-modern"
                      style={{ fontSize: 11, padding: "6px 12px" }}
                      disabled={busy !== null || typedPrice == null}
                      onClick={() => void decide(g, "update")}
                    >
                      {isBusy ? "Saving…" : typedPrice != null ? `Update book to ${priceText(typedPrice)}` : "Update book"}
                    </button>
                    <button
                      type="button"
                      className="btn-secondary-modern"
                      style={{ fontSize: 11, padding: "6px 12px" }}
                      disabled={busy !== null}
                      onClick={() => void decide(g, "keep")}
                    >
                      Keep book price
                    </button>
                  </div>
                </div>
                {errors[key] && (
                  <div className="setup-error" style={{ margin: "8px 0 0" }}>
                    {errors[key]}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
