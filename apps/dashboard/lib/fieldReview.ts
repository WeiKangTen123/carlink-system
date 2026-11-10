/** Field review on the forms (contract 7, agent FIELDS): which review key
 * each form field is, each field's state while it is being edited, the
 * Confirm / N/A / Undo a person adds, and the case page's confirmations.
 * The states, rules and badge are the shared base: lib/review.ts and
 * components/ReviewBadge.tsx. */
import { useState } from "react";
import { confirmFields, type CheckedEntry, type CheckedSet, type CurrentUser, type ReportData, type ReportDetail } from "./api";
import type { CoverForm, CoverFormKey } from "./coverForm";
import { localState, type FieldReview, type FieldRule, type Review, type ReviewCounts, type ReviewState } from "./review";

/** The ten tyre boxes are one review key. */
export const TYRES_REVIEW_KEY = "vehicle_info.tyres";

/** Each Cover Page form field's review key: its dotted path in the report
 * data (lib/coverForm.ts reads the same paths). */
export const COVER_REVIEW_KEYS: Record<CoverFormKey, string> = {
  report_id: "report_id",
  date_of_request: "cover_page.date_of_request",
  requested_by: "cover_page.requested_by",
  requested_by_address: "cover_page.requested_by_address",
  claim_type: "insurance_details.claim_type",
  workshop_assigned: "insurance_details.workshop_assigned",
  workshop_address: "cover_page.workshop_address",
  incident_datetime: "incident_datetime",
  date_of_survey: "cover_page.date_of_survey",
  date_of_dismantle: "cover_page.date_of_dismantle",
  date_before_painting: "cover_page.date_before_painting",
  date_after_painting: "cover_page.date_after_painting",
  plate_number: "vehicle_info.plate_number",
  make: "vehicle_info.make",
  model: "vehicle_info.model",
  date_of_registration: "vehicle_info.date_of_registration",
  engine_number: "vehicle_info.engine_number",
  vin: "vehicle_info.vin",
  odometer_reading: "vehicle_info.odometer_reading",
  transmission: "vehicle_info.transmission",
  color: "vehicle_info.color",
  steering_condition: "vehicle_info.steering_condition",
  brakes_condition: "vehicle_info.brakes_condition",
  handbrake_condition: "cover_page.handbrake_condition",
  paintwork_condition: "vehicle_info.paintwork_condition",
  modification: "cover_page.modification",
  general_condition: "cover_page.general_condition",
  front_nearside_mm: TYRES_REVIEW_KEY,
  front_nearside_brand: TYRES_REVIEW_KEY,
  front_offside_mm: TYRES_REVIEW_KEY,
  front_offside_brand: TYRES_REVIEW_KEY,
  front_size: TYRES_REVIEW_KEY,
  rear_nearside_mm: TYRES_REVIEW_KEY,
  rear_nearside_brand: TYRES_REVIEW_KEY,
  rear_offside_mm: TYRES_REVIEW_KEY,
  rear_offside_brand: TYRES_REVIEW_KEY,
  rear_size: TYRES_REVIEW_KEY,
  point_of_impact: "vehicle_info.point_of_impact",
  point_of_impact_note: "cover_page.point_of_impact_note",
};

const TYRE_FORM_KEYS = (Object.keys(COVER_REVIEW_KEYS) as CoverFormKey[]).filter((k) => COVER_REVIEW_KEYS[k] === TYRES_REVIEW_KEY);

/** The Case record facts with a review key (wizard step 4, the Case record
 * drawer). Their values are read straight from the data by path. */
export const PARTY_REVIEW_KEYS = {
  location: "location",
  accident_type: "accident_type",
  reported_to_police: "police_report.reported_to_police",
  police_station: "police_report.police_station",
  report_number: "police_report.report_number",
  officer_name: "police_report.officer_name",
  date_reported: "police_report.date_reported",
  insurer_name: "insurance_details.insurer_name",
  policy_number: "insurance_details.policy_number",
  claim_number: "insurance_details.claim_number",
} as const;

const COVER_KEY_SET = new Set<string>(Object.values(COVER_REVIEW_KEYS));
const PARTY_KEY_SET = new Set<string>(Object.values(PARTY_REVIEW_KEYS));

/** Which review.counts section a key is counted in, as far as the forms
 * know: the Cover Page form's keys -> cover, the Case record's -> parties. */
export function sectionOfKey(key: string): "cover" | "parties" | null {
  if (COVER_KEY_SET.has(key)) return "cover";
  if (PARTY_KEY_SET.has(key)) return "parties";
  return null;
}

const isBlank = (v: unknown) => v === null || v === undefined || (typeof v === "string" && v.trim() === "");

