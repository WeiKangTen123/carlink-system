/**
 * Contract 9 (contract9.md section 7.1): every damage-list item and every
 * suggestion as one Finding, with its evidence -- what the evidence viewer,
 * the suggestion cards and the 3D damage map all show. The evidence UI owns
 * this file; the 3D map imports it only.
 *
 * Imports types only, so the node check scripts (scripts/*.mjs) can load it
 * without a bundler.
 */
import type {
  Box,
  DamageSummaryItem,
  DecideAction,
  DecideResult,
  Evidence,
  ReportData,
  SecondLookItem,
  SuggestionCheck,
  SuggestionDecision,
} from "./api";

export type FindingStatus = "confirmed" | "ai" | "check" | "suggestion" | "dismissed";

export type Finding = {
  /** "item:<index>" (a damage list row) | "sug:<suggestion key>" */
  key: string;
  kind: "item" | "suggestion";
  /** The damage_summary index (kind "item"). */
  index: number | null;
  /** prefill.item_key's equivalent: the suggestion's key, or the item's part
   * (a named Other by its name). */
  partKey: string;
  /** The canonical part, and its display wording. */
  part: string;
  name: string;
  /** item: lib/reviewRows damageState (you = confirmed, check, ai);
   * suggestion: dismissed when its key is in second_look.dismissed. */
  status: FindingStatus;
  /** The AI's own words; null -> the UI shows codeReason(f), not as "AI says". */
  reason: string | null;
  /** Never empty when any photo is known (evidenceOf's fallback). The
   * item's own photo (photo_reference) comes first. */
  evidence: Evidence[];
  seenIn: string[];
  foundBy: string[];
  confidence: string | null;
  check: SuggestionCheck | null;
  otherSideListed: string | null;
  source: "list" | "zone" | "kit" | "nearby";
  /** The Condition in the firm's words ("Cracked"), else the AI's damage
   * type; for the cards and the viewer. Optional: added by the evidence UI. */
  condition?: string | null;
};

export type PhotoInfo = {
  ref: string;
  label: string;
  src: string;
  thumb: string;
  w?: number;
  h?: number;
  area?: string | null;
  deleted?: boolean;
  /** The full-size original, when the page has one (the viewer's "Open
   * original"). Optional: added by the evidence UI. */
  original?: string;
};

/** Accepts "@<photo id>" and "P07". */
export type PhotoLookup = (ref: string) => PhotoInfo | null;

export type Selection = { kind: "finding"; key: string } | { kind: "zone"; zoneKey: string } | { kind: "photo"; ref: string } | null;

// taxonomy.OTHER_PART (lib/api.ts OTHER_PART): a value, so written here too --
// this file imports types only.
const OTHER_PART = "Other / Not Listed";
// Evidence entries kept per finding (bot-service reports/schema.Evidence).
const MAX_EVIDENCE = 4;
const CONFIDENCES = new Set(["High", "Medium", "Low"]);
const CONFIDENCE_RANK: Record<string, number> = { High: 0, Medium: 1, Low: 2 };

const words = (text: string | null | undefined) => (text || "").split(/\s+/).filter(Boolean).join(" ");

/** A named Other's name, else null (lib/api.ts otherPartName). */
function otherName(item: { part: string; other_part_name?: string | null }): string | null {
  return (item.part || "").trim() === OTHER_PART ? words(item.other_part_name) || null : null;
}

/** prefill.item_key: the canonical part, or a named Other by its name
 * ("Other / Not Listed: rear air vent"). */
export function itemKey(item: { part: string; other_part_name?: string | null }): string {
  const other = otherName(item);
  return other ? `${OTHER_PART}: ${other.toLowerCase()}` : (item.part || "").trim();
}

/** A stored box, when it is one: four numbers (lib/evidence.ts cleanBox
 * clamps and checks it before anything is drawn). */
function asBox(value: unknown): Box | null {
  return Array.isArray(value) && value.length === 4 && value.every((n) => typeof n === "number" && Number.isFinite(n))
    ? (value as Box)
    : null;
}

