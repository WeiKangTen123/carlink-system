#!/usr/bin/env node
/**
 * Checks the part picker (L-library-cycle.md B.2, B.3; step 3A),
 * lib/partPicker.ts, against the real parts library
 * (bot-service app/parts/library_core.json) and the real part list
 * (taxonomy.py CANONICAL_PARTS and CAR_MAP_PARTS), plus a made-up staff
 * library with made-up price-book wordings:
 *   - every name the old part dropdown offered is still found -- the top
 *     hit for its own name -- and gives exactly that name, with the car
 *     map's names off and on, with and without the library; no pick gives a
 *     part off the list;
 *   - ranking (exact, starts-with, all words, alias, near), ties to more
 *     uses then the chosen area; typos in 5+ letter words only; side words
 *     set the side; singular = plural;
 *   - "Other / Not Listed" with the typed name, and "Did you mean" first;
 *   - the price book's past wordings (staff) as named Others;
 *   - pre-filters: area chips, the map's panel (its own part first, as the
 *     old add row's prefill), the photo's sorter area and part tag;
 *   - "prints as" for this car's body, the mini diagram's zones;
 * and, from the source, that the four entry points open the picker.
 *
 *   node apps/dashboard/scripts/check-picker.mjs
 *
 * Prints one line per check and exits 1 if any fails. Needs Node with
 * built-in type stripping (22.18+ / 24) to import the .ts files.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

process.removeAllListeners("warning");
process.on("warning", (w) => {
  if (w.code !== "MODULE_TYPELESS_PACKAGE_JSON") console.error(w.stack ?? w.message);
});
// The lib files import each other the bundler's way ("./partSearch", no
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

const P = await import("../lib/partPicker.ts");
const R = await import("../lib/reviewChecks.ts");
const Z = await import("../lib/vehicleZones.ts");

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

// ---- the real lists and library ---------------------------------------------------------
const bot = new URL("../../bot-service/", import.meta.url);
const core = JSON.parse(readFileSync(new URL("app/parts/library_core.json", bot), "utf8"));
const taxonomyPy = readFileSync(new URL("app/reports/taxonomy.py", bot), "utf8");
const listOf = (name) => {
  const start = taxonomyPy.indexOf(`${name}: list[str] = [`);
  const end = taxonomyPy.indexOf("\n]", start);
  return start < 0 ? [] : [...taxonomyPy.slice(start, end).matchAll(/^\s*"([^"]+)",/gm)].map((m) => m[1]);
};
const CANON = listOf("CANONICAL_PARTS");
const CAR_MAP = listOf("CAR_MAP_PARTS");
const OTHER = "Other / Not Listed";
const tax = (on) => ({ parts: CANON, car_map_parts: CAR_MAP, car_map_names: on });
const fixture = JSON.parse(readFileSync(new URL("./fixtures/prints-as-sample.json", import.meta.url), "utf8"));

// What GET /library gives an account that isn't staff: no counts, no wordings.
const lib = { ...core, staff: false, fittings: [] };
// A made-up staff answer: counts, and made-up price-book wordings (names only).
const staffLib = {
  ...core,
  staff: true,
  components: core.components.map((c) => ({ ...c, uses: { "rear-bumper": 9, "front-bumper": 2 }[c.key] ?? 0, conditions: [] })),
  fittings: [
    {
      key: "seed-rear-bumper-side-retainer", name: "Rear bumper side retainer", kind: "fitting", status: "seed", family: "Rear Bumper",
      parent_key: "rear-bumper", parent_keys: ["rear-bumper"], area: "rear", assembly: "rear-bumper", panels: ["rear_bumper"], sided: true,
      aliases: [{ text: "Rear bumper side retainers", kind: "wording", source: "price book" }], uses: 3, conditions: [],
    },
    {
      key: "seed-headlamp-lower-bracket", name: "Headlamp lower bracket", kind: "fitting", status: "seed", family: "Headlamp",
      parent_key: "headlamp", parent_keys: ["headlamp"], area: "front", assembly: "headlamp", panels: ["l_headlamp", "r_headlamp"], sided: true,
      aliases: [], uses: 1, conditions: [],
    },
    {
      key: "seed-clip-assy", name: "Clip assy", kind: "fitting", status: "seed", family: null,
      parent_key: null, parent_keys: [], area: null, assembly: null, panels: [], sided: false, aliases: [], uses: 0, conditions: [],
    },
  ],
};

const entriesOff = P.pickerEntries(lib, tax(false));
const entriesOn = P.pickerEntries(lib, tax(true));
const listFor = (on) => [...CANON.filter((p) => p !== OTHER), ...(on ? CAR_MAP : [])];
const entryNamed = (entries, name) => entries.find((e) => e.name === name);
/** The top pick for a query, as Enter on the first row with the query's side. */
const topPick = (entries, query, opts = {}) => {
  const side = P.querySide(query);
  const view = P.pickerView(entries, query, { side, ...opts });
  return view.hits[0] ? P.pickFor(view.hits[0].entry, side) : null;
};
const names = (hits) => hits.map((h) => h.entry.name);

