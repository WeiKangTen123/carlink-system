/** Free-text damage_summary.part -> zone key mapping, shared between the
 * Damage & Parts Checklist table, the 3D damage map (DamageMapPanel,
 * VehicleBlueprint3D.tsx, lib/damageLinks.ts) and the cover page's impact
 * sketch, so all of them agree on which panel a part sits on -- computed
 * once here rather than duplicated in places that could drift apart.
 *
 * Same keyword-regex philosophy as the app's older zone matchers: checked
 * in order, most specific first, never an exact-string lookup since real
 * part text ("Rear bumper fascia", "Tail gate lock striker") never matches
 * a fixed vocabulary.
 *
 * Pure data and functions with a type-only import, so Node (built-in type
 * stripping) can load it: scripts/check-damage-links.mjs.
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
  { test: /wind\s*-?screen|wind\s*-?shield/i, zoneKey: "windscreen" },
  { test: /\broof\b/i, zoneKey: "roof" },
  { test: /\bbonnet\b|\bhood\b/i, zoneKey: "bonnet" },
  { test: /\b(front\s*)?grill(e)?\b/i, zoneKey: "front_grill" },
  // Underbody. Real assessor and AI output both reach for these terms on
  // rear/front impacts ("Undercarriage" came back on every test run against
  // a real collision photo), and they previously matched no zone at all --
  // so a case whose only detected damage was structural showed a completely
  // blank blueprint. Checked before the bumper/fender rules so "rear
  // subframe" resolves to the chassis rather than the bumper skin.
  { test: /undercarriage|under\s*-?body|chassis|sub\s*-?frame|floor\s*pan|cross\s*-?member|\bsill\b|rocker\s*panel/i, zoneKey: "underbody" },
  // Door glass before the generic door rule and before rear_glass, so
  // "rear door window glass LH" resolves to the door's own glass rather
  // than the tailgate/back windscreen or the door panel itself.
  { test: /door.*(window|glass)|(window|glass).*door|quarter\s*glass/i, base: "door_glass", paired: true, depthVar: true },
  { test: /rear.*(glass|window|screen)\b|back\s*glass/i, zoneKey: "rear_glass" },
  { test: /\bbumper\b/i, base: "bumper", depthVar: true },
  { test: /head\s*-?lamp|head\s*-?light/i, base: "headlamp", paired: true },
  { test: /tail\s*-?lamp|tail\s*-?light|rear.*light|rear.*lamp/i, base: "taillamp", paired: true },
  { test: /mirror/i, base: "mirror", paired: true },
  // Tyre/rim before the fender rule: "wheel arch" is bodywork (fender),
  // but a bare "wheel"/"rim"/"tyre" is the wheel itself, and the fender
  // rule's own /wheel\s*arch/ would otherwise swallow both.
  { test: /\btyre\b|\btire\b|\brim\b|\bwheel\b(?!\s*arch)/i, base: "wheel", paired: true, depthVar: true },
  // The rear quarter is its own zone: "Rear fender wheel arch garnish LH"
  // (real assessor wording) is the rear quarter, and the sedan's front
  // fender mesh is a different panel at the other end of the car.
  { test: /quarter\s*panel|rear.*(fender|wheel\s*arch|wing)|(fender|wheel\s*arch|wing).*\brear\b/i, base: "quarter", paired: true },
  { test: /fender|wheel\s*arch|wing/i, base: "fender", paired: true },
  { test: /\bdoor\b/i, base: "door", paired: true, depthVar: true },
];

// Real assessor reports in this market write sides as LH/RH (and L/H,
// R/H), not "Left"/"Right" -- confirmed on the live SLK 3063 Z report,
// where "Tail lamp LH", "Rear fender wheel arch garnish LH" and "Rear
// fender inner trim board LH" were all silently getting NO 3D marker
// because only the spelled-out words were recognised.
//
// Deliberately NOT matching nearside/offside: which physical side those
// mean depends on whether the market is left- or right-hand drive, so
// guessing would risk highlighting the wrong real panel.
/** Canonical part name -> 3D zone.
 *
 * Gemini is now constrained to these exact names (see taxonomy.py, fed to
 * the model as a schema enum), so for anything extracted from here on this
 * is a direct lookup rather than pattern-matching. The regex rules above
 * remain as the fallback for the six reports already on file, which hold
 * free text like "Rear bumper fascia" and "Undercarriage".
 *
 * `null` means the part is real but the models have no mesh for it -- an
 * honest gap. Those items still appear in the damage checklist and in the
 * map's "Not on model" list, rather than being silently attached to the
 * wrong panel. An `any_*` value names a part whose side (and for doors and
 * wheels, whose end) is unknown: EITHER_SIDE lists the zones it may be, and
 * the map marks all of them with "?" instead of guessing the left one.
 *
 * Keep the declaration and the plain "zone" / null values as they are:
 * bot-service scripts/check_taxonomy_sync.py reads this block.
 */