function entry(photo: string, box: Box | null, confidence: string | null | undefined, by: string): Evidence {
  return {
    photo,
    box,
    box_kind: box ? "ai" : null,
    by,
    reason: null,
    confidence: confidence && CONFIDENCES.has(confidence) ? (confidence as Evidence["confidence"]) : null,
    img_w: null,
    img_h: null,
  };
}

/** A finding's evidence, with its own photo (photo_reference) first.
 *
 * - Stored evidence is the AI's record, best first. When a person linked
 *   another photo, that photo goes first with no box (section 4.1: "the
 *   viewer shows the person's photo first"); the AI's photos follow.
 * - bbox_2d is the AI's box on photo_reference, so it beats a sorter's box
 *   or none on that photo (box precedence).
 * - A case from before (4.9) has no evidence: one entry is built from
 *   photo_reference + bbox_2d, then the other seen_in photos with no box. */
function evidenceFrom(
  stored: Evidence[] | undefined | null,
  photoRef: string | null | undefined,
  bbox: unknown,
  seenIn: string[] | undefined | null,
  confidence: string | null | undefined,
  fallbackBy: string,
  /** Where photo_reference came from when the AI's evidence lacks it. */
  ownBy: string,
): Evidence[] {
  const box = asBox(bbox);
  const kept = (stored || []).filter((e) => e && typeof e.photo === "string" && e.photo);
  if (kept.length) {
    if (!photoRef) return kept;
    const at = kept.findIndex((e) => e.photo === photoRef);
    if (at < 0) return [entry(photoRef, box, confidence, box ? "overall" : ownBy), ...kept];
    let own = kept[at];
    if (box && !(asBox(own.box) && own.box_kind === "ai")) own = { ...own, box, box_kind: "ai", img_w: null, img_h: null };
    return [own, ...kept.slice(0, at), ...kept.slice(at + 1)];
  }
  const out: Evidence[] = [];
  const seen = new Set<string>();
  if (photoRef) {
    out.push(entry(photoRef, box, confidence, fallbackBy));
    seen.add(photoRef);
  }
  for (const ref of seenIn || []) {
    if (!ref || seen.has(ref) || out.length >= MAX_EVIDENCE) continue;
    out.push(entry(ref, null, null, fallbackBy));
    seen.add(ref);
  }
  return out;
}

/** A damage item's evidence (section 4.9's fallback for older items). An
 * item with no AI confidence and no read was added by a person: its photo
 * is theirs ("linked"). */
export function evidenceOf(item: DamageSummaryItem): Evidence[] {
  const fallbackBy = item.ai_confidence || item.found_by?.length ? "overall" : "linked";
  // A photo that isn't among the AI's was linked by a person: the main
  // read's own photo is always in its evidence.
  return evidenceFrom(item.evidence, item.photo_reference, item.bbox_2d, item.seen_in, item.ai_confidence, fallbackBy, "linked");
}

/** lib/reviewRows.ts damageState in the finding's words: a person's tick
 * or edit is confirmed, an unsure part or an inferred side is check. */
function itemStatus(item: DamageSummaryItem): FindingStatus {
  if (item.human_verified) return "confirmed";
  if (item.ai_confidence === "Low" || item.side_inferred) return "check";
  return "ai";
}

function itemFinding(item: DamageSummaryItem, index: number): Finding {
  const evidence = evidenceOf(item);
  return {
    key: `item:${index}`,
    kind: "item",
    index,
    partKey: itemKey(item),
    part: item.part,
    name: otherName(item) || item.part,
    status: itemStatus(item),
    reason: item.reason ?? null,
    evidence,
    seenIn: item.seen_in?.length ? item.seen_in : evidence.map((e) => e.photo),
    foundBy: item.found_by ?? [],
    confidence: item.ai_confidence ?? null,
    check: item.check ?? null,
    otherSideListed: null,
    source: "list",
    condition: item.condition || item.damage_type || null,
  };
}

type WithDismissedParts = ReportData & { dismissed_parts?: string[] | null };

