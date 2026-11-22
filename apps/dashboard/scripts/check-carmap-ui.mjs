#!/usr/bin/env node
/**
 * Checks the car map's review screen helpers (K4,
 * plans/2026-11-18-car-knowledge/K1-design.md 5b) in lib/findings.ts --
 * the K4 fields on a Finding, the assembly groups, the folded "Check at
 * strip-down" block, the words, the viewer's walk -- against
 * scripts/fixtures/carmap-sample.json (synthetic, from K1's worked
 * examples), and that links-sample.json (no K4 field) still gives today's
 * sections.
 *
 *   node apps/dashboard/scripts/check-carmap-ui.mjs
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

const F = await import("../lib/findings.ts");

const load = (name) => JSON.parse(readFileSync(new URL(`./fixtures/${name}`, import.meta.url), "utf8"));
const fixture = load("carmap-sample.json");
const links = load("links-sample.json");
const data = fixture.data;
const clone = (x) => JSON.parse(JSON.stringify(x));
const photos = (ref) => fixture.photos.find((p) => p.ref === ref || p.label === ref) ?? null;

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

const open = (d = data) => F.buildFindings(d).filter((f) => f.kind === "suggestion" && f.status === "suggestion");
const parts = (list) => list.map((f) => f.part);
const sug = (part, d = data) => open(d).find((f) => f.part === part);
const texts = [];
const say = (t) => {
  if (t) texts.push(t);
  return t;
};

// ---------------------------------------------------------------------------
console.log("-- the K4 fields");

check("copied only when the server sent them", () => {
  const beam = sug("Front Bumper Beam");
  assert.equal(beam.assembly, "front-bumper");
  assert.equal(beam.assemblyName, "Front bumper assembly");
  assert.equal(beam.prior, 1);
  assert.equal(beam.hop, 1);
  assert.deepEqual(beam.alsoCheck, ["absorber", "side brackets"]);
  assert.deepEqual(beam.signsYes.map((s) => s.id), ["fb-pushed"]);
  assert.equal("fitted" in beam, false);
  assert.equal("folded" in beam, false);
  const radiator = sug("Radiator");
  assert.equal(radiator.hop, 2);
  assert.equal(radiator.via, "Air Conditioning Condenser");
  assert.equal(radiator.folded, true);
  const headlamp = sug("Right Headlamp");
  for (const k of ["assembly", "assemblyName", "prior", "hop", "via", "fitted", "fittedNote", "signsYes", "alsoCheck", "folded"])
    assert.equal(k in headlamp, false, k);
  for (const f of open(links.data)) for (const k of ["assembly", "folded", "signsYes", "alsoCheck"]) assert.equal(k in f, false, `${f.part} ${k}`);
});

check("a zone suggestion never takes the K4 fields, even if sent", () => {
  const d = clone(data);
  const zone = d.second_look.items.find((i) => i.source === "zone");
  zone.assembly = "front-bumper";
  zone.folded = true;
  const f = sug("Right Headlamp", d);
  assert.equal("assembly" in f, false);
  assert.equal("folded" in f, false);
});

// ---------------------------------------------------------------------------
console.log("-- the groups and the block");

const sections = F.suggestionSections(open());

check("assembly groups in the server's order; a part with none keeps its listed part's group; the block apart", () => {
  assert.deepEqual(parts(sections.seen), ["Right Headlamp"]);
  assert.deepEqual(sections.assemblies.map((g) => g.key), ["front-bumper", "boot-lid", "front-fender.left"]);
  assert.deepEqual(sections.groups.map((g) => g.key), ["Left Front Fender"]);
  assert.deepEqual(parts(sections.groups[0].rows), ["Left Front Wheel Arch Liner"]);
  assert.deepEqual(parts(sections.stripDown), [
    "Air Conditioning Condenser",
    "Engine Undercover",
    "Radiator",
    "Boot Lid Inner Trim",
    "Rear End Panel",
    "Rear Floor Panel",
  ]);
});

check("the header: name, damaged parent with its condition and hit, sign chips, also check", () => {
  const [bumper, boot, fender] = sections.assemblies;
  assert.equal(bumper.name, "Front bumper assembly");
  assert.equal(say(F.assemblyDamagedText(bumper)), "damaged: Front Bumper (Cracked/deformed · heavy hit)");
  assert.equal(say(F.assemblyDamagedText(boot)), "damaged: Boot Lid (Buckled · heavy hit)");
  assert.equal(say(F.assemblyDamagedText(fender)), "damaged: Left Front Fender (Dented · medium hit)");
  assert.deepEqual(bumper.signs.map((s) => s.id), ["fb-pushed"]);
  assert.equal(say(F.signText(bumper.signs[0], photos)), "Is the bumper cover pushed inward, not just scratched? yes · P03");
  assert.equal(say(F.alsoCheckText(bumper.alsoCheck)), "Also check (no part name yet): absorber, side brackets");
  assert.equal(F.alsoCheckText(boot.alsoCheck), null, "only the group whose relations they are");
  assert.equal(fender.name, "Front fender assembly");
});

check("inside a group: the server's order in each bucket (its prior order), 'probably not' last", () => {
  const [bumper, boot] = sections.assemblies;
  assert.deepEqual(parts(bumper.rows), ["Left Fog Lamp", "Front Bumper Beam", "Front Number Plate"]);
  assert.deepEqual(parts(bumper.probablyNot), ["Front Grille"]);
  assert.deepEqual(parts(boot.rows), ["Boot Lid Lock", "Boot Lid Hinge", "Reversing Camera"]);
  // The kind-of-link order would put the camera (attached) before the lock (opens_with).
  const d = clone(data);
  const items = d.second_look.items;
  const at = (p) => items.findIndex((i) => i.part === p);
  const [lock] = items.splice(at("Boot Lid Lock"), 1);
  items.splice(at("Reversing Camera") + 1, 0, lock);
  assert.deepEqual(parts(F.suggestionSections(open(d)).assemblies[1].rows), ["Boot Lid Hinge", "Reversing Camera", "Boot Lid Lock"]);
});

check("two listed parts in one assembly: one group, both named, each with its hit", () => {
  const d = clone(data);
  d.damage_summary.push({ ...clone(d.damage_summary[0]), part: "Front Grille", condition: "Cracked", condition_tokens: ["Cracked"] });
  d.second_look.items.push({
    ...clone(d.second_look.items.find((i) => i.part === "Front Number Plate")),
    key: "Front Bumper Reflector",
    part: "Front Bumper Reflector",
    linked_from: ["Front Grille"],
    hit: "medium",
  });
  d.second_look.items = d.second_look.items.filter((i) => i.part !== "Front Grille");
  const g = F.suggestionSections(open(d)).assemblies[0];
  assert.equal(say(F.assemblyDamagedText(g)), "damaged: Front Bumper (Cracked/deformed · heavy hit), Front Grille (Cracked · medium hit)");
});

check("the block: its note, a second hop's words, the group named on each card", () => {
  assert.equal(say(F.stripDownBlockNote(sections.stripDown)), "not counted in the parts above · 2 via a second hop");
  assert.equal(say(F.hopText(sug("Radiator"))), "second hop, via Air Conditioning Condenser");
  assert.equal(F.hopText(sug("Engine Undercover")), null);
  assert.equal(F.stripDownBlockNote([{ hop: 1 }]), "not counted in the parts above");
});

check("the block holds a part only while it is still a strip-down one", () => {
  const d = clone(data);
  const condenser = d.second_look.items.find((i) => i.part === "Air Conditioning Condenser");
  condenser.bucket = "likely"; // the linked check saw it
  const radiator = d.second_look.items.find((i) => i.part === "Radiator");
  radiator.bucket = "likely"; // a second hop's stays until a photo shows it
  const s = F.suggestionSections(open(d));
  assert.equal(parts(s.stripDown).includes("Air Conditioning Condenser"), false);
  assert.ok(parts(s.assemblies[0].rows).includes("Air Conditioning Condenser"), "back in its group");
  assert.ok(parts(s.stripDown).includes("Radiator"));
  radiator.bucket = "seen";
  assert.equal(parts(F.suggestionSections(open(d)).stripDown).includes("Radiator"), false);
  assert.equal(F.inStripDownBlock({ bucket: "strip_down" }), false, "not folded: never in the block (cap in)");
});

check("'— if fitted' and the catalogue's note", () => {
  assert.equal(say(F.fittedText(sug("Left Fog Lamp"))), "— if fitted (this model lists it)");
  assert.equal(say(F.fittedText(sug("Reversing Camera"))), "— if fitted");
  assert.equal(F.fittedText(sug("Front Bumper Beam")), null);
  assert.equal(F.fittedText({ fittedNote: "not on this model's parts diagram" }), "(not on this model's parts diagram)");
});

check("a group with no name sent: its id in words", () => {
  assert.equal(F.assemblyFallbackName("front-door.left"), "Front door assembly");
  assert.equal(F.assemblyFallbackName("boot-lid"), "Boot lid assembly");
  assert.equal(F.assemblyFallbackName(""), "Linked parts");
  const d = clone(data);
  for (const i of d.second_look.items) delete i.assembly_name;
  assert.equal(F.suggestionSections(open(d)).assemblies[0].name, "Front bumper assembly");
});

// ---------------------------------------------------------------------------
console.log("-- the viewer's walk (A / N)");

check("panel order: seen, the assembly groups, the listed parts' groups, then the block", () => {
  assert.deepEqual(parts(F.panelOrder(open())), [
    "Right Headlamp",
    "Left Fog Lamp",
    "Front Bumper Beam",
    "Front Number Plate",
    "Front Grille",
    "Boot Lid Lock",
    "Boot Lid Hinge",
    "Reversing Camera",
    "Left Front Wheel Arch Trim",
    "Left Front Wheel Arch Liner",
    "Air Conditioning Condenser",
    "Engine Undercover",
    "Radiator",
    "Boot Lid Inner Trim",
    "Rear End Panel",
    "Rear Floor Panel",
  ]);
});

check("a decided part leaves its group and the walk; undone, it is back in its place", () => {
  const d = clone(data);
  d.second_look.dismissed.push("Boot Lid Lock");
  assert.equal(parts(F.panelOrder(open(d))).includes("Boot Lid Lock"), false);
  d.second_look.dismissed = [];
  assert.deepEqual(parts(F.panelOrder(open(d))), parts(F.panelOrder(open())));
});

// ---------------------------------------------------------------------------
console.log("-- without the K4 fields: today's panel");

check("links-sample.json: no assembly group, no block, the same listed-part groups and walk", () => {
  const list = open(links.data);
  const s = F.suggestionSections(list);
  assert.deepEqual(s.assemblies, []);
  assert.deepEqual(s.stripDown, []);
  assert.deepEqual(s.groups.map((g) => g.key), ["Rear Bumper", "Left Rear Door"]);
  assert.deepEqual(parts(F.panelOrder(list)), [
    "Boot Lid Lock",
    "Rear Bumper Reflector (side undetermined)",
    "Rear Bumper Beam",
    "Rear Floor Panel",
    "Left Rear Quarter Panel",
    "Left Rear Door Glass",
  ]);
});

check("the cap out without a car map (L1 + stripdown_cap out): the listed-part groups and the block", () => {
  const d = clone(links.data);
  const beam = d.second_look.items.find((i) => i.part === "Rear Bumper Beam");
  beam.folded = true;
  const s = F.suggestionSections(open(d));
  assert.deepEqual(parts(s.stripDown), ["Rear Bumper Beam"]);
  assert.deepEqual(s.assemblies, []);
  assert.equal(parts(s.groups[0].rows).includes("Rear Bumper Beam"), false);
});

// ---------------------------------------------------------------------------
console.log("-- words");

check("no percentage and no 'AI says' in the K4 words", () => {
  assert.ok(texts.length >= 10);
  for (const t of texts) {
    assert.doesNotMatch(t, /%/, t);
    assert.doesNotMatch(t, /AI says/, t);
  }
  for (const f of open()) {
    const t = F.codeReason(f);
    assert.doesNotMatch(t, /%/, t);
  }
});

// ---------------------------------------------------------------------------
console.log("-- a strip-down block card without a group name says what it hangs off");
check("alsoLinkedText(all): every listed part, \"Linked to\"", () => {
  const f = { source: "link", parents: [{ name: "Rear Bumper" }, { name: "Boot Lid" }] };
  assert.equal(F.alsoLinkedText(f), "Also linked to: Boot Lid");
  assert.equal(F.alsoLinkedText(f, true), "Linked to: Rear Bumper, Boot Lid");
  assert.equal(F.alsoLinkedText({ source: "link", parents: [{ name: "Rear Bumper" }] }, true), "Linked to: Rear Bumper");
  assert.equal(F.alsoLinkedText({ source: "link", parents: [{ name: "Rear Bumper" }] }), null);
  assert.equal(F.alsoLinkedText({ source: "link", linkedFrom: ["Rear Bumper"] }, true), "Linked to: Rear Bumper");
});

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
process.exit(failed ? 1 : 0);
