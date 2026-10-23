"use client";

import { Trash2, Undo2, X } from "lucide-react";

type Props = {
  message: string;
  /** How long the undo window lasts; drives the shrinking bar only. The
   * wizard owns the actual timer. */
  durationMs: number;
  /** Changes whenever the window restarts (another photo deleted), so the
   * bar starts again from full. */
  timerKey: number;
  onUndo: () => void;
  /** Close now: the delete goes through without waiting. */
  onDismiss: () => void;
};

/** "Photo deleted · Undo" at the bottom of the wizard. */
export function UndoToast({ message, durationMs, timerKey, onUndo, onDismiss }: Props) {
  return (
    <div className="intake-toast" role="status" aria-live="polite">
      <Trash2 style={{ width: 15, height: 15, flexShrink: 0, color: "var(--text-muted)" }} />
      <span>{message}</span>
      <button type="button" className="intake-toast-undo" onClick={onUndo}>
        <Undo2 style={{ width: 14, height: 14 }} /> Undo
      </button>
      <button type="button" className="intake-icon-btn intake-toast-close" onClick={onDismiss} aria-label="Dismiss">
        <X style={{ width: 13, height: 13 }} />
      </button>
      <span key={timerKey} className="intake-toast-timer" style={{ animationDuration: `${durationMs}ms` }} aria-hidden="true" />
    </div>
  );
}
