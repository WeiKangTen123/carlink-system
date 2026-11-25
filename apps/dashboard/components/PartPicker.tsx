"use client";

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { Search, X } from "lucide-react";
import type { Taxonomy } from "@/lib/api";
import { OUTLINE_KEYS, QUARTER_SHAPES, ZONE_SHAPES, type Shape } from "@/components/ImpactSketch";
import { TIER_LABELS } from "@/lib/partSearch";
import { usePartsLibrary } from "@/lib/usePartsLibrary";
import { zoneLabel } from "@/lib/vehicleZones";
import {
  SIDE_LABELS,
  areaLabel,
  emptyText,
  highlightFor,
  isTabbable,
  listedNote,
  listedRows,
  otherPick,
  otherStep,
  pickFor,
  pickLabel,
  pickerAreas,
  pickerEntries,
  pickerList,
  pickerView,
  printsAsFor,
  querySide,
  sideChoices,
  twinNote,
  zonePreferred,
  type ListedItem,
  type PartPick,
  type PickSide,
  type PickerEntry,
  type PickerHit,
  type PickerPreset,
} from "@/lib/partPicker";

/** The one search picker for adding a damaged part (L-library-cycle.md B.2,
 * B.3; step 3A): type to search, a side toggle LH / RH / "?", area chips,
 * results grouped by area, each with its kind, what it prints as on THIS
 * car's Annex A, whether it is listed already and -- on a wide screen --
 * where it is on a small top view of the car. Arrow keys move, Enter picks,
 * Esc closes. Matching, order and what a pick gives are lib/partPicker.ts's:
 * a part on the list gives its canonical name, exactly as the old dropdown
 * did, and nothing can be picked before there is a part list.
 *
 * "Other / Not Listed" stays, last, with what was typed as its name -- after
 * one confirmation, the best three matches offered first ("Did you mean").
 * A sided part picked with no side asks "Which side?" in a bar above the
 * list (the list's options hold no buttons).
 *
 * The caller renders it while it is open and closes it on onPick / onClose;
 * it is drawn into <body>, so a table that scrolls sideways can't clip it,
 * and the page behind doesn't scroll while it is open. */
export type PartPickerProps = {
  title: string;
  taxonomy: Taxonomy | null;
  /** For "prints as": a tail gate or a boot lid. */
  vehicle?: { make?: string | null; model?: string | null; body_type?: string | null } | null;
  preset?: PickerPreset | null;
  /** The parts on the list now: marked "already row 3", and the map
   * panel's own part not put first when it is (review M1). */
  listed?: readonly ListedItem[] | null;
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
  if (entry.type === "wording") return `past wording${entry.parent ? ` · under ${entry.parent}` : ""}`;
  return entry.kind ?? "part";
}

type Row = { key: string; kind: "hit"; hit: PickerHit } | { key: string; kind: "other" };