const CANONICAL_ZONES: Record<string, string | null> = {
  "Front Bumper": "front_bumper",
  "Front Grille": "front_grill",
  "Bonnet": "bonnet",
  "Left Headlamp": "l_headlamp",
  "Right Headlamp": "r_headlamp",
  "Front Windscreen": "windscreen",
  "Left Front Fender": "l_fender",
  "Right Front Fender": "r_fender",
  "Front Bumper Beam": "front_bumper",
  "Left Fog Lamp": "front_bumper",
  "Right Fog Lamp": "front_bumper",
  "Front Number Plate": "front_bumper",
  "Left Front Wheel Arch Liner": "l_fender",
  "Right Front Wheel Arch Liner": "r_fender",
  // The arch's outside garnish: on the fender's mesh, as the liner.
  "Left Front Wheel Arch Trim": "l_fender",
  "Right Front Wheel Arch Trim": "r_fender",

  "Left Front Door": "l_door_front",
  "Right Front Door": "r_door_front",
  "Left Rear Door": "l_door_rear",
  "Right Rear Door": "r_door_rear",
  "Left Front Door Glass": "l_door_glass_front",
  "Right Front Door Glass": "r_door_glass_front",
  "Left Rear Door Glass": "l_door_glass_rear",
  "Right Rear Door Glass": "r_door_glass_rear",
  "Left Wing Mirror": "l_mirror",
  "Right Wing Mirror": "r_mirror",
  // Inside the door: no mesh of its own, so it marks its door.
  "Left Front Window Regulator": "l_door_front",
  "Right Front Window Regulator": "r_door_front",
  "Left Rear Window Regulator": "l_door_rear",
  "Right Rear Window Regulator": "r_door_rear",

  "Rear Bumper": "rear_bumper",
  "Boot Lid": "tailgate",
  "Rear Windscreen": "rear_glass",
  "Left Tail Lamp": "l_taillamp",
  "Right Tail Lamp": "r_taillamp",
  // Its own zone: on the sedan it used to light the FRONT fender's mesh.
  "Left Rear Quarter Panel": "l_quarter",
  "Right Rear Quarter Panel": "r_quarter",
  "Rear Number Plate": "rear_bumper",
  "Rear Bumper Beam": "rear_bumper",
  "Left Rear Bumper Reflector": "rear_bumper",
  "Right Rear Bumper Reflector": "rear_bumper",
  "Rear End Panel": "rear_bumper",
  // Mounted on the boot lid / tailgate, so they mark the tailgate mesh.
  "Boot Lid Lock": "tailgate",
  "Boot Lid Hinge": "tailgate",
  "Boot Lid Weatherstrip": "tailgate",
  "Boot Lid Inner Trim": "tailgate",
  "Left Boot Lid Lamp": "tailgate",
  "Right Boot Lid Lamp": "tailgate",
  // Inside the boot: no mesh of their own.
  "Boot Floor Board": "underbody",
  "Left Boot Side Trim": null,
  "Right Boot Side Trim": null,
  // The rear arch is part of the rear quarter.
  "Left Rear Wheel Arch Liner": "l_quarter",
  "Right Rear Wheel Arch Liner": "r_quarter",
  "Left Rear Wheel Arch Trim": "l_quarter",
  "Right Rear Wheel Arch Trim": "r_quarter",

  "Left Front Wheel": "l_wheel_front",
  "Right Front Wheel": "r_wheel_front",
  "Left Rear Wheel": "l_wheel_rear",
  "Right Rear Wheel": "r_wheel_rear",
  // The wheel's mesh is rim and tyre together.
  "Left Front Tyre": "l_wheel_front",
  "Right Front Tyre": "r_wheel_front",
  "Left Rear Tyre": "l_wheel_rear",
  "Right Rear Tyre": "r_wheel_rear",

  "Underbody / Chassis": "underbody",
  "Left Sill / Rocker Panel": "underbody",
  "Right Sill / Rocker Panel": "underbody",
  "Rear Floor Panel": "underbody",
  "Front Subframe": "underbody",
  "Rear Subframe": "underbody",
  "Engine Undercover": "underbody",

  // Roof mapped to 3D roof zone; critical mechanical mapped to nearest visible anchors
  "Roof": "roof",
  "Radiator": "front_grill",
  "Air Conditioning Condenser": "front_grill",
  "Exhaust System": null,
  "Fuel Tank": null,
  "Suspension - Front": null,
  "Suspension - Rear": null,
  "Steering Assembly": null,
  "Airbag System": null,
  "Parking Sensor": "rear_bumper",
  "Reversing Camera": "rear_bumper",
  "Interior Trim": null,
  "Seat": null,
  "Dashboard": null,

  // Side unknown: every zone it may be (EITHER_SIDE), never the left one alone.
  "Quarter Panel (side undetermined)": "any_quarter",
  "Front Fender (side undetermined)": "any_fender",
  "Door (side undetermined)": "any_door",
  "Door Glass (side undetermined)": "any_door_glass",
  "Headlamp (side undetermined)": "any_headlamp",
  "Tail Lamp (side undetermined)": "any_taillamp",
  "Wing Mirror (side undetermined)": "any_mirror",
  "Wheel (side undetermined)": "any_wheel",
  "Sill / Rocker Panel (side undetermined)": "underbody",
  "Fog Lamp (side undetermined)": "front_bumper",
  "Rear Bumper Reflector (side undetermined)": "rear_bumper",
  "Boot Lid Lamp (side undetermined)": "tailgate",
  "Wheel Arch Liner (side undetermined)": "any_arch",
  "Boot Side Trim (side undetermined)": null,
  "Wheel Arch Trim (side undetermined)": "any_arch",
  "Window Regulator (side undetermined)": "any_door",
  "Tyre (side undetermined)": "any_wheel",

  "Other / Not Listed": null,
};

