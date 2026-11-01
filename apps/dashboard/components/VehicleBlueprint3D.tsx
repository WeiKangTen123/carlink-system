"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Grid, Html, OrbitControls, useGLTF } from "@react-three/drei";
import { clone as cloneWithSkeleton } from "three/examples/jsm/utils/SkeletonUtils.js";
import { ArrowLeft, RotateCcw } from "lucide-react";
import type { DamageSummaryItem } from "@/lib/api";
import { resolveZones, groupByZone, type ZoneResolution } from "@/lib/vehicleZones";
import { CAR_PARTS_REAL, isExcludedMesh, meshNamesMatch } from "@/lib/vehicleMeshRules";
import { severityClass } from "@/lib/caseFields";

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

const SEVERITY_COLOR: Record<string, string> = { severe: "#ef4444", moderate: "#f59e0b", minor: "#10b981" };
function severityColor(severity?: string | null): string | null {
  const cls = severityClass(severity);
  return SEVERITY_COLOR[cls] ?? null;
}
const SEVERITY_RANK: Record<string, number> = { minor: 1, moderate: 2, severe: 3 };

// Zone key -> real mesh names inside the five models (CAR_PARTS_REAL) and
// the interior/mechanical clutter kept hidden (CAR_EXCLUDE) live in
// lib/vehicleMeshRules.ts, shared with scripts/check-vehicle-mesh-rules.mjs,
// which checks every rule against the real model files. The free-text
// part -> zone-key mapping itself lives in lib/vehicleZones.ts.

// Fallback normalized 3D anchors (-1 to 1) for zones no mesh rule matched
// (joined panels, or the MPV's generically named meshes). Axes as measured
// in every model file: +Z = front, -Z = rear, +X = the car's LEFT (LH)
// side, +Y = up. X/Z are scaled by the model's box in markers below.
const ZONE_ANCHORS_NORMALIZED: Record<string, [number, number, number]> = {
  front_bumper: [0, 0.28, 0.98],
  bonnet: [0, 0.65, 0.65],
  windscreen: [0, 0.88, 0.35],
  roof: [0, 0.98, -0.05],
  rear_glass: [0, 0.88, -0.45],
  tailgate: [0, 0.65, -0.95],
  rear_bumper: [0, 0.28, -0.98],
  l_door_front: [0.96, 0.55, 0.25],
  r_door_front: [-0.96, 0.55, 0.25],
  l_door_rear: [0.96, 0.55, -0.3],
  r_door_rear: [-0.96, 0.55, -0.3],
  underbody: [0, 0.1, 0],
  front_grill: [0, 0.42, 0.98],
  l_headlamp: [0.68, 0.52, 0.92],
  r_headlamp: [-0.68, 0.52, 0.92],
  l_taillamp: [0.68, 0.65, -0.92],
  r_taillamp: [-0.68, 0.65, -0.92],
  l_wheel_front: [0.95, 0.25, 0.65],
  r_wheel_front: [-0.95, 0.25, 0.65],
  l_wheel_rear: [0.95, 0.25, -0.65],
  r_wheel_rear: [-0.95, 0.25, -0.65],
  l_fender: [0.95, 0.55, 0.65],
  r_fender: [-0.95, 0.55, 0.65],
  l_mirror: [1.02, 0.75, 0.4],
  r_mirror: [-1.02, 0.75, 0.4],
  l_door_glass_front: [0.95, 0.8, 0.25],
  r_door_glass_front: [-0.95, 0.8, 0.25],
  l_door_glass_rear: [0.95, 0.8, -0.3],
  r_door_glass_rear: [-0.95, 0.8, -0.3],
};

/** The three names a rule can match for one mesh: its own, its parent's
 * and its grandparent's. The models differ in where the descriptive name
 * sits -- the SUV names the mesh itself (parent unnamed), Sketchfab exports
 * name a wrapper Group one level up ("Object_N" meshes under
 * "door-front-l_16"), and the sedan's wheel corner sits two levels up.
 * Empty strings where a level is missing. */
