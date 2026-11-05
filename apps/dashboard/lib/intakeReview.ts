/** Helpers for the New Intake review step (components/intake/ReviewStep.tsx
 * and its Photos by part panel, photo picker and damage photo viewer). */
import type { CSSProperties } from "react";
import type { CoverPageInfo, DamageSummaryItem, InsuranceDetails, ReportData, TyreCondition, TyreItem, VehicleInfo } from "./api";
import { parseDepth, type CoverForm, type CoverFormKey } from "./coverForm";
import { DOCUMENT_KINDS, photoForReference, type IntakePhoto } from "./intake";

/** bbox_2d is Gemini's [y_min, x_min, y_max, x_max] on a 0-1000 scale, so as
 * percentages it lines up with any box that is exactly the image's size. */
export function bboxStyle(bbox: number[] | null | undefined): CSSProperties | null {
  if (!bbox || bbox.length !== 4 || bbox.some((v) => typeof v !== "number" || Number.isNaN(v))) return null;
  const [yMin, xMin, yMax, xMax] = bbox;
  return { top: `${yMin / 10}%`, left: `${xMin / 10}%`, width: `${(xMax - xMin) / 10}%`, height: `${(yMax - yMin) / 10}%` };
}

/** True when a key press belongs to a form field, not to a viewer's shortcuts. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return Boolean(target.closest("input, select, textarea, [contenteditable='true']"));
}

/** A photo of the vehicle that could back a damage item -- not a document
 * (log card, police report...) and not one the surveyor excluded. */
export function isEvidencePhoto(p: IntakePhoto): boolean {
  return !p.excluded && !(p.kind && DOCUMENT_KINDS.includes(p.kind));
}

const norm = (s: string | null | undefined) => (s || "").trim().toLowerCase();

export function samePart(a: string | null | undefined, b: string | null | undefined): boolean {
  return Boolean(norm(a)) && norm(a) === norm(b);
}

const usable = (p: IntakePhoto) => p.quality !== "blurry" && p.quality !== "unusable";

/** The photo to link when a group is added or attached: the first that shows
 * damage and isn't blurry, else the first in the group. */
export function bestPhoto(photos: IntakePhoto[]): IntakePhoto | undefined {
  return photos.find((p) => p.shows_damage && usable(p)) ?? photos[0];
}

export type PhotoGroup = {
  id: string;
  /** The sorter's canonical part name; null for a group by area only. */
  part: string | null;
  area: string | null;
  title: string;
  /** Damage photos first, each in case order. */
  photos: IntakePhoto[];
  damageCount: number;
  /** A part group is listed when the damage list has that part; an area
   * group (no part to compare) when one of its photos is linked to an item. */
  listed: boolean;
  /** For a listed part group: the first damage item for that part with no
   * photo yet, which "Attach photo" fills in. */
  itemWithoutPhoto: number | null;
  best: IntakePhoto;
};

/** Groups the case's photos by the part the sorter named, falling back to
 * the area of the car. Unlisted groups with damage photos come first (the
 * ones the surveyor should check), then other unlisted groups, then listed. */
export function groupPhotosByPart(photos: IntakePhoto[], damage: DamageSummaryItem[]): PhotoGroup[] {
  const buckets = new Map<string, { part: string | null; area: string | null; photos: IntakePhoto[] }>();
  for (const p of [...photos].sort((a, b) => a.position - b.position)) {
    if (!isEvidencePhoto(p)) continue;
    const part = p.part?.trim() || null;
    const area = p.area?.trim() || null;
    if (!part && !area) continue;
    const id = part ? `part:${norm(part)}` : `area:${norm(area)}`;
    const bucket = buckets.get(id) ?? { part, area: part ? null : area, photos: [] };
    bucket.photos.push(p);
    buckets.set(id, bucket);
  }

  const linked = new Set(
    damage.map((d) => (d.photo_reference?.startsWith("@") ? d.photo_reference.slice(1) : "")).filter(Boolean)
  );

  const groups: PhotoGroup[] = [];
  for (const [id, b] of buckets) {
    const ordered = [...b.photos.filter((p) => p.shows_damage), ...b.photos.filter((p) => !p.shows_damage)];
    const best = bestPhoto(ordered);
    if (!best) continue;
    let listed: boolean;
    let itemWithoutPhoto: number | null = null;
    if (b.part) {
      listed = damage.some((d) => samePart(d.part, b.part));
      const idx = damage.findIndex((d) => samePart(d.part, b.part) && !photoForReference(d.photo_reference, photos));
      itemWithoutPhoto = idx >= 0 ? idx : null;
    } else {
      listed = b.photos.some((p) => linked.has(p.id));
    }
    groups.push({
      id,
      part: b.part,
      area: b.area,
      title: b.part ?? `Area: ${b.area}`,
      photos: ordered,
      damageCount: b.photos.filter((p) => p.shows_damage).length,
      listed,
      itemWithoutPhoto,
      best,
    });
  }

  const rank = (g: PhotoGroup) => (g.listed ? 2 : g.damageCount > 0 ? 0 : 1);
  const firstPosition = (g: PhotoGroup) => Math.min(...g.photos.map((p) => p.position));
  return groups.sort(
    (a, b) => rank(a) - rank(b) || Number(!a.part) - Number(!b.part) || firstPosition(a) - firstPosition(b)
  );
}

