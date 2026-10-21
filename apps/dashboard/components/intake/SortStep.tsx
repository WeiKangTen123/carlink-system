"use client";

import { useEffect, useMemo, useState } from "react";
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

type GroupKey = PhotoKind | "all" | "unsorted" | "excluded";

type Props = {
  state: IntakeState;
  regrouped: boolean;
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

function inGroup(p: IntakePhoto, group: GroupKey): boolean {
  if (group === "all") return true;
  if (group === "excluded") return p.excluded;
  if (group === "unsorted") return !p.kind;
  return p.kind === group;
}

export function SortStep({ state, regrouped, busy, onRegroup, onToggleExclude, onRemove, onReread, onRetry, onSkipAi, onBack, onNext }: Props) {
  const { photos, stage, progress } = state;
  const [group, setGroup] = useState<GroupKey>("all");
  const [dropTarget, setDropTarget] = useState<GroupKey | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const readIds = useMemo(() => new Set(state.read_photo_ids), [state.read_photo_ids]);

  const running = stage === "sorting" || stage === "reading";
  const visible = photos.filter((p) => inGroup(p, group));
  const groups: { key: GroupKey; label: string; count: number }[] = [
    { key: "all", label: "All photos", count: photos.length },
    ...PHOTO_KINDS.map((k) => ({ key: k as GroupKey, label: PHOTO_KIND_LABELS[k], count: photos.filter((p) => p.kind === k).length })),
    { key: "unsorted", label: "Not sorted yet", count: photos.filter((p) => !p.kind).length },
    { key: "excluded", label: "Excluded from AI", count: photos.filter((p) => p.excluded).length },
  ];

  const openIndex = visible.findIndex((p) => p.id === openId);
  const open = openIndex >= 0 ? visible[openIndex] : null;

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpenId(null);
      if (e.key === "ArrowRight" && openIndex < visible.length - 1) setOpenId(visible[openIndex + 1].id);
      if (e.key === "ArrowLeft" && openIndex > 0) setOpenId(visible[openIndex - 1].id);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, openIndex, visible]);

  let progressText = "";
  let pct = 0;
  if (stage === "sorting") {
    progressText = progress.total ? `Sorting photos ${progress.done} / ${progress.total}` : "Starting…";
    pct = progress.total ? (100 * progress.done) / progress.total : 3;
  } else if (stage === "reading") {
    progressText = "Reading vehicle facts and damage from the key photos…";
    pct = 92;
  }

  return (
    <section className="card intake-panel">
      <div className="intake-panel-head">
        <h2>AI sorting</h2>
        {running ? (
          <div className="intake-progress-row compact">
            <Loader2 className="spin" style={{ width: 14, height: 14, color: "var(--accent-primary)" }} />
            <span>{progressText}</span>
            <div className="intake-progress">
              <div className="intake-progress-fill" style={{ width: `${pct}%` }} />
            </div>
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
            You changed the groups since the AI last read this case. Re-reading updates the AI&apos;s facts; anything you edited or checked is kept.
          </div>
          <button type="button" className="btn-secondary-modern" onClick={onReread} disabled={busy}>
            <RotateCcw style={{ width: 13, height: 13 }} /> Re-read with my changes
          </button>
        </div>
      )}

      <div className="intake-sort-layout">
        <nav className="intake-group-list" aria-label="Photo groups">
          {groups
            .filter((g) => g.count > 0 || g.key === "all" || PHOTO_KINDS.includes(g.key as PhotoKind))
            .map((g) => {
              const isKind = PHOTO_KINDS.includes(g.key as PhotoKind);
              return (
                <button
                  key={g.key}
                  type="button"
                  className={`intake-group ${group === g.key ? "active" : ""} ${dropTarget === g.key ? "drop-target" : ""} ${g.count === 0 ? "empty" : ""}`}
                  onClick={() => setGroup(g.key)}
                  onDragOver={(e) => {
                    if (!isKind) return;
                    e.preventDefault();
                    setDropTarget(g.key);
                  }}
                  onDragLeave={() => setDropTarget(null)}
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
            <div className="intake-empty">{running ? "Photos appear here as they are sorted." : "No photos in this group."}</div>
          ) : (
            <div className="intake-tile-grid large">
              {visible.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={`intake-tile ${p.excluded ? "excluded" : ""} ${readIds.has(p.id) ? "read" : ""}`}
                  draggable={!running}
                  onDragStart={(e) => e.dataTransfer.setData("text/photo-id", p.id)}
                  onClick={() => setOpenId(p.id)}
                  title={p.note || p.name}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={fileUrl(p.thumb_url)} alt={p.note || p.label} loading="lazy" />
                  <span className="intake-tile-label">{p.label}</span>
                  {p.quality === "blurry" && <span className="intake-tile-flag warn">Blurry</span>}
                  {p.quality === "unusable" && <span className="intake-tile-flag danger">Unusable</span>}
                  {p.excluded && <span className="intake-tile-flag">Excluded</span>}
                  <span className="intake-tile-caption">{p.kind ? PHOTO_KIND_LABELS[p.kind] : "Not sorted"}{p.area ? ` · ${p.area}` : ""}</span>
                </button>
              ))}
            </div>
          )}
          {readIds.size > 0 && (
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
        <button type="button" className="btn-primary-modern" onClick={onNext} disabled={stage !== "ready" || busy}>
          {stage === "ready" ? "Looks right — review facts" : running ? "Waiting for the AI…" : "Review facts"}
          <ArrowRight style={{ width: 14, height: 14 }} />
        </button>
      </div>

      {open && (
        <div className="intake-lightbox" role="dialog" aria-modal="true" onClick={() => setOpenId(null)}>
          <div className="intake-lightbox-body" onClick={(e) => e.stopPropagation()}>
            <div className="intake-lightbox-image">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={fileUrl(open.preview_url)} alt={open.note || open.label} />
              <button type="button" className="intake-lightbox-nav prev" disabled={openIndex <= 0} onClick={() => setOpenId(visible[openIndex - 1].id)} aria-label="Previous photo">
                <ChevronLeft style={{ width: 20, height: 20 }} />
              </button>
              <button
                type="button"
                className="intake-lightbox-nav next"
                disabled={openIndex >= visible.length - 1}
                onClick={() => setOpenId(visible[openIndex + 1].id)}
                aria-label="Next photo"
              >
                <ChevronRight style={{ width: 20, height: 20 }} />
              </button>
            </div>
            <aside className="intake-lightbox-side">
              <div className="intake-panel-head">
                <strong>{open.label}</strong>
                <button type="button" className="intake-icon-btn" onClick={() => setOpenId(null)} aria-label="Close">
                  <X style={{ width: 16, height: 16 }} />
                </button>
              </div>
              <div className="intake-muted" style={{ wordBreak: "break-all" }}>{open.name}</div>
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
              <button
                type="button"
                className="intake-danger-btn"
                disabled={running}
                onClick={() => {
                  const next = visible[openIndex + 1] ?? visible[openIndex - 1];
                  onRemove(open.id);
                  setOpenId(next ? next.id : null);
                }}
              >
                <Trash2 style={{ width: 14, height: 14 }} /> Delete photo from case
              </button>
            </aside>
          </div>
        </div>
      )}
    </section>
  );
}