function meshNames(obj: THREE.Object3D): string[] {
  return [obj.name || "", obj.parent?.name || "", obj.parent?.parent?.name || ""];
}

function isExcluded(obj: THREE.Object3D): boolean {
  return isExcludedMesh(meshNames(obj));
}

function findCarMeshes(root: THREE.Object3D, patterns: RegExp[], includeHidden = false): THREE.Mesh[] {
  const found: THREE.Mesh[] = [];
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (!mesh.isMesh) return;
    if (!mesh.visible && !includeHidden) return;
    if (meshNamesMatch(meshNames(mesh), patterns)) found.push(mesh);
  });
  return found;
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

export type VehicleArchetype = "suv" | "sedan" | "van" | "mpv" | "hatchback";

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

// Yaw that turns a model so its front faces +Z like the other four
// (measured from where each file keeps its front bumper / grille). Only
// the van is exported nose-along-+X.
const MODEL_YAW: Partial<Record<VehicleArchetype, number>> = { van: -Math.PI / 2 };

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

interface FocusState {
  position: THREE.Vector3;
  target: THREE.Vector3;
}

/** Smoothly lerps the camera/OrbitControls target toward `focus` (or back
 * to `home` when focus is null) instead of an abrupt cut -- reads as
 * "pushing in toward what you clicked". Pauses auto-rotate while focused. */
function CameraRig({
  focus,
  home,
  controlsRef,
}: {
  focus: FocusState | null;
  home: FocusState;
  controlsRef: React.RefObject<any>;
}) {
  const { camera } = useThree();
  const animRef = useRef<{ fromPos: THREE.Vector3; toPos: THREE.Vector3; fromTarget: THREE.Vector3; toTarget: THREE.Vector3; start: number } | null>(null);
  const lastFocusRef = useRef<FocusState | null>(null);

  useEffect(() => {
    if (focus === lastFocusRef.current) return;
    lastFocusRef.current = focus;
    const toState = focus ?? home;
    animRef.current = {
      fromPos: camera.position.clone(),
      toPos: toState.position.clone(),
      fromTarget: controlsRef.current ? controlsRef.current.target.clone() : home.target.clone(),
      toTarget: toState.target.clone(),
      start: performance.now(),
    };
    if (controlsRef.current) controlsRef.current.autoRotate = !focus;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus]);

  useFrame(() => {
    const anim = animRef.current;
    if (!anim) return;
    const t = Math.min(1, (performance.now() - anim.start) / 550);
    const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    camera.position.lerpVectors(anim.fromPos, anim.toPos, e);
    if (controlsRef.current) controlsRef.current.target.lerpVectors(anim.fromTarget, anim.toTarget, e);
    if (t >= 1) animRef.current = null;
  });

  return null;
}

