#!/usr/bin/env node
/**
 * Checks the contract 10 linked-parts UI helpers -- lib/findings.ts (the
 * linked fields on a Finding, the suggestions panel's sections and groups,
 * the words), lib/damageLinks.ts (a listed part -> its linked parts on the
 * 3D map) and lib/vehicleZones.ts (stand-ins) -- against
 * scripts/fixtures/links-sample.json, the synthetic Step 0 fixture.
 *
 *   node apps/dashboard/scripts/check-links-ui.mjs
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
process.removeAllListeners("warning");
process.on("warning", (w) => {
  if (w.code !== "MODULE_TYPELESS_PACKAGE_JSON") console.error(w.stack ?? w.message);
});
// The lib files import each other the bundler's way (no extension).
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
const E = await import("../lib/evidence.ts");
const links = await import("../lib/damageLinks.ts");
const zones = await import("../lib/vehicleZones.ts");

const fixture = JSON.parse(readFileSync(new URL("./fixtures/links-sample.json", import.meta.url), "utf8"));
const evidenceFixture = JSON.parse(readFileSync(new URL("./fixtures/evidence-sample.json", import.meta.url), "utf8"));
const data = fixture.data;
const clone = (x) => JSON.parse(JSON.stringify(x));

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

const all = F.buildFindings(data, { showDismissed: true });
const open = (d = data) => F.buildFindings(d).filter((f) => f.kind === "suggestion" && f.status === "suggestion");
const byPart = (list, part) => list.find((f) => f.part === part);
const sug = (part) => byPart(all.filter((f) => f.kind === "suggestion"), part);
const parts = (list) => list.map((f) => f.part);
// Every string the UI builds for a finding, for the words checks.
const texts = [];
const say = (t) => {
  if (t) texts.push(t);
  return t;
};

// ---------------------------------------------------------------------------
console.log("-- linked parts as findings");

check("link fields copied only when the server sent them; a zone suggestion has none", () => {
  const beam = sug("Rear Bumper Beam");
  assert.equal(beam.source, "link");
  assert.deepEqual(beam.linkedFrom, ["Rear Bumper"]);
  assert.equal(beam.linkType, "behind");
  assert.equal(beam.bucket, "strip_down");
  assert.equal(beam.hit, "crush");
  assert.equal(beam.hidden, true);
  assert.equal(beam.needsStripDown, true);
  assert.deepEqual(beam.oem, ["REAR BUMPER", "REAR BUMPER REINFORCEMENT"]);
  const glass = sug("Left Rear Door Glass");
  assert.equal(glass.sideFrom, "Left Rear Door");
  assert.deepEqual(glass.intactIn, ["@ph09"]);
  assert.equal("hidden" in glass, false, "hidden=false is left out, as the server does");
  const lock = sug("Boot Lid Lock");
  for (const k of ["linkedFrom", "linkType", "bucket", "hidden", "linkReason", "parents"]) assert.equal(k in lock, false, k);
});

check("each linked part names its listed part as the list does (name, condition, row)", () => {
  assert.deepEqual(sug("Rear Bumper Beam").parents, [{ key: "Rear Bumper", name: "Rear Bumper", condition: "Cracked/deformed", findingKey: "item:0" }]);
  assert.deepEqual(sug("Left Rear Quarter Panel").parents, [{ key: "Left Rear Door", name: "Left Rear Door", condition: "Dented", findingKey: "item:3" }]);
  const hinge = all.find((f) => f.kind === "item" && f.part === "Boot Lid Hinge");
  assert.deepEqual(hinge.parents, [{ key: "Boot Lid", name: "Boot Lid", condition: "Dented", findingKey: "item:1" }]);
});

check("a linked part's reason is the code's (the link table's), never 'AI says'", () => {
  for (const f of all.filter((x) => x.source === "link")) {
    assert.equal(f.reason, null, f.part);
    assert.ok(f.linkReason, f.part);
    assert.equal(say(F.codeReason(f)), f.linkReason);
    assert.doesNotMatch(F.codeReason(f), /AI says/);
  }
  const hinge = all.find((f) => f.kind === "item" && f.part === "Boot Lid Hinge");
  assert.equal(hinge.reason, null, "an item added from a linked part keeps the code's words");
  assert.match(say(F.codeReason(hinge)), /^Opens with the boot lid/);
  // The zone suggestion keeps the AI's own words.
  assert.equal(sug("Boot Lid Lock").reason, "Lock striker bent out of line");
});

check("the listed part's photo is not a photo a linked part was seen in", () => {
  assert.deepEqual(sug("Rear Bumper Beam").seenIn, []);
  assert.deepEqual(sug("Rear Floor Panel").seenIn, []);
  assert.deepEqual(sug("Rear Bumper Reflector (side undetermined)").seenIn, ["@ph04"]);
  assert.equal(F.seenCount(sug("Rear Bumper Beam")), null);
});

// ---------------------------------------------------------------------------
console.log("-- the suggestions panel");

const sections = F.suggestionSections(open());

check("zone suggestions under 'Seen in photos'; linked parts grouped by listed part", () => {
  assert.equal(sections.hasLinks, true);
  assert.deepEqual(parts(sections.seen), ["Boot Lid Lock"]);
  assert.deepEqual(sections.other, []);
  assert.deepEqual(
    sections.groups.map((g) => g.key),
    ["Rear Bumper", "Left Rear Door"],
  );
  assert.equal(say(F.linkGroupTitle(sections.groups[0])), "Linked to: Rear Bumper (Cracked/deformed · crushed)");
  assert.equal(say(F.linkGroupTitle(sections.groups[1])), "Linked to: Left Rear Door (Dented · medium hit)");
});

check("buckets inside a group: seen, likely, strip-down; all three on the fixture", () => {
  const [bumper, door] = sections.groups;
  assert.deepEqual(parts(bumper.rows), ["Rear Bumper Reflector (side undetermined)", "Rear Bumper Beam"]);
  assert.deepEqual(bumper.rows.map(F.linkBucket), ["seen", "strip_down"]);
  assert.deepEqual(parts(door.rows), ["Left Rear Quarter Panel", "Left Rear Door Glass"]);
  assert.deepEqual(door.rows.map(F.linkBucket), ["seen", "likely"]);
  const buckets = new Set(sections.groups.flatMap((g) => [...g.rows, ...g.crush]).map(F.linkBucket));
  assert.deepEqual([...buckets].sort(), ["likely", "seen", "strip_down"]);
  assert.deepEqual(F.BUCKET_LABELS, { seen: "Seen", likely: "Likely", strip_down: "Strip-down" });
});

check("the crush path is collapsed: its rows are apart from the shown ones", () => {
  const [bumper, door] = sections.groups;
  assert.deepEqual(parts(bumper.crush), ["Rear Floor Panel"]);
  assert.equal(bumper.rows.some((f) => f.linkType === "crush"), false);
  assert.deepEqual(door.crush, []);
});

check("the hidden part: Hidden + 'possible – confirm at strip-down'", () => {
  const beam = sections.groups[0].rows.find((f) => f.part === "Rear Bumper Beam");
  assert.equal(beam.hidden, true);
  assert.equal(F.STRIP_DOWN_TEXT, "possible – confirm at strip-down");
  assert.equal(sections.groups.flatMap((g) => [...g.rows, ...g.crush]).filter((f) => f.hidden).length, 1, "only the beam is hidden");
});

check("nothing is pre-ticked: every linked part waits for a person", () => {
  const states = F.decisionStates(data.second_look.decisions);
  const listed = new Set(data.damage_summary.map((d) => d.part));
  for (const f of open().filter((x) => x.source === "link")) {
    assert.equal(f.status, "suggestion", f.part);
    assert.equal(states.has(f.partKey), false, `${f.part} has no decision`);
    assert.equal(listed.has(f.part), false, `${f.part} is not on the list`);
  }
  // The one added from a link was added by a person (a decision), and the
  // dismissed one stays out of the open list.
  assert.equal(states.get("Boot Lid Hinge").action, "added");
  assert.equal(byPart(open(), "Rear Number Plate"), undefined);
  assert.equal(sug("Rear Number Plate").status, "dismissed");
});

check("'same side as <listed part>' when the side came from it; none for an undetermined side", () => {
  assert.equal(say(F.sideFromText(sug("Left Rear Quarter Panel"))), "same side as Left Rear Door");
  assert.equal(F.sideFromText(sug("Left Rear Door Glass")), "same side as Left Rear Door");
  assert.equal(F.sideFromText(sug("Rear Bumper Reflector (side undetermined)")), null);
});

check("the linked check's words: 'AI check: seen — “...”' (the AI's own, quoted)", () => {
  const line = say(F.linkCheckLine(sug("Rear Bumper Reflector (side undetermined)").linkCheck));
  assert.equal(line, "AI check: seen — “Reflector lens cracked at the bumper corner”");
  assert.equal(F.linkCheckLine(null), null);
  assert.equal(F.linkCheckWords({ verdict: "no", cue: "none_seen", model: null, reason: null }), "AI check: probably not");
  assert.equal(F.linkCheckWords({ verdict: "cannot_tell", cue: null, model: null, reason: null }), "AI check: can't tell");
});

check("'no' (linked check or re-check) goes last in its group, still there to add", () => {
  const d = clone(data);
  const items = d.second_look.items;
  items.find((i) => i.part === "Rear Bumper Reflector (side undetermined)").link_check.verdict = "no";
  items.find((i) => i.part === "Left Rear Door Glass").check = { verdict: "no", p: 0.1, by: "decision", model: null, reason: null };
  const s = F.suggestionSections(open(d));
  const [bumper, door] = s.groups;
  assert.deepEqual(parts(bumper.probablyNot), ["Rear Bumper Reflector (side undetermined)"]);
  assert.deepEqual(parts(bumper.rows), ["Rear Bumper Beam"]);
  assert.deepEqual(parts(door.probablyNot), ["Left Rear Door Glass"]);
  const order = parts(F.panelOrder(open(d)));
  assert.deepEqual(order, [
    "Boot Lid Lock",
    "Rear Bumper Beam",
    "Rear Floor Panel",
    "Rear Bumper Reflector (side undetermined)",
    "Left Rear Quarter Panel",
    "Left Rear Door Glass",
  ]);
});

check("the viewer walks the panel's order (A / N move to the card below)", () => {
  assert.deepEqual(parts(F.panelOrder(open())), [
    "Boot Lid Lock",
    "Rear Bumper Reflector (side undetermined)",
    "Rear Bumper Beam",
    "Rear Floor Panel",
    "Left Rear Quarter Panel",
    "Left Rear Door Glass",
  ]);
});

check("at most 4 per listed part before 'Show more'; order seen > likely > strip-down, then the kind of link", () => {
  assert.equal(F.LINKS_PER_PARENT, 4);
  const mk = (part, bucket, linkType, extra = {}) => ({
    key: `sug:${part}`, kind: "suggestion", index: null, partKey: part, part, name: part, status: "suggestion", reason: null,
    evidence: [], seenIn: [], foundBy: [], confidence: null, check: null, otherSideListed: null, source: "link",
    linkedFrom: ["Front Bumper"], bucket, linkType, ...extra,
  });
  const list = [
    mk("A", "strip_down", "behind"),
    mk("B", "likely", "attached"),
    mk("C", "likely", "opens_with"),
    mk("D", "seen", "attached"),
    mk("E", "likely", "attached"),
    mk("F", "strip_down", "behind"),
    mk("G", "likely", "crush"),
  ];
  const [g] = F.suggestionSections(list).groups;
  assert.deepEqual(parts(g.rows), ["D", "B", "E", "C", "A", "F"]);
  assert.equal(g.rows.length > F.LINKS_PER_PARENT, true, "the panel shows 4 and 'Show 2 more'");
  assert.deepEqual(parts(g.crush), ["G"]);
});

check("without linked parts the panel and the viewer's order are as before (contract 9 fixture)", () => {
  const before = F.buildFindings(evidenceFixture.data).filter((f) => f.kind === "suggestion" && f.status === "suggestion");
  const s = F.suggestionSections(before);
  assert.equal(s.hasLinks, false);
  assert.deepEqual(s.groups, []);
  assert.deepEqual(parts(s.seen), parts(F.sortByCheck(before)));
  assert.deepEqual(parts(F.panelOrder(before)), parts(F.sortByCheck(before)));
});

// ---------------------------------------------------------------------------
console.log("-- the evidence viewer");

check("no linked-check photo: the listed part's photo, captioned, with no box", () => {
  const beam = sug("Rear Bumper Beam");
  const slides = E.slidesOf(beam);
  assert.equal(slides.length, 1);
  assert.equal(slides[0].by, "link");
  assert.equal(slides[0].box, null);
  assert.equal(say(F.linkPhotoCaption(beam)), "Photo of the Rear Bumper (this part sits behind it)");
  assert.equal(F.linkPhotoCaption(sug("Left Rear Door Glass")), "Photo of the Left Rear Door (this part sits on it)");
  assert.equal(say(F.hiddenNoPhotoText(beam)), "Hidden behind the Rear Bumper – confirm at strip-down");
  assert.equal(F.sourceName("link"), "listed part's photo");
});

check("with a linked-check photo: its box, labelled 'AI box', first", () => {
  const slides = E.slidesOf(sug("Rear Bumper Reflector (side undetermined)"));
  assert.equal(slides[0].by, "link_check");
  assert.equal(E.boxChip(slides[0]), "AI box");
  assert.deepEqual(slides[0].box, [610, 120, 700, 260]);
  assert.equal(slides[1].by, "link");
  assert.equal(F.sourceName("link_check"), "linked check");
});

// ---------------------------------------------------------------------------
console.log("-- the strip-down chip (DetectedPartsTable)");

check("'Confirm at strip-down' until a person gives the part its own condition", () => {
  const hinge = data.damage_summary.find((d) => d.part === "Boot Lid Hinge");
  assert.equal(F.needsStripDownChip(hinge), true);
  assert.equal(F.needsStripDownChip({ ...hinge, condition: "Subject to strip-down" }), true);
  assert.equal(F.needsStripDownChip({ ...hinge, condition: "Bent" }), false);
  assert.equal(F.needsStripDownChip(data.damage_summary[0]), false);
  assert.equal(F.STRIP_DOWN_CHIP, "Confirm at strip-down");
});

// ---------------------------------------------------------------------------
console.log("-- the 3D map");

const photos = fixture.photos;
const canon = (ref) => photos.find((p) => p.ref === ref || p.label === ref)?.ref ?? ref;
const L = links.buildLinks(all, canon);
const shownOpen = F.buildFindings(data);
const shownKeys = new Set(shownOpen.map((f) => f.key));

check("a listed part -> its linked parts, and back", () => {
  assert.deepEqual(L.childrenOf.get("item:0").map((k) => all.find((f) => f.key === k).part), [
    "Rear Bumper Reflector (side undetermined)",
    "Rear Floor Panel",
    "Rear Bumper Beam",
    "Rear Number Plate",
  ]);
  assert.deepEqual(L.childrenOf.get("item:3"), ["sug:Left Rear Quarter Panel", "sug:Left Rear Door Glass"]);
  assert.deepEqual(L.childrenOf.get("item:1"), ["item:4"], "the item added from a link hangs off the Boot Lid");
  assert.deepEqual(L.parentsOf.get("sug:Left Rear Quarter Panel"), ["item:3"]);
});

check("selecting a listed part outlines its linked parts' zones; the camera stays on the part", () => {
  const sel = { kind: "finding", key: "item:0" };
  assert.deepEqual(links.selectionZones(sel, L), ["rear_bumper"], "the camera's zones");
  assert.equal(links.selectionMovesCamera({ kind: "finding", key: "item:0" }), true);
  assert.deepEqual(links.linkedZones(sel, L, shownKeys), ["underbody"], "the floor; the beam and reflector share the bumper's zone");
  assert.deepEqual(links.linkedZones({ kind: "finding", key: "item:3" }, L, shownKeys), ["l_quarter", "l_door_glass_rear"]);
  assert.deepEqual(links.linkedZones({ kind: "finding", key: "sug:Left Rear Door Glass" }, L, shownKeys), ["l_door_rear"], "a linked part outlines its listed part");
  assert.deepEqual(links.linkedZones({ kind: "zone", zoneKey: "rear_bumper" }, L, shownKeys), [], "only a finding selection");
  assert.deepEqual(links.linkedZones(null, L), []);
});

check("hidden parts at their stand-in places; the zone's label says so", () => {
  const beam = L.places.get("sug:Rear Bumper Beam");
  assert.deepEqual(beam.zones, ["rear_bumper"]);
  assert.equal(beam.standIn, "behind the bumper");
  const marks = links.zoneMarks(shownOpen, links.buildLinks(shownOpen, canon));
  const bumper = marks.find((m) => m.zone === "rear_bumper");
  assert.equal(bumper.hidden, 1);
  assert.match(bumper.label, new RegExp(`; 1 hidden part ${links.HIDDEN_LABEL}$`));
  assert.equal(marks.find((m) => m.zone === "l_quarter").hidden, 0);
  assert.notEqual(links.marksSignature(marks), links.marksSignature(marks.map((m) => ({ ...m, hidden: 0 }))), "a new hidden part repaints");
  for (const part of ["Boot Lid Lock", "Boot Lid Hinge", "Boot Lid Weatherstrip", "Boot Lid Inner Trim"]) assert.ok(zones.standInFor(part), part);
});

check("a linked part isn't marked on its listed part's photo, but the photo is among its photos", () => {
  assert.deepEqual(L.photosOf.get("sug:Rear Bumper Beam"), ["@ph03"]);
  assert.equal((L.marksOn.get("@ph03") ?? []).some((m) => m.key === "sug:Rear Bumper Beam"), false);
  assert.equal((L.marksOn.get("@ph04") ?? []).some((m) => m.key === "sug:Rear Bumper Reflector (side undetermined)"), true, "the linked check's photo");
});

check("linked parts with no place on the model stay under 'Not on model'", () => {
  const d = clone(data);
  for (const part of ["Exhaust System", "Suspension - Rear", "Steering Assembly"]) {
    d.second_look.items.push({ ...clone(d.second_look.items[4]), key: part, part, name: part, covers: [part] });
  }
  const list = F.buildFindings(d);
  const L2 = links.buildLinks(list, canon);
  for (const part of ["Exhaust System", "Suspension - Rear", "Steering Assembly"]) {
    const place = L2.places.get(`sug:${part}`);
    assert.deepEqual(place.zones, [], part);
    assert.deepEqual(place.areas, ["none"], part);
    assert.ok(L2.byArea.get("none").includes(`sug:${part}`), part);
  }
  assert.ok(L2.childrenOf.get("item:0").includes("sug:Exhaust System"), "still linked to the bumper");
});

// ---------------------------------------------------------------------------
console.log("-- words");

check("no percentage and no 'AI says' in the code's words", () => {
  assert.ok(texts.length > 10);
  for (const t of texts) {
    assert.doesNotMatch(t, /%/, t);
    assert.doesNotMatch(t, /AI says/, t);
  }
  for (const h of Object.values(F.HIT_WORDS)) assert.doesNotMatch(h, /\d/, h);
});

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
process.exit(failed ? 1 : 0);
