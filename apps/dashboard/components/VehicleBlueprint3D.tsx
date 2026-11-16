"use client";

import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";

// Filter out benign THREE.Clock deprecation notice triggered by @react-three/fiber v9 until upstream updates to THREE.Timer
if (typeof window !== "undefined") {
  const originalWarn = console.warn;
  console.warn = (...args: any[]) => {
    if (typeof args[0] === "string" && args[0].includes("THREE.Clock: This module has been deprecated")) {
      return;
    }
    originalWarn.apply(console, args);
  };
}

import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { Grid, Html, OrbitControls, useGLTF } from "@react-three/drei";
import { clone as cloneWithSkeleton } from "three/examples/jsm/utils/SkeletonUtils.js";
import { marksSignature, STATUS_GLYPHS, zoneMarks, type ZoneMark } from "@/lib/damageLinks";
import { detectBodyType, zoneLabel, type VehicleArchetype } from "@/lib/vehicleZones";
import {
  CAR_PARTS_REAL,
  MODEL_YAW,
  anchorPosition,
  isExcludedMesh,
  nearestAnchorZone,
  roofClickAllowed,
  zoneForMeshNames,
  type PlainBox,
} from "@/lib/vehicleMeshRules";

// The body type -> model choice moved to lib/vehicleZones.ts (pages pick a
// model without loading three.js); re-exported for older importers.
export { detectBodyType };
export type { VehicleArchetype };

/** Reads a CSS custom property off <html> and keeps it live across the
 * app's dark/white theme toggle (ThemeToggle.tsx flips data-theme with no
 * page reload) -- WebGL material colors are plain numbers, not CSS
 * `var()`, so this has to be re-read explicitly whenever the attribute
 * changes. */
function useThemeColor(varName: string, fallback: string): string {
  const [color, setColor] = useState(fallback);
  useEffect(() => {
    const read = () => {
      const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
      setColor(v || fallback);
    };
    read();
    const observer = new MutationObserver(read);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, [varName, fallback]);
  return color;
}

// Zone key -> real mesh names inside the five models (CAR_PARTS_REAL), the
// interior/mechanical clutter kept hidden (CAR_EXCLUDE), the anchors for
// zones no mesh rule matched and the click rules live in
// lib/vehicleMeshRules.ts, shared with scripts/check-vehicle-mesh-rules.mjs,
// which checks every rule against the real model files. The free-text
// part -> zone-key mapping lives in lib/vehicleZones.ts, and finding ->
// zone in lib/damageLinks.ts. Axes in every model: +Z = front, +X = the
// car's LEFT (LH) side, +Y = up -- so the front view shows the left side on
// the screen's right.

/** The three names a rule can match for one mesh: its own, its parent's
 * and its grandparent's. The models differ in where the descriptive name
 * sits -- the SUV names the mesh itself (parent unnamed), Sketchfab exports
 * name a wrapper Group one level up ("Object_N" meshes under
 * "door-front-l_16"), and the sedan's wheel corner sits two levels up.
 * Empty strings where a level is missing. */
function meshNames(obj: THREE.Object3D): string[] {
  return [obj.name || "", obj.parent?.name || "", obj.parent?.parent?.name || ""];
}

/** World-space box of one mesh, measured the way it is drawn.
 *
 * A skinned mesh (the van) is drawn where its bones put it, not where its
 * own node sits, and the compressed van file keeps its real size in the
 * bind matrices: its raw vertices only span a unit cube. So the bones are
 * posed from the current transforms first (Skeleton.update() is otherwise
 * only run by the renderer, and its matrices are all zero before the first
 * frame) and three.js then runs every vertex through them. */
function meshWorldBox(mesh: THREE.Mesh, target: THREE.Box3): THREE.Box3 {
  const skinned = mesh as THREE.SkinnedMesh;
  if (skinned.isSkinnedMesh) {
    skinned.skeleton.update();
    skinned.computeBoundingBox();
    return target.copy(skinned.boundingBox as THREE.Box3).applyMatrix4(mesh.matrixWorld);
  }
  const geo = mesh.geometry;
  if (geo.boundingBox === null) geo.computeBoundingBox();
  return target.copy(geo.boundingBox as THREE.Box3).applyMatrix4(mesh.matrixWorld);
}

function boxOfMeshes(meshes: THREE.Mesh[]): THREE.Box3 {
  const box = new THREE.Box3();
  const tmp = new THREE.Box3();
  meshes.forEach((m) => box.union(meshWorldBox(m, tmp)));
  return box;
}

/** Bounds of everything currently drawn (hidden interior clutter left out). */
function measureVisible(root: THREE.Object3D): THREE.Box3 {
  root.updateMatrixWorld(true);
  const shown: THREE.Mesh[] = [];
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (mesh.isMesh && mesh.visible && mesh.geometry) shown.push(mesh);
  });
  return boxOfMeshes(shown);
}

