#!/usr/bin/env node
/**
 * The Review step's pinned 3D map must never cover anything (owner,
 * 23 Nov 2026). First the lists showed THROUGH the map (its background was
 * 85% opaque); then, with a solid map, they slid UNDER it, readable only in
 * a strip below it. The fix: the map has a column of its own, and the page's
 * sticky top bar is solid. This checks, without a browser:
 *
 * app/globals.css
 *   - every rule that makes .dmap-intake sticky sets
 *     background: var(--surface-card-solid) and a max-height that leaves
 *     room for the top bar and the footer (calc(100vh - ...px - ...px));
 *   - the review layout has the areas "cover map" / "lists map" (the map
 *     spans both rows) and the map area stretches (align-self: stretch);
 *   - narrow screens stack cover, map, lists;
 *   - .top-bar uses var(--nav-bg-solid); --surface-card-solid and
 *     --nav-bg-solid are solid colours (#rrggbb) in both themes.
 * components/intake/ReviewStep.tsx
 *   - the map's area holds the map and nothing else: the damaged parts,
 *     the suggestions and photos by part come after it, in the lists area.
 *
 *   node apps/dashboard/scripts/check-dmap-sticky.mjs
 *
 * Prints one line per check and exits 1 if any fails. The live check (a
 * browser scrolling the whole page at several screen sizes) runs against
 * staging before a release; see the PR.
 */
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const tsx = readFileSync(new URL("../components/intake/ReviewStep.tsx", import.meta.url), "utf8");
let failed = 0;
const check = (ok, what) => {
  console.log(`${ok ? "ok  " : "FAIL"} ${what}`);
  if (!ok) failed += 1;
};

// Each "selector { body }" with no nested braces in the body.
const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({ sel: m[1].trim(), body: m[2] }));
const has = (sel) => (r) => r.sel.split(",").map((s) => s.trim()).includes(sel);

// The pinned map.
const pinned = rules.filter((r) => /(^|[\s,])\.dmap-intake(?![\w-])/.test(r.sel) && /position\s*:\s*sticky/.test(r.body));
check(pinned.length > 0, `found the rule(s) that pin .dmap-intake (${pinned.length})`);
for (const r of pinned) {
  check(/(^|;|\s)background\s*:\s*var\(--surface-card-solid\)/.test(r.body),
    `"${r.sel}" (sticky) sets background: var(--surface-card-solid)`);
  check(/max-height\s*:\s*calc\(100vh\s*-\s*\d+px\s*-\s*\d+px\)/.test(r.body),
    `"${r.sel}" (sticky) leaves room for the top bar and the footer (max-height: calc(100vh - Apx - Bpx))`);
}

// The review layout: the map alone in its column.
const layout = rules.filter(has(".intake-review-layout")).map((r) => r.body.replace(/\s+/g, " ")).join(" ");
check(/grid-template-areas\s*:\s*"cover map"\s*"lists map"\s*"footer footer"/.test(layout),
  `.intake-review-layout: areas "cover map" / "lists map" / "footer footer" (the map spans both rows)`);
check(/grid-template-areas\s*:\s*"cover"\s*"map"\s*"lists"\s*"footer"/.test(layout),
  `.intake-review-layout narrow: "cover" / "map" / "lists" / "footer"`);
const mapArea = rules.filter(has(".intake-area-map")).map((r) => r.body).join(" ");
check(/grid-area\s*:\s*map/.test(mapArea) && /align-self\s*:\s*stretch/.test(mapArea),
  ".intake-area-map: grid-area map and align-self: stretch (as tall as both rows, so the map stays pinned)");
check(rules.some((r) => has(".intake-area-cover")(r) && /grid-area\s*:\s*cover/.test(r.body)), ".intake-area-cover: grid-area cover");
check(rules.some((r) => has(".intake-area-lists")(r) && /grid-area\s*:\s*lists/.test(r.body)), ".intake-area-lists: grid-area lists");

// The solid top bar.
const topBar = rules.filter(has(".top-bar")).map((r) => r.body).join(" ");
check(/background\s*:\s*var\(--nav-bg-solid\)/.test(topBar), ".top-bar: background var(--nav-bg-solid)");
for (const token of ["--surface-card-solid", "--nav-bg-solid"]) {
  const values = [...css.matchAll(new RegExp(`${token}\\s*:\\s*([^;]+);`, "g"))].map((m) => m[1].trim());
  check(values.length >= 2, `${token} is defined for both themes (${values.length} definitions)`);
  for (const v of values) check(/^#[0-9a-fA-F]{6}$/.test(v), `${token} ${v} is a solid colour`);
}

// ReviewStep.tsx: what each area holds.
const at = (s) => tsx.indexOf(s);
const iCover = at('className="intake-col intake-area-cover"');
const iMap = at('className="intake-col intake-area-map"');
const iLists = at('className="intake-col intake-area-lists"');
const iFooter = at('className="intake-footer intake-review-footer"');
check(iCover > 0 && iMap > iCover && iLists > iMap && iFooter > iLists, "ReviewStep: the areas in the order cover, map, lists, footer");
const mapBlock = tsx.slice(iMap, iLists);
check(mapBlock.includes("<DamageMapPanel") && !/<SuggestionPanel|<PhotosByPart|Damaged parts|<section/.test(mapBlock),
  "ReviewStep: the map's area holds only the map");
const listsBlock = tsx.slice(iLists, iFooter);
check(["Damaged parts", "<SuggestionPanel", "<PhotosByPart"].every((s) => listsBlock.includes(s)),
  "ReviewStep: damaged parts, suggestions and photos by part are in the lists area");

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
process.exit(failed ? 1 : 0);
