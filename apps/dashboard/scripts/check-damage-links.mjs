#!/usr/bin/env node
/**
 * Checks the pure parts of the 3D damage map (contract9.md section 9 B):
 * lib/vehicleZones.ts (part -> zone, areas), lib/damageLinks.ts (findings
 * <-> zones <-> photos, the strip, the selection) and the click rules in
 * lib/vehicleMeshRules.ts, against scripts/fixtures/evidence-sample.json
 * (synthetic) and a few made-up parts.
 *
 *   node apps/dashboard/scripts/check-damage-links.mjs
 *
 * Prints one line per check and exits 1 if any fails. Needs Node with
 * built-in type stripping (22.18+ / 24) to import the .ts files.
 */
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

// apps/dashboard/package.json has no "type" field (Next.js owns it), so Node
// warns MODULE_TYPELESS_PACKAGE_JSON when it loads the ESM .ts files.
// Swallow just that warning; everything else still reaches stderr.
process.removeAllListeners("warning");
process.on("warning", (w) => {
  if (w.code !== "MODULE_TYPELESS_PACKAGE_JSON") console.error(w.stack ?? w.message);
});
// The lib files import each other the bundler's way ("./vehicleZones", no
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

const zones = await import("../lib/vehicleZones.ts");
const links = await import("../lib/damageLinks.ts");
const rules = await import("../lib/vehicleMeshRules.ts");
const { buildFindings } = await import("../lib/findings.ts");

const fixture = JSON.parse(readFileSync(new URL("./fixtures/evidence-sample.json", import.meta.url), "utf8"));

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

const keysOf = (part, hint) => zones.zoneKeysFor(part, hint)?.keys ?? null;

// ---------------------------------------------------------------------------
console.log("-- part -> zone");

check("the rear quarter has its own zone (it used to light the sedan's FRONT fender)", () => {
  assert.deepEqual(keysOf("Left Rear Quarter Panel"), ["l_quarter"]);
  assert.deepEqual(keysOf("Right Rear Quarter Panel"), ["r_quarter"]);
  assert.deepEqual(keysOf("Left Rear Wheel Arch Liner"), ["l_quarter"]);
  assert.deepEqual(keysOf("Right Rear Wheel Arch Trim"), ["r_quarter"]);
  assert.deepEqual(keysOf("Left Front Fender"), ["l_fender"]);
  assert.deepEqual(keysOf("Left Front Wheel Arch Liner"), ["l_fender"]);
});

check("free text: the rear fender / arch is the quarter, the front one the fender", () => {
  assert.deepEqual(keysOf("Rear fender wheel arch garnish LH"), ["l_quarter"]);
  assert.deepEqual(keysOf("Quarter panel RH"), ["r_quarter"]);
  assert.deepEqual(keysOf("Front fender wheel arch protector RH"), ["r_fender"]);
  assert.deepEqual(keysOf("Tail lamp LH"), ["l_taillamp"]);
  assert.deepEqual(keysOf("Rear door window glass LH"), ["l_door_glass_rear"]);
});

check("an unknown side is never put on the left: both sides, marked '?'", () => {
  assert.deepEqual(zones.zoneKeysFor("Tail Lamp (side undetermined)"), { keys: ["l_taillamp", "r_taillamp"], sideUnknown: true });
  assert.deepEqual(zones.zoneKeysFor("Door (side undetermined)").keys, ["l_door_front", "r_door_front", "l_door_rear", "r_door_rear"]);
  assert.deepEqual(keysOf("Quarter Panel (side undetermined)"), ["l_quarter", "r_quarter"]);
  assert.deepEqual(keysOf("Wheel Arch Liner (side undetermined)"), ["l_fender", "r_fender", "l_quarter", "r_quarter"]);
  // free text with no side and no hint
  assert.deepEqual(zones.zoneKeysFor("Fender"), { keys: ["l_fender", "r_fender"], sideUnknown: true });
  assert.deepEqual(keysOf("Front door"), ["l_door_front", "r_door_front"]);
  // the one-zone name a single-zone caller sees names both, so it matches no mesh
  assert.equal(zones.zoneKeyFor("Headlamp (side undetermined)"), "l_headlamp / r_headlamp");
  // ...but the report's own dominant side still decides a plain "Fender" (resolveZones' rule)
  assert.deepEqual(keysOf("Fender", "r"), ["r_fender"]);
});