// Geometry-only, meshopt-compressed copies of the original models (about
// 67MB -> 4.8MB for all five; see scripts/optimize-vehicle-models.mjs).
// drei's useGLTF turns the meshopt decoder on by default. /assets/* is
// served `immutable`, so a changed model needs a new file name (bump the
// .vN), never an in-place replacement -- browsers that cached the old URL
// would keep using the old file.
export const MODEL_PATHS: Record<VehicleArchetype, string> = {
  suv: "/assets/vehicles/suv.v2.glb",
  sedan: "/assets/vehicles/sedan.v2.glb",
  van: "/assets/vehicles/van.v2.glb",
  mpv: "/assets/vehicles/mpv.v2.glb",
  hatchback: "/assets/vehicles/hatchback.v2.glb",
};

// Real length, in metres, each model is scaled to. The files use whatever
// unit their author exported -- measured bumper to bumper: sedan 4.98
// (metres), suv 419 (centimetres), hatchback 0.04, mpv 122, van 3.74 --
// so a file's own numbers can't be drawn as they are: the SUV put the
// camera inside a 400 m long car, the hatchback was an invisible dot.
const TARGET_LENGTH_M: Record<VehicleArchetype, number> = {
  sedan: 4.8,
  suv: 4.5,
  hatchback: 4.0,
  mpv: 4.8,
  van: 4.9,
};

// A mesh longer than this share of the car is a body shell (the sedan's
// "sedan-unibody", the SUV's "chassis-sunroof"): the roof rule's only
// mesh, but painting or outlining it would light the whole car. The roof
// is then shown by its marker at the top instead.
const SHELL_SHARE = 0.6;

/** The pattern each status draws with (never colour alone: the markers
 * carry the glyph). Colours sit on top of the pattern. */
const STATUS_COLOR = {
  confirmed: "#10b981",
  ai: "#f59e0b",
  check: "#f97316",
  suggestion: "#a855f7",
  dismissed: "#94a3b8",
} as const;

// =============================================================================
// Camera

export type BlueprintView = "home" | "front" | "rear" | "lh" | "rh" | "top";

/** Where the camera goes. `n` changes for every request, so the same view
 * or part asked for twice moves the camera back to it. Nothing else moves
 * the camera: not a hover, not a new damage list. */
export type CameraRequest = { kind: "view"; view: BlueprintView; n: number } | { kind: "zones"; zones: string[]; n: number };

interface FocusState {
  position: THREE.Vector3;
  target: THREE.Vector3;
}

const HOME: FocusState = { position: new THREE.Vector3(3.6, 2.4, 3.9), target: new THREE.Vector3(0, 0.55, 0) };
const HOME_DIRECTION = new THREE.Vector3(3.6, 1.85, 3.9).normalize();

const FOV = 40;

/** How far back the camera stands to fit `across` (screen width) and `up`
 * (screen height) with a margin, for the canvas's aspect. */
function fitDistance(across: number, up: number, aspect: number): number {
  const tan = Math.tan(THREE.MathUtils.degToRad(FOV / 2));
  return 1.3 * Math.max(across / 2 / (tan * Math.max(aspect, 0.5)), up / 2 / tan);
}

/** A preset view for a model of this size. The front view looks down -Z at
 * the front, so the car's left (+X) is on the screen's right; the top view
 * has the front at the top of the screen. */
function viewState(view: BlueprintView, box: THREE.Box3, aspect: number): FocusState {
  const size = box.getSize(new THREE.Vector3());
  const target = new THREE.Vector3(0, size.y * 0.45, 0);
  const at = (x: number, y: number, z: number) => ({ position: new THREE.Vector3(x, y, z), target: target.clone() });
  const lift = size.y * 0.35;
  switch (view) {
    case "front":
    case "rear": {
      const d = fitDistance(size.x, size.y, aspect) + size.z / 2;
      return at(0, target.y + lift, view === "front" ? d : -d);
    }
    case "lh":
    case "rh": {
      const d = fitDistance(size.z, size.y, aspect) + size.x / 2;
      return at(view === "lh" ? d : -d, target.y + lift, 0);
    }
    case "top": {
      // A hair behind straight-down, so the screen's "up" is the car's front.
      const d = fitDistance(size.x, size.z, aspect) + size.y;
      return { position: new THREE.Vector3(0, d, -0.02), target: new THREE.Vector3(0, size.y * 0.3, 0) };
    }
    default: {
      // Three-quarter from the front left, the whole car in frame.
      const t = new THREE.Vector3(0, size.y * 0.4, 0);
      const d = Math.max(fitDistance(Math.hypot(size.x, size.z) * 0.92, size.y * 1.9, aspect), 3);
      return { position: t.clone().addScaledVector(HOME_DIRECTION, d), target: t };
    }
  }
}

