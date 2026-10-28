import Link from "next/link";
import { ExternalLink, ShieldAlert } from "lucide-react";
import { aiOffNeedsAdmin } from "@/lib/api";

/** What to do when the AI was refused (release P4, AI_OFF_CODES in
 * lib/api.ts): "Fix in Settings", a link to Settings -> My AI key, or, for
 * what only an admin can put right (a key this server can't read, an
 * account that is off), "Tell an admin".
 *
 * The link opens a new tab: the wizard's uploads, an edit form and an
 * unsaved Annex A all live only in the page, and would be lost by leaving
 * it. `code` null: AI is off for a reason not known here; My AI key says
 * which. */
export function AiFixLink({ code }: { code: string | null }) {
  if (aiOffNeedsAdmin(code)) {
    return (
      <span className="ai-fix-admin" title="Only an admin can fix this">
        <ShieldAlert style={{ width: 12, height: 12, flexShrink: 0 }} /> Tell an admin
      </span>
    );
  }
  return (
    <Link
      href="/settings#my-ai-key"
      prefetch={false}
      target="_blank"
      rel="noopener"
      className="ai-fix-link"
      title="Opens Settings → My AI key in a new tab, so nothing here is lost"
    >
      Fix in Settings <ExternalLink style={{ width: 11, height: 11, flexShrink: 0 }} />
    </Link>
  );
}
