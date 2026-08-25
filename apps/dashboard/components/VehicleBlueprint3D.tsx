"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Grid, Html, OrbitControls, useGLTF } from "@react-three/drei";
import type { DamageSummaryItem } from "@/lib/api";
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
// LAYER 1: free-text damage_summary.part -> zone key.
//
// Same keyword-regex philosophy as the 2D blueprint's old ZONE_RULES and the
// previous coordinate-based ZONE_RULES_3D (see git history) -- checked in
// order, most specific first, never an exact-string lookup since real part
// text ("Rear bumper fascia", "Tail gate lock striker") never matches a
// fixed vocabulary. The difference here: this resolves to a *zone key*
// string that Layer 2 (below) maps to real mesh names in the sedan model,
// not a raw coordinate.
//
// Left/right ambiguity is handled differently from the old system on
// purpose: the old coordinate placement defaulted to a generic point when
// side wasn't stated (an approximate marker, never claimed to BE a specific
// panel). This system recolors an actual, specific, real body panel red --
// so guessing a side when the source text doesn't say one would mean
// falsely claiming "the right door is damaged" when the report never said
// that. Rather than risk that, an unresolvable side returns null (no 3D
// highlight for that item -- it still shows in the regular damage table).
// Front/rear ambiguity is lower-stakes (same side, adjacent zone) so that
// still defaults sensibly, consistent with the existing codebase's
// "sensible default over gap" precedent for that specific kind of gap.
// =============================================================================
interface CategoryRule {
  test: RegExp;
  zoneKey?: string; // set for rules that resolve directly, no side/depth needed
  base?: string; // set for rules needing side and/or depth resolution
  paired?: boolean;
  depthVar?: boolean;
}

const CATEGORY_RULES: CategoryRule[] = [
  // Structural/rear-body items without their own visible panel in this
  // model -- the closest real, visible zone is the rear bumper area.
  { test: /rear\s*(end|body)?\s*panel|floor\s*panel/i, zoneKey: "rear_bumper" },
  { test: /sensor|reverse/i, zoneKey: "rear_bumper" },
  { test: /plate/i, zoneKey: "rear_bumper" },
  { test: /tail\s*-?gate|\bboot\b|\btrunk\b/i, zoneKey: "tailgate" },
  { test: /rear.*(glass|window|screen)\b|back\s*glass/i, zoneKey: "rear_glass" },
  { test: /wind\s*-?screen|wind\s*-?shield/i, zoneKey: "windscreen" },
  { test: /\broof\b/i, zoneKey: "roof" },
  { test: /\bbonnet\b|\bhood\b/i, zoneKey: "bonnet" },
  { test: /\bbumper\b/i, base: "bumper", depthVar: true },
  { test: /head\s*-?lamp|head\s*-?light/i, base: "headlamp", paired: true },
  { test: /tail\s*-?lamp|tail\s*-?light|rear.*light|rear.*lamp/i, base: "taillamp", paired: true },
  { test: /mirror/i, base: "mirror", paired: true },
  { test: /fender|wheel\s*arch|wing/i, base: "fender", paired: true },
  { test: /\bdoor\b/i, base: "door", paired: true, depthVar: true },
];

const SIDE_LEFT = /\bleft\b/i;
const SIDE_RIGHT = /\bright\b/i;
const DEPTH_REAR = /rear|\bback\b/i;

function zoneKeyFor(part: string): string | null {
  const rule = CATEGORY_RULES.find((r) => r.test.test(part));
  if (!rule) return null;
  if (rule.zoneKey) return rule.zoneKey;

  let side: "l" | "r" | null = null;
  if (rule.paired) {
    if (SIDE_LEFT.test(part)) side = "l";
    else if (SIDE_RIGHT.test(part)) side = "r";
    if (!side) return null; // can't tell which real side -- never guess
  }
  const depth = rule.depthVar ? (DEPTH_REAR.test(part) ? "rear" : "front") : null;

  if (side && depth) return `${side}_${rule.base}_${depth}`; // l_door_front
  if (side) return `${side}_${rule.base}`; // l_headlamp
  if (depth) return `${depth}_${rule.base}`; // front_bumper
  return rule.base!;
}