/** Lerps the camera (and the orbit target) to `focus` -- reads as "pushing
 * in toward what you clicked". Frames are drawn on demand, so it asks for
 * one per step while it moves. */
function CameraRig({ focus, controlsRef }: { focus: FocusState | null; controlsRef: React.RefObject<any> }) {
  const { camera, invalidate } = useThree();
  const animRef = useRef<{ fromPos: THREE.Vector3; toPos: THREE.Vector3; fromTarget: THREE.Vector3; toTarget: THREE.Vector3; start: number } | null>(null);
  const targetRef = useRef(HOME.target.clone());

  useEffect(() => {
    if (!focus) return;
    animRef.current = {
      fromPos: camera.position.clone(),
      toPos: focus.position.clone(),
      fromTarget: controlsRef.current ? controlsRef.current.target.clone() : targetRef.current.clone(),
      toTarget: focus.target.clone(),
      start: performance.now(),
    };
    invalidate();
  }, [focus, camera, controlsRef, invalidate]);

  useFrame(() => {
    const anim = animRef.current;
    if (!anim) return;
    const t = Math.min(1, (performance.now() - anim.start) / 550);
    const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    camera.position.lerpVectors(anim.fromPos, anim.toPos, e);
    targetRef.current.lerpVectors(anim.fromTarget, anim.toTarget, e);
    // Turned here, not on the controls' next update: that ran earlier in
    // this frame, and on demand there may be no next frame to catch up.
    camera.lookAt(targetRef.current);
    if (controlsRef.current) {
      controlsRef.current.target.copy(targetRef.current);
      controlsRef.current.update();
    }
    if (t >= 1) animRef.current = null;
    else invalidate();
  });

  return null;
}

// =============================================================================
// Materials: one shared set per model, never one per mesh.

type Materials = {
  base: THREE.MeshBasicMaterial;
  confirmed: THREE.MeshBasicMaterial;
  ai: THREE.MeshBasicMaterial;
  check: THREE.MeshBasicMaterial;
  suggestion: THREE.MeshBasicMaterial;
  dismissed: THREE.MeshBasicMaterial;
  select: THREE.MeshBasicMaterial;
  hover: THREE.MeshBasicMaterial;
  stripe: { value: number };
};

/** Suggestions are striped. The optimised models have no UVs, so the
 * stripes are drawn in screen space (gl_FragCoord) on top of the usual
 * flat colour: one extra shader program, shared by every striped mesh. */
function stripedMaterial(color: string, stripe: { value: number }): THREE.MeshBasicMaterial {
  const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthWrite: false });
  mat.onBeforeCompile = (shader) => {
    shader.uniforms.uStripe = stripe;
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nuniform float uStripe;")
      .replace(
        "#include <dithering_fragment>",
        "#include <dithering_fragment>\n\tif ( mod( gl_FragCoord.x + gl_FragCoord.y, uStripe ) < uStripe * 0.5 ) gl_FragColor.a *= 0.15;",
      );
  };
  mat.customProgramCacheKey = () => "carlink-stripes";
  return mat;
}

function makeMaterials(accent: string, ink: string): Materials {
  const fill = (color: string, opacity: number) =>
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity, side: THREE.DoubleSide, depthWrite: false });
  const wire = (color: string, opacity: number, onTop: boolean) =>
    new THREE.MeshBasicMaterial({ color, wireframe: true, transparent: true, opacity, depthTest: !onTop, depthWrite: false });
  const stripe = { value: 10 * (typeof window !== "undefined" ? Math.min(window.devicePixelRatio || 1, 2) : 1) };
  return {
    base: wire(accent, 0.85, false),
    confirmed: fill(STATUS_COLOR.confirmed, 0.8),
    ai: fill(STATUS_COLOR.ai, 0.38),
    check: fill(STATUS_COLOR.check, 0.38),
    suggestion: stripedMaterial(STATUS_COLOR.suggestion, stripe),
    dismissed: wire(STATUS_COLOR.dismissed, 0.9, false),
    select: wire(ink, 0.95, true),
    hover: wire(accent, 0.7, true),
    stripe,
  };
}

// =============================================================================
// The model

type Placement = { center: THREE.Vector3; size: THREE.Vector3; fromMesh: boolean };

