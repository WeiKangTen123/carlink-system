#!/usr/bin/env node
/**
 * Checks Phase 1's report checks on the dashboard side (owner-approved,
 * 23 Nov 2026): the Cover Page's soft checks in lib/coverChecks.ts (dates,
 * plate, chassis, engine no., speedometer, tyres, point of impact against
 * the damaged parts) and the must-have helpers in lib/review.ts (which
 * fields are on the sign-off list, what a blank one shows, what is still
 * missing), on made-up values only.
 *
 *   node apps/dashboard/scripts/check-cover-checks.mjs
 *
 * Prints one line per check and exits 1 if any fails. Needs Node with
 * built-in type stripping (22.18+ / 24) to import the .ts files.
 */
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

process.removeAllListeners("warning");
process.on("warning", (w) => {
  if (w.code !== "MODULE_TYPELESS_PACKAGE_JSON") console.error(w.stack ?? w.message);
});
registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith(".") && !/\.[cm]?[jt]s$/.test(specifier) && context.parentURL) {
      const url = new URL(`${specifier}.ts`, context.parentURL);
      if (existsSync(fileURLToPath(url))) return next(url.href, context);
    }
    return next(specifier, context);
  },
});

const C = await import("../lib/coverChecks.ts");
const R = await import("../lib/review.ts");
const { coverFormFrom } = await import("../lib/coverForm.ts");

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

const TODAY = new Date(2026, 10, 23); // 23 Nov 2026, local time
/** A made-up cover form: every field blank but the ones given. */
const form = (values = {}) => ({ ...coverFormFrom({}), ...values });
const checksOf = (values, damage = []) => C.coverChecks(form(values), damage, TODAY);

// ---------------------------------------------------------------------------
console.log("-- reading dates");
check("the firm's style, with ordinals and a time", () => {
  assert.deepEqual(C.parseCoverDate("13th January 2026 (9.30am)"), { y: 2026, m: 1, d: 13 });
  assert.deepEqual(C.parseCoverDate("1st Jan 2026"), { y: 2026, m: 1, d: 1 });
  assert.deepEqual(C.parseCoverDate("3 Sept 2025"), { y: 2025, m: 9, d: 3 });
  assert.deepEqual(C.parseCoverDate("January 13, 2026"), { y: 2026, m: 1, d: 13 });
});
check("day first with / - . and two-digit years; ISO and the AI's datetime", () => {
  assert.deepEqual(C.parseCoverDate("02/03/2026"), { y: 2026, m: 3, d: 2 });
  assert.deepEqual(C.parseCoverDate("2-3-26"), { y: 2026, m: 3, d: 2 });
  assert.deepEqual(C.parseCoverDate("2026-11-01"), { y: 2026, m: 11, d: 1 });
  assert.deepEqual(C.parseCoverDate("2026-11-01T14:30:00"), { y: 2026, m: 11, d: 1 });
  assert.deepEqual(C.parseCoverDate("2026-11-01 09:00"), { y: 2026, m: 11, d: 1 });
});
check("not a date: no reading (and so no check)", () => {
  for (const text of ["", "Not applicable", "31/02/2026", "Smarch 3 2026", "tomorrow", "13/13/2026"]) {
    assert.equal(C.parseCoverDate(text), null, text);
  }
});

console.log("-- dates in a sensible order");
check("a date in the future", () => {
  const c = checksOf({ date_of_survey: "24th November 2026", date_of_request: "23rd November 2026" });
  assert.deepEqual(c.date_of_survey, ["In the future"]);
  assert.equal(c.date_of_request, undefined, "today is not the future");
});
check("accident <= request <= survey", () => {
  const c = checksOf({ incident_datetime: "2026-11-10", date_of_request: "9th November 2026", date_of_survey: "8th November 2026" });
  assert.deepEqual(c.date_of_request, ["Before the accident date"]);
  assert.deepEqual(c.date_of_survey, ["Before the date of request"]);
  const ok = checksOf({ incident_datetime: "2026-11-10T08:00", date_of_request: "10/11/2026", date_of_survey: "12th November 2026" });
  assert.equal(ok.date_of_request, undefined);
  assert.equal(ok.date_of_survey, undefined);
  const noRequest = checksOf({ incident_datetime: "2026-11-10", date_of_survey: "1st November 2026" });
  assert.deepEqual(noRequest.date_of_survey, ["Before the accident date"]);
});
check("dismantling and painting after the accident, painting in order; registration before it", () => {
  const c = checksOf({
    incident_datetime: "10th November 2026",
    date_of_dismantle: "9th November 2026",
    date_before_painting: "15th November 2026",
    date_after_painting: "14th November 2026",
    date_of_registration: "11th November 2026",
  });
  assert.deepEqual(c.date_of_dismantle, ["Before the accident date"]);
  assert.equal(c.date_before_painting, undefined);
  assert.deepEqual(c.date_after_painting, ["Before the date of before painting"]);
  assert.deepEqual(c.date_of_registration, ["After the accident date"]);
});
check("a date not read as one is never checked; blank forms have no checks", () => {
  assert.deepEqual(checksOf({ incident_datetime: "Night of the storm", date_of_survey: "1st January 2020" }), {});
  assert.deepEqual(checksOf({}), {});
});

