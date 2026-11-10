import type { CSSProperties } from "react";
import { Check, Undo2 } from "lucide-react";

const BASE: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  gap: 3,
  minWidth: 22,
  height: 20,
  padding: "0 6px",
  borderRadius: 999,
  fontSize: 10,
  fontWeight: 700,
  fontFamily: "inherit",
  lineHeight: 1,
  cursor: "pointer",
  whiteSpace: "nowrap",
  flexShrink: 0,
};

/** The small [✓] beside a "Check" or "AI" badge on a table row: a person
 * looked and the AI's value (or the past-case price) stands. `label` names
 * the row for screen readers ("Front door assy RH"). */
export function ReviewConfirmButton({
  label,
  onClick,
  disabled,
  title,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={`Confirm ${label}`}
      title={title || "Confirm: you checked it"}
      style={{
        ...BASE,
        background: "var(--surface-elevated)",
        border: "1px solid var(--badge-green-border)",
        color: "var(--badge-green-text)",
        opacity: disabled ? 0.5 : 1,
        cursor: disabled ? "not-allowed" : "pointer",
      }}
    >
      <Check aria-hidden style={{ width: 12, height: 12 }} />
    </button>
  );
}

/** Takes a person's confirm back (a damage part's tick, "No labour"). */
export function ReviewUndoButton({ label, onClick, disabled }: { label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={`Undo the confirm of ${label}`}
      title="Undo"
      style={{
        ...BASE,
        background: "none",
        border: "1px solid transparent",
        color: "var(--text-muted)",
        opacity: disabled ? 0.5 : 1,
        cursor: disabled ? "not-allowed" : "pointer",
      }}
    >
      <Undo2 aria-hidden style={{ width: 12, height: 12 }} />
    </button>
  );
}
