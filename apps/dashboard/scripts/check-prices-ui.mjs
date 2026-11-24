#!/usr/bin/env node
/**
 * Stage 1, "My prices" and price safety (plans/2026-11-23-report-and-parts/
 * S1-contract.md, "Screens"): the dashboard's pure rules in lib/prices.ts --
 *   - the Annex A source tag ("Your price · 23 Nov", "Approved price",
 *     "Book · 6 cases · 8 mo"), its age, "old" after 12 months, "check"
 *     until confirmed, and the tooltip's book price;
 *   - which lines still need confirming, and what typing a price does;
 *   - the "Saved as your price ..." toast and its Undo words;
 *   - the sign-off's 409 (prices_unconfirmed, must_have, or both at once);
 *   - the new routes' answers (My prices, All users' prices, confirm-prices);
 *   - the admins' All users' prices list: filters, the gap to the shared
 *     default, the "Use this price for everyone" body; the retired Price
 *     differences list gone;
 *   - Annex A line ids sent back exactly as the API gave them (review H2);
 *   - old cases and an older server: every new field missing, nothing shows,
 *     nothing blocks;
 * the review state of an unconfirmed line (lib/reviewRows.ts), the unsaved
 * check (lib/annexRows.ts), and, from the source, that the screens use them.
 *
 *   node apps/dashboard/scripts/check-prices-ui.mjs
 *
 * Prints one line per check and exits 1 if any fails. Needs Node with
 * built-in type stripping (22.18+ / 24) to import the .ts files. No network,
 * no AI, no real data: made-up lines only.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

// apps/dashboard/package.json has no "type" field (Next.js owns it), so Node
// warns MODULE_TYPELESS_PACKAGE_JSON when it loads the ESM .ts files.
process.removeAllListeners("warning");
process.on("warning", (w) => {
  if (w.code !== "MODULE_TYPELESS_PACKAGE_JSON") console.error(w.stack ?? w.message);
});
// The lib files import each other the bundler's way ("./prices", no
// extension); Node needs the file name.
registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith(".") && !/\.[cm]?[jt]s$/.test(specifier) && context.parentURL) {
      const url = new URL(`${specifier}.ts`, context.parentURL);
      if (existsSync(fileURLToPath(url))) return next(url.href, context);
    }
    return next(specifier, context);
  },
});

const P = await import("../lib/prices.ts");
const RR = await import("../lib/reviewRows.ts");
const AR = await import("../lib/annexRows.ts");
const source = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");

let failed = 0;
function check(name, fn) {
  try {
    fn();
    console.log(`ok   ${name}`);
  } catch (err) {
    failed += 1;
    console.log(`FAIL ${name}\n     ${String(err.message).split("\n").join("\n     ")}`);
  }
}

// Noon in Malaysia, 24 Nov 2026.
const NOW = new Date("2026-11-24T04:00:00Z");

/** A made-up Annex A line. */
const line = (over = {}) => ({
  item_no: "1.0",
  part_name: "Rear bumper fascia",
  condition: "Deformed/cut",
  action: "Replace",
  qty: 1,
  workshop_est: 400,
  discount_pct: 0.2,
  adjusted_cost: 320,
  is_net_item: false,
  ...over,
});

// ---- Age and dates ------------------------------------------------------------------

check("monthsSince: whole calendar months, the day counts", () => {
  assert.equal(P.monthsSince("2026-11-23", NOW), 0);
  assert.equal(P.monthsSince("2026-10-24", NOW), 1);
  assert.equal(P.monthsSince("2026-10-25", NOW), 0);
  assert.equal(P.monthsSince("2026-03-01T09:00:00", NOW), 8);
  assert.equal(P.monthsSince("2025-11-24", NOW), 12);
  assert.equal(P.monthsSince("2027-01-01", NOW), 0, "a date ahead of today is 0, never negative");
});

check("monthsSince / ageText: no date, or not a date, is null", () => {
  for (const bad of [null, undefined, "", "yesterday", "2026-13-01", 42]) {
    assert.equal(P.monthsSince(bad, NOW), null, String(bad));
    assert.equal(P.ageText(bad, NOW), null, String(bad));
  }
});

check('ageText: "< 1 mo", "8 mo", "14 mo", "3 yr"', () => {
  assert.equal(P.ageText("2026-11-20", NOW), "< 1 mo");
  assert.equal(P.ageText("2026-03-24", NOW), "8 mo");
  assert.equal(P.ageText("2025-09-01", NOW), "14 mo");
  assert.equal(P.ageText("2023-06-01", NOW), "3 yr");
});

check('isOldPrice: "old" from 12 months, not at 11', () => {
  assert.equal(P.isOldPrice("2025-12-24", NOW), false);
  assert.equal(P.isOldPrice("2025-11-25", NOW), false);
  assert.equal(P.isOldPrice("2025-11-24", NOW), true);
  assert.equal(P.isOldPrice("2024-01-01", NOW), true);
  assert.equal(P.isOldPrice(null, NOW), false, "no date is never old");
});

check('shortDate: "23 Nov" this year, "23 Nov 2025" otherwise; as written, no time zone shift', () => {
  assert.equal(P.shortDate("2026-11-23", NOW), "23 Nov");
  assert.equal(P.shortDate("2026-11-23T23:30:00Z", NOW), "23 Nov");
  assert.equal(P.shortDate("2025-11-23", NOW), "23 Nov 2025");
  assert.equal(P.shortDate(null, NOW), null);
});

check("today is Malaysia's: 31 Dec 2026, 20:00 UTC is already 2027", () => {
  const nye = new Date("2026-12-31T20:00:00Z");
  assert.equal(P.shortDate("2027-01-01", nye), "1 Jan");
  assert.equal(P.shortDate("2026-12-31", nye), "31 Dec 2026");
});

check('priceText: "$400", "$1,250.50", "—" for none', () => {
  assert.equal(P.priceText(400), "$400");
  assert.equal(P.priceText(1250.5), "$1,250.50");
  assert.equal(P.priceText(80.004), "$80");
  assert.equal(P.priceText(null), "—");
  assert.equal(P.priceText(undefined), "—");
  assert.equal(P.priceText(Number.NaN), "—");
});

