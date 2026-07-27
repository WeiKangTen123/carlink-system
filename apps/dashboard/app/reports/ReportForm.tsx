"use client";

import { useState } from "react";
import { analyzePhotosAction, reanalyzeExistingPhotosAction } from "./actions";
import {
  CATEGORY_OPTIONS,
  ACCIDENT_TYPE_OPTIONS,
  WEATHER_OPTIONS,
  ROAD_OPTIONS,
  TRAFFIC_OPTIONS,
  CLAIM_TYPE_OPTIONS,
  fileUrl,
  type PhotoAnalysisDraft,
  type ReportData,
} from "@/lib/api";

type PersonRow = { id: number; name: string; role: string; department: string; contact: string };
type WitnessRow = { id: number; name: string; contact: string; statement: string };

let nextRowId = 0;

type ReportFormProps = {
  mode: "create" | "edit";
  /** Existing report data to pre-fill when editing. Omitted for create. */
  initialData?: ReportData;
  /** Already-saved photos (edit mode only) -- shown read-only; new uploads
   * are added alongside these, never replacing them (see the backend's
   * PUT /reports/{id}, which appends rather than overwrites). */
  existingPhotoUrls?: string[];
  /** Needed so "Re-analyze with AI" can look the report's saved photos back
   * up server-side when the reporter hasn't picked any new files. Omitted
   * for create, where there's no report yet to look up. */
  reportId?: string;
  action: (formData: FormData) => void | Promise<void>;
  submitLabel: string;
};

