/** The one search picker for adding a damaged part (L-library-cycle.md B.2,
 * B.3; step 3A): what it searches, how it ranks and groups, and what a pick
 * puts on the list. components/PartPicker.tsx draws it.
 *
 * The part list is the old dropdown's (lib/partsList.ts partsToPick): GET
 * /taxonomy's `parts`, the car map's names only while the server says
 * `car_map_names` (intake_car_map_names "on"). While /taxonomy hasn't
 * answered (or failed: an empty list), the library's own canonical names
 * stand in -- the reader's list, ai_use "reader", never the car map's.
 * With neither, nothing can be picked (review H1): a list part must never
 * turn into an "Other" because the list wasn't there.
 *
 * What it searches (all on the page, no server call per key):
 *   - the library's components ON THE LIST (GET /library): the standard
 *     name, the firm's wording (sedan and tail-gate), the side names, the
 *     "(side undetermined)" twin, aliases, the car map's "also check" words,
 *     and "light" for "lamp" ("Head lamp", "Head light" for Headlamp);
 *   - for staff, the price book's past wordings of main parts (decision D3,
 *     /library's seed `fittings` of kind "part"): one that is a list part's
 *     own wording joins that part as another name; the rest pick as a named
 *     Other. Wordings of kind "fitting" wait for step 4A (D4, D5);
 *   - with no library, the part list itself (zones from lib/vehicleZones).
 * Matching and order are lib/partSearch.ts's (B.2): lower case, no
 * punctuation, side words lifted out and used to set the side toggle,
 * singular = plural, one typo in a word of 5+ letters; exact, starts-with,
 * all words, alias, near spelling; ties to more uses, then the chosen area.
 *
 * What a pick gives -- exactly what the old <select> gave: `part` = the
 * canonical sided name ("Left Tail Lamp", "Tail Lamp (side undetermined)"),
 * no other_part_name. A past wording, or a name typed as "Other / Not
 * Listed" (after one confirmation, with "Did you mean" first), is an Other
 * named after it, the side last as LH / RH.
 *
 * Pure, with type-only imports from ./api and ./library's types, so Node
 * loads it: scripts/check-picker.mjs. */
import type { Taxonomy } from "./api";
import { panelsFor, printsAs as libraryPrintsAs, type BodyClass, type Library, type LibraryKind } from "./library";
import { liftSide, nearWord, normalize, parseQuery, searchParts, stem, type MatchTier, type SearchEntry, type Side } from "./partSearch";
import { partsToPick } from "./partsList";
import { itemKey } from "./findings";
import { HATCH_BODIES, bodyTypeFor, otherWording, partForMapAdd, printsAs as rowPrintsAs, type FirmWording } from "./reviewChecks";
import { zoneKeysFor } from "./vehicleZones";

/** taxonomy.OTHER_PART, written here as findings.ts does (api.ts isn't
 * loadable outside Next.js). */
export const OTHER_PART = "Other / Not Listed";
/** taxonomy.MAX_OTHER_NAME. */
const MAX_NAME = 80;

/** The side toggle: LH, RH or "?" (the "(side undetermined)" twin). */
export type PickSide = Side | "unknown";
export const SIDE_LABELS: Record<PickSide, string> = { left: "LH", right: "RH", unknown: "?" };

export type PickerTaxonomy = Partial<Pick<Taxonomy, "parts" | "car_map_parts" | "car_map_names" | "usage">> & FirmWording;
type PickerLibrary = Partial<Pick<Library, "components" | "twins" | "fittings">>;
type Vehicle = { make?: string | null; model?: string | null; body_type?: string | null } | null | undefined;

