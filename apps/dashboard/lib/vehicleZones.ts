/** Free-text damage_summary.part -> zone key mapping, shared between the
 * Damage & Parts Checklist table (StudioApp.tsx) and the 3D model
 * (VehicleBlueprint3D.tsx) so both always agree on which badge number
 * belongs to which part -- computed once here rather than duplicated in
 * two places that could drift apart.
 *
 * Same keyword-regex philosophy as the app's older zone matchers: checked
 * in order, most specific first, never an exact-string lookup since real
 * part text ("Rear bumper fascia", "Tail gate lock striker") never matches
 * a fixed vocabulary.
 */
import type { DamageSummaryItem } from "./api";

interface CategoryRule {
  test: RegExp;
  zoneKey?: string; // resolves directly, no side/depth needed
  base?: string; // needs side and/or depth resolution
  paired?: boolean;
  depthVar?: boolean;
}

const CATEGORY_RULES: CategoryRule[] = [
  // Structural/rear-body items without their own visible panel in the 3D
  // model -- the closest real, visible zone is the rear bumper area.
  { test: /rear\s*(end|body)?\s*panel|floor\s*panel/i, zoneKey: "rear_bumper" },
  { test: /sensor|reverse/i, zoneKey: "rear_bumper" },
  { test: /plate/i, zoneKey: "rear_bumper" },
  { test: /tail\s*-?gate|\bboot\b|\btrunk\b/i, zoneKey: "tailgate" },
  { test: /rear.*(glass|window|screen)\b|back\s*glass/i, zoneKey: "rear_glass" },
  { test: /wind\s*-?screen|wind\s*-?shield/i, zoneKey: "windscreen" },
  { test: /\broof\b/i, zoneKey: "roof" },
  { test: /\bbonnet\b|\bhood\b/i, zoneKey: "bonnet" },
  { test: /\bbumper\b/i, base: "bumper", depthVar: true },
  { test: /head\s*-?lamp|head\s*-?light/i, base: "headlamp", paired: true },
  { test: /tail\s*-?lamp|tail\s*-?light|rear.*light|rear.*lamp/i, base: "taillamp", paired: true },
  { test: /mirror/i, base: "mirror", paired: true },
  { test: /fender|wheel\s*arch|wing/i, base: "fender", paired: true },
  { test: /\bdoor\b/i, base: "door", paired: true, depthVar: true },
];

const SIDE_LEFT = /\bleft\b/i;
const SIDE_RIGHT = /\bright\b/i;
const DEPTH_REAR = /rear|\bback\b/i;

/** `sideHint` is only ever used when the part's own text doesn't say
 * left/right -- see resolveZones() below for where that hint comes from
 * and why it's safe (only applied when the rest of the same report is
 * unambiguous about which side). */
export function zoneKeyFor(part: string, sideHint?: "l" | "r"): string | null {
  const rule = CATEGORY_RULES.find((r) => r.test.test(part));
  if (!rule) return null;
  if (rule.zoneKey) return rule.zoneKey;

  let side: "l" | "r" | null = null;
  if (rule.paired) {
    if (SIDE_LEFT.test(part)) side = "l";
    else if (SIDE_RIGHT.test(part)) side = "r";
    else if (sideHint) side = sideHint;
    if (!side) return null; // genuinely can't tell which real side -- never guess
  }
  const depth = rule.depthVar ? (DEPTH_REAR.test(part) ? "rear" : "front") : null;

  if (side && depth) return `${side}_${rule.base}_${depth}`; // l_door_front
  if (side) return `${side}_${rule.base}`; // l_headlamp
  if (depth) return `${depth}_${rule.base}`; // front_bumper
  return rule.base!;
}

export interface ZoneResolution {
  key: string;
  badgeNumber: number; // 1-based, matches the numbered marker shown on the 3D model
}

/** Resolves every damage item to a zone + a shared badge number in one
 * pass, so the table and the 3D view can never disagree.
 *
 * Left/right inference: an item with no explicit side in its own text
 * (e.g. a plain "Fender" on a report whose other items say "Right Door")
 * only gets assigned the report's dominant side when that side is
 * unambiguous among the OTHER items that did state one explicitly. A
 * report with a genuine mix (some left, some right) leaves ambiguous
 * items unresolved (null) rather than guessing -- still shown in the
 * table, just without a 3D marker, an honest gap rather than a
 * potentially wrong claim about which specific panel is damaged. */
export function resolveZones(damageEntries: DamageSummaryItem[]): (ZoneResolution | null)[] {
  let leftCount = 0;
  let rightCount = 0;
  damageEntries.forEach((item) => {
    if (SIDE_LEFT.test(item.part)) leftCount++;
    else if (SIDE_RIGHT.test(item.part)) rightCount++;
  });
  const dominantSide: "l" | "r" | undefined = leftCount === rightCount ? undefined : leftCount > rightCount ? "l" : "r";

  const zoneOrder: string[] = [];
  return damageEntries.map((item) => {
    const key = zoneKeyFor(item.part, dominantSide);
    if (!key) return null;
    let order = zoneOrder.indexOf(key);
    if (order === -1) {
      zoneOrder.push(key);
      order = zoneOrder.length - 1;
    }
    return { key, badgeNumber: order + 1 };
  });
}
