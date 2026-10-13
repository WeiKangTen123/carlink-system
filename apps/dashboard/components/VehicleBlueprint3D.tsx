"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Grid, Html, OrbitControls, useGLTF } from "@react-three/drei";
import { ArrowLeft, RotateCcw } from "lucide-react";
import type { DamageSummaryItem } from "@/lib/api";
import { resolveZones, groupByZone, type ZoneResolution } from "@/lib/vehicleZones";
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

// =============================================================================
// Zone key -> real mesh names in the sedan model.
//
// "Generic Sedan Car" by MMC Works (CC-BY 4.0, credited in
// public/assets/generic-sedan/CREDIT.txt) -- 124 separate real meshes
// (bumper-front, door-front-l, headlight-projector-l, etc), not a merged
// blob, so damage highlighting recolors the actual named part. The
// free-text-part -> zone-key mapping itself lives in lib/vehicleZones.ts,
// shared with the Damage & Parts Checklist table so both always agree on
// numbering.
//
// Sketchfab's Blender->glTF export puts the descriptive name on a wrapper
// Group one level up from the mesh itself (mesh nodes are generically
// named "Object_N") -- confirmed by inspecting the raw glTF node list
// directly. Every lookup here goes through the mesh's parent.
// =============================================================================
const CAR_EXCLUDE = /interior|dashboard|seat|steering-wheel|column-wheel|center-console|engine|escape-pipes|radiator|brake-|shock-absorber|spring|suspension-|frame-front|frame-middle|frame-rear|roof-pillars-interior/i;

