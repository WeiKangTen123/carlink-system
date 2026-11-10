"use client";

import { useState, useMemo, useRef, useEffect } from "react";
import { Camera, Eye, EyeOff, ZoomIn, ZoomOut, Maximize2, X, Sparkles, Layers, ChevronLeft, ChevronRight } from "lucide-react";
import { type DamageSummaryItem, damagePartLabel, fileUrl } from "@/lib/api";
import { severityClass } from "@/lib/caseFields";
import { VEHICLE_ZONE_GROUPS, UNCLASSIFIED_ZONE_ID, classifyPhotos } from "@/lib/vehicleZones";
import { evidenceCache } from "@/lib/evidenceCache";

/** Photos no damage item links to: the pill's wording on this page. */
const NOT_LINKED_LABEL = "Not linked";

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
  photoPreviews?: string[];
  damageEntries: DamageSummaryItem[];
  activePhotoIndex: number;
  selectedZone?: string | null;
  onSelectPhoto: (idx: number) => void;
  onSelectZone?: (zoneId: string | null) => void;
  /** The arrow keys flick through photos only while this view is the one
   * on screen (tab 1 stays mounted behind the other tabs). */
  keyboardActive?: boolean;
}

/** "Photos" on tab 1: the viewer with the AI boxes, the photos-by-part
 * pills, the thumbnail rail and the full-size view. The 2x2 "presentation
 * matrix" mode went on 2026-11-04 (the firm's report never had slides);
 * the wording follows the rest of the page (photos, parts, AI boxes). */
