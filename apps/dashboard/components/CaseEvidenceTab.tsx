"use client";

import { useState } from "react";
import { Camera, Eye, EyeOff, ZoomIn, ZoomOut, Maximize2, X, Sparkles, Layers } from "lucide-react";
import { type DamageSummaryItem, fileUrl } from "@/lib/api";
import { severityClass } from "@/lib/caseFields";

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
  photoThumbs?: string[];
  damageEntries: DamageSummaryItem[];
  activePhotoIndex: number;
  onSelectPhoto: (idx: number) => void;
}

export function CaseEvidenceTab({ photos, photoThumbs, damageEntries, activePhotoIndex, onSelectPhoto }: Props) {
  const [showBadges, setShowBadges] = useState(true);
  const [zoomLevel, setZoomLevel] = useState<number>(1);
  const [isLightboxOpen, setIsLightboxOpen] = useState(false);

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

  const toggleZoom = () => {
    setZoomLevel((prev) => (prev === 1 ? 1.5 : prev === 1.5 ? 2 : 1));
  };

  return (
    <>
      <div className="card-glass">
        <div className="card-header">
          <div>
            <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Camera style={{ width: 18, height: 18, color: "var(--accent-primary)" }} />
              <span>Evidence Photo HUD</span>
            </div>
            <div className="card-subtitle">
              Photo {activePhotoIndex + 1} of {photos.length} &bull; Ref:{" "}
              <strong style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>{currentPhotoLabel}</strong>
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button
              type="button"
              className="btn-secondary-modern"
              style={{ fontSize: 11, padding: "5px 9px", display: "inline-flex", alignItems: "center", gap: 5 }}
              onClick={toggleZoom}
              title="Toggle Zoom (1x / 1.5x / 2x)"
            >
              {zoomLevel > 1 ? <ZoomOut style={{ width: 13, height: 13 }} /> : <ZoomIn style={{ width: 13, height: 13 }} />}
              <span>{zoomLevel}x</span>
            </button>

            <button
              type="button"
              className="btn-secondary-modern"
              style={{ fontSize: 11, padding: "5px 9px", display: "inline-flex", alignItems: "center", gap: 5 }}
              onClick={() => setIsLightboxOpen(true)}
              title="Open Fullscreen Lightbox"
            >
              <Maximize2 style={{ width: 13, height: 13 }} />
              <span>Inspect</span>
            </button>

            <button
              type="button"
              className="btn-secondary-modern"
              style={{ fontSize: 11, padding: "5px 10px", display: "inline-flex", alignItems: "center", gap: 5 }}
              onClick={() => setShowBadges(!showBadges)}
            >
              {showBadges ? <EyeOff style={{ width: 13, height: 13 }} /> : <Eye style={{ width: 13, height: 13 }} />}
              <span>{showBadges ? "Hide AI Tags" : "Show AI Tags"}</span>
            </button>
          </div>
        </div>

        {/* Aspect-ratio preserving viewport to ensure bounding box alignment */}
        <div className="photo-inspector-box" style={{ overflow: "hidden", display: "flex", justifyContent: "center", alignItems: "center", minHeight: 340, background: "#050811" }}>
          <div
            style={{
              position: "relative",
              display: "inline-block",
              maxWidth: "100%",
              transform: `scale(${zoomLevel})`,
              transformOrigin: "center center",
              transition: "transform 0.25s cubic-bezier(0.16, 1, 0.3, 1)",
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={fileUrl(currentPhotoUrl)}
              alt={currentPhotoLabel}
              className="inspector-main-img"
              style={{
                maxHeight: 380,
                width: "auto",
                maxWidth: "100%",
                objectFit: "contain",
                display: "block",
              }}
            />

            {showBadges && (
              <div className="ai-bounding-overlay" style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
                {currentPhotoDamage
                  .map((item, i) => ({ item, i, css: bboxToCss(item.bbox_2d) }))
                  .filter((x): x is { item: DamageSummaryItem; i: number; css: NonNullable<ReturnType<typeof bboxToCss>> } => x.css !== null)
                  .map(({ item, i, css }) => (
                    <div key={i} className="ai-box-marker-glow" style={css}>
                      <div className="ai-box-tag-glow">
                        <Sparkles style={{ width: 10, height: 10 }} />
                        <span>{item.part}</span>
                        {item.ai_confidence ? <span style={{ opacity: 0.8 }}>// {item.ai_confidence}</span> : null}
                      </div>
                    </div>
                  ))}
              </div>
            )}
          </div>
        </div>

        {showBadges && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
            {currentPhotoDamage.length > 0 ? (
              currentPhotoDamage.map((item, i) => (
                <span key={i} className={`chip-severity ${severityClass(item.severity)}`} style={{ fontSize: 11, display: "inline-flex", alignItems: "center", gap: 4 }}>
                  <Layers style={{ width: 11, height: 11 }} />
                  <span>{item.part}</span>
                  {item.ai_confidence ? <span style={{ opacity: 0.85 }}>· {item.ai_confidence}</span> : null}
                  {item.bbox_2d ? <span title="AI Localized Bounding Box">⊡</span> : null}
                </span>
              ))
            ) : (
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                No localized damaged parts linked to this photo
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
              onClick={() => {
                onSelectPhoto(idx);
                setZoomLevel(1);
              }}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={fileUrl(photoThumbs?.[idx] || src)} alt={photoLabel(idx)} loading="lazy" decoding="async" />
            </div>
          ))}
        </div>
      </div>

      {/* High-Resolution Fullscreen Lightbox Modal */}
      {isLightboxOpen && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0, 0, 0, 0.92)",
            zIndex: 10000,
            display: "flex",
            flexDirection: "column",
            padding: 24,
            backdropFilter: "blur(12px)",
          }}
          onClick={() => setIsLightboxOpen(false)}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
            <div style={{ color: "#fff", display: "flex", alignItems: "center", gap: 12 }}>
              <span style={{ fontWeight: 800, fontSize: 16 }}>Forensic Evidence Lightbox</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--accent-primary)" }}>{currentPhotoLabel}</span>
            </div>
            <button
              type="button"
              className="btn-secondary-modern"
              style={{ color: "#fff", padding: "6px 12px", display: "flex", alignItems: "center", gap: 6 }}
              onClick={() => setIsLightboxOpen(false)}
            >
              <X style={{ width: 14, height: 14 }} />
              <span>Close</span>
            </button>
          </div>

          <div
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              position: "relative",
              overflow: "auto",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ position: "relative", display: "inline-block" }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={fileUrl(currentPhotoUrl)}
                alt={currentPhotoLabel}
                style={{
                  maxHeight: "82vh",
                  maxWidth: "92vw",
                  objectFit: "contain",
                  borderRadius: 8,
                  boxShadow: "0 20px 50px rgba(0,0,0,0.8)",
                }}
              />
              {showBadges && (
                <div style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
                  {currentPhotoDamage
                    .map((item, i) => ({ item, i, css: bboxToCss(item.bbox_2d) }))
                    .filter((x): x is { item: DamageSummaryItem; i: number; css: NonNullable<ReturnType<typeof bboxToCss>> } => x.css !== null)
                    .map(({ item, i, css }) => (
                      <div key={i} className="ai-box-marker-glow" style={css}>
                        <div className="ai-box-tag-glow">
                          <Sparkles style={{ width: 10, height: 10 }} />
                          <span>{item.part}</span>
                          {item.ai_confidence ? <span style={{ opacity: 0.8 }}>// {item.ai_confidence}</span> : null}
                        </div>
                      </div>
                    ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