/** Every finding of a case: the damage list's items in order, then the
 * suggestions in the order the server sorted them. A suggestion is left
 * out when the list has its part already (covers; a named Other by its
 * key), when a person removed that part from the list (data.dismissed_parts),
 * or when it was marked Not damaged -- unless showDismissed, then it is
 * there with status "dismissed". */
export function buildFindings(data: ReportData, opts?: { showDismissed?: boolean }): Finding[] {
  const items = data.damage_summary || [];
  const out: Finding[] = items.map(itemFinding);
  const look = data.second_look;
  if (!look) return out;
  const listedParts = new Set(items.map((it) => it.part));
  const listedKeys = new Set(items.map(itemKey));
  const removed = new Set((data as WithDismissedParts).dismissed_parts || []);
  const dismissed = new Set(look.dismissed || []);
  const shown = new Set<string>();
  for (const s of (look.items || []) as SecondLookItem[]) {
    // An old item has no key: its part is its key.
    const key = s.key || s.part;
    if (!key || shown.has(key)) continue;
    const covers = s.covers?.length ? s.covers : [s.part];
    const listed = s.part === OTHER_PART ? listedKeys.has(key) : covers.some((p) => listedParts.has(p)) || listedKeys.has(key);
    if (listed || removed.has(key)) continue;
    // An old dismissed list holds part names.
    const isDismissed = dismissed.has(key) || dismissed.has(s.part);
    if (isDismissed && !opts?.showDismissed) continue;
    shown.add(key);
    const source = s.source ?? "nearby";
    // Where a suggestion with no stored evidence came from, for its photo.
    const fallbackBy = source === "zone" ? s.found_by?.[0] || "overall" : source;
    const evidence = evidenceFrom(s.evidence, s.photo_reference, null, s.seen_in, s.confidence, fallbackBy, fallbackBy);
    out.push({
      key: `sug:${key}`,
      kind: "suggestion",
      index: null,
      partKey: key,
      part: s.part,
      name: s.name || otherName(s) || s.part,
      status: isDismissed ? "dismissed" : "suggestion",
      reason: s.reason ?? null,
      evidence,
      seenIn: s.seen_in?.length ? s.seen_in : evidence.map((e) => e.photo),
      foundBy: s.found_by ?? [],
      confidence: s.confidence ?? null,
      check: s.check ?? null,
      otherSideListed: s.other_side_listed ?? null,
      source,
      condition: s.condition || s.damage_type || null,
    });
  }
  return out;
}

const READ_NAMES: Record<string, string> = {
  overall: "main",
  "zone:front": "front",
  "zone:rear": "rear",
  "zone:left": "left side",
  "zone:right": "right side",
  "zone:close": "close-up",
};

/** A read's name in words: "main", "rear", "left side". */
export const readName = (read: string) => READ_NAMES[read] || read.replace(/^zone:/, "");

/** Where one photo's entry came from, in words: "main read", "rear read",
 * "the sorter", "re-check". */
export function sourceName(by: string | null | undefined): string {
  if (!by) return "";
  if (by === "sort") return "the sorter";
  if (by === "check") return "re-check";
  if (by === "second_look") return "second look";
  if (by === "linked") return "linked by hand";
  if (by === "kit") return "past cases";
  if (by === "nearby") return "next to a found part";
  return `${readName(by)} read`;
}

/** "rear and left side" */
const joined = (names: string[]) =>
  names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;

const isLabel = (ref: string) => /^P\d+$/.test(ref);

/** "seen in P07, P12" with the case's labels; a draft's photos ("@<id>")
 * have none yet, so they are counted. */
function seenText(refs: string[]): string {
  if (!refs.length) return "";
  if (refs.every(isLabel)) return `seen in ${refs.join(", ")}`;
  return refs.length === 1 ? "seen in 1 photo" : `seen in ${refs.length} photos`;
}

/** "seen in 2 photos" whatever the refs look like (the cards' facts line). */
export function seenCount(f: Pick<Finding, "seenIn">): string | null {
  const n = new Set(f.seenIn.filter(Boolean)).size;
  return n ? `seen in ${n} photo${n === 1 ? "" : "s"}` : null;
}

