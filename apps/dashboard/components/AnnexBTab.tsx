"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronDown, ChevronRight, Lightbulb, Plus, RotateCcw, Save, Trash2 } from "lucide-react";
import type { LabourSuggestionLine, LabourSuggestions, ReportDetail } from "@/lib/api";
import { getLabourSuggestionsAction, saveAnnexBAction } from "@/app/reports/actions";
import { formatAmount, formatSgd, sameLabourKey } from "@/lib/pastReports";
import type { CaseStore } from "@/lib/caseState";
import { BLANK_LABOUR_LINE, fromLabourRow, isBadAmount, parseAmount, sumRows, toLabourRow, type LabourRow } from "@/lib/annexRows";
import { LockedNote, SavedNote, UnsavedNote } from "./SaveNotes";

/** "Usual labour lines" above the Annex B table: what past reports listed
 * for this make and model (else any), with what each was usually adjusted
 * to. A line goes into the table with that price filled and `prefilled`
 * set, so the tag shows where the figure came from until someone types
 * over it. Lines already in the table are hidden. */
function LabourLinesPicker({
  reportId,
  active,
  existing,
  disabled,
  onAdd,
}: {
  reportId: string;
  /** The Annex B tab has been opened -- nothing is fetched before. */
  active: boolean;
  /** Job descriptions currently in the table (saved or not). */
  existing: string[];
  disabled: boolean;
  onAdd: (line: LabourSuggestionLine) => void;
}) {
  const [open, setOpen] = useState(true);
  const [loadState, setLoadState] = useState<"idle" | "loading" | "loaded" | "error">("idle");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [data, setData] = useState<LabourSuggestions | null>(null);

  async function load() {
    setLoadState("loading");
    setLoadError(null);
    const res = await getLabourSuggestionsAction(reportId);
    if ("error" in res) {
      setLoadError(res.error);
      setLoadState("error");
      return;
    }
    setData(res);
    setLoadState("loaded");
    if (res.groups.length === 0) setOpen(false);
  }

  const requested = useRef(false);
  useEffect(() => {
    if (!active || !open || requested.current) return;
    requested.current = true;
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, open]);

  const inTable = new Set(existing.map(sameLabourKey).filter(Boolean));
  const groups = (data?.groups || [])
    .map((g) => ({ ...g, lines: g.lines.filter((l) => !inTable.has(sameLabourKey(l.description))) }))
    .filter((g) => g.lines.length > 0);
  const total = (data?.groups || []).reduce((n, g) => n + g.lines.length, 0);

  let summary = "";
  if (loadState === "loading") summary = "Looking through past reports…";
  else if (loadState === "error") summary = "Couldn't load";
  else if (data?.hidden) summary = "Staff only";
  else if (data) summary = total === 0 ? "No past labour lines imported yet" : data.vehicle ? `From past ${data.vehicle} cases` : "From past cases";

  return (
    <div className="annex-suggest-panel">
      <button type="button" className="annex-suggest-header" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? <ChevronDown style={{ width: 14, height: 14 }} /> : <ChevronRight style={{ width: 14, height: 14 }} />}
        <Lightbulb style={{ width: 14, height: 14, color: "var(--accent-cyan)" }} />
        <span style={{ fontWeight: 700, fontSize: 13 }}>Usual labour lines</span>
        <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-muted)", textAlign: "right" }}>{summary}</span>
      </button>

      {open && (
        <div style={{ padding: "0 14px 14px" }}>
          {loadState === "loading" && <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Looking through past reports…</div>}

          {loadState === "error" && (
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <div className="setup-error" style={{ margin: 0 }}>{loadError}</div>
              <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} onClick={() => void load()}>
                Try again
              </button>
            </div>
          )}

          {loadState === "loaded" && data && (
            <>
              {data.hidden ? (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Price history is available to staff only.</div>
              ) : total === 0 ? (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  No past reports with an Annex B imported yet — import them on the Knowledge page.
                </div>
              ) : groups.length === 0 ? (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Every usual line is already in Annex B.</div>
              ) : (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  Prices are what past cases were adjusted to — a starting point, not the workshop&apos;s quote.
                </div>
              )}

              {groups.map((group) => (
                <div key={group.reason} style={{ marginTop: 12 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>{group.reason}</div>
                  {group.lines.map((line) => (
                    <div key={sameLabourKey(line.description)} className="annex-suggest-line" style={{ cursor: "default" }}>
                      <span style={{ flex: "1 1 240px", minWidth: 0, fontWeight: 600 }}>{line.description}</span>
                      <span
                        style={{ minWidth: 200, textAlign: "right", whiteSpace: "nowrap", color: line.usual_price != null ? "var(--text-secondary)" : "var(--text-muted)" }}
                        title={line.usual_workshop_est != null ? `Workshops usually quoted ${formatSgd(line.usual_workshop_est)}` : undefined}
                      >
                        {line.usual_price != null
                          ? `usual ${formatSgd(line.usual_price)} (${line.cases} case${line.cases === 1 ? "" : "s"})`
                          : "no price"}
                      </span>
                      <button
                        type="button"
                        className="btn-secondary-modern"
                        style={{ fontSize: 11, padding: "3px 10px" }}
                        disabled={disabled}
                        onClick={() => onAdd(line)}
                      >
                        <Plus style={{ width: 12, height: 12 }} /> Add
                      </button>
                    </div>
                  ))}
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** Tab 4, Annex B: the labour and spray painting table, the usual lines
 * picker, its total, and the Summary with the live Annex A + B figures as
 * saved. Owns annex_b through PUT /reports/{id}/annex-b. Moved from
 * CaseAssessmentTab.tsx on 2026-11-04. */
export function AnnexBTab({
  report,
  store,
  canEdit,
  isSignedOff,
  active,
}: {
  report: ReportDetail;
  store: CaseStore;
  canEdit: boolean;
  isSignedOff: boolean;
  /** The tab has been opened at least once (the picker fetches then). */
  active: boolean;
}) {
  const router = useRouter();
  const { readOnly } = store;
  const plate = store.data.vehicle_info?.plate_number || "";
  const annexBItems = store.annexB;
  const status = store.status.annexB;
  const annexBDirty = store.dirty.annexB;
  const annexBLocked = readOnly || status.save === "saving";

  const sgd = (n: number | null | undefined) => (n == null ? "—" : formatSgd(n));
  const amt = (n: number) => formatAmount(n);

  const liveLabour = {
    est: annexBItems.reduce((acc, it) => acc + parseAmount(it._est), 0),
    adj: annexBItems.reduce((acc, it) => acc + parseAmount(it._adj), 0),
  };
  const hasPricedLabour = annexBItems.some((it) => parseAmount(it._est) > 0 || parseAmount(it._adj) > 0);

  const updateLabourRow = (key: string, patch: Partial<LabourRow>) => {
    if (annexBLocked) return;
    store.setAnnexB((prev) => prev.map((row) => (row._key === key ? { ...row, ...patch } : row)));
  };

  const handleAddLabourLine = () => {
    if (annexBLocked) return;
    store.setAnnexB((prev) => [...prev, toLabourRow(BLANK_LABOUR_LINE)]);
  };

  const handleRemoveLabourLine = (key: string) => {
    if (annexBLocked) return;
    store.setAnnexB((prev) => prev.filter((row) => row._key !== key));
  };

  /** From the usual labour lines: the line with its usual price filled in
   * and marked prefilled, so the tag says where the figure came from. */
  const handleAddUsualLine = (line: LabourSuggestionLine) => {
    if (annexBLocked) return;
    store.setAnnexB((prev) => {
      if (prev.some((r) => sameLabourKey(r.description) === sameLabourKey(line.description))) return prev;
      return [
        ...prev,
        toLabourRow({
          item_no: "",
          description: line.description,
          workshop_est: line.usual_workshop_est ?? 0,
          adjusted_cost: line.usual_price ?? 0,
          justification: null,
          prefilled: line.usual_price != null,
          suggested_from: line.source,
          usual_price: line.usual_price,
        }),
      ];
    });
  };

  const handleDiscardAnnexB = () => {
    if (annexBLocked || !confirm("Discard your unsaved changes to Annex B?")) return;
    store.discard("annexB");
  };

  const handleSaveAnnexB = async () => {
    if (annexBLocked) return;
    store.setStatus("annexB", "saving");
    const res = await saveAnnexBAction(report.id, annexBItems.map(fromLabourRow));
    if ("error" in res) {
      store.setStatus("annexB", "error", res.error);
      return;
    }
    // The API's answer replaces the table: numbered, without blank lines.
    store.applySaved("annexB", res);
    router.refresh();
  };

  // The Summary block uses the tables as last saved -- what the PDF and the
  // workbook print.
  const savedParts = sumRows(store.annexABaseline);
  const savedLabour = sumRows(store.annexBBaseline);
  const savedTotal = { est: Math.round((savedParts.est + savedLabour.est) * 100) / 100, adj: Math.round((savedParts.adj + savedLabour.adj) * 100) / 100 };
  const anyPriced = savedTotal.est > 0 || savedTotal.adj > 0;

  return (
    <div className="card-glass" style={{ padding: 20 }}>
      <div style={{ marginBottom: 14 }}>
        <h4 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Annex B : Adjustment on Labour and Spray Painting</h4>
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
          {plate ? `${plate} — ` : ""}The adjusted cost is yours to set; there is no discount rule on labour.
        </div>
        <LockedNote isSignedOff={isSignedOff} canEdit={canEdit} />
      </div>

      {!readOnly && (
        <LabourLinesPicker reportId={report.id} active={active} existing={annexBItems.map((r) => r.description)} disabled={annexBLocked} onAdd={handleAddUsualLine} />
      )}

      <div className="annex-table-wrapper">
        <table className="annex-table annex-cards">
          <thead>
            <tr>
              <th style={{ width: 45 }}>Item</th>
              <th>Job Description</th>
              <th style={{ textAlign: "right", width: 140 }}>Workshop Est. ($)</th>
              <th style={{ textAlign: "right", width: 140 }}>Adjusted Cost ($)</th>
              {!readOnly && <th style={{ width: 36 }} aria-label="Remove line" />}
            </tr>
          </thead>
          <tbody>
            {annexBItems.length === 0 && (
              <tr>
                <td colSpan={readOnly ? 4 : 5} style={{ textAlign: "center", color: "var(--text-muted)", padding: "18px 10px" }}>
                  {readOnly ? "No labour recorded" : "No labour recorded yet. Add the usual lines above, or add a line below."}
                </td>
              </tr>
            )}
            {annexBItems.map((lab, idx) => {
              const no = idx + 1;
              if (readOnly) {
                return (
                  <tr key={lab._key}>
                    <td data-label="Item" style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{no}</td>
                    <td data-label="Job" style={{ fontWeight: 600, whiteSpace: "pre-wrap" }}>{lab.description}</td>
                    <td data-label="Workshop Est." style={{ textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{lab.workshop_est ? amt(lab.workshop_est) : "—"}</td>
                    <td data-label="Adjusted" style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--accent-cyan)" }}>
                      {lab.workshop_est || lab.adjusted_cost ? amt(lab.adjusted_cost) : "—"}
                    </td>
                  </tr>
                );
              }
              const line = `Labour line ${no}`;
              return (
                <tr key={lab._key}>
                  <td data-label="Item" style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{no}</td>
                  <td data-label="Job" style={{ minWidth: 260 }}>
                    <textarea
                      className="annex-cell-input"
                      style={{ fontWeight: 600, resize: "vertical", minHeight: 32, fontFamily: "inherit" }}
                      rows={1}
                      value={lab.description}
                      placeholder="Job description, e.g. To remove, reinstall tail gate parts"
                      aria-label={`${line}: job description`}
                      disabled={annexBLocked}
                      onChange={(e) => updateLabourRow(lab._key, { description: e.target.value })}
                    />
                    {lab.prefilled && lab.suggested_from ? (
                      <span className="annex-prefill-tag" title="The price was filled from past cases, not typed. Typing a price replaces it.">
                        from {lab.suggested_from}
                      </span>
                    ) : lab.suggested_from ? (
                      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>From {lab.suggested_from}</div>
                    ) : null}
                  </td>
                  <td data-label="Workshop Est." style={{ textAlign: "right" }}>
                    <input
                      type="text"
                      inputMode="decimal"
                      className="annex-cell-input"
                      style={{ width: 110, textAlign: "right", fontFamily: "var(--font-mono)" }}
                      value={lab._est}
                      placeholder="0.00"
                      aria-label={`${line}: workshop estimate`}
                      aria-invalid={isBadAmount(lab._est) ? true : undefined}
                      disabled={annexBLocked}
                      onChange={(e) => updateLabourRow(lab._key, { _est: e.target.value, workshop_est: parseAmount(e.target.value), prefilled: false })}
                    />
                  </td>
                  <td data-label="Adjusted" style={{ textAlign: "right" }}>
                    <input
                      type="text"
                      inputMode="decimal"
                      className="annex-cell-input"
                      style={{ width: 110, textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 700 }}
                      value={lab._adj}
                      placeholder="0.00"
                      aria-label={`${line}: adjusted cost`}
                      aria-invalid={isBadAmount(lab._adj) ? true : undefined}
                      disabled={annexBLocked}
                      onChange={(e) => updateLabourRow(lab._key, { _adj: e.target.value, adjusted_cost: parseAmount(e.target.value), prefilled: false })}
                    />
                    {lab.usual_price != null && (
                      <div
                        title={`What past cases adjusted this line to (median), from ${lab.suggested_from || "past reports"}.`}
                        style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2, cursor: "help", whiteSpace: "nowrap" }}
                      >
                        usual {sgd(lab.usual_price)}
                      </div>
                    )}
                  </td>
                  <td data-label="" style={{ textAlign: "center" }}>
                    <button
                      type="button"
                      className="annex-row-remove"
                      title="Remove this line"
                      aria-label={`Remove labour line ${no}`}
                      disabled={annexBLocked}
                      onClick={() => handleRemoveLabourLine(lab._key)}
                    >
                      <Trash2 style={{ width: 13, height: 13 }} />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={2} style={{ textAlign: "right" }}>
                TOTAL FOR LABOUR &amp; SPRAY PAINTING :
              </td>
              <td data-label="Workshop Est." style={{ textAlign: "right", color: "var(--text-secondary)" }}>{hasPricedLabour ? amt(liveLabour.est) : "—"}</td>
              <td data-label="Adjusted" style={{ textAlign: "right", color: "var(--accent-cyan)", fontSize: 13 }}>{hasPricedLabour ? amt(liveLabour.adj) : "—"}</td>
              {!readOnly && <td></td>}
            </tr>
          </tfoot>
        </table>
      </div>

      {!readOnly && (
        <div className="annex-edit-bar">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap", marginTop: 12 }}>
            <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "6px 12px" }} disabled={annexBLocked} onClick={handleAddLabourLine}>
              <Plus style={{ width: 13, height: 13 }} /> Add line
            </button>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <UnsavedNote state={status.save} dirty={annexBDirty} />
              <SavedNote state={status.save} dirty={annexBDirty} />
              <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "6px 12px" }} disabled={!annexBDirty || annexBLocked} onClick={handleDiscardAnnexB}>
                <RotateCcw style={{ width: 13, height: 13 }} /> Discard changes
              </button>
              <button type="button" className="btn-primary-modern" style={{ fontSize: 11, padding: "6px 14px" }} disabled={!annexBDirty || annexBLocked} onClick={() => void handleSaveAnnexB()}>
                <Save style={{ width: 13, height: 13 }} /> {status.save === "saving" ? "Saving…" : "Save Annex B"}
              </button>
            </div>
          </div>
          {annexBItems.some((r) => !r.description.trim()) && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 8 }}>Lines with no job description are left out when you save.</div>
          )}
          {status.save === "error" && status.error && (
            <div className="setup-error" style={{ marginTop: 10, marginBottom: 0 }}>
              {status.error}
            </div>
          )}
        </div>
      )}

      {/* The Summary printed under the labour table, from the tables as saved. */}
      <div className="annex-summary">
        <table className="annex-table">
          <thead>
            <tr>
              <th>Summary</th>
              <th style={{ textAlign: "right", width: 160 }}>Workshop Est. ($)</th>
              <th style={{ textAlign: "right", width: 160 }}>Adjusted Cost ($)</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>TOTAL FOR SPARE PARTS :</td>
              <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{savedParts.est || savedParts.adj ? amt(savedParts.est) : "—"}</td>
              <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{savedParts.est || savedParts.adj ? amt(savedParts.adj) : "—"}</td>
            </tr>
            <tr>
              <td>TOTAL FOR LABOUR &amp; SPRAY PAINTING :</td>
              <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{savedLabour.est || savedLabour.adj ? amt(savedLabour.est) : "—"}</td>
              <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{savedLabour.est || savedLabour.adj ? amt(savedLabour.adj) : "—"}</td>
            </tr>
          </tbody>
          <tfoot>
            <tr>
              <td>TOTAL REPAIR COSTS :</td>
              <td style={{ textAlign: "right", color: "var(--text-secondary)" }}>{anyPriced ? amt(savedTotal.est) : "—"}</td>
              <td style={{ textAlign: "right", color: "var(--accent-cyan)", fontSize: 13 }}>{anyPriced ? amt(savedTotal.adj) : "—"}</td>
            </tr>
          </tfoot>
        </table>
        <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "8px 12px" }}>
          From Annex A and Annex B as last saved.{store.dirty.annexA || annexBDirty ? " Unsaved changes are not in these figures yet." : ""}
        </div>
      </div>
    </div>
  );
}
