"use client";

import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, BookOpen, ChevronDown, ChevronRight, Clock, ExternalLink, Loader2 } from "lucide-react";
import { getCatalogue } from "@/lib/api";
import { cancelCatalogueLookupAction, decideCatalogueSuggestionAction, requestCatalogueLookupAction } from "@/app/reports/actions";
import {
  CATALOGUE_OFF,
  CATALOGUE_POLL_MS,
  ROWS_SHOWN,
  autoStartedText,
  blockedContext,
  blockedReasonText,
  canDecide,
  dayTime,
  carDetails,
  carLabel,
  failureLines,
  foldVariants,
  hasCatalogueContent,
  hiddenCount,
  hintReading,
  isJobOpen,
  jobStatusView,
  lookupRefusalText,
  matchHintText,
  pageRows,
  partsShown,
  priceLabel,
  priceTitle,
  resultsTime,
  safeUrl,
  sameDiagramText,
  scoreLabel,
  shouldPoll,
  siteLabel,
  splitRows,
  twinWarning,
  variantCountLabel,
  variantNumberLabel,
  withSuggestion,
  workerLabel,
  workerOnline,
  workerSeen,
  type CatalogueBusy,
  type CatalogueDiagram,
  type CataloguePart,
  type CatalogueState,
  type CatalogueSuggestion,
} from "@/lib/catalogue";

// ---------------------------------------------------------------------------
// The case's catalogue look-up: loaded once, polled while a job runs

export type CatalogueController = {
  /** Null until the API first answers (and if it never does: then nothing
   * is shown, as when the look-up is off). */
  state: CatalogueState | null;
  busy: CatalogueBusy;
  /** Rows whose Hide / Undo is on its way. */
  deciding: Record<number, boolean>;
  actionError: string | null;
  /** Starts a look-up: vehicleKey null lets the API pick the car. */
  lookup: (vehicleKey: string | null) => Promise<void>;
  cancel: () => Promise<void>;
  decide: (id: number, action: "reject" | "undo") => Promise<void>;
  reload: () => void;
};

/** GET /reports/{id}/catalogue on mount, then every CATALOGUE_POLL_MS while
 * the case's job is pending or running (and the browser tab is in view),
 * stopping as soon as it isn't. Used by AnnexATab, which shows the button
 * from it and hands it to the panel, so both see one state. `enabled`
 * false (a read-only case) asks nothing. */
