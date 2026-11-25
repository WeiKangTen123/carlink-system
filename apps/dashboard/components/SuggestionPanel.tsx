"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Box as BoxIcon, Check, Eye, Plus, Undo2, X } from "lucide-react";
import type { CheckRun, DecideAction, DecideResult, SuggestionDecision } from "@/lib/api";
import { boxChip, slidesOf } from "@/lib/evidence";
import {
  BUCKET_LABELS,
  LINKS_PER_PARENT,
  STRIP_DOWN_TEXT,
  aiSays,
  alsoCheckText,
  alsoLinkedText,
  assemblyDamagedText,
  checkWords,
  codeReason,
  confidenceText,
  confirmAtStripDown,
  decisionStates,
  fittedText,
  foundByText,
  hopText,
  intactText,
  linkBucket,
  linkCheckLine,
  linkGroupTitle,
  linkRunNote,
  parentName,
  seenCount,
  sideFromText,
  signText,
  stripDownBlockNote,
  suggestionSections,
  type AssemblyGroup,
  type Finding,
  type LinkGroup,
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
  /** Contract 10: second_look.link_run, what the linked check did; its note
   * goes under "Linked parts to check". Optional: added by the review fix. */
  linkRun?: CheckRun | null;
  photos: PhotoLookup;
  readOnly?: boolean;
  /** Keys are finding keys ("sug:<key>"); lib/useSuggestionDecisions takes
   * them. May resolve with the server's answer: an add answered with item
   * null was on the list already, and the toast says so. */
  onDecide: (key: string, action: DecideAction, looked: boolean) => Promise<DecideResult | void>;
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

/** "listed": an add the server answered with item null -- the part was on
 * the list already, so nothing was added and there is nothing to undo. */
export type DecisionToastState = { key: string; name: string; action: "add" | "dismiss" | "listed"; version: number };

/** The toast for a decision the server answered: "listed" when an add found
 * the part on the list already (item null). */
export function toastFor(f: { key: string; name: string }, action: "add" | "dismiss", r: DecideResult | void): DecisionToastState {
  const listed = action === "add" && typeof r === "object" && r !== null && !r.item;
  return { key: f.key, name: f.name, action: listed ? "listed" : action, version: Date.now() };
}

/** "Added Boot Lid Lock · Undo" after a decision, on both the panel and the
 * viewer. Undo is also in the panel's Decided list, so missing it is no loss.
 * "Already on the list" has no Undo: nothing was done. */
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
  const listed = toast.action === "listed";
  return (
    <div className="intake-toast ev-toast" role="status" aria-live="polite">
      {added || listed ? (
        <Check style={{ width: 15, height: 15, flexShrink: 0, color: "var(--badge-green-text)" }} aria-hidden="true" />
      ) : (
        <X style={{ width: 15, height: 15, flexShrink: 0, color: "var(--text-muted)" }} aria-hidden="true" />
      )}
      <span>
        {listed ? `Already on the list: ${toast.name}` : added ? `Added ${toast.name} to the damaged parts` : `${toast.name}: marked not damaged`}
      </span>
      {!listed && (
        <button type="button" className="intake-toast-undo" onClick={onUndo} disabled={busy}>
          <Undo2 style={{ width: 14, height: 14 }} /> Undo
        </button>
      )}
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
 * - Hidden when there are no suggestions and no decisions.
 * - Contract 10: with linked parts, the zone suggestions are under "Seen in
 *   photos" and the linked parts under "Linked parts to check", grouped by
 *   the listed part they hang off ("Linked to: Rear Bumper (...)"), each
 *   tagged Seen / Likely / Strip-down, at most 4 a group before "Show more",
 *   the crush path collapsed and the checks' "probably not" ones last.
 *   Their reason is the link table's, never "AI says" (the linked check's
 *   own words are: "AI check: likely — AI says: ..."); nothing is ticked.
 *   The linked check's outcome note sits under their heading. */
export function SuggestionPanel({ surface, findings, decisions, check, linkRun, photos, readOnly, onDecide, onLook, onShowOnMap, reads }: SuggestionPanelProps) {
  const [showDecided, setShowDecided] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<DecisionToastState | null>(null);
  // Suggestions opened in the viewer from here: an Add then counts as looked.
  const looked = useRef(new Set<string>());

  // Contract 10: the groups whose extra rows / crush path are shown.
  const [moreOpen, setMoreOpen] = useState<Set<string>>(() => new Set());
  const [crushOpen, setCrushOpen] = useState<Set<string>>(() => new Set());
  // K4: the folded "Check at strip-down" block, opened by a person.
  const [blockOpen, setBlockOpen] = useState(false);

  const suggestions = useMemo(() => findings.filter((f) => f.kind === "suggestion"), [findings]);
  const sections = useMemo(() => suggestionSections(suggestions.filter((f) => f.status === "suggestion")), [suggestions]);
  const open = useMemo(
    () => [
      ...sections.seen,
      ...sections.other,
      ...sections.assemblies.flatMap((g) => [...g.rows, ...g.crush, ...g.probablyNot]),
      ...sections.groups.flatMap((g) => [...g.rows, ...g.crush, ...g.probablyNot]),
      ...sections.stripDown,
    ],
    [sections]
  );
  // Without linked parts, `seen` is every suggestion in the re-check's order.
  const likely = sections.seen.filter((f) => f.check?.verdict !== "no");
  const unlikely = sections.seen.filter((f) => f.check?.verdict === "no");

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
      const r = await onDecide(f.key, action, looked.current.has(f.key));
      setToast(action === "undo" ? null : toastFor(f, action, r));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const card = (f: Finding, inBlock = false) => {
    const isLink = f.source === "link";
    // A linked part's card shows only its own photos; the listed part's
    // photo is in the viewer, captioned as the listed part's.
    const slides = slidesOf(f).filter((s) => !(isLink && s.by === "link"));
    const sided = isLink && !f.sideFrom && f.part.includes("(side undetermined)") ? "side not known" : null;
    const oem = isLink && f.oem?.length ? `the parts catalogue puts it with the ${parentName(f) ?? "listed part"}` : null;
    // K4: in the strip-down block a card names its group; a second hop's says via what.
    const where = inBlock && f.assemblyName ? f.assemblyName : null;
    const facts = [where, hopText(f), sideFromText(f), sided, foundByText(f, reads), seenCount(f), confidenceText(f.confidence), oem]
      .filter(Boolean)
      .join(" · ");
    const fitted = isLink ? fittedText(f) : null;
    const signs = isLink ? (f.signsYes ?? []) : [];
    const verdict = checkWords(f.check);
    const isBusy = busy === f.key;
    const bucket = isLink ? linkBucket(f) : null;
    const strip = confirmAtStripDown(f);
    const intact = intactText(f.intactIn, photos);
    // In the strip-down block a card sits apart from its parent's group, so
    // without a group name it says which listed part(s) it hangs off.
    const also = alsoLinkedText(f, inBlock && !f.assemblyName);
    const lc = linkCheckLine(f.linkCheck);
    const unlikelyCard = f.check?.verdict === "no" || f.linkCheck?.verdict === "no";
    return (
      <li
        key={f.key}
        className={`sg-card ${unlikelyCard ? "is-unlikely" : ""} ${isLink ? "lk-card" : ""} ${f.hidden ? "is-hidden" : ""}`}
        aria-busy={isBusy}
      >
        <div className="sg-card-head">
          {bucket && <span className={`lk-bucket lk-bucket-${bucket}`}>{BUCKET_LABELS[bucket]}</span>}
          <span className="sg-name">{f.name}</span>
          {fitted && <span className="lk-fitted">{fitted}</span>}
          {f.hidden && <span className="intake-chip lk-hidden">Hidden</span>}
          {f.condition && <span className="intake-chip">{f.condition}</span>}
          {verdict && <span className={`sg-check sg-check-${f.check?.verdict}`}>{verdict}</span>}
        </div>
        {strip && <p className="lk-strip">{STRIP_DOWN_TEXT}</p>}
        <p className={`sg-reason ${f.reason ? "" : "is-code"}`}>{f.reason ? aiSays(f.reason) : codeReason(f)}</p>
        {signs.length > 0 && (
          <div className="lk-signs" aria-label="Signs seen">
            {signs.map((sign) => (
              <span key={sign.id} className="lk-sign">
                Sign: {signText(sign, photos)}
              </span>
            ))}
          </div>
        )}
        {lc && <p className={`sg-reason lk-check lk-check-${f.linkCheck?.verdict}`}>{lc}</p>}
        {f.check?.reason && <p className="sg-reason">Re-check: {aiSays(f.check.reason)}</p>}
        {facts && <div className="sg-facts">{facts}</div>}
        {also && <div className="sg-facts">{also}</div>}
        {intact && <div className="sg-note lk-intact">{intact}</div>}
        {f.otherSideListed && (
          <div className="sg-note">
            The list already has {f.otherSideListed}. Check this isn&apos;t the same damage on the other side.
          </div>
        )}
        {f.wrongEndNote && <div className="sg-note">{f.wrongEndNote}</div>}
        {f.inventoryNote && <div className="sg-note">{f.inventoryNote}</div>}
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

  const toggle = (set: (u: (s: Set<string>) => Set<string>) => void, key: string) =>
    set((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  /** "Linked to: Rear Bumper (Cracked/deformed · crushed)" and its parts. */
  const group = (g: LinkGroup) => {
    const more = moreOpen.has(g.key);
    const shown = more ? g.rows : g.rows.slice(0, LINKS_PER_PARENT);
    const crush = crushOpen.has(g.key);
    const title = linkGroupTitle(g);
    const parentKey = g.parent?.findingKey;
    return (
      <div key={g.key || "-"} className="lk-group" role="group" aria-label={title}>
        <h4 className="lk-group-head">
          <span>{title}</span>
          {onShowOnMap && parentKey && (
            <button
              type="button"
              className="intake-link sg-map"
              onClick={() => onShowOnMap(parentKey)}
              aria-label={`Show the ${g.parent?.name} and its linked parts on the 3D model`}
            >
              <BoxIcon style={{ width: 13, height: 13 }} /> 3D
            </button>
          )}
        </h4>
        {shown.length > 0 && <ul className="sg-list">{shown.map((f) => card(f))}</ul>}
        {g.rows.length > LINKS_PER_PARENT && (
          <button type="button" className="intake-link lk-more" aria-expanded={more} onClick={() => toggle(setMoreOpen, g.key)}>
            {more ? "Show fewer" : `Show ${g.rows.length - LINKS_PER_PARENT} more`}
          </button>
        )}
        {g.crush.length > 0 && (
          <div className="lk-crush">
            <div className="lk-crush-line">
              <span>
                <span aria-hidden="true">{crush ? "⌄" : "›"}</span> {g.crush.length} more along the crush path
              </span>
              <button type="button" className="intake-link" aria-expanded={crush} onClick={() => toggle(setCrushOpen, g.key)}>
                {crush ? "Hide" : "Show"}
              </button>
            </div>
            {crush && <ul className="sg-list">{g.crush.map((f) => card(f))}</ul>}
          </div>
        )}
        {g.probablyNot.length > 0 && (
          <div className="lk-not">
            <h5 className="lk-not-head">
              Probably not ({g.probablyNot.length})<span className="intake-muted"> Still here: add one if the car shows damage.</span>
            </h5>
            <ul className="sg-list">{g.probablyNot.map((f) => card(f))}</ul>
          </div>
        )}
      </div>
    );
  };

  /** K4: "Front bumper assembly (damaged: Front Bumper ...)" and its parts,
   * the yes signs as chips, "Also check (no part name yet): ..." last. */
  const assemblyGroup = (g: AssemblyGroup) => {
    const more = moreOpen.has(g.key);
    const shown = more ? g.rows : g.rows.slice(0, LINKS_PER_PARENT);
    const crush = crushOpen.has(g.key);
    const damaged = assemblyDamagedText(g);
    const also = alsoCheckText(g.alsoCheck);
    const parentKey = g.parents.find((p) => p.parent.findingKey)?.parent.findingKey;
    return (
      <div key={`asm:${g.key}`} className="lk-group lk-assembly" role="group" aria-label={`${g.name}${damaged ? ` (${damaged})` : ""}`}>
        <div className="lk-asm-head">
          <h4 className="lk-group-head">
            <span>{g.name}</span>
            {onShowOnMap && parentKey && (
              <button
                type="button"
                className="intake-link sg-map"
                onClick={() => onShowOnMap(parentKey)}
                aria-label={`Show the ${g.parents[0]?.parent.name} and its linked parts on the 3D model`}
              >
                <BoxIcon style={{ width: 13, height: 13 }} /> 3D
              </button>
            )}
          </h4>
          {damaged && <div className="lk-asm-damaged">{damaged}</div>}
          {g.signs.length > 0 && (
            <div className="lk-signs" aria-label="Signs seen">
              {g.signs.map((sign) => (
                <span key={sign.id} className="lk-sign">
                  Sign: {signText(sign, photos)}
                </span>
              ))}
            </div>
          )}
        </div>
        {shown.length > 0 && <ul className="sg-list">{shown.map((f) => card(f))}</ul>}
        {g.rows.length > LINKS_PER_PARENT && (
          <button type="button" className="intake-link lk-more" aria-expanded={more} onClick={() => toggle(setMoreOpen, g.key)}>
            {more ? "Show fewer" : `Show ${g.rows.length - LINKS_PER_PARENT} more`}
          </button>
        )}
        {g.crush.length > 0 && (
          <div className="lk-crush">
            <div className="lk-crush-line">
              <span>
                <span aria-hidden="true">{crush ? "⌄" : "›"}</span> {g.crush.length} more along the crush path
              </span>
              <button type="button" className="intake-link" aria-expanded={crush} onClick={() => toggle(setCrushOpen, g.key)}>
                {crush ? "Hide" : "Show"}
              </button>
            </div>
            {crush && <ul className="sg-list">{g.crush.map((f) => card(f))}</ul>}
          </div>
        )}
        {g.probablyNot.length > 0 && (
          <div className="lk-not">
            <h5 className="lk-not-head">
              Probably not ({g.probablyNot.length})<span className="intake-muted"> Still here: add one if the car shows damage.</span>
            </h5>
            <ul className="sg-list">{g.probablyNot.map((f) => card(f))}</ul>
          </div>
        )}
        {also && <p className="lk-also">{also}</p>}
      </div>
    );
  };

  /** K4: "› Check at strip-down (N) not counted in the parts above · k via
   * a second hop [Show]": folded until a person opens it. */
  const stripDownBlock = () => (
    <div className="lk-block">
      <div className="lk-block-line">
        <span className="lk-block-title">
          <span aria-hidden="true">{blockOpen ? "⌄" : "›"}</span> Check at strip-down ({sections.stripDown.length})
          <span className="intake-muted"> {stripDownBlockNote(sections.stripDown)}</span>
        </span>
        <button type="button" className="intake-link" aria-expanded={blockOpen} onClick={() => setBlockOpen((v) => !v)}>
          {blockOpen ? "Hide" : "Show"}
        </button>
      </div>
      {blockOpen && <ul className="sg-list">{sections.stripDown.map((f) => card(f, true))}</ul>}
    </div>
  );

  const linkedCount =
    sections.assemblies.reduce((n, g) => n + g.rows.length + g.crush.length + g.probablyNot.length, 0) +
    sections.groups.reduce((n, g) => n + g.rows.length + g.crush.length + g.probablyNot.length, 0) +
    sections.stripDown.length;
  const linkNote = linkRunNote(linkRun);

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
      {sections.hasLinks && sections.seen.length > 0 && (
        <h3 className="lk-section-head">
          Seen in photos ({sections.seen.length})<span className="intake-muted"> found by a zone read, not by the main read</span>
        </h3>
      )}
      {likely.length > 0 && <ul className="sg-list">{likely.map((f) => card(f))}</ul>}
      {unlikely.length > 0 && (
        <div className="sg-group">
          <h3 className="sg-group-head">
            AI re-check: probably not damaged ({unlikely.length})
            <span className="intake-muted"> Still here: add one if the photo shows damage.</span>
          </h3>
          <ul className="sg-list">{unlikely.map((f) => card(f))}</ul>
        </div>
      )}
      {sections.other.length > 0 && (
        <>
          <h3 className="lk-section-head">
            Other suggestions ({sections.other.length})<span className="intake-muted"> from past cases or next to a part found</span>
          </h3>
          <ul className="sg-list">{sections.other.map((f) => card(f))}</ul>
        </>
      )}
      {linkedCount > 0 && (
        <>
          <h3 className="lk-section-head">
            Linked parts to check ({linkedCount})<span className="intake-muted"> tied to parts on the list; check each one on the car</span>
          </h3>
          {linkNote && <div className="intake-muted sg-run-note lk-run-note">{linkNote}</div>}
          {sections.assemblies.map(assemblyGroup)}
          {sections.groups.map(group)}
          {sections.stripDown.length > 0 && stripDownBlock()}
        </>
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
