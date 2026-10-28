"use client";

import { useRef, useState, useTransition } from "react";
import { unstable_rethrow } from "next/navigation";
import { Sparkles, AlertTriangle, CheckCircle2, Paperclip, Plus } from "lucide-react";
import { analyzePhotosAction, attachPhotosAction, reanalyzeExistingPhotosAction, type AiActionError } from "./actions";
import { AiFixLink } from "@/components/AiFixLink";
import { useCurrentUser } from "@/components/CurrentUser";
import {
  aiOffMessage,
  isAiOffCode,
  CATEGORY_OPTIONS,
  ACCIDENT_TYPE_OPTIONS,
  WEATHER_OPTIONS,
  ROAD_OPTIONS,
  TRAFFIC_OPTIONS,
  CLAIM_TYPE_OPTIONS,
  fileUrl,
  type PhotoAnalysisDraft,
  type ReportData,
  type AiOffCode,
  type DamageSummaryItem,
} from "@/lib/api";

/** Why the last Analyze / Re-analyze didn't fill the form. `aiOff`: the AI
 * was refused for this person (release P4) -- nothing was analyzed, and the
 * photos it was sent weren't attached (the API checks before saving any);
 * "Attach photos without AI" attaches them instead. `limit`: a 409 or 429
 * the API words itself (one AI request at a time, the case's photo limit),
 * shown as it is rather than as the AI failing. */
type AnalyzeError = { text: string; aiOff: AiOffCode | null; limit: boolean; newPhotos: boolean };

function toAnalyzeError(result: AiActionError, newPhotos: boolean): AnalyzeError {
  if (isAiOffCode(result.code)) return { text: aiOffMessage(result.code, result.error), aiOff: result.code, limit: false, newPhotos };
  const limit = result.status === 409 || result.status === 429;
  return { text: result.error, aiOff: null, limit, newPhotos };
}

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
  /** 400px copies of existingPhotoUrls, same order (the API's
   * photo_thumb_urls), shown instead of the multi-MB originals. */
  existingPhotoThumbUrls?: string[];
  /** Needed so "Re-analyze with AI" can look the report's saved photos back
   * up server-side when the reporter hasn't picked any new files. Omitted
   * for create, where there's no report yet to look up. */
  reportId?: string;
  /** Saves the form. A refusal it answers with ({error}) is shown above the
   * save button, with the form as it was. */
  action: (formData: FormData) => void | Promise<{ error: string } | void>;
  submitLabel: string;
};