export function ReportForm({ mode, initialData, existingPhotoUrls = [], reportId, action, submitLabel }: ReportFormProps) {
  const [people, setPeople] = useState<PersonRow[]>(
    () => initialData?.people_involved?.map((p) => ({ id: nextRowId++, name: p.name, role: p.role, department: p.department || "", contact: p.contact || "" })) ?? []
  );
  const [witnesses, setWitnesses] = useState<WitnessRow[]>(
    () => initialData?.witnesses?.map((w) => ({ id: nextRowId++, name: w.name, contact: w.contact || "", statement: w.statement || "" })) ?? []
  );

  const [photos, setPhotos] = useState<File[]>([]);
  const [photoPreviews, setPhotoPreviews] = useState<string[]>([]);
  const [tempPhotoPaths, setTempPhotoPaths] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [aiApplied, setAiApplied] = useState(false);

  const [description, setDescription] = useState(initialData?.description ?? "");
  const [category, setCategory] = useState<Set<string>>(
    new Set(initialData?.category ?? (mode === "create" ? ["Vehicle Collision or Damage"] : []))
  );
  const [accidentType, setAccidentType] = useState(initialData?.accident_type ?? "");
  const [severityLevel, setSeverityLevel] = useState(initialData?.severity_level || "Minor");
  const [damagedParts, setDamagedParts] = useState(initialData?.damaged_parts?.join(", ") ?? "");

  function applyDraft(draft: PhotoAnalysisDraft, currentDescription: string) {
    if (draft.accident_type) setAccidentType(draft.accident_type);
    if (draft.severity_level) setSeverityLevel(draft.severity_level);
    if (draft.damaged_parts && draft.damaged_parts.length) setDamagedParts(draft.damaged_parts.join(", "));
    if (draft.category && draft.category.length) setCategory(new Set(draft.category));
    // Never overwrites a description the reporter already wrote -- only
    // fills it in when they uploaded photos before typing anything.
    if (draft.description && !currentDescription.trim()) setDescription(draft.description);
    setAiApplied(true);
  }

  // Runs the AI analysis against whatever photos + description text are
  // current *at the time it's called* and immediately fills the structured
  // fields below -- no separate "apply" click. That separate click was the
  // actual bug: the AI's suggestions were computed correctly but sat unused
  // unless the reporter noticed and pressed a second button. Re-running this
  // (via the "Re-analyze" button) after finishing the description text also
  // fixes the AI seeing an empty/stale description when it only ran once, at
  // the moment photos were first selected.
  async function runAnalysis(currentPhotos: File[], currentDescription: string) {
    if (currentPhotos.length === 0) return;
    setUploading(true);
    setAnalyzeError(null);
    setAiApplied(false);
    const fd = new FormData();
    fd.set("description", currentDescription);
    for (const p of currentPhotos) fd.append("photos", p);
    const result = await analyzePhotosAction(fd);
    setUploading(false);

    if ("error" in result) {
      setAnalyzeError(result.error);
      return;
    }
    // Photos are attached the moment analysis succeeds, regardless of
    // whether any structured field ends up changing below.
    setTempPhotoPaths(result.temp_photo_paths);
    applyDraft(result.draft, currentDescription);
  }

  // Lets "Re-analyze with AI" work in edit mode even when the reporter hasn't
  // picked any new files in this session. Runs entirely server-side (see
  // reanalyzeExistingPhotosAction) since re-fetching the API's /files/ route
  // straight from the browser hits a CORS error there. Deliberately doesn't
  // touch tempPhotoPaths/setTempPhotoPaths -- these photos already have
  // permanent photo_XX.jpg paths attached to the report, so there's nothing
  // new to attach on save.
  async function reanalyzeExistingPhotos() {
    if (!reportId) return;
    setUploading(true);
    setAnalyzeError(null);
    setAiApplied(false);
    const result = await reanalyzeExistingPhotosAction(reportId, description);
    setUploading(false);

    if ("error" in result) {
      setAnalyzeError(result.error);
      return;
    }
    applyDraft(result.draft, description);
  }

  async function handlePhotoChange(e: React.ChangeEvent<HTMLInputElement>) {
    photoPreviews.forEach((url) => URL.revokeObjectURL(url));
    const files = Array.from(e.target.files || []);
    setPhotos(files);
    setPhotoPreviews(files.map((f) => URL.createObjectURL(f)));
    setTempPhotoPaths([]);
    setAiApplied(false);
    setAnalyzeError(null);
    await runAnalysis(files, description);
  }

  function toggleCategory(c: string) {
    setCategory((prev) => {
      const next = new Set(prev);
      if (next.has(c)) next.delete(c);
      else next.add(c);
      return next;
    });
  }

  return (
    <form action={action} className="new-report-form">
      <h1>{mode === "edit" ? "Edit Incident Report" : "File an Incident Report"}</h1>
      <p className="form-hint">
        {mode === "edit"
          ? "Update any field below and save -- the PDF is regenerated automatically. Existing photos stay attached; anything you upload here is added alongside them."
          : "Use this form to manually file or record an incident -- vehicle accident, security, or otherwise -- into the Carlink system."}
      </p>

      {/* Reporter Info */}
      <section className="card">
        <h2>Reporter Information</h2>
        <div className="field-grid">
          <label>
            Reporter Name
            <input name="reporter_name" type="text" placeholder="Full name" defaultValue={initialData?.reporter_name ?? ""} />
          </label>
          <label>
            Reporter Role / Position
            <input name="reporter_role" type="text" placeholder="e.g. Site Supervisor" defaultValue={initialData?.reporter_role ?? ""} />
          </label>
          <label>
            Reporter Contact Number
            <input name="reporter_contact" type="text" placeholder="e.g. +65 9123 4567" defaultValue={initialData?.reporter_contact ?? ""} />
          </label>
          <label>
            Reporter Department / Company
            <input name="reporter_department" type="text" placeholder="e.g. Fleet Operations" defaultValue={initialData?.reporter_department ?? ""} />
          </label>
        </div>
      </section>

      {/* Photo Evidence & AI Analysis */}
      <section className="card">
        <h2>Photo Evidence &amp; Description</h2>
        <p className="form-hint">
          Upload photos of the incident and describe what happened. AI automatically analyzes the
          photos and description to fill in the accident type, severity, damaged parts, and
          category below -- review and adjust before saving.
        </p>

        {existingPhotoUrls.length > 0 && (
          <>
            <div className="detail-field-label" style={{ marginTop: 16 }}>
              Current Photos ({existingPhotoUrls.length})
            </div>
            <div className="photo-preview-grid">
              {existingPhotoUrls.map((url, i) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img key={url} src={fileUrl(url)} alt={`Existing photo ${i + 1}`} />
              ))}
            </div>
          </>
        )}

        <label style={{ marginTop: 16 }}>
          {existingPhotoUrls.length > 0 ? "Add More Photos" : "Photos"}
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
          onClick={() => (photos.length > 0 ? runAnalysis(photos, description) : reanalyzeExistingPhotos())}
          disabled={uploading || (photos.length === 0 && (existingPhotoUrls.length === 0 || !reportId))}
          title={
            photos.length > 0
              ? "Re-run AI analysis using the description as currently written -- use this after editing the description following a photo upload"
              : "Re-run AI analysis against this report's already-saved photos"
          }
        >
          {uploading ? "Analyzing..." : "🔄 Re-analyze with AI"}
        </button>

        {analyzeError && (
          <p className="form-hint" style={{ color: "var(--badge-red-text)", marginTop: 8, fontWeight: 600 }}>
            ⚠️ AI analysis failed: {analyzeError} -- photos may not be attached; try again before saving.
          </p>
        )}
        {aiApplied && !analyzeError && (
          <p className="form-hint" style={{ color: "var(--badge-green-text)", marginTop: 8 }}>
            AI filled in Accident Type, Severity, Damaged Parts, and Category below -- review before
            saving.
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
            <input name="plate_number" type="text" placeholder="Plate number" defaultValue={initialData?.vehicle_info?.plate_number ?? ""} />
          </label>
          <label>
            Vehicle Make
            <input name="make" type="text" placeholder="e.g. Toyota" defaultValue={initialData?.vehicle_info?.make ?? ""} />
          </label>
          <label>
            Vehicle Model
            <input name="model" type="text" placeholder="e.g. Camry" defaultValue={initialData?.vehicle_info?.model ?? ""} />
          </label>
          <label>
            VIN / Chassis Number
            <input name="vin" type="text" placeholder="Vehicle identification number" defaultValue={initialData?.vehicle_info?.vin ?? ""} />
          </label>
          <label>
            Driver Name
            <input name="driver_name" type="text" placeholder="Who was driving" defaultValue={initialData?.vehicle_info?.driver_name ?? ""} />
          </label>
        </div>
      </section>

      {/* Incident Details & Classification */}
      <section className="card">
        <h2>Incident Details &amp; Damage Classification</h2>
        <div className="field-grid">
          <label>
            Incident Date &amp; Time
            <input name="incident_datetime" type="text" placeholder="e.g. 2026-07-26 14:20" defaultValue={initialData?.incident_datetime ?? ""} />
          </label>
          <label>
            Location
            <input name="location" type="text" placeholder="e.g. Workshop Bay 2 / Federal Highway" defaultValue={initialData?.location ?? ""} />
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
          <label>
            Weather Condition
            <select name="weather_condition" defaultValue={initialData?.weather_condition ?? ""}>
              <option value="">Select...</option>
              {WEATHER_OPTIONS.map((w) => (
                <option key={w} value={w}>{w}</option>
              ))}
            </select>
          </label>
          <label>
            Road Condition
            <select name="road_condition" defaultValue={initialData?.road_condition ?? ""}>
              <option value="">Select...</option>
              {ROAD_OPTIONS.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </label>
          <label>
            Traffic Condition
            <select name="traffic_condition" defaultValue={initialData?.traffic_condition ?? ""}>
              <option value="">Select...</option>
              {TRAFFIC_OPTIONS.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
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
          <textarea name="immediate_actions" rows={2} placeholder="e.g. Area secured, photos taken, vehicle moved" defaultValue={initialData?.immediate_actions ?? ""} />
        </label>
        <label className="checkbox-label" style={{ marginTop: 10 }}>
          <input type="checkbox" name="reported_to_authorities" defaultChecked={initialData?.reported_to_authorities ?? false} /> Reported to Police / Authorities
        </label>
        <div className="field-grid" style={{ marginTop: 12 }}>
          <label>
            Police Station
            <input name="police_station" type="text" placeholder="e.g. Sengkang Neighbourhood Police Centre" defaultValue={initialData?.police_report?.police_station ?? ""} />
          </label>
          <label>
            Reporting Officer Name
            <input name="officer_name" type="text" placeholder="Officer name" defaultValue={initialData?.police_report?.officer_name ?? ""} />
          </label>
          <label>
            Police Report / Reference Number
            <input
              name="report_number"
              type="text"
              placeholder="e.g. POL-2026-9941"
              defaultValue={initialData?.police_report?.report_number ?? initialData?.authority_reference ?? ""}
            />
          </label>
        </div>
      </section>

      {/* Insurance */}
      <section className="card">
        <h2>Insurance &amp; Claim Details</h2>
        <p className="form-hint">Only fill this in once a claim has actually been raised.</p>
        <div className="field-grid">
          <label>
            Insurer Name
            <input name="insurer_name" type="text" placeholder="e.g. NTUC Income" defaultValue={initialData?.insurance_details?.insurer_name ?? ""} />
          </label>
          <label>
            Claim Number
            <input name="claim_number" type="text" placeholder="Claim number" defaultValue={initialData?.insurance_details?.claim_number ?? ""} />
          </label>
          <label>
            Claim Type
            <select name="claim_type" defaultValue={initialData?.insurance_details?.claim_type ?? ""}>
              <option value="">Select...</option>
              {CLAIM_TYPE_OPTIONS.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </label>
        </div>
      </section>

      {/* Recommendations */}
      <section className="card">
        <h2>Assessment &amp; Recommendations</h2>
        <p className="form-hint">The surveyor's own recommendation, not an AI-generated one.</p>
        <label>
          Repair Recommendation
          <textarea name="repair_recommendation" rows={2} placeholder="e.g. Replace front bumper, respray affected panel" defaultValue={initialData?.recommendations?.repair_recommendation ?? ""} />
        </label>
        <label style={{ marginTop: 10 }}>
          Inspection Recommendation
          <textarea name="inspection_recommendation" rows={2} placeholder="e.g. Full underbody inspection recommended before drivable" defaultValue={initialData?.recommendations?.inspection_recommendation ?? ""} />
        </label>
      </section>

      <button type="submit" className="submit-button">
        {submitLabel}
      </button>
    </form>
  );
}
