"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ArrowLeft, ArrowRight, Box as BoxIcon, ChevronDown, ChevronRight, ImagePlus, Plus, Search, Trash2 } from "lucide-react";
import {
  DAMAGE_REMARKS,
  MAX_OTHER_NAME,
  OTHER_PART,
  cleanPartName,
  conditionWordsOf,
  damagePartLabel,
  fileUrl,
  toDamageRemarks,
  type DamageSummaryItem,
  type DecideAction,
  type ReportData,
  type Taxonomy,
} from "@/lib/api";
import { ConditionPicker, conditionWordsIn } from "@/components/ConditionPicker";
import { ReviewBadge } from "@/components/ReviewBadge";
import { ReviewConfirmButton, ReviewUndoButton } from "@/components/ReviewConfirmButton";
import type { Review } from "@/lib/review";
import { damageCheckDetail } from "@/lib/reviewRows";
import {
  checkAdd,
  filingProblems,
  gapWords,
  printsAs,
  removeAt,
  restoreRemoved,
  rowGaps,
  rowState as reviewRowState,
  type AddCheck,
  type Removed,
} from "@/lib/reviewChecks";
import { partsToPick } from "@/lib/partsList";
import { PartPicker } from "@/components/PartPicker";
import { presetFromPhoto, presetFromZone, type PartPick, type PickerPreset } from "@/lib/partPicker";
import { CoverPageFields } from "@/components/CoverPageFields";
import { canViewAllCases, isAdmin, useCurrentUser } from "@/components/CurrentUser";
import { aiReadFrom, coverFormFrom, parseDepth, type AiRead, type CoverForm, type CoverFormKey } from "@/lib/coverForm";
import { DOCUMENT_KINDS, PHOTO_KIND_LABELS, photoForReference, type IntakePhoto } from "@/lib/intake";
import { applyCoverPatch, isEvidencePhoto } from "@/lib/intakeReview";
import { COVER_REVIEW_KEYS, coverViews, dropNaFor, keysInState, markChecked, withStates, type ReviewAction } from "@/lib/fieldReview";
import { ConfirmAiButton } from "@/components/FieldReviewControls";
import { ReviewProgress } from "@/components/ReviewProgress";
import { DamageMapPanel } from "@/components/DamageMapPanel";
import { EvidenceViewer, type EvidenceViewerProps } from "@/components/EvidenceViewer";
import { PhotoBoxes } from "@/components/PhotoBoxes";
import { SuggestionPanel } from "@/components/SuggestionPanel";
import { boxChip, slidesOf } from "@/lib/evidence";
import {
  STRIP_DOWN_CHIP,
  applyDecision,
  buildFindings,
  needsStripDownChip,
  rowsTakenOff,
  type PhotoInfo,
  type PhotoLookup,
  type Selection,
} from "@/lib/findings";
import { useDamageSelection } from "@/lib/useDamageSelection";
import { suggestionKey, useSuggestionDecisions } from "@/lib/useSuggestionDecisions";
import { detectBodyType, zoneLabel } from "@/lib/vehicleZones";
import { PhotoPicker } from "./PhotoPicker";
import { PhotosByPart } from "./PhotosByPart";
import { ReadByNotice } from "./ReadByNotice";
import { AddCheckDialog } from "./ReviewCheckDialogs";
import { UndoToast } from "./UndoToast";

type Props = {
  /** The draft's id, for the suggestions' decide endpoint. */
  draftId: string;
  data: ReportData;
  onChange: (update: (d: ReportData) => ReportData) => void;
  photos: IntakePhoto[];
  readPhotoIds: string[];
  /** F1: GET /intake/{id}'s read_notice -- the main read failed and the
   * list comes from the area reads. Shown as a banner; null: none. */
  readNotice?: string | null;
  taxonomy: Taxonomy;
  aiSkipped: boolean;
  /** What the AI last read for the cover fields, when the API sent it
   * (IntakeState.ai_read / ai_read_photos): shown as "(P03)" tags beside a
   * field that still says what the AI said. */
  aiRead?: AiRead;
  aiReadPhotos?: Record<string, string> | null;
  /** Field review (IntakeState.review): absent from an older API. */
  review?: Review | null;
  /** The draft as the server last sent it -- what `review` describes. */
  savedData?: ReportData | null;
  /** Phase 1 F: a damage row to bring into view on arrival (the check
   * before filing's [Go to it]), and what to put the cursor in; a new
   * nonce asks again. */
  reveal?: RevealRequest | null;
  /** Called once `reveal` has been shown, so it isn't shown again. */
  onRevealed?: () => void;
  onBack: () => void;
  onNext: () => void;
};

/** Which control of a damage row gets the cursor. */
export type RowFocus = "part" | "name" | "condition";
export type RevealRequest = { index: number; focus: RowFocus; nonce: number };

/** A select that still shows a value outside its option list (an older or
 * unexpected AI answer) instead of silently snapping to blank. */
