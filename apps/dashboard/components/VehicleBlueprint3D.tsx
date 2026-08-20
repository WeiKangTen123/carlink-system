"use client";

import React, { useEffect, useMemo, useState } from "react";
import * as THREE from "three";
import { Canvas } from "@react-three/fiber";
import { Grid, Html, OrbitControls } from "@react-three/drei";
import type { DamageSummaryItem } from "@/lib/api";
import { severityClass } from "./StudioApp";

/** Same idea as the old 2D blueprint's ZONE_RULES (see StudioApp.tsx git
 * history) -- keyword matching against real, free-text part names, not an
 * exact-string lookup -- but now resolving to a real 3D point on the car
 * instead of a flat top-down pixel position. Axes: x = left(-)/right(+),
 * y = up, z = front(-)/rear(+).
 *
 * `paired` marks parts that physically exist on both sides (doors,
 * mirrors, wheels...) -- for those, an explicit "left"/"right" in the part
 * text places the marker on that literal side; `depthVar` marks parts that
 * can be front or back (fenders, wheels, doors...) -- an explicit
 * "front"/"rear"/"back" flips z accordingly. Absent that explicit text,
 * the rule's own base position is used as-is (never guessed from make/
 * model or region-specific driver-side conventions) -- same "approximate
 * zone, honestly labeled" placement philosophy as the 2D version. */
const ZONE_RULES_3D: {
  test: RegExp;
  zone: { x: number; y: number; z: number };
  paired?: boolean;
  depthVar?: boolean;
}[] = [
  { test: /tail\s*-?gate|\bboot\b|\btrunk\b/i, zone: { x: 0, y: 0.62, z: 1.55 } },
  { test: /rear\s*(end|body)?\s*panel|floor\s*panel/i, zone: { x: 0, y: 0.28, z: 1.3 } },
  { test: /rear.*bumper|bumper.*rear/i, zone: { x: 0, y: 0.32, z: 1.78 } },
  { test: /front.*bumper|bumper.*front/i, zone: { x: 0, y: 0.32, z: -1.78 } },
  { test: /\bbumper\b/i, zone: { x: 0, y: 0.32, z: 0 } },
  { test: /tail\s*-?lamp|tail\s*-?light|rear.*light|rear.*lamp/i, zone: { x: 0.72, y: 0.58, z: 1.65 }, paired: true },
  { test: /head\s*-?lamp|head\s*-?light/i, zone: { x: 0.72, y: 0.52, z: -1.65 }, paired: true },
  { test: /wind\s*-?screen|wind\s*-?shield/i, zone: { x: 0, y: 0.92, z: -0.9 } },
  { test: /\broof\b/i, zone: { x: 0, y: 1.15, z: 0 } },
  { test: /\bbonnet\b|\bhood\b/i, zone: { x: 0, y: 0.58, z: -1.3 } },
  { test: /mirror/i, zone: { x: 0.95, y: 0.85, z: -0.35 }, paired: true },
  { test: /fender|wheel\s*arch|wing/i, zone: { x: 0.86, y: 0.42, z: -1.05 }, paired: true, depthVar: true },
  { test: /wheel|\brim\b|tyre|\btire\b/i, zone: { x: 0.87, y: 0.34, z: -1.05 }, paired: true, depthVar: true },
  { test: /sensor|reverse/i, zone: { x: 0, y: 0.38, z: 1.72 } },
  { test: /plate/i, zone: { x: 0, y: 0.4, z: 1.8 }, depthVar: true },
  { test: /chassis|\bframe\b|subframe|undercarriage/i, zone: { x: 0, y: 0.08, z: 0 } },
  { test: /\bdoor\b/i, zone: { x: 0.9, y: 0.58, z: -0.5 }, paired: true, depthVar: true },
];
const DEFAULT_ZONE_3D = { x: 0, y: 0.5, z: 0 };

const SIDE_LEFT = /\bleft\b/i;
const SIDE_RIGHT = /\bright\b/i;
const DEPTH_REAR = /rear|\bback\b/i;

function zoneFor3D(part: string): { x: number; y: number; z: number } {
  const rule = ZONE_RULES_3D.find((r) => r.test.test(part));
  if (!rule) return DEFAULT_ZONE_3D;
  let { x, y, z } = rule.zone;
  if (rule.paired) {
    if (SIDE_LEFT.test(part)) x = -Math.abs(x);
    else if (SIDE_RIGHT.test(part)) x = Math.abs(x);
  }
  if (rule.depthVar && DEPTH_REAR.test(part)) z = -z;
  return { x, y, z };
}

/** 3D analogue of the 2D blueprint's spreadZones -- fans out markers that
 * land on the exact same point (e.g. several distinct real sub-components
 * with no left/right/front/rear stated) instead of stacking them, using
 * the same golden-angle trick so the fan never lines up along one axis. */
function spreadZones3D<T>(items: T[], zoneOf: (item: T) => { x: number; y: number; z: number }) {
  const seen = new Map<string, number>();
  return items.map((item) => {
    const base = zoneOf(item);
    const key = `${base.x.toFixed(2)},${base.y.toFixed(2)},${base.z.toFixed(2)}`;
    const idx = seen.get(key) ?? 0;
    seen.set(key, idx + 1);
    if (idx === 0) return base;
    const angle = (idx * 137.5 * Math.PI) / 180;
    const radius = 0.14 + Math.floor(idx / 6) * 0.09;
    return {
      x: base.x + Math.cos(angle) * radius,
      y: base.y + Math.sin(angle) * radius * 0.4,
      z: base.z + Math.sin(angle) * radius,
    };
  });
}