// ---------------------------------------------------------------------------
console.log("-- the old dropdown's names (the contract: a pick = the old pick)");
check("the real lists read (102 canonical with Other, 45 car map)", () => {
  assert.equal(CANON.length, 102);
  assert.equal(CAR_MAP.length, 45);
  assert.equal(CANON[CANON.length - 1], OTHER);
});
for (const on of [false, true]) {
  for (const withLib of [true, false]) {
    const entries = withLib ? P.pickerEntries(lib, tax(on)) : P.pickerEntries(null, tax(on));
    const list = listFor(on);
    const label = `car map names ${on ? "on" : "off"}, ${withLib ? "with the library" : "no library (older API)"}`;
    check(`${label}: each of the ${list.length} names is the top hit for itself and picks exactly that name`, () => {
      const wrong = [];
      for (const name of list) {
        const pick = topPick(entries, name);
        if (!pick || pick.part !== name || pick.other_part_name !== null) wrong.push(`${name} -> ${JSON.stringify(pick)}`);
      }
      assert.deepEqual(wrong, []);
    });
    check(`${label}: the picks give every list name and nothing off the list`, () => {
      const got = new Set();
      for (const e of entries) {
        for (const side of [null, "left", "right", "unknown"]) {
          const pick = P.pickFor(e, side);
          if (!pick) continue;
          if (pick.part === OTHER) {
            assert.ok(!e.onList, `${e.name} on the list but picks as Other`);
            continue;
          }
          assert.ok(list.includes(pick.part), `${pick.part} isn't on the list`);
          assert.equal(pick.other_part_name, null, `${pick.part}: a list part has no other_part_name`);
          got.add(pick.part);
        }
      }
      assert.deepEqual([...got].sort(), [...list].sort());
    });
  }
}
check("a car map name is a list part only while the switch is on (else a named Other, as typing it was)", () => {
  assert.deepEqual(P.pickFor(entryNamed(entriesOn, "Front Bumper Absorber"), null), { part: "Front Bumper Absorber", other_part_name: null });
  const off = entryNamed(entriesOff, "Front Bumper Absorber");
  assert.equal(off.onList, false);
  assert.equal(off.carMap, true);
  assert.deepEqual(P.pickFor(off, null), { part: OTHER, other_part_name: "Front Bumper Absorber" });
  assert.deepEqual(P.pickFor(entryNamed(entriesOff, "Front Bumper Bracket"), "right"), { part: OTHER, other_part_name: "Front Bumper Bracket RH" });
  assert.deepEqual(P.pickFor(entryNamed(entriesOn, "Front Bumper Bracket"), "right"), { part: "Right Front Bumper Bracket", other_part_name: null });
});
check("one entry per library component (86); with no library the list's Left/Right/twin names are one entry", () => {
  assert.equal(entriesOff.filter((e) => e.type === "part").length, core.components.length);
  const tail = P.pickerEntries(null, tax(false)).find((e) => e.name === "Tail Lamp");
  assert.deepEqual(tail.names, { left: "Left Tail Lamp", right: "Right Tail Lamp" });
  assert.equal(tail.twin, "Tail Lamp (side undetermined)");
});

