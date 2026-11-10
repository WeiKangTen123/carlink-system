/** Field review: what the AI filled, what a person must check, what only a
 * person can know, and what a person has confirmed -- for every cover,
 * parties and annex field, damage item and Annex row (contract 7).
 *
 * The API works the states out (app/reports/review.py) and sends them as
 * `review` on GET /reports/{id} and GET /intake/{id}. A screen shows each
 * with <ReviewBadge>; while a person edits a form it recomputes a field
 * locally with localState(), from the same rules (`review.rules`), so the
 * badge changes before the next save. */

/** ai: the AI read it from a photo and it is usually right -- glance and
 * confirm. check: an AI guess, a past-case price, an inferred side, low
 * confidence. needs: only a person can know it and it is empty (or
 * unpriced). you: a person entered or confirmed it. na: a person said it
 * rightly stays blank. none: nothing to show (an optional field left
 * empty, or a case signed off). */
export type ReviewState = "ai" | "check" | "needs" | "you" | "na" | "none";

/** How a field is judged when nobody has confirmed it (app/reports/
 * review.FIELD_RULES): doc -- read from a document or plate photo, the AI
 * is reliable; guess -- the AI's guess, check it; office -- only the office
 * knows it; template -- the firm's usual wording, filled in, check it;
 * optional -- may rightly stay blank (N/A). */
export type FieldRule = "doc" | "guess" | "office" | "template" | "optional";

export type FieldReview = {
  state: ReviewState;
  /** The photo the AI read it from ("P04"), when known. */
  photo?: string | null;
  /** Who confirmed it, and when (ISO), for "you" / "na". */
  by?: string | null;
  at?: string | null;
};

export type ReviewCounts = { total: number; you: number; ai: number; check: number; needs: number; na: number };

export type AnnexACounts = {
  /** Rows with no workshop estimate yet. */
  needs_price: number;
  /** Rows priced only from past cases, not confirmed against the quotation. */
  history_price: number;
  check: number;
  you: number;
};

export type Review = {
  /** Field key ("vehicle_info.plate_number", "cover_page.date_of_survey",
   * "insurance_details.insurer_name", "report_id", "annex_b", ...) -> state. */
  fields: Record<string, FieldReview>;
  /** Field key -> how it is judged (for localState). */
  rules: Record<string, FieldRule>;
  /** Damage items by index in damage_summary, Annex A rows by index in
   * annex_a.items: their state. */
  damage: ReviewState[];
  annex_a: ReviewState[];
  counts: {
    cover: ReviewCounts;
    parties: ReviewCounts;
    damage: ReviewCounts;
    annex_a: AnnexACounts;
    annex_b: ReviewCounts;
    annex_c: ReviewCounts;
  };
  /** Everything still open: needs + check over every section. */
  open: number;
};

/** A field's state while a person is editing the form: what the server
 * said, unless the value has since changed (then it is theirs). */
export function localState(
  review: Review | null | undefined,
  key: string,
  value: unknown,
  savedValue: unknown,
): ReviewState {
  const server = review?.fields[key]?.state ?? "none";
  const blank = (v: unknown) => v === null || v === undefined || (typeof v === "string" && v.trim() === "");
  if (JSON.stringify(value ?? null) !== JSON.stringify(savedValue ?? null)) {
    if (!blank(value)) return "you";
    const rule = review?.rules[key];
    return rule === "office" || rule === "doc" ? "needs" : "none";
  }
  return server;
}

/** The words a badge says, and its screen-reader label. */
export const REVIEW_WORDS: Record<ReviewState, string> = {
  ai: "AI",
  check: "Check",
  needs: "Needs you",
  you: "You",
  na: "N/A",
  none: "",
};
