"use client";

import { useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, FolderOpen, ImagePlus, Loader2, RotateCcw, Sparkles, UploadCloud, X } from "lucide-react";
import { fileUrl, type ReportData } from "@/lib/api";
import { formatBytes, type IntakePhoto, type IntakeStage } from "@/lib/intake";

export type UploadStatus = "queued" | "uploading" | "done" | "duplicate" | "failed" | "rejected";

export type UploadItem = {
  key: string;
  file: File;
  name: string;
  size: number;
  position: number;
  status: UploadStatus;
  error?: string;
};

/** Every file in a drop, including the contents of dropped folders.
 *
 * The entries are taken synchronously, before the first await: a
 * DataTransfer's items are only readable during the drop event itself. */
async function filesFromDrop(dt: DataTransfer): Promise<File[]> {
  const entries = Array.from(dt.items)
    .map((item) => (item.kind === "file" ? item.webkitGetAsEntry?.() : null))
    .filter((e): e is FileSystemEntry => Boolean(e));
  if (entries.length === 0) return Array.from(dt.files);

  const files: File[] = [];
  async function walk(entry: FileSystemEntry): Promise<void> {
    if (entry.isFile) {
      files.push(await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject)));
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      // readEntries returns at most ~100 entries per call; keep reading
      // until it returns an empty batch.
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
        if (batch.length === 0) break;
        for (const child of batch) await walk(child);
      }
    }
  }
  for (const entry of entries) await walk(entry);
  return files;
}

type Props = {
  photos: IntakePhoto[];
  uploads: UploadItem[];
  stage: IntakeStage | null;
  data: ReportData;
  onDataChange: (patch: Partial<ReportData>) => void;
  notes: string;
  onNotesChange: (notes: string) => void;
  /** The AI is reading the case: its result is merged into this form when
   * it finishes, so the form can't change meanwhile. */
  locked: boolean;
  maxPhotos: number;
  maxPhotoBytes: number;
  busy: boolean;
  onFiles: (files: File[]) => void;
  onRetryFailed: () => void;
  onRemovePhoto: (photoId: string) => void;
  onStart: () => void;
  onContinue: () => void;
};