// ---------------------------------------------------------------------------
console.log("-- ranking (B.2): exact, starts with, all words, alias, near; ties");
const tierRank = (t) => ["exact", "starts", "words", "alias", "near"].indexOf(t);
check("'rear bumper': Rear Bumper (exact) first, the tiers in order down the list", () => {
  const hits = P.pickerView(entriesOff, "rear bumper").hits;
  assert.equal(hits[0].entry.name, "Rear Bumper");
  assert.equal(hits[0].tier, "exact");
  assert.equal(hits[1].tier, "starts");
  for (let i = 1; i < hits.length; i++) assert.ok(tierRank(hits[i - 1].tier) <= tierRank(hits[i].tier), `${names(hits)}`);
});
check("aliases: 'hood' -> Bonnet, 'windshield' -> Front Windscreen, 'tailgate' / 'tail gate' -> Boot Lid", () => {
  assert.deepEqual(topPick(entriesOff, "hood"), { part: "Bonnet", other_part_name: null });
  assert.deepEqual(topPick(entriesOff, "windshield"), { part: "Front Windscreen", other_part_name: null });
  assert.deepEqual(topPick(entriesOff, "tailgate"), { part: "Boot Lid", other_part_name: null });
  assert.deepEqual(topPick(entriesOff, "tail gate"), { part: "Boot Lid", other_part_name: null });
  assert.equal(P.pickerView(entriesOff, "hood").hits[0].tier, "alias");
});
check("the firm's wording finds its part: 'rear fender' -> Rear Quarter Panel (a side to choose)", () => {
  const hit = P.pickerView(entriesOff, "rear fender").hits[0];
  assert.equal(hit.entry.name, "Rear Quarter Panel");
  assert.equal(P.needsSide(hit.entry, null), true);
});
check("the car map's 'also check' words: 'side brackets' finds both bumper brackets", () => {
  const got = names(P.pickerView(entriesOn, "side brackets").hits);
  assert.ok(got.includes("Front Bumper Bracket") && got.includes("Rear Bumper Bracket"), got.join(", "));
});
check("ties: more uses first (staff counts), then the chosen area", () => {
  const staffEntries = P.pickerEntries(staffLib, tax(true));
  // All "all words": Rear Bumper used 9 times, the made-up retainer
  // wording 3, Front Bumper 2, the rest none. Grouped by area, the group of
  // the best hit first.
  const view = P.pickerView(staffEntries, "bumper", { areas: P.pickerAreas(staffLib) });
  assert.equal(view.groups[0].key, "rear");
  assert.deepEqual(names(view.groups[0].hits).slice(0, 2), ["Rear Bumper", "Rear bumper side retainer"]);
  assert.equal(view.groups.find((g) => g.key === "front").hits[0].entry.name, "Front Bumper");
  // The other way round: Front Bumper used more, the front group leads.
  const swapped = P.pickerEntries(
    { ...staffLib, fittings: [], components: staffLib.components.map((c) => ({ ...c, uses: { "rear-bumper": 2, "front-bumper": 9 }[c.key] ?? 0 })) },
    tax(true),
  );
  assert.equal(P.pickerView(swapped, "bumper").hits[0].entry.name, "Front Bumper");
  // Equal uses (none): the chosen area first.
  assert.equal(P.pickerView(entriesOn, "absorber", { preferArea: "rear" }).hits[0].entry.name, "Rear Bumper Absorber");
  assert.equal(P.pickerView(entriesOn, "absorber", { preferArea: "front" }).hits[0].entry.name, "Front Bumper Absorber");
});

