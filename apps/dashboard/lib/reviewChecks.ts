/** Phase 1 F (plans/2026-11-23-report-and-parts P-parts 5.2, 5.4, gaps 3,
 * 5, 6, 8, 12, 16 and the map bug): the Review step's checks on the
 * damaged-parts list, so a person sees what Annex A will say before filing.
 *
 *   - filingProblems: rows with no condition, an unnamed "Other / Not
 *     Listed", no part chosen, or the same part twice -- the check before
 *     "File case";
 *   - rowGaps / rowState: the "Needs you" state of such a row;
 *   - checkAdd: "already row 3" and "the other side?" before an add;
 *   - removeAt / restoreRemoved: undo a removed row, exactly;
 *   - printsAs: the firm's wording a row prints as on Annex A, from GET
 *     /taxonomy's firm_part_names (bot-service taxonomy.FIRM_PART_NAMES),
 *     by the rules filing uses (prefill.row_name / item_row_name);
 *   - quickConditions / joinAsPicked: the Condition picker's one-click
 *     whole values and the person's own word order;
 *   - partForMapAdd: the part the 3D map's "+ Add as damage" pre-fills.
 *
 * Pure, with type-only imports from ./api, so Node can load it:
 * scripts/check-review-checks.mjs. */
import type { DamageSummaryItem } from "./api";
import type { ReviewState } from "./review";
import { itemKey } from "./findings";
import { damageState } from "./reviewRows";
import { partForZone, zoneKeyFor } from "./vehicleZones";

// taxonomy.OTHER_PART and the floor wording of an Other nobody named
// (taxonomy._FIRM_WORDING): values, so written here as findings.ts does.
const OTHER_PART = "Other / Not Listed";
const UNNAMED_OTHER = "Other part (to be named)";

type Row = Pick<
  DamageSummaryItem,
  "part" | "other_part_name" | "condition" | "condition_tokens" | "damage_type" | "human_verified" | "ai_confidence" | "side_inferred"
>;

const words = (text: string | null | undefined) => (text || "").split(/\s+/).filter(Boolean).join(" ");

/** taxonomy.clean_part_name (lib/api.ts cleanPartName): spaces collapsed,
 * first letter capitalised; null for blank. */
function cleanName(text: string | null | undefined): string | null {
  const name = words(text).slice(0, 80).trim();
  return name ? name[0].toUpperCase() + name.slice(1) : null;
}

const partOf = (item: Pick<Row, "part">) => (item.part || "").trim();
const isOther = (item: Pick<Row, "part">) => partOf(item) === OTHER_PART;

/** An "Other / Not Listed" row nobody named: it prints "Other part (to be named)". */
export const isUnnamedOther = (item: Pick<Row, "part" | "other_part_name">) => isOther(item) && !cleanName(item.other_part_name);

/** Whether Annex A's Condition for the row has anything to say
 * (taxonomy.annex_condition: the condition, else its words, else the
 * damage type). */
export const hasCondition = (item: Pick<Row, "condition" | "condition_tokens" | "damage_type">) =>
  Boolean((item.condition || "").trim()) || (item.condition_tokens?.length ?? 0) > 0 || Boolean((item.damage_type || "").trim());

/** The row's Condition as its Annex A line shows it, for the warnings
 * ("Deformed/cut"); null when there is none. */
export function conditionShown(item: Pick<Row, "condition" | "condition_tokens" | "damage_type">): string | null {
  const c = (item.condition || "").trim();
  if (c) return c;
  const tokens = (item.condition_tokens || []).filter(Boolean);
  if (tokens.length) return tokens.map((t, i) => (i === 0 ? t[0].toUpperCase() + t.slice(1).toLowerCase() : t.toLowerCase())).join("/");
  return (item.damage_type || "").trim() || null;
}

/** The part as the list shows it: "Other: Rear air vent" for a named
 * Other (lib/api.ts damagePartLabel); "(no part chosen)" for none. */
export function rowLabel(item: Pick<Row, "part" | "other_part_name">): string {
  const part = partOf(item);
  if (!part) return "(no part chosen)";
  const other = isOther(item) ? cleanName(item.other_part_name) : null;
  return other ? `Other: ${other}` : part;
}

// ---- Needs you (P-parts gap 3) ------------------------------------------------

