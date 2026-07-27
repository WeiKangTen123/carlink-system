"use client";

import { useState, useEffect } from "react";
import { fileUrl } from "@/lib/api";

export function ImageLightbox({ photoUrls }: { photoUrls: string[] }) {
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (selectedIndex === null) return;
      if (e.key === "Escape") setSelectedIndex(null);
      if (e.key === "ArrowLeft") {
        setSelectedIndex((prev) => (prev !== null && prev > 0 ? prev - 1 : photoUrls.length - 1));
      }
      if (e.key === "ArrowRight") {
        setSelectedIndex((prev) => (prev !== null && prev < photoUrls.length - 1 ? prev + 1 : 0));
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [selectedIndex, photoUrls.length]);

  if (!photoUrls || photoUrls.length === 0) return null;

  return (
    <div>
      {/* Thumbnail Gallery Grid */}
      <div className="photo-grid">
        {photoUrls.map((url, idx) => (
          <div
            key={url}
            id={`photo-P${String(idx + 1).padStart(2, "0")}`}
            onClick={() => setSelectedIndex(idx)}
            style={{
              position: "relative",
              cursor: "pointer",
              borderRadius: 8,
              overflow: "hidden",
              border: "1px solid var(--border-color)",
              scrollMarginTop: 90,
            }}
          >
            <img
              src={fileUrl(url)}
              alt={`Incident Photo ${idx + 1}`}
              style={{
                width: "100%",
                height: 160,
                objectFit: "cover",
                display: "block",
                transition: "transform 0.2s ease",
              }}
            />
            <span
              style={{
                position: "absolute",
                bottom: 8,
                left: 8,
                background: "rgba(15, 23, 42, 0.85)",
                color: "#fff",
                padding: "3px 8px",
                borderRadius: 4,
                fontSize: 11,
                fontWeight: 600,
                backdropFilter: "blur(4px)",
              }}
            >
              🔍 Photo P{String(idx + 1).padStart(2, "0")}
            </span>
          </div>
        ))}
      </div>

      {/* Full-Screen In-App Lightbox Modal */}
      {selectedIndex !== null && (
        <div
          onClick={() => setSelectedIndex(null)}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 1000,
            background: "rgba(11, 15, 25, 0.92)",
            backdropFilter: "blur(12px)",
            WebkitBackdropFilter: "blur(12px)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            padding: 24,
          }}
        >
          {/* Modal Header Bar */}
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: "100%",
              maxWidth: 960,
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 16,
              color: "#fff",
            }}
          >
            <div style={{ fontSize: 16, fontWeight: 600 }}>
              Photo Evidence P{String(selectedIndex + 1).padStart(2, "0")} ({selectedIndex + 1} of {photoUrls.length})
            </div>
            <button
              type="button"
              onClick={() => setSelectedIndex(null)}
              style={{
                background: "rgba(255,255,255,0.15)",
                border: "none",
                color: "#fff",
                fontSize: 18,
                fontWeight: "bold",
                width: 36,
                height: 36,
                borderRadius: "50%",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              ✕
            </button>
          </div>

          {/* Main Full-Size Image Frame */}
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              position: "relative",
              maxWidth: "100%",
              maxHeight: "80vh",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            {photoUrls.length > 1 && (
              <button
                type="button"
                onClick={() => setSelectedIndex((prev) => (prev !== null && prev > 0 ? prev - 1 : photoUrls.length - 1))}
                style={{
                  position: "absolute",
                  left: -20,
                  zIndex: 10,
                  background: "rgba(15, 23, 42, 0.8)",
                  color: "#fff",
                  border: "1px solid rgba(255,255,255,0.2)",
                  width: 44,
                  height: 44,
                  borderRadius: "50%",
                  fontSize: 20,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                ‹
              </button>
            )}

            <img
              src={fileUrl(photoUrls[selectedIndex])}
              alt={`Full Incident Photo ${selectedIndex + 1}`}
              style={{
                maxWidth: "100%",
                maxHeight: "75vh",
                borderRadius: 8,
                boxShadow: "0 20px 50px rgba(0,0,0,0.5)",
                objectFit: "contain",
              }}
            />

            {photoUrls.length > 1 && (
              <button
                type="button"
                onClick={() => setSelectedIndex((prev) => (prev !== null && prev < photoUrls.length - 1 ? prev + 1 : 0))}
                style={{
                  position: "absolute",
                  right: -20,
                  zIndex: 10,
                  background: "rgba(15, 23, 42, 0.8)",
                  color: "#fff",
                  border: "1px solid rgba(255,255,255,0.2)",
                  width: 44,
                  height: 44,
                  borderRadius: "50%",
                  fontSize: 20,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                ›
              </button>
            )}
          </div>

          <div style={{ marginTop: 12, color: "#94a3b8", fontSize: 13 }}>
            Press <kbd style={{ background: "rgba(255,255,255,0.1)", padding: "2px 6px", borderRadius: 4 }}>ESC</kbd> or click outside to close
          </div>
        </div>
      )}
    </div>
  );
}
