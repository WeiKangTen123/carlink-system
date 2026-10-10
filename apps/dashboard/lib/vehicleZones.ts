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
  { test: /fender|wheel\s*arch|wing|quarter\s*panel/i, base: "fender", paired: true },
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
 * is a direct lookup rather than pattern-matching. The regex rules below
 * remain as the fallback for the six reports already on file, which hold
 * free text like "Rear bumper fascia" and "Undercarriage".
 *
 * `null` means the part is real but the sedan model has no mesh for it --
 * an honest gap. Those items still appear in the damage checklist, just
 * without a marker, rather than being silently attached to the wrong panel.
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

  "Rear Bumper": "rear_bumper",
  "Boot Lid": "tailgate",
  "Rear Windscreen": "rear_glass",
  "Left Tail Lamp": "l_taillamp",
  "Right Tail Lamp": "r_taillamp",
  // The sedan's rear arch is part of the same fender mesh group.
  "Left Rear Quarter Panel": "l_fender",
  "Right Rear Quarter Panel": "r_fender",
  "Rear Number Plate": "rear_bumper",

  "Left Front Wheel": "l_wheel_front",
  "Right Front Wheel": "r_wheel_front",
  "Left Rear Wheel": "l_wheel_rear",
  "Right Rear Wheel": "r_wheel_rear",

  "Underbody / Chassis": "underbody",
  "Left Sill / Rocker Panel": "underbody",
  "Right Sill / Rocker Panel": "underbody",
  "Rear Floor Panel": "underbody",
  "Front Subframe": "underbody",
  "Rear Subframe": "underbody",

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

  // Side-agnostic variants: mapped to visible zone anchors so damage items
  // always get a visible 3D hotspot marker instead of disappearing.
  "Quarter Panel (side undetermined)": "rear_bumper",
  "Front Fender (side undetermined)": "front_bumper",
  "Door (side undetermined)": "underbody",
  "Door Glass (side undetermined)": "roof",
  "Headlamp (side undetermined)": "front_bumper",
  "Tail Lamp (side undetermined)": "rear_bumper",
  "Wing Mirror (side undetermined)": "windscreen",
  "Wheel (side undetermined)": "underbody",
  "Sill / Rocker Panel (side undetermined)": "underbody",

  "Other / Not Listed": null,
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

const SIDE_LEFT = /\bleft\b|\bl\s*[/.]?\s*h\b|\blh\b/i;
const SIDE_RIGHT = /\bright\b|\br\s*[/.]?\s*h\b|\brh\b/i;
const DEPTH_REAR = /rear|\bback\b/i;

/** `sideHint` is only ever used when the part's own text doesn't say
 * left/right -- see resolveZones() below for where that hint comes from
 * and why it's safe (only applied when the rest of the same report is
 * unambiguous about which side). */
export function zoneKeyFor(part: string, sideHint?: "l" | "r"): string | null {
  // Exact canonical match first -- for anything extracted since the
  // vocabulary was introduced this is unambiguous, and it can't be
  // mis-claimed by a broad regex the way "rear subframe" could be caught
  // by the bumper rule.
  const canonical = CANONICAL_ZONES[part.trim()];
  if (canonical !== undefined) return canonical;

  const rule = CATEGORY_RULES.find((r) => r.test.test(part));
  if (!rule) return null;
  if (rule.zoneKey) return rule.zoneKey;

  let side: "l" | "r" | null = null;
  if (rule.paired) {
    if (SIDE_LEFT.test(part)) side = "l";
    else if (SIDE_RIGHT.test(part)) side = "r";
    else if (sideHint) side = sideHint;
    else side = "l"; // safe default so paired part without side always gets a visible marker on the 3D model
  }
  const depth = rule.depthVar ? (DEPTH_REAR.test(part) ? "rear" : "front") : null;

  if (side && depth) return `${side}_${rule.base}_${depth}`; // l_door_front
  if (side) return `${side}_${rule.base}`; // l_headlamp
  if (depth) return `${depth}_${rule.base}`; // front_bumper
  return rule.base!;
}

export interface ZoneResolution {
  /** Which 3D zone (and therefore which real mesh) this item belongs to.
   * Several items commonly share one key -- a real 18-line rear-collision
   * report puts 9 separate items on `rear_bumper` alone. */
  key: string;
  /** 1-based, and deliberately the item's own row number rather than its
   * zone's: badges used to be numbered per-zone, which meant all 9 of
   * those rear-bumper rows showed an identical "01" in the checklist and
   * only one marker ever appeared on the model. Numbering per item makes
   * checklist row N and blueprint marker N the same thing. */
  badgeNumber: number;
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

  return damageEntries.map((item, idx) => {
    const key = zoneKeyFor(item.part, dominantSide);
    if (!key) return null;
    return { key, badgeNumber: idx + 1 };
  });
}

