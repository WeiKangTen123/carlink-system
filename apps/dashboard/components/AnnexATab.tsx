"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Plus, RotateCcw, Save, Sparkles, Trash2 } from "lucide-react";
import { conditionWordsOf, type AnnexAPartItem, type ReportDetail } from "@/lib/api";
import { getTaxonomyAction, prefillAnnexAAction, saveAnnexAAction } from "@/app/reports/actions";
import { canViewAllCases, useCurrentUser } from "@/components/CurrentUser";
import { formatAmount, formatSgd, sameLineKey } from "@/lib/pastReports";
import type { CaseStore } from "@/lib/caseState";
import {
  BLANK_ANNEX_LINE,
  DISCOUNT_OPTIONS,
  REMARK_OPTIONS,
  annexAdjustedCost,
  fromAnnexRow,
  inSections,
  isBadAmount,
  originLabel,
  parseAmount,
  qtyFromText,
  toAnnexRow,
  type AnnexRow,
} from "@/lib/annexRows";
import { AnnexSuggestionsPanel } from "./AnnexSuggestionsPanel";
import { LockedNote, SavedNote, UnsavedNote, fieldInputStyle } from "./SaveNotes";

/** Tab 3, Annex A: the spare parts table (List Items / S/Nett Items,
 * Remarks, Qty as text, line-total estimates), the suggested lines from past
 * cases, "Fill prices from past cases", and the rows' origins. Owns annex_a
 * through PUT /reports/{id}/annex-a; nothing is saved until "Save Annex A".
 * Rows carry source_part and origin so tab 1's detection can re-sync
 * without touching what a person priced. Moved from CaseAssessmentTab.tsx
 * on 2026-11-04. */
