"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, Library as LibraryIcon, List, Search, X } from "lucide-react";
import { OUTLINE_KEYS, QUARTER_SHAPES, ZONE_SHAPES, type Shape } from "@/components/ImpactSketch";
import { zoneLabel } from "@/lib/vehicleZones";
import {
  LIBRARY_KINDS,
  NO_FILTERS,
  areaCounts,
  findEntry,
  groupLibrary,
  panelUses,
  panelsFor,
  printsAs,
  searchEntries,
  type BodyClass,
  type Library,
  type LibraryComponent,
  type LibraryEntry,
  type LibraryFilters,
  type LibraryFitting,
  type LibraryTwin,
} from "@/lib/library";
import { parseQuery, searchParts, TIER_LABELS } from "@/lib/partSearch";
import { getLibraryAction } from "./libraryActions";

/** Knowledge > Parts library (L-library-cycle.md A.7, step 2A): every part
 * the code knows, by area -> assembly -> component, with the price book's
 * wordings as seed fittings under them (staff). Read-only: nothing here
 * changes a part or what the AI sees. The old flat list of the AI's names
 * ("Vehicle Parts & 3D") stays one click away: `children`. */

// The rear quarters: the Cover tab's sketch has no shape for them; here a
// person picks them like any panel (shared with the part picker's diagram).
const EXTRA_SHAPES: Record<string, Shape> = QUARTER_SHAPES;
const SHAPES: Record<string, Shape> = { ...ZONE_SHAPES, ...EXTRA_SHAPES };
// Pickable on the drawing, later ones on top: the sketch's shapes without
// the door glass (the same rectangle as its door; picking the door means
// either, lib/library.ts PANEL_ALSO) and the underbody (it lies under the
// roof; it has its own chip).
const PICK_KEYS = [...Object.keys(ZONE_SHAPES).filter((k) => !k.includes("door_glass") && k !== "underbody"), ...Object.keys(EXTRA_SHAPES)];

const selectStyle: React.CSSProperties = {
  padding: "6px 10px",
  borderRadius: 6,
  border: "1px solid var(--border-color)",
  background: "var(--surface-card)",
  color: "var(--text-primary)",
  fontSize: 11,
  outline: "none",
};
const muted: React.CSSProperties = { color: "var(--text-muted)" };
const small: React.CSSProperties = { fontSize: 11, color: "var(--text-muted)" };

const AI_USE: Record<string, string> = {
  reader: "the AI may name it (its answer list)",
  car_map: "only as one of the car map's linked parts, which are switched off: the AI doesn't name it today",
  none: "the AI never names it",
};
const PLACEMENT: Record<string, string> = {
  by_side: "a person or the photos can put it on a side",
  needs_end: "needs the side and front or rear",
  not_placed: "a side alone doesn't say which one: stays as it is",
};

const usedText = (n: number | undefined) => (n == null ? null : n ? `used ${n}×` : "not used yet");

function Chip({ children, cls = "unrated", title }: { children: ReactNode; cls?: string; title?: string }) {
  return (
    <span className={`chip-severity ${cls}`} style={{ fontSize: 10 }} title={title}>
      {children}
    </span>
  );
}

function zoneText(panels: string[]): string {
  if (!panels.length) return "no panel on the model";
  return [...new Set(panels.map((p) => zoneLabel(p).replace(/^(Left|Right) /, "")))].join(" / ");
}

