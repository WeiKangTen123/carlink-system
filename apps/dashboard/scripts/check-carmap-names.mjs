#!/usr/bin/env node
/**
 * Checks the car map's own part names on the dashboard side (bot-service
 * taxonomy.CAR_MAP_PARTS, behind intake_car_map_names):
 *  - lib/vehicleZones.ts: each name and twin has its 3D zone as
 *    car_map_new_names.json (N1) gives it, a door twin is "?" on every door,
 *    and each one N1 says sits behind or inside its panel has a stand-in;
 *  - lib/partsList.ts: the pickers' list is GET /taxonomy's `parts` exactly
 *    with the names off (or from an API without them), and adds the names
 *    before "Other / Not Listed" with them on; a stored car map part is
 *    known as one either way.
 *
 *   node apps/dashboard/scripts/check-carmap-names.mjs
 *
 * Prints one line per check and exits 1 if any fails. Needs Node with
 * built-in type stripping (22.18+ / 24) to import the .ts files.
 */
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
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

const zones = await import("../lib/vehicleZones.ts");
const list = await import("../lib/partsList.ts");

const bot = new URL("../../bot-service/", import.meta.url);
const n1 = JSON.parse(readFileSync(new URL("app/intake/car_map_new_names.json", bot), "utf8"));
// taxonomy.CAR_MAP_PARTS as written in taxonomy.py: the dashboard follows it.
const taxonomy = readFileSync(new URL("app/reports/taxonomy.py", bot), "utf8");
const block = taxonomy.match(/CAR_MAP_PARTS: list\[str\] = \[([\s\S]*?)\n\]/);
const carMapParts = block ? [...block[1].matchAll(/^\s*"([^"]+)",/gm)].map((m) => m[1]) : [];

let failed = 0;
function check(label, fn) {
  try {
    fn();
    console.log(`    ok   ${label}`);
  } catch (err) {
    failed++;
    console.log(`    FAIL ${label}: ${err.message}`);
  }
}

const EITHER = {
  any_door: ["l_door_front", "r_door_front", "l_door_rear", "r_door_rear"],
  any_quarter: ["l_quarter", "r_quarter"],
};
const keysOf = (zone) => EITHER[zone] ?? [zone];
const sides = (name) => (name.startsWith("{side} ") ? ["Left", "Right"].map((s) => name.replace("{side}", s)) : [name]);

console.log("vehicleZones.ts: the car map's names on the 3D map");
check("taxonomy.py's CAR_MAP_PARTS read (45 names)", () => assert.equal(carMapParts.length, 45));
check("every name and twin has a zone decision", () => {
  const missing = carMapParts.filter((p) => !zones.zoneKeysFor(p));
  assert.deepEqual(missing, []);
});
for (const entry of n1.names) {
  for (const name of sides(entry.name)) {
    const side = name.startsWith("Left ") ? "Left" : name.startsWith("Right ") ? "Right" : null;
    const want = typeof entry.zone === "string" ? entry.zone : entry.zone[side];
    check(`${name} -> ${want}${entry.stand_in ? `, "${entry.stand_in}"` : ""}`, () => {
      const got = zones.zoneKeysFor(name);
      assert.deepEqual(got.keys, keysOf(want));
      assert.equal(got.sideUnknown, keysOf(want).length > 1);
      assert.equal(zones.standInFor(name), entry.stand_in ?? null);
    });
  }
}
const twins = [...new Set(n1.names.filter((e) => e.twin).map((e) => e.twin))];
for (const twin of twins) {
  const entry = n1.names.find((e) => e.twin === twin);
  check(`${twin} -> ${entry.zone.twin} (side unknown where it may be either side's panel)`, () => {
    const got = zones.zoneKeysFor(twin);
    assert.deepEqual(got.keys, keysOf(entry.zone.twin));
    assert.equal(zones.isSideUndetermined(twin), true);
    if (entry.stand_in) assert.ok(zones.standInFor(twin), "a stand-in");
  });
}
check("a door twin marks every door with ? (never a guessed side)", () => {
  for (const twin of ["Door Lock (side undetermined)", "Door Hinge (side undetermined)", "Door Handle (side undetermined)"]) {
    assert.deepEqual(zones.zoneKeysFor(twin), { keys: EITHER.any_door, sideUnknown: true });
  }
});
check("Front Parking Sensor is at the front (the regex alone would put a sensor at the rear)", () => {
  assert.deepEqual(zones.zoneKeysFor("Front Parking Sensor").keys, ["front_bumper"]);
  assert.deepEqual(zones.zoneKeysFor("Parking Sensor").keys, ["rear_bumper"]);
});
check("no car map name is a part with no mesh (each marks a panel)", () => {
  assert.deepEqual(carMapParts.filter((p) => zones.isKnownUnmappedPart(p)), []);
});

console.log("partsList.ts: what the pickers offer");
const parts = ["Front Bumper", "Left Tail Lamp", "Tail Lamp (side undetermined)", "Other / Not Listed"];
const carMap = ["Front Bumper Absorber", "Left Front Door Lock", "Door Lock (side undetermined)"];
check("names off: exactly the canonical list", () => {
  assert.deepEqual(list.partsToPick({ parts, car_map_parts: carMap, car_map_names: false }), parts);
});
check("an API from before the names (no fields): the canonical list", () => {
  assert.deepEqual(list.partsToPick({ parts }), parts);
  assert.deepEqual(list.partsToPick(null), []);
});
check("names on: the car map's after the canonical ones, Other still last", () => {
  assert.deepEqual(list.partsToPick({ parts, car_map_parts: carMap, car_map_names: true }), [
    "Front Bumper",
    "Left Tail Lamp",
    "Tail Lamp (side undetermined)",
    "Front Bumper Absorber",
    "Left Front Door Lock",
    "Door Lock (side undetermined)",
    "Other / Not Listed",
  ]);
});
check("only a true flag offers them (a truthy string doesn't)", () => {
  assert.deepEqual(list.partsToPick({ parts, car_map_parts: carMap, car_map_names: "on" }), parts);
  assert.deepEqual(list.carMapPartsOffered({ parts, car_map_parts: carMap, car_map_names: true }), carMap);
});
check("a stored car map part is one whatever the flag (not 'as recorded')", () => {
  assert.equal(list.isCarMapPart({ parts, car_map_parts: carMap, car_map_names: false }, "Left Front Door Lock"), true);
  assert.equal(list.isCarMapPart({ parts, car_map_parts: carMap, car_map_names: false }, "Rear bumper fascia"), false);
  assert.equal(list.isCarMapPart({ parts }, "Left Front Door Lock"), false);
});

if (failed) {
  console.log(`\n${failed} FAILED`);
  process.exit(1);
}
console.log("\nALL CAR MAP NAME CHECKS PASSED");