check("resolveZones: per-row badge numbers, both sides for an unknown side", () => {
  const res = zones.resolveZones([{ part: "Rear Bumper" }, { part: "Door (side undetermined)" }, { part: "Seat" }]);
  assert.equal(res[0].key, "rear_bumper");
  assert.equal(res[0].badgeNumber, 1);
  assert.equal(res[1].sideUnknown, true);
  assert.equal(res[1].keys.length, 4);
  assert.equal(res[1].badgeNumber, 2);
  assert.equal(res[2], null);
});

check("no-mesh parts stay off the model; stand-ins say where they really are", () => {
  assert.equal(zones.zoneKeysFor("Seat"), null);
  assert.equal(zones.zoneKeysFor("Other / Not Listed"), null);
  assert.equal(zones.isKnownUnmappedPart("Left Boot Side Trim"), true);
  assert.deepEqual(keysOf("Left Front Window Regulator"), ["l_door_front"]);
  assert.equal(zones.standInFor("Left Front Window Regulator"), "inside the door");
  assert.equal(zones.standInFor("Left Front Door"), null);
});

check("every zone a part can reach has a label, an anchor and an area", () => {
  for (const key of Object.keys(rules.ZONE_ANCHORS)) {
    assert.ok(zones.ZONE_LABELS[key], `no label for ${key}`);
    assert.notEqual(zones.zoneArea(key), "none", `no area for ${key}`);
  }
  for (const key of Object.keys(zones.ZONE_LABELS)) assert.ok(rules.ZONE_ANCHORS[key], `no anchor for ${key}`);
  // every zone a canonical part can name is a known zone
  for (const part of ["Left Rear Quarter Panel", "Door (side undetermined)", "Wheel Arch Liner (side undetermined)", "Boot Floor Board"]) {
    for (const key of keysOf(part)) assert.ok(zones.ZONE_LABELS[key], `${part} -> unknown zone ${key}`);
  }
});

check("areas: lamps and bumpers by their end, side panels by their side, LH = left", () => {
  assert.equal(zones.zoneArea("l_headlamp"), "front");
  assert.equal(zones.zoneArea("r_taillamp"), "rear");
  assert.equal(zones.zoneArea("l_quarter"), "lh");
  assert.equal(zones.zoneArea("r_door_front"), "rh");
  assert.equal(zones.zoneArea("roof"), "top");
  assert.deepEqual(zones.photoAreas("rear-left"), ["rear", "lh"]);
  assert.deepEqual(zones.photoAreas("interior"), []);
  assert.deepEqual(zones.photoAreas(null), []);
});

check("body type: stated first, then make/model, else sedan", () => {
  assert.equal(zones.detectBodyType("Honda Vezel", null), "suv");
  assert.equal(zones.detectBodyType("Honda Vezel", "MPV"), "mpv");
  assert.equal(zones.detectBodyType("Toyota Hiace", ""), "van");
  assert.equal(zones.detectBodyType("", "Wagon"), "sedan");
});

// ---------------------------------------------------------------------------
console.log("-- the fixture's findings on the map");

const photos = fixture.photos;
const lookup = (ref) => photos.find((p) => p.ref === ref || p.label === links.normalLabel(ref)) ?? null;
const canon = (ref) => lookup(ref)?.ref ?? ref;
const order = photos.map((p) => p.ref);
const areaOf = Object.fromEntries(photos.map((p) => [p.ref, p.area ?? null]));
const all = buildFindings(fixture.data, { showDismissed: true });
const L = links.buildLinks(all, canon);
const name = (key) => all.find((f) => f.key === key)?.name ?? key;
const sug = (part) => all.find((f) => f.kind === "suggestion" && f.part === part).key;

