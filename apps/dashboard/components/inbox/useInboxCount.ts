"use client";

import { useEffect, useState } from "react";
import type { InboxCount } from "@/lib/api";
import { INBOX_CHANGED_EVENT } from "@/lib/inbox";
import { countInboxAction } from "@/app/inbox/actions";

/** The Public Inbox count (GET /inbox/count), for people who see all cases.
 * Asked when `enabled` turns on and whenever `refreshKey` changes (the
 * sidebar passes the path, so every route change asks again), when a take,
 * release or discard here announces a change, and -- with `pollMs` -- on a
 * timer while the tab is in view. Null until known, or when it can't be. */
export function useInboxCount(enabled: boolean, opts: { pollMs?: number; refreshKey?: unknown } = {}): InboxCount | null {
  const { pollMs, refreshKey } = opts;
  const [count, setCount] = useState<InboxCount | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let live = true;
    let inFlight = false;
    const load = () => {
      if (document.visibilityState !== "visible" || inFlight) return;
      inFlight = true;
      countInboxAction()
        .then((c) => {
          if (live && c) setCount(c);
        })
        .catch(() => {
          // Keep the last count; the next poll tries again.
        })
        .finally(() => {
          inFlight = false;
        });
    };
    load();
    const timer = pollMs ? setInterval(load, pollMs) : undefined;
    const onVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    if (pollMs) document.addEventListener("visibilitychange", onVisible);
    window.addEventListener(INBOX_CHANGED_EVENT, load);
    return () => {
      live = false;
      if (timer) clearInterval(timer);
      if (pollMs) document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener(INBOX_CHANGED_EVENT, load);
    };
  }, [enabled, pollMs, refreshKey]);

  return enabled ? count : null;
}