function VehicleModel({
  modelUrl,
  archetype,
  color,
  damageEntries,
  zoneResolutions,
  highlightedIdx,
  selectedZone,
  onZoneClick,
  onZoneSelect,
  onFocusRequest,
}: {
  modelUrl: string;
  archetype: VehicleArchetype;
  color: string;
  damageEntries: DamageSummaryItem[];
  zoneResolutions: (ZoneResolution | null)[];
  highlightedIdx: number | null;
  selectedZone?: string | null;
  onZoneClick: (idx: number) => void;
  onZoneSelect?: (zoneKey: string | null) => void;
  onFocusRequest: (center: THREE.Vector3, size: THREE.Vector3) => void;
}) {
  const gltf = useGLTF(modelUrl);

  // One clone per mount, normalized from the loaded geometry itself (never
  // a hand-typed offset): hide interior clutter, turn the front to +Z, scale
  // to the archetype's real length, centre on x/z and sit the wheels on the
  // ground. cloneWithSkeleton() keeps the van's bones with its skinned
  // meshes; a plain clone() leaves them on the cached original, and a
  // skinned mesh draws where its bones are, so the van would ignore every
  // transform applied here.
  const { root, box } = useMemo(() => {
    const clone = cloneWithSkeleton(gltf.scene);
    clone.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      if (!mesh.isMesh) return;
      if (isExcluded(mesh)) mesh.visible = false;
      // A skinned mesh's culling sphere comes from bone state; never cull it.
      if ((mesh as THREE.SkinnedMesh).isSkinnedMesh) mesh.frustumCulled = false;
    });
    clone.rotation.set(0, MODEL_YAW[archetype] ?? 0, 0);
    const raw = measureVisible(clone);
    const rawLength = raw.max.z - raw.min.z;
    if (rawLength > 1e-6) clone.scale.setScalar(TARGET_LENGTH_M[archetype] / rawLength);
    const scaled = measureVisible(clone);
    const center = scaled.getCenter(new THREE.Vector3());
    clone.position.set(-center.x, -scaled.min.y, -center.z);
    return { root: clone, box: measureVisible(clone) };
  }, [gltf, archetype]);

  // Zone key -> worst severity among the real damage_summary items that
  // resolved to it. The mesh itself can only be one colour, so a zone
  // holding both a Severe and a Minor item paints the worse of the two.
  const zoneSeverity = useMemo(() => {
    const severity = new Map<string, string>();
    damageEntries.forEach((item, idx) => {
      const res = zoneResolutions[idx];
      if (!res) return;
      const sev = severityClass(item.severity);
      const existing = severity.get(res.key);
      if (!existing || (SEVERITY_RANK[sev] ?? 0) > (SEVERITY_RANK[existing] ?? 0)) severity.set(res.key, sev);
    });
    return severity;
  }, [damageEntries, zoneResolutions]);

  // Where a zone sits on this model: the box of its real meshes, or -- for
  // zones no rule maps here (most of the MPV's and the van's generically
  // named meshes) -- the normalized anchor scaled into the model's box, so
  // badges and zoom-to-part still land in roughly the right place. Same
  // identity for the life of the clone: the zoom effects below list it.
  const zonePlacement = useCallback(
    (zoneKey: string): { center: THREE.Vector3; size: THREE.Vector3; fromMesh: boolean } | null => {
      const zone = CAR_PARTS_REAL.find((z) => z.key === zoneKey);
      if (!zone) return null;
      const meshes = findCarMeshes(root, zone.match, true);
      if (meshes.length) {
        const partBox = boxOfMeshes(meshes);
        const center = partBox.getCenter(new THREE.Vector3());
        if (zoneKey === "roof") {
          center.y = Math.max(center.y, partBox.max.y - 0.05);
        }
        return { center, size: partBox.getSize(new THREE.Vector3()), fromMesh: true };
      }
      const normAnchor = ZONE_ANCHORS_NORMALIZED[zoneKey] || [0, 0.5, 0];
      const bCenter = box.getCenter(new THREE.Vector3());
      const bSize = box.getSize(new THREE.Vector3());
      const center = new THREE.Vector3(
        bCenter.x + normAnchor[0] * bSize.x * 0.46,
        box.min.y + normAnchor[1] * bSize.y,
        bCenter.z + normAnchor[2] * bSize.z * 0.46,
      );
      return { center, size: new THREE.Vector3(0.4, 0.3, 0.4), fromMesh: false };
    },
    [root, box],
  );

  // An anchored zone has no mesh box to frame; pull back to about a quarter
  // of the car instead of a 1 m close-up of unnamed wireframe.
  const focusOnZone = useCallback(
    (zoneKey: string) => {
      const placed = zonePlacement(zoneKey);
      if (!placed) return;
      onFocusRequest(placed.center, placed.fromMesh ? placed.size : new THREE.Vector3(1.1, 1.1, 1.1));
    },
    [zonePlacement, onFocusRequest],
  );

  const markers = useMemo(() => {
    const byZone = groupByZone(zoneResolutions);
    const out: { idx: number; label: string; badge: number; severity: string; pos: THREE.Vector3 }[] = [];

    byZone.forEach((itemIdxs, zoneKey) => {
      const placed = zonePlacement(zoneKey);
      if (!placed) return;
      const { center, size } = placed;

      const spread = Math.max(Math.min(size.x, size.z) * 0.32, 0.09);

      itemIdxs.forEach((itemIdx, n) => {
        const res = zoneResolutions[itemIdx]!;
        const item = damageEntries[itemIdx];
        const pos = center.clone();
        if (itemIdxs.length > 1) {
          const angle = (n * 137.5 * Math.PI) / 180; // golden angle
          const r = spread * Math.sqrt(n / itemIdxs.length + 0.15);
          pos.x += Math.cos(angle) * r;
          pos.z += Math.sin(angle) * r;
          pos.y += (n % 3) * 0.045; // slight stagger
        }
        out.push({
          idx: itemIdx,
          label: item.part,
          badge: res.badgeNumber,
          severity: severityClass(item.severity),
          pos,
        });
      });
    });
    return out;
  }, [damageEntries, zoneResolutions, zonePlacement]);

  useEffect(() => {
    // Re-assert the base hidden/visible state first. Without this, a zone
    // revealed for a previous report would stay revealed after the damage
    // list changes.
    //
    // CRITICAL GPU FIX: Reuse shared materials rather than instantiating 124+
    // individual materials per render cycle. This prevents WebGL shader program
    // explosion and memory exhaustion that crashed the browser tab.
    const baseMat = new THREE.MeshBasicMaterial({
      color,
      wireframe: true,
      transparent: true,
      opacity: 0.85,
    });
    const severityMats = new Map<string, THREE.MeshBasicMaterial>();

    root.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.visible = !isExcluded(mesh);
      if (mesh.visible) {
        mesh.material = baseMat;
      }
    });

    CAR_PARTS_REAL.forEach((zone) => {
      const sev = zoneSeverity.get(zone.key);
      if (!sev) return;
      const hex = severityColor(sev);
      if (!hex) return;

      let zoneMat = severityMats.get(hex);
      if (!zoneMat) {
        zoneMat = new THREE.MeshBasicMaterial({
          color: hex,
          transparent: true,
          opacity: 0.7,
          side: THREE.DoubleSide,
        });
        severityMats.set(hex, zoneMat);
      }

      findCarMeshes(root, zone.match, true).forEach((m) => {
        // Reveal a normally-hidden structural mesh when it's the damaged part.
        m.visible = true;
        m.material = zoneMat!;
      });
    });

    return () => {
      baseMat.dispose();
      severityMats.forEach((mat) => mat.dispose());
    };
  }, [root, color, zoneSeverity]);

  // Reacts to highlightedIdx regardless of WHERE the selection came from --
  // clicking a part/badge here, or clicking a row in the Damage & Parts
  // Checklist table (which sets the same prop one level up in
  // StudioApp.tsx) -- both funnel through here, so the camera zooms in the
  // same way either way instead of needing two separate code paths.
  const lastHandledRef = useRef<number | null>(null);
  useEffect(() => {
    if (highlightedIdx === null || highlightedIdx === lastHandledRef.current) return;
    lastHandledRef.current = highlightedIdx;
    const res = zoneResolutions[highlightedIdx];
    if (!res) return;
    focusOnZone(res.key);
  }, [highlightedIdx, zoneResolutions, focusOnZone]);

  // Synchronize 3D camera when an automotive zone is selected from the evidence strip.
  // Guarded like highlightedIdx above: zoom once per new selection, not on
  // every re-run of the effect (a re-run used to re-focus, which re-rendered,
  // which re-ran it again).
  const lastZoneRef = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedZone) {
      lastZoneRef.current = null;
      return;
    }
    if (selectedZone === lastZoneRef.current) return;
    lastZoneRef.current = selectedZone;
    let targetKey = selectedZone;
    if (selectedZone === "skeleton") targetKey = "underbody";
    if (selectedZone === "lighting") targetKey = "l_taillamp";
    if (selectedZone === "boot_floor") targetKey = "tailgate";
    const zone = CAR_PARTS_REAL.find((z) => z.key === targetKey || z.key.includes(targetKey));
    if (!zone) return;
    focusOnZone(zone.key);
  }, [selectedZone, focusOnZone]);

  const zoneIdxLookup = useMemo(() => {
    const map = new Map<string, number>();
    zoneResolutions.forEach((res, idx) => {
      if (res && !map.has(res.key)) map.set(res.key, idx);
    });
    return map;
  }, [zoneResolutions]);

  const handleClick = (e: any) => {
    e.stopPropagation();
    const names = meshNames(e.object);
    const zone = CAR_PARTS_REAL.find((z) => meshNamesMatch(names, z.match));
    if (!zone) return;
    onZoneSelect?.(zone.key);
    const idx = zoneIdxLookup.get(zone.key);
    if (idx !== undefined) onZoneClick(idx);
  };

  return (
    <>
      <primitive object={root} onClick={handleClick} />
      {markers.map((m) => (
        <Html key={m.idx} position={[m.pos.x, m.pos.y + 0.12, m.pos.z]} center occlude={false} zIndexRange={[10, 0]}>
          <button
            type="button"
            className={`hotspot-beacon-3d ${m.severity === "severe" ? "severe-spot" : ""} ${highlightedIdx === m.idx ? "active-spot" : ""}`}
            title={m.label}
            onClick={() => onZoneClick(m.idx)}
          >
            {String(m.badge).padStart(2, "0")}
          </button>
        </Html>
      ))}
      {/* +Z is the front in every model (the front bumper sits at +Z after normalizing). */}
      <Html position={[0, 0.05, box.getSize(new THREE.Vector3()).z / 2 + 0.35]} center distanceFactor={8} occlude={false}>
        <span className="blueprint-3d-axis-label">FRONT</span>
      </Html>
      <Html position={[0, 0.05, -box.getSize(new THREE.Vector3()).z / 2 - 0.35]} center distanceFactor={8} occlude={false}>
        <span className="blueprint-3d-axis-label">REAR</span>
      </Html>
    </>
  );
}
// No useGLTF.preload() here: a case shows one model, and useGLTF(modelUrl)
// above fetches just that one. Preloading at module load downloaded all of
// them on the first case page.

