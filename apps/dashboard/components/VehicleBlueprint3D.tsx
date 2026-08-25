"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Grid, Html, OrbitControls, useGLTF } from "@react-three/drei";
import type { DamageSummaryItem } from "@/lib/api";
import { resolveZones, type ZoneResolution } from "@/lib/vehicleZones";
import { severityClass } from "./StudioApp";

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
  { key: "front_bumper", label: "Front Bumper", match: [/^bumper-front/i] },
  { key: "rear_bumper", label: "Rear Bumper", match: [/^bumper-rear/i] },
  { key: "bonnet", label: "Bonnet/Hood", match: [/^hood_/i] },
  { key: "windscreen", label: "Windscreen", match: [/^windshield_/i, /^windshield-grill/i] },
  { key: "rear_glass", label: "Rear Glass", match: [/^glass-rear/i] },
  { key: "tailgate", label: "Trunk", match: [/^trunk_/i] },
  { key: "l_headlamp", label: "Left Headlamp", match: [/^headlights?-.*-l[_.]/i] },
  { key: "r_headlamp", label: "Right Headlamp", match: [/^headlights?-.*-r[_.]/i] },
  { key: "l_taillamp", label: "Left Taillamp", match: [/^taillight-.*-l[_.]/i] },
  { key: "r_taillamp", label: "Right Taillamp", match: [/^taillight-.*-r[_.]/i] },
  { key: "l_door_front", label: "Left Front Door", match: [/^door-front-l_/i] },
  { key: "r_door_front", label: "Right Front Door", match: [/^door-front-r_/i] },
  { key: "l_door_rear", label: "Left Rear Door", match: [/^door-rear-l_/i] },
  { key: "r_door_rear", label: "Right Rear Door", match: [/^door-rear-r_/i] },
  { key: "l_fender", label: "Left Fender", match: [/^fender-front-l_/i, /^wheel-fender-l_/i] },
  { key: "r_fender", label: "Right Fender", match: [/^fender-front-r_/i, /^wheel-fender-r_/i] },
  { key: "l_mirror", label: "Left Mirror", match: [/^side-mirror-l/i] },
  { key: "r_mirror", label: "Right Mirror", match: [/^side-mirror-r/i] },
];

function partName(mesh: THREE.Object3D): string {
  return mesh.parent ? mesh.parent.name : mesh.name;
}

function findCarMeshes(root: THREE.Object3D, patterns: RegExp[]): THREE.Mesh[] {
  const found: THREE.Mesh[] = [];
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (!mesh.isMesh || !mesh.visible) return;
    if (patterns.some((re) => re.test(partName(mesh)))) found.push(mesh);
  });
  return found;
}

