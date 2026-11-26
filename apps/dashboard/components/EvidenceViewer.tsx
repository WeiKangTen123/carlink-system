"use client";

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Box as BoxIcon, Check, ChevronLeft, ChevronRight, Clock, ExternalLink, Plus, Undo2, X } from "lucide-react";
import type { DecideAction, DecideResult } from "@/lib/api";
import {
  BOX_WORDS,
  aspectMatches,
  boxChip,
  boxLine,
  slidesOf,
  zoomToBox,
  type Frame,
  type Slide,
} from "@/lib/evidence";
import {
  BUCKET_LABELS,
  STRIP_DOWN_TEXT,
  aiSays,
  alsoLinkedText,
  checkWords,
  codeReason,
  confidenceText,
  confirmAtStripDown,
  fittedText,
  foundByNames,
  foundByText,
  hiddenNoPhotoText,
  hopText,
  intactText,
  linkBucket,
  linkCheckLine,
  linkGroupTitle,
  linkPhotoCaption,
  panelOrder,
  sideFromText,
  signText,
  sourceLabel,
  sourceName,
  type Finding,
  type FindingStatus,
  type PhotoInfo,
  type PhotoLookup,
} from "@/lib/findings";
import { isTypingTarget } from "@/lib/intakeReview";
import { PhotoBoxes, type BoxMark } from "./PhotoBoxes";
import { DecisionToast, toastFor, type DecisionToastState } from "./SuggestionPanel";

export type EvidenceViewerProps = {
  open: boolean;
  mode: "suggestions" | "listed" | "photo";
  findings: Finding[];
  startKey?: string | null;
  startPhoto?: string | null;
  photos: PhotoLookup;
  /** Keys are finding keys ("sug:<key>"); lib/useSuggestionDecisions takes
   * them. May resolve with the server's answer (an add with item null was
   * on the list already). */
  onDecide?: (key: string, action: DecideAction, looked: boolean) => Promise<DecideResult | void>;
  onShowOnMap?: (key: string) => void;
  onClose: () => void;
  readOnly?: boolean;
  /** second_look.reads, for "Found by 1 of 5 reads". Optional: added by the
   * evidence UI. */
  reads?: string[] | null;
  /** "photo" mode: the photos to step through, in order (e.g. a Photos by
   * part group). Without it, every photo a finding was seen in. Optional:
   * added by the evidence UI. */
  photoOrder?: string[] | null;
  /** Step 3A: [Add part seen here] under a photo shown on its own ("photo"
   * mode), for a page that can add a part (it opens its part picker on that
   * photo). Never beside a suggestion's own Add, whose decision would then
   * go unrecorded. Not passed, or read-only: no button. */
  onAddFromPhoto?: (photoRef: string) => void;
};

const STATUS_TEXT: Record<FindingStatus, string> = {
  confirmed: "✓ Confirmed by a person",
  ai: "Listed by the AI, not checked yet",
  check: "! Check: the AI was unsure or guessed the side",
  suggestion: "+ Suggestion: not on the damage list",
  dismissed: "× Marked not damaged",
};
const STATUS_GLYPH: Record<FindingStatus, string> = { confirmed: "✓", ai: "", check: "!", suggestion: "+", dismissed: "×" };

const ZOOMS = [1, 2, 4] as const;
// The stage's padding round the photo, both sides together (globals.css .ev-stage).
const STAGE_PAD = 24;

/** The size an image loads at, without drawing it: the stage is sized from
 * it before the photo is laid out. */
function useNaturalSize(src: string | null): Frame | null {
  const [size, setSize] = useState<(Frame & { src: string }) | null>(null);
  useEffect(() => {
    if (!src) return;
    let live = true;
    const img = new Image();
    img.onload = () => {
      if (live && img.naturalWidth > 0) setSize({ src, w: img.naturalWidth, h: img.naturalHeight });
    };
    img.src = src;
    return () => {
      live = false;
      img.onload = null;
    };
  }, [src]);
  return size && size.src === src ? { w: size.w, h: size.h } : null;
}

