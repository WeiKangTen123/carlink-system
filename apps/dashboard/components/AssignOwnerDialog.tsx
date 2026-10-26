"use client";

import { useEffect, useMemo, useState } from "react";
import { Lightbulb, Search, UserRound } from "lucide-react";
import { ownerLabel, personLabel, type Owner, type SuggestedOwner } from "@/lib/api";
import { listAssignableUsersAction, setCaseOwnerAction, type AssignableUser } from "@/app/reports/actions";

/** Admin: give a case to someone, or make it Unassigned. Opens on the
 * suggested owner when the case has none (GET /reports/{id} sends
 * suggested_owner to admins on Unassigned cases), and says why. */
export function AssignOwnerDialog({
  reportId,
  owner,
  suggested,
  onClose,
  onAssigned,
}: {
  reportId: string;
  owner: Owner | null;
  suggested?: SuggestedOwner | null;
  onClose: () => void;
  onAssigned: (owner: Owner | null) => void;
}) {
  const [users, setUsers] = useState<AssignableUser[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  // "" = Unassigned.
  const [picked, setPicked] = useState<string>(owner?.id ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ownerId = owner?.id ?? null;
  const suggestedId = suggested?.id ?? null;

  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    listAssignableUsersAction().then((res) => {
      if (cancelled) return;
      if ("error" in res) {
        setLoadError(res.error);
        return;
      }
      setUsers(res.users);
      // An Unassigned case starts on the suggestion, if that person can
      // still be picked (a disabled account can't).
      if (!ownerId && suggestedId && res.users.some((u) => u.id === suggestedId)) setPicked(suggestedId);
    });
    return () => {
      cancelled = true;
    };
  }, [attempt, ownerId, suggestedId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !saving) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [saving, onClose]);

  const needle = query.trim().toLowerCase();
  const shown = useMemo(() => {
    const list = (users ?? []).filter(
      (u) => !needle || (u.name || "").toLowerCase().includes(needle) || u.username.toLowerCase().includes(needle)
    );
    // The suggestion first, then by name.
    return list.sort((a, b) => {
      if (a.id === suggestedId) return -1;
      if (b.id === suggestedId) return 1;
      return (a.name || a.username).localeCompare(b.name || b.username);
    });
  }, [users, needle, suggestedId]);

  const unchanged = picked === (ownerId ?? "");

  async function save() {
    setSaving(true);
    setError(null);
    const res = await setCaseOwnerAction(reportId, picked || null);
    setSaving(false);
    if ("error" in res) {
      setError(res.error);
      return;
    }
    onAssigned(res.owner);
  }

  return (
    <div className="modal-backdrop" onClick={() => !saving && onClose()}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="assign-owner-title"
        style={{ maxWidth: 520 }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="assign-owner-title" style={{ fontSize: 16, fontWeight: 800, margin: "0 0 4px" }}>
          Assign this case
        </h3>
        <p className="setup-note" style={{ margin: "0 0 12px" }}>
          Now: <strong>{ownerLabel(owner)}</strong>. The owner sees the case under My cases and works on it.
        </p>

        {suggested && !owner && (
          <div className="assign-owner-hint">
            <Lightbulb style={{ width: 14, height: 14, flexShrink: 0, marginTop: 1 }} />
            <span>
              Suggested: <strong>{personLabel(suggested)}</strong>
              {suggested.reason ? <> — {suggested.reason}</> : null}
            </span>
          </div>
        )}

        {loadError ? (
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <div className="setup-error" style={{ margin: 0 }}>{loadError}</div>
            <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          </div>
        ) : users === null ? (
          <p className="setup-note">Loading people…</p>
        ) : (
          <>
            <div style={{ position: "relative", marginBottom: 8 }}>
              <Search style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", width: 13, height: 13, color: "var(--text-muted)" }} />
              <input
                type="text"
                className="settings-input"
                style={{ marginTop: 0, paddingLeft: 28 }}
                placeholder="Search name or username…"
                aria-label="Search people"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                autoFocus
              />
            </div>

            <div className="assign-owner-list" role="radiogroup" aria-label="Owner">
              {!needle && (
                <label className={`assign-owner-row ${picked === "" ? "picked" : ""}`}>
                  <input type="radio" name="assign-owner" checked={picked === ""} onChange={() => setPicked("")} />
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <strong>Unassigned</strong>
                    <span className="assign-owner-sub">Only admins and staff who see all cases see it.</span>
                  </span>
                </label>
              )}
              {shown.map((u) => (
                <label key={u.id} className={`assign-owner-row ${picked === u.id ? "picked" : ""}`}>
                  <input type="radio" name="assign-owner" checked={picked === u.id} onChange={() => setPicked(u.id)} />
                  <UserRound style={{ width: 14, height: 14, color: "var(--text-muted)", flexShrink: 0 }} />
                  <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {personLabel(u)}
                    {ownerId === u.id && <span className="settings-readonly-tag" style={{ marginLeft: 6 }}>Now</span>}
                    {suggestedId === u.id && !ownerId && <span className="user-new-tag">Suggested</span>}
                  </span>
                  {u.cases && <span className="assign-owner-sub" style={{ flexShrink: 0 }}>{u.cases.open} open</span>}
                </label>
              ))}
              {shown.length === 0 && <p className="setup-note" style={{ margin: "8px 4px" }}>Nobody matches “{query.trim()}”.</p>}
            </div>
          </>
        )}

        {error && <div className="setup-error" style={{ marginTop: 12 }}>{error}</div>}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 14 }}>
          <button type="button" className="btn-secondary-modern" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="button" className="btn-primary-modern" onClick={() => void save()} disabled={saving || unchanged || users === null}>
            {saving ? "Saving…" : picked ? "Assign" : "Make unassigned"}
          </button>
        </div>
      </div>
    </div>
  );
}
