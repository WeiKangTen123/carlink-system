/** The parts library as GET /library gives it (bot-service app/api/library.py,
 * app/parts/library.py; L-library-cycle.md step 2A), and the pure helpers the
 * Knowledge page's "Parts library" tab groups and filters it with.
 *
 * The library is read-only here: the core is the code's own parts (area ->
 * assembly -> component), fittings are "seed" rows from the price book's
 * wordings (staff only, names only). Usage counts come only for staff
 * (decision G9); `staff` says whether this answer has them.
 *
 * Pure: type-only imports, so Node loads it directly
 * (scripts/check-library.mjs). */
import type { SearchEntry } from "./partSearch";

export type LibraryKind = "panel" | "part" | "mechanical" | "electrical" | "interior" | "fitting";
export const LIBRARY_KINDS: LibraryKind[] = ["panel", "part", "mechanical", "electrical", "interior", "fitting"];
export type LibraryStatus = "core" | "seed";
export type BodyClass = "sedan" | "hatch";

export interface LibraryAlias {
  text: string;
  /** synonym / typo / also_check (the car map's words) / wording (another spelling in the price book). */
  kind: string;
  source: string;
}

export interface LibraryLink {
  id: string;
  key: string;
  name: string;
  type: string;
  side?: string;
  min_hit?: string;
  hidden?: boolean;
  strip_down?: boolean;
  area?: string;
  reason?: string;
}

export interface LibraryComponent {
  key: string;
  name: string;
  /** The canonical names it holds: {left, right} or {plain}. */
  names: Record<string, string>;
  twin: string | null;
  twin_key: string | null;
  side_rule: "sided" | "none" | "one_side_unknown";
  twin_placement: "by_side" | "needs_end" | "not_placed" | null;
  area: string;
  assembly: string;
  also_in: string[];
  kind: LibraryKind;
  role: string;
  firm_wording: string | null;
  firm_wording_hatch: string | null;
  family: string | null;
  parent_key: string | null;
  /** 3D zone per name ({left, right} or {plain}); null: no mesh. */
  zones: Record<string, string | null>;
  /** Every 3D zone it may be drawn on. */
  panels: string[];
  stand_in: string | null;
  bodies: string[] | null;
  body_classes: BodyClass[];
  hidden: boolean;
  strip_down: boolean;
  if_fitted: boolean;
  aliases: LibraryAlias[];
  linked: { out: LibraryLink[]; in: LibraryLink[] };
  ai_use: "reader" | "links" | "none";
  note: string | null;
  status: LibraryStatus;
  version_added: number;
  /** Staff only. */
  uses?: number;
  conditions?: [string, number][];
}

export interface LibraryTwin {
  key: string;
  name: string;
  base: string;
  covers: string[];
  placement: "by_side" | "needs_end" | "not_placed";
  area: string | null;
  kind: LibraryKind | null;
  zone: string | null;
  panels: string[];
  stand_in: string | null;
  firm_wording: string | null;
  firm_wording_hatch: string | null;
  ai_use: "reader" | "links" | "none";
  status: LibraryStatus;
  uses?: number;
  conditions?: [string, number][];
}

export interface LibraryFitting {
  key: string;
  name: string;
  kind: LibraryKind;
  status: LibraryStatus;
  family: string | null;
  parent_key: string | null;
  parent_keys: string[];
  area: string | null;
  assembly: string | null;
  panels: string[];
  sided: boolean;
  aliases: LibraryAlias[];
  uses?: number;
  conditions?: [string, number][];
}

export interface LibraryAssembly {
  key: string;
  name: string;
  area: string;
  anchors: string[];
  components: string[];
  also: string[];
}

export interface LibraryArea {
  key: string;
  label: string;
  assemblies: string[];
}

export interface Library {
  format: number;
  version: number;
  core: string;
  staff: boolean;
  areas: LibraryArea[];
  assemblies: LibraryAssembly[];
  components: LibraryComponent[];
  twins: LibraryTwin[];
  fittings: LibraryFitting[];
  other: { name: string; firm_wording: string | null };
}

