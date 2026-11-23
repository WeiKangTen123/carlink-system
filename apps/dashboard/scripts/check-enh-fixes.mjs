#!/usr/bin/env node
/**
 * Checks the dashboard side of plans/2026-11-22-enhance-staging's F3 (the
 * "Other side?" suggestion, source "other_side") in lib/findings.ts, on
 * synthetic data: its name, its code reason, its photo's fallback, and that
 * the listed side doesn't hide it while the other side does.
 *
 *   node apps/dashboard/scripts/check-enh-fixes.mjs
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

// As second_look._store stores an F3 suggestion: the undetermined name, its
// covers without the listed side, other_side_listed that side.
const otherSide = {
  key: "Tail Lamp (side undetermined)",
  part: "Tail Lamp (side undetermined)",
  name: "Tail Lamp (side undetermined)",
  photo_reference: "@p9",
  source: "other_side",
  other_side_of: "Left Tail Lamp",
  other_side_listed: "Left Tail Lamp",
  covers: ["Tail Lamp (side undetermined)", "Right Tail Lamp"],
  confidence: "Low",
  reason: null,
  evidence: [],
  found_by: ["overall"],
  seen_in: ["@p9"],
  condition_tokens: ["Cracked"],
  condition: null,
  damage_type: null,
  check: null,
  other_part_name: null,
  severity: null,
  remarks: null,
  note: null,
};
const data = {
  damage_summary: [{ part: "Left Tail Lamp", photo_reference: "@p4", ai_confidence: "Medium", found_by: ["overall"] }],
  second_look: { status: "skipped", items: [otherSide], dismissed: [], decisions: [], reads: ["overall"] },
};

const sug = () => F.buildFindings(data).find((f) => f.kind === "suggestion");

check("an Other side? suggestion is shown while only the listed side is on the list", () => {
  assert.ok(sug(), "not shown");
});
check('its name reads "Other side? Tail Lamp (side undetermined)"', () => {
  assert.equal(sug().name, "Other side? Tail Lamp (side undetermined)");
});
check("its code reason says it was seen on another photo, never 'Next to a part found here'", () => {
  const why = F.codeReason(sug());
  assert.match(why, /^Seen on another photo than the listed Left Tail Lamp: it may be the other side/);
  assert.doesNotMatch(why, /Next to a part found here/);
});
check("its note still names the listed side (otherSideListed)", () => {
  assert.equal(sug().otherSideListed, "Left Tail Lamp");
});
check("its photo comes from the read that found it, never an 'other_side read'", () => {
  const ev = sug().evidence;
  assert.ok(ev.length >= 1);
  assert.equal(F.sourceName(ev[0].by), "main read");
});
check("once the other side is listed too, it is hidden (covers)", () => {
  const both = { ...data, damage_summary: [...data.damage_summary, { part: "Right Tail Lamp", photo_reference: "@p10" }] };
  assert.equal(F.buildFindings(both).find((f) => f.kind === "suggestion"), undefined);
});
check("a zone suggestion keeps its own name and reason (unchanged)", () => {
  const zone = { ...otherSide, key: "Left Boot Lid Lamp", part: "Left Boot Lid Lamp", name: "Boot lid lamp LH", source: "zone",
                 other_side_of: undefined, other_side_listed: null, covers: ["Left Boot Lid Lamp"], found_by: ["zone:rear"] };
  const f = F.buildFindings({ ...data, second_look: { ...data.second_look, items: [zone] } }).find((x) => x.kind === "suggestion");
  assert.equal(f.name, "Boot lid lamp LH");
  assert.match(F.codeReason(f), /^Found by the rear read/);
});

if (failed) {
  console.log(`\n${failed} check(s) failed`);
  process.exit(1);
}
console.log("\nall checks passed");
