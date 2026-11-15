"use client";

import type { DecideAction } from "@/lib/api";
import type { Finding, PhotoLookup } from "@/lib/findings";

export type EvidenceViewerProps = {
  open: boolean;
  mode: "suggestions" | "listed" | "photo";
  findings: Finding[];
  startKey?: string | null;
  startPhoto?: string | null;
  photos: PhotoLookup;
  onDecide?: (key: string, action: DecideAction, looked: boolean) => Promise<void>;
  onShowOnMap?: (key: string) => void;
  onClose: () => void;
  readOnly?: boolean;
};

/** The full-screen evidence viewer: a finding's photos with their boxes and
 * the AI's words, on both the wizard's Review step and the case page
 * (contract9.md section 7.2); replaces intake/DamagePhotoViewer.tsx. Step 1
 * stub: renders nothing -- the evidence UI agent builds it. */
export function EvidenceViewer(props: EvidenceViewerProps) {
  void props;
  return null;
}
