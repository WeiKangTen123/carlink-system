#!/usr/bin/env node
/**
 * Checks the dashboard side of Detection Round 1 (the photo sorter's own
 * finds, source "sorter": settings.intake_sorter_hints / intake_intact_rule)
 * in lib/findings.ts, on synthetic data: shown as a suggestion with the
 * "Seen by the photo sorter" label, the sorter's note as its words, its photo
 * by the sorter when it has no evidence, hidden once its part is listed, and
 * nothing changed for the other sources.
 *
 *   node apps/dashboard/scripts/check-sorter-hints.mjs
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

const F = await import("../lib/findings.ts");

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

// As second_look._store stores a sorter suggestion (intake/sorter_hints.py).
const item = (over = {}) => ({
  key: "Left Fog Lamp",
  part: "Left Fog Lamp",
  name: "Front fog lamp LH",
  photo_reference: "@p12",
  source: "sorter",
  covers: ["Left Fog Lamp", "Fog Lamp (side undetermined)"],
  confidence: "Low",
  reason: "Left fog lamp cover cracked",
  evidence: [{ photo: "@p12", box: [100, 100, 400, 400], box_kind: "sort", by: "sort", reason: null, confidence: "Low", img_w: null, img_h: null }],
  found_by: ["sorter_seen"],
  seen_in: ["@p12"],
  condition_tokens: [],
  condition: null,
  damage_type: null,
  check: null,
  other_side_listed: null,
  other_part_name: null,
  severity: null,
  remarks: null,
  note: null,
  ...over,
});
const dataWith = (items, listed = []) => ({
  damage_summary: listed.map((part) => ({ part, photo_reference: "@p3", ai_confidence: "Medium", found_by: ["overall"] })),
  second_look: { status: "skipped", items, dismissed: [], decisions: [], reads: ["overall"] },
});
const sug = (data) => F.buildFindings(data).find((f) => f.kind === "suggestion");

check("a sorter suggestion is shown, its source kept", () => {
  const f = sug(dataWith([item()], ["Rear Bumper"]));
  assert.ok(f, "not shown");
  assert.equal(f.source, "sorter");
  assert.equal(f.name, "Front fog lamp LH");
});
check('its card says "Seen by the photo sorter" (sourceLabel) beside its words; other sources none', () => {
  assert.equal(F.SORTER_LABEL, "Seen by the photo sorter");
  assert.equal(F.sourceLabel(sug(dataWith([item()]))), "Seen by the photo sorter");
  for (const source of ["zone", "kit", "nearby", "link", "other_side"]) {
    assert.equal(F.sourceLabel({ source, reason: "x", foundBy: [], intactIn: [] }), null, source);
  }
});
check("L5: one label, not both -- with no words of its own, codeReason says it and sourceLabel nothing", () => {
  const f = sug(dataWith([item({ reason: null })]));
  assert.equal(F.sourceLabel(f), null);
  assert.ok(F.codeReason(f).startsWith("Seen by the photo sorter"), F.codeReason(f));
});
check("its marks are not reads: never 'Found by the sorter_seen read'", () => {
  const f = sug(dataWith([item({ reason: null })]));
  assert.equal(F.foundByText(f), null);
  assert.equal(F.foundByNames(f), null);
  assert.ok(!F.codeReason(f).includes("sorter_"), F.codeReason(f));
  assert.deepEqual(F.SORTER_MARKS, ["sorter_seen", "sorter_intact"]);
  assert.equal(F.isRead("zone:rear"), true);
  assert.equal(F.isRead("sorter_intact"), false);
});
check("M5: A2's sentence is written here from its facts (intact_in, the mark) -- no photo label, not 'AI says'", () => {
  const a2 = item({ found_by: ["sorter_intact"], intact_in: ["zone:rear"], reason: "Rear windscreen cracked" });
  const f = sug(dataWith([a2]));
  assert.equal(f.reason, "Rear windscreen cracked");
  assert.equal(F.sourceLabel(f), "The rear read called it intact, but the photo sorter saw damage on this photo");
  const bare = sug(dataWith([{ ...a2, reason: null }]));
  assert.ok(F.codeReason(bare).startsWith("The rear read called it intact, but the photo sorter saw damage"), F.codeReason(bare));
  assert.ok(!/P\d+/.test(F.codeReason(bare)), F.codeReason(bare));
  const two = sug(dataWith([{ ...a2, intact_in: ["zone:rear", "zone:left"] }]));
  assert.equal(F.sourceLabel(two), "The rear and left side reads called it intact, but the photo sorter saw damage on this photo");
});
check("its words are the sorter's note, as any suggestion's reason", () => {
  assert.equal(sug(dataWith([item()])).reason, "Left fog lamp cover cracked");
});
check('with no note: the code reason "Seen by the photo sorter"', () => {
  const f = sug(dataWith([item({ reason: null })]));
  assert.ok(F.codeReason(f).startsWith("Seen by the photo sorter"), F.codeReason(f));
});
check("its evidence is the sorter's photo and box; with none stored, its photo by the sorter", () => {
  const f = sug(dataWith([item()]));
  assert.equal(f.evidence[0].by, "sort");
  assert.equal(F.sourceName(f.evidence[0].by), "the sorter");
  const bare = sug(dataWith([item({ evidence: [] })]));
  assert.equal(bare.evidence[0]?.photo, "@p12");
  assert.equal(bare.evidence[0]?.by, "sort");
});
check("hidden once its part (or its undetermined twin) is on the list", () => {
  assert.equal(sug(dataWith([item()], ["Left Fog Lamp"])), undefined);
  assert.equal(sug(dataWith([item()], ["Fog Lamp (side undetermined)"])), undefined);
});
check("with the linked parts: it sits with the other non-zone suggestions", () => {
  const link = item({ key: "Rear Bumper Beam", part: "Rear Bumper Beam", name: "Rear bumper beam", source: "link",
    covers: ["Rear Bumper Beam"], linked_from: ["Rear Bumper"], link_type: "behind", bucket: "likely", hit: "heavy",
    reason: "Behind the bumper.", evidence: [] });
  const list = F.buildFindings(dataWith([item(), link], ["Rear Bumper"])).filter((f) => f.kind === "suggestion");
  const sections = F.suggestionSections(list);
  assert.ok(sections.hasLinks);
  assert.deepEqual(sections.other.map((f) => f.part), ["Left Fog Lamp"]);
});

if (failed) {
  console.log(`\n${failed} check(s) failed`);
  process.exit(1);
}
console.log("\nall sorter-hints checks passed");
