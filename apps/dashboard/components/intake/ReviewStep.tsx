"use client";

import dynamic from "next/dynamic";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { AlertTriangle, ArrowLeft, ArrowRight, ImagePlus, Plus, Trash2 } from "lucide-react";
import {
  ACCIDENT_TYPE_OPTIONS,
  ROAD_OPTIONS,
  TRAFFIC_OPTIONS,
  WEATHER_OPTIONS,
  fileUrl,
  type DamageSummaryItem,
  type ReportData,
  type Taxonomy,
  type VehicleInfo,
} from "@/lib/api";
import { severityClass } from "@/lib/caseFields";
import { DOCUMENT_KINDS, PHOTO_KIND_LABELS, photoForReference, type IntakePhoto } from "@/lib/intake";
import { incidentTypeOptions } from "@/lib/intakeCategories";
import { bboxStyle } from "@/lib/intakeReview";
import { DamagePhotoViewer, type ViewerSlide } from "./DamagePhotoViewer";
import { PhotoPicker } from "./PhotoPicker";
import { PhotosByPart } from "./PhotosByPart";

// Three.js/WebGL only exists client-side (same as CaseInspectionTab).
const VehicleBlueprint3D = dynamic(() => import("@/components/VehicleBlueprint3D").then((m) => m.VehicleBlueprint3D), {
  ssr: false,
  loading: () => <div className="blueprint-stage-3d blueprint-3d-loading">Loading 3D preview…</div>,
});

type Props = {
  data: ReportData;
  onChange: (update: (d: ReportData) => ReportData) => void;
  photos: IntakePhoto[];
  readPhotoIds: string[];
  taxonomy: Taxonomy;
  aiSkipped: boolean;
  onBack: () => void;
  onNext: () => void;
};

/** A select that still shows a value outside its option list (an older or
 * unexpected AI answer) instead of silently snapping to blank. */
