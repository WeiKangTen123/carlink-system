"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ArrowLeft, ArrowRight, ChevronDown, ChevronRight, ImagePlus, Plus, Trash2 } from "lucide-react";
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
  type ReportData,
  type Taxonomy,
} from "@/lib/api";
import { ConditionPicker, conditionWordsIn } from "@/components/ConditionPicker";
import { ReviewBadge } from "@/components/ReviewBadge";
import { ReviewConfirmButton, ReviewUndoButton } from "@/components/ReviewConfirmButton";
import type { Review } from "@/lib/review";
import { damageCheckDetail, damageState } from "@/lib/reviewRows";
import { CoverPageFields } from "@/components/CoverPageFields";
import { isAdmin, useCurrentUser } from "@/components/CurrentUser";
import { aiReadFrom, coverFormFrom, parseDepth, type AiRead, type CoverForm, type CoverFormKey } from "@/lib/coverForm";
import { DOCUMENT_KINDS, PHOTO_KIND_LABELS, photoForReference, type IntakePhoto } from "@/lib/intake";
import { applyCoverPatch, bboxStyle } from "@/lib/intakeReview";
import { COVER_REVIEW_KEYS, coverViews, dropNaFor, keysInState, markChecked, withStates, type ReviewAction } from "@/lib/fieldReview";
import { ConfirmAiButton } from "@/components/FieldReviewControls";
import { ReviewProgress } from "@/components/ReviewProgress";
import { DamagePhotoViewer, type ViewerSlide } from "./DamagePhotoViewer";
import { PhotoPicker } from "./PhotoPicker";
import { PhotosByPart } from "./PhotosByPart";
import { ReadByNotice } from "./ReadByNotice";
import { SecondLookPanel } from "./SecondLookPanel";

// Three.js/WebGL only exists client-side (same as the case page).
const VehicleBlueprint3D = dynamic(() => import("@/components/VehicleBlueprint3D").then((m) => m.VehicleBlueprint3D), {
  ssr: false,
  loading: () => <div className="blueprint-stage-3d blueprint-3d-loading">Loading the 3D model…</div>,
});

type Props = {
  data: ReportData;
  onChange: (update: (d: ReportData) => ReportData) => void;
  photos: IntakePhoto[];
  readPhotoIds: string[];
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
  onBack: () => void;
  onNext: () => void;
};

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

/** What the photo viewer is walking: the damage items that have a photo,
 * or one Photos by part group. */
type ViewerState = { kind: "damage"; key: string } | { kind: "photos"; title: string; ids: string[]; id: string };

/** Where to bring a damage row into view once it has rendered. */
type Reveal = { key: string; block: ScrollLogicalPosition; focusPart: boolean };

const FLASH_MS = 2000;

/** The tyre fields that are numbers on the draft (TyreItem.tread_depth_mm). */
const MM_KEYS: readonly CoverFormKey[] = ["front_nearside_mm", "front_offside_mm", "rear_nearside_mm", "rear_offside_mm"];

/** Whether the text in a cover field still means what the draft holds for
 * it: the same number for a tread depth, the same trimmed text otherwise
 * (the draft stores trimmed text, blank as null -> ""). */
const meansStored = (key: CoverFormKey, raw: string, stored: string): boolean =>
  MM_KEYS.includes(key) ? parseDepth(raw) === parseDepth(stored) : raw.trim() === stored;

/** Step 3: the Cover Page (left, as printed) and the damaged parts as the
 * Annex A rows they become (right), with the photo evidence beside them. */
