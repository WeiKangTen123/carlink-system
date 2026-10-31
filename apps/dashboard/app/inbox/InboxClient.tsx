"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertTriangle, Check, Inbox, Search, ShieldCheck, Trash2, X } from "lucide-react";
import { ownerLabel, publicRoleLabel, type InboxItem, type InboxList, type InboxView } from "@/lib/api";
import { announceInboxChanged, expiryInfo, shortSgt, timeAgo } from "@/lib/inbox";
import { discardSubmissionsAction } from "./actions";

const TABS: { view: InboxView; label: string; title: string }[] = [
  { view: "new", label: "New", title: "Reports nobody has taken yet" },
  { view: "taken", label: "Taken, not filed", title: "Reports someone has taken but not filed as a case yet" },
];

/** What the search looks through: the reference, what the sender gave, the
 * masked phone, the plate and the start of their statement. */
function searchText(item: InboxItem): string {
  const s = item.sender;
  return [
    item.reference,
    s?.name,
    publicRoleLabel(s),
    s?.phone_masked,
    s?.telegram_username ? `@${s.telegram_username}` : "",
    item.plate,
    item.statement_preview,
    item.owner ? `${item.owner.name} ${item.owner.username}` : "",
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

/** Oldest first: the ones closest to being deleted at the top. */
function bySubmitted(a: InboxItem, b: InboxItem): number {
  return new Date(a.submitted_at).getTime() - new Date(b.submitted_at).getTime();
}

export function InboxClient({
  view,
  list,
  error,
  discarded,
  renderedAt,
}: {
  view: InboxView;
  list: InboxList | null;
  error: string | null;
  /** The reference just discarded from its own page, to say so here. */
  discarded: string | null;
  /** The server's clock when it drew the page, so the first render here
   * matches the server's; the countdowns then follow the browser's. */
  renderedAt: number;
}) {
  const router = useRouter();
  const [now, setNow] = useState(renderedAt);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  const [confirmSpam, setConfirmSpam] = useState(false);
  const [blockSenders, setBlockSenders] = useState(true);
  const [discarding, setDiscarding] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "warn"; text: string } | null>(
    discarded ? { tone: "ok", text: `${discarded} was discarded.` } : null
  );

  // The countdowns move on by the minute.
  useEffect(() => {
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, []);

  // A "?discarded=" notice is shown once; take it out of the address so a
  // reload doesn't show it again.
  useEffect(() => {
    if (discarded) window.history.replaceState(null, "", `/inbox${view === "taken" ? "?view=taken" : ""}`);
  }, [discarded, view]);

  const items = useMemo(() => [...(list?.items ?? [])].sort(bySubmitted), [list]);

  // Drop selected rows that are no longer listed (taken, discarded elsewhere).
  useEffect(() => {
    setSelected((prev) => {
      const ids = new Set(items.map((i) => i.id));
      const kept = [...prev].filter((id) => ids.has(id));
      return kept.length === prev.size ? prev : new Set(kept);
    });
  }, [items]);

  const needle = query.trim().toLowerCase();
  const shown = useMemo(() => (needle ? items.filter((i) => searchText(i).includes(needle)) : items), [items, needle]);

  const selectable = view === "new";
  const allShownSelected = shown.length > 0 && shown.every((i) => selected.has(i.id));

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAllShown() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allShownSelected) shown.forEach((i) => next.delete(i.id));
      else shown.forEach((i) => next.add(i.id));
      return next;
    });
  }

  async function discardSelectedAsSpam() {
    const ids = items.filter((i) => selected.has(i.id)).map((i) => i.id);
    if (!ids.length) return;
    setDiscarding(true);
    const res = await discardSubmissionsAction(ids, { reason: "spam", block_sender: blockSenders });
    setDiscarding(false);
    setConfirmSpam(false);
    setSelected(new Set());
    const refs = new Map(items.map((i) => [i.id, i.reference]));
    if (res.failed.length) {
      const first = res.failed[0];
      setNotice({
        tone: "warn",
        text:
          `Discarded ${res.discarded.length} of ${ids.length}. ` +
          `${res.failed.length === 1 ? refs.get(first.id) ?? "One" : `${res.failed.length} reports`} couldn't be: ${first.error}`,
      });
    } else {
      setNotice({ tone: "ok", text: `Discarded ${res.discarded.length} report${res.discarded.length === 1 ? "" : "s"} as spam.` });
    }
    if (res.discarded.length) announceInboxChanged();
    router.refresh();
  }

  const counts = list?.counts ?? { new: 0, taken: 0 };
  const limits = list?.limits;
  const selectedCount = items.filter((i) => selected.has(i.id)).length;

  return (
    <div className="inbox-page">
      <div className="inbox-head">
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", minWidth: 0 }}>
          <h1 className="inbox-title">
            <Inbox style={{ width: 18, height: 18, color: "var(--accent-primary)" }} /> Public Inbox
          </h1>
          <div className="view-mode-toggle case-scope-switch" style={{ padding: 2 }} role="tablist" aria-label="Which reports">
            {TABS.map((t) => (
              <Link
                key={t.view}
                href={t.view === "new" ? "/inbox" : "/inbox?view=taken"}
                prefetch={false}
                role="tab"
                aria-selected={view === t.view}
                className={`view-mode-btn ${view === t.view ? "active" : ""}`}
                style={{ padding: "4px 10px", fontSize: 12 }}
                title={t.title}
              >
                {t.label} ({t.view === "new" ? counts.new : counts.taken})
              </Link>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <div style={{ position: "relative" }}>
            <Search className="inbox-search-icon" />
            <input
              type="search"
              className="settings-input inbox-search"
              placeholder="Search ref, name, plate…"
              aria-label="Search the inbox"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          {selectable && (
            <button
              type="button"
              className="btn-secondary-modern inbox-danger-outline"
              disabled={selectedCount === 0 || discarding}
              onClick={() => setConfirmSpam(true)}
              title={selectedCount === 0 ? "Tick the reports to discard first" : undefined}
            >
              <Trash2 style={{ width: 13, height: 13 }} /> Discard selected as spam{selectedCount ? ` (${selectedCount})` : ""}
            </button>
          )}
        </div>
      </div>

      <p className="setup-note" style={{ margin: 0 }}>
        Sent by the public through the Telegram bot. Reports nobody takes are deleted after 30 days. What senders give is
        their own words and hasn&apos;t been checked; the phone number was shared from their own Telegram account (✓).
      </p>

      {limits && !limits.accepting && (
        <div className="inbox-banner warn" role="status">
          <AlertTriangle style={{ width: 14, height: 14, flexShrink: 0 }} />
          <span>
            The inbox is full{limits.max_open ? ` (${limits.max_open} reports nobody has taken)` : ""}, so the bot is turning
            new reports away. Take or discard some to open it again.
          </span>
        </div>
      )}

      {notice && (
        <div className={`inbox-banner ${notice.tone === "ok" ? "ok" : "warn"}`} role="status">
          {notice.tone === "ok" ? <Check style={{ width: 14, height: 14, flexShrink: 0 }} /> : <AlertTriangle style={{ width: 14, height: 14, flexShrink: 0 }} />}
          <span style={{ flex: 1 }}>{notice.text}</span>
          <button type="button" className="intake-icon-btn" onClick={() => setNotice(null)} aria-label="Dismiss">
            <X style={{ width: 13, height: 13 }} />
          </button>
        </div>
      )}

      {error ? (
        <div className="intake-alert danger" role="alert">
          <AlertTriangle style={{ width: 16, height: 16, flexShrink: 0 }} />
          <div style={{ flex: 1 }}>Couldn&apos;t load the inbox: {error}</div>
          <button type="button" className="btn-secondary-modern" onClick={() => router.refresh()}>
            Try again
          </button>
        </div>
      ) : (
        <div className="card-glass triage-table-card" style={{ padding: 0 }}>
          <div className="triage-table-wrapper inbox-table-wrapper">
            <table className="triage-table inbox-table">
              <thead>
                <tr>
                  {selectable && (
                    <th className="inbox-col-pick">
                      <input
                        type="checkbox"
                        aria-label="Select every report shown"
                        checked={allShownSelected}
                        disabled={shown.length === 0}
                        onChange={toggleAllShown}
                      />
                    </th>
                  )}
                  <th>Ref</th>
                  <th>Sender (as given)</th>
                  <th>Phone</th>
                  <th>Plate</th>
                  <th className="col-center">Photos</th>
                  <th>Sent</th>
                  {view === "new" ? <th>Left</th> : <th>Taken by</th>}
                </tr>
              </thead>
              <tbody>
                {shown.map((item) => {
                  const left = expiryInfo(item.expires_at, now);
                  const s = item.sender;
                  return (
                    <tr
                      key={item.id}
                      className={`inbox-row ${selected.has(item.id) ? "picked" : ""}`}
                      onClick={(e) => {
                        // Not when the click was on a link, button or tick box.
                        if ((e.target as HTMLElement).closest("a, button, input, label")) return;
                        router.push(`/inbox/${item.id}`);
                      }}
                    >
                      {selectable && (
                        <td className="inbox-col-pick">
                          <input
                            type="checkbox"
                            aria-label={`Select ${item.reference}`}
                            checked={selected.has(item.id)}
                            onChange={() => toggle(item.id)}
                          />
                        </td>
                      )}
                      <td>
                        <Link href={`/inbox/${item.id}`} prefetch={false} className="inbox-ref">
                          {item.reference}
                        </Link>
                        {item.withdraw_requested && <span className="intake-chip warn inbox-row-chip">Withdrawal requested</span>}
                      </td>
                      <td className="inbox-sender-cell">
                        <span className="inbox-as-given inbox-sender-name">{s?.name || "No name given"}</span>
                        <span className="inbox-sub">
                          {publicRoleLabel(s)}
                          {s?.telegram_username ? ` · @${s.telegram_username}` : ""}
                        </span>
                      </td>
                      <td className="inbox-mono" style={{ whiteSpace: "nowrap" }}>
                        {s?.phone_masked || "—"}{" "}
                        {s?.phone_masked && (
                          <ShieldCheck
                            style={{ width: 12, height: 12, color: "var(--badge-green-text)", verticalAlign: "-2px" }}
                            aria-label="Shared from their own Telegram account"
                          />
                        )}
                      </td>
                      <td className="inbox-mono inbox-as-given">{item.plate || "—"}</td>
                      <td className="col-center inbox-mono">{item.photo_count}</td>
                      <td style={{ whiteSpace: "nowrap" }} suppressHydrationWarning>
                        {shortSgt(item.submitted_at)}
                        <span className="inbox-sub">{timeAgo(item.submitted_at, now)}</span>
                      </td>
                      {view === "new" ? (
                        <td style={{ whiteSpace: "nowrap" }}>
                          {left ? (
                            <span className={`inbox-left ${left.tone}`} title={left.long}>
                              {left.tone !== "ok" && <AlertTriangle style={{ width: 11, height: 11 }} />}
                              {left.short}
                            </span>
                          ) : (
                            "—"
                          )}
                        </td>
                      ) : (
                        <td>
                          <span className="inbox-taken-by">{ownerLabel(item.owner)}</span>
                          {item.taken_at && <span className="inbox-sub">{timeAgo(item.taken_at, now)}</span>}
                        </td>
                      )}
                    </tr>
                  );
                })}
                {shown.length === 0 && (
                  <tr>
                    <td colSpan={selectable ? 8 : 7} className="inbox-empty">
                      {needle
                        ? `Nothing matches “${query.trim()}”.`
                        : view === "new"
                          ? "Nothing new. Reports the public sends through the Telegram bot appear here."
                          : "No taken reports waiting to be filed."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {confirmSpam && (
        <div className="modal-backdrop" onClick={() => !discarding && setConfirmSpam(false)}>
          <div
            className="modal-card"
            role="dialog"
            aria-modal="true"
            aria-labelledby="spam-title"
            style={{ maxWidth: 460 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="spam-title" style={{ fontSize: 16, fontWeight: 800, margin: "0 0 4px" }}>
              Discard {selectedCount} report{selectedCount === 1 ? "" : "s"} as spam?
            </h3>
            <p className="setup-note" style={{ margin: "0 0 12px" }}>
              This deletes {selectedCount === 1 ? "it" : "them"}, the photos and the senders&apos; details for good. The case
              history keeps only each reference and &quot;Spam&quot;.
            </p>
            <label className="user-tick">
              <input type="checkbox" checked={blockSenders} disabled={discarding} onChange={(e) => setBlockSenders(e.target.checked)} />
              <span>
                Block {selectedCount === 1 ? "this sender" : "these senders"} for 90 days
                <span className="field-hint">The bot won&apos;t take reports from their Telegram accounts until then.</span>
              </span>
            </label>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 16 }}>
              <button type="button" className="btn-secondary-modern" onClick={() => setConfirmSpam(false)} disabled={discarding}>
                Cancel
              </button>
              <button type="button" className="btn-primary-modern inbox-danger-btn" onClick={() => void discardSelectedAsSpam()} disabled={discarding}>
                <Trash2 style={{ width: 14, height: 14 }} /> {discarding ? "Discarding…" : "Discard as spam"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