/** A part whose side (and for doors and wheels, whose end) is unknown: the
 * zones it may be on. The table says "Door (side undetermined)" and the
 * door's place isn't known either (taxonomy _NOT_PLACED / _NEEDS_END). */
const EITHER_SIDE: Record<string, string[]> = {
  any_quarter: ["l_quarter", "r_quarter"],
  any_fender: ["l_fender", "r_fender"],
  any_door: ["l_door_front", "r_door_front", "l_door_rear", "r_door_rear"],
  any_door_glass: ["l_door_glass_front", "r_door_glass_front", "l_door_glass_rear", "r_door_glass_rear"],
  any_headlamp: ["l_headlamp", "r_headlamp"],
  any_taillamp: ["l_taillamp", "r_taillamp"],
  any_mirror: ["l_mirror", "r_mirror"],
  any_wheel: ["l_wheel_front", "r_wheel_front", "l_wheel_rear", "r_wheel_rear"],
  any_arch: ["l_fender", "r_fender", "l_quarter", "r_quarter"],
};

/** Parts drawn on a panel that isn't the part itself: where they really
 * are, so the map can say "inside the door" rather than imply the door
 * skin is damaged. Keyed by canonical part. */
const STAND_INS: Record<string, string> = {
  "Left Front Window Regulator": "inside the door",
  "Right Front Window Regulator": "inside the door",
  "Left Rear Window Regulator": "inside the door",
  "Right Rear Window Regulator": "inside the door",
  "Window Regulator (side undetermined)": "inside a door",
  "Left Front Wheel Arch Liner": "inside the wheel arch",
  "Right Front Wheel Arch Liner": "inside the wheel arch",
  "Left Rear Wheel Arch Liner": "inside the wheel arch",
  "Right Rear Wheel Arch Liner": "inside the wheel arch",
  "Wheel Arch Liner (side undetermined)": "inside a wheel arch",
  "Front Bumper Beam": "behind the bumper",
  "Rear Bumper Beam": "behind the bumper",
  "Rear End Panel": "behind the bumper",
  "Radiator": "behind the grille",
  "Air Conditioning Condenser": "behind the grille",
  "Left Fog Lamp": "in the bumper",
  "Right Fog Lamp": "in the bumper",
  "Fog Lamp (side undetermined)": "in the bumper",
  "Left Rear Bumper Reflector": "in the bumper",
  "Right Rear Bumper Reflector": "in the bumper",
  "Rear Bumper Reflector (side undetermined)": "in the bumper",
  "Parking Sensor": "in the bumper",
  "Reversing Camera": "near the bumper",
  "Boot Floor Board": "inside the boot",
  "Left Sill / Rocker Panel": "below the doors",
  "Right Sill / Rocker Panel": "below the doors",
  "Sill / Rocker Panel (side undetermined)": "below the doors",
  "Rear Floor Panel": "under the car",
  "Front Subframe": "under the car",
  "Rear Subframe": "under the car",
  "Engine Undercover": "under the car",
};

