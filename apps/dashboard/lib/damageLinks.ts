/**
 * Contract 9 (contract9.md sections 7.2-7.3): the links behind the 3D damage
 * map. Every finding (lib/findings.ts) gets its place on the model (zones,
 * area of the car, "inside the door", "?" for an unknown side) and its
 * photos; every zone, area and photo gets the findings on it. The map, its
 * photo strip, its selection card and the area chips all read from here, so
 * the four agree.
 *
 * Pure: no React, no three.js, type-only imports apart from lib/vehicleZones
 * (itself pure), so scripts/check-damage-links.mjs runs it under Node.
 */
import type { Box } from "./api";
import type { Finding, FindingStatus, Selection } from "./findings";
import { dominantSide, photoAreas, standInFor, zoneArea, zoneKeysFor, zoneLabel, type MapArea } from "./vehicleZones";

/** Where one finding sits on the map. */
export type FindingPlace = {
  key: string;
  /** The zones it marks: one, several when its side is unknown, none when the
   * model has no place for it ("Not on model"). */
  zones: string[];
  sideUnknown: boolean;
  /** Where the part really is when its zone is a nearby panel ("inside the door"). */
  standIn: string | null;
  /** The areas its zones are in; ["none"] when it has no zone. */
  areas: MapArea[];
  /** The damage list's row number (item index + 1); null for a suggestion. */
  number: number | null;
};

/** One finding's proof on one photo, as the map's photo draws it. */
export type PhotoMark = {
  key: string;
  box: Box | null;
  kind: "ai" | "sort" | "part" | null;
  frame: { w: number; h: number } | null;
};

export type DamageLinks = {
  places: Map<string, FindingPlace>;
  /** zone -> finding keys, list items first then suggestions (findings' order). */
  byZone: Map<string, string[]>;
  /** area -> finding keys ("none": not on the model). */
  byArea: Map<MapArea, string[]>;
  /** finding key -> its photos: the evidence's (best first), then the other
   * seen_in photos, each once. */
  photosOf: Map<string, string[]>;
  /** photo -> the findings that name it, one mark each (box or not). A
   * linked part is not marked on its listed part's photo (by "link"). */
  marksOn: Map<string, PhotoMark[]>;
  /** Contract 10: listed part's finding key -> its linked parts' keys, and
   * a linked part's key -> its listed parts' keys (both in findings order). */
  childrenOf: Map<string, string[]>;
  parentsOf: Map<string, string[]>;
};

/** The same photo whether a finding says "@<id>" or "P07": the page's
 * PhotoLookup turns either into the one ref the strip uses. */
export type CanonRef = (ref: string) => string;
const sameRef: CanonRef = (ref) => ref;

/** "P7", "p07" -> "P07"; anything else as it is. */
export function normalLabel(ref: string): string {
  const m = /^p0*(\d+)$/i.exec(ref.trim());
  return m ? `P${m[1].padStart(2, "0")}` : ref.trim();
}

/** "P07" -> 6 (the case's photo_urls index); -1 for anything else. */
export function labelIndex(ref: string | null | undefined): number {
  const m = /^p0*(\d+)$/i.exec((ref || "").trim());
  return m ? Number(m[1]) - 1 : -1;
}

/** "P07" for index 6. */
export const indexLabel = (index: number) => `P${String(index + 1).padStart(2, "0")}`;

/** "03" for a list row, "" for a suggestion. */
export function findingNumber(place: FindingPlace | undefined): string {
  return place?.number ? String(place.number).padStart(2, "0") : "";
}

function push<K, V>(map: Map<K, V[]>, key: K, value: V) {
  const list = map.get(key);
  if (list) list.push(value);
  else map.set(key, [value]);
}

/** Builds every link once per findings array. The damage list's parts
 * share one side hint (vehicleZones.resolveZones' rule), so a plain
 * "Fender" on a report of right-side parts lands where the table's badge
 * says; suggestions are canonical names and need none. */
