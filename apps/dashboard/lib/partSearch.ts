/** Finding a part by what people type (plans/2026-11-23-report-and-parts/
 * L-library-cycle.md B.2). Shared by the Knowledge page's parts library and,
 * next, the part picker (step 3A): one matcher, so a word finds the same
 * part everywhere.
 *
 * How it matches:
 *   - lower case, punctuation off ("Sill / Rocker Panel" = "sill rocker panel");
 *   - side words are taken out of the words and become the side: LH, L/H,
 *     L.H., LHS, L.H.S., left, left hand (side), nearside, N/S -- and the
 *     same for the right, with offside and O/S. Malaysia drives on the left,
 *     so the near side (the kerb side) is the LEFT and the off side the
 *     RIGHT. A trailing "side" goes with them ("lh side"). The side never
 *     hides a part: a part with LH / RH gets it (SearchHit.side, the picker's
 *     toggle), a part without one is found as usual and the side simply
 *     doesn't apply ("rear bumper lh" still finds the rear bumper);
 *   - singular and plural are one word ("clips" = "clip", "hinges" =
 *     "hinge", "glasses" = "glass");
 *   - one wrong letter -- or two letters swapped -- is forgiven in a word of
 *     5 letters or more ("ganrish" finds garnish), never in a shorter one;
 *   - the last word may be half typed ("rear bum" finds the rear bumper).
 *
 * Order: an exact name, then a name that starts with the words, then one
 * with all the words, then another name (an alias), then a close spelling.
 * A "name" is the standard name or the firm's printed wording (sedan or
 * tail-gate). Ties: more uses first, then -- when the query named a side --
 * the parts that have one, then the preferred area, then the shorter name,
 * then A-Z.
 *
 * Pure: no React, no fetch, no imports. Node loads it directly
 * (scripts/check-library.mjs). */

export type Side = "left" | "right";

/** How well an entry matched, best first. */
export type MatchTier = "exact" | "starts" | "words" | "alias" | "near";
export const TIER_ORDER: MatchTier[] = ["exact", "starts", "words", "alias", "near"];
export const TIER_LABELS: Record<MatchTier, string> = {
  exact: "exact name",
  starts: "starts with",
  words: "all words",
  alias: "other name",
  near: "close spelling",
};

/** One thing a person may be looking for: a component, a fitting... */
export interface SearchEntry {
  key: string;
  /** The standard name. */
  name: string;
  /** Its other names: the firm's wording, both bodies. */
  wordings?: (string | null | undefined)[];
  /** Other names and known misspellings ("Hood", "ganrish"). */
  aliases?: (string | null | undefined)[];
  /** Cases that list it; unknown (not staff) counts as 0. */
  uses?: number | null;
  /** The library area ("front", "rear", ...). */
  area?: string | null;
  /** false: its name never takes LH / RH, so a side in the query doesn't
   * apply to it (it is still found). */
  sided?: boolean | null;
}

export interface ParsedQuery {
  /** The words without the side words, normalised. */
  norm: string;
  /** The same without spaces ("tailgate" matches "tail gate"). */
  compact: string;
  words: string[];
  /** The side the query named; null for none, or for both. */
  side: Side | null;
}

export interface SearchHit<T extends SearchEntry> {
  entry: T;
  tier: MatchTier;
  /** The text that matched (the name, a wording or an alias). */
  matched: string;
  /** The side the query named, for an entry that takes one; null when the
   * query named none or the entry has no side. */
  side: Side | null;
}

export interface SearchOptions {
  /** Only entries in this area. */
  area?: string | null;
  /** On a tie, entries in this area first. */
  preferArea?: string | null;
  /** At most this many hits (all by default). */
  limit?: number;
}

// A side word, then an optional "side": left, left hand, left-hand side,
// lh, l/h, l.h., lhs, l.h.s., nearside, near side, n/s (and the right's).
const sideWords = (word: string, letter: string, near: string) =>
  new RegExp(
    `(?<![a-z0-9])(?:${word}(?:\\s*-?\\s*hand)?|${letter}\\s*[/.]?\\s*h\\s*[/.]?\\s*s?|${near}\\s*-?\\s*side|${near[0]}\\s*[/.]\\s*s)` +
      `(?:\\s*-?\\s*side)?(?![a-z0-9])\\.?`,
    "g",
  );
const LEFT = sideWords("left", "l", "near");
const RIGHT = sideWords("right", "r", "off");

/** Lower case, anything but letters and digits as one space. */
export function normalize(text: string | null | undefined): string {
  return (text ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/** The text without its side words, and the side they named. */
export function liftSide(text: string | null | undefined): { text: string; side: Side | null } {
  const lower = (text ?? "").toLowerCase();
  const left = new RegExp(LEFT.source).test(lower);
  const right = new RegExp(RIGHT.source).test(lower);
  const rest = lower.replace(LEFT, " ").replace(RIGHT, " ");
  return { text: rest, side: left === right ? null : left ? "left" : "right" };
}

/** One form for singular and plural: "clips" -> "clip", "hinges" ->
 * "hinge", "brushes" -> "brush", "assemblies" -> "assembly"; "glass",
 * "chassis" and short words stay as they are. */
export function stem(word: string): string {
  if (word.length <= 3 || /(ss|us|is)$/.test(word)) return word;
  if (word.endsWith("ies") && word.length > 4) return `${word.slice(0, -3)}y`;
  if (/(ss|sh|ch|x|z)es$/.test(word)) return word.slice(0, -2);
  return word.endsWith("s") ? word.slice(0, -1) : word;
}

/** Edits between two words, a swap of two neighbours counting as one
 * (optimal string alignment). */
export function typoDistance(a: string, b: string): number {
  if (a === b) return 0;
  const rows: number[][] = [];
  for (let i = 0; i <= a.length; i++) {
    rows.push([i]);
    for (let j = 1; j <= b.length; j++) rows[i].push(i === 0 ? j : 0);
  }
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let d = Math.min(rows[i - 1][j] + 1, rows[i][j - 1] + 1, rows[i - 1][j - 1] + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) d = Math.min(d, rows[i - 2][j - 2] + 1);
      rows[i][j] = d;
    }
  }
  return rows[a.length][b.length];
}