check("places: zones, areas, row numbers, 'Not on model', stand-ins", () => {
  assert.deepEqual(L.places.get("item:4"), { key: "item:4", zones: ["l_quarter"], sideUnknown: false, standIn: null, areas: ["lh"], number: 5 });
  assert.deepEqual(L.places.get("item:5").zones, []);
  assert.deepEqual(L.places.get("item:5").areas, ["none"]);
  assert.equal(L.places.get("item:3").standIn, "behind the bumper");
  assert.deepEqual(L.places.get(sug("Right Tail Lamp")).zones, ["r_taillamp"]);
  assert.equal(L.places.get(sug("Right Tail Lamp")).number, null);
  assert.equal(L.places.get(sug("Left Rear Wheel Arch Liner")).standIn, "inside the wheel arch");
});

check("photos of a finding: evidence first, then seen_in; old items from photo_reference", () => {
  assert.deepEqual(L.photosOf.get("item:0"), ["@ph03", "@ph04"]);
  assert.deepEqual(L.photosOf.get("item:3"), ["@ph07"]);
  assert.deepEqual(L.photosOf.get(sug("Left Rear Wheel Arch Liner")), ["@ph08", "@ph12"]);
  // a kit suggestion with no evidence still has its seen_in photo
  assert.deepEqual(L.photosOf.get(sug("Boot Lid Hinge")), ["@ph05"]);
});

check("marks on a photo: one per finding, the box and its kind and frame", () => {
  const p05 = L.marksOn.get("@ph05");
  assert.deepEqual(
    p05.map((m) => [m.key, m.kind]),
    [
      ["item:1", "sort"],
      [sug("Boot Lid Lock"), "ai"],
      [sug("Boot Lid Hinge"), null],
    ],
  );
  assert.deepEqual(L.marksOn.get("@ph09")[0].frame, { w: 1200, h: 1600 });
  assert.equal(L.marksOn.get("@ph04").find((m) => m.key === "item:0").box, null);
});

const marks = links.zoneMarks(all, L);
const mark = (zone) => marks.find((m) => m.zone === zone);

check("zone patterns: solid when every listed part is confirmed, light while the AI's, ! to check, stripes for suggestions only", () => {
  assert.equal(mark("l_quarter").lead, "confirmed");
  assert.equal(mark("l_quarter").listed, 1);
  assert.equal(mark("l_quarter").suggested, 1);
  assert.equal(mark("l_quarter").number, 5);
  assert.equal(mark("rear_bumper").lead, "ai");
  assert.equal(mark("rear_bumper").number, null);
  assert.equal(mark("l_taillamp").lead, "check");
  assert.equal(mark("r_taillamp").lead, "suggestion");
  assert.equal(mark("tailgate").dismissed, 1);
  assert.match(mark("tailgate").label, /^Boot Lid \/ Tailgate: 1 AI, not checked, 1 suggestion, 1 not damaged$/);
  assert.equal(marks.some((m) => m.zone === "l_fender"), false, "nothing on the front fender");
});

check("one marker per zone, never one per finding", () => {
  assert.equal(new Set(marks.map((m) => m.zone)).size, marks.length);
  assert.equal(mark("rear_bumper").keys.length, 3);
});

check("dismissed suggestions are left off when the page hides them", () => {
  const shown = buildFindings(fixture.data);
  const m = links.zoneMarks(shown, links.buildLinks(shown, canon));
  assert.equal(m.find((x) => x.zone === "tailgate").dismissed, 0);
});

check("a side-unknown part marks both sides with '?'", () => {
  const data = { ...fixture.data, damage_summary: [{ part: "Tail Lamp (side undetermined)", human_verified: false }], second_look: null };
  const f = buildFindings(data);
  const m = links.zoneMarks(f, links.buildLinks(f));
  assert.deepEqual(m.map((x) => [x.zone, x.sideUnknown]), [["l_taillamp", true], ["r_taillamp", true]]);
});

check("the marks' signature changes with what is drawn, not with the array", () => {
  const again = links.zoneMarks(buildFindings(fixture.data, { showDismissed: true }), links.buildLinks(buildFindings(fixture.data, { showDismissed: true }), canon));
  assert.equal(links.marksSignature(again), links.marksSignature(marks));
  const ticked = structuredClone(fixture.data);
  ticked.damage_summary[0].human_verified = true;
  const f2 = buildFindings(ticked, { showDismissed: true });
  assert.notEqual(links.marksSignature(links.zoneMarks(f2, links.buildLinks(f2, canon))), links.marksSignature(marks));
});