export function CaseEvidenceTab({
  photos,
  photoThumbs,
  photoPreviews,
  damageEntries,
  activePhotoIndex,
  selectedZone,
  onSelectPhoto,
  onSelectZone,
  keyboardActive = true,
}: Props) {
  const [showBadges, setShowBadges] = useState(true);
  const [zoomLevel, setZoomLevel] = useState<number>(1);
  const [isLightboxOpen, setIsLightboxOpen] = useState(false);
  const [loadRawInLightbox, setLoadRawInLightbox] = useState(false);
  const [activeZoneFilter, setActiveZoneFilter] = useState<string | null>(selectedZone || null);

  const railRef = useRef<HTMLDivElement>(null);

  // Synchronize internal filter with parent prop if selectedZone changes
  useEffect(() => {
    if (selectedZone !== undefined) {
      setActiveZoneFilter(selectedZone);
    }
  }, [selectedZone]);

  // Classify all photos deterministically into the parts they show
  const classifiedPhotos = useMemo(() => classifyPhotos(photos, photoThumbs, damageEntries, photoPreviews), [photos, photoThumbs, damageEntries, photoPreviews]);

  // Filter photos based on currently selected zone
  const filteredPhotos = useMemo(() => {
    if (!activeZoneFilter) return classifiedPhotos;
    return classifiedPhotos.filter((p) => p.zoneId === activeZoneFilter);
  }, [classifiedPhotos, activeZoneFilter]);

  // When activePhotoIndex changes, auto-scroll thumbnail rail into view
  useEffect(() => {
    const el = document.getElementById(`capsule-thumb-${activePhotoIndex}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    }
  }, [activePhotoIndex]);

  const currentPhoto = classifiedPhotos[activePhotoIndex] || classifiedPhotos[0];
  const currentPhotoUrl = currentPhoto?.url || "";
  const currentPhotoPreview = currentPhoto?.previewUrl || currentPhoto?.thumbUrl || currentPhotoUrl;
  const currentPhotoLabel = currentPhoto?.photoRef || "P01";
  const currentPhotoDamage = currentPhoto?.damageItems || [];
  const zoneLabel = (p: { zoneId: string; zoneLabel: string } | undefined) => (p ? (p.zoneId === UNCLASSIFIED_ZONE_ID ? NOT_LINKED_LABEL : p.zoneLabel) : "");

  // Track cached display URL (0ms from CacheStorage / Memory LRU) & HD loading state
  const [activeDisplayUrl, setActiveDisplayUrl] = useState<string>(currentPhoto ? fileUrl(currentPhoto.thumbUrl || currentPhotoPreview) : "");
  const [isHdLoaded, setIsHdLoaded] = useState<boolean>(false);

  // Dual-tier cache lookup: Instant L1 memory check, followed by L2 CacheStorage resolution
  useEffect(() => {
    if (!currentPhotoPreview) return;
    let isCancelled = false;
    const targetUrl = fileUrl(currentPhotoPreview);

    // Initial placeholder sync
    if (currentPhoto?.thumbUrl) {
      setActiveDisplayUrl(fileUrl(currentPhoto.thumbUrl));
    }

    evidenceCache.getCachedUrl(targetUrl).then((resolvedUrl) => {
      if (!isCancelled) {
        setActiveDisplayUrl(resolvedUrl);
        setIsHdLoaded(true);
      }
    });

    return () => {
      isCancelled = true;
    };
  }, [currentPhotoPreview, currentPhoto?.thumbUrl]);

  // Controlled Concurrency Preloader:
  // Pre-caches adjacent preview WebP photos (N-1, N+1, N+2) in the background via requestIdleCallback
  useEffect(() => {
    if (!classifiedPhotos || classifiedPhotos.length === 0) return;
    const targets = [activePhotoIndex + 1, activePhotoIndex + 2, activePhotoIndex - 1].filter((i) => i >= 0 && i < classifiedPhotos.length);

    const urlsToPreload = targets.map((idx) => {
      const p = classifiedPhotos[idx];
      return fileUrl(p.previewUrl || p.thumbUrl || p.url);
    });

    evidenceCache.preload(urlsToPreload);
  }, [activePhotoIndex, classifiedPhotos]);

  // Clean up Object URLs when unmounting to free browser GPU/RAM memory
  useEffect(() => {
    return () => {
      evidenceCache.clearMemory();
    };
  }, []);

  // Keyboard navigation: ArrowLeft / ArrowRight to rapidly flick through photos
  useEffect(() => {
    if (!keyboardActive) return;
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
  }, [activePhotoIndex, photos.length, onSelectPhoto, keyboardActive]);

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
  };

  const scrollRail = (direction: "left" | "right") => {
    if (railRef.current) {
      const scrollAmt = direction === "left" ? -240 : 240;
      railRef.current.scrollBy({ left: scrollAmt, behavior: "smooth" });
    }
  };

  if (!photos || photos.length === 0) {
    return (
      <div className="card-glass" style={{ padding: "48px 24px", textAlign: "center" }}>
        <Camera style={{ width: 36, height: 36, color: "var(--text-muted)", margin: "0 auto 12px" }} />
        <h4 style={{ fontSize: 14, fontWeight: 700, margin: "0 0 6px" }}>No photos</h4>
        <p style={{ fontSize: 12, color: "var(--text-muted)", margin: 0 }}>This case has no photos attached.</p>
      </div>
    );
  }

  return (
    <>
      <div className="card-glass">
        {/* Header: title, the photo being shown, the tools */}
        <div className="card-header" style={{ marginBottom: 12 }}>
          <div>
            <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Camera style={{ width: 18, height: 18, color: "var(--accent-primary)" }} />
              <span>Photos</span>
              <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 12, background: "var(--surface-hover)", color: "var(--text-secondary)", fontWeight: 600 }}>
                {photos.length}
              </span>
            </div>
            <div className="card-subtitle">
              Photo {activePhotoIndex + 1} of {photos.length} •{" "}
              <strong style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>{currentPhotoLabel}</strong>
              {currentPhoto?.zoneLabel && (
                <>
                  {" "}
                  • <span style={{ color: currentPhoto.zoneId === UNCLASSIFIED_ZONE_ID ? "var(--text-muted)" : "var(--accent-primary)", fontWeight: 600 }}>{zoneLabel(currentPhoto)}</span>
                </>
              )}
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button
              type="button"
              className="btn-secondary-modern"
              style={{ fontSize: 11, padding: "5px 9px", display: "inline-flex", alignItems: "center", gap: 5 }}
              onClick={toggleZoom}
              title="Zoom (1x / 1.5x / 2x / 3x)"
            >
              {zoomLevel > 1 ? <ZoomOut style={{ width: 13, height: 13 }} /> : <ZoomIn style={{ width: 13, height: 13 }} />}
              <span>{zoomLevel}x</span>
            </button>

            <button
              type="button"
              className="btn-secondary-modern"
              style={{ fontSize: 11, padding: "5px 9px", display: "inline-flex", alignItems: "center", gap: 5 }}
              onClick={() => setIsLightboxOpen(true)}
              title="Show the photo full size"
            >
              <Maximize2 style={{ width: 13, height: 13 }} />
              <span>Full size</span>
            </button>

            <button
              type="button"
              className="btn-secondary-modern"
              style={{ fontSize: 11, padding: "5px 10px", display: "inline-flex", alignItems: "center", gap: 5 }}
              onClick={() => setShowBadges(!showBadges)}
            >
              {showBadges ? <EyeOff style={{ width: 13, height: 13 }} /> : <Eye style={{ width: 13, height: 13 }} />}
              <span>{showBadges ? "Hide AI boxes" : "Show AI boxes"}</span>
            </button>
          </div>
        </div>

        {/* 1. PHOTOS BY PART */}
        <div className="studio-zone-strip" aria-label="Photos by part">
          <button type="button" className={`studio-zone-pill ${activeZoneFilter === null ? "active" : ""}`} onClick={() => handleZoneClick(null)}>
            <span>All</span>
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
                {hasDamage && <span className="zone-damage-dot" title="Damage detected on this part" />}
                <span>{group.shortLabel}</span>
                <span style={{ opacity: 0.8, fontSize: 10 }}>({count})</span>
              </button>
            );
          })}

          {/* Photos no damage item links to -- not filed under a part they
              may not show (see classifyPhotos). */}
          {(() => {
            const count = classifiedPhotos.filter((p) => p.zoneId === UNCLASSIFIED_ZONE_ID).length;
            if (count === 0) return null;
            return (
              <button
                type="button"
                className={`studio-zone-pill ${activeZoneFilter === UNCLASSIFIED_ZONE_ID ? "active" : ""}`}
                onClick={() => handleZoneClick(UNCLASSIFIED_ZONE_ID)}
                title="Photos not linked to a detected part"
              >
                <span>{NOT_LINKED_LABEL}</span>
                <span style={{ opacity: 0.8, fontSize: 10 }}>({count})</span>
              </button>
            );
          })()}
        </div>

        {/* 2. THE PHOTO */}
        {filteredPhotos.length === 0 ? (
          <div
            style={{
              padding: "48px 24px",
              textAlign: "center",
              background: "#050811",
              borderRadius: 10,
              border: "1px dashed var(--border-color)",
              margin: "12px 0",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              minHeight: 320,
            }}
          >
            <Camera style={{ width: 32, height: 32, color: "var(--accent-primary)", marginBottom: 12, opacity: 0.8 }} />
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)", marginBottom: 6 }}>No photos of this part</div>
            <p style={{ fontSize: 12, color: "var(--text-muted)", maxWidth: 380, lineHeight: 1.5, margin: "0 0 16px" }}>
              None of the {photos.length} photos is linked to this part.
            </p>
            <button
              type="button"
              className="btn-secondary-modern"
              onClick={() => handleZoneClick(null)}
              style={{ fontSize: 11, padding: "6px 14px", display: "inline-flex", alignItems: "center", gap: 6 }}
            >
              <span>All photos ({photos.length})</span>
            </button>
          </div>
        ) : (
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
                title="Previous photo (left arrow key)"
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
                title="Next photo (right arrow key)"
              >
                <ChevronRight style={{ width: 18, height: 18 }} />
              </button>

              {/* Subtle HD Buffering indicator if high-res image is still downloading */}
              {!isHdLoaded && currentPhoto?.thumbUrl && (
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
                  <span>Loading…</span>
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
                {currentPhoto?.thumbUrl && !isHdLoaded && (
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
                      filter: "blur(4px)",
                      opacity: 0.9,
                    }}
                  />
                )}

                {/* 2. Web-Optimized High-Definition Inspection Photo (Cached via Dual-Tier L1/L2) */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={activeDisplayUrl}
                  alt={currentPhotoLabel}
                  className="inspector-main-img"
                  loading="eager"
                  decoding="async"
                  onLoad={() => setIsHdLoaded(true)}
                  style={{
                    maxHeight: 380,
                    width: "auto",
                    maxWidth: "100%",
                    objectFit: "contain",
                    display: isHdLoaded || !currentPhoto?.thumbUrl ? "block" : "none",
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
                            <span>{damagePartLabel(item)}</span>
                          </div>
                        </div>
                      ))}
                  </div>
                )}
              </div>
            </div>

            {/* The parts the AI linked to this photo */}
            {showBadges && (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
                {currentPhotoDamage.length > 0 ? (
                  currentPhotoDamage.map((item, i) => (
                    <span key={i} className={`chip-severity ${severityClass(item.severity)}`} style={{ fontSize: 11, display: "inline-flex", alignItems: "center", gap: 4 }}>
                      <Layers style={{ width: 11, height: 11 }} />
                      <span>{damagePartLabel(item)}</span>
                      {/* The firm's wording when the case has it ("Buckled/cut"), else the AI's damage type. */}
                      {item.condition || item.damage_type ? <span style={{ opacity: 0.85 }}>({item.condition || item.damage_type})</span> : null}
                      {item.bbox_2d ? <span title="The AI drew a box for this part">⊡</span> : null}
                    </span>
                  ))
                ) : (
                  <span style={{ fontSize: 12, color: "var(--text-muted)" }}>No detected part is linked to this photo</span>
                )}
              </div>
            )}
          </>
        )}

        {/* 3. THUMBNAIL RAIL */}
        <div className="studio-capsule-rail-wrap">
          <button type="button" className="btn-secondary-modern" style={{ padding: "8px 6px", borderRadius: 8, height: 60, flexShrink: 0 }} onClick={() => scrollRail("left")} title="Scroll left">
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
                  title={`${item.photoRef} • ${zoneLabel(item)}${hasDamage ? ` (${item.damageItems.length} part${item.damageItems.length === 1 ? "" : "s"})` : ""}`}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={fileUrl(item.thumbUrl || item.url)} alt={item.photoRef} loading="lazy" decoding="async" />
                  <span className="studio-thumb-ref">{item.photoRef}</span>
                  {hasDamage && <span className="studio-thumb-damage-indicator" />}
                </div>
              );
            })}
          </div>

          <button type="button" className="btn-secondary-modern" style={{ padding: "8px 6px", borderRadius: 8, height: 60, flexShrink: 0 }} onClick={() => scrollRail("right")} title="Scroll right">
            <ChevronRight style={{ width: 14, height: 14 }} />
          </button>
        </div>
      </div>

      {/* Full-size view */}
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
              <span style={{ fontWeight: 800, fontSize: 16 }}>Photo</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--accent-primary)" }}>{currentPhotoLabel}</span>
              <span style={{ fontSize: 12, color: "rgba(255,255,255,0.7)" }}>• {zoneLabel(currentPhoto)}</span>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <button
                type="button"
                className="btn-secondary-modern"
                style={{
                  color: loadRawInLightbox ? "var(--accent-primary)" : "#fff",
                  borderColor: loadRawInLightbox ? "var(--accent-primary)" : "rgba(255,255,255,0.2)",
                  fontSize: 11,
                  padding: "5px 10px",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
                onClick={() => setLoadRawInLightbox(!loadRawInLightbox)}
                title="Show the original file instead of the web-sized copy"
              >
                <Sparkles style={{ width: 13, height: 13 }} />
                <span>{loadRawInLightbox ? "Showing the original" : "Open original"}</span>
              </button>
              <button
                type="button"
                className="btn-secondary-modern"
                style={{ color: "#fff", padding: "6px 12px", display: "flex", alignItems: "center", gap: 6 }}
                onClick={() => {
                  setIsLightboxOpen(false);
                  setLoadRawInLightbox(false);
                }}
              >
                <X style={{ width: 14, height: 14 }} />
                <span>Close</span>
              </button>
            </div>
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
                src={fileUrl(loadRawInLightbox ? currentPhotoUrl : currentPhoto.previewUrl || currentPhotoUrl)}
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
                          <span>{damagePartLabel(item)}</span>
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