export function useCatalogue(reportId: string, enabled: boolean): CatalogueController {
  const [state, setState] = useState<CatalogueState | null>(null);
  const [busy, setBusy] = useState<CatalogueBusy>(null);
  const [deciding, setDeciding] = useState<Record<number, boolean>>({});
  const [actionError, setActionError] = useState<string | null>(null);
  // Bumped by every click, so an answer to a GET sent before it (older
  // than what the click brought back) is dropped.
  const seq = useRef(0);

  const load = useCallback(async () => {
    const started = seq.current;
    try {
      const next = await getCatalogue(reportId);
      if (started === seq.current) setState(next);
    } catch {
      // Keep what is shown; a poll tries again. Never loaded: nothing shows.
    }
  }, [reportId]);

  useEffect(() => {
    if (enabled) void load();
  }, [enabled, load]);

  const polling = enabled && shouldPoll(state);
  useEffect(() => {
    if (!polling) return;
    let stopped = false;
    let timer: number | undefined;
    const tick = async () => {
      if (stopped) return;
      if (document.visibilityState !== "hidden") await load();
      if (!stopped) timer = window.setTimeout(tick, CATALOGUE_POLL_MS);
    };
    timer = window.setTimeout(tick, CATALOGUE_POLL_MS);
    // Back in view: ask at once rather than at the next tick.
    const onVisible = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [polling, load]);

  /** A refusal in the panel's words; 404 means the look-up was turned off
   * meanwhile, so all of it goes. */
  const refused = (res: { error: string; code: string | null; status: number | null; detail?: string | null }) => {
    if (res.status === 404) {
      setState(CATALOGUE_OFF);
      return;
    }
    setActionError(lookupRefusalText(res.code, res.status, state?.enabled ? blockedContext(state) : {}, res.detail) ?? res.error);
  };

  const lookup = async (vehicleKey: string | null) => {
    if (busy) return;
    seq.current += 1;
    setBusy("lookup");
    setActionError(null);
    const res = await requestCatalogueLookupAction(reportId, vehicleKey);
    seq.current += 1;
    setBusy(null);
    if ("error" in res) {
      refused(res);
      // job_open: someone else started one; show it.
      if (res.code === "job_open") void load();
      return;
    }
    setState((s) => (s?.enabled ? { ...s, job: res.job } : s));
    void load();
  };

  const cancel = async () => {
    if (busy) return;
    seq.current += 1;
    setBusy("cancel");
    setActionError(null);
    const res = await cancelCatalogueLookupAction(reportId);
    seq.current += 1;
    setBusy(null);
    if ("error" in res) {
      refused(res);
      return;
    }
    setState((s) => (s?.enabled ? { ...s, job: res.job } : s));
    void load();
  };

  const decide = async (id: number, action: "reject" | "undo") => {
    if (deciding[id]) return;
    seq.current += 1;
    setDeciding((d) => ({ ...d, [id]: true }));
    setActionError(null);
    const res = await decideCatalogueSuggestionAction(reportId, id, action);
    seq.current += 1;
    setDeciding((d) => {
      const next = { ...d };
      delete next[id];
      return next;
    });
    if ("error" in res) {
      refused(res);
      return;
    }
    setState((s) => (s?.enabled ? withSuggestion(s, res.suggestion) : s));
  };

  return { state, busy, deciding, actionError, lookup, cancel, decide, reload: () => void load() };
}

// ---------------------------------------------------------------------------
// The panel

/** "Catalogue parts — reference only", under "Suggested lines" on Annex A:
 * the catalogue car the look-up used, and per damaged part its diagrams
 * and their rows (part number, catalogue name, qty, the site's price), each
 * with which Annex A line it looks like. A diagram an earlier part already
 * shows says so instead of listing its rows again, and a part's colour
 * versions fold into one row ("8 colour versions [show]"). Reference only:
 * nothing here fills W/shop Est. or prints; [Hide] only tidies the list.
 * With renderAttach (AnnexATab, Phase 2) each row gets a tick box and a
 * line to go on, and `footer` holds the [Use N ticked] button that puts
 * them into the Annex A draft. Shows nothing at all while the look-up is
 * off. */
export function CatalogueSuggestionsPanel({
  catalogue,
  tableNames,
  locked,
  onOpenCover,
  renderAttach,
  chosenLabel,
  footer,
}: {
  catalogue: CatalogueController;
  /** Annex A's part descriptions in the order the table shows them, to
   * number the "~ like line 2" hints as on screen. */
  tableNames: string[];
  /** Annex A is saving or filling prices: no new look-up meanwhile. */
  locked: boolean;
  /** Opens the Cover page tab (to confirm the make and model). */
  onOpenCover?: () => void;
  /** The control that puts a row on an Annex A line (catalogue_ref):
   * AnnexATab's tick box and line picker. Without it the rows are a list
   * to read (and the panel tags a row already in Annex A itself). */
  renderAttach?: (row: CatalogueSuggestion, part: CataloguePart, diagram: CatalogueDiagram) => ReactNode;
  /** Where a row of a folded colour group is ("ticked", "on line 3"), for
   * the group's own line: AnnexATab knows the ticks and the draft. */
  chosenLabel?: (row: CatalogueSuggestion) => string | null;
  /** In place of the footer's note: AnnexATab's [Use N ticked]. */
  footer?: ReactNode;
}) {
  const { state } = catalogue;
  const info = state?.enabled ? state : null;
  // Null until the surveyor opens or closes it: then open only when there
  // is something to see.
  const [openChoice, setOpenChoice] = useState<boolean | null>(null);
  const [carsOpen, setCarsOpen] = useState(false);
  const carsId = useId();

  // The clock for "seen 1 min ago": set on every answer, and every 30 s
  // while the panel is open.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => setNow(Date.now()), [state]);
  const open = openChoice ?? (info ? hasCatalogueContent(info) : false);
  const shown = Boolean(info);
  useEffect(() => {
    if (!open || !shown) return;
    const id = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, [open, shown]);

  // A look-up just started (here or by someone else): open to show it.
  const jobOpen = isJobOpen(info?.job);
  useEffect(() => {
    if (jobOpen) setOpenChoice(true);
  }, [jobOpen]);

  if (!info) return null;

  const mayChange = canDecide(info);
  const mayLookUp = info.can_request && !jobOpen && !catalogue.busy && !locked;
  const status = jobStatusView(info.job, info.worker, now);
  const online = workerOnline(info.worker, now);
  const parts = partsShown(info);
  const failures = failureLines(info);
  const hidden = hiddenCount(info);
  const lookedUpAt = resultsTime(info);
  const twin = twinWarning(info.vehicle);
  const hint = info.vehicle_hint;
  const hasResults = Boolean(info.vehicle || parts.length || info.not_found.length);

  const toggle = () => {
    // Opening asks again, so the worker's state isn't an old copy.
    if (!open) catalogue.reload();
    setOpenChoice(!open);
  };

  const pickCar = (key: string) => {
    setCarsOpen(false);
    void catalogue.lookup(key);
  };

  return (
    <div className="annex-suggest-panel catalogue-panel">
      <button type="button" className="annex-suggest-header" onClick={toggle} aria-expanded={open}>
        {open ? <ChevronDown style={{ width: 14, height: 14 }} /> : <ChevronRight style={{ width: 14, height: 14 }} />}
        <BookOpen style={{ width: 14, height: 14, color: "var(--accent-cyan)" }} />
        <span style={{ fontWeight: 700, fontSize: 13 }}>Catalogue parts — reference only</span>
        <span className="catalogue-worker" title={online ? (workerSeen(info.worker, now) ?? undefined) : undefined}>
          <span className={`catalogue-worker-dot${online ? " online" : ""}`} aria-hidden="true" />
          {workerLabel(info.worker, now)}
        </span>
      </button>

      {open && (
        <div style={{ padding: "0 14px 14px" }}>
          {status && (
            <div role="status" className={`catalogue-status ${status.tone}`} title={info.job?.status === "failed" && info.job.error_text ? info.job.error_text : undefined}>
              {status.tone === "busy" ? (
                <Loader2 className="spin" style={{ width: 13, height: 13, flexShrink: 0 }} />
              ) : status.tone === "wait" ? (
                <Clock style={{ width: 13, height: 13, flexShrink: 0 }} />
              ) : status.tone === "fail" ? (
                <AlertTriangle style={{ width: 13, height: 13, flexShrink: 0 }} />
              ) : null}
              <span style={{ flex: "1 1 240px", minWidth: 0 }}>
                {status.text}
                {status.detail && <span style={{ color: "var(--text-muted)" }}> {status.detail}</span>}
              </span>
              {jobOpen && mayChange && (
                <button
                  type="button"
                  className="btn-secondary-modern"
                  style={{ fontSize: 11, padding: "3px 10px" }}
                  disabled={catalogue.busy !== null}
                  onClick={() => void catalogue.cancel()}
                  aria-label="Cancel the catalogue look-up"
                >
                  {catalogue.busy === "cancel" ? "Cancelling…" : "Cancel"}
                </button>
              )}
            </div>
          )}

          {autoStartedText(info.job) && (
            <div className="catalogue-muted" style={{ marginTop: 6 }}>
              {autoStartedText(info.job)}
              {info.job?.created_at ? ` · ${dayTime(info.job.created_at, now)}` : ""}
            </div>
          )}

          {catalogue.actionError && (
            <div className="setup-error" role="alert" style={{ margin: "8px 0 0" }}>
              {catalogue.actionError}
            </div>
          )}

          {/* The look-up goes by the case's make and model: say so while
              they are still the AI's reading. */}
          {hint && !hint.confirmed && hintReading(hint) && (
            <div className="catalogue-note">
              <AlertTriangle style={{ width: 12, height: 12, flexShrink: 0 }} />
              <span>Make and model are still the AI&apos;s reading ({hintReading(hint)}).</span>
              {onOpenCover && (
                <button type="button" className="annex-stale-btn" onClick={onOpenCover}>
                  Confirm on Cover page
                </button>
              )}
            </div>
          )}

          {!info.can_request && !jobOpen && (
            <div className="catalogue-muted" style={{ marginTop: 8 }}>
              {blockedReasonText(info.blocked_reason, blockedContext(info))}
              {info.blocked_reason === "no_make_model" && onOpenCover && (
                <>
                  {" "}
                  <button type="button" className="catalogue-text-btn" onClick={onOpenCover}>
                    Open the Cover page
                  </button>
                </>
              )}
            </div>
          )}

          {/* The catalogue car, and the others it could have been. */}
          {(info.vehicle || info.candidates.length > 0) && (
            <div className="catalogue-car">
              <div style={{ flex: "1 1 260px", minWidth: 0 }}>
                {info.vehicle ? (
                  <>
                    <strong>{carLabel(info.vehicle)}</strong>
                    {carDetails(info.vehicle, lookedUpAt, now).map((d) => (
                      <span key={d} style={{ color: "var(--text-muted)" }}>
                        {" · "}
                        {d}
                      </span>
                    ))}
                  </>
                ) : (
                  <span style={{ color: "var(--text-muted)" }}>No catalogue car picked for this case.</span>
                )}
              </div>
              {mayChange && info.candidates.length > 0 && (
                <button
                  type="button"
                  className="btn-secondary-modern"
                  style={{ fontSize: 11, padding: "3px 10px" }}
                  aria-expanded={carsOpen}
                  aria-controls={carsId}
                  disabled={!mayLookUp}
                  title={!info.can_request ? blockedReasonText(info.blocked_reason, blockedContext(info)) : "Look up again with another catalogue car"}
                  onClick={() => setCarsOpen((o) => !o)}
                >
                  {info.vehicle ? "Change car" : "Pick a car"} <ChevronDown style={{ width: 12, height: 12 }} />
                </button>
              )}
            </div>
          )}
          {twin && (
            <div className="catalogue-note">
              <AlertTriangle style={{ width: 12, height: 12, flexShrink: 0 }} />
              <span>{twin}</span>
            </div>
          )}
          {carsOpen && mayLookUp && (
            <ul id={carsId} className="catalogue-cars" aria-label="Catalogue cars to look up with">
              {info.candidates.map((v) => {
                const current = v.key === info.vehicle?.key;
                return (
                  <li key={v.key}>
                    <button type="button" className="catalogue-car-option" disabled={current} onClick={() => pickCar(v.key)}>
                      <strong>{carLabel(v)}</strong>
                      <span style={{ color: "var(--text-muted)" }}> · {carDetails(v, null, now).join(" · ")}</span>
                      {v.twin_of && <span className="annex-suggest-tag">twin of {v.twin_of}</span>}
                      {current && <span className="annex-suggest-tag">in use</span>}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          {parts.map((part) => (
            <div key={part.source_part} style={{ marginTop: 14 }}>
              <div className="catalogue-part-head">{part.source_part}</div>
              {part.diagrams.map((diagram) => (
                <DiagramBlock
                  key={diagram.diagram_key}
                  diagram={diagram}
                  tableNames={tableNames}
                  now={now}
                  mayChange={mayChange}
                  deciding={catalogue.deciding}
                  onDecide={(id, action) => void catalogue.decide(id, action)}
                  renderAttach={renderAttach ? (row) => renderAttach(row, part, diagram) : undefined}
                  chosenLabel={chosenLabel}
                />
              ))}
            </div>
          ))}

          {info.not_found.length > 0 && (
            <div style={{ fontSize: 12, marginTop: 12 }}>
              Not in catalogues: <strong>{info.not_found.join(", ")}</strong>
            </div>
          )}

          {failures.length > 0 && (
            <ul className="catalogue-failures">
              {failures.map((text) => (
                <li key={text}>
                  <AlertTriangle style={{ width: 12, height: 12, flexShrink: 0, marginTop: 2 }} />
                  <span>{text}</span>
                </li>
              ))}
            </ul>
          )}

          {!info.job && !hasResults && (
            <div className="catalogue-muted" style={{ marginTop: 6 }}>
              Not looked up yet. <strong>Look up in catalogue</strong> (above the table) finds the damaged parts in the makers&apos; parts catalogues:
              part numbers, diagrams and the sites&apos; prices, as a reference.
            </div>
          )}
          {info.job?.status === "done" && !hasResults && failures.length === 0 && (
            <div className="catalogue-muted" style={{ marginTop: 6 }}>
              The look-up found nothing for this car.
            </div>
          )}

          {hasResults && (
            <div className="annex-suggest-footer" style={{ fontSize: 11, color: "var(--text-muted)" }}>
              {footer ?? <span>Reference prices from the parts sites, not the workshop&apos;s: they never fill W/shop Est. and never print.</span>}
              {hidden > 0 && (
                <span>
                  {hidden} row{hidden === 1 ? "" : "s"} hidden on this case
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** One diagram of a damaged part: its name, match and link, the first
 * ROWS_SHOWN rows (+ N more; a colour group counts as one), and the rows
 * the surveyor hid, folded away. A diagram an earlier part already shows
 * (same_as_part) says so, with only its rows not listed there. */
function DiagramBlock({
  diagram,
  tableNames,
  now,
  mayChange,
  deciding,
  onDecide,
  renderAttach,
  chosenLabel,
}: {
  diagram: CatalogueDiagram;
  tableNames: string[];
  now: number;
  mayChange: boolean;
  deciding: Record<number, boolean>;
  onDecide: (id: number, action: "reject" | "undo") => void;
  renderAttach?: (row: CatalogueSuggestion) => ReactNode;
  chosenLabel?: (row: CatalogueSuggestion) => string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const { shown, hidden } = splitRows(diagram.rows);
  const units = foldVariants(shown);
  const page = pageRows(units, expanded);
  const sameAs = diagram.same_as_part || null;
  const name = diagram.diagram_name || "unnamed diagram";
  const href = safeUrl(diagram.diagram_url);
  const site = siteLabel(diagram.rows[0]?.source ?? null, diagram.diagram_url);
  const score = scoreLabel(diagram.match_score);

  const row = (r: CatalogueSuggestion, nested = false) => (
    <CatalogueRowLine
      key={r.id}
      row={r}
      diagramUrl={diagram.diagram_url}
      tableNames={tableNames}
      now={now}
      mayChange={mayChange}
      busy={Boolean(deciding[r.id])}
      onHide={() => onDecide(r.id, "reject")}
      onUndo={() => onDecide(r.id, "undo")}
      attach={renderAttach?.(r)}
      nested={nested}
    />
  );

  return (
    <div style={{ marginTop: 6 }}>
      <div className="catalogue-diagram-head">
        <span>
          diagram: <strong style={{ color: "var(--text-secondary)" }}>{name}</strong>
        </span>
        {score && <span>{score}</span>}
        {href && (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="setup-help-link"
            style={{ fontSize: 11, marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 3 }}
            aria-label={`Open the diagram ${name}${site ? ` on ${site}` : ""} (new tab)`}
          >
            open <ExternalLink style={{ width: 11, height: 11 }} />
          </a>
        )}
      </div>

      {sameAs && <div className="catalogue-muted catalogue-same-diagram">{sameDiagramText(sameAs, shown.length + hidden.length)}</div>}
      {!sameAs && shown.length === 0 && hidden.length === 0 && (
        <div className="catalogue-muted" style={{ padding: "2px 8px" }}>
          No parts listed on this diagram.
        </div>
      )}
      {page.rows.map((unit) =>
        unit.kind === "row" ? (
          row(unit.row)
        ) : (
          <VariantGroup
            key={`v:${unit.key}`}
            rows={unit.rows}
            diagramUrl={diagram.diagram_url}
            tableNames={tableNames}
            now={now}
            chosenLabel={chosenLabel}
            renderRow={(r) => row(r, true)}
          />
        )
      )}

      {(page.more > 0 || (expanded && units.length > ROWS_SHOWN) || hidden.length > 0) && (
        <div className="catalogue-diagram-foot">
          {page.more > 0 && (
            <button type="button" className="catalogue-text-btn" onClick={() => setExpanded(true)}>
              + {page.more} more row{page.more === 1 ? "" : "s"} on this diagram
            </button>
          )}
          {expanded && units.length > ROWS_SHOWN && (
            <button type="button" className="catalogue-text-btn" onClick={() => setExpanded(false)}>
              Show the first {ROWS_SHOWN} only
            </button>
          )}
          {hidden.length > 0 && (
            <span>
              {hidden.length} hidden{" "}
              <button type="button" className="catalogue-text-btn" aria-expanded={showHidden} onClick={() => setShowHidden((s) => !s)}>
                {showHidden ? "close" : "show"}
              </button>
            </span>
          )}
        </div>
      )}
      {showHidden && hidden.map((r) => row(r))}
    </div>
  );
}

/** A part's colour versions on one diagram (the API's variant_key): one
 * line -- callout, the numbers they share, the name, qty, price, "8 colour
 * versions [show]" and which one is ticked or on a line -- and, opened, each
 * version with its own tick box (AnnexATab lets one per group be ticked). */
function VariantGroup({
  rows,
  diagramUrl,
  tableNames,
  now,
  chosenLabel,
  renderRow,
}: {
  rows: CatalogueSuggestion[];
  diagramUrl: string | null;
  tableNames: string[];
  now: number;
  chosenLabel?: (row: CatalogueSuggestion) => string | null;
  renderRow: (row: CatalogueSuggestion) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  const first = rows[0];
  const site = siteLabel(first.source, first.url, diagramUrl);
  const hint = matchHintText(first.match_hint, tableNames);
  const chosen = rows
    .map((r) => {
      const where = chosenLabel?.(r) ?? (r.status === "accepted" ? "in Annex A" : null);
      return where ? `${r.part_number || "a version"} ${where}` : null;
    })
    .filter((x): x is string => Boolean(x));
  return (
    <>
      <div className="catalogue-row catalogue-variants">
        <span className="catalogue-callout" title="Number on the diagram">
          {first.callout || "–"}
        </span>
        <span className="catalogue-partno">{variantNumberLabel(rows)}</span>
        <span className="catalogue-name">
          {first.name || "—"}{" "}
          <span className="annex-suggest-tag" style={{ marginLeft: 4 }}>
            {variantCountLabel(rows.length)}
          </span>{" "}
          <button type="button" className="catalogue-text-btn" aria-expanded={open} aria-controls={listId} onClick={() => setOpen((o) => !o)}>
            {open ? "hide" : "show"}
          </button>
          {chosen.map((c) => (
            <span key={c} className="annex-suggest-tag">
              {c}
            </span>
          ))}
        </span>
        <span className="catalogue-row-end">
          <span className="catalogue-qty">qty {first.qty || "—"}</span>
          <span className="catalogue-price" title={priceTitle(first, now, site)}>
            <span style={{ color: first.ref_amount != null ? "var(--text-secondary)" : "var(--text-muted)" }}>{priceLabel(first)}</span>
            {site ? ` · ${site}` : ""}
          </span>
        </span>
        <div className="catalogue-row-hint">
          {hint ? `${hint} · ` : ""}
          {open ? "Tick the colour on the car." : "The same part in other colours: show them to pick the one on the car."}
        </div>
      </div>
      {open && (
        <div id={listId} role="group" aria-label={`${variantCountLabel(rows.length)} of ${first.name || "this part"}`}>
          {rows.map((r) => renderRow(r))}
        </div>
      )}
    </>
  );
}

/** One catalogue row: callout, part number, the catalogue's name, qty, the
 * site's price, and which Annex A line it looks like.
 *
 * `attach` is the control for using the row on an Annex A line
 * (catalogue_ref, saved with PUT /annex-a): AnnexATab's tick box and line
 * picker, or where the row already is. */
function CatalogueRowLine({
  row,
  diagramUrl,
  tableNames,
  now,
  mayChange,
  busy,
  onHide,
  onUndo,
  attach,
  nested = false,
}: {
  row: CatalogueSuggestion;
  /** Names the site for a row with no link of its own. */
  diagramUrl: string | null;
  tableNames: string[];
  now: number;
  mayChange: boolean;
  busy: boolean;
  onHide: () => void;
  onUndo: () => void;
  attach?: ReactNode;
  /** One version of an opened colour group: indented under it. */
  nested?: boolean;
}) {
  const isHidden = row.status === "rejected";
  const hint = isHidden ? null : matchHintText(row.match_hint, tableNames);
  const site = siteLabel(row.source, row.url, diagramUrl);
  const href = safeUrl(row.url);
  const what = row.part_number || row.name || "this row";
  return (
    <div className={`catalogue-row${isHidden ? " hidden-row" : ""}${nested ? " variant-member" : ""}`}>
      <span className="catalogue-callout" title="Number on the diagram">
        {row.callout || "–"}
      </span>
      <span className="catalogue-partno">{row.part_number || "no part no."}</span>
      <span className="catalogue-name">
        {row.name || "—"}
        {row.superseded_from && (
          <span className="annex-suggest-tag" title={`This part number replaces ${row.superseded_from}`}>
            replaces {row.superseded_from}
          </span>
        )}
        {/* With an attach control, it says where the row is (the draft may
            differ from what is saved). */}
        {row.status === "accepted" && attach === undefined && <span className="annex-suggest-tag">in Annex A</span>}
      </span>
      <span className="catalogue-row-end">
        <span className="catalogue-qty">qty {row.qty || "—"}</span>
        <span className="catalogue-price" title={priceTitle(row, now, site)}>
          <span style={{ color: row.ref_amount != null ? "var(--text-secondary)" : "var(--text-muted)" }}>{priceLabel(row)}</span>
          {site && (
            <>
              {" · "}
              {href ? (
                <a href={href} target="_blank" rel="noopener noreferrer" className="setup-help-link" style={{ fontSize: 11, fontWeight: 500 }} aria-label={`${what} on ${site} (new tab)`}>
                  {site}
                </a>
              ) : (
                site
              )}
            </>
          )}
          {row.stale && <span className="annex-suggest-tag">old price</span>}
        </span>
        {attach}
        {/* An accepted row is its Annex A line's: the API refuses Hide on it
            (remove or unlink the line instead). */}
        {mayChange && row.status !== "accepted" && (
          <button
            type="button"
            className="annex-stale-btn catalogue-row-btn"
            disabled={busy}
            onClick={isHidden ? onUndo : onHide}
            aria-label={isHidden ? `Undo hiding ${what}` : `Hide ${what}`}
            title={isHidden ? "Show this row again" : "Hide this row from the list (it stays hidden on later look-ups)"}
          >
            {isHidden ? "Undo" : "Hide"}
          </button>
        )}
      </span>
      {(hint || row.notes) && (
        <div className="catalogue-row-hint">
          {hint}
          {hint && row.notes ? " · " : ""}
          {row.notes}
        </div>
      )}
    </div>
  );
}
