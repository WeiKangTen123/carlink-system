#!/usr/bin/env node
/**
 * Checks the dashboard side of INTAKE_USER_FIELDS (owner, 25 Nov 2026;
 * apps/bot-service/app/reports/user_fields.py): the upload step's tip
 * (lib/uploadTip.ts). Off, it is the tip as before, word for word; on, it
 * still asks for the odometer photo but never says the AI reads it -- and
 * the wizard knows the switch before a New Report has a draft.
 *
 *   node apps/dashboard/scripts/check-user-fields.mjs
 *
 * Prints one line per check and exits 1 if any fails. Needs Node with
 * built-in type stripping (22.18+ / 24) to import the .ts file.
 */
import assert from "node:assert/strict";

process.removeAllListeners("warning");
process.on("warning", (w) => {
  if (w.code !== "MODULE_TYPELESS_PACKAGE_JSON") console.error(w.stack ?? w.message);
});

const { uploadTip, userFieldsOn } = await import("../lib/uploadTip.ts");

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

check("off: the tip as before, word for word", () => {
  assert.equal(uploadTip(false), "Tip: also photograph the log card, the VIN/chassis plate and the odometer, so the AI can read them.");
});
check("on: still asks for the odometer photo", () => {
  assert.match(uploadTip(true), /odometer/);
  assert.match(uploadTip(true), /log card and the VIN\/chassis plate, so the AI can read them/);
});
check("on: never says the AI reads the odometer", () => {
  const tip = uploadTip(true);
  const aiClause = tip.slice(0, tip.indexOf("so the AI can read them"));
  assert.doesNotMatch(aiClause, /odometer/);
  assert.match(tip, /you type its reading in/);
});

check("the switch is known before any draft (GET /auth/me), and from the draft", () => {
  assert.equal(userFieldsOn(null, { user_fields: true }), true, "a New Report before its first upload");
  assert.equal(userFieldsOn({ user_fields: true }, null), true);
  assert.equal(userFieldsOn(undefined, undefined), false);
  assert.equal(userFieldsOn({}, {}), false, "off: both answers leave it out");
  assert.equal(userFieldsOn(null, { user_fields: false }), false);
});

if (failed) {
  console.log(`\n${failed} check(s) failed`);
  process.exit(1);
}
console.log("\nall checks passed");
