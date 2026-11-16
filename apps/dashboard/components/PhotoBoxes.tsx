"use client";

import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import type { Box } from "@/lib/api";
import { BOX_HIDDEN_TEXT, BOX_WORDS, aspectMatches, bboxStyle, cleanBox, fitSize, type Frame } from "@/lib/evidence";
import type { FindingStatus } from "@/lib/findings";

/** One box to draw on a photo. frame: the size of the image the box was
 * drawn on (Evidence.img_w / img_h); a box whose frame's aspect differs
 * from the shown image's by more than 2% is hidden ("box hidden: photo
 * changed"). */
export type BoxMark = {
  key: string;
  label: string;
  status: FindingStatus;
  box: Box | null;
  kind: "ai" | "sort" | null;
  frame?: { w: number; h: number } | null;
};

export type PhotoBoxesProps = {
  src: string;
  alt: string;
  marks: BoxMark[];
  selectedKey?: string | null;
  hoverKey?: string | null;
  /** Default true. */
  showBoxes?: boolean;
  /** Boxes are clickable only when given. */
  onSelectMark?: (key: string) => void;
  onHoverMark?: (key: string | null) => void;
  fit?: "contain" | "natural";
  className?: string;
};

/** A status in a glyph and words, so a box never says it by colour alone. */
const STATUS_GLYPH: Record<FindingStatus, string> = { confirmed: "✓", ai: "", check: "!", suggestion: "+", dismissed: "×" };
const STATUS_WORDS: Record<FindingStatus, string> = {
  confirmed: "confirmed",
  ai: "listed by the AI",
  check: "to check",
  suggestion: "suggestion",
  dismissed: "not damaged",
};

// useLayoutEffect warns during the server render; the effect only measures.
const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/** A photo with its findings' boxes: the ONLY code that draws boxes
 * (contract9.md section 7.2, conflict #11).
 *
 * - Boxes are 0-1000 percentages of a frame exactly the image's size: in
 *   "natural" the frame shrink-wraps the image (size it with CSS on
 *   .pb-img, in px or viewport units), in "contain" the image is fitted
 *   inside the element and the frame is placed over it.
 * - Nothing is drawn until the image has loaded: before that its size, and
 *   so its shape, is unknown.
 * - A box drawn on a frame of another shape (>2%) is hidden, and the photo
 *   says so ("box hidden: photo changed").
 * - Line style tells the kind (AI box solid, sorter's box dashed); a glyph
 *   and words tell the status. */
export function PhotoBoxes({
  src,
  alt,
  marks,
  selectedKey = null,
  hoverKey = null,
  showBoxes = true,
  onSelectMark,
  onHoverMark,
  fit = "natural",
  className,
}: PhotoBoxesProps) {
  const imgRef = useRef<HTMLImageElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const [natural, setNatural] = useState<(Frame & { src: string }) | null>(null);
  const [area, setArea] = useState<Frame | null>(null);
  const loaded = natural && natural.src === src ? natural : null;

  // A cached image can finish before React attaches onLoad.
  useEffect(() => {
    const img = imgRef.current;
    if (img && img.complete && img.naturalWidth > 0) setNatural({ src, w: img.naturalWidth, h: img.naturalHeight });
  }, [src]);

  // "contain": where the fitted image sits inside the element.
  useIsoLayoutEffect(() => {
    if (fit !== "contain") return;
    const el = rootRef.current;
    if (!el) return;
    const measure = () => setArea({ w: el.clientWidth, h: el.clientHeight });
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [fit]);

  const shown = loaded ? { w: loaded.w, h: loaded.h } : null;
  const drawable = marks.filter((m) => cleanBox(m.box) && m.kind);
  const fits = (m: BoxMark) => aspectMatches(m.frame ?? null, shown);
  const hidden = loaded ? drawable.filter((m) => !fits(m)) : [];
  // The selected box last, so it sits on top of the others.
  const visible = loaded && showBoxes ? drawable.filter(fits).sort((a, b) => Number(a.key === selectedKey) - Number(b.key === selectedKey)) : [];

  const boxes = visible.map((m) => {
    const style = bboxStyle(m.box) as CSSProperties;
    const kindWord = BOX_WORDS[m.kind as "ai" | "sort"];
    const glyph = STATUS_GLYPH[m.status];
    const cls = [
      "pb-box",
      `pb-kind-${m.kind}`,
      `pb-status-${m.status}`,
      m.key === selectedKey ? "is-selected" : "",
      m.key === hoverKey ? "is-hover" : "",
      // Near the top edge the tag goes inside the box, not above it.
      (m.box as Box)[0] < 60 ? "tag-inside" : "",
    ]
      .filter(Boolean)
      .join(" ");
    const tag = (
      <span className="pb-tag" aria-hidden="true">
        {glyph && <span className="pb-glyph">{glyph}</span>}
        {kindWord}
        {m.label ? ` · ${m.label}` : ""}
      </span>
    );
    const said = `${kindWord}: ${m.label || "damage"}, ${STATUS_WORDS[m.status]}`;
    return onSelectMark ? (
      <button
        key={m.key}
        type="button"
        className={cls}
        style={style}
        aria-label={said}
        aria-pressed={m.key === selectedKey}
        onClick={(e) => {
          e.stopPropagation();
          onSelectMark(m.key);
        }}
        onMouseEnter={onHoverMark ? () => onHoverMark(m.key) : undefined}
        onMouseLeave={onHoverMark ? () => onHoverMark(null) : undefined}
        onFocus={onHoverMark ? () => onHoverMark(m.key) : undefined}
        onBlur={onHoverMark ? () => onHoverMark(null) : undefined}
      >
        {tag}
      </button>
    ) : (
      <span key={m.key} className={cls} style={style} role="img" aria-label={said}>
        {tag}
      </span>
    );
  });

  const image = (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      ref={imgRef}
      key={src}
      className="pb-img"
      src={src}
      alt={alt}
      draggable={false}
      onLoad={(e) => setNatural({ src, w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
    />
  );
  const note = hidden.length > 0 && (
    <span className="pb-note" title="The photo's shape changed since the AI drew this box, so it isn't drawn.">
      {BOX_HIDDEN_TEXT}
    </span>
  );

  if (fit === "contain") {
    const placed = shown && area ? fitSize(shown, area) : null;
    const frameStyle: CSSProperties | undefined =
      placed && area
        ? { left: (area.w - placed.w) / 2, top: (area.h - placed.h) / 2, width: placed.w, height: placed.h }
        : undefined;
    return (
      <div ref={rootRef} className={`pb-root pb-contain ${className || ""}`}>
        <div className={`pb-frame ${frameStyle ? "is-placed" : ""}`} style={frameStyle}>
          {image}
          {frameStyle && boxes}
          {frameStyle && note}
        </div>
      </div>
    );
  }
  return (
    <div ref={rootRef} className={`pb-root pb-natural ${className || ""}`}>
      <div className="pb-frame">
        {image}
        {boxes}
        {note}
      </div>
    </div>
  );
}
