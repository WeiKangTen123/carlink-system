#!/usr/bin/env node
/**
 * Checks the pure helpers of the contract 9 evidence UI -- lib/findings.ts
 * (every list item and suggestion as a Finding, its evidence, the words,
 * the decisions) and lib/evidence.ts (which box a photo shows, the 2% frame
 * check, the zoom) -- against scripts/fixtures/evidence-sample.json, the
 * synthetic Step 1 fixture.
 *
 *   node apps/dashboard/scripts/check-evidence.mjs
 *
 * Prints one line per check and exits 1 if any fails. Needs Node with
 * built-in type stripping (22.18+ / 24) to import the .ts files.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// apps/dashboard/package.json has no "type" field (Next.js owns it), so Node
// warns MODULE_TYPELESS_PACKAGE_JSON when it loads the ESM .ts files.
// Swallow just that warning; everything else still reaches stderr.
process.removeAllListeners("warning");
process.on("warning", (w) => {
  if (w.code !== "MODULE_TYPELESS_PACKAGE_JSON") console.error(w.stack ?? w.message);
});
const F = await import("../lib/findings.ts");
const E = await import("../lib/evidence.ts");

const fixture = JSON.parse(readFileSync(new URL("./fixtures/evidence-sample.json", import.meta.url), "utf8"));
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

const ev = (photo, box, kind, extra = {}) => ({
  photo,
  box,
  box_kind: box ? kind : null,
  by: kind === "sort" ? "sort" : "overall",
  reason: null,
  confidence: null,
  img_w: null,
  img_h: null,
  ...extra,
});

// ---------------------------------------------------------------------------
console.log("-- box precedence");

check("an AI box beats a sorter's box beats none on one photo", () => {
  assert.equal(E.pickBox([ev("@a", null, null), ev("@a", [1, 1, 50, 50], "sort"), ev("@a", [5, 5, 60, 60], "ai")]).box_kind, "ai");
  assert.equal(E.pickBox([ev("@a", null, null), ev("@a", [1, 1, 50, 50], "sort")]).box_kind, "sort");
  assert.equal(E.pickBox([ev("@a", null, null)]).box, null);
  // A broken box counts as none.
  assert.equal(E.pickBox([ev("@a", [50, 50, 10, 10], "ai"), ev("@a", [1, 1, 50, 50], "sort")]).box_kind, "sort");
});

check("slidesOf: one slide a photo, its box by precedence, words from the entry that has them", () => {
  const slides = E.slidesOf({
    evidence: [
      ev("@a", null, null, { reason: "Crack along the edge", by: "zone:rear" }),
      ev("@a", [100, 100, 300, 300], "sort"),
      ev("@a", [110, 120, 290, 310], "ai", { img_w: 1600, img_h: 1200 }),
      ev("@b", null, null),
    ],
  });
  assert.equal(slides.length, 2);
  assert.deepEqual(slides[0].box, [110, 120, 290, 310]);
  assert.equal(slides[0].kind, "ai");
  assert.deepEqual(slides[0].frame, { w: 1600, h: 1200 });
  assert.equal(slides[0].reason, "Crack along the edge");
  assert.equal(slides[1].box, null);
  assert.equal(slides[1].kind, null);
});

check("the item's bbox_2d (the AI's box on its own photo) beats a sorter's box there", () => {
  const item = { ...clone(data.damage_summary[1]), bbox_2d: [120, 260, 430, 740] };
  const first = E.slidesOf({ evidence: F.evidenceOf(item) })[0];
  assert.equal(first.photo, "@ph05");
  assert.equal(first.kind, "ai");
  assert.deepEqual(first.box, [120, 260, 430, 740]);
  // Without it, the sorter's box shows (fixture: Boot Lid).
  const sorted = E.slidesOf({ evidence: F.evidenceOf(data.damage_summary[1]) })[0];
  assert.equal(sorted.kind, "sort");
  assert.equal(E.boxLine(sorted), "Sorter's box");
});

check("a photo a person linked comes first, with no box; the AI's photos follow", () => {
  const item = { ...clone(data.damage_summary[0]), photo_reference: "@ph02", bbox_2d: null };
  const evidence = F.evidenceOf(item);
  assert.deepEqual(
    evidence.map((e) => e.photo),
    ["@ph02", "@ph03", "@ph04"]
  );
  assert.equal(evidence[0].box, null);
  assert.equal(evidence[0].by, "linked");
  assert.equal(evidence[1].box_kind, "ai");
});

// ---------------------------------------------------------------------------
console.log("-- boxes and frames");

check("cleanBox clamps into the photo and refuses inverted, empty or odd boxes", () => {
  assert.deepEqual(E.cleanBox([-5, 10, 500, 1010]), [0, 10, 500, 1000]);
  assert.equal(E.cleanBox([500, 10, 400, 20]), null);
  assert.equal(E.cleanBox([10, 10, 10, 20]), null);
  assert.equal(E.cleanBox([1, 2, 3]), null);
  assert.equal(E.cleanBox([1, 2, 3, "4"]), null);
  assert.equal(E.cleanBox(null), null);
});

check("bboxStyle puts the 0-1000 box in percentages", () => {
  assert.deepEqual(E.bboxStyle([300, 300, 500, 500]), { top: "30%", left: "30%", width: "20%", height: "20%" });
  assert.equal(E.bboxStyle([500, 500, 300, 300]), null);
});

check("a box drawn on a frame of another shape (>2%) is hidden; same shape or unknown is drawn", () => {
  const frame = { w: 1600, h: 1200 };
  assert.equal(E.aspectMatches(frame, { w: 1600, h: 1200 }), true);
  assert.equal(E.aspectMatches(frame, { w: 400, h: 300 }), true, "the thumbnail has the same shape");
  assert.equal(E.aspectMatches(frame, { w: 1200, h: 1600 }), false, "rotated");
  assert.equal(E.aspectMatches(frame, { w: 1600, h: 900 }), false, "cropped to 16:9");
  assert.equal(E.aspectMatches(frame, { w: 1600, h: 1182 }), true, "1.5% off");
  assert.equal(E.aspectMatches(frame, { w: 1600, h: 1171 }), false, "2.5% off");
  assert.equal(E.aspectMatches(null, { w: 1600, h: 900 }), true, "no frame recorded: drawn as before");
  assert.equal(E.aspectMatches(frame, null), true, "not loaded yet");
});

check("the box words are the contract's", () => {
  assert.equal(E.NO_BOX_TEXT, "No box: the AI named the part but didn't mark where");
  assert.equal(E.boxLine({ box: null, kind: null }), E.NO_BOX_TEXT);
  assert.equal(E.boxLine({ box: [1, 1, 50, 50], kind: "ai" }), "AI box");
  assert.match(E.boxLine({ box: [1, 1, 50, 50], kind: "ai" }, { hidden: true }), /box hidden: photo changed/);
  assert.equal(E.boxChip({ box: null, kind: null }), "no box");
});

check("fitSize and zoomToBox", () => {
  assert.deepEqual(E.fitSize({ w: 1600, h: 1200 }, { w: 800, h: 800 }), { w: 800, h: 600 });
  assert.deepEqual(E.fitSize({ w: 1200, h: 1600 }, { w: 800, h: 800 }), { w: 600, h: 800 });
  const z = E.zoomToBox([400, 400, 600, 600], { w: 1000, h: 750 }, { w: 1000, h: 750 });
  assert.equal(z.zoom, 3);
  assert.equal(z.left, 1000);
  assert.equal(z.top, 750);
  assert.equal(E.zoomToBox([490, 490, 500, 500], { w: 1000, h: 750 }, { w: 1000, h: 750 }).zoom, 4, "4x at most");
  const big = E.zoomToBox([0, 0, 1000, 1000], { w: 1000, h: 750 }, { w: 1000, h: 750 });
  assert.deepEqual(big, { zoom: 1, left: 0, top: 0 });
});

// ---------------------------------------------------------------------------
console.log("-- evidenceOf on older items (section 4.9)");

check("no evidence: photo_reference + bbox_2d as an AI box, then the other seen_in photos with none", () => {
  const evidence = F.evidenceOf({ part: "Bonnet", photo_reference: "P03", bbox_2d: [100, 200, 300, 400], seen_in: ["P03", "P04"], ai_confidence: "High" });
  assert.equal(evidence.length, 2);
  assert.deepEqual(evidence[0], { photo: "P03", box: [100, 200, 300, 400], box_kind: "ai", by: "overall", reason: null, confidence: "High", img_w: null, img_h: null });
  assert.equal(evidence[1].photo, "P04");
  assert.equal(evidence[1].box, null);
  assert.equal(evidence[1].box_kind, null);
});

check("no box and no photo: nothing to show; a person's own item says linked", () => {
  assert.deepEqual(F.evidenceOf({ part: "Bonnet" }), []);
  const mine = F.evidenceOf({ part: "Bonnet", photo_reference: "@ph02", ai_confidence: null, human_verified: true });
  assert.equal(mine[0].by, "linked");
  assert.equal(mine[0].box_kind, null);
});

check("the fixture's old-style item (Rear End Panel, evidence []) gets its AI box back", () => {
  const evidence = F.evidenceOf(data.damage_summary[3]);
  assert.equal(evidence.length, 1);
  assert.equal(evidence[0].photo, "@ph07");
  assert.deepEqual(evidence[0].box, [300, 300, 600, 700]);
  assert.equal(evidence[0].box_kind, "ai");
});

// ---------------------------------------------------------------------------
console.log("-- findings and status mapping");

const findings = F.buildFindings(data);
const withDismissed = F.buildFindings(data, { showDismissed: true });
const items = findings.filter((f) => f.kind === "item");
const sugs = findings.filter((f) => f.kind === "suggestion");

check("list items map to confirmed / ai / check as the review badge does", () => {
  assert.deepEqual(
    items.map((f) => f.status),
    ["ai", "ai", "check", "confirmed", "confirmed", "check"]
  );
  assert.deepEqual(
    items.map((f) => f.key),
    ["item:0", "item:1", "item:2", "item:3", "item:4", "item:5"]
  );
  assert.equal(items[5].name, "Tow hook cover", "a named Other shows its name");
  assert.equal(items[5].partKey, "Other / Not Listed: tow hook cover");
});

check("suggestions: undecided ones in, a Not damaged one only with showDismissed", () => {
  assert.deepEqual(
    sugs.map((f) => f.key),
    ["sug:Boot Lid Lock", "sug:Right Tail Lamp", "sug:Left Rear Bumper Reflector", "sug:Left Rear Wheel Arch Liner"]
  );
  assert.ok(sugs.every((f) => f.status === "suggestion"));
  const hinge = withDismissed.find((f) => f.key === "sug:Boot Lid Hinge");
  assert.equal(hinge?.status, "dismissed");
  assert.equal(hinge?.source, "kit");
  assert.equal(sugs[0].name, "Tail gate lock", "the firm's wording");
  assert.equal(sugs[1].otherSideListed, "Left Tail Lamp");
});

check("a suggestion the list covers, or a part removed from the list, is not a finding", () => {
  const listed = clone(data);
  listed.damage_summary.push({ part: "Tail Lamp (side undetermined)", severity: null });
  assert.ok(!F.buildFindings(listed).some((f) => f.key === "sug:Right Tail Lamp"));
  const removed = { ...clone(data), dismissed_parts: ["Boot Lid Lock"] };
  assert.ok(!F.buildFindings(removed).some((f) => f.key === "sug:Boot Lid Lock"));
});

check("an old suggestion with no key goes by its part; an old dismissed list by part name", () => {
  const old = clone(data);
  old.second_look.items = old.second_look.items.map(({ key, ...rest }) => rest);
  const keys = F.buildFindings(old).filter((f) => f.kind === "suggestion").map((f) => f.partKey);
  assert.deepEqual(keys, ["Boot Lid Lock", "Right Tail Lamp", "Left Rear Bumper Reflector", "Left Rear Wheel Arch Liner"]);
});

check("no second_look: no suggestions at all", () => {
  const none = { ...clone(data), second_look: null };
  assert.equal(F.buildFindings(none).filter((f) => f.kind === "suggestion").length, 0);
});

check("a deleted photo stays in the evidence (the UI shows it as deleted)", () => {
  const liner = sugs.find((f) => f.key === "sug:Left Rear Wheel Arch Liner");
  assert.deepEqual(
    E.slidesOf(liner).map((s) => s.photo),
    ["@ph08", "@ph12"]
  );
  const deleted = fixture.photos.find((p) => p.ref === "@ph12");
  assert.equal(deleted.deleted, true);
});

// ---------------------------------------------------------------------------
console.log("-- words");

check("the re-check in words, never a number", () => {
  assert.equal(F.checkWords({ verdict: "yes", p: 0.91 }), "AI re-check: probably damaged");
  assert.equal(F.checkWords({ verdict: "unsure", p: 0.52 }), "AI re-check: unsure");
  assert.equal(F.checkWords({ verdict: "no", p: 0.12 }), "AI re-check: probably not damaged");
  assert.equal(F.checkWords(null), null);
});

check("Found by N of M reads, seen in, AI confidence", () => {
  const lock = sugs[0];
  assert.equal(F.foundByText(lock, data.second_look.reads), "Found by 1 of 4 reads");
  assert.equal(F.foundByText(lock), "Found by the rear read");
  assert.equal(F.foundByText({ foundBy: ["zone:left", "zone:close"] }), "Found by the left side and close-up reads");
  assert.equal(F.foundByText({ foundBy: [] }, data.second_look.reads), null);
  assert.equal(F.foundByNames(lock), "rear read");
  assert.equal(F.seenCount(lock), "seen in 1 photo");
  assert.equal(F.confidenceText("Medium"), "AI: Medium");
  assert.equal(F.confidenceText(null), null);
  assert.equal(F.aiSays("Lens cracked"), "AI says: “Lens cracked”");
});

check("codeReason: the code's own line when the AI gave none", () => {
  assert.equal(F.codeReason(items[1]), "Listed by the main read · seen in 1 photo");
  const hinge = withDismissed.find((f) => f.key === "sug:Boot Lid Hinge");
  assert.equal(F.codeReason(hinge), "Often damaged with a part found here (past cases) · seen in 1 photo");
  const filed = { ...items[0], seenIn: ["P03", "P04"] };
  assert.equal(F.codeReason(filed), "Listed by the main read · seen in P03, P04");
});

check("nothing the cards or viewer say is a percentage", () => {
  const texts = [];
  for (const f of withDismissed) {
    texts.push(F.codeReason(f), F.foundByText(f, data.second_look.reads), F.foundByNames(f), F.confidenceText(f.confidence), F.checkWords(f.check), F.seenCount(f));
    for (const s of E.slidesOf(f)) texts.push(E.boxLine(s), E.boxChip(s), F.sourceName(s.by));
  }
  for (const t of texts.filter(Boolean)) assert.ok(!/%|\d\.\d/.test(t), `"${t}"`);
});

// ---------------------------------------------------------------------------
console.log("-- order and decisions");

check("sortByCheck: yes (most likely first), then unsure / none, then no", () => {
  assert.deepEqual(
    F.sortByCheck(sugs).map((f) => f.partKey),
    ["Boot Lid Lock", "Right Tail Lamp", "Left Rear Wheel Arch Liner", "Left Rear Bumper Reflector"]
  );
  const mk = (key, verdict, p, confidence = "Medium") => ({ key, partKey: key, check: verdict ? { verdict, p } : null, confidence });
  const order = F.sortByCheck([mk("n1", "no", 0.25), mk("y1", "yes", 0.8), mk("u", "unsure", 0.5, "Low"), mk("y2", "yes", 0.95), mk("x", null, null, "High"), mk("n2", "no", 0.1)]);
  assert.deepEqual(
    order.map((f) => f.key),
    ["y2", "y1", "x", "u", "n2", "n1"]
  );
});

check("decisionStates / decidedCounts from the fixture's log", () => {
  const states = F.decisionStates(data.second_look.decisions);
  assert.deepEqual([...states.keys()].sort(), ["Boot Lid Hinge", "Left Rear Quarter Panel", "Rear Windscreen"]);
  assert.equal(states.get("Left Rear Quarter Panel").action, "added");
  assert.deepEqual(F.decidedCounts(data.second_look.decisions, data.second_look.dismissed), { added: 1, dismissed: 2 });
  assert.deepEqual(F.decidedCounts([], ["Old Part"]), { added: 0, dismissed: 1 }, "an older dismissed list counts");
});

const at = "2026-11-15T03:00:00Z";
const decision = (key, action) => ({ key, part: key, action, by_id: "u1", by_name: "Test", at, looked: true, where: "intake" });

check("undo reverts the latest decision still standing", () => {
  const log = [...data.second_look.decisions, decision("Left Rear Quarter Panel", "undone")];
  assert.ok(!F.decisionStates(log).has("Left Rear Quarter Panel"));
  assert.equal(F.undoneAction(log, "Left Rear Quarter Panel"), "added");
  const twice = [decision("K", "dismissed"), decision("K", "undone"), decision("K", "dismissed")];
  assert.equal(F.decisionStates(twice).get("K").action, "dismissed");
  assert.equal(F.undoneAction(twice, "K"), "dismissed");
  assert.equal(F.undoneAction(twice, "Other"), null);
});

check("undecidedCount: what the filing and sign-off warnings count", () => {
  assert.equal(F.undecidedCount(data), 4);
  assert.equal(F.undecidedCount({ ...clone(data), second_look: null }), 0);
});

// ---------------------------------------------------------------------------
console.log("-- the wizard's local draft after a decision (applyDecision)");

const lockItem = { part: "Boot Lid Lock", condition: "Bent", photo_reference: "@ph05", bbox_2d: [380, 430, 520, 570], human_verified: true, added_from: "suggestion" };

check("add: the server's item goes on the end of the list, once; second_look is the server's", () => {
  const look = { ...clone(data.second_look), decisions: [...data.second_look.decisions, decision("Boot Lid Lock", "added")] };
  const r = { second_look: look, item: lockItem, item_index: 6 };
  const next = F.applyDecision(data, r, { key: "Boot Lid Lock", action: "add" });
  assert.equal(next.damage_summary.length, 7);
  assert.equal(next.damage_summary[6], lockItem);
  assert.ok(next.damaged_parts.includes("Boot Lid Lock"));
  assert.equal(next.second_look, look);
  const again = F.applyDecision(next, r, { key: "Boot Lid Lock", action: "add" });
  assert.equal(again.damage_summary.length, 7, "a repeated answer adds nothing");
  // The added part is now listed, so it is no longer a suggestion.
  assert.ok(!F.buildFindings(next).some((f) => f.key === "sug:Boot Lid Lock"));
  // An "already listed" answer (item null) leaves the list alone.
  const covered = F.applyDecision(data, { second_look: look, item: null, item_index: null }, { key: "Boot Lid Lock", action: "add" });
  assert.equal(covered.damage_summary, data.damage_summary);
});

check("undo of an add takes the suggestion's item off; a person's own item of that part stays", () => {
  const added = { ...clone(data), damage_summary: [...clone(data.damage_summary), lockItem, { part: "Boot Lid Lock", human_verified: true }] };
  const look = { ...clone(data.second_look), decisions: [decision("Boot Lid Lock", "added"), decision("Boot Lid Lock", "undone")] };
  const next = F.applyDecision(added, { second_look: look, item: null, item_index: null }, { key: "Boot Lid Lock", action: "undo" });
  assert.equal(next.damage_summary.length, 7);
  assert.ok(!next.damage_summary.some((it) => it.added_from === "suggestion" && it.part === "Boot Lid Lock"));
  assert.ok(next.damage_summary.some((it) => it.part === "Boot Lid Lock" && !it.added_from));
});

check("dismiss, and undo of a dismiss, change second_look only", () => {
  const look = { ...clone(data.second_look), dismissed: [...data.second_look.dismissed, "Right Tail Lamp"] };
  const next = F.applyDecision(data, { second_look: look, item: null, item_index: null }, { key: "Right Tail Lamp", action: "dismiss" });
  assert.equal(next.damage_summary, data.damage_summary);
  assert.ok(!F.buildFindings(next).some((f) => f.key === "sug:Right Tail Lamp"));
  const undoLook = { ...clone(data.second_look), decisions: [decision("Right Tail Lamp", "dismissed"), decision("Right Tail Lamp", "undone")] };
  const back = F.applyDecision(next, { second_look: undoLook, item: null, item_index: null }, { key: "Right Tail Lamp", action: "undo" });
  assert.equal(back.damage_summary, data.damage_summary);
});

check("a re-read keeps the decisions: same suggestions hidden, same counts", () => {
  // As the server's _store leaves it after a re-read: the list and the items
  // rebuilt (new objects, another order), dismissed and decisions kept.
  const reread = clone(data);
  reread.damage_summary = clone(data.damage_summary).reverse();
  reread.second_look.items = clone(data.second_look.items).reverse();
  reread.second_look.check = null;
  const before = new Set(F.buildFindings(data).filter((f) => f.kind === "suggestion").map((f) => f.partKey));
  const after = new Set(F.buildFindings(reread).filter((f) => f.kind === "suggestion").map((f) => f.partKey));
  assert.deepEqual([...after].sort(), [...before].sort());
  assert.ok(!after.has("Boot Lid Hinge"), "still dismissed");
  assert.deepEqual(F.decidedCounts(reread.second_look.decisions, reread.second_look.dismissed), { added: 1, dismissed: 2 });
  assert.equal(F.undecidedCount(reread), F.undecidedCount(data));
});

check("decided suggestions kept in items (review L3): out of the list, the listed one never twice, Undo brings each back", () => {
  // As the server keeps them after a re-read: the added suggestion (its part
  // on the list) and the dismissed one stay in second_look.items.
  const kept = clone(data);
  const like = (key) => ({ ...clone(data.second_look.items[0]), key, part: key, name: null, covers: [key], evidence: [], seen_in: [], check: null });
  kept.second_look.items.push(like("Left Rear Quarter Panel"), like("Rear Windscreen"));
  const openKeys = (d) => F.buildFindings(d).filter((f) => f.kind === "suggestion").map((f) => f.partKey);
  assert.deepEqual(openKeys(kept), openKeys(data), "the main list is the same");
  assert.equal(F.undecidedCount(kept), F.undecidedCount(data));
  const all = F.buildFindings(kept, { showDismissed: true });
  assert.deepEqual(
    all.filter((f) => f.partKey === "Left Rear Quarter Panel").map((f) => f.key),
    ["item:4"],
    "an added suggestion is its list row only"
  );
  assert.equal(all.find((f) => f.key === "sug:Rear Windscreen")?.status, "dismissed", "under Show dismissed / Decided");
  // Undo of the dismissal: its card comes back.
  const undismissed = clone(kept);
  undismissed.second_look.dismissed = undismissed.second_look.dismissed.filter((k) => k !== "Rear Windscreen");
  assert.equal(F.buildFindings(undismissed).find((f) => f.key === "sug:Rear Windscreen")?.status, "suggestion");
  // Undo of the add: the row comes off the list, the card comes back, once.
  const look = { ...clone(kept.second_look), decisions: [...kept.second_look.decisions, decision("Left Rear Quarter Panel", "undone")] };
  const asked = { key: "Left Rear Quarter Panel", action: "undo" };
  const r = { second_look: look, item: clone(data.damage_summary[4]), item_index: 4 };
  assert.deepEqual(F.rowsTakenOff(kept.damage_summary, r, asked), [4], "the row a selection must follow");
  assert.deepEqual(F.rowsTakenOff(kept.damage_summary, r, { ...asked, action: "add" }), []);
  const back = F.applyDecision(kept, r, asked);
  assert.deepEqual(
    F.buildFindings(back).filter((f) => f.partKey === "Left Rear Quarter Panel").map((f) => f.key),
    ["sug:Left Rear Quarter Panel"]
  );
});

process.exitCode = failed ? 1 : 0;
console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