/** A value in the data by dotted path, as trimmed text. "Reported to
 * police" only counts when ticked: a default false is not a recorded "No". */
export function valueAt(data: ReportData | null | undefined, key: string): unknown {
  let v: unknown = data;
  for (const part of key.split(".")) v = v && typeof v === "object" ? (v as Record<string, unknown>)[part] : undefined;
  if (typeof v === "boolean") return v ? true : null;
  if (typeof v === "string") return v.trim();
  return v ?? null;
}

/** One field's review as a form shows it. */
export type FieldView = {
  key: string;
  state: ReviewState;
  /** For the badge: the server's (photo, who, when) while the state is the
   * server's; who confirmed it, from the draft, in the wizard. */
  review: FieldReview | null;
  rule: FieldRule | null;
  blank: boolean;
  /** Changed on the case page and not saved yet: nothing to confirm or
   * mark N/A until it is (the wizard saves as it goes). */
  unsaved: boolean;
};

/** The wizard keeps its confirmations in the draft (data.checked): they
 * count at once, before the next save. */
export type DraftChecks = { checked: Record<string, CheckedEntry> };

/** A field's state while it is edited: the server's review, unless the
 * value changed since (localState), or -- in the wizard -- the draft says
 * a person confirmed it or marked it N/A. An N/A on a field that has since
 * been given a value is that person's value. */
export function fieldView(review: Review, key: string, value: unknown, saved: unknown, draft?: DraftChecks): FieldView {
  const server = review.fields[key] ?? null;
  const rule = review.rules[key] ?? null;
  const blank = isBlank(value);
  const unsaved = !draft && JSON.stringify(value ?? null) !== JSON.stringify(saved ?? null);
  const view = (state: ReviewState, fr: FieldReview | null): FieldView => ({ key, state, review: fr, rule, blank, unsaved });
  if (draft) {
    const entry = draft.checked[key];
    if (entry) {
      const state: ReviewState = entry.na && blank ? "na" : "you";
      return view(state, { state, by: entry.by ?? null, at: entry.at ?? null });
    }
    // An N/A taken back here since the server's copy.
    if (server?.state === "na" && blank) return view(rule === "office" || rule === "doc" || rule === "template" ? "needs" : "none", null);
  }
  let state = localState(review, key, value, saved);
  if (state === "na" && !blank) state = "you";
  return view(state, state === server?.state ? server : { state });
}

/** A cover field's value for comparing: trimmed text; the tyres as one. */
function coverValue(form: CoverForm, key: string, formKey: CoverFormKey): string {
  if (key !== TYRES_REVIEW_KEY) return form[formKey].trim();
  const parts = TYRE_FORM_KEYS.map((k) => form[k].trim());
  return parts.some(Boolean) ? parts.join("|") : "";
}

/** Every Cover Page field's view, by review key; null without a review
 * (an older API). `base` is the form as the review saw it. */
export function coverViews(
  review: Review | null | undefined,
  form: CoverForm,
  base: CoverForm,
  draft?: DraftChecks,
): Record<string, FieldView> | null {
  if (!review) return null;
  const out: Record<string, FieldView> = {};
  for (const formKey of Object.keys(COVER_REVIEW_KEYS) as CoverFormKey[]) {
    const key = COVER_REVIEW_KEYS[formKey];
    if (!out[key]) out[key] = fieldView(review, key, coverValue(form, key, formKey), coverValue(base, key, formKey), draft);
  }
  return out;
}

/** The Case record fields' views, by review key. `base` is the data as
 * the review saw it. */
export function partyViews(
  review: Review | null | undefined,
  data: ReportData,
  base: ReportData | null | undefined,
  draft?: DraftChecks,
): Record<string, FieldView> | null {
  if (!review) return null;
  const out: Record<string, FieldView> = {};
  for (const key of Object.values(PARTY_REVIEW_KEYS)) out[key] = fieldView(review, key, valueAt(data, key), valueAt(base ?? data, key), draft);
  return out;
}

export type ReviewAction = "confirm" | "na" | "undo";

/** What a person can do with a field: confirm what the AI read or guessed,
 * mark an office or optional field N/A while it is blank, or take an N/A
 * back. */
export function reviewActions(view: FieldView): ReviewAction[] {
  if (view.unsaved) return [];
  if (view.state === "ai" || view.state === "check") return ["confirm"];
  if (view.state === "na") return ["undo"];
  if (view.blank && (view.rule === "office" || view.rule === "optional")) return ["na"];
  return [];
}