// ---------------------------------------------------------------------------
console.log("-- selection, strip and photo");

check("a selection lights its zones: a finding's, the zone, a photo's findings' (by @id or P-number)", () => {
  assert.deepEqual(links.selectionZones({ kind: "finding", key: "item:4" }, L), ["l_quarter"]);
  assert.deepEqual(links.selectionZones({ kind: "zone", zoneKey: "l_door_front" }, L), ["l_door_front"]);
  assert.deepEqual(links.selectionZones({ kind: "photo", ref: "@ph05" }, L, canon), ["tailgate"]);
  assert.deepEqual(links.selectionZones({ kind: "photo", ref: "P05" }, L, canon), ["tailgate"]);
  assert.deepEqual(links.selectionZones({ kind: "photo", ref: "p5" }, L, canon), ["tailgate"]);
  assert.deepEqual(links.selectionZones(null, L), []);
  assert.equal(links.selectionMovesCamera({ kind: "photo", ref: "P05" }), false);
  assert.equal(links.selectionMovesCamera({ kind: "finding", key: "item:0" }), true);
});

check("the strip: a finding's photos best first; a zone with none falls back to its side's", () => {
  const f = links.stripFilter({ kind: "finding", key: "item:0" }, null, L, order, areaOf, name);
  assert.deepEqual(f.refs, ["@ph03", "@ph04"]);
  assert.equal(f.title, "Rear Bumper");
  const z = links.stripFilter({ kind: "zone", zoneKey: "l_door_front" }, null, L, order, areaOf, name);
  assert.deepEqual(z.refs, []);
  assert.equal(z.title, "Left Front Door");
  assert.deepEqual(z.fallback, { area: "lh", refs: ["@ph06", "@ph08", "@ph09", "@ph10", "@ph12"] });
  const q = links.stripFilter({ kind: "zone", zoneKey: "l_quarter" }, null, L, order, areaOf, name);
  assert.deepEqual(q.refs, ["@ph08", "@ph09", "@ph10", "@ph12"]);
});

check("the strip by area chip; no filter shows every photo; a photo selection keeps them all", () => {
  assert.deepEqual(links.stripFilter(null, "rear", L, order, areaOf, name).refs, ["@ph02", "@ph03", "@ph04", "@ph05", "@ph06", "@ph07"]);
  assert.deepEqual(links.stripFilter(null, null, L, order, areaOf, name).refs, order);
  assert.deepEqual(links.stripFilter({ kind: "photo", ref: "@ph05" }, null, L, order, areaOf, name).refs, order);
  // "Not on model": the Other part's photo
  assert.deepEqual(links.stripFilter(null, "none", L, order, areaOf, name).refs, ["@ph11"]);
});

check("the photo shown: a finding's best, a photo, a zone's first; else the first boxed photo", () => {
  assert.equal(links.photoForSelection({ kind: "finding", key: "item:4" }, L, order), "@ph09");
  assert.equal(links.photoForSelection({ kind: "photo", ref: "P10" }, L, order, canon), "@ph10");
  assert.equal(links.photoForSelection({ kind: "zone", zoneKey: "tailgate" }, L, order), "@ph05");
  assert.equal(links.photoForSelection({ kind: "zone", zoneKey: "l_door_front" }, L, order), null);
  assert.equal(links.photoForSelection(null, L, order), "@ph03");
});

check("area chips count findings (a side-unknown one on both sides)", () => {
  const c = links.areaCounts(all, L);
  assert.equal(c.all, all.length);
  assert.equal(c.byArea.get("lh"), 2);
  assert.equal(c.byArea.get("none"), 1);
  assert.equal(c.byArea.get("rh") ?? 0, 0);
  assert.equal(c.byArea.get("rear"), 8);
});

