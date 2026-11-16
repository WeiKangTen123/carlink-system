#!/usr/bin/env node
/**
 * Checks lib/vehicleMeshRules.ts against the real vehicle GLBs.
 *
 *   node apps/dashboard/scripts/check-vehicle-mesh-rules.mjs            # all five models
 *   node apps/dashboard/scripts/check-vehicle-mesh-rules.mjs suv van    # a subset
 *   node apps/dashboard/scripts/check-vehicle-mesh-rules.mjs --all      # never truncate the unmapped list
 *   node apps/dashboard/scripts/check-vehicle-mesh-rules.mjs --names    # also dump every mesh's runtime [own | parent | grandparent]
 *
 * For each public/assets/vehicles/<model>.v2.glb it parses the JSON chunk
 * (no geometry decoding, no dependencies), rebuilds the names three.js's
 * GLTFLoader (r185) gives every Mesh and its parent / grandparent, and prints
 * per model: each zone -> the meshes it resolves to, the meshes hidden by
 * CAR_EXCLUDE, meshes claimed by more than one zone (the first wins at
 * runtime), zones with no mesh at all (they fall back to ZONE_ANCHORS), and
 * finally the rules that matched nothing in any model.
 *
 * Run it after swapping, re-exporting or re-optimising a model, then fix the
 * rules until every exterior panel lands in the right zone. Needs Node with
 * built-in type stripping (22.18+ / 24) to import the .ts rules file.
 *
 * Then it CHECKS (exit 1 on a failure), from the accessors' min/max and the
 * node transforms (still no geometry decoding):
 *  - left is +X: every l_ zone's meshes sit on the car's +X half and every r_
 *    zone's on the -X half, and every l_ / r_ anchor likewise (the 3D map
 *    shows a Left Front Door on the screen's right in the front view);
 *  - front is +Z: bumpers, lamps, wheels, doors, fenders and quarters sit at
 *    the right end;
 *  - the rear quarter has its own zone and no front-fender rule takes a
 *    rear mesh (the sedan's quarter used to light the front fender);
 *  - the roof rule reaches the top of the car, every zone has an anchor, and
 *    every zone the part map (lib/vehicleZones.ts) uses has a mesh rule.
 * Skinned meshes (the van's body and wheels) are drawn where their bones put
 * them, which the JSON alone can't tell, so they are left out of the
 * position checks.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// apps/dashboard/package.json has no "type" field (Next.js owns it), so Node
// warns MODULE_TYPELESS_PACKAGE_JSON when it loads the ESM .ts rules file.
// Swallow just that warning; everything else still reaches stderr.
process.removeAllListeners("warning");
process.on("warning", (w) => {
  if (w.code !== "MODULE_TYPELESS_PACKAGE_JSON") console.error(w.stack ?? w.message);
});
const { CAR_PARTS_REAL, MODEL_YAW, ZONE_ANCHORS, isExcludedMesh, zonesForMeshNames } = await import("../lib/vehicleMeshRules.ts");
const { ZONE_LABELS } = await import("../lib/vehicleZones.ts");

const MODELS = ["sedan", "suv", "hatchback", "mpv", "van"];
const args = process.argv.slice(2);
const showAll = args.includes("--all");
const dumpNames = args.includes("--names");
const wanted = args.filter((a) => !a.startsWith("--"));
const models = wanted.length ? wanted : MODELS;
const vehiclesDir = fileURLToPath(new URL("../public/assets/vehicles/", import.meta.url));
const UNMAPPED_LIMIT = 30;

// ---------------------------------------------------------------------------
// GLB -> glTF JSON (header + first chunk only)
// ---------------------------------------------------------------------------
function readGltfJson(file) {
  const buf = readFileSync(file);
  if (buf.readUInt32LE(0) !== 0x46546c67) throw new Error(`${file}: not a GLB (bad magic)`);
  const jsonLen = buf.readUInt32LE(12);
  if (buf.readUInt32LE(16) !== 0x4e4f534a) throw new Error(`${file}: first chunk is not JSON`);
  return JSON.parse(buf.subarray(20, 20 + jsonLen).toString("utf8"));
}

// ---------------------------------------------------------------------------
// three.js GLTFLoader naming, replicated
//  - PropertyBinding.sanitizeNodeName: whitespace -> "_", strip [ ] . : /
//  - loadMesh names each primitive createUniqueName(mesh.name || "mesh_<i>")
//    (first use "x", later duplicates "x_1", "x_2" ... with one counter for
//    the whole file); a node then overwrites the top object's name with its
//    own sanitized name, so children of a multi-primitive Group keep the
//    mesh-derived names while the Group carries the node name
//  - a mesh referenced by several nodes is cloned and suffixed "_instance_N"
//    (only visible when the node itself has no name)
//  - the scene Group carries scenes[0].name; its own parent is the component
// ---------------------------------------------------------------------------
const sanitize = (name) => name.replace(/\s/g, "_").replace(/[\[\].:/]/g, "");

function buildRuntimeMeshes(json) {
  const nodes = json.nodes ?? [];
  const meshes = json.meshes ?? [];
  const scene = json.scenes?.[json.scene ?? 0] ?? { nodes: [] };

  const used = new Map();
  const uniqueName = (original) => {
    const s = sanitize(original || "");
    if (used.has(s)) {
      const n = used.get(s) + 1;
      used.set(s, n);
      return `${s}_${n}`;
    }
    used.set(s, 0);
    return s;
  };

  const refs = new Map();
  for (const n of nodes) if (n.mesh !== undefined) refs.set(n.mesh, (refs.get(n.mesh) ?? 0) + 1);
  const uses = new Map();

  const meshCache = new Map();
  const loadMesh = (mi) => {
    if (!meshCache.has(mi)) {
      const def = meshes[mi];
      meshCache.set(mi, def.primitives.map(() => uniqueName(def.name || `mesh_${mi}`)));
    }
    return meshCache.get(mi);
  };

  const sceneName = scene.name ? uniqueName(scene.name) : "";
  const out = [];
  let multi = 0;
  let unnamed = 0;

  const visit = (ni, parent, grand) => {
    const def = nodes[ni];
    const nodeName = def.name ? sanitize(def.name) : null;
    let own;
    if (def.mesh !== undefined) {
      const prims = loadMesh(def.mesh);
      const isGroup = prims.length > 1;
      let base = isGroup ? "" : prims[0];
      if ((refs.get(def.mesh) ?? 0) > 1) {
        const k = uses.get(def.mesh) ?? 0;
        uses.set(def.mesh, k + 1);
        base = `${base}_instance_${k}`;
      }
      own = nodeName ?? base;
      if (!def.name) unnamed++;
      if (isGroup) {
        multi++;
        prims.forEach((p, prim) => out.push({ names: [p, own, parent], group: true, node: ni, prim }));
      } else {
        out.push({ names: [own, parent, grand], group: false, node: ni, prim: 0 });
      }
    } else {
      own = nodeName ?? "";
    }
    for (const c of def.children ?? []) visit(c, own, parent);
  };
  for (const ni of scene.nodes ?? []) visit(ni, sceneName, "");

  // Duplicate glTF mesh names make the "_N" suffixes depend on load order,
  // which this script approximates with a pre-order walk. Flag it.
  const seen = new Map();
  for (const m of meshes) if (m.name) seen.set(m.name, (seen.get(m.name) ?? 0) + 1);
  const dupMeshNames = [...seen].filter(([, c]) => c > 1).map(([n]) => n);

  return { meshes: out, multi, unnamed, sceneName, dupMeshNames };
}

// ---------------------------------------------------------------------------
// Where each mesh is: accessor bounds through the node transforms
// ---------------------------------------------------------------------------
// 4x4 matrices, column-major as glTF writes them.
function trs(node) {
  if (node.matrix) return node.matrix.slice();
  const [tx, ty, tz] = node.translation ?? [0, 0, 0];
  const [x, y, z, w] = node.rotation ?? [0, 0, 0, 1];
  const [sx, sy, sz] = node.scale ?? [1, 1, 1];
  return [
    (1 - 2 * (y * y + z * z)) * sx, 2 * (x * y + z * w) * sx, 2 * (x * z - y * w) * sx, 0,
    2 * (x * y - z * w) * sy, (1 - 2 * (x * x + z * z)) * sy, 2 * (y * z + x * w) * sy, 0,
    2 * (x * z + y * w) * sz, 2 * (y * z - x * w) * sz, (1 - 2 * (x * x + y * y)) * sz, 0,
    tx, ty, tz, 1,
  ];
}
function mul(a, b) {
  const out = new Array(16).fill(0);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) for (let k = 0; k < 4; k++) out[c * 4 + r] += a[k * 4 + r] * b[c * 4 + k];
  return out;
}
const apply = (m, [x, y, z]) => [m[0] * x + m[4] * y + m[8] * z + m[12], m[1] * x + m[5] * y + m[9] * z + m[13], m[2] * x + m[6] * y + m[10] * z + m[14]];
// KHR_mesh_quantization: a normalized accessor's min/max are the stored integers.
const NORM = { 5120: 127, 5121: 255, 5122: 32767, 5123: 65535 };
const emptyBox = () => ({ min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] });
const isEmpty = (b) => b.min[0] > b.max[0];
function grow(box, p) {
  for (let i = 0; i < 3; i++) {
    box.min[i] = Math.min(box.min[i], p[i]);
    box.max[i] = Math.max(box.max[i], p[i]);
  }
}
const center = (b) => [0, 1, 2].map((i) => (b.min[i] + b.max[i]) / 2);

/** World matrix per node, from the scene's roots down. */
function worldMatrices(json) {
  const nodes = json.nodes ?? [];
  const world = new Map();
  const scene = json.scenes?.[json.scene ?? 0] ?? { nodes: [] };
  const visit = (ni, parent) => {
    const m = mul(parent, trs(nodes[ni]));
    world.set(ni, m);
    for (const c of nodes[ni].children ?? []) visit(c, m);
  };
  const identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  for (const ni of scene.nodes ?? []) visit(ni, identity);
  return world;
}

