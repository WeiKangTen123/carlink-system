/** The one search picker for adding a damaged part (L-library-cycle.md B.2,
 * B.3; step 3A): what it searches, how it ranks and groups, and what a pick
 * puts on the list. components/PartPicker.tsx draws it.
 *
 * What it searches (all on the page, no server call per key):
 *   - the parts library's components (GET /library): the standard name,
 *     the firm's wording (sedan and tail-gate), the side names, the
 *     "(side undetermined)" twin, aliases and the car map's "also check"
 *     words;
 *   - the price book's past wordings, names only, for staff (decision D3:
 *     /library's seed `fittings`; others get none);
 *   - with no library (an older API, an error), the part list itself.
 * Matching and order are lib/partSearch.ts's (B.2): lower case, no
 * punctuation, side words lifted out and used to set the side toggle,
 * singular = plural, one typo in a word of 5+ letters; exact, starts-with,
 * all words, alias, near spelling; ties to more uses, then the chosen area.
 *
 * What a pick gives -- exactly what the old <select> gave for a part on it
 * (lib/partsList.ts partsToPick, the same list): `part` = the canonical
 * sided name ("Left Tail Lamp", "Tail Lamp (side undetermined)"), no
 * other_part_name. Anything the list doesn't hold -- a past wording, a car
 * map name while intake_car_map_names is off -- is an "Other / Not Listed"
 * named after it (side last, as LH / RH), which is what typing that name
 * into the old Other box gave. Fittings as Annex A lines and new components
 * are later steps (4A, 3B).
 *
 * Pure, with type-only imports from ./api and ./library's types, so Node
 * loads it: scripts/check-picker.mjs. */
import type { Taxonomy } from "./api";
import { panelsFor, printsAs as libraryPrintsAs, type BodyClass, type Library, type LibraryKind } from "./library";
import { parseQuery, searchParts, type MatchTier, type SearchEntry, type Side } from "./partSearch";
import { partsToPick } from "./partsList";
import { HATCH_BODIES, bodyTypeFor, otherWording, partForMapAdd, printsAs as rowPrintsAs, type FirmWording } from "./reviewChecks";

/** taxonomy.OTHER_PART, written here as findings.ts does (api.ts isn't
 * loadable outside Next.js). */
export const OTHER_PART = "Other / Not Listed";
/** taxonomy.MAX_OTHER_NAME. */
const MAX_NAME = 80;

/** The side toggle: LH, RH or "?" (the "(side undetermined)" twin). */
export type PickSide = Side | "unknown";
export const SIDE_LABELS: Record<PickSide, string> = { left: "LH", right: "RH", unknown: "?" };

export type PickerTaxonomy = Pick<Taxonomy, "parts"> & Partial<Pick<Taxonomy, "car_map_parts" | "car_map_names" | "usage">> & FirmWording;
type Vehicle = { make?: string | null; model?: string | null; body_type?: string | null } | null | undefined;

export interface PickerEntry extends SearchEntry {
  /** part: a library component (or a list name with no library); wording:
   * one of the price book's past wordings (staff). */
  type: "part" | "wording";
  /** Its canonical names: {left, right} or {plain}. */
  names: { left?: string; right?: string; plain?: string };
  /** The "(side undetermined)" name, when the list has it. */
  twin: string | null;
  /** Every name of it is on the part list: a pick is a list part. false: a
   * pick is an Other named after it. */
  onList: boolean;
  kind: LibraryKind | null;
  /** A past wording's part ("under Rear Bumper"). */
  parent: string | null;
  /** 3D zone per name ({left, right} or {plain}). */
  zones: Record<string, string | null | undefined>;
  /** Every 3D zone it may be drawn on. */
  panels: string[];
  firm_wording: string | null;
  firm_wording_hatch: string | null;
  /** Uses to show (staff only: /library sends them to staff alone). */
  shownUses: number | null;
  /** The library's order, for browsing. */
  order: number;
  /** A car map name the list doesn't offer now (intake_car_map_names off). */
  carMap: boolean;
}

export interface PartPick {
  part: string;
  other_part_name: string | null;
}

/** taxonomy.clean_part_name: spaces collapsed, at most 80 characters,
 * first letter capital; null for blank. */
export function cleanName(text: string | null | undefined): string | null {
  const name = (text || "").split(/\s+/).filter(Boolean).join(" ").slice(0, MAX_NAME).trim();
  return name ? name[0].toUpperCase() + name.slice(1) : null;
}

const TWIN_SUFFIX = / \(side undetermined\)$/;
const twinBase = (twin: string) => twin.replace(TWIN_SUFFIX, "");

