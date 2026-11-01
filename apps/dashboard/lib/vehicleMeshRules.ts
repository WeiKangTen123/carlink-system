/**
 * Damage-zone key -> real mesh names inside the five vehicle GLBs
 * (public/assets/vehicles/{sedan,suv,hatchback,mpv,van}.v2.glb), plus the
 * interior / mechanical clutter the blueprint hides.
 *
 * Pure data and a few tiny matchers, deliberately free of three.js, enums,
 * namespaces and parameter properties so Node (built-in type stripping) can
 * load this file directly:
 *
 *     node apps/dashboard/scripts/check-vehicle-mesh-rules.mjs
 *
 * Run that after swapping, re-exporting or re-optimising a model: it rebuilds
 * the runtime names from each GLB and prints what every zone resolves to,
 * what is hidden, what collides and which zones fall back to anchors.
 *
 * HOW NAMES LOOK AT RUNTIME (three.js GLTFLoader, r185)
 *  - Every node name goes through PropertyBinding.sanitizeNodeName:
 *    whitespace -> "_", and the characters  [ ] . : /  are REMOVED.
 *      "Name-Wheel.Ft.L_123"        -> "Name-WheelFtL_123"
 *      "hiace_wheel.smd.001"        -> "hiace_wheelsmd001"
 *      "Honda:SM_Bumper_F_101_..."  -> "HondaSM_Bumper_F_101_..."
 *  - A node whose mesh has one primitive becomes a THREE.Mesh carrying the
 *    node's name. A multi-primitive mesh becomes a Group with the node's name
 *    whose Mesh children carry the glTF *mesh* name (or "mesh_<index>" when
 *    the mesh is unnamed, as in the SUV), then "_1", "_2", ...
 *  - The component tests every rule against the three names
 *    [mesh.name, mesh.parent.name, mesh.parent.parent.name] ("" when absent).
 *
 * WHERE THE DESCRIPTIVE NAME LIVES, PER MODEL
 *  sedan     Sketchfab export: meshes are "Object_N" under descriptive wrapper
 *            groups ("bumper-front_8", "door-front-l_16", "Name-WheelFtL_123")
 *            -> rules hit the PARENT (wheels: the GRANDPARENT).
 *  suv       96 descriptively named nodes ("bumper-front", "door-rear-left",
 *            "wheel-fl") under one unnamed root. 12 of them (lamps, mirrors)
 *            are multi-primitive -> children "mesh_N", "mesh_N_1"... and the
 *            rule hits the PARENT. Its glTF meshes carry no names at all.
 *  hatchback Sketchfab Honda export. Each part is a group such as
 *            "HondaSM_Bumper_F_101_SM_Bumper_F_101_MI_Exhaust_037" holding one
 *            mesh per material, named "<group>_<material>_0". The trailing
 *            material says what the sub-mesh is:
 *              _HondaMI_1089010001_151_0   body paint
 *              _HondaMI_Exhaust1_0         dark trim / plastic / metal
 *              _HondaMI_Glass1_0           glass      _HondaMI_Light1_0 lamp
 *              _Red_glass_0, _orange_glass_0  lenses  _HondaMI_Mirror1_0 mirror
 *              _HondaMI_Cabin1_0, _HondaMI_Cabin_Leather_A1_0,
 *              _HondaMI_Cabin_Plastic_A1_0, _HondaMI_Dashboard1_0,
 *              _HondaMI_Grid_A1_0          interior (hidden below)
 *            Rules match the OWN name by group prefix (/^HondaSM_Bumper_F_/)
 *            and, where one group mixes zones (door skin vs door glass,
 *            tailgate vs rear glass), by material suffix. Tyres, rims and
 *            brakes sit under the groups WL_1, WL_2, WR_1, WR_2 (parent or
 *            grandparent). Front/rear lamps and both fender groups are ONE
 *            mesh for left+right, so they stay unmapped (anchor fallback)
 *            rather than lighting both sides for a one-sided repair.
 *  mpv       432 root-level meshes, nearly all generically named
 *            ("G-__584454", "Entity97", "Mesh84"). Only bonnet, front-glas,
 *            rear-bumpe, aero-intak, chapa (rear plate) and the four wheels
 *            t01, t01001, t01002, t01003 can be mapped.
 *  van       Skinned Hiace: hiacesmd (the WHOLE body in one mesh) and
 *            bumperssmd + bumpers_blacksmd (front AND rear bumper in one
 *            mesh) are left unmapped on purpose; grillsmd / grill_blacksmd /
 *            grill_chromesmd and hiace_wheelsmd(001..003) are mapped.
 *            Parents are the armatures "hiace_ARM" / "hiace_wheel_ARM".
 *
 * AXES: in every model the car's front is +Z and its LEFT side is +X (the raw
 * van faces +X and is rotated by the component so the same holds). Every
 * left/right and front/rear assignment below was checked against the meshes'
 * world-space bounding boxes (the hatchback's W*_N and the MPV's t01* wheel
 * numbering is not what you would guess).
 *
 * The zone KEYS are shared with lib/vehicleZones.ts (resolveZones) and must
 * not change. A mesh gets the FIRST zone in table order whose rules match any
 * of its three names, so order matters where one group holds two zones (the
 * hatchback tailgate's glass is claimed by rear_glass, listed before tailgate).
 */