/** True when a part names a panel but leaves the side open. The UI can then
 * prompt for the side rather than just showing a missing marker. */
export function isSideUndetermined(part: string): boolean {
  return part.includes("(side undetermined)");
}

/** True when the name is a canonical part we deliberately have no mesh for,
 * as opposed to one we simply failed to match. Lets the UI distinguish
 * "no 3D geometry exists for this" from "couldn't interpret this". */
export function isKnownUnmappedPart(part: string): boolean {
  return part in CANONICAL_ZONES && CANONICAL_ZONES[part] === null;
}

/** Where a part really is when the map marks a nearby panel for it
 * ("inside the door"), else null. */
export function standInFor(part: string): string | null {
  return STAND_INS[part.trim()] ?? null;
}

const SIDE_LEFT = /\bleft\b|\bl\s*[/.]?\s*h\b|\blh\b/i;
const SIDE_RIGHT = /\bright\b|\br\s*[/.]?\s*h\b|\brh\b/i;
const DEPTH_REAR = /rear|\bback\b/i;

/** The zones one part may be on. `keys` has one zone when the place is
 * known, more when the side (or the end) is not -- then `sideUnknown`. */
export type ZoneMatch = { keys: string[]; sideUnknown: boolean };

const one = (key: string): ZoneMatch => ({ keys: [key], sideUnknown: false });

/** `sideHint` is only ever used when the part's own text doesn't say
 * left/right -- see resolveZones() below for where that hint comes from
 * and why it's safe (only applied when the rest of the same report is
 * unambiguous about which side). Without either, a paired part is on both
 * sides ("?"): it is never put on the left by default. */
export function zoneKeysFor(part: string, sideHint?: "l" | "r"): ZoneMatch | null {
  // Exact canonical match first -- for anything extracted since the
  // vocabulary was introduced this is unambiguous, and it can't be
  // mis-claimed by a broad regex the way "rear subframe" could be caught
  // by the bumper rule.
  const canonical = CANONICAL_ZONES[part.trim()];
  if (canonical !== undefined) {
    if (canonical === null) return null;
    const either = EITHER_SIDE[canonical];
    return either ? { keys: either, sideUnknown: true } : one(canonical);
  }

  const rule = CATEGORY_RULES.find((r) => r.test.test(part));
  if (!rule) return null;
  if (rule.zoneKey) return one(rule.zoneKey);

  let sides: ("l" | "r")[] = [];
  if (rule.paired) {
    if (SIDE_LEFT.test(part)) sides = ["l"];
    else if (SIDE_RIGHT.test(part)) sides = ["r"];
    else if (sideHint) sides = [sideHint];
    else sides = ["l", "r"];
  }
  const depth = rule.depthVar ? (DEPTH_REAR.test(part) ? "rear" : "front") : null;

  if (!sides.length) return one(depth ? `${depth}_${rule.base}` : rule.base!); // front_bumper
  const keys = sides.map((side) => (depth ? `${side}_${rule.base}_${depth}` : `${side}_${rule.base}`)); // l_door_front, l_headlamp
  return { keys, sideUnknown: keys.length > 1 };
}

/** One name for where a part goes: its zone, or for a part whose side is
 * unknown, its possible zones joined ("l_door_front / r_door_front"), which
 * no mesh rule matches -- so a caller that can draw one zone only (the
 * impact sketch) lists the part instead of guessing a side. */