export function ReportForm({ mode, initialData, existingPhotoUrls = [], existingPhotoThumbUrls = [], reportId, action, submitLabel }: ReportFormProps) {
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
  const [analyzeError, setAnalyzeError] = useState<AnalyzeError | null>(null);
  const [aiApplied, setAiApplied] = useState(false);
  // "Attach photos without AI" (release P4), for people whose AI is off: the
  // picked photos go to the case on save, with no analysis. Set once the
  // current pick was attached that way rather than by an analysis.
  const [attaching, setAttaching] = useState(false);
  const [attachError, setAttachError] = useState<string | null>(null);
  const [attachedWithoutAi, setAttachedWithoutAi] = useState(false);
  const user = useCurrentUser();
  // Bumped on every new pick of photos. An analysis or attach that finishes
  // after the photos it was sent were replaced is dropped: its temp paths
  // are for the old photos. (The picker is also disabled while either runs.)
  const pickRef = useRef(0);
  const [saving, startSaving] = useTransition();
  const [saveError, setSaveError] = useState<string | null>(null);

  const [description, setDescription] = useState(initialData?.description ?? "");
  const [category, setCategory] = useState<Set<string>>(
    new Set(initialData?.category ?? (mode === "create" ? ["Vehicle Collision or Damage"] : []))
  );
  const [accidentType, setAccidentType] = useState(initialData?.accident_type ?? "");
  // Empty = not assessed. It used to default to "Minor", so saving an edit
  // on an unrated report silently recorded a Minor assessment.
  const [severityLevel, setSeverityLevel] = useState(initialData?.severity_level || "");
  const [damagedParts, setDamagedParts] = useState(initialData?.damaged_parts?.join(", ") ?? "");
  // The AI's per-part damage_type/photo_reference/ai_confidence -- kept
  // separately from the plain damagedParts text above because that field
  // stays freely editable, and this richer data only makes sense for
  // exactly the parts the AI actually identified from a real photo (see
  // buildFinalDamageSummary, which reconciles the two on submit rather than
  // just discarding this the way the form used to).
  const [damageSummary, setDamageSummary] = useState<DamageSummaryItem[]>(initialData?.damage_summary ?? []);
  const [bodyType, setBodyType] = useState(initialData?.vehicle_info?.body_type ?? "");

  function applyDraft(draft: PhotoAnalysisDraft, currentDescription: string) {
    if (draft.accident_type) setAccidentType(draft.accident_type);
    if (draft.severity_level) setSeverityLevel(draft.severity_level);
    if (draft.damaged_parts && draft.damaged_parts.length) setDamagedParts(draft.damaged_parts.join(", "));
    if (draft.damage_summary && draft.damage_summary.length) setDamageSummary(draft.damage_summary);
    if (draft.category && draft.category.length) setCategory(new Set(draft.category));
    if (draft.vehicle_info?.body_type) setBodyType(draft.vehicle_info.body_type);
    // Never overwrites a description the reporter already wrote -- only
    // fills it in when they uploaded photos before typing anything.
    if (draft.description && !currentDescription.trim()) setDescription(draft.description);
    setAiApplied(true);
  }

  // Reconciles the freely-typed "Damaged Parts" text against the richer
  // AI-provided damageSummary at submit time: a part the reporter hasn't
  // touched since AI set it keeps its real photo_reference/ai_confidence/
  // damage_type; a part they typed by hand (or edited after AI ran) gets a
  // plain entry instead of carrying over another part's AI data by mistake.
  function buildFinalDamageSummary(): DamageSummaryItem[] {
    const currentParts = damagedParts.split(",").map((p) => p.trim()).filter(Boolean);
    const byPart = new Map(damageSummary.map((item) => [item.part.toLowerCase(), item]));
    return currentParts.map((part) => {
      const aiItem = byPart.get(part.toLowerCase());
      if (aiItem) return aiItem;
      // No per-part severity for a hand-typed part: the overall rating
      // isn't an assessment of this particular part.
      return { part, severity: null, human_verified: true };
    });
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
    const pick = pickRef.current;
    setUploading(true);
    setAnalyzeError(null);
    setAiApplied(false);
    const fd = new FormData();
    fd.set("description", currentDescription);
    for (const p of currentPhotos) fd.append("photos", p);
    const result = await analyzePhotosAction(fd);
    // Other photos were picked meanwhile: this result is for the old ones.
    if (pick !== pickRef.current) return;
    setUploading(false);

    if ("error" in result) {
      setAnalyzeError(toAnalyzeError(result, true));
      return;
    }
    // Photos are attached the moment analysis succeeds, regardless of
    // whether any structured field ends up changing below. (After "Attach
    // photos without AI", these replace the paths that attached.)
    setTempPhotoPaths(result.temp_photo_paths);
    setAttachedWithoutAi(false);
    setAttachError(null);
    applyDraft(result.draft, currentDescription);
  }

  // "Attach photos without AI": the picked photos go to the API's temp dir
  // with no analysis, and come back as temp paths sent on save exactly like
  // an analysis's (POST /reports/{id}/upload-photos). Needs the report, so
  // edit mode only.
  async function attachWithoutAi() {
    if (!reportId || photos.length === 0) return;
    const pick = pickRef.current;
    setAttaching(true);
    setAttachError(null);
    const fd = new FormData();
    for (const p of photos) fd.append("photos", p);
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
    setAttachedWithoutAi(true);
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
    const pick = pickRef.current;
    setUploading(true);
    setAnalyzeError(null);
    setAiApplied(false);
    const result = await reanalyzeExistingPhotosAction(reportId, description);
    if (pick !== pickRef.current) return;
    setUploading(false);

    if ("error" in result) {
      setAnalyzeError(toAnalyzeError(result, false));
      return;
    }
    applyDraft(result.draft, description);
  }

  async function handlePhotoChange(e: React.ChangeEvent<HTMLInputElement>) {
    photoPreviews.forEach((url) => URL.revokeObjectURL(url));
    const files = Array.from(e.target.files || []);
    // Anything still running for the previous pick no longer counts.
    pickRef.current += 1;
    setUploading(false);
    setAttaching(false);
    setPhotos(files);
    setPhotoPreviews(files.map((f) => URL.createObjectURL(f)));
    setTempPhotoPaths([]);
    setAttachedWithoutAi(false);
    setAttachError(null);
    setAiApplied(false);
    setAnalyzeError(null);
    // AI is off for this person: an analysis would only be refused. They
    // attach the photos without AI instead, or press Re-analyze if they've
    // fixed it meanwhile.
    if (user?.ai_allowed === false) return;
    await runAnalysis(files, description);
  }

  // GET /auth/me, with the page: whether the AI runs for this person. Unknown
  // counts as allowed; the API decides.
  const aiAllowed = user?.ai_allowed !== false;
  // AI is off for this person: GET /auth/me said so, or the last Analyze was
  // refused for it.
  const aiOff = Boolean(analyzeError?.aiOff) || !aiAllowed;
  const attached = tempPhotoPaths.length > 0;
  // Picked photos not attached: offer to attach them without AI when AI is
  // off, or when the analysis failed (which attaches nothing).
  const offerAttach = Boolean(reportId) && photos.length > 0 && !attached && !uploading && (aiOff || analyzeError !== null);
  const newPhotosStatus = uploading
    ? { tone: "busy", label: "Being analyzed…" }
    : attaching
      ? { tone: "busy", label: "Attaching…" }
      : attached
        ? attachedWithoutAi
          ? { tone: "ok", label: "Attached · not analyzed by AI" }
          : { tone: "ok", label: "Attached · analyzed by AI" }
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
      const why = uploading || attaching ? "The new photos are still being attached." : "The new photos aren't attached.";
      if (!window.confirm(`${why} Save without them?`)) return;
    }
    const formData = new FormData(e.currentTarget);
    setSaveError(null);
    startSaving(async () => {
      const result = await action(formData);
      if (result && "error" in result) setSaveError(result.error);
    });
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
    <form onSubmit={submit} className="new-report-form">
      <h1>{mode === "edit" ? "Edit Incident Report" : "New Case Intake (FNOL)"}</h1>
      <p className="form-hint">
        {mode === "edit"
          ? "Update any field below and save -- the PDF is regenerated automatically. Existing photos stay attached; anything you upload here is added alongside them."
          : "Fast-track First Notice of Loss (FNOL) intake docket for Singapore loss adjusters. Drop accident damage photos for Gemini AI vision extraction or enter preliminary survey findings."}
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
          {aiAllowed
            ? "Upload photos of the incident and describe what happened. AI automatically analyzes the photos and description to fill in the accident type, severity, damaged parts, and category below -- review and adjust before saving."
            : "Upload photos of the incident and describe what happened, then fill in the accident type, severity, damaged parts, and category below."}
        </p>

        {existingPhotoUrls.length > 0 && (
          <>
            <div className="detail-field-label" style={{ marginTop: 16 }}>
              Current Photos ({existingPhotoUrls.length})
            </div>
            <div className="photo-preview-grid">
              {existingPhotoUrls.map((url, i) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  key={url}
                  src={fileUrl(existingPhotoThumbUrls[i] || url)}
                  alt={`Existing photo ${i + 1}`}
                  loading="lazy"
                  decoding="async"
                />
              ))}
            </div>
          </>
        )}

        <label style={{ marginTop: 16 }}>
          {existingPhotoUrls.length > 0 ? "Add More Photos" : "Photos"}
          {/* Not while photos are being analyzed or attached: a new pick
              then would get the old pick's result. */}
          <input type="file" accept="image/*" multiple onChange={handlePhotoChange} disabled={uploading || attaching} />
        </label>

        {aiOff && !analyzeError && photos.length === 0 && reportId && (
          <p className="form-hint ai-attach-note">
            AI is off for your account, so photos added here aren&apos;t analyzed: attach them without AI, then fill in
            the fields below by hand. <AiFixLink code={null} />
          </p>
        )}

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
        {uploading && (
          <p className="form-hint" style={{ marginTop: 8 }}>Uploading and analyzing photos...</p>
        )}
        {attached && attachedWithoutAi && (
          <p className="form-hint ai-attach-note">
            <Paperclip style={{ width: 13, height: 13, flexShrink: 0, verticalAlign: "-2px" }} /> These photos are added to
            the case when you save. The AI hasn&apos;t looked at them, so fill in the fields below by hand.
          </p>
        )}
        {offerAttach && (
          <div className="ai-attach-row">
            <button
              type="button"
              className="add-row-button"
              style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
              onClick={() => void attachWithoutAi()}
              disabled={attaching}
            >
              <Paperclip style={{ width: 14, height: 14 }} /> {attaching ? "Attaching…" : "Attach photos without AI"}
            </button>
            <span className="form-hint">
              {aiOff ? "AI is off for you, so they weren't analyzed." : "The AI analysis failed, so they aren't attached."}
            </span>
          </div>
        )}
        {attachError && (
          <p className="form-hint" style={{ color: "var(--badge-red-text)", marginTop: 8, fontWeight: 600, display: "flex", alignItems: "center", gap: 6 }}>
            <AlertTriangle style={{ width: 14, height: 14, flexShrink: 0 }} /> Couldn&apos;t attach the photos: {attachError}
          </p>
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

        {/* Only for people with AI: for the rest it would only be refused,
            and "Attach photos without AI" is their way to add photos. */}
        {aiAllowed && (
          <button
            type="button"
            className="add-row-button"
            style={{ marginTop: 12, display: "inline-flex", alignItems: "center", gap: 6 }}
            onClick={() => (photos.length > 0 ? runAnalysis(photos, description) : reanalyzeExistingPhotos())}
            disabled={uploading || attaching || (photos.length === 0 && (existingPhotoUrls.length === 0 || !reportId))}
            title={
              photos.length > 0
                ? "Re-run AI analysis using the description as currently written -- use this after editing the description following a photo upload"
                : "Re-run AI analysis against this report's already-saved photos"
            }
          >
            <Sparkles style={{ width: 14, height: 14 }} /> {uploading ? "Analyzing..." : "Re-analyze with AI"}
          </button>
        )}

        {analyzeError?.aiOff ? (
          <div className="form-hint ai-off-hint" role="alert">
            <AlertTriangle style={{ width: 14, height: 14, flexShrink: 0, marginTop: 2 }} />
            <span>
              <strong>{analyzeError.text}</strong>{" "}
              {analyzeError.newPhotos && reportId && !attached
                ? "You can attach the new photos without AI (above) and fill in the fields below by hand."
                : "You can still fill in the fields below by hand."}{" "}
              <AiFixLink code={analyzeError.aiOff} />
            </span>
          </div>
        ) : analyzeError ? (
          <p className="form-hint" style={{ color: "var(--badge-red-text)", marginTop: 8, fontWeight: 600, display: "flex", alignItems: "center", gap: 6 }}>
            <AlertTriangle style={{ width: 14, height: 14, flexShrink: 0 }} />{" "}
            {/* A limit (409/429) is in the API's own words: not the AI failing. */}
            {analyzeError.limit ? `Not analyzed: ${analyzeError.text}` : `AI analysis failed: ${analyzeError.text}`}
            {analyzeError.newPhotos && !attached && reportId
              ? " -- the new photos aren't attached; try again, or attach them without AI."
              : analyzeError.limit
                ? ""
                : " -- photos may not be attached; try again before saving."}
          </p>
        ) : null}
        {aiApplied && !analyzeError && (
          <p className="form-hint" style={{ color: "var(--badge-green-text)", marginTop: 8, display: "flex", alignItems: "center", gap: 6 }}>
            <CheckCircle2 style={{ width: 14, height: 14, flexShrink: 0 }} /> AI filled in Accident Type, Severity, Damaged Parts, and Category below -- review before saving.
          </p>
        )}

        <input type="hidden" name="temp_photo_paths" value={JSON.stringify(tempPhotoPaths)} />
        <input type="hidden" name="damage_summary" value={JSON.stringify(buildFinalDamageSummary())} />
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
          <label>
            Body Type / Classification
            <select name="body_type" value={bodyType} onChange={(e) => setBodyType(e.target.value)}>
              <option value="">Auto-Detect from Make/Model...</option>
              <option value="SUV">SUV / Crossover (e.g. Vezel, HR-V, Atto 3)</option>
              <option value="Sedan">Sedan (e.g. Altis, Civic, C-Class)</option>
              <option value="Van">Commercial Van / LGV (e.g. HiAce, NV200)</option>
              <option value="MPV">MPV / People Mover (e.g. Alphard, Sienta)</option>
              <option value="Hatchback">Hatchback (e.g. Jazz/Fit, Swift, Golf)</option>
            </select>
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
              <option value="">Not assessed</option>
              {/* Keep a recorded value outside this list visible rather than
                  showing it as blank (same as Accident Type above). */}
              {severityLevel && !["Minor", "Moderate", "Severe"].includes(severityLevel) && (
                <option value={severityLevel}>{severityLevel}</option>
              )}
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
          style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
          onClick={() =>
            setPeople([...people, { id: nextRowId++, name: "", role: "", department: "", contact: "" }])
          }
        >
          <Plus style={{ width: 14, height: 14 }} /> Add Person
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
          style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
          onClick={() => setWitnesses([...witnesses, { id: nextRowId++, name: "", contact: "", statement: "" }])}
        >
          <Plus style={{ width: 14, height: 14 }} /> Add Witness
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
