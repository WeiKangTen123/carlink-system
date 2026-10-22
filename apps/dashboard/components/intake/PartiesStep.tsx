"use client";

import { ArrowLeft, CheckCircle2, Loader2, Plus, X } from "lucide-react";
import {
  CLAIM_TYPE_OPTIONS,
  type InsuranceDetails,
  type PersonInvolved,
  type PoliceReportDetails,
  type ReportData,
  type ThirdPartyInfo,
  type Witness,
} from "@/lib/api";

type Props = {
  data: ReportData;
  onChange: (update: (d: ReportData) => ReportData) => void;
  finalizing: boolean;
  /** Photos still uploading: creating the case now would leave them out. */
  uploading: boolean;
  onBack: () => void;
  onFinalize: () => void;
};

export function PartiesStep({ data, onChange, finalizing, uploading, onBack, onFinalize }: Props) {
  const people = data.people_involved || [];
  const witnesses = data.witnesses || [];
  const police: PoliceReportDetails = data.police_report || {};
  const insurance: InsuranceDetails = data.insurance_details || {};
  const tp: ThirdPartyInfo = data.third_party_info || {};

  const setPerson = (i: number, patch: Partial<PersonInvolved>) =>
    onChange((d) => ({ ...d, people_involved: (d.people_involved || []).map((p, j) => (j === i ? { ...p, ...patch } : p)) }));
  const setWitness = (i: number, patch: Partial<Witness>) =>
    onChange((d) => ({ ...d, witnesses: (d.witnesses || []).map((w, j) => (j === i ? { ...w, ...patch } : w)) }));
  const setPolice = (patch: Partial<PoliceReportDetails>) => onChange((d) => ({ ...d, police_report: { ...(d.police_report || {}), ...patch } }));
  const setInsurance = (patch: Partial<InsuranceDetails>) => onChange((d) => ({ ...d, insurance_details: { ...(d.insurance_details || {}), ...patch } }));
  const setTp = (patch: Partial<ThirdPartyInfo>) => onChange((d) => ({ ...d, third_party_info: { ...(d.third_party_info || {}), ...patch } }));

  return (
    <section className="card intake-panel">
      <div className="intake-panel-head">
        <h2>Parties &amp; claim</h2>
        <span className="intake-muted">Optional — anything left blank can be completed later in the case file.</span>
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
          <label className="checkbox-label">
            <input type="checkbox" checked={Boolean(police.reported_to_police)} onChange={(e) => setPolice({ reported_to_police: e.target.checked })} /> Reported to police
          </label>
          <div className="intake-field-grid single">
            <label>
              Police station
              <input type="text" placeholder="e.g. Sengkang NPC" value={police.police_station ?? ""} onChange={(e) => setPolice({ police_station: e.target.value })} />
            </label>
            <label>
              Report number
              <input type="text" placeholder="e.g. POL-2026-9941" value={police.report_number ?? ""} onChange={(e) => setPolice({ report_number: e.target.value })} />
            </label>
            <label>
              Officer
              <input type="text" value={police.officer_name ?? ""} onChange={(e) => setPolice({ officer_name: e.target.value })} />
            </label>
          </div>
          <label className="intake-field-wide">
            Immediate actions taken
            <textarea rows={2} value={data.immediate_actions ?? ""} onChange={(e) => onChange((d) => ({ ...d, immediate_actions: e.target.value }))} />
          </label>
        </div>

        <div className="intake-subcard">
          <h3>Insurance</h3>
          <div className="intake-field-grid single">
            <label>
              Insurer
              <input type="text" placeholder="e.g. NTUC Income" value={insurance.insurer_name ?? ""} onChange={(e) => setInsurance({ insurer_name: e.target.value })} />
            </label>
            <label>
              Claim number
              <input type="text" value={insurance.claim_number ?? ""} onChange={(e) => setInsurance({ claim_number: e.target.value })} />
            </label>
            <label>
              Claim type
              <select value={insurance.claim_type ?? ""} onChange={(e) => setInsurance({ claim_type: e.target.value || null })}>
                <option value="">Select…</option>
                {CLAIM_TYPE_OPTIONS.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <h3 style={{ marginTop: 18 }}>Third-party vehicle</h3>
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
          {finalizing ? "Creating case & PDF…" : "Create case & open dossier"}
        </button>
      </div>
    </section>
  );
}
