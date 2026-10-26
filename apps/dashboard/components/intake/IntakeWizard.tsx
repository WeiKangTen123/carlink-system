"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertTriangle, ArrowLeft, Bot, Check, Lock, Save, X } from "lucide-react";
import { ownerLabel, type ReportData, type Taxonomy } from "@/lib/api";
import { CaseNotFound } from "@/components/CaseNotFound";
import { canViewAllCases, useCurrentUser } from "@/components/CurrentUser";
import {
  ALLOWED_PHOTO_EXTENSIONS,
  IntakeRequestError,
  createIntake,
  deleteIntakePhoto,
  finalizeIntake,
  formatBytes,
  getIntake,
  isPermanentFailure,
  isTransientFailure,
  processIntake,
  saveIntakeData,
  updateIntakePhoto,
  uploadFailureReason,
  uploadIntakePhoto,
  UPLOAD_RETRY_DELAYS_MS,
  type IntakePhoto,
  type IntakeStage,
  type IntakeState,
  type PhotoKind,
} from "@/lib/intake";
import { PartiesStep } from "./PartiesStep";
import { ReviewStep } from "./ReviewStep";
import { SortStep } from "./SortStep";
import { UndoToast } from "./UndoToast";
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
// A server's Retry-After can ask for a longer wait before an automatic
// upload retry than UPLOAD_RETRY_DELAYS_MS, up to this.
const MAX_RETRY_AFTER_MS = 60_000;
// How long "Photo deleted · Undo" stays up before the delete is sent.
const DELETE_UNDO_MS = 8000;
const POLL_MS = 2000;
const AUTOSAVE_MS = 1500;
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
  const user = useCurrentUser();
  const [draftId, setDraftId] = useState<string | null>(initialDraftId);
  const [state, setState] = useState<IntakeState | null>(null);
  const [data, setData] = useState<ReportData>(EMPTY_DATA);
  const [notes, setNotes] = useState("");
  const [step, setStep] = useState<Step>(1);
  const [loading, setLoading] = useState(Boolean(initialDraftId));
  // Set when an existing draft couldn't be loaded. Nothing may be saved
  // then: the form is empty, and saving it would wipe the draft on the
  // server (the "Save & exit" button used to do exactly that).
  const [loadError, setLoadError] = useState<string | null>(null);
  // The API said 404: no such draft, or not one this account may open.
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The AI was refused for this account (ai_key_missing): offer to fill the
  // case in by hand instead.
  const [aiRefused, setAiRefused] = useState(false);
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const [busy, setBusy] = useState<null | "saving" | "starting" | "finalizing">(null);
  // Edits not yet on the server. Saved automatically shortly after the
  // last change (previously only on a step change, so leaving the page
  // from step 3 silently lost everything typed there).
  const [dirty, setDirty] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  // Photos regrouped/excluded/deleted since the AI last read the case.
  const [regrouped, setRegrouped] = useState(false);
  const [aiSkipped, setAiSkipped] = useState(false);
  // When each photo was last uploaded, regrouped or (un)excluded here, and
  // when the last AI run began -- so step 2 can tell which unread photos the
  // read never saw. Client-side only, like `regrouped`: lost on reload.
  const [changedAt, setChangedAt] = useState<ReadonlyMap<string, number>>(() => new Map());
  const [readStartedAt, setReadStartedAt] = useState(0);
  // The "Photo deleted · Undo" toast; version restarts its countdown bar.
  const [deleteToast, setDeleteToast] = useState<{ count: number; version: number } | null>(null);

  // The upload queue lives in refs: uploads finish out of order and each
  // completion schedules the next, so reading it through React state would
  // see stale snapshots. `uploads` is just the rendered copy.
  const uploadsRef = useRef<UploadItem[]>([]);
  const activeRef = useRef(0);
  const draftIdRef = useRef<string | null>(initialDraftId);
  const creatingRef = useRef<Promise<string> | null>(null);
  const stageRef = useRef<IntakeStage | null>(null);
  // Latest values for the save paths that run outside React's render cycle
  // (tab hidden/closed), where state would be a stale snapshot.
  const loadedRef = useRef(!initialDraftId);
  const dataRef = useRef<ReportData>(EMPTY_DATA);
  const notesRef = useRef("");
  const dirtyRef = useRef(false);
  // Bumped on every edit, so a save that finishes after further typing
  // doesn't mark those newer edits as saved.
  const editVersionRef = useRef(0);
  // Deleted photos inside their undo window: gone from the screen, not yet
  // from the server. The DELETE goes out when the window ends, or sooner
  // whenever the wizard moves on (see flushDeletes).
  const pendingDeletesRef = useRef<Map<string, IntakePhoto>>(new Map());
  const deleteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Deletes sent and not yet answered; true if they all went through.
  const flushingRef = useRef<Promise<boolean> | null>(null);
  // `regrouped` as it was before this batch of deletes, for Undo to put back.
  const regroupedBeforeDeleteRef = useRef<boolean | null>(null);
  // The latest flushDeletes, for timers and page-hide listeners set up once.
  const flushDeletesRef = useRef<(opts?: { keepalive?: boolean }) => Promise<boolean>>(async () => true);
  dataRef.current = data;
  notesRef.current = notes;
  dirtyRef.current = dirty;

  function editData(update: (d: ReportData) => ReportData) {
    setData(update);
    editVersionRef.current += 1;
    setDirty(true);
  }

  function editNotes(value: string) {
    setNotes(value);
    editVersionRef.current += 1;
    setDirty(true);
  }

  const reviewTaxonomy: Taxonomy = {
    ...taxonomy,
    severities: taxonomy.severities.length ? taxonomy.severities : FALLBACK_SEVERITIES,
  };

  const applyState = useCallback((s: IntakeState, initial: boolean) => {
    const finishedReading = isRunning(stageRef.current) && s.stage === "ready";
    stageRef.current = s.stage;
    // Photos waiting out their undo window are still on the server; keep
    // them off the screen.
    const hidden = pendingDeletesRef.current;
    setState(hidden.size ? { ...s, photos: s.photos.filter((p) => !hidden.has(p.id)) } : s);
    if (initial || finishedReading) {
      // The server's form replaces the local one -- only ever at these two
      // moments, never on routine polls, so typing isn't overwritten. After
      // a read the server has already merged the AI's facts with
      // everything the surveyor had saved (pipeline.merge_redraft), and
      // editing is locked while the AI runs, so nothing local is lost.
      setData({ ...EMPTY_DATA, ...s.data });
      setRegrouped(false);
      setDirty(false);
    }
    if (initial) {
      setNotes(s.reporter_notes || "");
      loadedRef.current = true;
    }
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
  const [loadAttempt, setLoadAttempt] = useState(0);
  useEffect(() => {
    if (!initialDraftId) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
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
        if (e instanceof IntakeRequestError && e.status === 404) {
          setMissing(true);
          return;
        }
        setLoadError(e.message);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [initialDraftId, applyState, router, loadAttempt]);

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

  // Leaving mid-upload loses the rest of the queue (the File objects only
  // exist in this page), and leaving with unsaved edits loses those. The
  // browser's own prompt covers closing the tab and reloading; in-app links
  // (sidebar, breadcrumbs, "Back to Cases Repository") never fire
  // beforeunload, so those clicks are caught here too.
  const uploading = uploads.some((u) => u.status === "queued" || u.status === "uploading" || u.status === "retrying");
  const unsafeToLeave = uploading || dirty;
  useEffect(() => {
    if (!unsafeToLeave) return;
    const beforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    const linkClick = (e: MouseEvent) => {
      const link = (e.target as HTMLElement | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!link || link.target === "_blank" || link.getAttribute("href")?.startsWith("#")) return;
      if (new URL(link.href, window.location.href).origin !== window.location.origin) return;
      const why = uploading ? "Photos are still uploading" : "You have unsaved changes";
      if (!window.confirm(`${why}. Leave this page anyway?`)) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", linkClick, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", linkClick, true);
    };
  }, [unsafeToLeave, uploading]);

  // A hidden tab may be discarded or closed without warning; push unsaved
  // edits and deletes still inside their undo window out first. keepalive
  // lets the requests finish after the page goes.
  useEffect(() => {
    const flush = () => {
      if (document.visibilityState !== "hidden") return;
      void flushDeletesRef.current({ keepalive: true });
      const id = draftIdRef.current;
      if (!id || !dirtyRef.current || !loadedRef.current) return;
      if (isRunning(stageRef.current)) return;
      saveIntakeData(id, dataRef.current, notesRef.current, { keepalive: true })
        .then(() => setDirty(false))
        .catch(() => {
          // stays dirty; the next autosave retries
        });
    };
    const pageHide = () => void flushDeletesRef.current({ keepalive: true });
    document.addEventListener("visibilitychange", flush);
    window.addEventListener("pagehide", pageHide);
    return () => {
      document.removeEventListener("visibilitychange", flush);
      window.removeEventListener("pagehide", pageHide);
      // Leaving the wizard (an in-app link, Save & exit): the undo window
      // ends here too.
      void flushDeletesRef.current({ keepalive: true });
    };
  }, []);

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

  function noteChanged(photoId: string) {
    const at = Date.now();
    setChangedAt((prev) => new Map(prev).set(photoId, at));
  }

  function pump() {
    const id = draftIdRef.current;
    if (!id) return;
    while (activeRef.current < UPLOAD_CONCURRENCY) {
      const next = uploadsRef.current.find((u) => u.status === "queued");
      if (!next) break;
      activeRef.current += 1;
      patchUpload(next.key, { status: "uploading" });
      uploadIntakePhoto(id, next.file, next.position)
        .then((res) => {
          // On a retry, "duplicate" almost always means the earlier try that
          // timed out did land after all -- it's this photo, not a repeat.
          patchUpload(next.key, {
            status: res.duplicate_of && next.attempts === 0 ? "duplicate" : "done",
            error: undefined,
            reason: undefined,
          });
          const photo = res.photo;
          if (photo) {
            noteChanged(photo.id);
            // The server numbers photos (P01, P02...) only when the case is
            // reloaded below; until then a placeholder, not a blank tile label.
            const shown = { ...photo, label: photo.label || "…" };
            // A poll or refresh may already have brought this photo in; adding
            // it again gave a duplicate tile (and a duplicate React key).
            setState((prev) =>
              prev && !prev.photos.some((p) => p.id === shown.id) ? { ...prev, photos: [...prev.photos, shown].sort(byPosition) } : prev
            );
          }
        })
        .catch((e: unknown) => {
          const error = e instanceof Error ? e.message : String(e);
          const reason = uploadFailureReason(e);
          if (isTransientFailure(e) && next.attempts < UPLOAD_RETRY_DELAYS_MS.length) {
            const asked = e instanceof IntakeRequestError ? (e.retryAfterMs ?? 0) : 0;
            const wait = Math.min(MAX_RETRY_AFTER_MS, Math.max(UPLOAD_RETRY_DELAYS_MS[next.attempts], asked));
            patchUpload(next.key, { status: "retrying", attempts: next.attempts + 1, error, reason });
            setTimeout(() => {
              // Removed from the queue while it waited.
              if (!uploadsRef.current.some((u) => u.key === next.key && u.status === "retrying")) return;
              patchUpload(next.key, { status: "queued" });
              pump();
            }, wait);
          } else {
            patchUpload(next.key, { status: "failed", error, reason, permanent: isPermanentFailure(e) });
          }
        })
        .finally(() => {
          activeRef.current -= 1;
          pump();
          // Queue drained: reload once for the server's P-number labels.
          if (activeRef.current === 0 && !uploadsRef.current.some((u) => u.status === "queued" || u.status === "retrying")) refresh();
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
    const pending = uploadsRef.current.filter((u) => u.status === "queued" || u.status === "uploading" || u.status === "retrying").length;
    let room = maxPhotos - (state?.photos.length ?? 0) - pending;

    const items: UploadItem[] = [];
    for (const file of sorted) {
      // OS clutter from dropped folders (.DS_Store, Thumbs.db) isn't worth
      // reporting as a rejected file.
      if (file.name.startsWith(".") || /^(thumbs\.db|desktop\.ini)$/i.test(file.name)) continue;
      const ext = file.name.includes(".") ? file.name.slice(file.name.lastIndexOf(".")).toLowerCase() : "";
      let rejection: string | undefined;
      if (!ALLOWED_PHOTO_EXTENSIONS.includes(ext)) rejection = "Not a supported photo type";
      else if (file.size > maxBytes) rejection = `${formatBytes(file.size)} -- over the ${formatBytes(maxBytes)} limit`;
      else if (room <= 0) rejection = `Case limit of ${maxPhotos} photos reached`;
      else room -= 1;
      items.push({
        key: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
        file,
        name: file.name,
        size: file.size,
        position: rejection ? -1 : position++,
        status: rejection ? "rejected" : "queued",
        attempts: 0,
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
      uploadsRef.current = uploadsRef.current.map((u) =>
        u.status === "queued" ? { ...u, status: "failed", error: message, reason: "Draft not started" } : u
      );
      syncUploads();
    }
  }

  /** Retries failed uploads that may still work -- `key` for one tile, or
   * every one. Uploads the server refused (unreadable, too big, case full)
   * are left alone: sending them again can't help. */
  function retryUploads(key?: string) {
    const retried = new Set(
      uploadsRef.current.filter((u) => u.status === "failed" && !u.permanent && (key === undefined || u.key === key)).map((u) => u.key)
    );
    if (!retried.size) return;
    uploadsRef.current = uploadsRef.current.map((u) =>
      retried.has(u.key) ? { ...u, status: "queued", attempts: 0, error: undefined, reason: undefined } : u
    );
    syncUploads();
    ensureDraft()
      .then(() => pump())
      .catch((e: Error) => {
        // Put them back to failed: left "queued" with no draft to upload
        // into, they would spin forever and keep Save & exit disabled.
        setError(e.message);
        uploadsRef.current = uploadsRef.current.map((u) =>
          retried.has(u.key) ? { ...u, status: "failed", error: e.message, reason: "Draft not started" } : u
        );
        syncUploads();
      });
  }

  /** Drops a failed (or waiting-to-retry) upload from the queue. One mid-upload
   * can't be called back, so it stays. */
  function removeUpload(key: string) {
    uploadsRef.current = uploadsRef.current.filter((u) => u.key !== key || u.status === "uploading");
    syncUploads();
  }

  function dismissRejected() {
    uploadsRef.current = uploadsRef.current.filter((u) => u.status !== "rejected");
    syncUploads();
  }

  /** quiet: an autosave -- no busy state, no error banner (it retries). */
  async function save(opts: { quiet?: boolean } = {}): Promise<boolean> {
    const id = draftIdRef.current;
    if (!id) return true;
    // Never save a form that didn't load from the server: it is empty, and
    // saving it would wipe the draft.
    if (!loadedRef.current) return false;
    if (!dirtyRef.current) return true;
    const version = editVersionRef.current;
    if (!opts.quiet) setBusy("saving");
    try {
      await saveIntakeData(id, dataRef.current, notesRef.current);
      setSavedAt(new Date());
      setSaveFailed(false);
      if (editVersionRef.current === version) setDirty(false);
      return true;
    } catch (e) {
      setSaveFailed(true);
      if (!opts.quiet) setError(`Couldn't save: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    } finally {
      if (!opts.quiet) setBusy(null);
    }
  }

  // Autosave shortly after the last change. Paused while the AI runs (the
  // server refuses edits then, and the form is locked anyway).
  useEffect(() => {
    if (!dirty || !draftId || running || busy || loading || loadError) return;
    const timer = setTimeout(() => {
      void save({ quiet: true });
    }, AUTOSAVE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dirty, data, notes, draftId, running, busy, loading, loadError]);

  async function goTo(next: Step) {
    if (next === step) return;
    // A failed delete puts its photo back and says so; it doesn't hold up
    // the step change.
    await flushDeletes();
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
      // The AI must not read a photo that is on its way out (and the server
      // refuses deletes once it has started).
      if (!(await flushDeletes())) return;
      await saveIntakeData(id, dataRef.current, notesRef.current);
      setSavedAt(new Date());
      setDirty(false);
      const started = Date.now();
      applyState(await processIntake(id, mode), false);
      setReadStartedAt(started);
      setAiSkipped(false);
      setStep(2);
    } catch (e) {
      if (e instanceof IntakeRequestError && e.code === "ai_key_missing") setAiRefused(true);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  /** No AI for this account: straight to entering the facts by hand. */
  function fillByHand() {
    setError(null);
    setAiSkipped(true);
    void goTo(3);
  }

  /** A change the AI's last read didn't see: offer a re-read. */
  function markRegrouped(photoId: string) {
    noteChanged(photoId);
    if (!state?.read_photo_ids.length) return;
    setRegrouped(true);
    // Undoing a delete must not clear the flag this change set.
    if (regroupedBeforeDeleteRef.current === false) regroupedBeforeDeleteRef.current = true;
  }

  async function regroup(photoId: string, kind: PhotoKind) {
    const id = draftIdRef.current;
    if (!id) return;
    setState((prev) =>
      prev ? { ...prev, photos: prev.photos.map((p) => (p.id === photoId ? { ...p, kind, kind_source: "surveyor" as const } : p)) } : prev
    );
    markRegrouped(photoId);
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
    markRegrouped(photoId);
    try {
      await updateIntakePhoto(id, photoId, { excluded });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      refresh();
    }
  }

  /** Takes the photo off the screen at once; the DELETE waits out the undo
   * window. Deleting more photos meanwhile adds them to the same toast and
   * restarts its countdown. */
  function deletePhoto(photoId: string) {
    const photo = state?.photos.find((p) => p.id === photoId);
    if (!photo || !draftIdRef.current) return;
    const pending = pendingDeletesRef.current;
    if (pending.size === 0) regroupedBeforeDeleteRef.current = regrouped;
    pending.set(photoId, photo);
    setState((prev) => (prev ? { ...prev, photos: prev.photos.filter((p) => p.id !== photoId) } : prev));
    if (state?.read_photo_ids.includes(photoId)) setRegrouped(true);
    if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current);
    deleteTimerRef.current = setTimeout(() => void flushDeletesRef.current(), DELETE_UNDO_MS);
    const count = pending.size;
    setDeleteToast((t) => ({ count, version: (t?.version ?? 0) + 1 }));
  }

  function undoDelete() {
    const pending = pendingDeletesRef.current;
    if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current);
    deleteTimerRef.current = null;
    setDeleteToast(null);
    if (!pending.size) return;
    pendingDeletesRef.current = new Map();
    const restored = [...pending.values()];
    setState((prev) =>
      prev ? { ...prev, photos: [...prev.photos.filter((p) => !pending.has(p.id)), ...restored].sort(byPosition) } : prev
    );
    if (regroupedBeforeDeleteRef.current === false) setRegrouped(false);
    regroupedBeforeDeleteRef.current = null;
  }

  /** Sends the deletes still inside their undo window, now, and waits for
   * any already on their way (the toast's own timer may have just sent
   * some -- finalize mustn't overtake them). A delete that fails puts its
   * photo back and shows why. True if all went through.
   * keepalive: the page is being hidden or left. */
  async function flushDeletes(opts: { keepalive?: boolean } = {}): Promise<boolean> {
    if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current);
    deleteTimerRef.current = null;
    const batch = pendingDeletesRef.current;
    const id = draftIdRef.current;
    if (batch.size && id) {
      pendingDeletesRef.current = new Map();
      regroupedBeforeDeleteRef.current = null;
      setDeleteToast(null);
      const sent = sendDeletes(id, [...batch.values()], opts);
      const earlier = flushingRef.current;
      flushingRef.current = earlier ? Promise.all([earlier, sent]).then(([a, b]) => a && b) : sent;
    }
    const inFlight = flushingRef.current;
    if (!inFlight) return true;
    const ok = await inFlight;
    if (flushingRef.current === inFlight) flushingRef.current = null;
    return ok;
  }
  flushDeletesRef.current = flushDeletes;

  async function sendDeletes(id: string, photos: IntakePhoto[], opts: { keepalive?: boolean }): Promise<boolean> {
    const results = await Promise.allSettled(photos.map((p) => deleteIntakePhoto(id, p.id, opts)));
    const failed: IntakePhoto[] = [];
    let message = "";
    results.forEach((r, i) => {
      if (r.status === "fulfilled") return;
      // Already gone (deleted from another tab): that's what was wanted.
      if (r.reason instanceof IntakeRequestError && r.reason.status === 404) return;
      failed.push(photos[i]);
      message ||= r.reason instanceof Error ? r.reason.message : String(r.reason);
    });
    if (failed.length) {
      const back = new Set(failed.map((p) => p.id));
      setState((prev) => (prev ? { ...prev, photos: [...prev.photos.filter((p) => !back.has(p.id)), ...failed].sort(byPosition) } : prev));
      const what = failed.length === 1 ? `photo ${failed[0].label || failed[0].name}, so it's` : `${failed.length} photos, so they're`;
      setError(`Couldn't delete ${what} back in the case: ${message}`);
    }
    // Later photos' P-numbers shift down. Not while the page is going away.
    if (!opts.keepalive) void refresh();
    return failed.length === 0;
  }

  async function finalize() {
    const id = draftIdRef.current;
    if (!id) return;
    setBusy("finalizing");
    setError(null);
    try {
      // A photo deleted moments ago must not end up in the case.
      if (!(await flushDeletes())) {
        setBusy(null);
        return;
      }
      await finalizeIntake(id, data);
      setDirty(false);
      router.push(`/reports/${id}`);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  }

  async function saveAndExit() {
    if (!(await flushDeletes())) return;
    if (await save()) router.push("/reports");
  }

  const stage = state?.stage ?? null;
  const reachable = (n: Step): boolean => {
    if (n === 1) return true;
    if (!draftId || !state) return false;
    if (n === 2) return stage !== "uploading" || state.photos.some((p) => p.kind);
    // A failed AI never blocks filling in the facts by hand.
    return stage === "ready" || stage === "failed" || aiSkipped;
  };

  const changedSinceRead = useMemo(
    () => new Set([...changedAt].filter(([, at]) => at > readStartedAt).map(([photoId]) => photoId)),
    [changedAt, readStartedAt]
  );

  if (loading) {
    return <div className="intake-shell"><div className="card intake-panel intake-muted">Loading draft…</div></div>;
  }

  if (missing) return <CaseNotFound />;

  if (loadError) {
    return (
      <div className="intake-shell">
        <div className="intake-alert danger" role="alert">
          <AlertTriangle style={{ width: 16, height: 16, flexShrink: 0 }} />
          <div style={{ flex: 1 }}>
            Couldn&apos;t load this draft: {loadError}. Nothing has been changed -- try again in a moment.
          </div>
          <button type="button" className="btn-secondary-modern" onClick={() => setLoadAttempt((n) => n + 1)}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  // Someone else's unfinished draft: staff who see all cases can open it,
  // but only its owner or an admin may work on it.
  if (state && !state.permissions.edit) {
    return (
      <div className="intake-shell">
        <div className="card intake-panel intake-view-only">
          <Lock style={{ width: 20, height: 20, color: "var(--text-muted)", flexShrink: 0 }} />
          <div style={{ minWidth: 0 }}>
            <h1 style={{ fontSize: 18, fontWeight: 800, margin: "0 0 6px" }}>Unfinished draft</h1>
            <p className="form-hint" style={{ margin: "0 0 12px" }}>
              Owner: <strong>{ownerLabel(state.owner)}</strong>. Only its owner or an admin can work on a draft. You can open
              it once it is filed as a case.
            </p>
            <Link href="/reports" prefetch={false} className="btn-secondary-modern" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <ArrowLeft style={{ width: 13, height: 13 }} /> Back to cases
            </Link>
          </div>
        </div>
      </div>
    );
  }

  // AI is off for this account (GET /auth/me), or the API just said so.
  const aiOff = aiRefused || user?.ai_allowed === false;

  let saveStatus: React.ReactNode = null;
  if (running) saveStatus = <span className="intake-muted">Editing locked while the AI reads</span>;
  else if (saveFailed && dirty) saveStatus = <span className="intake-chip warn">Not saved -- retrying</span>;
  else if (dirty) saveStatus = <span className="intake-muted">Unsaved changes…</span>;
  else if (savedAt)
    saveStatus = (
      <span className="intake-muted">
        <Check style={{ width: 12, height: 12, verticalAlign: "-2px" }} /> Saved {savedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
      </span>
    );

  return (
    <div className="intake-shell new-report-form">
      <header className="intake-header">
        <div>
          <h1>New Case Intake</h1>
          <p className="form-hint">Drop the whole case folder — the AI sorts the photos and reads the facts, you check them.</p>
        </div>
        <div className="intake-header-actions">
          {draftId && <span className="intake-chip">Draft {draftId.slice(0, 6).toUpperCase()}</span>}
          {/* Only someone who sees other people's cases needs telling whose
              draft this is. Drafts can't be reassigned (owner_draft). */}
          {state && canViewAllCases(user) && (
            <span className={`intake-chip ${state.owner ? "" : "warn"}`}>Owner: {ownerLabel(state.owner)}</span>
          )}
          <span role="status">{saveStatus}</span>
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
        <div className="intake-alert danger" role="alert">
          <AlertTriangle style={{ width: 16, height: 16, flexShrink: 0 }} />
          <div style={{ flex: 1 }}>{error}</div>
          <button type="button" className="intake-icon-btn" onClick={() => setError(null)} aria-label="Dismiss">
            <X style={{ width: 14, height: 14 }} />
          </button>
        </div>
      )}

      {step === 1 && aiOff && (
        <div className="intake-alert info" role="status">
          <Bot style={{ width: 16, height: 16, flexShrink: 0 }} />
          <div style={{ flex: 1 }}>
            AI is off for your account, so the photos won&apos;t be sorted or read for you. You can fill the case in by hand.
          </div>
          <button
            type="button"
            className="btn-secondary-modern"
            onClick={fillByHand}
            disabled={!draftId || !state || uploading || Boolean(busy)}
            title={!draftId ? "Add the photos first" : undefined}
          >
            Fill in by hand
          </button>
        </div>
      )}

      {step === 1 && (
        <UploadStep
          photos={state?.photos ?? []}
          uploads={uploads}
          stage={stage}
          data={data}
          onDataChange={(patch) => editData((d) => ({ ...d, ...patch }))}
          notes={notes}
          onNotesChange={editNotes}
          locked={running}
          maxPhotos={state?.limits.max_photos ?? DEFAULT_MAX_PHOTOS}
          maxPhotoBytes={state?.limits.max_photo_bytes ?? DEFAULT_MAX_BYTES}
          busy={Boolean(busy)}
          onFiles={addFiles}
          onRetryFailed={() => retryUploads()}
          onRetryUpload={retryUploads}
          onRemoveUpload={removeUpload}
          onDismissRejected={dismissRejected}
          onRemovePhoto={deletePhoto}
          onStart={() => startProcessing("all")}
          onContinue={() => goTo(2)}
          aiOff={aiOff}
          onFillByHand={fillByHand}
        />
      )}

      {step === 2 && state && (
        <SortStep
          state={state}
          regrouped={regrouped}
          changedSinceRead={changedSinceRead}
          aiSkipped={aiSkipped}
          busy={Boolean(busy)}
          onRegroup={regroup}
          onToggleExclude={toggleExclude}
          onRemove={deletePhoto}
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
          onChange={editData}
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
          onChange={editData}
          finalizing={busy === "finalizing"}
          uploading={uploading}
          onBack={() => goTo(3)}
          onFinalize={finalize}
        />
      )}

      {deleteToast && (
        <UndoToast
          message={deleteToast.count === 1 ? "Photo deleted" : `${deleteToast.count} photos deleted`}
          durationMs={DELETE_UNDO_MS}
          timerKey={deleteToast.version}
          onUndo={undoDelete}
          onDismiss={() => void flushDeletes()}
        />
      )}
    </div>
  );
}
