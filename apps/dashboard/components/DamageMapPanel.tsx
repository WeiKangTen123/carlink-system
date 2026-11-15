"use client";

import type { DecideAction } from "@/lib/api";
import type { Finding, PhotoLookup, Selection } from "@/lib/findings";

export type DamageMapPanelProps = {
  surface: "intake" | "case";
  findings: Finding[];
  photos: PhotoLookup;
  /** Photo refs in case order (P01 first). */
  photoOrder: string[];
  /** ref -> the sorter's area (data.photo_index[i].area on a filed case). */
  photoAreas: Record<string, string | null>;
  bodyType: string | null;
  onBodyTypeChange?: (b: string) => void;
  selection: Selection;
  onSelect: (s: Selection) => void;
  hover: Selection;
  onHover: (s: Selection) => void;
  onOpenEvidence: (key: string, photo?: string) => void;
  /** [Tick to add] / [Dismiss] on the mesh's selection card. */
  onDecide?: (key: string, action: DecideAction) => Promise<void>;
  onAddDamage?: (zoneKey: string) => void;
  readOnly?: boolean;
};

/** The 3D damage map with its photo, chips, strip and selection card, linked
 * both ways to the findings (contract9.md section 7.2). Step 1 stub: renders
 * nothing -- the 3D damage map agent builds it. */
export function DamageMapPanel(props: DamageMapPanelProps) {
  void props;
  return null;
}
