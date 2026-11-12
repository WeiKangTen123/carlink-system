"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, BookOpen, Plus, RotateCcw, Save, Sparkles, Trash2 } from "lucide-react";
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
import { CatalogueSuggestionsPanel, useCatalogue } from "./CatalogueSuggestionsPanel";
import {
  applyCataloguePicks,
  attachTargets,
  clashingTargets,
  defaultTarget,
  detachFromLine,
  findSuggestion,
  lookupButton,
  needsWording,
  refLine,
  refPrice,
  resolvedTarget,
  safeUrl,
  tickOnly,
  variantSiblings,
  type CatalogueDiagram,
  type CatalogueSuggestion,
} from "@/lib/catalogue";
import { ReviewBadge } from "./ReviewBadge";
import { ReviewConfirmButton } from "./ReviewConfirmButton";
import { CONFIRMED_ROW, annexAConfirmable, annexAState, annexATally, annexATallyText, isPastCasePrice } from "@/lib/reviewRows";
import { LockedNote, SavedNote, UnsavedNote, fieldInputStyle } from "./SaveNotes";

/** Tab 3, Annex A: the spare parts table (List Items / S/Nett Items,
 * Remarks, Qty as text, line-total estimates), the suggested lines from past
 * cases, "Fill prices from past cases", and the rows' origins. Owns annex_a
 * through PUT /reports/{id}/annex-a; nothing is saved until "Save Annex A".
 * Rows carry source_part and origin so tab 1's detection can re-sync
 * without touching what a person priced. Moved from CaseAssessmentTab.tsx
 * on 2026-11-04. The parts-catalogue look-up (button and panel, reference
 * only) is shown only when the API has it switched on; its rows can be
 * ticked and put on a line or a new line of the draft ([Use N ticked]),
 * which then shows the part number, site and the site's price under it --
 * never in W/shop Est., never printed. */
