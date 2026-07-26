"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { signOffReport } from "@/lib/api";

export function SignOffButton({ id, currentStatus }: { id: string; currentStatus: string }) {
  const [loading, setLoading] = useState(false);
  const router = useRouter();

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
      await signOffReport(id, "Surveyor Sign-Off");
      router.refresh();
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