const CAR_PARTS_REAL: { key: string; label: string; match: RegExp[] }[] = [
  { key: "front_bumper", label: "Front Bumper", match: [/^bumper-front/i, /^bumper.*front/i, /bumpers?\.smd/i] },
  { key: "rear_bumper", label: "Rear Bumper", match: [/^bumper-rear/i, /^bumper.*rear/i] },
  { key: "bonnet", label: "Bonnet/Hood", match: [/^hood/i, /^bonnet/i] },
  { key: "windscreen", label: "Windscreen", match: [/^windshield/i, /^windscreen/i, /^glass-windshield/i] },
  { key: "roof", label: "Roof", match: [/^roof/i, /^sedan-unibody/i, /^chassis-sunroof/i] },
  { key: "rear_glass", label: "Rear Glass", match: [/^glass-rear/i, /^rear.*glass/i] },
  { key: "tailgate", label: "Trunk / Tailgate", match: [/^trunk/i, /^tailgate/i, /^boot/i] },
  { key: "l_headlamp", label: "Left Headlamp", match: [/^headlights?-.*-l[_.]/i, /^headlight.*-(?:l_|left|l\b)/i, /^headlight.*left/i] },
  { key: "r_headlamp", label: "Right Headlamp", match: [/^headlights?-.*-r[_.]/i, /^headlight.*-(?:r_|right|r\b)/i, /^headlight.*right/i] },
  { key: "l_taillamp", label: "Left Taillamp", match: [/^taillight-.*-l[_.]/i, /^taillight.*-(?:l_|left|l\b)/i, /^taillight.*left/i] },
  { key: "r_taillamp", label: "Right Taillamp", match: [/^taillight-.*-r[_.]/i, /^taillight.*-(?:r_|right|r\b)/i, /^taillight.*right/i] },
  { key: "l_door_front", label: "Left Front Door", match: [/^door-front-(?:l_|left)/i, /^door.*front.*left/i] },
  { key: "r_door_front", label: "Right Front Door", match: [/^door-front-(?:r_|right)/i, /^door.*front.*right/i] },
  { key: "l_door_rear", label: "Left Rear Door", match: [/^door-rear-(?:l_|left)/i, /^door.*rear.*left/i] },
  { key: "r_door_rear", label: "Right Rear Door", match: [/^door-rear-(?:r_|right)/i, /^door.*rear.*right/i] },
  { key: "l_fender", label: "Left Fender", match: [/^fender-front-(?:l_|left)/i, /^wheel-fender-l_/i, /fender.*left/i, /SM_Fender.*F.*101/i] },
  { key: "r_fender", label: "Right Fender", match: [/^fender-front-(?:r_|right)/i, /^wheel-fender-r_/i, /fender.*right/i] },
  { key: "l_mirror", label: "Left Mirror", match: [/(?:side-)?mirror-(?:l|left)/i, /mirror.*left/i] },
  { key: "r_mirror", label: "Right Mirror", match: [/(?:side-)?mirror-(?:r|right)/i, /mirror.*right/i] },
  { key: "front_grill", label: "Front Grille", match: [/^front-grill/i, /^honeycomb-grill/i, /^bumper-front-grill-frame/i, /grill\.smd/i] },
  { key: "l_door_glass_front", label: "Left Front Door Glass", match: [/^door-front-window-glass-l_/i, /^glass-door-front-left/i] },
  { key: "r_door_glass_front", label: "Right Front Door Glass", match: [/^door-front-window-glass-r_/i, /^glass-door-front-right/i] },
  { key: "l_door_glass_rear", label: "Left Rear Door Glass", match: [/^door-rear-window-glass-l_/i, /^door-rear-trim-glass-l_/i, /^glass-door-rear-left/i] },
  { key: "r_door_glass_rear", label: "Right Rear Door Glass", match: [/^door-rear-trim-glass-r_/i, /^door-rear-window-glass-r_/i, /^glass-door-rear-right/i] },
  { key: "underbody", label: "Underbody / Chassis", match: [/^frame-front/i, /^frame-middle/i, /^frame-rear/i] },
  { key: "l_wheel_front", label: "Left Front Wheel", match: [/^Name-Wheel\.Ft\.L/i, /wheel.*fl/i, /wheel.*front.*left/i, /wheel-fl/i, /tire-low-fl/i, /hiace_wheel\.smd$/i] },
  { key: "r_wheel_front", label: "Right Front Wheel", match: [/^Name-Wheel\.Ft\.R/i, /wheel.*fr/i, /wheel.*front.*right/i, /wheel-fr/i, /tire-low-fr/i, /hiace_wheel\.smd\.001/i] },
  { key: "l_wheel_rear", label: "Left Rear Wheel", match: [/^Name-Wheel\.Bk\.L/i, /wheel.*rl/i, /wheel.*rear.*left/i, /wheel-rl/i, /tire-low-rl/i, /hiace_wheel\.smd\.002/i] },
  { key: "r_wheel_rear", label: "Right Rear Wheel", match: [/^Name-Wheel\.Bk\.R/i, /wheel.*rr/i, /wheel.*rear.*right/i, /wheel-rr/i, /tire-low-rr/i, /hiace_wheel\.smd\.003/i] },
];

// Fallback normalized 3D anchors (-1 to 1) for models where some panel meshes are joined
const ZONE_ANCHORS_NORMALIZED: Record<string, [number, number, number]> = {
  front_bumper: [0, 0.28, -0.98],
  bonnet: [0, 0.65, -0.65],
  windscreen: [0, 0.88, -0.35],
  roof: [0, 0.98, 0.05],
  rear_glass: [0, 0.88, 0.45],
  tailgate: [0, 0.65, 0.95],
  rear_bumper: [0, 0.28, 0.98],
  l_door_front: [-0.96, 0.55, -0.25],
  r_door_front: [0.96, 0.55, -0.25],
  l_door_rear: [-0.96, 0.55, 0.3],
  r_door_rear: [0.96, 0.55, 0.3],
  underbody: [0, 0.1, 0.0],
  front_grill: [0, 0.42, -0.98],
  l_headlamp: [-0.68, 0.52, -0.92],
  r_headlamp: [0.68, 0.52, -0.92],
  l_taillamp: [-0.68, 0.65, 0.92],
  r_taillamp: [0.68, 0.65, 0.92],
  l_wheel_front: [-0.95, 0.25, -0.65],
  r_wheel_front: [0.95, 0.25, -0.65],
  l_wheel_rear: [-0.95, 0.25, 0.65],
  r_wheel_rear: [0.95, 0.25, 0.65],
  l_fender: [-0.95, 0.55, -0.65],
  r_fender: [0.95, 0.55, -0.65],
  l_mirror: [-1.02, 0.75, -0.4],
  r_mirror: [1.02, 0.75, -0.4],
  l_door_glass_front: [-0.95, 0.8, -0.25],
  r_door_glass_front: [0.95, 0.8, -0.25],
  l_door_glass_rear: [-0.95, 0.8, 0.3],
  r_door_glass_rear: [0.95, 0.8, 0.3],
};

