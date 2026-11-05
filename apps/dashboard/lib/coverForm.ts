/** The Cover Page as a form: every field as text, as typed. Shared by the
 * case page's Cover tab (components/CoverTab.tsx) and the intake wizard's
 * review step, so both read and write the same keys of the report the same
 * way. The mapping was lifted from CaseAssessmentTab.tsx on 2026-11-04 and
 * is unchanged apart from the tyres, which are flat keys here so the wizard
 * can patch one field at a time. */
import type { CoverPageRequest, ReportData, TyreCondition, TyreItem } from "./api";

/** Every Cover Page field as text, as typed -- the firm's dates are free
 * text ("27th February 2023 (10.15am)") and so are the readings. "" when
 * absent: the form never shows a guessed value. */
export type CoverForm = {
  report_id: string;
  date_of_request: string;
  requested_by: string;
  requested_by_address: string;
  claim_type: string;
  workshop_assigned: string;
  workshop_address: string;
  incident_datetime: string;
  date_of_survey: string;
  date_of_dismantle: string;
  date_before_painting: string;
  date_after_painting: string;
  plate_number: string;
  make: string;
  model: string;
  date_of_registration: string;
  engine_number: string;
  vin: string;
  odometer_reading: string;
  transmission: string;
  color: string;
  steering_condition: string;
  brakes_condition: string;
  handbrake_condition: string;
  paintwork_condition: string;
  modification: string;
  general_condition: string;
  front_nearside_mm: string;
  front_nearside_brand: string;
  front_offside_mm: string;
  front_offside_brand: string;
  front_size: string;
  rear_nearside_mm: string;
  rear_nearside_brand: string;
  rear_offside_mm: string;
  rear_offside_brand: string;
  rear_size: string;
  point_of_impact: string;
  point_of_impact_note: string;
};

export type CoverFormKey = keyof CoverForm;

/** The parts of a report the form reads. ReportData satisfies it, and so
 * does PUT /cover-page's answer (CoverPageResponse). */
export type CoverSource = Pick<ReportData, "report_id" | "incident_datetime" | "cover_page" | "vehicle_info" | "insurance_details">;

/** A recorded value as text; nothing for null or undefined. */
export const text = (v: string | number | null | undefined): string => (v == null ? "" : String(v));

/** The static-check wording the firm writes on nearly every report. Offered
 * as a button, never filled in on its own: the surveyor chooses it, and
 * nothing is saved until the host saves. */
export const USUAL_STATIC_CHECK: Partial<CoverForm> = {
  steering_condition: "see annex(es)",
  brakes_condition: "see annex(es)",
  handbrake_condition: "see annex(es)",
  paintwork_condition: "Good",
  modification: "Nil",
  general_condition: "Good",
};

export function coverFormFrom(d: CoverSource): CoverForm {
  const c = d.cover_page || {};
  const v = d.vehicle_info || {};
  const ins = d.insurance_details || {};
  const t = v.tyres || {};
  return {
    report_id: text(d.report_id),
    date_of_request: text(c.date_of_request),
    requested_by: text(c.requested_by),
    requested_by_address: text(c.requested_by_address),
    claim_type: text(ins.claim_type),
    workshop_assigned: text(ins.workshop_assigned),
    workshop_address: text(c.workshop_address),
    incident_datetime: text(d.incident_datetime),
    date_of_survey: text(c.date_of_survey),
    date_of_dismantle: text(c.date_of_dismantle),
    date_before_painting: text(c.date_before_painting),
    date_after_painting: text(c.date_after_painting),
    plate_number: text(v.plate_number),
    make: text(v.make),
    model: text(v.model),
    date_of_registration: text(v.date_of_registration),
    engine_number: text(v.engine_number),
    vin: text(v.vin),
    odometer_reading: text(v.odometer_reading),
    transmission: text(v.transmission),
    color: text(v.color),
    steering_condition: text(v.steering_condition),
    brakes_condition: text(v.brakes_condition),
    handbrake_condition: text(c.handbrake_condition),
    paintwork_condition: text(v.paintwork_condition),
    modification: text(c.modification),
    general_condition: text(c.general_condition),
    front_nearside_mm: text(t.front_nearside?.tread_depth_mm),
    front_nearside_brand: text(t.front_nearside?.brand),
    front_offside_mm: text(t.front_offside?.tread_depth_mm),
    front_offside_brand: text(t.front_offside?.brand),
    // One size per axle, as the firm prints it: the nearside's, else the offside's.
    front_size: text(t.front_nearside?.size || t.front_offside?.size),
    rear_nearside_mm: text(t.rear_nearside?.tread_depth_mm),
    rear_nearside_brand: text(t.rear_nearside?.brand),
    rear_offside_mm: text(t.rear_offside?.tread_depth_mm),
    rear_offside_brand: text(t.rear_offside?.brand),
    rear_size: text(t.rear_nearside?.size || t.rear_offside?.size),
    point_of_impact: text(v.point_of_impact),
    point_of_impact_note: text(c.point_of_impact_note),
  };
}

