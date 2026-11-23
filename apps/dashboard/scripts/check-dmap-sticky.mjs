#!/usr/bin/env node
/**
 * The wizard's 3D damage map (.dmap-intake) stays pinned while the parts
 * list and the suggestions scroll up under it. If its pinned rule has no
 * solid background, the text underneath shows through the map (owner,
 * 23 Nov 2026). This checks app/globals.css:
 *   - every rule that makes .dmap-intake sticky also sets
 *     background: var(--surface-card-solid);
 *   - both themes define --surface-card-solid as a solid colour (#rrggbb).
 *
 *   node apps/dashboard/scripts/check-dmap-sticky.mjs
 *
 * Prints one line per check and exits 1 if any fails.
 */
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
let failed = 0;
const check = (ok, what) => {
  console.log(`${ok ? "ok  " : "FAIL"} ${what}`);
  if (!ok) failed += 1;
};

// Each "selector { body }" with no nested braces in the body.
const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({ sel: m[1].trim(), body: m[2] }));
const pinned = rules.filter((r) => /(^|[\s,])\.dmap-intake(?![\w-])/.test(r.sel) && /position\s*:\s*sticky/.test(r.body));
check(pinned.length > 0, `found the rule(s) that pin .dmap-intake (${pinned.length})`);
for (const r of pinned) {
  check(/(^|;|\s)background\s*:\s*var\(--surface-card-solid\)/.test(r.body),
    `"${r.sel}" (sticky) sets background: var(--surface-card-solid)`);
}

const solids = [...css.matchAll(/--surface-card-solid\s*:\s*([^;]+);/g)].map((m) => m[1].trim());
check(solids.length >= 2, `--surface-card-solid is defined for both themes (${solids.length} definitions)`);
for (const v of solids) check(/^#[0-9a-fA-F]{6}$/.test(v), `--surface-card-solid ${v} is a solid colour`);

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
process.exit(failed ? 1 : 0);
