/** Field review for the tables (contract 7, TABLES): the damage items, the
 * Annex A and Annex B rows, the Annex C figures, and the "Open items" list
 * the sign-off box shows.
 *
 * The API works out every state (app/reports/review.py) and sends them as
 * `review`. The tables are edited live, though -- a tick, a typed price, a
 * confirmed past-case price -- and their rows move (added, removed, put in
 * sections) before the next GET, so a row's state is worked out here from
 * the row itself, by the same rules. `review` being there at all is what
 * turns this on: an older API sends none, and then nothing new shows.
 *
 * Pure (no React, no fetching), so the rules can be checked on their own. */
import type { AnnexAPartItem, AnnexBLabourItem, AnnexCInfo, DamageSummaryItem } from "./api";
import type { FieldReview, Review, ReviewState } from "./review";

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

// ---- Damage items ------------------------------------------------------------

type DamageFacts = Pick<DamageSummaryItem, "human_verified" | "ai_confidence" | "side_inferred">;

/** A detected part: "you" once a person ticked or edited it; "check" while
 * the AI was unsure (Low confidence) or only inferred the side; else "ai". */
export function damageState(item: DamageFacts): ReviewState {
  if (item.human_verified) return "you";
  if (item.ai_confidence === "Low" || item.side_inferred) return "check";
  return "ai";
}

/** Why a part is "check", in a few words for the badge ("low confidence"). */
export function damageCheckDetail(item: DamageFacts): string | undefined {
  if (damageState(item) !== "check") return undefined;
  const why = [item.ai_confidence === "Low" ? "low confidence" : null, item.side_inferred ? "side inferred" : null].filter(Boolean);
  return why.join(", ") || undefined;
}

/** How many parts are in each state ("3 of 5 confirmed"). */
export function damageTally(items: DamageFacts[]): { you: number; ai: number; check: number } {
  const t = { you: 0, ai: 0, check: 0 };
  for (const it of items) {
    const s = damageState(it);
    if (s === "you" || s === "ai" || s === "check") t[s] += 1;
  }
  return t;
}

// ---- Annex A -----------------------------------------------------------------

type AnnexAFacts = Pick<AnnexAPartItem, "part_name" | "condition" | "workshop_est" | "prefilled" | "origin" | "stale">;

const hasPrice = (row: Pick<AnnexAPartItem, "workshop_est">) => Number(row.workshop_est) > 0;

/** "Serviceable" (the part is kept): a line that rightly has no price. */
export const isServiceable = (row: Pick<AnnexAPartItem, "condition">) => (row.condition || "").trim().toLowerCase() === "serviceable";

/** A price filled from past cases (POST annex-a/prefill) that nobody has
 * typed over or confirmed: "Check · from 3 past cases". Owner decision 2:
 * it may be signed off, but it shows as check, not as priced. A person
 * changing only the row's Condition leaves the price the past cases' --
 * still check. */
export function isPastCasePrice(row: AnnexAFacts): boolean {
  return Boolean(row.prefilled) && hasPrice(row) && !row.stale;
}

/** An Annex A row: "none" for a line with no description (left out on
 * save); "check" for a stale line (its part is no longer detected: Keep or
 * Remove); "needs" with no price, unless Serviceable; "check" for a
 * past-case price, and for a price the machine put there (detection,
 * suggestion, lookup) nobody confirmed; "you" once a person typed or
 * confirmed it (origin "typed"). Rows from before origins existed read as
 * the API reads them (prefill.origin_of): an estimate nobody prefilled is
 * a person's. */
export function annexAState(row: AnnexAFacts): ReviewState {
  if (!(row.part_name || "").trim()) return "none";
  if (row.stale) return "check";
  if (!hasPrice(row) && !isServiceable(row)) return "needs";
  if (isPastCasePrice(row)) return "check";
  const origin = row.origin || (row.prefilled ? "history" : hasPrice(row) ? "typed" : "detection");
  return origin === "typed" ? "you" : "check";
}

/** A row the [✓] Confirm can settle: a price (or a Serviceable line) the
 * machine put there. A stale row has its own Keep / Remove instead. */
export function annexAConfirmable(row: AnnexAFacts): boolean {
  return annexAState(row) === "check" && !row.stale;
}

/** What Confirm does to a row: the price stays, and it is a person's now.
 * PUT annex-a keeps both (origin may only be raised: typed outranks
 * history / detection), so the next re-sync leaves the row alone. */
export const CONFIRMED_ROW = { prefilled: false, origin: "typed" } as const;

export type AnnexATally = {
  /** No price yet. */
  needsPrice: number;
  /** Priced from past cases only. */
  pastPrice: number;
  /** Any other check: stale, or a machine price not confirmed. */
  check: number;
  /** Typed or confirmed by a person. */
  you: number;
};

export function annexATally(rows: AnnexAFacts[]): AnnexATally {
  const t: AnnexATally = { needsPrice: 0, pastPrice: 0, check: 0, you: 0 };
  for (const row of rows) {
    const s = annexAState(row);
    if (s === "needs") t.needsPrice += 1;
    else if (s === "check") {
      if (isPastCasePrice(row)) t.pastPrice += 1;
      else t.check += 1;
    } else if (s === "you") t.you += 1;
  }
  return t;
}

/** "3 need a price · 2 past-case prices to check · 1 line to check · 5
 * lines confirmed" -- zeros left out; "" when there are no lines. */
export function annexATallyText(t: AnnexATally): string {
  const parts: string[] = [];
  if (t.needsPrice) parts.push(`${t.needsPrice} need${t.needsPrice === 1 ? "s" : ""} a price`);
  if (t.pastPrice) parts.push(`${plural(t.pastPrice, "past-case price", "past-case prices")} to check`);
  if (t.check) parts.push(`${plural(t.check, "line", "lines")} to check`);
  if (t.you) parts.push(`${plural(t.you, "line", "lines")} confirmed`);
  return parts.join(" · ");
}

