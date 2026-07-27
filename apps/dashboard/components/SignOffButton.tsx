"use client";

import { useState } from "react";
import { signOffReportAction } from "@/app/reports/actions";

export function SignOffButton({ id, currentStatus }: { id: string; currentStatus: string }) {
  const [loading, setLoading] = useState(false);

  if (currentStatus === "Signed Off") {
    return (
      <span style={{ background: "#dcfce7", color: "#166534", padding: "6px 12px", borderRadius: 4, fontWeight: "bold", fontSize: 13 }}>
        ✓ Signed Off &amp; Locked
      </span>
    );
  }

  const handleSignOff = async () => {
    if (!confirm("Are you sure you want to sign off and finalize this Car Incident Report?")) return;
    setLoading(true);
    try {
      const result = await signOffReportAction(id, "Surveyor Sign-Off");
      if ("error" in result) throw new Error(result.error);
      // router.refresh() left this showing the pre-signoff button for up to
      // ~30s (Next.js 16's client Router Cache staleTime for this dynamic
      // route) despite revalidatePath having already run server-side --
      // confirmed live: the backend's status flipped to "Signed Off"
      // immediately, but the rendered page didn't. A full reload sidesteps
      // that cache entirely instead of trying to tune staleTimes globally.
      window.location.reload();
    } catch (err: any) {
      alert(`Sign-off failed: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      type="button"
      onClick={handleSignOff}
      disabled={loading}
      className="button-primary"
      style={{ background: "#16a34a", color: "#fff", padding: "8px 16px", borderRadius: 4, cursor: "pointer", border: "none" }}
    >
      {loading ? "Signing Off..." : "✍️ Sign Off & Finalize PDF"}
    </button>
  );
}