export interface LibraryFilters {
  status: "all" | LibraryStatus;
  kind: "all" | LibraryKind;
  body: "all" | BodyClass;
  /** A library area key, or "none": parts with no panel on the 3D model. */
  area: string | null;
  /** A 3D zone picked on the diagram. */
  panel: string | null;
}

export const NO_FILTERS: LibraryFilters = { status: "all", kind: "all", body: "all", area: null, panel: null };

/** The 2D top view draws a door and its glass as one shape: picking it
 * means either. */
export const PANEL_ALSO: Record<string, string[]> = {
  l_door_front: ["l_door_glass_front"],
  r_door_front: ["r_door_glass_front"],
  l_door_rear: ["l_door_glass_rear"],
  r_door_rear: ["r_door_glass_rear"],
};

export function panelsFor(panel: string): string[] {
  return [panel, ...(PANEL_ALSO[panel] ?? [])];
}

/** How a component prints on Annex A for a body (side code left out). */
export function printsAs(entry: { firm_wording: string | null; firm_wording_hatch: string | null }, body: BodyClass): string | null {
  return (body === "hatch" ? entry.firm_wording_hatch : null) ?? entry.firm_wording;
}

function onPanel(panels: string[], panel: string): boolean {
  const wanted = panelsFor(panel);
  return panels.some((p) => wanted.includes(p));
}

/** A component passes the filters on its own. */
export function componentPasses(c: LibraryComponent, f: LibraryFilters): boolean {
  if (f.status !== "all" && c.status !== f.status) return false;
  if (f.kind !== "all" && c.kind !== f.kind) return false;
  if (f.body !== "all" && !c.body_classes.includes(f.body)) return false;
  if (f.area === "none") return c.panels.length === 0;
  if (f.area && c.area !== f.area) return false;
  return !(f.panel && !onPanel(c.panels, f.panel));
}

/** A fitting passes the filters on its own (it takes its part's place). */
export function fittingPasses(x: LibraryFitting, f: LibraryFilters): boolean {
  if (f.status !== "all" && x.status !== f.status) return false;
  if (f.kind !== "all" && x.kind !== f.kind) return false;
  if (f.area === "none") return x.panels.length === 0;
  if (f.area && x.area !== f.area) return false;
  return !(f.panel && !onPanel(x.panels, f.panel));
}

export interface ComponentRow {
  component: LibraryComponent;
  /** Shown only as the parent of fittings that pass (it doesn't itself). */
  context: boolean;
  fittings: LibraryFitting[];
}

export interface AssemblyGroup {
  key: string;
  name: string;
  rows: ComponentRow[];
}

export interface AreaGroup {
  key: string;
  label: string;
  assemblies: AssemblyGroup[];
  /** Unplaced fittings (no part to hang under), area "unplaced" only. */
  loose: LibraryFitting[];
  components: number;
  fittings: number;
  /** Uses of its components (staff); null without counts. */
  uses: number | null;
}

/** The library as the page lists it: area -> assembly -> component ->
 * fittings indented, in the library's order. A component shows when it
 * passes the filters, or -- as context -- when one of its fittings does;
 * empty assemblies and areas are left out. Fittings no rule places come
 * last ("Not placed yet"). */
