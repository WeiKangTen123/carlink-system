"use client";

import { useState } from "react";
import { LockOpen } from "lucide-react";
import { reopenReportAction } from "../../actions";

/** Reopening withdraws a signature, so only the surveyor who signed (by
 * account) or an admin may. `allowed` is the case's permissions.reopen from
 * the API, which enforces the same rule. */
export function ReopenButton({ id, allowed }: { id: string; allowed: boolean }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleReopen() {
    if (!confirm("Reopen this report for editing? This resets its status to Draft until it's signed off again.")) return;
    setLoading(true);
    setError(null);
    const result = await reopenReportAction(id);
    if ("error" in result) {
      setError(result.error);
      setLoading(false);
      return;
    }
    // router.refresh() left this page showing the "Report Locked" view for
    // up to ~30s after reopen (same Next.js 16 client Router Cache
    // staleness confirmed on SignOffButton) even though the backend's
    // status had already flipped -- a full reload sidesteps it.
    window.location.reload();
  }

  if (!allowed) {
    return (
      <p className="form-hint">
        Only the surveyor who signed this report, or an admin, can reopen it.
      </p>
    );
  }

  return (
    <>
      <button
        type="button"
        className="btn-primary-modern"
        onClick={handleReopen}
        disabled={loading}
        style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "8px 16px" }}
      >
        <LockOpen style={{ width: 14, height: 14 }} />
        {loading ? "Reopening..." : "Reopen for Editing"}
      </button>
      {error && <div className="setup-error" style={{ marginTop: 12 }}>{error}</div>}
    </>
  );
}
