"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  Eye,
  EyeOff,
  Loader2,
  RotateCcw,
  ScanLine,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { fileUrl } from "@/lib/api";
import { PHOTO_KIND_LABELS, PHOTO_KINDS, type IntakePhoto, type IntakeState, type PhotoKind } from "@/lib/intake";

type GroupKey = PhotoKind | "all" | "unsorted" | "excluded" | "unread";

/** Mirrors MAX_READ_PHOTOS in apps/bot-service/app/intake/pipeline.py. */
const MAX_READ_PHOTOS = 32;

type Props = {
  state: IntakeState;
  regrouped: boolean;
  /** Photos uploaded, regrouped or (un)excluded since the last AI read began
   * -- the read never saw them as they are now. */
  changedSinceRead: ReadonlySet<string>;
  aiSkipped: boolean;
  busy: boolean;
  onRegroup: (photoId: string, kind: PhotoKind) => void;
  onToggleExclude: (photoId: string, excluded: boolean) => void;
  onRemove: (photoId: string) => void;
  onReread: () => void;
  onRetry: () => void;
  onSkipAi: () => void;
  onBack: () => void;
  onNext: () => void;
};

function inGroup(p: IntakePhoto, group: GroupKey, readIds: ReadonlySet<string>): boolean {
  if (group === "all") return true;
  if (group === "excluded") return p.excluded;
  if (group === "unsorted") return !p.kind;
  if (group === "unread") return !readIds.has(p.id);
  return p.kind === group;
}

/** Why the AI's last read left this photo out, most specific reason first. */
function unreadReason(p: IntakePhoto, readCount: number, changed: ReadonlySet<string>): string {
  if (p.excluded) return "Excluded";
  if (p.quality === "unusable") return "Unusable";
  if (changed.has(p.id)) return "Added or regrouped after the read";
  // The reader also keeps to a small budget per group (a few log cards, one
  // VIN plate...), so a smaller read can leave photos out too.
  return readCount >= MAX_READ_PHOTOS ? "Over the AI's 32-photo limit" : "Not picked as a key photo";
}

function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

function formatTimeLeft(ms: number): string {
  const s = ms / 1000;
  if (s < 45) return "less than a minute left";
  return `about ${Math.max(1, Math.round(s / 60))} min left`;
}

