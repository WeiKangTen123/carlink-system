"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, ArrowRight, X } from "lucide-react";
import { addCheckText, type AddCheck, type FilingProblem } from "@/lib/reviewChecks";

/** A small question over the page (Phase 1 F): Escape, the X or a click
 * outside is the safe answer (onClose); focus moves in, and back on close.
 * Rendered into <body>, as ConditionPicker's dialog: a fixed overlay inside
 * a table wrapper that scrolls sideways would be clipped. */
function DialogShell({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const headingId = useId();
  const boxRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    // The first action button, else the close button.
    (boxRef.current?.querySelector<HTMLElement>("[data-autofocus]") ?? boxRef.current?.querySelector<HTMLElement>("button"))?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      opener?.focus({ preventScroll: true });
    };
  }, []);

  if (typeof document === "undefined") return null;
  return createPortal(
    <div
      className="rv-modal"
      onClick={(e) => {
        e.stopPropagation();
        onClose();
      }}
    >
      <div ref={boxRef} className="rv-picker rc-dialog" role="alertdialog" aria-modal="true" aria-labelledby={headingId} onClick={(e) => e.stopPropagation()}>
        <div className="rv-picker-head">
          <AlertTriangle style={{ width: 16, height: 16, flexShrink: 0, color: "var(--badge-amber-text, #b45309)" }} aria-hidden="true" />
          <h2 id={headingId}>{title}</h2>
          <button type="button" className="intake-icon-btn" onClick={onClose} aria-label="Close">
            <X style={{ width: 16, height: 16 }} />
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body
  );
}

/** The check before "File case" (P-parts 5.2): the rows that need the
 * person, each with [Go to it]; [Back to review] or [File anyway].
 * onClose (Escape, X): stay where you are, nothing filed. */
export function FilingCheckDialog({
  problems,
  onGoTo,
  onBack,
  onFileAnyway,
  onClose,
}: {
  problems: FilingProblem[];
  onGoTo: (problem: FilingProblem) => void;
  onBack: () => void;
  onFileAnyway: () => void;
  onClose: () => void;
}) {
  const n = problems.length;
  return (
    <DialogShell title={`${n} ${n === 1 ? "part needs" : "parts need"} you before filing`} onClose={onClose}>
      <p className="intake-muted rv-picker-hint">These rows of the damaged parts list would not print as they should on Annex A.</p>
      <ul className="rc-problem-list">
        {problems.map((p) => (
          <li key={p.index}>
            <span className="rc-problem-text">
              <strong>
                Row {p.index + 1} · {p.label}
              </strong>{" "}
              — {p.text}
            </span>
            <button type="button" className="btn-secondary-modern rv-small-btn" onClick={() => onGoTo(p)} aria-label={`Go to row ${p.index + 1}, ${p.label}`}>
              Go to it <ArrowRight style={{ width: 12, height: 12 }} />
            </button>
          </li>
        ))}
      </ul>
      <div className="rc-dialog-actions">
        <button type="button" className="btn-secondary-modern" onClick={onBack} data-autofocus>
          Back to review
        </button>
        <button type="button" className="btn-primary-modern" onClick={onFileAnyway}>
          File anyway
        </button>
      </div>
    </DialogShell>
  );
}

/** The question before an add (P-parts 5.4): the same part is already a
 * row -- [Go to row 3] or [Add anyway]; its other side is -- [Yes, add] or
 * [Cancel]. */
export function AddCheckDialog({
  check,
  onAdd,
  onGoTo,
  onCancel,
}: {
  check: NonNullable<AddCheck>;
  onAdd: () => void;
  onGoTo: (index: number) => void;
  onCancel: () => void;
}) {
  const duplicate = check.kind === "duplicate";
  return (
    <DialogShell title={duplicate ? "Already on the list" : "The other side?"} onClose={onCancel}>
      <p className="rc-dialog-text">{addCheckText(check)}</p>
      <p className="intake-muted rv-picker-hint">
        {duplicate
          ? "Annex A keeps one line per part: a second row's condition and remarks never print. Change the listed row instead, unless this really is a second one."
          : "Both sides damaged? Add it. The same damage read on the wrong side? Change the listed row's part instead."}
      </p>
      <div className="rc-dialog-actions">
        {duplicate ? (
          <>
            <button type="button" className="btn-primary-modern" onClick={() => onGoTo(check.index)} data-autofocus>
              Go to row {check.index + 1}
            </button>
            <button type="button" className="btn-secondary-modern" onClick={onAdd}>
              Add anyway
            </button>
          </>
        ) : (
          <>
            <button type="button" className="btn-secondary-modern" onClick={onCancel} data-autofocus>
              Cancel
            </button>
            <button type="button" className="btn-primary-modern" onClick={onAdd}>
              Yes, add
            </button>
          </>
        )}
      </div>
    </DialogShell>
  );
}
