"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { ArrowRightLeft, Check, KeyRound, MoreHorizontal, Pencil, Plus, UserCheck, UserX, Users, X } from "lucide-react";
import { aiKeyWorking, type AccountLinkRow, type CaseCounts, type UserAccount, type UserPatch, type UserTicks } from "@/lib/api";
import { ROLE_LABEL, formatSgt, formatSgtDay } from "@/lib/signup";
import {
  createResetLinkAction,
  deleteUserAiKeyAction,
  disconnectUserTelegramAction,
  listAccountLinksAction,
  listUsersAction,
  setSignupsOpenAction,
  updateUserAction,
} from "./actions";
import { AccountLinksTab } from "./AccountLinksTab";
import { DisableUserDialog } from "./DisableUserDialog";
import { EditUserDialog, ticksOf, type EditUserDraft } from "./EditUserDialog";
import { CreateInviteDialog, LinkReadyDialog, type ReadyLink } from "./LinkDialogs";
import { MoveCasesDialog } from "./MoveCasesDialog";
import { telegramHandle, telegramLabel } from "./telegramWords";

type Tab = "users" | "links";

/** What a user's ticks allow, in a few words, under their role. */
function accessSummary(u: UserAccount): string {
  if (u.role === "admin") return "Everything";
  const t = ticksOf(u);
  const parts = [t.sees_all_cases ? "All cases" : "Own cases", t.ai_company_key_allowed ? "AI" : null, t.may_sign_off ? "Signs off" : null];
  return parts.filter(Boolean).join(" · ");
}

const NEW_FOR_DAYS = 7;

function isNew(u: UserAccount): boolean {
  return Date.now() - new Date(u.created_at).getTime() < NEW_FOR_DAYS * 24 * 3600 * 1000;
}

function statusOf(u: UserAccount): { label: string; tone: "ok" | "warn" | "off" } {
  if (!u.active) return { label: "Disabled", tone: "off" };
  if (u.locked) return { label: "Locked (wrong passwords)", tone: "warn" };
  if (u.must_change_password) return { label: "Temporary password", tone: "warn" };
  return { label: "Active", tone: "ok" };
}

/** The on/off switch for the public /signup page. Invite links work
 * either way. */
function SignupSwitch({ open, onChange }: { open: boolean; onChange: (open: boolean) => void }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const flip = async () => {
    const next = !open;
    if (!next && !confirm("Turn off new signups? Nobody new can create an account until you turn it back on. Invite links still work.")) return;
    setSaving(true);
    setError(null);
    const result = await setSignupsOpenAction(next);
    setSaving(false);
    if ("error" in result) setError(result.error);
    else onChange(result.open);
  };

  return (
    <div className={`signup-switch ${open ? "on" : "off"}`}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 800, fontSize: 13 }}>New signups: {open ? "on" : "off"}</div>
        <div className="setup-note" style={{ margin: 0 }}>
          {open
            ? "Anyone with the site address can create an account on the sign-in page. They join as a User."
            : "Nobody new can create an account. Invite links still work."}
        </div>
        {error && <div className="setup-error" style={{ margin: "8px 0 0" }}>{error}</div>}
      </div>
      <button type="button" className="btn-secondary-modern" style={{ fontSize: 12, padding: "6px 12px", flexShrink: 0 }} onClick={flip} disabled={saving}>
        {saving ? "Saving…" : open ? "Turn off" : "Turn on"}
      </button>
    </div>
  );
}

type MenuItem = { label: string; icon: React.ReactNode; onSelect: () => void; danger?: boolean };

/** Where the open menu sits, in window coordinates. */
type MenuSpot = { right: number; top?: number; bottom?: number };

/** How long after opening a scroll that moves the button counts as the
 * browser still bringing it into view (the menu follows) rather than the
 * person scrolling away (the menu closes). */
const SETTLE_MS = 200;

