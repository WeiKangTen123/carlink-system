"use client";

import { useEffect, useId, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { CONDITION_WHOLE_VALUES, type ConditionWords } from "@/lib/api";

/** The select's "Other words…" entry. The firm's words never start with "_",
 * so this can't collide with a real Condition. */
const OTHER = "__other__";
/** The firm's longest Conditions use three words ("Distorted/cut/faulty"). */
const MAX_WORDS = 3;

const capitalise = (w: string) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();

/** The picked words as the firm prints them: in the order of its word list
 * (surface, then shape, break, function), the first capitalised, the rest in
 * lower case, joined with "/". ["cut", "Distorted"] -> "Distorted/cut". */
export function joinConditionWords(picked: readonly string[], tokens: readonly string[]): string {
  const wanted = new Set(picked.map((w) => w.toLowerCase()));
  return tokens
    .filter((t) => wanted.has(t.toLowerCase()))
    .map((t, i) => (i === 0 ? capitalise(t) : t.toLowerCase()))
    .join("/");
}

/** The firm's words a Condition is made of, spelt as in the list:
 * "Grazed/cut" -> ["Grazed", "Cut"]. Empty for a whole value or for anything
 * holding a word outside the list -- that isn't the firm's wording, so there
 * are no words to keep. */
export function conditionWordsIn(value: string | null | undefined, tokens: readonly string[]): string[] {
  if (!value) return [];
  const found: string[] = [];
  for (const part of value.split("/")) {
    const word = tokens.find((t) => t.toLowerCase() === part.trim().toLowerCase());
    if (!word) return [];
    found.push(word);
  }
  return found;
}

/** "necessary" -> "Necessary"; null when the value isn't one of the whole values. */
const wholeValueOf = (value: string | null | undefined): string | null =>
  CONDITION_WHOLE_VALUES.find((w) => w.toLowerCase() === (value || "").trim().toLowerCase()) ?? null;

type Props = {
  /** The Condition as saved, or as just chosen while it saves; "" when there is none yet. */
  value: string;
  /** Shown while there is no Condition: the item's damage_type, which is all
   * a case drafted before the firm's words has. */
  fallback?: string | null;
  words: ConditionWords;
  /** Called only with a Condition different from `value`. */
  onChange: (condition: string) => void;
  disabled?: boolean;
  ariaLabel: string;
  /** Names the part in the word picker's heading. */
  partName?: string;
  /** The empty entry's text when there is no Condition and no fallback. */
  placeholder?: string;
  className?: string;
  style?: CSSProperties;
};

/** The Condition editor of tab 1's detected parts and the intake Review
 * step. It only offers the firm's wording, because the Condition prints on
 * Annex A as it stands: the most used Conditions in a select, and "Other
 * words…" to put one together from the firm's words (up to three, joined in
 * the firm's order) or to pick a whole value. There is deliberately no blank
 * entry once a Condition is set: a damaged part always has one, and the API
 * would otherwise print the AI's damage type instead. */
export function ConditionPicker({ value, fallback, words, onChange, disabled, ariaLabel, partName, placeholder = "—", className, style }: Props) {
  const [picking, setPicking] = useState(false);
  return (
    <>
      <select
        className={className}
        style={style}
        value={value}
        disabled={disabled}
        aria-label={ariaLabel}
        onChange={(e) => {
          const next = e.target.value;
          // The select is controlled: choosing "Other words…" opens the
          // picker and the select goes back to showing the current value.
          if (next === OTHER) setPicking(true);
          else if (next && next !== value) onChange(next);
        }}
      >
        {!value && <option value="">{fallback ? `${fallback} (damage type)` : placeholder}</option>}
        {value && !words.common.includes(value) && <option value={value}>{value}</option>}
        {words.common.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
        <option value={OTHER}>Other words…</option>
      </select>
      {picking && (
        <ConditionWordsDialog
          initial={value}
          tokens={words.tokens}
          partName={partName}
          onUse={(condition) => {
            setPicking(false);
            if (condition !== value) onChange(condition);
          }}
          onClose={() => setPicking(false)}
        />
      )}
    </>
  );
}

const chipStyle = (on: boolean, blocked: boolean): CSSProperties => ({
  fontFamily: "inherit",
  fontSize: 12,
  padding: "5px 11px",
  cursor: blocked ? "not-allowed" : "pointer",
  opacity: blocked ? 0.45 : 1,
  ...(on ? { borderColor: "var(--accent-primary)", background: "var(--surface-hover)", color: "var(--text-primary)" } : null),
});

/** Puts a Condition together from the firm's words, or picks a whole value.
 * Rendered into <body> like PhotoPicker: a fixed overlay inside a table
 * wrapper that scrolls sideways would be clipped. */
function ConditionWordsDialog({
  initial,
  tokens,
  partName,
  onUse,
  onClose,
}: {
  initial: string;
  tokens: readonly string[];
  partName?: string;
  onUse: (condition: string) => void;
  onClose: () => void;
}) {
  const headingId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  // Opens on what the cell holds, so changing one word of "Distorted/cut"
  // doesn't mean picking the others again.
  const [initialWords] = useState<string[]>(() => conditionWordsIn(initial, tokens));
  const [picked, setPicked] = useState<string[]>(initialWords);
  const [whole, setWhole] = useState<string | null>(() => wholeValueOf(initial));
  // The same words as the cell keep the cell's own text: the firm sometimes
  // writes its words out of list order ("Shifted/bent/cut"), and opening the
  // picker must not reword it. Only a change of words is rebuilt in order.
  const sameWords = picked.length > 0 && picked.length === initialWords.length && picked.every((w) => initialWords.includes(w));
  const result = whole ?? (sameWords ? initial : joinConditionWords(picked, tokens));

  // Focus moves into the dialog, and back to the select on close.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    return () => opener?.focus();
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // A whole value never joins a word, so picking one side clears the other.
  const toggleWord = (t: string) => {
    setWhole(null);
    setPicked((p) => (p.includes(t) ? p.filter((x) => x !== t) : p.length >= MAX_WORDS ? p : [...p, t]));
  };
  const toggleWhole = (w: string) => {
    setPicked([]);
    setWhole((cur) => (cur === w ? null : w));
  };

  if (typeof document === "undefined") return null;

  // stopPropagation on the overlay too: React passes a portal's clicks up to
  // the table row it was opened from, which has its own click handler.
  return createPortal(
    <div
      className="rv-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby={headingId}
      onClick={(e) => {
        e.stopPropagation();
        onClose();
      }}
    >
      <div className="rv-picker" style={{ width: "min(460px, 100%)" }} onClick={(e) => e.stopPropagation()}>
        <div className="rv-picker-head">
          <h2 id={headingId}>Condition{partName ? ` of ${partName}` : ""}</h2>
          <button ref={closeRef} type="button" className="intake-icon-btn" onClick={onClose} aria-label="Close">
            <X style={{ width: 16, height: 16 }} />
          </button>
        </div>
        <p className="intake-muted rv-picker-hint">Pick up to {MAX_WORDS} of the firm&apos;s words. They print in the firm&apos;s order, joined with &quot;/&quot;.</p>
        <div role="group" aria-label="Condition words" style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {tokens.map((t) => {
            const on = picked.includes(t);
            const blocked = !on && picked.length >= MAX_WORDS;
            return (
              <button key={t} type="button" className="intake-chip" aria-pressed={on} disabled={blocked} onClick={() => toggleWord(t)} style={chipStyle(on, blocked)}>
                {on ? "✓ " : ""}
                {t}
              </button>
            );
          })}
        </div>
        <div className="intake-muted" style={{ fontSize: 12 }}>
          Or one value on its own
        </div>
        <div role="group" aria-label="Values used on their own" style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {CONDITION_WHOLE_VALUES.map((w) => {
            const on = whole === w;
            return (
              <button
                key={w}
                type="button"
                className="intake-chip"
                aria-pressed={on}
                onClick={() => toggleWhole(w)}
                style={chipStyle(on, false)}
                title={w === "Repair" ? "Repaired rather than renewed: the line is priced, then disallowed in full" : undefined}
              >
                {on ? "✓ " : ""}
                {w}
              </button>
            );
          })}
        </div>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontSize: 12 }}>
            Prints as: <strong>{result || "—"}</strong>
          </span>
          <div style={{ display: "flex", gap: 6 }}>
            <button type="button" className="btn-secondary-modern" style={{ fontSize: 12, padding: "6px 12px" }} onClick={onClose}>
              Cancel
            </button>
            <button type="button" className="btn-primary-modern" style={{ fontSize: 12, padding: "6px 12px" }} disabled={!result} onClick={() => onUse(result)}>
              Use
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}
