"use client";

import { useRef, useState, useTransition } from "react";
import { unstable_rethrow } from "next/navigation";
import { AlertTriangle, Paperclip, Plus } from "lucide-react";
import { attachPhotosAction } from "./actions";
import { fileUrl, type ReportData } from "@/lib/api";

type PersonRow = { id: number; name: string; role: string; contact: string };
type WitnessRow = { id: number; name: string; contact: string; statement: string };

let nextRowId = 0;

type ReportFormProps = {
  /** The case as saved, to pre-fill. */
  initialData: ReportData;
  /** Already-saved photos -- shown read-only; new uploads are added
   * alongside these, never replacing them (the backend's PUT /reports/{id}
   * appends rather than overwrites). */
  existingPhotoUrls?: string[];
  /** 400px copies of existingPhotoUrls, same order (the API's
   * photo_thumb_urls), shown instead of the multi-MB originals. */
  existingPhotoThumbUrls?: string[];
  reportId: string;
  /** Saves the form. A refusal it answers with ({error}) is shown above the
   * save button, with the form as it was. */
  action: (formData: FormData) => void | Promise<{ error: string } | void>;
  submitLabel: string;
};

/** The Case Record edit form: what the report never prints (description,
 * location, police, insurer, people, the other party) plus adding photos.
 * The printed facts are edited where they print -- the Cover page tab and
 * Damage detection / Annex A -- so they are not here (form-payload.ts says
 * why the form must not send them). */
