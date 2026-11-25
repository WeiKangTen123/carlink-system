"use client";

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { Search, X } from "lucide-react";
import type { Taxonomy } from "@/lib/api";
import { OUTLINE_KEYS, QUARTER_SHAPES, ZONE_SHAPES, type Shape } from "@/components/ImpactSketch";
import { TIER_LABELS } from "@/lib/partSearch";
import { partsToPick } from "@/lib/partsList";
import { usePartsLibrary } from "@/lib/usePartsLibrary";
import { zoneLabel } from "@/lib/vehicleZones";
import {
  SIDE_LABELS,
  cleanName,
  didYouMean,
  highlightFor,
  otherPick,
  pickFor,
  pickLabel,
  pickerAreas,
  pickerEntries,
  pickerView,
  printsAsFor,
  querySide,
  sideChoices,
  zonePreferred,
  type PartPick,
  type PickSide,
  type PickerEntry,
  type PickerHit,
  type PickerPreset,
} from "@/lib/partPicker";

/** The one search picker for adding a damaged part (L-library-cycle.md B.2,
 * B.3; step 3A): type to search, a side toggle LH / RH / "?", area chips,
 * results grouped by area, each with its kind, what it prints as on THIS
 * car's Annex A and -- on a wide screen -- where it is on a small top view
 * of the car. Arrow keys move, Enter picks, Esc closes. Matching, order and
 * what a pick gives are lib/partPicker.ts's: a part on the list gives its
 * canonical name, exactly as the old dropdown did.
 *
 * "Other / Not Listed" stays, last, with what was typed as its name; the
 * best three matches are offered first ("Did you mean").
 *
 * The caller renders it while it is open and closes it on onPick / onClose;
 * it is drawn into <body>, so a table that scrolls sideways can't clip it. */
export type PartPickerProps = {
  title: string;
  taxonomy: Taxonomy | null;
  /** For "prints as": a tail gate or a boot lid. */
  vehicle?: { make?: string | null; model?: string | null; body_type?: string | null } | null;
  preset?: PickerPreset | null;
  onPick: (pick: PartPick) => void;
  onClose: () => void;
};

export function PartPicker(props: PartPickerProps) {
  if (typeof document === "undefined") return null;
  return createPortal(<PickerDialog {...props} />, document.body);
}

const SHAPES: Record<string, Shape> = { ...ZONE_SHAPES, ...QUARTER_SHAPES };
const OUTLINES = [...OUTLINE_KEYS, ...Object.keys(QUARTER_SHAPES)];

/** The library page's top view (components/ImpactSketch.tsx shapes, the
 * rear quarters added), the row's zones lit. */
function MiniCarDiagram({ lit }: { lit: readonly string[] }) {
  const shown = [...new Set(lit)].filter((k) => SHAPES[k]);
  return (
    <svg viewBox="0 0 200 400" className="pp-diagram-svg" aria-hidden="true" focusable="false">
      <text x="100" y="14" textAnchor="middle" className="impact-sketch-axis">
        FRONT
      </text>
      <text x="100" y="392" textAnchor="middle" className="impact-sketch-axis">
        REAR
      </text>
      <text x="13" y="206" textAnchor="middle" className="impact-sketch-axis">
        LH
      </text>
      <text x="187" y="206" textAnchor="middle" className="impact-sketch-axis">
        RH
      </text>
      {OUTLINES.map((k) => {
        const s = SHAPES[k];
        return <rect key={`o-${k}`} x={s.x} y={s.y} width={s.w} height={s.h} rx={s.r ?? 0} className="impact-zone outline" />;
      })}
      {shown.map((k) => {
        const s = SHAPES[k];
        return <rect key={`lit-${k}`} x={s.x} y={s.y} width={s.w} height={s.h} rx={s.r ?? 0} className="impact-zone unrated pp-lit" />;
      })}
    </svg>
  );
}

function whereText(zones: readonly string[]): string {
  if (!zones.length) return "Not on the 3D model";
  return [...new Set(zones.map((z) => zoneLabel(z)))].join(", ");
}