/** One runtime mesh's box in the model's space after the component's yaw,
 * or null for a skinned mesh (drawn where its bones put it). */
function meshBox(json, world, m, yaw) {
  const node = json.nodes[m.node];
  if (node.skin !== undefined) return null;
  const prim = json.meshes[node.mesh].primitives[m.prim];
  const acc = json.accessors[prim.attributes.POSITION];
  if (!acc?.min || !acc?.max) return null;
  const k = acc.normalized ? NORM[acc.componentType] ?? 1 : 1;
  const lo = acc.min.map((v) => Math.max(v / k, -1));
  const hi = acc.max.map((v) => v / k);
  const box = emptyBox();
  const c = Math.cos(yaw);
  const s = Math.sin(yaw);
  for (const x of [lo[0], hi[0]]) {
    for (const y of [lo[1], hi[1]]) {
      for (const z of [lo[2], hi[2]]) {
        const [wx, wy, wz] = apply(world.get(m.node), [x, y, z]);
        // three.js rotation.y: x' = x cos + z sin, z' = -x sin + z cos
        grow(box, [wx * c + wz * s, wy, -wx * s + wz * c]);
      }
    }
  }
  return box;
}

// ---------------------------------------------------------------------------
// Checks
// ---------------------------------------------------------------------------
const results = [];
const checkThat = (name, ok, detail = "") => results.push({ name, ok, detail });