/** The full-screen evidence viewer: a finding's photos with their boxes and
 * the AI's words, on both the wizard's Review step and the case page
 * (contract9.md section 7.2); replaces intake/DamagePhotoViewer.tsx.
 *
 * - suggestions: steps through the undecided suggestions; [A Add] [N Not
 *   damaged] [L Later] decide here and move on to the next.
 * - listed: the parts on the list, read-only ("why the AI listed it").
 * - photo: one photo with every finding marked on it.
 *
 * Zoom Fit / 2x / 4x / Zoom to box and Box on/off; keys A N L, arrows
 * (left/right photos, up/down findings), Z B F and Esc, never while typing.
 *
 * Contract 10, a linked part: with no photo of its own it shows its listed
 * part's photo, captioned "Photo of the Rear Bumper (this part sits behind
 * it)" and never with a box; the linked check's photo shows its "AI box".
 * Focus stays inside while it is open and goes back to the opener after. */
export function EvidenceViewer(props: EvidenceViewerProps) {
  if (!props.open || typeof document === "undefined") return null;
  // A new opening starts afresh; decisions made inside keep it as it is.
  return <ViewerDialog key={`${props.mode}|${props.startKey ?? ""}|${props.startPhoto ?? ""}`} {...props} />;
}

type Cursor = { key: string | null; photo: string | null; index: number };

