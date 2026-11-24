#!/usr/bin/env node
/**
 * Checks the parts library's pure parts (L-library-cycle.md step 2A):
 * lib/partSearch.ts -- the B.2 matching rules the library page uses now and
 * the part picker (step 3A) next -- and lib/library.ts -- grouping area ->
 * assembly -> component -> fittings, the filters, the diagram's counts --
 * against a small made-up library and the real core
 * (bot-service app/parts/library_core.json, the code's own parts).
 *
 *   node apps/dashboard/scripts/check-library.mjs
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

const S = await import("../lib/partSearch.ts");
const L = await import("../lib/library.ts");

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

const names = (hits) => hits.map((h) => h.entry.name);

// ---------------------------------------------------------------------------
console.log("-- normalising and the query");
check("lower case, punctuation off", () => assert.equal(S.normalize("Sill / Rocker  Panel!"), "sill rocker panel"));
check("side words become the side: LH", () => {
  const q = S.parseQuery("Tail lamp LH");
  assert.deepEqual([q.words, q.side], [["tail", "lamp"], "left"]);
});
check("side words: L/H, R.H., left, right", () => {
  assert.equal(S.parseQuery("fender l/h").side, "left");
  assert.equal(S.parseQuery("fender R.H.").side, "right");
  assert.equal(S.parseQuery("Right front door").side, "right");
  assert.deepEqual(S.parseQuery("left front door").words, ["front", "door"]);
});
check("more side words: LHS / RHS, L.H.S., left hand (side), right-hand side", () => {
  const read = (t) => [S.parseQuery(t).side, S.parseQuery(t).words.join(" ")];
  assert.deepEqual(read("tail lamp LHS"), ["left", "tail lamp"]);
  assert.deepEqual(read("tail lamp rhs"), ["right", "tail lamp"]);
  assert.deepEqual(read("L.H.S. door"), ["left", "door"]);
  assert.deepEqual(read("r.h.s door"), ["right", "door"]);
  assert.deepEqual(read("left hand door"), ["left", "door"]);
  assert.deepEqual(read("lefthand door"), ["left", "door"]);
  assert.deepEqual(read("right-hand side door"), ["right", "door"]);
  assert.deepEqual(read("rear bumper lh side"), ["left", "rear bumper"]);
});
check("nearside = LEFT, offside = RIGHT (Malaysia drives on the left); N/S, O/S", () => {
  const read = (t) => [S.parseQuery(t).side, S.parseQuery(t).words.join(" ")];
  assert.deepEqual(read("nearside mirror"), ["left", "mirror"]);
  assert.deepEqual(read("near side mirror"), ["left", "mirror"]);
  assert.deepEqual(read("near-side mirror"), ["left", "mirror"]);
  assert.deepEqual(read("N/S mirror"), ["left", "mirror"]);
  assert.deepEqual(read("offside door"), ["right", "door"]);
  assert.deepEqual(read("off side door"), ["right", "door"]);
  assert.deepEqual(read("O/S door"), ["right", "door"]);
});
check("both sides named: no side", () => {
  assert.equal(S.parseQuery("lamp lh rh").side, null);
  assert.equal(S.parseQuery("nearside and offside lamps").side, null);
});
check("a side word inside another word is not a side", () => {
  for (const t of ["lhd", "brighton", "rhombus", "upright", "offset", "nos", "nearby", "l hinge", "bush"]) {
    assert.equal(S.parseQuery(t).side, null, t);
    assert.ok(S.parseQuery(t).words.length > 0, t);
  }
});
check("singular and plural are one word", () => {
  assert.equal(S.stem("clips"), "clip");
  assert.equal(S.stem("hinges"), S.stem("hinge"));
  assert.equal(S.stem("glasses"), "glass");
  assert.equal(S.stem("glass"), "glass");
  assert.equal(S.stem("brushes"), "brush");
  assert.equal(S.stem("assemblies"), "assembly");
  assert.equal(S.stem("chassis"), "chassis");
});
check("one wrong letter or one swap, words of 5+ letters only", () => {
  assert.equal(S.typoDistance("ganrish", "garnish"), 1);
  assert.ok(S.nearWord("ganrish", "garnish"));
  assert.ok(S.nearWord("bumpr", "bumper"));
  assert.ok(!S.nearWord("lamb", "lamp"), "a 4-letter word gets no typo");
  assert.ok(!S.nearWord("rear", "reer"), "nor a 4-letter one");
  assert.ok(!S.nearWord("bonnet", "bumper"));
});

// ---------------------------------------------------------------------------
console.log("-- ranking (exact > starts-with > all words > alias > close spelling; ties: uses, area)");
const E = (name, extra = {}) => ({ key: name.toLowerCase().replace(/\W+/g, "-"), name, uses: 0, area: "rear", sided: false, ...extra });
const entries = [
  E("Rear Bumper Beam", { uses: 9 }),
  E("Rear Bumper", { uses: 1 }),
  E("Bumper Rear Reflector", { uses: 20 }),
  E("Boot Lid", { wordings: ["Boot lid assy", "Tail gate assy"], aliases: ["Rear bumper cover"], uses: 50 }),
  E("Rear Bumpar Guard"),
  E("Front Bumper", { area: "front", uses: 3 }),
  E("Tail Lamp", { sided: true, uses: 2 }),
  E("Boot Lid Garnish", { uses: 1 }),
  E("Front Wheel Arch Trim", { area: "front", wordings: ["Front fender wheel arch garnish"], sided: true }),
];
check("the order of the tiers", () => {
  const hits = S.searchParts(entries, "rear bumper");
  assert.deepEqual(
    hits.map((h) => [h.entry.name, h.tier]),
    [
      ["Rear Bumper", "exact"],
      ["Rear Bumper Beam", "starts"],
      ["Bumper Rear Reflector", "words"],
      ["Boot Lid", "alias"],
      ["Rear Bumpar Guard", "near"],
    ],
  );
});
check("an exact name beats one used more", () => assert.equal(names(S.searchParts(entries, "Rear Bumper"))[0], "Rear Bumper"));
check("ties go to more uses", () => {
  const hits = S.searchParts([E("Rear Lamp A", { uses: 1 }), E("Rear Lamp B", { uses: 7 })], "rear lamp");
  assert.deepEqual(names(hits), ["Rear Lamp B", "Rear Lamp A"]);
});
check("then to the preferred area", () => {
  const two = [E("Door Seal X", { area: "rear" }), E("Door Seal Y", { area: "side" })];
  assert.deepEqual(names(S.searchParts(two, "door seal", { preferArea: "side" })), ["Door Seal Y", "Door Seal X"]);
  assert.deepEqual(names(S.searchParts(two, "door seal")), ["Door Seal X", "Door Seal Y"]);
});
check("the firm's wording is a name: 'tail gate' finds the boot lid", () => {
  const hits = S.searchParts(entries, "tail gate");
  assert.equal(hits[0].entry.name, "Boot Lid");
  assert.equal(hits[0].tier, "starts");
  assert.equal(hits[0].matched, "Tail gate assy");
});
check("written as one word: 'tailgate' finds 'Tail gate assy'", () => assert.equal(names(S.searchParts(entries, "tailgate"))[0], "Boot Lid"));
check("typo 'ganrish' finds the garnish (close spelling)", () => {
  const hits = S.searchParts(entries, "ganrish");
  assert.deepEqual(names(hits), ["Boot Lid Garnish", "Front Wheel Arch Trim"]);
  assert.ok(hits.every((h) => h.tier === "near"));
});
check("a short word gets no typo: 'lamb' finds nothing", () => assert.deepEqual(S.searchParts(entries, "lamb"), []));
check("plural: 'tail lamps' finds the tail lamp", () => assert.equal(names(S.searchParts(entries, "tail lamps"))[0], "Tail Lamp"));
check("a half-typed last word: 'rear bum'", () => assert.deepEqual(names(S.searchParts(entries, "rear bum")).slice(0, 2), ["Rear Bumper Beam", "Rear Bumper"]));
check("a side word never hides a part without one: 'rear bumper lh' still finds the rear bumper first", () => {
  const hits = S.searchParts(entries, "rear bumper lh");
  assert.deepEqual(names(hits), names(S.searchParts(entries, "rear bumper")));
  assert.equal(hits[0].entry.name, "Rear Bumper");
  assert.equal(hits[0].side, null, "the side doesn't apply to it");
});
check("a part with LH / RH gets the side; one without gets none", () => {
  const hits = S.searchParts([...entries, E("Rear Lamp Panel")], "lamp lh");
  assert.deepEqual(hits.map((h) => [h.entry.name, h.side]), [["Tail Lamp", "left"], ["Rear Lamp Panel", null]]);
  assert.equal(S.searchParts(entries, "tail lamp")[0].side, null, "no side in the query, none on the hit");
  assert.equal(S.searchParts(entries, "nearside tail lamp")[0].side, "left");
  assert.equal(S.searchParts(entries, "tail lamp O/S")[0].side, "right");
});
check("on a tie, a part that takes the side comes first", () => {
  const two = [E("Door Trim A"), E("Door Trim B", { sided: true })];
  assert.deepEqual(names(S.searchParts(two, "door trim")), ["Door Trim A", "Door Trim B"]);
  assert.deepEqual(names(S.searchParts(two, "door trim lhs")), ["Door Trim B", "Door Trim A"]);
  const used = [E("Door Trim A", { uses: 5 }), E("Door Trim B", { sided: true })];
  assert.deepEqual(names(S.searchParts(used, "door trim lh")), ["Door Trim A", "Door Trim B"], "uses still come first");
});
check("side words alone find nothing (the page shows its list)", () => assert.deepEqual(S.searchParts(entries, "LH"), []));
check("area filter", () => {
  assert.deepEqual(names(S.searchParts(entries, "bumper", { area: "front" })), ["Front Bumper"]);
  assert.ok(!names(S.searchParts(entries, "bumper", { area: "rear" })).includes("Front Bumper"));
});
check("limit", () => assert.equal(S.searchParts(entries, "rear", { limit: 2 }).length, 2));

// ---------------------------------------------------------------------------
console.log("-- the real core (bot-service app/parts/library_core.json)");
const coreUrl = new URL("../../bot-service/app/parts/library_core.json", import.meta.url);
const core = JSON.parse(readFileSync(coreUrl, "utf8"));
const lib = { ...core, staff: false, fittings: [] };
check("86 components, 7 areas", () => {
  assert.equal(lib.components.length, 86);
  assert.deepEqual(lib.areas.map((a) => a.key), ["front", "rear", "side", "roof", "under", "wheels", "inside"]);
});
const real = L.searchEntries(lib);
const top = (q, opts) => S.searchParts(real, q, opts)[0]?.entry.name;
check("'rear bumper' -> Rear Bumper", () => assert.equal(top("rear bumper"), "Rear Bumper"));
check("'Rear bumper fascia' (the firm's wording) -> Rear Bumper", () => assert.equal(top("Rear bumper fascia"), "Rear Bumper"));
check("'hood' (alias) -> Bonnet", () => assert.equal(top("hood"), "Bonnet"));
check("'tailgate' -> Boot Lid", () => assert.equal(top("tailgate"), "Boot Lid"));
check("'ganrish' -> a garnish (the firm's typo is an alias of the arch garnish)", () => assert.match(top("ganrish"), /Wheel Arch Trim/));
check("'garnsh' (a typo nobody wrote down) -> a garnish", () => assert.match(top("garnsh"), /Garnish|Wheel Arch Trim/));
check("'tail lamp lh' -> Tail Lamp, side left", () => {
  const hits = S.searchParts(real, "tail lamp lh");
  assert.deepEqual([hits[0].entry.name, hits[0].side], ["Tail Lamp", "left"]);
});
check("'tail lamp lhs', 'tail lamp nearside', 'N/S tail lamp' -> Tail Lamp, left", () => {
  for (const q of ["tail lamp lhs", "tail lamp nearside", "N/S tail lamp", "left hand tail lamp"]) {
    const hit = S.searchParts(real, q)[0];
    assert.deepEqual([hit?.entry.name, hit?.side], ["Tail Lamp", "left"], q);
  }
  const hit = S.searchParts(real, "offside tail lamp")[0];
  assert.deepEqual([hit.entry.name, hit.side], ["Tail Lamp", "right"]);
});
check("'rear bumper lh' -> Rear Bumper first (no side), its sided parts get LH", () => {
  const hits = S.searchParts(real, "rear bumper lh");
  assert.deepEqual([hits[0].entry.name, hits[0].side], ["Rear Bumper", null]);
  const bracket = hits.find((h) => h.entry.name === "Rear Bumper Bracket");
  assert.equal(bracket?.side, "left");
});
check("'fuel flap rh' -> the fuel filler flap (on one side; its name takes none)", () => {
  const hit = S.searchParts(real, "fuel flap rh")[0];
  assert.deepEqual([hit?.entry.name, hit?.side], ["Fuel Filler Flap", null]);
});
check("no name, wording or alias in the library holds a side word", () => {
  const texts = real.flatMap((e) => [e.name, ...(e.wordings ?? []), ...(e.aliases ?? [])].filter(Boolean));
  assert.deepEqual(texts.filter((t) => S.liftSide(t).side !== null || /\b(lh|rh|lhs|rhs)\b/i.test(t)), []);
});
check("'door hinges' -> a door hinge", () => assert.match(top("door hinges"), /Door Hinge/));
check("'splash shield' in the front only", () => assert.equal(top("splash shield", { area: "front" }), "Front Wheel Arch Liner"));

// ---------------------------------------------------------------------------
console.log("-- grouping and filters (lib/library.ts)");
const fitting = (name, parents, extra = {}) => ({
  key: `seed-${name.toLowerCase().replace(/\W+/g, "-")}`,
  name,
  kind: "fitting",
  status: "seed",
  family: null,
  parent_key: parents[0] ?? null,
  parent_keys: parents,
  area: parents.length ? lib.components.find((c) => c.key === parents[0]).area : null,
  assembly: null,
  panels: parents.length ? lib.components.find((c) => c.key === parents[0]).panels : [],
  sided: false,
  aliases: [],
  ...extra,
});
const withSeeds = {
  ...lib,
  staff: true,
  components: lib.components.map((c) => ({ ...c, uses: c.key === "rear-bumper" ? 4 : c.key === "tail-lamp" ? 2 : 0 })),
  fittings: [
    fitting("Rear bumper clips", ["rear-bumper"], { uses: 3 }),
    fitting("Sport rim", ["front-wheel", "rear-wheel"], { kind: "part" }),
    fitting("Body panel joint sealer", []),
  ],
};
check("area -> assembly -> component, every component once", () => {
  const groups = L.groupLibrary(lib);
  assert.deepEqual(groups.map((g) => g.key), ["front", "rear", "side", "roof", "under", "wheels", "inside"]);
  const keys = groups.flatMap((g) => g.assemblies.flatMap((a) => a.rows.map((r) => r.component.key)));
  assert.equal(keys.length, 86);
  assert.equal(new Set(keys).size, 86);
});
check("fittings indented under their part; a two-part seed under both", () => {
  const groups = L.groupLibrary(withSeeds);
  const row = (key) => groups.flatMap((g) => g.assemblies.flatMap((a) => a.rows)).find((r) => r.component.key === key);
  assert.deepEqual(row("rear-bumper").fittings.map((f) => f.name), ["Rear bumper clips"]);
  assert.deepEqual(row("front-wheel").fittings.map((f) => f.name), ["Sport rim"]);
  assert.deepEqual(row("rear-wheel").fittings.map((f) => f.name), ["Sport rim"]);
  assert.equal(groups.at(-1).key, "unplaced");
  assert.deepEqual(groups.at(-1).loose.map((f) => f.name), ["Body panel joint sealer"]);
});
check("area counts and uses (staff)", () => {
  const rear = L.groupLibrary(withSeeds).find((g) => g.key === "rear");
  assert.equal(rear.components, lib.components.filter((c) => c.area === "rear").length);
  assert.equal(rear.fittings, 1);
  assert.equal(rear.uses, 6);
  assert.equal(L.groupLibrary(lib).find((g) => g.key === "rear").uses, null, "no counts without staff");
});
check("filter: area", () => {
  const groups = L.groupLibrary(lib, { ...L.NO_FILTERS, area: "wheels" });
  assert.deepEqual(groups.map((g) => g.key), ["wheels"]);
  assert.equal(groups[0].components, 4);
});
check("filter: a panel on the diagram (the door means its glass too)", () => {
  const groups = L.groupLibrary(lib, { ...L.NO_FILTERS, panel: "l_door_front" });
  const keys = groups.flatMap((g) => g.assemblies.flatMap((a) => a.rows.map((r) => r.component.key)));
  assert.ok(keys.includes("front-door") && keys.includes("front-door-glass") && keys.includes("front-window-regulator"));
  assert.ok(!keys.includes("rear-door"));
});
check("filter: no panel on the model", () => {
  const groups = L.groupLibrary(lib, { ...L.NO_FILTERS, area: "none" });
  const comps = groups.flatMap((g) => g.assemblies.flatMap((a) => a.rows.map((r) => r.component)));
  assert.ok(comps.length > 0 && comps.every((c) => c.panels.length === 0));
  assert.equal(comps.length, L.areaCounts(lib).none);
});
check("filter: kind", () => {
  const comps = L.groupLibrary(lib, { ...L.NO_FILTERS, kind: "panel" }).flatMap((g) => g.assemblies.flatMap((a) => a.rows));
  assert.ok(comps.length > 0 && comps.every((r) => r.component.kind === "panel"));
});
check("filter: seeds only -- their parts shown as context", () => {
  const rows = L.groupLibrary(withSeeds, { ...L.NO_FILTERS, status: "seed" }).flatMap((g) => g.assemblies.flatMap((a) => a.rows));
  assert.deepEqual(rows.map((r) => [r.component.key, r.context]), [["rear-bumper", true], ["front-wheel", true], ["rear-wheel", true]]);
});
check("filter: core only -- no fittings", () => {
  const groups = L.groupLibrary(withSeeds, { ...L.NO_FILTERS, status: "core" });
  assert.ok(groups.every((g) => g.key !== "unplaced" && g.assemblies.every((a) => a.rows.every((r) => !r.fittings.length))));
});
check("filter: body (the rear wiper motor is on tail-gate cars only)", () => {
  const keys = (body) =>
    L.groupLibrary(lib, { ...L.NO_FILTERS, body }).flatMap((g) => g.assemblies.flatMap((a) => a.rows.map((r) => r.component.key)));
  assert.ok(keys("hatch").includes("rear-wiper-motor"));
  assert.ok(!keys("sedan").includes("rear-wiper-motor"));
});
check("prints as, per body", () => {
  const boot = lib.components.find((c) => c.key === "boot-lid");
  assert.equal(L.printsAs(boot, "sedan"), "Boot lid assy");
  assert.equal(L.printsAs(boot, "hatch"), "Tail gate assy");
  const bumper = lib.components.find((c) => c.key === "rear-bumper");
  assert.equal(L.printsAs(bumper, "hatch"), "Rear bumper fascia");
});
check("panel uses for the diagram (staff only)", () => {
  assert.equal(L.panelUses(lib), null);
  const uses = L.panelUses(withSeeds);
  assert.equal(uses.rear_bumper, 4);
  assert.equal(uses.l_taillamp, 2);
  assert.equal(uses.r_taillamp, 2);
});
check("findEntry: a component, a fitting, a twin", () => {
  assert.equal(L.findEntry(withSeeds, "tail-lamp").type, "component");
  assert.equal(L.findEntry(withSeeds, "seed-sport-rim").type, "fitting");
  assert.equal(L.findEntry(withSeeds, "door-side-undetermined").type, "twin");
  assert.equal(L.findEntry(withSeeds, "nope"), null);
});
check("search entries follow the filters", () => {
  const only = L.searchEntries(withSeeds, { ...L.NO_FILTERS, status: "seed" });
  assert.deepEqual(only.map((e) => e.name).sort(), ["Body panel joint sealer", "Rear bumper clips", "Sport rim"]);
  assert.equal(S.searchParts(L.searchEntries(withSeeds), "rear bumper clip")[0].entry.name, "Rear bumper clips");
});

if (failed) {
  console.log(`\n${failed} check(s) failed`);
  process.exit(1);
}
console.log("\nall parts library checks passed");
