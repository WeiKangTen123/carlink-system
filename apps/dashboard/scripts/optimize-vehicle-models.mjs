// Rebuilds the compressed vehicle models in public/assets/vehicles/*.v2.glb
// from the original Sketchfab exports.
//
// Not part of the build and not a dashboard dependency -- it documents (and
// can reproduce) the one-off conversion. Run it from a scratch folder:
//
//   mkdir gltf-tools && cd gltf-tools && npm init -y
//   npm i @gltf-transform/core@4.5.1 @gltf-transform/extensions@4.5.1 \
//         @gltf-transform/functions@4.5.1 meshoptimizer@1.3.0
//   cp <repo>/apps/dashboard/scripts/optimize-vehicle-models.mjs .
//   node optimize-vehicle-models.mjs <dir-with-original-glbs> <repo>/apps/dashboard/public/assets/vehicles
//
// The originals (sedan.glb, suv.glb, hatchback.glb, mpv.glb, van.glb) were
// deleted from public/ in the same change; take them from git history (the
// commit before this script was added).
//
// What it does, and why it can't change what the 3D blueprint shows or which
// parts it finds:
//
// - Drops textures/images, the UVs and tangents only they used, and vertex
//   colours. VehicleBlueprint3D replaces the material of EVERY visible mesh
//   with a plain MeshBasicMaterial (wireframe, or a flat severity colour, no
//   map, no vertexColors), so none of that was ever displayed. Materials
//   themselves are kept (just without textures).
// - Welds bit-identical vertices (the SUV was stored unindexed) and reorders
//   them for compression. Triangles are untouched -- same count, same shape.
// - Quantizes positions to 14 bits per mesh (KHR_mesh_quantization) and
//   compresses all vertex/index data with EXT_meshopt_compression (vertex
//   codec v0, the version three-stdlib's bundled MeshoptDecoder -- what
//   drei's useGLTF enables by default -- can decode). No Draco: its decoder
//   would be fetched from an external CDN.
// - Never joins, flattens, instances, simplifies, renames or removes nodes or
//   meshes: zones are found by node/mesh NAME (findCarMeshes, partName,
//   partGroupName, CAR_PARTS_REAL), so the scene graph must stay exactly as
//   it was. The script checks that before writing each file.
//
// The output name carries a version (.v2.glb) because /assets/* is served
// `Cache-Control: immutable`: a browser that cached the old file under the
// old URL would never fetch a replacement under that same URL.

import fs from 'node:fs';
import path from 'node:path';
import { NodeIO, PropertyType } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { meshopt, prune, weld } from '@gltf-transform/functions';
import { MeshoptDecoder, MeshoptEncoder } from 'meshoptimizer';

const MODELS = ['sedan', 'suv', 'hatchback', 'mpv', 'van'];
const VERSION = 'v2';

const [srcDir, outDir] = process.argv.slice(2);
if (!srcDir || !outDir) {
  console.error('usage: node optimize-vehicle-models.mjs <src-dir> <out-dir>');
  process.exit(1);
}

await MeshoptDecoder.ready;
await MeshoptEncoder.ready;
const io = new NodeIO()
  .registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.decoder': MeshoptDecoder, 'meshopt.encoder': MeshoptEncoder });

/** Scene graph as text: one line per node, indented by depth, with the mesh
 * name and each primitive's triangle count. Must be identical before/after. */
function describeSceneGraph(doc) {
  const lines = [];
  const walk = (node, depth) => {
    const mesh = node.getMesh();
    const tris = mesh
      ? mesh.listPrimitives().map((p) => (p.getIndices() ?? p.getAttribute('POSITION')).getCount() / 3)
      : [];
    lines.push(`${'  '.repeat(depth)}${node.getName()}${mesh ? ` [${mesh.getName()}] ${tris.join(',')}` : ''}`);
    node.listChildren().forEach((child) => walk(child, depth + 1));
  };
  for (const scene of doc.getRoot().listScenes()) {
    lines.push(`scene ${scene.getName()}`);
    scene.listChildren().forEach((n) => walk(n, 1));
  }
  return lines.join('\n');
}

fs.mkdirSync(outDir, { recursive: true });
for (const name of MODELS) {
  const src = path.join(srcDir, `${name}.glb`);
  const dst = path.join(outDir, `${name}.${VERSION}.glb`);
  const doc = await io.read(src);
  const before = describeSceneGraph(doc);

  // 1. Appearance-only data the viewer never displays.
  for (const texture of doc.getRoot().listTextures()) texture.dispose();
  for (const mesh of doc.getRoot().listMeshes()) {
    for (const prim of mesh.listPrimitives()) {
      for (const semantic of prim.listSemantics()) {
        if (semantic.startsWith('COLOR_')) prim.setAttribute(semantic, null);
      }
    }
  }
  // Removes the now-unused UVs/tangents and orphaned accessors/textures.
  // Nodes, meshes and materials are deliberately NOT in propertyTypes, and
  // keepLeaves keeps empty named group nodes.
  await doc.transform(
    prune({
      propertyTypes: [PropertyType.ACCESSOR, PropertyType.TEXTURE],
      keepAttributes: false,
      keepIndices: true,
      keepLeaves: true,
      keepExtras: true,
    }),
  );

  // 2. Geometry: weld identical vertices, then meshopt (reorder + quantize +
  // EXT_meshopt_compression on write).
  await doc.transform(weld(), meshopt({ encoder: MeshoptEncoder, level: 'high' }));

  const after = describeSceneGraph(doc);
  if (after !== before) {
    throw new Error(`${name}: scene graph changed (node/mesh names, hierarchy or triangle counts) -- not writing`);
  }
  await io.write(dst, doc);
  const kb = (f) => (fs.statSync(f).size / 1024 / 1024).toFixed(2);
  console.log(`${name}: ${kb(src)} MB -> ${kb(dst)} MB  (${dst})`);
}
