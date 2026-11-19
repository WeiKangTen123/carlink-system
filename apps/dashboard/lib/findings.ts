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
  CheckRun,
  DamageSummaryItem,
  DecideAction,
  DecideResult,
  Evidence,
  HitLevel,
  LinkBucket,
  LinkCheck,
  LinkType,
  ReportData,
  SecondLook,
  SecondLookItem,
  SignYes,
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
  /** Contract 10: "link", a part tied to a listed one by the link table
   * (SecondLookItem.source). */
  source: "list" | "zone" | "kit" | "nearby" | "link";
  /** The Condition in the firm's words ("Cracked"), else the AI's damage
   * type; for the cards and the viewer. Optional: added by the evidence UI. */
  condition?: string | null;

  // ---- Contract 10: linked parts (contract10.md 4.1-4.2). Each is there
  // only when the server sent it: a linked-part suggestion, a zone
  // suggestion joined with one, or a list item added from one.
  /** The item keys of the listed parts it hangs off (partKey of their items). */
  linkedFrom?: string[];
  linkType?: LinkType | null;
  /** "L1:rear-bumper>beam": the table's version and link id. */
  linkRule?: string | null;
  /** How hard the listed part was hit. */
  hit?: HitLevel | null;
  bucket?: LinkBucket | null;
  /** Not seen in photos as they are usually taken. */
  hidden?: boolean;
  /** Confirmed only once parts come off. */
  needsStripDown?: boolean;
  /** The listed part whose side it took; absent when it took none. */
  sideFrom?: string | null;
  /** The zone reads that listed it intact ("zone:rear"); an older draft's
   * may be photo refs. Shown with intactText. */
  intactIn?: string[];
  /** The linked check's answer (the AI's own words in its reason). */
  linkCheck?: LinkCheck | null;
  /** The catalogue's diagram names that put it with the listed part. */
  oem?: string[];
  /** Why it is suggested, in the link table's words: the code's reason,
   * never shown as "AI says" (reason stays null for a linked part). */
  linkReason?: string | null;
  /** The listed parts it hangs off, as the cards name them. */
  parents?: LinkParent[];

  // ---- The car map's review screen (K4): each there only when the server
  // sent it (intake_car_map "on"); without them the panel is as before.
  /** Its group: the assembly ("front-bumper", "front-door.left"). */
  assembly?: string;
  assemblyName?: string;
  /** The relation's rank, 1 listed most often .. 5. */
  prior?: number;
  /** 2: a second hop, via the part between. */
  hop?: number;
  via?: string;
  /** "if_fitted": an option some cars lack ("— if fitted"). */
  fitted?: string;
  fittedNote?: string;
  signsYes?: SignYes[];
  /** The group's words for parts with no name yet. */
  alsoCheck?: string[];
  /** In the "Check at strip-down" block, outside the linked parts' cap. */
  folded?: boolean;
};