export function buildLinks(findings: Finding[], canon: CanonRef = sameRef): DamageLinks {
  const hint = dominantSide(findings.filter((f) => f.kind === "item").map((f) => f.part));
  const places = new Map<string, FindingPlace>();
  const byZone = new Map<string, string[]>();
  const byArea = new Map<MapArea, string[]>();
  const photosOf = new Map<string, string[]>();
  const marksOn = new Map<string, PhotoMark[]>();
  const childrenOf = new Map<string, string[]>();
  const parentsOf = new Map<string, string[]>();
  // The listed part a linked part names in linked_from is the first list
  // item with that key (lib/findings parentsOf).
  const listedByKey = new Map<string, string>();
  for (const f of findings) if (f.kind === "item" && !listedByKey.has(f.partKey)) listedByKey.set(f.partKey, f.key);

  for (const f of findings) {
    const match = zoneKeysFor(f.part, f.kind === "item" ? hint : undefined);
    const zones = match?.keys ?? [];
    const areas = zones.length ? [...new Set(zones.map(zoneArea))] : (["none"] as MapArea[]);
    places.set(f.key, {
      key: f.key,
      zones,
      sideUnknown: Boolean(match?.sideUnknown),
      standIn: zones.length ? standInFor(f.part) : null,
      areas,
      number: f.kind === "item" && f.index !== null ? f.index + 1 : null,
    });
    for (const zone of zones) push(byZone, zone, f.key);
    for (const area of areas) push(byArea, area, f.key);

    for (const parentKey of f.linkedFrom ?? []) {
      const parent = listedByKey.get(parentKey);
      if (!parent || parent === f.key) continue;
      push(childrenOf, parent, f.key);
      push(parentsOf, f.key, parent);
    }

    const refs: string[] = [];
    for (const e of f.evidence) {
      const ref = canon(e.photo);
      if (refs.includes(ref)) continue;
      refs.push(ref);
      // The listed part's photo, shown for context: the linked part isn't on
      // it, so it is in the part's photos but not marked on the photo.
      if (e.by === "link") continue;
      push(marksOn, ref, {
        key: f.key,
        box: e.box,
        kind: e.box ? e.box_kind ?? "ai" : null,
        frame: e.img_w && e.img_h ? { w: e.img_w, h: e.img_h } : null,
      });
    }
    for (const r of f.seenIn) {
      const ref = canon(r);
      if (refs.includes(ref)) continue;
      refs.push(ref);
      push(marksOn, ref, { key: f.key, box: null, kind: null, frame: null });
    }
    photosOf.set(f.key, refs);
  }
  return { places, byZone, byArea, photosOf, marksOn, childrenOf, parentsOf };
}

/** Contract 10: what a selected finding is linked to on the map -- a listed
 * part's linked parts, or a linked part's listed parts -- among the findings
 * shown. The map outlines their zones (never moving the camera). */
export function linkedKeys(sel: Selection, links: DamageLinks, shown?: Set<string>): string[] {
  if (sel?.kind !== "finding") return [];
  const keys = [...(links.childrenOf.get(sel.key) ?? []), ...(links.parentsOf.get(sel.key) ?? [])];
  return shown ? keys.filter((k) => shown.has(k)) : keys;
}

/** The zones to outline for a selection's linked findings, without the
 * selection's own zones (those are outlined already). */
export function linkedZones(sel: Selection, links: DamageLinks, shown?: Set<string>): string[] {
  const own = new Set(selectionZones(sel, links));
  const out: string[] = [];
  for (const key of linkedKeys(sel, links, shown)) {
    for (const z of links.places.get(key)?.zones ?? []) if (!own.has(z) && !out.includes(z)) out.push(z);
  }
  return out;
}

// ---------------------------------------------------------------------------
// What the 3D model draws per zone

/** One zone's look on the model. `lead` picks the pattern: a solid fill when
 * every listed part on it is confirmed, a lighter one while any is still the
 * AI's ("check" adds "!"), stripes when it holds suggestions only, a grey
 * outline when only dismissed ones are shown. */
export type ZoneMark = {
  zone: string;
  /** The findings on it, in the findings' order. */
  keys: string[];
  lead: FindingStatus;
  listed: number;
  suggested: number;
  dismissed: number;
  /** The row number when exactly one listed part is on it. */
  number: number | null;
  /** Every finding on it might be on the other side instead ("?"). */
  sideUnknown: boolean;
  /** Every finding on it is a part inside / behind it (◇). */
  standIn: boolean;
  /** Contract 10: hidden linked parts (suggestions) on it, to confirm at
   * strip-down; the label says so ("1 hidden part to confirm at strip-down")
   * and the map draws the marker's ring dashed while a listed part with
   * linked parts is selected (globals.css, Contract 10 U). */
  hidden: number;
  /** For a screen reader and a tooltip: "Left Front Door: 1 confirmed, 1 suggestion". */
  label: string;
};

/** The words a zone's label ends with when hidden linked parts are on it
 * (the CSS finds the marker by them: the marker isn't this file's). */
export const HIDDEN_LABEL = "to confirm at strip-down";

const STATUS_ORDER: FindingStatus[] = ["confirmed", "check", "ai", "suggestion", "dismissed"];

const STATUS_WORDS: Record<FindingStatus, [string, string]> = {
  confirmed: ["confirmed", "confirmed"],
  ai: ["AI, not checked", "AI, not checked"],
  check: ["to check", "to check"],
  suggestion: ["suggestion", "suggestions"],
  dismissed: ["not damaged", "not damaged"],
};

