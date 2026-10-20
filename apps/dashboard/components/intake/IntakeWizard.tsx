"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Check, Save, X } from "lucide-react";
import type { ReportData, Taxonomy } from "@/lib/api";
import {
  ALLOWED_PHOTO_EXTENSIONS,
  createIntake,
  deleteIntakePhoto,
  finalizeIntake,
  formatBytes,
  getIntake,
  processIntake,
  saveIntakeData,
  updateIntakePhoto,
  uploadIntakePhoto,
  type IntakePhoto,
  type IntakeStage,
  type IntakeState,
  type PhotoKind,
} from "@/lib/intake";
import { PartiesStep } from "./PartiesStep";
import { ReviewStep } from "./ReviewStep";
import { SortStep } from "./SortStep";
import { UploadStep, type UploadItem } from "./UploadStep";

type Step = 1 | 2 | 3 | 4;

const STEPS: { n: Step; label: string }[] = [
  { n: 1, label: "Upload" },
  { n: 2, label: "AI Sorting" },
  { n: 3, label: "Review Facts" },
  { n: 4, label: "Parties & Claim" },
];

// Three at a time keeps a 70-photo case moving without one slow phone
// connection starving the rest; the server does thumbnail work per upload.
const UPLOAD_CONCURRENCY = 3;
const POLL_MS = 2000;
const DEFAULT_MAX_PHOTOS = 150;
const DEFAULT_MAX_BYTES = 25 * 1024 * 1024;
const FALLBACK_SEVERITIES = ["Minor", "Moderate", "Severe"];

const EMPTY_DATA: ReportData = {
  description: "",
  category: ["Vehicle Collision or Damage"],
  people_involved: [],
  witnesses: [],
  reported_to_authorities: false,
};

const isRunning = (stage: IntakeStage | null | undefined) => stage === "sorting" || stage === "reading";

function stepFor(state: IntakeState): Step {
  if (state.stage === "ready") return 3;
  if (state.stage === "uploading" || state.photos.length === 0) return 1;
  return 2;
}

const byPosition = (a: IntakePhoto, b: IntakePhoto) => a.position - b.position;

type Props = {
  initialDraftId: string | null;
  taxonomy: Taxonomy;
};