// ---------------------------------------------------------------------------
console.log("-- typos, side words, plurals");
check("one typo in a 5+ letter word: 'ganrish' finds the garnish (and the firm's typo alias), 'bumpr', 'headlmap'", () => {
  const got = names(P.pickerView(entriesOn, "ganrish").hits);
  assert.ok(got.includes("Boot Lid Garnish"), got.join(", "));
  assert.ok(got.includes("Front Wheel Arch Trim"), "its alias 'Front fender wheel arch ganrish'");
  assert.equal(P.pickerView(entriesOff, "bumpr").hits[0].tier, "near");
  assert.ok(names(P.pickerView(entriesOff, "rear bumpr").hits)[0] === "Rear Bumper");
  assert.equal(P.pickerView(entriesOff, "headlmap").hits[0].entry.name, "Headlamp");
});
check("no typo in a short word: 'lamo', 'doro', 'tyer' find nothing", () => {
  for (const q of ["lamo", "doro", "tyer"]) assert.deepEqual(names(P.pickerView(entriesOff, q).hits), [], q);
});
check("side words set the side and leave the search: 'tail lamp lh', 'RH front door', 'nearside mirror', 'o/s headlamp'", () => {
  assert.equal(P.querySide("tail lamp lh"), "left");
  assert.deepEqual(topPick(entriesOff, "tail lamp lh"), { part: "Left Tail Lamp", other_part_name: null });
  assert.deepEqual(topPick(entriesOff, "RH front door"), { part: "Right Front Door", other_part_name: null });
  assert.deepEqual(topPick(entriesOff, "nearside mirror"), { part: "Left Wing Mirror", other_part_name: null });
  assert.deepEqual(topPick(entriesOff, "o/s headlamp"), { part: "Right Headlamp", other_part_name: null });
});
check("'(side undetermined)' is the toggle's '?': 'door (side undetermined)' -> Door (side undetermined), shown once", () => {
  assert.equal(P.querySide("Door (side undetermined)"), "unknown");
  assert.equal(P.searchText("Door (side undetermined)").trim(), "Door");
  assert.deepEqual(topPick(entriesOff, "door (side undetermined)"), { part: "Door (side undetermined)", other_part_name: null });
  const hits = P.pickerView(entriesOff, "door", { side: "unknown" }).hits;
  const twins = hits.filter((h) => h.entry.onList && h.entry.twin).map((h) => h.entry.twin);
  assert.equal(new Set(twins).size, twins.length, `one row per twin: ${twins}`);
});
check("a sided part with no side asks first (LH / RH / ?); a side-less part ignores the toggle", () => {
  const tail = entryNamed(entriesOff, "Tail Lamp");
  assert.equal(P.pickFor(tail, null), null);
  assert.equal(P.needsSide(tail, null), true);
  assert.deepEqual(P.sideChoices(tail), ["left", "right", "unknown"]);
  assert.deepEqual(P.pickFor(tail, "unknown"), { part: "Tail Lamp (side undetermined)", other_part_name: null });
  assert.deepEqual(P.pickFor(entryNamed(entriesOff, "Rear Bumper"), "left"), { part: "Rear Bumper", other_part_name: null });
  assert.deepEqual(P.sideChoices(entryNamed(entriesOff, "Rear Bumper")), []);
});
check("singular = plural: 'tail lamps', 'door hinges', 'wheels', 'door glasses'", () => {
  assert.equal(P.pickerView(entriesOff, "tail lamps").hits[0].entry.name, "Tail Lamp");
  const hinges = names(P.pickerView(entriesOn, "door hinges").hits);
  assert.deepEqual(hinges.slice(0, 2).sort(), ["Front Door Hinge", "Rear Door Hinge"]);
  assert.deepEqual(names(P.pickerView(entriesOff, "wheels").hits).slice(0, 2).sort(), ["Front Wheel", "Rear Wheel"]);
  assert.deepEqual(names(P.pickerView(entriesOff, "door glasses").hits).slice(0, 2).sort(), ["Front Door Glass", "Rear Door Glass"]);
});

