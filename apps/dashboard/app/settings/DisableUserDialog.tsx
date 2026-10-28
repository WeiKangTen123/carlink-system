"use client";

import { useEffect, useMemo, useState } from "react";
import { UserX } from "lucide-react";
import { personLabel, type OpenCasesChoice, type UserAccount } from "@/lib/api";
import { updateUserAction } from "./actions";

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
 * The counts come from GET /users (`cases`).
 *
 * SLOT "ALSO" (P3-UI): "Their AI key is deleted." (P5): "Telegram @x is
 * disconnected. Enabling again won't reconnect." */
export function DisableUserDialog({
  user,
  people,
  onCancel,
  onDone,
}: {
  user: UserAccount;
  /** Everyone their cases could go to: active, and not them. */
  people: UserAccount[];
  onCancel: () => void;
  onDone: (notice: string) => void;
}) {
  const open = user.cases?.open ?? 0;
  const signedOff = user.cases?.signed_off ?? 0;
  const drafts = user.cases?.drafts ?? 0;
  const who = shortName(user);

  const [choice, setChoice] = useState<OpenCasesChoice>("unassign");
  const [target, setTarget] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEscape(onCancel, saving);

  const targetPerson = people.find((p) => p.id === target) ?? null;
  const ready = open === 0 || choice !== "move" || Boolean(targetPerson);

  const submit = async () => {
    setSaving(true);
    setError(null);
    // With nothing open there is nothing to choose; the API keeps whatever
    // turns up meanwhile under them (its default).
    const patch =
      open === 0
        ? { active: false }
        : choice === "move"
          ? { active: false, open_cases: choice, open_cases_to: target }
          : { active: false, open_cases: choice };
    const result = await updateUserAction(user.id, patch);
    setSaving(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    const cases =
      open === 0
        ? ""
        : choice === "move" && targetPerson
          ? ` Their ${plural(open, "open case")} went to ${personLabel(targetPerson)}.`
          : choice === "unassign"
            ? ` Their ${plural(open, "open case")} ${open === 1 ? "is" : "are"} now Unassigned.`
            : ` Their ${plural(open, "open case")} stayed under them.`;
    onDone(`${personLabel(user)} is disabled.${cases}`);
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
          {open > 0 ? (
            <>
              <div className="settings-dialog-label">Their {plural(open, "open case")}</div>
              <div className="settings-choices" role="radiogroup" aria-label={`What happens to their ${plural(open, "open case")}`}>
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
            </>
          ) : (
            <>
              <div className="settings-dialog-label">Their cases</div>
              <p className="setup-note" style={{ margin: 0 }}>They have no open cases.</p>
            </>
          )}
          {stays.length > 0 && (
            <p className="setup-note" style={{ margin: "10px 0 0" }}>
              {stays.join(" ")}
            </p>
          )}
        </div>

        {/* ---- SLOT "ALSO" (P3-UI, P5) ----
            <div className="settings-dialog-section">
              <div className="settings-dialog-label">Also</div>
              <ul className="settings-dialog-list"><li>Their AI key is deleted.</li></ul>
            </div> */}

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