// =============================================================================
// LAYER 2: zone key -> real mesh names in the sedan model.
//
// "Generic Sedan Car" by MMC Works (CC-BY 4.0, credited in
// public/assets/generic-sedan/CREDIT.txt) -- 124 separate real meshes
// (bumper-front, door-front-l, headlight-projector-l, etc), not a merged
// blob, so damage highlighting recolors the actual named part.
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
  onPartClick,
  highlightedIdx,
}: {
  color: string;
  damageEntries: DamageSummaryItem[];
  onPartClick: (zoneKey: string, worldPos: THREE.Vector3, size: THREE.Vector3) => void;
  highlightedIdx: number | null;
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
  // the index of the first item that resolved there -- used so the badge
  // shows the same numbering the damage table uses, rather than inventing
  // a separate labeling scheme.
  const { zoneSeverity, zoneFirstIdx } = useMemo(() => {
    const severity = new Map<string, string>();
    const firstIdx = new Map<string, number>();
    damageEntries.forEach((item, idx) => {
      const key = zoneKeyFor(item.part);
      if (!key) return;
      if (!firstIdx.has(key)) firstIdx.set(key, idx);
      const sev = severityClass(item.severity);
      const existing = severity.get(key);
      if (!existing || (SEVERITY_RANK[sev] ?? 0) > (SEVERITY_RANK[existing] ?? 0)) severity.set(key, sev);
    });
    return { zoneSeverity: severity, zoneFirstIdx: firstIdx };
  }, [damageEntries]);

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

  const handleClick = (e: any) => {
    e.stopPropagation();
    const zone = CAR_PARTS_REAL.find((z) => z.match.some((re) => re.test(partName(e.object))));
    if (!zone) return;
    const meshes = findCarMeshes(root, zone.match);
    const partBox = new THREE.Box3();
    meshes.forEach((m) => partBox.expandByObject(m));
    const center = partBox.getCenter(new THREE.Vector3());
    const size = partBox.getSize(new THREE.Vector3());
    onPartClick(zone.key, center, size);
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
        const size = partBox.getSize(new THREE.Vector3());
        const idx = zoneFirstIdx.get(zone.key);
        return (
          <Html key={zone.key} position={[center.x, center.y + 0.14, center.z]} center occlude={false} zIndexRange={[10, 0]}>
            <button
              type="button"
              className={`hotspot-beacon-3d ${sev === "severe" ? "severe-spot" : ""} ${highlightedIdx === idx ? "active-spot" : ""}`}
              title={zone.label}
              onClick={() => onPartClick(zone.key, center, size)}
            >
              {idx !== undefined ? String(idx + 1).padStart(2, "0") : "?"}
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

  // Reset any camera focus whenever the report itself changes (new
  // damageEntries identity), so switching reports never leaves a stale
  // zoom-in from the previous one.
  useEffect(() => setFocus(null), [damageEntries]);

  const handlePartClick = (zoneKey: string, center: THREE.Vector3, size: THREE.Vector3) => {
    // Jump to whichever real damage item actually resolved to this zone --
    // reuses the exact same click-to-photo/scroll-to-row behavior the 2D
    // hotspots already had, so this is additive, not a new interaction.
    const idx = damageEntries.findIndex((item) => zoneKeyFor(item.part) === zoneKey);
    if (idx >= 0) onHotspotClick(idx, damageEntries[idx]);

    const radius = Math.max(size.x, size.y, size.z, 0.25);
    const dist = Math.max(radius * 2.6, 0.9);
    const dir = (controlsRef.current ? controlsRef.current.object.position.clone().sub(controlsRef.current.target) : home.position.clone().sub(home.target)).normalize();
    setFocus({ position: center.clone().addScaledVector(dir, dist), target: center.clone() });
  };

  return (
    <div className="blueprint-stage-3d">
      <Canvas camera={{ position: home.position.toArray(), fov: 40 }} dpr={[1, 2]}>
        {bodyType === "car" ? (
          <CarModel color={accent} damageEntries={damageEntries} onPartClick={handlePartClick} highlightedIdx={highlightedDamageIndex} />
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
