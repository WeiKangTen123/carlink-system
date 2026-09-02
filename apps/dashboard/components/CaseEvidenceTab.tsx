"use client";

import { useState } from "react";
import { type DamageSummaryItem, fileUrl } from "@/lib/api";
import { severityClass } from "./StudioApp";

/** damage_summary.photo_reference is "P01", "P02"... in upload order (see
 * schema.py) -- this is the same indexing scheme applied to photo_urls. */
const photoLabel = (index: number) => `P${String(index + 1).padStart(2, "0")}`;

/** Converts Gemini's native bbox_2d format ([y_min, x_min, y_max, x_max],
 * each 0-1000) into CSS percentage positioning for an absolutely-
 * positioned overlay div. Returns null for anything malformed rather than
 * rendering a garbled box. */
function bboxToCss(bbox: number[] | null | undefined): { top: string; left: string; width: string; height: string } | null {
  if (!bbox || bbox.length !== 4) return null;
  const [yMin, xMin, yMax, xMax] = bbox;
  if ([yMin, xMin, yMax, xMax].some((v) => typeof v !== "number" || Number.isNaN(v))) return null;
  return {
    top: `${yMin / 10}%`,
    left: `${xMin / 10}%`,
    width: `${(xMax - xMin) / 10}%`,
    height: `${(yMax - yMin) / 10}%`,
  };
}

interface Props {
  photos: string[];
  damageEntries: DamageSummaryItem[];
  activePhotoIndex: number;
  onSelectPhoto: (idx: number) => void;
}

export function CaseEvidenceTab({ photos, damageEntries, activePhotoIndex, onSelectPhoto }: Props) {
  const [showBadges, setShowBadges] = useState(true);

  if (photos.length === 0) {
    return (
      <div className="card-glass">
        <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>No photos attached to this report.</p>
      </div>
    );
  }

  const currentPhotoUrl = photos[activePhotoIndex];
  const currentPhotoLabel = photoLabel(activePhotoIndex);
  const currentPhotoDamage = damageEntries.filter((item) => item.photo_reference === currentPhotoLabel);

  return (
    <div className="card-glass">
      <div className="card-header">
        <div>
          <div className="card-title">
            <span>📸</span> Photo Evidence Inspector
          </div>
          <div className="card-subtitle">
            Photo {activePhotoIndex + 1} of {photos.length} &bull; Photo ID:{" "}
            <strong style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>{currentPhotoLabel}</strong>
          </div>
        </div>
        <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 10px" }} onClick={() => setShowBadges(!showBadges)}>
          {showBadges ? "🙈 Hide Detected Parts" : "👁️ Show Detected Parts"}
        </button>
      </div>

      <div className="photo-inspector-box">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={fileUrl(currentPhotoUrl)} alt={currentPhotoLabel} className="inspector-main-img" />

        {/* Real AI-detected bounding boxes only -- Gemini localizes
            these itself (see extraction.py's bbox_2d prompt, using
            Gemini's own native [y_min,x_min,y_max,x_max]/1000
            convention); never drawn from an invented/estimated
            position. Items without a real box just don't get one,
            no fallback guess. */}
        {showBadges && (
          <div className="ai-bounding-overlay">
            {currentPhotoDamage
              .map((item, i) => ({ item, i, css: bboxToCss(item.bbox_2d) }))
              .filter((x): x is { item: DamageSummaryItem; i: number; css: NonNullable<ReturnType<typeof bboxToCss>> } => x.css !== null)
              .map(({ item, i, css }) => (
                <div key={i} className="ai-box-marker-glow" style={css}>
                  <div className="ai-box-tag-glow">
                    ⚡ {item.part}
                    {item.ai_confidence ? ` // ${item.ai_confidence}` : ""}
                  </div>
                </div>
              ))}
          </div>
        )}
      </div>

      {showBadges && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
          {currentPhotoDamage.length > 0 ? (
            currentPhotoDamage.map((item, i) => (
              <span key={i} className={`chip-severity ${severityClass(item.severity)}`} style={{ fontSize: 11 }}>
                ⚡ {item.part}
                {item.ai_confidence ? ` // ${item.ai_confidence} confidence` : ""}
                {item.bbox_2d ? " 🔲" : ""}
              </span>
            ))
          ) : (
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              No damaged parts linked to this specific photo
            </span>
          )}
        </div>
      )}

      {/* Thumbnail Selector Strip */}
      <div className="photo-thumb-strip">
        {photos.map((src, idx) => (
          <div
            key={idx}
            className={`photo-thumb ${activePhotoIndex === idx ? "active" : ""}`}
            onClick={() => onSelectPhoto(idx)}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={fileUrl(src)} alt={photoLabel(idx)} />
          </div>
        ))}
      </div>
    </div>
  );
}
