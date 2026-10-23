"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { fileUrl } from "@/lib/api";
import type { IntakePhoto } from "@/lib/intake";
import { photosForPicker } from "@/lib/intakeReview";

type Props = {
  photos: IntakePhoto[];
  /** The damage item's part ("" when it hasn't been chosen yet). */
  part: string;
  current: IntakePhoto | null;
  readPhotoIds: string[];
  onPick: (photoId: string) => void;
  onClose: () => void;
};

/** Chooses the photo a damage item points at. Rendered into <body> for the
 * same reason as DamagePhotoViewer: a fixed overlay inside a .card is clipped. */
export function PhotoPicker({ photos, part, current, readPhotoIds, onPick, onClose }: Props) {
  const headingId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const options = useMemo(() => photosForPicker(photos, part, current), [photos, part, current]);
  const readIds = useMemo(() => new Set(readPhotoIds), [readPhotoIds]);
  const samePartCount = options.filter((o) => o.samePart).length;
  const [filter, setFilter] = useState<"part" | "all">(samePartCount > 0 ? "part" : "all");
  const shown = filter === "part" ? options.filter((o) => o.samePart) : options;

  // Focus moves into the dialog, and back to whatever opened it on close.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    return () => opener?.focus();
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (typeof document === "undefined") return null;

  return createPortal(
    <div className="rv-modal" role="dialog" aria-modal="true" aria-labelledby={headingId} onClick={onClose}>
      <div className="rv-picker" onClick={(e) => e.stopPropagation()}>
        <div className="rv-picker-head">
          <h2 id={headingId}>Choose a photo{part ? ` for ${part}` : ""}</h2>
          {part && (
            <div className="rv-segmented" role="group" aria-label="Show">
              <button type="button" aria-pressed={filter === "part"} disabled={samePartCount === 0} onClick={() => setFilter("part")}>
                This part ({samePartCount})
              </button>
              <button type="button" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>
                All ({options.length})
              </button>
            </div>
          )}
          <button ref={closeRef} type="button" className="intake-icon-btn" onClick={onClose} aria-label="Close">
            <X style={{ width: 16, height: 16 }} />
          </button>
        </div>
        <p className="intake-muted rv-picker-hint">
          {part ? "Photos sorted under this part come first, then others from the same area of the car." : "Pick the photo that shows this damage best."}
          {current ? " Choosing a different photo clears the damage box, which was drawn on the old one." : ""}
        </p>

        {shown.length === 0 ? (
          <div className="intake-empty">No photos to choose from.</div>
        ) : (
          <div className="rv-picker-grid">
            {shown.map(({ photo: p }) => {
              const isCurrent = current?.id === p.id;
              return (
                <button
                  key={p.id}
                  type="button"
                  className={`rv-pick ${isCurrent ? "current" : ""} ${p.excluded ? "excluded" : ""}`}
                  onClick={() => onPick(p.id)}
                  title={p.note || p.name}
                  aria-label={`${p.label}${p.note ? `: ${p.note}` : ""}${isCurrent ? " (current photo)" : ""}`}
                >
                  <span className={`rv-pick-img ${readIds.has(p.id) ? "read" : ""}`}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={fileUrl(p.thumb_url)} alt="" loading="lazy" />
                    <span className="rv-tile-label">{p.label}</span>
                    <span className="rv-tile-flags">
                      {isCurrent && <span className="rv-tile-flag current">Current</span>}
                      {p.shows_damage && <span className="rv-tile-flag damage">Damage</span>}
                      {p.quality === "blurry" && <span className="rv-tile-flag warn">Blurry</span>}
                      {p.excluded && <span className="rv-tile-flag">Excluded</span>}
                    </span>
                  </span>
                  <span className="rv-pick-note">{p.note || p.part || (p.area ? `Area: ${p.area}` : "No note")}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