/** Why a finding is there, built by code when the AI gave no reason (never
 * shown as "AI says"): "Listed by the main read · seen in P07, P12". */
export function codeReason(f: Finding): string {
  const reads = f.foundBy.filter((r) => r !== "overall").map(readName);
  let why: string;
  if (f.kind === "item") {
    why = f.foundBy.includes("overall")
      ? "Listed by the main read"
      : reads.length
        ? `Listed by the ${joined(reads)} read${reads.length > 1 ? "s" : ""}`
        : f.status === "confirmed"
          ? "On the damage list"
          : "Listed by the AI";
  } else if (reads.length) {
    why = `Found by the ${joined(reads)} read${reads.length > 1 ? "s" : ""}`;
  } else if (f.source === "kit") {
    why = "Often damaged with a part found here (past cases)";
  } else {
    why = "Next to a part found here";
  }
  const seen = seenText(f.seenIn);
  return seen ? `${why} · ${seen}` : why;
}

/** "AI says: “…”" -- the AI's own words, always marked as the AI's. */
export const aiSays = (reason: string) => `AI says: “${reason}”`;

/** "Found by 1 of 5 reads" when the case's reads are known, else "Found by
 * the rear read"; null for a suggestion no read found (kit / nearby). */
export function foundByText(f: Pick<Finding, "foundBy">, reads?: string[] | null): string | null {
  const found = [...new Set(f.foundBy.filter(Boolean))];
  if (!found.length) return null;
  const total = reads?.length ? new Set([...reads, ...found]).size : 0;
  if (total > 1) return `Found by ${found.length} of ${total} reads`;
  return `Found by the ${joined(found.map(readName))} read${found.length > 1 ? "s" : ""}`;
}

/** The reads by name, for the viewer's "Found by: rear read". */
export function foundByNames(f: Pick<Finding, "foundBy">): string | null {
  const found = [...new Set(f.foundBy.filter(Boolean))].map(readName);
  return found.length ? `${joined(found)} read${found.length > 1 ? "s" : ""}` : null;
}

/** "AI: Medium" -- the AI's confidence in words, never a number. */
export const confidenceText = (c: string | null | undefined) => (c && CONFIDENCES.has(c) ? `AI: ${c}` : null);

export const CHECK_WORDS: Record<SuggestionCheck["verdict"], string> = {
  yes: "AI re-check: probably damaged",
  unsure: "AI re-check: unsure",
  no: "AI re-check: probably not damaged",
};

/** The re-check's answer in words (never its probability). */
export function checkWords(check: SuggestionCheck | null | undefined): string | null {
  return check && CHECK_WORDS[check.verdict] ? CHECK_WORDS[check.verdict] : null;
}

const checkGroup = (f: Finding) => (f.check?.verdict === "yes" ? 0 : f.check?.verdict === "no" ? 2 : 1);

/** Suggestions in the re-check's order (section 4.7): "yes" (most likely
 * first), then unsure or not re-checked, then "no" (as the server sorts
 * them: p low to high). Ties go by the AI's confidence, then the order
 * given. The "no" ones are last but stay: they can still be added. */
export function sortByCheck(list: Finding[]): Finding[] {
  return list
    .map((f, i) => ({ f, i }))
    .sort((a, b) => {
      const ga = checkGroup(a.f);
      const gb = checkGroup(b.f);
      if (ga !== gb) return ga - gb;
      const pa = a.f.check?.p;
      const pb = b.f.check?.p;
      if (ga !== 1 && typeof pa === "number" && typeof pb === "number" && pa !== pb) return ga === 0 ? pb - pa : pa - pb;
      const ca = CONFIDENCE_RANK[a.f.confidence || ""] ?? 3;
      const cb = CONFIDENCE_RANK[b.f.confidence || ""] ?? 3;
      return ca - cb || a.i - b.i;
    })
    .map((x) => x.f);
}

