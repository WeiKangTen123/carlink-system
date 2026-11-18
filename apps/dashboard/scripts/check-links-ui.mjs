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
  assert.deepEqual(glass.intactIn, ["zone:left"], "a zone read's name, as links.intact_in writes it");
  assert.deepEqual(sug("Rear Floor Panel").intactIn, ["@ph09"], "an older draft's photo ref");
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

check("'confirm at strip-down' only when the parts must come off; hidden alone is just the chip", () => {
  assert.equal(F.confirmAtStripDown(sug("Rear Bumper Beam")), true);
  assert.equal(F.confirmAtStripDown(sug("Left Rear Door Glass")), false);
  // A hidden part checked without strip-down (open the lid): Hidden, not strip-down.
  const hinge = { ...sug("Rear Number Plate"), hidden: true, linkType: "opens_with", bucket: "likely" };
  assert.equal(F.confirmAtStripDown(hinge), false);
  assert.equal(say(F.hiddenNoPhotoText({ ...hinge, parents: [{ key: "Boot Lid", name: "Boot Lid" }] })), "Hidden under the Boot Lid – check it on the car");
  assert.equal(F.confirmAtStripDown({ ...hinge, needsStripDown: true }), true);
  assert.equal(F.confirmAtStripDown(sug("Boot Lid Lock")), false, "a zone suggestion never");
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

check("the linked check's words: 'AI check: seen — AI says: “...”' (the AI's own, as contract 9's)", () => {
  // Not in `texts`: these are the AI's words, and they say so.
  const line = F.linkCheckLine(sug("Rear Bumper Reflector (side undetermined)").linkCheck);
  assert.equal(line, "AI check: seen — AI says: “Reflector lens cracked at the bumper corner”");
  assert.match(line.split(" — ")[1], /^AI says: “/, "the reason part starts with 'AI says:'");
  assert.equal(F.linkCheckLine(null), null);
  const bare = { verdict: "likely", cue: "gap_misfit", model: null, reason: null };
  assert.equal(say(F.linkCheckLine(bare)), "AI check: likely", "no reason: the verdict alone, no empty quote");
  assert.equal(F.linkCheckWords({ verdict: "no", cue: "none_seen", model: null, reason: null }), "AI check: probably not");
  assert.equal(F.linkCheckWords({ verdict: "cannot_tell", cue: null, model: null, reason: null }), "AI check: can't tell");
  // The link table's reasons stay the code's, plain.
  for (const f of all.filter((x) => x.source === "link")) assert.doesNotMatch(F.codeReason(f), /AI says|AI check/, f.part);
});

check("M1: 'A zone read (left side) listed it as not damaged' from a read name; an old photo ref by its label", () => {
  const lookup = (ref) => fixture.photos.find((p) => p.ref === ref || p.label === ref) ?? null;
  assert.equal(
    say(F.intactText(sug("Left Rear Door Glass").intactIn, lookup)),
    "A zone read (left side) listed it as not damaged. Check it on the car.",
  );
  assert.equal(say(F.intactText(["zone:rear"], lookup)), "A zone read (rear) listed it as not damaged. Check it on the car.");
  assert.equal(
    say(F.intactText(["zone:rear", "zone:left", "zone:rear"], lookup)),
    "Zone reads (rear and left side) listed it as not damaged. Check it on the car.",
  );
  // Before the fix the note looked read names up as photos and never showed.
  assert.equal(lookup("zone:left"), null);
  assert.equal(
    say(F.intactText(sug("Rear Floor Panel").intactIn, lookup)),
    "A zone read listed it as not damaged in P09. Check it on the car.",
    "an older draft's '@<id>'",
  );
  assert.equal(F.intactText(["P09"], lookup), "A zone read listed it as not damaged in P09. Check it on the car.", "a filed case's P-number");
  assert.equal(F.intactText(["@gone"], lookup), null, "a deleted photo names nothing");
  assert.equal(F.intactText(["zone:rear"]), "A zone read (rear) listed it as not damaged. Check it on the car.", "no lookup needed for a read");
  assert.equal(F.intactText([], lookup), null);
  assert.equal(F.intactText(undefined, lookup), null);
  assert.equal(F.intactText(["", "  "], lookup), null);
});

check("L3: the linked check's outcome note (link_run), as the re-check's", () => {
  assert.equal(F.linkRunNote(data.second_look.link_run), null, "ran fine, nothing to say: no note");
  assert.equal(F.linkRunNote(null), null);
  assert.equal(F.linkRunNote(undefined), null);
  const run = (status, note) => ({ status, by: status === "ok" ? "lite" : null, model: null, note, seconds: 1.2, calls: 1 });
  assert.equal(say(F.linkRunNote(run("ok", "Checked 1 of 3 areas."))), "AI check: Checked 1 of 3 areas.");
  assert.equal(
    say(F.linkRunNote(run("failed", "The AI was busy; the linked parts are as the table placed them."))),
    "AI check didn't run. The AI was busy; the linked parts are as the table placed them.",
  );
  assert.equal(
    say(F.linkRunNote(run("skipped", "No photos of the linked parts' areas to check."))),
    "AI check didn't run. No photos of the linked parts' areas to check.",
  );
  assert.equal(say(F.linkRunNote(run("failed", null))), "AI check didn't run. The linked parts are as the link table placed them.");
  assert.equal(F.linkRunNote(run("ok", "  ")), null);
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

check("L2: an item added from a linked part isn't 'seen in' its listed part's photo", () => {
  const d = clone(data);
  const hinge = d.damage_summary.find((x) => x.part === "Boot Lid Hinge");
  hinge.seen_in = [];
  hinge.photo_reference = "@ph02";
  hinge.evidence = [{ photo: "@ph02", box: null, box_kind: null, by: "link", reason: null, confidence: null, img_w: null, img_h: null }];
  const f = F.buildFindings(d).find((x) => x.kind === "item" && x.part === "Boot Lid Hinge");
  assert.deepEqual(f.seenIn, []);
  assert.equal(F.seenCount(f), null);
  assert.equal(f.evidence[0].by, "link", "kept as evidence (the viewer captions it)");
  hinge.evidence.push({ photo: "@ph05", box: [1, 2, 3, 4], box_kind: "ai", by: "link_check", reason: null, confidence: null, img_w: null, img_h: null });
  assert.deepEqual(F.buildFindings(d).find((x) => x.kind === "item" && x.part === "Boot Lid Hinge").seenIn, ["@ph05"]);
});

check("L7: an autosave's older second_look never rolls back a newer decision", () => {
  const look = data.second_look;
  const decision = (key, action, at) => ({ key, part: key, action, by_id: "u1", by_name: "A", at, looked: true, where: "intake" });
  const newer = { ...look, decisions: [...look.decisions, decision("Rear Floor Panel", "dismissed", "2026-11-17T09:00:05+00:00")] };
  assert.equal(F.isOlderLook(look, newer), true, "saved before the decision: fewer decisions, kept out");
  assert.equal(F.isOlderLook(newer, look), false, "a newer one is taken");
  const relinked = { ...newer, items: [...newer.items, { ...newer.items[0], key: "X", part: "X" }] };
  assert.equal(F.isOlderLook(relinked, newer), false, "saved after the decision (same decisions, new links): taken");
  assert.equal(F.isOlderLook(null, look), true);
  assert.equal(F.isOlderLook(look, null), false);
  assert.equal(F.isOlderLook({ ...look, decisions: undefined }, { ...look, decisions: [] }), false, "an old shape with no decisions");
  // At the server's cap (200, the oldest dropped) the counts tie: line up on our latest decision.
  const stamp = (i) => `2026-11-17T08:${String(Math.floor(i / 60)).padStart(2, "0")}:${String(i % 60).padStart(2, "0")}+00:00`;
  const many = Array.from({ length: 200 }, (_, i) => decision(`K${i}`, "dismissed", stamp(i)));
  const mine = { ...look, decisions: [...many.slice(1), decision("Z", "added", "2026-11-17T09:30:00+00:00")] };
  assert.equal(F.isOlderLook({ ...look, decisions: many }, mine), true, "capped, without our latest: older");
  assert.equal(F.isOlderLook({ ...look, decisions: mine.decisions }, mine), false, "capped, with it: taken");
});

check("L7: a save's answer -- 'linked' (new api) is added to ours; a whole second_look (old api) only when not older", () => {
  const look = data.second_look;
  const decision = { key: "Rear Floor Panel", part: "Rear Floor Panel", action: "dismissed", by_id: "u1", by_name: "A", at: "2026-11-17T09:00:05+00:00", looked: true, where: "intake" };
  // Ours: a dismissal answered after the save started.
  const ours = { ...look, dismissed: [...look.dismissed, "Rear Floor Panel"], decisions: [...look.decisions, decision] };
  const liner = { ...clone(look.items[1]), key: "Left Rear Wheel Arch Liner", part: "Left Rear Wheel Arch Liner", covers: ["Left Rear Wheel Arch Liner"] };
  // The save's look: stored before that dismissal, with the new linked part.
  const saved = { ...look, items: [...look.items, liner] };

  const merged = F.withSavedLook(ours, { second_look: saved, linked: [liner] });
  assert.deepEqual(merged.items.map((i) => i.key), [...ours.items.map((i) => i.key), "Left Rear Wheel Arch Liner"], "the new one on the end");
  assert.equal(merged.decisions, ours.decisions, "our decisions stay");
  assert.ok(merged.dismissed.includes("Rear Floor Panel"), "the dismissed card stays dismissed");
  const card = (l) => F.buildFindings({ ...data, second_look: l }).find((f) => f.part === "Rear Floor Panel");
  assert.equal(card(merged), undefined, "not back among the open suggestions");
  assert.equal(F.withSavedLook(merged, { second_look: saved, linked: [liner] }), merged, "twice: once only (ours returned as is)");
  assert.equal(F.withSavedLook(ours, { linked: [] }), ours, "nothing new: ours as is");

  // An older api: the whole second_look, refused while it is older than ours...
  assert.equal(F.withSavedLook(ours, { second_look: saved }), ours);
  assert.ok(card(saved), "(which would have put the dismissed card back)");
  // ...and taken when it isn't (saved after the decision).
  const later = { ...ours, items: [...ours.items, liner] };
  assert.equal(F.withSavedLook(ours, { second_look: later }), later);
  assert.equal(F.withSavedLook(null, { second_look: later }), later, "no look of ours yet");
  assert.equal(F.withSavedLook(null, { second_look: later, linked: [liner] }), later, "no look of ours yet (new api)");
  assert.equal(F.withSavedLook(ours, {}), ours);
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