/** The "⋯" menu at the end of a user's row. Rendered into document.body:
 * inside the card it would be placed and clipped relative to the card,
 * whose backdrop-filter makes it the containing block of anything
 * position: fixed (and whose overflow is hidden). Closes on a click
 * elsewhere, Escape, resizing, and on scrolling that actually moves the
 * button -- not on the scroll event the browser fires just after the
 * opening click, nor on scrolling inside the menu. */
function RowMenu({ label, items, disabled }: { label: string; items: MenuItem[]; disabled?: boolean }) {
  const [at, setAt] = useState<MenuSpot | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
  const panel = useRef<HTMLDivElement | null>(null);
  /** The button's position when the menu was (last) placed, and when it opened. */
  const anchor = useRef<{ top: number; left: number; openedAt: number } | null>(null);

  const place = (): MenuSpot | null => {
    const b = button.current;
    if (!b) return null;
    const r = b.getBoundingClientRect();
    anchor.current = { top: r.top, left: r.left, openedAt: anchor.current?.openedAt ?? Date.now() };
    const right = Math.max(8, window.innerWidth - r.right);
    const roomBelow = window.innerHeight - r.bottom;
    const needed = 16 + items.length * 34;
    // Opens upwards near the bottom of the window, if there's more room there.
    return roomBelow < needed && r.top > roomBelow ? { right, bottom: window.innerHeight - r.top + 4 } : { right, top: r.bottom + 4 };
  };

  useEffect(() => {
    if (!at) return;
    const close = () => {
      anchor.current = null;
      setAt(null);
    };
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (panel.current?.contains(t) || button.current?.contains(t)) return;
      close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      close();
      button.current?.focus({ preventScroll: true });
    };
    const onScroll = (e: Event) => {
      if (e.target instanceof Node && panel.current?.contains(e.target)) return;
      const b = button.current;
      const a = anchor.current;
      if (!b || !a) return close();
      const r = b.getBoundingClientRect();
      if (Math.abs(r.top - a.top) < 1 && Math.abs(r.left - a.left) < 1) return;
      if (Date.now() - a.openedAt < SETTLE_MS) setAt(place());
      else close();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", onScroll, true);
    // Keyboard users land on the first item, without scrolling the page.
    panel.current?.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", onScroll, true);
    };
    // place() reads only refs and items: re-run only when the menu moves.
  }, [at]);

  const toggle = () => {
    if (at) {
      anchor.current = null;
      setAt(null);
      return;
    }
    anchor.current = null;
    setAt(place());
  };

  return (
    <>
      <button
        ref={button}
        type="button"
        className="btn-secondary-modern users-action users-row-menu-btn"
        aria-haspopup="menu"
        aria-expanded={Boolean(at)}
        aria-label={label}
        title={label}
        onClick={toggle}
        disabled={disabled}
      >
        <MoreHorizontal style={{ width: 14, height: 14 }} />
      </button>
      {at &&
        createPortal(
          <div ref={panel} className="users-row-menu" role="menu" aria-label={label} style={{ right: at.right, top: at.top, bottom: at.bottom }}>
            {items.map((item) => (
              <button
                key={item.label}
                type="button"
                role="menuitem"
                className={`user-menu-item ${item.danger ? "danger" : ""}`}
                onClick={() => {
                  anchor.current = null;
                  setAt(null);
                  item.onSelect();
                }}
              >
                {item.icon} {item.label}
              </button>
            ))}
          </div>,
          document.body
        )}
    </>
  );
}

/** "28 Oct, 17:30" in Singapore time (the full date and year are in the
 * tooltip). Fixed to SGT so the server and the browser print the same. */
function sgtShort(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-SG", {
    timeZone: "Asia/Singapore",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
}

/** "4 open" over "11 signed off · 1 draft": two short lines, so the column
 * stays narrow. */
function CaseCountsCell({ cases }: { cases: CaseCounts | null | undefined }) {
  if (!cases) return <span style={{ color: "var(--text-muted)" }}>—</span>;
  return (
    <>
      <div className={`users-cases-open ${cases.open ? "" : "zero"}`}>{cases.open} open</div>
      <div className="users-cell-sub">
        {cases.signed_off} signed off · {cases.drafts} {cases.drafts === 1 ? "draft" : "drafts"}
      </div>
    </>
  );
}