/** Dev only: what the test page reads for the performance checks. */
type DevHook = { gl?: THREE.WebGLRenderer; camera?: THREE.Camera; frames: number; lastFrameAt: number; overlays: number; paintMs: number; outlineMs: number; outlineAt?: number };
const devHook = (): DevHook | null => {
  if (process.env.NODE_ENV === "production" || typeof window === "undefined") return null;
  const w = window as unknown as { __damageMap?: DevHook };
  w.__damageMap ??= { frames: 0, lastFrameAt: 0, overlays: 0, paintMs: 0, outlineMs: 0 };
  return w.__damageMap;
};

type ModelProps = {
  modelUrl: string;
  archetype: VehicleArchetype;
  accent: string;
  ink: string;
  marks: ZoneMark[];
  selectedZones: string[];
  hoverZones: string[];
  camera: CameraRequest | null;
  showAnchors: boolean;
  onZoneClick: (zone: string, approx: boolean) => void;
  onZoneHover?: (zone: string | null) => void;
  onFocusRequest: (state: FocusState) => void;
};

function VehicleModel({
  modelUrl,
  archetype,
  accent,
  ink,
  marks,
  selectedZones,
  hoverZones,
  camera,
  showAnchors,
  onZoneClick,
  onZoneHover,
  onFocusRequest,
}: ModelProps) {
  const gltf = useGLTF(modelUrl);
  const { invalidate, size: viewport } = useThree();

  // One clone per mount, normalized from the loaded geometry itself (never
  // a hand-typed offset): hide interior clutter, turn the front to +Z, scale
  // to the archetype's real length, centre on x/z and sit the wheels on the
  // ground. cloneWithSkeleton() keeps the van's bones with its skinned
  // meshes; a plain clone() leaves them on the cached original, and a
  // skinned mesh draws where its bones are, so the van would ignore every
  // transform applied here. Everything per mesh is worked out here once:
  // which zone it is, whether it is a shell, which meshes each zone has.
  const model = useMemo(() => {
    const clone = cloneWithSkeleton(gltf.scene);
    const meshes: THREE.Mesh[] = [];
    clone.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      if (!mesh.isMesh) return;
      meshes.push(mesh);
      if (isExcludedMesh(meshNames(mesh))) mesh.visible = false;
      // A skinned mesh's culling sphere comes from bone state; never cull it.
      if ((mesh as THREE.SkinnedMesh).isSkinnedMesh) mesh.frustumCulled = false;
      // three.js raycasts hidden meshes too: a click went to the seat behind
      // the door. Only what is drawn can be clicked.
      const raycast = mesh.raycast;
      mesh.raycast = function (raycaster, hits) {
        if (this.visible) raycast.call(this, raycaster, hits);
      };
    });
    clone.rotation.set(0, MODEL_YAW[archetype] ?? 0, 0);
    const raw = measureVisible(clone);
    const rawLength = raw.max.z - raw.min.z;
    if (rawLength > 1e-6) clone.scale.setScalar(TARGET_LENGTH_M[archetype] / rawLength);
    const scaled = measureVisible(clone);
    const center = scaled.getCenter(new THREE.Vector3());
    clone.position.set(-center.x, -scaled.min.y, -center.z);
    const box = measureVisible(clone);
    const length = box.max.z - box.min.z;

    const hiddenByDefault = new Set(meshes.filter((m) => !m.visible));
    const zoneOf = new Map<THREE.Mesh, string | null>();
    const zoneMeshes = new Map<string, THREE.Mesh[]>();
    const shells = new Set<THREE.Mesh>();
    const tmp = new THREE.Box3();
    for (const mesh of meshes) {
      const zone = zoneForMeshNames(meshNames(mesh));
      zoneOf.set(mesh, zone);
      if (!zone) continue;
      const b = meshWorldBox(mesh, tmp);
      if (b.max.z - b.min.z > SHELL_SHARE * length) {
        shells.add(mesh);
        continue;
      }
      const list = zoneMeshes.get(zone);
      if (list) list.push(mesh);
      else zoneMeshes.set(zone, [mesh]);
    }
    const plainBox: PlainBox = { min: box.min.toArray() as [number, number, number], max: box.max.toArray() as [number, number, number] };
    const noMeshZones = CAR_PARTS_REAL.map((r) => r.key).filter((k) => !zoneMeshes.has(k));
    return { root: clone, meshes, box, plainBox, hiddenByDefault, zoneOf, zoneMeshes, shells, noMeshZones };
  }, [gltf, archetype]);

  const { root, meshes, box, plainBox, hiddenByDefault, zoneOf, zoneMeshes, shells, noMeshZones } = model;

  // Where a zone sits on this model: the box of its real meshes, or -- for
  // zones no rule maps here (the MPV's and the van's generically named
  // meshes, the sedan's quarter inside its body shell) -- its anchor.
  const placements = useMemo(() => {
    const cache = new Map<string, Placement>();
    return (zone: string): Placement => {
      let p = cache.get(zone);
      if (p) return p;
      const own = zoneMeshes.get(zone);
      if (own?.length) {
        const b = boxOfMeshes(own);
        p = { center: b.getCenter(new THREE.Vector3()), size: b.getSize(new THREE.Vector3()), fromMesh: true };
      } else {
        p = { center: new THREE.Vector3(...anchorPosition(zone, plainBox)), size: new THREE.Vector3(0.4, 0.3, 0.4), fromMesh: false };
      }
      cache.set(zone, p);
      return p;
    };
  }, [zoneMeshes, plainBox]);

  // One shared set of materials for the life of this model.
  const mats = useMemo(() => makeMaterials(accent, ink), [accent, ink]);
  useEffect(() => () => Object.values(mats).forEach((m) => (m as THREE.Material).dispose?.()), [mats]);

  // Paint: every drawn mesh the base wireframe, each zone with a mark its
  // status's material. Runs only when the marks' content changes (the
  // caller keeps their identity), so a tick elsewhere doesn't repaint.
  useEffect(() => {
    const t0 = performance.now();
    for (const mesh of meshes) {
      mesh.visible = !hiddenByDefault.has(mesh);
      mesh.material = mats.base;
    }
    for (const m of marks) {
      const mat = mats[m.lead];
      for (const mesh of zoneMeshes.get(m.zone) ?? []) {
        // A normally-hidden structural mesh is shown when it is the damaged part.
        mesh.visible = true;
        mesh.material = mat;
      }
    }
    const hook = devHook();
    if (hook) hook.paintMs = performance.now() - t0;
    invalidate();
  }, [meshes, hiddenByDefault, zoneMeshes, marks, mats, invalidate]);

  // Outlines: a selected zone's meshes drawn again on top in a bright
  // wireframe (a clone sharing the geometry, so nothing is built), a
  // hovered one in a softer one; the rest of the car dims while something
  // is selected. Only these few overlay objects change on a selection, in
  // the same commit, so the next frame already shows them.
  const overlaysRef = useRef<THREE.Mesh[]>([]);
  useLayoutEffect(() => {
    const t0 = performance.now();
    for (const o of overlaysRef.current) o.parent?.remove(o);
    overlaysRef.current = [];
    const add = (zone: string, mat: THREE.Material) => {
      for (const mesh of zoneMeshes.get(zone) ?? []) {
        if (!mesh.visible || !mesh.parent) continue;
        const o = mesh.clone(false) as THREE.Mesh;
        o.material = mat;
        o.raycast = () => {};
        o.renderOrder = 10;
        mesh.parent.add(o);
        overlaysRef.current.push(o);
      }
    };
    const selected = new Set(selectedZones);
    selectedZones.forEach((z) => add(z, mats.select));
    hoverZones.filter((z) => !selected.has(z)).forEach((z) => add(z, mats.hover));
    mats.base.opacity = selectedZones.length ? 0.3 : 0.85;
    const hook = devHook();
    if (hook) {
      hook.outlineMs = performance.now() - t0;
      hook.outlineAt = performance.now();
      hook.overlays = overlaysRef.current.length;
    }
    invalidate();
  }, [selectedZones, hoverZones, zoneMeshes, mats, marks, invalidate]);
  useEffect(() => () => overlaysRef.current.forEach((o) => o.parent?.remove(o)), []);

  // The camera moves only when asked (a view button, a selection), never
  // on hover or on a new damage list. A fresh model with nothing asked yet
  // frames itself for this canvas.
  useEffect(() => {
    if (!camera) {
      onFocusRequest(viewState("home", box, viewport.width / Math.max(viewport.height, 1)));
      return;
    }
    if (camera.kind === "view") {
      onFocusRequest(viewState(camera.view, box, viewport.width / Math.max(viewport.height, 1)));
      return;
    }
    if (!camera.zones.length) return;
    const union = new THREE.Box3();
    for (const z of camera.zones) {
      const p = placements(z);
      const size = p.fromMesh ? p.size : new THREE.Vector3(1.1, 1.1, 1.1);
      union.union(new THREE.Box3().setFromCenterAndSize(p.center, size));
    }
    const center = union.getCenter(new THREE.Vector3());
    const size = union.getSize(new THREE.Vector3());
    const radius = Math.max(size.x, size.y, size.z, 0.25);
    const dist = Math.max(radius * 2.6, 2);
    // Approach from the side the part sits on relative to the car's centre
    // (the model is centred on x=0, z=0), not from wherever the camera is:
    // the right door is always approached from the right, the front bumper
    // from the front. Both sides selected (an unknown side): from its end,
    // or from above.
    const outward = new THREE.Vector3(center.x, 0, center.z);
    if (outward.lengthSq() < 0.04) outward.set(0, 0, 0);
    const up = outward.lengthSq() < 0.0001 ? 1.4 : 0.45;
    if (outward.lengthSq() < 0.0001) outward.set(0, 0, -0.02);
    outward.normalize();
    const dir = outward.multiplyScalar(0.82).add(new THREE.Vector3(0, up, 0)).normalize();
    onFocusRequest({ position: center.clone().addScaledVector(dir, dist), target: center.clone() });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only a new request moves the camera
  }, [camera?.n, box]);

  /** What a pointer is on: the first DRAWN mesh under it (hidden ones and
   * outlines never count). A shell counts as the roof only in the top band;
   * a mesh with no rule (or the rest of a shell) goes to the nearest anchor
   * of a zone this model has no mesh for, as "(approx.)". */
  const resolveHit = useCallback(
    (e: ThreeEvent<PointerEvent | MouseEvent>): { zone: string; approx: boolean } | null => {
      for (const hit of e.intersections) {
        const mesh = hit.object as THREE.Mesh;
        if (!mesh.isMesh || !mesh.visible || !zoneOf.has(mesh)) continue;
        const zone = zoneOf.get(mesh) ?? null;
        if (zone && !shells.has(mesh)) return { zone, approx: false };
        if (zone === "roof" && roofClickAllowed(hit.point.y, box.min.y, box.max.y)) return { zone: "roof", approx: false };
        const near = nearestAnchorZone([hit.point.x, hit.point.y, hit.point.z], plainBox, noMeshZones);
        return near ? { zone: near, approx: true } : null;
      }
      return null;
    },
    [zoneOf, shells, box, plainBox, noMeshZones],
  );

  const hoverRef = useRef<string | null>(null);
  const setHover = (zone: string | null) => {
    if (hoverRef.current === zone) return;
    hoverRef.current = zone;
    document.body.style.cursor = zone ? "pointer" : "";
    onZoneHover?.(zone);
  };
  useEffect(() => () => {
    document.body.style.cursor = "";
  }, []);

  // Markers on the far side of the car fade (they still click): drawn
  // through the body, the rear's markers crowded a front view. Set on the
  // DOM each drawn frame, no re-render.
  const markerEls = useRef(new Map<string, HTMLElement>());
  const toCamera = useMemo(() => new THREE.Vector3(), []);
  const outward = useMemo(() => new THREE.Vector3(), []);
  useFrame(({ camera: cam }) => {
    markerEls.current.forEach((el, zone) => {
      const c = placements(zone).center;
      outward.set(c.x, 0, c.z);
      if (outward.lengthSq() < 0.04) outward.set(0, 1, 0);
      else outward.normalize().multiplyScalar(0.9).add(new THREE.Vector3(0, 0.3, 0)).normalize();
      toCamera.copy(cam.position).sub(c).normalize();
      const far = outward.dot(toCamera) < -0.15;
      if ((el.dataset.far === "1") !== far) el.dataset.far = far ? "1" : "";
    });
  });
  const markerRef = (zone: string) => (el: HTMLElement | null) => {
    if (el) markerEls.current.set(zone, el);
    else markerEls.current.delete(zone);
  };

  const markByZone = useMemo(() => new Map(marks.map((m) => [m.zone, m])), [marks]);
  const selected = useMemo(() => new Set(selectedZones), [selectedZones]);
  const hovered = useMemo(() => new Set(hoverZones), [hoverZones]);
  // Anchor spots: the zones this model has no mesh for and no finding on,
  // so they can still be clicked ("Not detected" + Add as damage).
  const anchorSpots = showAnchors ? noMeshZones.filter((z) => !markByZone.has(z)) : [];
  const size = box.getSize(new THREE.Vector3());

  return (
    <>
      <primitive
        object={root}
        onClick={(e: ThreeEvent<MouseEvent>) => {
          e.stopPropagation();
          const hit = resolveHit(e);
          if (hit) onZoneClick(hit.zone, hit.approx);
        }}
        onPointerMove={(e: ThreeEvent<PointerEvent>) => {
          e.stopPropagation();
          setHover(resolveHit(e)?.zone ?? null);
        }}
        onPointerOut={() => setHover(null)}
      />
      {marks.map((m) => {
        const p = placements(m.zone);
        return (
          <Html key={m.zone} position={[p.center.x, p.center.y + 0.12, p.center.z]} center occlude={false} zIndexRange={[10, 0]}>
            <ZoneMarker
              ref={markerRef(m.zone)}
              mark={m}
              approx={!p.fromMesh}
              selected={selected.has(m.zone)}
              hovered={hovered.has(m.zone)}
              onClick={() => onZoneClick(m.zone, !p.fromMesh)}
              onHover={(on) => onZoneHover?.(on ? m.zone : null)}
            />
          </Html>
        );
      })}
      {anchorSpots.map((zone) => {
        const p = placements(zone);
        return (
          <Html key={`a-${zone}`} position={[p.center.x, p.center.y, p.center.z]} center occlude={false} zIndexRange={[9, 0]}>
            <button
              ref={markerRef(zone)}
              type="button"
              className={`dmap-anchor ${selected.has(zone) ? "is-selected" : ""}`}
              aria-label={`${zoneLabel(zone)} (approx.)`}
              title={`${zoneLabel(zone)} (approx.)`}
              onClick={() => onZoneClick(zone, true)}
            />
          </Html>
        );
      })}
      {/* +Z is the front in every model (the front bumper sits at +Z after normalizing). */}
      <Html position={[0, 0.05, size.z / 2 + 0.35]} center distanceFactor={8} occlude={false} zIndexRange={[5, 0]}>
        <span className="blueprint-3d-axis-label">FRONT</span>
      </Html>
      <Html position={[0, 0.05, -size.z / 2 - 0.35]} center distanceFactor={8} occlude={false} zIndexRange={[5, 0]}>
        <span className="blueprint-3d-axis-label">REAR</span>
      </Html>
      <Html position={[size.x / 2 + 0.35, 0.05, 0]} center distanceFactor={8} occlude={false} zIndexRange={[5, 0]}>
        <span className="blueprint-3d-axis-label">LH</span>
      </Html>
      <Html position={[-size.x / 2 - 0.35, 0.05, 0]} center distanceFactor={8} occlude={false} zIndexRange={[5, 0]}>
        <span className="blueprint-3d-axis-label">RH</span>
      </Html>
    </>
  );
}
// No useGLTF.preload() here: a case shows one model, and useGLTF(modelUrl)
// above fetches just that one. Preloading at module load downloaded all of
// them on the first case page.

