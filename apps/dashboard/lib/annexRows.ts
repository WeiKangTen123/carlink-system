/** Annex A and Annex B rows while they are edited, and the sums and
 * signatures the tabs share. Moved out of CaseAssessmentTab.tsx on
 * 2026-11-04 so the Annex A, Annex B and Annex C tabs, and the case store
 * (lib/caseState.ts), use one set of rules. */
import type { AnnexAPartItem, AnnexBLabourItem } from "./api";
import { leadingNumber, sameLineKey } from "./pastReports";

// ---------------------------------------------------------------------------
// Annex A rows

/** An Annex A row while it is being edited: the item, a stable key for
 * React, and qty / estimate as typed (so "0.", "12.5" and "1set" survive
 * typing). */
export type AnnexRow = AnnexAPartItem & { _key: string; _qty: string; _est: string };

let annexRowSeq = 0;

export function toAnnexRow(item: AnnexAPartItem): AnnexRow {
  annexRowSeq += 1;
  return {
    ...item,
    _key: `annex-row-${annexRowSeq}`,
    _qty: item.qty_text || (item.qty ? String(item.qty) : ""),
    _est: item.workshop_est ? String(item.workshop_est) : "",
  };
}

/** The same sum the API does on save (PUT /reports/{id}/annex-a):
 * estimate x (1 - discount), no discount on nett lines, 0 with no estimate.
 * The estimate is the line total as the workshop quoted it (10 clips at 50
 * -> 40), so it is NOT multiplied by qty -- this used to, and so did the
 * API, which made every multi-piece line too big. */
export function annexAdjustedCost(it: Pick<AnnexAPartItem, "workshop_est" | "discount_pct" | "is_net_item">): number {
  const est = it.workshop_est || 0;
  if (!est) return 0;
  const discount = it.is_net_item ? 0 : it.discount_pct || 0;
  return Math.round(est * (1 - discount) * 100) / 100;
}

/** Qty as typed: a plain number is the qty; anything else ("1set", "2 pcs")
 * is kept as qty_text, printed as written, with its leading number as the
 * qty the statistics use. Blank reads as 1, as the API does. */
export function qtyFromText(text: string): { qty: number; qty_text: string | null } {
  const t = text.trim();
  if (!t) return { qty: 1, qty_text: null };
  if (/^\d+(\.\d+)?$/.test(t)) {
    const n = parseFloat(t);
    return { qty: n > 0 ? n : 1, qty_text: null };
  }
  return { qty: leadingNumber(t) || 1, qty_text: t };
}

/** The row as saved: editing-only fields dropped. catalogue_ref is always
 * sent, null when the line has none: the API keeps a stored line's ref only
 * when a client leaves the field out (a page from before it), so a blank
 * line typed with a removed line's name never inherits its catalogue part. */
export function fromAnnexRow(row: AnnexRow): AnnexAPartItem {
  const { _key, _qty, _est, ...item } = row;
  const parsed = { ...item, ...qtyFromText(_qty), catalogue_ref: item.catalogue_ref ?? null };
  return { ...parsed, adjusted_cost: annexAdjustedCost(parsed) };
}

/** List Items first, then S/Nett Items -- the order the firm prints, and
 * the order the table is saved in so the API's numbering matches what is
 * on screen. */
export function inSections<T extends { is_net_item: boolean }>(rows: T[]): T[] {
  return [...rows.filter((r) => !r.is_net_item), ...rows.filter((r) => r.is_net_item)];
}

/** What a save would change -- to tell whether anything is unsaved. A
 * catalogue row put on a line (or unlinked) counts: its suggestion and part
 * number, which is all of it a save can change (the API writes the rest
 * from the stored row). */
export function annexSignature(items: AnnexAPartItem[]): string {
  return JSON.stringify(
    inSections(items).map((it) => [
      (it.part_name || "").trim(),
      (it.condition || "").trim(),
      it.action || "",
      it.qty > 0 ? it.qty : 1,
      it.qty_text || null,
      it.workshop_est || 0,
      it.is_net_item ? 0 : it.discount_pct,
      Boolean(it.is_net_item),
      it.oem_part_number || null,
      it.suggested_from || null,
      it.usual_unit_price ?? null,
      Boolean(it.prefilled),
      it.origin || null,
      it.source_part || null,
      Boolean(it.stale),
      it.catalogue_ref?.suggestion_id ?? null,
      it.catalogue_ref?.part_number ?? null,
    ])
  );
}

/** "1,250.50", "S$80" or "12" as typed; anything else counts as 0. */
export function parseAmount(text: string): number {
  const n = parseFloat(text.replace(/[,\s]/g, "").replace(/^s?\$/i, ""));
  return Number.isFinite(n) && n > 0 ? n : 0;
}

export function isBadAmount(text: string): boolean {
  const t = text.replace(/[,\s]/g, "").replace(/^s?\$/i, "");
  return t !== "" && !(Number.isFinite(Number(t)) && Number(t) >= 0);
}

