"use client";

import React, { useId } from "react";
import { CLAIM_TYPE_OPTIONS } from "@/lib/api";
import { USUAL_STATIC_CHECK, type AiReadTags, type CoverForm, type CoverFormKey } from "@/lib/coverForm";

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
}) {
  // The datalists are shared by every input of the form; the ids are made
  // unique so two forms on one page (the wizard beside a case) don't clash.
  const uid = useId();
  const claimTypesId = `${uid}-claim-types`;
  const seeAnnexId = `${uid}-see-annex`;
  const goodNilId = `${uid}-good-nil`;

  const set = (key: CoverFormKey) => (v: string) => onChange({ [key]: v } as Partial<CoverForm>);
  const tag = (key: CoverFormKey): string | null => {
    if (!aiRead || !(key in aiRead)) return null;
    if (aiValues && aiValues[key] !== undefined && aiValues[key] !== value[key]) return null;
    const photo = aiRead[key];
    return photo ? `(${photo})` : "(AI)";
  };
  const field = (key: CoverFormKey, label: string, extra: { hint?: string; list?: string; wide?: boolean } = {}) => (
    <CoverField
      label={label}
      value={value[key]}
      onChange={set(key)}
      disabled={disabled}
      aiTag={tag(key)}
      hint={extra.hint}
      list={extra.list}
      wide={extra.wide}
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

      <div className="cover-form-section">1 &nbsp;Reference</div>
      <CoverField
        label="Our Ref"
        value={value.report_id}
        onChange={set("report_id")}
        disabled={disabled || !ourRefEditable}
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

      <div className="cover-form-section">4 &nbsp;Tyre condition</div>
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
      </div>

      <div className="cover-form-section">5 &nbsp;Point of impact</div>
      {sketch && <div className="cover-field wide">{sketch}</div>}
      {field("point_of_impact", "Point of impact", { hint: "e.g. Rear centre" })}
      {field("point_of_impact_note", "Note printed under 5", { hint: "The firm writes: See Annex(es) and Motor Accident Reports" })}

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
}) {
  return (
    <label className={`cover-field${wide ? " wide" : ""}`}>
      <span>
        {label}
        {aiTag && (
          <span className="cover-ai-tag" title="Read by the AI from the photos. Check it against the vehicle.">
            {aiTag}
          </span>
        )}
      </span>
      <input type="text" value={value} list={list} placeholder="Not recorded" disabled={disabled} onChange={(e) => onChange(e.target.value)} />
      {hint && <span className="cover-field-hint">{hint}</span>}
    </label>
  );
}
