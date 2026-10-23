"use client";

import { useEffect, useRef, useState } from "react";
import { Bot, ChevronDown, ChevronRight, Lightbulb, Plus } from "lucide-react";
import type { AnnexAPartItem, AnnexSuggestionGroup, AnnexSuggestionLine, AnnexSuggestions } from "@/lib/api";
import { askAiAnnexSuggestionsAction, getAnnexSuggestionsAction } from "@/app/reports/actions";
import { isAdmin, useCurrentUser } from "@/components/CurrentUser";
import { formatSgd as money, sameLineKey, shortIsoDate as shortDate } from "@/lib/pastReports";

const fmtQty = (q: number) => String(Math.round((q || 1) * 100) / 100);

/** "2 past Honda Vezel cases", "1 past Honda case", "3 imported cases". */
function casesPhrase(n: number, scope: string): string {
  const noun = scope.replace(/^all /, "");
  return `${n} ${n === 1 ? noun.replace(/cases$/, "case") : noun}`;
}

/** "From 2 past Honda Vezel cases", "From all 5 imported cases". */
function scopeLine(n: number, scope: string): string {
  if (scope.startsWith("all ")) {
    const noun = scope.slice(4);
    return n === 1 ? `From the one ${noun.replace(/cases$/, "case")}` : `From all ${n} ${noun}`;
  }
  return `From ${casesPhrase(n, scope)}`;
}

function reasonLabel(reason: string): string {
  if (reason === "damaged") return "Damaged";
  return reason.charAt(0).toUpperCase() + reason.slice(1);
}

type Source = "history" | "ai";

const lineId = (source: Source, group: AnnexSuggestionGroup, line: AnnexSuggestionLine) =>
  `${source}|${group.part}|${sameLineKey(line.name)}`;

/** Ticked to start with: lines for a damaged part that were in at least
 * half of the past cases with that part -- the most-seen line of the
 * group stands for that count. Companions and AI lines start unticked. */
function defaultTicks(groups: AnnexSuggestionGroup[]): Record<string, boolean> {
  const ticks: Record<string, boolean> = {};
  for (const group of groups) {
    if (group.reason !== "damaged") continue;
    const most = Math.max(0, ...group.lines.map((l) => l.cases || 0));
    for (const line of group.lines) {
      if (most > 0 && (line.cases || 0) * 2 >= most) ticks[lineId("history", group, line)] = true;
    }
  }
  return ticks;
}

function priceTitle(line: AnnexSuggestionLine): string {
  const parts: string[] = [];
  if (line.usual_unit_price != null) {
    parts.push("Median of what past lines were adjusted to, per unit.");
    if (line.price_range) parts.push(`Range ${money(line.price_range[0])} to ${money(line.price_range[1])}.`);
  }
  if (line.cases) parts.push(`In ${line.cases} past case${line.cases === 1 ? "" : "s"}.`);
  const last = shortDate(line.last_seen);
  if (last) parts.push(`Last seen ${last}.`);
  if (line.conditions?.length) parts.push(`Condition: ${line.conditions.join(", ")}.`);
  return parts.join(" ");
}

/** "Suggested lines" above the Annex A table: what past reports listed for
 * this case's damage, with what each line usually cost. Nothing goes into
 * the table until the surveyor ticks lines and adds them, and nothing is
 * saved until they save Annex A. Lines already in the table are hidden, so
 * a line comes back here if it is removed from the table again. */