/** "7", "7mm", "7.5 mm" -> 7; blank or anything else -> null. */
export function parseDepth(value: string): number | null {
  const t = value.replace(/mm/i, "").trim();
  if (!t) return null;
  const n = parseFloat(t);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

const COVER_PAGE_KEYS = [
  "date_of_request", "requested_by", "requested_by_address", "workshop_address", "date_of_survey", "date_of_dismantle",
  "date_before_painting", "date_after_painting", "handbrake_condition", "modification", "general_condition", "point_of_impact_note",
] as const;
const VEHICLE_KEYS = [
  "plate_number", "make", "model", "date_of_registration", "engine_number", "vin", "odometer_reading", "transmission", "color",
  "steering_condition", "brakes_condition", "paintwork_condition", "point_of_impact",
] as const;
const TYRE_KEYS = [
  "front_nearside_mm", "front_nearside_brand", "front_offside_mm", "front_offside_brand", "front_size",
  "rear_nearside_mm", "rear_nearside_brand", "rear_offside_mm", "rear_offside_brand", "rear_size",
] as const;
const INSURANCE_KEYS = ["claim_type", "workshop_assigned"] as const;

/** What the form shows, as PUT /reports/{id}/cover-page takes it: trimmed,
 * blank as null (which clears the value). Only what changed since the last
 * load or save is sent -- the API merges key by key, so the rest stays as
 * it is (and Our Ref, which only staff may change, isn't sent back by
 * someone who only changed a date). A tyre keeps its other fields
 * (condition) when `tyres` -- the tyres as saved -- is given, because tyres
 * are replaced whole. */
export function coverRequestFrom(form: CoverForm, baseline: CoverForm, tyres?: TyreCondition | null): CoverPageRequest {
  const s = (v: string): string | null => v.trim() || null;
  const changed = (keys: readonly CoverFormKey[]) => keys.some((k) => form[k] !== baseline[k]);
  const tyre = (prev: TyreItem | null | undefined, mm: string, make: string, size: string): TyreItem => ({
    ...(prev || {}),
    tread_depth_mm: parseDepth(mm),
    brand: s(make),
    size: s(size),
  });
  const body: CoverPageRequest = {};
  if (form.report_id !== baseline.report_id) body.report_id = s(form.report_id);
  if (form.incident_datetime !== baseline.incident_datetime) body.incident_datetime = s(form.incident_datetime);
  if (changed(COVER_PAGE_KEYS)) {
    body.cover_page = {
      date_of_request: s(form.date_of_request),
      requested_by: s(form.requested_by),
      requested_by_address: s(form.requested_by_address),
      workshop_address: s(form.workshop_address),
      date_of_survey: s(form.date_of_survey),
      date_of_dismantle: s(form.date_of_dismantle),
      date_before_painting: s(form.date_before_painting),
      date_after_painting: s(form.date_after_painting),
      handbrake_condition: s(form.handbrake_condition),
      modification: s(form.modification),
      general_condition: s(form.general_condition),
      point_of_impact_note: s(form.point_of_impact_note),
    };
  }
  if (changed(VEHICLE_KEYS) || changed(TYRE_KEYS)) {
    body.vehicle_info = {
      plate_number: s(form.plate_number),
      make: s(form.make),
      model: s(form.model),
      date_of_registration: s(form.date_of_registration),
      engine_number: s(form.engine_number),
      vin: s(form.vin),
      odometer_reading: s(form.odometer_reading),
      transmission: s(form.transmission),
      color: s(form.color),
      steering_condition: s(form.steering_condition),
      brakes_condition: s(form.brakes_condition),
      paintwork_condition: s(form.paintwork_condition),
      point_of_impact: s(form.point_of_impact),
      tyres: {
        front_nearside: tyre(tyres?.front_nearside, form.front_nearside_mm, form.front_nearside_brand, form.front_size),
        front_offside: tyre(tyres?.front_offside, form.front_offside_mm, form.front_offside_brand, form.front_size),
        rear_nearside: tyre(tyres?.rear_nearside, form.rear_nearside_mm, form.rear_nearside_brand, form.rear_size),
        rear_offside: tyre(tyres?.rear_offside, form.rear_offside_mm, form.rear_offside_brand, form.rear_size),
      },
    };
  }
  if (changed(INSURANCE_KEYS)) {
    body.insurance_details = {
      claim_type: s(form.claim_type),
      workshop_assigned: s(form.workshop_assigned),
    };
  }
  return body;
}

/** What the AI last read for the cover fields (GET /reports/{id}.ai_read):
 * the non-empty values, with the photo each came from when the draft can
 * tell (ai_read_photos). Never printed. */
export type AiRead = {
  vehicle_info?: Record<string, string | number | null | undefined> | null;
  incident_datetime?: string | null;
} | null;

/** A field the AI read -> the photo it came from ("P03"), or null when the
 * draft can't say which photo. Keys are CoverForm keys. */
export type AiReadTags = Partial<Record<CoverFormKey, string | null>>;

/** The AI's values as the form would show them, so a tag only shows while
 * the field still says what the AI said. */
export function aiReadFrom(aiRead: AiRead | undefined, photos: Record<string, string> | null | undefined): { tags: AiReadTags; values: Partial<CoverForm> } {
  const tags: AiReadTags = {};
  const values: Partial<CoverForm> = {};
  if (!aiRead) return { tags, values };
  const vehicle = aiRead.vehicle_info || {};
  for (const key of VEHICLE_KEYS) {
    const v = text(vehicle[key]);
    if (!v) continue;
    values[key] = v;
    tags[key] = photos?.[key] || null;
  }
  const when = text(aiRead.incident_datetime);
  if (when) {
    values.incident_datetime = when;
    tags.incident_datetime = photos?.incident_datetime || null;
  }
  return { tags, values };
}