export function ReportForm({ initialData, existingPhotoUrls = [], existingPhotoThumbUrls = [], reportId, action, submitLabel }: ReportFormProps) {
  const [people, setPeople] = useState<PersonRow[]>(
    () => initialData.people_involved?.map((p) => ({ id: nextRowId++, name: p.name, role: p.role, contact: p.contact || "" })) ?? []
  );
  const [witnesses, setWitnesses] = useState<WitnessRow[]>(
    () => initialData.witnesses?.map((w) => ({ id: nextRowId++, name: w.name, contact: w.contact || "", statement: w.statement || "" })) ?? []
  );

  // Picked photos go to the API's temp dir as soon as they are picked
  // (POST /reports/{id}/upload-photos) and come back as temp paths sent on
  // save. No AI runs here: detection on added photos comes with a later
  // release (Phase 2), on the case page.
  const [photos, setPhotos] = useState<File[]>([]);
  const [photoPreviews, setPhotoPreviews] = useState<string[]>([]);
  const [tempPhotoPaths, setTempPhotoPaths] = useState<string[]>([]);
  const [attaching, setAttaching] = useState(false);
  const [attachError, setAttachError] = useState<string | null>(null);
  // Bumped on every new pick of photos. An attach that finishes after the
  // photos it was sent were replaced is dropped: its temp paths are for the
  // old photos. (The picker is also disabled while one runs.)
  const pickRef = useRef(0);
  const [saving, startSaving] = useTransition();
  const [saveError, setSaveError] = useState<string | null>(null);

  async function attach(files: File[]) {
    if (files.length === 0) return;
    const pick = pickRef.current;
    setAttaching(true);
    setAttachError(null);
    const fd = new FormData();
    for (const p of files) fd.append("photos", p);
    let result: Awaited<ReturnType<typeof attachPhotosAction>>;
    try {
      result = await attachPhotosAction(reportId, fd);
    } catch (err) {
      // Next's redirects (signed out) go through; no answer at all is an error.
      unstable_rethrow(err);
      result = { error: "Couldn't reach Carlink just now. Check your connection and try again.", code: null };
    }
    // Other photos were picked meanwhile: these paths are for the old ones.
    if (pick !== pickRef.current) return;
    setAttaching(false);
    if ("error" in result) {
      setAttachError(result.error);
      return;
    }
    setTempPhotoPaths(result.temp_photo_paths);
  }

  async function handlePhotoChange(e: React.ChangeEvent<HTMLInputElement>) {
    photoPreviews.forEach((url) => URL.revokeObjectURL(url));
    const files = Array.from(e.target.files || []);
    // Anything still running for the previous pick no longer counts.
    pickRef.current += 1;
    setAttaching(false);
    setPhotos(files);
    setPhotoPreviews(files.map((f) => URL.createObjectURL(f)));
    setTempPhotoPaths([]);
    setAttachError(null);
    await attach(files);
  }

  const attached = tempPhotoPaths.length > 0;
  const newPhotosStatus = attaching
    ? { tone: "busy", label: "Attaching…" }
    : attached
      ? { tone: "ok", label: "Attached · added to the case on save" }
      : { tone: "warn", label: "Not attached yet" };

  /** Saves. Run here rather than as the form's action, so a refusal the
   * save answers with (the case's photo limit, say) is shown with the form
   * as it was: React resets a form's fields after its action finishes.
   * Picked photos that aren't attached would be dropped without a word, so
   * that asks first. */
  function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (saving) return;
    if (photos.length > 0 && !attached) {
      const why = attaching ? "The new photos are still being attached." : "The new photos aren't attached.";
      if (!window.confirm(`${why} Save without them?`)) return;
    }
    const formData = new FormData(e.currentTarget);
    setSaveError(null);
    startSaving(async () => {
      const result = await action(formData);
      if (result && "error" in result) setSaveError(result.error);
    });
  }

  return (
    <form onSubmit={submit} className="new-report-form">
      <h1>Edit case record</h1>
      <p className="form-hint">
        What the report never prints. The cover page facts (vehicle, dates, Our Ref, type of claim, workshop) are edited on the
        case&apos;s Cover page tab, and the damaged parts under Damage detection and Annex A. Photos you add here stay with the
        case; the PDF is made again on save.
      </p>

      {/* Photos */}
      <section className="card">
        <h2>Photos</h2>

        {existingPhotoUrls.length > 0 && (
          <>
            <div className="detail-field-label" style={{ marginTop: 16 }}>
              Current photos ({existingPhotoUrls.length})
            </div>
            <div className="photo-preview-grid">
              {existingPhotoUrls.map((url, i) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img key={url} src={fileUrl(existingPhotoThumbUrls[i] || url)} alt={`Photo ${i + 1}`} loading="lazy" decoding="async" />
              ))}
            </div>
          </>
        )}

        <label style={{ marginTop: 16 }}>
          {existingPhotoUrls.length > 0 ? "Add more photos" : "Photos"}
          {/* Not while photos are being attached: a new pick then would get
              the old pick's result. */}
          <input type="file" accept="image/*" multiple onChange={handlePhotoChange} disabled={attaching} />
        </label>

        {photoPreviews.length > 0 && (
          <>
            <div className="detail-field-label ai-attach-label">
              New photos ({photoPreviews.length})
              <span className={`ai-attach-chip ${newPhotosStatus.tone}`}>{newPhotosStatus.label}</span>
            </div>
            <div className={`photo-preview-grid${attached ? "" : " ai-attach-pending"}`}>
              {photoPreviews.map((src, i) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img key={i} src={src} alt={`Selected photo ${i + 1}`} />
              ))}
            </div>
          </>
        )}
        {attached && (
          <p className="form-hint ai-attach-note">
            <Paperclip style={{ width: 13, height: 13, flexShrink: 0, verticalAlign: "-2px" }} /> These photos are added to the
            case when you save. Damage detection on them is not run from here.
          </p>
        )}
        {attachError && (
          <div className="ai-attach-row">
            <button
              type="button"
              className="add-row-button"
              style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
              onClick={() => void attach(photos)}
              disabled={attaching}
            >
              <Paperclip style={{ width: 14, height: 14 }} /> {attaching ? "Attaching…" : "Try attaching again"}
            </button>
            <span className="form-hint" style={{ color: "var(--badge-red-text)", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: 6 }}>
              <AlertTriangle style={{ width: 14, height: 14, flexShrink: 0 }} /> Couldn&apos;t attach the photos: {attachError}
            </span>
          </div>
        )}

        <input type="hidden" name="temp_photo_paths" value={JSON.stringify(tempPhotoPaths)} />
      </section>

      {/* What happened, where */}
      <section className="card">
        <h2>Incident</h2>
        <label>
          Description <span className="form-hint">(shown on the case page, not printed)</span>
          <textarea name="description" rows={4} placeholder="What happened" defaultValue={initialData.description ?? ""} />
        </label>
        <label style={{ marginTop: 12 }}>
          Accident location
          <input name="location" type="text" placeholder="e.g. Ubi Road 1" defaultValue={initialData.location ?? ""} />
        </label>
      </section>

      {/* Police */}
      <section className="card">
        <h2>Police</h2>
        <label className="checkbox-label">
          <input type="checkbox" name="reported_to_authorities" defaultChecked={initialData.reported_to_authorities ?? false} /> Reported to police
        </label>
        <div className="field-grid" style={{ marginTop: 12 }}>
          <label>
            Station
            <input name="police_station" type="text" placeholder="e.g. Sengkang NPC" defaultValue={initialData.police_report?.police_station ?? ""} />
          </label>
          <label>
            Report no.
            <input
              name="report_number"
              type="text"
              defaultValue={initialData.police_report?.report_number ?? initialData.authority_reference ?? ""}
            />
          </label>
          <label>
            Officer
            <input name="officer_name" type="text" defaultValue={initialData.police_report?.officer_name ?? ""} />
          </label>
        </div>
      </section>

      {/* Insurer */}
      <section className="card">
        <h2>Insurer</h2>
        <p className="form-hint">Type of claim and the workshop are on the Cover page tab.</p>
        <div className="field-grid">
          <label>
            Insurer
            <input name="insurer_name" type="text" placeholder="e.g. NTUC Income" defaultValue={initialData.insurance_details?.insurer_name ?? ""} />
          </label>
          <label>
            Claim no.
            <input name="claim_number" type="text" defaultValue={initialData.insurance_details?.claim_number ?? ""} />
          </label>
        </div>
      </section>

      {/* People Involved */}
      <section className="card">
        <h2>People involved</h2>
        {people.map((p) => (
          <div key={p.id} className="repeatable-row">
            <input name="person_name" placeholder="Name" defaultValue={p.name} />
            <input name="person_role" placeholder="Role (e.g. Driver)" defaultValue={p.role} />
            <input name="person_contact" placeholder="Contact" defaultValue={p.contact} />
            <button type="button" className="row-remove" onClick={() => setPeople(people.filter((x) => x.id !== p.id))}>
              Remove
            </button>
          </div>
        ))}
        <button
          type="button"
          className="add-row-button"
          style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
          onClick={() => setPeople([...people, { id: nextRowId++, name: "", role: "", contact: "" }])}
        >
          <Plus style={{ width: 14, height: 14 }} /> Add person
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
            <button type="button" className="row-remove" onClick={() => setWitnesses(witnesses.filter((x) => x.id !== w.id))}>
              Remove
            </button>
          </div>
        ))}
        <button
          type="button"
          className="add-row-button"
          style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
          onClick={() => setWitnesses([...witnesses, { id: nextRowId++, name: "", contact: "", statement: "" }])}
        >
          <Plus style={{ width: 14, height: 14 }} /> Add witness
        </button>
      </section>

      {/* Other party */}
      <section className="card">
        <h2>Other party</h2>
        <div className="field-grid">
          <label>
            Plate
            <input name="tp_plate_number" type="text" defaultValue={initialData.third_party_info?.plate_number ?? ""} />
          </label>
          <label>
            Make / model
            <input name="tp_make_model" type="text" defaultValue={initialData.third_party_info?.make_model ?? ""} />
          </label>
          <label>
            Their insurer
            <input name="tp_insurer_name" type="text" defaultValue={initialData.third_party_info?.insurer_name ?? ""} />
          </label>
        </div>
      </section>

      {saveError && (
        <div className="setup-error" role="alert" style={{ marginTop: 16, marginBottom: 0 }}>
          Not saved: {saveError}
        </div>
      )}
      <button type="submit" className="submit-button" disabled={saving}>
        {saving ? "Saving…" : submitLabel}
      </button>
    </form>
  );
}