// [front zone, rear zone] pairs whose meshes must keep that order along +Z.
const FRONT_OF = [
  ["front_bumper", "rear_bumper"],
  ["l_headlamp", "l_taillamp"],
  ["r_headlamp", "r_taillamp"],
  ["l_wheel_front", "l_wheel_rear"],
  ["r_wheel_front", "r_wheel_rear"],
  ["l_door_front", "l_door_rear"],
  ["r_door_front", "r_door_rear"],
  ["l_fender", "l_quarter"],
  ["r_fender", "r_quarter"],
  ["bonnet", "tailgate"],
  ["windscreen", "rear_glass"],
];
const AT_FRONT = ["front_bumper", "front_grill", "bonnet", "l_headlamp", "r_headlamp", "l_fender", "r_fender", "l_wheel_front", "r_wheel_front"];
const AT_REAR = ["rear_bumper", "tailgate", "rear_glass", "l_taillamp", "r_taillamp", "l_quarter", "r_quarter", "l_wheel_rear", "r_wheel_rear"];

function checkModel(model, json, analysed) {
  const world = worldMatrices(json);
  const yaw = MODEL_YAW[model] ?? 0;
  const whole = emptyBox();
  const zoneBox = new Map();
  let skinned = 0;
  for (const m of analysed) {
    const box = meshBox(json, world, m, yaw);
    if (!box) {
      skinned += 1;
      continue;
    }
    if (!m.hidden) {
      grow(whole, box.min);
      grow(whole, box.max);
    }
    if (m.zone) {
      const zb = zoneBox.get(m.zone) ?? emptyBox();
      grow(zb, box.min);
      grow(zb, box.max);
      zoneBox.set(m.zone, zb);
    }
  }
  if (skinned) console.log(`  (${skinned} skinned mesh${skinned === 1 ? "" : "es"} left out of the position checks)`);
  // The van: every mesh is skinned, so nothing to measure (its rules are
  // checked by name above, and its yaw by the 3D map's own screenshots).
  if (isEmpty(whole)) return;
  const mid = center(whole);
  const sideZones = [...zoneBox.keys()].filter((z) => /^[lr]_/.test(z));
  const sides = [];
  for (const zone of sideZones) {
    const [x] = center(zoneBox.get(zone));
    if (zone.startsWith("l_") && !(x > mid[0])) sides.push(`${zone} at x ${(x - mid[0]).toFixed(3)} (left must be +X)`);
    if (zone.startsWith("r_") && !(x < mid[0])) sides.push(`${zone} at x ${(x - mid[0]).toFixed(3)} (right must be -X)`);
  }
  checkThat(`${model}: every l_ zone on +X, every r_ zone on -X (${sideZones.length} zones with meshes)`, !sides.length, sides.join("; "));

  const ends = [];
  for (const zone of AT_FRONT) {
    const b = zoneBox.get(zone);
    if (b && !(center(b)[2] > mid[2])) ends.push(`${zone} not in the front half`);
  }
  for (const zone of AT_REAR) {
    const b = zoneBox.get(zone);
    if (b && !(center(b)[2] < mid[2])) ends.push(`${zone} not in the rear half`);
  }
  for (const [f, r] of FRONT_OF) {
    const bf = zoneBox.get(f);
    const br = zoneBox.get(r);
    if (bf && br && !(center(bf)[2] > center(br)[2])) ends.push(`${f} not in front of ${r}`);
  }
  checkThat(`${model}: front is +Z (each end's zones, and front-of pairs)`, !ends.length, ends.join("; "));

  const roof = zoneBox.get("roof");
  if (roof) {
    const height = whole.max[1] - whole.min[1];
    checkThat(
      `${model}: the roof rule reaches the top of the car`,
      roof.max[1] >= whole.max[1] - 0.05 * height,
      `roof top ${roof.max[1].toFixed(3)} vs car top ${whole.max[1].toFixed(3)}`,
    );
  }
}