export function groupLibrary(lib: Library, filters: LibraryFilters = NO_FILTERS): AreaGroup[] {
  const comps = new Map(lib.components.map((c) => [c.key, c]));
  const fittingsOf = new Map<string, LibraryFitting[]>();
  const loose: LibraryFitting[] = [];
  for (const x of lib.fittings) {
    if (!fittingPasses(x, filters)) continue;
    if (!x.parent_keys.length) loose.push(x);
    for (const k of x.parent_keys) fittingsOf.set(k, [...(fittingsOf.get(k) ?? []), x]);
  }
  const asmByKey = new Map(lib.assemblies.map((a) => [a.key, a]));
  const out: AreaGroup[] = [];
  for (const area of lib.areas) {
    const assemblies: AssemblyGroup[] = [];
    for (const asmKey of area.assemblies) {
      const asm = asmByKey.get(asmKey);
      if (!asm) continue;
      const rows: ComponentRow[] = [];
      for (const key of asm.components) {
        const c = comps.get(key);
        if (!c) continue;
        const passes = componentPasses(c, filters);
        const fittings = fittingsOf.get(key) ?? [];
        if (passes || fittings.length) rows.push({ component: c, context: !passes, fittings });
      }
      if (rows.length) assemblies.push({ key: asm.key, name: asm.name, rows });
    }
    if (!assemblies.length) continue;
    const rows = assemblies.flatMap((a) => a.rows);
    out.push({
      key: area.key,
      label: area.label,
      assemblies,
      loose: [],
      components: rows.filter((r) => !r.context).length,
      fittings: new Set(rows.flatMap((r) => r.fittings.map((x) => x.key))).size,
      uses: lib.staff ? rows.filter((r) => !r.context).reduce((n, r) => n + (r.component.uses ?? 0), 0) : null,
    });
  }
  if (loose.length && !filters.panel && (!filters.area || filters.area === "none")) {
    out.push({ key: "unplaced", label: "Not placed yet", assemblies: [], loose, components: 0, fittings: loose.length, uses: null });
  }
  return out;
}

/** Uses per panel of the 2D top view (staff): the components drawn there,
 * a sided one on both its sides. null without counts. */
export function panelUses(lib: Library): Record<string, number> | null {
  if (!lib.staff) return null;
  const out: Record<string, number> = {};
  for (const c of lib.components) for (const p of c.panels) out[p] = (out[p] ?? 0) + (c.uses ?? 0);
  return out;
}

/** Components per area (and "none": no panel on the model), for the chips. */
export function areaCounts(lib: Library): Record<string, number> {
  const out: Record<string, number> = { none: 0 };
  for (const c of lib.components) {
    out[c.area] = (out[c.area] ?? 0) + 1;
    if (!c.panels.length) out.none += 1;
  }
  return out;
}

export type LibraryEntry =
  | { type: "component"; entry: LibraryComponent }
  | { type: "fitting"; entry: LibraryFitting }
  | { type: "twin"; entry: LibraryTwin };

export function findEntry(lib: Library, key: string): LibraryEntry | null {
  const c = lib.components.find((x) => x.key === key);
  if (c) return { type: "component", entry: c };
  const f = lib.fittings.find((x) => x.key === key);
  if (f) return { type: "fitting", entry: f };
  const t = lib.twins.find((x) => x.key === key);
  return t ? { type: "twin", entry: t } : null;
}

/** What the search box looks through: every component (name, both
 * wordings, aliases) and every fitting (name, other spellings) that passes
 * the filters. */
export interface LibrarySearchEntry extends SearchEntry {
  ref: LibraryEntry;
}

export function searchEntries(lib: Library, filters: LibraryFilters = NO_FILTERS): LibrarySearchEntry[] {
  const out: LibrarySearchEntry[] = [];
  for (const c of lib.components) {
    if (!componentPasses(c, filters)) continue;
    out.push({
      key: c.key,
      name: c.name,
      wordings: [c.firm_wording, c.firm_wording_hatch],
      aliases: c.aliases.map((a) => a.text),
      uses: c.uses ?? null,
      area: c.area,
      sided: c.side_rule === "sided",
      ref: { type: "component", entry: c },
    });
  }
  for (const x of lib.fittings) {
    if (!fittingPasses(x, filters)) continue;
    out.push({
      key: x.key,
      name: x.name,
      aliases: x.aliases.map((a) => a.text),
      uses: x.uses ?? null,
      area: x.area,
      sided: x.sided,
      ref: { type: "fitting", entry: x },
    });
  }
  return out;
}
