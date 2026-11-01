"use client";

import { useEffect, useState } from "react";
import { Undo2 } from "lucide-react";
import { releaseSubmissionAction } from "@/app/inbox/actions";
import { longSgt } from "@/lib/inbox";
import { INBOX_MOVED_CODES } from "./DiscardDialog";

/** "Return to inbox" (release P6): a taken public report goes back to the
 * Public Inbox for someone else to take. What was filled in stays with it.
 * Its 30 days don't start again, so one returned after them is deleted at
 * the next clean-up -- the dialog says so. */
export function ReleaseDialog({
  reportId,
  reference,
  expiresAt,
  beforeRelease,
  onClose,
  onReleased,
  onMoved,
}: {
  reportId: string;
  reference: string;
  expiresAt: string | null | undefined;
  /** Runs first (the wizard saves what is on screen); false stops here. */
  beforeRelease?: () => Promise<boolean>;
  onClose: () => void;
  onReleased: () => void;
  onMoved?: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Read once, when the dialog opens.
  const [openedAt] = useState(() => Date.now());

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !saving) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [saving, onClose]);

  const expires = expiresAt ? new Date(expiresAt).getTime() : NaN;
  const pastExpiry = !Number.isNaN(expires) && expires <= openedAt;

  async function release() {
    setSaving(true);
    setError(null);
    if (beforeRelease && !(await beforeRelease())) {
      setSaving(false);
      setError("What's on screen couldn't be saved, so the report wasn't returned. Try again.");
      return;
    }
    const res = await releaseSubmissionAction(reportId);
    setSaving(false);
    if ("error" in res) {
      setError(res.error);
      if (res.code && INBOX_MOVED_CODES.has(res.code)) onMoved?.();
      return;
    }
    onReleased();
  }

  return (
    <div className="modal-backdrop" onClick={() => !saving && onClose()}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="release-title"
        style={{ maxWidth: 480 }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="release-title" style={{ fontSize: 16, fontWeight: 800, margin: "0 0 4px" }}>
          Return {reference} to the inbox?
        </h3>
        <p className="setup-note" style={{ margin: "0 0 10px" }}>
          It leaves your drafts and goes back to the Public Inbox, where anyone who sees all cases can take it. What has
          been filled in so far stays with it.
        </p>
        {Number.isNaN(expires) ? null : pastExpiry ? (
          <div className="intake-alert danger" style={{ fontSize: 12 }}>
            Its 30 days are already up, so it will be deleted at the next clean-up (within about 6 hours) unless someone
            takes it first.
          </div>
        ) : (
          <p className="setup-note" style={{ margin: 0 }}>
            Its 30 days don&apos;t start again: if nobody takes it by <strong>{longSgt(expiresAt)}</strong>, it is deleted.
          </p>
        )}

        {error && (
          <div className="setup-error" role="alert" style={{ marginTop: 12 }}>
            {error}
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 16 }}>
          <button type="button" className="btn-secondary-modern" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="button" className="btn-primary-modern" onClick={() => void release()} disabled={saving}>
            <Undo2 style={{ width: 14, height: 14 }} /> {saving ? "Returning…" : "Return to inbox"}
          </button>
        </div>
      </div>
    </div>
  );
}
