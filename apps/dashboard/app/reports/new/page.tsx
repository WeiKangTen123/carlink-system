import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Breadcrumb } from "@/components/Breadcrumb";
import { IntakeWizard } from "@/components/intake/IntakeWizard";
import { getTaxonomy } from "@/lib/api";

/** New Case Intake. `?draft=<id>` resumes a saved draft -- the wizard puts
 * it in the URL as soon as the first photo creates the draft, and drafts in
 * the Cases Repository's New Intake / Draft lane link back here (see
 * app/reports/[id]/page.tsx). */
export default async function NewReportPage({ searchParams }: { searchParams: Promise<{ draft?: string }> }) {
  const { draft } = await searchParams;
  const taxonomy = await getTaxonomy();

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

      {/* Keyed on the draft so switching drafts remounts with fresh state. */}
      <IntakeWizard key={draft ?? "new"} initialDraftId={draft ?? null} taxonomy={taxonomy} />
    </div>
  );
}