check("parseUnitPrice: a price above 0 as typed, to the cent; else null", () => {
  assert.equal(P.parseUnitPrice("400"), 400);
  assert.equal(P.parseUnitPrice(" $1,250.505 "), 1250.51);
  assert.equal(P.parseUnitPrice("S$80"), 80);
  assert.equal(P.parseUnitPrice("RM 95.5"), 95.5);
  for (const bad of ["", "0", "-5", "abc", "1e3", "12.3.4", "99999999"]) assert.equal(P.parseUnitPrice(bad), null, bad);
});

check('vehicleKeyLabel: "Honda Vezel", "Honda CR-V", capitals kept', () => {
  assert.equal(P.vehicleKeyLabel("honda", "vezel"), "Honda Vezel");
  assert.equal(P.vehicleKeyLabel("honda", "cr-v"), "Honda CR-V");
  assert.equal(P.vehicleKeyLabel("Toyota", "Corolla Altis"), "Toyota Corolla Altis");
  assert.equal(P.vehicleKeyLabel("bmw", "x5"), "Bmw X5");
  assert.equal(P.vehicleKeyLabel("", ""), "");
  assert.equal(P.vehicleKeyLabel(null, undefined), "");
});

// ---- The tag on an Annex A line ---------------------------------------------------------

check('tag "Your price · 23 Nov", with the book price in the tooltip', () => {
  const t = P.priceTag({ price_source: "mine", price_confirmed: true, book_price: 380, price_date: "2026-11-23" }, NOW);
  assert.equal(t.text, "Your price · 23 Nov");
  assert.equal(t.source, "mine");
  assert.equal(t.check, false);
  assert.equal(t.old, false);
  assert.match(t.title, /Book price: \$380 per unit\./);
  assert.match(t.title, /Settings → My prices/);
});

check('tag "Approved price", with the book price in the tooltip', () => {
  const t = P.priceTag({ price_source: "approved", price_confirmed: true, book_price: 420, price_date: "2026-11-02" }, NOW);
  assert.equal(t.text, "Approved price");
  assert.match(t.title, /approved/);
  assert.match(t.title, /on 2 Nov/);
  assert.match(t.title, /Book price: \$420 per unit\./);
});

check('tag "Book · 6 cases · 8 mo" (no "Book price:" line: it is the book)', () => {
  const t = P.priceTag({ price_source: "book", price_confirmed: true, book_price: 400, price_date: "2026-03-10", price_cases: 6 }, NOW);
  assert.equal(t.text, "Book · 6 cases · 8 mo");
  assert.doesNotMatch(t.title, /Book price:/);
  assert.match(t.title, /6 past cases/);
});

check("tag: a book price without its count or date says what it has", () => {
  assert.equal(P.priceTag({ price_source: "book", price_confirmed: true }, NOW).text, "Book");
  assert.equal(P.priceTag({ price_source: "book", price_cases: 1, price_confirmed: true }, NOW).text, "Book · 1 case");
  assert.equal(P.priceTag({ price_source: "mine", price_confirmed: true }, NOW).text, "Your price");
});

check('tag: "old" after 12 months (from the price\'s own date), never blocking', () => {
  const t = P.priceTag({ price_source: "mine", price_confirmed: true, price_date: "2025-10-01" }, NOW);
  assert.equal(t.old, true);
  assert.equal(t.check, false);
  assert.equal(t.text, "Your price · 1 Oct 2025");
  assert.match(t.title, /Over 12 months old/);
  assert.equal(P.priceTag({ price_source: "book", price_date: "2025-09-01", price_cases: 3, price_confirmed: true }, NOW).text, "Book · 3 cases · 14 mo");
});

check('tag: "check" until confirmed -- false only, not missing', () => {
  const t = P.priceTag({ price_source: "approved", price_confirmed: false, book_price: 300 }, NOW);
  assert.equal(t.check, true);
  assert.match(t.title, /Not confirmed yet/);
  assert.equal(P.priceTag({ price_source: "approved" }, NOW).check, false, "missing price_confirmed counts as confirmed");
});

check("tag: none for typed, AI, catalogue, no source -- and the tooltip says when the book has no price", () => {
  for (const s of ["typed", "ai", "catalogue", null, undefined, "something-new"]) assert.equal(P.priceTag({ price_source: s, price_confirmed: false }, NOW), null, String(s));
  assert.match(P.priceTag({ price_source: "mine", price_confirmed: true, book_price: null }, NOW).title, /no price for this line/);
});

check("old cases: a line with none of the new fields has no tag, isn't unconfirmed, isn't listed", () => {
  const old = line();
  assert.equal(P.priceTag(old, NOW), null);
  assert.equal(P.isPriceUnconfirmed(old), false);
  assert.deepEqual(P.unconfirmedPriceLines([old, line({ part_name: "Tail lamp LH" })]), []);
  assert.deepEqual(P.typedPricePatch(old), {}, "typing a price on an old line adds nothing new");
});

check("an unpriced machine row (no price fields at all, as the API writes it): no tag, confirmed, needs a price", () => {
  // prefill._rows_for: a row the book can't price carries none of the five
  // fields; the save's no_price_fields() writes them as null.
  const bare = line({ workshop_est: 0, adjusted_cost: 0, prefilled: true, origin: "detection", condition: "" });
  const nulls = { ...bare, price_source: null, price_confirmed: null, book_price: null, price_date: null, price_cases: null };
  for (const row of [bare, nulls]) {
    assert.equal(P.priceTag(row, NOW), null);
    assert.equal(P.isPriceUnconfirmed(row), false);
    assert.deepEqual(P.unconfirmedPriceLines([row]), []);
    assert.deepEqual(P.typedPricePatch(row), {});
    assert.equal(RR.annexAState(row), "needs");
    assert.equal(RR.isPastCasePrice(row), false);
    assert.deepEqual(RR.confirmRow(row), { prefilled: false, origin: "typed" }, "no price_confirmed added");
  }
});