export type MeshRule = { key: string; label: string; match: RegExp[] };

/**
 * Interior and mechanical clutter the blueprint hides. Tested against own /
 * parent / grandparent names, so every term must be specific enough never to
 * touch an exterior panel in any of the five models.
 */
const EXCLUDE_TERMS: string[] = [
  // --- sedan (the original list). "engine" and "frame-*" are now anchored:
  //     the hatchback hood's node names contain "MI_Engine_00N" and the SUV
  //     has "headlight-frame-left" / "glass-rear-frame-left".
  "interior",
  "dashboard",
  "seat",
  "steering-wheel",
  "column-wheel",
  "center-console",
  "^engine-", // sedan engine-parts / -protector / -transmission, suv engine-low
  "escape-pipes",
  "radiator",
  "brake-",
  "shock-absorber",
  "spring", // sedan spring_74.., suv spring-damper-*
  "suspension-",
  "^frame-(?:front|middle|rear)", // sedan underbody frames (still mapped to underbody below)
  "roof-pillars-interior",
  // --- suv ---
  "^door-panel-", // interior door cards
  "^floor-carpet$",
  "^pedals$",
  "^rearview-mirror-intern$",
  "^exhaust-pipe$",
  "^roof-lining-", // headliner + sunroof blind
  "^trunk-panel$", // tailgate inner skin: same outline as "trunk", 2% further inboard
  "^trunk-lid-cover$", // horizontal parcel-shelf / cargo cover behind the rear seat
  "^chassis-mechanics-floor$", // underbody: hidden like the sedan frames, still mapped to underbody below
  // --- hatchback ---
  "^HondaSM_Engine_",
  "^HondaSK_Exhaust_",
  "_HondaMI_Cabin(?:_Leather_A|_Plastic_A)?1_0$", // seats, carpet, door cards, tailgate trim
  "_HondaMI_Dashboard1_0$",
  "_HondaMI_Grid_A1_0$", // speaker grilles on the door cards / horn pad
  "_HondaMI_ChangeA_Brake1_0$", // brake discs + calipers inside the rims
  "^HondaSM_Body_101_.*_HondaMI_Mirror1_0$", // interior rear-view mirror (the side mirrors' glass lives under SideMirror_L/R)
  // --- mpv ---
  "^3DGeom~2[56]_Defintion$", // interior rear-view mirror (housing + glass) hanging inside the windscreen top
];

export const CAR_EXCLUDE: RegExp = new RegExp(EXCLUDE_TERMS.join("|"), "i");

