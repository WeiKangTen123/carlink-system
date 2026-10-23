"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronLeft, ChevronRight, ExternalLink, X } from "lucide-react";
import { fileUrl } from "@/lib/api";
import { severityClass } from "@/lib/caseFields";
import type { IntakePhoto } from "@/lib/intake";
import { bboxStyle, isTypingTarget } from "@/lib/intakeReview";

export type ViewerSlide = {
  key: string;
  photo: IntakePhoto;
  /** Set when the slide is a damage item: what the list says about it. */
  damage?: { part: string; damageType?: string | null; severity?: string | null; bbox?: number[] | null };
  /** Shown instead for a plain photo (opened from Photos by part). */
  heading?: string;
};

type Props = {
  slides: ViewerSlide[];
  index: number;
  title: string;
  onNavigate: (index: number) => void;
  onClose: () => void;
};

/** Full-size view of a review-step photo with the AI's damage box drawn on
 * it, so the surveyor can check an item without leaving the page. Rendered
 * into <body>: a fixed overlay inside a .card would be clipped to the card. */
export function DamagePhotoViewer({ slides, index, title, onNavigate, onClose }: Props) {
  const slide = slides[index];
  const [loadedSrc, setLoadedSrc] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  // Focus moves into the dialog, and back to whatever opened it on close.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    return () => opener?.focus();
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (isTypingTarget(e.target)) return;
      if (e.key === "ArrowLeft" && index > 0) onNavigate(index - 1);
      if (e.key === "ArrowRight" && index < slides.length - 1) onNavigate(index + 1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, slides.length, onNavigate, onClose]);

  if (!slide || typeof document === "undefined") return null;
  const { photo, damage } = slide;
  const src = fileUrl(photo.preview_url);
  // Only once the image has its real size -- before that the frame is empty
  // and the box would be drawn against nothing.
  const box = loadedSrc === src ? bboxStyle(damage?.bbox) : null;
  const meta = [
    photo.area ? `Area: ${photo.area}` : null,
    photo.shows_damage ? "shows damage" : null,
    photo.quality && photo.quality !== "good" ? photo.quality : null,
  ].filter(Boolean);

  return createPortal(
    <div className="rv-modal" role="dialog" aria-modal="true" aria-label={`${title}: ${photo.label}`} onClick={onClose}>
      <div className="rv-viewer-body" onClick={(e) => e.stopPropagation()}>
        <div className="rv-viewer-stage">
          <div className="rv-viewer-frame">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img key={src} src={src} alt={damage?.part || photo.note || photo.label} onLoad={() => setLoadedSrc(src)} />
            {box && <span className="rv-viewer-box" style={box} />}
          </div>
          {slides.length > 1 && (
            <>
              <button type="button" className="rv-viewer-nav prev" disabled={index <= 0} onClick={() => onNavigate(index - 1)} aria-label="Previous">
                <ChevronLeft style={{ width: 20, height: 20 }} />
              </button>
              <button
                type="button"
                className="rv-viewer-nav next"
                disabled={index >= slides.length - 1}
                onClick={() => onNavigate(index + 1)}
                aria-label="Next"
              >
                <ChevronRight style={{ width: 20, height: 20 }} />
              </button>
            </>
          )}
        </div>
        <aside className="rv-viewer-side">
          <div className="rv-viewer-head">
            <div>
              <strong className="rv-viewer-label">{photo.label}</strong>
              <span className="intake-muted">
                {" "}
                · {index + 1} of {slides.length}
              </span>
            </div>
            <button ref={closeRef} type="button" className="intake-icon-btn" onClick={onClose} aria-label="Close">
              <X style={{ width: 16, height: 16 }} />
            </button>
          </div>
          <div className="intake-muted">{title}</div>

          {damage ? (
            <dl className="rv-viewer-facts">
              <dt>Part</dt>
              <dd>{damage.part || "Not chosen yet"}</dd>
              <dt>Damage</dt>
              <dd>{damage.damageType || "Not set"}</dd>
              <dt>Severity</dt>
              <dd>
                <span className={`chip-severity ${severityClass(damage.severity)}`}>{damage.severity || "Unrated"}</span>
              </dd>
            </dl>
          ) : (
            slide.heading && <div className="rv-viewer-heading">{slide.heading}</div>
          )}
          {damage && !bboxStyle(damage.bbox) && <div className="intake-muted">No damage box on this photo.</div>}

          {photo.note && (
            <div>
              <div className="rv-viewer-caption">Sorter&apos;s note</div>
              <p className="rv-viewer-note">“{photo.note}”</p>
            </div>
          )}
          {meta.length > 0 && <div className="intake-muted">{meta.join(" · ")}</div>}
          <a className="rv-viewer-open" href={src} target="_blank" rel="noreferrer">
            <ExternalLink style={{ width: 13, height: 13 }} /> Open in a new tab
          </a>
          {slides.length > 1 && <div className="intake-muted">← → to move between photos, Esc to close.</div>}
        </aside>
      </div>
    </div>,
    document.body
  );
}