/** Each key's decision as it stands: the latest "added" / "dismissed" not
 * reverted by an "undone" (each "undone" reverts the latest one still
 * standing, as the decide endpoint does). Keys whose decisions were all
 * undone are absent. */
export function decisionStates(decisions: SuggestionDecision[] | null | undefined): Map<string, SuggestionDecision> {
  const stacks = new Map<string, SuggestionDecision[]>();
  for (const d of decisions || []) {
    if (!d?.key) continue;
    const stack = stacks.get(d.key) ?? [];
    if (d.action === "undone") stack.pop();
    else stack.push(d);
    stacks.set(d.key, stack);
  }
  const out = new Map<string, SuggestionDecision>();
  for (const [key, stack] of stacks) if (stack.length) out.set(key, stack[stack.length - 1]);
  return out;
}

/** What the latest "undone" on a key reverted ("added" / "dismissed"). */
export function undoneAction(decisions: SuggestionDecision[] | null | undefined, key: string): "added" | "dismissed" | null {
  const stack: SuggestionDecision[] = [];
  let last: "added" | "dismissed" | null = null;
  for (const d of decisions || []) {
    if (d?.key !== key) continue;
    if (d.action === "undone") {
      const popped = stack.pop();
      if (popped) last = popped.action as "added" | "dismissed";
    } else stack.push(d);
  }
  return last;
}

/** "Decided: 2 added, 1 not damaged": from the decisions as they stand,
 * plus any key in an older dismissed list that has no decision. */
export function decidedCounts(
  decisions: SuggestionDecision[] | null | undefined,
  dismissed?: string[] | null,
): { added: number; dismissed: number } {
  const states = decisionStates(decisions);
  let added = 0;
  let notDamaged = 0;
  for (const d of states.values()) {
    if (d.action === "added") added += 1;
    else if (d.action === "dismissed") notDamaged += 1;
  }
  for (const key of dismissed || []) if (!states.has(key)) notDamaged += 1;
  return { added, dismissed: notDamaged };
}

/** The suggestions nobody has decided yet (the filing and sign-off soft
 * warnings count these). */
export function undecidedCount(data: ReportData): number {
  return buildFindings(data).filter((f) => f.kind === "suggestion" && f.status === "suggestion").length;
}

/** The rows of the wizard's list an answer takes off (applyDecision): an
 * undone add's item, wherever it is now. The page moves a selection that
 * named a row by its place (useDamageSelection itemsRemoved). */
export function rowsTakenOff(
  damage: DamageSummaryItem[] | null | undefined,
  r: DecideResult,
  asked?: { key: string; action: DecideAction },
): number[] {
  if (asked?.action !== "undo" || undoneAction(r.second_look?.decisions, asked.key) !== "added") return [];
  const out: number[] = [];
  (damage || []).forEach((it, i) => {
    if (it.added_from === "suggestion" && itemKey(it) === asked.key) out.push(i);
  });
  return out;
}

/** The wizard's local draft after a decide call (section 4.4, conflict #10):
 * second_look is the server's; an added item goes on the end of the list,
 * as the server put it (once: a repeated answer adds nothing); an undone add
 * takes that item back off the list, as the server did -- otherwise the next
 * whole-draft save would put it back. */
export function applyDecision(data: ReportData, r: DecideResult, asked?: { key: string; action: DecideAction }): ReportData {
  const before = data.damage_summary || [];
  let damage = before;
  const action = asked?.action ?? (r.item ? "add" : null);
  if (action === "add" && r.item) {
    const key = itemKey(r.item);
    if (!damage.some((it) => itemKey(it) === key)) damage = [...damage, r.item];
  } else {
    const gone = new Set(rowsTakenOff(damage, r, asked));
    if (gone.size) damage = damage.filter((_, i) => !gone.has(i));
  }
  const next: ReportData = { ...data, second_look: r.second_look ?? data.second_look ?? null };
  if (damage !== before) {
    next.damage_summary = damage;
    next.damaged_parts = damage.map((d) => d.part);
  }
  return next;
}