export type RowGap = "no_part" | "no_name" | "no_condition";

/** What a row lacks for its Annex A line: no part (the row is left out),
 * an Other with no name, or no condition. A row with no part has nothing
 * else to say. */
export function rowGaps(item: Row): RowGap[] {
  if (!partOf(item)) return ["no_part"];
  const gaps: RowGap[] = [];
  if (isUnnamedOther(item)) gaps.push("no_name");
  if (!hasCondition(item)) gaps.push("no_condition");
  return gaps;
}

const GAP_WORDS: Record<RowGap, string> = { no_part: "no part chosen", no_name: "no name", no_condition: "no condition" };

/** "no name, no condition" -- the badge's detail. */
export const gapWords = (gaps: RowGap[]) => gaps.map((g) => GAP_WORDS[g]).join(", ");

/** The row's review state: "needs" while it lacks a part, a name or a
 * condition -- a hand-added row is the person's from the start
 * (human_verified), but it isn't done -- else lib/reviewRows damageState. */
export function rowState(item: Row): ReviewState {
  return rowGaps(item).length ? "needs" : damageState(item);
}

// ---- Check before filing (P-parts 5.2) -------------------------------------------

export type FilingIssue = RowGap | "duplicate";

export type FilingProblem = {
  /** The damage_summary index. */
  index: number;
  /** The part as the list shows it. */
  label: string;
  issues: FilingIssue[];
  /** For "duplicate": the index of the earlier row of the same part. */
  sameAs: number | null;
  /** What will happen, in a few words. */
  text: string;
};

/** Every row that needs the person before filing, in list order: a row
 * with no part (left out of Annex A), an Other with no name (prints
 * "Other part (to be named)"), no condition (Annex A's Condition blank),
 * or a part already on an earlier row (prefill._damaged keeps the first
 * row only: this one's condition and remarks never print). Two unnamed
 * Others are one Annex A line too, but naming them settles both, so they
 * are listed as unnamed only. Empty when nothing needs anyone. */
export function filingProblems(items: readonly Row[] | null | undefined): FilingProblem[] {
  const out: FilingProblem[] = [];
  const firstOf = new Map<string, number>();
  (items || []).forEach((item, index) => {
    const issues: FilingIssue[] = [...rowGaps(item)];
    let sameAs: number | null = null;
    if (partOf(item) && !isUnnamedOther(item)) {
      const key = itemKey({ part: item.part, other_part_name: item.other_part_name });
      const first = firstOf.get(key);
      if (first === undefined) firstOf.set(key, index);
      else {
        sameAs = first;
        issues.push("duplicate");
      }
    }
    if (!issues.length) return;
    const text = issues
      .map((issue) => {
        if (issue === "no_part") return "no part chosen: it will be left out of Annex A";
        if (issue === "no_name") return `no name: it prints "${UNNAMED_OTHER}"`;
        if (issue === "no_condition") return "no condition: its Annex A Condition will be blank";
        return `same part as row ${(sameAs ?? 0) + 1}: only row ${(sameAs ?? 0) + 1}'s condition prints`;
      })
      .join("; ");
    out.push({ index, label: rowLabel(item), issues, sameAs, text });
  });
  return out;
}

/** Where "Go to it" puts the cursor: the part select for a row with no
 * part, the name box for an unnamed Other, the Condition for the rest. */
export function focusFor(problem: Pick<FilingProblem, "issues">): "part" | "name" | "condition" {
  if (problem.issues.includes("no_part") || problem.issues.includes("duplicate")) return "part";
  if (problem.issues.includes("no_name")) return "name";
  return "condition";
}

// ---- Duplicate and other-side warnings (P-parts 5.4) ----------------------------

/** "Left Tail Lamp" <-> "Right Tail Lamp"; null for a part with no side. */
export function mirrorOf(part: string | null | undefined): string | null {
  const m = /^(Left|Right) (.+)$/.exec((part || "").trim());
  return m ? `${m[1] === "Left" ? "Right" : "Left"} ${m[2]}` : null;
}

export type AddCheck =
  | { kind: "duplicate"; part: string; index: number; condition: string | null }
  | { kind: "mirror"; part: string; index: number; listed: string }
  | null;

