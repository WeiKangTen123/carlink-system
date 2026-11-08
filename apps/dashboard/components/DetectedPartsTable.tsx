"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ClipboardCheck, Image as ImageIcon, Link2Off, Plus, Trash2, X } from "lucide-react";
import {
  DAMAGE_REMARKS,
  conditionWordsOf,
  fileUrl,
  toDamageRemarks,
  type AnnexAPartItem,
  type DamageItemPatch,
  type DamageItemsResponse,
  type DamageSummaryItem,
  type Taxonomy,
} from "@/lib/api";
import type { ZoneResolution } from "@/lib/vehicleZones";
import { isSideUndetermined } from "@/lib/vehicleZones";
import { addDamageItemAction, getTaxonomyAction, patchDamageItemAction, removeDamageItemAction } from "@/app/reports/actions";
import { annexLinesByPart } from "@/lib/annexRows";
import { severityClass } from "@/lib/caseFields";
import { ConditionPicker } from "./ConditionPicker";

/** damage_summary.photo_reference is "P01", "P02"... in upload order (see schema.py) */
const photoLabel = (index: number) => `P${String(index + 1).padStart(2, "0")}`;
const photoIndexOf = (ref: string | null | undefined): number => {
  if (!ref) return -1;
  const n = parseInt(ref.replace(/\D/g, ""), 10) - 1;
  return Number.isFinite(n) ? n : -1;
};

interface Props {
  reportId: string;
  /** Signed off, or this account may only look: nothing can be changed. */
  readOnly: boolean;
  damageEntries: DamageSummaryItem[];
  zoneResolutions: (ZoneResolution | null)[];
  highlightedDamageIndex: number | null;
  onHotspotClick: (idx: number, item: DamageSummaryItem) => void;
  photos: string[];
  photoThumbs?: string[];
  onSelectPhoto: (idx: number) => void;
  /** Annex A as saved, for the "3 lines, 2 priced" cell per part. */
  annexAItems: AnnexAPartItem[];
  /** The API's answer to any change (parts + re-synced Annex A + totals). */
  onApplied: (res: DamageItemsResponse) => void;
  /** A row was removed: index-keyed state elsewhere (the highlight) is stale. */
  onRemoved?: (idx: number) => void;
  onGoToAnnexA?: () => void;
}

/** The AI took this part's side from nearby photos, not its own: worth a
 * look until a person has touched the row. The API clears the flag on any
 * edit; a ticked row is hidden too, because the tick is that look. */
const sideInferred = (item: DamageSummaryItem) => Boolean(item.side_inferred) && !item.human_verified;

/** Tab 1's "Detected parts": the damage_summary as an Annex A-shaped table
 * (Item | Vehicle Parts Description | Condition | Remarks | Photo | Annex A
 * | Verified). What is damaged is decided here; what the report lists and at
 * what price is Annex A's (tab 3), so no price shows here. Every change
 * goes straight to PATCH / POST / DELETE damage-items and the API re-syncs
 * Annex A (priced and typed rows kept). From CaseDamageTab.tsx, 2026-11-04:
 * the OEM number and AI confidence columns went (the firm prints neither).
 * 2026-11-06: Condition is the firm's wording (`condition`, damage_type
 * when an older case has none) and Remarks is set here, because both print
 * on the part's main Annex A line. */