// ---- Annex B -----------------------------------------------------------------

type LabourFacts = Pick<AnnexBLabourItem, "description" | "prefilled">;

const described = (rows: LabourFacts[]) => rows.filter((r) => (r.description || "").trim());

/** A labour line: "check" while its price is the usual one from past cases
 * (prefilled), "you" once a person typed or confirmed it; "none" for a line
 * with no job description (left out on save). */
export function labourRowState(row: LabourFacts): ReviewState {
  if (!(row.description || "").trim()) return "none";
  return row.prefilled ? "check" : "you";
}

/** Annex B as a whole: "needs" with no lines unless a person said there is
 * no labour ("na": data.checked["annex_b"], as review.fields["annex_b"]);
 * "check" while any line is a past-case price; else "you". */
export function annexBState(rows: LabourFacts[], server: FieldReview | null | undefined): ReviewState {
  const lines = described(rows);
  if (lines.length === 0) return server?.state === "na" ? "na" : "needs";
  return lines.some((r) => r.prefilled) ? "check" : "you";
}

/** The review key "No labour" confirms (PUT /reports/{id}/checked). */
export const ANNEX_B_KEY = "annex_b";

// ---- Annex C -----------------------------------------------------------------

/** The review keys of the two figures the surveyor types on Annex C. */
export const ANNEX_C_KEYS = { lump: "annex_c.agreed_lump_sum", days: "annex_c.repair_days" } as const;

/** One Annex C figure: "needs" while blank (unless a person said N/A),
 * "you" once entered. */
export function annexCFieldState(value: number | null | undefined, server: FieldReview | null | undefined): ReviewState {
  if (value == null) return server?.state === "na" ? "na" : "needs";
  return "you";
}

// ---- Open items (Annex C's sign-off box) ---------------------------------------

/** Where an open item is fixed: a case tab (components/StudioApp.tsx
 * CaseTab), or the Case record drawer (the parties). */
export type OpenTarget = "detection" | "cover" | "annexA" | "annexB" | "annexC" | "record";

export type OpenSection = {
  id: "cover" | "parties" | "damage" | "annexA" | "annexB" | "annexC";
  label: string;
  /** needs + check in this section. */
  open: number;
  /** "3 need you · 2 to check" */
  text: string;
  /** Any "needs" in it (else only checks): the list's icon. */
  needs: boolean;
  target: OpenTarget;
};

/** What the case holds as saved -- sign-off takes it as saved. */
export type SavedTables = {
  damage: DamageFacts[];
  annexA: AnnexAFacts[];
  annexB: LabourFacts[];
  annexC: Pick<AnnexCInfo, "agreed_lump_sum" | "repair_days"> | null | undefined;
};

function needsCheckText(needs: number, check: number): string {
  return [needs ? `${needs} need${needs === 1 ? "s" : ""} you` : "", check ? `${check} to check` : ""].filter(Boolean).join(" · ");
}

/** Every section with something open, in the order of the case's tabs
 * (the parties last: they sit in the Case record drawer). The cover page
 * and the parties are the API's counts (agent FIELDS' screens); the tables
 * are counted here from the saved rows by the rules above, so the list
 * says what their badges say. */
export function openSections(review: Review, saved: SavedTables): OpenSection[] {
  const out: OpenSection[] = [];
  const push = (id: OpenSection["id"], label: string, target: OpenTarget, needsN: number, checkN: number, text: string) => {
    const open = (needsN || 0) + (checkN || 0);
    if (open > 0) out.push({ id, label, target, open, text, needs: needsN > 0 });
  };

  const cover = review.counts?.cover;
  if (cover) push("cover", "Cover page", "cover", cover.needs, cover.check, needsCheckText(cover.needs, cover.check));

  const dmg = damageTally(saved.damage);
  push("damage", "Damaged parts", "detection", 0, dmg.check, `${dmg.check} to check`);

  const a = annexATally(saved.annexA);
  push("annexA", "Annex A", "annexA", a.needsPrice, a.pastPrice + a.check, annexATallyText({ ...a, you: 0 }));

  const bChecks = described(saved.annexB).filter((r) => r.prefilled).length;
  if (annexBState(saved.annexB, review.fields?.[ANNEX_B_KEY]) === "needs") push("annexB", "Annex B", "annexB", 1, 0, "no labour lines, or say there is no labour");
  else push("annexB", "Annex B", "annexB", 0, bChecks, `${plural(bChecks, "past-case price", "past-case prices")} to check`);

  const c = saved.annexC;
  const blanks = [
    annexCFieldState(c?.agreed_lump_sum, review.fields?.[ANNEX_C_KEYS.lump]) === "needs" ? "the lump sum" : "",
    annexCFieldState(c?.repair_days, review.fields?.[ANNEX_C_KEYS.days]) === "needs" ? "the working days" : "",
  ].filter(Boolean);
  push("annexC", "Annex C", "annexC", blanks.length, 0, `${blanks.join(" and ")} not entered`);

  const parties = review.counts?.parties;
  if (parties) push("parties", "Parties", "record", parties.needs, parties.check, needsCheckText(parties.needs, parties.check));

  return out;
}

export const openTotal = (sections: OpenSection[]) => sections.reduce((n, s) => n + s.open, 0);

/** The sign-off's extra tick: "Sign off with 1 item open". */
export const openItemsLabel = (n: number) => `Sign off with ${plural(n, "item", "items")} open`;