function kindText(entry: PickerEntry): string {
  if (entry.type === "wording") return `past wording${entry.kind === "fitting" ? " · fitting" : ""}${entry.parent ? ` · under ${entry.parent}` : ""}`;
  if (!entry.onList) return `${entry.kind ?? "part"} · ${entry.carMap ? "car-map name, " : ""}not on the part list`;
  return entry.kind ?? "part";
}

type Row = { id: string; kind: "hit"; hit: PickerHit } | { id: string; kind: "other" };

function PickerDialog({ title, taxonomy, vehicle, preset, onPick, onClose }: PartPickerProps) {
  const headingId = useId();
  const listId = useId();
  const idBase = useId();
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const dymRef = useRef<HTMLDivElement>(null);
  const firstChip = useRef(new Map<string, HTMLButtonElement>());

  const { library, error: libraryError, loading } = usePartsLibrary();
  const list = useMemo(() => partsToPick(taxonomy), [taxonomy]);
  const entries = useMemo(() => pickerEntries(library, taxonomy), [library, taxonomy]);
  const areas = useMemo(() => pickerAreas(library), [library]);
  const staff = Boolean(library?.staff);

  const startQuery = preset?.query ?? "";
  const [query, setQuery] = useState(startQuery);
  const [side, setSide] = useState<PickSide | null>(preset?.side ?? querySide(startQuery));
  // Where the side came from: a photo's area or the map's panel is a
  // guess to check; typed words are the person's own.
  const [sideFrom, setSideFrom] = useState<"photo" | "zone" | "words" | null>(
    preset?.side ? (preset.sideFrom ?? null) : querySide(startQuery) ? "words" : null
  );
  const [area, setArea] = useState<string | null>(preset?.area ?? null);
  const [zone, setZone] = useState<string | null>(preset?.zone ?? null);
  const [active, setActive] = useState(0);
  // The row asking "Which side?" (Enter on a sided part with no side).
  const [askSide, setAskSide] = useState<string | null>(null);
  // "Did you mean": the best matches for a name about to go in as Other.
  const [dym, setDym] = useState<PickerHit[] | null>(null);

  const view = useMemo(
    () => pickerView(entries, query, { area, zone, side, preferArea: preset?.preferArea ?? null, preferNames: zonePreferred(zone, list), areas }),
    [entries, query, area, zone, side, preset?.preferArea, list, areas]
  );
  const rows: Row[] = useMemo(
    () => [...view.hits.map((hit, i) => ({ id: `${idBase}-r${i}`, kind: "hit" as const, hit })), { id: `${idBase}-other`, kind: "other" as const }],
    [view.hits, idBase]
  );
  const at = Math.min(active, rows.length - 1);
  const activeRow = rows[at];
  const typed = cleanName(query);

  // Focus: the search box on opening (after any dialog closing under it
  // hands its focus back), the opener's again on closing.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const timer = window.setTimeout(() => {
      const input = inputRef.current;
      if (!input) return;
      input.focus({ preventScroll: true });
      input.setSelectionRange(input.value.length, input.value.length);
    }, 0);
    return () => {
      window.clearTimeout(timer);
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, []);

  // The active row stays in view as the arrows move it.
  useEffect(() => {
    if (activeRow) document.getElementById(activeRow.id)?.scrollIntoView({ block: "nearest" });
  }, [activeRow]);

  useEffect(() => {
    if (askSide) firstChip.current.get(askSide)?.focus({ preventScroll: true });
  }, [askSide]);

  useEffect(() => {
    if (dym) dymRef.current?.querySelector<HTMLElement>("button")?.focus({ preventScroll: true });
  }, [dym]);

  const backToSearch = () => {
    setAskSide(null);
    setDym(null);
    inputRef.current?.focus({ preventScroll: true });
  };

  const onQuery = (text: string) => {
    setQuery(text);
    const named = querySide(text);
    if (named) {
      setSide(named);
      setSideFrom("words");
    }
    setActive(0);
    setAskSide(null);
    setDym(null);
  };

  const chooseSide = (s: PickSide) => {
    setSide((cur) => (cur === s ? null : s));
    setSideFrom(null);
    setAskSide(null);
  };

  /** A row picked: its pick, or "Which side?" first. */
  const choose = (entry: PickerEntry, s: PickSide | null) => {
    const pick = pickFor(entry, s);
    if (!pick) {
      setAskSide(entry.key);
      return;
    }
    onPick(pick);
  };

  /** "Other / Not Listed" with the typed name: the best matches first. */
  const chooseOther = () => {
    if (typed && !dym) {
      const hits = didYouMean(entries, query);
      if (hits.length) {
        setDym(hits);
        return;
      }
    }
    onPick(otherPick(query));
  };

  const activate = (row: Row | undefined) => {
    if (!row) return;
    if (row.kind === "other") chooseOther();
    else choose(row.hit.entry, side);
  };

  const onInputKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    const move = (to: number) => {
      e.preventDefault();
      setAskSide(null);
      setActive(Math.max(0, Math.min(rows.length - 1, to)));
    };
    if (e.key === "ArrowDown") move(at + 1);
    else if (e.key === "ArrowUp") move(at - 1);
    else if (e.key === "PageDown") move(at + 8);
    else if (e.key === "PageUp") move(at - 8);
    else if (e.key === "Enter") {
      e.preventDefault();
      activate(activeRow);
    }
  };

  /** Esc: back out of "Which side?" / "Did you mean" first, then close.
   * Tab goes round inside the dialog. */
  const onDialogKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      if (askSide || dym) backToSearch();
      else onClose();
      return;
    }
    if (e.key !== "Tab" || !boxRef.current) return;
    const items = [...boxRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), input, [tabindex]:not([tabindex='-1'])")].filter(
      (el) => el.offsetParent !== null
    );
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  /** LH / RH / ? on a row: arrows move between them. */
  const onChipKey = (e: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const sib = e.key === "ArrowLeft" ? e.currentTarget.previousElementSibling : e.currentTarget.nextElementSibling;
    if (sib instanceof HTMLButtonElement) sib.focus();
  };

  const sideChips = (entry: PickerEntry, onChosen: (s: PickSide) => void) => (
    <span className="pp-row-sides" role="group" aria-label={`Which side of ${entry.name}?`} onClick={(e) => e.stopPropagation()}>
      {askSide === entry.key && <span className="pp-ask">Which side?</span>}
      {sideChoices(entry).map((s, i) => (
        <button
          key={s}
          type="button"
          className="pp-side-chip"
          tabIndex={askSide === entry.key ? 0 : -1}
          ref={(el) => {
            if (i !== 0) return;
            if (el) firstChip.current.set(entry.key, el);
            else firstChip.current.delete(entry.key);
          }}
          onKeyDown={onChipKey}
          onClick={() => onChosen(s)}
          title={s === "unknown" ? (entry.onList ? `${entry.twin}` : "No side") : s === "left" ? "Left (LH)" : "Right (RH)"}
        >
          {SIDE_LABELS[s]}
        </button>
      ))}
    </span>
  );

  // What the diagram lights: the row under the arrows (or the mouse).
  const litEntry = activeRow?.kind === "hit" ? activeRow.hit.entry : null;
  const lit = litEntry ? highlightFor(litEntry, side) : [];

  const renderHit = (row: Extract<Row, { kind: "hit" }>, index: number) => {
    const { entry, tier, matched } = row.hit;
    const pick = pickFor(entry, side);
    const asks = !pick;
    const prints = printsAsFor(entry, side, taxonomy, vehicle);
    const isActive = index === at;
    const label = pick ? pickLabel(pick) : entry.name;
    // Matched by another name (an alias, a wording): say which; a close
    // spelling of the name itself needs no note.
    const other = matched && matched.toLowerCase() !== entry.name.toLowerCase() && matched.toLowerCase() !== label.toLowerCase();
    const note = (tier === "alias" || tier === "near") && other ? `${TIER_LABELS[tier]}: ${matched}` : null;
    return (
      <div
        key={row.id}
        id={row.id}
        role="option"
        aria-selected={isActive}
        className={`pp-row ${isActive ? "is-active" : ""} ${entry.onList ? "" : "is-other"}`}
        onMouseMove={() => {
          if (index !== at) setActive(index);
        }}
        onClick={() => choose(entry, side)}
      >
        <span className="pp-row-main">
          <span className="pp-name">{label}</span>
          <span className="pp-kind">{kindText(entry)}</span>
          {staff && entry.shownUses != null && <span className="pp-uses">{entry.shownUses ? `used ${entry.shownUses}×` : "not used yet"}</span>}
        </span>
        <span className="pp-row-sub">
          {prints && (
            <span>
              Prints <em>{prints}</em>
            </span>
          )}
          {note && <span className="pp-note">{note}</span>}
          {!entry.onList && <span className="pp-note">Adds as &quot;Other&quot;: {pick?.other_part_name ?? entry.name}</span>}
        </span>
        {asks && sideChips(entry, (s) => choose(entry, s))}
      </div>
    );
  };

  const otherIndex = rows.length - 1;
  const otherActive = at === otherIndex;
  const count = view.hits.length;
  const status = loading && !entries.length ? "Loading the parts…" : `${count} ${count === 1 ? "part" : "parts"}${view.searching ? " found" : ""}`;
  const hint = sideFrom === "photo" ? "Set from the photo's area: check it" : sideFrom === "zone" ? "Set from the map's panel: check it" : null;

  let body;
  if (dym) {
    body = (
      <div className="pp-dym" ref={dymRef}>
        <p className="pp-dym-title">
          Did you mean one of these? &quot;{typed}&quot; isn&apos;t on the list.
        </p>
        <ul className="pp-dym-list">
          {dym.map((h) => {
            const pick = pickFor(h.entry, side);
            const prints = printsAsFor(h.entry, side, taxonomy, vehicle);
            return (
              <li key={h.entry.key} className="pp-dym-item">
                {pick ? (
                  <button type="button" className="pp-dym-btn" onClick={() => onPick(pick)}>
                    <span className="pp-name">{pickLabel(pick)}</span>
                    <span className="pp-kind">{kindText(h.entry)}</span>
                    {prints && (
                      <span className="pp-row-sub">
                        Prints <em>{prints}</em>
                      </span>
                    )}
                  </button>
                ) : (
                  <div className="pp-dym-sided">
                    <span className="pp-name">{h.entry.name}</span>
                    {prints && (
                      <span className="pp-row-sub">
                        Prints <em>{prints}</em>
                      </span>
                    )}
                    <span className="pp-row-sides" role="group" aria-label={`Which side of ${h.entry.name}?`}>
                      {sideChoices(h.entry).map((s) => (
                        <button key={s} type="button" className="pp-side-chip" onKeyDown={onChipKey} onClick={() => choose(h.entry, s)}>
                          {SIDE_LABELS[s]}
                        </button>
                      ))}
                    </span>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        <div className="pp-dym-actions">
          <button type="button" className="btn-secondary-modern" onClick={backToSearch}>
            Back to the list
          </button>
          <button type="button" className="btn-primary-modern" onClick={() => onPick(otherPick(query))}>
            No, add &quot;{typed}&quot; as Other
          </button>
        </div>
      </div>
    );
  } else {
    // Each group's first row in `rows` (the groups' hits, in order).
    const starts = view.groups.map((_, gi) => view.groups.slice(0, gi).reduce((n, g) => n + g.hits.length, 0));
    body = (
      <div className="pp-body">
        <div className="pp-list" id={listId} role="listbox" aria-label="Parts">
          {view.groups.map((g, gi) => (
            <div key={g.key} role="group" aria-labelledby={`${idBase}-g-${g.key}`}>
              <div className="pp-group-label" id={`${idBase}-g-${g.key}`}>
                {g.label}
              </div>
              {g.hits.map((_, hi) => renderHit(rows[starts[gi] + hi] as Extract<Row, { kind: "hit" }>, starts[gi] + hi))}
            </div>
          ))}
          {!count && (
            <div className="pp-empty">
              {loading && !entries.length ? (
                "Loading the parts…"
              ) : view.searching ? (
                <>
                  No part matches &quot;{typed}&quot;{area ? " in this area" : ""}.
                  {view.elsewhere > 0 && (
                    <button type="button" className="intake-link" onClick={() => setArea(null)}>
                      Show {view.elsewhere} in all areas
                    </button>
                  )}
                </>
              ) : (
                "No part here."
              )}
            </div>
          )}
          <div
            id={rows[otherIndex].id}
            role="option"
            aria-selected={otherActive}
            className={`pp-row pp-row-other ${otherActive ? "is-active" : ""}`}
            onMouseMove={() => {
              if (!otherActive) setActive(otherIndex);
            }}
            onClick={chooseOther}
          >
            <span className="pp-row-main">
              <span className="pp-name">Other / Not Listed</span>
            </span>
            <span className="pp-row-sub">
              {typed ? <>Adds &quot;{typed}&quot; as a part not on the list</> : "Adds a part not on the list: name it on its row"}
            </span>
          </div>
        </div>
        <aside className="pp-aside" aria-hidden="true">
          <MiniCarDiagram lit={lit} />
          <span>{litEntry ? whereText(lit) : "Where it is"}</span>
        </aside>
      </div>
    );
  }

  return (
    <div
      className="rv-modal pp-modal"
      onClick={(e) => {
        e.stopPropagation();
        onClose();
      }}
    >
      <div
        ref={boxRef}
        className="rv-picker pp-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onDialogKey}
      >
        <div className="rv-picker-head">
          <h2 id={headingId}>{title}</h2>
          <button type="button" className="intake-icon-btn" onClick={onClose} aria-label="Close">
            <X style={{ width: 16, height: 16 }} />
          </button>
        </div>

        <div className="pp-search-row">
          <div className="pp-search">
            <Search style={{ width: 15, height: 15, flexShrink: 0, color: "var(--text-muted)" }} aria-hidden="true" />
            <input
              ref={inputRef}
              type="text"
              role="combobox"
              aria-label="Search parts, wording or other names"
              aria-expanded={!dym}
              aria-controls={listId}
              aria-autocomplete="list"
              aria-activedescendant={!dym && activeRow ? activeRow.id : undefined}
              autoComplete="off"
              spellCheck={false}
              enterKeyHint="search"
              placeholder="Search: part, wording, other name…"
              value={query}
              onChange={(e) => onQuery(e.target.value)}
              onKeyDown={onInputKey}
            />
            {query && (
              <button type="button" className="pp-clear" aria-label="Clear the search" onClick={() => onQuery("")}>
                <X style={{ width: 13, height: 13 }} />
              </button>
            )}
          </div>
          <div className="pp-side" role="group" aria-label="Side">
            <span className="pp-side-label">Side</span>
            <span className="rv-segmented">
              {(["left", "right", "unknown"] as PickSide[]).map((s) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={side === s}
                  onClick={() => chooseSide(s)}
                  title={s === "unknown" ? "Side undetermined" : s === "left" ? "Left (LH)" : "Right (RH)"}
                >
                  {SIDE_LABELS[s]}
                </button>
              ))}
            </span>
            {hint && side && (
              <span className="intake-chip warn pp-check" title={hint}>
                check
              </span>
            )}
          </div>
        </div>

        <div className="pp-chips" role="group" aria-label="Area">
          <button type="button" className="pp-chip" aria-pressed={!area} onClick={() => setArea(null)}>
            All
          </button>
          {areas.map((a) => (
            <button key={a.key} type="button" className="pp-chip" aria-pressed={area === a.key} onClick={() => setArea(area === a.key ? null : a.key)}>
              {a.label}
            </button>
          ))}
          {zone && (
            <button type="button" className="pp-chip pp-zone-chip" aria-pressed="true" onClick={() => setZone(null)} title="Show every part">
              On the map: {zoneLabel(zone)} <X style={{ width: 11, height: 11, verticalAlign: "-1px" }} aria-hidden="true" />
            </button>
          )}
        </div>

        {libraryError && <p className="pp-notice">The parts library didn&apos;t load: searching the part names only.</p>}

        {body}

        <div className="pp-foot">
          <span role="status" aria-live="polite">
            {dym ? "" : status}
          </span>
          <span className="pp-keys">↑ ↓ move · Enter pick · Esc close</span>
        </div>
      </div>
    </div>
  );
}
