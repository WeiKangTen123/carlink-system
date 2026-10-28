"use client";

import { useEffect, useMemo, useState } from "react";
import { UserX } from "lucide-react";
import { personLabel, type OpenCasesChoice, type UserAccount, type UserPatch } from "@/lib/api";
import { listUsersAction, updateUserAction } from "./actions";

/** "Tan Ah Huat", or "@ahhuat" when the name is just the username. */
export function shortName(u: Pick<UserAccount, "name" | "username">): string {
  return u.name && u.name !== u.username ? u.name : `@${u.username}`;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Close on Escape, unless something is being saved. */
export function useEscape(onClose: () => void, busy: boolean) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);
}

/** The person's case counts and who their cases could go to, read again
 * (GET /users) when a dialog opens: the table's counts are from when the
 * page loaded, and cases may have been filed or moved since. Until that
 * answers, `checking` is true; if it fails, the table's counts are used
 * and `stale` says so. Read once per dialog (`user` is the row it was
 * opened from). */
export function useFreshCounts(user: UserAccount, initialPeople: UserAccount[]) {
  const [state, setState] = useState({ user, people: initialPeople, checking: true, stale: false });
  const userId = user.id;
  useEffect(() => {
    let cancelled = false;
    listUsersAction().then((res) => {
      if (cancelled) return;
      if ("error" in res) {
        setState((s) => ({ ...s, checking: false, stale: true }));
        return;
      }
      setState((s) => ({
        user: res.users.find((u) => u.id === userId) ?? s.user,
        people: res.users.filter((p) => p.active && p.id !== userId),
        checking: false,
        stale: false,
      }));
    });
    return () => {
      cancelled = true;
    };
  }, [userId]);
  return state;
}

/** A picker of the accounts cases can go to: active ones only (the API
 * refuses a disabled one, owner_inactive), each with how many open cases
 * they already have. */
export function PersonSelect({
  people,
  value,
  onChange,
  label,
}: {
  people: UserAccount[];
  value: string;
  onChange: (id: string) => void;
  label: string;
}) {
  const sorted = useMemo(
    () => [...people].sort((a, b) => (a.name || a.username).localeCompare(b.name || b.username)),
    [people]
  );
  return (
    <select className="settings-input settings-person-select" aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{sorted.length ? "Choose someone…" : "No one else is active"}</option>
      {sorted.map((p) => (
        <option key={p.id} value={p.id}>
          {personLabel(p)} · {p.cases?.open ?? 0} open
        </option>
      ))}
    </select>
  );
}

/** Settings -> Users -> Disable. One PATCH does all of it or none of it:
 * the account is disabled, its sessions end, and its open cases (not signed
 * off, not an unfinished draft) are moved, unassigned or kept as chosen.
 * The counts are read again when the dialog opens (useFreshCounts).
 *
 * "Also" says their AI key is deleted, when they have one (the same PATCH
 * deletes it). SLOT (P5): "Telegram @x is disconnected. Enabling again
 * won't reconnect." goes in the same list. */
