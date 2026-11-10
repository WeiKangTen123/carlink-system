import { Fragment, type CSSProperties, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, PenLine } from "lucide-react";
import type { Review, ReviewCounts } from "@/lib/review";

/** A review.counts section. */
export type ReviewSection = "cover" | "damage" | "annex_a" | "annex_b" | "annex_c" | "parties";

const NAMES: Record<ReviewSection, string> = {
  cover: "Cover",
  damage: "Damage",
  annex_a: "Annex A",
  annex_b: "Annex B",
  annex_c: "Annex C",
  parties: "Case record",
};

const ALL: ReviewSection[] = ["cover", "damage", "annex_a", "annex_b", "annex_c", "parties"];

/** The same icons and colours as the badges (components/ReviewBadge.tsx). */
const Done = () => <CheckCircle2 aria-hidden className="review-progress-icon" style={{ color: "var(--badge-green-text)" }} />;
const Check = () => <AlertTriangle aria-hidden className="review-progress-icon" style={{ color: "var(--badge-amber-text)" }} />;
const Needs = () => <PenLine aria-hidden className="review-progress-icon" style={{ color: "var(--badge-red-text)" }} />;

/** What is still open in a section: to check (!) and needing a person (✎);
 * for Annex A, the rows needing a price. Null when the section has nothing
 * to review. */
export function sectionOpen(review: Review, section: ReviewSection): { check: number; needs: number } | null {
  if (section === "annex_a") {
    if (!review.annex_a?.length) return null;
    const a = review.counts.annex_a;
    return { check: a.check, needs: a.needs_price };
  }
  const c: ReviewCounts | undefined = review.counts[section];
  if (!c || c.total === 0) return null;
  return { check: c.check, needs: c.needs };
}

type Segment = { section: ReviewSection; parts: ReactNode; words: string };

function segment(review: Review, section: ReviewSection): Segment | null {
  const open = sectionOpen(review, section);
  if (!open) return null;
  const name = NAMES[section];
  const { check, needs } = open;
  const words: string[] = [];
  const parts: ReactNode[] = [];
  const dot = (k: string) => parts.length > 0 && <span key={`dot-${k}`} aria-hidden> · </span>;
  if (section === "annex_a") {
    if (needs) {
      parts.push(
        <Fragment key="needs">
          {needs} <Needs /> price
        </Fragment>
      );
      words.push(`${needs} need${needs === 1 ? "s" : ""} a price`);
    }
  } else if (section === "annex_b" || section === "annex_c") {
    // One or two things each: "Annex B ✎", no count.
    if (needs) {
      parts.push(
        <Fragment key="needs">
          {needs > 1 ? `${needs} ` : ""}
          <Needs />
        </Fragment>
      );
      words.push(needs > 1 ? `${needs} need you` : "needs you");
    }
  } else {
    const c = review.counts[section];
    const done = Math.max(0, c.total - check - needs);
    parts.push(
      <Fragment key="done">
        {done}/{c.total} <Done />
      </Fragment>
    );
    words.push(`${done} of ${c.total} done`);
  }
  if (check) {
    parts.push(
      <Fragment key="check">
        {dot("check")}
        {check} <Check />
      </Fragment>
    );
    words.push(`${check} to check`);
  }
  if (needs && section !== "annex_a" && section !== "annex_b" && section !== "annex_c") {
    parts.push(
      <Fragment key="needs">
        {dot("needs")}
        {needs} <Needs />
      </Fragment>
    );
    words.push(`${needs} need${needs === 1 ? "s" : ""} you`);
  }
  if (parts.length === 0) {
    parts.push(<Done key="done" />);
    words.push("done");
  }
  return { section, parts, words: `${name}: ${words.join(", ")}` };
}

/** The review in one line (contract 7):
 * "Cover 28/37 ✓ · 4 ! · 5 ✎ │ Annex A 3 ✎ price · 2 ! │ Annex B ✎ │ Annex C ✓".
 * ✓ done (AI-read, confirmed, typed or N/A), ! to check, ✎ needs a person.
 * A section with nothing to review is left out; nothing at all without a
 * review (an older API). With onSelect each section is a button (the case
 * page opens its tab). */
export function ReviewProgress({
  review,
  sections = ALL,
  onSelect,
  style,
}: {
  review: Review | null | undefined;
  sections?: ReviewSection[];
  onSelect?: (section: ReviewSection) => void;
  style?: CSSProperties;
}) {
  if (!review) return null;
  const segments = sections.map((s) => segment(review, s)).filter((s): s is Segment => s !== null);
  if (segments.length === 0) return null;
  return (
    <div className="review-progress" role="group" aria-label="Field review" style={style}>
      {segments.map((seg, i) => {
        const inner = (
          <>
            <span className="review-progress-name" aria-hidden>
              {NAMES[seg.section]}
            </span>{" "}
            <span aria-hidden>{seg.parts}</span>
            <span className="review-sr">{seg.words}</span>
          </>
        );
        return (
          <Fragment key={seg.section}>
            {i > 0 && (
              <span className="review-progress-sep" aria-hidden>
                │
              </span>
            )}
            {onSelect ? (
              <button type="button" className="review-progress-seg link" onClick={() => onSelect(seg.section)} title={`Open ${NAMES[seg.section]}`}>
                {inner}
              </button>
            ) : (
              <span className="review-progress-seg">{inner}</span>
            )}
          </Fragment>
        );
      })}
    </div>
  );
}

/** The open counts on a tab label or a button: "4 ! 5 ✎". Nothing when the
 * section has nothing open. */
export function ReviewTabCount({ review, section }: { review: Review | null | undefined; section: ReviewSection }) {
  if (!review) return null;
  const open = sectionOpen(review, section);
  if (!open || (open.check === 0 && open.needs === 0)) return null;
  const words = [open.check ? `${open.check} to check` : "", open.needs ? `${open.needs} need${open.needs === 1 ? "s" : ""} you` : ""]
    .filter(Boolean)
    .join(", ");
  return (
    <span className="review-tab-count" title={words}>
      <span aria-hidden>
        {open.check > 0 && (
          <span className="review-tab-count-part">
            {open.check}
            <Check />
          </span>
        )}
        {open.needs > 0 && (
          <span className="review-tab-count-part">
            {open.needs}
            <Needs />
          </span>
        )}
      </span>
      <span className="review-sr">{words}</span>
    </span>
  );
}