export function zoneKeyFor(part: string, sideHint?: "l" | "r"): string | null {
  const match = zoneKeysFor(part, sideHint);
  return match ? match.keys.join(" / ") : null;
}

export interface ZoneResolution {
  /** Which 3D zone (and therefore which real mesh) this item belongs to.
   * Several items commonly share one key -- a real 18-line rear-collision
   * report puts 9 separate items on `rear_bumper` alone. For a part whose
   * side is unknown it is zoneKeyFor's joined name: see `keys`. */
  key: string;
  /** Every zone the item may be on: one, or both sides when `sideUnknown`. */
  keys: string[];
  sideUnknown: boolean;
  /** 1-based, and deliberately the item's own row number rather than its
   * zone's: badges used to be numbered per-zone, which meant all 9 of
   * those rear-bumper rows showed an identical "01" in the checklist and
   * only one marker ever appeared on the model. Numbering per item makes
   * checklist row N and blueprint marker N the same thing. */
  badgeNumber: number;
}

/** The side most of a report's parts state, when they agree: "l", "r" or
 * undefined for a mix (or none). */
export function dominantSide(parts: string[]): "l" | "r" | undefined {
  let leftCount = 0;
  let rightCount = 0;
  parts.forEach((part) => {
    if (SIDE_LEFT.test(part)) leftCount++;
    else if (SIDE_RIGHT.test(part)) rightCount++;
  });
  return leftCount === rightCount ? undefined : leftCount > rightCount ? "l" : "r";
}

/** Resolves every damage item to a zone + a shared badge number in one
 * pass, so the table and the 3D view can never disagree.
 *
 * Left/right inference: an item with no explicit side in its own text
 * (e.g. a plain "Fender" on a report whose other items say "Right Door")
 * only gets assigned the report's dominant side when that side is
 * unambiguous among the OTHER items that did state one explicitly. A
 * report with a genuine mix (some left, some right) leaves such items on
 * both sides ("?") rather than guessing. */
export function resolveZones(damageEntries: DamageSummaryItem[]): (ZoneResolution | null)[] {
  const hint = dominantSide(damageEntries.map((item) => item.part));
  return damageEntries.map((item, idx) => {
    const match = zoneKeysFor(item.part, hint);
    if (!match) return null;
    return { key: match.keys.join(" / "), keys: match.keys, sideUnknown: match.sideUnknown, badgeNumber: idx + 1 };
  });
}

// ---------------------------------------------------------------------------
// Zones in words, and the areas of the car the map groups them by.

/** Every zone the map knows, in words. */
export const ZONE_LABELS: Record<string, string> = {
  front_bumper: "Front Bumper",
  front_grill: "Front Grille",
  bonnet: "Bonnet",
  windscreen: "Front Windscreen",
  l_headlamp: "Left Headlamp",
  r_headlamp: "Right Headlamp",
  l_fender: "Left Front Fender",
  r_fender: "Right Front Fender",
  l_mirror: "Left Wing Mirror",
  r_mirror: "Right Wing Mirror",
  l_door_front: "Left Front Door",
  r_door_front: "Right Front Door",
  l_door_rear: "Left Rear Door",
  r_door_rear: "Right Rear Door",
  l_door_glass_front: "Left Front Door Glass",
  r_door_glass_front: "Right Front Door Glass",
  l_door_glass_rear: "Left Rear Door Glass",
  r_door_glass_rear: "Right Rear Door Glass",
  l_quarter: "Left Rear Quarter Panel",
  r_quarter: "Right Rear Quarter Panel",
  l_wheel_front: "Left Front Wheel",
  r_wheel_front: "Right Front Wheel",
  l_wheel_rear: "Left Rear Wheel",
  r_wheel_rear: "Right Rear Wheel",
  rear_bumper: "Rear Bumper",
  tailgate: "Boot Lid / Tailgate",
  rear_glass: "Rear Windscreen",
  l_taillamp: "Left Tail Lamp",
  r_taillamp: "Right Tail Lamp",
  roof: "Roof",
  underbody: "Underbody / Chassis",
};

/** The canonical part a person adds when they mark a zone as damaged
 * ("+ Add as damage" on the map's card). */
const ZONE_PARTS: Record<string, string> = {
  ...ZONE_LABELS,
  tailgate: "Boot Lid",
};