export function DetectedPartsTable({
  reportId,
  readOnly,
  damageEntries,
  zoneResolutions,
  highlightedDamageIndex,
  onHotspotClick,
  photos,
  photoThumbs,
  onSelectPhoto,
  annexAItems,
  onApplied,
  onRemoved,
  onGoToAnnexA,
}: Props) {
  const router = useRouter();
  const [savingIdx, setSavingIdx] = useState<number | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Which row has its photo picker open (or "new" for the add row).
  const [pickerFor, setPickerFor] = useState<number | "new" | null>(null);
  const [adding, setAdding] = useState<{ part: string; condition: string; remarks: string; photo_reference: string } | null>(null);
  // A condition or remark a row was just set to, kept visible while it
  // saves and after a refusal (so the choice isn't silently snapped back).
  const [conditionDraft, setConditionDraft] = useState<Record<number, string>>({});
  const [remarksDraft, setRemarksDraft] = useState<Record<number, string>>({});

  // The controlled vocabulary: the part select and the firm's condition
  // words. Loaded once; an empty part list just leaves the current values
  // as the only options, and the condition words fall back to the built-in
  // copy (also while loading), so a Condition can always be changed.
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null);
  useEffect(() => {
    let cancelled = false;
    void getTaxonomyAction().then((t) => {
      if (!cancelled) setTaxonomy(t);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  const parts = taxonomy?.parts || [];
  const words = useMemo(() => conditionWordsOf(taxonomy), [taxonomy]);

  const linesByPart = useMemo(
    () =>
      annexLinesByPart(
        annexAItems,
        damageEntries.map((d) => d.part)
      ),
    [annexAItems, damageEntries]
  );
  const verifiedCount = damageEntries.filter((item) => item.human_verified).length;

  /** Runs one damage-items call and hands its answer to the store. An API
   * from before the widened endpoints answers without the parts: then the
   * page is refreshed instead, so nothing shows a change that didn't stick. */
  const run = async (key: number | "new", call: () => Promise<DamageItemsResponse | { error: string }>): Promise<boolean> => {
    setSavingIdx(key);
    setError(null);
    const res = await call();
    setSavingIdx(null);
    if ("error" in res) {
      setError(res.error);
      return false;
    }
    if (Array.isArray(res.damage_summary)) onApplied(res);
    else router.refresh();
    return true;
  };

  const patch = (idx: number, body: DamageItemPatch) => run(idx, () => patchDamageItemAction(reportId, idx, body));

  const toggleVerified = (idx: number, item: DamageSummaryItem) => {
    if (readOnly) return;
    void patch(idx, { human_verified: !item.human_verified });
  };

  /** Saves one field of a row, keeping the choice shown while it saves; a
   * refusal leaves it shown beside the error instead of snapping back
   * silently. */
  const saveDrafted = async (
    idx: number,
    value: string,
    saved: string,
    setDraft: (update: (s: Record<number, string>) => Record<number, string>) => void,
    body: DamageItemPatch
  ) => {
    if (readOnly) return;
    const dropDraft = () =>
      setDraft((s) => {
        const next = { ...s };
        delete next[idx];
        return next;
      });
    // Back to what is saved (after a refusal, say): nothing to send, only
    // the refused choice to stop showing.
    if (value === saved) return dropDraft();
    setDraft((s) => ({ ...s, [idx]: value }));
    if (await patch(idx, body)) dropDraft();
  };

  // The API only accepts the firm's words (400 otherwise), which is all the
  // editor offers. damage_type is left as the AI's category.
  const saveCondition = (idx: number, item: DamageSummaryItem, value: string) =>
    saveDrafted(idx, value, item.condition || "", setConditionDraft, { condition: value });

  const saveRemarks = (idx: number, item: DamageSummaryItem, value: string) =>
    saveDrafted(idx, value, item.remarks || "", setRemarksDraft, { remarks: toDamageRemarks(value) });

  const changePart = (idx: number, item: DamageSummaryItem, part: string) => {
    if (readOnly || part === item.part) return;
    void patch(idx, { part });
  };

  const linkPhoto = (idx: number, photoIdx: number | null) => {
    setPickerFor(null);
    if (readOnly) return;
    void patch(idx, { photo_reference: photoIdx == null ? null : photoLabel(photoIdx) });
  };

  const remove = async (idx: number, item: DamageSummaryItem) => {
    if (readOnly) return;
    if (!confirm(`Remove "${item.part}" from the detected parts? Its unpriced Annex A lines go too; priced lines are kept and flagged.`)) return;
    const ok = await run(idx, () => removeDamageItemAction(reportId, idx));
    if (ok) {
      // Index-keyed state would now point at the next row.
      setPickerFor(null);
      setConditionDraft({});
      setRemarksDraft({});
      onRemoved?.(idx);
    }
  };

  const submitAdd = async () => {
    if (readOnly || !adding || !adding.part.trim()) return;
    const ok = await run("new", () =>
      // No damage_type: the add row asks for the firm's wording only.
      addDamageItemAction(reportId, {
        part: adding.part.trim(),
        condition: adding.condition || null,
        remarks: toDamageRemarks(adding.remarks),
        photo_reference: adding.photo_reference || null,
      })
    );
    if (ok) setAdding(null);
  };

  /** The photo strip under a row: pick the photo this part is on. */
  const photoPicker = (key: number | "new", current: string | null | undefined, onPick: (photoIdx: number | null) => void) => (
    <tr key={`picker-${key}`} className="detected-photo-picker-row">
      <td colSpan={8}>
        <div className="detected-photo-picker">
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Which photo shows this part?</span>
          {photos.map((_, i) => {
            const label = photoLabel(i);
            return (
              <button
                key={label}
                type="button"
                className={`detected-photo-choice ${current === label ? "active" : ""}`}
                onClick={() => onPick(i)}
                title={label}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={fileUrl(photoThumbs?.[i] || photos[i])} alt={label} loading="lazy" decoding="async" />
                <span>{label}</span>
              </button>
            );
          })}
          {current && (
            <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "4px 10px" }} onClick={() => onPick(null)}>
              <Link2Off style={{ width: 12, height: 12 }} /> Unlink
            </button>
          )}
          <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "4px 10px" }} onClick={() => setPickerFor(null)}>
            <X style={{ width: 12, height: 12 }} /> Close
          </button>
        </div>
      </td>
    </tr>
  );

  const annexCell = (part: string) => {
    const n = linesByPart.get(part);
    if (!n) return <span style={{ color: "var(--text-muted)" }}>not linked</span>;
    const lines = `${n.lines} line${n.lines === 1 ? "" : "s"}`;
    const priced = n.priced === 0 ? "unpriced" : n.priced === n.lines ? "all priced" : `${n.priced} priced`;
    return (
      <span style={{ color: n.priced === 0 ? "var(--badge-amber-text)" : "var(--text-secondary)" }}>
        {lines}, {priced}
      </span>
    );
  };

  return (
    <div className="card-glass">
      <div className="card-header">
        <div>
          <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <ClipboardCheck style={{ width: 16, height: 16, color: "var(--accent-primary)" }} /> Detected parts ({damageEntries.length})
          </div>
          <div className="card-subtitle">
            {readOnly ? "Locked — reopen the report to change the parts" : "Check each part the AI found: its name, condition, remarks and photo. Tick it when verified."}
          </div>
        </div>
        <span
          className={`chip-severity ${verifiedCount === damageEntries.length && damageEntries.length > 0 ? "minor" : "moderate"}`}
          style={{ fontSize: 10 }}
        >
          {verifiedCount} of {damageEntries.length} verified
        </span>
      </div>

      {error && <p style={{ fontSize: 12, color: "var(--danger, #ef4444)", marginTop: 0, marginBottom: 12 }}>{error}</p>}

      {damageEntries.length === 0 && !adding ? (
        <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>No parts detected.</p>
      ) : (
        <div className="annex-table-wrapper">
          <table className="damage-table-modern detected-parts-table">
            <thead>
              <tr>
                <th style={{ width: 50 }}>Item</th>
                <th>Vehicle Parts Description</th>
                <th>Condition</th>
                <th>Remarks</th>
                <th>Photo</th>
                <th>Annex A</th>
                <th>Verified</th>
                {!readOnly && <th style={{ width: 36 }} aria-label="Remove part" />}
              </tr>
            </thead>
            <tbody>
              {damageEntries.map((item, idx) => {
                const badgeNumber = zoneResolutions[idx]?.badgeNumber ?? idx + 1;
                const verified = Boolean(item.human_verified);
                const photoIdx = photoIndexOf(item.photo_reference);
                const hasPhoto = photoIdx >= 0 && photoIdx < photos.length;
                const condition = conditionDraft[idx] ?? (item.condition || "");
                const remarks = remarksDraft[idx] ?? (item.remarks || "");
                const inVocabulary = parts.includes(item.part);
                const rows = [
                  <tr
                    key={idx}
                    id={`damage-row-${idx}`}
                    onClick={() => onHotspotClick(idx, item)}
                    style={{
                      background: highlightedDamageIndex === idx ? "var(--bg-hover, rgba(56,189,248,0.08))" : undefined,
                      cursor: "pointer",
                      opacity: savingIdx === idx ? 0.6 : 1,
                    }}
                    title="Click to highlight on the 3D blueprint"
                  >
                    <td>
                      <span
                        className={`hotspot-beacon-3d ${severityClass(item.severity) === "severe" ? "severe-spot" : ""}`}
                        style={{ position: "static", width: 20, height: 20, fontSize: 9, flexShrink: 0, display: "inline-flex" }}
                      >
                        {String(badgeNumber).padStart(2, "0")}
                      </span>
                    </td>
                    {/* stopPropagation throughout: the row itself is a click
                        target (highlights the part on the blueprint), so
                        without this, picking a part or ticking the box would
                        also fire the row's own handler. */}
                    <td onClick={(e) => e.stopPropagation()}>
                      {readOnly ? (
                        <strong>{item.part}</strong>
                      ) : (
                        <select
                          className="detected-part-select"
                          value={item.part}
                          disabled={savingIdx === idx}
                          aria-label={`Part ${badgeNumber}`}
                          onChange={(e) => changePart(idx, item, e.target.value)}
                        >
                          {!inVocabulary && <option value={item.part}>{item.part} (as recorded)</option>}
                          {parts.map((p) => (
                            <option key={p} value={p}>
                              {p}
                            </option>
                          ))}
                        </select>
                      )}
                      {/* The AI identified the panel but not which side it
                          was on, so no 3D marker is shown -- guessing a side
                          would invent the one fact it couldn't determine. */}
                      {isSideUndetermined(item.part) && (
                        <span className="chip-severity moderate" style={{ fontSize: 9, marginLeft: 6 }} title="Side not identified from the photo — pick the sided part to place it on the blueprint">
                          set side
                        </span>
                      )}
                      {sideInferred(item) && (
                        <span className="chip-severity moderate" style={{ fontSize: 9, marginLeft: 6 }} title="The AI guessed the side from nearby photos; check it">
                          side inferred
                        </span>
                      )}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      {readOnly ? (
                        <span>{item.condition || item.damage_type || "—"}</span>
                      ) : (
                        <ConditionPicker
                          className="detected-part-select"
                          style={{ minWidth: 140 }}
                          value={condition}
                          fallback={item.damage_type}
                          words={words}
                          disabled={savingIdx === idx}
                          ariaLabel={`Condition of part ${badgeNumber}`}
                          partName={item.part}
                          onChange={(value) => void saveCondition(idx, item, value)}
                        />
                      )}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      {readOnly ? (
                        <span>{item.remarks || "—"}</span>
                      ) : (
                        <select
                          className="detected-part-select"
                          style={{ minWidth: 96 }}
                          value={remarks}
                          disabled={savingIdx === idx}
                          aria-label={`Remarks for part ${badgeNumber}`}
                          onChange={(e) => void saveRemarks(idx, item, e.target.value)}
                        >
                          <option value="">—</option>
                          {/* A stored remark outside the two (none should
                              exist) still shows rather than reading blank. */}
                          {remarks && !toDamageRemarks(remarks) && <option value={remarks}>{remarks}</option>}
                          {DAMAGE_REMARKS.map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                      )}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="detected-photo-cell">
                        {hasPhoto ? (
                          <button type="button" className="detected-photo-thumb" onClick={() => onSelectPhoto(photoIdx)} title={`Show ${item.photo_reference}`}>
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img src={fileUrl(photoThumbs?.[photoIdx] || photos[photoIdx])} alt={item.photo_reference || ""} loading="lazy" decoding="async" />
                            <span>{item.photo_reference}</span>
                          </button>
                        ) : (
                          <span style={{ fontSize: 11, color: "var(--text-muted)", display: "inline-flex", alignItems: "center", gap: 4 }}>
                            <ImageIcon style={{ width: 12, height: 12 }} /> {item.photo_reference ? `${item.photo_reference} (missing)` : "not linked"}
                          </span>
                        )}
                        {!readOnly && (
                          <button
                            type="button"
                            className="detected-link-btn"
                            disabled={savingIdx === idx || photos.length === 0}
                            onClick={() => setPickerFor(pickerFor === idx ? null : idx)}
                          >
                            {hasPhoto ? "Change" : "Link"}
                          </button>
                        )}
                      </div>
                    </td>
                    <td onClick={(e) => e.stopPropagation()} style={{ fontSize: 12, whiteSpace: "nowrap" }}>
                      {annexCell(item.part)}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <button
                        type="button"
                        className={`verify-toggle-modern ${verified ? "verified" : ""}`}
                        onClick={() => toggleVerified(idx, item)}
                        disabled={readOnly || savingIdx === idx}
                        style={{ cursor: readOnly ? "default" : "pointer", border: "none" }}
                        title={readOnly ? "Report is locked" : verified ? "Mark as unverified" : "Mark as verified"}
                      >
                        {verified ? "✓ Verified" : "Pending"}
                      </button>
                    </td>
                    {!readOnly && (
                      <td onClick={(e) => e.stopPropagation()} style={{ textAlign: "center" }}>
                        <button
                          type="button"
                          className="annex-row-remove"
                          title="Remove this part"
                          aria-label={`Remove part ${badgeNumber}`}
                          disabled={savingIdx === idx}
                          onClick={() => void remove(idx, item)}
                        >
                          <Trash2 style={{ width: 13, height: 13 }} />
                        </button>
                      </td>
                    )}
                  </tr>,
                ];
                if (pickerFor === idx && !readOnly) rows.push(photoPicker(idx, item.photo_reference, (p) => linkPhoto(idx, p)));
                return rows;
              })}

              {adding && !readOnly && (
                <>
                  <tr key="add-row" style={{ opacity: savingIdx === "new" ? 0.6 : 1 }}>
                    <td>
                      <Plus style={{ width: 14, height: 14, color: "var(--text-muted)" }} />
                    </td>
                    <td>
                      <select
                        className="detected-part-select"
                        value={adding.part}
                        aria-label="New part"
                        onChange={(e) => setAdding({ ...adding, part: e.target.value })}
                      >
                        <option value="">Choose a part…</option>
                        {parts.map((p) => (
                          <option key={p} value={p}>
                            {p}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <ConditionPicker
                        className="detected-part-select"
                        style={{ minWidth: 140 }}
                        value={adding.condition}
                        words={words}
                        placeholder="Condition…"
                        ariaLabel="New part condition"
                        partName={adding.part || undefined}
                        onChange={(value) => setAdding({ ...adding, condition: value })}
                      />
                    </td>
                    <td>
                      <select
                        className="detected-part-select"
                        style={{ minWidth: 96 }}
                        value={adding.remarks}
                        aria-label="New part remarks"
                        onChange={(e) => setAdding({ ...adding, remarks: e.target.value })}
                      >
                        <option value="">—</option>
                        {DAMAGE_REMARKS.map((r) => (
                          <option key={r} value={r}>
                            {r}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <div className="detected-photo-cell">
                        <span style={{ fontSize: 11, color: adding.photo_reference ? "var(--text-primary)" : "var(--text-muted)" }}>
                          {adding.photo_reference || "not linked"}
                        </span>
                        <button type="button" className="detected-link-btn" disabled={photos.length === 0} onClick={() => setPickerFor(pickerFor === "new" ? null : "new")}>
                          {adding.photo_reference ? "Change" : "Link"}
                        </button>
                      </div>
                    </td>
                    <td style={{ fontSize: 12, color: "var(--text-muted)" }}>added after saving</td>
                    <td colSpan={2}>
                      <div style={{ display: "flex", gap: 6 }}>
                        <button type="button" className="btn-primary-modern" style={{ fontSize: 11, padding: "5px 12px" }} disabled={!adding.part || savingIdx === "new"} onClick={() => void submitAdd()}>
                          {savingIdx === "new" ? "Adding…" : "Add"}
                        </button>
                        <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} onClick={() => setAdding(null)}>
                          Cancel
                        </button>
                      </div>
                    </td>
                  </tr>
                  {pickerFor === "new" &&
                    photoPicker("new", adding.photo_reference || null, (p) => {
                      setPickerFor(null);
                      setAdding({ ...adding, photo_reference: p == null ? "" : photoLabel(p) });
                    })}
                </>
              )}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap", marginTop: 12 }}>
        {!readOnly && !adding ? (
          <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "6px 12px" }} onClick={() => setAdding({ part: "", condition: "", remarks: "", photo_reference: "" })}>
            <Plus style={{ width: 13, height: 13 }} /> Add a part
          </button>
        ) : (
          <span />
        )}
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          These parts are listed in Annex A.{" "}
          {onGoToAnnexA ? (
            <button type="button" className="link-button" onClick={onGoToAnnexA}>
              Price them in 3 Annex A →
            </button>
          ) : (
            "Price them in 3 Annex A."
          )}
        </span>
      </div>
    </div>
  );
}
