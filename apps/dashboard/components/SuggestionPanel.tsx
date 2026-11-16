"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Box as BoxIcon, Check, Eye, Plus, Undo2, X } from "lucide-react";
import type { CheckRun, DecideAction, SuggestionDecision } from "@/lib/api";
import { boxChip, slidesOf } from "@/lib/evidence";
import {
  aiSays,
  checkWords,
  codeReason,
  confidenceText,
  decisionStates,
  foundByText,
  seenCount,
  sortByCheck,
  type Finding,
  type PhotoLookup,
} from "@/lib/findings";
import { PhotoBoxes } from "./PhotoBoxes";

export type SuggestionPanelProps = {
  surface: "intake" | "case";
  /** kind "suggestion" only. Pass them with the dismissed ones
   * (buildFindings showDismissed): those go under "Decided". */
  findings: Finding[];
  decisions: SuggestionDecision[];
  check: CheckRun | null;
  photos: PhotoLookup;
  readOnly?: boolean;
  /** Keys are finding keys ("sug:<key>"); lib/useSuggestionDecisions takes them. */
  onDecide: (key: string, action: DecideAction, looked: boolean) => Promise<void>;
  onLook: (key: string) => void;
  onShowOnMap?: (key: string) => void;
  /** second_look.reads, for "Found by 1 of 5 reads" (without it: "Found
   * by the rear read"). Optional: added by the evidence UI. */
  reads?: string[] | null;
};

/** How long a decision's Undo stays up. */
export const DECISION_UNDO_MS = 6000;
/** Photos shown on a card. */
const CARD_PHOTOS = 3;

export type DecisionToastState = { key: string; name: string; action: "add" | "dismiss"; version: number };

/** "Added Boot Lid Lock · Undo" after a decision, on both the panel and the
 * viewer. Undo is also in the panel's Decided list, so missing it is no loss. */