console.log("-- vehicle facts");
check("plate: letters, digits, maybe a letter; warns otherwise", () => {
  for (const plate of ["WXY 1234", "wxy1234", "QAA 1234 A", "TST 99 Z", "V 1"]) assert.equal(checksOf({ plate_number: plate }).plate_number, undefined, plate);
  for (const plate of ["TESTX 1234", "1234 WXY", "WXY-1234-AB", "?"]) assert.equal(checksOf({ plate_number: plate }).plate_number?.length, 1, plate);
});
check("chassis: 17 characters without I/O/Q, or a frame number; warns otherwise", () => {
  assert.equal(checksOf({ vin: "1HGBH41JXMN109186" }).vin, undefined);
  assert.equal(checksOf({ vin: "rv1-1234567" }).vin, undefined, "a frame number");
  assert.match(checksOf({ vin: "1HGBH41JXMN10918" }).vin[0], /^16 characters: a VIN has 17/);
  assert.match(checksOf({ vin: "1HGBH41JXMN1O9186" }).vin[0], /never has I, O or Q/);
});
check("engine no.: letters and digits; not a word in its place", () => {
  assert.equal(checksOf({ engine_number: "TST15B1234567" }).engine_number, undefined);
  for (const engine of ["N/A", "unknown", "ABCDEF", "A1", "-"]) assert.equal(checksOf({ engine_number: engine }).engine_number?.length, 1, engine);
});
check("speedometer: the digits, then Km", () => {
  for (const odo of ["012345 Km", "12,345 km", "12345km"]) assert.equal(checksOf({ odometer_reading: odo }).odometer_reading, undefined, odo);
  assert.match(checksOf({ odometer_reading: "12345" }).odometer_reading[0], /then Km/);
  assert.match(checksOf({ odometer_reading: "12345 miles" }).odometer_reading[0], /then Km/);
  assert.match(checksOf({ odometer_reading: "not visible" }).odometer_reading[0], /^No digits/);
});
check("tyres: below 1.6 mm, and a depth that isn't a number", () => {
  const c = checksOf({ front_nearside_mm: "1.2mm", front_offside_mm: "1.6", rear_nearside_mm: "7", rear_offside_mm: "worn" });
  assert.deepEqual(c.front_nearside_mm, ["Front nearside 1.2 mm: below the 1.6 mm legal minimum"]);
  assert.equal(c.front_offside_mm, undefined, "1.6 is the minimum, not below it");
  assert.equal(c.rear_nearside_mm, undefined);
  assert.match(c.rear_offside_mm[0], /not a depth in mm/);
  assert.equal(C.tyreChecks(c).length, 2);
});

