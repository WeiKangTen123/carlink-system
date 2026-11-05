"use client";

import { useMemo } from "react";
import type { DamageSummaryItem } from "@/lib/api";
import { SEVERITY_RANK, severityClass } from "@/lib/caseFields";
import { resolveZones } from "@/lib/vehicleZones";

/** A small top view of the car under 5 POINT OF IMPACT, shaded where parts
 * were detected. Plain SVG (no WebGL): it has to draw on a phone and in a
 * print preview. The zones are the same ones the 3D blueprint resolves
 * (lib/vehicleZones.ts resolveZones), so the sketch and the model never
 * disagree about which panel a part sits on. Front at the top; the car's
 * left (LH) on the viewer's left, as when looking down on it. */

type Shape = { x: number; y: number; w: number; h: number; r?: number; label: string };

// viewBox 0 0 200 400. Doors and door glass share one shape per door: the
// sketch has no room to tell them apart and both mean "that door".
const ZONE_SHAPES: Record<string, Shape> = {
  front_bumper: { x: 44, y: 28, w: 112, h: 22, r: 10, label: "Front bumper" },
  front_grill: { x: 80, y: 34, w: 40, h: 12, r: 3, label: "Front grille" },
  l_headlamp: { x: 50, y: 32, w: 24, h: 13, r: 4, label: "Left headlamp" },
  r_headlamp: { x: 126, y: 32, w: 24, h: 13, r: 4, label: "Right headlamp" },
  bonnet: { x: 50, y: 54, w: 100, h: 70, r: 10, label: "Bonnet" },
  windscreen: { x: 54, y: 128, w: 92, h: 38, r: 8, label: "Front windscreen" },
  roof: { x: 54, y: 170, w: 92, h: 78, r: 6, label: "Roof" },
  rear_glass: { x: 54, y: 252, w: 92, h: 34, r: 8, label: "Rear windscreen" },
  tailgate: { x: 50, y: 290, w: 100, h: 54, r: 10, label: "Boot lid / tailgate" },
  rear_bumper: { x: 44, y: 348, w: 112, h: 22, r: 10, label: "Rear bumper" },
  l_taillamp: { x: 50, y: 352, w: 24, h: 13, r: 4, label: "Left tail lamp" },
  r_taillamp: { x: 126, y: 352, w: 24, h: 13, r: 4, label: "Right tail lamp" },
  l_fender: { x: 38, y: 56, w: 12, h: 70, r: 4, label: "Left fender / quarter panel" },
  r_fender: { x: 150, y: 56, w: 12, h: 70, r: 4, label: "Right fender / quarter panel" },
  l_door_front: { x: 38, y: 130, w: 12, h: 62, r: 3, label: "Left front door" },
  r_door_front: { x: 150, y: 130, w: 12, h: 62, r: 3, label: "Right front door" },
  l_door_rear: { x: 38, y: 194, w: 12, h: 62, r: 3, label: "Left rear door" },
  r_door_rear: { x: 150, y: 194, w: 12, h: 62, r: 3, label: "Right rear door" },
  l_door_glass_front: { x: 38, y: 130, w: 12, h: 62, r: 3, label: "Left front door glass" },
  r_door_glass_front: { x: 150, y: 130, w: 12, h: 62, r: 3, label: "Right front door glass" },
  l_door_glass_rear: { x: 38, y: 194, w: 12, h: 62, r: 3, label: "Left rear door glass" },
  r_door_glass_rear: { x: 150, y: 194, w: 12, h: 62, r: 3, label: "Right rear door glass" },
  l_mirror: { x: 26, y: 126, w: 11, h: 12, r: 3, label: "Left wing mirror" },
  r_mirror: { x: 163, y: 126, w: 11, h: 12, r: 3, label: "Right wing mirror" },
  l_wheel_front: { x: 28, y: 76, w: 10, h: 34, r: 4, label: "Left front wheel" },
  r_wheel_front: { x: 162, y: 76, w: 10, h: 34, r: 4, label: "Right front wheel" },
  l_wheel_rear: { x: 28, y: 290, w: 10, h: 34, r: 4, label: "Left rear wheel" },
  r_wheel_rear: { x: 162, y: 290, w: 10, h: 34, r: 4, label: "Right rear wheel" },
  underbody: { x: 76, y: 150, w: 48, h: 100, r: 6, label: "Underbody / chassis" },
};

