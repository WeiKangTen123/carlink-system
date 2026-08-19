"use client";

import React from "react";

export type Hotspot = {
  id: number;
  label: string;
  title: string;
  top: string;
  left: string;
  severe?: boolean;
};

interface VehicleBlueprintProps {
  hotspots?: Hotspot[];
  onSelectHotspot?: (index: number) => void;
  activeHotspot?: number;
}

export function VehicleBlueprint({
  hotspots = [],
  onSelectHotspot,
  activeHotspot,
}: VehicleBlueprintProps) {
  return (
    <div className="blueprint-stage-radar">
      {/* Animated Radar Scanning Laser Beam */}
      <div className="radar-laser-beam" />

      <div className="svg-car-container">
        <svg viewBox="0 0 400 180" width="100%" height="auto" style={{ display: "block" }}>
          {/* Car Body Chassis Wireframe */}
          <path
            d="M 60 90 Q 60 40 100 35 L 140 35 L 180 20 L 260 20 L 300 35 L 340 40 Q 365 90 340 140 L 300 145 L 260 160 L 180 160 L 140 145 L 100 145 Q 60 140 60 90 Z"
            fill="none"
            stroke="var(--border-glow, #38bdf8)"
            strokeWidth="2.2"
            strokeLinejoin="round"
          />

          {/* Windshield & Windows */}
          <path
            d="M 175 35 L 255 35 L 275 50 L 155 50 Z"
            fill="rgba(56, 189, 248, 0.08)"
            stroke="var(--border-color)"
            strokeWidth="1.5"
          />
          <path
            d="M 175 145 L 255 145 L 275 130 L 155 130 Z"
            fill="rgba(56, 189, 248, 0.08)"
            stroke="var(--border-color)"
            strokeWidth="1.5"
          />
          <rect
            x="180"
            y="55"
            width="80"
            height="70"
            rx="6"
            fill="none"
            stroke="var(--border-color)"
            strokeWidth="1.5"
          />

          {/* Wheels */}
          <rect x="105" y="14" width="36" height="16" rx="3" fill="var(--text-muted)" opacity="0.4" />
          <rect x="270" y="14" width="36" height="16" rx="3" fill="var(--text-muted)" opacity="0.4" />
          <rect x="105" y="150" width="36" height="16" rx="3" fill="var(--text-muted)" opacity="0.4" />
          <rect x="270" y="150" width="36" height="16" rx="3" fill="var(--text-muted)" opacity="0.4" />

          {/* Indicators */}
          <text
            x="35"
            y="94"
            fontFamily="var(--font-mono)"
            fontSize="9"
            fontWeight="700"
            fill="var(--text-muted)"
            textAnchor="middle"
          >
            FRONT
          </text>
          <text
            x="365"
            y="94"
            fontFamily="var(--font-mono)"
            fontSize="9"
            fontWeight="700"
            fill="var(--text-muted)"
            textAnchor="middle"
          >
            REAR
          </text>
        </svg>

        {/* Dynamic Pulsing Damage Hotspots */}
        {hotspots.map((spot, idx) => (
          <button
            key={spot.id || idx}
            type="button"
            className={`hotspot-beacon ${spot.severe ? "severe-spot" : ""} ${
              activeHotspot === idx ? "active-spot" : ""
            }`}
            style={{
              top: spot.top,
              left: spot.left,
            }}
            title={spot.title}
            onClick={() => onSelectHotspot && onSelectHotspot(idx)}
          >
            {spot.label}
          </button>
        ))}
      </div>
    </div>
  );
}
