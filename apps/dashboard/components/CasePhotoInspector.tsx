"use client";

import React, { useState } from "react";

export type BoundingBox = {
  top: string;
  left: string;
  width: string;
  height: string;
  tag: string;
  color?: string;
};

export type EvidencePhoto = {
  id: string;
  src: string;
  title?: string;
  category?: string;
  boxes?: BoundingBox[];
};

interface CasePhotoInspectorProps {
  photos: EvidencePhoto[];
  initialIndex?: number;
  onPhotoChange?: (index: number) => void;
}

export function CasePhotoInspector({
  photos = [],
  initialIndex = 0,
  onPhotoChange,
}: CasePhotoInspectorProps) {
  const [activeIndex, setActiveIndex] = useState(initialIndex);
  const [activeCategory, setActiveCategory] = useState("All Photos");
  const [showOverlay, setShowOverlay] = useState(true);

  if (!photos || photos.length === 0) {
    return (
      <div
        style={{
          padding: "32px",
          textAlign: "center",
          background: "var(--surface-hover)",
          borderRadius: "12px",
          color: "var(--text-muted)",
          fontSize: "13px",
        }}
      >
        No photo evidence attached to this case yet.
      </div>
    );
  }

  const currentPhoto = photos[activeIndex] || photos[0];
  const categories = ["All Photos", ...Array.from(new Set(photos.map((p) => p.category || "General").filter(Boolean)))];

  const handleSelectPhoto = (idx: number) => {
    setActiveIndex(idx);
    if (onPhotoChange) onPhotoChange(idx);
  };

  return (
    <div>
      {/* Photo Inspector Header Toolbar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "10px",
          flexWrap: "wrap",
          gap: "8px",
        }}
      >
        <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>
          Photo {activeIndex + 1} of {photos.length} &bull; Photo ID:{" "}
          <strong style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>
            {currentPhoto.id}
          </strong>
        </div>

        <div style={{ display: "flex", gap: "8px" }}>
          <button
            type="button"
            className="btn-secondary-modern"
            style={{ fontSize: "11px", padding: "4px 10px" }}
            onClick={() => setShowOverlay(!showOverlay)}
          >
            <span>{showOverlay ? "👁️" : "🙈"}</span>
            <span>{showOverlay ? "Hide AI Vision Box" : "Show AI Vision Box"}</span>
          </button>
        </div>
      </div>

      {/* Main Evidence Photo Display */}
      <div className="photo-inspector-box">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={currentPhoto.src}
          alt={currentPhoto.title || currentPhoto.id}
          className="inspector-main-img"
        />

        {/* AI Bounding Box HUD Overlays */}
        {showOverlay && currentPhoto.boxes && currentPhoto.boxes.length > 0 && (
          <div className="ai-bounding-overlay">
            {currentPhoto.boxes.map((box, bIdx) => (
              <div
                key={bIdx}
                className="ai-box-marker-glow"
                style={{
                  top: box.top,
                  left: box.left,
                  width: box.width,
                  height: box.height,
                  borderColor: box.color || "var(--accent-cyan)",
                }}
              >
                <div
                  className="ai-box-tag-glow"
                  style={{
                    background: box.color
                      ? `linear-gradient(135deg, ${box.color} 0%, #1e293b 100%)`
                      : "var(--accent-gradient)",
                  }}
                >
                  {box.tag}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Category Filter Chips */}
      {categories.length > 2 && (
        <div className="photo-category-strip">
          {categories.map((cat) => {
            const count =
              cat === "All Photos"
                ? photos.length
                : photos.filter((p) => (p.category || "General") === cat).length;
            const isActive = activeCategory === cat;
            return (
              <button
                key={cat}
                type="button"
                className={`photo-cat-btn ${isActive ? "active" : ""}`}
                onClick={() => setActiveCategory(cat)}
              >
                {cat} ({count})
              </button>
            );
          })}
        </div>
      )}

      {/* Thumbnail Carousel */}
      <div className="photo-thumb-strip">
        {photos.map((p, idx) => {
          const isCategoryMatch =
            activeCategory === "All Photos" || (p.category || "General") === activeCategory;
          if (!isCategoryMatch) return null;

          const isActive = activeIndex === idx;
          return (
            <div
              key={p.id || idx}
              className={`photo-thumb ${isActive ? "active" : ""}`}
              onClick={() => handleSelectPhoto(idx)}
              title={p.title || p.id}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={p.src} alt={p.id} />
            </div>
          );
        })}
      </div>
    </div>
  );
}
