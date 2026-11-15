/**
 * Contract 9 (contract9.md section 7.1): every damage-list item and every
 * suggestion as one Finding, with its evidence -- what the evidence viewer,
 * the suggestion cards and the 3D damage map all show. The evidence UI owns
 * this file; the 3D map imports it only.
 *
 * Imports types only, so the node check scripts (scripts/*.mjs) can load it
 * without a bundler.
 */
import type { Box, DamageSummaryItem, Evidence, ReportData, SecondLookItem, SuggestionCheck } from "./api";

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
  /** Never empty when any photo is known (evidenceOf's fallback). */
  evidence: Evidence[];
  seenIn: string[];
  foundBy: string[];
  confidence: string | null;
  check: SuggestionCheck | null;
  otherSideListed: string | null;
  source: "list" | "zone" | "kit" | "nearby";
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

/** A stored box, when it is one: four numbers. */
function asBox(value: unknown): Box | null {
  return Array.isArray(value) && value.length === 4 && value.every((n) => typeof n === "number" && Number.isFinite(n))
    ? (value as Box)
    : null;
}

function entry(photo: string, box: Box | null, confidence: string | null | undefined): Evidence {
  return {
    photo,
    box,
    box_kind: box ? "ai" : null,
    by: "overall",
    reason: null,
    confidence: confidence && CONFIDENCES.has(confidence) ? (confidence as Evidence["confidence"]) : null,
    img_w: null,
    img_h: null,
  };
}

/** The evidence a finding has stored, or -- for a case from before (4.9) --
 * one entry built from photo_reference + bbox_2d (an AI box when there is
 * one), then the other seen_in photos with no box. At most 4, one a photo. */
function evidenceFrom(
  stored: Evidence[] | undefined,
  photoRef: string | null | undefined,
  bbox: unknown,
  seenIn: string[] | undefined,
  confidence: string | null | undefined,
): Evidence[] {
  if (stored && stored.length) return stored;
  const out: Evidence[] = [];
  const seen = new Set<string>();
  if (photoRef) {
    out.push(entry(photoRef, asBox(bbox), confidence));
    seen.add(photoRef);
  }
  for (const ref of seenIn || []) {
    if (!ref || seen.has(ref) || out.length >= MAX_EVIDENCE) continue;
    out.push(entry(ref, null, null));
    seen.add(ref);
  }
  return out;
}

/** A damage item's evidence (section 4.9's fallback for older items). */
export function evidenceOf(item: DamageSummaryItem): Evidence[] {
  return evidenceFrom(item.evidence, item.photo_reference, item.bbox_2d, item.seen_in, item.ai_confidence);
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
  for (const s of (look.items || []) as SecondLookItem[]) {
    // An old item has no key: its part is its key.
    const key = s.key || s.part;
    const covers = s.covers?.length ? s.covers : [s.part];
    const listed = s.part === OTHER_PART ? listedKeys.has(key) : covers.some((p) => listedParts.has(p));
    if (listed || removed.has(key)) continue;
    // An old dismissed list holds part names.
    const isDismissed = dismissed.has(key) || dismissed.has(s.part);
    if (isDismissed && !opts?.showDismissed) continue;
    const evidence = evidenceFrom(s.evidence, s.photo_reference, null, s.seen_in, s.confidence);
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
      source: s.source ?? "nearby",
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

const readName = (read: string) => READ_NAMES[read] || read.replace(/^zone:/, "");

/** "rear and left side" */
const joined = (names: string[]) =>
  names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;

/** "seen in P07, P12" with the case's labels; a draft's photos ("@<id>")
 * have none yet, so they are counted. */
function seenText(refs: string[]): string {
  if (!refs.length) return "";
  if (refs.every((r) => /^P\d+$/.test(r))) return `seen in ${refs.join(", ")}`;
  return refs.length === 1 ? "seen in 1 photo" : `seen in ${refs.length} photos`;
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
