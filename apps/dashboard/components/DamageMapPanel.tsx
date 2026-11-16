"use client";

import dynamic from "next/dynamic";
import { Component, memo, useCallback, useEffect, useMemo, useRef, useState, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, Maximize2, Move3d, Plus, RotateCcw, X } from "lucide-react";
import type { DecideAction, DecideResult } from "@/lib/api";
import { codeReason, type Finding, type FindingStatus, type PhotoLookup, type Selection } from "@/lib/findings";
import { PhotoBoxes, type BoxMark } from "@/components/PhotoBoxes";
import {
  AREA_LABELS,
  ARCHETYPES,
  ARCHETYPE_LABELS,
  MAP_AREAS,
  detectBodyType,
  partForZone,
  zoneLabel,
  type MapArea,
} from "@/lib/vehicleZones";
import {
  STATUS_GLYPHS,
  STATUS_LABELS,
  areaCounts,
  buildLinks,
  findingNumber,
  marksSignature,
  photoForSelection,
  selectionFindings,
  selectionMovesCamera,
  selectionZones,
  stripFilter,
  zoneMarks,
  type DamageLinks,
} from "@/lib/damageLinks";
import type { BlueprintView, CameraRequest } from "./VehicleBlueprint3D";

// Three.js/WebGL only exists client-side, and is heavy: loaded after mount,
// and only once the map is scrolled into view (MapStage below).
const DamageBlueprint3D = dynamic(() => import("./VehicleBlueprint3D").then((m) => m.DamageBlueprint3D), {
  ssr: false,
  loading: () => <div className="blueprint-stage-3d blueprint-3d-loading">Loading the 3D model…</div>,
});

export type DamageMapPanelProps = {
  surface: "intake" | "case";
  /** Every finding to show (lib/findings buildFindings). Built with
   * showDismissed, the panel's "Show dismissed" can bring those back. */
  findings: Finding[];
  photos: PhotoLookup;
  /** Photo refs in case order (P01 first). */
  photoOrder: string[];
  /** ref -> the sorter's area (data.photo_index[i].area on a filed case). */
  photoAreas: Record<string, string | null>;
  /** The model to draw: a body type ("Sedan", "SUV", ...) or one of the five
   * models' names; anything else draws the sedan. A page that knows the
   * make/model passes vehicleZones.detectBodyType(vehicle, stated). */
  bodyType: string | null;
  onBodyTypeChange?: (b: string) => void;
  selection: Selection;
  onSelect: (s: Selection) => void;
  hover: Selection;
  onHover: (s: Selection) => void;
  /** key: the finding's key ("item:3", "sug:<key>"), or "" to open a photo
   * on its own; photo: the ref to start on. */
  onOpenEvidence: (key: string, photo?: string) => void;
  /** [Tick to add] / [Dismiss] / [Undo] on the mesh's selection card. key:
   * the suggestion's own key (Finding.partKey) -- what the decide endpoint
   * takes, so useSuggestionDecisions' decide can be passed straight on. */
  onDecide?: (key: string, action: DecideAction) => Promise<DecideResult | void>;
  onAddDamage?: (zoneKey: string) => void;
  readOnly?: boolean;
};

/** Scrolls the page's damage map into view (a [Map] / [Show on 3D] button
 * elsewhere on the page selects the part, then calls this). */
export function revealDamageMap() {
  if (typeof document === "undefined") return;
  document.querySelector("[data-damage-map]")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ---------------------------------------------------------------------------

interface BlueprintErrorBoundaryProps {
  children: ReactNode;
}

interface BlueprintErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

/** A device that can't draw WebGL gets a message, not a broken page: the
 * findings are still in the card, the chips and the table. */
export class BlueprintErrorBoundary extends Component<BlueprintErrorBoundaryProps, BlueprintErrorBoundaryState> {
  constructor(props: BlueprintErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): BlueprintErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("BlueprintErrorBoundary caught a WebGL/Three.js render exception:", error, errorInfo);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="blueprint-stage-3d dmap-stage-error">
          <AlertTriangle style={{ width: 20, height: 20, color: "#ef4444" }} />
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>3D model unavailable</div>
          <p style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5, margin: 0, maxWidth: 360 }}>
            The 3D model can&apos;t be drawn on this device. The parts are in the list and the table.
          </p>
          <button type="button" onClick={this.handleRetry} className="btn-secondary-modern" style={{ fontSize: 11, padding: "6px 14px" }}>
            <RotateCcw style={{ width: 12, height: 12 }} /> Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

