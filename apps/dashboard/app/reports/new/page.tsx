"use client";

import { useState } from "react";
import { createReportAction, analyzePhotosAction } from "./actions";
import { CATEGORY_OPTIONS, ACCIDENT_TYPE_OPTIONS, type PhotoAnalysisDraft } from "@/lib/api";

type PersonRow = { id: number; name: string; role: string; department: string; contact: string };
type WitnessRow = { id: number; name: string; contact: string; statement: string };

let nextRowId = 0;

export default function NewReportPage() {
  const [people, setPeople] = useState<PersonRow[]>([]);
  const [witnesses, setWitnesses] = useState<WitnessRow[]>([]);

  const [photos, setPhotos] = useState<File[]>([]);
  const [photoPreviews, setPhotoPreviews] = useState<string[]>([]);
  const [tempPhotoPaths, setTempPhotoPaths] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [aiApplied, setAiApplied] = useState(false);
  // The AI draft from the upload, held back until the reporter explicitly
  // asks to apply it -- uploading (so photos are never silently dropped)
  // and applying the AI's field suggestions are deliberately separate
  // actions now. Previously photos only got attached to the saved report if
  // the reporter happened to click "Analyze with AI" -- anyone who just
  // wanted to fill the form manually lost their photos with no warning.
  const [pendingDraft, setPendingDraft] = useState<PhotoAnalysisDraft | null>(null);

  const [description, setDescription] = useState("");
  const [category, setCategory] = useState<Set<string>>(new Set(["Vehicle Collision or Damage"]));
  const [accidentType, setAccidentType] = useState("");
  const [severityLevel, setSeverityLevel] = useState("Minor");
  const [damagedParts, setDamagedParts] = useState("");

  async function handlePhotoChange(e: React.ChangeEvent<HTMLInputElement>) {
    photoPreviews.forEach((url) => URL.revokeObjectURL(url));
    const files = Array.from(e.target.files || []);
    setPhotos(files);
    setPhotoPreviews(files.map((f) => URL.createObjectURL(f)));
    setTempPhotoPaths([]);
    setPendingDraft(null);
    setAiApplied(false);
    setAnalyzeError(null);

    if (files.length === 0) return;

    setUploading(true);
    const fd = new FormData();
    fd.set("description", description);
    for (const p of files) fd.append("photos", p);
    const result = await analyzePhotosAction(fd);
    setUploading(false);

    if ("error" in result) {
      setAnalyzeError(result.error);
      return;
    }
    // Photos are attached the moment they're uploaded, regardless of
    // whether the reporter ever applies the AI's suggestions below.
    setTempPhotoPaths(result.temp_photo_paths);
    setPendingDraft(result.draft);
  }

  function toggleCategory(c: string) {
    setCategory((prev) => {
      const next = new Set(prev);
      if (next.has(c)) next.delete(c);
      else next.add(c);
      return next;
    });
  }

  function applyAiSuggestions() {
    if (!pendingDraft) return;
    if (pendingDraft.accident_type) setAccidentType(pendingDraft.accident_type);
    if (pendingDraft.severity_level) setSeverityLevel(pendingDraft.severity_level);
    if (pendingDraft.damaged_parts && pendingDraft.damaged_parts.length) setDamagedParts(pendingDraft.damaged_parts.join(", "));
    if (pendingDraft.category && pendingDraft.category.length) setCategory(new Set(pendingDraft.category));
    if (pendingDraft.description) setDescription(pendingDraft.description);
    setAiApplied(true);
  }

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

      {/* Photo Evidence & AI Analysis */}
      <section className="card">
        <h2>Photo Evidence &amp; Description</h2>
        <p className="form-hint">
          Upload photos of the incident and describe what happened. AI can analyze the photos and
          description to suggest the accident type, severity, damaged parts, and category below --
          review and adjust its suggestions before saving.
        </p>

        <label style={{ marginTop: 16 }}>
          Photos
          <input type="file" accept="image/*" multiple onChange={handlePhotoChange} />
        </label>

        {photoPreviews.length > 0 && (
          <div className="photo-preview-grid">
            {photoPreviews.map((src, i) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img key={i} src={src} alt={`Selected photo ${i + 1}`} />
            ))}
          </div>
        )}
        {uploading && (
          <p className="form-hint" style={{ marginTop: 8 }}>Uploading and analyzing photos...</p>
        )}

        <label style={{ marginTop: 16 }}>
          Detailed Description *
          <textarea
            name="description"
            required
            rows={4}
            placeholder="Describe what happened..."
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>

        <button
          type="button"
          className="add-row-button"
          style={{ marginTop: 12 }}
          onClick={applyAiSuggestions}
          disabled={uploading || !pendingDraft}
        >
          🔍 Apply AI Suggestions
        </button>

        {analyzeError && (
          <p className="form-hint" style={{ color: "var(--badge-red-text)", marginTop: 8 }}>
            {analyzeError}
          </p>
        )}
        {aiApplied && !analyzeError && (
          <p className="form-hint" style={{ color: "var(--badge-green-text)", marginTop: 8 }}>
            AI suggestions applied to Accident Type, Severity, Damaged Parts, and Category below --
            review before saving.
          </p>
        )}

        <input type="hidden" name="temp_photo_paths" value={JSON.stringify(tempPhotoPaths)} />
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
            <select name="accident_type" value={accidentType} onChange={(e) => setAccidentType(e.target.value)}>
              <option value="">Select Mechanism...</option>
              {/* If AI (or a future model) returns a value outside this list, show it
                  anyway instead of silently reverting to the blank option. */}
              {accidentType && !ACCIDENT_TYPE_OPTIONS.includes(accidentType as (typeof ACCIDENT_TYPE_OPTIONS)[number]) && (
                <option value={accidentType}>{accidentType}</option>
              )}
              {ACCIDENT_TYPE_OPTIONS.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </label>
          <label>
            Overall Damage Severity
            <select name="severity_level" value={severityLevel} onChange={(e) => setSeverityLevel(e.target.value)}>
              <option value="Minor">Minor (Cosmetic / Scrape)</option>
              <option value="Moderate">Moderate (Panel Dent / Repaint)</option>
              <option value="Severe">Severe (Structural / Non-drivable)</option>
            </select>
          </label>
        </div>

        <label style={{ marginTop: 12 }}>
          Damaged Parts (Comma Separated)
          <input
            name="damaged_parts"
            type="text"
            placeholder="e.g. Front Bumper, Right Door, Hood, Side Mirror"
            value={damagedParts}
            onChange={(e) => setDamagedParts(e.target.value)}
          />
        </label>

        <fieldset className="category-fieldset" style={{ marginTop: 12 }}>
          <legend>Category</legend>
          {CATEGORY_OPTIONS.map((c) => (
            <label key={c} className="checkbox-label">
              <input
                type="checkbox"
                name={`category_${c}`}
                checked={category.has(c)}
                onChange={() => toggleCategory(c)}
              />{" "}
              {c}
            </label>
          ))}
        </fieldset>
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