/** The 2D top view (components/ImpactSketch.tsx shapes) to pick a panel. */
function CarDiagram({
  lib,
  panel,
  onPanel,
}: {
  lib: Library;
  panel: string | null;
  onPanel: (zone: string | null) => void;
}) {
  const uses = useMemo(() => panelUses(lib), [lib]);
  const count = (zone: string) => (uses ? panelsFor(zone).reduce((n, z) => n + (uses[z] ?? 0), 0) : null);
  return (
    <svg viewBox="0 0 200 400" role="group" aria-label="Top view of the car: pick a panel" style={{ width: "100%", maxWidth: 200, height: "auto" }}>
      <text x="100" y="14" textAnchor="middle" className="impact-sketch-axis">
        FRONT
      </text>
      <text x="100" y="392" textAnchor="middle" className="impact-sketch-axis">
        REAR
      </text>
      <text x="14" y="206" textAnchor="middle" className="impact-sketch-axis">
        LH
      </text>
      <text x="186" y="206" textAnchor="middle" className="impact-sketch-axis">
        RH
      </text>
      {OUTLINE_KEYS.map((k) => {
        const s = ZONE_SHAPES[k];
        return <rect key={`o-${k}`} x={s.x} y={s.y} width={s.w} height={s.h} rx={s.r ?? 0} className="impact-zone outline" />;
      })}
      {PICK_KEYS.map((k) => {
        const s = SHAPES[k];
        const n = count(k);
        const picked = panel === k;
        return (
          <g
            key={k}
            role="button"
            tabIndex={0}
            aria-pressed={picked}
            aria-label={s.label}
            style={{ cursor: "pointer" }}
            onClick={() => onPanel(picked ? null : k)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onPanel(picked ? null : k);
              }
            }}
          >
            <rect
              x={s.x}
              y={s.y}
              width={s.w}
              height={s.h}
              rx={s.r ?? 0}
              className={picked ? "impact-zone unrated" : "impact-zone outline"}
              style={picked ? undefined : { fillOpacity: 0.55 }}
            >
              <title>{`${s.label}${n != null ? ` — parts here listed ${n}×` : ""}`}</title>
            </rect>
            {n != null && n > 0 && s.w >= 24 && s.h >= 12 && (
              <text x={s.x + s.w / 2} y={s.y + s.h / 2 + 3} textAnchor="middle" style={{ fontSize: 9, fill: "var(--text-secondary)", pointerEvents: "none" }}>
                {n}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function FittingLine({ f, staff, selected, onSelect }: { f: LibraryFitting; staff: boolean; selected: boolean; onSelect: (key: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(f.key)}
      style={{
        display: "flex",
        gap: 8,
        alignItems: "baseline",
        flexWrap: "wrap",
        width: "100%",
        textAlign: "left",
        background: selected ? "var(--surface-hover)" : "transparent",
        border: "none",
        padding: "3px 6px 3px 26px",
        color: "var(--text-secondary)",
        fontSize: 12,
        cursor: "pointer",
        borderRadius: 4,
      }}
    >
      <span>– {f.name}</span>
      <Chip cls={f.kind === "fitting" ? "minor" : "unrated"}>{f.kind}</Chip>
      <Chip cls="moderate" title="From the price book's wordings; an admin has not reviewed it yet">
        seed
      </Chip>
      {f.sided && <span style={small}>LH / RH</span>}
      {staff && <span style={small}>{usedText(f.uses)}</span>}
    </button>
  );
}

function ComponentLine({
  c,
  context,
  body,
  staff,
  selected,
  onSelect,
}: {
  c: LibraryComponent;
  context: boolean;
  body: "all" | BodyClass;
  staff: boolean;
  selected: boolean;
  onSelect: (key: string) => void;
}) {
  const sedan = printsAs(c, "sedan");
  const hatch = printsAs(c, "hatch");
  const prints = body === "all" ? sedan : printsAs(c, body);
  return (
    <button
      type="button"
      onClick={() => onSelect(c.key)}
      style={{
        display: "flex",
        gap: 8,
        alignItems: "baseline",
        flexWrap: "wrap",
        width: "100%",
        textAlign: "left",
        background: selected ? "var(--surface-hover)" : "transparent",
        border: "none",
        padding: "4px 6px 4px 12px",
        color: "var(--text-primary)",
        opacity: context ? 0.6 : 1,
        fontSize: 13,
        cursor: "pointer",
        borderRadius: 4,
      }}
    >
      <strong style={{ fontWeight: 600 }}>{c.name}</strong>
      <Chip>{c.kind}</Chip>
      {c.side_rule === "sided" && <span style={small}>LH / RH</span>}
      <span style={small}>
        prints “{prints}”{body === "all" && hatch !== sedan ? ` · tail gate “${hatch}”` : ""}
      </span>
      <span style={small}>3D: {zoneText(c.panels)}</span>
      {(c.hidden || c.strip_down) && <span style={small}>{c.strip_down ? "hidden · strip-down" : "hidden"}</span>}
      <Chip cls="minor">{c.status}</Chip>
      {staff && <span style={small}>{usedText(c.uses)}</span>}
    </button>
  );
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <div className="detail-field-label">{label}</div>
      <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>{children}</div>
    </div>
  );
}

function Conditions({ list }: { list?: [string, number][] }) {
  if (!list) return null;
  return (
    <DetailRow label="Typical conditions">
      {list.length ? list.map(([c, n]) => `${c} ×${n}`).join(", ") : <span style={muted}>none written yet</span>}
    </DetailRow>
  );
}

function DetailPanel({ lib, picked, onSelect, onClose }: { lib: Library; picked: LibraryEntry; onSelect: (key: string) => void; onClose: () => void }) {
  const asmName = (key: string | null) => lib.assemblies.find((a) => a.key === key)?.name ?? key ?? "—";
  const nameOf = (key: string) => findEntry(lib, key)?.entry.name ?? key;
  const linkTo = (key: string, text: string) => (
    <button key={`${key}-${text}`} type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "2px 8px", margin: "2px 4px 2px 0" }} onClick={() => onSelect(key)}>
      {text}
    </button>
  );

  let body: ReactNode;
  if (picked.type === "component") {
    const c: LibraryComponent = picked.entry;
    const sides = c.side_rule === "sided" ? " (LH / RH added last)" : "";
    body = (
      <>
        <DetailRow label="Prints as">
          Sedan / other: “{printsAs(c, "sedan")}”{sides}
          <br />
          Tail-gate bodies: “{printsAs(c, "hatch")}”
        </DetailRow>
        <DetailRow label="Names it holds">
          {Object.values(c.names).join(", ")}
          {c.twin && (
            <>
              <br />
              Side not known: {linkTo(c.twin_key ?? "", c.twin)}
            </>
          )}
        </DetailRow>
        <DetailRow label="Other names">
          {c.aliases.length ? c.aliases.map((a) => `${a.text}${a.kind === "typo" ? " (typo)" : ""}`).join(", ") : <span style={muted}>none</span>}
        </DetailRow>
        <DetailRow label="On the 3D model">
          {zoneText(c.panels)}
          {c.stand_in && <span> — really {c.stand_in}</span>}
          {c.side_rule === "one_side_unknown" && <span> — on one side; which one is the car&apos;s own fact</span>}
        </DetailRow>
        <DetailRow label="Assembly">
          {asmName(c.assembly)}
          {c.also_in.length > 0 && <span style={muted}> · also in {c.also_in.map(asmName).join(", ")}</span>}
        </DetailRow>
        <DetailRow label="Kind">
          {c.kind} ({c.role}){c.if_fitted ? " · if fitted" : ""}
          {c.hidden ? " · hidden" : ""}
          {c.strip_down ? " · seen at strip-down" : ""}
          {c.body_classes.length < 2 ? ` · ${c.body_classes.join(", ")} bodies only` : ""}
        </DetailRow>
        {c.parent_key && <DetailRow label="Price book files its lines under">{linkTo(c.parent_key, nameOf(c.parent_key))}</DetailRow>}
        <DetailRow label="Linked parts (L1)">
          {c.linked.out.length + c.linked.in.length === 0 && <span style={muted}>none</span>}
          {c.linked.out.length > 0 && (
            <div>
              <span style={muted}>Brings in: </span>
              {c.linked.out.map((l) => linkTo(l.key, `${l.name} · ${l.type}`))}
            </div>
          )}
          {c.linked.in.length > 0 && (
            <div>
              <span style={muted}>Comes with: </span>
              {c.linked.in.map((l) => linkTo(l.key, `${l.name} · ${l.type}`))}
            </div>
          )}
        </DetailRow>
        <Conditions list={c.conditions} />
        {c.note && <DetailRow label="Note">{c.note}</DetailRow>}
        <DetailRow label="AI">{AI_USE[c.ai_use] ?? c.ai_use}</DetailRow>
        <DetailRow label="Library">
          {c.status}, version {c.version_added}
          {lib.staff && ` · ${usedText(c.uses)}`}
        </DetailRow>
      </>
    );
  } else if (picked.type === "fitting") {
    const f: LibraryFitting = picked.entry;
    body = (
      <>
        <DetailRow label="Belongs to">{f.parent_keys.length ? f.parent_keys.map((k) => linkTo(k, nameOf(k))) : <span style={muted}>no rule places it yet</span>}</DetailRow>
        <DetailRow label="Kind">
          {f.kind}
          {f.sided ? " · written with LH / RH" : ""}
        </DetailRow>
        <DetailRow label="Other spellings">{f.aliases.length ? f.aliases.map((a) => a.text).join(", ") : <span style={muted}>none</span>}</DetailRow>
        <Conditions list={f.conditions} />
        <DetailRow label="Library">
          seed: from the price book&apos;s wordings, names only; an admin reviews seeds later{f.uses != null && ` · in ${f.uses} past report${f.uses === 1 ? "" : "s"}`}
        </DetailRow>
      </>
    );
  } else {
    const t: LibraryTwin = picked.entry;
    body = (
      <>
        <DetailRow label="Prints as">
          “{t.firm_wording}”{t.firm_wording_hatch !== t.firm_wording ? ` · tail gate “${t.firm_wording_hatch}”` : ""} (no LH / RH)
        </DetailRow>
        <DetailRow label="Stands for">{t.covers.map((k) => linkTo(k, nameOf(k)))}</DetailRow>
        <DetailRow label="Placing it">{PLACEMENT[t.placement] ?? t.placement}</DetailRow>
        <DetailRow label="On the 3D model">{zoneText(t.panels)} (marked “?” on each)</DetailRow>
        <Conditions list={t.conditions} />
        <DetailRow label="AI">{AI_USE[t.ai_use] ?? t.ai_use}</DetailRow>
        {lib.staff && <DetailRow label="Library">{usedText(t.uses)}</DetailRow>}
      </>
    );
  }

  return (
    <div className="card-glass" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <div>
          <div className="card-title">{picked.entry.name}</div>
          <div className="card-subtitle">{picked.type === "twin" ? "side not known" : picked.type}</div>
        </div>
        <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "2px 6px" }} onClick={onClose} aria-label="Close the details">
          <X style={{ width: 12, height: 12 }} />
        </button>
      </div>
      {body}
    </div>
  );
}

export function PartsLibraryTab({ children }: { children?: ReactNode }) {
  const [lib, setLib] = useState<Library | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"library" | "names">("library");
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<LibraryFilters>(NO_FILTERS);
  const [selected, setSelected] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let alive = true;
    void getLibraryAction().then((res) => {
      if (!alive) return;
      if ("error" in res) setError(res.error);
      else setLib(res);
    });
    return () => {
      alive = false;
    };
  }, []);

  const groups = useMemo(() => (lib ? groupLibrary(lib, filters) : []), [lib, filters]);
  const entries = useMemo(() => (lib ? searchEntries(lib, filters) : []), [lib, filters]);
  const parsed = parseQuery(query);
  const hits = useMemo(() => (parsed.words.length ? searchParts(entries, query, { limit: 80 }) : []), [entries, query, parsed.words.length]);
  const counts = useMemo(() => (lib ? areaCounts(lib) : {}), [lib]);

  const set = (patch: Partial<LibraryFilters>) => setFilters((f) => ({ ...f, ...patch }));
  const picked = lib && selected ? findEntry(lib, selected) : null;
  const staff = Boolean(lib?.staff);
  const narrowed = Boolean(filters.area || filters.panel);

  const header = (
    <div className="card-header" style={{ flexWrap: "wrap", gap: 10 }}>
      <div>
        <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <LibraryIcon style={{ width: 16, height: 16, color: "var(--accent-primary)" }} /> Parts library
        </div>
        <div className="card-subtitle">
          {lib
            ? `${lib.components.length} parts · ${lib.twins.length} side-unknown names · ${lib.fittings.length} seed fittings · library v${lib.version} (${lib.core})`
            : "Every part the system knows, by where it is on the car"}
        </div>
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 10px", opacity: view === "library" ? 1 : 0.65 }} onClick={() => setView("library")}>
          <LibraryIcon style={{ width: 12, height: 12 }} /> Library
        </button>
        <button
          type="button"
          className="btn-secondary-modern"
          style={{ fontSize: 11, padding: "5px 10px", opacity: view === "names" ? 1 : 0.65 }}
          onClick={() => setView("names")}
          title="The flat list of names the AI answers with, and their 3D zones (the former Vehicle Parts & 3D tab)"
        >
          <List style={{ width: 12, height: 12 }} /> AI name list
        </button>
      </div>
    </div>
  );

  if (view === "names") {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div className="card-glass">{header}</div>
        {children}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="card-glass">
        {header}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ position: "relative", flex: "1 1 260px", maxWidth: 420 }}>
            <Search style={{ position: "absolute", left: 9, top: 9, width: 13, height: 13, color: "var(--text-muted)" }} />
            <input
              type="search"
              className="settings-input"
              style={{ marginTop: 0, width: "100%", paddingLeft: 28 }}
              placeholder="Search parts, wording, other names…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search the parts library"
            />
          </div>
          <select style={selectStyle} value={filters.body} aria-label="Body" onChange={(e) => set({ body: e.target.value as LibraryFilters["body"] })}>
            <option value="all">Body: all</option>
            <option value="sedan">Sedan (boot lid)</option>
            <option value="hatch">Tail gate (hatch, SUV, MPV…)</option>
          </select>
          <select style={selectStyle} value={filters.status} aria-label="Show" onChange={(e) => set({ status: e.target.value as LibraryFilters["status"] })}>
            <option value="all">Show: all</option>
            <option value="core">Core (built in)</option>
            <option value="seed">Seed fittings</option>
          </select>
          <select style={selectStyle} value={filters.kind} aria-label="Kind" onChange={(e) => set({ kind: e.target.value as LibraryFilters["kind"] })}>
            <option value="all">Kind: all</option>
            {LIBRARY_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          {parsed.side && <Chip cls="moderate" title="Side words (LH, LHS, nearside, N/S...) are taken out of the search: parts with LH / RH take this side, parts without one are still shown">side: {parsed.side === "left" ? "LH" : "RH"}</Chip>}
        </div>
        {lib && !staff && (
          <p style={{ ...small, margin: "8px 0 0" }}>Usage counts and the price book&apos;s fitting names are shown to staff (admins, &quot;see all cases&quot;, &quot;may sign off&quot;).</p>
        )}
      </div>

      {error && (
        <div className="card-glass" style={{ color: "var(--badge-red-text)", fontSize: 13 }}>
          {error}
        </div>
      )}
      {!lib && !error && <div className="card-glass" style={{ ...muted, fontSize: 13 }}>Loading the parts library…</div>}

      {lib && (
        <div style={{ display: "flex", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
          {/* Where on the car */}
          <div className="card-glass" style={{ flex: "0 1 230px", minWidth: 200, display: "flex", flexDirection: "column", gap: 10, alignItems: "center" }}>
            <div style={{ ...small, alignSelf: "flex-start" }}>Tap an area or a panel</div>
            <CarDiagram lib={lib} panel={filters.panel} onPanel={(zone) => set({ panel: zone, area: null })} />
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, justifyContent: "center" }}>
              <button type="button" className="btn-secondary-modern" style={{ fontSize: 10, padding: "3px 8px", opacity: narrowed ? 0.65 : 1 }} onClick={() => set({ area: null, panel: null })}>
                All
              </button>
              {lib.areas.map((a) => (
                <button
                  key={a.key}
                  type="button"
                  className="btn-secondary-modern"
                  style={{ fontSize: 10, padding: "3px 8px", opacity: filters.area === a.key ? 1 : 0.65 }}
                  onClick={() => set({ area: filters.area === a.key ? null : a.key, panel: null })}
                >
                  {a.label} {counts[a.key] ?? 0}
                </button>
              ))}
              <button
                type="button"
                className="btn-secondary-modern"
                style={{ fontSize: 10, padding: "3px 8px", opacity: filters.panel === "underbody" ? 1 : 0.65 }}
                onClick={() => set({ panel: filters.panel === "underbody" ? null : "underbody", area: null })}
                title="The underbody panel of the 3D model (sills, floor, subframes...)"
              >
                Underbody panel
              </button>
              <button
                type="button"
                className="btn-secondary-modern"
                style={{ fontSize: 10, padding: "3px 8px", opacity: filters.area === "none" ? 1 : 0.65 }}
                onClick={() => set({ area: filters.area === "none" ? null : "none", panel: null })}
                title="Parts the 3D model has no panel for"
              >
                No panel {counts.none ?? 0}
              </button>
            </div>
            {filters.panel && <div style={small}>Panel: {zoneLabel(filters.panel)}</div>}
          </div>

          {/* What is there */}
          <div className="card-glass" style={{ flex: "1 1 420px", minWidth: 0, display: "flex", flexDirection: "column", gap: 6 }}>
            {parsed.words.length > 0 ? (
              hits.length === 0 ? (
                <p style={{ ...muted, fontSize: 13, margin: 0 }}>Nothing matches “{query}”{narrowed || filters.kind !== "all" || filters.status !== "all" ? " with these filters" : ""}.</p>
              ) : (
                <>
                  <div style={small}>
                    {hits.length} match{hits.length === 1 ? "" : "es"}, best first
                  </div>
                  {hits.map((h) => {
                    const ref = h.entry.ref;
                    const area = lib.areas.find((a) => a.key === h.entry.area)?.label ?? "not placed";
                    return (
                      <div key={h.entry.key} style={{ display: "flex", flexDirection: "column" }}>
                        {ref.type === "component" ? (
                          <ComponentLine c={ref.entry} context={false} body={filters.body} staff={staff} selected={selected === ref.entry.key} onSelect={setSelected} />
                        ) : ref.type === "fitting" ? (
                          <FittingLine f={ref.entry} staff={staff} selected={selected === ref.entry.key} onSelect={setSelected} />
                        ) : null}
                        <div style={{ ...small, paddingLeft: 12 }}>
                          {TIER_LABELS[h.tier]}
                          {h.matched !== h.entry.name ? `: “${h.matched}”` : ""} · {area}
                          {h.side ? ` · ${h.side === "left" ? "LH" : "RH"}` : ""}
                        </div>
                      </div>
                    );
                  })}
                </>
              )
            ) : groups.length === 0 ? (
              <p style={{ ...muted, fontSize: 13, margin: 0 }}>No parts with these filters.</p>
            ) : (
              groups.map((g) => (
                <div key={g.key} style={{ display: "flex", flexDirection: "column", gap: 2, marginBottom: 6 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", borderBottom: "1px solid var(--border-color)", paddingBottom: 4 }}>
                    <strong style={{ fontSize: 12, letterSpacing: 0.5 }}>{g.label.toUpperCase()}</strong>
                    <span style={small}>
                      {g.components} part{g.components === 1 ? "" : "s"}
                      {g.fittings ? ` · ${g.fittings} fitting${g.fittings === 1 ? "" : "s"}` : ""}
                      {g.uses != null ? ` · used ${g.uses}×` : ""}
                    </span>
                  </div>
                  {g.loose.map((f) => (
                    <FittingLine key={f.key} f={f} staff={staff} selected={selected === f.key} onSelect={setSelected} />
                  ))}
                  {g.assemblies.map((a) => {
                    const isOpen = open[a.key] ?? narrowed;
                    const n = a.rows.filter((r) => !r.context).length;
                    const used = staff ? a.rows.reduce((s, r) => s + (r.context ? 0 : (r.component.uses ?? 0)), 0) : null;
                    return (
                      <div key={a.key}>
                        <button
                          type="button"
                          onClick={() => setOpen((o) => ({ ...o, [a.key]: !isOpen }))}
                          aria-expanded={isOpen}
                          style={{ display: "flex", alignItems: "center", gap: 6, width: "100%", background: "transparent", border: "none", padding: "4px 0", color: "var(--text-primary)", fontSize: 13, cursor: "pointer", textAlign: "left" }}
                        >
                          {isOpen ? <ChevronDown style={{ width: 13, height: 13 }} /> : <ChevronRight style={{ width: 13, height: 13 }} />}
                          <span style={{ fontWeight: 600 }}>{a.name}</span>
                          <span style={small}>
                            {n} part{n === 1 ? "" : "s"}
                            {used != null ? ` · used ${used}×` : ""}
                          </span>
                        </button>
                        {isOpen &&
                          a.rows.map((r) => (
                            <div key={r.component.key}>
                              <ComponentLine c={r.component} context={r.context} body={filters.body} staff={staff} selected={selected === r.component.key} onSelect={setSelected} />
                              {r.fittings.map((f) => (
                                <FittingLine key={f.key} f={f} staff={staff} selected={selected === f.key} onSelect={setSelected} />
                              ))}
                            </div>
                          ))}
                      </div>
                    );
                  })}
                </div>
              ))
            )}
          </div>

          {/* The picked part */}
          {picked && (
            <div style={{ flex: "0 1 320px", minWidth: 260 }}>
              <DetailPanel lib={lib} picked={picked} onSelect={setSelected} onClose={() => setSelected(null)} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