check("selections compare by value; labels normalise", () => {
  assert.equal(links.sameSelection({ kind: "zone", zoneKey: "roof" }, { kind: "zone", zoneKey: "roof" }), true);
  assert.equal(links.sameSelection({ kind: "zone", zoneKey: "roof" }, { kind: "finding", key: "roof" }), false);
  assert.equal(links.sameSelection(null, null), true);
  assert.equal(links.normalLabel("p7"), "P07");
  assert.equal(links.labelIndex("P07"), 6);
  assert.equal(links.labelIndex("@ph07"), -1);
  assert.equal(links.indexLabel(6), "P07");
});

check("an undone add takes a row off: an item selection follows its row up; the rest stay as they are", () => {
  assert.deepEqual(links.afterItemsRemoved({ kind: "finding", key: "item:5" }, [4]), { kind: "finding", key: "item:4" });
  assert.equal(links.afterItemsRemoved({ kind: "finding", key: "item:4" }, [4]), null, "the row itself is gone");
  assert.deepEqual(links.afterItemsRemoved({ kind: "finding", key: "item:6" }, [2, 4]), { kind: "finding", key: "item:4" });
  // Untouched selections are the same object, so the map doesn't fly again.
  for (const sel of [{ kind: "finding", key: "item:3" }, { kind: "finding", key: "sug:Boot Lid Lock" }, { kind: "zone", zoneKey: "roof" }, { kind: "photo", ref: "P01" }]) {
    assert.equal(links.afterItemsRemoved(sel, [4]), sel);
  }
  assert.equal(links.afterItemsRemoved(null, [4]), null);
  const sel = { kind: "finding", key: "item:5" };
  assert.equal(links.afterItemsRemoved(sel, []), sel);
});

// ---------------------------------------------------------------------------
console.log("-- clicks on the model");

// A sedan-sized box, already normalised (4.8 m long, wheels on the ground).
const box = { min: [-0.9, 0, -2.4], max: [0.9, 1.45, 2.4] };

check("a roof click counts only in the top 15% of the car", () => {
  assert.equal(rules.roofClickAllowed(1.4, 0, 1.45), true);
  assert.equal(rules.roofClickAllowed(1.0, 0, 1.45), false);
});

check("a click on a mesh with no rule goes to the nearest no-mesh anchor within 0.6 m", () => {
  const quarter = rules.anchorPosition("l_quarter", box);
  assert.ok(quarter[0] > 0, "the left quarter is at +X");
  assert.equal(rules.nearestAnchorZone([quarter[0] + 0.1, quarter[1] - 0.1, quarter[2] + 0.2], box, ["l_quarter", "r_quarter"]), "l_quarter");
  assert.equal(rules.nearestAnchorZone([0, 0.7, 0], box, ["l_quarter", "r_quarter"]), null);
});

// ---------------------------------------------------------------------------
console.log("-- detection Round 2");

check("a located part box beside the read's box-less entry is the photo's mark (the second review's N2)", () => {
  const f = {
    key: "item:9", kind: "item", index: 9, partKey: "Boot Lid", part: "Boot Lid", name: "Boot Lid", status: "check",
    reason: null, seenIn: ["P05"], foundBy: ["overall"], confidence: "High", check: null, otherSideListed: null,
    source: "list", note: null, condition: null,
    evidence: [
      { photo: "P05", box: null, box_kind: null, by: "overall", reason: null, confidence: "High", img_w: null, img_h: null },
      { photo: "P05", box: [100, 150, 450, 850], box_kind: "part", by: "locate:zone:rear", reason: null, confidence: null,
        img_w: 1600, img_h: 1200 },
    ],
  };
  const L = links.buildLinks([f]);
  const marks = L.marksOn.get("P05");
  assert.equal(marks.length, 1, "one mark a photo");
  assert.deepEqual([marks[0].box, marks[0].kind], [[100, 150, 450, 850], "part"]);
  assert.deepEqual(L.photosOf.get("item:9"), ["P05"]);
  const plain = { ...f, evidence: [f.evidence[0]] };
  assert.deepEqual([links.buildLinks([plain]).marksOn.get("P05")[0].box], [null], "without it: no box, as before");
});

if (failed) {
  console.log(`\n${failed} check${failed === 1 ? "" : "s"} failed`);
  process.exit(1);
}
console.log("\nall checks passed");
