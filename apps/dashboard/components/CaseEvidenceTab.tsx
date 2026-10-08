"use client";

import { useState, useMemo, useRef, useEffect } from "react";
import {
  Camera,
  Eye,
  EyeOff,
  ZoomIn,
  ZoomOut,
  Maximize2,
  X,
  Sparkles,
  Layers,
  ChevronLeft,
  ChevronRight,
  SlidersHorizontal,
  LayoutGrid,
  FileSpreadsheet,
} from "lucide-react";
import { type DamageSummaryItem, fileUrl } from "@/lib/api";
import { severityClass } from "@/lib/caseFields";
import {
  VEHICLE_ZONE_GROUPS,
  classifyPhotos,
  buildPresentationSlides,
  type ClassifiedPhoto,
} from "@/lib/vehicleZones";

/** damage_summary.photo_reference is "P01", "P02"... in upload order (see schema.py) */
const photoLabel = (index: number) => `P${String(index + 1).padStart(2, "0")}`;

/** Converts Gemini's native bbox_2d format ([y_min, x_min, y_max, x_max], each 0-1000) into CSS % positioning */
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
  selectedZone?: string | null;
  onSelectPhoto: (idx: number) => void;
  onSelectZone?: (zoneId: string | null) => void;
}

export function CaseEvidenceTab({
  photos,
  photoThumbs,
  damageEntries,
  activePhotoIndex,
  selectedZone,
  onSelectPhoto,
  onSelectZone,
}: Props) {
  const [viewMode, setViewMode] = useState<"hud" | "matrix">("hud");
  const [showBadges, setShowBadges] = useState(true);
  const [zoomLevel, setZoomLevel] = useState<number>(1);
  const [isLightboxOpen, setIsLightboxOpen] = useState(false);
  const [activeZoneFilter, setActiveZoneFilter] = useState<string | null>(selectedZone || null);
  const [currentSlideIndex, setCurrentSlideIndex] = useState<number>(0);

  const railRef = useRef<HTMLDivElement>(null);

  // Synchronize internal filter with parent prop if selectedZone changes
  useEffect(() => {
    if (selectedZone !== undefined) {
      setActiveZoneFilter(selectedZone);
    }
  }, [selectedZone]);

  // Classify all photos deterministically into automotive assemblies
  const classifiedPhotos = useMemo(
    () => classifyPhotos(photos, photoThumbs, damageEntries),
    [photos, photoThumbs, damageEntries]
  );

  // Filter photos based on currently selected zone
  const filteredPhotos = useMemo(() => {
    if (!activeZoneFilter) return classifiedPhotos;
    return classifiedPhotos.filter((p) => p.zoneId === activeZoneFilter);
  }, [classifiedPhotos, activeZoneFilter]);

  // Build 2x2 presentation matrix slides (matching Presentation1.pptx)
  const presentationSlides = useMemo(
    () => buildPresentationSlides(filteredPhotos),
    [filteredPhotos]
  );

  // Keep slide index bounded
  useEffect(() => {
    if (currentSlideIndex >= presentationSlides.length) {
      setCurrentSlideIndex(Math.max(0, presentationSlides.length - 1));
    }
  }, [presentationSlides.length, currentSlideIndex]);

  // When activePhotoIndex changes, auto-scroll thumbnail rail into view
  useEffect(() => {
    const el = document.getElementById(`capsule-thumb-${activePhotoIndex}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    }
  }, [activePhotoIndex]);

  if (photos.length === 0) {
    return (
      <div className="card-glass">
        <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>No photos attached to this report.</p>
      </div>
    );
  }

  const currentPhoto = classifiedPhotos[activePhotoIndex] || classifiedPhotos[0];
  const currentPhotoUrl = currentPhoto.url;
  const currentPhotoLabel = currentPhoto.photoRef;
  const currentPhotoDamage = currentPhoto.damageItems;

  // Track high-res photo loaded status for instantaneous progressive rendering
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null);

  // When active photo changes, check if it's already in browser cache
  useEffect(() => {
    if (!currentPhotoUrl) return;
    const testImg = new Image();
    testImg.src = fileUrl(currentPhotoUrl);
    if (testImg.complete && testImg.naturalWidth > 0) {
      setLoadedUrl(currentPhotoUrl);
    } else {
      setLoadedUrl(null);
      testImg.onload = () => setLoadedUrl(currentPhotoUrl);
    }
  }, [currentPhotoUrl]);

  // Intelligent Predictive Preloader:
  // Pre-caches adjacent photos (N-1, N+1, N+2, N+3) in the background
  useEffect(() => {
    if (!photos || photos.length === 0) return;
    const targets = [
      activePhotoIndex + 1,
      activePhotoIndex + 2,
      activePhotoIndex + 3,
      activePhotoIndex - 1,
    ].filter((i) => i >= 0 && i < photos.length);

    targets.forEach((idx) => {
      const preload = new Image();
      preload.src = fileUrl(photos[idx]);
      preload.decoding = "async";
    });
  }, [activePhotoIndex, photos]);

  // Keyboard navigation: ArrowLeft / ArrowRight to rapidly flick through photos
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes((e.target as HTMLElement)?.tagName)) return;
      if (e.key === "ArrowRight") {
        if (activePhotoIndex < photos.length - 1) {
          onSelectPhoto(activePhotoIndex + 1);
          setZoomLevel(1);
        }
      } else if (e.key === "ArrowLeft") {
        if (activePhotoIndex > 0) {
          onSelectPhoto(activePhotoIndex - 1);
          setZoomLevel(1);
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activePhotoIndex, photos.length, onSelectPhoto]);

  const toggleZoom = () => {
    setZoomLevel((prev) => (prev === 1 ? 1.5 : prev === 1.5 ? 2 : prev === 2 ? 3 : 1));
  };

  const handleZoneClick = (zoneId: string | null) => {
    const nextZone = activeZoneFilter === zoneId ? null : zoneId;
    setActiveZoneFilter(nextZone);
    onSelectZone?.(nextZone);

    // If switching to a specific zone, select the first photo in that zone
    if (nextZone) {
      const firstInZone = classifiedPhotos.find((p) => p.zoneId === nextZone);
      if (firstInZone) {
        onSelectPhoto(firstInZone.index);
        setZoomLevel(1);
      }
    }
    setCurrentSlideIndex(0);
  };

  const scrollRail = (direction: "left" | "right") => {
    if (railRef.current) {
      const scrollAmt = direction === "left" ? -240 : 240;
      railRef.current.scrollBy({ left: scrollAmt, behavior: "smooth" });
    }
  };

  const currentSlide = presentationSlides[currentSlideIndex] || presentationSlides[0];

  return (
    <>
      <div className="card-glass">
        {/* Top Header Strip: Title, Active Status, Mode Toggle & Tools */}
        <div className="card-header" style={{ marginBottom: 12 }}>
          <div>
            <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Camera style={{ width: 18, height: 18, color: "var(--accent-primary)" }} />
              <span>Forensic Evidence Studio</span>
              <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 12, background: "var(--surface-hover)", color: "var(--text-secondary)", fontWeight: 600 }}>
                {photos.length} Photos Attached
              </span>
            </div>
            <div className="card-subtitle">
              Photo {activePhotoIndex + 1} of {photos.length} &bull; Ref:{" "}
              <strong style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>{currentPhotoLabel}</strong>
              {currentPhoto.zoneLabel && (
                <> &bull; Zone: <span style={{ color: "var(--accent-primary)", fontWeight: 600 }}>{currentPhoto.zoneLabel}</span></>
              )}
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {/* View Mode Toggle: 1-Up Forensic HUD vs 2x2 Presentation Matrix */}
            <div className="studio-view-toggle">
              <button
                type="button"
                className={`studio-view-toggle-btn ${viewMode === "hud" ? "active" : ""}`}
                onClick={() => setViewMode("hud")}
                title="Single Photo Deep Inspection HUD"
              >
                <SlidersHorizontal style={{ width: 12, height: 12 }} />
                <span>1-Up HUD</span>
              </button>
              <button
                type="button"
                className={`studio-view-toggle-btn ${viewMode === "matrix" ? "active" : ""}`}
                onClick={() => setViewMode("matrix")}
                title="2x2 Presentation Matrix (Matching Presentation1.pptx)"
              >
                <LayoutGrid style={{ width: 12, height: 12 }} />
                <span>2×2 Matrix</span>
              </button>
            </div>

            {viewMode === "hud" && (
              <>
                <button
                  type="button"
                  className="btn-secondary-modern"
                  style={{ fontSize: 11, padding: "5px 9px", display: "inline-flex", alignItems: "center", gap: 5 }}
                  onClick={toggleZoom}
                  title="Toggle Zoom (1x / 1.5x / 2x / 3x)"
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
              </>
            )}
          </div>
        </div>

        {/* 1. VEHICLE ASSEMBLY / ZONE FILTER STRIP */}
        <div className="studio-zone-strip">
          <button
            type="button"
            className={`studio-zone-pill ${activeZoneFilter === null ? "active" : ""}`}
            onClick={() => handleZoneClick(null)}
          >
            <span>All Photos</span>
            <span style={{ opacity: 0.8, fontSize: 10 }}>({photos.length})</span>
          </button>

          {VEHICLE_ZONE_GROUPS.map((group) => {
            const count = classifiedPhotos.filter((p) => p.zoneId === group.id).length;
            const hasDamage = classifiedPhotos.some((p) => p.zoneId === group.id && p.damageItems.length > 0);
            if (count === 0 && !hasDamage) return null;

            return (
              <button
                key={group.id}
                type="button"
                className={`studio-zone-pill ${activeZoneFilter === group.id ? "active" : ""}`}
                onClick={() => handleZoneClick(group.id)}
                title={group.label}
              >
                {hasDamage && <span className="zone-damage-dot" title="Damage detected in this zone" />}
                <span>{group.shortLabel}</span>
                <span style={{ opacity: 0.8, fontSize: 10 }}>({count})</span>
              </button>
            );
          })}
        </div>

        {/* 2. MAIN VIEWPORT: FORENSIC 1-UP HUD OR 2x2 PRESENTATION MATRIX */}
        {viewMode === "hud" ? (
          /* =========================================================================
             MODE A: FORENSIC 1-UP HUD
             ========================================================================= */
          <>
            <div
              className="photo-inspector-box"
              style={{
                overflow: "hidden",
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                minHeight: 380,
                background: "#050811",
                position: "relative",
                borderRadius: 10,
              }}
            >
              {/* Prev / Next Floating Navigation Arrows */}
              <button
                type="button"
                className="inspector-nav-arrow left"
                disabled={activePhotoIndex === 0}
                onClick={() => {
                  if (activePhotoIndex > 0) {
                    onSelectPhoto(activePhotoIndex - 1);
                    setZoomLevel(1);
                  }
                }}
                style={{
                  position: "absolute",
                  left: 12,
                  top: "50%",
                  transform: "translateY(-50%)",
                  width: 36,
                  height: 36,
                  borderRadius: "50%",
                  background: "rgba(10, 16, 32, 0.78)",
                  border: "1px solid rgba(255, 255, 255, 0.15)",
                  color: "#fff",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  cursor: activePhotoIndex === 0 ? "not-allowed" : "pointer",
                  opacity: activePhotoIndex === 0 ? 0.25 : 0.85,
                  transition: "all 0.18s ease",
                  zIndex: 20,
                  backdropFilter: "blur(6px)",
                }}
                title="Previous Photo (Left Arrow Key)"
              >
                <ChevronLeft style={{ width: 18, height: 18 }} />
              </button>

              <button
                type="button"
                className="inspector-nav-arrow right"
                disabled={activePhotoIndex >= photos.length - 1}
                onClick={() => {
                  if (activePhotoIndex < photos.length - 1) {
                    onSelectPhoto(activePhotoIndex + 1);
                    setZoomLevel(1);
                  }
                }}
                style={{
                  position: "absolute",
                  right: 12,
                  top: "50%",
                  transform: "translateY(-50%)",
                  width: 36,
                  height: 36,
                  borderRadius: "50%",
                  background: "rgba(10, 16, 32, 0.78)",
                  border: "1px solid rgba(255, 255, 255, 0.15)",
                  color: "#fff",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  cursor: activePhotoIndex >= photos.length - 1 ? "not-allowed" : "pointer",
                  opacity: activePhotoIndex >= photos.length - 1 ? 0.25 : 0.85,
                  transition: "all 0.18s ease",
                  zIndex: 20,
                  backdropFilter: "blur(6px)",
                }}
                title="Next Photo (Right Arrow Key)"
              >
                <ChevronRight style={{ width: 18, height: 18 }} />
              </button>

              {/* Subtle HD Buffering indicator if high-res image is still downloading */}
              {loadedUrl !== currentPhotoUrl && currentPhoto.thumbUrl && (
                <div
                  style={{
                    position: "absolute",
                    top: 12,
                    right: 12,
                    background: "rgba(10, 16, 32, 0.8)",
                    border: "1px solid rgba(56, 189, 248, 0.3)",
                    color: "var(--accent-primary)",
                    borderRadius: 6,
                    padding: "3px 8px",
                    fontSize: 10,
                    fontWeight: 600,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    zIndex: 20,
                    backdropFilter: "blur(4px)",
                  }}
                >
                  <span style={{ display: "inline-block", width: 6, height: 6, borderRadius: "50%", background: "var(--accent-primary)" }} />
                  <span>Buffering HD&hellip;</span>
                </div>
              )}

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
                {/* 1. Instant Lightweight Placeholder (0ms latency from browser cache) */}
                {currentPhoto.thumbUrl && loadedUrl !== currentPhotoUrl && (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img
                    src={fileUrl(currentPhoto.thumbUrl)}
                    alt={currentPhotoLabel}
                    className="inspector-thumb-placeholder"
                    style={{
                      maxHeight: 380,
                      width: "auto",
                      maxWidth: "100%",
                      objectFit: "contain",
                      display: "block",
                      filter: "blur(3px)",
                      opacity: 0.9,
                    }}
                  />
                )}

                {/* 2. Full-Resolution Master Inspection Photo (decoding="async" off main thread) */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={fileUrl(currentPhotoUrl)}
                  alt={currentPhotoLabel}
                  className="inspector-main-img"
                  loading="eager"
                  decoding="async"
                  onLoad={() => setLoadedUrl(currentPhotoUrl)}
                  style={{
                    maxHeight: 380,
                    width: "auto",
                    maxWidth: "100%",
                    objectFit: "contain",
                    display: (loadedUrl === currentPhotoUrl || !currentPhoto.thumbUrl) ? "block" : "none",
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

            {/* Damage Tags for active photo */}
            {showBadges && (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
                {currentPhotoDamage.length > 0 ? (
                  currentPhotoDamage.map((item, i) => (
                    <span
                      key={i}
                      className={`chip-severity ${severityClass(item.severity)}`}
                      style={{ fontSize: 11, display: "inline-flex", alignItems: "center", gap: 4 }}
                    >
                      <Layers style={{ width: 11, height: 11 }} />
                      <span>{item.part}</span>
                      {item.damage_type ? <span style={{ opacity: 0.85 }}>({item.damage_type})</span> : null}
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
          </>
        ) : (
          /* =========================================================================
             MODE B: 2×2 PRESENTATION MATRIX (Matching Presentation1.pptx)
             ========================================================================= */
          <div className="studio-matrix-container">
            {/* Matrix Slide Navigation Toolbar */}
            <div className="studio-matrix-nav">
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <FileSpreadsheet style={{ width: 15, height: 15, color: "var(--accent-primary)" }} />
                <span style={{ fontSize: 12, fontWeight: 700 }}>
                  Presentation Slide {currentSlideIndex + 1} of {Math.max(1, presentationSlides.length)}
                </span>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  &bull; {filteredPhotos.length} Photos in this bundle
                </span>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <button
                  type="button"
                  className="btn-secondary-modern"
                  style={{ padding: "4px 8px", fontSize: 11 }}
                  disabled={currentSlideIndex === 0}
                  onClick={() => setCurrentSlideIndex((prev) => Math.max(0, prev - 1))}
                >
                  <ChevronLeft style={{ width: 13, height: 13 }} />
                  <span>Prev Slide</span>
                </button>

                <div style={{ display: "flex", gap: 4 }}>
                  {presentationSlides.map((_, sIdx) => (
                    <button
                      key={sIdx}
                      type="button"
                      style={{
                        width: 22,
                        height: 22,
                        borderRadius: "50%",
                        border: sIdx === currentSlideIndex ? "1px solid var(--accent-primary)" : "1px solid var(--border-color)",
                        background: sIdx === currentSlideIndex ? "var(--accent-primary)" : "var(--surface-card)",
                        color: sIdx === currentSlideIndex ? "#fff" : "var(--text-secondary)",
                        fontSize: 10,
                        fontWeight: 700,
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                      }}
                      onClick={() => setCurrentSlideIndex(sIdx)}
                    >
                      {sIdx + 1}
                    </button>
                  ))}
                </div>

                <button
                  type="button"
                  className="btn-secondary-modern"
                  style={{ padding: "4px 8px", fontSize: 11 }}
                  disabled={currentSlideIndex >= presentationSlides.length - 1}
                  onClick={() => setCurrentSlideIndex((prev) => Math.min(presentationSlides.length - 1, prev + 1))}
                >
                  <span>Next Slide</span>
                  <ChevronRight style={{ width: 13, height: 13 }} />
                </button>
              </div>
            </div>

            {/* 2×2 Quadrant Grid: Q1, Q2, Q3, Q4 */}
            <div className="studio-matrix-grid">
              {(["q1", "q2", "q3", "q4"] as const).map((qKey, qIdx) => {
                const qPhoto: ClassifiedPhoto | null = currentSlide?.quadrants[qKey] || null;
                const qBadge = `Q${qIdx + 1}`;
                const qTitle =
                  qIdx === 0
                    ? "Top-Left"
                    : qIdx === 1
                    ? "Top-Right"
                    : qIdx === 2
                    ? "Bottom-Left"
                    : "Bottom-Right";

                if (!qPhoto) {
                  return (
                    <div
                      key={qKey}
                      className="studio-quadrant-card"
                      style={{ opacity: 0.35, display: "flex", alignItems: "center", justifyContent: "center" }}
                    >
                      <div className="studio-quadrant-header">
                        <span className="studio-quadrant-badge">{qBadge}</span>
                        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{qTitle}</span>
                      </div>
                      <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Slot {qBadge} Empty</span>
                    </div>
                  );
                }

                const isActive = activePhotoIndex === qPhoto.index;

                return (
                  <div
                    key={qKey}
                    className={`studio-quadrant-card ${isActive ? "active" : ""}`}
                    onClick={() => onSelectPhoto(qPhoto.index)}
                  >
                    {/* Quadrant Header Overlay */}
                    <div className="studio-quadrant-header">
                      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                        <span className="studio-quadrant-badge">{qBadge}</span>
                        <span
                          style={{
                            background: "rgba(0,0,0,0.75)",
                            color: "#fff",
                            fontFamily: "var(--font-mono)",
                            fontSize: 10,
                            padding: "2px 6px",
                            borderRadius: 4,
                            fontWeight: 700,
                          }}
                        >
                          {qPhoto.photoRef}
                        </span>
                      </div>
                      <span style={{ fontSize: 10, color: "rgba(255,255,255,0.7)", fontWeight: 500 }}>
                        {qPhoto.zoneLabel}
                      </span>
                    </div>

                    {/* Image */}
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={fileUrl(qPhoto.thumbUrl || qPhoto.url)}
                      alt={qPhoto.photoRef}
                      loading="lazy"
                      decoding="async"
                      style={{
                        width: "100%",
                        height: "100%",
                        objectFit: "contain",
                        background: "#080c18",
                      }}
                    />

                    {/* Quadrant Footer Overlay */}
                    <div className="studio-quadrant-footer">
                      <div style={{ maxWidth: "70%" }}>
                        {qPhoto.damageItems.length > 0 ? (
                          <span
                            className={`chip-severity ${severityClass(qPhoto.damageItems[0]?.severity)}`}
                            style={{ fontSize: 10, padding: "2px 6px" }}
                          >
                            {qPhoto.damageItems[0]?.part}
                          </span>
                        ) : (
                          <span style={{ fontSize: 10, color: "rgba(255,255,255,0.7)" }}>
                            Inspection Reference
                          </span>
                        )}
                      </div>

                      <button
                        type="button"
                        className="btn-secondary-modern"
                        style={{
                          fontSize: 10,
                          padding: "3px 7px",
                          background: "rgba(0,0,0,0.8)",
                          color: "#fff",
                          borderColor: "rgba(255,255,255,0.2)",
                        }}
                        onClick={(e) => {
                          e.stopPropagation();
                          onSelectPhoto(qPhoto.index);
                          setViewMode("hud");
                        }}
                      >
                        Inspect 1-Up
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* 3. BOUNDED CAPSULE THUMBNAIL RAIL (Fixed Height ≤ 90px — completely eliminates 11-row overflow) */}
        <div className="studio-capsule-rail-wrap">
          <button
            type="button"
            className="btn-secondary-modern"
            style={{ padding: "8px 6px", borderRadius: 8, height: 60, flexShrink: 0 }}
            onClick={() => scrollRail("left")}
            title="Scroll Rail Left"
          >
            <ChevronLeft style={{ width: 14, height: 14 }} />
          </button>

          <div className="studio-capsule-rail" ref={railRef}>
            {classifiedPhotos.map((item) => {
              const isActive = activePhotoIndex === item.index;
              const hasDamage = item.damageItems.length > 0;
              const isDimmed = activeZoneFilter !== null && item.zoneId !== activeZoneFilter;

              return (
                <div
                  key={item.index}
                  id={`capsule-thumb-${item.index}`}
                  className={`studio-capsule-thumb ${isActive ? "active" : ""}`}
                  style={{ opacity: isDimmed ? 0.35 : isActive ? 1 : 0.75 }}
                  onClick={() => {
                    onSelectPhoto(item.index);
                    setZoomLevel(1);
                  }}
                  title={`${item.photoRef} • ${item.zoneLabel}${hasDamage ? ` (${item.damageItems.length} damages)` : ""}`}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={fileUrl(item.thumbUrl || item.url)} alt={item.photoRef} loading="lazy" decoding="async" />
                  <span className="studio-thumb-ref">{item.photoRef}</span>
                  {hasDamage && <span className="studio-thumb-damage-indicator" />}
                </div>
              );
            })}
          </div>

          <button
            type="button"
            className="btn-secondary-modern"
            style={{ padding: "8px 6px", borderRadius: 8, height: 60, flexShrink: 0 }}
            onClick={() => scrollRail("right")}
            title="Scroll Rail Right"
          >
            <ChevronRight style={{ width: 14, height: 14 }} />
          </button>
        </div>
      </div>

      {/* High-Resolution Fullscreen Lightbox Modal */}
      {isLightboxOpen && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0, 0, 0, 0.94)",
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
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--accent-primary)" }}>
                {currentPhotoLabel}
              </span>
              <span style={{ fontSize: 12, color: "rgba(255,255,255,0.7)" }}>&bull; {currentPhoto.zoneLabel}</span>
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
                loading="eager"
                decoding="async"
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