export function UploadStep({
  photos,
  uploads,
  stage,
  data,
  onDataChange,
  notes,
  onNotesChange,
  locked,
  maxPhotos,
  maxPhotoBytes,
  busy,
  onFiles,
  onRetryFailed,
  onRemovePhoto,
  onStart,
  onContinue,
}: Props) {
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  const counted = uploads.filter((u) => u.status !== "rejected");
  const finished = counted.filter((u) => u.status === "done" || u.status === "duplicate").length;
  const inFlight = uploads.filter((u) => u.status === "queued" || u.status === "uploading");
  const failed = uploads.filter((u) => u.status === "failed");
  const duplicates = uploads.filter((u) => u.status === "duplicate").length;
  const rejected = uploads.filter((u) => u.status === "rejected");
  const pct = counted.length ? Math.round((100 * finished) / counted.length) : 0;
  const unsorted = photos.filter((p) => !p.kind).length;
  const running = stage === "sorting" || stage === "reading";

  function handleInput(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files || []);
    // Reset so choosing the same files again still fires onChange.
    e.target.value = "";
    if (files.length) onFiles(files);
  }

  async function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const files = await filesFromDrop(e.dataTransfer);
    if (files.length) onFiles(files);
  }

  let primary: { label: string; action: () => void; icon: React.ReactNode };
  if (running) {
    primary = { label: "View AI progress", action: onContinue, icon: <Loader2 className="spin" style={{ width: 15, height: 15 }} /> };
  } else if (stage === "ready" && unsorted === 0) {
    primary = { label: "Next: check AI sorting", action: onContinue, icon: <CheckCircle2 style={{ width: 15, height: 15 }} /> };
  } else if (stage === "ready") {
    primary = { label: `Sort ${unsorted} new photo${unsorted === 1 ? "" : "s"} & re-read`, action: onStart, icon: <Sparkles style={{ width: 15, height: 15 }} /> };
  } else if (stage === "failed") {
    primary = { label: "Retry AI processing", action: onStart, icon: <RotateCcw style={{ width: 15, height: 15 }} /> };
  } else {
    primary = { label: "Start AI processing", action: onStart, icon: <Sparkles style={{ width: 15, height: 15 }} /> };
  }
  const canStart = !busy && inFlight.length === 0 && (photos.length > 0 || notes.trim().length > 0);

  return (
    <section className="card intake-panel">
      <div className="intake-panel-head">
        <h2>Upload evidence</h2>
        <span className="intake-muted">
          {photos.length} / {maxPhotos} photos
        </span>
      </div>

      <div
        className={`intake-dropzone ${dragging ? "dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
      >
        <UploadCloud style={{ width: 30, height: 30, color: "var(--accent-primary)" }} />
        <div className="intake-dropzone-title">Drop photos or a whole case folder here</div>
        <div className="intake-muted">
          JPG · PNG · WEBP · HEIC · up to {formatBytes(maxPhotoBytes)} each · up to {maxPhotos} photos per case
        </div>
        <div className="intake-dropzone-actions">
          <button type="button" className="btn-secondary-modern" onClick={() => fileInput.current?.click()}>
            <ImagePlus style={{ width: 14, height: 14 }} /> Browse photos
          </button>
          <button type="button" className="btn-secondary-modern" onClick={() => folderInput.current?.click()}>
            <FolderOpen style={{ width: 14, height: 14 }} /> Choose folder
          </button>
        </div>
        <input ref={fileInput} type="file" accept="image/*,.heic" multiple hidden onChange={handleInput} />
        <input
          ref={folderInput}
          type="file"
          multiple
          hidden
          onChange={handleInput}
          {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
        />
      </div>

      {counted.length > 0 && (
        <div className="intake-upload-status">
          <div className="intake-progress-row">
            <span>
              {inFlight.length > 0 ? "Uploading" : "Uploaded"} {finished} / {counted.length}
            </span>
            <div className="intake-progress">
              <div className="intake-progress-fill" style={{ width: `${pct}%` }} />
            </div>
            <span className="intake-muted">{pct}%</span>
          </div>
          <div className="intake-upload-notes">
            {failed.length > 0 && (
              <span className="intake-chip danger">
                {failed.length} failed
                <button type="button" className="intake-link" onClick={onRetryFailed}>
                  Retry
                </button>
              </span>
            )}
            {duplicates > 0 && <span className="intake-chip">{duplicates} duplicate{duplicates === 1 ? "" : "s"} skipped</span>}
            {rejected.length > 0 && (
              <span className="intake-chip warn" title={rejected.map((r) => `${r.name}: ${r.error}`).join("\n")}>
                {rejected.length} file{rejected.length === 1 ? "" : "s"} not added
              </span>
            )}
          </div>
        </div>
      )}

      {(photos.length > 0 || inFlight.length > 0 || failed.length > 0) && (
        <div className="intake-tile-grid">
          {photos.map((p) => (
            <div key={p.id} className="intake-tile" title={p.name}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={fileUrl(p.thumb_url)} alt={p.label || p.name} loading="lazy" />
              {p.label && <span className="intake-tile-label">{p.label}</span>}
              <button
                type="button"
                className="intake-tile-remove"
                onClick={() => onRemovePhoto(p.id)}
                aria-label={`Remove ${p.name}`}
                disabled={running}
              >
                <X style={{ width: 12, height: 12 }} />
              </button>
            </div>
          ))}
          {[...inFlight, ...failed].map((u) => (
            <div key={u.key} className={`intake-tile pending ${u.status}`} title={u.error || u.name}>
              {u.status === "failed" ? (
                <AlertTriangle style={{ width: 18, height: 18, color: "var(--badge-red-text)" }} />
              ) : (
                <Loader2 className={u.status === "uploading" ? "spin" : ""} style={{ width: 18, height: 18, color: "var(--text-muted)" }} />
              )}
              <span className="intake-tile-name">{u.name}</span>
            </div>
          ))}
        </div>
      )}

      <label className="intake-field-wide">
        What happened? <span className="intake-optional">(optional, helps the AI)</span>
        <textarea
          rows={3}
          placeholder="e.g. Rear-ended at PIE exit 12 while stationary in traffic..."
          value={notes}
          onChange={(e) => onNotesChange(e.target.value)}
          disabled={locked}
        />
      </label>

      <div className="intake-field-grid three">
        <label>
          Filed by
          <input
            type="text"
            placeholder="Reporter name"
            value={data.reporter_name ?? ""}
            disabled={locked}
            onChange={(e) => onDataChange({ reporter_name: e.target.value })}
          />
        </label>
        <label>
          Contact
          <input
            type="text"
            placeholder="e.g. +65 9123 4567"
            value={data.reporter_contact ?? ""}
            disabled={locked}
            onChange={(e) => onDataChange({ reporter_contact: e.target.value })}
          />
        </label>
        <label>
          Company / Dept
          <input
            type="text"
            placeholder="e.g. Fleet Operations"
            value={data.reporter_department ?? ""}
            disabled={locked}
            onChange={(e) => onDataChange({ reporter_department: e.target.value })}
          />
        </label>
      </div>

      <div className="intake-footer">
        <span className="intake-muted">
          {inFlight.length > 0
            ? "Waiting for uploads to finish…"
            : photos.length === 0
              ? "Add photos to start. Your draft saves automatically."
              : "The AI sorts every photo, then reads the vehicle facts and damage."}
        </span>
        <button type="button" className="btn-primary-modern" onClick={primary.action} disabled={!running && !canStart}>
          {primary.icon} {primary.label}
        </button>
      </div>
    </section>
  );
}