// Drawn behind the shaded zones so the car reads as a car when nothing is shaded.
const OUTLINE_KEYS = [
  "front_bumper", "bonnet", "windscreen", "roof", "rear_glass", "tailgate", "rear_bumper",
  "l_fender", "r_fender", "l_door_front", "r_door_front", "l_door_rear", "r_door_rear",
  "l_mirror", "r_mirror", "l_wheel_front", "r_wheel_front", "l_wheel_rear", "r_wheel_rear",
];

export function ImpactSketch({ damageEntries, pointOfImpact }: { damageEntries: DamageSummaryItem[]; pointOfImpact?: string }) {
  // Zone -> worst severity among the parts on it (as the 3D model colours
  // its panels), and the parts the sketch has no panel for.
  const { shaded, elsewhere } = useMemo(() => {
    const resolutions = resolveZones(damageEntries);
    const shaded = new Map<string, { severity: string; parts: string[] }>();
    const elsewhere: string[] = [];
    damageEntries.forEach((item, idx) => {
      const key = resolutions[idx]?.key;
      if (!key || !ZONE_SHAPES[key]) {
        elsewhere.push(item.part);
        return;
      }
      const sev = severityClass(item.severity);
      const have = shaded.get(key);
      if (!have) shaded.set(key, { severity: sev, parts: [item.part] });
      else {
        have.parts.push(item.part);
        if ((SEVERITY_RANK[sev] ?? 0) > (SEVERITY_RANK[have.severity] ?? 0)) have.severity = sev;
      }
    });
    return { shaded, elsewhere };
  }, [damageEntries]);

  const shadedList = [...shaded.entries()];
  const description =
    shadedList.length === 0
      ? "No detected part sits on the sketch."
      : `Shaded: ${shadedList.map(([k]) => ZONE_SHAPES[k].label).join(", ")}.`;

  return (
    <div className="impact-sketch">
      <svg viewBox="0 0 200 400" role="img" aria-label={`Top view of the vehicle. ${description}`} className="impact-sketch-svg">
        <text x="100" y="14" textAnchor="middle" className="impact-sketch-axis">
          FRONT
        </text>
        <text x="100" y="392" textAnchor="middle" className="impact-sketch-axis">
          REAR
        </text>
        <text x="14" y="206" textAnchor="middle" className="impact-sketch-axis">
          LH
        </text>
        <text x="186" y="206" textAnchor="middle" className="impact-sketch-axis">
          RH
        </text>
        {OUTLINE_KEYS.map((k) => {
          const s = ZONE_SHAPES[k];
          return <rect key={`o-${k}`} x={s.x} y={s.y} width={s.w} height={s.h} rx={s.r ?? 0} className="impact-zone outline" />;
        })}
        {shadedList.map(([k, v]) => {
          const s = ZONE_SHAPES[k];
          return (
            <rect key={k} x={s.x} y={s.y} width={s.w} height={s.h} rx={s.r ?? 0} className={`impact-zone ${v.severity}`}>
              <title>{`${s.label}: ${v.parts.join(", ")}`}</title>
            </rect>
          );
        })}
      </svg>
      <div className="impact-sketch-notes">
        <div>
          <strong>Point of impact:</strong> {pointOfImpact?.trim() ? pointOfImpact : <span style={{ color: "var(--text-muted)" }}>not recorded</span>}
        </div>
        <div style={{ color: "var(--text-muted)" }}>
          Shaded from the parts detected on tab 1 ({damageEntries.length} part{damageEntries.length === 1 ? "" : "s"}). The report prints the point of
          impact as typed; the sketch is not printed yet.
        </div>
        {elsewhere.length > 0 && (
          <div style={{ color: "var(--text-muted)" }}>Not on the sketch: {elsewhere.join(", ")}.</div>
        )}
      </div>
    </div>
  );
}
