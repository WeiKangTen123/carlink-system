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
const { CAR_PARTS_REAL, isExcludedMesh, zonesForMeshNames } = await import("../lib/vehicleMeshRules.ts");

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
        for (const p of prims) out.push({ names: [p, own, parent], group: true });
      } else {
        out.push({ names: [own, parent, grand], group: false });
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

process.exitCode = failed ? 1 : 0;