/** The AI key column (agent5 §9): "Own ..1234 OK" / "Own ..1234 Failing" /
 * "Company (allowed)" / "None - AI off", on two short lines like the other
 * cells. Company: admins, and accounts ticked "May use the company AI key".
 * Only the last 4 characters of a key ever reach this screen. */
function AiKeyCell({ u }: { u: UserAccount }) {
  const key = u.ai_key;
  const label = <span className="users-ai-label">AI key </span>;
  if (key?.has_key) {
    const ok = aiKeyWorking(key.status);
    const state = ok ? "OK" : "Failing";
    return (
      <div title={`Own key ..${key.last4}: ${ok ? (key.status === "untested" ? "OK, not checked with Google yet" : "OK") : "failing"}`}>
        <div className="users-ai-main">
          {label}Own ..{key.last4}
        </div>
        <div className={`users-ai-state ${ok ? "ok" : "fail"}`}>
          {state}
          {key.status === "untested" && <span className="users-ai-untested"> · not checked</span>}
        </div>
      </div>
    );
  }
  if (u.role === "admin" || u.ai_company_key_allowed) {
    return (
      <div title="No key of their own; may use the company AI key">
        <div className="users-ai-main">{label}Company</div>
        <div className="users-cell-sub">(allowed)</div>
      </div>
    );
  }
  return (
    <div title="No key of their own, and not allowed the company AI key">
      <div className="users-ai-main none">{label}None</div>
      <div className="users-cell-sub">AI off</div>
    </div>
  );
}

/** The Telegram column (release P5): "@ahhuat_sg" over "since 12 Nov", or
 * "–" when none is connected. A long username is cut short here; the
 * tooltip has all of it. */
function TelegramCell({ u }: { u: UserAccount }) {
  const t = u.telegram ?? null;
  const label = <span className="users-tg-label">Telegram </span>;
  if (!t) {
    return (
      <div className="users-tg-main none" title="No Telegram connected">
        {label}–
      </div>
    );
  }
  const handle = telegramHandle(t) || "Connected";
  const since = t.linked_at ? formatSgtDay(t.linked_at) : "";
  return (
    <div title={`Telegram ${telegramLabel(t) || "connected"}${t.linked_at ? `, connected ${formatSgt(t.linked_at)}` : ""}`}>
      <div className="users-tg-main">
        {label}
        {handle}
      </div>
      {since && <div className="users-cell-sub">since {since}</div>}
    </div>
  );
}