/** Contract 10: one listed part a linked part hangs off. */
export type LinkParent = {
  /** Its item key (what linked_from holds). */
  key: string;
  /** Its name on the list; the key when it is no longer listed. */
  name: string;
  /** Its condition in the firm's words, else the AI's damage type. */
  condition: string | null;
  /** "item:<index>" while it is on the list, else null. */
  findingKey: string | null;
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

type LinkSource = Pick<
  SecondLookItem,
  "linked_from" | "link_type" | "link_rule" | "hit" | "bucket" | "hidden" | "needs_strip_down" | "side_from" | "intact_in" | "link_check" | "oem"
>;

/** Contract 10: the linked-part fields a suggestion or an item has, and
 * only those (the server leaves the defaults out, and so does a Finding). */
function linkFields(s: Partial<LinkSource>): Partial<Finding> {
  const out: Partial<Finding> = {};
  if (s.linked_from?.length) out.linkedFrom = s.linked_from;
  if (s.link_type) out.linkType = s.link_type;
  if (s.link_rule) out.linkRule = s.link_rule;
  if (s.hit) out.hit = s.hit;
  if (s.bucket) out.bucket = s.bucket;
  if (s.hidden) out.hidden = true;
  if (s.needs_strip_down) out.needsStripDown = true;
  if (s.side_from) out.sideFrom = s.side_from;
  if (s.intact_in?.length) out.intactIn = s.intact_in;
  if (s.link_check) out.linkCheck = s.link_check;
  if (s.oem?.length) out.oem = s.oem;
  return out;
}

/** K4: the car map's fields a suggestion has, and only those. */
function carMapFields(s: SecondLookItem): Partial<Finding> {
  const out: Partial<Finding> = {};
  if (s.assembly) out.assembly = s.assembly;
  if (s.assembly_name) out.assemblyName = s.assembly_name;
  if (typeof s.prior === "number") out.prior = s.prior;
  if (typeof s.hop === "number") out.hop = s.hop;
  if (s.via) out.via = s.via;
  if (s.fitted) out.fitted = s.fitted;
  if (s.fitted_note) out.fittedNote = s.fitted_note;
  if (s.signs_yes?.length) out.signsYes = s.signs_yes.filter((x) => x && typeof x.id === "string");
  if (s.also_check?.length) out.alsoCheck = s.also_check.filter((w) => typeof w === "string" && w.trim());
  if (s.folded) out.folded = true;
  return out;
}

function itemFinding(item: DamageSummaryItem, index: number): Finding {
  const evidence = evidenceOf(item);
  // An item added from a linked part no read found carries the link
  // table's reason: the code's words, never the AI's.
  const codeWords = Boolean(item.link_type) && !item.found_by?.length;
  return {
    key: `item:${index}`,
    kind: "item",
    index,
    partKey: itemKey(item),
    part: item.part,
    name: otherName(item) || item.part,
    status: itemStatus(item),
    reason: codeWords ? null : (item.reason ?? null),
    evidence,
    // The listed part's photo (by "link") is not one this part was seen in.
    seenIn: item.seen_in?.length ? item.seen_in : evidence.filter((e) => e.by !== "link").map((e) => e.photo),
    foundBy: item.found_by ?? [],
    confidence: item.ai_confidence ?? null,
    check: item.check ?? null,
    otherSideListed: null,
    source: "list",
    condition: item.condition || item.damage_type || null,
    ...linkFields(item),
    ...(codeWords && item.reason ? { linkReason: item.reason } : {}),
  };
}

/** The listed parts a finding hangs off, named as the list names them. */
function parentsOf(f: Finding, listed: Map<string, Finding>): LinkParent[] {
  return (f.linkedFrom || []).map((key) => {
    const item = listed.get(key);
    return item
      ? { key, name: item.name, condition: item.condition ?? null, findingKey: item.key }
      : { key, name: key, condition: null, findingKey: null };
  });
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
  // Contract 10: each linked finding names its listed parts (the first item
  // with that key, as the server's linked_from means it).
  const listed = new Map<string, Finding>();
  for (const f of out) if (!listed.has(f.partKey)) listed.set(f.partKey, f);
  const withParents = (list: Finding[]) => {
    for (const f of list) if (f.linkedFrom?.length) f.parents = parentsOf(f, listed);
    return list;
  };
  const look = data.second_look;
  if (!look) return withParents(out);
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
    // Contract 10: a linked part's reason is the link table's (the code's
    // words), and the listed part's photo ("link") is not a photo it was
    // seen in.
    const isLink = source === "link";
    const ownPhotos = isLink ? evidence.filter((e) => e.by !== "link") : evidence;
    out.push({
      key: `sug:${key}`,
      kind: "suggestion",
      index: null,
      partKey: key,
      part: s.part,
      name: s.name || otherName(s) || s.part,
      status: isDismissed ? "dismissed" : "suggestion",
      reason: isLink ? null : (s.reason ?? null),
      evidence,
      seenIn: s.seen_in?.length ? s.seen_in : ownPhotos.map((e) => e.photo),
      foundBy: s.found_by ?? [],
      confidence: s.confidence ?? null,
      check: s.check ?? null,
      otherSideListed: s.other_side_listed ?? null,
      source,
      condition: s.condition || s.damage_type || null,
      ...linkFields(s),
      ...(isLink ? carMapFields(s) : {}),
      ...(isLink && s.reason ? { linkReason: s.reason } : {}),
    });
  }
  return withParents(out);
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
  // Contract 10: the listed part's photo (no box), and the linked check's.
  if (by === "link") return "listed part's photo";
  if (by === "link_check") return "linked check";
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
  // Contract 10: a linked part's reason is the link table's plain sentence.
  if (f.linkReason) return f.linkReason;
  if (f.source === "link") {
    const parent = f.parents?.[0]?.name || f.linkedFrom?.[0];
    return parent ? `Linked to the ${parent} on the list` : "Linked to a part on the list";
  }
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

// ---------------------------------------------------------------------------
// Contract 10: linked parts on the suggestions panel, the viewer and the map

/** How hard the listed part was hit, in words (never a number). */
export const HIT_WORDS: Record<HitLevel, string> = {
  light: "light hit",
  medium: "medium hit",
  heavy: "heavy hit",
  crush: "crushed",
};
const HIT_RANK: Record<string, number> = { light: 0, medium: 1, heavy: 2, crush: 3 };

/** The bucket tags on the cards: Seen / Likely / Confirm at strip-down. */
export const BUCKET_LABELS: Record<LinkBucket, string> = { seen: "Seen", likely: "Likely", strip_down: "Strip-down" };
const BUCKET_RANK: Record<string, number> = { seen: 0, likely: 1, strip_down: 2 };
/** The visible kinds of link first, the crush path last (as the server orders them). */
const TYPE_RANK: Record<string, number> = { attached: 0, opens_with: 1, behind: 2, crush: 3 };

/** Under a hidden part: the owner's words (17 Nov). */
export const STRIP_DOWN_TEXT = "possible – confirm at strip-down";
/** The chip on a list item added from a hidden part. */
export const STRIP_DOWN_CHIP = "Confirm at strip-down";
/** Linked parts shown per listed part before "Show more". */
export const LINKS_PER_PARENT = 4;

const CHECK_VERDICT_WORDS: Record<string, string> = {
  seen: "seen",
  likely: "likely",
  strip_down: "confirm at strip-down",
  no: "probably not",
  cannot_tell: "can't tell",
};

/** "AI check: likely": the linked check's verdict in words. */
export function linkCheckWords(check: LinkCheck | null | undefined): string | null {
  const word = check ? CHECK_VERDICT_WORDS[check.verdict] : null;
  return word ? `AI check: ${word}` : null;
}

/** "AI check: likely — AI says: “lens cracked at the corner”": the verdict
 * and the AI's own words, which (contract 9's UI words) always start with
 * "AI says:". The link table's reasons are the code's and stay plain. */
export function linkCheckLine(check: LinkCheck | null | undefined): string | null {
  const words = linkCheckWords(check);
  if (!words) return null;
  return check?.reason ? `${words} — ${aiSays(check.reason)}` : words;
}

/** What the linked check did (second_look.link_run), beside "Linked parts
 * to check" as the re-check's note is beside the suggestions: "AI check:
 * Checked 1 of 3 areas." / "AI check didn't run. The AI was busy; ...".
 * The notes are the server's own sentences (link_check.NOTE_*). Null when
 * it ran with nothing to say, or never ran. */
export function linkRunNote(run: CheckRun | null | undefined): string | null {
  if (!run || typeof run !== "object") return null;
  const note = typeof run.note === "string" && run.note.trim() ? run.note.trim() : null;
  if (run.status === "failed" || run.status === "skipped")
    return `AI check didn't run. ${note ?? "The linked parts are as the link table placed them."}`;
  return note ? `AI check: ${note}` : null;
}

const isPhotoRef = (ref: string) => ref.startsWith("@") || isLabel(ref);

/** "A zone read (rear) listed it as not damaged. Check it on the car."
 * intact_in holds the zone reads that listed the part intact ("zone:rear":
 * ai_draft.zone_intact keeps no photo per part); a draft from before may
 * hold photo refs ("@<id>" / "P07"), named by their labels. Null when it
 * names nothing still there (a deleted photo). */
export function intactText(intactIn: string[] | null | undefined, photos?: PhotoLookup | null): string | null {
  const reads = new Set<string>();
  const shots = new Set<string>();
  for (const ref of intactIn ?? []) {
    if (typeof ref !== "string" || !ref.trim()) continue;
    if (!isPhotoRef(ref)) {
      reads.add(readName(ref));
      continue;
    }
    const label = photos?.(ref)?.label;
    if (label) shots.add(label);
  }
  if (!reads.size && !shots.size) return null;
  const names = [...reads];
  const who = !names.length
    ? "A zone read"
    : names.length === 1
      ? `A zone read (${names[0]})`
      : `Zone reads (${joined(names)})`;
  const where = shots.size ? ` in ${[...shots].join(", ")}` : "";
  return `${who} listed it as not damaged${where}. Check it on the car.`;
}

/** The bucket a linked part shows in: the server's, else a hidden part is
 * a strip-down one and anything else likely. */
export function linkBucket(f: Pick<Finding, "bucket" | "hidden" | "needsStripDown">): LinkBucket {
  if (f.bucket && f.bucket in BUCKET_RANK) return f.bucket;
  return f.hidden || f.needsStripDown ? "strip_down" : "likely";
}

/** The linked check or the re-check said it is probably not damaged: it
 * goes last in its group (still addable). */
export function isProbablyNot(f: Pick<Finding, "linkCheck" | "check">): boolean {
  return f.linkCheck?.verdict === "no" || f.check?.verdict === "no";
}

/** "same side as Left Rear Door" when the side came from the listed part. */
export function sideFromText(f: Pick<Finding, "sideFrom">): string | null {
  return f.sideFrom ? `same side as ${f.sideFrom}` : null;
}

/** The first listed part a finding hangs off, by name. */
export function parentName(f: Pick<Finding, "parents" | "linkedFrom">): string | null {
  return f.parents?.[0]?.name || f.linkedFrom?.[0] || null;
}

/** "Also linked to: Boot Lid" for a part tied to more than one listed part
 * (its group is the first one's); for a zone suggestion joined with a link,
 * every one. */
export function alsoLinkedText(f: Pick<Finding, "parents" | "linkedFrom" | "source">): string | null {
  const names = (f.parents?.map((p) => p.name) ?? f.linkedFrom ?? []).filter(Boolean);
  const rest = f.source === "link" ? names.slice(1) : names;
  if (!rest.length) return null;
  return `${f.source === "link" ? "Also linked to" : "Linked to"}: ${rest.join(", ")}`;
}

const PLACE_WORDS: Record<LinkType, string> = {
  behind: "sits behind it",
  attached: "sits on it",
  opens_with: "opens with it",
  crush: "sits behind it",
};

/** Under the listed part's photo in the viewer (no box: the linked part is
 * not marked on it): "Photo of the Rear Bumper (this part sits behind it)". */
export function linkPhotoCaption(f: Pick<Finding, "parents" | "linkedFrom" | "linkType">): string {
  const parent = parentName(f) || "listed part";
  const place = f.linkType ? PLACE_WORDS[f.linkType] : null;
  return `Photo of the ${parent} (this part ${place ?? "is linked to it"})`;
}

/** "possible – confirm at strip-down" goes under a linked part the server
 * says needs the parts off (or put in the strip-down bucket). A hidden part
 * that doesn't (a boot lid hinge: open the lid) only gets the Hidden chip. */
export function confirmAtStripDown(f: Pick<Finding, "source" | "bucket" | "hidden" | "needsStripDown">): boolean {
  return f.source === "link" && (Boolean(f.needsStripDown) || linkBucket(f) === "strip_down");
}

/** The viewer's words when a hidden linked part has no photo to show. */
export function hiddenNoPhotoText(f: Pick<Finding, "parents" | "linkedFrom" | "linkType" | "needsStripDown">): string {
  const parent = parentName(f);
  const then = f.needsStripDown ? "confirm at strip-down" : "check it on the car";
  if (!parent) return `Hidden – ${then}`;
  const where = f.linkType === "opens_with" ? "under" : f.linkType === "attached" ? "on" : "behind";
  return `Hidden ${where} the ${parent} – ${then}`;
}

/** An item added from a hidden linked part keeps its "Confirm at strip-down"
 * chip until a person gives it a condition of its own (contract10.md 1.2):
 * blank, or the strip-down wording the add path writes. */
export function needsStripDownChip(item: Pick<DamageSummaryItem, "needs_strip_down" | "condition">): boolean {
  if (!item.needs_strip_down) return false;
  const condition = (item.condition || "").trim();
  return !condition || /strip[\s-]?down/i.test(condition);
}

/** One "Linked to: <listed part>" group of the suggestions panel. */
export type LinkGroup = {
  /** The listed part's item key ("" when the server named none). */
  key: string;
  parent: LinkParent | null;
  /** The hardest hit among its parts. */
  hit: HitLevel | null;
  /** Shown: seen, likely, then strip-down; by kind of link; then as the
   * server sorted them. */
  rows: Finding[];
  /** Along the crush path: collapsed behind "> N more along the crush path". */
  crush: Finding[];
  /** The checks said probably not: last in the group, still addable. */
  probablyNot: Finding[];
};

/** "Linked to: Rear Bumper (Cracked/deformed · crushed)" */
export function linkGroupTitle(g: Pick<LinkGroup, "key" | "parent" | "hit">): string {
  const name = g.parent?.name || g.key || "a part on the list";
  const detail = [g.parent?.condition, g.hit ? HIT_WORDS[g.hit] : null].filter(Boolean).join(" · ");
  return `Linked to: ${name}${detail ? ` (${detail})` : ""}${g.parent && !g.parent.findingKey ? " – no longer on the list" : ""}`;
}

/** K4: one assembly group of the suggestions panel (the car map on). */
export type AssemblyGroup = {
  /** The item's assembly ("front-bumper", "front-door.left"). */
  key: string;
  /** Its header ("Front bumper assembly"). */
  name: string;
  /** The listed parts its linked parts hang off, first seen first, each
   * with the hardest hit its parts give it (null: none says). */
  parents: { parent: LinkParent; hit: HitLevel | null }[];
  /** The yes signs of its parts, once each. */
  signs: SignYes[];
  /** "Also check (no part name yet)": the group's words, once each. */
  alsoCheck: string[];
  /** As a LinkGroup's: the server's order inside each bucket (its prior
   * order), the crush path collapsed, the checks' "probably not" last. */
  rows: Finding[];
  crush: Finding[];
  probablyNot: Finding[];
};

export type SuggestionSections = {
  /** Any linked part among them: without one the panel looks as before. */
  hasLinks: boolean;
  /** Found by a zone read (in the re-check's order, "no" last). Without
   * linked parts: every suggestion, as before. */
  seen: Finding[];
  /** Past cases / next to a found part (only split out with linked parts). */
  other: Finding[];
  /** The linked parts without an assembly, by the listed part they hang off
   * (contract 10; every linked part when none has an assembly). */
  groups: LinkGroup[];
  /** K4: the linked parts with an assembly, by assembly ([] without). */
  assemblies: AssemblyGroup[];
  /** K4: the folded "Check at strip-down" block, outside the cap ([] when
   * the server folded none). */
  stripDown: Finding[];
};

const linkOrder = (list: { f: Finding; i: number }[]) =>
  list
    .sort(
      (a, b) =>
        (BUCKET_RANK[linkBucket(a.f)] ?? 3) - (BUCKET_RANK[linkBucket(b.f)] ?? 3) ||
        (TYPE_RANK[a.f.linkType || ""] ?? 4) - (TYPE_RANK[b.f.linkType || ""] ?? 4) ||
        a.i - b.i,
    )
    .map((x) => x.f);

/** The server's order inside each bucket (K4: its prior order, which the
 * kind-of-link order would undo). */
const bucketOrder = (list: { f: Finding; i: number }[]) =>
  list.sort((a, b) => (BUCKET_RANK[linkBucket(a.f)] ?? 3) - (BUCKET_RANK[linkBucket(b.f)] ?? 3) || a.i - b.i).map((x) => x.f);

const hardest = (hits: (HitLevel | null | undefined)[]): HitLevel | null => {
  const known = hits.filter((h): h is HitLevel => Boolean(h && h in HIT_RANK));
  return known.length ? known.reduce((a, b) => (HIT_RANK[b] > HIT_RANK[a] ? b : a)) : null;
};

/** K4: the server put it in the folded "Check at strip-down" block, and it
 * is still a strip-down part (a second hop's: still unseen) -- the linked
 * check may have seen it since, and then it shows in its group. */
export function inStripDownBlock(f: Pick<Finding, "folded" | "hop" | "bucket" | "hidden" | "needsStripDown">): boolean {
  if (!f.folded) return false;
  return f.hop === 2 ? linkBucket(f) !== "seen" : linkBucket(f) === "strip_down";
}

/** The suggestions panel's sections (contract10.md 1.1, plan.md P5): zone
 * suggestions under "Seen in photos" as before; linked parts grouped by the
 * listed part they hang off, in buckets, the crush path collapsed and the
 * "probably not" ones last. A part tied to several listed parts sits in the
 * first one's group. Nothing here is ticked: these are suggestions only.
 *
 * K4 (the car map on): a linked part with an assembly is in its assembly's
 * group instead (in the server's order inside each bucket: its prior
 * order), and one the server folded is in the "Check at strip-down" block
 * after every group. Without those fields: exactly as before. */
export function suggestionSections(list: Finding[]): SuggestionSections {
  const links = list.filter((f) => f.source === "link");
  if (!links.length) return { hasLinks: false, seen: sortByCheck(list), other: [], groups: [], assemblies: [], stripDown: [] };
  const rest = list.filter((f) => f.source !== "link");
  const groups = new Map<string, { parent: LinkParent | null; items: { f: Finding; i: number }[] }>();
  const assemblies = new Map<string, { name: string; items: { f: Finding; i: number }[] }>();
  const block: { f: Finding; i: number }[] = [];
  links.forEach((f, i) => {
    if (inStripDownBlock(f)) {
      block.push({ f, i });
      return;
    }
    if (f.assembly) {
      const a = assemblies.get(f.assembly) ?? { name: f.assemblyName || "", items: [] };
      if (!a.name && f.assemblyName) a.name = f.assemblyName;
      a.items.push({ f, i });
      assemblies.set(f.assembly, a);
      return;
    }
    const key = f.linkedFrom?.[0] ?? "";
    const g = groups.get(key) ?? { parent: f.parents?.[0] ?? null, items: [] };
    g.items.push({ f, i });
    groups.set(key, g);
  });
  const split = (items: { f: Finding; i: number }[]) => {
    const not = items.filter((x) => isProbablyNot(x.f));
    const kept = items.filter((x) => !isProbablyNot(x.f));
    const crush = kept.filter((x) => x.f.linkType === "crush" && linkBucket(x.f) !== "seen");
    const rows = kept.filter((x) => !crush.includes(x));
    return { not, crush, rows };
  };
  return {
    hasLinks: true,
    seen: sortByCheck(rest.filter((f) => f.source === "zone")),
    other: sortByCheck(rest.filter((f) => f.source !== "zone")),
    groups: [...groups.entries()].map(([key, g]) => {
      const { not, crush, rows } = split(g.items);
      const hit = hardest(g.items.map((x) => x.f.hit));
      return { key, parent: g.parent, hit, rows: linkOrder(rows), crush: linkOrder(crush), probablyNot: linkOrder(not) };
    }),
    assemblies: [...assemblies.entries()].map(([key, a]) => {
      const { not, crush, rows } = split(a.items);
      const parents = new Map<string, { parent: LinkParent; hits: (HitLevel | null | undefined)[] }>();
      for (const { f } of a.items) {
        const named: LinkParent[] = f.parents ?? (f.linkedFrom ?? []).map((k) => ({ key: k, name: k, condition: null, findingKey: null }));
        named.forEach((p, n) => {
          const held = parents.get(p.key) ?? { parent: p, hits: [] };
          // A part's hit is its first listed part's.
          if (n === 0) held.hits.push(f.hit);
          parents.set(p.key, held);
        });
      }
      const signs = new Map<string, SignYes>();
      for (const { f } of a.items) for (const sign of f.signsYes ?? []) if (!signs.has(sign.id)) signs.set(sign.id, sign);
      return {
        key,
        name: a.name || assemblyFallbackName(key),
        parents: [...parents.values()].map((p) => ({ parent: p.parent, hit: hardest(p.hits) })),
        signs: [...signs.values()],
        alsoCheck: [...new Set(a.items.flatMap((x) => x.f.alsoCheck ?? []))],
        rows: bucketOrder(rows),
        crush: bucketOrder(crush),
        probablyNot: bucketOrder(not),
      };
    }),
    stripDown: [...block.filter((x) => !isProbablyNot(x.f)), ...block.filter((x) => isProbablyNot(x.f))].map((x) => x.f),
  };
}

/** The suggestions in the order the panel shows them, so the viewer's A / N
 * move on to the card below (the collapsed ones in their places). Without
 * linked parts: the re-check's order, as before. K4: the assembly groups,
 * then the listed parts' groups, then the folded strip-down block. */
export function panelOrder(list: Finding[]): Finding[] {
  const s = suggestionSections(list);
  if (!s.hasLinks) return s.seen;
  return [
    ...s.seen,
    ...s.other,
    ...s.assemblies.flatMap((g) => [...g.rows, ...g.crush, ...g.probablyNot]),
    ...s.groups.flatMap((g) => [...g.rows, ...g.crush, ...g.probablyNot]),
    ...s.stripDown,
  ];
}

// ---------------------------------------------------------------------------
// K4: the car map's words on the suggestions panel and the viewer

/** An assembly with no name sent: its id in words ("front-door.left" ->
 * "Front door assembly"). */
export function assemblyFallbackName(key: string): string {
  const base = (key.split(".")[0] || "").replace(/-/g, " ").trim();
  return base ? `${base.charAt(0).toUpperCase()}${base.slice(1)} assembly` : "Linked parts";
}

/** "damaged: Front Bumper (heavy hit), Front Grille (crushed)" */
export function assemblyDamagedText(g: Pick<AssemblyGroup, "parents">): string | null {
  const names = g.parents.map(({ parent, hit }) => {
    const detail = [parent.condition, hit ? HIT_WORDS[hit] : null].filter(Boolean).join(" · ");
    const gone = parent.findingKey ? "" : " – no longer on the list";
    return `${parent.name}${detail ? ` (${detail})` : ""}${gone}`;
  });
  return names.length ? `damaged: ${names.join(", ")}` : null;
}

/** "— if fitted" (and the catalogue's note) after the part's name. */
export function fittedText(f: Pick<Finding, "fitted" | "fittedNote">): string | null {
  if (f.fitted !== "if_fitted") return f.fittedNote ? `(${f.fittedNote})` : null;
  return f.fittedNote ? `— if fitted (${f.fittedNote})` : "— if fitted";
}

/** A yes sign in words: "Is the bumper cover pushed inward? yes · P03". */
export function signText(sign: SignYes, photos?: PhotoLookup | null): string {
  const ask = (sign.ask || sign.id).trim();
  const label = sign.photo ? photos?.(sign.photo)?.label || (isLabel(sign.photo) ? sign.photo : null) : null;
  return `${ask} yes${label ? ` · ${label}` : ""}`;
}

/** "Also check (no part name yet): absorber, side brackets" */
export function alsoCheckText(words: string[] | null | undefined): string | null {
  const list = [...new Set((words ?? []).map((w) => w.trim()).filter(Boolean))];
  return list.length ? `Also check (no part name yet): ${list.join(", ")}` : null;
}

/** "second hop, via Air Conditioning Condenser" for a hop-2 part. */
export function hopText(f: Pick<Finding, "hop" | "via">): string | null {
  if (f.hop !== 2) return null;
  return f.via ? `second hop, via ${f.via}` : "second hop";
}

/** The block's line after its count: "not counted in the parts above · 2
 * via a second hop". */
export function stripDownBlockNote(list: Pick<Finding, "hop">[]): string {
  const hops = list.filter((f) => f.hop === 2).length;
  return `not counted in the parts above${hops ? ` · ${hops} via a second hop` : ""}`;
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

// reports/schema.MAX_DECISIONS: the server keeps the latest 200 decisions.
const MAX_DECISIONS = 200;

const sameDecision = (a: SuggestionDecision | undefined, b: SuggestionDecision | undefined) =>
  Boolean(a && b) && a!.key === b!.key && a!.action === b!.action && a!.at === b!.at && (a!.by_id ?? null) === (b!.by_id ?? null);

/** Contract 10 (review L7): is `incoming`, the second_look an autosave
 * answered with, older than the wizard's own? The server takes the save and
 * the decisions in turn, but the answers can land out of turn: a save's look
 * landing after a later decision's would put a just-dismissed card back.
 *
 * The server only ever appends a decision (add, dismiss and undo alike; past
 * MAX_DECISIONS the oldest go), so a look with fewer decisions than ours --
 * or, at the cap, without our latest one -- was stored before ours. */
export function isOlderLook(incoming: SecondLook | null | undefined, local: SecondLook | null | undefined): boolean {
  if (!incoming) return true;
  if (!local) return false;
  const mine = Array.isArray(local.decisions) ? local.decisions : [];
  const theirs = Array.isArray(incoming.decisions) ? incoming.decisions : [];
  if (mine.length < MAX_DECISIONS && theirs.length < MAX_DECISIONS) return theirs.length < mine.length;
  const last = mine[mine.length - 1];
  return Boolean(last) && !theirs.some((d) => sameDecision(d, last));
}

/** The page's second_look after a save answered with suggestions (the
 * wizard's autosave, the case page's added part; review L7).
 *
 * - Since the review the api answers `linked`: only the suggestions that
 *   save joined. They go on the end of ours (as the server appends them),
 *   once each, and ours keeps its own decisions -- nothing to roll back.
 * - An older api answers only the whole second_look: taken unless it is
 *   older than ours (isOlderLook).
 *
 * Returns `local` itself when nothing changes. */
export function withSavedLook(
  local: SecondLook | null | undefined,
  answer: { second_look?: SecondLook | null; linked?: SecondLookItem[] | null },
): SecondLook | null {
  const mine = local ?? null;
  if (Array.isArray(answer.linked) && mine) {
    const items = Array.isArray(mine.items) ? mine.items : [];
    const have = new Set(items.map((it) => it.key || it.part));
    const fresh: SecondLookItem[] = [];
    for (const it of answer.linked) {
      const key = it && (it.key || it.part);
      if (!key || have.has(key)) continue;
      have.add(key);
      fresh.push(it);
    }
    return fresh.length ? { ...mine, items: [...items, ...fresh] } : mine;
  }
  const look = answer.second_look ?? null;
  if (!look) return mine;
  return isOlderLook(look, mine) ? mine : look;
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