export function AnnexATab({
  report,
  store,
  canEdit,
  isSignedOff,
  onOpenCover,
}: {
  report: ReportDetail;
  store: CaseStore;
  canEdit: boolean;
  isSignedOff: boolean;
  /** Opens tab 2, for the catalogue panel's "Confirm on Cover page". */
  onOpenCover?: () => void;
}) {
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

  // The parts-catalogue look-up: asked only while the table can be edited,
  // like the suggested lines. Off on the server -> null here, and neither
  // the button nor the panel shows.
  const catalogue = useCatalogue(report.id, !readOnly);
  const catalogueButton = lookupButton(catalogue.state, { locked: annexALocked, busy: catalogue.busy });
  // Ticked catalogue rows: suggestion id -> the line it goes on (a draft
  // row's key, or NEW_LINE). Nothing reaches the table until [Use N ticked].
  const [cataloguePicks, setCataloguePicks] = useState<Record<number, string>>({});

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

  // The catalogue rows the draft already carries, by the line number shown.
  const tableLines = annexAShown.map((r) => ({ key: r._key, part_name: r.part_name }));
  const catalogueTargets = attachTargets(tableLines);
  const draftLineOf = new Map<number, number>();
  annexAShown.forEach((r, i) => {
    const id = r.catalogue_ref?.suggestion_id;
    if (typeof id === "number" && !draftLineOf.has(id)) draftLineOf.set(id, i + 1);
  });
  const catalogueInfo = catalogue.state?.enabled ? catalogue.state : null;
  // Ticks still meaning something: the row is still listed, not hidden, and
  // not already on a line of the draft.
  const liveCataloguePicks = catalogueInfo
    ? Object.entries(cataloguePicks)
        .map(([id, target]) => ({ id: Number(id), target: resolvedTarget(target, tableLines) }))
        .filter((p) => {
          const found = findSuggestion(catalogueInfo, p.id);
          return found !== null && found.row.status !== "rejected" && !draftLineOf.has(p.id);
        })
    : [];
  const catalogueClashes = clashingTargets(liveCataloguePicks.map((p) => p.target));
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

  // Field review (contract 7), when the API sends one and the case is open:
  // each line's state (Needs you / Check / You) from the line as it is now,
  // so a typed price or a [✓] shows at once; the header counts them.
  const reviewOn = Boolean(report.review) && !isSignedOff;
  const tallyText = reviewOn ? annexATallyText(annexATally(annexAItems)) : "";

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

  // [Use N ticked] in the catalogue panel: each ticked row onto its line
  // (catalogue_ref, and the part number if the line had none -- name, qty
  // and W/shop Est. untouched) or onto a new, unpriced line. Into the draft
  // only: Save Annex A keeps it, and marks the rows accepted.
  const handleUseCatalogue = () => {
    if (annexALocked || !catalogueInfo || liveCataloguePicks.length === 0 || catalogueClashes.length > 0) return;
    const picks = liveCataloguePicks.flatMap((p) => {
      const found = findSuggestion(catalogueInfo, p.id);
      return found ? [{ target: p.target, ...found }] : [];
    });
    store.setAnnexA((prev) => applyCataloguePicks(prev, picks, (line) => toAnnexRow(line)));
    setCataloguePicks({});
  };

  // Unlink: the line stays, without its catalogue part (and the part number
  // that came with it).
  const handleUnlinkCatalogue = (key: string) => {
    if (annexALocked) return;
    store.setAnnexA((prev) => prev.map((row) => (row._key === key ? detachFromLine(row) : row)));
  };

  /** The tick box and line picker on a catalogue row, or where it is. A
   * colour version (variant_key) shares its group with the others on the
   * diagram: one of them per group, so ticking it unticks the others, and
   * none can be ticked while another is on a line. */
  const renderCatalogueAttach = (row: CatalogueSuggestion, _part: unknown, diagram: CatalogueDiagram) => {
    const onLine = draftLineOf.get(row.id);
    if (onLine) {
      const saved = row.status === "accepted";
      return (
        <span className="annex-suggest-tag" title={saved ? "On this Annex A line" : "On this Annex A line; Save Annex A to keep it"}>
          on line {onLine}
          {saved ? "" : " (unsaved)"}
        </span>
      );
    }
    if (row.status === "rejected") return null;
    const what = row.part_number || row.name || "this row";
    const ticked = row.id in cataloguePicks;
    const target = resolvedTarget(cataloguePicks[row.id], tableLines);
    const siblings = variantSiblings(diagram.rows, row);
    const siblingLine = siblings.map((x) => draftLineOf.get(x.id)).find((n): n is number => typeof n === "number");
    return (
      <span className="catalogue-attach">
        <input
          type="checkbox"
          checked={ticked}
          disabled={annexALocked || siblingLine !== undefined}
          aria-label={`Use ${what} in Annex A`}
          title={
            siblingLine !== undefined
              ? `Another colour version is on line ${siblingLine}: unlink it there first`
              : siblings.length
                ? "Tick the colour on the car (one per part)"
                : "Tick to put this part on an Annex A line"
          }
          onChange={(e) => {
            const on = e.target.checked;
            setCataloguePicks((prev) => {
              if (on) return tickOnly(prev, row.id, defaultTarget(row.match_hint, tableLines), siblings.map((x) => x.id));
              const next = { ...prev };
              delete next[row.id];
              return next;
            });
          }}
        />
        {ticked && (
          <select
            value={target}
            disabled={annexALocked}
            aria-label={`Annex A line for ${what}`}
            onChange={(e) => {
              const value = e.target.value;
              setCataloguePicks((prev) => ({ ...prev, [row.id]: value }));
            }}
            style={{ ...fieldInputStyle, padding: "1px 4px", fontSize: 11, maxWidth: 200 }}
          >
            {catalogueTargets.map((t) => (
              <option key={t.key} value={t.key}>
                {t.label}
              </option>
            ))}
          </select>
        )}
      </span>
    );
  };

  /** Where a row of a folded colour group is, for the group's own line. */
  const catalogueChosen = (row: CatalogueSuggestion): string | null => {
    const onLine = draftLineOf.get(row.id);
    if (onLine) return `on line ${onLine}${row.status === "accepted" ? "" : " (unsaved)"}`;
    return row.id in cataloguePicks ? "ticked" : null;
  };

  /** The tidy name of the catalogue row on a line (the ref doesn't keep
   * it): "check wording" shows while the line still carries it. */
  const tidyNameOf = (item: AnnexRow): string | null => {
    const id = item.catalogue_ref?.suggestion_id;
    return catalogueInfo && typeof id === "number" ? (findSuggestion(catalogueInfo, id)?.row.tidy_name ?? null) : null;
  };

  const catalogueClashLabel = catalogueClashes.length ? catalogueTargets.find((t) => t.key === catalogueClashes[0])?.label : null;
  const catalogueFooter = (
    <>
      <span>
        Reference prices never fill W/shop Est.
        {catalogueClashLabel && (
          <span style={{ color: "var(--badge-amber-text)" }}> Two ticked rows are set to {catalogueClashLabel}: pick another line, or New line, for one.</span>
        )}
      </span>
      <button
        type="button"
        className="btn-primary-modern"
        style={{ fontSize: 11, padding: "6px 14px" }}
        disabled={annexALocked || liveCataloguePicks.length === 0 || catalogueClashes.length > 0}
        title="Puts the ticked parts on their Annex A lines (or new lines). Save Annex A to keep them."
        onClick={handleUseCatalogue}
      >
        <Plus style={{ width: 13, height: 13 }} /> Use {liveCataloguePicks.length} ticked
      </button>
    </>
  );

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
    // The save marked the catalogue rows on lines accepted (and the ones
    // taken off suggested again): show them so.
    if (catalogueInfo) catalogue.reload();
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
    // With field review on, the line's badge says "Check · from 3 past cases".
    if (reviewOn && isPastCasePrice(item)) return null;
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

  /** Field review: the line's state, and [✓] Confirm on a price the
   * machine put there (past cases above all) -- the price stays and the line
   * is a person's (prefilled false, origin "typed"), saved with Save Annex A. */
  const reviewLine = (item: AnnexRow, no: number) => {
    if (!reviewOn) return null;
    const state = annexAState(item);
    if (state === "none") return null;
    const label = item.part_name.trim() || `line ${no}`;
    const detail =
      state === "needs"
        ? "no price yet"
        : state === "check" && isPastCasePrice(item)
          ? item.suggested_from
            ? `from ${item.suggested_from}`
            : "past-case price"
          : state === "check" && !item.stale
            ? item.workshop_est > 0
              ? "price not confirmed"
              : "Serviceable, no price"
            : undefined;
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap", marginTop: 3 }}>
        <ReviewBadge state={state} label={label} detail={detail} />
        {!readOnly && annexAConfirmable(item) && (
          <ReviewConfirmButton
            label={label}
            disabled={annexALocked}
            title="Confirm: the price stands (checked against the quotation). Saved with Save Annex A."
            onClick={() => updateAnnexRow(item._key, CONFIRMED_ROW)}
          />
        )}
      </div>
    );
  };

  /** The parts-catalogue row on a line: "# 04715T7WA90 · parts-honda.uk
   * diagram", "check wording" while the name is still the catalogue's, and
   * Unlink. A reference: not printed. */
  const catalogueNote = (item: AnnexRow, no: number) => {
    const ref = item.catalogue_ref;
    if (!ref) return null;
    const href = safeUrl(ref.diagram_url);
    return (
      <div className="annex-catalogue-ref" title="From the parts catalogue: a reference for the surveyor, not printed">
        <span style={{ fontFamily: "var(--font-mono)" }}>{refLine(ref)}</span>
        {href && (
          <a href={href} target="_blank" rel="noopener noreferrer" className="setup-help-link" aria-label={`Open the catalogue diagram ${ref.diagram_name || ""} (new tab)`}>
            diagram
          </a>
        )}
        {needsWording(item, tidyNameOf(item)) && (
          <span className="annex-suggest-tag" style={{ marginLeft: 0, color: "var(--badge-amber-text)" }} title="The catalogue's own wording: write it as the firm would">
            check wording
          </span>
        )}
        {!readOnly && (
          <button type="button" className="catalogue-text-btn" disabled={annexALocked} onClick={() => handleUnlinkCatalogue(item._key)} aria-label={`Unlink the catalogue part from line ${no}`}>
            Unlink
          </button>
        )}
      </div>
    );
  };

  /** The site's own price under W/shop Est.: "ref EUR 309.41 ex VAT". */
  const catalogueRefPrice = (item: AnnexRow) => {
    const price = item.catalogue_ref ? refPrice(item.catalogue_ref) : null;
    return price ? (
      <div
        title="The parts site's own price: a reference only, never the workshop's estimate, never printed."
        style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2, cursor: "help", whiteSpace: "nowrap" }}
      >
        {price}
      </div>
    ) : null;
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
            {catalogueNote(item, no)}
            {reviewLine(item, no)}
            {staleNote(item, no)}
          </td>
          <td data-label="Condition">{item.condition || "—"}</td>
          <td data-label="Remarks">{item.action || ""}</td>
          <td data-label="Qty" style={{ textAlign: "center", fontFamily: "var(--font-mono)" }}>{qtyShown}</td>
          <td data-label="W/shop Est." style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
            {isUnpriced ? "—" : amt(item.workshop_est)}
            {catalogueRefPrice(item)}
          </td>
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
          {catalogueNote(item, no)}
          {reviewLine(item, no)}
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
          {catalogueRefPrice(item)}
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
              {verifiedCount} of {detected.length} detected part{detected.length === 1 ? "" : "s"} {reviewOn ? "confirmed" : "verified"}
              {" · "}
              {reviewOn
                ? tallyText || "no lines yet"
                : unpricedCount === 0
                  ? "every line priced"
                  : `${unpricedCount} line${unpricedCount === 1 ? "" : "s"} unpriced`}
            </div>
          )}
          {reviewOn && detected.length === 0 && tallyText && (
            <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>{tallyText}</div>
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
            {/* The parts-catalogue look-up (POST /reports/{id}/catalogue/
                lookup): the catalogue worker reads the makers' catalogues;
                the results show in the panel under Suggested lines. Hidden
                while the API has the look-up off. */}
            {catalogueButton && (
              <>
                <button
                  type="button"
                  className="btn-secondary-modern"
                  style={{ fontSize: 11, padding: "5px 12px" }}
                  disabled={catalogueButton.disabled}
                  title={catalogueButton.title}
                  onClick={() => void catalogue.lookup(null)}
                >
                  <BookOpen style={{ width: 13, height: 13, color: "var(--accent-cyan)" }} /> {catalogueButton.label}
                </button>
                {catalogueButton.showCancel && (
                  <button
                    type="button"
                    className="btn-secondary-modern"
                    style={{ fontSize: 11, padding: "5px 12px" }}
                    disabled={catalogue.busy !== null}
                    aria-label="Cancel the catalogue look-up"
                    onClick={() => void catalogue.cancel()}
                  >
                    {catalogue.busy === "cancel" ? "Cancelling…" : "Cancel"}
                  </button>
                )}
              </>
            )}
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

      {/* Ticked rows go into the draft ([Use N ticked]); a reference only:
          never the estimate, never printed. */}
      {!readOnly && (
        <CatalogueSuggestionsPanel
          catalogue={catalogue}
          tableNames={annexAShown.map((r) => r.part_name)}
          locked={annexALocked}
          onOpenCover={onOpenCover}
          renderAttach={renderCatalogueAttach}
          chosenLabel={catalogueChosen}
          footer={catalogueFooter}
        />
      )}

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
