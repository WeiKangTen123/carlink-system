"use client";

import React from "react";
import { CheckCircle2 } from "lucide-react";
import type { SaveState } from "@/lib/caseState";

/** The small notes every editable tab of the case page shows beside its
 * Save / Discard buttons, and the style of its plain inputs. One place so
 * the four tabs (components/CoverTab.tsx, AnnexATab.tsx, AnnexBTab.tsx,
 * AnnexCTab.tsx) read the same. Moved from CaseAssessmentTab.tsx. */

export const fieldInputStyle: React.CSSProperties = {
  background: "var(--surface-base)",
  color: "var(--text-primary)",
  border: "1px solid var(--border-color)",
  borderRadius: 4,
  padding: "6px 8px",
  fontSize: 12,
  fontFamily: "inherit",
};

/** Why the tab can't be changed, under its heading; nothing when it can. */
export function LockedNote({ isSignedOff, canEdit }: { isSignedOff: boolean; canEdit: boolean }) {
  if (isSignedOff) {
    return <div style={{ fontSize: 11, color: "var(--badge-amber-text)", marginTop: 2 }}>Signed off — reopen the case from the header to change it.</div>;
  }
  if (!canEdit) {
    return <div style={{ fontSize: 11, color: "var(--badge-amber-text)", marginTop: 2 }}>View only — you can&apos;t change this case.</div>;
  }
  return null;
}

export function SavedNote({ state, dirty }: { state: SaveState; dirty: boolean }) {
  if (state !== "saved" || dirty) return null;
  return (
    <span style={{ fontSize: 11, fontWeight: 700, color: "var(--badge-green-text)", display: "inline-flex", alignItems: "center", gap: 4 }}>
      <CheckCircle2 style={{ width: 13, height: 13 }} /> Saved. The PDF and Excel pick it up on the next download.
    </span>
  );
}

export function UnsavedNote({ state, dirty }: { state: SaveState; dirty: boolean }) {
  if (state === "saving" || !dirty) return null;
  return <span style={{ fontSize: 11, fontWeight: 700, color: "var(--badge-amber-text)" }}>Unsaved changes</span>;
}

/** A value filled into the Annex C letter, and the mark for one that is
 * still missing ("(to be agreed)") -- never a guessed figure. */
export const Var = ({ children }: { children: React.ReactNode }) => <span className="letter-var">{children}</span>;
export const Missing = ({ children }: { children: React.ReactNode }) => <span className="letter-missing">{children}</span>;
