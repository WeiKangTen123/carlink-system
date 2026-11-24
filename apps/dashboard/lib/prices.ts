/** Stage 1, "My prices" and price safety (owner, 24 Nov 2026; plans
 * 2026-11-23-report-and-parts/S1-contract.md): the words and rules the
 * screens share --
 *   - the source tag on an Annex A line ("Your price · 23 Nov", "Approved
 *     price", "Book · 6 cases · 8 mo"), its age, "old" after 12 months, and
 *     "check" until the price is confirmed;
 *   - which lines still need confirming before sign-off;
 *   - the "Saved as your price ..." toast after a save;
 *   - the sign-off's 409 (prices_unconfirmed, Phase 1's must_have, or both);
 *   - the answers of the new routes (/me/prices, /admin/price-differences,
 *     annex-a/confirm-prices), read so that a field an older server leaves
 *     out never breaks a screen.
 *
 * Every new field is optional: an old case, or a server from before Stage
 * 1, has none of them, and then nothing new shows -- no tag, no Confirm
 * button, nothing blocked. A line without price_confirmed counts as
 * confirmed.
 *
 * Pure (no React, no fetching), so scripts/check-prices-ui.mjs can check
 * it with Node alone. Type imports only: Node strips them. */
import type { AnnexAPartItem, AnnexCInfo } from "./api";
import type { MustHaveItem } from "./review";

// ---- Types ---------------------------------------------------------------------

/** Where an Annex A line's price came from: the person's own remembered
 * price, an admin-approved price, the price book's median, typed by the
 * person, or (later) the AI or the parts catalogue. Null: none. */
export type PriceSource = "mine" | "approved" | "book" | "typed" | "ai" | "catalogue";

/** The sources a person must confirm before sign-off (contract P5). */
export const GATED_SOURCES: readonly string[] = ["mine", "approved", "book"];

/** The Annex A line fields this module reads. */
export type PriceFacts = Pick<AnnexAPartItem, "price_source" | "price_confirmed" | "book_price" | "price_date" | "price_cases">;

/** A line the sign-off refuses until it is confirmed (409 prices_unconfirmed). */
export type UnconfirmedPriceLine = {
  /** The line's place in annex_a.items (0-based); -1 when not sent. */
  index: number;
  wording: string;
  price_source: string | null;
  /** Per unit; null when not sent. */
  unit_price: number | null;
};

/** A price the Annex A save remembered as the person's own (PUT annex-a,
 * `remembered`). previous_unit_price is null when the row is new. */
export type RememberedPrice = {
  id: number | string;
  wording: string;
  make: string;
  model_key: string;
  unit_price: number;
  previous_unit_price: number | null;
};

/** One of the person's own prices (GET /me/prices). */
export type MyPrice = {
  id: number | string;
  make: string;
  model_key: string;
  wording: string;
  side: string | null;
  unit_price: number;
  updated_at: string | null;
  uses: number;
  last_case_id: string | null;
  /** Older than 12 months (shown, never blocking). */
  old: boolean;
};

/** A group on the admins' "Price differences" list
 * (GET /admin/price-differences): people's own prices differ from the
 * book's by more than 10%. Counts only, never names. */
export type PriceDifference = {
  make: string;
  model_key: string;
  item_key: string;
  wording: string;
  /** The book reference per unit: the active approved price, else the
   * median of >=2 confirmed cases; null when the book has none. */
  book_price: number | null;
  book_cases: number | null;
  /** The reference is an admin-approved price. */
  approved: boolean;
  users_count: number;
  cases_count: number;
  min_price: number | null;
  max_price: number | null;
  median_user_price: number | null;
  last_used: string | null;
};

/** POST /admin/price-differences/decide. */
export type PriceDecision = {
  make: string;
  model_key: string;
  item_key: string;
  decision: "update" | "keep";
  unit_price?: number;
  reason?: string;
};

/** What the sign-off's 409 said: Phase 1's must-have facts, the Annex A
 * prices still to confirm, or both in one answer. */
