"use client";

import { useState } from "react";
import { LockOpen } from "lucide-react";
import { reopenReportAction } from "../../actions";

export function ReopenButton({ id }: { id: string }) {
  const [loading, setLoading] = useState(false);

  async function handleReopen() {
    if (!confirm("Reopen this report for editing? This resets its status to Draft until it's signed off again.")) return;
    setLoading(true);
    await reopenReportAction(id);
    // router.refresh() left this page showing the "Report Locked" view for
    // up to ~30s after reopen (same Next.js 16 client Router Cache
    // staleness confirmed on SignOffButton) even though the backend's
    // status had already flipped -- a full reload sidesteps it.
    window.location.reload();
  }

  return (
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
  );
}