/** The words for a status, as the legend and the card say them. */
export const STATUS_LABELS: Record<FindingStatus, string> = {
  confirmed: "Confirmed",
  ai: "AI, not checked",
  check: "Needs a check",
  suggestion: "Suggestion",
  dismissed: "Not damaged",
};

/** The glyph that goes with each status, so it never rests on colour alone. */
export const STATUS_GLYPHS: Record<FindingStatus, string> = {
  confirmed: "✓",
  ai: "",
  check: "!",
  suggestion: "+",
  dismissed: "×",
};

export function zoneMarks(findings: Finding[], links: DamageLinks): ZoneMark[] {
  const byKey = new Map(findings.map((f) => [f.key, f]));
  const out: ZoneMark[] = [];
  for (const [zone, keys] of links.byZone) {
    const shown = keys.map((k) => byKey.get(k)).filter((f): f is Finding => Boolean(f));
    if (!shown.length) continue;
    const listed = shown.filter((f) => f.kind === "item");
    const suggested = shown.filter((f) => f.status === "suggestion");
    const dismissed = shown.filter((f) => f.status === "dismissed");
    let lead: FindingStatus;
    if (listed.length) {
      lead = listed.every((f) => f.status === "confirmed") ? "confirmed" : listed.some((f) => f.status === "check") ? "check" : "ai";
    } else lead = suggested.length ? "suggestion" : "dismissed";

    const counts = new Map<FindingStatus, number>();
    for (const f of shown) counts.set(f.status, (counts.get(f.status) ?? 0) + 1);
    const words = STATUS_ORDER.filter((s) => counts.get(s)).map((s) => {
      const n = counts.get(s)!;
      return `${n} ${STATUS_WORDS[s][n === 1 ? 0 : 1]}`;
    });
    const places = shown.map((f) => links.places.get(f.key)!);
    const hidden = suggested.filter((f) => f.hidden).length;
    out.push({
      zone,
      keys: shown.map((f) => f.key),
      lead,
      listed: listed.length,
      suggested: suggested.length,
      dismissed: dismissed.length,
      number: listed.length === 1 ? links.places.get(listed[0].key)?.number ?? null : null,
      sideUnknown: places.every((p) => p.sideUnknown),
      standIn: places.every((p) => Boolean(p.standIn)),
      hidden,
      label: `${zoneLabel(zone)}: ${words.join(", ")}${hidden ? `; ${hidden} hidden part${hidden === 1 ? "" : "s"} ${HIDDEN_LABEL}` : ""}`,
    });
  }
  return out;
}

/** A content key for zone marks: the 3D model repaints only when it changes,
 * not on every new findings array (a verified tick elsewhere, an autosave). */
export function marksSignature(marks: ZoneMark[]): string {
  return marks.map((m) => `${m.zone}:${m.lead}:${m.listed}:${m.suggested}:${m.dismissed}:${m.number ?? ""}:${m.sideUnknown ? 1 : 0}:${m.hidden}`).join("|");
}

// ---------------------------------------------------------------------------
// Selection -> what lights up

export function sameSelection(a: Selection, b: Selection): boolean {
  if (a === b) return true;
  if (!a || !b || a.kind !== b.kind) return false;
  if (a.kind === "finding") return a.key === (b as { key: string }).key;
  if (a.kind === "zone") return a.zoneKey === (b as { zoneKey: string }).zoneKey;
  return a.ref === (b as { ref: string }).ref;
}

/** The zones a selection (or hover) lights: a finding's zones; the zone;
 * every zone of a finding on the photo. */
export function selectionZones(sel: Selection, links: DamageLinks, canon: CanonRef = sameRef): string[] {
  if (!sel) return [];
  if (sel.kind === "finding") return links.places.get(sel.key)?.zones ?? [];
  if (sel.kind === "zone") return [sel.zoneKey];
  const zones = new Set<string>();
  for (const m of links.marksOn.get(canon(sel.ref)) ?? []) for (const z of links.places.get(m.key)?.zones ?? []) zones.add(z);
  return [...zones];
}

/** The findings a selection is about: the finding; the zone's; the photo's. */
export function selectionFindings(sel: Selection, links: DamageLinks, canon: CanonRef = sameRef): string[] {
  if (!sel) return [];
  if (sel.kind === "finding") return links.places.has(sel.key) ? [sel.key] : [];
  if (sel.kind === "zone") return links.byZone.get(sel.zoneKey) ?? [];
  return (links.marksOn.get(canon(sel.ref)) ?? []).map((m) => m.key);
}