// =============================================================================
// Van (procedural) -- no free/realistic van model sourced yet, so this
// keeps the extruded side-profile silhouette technique: a real car
// outline (front bumper -> hood -> windshield -> roof -> rear glass ->
// tailgate -> rear bumper) extruded across the width, still honestly a
// schematic approximation rather than a real model like the car above.
// =============================================================================
const VAN_PROFILE: [number, number][] = [
  [-1.85, 0.12], [-1.9, 0.3], [-1.85, 0.5], [-1.8, 1.05],
  [-1.55, 1.55], [1.7, 1.58], [1.95, 1.15], [1.95, 0.5],
  [1.85, 0.12],
];
const VAN_WIDTH = 1.9;

const VAN_ZONE_ANCHORS: Record<string, [number, number, number]> = {
  front_bumper: [0, 0.3, -1.9],
  bonnet: [0, 0.7, -1.4],
  windscreen: [0, 1.25, -1.0],
  roof: [0, 1.62, 0.0],
  rear_glass: [0, 1.35, 1.6],
  tailgate: [0, 0.7, 1.9],
  rear_bumper: [0, 0.3, 1.95],
  l_door_front: [-0.96, 0.8, -0.6],
  r_door_front: [0.96, 0.8, -0.6],
  l_door_rear: [-0.96, 0.8, 0.6],
  r_door_rear: [0.96, 0.8, 0.6],
  underbody: [0, 0.15, 0.0],
  front_grill: [0, 0.5, -1.9],
  l_headlamp: [-0.75, 0.55, -1.85],
  r_headlamp: [0.75, 0.55, -1.85],
  l_taillamp: [-0.75, 0.75, 1.9],
  r_taillamp: [0.75, 0.75, 1.9],
  l_wheel_front: [-0.95, 0.4, -1.3],
  r_wheel_front: [0.95, 0.4, -1.3],
  l_wheel_rear: [-0.95, 0.4, 1.3],
  r_wheel_rear: [0.95, 0.4, 1.3],
  l_fender: [-0.96, 0.65, -1.3],
  r_fender: [0.96, 0.65, -1.3],
  l_mirror: [-1.05, 1.1, -0.9],
  r_mirror: [1.05, 1.1, -0.9],
  l_door_glass_front: [-0.96, 1.2, -0.6],
  r_door_glass_front: [0.96, 1.2, -0.6],
  l_door_glass_rear: [-0.96, 1.2, 0.6],
  r_door_glass_rear: [0.96, 1.2, 0.6],
};

