"use client";

import type { CheckRun, DecideAction, SuggestionDecision } from "@/lib/api";
import type { Finding, PhotoLookup } from "@/lib/findings";

export type SuggestionPanelProps = {
  surface: "intake" | "case";
  /** kind "suggestion" only. */
  findings: Finding[];
  decisions: SuggestionDecision[];
  check: CheckRun | null;
  photos: PhotoLookup;
  readOnly?: boolean;
  onDecide: (key: string, action: DecideAction, looked: boolean) => Promise<void>;
  onLook: (key: string) => void;
  onShowOnMap?: (key: string) => void;
};

/** "Suggestions to decide (N)": one card per suggestion with [Look] [Add]
 * [Not damaged], on both pages (contract9.md section 7.2); replaces
 * intake/SecondLookPanel.tsx. Step 1 stub: renders nothing -- the evidence
 * UI agent builds it. */
export function SuggestionPanel(props: SuggestionPanelProps) {
  void props;
  return null;
}
