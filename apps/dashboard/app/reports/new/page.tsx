"use client";

import { useState } from "react";
import { createReportAction } from "./actions";
import { CATEGORY_OPTIONS, ACCIDENT_TYPE_OPTIONS } from "@/lib/api";

type PersonRow = { id: number; name: string; role: string; department: string; contact: string };
type WitnessRow = { id: number; name: string; contact: string; statement: string };

let nextRowId = 0;

export default function NewReportPage() {
  const [people, setPeople] = useState<PersonRow[]>([]);
  const [witnesses, setWitnesses] = useState<WitnessRow[]>([]);

  return (
    <form action={createReportAction} className="new-report-form">
      <h1>File an Incident Report</h1>
      <p className="form-hint">
        Use this form to manually file or record an incident -- vehicle accident, security, or
        otherwise -- into the Carlink system.
      </p>

      {/* Reporter Info */}
      <section className="card">
        <h2>Reporter Information</h2>
        <div className="field-grid">
          <label>
            Reporter Name
            <input name="reporter_name" type="text" placeholder="Full name" />
          </label>
          <label>
            Reporter Role / Position
            <input name="reporter_role" type="text" placeholder="e.g. Site Supervisor" />
          </label>
        </div>
      </section>

      {/* Vehicle Info */}
      <section className="card">
        <h2>Vehicle Information</h2>
        <p className="form-hint">Only fill this in if the incident involves a vehicle.</p>
        <div className="field-grid">
          <label>
            Vehicle Plate Number
            <input name="plate_number" type="text" placeholder="Plate number" />
          </label>
          <label>
            Vehicle Make &amp; Model
            <input name="make_model" type="text" placeholder="Make and model" />
          </label>
        </div>
      </section>

      {/* Incident Details & Classification */}
      <section className="card">
        <h2>Incident Details &amp; Damage Classification</h2>
        <div className="field-grid">
          <label>
            Incident Date &amp; Time
            <input name="incident_datetime" type="text" placeholder="e.g. 2026-07-26 14:20" />
          </label>
          <label>
            Location
            <input name="location" type="text" placeholder="e.g. Workshop Bay 2 / Federal Highway" />
          </label>
          <label>
            Accident Type
            <select name="accident_type">
              <option value="">Select Mechanism...</option>
              {ACCIDENT_TYPE_OPTIONS.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </label>
          <label>
            Overall Damage Severity
            <select name="severity_level">
              <option value="Minor">Minor (Cosmetic / Scrape)</option>
              <option value="Moderate">Moderate (Panel Dent / Repaint)</option>
              <option value="Severe">Severe (Structural / Non-drivable)</option>
            </select>
          </label>
        </div>

        <label style={{ marginTop: 12 }}>
          Damaged Parts (Comma Separated)
          <input name="damaged_parts" type="text" placeholder="e.g. Front Bumper, Right Door, Hood, Side Mirror" />
        </label>

        <fieldset className="category-fieldset" style={{ marginTop: 12 }}>
          <legend>Category</legend>
          {CATEGORY_OPTIONS.map((c) => (
            <label key={c} className="checkbox-label">
              <input type="checkbox" name={`category_${c}`} defaultChecked={c === "Vehicle Collision or Damage"} /> {c}
            </label>
          ))}
        </fieldset>

        <label style={{ marginTop: 12 }}>
          Detailed Description *
          <textarea name="description" required rows={4} placeholder="Describe what happened..." />
        </label>
      </section>

      {/* People Involved */}
      <section className="card">
        <h2>People Involved</h2>
        {people.map((p) => (
          <div key={p.id} className="repeatable-row">
            <input name="person_name" placeholder="Name" defaultValue={p.name} />
            <input name="person_role" placeholder="Role (e.g. Driver)" defaultValue={p.role} />
            <input name="person_department" placeholder="Department / Company" defaultValue={p.department} />
            <input name="person_contact" placeholder="Contact Number" defaultValue={p.contact} />
            <button
              type="button"
              className="row-remove"
              onClick={() => setPeople(people.filter((x) => x.id !== p.id))}
            >
              Remove
            </button>
          </div>
        ))}
        <button
          type="button"
          className="add-row-button"
          onClick={() =>
            setPeople([...people, { id: nextRowId++, name: "", role: "Driver", department: "", contact: "" }])
          }
        >
          + Add Person
        </button>
      </section>

      {/* Witnesses */}
      <section className="card">
        <h2>Witnesses</h2>
        {witnesses.map((w) => (
          <div key={w.id} className="repeatable-row">
            <input name="witness_name" placeholder="Name" defaultValue={w.name} />
            <input name="witness_contact" placeholder="Contact" defaultValue={w.contact} />
            <input name="witness_statement" placeholder="Statement" defaultValue={w.statement} />
            <button
              type="button"
              className="row-remove"
              onClick={() => setWitnesses(witnesses.filter((x) => x.id !== w.id))}
            >
              Remove
            </button>
          </div>
        ))}
        <button
          type="button"
          className="add-row-button"
          onClick={() => setWitnesses([...witnesses, { id: nextRowId++, name: "", contact: "", statement: "" }])}
        >
          + Add Witness
        </button>
      </section>

      {/* Response & Police */}
      <section className="card">
        <h2>Response &amp; Authority Details</h2>
        <label>
          Immediate Actions Taken
          <textarea name="immediate_actions" rows={2} placeholder="e.g. Area secured, photos taken, vehicle moved" />
        </label>
        <label className="checkbox-label" style={{ marginTop: 10 }}>
          <input type="checkbox" name="reported_to_authorities" /> Reported to Police / Authorities
        </label>
        <label>
          Police Station / Report Reference Number
          <input name="authority_reference" type="text" placeholder="e.g. POL-2026-9941" />
        </label>
      </section>

      <button type="submit" className="submit-button">
        Save Report &amp; Generate PDF
      </button>
    </form>
  );
}