// ---- the entries ---------------------------------------------------------------

/** What the picker searches: the library's components (in its order), the
 * price book's wordings (staff), then any list name the library doesn't
 * hold (all of them with no library), grouped Left / Right / twin. */
export function pickerEntries(lib: Pick<Library, "components" | "fittings"> | null | undefined, t: PickerTaxonomy | null | undefined): PickerEntry[] {
  const list = partsToPick(t);
  const onList = new Set(list);
  const carMapAll = new Set(Array.isArray(t?.car_map_parts) ? t.car_map_parts : []);
  const usage = t?.usage?.parts ?? {};
  const usesOf = (names: string[]) => names.reduce((n, name) => n + (usage[name] ?? 0), 0);
  const out: PickerEntry[] = [];
  const taken = new Set<string>();
  const comps = new Map((lib?.components ?? []).map((c) => [c.key, c]));

  for (const c of lib?.components ?? []) {
    const names: PickerEntry["names"] = { ...c.names };
    const all = Object.values(names).filter(Boolean) as string[];
    if (!all.length) continue;
    all.forEach((n) => taken.add(n));
    if (c.twin) taken.add(c.twin);
    const listed = all.every((n) => onList.has(n));
    const sided = c.side_rule === "sided" && Boolean(names.left && names.right);
    out.push({
      key: c.key,
      type: "part",
      name: c.name,
      wordings: [c.firm_wording, c.firm_wording_hatch, ...all, c.twin ? twinBase(c.twin) : null],
      aliases: (c.aliases ?? []).map((a) => a.text),
      uses: c.uses ?? usesOf(all),
      shownUses: c.uses ?? null,
      area: c.area,
      sided,
      names,
      twin: listed && sided && c.twin && onList.has(c.twin) ? c.twin : null,
      onList: listed,
      kind: c.kind,
      parent: null,
      zones: c.zones ?? {},
      panels: c.panels ?? [],
      firm_wording: c.firm_wording,
      firm_wording_hatch: c.firm_wording_hatch,
      order: out.length,
      carMap: !listed && all.some((n) => carMapAll.has(n) || c.ai_use === "car_map"),
    });
  }

  for (const f of lib?.fittings ?? []) {
    const parent = f.parent_key ? comps.get(f.parent_key) : undefined;
    out.push({
      key: f.key,
      type: "wording",
      name: f.name,
      aliases: (f.aliases ?? []).map((a) => a.text),
      uses: f.uses ?? 0,
      shownUses: f.uses ?? null,
      area: f.area,
      sided: f.sided,
      names: {},
      twin: null,
      onList: false,
      kind: f.kind,
      parent: parent?.name ?? null,
      zones: {},
      panels: f.panels ?? [],
      firm_wording: null,
      firm_wording_hatch: null,
      order: out.length,
      carMap: false,
    });
  }

  // List names the library doesn't hold: "Left X" / "Right X" become one
  // entry X with its "X (side undetermined)", the rest one each.
  const pairs = new Map<string, PickerEntry["names"]>();
  const twins = new Map<string, string>();
  const plain: string[] = [];
  for (const name of list) {
    if (name === OTHER_PART || taken.has(name)) continue;
    const m = /^(Left|Right) (.+)$/.exec(name);
    if (m) pairs.set(m[2], { ...pairs.get(m[2]), [m[1] === "Left" ? "left" : "right"]: name });
    else if (TWIN_SUFFIX.test(name)) twins.set(twinBase(name), name);
    else plain.push(name);
  }
  const extra = (name: string, names: PickerEntry["names"], twin: string | null): PickerEntry => ({
    key: `list:${name}`,
    type: "part",
    name,
    wordings: [...(Object.values(names) as string[]), twin ? twinBase(twin) : null],
    uses: usesOf(Object.values(names) as string[]),
    shownUses: null,
    area: null,
    sided: Boolean(names.left && names.right),
    names,
    twin,
    onList: true,
    kind: null,
    parent: null,
    zones: {},
    panels: [],
    firm_wording: null,
    firm_wording_hatch: null,
    order: 0,
    carMap: false,
  });
  for (const [base, names] of pairs) {
    if (names.left && names.right) {
      const twin = twins.get(base) ?? null;
      twins.delete(base);
      out.push({ ...extra(base, names, twin), order: out.length });
    } else {
      const name = (names.left ?? names.right) as string;
      out.push({ ...extra(name, { plain: name }, null), order: out.length });
    }
  }
  for (const name of [...plain, ...twins.values()]) out.push({ ...extra(name, { plain: name }, null), order: out.length });
  return out;
}