export function ReviewStep({ data, onChange, photos, readPhotoIds, taxonomy, aiSkipped, aiRead, aiReadPhotos, review, savedData, onBack, onNext }: Props) {
  const user = useCurrentUser();
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const [flashKey, setFlashKey] = useState<string | null>(null);
  const [newPart, setNewPart] = useState("");
  const [pickerFor, setPickerFor] = useState<string | null>(null);
  const [viewer, setViewer] = useState<ViewerState | null>(null);
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
  const revealRow = ({ key, block, focusPart }: Reveal): boolean => {
    const row = rowEls.current.get(key);
    if (!row) return false;
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    row.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block });
    if (focusPart) row.querySelector<HTMLSelectElement>("select")?.focus({ preventScroll: true });
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
  useEffect(() => () => window.clearTimeout(flashTimer.current), []);

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
   * damage_type is left as it is. */
  const setCondition = (index: number, condition: string) =>
    setItem(index, { condition, condition_tokens: conditionWordsIn(condition, words.tokens) });
  const removeItem = (index: number) => {
    setHighlighted(null);
    onChange((d) => ({ ...d, damage_summary: (d.damage_summary || []).filter((_, i) => i !== index) }));
  };
  const pushItem = (item: DamageSummaryItem, reveal: Omit<Reveal, "key">) => {
    pendingReveal.current = { key: keyFor(item), ...reveal };
    onChange((d) => ({ ...d, damage_summary: [...(d.damage_summary || []), item] }));
  };
  const addItem = () => {
    if (!newPart) return;
    pushItem({ part: newPart, severity: null, human_verified: true }, { block: "nearest", focusPart: false });
    setNewPart("");
  };
  /** "Add as damage" from Photos by part. An area-only group can't say which
   * part it shows, so that row opens with its part select focused. */
  const addFromPhotos = (part: string, photoId: string) =>
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
      { block: "center", focusPart: !part }
    );
  /** Links a photo to an item, or unlinks it. Any box was drawn on the old photo. */
  const setItemPhoto = (index: number, photoId: string | null) =>
    setItem(index, { photo_reference: photoId ? `@${photoId}` : null, bbox_2d: null });
  const attachFromPhotos = (index: number, photoId: string) => {
    if (rowKeys[index]) pendingReveal.current = { key: rowKeys[index], block: "center", focusPart: false };
    setItemPhoto(index, photoId);
  };
  const setBodyType = (bodyType: string) => onChange((d) => ({ ...d, vehicle_info: { ...(d.vehicle_info || {}), body_type: bodyType || null } }));
  const setDescription = (description: string) => onChange((d) => ({ ...d, description }));

  const pickerIndex = pickerFor ? rowKeys.indexOf(pickerFor) : -1;
  const pickerItem = pickerIndex >= 0 ? damage[pickerIndex] : null;
  const pickerPhoto = pickerItem ? (photoForReference(pickerItem.photo_reference, photos) ?? null) : null;

  let viewerSlides: ViewerSlide[] = [];
  let viewerIndex = -1;
  if (viewer?.kind === "damage") {
    viewerSlides = damage.flatMap((item, i) => {
      const photo = photoForReference(item.photo_reference, photos);
      if (!photo) return [];
      // The viewer's "Condition" is the table's: the firm's wording, else the AI's type.
      return [
        { key: rowKeys[i], photo, damage: { part: damagePartLabel(item), damageType: item.condition || item.damage_type, severity: item.severity, bbox: item.bbox_2d } },
      ];
    });
    viewerIndex = viewerSlides.findIndex((s) => s.key === viewer.key);
  } else if (viewer?.kind === "photos") {
    viewerSlides = viewer.ids.flatMap((id) => {
      const photo = photos.find((p) => p.id === id);
      return photo ? [{ key: id, photo, heading: viewer.title }] : [];
    });
    viewerIndex = viewerSlides.findIndex((s) => s.key === viewer.id);
  }

  return (
    <div className="intake-review-layout">
      <div className="intake-col">
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

      <div className="intake-col">
        <section className="card intake-panel">
          <div className="intake-panel-head">
            <h2>Damaged parts ({damage.length})</h2>
            <span className="intake-muted">These become the Annex A rows, unpriced.</span>
            {damage.length > 0 && (
              <span className={`intake-chip ${checked === damage.length ? "ok" : ""}`}>
                {checked} of {damage.length} {review ? "confirmed" : "checked"}
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
                <tbody>
                  {damage.map((item, i) => {
                    const key = rowKeys[i];
                    const photo = photoForReference(item.photo_reference, photos);
                    const box = bboxStyle(item.bbox_2d);
                    const partName = (item.part && damagePartLabel(item)) || "this item";
                    // The AI's own doubt, only until the surveyor checks the row
                    // (the review badge says it instead, when the API sends one).
                    const unsure = !review && item.ai_confidence === "Low" && !item.human_verified;
                    const rowState = damageState(item);
                    const lit = highlighted === i || flashKey === key;
                    return (
                      <tr
                        key={key}
                        ref={(el) => {
                          if (el) rowEls.current.set(key, el);
                          else rowEls.current.delete(key);
                        }}
                        onMouseEnter={() => setHighlighted(i)}
                        // The 3D hotspot <-> row coupling: the hovered or
                        // clicked row lights up (globals.css has no rule for
                        // a table row here, hence inline).
                        style={lit ? { background: "var(--surface-hover)", outline: "2px solid var(--accent-primary)", outlineOffset: -2 } : undefined}
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
                        <td>
                          <Select
                            className="annex-cell-input"
                            value={item.part}
                            options={taxonomy.parts}
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
                          {!review && item.side_inferred && !item.human_verified && (
                            <span className="intake-chip warn" style={{ marginTop: 4, fontSize: 10 }} title="The AI guessed the side from nearby photos; check it">
                              side inferred
                            </span>
                          )}
                        </td>
                        <td>
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
                                  onClick={() => setViewer({ kind: "damage", key })}
                                  title={photo.note ? `${photo.label} — ${photo.note}` : `View ${photo.label}`}
                                  aria-label={`View ${photo.label} for ${partName}`}
                                >
                                  {/* eslint-disable-next-line @next/next/no-img-element */}
                                  <img src={fileUrl(photo.thumb_url)} alt="" />
                                  {box && <span className="intake-bbox" style={box} />}
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
                              <button type="button" className="intake-damage-nophoto rv-attach-btn" onClick={() => setPickerFor(key)}>
                                <ImagePlus style={{ width: 16, height: 16 }} />
                                Attach
                              </button>
                            )}
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
                              <ReviewBadge state={rowState} label={partName} detail={damageCheckDetail(item)} />
                              {rowState === "you" ? (
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
          <div className="intake-add-row">
            <Select value={newPart} options={taxonomy.parts} placeholder="Add a damaged part…" ariaLabel="Add a damaged part" onChange={setNewPart} />
            <button type="button" className="btn-secondary-modern" onClick={addItem} disabled={!newPart}>
              <Plus style={{ width: 14, height: 14 }} /> Add
            </button>
          </div>
          <p className="intake-muted" style={{ margin: 0 }}>
            Condition and Remarks print on each part&apos;s Annex A line; Qty and prices are filled in Annex A after filing.
            <br />* Severity is kept for the case list and analytics; it is not printed.
          </p>
        </section>

        <SecondLookPanel data={data} onChange={onChange} photos={photos} />
        <PhotosByPart
          photos={photos}
          damage={damage}
          readPhotoIds={readPhotoIds}
          onAddDamage={addFromPhotos}
          onAttachPhoto={attachFromPhotos}
          onOpenPhoto={(group, id) => setViewer({ kind: "photos", title: group.title, ids: group.photos.map((p) => p.id), id })}
        />

        <section className="card intake-panel">
          <div className="intake-panel-head">
            <h2>Vehicle model</h2>
            <span className="intake-muted">{vehicleName || "Vehicle"}</span>
            {/* The body type only picks the 3D model; it is not printed. */}
            <label className="intake-muted" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              Body type
              <Select value={v.body_type ?? ""} options={taxonomy.body_types} placeholder="Auto from make/model" onChange={setBodyType} />
            </label>
          </div>
          <VehicleBlueprint3D
            damageEntries={damage}
            onHotspotClick={(idx) => {
              setHighlighted(idx);
              if (rowKeys[idx]) revealRow({ key: rowKeys[idx], block: "center", focusPart: false });
            }}
            highlightedDamageIndex={highlighted}
            vehicleName={vehicleName}
            bodyType={v.body_type}
          />
        </section>
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

      {viewer && viewerIndex >= 0 && (
        <DamagePhotoViewer
          slides={viewerSlides}
          index={viewerIndex}
          title={viewer.kind === "damage" ? "Damaged parts" : `Photos by part · ${viewer.title}`}
          onNavigate={(i) => {
            const next = viewerSlides[i];
            if (!next) return;
            setViewer(viewer.kind === "damage" ? { kind: "damage", key: next.key } : { ...viewer, id: next.key });
          }}
          onClose={() => setViewer(null)}
        />
      )}
    </div>
  );
}
