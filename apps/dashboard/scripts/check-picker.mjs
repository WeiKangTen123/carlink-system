#!/usr/bin/env node
/**
 * Checks the part picker (L-library-cycle.md B.2, B.3; step 3A),
 * lib/partPicker.ts, against the real parts library
 * (bot-service app/parts/library_core.json) and the real part list
 * (taxonomy.py CANONICAL_PARTS and CAR_MAP_PARTS), plus a made-up staff
 * library with made-up price-book wordings:
 *   - every name the old part dropdown offered is still found -- the top
 *     hit for its own name -- and gives exactly that name, with the car
 *     map's names off and on, with and without the library, and with the
 *     taxonomy still loading or failed (the library's own list); no pick of
 *     a list part is ever an "Other"; nothing is picked with no list at all;
 *   - the car map's names follow intake_car_map_names; price-book fittings
 *     wait for step 4A; a past wording of a list part is that part;
 *   - ranking, ties, typos, side words, plurals; "Did you mean" and the
 *     one confirmation before a typed Other; "already row 3"; the add row
 *     after a pick; area chips, the map's panel (with no library too), the
 *     photo as a preference; prints-as; the mini diagram; the focus trap;
 *   - from the source, the wiring the pure parts can't show.
 * The fixes of the fresh-eyes review (review-3a.md) are named H1 ... L11.
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
// GET /taxonomy failed: lib/api.ts DEFAULT_TAXONOMY has an empty part list.
const FAILED_TAXONOMY = { parts: [], car_map_parts: [], car_map_names: false };
const fixture = JSON.parse(readFileSync(new URL("./fixtures/prints-as-sample.json", import.meta.url), "utf8"));

// What GET /library gives an account that isn't staff: no counts, no wordings.
const lib = { ...core, staff: false, fittings: [] };
// A made-up staff answer: counts, and made-up price-book wordings (names only).
const seed = (key, name, kind, parent, area, panels, sided, extra = {}) => ({
  key, name, kind, status: "seed", family: null, parent_key: parent, parent_keys: parent ? [parent] : [], area, assembly: null,
  panels, sided, aliases: [], uses: 1, conditions: [], ...extra,
});
const staffLib = {
  ...core,
  staff: true,
  components: core.components.map((c) => ({ ...c, uses: { "rear-bumper": 9, "front-bumper": 2 }[c.key] ?? 0, conditions: [] })),
  fittings: [
    // A fitting: waits for step 4A (review H3).
    seed("seed-rear-bumper-side-retainer", "Rear bumper side retainer", "fitting", "rear-bumper", "rear", ["rear_bumper"], true, { uses: 3 }),
    // A main part's wording not on the list: a named Other.
    seed("seed-rear-bumper-lower-grille", "Rear bumper lower grille", "part", "rear-bumper", "rear", ["rear_bumper"], true, { uses: 3 }),
    // Wordings that ARE list parts (plural, compound): that part.
    seed("seed-bonnets", "Bonnets", "part", "bonnet", "front", ["bonnet"], false),
    seed("seed-head-lamps", "Head lamps", "part", "headlamp", "front", ["l_headlamp", "r_headlamp"], true),
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
const allPicks = (entries) => entries.flatMap((e) => [null, "left", "right", "unknown"].map((s) => ({ e, pick: P.pickFor(e, s) }))).filter((x) => x.pick);

// ---------------------------------------------------------------------------
console.log("-- the old dropdown's names (the contract: a pick = the old pick)");
check("the real lists read (102 canonical with Other, 45 car map)", () => {
  assert.equal(CANON.length, 102);
  assert.equal(CAR_MAP.length, 45);
  assert.equal(CANON[CANON.length - 1], OTHER);
});
const states = [
  { label: "car map names off, with the library", entries: entriesOff, list: listFor(false) },
  { label: "car map names off, no library (older API)", entries: P.pickerEntries(null, tax(false)), list: listFor(false) },
  { label: "car map names on, with the library", entries: entriesOn, list: listFor(true) },
  { label: "car map names on, no library (older API)", entries: P.pickerEntries(null, tax(true)), list: listFor(true) },
  // H1: the taxonomy still loading (null) or failed (empty): the library's own list.
  { label: "taxonomy still loading (null), the library's list", entries: P.pickerEntries(lib, null), list: listFor(false) },
  { label: "taxonomy failed (empty list), the library's list", entries: P.pickerEntries(lib, FAILED_TAXONOMY), list: listFor(false) },
];
for (const { label, entries, list } of states) {
  check(`${label}: each of the ${list.length} names is the top hit for itself and picks exactly that name`, () => {
    const wrong = [];
    for (const name of list) {
      const pick = topPick(entries, name);
      if (!pick || pick.part !== name || pick.other_part_name !== null) wrong.push(`${name} -> ${JSON.stringify(pick)}`);
    }
    assert.deepEqual(wrong, []);
  });
  check(`${label}: the picks give every list name, nothing off the list, and no list part as an Other`, () => {
    const got = new Set();
    for (const { e, pick } of allPicks(entries)) {
      assert.notEqual(pick.part, OTHER, `${e.name} (${e.type}) picks as an Other`);
      assert.ok(list.includes(pick.part), `${pick.part} isn't on the list`);
      assert.equal(pick.other_part_name, null);
      got.add(pick.part);
    }
    assert.deepEqual([...got].sort(), [...list].sort());
  });
}
check("H1: no part list yet (taxonomy loading or failed, no library): no entries, so no picks; the list says so", () => {
  assert.deepEqual(P.pickerEntries(null, null), []);
  assert.deepEqual(P.pickerEntries(null, FAILED_TAXONOMY), []);
  assert.deepEqual(P.pickerEntries({ components: [] }, null), []);
  assert.deepEqual(P.pickerList(null, null), { names: [], from: null });
  assert.equal(P.pickerList(lib, null).from, "library");
  assert.equal(P.pickerList(lib, tax(false)).from, "taxonomy");
  const base = { searching: false, typed: "", area: null, zone: null };
  assert.equal(P.emptyText({ ...base, ready: false, loading: true }), "Loading the part list…");
  assert.match(P.emptyText({ ...base, ready: false, loading: false }), /didn't load/);
});
check("H1: the library's stand-in list is the reader's canonical names only (the car map's left out)", () => {
  const names = P.pickerList(lib, null).names;
  assert.deepEqual([...names].sort(), [...CANON].sort());
});
check("one entry per list component: 60 with the car map's names off, all 86 with them on; no library: Left/Right/twin as one", () => {
  assert.equal(entriesOff.length, core.components.filter((c) => c.ai_use === "reader").length);
  assert.equal(entriesOn.length, core.components.length);
  const tail = P.pickerEntries(null, tax(false)).find((e) => e.name === "Tail Lamp");
  assert.deepEqual(tail.names, { left: "Left Tail Lamp", right: "Right Tail Lamp" });
  assert.equal(tail.twin, "Tail Lamp (side undetermined)");
});

// ---------------------------------------------------------------------------
console.log("-- H2: the car map's names follow intake_car_map_names; H3: past wordings");
check("H2: switch off -- no car map name is offered, browsed or found (not even as an Other)", () => {
  const carMapNames = new Set(CAR_MAP);
  for (const e of entriesOff) assert.ok(!Object.values(e.names).some((n) => carMapNames.has(n)), `${e.name} offered`);
  assert.ok(!names(P.pickerView(entriesOff, "absorber").hits).some((n) => /Bumper Absorber/.test(n)));
  assert.ok(!names(P.pickerView(entriesOff, "", P.presetFromZone("front_bumper")).hits).includes("Front Bumper Absorber"));
});
check("H2: switch on -- they are list parts, picked by their canonical names", () => {
  assert.deepEqual(P.pickFor(entryNamed(entriesOn, "Front Bumper Absorber"), null), { part: "Front Bumper Absorber", other_part_name: null });
  assert.deepEqual(P.pickFor(entryNamed(entriesOn, "Front Bumper Bracket"), "right"), { part: "Right Front Bumper Bracket", other_part_name: null });
});
const staffEntries = P.pickerEntries(staffLib, tax(false));
check("H3: price-book fittings are left out until step 4A; others (not staff) get no wordings at all", () => {
  assert.ok(!staffEntries.some((e) => e.key === "seed-rear-bumper-side-retainer"));
  assert.ok(!names(P.pickerView(staffEntries, "retainer").hits).includes("Rear bumper side retainer"));
  assert.equal(entriesOff.filter((e) => e.type === "wording").length, 0);
});
check("H3: a main part's past wording not on the list picks as a named Other, its side last", () => {
  const e = staffEntries.find((x) => x.key === "seed-rear-bumper-lower-grille");
  assert.equal(e.type, "wording");
  assert.equal(e.parent, "Rear Bumper");
  assert.equal(P.pickerView(staffEntries, "lower grille").hits[0].entry, e);
  assert.deepEqual(P.pickFor(e, "left"), { part: OTHER, other_part_name: "Rear bumper lower grille LH" });
  assert.equal(P.printsAsFor(e, "left", tax(false), null), "Rear bumper lower grille LH");
});
check("H3: a past wording that is a list part ('Bonnets', 'Head lamps') is that part, not an Other", () => {
  assert.ok(!staffEntries.some((e) => e.key === "seed-bonnets" || e.key === "seed-head-lamps"));
  assert.deepEqual(topPick(staffEntries, "bonnets"), { part: "Bonnet", other_part_name: null });
  assert.deepEqual(topPick(staffEntries, "head lamps lh"), { part: "Left Headlamp", other_part_name: null });
  assert.ok(entryNamed(staffEntries, "Bonnet").aliases.includes("Bonnets"));
});
check("browsing (nothing typed) lists the parts, not the past wordings", () => {
  const view = P.pickerView(staffEntries, "");
  assert.equal(view.searching, false);
  assert.ok(view.hits.every((h) => h.entry.type === "part"));
  assert.equal(view.hits.length, entriesOff.length);
});

// ---------------------------------------------------------------------------
console.log("-- ranking (B.2): exact, starts with, all words, alias, near; ties; groups");
const tierRank = (t) => ["exact", "starts", "words", "alias", "near"].indexOf(t);
check("'rear bumper': Rear Bumper (exact) first, the tiers in order within each group", () => {
  const view = P.pickerView(entriesOff, "rear bumper");
  assert.equal(view.hits[0].entry.name, "Rear Bumper");
  assert.equal(view.hits[0].tier, "exact");
  for (const g of view.groups) for (let i = 1; i < g.hits.length; i++) assert.ok(tierRank(g.hits[i - 1].tier) <= tierRank(g.hits[i].tier), g.key);
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
check("the car map's 'also check' words (switch on): 'side brackets' finds both bumper brackets", () => {
  const got = names(P.pickerView(entriesOn, "side brackets").hits);
  assert.ok(got.includes("Front Bumper Bracket") && got.includes("Rear Bumper Bracket"), got.join(", "));
});
check("ties: more uses first (staff counts) -- the best hit's group leads; then the chosen area", () => {
  const view = P.pickerView(P.pickerEntries(staffLib, tax(true)), "bumper", { areas: P.pickerAreas(staffLib) });
  assert.equal(view.groups[0].key, "rear");
  assert.equal(view.groups[0].hits[0].entry.name, "Rear Bumper");
  assert.equal(view.groups.find((g) => g.key === "front").hits[0].entry.name, "Front Bumper");
  const swapped = P.pickerEntries(
    { ...staffLib, fittings: [], components: staffLib.components.map((c) => ({ ...c, uses: { "rear-bumper": 2, "front-bumper": 9 }[c.key] ?? 0 })) },
    tax(true),
  );
  assert.equal(P.pickerView(swapped, "bumper").hits[0].entry.name, "Front Bumper");
  assert.equal(P.pickerView(entriesOn, "absorber", { area: null, preferArea: "rear" }).hits[0].entry.name, "Rear Bumper Absorber");
  assert.equal(P.pickerView(entriesOn, "absorber", { preferArea: "front" }).hits[0].entry.name, "Front Bumper Absorber");
});
check("L8: close spellings come last, in a group of their own; the area groups hold the closer matches", () => {
  for (const q of ["bumpr", "rear bumpr", "bonet", "ganrish", "tail lamp", "door", "rear fender"]) {
    const view = P.pickerView(entriesOn, q);
    const nearAt = view.groups.findIndex((g) => g.key === P.NEAR_GROUP);
    if (nearAt >= 0) {
      assert.equal(nearAt, view.groups.length - 1, `${q}: close spellings last`);
      assert.equal(view.groups[nearAt].label, "Close spellings");
      assert.ok(view.groups[nearAt].hits.every((h) => h.tier === "near"));
    }
    for (const g of view.groups) if (g.key !== P.NEAR_GROUP) assert.ok(g.hits.every((h) => h.tier !== "near"), `${q}: ${g.key}`);
  }
});

// ---------------------------------------------------------------------------
console.log("-- typos, side words, plurals, lamps");
check("one typo in a 5+ letter word: 'bumpr', 'headlmap', 'bonet' (5), and the firm's typo alias 'ganrish'", () => {
  assert.equal(P.pickerView(entriesOff, "bumpr").hits[0].tier, "near");
  assert.equal(names(P.pickerView(entriesOff, "rear bumpr").hits)[0], "Rear Bumper");
  assert.equal(P.pickerView(entriesOff, "headlmap").hits[0].entry.name, "Headlamp");
  assert.equal(P.pickerView(entriesOff, "bonet").hits[0].entry.name, "Bonnet");
  assert.ok(names(P.pickerView(entriesOff, "ganrish").hits).includes("Front Wheel Arch Trim"));
  assert.ok(names(P.pickerView(entriesOn, "ganrish").hits).includes("Boot Lid Garnish"));
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
  const twins = P.pickerView(entriesOff, "door", { side: "unknown" }).hits.filter((h) => h.entry.twin).map((h) => h.entry.twin);
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
check("L10: '?' on a front / rear door, wheel or regulator says front / rear isn't kept; a lamp's '?' needs no note", () => {
  for (const n of ["Front Door", "Rear Wheel", "Front Window Regulator", "Rear Door Glass"]) {
    assert.match(P.twinNote(entryNamed(entriesOff, n), "unknown") ?? "", /front \/ rear not kept/, n);
  }
  // No library: the list alone can't tie "Door (side undetermined)" to the
  // front door, so the front door offers LH / RH only and the twin is a row
  // of its own -- nothing to lose.
  const noLib = P.pickerEntries(null, tax(false));
  assert.deepEqual(P.sideChoices(entryNamed(noLib, "Front Door")), ["left", "right"]);
  assert.deepEqual(P.pickFor(entryNamed(noLib, "Door (side undetermined)"), null), { part: "Door (side undetermined)", other_part_name: null });
  assert.equal(P.twinNote(entryNamed(entriesOff, "Front Door"), "left"), null);
  assert.equal(P.twinNote(entryNamed(entriesOff, "Tail Lamp"), "unknown"), null);
  assert.equal(P.twinNote(entryNamed(entriesOff, "Rear Quarter Panel"), "unknown"), null, "one quarter panel twin, one part");
});
check("singular = plural: 'tail lamps', 'door hinges' (switch on), 'wheels', 'door glasses'", () => {
  assert.equal(P.pickerView(entriesOff, "tail lamps").hits[0].entry.name, "Tail Lamp");
  assert.deepEqual(names(P.pickerView(entriesOn, "door hinges").hits).slice(0, 2).sort(), ["Front Door Hinge", "Rear Door Hinge"]);
  assert.deepEqual(names(P.pickerView(entriesOff, "wheels").hits).slice(0, 2).sort(), ["Front Wheel", "Rear Wheel"]);
  assert.deepEqual(names(P.pickerView(entriesOff, "door glasses").hits).slice(0, 2).sort(), ["Front Door Glass", "Rear Door Glass"]);
});
check("L6: 'lamp' finds the Headlamp; 'light' / 'headlight' / 'tail light' / 'fog lights' find the lamps", () => {
  assert.ok(names(P.pickerView(entriesOff, "lamp").hits).includes("Headlamp"));
  const lights = names(P.pickerView(entriesOff, "light").hits);
  for (const n of ["Headlamp", "Tail Lamp", "Fog Lamp"]) assert.ok(lights.includes(n), `light -> ${n}: ${lights}`);
  assert.equal(P.pickerView(entriesOff, "headlight").hits[0].entry.name, "Headlamp");
  assert.deepEqual(topPick(entriesOff, "tail light lh"), { part: "Left Tail Lamp", other_part_name: null });
  assert.equal(P.pickerView(entriesOff, "fog lights").hits[0].entry.name, "Fog Lamp");
  assert.deepEqual(P.lampNames(["Headlamp"]).sort(), ["Head lamp", "Head light", "Headlight"]);
});

// ---------------------------------------------------------------------------
console.log("-- Other / Not Listed: one confirmation, \"Did you mean\" first (M4, L4)");
check("M4: typed words never go in at once -- otherStep asks first; nothing typed is an unnamed Other at once", () => {
  const typed = P.otherStep(entriesOff, "xyzzy 1234", null);
  assert.equal(typed.confirm, true);
  assert.deepEqual(typed.suggestions, []);
  assert.deepEqual(typed.pick, { part: OTHER, other_part_name: "Xyzzy 1234" });
  assert.deepEqual(P.otherStep(entriesOff, "  ", null), { pick: { part: OTHER, other_part_name: null }, confirm: false, suggestions: [] });
});
check("M4: Did you mean is looser: 'rear bumper retainer' -> Rear Bumper; 'front bumper ganrish grille' -> Front Bumper, Front Grille", () => {
  assert.equal(P.didYouMean(entriesOff, "rear bumper retainer")[0].entry.name, "Rear Bumper");
  assert.deepEqual(names(P.didYouMean(entriesOff, "front bumper ganrish grille")).slice(0, 2).sort(), ["Front Bumper", "Front Grille"]);
  assert.equal(P.otherStep(entriesOff, "rear bumper retainer", null).suggestions[0].entry.name, "Rear Bumper");
});
check("Did you mean: strict matches first, at most three; none for gibberish, nothing typed or side words alone", () => {
  assert.equal(P.didYouMean(entriesOn, "rear bumper absorbr")[0].entry.name, "Rear Bumper Absorber");
  assert.equal(P.didYouMean(entriesOff, "lamp").length, 3);
  assert.equal(P.didYouMean(entriesOff, "taillamp")[0].entry.name, "Tail Lamp");
  assert.deepEqual(P.didYouMean(entriesOff, "xyzzy"), []);
  assert.deepEqual(P.didYouMean(entriesOff, ""), []);
  assert.deepEqual(P.didYouMean(entriesOff, "lh"), []);
});
check("Other keeps the typed name, tidied as the name box did (spaces, capital, 80 characters)", () => {
  assert.deepEqual(P.otherPick("  left rear   air vent "), { part: OTHER, other_part_name: "Left rear air vent" });
  assert.deepEqual(P.otherPick(""), { part: OTHER, other_part_name: null });
  assert.equal(P.otherPick("x".repeat(120)).other_part_name.length, 80);
});
check("L4: a typed Other takes the side toggle (LH / RH last) unless its words name a side; '?' adds none", () => {
  assert.equal(P.otherPick("air vent", "left").other_part_name, "Air vent LH");
  assert.equal(P.otherPick("air vent", "right").other_part_name, "Air vent RH");
  assert.equal(P.otherPick("left air vent", "right").other_part_name, "Left air vent");
  assert.equal(P.otherPick("air vent", "unknown").other_part_name, "Air vent");
  assert.equal(P.otherStep(entriesOff, "air vent", "left").pick.other_part_name, "Air vent LH");
});

// ---------------------------------------------------------------------------
console.log("-- already listed (M1, L5)");
const listedItems = [{ part: "Rear Bumper" }, { part: "Left Tail Lamp" }, { part: OTHER, other_part_name: "Rear air vent" }];
const rows = P.listedRows(listedItems);
check("M1: a listed part says 'already row N'; a sided part still to choose says which side is listed", () => {
  assert.equal(P.listedNote(entryNamed(entriesOff, "Rear Bumper"), null, rows), "already row 1");
  assert.equal(P.listedNote(entryNamed(entriesOff, "Tail Lamp"), null, rows), "LH is row 2");
  assert.equal(P.listedNote(entryNamed(entriesOff, "Tail Lamp"), "left", rows), "already row 2");
  assert.equal(P.listedNote(entryNamed(entriesOff, "Tail Lamp"), "right", rows), null);
  assert.equal(P.listedNote(entryNamed(entriesOff, "Bonnet"), null, rows), null);
  assert.equal(P.listedNote(entryNamed(entriesOff, "Bonnet"), null, P.listedRows([])), null);
});
check("M1: the map panel's own part isn't put first when it is listed already", () => {
  const list = listFor(false);
  assert.deepEqual(P.zonePreferred("rear_bumper", list), ["Rear Bumper"]);
  assert.deepEqual(P.zonePreferred("rear_bumper", list, rows), []);
  const view = P.pickerView(entriesOff, "", { ...P.presetFromZone("rear_bumper"), preferNames: P.zonePreferred("rear_bumper", list, rows), listed: rows });
  assert.notEqual(view.hits[0].entry.name, "Rear Bumper");
  assert.ok(names(view.hits).includes("Rear Bumper"), "still there, after the rest");
  // Not listed: first again.
  const fresh = P.pickerView(entriesOff, "", { ...P.presetFromZone("rear_bumper"), preferNames: P.zonePreferred("rear_bumper", list), listed: P.listedRows([]) });
  assert.equal(fresh.hits[0].entry.name, "Rear Bumper");
});
check("M1: the case page's pick asks as the Review step does (checkAdd): a listed part is a duplicate", () => {
  const pick = P.pickFor(entryNamed(entriesOff, "Rear Bumper"), null);
  assert.equal(R.checkAdd(listedItems, pick.part, pick.other_part_name)?.kind, "duplicate");
  assert.equal(R.checkAdd(listedItems, "Right Tail Lamp", null)?.kind, "mirror");
  assert.equal(R.checkAdd(listedItems, OTHER, "rear air vent")?.kind, "duplicate");
});
check("L5: a named Other with a side gets the other-side question too (LH vs RH, 'Left x' vs 'x RH')", () => {
  const items = [{ part: OTHER, other_part_name: "Front bumper bracket LH" }];
  const a = R.checkAdd(items, OTHER, "Front bumper bracket RH");
  assert.deepEqual([a?.kind, a?.index, a?.listed, a?.part], ["mirror", 0, "Front bumper bracket LH", "Front bumper bracket RH"]);
  assert.equal(R.checkAdd(items, OTHER, "Right front bumper bracket")?.kind, "mirror");
  assert.equal(R.checkAdd(items, OTHER, "Front bumper bracket")?.kind ?? null, null, "no side: nothing to mirror");
  assert.equal(R.checkAdd(items, OTHER, "Rear air vent RH") ?? null, null);
  assert.equal(R.checkAdd(items, OTHER, "Front bumper bracket LH")?.kind, "duplicate");
});

// ---------------------------------------------------------------------------
console.log("-- the case page's add row after a pick (M5)");
const half = { part: "Left Tail Lamp", other_part_name: "", condition: "Broken", remarks: "Replace", photo_reference: "P03" };
check("M5: a new add from the map or a photo starts the row afresh, with only its photo", () => {
  assert.deepEqual(P.nextAddRow(half, { part: "Bonnet", other_part_name: null }, { fresh: true, photoLabel: null }), {
    part: "Bonnet", other_part_name: "", condition: "", remarks: "", photo_reference: "",
  });
  assert.deepEqual(P.nextAddRow(half, { part: "Bonnet", other_part_name: null }, { fresh: true, photoLabel: "P07" }).photo_reference, "P07");
});
check("M5: 'Change the part' keeps the row's condition, remarks and photo", () => {
  assert.deepEqual(P.nextAddRow(half, { part: "Right Tail Lamp", other_part_name: null }, { fresh: false, photoLabel: null }), {
    ...half, part: "Right Tail Lamp",
  });
  assert.deepEqual(P.nextAddRow(null, { part: OTHER, other_part_name: "Air vent LH" }, { fresh: false, photoLabel: null }), {
    part: OTHER, other_part_name: "Air vent LH", condition: "", remarks: "", photo_reference: "",
  });
});

// ---------------------------------------------------------------------------
console.log("-- pre-filters: area chips, the map's panel, the photo (M2, M6, L11)");
check("an area chip: only that area; matches elsewhere counted (shown as '+N in other areas')", () => {
  const rear = P.pickerView(entriesOff, "", { area: "rear" });
  assert.ok(rear.hits.length > 10 && rear.hits.every((h) => h.entry.area === "rear"));
  const front = P.pickerView(entriesOff, "lamp", { area: "front" });
  assert.ok(front.hits.length > 0 && front.hits.every((h) => h.entry.area === "front"));
  assert.ok(front.elsewhere > 0);
});
check("map panel (left front door): its parts only, its own part first, the side LH -- the old prefill, Left Front Door", () => {
  const preset = P.presetFromZone("l_door_front");
  assert.deepEqual(preset, { zone: "l_door_front", side: "left", sideFrom: "zone" });
  const view = P.pickerView(entriesOff, "", { ...preset, preferNames: P.zonePreferred("l_door_front", listFor(false)) });
  assert.ok(view.hits.every((h) => P.onZone(h.entry, "l_door_front")));
  assert.ok(names(view.hits).includes("Front Door Glass"), "a door means its glass too");
  assert.deepEqual(P.pickFor(view.hits[0].entry, preset.side), { part: "Left Front Door", other_part_name: null });
});
for (const [label, entries] of [["with the library", entriesOff], ["M2: no library (loading or failed)", P.pickerEntries(null, tax(false))]]) {
  check(`every panel of the 3D map has parts, ${label}; the old prefill (partForMapAdd) is the first pick`, () => {
    const list = listFor(false);
    for (const zone of Object.keys(Z.ZONE_LABELS)) {
      const preset = P.presetFromZone(zone);
      const view = P.pickerView(entries, "", { ...preset, preferNames: P.zonePreferred(zone, list) });
      assert.ok(view.hits.length > 0, `${zone}: no part`);
      const old = R.partForMapAdd(zone, list);
      if (old) assert.equal(P.pickFor(view.hits[0].entry, preset.side)?.part, old, zone);
    }
  });
}
check("M2 / L11: the empty list says 'Loading…' while the parts load, never 'No part here.'; a panel search offers all parts", () => {
  const base = { ready: true, typed: "", area: null, zone: "front_bumper" };
  assert.equal(P.emptyText({ ...base, loading: true, searching: false }), "Loading the parts…");
  assert.notEqual(P.emptyText({ ...base, loading: true, searching: false }), "No part here.");
  assert.equal(P.emptyText({ ...base, loading: false, searching: true, typed: "headlamp" }), 'No part matches "headlamp" on this panel.');
  const view = P.pickerView(entriesOff, "headlamp", { zone: "front_bumper" });
  assert.equal(view.hits.length, 0, "the headlamp isn't on the bumper");
  assert.ok(P.pickerView(entriesOff, "headlamp").hits.length > 0, "'Search all parts' finds it");
});
check("photo with a part tag: the tag is the search, its side the side ('check')", () => {
  const preset = P.presetFromPhoto({ area: "front-right", part: "Right Headlamp", label: "P04" });
  assert.equal(preset.query, "Right Headlamp");
  assert.equal(preset.side, "right");
  assert.equal(preset.sideFrom, "photo");
  assert.equal(preset.preferArea, "front");
  assert.deepEqual(topPick(entriesOff, preset.query), { part: "Right Headlamp", other_part_name: null });
});
check("M6: a photo's area is a preference, never a chip: its parts first (browsing and within a tier), the others still there", () => {
  for (const area of Object.keys(P.SORTER_AREAS)) assert.equal(P.presetFromPhoto({ area }).area, null, area);
  const corner = P.presetFromPhoto({ area: "rear-left", part: null });
  assert.deepEqual([corner.preferArea, corner.side], ["rear", "left"]);
  const browse = P.pickerView(entriesOff, "", { preferArea: "front", areas: P.pickerAreas(lib) });
  assert.equal(browse.groups[0].key, "front");
  assert.equal(browse.hits.length, entriesOff.length, "nothing hidden");
  // 'lamp': all "all words" -- the photo's area first, whatever the uses.
  assert.equal(P.pickerView(entriesOff, "lamp", { preferArea: "front" }).hits[0].entry.area, "front");
  assert.equal(P.pickerView(entriesOff, "lamp", { preferArea: "rear" }).hits[0].entry.area, "rear");
  const both = names(P.pickerView(entriesOff, "lamp", { preferArea: "front" }).hits);
  assert.ok(both.includes("Tail Lamp") && both.includes("Fog Lamp"));
  assert.equal(P.presetFromPhoto({ area: "front", part: OTHER }).query, "", "an Other tag isn't a search");
  assert.equal(P.presetFromPhoto(null).query, "");
});

// ---------------------------------------------------------------------------
console.log("-- prints as (this car's body), the mini diagram, the focus trap");
const vehicles = Object.fromEntries(fixture.vehicles.map((v) => [v.vehicle.plate_number, v.vehicle]));
const firm = { ...tax(false), ...fixture.taxonomy };
check("prints as the Review step's own rule (reviewChecks.printsAs): boot lid on a sedan, tail gate on an SUV", () => {
  const boot = entryNamed(entriesOff, "Boot Lid");
  assert.equal(P.printsAsFor(boot, null, firm, vehicles.TESTPA4), R.printsAs({ part: "Boot Lid", human_verified: true }, firm, vehicles.TESTPA4).text);
  assert.equal(P.printsAsFor(boot, null, firm, vehicles.TESTPA4), "Boot lid assy");
  assert.equal(P.printsAsFor(boot, null, firm, vehicles.TESTPA1), "Tail gate assy");
});
check("prints as: the side's wording, 'LH / RH' while no side is chosen; older API: the library's wording", () => {
  const tail = entryNamed(entriesOff, "Tail Lamp");
  assert.equal(P.printsAsFor(tail, "left", firm, vehicles.TESTPA4), "Tail lamp LH");
  assert.equal(P.printsAsFor(tail, null, firm, vehicles.TESTPA4), "Tail lamp LH / RH");
  // A name the firm's list doesn't word prints as written: no "Left" in "LH / RH".
  assert.equal(P.printsAsFor(entryNamed(entriesOff, "Rear Bumper Reflector"), null, firm, vehicles.TESTPA4), "Rear Bumper Reflector LH / RH");
  const quarter = entryNamed(entriesOff, "Rear Quarter Panel");
  assert.equal(P.printsAsFor(quarter, "left", tax(false), null), `${quarter.firm_wording} LH`);
});
check("the mini diagram lights the side's zone, or every zone of the part", () => {
  const tail = entryNamed(entriesOff, "Tail Lamp");
  assert.deepEqual(P.highlightFor(tail, "left"), ["l_taillamp"]);
  assert.deepEqual([...P.highlightFor(tail, null)].sort(), ["l_taillamp", "r_taillamp"]);
  assert.deepEqual(P.highlightFor(entryNamed(entriesOff, "Rear Bumper"), "left"), ["rear_bumper"]);
});
check("M3: the focus trap counts only what Tab reaches (tabIndex >= 0, enabled, shown)", () => {
  const el = (over) => ({ tabIndex: 0, disabled: false, offsetParent: {}, getAttribute: () => null, ...over });
  assert.equal(P.isTabbable(el({})), true);
  assert.equal(P.isTabbable(el({ tabIndex: -1 })), false, "a tabindex -1 chip");
  assert.equal(P.isTabbable(el({ disabled: true })), false);
  assert.equal(P.isTabbable(el({ offsetParent: null })), false, "hidden");
  assert.equal(P.isTabbable(el({ getAttribute: (n) => (n === "aria-hidden" ? "true" : null) })), false);
});

// ---------------------------------------------------------------------------
console.log("-- the wiring (from the source: JSX Node can't run)");
const source = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8").replace(/\r\n/g, "\n");
const picker = source("../components/PartPicker.tsx");
check("PartPicker: picks wait for a list (H1); Other goes through otherStep (M4); the trap uses isTabbable (M3)", () => {
  assert.match(picker, /const ready = entries\.length > 0;/);
  assert.match(picker, /const choose = \(entry: PickerEntry, s: PickSide \| null\) => \{\n\s*if \(!ready\) return;/);
  assert.match(picker, /const step = otherStep\(entries, query, side\);\n\s*if \(!step\.confirm\) onPick\(step\.pick\);\n\s*else setConfirm/);
  assert.match(picker, /\.filter\(isTabbable\)/);
  assert.match(picker, /emptyText\(\{ ready, loading,/);
});
check("PartPicker: no buttons inside the list's options (L2); 'Which side?' is a bar outside the list", () => {
  const options = picker.slice(picker.indexOf("const renderHit"), picker.indexOf("const otherIndex"));
  assert.doesNotMatch(options, /<button/);
  const other = picker.slice(picker.indexOf('id={rowId(otherIndex)}'), picker.indexOf("</aside>"));
  assert.doesNotMatch(other.slice(0, other.indexOf("</div>\n          )}")), /<button/);
  assert.match(picker, /className="pp-ask-bar"/);
  assert.match(picker, /aria-controls=\{confirm \? undefined : listId\}/);
});
check("PartPicker: the page doesn't scroll behind it, the list contains its scroll, a drag out of the box doesn't close it (L3)", () => {
  assert.match(picker, /body\.style\.overflow = "hidden"/);
  assert.match(picker, /downOnBackdrop\.current = e\.target === e\.currentTarget/);
  assert.match(picker, /if \(downOnBackdrop\.current && e\.target === e\.currentTarget\) onClose\(\)/);
  const css = source("../app/globals.css");
  assert.match(css.slice(css.indexOf(".pp-list {"), css.indexOf("}", css.indexOf(".pp-list {"))), /overscroll-behavior: contain/);
});
check("PartPicker: the photo's area is a note ('Showing Front first'), the map panel has 'Search all parts' (M6, L11)", () => {
  assert.match(picker, /Showing \{areaLabel\(preferArea, areas\)\} first \(from the photo\)\./);
  assert.equal((picker.match(/Search all parts/g) || []).length, 2);
});
check("Review step: the add row opens the picker; a pick adds the old row; listed parts are marked; the library is prefetched", () => {
  const tsx = source("../components/intake/ReviewStep.tsx");
  const row = tsx.slice(tsx.indexOf('<div className="intake-add-row">'), tsx.indexOf("</div>", tsx.indexOf('<div className="intake-add-row">')));
  assert.doesNotMatch(row, /<Select/);
  assert.match(row, /setAddPicker\(\{ title: "Add a damaged part"/);
  assert.match(tsx, /<PartPicker[\s\S]*?listed=\{damage\}[\s\S]*?addPicked\(pick, photoId\)/);
  // A list part: { part, severity: null, human_verified: true } as before.
  assert.match(tsx, /\{\s*part,\s*\.\.\.\(other_part_name \? \{ other_part_name \} : \{\}\),\s*severity: null,\s*\.\.\.\(photoId \? \{ photo_reference: `@\$\{photoId\}`, bbox_2d: null \} : \{\}\),\s*human_verified: true,\s*\}/);
  assert.match(tsx, /onAddDamage=\{addForZone\}/);
  assert.match(tsx, /onAddFromPhoto=\{addFromViewer\}/);
  assert.match(tsx, /useEffect\(\(\) => prefetchPartsLibrary\(\), \[\]\)/);
  assert.match(tsx, /photoId \? "condition" : undefined/, "after a photo's add, the cursor on the new row (L1)");
});
check("Case tab 1: picks ask checkAdd first (M1) and fill a fresh row (M5); the cursor goes to the row (L1)", () => {
  const table = source("../components/DetectedPartsTable.tsx");
  assert.doesNotMatch(table, /aria-label="New part"/, "the add row's part <select> is gone");
  assert.match(table, /const check = checkAdd\(damageEntries, pick\.part, pick\.other_part_name\);\n\s*if \(check\) setAddAsk\(\{ check, go: \(\) => fillAdd\(pick, opts\) \}\);\n\s*else fillAdd\(pick, opts\);/);
  assert.match(table, /<AddCheckDialog/);
  assert.match(table, /setAdding\(\(cur\) => nextAddRow\(cur, pick, opts\)\)/);
  assert.match(table, /fresh: true \}\)/);
  assert.match(table, /listed=\{damageEntries\}/);
  assert.match(table, /"Name of the new part" : "New part condition"/);
  const tab = source("../components/DamageDetectionTab.tsx");
  assert.match(tab, /addRequest=\{addRequest\}/);
  assert.match(tab, /preset: presetFromZone\(zoneKey\)/);
  assert.match(tab, /onAddFromPhoto=\{readOnly \? undefined : addFromPhoto\}/);
});
check("3D map: '+ Add part here' on any panel's card (not only an empty one)", () => {
  const tsx = source("../components/DamageMapPanel.tsx");
  assert.match(tsx, /\{card\.zone && onAddDamage && !readOnly && \(\s*<div className="dmap-card-add">/);
  assert.match(tsx, /Add part here/);
  assert.doesNotMatch(tsx, /Add as damage/);
});
check("M7: photo viewer's 'Add part seen here' only in photo mode, never read-only (never beside a suggestion's Add)", () => {
  const tsx = source("../components/EvidenceViewer.tsx");
  assert.match(tsx, /onAddFromPhoto && !readOnly && mode === "photo" && photoRef && src && \(/);
  assert.match(tsx, /Add part seen here/);
});

if (failed) {
  console.log(`\n${failed} check(s) failed`);
  process.exit(1);
}
console.log("\nall part picker checks passed");