export function AnnexSuggestionsPanel({
  reportId,
  existingNames,
  onAdd,
}: {
  reportId: string;
  /** Part descriptions currently in the table (saved or not). */
  existingNames: string[];
  onAdd: (items: AnnexAPartItem[]) => void;
}) {
  const user = useCurrentUser();
  const [open, setOpen] = useState(true);
  const [loadState, setLoadState] = useState<"idle" | "loading" | "loaded" | "error">("idle");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [data, setData] = useState<AnnexSuggestions | null>(null);
  const [ticked, setTicked] = useState<Record<string, boolean>>({});
  const [aiGroups, setAiGroups] = useState<AnnexSuggestionGroup[] | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);

  async function load() {
    setLoadState("loading");
    setLoadError(null);
    const res = await getAnnexSuggestionsAction(reportId);
    if ("error" in res) {
      setLoadError(res.error);
      setLoadState("error");
      return;
    }
    setData(res);
    setTicked(defaultTicks(res.groups));
    setLoadState("loaded");
    // Open by default only when there is something to pick from.
    if (res.groups.length === 0 && res.without_history.length === 0) setOpen(false);
  }

  // Fetched on first open, once (the panel starts open).
  const requested = useRef(false);
  useEffect(() => {
    if (!open || requested.current) return;
    requested.current = true;
    void load();
  }, [open]);

  async function askAi() {
    if (!data || aiBusy) return;
    setAiBusy(true);
    setAiError(null);
    const res = await askAiAnnexSuggestionsAction(reportId, data.without_history.slice(0, 40));
    setAiBusy(false);
    if ("error" in res) {
      setAiError(res.error);
      return;
    }
    setAiGroups(res.groups);
  }

  const inTable = new Set(existingNames.map(sameLineKey).filter(Boolean));
  const shown = (groups: AnnexSuggestionGroup[], source: Source) =>
    groups
      .map((group) => ({ group, source, lines: group.lines.filter((l) => !inTable.has(sameLineKey(l.name))) }))
      .filter((g) => g.lines.length > 0);

  const historyGroups = data ? shown(data.groups, "history") : [];
  const aiShown = aiGroups ? shown(aiGroups, "ai") : [];
  const allShown = [...historyGroups, ...aiShown];
  const selected = allShown.flatMap(({ group, source, lines }) =>
    lines.filter((l) => ticked[lineId(source, group, l)]).map((line) => ({ group, source, line }))
  );
  // The same wording ticked under two parts is added once.
  const selectedCount = new Set(selected.map((s) => sameLineKey(s.line.name))).size;

  function handleAdd() {
    if (!data || selected.length === 0) return;
    const items: AnnexAPartItem[] = [];
    const seen = new Set<string>();
    for (const { source, line } of selected) {
      const key = sameLineKey(line.name);
      if (seen.has(key)) continue;
      seen.add(key);
      const nett = Boolean(line.nett);
      items.push({
        item_no: "",
        part_name: line.name,
        condition: line.kind === "fitting" ? "Necessary" : "",
        action: "Replace",
        qty: line.qty || 1,
        // The workshop's estimate is the surveyor's to enter; the usual
        // price is kept beside it as a reference only.
        workshop_est: 0,
        discount_pct: nett ? 0 : 0.2,
        adjusted_cost: 0,
        is_net_item: nett,
        oem_part_number: null,
        suggested_from: source === "ai" ? "AI suggestion" : casesPhrase(line.cases, data.scope),
        usual_unit_price: line.usual_unit_price ?? null,
      });
    }
    onAdd(items);
    setTicked((prev) => {
      const next = { ...prev };
      for (const { group, source, line } of selected) delete next[lineId(source, group, line)];
      return next;
    });
  }

  let summary = "";
  if (loadState === "loading") summary = "Looking through past reports…";
  else if (loadState === "error") summary = "Couldn't load";
  else if (data) summary = data.cases_used > 0 ? scopeLine(data.cases_used, data.scope) : "No past reports imported yet";

  const renderGroup = ({ group, source, lines }: { group: AnnexSuggestionGroup; source: Source; lines: AnnexSuggestionLine[] }) => (
    <div key={`${source}|${group.part}`} style={{ marginTop: 12 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap", marginBottom: 4 }}>
        <span style={{ fontSize: 12, fontWeight: 700 }}>{group.part}</span>
        <span style={{ fontSize: 11, color: source === "ai" ? "var(--badge-amber-text)" : "var(--text-muted)" }}>
          {source === "ai" ? "AI suggestion — no price, check every line" : reasonLabel(group.reason)}
        </span>
      </div>
      {lines.map((line) => {
        const id = lineId(source, group, line);
        return (
          <label key={id} className="annex-suggest-line">
            <input
              type="checkbox"
              checked={Boolean(ticked[id])}
              onChange={(e) => setTicked((prev) => ({ ...prev, [id]: e.target.checked }))}
            />
            <span style={{ flex: "1 1 200px", minWidth: 0, fontWeight: 600 }}>
              {line.name}
              {line.kind === "fitting" && <span className="annex-suggest-tag">fitting</span>}
            </span>
            <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)", whiteSpace: "nowrap" }}>qty {fmtQty(line.qty)}</span>
            <span
              title={priceTitle(line) || undefined}
              style={{ minWidth: 220, textAlign: "right", whiteSpace: "nowrap", color: line.usual_unit_price != null ? "var(--text-secondary)" : "var(--text-muted)" }}
            >
              {line.usual_unit_price != null
                ? `usual ${money(line.usual_unit_price)} /unit (from ${line.price_from_cases ?? line.cases} case${(line.price_from_cases ?? line.cases) === 1 ? "" : "s"})`
                : "no price"}
            </span>
          </label>
        );
      })}
    </div>
  );

  return (
    <div className="annex-suggest-panel">
      <button type="button" className="annex-suggest-header" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? <ChevronDown style={{ width: 14, height: 14 }} /> : <ChevronRight style={{ width: 14, height: 14 }} />}
        <Lightbulb style={{ width: 14, height: 14, color: "var(--accent-cyan)" }} />
        <span style={{ fontWeight: 700, fontSize: 13 }}>Suggested lines</span>
        <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-muted)", textAlign: "right" }}>{summary}</span>
      </button>

      {open && (
        <div style={{ padding: "0 14px 14px" }}>
          {loadState === "loading" && (
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Looking through past reports…</div>
          )}

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
              {data.cases_used === 0 ? (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  No past reports imported yet — import them on the Knowledge page.
                  {isAdmin(user) && (
                    <>
                      {" "}
                      <a href="/knowledge" className="setup-help-link">Import past reports</a>
                    </>
                  )}
                </div>
              ) : (
                <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  {scopeLine(data.cases_used, data.scope)}
                  {data.vehicle && data.scope !== `past ${data.vehicle} cases` && (
                    <span style={{ color: "var(--text-muted)" }}> (none for {data.vehicle} yet)</span>
                  )}
                  .{" "}
                  <span style={{ color: "var(--text-muted)" }}>
                    Ticked: lines in at least half of the past cases with that damage. Prices are a reference only.
                  </span>
                </div>
              )}

              {data.damaged_parts.length === 0 && (
                <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8 }}>
                  No damaged parts are recorded on this case, so there is nothing to look up.
                </div>
              )}

              {historyGroups.map(renderGroup)}

              {data.groups.length > 0 && historyGroups.length === 0 && (
                <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8 }}>Every suggested line is already in Annex A.</div>
              )}

              {data.without_history.length > 0 && (
                <div className="annex-suggest-gap">
                  <div style={{ fontSize: 12 }}>
                    No past cases cover: <strong>{data.without_history.join(", ")}</strong>
                  </div>
                  {(aiGroups === null || aiGroups.length === 0) && (
                    <button
                      type="button"
                      className="btn-secondary-modern"
                      style={{ fontSize: 11, padding: "5px 12px", marginTop: 8 }}
                      disabled={aiBusy}
                      onClick={() => void askAi()}
                    >
                      <Bot style={{ width: 13, height: 13 }} /> {aiBusy ? "Asking the AI…" : "Ask the AI for lines for these"}
                    </button>
                  )}
                  {aiGroups && aiGroups.length === 0 && (
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>The AI suggested no lines for these.</div>
                  )}
                  {aiError && <div className="setup-error" style={{ marginTop: 8, marginBottom: 0 }}>{aiError}</div>}
                  {aiShown.map(renderGroup)}
                </div>
              )}

              {allShown.length > 0 && (
                <div className="annex-suggest-footer">
                  <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                    Added lines start with no workshop estimate. Save Annex A to keep them.
                  </span>
                  <button
                    type="button"
                    className="btn-primary-modern"
                    style={{ fontSize: 11, padding: "6px 14px" }}
                    disabled={selectedCount === 0}
                    onClick={handleAdd}
                  >
                    <Plus style={{ width: 13, height: 13 }} /> Add {selectedCount} selected to Annex A
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
