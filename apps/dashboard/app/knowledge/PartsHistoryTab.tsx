"use client";

import { useRef, useState } from "react";
import { Download, FileSpreadsheet, History, Lock, Search, Trash2, Upload } from "lucide-react";
import {
  importPastReports,
  MAX_PAST_REPORT_FILES,
  type PartsHistory,
  type PartsHistoryLine,
  type PastReportCase,
  type PastReportImportResult,
} from "@/lib/api";
import { isAdmin, useCurrentUser } from "@/components/CurrentUser";
import { formatSgd, shortIsoDate } from "@/lib/pastReports";
import { deletePastReportAction } from "./actions";

/** The API lists at most this many lines (GET /parts-history). */
const API_LINE_LIMIT = 1000;

const selectStyle: React.CSSProperties = {
  padding: "6px 10px",
  borderRadius: 6,
  border: "1px solid var(--border-color)",
  background: "var(--surface-card)",
  color: "var(--text-primary)",
  fontSize: 11,
  outline: "none",
};

function uniqueSorted(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

const vehicleOf = (x: { make: string; model: string }) => [x.make, x.model].filter(Boolean).join(" ") || "—";

/** CSV of the lines shown. Text cells that a spreadsheet would read as a
 * formula are prefixed with an apostrophe. */
function downloadCsv(rows: PartsHistoryLine[]) {
  const cell = (v: string | number | null | undefined) => {
    if (v == null) return "";
    let s = String(v);
    if (typeof v === "string" && /^[=+\-@]/.test(s)) s = `'${s}`;
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const header = ["Item", "Part family", "Make", "Model", "Cases", "Usual unit price (SGD)", "Lowest (SGD)", "Highest (SGD)", "Fitting", "Last seen"];
  const body = rows.map((r) => [
    r.item,
    r.family,
    r.make,
    r.model,
    r.cases,
    r.usual_unit_price,
    r.min_price,
    r.max_price,
    r.fitting ? "yes" : "",
    r.last_seen,
  ]);
  const csv = [header, ...body].map((r) => r.map(cell).join(",")).join("\r\n");
  // The BOM makes Excel read it as UTF-8.
  const url = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `carlink_parts_price_history_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** "Parts price history" on the Knowledge page: the past reports imported
 * (Annex A lines of the firm's own spreadsheets) and, per line wording and
 * vehicle, what one unit was usually adjusted to. The same history feeds
 * the "Suggested lines" panel on a case's Annex A. */
export function PartsHistoryTab({
  history,
  loadError,
  onReload,
}: {
  history: PartsHistory | null;
  loadError: string | null;
  onReload: () => Promise<void>;
}) {
  const user = useCurrentUser();
  const admin = isAdmin(user);
  const fileInput = useRef<HTMLInputElement>(null);

  const [search, setSearch] = useState("");
  const [make, setMake] = useState("");
  const [model, setModel] = useState("");
  const [reloading, setReloading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importResults, setImportResults] = useState<PastReportImportResult[] | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);

  const cases = history?.cases ?? [];
  const lines = history?.lines ?? [];
  const totalCaseLines = cases.reduce((n, c) => n + c.lines, 0);

  const makes = uniqueSorted(lines.map((l) => l.make));
  const models = uniqueSorted(lines.filter((l) => !make || l.make === make).map((l) => l.model));
  const needle = search.trim().toLowerCase();
  const rows = lines.filter(
    (l) =>
      (!make || l.make === make) &&
      (!model || l.model === model) &&
      (!needle || l.item.toLowerCase().includes(needle) || (l.family || "").toLowerCase().includes(needle))
  );

  async function reload() {
    setReloading(true);
    await onReload();
    setReloading(false);
  }

  async function handleFiles(files: File[]) {
    if (files.length === 0) return;
    setImportResults(null);
    setImportError(null);
    if (files.length > MAX_PAST_REPORT_FILES) {
      setImportError(`At most ${MAX_PAST_REPORT_FILES} files at a time (${files.length} were picked).`);
      return;
    }
    setImporting(true);
    try {
      const { results } = await importPastReports(files);
      setImportResults(results);
      if (results.some((r) => !("error" in r))) await onReload();
    } catch (err) {
      setImportError(
        err instanceof TypeError
          ? "Couldn't reach the server. Check the connection and try again."
          : err instanceof Error
            ? err.message
            : "Failed to import the reports"
      );
    } finally {
      setImporting(false);
    }
  }

  async function handleRemove(c: PastReportCase) {
    const what = `${c.ref}${c.make || c.model ? ` (${vehicleOf(c)})` : ""}`;
    if (!confirm(`Remove ${what}? Its ${c.lines} line${c.lines === 1 ? "" : "s"} stop counting towards usual prices and suggested lines. The spreadsheet can be imported again later.`)) {
      return;
    }
    setRemoving(c.ref);
    setRemoveError(null);
    const res = await deletePastReportAction(c.ref);
    if ("error" in res) {
      setRemoveError(res.error);
    } else {
      await onReload();
    }
    setRemoving(null);
  }

  const imported = importResults?.filter((r): r is Extract<PastReportImportResult, { lines: number }> => !("error" in r)) ?? [];
  const failed = importResults?.filter((r): r is Extract<PastReportImportResult, { error: string }> => "error" in r) ?? [];

  return (
    <div className="parts-history-tab" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Imported past reports */}
      <div className="card-glass">
        <div className="card-header" style={{ alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
          <div style={{ flex: "1 1 320px" }}>
            <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <History style={{ width: 16, height: 16, color: "var(--accent-primary)" }} /> Parts price history
            </div>
            <div className="card-subtitle">
              Usual prices are what past cases&apos; lines were adjusted to (median per unit). Import past report spreadsheets to grow this list.
            </div>
          </div>
          {admin && (
            <>
              <input
                ref={fileInput}
                type="file"
                accept=".xls,.xlsx,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                multiple
                hidden
                onChange={(e) => {
                  // Copied before clearing: the same files can then be picked again.
                  const files = Array.from(e.target.files ?? []);
                  e.target.value = "";
                  void handleFiles(files);
                }}
              />
              <button
                type="button"
                className="btn-primary-modern"
                style={{ fontSize: 12, padding: "7px 14px" }}
                disabled={importing}
                onClick={() => fileInput.current?.click()}
                title={`One report per file, .xls or .xlsx, up to ${MAX_PAST_REPORT_FILES} at a time. A report imported again replaces its earlier lines.`}
              >
                <Upload style={{ width: 14, height: 14 }} /> {importing ? "Importing…" : "Import past reports"}
              </button>
            </>
          )}
        </div>

        {importError && <div className="setup-error">{importError}</div>}

        {importResults && (
          <div className="parts-import-results">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginBottom: 6 }}>
              <strong style={{ fontSize: 12 }}>
                {imported.length} imported{failed.length > 0 ? `, ${failed.length} not imported` : ""}
              </strong>
              <button type="button" className="setup-help-link" style={{ background: "none", border: "none", cursor: "pointer" }} onClick={() => setImportResults(null)}>
                Dismiss
              </button>
            </div>
            <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 3 }}>
              {importResults.map((r, i) => (
                <li key={`${r.file}-${i}`} style={{ fontSize: 12 }}>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{r.file}</span>
                  {" — "}
                  {"error" in r ? (
                    <span style={{ color: "var(--badge-red-text)" }}>{r.error}</span>
                  ) : (
                    <span style={{ color: "var(--badge-green-text)" }}>
                      {r.ref}: imported {r.lines} line{r.lines === 1 ? "" : "s"}
                      {r.make || r.model ? ` (${vehicleOf(r)})` : ""}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {loadError ? (
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <div className="setup-error" style={{ margin: 0 }}>{loadError}</div>
            <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} disabled={reloading} onClick={() => void reload()}>
              {reloading ? "Loading…" : "Try again"}
            </button>
          </div>
        ) : history?.hidden ? (
          // GET /parts-history hides prices from accounts that aren't staff.
          <div style={{ textAlign: "center", padding: "28px 12px", color: "var(--text-muted)" }}>
            <Lock style={{ width: 24, height: 24, marginBottom: 8 }} />
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>Price history is available to staff only.</div>
          </div>
        ) : cases.length === 0 ? (
          <div style={{ textAlign: "center", padding: "28px 12px", color: "var(--text-muted)" }}>
            <FileSpreadsheet style={{ width: 28, height: 28, marginBottom: 8 }} />
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>No past reports imported yet</div>
            <div style={{ fontSize: 12, marginTop: 4, maxWidth: 520, marginInline: "auto", lineHeight: 1.6 }}>
              {admin
                ? "Import past report spreadsheets (.xls or .xlsx, one report per file). Their Annex A lines give the usual prices here and the suggested lines on a case's Annex A."
                : "An admin can import past report spreadsheets. Their Annex A lines give the usual prices here and the suggested lines on a case's Annex A."}
            </div>
          </div>
        ) : (
          <>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, margin: "4px 0 8px" }}>
              <span style={{ fontSize: 13, fontWeight: 700 }}>Imported reports</span>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                {cases.length} report{cases.length === 1 ? "" : "s"}, {totalCaseLines} Annex A line{totalCaseLines === 1 ? "" : "s"}
              </span>
            </div>
            {removeError && <div className="setup-error">{removeError}</div>}
            <div style={{ maxHeight: 320, overflowY: "auto", overflowX: "auto" }}>
              <table className="damage-table-modern">
                <thead>
                  <tr>
                    <th>Ref</th>
                    <th>Vehicle</th>
                    {admin && <th>Plate</th>}
                    <th>Surveyed</th>
                    <th style={{ textAlign: "right" }}>Lines</th>
                    {admin && <th aria-label="Remove" />}
                  </tr>
                </thead>
                <tbody>
                  {cases.map((c) => (
                    <tr key={c.ref}>
                      <td style={{ fontFamily: "var(--font-mono)", fontWeight: 700, whiteSpace: "nowrap" }} title={c.file || undefined}>
                        {c.ref}
                      </td>
                      <td>{vehicleOf(c)}</td>
                      {admin && <td style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{c.plate || "—"}</td>}
                      <td style={{ whiteSpace: "nowrap" }}>{shortIsoDate(c.surveyed) || "—"}</td>
                      <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{c.lines}</td>
                      {admin && (
                        <td style={{ textAlign: "right" }}>
                          <button
                            type="button"
                            className="btn-secondary-modern"
                            style={{ fontSize: 11, padding: "4px 10px", color: "var(--danger, #ef4444)" }}
                            disabled={removing !== null || importing}
                            onClick={() => void handleRemove(c)}
                          >
                            <Trash2 style={{ width: 12, height: 12 }} /> {removing === c.ref ? "Removing…" : "Remove"}
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      {/* Usual prices per line */}
      {lines.length > 0 && (
        <div className="card-glass">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <div style={{ position: "relative", width: 220 }}>
                <Search style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", width: 13, height: 13, color: "var(--text-muted)" }} />
                <input
                  type="text"
                  className="settings-input"
                  style={{ marginTop: 0, paddingLeft: 28, fontSize: 11 }}
                  placeholder="Search item or part family…"
                  aria-label="Search the parts price history"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <select
                value={make}
                aria-label="Filter by make"
                onChange={(e) => {
                  const next = e.target.value;
                  setMake(next);
                  // Keep the model only if that make has it.
                  if (model && !lines.some((l) => (!next || l.make === next) && l.model === model)) setModel("");
                }}
                style={selectStyle}
              >
                <option value="">All makes</option>
                {makes.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
              <select value={model} aria-label="Filter by model" onChange={(e) => setModel(e.target.value)} style={selectStyle}>
                <option value="">All models</option>
                {models.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                {rows.length} of {lines.length} line{lines.length === 1 ? "" : "s"}
              </span>
            </div>
            <button
              type="button"
              className="btn-secondary-modern"
              style={{ fontSize: 11, padding: "6px 12px" }}
              disabled={rows.length === 0}
              onClick={() => downloadCsv(rows)}
              title="Download the lines shown as a CSV file"
            >
              <Download style={{ width: 13, height: 13 }} /> Export CSV
            </button>
          </div>

          {lines.length >= API_LINE_LIMIT && (
            <p className="setup-note">
              Only the first {API_LINE_LIMIT.toLocaleString("en-SG")} lines are listed, so a search here may miss some.
            </p>
          )}

          {rows.length === 0 ? (
            <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>No lines match these filters.</p>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table className="damage-table-modern">
                <thead>
                  <tr>
                    <th>Item</th>
                    <th>Part family</th>
                    <th>Make / model</th>
                    <th style={{ textAlign: "right" }}>Cases</th>
                    <th style={{ textAlign: "right" }}>Usual price</th>
                    <th style={{ textAlign: "right" }}>Range</th>
                    <th>Last seen</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((l, i) => (
                    <tr key={`${l.make}|${l.model}|${l.item}|${i}`}>
                      <td>
                        <strong>{l.item}</strong>
                        {l.fitting && (
                          <span className="annex-suggest-tag" title="A fitting renewed on refit (clips, seals and the like)">
                            fitting
                          </span>
                        )}
                      </td>
                      <td style={{ color: l.family ? undefined : "var(--text-muted)" }}>{l.family || "—"}</td>
                      <td>{vehicleOf(l)}</td>
                      <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{l.cases}</td>
                      <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", whiteSpace: "nowrap" }}>
                        {l.usual_unit_price != null ? (
                          <>
                            {formatSgd(l.usual_unit_price)}
                            <span style={{ fontSize: 10, color: "var(--text-muted)" }}> /unit</span>
                          </>
                        ) : (
                          <span style={{ color: "var(--text-muted)" }}>no price</span>
                        )}
                      </td>
                      <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 11, whiteSpace: "nowrap", color: "var(--text-muted)" }}>
                        {l.min_price != null && l.max_price != null && l.min_price !== l.max_price
                          ? `${formatSgd(l.min_price)} – ${formatSgd(l.max_price)}`
                          : "—"}
                      </td>
                      <td style={{ whiteSpace: "nowrap" }}>{shortIsoDate(l.last_seen) || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
