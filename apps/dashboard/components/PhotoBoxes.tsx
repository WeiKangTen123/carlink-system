"use client";

import type { Box } from "@/lib/api";
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

/** A photo with its findings' boxes: the ONLY code that draws boxes
 * (contract9.md section 7.2, conflict #11). Step 1 stub: the photo alone,
 * no boxes yet -- the evidence UI agent draws them. */
export function PhotoBoxes({ src, alt, className }: PhotoBoxesProps) {
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt={alt} className={className} />;
}