// Vehicle body types this app currently distinguishes: a real sedan model
// for cars/SUVs (the SLK 3063 Z Vezel and the Civics in real data both fall
// here), and a procedural van silhouette -- no free/realistic van model has
// been sourced yet, so that stays an honest approximation, not a real model.
const VAN_PATTERN = /\b(hiace|van|alphard|starex|caravelle|transporter|kombi|mpv)\b/i;
function detectBodyType(vehicleText: string): "car" | "van" {
  return VAN_PATTERN.test(vehicleText) ? "van" : "car";
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

function CarModel({
  color,
  damageEntries,
  zoneResolutions,
  highlightedIdx,
  onZoneClick,
  onFocusRequest,
}: {
  color: string;
  damageEntries: DamageSummaryItem[];
  zoneResolutions: (ZoneResolution | null)[];
  highlightedIdx: number | null;
  onZoneClick: (idx: number) => void;
  onFocusRequest: (center: THREE.Vector3, size: THREE.Vector3) => void;
}) {
  const gltf = useGLTF("/assets/generic-sedan/sedan.glb");

  // Filter interior/mechanical clutter, center on the ground plane, and
  // clone once per mount -- computed from the actual loaded geometry via
  // Box3, never a hand-typed offset (the file's own accessor min/max
  // metadata didn't hold up under scrutiny when checked directly).
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
  // resolved to it (several granular real part names can share one zone,
  // e.g. "Rear bumper fascia" + "Reverse sensor" both -> rear_bumper), and
  // zone key -> the shared badge number from resolveZones() -- the same
  // number shown in the Damage & Parts Checklist table, so a marker on the
  // model and a row in the table always visibly match.
  const { zoneSeverity, zoneBadge } = useMemo(() => {
    const severity = new Map<string, string>();
    const badge = new Map<string, number>();
    damageEntries.forEach((item, idx) => {
      const res = zoneResolutions[idx];
      if (!res) return;
      if (!badge.has(res.key)) badge.set(res.key, res.badgeNumber);
      const sev = severityClass(item.severity);
      const existing = severity.get(res.key);
      if (!existing || (SEVERITY_RANK[sev] ?? 0) > (SEVERITY_RANK[existing] ?? 0)) severity.set(res.key, sev);
    });
    return { zoneSeverity: severity, zoneBadge: badge };
  }, [damageEntries, zoneResolutions]);

  useEffect(() => {
    root.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      if (!mesh.isMesh || !mesh.visible) return;
      mesh.material = new THREE.MeshBasicMaterial({ color, wireframe: true, transparent: true, opacity: 0.85 });
    });
    CAR_PARTS_REAL.forEach((zone) => {
      const sev = zoneSeverity.get(zone.key);
      if (!sev) return;
      const hex = severityColor(sev);
      if (!hex) return;
      findCarMeshes(root, zone.match).forEach((m) => {
        m.material = new THREE.MeshBasicMaterial({ color: hex, transparent: true, opacity: 0.7, side: THREE.DoubleSide });
      });
    });
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
    const meshes = findCarMeshes(root, zone.match);
    if (!meshes.length) return;
    const partBox = new THREE.Box3();
    meshes.forEach((m) => partBox.expandByObject(m));
    onFocusRequest(partBox.getCenter(new THREE.Vector3()), partBox.getSize(new THREE.Vector3()));
  }, [highlightedIdx, zoneResolutions, root, onFocusRequest]);

  const zoneIdxLookup = useMemo(() => {
    const map = new Map<string, number>();
    zoneResolutions.forEach((res, idx) => {
      if (res && !map.has(res.key)) map.set(res.key, idx);
    });
    return map;
  }, [zoneResolutions]);

  const handleClick = (e: any) => {
    e.stopPropagation();
    const zone = CAR_PARTS_REAL.find((z) => z.match.some((re) => re.test(partName(e.object))));
    if (!zone) return;
    const idx = zoneIdxLookup.get(zone.key);
    if (idx !== undefined) onZoneClick(idx);
  };

  return (
    <>
      <primitive object={root} onClick={handleClick} />
      {CAR_PARTS_REAL.map((zone) => {
        const sev = zoneSeverity.get(zone.key);
        if (!sev) return null;
        const meshes = findCarMeshes(root, zone.match);
        if (!meshes.length) return null;
        const partBox = new THREE.Box3();
        meshes.forEach((m) => partBox.expandByObject(m));
        const center = partBox.getCenter(new THREE.Vector3());
        const badgeNumber = zoneBadge.get(zone.key);
        const idx = zoneIdxLookup.get(zone.key);
        return (
          <Html key={zone.key} position={[center.x, center.y + 0.14, center.z]} center occlude={false} zIndexRange={[10, 0]}>
            <button
              type="button"
              className={`hotspot-beacon-3d ${sev === "severe" ? "severe-spot" : ""} ${highlightedIdx === idx ? "active-spot" : ""}`}
              title={zone.label}
              onClick={() => idx !== undefined && onZoneClick(idx)}
            >
              {badgeNumber !== undefined ? String(badgeNumber).padStart(2, "0") : "?"}
            </button>
          </Html>
        );
      })}
      <Html position={[0, 0.05, -box.getSize(new THREE.Vector3()).z / 2 - 0.35]} center distanceFactor={8} occlude={false}>
        <span className="blueprint-3d-axis-label">FRONT</span>
      </Html>
      <Html position={[0, 0.05, box.getSize(new THREE.Vector3()).z / 2 + 0.35]} center distanceFactor={8} occlude={false}>
        <span className="blueprint-3d-axis-label">REAR</span>
      </Html>
    </>
  );
}
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

function VanModel({ color }: { color: string }) {
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
}

const CAR_HOME: FocusState = { position: new THREE.Vector3(3.6, 2.4, 3.9), target: new THREE.Vector3(0, 0.55, 0) };
const VAN_HOME: FocusState = { position: new THREE.Vector3(4.2, 2.6, 4.6), target: new THREE.Vector3(0, 0.55, 0) };

export function VehicleBlueprint3D({ damageEntries, onHotspotClick, highlightedDamageIndex, vehicleName }: Props) {
  const accent = useThemeColor("--border-glow", "#38bdf8");
  const bodyType = detectBodyType(vehicleName);
  const controlsRef = useRef<any>(null);
  const [focus, setFocus] = useState<FocusState | null>(null);
  const home = bodyType === "van" ? VAN_HOME : CAR_HOME;

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
        {bodyType === "car" ? (
          <CarModel
            color={accent}
            damageEntries={damageEntries}
            zoneResolutions={zoneResolutions}
            highlightedIdx={highlightedDamageIndex}
            onZoneClick={handleZoneClick}
            onFocusRequest={handleFocusRequest}
          />
        ) : (
          <VanModel color={accent} />
        )}

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
        <button type="button" className="blueprint-3d-back-btn" onClick={() => setFocus(null)}>
          ◀ Back to full view
        </button>
      )}
      <div className="blueprint-3d-hint">🖱️ drag to rotate &bull; scroll to zoom &bull; click a part to inspect</div>
    </div>
  );
}