/** One marker per zone: the status's glyph, the row number when one listed
 * part is on it (else how many), "+n" for suggestions beside listed parts,
 * "?" for a side not known and ◇ for a part inside / behind the panel. */
function ZoneMarker({
  ref,
  mark,
  approx,
  selected,
  hovered,
  onClick,
  onHover,
}: {
  ref?: React.Ref<HTMLButtonElement>;
  mark: ZoneMark;
  approx: boolean;
  selected: boolean;
  hovered: boolean;
  onClick: () => void;
  onHover: (on: boolean) => void;
}) {
  const glyph = STATUS_GLYPHS[mark.lead];
  let text: string;
  if (mark.lead === "suggestion") text = mark.suggested > 1 ? String(mark.suggested) : "";
  else if (mark.lead === "dismissed") text = mark.dismissed > 1 ? String(mark.dismissed) : "";
  else text = mark.number !== null ? String(mark.number).padStart(2, "0") : String(mark.listed);
  const label = `${mark.label}${mark.sideUnknown ? " (side not known)" : ""}${approx ? " (approx.)" : ""}`;
  return (
    <button
      ref={ref}
      type="button"
      className={`dmap-marker is-${mark.lead} ${mark.number === null && mark.listed > 1 ? "is-count" : ""} ${selected ? "is-selected" : ""} ${hovered ? "is-hovered" : ""}`}
      aria-label={label}
      title={label}
      aria-pressed={selected}
      onClick={onClick}
      onPointerEnter={() => onHover(true)}
      onPointerLeave={() => onHover(false)}
    >
      {glyph && <span className="dmap-marker-glyph" aria-hidden="true">{glyph}</span>}
      {text && <span aria-hidden="true">{text}</span>}
      {mark.lead !== "suggestion" && mark.suggested > 0 && (
        <span className="dmap-marker-plus" aria-hidden="true">
          +{mark.suggested}
        </span>
      )}
      {mark.sideUnknown && (
        <span className="dmap-marker-q" aria-hidden="true">
          ?
        </span>
      )}
      {mark.standIn && (
        <span className="dmap-marker-in" aria-hidden="true">
          ◇
        </span>
      )}
    </button>
  );
}