/** What to ask before adding `part` (a named Other by `otherName`): the
 * same part is already on the list (the first such row), or its other
 * side is. null: add it. An unnamed Other is never a duplicate -- it is
 * named after it is added. */
export function checkAdd(items: readonly Row[] | null | undefined, part: string, otherName?: string | null): AddCheck {
  const name = (part || "").trim();
  if (!name) return null;
  const list = items || [];
  const candidate = { part: name, other_part_name: otherName ?? null };
  if (!isUnnamedOther(candidate)) {
    const key = itemKey(candidate);
    const index = list.findIndex((it) => partOf(it) && !isUnnamedOther(it) && itemKey({ part: it.part, other_part_name: it.other_part_name }) === key);
    if (index >= 0) return { kind: "duplicate", part: rowLabel(candidate), index, condition: conditionShown(list[index]) };
  }
  const twin = mirrorOf(name);
  if (twin) {
    const index = list.findIndex((it) => partOf(it) === twin);
    if (index >= 0) return { kind: "mirror", part: name, index, listed: twin };
  }
  return null;
}

/** The warning's words: 'Rear Bumper is already row 3 (Deformed/cut).' /
 * 'The list has Left Tail Lamp. Adding Right Tail Lamp — is it the other side?' */
export function addCheckText(check: NonNullable<AddCheck>): string {
  if (check.kind === "duplicate") return `${check.part} is already row ${check.index + 1}${check.condition ? ` (${check.condition})` : ""}.`;
  return `The list has ${check.listed}. Adding ${check.part} — is it the other side?`;
}

// ---- Undo a removed row (P-parts gap 6) -------------------------------------------

export type Removed<T> = { item: T; index: number };

/** The list without row `index`, and what was taken (null: no such row). */
export function removeAt<T>(list: readonly T[], index: number): { list: T[]; removed: Removed<T> | null } {
  if (index < 0 || index >= list.length) return { list: [...list], removed: null };
  return { list: list.filter((_, i) => i !== index), removed: { item: list[index], index } };
}

/** Puts removed rows back, the same objects (their AI evidence, box and
 * fields untouched), each where it was: the latest removal first, so the
 * earlier ones' places are right again. A place past the end (rows taken
 * off meanwhile) is the end. */
export function restoreRemoved<T>(list: readonly T[], removed: readonly Removed<T>[]): T[] {
  const out = [...list];
  for (const r of [...removed].reverse()) out.splice(Math.min(Math.max(r.index, 0), out.length), 0, r.item);
  return out;
}

// ---- Prints as (R-report gap 8, P-parts gap 8) ------------------------------------

/** What GET /taxonomy sends for the wording (absent from an older API). */
export type FirmWording = {
  firm_part_names?: Record<string, string> | null;
  firm_part_names_hatch?: Record<string, string> | null;
  model_body_types?: Record<string, string> | null;
  body_types?: readonly string[] | null;
};

type Vehicle = { make?: string | null; model?: string | null; body_type?: string | null } | null | undefined;

// taxonomy._HATCH_BODIES: a tail gate, not a boot lid.
const HATCH_BODIES = new Set(["Hatchback", "SUV", "MPV", "Van", "Wagon", "Pickup"]);

/** taxonomy.body_type_for, as filing sets the case's body type
 * (api/intake.py finalize): the make/model catalogue first, then the body
 * type the draft states, else null. */
export function bodyTypeFor(vehicle: Vehicle, t: FirmWording | null | undefined): string | null {
  const text = `${vehicle?.make || ""} ${vehicle?.model || ""}`.toLowerCase();
  for (const [fragment, body] of Object.entries(t?.model_body_types || {})) if (text.includes(fragment)) return body;
  const stated = vehicle?.body_type || null;
  return stated && (t?.body_types || []).includes(stated) ? stated : null;
}

// history.side_of / history._SIDE_WORDS, for a named Other.
const SIDE_WORDS = /\s*\(?\s*\b(?:l\.?\/?h|r\.?\/?h|left|right|nearside|offside)\b\.?\s*\)?/gi;
function sideOf(text: string): "LH" | "RH" | null {
  const t = ` ${text.toLowerCase()} `;
  if (/\blh\b|\bleft\b|\bnearside\b/.test(t)) return "LH";
  if (/\brh\b|\bright\b|\boffside\b/.test(t)) return "RH";
  return null;
}