export function AnnexATab({ report, store, canEdit, isSignedOff }: { report: ReportDetail; store: CaseStore; canEdit: boolean; isSignedOff: boolean }) {
  const router = useRouter();
  const user = useCurrentUser();
  const { readOnly } = store;
  const plate = store.data.vehicle_info?.plate_number || "";
  const annexAItems = store.annexA;
  const status = store.status.annexA;
  const annexADirty = store.dirty.annexA;
  const [prefillBusy, setPrefillBusy] = useState(false);
  const [prefillNote, setPrefillNote] = useState<string | null>(null);
  const annexALocked = readOnly || status.save === "saving" || prefillBusy;

  // The Condition suggestions: the firm's most used wordings from GET
  // /taxonomy (so they follow the backend's list, not a copy kept here),
  // plus Necessary and Serviceable, the whole-word Conditions the firm
  // writes on fittings and kept parts. The built-in copy stands in while loading
  // or for an older API. Only fetched while the table can be edited, the
  // only time the list is shown.
  const [conditionCommon, setConditionCommon] = useState<string[]>(() => conditionWordsOf(null).common);
  useEffect(() => {
    if (readOnly) return;
    let cancelled = false;
    void getTaxonomyAction().then((t) => {
      if (!cancelled) setConditionCommon(conditionWordsOf(t).common);
    });
    return () => {
      cancelled = true;
    };
  }, [readOnly]);
  const conditionSuggestions = useMemo(() => [...new Set([...conditionCommon, "Necessary", "Serviceable"])], [conditionCommon]);

  const sgd = (n: number | null | undefined) => (n == null ? "—" : formatSgd(n));
  const amt = (n: number) => formatAmount(n);

  // A row's adjusted cost is worked out live while the table can be edited
  // (the API's own sum, so it matches what saving stores); once signed off
  // it is the recorded figure.
  const rowAdjusted = (it: AnnexAPartItem) => (readOnly ? it.adjusted_cost : annexAdjustedCost(it));
  const annexAShown = inSections(annexAItems);
  const listRows = annexAShown.filter((r) => !r.is_net_item);
  const nettRows = annexAShown.filter((r) => r.is_net_item);
  const liveParts = {
    est: annexAItems.reduce((acc, it) => acc + (it.workshop_est || 0), 0),
    adj: annexAItems.reduce((acc, it) => acc + rowAdjusted(it), 0),
  };
  // Unpriced rows carry 0 -- a total over them is "—", not 0.00.
  const hasPricedParts = annexAItems.some((it) => it.workshop_est > 0);

  // "5 of 8 detected parts verified · 3 lines unpriced": from tab 1's parts
  // and this table, counted, never scored. A stale line (its part no longer
  // damaged) waits on Keep or Remove, not on a price, so it isn't counted --
  // nor in the Overview's "N Annex A rows unpriced" (prefill.unpriced_rows).
  const detected = store.data.damage_summary || [];
  const verifiedCount = detected.filter((d) => d.human_verified).length;
  const unpricedCount = annexAItems.filter((r) => !r.stale && r.part_name.trim() && !r.workshop_est).length;

  // Every change goes into the store's Annex A draft and nothing is saved
  // until "Save Annex A"; adjusted costs follow from the row
  // (annexAdjustedCost), so the handlers only set what was changed. Rows are
  // found by key, not index, because the table is shown in sections, not in
  // array order.
  const updateAnnexRow = (key: string, patch: Partial<AnnexRow>) => {
    if (annexALocked) return;
    store.setAnnexA((prev) => prev.map((row) => (row._key === key ? { ...row, ...patch } : row)));
  };

  // Any edit by a person -- description, condition, remark, qty, estimate,
  // discount, S/Nett -- makes the row theirs: origin "typed", which the
  // detection re-sync then leaves alone (an unpriced detection row would
  // otherwise be rebuilt blank, losing the edit).
  const updateTypedRow = (key: string, patch: Partial<AnnexRow>) => updateAnnexRow(key, { ...patch, origin: "typed" });

  const handleRemarkChange = (key: string, remark: string) => {
    if (annexALocked) return;
    store.setAnnexA((prev) =>
      prev.map((row) => {
        if (row._key !== key) return row;
        const item: AnnexRow = { ...row, action: remark, origin: "typed" };
        if (remark === "Disallow") {
          // Not nett as well: a nett line takes no discount, so it would
          // still be paid in full.
          item.discount_pct = 1.0;
          item.is_net_item = false;
        } else if (row.action === "Disallow" && item.discount_pct === 1.0) {
          item.discount_pct = 0.2;
        }
        return item;
      })
    );
  };

  const handleApplyTradeDiscountToAll = () => {
    if (annexALocked) return;
    store.setAnnexA((prev) =>
      prev.map((it) => (it.is_net_item || it.action === "Disallow" || it.discount_pct === 0.2 ? it : { ...it, discount_pct: 0.2, origin: "typed" }))
    );
  };

  const handleNetChange = (key: string, isNet: boolean) => {
    updateTypedRow(key, isNet ? { is_net_item: true, discount_pct: 0 } : { is_net_item: false, discount_pct: 0.2 });
  };

  const handleAddAnnexLine = () => {
    if (annexALocked) return;
    store.setAnnexA((prev) => [...prev, toAnnexRow(BLANK_ANNEX_LINE)]);
  };

  const handleRemoveAnnexLine = (key: string) => {
    if (annexALocked) return;
    store.setAnnexA((prev) => prev.filter((row) => row._key !== key));
  };

  // "Keep" on a stale row: the part is no longer detected but the surveyor
  // wants the line, so it becomes their own (no source part, typed) and the
  // next re-sync won't flag it again.
  const handleKeepStale = (key: string) => updateAnnexRow(key, { stale: false, source_part: null, origin: "typed" });

  // From the "Suggested lines" panel: appended, not saved. They come from
  // past cases, so their origin is "history" unless the API said otherwise.
  const handleAddSuggestedLines = (items: AnnexAPartItem[]) => {
    if (annexALocked) return;
    store.setAnnexA((prev) => {
      const have = new Set(prev.map((r) => sameLineKey(r.part_name)));
      return [
        ...prev,
        ...items
          .filter((it) => !have.has(sameLineKey(it.part_name)))
          .map((it) => toAnnexRow({ ...it, origin: it.origin ?? "history", source_part: it.source_part ?? null, stale: false })),
      ];
    });
  };

  const handleDiscardAnnexA = () => {
    if (annexALocked || !confirm("Discard your unsaved changes to Annex A?")) return;
    store.discard("annexA");
  };

  const handleSaveAnnexA = async () => {
    if (annexALocked) return;
    store.setStatus("annexA", "saving");
    setPrefillNote(null);
    const res = await saveAnnexAAction(report.id, inSections(annexAItems).map(fromAnnexRow));
    if ("error" in res) {
      store.setStatus("annexA", "error", res.error);
      return;
    }
    // The API's answer replaces the table: its numbering, adjusted costs,
    // and without the lines that had no part description.
    store.applySaved("annexA", res);
    router.refresh();
  };

  // POST /reports/{id}/annex-a/prefill works on the table as saved and saves
  // the result: rows a person priced stay, prefilled and unpriced rows are
  // priced from past cases of this make and model.
  const handlePrefill = async () => {
    if (annexALocked) return;
    if (annexADirty && !confirm("Filling prices uses Annex A as last saved, so your unsaved changes would be lost. Continue?")) return;
    setPrefillBusy(true);
    setPrefillNote(null);
    store.setStatus("annexA", "saving");
    const res = await prefillAnnexAAction(report.id);
    setPrefillBusy(false);
    if ("error" in res) {
      store.setStatus("annexA", "error", res.error);
      return;
    }
    store.applySaved("annexA", res);
    router.refresh();
    const n = res.filled;
    setPrefillNote(
      n === 0
        ? "No prices found in past cases for the unpriced lines."
        : `${n} line${n === 1 ? "" : "s"} priced from past cases${
            res.unpriced ? `; ${res.unpriced} still ${res.unpriced === 1 ? "has" : "have"} no price` : ""
          }.`
    );
  };

  const annexAColumns = readOnly ? 8 : 10;

  /** Where a row came from, under its description. The amber tag marks a
   * price from past cases that nobody has typed over yet. */
  const originNote = (item: AnnexRow) => {
    if (item.prefilled && item.suggested_from) {
      return (
        <span className="annex-prefill-tag" title="The estimate was filled from past cases, not typed. Typing an estimate replaces it.">
          from {item.suggested_from}
        </span>
      );
    }
    if (item.suggested_from === "AI suggestion") {
      return <div style={{ fontSize: 10, color: "var(--badge-amber-text)", marginTop: 2 }}>AI suggestion — check it</div>;
    }
    const label = originLabel(item);
    return label ? <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>{label}</div> : null;
  };

  /** A kept row whose part is no longer detected: the machine never deletes
   * it; the surveyor keeps or removes it (and saves). */
  const staleNote = (item: AnnexRow, no: number) =>
    item.stale ? (
      <div className="annex-stale-note">
        <AlertTriangle style={{ width: 11, height: 11 }} /> part no longer detected
        {!readOnly && (
          <>
            <button type="button" className="annex-stale-btn" disabled={annexALocked} onClick={() => handleKeepStale(item._key)} aria-label={`Keep line ${no}`}>
              Keep
            </button>
            <button type="button" className="annex-stale-btn" disabled={annexALocked} onClick={() => handleRemoveAnnexLine(item._key)} aria-label={`Remove line ${no}`}>
              Remove
            </button>
          </>
        )}
      </div>
    ) : null;

  /** One Annex A row, editable or as recorded. */
  const renderAnnexARow = (item: AnnexRow, no: number) => {
    const isDisallowed = item.action === "Disallow" || item.discount_pct >= 0.99;
    // Rows carry 0 until a surveyor (or the prefill) prices them.
    const isUnpriced = !item.workshop_est;
    const qtyShown = item.qty_text || (item.qty ? String(item.qty) : "1");

    if (readOnly) {
      return (
        <tr key={item._key} style={{ opacity: isDisallowed ? 0.65 : 1 }} className={item.stale ? "annex-row-stale" : undefined}>
          <td data-label="Item" style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{no}</td>
          <td data-label="Part" style={{ fontWeight: 600 }}>
            {item.part_name}
            {originNote(item)}
            {staleNote(item, no)}
          </td>
          <td data-label="Condition">{item.condition || "—"}</td>
          <td data-label="Remarks">{item.action || ""}</td>
          <td data-label="Qty" style={{ textAlign: "center", fontFamily: "var(--font-mono)" }}>{qtyShown}</td>
          <td data-label="W/shop Est." style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{isUnpriced ? "—" : amt(item.workshop_est)}</td>
          <td data-label="Disc" style={{ textAlign: "center", fontFamily: "var(--font-mono)" }}>{item.is_net_item ? "" : `${Math.round(item.discount_pct * 100)}%`}</td>
          <td data-label="Adjusted" style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 700, color: isDisallowed ? "var(--text-muted)" : "var(--accent-cyan)" }}>
            {isUnpriced ? "—" : amt(item.adjusted_cost)}
          </td>
        </tr>
      );
    }

    const line = `Line ${no}`;
    const knownDiscount = DISCOUNT_OPTIONS.some(([v]) => Math.abs(v - item.discount_pct) < 1e-9);
    return (
      <tr key={item._key} style={{ opacity: isDisallowed ? 0.65 : 1 }} className={item.stale ? "annex-row-stale" : undefined}>
        <td data-label="Item" style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{no}</td>
        <td data-label="Part" style={{ minWidth: 200 }}>
          <input
            type="text"
            className="annex-cell-input"
            style={{ fontWeight: 600 }}
            value={item.part_name}
            placeholder="Part description"
            aria-label={`${line}: part description`}
            disabled={annexALocked}
            onChange={(e) => updateTypedRow(item._key, { part_name: e.target.value })}
          />
          {originNote(item)}
          {staleNote(item, no)}
        </td>
        <td data-label="Condition" style={{ minWidth: 120 }}>
          <input
            type="text"
            className="annex-cell-input"
            list="annex-a-conditions"
            value={item.condition}
            placeholder="—"
            aria-label={`${line}: condition`}
            disabled={annexALocked}
            onChange={(e) => updateTypedRow(item._key, { condition: e.target.value })}
          />
        </td>
        <td data-label="Remarks">
          <select
            value={item.action}
            onChange={(e) => handleRemarkChange(item._key, e.target.value)}
            aria-label={`${line}: remarks`}
            disabled={annexALocked}
            style={{ ...fieldInputStyle, padding: "2px 6px", fontSize: 11 }}
          >
            <option value="">—</option>
            {item.action && !REMARK_OPTIONS.includes(item.action) && <option value={item.action}>{item.action}</option>}
            {REMARK_OPTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </td>
        <td data-label="Qty" style={{ textAlign: "center" }}>
          <input
            type="text"
            className="annex-cell-input"
            style={{ width: 60, textAlign: "center", fontFamily: "var(--font-mono)" }}
            value={item._qty}
            placeholder="1"
            title='A number, or as written on the quote ("1set")'
            aria-label={`${line}: quantity`}
            disabled={annexALocked}
            onChange={(e) => updateTypedRow(item._key, { _qty: e.target.value, ...qtyFromText(e.target.value) })}
          />
        </td>
        <td data-label="W/shop Est." style={{ textAlign: "right" }}>
          <input
            type="text"
            inputMode="decimal"
            className="annex-cell-input"
            style={{ width: 96, textAlign: "right", fontFamily: "var(--font-mono)" }}
            value={item._est}
            placeholder="0.00"
            aria-label={`${line}: workshop estimate (line total)`}
            aria-invalid={isBadAmount(item._est) ? true : undefined}
            disabled={annexALocked}
            // Typed by a person now, so the "from past cases" tag goes.
            onChange={(e) => updateTypedRow(item._key, { _est: e.target.value, workshop_est: parseAmount(e.target.value), prefilled: false })}
          />
          {item.usual_unit_price != null && (
            <div
              title={`What past cases' lines were adjusted to per unit (median), from ${item.suggested_from || "past reports"}. A reference only: enter the workshop's own figure for the line.`}
              style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2, cursor: "help", whiteSpace: "nowrap" }}
            >
              usual {sgd(item.usual_unit_price)} /unit
            </div>
          )}
        </td>
        <td data-label="Disc" style={{ textAlign: "center", fontFamily: "var(--font-mono)" }}>
          {item.is_net_item ? (
            <span style={{ color: "var(--text-muted)" }}>—</span>
          ) : (
            <select
              value={item.discount_pct}
              onChange={(e) => updateTypedRow(item._key, { discount_pct: parseFloat(e.target.value) })}
              aria-label={`${line}: discount`}
              disabled={annexALocked}
              style={{ ...fieldInputStyle, padding: "2px 4px", fontSize: 11 }}
            >
              {!knownDiscount && <option value={item.discount_pct}>{Math.round(item.discount_pct * 100)}%</option>}
              {DISCOUNT_OPTIONS.map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          )}
        </td>
        <td data-label="Nett" style={{ textAlign: "center" }}>
          <input
            type="checkbox"
            checked={item.is_net_item}
            aria-label={`${line}: nett item`}
            title={item.action === "Disallow" ? "A disallowed line can't be nett" : "Nett item: listed under S/Nett Items, no discount"}
            disabled={annexALocked || item.action === "Disallow"}
            onChange={(e) => handleNetChange(item._key, e.target.checked)}
          />
        </td>
        <td data-label="Adjusted" style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 700, color: isDisallowed ? "var(--text-muted)" : "var(--accent-cyan)" }}>
          {isUnpriced ? "—" : amt(rowAdjusted(item))}
        </td>
        <td data-label="" style={{ textAlign: "center" }}>
          <button
            type="button"
            className="annex-row-remove"
            title="Remove this line"
            aria-label={`Remove line ${no}`}
            disabled={annexALocked}
            onClick={() => handleRemoveAnnexLine(item._key)}
          >
            <Trash2 style={{ width: 13, height: 13 }} />
          </button>
        </td>
      </tr>
    );
  };

  const sectionRow = (title: string) => (
    <tr className="annex-section-row" key={`section-${title}`}>
      <td colSpan={annexAColumns}>{title}</td>
    </tr>
  );

  return (
    <div className="card-glass" style={{ padding: 20 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14, flexWrap: "wrap", gap: 10 }}>
        <div>
          <h4 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Annex A : Assessment / Adjustment on Spare Parts</h4>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            {plate ? `${plate} — ` : ""}List Items take the discount; S/Nett Items are paid in full.
          </div>
          {detected.length > 0 && (
            <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>
              {verifiedCount} of {detected.length} detected part{detected.length === 1 ? "" : "s"} verified
              {" · "}
              {unpricedCount === 0 ? "every line priced" : `${unpricedCount} line${unpricedCount === 1 ? "" : "s"} unpriced`}
            </div>
          )}
          <LockedNote isSignedOff={isSignedOff} canEdit={canEdit} />
        </div>
        {!readOnly && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {/* Past-case prices are the firm's price book (admins and
                "see all cases" staff); the API refuses anyone else. */}
            {canViewAllCases(user) && (
              <button
                type="button"
                className="btn-secondary-modern"
                style={{ fontSize: 11, padding: "5px 12px" }}
                disabled={annexALocked}
                title="Prices the unpriced and prefilled lines from past cases of this make and model; lines you priced stay."
                onClick={() => void handlePrefill()}
              >
                <Sparkles style={{ width: 13, height: 13, color: "var(--accent-cyan)" }} /> {prefillBusy ? "Filling prices…" : "Fill prices from past cases"}
              </button>
            )}
            {/* "Look up prices (N unpriced)" goes here in Phase 3 (POST
                /reports/{id}/annex-a/lookup, applied by [Use]). */}
            <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} disabled={annexALocked} onClick={handleApplyTradeDiscountToAll}>
              Set 20% on all List Items
            </button>
          </div>
        )}
      </div>

      {prefillNote && <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 10 }}>{prefillNote}</div>}

      {store.annexAChangedUnderDraft && (
        <div className="setup-error" style={{ marginBottom: 10 }}>
          The detected parts changed on tab 1, so Annex A was re-synced on the server. Your unsaved lines were kept as they are: discard them to see the new rows, or
          save to keep yours.
        </div>
      )}

      {!readOnly && <AnnexSuggestionsPanel reportId={report.id} existingNames={annexAItems.map((r) => r.part_name)} onAdd={handleAddSuggestedLines} />}

      <div className="annex-table-wrapper">
        <table className="annex-table annex-cards">
          <thead>
            <tr>
              <th style={{ width: 45 }}>Item</th>
              <th>Vehicle Parts Description</th>
              <th>Condition</th>
              <th>Remarks</th>
              <th style={{ textAlign: "center", width: 70 }}>Qty</th>
              <th style={{ textAlign: "right", width: 120 }} title="The line total as the workshop quoted it, not a price per unit">
                W/shop Est. ($)
                {!readOnly && <div style={{ fontSize: 9, fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>line total as quoted</div>}
              </th>
              <th style={{ textAlign: "center", width: 80 }}>Disc (%)</th>
              {!readOnly && (
                <th style={{ textAlign: "center", width: 50 }} title="Nett items (clips, sealants, plate) take no discount">
                  Nett
                </th>
              )}
              <th style={{ textAlign: "right", width: 120 }}>Adjusted Cost ($)</th>
              {!readOnly && <th style={{ width: 36 }} aria-label="Remove line" />}
            </tr>
          </thead>
          <tbody>
            {annexAItems.length === 0 && (
              <tr>
                <td colSpan={annexAColumns} style={{ textAlign: "center", color: "var(--text-muted)", padding: "18px 10px" }}>
                  {readOnly ? "No parts recorded" : "No parts recorded yet. Add suggested lines above, or add a line below."}
                </td>
              </tr>
            )}
            {listRows.length > 0 && sectionRow("List Items")}
            {listRows.map((item, i) => renderAnnexARow(item, i + 1))}
            {nettRows.length > 0 && sectionRow("S/Nett Items")}
            {nettRows.map((item, i) => renderAnnexARow(item, listRows.length + i + 1))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={5} style={{ textAlign: "right" }}>
                Total for Spare Parts :
              </td>
              <td data-label="W/shop Est." style={{ textAlign: "right", color: "var(--text-secondary)" }}>{hasPricedParts ? amt(liveParts.est) : "—"}</td>
              <td></td>
              {!readOnly && <td></td>}
              <td data-label="Adjusted" style={{ textAlign: "right", color: "var(--accent-cyan)", fontSize: 13 }}>{hasPricedParts ? amt(liveParts.adj) : "—"}</td>
              {!readOnly && <td></td>}
            </tr>
          </tfoot>
        </table>
      </div>

      {!readOnly && (
        <div className="annex-edit-bar">
          <datalist id="annex-a-conditions">
            {conditionSuggestions.map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap", marginTop: 12 }}>
            <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "6px 12px" }} disabled={annexALocked} onClick={handleAddAnnexLine}>
              <Plus style={{ width: 13, height: 13 }} /> Add line
            </button>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <UnsavedNote state={status.save} dirty={annexADirty} />
              <SavedNote state={status.save} dirty={annexADirty} />
              <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "6px 12px" }} disabled={!annexADirty || annexALocked} onClick={handleDiscardAnnexA}>
                <RotateCcw style={{ width: 13, height: 13 }} /> Discard changes
              </button>
              <button type="button" className="btn-primary-modern" style={{ fontSize: 11, padding: "6px 14px" }} disabled={!annexADirty || annexALocked} onClick={() => void handleSaveAnnexA()}>
                <Save style={{ width: 13, height: 13 }} /> {status.save === "saving" && !prefillBusy ? "Saving…" : "Save Annex A"}
              </button>
            </div>
          </div>
          {annexAItems.some((r) => !r.part_name.trim()) && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 8 }}>Lines with no part description are left out when you save.</div>
          )}
          {status.save === "error" && status.error && (
            <div className="setup-error" style={{ marginTop: 10, marginBottom: 0 }}>
              {status.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