export type SignOffBlock = {
  codes: string[];
  detail: string | null;
  missing: MustHaveItem[];
  prices: UnconfirmedPriceLine[];
};

// ---- Small readers -------------------------------------------------------------

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => Boolean(v) && typeof v === "object" && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === "string" ? v : "");
const strOrNull = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const count = (v: unknown): number => {
  const n = num(v);
  return n != null && n > 0 ? Math.floor(n) : 0;
};
const idOf = (v: unknown): number | string | null => (typeof v === "number" && Number.isFinite(v) ? v : typeof v === "string" && v ? v : null);

// ---- Dates and money -------------------------------------------------------------

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A price is "old" after this many months (contract P4). */
export const OLD_AFTER_MONTHS = 12;

/** The date an ISO date or date-time names, as written (no time zone
 * shift, so the server and the browser agree). */
function dateOf(iso: string | null | undefined): { y: number; m: number; d: number } | null {
  const hit = typeof iso === "string" ? iso.match(/^(\d{4})-(\d{2})-(\d{2})/) : null;
  if (!hit) return null;
  const [y, m, d] = [Number(hit[1]), Number(hit[2]), Number(hit[3])];
  return m >= 1 && m <= 12 && d >= 1 && d <= 31 ? { y, m, d } : null;
}

/** Today in Malaysia and Singapore (UTC+8). */
function todayOf(now: Date): { y: number; m: number; d: number } {
  const t = new Date(now.getTime() + 8 * 3600 * 1000);
  return { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate() };
}

/** Whole calendar months from the date to today (0 within the first
 * month, never negative); null for no date. */
export function monthsSince(iso: string | null | undefined, now: Date = new Date()): number | null {
  const from = dateOf(iso);
  if (!from) return null;
  const to = todayOf(now);
  const months = (to.y - from.y) * 12 + (to.m - from.m) - (to.d < from.d ? 1 : 0);
  return Math.max(0, months);
}

/** Older than 12 months: shown as "old", never blocking. */
export function isOldPrice(iso: string | null | undefined, now: Date = new Date()): boolean {
  const months = monthsSince(iso, now);
  return months != null && months >= OLD_AFTER_MONTHS;
}

/** "< 1 mo", "8 mo", "14 mo", "3 yr": how old a price is. */
export function ageText(iso: string | null | undefined, now: Date = new Date()): string | null {
  const months = monthsSince(iso, now);
  if (months == null) return null;
  if (months < 1) return "< 1 mo";
  if (months < 24) return `${months} mo`;
  return `${Math.floor(months / 12)} yr`;
}

/** "23 Nov", or "23 Nov 2025" when not this year. */
export function shortDate(iso: string | null | undefined, now: Date = new Date()): string | null {
  const at = dateOf(iso);
  if (!at) return null;
  const base = `${at.d} ${MONTHS[at.m - 1]}`;
  return at.y === todayOf(now).y ? base : `${base} ${at.y}`;
}

