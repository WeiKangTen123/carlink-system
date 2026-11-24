"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Tag, Undo2, X } from "lucide-react";
import type { RememberedPrice } from "@/lib/api";
import { confirmAnnexAPricesAction, undoMyPricesAction } from "@/app/reports/actions";
import { sumRows } from "@/lib/annexRows";
import type { CaseStore } from "@/lib/caseState";
import { priceTag, rememberedToastText, undoneText, type PriceFacts } from "@/lib/prices";

/** Stage 1 ("My prices" and price safety) on a case: the source tag beside
 * an Annex A price, the toast after a save remembered prices, and "Confirm
 * all prices", shared by the Annex A tab and the sign-off box. Nothing
 * shows for a line without a source (old cases, an older server). */

/** "Your price · 23 Nov", "Approved price", "Book · 6 cases · 8 mo", with
 * "old" after 12 months and "check" until the price is confirmed. The
 * tooltip says where it came from and, unless it is the book's, the book
 * price. Shown on the screen only, never printed. */
export function PriceSourceTag({ row }: { row: Partial<PriceFacts> }) {
  // The age depends on today: the server's render and the browser's may
  // differ around midnight, so the text may be put right on the client.
  const tag = priceTag(row);
  if (!tag) return null;
  return (
    <div className="s1-price-tags">
      <span className={`s1-price-tag s1-price-${tag.source}`} title={tag.title} suppressHydrationWarning>
        {tag.text}
      </span>
      {tag.old && (
        <span className="s1-price-old" title="This price is over 12 months old. It still applies; check it is still right.">
          old
        </span>
      )}
      {tag.check && (
        <span className="s1-price-check" title="Not confirmed yet: check it against the workshop's quote, or use Confirm all prices.">
          check
        </span>
      )}
    </div>
  );
}

/** How long the toast's Undo stays up. */
export const PRICE_TOAST_MS = 8000;

export type PriceToastState = { list: RememberedPrice[]; version: number };

/** "Saved as your price for Honda Vezel · Rear bumper fascia ($400).
 * [Undo]" -- or "Saved 3 prices as yours." -- after a save remembered the
 * prices the person typed. Undo puts each back (POST /me/prices/{id}/undo):
 * the price before, or none. Keyed by version, so each save starts fresh. */
export function RememberedPricesToast({ toast, onClose }: { toast: PriceToastState; onClose: () => void }) {
  const [phase, setPhase] = useState<"offer" | "busy" | "done">("offer");
  const [doneText, setDoneText] = useState("");

  useEffect(() => {
    if (phase === "busy") return;
    const t = window.setTimeout(onClose, phase === "done" ? 4000 : PRICE_TOAST_MS);
    return () => window.clearTimeout(t);
  }, [phase, onClose]);

  const undo = async () => {
    setPhase("busy");
    let failed = toast.list.length;
    try {
      failed = (await undoMyPricesAction(toast.list.map((r) => r.id))).failed;
    } catch {
      // The server action itself failed (connection lost): nothing undone.
    }
    setDoneText(undoneText(toast.list, failed));
    setPhase("done");
  };

  return (
    <div className="intake-toast s1-price-toast" role="status" aria-live="polite">
      <Tag style={{ width: 15, height: 15, flexShrink: 0, color: "var(--accent-primary)" }} aria-hidden="true" />
      <span>{phase === "done" ? doneText : rememberedToastText(toast.list)}</span>
      {phase !== "done" && (
        <button type="button" className="intake-toast-undo" onClick={() => void undo()} disabled={phase === "busy"}>
          <Undo2 style={{ width: 14, height: 14 }} /> {phase === "busy" ? "Undoing…" : "Undo"}
        </button>
      )}
      <button type="button" className="intake-icon-btn intake-toast-close" onClick={onClose} aria-label="Close">
        <X style={{ width: 13, height: 13 }} />
      </button>
      {phase === "offer" && (
        <span key={toast.version} className="intake-toast-timer" style={{ animationDuration: `${PRICE_TOAST_MS}ms` }} aria-hidden="true" />
      )}
    </div>
  );
}

/** "Confirm all prices" (POST /reports/{id}/annex-a/confirm-prices) on the
 * case's Annex A as saved: the lines that come back replace the table and
 * its baseline, and the case is fetched again. The caller makes sure there
 * are no unsaved Annex A changes first. */
export function useConfirmAllPrices(reportId: string, store: CaseStore) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  const confirmAll = async (): Promise<{ ok: true } | { error: string }> => {
    setBusy(true);
    store.setStatus("annexA", "saving");
    let res: Awaited<ReturnType<typeof confirmAnnexAPricesAction>>;
    try {
      res = await confirmAnnexAPricesAction(reportId);
    } catch {
      res = { error: "Couldn't reach the server. Check the connection and try again." };
    }
    setBusy(false);
    if ("error" in res) {
      store.setStatus("annexA", "error", res.error);
      return { error: res.error };
    }
    if (res.items) {
      const totals = sumRows(res.items);
      store.applySaved("annexA", {
        annex_a: { items: res.items, total_workshop_est: totals.est, total_adjusted_cost: totals.adj },
        ...(res.annex_c ? { annex_c: res.annex_c } : {}),
      });
    } else {
      store.setStatus("annexA", "saved");
    }
    router.refresh();
    return { ok: true };
  };

  return { busy, confirmAll };
}