export function DisableUserDialog({
  user: rowUser,
  people: rowPeople,
  onCancel,
  onDone,
}: {
  user: UserAccount;
  /** Everyone their cases could go to (active, and not them), as the table
   * had it; read again on open. */
  people: UserAccount[];
  onCancel: () => void;
  onDone: (notice: string) => void;
}) {
  const { user, people, checking, stale } = useFreshCounts(rowUser, rowPeople);
  const open = user.cases?.open ?? 0;
  const signedOff = user.cases?.signed_off ?? 0;
  const drafts = user.cases?.drafts ?? 0;
  const who = shortName(user);

  // Null: nothing picked. With open cases it starts on "unassign" (agent5
  // §9). With none, nothing is picked unless the admin picks something;
  // with nothing sent the API keeps whatever turns up meanwhile under them.
  const [choice, setChoice] = useState<OpenCasesChoice | null>(null);
  const [target, setTarget] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEscape(onCancel, saving);

  useEffect(() => {
    if (!checking && open > 0) setChoice((c) => c ?? "unassign");
  }, [checking, open]);

  const targetPerson = people.find((p) => p.id === target) ?? null;
  // The API deletes it in the same change (P3; agent6 D6).
  const hadAiKey = Boolean(user.ai_key?.has_key);
  const ready = !checking && (choice !== "move" || Boolean(targetPerson));

  const submit = async () => {
    setSaving(true);
    setError(null);
    const patch: UserPatch = { active: false };
    if (choice) {
      patch.open_cases = choice;
      if (choice === "move") patch.open_cases_to = target;
    }
    const result = await updateUserAction(user.id, patch);
    setSaving(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    // How many the API actually moved, when it says; plain words otherwise.
    const moved = typeof result.user.moved === "number" ? result.user.moved : null;
    let cases = "";
    if (choice === "move" && targetPerson) {
      cases =
        moved === null
          ? ` Their open cases went to ${personLabel(targetPerson)}.`
          : moved === 0
            ? " They had no open cases to move."
            : ` ${plural(moved, "open case")} went to ${personLabel(targetPerson)}.`;
    } else if (choice === "unassign") {
      cases =
        moved === null
          ? " Their open cases are now Unassigned."
          : moved === 0
            ? " They had no open cases to unassign."
            : ` ${plural(moved, "open case")} ${moved === 1 ? "is" : "are"} now Unassigned.`;
    } else if (choice === "keep") {
      cases = " Their open cases stay under them.";
    }
    onDone(`${personLabel(user)} is disabled.${cases}${hadAiKey ? " Their AI key was deleted." : ""}`);
  };

  const stays = [
    signedOff ? `${plural(signedOff, "signed-off case")} stay${signedOff === 1 ? "s" : ""} under ${who}.` : null,
    drafts
      ? `${plural(drafts, "unfinished draft")} stay${drafts === 1 ? "s" : ""} and ${drafts === 1 ? "is" : "are"} cleared after 7 idle days.`
      : null,
  ].filter(Boolean);

  return (
    <div className="modal-backdrop" onClick={() => !saving && onCancel()}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="disable-user-title"
        style={{ maxWidth: 560 }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="disable-user-title" style={{ fontSize: 16, fontWeight: 800, margin: "0 0 4px" }}>
          Disable {personLabel(user)}?
        </h3>
        <p className="setup-note" style={{ margin: "0 0 4px" }}>
          They are signed out at once and can&apos;t sign in until you enable them.
        </p>

        <div className="settings-dialog-section">
          {checking ? (
            <>
              <div className="settings-dialog-label">Their cases</div>
              <p className="setup-note" style={{ margin: 0 }} role="status">
                Checking their cases…
              </p>
            </>
          ) : (
            <>
              <div className="settings-dialog-label">{open > 0 ? `Their ${plural(open, "open case")}` : "Their open cases"}</div>
              {open === 0 && (
                <p className="setup-note" style={{ margin: "0 0 8px" }}>
                  They have none right now. If you pick something, it also covers any filed before you confirm.
                </p>
              )}
              <div className="settings-choices" role="radiogroup" aria-label="What happens to their open cases">
                <label className={`settings-choice ${choice === "move" ? "picked" : ""}`}>
                  <input type="radio" name="open-cases" checked={choice === "move"} onChange={() => setChoice("move")} />
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span className="settings-choice-title">Move them to</span>
                    <PersonSelect
                      people={people}
                      value={target}
                      label="Move their open cases to"
                      onChange={(id) => {
                        setTarget(id);
                        if (id) setChoice("move");
                      }}
                    />
                  </span>
                </label>
                <label className={`settings-choice ${choice === "unassign" ? "picked" : ""}`}>
                  <input type="radio" name="open-cases" checked={choice === "unassign"} onChange={() => setChoice("unassign")} />
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span className="settings-choice-title">Leave them unassigned</span>
                    <span className="settings-choice-sub">Only admins and staff who see all cases see them, until someone is assigned.</span>
                  </span>
                </label>
                <label className={`settings-choice ${choice === "keep" ? "picked" : ""}`}>
                  <input type="radio" name="open-cases" checked={choice === "keep"} onChange={() => setChoice("keep")} />
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span className="settings-choice-title">Keep them under {who}</span>
                    <span className="settings-choice-sub">Shown as &quot;Owner: {personLabel(user)} (disabled)&quot;. You can move them later.</span>
                  </span>
                </label>
              </div>
              {stays.length > 0 && (
                <p className="setup-note" style={{ margin: "10px 0 0" }}>
                  {stays.join(" ")}
                </p>
              )}
              {stale && (
                <p className="setup-note" style={{ margin: "10px 0 0", color: "var(--badge-amber-text)" }}>
                  Couldn&apos;t check their latest cases; these counts are from when the page loaded.
                </p>
              )}
            </>
          )}
        </div>

        {/* ALSO: one line per thing that goes with the account. P5 adds
            "Telegram @x is disconnected. Enabling again won't reconnect." */}
        {hadAiKey && (
          <div className="settings-dialog-section">
            <div className="settings-dialog-label">Also</div>
            <ul className="settings-dialog-list">
              <li>Their AI key is deleted. Enabling them again won&apos;t bring it back.</li>
            </ul>
          </div>
        )}

        {error && <div className="setup-error" style={{ marginTop: 12 }}>{error}</div>}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 16 }}>
          <button type="button" className="btn-secondary-modern" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary-modern settings-danger-btn"
            onClick={() => void submit()}
            disabled={saving || !ready}
            style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
          >
            <UserX style={{ width: 14, height: 14 }} /> {saving ? "Disabling…" : `Disable ${who}`}
          </button>
        </div>
      </div>
    </div>
  );
}
