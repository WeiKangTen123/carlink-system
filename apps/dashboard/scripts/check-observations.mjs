#!/usr/bin/env node
/**
 * Step 2B, Settings -> AI accuracy "What people changed": the dashboard's
 * pure rules in lib/observations.ts --
 *   - the API's answer read field by field (an older server's missing
 *     fields, odd values, nameless rows: 0 or left out, never an error);
 *   - the date range: real YYYY-MM-DD dates only, from not after to, sent
 *     as query values;
 *   - the area names and the "changed" count;
 * and, from the source, that the section is registered on the AI accuracy
 * tab and loads through the server action (never the browser to the API),
 * and that it shows no case link.
 *
 *   node apps/dashboard/scripts/check-observations.mjs
 *
 * Prints one line per check and exits 1 if any fails. Needs Node with
 * built-in type stripping (22.18+ / 24) to import the .ts file. No network,
 * no real data.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

process.removeAllListeners("warning");
process.on("warning", (w) => {
  if (w.code !== "MODULE_TYPELESS_PACKAGE_JSON") console.error(w.stack ?? w.message);
});

const { normalizeObservationSummary, rangeQuery, isIsoDate, changes, areaName } = await import("../lib/observations.ts");

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

const ANSWER = {
  from: "2026-11-01",
  to: "2026-11-30",
  totals: { cases: 3, parts: 12, kept: 5, removed: 2, added: 3, added_prompted: 1, dismissed: 2, left: 0, cases_without_ai_draft: 1 },
  by_part: [
    { part: "Tail Lamp", cases: 2, kept: 0, removed: 0, added: 2, added_prompted: 1, dismissed: 0, left: 0 },
    { part: "Other / Not Listed: rear air vent", cases: 1, kept: 0, removed: 1, added: 0, added_prompted: 0, dismissed: 0, left: 0 },
  ],
  by_make: [{ make: "Alphamotors", cases: 3, kept: 5, removed: 2, added: 3, added_prompted: 1, dismissed: 2, left: 0 }],
  by_area: [{ area: "rear", cases: 3, kept: 2, removed: 1, added: 3, added_prompted: 1, dismissed: 1, left: 0 }],
};

check("a full answer is read as sent", () => {
  const s = normalizeObservationSummary(ANSWER);
  assert.deepEqual(s.totals, ANSWER.totals);
  assert.equal(s.from, "2026-11-01");
  assert.equal(s.by_part.length, 2);
  assert.equal(s.by_part[0].part, "Tail Lamp");
  assert.equal(s.by_make[0].make, "Alphamotors");
  assert.equal(s.by_area[0].area, "rear");
});

check("an empty or odd answer is zeros and empty lists, never an error", () => {
  for (const body of [null, undefined, "x", 3, [], {}, { totals: "x", by_part: "y" }]) {
    const s = normalizeObservationSummary(body);
    assert.equal(s.totals.cases, 0);
    assert.equal(s.totals.cases_without_ai_draft, 0);
    assert.deepEqual([s.by_part, s.by_make, s.by_area], [[], [], []]);
    assert.equal(s.from, null);
  }
});

check("odd counts are 0, fractions cut, nameless rows left out", () => {
  const s = normalizeObservationSummary({
    totals: { cases: -2, added: "3", removed: 2.7, dismissed: Infinity },
    by_part: [{ part: "  ", added: 1 }, { added: 1 }, "x", { part: "Bonnet", added: 1.9, removed: null }],
  });
  assert.equal(s.totals.cases, 0);
  assert.equal(s.totals.added, 0);
  assert.equal(s.totals.removed, 2);
  assert.equal(s.totals.dismissed, 0);
  assert.deepEqual(s.by_part.map((r) => [r.part, r.added, r.removed]), [["Bonnet", 1, 0]]);
});

check("only real YYYY-MM-DD dates", () => {
  assert.equal(isIsoDate("2026-11-24"), true);
  assert.equal(isIsoDate("2024-02-29"), true);
  for (const bad of ["2026-02-30", "2026-13-01", "24/11/2026", "2026-1-5", "", "2026-11-24T00:00", "x"]) {
    assert.equal(isIsoDate(bad), false, bad);
  }
});

check("the range: blank is no query; dates are query values; from after to refused", () => {
  assert.deepEqual(rangeQuery("", ""), { query: "" });
  assert.deepEqual(rangeQuery(undefined, undefined), { query: "" });
  assert.deepEqual(rangeQuery("2026-11-01", ""), { query: "?from=2026-11-01" });
  assert.deepEqual(rangeQuery("", "2026-11-30"), { query: "?to=2026-11-30" });
  assert.deepEqual(rangeQuery(" 2026-11-01 ", "2026-11-30"), { query: "?from=2026-11-01&to=2026-11-30" });
  assert.ok("error" in rangeQuery("2026-12-01", "2026-11-30"));
  assert.ok("error" in rangeQuery("../users", ""));
  assert.ok("error" in rangeQuery("", "2026-11-30&x=1"));
});

check("what people changed, and the area names", () => {
  assert.equal(changes({ added: 2, removed: 1, dismissed: 3, kept: 9, left: 4, added_prompted: 1, cases: 1 }), 6);
  assert.equal(areaName("side"), "Side (doors, mirrors, sills)");
  assert.equal(areaName("unknown"), "Not placed");
  assert.equal(areaName("mystery"), "mystery");
});

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

check("the section is on the AI accuracy tab", () => {
  const client = read("../app/settings/AiAccuracyClient.tsx");
  assert.match(client, /import \{ PeopleChangedSection \} from "\.\/PeopleChangedSection";/);
  assert.match(client, /<PeopleChangedSection \/>/);
});

check("it loads through the server action, which checks the range first", () => {
  const section = read("../app/settings/PeopleChangedSection.tsx");
  assert.match(section, /getObservationSummaryAction/);
  assert.doesNotMatch(section, /apiFetch|fetch\(/, "the browser never calls the API itself");
  const actions = read("../app/settings/ai-accuracy/actions.ts");
  assert.match(actions, /^"use server";/);
  assert.match(actions, /const range = rangeQuery\(/);
  assert.match(actions, /\/admin\/observations\/summary\$\{range\.query\}/);
});

check("counts only: the section links to no case and names no one", () => {
  const section = read("../app/settings/PeopleChangedSection.tsx");
  assert.doesNotMatch(section, /\/reports\//);
  assert.doesNotMatch(section, /next\/link/);
  assert.doesNotMatch(section, /report_id|our_ref|plate/);
});

if (failed) {
  console.log(`\n${failed} check(s) failed`);
  process.exit(1);
}
console.log("\nALL OBSERVATIONS CHECKS PASSED");