// ---------------------------------------------------------------------------

const VIEWS: { view: BlueprintView; label: string; title: string }[] = [
  { view: "front", label: "Front", title: "Look at the front (the car's left is on your right)" },
  { view: "rear", label: "Rear", title: "Look at the rear" },
  { view: "lh", label: "LH", title: "Look at the left-hand side" },
  { view: "rh", label: "RH", title: "Look at the right-hand side" },
  { view: "top", label: "Top", title: "Look down on the roof (front at the top)" },
];

const AREA_VIEW: Partial<Record<MapArea, BlueprintView>> = { front: "front", rear: "rear", lh: "lh", rh: "rh", top: "top" };

const LEGEND: FindingStatus[] = ["confirmed", "ai", "check", "suggestion", "dismissed"];

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Lazy mount: the 3D model is fetched and drawn only once the map has
 * been scrolled near the screen; then it stays (a later scroll away only
 * stops its frames). */
function MapStage({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    if (typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setSeen(true);
      },
      { rootMargin: "300px 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [seen]);
  return (
    <div ref={ref} className="dmap-stage-slot">
      {seen ? children : <div className="blueprint-stage-3d blueprint-3d-loading">3D model</div>}
    </div>
  );
}

/** True on a touch-first device: the map then doesn't take one-finger drags
 * (the page scrolls) until the person taps [Rotate]. */
function useCoarsePointer(): boolean {
  const [coarse, setCoarse] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(pointer: coarse)");
    const read = () => setCoarse(mq.matches);
    read();
    mq.addEventListener?.("change", read);
    return () => mq.removeEventListener?.("change", read);
  }, []);
  return coarse;
}

/** The 3D damage map with its photo, chips, strip and selection card, linked
 * both ways to the findings (contract9.md section 7). One selection and one
 * hover for the whole page come in from useDamageSelection: a click on the
 * model, a marker, a chip row, a box on the photo or a strip photo sets it,
 * and the table / cards / viewer follow -- and the other way round. A click
 * moves the camera; a hover only outlines. Status is a pattern plus a glyph
 * everywhere (solid ✓, light, light !, striped +, grey ×), never colour
 * alone. */