/** Dev only: counts drawn frames for the test page's timing. */
function DevFrames() {
  const { gl, camera } = useThree();
  useEffect(() => {
    const hook = devHook();
    if (hook) {
      hook.gl = gl;
      hook.camera = camera;
    }
  }, [gl, camera]);
  useFrame(() => {
    const hook = devHook();
    if (!hook) return;
    hook.frames += 1;
    hook.lastFrameAt = performance.now();
  });
  return null;
}

// =============================================================================
// The 3D map

export type DamageBlueprint3DProps = {
  archetype: VehicleArchetype;
  /** One per zone (lib/damageLinks zoneMarks). Keep the array's identity
   * until its content changes (marksSignature): a new one repaints. */
  marks: ZoneMark[];
  /** Outlined, the rest dimmed. */
  selectedZones: string[];
  /** Outlined softly; never moves the camera. */
  hoverZones: string[];
  camera: CameraRequest | null;
  onZoneClick: (zone: string, approx: boolean) => void;
  onZoneHover?: (zone: string | null) => void;
  /** Dots for the zones this model has no mesh for. Default true. */
  showAnchors?: boolean;
  /** Drag to rotate / pinch to zoom. Off on a phone until the person asks
   * ([Rotate]), so a finger on the map still scrolls the page. Default true. */
  interactive?: boolean;
  className?: string;
};