/** "$400", "$1,250.50": a price as the screens write it. */
export function priceText(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  const whole = Math.abs(n - Math.round(n)) < 0.005;
  const digits = whole ? 0 : 2;
  return `$${n.toLocaleString("en-SG", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

/** The price per unit of a line: its estimate over its quantity, to the cent. */
export function unitPriceOf(line: Pick<AnnexAPartItem, "workshop_est" | "qty">): number | null {
  const est = Number(line.workshop_est) || 0;
  if (est <= 0) return null;
  const qty = Number(line.qty) > 0 ? Number(line.qty) : 1;
  return Math.round((est / qty) * 100) / 100;
}

/** A price as typed ("400", "$1,250.50", "RM 80"): the number, to the
 * cent, or null when it isn't a price above 0. */
export function parseUnitPrice(text: string): number | null {
  const t = text.trim().replace(/[,\s]/g, "").replace(/^(?:rm|s?\$)/i, "");
  if (!/^\d+(\.\d+)?$/.test(t)) return null;
  const n = Math.round(Number(t) * 100) / 100;
  return n > 0 && n < 10_000_000 ? n : null;
}

/** "honda" + "vezel" -> "Honda Vezel"; "cr-v" -> "CR-V". The price book's
 * keys are lower case; a word already in capitals is left as it is. */
export function vehicleKeyLabel(make: string | null | undefined, modelKey: string | null | undefined): string {
  const word = (w: string) => {
    if (w !== w.toLowerCase()) return w;
    if (/[\d-]/.test(w) && w.length <= 5) return w.toUpperCase();
    return w.charAt(0).toUpperCase() + w.slice(1);
  };
  return [make, modelKey]
    .map((s) => (s || "").trim())
    .filter(Boolean)
    .join(" ")
    .split(/\s+/)
    .filter(Boolean)
    .map(word)
    .join(" ");
}

// ---- An Annex A line ----------------------------------------------------------------

/** A price filled in for the person (theirs, approved, or the book's)
 * that nobody has confirmed yet. A line without price_confirmed (an old
 * case, an older server) counts as confirmed, so it never blocks. */
export function isPriceUnconfirmed(row: Partial<PriceFacts>): boolean {
  return typeof row.price_source === "string" && GATED_SOURCES.includes(row.price_source) && row.price_confirmed === false;
}

/** The tag beside a line's price. */
export type PriceTag = {
  source: "mine" | "approved" | "book";
  /** "Your price · 23 Nov", "Approved price", "Book · 6 cases · 8 mo". */
  text: string;
  /** Not confirmed yet: "check". */
  check: boolean;
  /** Over 12 months old: "old". */
  old: boolean;
  /** The tooltip: where the price came from, and the book price when the
   * source isn't the book. */
  title: string;
};

/** The source tag of an Annex A line; null for a typed price, a line
 * without a source, and old cases (nothing to say). */
export function priceTag(row: Partial<PriceFacts>, now: Date = new Date()): PriceTag | null {
  const source = row.price_source;
  if (source !== "mine" && source !== "approved" && source !== "book") return null;
  const date = shortDate(row.price_date, now);
  const old = isOldPrice(row.price_date, now);
  const check = isPriceUnconfirmed(row);
  const cases = count(row.price_cases);
  const book = num(row.book_price);
  const bookWords = book != null ? `Book price: ${priceText(book)} per unit.` : "The price book has no price for this line.";
  let text: string;
  let title: string;
  if (source === "mine") {
    text = date ? `Your price · ${date}` : "Your price";
    title = `Your own price for this make and model${date ? `, last set ${date}` : ""} (Settings → My prices). ${bookWords}`;
  } else if (source === "approved") {
    text = "Approved price";
    title = `A price an admin approved for the firm's price book${date ? ` on ${date}` : ""}. ${bookWords}`;
  } else {
    const age = ageText(row.price_date, now);
    text = ["Book", cases ? `${cases} case${cases === 1 ? "" : "s"}` : "", age || ""].filter(Boolean).join(" · ");
    title = `The price book: the middle price of ${cases ? `${cases} past case${cases === 1 ? "" : "s"}` : "past cases"} of this make and model${
      date ? `, newest ${date}` : ""
    }.`;
  }
  if (old) title += " Over 12 months old.";
  if (check) title += " Not confirmed yet: check it against the workshop's quote, or use Confirm all prices.";
  return { source, text, check, old, title };
}

/** The lines still to confirm before sign-off, as the API lists them
 * (index in the table as saved, wording, source, price per unit). */
export function unconfirmedPriceLines(
  items: ReadonlyArray<Partial<PriceFacts> & Pick<AnnexAPartItem, "part_name" | "workshop_est" | "qty">>
): UnconfirmedPriceLine[] {
  const out: UnconfirmedPriceLine[] = [];
  items.forEach((it, index) => {
    if (!(it.part_name || "").trim() || !isPriceUnconfirmed(it)) return;
    out.push({ index, wording: it.part_name.trim(), price_source: it.price_source ?? null, unit_price: unitPriceOf(it) });
  });
  return out;
}