check('a line from a single past case: listed, unpriced, no "book" tag, nothing to confirm', () => {
  // prefill._rows_for: fewer than 2 distinct cases -> workshop_est 0 and no
  // price fields, the usual price kept as a reference only.
  const one = line({
    part_name: "Rear bumper bracket",
    workshop_est: 0,
    adjusted_cost: 0,
    prefilled: true,
    origin: "history",
    suggested_from: "1 past case (Honda Vezel)",
    usual_unit_price: 120,
  });
  assert.equal(P.priceTag(one, NOW), null);
  assert.equal(P.isPriceUnconfirmed(one), false);
  assert.deepEqual(P.unconfirmedPriceLines([one]), []);
  assert.equal(RR.annexAState(one), "needs");
  assert.equal(RR.isPastCasePrice(one), false, "no price: not a past-case price to check");
  assert.equal(P.prefillNoteText(0, 1, [one]), "No prices found for the unpriced lines, in your own prices or past cases.");
  const t = RR.annexATally([one, line({ origin: "typed" })]);
  assert.deepEqual(t, { needsPrice: 1, pastPrice: 0, check: 0, you: 1 });
});

check("isPriceUnconfirmed: only mine / approved / book with price_confirmed false", () => {
  for (const s of ["mine", "approved", "book"]) {
    assert.equal(P.isPriceUnconfirmed({ price_source: s, price_confirmed: false }), true, s);
    assert.equal(P.isPriceUnconfirmed({ price_source: s, price_confirmed: true }), false, s);
    assert.equal(P.isPriceUnconfirmed({ price_source: s }), false, `${s} without the field`);
  }
  for (const s of ["typed", "ai", "catalogue", null]) assert.equal(P.isPriceUnconfirmed({ price_source: s, price_confirmed: false }), false, String(s));
});

check("unconfirmedPriceLines: the API's shape -- index in the table, wording, source, price per unit", () => {
  const items = [
    line({ price_source: "typed", price_confirmed: true }),
    line({ part_name: " Tail lamp LH ", qty: 2, workshop_est: 300, price_source: "mine", price_confirmed: false }),
    line({ part_name: "Clips", qty: 10, workshop_est: 50, price_source: "book", price_confirmed: false, is_net_item: true }),
    line({ part_name: "   ", price_source: "book", price_confirmed: false }),
    line({ part_name: "Boot lid", workshop_est: 0, price_source: "approved", price_confirmed: false }),
  ];
  assert.deepEqual(P.unconfirmedPriceLines(items), [
    { index: 1, wording: "Tail lamp LH", price_source: "mine", unit_price: 150 },
    { index: 2, wording: "Clips", price_source: "book", unit_price: 5 },
    { index: 4, wording: "Boot lid", price_source: "approved", unit_price: null },
  ]);
});

check('typing a price: "typed" and confirmed, on a line that had a source', () => {
  assert.deepEqual(P.typedPricePatch({ price_source: "mine", price_confirmed: false }), { price_source: "typed", price_confirmed: true });
  assert.deepEqual(P.typedPricePatch({ price_source: null, price_confirmed: true }), { price_source: "typed", price_confirmed: true });
});

check('confirmAllLabel and sourceWord: "Confirm all prices (3)", "Your price" / "Approved" / "Book"', () => {
  assert.equal(P.confirmAllLabel(3), "Confirm all prices (3)");
  assert.equal(P.sourceWord("mine"), "Your price");
  assert.equal(P.sourceWord("approved"), "Approved");
  assert.equal(P.sourceWord("book"), "Book");
  assert.equal(P.sourceWord(null), "");
});

// ---- Review state and the unsaved check ----------------------------------------------------

check("review: an unconfirmed price is check, whatever its origin; [✓] Confirm makes it the person's", () => {
  const row = line({ origin: "typed", prefilled: false, price_source: "mine", price_confirmed: false });
  assert.equal(RR.annexAState(row), "check");
  assert.equal(RR.annexAConfirmable(row), true);
  const patch = RR.confirmRow(row);
  assert.deepEqual(patch, { prefilled: false, origin: "typed", price_confirmed: true });
  assert.equal(RR.annexAState({ ...row, ...patch }), "you");
});

check("review: an old line is judged as before (typed -> you; prefilled -> check); Confirm adds no new field", () => {
  assert.equal(RR.annexAState(line({ origin: "typed" })), "you");
  assert.equal(RR.annexAState(line({ prefilled: true, origin: "history" })), "check");
  assert.deepEqual(RR.confirmRow(line({ prefilled: true, origin: "history" })), { prefilled: false, origin: "typed" });
  assert.equal(RR.annexAState(line({ workshop_est: 0, price_source: "book", price_confirmed: false })), "needs", "no price: needs, as before");
});

check("review as the API's (review.annex_a_state): a filled price is check until confirmed, then you -- prefilled or not", () => {
  // As the machine fills it (prices._filled / prefill._rows_for): prefilled
  // true, origin history, price_confirmed false.
  const filled = (source, over = {}) => line({ prefilled: true, origin: "history", price_source: source, price_confirmed: false, ...over });
  for (const s of ["mine", "approved", "book"]) {
    const row = filled(s);
    assert.equal(RR.annexAState(row), "check", s);
    assert.equal(RR.isPastCasePrice(row), s === "book", `${s}: only the book's is a past-case price`);
    assert.equal(RR.annexAConfirmable(row), true, s);
    // "Confirm all prices" sets price_confirmed and leaves prefilled set.
    const confirmed = { ...row, price_confirmed: true };
    assert.equal(RR.annexAState(confirmed), "you", `${s} confirmed: you`);
    assert.equal(RR.isPastCasePrice(confirmed), false, `${s} confirmed: no longer a past-case price`);
    assert.equal(RR.annexAConfirmable(confirmed), false, `${s} confirmed: nothing to confirm`);
  }
  const t = RR.annexATally([filled("book"), filled("mine"), filled("book", { price_confirmed: true }), line({ prefilled: true, origin: "history" })]);
  assert.deepEqual(t, { needsPrice: 0, pastPrice: 2, check: 1, you: 1 }, "an old prefilled line is a past-case price, as before");
  assert.equal(RR.annexAState(filled("book", { stale: true, price_confirmed: true })), "check", "stale first, as the API");
});

