#!/usr/bin/env node
/**
 * Checks that an id from a server action can't steer the server's own API
 * call to another route (CodeQL js/request-forgery, alerts 1 and 2):
 * lib/apiPath.ts's seg() keeps a value inside one path segment, and every
 * value put into the path of an API URL in lib/api.ts goes through it.
 *
 *   node apps/dashboard/scripts/check-api-paths.mjs
 *
 * Prints one line per check and exits 1 if any fails. Needs Node with
 * built-in type stripping (22.18+ / 24) to import the .ts file.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

process.removeAllListeners("warning");
process.on("warning", (w) => {
  if (w.code !== "MODULE_TYPELESS_PACKAGE_JSON") console.error(w.stack ?? w.message);
});

const { seg } = await import("../lib/apiPath.ts");

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

const BASE = "http://api:8000";
const pathOf = (id, rest = "/download") => new URL(`${BASE}/reports/${seg(id)}${rest}`).pathname;

check("a real case id (12 hex) is unchanged", () => assert.equal(seg("2790f292c0e3"), "2790f292c0e3"));
check("a number (item index, suggestion id) is unchanged", () => assert.equal(seg(7), "7"));
check("a key service name is unchanged", () => assert.equal(seg("gemini"), "gemini"));
check("'/' stays inside the segment", () => assert.equal(pathOf("../users/abc"), "/reports/..%2Fusers%2Fabc/download"));
check("'?' and '#' stay inside the segment", () => assert.equal(pathOf("x?admin=1#y"), "/reports/x%3Fadmin%3D1%23y/download"));
check("'\\' stays inside the segment", () => assert.equal(pathOf("..\\catalogue-worker"), "/reports/..%5Ccatalogue-worker/download"));
check("percent-encoded dots stay literal", () => assert.equal(pathOf("%2e%2e", ""), "/reports/%252e%252e"));
for (const bad of ["", ".", ".."]) {
  check(`${JSON.stringify(bad)} is refused`, () => assert.throws(() => seg(bad), /Invalid id/));
}

// Every ${...} in the path of an API URL in lib/api.ts is seg(...), or one
// of the few values the module builds itself.
const BUILT_HERE = new Set([
  "API_BASE_URL",
  "CLIENT_API_BASE_URL",
  "action", // inboxPost: "take" | "assign" | "release" | "discard", from this file
  "path", // putChecked: confirmFields / confirmIntakeFields, built with seg()
  "query", // "?scope=..." / "?since=...": after the "?", not in the path
]);
const source = readFileSync(new URL("../lib/api.ts", import.meta.url), "utf8");
const literals = [...source.matchAll(/`(\$\{(?:CLIENT_)?API_BASE_URL\}[^`]*|\/(?:reports|intake)\/[^`]*)`/g)].map((m) => m[1]);
check("lib/api.ts builds API URLs (sanity)", () => assert.ok(literals.length > 40, `only ${literals.length} found`));
const unsafe = [];
for (const lit of literals) {
  const pathPart = lit.split("?")[0];
  for (const m of pathPart.matchAll(/\$\{([^}]*)\}/g)) {
    const expr = m[1].trim();
    if (BUILT_HERE.has(expr) || /^seg\(\w+\)$/.test(expr)) continue;
    unsafe.push(`\`${lit}\` -> \${${expr}}`);
  }
}
// The two download links are <a href> targets for the browser, not calls
// the server makes; ids there come from the API's own list.
const allowedLinks = ["${CLIENT_API_BASE_URL}/reports/${reportId}/download", "${CLIENT_API_BASE_URL}/reports/${reportId}/download.xlsx"];
const remaining = unsafe.filter((u) => !allowedLinks.some((l) => u.startsWith(`\`${l}\``)));
check("every caller-supplied value in an API path goes through seg()", () => assert.deepEqual(remaining, []));

if (failed) {
  console.log(`\n${failed} check(s) failed`);
  process.exit(1);
}
console.log("\nall API path checks passed");
