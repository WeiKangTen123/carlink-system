"use client";

import { useState } from "react";
import { ArrowRightLeft } from "lucide-react";
import { personLabel, type UserAccount } from "@/lib/api";
import { reassignCasesAction } from "./actions";
import { CaseCountsText } from "./EditUserDialog";
import { PersonSelect, plural, shortName, useEscape, useFreshCounts } from "./DisableUserDialog";

/** Settings -> Users -> Move their cases (POST /reports/reassign): all of
 * one person's open cases, and their signed-off ones if ticked, go to
 * someone else or become Unassigned. Unfinished drafts stay with them.
 * Each case's history records the move, as with Assign on a case. Works
 * for disabled accounts too, whose cases may have been kept under them.
 * The counts are read again when the dialog opens (useFreshCounts); the
 * message afterwards uses the number the API says it moved. */
export function MoveCasesDialog({
  user: rowUser,
  people: rowPeople,
  onCancel,
  onDone,
}: {
  user: UserAccount;
  /** Everyone the cases could go to (active, and not them), as the table
   * had it; read again on open. */
  people: UserAccount[];
  onCancel: () => void;
  onDone: (notice: string) => void;
}) {
  const { user, people, checking, stale } = useFreshCounts(rowUser, rowPeople);
  const open = user.cases?.open ?? 0;
  const signedOff = user.cases?.signed_off ?? 0;
  const who = shortName(user);

  const [toUnassigned, setToUnassigned] = useState(false);
  const [target, setTarget] = useState("");
  const [includeSigned, setIncludeSigned] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEscape(onCancel, saving);

  const targetPerson = people.find((p) => p.id === target) ?? null;
  const count = open + (includeSigned ? signedOff : 0);
  const ready = !checking && count > 0 && (toUnassigned || Boolean(targetPerson));

  const submit = async () => {
    setSaving(true);
    setError(null);
    const result = await reassignCasesAction(user.id, toUnassigned ? null : target, includeSigned);
    setSaving(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    onDone(
      result.moved === 0
        ? `${personLabel(user)} had no cases to move.`
        : toUnassigned || !targetPerson
          ? `Made ${plural(result.moved, "case")} of ${personLabel(user)} Unassigned.`
          : `Moved ${plural(result.moved, "case")} from ${personLabel(user)} to ${personLabel(targetPerson)}.`
    );
  };

  return (
    <div className="modal-backdrop" onClick={() => !saving && onCancel()}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="move-cases-title"
        style={{ maxWidth: 560 }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="move-cases-title" style={{ fontSize: 16, fontWeight: 800, margin: "0 0 4px" }}>
          Move {personLabel(user)}&apos;s cases
        </h3>
        <p className="setup-note" style={{ margin: "0 0 4px" }} role="status">
          {checking ? (
            "Checking their cases…"
          ) : (
            <>
              Now: <CaseCountsText cases={user.cases} />. The new owner sees them under My cases and works on them.
            </>
          )}
        </p>
        {stale && (
          <p className="setup-note" style={{ margin: "0 0 4px", color: "var(--badge-amber-text)" }}>
            Couldn&apos;t check their latest cases; these counts are from when the page loaded.
          </p>
        )}

        <div className="settings-dialog-section">
          <div className="settings-dialog-label">Move to</div>
          <div className="settings-choices" role="radiogroup" aria-label="Move their cases to">
            <label className={`settings-choice ${!toUnassigned ? "picked" : ""}`}>
              <input type="radio" name="move-to" checked={!toUnassigned} onChange={() => setToUnassigned(false)} />
              <span style={{ flex: 1, minWidth: 0 }}>
                <span className="settings-choice-title">Someone else</span>
                <PersonSelect
                  people={people}
                  value={target}
                  label="Move their cases to"
                  onChange={(id) => {
                    setTarget(id);
                    if (id) setToUnassigned(false);
                  }}
                />
              </span>
            </label>
            <label className={`settings-choice ${toUnassigned ? "picked" : ""}`}>
              <input type="radio" name="move-to" checked={toUnassigned} onChange={() => setToUnassigned(true)} />
              <span style={{ flex: 1, minWidth: 0 }}>
                <span className="settings-choice-title">Nobody: make them Unassigned</span>
                <span className="settings-choice-sub">Only admins and staff who see all cases see them, until someone is assigned.</span>
              </span>
            </label>
          </div>

          {signedOff > 0 && (
            <label className="user-tick" style={{ marginTop: 12 }}>
              <input type="checkbox" checked={includeSigned} onChange={(e) => setIncludeSigned(e.target.checked)} />
              <span>
                Also move their {plural(signedOff, "signed-off case")}
                <span className="field-hint">Off: those stay under {who}. Either way the sign-off stays in the name of whoever signed.</span>
              </span>
            </label>
          )}
          <p className="setup-note" style={{ margin: "10px 0 0" }}>
            {checking
              ? "Checking…"
              : count === 0
              ? signedOff > 0
                ? "They have no open cases. Tick the box to move their signed-off ones."
                : "They have no open cases to move."
              : "Unfinished drafts stay with them."}
          </p>
        </div>

        {error && <div className="setup-error" style={{ marginTop: 12 }}>{error}</div>}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 16 }}>
          <button type="button" className="btn-secondary-modern" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary-modern"
            onClick={() => void submit()}
            disabled={saving || !ready}
            style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
          >
            <ArrowRightLeft style={{ width: 14, height: 14 }} />
            {saving ? "Moving…" : !checking && count > 0 ? `Move ${plural(count, "case")}` : "Move cases"}
          </button>
        </div>
      </div>
    </div>
  );
}