function partName(mesh: THREE.Object3D): string {
  return mesh.parent ? mesh.parent.name : mesh.name;
}

/** The wheel groups carry their corner ("Name-Wheel.Ft.L") one level above
 * the mesh's own parent, so lookups test the grandparent too. Every
 * pattern above is anchored with ^, so widening the search this way can't
 * make an existing body-panel rule match something new by accident. */
function partGroupName(mesh: THREE.Object3D): string {
  return mesh.parent?.parent ? mesh.parent.parent.name : "";
}

function findCarMeshes(root: THREE.Object3D, patterns: RegExp[], includeHidden = false): THREE.Mesh[] {
  const found: THREE.Mesh[] = [];
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (!mesh.isMesh) return;
    if (!mesh.visible && !includeHidden) return;
    const name = partName(mesh);
    const group = partGroupName(mesh);
    if (patterns.some((re) => re.test(name) || (group && re.test(group)))) found.push(mesh);
  });
  return found;
}

export type VehicleArchetype = "suv" | "sedan" | "van" | "mpv" | "hatchback";

export const MODEL_PATHS: Record<VehicleArchetype, string> = {
  suv: "/assets/vehicles/suv.glb",
  sedan: "/assets/vehicles/sedan.glb",
  van: "/assets/vehicles/van.glb",
  mpv: "/assets/vehicles/mpv.glb",
  hatchback: "/assets/vehicles/hatchback.glb",
};

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

  // Filter interior/mechanical clutter, center on the ground plane, and
  // clone once per mount -- computed from the actual loaded geometry via
  // Box3, never a hand-typed offset.
  const { root, box } = useMemo(() => {
    const clone = gltf.scene.clone(true);
    clone.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      if (!mesh.isMesh) return;
      if (CAR_EXCLUDE.test(partName(mesh))) mesh.visible = false;
    });
    const rawBox = new THREE.Box3().setFromObject(clone);
    const center = rawBox.getCenter(new THREE.Vector3());
    clone.position.set(-center.x, -rawBox.min.y, -center.z);
    const normalizedBox = new THREE.Box3().setFromObject(clone);
    return { root: clone, box: normalizedBox };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gltf]);

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

  const markers = useMemo(() => {
    const byZone = groupByZone(zoneResolutions);
    const out: { idx: number; label: string; badge: number; severity: string; pos: THREE.Vector3 }[] = [];

    byZone.forEach((itemIdxs, zoneKey) => {
      const zone = CAR_PARTS_REAL.find((z) => z.key === zoneKey);
      if (!zone) return;
      const meshes = findCarMeshes(root, zone.match, true);
      let center: THREE.Vector3;
      let size: THREE.Vector3;

      if (meshes.length) {
        const partBox = new THREE.Box3();
        meshes.forEach((m) => partBox.expandByObject(m));
        center = partBox.getCenter(new THREE.Vector3());
        if (zoneKey === "roof") {
          center.y = Math.max(center.y, partBox.max.y - 0.05);
        }
        size = partBox.getSize(new THREE.Vector3());
      } else {
        // Fallback for models with joined meshes: use normalized anchor scaled to bounding box
        const normAnchor = ZONE_ANCHORS_NORMALIZED[zoneKey] || [0, 0.5, 0];
        const bCenter = box.getCenter(new THREE.Vector3());
        const bSize = box.getSize(new THREE.Vector3());
        center = new THREE.Vector3(
          bCenter.x + (normAnchor[0] * bSize.x) * 0.46,
          box.min.y + normAnchor[1] * bSize.y,
          bCenter.z + (normAnchor[2] * bSize.z) * 0.46
        );
        size = new THREE.Vector3(0.4, 0.3, 0.4);
      }

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
  }, [damageEntries, zoneResolutions, root, box]);

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
      mesh.visible = !CAR_EXCLUDE.test(partName(mesh));
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
    const zone = CAR_PARTS_REAL.find((z) => z.key === res.key);
    if (!zone) return;
    const meshes = findCarMeshes(root, zone.match, true);
    if (!meshes.length) return;
    const partBox = new THREE.Box3();
    meshes.forEach((m) => partBox.expandByObject(m));
    const center = partBox.getCenter(new THREE.Vector3());
    if (zone.key === "roof") {
      center.y = Math.max(center.y, partBox.max.y - 0.05);
    }
    onFocusRequest(center, partBox.getSize(new THREE.Vector3()));
  }, [highlightedIdx, zoneResolutions, root, onFocusRequest]);

  // Synchronize 3D camera when an automotive zone is selected from the evidence strip
  useEffect(() => {
    if (!selectedZone) return;
    let targetKey = selectedZone;
    if (selectedZone === "skeleton") targetKey = "underbody";
    if (selectedZone === "lighting") targetKey = "l_taillamp";
    if (selectedZone === "boot_floor") targetKey = "tailgate";
    const zone = CAR_PARTS_REAL.find((z) => z.key === targetKey || z.key.includes(targetKey));
    if (!zone) return;
    const meshes = findCarMeshes(root, zone.match, true);
    if (!meshes.length) return;
    const partBox = new THREE.Box3();
    meshes.forEach((m) => partBox.expandByObject(m));
    const center = partBox.getCenter(new THREE.Vector3());
    if (zone.key === "roof") {
      center.y = Math.max(center.y, partBox.max.y - 0.05);
    }
    onFocusRequest(center, partBox.getSize(new THREE.Vector3()));
  }, [selectedZone, root, onFocusRequest]);

  const zoneIdxLookup = useMemo(() => {
    const map = new Map<string, number>();
    zoneResolutions.forEach((res, idx) => {
      if (res && !map.has(res.key)) map.set(res.key, idx);
    });
    return map;
  }, [zoneResolutions]);

  const handleClick = (e: any) => {
    e.stopPropagation();
    const name = partName(e.object);
    const group = partGroupName(e.object);
    const zone = CAR_PARTS_REAL.find((z) => z.match.some((re) => re.test(name) || (group && re.test(group))));
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
      <Html position={[0, 0.05, -box.getSize(new THREE.Vector3()).z / 2 - 0.35]} center distanceFactor={8} occlude={false}>
        <span className="blueprint-3d-axis-label">FRONT</span>
      </Html>
      <Html position={[0, 0.05, box.getSize(new THREE.Vector3()).z / 2 + 0.35]} center distanceFactor={8} occlude={false}>
        <span className="blueprint-3d-axis-label">REAR</span>
      </Html>
    </>
  );
}
useGLTF.preload(MODEL_PATHS.suv);
useGLTF.preload(MODEL_PATHS.sedan);
useGLTF.preload(MODEL_PATHS.van);
useGLTF.preload(MODEL_PATHS.mpv);
useGLTF.preload(MODEL_PATHS.hatchback);
useGLTF.preload("/assets/generic-sedan/sedan.glb");

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

  const handleFocusRequest = (center: THREE.Vector3, size: THREE.Vector3) => {
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
  };

  return (
    <div className="blueprint-stage-3d">
      <Canvas camera={{ position: home.position.toArray(), fov: 40 }} dpr={[1, 2]}>
        <React.Suspense
          fallback={
            <Html center>
              <div className="blueprint-3d-loading" style={{ color: "var(--text-secondary)", fontSize: 12 }}>
                Loading 3D asset&hellip;
              </div>
            </Html>
          }
        >
          <VehicleModel
            key={archetype}
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
        <RotateCcw style={{ width: 11, height: 11 }} /> Drag to rotate &bull; Scroll to zoom &bull; Click a part to inspect
      </div>
    </div>
  );
}