function Select({
  value,
  options,
  onChange,
  placeholder,
  className,
  ariaLabel,
}: {
  value: string;
  options: readonly string[];
  onChange: (v: string) => void;
  placeholder: string;
  className?: string;
  ariaLabel?: string;
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className={className} aria-label={ariaLabel}>
      <option value="">{placeholder}</option>
      {value && !options.includes(value) && <option value={value}>{value}</option>}
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

/** What the evidence viewer was opened on (contract9.md section 7.3): a
 * finding from the table, a card or the map, or a photo from Photos by part
 * (with that group's photos to step through). */
type ViewerState = Pick<EvidenceViewerProps, "mode" | "startKey" | "startPhoto" | "photoOrder">;

/** Where to bring a damage row into view once it has rendered. */
type Reveal = { key: string; block: ScrollLogicalPosition; focus?: RowFocus };

const FLASH_MS = 2000;
/** How long "Removed Rear Bumper · Undo" stays up (P-parts gap 6). */
const PART_UNDO_MS = 10_000;

/** The control a RowFocus names, in a damage row (the cells' data-col). */
const FOCUS_SELECTOR: Record<RowFocus, string> = {
  part: 'td[data-col="part"] select',
  name: 'td[data-col="part"] input',
  condition: 'td[data-col="condition"] select',
};

/** The tyre fields that are numbers on the draft (TyreItem.tread_depth_mm). */
const MM_KEYS: readonly CoverFormKey[] = ["front_nearside_mm", "front_offside_mm", "rear_nearside_mm", "rear_offside_mm"];

/** Whether the text in a cover field still means what the draft holds for
 * it: the same number for a tread depth, the same trimmed text otherwise
 * (the draft stores trimmed text, blank as null -> ""). */
const meansStored = (key: CoverFormKey, raw: string, stored: string): boolean =>
  MM_KEYS.includes(key) ? parseDepth(raw) === parseDepth(stored) : raw.trim() === stored;

/** Step 3: the Cover Page (left, as printed) and, on the right, the 3D
 * damage map, the damaged parts as the Annex A rows they become, the AI's
 * suggestions to decide and the photos by part -- one selection shared by
 * all of them, and one evidence viewer (contract9.md section 7.3). */
export function ReviewStep({
  draftId,
  data,
  onChange,
  photos,
  readPhotoIds,
  readNotice,
  taxonomy,
  aiSkipped,
  aiRead,
  aiReadPhotos,
  review,
  savedData,
  reveal,
  onRevealed,
  onBack,
  onNext,
}: Props) {
  const user = useCurrentUser();
  const [flashKey, setFlashKey] = useState<string | null>(null);
  // The part picker (step 3A), open: what it was opened on, and the photo
  // an add from the photo viewer links.
  const [addPicker, setAddPicker] = useState<{ title: string; preset: PickerPreset | null; photoId: string | null } | null>(null);
  const [pickerFor, setPickerFor] = useState<string | null>(null);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const damage = data.damage_summary || [];
  const v = data.vehicle_info || {};

  // The cover page as text, re-read from the draft on every change. The
  // draft stores each value trimmed (tread depth as a number), so the text
  // as typed is kept here too and shown while it still means the stored
  // value: re-deriving alone would take a trailing space or a "7." dot
  // away under the surveyor's cursor. Once the draft says something else
  // (the server's copy after a read), the draft wins. Every change is
  // written through to the draft at once (autosave sends the whole draft).
  const derived = useMemo(() => coverFormFrom(data), [data]);
  const [typed, setTyped] = useState<Partial<CoverForm>>({});
  const cover = useMemo(() => {
    const c = { ...derived };
    for (const [k, raw] of Object.entries(typed) as [CoverFormKey, string | undefined][]) {
      if (raw !== undefined && meansStored(k, raw, derived[k])) c[k] = raw;
    }
    return c;
  }, [derived, typed]);
  const patchCover = (patch: Partial<CoverForm>) => {
    setTyped((prev) => ({ ...prev, ...patch }));
    // A field given a value is no longer N/A (field review).
    const filled = (Object.keys(patch) as CoverFormKey[]).filter((k) => patch[k]?.trim()).map((k) => COVER_REVIEW_KEYS[k]);
    onChange((d) => {
      const next = applyCoverPatch(d, patch);
      const checked = dropNaFor(next.checked, filled);
      return checked === next.checked ? next : { ...next, checked };
    });
  };
  // Our Ref is the firm's file number: the API refuses a change from anyone
  // else (403), which would leave autosave stuck on "Not saved -- retrying".
  const ourRefEditable = isAdmin(user) || Boolean(user?.may_sign_off);
  const ai = useMemo(() => aiReadFrom(aiRead, aiReadPhotos), [aiRead, aiReadPhotos]);
  // Field review (contract 7): each cover field's state -- the server's,
  // what was typed since, and the draft's own Confirm / N/A (data.checked,
  // saved with the draft) -- and the progress line from them.
  const savedCover = useMemo(() => (savedData ? coverFormFrom(savedData) : null), [savedData]);
  const coverReview = useMemo(
    () => (savedCover ? coverViews(review, derived, savedCover, { checked: data.checked ?? {} }) : null),
    [review, derived, savedCover, data.checked]
  );
  const shownReview = useMemo(() => (review ? withStates(review, coverReview) : null), [review, coverReview]);
  const reviewFields = (keys: string[], action: ReviewAction) => onChange((d) => ({ ...d, checked: markChecked(d.checked, keys, action, user) }));

  // Client-only row keys, so editing or removing an item never remounts a
  // different row (an index key shifts every row below a removed one). Kept
  // in a WeakMap off the items themselves -- nothing here is ever saved.
  const itemKeys = useRef(new WeakMap<DamageSummaryItem, string>());
  const keyCounter = useRef(0);
  const keyFor = (item: DamageSummaryItem) => {
    let k = itemKeys.current.get(item);
    if (!k) {
      keyCounter.current += 1;
      k = `item-${keyCounter.current}`;
      itemKeys.current.set(item, k);
    }
    return k;
  };
  /** An edit makes a new item object -- it keeps the old one's key. */
  const carryKey = (from: DamageSummaryItem, to: DamageSummaryItem) => {
    const k = itemKeys.current.get(from);
    if (k) itemKeys.current.set(to, k);
    return to;
  };
  const seenKeys = new Set<string>();
  const rowKeys = damage.map((item, i) => {
    const k = keyFor(item);
    // The same object twice in the list would otherwise share a key.
    const unique = seenKeys.has(k) ? `${k}-${i}` : k;
    seenKeys.add(unique);
    return unique;
  });

  const rowEls = useRef(new Map<string, HTMLTableRowElement>());
  const pendingReveal = useRef<Reveal | null>(null);
  const flashTimer = useRef<number | undefined>(undefined);

  /** Scrolls a damage row into view and outlines it for a moment. */
  const revealRow = ({ key, block, focus }: Reveal): boolean => {
    const row = rowEls.current.get(key);
    if (!row) return false;
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    row.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block });
    if (focus) (row.querySelector<HTMLElement>(FOCUS_SELECTOR[focus]) ?? row.querySelector<HTMLElement>("select"))?.focus({ preventScroll: true });
    setFlashKey(key);
    window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlashKey(null), FLASH_MS);
    return true;
  };

  // A row added or changed from Photos by part only exists after the next
  // render -- reveal it then.
  useEffect(() => {
    const pending = pendingReveal.current;
    if (pending && revealRow(pending)) pendingReveal.current = null;
  });
  // The check before filing's [Go to it]: the row, once this step shows it.
  // Found by its place in the table (data-row-key), as drawn.
  const revealed = useRef<number | null>(null);
  const tbodyRef = useRef<HTMLTableSectionElement>(null);
  useEffect(() => {
    if (!reveal || revealed.current === reveal.nonce) return;
    const key = tbodyRef.current?.querySelectorAll<HTMLTableRowElement>(":scope > tr")[reveal.index]?.dataset.rowKey;
    // A row not drawn yet: after the next render. One gone: nothing to show.
    if (key && !revealRow({ key, block: "center", focus: reveal.focus })) return;
    revealed.current = reveal.nonce;
    onRevealed?.();
  });
  // "Removed Rear Bumper · Undo" (P-parts gap 6): the rows taken off in this
  // undo window, the latest last; version restarts the toast's bar.
  const [removedRows, setRemovedRows] = useState<{ entries: Removed<DamageSummaryItem>[]; version: number } | null>(null);
  const undoTimer = useRef<number | undefined>(undefined);
  useEffect(
    () => () => {
      window.clearTimeout(flashTimer.current);
      window.clearTimeout(undoTimer.current);
    },
    []
  );

  const sources = useMemo(
    () => photos.filter((p) => readPhotoIds.includes(p.id) && p.kind && DOCUMENT_KINDS.includes(p.kind)),
    [photos, readPhotoIds]
  );
  const checked = damage.filter((d) => d.human_verified).length;
  const vehicleName = [v.make, v.model].filter(Boolean).join(" ");
  // The firm's condition words; the built-in copy when the API sent none.
  const words = useMemo(() => conditionWordsOf(taxonomy), [taxonomy]);

  const setItem = (index: number, patch: Partial<DamageSummaryItem>) =>
    onChange((d) => ({
      ...d,
      // An edit counts as the surveyor checking the item -- except on a row
      // whose side the AI only inferred: changing its condition, remarks or
      // photo says nothing about the side, and a checked row prints LH/RH in
      // Annex A. Such a row stays unconfirmed until the Verified tick or a
      // change of the part itself (which names the side). The whole draft is
      // saved, so the flag is handled here rather than by the API's per-row edit.
      damage_summary: (d.damage_summary || []).map((it, i) => {
        if (i !== index) return it;
        const partChanged = patch.part !== undefined && patch.part !== it.part;
        const sidePending = Boolean(it.side_inferred) && !partChanged;
        return carryKey(it, {
          ...it,
          ...patch,
          // The name is an Other's alone, as the API keeps it.
          ...(partChanged && patch.part !== OTHER_PART ? { other_part_name: null } : {}),
          human_verified: sidePending ? it.human_verified : true,
          side_inferred: sidePending ? it.side_inferred : false,
        });
      }),
    }));
  /** The Verified tick itself: the one edit that may also untick. Ticking
   * confirms an inferred side; unticking doesn't bring the guess back. */
  const setVerified = (index: number, verified: boolean) =>
    onChange((d) => ({
      ...d,
      damage_summary: (d.damage_summary || []).map((it, i) =>
        i === index ? carryKey(it, { ...it, human_verified: verified, side_inferred: verified ? false : it.side_inferred }) : it
      ),
    }));
  /** A person's Condition. condition_tokens follow it, so the draft never
   * holds words that say something else; a whole value has none. The AI's
   * damage_type is left as it is. Contract 10: it ends "Confirm at
   * strip-down" on a part added from a hidden linked part (the server
   * clears its own copy on the save too). */
  const setCondition = (index: number, condition: string) =>
    setItem(index, {
      condition,
      condition_tokens: conditionWordsIn(condition, words.tokens),
      ...(damage[index]?.needs_strip_down ? { needs_strip_down: false } : {}),
    });
  const removeItem = (index: number) => {
    const item = damage[index];
    if (!item) return;
    // Rows below move up a place: an "item:<index>" selection would point
    // at the next part.
    setSelection(null);
    setHover(null);
    onChange((d) => ({ ...d, damage_summary: removeAt(d.damage_summary || [], index).list }));
    // The row itself is kept for Undo -- the same object, its AI evidence,
    // box and fields untouched -- until the window ends.
    setRemovedRows((r) => ({ entries: [...(r?.entries ?? []), { item, index }], version: (r?.version ?? 0) + 1 }));
    window.clearTimeout(undoTimer.current);
    undoTimer.current = window.setTimeout(() => setRemovedRows(null), PART_UNDO_MS);
  };
  const undoRemove = () => {
    const entries = removedRows?.entries ?? [];
    window.clearTimeout(undoTimer.current);
    setRemovedRows(null);
    if (!entries.length) return;
    // Rows below move down again.
    setSelection(null);
    setHover(null);
    pendingReveal.current = { key: keyFor(entries[0].item), block: "center" };
    onChange((d) => ({ ...d, damage_summary: restoreRemoved(d.damage_summary || [], entries) }));
  };
  const pushItem = (item: DamageSummaryItem, reveal: Omit<Reveal, "key">) => {
    pendingReveal.current = { key: keyFor(item), ...reveal };
    onChange((d) => ({ ...d, damage_summary: [...(d.damage_summary || []), item] }));
  };
  // "Rear Bumper is already row 3" / "the other side?" (P-parts 5.4): asked
  // before a hand add, from the add row (the map's add fills it) and from
  // Photos by part. A suggestion whose part is listed never shows (the
  // panel hides it, lib/findings.ts) and the server never adds it twice.
  const [addAsk, setAddAsk] = useState<{ check: NonNullable<AddCheck>; add: () => void } | null>(null);
  const askBeforeAdd = (part: string, add: () => void, otherName?: string | null) => {
    const check = checkAdd(damage, part, otherName);
    if (check) setAddAsk({ check, add });
    else add();
  };
  /** A part picked in the part picker (the add row, the map's "+ Add part
   * here", the photo viewer's "Add part seen here"): the row a pick from
   * the old add-row select made -- a named Other with its name, an add from
   * a photo with that photo linked, as Photos by part links it. */
  const addPicked = ({ part, other_part_name }: PartPick, photoId: string | null) =>
    askBeforeAdd(
      part,
      () =>
        pushItem(
          {
            part,
            ...(other_part_name ? { other_part_name } : {}),
            severity: null,
            ...(photoId ? { photo_reference: `@${photoId}`, bbox_2d: null } : {}),
            human_verified: true,
          },
          { block: photoId ? "center" : "nearest", focus: part === OTHER_PART && !other_part_name ? "name" : undefined }
        ),
      other_part_name
    );
  /** "Add as damage" from Photos by part. An area-only group can't say which
   * part it shows, so that row opens with its part select focused. */
  const addFromPhotos = (part: string, photoId: string) =>
    askBeforeAdd(part, () =>
      pushItem(
        {
          part,
          damage_type: null,
          severity: null,
          photo_reference: `@${photoId}`,
          bbox_2d: null,
          ai_confidence: null,
          human_verified: true,
        },
        { block: "center", focus: part ? undefined : "part" }
      )
    );
  /** Links a photo to an item, or unlinks it. Any box was drawn on the old photo. */
  const setItemPhoto = (index: number, photoId: string | null) =>
    setItem(index, { photo_reference: photoId ? `@${photoId}` : null, bbox_2d: null });
  const attachFromPhotos = (index: number, photoId: string) => {
    if (rowKeys[index]) pendingReveal.current = { key: rowKeys[index], block: "center" };
    setItemPhoto(index, photoId);
  };
  const setBodyType = (bodyType: string) => onChange((d) => ({ ...d, vehicle_info: { ...(d.vehicle_info || {}), body_type: bodyType || null } }));
  const setDescription = (description: string) => onChange((d) => ({ ...d, description }));

  const pickerIndex = pickerFor ? rowKeys.indexOf(pickerFor) : -1;
  const pickerItem = pickerIndex >= 0 ? damage[pickerIndex] : null;
  const pickerPhoto = pickerItem ? (photoForReference(pickerItem.photo_reference, photos) ?? null) : null;

  // ---- Contract 9: findings, the shared selection, the viewer, decisions ----
  // Rebuilt only when the list or the suggestions change, not on every
  // cover-page keystroke. Dismissed suggestions are in (the panel's Decided
  // list, the map's "Show dismissed").
  const damageList = data.damage_summary;
  const secondLook = data.second_look ?? null;
  const dismissedParts = (data as ReportData & { dismissed_parts?: string[] | null }).dismissed_parts;
  const findings = useMemo(() => {
    const listOnly: ReportData & { dismissed_parts?: string[] | null } = {
      category: [],
      description: "",
      people_involved: [],
      witnesses: [],
      reported_to_authorities: false,
      damage_summary: damageList,
      second_look: secondLook,
      dismissed_parts: dismissedParts,
    };
    return buildFindings(listOnly, { showDismissed: true });
  }, [damageList, secondLook, dismissedParts]);
  const suggestionFindings = useMemo(() => findings.filter((f) => f.kind === "suggestion"), [findings]);

  // "@<photo id>" and "P07" both find a photo; one gone from the draft is null.
  const photoMaps = useMemo(() => {
    const byId = new Map<string, IntakePhoto>();
    const byLabel = new Map<string, IntakePhoto>();
    for (const p of photos) {
      byId.set(p.id, p);
      byLabel.set(p.label, p);
    }
    return { byId, byLabel };
  }, [photos]);
  const photoLookup: PhotoLookup = useCallback(
    (ref: string): PhotoInfo | null => {
      const p = ref.startsWith("@") ? photoMaps.byId.get(ref.slice(1)) : photoMaps.byLabel.get(ref);
      return p ? { ref: `@${p.id}`, label: p.label, src: fileUrl(p.preview_url), thumb: fileUrl(p.thumb_url), area: p.area } : null;
    },
    [photoMaps]
  );
  // The map's photo strip: the vehicle's photos in case order (no documents).
  const mapPhotos = useMemo(() => [...photos].sort((a, b) => a.position - b.position).filter(isEvidencePhoto), [photos]);
  const photoOrder = useMemo(() => mapPhotos.map((p) => `@${p.id}`), [mapPhotos]);
  const photoAreas = useMemo(() => Object.fromEntries(mapPhotos.map((p) => [`@${p.id}`, p.area])), [mapPhotos]);

  const { selection, setSelection, hover, setHover, itemsRemoved } = useDamageSelection();
  const [viewer, setViewer] = useState<ViewerState | null>(null);
  // Suggestions the person has opened in the viewer: deciding one later,
  // from a card or the map, still counts as "looked" (section 11).
  const lookedKeys = useRef(new Set<string>());

  // The list as it is now, for a decision's answer that comes in later.
  const damageNow = useRef(damage);
  damageNow.current = damage;
  const { decide } = useSuggestionDecisions({
    surface: "intake",
    reportId: draftId,
    // The server appended (or took back) the item and owns second_look; the
    // local draft follows at once and is saved with the next autosave. An
    // undone add takes a row off: a selection or hover of a row below it
    // follows its row up (its index would name the next part).
    applyToDraft: (r, asked) => {
      itemsRemoved(rowsTakenOff(damageNow.current, r, asked));
      onChange((d) => applyDecision(d, r, asked));
    },
  });
  // The map sends a suggestion's own key, the cards and viewer "sug:<key>".
  const decideLooked = useCallback(
    (key: string, action: DecideAction, looked: boolean) => decide(key, action, looked || lookedKeys.current.has(suggestionKey(key))),
    [decide]
  );
  const decideFromMap = useCallback((key: string, action: DecideAction) => decideLooked(key, action, false), [decideLooked]);

  // A finding key opens its evidence; the map's "" + photo, that photo alone.
  const openEvidence = useCallback((key: string, photo?: string) => {
    if (!key) {
      if (photo) setViewer({ mode: "photo", startKey: null, startPhoto: photo, photoOrder: [photo] });
      return;
    }
    lookedKeys.current.add(suggestionKey(key));
    setViewer({ mode: key.startsWith("sug:") ? "suggestions" : "listed", startKey: key, startPhoto: photo ?? null });
  }, []);

  // The map has a column of its own (the right one on wide screens, pinned
  // there beside the lists; between the cover page and the lists on narrow
  // ones). [Map] / [Show on 3D] select the finding and bring the map into
  // view only when it isn't already: on a wide screen it is, so the page no
  // longer jumps back up from the suggestions. An empty marker before the
  // map, not a wrapper, so the map's own sticky position still works.
  const mapAnchor = useRef<HTMLDivElement>(null);
  const revealMap = useCallback(() => {
    const anchor = mapAnchor.current;
    if (!anchor) return;
    const r = anchor.parentElement?.querySelector<HTMLElement>("[data-damage-map]")?.getBoundingClientRect();
    if (r && r.top >= 0 && r.bottom <= window.innerHeight) return;
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    anchor.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  }, []);
  const showOnMap = useCallback(
    (key: string) => {
      setViewer(null);
      setSelection({ kind: "finding", key });
      revealMap();
    },
    [setSelection, revealMap]
  );
  // PhotosByPart's [Map] hands over a whole selection (a part's zone or a
  // finding), not just a finding key.
  const showSelectionOnMap = useCallback(
    (s: Selection) => {
      if (s?.kind === "finding") return showOnMap(s.key);
      setViewer(null);
      setSelection(s);
      revealMap();
    },
    [setSelection, showOnMap, revealMap]
  );

  // A list item picked on the map: its row comes into view and flashes.
  const revealLatest = useRef<(index: number) => void>(() => {});
  revealLatest.current = (index: number) => {
    if (rowKeys[index]) revealRow({ key: rowKeys[index], block: "center" });
  };
  const selectFromMap = useCallback(
    (s: Selection) => {
      setSelection(s);
      if (s?.kind === "finding" && s.key.startsWith("item:")) revealLatest.current(Number(s.key.slice(5)));
    },
    [setSelection]
  );

  // [+ Add part here] on any panel of the 3D model: the part picker with
  // that panel's parts, its own part first ("Underbody / Chassis", as the
  // case page adds it: lib/partPicker.ts zonePreferred) and the panel's
  // side, for the person to check.
  // The car map's names join only while the server offers them
  // (lib/partsList.ts); Select still shows a row's own part either way.
  const partsList = useMemo(() => partsToPick(taxonomy), [taxonomy]);
  const addForZone = useCallback(
    (zoneKey: string) => setAddPicker({ title: `Add a part: ${zoneLabel(zoneKey)}`, preset: presetFromZone(zoneKey), photoId: null }),
    []
  );
  // [Add part seen here] in the photo viewer: the picker set from the
  // photo's sorter area and part tag; the part comes with the photo linked.
  const addFromViewer = (ref: string) => {
    const p = ref.startsWith("@") ? photoMaps.byId.get(ref.slice(1)) : photoMaps.byLabel.get(ref);
    if (!p) return;
    setViewer(null);
    setAddPicker({ title: `Add a part seen on ${p.label}`, preset: presetFromPhoto(p), photoId: p.id });
  };
  const setBodyTypeStable = useRef(setBodyType);
  setBodyTypeStable.current = setBodyType;
  const onBodyTypeChange = useCallback((b: string) => setBodyTypeStable.current(b), []);

  const selectedKey = selection?.kind === "finding" ? selection.key : null;
  const hoverKey = hover?.kind === "finding" ? hover.key : null;
  // Phase 1 F: the rows the check before filing would list, and what filing
  // really does with the list (P-parts gap 16): price-book staff get past
  // cases' lines and prices for a make that has them (prefill.sync_annex_a).
  const needYou = filingProblems(damage).length;
  const priceBook = canViewAllCases(user);
  const removedLast = removedRows?.entries[removedRows.entries.length - 1]?.item;

  return (
    // Three areas (globals.css "Step 3: review"): wide screens put the cover
    // page and, under it, the lists on the left, and the map ALONE on the
    // right, pinned the whole way down -- nothing can scroll under it (owner,
    // 23 Nov 2026: the lists used to slide under the pinned map). Narrow
    // screens: cover page, map, lists, in this source order.
    <div className="intake-review-layout">
      <div className="intake-col intake-area-cover">
        {readNotice && (
          <div className="intake-alert danger" role="alert">
            <AlertTriangle style={{ width: 16, height: 16, flexShrink: 0 }} />
            <span style={{ flex: "1 1 260px", minWidth: 0 }}>{readNotice}</span>
          </div>
        )}
        <ReadByNotice readPhotoIds={readPhotoIds} />
        <section className="card intake-panel">
          <div className="intake-panel-head">
            <h2>Cover page</h2>
            <span className="intake-muted">As printed. Anything left blank prints as &quot;Not recorded&quot;.</span>
            {aiSkipped && <span className="intake-chip warn">Filled in by hand</span>}
          </div>
          {shownReview && (
            <div className="review-bar" style={{ marginTop: 10 }}>
              <ReviewProgress review={shownReview} sections={["cover"]} />
              <ConfirmAiButton keys={keysInState(coverReview, "ai")} onConfirm={(keys) => reviewFields(keys, "confirm")} />
            </div>
          )}
          {sources.length > 0 && (
            <div className="intake-sources">
              <span className="intake-muted">Read from</span>
              {sources.map((p) => (
                <a key={p.id} className="intake-source-chip" href={fileUrl(p.preview_url)} target="_blank" rel="noreferrer" title={p.note || p.name}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={fileUrl(p.thumb_url)} alt="" />
                  {p.label} · {p.kind ? PHOTO_KIND_LABELS[p.kind] : ""}
                </a>
              ))}
            </div>
          )}
          <CoverPageFields
            value={cover}
            onChange={patchCover}
            disabled={false}
            ourRefEditable={ourRefEditable}
            aiRead={ai.tags}
            aiValues={ai.values}
            showUsualValues
            review={coverReview}
            onReview={(key, action) => reviewFields([key], action)}
            // As the case page's Cover tab: the AI's own word beside a
            // field (ai_read.notes) and the point of impact checked against
            // the damaged parts. No sketch here: the map has its own column.
            damage={damage}
            aiNotes={ai.notes}
          />
        </section>

        <section className="card intake-panel">
          <div className="intake-panel-head">
            <h2>
              <button type="button" className="rv-collapse" aria-expanded={summaryOpen} onClick={() => setSummaryOpen((o) => !o)}>
                {summaryOpen ? <ChevronDown style={{ width: 16, height: 16 }} /> : <ChevronRight style={{ width: 16, height: 16 }} />}
                Incident summary
              </button>
            </h2>
            <span className="intake-muted">{aiSkipped ? "Shown on the case page, not printed" : "AI draft; shown on the case page, not printed"}</span>
          </div>
          {summaryOpen && (
            <label className="intake-field-wide">
              <textarea rows={5} value={data.description ?? ""} onChange={(e) => setDescription(e.target.value)} placeholder="What happened" />
            </label>
          )}
        </section>
      </div>

      <div className="intake-col intake-area-map">
        <div ref={mapAnchor} className="ev-map-anchor" aria-hidden="true" />
        <DamageMapPanel
          surface="intake"
          findings={findings}
          photos={photoLookup}
          photoOrder={photoOrder}
          photoAreas={photoAreas}
          bodyType={detectBodyType(vehicleName, v.body_type)}
          onBodyTypeChange={onBodyTypeChange}
          selection={selection}
          onSelect={selectFromMap}
          hover={hover}
          onHover={setHover}
          onOpenEvidence={openEvidence}
          onDecide={decideFromMap}
          onAddDamage={addForZone}
        />
      </div>

      <div className="intake-col intake-area-lists">
        <section className="card intake-panel">
          <div className="intake-panel-head">
            <h2>Damaged parts ({damage.length})</h2>
            <span className="intake-muted">
              {priceBook
                ? "Filing makes each part its Annex A line, worded as shown under it. For a make with past cases, their lines and prices come in too: check them on Annex A."
                : "Filing makes each part one Annex A line, worded as shown under it and unpriced. Prices are entered on Annex A."}
            </span>
            {damage.length > 0 && (
              <span className={`intake-chip ${checked === damage.length ? "ok" : ""}`}>
                {checked} of {damage.length} {review ? "confirmed" : "checked"}
              </span>
            )}
            {needYou > 0 && (
              <span className="intake-chip warn" title="Rows with no part, no name or no condition, or a part listed twice">
                {needYou} need{needYou === 1 ? "s" : ""} you
              </span>
            )}
          </div>
          {damage.length === 0 ? (
            <div className="intake-empty">No damaged parts yet. Add them below, or from Photos by part.</div>
          ) : (
            <div className="annex-table-wrapper">
              <table className="annex-table">
                <thead>
                  <tr>
                    <th style={{ width: 44 }}>Item</th>
                    <th>Vehicle Parts Description</th>
                    <th>Condition</th>
                    <th style={{ width: 92 }}>Remarks</th>
                    <th style={{ width: 110 }}>Photo</th>
                    <th>Severity*</th>
                    <th style={{ width: review ? 150 : 70 }}>{review ? "Review" : "Verified"}</th>
                    <th style={{ width: 36 }} aria-label="Remove" />
                  </tr>
                </thead>
                <tbody ref={tbodyRef}>
                  {damage.map((item, i) => {
                    const key = rowKeys[i];
                    const photo = photoForReference(item.photo_reference, photos);
                    const findingKey = `item:${i}`;
                    // findings starts with the list's items, in order. Its
                    // first slide is the item's own photo, with its box.
                    const finding = findings[i]?.key === findingKey ? findings[i] : null;
                    const slides = finding ? slidesOf(finding) : [];
                    const own = photo && slides[0]?.photo === item.photo_reference ? slides[0] : null;
                    const partName = (item.part && damagePartLabel(item)) || "this item";
                    // The AI's own doubt, only until the surveyor checks the row
                    // (the review badge says it instead, when the API sends one).
                    const unsure = !review && item.ai_confidence === "Low" && !item.human_verified;
                    // Needs you: no part, an unnamed Other or no condition
                    // (P-parts gap 3), even on a row a person added.
                    const gaps = rowGaps(item);
                    const rowState = reviewRowState(item);
                    const wording = printsAs(item, taxonomy, v);
                    const picked = selectedKey === findingKey || flashKey === key;
                    const hovered = hoverKey === findingKey;
                    return (
                      <tr
                        key={key}
                        data-row-key={key}
                        ref={(el) => {
                          if (el) rowEls.current.set(key, el);
                          else rowEls.current.delete(key);
                        }}
                        // The map <-> row link: hover only outlines on the map
                        // (the camera never moves on hover); a row picked on
                        // the map, or just added, is outlined here.
                        onMouseEnter={() => setHover({ kind: "finding", key: findingKey })}
                        onMouseLeave={() => setHover(null)}
                        className={picked ? "ev-row-picked" : hovered ? "ev-row-hover" : undefined}
                      >
                        <td style={{ fontFamily: "var(--font-mono)", whiteSpace: "nowrap" }}>
                          {i + 1}
                          {unsure && (
                            <AlertTriangle
                              style={{ width: 12, height: 12, marginLeft: 4, verticalAlign: "-2px", color: "var(--badge-amber-text, #b45309)" }}
                              aria-label="The AI was unsure about this part"
                            >
                              <title>The AI was unsure about this part. Check it against the photo, then tick Verified.</title>
                            </AlertTriangle>
                          )}
                        </td>
                        <td data-col="part">
                          <Select
                            className="annex-cell-input"
                            value={item.part}
                            options={partsList}
                            placeholder="Choose the part…"
                            ariaLabel={`Vehicle parts description, item ${i + 1}`}
                            onChange={(val) => setItem(i, { part: val })}
                          />
                          {/* What the Other is: Annex A prints and prices it
                              by this name. Tidied (spaces, capital) when the
                              field is left. */}
                          {item.part === OTHER_PART && (
                            <input
                              className="annex-cell-input"
                              style={{ marginTop: 4 }}
                              value={item.other_part_name ?? ""}
                              maxLength={MAX_OTHER_NAME}
                              placeholder="Name the part…"
                              aria-label={`Name of the part, item ${i + 1}`}
                              onChange={(e) => setItem(i, { other_part_name: e.target.value })}
                              onBlur={(e) => {
                                const name = cleanPartName(e.target.value);
                                if (name !== (item.other_part_name ?? null)) setItem(i, { other_part_name: name });
                              }}
                            />
                          )}
                          {/* What its Annex A line will say (R-report gap 8). */}
                          {wording.note === "left_out" ? (
                            <span className="rc-prints-as warn">Left out of Annex A until a part is chosen</span>
                          ) : wording.text && wording.text !== item.part ? (
                            <span className="rc-prints-as">
                              Prints as <em>{wording.text}</em>
                              {wording.note === "unsided" ? " (no LH/RH until you confirm the side)" : wording.note === "unnamed" ? " until you name it" : ""}
                            </span>
                          ) : null}
                          {!review && gaps.length > 0 && (
                            <span className="intake-chip warn" style={{ marginTop: 4, fontSize: 10 }}>
                              Needs you: {gapWords(gaps)}
                            </span>
                          )}
                          {!review && item.side_inferred && !item.human_verified && (
                            <span className="intake-chip warn" style={{ marginTop: 4, fontSize: 10 }} title="The AI guessed the side from nearby photos; check it">
                              side inferred
                            </span>
                          )}
                          {/* Contract 10, as the case page's table: added from
                              a hidden linked part; the chip goes once a person
                              gives it a condition. */}
                          {needsStripDownChip(item) && (
                            <span
                              className="intake-chip warn lk-strip-chip"
                              style={{ marginTop: 4, fontSize: 10 }}
                              title="Added from a hidden linked part: check it once the parts come off, then set its condition"
                            >
                              {STRIP_DOWN_CHIP}
                            </span>
                          )}
                        </td>
                        <td data-col="condition">
                          <ConditionPicker
                            className="annex-cell-input"
                            value={item.condition ?? ""}
                            fallback={item.damage_type}
                            words={words}
                            ariaLabel={`Condition of ${partName}`}
                            partName={item.part || undefined}
                            onChange={(val) => setCondition(i, val)}
                          />
                        </td>
                        <td>
                          <Select
                            className="annex-cell-input"
                            value={item.remarks ?? ""}
                            options={DAMAGE_REMARKS}
                            placeholder="—"
                            ariaLabel={`Remarks for ${partName}`}
                            onChange={(val) => setItem(i, { remarks: toDamageRemarks(val) })}
                          />
                        </td>
                        <td>
                          <div className="intake-damage-photo" style={{ width: 88 }}>
                            {photo ? (
                              <>
                                <button
                                  type="button"
                                  className="rv-photo-btn"
                                  onClick={() => openEvidence(findingKey, item.photo_reference ?? undefined)}
                                  title={photo.note ? `${photo.label} — ${photo.note}` : `View ${photo.label}`}
                                  aria-label={`Evidence for ${partName}: ${photo.label}, ${boxChip(own)}`}
                                >
                                  <PhotoBoxes
                                    src={fileUrl(photo.thumb_url)}
                                    alt=""
                                    fit="natural"
                                    className="pb-compact pb-fill"
                                    marks={
                                      finding && own
                                        ? [{ key: findingKey, label: finding.name, status: finding.status, box: own.box, kind: own.kind, frame: own.frame }]
                                        : []
                                    }
                                  />
                                  <span className="intake-tile-label">{photo.label}</span>
                                </button>
                                <div className="rv-photo-actions">
                                  <button type="button" className="intake-link" onClick={() => setPickerFor(key)} aria-label={`Change the photo for ${partName}`}>
                                    Change
                                  </button>
                                  <button type="button" className="intake-link" onClick={() => setItemPhoto(i, null)} aria-label={`Unlink the photo from ${partName}`}>
                                    Unlink
                                  </button>
                                </div>
                              </>
                            ) : (
                              <>
                                <button type="button" className="intake-damage-nophoto rv-attach-btn" onClick={() => setPickerFor(key)}>
                                  <ImagePlus style={{ width: 16, height: 16 }} />
                                  Attach
                                </button>
                                {/* No photo linked, but the AI saw it in some. */}
                                {slides.length > 0 && (
                                  <div className="rv-photo-actions">
                                    <button type="button" className="intake-link" onClick={() => openEvidence(findingKey)} aria-label={`Evidence for ${partName}`}>
                                      Evidence
                                    </button>
                                  </div>
                                )}
                              </>
                            )}
                            <div className="rv-photo-actions">
                              <button
                                type="button"
                                className="intake-link"
                                onClick={() => showOnMap(findingKey)}
                                aria-label={`Show ${partName} on the 3D map`}
                                title="Select it on the 3D damage map"
                              >
                                <BoxIcon style={{ width: 11, height: 11, verticalAlign: "-1px" }} /> Map
                              </button>
                            </div>
                          </div>
                        </td>
                        <td>
                          <Select
                            className="annex-cell-input"
                            value={item.severity ?? ""}
                            options={taxonomy.severities}
                            placeholder=""
                            ariaLabel={`Severity of ${partName} (not printed)`}
                            onChange={(val) => setItem(i, { severity: val || null })}
                          />
                        </td>
                        {review ? (
                          // Field review: the row's state, and [✓] to confirm it
                          // (the old Verified tick) or undo a confirm.
                          <td>
                            <div style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap" }}>
                              <ReviewBadge state={rowState} label={partName} detail={gaps.length ? gapWords(gaps) : damageCheckDetail(item)} />
                              {rowState === "needs" ? null : rowState === "you" ? (
                                <ReviewUndoButton label={partName} onClick={() => setVerified(i, false)} />
                              ) : (
                                <ReviewConfirmButton
                                  label={partName}
                                  title="Confirm: you checked this part, its side and condition against the photo"
                                  onClick={() => setVerified(i, true)}
                                />
                              )}
                            </div>
                          </td>
                        ) : (
                          <td style={{ textAlign: "center" }}>
                            <input
                              type="checkbox"
                              checked={Boolean(item.human_verified)}
                              onChange={(e) => setVerified(i, e.target.checked)}
                              aria-label={`${partName} checked`}
                              title={item.human_verified ? "Checked by you" : "Tick once you have checked this row against the photo"}
                            />
                          </td>
                        )}
                        <td>
                          <button type="button" className="annex-row-remove" onClick={() => removeItem(i)} aria-label={`Remove ${partName}`} title="Remove">
                            <Trash2 style={{ width: 14, height: 14 }} />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {/* Step 3A: the part picker opens where the add row's part
              select was (the same two controls, the same place). */}
          <div className="intake-add-row">
            <button type="button" className="pp-open" aria-haspopup="dialog" onClick={() => setAddPicker({ title: "Add a damaged part", preset: null, photoId: null })}>
              <Search style={{ width: 14, height: 14, flexShrink: 0 }} aria-hidden="true" />
              <span>Add a damaged part…</span>
            </button>
            <button
              type="button"
              className="btn-secondary-modern"
              aria-haspopup="dialog"
              onClick={() => setAddPicker({ title: "Add a damaged part", preset: null, photoId: null })}
            >
              <Plus style={{ width: 14, height: 14 }} /> Add
            </button>
          </div>
          <p className="intake-muted" style={{ margin: 0 }}>
            Condition and Remarks print on each part&apos;s Annex A line; Qty and prices are filled in Annex A after filing.
            <br />* Severity is kept for the case list and analytics; it is not printed.
          </p>
        </section>

        <SuggestionPanel
          surface="intake"
          findings={suggestionFindings}
          decisions={secondLook?.decisions ?? []}
          check={secondLook?.check ?? null}
          linkRun={secondLook?.link_run ?? null}
          photos={photoLookup}
          reads={secondLook?.reads}
          onDecide={decideLooked}
          onLook={openEvidence}
          onShowOnMap={showOnMap}
        />
        <PhotosByPart
          photos={photos}
          damage={damage}
          readPhotoIds={readPhotoIds}
          onAddDamage={addFromPhotos}
          onAttachPhoto={attachFromPhotos}
          onOpenPhoto={(group, id) => setViewer({ mode: "photo", startKey: null, startPhoto: `@${id}`, photoOrder: group.photos.map((p) => `@${p.id}`) })}
          findings={findings}
          selection={selection}
          onShowOnMap={showSelectionOnMap}
        />

      </div>

      <div className="intake-footer intake-review-footer">
        <button type="button" className="btn-secondary-modern" onClick={onBack}>
          <ArrowLeft style={{ width: 14, height: 14 }} /> Back
        </button>
        <button type="button" className="btn-primary-modern" onClick={onNext}>
          Next: parties <ArrowRight style={{ width: 14, height: 14 }} />
        </button>
      </div>

      {pickerItem && (
        <PhotoPicker
          photos={photos}
          part={pickerItem.part}
          current={pickerPhoto}
          readPhotoIds={readPhotoIds}
          onPick={(id) => {
            // Picking the photo it already has keeps its damage box.
            if (pickerPhoto?.id !== id) setItemPhoto(pickerIndex, id);
            setPickerFor(null);
          }}
          onClose={() => setPickerFor(null)}
        />
      )}

      {addPicker && (
        <PartPicker
          title={addPicker.title}
          taxonomy={taxonomy}
          vehicle={v}
          preset={addPicker.preset}
          onPick={(pick) => {
            const { photoId } = addPicker;
            setAddPicker(null);
            addPicked(pick, photoId);
          }}
          onClose={() => setAddPicker(null)}
        />
      )}

      {addAsk && (
        <AddCheckDialog
          check={addAsk.check}
          onAdd={() => {
            const { add } = addAsk;
            setAddAsk(null);
            add();
          }}
          onGoTo={(index) => {
            setAddAsk(null);
            // Once the dialog has gone (it hands focus back as it closes).
            if (rowKeys[index]) pendingReveal.current = { key: rowKeys[index], block: "center", focus: "condition" };
          }}
          onCancel={() => setAddAsk(null)}
        />
      )}

      {removedRows && (
        <UndoToast
          message={
            removedRows.entries.length === 1 && removedLast
              ? `Removed ${(removedLast.part && damagePartLabel(removedLast)) || "a row"}`
              : `Removed ${removedRows.entries.length} parts`
          }
          durationMs={PART_UNDO_MS}
          timerKey={removedRows.version}
          onUndo={undoRemove}
          onDismiss={() => {
            window.clearTimeout(undoTimer.current);
            setRemovedRows(null);
          }}
        />
      )}

      <EvidenceViewer
        open={Boolean(viewer)}
        mode={viewer?.mode ?? "listed"}
        findings={findings}
        startKey={viewer?.startKey}
        startPhoto={viewer?.startPhoto}
        photoOrder={viewer?.photoOrder}
        photos={photoLookup}
        reads={secondLook?.reads}
        onDecide={decideLooked}
        onShowOnMap={showOnMap}
        onAddFromPhoto={addFromViewer}
        onClose={() => setViewer(null)}
      />
    </div>
  );
}