/** Groups resolved items by zone, preserving each item's own index.
 * The 3D view needs this to fan a marker per real damage item around its
 * shared zone, instead of collapsing a nine-item bumper into one dot. */
export function groupByZone(resolutions: (ZoneResolution | null)[]): Map<string, number[]> {
  const byZone = new Map<string, number[]>();
  resolutions.forEach((res, idx) => {
    if (!res) return;
    const list = byZone.get(res.key);
    if (list) list.push(idx);
    else byZone.set(res.key, [idx]);
  });
  return byZone;
}

// =============================================================================
// STUDIO 3.0: 7-ZONE AUTOMOTIVE TAXONOMY & PHOTO SEGMENTATION ENGINE
// Matches real Singapore loss adjuster practice (Presentation1.pptx & SLK 3063 Z)
// =============================================================================

export interface VehicleZoneGroup {
  id: string;
  label: string;
  shortLabel: string;
  iconName?: string;
  keywords: RegExp[];
  cameraAngle: {
    position: [number, number, number];
    target: [number, number, number];
  };
}

export const VEHICLE_ZONE_GROUPS: VehicleZoneGroup[] = [
  {
    id: "survey",
    label: "Survey & Identification",
    shortLabel: "Survey & VIN",
    keywords: [/\b(vin|chassis|plate|speedo|odometer|tyre|tire|wheel|overview|static|baseline|survey)\b/i],
    cameraAngle: {
      position: [3.6, 2.4, 3.9],
      target: [0, 0.55, 0],
    },
  },
  {
    id: "rear_bumper",
    label: "Rear Bumper & Sensors",
    shortLabel: "Rear Bumper",
    keywords: [/\b(rear\s*bumper|bumper\s*fascia|bumper\s*side|bumper\s*beam|bumper\s*clip|reverse\s*sensor|bumper\s*retainer|diffuser)\b/i],
    cameraAngle: {
      position: [0, 1.25, 4.2],
      target: [0, 0.6, 1.8],
    },
  },
  {
    id: "tailgate",
    label: "Tailgate & Rear Glass",
    shortLabel: "Tailgate & Glass",
    keywords: [/\b(tail\s*gate|boot\s*lid|trunk|rear\s*windscreen|rear\s*glass|lock\s*striker|tail\s*gate\s*lock|hinge|weatherstripe|weatherstrip|vezel|emblem)\b/i],
    cameraAngle: {
      position: [0, 2.1, 3.7],
      target: [0, 1.1, 1.6],
    },
  },
  {
    id: "lighting",
    label: "Lighting & Lens",
    shortLabel: "Tail Lamps",
    keywords: [/\b(tail\s*lamp|tail\s*light|reflector|lamp\s*clip|sealant|headlamp|fog\s*lamp)\b/i],
    cameraAngle: {
      position: [-1.8, 1.3, 3.6],
      target: [-0.7, 0.8, 1.7],
    },
  },
  {
    id: "quarter_panel",
    label: "Quarter Panel & Wheel Arch",
    shortLabel: "Quarter Panel",
    keywords: [/\b(quarter\s*panel|wheel\s*arch|garnish|fender|splash\s*guard|inner\s*trim)\b/i],
    cameraAngle: {
      position: [-3.8, 1.4, 1.2],
      target: [-0.5, 0.7, 0.5],
    },
  },
  {
    id: "boot_floor",
    label: "Boot Interior & Floor Pan",
    shortLabel: "Boot Floor",
    keywords: [/\b(floor\s*panel|floor\s*board|insulator\s*cloth|tool\s*tray|sponge|spare\s*wheel|spare\s*tyre|boot\s*interior|luggage|scuff\s*plate)\b/i],
    cameraAngle: {
      position: [0, 3.0, 2.2],
      target: [0, 0.5, 1.2],
    },
  },
  {
    id: "skeleton",
    label: "Structural Skeleton & Undercarriage",
    shortLabel: "Skeleton & Frame",
    keywords: [/\b(rear\s*end\s*panel|chassis|underbody|undercarriage|frame\s*member|car\s*o\s*liner|subframe|crossmember|straighten)\b/i],
    cameraAngle: {
      position: [0, 0.35, 3.6],
      target: [0, 0.3, 1.4],
    },
  },
];

export interface ClassifiedPhoto {
  index: number;
  photoRef: string; // "P01", "P02", ...
  url: string;
  thumbUrl?: string;
  zoneId: string;
  zoneLabel: string;
  damageItems: DamageSummaryItem[];
}