export function DamageMapPanel({
  surface,
  findings,
  photos,
  photoOrder,
  photoAreas,
  bodyType,
  onBodyTypeChange,
  selection,
  onSelect,
  hover,
  onHover,
  onOpenEvidence,
  onDecide,
  onAddDamage,
  readOnly,
}: DamageMapPanelProps) {
  const archetype = detectBodyType("", bodyType);
  const [showDismissed, setShowDismissed] = useState(false);
  const [area, setArea] = useState<MapArea | null>(null);
  const [activePhoto, setActivePhoto] = useState<string | null>(null);
  const [camera, setCamera] = useState<CameraRequest | null>(null);
  const [approxZone, setApproxZone] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [rotate, setRotate] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [decideError, setDecideError] = useState<string | null>(null);
  const coarse = useCoarsePointer();

  // One ref per photo whichever way a finding names it ("@<id>" or "P07").
  const canon = useCallback((ref: string) => photos(ref)?.ref ?? ref, [photos]);
  const links = useMemo(() => buildLinks(findings, canon), [findings, canon]);
  const shown = useMemo(() => (showDismissed ? findings : findings.filter((f) => f.status !== "dismissed")), [findings, showDismissed]);
  const byKey = useMemo(() => new Map(findings.map((f) => [f.key, f])), [findings]);
  const dismissedCount = findings.length - findings.filter((f) => f.status !== "dismissed").length;

  // The model repaints only when what it draws changes: a new findings
  // array from an autosave or a tick elsewhere keeps the marks' identity.
  const freshMarks = useMemo(() => zoneMarks(shown, links), [shown, links]);
  const signature = marksSignature(freshMarks);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the content on purpose
  const marks = useMemo(() => freshMarks, [signature]);

  const selZonesKey = selectionZones(selection, links, canon).join(",");
  const hoverZonesKey = selectionZones(hover, links, canon).join(",");
  const selectedZones = useMemo(() => (selZonesKey ? selZonesKey.split(",") : []), [selZonesKey]);
  const hoverZones = useMemo(() => (hoverZonesKey ? hoverZonesKey.split(",") : []), [hoverZonesKey]);

  // A box clicked on the photo shown: the finding it selects keeps that
  // photo (its own best photo may be another one).
  const keepPhoto = useRef<{ key: string; photo: string } | null>(null);

  // A new selection (any source, even the same part again) moves the
  // camera to it; a photo only outlines. Keyed on the selection object, so
  // a re-render with the same selection never moves anything.
  const lastSelection = useRef<Selection>(null);
  useEffect(() => {
    if (selection === lastSelection.current) return;
    lastSelection.current = selection;
    const kept = keepPhoto.current;
    keepPhoto.current = null;
    setActivePhoto(kept && selection?.kind === "finding" && selection.key === kept.key ? kept.photo : null);
    if (selection && selection.kind !== "zone") setApproxZone(null);
    if (!selectionMovesCamera(selection)) return;
    const zones = selectionZones(selection, links, canon);
    if (zones.length) setCamera((c) => ({ kind: "zones", zones, n: (c?.n ?? 0) + 1 }));
  }, [selection, links, canon]);

  const view = (v: BlueprintView) => setCamera((c) => ({ kind: "view", view: v, n: (c?.n ?? 0) + 1 }));

  const nameOf = useCallback((key: string) => byKey.get(key)?.name ?? key, [byKey]);
  const strip = useMemo(() => stripFilter(selection, area, links, photoOrder, photoAreas, nameOf), [selection, area, links, photoOrder, photoAreas, nameOf]);
  const counts = useMemo(() => areaCounts(shown, links), [shown, links]);

  const photoRef = activePhoto ?? (selection || !area ? photoForSelection(selection, links, photoOrder, canon) : strip.refs[0] ?? null);
  const photo = photoRef ? photos(photoRef) : null;
  const selectedKey = selection?.kind === "finding" ? selection.key : null;
  const hoverKey = hover?.kind === "finding" ? hover.key : null;
  const shownKeys = useMemo(() => new Set(shown.map((f) => f.key)), [shown]);
  const photoMarks: BoxMark[] = useMemo(() => {
    if (!photoRef) return [];
    return (links.marksOn.get(photoRef) ?? [])
      .filter((m) => shownKeys.has(m.key))
      .map((m) => {
        const f = byKey.get(m.key)!;
        const no = findingNumber(links.places.get(m.key));
        return { key: m.key, label: `${no || STATUS_GLYPHS[f.status] || ""} ${f.name}`.trim(), status: f.status, box: m.box, kind: m.kind, frame: m.frame };
      });
  }, [photoRef, links, byKey, shownKeys]);
  const unboxedSelected = selectedKey ? photoMarks.find((m) => m.key === selectedKey && !m.box) : undefined;

  // ---- the card: what the selection (or the area chip) is about
  const card = useMemo(() => {
    if (selection?.kind === "finding") {
      const f = byKey.get(selection.key);
      if (!f) return null;
      const others = new Set<string>();
      for (const z of links.places.get(f.key)?.zones ?? []) for (const k of links.byZone.get(z) ?? []) if (k !== f.key && shownKeys.has(k)) others.add(k);
      return { title: f.name, approx: false, zone: null as string | null, keys: [f.key, ...others], empty: false };
    }
    if (selection?.kind === "zone") {
      const keys = (links.byZone.get(selection.zoneKey) ?? []).filter((k) => shownKeys.has(k));
      return { title: zoneLabel(selection.zoneKey), approx: approxZone === selection.zoneKey, zone: selection.zoneKey, keys, empty: keys.length === 0 };
    }
    if (selection?.kind === "photo") {
      const keys = selectionFindings(selection, links, canon).filter((k) => shownKeys.has(k));
      return { title: `Photo ${photos(selection.ref)?.label ?? selection.ref}`, approx: false, zone: null, keys, empty: keys.length === 0 };
    }
    if (area) {
      const keys = (links.byArea.get(area) ?? []).filter((k) => shownKeys.has(k));
      return { title: AREA_LABELS[area], approx: false, zone: null, keys, empty: keys.length === 0 };
    }
    return null;
  }, [selection, area, byKey, links, shownKeys, approxZone, canon, photos]);

  const listedCount = findings.filter((f) => f.kind === "item").length;
  const suggestionCount = findings.filter((f) => f.status === "suggestion").length;

  const pickArea = (a: MapArea | null) => {
    const next = area === a ? null : a;
    setArea(next);
    setActivePhoto(null);
    if (selection) onSelect(null);
    const v = next ? AREA_VIEW[next] : "home";
    if (v) view(v);
  };

  const pickPhoto = (ref: string) => {
    // Inside a part's photos the part stays selected; otherwise the photo
    // becomes the selection, and the model outlines what it shows.
    if (selection?.kind === "finding" || selection?.kind === "zone" || area) setActivePhoto(ref);
    else onSelect({ kind: "photo", ref });
  };

  // One function for every strip photo, so a selection re-renders only the
  // photos whose look changed (StripThumb is memoised).
  const pickRef = useRef(pickPhoto);
  pickRef.current = pickPhoto;
  const onPick = useCallback((ref: string) => pickRef.current(ref), []);

  const decide = async (f: Finding, action: DecideAction) => {
    if (!onDecide) return;
    setBusyKey(f.key);
    setDecideError(null);
    try {
      await onDecide(f.partKey, action);
    } catch (err) {
      setDecideError(err instanceof Error ? err.message : "Couldn't save that. Try again.");
    } finally {
      setBusyKey(null);
    }
  };

  const hasSelection = Boolean(selection) || Boolean(area);
  const showRotate = coarse;
  const fallback = strip.fallback;

  return (
    <section
      className={`dmap dmap-${surface} ${hasSelection ? "has-selection" : ""} ${collapsed ? "is-collapsed" : ""}`}
      data-damage-map
      aria-label="Damage map"
      onKeyDown={(e) => {
        if (e.key === "Escape" && (selection || area)) {
          e.stopPropagation();
          setArea(null);
          onSelect(null);
        }
      }}
    >
      <header className="dmap-head">
        <button type="button" className="dmap-collapse" aria-expanded={!collapsed} onClick={() => setCollapsed((c) => !c)}>
          {collapsed ? <ChevronRight style={{ width: 16, height: 16 }} /> : <ChevronDown style={{ width: 16, height: 16 }} />}
          <h2>Damage map</h2>
        </button>
        <span className="dmap-head-count">
          {plural(listedCount, "part")} listed
          {suggestionCount > 0 ? ` · ${plural(suggestionCount, "suggestion")}` : ""}
        </span>
        <label className="dmap-body">
          Body
          {onBodyTypeChange && !readOnly ? (
            <select value={archetype} onChange={(e) => onBodyTypeChange(ARCHETYPE_LABELS[e.target.value as keyof typeof ARCHETYPE_LABELS])} aria-label="Body type (picks the 3D model)">
              {ARCHETYPES.map((a) => (
                <option key={a} value={a}>
                  {ARCHETYPE_LABELS[a]}
                </option>
              ))}
            </select>
          ) : (
            <strong>{ARCHETYPE_LABELS[archetype]}</strong>
          )}
        </label>
      </header>

      {!collapsed && (
        <div className="dmap-grid">
          <div className="dmap-stage">
            <MapStage>
              <BlueprintErrorBoundary>
                <DamageBlueprint3D
                  archetype={archetype}
                  marks={marks}
                  selectedZones={selectedZones}
                  hoverZones={hoverZones}
                  camera={camera}
                  interactive={!coarse || rotate}
                  onZoneClick={(zone, approx) => {
                    setApproxZone(approx ? zone : null);
                    setArea(null);
                    onSelect({ kind: "zone", zoneKey: zone });
                  }}
                  onZoneHover={(zone) => onHover(zone ? { kind: "zone", zoneKey: zone } : null)}
                />
              </BlueprintErrorBoundary>
            </MapStage>
            {showRotate && (
              <button type="button" className={`dmap-rotate ${rotate ? "is-on" : ""}`} aria-pressed={rotate} onClick={() => setRotate((r) => !r)}>
                <Move3d style={{ width: 13, height: 13 }} /> {rotate ? "Rotate on" : "Rotate"}
              </button>
            )}
          </div>

          <div className="dmap-detail">
            <div className="dmap-photo">
              {photoRef && photo && !photo.deleted ? (
                <PhotoBoxes
                  src={photo.src}
                  alt={`Photo ${photo.label}`}
                  marks={photoMarks}
                  selectedKey={selectedKey}
                  hoverKey={hoverKey}
                  onSelectMark={(key) => {
                    keepPhoto.current = { key, photo: photoRef };
                    onSelect({ kind: "finding", key });
                  }}
                  onHoverMark={(key) => onHover(key ? { kind: "finding", key } : null)}
                  fit="contain"
                  className="dmap-photo-img"
                />
              ) : (
                <div className="dmap-photo-empty">
                  {photoRef && photo?.deleted
                    ? `Photo ${photo.label} was deleted`
                    : photoRef
                      ? "This photo isn't on the case any more"
                      : card?.empty
                        ? "No damage photos linked"
                        : "No photo"}
                </div>
              )}
              <div className="dmap-photo-bar">
                <span className="dmap-photo-label">{photo ? `Photo ${photo.label}` : ""}</span>
                <span className="dmap-photo-note">
                  {unboxedSelected
                    ? "No box: the AI named the part but didn't mark where"
                    : photoMarks.length
                      ? plural(photoMarks.length, "finding")
                      : photo
                        ? "No finding on this photo"
                        : ""}
                </span>
                {photoRef && photo && (
                  <button
                    type="button"
                    className="btn-secondary-modern dmap-small-btn"
                    onClick={() => onOpenEvidence(selectedKey ?? photoMarks[0]?.key ?? "", photoRef)}
                    title="Open the photo full size with its boxes"
                  >
                    <Maximize2 style={{ width: 12, height: 12 }} /> Open
                  </button>
                )}
              </div>
            </div>

            {card && (
              <div className="dmap-card" aria-live="polite">
                <div className="dmap-card-head">
                  <h3>
                    {card.title}
                    {card.approx && <span className="dmap-approx"> (approx.)</span>}
                  </h3>
                  <span className="dmap-card-count">
                    {card.empty ? "Not detected" : cardCounts(card.keys, byKey)}
                  </span>
                  <button
                    type="button"
                    className="dmap-card-close"
                    aria-label="Close"
                    onClick={() => {
                      setArea(null);
                      onSelect(null);
                    }}
                  >
                    <X style={{ width: 14, height: 14 }} />
                  </button>
                </div>
                {card.empty ? (
                  <div className="dmap-card-empty">
                    <p>{selection?.kind === "photo" ? "No finding names this photo." : "No damage photos linked."}</p>
                    {fallback && fallback.refs.length > 0 && (
                      <p>
                        {AREA_LABELS[fallback.area]} photos: {fallback.refs.length}{" "}
                        <button type="button" className="dmap-link" onClick={() => pickArea(fallback.area)}>
                          Show
                        </button>
                      </p>
                    )}
                    {card.zone && onAddDamage && !readOnly && partForZone(card.zone) && (
                      <button type="button" className="btn-secondary-modern dmap-small-btn" onClick={() => onAddDamage(card.zone!)}>
                        <Plus style={{ width: 12, height: 12 }} /> Add as damage
                      </button>
                    )}
                  </div>
                ) : (
                  <ul className="dmap-rows">
                    {card.keys.map((key) => {
                      const f = byKey.get(key);
                      if (!f) return null;
                      return (
                        <FindingRow
                          key={key}
                          f={f}
                          links={links}
                          selected={selectedKey === key}
                          hovered={hoverKey === key}
                          busy={busyKey === key}
                          canDecide={Boolean(onDecide) && !readOnly}
                          onSelect={() => onSelect({ kind: "finding", key })}
                          onHover={(on) => onHover(on ? { kind: "finding", key } : null)}
                          onLook={() => onOpenEvidence(key, links.photosOf.get(key)?.[0])}
                          onDecide={(a) => void decide(f, a)}
                        />
                      );
                    })}
                  </ul>
                )}
                {decideError && <p className="dmap-error">{decideError}</p>}
              </div>
            )}
          </div>

          <div className="dmap-controls">
            <div className="dmap-chips" role="group" aria-label="Turn the model">
              <span className="dmap-chips-label">View</span>
              {VIEWS.map((v) => (
                <button key={v.view} type="button" className="dmap-chip" onClick={() => view(v.view)} title={v.title}>
                  {v.label}
                </button>
              ))}
              <button type="button" className="dmap-chip" onClick={() => view("home")} title="Back to the full view" aria-label="Back to the full view">
                <RotateCcw style={{ width: 12, height: 12 }} />
              </button>
            </div>
            <div className="dmap-chips" role="group" aria-label="Parts by area">
              <span className="dmap-chips-label">Area</span>
              <button type="button" className={`dmap-chip ${area === null && !selection ? "is-active" : ""}`} aria-pressed={area === null && !selection} onClick={() => pickArea(null)}>
                All <b>{counts.all}</b>
              </button>
              {MAP_AREAS.map((a) => {
                const n = counts.byArea.get(a) ?? 0;
                if (a === "none" && n === 0) return null;
                return (
                  <button key={a} type="button" className={`dmap-chip ${area === a ? "is-active" : ""} ${n === 0 ? "is-zero" : ""}`} aria-pressed={area === a} onClick={() => pickArea(a)}>
                    {AREA_LABELS[a]} <b>{n}</b>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="dmap-strip">
            <div className="dmap-strip-head">
              <span>
                {strip.title ? (
                  <>
                    Photos of <strong>{strip.title}</strong>: {strip.refs.length} of {photoOrder.length}
                  </>
                ) : area ? (
                  <>
                    {AREA_LABELS[area]} photos: {strip.refs.length} of {photoOrder.length}
                  </>
                ) : (
                  <>All photos: {photoOrder.length}</>
                )}
              </span>
              {(strip.title || area) && (
                <button
                  type="button"
                  className="dmap-link"
                  onClick={() => {
                    setArea(null);
                    if (selection && selection.kind !== "photo") onSelect(null);
                  }}
                >
                  All photos
                </button>
              )}
            </div>
            {strip.refs.length === 0 ? (
              <p className="dmap-strip-empty">No photos linked{fallback && fallback.refs.length ? ` · ${AREA_LABELS[fallback.area]} photos: ${fallback.refs.length}` : ""}</p>
            ) : (
              <div className="dmap-strip-row" role="list">
                {strip.refs.map((ref) => (
                  <StripThumb
                    key={ref}
                    info={photos(ref)}
                    refKey={ref}
                    active={ref === photoRef}
                    boxed={(links.marksOn.get(ref) ?? []).some((m) => m.box && shownKeys.has(m.key))}
                    linked={(links.marksOn.get(ref) ?? []).some((m) => shownKeys.has(m.key))}
                    onPick={onPick}
                  />
                ))}
              </div>
            )}
          </div>

          <div className="dmap-legend" aria-label="What the patterns mean">
            {LEGEND.map((s) =>
              s === "dismissed" && dismissedCount === 0 ? null : (
                <span key={s} className="dmap-legend-item">
                  <span className={`dmap-swatch is-${s}`} aria-hidden="true">
                    {STATUS_GLYPHS[s]}
                  </span>
                  {STATUS_LABELS[s]}
                </span>
              ),
            )}
            <span className="dmap-legend-item">
              <span className="dmap-swatch is-q" aria-hidden="true">
                ?
              </span>
              Side not known
            </span>
            <span className="dmap-legend-item">
              <span className="dmap-swatch is-in" aria-hidden="true">
                ◇
              </span>
              Inside / behind the panel
            </span>
            {dismissedCount > 0 && (
              <label className="dmap-legend-toggle">
                <input type="checkbox" checked={showDismissed} onChange={(e) => setShowDismissed(e.target.checked)} /> Show dismissed ({dismissedCount})
              </label>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

/** "1 found · 1 suggestion" for a card's findings. */
function cardCounts(keys: string[], byKey: Map<string, Finding>): string {
  const fs = keys.map((k) => byKey.get(k)).filter((f): f is Finding => Boolean(f));
  const found = fs.filter((f) => f.kind === "item").length;
  const sug = fs.filter((f) => f.status === "suggestion").length;
  const gone = fs.filter((f) => f.status === "dismissed").length;
  return [found ? `${found} found` : "", sug ? plural(sug, "suggestion") : "", gone ? `${gone} not damaged` : ""].filter(Boolean).join(" · ");
}

function FindingRow({
  f,
  links,
  selected,
  hovered,
  busy,
  canDecide,
  onSelect,
  onHover,
  onLook,
  onDecide,
}: {
  f: Finding;
  links: DamageLinks;
  selected: boolean;
  hovered: boolean;
  busy: boolean;
  canDecide: boolean;
  onSelect: () => void;
  onHover: (on: boolean) => void;
  onLook: () => void;
  onDecide: (a: DecideAction) => void;
}) {
  const place = links.places.get(f.key);
  const photoCount = links.photosOf.get(f.key)?.length ?? 0;
  const no = findingNumber(place);
  const where = place?.zones.length
    ? place.zones.length > 1
      ? `on ${place.zones.map(zoneLabel).join(" or ")}`
      : null
    : "not on the model";
  return (
    <li className={`dmap-row is-${f.status} ${selected ? "is-selected" : ""} ${hovered ? "is-hovered" : ""}`}>
      <button type="button" className="dmap-row-main" onClick={onSelect} onMouseEnter={() => onHover(true)} onMouseLeave={() => onHover(false)} aria-pressed={selected}>
        <span className={`dmap-badge is-${f.status}`} aria-hidden="true">
          {STATUS_GLYPHS[f.status]}
          {no}
        </span>
        <span className="dmap-row-name">{f.name}</span>
        <span className="dmap-row-status">{STATUS_LABELS[f.status]}</span>
        <span className="dmap-row-photos">{photoCount ? plural(photoCount, "photo") : "no photo"}</span>
      </button>
      {(place?.standIn || place?.sideUnknown || where) && (
        <p className="dmap-row-where">
          {place?.standIn && <span>◇ {place.standIn}</span>}
          {place?.sideUnknown && <span>? side not known</span>}
          {where && <span>{where}</span>}
        </p>
      )}
      <p className="dmap-row-reason">{f.reason ? `AI says: “${f.reason}”` : codeReason(f)}</p>
      <div className="dmap-row-actions">
        <button type="button" className="btn-secondary-modern dmap-small-btn" onClick={onLook} disabled={!photoCount}>
          Look
        </button>
        {canDecide && f.status === "suggestion" && (
          <>
            <button type="button" className="btn-primary-modern dmap-small-btn" disabled={busy} onClick={() => onDecide("add")}>
              Tick to add
            </button>
            <button type="button" className="btn-secondary-modern dmap-small-btn" disabled={busy} onClick={() => onDecide("dismiss")}>
              Dismiss
            </button>
          </>
        )}
        {canDecide && f.status === "dismissed" && (
          <button type="button" className="btn-secondary-modern dmap-small-btn" disabled={busy} onClick={() => onDecide("undo")}>
            Undo
          </button>
        )}
      </div>
    </li>
  );
}

const StripThumb = memo(function StripThumb({
  info,
  refKey,
  active,
  boxed,
  linked,
  onPick,
}: {
  info: ReturnType<PhotoLookup>;
  refKey: string;
  active: boolean;
  boxed: boolean;
  linked: boolean;
  onPick: (ref: string) => void;
}) {
  const label = info?.label ?? refKey;
  return (
    <button
      type="button"
      role="listitem"
      className={`dmap-thumb ${active ? "is-active" : ""} ${info?.deleted ? "is-deleted" : ""}`}
      onClick={() => onPick(refKey)}
      aria-current={active}
      aria-label={`${label}${boxed ? ", has a box" : linked ? ", linked to a finding" : ""}${info?.deleted ? ", deleted" : ""}`}
      title={label}
    >
      {info && !info.deleted ? (
        /* eslint-disable-next-line @next/next/no-img-element */
        <img src={info.thumb} alt="" loading="lazy" decoding="async" />
      ) : (
        <span className="dmap-thumb-gone">{info?.deleted ? "deleted" : "?"}</span>
      )}
      <span className="dmap-thumb-label">
        {label}
        {boxed ? " ▭" : ""}
      </span>
      {linked && <span className="dmap-thumb-dot" aria-hidden="true" />}
    </button>
  );
});
