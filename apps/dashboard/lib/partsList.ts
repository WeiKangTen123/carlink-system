/** The part names a person may pick (tab 1's part select and add row, the
 * Review step's part selects), from GET /taxonomy.
 *
 * `parts` is the canonical list (bot-service taxonomy.CANONICAL_PARTS).
 * `car_map_parts` are the car map's own names (taxonomy.CAR_MAP_PARTS: a
 * bumper's absorber and brackets, a door's lock, hinges and handle ...),
 * offered only while the server says `car_map_names` (its setting
 * intake_car_map_names is "on"). A case that already holds one shows it
 * whatever the setting: the selects keep a value outside their options.
 *
 * Pure, with a type-only import, so Node can load it:
 * scripts/check-carmap-names.mjs.
 */
import type { Taxonomy } from "./api";

/** taxonomy.OTHER_PART, written here as findings.ts does (api.ts isn't
 * loadable outside Next.js). */
const OTHER = "Other / Not Listed";

type PartLists = Pick<Taxonomy, "parts"> & Partial<Pick<Taxonomy, "car_map_parts" | "car_map_names">>;

const names = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

/** The car map's names when the server offers them, else none. */
export function carMapPartsOffered(t: PartLists | null | undefined): string[] {
  return t?.car_map_names === true ? names(t.car_map_parts) : [];
}

/** The parts to pick from: the canonical list, then -- names on -- the car
 * map's, "Other / Not Listed" kept last. With the names off (or an API from
 * before them) exactly `parts`. */
export function partsToPick(t: PartLists | null | undefined): string[] {
  const parts = names(t?.parts);
  const extra = carMapPartsOffered(t).filter((p) => !parts.includes(p));
  if (!extra.length) return parts;
  const other = parts.includes(OTHER) ? [OTHER] : [];
  return [...parts.filter((p) => p !== OTHER), ...extra, ...other];
}

/** A car map name (offered or not): a stored one is a part on the list,
 * not free text "as recorded". */
export function isCarMapPart(t: PartLists | null | undefined, part: string): boolean {
  return names(t?.car_map_parts).includes(part);
}