/** prefill._other_wording: a named Other with its side last as LH / RH
 * ("Left rear air vent" -> "Rear air vent LH"). */
export function otherWording(name: string): string {
  const side = sideOf(name);
  const base = cleanName(name.replace(SIDE_WORDS, " ").replace(/\s{2,}/g, " ").trim()) || name;
  return side ? `${base} ${side}` : base;
}

export type PrintsAs = {
  /** The Annex A wording; null when the row prints nothing (no part) or
   * the API sent no wording (an older one). */
  text: string | null;
  /** left_out: no part chosen; unnamed: an Other nobody named; unsided:
   * the side is the AI's guess, so it prints without LH / RH until a
   * person confirms the row. */
  note: "left_out" | "unnamed" | "unsided" | null;
};

/** The wording a row's own Annex A line takes when the case is filed
 * without past cases (prefill.item_row_name / row_name: the floor,
 * taxonomy.firm_part_name): a named Other by its name, a canonical part by
 * the firm's wording for the body type (tail gate or boot lid), a
 * pre-taxonomy name as written; LH / RH dropped while the side is only
 * inferred (prefill.side_unconfirmed). Past cases of the same make, for
 * staff with the price book, may bring their own wording and lines. */
export function printsAs(
  item: Pick<Row, "part" | "other_part_name" | "side_inferred" | "human_verified">,
  t: FirmWording | null | undefined,
  vehicle: Vehicle
): PrintsAs {
  const part = partOf(item);
  if (!part) return { text: null, note: "left_out" };
  if (part === OTHER_PART) {
    const name = cleanName(item.other_part_name);
    return name ? { text: otherWording(name), note: null } : { text: t?.firm_part_names?.[OTHER_PART] || UNNAMED_OTHER, note: "unnamed" };
  }
  const plain = t?.firm_part_names;
  if (!plain || !Object.keys(plain).length) return { text: null, note: null };
  const hatch = HATCH_BODIES.has(bodyTypeFor(vehicle, t) || "");
  const names = hatch && t?.firm_part_names_hatch ? t.firm_part_names_hatch : plain;
  const name = names[part];
  // A name off the list (a case from before it) prints as it was written.
  if (!name) return { text: part, note: null };
  if (item.side_inferred && !item.human_verified) {
    const unsided = name.replace(/ (LH|RH)$/, "");
    return { text: unsided, note: unsided === name ? null : "unsided" };
  }
  return { text: name, note: null };
}

// ---- Condition picker (P-parts P-S3, gap 12) ---------------------------------------

/** The whole values one click away in the Condition select, after the
 * common compounds: Necessary is the firm's most used Condition (61 of
 * 171 lines), Serviceable a part kept. Repair stays in "Other words…". */
export const QUICK_WHOLE_CONDITIONS = ["Necessary", "Serviceable"] as const;

/** The select's one-click Conditions: the common compounds, then the quick
 * whole values not already among them (the API may add them later). */
export function quickConditions(common: readonly string[]): string[] {
  const have = new Set(common.map((c) => c.toLowerCase()));
  return [...common, ...QUICK_WHOLE_CONDITIONS.filter((w) => !have.has(w.toLowerCase()))];
}

/** The picked words in the order the person picked them, first capitalised
 * ("Torn", "Cracked" -> "Torn/cracked"); the server keeps a typed order
 * (taxonomy.parse_condition). */
export function joinAsPicked(picked: readonly string[]): string {
  return picked
    .filter(Boolean)
    .map((w, i) => (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1).toLowerCase() : w.toLowerCase()))
    .join("/");
}

// ---- The map's "+ Add as damage" (P-parts summary, gap 9) ------------------------

/** The part the intake's add row gets for a map panel: the panel's own part
 * as the case page adds it (vehicleZones.partForZone: "Underbody /
 * Chassis", "Boot Lid" for the tailgate) when it can be picked, else the
 * first pickable part that sits there, else none. */
export function partForMapAdd(zoneKey: string, partsList: readonly string[]): string {
  const own = partForZone(zoneKey);
  if (own && partsList.includes(own)) return own;
  return partsList.find((p) => zoneKeyFor(p) === zoneKey) ?? "";
}