console.log("-- point of impact against the damaged parts");
check("the impact's words", () => {
  assert.deepEqual(C.impactAreas("Rear centre"), { end: "rear", side: null });
  assert.deepEqual(C.impactAreas("Front LH quarter"), { end: "front", side: "lh" });
  assert.deepEqual(C.impactAreas("Offside"), { end: null, side: "rh" });
  assert.deepEqual(C.impactAreas("Front and rear"), { end: null, side: null });
});
check("the parts' areas: ends from their names, doors only a side", () => {
  assert.deepEqual(C.partAreas("Front Bumper"), { end: "front", side: null });
  assert.deepEqual(C.partAreas("Right Front Fender"), { end: "front", side: "rh" });
  assert.deepEqual(C.partAreas("Left Front Door"), { end: null, side: "lh" });
  assert.deepEqual(C.partAreas("Rear Windscreen"), { end: "rear", side: null });
  assert.deepEqual(C.partAreas("Boot Lid"), { end: "rear", side: null });
  assert.deepEqual(C.partAreas("Left Tail Lamp"), { end: "rear", side: "lh" });
  assert.deepEqual(C.partAreas("Bonnet"), { end: "front", side: null });
  assert.deepEqual(C.partAreas("Roof"), { end: null, side: null });
});
check("rear impact, every part at the front: a check", () => {
  const damage = [{ part: "Front Bumper" }, { part: "Bonnet" }, { part: "Left Headlamp" }, { part: "Left Front Door" }];
  const c = checksOf({ point_of_impact: "Rear centre" }, damage);
  assert.equal(c.point_of_impact.length, 1);
  assert.match(c.point_of_impact[0], /^Says rear, but every damaged part listed with an end is at the front/);
});
check("an impact the parts agree with, or no parts: no check", () => {
  const damage = [{ part: "Rear Bumper" }, { part: "Boot Lid" }, { part: "Front Bumper" }];
  assert.equal(checksOf({ point_of_impact: "Rear centre" }, damage).point_of_impact, undefined);
  assert.equal(checksOf({ point_of_impact: "Rear centre" }, []).point_of_impact, undefined);
  assert.equal(checksOf({ point_of_impact: "Rear centre" }, [{ part: "Roof" }]).point_of_impact, undefined);
});
check("the side too: LH impact, every sided part on the RH", () => {
  const damage = [{ part: "Right Front Door" }, { part: "Right Wing Mirror" }, { part: "Front Bumper" }];
  const c = checksOf({ point_of_impact: "Front LH quarter" }, damage);
  assert.deepEqual(c.point_of_impact, ["Says LH, but every damaged part listed with a side is on the RH"]);
});
check("an Other part counts by its typed name", () => {
  const damage = [{ part: "Other / Not Listed", other_part_name: "Rear air vent" }];
  assert.equal(checksOf({ point_of_impact: "Rear centre" }, damage).point_of_impact, undefined);
  assert.equal(checksOf({ point_of_impact: "Front" }, damage).point_of_impact?.length, 1);
});

console.log("-- the sign-off's must-have list (lib/review.ts)");
const review = {
  fields: {},
  rules: { "cover_page.requested_by": "office", "insurance_details.workshop_assigned": "guess", "vehicle_info.color": "guess" },
  damage: [],
  annex_a: [],
  counts: {},
  open: 0,
  must_have: [
    { label: "Requested by", keys: ["cover_page.requested_by"] },
    { label: "Workshop", keys: ["insurance_details.workshop_assigned"] },
    { label: "Make & Model", keys: ["vehicle_info.make", "vehicle_info.model"] },
  ],
  before_sign_off: [{ label: "Workshop", keys: ["insurance_details.workshop_assigned"] }],
};
check("which keys are on the list", () => {
  assert.equal(R.isMustHave(review, "insurance_details.workshop_assigned"), true);
  assert.equal(R.isMustHave(review, "vehicle_info.model"), true);
  assert.equal(R.isMustHave(review, "vehicle_info.color"), false);
  assert.equal(R.isMustHave(null, "vehicle_info.model"), false, "an older API: none");
});
check("a blank field's state: a must-have guess needs a person, another guess none", () => {
  assert.equal(R.blankState(review, "insurance_details.workshop_assigned"), "needs");
  assert.equal(R.blankState(review, "cover_page.requested_by"), "needs");
  assert.equal(R.blankState(review, "vehicle_info.color"), "none");
  assert.equal(R.localState(review, "insurance_details.workshop_assigned", "", "Workshop One"), "needs", "emptied while editing");
});
check("what is still missing before sign-off", () => {
  assert.deepEqual(R.beforeSignOff(review), [{ label: "Workshop", keys: ["insurance_details.workshop_assigned"] }]);
  assert.deepEqual(R.beforeSignOff({ ...review, before_sign_off: undefined }), [], "an older API: nothing to show");
});

if (failed) {
  console.log(`\n${failed} check(s) FAILED`);
  process.exit(1);
}
console.log("\nall cover checks passed");