/** Should the camera fly for this selection? A photo only outlines. */
export function selectionMovesCamera(sel: Selection): boolean {
  return Boolean(sel && sel.kind !== "photo");
}

/** A selection (or hover) after damage-list rows went (an undone add takes
 * its item off): an "item:<n>" names a row by its place, so one below them
 * moves up, one on them is dropped, and anything else stays as it is (the
 * same object, so nothing re-renders or moves the camera). */
export function afterItemsRemoved(sel: Selection, removed: number[]): Selection {
  if (sel?.kind !== "finding" || !sel.key.startsWith("item:") || !removed.length) return sel;
  const at = Number(sel.key.slice("item:".length));
  if (!Number.isInteger(at)) return sel;
  if (removed.includes(at)) return null;
  const shift = removed.filter((i) => i < at).length;
  return shift ? { kind: "finding", key: `item:${at - shift}` } : sel;
}

// ---------------------------------------------------------------------------
// The photo strip

export type StripFilter = {
  /** The photos to show, in case order (a finding's: best first). */
  refs: string[];
  /** What they are, for the strip's label: "Left Front Door", "Rear", null for all. */
  title: string | null;
  /** The photos of the side / end when the selection has none of its own. */
  fallback: { area: MapArea; refs: string[] } | null;
};

/** The photos of an area: those the findings there name, and those the
 * sorter took of it (photoAreas), in case order. */
export function areaPhotos(
  area: MapArea,
  links: DamageLinks,
  photoOrder: string[],
  areaOf: Record<string, string | null | undefined>,
): string[] {
  const want = new Set<string>();
  for (const key of links.byArea.get(area) ?? []) for (const ref of links.photosOf.get(key) ?? []) want.add(ref);
  return photoOrder.filter((ref) => want.has(ref) || photoAreas(areaOf[ref]).includes(area));
}

/** What the strip shows: a finding's photos (best first); a zone's findings'
 * photos (or none, with its side's photos as the fallback); the area chip's
 * photos; else all of them. A photo selection keeps the whole strip. */
export function stripFilter(
  sel: Selection,
  area: MapArea | null,
  links: DamageLinks,
  photoOrder: string[],
  areaOf: Record<string, string | null | undefined>,
  names: (key: string) => string,
): StripFilter {
  if (sel?.kind === "finding" && links.places.has(sel.key)) {
    const refs = links.photosOf.get(sel.key) ?? [];
    const fallbackArea = links.places.get(sel.key)!.areas[0];
    return {
      refs,
      title: names(sel.key),
      fallback: refs.length || fallbackArea === "none" ? null : { area: fallbackArea, refs: areaPhotos(fallbackArea, links, photoOrder, areaOf) },
    };
  }
  if (sel?.kind === "zone") {
    const want = new Set<string>();
    for (const key of links.byZone.get(sel.zoneKey) ?? []) for (const ref of links.photosOf.get(key) ?? []) want.add(ref);
    const refs = photoOrder.filter((r) => want.has(r));
    const zoneSide = zoneArea(sel.zoneKey);
    return {
      refs,
      title: zoneLabel(sel.zoneKey),
      fallback: refs.length || zoneSide === "none" ? null : { area: zoneSide, refs: areaPhotos(zoneSide, links, photoOrder, areaOf) },
    };
  }
  if (area) return { refs: areaPhotos(area, links, photoOrder, areaOf), title: null, fallback: null };
  return { refs: photoOrder, title: null, fallback: null };
}

/** The photo the map shows for a selection: the finding's best; the photo;
 * the zone's first finding's best; else the first photo with a finding on
 * it, else the first photo. */
export function photoForSelection(sel: Selection, links: DamageLinks, photoOrder: string[], canon: CanonRef = sameRef): string | null {
  if (sel?.kind === "photo") return canon(sel.ref);
  if (sel?.kind === "finding") return links.photosOf.get(sel.key)?.[0] ?? null;
  if (sel?.kind === "zone") {
    for (const key of links.byZone.get(sel.zoneKey) ?? []) {
      const ref = links.photosOf.get(key)?.[0];
      if (ref) return ref;
    }
    return null;
  }
  return photoOrder.find((r) => links.marksOn.get(r)?.some((m) => m.box)) ?? photoOrder.find((r) => links.marksOn.has(r)) ?? photoOrder[0] ?? null;
}

/** Findings per area chip (a side-unknown finding counts on both sides), and "All". */
export function areaCounts(findings: Finding[], links: DamageLinks): { all: number; byArea: Map<MapArea, number> } {
  const shown = new Set(findings.map((f) => f.key));
  const byArea = new Map<MapArea, number>();
  for (const [area, keys] of links.byArea) byArea.set(area, keys.filter((k) => shown.has(k)).length);
  return { all: shown.size, byArea };
}
