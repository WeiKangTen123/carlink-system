import Link from "next/link";
import { FileQuestion, FileText } from "lucide-react";

/** What a case address shows when the API answers 404: there is no such
 * case, or it isn't one this account may open. The API answers both the
 * same way, so this says the same for both. */
export function CaseNotFound() {
  return (
    <div className="case-not-found">
      <div className="card-glass case-not-found-card">
        <div className="case-not-found-icon">
          <FileQuestion style={{ width: 28, height: 28 }} />
        </div>
        <h1>Case not found</h1>
        <p>This case doesn&apos;t exist, or it isn&apos;t one you can open. Check the link, or ask an admin.</p>
        <Link href="/reports" prefetch={false} className="btn-primary-modern" style={{ padding: "9px 20px", fontSize: 13 }}>
          <FileText style={{ width: 15, height: 15 }} /> Back to cases
        </Link>
      </div>
    </div>
  );
}