/** What typing a W/shop Est. does to a line: the price is the person's
 * own now, and confirmed (the API sets the same when it sees the estimate
 * changed). Only for a line that has a source to replace. */
export function typedPricePatch(row: Partial<PriceFacts>): Partial<PriceFacts> {
  return row.price_source == null && row.price_confirmed == null ? {} : { price_source: "typed", price_confirmed: true };
}

/** "Your price", "Approved", "Book": a source in a word, for lists. */
export function sourceWord(source: string | null | undefined): string {
  switch (source) {
    case "mine":
      return "Your price";
    case "approved":
      return "Approved";
    case "book":
      return "Book";
    case "typed":
      return "Typed";
    case "ai":
      return "AI";
    case "catalogue":
      return "Catalogue";
    default:
      return "";
  }
}

/** "Confirm all prices (3)". */
export function confirmAllLabel(n: number): string {
  return `Confirm all prices (${n})`;
}

// ---- After a save -----------------------------------------------------------------------

/** The `remembered` list of a save's answer; [] when there is none (an
 * older server) or an entry can't be read. */
export function parseRemembered(body: unknown): RememberedPrice[] {
  const list = isObj(body) && Array.isArray(body.remembered) ? body.remembered : [];
  return list.flatMap((r: unknown) => {
    if (!isObj(r)) return [];
    const id = idOf(r.id);
    const unit = num(r.unit_price);
    if (id == null || unit == null) return [];
    return [{ id, wording: str(r.wording), make: str(r.make), model_key: str(r.model_key), unit_price: unit, previous_unit_price: num(r.previous_unit_price) }];
  });
}

/** "Saved as your price for Honda Vezel · Rear bumper fascia ($400)." or
 * "Saved 3 prices as yours."; "" for none. */
export function rememberedToastText(list: ReadonlyArray<RememberedPrice>): string {
  if (list.length === 0) return "";
  if (list.length > 1) return `Saved ${list.length} prices as yours.`;
  const r = list[0];
  const car = vehicleKeyLabel(r.make, r.model_key);
  const what = [car, r.wording.trim()].filter(Boolean).join(" · ");
  return `Saved as your price${what ? ` for ${what}` : ""} (${priceText(r.unit_price)}).`;
}

/** "Undone: back to $380." / "Undone." -- after the toast's Undo. */
export function undoneText(list: ReadonlyArray<RememberedPrice>, failed: number): string {
  if (failed > 0) return `${failed} of ${list.length} couldn't be undone. Change ${failed === 1 ? "it" : "them"} in Settings → My prices.`;
  if (list.length === 1 && list[0].previous_unit_price != null) return `Undone: your price is ${priceText(list[0].previous_unit_price)} again.`;
  return list.length === 1 ? "Undone: that price is no longer remembered." : `Undone: ${list.length} prices no longer remembered.`;
}

// ---- Confirm all prices ---------------------------------------------------------------

/** The lines POST annex-a/confirm-prices answered with -- as annex_a
 * ({annex_a: {items}}), `lines`, `items`, or the list itself -- or null
 * when the answer holds no lines (then the page is fetched again). */
export function confirmedLinesFrom(body: unknown): { items: AnnexAPartItem[] | null; annex_c: AnnexCInfo | null } {
  const looksLikeLines = (v: unknown): v is AnnexAPartItem[] =>
    Array.isArray(v) && v.every((x) => isObj(x) && typeof x.part_name === "string");
  let items: AnnexAPartItem[] | null = null;
  if (looksLikeLines(body)) items = body;
  else if (isObj(body)) {
    const annexA = isObj(body.annex_a) ? body.annex_a.items : undefined;
    for (const candidate of [annexA, body.lines, body.items]) {
      if (looksLikeLines(candidate)) {
        items = candidate;
        break;
      }
    }
  }
  const annexC = isObj(body) && isObj(body.annex_c) ? (body.annex_c as unknown as AnnexCInfo) : null;
  return { items, annex_c: annexC };
}

// ---- The sign-off's 409 ------------------------------------------------------------------