check("unsaved check: a confirm or a changed source is a change to save; the same line twice isn't", () => {
  const a = line({ price_source: "book", price_confirmed: false });
  assert.equal(AR.annexSignature([a]), AR.annexSignature([{ ...a }]));
  assert.notEqual(AR.annexSignature([a]), AR.annexSignature([{ ...a, price_confirmed: true }]));
  assert.notEqual(AR.annexSignature([a]), AR.annexSignature([{ ...a, price_source: "typed" }]));
  assert.equal(AR.annexSignature([line()]), AR.annexSignature([line({ price_source: undefined, price_confirmed: undefined })]));
});

// ---- After a save: the toast ------------------------------------------------------------------

const REM = (over = {}) => ({ id: 7, wording: "Rear bumper fascia", make: "honda", model_key: "vezel", unit_price: 400, previous_unit_price: null, ...over });

check("parseRemembered: the save's `remembered`; [] from an older server; bad entries left out", () => {
  assert.deepEqual(P.parseRemembered({ annex_a: { items: [] } }), []);
  assert.deepEqual(P.parseRemembered(null), []);
  assert.deepEqual(P.parseRemembered({ remembered: "yes" }), []);
  const got = P.parseRemembered({ remembered: [REM(), { id: 8 }, { unit_price: 5 }, "x", REM({ id: "a1", previous_unit_price: 380, wording: undefined })] });
  assert.deepEqual(got, [REM(), { id: "a1", wording: "", make: "honda", model_key: "vezel", unit_price: 400, previous_unit_price: 380 }]);
});

check('toast: "Saved as your price for Honda Vezel · Rear bumper fascia ($400)."', () => {
  assert.equal(P.rememberedToastText([REM()]), "Saved as your price for Honda Vezel · Rear bumper fascia ($400).");
  assert.equal(P.rememberedToastText([REM({ make: "", model_key: "" })]), "Saved as your price for Rear bumper fascia ($400).");
  assert.equal(P.rememberedToastText([REM({ unit_price: 1250.5 })]), "Saved as your price for Honda Vezel · Rear bumper fascia ($1,250.50).");
});

check('toast: several -> "Saved 3 prices as yours."; none -> ""', () => {
  assert.equal(P.rememberedToastText([REM(), REM({ id: 8 }), REM({ id: 9 })]), "Saved 3 prices as yours.");
  assert.equal(P.rememberedToastText([]), "");
});