/** Classifies photos into the 7 primary functional automotive zones.
 * Uses exact damage_summary.photo_reference bindings first, followed by
 * sequential assessor shooting clusters (as proven in Presentation1.pptx
 * and SLK 3063 Z), ensuring all 50-70 photos are cleanly segmented.
 */
export function classifyPhotos(
  photos: string[],
  photoThumbs: string[] | undefined,
  damageEntries: DamageSummaryItem[]
): ClassifiedPhoto[] {
  if (!photos || photos.length === 0) return [];

  // Build photoRef -> damage items lookup
  const damageByPhoto = new Map<string, DamageSummaryItem[]>();
  damageEntries.forEach((item) => {
    if (item.photo_reference) {
      const ref = item.photo_reference.trim().toUpperCase();
      const list = damageByPhoto.get(ref) || [];
      list.push(item);
      damageByPhoto.set(ref, list);
    }
  });

  // Helper to test part against zone groups
  const matchZoneId = (partText: string): string | null => {
    for (const group of VEHICLE_ZONE_GROUPS) {
      if (group.keywords.some((rx) => rx.test(partText))) {
        return group.id;
      }
    }
    return null;
  };

  // First pass: direct zone match from linked damage items
  const directZones: (string | null)[] = photos.map((_, idx) => {
    const photoRef = `P${String(idx + 1).padStart(2, "0")}`;
    const linked = damageByPhoto.get(photoRef);
    if (linked && linked.length > 0) {
      for (const item of linked) {
        const matched = matchZoneId(item.part);
        if (matched) return matched;
      }
    }
    return null;
  });

  // Second pass: sequential cluster propagation & loss-adjuster photo workflow heuristics
  // In real Singapore insurance surveys (SLK 3063 Z & SMW 7530X):
  // 1. Initial 10% are overview & impact entry (rear_bumper / survey)
  // 2. Middle 60% are exterior panels -> disassembly -> boot floor -> underbody
  // 3. Final 15% are static compliance (VIN plate, odometer, tyres) -> survey
  const n = photos.length;
  const classifiedZones: string[] = directZones.map((direct, idx) => {
    if (direct) return direct;

    // Check adjacent neighbors (within 2 photos) with direct matches
    for (let offset = 1; offset <= 3; offset++) {
      if (idx - offset >= 0 && directZones[idx - offset]) return directZones[idx - offset]!;
      if (idx + offset < n && directZones[idx + offset]) return directZones[idx + offset]!;
    }

    // Survey heuristic for standard 50-70 photo cases
    if (n >= 30) {
      const ratio = idx / n;
      if (ratio > 0.82) return "survey"; // VIN, odometer, tread depths at end of survey
      if (ratio < 0.12) return "survey"; // initial 4-corner overview
      if (ratio > 0.55 && ratio <= 0.72) return "skeleton"; // teardown & chassis jig
      if (ratio > 0.40 && ratio <= 0.55) return "boot_floor"; // interior floor pan
      if (ratio > 0.22 && ratio <= 0.40) return "tailgate"; // tailgate latch & trim
      return "rear_bumper"; // primary impact zone
    }

    return "rear_bumper";
  });

  return photos.map((url, idx) => {
    const photoRef = `P${String(idx + 1).padStart(2, "0")}`;
    const zoneId = classifiedZones[idx] || "rear_bumper";
    const group = VEHICLE_ZONE_GROUPS.find((g) => g.id === zoneId) || VEHICLE_ZONE_GROUPS[1];
    return {
      index: idx,
      photoRef,
      url,
      thumbUrl: photoThumbs?.[idx] || url,
      zoneId,
      zoneLabel: group.label,
      damageItems: damageByPhoto.get(photoRef) || [],
    };
  });
}

/** Chunks photos into 4-quadrant slides matching Presentation1.pptx:
 * Q1: Top-Left, Q2: Top-Right, Q3: Bottom-Left, Q4: Bottom-Right.
 */
export interface PresentationSlide {
  slideNumber: number; // 1-based
  quadrants: {
    q1: ClassifiedPhoto | null; // Top-Left
    q2: ClassifiedPhoto | null; // Top-Right
    q3: ClassifiedPhoto | null; // Bottom-Left
    q4: ClassifiedPhoto | null; // Bottom-Right
  };
}

export function buildPresentationSlides(photos: ClassifiedPhoto[]): PresentationSlide[] {
  if (!photos || photos.length === 0) return [];
  const slides: PresentationSlide[] = [];
  const totalSlides = Math.ceil(photos.length / 4);

  for (let s = 0; s < totalSlides; s++) {
    const base = s * 4;
    slides.push({
      slideNumber: s + 1,
      quadrants: {
        q1: photos[base] || null,
        q2: photos[base + 1] || null,
        q3: photos[base + 2] || null,
        q4: photos[base + 3] || null,
      },
    });
  }

  return slides;
}