export interface PickerEntry extends SearchEntry {
  /** part: a list part; wording: one of the price book's past wordings of
   * a main part (staff), picked as a named Other. */
  type: "part" | "wording";
  /** Its canonical names: {left, right} or {plain}. */
  names: { left?: string; right?: string; plain?: string };
  /** The "(side undetermined)" name, when the list has it. */
  twin: string | null;
  /** The twin is one name for parts at both ends ("Door (side
   * undetermined)" for the front and rear doors): "?" drops front / rear. */
  twinDropsEnd: boolean;
  /** A list part (a pick is its canonical name); false: a past wording. */
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
const uniq = <T>(xs: T[]) => [...new Set(xs)];

// ---- the list --------------------------------------------------------------------

export interface PickerList {
  /** The names a pick may give, "Other / Not Listed" last. */
  names: string[];
  /** Where they came from; null: neither has answered -- no picks yet. */
  from: "taxonomy" | "library" | null;
}

/** The part list: the old dropdown's (GET /taxonomy), else -- the taxonomy
 * not there yet, or failed (DEFAULT_TAXONOMY's empty list) -- the
 * library's canonical names, the car map's left out (the switch isn't
 * known, so it counts as off). */
export function pickerList(lib: PickerLibrary | null | undefined, t: PickerTaxonomy | null | undefined): PickerList {
  if (Array.isArray(t?.parts) && t.parts.length) return { names: partsToPick({ ...t, parts: t.parts }), from: "taxonomy" };
  if (lib?.components?.length) {
    const names: string[] = [];
    for (const c of lib.components) if (c.ai_use === "reader") names.push(...(Object.values(c.names).filter(Boolean) as string[]));
    for (const tw of lib.twins ?? []) if (tw.ai_use === "reader") names.push(tw.name);
    return { names: [...uniq(names), OTHER_PART], from: "library" };
  }
  return { names: [], from: null };
}

// ---- the entries -----------------------------------------------------------------

/** "light" for "lamp", and a compound split ("Headlamp" -> "Head lamp",
 * "Head light", "Headlight"): what people type for the lamps (review L6). */
export function lampNames(texts: readonly (string | null | undefined)[]): string[] {
  const out = new Set<string>();
  for (const t of texts) {
    if (!t || !/lamp/i.test(t)) continue;
    const split = t.replace(/\b([a-z]{3,})(lamp)\b/gi, "$1 $2");
    if (split !== t) out.add(split);
    for (const v of [t, split]) out.add(v.replace(/lamp/gi, (m) => (m[0] === "L" ? "Light" : "light")));
  }
  return [...out].filter((x) => !texts.includes(x));
}

/** A text as one key: side words out, lower case, no punctuation, each
 * word singular. */
const keyOf = (text: string) => normalize(liftSide(text).text).split(" ").filter(Boolean).map(stem).join(" ");

/** The zones a list name sits on, from lib/vehicleZones (no library). */
const zonesOf = (name: string | undefined) => (name ? (zoneKeysFor(name)?.keys ?? []) : []);

/** What the picker searches: the library's list parts (in its order), the
 * price book's main-part wordings (staff), then any list name the library
 * doesn't hold (all of them with no library), Left / Right / twin as one.
 * Empty while there is no list (pickerList). */
export function pickerEntries(lib: PickerLibrary | null | undefined, t: PickerTaxonomy | null | undefined): PickerEntry[] {
  const list = pickerList(lib, t).names;
  if (!list.length) return [];
  const onList = new Set(list);
  const usage = t?.usage?.parts ?? {};
  const usesOf = (names: string[]) => names.reduce((n, name) => n + (usage[name] ?? 0), 0);
  const out: PickerEntry[] = [];
  const taken = new Set<string>();
  const comps = new Map((lib?.components ?? []).map((c) => [c.key, c]));
  const twinCovers = new Map((lib?.twins ?? []).map((tw) => [tw.name, tw.covers?.length ?? 1]));

  for (const c of lib?.components ?? []) {
    const names: PickerEntry["names"] = { ...c.names };
    const all = Object.values(names).filter(Boolean) as string[];
    // Off the list -- the car map's names while intake_car_map_names is
    // off (review H2) -- not offered: they were never in the old dropdown.
    if (!all.length || !all.every((n) => onList.has(n))) continue;
    all.forEach((n) => taken.add(n));
    const sided = c.side_rule === "sided" && Boolean(names.left && names.right);
    const twin = sided && c.twin && onList.has(c.twin) ? c.twin : null;
    if (twin) taken.add(twin);
    const wordings = [c.firm_wording, c.firm_wording_hatch, ...all, twin ? twinBase(twin) : null];
    const aliases = (c.aliases ?? []).map((a) => a.text);
    out.push({
      key: c.key,
      type: "part",
      name: c.name,
      wordings,
      aliases: [...aliases, ...lampNames([c.name, ...wordings, ...aliases])],
      uses: c.uses ?? usesOf(all),
      shownUses: c.uses ?? null,
      area: c.area,
      sided,
      names,
      twin,
      twinDropsEnd: Boolean(twin && (twinCovers.get(twin) ?? 1) > 1),
      onList: true,
      kind: c.kind,
      parent: null,
      zones: c.zones ?? {},
      panels: c.panels ?? [],
      firm_wording: c.firm_wording,
      firm_wording_hatch: c.firm_wording_hatch,
      order: out.length,
    });
  }

  // List names the library doesn't hold: "Left X" / "Right X" become one
  // entry X with its "X (side undetermined)", the rest one each; their
  // zones from lib/vehicleZones, so a map panel finds them (review M2).
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
  const extra = (name: string, names: PickerEntry["names"], twin: string | null): PickerEntry => {
    const all = Object.values(names) as string[];
    const zones: PickerEntry["zones"] = names.left
      ? { left: zonesOf(names.left)[0] ?? null, right: zonesOf(names.right)[0] ?? null }
      : { plain: zonesOf(names.plain).length === 1 ? zonesOf(names.plain)[0] : null };
    const wordings = [...all, twin ? twinBase(twin) : null];
    return {
      key: `list:${name}`,
      type: "part",
      name,
      wordings,
      aliases: lampNames([name, ...wordings]),
      uses: usesOf(all),
      shownUses: null,
      area: null,
      sided: Boolean(names.left && names.right),
      names,
      twin,
      // "Door" for "Front Door": the twin's name dropped the end.
      twinDropsEnd: Boolean(twin && /\b(front|rear)\b/i.test(name) && !/\b(front|rear)\b/i.test(twinBase(twin))),
      onList: true,
      kind: null,
      parent: null,
      zones,
      panels: uniq([...all, ...(twin ? [twin] : [])].flatMap(zonesOf)),
      firm_wording: null,
      firm_wording_hatch: null,
      order: 0,
    };
  };
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

  // The price book's wordings (staff only: /library sends none to anyone
  // else). Fittings wait for step 4A (review H3: D4, D5). A wording that is
  // a list part's own (its name, wording or another name, side and plural
  // aside) joins that part as another name: a pick is the part.
  const byKey = new Map<string, PickerEntry>();
  for (const e of out) for (const text of [e.name, ...(e.wordings ?? []), ...(e.aliases ?? [])]) if (text) byKey.set(keyOf(text), e);
  for (const f of lib?.fittings ?? []) {
    if (f.kind === "fitting") continue;
    const texts = [f.name, ...(f.aliases ?? []).map((a) => a.text)];
    const same = texts.map((x) => byKey.get(keyOf(x))).find(Boolean);
    if (same) {
      same.aliases = uniq([...(same.aliases ?? []), ...texts]) as string[];
      continue;
    }
    const parent = f.parent_key ? comps.get(f.parent_key) : undefined;
    out.push({
      key: f.key,
      type: "wording",
      name: f.name,
      aliases: texts.slice(1),
      uses: f.uses ?? 0,
      shownUses: f.uses ?? null,
      area: f.area,
      sided: f.sided,
      names: {},
      twin: null,
      twinDropsEnd: false,
      onList: false,
      kind: f.kind,
      parent: parent?.name ?? null,
      zones: {},
      panels: f.panels ?? [],
      firm_wording: null,
      firm_wording_hatch: null,
      order: out.length,
    });
  }
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
 * twin). A list part: its canonical name. A past wording: an Other named
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
 * tidied, as the old name box took them, with the side toggle's LH / RH
 * last when the words name no side (review L4); none typed, an unnamed
 * Other (named on its row). */
export function otherPick(typed: string | null | undefined, side: PickSide | null = null): PartPick {
  const name = cleanName(typed);
  if (!name) return { part: OTHER_PART, other_part_name: null };
  const code = !querySide(name) && side === "left" ? " LH" : !querySide(name) && side === "right" ? " RH" : "";
  return { part: OTHER_PART, other_part_name: cleanName(`${name}${code}`) };
}

/** "Other / Not Listed" with typed words never goes in at once (review
 * M4): the best matches first ("Did you mean"), then one confirmation.
 * Nothing typed: an unnamed Other, named on its row, at once. */
export function otherStep(entries: readonly PickerEntry[], typed: string, side: PickSide | null): { pick: PartPick; confirm: boolean; suggestions: PickerHit[] } {
  const pick = otherPick(typed, side);
  if (!pick.other_part_name) return { pick, confirm: false, suggestions: [] };
  return { pick, confirm: true, suggestions: didYouMean(entries, typed) };
}

// ---- already on the list (review M1) -----------------------------------------------

export type ListedItem = { part?: string | null; other_part_name?: string | null };

/** The rows on the list now, by findings.itemKey (a named Other by its
 * name): their first row. */
export function listedRows(items: readonly ListedItem[] | null | undefined): Map<string, number> {
  const out = new Map<string, number>();
  (items ?? []).forEach((it, i) => {
    if (!(it.part || "").trim()) return;
    const key = itemKey({ part: it.part || "", other_part_name: it.other_part_name });
    if (!out.has(key)) out.set(key, i);
  });
  return out;
}

/** "Already row 3" for a pick on the list; for a part whose side is still
 * to choose, which of its sides are ("LH is row 3"). null: not listed. */
export function listedNote(entry: PickerEntry, side: PickSide | null, rows: Map<string, number>): string | null {
  if (!rows.size) return null;
  const rowOf = (p: PartPick | null) => (p ? rows.get(itemKey(p)) : undefined);
  const pick = pickFor(entry, side);
  if (pick) {
    const row = rowOf(pick);
    return row === undefined ? null : `already row ${row + 1}`;
  }
  const notes = sideChoices(entry)
    .map((s) => ({ s, row: rowOf(pickFor(entry, s)) }))
    .filter((x) => x.row !== undefined)
    .map((x) => `${SIDE_LABELS[x.s]} is row ${(x.row as number) + 1}`);
  return notes.length ? notes.join(", ") : null;
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
  // The left one's wording without its side ("Tail lamp LH", or a name
  // printed as written: "Left Rear Bumper Reflector").
  const left = pickFor(entry, "left");
  const text = left ? wording(left) : null;
  return text ? `${text.replace(/ (LH|RH)$/, "").replace(/^(Left|Right) /, "")} LH / RH` : null;
}

/** "?" on a front or rear door, wheel, arch or regulator gives one name
 * for both ends ("Door (side undetermined)"): the list has no front / rear
 * twin, so the row says so (review L10). */
export function twinNote(entry: PickerEntry, side: PickSide | null): string | null {
  return side === "unknown" && entry.twin && entry.twinDropsEnd ? `front / rear not kept: the list has one "${entry.twin}"` : null;
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

export const areaLabel = (key: string | null | undefined, areas: readonly PickerArea[] = DEFAULT_AREAS) =>
  (key && areas.find((a) => a.key === key)?.label) || key || "";

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
  /** The photo's area: its parts first -- a boost within a match tier
   * while searching, its group first while browsing; never a filter. */
  preferArea?: string | null;
  /** Shown first while browsing (the map panel's own part). */
  preferNames?: readonly string[];
  /** The side toggle: with "?", two parts sharing one twin ("Front Door",
   * "Rear Door" -> "Door (side undetermined)") show once. */
  side?: PickSide | null;
  areas?: readonly PickerArea[];
  /** The rows on the list now (listedRows): while browsing, a part whose
   * pick is listed already comes after the rest of its group (review M1). */
  listed?: Map<string, number>;
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
const TIERS: MatchTier[] = ["exact", "starts", "words", "alias", "near"];
/** The last group while searching: close spellings, whatever their area. */
export const NEAR_GROUP = "near";

function passes(entry: PickerEntry, opts: PickerOptions): boolean {
  if (opts.zone && !onZone(entry, opts.zone)) return false;
  return !(opts.area && entry.area !== opts.area);
}

/** The rows for a query: filtered by the chip and the zone, ranked
 * (partSearch.searchParts; the photo's area first within a tier), grouped
 * by area -- the area of the best hit first, close spellings last while
 * searching; the library's order, the preferred part's and area's group
 * first, while browsing. */
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
    // The photo's area: a boost within each tier (a stable sort).
    if (opts.preferArea && !opts.area) {
      const tier = (h: PickerHit) => TIERS.indexOf(h.tier as MatchTier);
      const home = (h: PickerHit) => Number(h.entry.area === opts.preferArea);
      hits = [...hits].sort((a, b) => tier(a) - tier(b) || home(b) - home(a));
    }
  } else {
    const isListed = (e: PickerEntry) => {
      const pick = opts.listed?.size ? pickFor(e, opts.side ?? null) : null;
      return Boolean(pick && opts.listed?.has(itemKey(pick)));
    };
    hits = entries
      .filter((e) => e.type === "part" && passes(e, opts))
      .sort((a, b) => Number(isListed(a)) - Number(isListed(b)) || Number(preferred(b)) - Number(preferred(a)) || a.order - b.order)
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
  const label = (key: string) =>
    key === NEAR_GROUP ? "Close spellings" : (areas.find((a) => a.key === key)?.label ?? (key === "other" ? "Other parts" : key));
  const groupOf = (h: PickerHit) => (searching && h.tier === "near" ? NEAR_GROUP : areaOf(h.entry));
  const byGroup = new Map<string, PickerHit[]>();
  for (const h of hits) byGroup.set(groupOf(h), [...(byGroup.get(groupOf(h)) ?? []), h]);
  let keys = [...byGroup.keys()];
  if (searching) {
    keys = [...keys.filter((k) => k !== NEAR_GROUP), ...keys.filter((k) => k === NEAR_GROUP)];
  } else {
    // The preferred part's area first (the map panel's own part on top),
    // then the photo's area, then the library's order.
    const lead = hits.find((h) => preferred(h.entry));
    const rank = (k: string) =>
      lead && k === areaOf(lead.entry) ? -2 : k === opts.preferArea ? -1 : k === "other" ? areas.length : Math.max(0, areas.findIndex((a) => a.key === k));
    keys = keys.sort((a, b) => rank(a) - rank(b));
  }
  const groups = keys.map((key) => ({ key, label: label(key), hits: byGroup.get(key) ?? [] }));
  return { searching, groups, hits: groups.flatMap((g) => g.hits), elsewhere };
}

/** "Did you mean": the best three for a name about to go in as an Other
 * (K-pricing B.4 step 2; review M4), whatever the chip or the zone. First
 * the strict matches (all words); then any part sharing at least half of
 * the typed words (a 5+ letter word may be one letter out), most shared
 * first, then the part whose own words are most covered, more uses,
 * shorter name. "rear bumper retainer" -> Rear Bumper. */
export function didYouMean(entries: readonly PickerEntry[], typed: string, n = 3): PickerHit[] {
  if (!hasWords(typed)) return [];
  const strict = searchParts([...entries], searchText(typed), { limit: n }).map((h) => ({ entry: h.entry, tier: h.tier, matched: h.matched }));
  if (strict.length >= n) return strict;
  const words = uniq(parseQuery(searchText(typed)).words.filter((w) => w.length >= 2));
  const need = Math.max(1, Math.ceil(words.length / 2));
  const same = (q: string, w: string) => stem(q) === stem(w) || nearWord(q, w);
  const scored: { hit: PickerHit; shared: number; cover: number }[] = [];
  for (const entry of entries) {
    if (strict.some((h) => h.entry === entry)) continue;
    let best: { shared: number; cover: number; text: string } | null = null;
    for (const text of [entry.name, ...(entry.wordings ?? []), ...(entry.aliases ?? [])]) {
      if (!text) continue;
      const own = normalize(liftSide(text).text).split(" ").filter(Boolean);
      if (!own.length) continue;
      const shared = words.filter((q) => own.some((w) => same(q, w))).length;
      const cover = own.filter((w) => words.some((q) => same(q, w))).length / own.length;
      if (!best || shared > best.shared || (shared === best.shared && cover > best.cover)) best = { shared, cover, text };
    }
    if (best && best.shared >= need) scored.push({ hit: { entry, tier: null, matched: best.text }, shared: best.shared, cover: best.cover });
  }
  scored.sort(
    (a, b) =>
      b.shared - a.shared ||
      b.cover - a.cover ||
      (b.hit.entry.uses ?? 0) - (a.hit.entry.uses ?? 0) ||
      a.hit.entry.name.length - b.hit.entry.name.length ||
      a.hit.entry.name.localeCompare(b.hit.entry.name)
  );
  return [...strict, ...scored.map((s) => s.hit)].slice(0, n);
}

/** What the list says when no row shows (review M2): never "No part here."
 * while the parts are still loading. */
export function emptyText(s: {
  ready: boolean;
  loading: boolean;
  searching: boolean;
  typed: string | null;
  area: string | null;
  zone: string | null;
}): string {
  if (!s.ready) return s.loading ? "Loading the part list…" : "The part list didn't load. Close this and try again.";
  if (s.searching) return `No part matches "${s.typed ?? ""}"${s.zone ? " on this panel" : s.area ? " in this area" : ""}.`;
  if (s.loading) return "Loading the parts…";
  return s.zone ? "No part on this panel." : "No part here.";
}

/** The zones the mini diagram lights for a row: the side's own zone when
 * a side is chosen, else every zone it may be on. */
export function highlightFor(entry: PickerEntry, side: PickSide | null): string[] {
  const own = side === "left" || side === "right" ? entry.zones[side] : null;
  if (own) return [own];
  const plain = entry.zones.plain;
  return entry.panels.length ? entry.panels : plain ? [plain] : [];
}

/** The dialog's focus trap goes round the elements Tab really reaches:
 * tabIndex 0 or more, not disabled, shown (review M3). */
export function isTabbable(el: { tabIndex: number; disabled?: boolean; offsetParent: unknown; getAttribute?: (name: string) => string | null }): boolean {
  return el.tabIndex >= 0 && !el.disabled && el.offsetParent !== null && el.getAttribute?.("aria-hidden") !== "true";
}

// ---- the case page's add row (review M5) -------------------------------------------------

/** Tab 1's add row before [Add]. */
export type AddRowDraft = { part: string; other_part_name: string; condition: string; remarks: string; photo_reference: string };

/** The add row after a pick. A new add (from "+ Add a part", the map or the
 * photo viewer) starts afresh, with only what its preset sets (the
 * photo); "Change the part" keeps the rest of the row. */
export function nextAddRow(cur: AddRowDraft | null, pick: PartPick, opts: { fresh: boolean; photoLabel: string | null }): AddRowDraft {
  const keep = opts.fresh ? null : cur;
  return {
    part: pick.part,
    other_part_name: pick.other_part_name ?? "",
    condition: keep?.condition ?? "",
    remarks: keep?.remarks ?? "",
    photo_reference: opts.photoLabel ?? keep?.photo_reference ?? "",
  };
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
  /** The photo's area: first (a boost, never a filter). */
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
 * the old add row pre-filled), when the list has it and it isn't listed
 * already (review M1). */
export function zonePreferred(zone: string | null | undefined, list: readonly string[], rows?: Map<string, number>): string[] {
  const own = zone ? partForMapAdd(zone, list) : "";
  if (!own || rows?.has(itemKey({ part: own, other_part_name: null }))) return [];
  return [own];
}

/** The sorter's photo areas (ai/triage.py AREAS) as the library's areas,
 * and the side they name. */
export const SORTER_AREAS: Record<string, { area: string; side: Side | null }> = {
  front: { area: "front", side: null },
  rear: { area: "rear", side: null },
  left: { area: "side", side: "left" },
  right: { area: "side", side: "right" },
  "front-left": { area: "front", side: "left" },
  "front-right": { area: "front", side: "right" },
  "rear-left": { area: "rear", side: "left" },
  "rear-right": { area: "rear", side: "right" },
  roof: { area: "roof", side: null },
  underbody: { area: "under", side: null },
  interior: { area: "inside", side: null },
};

/** The photo viewer's "Add part seen here": the sorter's part tag as the
 * search (its side words set the side); the photo's area first -- a
 * preference, never a filter, as the sorter's area is a guess and a front
 * photo can show a mirror or a wheel (review M6); the side from the area,
 * shown as "check". */
export function presetFromPhoto(photo: { area?: string | null; part?: string | null; label?: string | null } | null | undefined): PickerPreset {
  const where = SORTER_AREAS[(photo?.area || "").toLowerCase()] ?? null;
  const part = (photo?.part || "").trim();
  const tagged = part && part !== OTHER_PART ? part : "";
  const fromTag = tagged ? querySide(tagged) : null;
  const side = fromTag ?? where?.side ?? null;
  return {
    query: tagged,
    area: null,
    preferArea: where?.area ?? null,
    side,
    sideFrom: side ? "photo" : null,
    photoLabel: photo?.label ?? null,
  };
}