export const CAR_PARTS_REAL: MeshRule[] = [
  {
    key: "front_bumper",
    label: "Front Bumper",
    match: [
      /^bumper-front(?:_\d+)?$/i, // sedan bumper-front_8, suv bumper-front
      /^bumper-front-drl-(?:left|right)$/i, // suv DRL strips set low in the bumper (fog-lamp position)
      /^HondaSM_Bumper_F_/i, // hatchback
      // van: bumpers.smd / bumpers_black.smd hold the front AND rear bumper in
      // one mesh, so they stay unmapped (anchor fallback) -- lighting both ends
      // for a one-end repair, and zooming to their centre, would both mislead.
    ],
  },
  {
    key: "rear_bumper",
    label: "Rear Bumper",
    match: [
      /^bumper-rear(?:_\d+)?$/i, // sedan bumper-rear_10, suv bumper-rear
      /^HondaSM_Bumper_B_/i, // hatchback
      /^HondaSM_Diffuser_B_/i, // hatchback lower diffuser strip under the bumper
      /^rear-bumpe$/i, // mpv (name truncated by its exporter)
      /^chapa$/i, // mpv rear number plate (vehicleZones puts plates on the bumper)
    ],
  },
  {
    key: "bonnet",
    label: "Bonnet/Hood",
    match: [
      /^hood(?:_\d+)?$/i, // sedan hood_60, suv hood, hatchback group "hood"
      /^HondaSK_Hood_/i, // hatchback
      /^bonnet$/i, // mpv
    ],
  },
  {
    key: "windscreen",
    label: "Windscreen",
    match: [
      /^windshield/i, // sedan windshield_102 + windshield-grill_118 (cowl) + the "Windshield_Wiper_-_..." groups
      /^glass-windshield$/i, // suv
      /^hood-grill$/i, // suv cowl vent at the windscreen base (same spot as the sedan's windshield-grill)
      /^wiper-(?:left|right)$/i, // suv
      /^HondaSM_Body_101_SM_Body_101_MI_Glass_HondaMI_Glass1_0$/i, // hatchback fixed glass: windscreen + quarter lights in one mesh
      /^front-glas$/i, // mpv
    ],
  },
  {
    key: "roof",
    label: "Roof",
    match: [
      /^sedan-unibody_\d+$/i, // sedan: the body shell is the only mesh that contains the roof
      /^chassis-sunroof$/i, // suv: likewise the whole body shell
      /^glass-sunroof$/i, // suv
      /^roof$/i, // hatchback wrapper group
      /^HondaSM_Roof_/i, // hatchback roof panel, sunroof glass, fin antenna
    ],
  },
  {
    key: "rear_glass",
    label: "Rear Glass",
    match: [
      /^glass-rear/i, // sedan glass-rear_42, suv glass-rear + glass-rear-frame-left/right
      /^wiper-rear$/i, // suv
      /^HondaSK_Trunk_.*_HondaMI_Glass1_0$/i, // hatchback: the rear window is part of the tailgate group (listed before tailgate on purpose)
    ],
  },
  {
    key: "tailgate",
    label: "Trunk / Tailgate",
    match: [
      /^trunk(?:_\d+)?$/i, // sedan trunk_99, suv trunk, hatchback group "trunk"
      /^rear-splitter$/i, // suv roof spoiler on the tailgate top
      /^taillight-trunk-(?:back|cover)$/i, // suv full-width light bar across the tailgate (both sides in one mesh)
      /^HondaSK_Trunk_/i, // hatchback (its two glass sub-meshes were taken by rear_glass above)
      /^HondaSK_Spoiler_/i, // hatchback tailgate spoiler
    ],
  },
  {
    key: "l_headlamp",
    label: "Left Headlamp",
    match: [
      /^headlights?-[a-z-]+-l_\d+$/i, // sedan headlights-frame-l_52, headlight-projector-l_43, headlights-drl-l_50 ...
      /^headlight-[a-z]+-left$/i, // suv headlight-lens/-frame/-led-left
    ],
  },
  {
    key: "r_headlamp",
    label: "Right Headlamp",
    match: [
      /^headlights?-[a-z-]+-r_\d+$/i, // sedan
      /^headlights-drl001_\d+$/i, // sedan: the right DRL lost its side suffix ("headlights-drl.001"), verified at -X
      /^headlight-[a-z]+-right$/i, // suv
    ],
  },
  {
    key: "l_taillamp",
    label: "Left Taillamp",
    match: [
      /^taillight-[a-z0-9-]+-l_\d+$/i, // sedan taillight-lens-l_92, taillight-back-01-l_81, taillight-lens-trunk-l_90 ...
      /^taillight-(?:body|trunk)-[a-z]+-left$/i, // suv taillight-body-led/-cover/-back-left + taillight-trunk-led-left
    ],
  },
  {
    key: "r_taillamp",
    label: "Right Taillamp",
    match: [/^taillight-[a-z0-9-]+-r_\d+$/i, /^taillight-(?:body|trunk)-[a-z]+-right$/i],
  },
  {
    key: "l_door_front",
    label: "Left Front Door",
    match: [
      /^door-front-l_\d+$/i, // sedan
      /^door-front-left$/i, // suv
      /^HondaSK_Door_FL_.*_HondaMI_(?:1089010001_151|Exhaust1)_0$/i, // hatchback door skin + window frame (glass and door card are separate sub-meshes)
    ],
  },
  {
    key: "r_door_front",
    label: "Right Front Door",
    match: [/^door-front-r_\d+$/i, /^door-front-right$/i, /^HondaSK_Door_FR_.*_HondaMI_(?:1089010001_151|Exhaust1)_0$/i],
  },
  {
    key: "l_door_rear",
    label: "Left Rear Door",
    match: [/^door-rear-l_\d+$/i, /^door-rear-left$/i, /^HondaSK_Door_BL_.*_HondaMI_(?:1089010001_151|Exhaust1)_0$/i],
  },
  {
    key: "r_door_rear",
    label: "Right Rear Door",
    match: [/^door-rear-r_\d+$/i, /^door-rear-right$/i, /^HondaSK_Door_BR_.*_HondaMI_(?:1089010001_151|Exhaust1)_0$/i],
  },
  {
    key: "l_fender",
    label: "Left Fender",
    match: [
      /^fender-front-l_\d+$/i, // sedan
      /^wheel-fender-l_\d+$/i, // sedan front wheel-arch liner
      /^fender-(?:front|rear)-(?:arch-)?left$/i, // suv fender-front-left, fender-front-arch-left, fender-rear-arch-left (rear quarter -> fender, as in vehicleZones)
    ],
  },
  {
    key: "r_fender",
    label: "Right Fender",
    match: [/^fender-front-r_\d+$/i, /^wheel-fender-r_\d+$/i, /^fender-(?:front|rear)-(?:arch-)?right$/i],
  },
  {
    key: "l_mirror",
    label: "Left Mirror",
    match: [
      /^side-mirror-(?:trim-)?l_\d+$/i, // sedan
      /^mirror-left$/i, // suv
      /^HondaSM_SideMirror_L_/i, // hatchback
    ],
  },
  {
    key: "r_mirror",
    label: "Right Mirror",
    match: [/^side-mirror-(?:trim-)?r_\d+$/i, /^mirror-right$/i, /^HondaSM_SideMirror_R_/i],
  },
  {
    key: "front_grill",
    label: "Front Grille",
    match: [
      /^front-grill/i, // sedan front-grill_37, suv front-grille + front-grill-frame
      /^honeycomb-grill/i, // sedan upper + lower honeycomb inserts
      /^bumper-front-grill/i, // sedan bumper-front-grill-frame_9, suv bumper-front-grille (lower grille set in the bumper)
      /^grill(?:_black|_chrome)?smd$/i, // van grill.smd / grill_black.smd / grill_chrome.smd
      /^aero-intak$/i, // mpv lower air intake
    ],
  },
  {
    key: "l_door_glass_front",
    label: "Left Front Door Glass",
    match: [
      /^door-front-window-glass-l_\d+$/i, // sedan
      /^glass-door-front-left$/i, // suv
      /^HondaSK_Door_FL_.*_HondaMI_Glass1_0$/i, // hatchback
    ],
  },
  {
    key: "r_door_glass_front",
    label: "Right Front Door Glass",
    match: [/^door-front-window-glass-r_\d+$/i, /^glass-door-front-right$/i, /^HondaSK_Door_FR_.*_HondaMI_Glass1_0$/i],
  },
  {
    key: "l_door_glass_rear",
    label: "Left Rear Door Glass",
    match: [
      /^door-rear-(?:window|trim)-glass-l_\d+$/i, // sedan door-rear-window-glass-l_26 + door-rear-trim-glass-l_24 (quarter light)
      /^glass-door-rear-(?:column-)?left$/i, // suv glass-door-rear-left + glass-door-rear-column-left (quarter light)
      /^HondaSK_Door_BL_.*_HondaMI_Glass1_0$/i, // hatchback
    ],
  },
  {
    key: "r_door_glass_rear",
    label: "Right Rear Door Glass",
    match: [/^door-rear-(?:window|trim)-glass-r_\d+$/i, /^glass-door-rear-(?:column-)?right$/i, /^HondaSK_Door_BR_.*_HondaMI_Glass1_0$/i],
  },
  {
    key: "underbody",
    label: "Underbody / Chassis",
    match: [
      /^frame-(?:front|middle|rear)_\d+$/i, // sedan (hidden by CAR_EXCLUDE, still the underbody's mesh)
      /^chassis-mechanics-floor$/i, // suv (likewise)
    ],
  },
  // Wheels. Sedan: the corner is on the GRANDPARENT group "Name-Wheel.Ft.L"
  // -> "Name-WheelFtL_123" at runtime. Hatchback: WL_2/WR_1 are the FRONT
  // wheels, WL_1/WR_2 the rear (checked by position). MPV: t01 = right front,
  // t01.001 = left front, t01.002 = left rear, t01.003 = right rear. Van (after
  // the component's rotation): hiace_wheel.smd = LF, .001 = RF, .002 = LR, .003 = RR.
  {
    key: "l_wheel_front",
    label: "Left Front Wheel",
    match: [/^Name-WheelFtL_\d+$/i, /^wheel-fl$/i, /^tire-low-fl$/i, /^WL_2$/i, /^t01001$/i, /^hiace_wheelsmd$/i],
  },
  {
    key: "r_wheel_front",
    label: "Right Front Wheel",
    match: [/^Name-WheelFtR_\d+$/i, /^wheel-fr$/i, /^tire-low-fr$/i, /^WR_1$/i, /^t01$/i, /^hiace_wheelsmd001$/i],
  },
  {
    key: "l_wheel_rear",
    label: "Left Rear Wheel",
    match: [/^Name-WheelBkL_\d+$/i, /^wheel-rl$/i, /^tire-low-rl$/i, /^WL_1$/i, /^t01002$/i, /^hiace_wheelsmd002$/i],
  },
  {
    key: "r_wheel_rear",
    label: "Right Rear Wheel",
    match: [/^Name-WheelBkR_\d+$/i, /^wheel-rr$/i, /^tire-low-rr$/i, /^WR_2$/i, /^t01003$/i, /^hiace_wheelsmd003$/i],
  },
];

/** True when any pattern matches any of the (non-empty) names. */
export function meshNamesMatch(names: string[], patterns: RegExp[]): boolean {
  for (const re of patterns) {
    for (const name of names) {
      if (name && re.test(name)) return true;
    }
  }
  return false;
}

/** True when the mesh (or its parent / grandparent) is interior or mechanical clutter. */
export function isExcludedMesh(names: string[]): boolean {
  for (const name of names) {
    if (name && CAR_EXCLUDE.test(name)) return true;
  }
  return false;
}

/** First zone in table order whose rules match any of the names, or null. */
export function zoneForMeshNames(names: string[]): string | null {
  for (const rule of CAR_PARTS_REAL) {
    if (meshNamesMatch(names, rule.match)) return rule.key;
  }
  return null;
}

/** Every zone whose rules match (table order). More than one = a collision; the first wins at runtime. */
export function zonesForMeshNames(names: string[]): string[] {
  const zones: string[] = [];
  for (const rule of CAR_PARTS_REAL) {
    if (meshNamesMatch(names, rule.match)) zones.push(rule.key);
  }
  return zones;
}
