"use client";

import React, { useId, useMemo } from "react";
import { CLAIM_TYPE_OPTIONS } from "@/lib/api";
import { coverChecks, tyreChecks, type DamagedPart } from "@/lib/coverChecks";
import { POINT_OF_IMPACT_NOTE, USUAL_STATIC_CHECK, type AiNotes, type AiReadTags, type CoverForm, type CoverFormKey } from "@/lib/coverForm";
import { COVER_REVIEW_KEYS, TYRES_REVIEW_KEY, type FieldView, type ReviewAction } from "@/lib/fieldReview";
import { FieldReviewControls } from "./FieldReviewControls";

const NO_PARTS: DamagedPart[] = [];

/** The Cover Page's six sections, with the firm's labels and in its order,
 * as a controlled form. Used by the case page's Cover tab and by the intake
 * wizard's review step: the host owns the form state, the impact sketch and
 * Save / Discard. Lifted from CaseAssessmentTab.tsx on 2026-11-04. */
export function CoverPageFields({
  value,
  onChange,
  disabled,
  ourRefEditable,
  aiRead,
  aiValues,
  showUsualValues = true,
  sketch,
  review,
  onReview,
  reviewBusy,
  damage,
  aiNotes,
}: {
  value: CoverForm;
  onChange: (patch: Partial<CoverForm>) => void;
  disabled: boolean;
  /** Our Ref is the firm's file number: only staff who may sign off set it. */
  ourRefEditable: boolean;
  /** Fields the AI read, with the photo each came from ("P03", or null when
   * unknown). A tag shows beside the field while it still says what the
   * AI said. */
  aiRead?: AiReadTags;
  /** What the AI said for those fields, as the form shows them. Left out,
   * the tag shows for every key in aiRead. */
  aiValues?: Partial<CoverForm>;
  /** The "Use the firm's usual values" button on the static check. */
  showUsualValues?: boolean;
  /** Rendered under 5 POINT OF IMPACT (the host's top-view sketch). */
  sketch?: React.ReactNode;
  /** Field review (contract 7): each field's state by review key, from
   * coverViews(). A badge then shows beside each field, with the AI's
   * photo folded in ("AI · P03") instead of the "(P03)" tag. Left out (an
   * older API): no badges, and the tags show as before. */
  review?: Record<string, FieldView> | null;
  /** Confirm / N/A / Undo on one field (by review key). Left out, the
   * badges show without buttons. */
  onReview?: (key: string, action: ReviewAction) => void;
  reviewBusy?: boolean;
  /** The case's damaged parts, for the soft check of the point of impact
   * against where they are (lib/coverChecks.ts). Left out, that one check
   * is skipped; the others need only the form. */
  damage?: DamagedPart[] | null;
  /** The AI's own static-check / transmission words that have no firm
   * word with their meaning (GET .../ai_read.notes, aiReadFrom): shown
   * under the field, never printed. */
  aiNotes?: AiNotes;
}) {
  // The datalists are shared by every input of the form; the ids are made
  // unique so two forms on one page (the wizard beside a case) don't clash.
  const uid = useId();
  const claimTypesId = `${uid}-claim-types`;
  const seeAnnexId = `${uid}-see-annex`;
  const goodNilId = `${uid}-good-nil`;
  const impactNoteId = `${uid}-impact-note`;
  // Soft checks (never a block): worked out from the form as typed, so a
  // tag goes as soon as the value is fixed. Not on a form nobody may change
  // (a signed-off case); still while a save is on its way (no flicker).
  const showChecks = !disabled || Boolean(onReview);
  const checks = useMemo(() => (showChecks ? coverChecks(value, damage ?? NO_PARTS) : {}), [value, damage, showChecks]);
  const tyreWarnings = tyreChecks(checks);
  // The AI's own word, while the field doesn't already say it.
  const noteFor = (key: CoverFormKey): string | undefined => {
    const note = aiNotes?.[key];
    return note && note.toLowerCase() !== value[key].trim().toLowerCase() ? note : undefined;
  };

  const set = (key: CoverFormKey) => (v: string) => onChange({ [key]: v } as Partial<CoverForm>);
  const tag = (key: CoverFormKey): string | null => {
    if (!aiRead || !(key in aiRead)) return null;
    if (aiValues && aiValues[key] !== undefined && aiValues[key] !== value[key]) return null;
    const photo = aiRead[key];
    return photo ? `(${photo})` : "(AI)";
  };
  // With a review the badge says it: "AI · P03" (the photo from the review,
  // else from the tag while the field still says what the AI said).
  const photoOf = (key: CoverFormKey): string | null => (tag(key) ? aiRead?.[key] || null : null);
  const reviewProps = (key: CoverFormKey) =>
    review ? { view: review[COVER_REVIEW_KEYS[key]] ?? null, photo: photoOf(key), onReview: disabled ? undefined : onReview, reviewBusy } : {};
  const field = (key: CoverFormKey, label: string, extra: { hint?: string; list?: string; wide?: boolean } = {}) => (
    <CoverField
      label={label}
      value={value[key]}
      onChange={set(key)}
      disabled={disabled}
      aiTag={review ? null : tag(key)}
      hint={extra.hint}
      list={extra.list}
      wide={extra.wide}
      reviewKey={COVER_REVIEW_KEYS[key]}
      checks={checks[key]}
      note={noteFor(key)}
      {...reviewProps(key)}
    />
  );
  const usualValuesDiffer = (Object.keys(USUAL_STATIC_CHECK) as CoverFormKey[]).some((k) => value[k] !== USUAL_STATIC_CHECK[k]);

  return (
    <div className="cover-form">
      <datalist id={claimTypesId}>
        {CLAIM_TYPE_OPTIONS.map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>
      <datalist id={seeAnnexId}>
        <option value="see annex(es)" />
      </datalist>
      <datalist id={goodNilId}>
        <option value="Good" />
        <option value="Nil" />
      </datalist>
      <datalist id={impactNoteId}>
        <option value={POINT_OF_IMPACT_NOTE} />
      </datalist>

      <div className="cover-form-section">1 &nbsp;Reference</div>
      <CoverField
        label="Our Ref"
        value={value.report_id}
        onChange={set("report_id")}
        disabled={disabled || !ourRefEditable}
        reviewKey={COVER_REVIEW_KEYS.report_id}
        {...reviewProps("report_id")}
        onReview={disabled || !ourRefEditable ? undefined : onReview}
        hint={
          ourRefEditable
            ? "The firm's file number, e.g. CL 12330 SMV 7530 X. Blank prints as (to be issued)."
            : "The firm's file number, set by staff who may sign off. Blank prints as (to be issued)."
        }
      />
      {field("date_of_request", "Date of request")}
      {field("requested_by", "Requested by")}
      {field("requested_by_address", "C/o address (requested by)", {
        hint: "Printed under the name, e.g. C/o Blk 3023A, Ubi Road 1, #01-60, Singapore 408717",
      })}
      {field("claim_type", "Type of claim", { list: claimTypesId })}
      {field("workshop_assigned", "Workshop", { hint: "As the letter names it, e.g. M/S People's Vehicle Recovery Service" })}
      {field("workshop_address", "Workshop address")}
      {field("incident_datetime", "Accident date")}
      {field("date_of_survey", "Date of survey", { hint: "e.g. 27th February 2023 (10.15am)" })}
      {field("date_of_dismantle", "Date of dismantle parts")}
      {field("date_before_painting", "Date of before painting")}
      {field("date_after_painting", "Date of after painting")}

      <div className="cover-form-section">2 &nbsp;Details of vehicle</div>
      {field("plate_number", "Registration No.")}
      {field("make", "Make")}
      {field("model", "Model", { hint: "Printed together as Make & Model" })}
      {field("date_of_registration", "Date of registration")}
      {field("engine_number", "Engine No.")}
      {field("vin", "Chassis No.")}
      {field("odometer_reading", "Speedometer reading", { hint: "e.g. 058615 Km" })}
      {field("transmission", "Transmission")}
      {field("color", "Colour")}

      <div className="cover-form-section">
        <span>3 &nbsp;Static check</span>
        {showUsualValues && !disabled && (
          <button
            type="button"
            className="btn-secondary-modern cover-usual-values"
            disabled={!usualValuesDiffer}
            title='Fills Steering, Footbrake and Handbrake with "see annex(es)", Paintwork and General condition with "Good" and Modification with "Nil". Nothing is saved until you save the cover page.'
            onClick={() => onChange({ ...USUAL_STATIC_CHECK })}
          >
            Use the firm&apos;s usual values
          </button>
        )}
      </div>
      {field("steering_condition", "Steering", { list: seeAnnexId })}
      {field("brakes_condition", "Footbrake", { list: seeAnnexId })}
      {field("handbrake_condition", "Handbrake", { list: seeAnnexId })}
      {field("paintwork_condition", "Paintwork", { list: goodNilId })}
      {field("modification", "Modification", { list: goodNilId })}
      {field("general_condition", "General condition", { list: goodNilId })}

      <div className="cover-form-section">
        <span>4 &nbsp;Tyre condition</span>
        {review && (
          <FieldReviewControls view={review[TYRES_REVIEW_KEY]} label="Tyres" onReview={disabled ? undefined : onReview} busy={reviewBusy} />
        )}
      </div>
      <div className="cover-field wide">
        <div className="annex-table-wrapper">
          <table className="annex-table cover-tyre-table">
            <thead>
              <tr>
                <th style={{ width: 110 }}></th>
                <th style={{ width: 90 }}>Nearside mm</th>
                <th>Nearside make</th>
                <th style={{ width: 90 }}>Offside mm</th>
                <th>Offside make</th>
                <th>Size</th>
              </tr>
            </thead>
            <tbody>
              {(["front", "rear"] as const).map((axle) => (
                <tr key={axle}>
                  <td style={{ fontWeight: 600 }}>{axle === "front" ? "Front tyre" : "Rear tyre"}</td>
                  <td>
                    <input
                      type="text"
                      inputMode="decimal"
                      className="annex-cell-input"
                      value={value[`${axle}_nearside_mm`]}
                      placeholder="mm"
                      aria-label={`${axle} nearside tread depth (mm)`}
                      disabled={disabled}
                      onChange={(e) => onChange({ [`${axle}_nearside_mm`]: e.target.value } as Partial<CoverForm>)}
                    />
                  </td>
                  <td>
                    <input
                      type="text"
                      className="annex-cell-input"
                      value={value[`${axle}_nearside_brand`]}
                      placeholder="Not recorded"
                      aria-label={`${axle} nearside tyre make`}
                      disabled={disabled}
                      onChange={(e) => onChange({ [`${axle}_nearside_brand`]: e.target.value } as Partial<CoverForm>)}
                    />
                  </td>
                  <td>
                    <input
                      type="text"
                      inputMode="decimal"
                      className="annex-cell-input"
                      value={value[`${axle}_offside_mm`]}
                      placeholder="mm"
                      aria-label={`${axle} offside tread depth (mm)`}
                      disabled={disabled}
                      onChange={(e) => onChange({ [`${axle}_offside_mm`]: e.target.value } as Partial<CoverForm>)}
                    />
                  </td>
                  <td>
                    <input
                      type="text"
                      className="annex-cell-input"
                      value={value[`${axle}_offside_brand`]}
                      placeholder="Not recorded"
                      aria-label={`${axle} offside tyre make`}
                      disabled={disabled}
                      onChange={(e) => onChange({ [`${axle}_offside_brand`]: e.target.value } as Partial<CoverForm>)}
                    />
                  </td>
                  <td>
                    <input
                      type="text"
                      className="annex-cell-input"
                      value={value[`${axle}_size`]}
                      placeholder="e.g. 215/60 R16"
                      aria-label={`${axle} tyre size`}
                      disabled={disabled}
                      onChange={(e) => onChange({ [`${axle}_size`]: e.target.value } as Partial<CoverForm>)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <span className="cover-field-hint">Printed per axle as 7mm/Dunlop | 7mm/Dunlop | 215/60 R16.</span>
        {tyreWarnings.length > 0 && (
          <span className="p1b-check-text" role="note">
            <span className="p1b-check-tag" aria-hidden>
              check
            </span>{" "}
            {tyreWarnings.join(" · ")}
          </span>
        )}
      </div>

      <div className="cover-form-section">5 &nbsp;Point of impact</div>
      {sketch && <div className="cover-field wide">{sketch}</div>}
      {field("point_of_impact", "Point of impact", { hint: "e.g. Rear centre" })}
      {field("point_of_impact_note", "Note printed under 5", { hint: `The firm writes: ${POINT_OF_IMPACT_NOTE}`, list: impactNoteId })}

      <div className="cover-form-section">6 &nbsp;Remark</div>
      <div className="cover-field wide" style={{ fontSize: 12, color: "var(--text-secondary)" }}>
        Printed on every report: &quot;This inspection was conducted entirely on a &quot;WITHOUT PREJUDICE&quot; basis and we have not authorised any
        repair.&quot;
      </div>
    </div>
  );
}

/** One labelled text field of the Cover Page. Empty shows "Not recorded"
 * (never a guessed value), as the printed report does. */
function CoverField({
  label,
  value,
  onChange,
  disabled,
  wide,
  hint,
  list,
  aiTag,
  view,
  photo,
  onReview,
  reviewBusy,
  reviewKey,
  checks,
  note,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
  wide?: boolean;
  hint?: string;
  /** id of a <datalist> with the usual wordings. */
  list?: string;
  /** "(P03)" while the field still says what the AI read from that photo. */
  aiTag?: string | null;
  /** Field review: the badge and its buttons beside the label. */
  view?: FieldView | null;
  photo?: string | null;
  onReview?: (key: string, action: ReviewAction) => void;
  reviewBusy?: boolean;
  /** The field's review key, on the element as data-review-key: the
   * sign-off box's "Go to it" finds the field by it. */
  reviewKey?: string;
  /** Soft checks (lib/coverChecks.ts): a "check" tag and their words. */
  checks?: string[];
  /** The AI's own word, when it had no firm word with its meaning. */
  note?: string | null;
}) {
  // The label points at the input by id: the Confirm / N/A buttons inside
  // it would otherwise be what it labels.
  const id = useId();
  const warnings = checks ?? [];
  const warned = warnings.length > 0;
  // On the sign-off's must-have list and still blank (not marked N/A).
  const forSignOff = Boolean(view?.mustHave && view.blank && view.state !== "na");
  const inline = Boolean(view) || warned || forSignOff;
  const describedBy = [view && view.state !== "none" ? `${id}-review` : "", warned ? `${id}-checks` : "", note ? `${id}-note` : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <label className={`cover-field${wide ? " wide" : ""}`} htmlFor={`${id}-input`} data-review-key={reviewKey}>
      <span className={inline ? "cover-field-head" : undefined}>
        {inline ? <span>{label}</span> : label}
        {aiTag && (
          <span className="cover-ai-tag" title="Read by the AI from the photos. Check it against the vehicle.">
            {aiTag}
          </span>
        )}
        {(warned || forSignOff) && (
          <span className="p1b-tags">
            {forSignOff && (
              <span className="p1b-required-tag" title="Needed before sign-off: fill it in, or mark it N/A if it doesn't apply.">
                for sign-off
              </span>
            )}
            {warned && (
              <span className="p1b-check-tag" title={warnings.join("; ")}>
                check
              </span>
            )}
          </span>
        )}
        {view && <FieldReviewControls view={view} label={label} photo={photo} onReview={onReview} busy={reviewBusy} id={`${id}-review`} />}
      </span>
      <input
        id={`${id}-input`}
        type="text"
        value={value}
        list={list}
        placeholder="Not recorded"
        disabled={disabled}
        aria-label={inline ? label : undefined}
        aria-describedby={describedBy || undefined}
        onChange={(e) => onChange(e.target.value)}
      />
      {warned && (
        <span className="p1b-check-text" id={`${id}-checks`}>
          {warnings.join(" · ")}
        </span>
      )}
      {note && (
        <span className="p1b-ai-note" id={`${id}-note`}>
          The AI read &quot;{note}&quot;, not one of the firm&apos;s words, so it isn&apos;t printed. Write the firm&apos;s word, or keep the usual
          one.
        </span>
      )}
      {hint && <span className="cover-field-hint">{hint}</span>}
    </label>
  );
}
