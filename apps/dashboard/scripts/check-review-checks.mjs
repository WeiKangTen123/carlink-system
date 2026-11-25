#!/usr/bin/env node
/**
 * Phase 1 F (plans/2026-11-23-report-and-parts P-parts 5.2, 5.4, gaps 3, 5,
 * 6, 8, 12, 16 and the map bug): the Review step's checks on the damaged
 * parts list, lib/reviewChecks.ts --
 *   - the check before "File case" (filingProblems) and "Needs you";
 *   - "already row 3" and "the other side?" before an add (checkAdd);
 *   - undo after a remove (removeAt / restoreRemoved);
 *   - "prints as" (printsAs), against scripts/fixtures/prints-as-sample.json,
 *     which bot-service scripts/test_prints_as.py checks against filing;
 *   - the Condition picker's quick values and own word order;
 *   - the map's add: the panel's own part (partForMapAdd), first in the
 *     part picker (step 3A, scripts/check-picker.mjs);
 * and, from the source, that the Review step and the wizard use them.
 *
 *   node apps/dashboard/scripts/check-review-checks.mjs
 *
 * Prints one line per check and exits 1 if any fails. Needs Node with
 * built-in type stripping (22.18+ / 24) to import the .ts files.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

// apps/dashboard/package.json has no "type" field (Next.js owns it), so Node
// warns MODULE_TYPELESS_PACKAGE_JSON when it loads the ESM .ts files.
// Swallow just that warning; everything else still reaches stderr.
process.removeAllListeners("warning");
process.on("warning", (w) => {
  if (w.code !== "MODULE_TYPELESS_PACKAGE_JSON") console.error(w.stack ?? w.message);
});
// The lib files import each other the bundler's way ("./findings", no
// extension); Node needs the file name, so a relative import that names no
// file is tried as .ts.
registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith(".") && !/\.[cm]?[jt]s$/.test(specifier) && context.parentURL) {
      const url = new URL(`${specifier}.ts`, context.parentURL);
      if (existsSync(fileURLToPath(url))) return next(url.href, context);
    }
    return next(specifier, context);
  },
});

const R = await import("../lib/reviewChecks.ts");
const fixture = JSON.parse(readFileSync(new URL("./fixtures/prints-as-sample.json", import.meta.url), "utf8"));
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

const OTHER = "Other / Not Listed";
// A made-up list: an AI row, a hand-added row with no condition, an unnamed
// Other, a row with no part, a second Rear Bumper, a named Other.
const list = () => [
  { part: "Rear Bumper", condition: "Deformed/cut", condition_tokens: ["Deformed", "Cut"], damage_type: "Deformation", ai_confidence: "High" },
  { part: "Left Tail Lamp", severity: null, human_verified: true },
  { part: OTHER, condition: "Necessary", human_verified: true },
  { part: "", photo_reference: "@ph09", human_verified: true },
  { part: "Rear Bumper", condition: "Grazed/cut", human_verified: true },
  { part: OTHER, other_part_name: "rear air vent", condition: "Necessary", human_verified: true },
  { part: "Bonnet", damage_type: "Dent", human_verified: true },
];

// ---- the check before filing --------------------------------------------------

check("filingProblems: no part, unnamed Other, no condition, the same part twice -- in list order", () => {
  const problems = R.filingProblems(list());
  assert.deepEqual(
    problems.map((p) => [p.index, p.label, p.issues, p.sameAs]),
    [
      [1, "Left Tail Lamp", ["no_condition"], null],
      [2, OTHER, ["no_name"], null],
      [3, "(no part chosen)", ["no_part"], null],
      [4, "Rear Bumper", ["duplicate"], 0],
    ]
  );
  assert.match(problems[0].text, /no condition: its Annex A Condition will be blank/);
  assert.match(problems[1].text, /no name: it prints "Other part \(to be named\)"/);
  assert.match(problems[2].text, /left out of Annex A/);
  assert.match(problems[3].text, /same part as row 1: only row 1's condition prints/);
});

check("filingProblems: a clean list (an AI condition, a damage type, a whole value, a named Other) needs nobody", () => {
  const clean = list().filter((_, i) => [0, 5, 6].includes(i));
  assert.deepEqual(R.filingProblems(clean), []);
  assert.deepEqual(R.filingProblems([]), []);
  assert.deepEqual(R.filingProblems(undefined), []);
});

check("filingProblems: an unnamed Other with no condition lists both; two unnamed Others are 'no name' only", () => {
  const problems = R.filingProblems([{ part: OTHER }, { part: OTHER, condition: "Necessary" }]);
  assert.deepEqual(problems.map((p) => p.issues), [["no_name", "no_condition"], ["no_name"]]);
  assert.match(problems[0].text, /no name: .*; no condition/);
});

check("filingProblems: a named Other twice (any case) is a duplicate; the other side is not", () => {
  const problems = R.filingProblems([
    { part: OTHER, other_part_name: "Rear air vent", condition: "Necessary" },
    { part: OTHER, other_part_name: "rear  AIR vent", condition: "Necessary" },
    { part: "Left Tail Lamp", condition: "Cracked" },
    { part: "Right Tail Lamp", condition: "Cracked" },
  ]);
  assert.deepEqual(problems.map((p) => [p.index, p.issues, p.sameAs]), [[1, ["duplicate"], 0]]);
});

check("focusFor: part select for no part or a duplicate, the name box for an unnamed Other, else the Condition", () => {
  assert.equal(R.focusFor({ issues: ["no_part"] }), "part");
  assert.equal(R.focusFor({ issues: ["duplicate"] }), "part");
  assert.equal(R.focusFor({ issues: ["no_name", "no_condition"] }), "name");
  assert.equal(R.focusFor({ issues: ["no_condition"] }), "condition");
});

check("rowState: a hand-added row with no condition / no name / no part is 'needs', not 'you'", () => {
  const rows = list();
  assert.deepEqual(rows.map((r) => R.rowState(r)), ["ai", "needs", "needs", "needs", "you", "you", "you"]);
  assert.deepEqual(R.rowGaps(rows[1]), ["no_condition"]);
  assert.equal(R.gapWords(R.rowGaps({ part: OTHER })), "no name, no condition");
  // The AI's doubt still reads "check" once the row is complete.
  assert.equal(R.rowState({ part: "Roof", condition: "Dented", ai_confidence: "Low" }), "check");
  assert.equal(R.rowState({ part: "Roof", condition: "Dented", side_inferred: true }), "check");
});

// ---- duplicate and other-side warnings ---------------------------------------------

check("checkAdd: the same part -> its first row and condition; the words as the dialog says them", () => {
  const c = R.checkAdd(list(), "Rear Bumper");
  assert.deepEqual(c, { kind: "duplicate", part: "Rear Bumper", index: 0, condition: "Deformed/cut" });
  assert.equal(R.addCheckText(c), "Rear Bumper is already row 1 (Deformed/cut).");
  // A row with no condition is named without one; a damage type stands in.
  assert.equal(R.addCheckText(R.checkAdd(list(), "Left Tail Lamp")), "Left Tail Lamp is already row 2.");
  assert.equal(R.checkAdd(list(), "Bonnet").condition, "Dent");
  assert.equal(R.checkAdd([{ part: "Roof", condition_tokens: ["Dented", "Bent"] }], "Roof").condition, "Dented/bent");
});

check("checkAdd: the other side -> 'The list has Left Tail Lamp. Adding Right Tail Lamp — is it the other side?'", () => {
  const c = R.checkAdd(list(), "Right Tail Lamp");
  assert.deepEqual(c, { kind: "mirror", part: "Right Tail Lamp", index: 1, listed: "Left Tail Lamp" });
  assert.equal(R.addCheckText(c), "The list has Left Tail Lamp. Adding Right Tail Lamp — is it the other side?");
  assert.equal(R.mirrorOf("Right Front Door"), "Left Front Door");
  assert.equal(R.mirrorOf("Tail Lamp (side undetermined)"), null);
  assert.equal(R.mirrorOf("Bonnet"), null);
});

check("checkAdd: nothing to ask for a new part, a blank part, or an unnamed Other (named after the add)", () => {
  assert.equal(R.checkAdd(list(), "Roof"), null);
  assert.equal(R.checkAdd(list(), ""), null);
  assert.equal(R.checkAdd(list(), OTHER), null);
  assert.equal(R.checkAdd([], "Rear Bumper"), null);
  // A named Other is found by its name.
  assert.equal(R.checkAdd(list(), OTHER, "Rear Air Vent")?.index, 5);
  // A duplicate is said before its other side.
  assert.equal(R.checkAdd([{ part: "Right Tail Lamp" }, { part: "Left Tail Lamp" }], "Left Tail Lamp")?.kind, "duplicate");
});

// ---- undo after a remove -------------------------------------------------------------

check("removeAt / restoreRemoved: the same row object back in its place, evidence and all", () => {
  const rows = list();
  rows[4].evidence = [{ photo: "@ph04", box: [1, 2, 3, 4], box_kind: "ai", by: "overall" }];
  const before = [...rows];
  const { list: after, removed } = R.removeAt(rows, 4);
  assert.equal(after.length, rows.length - 1);
  assert.equal(removed.index, 4);
  assert.equal(removed.item, rows[4]);
  const back = R.restoreRemoved(after, [removed]);
  assert.deepEqual(back, before);
  back.forEach((item, i) => assert.equal(item, before[i], `row ${i} is the same object`));
});

check("restoreRemoved: several removals undone together put every row back where it was", () => {
  const before = list();
  let cur = before;
  const removed = [];
  for (const i of [5, 0, 2]) {
    const r = R.removeAt(cur, i);
    cur = r.list;
    removed.push(r.removed);
  }
  assert.equal(cur.length, before.length - 3);
  const back = R.restoreRemoved(cur, removed);
  back.forEach((item, i) => assert.equal(item, before[i], `row ${i}`));
});

check("restoreRemoved: a place past the end (rows removed meanwhile) is the end; removeAt off the list takes nothing", () => {
  const a = { part: "Roof" };
  assert.deepEqual(R.restoreRemoved([{ part: "Bonnet" }], [{ item: a, index: 5 }])[1], a);
  assert.equal(R.removeAt([a], 3).removed, null);
  assert.equal(R.removeAt([a], -1).list.length, 1);
});

// ---- prints as ---------------------------------------------------------------------

check("printsAs: every fixture row prints what filing prints (scripts/fixtures/prints-as-sample.json)", () => {
  let n = 0;
  for (const group of fixture.vehicles) {
    for (const row of group.items) {
      const got = R.printsAs(row.item, fixture.taxonomy, group.vehicle);
      assert.equal(got.text, row.prints, `${group.vehicle.make} ${group.vehicle.model}: ${JSON.stringify(row.item)}`);
      assert.equal(got.note, row.note ?? null, `note for ${JSON.stringify(row.item)}`);
      n += 1;
    }
  }
  assert.ok(n >= 12, `${n} rows`);
});

check("printsAs: the make/model catalogue beats a stated body type; a stated one counts only when it is a body type", () => {
  const t = fixture.taxonomy;
  assert.equal(R.bodyTypeFor({ make: "Alphamotors", model: "Civic", body_type: "SUV" }, t), "Sedan");
  assert.equal(R.bodyTypeFor({ make: "Betamotors", model: "Runabout", body_type: "Hatchback" }, t), "Hatchback");
  assert.equal(R.bodyTypeFor({ make: "Betamotors", model: "Runabout", body_type: "hatch" }, t), null);
  assert.equal(R.bodyTypeFor(null, t), null);
});

check("printsAs: an older API (no wording) shows nothing for a listed part; a name off the list prints as written", () => {
  assert.deepEqual(R.printsAs({ part: "Rear Bumper" }, { model_body_types: {} }, null), { text: null, note: null });
  assert.deepEqual(R.printsAs({ part: OTHER }, null, null), { text: "Other part (to be named)", note: "unnamed" });
  assert.deepEqual(R.printsAs({ part: OTHER, other_part_name: "Wheel nut" }, null, null), { text: "Wheel nut", note: null });
  assert.deepEqual(R.printsAs({ part: "Tail lamp garnish LH", side_inferred: true }, fixture.taxonomy, null), { text: "Tail lamp garnish LH", note: null });
});

check("otherWording: the side goes last as LH / RH (prefill._other_wording)", () => {
  assert.equal(R.otherWording("Left rear air vent"), "Rear air vent LH");
  assert.equal(R.otherWording("Rear air vent (RH)"), "Rear air vent RH");
  assert.equal(R.otherWording("Offside mudflap"), "Mudflap RH");
  assert.equal(R.otherWording("Wheel nut"), "Wheel nut");
});

// ---- the Condition picker ------------------------------------------------------------

check("quickConditions: Necessary and Serviceable after the common compounds, never twice", () => {
  assert.deepEqual(R.quickConditions(["Bent", "Deformed/cut"]), ["Bent", "Deformed/cut", "Necessary", "Serviceable"]);
  assert.deepEqual(R.quickConditions(["Necessary", "Bent"]), ["Necessary", "Bent", "Serviceable"]);
  assert.deepEqual(R.quickConditions([]), ["Necessary", "Serviceable"]);
});

check("joinAsPicked: the person's order, first word capitalised ('Torn/cracked', 'Shifted/bent/cut')", () => {
  assert.equal(R.joinAsPicked(["Torn", "Cracked"]), "Torn/cracked");
  assert.equal(R.joinAsPicked(["Shifted", "Bent", "Cut"]), "Shifted/bent/cut");
  assert.equal(R.joinAsPicked(["slack", "SHIFTED"]), "Slack/shifted");
  assert.equal(R.joinAsPicked([]), "");
});

// ---- the map's "+ Add as damage" -------------------------------------------------------

check("partForMapAdd: the underbody panel pre-fills Underbody / Chassis (was Boot Floor Board), as the case page", () => {
  const parts = ["Front Bumper", "Boot Lid", "Boot Floor Board", "Underbody / Chassis", "Left Sill / Rocker Panel", "Left Front Door", OTHER];
  assert.equal(R.partForMapAdd("underbody", parts), "Underbody / Chassis");
  assert.equal(R.partForMapAdd("tailgate", parts), "Boot Lid");
  assert.equal(R.partForMapAdd("l_door_front", parts), "Left Front Door");
  // Its own part not pickable: the first that sits there, else none.
  assert.equal(R.partForMapAdd("underbody", ["Boot Floor Board"]), "Boot Floor Board");
  assert.equal(R.partForMapAdd("roof", parts), "");
});

// ---- the wiring, from the source -----------------------------------------------------

check("ReviewStep: the checks are wired in (map add, add warnings, undo, prints as, Needs you); the wrong 'unpriced' line is gone", () => {
  const tsx = source("../components/intake/ReviewStep.tsx");
  // Step 3A: the map's add opens the part picker on the panel, its own part
  // first (unless listed already) -- partForMapAdd, by way of
  // lib/partPicker.ts zonePreferred (scripts/check-picker.mjs runs it).
  assert.match(tsx, /preset: presetFromZone\(zoneKey\)/);
  assert.match(source("../lib/partPicker.ts"), /partForMapAdd\(zone, list\)/);
  assert.match(source("../components/PartPicker.tsx"), /preferNames: zonePreferred\(zone, list, rowsListed\)/);
  assert.doesNotMatch(tsx, /These become the Annex A rows, unpriced/);
  // The picker's adds (add row, map, photo viewer) and Photos by part ask first.
  assert.equal((tsx.match(/askBeforeAdd\(\s*part,\s*\(\) =>/g) || []).length, 2, "the picker's adds and Photos by part ask first");
  assert.match(tsx, /const check = checkAdd\(damage, part, otherName\)/);
  assert.match(tsx, /restoreRemoved\(/);
  assert.match(tsx, /<UndoToast/);
  assert.match(tsx, /printsAs\(item, taxonomy, v\)/);
  assert.match(tsx, /reviewRowState\(item\)/);
});

check("ReviewStep: the cover form gets the AI's own words (aiNotes) and the damaged parts (impact check), as the Cover tab", () => {
  const tsx = source("../components/intake/ReviewStep.tsx");
  const start = tsx.indexOf("<CoverPageFields");
  const form = tsx.slice(start, tsx.indexOf("/>", start));
  assert.ok(start > 0, "ReviewStep renders CoverPageFields");
  assert.match(form, /aiNotes=\{ai\.notes\}/);
  assert.match(form, /damage=\{damage\}/);
  // The map stays alone in its column: no sketch in the wizard's cover form.
  assert.doesNotMatch(form, /sketch=/);
  const tab = source("../components/CoverTab.tsx");
  assert.match(tab, /aiNotes=\{ai\.notes\}/);
  assert.match(tab, /damage=\{damageEntries\}/);
});

check("IntakeWizard: File case runs the check first; [Go to it] goes back to the Review step at that row", () => {
  const tsx = source("../components/intake/IntakeWizard.tsx");
  assert.match(tsx, /onFinalize=\{requestFinalize\}/);
  assert.match(tsx, /filingProblems\(data\.damage_summary\)/);
  assert.match(tsx, /<FilingCheckDialog/);
  assert.match(tsx, /reveal=\{reveal\}/);
});

check("ConditionPicker: the quick values in the select, and 'Keep the order I picked them in'", () => {
  const tsx = source("../components/ConditionPicker.tsx");
  assert.match(tsx, /quickConditions\(words\.common\)/);
  assert.match(tsx, /joinAsPicked\(picked\)/);
  assert.match(tsx, /Keep the order I picked them in/);
});

if (failed) {
  console.log(`\n${failed} check(s) failed`);
  process.exit(1);
}
console.log("\nALL REVIEW CHECKS PASSED");
