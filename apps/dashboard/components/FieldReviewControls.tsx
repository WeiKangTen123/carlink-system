import { Check } from "lucide-react";
import { ReviewBadge } from "@/components/ReviewBadge";
import { reviewActions, type FieldView, type ReviewAction } from "@/lib/fieldReview";

/** A form field's review beside its label (contract 7): the badge, and --
 * when the host lets this person act -- Confirm (✓) for what the AI read or
 * guessed, N/A for a blank office or optional field, Undo for an N/A.
 * Renders nothing for a field with nothing to show or do. */
export function FieldReviewControls({
  view,
  label,
  photo,
  onReview,
  busy,
  id,
}: {
  view: FieldView | null | undefined;
  /** The field's name, for screen readers ("Registration No."). */
  label: string;
  /** The photo the AI read it from, when the review doesn't say ("P03"):
   * the badge then reads "AI · P03", or "Check · P03" for a guess. */
  photo?: string | null;
  /** Left out, the badge alone (a read-only case). */
  onReview?: (key: string, action: ReviewAction) => void;
  busy?: boolean;
  /** For the field's aria-describedby. */
  id?: string;
}) {
  if (!view) return null;
  const actions = onReview ? reviewActions(view) : [];
  if (view.state === "none" && actions.length === 0) return null;
  const seen = view.review?.photo || photo || null;
  const review = view.review ? { ...view.review, photo: seen } : { state: view.state, photo: seen };
  const detail = view.state === "check" && seen ? seen : undefined;
  return (
    <span className="review-controls" id={id}>
      <ReviewBadge state={view.state} review={review} label={label} detail={detail} />
      {actions.map((action) =>
        action === "confirm" ? (
          <button
            key={action}
            type="button"
            className="review-btn icon"
            disabled={busy}
            aria-label={`Confirm ${label}`}
            title={view.state === "ai" ? "Confirm: it matches the photo" : "Confirm: you checked it"}
            onClick={() => onReview?.(view.key, action)}
          >
            <Check aria-hidden style={{ width: 12, height: 12 }} />
          </button>
        ) : (
          <button
            key={action}
            type="button"
            className="review-btn"
            disabled={busy}
            aria-label={action === "na" ? `${label}: not applicable` : `${label}: undo N/A`}
            title={action === "na" ? "Not applicable: it stays blank" : "Take the N/A back"}
            onClick={() => onReview?.(view.key, action)}
          >
            {action === "na" ? "N/A" : "Undo"}
          </button>
        )
      )}
    </span>
  );
}

/** "Confirm 7 AI-read fields": confirms every field still showing "AI"
 * (never a "Check"). Nothing when there are none. */
export function ConfirmAiButton({ keys, onConfirm, busy }: { keys: string[]; onConfirm: (keys: string[]) => void; busy?: boolean }) {
  if (keys.length === 0) return null;
  return (
    <button
      type="button"
      className="btn-secondary-modern review-confirm-all"
      disabled={busy}
      title="Confirms the fields the AI read from the photos that still say what it read. Fields marked Check stay for you to look at."
      onClick={() => onConfirm(keys)}
    >
      <Check aria-hidden style={{ width: 13, height: 13 }} /> Confirm {keys.length} AI-read field{keys.length === 1 ? "" : "s"}
    </button>
  );
}
