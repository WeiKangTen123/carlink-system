"use client";

import { useState } from "react";
import { CheckCircle2, ShieldCheck } from "lucide-react";
import { signOffReportAction } from "@/app/reports/actions";

export function SignOffButton({ id, currentStatus }: { id: string; currentStatus: string }) {
  const [loading, setLoading] = useState(false);

  if (currentStatus === "Signed Off") {
    return (
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          background: "var(--badge-green-bg)",
          color: "var(--badge-green-text)",
          border: "1px solid var(--badge-green-border)",
          padding: "6px 12px",
          borderRadius: 6,
          fontWeight: 700,
          fontSize: 12,
        }}
      >
        <ShieldCheck style={{ width: 14, height: 14 }} /> Signed Off &amp; Locked
      </span>
    );
  }

  const handleSignOff = async () => {
    if (!confirm("Are you sure you want to sign off and finalize this Car Incident Report?")) return;
    setLoading(true);
    try {
      const result = await signOffReportAction(id, {
        surveyor_name: "Patrick Ng",
        qualifications: "MIMI, MIRTE, LCGI, I ENG, LAE, CGLI FTC",
        license_number: "SURV-SG-0492",
        firm_name: "Carlink Consultancy",
        terms_accepted: true,
      });
      if ("error" in result) throw new Error(result.error);
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
      className="btn-primary-modern"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "8px 16px",
      }}
    >
      <CheckCircle2 style={{ width: 14, height: 14 }} />
      {loading ? "Signing Off..." : "Sign Off & Finalize PDF"}
    </button>
  );
}