function Select({ value, options, onChange, placeholder }: { value: string; options: readonly string[]; onChange: (v: string) => void; placeholder: string }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{placeholder}</option>
      {value && !options.includes(value) && <option value={value}>{value}</option>}
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

/** What the photo viewer is walking: the damage items that have a photo,
 * or one Photos by part group. */
type ViewerState = { kind: "damage"; key: string } | { kind: "photos"; title: string; ids: string[]; id: string };

/** Where to bring a damage row into view once it has rendered. */
type Reveal = { key: string; block: ScrollLogicalPosition; focusPart: boolean };

const FLASH_MS = 2000;

export function ReviewStep({ data, onChange, photos, readPhotoIds, taxonomy, aiSkipped, onBack, onNext }: Props) {
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const [flashKey, setFlashKey] = useState<string | null>(null);
  const [newPart, setNewPart] = useState("");
  const [pickerFor, setPickerFor] = useState<string | null>(null);
  const [viewer, setViewer] = useState<ViewerState | null>(null);
  const incidentHeadingId = useId();
  const v: VehicleInfo = data.vehicle_info || {};
  const damage = data.damage_summary || [];

  // Client-only row keys, so editing or removing an item never remounts a
  // different row (an index key shifts every row below a removed one). Kept
  // in a WeakMap off the items themselves -- nothing here is ever saved.
  const itemKeys = useRef(new WeakMap<DamageSummaryItem, string>());
  const keyCounter = useRef(0);
  const keyFor = (item: DamageSummaryItem) => {
    let k = itemKeys.current.get(item);
    if (!k) {
      keyCounter.current += 1;
      k = `item-${keyCounter.current}`;
      itemKeys.current.set(item, k);
    }
    return k;
  };
  /** An edit makes a new item object -- it keeps the old one's key. */
  const carryKey = (from: DamageSummaryItem, to: DamageSummaryItem) => {
    const k = itemKeys.current.get(from);
    if (k) itemKeys.current.set(to, k);
    return to;
  };
  const seenKeys = new Set<string>();
  const rowKeys = damage.map((item, i) => {
    const k = keyFor(item);
    // The same object twice in the list would otherwise share a key.
    const unique = seenKeys.has(k) ? `${k}-${i}` : k;
    seenKeys.add(unique);
    return unique;
  });

  // A non-motor category the draft held stays on screen after being
  // unticked, so the surveyor can tick it back.
  const heldCategories = useRef<string[]>([]);
  for (const c of data.category || []) if (!heldCategories.current.includes(c)) heldCategories.current.push(c);
  const incidentTypes = incidentTypeOptions(heldCategories.current);

  const rowEls = useRef(new Map<string, HTMLDivElement>());
  const pendingReveal = useRef<Reveal | null>(null);
  const flashTimer = useRef<number | undefined>(undefined);

  /** Scrolls a damage row into view and outlines it for a moment. */
  const revealRow = ({ key, block, focusPart }: Reveal): boolean => {
    const row = rowEls.current.get(key);
    if (!row) return false;
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    row.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block });
    if (focusPart) row.querySelector<HTMLSelectElement>(".intake-damage-fields select")?.focus({ preventScroll: true });
    setFlashKey(key);
    window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlashKey(null), FLASH_MS);
    return true;
  };

  // A row added or changed from Photos by part only exists after the next
  // render -- reveal it then.
  useEffect(() => {
    const pending = pendingReveal.current;
    if (pending && revealRow(pending)) pendingReveal.current = null;
  });
  useEffect(() => () => window.clearTimeout(flashTimer.current), []);

  const sources = useMemo(
    () => photos.filter((p) => readPhotoIds.includes(p.id) && p.kind && DOCUMENT_KINDS.includes(p.kind)),
    [photos, readPhotoIds]
  );
  const lowConfidence = damage.filter((d) => d.ai_confidence === "Low" && !d.human_verified).length;
  const vehicleName = [v.make, v.model].filter(Boolean).join(" ");

  const set = (patch: Partial<ReportData>) => onChange((d) => ({ ...d, ...patch }));
  const setVehicle = (patch: Partial<VehicleInfo>) => onChange((d) => ({ ...d, vehicle_info: { ...(d.vehicle_info || {}), ...patch } }));
  const setItem = (index: number, patch: Partial<DamageSummaryItem>) =>
    onChange((d) => ({
      ...d,
      // Any edit counts as the surveyor confirming the item.
      damage_summary: (d.damage_summary || []).map((it, i) => (i === index ? carryKey(it, { ...it, ...patch, human_verified: true }) : it)),
    }));
  const removeItem = (index: number) => {
    setHighlighted(null);
    onChange((d) => ({ ...d, damage_summary: (d.damage_summary || []).filter((_, i) => i !== index) }));
  };
  const pushItem = (item: DamageSummaryItem, reveal: Omit<Reveal, "key">) => {
    pendingReveal.current = { key: keyFor(item), ...reveal };
    onChange((d) => ({ ...d, damage_summary: [...(d.damage_summary || []), item] }));
  };
  const addItem = () => {
    if (!newPart) return;
    pushItem({ part: newPart, severity: null, human_verified: true }, { block: "nearest", focusPart: false });
    setNewPart("");
  };
  /** "Add as damage" from Photos by part. An area-only group can't say which
   * part it shows, so that item opens with its part select focused. */
  const addFromPhotos = (part: string, photoId: string) =>
    pushItem(
      {
        part,
        damage_type: null,
        severity: null,
        photo_reference: `@${photoId}`,
        bbox_2d: null,
        ai_confidence: null,
        human_verified: true,
      },
      { block: "center", focusPart: !part }
    );
  /** Links a photo to an item, or unlinks it. Any box was drawn on the old photo. */
  const setItemPhoto = (index: number, photoId: string | null) =>
    setItem(index, { photo_reference: photoId ? `@${photoId}` : null, bbox_2d: null });
  const attachFromPhotos = (index: number, photoId: string) => {
    if (rowKeys[index]) pendingReveal.current = { key: rowKeys[index], block: "center", focusPart: false };
    setItemPhoto(index, photoId);
  };
  const toggleCategory = (c: string) =>
    onChange((d) => {
      const cats = new Set(d.category || []);
      if (cats.has(c)) cats.delete(c);
      else cats.add(c);
      return { ...d, category: Array.from(cats) };
    });

  const pickerIndex = pickerFor ? rowKeys.indexOf(pickerFor) : -1;
  const pickerItem = pickerIndex >= 0 ? damage[pickerIndex] : null;
  const pickerPhoto = pickerItem ? (photoForReference(pickerItem.photo_reference, photos) ?? null) : null;

  let viewerSlides: ViewerSlide[] = [];
  let viewerIndex = -1;
  if (viewer?.kind === "damage") {
    viewerSlides = damage.flatMap((item, i) => {
      const photo = photoForReference(item.photo_reference, photos);
      if (!photo) return [];
      return [{ key: rowKeys[i], photo, damage: { part: item.part, damageType: item.damage_type, severity: item.severity, bbox: item.bbox_2d } }];
    });
    viewerIndex = viewerSlides.findIndex((s) => s.key === viewer.key);
  } else if (viewer?.kind === "photos") {
    viewerSlides = viewer.ids.flatMap((id) => {
      const photo = photos.find((p) => p.id === id);
      return photo ? [{ key: id, photo, heading: viewer.title }] : [];
    });
    viewerIndex = viewerSlides.findIndex((s) => s.key === viewer.id);
  }

  const vehicleField = (label: string, key: keyof VehicleInfo, placeholder = "") => (
    <label>
      {label}
      <input
        type="text"
        placeholder={placeholder}
        value={(v[key] as string | null | undefined) ?? ""}
        onChange={(e) => setVehicle({ [key]: e.target.value } as Partial<VehicleInfo>)}
      />
    </label>
  );

  return (
    <div className="intake-review-layout">
      <div className="intake-col">
        <section className="card intake-panel">
          <div className="intake-panel-head">
            <h2>Vehicle</h2>
            {aiSkipped && <span className="intake-chip warn">Filled in by hand</span>}
          </div>
          {sources.length > 0 && (
            <div className="intake-sources">
              <span className="intake-muted">Read from</span>
              {sources.map((p) => (
                <a key={p.id} className="intake-source-chip" href={fileUrl(p.preview_url)} target="_blank" rel="noreferrer" title={p.note || p.name}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={fileUrl(p.thumb_url)} alt="" />
                  {p.label} · {p.kind ? PHOTO_KIND_LABELS[p.kind] : ""}
                </a>
              ))}
            </div>
          )}
          <div className="intake-field-grid">
            {vehicleField("Plate number", "plate_number", "e.g. SLK 3063 Z")}
            {vehicleField("Make", "make", "e.g. Honda")}
            {vehicleField("Model", "model", "e.g. Vezel 1.5X")}
            {vehicleField("Year", "year")}
            {vehicleField("VIN / Chassis no.", "vin", "From the log card")}
            {vehicleField("Engine no.", "engine_number")}
            {vehicleField("Colour", "color")}
            {vehicleField("Odometer", "odometer_reading", "e.g. 015,287 km")}
            {vehicleField("Registration date", "date_of_registration")}
            <label>
              Body type
              <Select value={v.body_type ?? ""} options={taxonomy.body_types} placeholder="Auto from make/model" onChange={(val) => setVehicle({ body_type: val || null })} />
            </label>
          </div>
        </section>

        <section className="card intake-panel">
          <div className="intake-panel-head">
            <h2>Incident</h2>
          </div>
          <div className="intake-field-grid">
            <label>
              Date &amp; time
              <input type="text" placeholder="e.g. 2026-10-18 08:40" value={data.incident_datetime ?? ""} onChange={(e) => set({ incident_datetime: e.target.value })} />
            </label>
            <label>
              Location
              <input type="text" placeholder="e.g. PIE exit 12" value={data.location ?? ""} onChange={(e) => set({ location: e.target.value })} />
            </label>
            <label>
              Accident type
              <Select value={data.accident_type ?? ""} options={ACCIDENT_TYPE_OPTIONS} placeholder="Select…" onChange={(val) => set({ accident_type: val || null })} />
            </label>
            <label>
              Overall severity
              <Select value={data.severity_level ?? ""} options={taxonomy.severities} placeholder="Not assessed" onChange={(val) => set({ severity_level: val || null })} />
            </label>
            <label>
              Point of impact
              <input type="text" placeholder="e.g. Rear centre" value={v.point_of_impact ?? ""} onChange={(e) => setVehicle({ point_of_impact: e.target.value })} />
            </label>
            <label>
              Weather
              <Select value={data.weather_condition ?? ""} options={WEATHER_OPTIONS} placeholder="Select…" onChange={(val) => set({ weather_condition: val || null })} />
            </label>
            <label>
              Road
              <Select value={data.road_condition ?? ""} options={ROAD_OPTIONS} placeholder="Select…" onChange={(val) => set({ road_condition: val || null })} />
            </label>
            <label>
              Traffic
              <Select value={data.traffic_condition ?? ""} options={TRAFFIC_OPTIONS} placeholder="Select…" onChange={(val) => set({ traffic_condition: val || null })} />
            </label>
          </div>
          <label className="intake-field-wide">
            Description <span className="intake-optional">{aiSkipped ? "" : "(AI draft — edit freely)"}</span>
            <textarea rows={5} value={data.description ?? ""} onChange={(e) => set({ description: e.target.value })} placeholder="What happened, in loss-adjuster English." />
          </label>
          <div role="group" aria-labelledby={incidentHeadingId}>
            <div id={incidentHeadingId} className="rv-subhead">
              Incident type
            </div>
            <div className="intake-category-row">
              {incidentTypes.map((c) => (
                <label key={c.value} className="checkbox-label" title={c.motor ? undefined : "From the AI draft — not one of the motor incident types"}>
                  <input type="checkbox" checked={(data.category || []).includes(c.value)} onChange={() => toggleCategory(c.value)} /> {c.label}
                </label>
              ))}
            </div>
          </div>
        </section>
      </div>

      <div className="intake-col">
        <section className="card intake-panel">
          <div className="intake-panel-head">
            <h2>Damage found ({damage.length})</h2>
            {lowConfidence > 0 && (
              <span className="intake-chip warn">
                <AlertTriangle style={{ width: 12, height: 12 }} /> {lowConfidence} low-confidence item{lowConfidence === 1 ? "" : "s"} to check
              </span>
            )}
          </div>
          {damage.length === 0 && <div className="intake-empty">No damage recorded yet. Add the damaged parts below.</div>}
          <div className="intake-damage-list">
            {damage.map((item, i) => {
              const key = rowKeys[i];
              const photo = photoForReference(item.photo_reference, photos);
              const box = bboxStyle(item.bbox_2d);
              const partName = item.part || "this item";
              return (
                <div
                  key={key}
                  ref={(el) => {
                    if (el) rowEls.current.set(key, el);
                    else rowEls.current.delete(key);
                  }}
                  className={`intake-damage-item ${highlighted === i ? "highlighted" : ""} ${flashKey === key ? "rv-flash" : ""}`}
                  onMouseEnter={() => setHighlighted(i)}
                >
                  <div className="intake-damage-photo">
                    {photo ? (
                      <>
                        <button
                          type="button"
                          className="rv-photo-btn"
                          onClick={() => setViewer({ kind: "damage", key })}
                          title={photo.note ? `${photo.label} — ${photo.note}` : `View ${photo.label}`}
                          aria-label={`View ${photo.label} for ${partName}`}
                        >
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={fileUrl(photo.thumb_url)} alt="" />
                          {box && <span className="intake-bbox" style={box} />}
                          <span className="intake-tile-label">{photo.label}</span>
                        </button>
                        <div className="rv-photo-actions">
                          <button type="button" className="intake-link" onClick={() => setPickerFor(key)} aria-label={`Change the photo for ${partName}`}>
                            Change
                          </button>
                          <button type="button" className="intake-link" onClick={() => setItemPhoto(i, null)} aria-label={`Remove the photo from ${partName}`}>
                            Remove
                          </button>
                        </div>
                      </>
                    ) : (
                      <button type="button" className="intake-damage-nophoto rv-attach-btn" onClick={() => setPickerFor(key)}>
                        <ImagePlus style={{ width: 16, height: 16 }} />
                        Attach photo
                      </button>
                    )}
                  </div>
                  <div className="intake-damage-fields">
                    <Select value={item.part} options={taxonomy.parts} placeholder="Part…" onChange={(val) => setItem(i, { part: val })} />
                    <div className="intake-damage-row">
                      <Select value={item.damage_type ?? ""} options={taxonomy.damage_types} placeholder="Damage type…" onChange={(val) => setItem(i, { damage_type: val || null })} />
                      <Select value={item.severity ?? ""} options={taxonomy.severities} placeholder="Severity…" onChange={(val) => setItem(i, { severity: val || null })} />
                    </div>
                    <div className="intake-damage-meta">
                      {!item.part && <span className="intake-chip warn">Choose the part</span>}
                      <span className={`chip-severity ${severityClass(item.severity)}`}>{item.severity || "Unrated"}</span>
                      {item.ai_confidence && !item.human_verified && <span className={`intake-chip ${item.ai_confidence === "Low" ? "warn" : ""}`}>AI confidence: {item.ai_confidence}</span>}
                      {item.human_verified && <span className="intake-chip ok">Checked</span>}
                      {!item.human_verified && (
                        <button type="button" className="intake-link" onClick={() => setItem(i, {})}>
                          Mark checked
                        </button>
                      )}
                    </div>
                  </div>
                  <button type="button" className="intake-icon-btn danger" onClick={() => removeItem(i)} aria-label={`Remove ${partName}`}>
                    <Trash2 style={{ width: 14, height: 14 }} />
                  </button>
                </div>
              );
            })}
          </div>
          <div className="intake-add-row">
            <Select value={newPart} options={taxonomy.parts} placeholder="Add a damaged part…" onChange={setNewPart} />
            <button type="button" className="btn-secondary-modern" onClick={addItem} disabled={!newPart}>
              <Plus style={{ width: 14, height: 14 }} /> Add
            </button>
          </div>
        </section>

        <PhotosByPart
          photos={photos}
          damage={damage}
          readPhotoIds={readPhotoIds}
          onAddDamage={addFromPhotos}
          onAttachPhoto={attachFromPhotos}
          onOpenPhoto={(group, id) => setViewer({ kind: "photos", title: group.title, ids: group.photos.map((p) => p.id), id })}
        />

        <section className="card intake-panel">
          <div className="intake-panel-head">
            <h2>3D preview</h2>
            <span className="intake-muted">{vehicleName || "Vehicle"}</span>
          </div>
          <VehicleBlueprint3D
            damageEntries={damage}
            onHotspotClick={(idx) => {
              setHighlighted(idx);
              if (rowKeys[idx]) revealRow({ key: rowKeys[idx], block: "center", focusPart: false });
            }}
            highlightedDamageIndex={highlighted}
            vehicleName={vehicleName}
            bodyType={v.body_type}
          />
        </section>
      </div>

      <div className="intake-footer intake-review-footer">
        <button type="button" className="btn-secondary-modern" onClick={onBack}>
          <ArrowLeft style={{ width: 14, height: 14 }} /> Back
        </button>
        <button type="button" className="btn-primary-modern" onClick={onNext}>
          Next: parties &amp; claim <ArrowRight style={{ width: 14, height: 14 }} />
        </button>
      </div>

      {pickerItem && (
        <PhotoPicker
          photos={photos}
          part={pickerItem.part}
          current={pickerPhoto}
          readPhotoIds={readPhotoIds}
          onPick={(id) => {
            // Picking the photo it already has keeps its damage box.
            if (pickerPhoto?.id !== id) setItemPhoto(pickerIndex, id);
            setPickerFor(null);
          }}
          onClose={() => setPickerFor(null)}
        />
      )}

      {viewer && viewerIndex >= 0 && (
        <DamagePhotoViewer
          slides={viewerSlides}
          index={viewerIndex}
          title={viewer.kind === "damage" ? "Damage photos" : `Photos by part · ${viewer.title}`}
          onNavigate={(i) => {
            const next = viewerSlides[i];
            if (!next) return;
            setViewer(viewer.kind === "damage" ? { kind: "damage", key: next.key } : { ...viewer, id: next.key });
          }}
          onClose={() => setViewer(null)}
        />
      )}
    </div>
  );
}