export function SortStep({
  state,
  regrouped,
  changedSinceRead,
  aiSkipped,
  busy,
  onRegroup,
  onToggleExclude,
  onRemove,
  onReread,
  onRetry,
  onSkipAi,
  onBack,
  onNext,
}: Props) {
  const { photos, stage, progress } = state;
  const [group, setGroup] = useState<GroupKey>("all");
  const [dropTarget, setDropTarget] = useState<GroupKey | null>(null);
  // The open photo and where it sat in the group. When it leaves the group
  // (regrouped, included again, deleted) the viewer moves on to the photo
  // that took its place instead of closing.
  const [viewer, setViewer] = useState<{ id: string; index: number } | null>(null);
  // Ticks every second while the AI works. Null until the first tick, so
  // the time-dependent text only ever renders on the client.
  const [now, setNow] = useState<number | null>(null);
  const readIds = useMemo(() => new Set(state.read_photo_ids), [state.read_photo_ids]);
  const hasRead = readIds.size > 0;

  const running = stage === "sorting" || stage === "reading";
  const activeGroup: GroupKey = group === "unread" && !hasRead ? "all" : group;
  const visible = photos.filter((p) => inGroup(p, activeGroup, readIds));
  const groups: { key: GroupKey; label: string; count: number }[] = [
    { key: "all", label: "All photos", count: photos.length },
    ...PHOTO_KINDS.map((k) => ({ key: k as GroupKey, label: PHOTO_KIND_LABELS[k], count: photos.filter((p) => p.kind === k).length })),
    { key: "unsorted", label: "Not sorted yet", count: photos.filter((p) => !p.kind).length },
    { key: "excluded", label: "Excluded from AI", count: photos.filter((p) => p.excluded).length },
    // Only once a read has happened -- before that, nothing has been read.
    ...(hasRead ? [{ key: "unread" as GroupKey, label: "Not read by AI", count: photos.filter((p) => !readIds.has(p.id)).length }] : []),
  ];

  let openIndex = -1;
  if (viewer && visible.length) {
    openIndex = visible.findIndex((p) => p.id === viewer.id);
    if (openIndex < 0) openIndex = Math.min(viewer.index, visible.length - 1);
  }
  const open = openIndex >= 0 ? visible[openIndex] : null;

  // Keep `viewer` pointing at the photo on screen, and clear it once the
  // group is empty -- a stale id would pop the viewer open again later,
  // whenever that photo came back into view.
  useEffect(() => {
    if (!viewer) return;
    if (!open) setViewer(null);
    else if (open.id !== viewer.id || openIndex !== viewer.index) setViewer({ id: open.id, index: openIndex });
  }, [viewer, open, openIndex]);

  function show(index: number) {
    const p = visible[index];
    if (p) setViewer({ id: p.id, index });
  }

  function selectGroup(key: GroupKey) {
    setGroup(key);
    setViewer(null);
  }

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setViewer(null);
        return;
      }
      // Arrow keys in the Group dropdown change the group; they mustn't
      // also flip to another photo.
      const target = e.target as HTMLElement | null;
      if (target?.closest?.("select, input, textarea, [contenteditable='true']")) return;
      if (e.key === "ArrowRight" && openIndex < visible.length - 1) show(openIndex + 1);
      if (e.key === "ArrowLeft" && openIndex > 0) show(openIndex - 1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, openIndex, visible]);

  useEffect(() => {
    if (!running) return;
    const tick = () => setNow(Date.now());
    const first = setTimeout(tick, 0);
    const timer = setInterval(tick, 1000);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [running]);

  // Server clock vs this one: clamp, so a small skew never shows negative time.
  const startedAt = state.stage_started_at ? Date.parse(state.stage_started_at) : NaN;
  const elapsed = now !== null && !Number.isNaN(startedAt) ? Math.max(0, now - startedAt) : null;

  let progressText = "";
  let timeText = "";
  let pct = 0;
  if (stage === "sorting") {
    progressText = progress.total ? `Sorting photos ${progress.done} / ${progress.total}` : "Sorting photos";
    pct = progress.total ? (100 * progress.done) / progress.total : 3;
    const elapsedText = elapsed !== null ? `${formatElapsed(elapsed)} · ` : "";
    if (progress.done > 0 && progress.total && elapsed !== null) {
      timeText = elapsedText + formatTimeLeft((elapsed / progress.done) * (progress.total - progress.done));
    } else {
      // Sorting runs 3 batches of 12 photos at once, each about a minute
      // (pipeline.SORT_WORKERS, triage.BATCH_SIZE).
      timeText = `${elapsedText}Starting… usually under a minute per 36 photos`;
    }
  } else if (stage === "reading") {
    progressText = "Reading key photos";
    timeText = `${elapsed !== null ? `${formatElapsed(elapsed)} ` : ""}(usually 15–40 s)`;
  }

  // A failed or skipped AI never blocks entering the facts by hand -- also
  // after a reload, when "Continue without AI" is no longer on screen.
  const canContinueByHand = stage === "failed" || aiSkipped;
  const byHand = canContinueByHand && !hasRead && stage !== "ready";
  let nextLabel = "Review facts";
  if (stage === "ready") nextLabel = "Looks right — review facts";
  else if (running) nextLabel = "Waiting for the AI…";
  else if (byHand) nextLabel = "Fill in the facts by hand";
  const canNext = !busy && !running && (stage === "ready" || canContinueByHand);

  const openReason = open && hasRead && !readIds.has(open.id) ? unreadReason(open, readIds.size, changedSinceRead) : null;

  return (
    <section className="card intake-panel">
      <div className="intake-panel-head">
        <h2>AI sorting</h2>
        {running ? (
          <div className="intake-progress-row compact">
            <Loader2 className="spin" style={{ width: 14, height: 14, color: "var(--accent-primary)" }} />
            <span>{progressText}</span>
            <div className={`intake-progress ${stage === "reading" ? "indeterminate" : ""}`}>
              <div className="intake-progress-fill" style={stage === "reading" ? undefined : { width: `${pct}%` }} />
            </div>
            {timeText && <span className="intake-muted intake-progress-time">{timeText}</span>}
          </div>
        ) : stage === "ready" ? (
          <span className="intake-chip ok">
            <ScanLine style={{ width: 12, height: 12 }} /> Facts read from {state.read_photo_ids.length} key photos
          </span>
        ) : null}
      </div>

      {stage === "failed" && (
        <div className="intake-alert danger">
          <AlertTriangle style={{ width: 16, height: 16, flexShrink: 0 }} />
          <div style={{ flex: 1 }}>{state.error || "AI processing failed."}</div>
          <button type="button" className="btn-secondary-modern" onClick={onRetry} disabled={busy}>
            <RotateCcw style={{ width: 13, height: 13 }} /> Retry
          </button>
          <button type="button" className="intake-link" onClick={onSkipAi}>
            Continue without AI
          </button>
        </div>
      )}
      {regrouped && stage === "ready" && (
        <div className="intake-alert info">
          <Sparkles style={{ width: 16, height: 16, flexShrink: 0 }} />
          <div style={{ flex: 1 }}>
            You changed the photos or their groups since the AI last read this case. Re-reading updates the AI&apos;s facts; anything you edited or checked is kept.
          </div>
          <button type="button" className="btn-secondary-modern" onClick={onReread} disabled={busy}>
            <RotateCcw style={{ width: 13, height: 13 }} /> Re-read with my changes
          </button>
        </div>
      )}

      <div className="intake-sort-layout">
        <nav className="intake-group-list" aria-label="Photo groups">
          {groups
            .filter((g) => g.count > 0 || g.key === "all" || g.key === "unread" || PHOTO_KINDS.includes(g.key as PhotoKind))
            .map((g) => {
              const isKind = PHOTO_KINDS.includes(g.key as PhotoKind);
              return (
                <button
                  key={g.key}
                  type="button"
                  className={`intake-group ${activeGroup === g.key ? "active" : ""} ${dropTarget === g.key ? "drop-target" : ""} ${g.count === 0 ? "empty" : ""}`}
                  onClick={() => selectGroup(g.key)}
                  onDragOver={(e) => {
                    if (!isKind) return;
                    e.preventDefault();
                    setDropTarget(g.key);
                  }}
                  onDragLeave={(e) => {
                    // Passing over the label or count inside the button isn't leaving it.
                    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
                    setDropTarget((t) => (t === g.key ? null : t));
                  }}
                  onDrop={(e) => {
                    setDropTarget(null);
                    const id = e.dataTransfer.getData("text/photo-id");
                    if (id && isKind) onRegroup(id, g.key as PhotoKind);
                  }}
                >
                  <span>{g.label}</span>
                  <span className="intake-group-count">{g.count}</span>
                </button>
              );
            })}
          <p className="intake-muted intake-group-hint">Drag a photo onto a group to move it. Excluded photos stay in the case file but are never sent to the AI.</p>
        </nav>

        <div>
          {visible.length === 0 ? (
            <div className="intake-empty">
              {running ? "Photos appear here as they are sorted." : activeGroup === "unread" ? "The AI read every photo." : "No photos in this group."}
            </div>
          ) : (
            <div className="intake-tile-grid large">
              {visible.map((p, i) => {
                const caption =
                  activeGroup === "unread"
                    ? unreadReason(p, readIds.size, changedSinceRead)
                    : `${p.kind ? PHOTO_KIND_LABELS[p.kind] : "Not sorted"}${p.area ? ` · ${p.area}` : ""}`;
                return (
                  <button
                    key={p.id}
                    type="button"
                    className={`intake-tile ${p.excluded ? "excluded" : ""} ${readIds.has(p.id) ? "read" : ""}`}
                    draggable={!running}
                    onDragStart={(e) => e.dataTransfer.setData("text/photo-id", p.id)}
                    onClick={() => setViewer({ id: p.id, index: i })}
                    title={p.note || p.name}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={fileUrl(p.thumb_url)} alt={p.note || p.label} loading="lazy" />
                    <span className="intake-tile-label">{p.label}</span>
                    {p.quality === "blurry" && <span className="intake-tile-flag warn">Blurry</span>}
                    {p.quality === "unusable" && <span className="intake-tile-flag danger">Unusable</span>}
                    {p.excluded && <span className="intake-tile-flag">Excluded</span>}
                    <span className="intake-tile-caption">{caption}</span>
                  </button>
                );
              })}
            </div>
          )}
          {hasRead && (
            <p className="intake-muted" style={{ marginTop: 10 }}>
              <span className="intake-read-dot" /> = one of the key photos the AI read the facts from.
            </p>
          )}
        </div>
      </div>

      <div className="intake-footer">
        <button type="button" className="btn-secondary-modern" onClick={onBack}>
          <ArrowLeft style={{ width: 14, height: 14 }} /> Back
        </button>
        <button type="button" className="btn-primary-modern" onClick={byHand ? onSkipAi : onNext} disabled={!canNext}>
          {nextLabel}
          <ArrowRight style={{ width: 14, height: 14 }} />
        </button>
      </div>

      {/* In <body>, not in this card: the card's backdrop-filter makes it the
          containing block for position: fixed, so the viewer was clipped to
          the card. The wrapper keeps the intake form styles (select, label). */}
      {open &&
        createPortal(
          <div className="intake-shell new-report-form intake-portal">
            <div className="intake-lightbox" role="dialog" aria-modal="true" aria-label={`Photo ${open.label}`} onClick={() => setViewer(null)}>
              <div className="intake-lightbox-body" onClick={(e) => e.stopPropagation()}>
                <div className="intake-lightbox-image">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={fileUrl(open.preview_url)} alt={open.note || open.label} />
                  <button type="button" className="intake-lightbox-nav prev" disabled={openIndex <= 0} onClick={() => show(openIndex - 1)} aria-label="Previous photo">
                    <ChevronLeft style={{ width: 20, height: 20 }} />
                  </button>
                  <button
                    type="button"
                    className="intake-lightbox-nav next"
                    disabled={openIndex >= visible.length - 1}
                    onClick={() => show(openIndex + 1)}
                    aria-label="Next photo"
                  >
                    <ChevronRight style={{ width: 20, height: 20 }} />
                  </button>
                </div>
                <aside className="intake-lightbox-side">
                  <div className="intake-panel-head">
                    <strong>{open.label}</strong>
                    <span className="intake-muted" style={{ marginLeft: "auto" }}>
                      {openIndex + 1} / {visible.length}
                    </span>
                    <button type="button" className="intake-icon-btn" onClick={() => setViewer(null)} aria-label="Close">
                      <X style={{ width: 16, height: 16 }} />
                    </button>
                  </div>
                  <div className="intake-muted" style={{ wordBreak: "break-all" }}>{open.name}</div>
                  {hasRead &&
                    (openReason === null ? (
                      <span className="intake-chip ok intake-read-badge">
                        <ScanLine style={{ width: 12, height: 12 }} /> Read by AI
                      </span>
                    ) : (
                      <span className="intake-chip warn intake-read-badge">
                        <EyeOff style={{ width: 12, height: 12 }} /> Not read by AI · {openReason}
                      </span>
                    ))}
                  {open.note && <p className="intake-lightbox-note">“{open.note}”</p>}
                  <label>
                    Group
                    <select value={open.kind ?? ""} onChange={(e) => onRegroup(open.id, e.target.value as PhotoKind)} disabled={running}>
                      {!open.kind && <option value="">Not sorted</option>}
                      {PHOTO_KINDS.map((k) => (
                        <option key={k} value={k}>
                          {PHOTO_KIND_LABELS[k]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="intake-muted">
                    {open.kind_source === "surveyor" ? "Group set by you" : open.kind ? "Group set by AI" : ""}
                    {open.area ? ` · area: ${open.area}` : ""}
                    {open.quality && open.quality !== "good" ? ` · ${open.quality}` : ""}
                  </div>
                  <button type="button" className="btn-secondary-modern" onClick={() => onToggleExclude(open.id, !open.excluded)} disabled={running}>
                    {open.excluded ? <Eye style={{ width: 14, height: 14 }} /> : <EyeOff style={{ width: 14, height: 14 }} />}
                    {open.excluded ? "Include in AI reading" : "Exclude from AI reading"}
                  </button>
                  {/* Undoable from the toast; the viewer moves on to the next photo. */}
                  <button type="button" className="intake-danger-btn" disabled={running} onClick={() => onRemove(open.id)}>
                    <Trash2 style={{ width: 14, height: 14 }} /> Delete photo from case
                  </button>
                </aside>
              </div>
            </div>
          </div>,
          document.body
        )}
    </section>
  );
}