function VanModel({
  color,
  damageEntries,
  zoneResolutions,
  highlightedIdx,
  selectedZone,
  onZoneClick,
  onZoneSelect,
  onFocusRequest,
}: {
  color: string;
  damageEntries: DamageSummaryItem[];
  zoneResolutions: (ZoneResolution | null)[];
  highlightedIdx: number | null;
  selectedZone?: string | null;
  onZoneClick: (idx: number) => void;
  onZoneSelect?: (zoneKey: string | null) => void;
  onFocusRequest?: (center: THREE.Vector3, size: THREE.Vector3) => void;
}) {
  const geo = useMemo(() => {
    const shape = new THREE.Shape();
    VAN_PROFILE.forEach(([z, y], i) => {
      const x = -z;
      if (i === 0) shape.moveTo(x, y);
      else shape.lineTo(x, y);
    });
    shape.closePath();
    const g = new THREE.ExtrudeGeometry(shape, { depth: VAN_WIDTH, bevelEnabled: false, curveSegments: 1 });
    g.translate(0, 0, -VAN_WIDTH / 2);
    g.rotateY(Math.PI / 2);
    return g;
  }, []);
  const wheelZ = 1.3, wheelX = 0.95, wheelR = 0.4;
  const wheelGeo = useMemo(() => new THREE.CylinderGeometry(wheelR, wheelR, 0.26, 12), []);

  const markers = useMemo(() => {
    const byZone = groupByZone(zoneResolutions);
    const out: { idx: number; label: string; badge: number; severity: string; pos: THREE.Vector3 }[] = [];

    byZone.forEach((itemIdxs, zoneKey) => {
      const anchor = VAN_ZONE_ANCHORS[zoneKey] || (
        zoneKey.includes("front") ? [0, 0.5, -1.5] :
        zoneKey.includes("rear") ? [0, 0.5, 1.5] :
        [0, 0.8, 0]
      );
      const center = new THREE.Vector3(...anchor);
      const spread = 0.16;

      itemIdxs.forEach((itemIdx, n) => {
        const res = zoneResolutions[itemIdx]!;
        const item = damageEntries[itemIdx];
        const pos = center.clone();
        if (itemIdxs.length > 1) {
          const angle = (n * 137.5 * Math.PI) / 180;
          const r = spread * Math.sqrt(n / itemIdxs.length + 0.15);
          pos.x += Math.cos(angle) * r;
          pos.z += Math.sin(angle) * r;
          pos.y += (n % 3) * 0.045;
        }
        out.push({
          idx: itemIdx,
          label: item.part,
          badge: res.badgeNumber,
          severity: severityClass(item.severity),
          pos,
        });
      });
    });
    return out;
  }, [damageEntries, zoneResolutions]);

  const lastHandledRef = useRef<number | null>(null);
  useEffect(() => {
    if (highlightedIdx === null || highlightedIdx === lastHandledRef.current) return;
    lastHandledRef.current = highlightedIdx;
    const res = zoneResolutions[highlightedIdx];
    if (!res || !onFocusRequest) return;
    const anchor = VAN_ZONE_ANCHORS[res.key] || [0, 0.8, 0];
    const center = new THREE.Vector3(...anchor);
    onFocusRequest(center, new THREE.Vector3(0.6, 0.6, 0.6));
  }, [highlightedIdx, zoneResolutions, onFocusRequest]);

  useEffect(() => {
    if (!selectedZone || !onFocusRequest) return;
    let targetKey = selectedZone;
    if (selectedZone === "skeleton") targetKey = "underbody";
    if (selectedZone === "lighting") targetKey = "l_taillamp";
    if (selectedZone === "boot_floor") targetKey = "tailgate";
    const anchor = VAN_ZONE_ANCHORS[targetKey];
    if (!anchor) return;
    const center = new THREE.Vector3(...anchor);
    onFocusRequest(center, new THREE.Vector3(0.6, 0.6, 0.6));
  }, [selectedZone, onFocusRequest]);

  return (
    <group>
      <lineSegments>
        <edgesGeometry args={[geo, 1]} />
        <lineBasicMaterial color={color} transparent opacity={0.9} />
      </lineSegments>
      <mesh geometry={geo}>
        <meshBasicMaterial color={color} transparent opacity={0.05} depthWrite={false} side={THREE.DoubleSide} />
      </mesh>
      {[[-wheelX, -wheelZ], [wheelX, -wheelZ], [-wheelX, wheelZ], [wheelX, wheelZ]].map(([x, z], i) => (
        <group key={i} position={[x, wheelR, z]} rotation={[0, 0, Math.PI / 2]}>
          <lineSegments>
            <edgesGeometry args={[wheelGeo]} />
            <lineBasicMaterial color={color} transparent opacity={0.9} />
          </lineSegments>
          <mesh geometry={wheelGeo}>
            <meshBasicMaterial color={color} transparent opacity={0.12} depthWrite={false} />
          </mesh>
        </group>
      ))}
      {markers.map((m) => (
        <Html key={m.idx} position={[m.pos.x, m.pos.y + 0.12, m.pos.z]} center occlude={false} zIndexRange={[10, 0]}>
          <button
            type="button"
            className={`hotspot-beacon-3d ${m.severity === "severe" ? "severe-spot" : ""} ${highlightedIdx === m.idx ? "active-spot" : ""}`}
            title={m.label}
            onClick={() => onZoneClick(m.idx)}
          >
            {String(m.badge).padStart(2, "0")}
          </button>
        </Html>
      ))}
      <Html position={[0, 0.06, -2.05]} center distanceFactor={8} occlude={false}>
        <span className="blueprint-3d-axis-label">FRONT</span>
      </Html>
      <Html position={[0, 0.06, 2.05]} center distanceFactor={8} occlude={false}>
        <span className="blueprint-3d-axis-label">REAR</span>
      </Html>
    </group>
  );
}