/** The 3D damage map: one Canvas for the life of the page (a click, a new
 * damage list or a hover never remounts it or resets the camera), frames
 * drawn on demand and none while scrolled away. */
export function DamageBlueprint3D({
  archetype,
  marks,
  selectedZones,
  hoverZones,
  camera,
  onZoneClick,
  onZoneHover,
  showAnchors = true,
  interactive = true,
  className,
}: DamageBlueprint3DProps) {
  const accent = useThemeColor("--border-glow", "#38bdf8");
  const ink = useThemeColor("--text-primary", "#f8fafc");
  const modelUrl = MODEL_PATHS[archetype] || MODEL_PATHS.sedan;
  const controlsRef = useRef<any>(null);
  const [focus, setFocus] = useState<FocusState | null>(null);

  // Stable identity (setFocus never changes): VehicleModel's camera effect
  // must not re-run because a new function arrived.
  const handleFocusRequest = useCallback((state: FocusState) => setFocus(state), []);
  // The orbit's target is the rig's to move; the controls only take it when
  // they mount (a target prop that changed with each focus would snap the
  // view before the rig's move began).
  // eslint-disable-next-line react-hooks/exhaustive-deps -- read once per mount of the controls
  const controlsTarget = useMemo(() => (focus?.target ?? HOME.target).toArray(), [interactive]);

  // Stop drawing while the stage is scrolled out of view or hidden.
  const stageRef = useRef<HTMLDivElement>(null);
  const [onScreen, setOnScreen] = useState(true);
  useEffect(() => {
    const el = stageRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => setOnScreen(entries[entries.length - 1].isIntersecting), { rootMargin: "200px 0px" });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div className={`blueprint-stage-3d ${className ?? ""}`} ref={stageRef}>
      <Canvas camera={{ position: HOME.position.toArray(), fov: FOV }} dpr={[1, 2]} frameloop={onScreen ? "demand" : "never"}>
        <React.Suspense
          fallback={
            <Html center>
              <div className="blueprint-3d-loading" style={{ color: "var(--text-secondary)", fontSize: 12 }}>
                Loading 3D asset…
              </div>
            </Html>
          }
        >
          <VehicleModel
            key={archetype}
            archetype={archetype}
            modelUrl={modelUrl}
            accent={accent}
            ink={ink}
            marks={marks}
            selectedZones={selectedZones}
            hoverZones={hoverZones}
            camera={camera}
            showAnchors={showAnchors}
            onZoneClick={onZoneClick}
            onZoneHover={onZoneHover}
            onFocusRequest={handleFocusRequest}
          />

          <Grid
            position={[0, 0, 0]}
            args={[8, 8]}
            cellSize={0.5}
            cellThickness={0.4}
            cellColor={accent}
            sectionSize={2}
            sectionThickness={0.6}
            sectionColor={accent}
            fadeDistance={9}
            fadeStrength={1.5}
            infiniteGrid={false}
          />
        </React.Suspense>

        <CameraRig focus={focus} controlsRef={controlsRef} />
        {interactive && (
          <OrbitControls
            ref={controlsRef}
            makeDefault
            enablePan={false}
            minDistance={0.8}
            maxDistance={11}
            maxPolarAngle={Math.PI / 2 - 0.02}
            target={controlsTarget}
          />
        )}
        {process.env.NODE_ENV !== "production" && <DevFrames />}
      </Canvas>

      {marks.length === 0 && <div className="blueprint-3d-empty">No damaged parts recorded yet</div>}
    </div>
  );
}