function checkRules(allAnalysed) {
  const keys = CAR_PARTS_REAL.map((r) => r.key);
  checkThat("the rear quarter has its own zones", keys.includes("l_quarter") && keys.includes("r_quarter"));
  const rearInFender = [];
  for (const [model, analysed] of allAnalysed) {
    for (const m of analysed) {
      if ((m.zone === "l_fender" || m.zone === "r_fender") && m.names.some((n) => /rear|back/i.test(n || ""))) {
        rearInFender.push(`${model}: ${m.names.filter(Boolean).join(" | ")}`);
      }
    }
  }
  checkThat("no front-fender rule takes a rear mesh", !rearInFender.length, rearInFender.join("; "));
  const noAnchor = keys.filter((k) => !ZONE_ANCHORS[k]);
  checkThat("every zone has an anchor", !noAnchor.length, noAnchor.join(", "));
  const badAnchor = Object.entries(ZONE_ANCHORS).filter(([k, [x]]) => (k.startsWith("l_") && !(x > 0)) || (k.startsWith("r_") && !(x < 0)));
  checkThat("every l_ anchor at +X, every r_ anchor at -X", !badAnchor.length, badAnchor.map(([k]) => k).join(", "));
  const unknown = Object.keys(ZONE_LABELS).filter((k) => !keys.includes(k));
  checkThat("every zone the part map uses has a mesh rule", !unknown.length, unknown.join(", "));
}

// ---------------------------------------------------------------------------
// Reporting
// ---------------------------------------------------------------------------
// Sedan "Object_N" and SUV "mesh_N(_i)" own names say nothing; show the parent.
const GENERIC_OWN = /^(?:Object_\d+|mesh_\d+(?:_\d+)?)$/;
const label = (m) => (m.group || GENERIC_OWN.test(m.names[0]) ? m.names[1] || "(unnamed)" : m.names[0]);

function aggregate(list) {
  const counts = new Map();
  for (const m of list) counts.set(label(m), (counts.get(label(m)) ?? 0) + 1);
  return [...counts].map(([name, c]) => (c > 1 ? `${name} x${c}` : name));
}

function printList(title, items, limit) {
  if (!items.length) return;
  const shown = limit && !showAll ? items.slice(0, limit) : items;
  console.log(`  ${title} (${items.length}):`);
  for (const it of shown) console.log(`      ${it}`);
  if (shown.length < items.length) console.log(`      ... ${items.length - shown.length} more (pass --all to list them)`);
}

const patternHits = new Map(); // "zone#i" -> hit count across all models
for (const rule of CAR_PARTS_REAL) rule.match.forEach((_, i) => patternHits.set(`${rule.key}#${i}`, 0));

