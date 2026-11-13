#!/usr/bin/env node
/**
 * Checks the pure helpers of lib/catalogue.ts (the parts-catalogue look-up
 * on Annex A) against scripts/fixtures/catalogue-sample.json, a GET
 * /reports/{id}/catalogue answer shaped as the contract says.
 *
 *   node apps/dashboard/scripts/check-catalogue.mjs
 *
 * Prints one line per check and exits 1 if any fails. Needs Node with
 * built-in type stripping (22.18+ / 24) to import the .ts file.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// apps/dashboard/package.json has no "type" field (Next.js owns it), so Node
// warns MODULE_TYPELESS_PACKAGE_JSON when it loads the ESM .ts file.
// Swallow just that warning; everything else still reaches stderr.
process.removeAllListeners("warning");
process.on("warning", (w) => {
  if (w.code !== "MODULE_TYPELESS_PACKAGE_JSON") console.error(w.stack ?? w.message);
});
const c = await import("../lib/catalogue.ts");

const fixture = JSON.parse(readFileSync(new URL("./fixtures/catalogue-sample.json", import.meta.url), "utf8"));
// 10:43 in Singapore on 12 Nov 2026; the fixture's job finished 10:42.
const NOW = Date.parse("2026-11-12T02:43:00Z");
const MIN = 60_000;
const iso = (ms) => new Date(ms).toISOString();

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

const state = c.normalizeCatalogue(fixture);
const bumper = state.parts[0].diagrams[0];
const tail = state.parts[1].diagrams[0];

// ---------------------------------------------------------------------------
console.log("-- reading the answer");

check("off when the API says enabled: false, or sends nothing usable", () => {
  assert.deepEqual(c.normalizeCatalogue({ enabled: false }), { enabled: false });
  assert.deepEqual(c.normalizeCatalogue(null), { enabled: false });
  assert.deepEqual(c.normalizeCatalogue("nope"), { enabled: false });
  assert.deepEqual(c.normalizeCatalogue({ enabled: "true" }), { enabled: false });
});

check("missing lists become empty lists", () => {
  const s = c.normalizeCatalogue({ enabled: true, parts: [{ source_part: "Bonnet", query: "bonnet", side: null }] });
  assert.equal(s.enabled, true);
  assert.deepEqual(s.candidates, []);
  assert.deepEqual(s.not_found, []);
  assert.deepEqual(s.failures, []);
  assert.deepEqual(s.parts[0].diagrams, []);
  assert.equal(s.job, null);
  assert.equal(s.vehicle, null);
  assert.equal(s.can_request, false);
  assert.deepEqual(s.worker, { last_seen_at: null, online: false });
});

check("the fixture reads whole", () => {
  assert.equal(state.enabled, true);
  assert.equal(state.parts.length, 3);
  assert.equal(bumper.rows.length, 9);
  assert.equal(state.vehicle.key, "microfiches:30584");
  assert.equal(state.vehicle_hint.confirmed, false);
});

// ---------------------------------------------------------------------------
console.log("-- prices");

check("EUR price ex VAT", () => {
  assert.equal(c.priceLabel({ ref_amount: 309.41, ref_currency: "EUR", ref_tax_note: "excl. VAT" }), "ref EUR 309.41 ex VAT");
});
check("SGD price with thousands, inc VAT", () => {
  assert.equal(c.priceLabel({ ref_amount: 1250, ref_currency: "sgd", ref_tax_note: "incl. VAT" }), "ref SGD 1,250.00 inc VAT");
});
check("no tax note, no currency", () => {
  assert.equal(c.priceLabel({ ref_amount: 12.3, ref_currency: "EUR", ref_tax_note: null }), "ref EUR 12.30");
  assert.equal(c.priceLabel({ ref_amount: 5, ref_currency: null, ref_tax_note: null }), "ref 5.00");
});
check("null price says so", () => {
  assert.equal(c.priceLabel({ ref_amount: null, ref_currency: "EUR", ref_tax_note: "excl. VAT" }), "no price shown");
  assert.equal(c.priceLabel({ ref_amount: Number.NaN, ref_currency: "EUR", ref_tax_note: null }), "no price shown");
});
check("an unknown tax note is kept as written", () => {
  assert.equal(c.priceLabel({ ref_amount: 1, ref_currency: "EUR", ref_tax_note: "net" }), "ref EUR 1.00 net");
});
check("site tag from the row's URL, else the source", () => {
  assert.equal(c.siteLabel("microfiches", "https://www.parts-honda.uk/part/1"), "parts-honda.uk");
  assert.equal(c.siteLabel("microfiches", "https://bmw-spare-parts.com/x"), "bmw-spare-parts.com");
  assert.equal(c.siteLabel("microfiches", null), "BMW/MINI/Honda catalogues");
  // A source this page doesn't know: its key as sent.
  assert.equal(c.siteLabel("othersite", null), "othersite");
  assert.equal(c.siteLabel("microfiches", "javascript:alert(1)"), "BMW/MINI/Honda catalogues");
  assert.equal(c.siteLabel("other", "http://insecure.example/x"), "other");
  // A row with no link of its own: its diagram's site.
  assert.equal(c.siteLabel("microfiches", null, "https://www.bmw-spare-parts.com/d/1"), "bmw-spare-parts.com");
});
check("only https links are used", () => {
  assert.equal(c.safeUrl("https://www.parts-honda.uk/a"), "https://www.parts-honda.uk/a");
  assert.equal(c.safeUrl("http://www.parts-honda.uk/a"), null);
  assert.equal(c.safeUrl("javascript:alert(1)"), null);
  assert.equal(c.safeUrl(null), null);
});
check("price tooltip says where and when, and that it never fills W/shop Est.", () => {
  const t = c.priceTitle(bumper.rows[4], NOW, c.siteLabel(bumper.rows[4].source, bumper.rows[4].url, bumper.diagram_url));
  assert.match(t, /^Reference price on parts-honda\.uk, excl\. VAT\./);
  assert.match(t, /Read 11 Nov 17:00, may be out of date\./);
  assert.match(t, /never fills W\/shop Est\./);
});

// ---------------------------------------------------------------------------
console.log("-- the catalogue worker");

check("running: 'Catalogue look-up: ready', seen when in the tooltip", () => {
  assert.equal(c.workerLabel({ online: true, last_seen_at: iso(NOW - 70_000) }, NOW), "Catalogue look-up: ready");
  assert.equal(c.workerSeen({ online: true, last_seen_at: iso(NOW - 70_000) }, NOW), "seen 1 min ago");
  assert.equal(c.workerSeen({ online: true, last_seen_at: iso(NOW - 10_000) }, NOW), "seen just now");
});
check("not running, last seen today / yesterday / earlier (Singapore time)", () => {
  // 08:12 SGT on 12 Nov = 00:12 UTC.
  assert.equal(c.workerLabel({ online: false, last_seen_at: "2026-11-12T00:12:00Z" }, NOW), "Catalogue look-up: not running (last seen 08:12 today)");
  assert.equal(c.workerLabel({ online: false, last_seen_at: "2026-11-11T00:12:00Z" }, NOW), "Catalogue look-up: not running (last seen 08:12 yesterday)");
  assert.equal(c.workerLabel({ online: false, last_seen_at: "2026-11-09T06:05:00Z" }, NOW), "Catalogue look-up: not running (last seen 9 Nov 14:05)");
  assert.equal(c.workerLabel({ online: false, last_seen_at: "2025-12-30T06:05:00Z" }, NOW), "Catalogue look-up: not running (last seen 30 Dec 2025 14:05)");
});
check("the API's online goes stale on an old copy", () => {
  assert.equal(c.workerOnline({ online: true, last_seen_at: iso(NOW - 10 * MIN) }, NOW), false);
  assert.match(c.workerLabel({ online: true, last_seen_at: iso(NOW - 10 * MIN) }, NOW), /^Catalogue look-up: not running \(last seen /);
});
check("never seen", () => {
  assert.equal(c.workerLabel({ online: false, last_seen_at: null }, NOW), "Catalogue look-up: not running (not seen yet)");
  assert.equal(c.workerLabel(null, NOW), "Catalogue look-up: not running (not seen yet)");
  assert.equal(c.workerSeen(null, NOW), null);
});

// ---------------------------------------------------------------------------
console.log("-- jobs, failures, refusals");

const job = (over) => ({ id: 1, status: "pending", created_at: "2026-11-12T02:40:00Z", claimed_at: null, finished_at: null, progress: null, error_code: null, error_text: null, ...over });
const offline = { online: false, last_seen_at: "2026-11-12T00:12:00Z" };
const online = { online: true, last_seen_at: iso(NOW - 20_000) };

check("queued, the look-up not running", () => {
  const v = c.jobStatusView(job({}), offline, NOW);
  assert.equal(v.tone, "wait");
  assert.equal(v.text, "Waiting to start: queued 10:40. The catalogue look-up is not running (last seen 08:12 today).");
  assert.equal(v.detail, "It starts by itself when the look-up runs again; tell an admin if it stays like this.");
});
check("queued, the look-up ready", () => {
  const v = c.jobStatusView(job({}), online, NOW);
  assert.equal(v.text, "Waiting to start: queued 10:40.");
  assert.equal(v.detail, null);
});
check("queued, the look-up never seen", () => {
  assert.equal(c.jobStatusView(job({}), { online: false, last_seen_at: null }, NOW).text, "Waiting to start: queued 10:40. The catalogue look-up is not running (not seen yet).");
});
check("running: looking up 3 of 5 parts", () => {
  const v = c.jobStatusView(job({ status: "running", progress: { done_parts: 2, total_parts: 5, message: "Rear Bumper: REAR BUMPER" } }), online, NOW);
  assert.equal(v.tone, "busy");
  assert.equal(v.text, "Looking up 3 of 5 parts…");
  assert.equal(v.detail, "Rear Bumper: REAR BUMPER");
  assert.equal(c.jobStatusView(job({ status: "running", progress: { done_parts: 5, total_parts: 5, message: null } }), online, NOW).text, "Looking up 5 of 5 parts…");
  assert.equal(c.jobStatusView(job({ status: "running" }), online, NOW).text, "Starting the look-up…");
});
check("done: no status line (the results carry the time)", () => {
  assert.equal(c.jobStatusView(job({ status: "done" }), online, NOW), null);
  assert.equal(c.jobStatusView(null, online, NOW), null);
});
check("cancelled", () => {
  assert.equal(c.jobStatusView(job({ status: "cancelled", finished_at: "2026-11-12T02:41:00Z" }), online, NOW).text, "Look-up cancelled 10:41 today.");
});

const CODE_TEXT = {
  needs_human: "The parts site asked for a human check; try again later.",
  site_blocked: "The parts site asked for a human check; try again later.",
  daily_cap_reached: "Today's catalogue look-ups are used up; it carries on tomorrow.",
  challenge_blocked: "The parts site asked for a human check; try again later.",
  not_supported: "The parts catalogues don't cover this make.",
  no_vehicle: "No car in the catalogues matches this make, model and year. Check them on the Cover page, or pick a car with Change car.",
  internal: "Something went wrong in the catalogue look-up. Try again; if it keeps failing, tell an admin.",
};
for (const [code, text] of Object.entries(CODE_TEXT)) {
  check(`failed job: ${code}`, () => {
    const v = c.jobStatusView(job({ status: "failed", error_code: code, error_text: "raw worker words" }), online, NOW);
    assert.equal(v.tone, "fail");
    assert.equal(v.text, text);
  });
}
check("failed job with an unknown or no code", () => {
  assert.equal(c.errorCodeText("something_new"), "The look-up failed. Try again.");
  assert.equal(c.errorCodeText(null), "The look-up failed. Try again.");
});
check("a site's failure names the site, except a site check (one sentence whichever site)", () => {
  assert.equal(c.failureText({ source: "othersite", code: "needs_human", message: null }), CODE_TEXT.needs_human);
  assert.equal(c.failureText({ source: "microfiches", code: "internal", message: null }), `BMW/MINI/Honda catalogues: ${CODE_TEXT.internal}`);
  assert.equal(c.failureText({ source: "microfiches", code: "challenge_blocked", message: null }), "The parts site asked for a human check; try again later.");
});
check("failure lines: each said once, not repeating the job's own line", () => {
  const s = { ...state, failures: [...state.failures, { source: "othersite", code: "site_blocked", message: "again" }] };
  assert.deepEqual(c.failureLines(s), [CODE_TEXT.site_blocked]);
  const failedJob = { ...s, job: job({ status: "failed", error_code: "site_blocked" }) };
  assert.deepEqual(c.failureLines(failedJob), []);
});
check("blocked reasons", () => {
  assert.equal(c.blockedReasonText("make_not_covered", { make: "Toyota", covered: ["Honda", "BMW", "MINI"] }), "No parts catalogue for Toyota yet (Honda, BMW and MINI are covered).");
  assert.equal(c.blockedReasonText("make_not_covered", { make: "Toyota" }), "No parts catalogue for Toyota yet (Honda, BMW and MINI are covered).");
  assert.equal(c.blockedReasonText("make_not_covered"), "No parts catalogue for this make yet (Honda, BMW and MINI are covered).");
  assert.equal(c.blockedReasonText("make_not_covered", { make: " Proton  Saga ", covered: ["Honda"] }), "No parts catalogue for Proton Saga yet (Honda is covered).");
  assert.equal(c.blockedReasonText("no_make_model"), "Add the make and model on the Cover page first.");
  assert.equal(c.blockedReasonText("draft"), "Finish filing the case first.");
  assert.equal(c.blockedReasonText("signed_off"), "The case is signed off.");
  assert.equal(c.blockedReasonText("readonly"), "You can look at this case but not change it.");
  assert.equal(c.blockedReasonText(null), "The look-up can't be started on this case.");
});
check("refusals of POST lookup", () => {
  assert.equal(c.lookupRefusalText("job_open", 409), "A look-up is already waiting or running for this case.");
  assert.equal(c.lookupRefusalText("no_make_model", 400), "Add the make and model on the Cover page first.");
  assert.equal(c.lookupRefusalText("no_parts", 400), "No damaged parts are recorded on tab 1, so there is nothing to look up.");
  assert.equal(c.lookupRefusalText(null, 404), "The catalogue look-up is turned off.");
  assert.equal(c.lookupRefusalText("make_not_covered", 400, { make: "Toyota", covered: ["Honda", "BMW", "MINI"] }), "No parts catalogue for Toyota yet (Honda, BMW and MINI are covered).");
  // No make known here: the API's own sentence (the same words) is shown.
  assert.equal(c.lookupRefusalText("make_not_covered", 400), null);
  assert.equal(c.lookupRefusalText(null, 500), null);
  // 429 lookup_limit: the API's words (they say how many), else plain ones.
  const said = "You've started 3 catalogue look-ups in the last 24 hours, the most for an account without staff access. Try again tomorrow, or ask an admin.";
  assert.equal(c.lookupRefusalText("lookup_limit", 429, {}, said), said);
  assert.equal(c.lookupRefusalText("lookup_limit", 429), c.LOOKUP_LIMIT_TEXT);
  assert.equal(c.lookupRefusalText("lookup_limit", 429, {}, "  "), c.LOOKUP_LIMIT_TEXT);
  assert.match(c.LOOKUP_LIMIT_TEXT, /^You've started the most catalogue look-ups allowed for now\./);
});

// ---------------------------------------------------------------------------
console.log("-- polling");

check("polls only while pending or running", () => {
  for (const status of ["pending", "running"]) assert.equal(c.shouldPoll({ ...state, job: job({ status }) }), true, status);
  for (const status of ["done", "failed", "cancelled"]) assert.equal(c.shouldPoll({ ...state, job: job({ status }) }), false, status);
  assert.equal(c.shouldPoll({ ...state, job: null }), false);
  assert.equal(c.shouldPoll({ enabled: false }), false);
  assert.equal(c.shouldPoll(null), false);
});

// ---------------------------------------------------------------------------
console.log("-- cars");

check("car line: name, years, market, site, when", () => {
  assert.equal(c.carLine(state.vehicle, c.resultsTime(state), NOW), "Honda HR-V 1.5 i-VTEC 5D · 2015–2019 · Europe · parts-honda.uk · looked up 12 Nov 10:42");
});
check("car label puts the model in front of a name without it", () => {
  assert.equal(c.carLabel({ brand: "BMW", model: "3 Series", name: "320i Sedan" }), "BMW 3 Series 320i Sedan");
  assert.equal(c.carLabel({ brand: "Honda", model: "HR-V", name: null }), "Honda HR-V");
  assert.equal(c.carLabel({ brand: null, model: null, name: null }), "Unnamed car");
  assert.equal(c.yearsLabel({ year_from: 2016, year_to: 2016 }), "2016");
  assert.equal(c.yearsLabel({ year_from: 2016, year_to: null }), "2016 on");
  assert.equal(c.yearsLabel({ year_from: null, year_to: null }), "");
});
check("twin warning", () => {
  assert.equal(c.twinWarning(state.vehicle), "Market twin of Vezel: part numbers may differ.");
  assert.equal(c.twinWarning({ twin_of: null }), null);
});
check("the AI's reading", () => {
  assert.equal(c.hintReading(state.vehicle_hint), "Honda Vezel Hybrid 2017");
});
check("results time falls back to the newest row when the latest job isn't done", () => {
  assert.equal(c.resultsTime({ ...state, job: job({ status: "running" }) }), "2026-11-12T02:42:00+00:00");
});

// ---------------------------------------------------------------------------
console.log("-- rows");

check("rows sort by callout number, no callout last", () => {
  assert.deepEqual(
    c.sortRows(bumper.rows).map((r) => r.callout),
    ["1", "2", "3", "4", "5", "6", "7", "10", null]
  );
  assert.deepEqual(c.sortRows([{ callout: "10a" }, { callout: "10" }, { callout: "2" }]).map((r) => r.callout), ["2", "10", "10a"]);
});
check("hidden rows split out", () => {
  const { shown, hidden } = c.splitRows(bumper.rows);
  assert.equal(shown.length, 8);
  assert.deepEqual(hidden.map((r) => r.id), [347]);
});
check("first 6 rows, then + N more", () => {
  const { shown } = c.splitRows(bumper.rows);
  const page = c.pageRows(shown, false);
  assert.equal(page.rows.length, 6);
  assert.equal(page.more, 2);
  assert.deepEqual(c.pageRows(shown, true), { rows: shown, more: 0 });
  assert.equal(c.pageRows(tail.rows, false).more, 0);
});
check("hidden count on the case", () => {
  assert.equal(c.hiddenCount(state), 1);
  const undone = c.withSuggestion(state, { ...bumper.rows[2], status: "suggested" });
  assert.equal(c.hiddenCount(undone), 0);
  const hidOne = c.withSuggestion(state, { ...tail.rows[0], status: "rejected" });
  assert.equal(c.hiddenCount(hidOne), 2);
  // The original is left alone.
  assert.equal(c.hiddenCount(state), 1);
});
check("parts shown: those with diagrams (the rest are 'not in catalogues')", () => {
  assert.deepEqual(c.partsShown(state).map((p) => p.source_part), ["Rear Bumper", "Tail Lamp (Left)"]);
  assert.deepEqual(state.not_found, ["Rear Number Plate"]);
});
check("match hint numbers the line as the table shows it", () => {
  const table = ["Tail lamp LH", "Rear bumper", "Clips"];
  assert.equal(c.matchHintText(bumper.rows[0].match_hint, table), '~ like line 2 "Rear bumper"');
  assert.equal(c.matchHintText(bumper.rows[0].match_hint, ["Rear  Bumper!"]), '~ like line 1 "Rear bumper"');
  assert.equal(c.matchHintText(bumper.rows[0].match_hint, []), '~ like "Rear bumper"');
  assert.equal(c.matchHintText({ kind: null }, table), "not in Annex A");
  assert.equal(c.matchHintText(null, table), null);
});
check("diagram score", () => {
  assert.equal(c.scoreLabel(1), "100% match");
  assert.equal(c.scoreLabel(0.854), "85% match");
  assert.equal(c.scoreLabel(null), null);
});

// ---------------------------------------------------------------------------
console.log("-- the button");

check("hidden when off or not known yet", () => {
  assert.equal(c.lookupButton({ enabled: false }, { locked: false, busy: null }), null);
  assert.equal(c.lookupButton(null, { locked: false, busy: null }), null);
});
check("ready", () => {
  const b = c.lookupButton(state, { locked: false, busy: null });
  assert.equal(b.label, "Look up in catalogue");
  assert.equal(b.disabled, false);
  assert.equal(b.showCancel, false);
});
check("disabled while Annex A is locked", () => {
  assert.equal(c.lookupButton(state, { locked: true, busy: null }).disabled, true);
});
check("disabled with the blocked reason as its tooltip", () => {
  const b = c.lookupButton({ ...state, can_request: false, blocked_reason: "no_make_model" }, { locked: false, busy: null });
  assert.equal(b.disabled, true);
  assert.equal(b.title, "Add the make and model on the Cover page first.");
});
check("Looking up… and Cancel while a job is open", () => {
  for (const status of ["pending", "running"]) {
    const b = c.lookupButton({ ...state, job: job({ status }) }, { locked: false, busy: null });
    assert.equal(b.label, "Looking up…");
    assert.equal(b.disabled, true);
    assert.equal(b.showCancel, true);
  }
  assert.equal(c.lookupButton({ ...state, job: job({ status: "failed" }) }, { locked: false, busy: null }).showCancel, false);
});
check("no Cancel / Hide for someone who can't change the case", () => {
  assert.equal(c.canDecide({ ...state, blocked_reason: "readonly" }), false);
  assert.equal(c.canDecide({ ...state, blocked_reason: "signed_off" }), false);
  assert.equal(c.canDecide({ ...state, blocked_reason: "no_make_model" }), true);
  assert.equal(c.lookupButton({ ...state, can_request: false, blocked_reason: "readonly", job: job({ status: "running" }) }, { locked: false, busy: null }).showCancel, false);
});

check("no damaged parts: the button is disabled with the reason, not a 400 on click", () => {
  const b = c.lookupButton({ ...state, can_request: false, blocked_reason: "no_parts" }, { locked: false, busy: null });
  assert.equal(b.disabled, true);
  assert.equal(b.title, "No damaged parts are recorded on tab 1, so there is nothing to look up.");
  assert.equal(c.blockedReasonText("no_parts"), b.title);
  assert.equal(c.lookupRefusalText("no_parts"), b.title);
  // Hide / Undo still work on a case with no parts left.
  assert.equal(c.canDecide({ ...state, blocked_reason: "no_parts" }), true);
});

// ---------------------------------------------------------------------------
console.log("-- putting a row on an Annex A line (Phase 2)");

const face = bumper.rows.find((r) => r.part_number === "04715T7WA90");
const clip = bumper.rows.find((r) => r.part_number === "91503SZ3003");
const lamp = tail.rows[0];
let seq = 0;
const draftRow = (item) => ({ ...item, _key: `k${++seq}`, _qty: String(item.qty), _est: item.workshop_est ? String(item.workshop_est) : "" });
const tableRow = (part_name, extra = {}) =>
  draftRow({ item_no: "", part_name, condition: "", action: "", qty: 1, qty_text: null, workshop_est: 0, discount_pct: 0.2, adjusted_cost: 0,
             is_net_item: false, oem_part_number: null, prefilled: false, origin: "detection", source_part: null, stale: false, ...extra });

check("the ref a line keeps: the row's facts and its diagram", () => {
  const ref = c.catalogueRefOf(face, bumper);
  assert.deepEqual(ref, {
    suggestion_id: face.id, source: face.source, part_number: "04715T7WA90", name: "FACE, RR. BUMPER",
    diagram_name: bumper.diagram_name, diagram_url: bumper.diagram_url, ref_amount: face.ref_amount,
    ref_currency: face.ref_currency, ref_tax_note: face.ref_tax_note, fetched_at: face.fetched_at,
  });
});
check("attaching sets the ref and an empty part number; name, qty and W/shop Est. untouched", () => {
  const row = tableRow("Rear bumper", { workshop_est: 900, qty: 1, prefilled: true, origin: "history", suggested_from: "2 past cases" });
  const out = c.attachToLine(row, c.catalogueRefOf(face, bumper));
  assert.equal(out.catalogue_ref.suggestion_id, face.id);
  assert.equal(out.oem_part_number, "04715T7WA90");
  for (const k of ["part_name", "qty", "workshop_est", "prefilled", "origin", "suggested_from", "_key", "_est"]) assert.equal(out[k], row[k], k);
  // A number someone typed stays; one only the previous catalogue row brought follows the new one.
  assert.equal(c.attachToLine({ ...row, oem_part_number: "OWN-1" }, c.catalogueRefOf(face, bumper)).oem_part_number, "OWN-1");
  assert.equal(c.attachToLine(out, c.catalogueRefOf(clip, bumper)).oem_part_number, "91503SZ3003");
});
check("unlinking takes the ref and the number it brought off (sent as null)", () => {
  const on = c.attachToLine(tableRow("Rear bumper"), c.catalogueRefOf(face, bumper));
  const off = c.detachFromLine(on);
  assert.equal(off.catalogue_ref, null);
  assert.equal(off.oem_part_number, null);
  assert.equal(c.detachFromLine({ ...on, oem_part_number: "OWN-1" }).oem_part_number, "OWN-1");
});
check("a new line: the tidy name, its qty if a number, unpriced, origin lookup, for the part", () => {
  const n = c.catalogueLine(clip, state.parts[0], bumper);
  assert.equal(n.part_name, "Bumper clip");
  assert.equal(c.catalogueLine({ ...clip, tidy_name: null }, state.parts[0], bumper).part_name, "CLIP, BUMPER", "no tidy name: the catalogue's");
  assert.equal(n.qty, 8);
  assert.equal(n.workshop_est, 0);
  assert.equal(n.adjusted_cost, 0);
  assert.equal(n.prefilled, false);
  assert.equal(n.origin, "lookup");
  assert.equal(n.source_part, "Rear Bumper");
  assert.equal(n.oem_part_number, "91503SZ3003");
  assert.equal(n.suggested_from, "catalogue (parts-honda.uk)");
  assert.equal(n.catalogue_ref.suggestion_id, clip.id);
  assert.equal(n.catalogue_ref.name, "CLIP, BUMPER", "the ref keeps the catalogue's own name");
  // "check wording" while the line carries the catalogue's name or its tidy name.
  assert.equal(c.needsWording(n, clip.tidy_name), true);
  assert.equal(c.needsWording(n), false, "the tidy name is only known with the row");
  assert.equal(c.needsWording({ ...n, part_name: "CLIP, BUMPER" }), true);
  assert.equal(c.needsWording({ ...n, part_name: "Rear bumper clips" }, clip.tidy_name), false);
  assert.equal(c.needsWording(tableRow("Rear bumper"), "Rear bumper"), false, "no catalogue row on it");
});
check("qty from the catalogue: a number, else 1", () => {
  assert.equal(c.catalogueQty("2"), 2);
  assert.equal(c.catalogueQty("2.0"), 2);
  assert.equal(c.catalogueQty(3), 3);
  for (const q of ["AR", "", null, undefined, "0", "1-2", "as required"]) assert.equal(c.catalogueQty(q), 1, String(q));
});
check("under the line: '# number · site', and the ref price on the price side", () => {
  const ref = c.catalogueRefOf(face, bumper);
  assert.equal(c.refLine(ref), "# 04715T7WA90 · parts-honda.uk");
  assert.equal(c.refPrice(ref), c.priceLabel(face));
  assert.match(c.refPrice(ref), /^ref EUR /);
  assert.equal(c.refPrice({ ...ref, ref_amount: null }), null);
});
check("targets: 'line N <name>' as the table numbers them, then New line", () => {
  const lines = [{ key: "a", part_name: "Tail lamp LH" }, { key: "b", part_name: "Rear bumper" }, { key: "c", part_name: " " }];
  assert.deepEqual(c.attachTargets(lines).map((t) => t.label), ["line 1 Tail lamp LH", "line 2 Rear bumper", "line 3 (no description)", "New line"]);
  assert.equal(c.attachTargets(lines).at(-1).key, c.NEW_LINE);
});
check("a ticked row starts on its match hint's line (by name), else New line", () => {
  const lines = [{ key: "a", part_name: "Tail lamp LH" }, { key: "b", part_name: "Rear  bumper" }];
  assert.equal(c.defaultTarget(face.match_hint, lines), "b");
  assert.equal(c.defaultTarget(lamp.match_hint, lines), "a");
  assert.equal(c.defaultTarget(clip.match_hint, lines), c.NEW_LINE);
  assert.equal(c.defaultTarget(face.match_hint, [{ key: "z", part_name: "Bonnet" }]), c.NEW_LINE);
  assert.equal(c.resolvedTarget("gone", lines), c.NEW_LINE);
  assert.equal(c.resolvedTarget("a", lines), "a");
  assert.equal(c.resolvedTarget(undefined, lines), c.NEW_LINE);
});
check("two ticked rows on one line clash; new lines never do", () => {
  assert.deepEqual(c.clashingTargets(["a", "b", c.NEW_LINE, c.NEW_LINE]), []);
  assert.deepEqual(c.clashingTargets(["a", "b", "a"]), ["a"]);
});
check("[Use N ticked]: onto their lines and new lines, in the draft only", () => {
  const rows = [tableRow("Tail lamp LH", { source_part: "Tail Lamp (Left)" }), tableRow("Rear bumper", { workshop_est: 900, prefilled: true, origin: "history" })];
  const found = (id) => c.findSuggestion(state, id);
  const picks = [
    { target: rows[1]._key, ...found(face.id) },
    { target: c.NEW_LINE, ...found(clip.id) },
    { target: rows[0]._key, ...found(lamp.id) },
  ];
  const out = c.applyCataloguePicks(rows, picks, draftRow);
  assert.equal(out.length, 3);
  assert.equal(out[1].catalogue_ref.suggestion_id, face.id);
  assert.equal(out[1].workshop_est, 900);
  assert.equal(out[1].prefilled, true);
  assert.equal(out[0].catalogue_ref.suggestion_id, lamp.id);
  assert.equal(out[0].oem_part_number, "33550T7WA01");
  assert.equal(out[2].part_name, "Bumper clip");
  assert.equal(out[2].workshop_est, 0);
  assert.ok(out[2]._key);
  assert.equal(rows[1].catalogue_ref, undefined, "the rows given are not changed");
  // A row already on a line of the draft is left where it is.
  const again = c.applyCataloguePicks(out, [{ target: c.NEW_LINE, ...found(face.id) }], draftRow);
  assert.equal(again.length, 3);
  // A line removed meanwhile: the pick becomes a new line.
  assert.equal(c.applyCataloguePicks([], [{ target: "gone", ...found(face.id) }], draftRow)[0].part_name, "Rear bumper face");
});
check("findSuggestion: the row with its part and diagram, or null", () => {
  const f = c.findSuggestion(state, lamp.id);
  assert.equal(f.part.source_part, "Tail Lamp (Left)");
  assert.equal(f.diagram.diagram_name, tail.diagram_name);
  assert.equal(c.findSuggestion(state, 987654), null);
});

// ---------------------------------------------------------------------------
console.log("-- Phase 3: covered makes, auto start, colour versions, a diagram once");

check("covered_makes read from the answer; the default when an older API leaves it out", () => {
  assert.deepEqual(state.covered_makes, ["Honda", "BMW", "MINI"]);
  assert.deepEqual(c.normalizeCatalogue({ enabled: true, covered_makes: ["Honda", "", 3] }).covered_makes, ["Honda"]);
  assert.deepEqual(c.normalizeCatalogue({ enabled: true }).covered_makes, ["Honda", "BMW", "MINI"]);
  assert.equal(c.makesList(["Honda", "BMW", "MINI"]), "Honda, BMW and MINI");
  assert.equal(c.makesList(["Honda", "BMW"]), "Honda and BMW");
  assert.equal(c.makesList(["Honda"]), "Honda");
});
check("make not covered: the button is greyed with the make named", () => {
  const s = { ...state, can_request: false, blocked_reason: "make_not_covered", vehicle_hint: { ...state.vehicle_hint, brand: "Toyota" } };
  const b = c.lookupButton(s, { locked: false, busy: null });
  assert.equal(b.disabled, true);
  assert.equal(b.title, "No parts catalogue for Toyota yet (Honda, BMW and MINI are covered).");
  assert.equal(c.blockedReasonText(s.blocked_reason, c.blockedContext(s)), b.title);
  // Hide / Undo / Cancel still work.
  assert.equal(c.canDecide(s), true);
  assert.match(c.lookupButton(state, { locked: false, busy: null }).title, /\(Honda, BMW and MINI\)/);
});
check("started automatically after detection", () => {
  assert.equal(c.AUTO_REQUESTED_BY, "auto (after detection)");
  assert.equal(c.autoStartedText(job({ requested_by: "auto (after detection)" })), "Started automatically after detection");
  assert.equal(c.autoStartedText(job({ requested_by: "S. Lee" })), null);
  assert.equal(c.autoStartedText(state.job), null);
  assert.equal(c.autoStartedText(null), null);
});

// Eight colour versions of one part (callout 5), as the API marks them.
const colour = (id, letter, over = {}) => ({
  ...face, id, callout: "5", part_number: `04718T7JH00Z${letter}`, name: "FACE, L. RR. BUMPER CORNE",
  tidy_name: "Rear bumper corner face LH", ref_amount: 52.1, variant_key: "04718T7JH00#5", match_hint: { kind: null }, ...over,
});
const colours = [..."BCDEFGHJ"].map((l, i) => colour(900 + i, l));
const colourRows = [face, ...colours, clip];

check("colour versions fold into one unit at the first one's place", () => {
  const units = c.foldVariants(colourRows);
  assert.deepEqual(units.map((u) => u.kind), ["row", "variants", "row"]);
  assert.equal(units[1].rows.length, 8);
  assert.equal(units[1].key, "04718T7JH00#5");
  assert.deepEqual(units[1].rows.map((r) => r.id), colours.map((r) => r.id));
  // Rows with no key stay rows; a group of one (the others hidden) is a row again.
  assert.deepEqual(c.foldVariants([face, clip]).map((u) => u.kind), ["row", "row"]);
  assert.deepEqual(c.foldVariants([face, colours[0]]).map((u) => u.kind), ["row", "row"]);
  // Hidden versions are split out before folding.
  const withHidden = [...colours.slice(0, 7), { ...colours[7], status: "rejected" }];
  const { shown } = c.splitRows(withHidden);
  assert.equal(c.foldVariants(shown)[0].rows.length, 7);
});
check("a folded group counts as one row for '+ N more'", () => {
  const units = c.foldVariants([...colourRows, ...bumper.rows.filter((r) => r.status !== "rejected" && r.id !== face.id && r.id !== clip.id)]);
  const page = c.pageRows(units, false);
  assert.equal(page.rows.length, 6);
  assert.equal(page.more, units.length - 6);
  assert.equal(units.length, 9);
});
check("the group's line: '8 colour versions', the numbers they share", () => {
  assert.equal(c.variantCountLabel(8), "8 colour versions");
  assert.equal(c.variantCountLabel(1), "1 colour version");
  assert.equal(c.variantNumberLabel(colours), "04718T7JH00Z…");
  assert.equal(c.variantNumberLabel([{ part_number: "04718T7JH00" }, { part_number: "04718T7JH00ZB" }]), "04718T7JH00…");
  assert.equal(c.variantNumberLabel([{ part_number: null }]), "no part no.");
});
check("one colour per group: ticking one unticks the others", () => {
  const sib = c.variantSiblings(colourRows, colours[2]);
  assert.equal(sib.length, 7);
  assert.ok(!sib.some((r) => r.id === colours[2].id));
  assert.deepEqual(c.variantSiblings(colourRows, face), []);
  let picks = { [face.id]: "new" };
  picks = c.tickOnly(picks, colours[0].id, "a", c.variantSiblings(colourRows, colours[0]).map((r) => r.id));
  picks = c.tickOnly(picks, colours[3].id, "b", c.variantSiblings(colourRows, colours[3]).map((r) => r.id));
  assert.deepEqual(picks, { [face.id]: "new", [colours[3].id]: "b" });
});
check("a diagram an earlier part shows: 'Same diagram as <part> above'", () => {
  assert.equal(c.sameDiagramText("Rear Bumper", 0), "Same diagram as Rear Bumper above");
  assert.equal(c.sameDiagramText("Left Tail Lamp", 1), "Same diagram as Left Tail Lamp above; only the rows not listed there are shown.");
  const s = c.normalizeCatalogue({
    enabled: true,
    parts: [{ source_part: "Rear Bumper Beam", query: "rear bumper beam", side: null, diagrams: [{ ...bumper, same_as_part: "Rear Bumper", rows: [] }] }],
  });
  assert.equal(s.parts[0].diagrams[0].same_as_part, "Rear Bumper");
  assert.deepEqual(c.partsShown(s).map((p) => p.source_part), ["Rear Bumper Beam"], "still listed, to say so");
  assert.equal(state.parts[0].diagrams[0].same_as_part, null, "absent in an older answer: null");
});
check("the tidy name is the API's, the catalogue's own name stays on the row", () => {
  assert.equal(face.name, "FACE, RR. BUMPER");
  assert.equal(face.tidy_name, "Rear bumper face");
  assert.equal(lamp.tidy_name, "Tail light assy LH");
});
check("no 'parts PC' left in what the dashboard says", () => {
  for (const file of ["../lib/catalogue.ts", "../components/CatalogueSuggestionsPanel.tsx", "../components/AnnexATab.tsx"]) {
    const text = readFileSync(new URL(file, import.meta.url), "utf8");
    assert.doesNotMatch(text, /parts PC|Parts PC|on the PC|the PC is/, file);
  }
});

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
process.exit(failed ? 1 : 0);