check("toast's Undo: back to the price before, or no longer remembered; failures said", () => {
  assert.equal(P.undoneText([REM({ previous_unit_price: 380 })], 0), "Undone: your price is $380 again.");
  assert.equal(P.undoneText([REM()], 0), "Undone: that price is no longer remembered.");
  assert.equal(P.undoneText([REM(), REM({ id: 8 })], 0), "Undone: 2 prices no longer remembered.");
  assert.match(P.undoneText([REM(), REM({ id: 8 })], 1), /1 of 2 couldn't be undone/);
});

// ---- Confirm all prices ---------------------------------------------------------------------------

check("confirm-prices' answer as the API sends it: {annex_a, annex_c, confirmed} -- the whole Annex A, its totals included", () => {
  const items = [line({ price_source: "mine", price_confirmed: true })];
  const annexA = { items, total_workshop_est: 400, total_adjusted_cost: 320 };
  const annexC = { workshop_total: 400, adjusted_total: 320 };
  assert.deepEqual(P.confirmedLinesFrom({ annex_a: annexA, annex_c: annexC, confirmed: 2 }), { annex_a: annexA, items, annex_c: annexC, confirmed: 2 });
  assert.equal(P.confirmedLinesFrom({ annex_a: annexA, annex_c: annexC, confirmed: 0 }).confirmed, 0);
  assert.equal(P.confirmedText(2, 5), "2 prices confirmed.", "the API's count first");
  assert.equal(P.confirmedText(1, 5), "1 price confirmed.");
  assert.equal(P.confirmedText(null, 3), "3 prices confirmed.", "the page's count when the API sent none");
  assert.equal(P.confirmedText(0, 0), "Every price is confirmed.");
});

check("confirm-prices' answer, other shapes: annex_a.items, lines, items or the list; anything else -> null (fetch again)", () => {
  const items = [line({ price_source: "mine", price_confirmed: true })];
  const none = { annex_a: null, items: null, annex_c: null, confirmed: null };
  assert.deepEqual(P.confirmedLinesFrom({ annex_a: { items }, annex_c: { total: 1 } }), { ...none, items, annex_c: { total: 1 } }, "no totals: the lines only");
  assert.deepEqual(P.confirmedLinesFrom({ lines: items }).items, items);
  assert.deepEqual(P.confirmedLinesFrom({ items }).items, items);
  assert.deepEqual(P.confirmedLinesFrom(items).items, items);
  assert.deepEqual(P.confirmedLinesFrom({ ok: true }), none);
  assert.deepEqual(P.confirmedLinesFrom({ lines: [{ index: 0, wording: "x" }] }).items, null, "lines that aren't Annex A lines");
  assert.deepEqual(P.confirmedLinesFrom({ annex_a: { items: [{ index: 0 }], total_workshop_est: 1, total_adjusted_cost: 1 } }).annex_a, null);
  assert.equal(P.confirmedLinesFrom({ confirmed: -1 }).confirmed, null);
  assert.equal(P.confirmedLinesFrom({ confirmed: "2" }).confirmed, null);
  assert.deepEqual(P.confirmedLinesFrom(null), none);
});

// ---- Fill prices --------------------------------------------------------------------------------

check("Fill prices refused (403, the price book can't go into this case): plain words; other refusals keep the API's", () => {
  const book = 'Prices from past cases are the firm\'s price book: for admins and "see all cases" staff.';
  assert.equal(P.prefillRefusal(403, book), P.PREFILL_REFUSED);
  assert.equal(P.prefillRefusal(403, null), P.PREFILL_REFUSED);
  assert.equal(P.prefillRefusal(403, "You can see this case, but you can't change it."), null);
  assert.equal(P.prefillRefusal(409, book), null);
  assert.match(P.PREFILL_REFUSED, /your own prices only go into your own cases/);
  assert.match(P.PREFILL_REFUSED, /Type the workshop's prices instead/);
});

check("Fill prices' note: how many lines, from your own prices and/or the price book, how many still unpriced", () => {
  const filled = (source) => line({ prefilled: true, origin: "history", price_source: source, price_confirmed: false });
  const unpriced = line({ part_name: "Clips", workshop_est: 0, prefilled: true, origin: "history" });
  assert.equal(P.prefillNoteText(2, 1, [filled("mine"), filled("mine"), unpriced]), "2 lines priced from your own prices; 1 still has no price.");
  assert.equal(P.prefillNoteText(3, 0, [filled("mine"), filled("book"), filled("approved")]), "3 lines priced (1 from your own prices, 2 from the price book).");
  assert.equal(P.prefillNoteText(1, 2, [filled("book"), unpriced, unpriced]), "1 line priced from past cases; 2 still have no price.");
  assert.equal(P.prefillNoteText(2, 0, [line({ prefilled: true }), line({ prefilled: true })]), "2 lines priced from past cases.", "an older server: as before");
  assert.equal(P.prefillNoteText(0, 3, []), "No prices found for the unpriced lines, in your own prices or past cases.");
});

// ---- The sign-off's 409 ------------------------------------------------------------------------------

const MISSING = [{ label: "Workshop", keys: ["insurance_details.workshop_assigned"] }];
const LINES = [{ index: 2, wording: "Tail lamp LH", price_source: "mine", unit_price: 150 }];

check("409 prices_unconfirmed alone: the lines, no must-haves", () => {
  const b = P.signOffBlock({ code: "prices_unconfirmed", detail: "2 prices to confirm.", lines: LINES });
  assert.deepEqual(b.codes, ["prices_unconfirmed"]);
  assert.deepEqual(b.missing, []);
  assert.deepEqual(b.prices, LINES);
  assert.equal(P.signOffBlockMessage(b), "2 prices to confirm.");
});

check("409 must_have alone (Phase 1, and an older server): as before, no price lines", () => {
  const b = P.signOffBlock({ code: "must_have", missing: MISSING });
  assert.deepEqual(b.codes, ["must_have"]);
  assert.deepEqual(b.missing, MISSING);
  assert.deepEqual(b.prices, []);
  assert.equal(P.signOffBlockMessage(b), "1 thing before sign-off: Workshop.");
});

check("409 with both in one answer: missing and lines, whichever code leads", () => {
  for (const code of ["must_have", "prices_unconfirmed", ["must_have", "prices_unconfirmed"]]) {
    const b = P.signOffBlock({ code, missing: MISSING, lines: LINES });
    assert.deepEqual(b.missing, MISSING, JSON.stringify(code));
    assert.deepEqual(b.prices, LINES, JSON.stringify(code));
    assert.deepEqual([...b.codes].sort(), ["must_have", "prices_unconfirmed"], JSON.stringify(code));
    assert.equal(P.signOffBlockMessage({ ...b, detail: null }), "1 thing before sign-off: Workshop. 1 price to confirm on Annex A.");
  }
});

check("409 as the API sends it: code must_have with both lists, lines non-empty -> the price lines show", () => {
  // api/main.py sign_off_report: one 409, code "must_have" when facts are
  // missing, and ALWAYS both `missing` and `lines`.
  const b = P.signOffBlock({ detail: "1 thing ... 1 price ...", code: "must_have", missing: MISSING, lines: LINES });
  assert.deepEqual(b.missing, MISSING);
  assert.deepEqual(b.prices, LINES, "the unconfirmed price lines, whatever the code");
  assert.ok(b.codes.includes("prices_unconfirmed") && b.codes.includes("must_have"));
  assert.equal(P.signOffBlockMessage(b), "1 thing ... 1 price ...");
  const onlyLines = P.signOffBlock({ detail: "x", code: "must_have", missing: [], lines: LINES });
  assert.deepEqual(onlyLines.prices, LINES, "must_have with no facts missing but lines: still shown");
  assert.deepEqual(onlyLines.missing, []);
});

check("409 as the API sends it: code must_have with lines [] -> no price lines, the must-haves as before", () => {
  const b = P.signOffBlock({ detail: "1 thing before sign-off: Workshop.", code: "must_have", missing: MISSING, lines: [] });
  assert.deepEqual(b.codes, ["must_have"]);
  assert.deepEqual(b.missing, MISSING);
  assert.deepEqual(b.prices, []);
  assert.equal(P.signOffBlockMessage(b), "1 thing before sign-off: Workshop.");
});

check("409 as the API sends it: code prices_unconfirmed with missing [] and the lines", () => {
  const b = P.signOffBlock({ detail: "1 price ...", code: "prices_unconfirmed", missing: [], lines: LINES });
  assert.deepEqual(b.codes, ["prices_unconfirmed"]);
  assert.deepEqual(b.missing, []);
  assert.deepEqual(b.prices, LINES);
});

check("409 inside detail (HTTPException(detail={...})) reads the same", () => {
  const b = P.signOffBlock({ detail: { code: "prices_unconfirmed", detail: "Confirm the prices first.", lines: LINES } });
  assert.deepEqual(b.prices, LINES);
  assert.equal(b.detail, "Confirm the prices first.");
});

check("409 lines with fields missing are still listed; open_items and other answers are not this", () => {
  const b = P.signOffBlock({ code: "prices_unconfirmed", lines: [{ wording: "Boot lid" }, null, { index: "x" }] });
  assert.deepEqual(b.prices, [
    { index: -1, wording: "Boot lid", price_source: null, unit_price: null },
    { index: -1, wording: "", price_source: null, unit_price: null },
  ]);
  assert.equal(P.signOffBlockMessage({ ...b, prices: [] }), "Some Annex A prices still need confirming.");
  assert.equal(P.signOffBlock({ code: "open_items", open: 3 }), null);
  assert.equal(P.signOffBlock({ detail: "Report is signed off and locked." }), null);
  assert.equal(P.signOffBlock(null), null);
  assert.equal(P.signOffBlock("409"), null);
});

// ---- My prices --------------------------------------------------------------------------------------

const MINE = [
  { id: 1, make: "honda", model_key: "vezel", wording: "Rear bumper fascia", side: null, unit_price: 400, updated_at: "2026-11-23T10:00:00", uses: 3, last_case_id: "abc123def456", old: false },
  { id: 2, make: "honda", model_key: "cr-v", wording: "Tail lamp", side: "LH", unit_price: 260, updated_at: "2025-08-01", uses: 1, last_case_id: null, old: true },
  { id: 3, make: "toyota", model_key: "corolla altis", wording: "Front bumper", side: null, unit_price: 350.5, updated_at: "2026-06-01", uses: 2, last_case_id: null, old: false },
];

check("GET /me/prices: {prices}, or the list; missing fields filled in; old worked out when not sent", () => {
  assert.deepEqual(P.normalizeMyPrices({ prices: MINE }, NOW), MINE);
  assert.deepEqual(P.normalizeMyPrices(MINE, NOW), MINE);
  const got = P.normalizeMyPrices({ prices: [{ id: 9, unit_price: 12, updated_at: "2025-01-01" }, { id: 10 }, { unit_price: 3 }, null] }, NOW);
  assert.deepEqual(got, [{ id: 9, make: "", model_key: "", wording: "", side: null, unit_price: 12, updated_at: "2025-01-01", uses: 0, last_case_id: null, old: true }]);
  assert.deepEqual(P.normalizeMyPrices({}, NOW), []);
  assert.deepEqual(P.normalizeMyPrices(null, NOW), []);
});

check("PATCH /me/prices/{id}: the row, or {price: row}; unreadable -> null", () => {
  assert.deepEqual(P.normalizeMyPrice(MINE[0], NOW), MINE[0]);
  assert.deepEqual(P.normalizeMyPrice({ price: MINE[1] }, NOW), MINE[1]);
  assert.equal(P.normalizeMyPrice({ ok: true }, NOW), null);
});

check("My prices: search across part, car and side; make and model filters", () => {
  const ids = (rows) => rows.map((r) => r.id);
  assert.deepEqual(ids(P.filterMyPrices(MINE, {})), [1, 2, 3]);
  assert.deepEqual(ids(P.filterMyPrices(MINE, { q: "bumper" })), [1, 3]);
  assert.deepEqual(ids(P.filterMyPrices(MINE, { q: "  HONDA vezel " })), [1]);
  assert.deepEqual(ids(P.filterMyPrices(MINE, { q: "lh" })), [2]);
  assert.deepEqual(ids(P.filterMyPrices(MINE, { make: "Honda" })), [1, 2]);
  assert.deepEqual(ids(P.filterMyPrices(MINE, { make: "honda", model: "cr-v" })), [2]);
  assert.deepEqual(ids(P.filterMyPrices(MINE, { make: "honda", q: "front" })), []);
});

check("My prices: the make and model options", () => {
  assert.deepEqual(P.makeModelOptions(MINE, ""), { makes: ["honda", "toyota"], models: ["corolla altis", "cr-v", "vezel"] });
  assert.deepEqual(P.makeModelOptions(MINE, "honda").models, ["cr-v", "vezel"]);
  assert.deepEqual(P.makeModelOptions([], ""), { makes: [], models: [] });
});

// ---- All users' prices (admins) ------------------------------------------------------------------------

const ROW = {
  id: 7,
  user_id: "u-stella",
  user_name: "Stella (@stella)",
  make: "Honda",
  model_key: "vezel",
  item_key: "rear bumper",
  part: "Rear Bumper",
  wording: "Rear bumper fascia",
  side: null,
  unit_price: 470,
  default_price: 400,
  default_source: "book",
  updated_at: "2026-11-20T08:00:00+00:00",
  uses: 3,
  last_case_id: "case-1",
};
const ROW2 = { ...ROW, id: 8, user_id: "u-una", user_name: "Una", item_key: "tail lamp", part: "Left Tail Lamp", wording: "Tail lamp LH", side: "Left", unit_price: 200, default_price: null, default_source: null };
const ROW3 = { ...ROW, id: 9, user_name: null, user_id: "u-gone", make: "Toyota", model_key: "corolla altis", default_source: "approved", default_price: 380 };

check("GET /admin/user-prices: {prices}, or the list; a row without its id, price or keys is left out", () => {
  assert.deepEqual(P.normalizeUserPrices({ prices: [ROW, ROW2] }), [ROW, ROW2]);
  assert.deepEqual(P.normalizeUserPrices([ROW]), [ROW]);
  const thin = P.normalizeUserPrices({ prices: [{ id: 1, unit_price: 5, make: "Honda", item_key: "boot lid" }, { id: 2, make: "Honda", item_key: "x" }, { unit_price: 1 }] });
  assert.deepEqual(thin, [
    { id: 1, user_id: "", user_name: null, make: "Honda", model_key: "", item_key: "boot lid", part: null, wording: "boot lid", side: null, unit_price: 5, default_price: null, default_source: null, updated_at: null, uses: 0, last_case_id: null },
  ]);
  assert.deepEqual(P.normalizeUserPrices({}), []);
  // A source without a price is no default.
  assert.equal(P.normalizeUserPrices([{ ...ROW, default_price: null, default_source: "book" }])[0].default_source, null);
  assert.deepEqual(P.normalizeUserPrice({ price: ROW }), ROW);
  assert.equal(P.normalizeUserPrice(null), null);
});

check("All users' prices: the gap to the default, the source in words", () => {
  assert.equal(P.priceGapPct(ROW), "+18%");
  assert.equal(P.priceGapPct({ unit_price: 352, default_price: 400 }), "-12%");
  assert.equal(P.priceGapPct(ROW2), null);
  assert.equal(P.defaultSourceWord("approved"), "Approved price");
  assert.equal(P.defaultSourceWord("book"), "Book");
  assert.equal(P.defaultSourceWord(null), "No default");
});

check("All users' prices: the person, make / model and search filters; the options", () => {
  const rows = [ROW, ROW2, ROW3];
  const ids = (xs) => xs.map((x) => x.id);
  assert.deepEqual(ids(P.filterUserPrices(rows, {})), [7, 8, 9]);
  assert.deepEqual(ids(P.filterUserPrices(rows, { user: "u-una" })), [8]);
  assert.deepEqual(ids(P.filterUserPrices(rows, { make: "honda" })), [7, 8]);
  assert.deepEqual(ids(P.filterUserPrices(rows, { make: "TOYOTA", model: "Corolla Altis" })), [9]);
  assert.deepEqual(ids(P.filterUserPrices(rows, { q: " left tail " })), [8], "the standard part name");
  assert.deepEqual(ids(P.filterUserPrices(rows, { q: "stella" })), [7], "the person");
  assert.deepEqual(ids(P.filterUserPrices(rows, { q: "honda vezel" })), [7, 8], "the car as shown");
  assert.deepEqual(P.userOptions(rows), [
    { id: "u-gone", name: "A removed account" },
    { id: "u-stella", name: "Stella (@stella)" },
    { id: "u-una", name: "Una" },
  ]);
  assert.deepEqual(P.userPriceCarOptions(rows, ""), { makes: ["Honda", "Toyota"], models: ["corolla altis", "vezel"] });
  assert.deepEqual(P.userPriceCarOptions(rows, "honda").models, ["vezel"]);
});

check("Use this price for everyone: the person's price unless edited, a price above 0, a blank reason left out", () => {
  assert.deepEqual(P.promoteBody(ROW, "470", " "), {});
  assert.deepEqual(P.promoteBody(ROW, "$455.50", "  new supplier "), { unit_price: 455.5, reason: "new supplier" });
  assert.match(P.promoteBody(ROW, "", "").error, /price per unit/);
  assert.match(P.promoteBody(ROW, "0", "").error, /above 0/);
  assert.equal(P.promoteBody(ROW, "470", "x".repeat(600)).reason.length, 500);
});

check("the retired Price differences list is gone from the page and the API calls", () => {
  for (const rel of ["../lib/prices.ts", "../lib/api.ts", "../app/knowledge/KnowledgeClient.tsx", "../app/knowledge/actions.ts"]) {
    const text = source(rel);
    assert.doesNotMatch(text, /price-differences|PriceDifference|decidePriceDifference|"update" \| "keep"/, rel);
  }
  assert.ok(!existsSync(fileURLToPath(new URL("../app/knowledge/PriceDifferencesTab.tsx", import.meta.url))), "PriceDifferencesTab.tsx removed");
});

// ---- Line ids (Stage 1 review H2) ---------------------------------------------------------------------------

check("Annex A line ids: sent back exactly as the API gave them, never made by the page", () => {
  const item = { ...line(), line_id: "L0123456789ab" };
  assert.equal(AR.fromAnnexRow(AR.toAnnexRow(item)).line_id, "L0123456789ab");
  assert.equal("line_id" in AR.fromAnnexRow(AR.toAnnexRow(line())), false, "a line without one is sent without one");
  assert.equal("line_id" in AR.BLANK_ANNEX_LINE, false, "a new blank line has none");
  // Typing a price or confirming keeps it (the patches never name it).
  assert.equal("line_id" in P.typedPricePatch({ price_source: "book", price_confirmed: false }), false);
  const api = source("../lib/api.ts");
  assert.match(api, /line_id\?: string \| null;/);
  // The page never sets one: nothing in the editors writes it.
  for (const rel of ["../components/AnnexATab.tsx", "../lib/annexRows.ts", "../lib/caseState.ts", "../lib/catalogue.ts", "../components/PriceSource.tsx"]) {
    assert.doesNotMatch(source(rel).replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, ""), /line_id\s*[:=]/, rel);
  }
});

// ---- The screens use them (from the source) ----------------------------------------------------------------

check("lib/api.ts: the Stage 1 routes, each id through seg()", () => {
  const api = source("../lib/api.ts");
  for (const route of [
    "${API_BASE_URL}/reports/${seg(reportId)}/annex-a/confirm-prices",
    "${API_BASE_URL}/me/prices${query}",
    "${API_BASE_URL}/me/prices/${seg(id)}",
    "${API_BASE_URL}/me/prices/${seg(id)}/undo",
    "${API_BASE_URL}/admin/user-prices${query}",
    "${API_BASE_URL}/admin/user-prices/${seg(id)}/promote",
  ]) {
    assert.ok(api.includes("`" + route), route);
  }
  assert.match(api, /method: "PATCH"[\s\S]{0,200}unit_price: unitPrice/);
  assert.match(api, /remembered: parseRemembered\(body\)/);
  assert.match(api, /signOffBlock\(body\)/);
});

check("Confirm all prices puts the API's annex_a / annex_c on the page as they are; the note uses its count", () => {
  const hook = source("../components/PriceSource.tsx");
  assert.match(hook, /if \(res\.annex_a\) \{[\s\S]{0,200}store\.applySaved\("annexA", \{ annex_a: res\.annex_a, \.\.\.annexC \}\)/);
  assert.match(hook, /return \{ ok: true, confirmed: res\.confirmed \}/);
  const tsx = source("../components/AnnexATab.tsx");
  assert.match(tsx, /confirmedText\(res\.confirmed, unconfirmedCount\)/);
});

check("Fill prices: shown to everyone who may edit Annex A, not only price-book staff; a 403 in plain words", () => {
  const tsx = source("../components/AnnexATab.tsx");
  const at = tsx.indexOf("onClick={() => void handlePrefill()}");
  assert.ok(at > 0, "the Fill prices button");
  const before = tsx.slice(Math.max(0, tsx.lastIndexOf("{!readOnly && (", at)), at);
  assert.doesNotMatch(before, /\{canViewAllCases\(user\) && \(/, "no staff-only gate around the button");
  assert.match(tsx, /prefillNoteText\(res\.filled, res\.unpriced, res\.annex_a\?\.items \|\| \[\]\)/);
  const api = source("../lib/api.ts");
  assert.match(api, /annex-a\/prefill`[\s\S]{0,200}res\.status === 403[\s\S]{0,200}prefillRefusal\(err\.status, err\.detail\)/);
});

check("case history: plain-English labels for the Stage 1 actions", () => {
  const tsx = source("../components/CaseRecordDrawer.tsx");
  for (const action of [
    "annex_a_prices_confirmed",
    "my_price_changed",
    "my_price_deleted",
    "my_price_undone",
    "book_price_promoted",
    "price_book_personal_data_dropped",
  ]) {
    assert.match(tsx, new RegExp(`\\n\\s*${action}: "[A-Z][^"]+",`), action);
  }
});

check("Annex A tab: the tag under each price, Confirm all prices (N), the toast after a save", () => {
  const tsx = source("../components/AnnexATab.tsx");
  assert.match(tsx, /<PriceSourceTag row=\{item\} \/>/);
  assert.match(tsx, /confirmAllLabel\(unconfirmedCount\)/);
  assert.match(tsx, /unconfirmedCount > 0 &&/);
  assert.match(tsx, /res\.remembered\?\.length/);
  assert.match(tsx, /<RememberedPricesToast/);
  assert.match(tsx, /typedPricePatch\(item\)/);
  assert.match(tsx, /confirmRow\(item\)/);
});

check("sign-off box: the price lines and [Confirm all prices] beside Phase 1's must-have list, one 409 for both", () => {
  const tsx = source("../components/AnnexCTab.tsx");
  const must = tsx.indexOf("<MustHaveList reportId");
  const prices = tsx.indexOf("<PricesToConfirmList");
  assert.ok(must > 0 && prices > must, "PricesToConfirmList comes right after MustHaveList");
  assert.match(tsx, /unconfirmedPriceLines\(store\.annexABaseline\)/);
  assert.match(tsx, /res\.mustHave \|\| res\.unconfirmedPrices/);
  assert.match(tsx, /missing\.length > 0 \|\| pricesToConfirm\.length > 0/);
  // The API's lines go on the box whatever the code (must_have or
  // prices_unconfirmed): from the 409 to the error, the action and the box.
  assert.match(tsx, /setRefusedPrices\(\{ base: store\.annexABaseline, lines: res\.unconfirmedPrices \?\? \[\] \}\)/);
  assert.match(source("../lib/api.ts"), /new MustHaveError\(signOffBlockMessage\(block\), block\.missing, block\.prices, code\)/);
  const actions = source("../app/reports/actions.ts");
  assert.match(actions, /if \(err instanceof MustHaveError\) return \{ error: err\.message, mustHave: err\.missing, unconfirmedPrices: err\.unconfirmedPrices \}/);
});

check("Settings: My prices for every account, beside My settings; the admin tabs only for admins", () => {
  const tabs = source("../app/settings/SettingsTabs.tsx");
  assert.match(tabs, /href: "\/settings\/my-prices", label: "My prices", icon: Tags, adminOnly: false/);
  assert.match(tabs, /admin \|\| !tab\.adminOnly/);
  const layout = source("../app/settings/layout.tsx");
  assert.match(layout, /<SettingsTabs admin=\{admin\} \/>/);
  assert.doesNotMatch(layout, /\{admin && <SettingsTabs/);
  assert.ok(existsSync(fileURLToPath(new URL("../app/settings/my-prices/page.tsx", import.meta.url))), "app/settings/my-prices/page.tsx");
});

check("Knowledge: the All users' prices tab for admins only, with its filters and Use this price for everyone", () => {
  const tsx = source("../app/knowledge/KnowledgeClient.tsx");
  assert.match(tsx, /id: "user_prices", label: "All users' prices", icon: Users, adminOnly: true/);
  assert.match(tsx, /admin \|\| !t\.adminOnly/);
  assert.match(tsx, /tab === "user_prices" && admin && <AllUserPricesTab \/>/);
  const tab = source("../app/knowledge/AllUserPricesTab.tsx");
  assert.match(tab, /aria-label="Filter by person"/);
  assert.match(tab, /aria-label="Filter by make"/);
  assert.match(tab, /aria-label="Filter by model"/);
  assert.match(tab, /aria-label="Search everyone's prices"/);
  assert.match(tab, /Use this price for everyone/);
  assert.match(tab, /promoteBody\(r, priceTyped, reason\)/);
  assert.match(tab, /if \(!confirm\(/, "a confirm before it is used for everyone");
  assert.match(tab, /promoteUserPriceAction\(r\.id, body\)/);
  const actions = source("../app/knowledge/actions.ts");
  assert.match(actions, /listUserPrices\(clean\)/);
  assert.match(actions, /promoteUserPrice\(id, clean\)/);
  // The Knowledge page's case list shows no file name (it held the plate).
  assert.doesNotMatch(source("../app/knowledge/PartsHistoryTab.tsx"), /c\.file/);
});

check("globals.css: the Stage 1 block once, at the end", () => {
  const css = source("../app/globals.css");
  const start = "/* ---- Stage 1: prices ---- */";
  const end = "/* ---- end Stage 1 ---- */";
  assert.equal(css.split(start).length, 2, "one start marker");
  assert.equal(css.split(end).length, 2, "one end marker");
  assert.equal(css.trimEnd().endsWith(end), true, "the block is last");
  const outside = css.slice(0, css.indexOf(start));
  assert.doesNotMatch(outside, /\.s1-[\w-]+\s*[{,]/, "no s1- rule outside the block");
});

if (failed) {
  console.log(`\n${failed} check(s) failed`);
  process.exit(1);
}
console.log("\nALL PRICES UI CHECKS PASSED");