export function DecisionToast({
  toast,
  busy,
  onUndo,
  onDismiss,
}: {
  toast: DecisionToastState;
  busy?: boolean;
  onUndo: () => void;
  onDismiss: () => void;
}) {
  useEffect(() => {
    const t = window.setTimeout(onDismiss, DECISION_UNDO_MS);
    return () => window.clearTimeout(t);
    // Restarted for each decision (version), not on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toast.version]);
  const added = toast.action === "add";
  return (
    <div className="intake-toast ev-toast" role="status" aria-live="polite">
      {added ? (
        <Check style={{ width: 15, height: 15, flexShrink: 0, color: "var(--badge-green-text)" }} aria-hidden="true" />
      ) : (
        <X style={{ width: 15, height: 15, flexShrink: 0, color: "var(--text-muted)" }} aria-hidden="true" />
      )}
      <span>{added ? `Added ${toast.name} to the damaged parts` : `${toast.name}: marked not damaged`}</span>
      <button type="button" className="intake-toast-undo" onClick={onUndo} disabled={busy}>
        <Undo2 style={{ width: 14, height: 14 }} /> Undo
      </button>
      <button type="button" className="intake-icon-btn intake-toast-close" onClick={onDismiss} aria-label="Close">
        <X style={{ width: 13, height: 13 }} />
      </button>
      <span key={toast.version} className="intake-toast-timer" style={{ animationDuration: `${DECISION_UNDO_MS}ms` }} aria-hidden="true" />
    </div>
  );
}

/** "15 Nov, 10:11" in Singapore time. h23: Node and browsers disagree on
 * midnight under hour12:false (as caseFields.formatSgtDateTime). */
function when(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("en-SG", { timeZone: "Asia/Singapore", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
}

const errorText = (e: unknown) => (e instanceof Error && e.message ? e.message : "Couldn't save the decision. Try again.");

/** "Suggestions to decide (N)": one card per suggestion with [Look] [Add]
 * [Not damaged], on both pages (contract9.md section 7.2); replaces
 * intake/SecondLookPanel.tsx.
 *
 * - Cards go in the re-check's order; "AI re-check: probably not damaged"
 *   ones are a group of their own at the bottom, still addable.
 * - Nothing is added until a person decides; each decision goes to the
 *   server (onDecide), which stamps who and when.
 * - "Decided: 2 added, 1 not damaged [Show]" lists the decisions, each
 *   with Undo. Read-only (signed off): no decisions, Look still works.
 * - Hidden when there are no suggestions and no decisions. */
export function SuggestionPanel({ surface, findings, decisions, check, photos, readOnly, onDecide, onLook, onShowOnMap, reads }: SuggestionPanelProps) {
  const [showDecided, setShowDecided] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<DecisionToastState | null>(null);
  // Suggestions opened in the viewer from here: an Add then counts as looked.
  const looked = useRef(new Set<string>());

  const suggestions = useMemo(() => findings.filter((f) => f.kind === "suggestion"), [findings]);
  const open = useMemo(() => sortByCheck(suggestions.filter((f) => f.status === "suggestion")), [suggestions]);
  const likely = open.filter((f) => f.check?.verdict !== "no");
  const unlikely = open.filter((f) => f.check?.verdict === "no");

  // The decisions as they stand, newest first, plus suggestions dismissed
  // before decisions were recorded (an older case's dismissed list).
  const decided = useMemo(() => {
    const states = decisionStates(decisions);
    const byKey = new Map(suggestions.map((f) => [f.partKey, f]));
    const rows = [...states.values()]
      .sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0))
      .map((d) => ({ key: d.key, name: byKey.get(d.key)?.name || d.part || d.key, action: d.action, by: d.by_name, at: d.at, finding: byKey.get(d.key) ?? null }));
    for (const f of suggestions) {
      if (f.status === "dismissed" && !states.has(f.partKey))
        rows.push({ key: f.partKey, name: f.name, action: "dismissed", by: null, at: "", finding: f });
    }
    return rows;
  }, [decisions, suggestions]);
  const addedCount = decided.filter((r) => r.action === "added").length;
  const dismissedCount = decided.length - addedCount;

  if (!open.length && !decided.length) return null;

  const look = (key: string) => {
    looked.current.add(key);
    onLook(key);
  };

  const decide = async (f: { key: string; name: string }, action: DecideAction) => {
    if (busy || readOnly) return;
    setBusy(f.key);
    setError(null);
    try {
      await onDecide(f.key, action, looked.current.has(f.key));
      setToast(action === "undo" ? null : { key: f.key, name: f.name, action, version: Date.now() });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const card = (f: Finding) => {
    const slides = slidesOf(f);
    const facts = [foundByText(f, reads), seenCount(f), confidenceText(f.confidence)].filter(Boolean).join(" · ");
    const verdict = checkWords(f.check);
    const isBusy = busy === f.key;
    return (
      <li key={f.key} className={`sg-card ${f.check?.verdict === "no" ? "is-unlikely" : ""}`} aria-busy={isBusy}>
        <div className="sg-card-head">
          <span className="sg-name">{f.name}</span>
          {f.condition && <span className="intake-chip">{f.condition}</span>}
          {verdict && <span className={`sg-check sg-check-${f.check?.verdict}`}>{verdict}</span>}
        </div>
        <p className={`sg-reason ${f.reason ? "" : "is-code"}`}>{f.reason ? aiSays(f.reason) : codeReason(f)}</p>
        {f.check?.reason && <p className="sg-reason">Re-check: {aiSays(f.check.reason)}</p>}
        {facts && <div className="sg-facts">{facts}</div>}
        {f.otherSideListed && (
          <div className="sg-note">
            The list already has {f.otherSideListed}. Check this isn&apos;t the same damage on the other side.
          </div>
        )}
        <div className="sg-card-foot">
          {slides.length > 0 && (
            <div className="sg-thumbs">
              {slides.slice(0, CARD_PHOTOS).map((s) => {
                const info = photos(s.photo);
                const gone = !info || info.deleted || !(info.thumb || info.src);
                // A photo deleted from a draft has no label left: no raw id either.
                const label = info?.label || "Photo";
                return gone ? (
                  <span key={s.photo} className="sg-thumb is-gone" title="This photo was deleted">
                    <span className="sg-thumb-gone">{label}</span>
                    <span className="sg-thumb-label">deleted</span>
                  </span>
                ) : (
                  <button
                    key={s.photo}
                    type="button"
                    className="sg-thumb"
                    onClick={() => look(f.key)}
                    aria-label={`Look at ${f.name} in ${info.label} (${boxChip(s)})`}
                  >
                    <PhotoBoxes
                      src={info.thumb || info.src}
                      alt=""
                      fit="natural"
                      className="pb-compact"
                      marks={[{ key: f.key, label: f.name, status: f.status, box: s.box, kind: s.kind, frame: s.frame }]}
                    />
                    <span className="sg-thumb-label">
                      {info.label} · {boxChip(s)}
                    </span>
                  </button>
                );
              })}
              {slides.length > CARD_PHOTOS && <span className="sg-more">+{slides.length - CARD_PHOTOS}</span>}
            </div>
          )}
          <div className="sg-actions">
            <button type="button" className="btn-secondary-modern sg-btn" onClick={() => look(f.key)}>
              <Eye style={{ width: 14, height: 14 }} /> Look
            </button>
            {!readOnly && (
              <>
                <button type="button" className="btn-primary-modern sg-btn" disabled={Boolean(busy)} onClick={() => void decide(f, "add")}>
                  <Plus style={{ width: 14, height: 14 }} /> Add
                </button>
                <button type="button" className="btn-secondary-modern sg-btn" disabled={Boolean(busy)} onClick={() => void decide(f, "dismiss")}>
                  <X style={{ width: 14, height: 14 }} /> Not damaged
                </button>
              </>
            )}
            {onShowOnMap && (
              <button type="button" className="intake-link sg-map" onClick={() => onShowOnMap(f.key)} aria-label={`Show ${f.name} on the 3D model`}>
                <BoxIcon style={{ width: 13, height: 13 }} /> 3D
              </button>
            )}
          </div>
        </div>
      </li>
    );
  };

  return (
    <section className="card intake-panel sg-panel" aria-labelledby={`sg-title-${surface}`}>
      <div className="intake-panel-head">
        <h2 id={`sg-title-${surface}`}>{readOnly ? `Suggestions not decided (${open.length})` : `Suggestions to decide (${open.length})`}</h2>
        <span className="intake-muted">
          {readOnly ? "Read-only." : surface === "case" ? "Nothing is added until you do. An added part goes to Annex A." : "Nothing is added until you do."}
        </span>
      </div>
      {check?.status === "failed" && (
        <div className="intake-muted sg-run-note">
          AI re-check didn&apos;t run{check.note ? `: ${check.note}` : ""}. The suggestions are in the order the reads found them.
        </div>
      )}
      {error && (
        <div className="sg-error" role="alert">
          {error}
        </div>
      )}
      {likely.length > 0 && <ul className="sg-list">{likely.map(card)}</ul>}
      {unlikely.length > 0 && (
        <div className="sg-group">
          <h3 className="sg-group-head">
            AI re-check: probably not damaged ({unlikely.length})
            <span className="intake-muted"> Still here: add one if the photo shows damage.</span>
          </h3>
          <ul className="sg-list">{unlikely.map(card)}</ul>
        </div>
      )}
      {!open.length && <div className="intake-muted">Every suggestion is decided.</div>}
      {decided.length > 0 && (
        <div className="sg-decided">
          <div className="sg-decided-line">
            <span>
              Decided: {addedCount} added, {dismissedCount} not damaged
            </span>
            <button type="button" className="intake-link" aria-expanded={showDecided} onClick={() => setShowDecided((v) => !v)}>
              {showDecided ? "Hide" : "Show"}
            </button>
          </div>
          {showDecided && (
            <ul className="sg-decided-list">
              {decided.map((r) => (
                <li key={r.key}>
                  <span className={`sg-decided-mark ${r.action === "added" ? "is-added" : "is-dismissed"}`} aria-hidden="true">
                    {r.action === "added" ? "✓" : "×"}
                  </span>
                  <span className="sg-decided-name">{r.name}</span>
                  <span className="intake-muted" suppressHydrationWarning>
                    {r.action === "added" ? "added" : "not damaged"}
                    {r.by ? ` by ${r.by}` : ""}
                    {r.at ? `, ${when(r.at)}` : ""}
                  </span>
                  <span className="sg-decided-actions">
                    {r.finding && (
                      <button type="button" className="intake-link" onClick={() => look(r.finding!.key)}>
                        Look
                      </button>
                    )}
                    {!readOnly && (
                      <button
                        type="button"
                        className="intake-link"
                        disabled={Boolean(busy)}
                        onClick={() => void decide({ key: `sug:${r.key}`, name: r.name }, "undo")}
                        aria-label={`Undo ${r.action === "added" ? "adding" : "not damaged on"} ${r.name}`}
                      >
                        Undo
                      </button>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {toast && (
        <DecisionToast
          toast={toast}
          busy={Boolean(busy)}
          onUndo={() => void decide({ key: toast.key, name: toast.name }, "undo")}
          onDismiss={() => setToast(null)}
        />
      )}
    </section>
  );
}