interface Props {
  damageEntries: DamageSummaryItem[];
  onHotspotClick: (idx: number, item: DamageSummaryItem) => void;
  highlightedDamageIndex: number | null;
  vehicleName: string;
  bodyType?: string | null;
  selectedZone?: string | null;
  onZoneSelect?: (zoneKey: string | null) => void;
}

const CAR_HOME: FocusState = { position: new THREE.Vector3(3.6, 2.4, 3.9), target: new THREE.Vector3(0, 0.55, 0) };
const VAN_HOME: FocusState = { position: new THREE.Vector3(4.2, 2.6, 4.6), target: new THREE.Vector3(0, 0.55, 0) };

export function VehicleBlueprint3D({
  damageEntries,
  onHotspotClick,
  highlightedDamageIndex,
  vehicleName,
  bodyType: statedBodyType,
  selectedZone,
  onZoneSelect,
}: Props) {
  const accent = useThemeColor("--border-glow", "#38bdf8");
  const archetype = detectBodyType(vehicleName, statedBodyType);
  const modelUrl = MODEL_PATHS[archetype] || MODEL_PATHS.sedan;
  const controlsRef = useRef<any>(null);
  const [focus, setFocus] = useState<FocusState | null>(null);
  const home = CAR_HOME;

  const zoneResolutions = useMemo(() => resolveZones(damageEntries), [damageEntries]);

  // Reset any camera focus whenever the report itself changes (new
  // damageEntries identity), so switching reports never leaves a stale
  // zoom-in from the previous one.
  useEffect(() => setFocus(null), [damageEntries]);

  const handleZoneClick = (idx: number) => {
    onHotspotClick(idx, damageEntries[idx]);
  };

  // Stable identity (setFocus never changes): VehicleModel's zoom effects
  // list this as a dependency, so a new function every render re-ran them.
  const handleFocusRequest = useCallback((center: THREE.Vector3, size: THREE.Vector3) => {
    const radius = Math.max(size.x, size.y, size.z, 0.25);
    const dist = Math.max(radius * 2.6, 0.9);
    // Approach from the direction the part actually sits relative to the
    // car's center (the model is always normalized to sit at x=0,z=0),
    // not from wherever the camera's *current* angle happens to be --
    // auto-rotate keeps spinning the camera whenever nothing is focused,
    // so "current angle" at the moment of a click is effectively random
    // and could zoom in from the wrong side of the car entirely. This way
    // the right door is always approached from the right, the front
    // bumper always from the front, regardless of click timing.
    const outward = new THREE.Vector3(center.x, 0, center.z);
    if (outward.lengthSq() < 0.0001) outward.set(0.5, 0, 0.7);
    outward.normalize();
    const dir = outward.multiplyScalar(0.82).add(new THREE.Vector3(0, 0.45, 0)).normalize();
    setFocus({ position: center.clone().addScaledVector(dir, dist), target: center.clone() });
  }, []);

  // Stop drawing frames (auto-rotate renders continuously) while the stage
  // is scrolled out of view or hidden; resume just before it comes back.
  const stageRef = useRef<HTMLDivElement>(null);
  const [onScreen, setOnScreen] = useState(true);
  useEffect(() => {
    const el = stageRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => setOnScreen(entries[entries.length - 1].isIntersecting),
      { rootMargin: "200px 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="blueprint-stage-3d" ref={stageRef}>
      <Canvas
        camera={{ position: home.position.toArray(), fov: 40 }}
        dpr={[1, 2]}
        frameloop={onScreen ? "always" : "never"}
      >
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
            color={accent}
            damageEntries={damageEntries}
            zoneResolutions={zoneResolutions}
            highlightedIdx={highlightedDamageIndex}
            selectedZone={selectedZone}
            onZoneClick={handleZoneClick}
            onZoneSelect={onZoneSelect}
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

        <CameraRig focus={focus} home={home} controlsRef={controlsRef} />
        <OrbitControls
          ref={controlsRef}
          makeDefault
          enablePan={false}
          minDistance={0.8}
          maxDistance={9}
          maxPolarAngle={Math.PI / 2 - 0.02}
          autoRotate
          autoRotateSpeed={0.6}
          target={home.target.toArray()}
        />
      </Canvas>

      {damageEntries.length === 0 && <div className="blueprint-3d-empty">No damaged parts recorded yet</div>}
      {focus && (
        <button
          type="button"
          className="blueprint-3d-back-btn"
          onClick={() => setFocus(null)}
          style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
        >
          <ArrowLeft style={{ width: 12, height: 12 }} /> Back to full view
        </button>
      )}
      <div className="blueprint-3d-hint" style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 6 }}>
        <RotateCcw style={{ width: 11, height: 11 }} /> Drag to rotate • Scroll to zoom • Click a part to inspect
      </div>
    </div>
  );
}
