"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { reopenReportAction } from "../../actions";

export function ReopenButton({ id }: { id: string }) {
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  async function handleReopen() {
    if (!confirm("Reopen this report for editing? This resets its status to Draft until it's signed off again.")) return;
    setLoading(true);
    try {
      await reopenReportAction(id);
      router.refresh();
    } finally {
      setLoading(false);
    }
  }

  return (
    <button type="button" className="submit-button" onClick={handleReopen} disabled={loading}>
      {loading ? "Reopening..." : "🔓 Reopen for Editing"}
    </button>
  );
}
