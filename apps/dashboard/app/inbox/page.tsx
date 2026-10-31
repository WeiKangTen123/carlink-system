import type { Metadata } from "next";
import Link from "next/link";
import { redirect, unstable_rethrow } from "next/navigation";
import { Lock } from "lucide-react";
import { errorCode, getCurrentUser, listInbox, loginPath, type InboxList, type InboxView } from "@/lib/api";
import { InboxClient } from "./InboxClient";

export const metadata: Metadata = {
  title: "Public Inbox // Carlink Studio",
  // Senders' numbers are shown as typed text; no phone, email or address
  // turned into a link by the browser (iOS Safari does that by default).
  formatDetection: { telephone: false, email: false, address: false },
};

/** Who may not open the inbox: everyone but admins and staff who see all
 * cases (the API answers them 403 not_allowed). */
function InboxNotAllowed() {
  return (
    <div className="card-glass settings-lock-card">
      <div className="settings-lock-icon">
        <Lock style={{ width: 20, height: 20 }} />
      </div>
      <div style={{ minWidth: 0 }}>
        <h2 className="settings-lock-title">Only admins and staff who see all cases can open the Public Inbox</h2>
        <p className="setup-note" style={{ margin: "0 0 14px" }}>
          Reports from the public are given out from here. Ask an admin if one should be yours.
        </p>
        <Link href="/reports" prefetch={false} className="btn-primary-modern settings-lock-link">
          Back to cases
        </Link>
      </div>
    </div>
  );
}

/** Public Inbox (release P6): reports members of the public sent through the
 * company Telegram bot, oldest first. ?view=taken lists the ones someone has
 * taken but not filed yet. */
export default async function InboxPage({
  searchParams,
}: {
  searchParams: Promise<{ view?: string; discarded?: string }>;
}) {
  const me = await getCurrentUser();
  if (!me) redirect(await loginPath());
  if (!(me.role === "admin" || me.can_view_all_cases)) return <InboxNotAllowed />;

  const { view: viewParam, discarded } = await searchParams;
  const view: InboxView = viewParam === "taken" ? "taken" : "new";

  let list: InboxList | null = null;
  let error: string | null = null;
  try {
    list = await listInbox(view);
  } catch (err) {
    unstable_rethrow(err);
    if (errorCode(err) === "not_allowed") return <InboxNotAllowed />;
    error = err instanceof Error ? err.message : "Failed to load the inbox";
  }

  return (
    <InboxClient
      // Each tab starts afresh: the selection belongs to the list it was
      // made on.
      key={view}
      view={view}
      list={list}
      error={error}
      discarded={typeof discarded === "string" && discarded.length <= 32 ? discarded : null}
      renderedAt={Date.now()}
    />
  );
}
