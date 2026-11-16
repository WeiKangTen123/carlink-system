/**
 * Contract 9 (contract9.md sections 4.1, 7.2): the box arithmetic behind the
 * evidence UI -- which box a photo shows, whether a box still fits the photo
 * it was drawn on, where it sits as CSS, and how far to zoom to it.
 * components/PhotoBoxes.tsx is the only code that draws boxes; it and the
 * evidence viewer take their numbers from here.
 *
 * Imports types only, so the node check scripts (scripts/*.mjs) can load it
 * without a bundler.
 */
import type { CSSProperties } from "react";
import type { Box, Evidence } from "./api";
import type { Finding } from "./findings";

/** A box drawn on a frame whose shape differs from the shown image's by more
 * than this is hidden: the photo was rotated, cropped or replaced since. */
export const ASPECT_TOLERANCE = 0.02;

export type BoxKind = "ai" | "sort" | null;
export type Frame = { w: number; h: number };

/** The words for each box (contract9.md section 1, "UI words"). */
export const BOX_WORDS: Record<"ai" | "sort", string> = { ai: "AI box", sort: "Sorter's box" };
export const NO_BOX_TEXT = "No box: the AI named the part but didn't mark where";
export const BOX_HIDDEN_TEXT = "box hidden: photo changed";
export const BOX_OFF_TEXT = "Boxes are off";

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

/** A stored box when it is one: four finite numbers, [ymin, xmin, ymax, xmax]
 * on 0-1000 with a size both ways. Values are clamped into the photo; an
 * inverted or empty box is no box (the server cleans them too, but an older
 * case or a hand-edited draft may still hold one). */
export function cleanBox(value: unknown): Box | null {
  if (!Array.isArray(value) || value.length !== 4) return null;
  if (!value.every((n) => typeof n === "number" && Number.isFinite(n))) return null;
  const [y0, x0, y1, x1] = (value as number[]).map((n) => clamp(n, 0, 1000));
  if (y1 <= y0 || x1 <= x0) return null;
  return [y0, x0, y1, x1];
}

/** A box as CSS percentages: it lines up with any element exactly the
 * image's size (bbox_2d's 0-1000 is a top-left origin). Moved here from
 * lib/intakeReview.ts. */
export function bboxStyle(bbox: number[] | null | undefined): CSSProperties | null {
  const box = cleanBox(bbox);
  if (!box) return null;
  const [yMin, xMin, yMax, xMax] = box;
  return { top: `${yMin / 10}%`, left: `${xMin / 10}%`, width: `${(xMax - xMin) / 10}%`, height: `${(yMax - yMin) / 10}%` };
}

/** The frame a box was drawn on, when the evidence says (img_w / img_h). */
export function frameOf(e: Pick<Evidence, "img_w" | "img_h"> | null | undefined): Frame | null {
  const w = e?.img_w;
  const h = e?.img_h;
  return typeof w === "number" && typeof h === "number" && w > 0 && h > 0 ? { w, h } : null;
}

/** Whether a box drawn on `frame` may be drawn on an image of `shown` size:
 * their shapes (width / height) agree within 2%. When either size is not
 * known the box is drawn, as it always was. */
export function aspectMatches(frame: Frame | null | undefined, shown: Frame | null | undefined): boolean {
  if (!frame || !shown || frame.w <= 0 || frame.h <= 0 || shown.w <= 0 || shown.h <= 0) return true;
  const ratio = frame.w / frame.h / (shown.w / shown.h);
  return Math.abs(ratio - 1) <= ASPECT_TOLERANCE;
}

/** One photo of a finding, as the viewer and the cards show it: the photo,
 * its box (or none) and what that read said. */
export type Slide = {
  photo: string;
  box: Box | null;
  kind: BoxKind;
  frame: Frame | null;
  /** The read the entry came from ("overall", "zone:rear", "sort"...). */
  by: string;
  reason: string | null;
  confidence: Evidence["confidence"];
};

const KIND_RANK: Record<string, number> = { ai: 0, sort: 1 };
const kindRank = (kind: BoxKind, box: Box | null) => (box && kind ? (KIND_RANK[kind] ?? 2) : 3);

/** The entry whose box a photo shows when a finding has more than one for
 * it: an AI box, then the sorter's box, then none (contract9.md section
 * 7.3, "Box precedence"). The first entry wins a tie. */