// ---------------------------------------------------------------------------
console.log("-- Other / Not Listed and \"Did you mean\"");
check("Other keeps the typed name, tidied as the name box did (spaces, capital, 80 characters); none typed: unnamed", () => {
  assert.deepEqual(P.otherPick("  left rear   air vent "), { part: OTHER, other_part_name: "Left rear air vent" });
  assert.deepEqual(P.otherPick(""), { part: OTHER, other_part_name: null });
  assert.equal(P.otherPick("x".repeat(120)).other_part_name.length, 80);
});
check("Did you mean: the best three matches for the typed name, whatever the chip; none for gibberish", () => {
  assert.equal(P.didYouMean(entriesOn, "rear bumper absorbr")[0].entry.name, "Rear Bumper Absorber");
  assert.equal(P.didYouMean(entriesOff, "front bumper ganrish grille")?.length, 0);
  assert.ok(P.didYouMean(entriesOff, "lamp").length === 3);
  assert.deepEqual(P.didYouMean(entriesOff, "xyzzy"), []);
  assert.deepEqual(P.didYouMean(entriesOff, ""), []);
  assert.deepEqual(P.didYouMean(entriesOff, "lh"), [], "side words alone are no name");
  assert.equal(P.didYouMean(entriesOff, "taillamp")[0].entry.name, "Tail Lamp");
});

// ---------------------------------------------------------------------------
console.log("-- the price book's past wordings (staff, names only)");
const staffEntries = P.pickerEntries(staffLib, tax(false));
check("staff search the past wordings; others have none", () => {
  assert.equal(staffEntries.filter((e) => e.type === "wording").length, 3);
  assert.equal(entriesOff.filter((e) => e.type === "wording").length, 0);
  const hit = P.pickerView(staffEntries, "retainer").hits[0];
  assert.equal(hit.entry.name, "Rear bumper side retainer");
  assert.equal(hit.entry.parent, "Rear Bumper");
  assert.equal(P.pickerView(staffEntries, "retainers").hits[0].entry.name, "Rear bumper side retainer");
});
check("a past wording picks as a named Other, its side last (as the old name box and Annex A put it)", () => {
  const e = staffEntries.find((x) => x.key === "seed-rear-bumper-side-retainer");
  assert.deepEqual(P.pickFor(e, "left"), { part: OTHER, other_part_name: "Rear bumper side retainer LH" });
  assert.deepEqual(P.pickFor(e, null), { part: OTHER, other_part_name: "Rear bumper side retainer" });
  assert.equal(P.printsAsFor(e, "left", tax(false), null), "Rear bumper side retainer LH");
  assert.deepEqual(P.pickFor(staffEntries.find((x) => x.key === "seed-clip-assy"), "left"), { part: OTHER, other_part_name: "Clip assy" });
});
check("browsing (nothing typed) lists the parts, not the past wordings", () => {
  const view = P.pickerView(staffEntries, "");
  assert.equal(view.searching, false);
  assert.ok(view.hits.every((h) => h.entry.type === "part"));
  assert.equal(view.hits.length, core.components.length);
});