/** Reads a CSS custom property off <html> and keeps it live across the
 * app's dark/white theme toggle (ThemeToggle.tsx flips data-theme with no
 * page reload) -- WebGL material colors are plain numbers, not CSS
 * `var()`, so unlike the old SVG blueprint (stroke="var(--border-glow)"),
 * this has to be re-read explicitly whenever the attribute changes. */
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

function EdgeBox({
  size,
  position,
  rotation,
  color,
}: {
  size: [number, number, number];
  position: [number, number, number];
  rotation?: [number, number, number];
  color: string;
}) {
  const geo = useMemo(() => new THREE.BoxGeometry(...size), size);
  return (
    <group position={position} rotation={rotation}>
      <lineSegments>
        <edgesGeometry args={[geo]} />
        <lineBasicMaterial color={color} transparent opacity={0.9} />
      </lineSegments>
      <mesh geometry={geo}>
        <meshBasicMaterial color={color} transparent opacity={0.05} depthWrite={false} />
      </mesh>
    </group>
  );
}

function EdgeWheel({ position, color }: { position: [number, number, number]; color: string }) {
  const geo = useMemo(() => new THREE.CylinderGeometry(0.34, 0.34, 0.26, 12), []);
  return (
    <group position={position} rotation={[0, 0, Math.PI / 2]}>
      <lineSegments>
        <edgesGeometry args={[geo]} />
        <lineBasicMaterial color={color} transparent opacity={0.9} />
      </lineSegments>
      <mesh geometry={geo}>
        <meshBasicMaterial color={color} transparent opacity={0.12} depthWrite={false} />
      </mesh>
    </group>
  );
}

function CarWireframe({ color }: { color: string }) {
  const wheelZ = 1.05;
  const wheelX = 0.87;
  return (
    <group>
      <EdgeBox size={[1.7, 0.45, 3.0]} position={[0, 0.3, 0]} color={color} />
      <EdgeBox size={[1.35, 0.5, 1.9]} position={[0, 0.8, -0.05]} color={color} />
      <EdgeBox size={[1.2, 0.03, 0.55]} position={[0, 0.72, -0.95]} rotation={[-0.5, 0, 0]} color={color} />
      <EdgeBox size={[1.2, 0.03, 0.55]} position={[0, 0.72, 0.85]} rotation={[0.5, 0, 0]} color={color} />
      <EdgeWheel position={[-wheelX, 0.34, -wheelZ]} color={color} />
      <EdgeWheel position={[wheelX, 0.34, -wheelZ]} color={color} />
      <EdgeWheel position={[-wheelX, 0.34, wheelZ]} color={color} />
      <EdgeWheel position={[wheelX, 0.34, wheelZ]} color={color} />

      <Html position={[0, 0.06, -1.95]} center distanceFactor={8} occlude={false}>
        <span className="blueprint-3d-axis-label">FRONT</span>
      </Html>
      <Html position={[0, 0.06, 1.95]} center distanceFactor={8} occlude={false}>
        <span className="blueprint-3d-axis-label">REAR</span>
      </Html>
    </group>
  );
}

interface Props {
  damageEntries: DamageSummaryItem[];
  onHotspotClick: (idx: number, item: DamageSummaryItem) => void;
  highlightedDamageIndex: number | null;
}

export function VehicleBlueprint3D({ damageEntries, onHotspotClick, highlightedDamageIndex }: Props) {
  const accent = useThemeColor("--border-glow", "#38bdf8");
  const zones = useMemo(
    () => spreadZones3D(damageEntries, (item) => zoneFor3D(item.part)),
    // damageEntries identity changes with the report, which is all this needs to react to
    [damageEntries]
  );

  return (
    <div className="blueprint-stage-3d">
      <Canvas camera={{ position: [3.4, 2.2, 3.9], fov: 40 }} dpr={[1, 2]}>
        <CarWireframe color={accent} />

        {damageEntries.map((item, idx) => {
          const zone = zones[idx];
          const severe = severityClass(item.severity) === "severe";
          return (
            <Html key={idx} position={[zone.x, zone.y, zone.z]} center occlude={false} zIndexRange={[10, 0]}>
              <button
                type="button"
                className={`hotspot-beacon-3d ${severe ? "severe-spot" : ""} ${highlightedDamageIndex === idx ? "active-spot" : ""}`}
                title={`${item.part}${item.damage_type ? " — " + item.damage_type : ""}`}
                onClick={() => onHotspotClick(idx, item)}
              >
                {String(idx + 1).padStart(2, "0")}
              </button>
            </Html>
          );
        })}

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

        <OrbitControls
          makeDefault
          enablePan={false}
          minDistance={2.6}
          maxDistance={7}
          maxPolarAngle={Math.PI / 2 - 0.02}
          autoRotate
          autoRotateSpeed={0.6}
          target={[0, 0.4, 0]}
        />
      </Canvas>

      {damageEntries.length === 0 && (
        <div className="blueprint-3d-empty">No damaged parts recorded yet</div>
      )}
      <div className="blueprint-3d-hint">🖱️ drag to rotate &bull; scroll to zoom</div>
    </div>
  );
}