// ---- the query -----------------------------------------------------------------

const UNDETERMINED = /\(?\s*\bside\s+undetermined\b\s*\)?|\bundetermined\b|\bside\s+unknown\b|\bunknown\s+side\b/gi;

/** The side the query names: LH / RH words (partSearch.liftSide), or "?"
 * for "(side undetermined)" / "side unknown"; null for none. */
export function querySide(query: string | null | undefined): PickSide | null {
  const text = query ?? "";
  if (new RegExp(UNDETERMINED.source, "i").test(text)) return "unknown";
  return parseQuery(text).side;
}

/** The query as searched: "(side undetermined)" is the toggle's "?", not
 * words to find. */
export function searchText(query: string | null | undefined): string {
  return (query ?? "").replace(UNDETERMINED, " ");
}

/** The query has words to search (not only side words). */
export const hasWords = (query: string | null | undefined) => parseQuery(searchText(query)).words.length > 0;

// ---- a pick --------------------------------------------------------------------

/** What picking `entry` with `side` puts on the list, or null when a side
 * must be chosen first (a sided list part and no side, or "?" with no
 * twin). A list part: its canonical name. Anything else: an Other named
 * after it, the side last as LH / RH ("?" or none: no side). */
export function pickFor(entry: PickerEntry, side: PickSide | null): PartPick | null {
  if (entry.onList) {
    if (!entry.sided) {
      const name = entry.names.plain ?? entry.names.left ?? entry.names.right ?? entry.name;
      return { part: name, other_part_name: null };
    }
    const name = side === "left" ? entry.names.left : side === "right" ? entry.names.right : side === "unknown" ? entry.twin : null;
    return name ? { part: name, other_part_name: null } : null;
  }
  const code = entry.sided && side === "left" ? " LH" : entry.sided && side === "right" ? " RH" : "";
  return { part: OTHER_PART, other_part_name: cleanName(`${entry.name}${code}`) };
}

/** A side must be chosen before this entry can be picked. */
export const needsSide = (entry: PickerEntry, side: PickSide | null) => pickFor(entry, side) === null;

/** The sides a row offers: LH and RH (and "?" when it has a twin, or for a
 * named Other: no side) for a sided entry; none for the rest. */
export function sideChoices(entry: PickerEntry): PickSide[] {
  if (!entry.sided) return [];
  return entry.onList && !entry.twin ? ["left", "right"] : ["left", "right", "unknown"];
}

/** The list's own label for a pick: "Left Tail Lamp", or the Other's name. */
export const pickLabel = (pick: PartPick) => (pick.part === OTHER_PART ? pick.other_part_name || OTHER_PART : pick.part);

/** "Other / Not Listed" for what the person typed: the typed words,
 * tidied, as the old name box took them; none typed, an unnamed Other
 * (named on its row). */
export function otherPick(typed: string | null | undefined): PartPick {
  return { part: OTHER_PART, other_part_name: cleanName(typed) };
}

// ---- prints as -----------------------------------------------------------------

function bodyClass(t: PickerTaxonomy | null | undefined, vehicle: Vehicle): BodyClass {
  return HATCH_BODIES.has(bodyTypeFor(vehicle, t) || "") ? "hatch" : "sedan";
}

/** What the pick prints as on Annex A for THIS car's body type: the
 * Review step's rule (reviewChecks.printsAs, a person's row) from GET
 * /taxonomy's wording; the library's wording on an older API. A side
 * still to choose: "Tail lamp LH / RH". */
export function printsAsFor(entry: PickerEntry, side: PickSide | null, t: PickerTaxonomy | null | undefined, vehicle: Vehicle): string | null {
  const pick = pickFor(entry, side);
  const wording = (p: PartPick): string | null => {
    if (p.part === OTHER_PART) return p.other_part_name ? otherWording(p.other_part_name) : null;
    const text = rowPrintsAs({ part: p.part, other_part_name: null, human_verified: true, side_inferred: false }, t, vehicle).text;
    if (text) return text;
    // An API without the firm's names: the library's.
    const base = libraryPrintsAs(entry, bodyClass(t, vehicle));
    if (!base) return null;
    return p.part === entry.names.left ? `${base} LH` : p.part === entry.names.right ? `${base} RH` : base;
  };
  if (pick) return wording(pick);
  const left = pickFor(entry, "left");
  const text = left ? wording(left) : null;
  return text ? `${text.replace(/ (LH|RH)$/, "")} LH / RH` : null;
}

// ---- results -------------------------------------------------------------------