// ---------------------------------------------------------------------------
console.log("-- pre-filters: area chips, the map's panel, the photo");
check("an area chip: only that area; matches elsewhere counted for 'show all areas'", () => {
  const rear = P.pickerView(entriesOff, "", { area: "rear" });
  assert.ok(rear.hits.length > 10 && rear.hits.every((h) => h.entry.area === "rear"));
  const front = P.pickerView(entriesOff, "bumper", { area: "front" });
  assert.ok(front.hits.every((h) => h.entry.area === "front"));
  assert.ok(front.elsewhere > 0);
  assert.equal(P.pickerView(entriesOff, "tail lamp", { area: "front" }).hits.length, 0);
});
check("results group by area: the best hit's area first while searching", () => {
  const view = P.pickerView(entriesOff, "lamp", { areas: P.pickerAreas(lib) });
  assert.equal(view.groups[0].key, view.hits[0].entry.area);
  assert.deepEqual(view.groups.flatMap((g) => g.hits), view.hits);
  assert.equal(view.groups.find((g) => g.key === "rear").label, "Rear");
});
check("map panel (left front door): its parts only, its own part first, the side LH -- the old prefill, Left Front Door", () => {
  const preset = P.presetFromZone("l_door_front");
  assert.deepEqual(preset, { zone: "l_door_front", side: "left", sideFrom: "zone" });
  const view = P.pickerView(entriesOff, "", { ...preset, preferNames: P.zonePreferred("l_door_front", listFor(false)) });
  assert.ok(view.hits.every((h) => P.onZone(h.entry, "l_door_front")));
  assert.ok(names(view.hits).includes("Front Door Glass"), "a door means its glass too");
  assert.deepEqual(P.pickFor(view.hits[0].entry, preset.side), { part: "Left Front Door", other_part_name: null });
  assert.equal(R.partForMapAdd("l_door_front", listFor(false)), "Left Front Door");
});
check("every panel of the 3D map has parts; the old prefill (partForMapAdd) is the first pick", () => {
  const list = listFor(false);
  for (const zone of Object.keys(Z.ZONE_LABELS)) {
    const preset = P.presetFromZone(zone);
    const view = P.pickerView(entriesOff, "", { ...preset, preferNames: P.zonePreferred(zone, list) });
    assert.ok(view.hits.length > 0, `${zone}: no part`);
    const old = R.partForMapAdd(zone, list);
    if (old) assert.equal(P.pickFor(view.hits[0].entry, preset.side)?.part, old, zone);
  }
});
check("photo with a part tag: the tag is the search, its side the side ('check')", () => {
  const preset = P.presetFromPhoto({ area: "front-right", part: "Right Headlamp", label: "P04" });
  assert.equal(preset.query, "Right Headlamp");
  assert.equal(preset.side, "right");
  assert.equal(preset.sideFrom, "photo");
  assert.equal(preset.area, null, "a tagged photo searches every area");
  assert.equal(preset.preferArea, "front");
  assert.deepEqual(topPick(entriesOff, preset.query), { part: "Right Headlamp", other_part_name: null });
});
check("photo with no tag: a whole-area photo turns its chip on; a side or corner photo only prefers its area", () => {
  assert.deepEqual(P.presetFromPhoto({ area: "front", part: null }).area, "front");
  const corner = P.presetFromPhoto({ area: "rear-left", part: null });
  assert.equal(corner.area, null);
  assert.equal(corner.preferArea, "rear");
  assert.equal(corner.side, "left");
  assert.equal(P.pickerView(entriesOff, "", { preferArea: corner.preferArea, areas: P.pickerAreas(lib) }).groups[0].key, "rear");
  assert.equal(P.presetFromPhoto({ area: "left" }).preferArea, "side");
  assert.equal(P.presetFromPhoto({ area: "underbody" }).area, "under");
  assert.equal(P.presetFromPhoto({ area: "interior" }).area, "inside");
  assert.equal(P.presetFromPhoto({ area: "front", part: OTHER }).query, "", "an Other tag isn't a search");
  assert.deepEqual(P.presetFromPhoto(null).query, "");
});

// ---------------------------------------------------------------------------
console.log("-- prints as (this car's body) and the mini diagram");
const vehicles = Object.fromEntries(fixture.vehicles.map((v) => [v.vehicle.plate_number, v.vehicle]));
const firm = { ...tax(false), ...fixture.taxonomy };
check("prints as the Review step's own rule (reviewChecks.printsAs): boot lid on a sedan, tail gate on an SUV", () => {
  const boot = entryNamed(entriesOff, "Boot Lid");
  const sedan = vehicles.TESTPA4;
  const suv = vehicles.TESTPA1;
  assert.equal(P.printsAsFor(boot, null, firm, sedan), R.printsAs({ part: "Boot Lid", human_verified: true }, firm, sedan).text);
  assert.equal(P.printsAsFor(boot, null, firm, sedan), "Boot lid assy");
  assert.equal(P.printsAsFor(boot, null, firm, suv), "Tail gate assy");
});
check("prints as: the side's wording, 'LH / RH' while no side is chosen", () => {
  const tail = entryNamed(entriesOff, "Tail Lamp");
  assert.equal(P.printsAsFor(tail, "left", firm, vehicles.TESTPA4), "Tail lamp LH");
  assert.equal(P.printsAsFor(tail, null, firm, vehicles.TESTPA4), "Tail lamp LH / RH");
});
check("prints as, older API (no firm names): the library's wording, LH / RH added", () => {
  const quarter = entryNamed(entriesOff, "Rear Quarter Panel");
  assert.equal(P.printsAsFor(quarter, "left", tax(false), null), `${quarter.firm_wording} LH`);
});
check("the mini diagram lights the side's zone, or every zone of the part", () => {
  const tail = entryNamed(entriesOff, "Tail Lamp");
  assert.deepEqual(P.highlightFor(tail, "left"), ["l_taillamp"]);
  assert.deepEqual([...P.highlightFor(tail, null)].sort(), ["l_taillamp", "r_taillamp"]);
  assert.deepEqual(P.highlightFor(entryNamed(entriesOff, "Rear Bumper"), "left"), ["rear_bumper"]);
});