export function zoneLabel(zoneKey: string): string {
  return ZONE_LABELS[zoneKey] ?? zoneKey;
}

/** The canonical part for a zone, or null for a zone no part names. */
export function partForZone(zoneKey: string): string | null {
  return ZONE_PARTS[zoneKey] ?? null;
}

/** The map's areas: the camera views and the area chips. "none" holds the
 * parts the model has no place for ("Not on model"). */
export type MapArea = "front" | "rear" | "lh" | "rh" | "top" | "none";
export const MAP_AREAS: MapArea[] = ["front", "rear", "lh", "rh", "top", "none"];
export const AREA_LABELS: Record<MapArea, string> = {
  front: "Front",
  rear: "Rear",
  lh: "LH",
  rh: "RH",
  top: "Top / under",
  none: "Not on model",
};

const FRONT_ZONES = new Set(["front_bumper", "front_grill", "bonnet", "windscreen", "l_headlamp", "r_headlamp"]);
const REAR_ZONES = new Set(["rear_bumper", "tailgate", "rear_glass", "l_taillamp", "r_taillamp"]);
const TOP_ZONES = new Set(["roof", "underbody"]);

/** The area a zone is counted under: lamps and bumpers by their end, the
 * side panels (fenders, doors, quarters, wheels, mirrors) by their side. */
export function zoneArea(zoneKey: string): MapArea {
  if (FRONT_ZONES.has(zoneKey)) return "front";
  if (REAR_ZONES.has(zoneKey)) return "rear";
  if (TOP_ZONES.has(zoneKey)) return "top";
  if (zoneKey.startsWith("l_")) return "lh";
  if (zoneKey.startsWith("r_")) return "rh";
  return "none";
}

/** The sorter's photo areas (bot-service ai/triage.AREAS) -> the map's
 * areas. A corner counts for its end and its side; "interior" for none. */
const PHOTO_AREA_MAP: Record<string, MapArea[]> = {
  front: ["front"],
  rear: ["rear"],
  left: ["lh"],
  right: ["rh"],
  "front-left": ["front", "lh"],
  "front-right": ["front", "rh"],
  "rear-left": ["rear", "lh"],
  "rear-right": ["rear", "rh"],
  roof: ["top"],
  underbody: ["top"],
};

export function photoAreas(area: string | null | undefined): MapArea[] {
  return PHOTO_AREA_MAP[(area || "").trim().toLowerCase()] ?? [];
}

// ---------------------------------------------------------------------------
// Which of the five 3D models a vehicle is drawn with.

export type VehicleArchetype = "suv" | "sedan" | "van" | "mpv" | "hatchback";
export const ARCHETYPES: VehicleArchetype[] = ["sedan", "hatchback", "suv", "mpv", "van"];
export const ARCHETYPE_LABELS: Record<VehicleArchetype, string> = {
  sedan: "Sedan",
  hatchback: "Hatchback",
  suv: "SUV",
  mpv: "MPV",
  van: "Van",
};

/** The model for a vehicle: its stated body type (taxonomy BODY_TYPES, or
 * one of the archetypes), else a guess from the make/model text, else the
 * sedan. */
export function detectBodyType(vehicleText: string, statedBodyType?: string | null): VehicleArchetype {
  const norm = (statedBodyType || "").trim().toLowerCase();
  if (norm === "suv") return "suv";
  if (norm === "mpv") return "mpv";
  if (norm === "van" || norm === "commercial van") return "van";
  if (norm === "hatchback") return "hatchback";
  if (norm === "sedan") return "sedan";

  const lower = (vehicleText || "").toLowerCase();
  if (/\b(vezel|hr-v|hrv|cr-v|crv|rav4|harrier|forester|qashqai|yaris cross|tucson|cx-3|cx-5|cx-30|suv|crossover)\b/i.test(lower)) return "suv";
  if (/\b(alphard|vellfire|sienta|noah|voxy|serena|stepwgn|odyssey|spade|roomy|carnival|mpv|minivan)\b/i.test(lower)) return "mpv";
  if (/\b(hiace|nv200|nv350|urvan|starex|transporter|caravelle|kombi|van|lorry)\b/i.test(lower)) return "van";
  if (/\b(jazz|fit|swift|golf|polo|march|note|yaris|hatchback)\b/i.test(lower)) return "hatchback";
  return "sedan";
}