/** A new line starts with no remark: the firm leaves most of that column
 * blank (this used to start every line as "Replace"). Typed by a person,
 * so a detection re-sync leaves it alone. */
export const BLANK_ANNEX_LINE: AnnexAPartItem = {
  item_no: "",
  part_name: "",
  condition: "",
  action: "",
  qty: 1,
  qty_text: null,
  workshop_est: 0,
  discount_pct: 0.2,
  adjusted_cost: 0,
  is_net_item: false,
  oem_part_number: null,
  prefilled: false,
  origin: "typed",
  source_part: null,
  stale: false,
};

/** The remarks the firm writes; any other wording can stay blank. */
export const REMARK_OPTIONS = ["Replace", "Repair", "Straighten", "Disallow"];

export const DISCOUNT_OPTIONS: [number, string][] = [
  [0.0, "0%"],
  [0.1, "10%"],
  [0.15, "15%"],
  [0.2, "20%"],
  [0.3, "30%"],
  [1.0, "100%"],
];

/** "from 2 past Honda Vezel cases", "detected", "from catalogue
 * (parts-honda.uk)": the row's origin in words, for the tag beside the part
 * description. Null for a typed row and for old rows with no origin
 * (nothing to say). */
export function originLabel(it: Pick<AnnexAPartItem, "origin" | "suggested_from" | "prefilled" | "catalogue_ref">): string | null {
  switch (it.origin) {
    case "history":
      return it.suggested_from ? `from ${it.suggested_from}` : "from past cases";
    case "detection":
      return "from detection";
    case "lookup":
      // A line added from the parts catalogue (lib/catalogue.ts
      // catalogueLine: suggested_from "catalogue (parts-honda.uk)").
      if (it.catalogue_ref || it.suggested_from?.startsWith("catalogue")) return it.suggested_from ? `from ${it.suggested_from}` : "from the parts catalogue";
      return it.suggested_from ? `lookup: ${it.suggested_from}` : "from a price lookup";
    default:
      // Rows from before origins existed: the prefill tag still says where
      // a price came from.
      return !it.origin && it.prefilled && it.suggested_from ? `from ${it.suggested_from}` : null;
  }
}

/** How many Annex A lines were made for each detected part, and how many
 * of them carry a price: the "Annex A: 3 lines, 2 priced" cell on tab 1.
 * Matched on source_part; a row without one (saved before source_part
 * existed, or typed by hand) counts for the detected part whose name it
 * matches, if any -- `parts` are the detected parts' sources
 * (damageItemSource: "Other / Not Listed: <name>" for a named Other). */
export function annexLinesByPart(items: AnnexAPartItem[], parts: string[] = []): Map<string, { lines: number; priced: number }> {
  const byName = new Map(parts.map((p) => [sameLineKey(p), p] as const));
  const out = new Map<string, { lines: number; priced: number }>();
  for (const it of items) {
    if (it.stale) continue;
    const part = it.source_part || byName.get(sameLineKey(it.part_name || ""));
    if (!part) continue;
    const entry = out.get(part) || { lines: 0, priced: 0 };
    entry.lines += 1;
    if (it.workshop_est > 0) entry.priced += 1;
    out.set(part, entry);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Annex B rows

export type LabourRow = AnnexBLabourItem & { _key: string; _est: string; _adj: string };

let labourRowSeq = 0;

export function toLabourRow(item: AnnexBLabourItem): LabourRow {
  labourRowSeq += 1;
  return {
    ...item,
    _key: `labour-row-${labourRowSeq}`,
    _est: item.workshop_est ? String(item.workshop_est) : "",
    _adj: item.adjusted_cost ? String(item.adjusted_cost) : "",
  };
}

/** The row as saved. The adjusted cost is whatever the surveyor typed --
 * labour has no discount rule, the API keeps it as given. */
export function fromLabourRow(row: LabourRow): AnnexBLabourItem {
  const { _key, _est, _adj, ...item } = row;
  return { ...item, workshop_est: parseAmount(_est), adjusted_cost: parseAmount(_adj) };
}

export function labourSignature(items: AnnexBLabourItem[]): string {
  return JSON.stringify(
    items.map((it) => [
      (it.description || "").trim(),
      it.workshop_est || 0,
      it.adjusted_cost || 0,
      Boolean(it.prefilled),
      it.suggested_from || null,
      it.usual_price ?? null,
    ])
  );
}

export const BLANK_LABOUR_LINE: AnnexBLabourItem = {
  item_no: "",
  description: "",
  workshop_est: 0,
  adjusted_cost: 0,
  justification: null,
  prefilled: false,
  suggested_from: null,
  usual_price: null,
};

/** Both columns summed over saved rows; what the Summary block and the
 * Annex C letter show. */
export function sumRows(rows: { workshop_est: number; adjusted_cost: number }[]): { est: number; adj: number } {
  const est = rows.reduce((acc, r) => acc + (r.workshop_est || 0), 0);
  const adj = rows.reduce((acc, r) => acc + (r.adjusted_cost || 0), 0);
  return { est: Math.round(est * 100) / 100, adj: Math.round(adj * 100) / 100 };
}