// ---------------------------------------------------------------------------
console.log("-- the entry points (from the source)");
const source = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");
check("Review step: the add row opens the picker (no part <select> there); a pick adds the row the old add made", () => {
  const tsx = source("../components/intake/ReviewStep.tsx");
  const row = tsx.slice(tsx.indexOf('<div className="intake-add-row">'), tsx.indexOf("</div>", tsx.indexOf('<div className="intake-add-row">')));
  assert.doesNotMatch(row, /<Select/);
  assert.match(row, /setAddPicker\(\{ title: "Add a damaged part"/);
  assert.match(tsx, /<PartPicker[\s\S]*?addPicked\(pick, photoId\)/);
  // A list part: { part, severity: null, human_verified: true } as before.
  assert.match(tsx, /\{\s*part,\s*\.\.\.\(other_part_name \? \{ other_part_name \} : \{\}\),\s*severity: null,\s*\.\.\.\(photoId \? \{ photo_reference: `@\$\{photoId\}`, bbox_2d: null \} : \{\}\),\s*human_verified: true,\s*\}/);
  assert.match(tsx, /onAddDamage=\{addForZone\}/);
  assert.match(tsx, /onAddFromPhoto=\{addFromViewer\}/);
  assert.match(tsx, /presetFromPhoto\(p\)/);
});
check("Case tab 1: '+ Add a part' and the add row open the picker; map and photo viewer adds come in as addRequest", () => {
  const table = source("../components/DetectedPartsTable.tsx");
  assert.doesNotMatch(table, /aria-label="New part"/, "the add row's part <select> is gone");
  assert.match(table, /<PartPicker/);
  assert.match(table, /fillAdd\(pick, photoLabel\)/);
  const tab = source("../components/DamageDetectionTab.tsx");
  assert.match(tab, /addRequest=\{addRequest\}/);
  assert.match(tab, /preset: presetFromZone\(zoneKey\)/);
  assert.match(tab, /onAddFromPhoto=\{readOnly \? undefined : addFromPhoto\}/);
  assert.doesNotMatch(tab, /confirm\(`Add "\$\{part\}"/, "the map's one-click add with confirm() is gone");
});
check("3D map: '+ Add part here' on any panel's card (not only an empty one)", () => {
  const tsx = source("../components/DamageMapPanel.tsx");
  assert.match(tsx, /\{card\.zone && onAddDamage && !readOnly && \(\s*<div className="dmap-card-add">/);
  assert.match(tsx, /Add part here/);
  assert.doesNotMatch(tsx, /Add as damage/);
});
check("photo viewer: 'Add part seen here' only for a page that passes onAddFromPhoto, never read-only", () => {
  const tsx = source("../components/EvidenceViewer.tsx");
  assert.match(tsx, /onAddFromPhoto && !readOnly && photoRef && src && \(/);
  assert.match(tsx, /Add part seen here/);
});

if (failed) {
  console.log(`\n${failed} check(s) failed`);
  process.exit(1);
}
console.log("\nall part picker checks passed");
