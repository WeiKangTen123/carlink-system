import type { CSSProperties } from "react";
import { AlertTriangle, CheckCircle2, MinusCircle, PenLine, ScanLine } from "lucide-react";
import { REVIEW_WORDS, type FieldReview, type ReviewState } from "@/lib/review";

/** Icon colour and border per state. The words stay in the normal text
 * colour (the badge colours are too light for small text in the light
 * theme); colour sits on the icon and the border only, and every state also
 * differs by icon, word and border style -- never colour alone. */
const LOOK: Record<Exclude<ReviewState, "none">, { icon: typeof ScanLine; colour: string; border: string }> = {
  ai: { icon: ScanLine, colour: "var(--badge-blue-text)", border: "1px solid var(--badge-blue-border)" },
  check: { icon: AlertTriangle, colour: "var(--badge-amber-text)", border: "1px solid var(--badge-amber-border)" },
  needs: { icon: PenLine, colour: "var(--badge-red-text)", border: "1px dashed var(--badge-red-border)" },
  you: { icon: CheckCircle2, colour: "var(--badge-green-text)", border: "1px solid transparent" },
  na: { icon: MinusCircle, colour: "var(--text-muted)", border: "1px solid transparent" },
};

/** One field's review state, as a small badge: "AI · P04", "Check",
 * "Needs you", "You", "N/A". `label` names the field for screen readers
 * ("Plate"). `detail` adds a few words ("from 3 past cases"). Renders
 * nothing for "none". */
export function ReviewBadge({
  state,
  review,
  label,
  detail,
  style,
}: {
  state: ReviewState;
  review?: FieldReview | null;
  label?: string;
  detail?: string;
  style?: CSSProperties;
}) {
  if (state === "none") return null;
  const look = LOOK[state];
  const Icon = look.icon;
  const photo = state === "ai" && review?.photo ? ` · ${review.photo}` : "";
  const who = (state === "you" || state === "na") && review?.by ? `${review.by}${review.at ? `, ${review.at.slice(0, 10)}` : ""}` : "";
  const words = `${REVIEW_WORDS[state]}${photo}${detail ? ` · ${detail}` : ""}`;
  const aria = [label, words, who].filter(Boolean).join(": ");
  return (
    <span
      className="review-badge"
      role="note"
      aria-label={aria}
      title={who || undefined}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: 10,
        lineHeight: 1.4,
        padding: "1px 6px",
        borderRadius: 999,
        border: look.border,
        color: "var(--text-secondary)",
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      <Icon aria-hidden style={{ width: 11, height: 11, color: look.colour }} />
      {words}
    </span>
  );
}
