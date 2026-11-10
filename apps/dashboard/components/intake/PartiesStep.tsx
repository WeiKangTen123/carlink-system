"use client";

import { useId, useMemo, type ReactNode } from "react";
import { ArrowLeft, CheckCircle2, Loader2, Plus, X } from "lucide-react";
import type { InsuranceDetails, PersonInvolved, PoliceReportDetails, ReportData, ThirdPartyInfo, Witness } from "@/lib/api";
import { useCurrentUser } from "@/components/CurrentUser";
import { ConfirmAiButton, FieldReviewControls } from "@/components/FieldReviewControls";
import { dropNaFor, keysInState, markChecked, partyViews, PARTY_REVIEW_KEYS as K, type FieldView, type ReviewAction } from "@/lib/fieldReview";
import type { Review } from "@/lib/review";

type Props = {
  data: ReportData;
  onChange: (update: (d: ReportData) => ReportData) => void;
  finalizing: boolean;
  /** Photos still uploading: filing the case now would leave them out. */
  uploading: boolean;
  onBack: () => void;
  onFinalize: () => void;
  /** Field review (IntakeState.review): absent from an older API. */
  review?: Review | null;
  /** The draft as the server last sent it -- what `review` describes. */
  savedData?: ReportData | null;
};

/** A labelled input with its review beside the label (contract 7). The
 * label points at the input by id: the Confirm / N/A buttons inside it
 * would otherwise be what it labels. Without a review, the plain label. */
function ReviewedField({
  label,
  view,
  onReview,
  children,
}: {
  label: string;
  view: FieldView | null | undefined;
  onReview: (key: string, action: ReviewAction) => void;
  children: (input: { id: string; "aria-label"?: string; "aria-describedby"?: string }) => ReactNode;
}) {
  const id = useId();
  if (!view) {
    return (
      <label htmlFor={`${id}-input`}>
        {label}
        {children({ id: `${id}-input` })}
      </label>
    );
  }
  return (
    <label htmlFor={`${id}-input`}>
      <span className="review-label-row">
        <span>{label}</span>
        <FieldReviewControls view={view} label={label} onReview={onReview} id={`${id}-review`} />
      </span>
      {children({ id: `${id}-input`, "aria-label": label, "aria-describedby": view.state !== "none" ? `${id}-review` : undefined })}
    </label>
  );
}

/** Step 4. Everything here stays on the Case Record (the case page's
 * drawer); none of it is printed on the report. Type of claim is asked on
 * step 3 with the other Cover Page facts. */