function UsersTable({
  users,
  currentUserId,
  busyId,
  onEdit,
  onResetPassword,
  onMoveCases,
  onDisable,
  onEnable,
}: {
  users: UserAccount[];
  currentUserId: string;
  busyId: string | null;
  onEdit: (u: UserAccount) => void;
  onResetPassword: (u: UserAccount) => void;
  onMoveCases: (u: UserAccount) => void;
  onDisable: (u: UserAccount) => void;
  onEnable: (u: UserAccount) => void;
}) {
  const icon = (Icon: typeof Pencil) => <Icon style={{ width: 14, height: 14 }} />;
  return (
    <>
      {/* Sized to fit the Settings column with no sideways scrolling; when
          the card itself is narrow, each person becomes a small card
          (globals.css, P2 block: @container users-table). */}
      <div className="users-table-wrap">
        <table className="users-table users-people-table">
          <thead>
            <tr>
              <th className="col-person">User</th>
              <th className="col-role">Role</th>
              <th className="col-cases">Cases</th>
              <th className="col-ai">AI key</th>
              <th className="col-tg">Telegram</th>
              <th className="col-seen">Last sign-in</th>
              <th className="col-status">Status</th>
              <th className="col-actions" aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const status = statusOf(u);
              const isSelf = u.id === currentUserId;
              const busy = busyId === u.id;
              // Your own row: no reset link (use Password on My settings)
              // and no disabling yourself (the API refuses both).
              const items: MenuItem[] = [
                { label: "Edit", icon: icon(Pencil), onSelect: () => onEdit(u) },
                ...(isSelf ? [] : [{ label: "Reset password link", icon: icon(KeyRound), onSelect: () => onResetPassword(u) }]),
                { label: "Move their cases", icon: icon(ArrowRightLeft), onSelect: () => onMoveCases(u) },
                ...(isSelf
                  ? []
                  : u.active
                    ? [{ label: "Disable", icon: icon(UserX), onSelect: () => onDisable(u), danger: true }]
                    : [{ label: "Enable", icon: icon(UserCheck), onSelect: () => onEnable(u) }]),
              ];
              return (
                <tr key={u.id} style={{ opacity: u.active ? 1 : 0.6 }}>
                  <td className="col-person">
                    <div className="users-person-name">
                      {u.username || u.name}
                      {isSelf && <span className="settings-readonly-tag" style={{ marginLeft: 6 }}>You</span>}
                      {!isSelf && isNew(u) && <span className="user-new-tag">New</span>}
                    </div>
                    {u.name && u.name !== u.username && <div className="users-person-sub">{u.name}</div>}
                    <div className="users-person-email">{u.email}</div>
                    {/* Only while the Telegram column is hidden for room
                        (globals.css, P5 block). */}
                    {u.telegram && <div className="users-person-tg">Telegram {telegramHandle(u.telegram) || "connected"}</div>}
                  </td>
                  <td className="col-role">
                    <span className={`role-chip ${u.role}`}>{ROLE_LABEL[u.role] ?? u.role}</span>
                    <div className="user-access-summary">{accessSummary(u)}</div>
                  </td>
                  <td className="col-cases">
                    <CaseCountsCell cases={u.cases} />
                  </td>
                  <td className="col-ai">
                    <AiKeyCell u={u} />
                  </td>
                  <td className="col-tg">
                    <TelegramCell u={u} />
                  </td>
                  <td className="col-seen">
                    <div title={u.last_login_at ? formatSgt(u.last_login_at) : undefined}>
                      <span className="users-seen-label">Last sign-in </span>
                      {u.last_login_at ? sgtShort(u.last_login_at) : "Never"}
                    </div>
                    <div className="users-cell-sub">Joined {formatSgtDay(u.created_at)}</div>
                  </td>
                  <td className="col-status">
                    <span className={`user-status ${status.tone}`}>{status.label}</span>
                  </td>
                  <td className="col-actions">
                    <RowMenu label={`Actions for ${u.username || u.name}`} items={items} disabled={busy} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="setup-note" style={{ marginTop: 12, marginBottom: 0 }}>
        Everything for one person is in the ⋯ menu on their row. To make someone an admin, or change what they may do,
        use Edit. Forgot password? Use Reset password link and send them the link. Users are disabled rather than
        deleted, so the history on their cases still shows who did what. Disable asks what happens to their open cases, and
        deletes their AI key and disconnects their Telegram; Move their cases hands them on at any time. Nobody sees
        anyone&apos;s AI key, admins included: only its last 4 characters and whether it works.
      </p>
    </>
  );
}

/** Users, the signup switch, and the one-time invite and password reset
 * links an admin sends. */
export function UsersCard({
  initialUsers,
  initialLinks,
  initialSignupsOpen,
  currentUserId,
}: {
  initialUsers: UserAccount[];
  /** Null when they couldn't be loaded. */
  initialLinks: AccountLinkRow[] | null;
  initialSignupsOpen: boolean;
  currentUserId: string;
}) {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("users");
  const [users, setUsers] = useState(initialUsers);
  const [links, setLinks] = useState(initialLinks);
  const [signupsOpen, setSignupsOpen] = useState(initialSignupsOpen);
  const [editing, setEditing] = useState<UserAccount | null>(null);
  const [disabling, setDisabling] = useState<UserAccount | null>(null);
  const [moving, setMoving] = useState<UserAccount | null>(null);
  const [inviting, setInviting] = useState(false);
  const [ready, setReady] = useState<ReadyLink | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** What the last disable or move did, in words. */
  const [notice, setNotice] = useState<string | null>(null);

  /** Reads the table again; the new rows, or null if that failed. */
  const refresh = async (): Promise<UserAccount[] | null> => {
    const result = await listUsersAction();
    if (!("users" in result)) return null;
    setUsers(result.users);
    return result.users;
  };

  const refreshLinks = async () => {
    const result = await listAccountLinksAction();
    setLinks("links" in result ? result.links : null);
  };

  /** A new link to show once. It also appears in Invites & links. */
  const issued = (link: ReadyLink) => {
    setReady(link);
    refreshLinks();
  };

  /** Who cases can go to: active accounts other than u. */
  const othersThan = (u: UserAccount) => users.filter((p) => p.active && p.id !== u.id);

  const saveUser = async (draft: EditUserDraft): Promise<string | null> => {
    if (!editing) return null;
    // Ticks only when changed, so the account's history records just the
    // changes.
    const before = ticksOf(editing);
    const { sees_all_cases, ai_company_key_allowed, may_sign_off, role, ...profile } = draft;
    const after: UserTicks = { sees_all_cases, ai_company_key_allowed, may_sign_off };
    const changedTicks: Partial<UserTicks> = {};
    for (const k of Object.keys(after) as (keyof UserTicks)[]) {
      if (after[k] !== before[k]) changedTicks[k] = after[k];
    }
    const patch: UserPatch = { ...profile, ...changedTicks };
    // The role only when it changed, and never on your own account: the API
    // refuses that (403 "You can't change your own role.").
    if (role !== editing.role && editing.id !== currentUserId) patch.role = role;
    const result = await updateUserAction(editing.id, patch);
    if ("error" in result) return result.error;
    // Your own name is in the top bar.
    if (editing.id === currentUserId) router.refresh();
    setEditing(null);
    await refresh();
    return null;
  };

  /** "Remove their key" in the Edit dialog: at once (the dialog asked
   * first), then the table and the open dialog show it gone. */
  const removeAiKey = async (u: UserAccount): Promise<string | null> => {
    const result = await deleteUserAiKeyAction(u.id);
    if ("error" in result) return result.error;
    const fresh = await refresh();
    const row = fresh?.find((x) => x.id === u.id) ?? { ...u, ai_key: null };
    setUsers((all) => all.map((x) => (x.id === u.id ? row : x)));
    setEditing((open) => (open && open.id === u.id ? row : open));
    return null;
  };

  /** [Disconnect] in the Edit dialog's Telegram row: at once (the dialog
   * asked first), then the table and the open dialog show it gone. */
  const disconnectTelegram = async (u: UserAccount): Promise<string | null> => {
    const result = await disconnectUserTelegramAction(u.id);
    if ("error" in result) return result.error;
    const fresh = await refresh();
    const row = fresh?.find((x) => x.id === u.id) ?? { ...u, telegram: null };
    setUsers((all) => all.map((x) => (x.id === u.id ? row : x)));
    setEditing((open) => (open && open.id === u.id ? row : open));
    return null;
  };

  const resetPassword = async (u: UserAccount) => {
    const who = u.username || u.name;
    if (!confirm(`Create a password reset link for ${who}? You send it to them, and they choose a new password with it.`)) return;
    setBusyId(u.id);
    setError(null);
    setNotice(null);
    const result = await createResetLinkAction(u.id);
    setBusyId(null);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    issued({
      purpose: "reset",
      token: result.token,
      name: u.name,
      email: u.email,
      expiresAt: result.link.expires_at,
      phone: null,
    });
    await refresh();
  };

  const enable = async (u: UserAccount) => {
    setBusyId(u.id);
    setError(null);
    setNotice(null);
    const result = await updateUserAction(u.id, { active: true });
    setBusyId(null);
    if ("error" in result) setError(result.error);
    else await refresh();
  };

  /** After the Disable or Move dialog: say what happened, show the new counts. */
  const finished = async (message: string) => {
    setDisabling(null);
    setMoving(null);
    setError(null);
    setNotice(message);
    await refresh();
  };

  const opening = (open: (u: UserAccount) => void) => (u: UserAccount) => {
    setNotice(null);
    setError(null);
    open(u);
  };

  const activeCount = users.filter((u) => u.active).length;
  const tabs: { id: Tab; label: string }[] = [
    { id: "users", label: `Users (${users.length})` },
    { id: "links", label: "Invites & links" },
  ];

  return (
    <div className="card-glass setup-card">
      <div className="setup-card-head">
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12, minWidth: 0 }}>
          <div className="setup-card-icon" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Users style={{ width: 22, height: 22, color: "var(--accent-primary)" }} />
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="card-title" style={{ marginBottom: 2 }}>Users</div>
            <div className="card-subtitle" style={{ marginBottom: 0 }}>Who can sign in, and what they can do</div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
          <span className="setup-status">
            <span style={{ color: "var(--badge-green-text)" }}>●</span> {activeCount} active
          </span>
          <button
            type="button"
            className="btn-primary-modern"
            style={{ fontSize: 12, padding: "6px 12px", display: "inline-flex", alignItems: "center", gap: 4 }}
            onClick={() => setInviting(true)}
            title="A one-time link for one email -- works even while signups are off"
          >
            <Plus style={{ width: 13, height: 13 }} /> Create invite link
          </button>
        </div>
      </div>

      <SignupSwitch open={signupsOpen} onChange={setSignupsOpen} />

      <div className="case-tab-bar" role="tablist" aria-label="Users" style={{ marginBottom: 16 }}>
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={`case-tab ${tab === t.id ? "active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "links" && (
        <AccountLinksTab
          links={links}
          onRetry={refreshLinks}
          onChanged={(changed) => setLinks((all) => all?.map((l) => (l.id === changed.id ? changed : l)) ?? all)}
        />
      )}

      {tab === "users" && (
        <>
          {error && <div className="setup-error">{error}</div>}
          {notice && (
            <div className="settings-notice" role="status">
              <Check style={{ width: 14, height: 14, flexShrink: 0, marginTop: 1 }} />
              <span style={{ flex: 1, minWidth: 0 }}>{notice}</span>
              <button type="button" className="settings-notice-close" aria-label="Dismiss" onClick={() => setNotice(null)}>
                <X style={{ width: 13, height: 13 }} />
              </button>
            </div>
          )}
          <UsersTable
            users={users}
            currentUserId={currentUserId}
            busyId={busyId}
            onEdit={opening(setEditing)}
            onResetPassword={resetPassword}
            onMoveCases={opening(setMoving)}
            onDisable={opening(setDisabling)}
            onEnable={enable}
          />
        </>
      )}

      {editing && (
        <EditUserDialog
          user={editing}
          isSelf={editing.id === currentUserId}
          onCancel={() => setEditing(null)}
          onSubmit={saveUser}
          onRemoveAiKey={() => removeAiKey(editing)}
          onDisconnectTelegram={() => disconnectTelegram(editing)}
        />
      )}

      {disabling && (
        <DisableUserDialog user={disabling} people={othersThan(disabling)} onCancel={() => setDisabling(null)} onDone={finished} />
      )}

      {moving && <MoveCasesDialog user={moving} people={othersThan(moving)} onCancel={() => setMoving(null)} onDone={finished} />}

      {inviting && (
        <CreateInviteDialog
          onCancel={() => setInviting(false)}
          onCreated={(link) => {
            setInviting(false);
            issued(link);
          }}
        />
      )}

      {ready && <LinkReadyDialog link={ready} onClose={() => setReady(null)} />}
    </div>
  );
}