export function IntakeWizard({ initialDraftId, taxonomy }: Props) {
  const router = useRouter();
  const [draftId, setDraftId] = useState<string | null>(initialDraftId);
  const [state, setState] = useState<IntakeState | null>(null);
  const [data, setData] = useState<ReportData>(EMPTY_DATA);
  const [notes, setNotes] = useState("");
  const [step, setStep] = useState<Step>(1);
  const [loading, setLoading] = useState(Boolean(initialDraftId));
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const [busy, setBusy] = useState<null | "saving" | "starting" | "finalizing">(null);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  // Photos regrouped/excluded since the AI last read the case.
  const [regrouped, setRegrouped] = useState(false);
  const [aiSkipped, setAiSkipped] = useState(false);

  // The upload queue lives in refs: uploads finish out of order and each
  // completion schedules the next, so reading it through React state would
  // see stale snapshots. `uploads` is just the rendered copy.
  const uploadsRef = useRef<UploadItem[]>([]);
  const activeRef = useRef(0);
  const draftIdRef = useRef<string | null>(initialDraftId);
  const creatingRef = useRef<Promise<string> | null>(null);
  const stageRef = useRef<IntakeStage | null>(null);

  const reviewTaxonomy: Taxonomy = {
    ...taxonomy,
    severities: taxonomy.severities.length ? taxonomy.severities : FALLBACK_SEVERITIES,
  };

  const applyState = useCallback((s: IntakeState, initial: boolean) => {
    const finishedReading = isRunning(stageRef.current) && s.stage === "ready";
    stageRef.current = s.stage;
    setState(s);
    if (initial || finishedReading) {
      // The AI's draft replaces the form -- only ever at these two moments,
      // never on routine polls, so typing isn't overwritten mid-edit.
      setData({ ...EMPTY_DATA, ...s.data });
      setRegrouped(false);
    }
    if (initial) setNotes(s.reporter_notes || "");
  }, []);

  const refresh = useCallback(async () => {
    const id = draftIdRef.current;
    if (!id) return;
    try {
      applyState(await getIntake(id), false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [applyState]);

  // Resume an existing draft (?draft=<id>).
  useEffect(() => {
    if (!initialDraftId) return;
    let cancelled = false;
    getIntake(initialDraftId)
      .then((s) => {
        if (cancelled) return;
        applyState(s, true);
        setStep(stepFor(s));
      })
      .catch((e: Error) => {
        if (cancelled) return;
        if (e.message.includes("already been turned into a case")) {
          router.replace(`/reports/${initialDraftId}`);
          return;
        }
        setError(e.message);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [initialDraftId, applyState, router]);

  // Poll while the AI works in the background.
  const running = isRunning(state?.stage);
  useEffect(() => {
    if (!running || !draftId) return;
    const timer = setInterval(() => {
      getIntake(draftId)
        .then((s) => applyState(s, false))
        .catch(() => {
          // transient -- the next tick retries
        });
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [running, draftId, applyState]);

  // Closing the tab mid-upload loses the rest of the queue (the File
  // objects only exist in this page), so warn first.
  const uploading = uploads.some((u) => u.status === "queued" || u.status === "uploading");
  useEffect(() => {
    if (!uploading) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [uploading]);

  async function ensureDraft(): Promise<string> {
    if (draftIdRef.current) return draftIdRef.current;
    if (!creatingRef.current) {
      creatingRef.current = createIntake()
        .then(({ id }) => {
          draftIdRef.current = id;
          setDraftId(id);
          // So a refresh, or coming back from the Drafts lane, resumes here.
          window.history.replaceState(null, "", `/reports/new?draft=${id}`);
          return id;
        })
        .catch((e) => {
          creatingRef.current = null;
          throw e;
        });
    }
    return creatingRef.current;
  }

  function syncUploads() {
    setUploads([...uploadsRef.current]);
  }

  function patchUpload(key: string, patch: Partial<UploadItem>) {
    uploadsRef.current = uploadsRef.current.map((u) => (u.key === key ? { ...u, ...patch } : u));
    syncUploads();
  }

  function pump() {
    const id = draftIdRef.current;
    if (!id) return;
    while (activeRef.current < UPLOAD_CONCURRENCY) {
      const next = uploadsRef.current.find((u) => u.status === "queued");
      if (!next) break;
      activeRef.current += 1;
      patchUpload(next.key, { status: "uploading", error: undefined });
      uploadIntakePhoto(id, next.file, next.position)
        .then((res) => {
          patchUpload(next.key, { status: res.duplicate_of ? "duplicate" : "done" });
          const photo = res.photo;
          if (photo) setState((prev) => (prev ? { ...prev, photos: [...prev.photos, photo].sort(byPosition) } : prev));
        })
        .catch((e: Error) => patchUpload(next.key, { status: "failed", error: e.message }))
        .finally(() => {
          activeRef.current -= 1;
          pump();
          // Queue drained: reload once for the server's P-number labels.
          if (activeRef.current === 0 && !uploadsRef.current.some((u) => u.status === "queued")) refresh();
        });
    }
  }

  async function addFiles(files: File[]) {
    setError(null);
    const maxBytes = state?.limits.max_photo_bytes ?? DEFAULT_MAX_BYTES;
    const maxPhotos = state?.limits.max_photos ?? DEFAULT_MAX_PHOTOS;
    // Name order is camera order (P1273082.JPG, P1273083.JPG...), which is
    // the order a surveyor expects P01, P02... to follow.
    const sorted = [...files].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" }));
    let position =
      Math.max(-1, ...(state?.photos.map((p) => p.position) ?? []), ...uploadsRef.current.map((u) => u.position)) + 1;
    const pending = uploadsRef.current.filter((u) => u.status === "queued" || u.status === "uploading").length;
    let room = maxPhotos - (state?.photos.length ?? 0) - pending;

    const items: UploadItem[] = [];
    for (const file of sorted) {
      // OS clutter from dropped folders (.DS_Store, Thumbs.db) isn't worth
      // reporting as a rejected file.
      if (file.name.startsWith(".") || /^(thumbs\.db|desktop\.ini)$/i.test(file.name)) continue;
      const ext = file.name.includes(".") ? file.name.slice(file.name.lastIndexOf(".")).toLowerCase() : "";
      let rejection: string | undefined;
      if (!ALLOWED_PHOTO_EXTENSIONS.includes(ext)) rejection = "Not a supported photo type";
      else if (file.size > maxBytes) rejection = `Over ${formatBytes(maxBytes)}`;
      else if (room <= 0) rejection = `Case limit of ${maxPhotos} photos reached`;
      else room -= 1;
      items.push({
        key: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
        file,
        name: file.name,
        size: file.size,
        position: rejection ? -1 : position++,
        status: rejection ? "rejected" : "queued",
        error: rejection,
      });
    }
    uploadsRef.current = [...uploadsRef.current, ...items];
    syncUploads();
    if (!items.some((u) => u.status === "queued")) return;

    try {
      await ensureDraft();
      if (!state) await refresh();
      pump();
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setError(`Couldn't start the draft: ${message}`);
      uploadsRef.current = uploadsRef.current.map((u) => (u.status === "queued" ? { ...u, status: "failed", error: message } : u));
      syncUploads();
    }
  }

  function retryFailed() {
    uploadsRef.current = uploadsRef.current.map((u) => (u.status === "failed" ? { ...u, status: "queued", error: undefined } : u));
    syncUploads();
    ensureDraft()
      .then(() => pump())
      .catch((e: Error) => setError(e.message));
  }

  async function save(): Promise<boolean> {
    const id = draftIdRef.current;
    if (!id) return true;
    setBusy("saving");
    try {
      await saveIntakeData(id, data, notes);
      setSavedAt(new Date());
      return true;
    } catch (e) {
      setError(`Couldn't save: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function goTo(next: Step) {
    if (next === step) return;
    if (draftIdRef.current && !(await save())) return;
    setStep(next);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function startProcessing(mode: "all" | "read") {
    const id = draftIdRef.current;
    if (!id) return;
    setBusy("starting");
    setError(null);
    try {
      await saveIntakeData(id, data, notes);
      setSavedAt(new Date());
      applyState(await processIntake(id, mode), false);
      setAiSkipped(false);
      setStep(2);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function regroup(photoId: string, kind: PhotoKind) {
    const id = draftIdRef.current;
    if (!id) return;
    setState((prev) =>
      prev ? { ...prev, photos: prev.photos.map((p) => (p.id === photoId ? { ...p, kind, kind_source: "surveyor" as const } : p)) } : prev
    );
    if (state?.read_photo_ids.length) setRegrouped(true);
    try {
      await updateIntakePhoto(id, photoId, { kind });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      refresh();
    }
  }

  async function toggleExclude(photoId: string, excluded: boolean) {
    const id = draftIdRef.current;
    if (!id) return;
    setState((prev) => (prev ? { ...prev, photos: prev.photos.map((p) => (p.id === photoId ? { ...p, excluded } : p)) } : prev));
    if (state?.read_photo_ids.length) setRegrouped(true);
    try {
      await updateIntakePhoto(id, photoId, { excluded });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      refresh();
    }
  }

  async function removePhoto(photoId: string) {
    const id = draftIdRef.current;
    if (!id) return;
    setState((prev) => (prev ? { ...prev, photos: prev.photos.filter((p) => p.id !== photoId) } : prev));
    try {
      await deleteIntakePhoto(id, photoId);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    // Later photos' P-numbers shift down by one.
    refresh();
  }

  async function finalize() {
    const id = draftIdRef.current;
    if (!id) return;
    setBusy("finalizing");
    setError(null);
    try {
      await finalizeIntake(id, data);
      router.push(`/reports/${id}`);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  }

  async function saveAndExit() {
    if (await save()) router.push("/reports");
  }

  const stage = state?.stage ?? null;
  const reachable = (n: Step): boolean => {
    if (n === 1) return true;
    if (!draftId || !state) return false;
    if (n === 2) return stage !== "uploading" || state.photos.some((p) => p.kind);
    return stage === "ready" || aiSkipped;
  };

  if (loading) {
    return <div className="intake-shell"><div className="card intake-panel intake-muted">Loading draft…</div></div>;
  }

  return (
    <div className="intake-shell new-report-form">
      <header className="intake-header">
        <div>
          <h1>New Case Intake</h1>
          <p className="form-hint">Drop the whole case folder — the AI sorts the photos and reads the facts, you check them.</p>
        </div>
        <div className="intake-header-actions">
          {draftId && <span className="intake-chip">Draft {draftId.slice(0, 6).toUpperCase()}</span>}
          {savedAt && (
            <span className="intake-muted">
              <Check style={{ width: 12, height: 12, verticalAlign: "-2px" }} /> Saved {savedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
          {draftId && (
            <button type="button" className="btn-secondary-modern" onClick={saveAndExit} disabled={Boolean(busy) || uploading}>
              <Save style={{ width: 14, height: 14 }} /> Save &amp; exit
            </button>
          )}
        </div>
      </header>

      <ol className="intake-stepper">
        {STEPS.map((s, i) => {
          const done = s.n < step;
          const can = reachable(s.n) && !busy;
          return (
            <li key={s.n} className={`intake-step ${s.n === step ? "active" : ""} ${done ? "done" : ""}`}>
              <button type="button" onClick={() => can && goTo(s.n)} disabled={!can}>
                <span className="intake-step-num">{done ? <Check style={{ width: 13, height: 13 }} /> : s.n}</span>
                <span>{s.label}</span>
              </button>
              {i < STEPS.length - 1 && <span className="intake-step-line" />}
            </li>
          );
        })}
      </ol>

      {error && (
        <div className="intake-alert danger">
          <AlertTriangle style={{ width: 16, height: 16, flexShrink: 0 }} />
          <div style={{ flex: 1 }}>{error}</div>
          <button type="button" className="intake-icon-btn" onClick={() => setError(null)} aria-label="Dismiss">
            <X style={{ width: 14, height: 14 }} />
          </button>
        </div>
      )}

      {step === 1 && (
        <UploadStep
          photos={state?.photos ?? []}
          uploads={uploads}
          stage={stage}
          data={data}
          onDataChange={(patch) => setData((d) => ({ ...d, ...patch }))}
          notes={notes}
          onNotesChange={setNotes}
          maxPhotos={state?.limits.max_photos ?? DEFAULT_MAX_PHOTOS}
          maxPhotoBytes={state?.limits.max_photo_bytes ?? DEFAULT_MAX_BYTES}
          busy={Boolean(busy)}
          onFiles={addFiles}
          onRetryFailed={retryFailed}
          onRemovePhoto={removePhoto}
          onStart={() => startProcessing("all")}
          onContinue={() => goTo(2)}
        />
      )}

      {step === 2 && state && (
        <SortStep
          state={state}
          regrouped={regrouped}
          busy={Boolean(busy)}
          onRegroup={regroup}
          onToggleExclude={toggleExclude}
          onRemove={removePhoto}
          onReread={() => startProcessing("read")}
          onRetry={() => startProcessing("all")}
          onSkipAi={() => {
            setAiSkipped(true);
            goTo(3);
          }}
          onBack={() => goTo(1)}
          onNext={() => goTo(3)}
        />
      )}

      {step === 3 && state && (
        <ReviewStep
          data={data}
          onChange={(update) => setData(update)}
          photos={state.photos}
          readPhotoIds={state.read_photo_ids}
          taxonomy={reviewTaxonomy}
          aiSkipped={aiSkipped}
          onBack={() => goTo(2)}
          onNext={() => goTo(4)}
        />
      )}

      {step === 4 && state && (
        <PartiesStep
          data={data}
          onChange={(update) => setData(update)}
          finalizing={busy === "finalizing"}
          onBack={() => goTo(3)}
          onFinalize={finalize}
        />
      )}
    </div>
  );
}