export type PickerPhoto = { photo: IntakePhoto; samePart: boolean };

/** The photos a damage item can be linked to, in the picker's order: the
 * same part first, then the same area of the car, then the rest, with
 * excluded photos last. Documents are left out. */
export function photosForPicker(photos: IntakePhoto[], part: string, current: IntakePhoto | null): PickerPhoto[] {
  const candidates = photos.filter((p) => !(p.kind && DOCUMENT_KINDS.includes(p.kind)));
  // The area to favour: the current photo's, else the one most of this
  // part's photos were taken from.
  let area = current?.area || null;
  if (!area) {
    const counts = new Map<string, number>();
    for (const p of candidates) if (p.area && samePart(p.part, part)) counts.set(p.area, (counts.get(p.area) || 0) + 1);
    area = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
  }
  const rank = (p: IntakePhoto) => (p.excluded ? 3 : samePart(p.part, part) ? 0 : area && p.area === area ? 1 : 2);
  return candidates
    .sort((a, b) => rank(a) - rank(b) || a.position - b.position)
    .map((photo) => ({ photo, samePart: !photo.excluded && samePart(photo.part, part) }));
}

// ---------------------------------------------------------------------------
// Cover page <-> draft

const COVER_PAGE_KEYS = [
  "date_of_request", "requested_by", "requested_by_address", "workshop_address", "date_of_survey", "date_of_dismantle",
  "date_before_painting", "date_after_painting", "handbrake_condition", "modification", "general_condition", "point_of_impact_note",
] as const satisfies readonly (keyof CoverPageInfo & CoverFormKey)[];
const VEHICLE_KEYS = [
  "plate_number", "make", "model", "date_of_registration", "engine_number", "vin", "odometer_reading", "transmission", "color",
  "steering_condition", "brakes_condition", "paintwork_condition", "point_of_impact",
] as const satisfies readonly (keyof VehicleInfo & CoverFormKey)[];
const INSURANCE_KEYS = ["claim_type", "workshop_assigned"] as const satisfies readonly (keyof InsuranceDetails & CoverFormKey)[];

const has = (keys: readonly string[], key: string) => keys.includes(key);

/** Writes a Cover Page edit (CoverForm keys, as typed) into the intake
 * draft at the keys the report prints them from: report_id and
 * incident_datetime at the top, cover_page.*, vehicle_info.* (tyres as the
 * four TyreItems), insurance_details.claim_type / workshop_assigned. The
 * reverse of coverFormFrom. Values are trimmed, and blank or whitespace-only
 * clears the value (null, which prints "Not recorded"), the same as the
 * Cover tab's coverRequestFrom -- a stored " " would count as filled and
 * print an empty cell. (ReviewStep keeps the raw text on screen while it is
 * being typed, so the trim never eats a space under the cursor.) Tread
 * depth is a number on the report (TyreItem.tread_depth_mm), so it is
 * parsed; the size is one per axle and goes on both its tyres, as the firm
 * prints it. */
export function applyCoverPatch(data: ReportData, patch: Partial<CoverForm>): ReportData {
  const next: ReportData = { ...data };
  let cover: CoverPageInfo | null = null;
  let vehicle: VehicleInfo | null = null;
  let insurance: InsuranceDetails | null = null;
  let tyres: TyreCondition | null = null;
  const tyre = (slot: keyof TyreCondition, fields: Partial<TyreItem>) => {
    const all: TyreCondition = tyres || { ...(data.vehicle_info?.tyres || {}) };
    all[slot] = { ...(all[slot] || {}), ...fields };
    tyres = all;
  };
  for (const [key, raw] of Object.entries(patch) as [CoverFormKey, string | undefined][]) {
    if (raw === undefined) continue;
    const value = raw.trim() || null;
    if (key === "report_id") next.report_id = value;
    else if (key === "incident_datetime") next.incident_datetime = value;
    else if (has(COVER_PAGE_KEYS, key)) cover = { ...(cover || data.cover_page || {}), [key]: value };
    else if (has(VEHICLE_KEYS, key)) vehicle = { ...(vehicle || data.vehicle_info || {}), [key]: value };
    else if (has(INSURANCE_KEYS, key)) insurance = { ...(insurance || data.insurance_details || {}), [key]: value };
    else {
      const side = /^(front|rear)_(nearside|offside)_(mm|brand)$/.exec(key);
      const size = /^(front|rear)_size$/.exec(key);
      if (side) {
        const slot = `${side[1]}_${side[2]}` as keyof TyreCondition;
        tyre(slot, side[3] === "mm" ? { tread_depth_mm: parseDepth(raw) } : { brand: value });
      } else if (size) {
        tyre(`${size[1]}_nearside` as keyof TyreCondition, { size: value });
        tyre(`${size[1]}_offside` as keyof TyreCondition, { size: value });
      }
    }
  }
  if (cover) next.cover_page = cover;
  if (insurance) next.insurance_details = insurance;
  if (vehicle || tyres) next.vehicle_info = { ...(vehicle || data.vehicle_info || {}), ...(tyres ? { tyres } : {}) };
  return next;
}