function ViewerDialog({ mode: startMode, findings, startKey, startPhoto, photos, onDecide, onShowOnMap, onClose, readOnly, reads, photoOrder, onAddFromPhoto }: EvidenceViewerProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState(startMode);
  // A finding opened on purpose stays in the walk even when decided (a
  // dismissed one opened from the Decided list).
  const [pinned, setPinned] = useState<string | null>(startKey ?? null);
  const [cur, setCur] = useState<Cursor>({ key: startKey ?? null, photo: startPhoto ?? null, index: 0 });
  const [zoom, setZoom] = useState(1);
  const [showBoxes, setShowBoxes] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<DecisionToastState | null>(null);
  const [announce, setAnnounce] = useState("");
  // Every suggestion in the walk was decided here: the keys the walk held
  // then, so one that arrives later (an Add's own linked parts) is new.
  const [ended, setEnded] = useState<ReadonlySet<string> | null>(null);
  // "2 new suggestions came in with that decision", on the first of them.
  const [arrived, setArrived] = useState<{ key: string; text: string } | null>(null);
  const pendingScroll = useRef<{ left: number; top: number } | null>(null);

  const norm = useCallback((ref: string | null | undefined) => (ref ? (photos(ref)?.ref ?? ref) : ""), [photos]);

  // ---- what is walked ------------------------------------------------------
  const walk = useMemo(() => {
    // In the cards' order (the re-check's; linked parts by group), so A / N
    // move on to the card below.
    if (mode === "suggestions") return panelOrder(findings.filter((f) => f.kind === "suggestion" && (f.status === "suggestion" || f.key === pinned)));
    if (mode === "listed") return findings.filter((f) => f.kind === "item");
    return [];
  }, [findings, mode, pinned]);

  let at = mode === "photo" || ended ? -1 : walk.findIndex((f) => f.key === cur.key);
  // Not found: opened with no start, or the finding went (decided elsewhere,
  // the list changed) -- the one now in its place.
  if (mode !== "photo" && !ended && at < 0 && walk.length) at = Math.min(cur.index, walk.length - 1);
  const finding: Finding | null = at >= 0 ? walk[at] : null;

  // Contract 10 (review L10): an Add can bring the added part's own linked
  // parts. The next card was picked before the answer came, so when the
  // last one was just decided, walk on to the newcomers once they arrive.
  useEffect(() => {
    if (!ended || mode !== "suggestions") return;
    const fresh = walk.filter((f) => f.status === "suggestion" && !ended.has(f.key));
    if (!fresh.length) return;
    const index = walk.indexOf(fresh[0]);
    setEnded(null);
    setCur({ key: fresh[0].key, photo: null, index });
    const text = `${fresh.length} new suggestion${fresh.length === 1 ? "" : "s"} came in with that decision.`;
    setArrived({ key: fresh[0].key, text });
  }, [ended, walk, mode]);
  const done = mode === "suggestions" && !finding;

  type Shown = Slide & { info: PhotoInfo | null; usable: boolean };
  const slides: Shown[] = useMemo(
    () =>
      finding
        ? slidesOf(finding).map((s) => {
            const info = photos(s.photo);
            // The listed part's photo: the linked part isn't marked on it.
            const own = s.by === "link" ? { box: null, kind: null, frame: null } : {};
            return { ...s, ...own, info, usable: Boolean(info && !info.deleted && info.src) };
          })
        : [],
    [finding, photos]
  );
  const usable = slides.filter((s) => s.usable);

  // Photo mode: the photos to step through.
  const photoList = useMemo(() => {
    if (mode !== "photo") return [];
    let refs: string[];
    if (photoOrder?.length) refs = photoOrder.map(norm);
    else {
      const set = new Map<string, string>();
      for (const f of findings) for (const e of f.evidence) set.set(norm(e.photo), photos(e.photo)?.label || e.photo);
      refs = [...set.entries()].sort((a, b) => a[1].localeCompare(b[1], undefined, { numeric: true })).map(([r]) => r);
    }
    const start = norm(startPhoto);
    if (start && !refs.includes(start)) refs = [start, ...refs];
    return refs.filter((r) => {
      const info = photos(r);
      return info && !info.deleted && info.src;
    });
  }, [mode, photoOrder, findings, photos, norm, startPhoto]);

  let slide: Shown | null = null;
  let photoRef: string | null = null;
  if (mode === "photo") {
    photoRef = photoList.includes(norm(cur.photo)) ? norm(cur.photo) : (photoList[0] ?? null);
  } else {
    slide = usable.find((s) => norm(s.photo) === norm(cur.photo)) ?? usable[0] ?? null;
    photoRef = slide ? norm(slide.photo) : null;
  }
  const info = photoRef ? photos(photoRef) : null;
  const src = info?.src || null;
  const natural = useNaturalSize(src);

  // The next photo is fetched ahead, so stepping through doesn't wait.
  const nextSrc = (() => {
    if (mode === "photo") {
      const i = photoList.indexOf(photoRef || "");
      return i >= 0 && photoList[i + 1] ? photos(photoList[i + 1])?.src || null : null;
    }
    const i = usable.findIndex((s) => norm(s.photo) === photoRef);
    return usable[i + 1]?.info?.src ?? null;
  })();
  useEffect(() => {
    if (!nextSrc) return;
    const img = new Image();
    img.src = nextSrc;
  }, [nextSrc]);

  // Every finding marked on the photo (photo mode).
  const onPhoto = useMemo(() => {
    if (mode !== "photo" || !photoRef) return [];
    const out: { f: Finding; s: Slide }[] = [];
    for (const f of findings) {
      if (f.status === "dismissed") continue;
      // A linked part is not on its listed part's photo (by "link").
      const s = slidesOf(f).find((x) => norm(x.photo) === photoRef && x.by !== "link");
      if (s) out.push({ f, s });
    }
    return out;
  }, [mode, photoRef, findings, norm]);

  const marks: BoxMark[] =
    mode === "photo"
      ? onPhoto.map(({ f, s }) => ({ key: f.key, label: f.name, status: f.status, box: s.box, kind: s.kind, frame: s.frame }))
      : finding && slide
        ? [{ key: finding.key, label: finding.name, status: finding.status, box: slide.box, kind: slide.kind, frame: slide.frame }]
        : [];
  const hidden = Boolean(slide?.box && natural && !aspectMatches(slide.frame, natural));

  // ---- zoom ----------------------------------------------------------------
  // The photo is fitted by CSS alone (globals.css .ev-canvas: container units
  // and the photo's shape), so a resize or a turned phone never leaves it at
  // a stale size. Only scrolling to a box reads sizes, at the moment of the
  // click.
  const canvasRef = useRef<HTMLDivElement>(null);
  const sizesNow = (): { fit: Frame; stage: Frame } | null => {
    const el = stageRef.current;
    const canvas = canvasRef.current;
    if (!el || !canvas || !natural) return null;
    const stage = { w: Math.max(0, el.clientWidth - STAGE_PAD), h: Math.max(0, el.clientHeight - STAGE_PAD) };
    return { fit: { w: canvas.offsetWidth / zoom, h: canvas.offsetHeight / zoom }, stage };
  };

  // A new photo starts fitted.
  useEffect(() => {
    setZoom(1);
  }, [photoRef]);

  useLayoutEffect(() => {
    const el = stageRef.current;
    const want = pendingScroll.current;
    if (!el || !want) return;
    el.scrollLeft = want.left;
    el.scrollTop = want.top;
    pendingScroll.current = null;
  });

  const boxForZoom = slide?.box && !hidden ? slide.box : null;
  const setZoomAt = (z: number) => {
    const now = sizesNow();
    if (!now) return;
    const { fit, stage } = now;
    const zoomed = { w: fit.w * z, h: fit.h * z };
    // Keep the box in the middle when there is one, else the photo's centre.
    const [y0, x0, y1, x1] = boxForZoom ?? [0, 0, 1000, 1000];
    const cx = ((x0 + x1) / 2000) * zoomed.w;
    const cy = ((y0 + y1) / 2000) * zoomed.h;
    pendingScroll.current = {
      left: Math.max(0, Math.min(cx - stage.w / 2, zoomed.w - stage.w)),
      top: Math.max(0, Math.min(cy - stage.h / 2, zoomed.h - stage.h)),
    };
    setZoom(z);
  };
  const zoomToTheBox = () => {
    const now = sizesNow();
    if (!boxForZoom || !now) return;
    const z = zoomToBox(boxForZoom, now.fit, now.stage);
    pendingScroll.current = { left: z.left, top: z.top };
    setZoom(z.zoom);
    if (!showBoxes) setShowBoxes(true);
  };

  // Drag to move round a zoomed photo.
  const drag = useRef<{ x: number; y: number; left: number; top: number; id: number } | null>(null);
  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    const el = stageRef.current;
    if (!el || zoom <= 1 || (e.target as HTMLElement).closest("button")) return;
    drag.current = { x: e.clientX, y: e.clientY, left: el.scrollLeft, top: el.scrollTop, id: e.pointerId };
    el.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const el = stageRef.current;
    const d = drag.current;
    if (!el || !d || d.id !== e.pointerId) return;
    el.scrollLeft = d.left - (e.clientX - d.x);
    el.scrollTop = d.top - (e.clientY - d.y);
  };
  const endDrag = () => {
    drag.current = null;
  };

  // ---- moving --------------------------------------------------------------
  const goFinding = useCallback(
    (index: number, photo?: string | null) => {
      const f = walk[index];
      if (!f) return;
      setError(null);
      setArrived(null);
      setCur({ key: f.key, photo: photo ?? null, index });
    },
    [walk]
  );
  const stepPhoto = (dir: 1 | -1) => {
    if (mode === "photo") {
      const i = photoList.indexOf(photoRef || "");
      const next = photoList[i + dir];
      if (next) setCur((c) => ({ ...c, photo: next }));
      return;
    }
    const i = usable.findIndex((s) => norm(s.photo) === photoRef);
    const next = usable[i + dir];
    if (next) setCur((c) => ({ ...c, photo: next.photo }));
  };
  const stepFinding = (dir: 1 | -1) => {
    if (mode === "photo" || at < 0) return;
    if (walk[at + dir]) goFinding(at + dir);
  };

  /** The next undecided suggestion after `key` (wrapping round), not `key`. */
  const nextAfter = (key: string): { key: string; index: number } | null => {
    const i = walk.findIndex((f) => f.key === key);
    for (let step = 1; step < walk.length; step += 1) {
      const j = (i + step) % walk.length;
      const f = walk[j];
      if (f.key !== key && f.status === "suggestion") return { key: f.key, index: j > i ? j - 1 : j };
    }
    return null;
  };

  const canDecide = mode === "suggestions" && !readOnly && Boolean(onDecide) && finding?.status === "suggestion";

  const decideHere = async (action: "add" | "dismiss") => {
    if (!canDecide || !finding || busy || !onDecide) return;
    const f = finding;
    const next = nextAfter(f.key);
    setBusy(true);
    setError(null);
    try {
      const r = await onDecide(f.key, action, true);
      const shown = toastFor(f, action, r);
      setToast(shown);
      setPinned(null);
      setArrived(null);
      if (next) setCur({ key: next.key, photo: null, index: next.index });
      else setEnded(new Set(walk.map((x) => x.key)));
      setAnnounce(
        shown.action === "listed" ? `${f.name} is already on the list.` : action === "add" ? `Added ${f.name}.` : `${f.name} marked not damaged.`
      );
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : "Couldn't save the decision. Try again.");
    } finally {
      setBusy(false);
    }
  };
  const later = () => {
    if (mode !== "suggestions" || !finding) return;
    const next = nextAfter(finding.key);
    if (next) setCur({ key: next.key, photo: null, index: next.index });
    else setAnnounce("This is the only suggestion left.");
  };
  const undo = async (key: string, name: string) => {
    if (!onDecide || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onDecide(key, "undo", true);
      setToast(null);
      setPinned(key);
      setEnded(null);
      setMode((m) => (m === "photo" ? m : "suggestions"));
      setCur({ key, photo: null, index: 0 });
      setAnnounce(`Undone: ${name} is a suggestion again.`);
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : "Couldn't undo. Try again.");
    } finally {
      setBusy(false);
    }
  };
  const openFinding = (f: Finding, photo: string | null) => {
    const nextMode = f.kind === "suggestion" ? "suggestions" : "listed";
    setMode(nextMode);
    setPinned(f.key);
    setEnded(null);
    setCur({ key: f.key, photo, index: 0 });
  };
  const showOnMap = (key: string) => {
    if (!onShowOnMap) return;
    onShowOnMap(key);
  };

  // ---- announcements -------------------------------------------------------
  const where =
    mode === "photo"
      ? info
        ? `Photo ${info.label}: ${onPhoto.length ? `${onPhoto.length} part${onPhoto.length === 1 ? "" : "s"} marked` : "no part marked"}.`
        : ""
      : finding
        ? `${finding.name}${info ? `, photo ${info.label}` : ""}: ${slide?.by === "link" ? linkPhotoCaption(finding) : slide ? boxLine(slide, { hidden }) : "no photo of this part"}.`
        : "";
  const newcomer = Boolean(arrived && finding && arrived.key === finding.key);
  const said = newcomer && where ? `${arrived!.text} ${where}` : where;
  useEffect(() => {
    if (said) setAnnounce(said);
  }, [said]);

  // ---- focus and keys ------------------------------------------------------
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    // No scroll: [Show on 3D] closes the viewer having just scrolled the
    // map into view, and a scrolling focus would jump back to the card.
    return () => opener?.focus({ preventScroll: true });
  }, []);

  // The toast's Undo can hold the focus; when the toast goes (undone, timed
  // out) the focus would drop to the page and Tab would leave the dialog.
  const focusInToast = useRef(false);
  useEffect(() => {
    if (toast || !focusInToast.current) return;
    focusInToast.current = false;
    if (!dialogRef.current?.contains(document.activeElement)) closeRef.current?.focus({ preventScroll: true });
  }, [toast]);

  const keys = useRef<(e: KeyboardEvent) => void>(() => {});
  keys.current = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey || isTypingTarget(e.target)) return;
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    // A held A / N / L repeats: after the first decision the cursor is on
    // the next card, and the repeat would decide that one unseen.
    if (e.repeat && (k === "a" || k === "n" || k === "l")) {
      e.preventDefault();
      return;
    }
    const actions: Record<string, (() => void) | null> = {
      ArrowLeft: () => stepPhoto(-1),
      ArrowRight: () => stepPhoto(1),
      ArrowUp: () => stepFinding(-1),
      ArrowDown: () => stepFinding(1),
      a: canDecide ? () => void decideHere("add") : null,
      n: canDecide ? () => void decideHere("dismiss") : null,
      l: mode === "suggestions" ? later : null,
      z: zoomToTheBox,
      f: () => setZoomAt(1),
      b: () => setShowBoxes((v) => !v),
    };
    const act = actions[k];
    if (!act) return;
    e.preventDefault();
    act();
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  /** Tab and Shift+Tab go round inside the dialog. */
  const trapFocus = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Tab" || !dialogRef.current) return;
    const items = [...dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])")].filter(
      (el) => el.offsetParent !== null || el === document.activeElement
    );
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && (document.activeElement === first || !dialogRef.current.contains(document.activeElement))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  // ---- the parts -----------------------------------------------------------
  const canvasStyle = (natural ? { "--ev-ratio": natural.w / natural.h, "--ev-zoom": zoom } : undefined) as CSSProperties | undefined;
  const photoIndex = mode === "photo" ? photoList.indexOf(photoRef || "") : usable.findIndex((s) => norm(s.photo) === photoRef);
  const photoCount = mode === "photo" ? photoList.length : usable.length;

  const selectOnPhoto = (key: string) => {
    const f = onPhoto.find((x) => x.f.key === key)?.f;
    if (f) openFinding(f, photoRef);
  };
  // The arrows sit over the stage, not in it: the stage scrolls when zoomed.
  const stagePart = done ? (
    <div className="ev-stage-wrap">
      <div className="ev-stage ev-stage-empty" ref={stageRef}>
        <span>All suggestions decided.</span>
      </div>
    </div>
  ) : (
    <div className="ev-stage-wrap">
      <div
        className={`ev-stage ${zoom > 1 ? "is-zoomed" : ""}`}
        ref={stageRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        {src ? (
          <div ref={canvasRef} className={`ev-canvas ${natural ? "is-sized" : ""}`} style={canvasStyle}>
            <PhotoBoxes
              src={src}
              alt={finding ? `${finding.name}, ${info?.label ?? "photo"}` : info?.label || "Photo"}
              fit="natural"
              className="pb-fill"
              marks={marks}
              selectedKey={finding?.key ?? null}
              showBoxes={showBoxes}
              onSelectMark={mode === "photo" ? selectOnPhoto : undefined}
            />
          </div>
        ) : (
          <div className="ev-nophoto">{finding ? (finding.hidden ? hiddenNoPhotoText(finding) : "No photo of this part.") : "No photo."}</div>
        )}
      </div>
      {photoCount > 1 && (
        <>
          <button type="button" className="rv-viewer-nav prev" disabled={photoIndex <= 0} onClick={() => stepPhoto(-1)} aria-label="Previous photo">
            <ChevronLeft style={{ width: 20, height: 20 }} />
          </button>
          <button type="button" className="rv-viewer-nav next" disabled={photoIndex >= photoCount - 1} onClick={() => stepPhoto(1)} aria-label="Next photo">
            <ChevronRight style={{ width: 20, height: 20 }} />
          </button>
        </>
      )}
    </div>
  );

  const toolbar = !done && src && (
    <div className="ev-toolbar" role="toolbar" aria-label="Zoom and boxes">
      {ZOOMS.map((z) => (
        <button
          key={z}
          type="button"
          className="ev-tool"
          data-ev-zoom={z}
          aria-pressed={zoom === z}
          disabled={!natural}
          onClick={() => setZoomAt(z)}
          title={z === 1 ? "Fit the photo (F)" : `Zoom ${z}x`}
        >
          {z === 1 ? "Fit" : `${z}x`}
        </button>
      ))}
      <button
        type="button"
        className="ev-tool"
        data-ev-zoom="box"
        disabled={!boxForZoom || !natural}
        aria-pressed={zoom > 1 && !ZOOMS.includes(zoom as 1 | 2 | 4)}
        onClick={zoomToTheBox}
        title="Zoom to the box (Z)"
      >
        Zoom to box
      </button>
      <button type="button" className="ev-tool" data-ev-boxes aria-pressed={showBoxes} onClick={() => setShowBoxes((v) => !v)} title="Boxes on or off (B)">
        <BoxIcon style={{ width: 13, height: 13 }} aria-hidden="true" /> Box {showBoxes ? "on" : "off"}
      </button>
    </div>
  );

  const chips = mode !== "photo" && slides.length > 0 && (
    <div className="ev-chips" role="group" aria-label="Photos">
      {slides.map((s) => {
        // A photo deleted from a draft has no label left: no raw id either.
        const label = s.info?.label || "Photo";
        const active = s.usable && norm(s.photo) === photoRef;
        return s.usable ? (
          <button
            key={s.photo}
            type="button"
            className={`ev-chip ${active ? "is-active" : ""}`}
            aria-pressed={active}
            onClick={() => setCur((c) => ({ ...c, photo: s.photo }))}
          >
            {label} <span className="ev-chip-kind">{s.by === "link" ? "listed part" : boxChip(s)}</span>
          </button>
        ) : (
          <span key={s.photo} className="ev-chip is-gone" title="This photo was deleted">
            {label} <span className="ev-chip-kind">deleted</span>
          </span>
        );
      })}
    </div>
  );

  let side: ReactNode;
  if (done) {
    side = (
      <div className="ev-done">
        <p>Every suggestion here is decided. Undo is in the panel&apos;s Decided list.</p>
        <button type="button" className="btn-primary-modern" onClick={onClose}>
          Close
        </button>
      </div>
    );
  } else if (mode === "photo") {
    side = (
      <>
        <div className="ev-caption">On this photo</div>
        {onPhoto.length ? (
          <ul className="ev-on-photo">
            {onPhoto.map(({ f, s }) => (
              <li key={f.key}>
                <span className={`ev-glyph ev-status-${f.status}`} aria-hidden="true">
                  {STATUS_GLYPH[f.status] || "•"}
                </span>
                <span className="ev-on-photo-name">
                  {f.name}
                  <span className="intake-muted"> · {f.kind === "suggestion" ? "suggestion" : "listed"} · {s.box && s.kind ? BOX_WORDS[s.kind] : "no box"}</span>
                </span>
                <button type="button" className="intake-link" onClick={() => openFinding(f, photoRef)}>
                  Look
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="intake-muted">No part is marked on this photo.</p>
        )}
        {info?.area && <div className="intake-muted">Area: {info.area}</div>}
      </>
    );
  } else if (finding) {
    const reason = finding.reason ? aiSays(finding.reason) : null;
    // The linked check's words show once, on its "AI check" line.
    const slideReason =
      slide?.reason && slide.reason !== finding.reason && !(slide.by === "link_check" && slide.reason === finding.linkCheck?.reason) ? slide.reason : null;
    const isLink = finding.source === "link";
    const bucket = isLink ? linkBucket(finding) : null;
    const linkLine = linkCheckLine(finding.linkCheck);
    // A photo sorter suggestion's intact_in is why it is there (sourceLabel /
    // codeReason say it), not a warning -- as on its card.
    const intact = finding.kind === "suggestion" && finding.source !== "sorter" ? intactText(finding.intactIn, photos) : null;
    // K4: the car map's group, "if fitted", a second hop and the yes signs.
    const linkFacts = [finding.assemblyName ?? null, hopText(finding), sideFromText(finding), alsoLinkedText(finding)]
      .filter(Boolean)
      .join(" · ");
    const fitted = isLink ? fittedText(finding) : null;
    const parent = finding.parents?.[0] ?? null;
    const facts = [
      finding.kind === "suggestion" ? sourceLabel(finding) : null,
      finding.kind === "suggestion" ? foundByText(finding, reads) : foundByNames(finding) && `Found by: ${foundByNames(finding)}`,
      confidenceText(finding.confidence),
    ]
      .filter(Boolean)
      .join(" · ");
    const verdict = checkWords(finding.check);
    side = (
      <>
        {newcomer && <div className="ev-note ev-arrived">{arrived!.text}</div>}
        <div className="ev-name">
          <span>{finding.name}</span>
          {fitted && <span className="lk-fitted">{fitted}</span>}
          {finding.condition && <span className="intake-chip">{finding.condition}</span>}
        </div>
        <div className={`ev-status ev-status-${finding.status}`}>{STATUS_TEXT[finding.status]}</div>
        {isLink && (
          <div className="lk-ev-head">
            <div className="ev-facts">{linkGroupTitle({ key: finding.linkedFrom?.[0] ?? "", parent, hit: finding.hit ?? null })}</div>
            <div className="lk-ev-tags">
              {bucket && <span className={`lk-bucket lk-bucket-${bucket}`}>{BUCKET_LABELS[bucket]}</span>}
              {finding.hidden && <span className="intake-chip lk-hidden">Hidden</span>}
            </div>
            {confirmAtStripDown(finding) && <p className="lk-strip">{STRIP_DOWN_TEXT}</p>}
            {(finding.signsYes ?? []).length > 0 && (
              <div className="lk-signs" aria-label="Signs seen">
                {(finding.signsYes ?? []).map((sign) => (
                  <span key={sign.id} className="lk-sign">
                    Sign: {signText(sign, photos)}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
        {finding.kind === "item" && <div className="ev-caption">{finding.linkedFrom?.length ? "Why it was suggested" : "Why the AI listed it"}</div>}
        <p className={`ev-reason ${reason ? "" : "is-code"}`}>{reason ?? codeReason(finding)}</p>
        {linkLine && <div className={`ev-check lk-check-${finding.linkCheck?.verdict}`}>{linkLine}</div>}
        {slideReason && <p className="ev-reason">On this photo: {aiSays(slideReason)}</p>}
        {linkFacts && <div className="ev-facts">{linkFacts}</div>}
        {facts && <div className="ev-facts">{facts}</div>}
        {verdict && (
          <div className={`ev-check ev-check-${finding.check?.verdict}`}>
            {verdict}
            {finding.check?.reason ? ` — ${aiSays(finding.check.reason)}` : ""}
          </div>
        )}
        {intact && <div className="ev-note">{intact}</div>}
        {finding.otherSideListed && (
          <div className="ev-note">The list already has {finding.otherSideListed}. Check this isn&apos;t the same damage on the other side.</div>
        )}
        <div className="ev-caption">Photos</div>
        {chips || <p className="intake-muted">No photo of this part.</p>}
        {slide &&
          (slide.by === "link" ? (
            <div className="ev-boxline lk-caption">
              <strong>{linkPhotoCaption(finding)}</strong>
              <span className="intake-muted"> · no box: this part isn&apos;t marked on it</span>
            </div>
          ) : (
            <div className="ev-boxline">
              <strong>{boxLine(slide, { hidden, boxesOff: !showBoxes })}</strong>
              {slide.by && <span className="intake-muted"> · from the {sourceName(slide.by)}</span>}
            </div>
          ))}
      </>
    );
  }

  const actions = !done && (
    <div className="ev-actions">
      {canDecide && finding && (
        <>
          <button type="button" className="btn-primary-modern ev-act" disabled={busy} onClick={() => void decideHere("add")} aria-keyshortcuts="A">
            <Check style={{ width: 14, height: 14 }} /> <kbd>A</kbd> Add
          </button>
          <button type="button" className="btn-secondary-modern ev-act" disabled={busy} onClick={() => void decideHere("dismiss")} aria-keyshortcuts="N">
            <X style={{ width: 14, height: 14 }} /> <kbd>N</kbd> Not damaged
          </button>
          <button type="button" className="btn-secondary-modern ev-act ev-act-later" disabled={busy} onClick={later} aria-keyshortcuts="L">
            <Clock style={{ width: 14, height: 14 }} /> <kbd>L</kbd> Later
          </button>
        </>
      )}
      {mode === "suggestions" && finding?.status === "dismissed" && !readOnly && onDecide && (
        <button type="button" className="btn-secondary-modern ev-act" disabled={busy} onClick={() => void undo(finding.key, finding.name)}>
          <Undo2 style={{ width: 14, height: 14 }} /> Undo &quot;Not damaged&quot;
        </button>
      )}
      {onShowOnMap && finding && (
        <button type="button" className="btn-secondary-modern ev-act" onClick={() => showOnMap(finding.key)}>
          <BoxIcon style={{ width: 14, height: 14 }} /> Show on 3D
        </button>
      )}
      {onAddFromPhoto && !readOnly && mode === "photo" && photoRef && src && (
        <button type="button" className="btn-secondary-modern ev-act" onClick={() => onAddFromPhoto(photoRef)} title="Add a damaged part this photo shows">
          <Plus style={{ width: 14, height: 14 }} /> Add part seen here
        </button>
      )}
    </div>
  );

  const count =
    mode === "photo"
      ? `Photo ${info?.label ?? ""}${photoList.length > 1 ? ` · ${photoIndex + 1} of ${photoList.length}` : ""}`
      : done
        ? "Suggestions"
        : `${mode === "suggestions" ? "Suggestion" : "Listed part"} ${at + 1} of ${walk.length}`;
  const keyHelp =
    mode === "suggestions"
      ? `${canDecide ? "A add · N not damaged · L later · " : ""}← → photos · ↑ ↓ suggestions · Z zoom to box · B boxes · Esc close`
      : mode === "listed"
        ? "← → photos · ↑ ↓ parts · Z zoom to box · B boxes · Esc close"
        : "← → photos · B boxes · Esc close";

  return createPortal(
    <div className="rv-modal ev-modal" onClick={onClose}>
      <div
        ref={dialogRef}
        className="ev-body"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={trapFocus}
        onFocus={(e) => {
          focusInToast.current = Boolean((e.target as HTMLElement).closest?.(".ev-toast"));
        }}
      >
        <div className="ev-main">
          {stagePart}
          {toolbar}
        </div>
        <aside className="ev-side">
          <div className="ev-head">
            <strong id={titleId} className="ev-count">
              {count}
            </strong>
            <button ref={closeRef} type="button" className="intake-icon-btn" onClick={onClose} aria-label="Close">
              <X style={{ width: 16, height: 16 }} />
            </button>
          </div>
          {readOnly && mode === "suggestions" && <div className="intake-muted">Read-only: suggestions can&apos;t be decided here.</div>}
          {side}
          {error && (
            <div className="sg-error" role="alert">
              {error}
            </div>
          )}
          {src && !done && (
            <a className="rv-viewer-open" href={info?.original || src} target="_blank" rel="noreferrer">
              <ExternalLink style={{ width: 13, height: 13 }} /> {info?.original ? "Open original" : "Open the photo in a new tab"}
            </a>
          )}
          <div className="ev-keys">{keyHelp}</div>
          {actions}
        </aside>
        <div className="ev-sr-only" aria-live="polite" role="status">
          {announce}
        </div>
        {/* Inside the dialog, so Tab reaches its Undo (the focus trap goes
            round the dialog only); it is fixed to the screen all the same. */}
        {toast && <DecisionToast toast={toast} busy={busy} onUndo={() => void undo(toast.key, toast.name)} onDismiss={() => setToast(null)} />}
      </div>
    </div>,
    document.body
  );
}