export interface PickerHit {
  entry: PickerEntry;
  /** How it matched; null while browsing (no words typed). */
  tier: MatchTier | null;
  matched: string | null;
}

export interface PickerGroup {
  key: string;
  label: string;
  hits: PickerHit[];
}

export interface PickerArea {
  key: string;
  label: string;
}

/** The library's areas (lib/library.ts LibraryArea), for the chips. */
export const DEFAULT_AREAS: PickerArea[] = [
  { key: "front", label: "Front" },
  { key: "rear", label: "Rear" },
  { key: "side", label: "Side" },
  { key: "roof", label: "Roof" },
  { key: "under", label: "Under" },
  { key: "wheels", label: "Wheels" },
  { key: "inside", label: "Inside" },
];

export function pickerAreas(lib: Pick<Library, "areas"> | null | undefined): PickerArea[] {
  const areas = (lib?.areas ?? []).map((a) => ({ key: a.key, label: a.label }));
  return areas.length ? areas : DEFAULT_AREAS;
}

/** The entry may be drawn on this 3D zone (a door means its glass too, as
 * the library page's diagram). */
export function onZone(entry: PickerEntry, zone: string): boolean {
  const wanted = panelsFor(zone);
  return entry.panels.some((p) => wanted.includes(p)) || Object.values(entry.zones).some((z) => Boolean(z) && wanted.includes(z as string));
}

export interface PickerOptions {
  /** An area chip: only this area. */
  area?: string | null;
  /** A 3D zone (the map's "+ Add part here"): only what sits there. */
  zone?: string | null;
  /** On a tie, and first while browsing (the photo's area). */
  preferArea?: string | null;
  /** Shown first while browsing (the map panel's own part). */
  preferNames?: readonly string[];
  /** The side toggle: with "?", two parts sharing one twin ("Front Door",
   * "Rear Door" -> "Door (side undetermined)") show once. */
  side?: PickSide | null;
  areas?: readonly PickerArea[];
}

export interface PickerView {
  /** Words were typed: ranked hits. Else browsing: the parts in the
   * library's order (no past wordings: they are found by typing). */
  searching: boolean;
  groups: PickerGroup[];
  /** Every hit, in the order shown (the groups', top to bottom). */
  hits: PickerHit[];
  /** Searching with an area chip on: matches in the other areas. */
  elsewhere: number;
}

const areaOf = (e: PickerEntry) => e.area || "other";

function passes(entry: PickerEntry, opts: PickerOptions): boolean {
  if (opts.zone && !onZone(entry, opts.zone)) return false;
  return !(opts.area && entry.area !== opts.area);
}

/** The rows for a query: filtered by the chip and the zone, ranked
 * (partSearch.searchParts), grouped by area -- the area of the best hit
 * first while searching; the library's order, the preferred area first,
 * while browsing. */
export function pickerView(entries: readonly PickerEntry[], query: string, opts: PickerOptions = {}): PickerView {
  const text = searchText(query);
  const searching = hasWords(query);
  let hits: PickerHit[];
  let elsewhere = 0;
  const prefer = new Set(opts.preferNames ?? []);
  const preferred = (e: PickerEntry) => Object.values(e.names).some((n) => prefer.has(n as string)) || (e.twin !== null && prefer.has(e.twin));
  if (searching) {
    const found = searchParts(entries.filter((e) => passes(e, { zone: opts.zone })), text, { preferArea: opts.area || opts.preferArea || null });
    const kept = found.filter((h) => !opts.area || h.entry.area === opts.area);
    elsewhere = found.length - kept.length;
    hits = kept.map((h) => ({ entry: h.entry, tier: h.tier, matched: h.matched }));
  } else {
    hits = entries
      .filter((e) => e.type === "part" && passes(e, opts))
      .sort((a, b) => Number(preferred(b)) - Number(preferred(a)) || a.order - b.order)
      .map((entry) => ({ entry, tier: null, matched: null }));
  }
  // "?": one row per twin.
  if (opts.side === "unknown") {
    const seen = new Set<string>();
    hits = hits.filter((h) => {
      const pick = h.entry.onList && h.entry.twin ? h.entry.twin : null;
      if (!pick) return true;
      if (seen.has(pick)) return false;
      seen.add(pick);
      return true;
    });
  }
  const areas = opts.areas ?? DEFAULT_AREAS;
  const label = (key: string) => areas.find((a) => a.key === key)?.label ?? (key === "other" ? "Other parts" : key);
  const byArea = new Map<string, PickerHit[]>();
  for (const h of hits) byArea.set(areaOf(h.entry), [...(byArea.get(areaOf(h.entry)) ?? []), h]);
  let keys = [...byArea.keys()];
  if (!searching) {
    // The preferred part's area first (the map panel's own part on top),
    // then the photo's area, then the library's order.
    const lead = hits.find((h) => preferred(h.entry));
    const rank = (k: string) =>
      lead && k === areaOf(lead.entry) ? -2 : k === opts.preferArea ? -1 : k === "other" ? areas.length : Math.max(0, areas.findIndex((a) => a.key === k));
    keys = keys.sort((a, b) => rank(a) - rank(b));
  }
  const groups = keys.map((key) => ({ key, label: label(key), hits: byArea.get(key) ?? [] }));
  return { searching, groups, hits: groups.flatMap((g) => g.hits), elsewhere };
}

