import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Breadcrumb } from "@/components/Breadcrumb";
import { ReportForm } from "../ReportForm";
import { createReportAction } from "./actions";

export default function NewReportPage() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 4 }}>
        <Breadcrumb
          items={[
            { label: "Home", href: "/" },
            { label: "Cases Repository", href: "/reports" },
            { label: "New Case Intake (FNOL)" },
          ]}
        />
        <Link
          href="/reports"
          className="btn-secondary-modern"
          style={{ fontSize: 12, padding: "5px 12px", display: "inline-flex", alignItems: "center", gap: 6 }}
        >
          <ArrowLeft style={{ width: 13, height: 13 }} />
          <span>Back to Cases Repository</span>
        </Link>
      </div>

      <ReportForm mode="create" action={createReportAction} submitLabel="Save Report & Generate PDF" />
    </div>
  );
}