const BLOCK_CODES = new Set(["must_have", "prices_unconfirmed"]);

function blockCodes(code: unknown): string[] {
  const list = Array.isArray(code) ? code : [code];
  return list.filter((c): c is string => typeof c === "string" && BLOCK_CODES.has(c));
}

function parseMissing(v: unknown): MustHaveItem[] {
  if (!Array.isArray(v)) return [];
  return v.flatMap((m: unknown) => {
    if (!isObj(m) || typeof m.label !== "string" || !Array.isArray(m.keys)) return [];
    return [{ label: m.label, keys: (m.keys as unknown[]).filter((k): k is string => typeof k === "string") }];
  });
}

function parsePriceLines(v: unknown): UnconfirmedPriceLine[] {
  if (!Array.isArray(v)) return [];
  return v.flatMap((l: unknown) => {
    if (!isObj(l)) return [];
    const index = num(l.index);
    return [{ index: index != null && index >= 0 ? Math.floor(index) : -1, wording: str(l.wording), price_source: strOrNull(l.price_source), unit_price: num(l.unit_price) }];
  });
}

/** A refused sign-off for must-have facts (409 must_have, `missing`),
 * unconfirmed prices (409 prices_unconfirmed, `lines`), or both in one
 * answer -- whether the API sends the fields at the top or inside detail
 * (HTTPException(detail={...})). Null for any other answer. */
export function signOffBlock(body: unknown): SignOffBlock | null {
  if (!isObj(body)) return null;
  const inner = isObj(body.detail) ? body.detail : null;
  const src = blockCodes(body.code).length ? body : inner && blockCodes(inner.code).length ? inner : null;
  if (!src) return null;
  const missing = parseMissing(src.missing);
  const prices = parsePriceLines(src.lines);
  const codes = new Set(blockCodes(src.code));
  if (missing.length) codes.add("must_have");
  if (prices.length) codes.add("prices_unconfirmed");
  return { codes: [...codes], detail: strOrNull(src.detail), missing, prices };
}

/** The refusal in words: the API's own, else "2 things before sign-off:
 * Make & Model, Workshop. 3 prices to confirm on Annex A." */
export function signOffBlockMessage(block: SignOffBlock): string {
  if (block.detail) return block.detail;
  const parts: string[] = [];
  const n = block.missing.length;
  if (n) parts.push(`${n} thing${n === 1 ? "" : "s"} before sign-off: ${block.missing.map((m) => m.label).join(", ")}.`);
  const p = block.prices.length;
  if (p) parts.push(`${p} price${p === 1 ? "" : "s"} to confirm on Annex A.`);
  if (!parts.length && block.codes.includes("prices_unconfirmed")) parts.push("Some Annex A prices still need confirming.");
  return parts.join(" ") || "Not ready for sign-off yet.";
}

// ---- My prices ----------------------------------------------------------------------------

function myPriceOf(r: unknown, now: Date): MyPrice | null {
  if (!isObj(r)) return null;
  const id = idOf(r.id);
  const unit = num(r.unit_price);
  if (id == null || unit == null) return null;
  const updated = strOrNull(r.updated_at);
  return {
    id,
    make: str(r.make),
    model_key: str(r.model_key),
    wording: str(r.wording),
    side: strOrNull(r.side),
    unit_price: unit,
    updated_at: updated,
    uses: count(r.uses),
    last_case_id: strOrNull(r.last_case_id),
    old: typeof r.old === "boolean" ? r.old : isOldPrice(updated, now),
  };
}

/** GET /me/prices' answer ({prices: [...]}, or the list itself). */
export function normalizeMyPrices(body: unknown, now: Date = new Date()): MyPrice[] {
  const list = Array.isArray(body) ? body : isObj(body) && Array.isArray(body.prices) ? body.prices : [];
  return list.flatMap((r: unknown) => {
    const row = myPriceOf(r, now);
    return row ? [row] : [];
  });
}

/** PATCH /me/prices/{id}'s answer: the row (or {price: row}); null when
 * it can't be read. */