function PickerDialog({ title, taxonomy, vehicle, preset, listed, onPick, onClose }: PartPickerProps) {
  const headingId = useId();
  const listId = useId();
  const idBase = useId();
  const hintId = useId();
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const askRef = useRef<HTMLDivElement>(null);
  const confirmRef = useRef<HTMLDivElement>(null);
  const downOnBackdrop = useRef(false);

  const { library, error: libraryError, loading: libraryLoading } = usePartsLibrary();
  const list = useMemo(() => pickerList(library, taxonomy).names, [library, taxonomy]);
  const entries = useMemo(() => pickerEntries(library, taxonomy), [library, taxonomy]);
  const areas = useMemo(() => pickerAreas(library), [library]);
  const rowsListed = useMemo(() => listedRows(listed), [listed]);
  const staff = Boolean(library?.staff);
  // Picks wait for a part list (review H1): the taxonomy's, else the
  // library's canonical names. A null taxonomy is still loading.
  const ready = entries.length > 0;
  const loading = libraryLoading || !taxonomy;

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
  // The row under the arrows, by key: it stays on its part when the rows
  // change under it (the library arriving, review L7).
  const [activeKey, setActiveKey] = useState<string | null>(null);
  // "Which side?" for a sided part picked with no side.
  const [askSide, setAskSide] = useState<PickerEntry | null>(null);
  // "Other" with typed words: one confirmation, "Did you mean" first.
  const [confirm, setConfirm] = useState<{ pick: PartPick; suggestions: PickerHit[] } | null>(null);

  const preferArea = preset?.preferArea ?? null;
  const view = useMemo(
    () => pickerView(entries, query, { area, zone, side, preferArea, preferNames: zonePreferred(zone, list, rowsListed), areas, listed: rowsListed }),
    [entries, query, area, zone, side, preferArea, list, rowsListed, areas]
  );
  const rows: Row[] = useMemo(
    () => [...view.hits.map((hit) => ({ key: `h:${hit.entry.key}`, kind: "hit" as const, hit })), ...(ready ? [{ key: "other", kind: "other" as const }] : [])],
    [view.hits, ready]
  );
  const at = Math.max(0, rows.findIndex((r) => r.key === activeKey));
  const activeRow = rows[at];
  const rowId = (i: number) => `${idBase}-o${i}`;
  const activeId = activeRow ? rowId(at) : null;
  const typed = otherPick(query, side).other_part_name;

  // Focus: the search box on opening (after any dialog closing under it
  // hands its focus back), the opener's again on closing when it is still
  // there (the page may move it on: tab 1's add row, a new row's name).
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

  // The page behind doesn't scroll while the picker is open (phones, L3).
  useEffect(() => {
    const body = document.body;
    const before = body.style.overflow;
    body.style.overflow = "hidden";
    return () => {
      body.style.overflow = before;
    };
  }, []);

  // The active row stays in view as the arrows move it.
  useEffect(() => {
    if (activeId) document.getElementById(activeId)?.scrollIntoView({ block: "nearest" });
  }, [activeId]);

  useEffect(() => {
    if (askSide) askRef.current?.querySelector<HTMLElement>("button")?.focus({ preventScroll: true });
  }, [askSide]);

  useEffect(() => {
    if (!confirm) return;
    const box = confirmRef.current;
    (box?.querySelector<HTMLElement>("[data-autofocus]") ?? box?.querySelector<HTMLElement>("button"))?.focus({ preventScroll: true });
  }, [confirm]);

  const backToSearch = () => {
    setAskSide(null);
    setConfirm(null);
    inputRef.current?.focus({ preventScroll: true });
  };

  const onQuery = (text: string) => {
    setQuery(text);
    const named = querySide(text);
    if (named) {
      setSide(named);
      setSideFrom("words");
    }
    setActiveKey(null);
    setAskSide(null);
    setConfirm(null);
  };

  const chooseSide = (s: PickSide) => {
    setSide((cur) => (cur === s ? null : s));
    setSideFrom(null);
    setAskSide(null);
  };

  /** A row picked: its pick, or "Which side?" first. */
  const choose = (entry: PickerEntry, s: PickSide | null) => {
    if (!ready) return;
    const pick = pickFor(entry, s);
    if (!pick) {
      setAskSide(entry);
      return;
    }
    onPick(pick);
  };

  /** "Other / Not Listed": typed words wait for one confirmation, with the
   * best matches first; nothing typed, an unnamed Other at once. */
  const chooseOther = () => {
    if (!ready) return;
    const step = otherStep(entries, query, side);
    if (!step.confirm) onPick(step.pick);
    else setConfirm({ pick: step.pick, suggestions: step.suggestions });
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
      const row = rows[Math.max(0, Math.min(rows.length - 1, to))];
      if (row) setActiveKey(row.key);
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

  /** Esc: back out of "Which side?" / the confirmation first, then close.
   * Tab goes round inside the dialog, over what Tab really reaches (M3). */
  const onDialogKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      if (askSide || confirm) backToSearch();
      else onClose();
      return;
    }
    if (e.key !== "Tab" || !boxRef.current) return;
    const items = [...boxRef.current.querySelectorAll<HTMLElement & { disabled?: boolean }>("button, input, [tabindex]")].filter(isTabbable);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    const inside = items.includes(document.activeElement as HTMLElement);
    if (e.shiftKey && (document.activeElement === first || !inside)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && (document.activeElement === last || !inside)) {
      e.preventDefault();
      first.focus();
    }
  };

  /** LH / RH / ? in the "Which side?" bar: arrows move between them. */
  const onChipKey = (e: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const sib = e.key === "ArrowLeft" ? e.currentTarget.previousElementSibling : e.currentTarget.nextElementSibling;
    if (sib instanceof HTMLButtonElement) sib.focus();
  };

  // What the diagram lights: the part asked about, else the row under the
  // arrows (or the mouse).
  const litEntry = askSide ?? (activeRow?.kind === "hit" ? activeRow.hit.entry : null);
  const lit = litEntry ? highlightFor(litEntry, side) : [];

  const renderHit = (row: Extract<Row, { kind: "hit" }>, index: number) => {
    const { entry, tier, matched } = row.hit;
    const pick = pickFor(entry, side);
    const prints = printsAsFor(entry, side, taxonomy, vehicle);
    const isActive = index === at;
    const label = pick ? pickLabel(pick) : entry.name;
    // Matched by another name (an alias, a wording): say which; a close
    // spelling of the name itself needs no note.
    const otherName = matched && matched.toLowerCase() !== entry.name.toLowerCase() && matched.toLowerCase() !== label.toLowerCase();
    const note = (tier === "alias" || tier === "near") && otherName ? `${TIER_LABELS[tier]}: ${matched}` : null;
    const onList = listedNote(entry, side, rowsListed);
    const ends = twinNote(entry, side);
    return (
      <div
        key={row.key}
        id={rowId(index)}
        role="option"
        aria-selected={isActive}
        className={`pp-row ${isActive ? "is-active" : ""} ${entry.onList ? "" : "is-other"}`}
        onMouseMove={() => {
          if (index !== at) setActiveKey(row.key);
        }}
        onClick={() => choose(entry, side)}
      >
        <span className="pp-row-main">
          <span className="pp-name">{label}</span>
          <span className="pp-kind">
            {kindText(entry)}
            {pick ? "" : " · side to choose"}
          </span>
          {staff && entry.shownUses != null && <span className="pp-uses">{entry.shownUses ? `used ${entry.shownUses}×` : "not used yet"}</span>}
          {onList && <span className="pp-listed">{onList}</span>}
        </span>
        <span className="pp-row-sub">
          {prints && (
            <span>
              Prints <em>{prints}</em>
            </span>
          )}
          {note && <span className="pp-note">{note}</span>}
          {ends && <span className="pp-note">{ends}</span>}
          {!entry.onList && <span className="pp-note">Adds as &quot;Other&quot;: {pick?.other_part_name ?? entry.name}</span>}
        </span>
      </div>
    );
  };

  const otherIndex = rows.length - 1;
  const otherActive = rows[at]?.kind === "other";
  const count = view.hits.length;
  const status = !ready ? "" : `${count} ${count === 1 ? "part" : "parts"}${view.searching ? " found" : ""}`;
  const hint = sideFrom === "photo" ? "from the photo: check" : sideFrom === "zone" ? "from the map: check" : null;

  let body;
  if (confirm) {
    const name = confirm.pick.other_part_name ?? "";
    body = (
      <div className="pp-dym" ref={confirmRef}>
        <p className="pp-dym-title">{confirm.suggestions.length ? "Did you mean one of these?" : `Add "${name}" as a part not on the list?`}</p>
        <p className="intake-muted pp-dym-hint">
          &quot;{name}&quot; isn&apos;t on the part list. As &quot;Other&quot; it prints on Annex A under that name, with no 3D marker and no linked
          parts.
        </p>
        {confirm.suggestions.length > 0 && (
          <ul className="pp-dym-list">
            {confirm.suggestions.map((h, i) => {
              const pick = pickFor(h.entry, side);
              const prints = printsAsFor(h.entry, side, taxonomy, vehicle);
              const onList = listedNote(h.entry, side, rowsListed);
              return (
                <li key={h.entry.key}>
                  <button type="button" className="pp-dym-btn" onClick={() => choose(h.entry, side)} data-autofocus={i === 0 ? true : undefined}>
                    <span className="pp-name">{pick ? pickLabel(pick) : h.entry.name}</span>
                    <span className="pp-kind">
                      {kindText(h.entry)}
                      {pick ? "" : " · then choose the side"}
                      {onList ? ` · ${onList}` : ""}
                    </span>
                    {prints && (
                      <span className="pp-row-sub">
                        Prints <em>{prints}</em>
                      </span>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        <div className="pp-dym-actions">
          <button type="button" className="btn-secondary-modern" onClick={backToSearch}>
            Back to the list
          </button>
          <button type="button" className="btn-primary-modern" onClick={() => onPick(confirm.pick)} data-autofocus={confirm.suggestions.length ? undefined : true}>
            Add &quot;{name}&quot; as Other
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
              {emptyText({ ready, loading, searching: view.searching, typed: query.trim(), area, zone })}
              {ready && view.searching && zone && (
                <button type="button" className="intake-link" onClick={() => setZone(null)}>
                  Search all parts
                </button>
              )}
              {ready && view.searching && !zone && view.elsewhere > 0 && (
                <button type="button" className="intake-link" onClick={() => setArea(null)}>
                  Show {view.elsewhere} in all areas
                </button>
              )}
            </div>
          )}
          {ready && (
            <div
              id={rowId(otherIndex)}
              role="option"
              aria-selected={otherActive}
              className={`pp-row pp-row-other ${otherActive ? "is-active" : ""}`}
              onMouseMove={() => {
                if (!otherActive) setActiveKey("other");
              }}
              onClick={chooseOther}
            >
              <span className="pp-row-main">
                <span className="pp-name">Other / Not Listed</span>
              </span>
              <span className="pp-row-sub">
                {typed ? <>Adds &quot;{typed}&quot; as a part not on the list (you confirm it next)</> : "Adds a part not on the list: name it on its row"}
              </span>
            </div>
          )}
        </div>
        <aside className="pp-aside" aria-hidden="true">
          <MiniCarDiagram lit={lit} />
          <span>{litEntry ? whereText(lit) : "Where it is"}</span>
        </aside>
      </div>
    );
  }

  // One line under the chips: what the list shows and how to widen it.
  const notes = [];
  if (zone)
    notes.push(
      <span key="zone">
        Showing the parts on {zoneLabel(zone)}.{" "}
        <button type="button" className="intake-link" onClick={() => setZone(null)}>
          Search all parts
        </button>
      </span>
    );
  else if (preferArea && !area) notes.push(<span key="photo">Showing {areaLabel(preferArea, areas)} first (from the photo).</span>);
  if (area && view.elsewhere > 0 && view.hits.length > 0)
    notes.push(
      <span key="elsewhere">
        {view.elsewhere} more in other areas.{" "}
        <button type="button" className="intake-link" onClick={() => setArea(null)}>
          Show all areas
        </button>
      </span>
    );
  if (ready && libraryLoading) notes.push(<span key="loading">Loading the parts library…</span>);
  if (libraryError) notes.push(<span key="error">The parts library didn&apos;t load: searching the part names only.</span>);

  return (
    <div
      className="rv-modal pp-modal"
      // Closes only on a click that starts and ends on the backdrop: a text
      // selection dragged out of the search box keeps it open (L3).
      onMouseDown={(e) => {
        downOnBackdrop.current = e.target === e.currentTarget;
      }}
      onClick={(e) => {
        e.stopPropagation();
        if (downOnBackdrop.current && e.target === e.currentTarget) onClose();
        downOnBackdrop.current = false;
      }}
    >
      <div ref={boxRef} className="rv-picker pp-dialog" role="dialog" aria-modal="true" aria-labelledby={headingId} onKeyDown={onDialogKey}>
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
              aria-expanded={!confirm}
              aria-controls={confirm ? undefined : listId}
              aria-autocomplete="list"
              aria-activedescendant={!confirm && activeId ? activeId : undefined}
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
          <div className="pp-side" role="group" aria-label="Side" aria-describedby={hint && side ? hintId : undefined}>
            <span className="pp-side-label">Side</span>
            <span className="rv-segmented">
              {(["left", "right", "unknown"] as PickSide[]).map((s) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={side === s}
                  aria-label={s === "unknown" ? "Side undetermined" : s === "left" ? "Left (LH)" : "Right (RH)"}
                  onClick={() => chooseSide(s)}
                >
                  {SIDE_LABELS[s]}
                </button>
              ))}
            </span>
            {hint && side && (
              <span className="intake-chip warn pp-check" id={hintId}>
                {hint}
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
            <button
              type="button"
              className="pp-chip pp-zone-chip"
              aria-pressed="true"
              onClick={() => setZone(null)}
              aria-label={`Showing ${zoneLabel(zone)} only: show every part`}
            >
              On the map: {zoneLabel(zone)} <X style={{ width: 11, height: 11, verticalAlign: "-1px" }} aria-hidden="true" />
            </button>
          )}
        </div>

        {notes.length > 0 && <p className="pp-notes">{notes}</p>}

        {askSide && (
          <div className="pp-ask-bar" ref={askRef} role="group" aria-label={`Which side of ${askSide.name}?`}>
            <span className="pp-ask">{askSide.name}: which side?</span>
            <span className="pp-ask-sides">
              {sideChoices(askSide).map((s) => (
                <button key={s} type="button" className="pp-side-chip" onKeyDown={onChipKey} onClick={() => choose(askSide, s)}>
                  {s === "unknown" ? "? side unknown" : SIDE_LABELS[s]}
                </button>
              ))}
            </span>
            {twinNote(askSide, "unknown") && <span className="pp-note">?: {twinNote(askSide, "unknown")}</span>}
            <button type="button" className="intake-link" onClick={backToSearch}>
              Cancel
            </button>
          </div>
        )}

        {body}

        <div className="pp-foot">
          <span role="status" aria-live="polite">
            {confirm ? "" : status}
          </span>
          <span className="pp-keys">↑ ↓ move · Enter pick · Esc close</span>
        </div>
      </div>
    </div>
  );
}