export function pickBox<T extends { box: unknown; box_kind: BoxKind }>(entries: T[]): T | null {
  let best: T | null = null;
  let bestRank = 4;
  for (const e of entries) {
    const box = cleanBox(e.box);
    const rank = kindRank(box ? (e.box_kind ?? "ai") : null, box);
    if (rank < bestRank) {
      best = e;
      bestRank = rank;
    }
  }
  return best;
}

/** A finding's photos, one slide a photo, in its evidence's order (best
 * first, the item's own photo first). Each slide shows one box, by
 * precedence among the entries for that photo; the words come from the
 * first entry that has any. */
export function slidesOf(f: Pick<Finding, "evidence">): Slide[] {
  const byPhoto = new Map<string, Evidence[]>();
  for (const e of f.evidence || []) {
    if (!e?.photo) continue;
    const list = byPhoto.get(e.photo);
    if (list) list.push(e);
    else byPhoto.set(e.photo, [e]);
  }
  const out: Slide[] = [];
  for (const [photo, entries] of byPhoto) {
    const boxed = pickBox(entries);
    const box = boxed ? cleanBox(boxed.box) : null;
    const first = entries[0];
    const worded = entries.find((e) => e.reason) ?? first;
    out.push({
      photo,
      box,
      kind: box ? (boxed?.box_kind ?? "ai") : null,
      frame: box ? frameOf(boxed) : null,
      by: (box ? boxed?.by : first.by) || first.by || "overall",
      reason: worded.reason ?? null,
      confidence: (box ? boxed?.confidence : first.confidence) ?? first.confidence ?? null,
    });
  }
  return out;
}

/** What to say under a photo about its box. */
export function boxLine(slide: Pick<Slide, "box" | "kind"> | null, opts: { hidden?: boolean; boxesOff?: boolean } = {}): string {
  if (!slide?.box || !slide.kind) return NO_BOX_TEXT;
  if (opts.hidden) return `${BOX_WORDS[slide.kind]} (${BOX_HIDDEN_TEXT})`;
  if (opts.boxesOff) return `${BOX_WORDS[slide.kind]} (${BOX_OFF_TEXT.toLowerCase()})`;
  return BOX_WORDS[slide.kind];
}

/** A short chip word for a photo's box: "AI box", "Sorter's box", "no box". */
export function boxChip(slide: Pick<Slide, "box" | "kind"> | null): string {
  return slide?.box && slide.kind ? BOX_WORDS[slide.kind] : "no box";
}

/** The size an image of `natural` size is shown at when fitted inside
 * `stage` (contain: whole image visible, scaled up or down). */
export function fitSize(natural: Frame, stage: Frame): Frame {
  if (natural.w <= 0 || natural.h <= 0 || stage.w <= 0 || stage.h <= 0) return { w: 0, h: 0 };
  const scale = Math.min(stage.w / natural.w, stage.h / natural.h);
  return { w: natural.w * scale, h: natural.h * scale };
}

export const MAX_ZOOM = 4;

/** "Zoom to box": the zoom (1..4) at which the box fills about 60% of the
 * stage, and the scroll offsets that put its centre in the middle. `fit` is
 * the image's size at zoom 1. */
export function zoomToBox(box: Box, fit: Frame, stage: Frame, fill = 0.6): { zoom: number; left: number; top: number } {
  const [y0, x0, y1, x1] = box;
  const bw = ((x1 - x0) / 1000) * fit.w;
  const bh = ((y1 - y0) / 1000) * fit.h;
  const want = Math.min(bw > 0 ? (stage.w * fill) / bw : MAX_ZOOM, bh > 0 ? (stage.h * fill) / bh : MAX_ZOOM);
  const zoom = clamp(Number.isFinite(want) ? want : 1, 1, MAX_ZOOM);
  const cx = ((x0 + x1) / 2000) * fit.w * zoom;
  const cy = ((y0 + y1) / 2000) * fit.h * zoom;
  // The zoomed image is centred when it is smaller than the stage one way.
  const maxLeft = Math.max(0, fit.w * zoom - stage.w);
  const maxTop = Math.max(0, fit.h * zoom - stage.h);
  return { zoom, left: clamp(cx - stage.w / 2, 0, maxLeft), top: clamp(cy - stage.h / 2, 0, maxTop) };
}