export function normalizeMyPrice(body: unknown, now: Date = new Date()): MyPrice | null {
  return myPriceOf(isObj(body) && isObj(body.price) ? body.price : body, now);
}

/** The table's filters: search across wording, make, model and side; make
 * and model exactly (any case). */
export function filterMyPrices(rows: ReadonlyArray<MyPrice>, f: { q?: string; make?: string; model?: string }): MyPrice[] {
  const q = (f.q || "").trim().toLowerCase();
  const make = (f.make || "").toLowerCase();
  const model = (f.model || "").toLowerCase();
  return rows.filter(
    (r) =>
      (!make || r.make.toLowerCase() === make) &&
      (!model || r.model_key.toLowerCase() === model) &&
      (!q || [r.wording, r.make, r.model_key, r.side || "", vehicleKeyLabel(r.make, r.model_key)].some((s) => s.toLowerCase().includes(q)))
  );
}

/** The makes in the list, and the models of one make (all makes when
 * none is picked), sorted. */
export function makeModelOptions(rows: ReadonlyArray<MyPrice>, make: string): { makes: string[]; models: string[] } {
  const uniq = (xs: string[]) => [...new Set(xs.filter(Boolean))].sort((a, b) => a.localeCompare(b));
  return {
    makes: uniq(rows.map((r) => r.make)),
    models: uniq(rows.filter((r) => !make || r.make.toLowerCase() === make.toLowerCase()).map((r) => r.model_key)),
  };
}

// ---- Price differences (admins) ---------------------------------------------------------------

/** GET /admin/price-differences' answer ({groups: [...]}, or the list
 * itself); a group without its keys is left out (it couldn't be decided). */
export function normalizePriceDifferences(body: unknown): PriceDifference[] {
  const list = Array.isArray(body)
    ? body
    : isObj(body)
      ? ([body.groups, body.differences, body.items].find(Array.isArray) as unknown[] | undefined) ?? []
      : [];
  return list.flatMap((g: unknown) => {
    if (!isObj(g)) return [];
    const make = str(g.make);
    const modelKey = str(g.model_key);
    const itemKey = str(g.item_key);
    if (!make || !itemKey) return [];
    return [
      {
        make,
        model_key: modelKey,
        item_key: itemKey,
        wording: str(g.wording) || itemKey,
        book_price: num(g.book_price),
        book_cases: num(g.book_cases),
        approved: g.approved === true,
        users_count: count(g.users_count),
        cases_count: count(g.cases_count),
        min_price: num(g.min_price),
        max_price: num(g.max_price),
        median_user_price: num(g.median_user_price),
        last_used: strOrNull(g.last_used),
      },
    ];
  });
}

/** One key per group. */
export function differenceKey(g: Pick<PriceDifference, "make" | "model_key" | "item_key">): string {
  return `${g.make}|${g.model_key}|${g.item_key}`;
}

/** "+18%" / "-12%": the people's median against the book's price. */
export function differencePct(g: Pick<PriceDifference, "book_price" | "median_user_price">): string | null {
  if (g.book_price == null || g.book_price <= 0 || g.median_user_price == null) return null;
  const pct = Math.round(((g.median_user_price - g.book_price) / g.book_price) * 100);
  return `${pct > 0 ? "+" : ""}${pct}%`;
}

/** The decide body for a group, or the reason it can't be sent (an
 * update needs a price above 0). The reason is trimmed; blank is left out. */
export function decisionBody(
  g: Pick<PriceDifference, "make" | "model_key" | "item_key">,
  decision: "update" | "keep",
  priceTyped: string,
  reason: string
): PriceDecision | { error: string } {
  const base: PriceDecision = { make: g.make, model_key: g.model_key, item_key: g.item_key, decision };
  const why = reason.trim();
  if (why) base.reason = why.slice(0, 500);
  if (decision === "keep") return base;
  const price = parseUnitPrice(priceTyped);
  if (price == null) return { error: "Type the price per unit to put in the book (a number above 0)." };
  return { ...base, unit_price: price };
}