/** A query word 5+ letters long one letter (or one swap) away from a word. */
export function nearWord(queryWord: string, word: string): boolean {
  if (queryWord.length < 5 || Math.abs(queryWord.length - word.length) > 1) return false;
  return typoDistance(queryWord, word) <= 1 || typoDistance(stem(queryWord), stem(word)) <= 1;
}

export function parseQuery(text: string | null | undefined): ParsedQuery {
  const { text: rest, side } = liftSide(text);
  const norm = normalize(rest);
  return { norm, compact: norm.replace(/ /g, ""), words: norm ? norm.split(" ") : [], side };
}

interface Prepared {
  text: string;
  norm: string;
  compact: string;
  words: string[];
}

function prepare(text: string): Prepared {
  const norm = normalize(liftSide(text).text);
  return { text, norm, compact: norm.replace(/ /g, ""), words: norm ? norm.split(" ") : [] };
}

function wordMatches(queryWord: string, word: string, last: boolean, typo: boolean): boolean {
  if (stem(queryWord) === stem(word)) return true;
  if (last && queryWord.length >= 2 && word.startsWith(queryWord)) return true;
  return typo && nearWord(queryWord, word);
}

function allWords(q: ParsedQuery, target: Prepared, typo: boolean): boolean {
  return q.words.every((w, i) => target.words.some((t) => wordMatches(w, t, i === q.words.length - 1, typo)));
}

const exactly = (q: ParsedQuery, t: Prepared) => t.norm === q.norm || (t.compact !== "" && t.compact === q.compact);
const startsWith = (q: ParsedQuery, t: Prepared) => t.norm.startsWith(q.norm) || t.compact.startsWith(q.compact);

const prepared = new WeakMap<SearchEntry, { names: Prepared[]; aliases: Prepared[] }>();

function texts(entry: SearchEntry): { names: Prepared[]; aliases: Prepared[] } {
  let got = prepared.get(entry);
  if (!got) {
    const clean = (list: (string | null | undefined)[] | undefined) => (list ?? []).filter((t): t is string => Boolean(t && t.trim()));
    got = {
      names: [entry.name, ...clean(entry.wordings)].map(prepare).filter((p) => p.norm),
      aliases: clean(entry.aliases).map(prepare).filter((p) => p.norm),
    };
    prepared.set(entry, got);
  }
  return got;
}

/** How an entry matches a parsed query, or null when it doesn't. */
export function matchTier(entry: SearchEntry, q: ParsedQuery): { tier: MatchTier; matched: string } | null {
  if (!q.words.length) return null;
  const { names, aliases } = texts(entry);
  const first = (list: Prepared[], test: (t: Prepared) => boolean) => list.find(test)?.text;
  let hit: string | undefined;
  if ((hit = first(names, (t) => exactly(q, t)))) return { tier: "exact", matched: hit };
  if ((hit = first(names, (t) => startsWith(q, t)))) return { tier: "starts", matched: hit };
  if ((hit = first(names, (t) => allWords(q, t, false)))) return { tier: "words", matched: hit };
  if ((hit = first(aliases, (t) => exactly(q, t) || startsWith(q, t) || allWords(q, t, false)))) return { tier: "alias", matched: hit };
  if ((hit = first([...names, ...aliases], (t) => allWords(q, t, true)))) return { tier: "near", matched: hit };
  return null;
}

/** The entries matching `query`, best first (see the top of the file). An
 * empty query (or one of side words only) finds nothing: the caller shows
 * its own list then. */
export function searchParts<T extends SearchEntry>(entries: T[], query: string, opts: SearchOptions = {}): SearchHit<T>[] {
  const q = parseQuery(query);
  if (!q.words.length) return [];
  const hits: SearchHit<T>[] = [];
  for (const entry of entries) {
    if (opts.area && entry.area !== opts.area) continue;
    const m = matchTier(entry, q);
    if (m) hits.push({ entry, ...m, side: entry.sided === false ? null : q.side });
  }
  const rank = (t: MatchTier) => TIER_ORDER.indexOf(t);
  hits.sort(
    (a, b) =>
      rank(a.tier) - rank(b.tier) ||
      (b.entry.uses ?? 0) - (a.entry.uses ?? 0) ||
      Number(b.side !== null) - Number(a.side !== null) ||
      Number(Boolean(opts.preferArea) && b.entry.area === opts.preferArea) - Number(Boolean(opts.preferArea) && a.entry.area === opts.preferArea) ||
      a.entry.name.length - b.entry.name.length ||
      a.entry.name.localeCompare(b.entry.name),
  );
  return opts.limit ? hits.slice(0, opts.limit) : hits;
}