let failed = false;
const allAnalysed = new Map();
for (const model of models) {
  const file = `${vehiclesDir}${model}.v2.glb`;
  let json;
  try {
    json = readGltfJson(file);
  } catch (err) {
    failed = true;
    console.log(`\n== ${model}.v2.glb: ${err.message}`);
    continue;
  }
  const { meshes, multi, unnamed, sceneName, dupMeshNames } = buildRuntimeMeshes(json);

  const analysed = meshes.map((m) => {
    const zones = zonesForMeshNames(m.names);
    const hidden = isExcludedMesh(m.names);
    for (const rule of CAR_PARTS_REAL) {
      rule.match.forEach((re, i) => {
        if (m.names.some((n) => n && re.test(n))) patternHits.set(`${rule.key}#${i}`, patternHits.get(`${rule.key}#${i}`) + 1);
      });
    }
    return { ...m, zones, zone: zones[0] ?? null, hidden };
  });
  allAnalysed.set(model, analysed);

  console.log(`\n${"=".repeat(78)}`);
  console.log(
    `== ${model}.v2.glb: ${analysed.length} meshes` +
      ` (${multi} multi-primitive group${multi === 1 ? "" : "s"}, ${unnamed} unnamed node${unnamed === 1 ? "" : "s"},` +
      ` scene "${sceneName}")`,
  );
  if (dupMeshNames.length) {
    console.log(`  WARNING: ${dupMeshNames.length} duplicate glTF mesh names; "_N" suffixes below may differ from runtime order`);
  }

  console.log("  zone -> meshes (generic Object_N / mesh_N children are shown by their parent group; [hidden] = also in CAR_EXCLUDE):");
  const emptyZones = [];
  const hiddenOnlyZones = [];
  for (const rule of CAR_PARTS_REAL) {
    const mine = analysed.filter((m) => m.zone === rule.key);
    const visible = aggregate(mine.filter((m) => !m.hidden));
    const hidden = aggregate(mine.filter((m) => m.hidden));
    const parts = [...visible, ...hidden.map((h) => `${h} [hidden]`)];
    if (!mine.length) emptyZones.push(rule.key);
    else if (!visible.length) hiddenOnlyZones.push(rule.key);
    console.log(`    ${rule.key.padEnd(20)} ${parts.length ? parts.join(", ") : "-- none (anchor fallback)"}`);
  }

  const hidden = analysed.filter((m) => m.hidden);
  printList(
    "hidden by CAR_EXCLUDE",
    aggregate(hidden.filter((m) => !m.zone)).concat(
      [...new Set(hidden.filter((m) => m.zone).map((m) => `${label(m)} -> ${m.zone}`))],
    ),
  );

  const collisions = analysed.filter((m) => m.zones.length > 1);
  printList(
    "COLLISIONS (claimed by more than one zone; first wins)",
    [...new Set(collisions.map((m) => `${label(m)}: ${m.zones[0]} (wins) | ${m.zones.slice(1).join(" | ")}`))],
  );

  const unmapped = analysed.filter((m) => !m.zone && !m.hidden);
  printList("visible but unmapped (drawn in the base colour)", aggregate(unmapped), UNMAPPED_LIMIT);

  if (hiddenOnlyZones.length) console.log(`  zones whose only meshes are hidden: ${hiddenOnlyZones.join(", ")}`);
  console.log(`  zones with NO mesh (anchor fallback) (${emptyZones.length}/${CAR_PARTS_REAL.length}): ${emptyZones.join(", ") || "none"}`);

  checkModel(model, json, analysed);

  if (dumpNames) {
    console.log("  runtime names [own | parent | grandparent] -> zone:");
    for (const m of analysed) {
      console.log(`      ${m.names.map((n) => JSON.stringify(n)).join(" | ")} -> ${m.zone ?? "-"}${m.hidden ? " [hidden]" : ""}`);
    }
  }
}

if (models.length === MODELS.length) {
  const unused = [];
  for (const rule of CAR_PARTS_REAL) {
    rule.match.forEach((re, i) => {
      if (patternHits.get(`${rule.key}#${i}`) === 0) unused.push(`${rule.key}: ${re}`);
    });
  }
  console.log(`\n${"=".repeat(78)}`);
  console.log(unused.length ? `Rules that matched nothing in any model (${unused.length}):` : "Every rule matched at least one mesh in some model.");
  for (const u of unused) console.log(`    ${u}`);
}

checkRules(allAnalysed);
console.log(`\n${"=".repeat(78)}\nChecks:`);
for (const r of results) console.log(`  ${r.ok ? "ok  " : "FAIL"} ${r.name}${!r.ok && r.detail ? `\n       ${r.detail}` : ""}`);
const failures = results.filter((r) => !r.ok).length;
console.log(failures ? `${failures} check${failures === 1 ? "" : "s"} failed` : `all ${results.length} checks passed`);

process.exitCode = failed || failures ? 1 : 0;
