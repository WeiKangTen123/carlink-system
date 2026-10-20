"use client";

import dynamic from "next/dynamic";
import { useMemo, useState } from "react";
import { AlertTriangle, ArrowLeft, ArrowRight, FileText, Plus, Trash2 } from "lucide-react";
import {
  ACCIDENT_TYPE_OPTIONS,
  CATEGORY_OPTIONS,
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

function bboxStyle(bbox: number[] | null | undefined): React.CSSProperties | null {
  if (!bbox || bbox.length !== 4 || bbox.some((v) => typeof v !== "number" || Number.isNaN(v))) return null;
  const [yMin, xMin, yMax, xMax] = bbox;
  return { top: `${yMin / 10}%`, left: `${xMin / 10}%`, width: `${(xMax - xMin) / 10}%`, height: `${(yMax - yMin) / 10}%` };
}

export function ReviewStep({ data, onChange, photos, readPhotoIds, taxonomy, aiSkipped, onBack, onNext }: Props) {
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const [newPart, setNewPart] = useState("");
  const v: VehicleInfo = data.vehicle_info || {};
  const damage = data.damage_summary || [];
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
      damage_summary: (d.damage_summary || []).map((it, i) => (i === index ? { ...it, ...patch, human_verified: true } : it)),
    }));
  const removeItem = (index: number) => {
    setHighlighted(null);
    onChange((d) => ({ ...d, damage_summary: (d.damage_summary || []).filter((_, i) => i !== index) }));
  };
  const addItem = () => {
    if (!newPart) return;
    onChange((d) => ({ ...d, damage_summary: [...(d.damage_summary || []), { part: newPart, severity: null, human_verified: true }] }));
    setNewPart("");
  };
  const toggleCategory = (c: string) =>
    onChange((d) => {
      const cats = new Set(d.category || []);
      if (cats.has(c)) cats.delete(c);
      else cats.add(c);
      return { ...d, category: Array.from(cats) };
    });

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
          <div className="intake-category-row">
            {CATEGORY_OPTIONS.map((c) => (
              <label key={c} className="checkbox-label">
                <input type="checkbox" checked={(data.category || []).includes(c)} onChange={() => toggleCategory(c)} /> {c}
              </label>
            ))}
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
              const photo = photoForReference(item.photo_reference, photos);
              const box = bboxStyle(item.bbox_2d);
              return (
                <div
                  key={i}
                  className={`intake-damage-item ${highlighted === i ? "highlighted" : ""}`}
                  onMouseEnter={() => setHighlighted(i)}
                >
                  <div className="intake-damage-photo">
                    {photo ? (
                      <a href={fileUrl(photo.preview_url)} target="_blank" rel="noreferrer" title={`Open ${photo.label}`}>
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={fileUrl(photo.thumb_url)} alt={item.part} />
                        {box && <span className="intake-bbox" style={box} />}
                        <span className="intake-tile-label">{photo.label}</span>
                      </a>
                    ) : (
                      <div className="intake-damage-nophoto">
                        <FileText style={{ width: 16, height: 16 }} />
                        No photo linked
                      </div>
                    )}
                  </div>
                  <div className="intake-damage-fields">
                    <Select value={item.part} options={taxonomy.parts} placeholder="Part…" onChange={(val) => setItem(i, { part: val })} />
                    <div className="intake-damage-row">
                      <Select value={item.damage_type ?? ""} options={taxonomy.damage_types} placeholder="Damage type…" onChange={(val) => setItem(i, { damage_type: val || null })} />
                      <Select value={item.severity ?? ""} options={taxonomy.severities} placeholder="Severity…" onChange={(val) => setItem(i, { severity: val || null })} />
                    </div>
                    <div className="intake-damage-meta">
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
                  <button type="button" className="intake-icon-btn danger" onClick={() => removeItem(i)} aria-label={`Remove ${item.part}`}>
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

        <section className="card intake-panel">
          <div className="intake-panel-head">
            <h2>3D preview</h2>
            <span className="intake-muted">{vehicleName || "Vehicle"}</span>
          </div>
          <VehicleBlueprint3D
            damageEntries={damage}
            onHotspotClick={(idx) => setHighlighted(idx)}
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
    </div>
  );
}
