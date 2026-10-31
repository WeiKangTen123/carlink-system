import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { FileQuestion, Inbox } from "lucide-react";
import { getInboxItem } from "@/lib/api";
import { InboxDetailClient } from "./InboxDetailClient";

export const metadata: Metadata = {
  title: "Public report // Carlink Studio",
  // See app/inbox/page.tsx: what the sender typed is never made a link.
  formatDetection: { telephone: false, email: false, address: false },
};

/** What /inbox/{id} shows for a 404: no such report, not one from the
 * public, deleted (discarded, withdrawn, past its 30 days), or not one this
 * account may open. The API answers all of them the same way. */
function InboxItemNotFound() {
  return (
    <div className="case-not-found">
      <div className="card-glass case-not-found-card">
        <div className="case-not-found-icon">
          <FileQuestion style={{ width: 28, height: 28 }} />
        </div>
        <h1>Report not found</h1>
        <p>
          This report isn&apos;t in the inbox. It may have been discarded, withdrawn by the sender or deleted after 30 days, or
          it isn&apos;t one you can open.
        </p>
        <Link href="/inbox" prefetch={false} className="btn-primary-modern" style={{ padding: "9px 20px", fontSize: 13 }}>
          <Inbox style={{ width: 15, height: 15 }} /> Back to the inbox
        </Link>
      </div>
    </div>
  );
}

/** One report from the public (release P6), read-only: nothing here can be
 * edited or autosaved before someone takes it. Take it opens it in the
 * wizard as the taker's draft. */
export default async function InboxItemPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const item = await getInboxItem(id);
  if (!item) return <InboxItemNotFound />;
  // Filed: it's a case now, with the sender's details on it.
  if (item.state === "filed") redirect(`/reports/${id}`);

  return <InboxDetailClient item={item} renderedAt={Date.now()} />;
}
