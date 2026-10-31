"use client";

import { useEffect, useState } from "react";
import { Trash2 } from "lucide-react";
import { DISCARD_NOTE_MAX, DISCARD_REASONS, DISCARD_REASON_LABELS, type DiscardReason } from "@/lib/api";
import { discardSubmissionAction } from "@/app/inbox/actions";

/** Codes that mean the report moved on while the dialog was open (taken,
 * withdrawn, filed): the page should reload to show where it is now. */
export const INBOX_MOVED_CODES = new Set(["inbox_changed", "inbox_gone", "inbox_filed", "inbox_taken"]);

/** Discard one public report (release P6). A reason is required. It deletes
 * the report, its photos and the sender's details for good; the case history
 * keeps only the reference and the reason (and the note). Spam ticks "block
 * this sender" by default: the bot then refuses their Telegram account for
 * 90 days. */
export function DiscardDialog({
  reportId,
  reference,
  onClose,
  onDiscarded,
  onMoved,
}: {
  reportId: string;
  reference: string;
  onClose: () => void;
  onDiscarded: () => void;
  /** The report changed meanwhile (see INBOX_MOVED_CODES). */
  onMoved?: () => void;
}) {
  const [reason, setReason] = useState<DiscardReason | null>(null);
  const [duplicateOf, setDuplicateOf] = useState("");
  const [note, setNote] = useState("");
  // Follows the reason (on for Spam) until the tick is changed by hand.
  const [block, setBlock] = useState(false);
  const [blockTouched, setBlockTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !saving) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [saving, onClose]);

  function pickReason(r: DiscardReason) {
    setReason(r);
    setError(null);
    if (!blockTouched) setBlock(r === "spam");
  }

  async function discard() {
    if (!reason) {
      setError("Pick a reason first.");
      return;
    }
    setSaving(true);
    setError(null);
    const res = await discardSubmissionAction(reportId, {
      reason,
      ...(reason === "duplicate" && duplicateOf.trim() ? { duplicate_of: duplicateOf.trim().toUpperCase() } : {}),
      ...(note.trim() ? { note: note.trim().slice(0, DISCARD_NOTE_MAX) } : {}),
      block_sender: block,
    });
    setSaving(false);
    if ("error" in res) {
      setError(res.status === 422 ? "Check the reason and the note, then try again." : res.error);
      if (res.code && INBOX_MOVED_CODES.has(res.code)) onMoved?.();
      return;
    }
    onDiscarded();
  }

  return (
    <div className="modal-backdrop" onClick={() => !saving && onClose()}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="discard-title"
        style={{ maxWidth: 500 }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="discard-title" style={{ fontSize: 16, fontWeight: 800, margin: "0 0 4px" }}>
          Discard {reference}?
        </h3>
        <p className="setup-note" style={{ margin: "0 0 14px" }}>
          This deletes the report, its photos and the sender&apos;s details for good. The case history keeps only the
          reference and the reason.
        </p>

        <fieldset className="inbox-discard-reasons">
          <legend className="detail-field-label">Reason (required)</legend>
          {DISCARD_REASONS.map((r) => (
            <label key={r} className={`assign-owner-row ${reason === r ? "picked" : ""}`}>
              <input type="radio" name="discard-reason" checked={reason === r} onChange={() => pickReason(r)} disabled={saving} />
              <span>{DISCARD_REASON_LABELS[r]}</span>
            </label>
          ))}
        </fieldset>

        {reason === "duplicate" && (
          <label style={{ display: "block", marginTop: 12 }}>
            <span className="detail-field-label">Duplicate of (optional)</span>
            <input
              type="text"
              className="settings-input"
              value={duplicateOf}
              maxLength={20}
              placeholder="TG-261029-Q8T2"
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => setDuplicateOf(e.target.value)}
              disabled={saving}
            />
          </label>
        )}

        <label style={{ display: "block", marginTop: 12 }}>
          <span className="detail-field-label">Note (optional)</span>
          <input
            type="text"
            className="settings-input"
            value={note}
            maxLength={DISCARD_NOTE_MAX}
            placeholder="A few words. No personal details."
            autoComplete="off"
            onChange={(e) => setNote(e.target.value)}
            disabled={saving}
          />
          <span className="field-hint">
            Kept in the case history, so no names, phone numbers or NRIC. {note.length}/{DISCARD_NOTE_MAX}
          </span>
        </label>

        <label className="user-tick" style={{ marginTop: 12 }}>
          <input
            type="checkbox"
            checked={block}
            disabled={saving}
            onChange={(e) => {
              setBlock(e.target.checked);
              setBlockTouched(true);
            }}
          />
          <span>
            Block this sender for 90 days
            <span className="field-hint">The bot won&apos;t take reports from their Telegram account until then.</span>
          </span>
        </label>

        {error && (
          <div className="setup-error" role="alert" style={{ marginTop: 12 }}>
            {error}
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 16 }}>
          <button type="button" className="btn-secondary-modern" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="button" className="btn-primary-modern inbox-danger-btn" onClick={() => void discard()} disabled={saving || !reason}>
            <Trash2 style={{ width: 14, height: 14 }} /> {saving ? "Discarding…" : "Discard"}
          </button>
        </div>
      </div>
    </div>
  );
}