/** The keys in a state ("ai" for "Confirm N AI-read fields"). */
export function keysInState(views: Record<string, FieldView> | null, state: ReviewState): string[] {
  return views ? Object.values(views).filter((v) => v.state === state).map((v) => v.key) : [];
}

/** An action as PUT .../checked takes it. */
export function checkedChange(keys: string[], action: ReviewAction): { set: CheckedSet; clear: string[] } {
  if (action === "undo") return { set: {}, clear: keys };
  const set: CheckedSet = {};
  for (const key of keys) set[key] = action === "na" ? { na: true } : {};
  return { set, clear: [] };
}

/** data.checked with an action applied (the wizard). */
export function markChecked(
  checked: Record<string, CheckedEntry> | null | undefined,
  keys: string[],
  action: ReviewAction,
  user: CurrentUser | null,
): Record<string, CheckedEntry> {
  const next = { ...(checked || {}) };
  const at = new Date().toISOString();
  for (const key of keys) {
    if (action === "undo") delete next[key];
    else next[key] = { by: user?.name ?? null, by_id: user?.id ?? null, at, ...(action === "na" ? { na: true } : {}) };
  }
  return next;
}

/** data.checked without the N/A of fields just given a value (the wizard):
 * an N/A only stands while the field is blank. */
export function dropNaFor(
  checked: Record<string, CheckedEntry> | null | undefined,
  keys: string[],
): Record<string, CheckedEntry> | null | undefined {
  if (!checked || !keys.some((k) => checked[k]?.na)) return checked;
  const next = { ...checked };
  for (const key of keys) if (next[key]?.na) delete next[key];
  return next;
}

type CountKey = Exclude<ReviewState, "none">;
const isOpen = (s: ReviewState) => s === "check" || s === "needs";

/** The review with these fields' states put in, and the cover / parties
 * counts moved to match, so the progress line follows a confirmation or an
 * edit before the server works the review out again. */
export function withStates(review: Review, states: Record<string, FieldView | FieldReview> | null): Review {
  if (!states) return review;
  const counts = { ...review.counts, cover: { ...review.counts.cover }, parties: { ...review.counts.parties } };
  const fields = { ...review.fields };
  let open = review.open;
  let changed = false;
  for (const [key, v] of Object.entries(states)) {
    const prev = review.fields[key]?.state ?? "none";
    const next = v.state;
    if (prev === next) continue;
    changed = true;
    fields[key] = "key" in v ? (v.review ?? { state: next }) : v;
    const section = sectionOfKey(key);
    if (section) {
      const c: ReviewCounts = counts[section];
      if (prev !== "none") c[prev as CountKey] = Math.max(0, c[prev as CountKey] - 1);
      if (next !== "none") c[next as CountKey] += 1;
    }
    open += (isOpen(next) ? 1 : 0) - (isOpen(prev) ? 1 : 0);
  }
  return changed ? { ...review, fields, counts, open: Math.max(0, open) } : review;
}

/** The case page's review: the server's, with this person's confirmations
 * shown at once while PUT /reports/{id}/checked and the page refresh are on
 * their way. A fresh report from the server replaces it. */
export type CaseReview = {
  review: Review | null;
  /** Saves an action on these keys; false when it failed (`error` says why). */
  apply: (keys: string[], action: ReviewAction) => Promise<boolean>;
  saving: boolean;
  error: string | null;
};

export function useCaseReview(report: ReportDetail, user: CurrentUser | null, refresh: () => void): CaseReview {
  const base = report.review ?? null;
  // Kept against the review it was made from: a refreshed report drops it.
  const [live, setLive] = useState<{ base: Review; review: Review } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const review = base && live?.base === base ? live.review : base;

  const apply = async (keys: string[], action: ReviewAction): Promise<boolean> => {
    if (!base || !review || saving || keys.length === 0) return false;
    const before = live?.base === base ? live : null;
    const at = new Date().toISOString();
    const states: Record<string, FieldReview> = {};
    for (const key of keys) {
      const rule = review.rules[key];
      states[key] =
        action === "undo"
          ? { state: rule === "office" || rule === "doc" || rule === "template" ? "needs" : "none" }
          : { state: action === "na" ? "na" : "you", by: user?.name ?? null, at };
    }
    setLive({ base, review: withStates(review, states) });
    setSaving(true);
    setError(null);
    try {
      const { set, clear } = checkedChange(keys, action);
      const res = await confirmFields(report.id, set, clear);
      if (res.review) setLive({ base, review: res.review });
      refresh();
      return true;
    } catch (e) {
      setLive(before);
      setError(e instanceof Error ? e.message : "Couldn't save the confirmation");
      return false;
    } finally {
      setSaving(false);
    }
  };

  return { review, apply, saving, error };
}