export function PartiesStep({ data, onChange, finalizing, uploading, onBack, onFinalize, review, savedData }: Props) {
  const user = useCurrentUser();
  const people = data.people_involved || [];
  const witnesses = data.witnesses || [];
  const police: PoliceReportDetails = data.police_report || {};
  const insurance: InsuranceDetails = data.insurance_details || {};
  const tp: ThirdPartyInfo = data.third_party_info || {};

  const setPerson = (i: number, patch: Partial<PersonInvolved>) =>
    onChange((d) => ({ ...d, people_involved: (d.people_involved || []).map((p, j) => (j === i ? { ...p, ...patch } : p)) }));
  const setWitness = (i: number, patch: Partial<Witness>) =>
    onChange((d) => ({ ...d, witnesses: (d.witnesses || []).map((w, j) => (j === i ? { ...w, ...patch } : w)) }));
  // Field review (contract 7): each fact's state, with the draft's own
  // Confirm / N/A (data.checked, saved with the draft). A fact given a value
  // is no longer N/A.
  const views = useMemo(() => partyViews(review, data, savedData, { checked: data.checked ?? {} }), [review, data, savedData]);
  const reviewFields = (keys: string[], action: ReviewAction) => onChange((d) => ({ ...d, checked: markChecked(d.checked, keys, action, user) }));
  const onReview = (key: string, action: ReviewAction) => reviewFields([key], action);
  const filled = (d: ReportData, prefix: string, patch: object): ReportData => {
    const keys = Object.entries(patch)
      .filter(([, v]) => (typeof v === "string" ? v.trim() : v))
      .map(([k]) => `${prefix}${k}`);
    const checked = dropNaFor(d.checked, keys);
    return checked === d.checked ? d : { ...d, checked };
  };
  const setPolice = (patch: Partial<PoliceReportDetails>) =>
    onChange((d) => filled({ ...d, police_report: { ...(d.police_report || {}), ...patch } }, "police_report.", patch));
  const setInsurance = (patch: Partial<InsuranceDetails>) =>
    onChange((d) => filled({ ...d, insurance_details: { ...(d.insurance_details || {}), ...patch } }, "insurance_details.", patch));
  const aiKeys = keysInState(views, "ai");
  const setTp = (patch: Partial<ThirdPartyInfo>) => onChange((d) => ({ ...d, third_party_info: { ...(d.third_party_info || {}), ...patch } }));

  return (
    <section className="card intake-panel">
      <div className="intake-panel-head">
        <h2>Parties and police</h2>
        <span className="intake-muted">Kept on the Case Record; not printed on the report. Anything left blank can be added later.</span>
        <ConfirmAiButton keys={aiKeys} onConfirm={(keys) => reviewFields(keys, "confirm")} />
      </div>

      <div className="intake-field-grid single">
        <ReviewedField label="Accident location" view={views?.[K.location]} onReview={onReview}>
          {(p) => (
            <input
              {...p}
              type="text"
              placeholder="e.g. Ubi Road 1"
              value={data.location ?? ""}
              onChange={(e) => onChange((d) => filled({ ...d, location: e.target.value }, "", { location: e.target.value }))}
            />
          )}
        </ReviewedField>
      </div>

      <div className="intake-parties-grid">
        <div className="intake-subcard">
          <h3>People involved</h3>
          {people.map((p, i) => (
            <div key={i} className="intake-person-row">
              <input type="text" placeholder="Name" value={p.name} onChange={(e) => setPerson(i, { name: e.target.value })} />
              <input type="text" placeholder="Role (e.g. Driver)" value={p.role} onChange={(e) => setPerson(i, { role: e.target.value })} />
              <input type="text" placeholder="Contact" value={p.contact ?? ""} onChange={(e) => setPerson(i, { contact: e.target.value })} />
              <button
                type="button"
                className="intake-icon-btn danger"
                aria-label="Remove person"
                onClick={() => onChange((d) => ({ ...d, people_involved: (d.people_involved || []).filter((_, j) => j !== i) }))}
              >
                <X style={{ width: 14, height: 14 }} />
              </button>
            </div>
          ))}
          <button
            type="button"
            className="add-row-button"
            onClick={() => onChange((d) => ({ ...d, people_involved: [...(d.people_involved || []), { name: "", role: "Driver", contact: "" }] }))}
          >
            <Plus style={{ width: 14, height: 14 }} /> Add person
          </button>

          <h3 style={{ marginTop: 18 }}>Witnesses</h3>
          {witnesses.map((w, i) => (
            <div key={i} className="intake-person-row">
              <input type="text" placeholder="Name" value={w.name} onChange={(e) => setWitness(i, { name: e.target.value })} />
              <input type="text" placeholder="Contact" value={w.contact ?? ""} onChange={(e) => setWitness(i, { contact: e.target.value })} />
              <input type="text" placeholder="Statement" value={w.statement ?? ""} onChange={(e) => setWitness(i, { statement: e.target.value })} />
              <button
                type="button"
                className="intake-icon-btn danger"
                aria-label="Remove witness"
                onClick={() => onChange((d) => ({ ...d, witnesses: (d.witnesses || []).filter((_, j) => j !== i) }))}
              >
                <X style={{ width: 14, height: 14 }} />
              </button>
            </div>
          ))}
          <button
            type="button"
            className="add-row-button"
            onClick={() => onChange((d) => ({ ...d, witnesses: [...(d.witnesses || []), { name: "", contact: "", statement: "" }] }))}
          >
            <Plus style={{ width: 14, height: 14 }} /> Add witness
          </button>
        </div>

        <div className="intake-subcard">
          <h3>Police</h3>
          {views ? (
            <span className="review-label-row" style={{ justifyContent: "flex-start" }}>
              <label className="checkbox-label">
                <input type="checkbox" checked={Boolean(police.reported_to_police)} onChange={(e) => setPolice({ reported_to_police: e.target.checked })} /> Reported to police
              </label>
              <FieldReviewControls view={views[K.reported_to_police]} label="Reported to police" onReview={onReview} />
            </span>
          ) : (
            <label className="checkbox-label">
              <input type="checkbox" checked={Boolean(police.reported_to_police)} onChange={(e) => setPolice({ reported_to_police: e.target.checked })} /> Reported to police
            </label>
          )}
          <div className="intake-field-grid single">
            <ReviewedField label="Station" view={views?.[K.police_station]} onReview={onReview}>
              {(p) => (
                <input {...p} type="text" placeholder="e.g. Sengkang NPC" value={police.police_station ?? ""} onChange={(e) => setPolice({ police_station: e.target.value })} />
              )}
            </ReviewedField>
            <ReviewedField label="Report no." view={views?.[K.report_number]} onReview={onReview}>
              {(p) => <input {...p} type="text" value={police.report_number ?? ""} onChange={(e) => setPolice({ report_number: e.target.value })} />}
            </ReviewedField>
            <ReviewedField label="Officer" view={views?.[K.officer_name]} onReview={onReview}>
              {(p) => <input {...p} type="text" value={police.officer_name ?? ""} onChange={(e) => setPolice({ officer_name: e.target.value })} />}
            </ReviewedField>
          </div>
        </div>

        <div className="intake-subcard">
          <h3>Insurer</h3>
          <div className="intake-field-grid single">
            <ReviewedField label="Insurer" view={views?.[K.insurer_name]} onReview={onReview}>
              {(p) => (
                <input {...p} type="text" placeholder="e.g. NTUC Income" value={insurance.insurer_name ?? ""} onChange={(e) => setInsurance({ insurer_name: e.target.value })} />
              )}
            </ReviewedField>
            <ReviewedField label="Claim no." view={views?.[K.claim_number]} onReview={onReview}>
              {(p) => <input {...p} type="text" value={insurance.claim_number ?? ""} onChange={(e) => setInsurance({ claim_number: e.target.value })} />}
            </ReviewedField>
          </div>

          <h3 style={{ marginTop: 18 }}>Other party</h3>
          <div className="intake-field-grid single">
            <label>
              Plate
              <input type="text" value={tp.plate_number ?? ""} onChange={(e) => setTp({ plate_number: e.target.value })} />
            </label>
            <label>
              Make / model
              <input type="text" value={tp.make_model ?? ""} onChange={(e) => setTp({ make_model: e.target.value })} />
            </label>
            <label>
              Their insurer
              <input type="text" value={tp.insurer_name ?? ""} onChange={(e) => setTp({ insurer_name: e.target.value })} />
            </label>
          </div>
        </div>
      </div>

      <div className="intake-footer">
        <button type="button" className="btn-secondary-modern" onClick={onBack} disabled={finalizing}>
          <ArrowLeft style={{ width: 14, height: 14 }} /> Back
        </button>
        {uploading && <span className="intake-muted">Waiting for photos to finish uploading…</span>}
        <button type="button" className="btn-primary-modern" onClick={onFinalize} disabled={finalizing || uploading}>
          {finalizing ? <Loader2 className="spin" style={{ width: 15, height: 15 }} /> : <CheckCircle2 style={{ width: 15, height: 15 }} />}
          {finalizing ? "Filing case…" : "File case"}
        </button>
      </div>
    </section>
  );
}