/** "Did you mean": the best three matches for a name typed as an Other
 * (K-pricing B.4 step 2), whatever the chip or the zone. */
export function didYouMean(entries: readonly PickerEntry[], typed: string, n = 3): PickerHit[] {
  if (!hasWords(typed)) return [];
  return searchParts([...entries], searchText(typed), { limit: n }).map((h) => ({ entry: h.entry, tier: h.tier, matched: h.matched }));
}

/** The zones the mini diagram lights for a row: the side's own zone when
 * a side is chosen, else every zone it may be on. */
export function highlightFor(entry: PickerEntry, side: PickSide | null): string[] {
  const own = side === "left" || side === "right" ? entry.zones[side] : null;
  if (own) return [own];
  const plain = entry.zones.plain;
  return entry.panels.length ? entry.panels : plain ? [plain] : [];
}

// ---- presets (B.3) ---------------------------------------------------------------

export interface PickerPreset {
  /** Search words to start with (the photo's part tag). */
  query?: string;
  /** An area chip, on. */
  area?: string | null;
  /** The 3D zone the map's "+ Add part here" was on: only its parts. */
  zone?: string | null;
  side?: PickSide | null;
  /** Where the side came from: shown as "check". */
  sideFrom?: "photo" | "zone" | null;
  /** The photo's area: first while browsing, and on a tie. */
  preferArea?: string | null;
  /** The photo the add links to ("P07"), for the title. */
  photoLabel?: string | null;
}

/** The map's "+ Add part here" on a panel: that panel's parts, the side
 * the panel is on (l_ / r_), shown as "check". */
export function presetFromZone(zone: string): PickerPreset {
  const side: Side | null = zone.startsWith("l_") ? "left" : zone.startsWith("r_") ? "right" : null;
  return { zone, side, sideFrom: side ? "zone" : null };
}

/** The map panel's own part first (reviewChecks.partForMapAdd: the part
 * the old add row pre-filled), when the list has it. */
export function zonePreferred(zone: string | null | undefined, list: readonly string[]): string[] {
  const own = zone ? partForMapAdd(zone, list) : "";
  return own ? [own] : [];
}

/** The sorter's photo areas (ai/triage.py AREAS) as the library's areas:
 * `chip` turns the area chip on (the whole photo is that area); a side or
 * corner photo only prefers it, as it may show a fender, a door or a wheel. */
export const SORTER_AREAS: Record<string, { area: string; side: Side | null; chip: boolean }> = {
  front: { area: "front", side: null, chip: true },
  rear: { area: "rear", side: null, chip: true },
  left: { area: "side", side: "left", chip: false },
  right: { area: "side", side: "right", chip: false },
  "front-left": { area: "front", side: "left", chip: false },
  "front-right": { area: "front", side: "right", chip: false },
  "rear-left": { area: "rear", side: "left", chip: false },
  "rear-right": { area: "rear", side: "right", chip: false },
  roof: { area: "roof", side: null, chip: true },
  underbody: { area: "under", side: null, chip: true },
  interior: { area: "inside", side: null, chip: true },
};

/** The photo viewer's "Add part seen here": the sorter's part tag as the
 * search (its side words set the side), else the photo's area; the side
 * from the area, shown as "check". */
export function presetFromPhoto(photo: { area?: string | null; part?: string | null; label?: string | null } | null | undefined): PickerPreset {
  const where = SORTER_AREAS[(photo?.area || "").toLowerCase()] ?? null;
  const part = (photo?.part || "").trim();
  const tagged = part && part !== OTHER_PART ? part : "";
  const fromTag = tagged ? querySide(tagged) : null;
  const side = fromTag ?? where?.side ?? null;
  return {
    query: tagged,
    area: !tagged && where?.chip ? where.area : null,
    preferArea: where?.area ?? null,
    side,
    sideFrom: side ? "photo" : null,
    photoLabel: photo?.label ?? null,
  };
}
