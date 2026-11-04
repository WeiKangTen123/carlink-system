"use client";

import React, { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Plus,
  Trash2,
  Save,
  RotateCcw,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Lightbulb,
  FileText,
  FileSpreadsheet,
  Sparkles,
  Eraser,
} from "lucide-react";
import {
  type ReportDetail,
  type ReportData,
  type DamageSummaryItem,
  type AnnexAPartItem,
  type AnnexBLabourItem,
  type AnnexCInfo,
  type CoverPageRequest,
  type LabourSuggestionLine,
  type LabourSuggestions,
  type SignOffRequest,
  type TyreCondition,
  type TyreItem,
  CLAIM_TYPE_OPTIONS,
  pdfDownloadUrl,
  xlsxDownloadUrl,
} from "@/lib/api";
import {
  getLabourSuggestionsAction,
  prefillAnnexAAction,
  saveAnnexAAction,
  saveAnnexBAction,
  saveAnnexCAction,
  saveCoverPageAction,
  signOffReportAction,
} from "@/app/reports/actions";
import { canViewAllCases, useCurrentUser } from "@/components/CurrentUser";
import { CaseAnalysisPanel } from "./CaseAnalysisPanel";
import { formatAmount, formatSgd, leadingNumber, sameLabourKey, sameLineKey } from "@/lib/pastReports";
import { AnnexSuggestionsPanel } from "./AnnexSuggestionsPanel";

// The tab mirrors the firm's own report workbook (example_case/*/CL *.xls:
// Cover Page, Annex A, Annex B, Annex C) and uses its wording. The GST
// lines, BOLA liability, "forensic" cards, seal hash and variance figures
// the dashboard used to add here were never on the firm's reports and went
// on 2026-11-01.

type SaveState = "idle" | "saving" | "saved" | "error";

// ---------------------------------------------------------------------------
// Annex A rows

/** An Annex A row while it is being edited: the item, a stable key for
 * React, and qty / estimate as typed (so "0.", "12.5" and "1set" survive
 * typing). */
type AnnexRow = AnnexAPartItem & { _key: string; _qty: string; _est: string };

let annexRowSeq = 0;

function toAnnexRow(item: AnnexAPartItem): AnnexRow {
  annexRowSeq += 1;
  return {
    ...item,
    _key: `annex-row-${annexRowSeq}`,
    _qty: item.qty_text || (item.qty ? String(item.qty) : ""),
    _est: item.workshop_est ? String(item.workshop_est) : "",
  };
}

/** The same sum the API does on save (PUT /reports/{id}/annex-a):
 * estimate x (1 - discount), no discount on nett lines, 0 with no estimate.
 * The estimate is the line total as the workshop quoted it (10 clips at 50
 * -> 40), so it is NOT multiplied by qty -- this used to, and so did the
 * API, which made every multi-piece line too big. */
function annexAdjustedCost(it: Pick<AnnexAPartItem, "workshop_est" | "discount_pct" | "is_net_item">): number {
  const est = it.workshop_est || 0;
  if (!est) return 0;
  const discount = it.is_net_item ? 0 : it.discount_pct || 0;
  return Math.round(est * (1 - discount) * 100) / 100;
}

/** Qty as typed: a plain number is the qty; anything else ("1set", "2 pcs")
 * is kept as qty_text, printed as written, with its leading number as the
 * qty the statistics use. Blank reads as 1, as the API does. */
function qtyFromText(text: string): { qty: number; qty_text: string | null } {
  const t = text.trim();
  if (!t) return { qty: 1, qty_text: null };
  if (/^\d+(\.\d+)?$/.test(t)) {
    const n = parseFloat(t);
    return { qty: n > 0 ? n : 1, qty_text: null };
  }
  return { qty: leadingNumber(t) || 1, qty_text: t };
}

/** The row as saved: editing-only fields dropped. */
function fromAnnexRow(row: AnnexRow): AnnexAPartItem {
  const { _key, _qty, _est, ...item } = row;
  const parsed = { ...item, ...qtyFromText(_qty) };
  return { ...parsed, adjusted_cost: annexAdjustedCost(parsed) };
}

/** List Items first, then S/Nett Items -- the order the firm prints, and
 * the order the table is saved in so the API's numbering matches what is
 * on screen. */
function inSections<T extends { is_net_item: boolean }>(rows: T[]): T[] {
  return [...rows.filter((r) => !r.is_net_item), ...rows.filter((r) => r.is_net_item)];
}

/** What a save would change -- to tell whether anything is unsaved. */
function annexSignature(items: AnnexAPartItem[]): string {
  return JSON.stringify(
    inSections(items).map((it) => [
      (it.part_name || "").trim(),
      (it.condition || "").trim(),
      it.action || "",
      it.qty > 0 ? it.qty : 1,
      it.qty_text || null,
      it.workshop_est || 0,
      it.is_net_item ? 0 : it.discount_pct,
      Boolean(it.is_net_item),
      it.oem_part_number || null,
      it.suggested_from || null,
      it.usual_unit_price ?? null,
      Boolean(it.prefilled),
    ])
  );
}

/** "1,250.50", "S$80" or "12" as typed; anything else counts as 0. */
function parseAmount(text: string): number {
  const n = parseFloat(text.replace(/[,\s]/g, "").replace(/^s?\$/i, ""));
  return Number.isFinite(n) && n > 0 ? n : 0;
}

function isBadAmount(text: string): boolean {
  const t = text.replace(/[,\s]/g, "").replace(/^s?\$/i, "");
  return t !== "" && !(Number.isFinite(Number(t)) && Number(t) >= 0);
}

/** A new line starts with no remark: the firm leaves most of that column
 * blank (this used to start every line as "Replace"). */
const BLANK_ANNEX_LINE: AnnexAPartItem = {
  item_no: "",
  part_name: "",
  condition: "",
  action: "",
  qty: 1,
  qty_text: null,
  workshop_est: 0,
  discount_pct: 0.2,
  adjusted_cost: 0,
  is_net_item: false,
  oem_part_number: null,
  prefilled: false,
};

/** The remarks the firm writes; any other wording can stay blank. */
const REMARK_OPTIONS = ["Replace", "Repair", "Straighten", "Disallow"];

const DISCOUNT_OPTIONS: [number, string][] = [
  [0.0, "0%"],
  [0.1, "10%"],
  [0.15, "15%"],
  [0.2, "20%"],
  [0.3, "30%"],
  [1.0, "100%"],
];

/** Conditions surveyors write most; any other wording can be typed. */
const CONDITION_SUGGESTIONS = ["Deformed/cut", "Grazed/cracked", "Necessary", "Serviceable"];

// ---------------------------------------------------------------------------
// Annex B rows

type LabourRow = AnnexBLabourItem & { _key: string; _est: string; _adj: string };

let labourRowSeq = 0;

function toLabourRow(item: AnnexBLabourItem): LabourRow {
  labourRowSeq += 1;
  return {
    ...item,
    _key: `labour-row-${labourRowSeq}`,
    _est: item.workshop_est ? String(item.workshop_est) : "",
    _adj: item.adjusted_cost ? String(item.adjusted_cost) : "",
  };
}

/** The row as saved. The adjusted cost is whatever the surveyor typed --
 * labour has no discount rule, the API keeps it as given. */
function fromLabourRow(row: LabourRow): AnnexBLabourItem {
  const { _key, _est, _adj, ...item } = row;
  return { ...item, workshop_est: parseAmount(_est), adjusted_cost: parseAmount(_adj) };
}

function labourSignature(items: AnnexBLabourItem[]): string {
  return JSON.stringify(
    items.map((it) => [
      (it.description || "").trim(),
      it.workshop_est || 0,
      it.adjusted_cost || 0,
      Boolean(it.prefilled),
      it.suggested_from || null,
      it.usual_price ?? null,
    ])
  );
}

const BLANK_LABOUR_LINE: AnnexBLabourItem = {
  item_no: "",
  description: "",
  workshop_est: 0,
  adjusted_cost: 0,
  justification: null,
  prefilled: false,
  suggested_from: null,
  usual_price: null,
};

/** Both columns summed over saved rows; what the Summary block and the
 * Annex C letter show. */
function sumRows(rows: { workshop_est: number; adjusted_cost: number }[]): { est: number; adj: number } {
  const est = rows.reduce((acc, r) => acc + (r.workshop_est || 0), 0);
  const adj = rows.reduce((acc, r) => acc + (r.adjusted_cost || 0), 0);
  return { est: Math.round(est * 100) / 100, adj: Math.round(adj * 100) / 100 };
}

// ---------------------------------------------------------------------------
// Cover Page form

type TyreForm = { nearside_mm: string; nearside_make: string; offside_mm: string; offside_make: string; size: string };

/** Every Cover Page field as text, as typed -- the firm's dates are free
 * text ("27th February 2023 (10.15am)") and so are the readings. */
type CoverForm = {
  report_id: string;
  date_of_request: string;
  requested_by: string;
  requested_by_address: string;
  claim_type: string;
  workshop_assigned: string;
  workshop_address: string;
  incident_datetime: string;
  date_of_survey: string;
  date_of_dismantle: string;
  date_before_painting: string;
  date_after_painting: string;
  plate_number: string;
  make: string;
  model: string;
  date_of_registration: string;
  engine_number: string;
  vin: string;
  odometer_reading: string;
  transmission: string;
  color: string;
  steering_condition: string;
  brakes_condition: string;
  handbrake_condition: string;
  paintwork_condition: string;
  modification: string;
  general_condition: string;
  front: TyreForm;
  rear: TyreForm;
  point_of_impact: string;
  point_of_impact_note: string;
};

type CoverSource = Pick<ReportData, "report_id" | "incident_datetime" | "cover_page" | "vehicle_info" | "insurance_details">;

const text = (v: string | number | null | undefined): string => (v == null ? "" : String(v));

function tyreForm(near: TyreItem | null | undefined, off: TyreItem | null | undefined): TyreForm {
  return {
    nearside_mm: text(near?.tread_depth_mm),
    nearside_make: text(near?.brand),
    offside_mm: text(off?.tread_depth_mm),
    offside_make: text(off?.brand),
    // One size per axle, as the firm prints it: the nearside's, else the offside's.
    size: text(near?.size || off?.size),
  };
}

function coverFormFrom(d: CoverSource): CoverForm {
  const c = d.cover_page || {};
  const v = d.vehicle_info || {};
  const ins = d.insurance_details || {};
  const t = v.tyres || {};
  return {
    report_id: text(d.report_id),
    date_of_request: text(c.date_of_request),
    requested_by: text(c.requested_by),
    requested_by_address: text(c.requested_by_address),
    claim_type: text(ins.claim_type),
    workshop_assigned: text(ins.workshop_assigned),
    workshop_address: text(c.workshop_address),
    incident_datetime: text(d.incident_datetime),
    date_of_survey: text(c.date_of_survey),
    date_of_dismantle: text(c.date_of_dismantle),
    date_before_painting: text(c.date_before_painting),
    date_after_painting: text(c.date_after_painting),
    plate_number: text(v.plate_number),
    make: text(v.make),
    model: text(v.model),
    date_of_registration: text(v.date_of_registration),
    engine_number: text(v.engine_number),
    vin: text(v.vin),
    odometer_reading: text(v.odometer_reading),
    transmission: text(v.transmission),
    color: text(v.color),
    steering_condition: text(v.steering_condition),
    brakes_condition: text(v.brakes_condition),
    handbrake_condition: text(c.handbrake_condition),
    paintwork_condition: text(v.paintwork_condition),
    modification: text(c.modification),
    general_condition: text(c.general_condition),
    front: tyreForm(t.front_nearside, t.front_offside),
    rear: tyreForm(t.rear_nearside, t.rear_offside),
    point_of_impact: text(v.point_of_impact),
    point_of_impact_note: text(c.point_of_impact_note),
  };
}

/** "7", "7mm", "7.5 mm" -> 7; blank or anything else -> null. */
function parseDepth(value: string): number | null {
  const t = value.replace(/mm/i, "").trim();
  if (!t) return null;
  const n = parseFloat(t);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

const COVER_PAGE_KEYS = [
  "date_of_request", "requested_by", "requested_by_address", "workshop_address", "date_of_survey", "date_of_dismantle",
  "date_before_painting", "date_after_painting", "handbrake_condition", "modification", "general_condition", "point_of_impact_note",
] as const;
const VEHICLE_KEYS = [
  "plate_number", "make", "model", "date_of_registration", "engine_number", "vin", "odometer_reading", "transmission", "color",
  "steering_condition", "brakes_condition", "paintwork_condition", "point_of_impact",
] as const;
const INSURANCE_KEYS = ["claim_type", "workshop_assigned"] as const;

/** What the form shows, as PUT /reports/{id}/cover-page takes it: trimmed,
 * blank as null (which clears the value). Only what changed since the last
 * load or save is sent -- the API merges key by key, so the rest stays as
 * it is (and Our Ref, which only staff may change, isn't sent back by
 * someone who only changed a date). A tyre keeps its other fields
 * (condition) because tyres are replaced whole. */
function coverRequestFrom(form: CoverForm, baseline: CoverForm, tyres: TyreCondition | null | undefined): CoverPageRequest {
  const s = (v: string): string | null => v.trim() || null;
  const changed = (keys: readonly (keyof CoverForm)[]) => keys.some((k) => form[k] !== baseline[k]);
  const tyre = (prev: TyreItem | null | undefined, mm: string, make: string, size: string): TyreItem => ({
    ...(prev || {}),
    tread_depth_mm: parseDepth(mm),
    brand: s(make),
    size: s(size),
  });
  const body: CoverPageRequest = {};
  if (form.report_id !== baseline.report_id) body.report_id = s(form.report_id);
  if (form.incident_datetime !== baseline.incident_datetime) body.incident_datetime = s(form.incident_datetime);
  if (changed(COVER_PAGE_KEYS)) {
    body.cover_page = {
      date_of_request: s(form.date_of_request),
      requested_by: s(form.requested_by),
      requested_by_address: s(form.requested_by_address),
      workshop_address: s(form.workshop_address),
      date_of_survey: s(form.date_of_survey),
      date_of_dismantle: s(form.date_of_dismantle),
      date_before_painting: s(form.date_before_painting),
      date_after_painting: s(form.date_after_painting),
      handbrake_condition: s(form.handbrake_condition),
      modification: s(form.modification),
      general_condition: s(form.general_condition),
      point_of_impact_note: s(form.point_of_impact_note),
    };
  }
  const tyresChanged = (["front", "rear"] as const).some((axle) => JSON.stringify(form[axle]) !== JSON.stringify(baseline[axle]));
  if (changed(VEHICLE_KEYS) || tyresChanged) {
    body.vehicle_info = {
      plate_number: s(form.plate_number),
      make: s(form.make),
      model: s(form.model),
      date_of_registration: s(form.date_of_registration),
      engine_number: s(form.engine_number),
      vin: s(form.vin),
      odometer_reading: s(form.odometer_reading),
      transmission: s(form.transmission),
      color: s(form.color),
      steering_condition: s(form.steering_condition),
      brakes_condition: s(form.brakes_condition),
      paintwork_condition: s(form.paintwork_condition),
      point_of_impact: s(form.point_of_impact),
      tyres: {
        front_nearside: tyre(tyres?.front_nearside, form.front.nearside_mm, form.front.nearside_make, form.front.size),
        front_offside: tyre(tyres?.front_offside, form.front.offside_mm, form.front.offside_make, form.front.size),
        rear_nearside: tyre(tyres?.rear_nearside, form.rear.nearside_mm, form.rear.nearside_make, form.rear.size),
        rear_offside: tyre(tyres?.rear_offside, form.rear.offside_mm, form.rear.offside_make, form.rear.size),
      },
    };
  }
  if (changed(INSURANCE_KEYS)) {
    body.insurance_details = {
      claim_type: s(form.claim_type),
      workshop_assigned: s(form.workshop_assigned),
    };
  }
  return body;
}

/** One labelled text field of the Cover Page. Empty shows "Not recorded"
 * (never a guessed value), as the printed report does. */
function CoverField({
  label,
  value,
  onChange,
  disabled,
  wide,
  hint,
  list,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
  wide?: boolean;
  hint?: string;
  /** id of a <datalist> with the usual wordings. */
  list?: string;
}) {
  return (
    <label className={`cover-field${wide ? " wide" : ""}`}>
      <span>{label}</span>
      <input
        type="text"
        value={value}
        list={list}
        placeholder="Not recorded"
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
      {hint && <span className="cover-field-hint">{hint}</span>}
    </label>
  );
}

/** A value filled into the Annex C letter, and the mark for one that is
 * still missing ("(to be agreed)") -- never a guessed figure. */
const Var = ({ children }: { children: React.ReactNode }) => <span className="letter-var">{children}</span>;
const Missing = ({ children }: { children: React.ReactNode }) => <span className="letter-missing">{children}</span>;

// ---------------------------------------------------------------------------
// Annex B: the usual labour lines

/** "Usual labour lines" above the Annex B table: what past reports listed
 * for this make and model (else any), with what each was usually adjusted
 * to. A line goes into the table with that price filled and `prefilled`
 * set, so the tag shows where the figure came from until someone types
 * over it. Lines already in the table are hidden. */
function LabourLinesPicker({
  reportId,
  active,
  existing,
  disabled,
  onAdd,
}: {
  reportId: string;
  /** The Annex B sub-tab has been opened -- nothing is fetched before. */
  active: boolean;
  /** Job descriptions currently in the table (saved or not). */
  existing: string[];
  disabled: boolean;
  onAdd: (line: LabourSuggestionLine) => void;
}) {
  const [open, setOpen] = useState(true);
  const [loadState, setLoadState] = useState<"idle" | "loading" | "loaded" | "error">("idle");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [data, setData] = useState<LabourSuggestions | null>(null);

  async function load() {
    setLoadState("loading");
    setLoadError(null);
    const res = await getLabourSuggestionsAction(reportId);
    if ("error" in res) {
      setLoadError(res.error);
      setLoadState("error");
      return;
    }
    setData(res);
    setLoadState("loaded");
    if (res.groups.length === 0) setOpen(false);
  }

  const requested = useRef(false);
  useEffect(() => {
    if (!active || !open || requested.current) return;
    requested.current = true;
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, open]);

  const inTable = new Set(existing.map(sameLabourKey).filter(Boolean));
  const groups = (data?.groups || [])
    .map((g) => ({ ...g, lines: g.lines.filter((l) => !inTable.has(sameLabourKey(l.description))) }))
    .filter((g) => g.lines.length > 0);
  const total = (data?.groups || []).reduce((n, g) => n + g.lines.length, 0);

  let summary = "";
  if (loadState === "loading") summary = "Looking through past reports…";
  else if (loadState === "error") summary = "Couldn't load";
  else if (data?.hidden) summary = "Staff only";
  else if (data) summary = total === 0 ? "No past labour lines imported yet" : data.vehicle ? `From past ${data.vehicle} cases` : "From past cases";

  return (
    <div className="annex-suggest-panel">
      <button type="button" className="annex-suggest-header" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? <ChevronDown style={{ width: 14, height: 14 }} /> : <ChevronRight style={{ width: 14, height: 14 }} />}
        <Lightbulb style={{ width: 14, height: 14, color: "var(--accent-cyan)" }} />
        <span style={{ fontWeight: 700, fontSize: 13 }}>Usual labour lines</span>
        <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-muted)", textAlign: "right" }}>{summary}</span>
      </button>

      {open && (
        <div style={{ padding: "0 14px 14px" }}>
          {loadState === "loading" && <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Looking through past reports…</div>}

          {loadState === "error" && (
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <div className="setup-error" style={{ margin: 0 }}>{loadError}</div>
              <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} onClick={() => void load()}>
                Try again
              </button>
            </div>
          )}

          {loadState === "loaded" && data && (
            <>
              {data.hidden ? (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Price history is available to staff only.</div>
              ) : total === 0 ? (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  No past reports with an Annex B imported yet — import them on the Knowledge page.
                </div>
              ) : groups.length === 0 ? (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Every usual line is already in Annex B.</div>
              ) : (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  Prices are what past cases were adjusted to — a starting point, not the workshop&apos;s quote.
                </div>
              )}

              {groups.map((group) => (
                <div key={group.reason} style={{ marginTop: 12 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>{group.reason}</div>
                  {group.lines.map((line) => (
                    <div key={sameLabourKey(line.description)} className="annex-suggest-line" style={{ cursor: "default" }}>
                      <span style={{ flex: "1 1 240px", minWidth: 0, fontWeight: 600 }}>{line.description}</span>
                      <span
                        style={{ minWidth: 200, textAlign: "right", whiteSpace: "nowrap", color: line.usual_price != null ? "var(--text-secondary)" : "var(--text-muted)" }}
                        title={line.usual_workshop_est != null ? `Workshops usually quoted ${formatSgd(line.usual_workshop_est)}` : undefined}
                      >
                        {line.usual_price != null
                          ? `usual ${formatSgd(line.usual_price)} (${line.cases} case${line.cases === 1 ? "" : "s"})`
                          : "no price"}
                      </span>
                      <button
                        type="button"
                        className="btn-secondary-modern"
                        style={{ fontSize: 11, padding: "3px 10px" }}
                        disabled={disabled}
                        onClick={() => onAdd(line)}
                      >
                        <Plus style={{ width: 12, height: 12 }} /> Add
                      </button>
                    </div>
                  ))}
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

export function CaseAssessmentTab({
  report,
  damageEntries,
  canEdit,
  canSignOff,
}: {
  report: ReportDetail;
  damageEntries: DamageSummaryItem[];
  /** The case's permissions.edit and permissions.sign_off from the API. */
  canEdit: boolean;
  canSignOff: boolean;
}) {
  const d = report.data;
  const ins = d.insurance_details;
  const signOff = d.sign_off;
  const isSignedOff = report.status === "Signed Off" || signOff?.status === "Signed Off";
  // Nothing can be changed once signed off, nor by someone who may only
  // look at the case; the sign-off box only by someone who may sign off.
  const readOnly = isSignedOff || !canEdit;
  const signOffLocked = isSignedOff || !canSignOff;
  const router = useRouter();
  const plate = d.vehicle_info?.plate_number || "";

  type SubTab = "cover" | "annexA" | "annexB" | "annexC";
  const [activeSubTab, setActiveSubTab] = useState<SubTab>("cover");
  // A sub-tab is mounted on first open and then kept (display: none), so
  // what was fetched there (suggested lines) or typed stays on coming back.
  const [opened, setOpened] = useState<Record<SubTab, boolean>>({ cover: true, annexA: false, annexB: false, annexC: false });
  const showSubTab = (tab: SubTab) => {
    setActiveSubTab(tab);
    setOpened((o) => (o[tab] ? o : { ...o, [tab]: true }));
  };

  const sgd = (n: number | null | undefined) => (n == null ? "—" : formatSgd(n));
  const amt = (n: number) => formatAmount(n);

  // ---- Cover Page ---------------------------------------------------------
  const [coverBaseline, setCoverBaseline] = useState<CoverForm>(() => coverFormFrom(d));
  const [cover, setCover] = useState<CoverForm>(() => coverFormFrom(d));
  // The tyres as saved, so a save keeps what the form doesn't show (condition).
  const [tyresSaved, setTyresSaved] = useState<TyreCondition | null>(d.vehicle_info?.tyres ?? null);
  const [coverSave, setCoverSave] = useState<SaveState>("idle");
  const [coverError, setCoverError] = useState<string | null>(null);
  const coverDirty = !readOnly && JSON.stringify(cover) !== JSON.stringify(coverBaseline);
  const coverLocked = readOnly || coverSave === "saving";
  const setCoverField = (patch: Partial<CoverForm>) => {
    if (coverLocked) return;
    setCover((c) => ({ ...c, ...patch }));
  };
  const setTyre = (axle: "front" | "rear", patch: Partial<TyreForm>) => {
    if (coverLocked) return;
    setCover((c) => ({ ...c, [axle]: { ...c[axle], ...patch } }));
  };

  const handleSaveCover = async () => {
    if (coverLocked) return;
    setCoverSave("saving");
    setCoverError(null);
    const res = await saveCoverPageAction(report.id, coverRequestFrom(cover, coverBaseline, tyresSaved));
    if ("error" in res) {
      setCoverSave("error");
      setCoverError(res.error);
      return;
    }
    // The API's answer replaces the form, so what is shown is what was kept.
    const saved = coverFormFrom({
      report_id: res.report_id,
      incident_datetime: res.incident_datetime,
      cover_page: res.cover_page,
      vehicle_info: res.vehicle_info,
      insurance_details: res.insurance_details,
    });
    setCoverBaseline(saved);
    setCover(saved);
    setTyresSaved(res.vehicle_info?.tyres ?? null);
    setCoverSave("saved");
    // The plate and workshop show elsewhere on the page too.
    router.refresh();
  };

  const handleDiscardCover = () => {
    if (coverLocked || !confirm("Discard your unsaved changes to the cover page?")) return;
    setCover(coverBaseline);
    setCoverSave("idle");
    setCoverError(null);
  };

  // ---- Annex C --------------------------------------------------------------
  // The letter's two figures from the surveyor: contract lump sum and
  // working days (PUT /reports/{id}/annex-c). Empty until entered -- they
  // used to default to S$8,700 and 11 days.
  const [annexC, setAnnexC] = useState<AnnexCInfo>(d.annex_c ?? { workshop_total: 0, adjusted_total: 0 });
  const [lumpText, setLumpText] = useState<string>(text(d.annex_c?.agreed_lump_sum));
  const [daysText, setDaysText] = useState<string>(text(d.annex_c?.repair_days));
  const [annexCSave, setAnnexCSave] = useState<SaveState>("idle");
  const [annexCSaveError, setAnnexCSaveError] = useState<string | null>(null);
  const lumpValue: number | null = lumpText.trim() === "" ? null : parseAmount(lumpText);
  const daysParsed = parseInt(daysText.trim(), 10);
  const daysValue: number | null = daysText.trim() === "" ? null : Number.isFinite(daysParsed) ? daysParsed : null;
  const badDays = daysText.trim() !== "" && !/^\d+$/.test(daysText.trim());
  const annexCDirty = !readOnly && (lumpValue !== (annexC.agreed_lump_sum ?? null) || daysValue !== (annexC.repair_days ?? null));
  const annexCLocked = readOnly || annexCSave === "saving";

  /** Annex C as the API saved it, after any annex is saved. */
  // resetInputs false: the totals move, the lump sum and days being typed
  // stay (a Save Annex A used to wipe unsaved Annex C typing without a word).
  const takeSavedAnnexC = (saved: AnnexCInfo, resetInputs = true) => {
    setAnnexC(saved);
    if (resetInputs) {
      setLumpText(text(saved.agreed_lump_sum));
      setDaysText(text(saved.repair_days));
    }
  };

  const handleSaveAnnexC = async () => {
    if (annexCLocked || isBadAmount(lumpText) || badDays) return;
    setAnnexCSave("saving");
    setAnnexCSaveError(null);
    const res = await saveAnnexCAction(report.id, { agreed_lump_sum: lumpValue, repair_days: daysValue });
    if ("error" in res) {
      setAnnexCSave("error");
      setAnnexCSaveError(res.error);
      return;
    }
    takeSavedAnnexC(res.annex_c);
    setAnnexCSave("saved");
    router.refresh();
  };

  const handleDiscardAnnexC = () => {
    if (annexCLocked) return;
    setLumpText(text(annexC.agreed_lump_sum));
    setDaysText(text(annexC.repair_days));
    setAnnexCSave("idle");
    setAnnexCSaveError(null);
  };

  // ---- Annex A --------------------------------------------------------------
  // The case's spare parts table, edited here and saved with "Save Annex A"
  // (PUT /reports/{id}/annex-a). The recorded rows only -- with none, this
  // used to invent a row per damaged part (S$850 estimate, 20% off to
  // S$680, condition "Deformed/cut") that fed every total on this tab.
  const initialAnnexA: AnnexAPartItem[] = d.annex_a?.items || [];
  // The table as last loaded or saved: what "Discard changes" goes back to,
  // what tells whether anything is unsaved, and what the Summary sums.
  const [annexABaseline, setAnnexABaseline] = useState<AnnexAPartItem[]>(initialAnnexA);
  const [annexAItems, setAnnexAItems] = useState<AnnexRow[]>(() => initialAnnexA.map(toAnnexRow));
  const [annexASave, setAnnexASave] = useState<SaveState>("idle");
  const [annexASaveError, setAnnexASaveError] = useState<string | null>(null);
  const [prefillBusy, setPrefillBusy] = useState(false);
  const [prefillNote, setPrefillNote] = useState<string | null>(null);
  const annexADirty = !readOnly && annexSignature(annexAItems.map(fromAnnexRow)) !== annexSignature(annexABaseline);
  const annexALocked = readOnly || annexASave === "saving" || prefillBusy;

  // A row's adjusted cost is worked out live while the table can be edited
  // (the API's own sum, so it matches what saving stores); once signed off
  // it is the recorded figure.
  const rowAdjusted = (it: AnnexAPartItem) => (readOnly ? it.adjusted_cost : annexAdjustedCost(it));
  const annexAShown = inSections(annexAItems);
  const listRows = annexAShown.filter((r) => !r.is_net_item);
  const nettRows = annexAShown.filter((r) => r.is_net_item);
  const liveParts = {
    est: annexAItems.reduce((acc, it) => acc + (it.workshop_est || 0), 0),
    adj: annexAItems.reduce((acc, it) => acc + rowAdjusted(it), 0),
  };
  // Unpriced rows carry 0 -- a total over them is "—", not 0.00.
  const hasPricedParts = annexAItems.some((it) => it.workshop_est > 0);

  // Every change goes into annexAItems and nothing is saved until "Save
  // Annex A"; adjusted costs follow from the row (annexAdjustedCost), so
  // the handlers only set what was changed. Rows are found by key, not
  // index, because the table is shown in sections, not in array order.
  const updateAnnexRow = (key: string, patch: Partial<AnnexRow>) => {
    if (annexALocked) return;
    setAnnexAItems((prev) => prev.map((row) => (row._key === key ? { ...row, ...patch } : row)));
  };

  const handleRemarkChange = (key: string, remark: string) => {
    if (annexALocked) return;
    setAnnexAItems((prev) =>
      prev.map((row) => {
        if (row._key !== key) return row;
        const item = { ...row, action: remark };
        if (remark === "Disallow") {
          // Not nett as well: a nett line takes no discount, so it would
          // still be paid in full.
          item.discount_pct = 1.0;
          item.is_net_item = false;
        } else if (row.action === "Disallow" && item.discount_pct === 1.0) {
          item.discount_pct = 0.2;
        }
        return item;
      })
    );
  };

  const handleApplyTradeDiscountToAll = () => {
    if (annexALocked) return;
    setAnnexAItems((prev) => prev.map((it) => (it.is_net_item || it.action === "Disallow" ? it : { ...it, discount_pct: 0.2 })));
  };

  const handleNetChange = (key: string, isNet: boolean) => {
    updateAnnexRow(key, isNet ? { is_net_item: true, discount_pct: 0 } : { is_net_item: false, discount_pct: 0.2 });
  };

  const handleAddAnnexLine = () => {
    if (annexALocked) return;
    setAnnexAItems((prev) => [...prev, toAnnexRow(BLANK_ANNEX_LINE)]);
  };

  const handleRemoveAnnexLine = (key: string) => {
    if (annexALocked) return;
    setAnnexAItems((prev) => prev.filter((row) => row._key !== key));
  };

  // From the "Suggested lines" panel: appended, not saved.
  const handleAddSuggestedLines = (items: AnnexAPartItem[]) => {
    if (annexALocked) return;
    setAnnexAItems((prev) => {
      const have = new Set(prev.map((r) => sameLineKey(r.part_name)));
      return [...prev, ...items.filter((it) => !have.has(sameLineKey(it.part_name))).map(toAnnexRow)];
    });
  };

  const handleDiscardAnnexA = () => {
    if (annexALocked || !confirm("Discard your unsaved changes to Annex A?")) return;
    setAnnexAItems(annexABaseline.map(toAnnexRow));
    setAnnexASave("idle");
    setAnnexASaveError(null);
  };

  /** The API's answer replaces the table: its numbering, adjusted costs,
   * and without the lines that had no part description. */
  const takeSavedAnnexA = (items: AnnexAPartItem[], annexC?: AnnexCInfo) => {
    setAnnexABaseline(items);
    setAnnexAItems(items.map(toAnnexRow));
    setAnnexASave("saved");
    if (annexC) takeSavedAnnexC(annexC, !annexCDirty);
    router.refresh();
  };

  const handleSaveAnnexA = async () => {
    if (annexALocked) return;
    setAnnexASave("saving");
    setAnnexASaveError(null);
    setPrefillNote(null);
    const res = await saveAnnexAAction(report.id, inSections(annexAItems).map(fromAnnexRow));
    if ("error" in res) {
      setAnnexASave("error");
      setAnnexASaveError(res.error);
      return;
    }
    takeSavedAnnexA(res.annex_a?.items || [], res.annex_c);
  };

  // POST /reports/{id}/annex-a/prefill works on the table as saved and saves
  // the result: rows a person priced stay, prefilled and unpriced rows are
  // priced from past cases of this make and model.
  const handlePrefill = async () => {
    if (annexALocked) return;
    if (annexADirty && !confirm("Filling prices uses Annex A as last saved, so your unsaved changes would be lost. Continue?")) return;
    setPrefillBusy(true);
    setPrefillNote(null);
    setAnnexASaveError(null);
    const res = await prefillAnnexAAction(report.id);
    setPrefillBusy(false);
    if ("error" in res) {
      setAnnexASave("error");
      setAnnexASaveError(res.error);
      return;
    }
    takeSavedAnnexA(res.annex_a?.items || [], res.annex_c);
    const n = res.filled;
    setPrefillNote(
      n === 0
        ? "No prices found in past cases for the unpriced lines."
        : `${n} line${n === 1 ? "" : "s"} priced from past cases${
            res.unpriced ? `; ${res.unpriced} still ${res.unpriced === 1 ? "has" : "have"} no price` : ""
          }.`
    );
  };

  // ---- Annex B --------------------------------------------------------------
  // Recorded rows only (with none, this used to fall back to nine labour
  // lines copied from the SLK case).
  const initialAnnexB: AnnexBLabourItem[] = d.annex_b?.items || [];
  const [annexBBaseline, setAnnexBBaseline] = useState<AnnexBLabourItem[]>(initialAnnexB);
  const [annexBItems, setAnnexBItems] = useState<LabourRow[]>(() => initialAnnexB.map(toLabourRow));
  const [annexBSave, setAnnexBSave] = useState<SaveState>("idle");
  const [annexBSaveError, setAnnexBSaveError] = useState<string | null>(null);
  const annexBDirty = !readOnly && labourSignature(annexBItems.map(fromLabourRow)) !== labourSignature(annexBBaseline);
  const annexBLocked = readOnly || annexBSave === "saving";
  const liveLabour = {
    est: annexBItems.reduce((acc, it) => acc + parseAmount(it._est), 0),
    adj: annexBItems.reduce((acc, it) => acc + parseAmount(it._adj), 0),
  };
  const hasPricedLabour = annexBItems.some((it) => parseAmount(it._est) > 0 || parseAmount(it._adj) > 0);

  const updateLabourRow = (key: string, patch: Partial<LabourRow>) => {
    if (annexBLocked) return;
    setAnnexBItems((prev) => prev.map((row) => (row._key === key ? { ...row, ...patch } : row)));
  };

  const handleAddLabourLine = () => {
    if (annexBLocked) return;
    setAnnexBItems((prev) => [...prev, toLabourRow(BLANK_LABOUR_LINE)]);
  };

  const handleRemoveLabourLine = (key: string) => {
    if (annexBLocked) return;
    setAnnexBItems((prev) => prev.filter((row) => row._key !== key));
  };

  /** From the usual labour lines: the line with its usual price filled in
   * and marked prefilled, so the tag says where the figure came from. */
  const handleAddUsualLine = (line: LabourSuggestionLine) => {
    if (annexBLocked) return;
    setAnnexBItems((prev) => {
      if (prev.some((r) => sameLabourKey(r.description) === sameLabourKey(line.description))) return prev;
      return [
        ...prev,
        toLabourRow({
          item_no: "",
          description: line.description,
          workshop_est: line.usual_workshop_est ?? 0,
          adjusted_cost: line.usual_price ?? 0,
          justification: null,
          prefilled: line.usual_price != null,
          suggested_from: line.source,
          usual_price: line.usual_price,
        }),
      ];
    });
  };

  const handleDiscardAnnexB = () => {
    if (annexBLocked || !confirm("Discard your unsaved changes to Annex B?")) return;
    setAnnexBItems(annexBBaseline.map(toLabourRow));
    setAnnexBSave("idle");
    setAnnexBSaveError(null);
  };

  const handleSaveAnnexB = async () => {
    if (annexBLocked) return;
    setAnnexBSave("saving");
    setAnnexBSaveError(null);
    const res = await saveAnnexBAction(report.id, annexBItems.map(fromLabourRow));
    if ("error" in res) {
      setAnnexBSave("error");
      setAnnexBSaveError(res.error);
      return;
    }
    // The API's answer replaces the table: numbered, without blank lines.
    const saved = res.annex_b?.items || [];
    setAnnexBBaseline(saved);
    setAnnexBItems(saved.map(toLabourRow));
    setAnnexBSave("saved");
    if (res.annex_c) takeSavedAnnexC(res.annex_c, !annexCDirty);
    router.refresh();
  };

  // The Summary block and the letter use the tables as last saved -- what
  // the PDF and the workbook print.
  const savedParts = sumRows(annexABaseline);
  const savedLabour = sumRows(annexBBaseline);
  const savedTotal = { est: Math.round((savedParts.est + savedLabour.est) * 100) / 100, adj: Math.round((savedParts.adj + savedLabour.adj) * 100) / 100 };
  const anyPriced = savedTotal.est > 0 || savedTotal.adj > 0;

  // Leaving the page (reload, another URL) with unsaved changes asks first.
  const anyDirty = coverDirty || annexADirty || annexBDirty || annexCDirty;
  useEffect(() => {
    if (!anyDirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [anyDirty]);

  // ---- Sign-off -------------------------------------------------------------
  // Who signs is the signed-in user, with their own profile (Settings -> My
  // settings) -- the API takes it from the account. This defaulted to one
  // fixed surveyor's name, qualifications and licence, so every case read
  // as signed by them.
  const user = useCurrentUser();
  const signer = isSignedOff
    ? { name: signOff?.surveyor_name, qualifications: signOff?.qualifications, license: signOff?.license_number, firm: signOff?.firm_name }
    : { name: user?.name, qualifications: user?.qualifications, license: user?.license_number, firm: user?.firm_name };
  const surveyorName = signer.name || "";
  const qualifications = signer.qualifications || "";
  const licenseNumber = signer.license || "";
  const firmName = signer.firm || "";
  const noFullName = !isSignedOff && canSignOff && Boolean(user) && (!user?.name || user.name === user.username);
  const profileIncomplete = !isSignedOff && canSignOff && Boolean(user) && (!user?.license_number || noFullName);

  const DEFAULT_TITLE = "Automotive Engineer Assessor";
  const [signatoryTitle, setSignatoryTitle] = useState<string>(signOff?.signatory_title || DEFAULT_TITLE);
  const [remarks, setRemarks] = useState<string>(signOff?.remarks || "");
  // Starts unticked -- the surveyor makes the declaration. A signed-off
  // case shows what was accepted.
  const [declared, setDeclared] = useState<boolean>(isSignedOff && signOff?.terms_accepted === true);
  const [isSigningOff, setIsSigningOff] = useState(false);
  const [signOffError, setSignOffError] = useState<string | null>(null);

  const fieldInputStyle: React.CSSProperties = {
    background: "var(--surface-base)",
    color: "var(--text-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    padding: "6px 8px",
    fontSize: 12,
    fontFamily: "inherit",
  };

  // The signature pad. Only what was actually drawn is sent: an untouched
  // pad used to go out as a blank image.
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [isDrawing, setIsDrawing] = useState(false);
  const [hasSignature, setHasSignature] = useState(false);

  useEffect(() => {
    const ctx = canvasRef.current?.getContext("2d");
    if (ctx) {
      ctx.strokeStyle = "#1e3a8a";
      ctx.lineWidth = 2.5;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
    }
  }, [signOffLocked]);

  const padPoint = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    // The canvas may be shown narrower than its drawing size.
    const sx = e.currentTarget.width / rect.width;
    const sy = e.currentTarget.height / rect.height;
    return { x: (e.clientX - rect.left) * sx, y: (e.clientY - rect.top) * sy };
  };

  const handlePadDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx) return;
    const { x, y } = padPoint(e);
    // A new stroke starts where the pen lands (strokes used to join up).
    ctx.beginPath();
    ctx.moveTo(x, y);
    setIsDrawing(true);
  };

  const handlePadMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isDrawing) return;
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx) return;
    const { x, y } = padPoint(e);
    ctx.lineTo(x, y);
    ctx.stroke();
    setHasSignature(true);
  };

  const handleClearSignature = () => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (canvas && ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    setHasSignature(false);
  };

  const handleSignOffSubmit = async () => {
    if (!declared) {
      setSignOffError("Tick the declaration before signing off.");
      return;
    }
    // Sign-off locks the case with the tables as saved; unsaved rows would be lost.
    const unsaved = [coverDirty && "the cover page", annexADirty && "Annex A", annexBDirty && "Annex B", annexCDirty && "Annex C"].filter(
      (s): s is string => Boolean(s)
    );
    const saving = [coverSave, annexASave, annexBSave, annexCSave].includes("saving") || prefillBusy;
    if (unsaved.length > 0 || saving) {
      setSignOffError(saving ? "Wait for the save to finish before signing off." : `Save or discard your changes to ${unsaved.join(", ")} before signing off.`);
      return;
    }

    setIsSigningOff(true);
    setSignOffError(null);

    const payload: SignOffRequest = {
      // Only what was drawn or typed here. The lump sum and working days
      // are not sent: Annex C saves them itself and the API keeps them.
      signature_data_url: hasSignature && canvasRef.current ? canvasRef.current.toDataURL("image/png") : undefined,
      signatory_title: signatoryTitle.trim() || DEFAULT_TITLE,
      remarks: remarks.trim() || undefined,
      // True only because the surveyor ticked it (checked above).
      terms_accepted: true,
    };

    const res = await signOffReportAction(report.id, payload);
    if ("error" in res) {
      setSignOffError(res.error);
      setIsSigningOff(false);
      return;
    }

    window.location.reload();
  };

  // ---- Letter values --------------------------------------------------------
  const workshopName = ins?.workshop_assigned?.trim() || "";
  const letterLump = lumpValue;
  const letterDays = daysValue;

  const lockedNote = isSignedOff ? (
    <div style={{ fontSize: 11, color: "var(--badge-amber-text)", marginTop: 2 }}>Signed off — reopen the case from the edit tab to change it.</div>
  ) : !canEdit ? (
    <div style={{ fontSize: 11, color: "var(--badge-amber-text)", marginTop: 2 }}>View only — you can&apos;t change this case.</div>
  ) : null;

  const savedNote = (state: SaveState, dirty: boolean) =>
    state === "saved" && !dirty ? (
      <span style={{ fontSize: 11, fontWeight: 700, color: "var(--badge-green-text)", display: "inline-flex", alignItems: "center", gap: 4 }}>
        <CheckCircle2 style={{ width: 13, height: 13 }} /> Saved. The PDF and Excel pick it up on the next download.
      </span>
    ) : null;

  const unsavedNote = (state: SaveState, dirty: boolean) =>
    state !== "saving" && dirty ? <span style={{ fontSize: 11, fontWeight: 700, color: "var(--badge-amber-text)" }}>Unsaved changes</span> : null;

  const annexAColumns = readOnly ? 8 : 10;

  /** One Annex A row, editable or as recorded. */
  const renderAnnexARow = (item: AnnexRow, no: number) => {
    const isDisallowed = item.action === "Disallow" || item.discount_pct >= 0.99;
    // Rows carry 0 until a surveyor (or the prefill) prices them.
    const isUnpriced = !item.workshop_est;
    const qtyShown = item.qty_text || (item.qty ? String(item.qty) : "1");

    if (readOnly) {
      return (
        <tr key={item._key} style={{ opacity: isDisallowed ? 0.65 : 1 }}>
          <td style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{no}</td>
          <td style={{ fontWeight: 600 }}>{item.part_name}</td>
          <td>{item.condition || "—"}</td>
          <td>{item.action || ""}</td>
          <td style={{ textAlign: "center", fontFamily: "var(--font-mono)" }}>{qtyShown}</td>
          <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{isUnpriced ? "—" : amt(item.workshop_est)}</td>
          <td style={{ textAlign: "center", fontFamily: "var(--font-mono)" }}>{item.is_net_item ? "" : `${Math.round(item.discount_pct * 100)}%`}</td>
          <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 700, color: isDisallowed ? "var(--text-muted)" : "var(--accent-cyan)" }}>
            {isUnpriced ? "—" : amt(item.adjusted_cost)}
          </td>
        </tr>
      );
    }

    const line = `Line ${no}`;
    const knownDiscount = DISCOUNT_OPTIONS.some(([v]) => Math.abs(v - item.discount_pct) < 1e-9);
    return (
      <tr key={item._key} style={{ opacity: isDisallowed ? 0.65 : 1 }}>
        <td style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{no}</td>
        <td style={{ minWidth: 200 }}>
          <input
            type="text"
            className="annex-cell-input"
            style={{ fontWeight: 600 }}
            value={item.part_name}
            placeholder="Part description"
            aria-label={`${line}: part description`}
            disabled={annexALocked}
            onChange={(e) => updateAnnexRow(item._key, { part_name: e.target.value })}
          />
          {item.prefilled && item.suggested_from ? (
            <span className="annex-prefill-tag" title="The estimate was filled from past cases, not typed. Typing an estimate replaces it.">
              from {item.suggested_from}
            </span>
          ) : item.suggested_from ? (
            <div style={{ fontSize: 10, color: item.suggested_from === "AI suggestion" ? "var(--badge-amber-text)" : "var(--text-muted)", marginTop: 2 }}>
              {item.suggested_from === "AI suggestion" ? "AI suggestion — check it" : `From ${item.suggested_from}`}
            </div>
          ) : null}
        </td>
        <td style={{ minWidth: 120 }}>
          <input
            type="text"
            className="annex-cell-input"
            list="annex-a-conditions"
            value={item.condition}
            placeholder="—"
            aria-label={`${line}: condition`}
            disabled={annexALocked}
            onChange={(e) => updateAnnexRow(item._key, { condition: e.target.value })}
          />
        </td>
        <td>
          <select
            value={item.action}
            onChange={(e) => handleRemarkChange(item._key, e.target.value)}
            aria-label={`${line}: remarks`}
            disabled={annexALocked}
            style={{ ...fieldInputStyle, padding: "2px 6px", fontSize: 11 }}
          >
            <option value="">—</option>
            {item.action && !REMARK_OPTIONS.includes(item.action) && <option value={item.action}>{item.action}</option>}
            {REMARK_OPTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </td>
        <td style={{ textAlign: "center" }}>
          <input
            type="text"
            className="annex-cell-input"
            style={{ width: 60, textAlign: "center", fontFamily: "var(--font-mono)" }}
            value={item._qty}
            placeholder="1"
            title='A number, or as written on the quote ("1set")'
            aria-label={`${line}: quantity`}
            disabled={annexALocked}
            onChange={(e) => updateAnnexRow(item._key, { _qty: e.target.value, ...qtyFromText(e.target.value) })}
          />
        </td>
        <td style={{ textAlign: "right" }}>
          <input
            type="text"
            inputMode="decimal"
            className="annex-cell-input"
            style={{ width: 96, textAlign: "right", fontFamily: "var(--font-mono)" }}
            value={item._est}
            placeholder="0.00"
            aria-label={`${line}: workshop estimate (line total)`}
            aria-invalid={isBadAmount(item._est) ? true : undefined}
            disabled={annexALocked}
            // Typed by a person now, so the "from past cases" tag goes.
            onChange={(e) => updateAnnexRow(item._key, { _est: e.target.value, workshop_est: parseAmount(e.target.value), prefilled: false })}
          />
          {item.usual_unit_price != null && (
            <div
              title={`What past cases' lines were adjusted to per unit (median), from ${item.suggested_from || "past reports"}. A reference only: enter the workshop's own figure for the line.`}
              style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2, cursor: "help", whiteSpace: "nowrap" }}
            >
              usual {sgd(item.usual_unit_price)} /unit
            </div>
          )}
        </td>
        <td style={{ textAlign: "center", fontFamily: "var(--font-mono)" }}>
          {item.is_net_item ? (
            <span style={{ color: "var(--text-muted)" }}>—</span>
          ) : (
            <select
              value={item.discount_pct}
              onChange={(e) => updateAnnexRow(item._key, { discount_pct: parseFloat(e.target.value) })}
              aria-label={`${line}: discount`}
              disabled={annexALocked}
              style={{ ...fieldInputStyle, padding: "2px 4px", fontSize: 11 }}
            >
              {!knownDiscount && <option value={item.discount_pct}>{Math.round(item.discount_pct * 100)}%</option>}
              {DISCOUNT_OPTIONS.map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          )}
        </td>
        <td style={{ textAlign: "center" }}>
          <input
            type="checkbox"
            checked={item.is_net_item}
            aria-label={`${line}: nett item`}
            title={item.action === "Disallow" ? "A disallowed line can't be nett" : "Nett item: listed under S/Nett Items, no discount"}
            disabled={annexALocked || item.action === "Disallow"}
            onChange={(e) => handleNetChange(item._key, e.target.checked)}
          />
        </td>
        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 700, color: isDisallowed ? "var(--text-muted)" : "var(--accent-cyan)" }}>
          {isUnpriced ? "—" : amt(rowAdjusted(item))}
        </td>
        <td style={{ textAlign: "center" }}>
          <button
            type="button"
            className="annex-row-remove"
            title="Remove this line"
            aria-label={`Remove line ${no}`}
            disabled={annexALocked}
            onClick={() => handleRemoveAnnexLine(item._key)}
          >
            <Trash2 style={{ width: 13, height: 13 }} />
          </button>
        </td>
      </tr>
    );
  };

  const sectionRow = (title: string) => (
    <tr className="annex-section-row" key={`section-${title}`}>
      <td colSpan={annexAColumns}>{title}</td>
    </tr>
  );

  return (
    <div className="assessment-workstation">
      <CaseAnalysisPanel report={report} damageEntries={damageEntries} />

      <div className="card-glass" style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--border-color)", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
          <div className="annex-nav-bar" style={{ borderBottom: "none", marginBottom: 0 }}>
            <button type="button" className={`annex-tab-btn ${activeSubTab === "cover" ? "active" : ""}`} onClick={() => showSubTab("cover")}>
              Cover
            </button>
            <button type="button" className={`annex-tab-btn ${activeSubTab === "annexA" ? "active" : ""}`} onClick={() => showSubTab("annexA")}>
              Annex A ({annexAItems.length})
            </button>
            <button type="button" className={`annex-tab-btn ${activeSubTab === "annexB" ? "active" : ""}`} onClick={() => showSubTab("annexB")}>
              Annex B ({annexBItems.length})
            </button>
            <button type="button" className={`annex-tab-btn ${activeSubTab === "annexC" ? "active" : ""}`} onClick={() => showSubTab("annexC")}>
              Annex C
            </button>
          </div>

          {/* The report as the firm sends it. Unsigned, it prints as a draft. */}
          <div className="report-downloads">
            {anyDirty && <span style={{ fontSize: 11, color: "var(--badge-amber-text)" }}>Save first: downloads use what is saved.</span>}
            <a href={pdfDownloadUrl(report.id)} className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} title="The report with the photo pages">
              <FileText style={{ width: 13, height: 13 }} /> PDF
            </a>
            <a href={pdfDownloadUrl(report.id, { photos: false })} className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} title="The four pages only">
              <FileText style={{ width: 13, height: 13 }} /> PDF without photos
            </a>
            <a href={xlsxDownloadUrl(report.id)} className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} title="Cover Page, Annex A, Annex B and Annex C as a workbook">
              <FileSpreadsheet style={{ width: 13, height: 13 }} /> Excel
            </a>
          </div>
        </div>

        {/* ---- COVER PAGE ---------------------------------------------------- */}
        <div style={{ padding: 20, display: activeSubTab === "cover" ? undefined : "none" }}>
          <div style={{ marginBottom: 14 }}>
            <h4 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Accident Vehicle Inspection / Assessment Report</h4>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
              The cover page as the firm prints it. Dates are printed as typed. Anything left blank prints as &quot;Not recorded&quot;.
            </div>
            {lockedNote}
          </div>

          <datalist id="cover-claim-types">
            {CLAIM_TYPE_OPTIONS.map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
          <datalist id="cover-see-annex">
            <option value="see annex(es)" />
          </datalist>
          <datalist id="cover-good-nil">
            <option value="Good" />
            <option value="Nil" />
          </datalist>

          <div className="cover-form">
            <div className="cover-form-section">1 &nbsp;Reference</div>
            <CoverField
              label="Our Ref"
              value={cover.report_id}
              onChange={(v) => setCoverField({ report_id: v })}
              disabled={coverLocked}
              hint="The firm's file number, e.g. CL 12330 SMV 7530 X. Blank prints as (to be issued)."
            />
            <CoverField label="Date of request" value={cover.date_of_request} onChange={(v) => setCoverField({ date_of_request: v })} disabled={coverLocked} />
            <CoverField label="Requested by" value={cover.requested_by} onChange={(v) => setCoverField({ requested_by: v })} disabled={coverLocked} />
            <CoverField
              label="C/o address (requested by)"
              value={cover.requested_by_address}
              onChange={(v) => setCoverField({ requested_by_address: v })}
              disabled={coverLocked}
              hint="Printed under the name, e.g. C/o Blk 3023A, Ubi Road 1, #01-60, Singapore 408717"
            />
            <CoverField label="Type of claim" value={cover.claim_type} onChange={(v) => setCoverField({ claim_type: v })} disabled={coverLocked} list="cover-claim-types" />
            <CoverField
              label="Workshop"
              value={cover.workshop_assigned}
              onChange={(v) => setCoverField({ workshop_assigned: v })}
              disabled={coverLocked}
              hint="As the letter names it, e.g. M/S People's Vehicle Recovery Service"
            />
            <CoverField label="Workshop address" value={cover.workshop_address} onChange={(v) => setCoverField({ workshop_address: v })} disabled={coverLocked} />
            <CoverField label="Accident date" value={cover.incident_datetime} onChange={(v) => setCoverField({ incident_datetime: v })} disabled={coverLocked} />
            <CoverField label="Date of survey" value={cover.date_of_survey} onChange={(v) => setCoverField({ date_of_survey: v })} disabled={coverLocked} hint="e.g. 27th February 2023 (10.15am)" />
            <CoverField label="Date of dismantle parts" value={cover.date_of_dismantle} onChange={(v) => setCoverField({ date_of_dismantle: v })} disabled={coverLocked} />
            <CoverField label="Date of before painting" value={cover.date_before_painting} onChange={(v) => setCoverField({ date_before_painting: v })} disabled={coverLocked} />
            <CoverField label="Date of after painting" value={cover.date_after_painting} onChange={(v) => setCoverField({ date_after_painting: v })} disabled={coverLocked} />

            <div className="cover-form-section">2 &nbsp;Details of vehicle</div>
            <CoverField label="Registration No." value={cover.plate_number} onChange={(v) => setCoverField({ plate_number: v })} disabled={coverLocked} />
            <CoverField label="Make" value={cover.make} onChange={(v) => setCoverField({ make: v })} disabled={coverLocked} />
            <CoverField label="Model" value={cover.model} onChange={(v) => setCoverField({ model: v })} disabled={coverLocked} hint="Printed together as Make & Model" />
            <CoverField label="Date of registration" value={cover.date_of_registration} onChange={(v) => setCoverField({ date_of_registration: v })} disabled={coverLocked} />
            <CoverField label="Engine No." value={cover.engine_number} onChange={(v) => setCoverField({ engine_number: v })} disabled={coverLocked} />
            <CoverField label="Chassis No." value={cover.vin} onChange={(v) => setCoverField({ vin: v })} disabled={coverLocked} />
            <CoverField label="Speedometer reading" value={cover.odometer_reading} onChange={(v) => setCoverField({ odometer_reading: v })} disabled={coverLocked} hint="e.g. 058615 Km" />
            <CoverField label="Transmission" value={cover.transmission} onChange={(v) => setCoverField({ transmission: v })} disabled={coverLocked} />
            <CoverField label="Colour" value={cover.color} onChange={(v) => setCoverField({ color: v })} disabled={coverLocked} />

            <div className="cover-form-section">3 &nbsp;Static check</div>
            <CoverField label="Steering" value={cover.steering_condition} onChange={(v) => setCoverField({ steering_condition: v })} disabled={coverLocked} list="cover-see-annex" />
            <CoverField label="Footbrake" value={cover.brakes_condition} onChange={(v) => setCoverField({ brakes_condition: v })} disabled={coverLocked} list="cover-see-annex" />
            <CoverField label="Handbrake" value={cover.handbrake_condition} onChange={(v) => setCoverField({ handbrake_condition: v })} disabled={coverLocked} list="cover-see-annex" />
            <CoverField label="Paintwork" value={cover.paintwork_condition} onChange={(v) => setCoverField({ paintwork_condition: v })} disabled={coverLocked} list="cover-good-nil" />
            <CoverField label="Modification" value={cover.modification} onChange={(v) => setCoverField({ modification: v })} disabled={coverLocked} list="cover-good-nil" />
            <CoverField label="General condition" value={cover.general_condition} onChange={(v) => setCoverField({ general_condition: v })} disabled={coverLocked} list="cover-good-nil" />

            <div className="cover-form-section">4 &nbsp;Tyre condition</div>
            <div className="cover-field wide">
              <div className="annex-table-wrapper">
                <table className="annex-table cover-tyre-table">
                  <thead>
                    <tr>
                      <th style={{ width: 110 }}></th>
                      <th style={{ width: 90 }}>Nearside mm</th>
                      <th>Nearside make</th>
                      <th style={{ width: 90 }}>Offside mm</th>
                      <th>Offside make</th>
                      <th>Size</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(["front", "rear"] as const).map((axle) => (
                      <tr key={axle}>
                        <td style={{ fontWeight: 600 }}>{axle === "front" ? "Front tyre" : "Rear tyre"}</td>
                        <td>
                          <input type="text" inputMode="decimal" className="annex-cell-input" value={cover[axle].nearside_mm} placeholder="mm" aria-label={`${axle} nearside tread depth (mm)`} disabled={coverLocked} onChange={(e) => setTyre(axle, { nearside_mm: e.target.value })} />
                        </td>
                        <td>
                          <input type="text" className="annex-cell-input" value={cover[axle].nearside_make} placeholder="Not recorded" aria-label={`${axle} nearside tyre make`} disabled={coverLocked} onChange={(e) => setTyre(axle, { nearside_make: e.target.value })} />
                        </td>
                        <td>
                          <input type="text" inputMode="decimal" className="annex-cell-input" value={cover[axle].offside_mm} placeholder="mm" aria-label={`${axle} offside tread depth (mm)`} disabled={coverLocked} onChange={(e) => setTyre(axle, { offside_mm: e.target.value })} />
                        </td>
                        <td>
                          <input type="text" className="annex-cell-input" value={cover[axle].offside_make} placeholder="Not recorded" aria-label={`${axle} offside tyre make`} disabled={coverLocked} onChange={(e) => setTyre(axle, { offside_make: e.target.value })} />
                        </td>
                        <td>
                          <input type="text" className="annex-cell-input" value={cover[axle].size} placeholder="e.g. 215/60 R16" aria-label={`${axle} tyre size`} disabled={coverLocked} onChange={(e) => setTyre(axle, { size: e.target.value })} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <span className="cover-field-hint">Printed per axle as 7mm/Dunlop | 7mm/Dunlop | 215/60 R16.</span>
            </div>

            <div className="cover-form-section">5 &nbsp;Point of impact</div>
            <CoverField label="Point of impact" value={cover.point_of_impact} onChange={(v) => setCoverField({ point_of_impact: v })} disabled={coverLocked} hint="e.g. Rear centre" />
            <CoverField
              label="Note printed under 5"
              value={cover.point_of_impact_note}
              onChange={(v) => setCoverField({ point_of_impact_note: v })}
              disabled={coverLocked}
              hint="The firm writes: See Annex(es) and Motor Accident Reports"
            />

            <div className="cover-form-section">6 &nbsp;Remark</div>
            <div className="cover-field wide" style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              Printed on every report: &quot;This inspection was conducted entirely on a &quot;WITHOUT PREJUDICE&quot; basis and we have not authorised any repair.&quot;
            </div>
          </div>

          {!readOnly && (
            <div className="annex-edit-bar">
              <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 10, flexWrap: "wrap", marginTop: 14 }}>
                {unsavedNote(coverSave, coverDirty)}
                {savedNote(coverSave, coverDirty)}
                <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "6px 12px" }} disabled={!coverDirty || coverLocked} onClick={handleDiscardCover}>
                  <RotateCcw style={{ width: 13, height: 13 }} /> Discard changes
                </button>
                <button type="button" className="btn-primary-modern" style={{ fontSize: 11, padding: "6px 14px" }} disabled={!coverDirty || coverLocked} onClick={() => void handleSaveCover()}>
                  <Save style={{ width: 13, height: 13 }} /> {coverSave === "saving" ? "Saving…" : "Save cover"}
                </button>
              </div>
              {coverSave === "error" && coverError && (
                <div className="setup-error" style={{ marginTop: 10, marginBottom: 0 }}>
                  {coverError}
                </div>
              )}
            </div>
          )}
        </div>

        {/* ---- ANNEX A ------------------------------------------------------- */}
        {opened.annexA && (
          <div style={{ padding: 20, display: activeSubTab === "annexA" ? undefined : "none" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14, flexWrap: "wrap", gap: 10 }}>
              <div>
                <h4 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Annex A : Assessment / Adjustment on Spare Parts</h4>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                  {plate ? `${plate} — ` : ""}List Items take the discount; S/Nett Items are paid in full.
                </div>
                {lockedNote}
              </div>
              {!readOnly && (
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  {/* Past-case prices are the firm's price book (admins and
                      "see all cases" staff); the API refuses anyone else. */}
                  {canViewAllCases(user) && (
                    <button
                      type="button"
                      className="btn-secondary-modern"
                      style={{ fontSize: 11, padding: "5px 12px" }}
                      disabled={annexALocked}
                      title="Prices the unpriced and prefilled lines from past cases of this make and model; lines you priced stay."
                      onClick={() => void handlePrefill()}
                    >
                      <Sparkles style={{ width: 13, height: 13, color: "var(--accent-cyan)" }} /> {prefillBusy ? "Filling prices…" : "Fill prices from past cases"}
                    </button>
                  )}
                  <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} disabled={annexALocked} onClick={handleApplyTradeDiscountToAll}>
                    Set 20% on all List Items
                  </button>
                </div>
              )}
            </div>

            {prefillNote && (
              <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 10 }}>{prefillNote}</div>
            )}

            {!readOnly && <AnnexSuggestionsPanel reportId={report.id} existingNames={annexAItems.map((r) => r.part_name)} onAdd={handleAddSuggestedLines} />}

            <div className="annex-table-wrapper">
              <table className="annex-table">
                <thead>
                  <tr>
                    <th style={{ width: 45 }}>Item</th>
                    <th>Vehicle Parts Description</th>
                    <th>Condition</th>
                    <th>Remarks</th>
                    <th style={{ textAlign: "center", width: 70 }}>Qty</th>
                    <th style={{ textAlign: "right", width: 120 }} title="The line total as the workshop quoted it, not a price per unit">
                      W/shop Est. ($)
                      {!readOnly && <div style={{ fontSize: 9, fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>line total as quoted</div>}
                    </th>
                    <th style={{ textAlign: "center", width: 80 }}>Disc (%)</th>
                    {!readOnly && (
                      <th style={{ textAlign: "center", width: 50 }} title="Nett items (clips, sealants, plate) take no discount">
                        Nett
                      </th>
                    )}
                    <th style={{ textAlign: "right", width: 120 }}>Adjusted Cost ($)</th>
                    {!readOnly && <th style={{ width: 36 }} aria-label="Remove line" />}
                  </tr>
                </thead>
                <tbody>
                  {annexAItems.length === 0 && (
                    <tr>
                      <td colSpan={annexAColumns} style={{ textAlign: "center", color: "var(--text-muted)", padding: "18px 10px" }}>
                        {readOnly ? "No parts recorded" : "No parts recorded yet. Add suggested lines above, or add a line below."}
                      </td>
                    </tr>
                  )}
                  {listRows.length > 0 && sectionRow("List Items")}
                  {listRows.map((item, i) => renderAnnexARow(item, i + 1))}
                  {nettRows.length > 0 && sectionRow("S/Nett Items")}
                  {nettRows.map((item, i) => renderAnnexARow(item, listRows.length + i + 1))}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={5} style={{ textAlign: "right" }}>
                      Total for Spare Parts :
                    </td>
                    <td style={{ textAlign: "right", color: "var(--text-secondary)" }}>{hasPricedParts ? amt(liveParts.est) : "—"}</td>
                    <td></td>
                    {!readOnly && <td></td>}
                    <td style={{ textAlign: "right", color: "var(--accent-cyan)", fontSize: 13 }}>{hasPricedParts ? amt(liveParts.adj) : "—"}</td>
                    {!readOnly && <td></td>}
                  </tr>
                </tfoot>
              </table>
            </div>

            {!readOnly && (
              <div className="annex-edit-bar">
                <datalist id="annex-a-conditions">
                  {CONDITION_SUGGESTIONS.map((c) => (
                    <option key={c} value={c} />
                  ))}
                </datalist>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap", marginTop: 12 }}>
                  <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "6px 12px" }} disabled={annexALocked} onClick={handleAddAnnexLine}>
                    <Plus style={{ width: 13, height: 13 }} /> Add line
                  </button>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                    {unsavedNote(annexASave, annexADirty)}
                    {savedNote(annexASave, annexADirty)}
                    <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "6px 12px" }} disabled={!annexADirty || annexALocked} onClick={handleDiscardAnnexA}>
                      <RotateCcw style={{ width: 13, height: 13 }} /> Discard changes
                    </button>
                    <button type="button" className="btn-primary-modern" style={{ fontSize: 11, padding: "6px 14px" }} disabled={!annexADirty || annexALocked} onClick={() => void handleSaveAnnexA()}>
                      <Save style={{ width: 13, height: 13 }} /> {annexASave === "saving" ? "Saving…" : "Save Annex A"}
                    </button>
                  </div>
                </div>
                {annexAItems.some((r) => !r.part_name.trim()) && (
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 8 }}>Lines with no part description are left out when you save.</div>
                )}
                {annexASave === "error" && annexASaveError && (
                  <div className="setup-error" style={{ marginTop: 10, marginBottom: 0 }}>
                    {annexASaveError}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ---- ANNEX B ------------------------------------------------------- */}
        {opened.annexB && (
          <div style={{ padding: 20, display: activeSubTab === "annexB" ? undefined : "none" }}>
            <div style={{ marginBottom: 14 }}>
              <h4 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Annex B : Adjustment on Labour and Spray Painting</h4>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                {plate ? `${plate} — ` : ""}The adjusted cost is yours to set; there is no discount rule on labour.
              </div>
              {lockedNote}
            </div>

            {!readOnly && (
              <LabourLinesPicker
                reportId={report.id}
                active={opened.annexB}
                existing={annexBItems.map((r) => r.description)}
                disabled={annexBLocked}
                onAdd={handleAddUsualLine}
              />
            )}

            <div className="annex-table-wrapper">
              <table className="annex-table">
                <thead>
                  <tr>
                    <th style={{ width: 45 }}>Item</th>
                    <th>Job Description</th>
                    <th style={{ textAlign: "right", width: 140 }}>Workshop Est. ($)</th>
                    <th style={{ textAlign: "right", width: 140 }}>Adjusted Cost ($)</th>
                    {!readOnly && <th style={{ width: 36 }} aria-label="Remove line" />}
                  </tr>
                </thead>
                <tbody>
                  {annexBItems.length === 0 && (
                    <tr>
                      <td colSpan={readOnly ? 4 : 5} style={{ textAlign: "center", color: "var(--text-muted)", padding: "18px 10px" }}>
                        {readOnly ? "No labour recorded" : "No labour recorded yet. Add the usual lines above, or add a line below."}
                      </td>
                    </tr>
                  )}
                  {annexBItems.map((lab, idx) => {
                    const no = idx + 1;
                    if (readOnly) {
                      return (
                        <tr key={lab._key}>
                          <td style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{no}</td>
                          <td style={{ fontWeight: 600, whiteSpace: "pre-wrap" }}>{lab.description}</td>
                          <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{lab.workshop_est ? amt(lab.workshop_est) : "—"}</td>
                          <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--accent-cyan)" }}>
                            {lab.workshop_est || lab.adjusted_cost ? amt(lab.adjusted_cost) : "—"}
                          </td>
                        </tr>
                      );
                    }
                    const line = `Labour line ${no}`;
                    return (
                      <tr key={lab._key}>
                        <td style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{no}</td>
                        <td style={{ minWidth: 260 }}>
                          <textarea
                            className="annex-cell-input"
                            style={{ fontWeight: 600, resize: "vertical", minHeight: 32, fontFamily: "inherit" }}
                            rows={1}
                            value={lab.description}
                            placeholder="Job description, e.g. To remove, reinstall tail gate parts"
                            aria-label={`${line}: job description`}
                            disabled={annexBLocked}
                            onChange={(e) => updateLabourRow(lab._key, { description: e.target.value })}
                          />
                          {lab.prefilled && lab.suggested_from ? (
                            <span className="annex-prefill-tag" title="The price was filled from past cases, not typed. Typing a price replaces it.">
                              from {lab.suggested_from}
                            </span>
                          ) : lab.suggested_from ? (
                            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>From {lab.suggested_from}</div>
                          ) : null}
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <input
                            type="text"
                            inputMode="decimal"
                            className="annex-cell-input"
                            style={{ width: 110, textAlign: "right", fontFamily: "var(--font-mono)" }}
                            value={lab._est}
                            placeholder="0.00"
                            aria-label={`${line}: workshop estimate`}
                            aria-invalid={isBadAmount(lab._est) ? true : undefined}
                            disabled={annexBLocked}
                            onChange={(e) => updateLabourRow(lab._key, { _est: e.target.value, workshop_est: parseAmount(e.target.value), prefilled: false })}
                          />
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <input
                            type="text"
                            inputMode="decimal"
                            className="annex-cell-input"
                            style={{ width: 110, textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 700 }}
                            value={lab._adj}
                            placeholder="0.00"
                            aria-label={`${line}: adjusted cost`}
                            aria-invalid={isBadAmount(lab._adj) ? true : undefined}
                            disabled={annexBLocked}
                            onChange={(e) => updateLabourRow(lab._key, { _adj: e.target.value, adjusted_cost: parseAmount(e.target.value), prefilled: false })}
                          />
                          {lab.usual_price != null && (
                            <div
                              title={`What past cases adjusted this line to (median), from ${lab.suggested_from || "past reports"}.`}
                              style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2, cursor: "help", whiteSpace: "nowrap" }}
                            >
                              usual {sgd(lab.usual_price)}
                            </div>
                          )}
                        </td>
                        <td style={{ textAlign: "center" }}>
                          <button
                            type="button"
                            className="annex-row-remove"
                            title="Remove this line"
                            aria-label={`Remove labour line ${no}`}
                            disabled={annexBLocked}
                            onClick={() => handleRemoveLabourLine(lab._key)}
                          >
                            <Trash2 style={{ width: 13, height: 13 }} />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={2} style={{ textAlign: "right" }}>
                      TOTAL FOR LABOUR &amp; SPRAY PAINTING :
                    </td>
                    <td style={{ textAlign: "right", color: "var(--text-secondary)" }}>{hasPricedLabour ? amt(liveLabour.est) : "—"}</td>
                    <td style={{ textAlign: "right", color: "var(--accent-cyan)", fontSize: 13 }}>{hasPricedLabour ? amt(liveLabour.adj) : "—"}</td>
                    {!readOnly && <td></td>}
                  </tr>
                </tfoot>
              </table>
            </div>

            {!readOnly && (
              <div className="annex-edit-bar">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap", marginTop: 12 }}>
                  <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "6px 12px" }} disabled={annexBLocked} onClick={handleAddLabourLine}>
                    <Plus style={{ width: 13, height: 13 }} /> Add line
                  </button>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                    {unsavedNote(annexBSave, annexBDirty)}
                    {savedNote(annexBSave, annexBDirty)}
                    <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "6px 12px" }} disabled={!annexBDirty || annexBLocked} onClick={handleDiscardAnnexB}>
                      <RotateCcw style={{ width: 13, height: 13 }} /> Discard changes
                    </button>
                    <button type="button" className="btn-primary-modern" style={{ fontSize: 11, padding: "6px 14px" }} disabled={!annexBDirty || annexBLocked} onClick={() => void handleSaveAnnexB()}>
                      <Save style={{ width: 13, height: 13 }} /> {annexBSave === "saving" ? "Saving…" : "Save Annex B"}
                    </button>
                  </div>
                </div>
                {annexBItems.some((r) => !r.description.trim()) && (
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 8 }}>Lines with no job description are left out when you save.</div>
                )}
                {annexBSave === "error" && annexBSaveError && (
                  <div className="setup-error" style={{ marginTop: 10, marginBottom: 0 }}>
                    {annexBSaveError}
                  </div>
                )}
              </div>
            )}

            {/* The Summary printed under the labour table, from the tables as saved. */}
            <div className="annex-summary">
              <table className="annex-table">
                <thead>
                  <tr>
                    <th>Summary</th>
                    <th style={{ textAlign: "right", width: 160 }}>Workshop Est. ($)</th>
                    <th style={{ textAlign: "right", width: 160 }}>Adjusted Cost ($)</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>TOTAL FOR SPARE PARTS :</td>
                    <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{savedParts.est || savedParts.adj ? amt(savedParts.est) : "—"}</td>
                    <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{savedParts.est || savedParts.adj ? amt(savedParts.adj) : "—"}</td>
                  </tr>
                  <tr>
                    <td>TOTAL FOR LABOUR &amp; SPRAY PAINTING :</td>
                    <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{savedLabour.est || savedLabour.adj ? amt(savedLabour.est) : "—"}</td>
                    <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{savedLabour.est || savedLabour.adj ? amt(savedLabour.adj) : "—"}</td>
                  </tr>
                </tbody>
                <tfoot>
                  <tr>
                    <td>TOTAL REPAIR COSTS :</td>
                    <td style={{ textAlign: "right", color: "var(--text-secondary)" }}>{anyPriced ? amt(savedTotal.est) : "—"}</td>
                    <td style={{ textAlign: "right", color: "var(--accent-cyan)", fontSize: 13 }}>{anyPriced ? amt(savedTotal.adj) : "—"}</td>
                  </tr>
                </tfoot>
              </table>
              <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "8px 12px" }}>
                From Annex A and Annex B as last saved.{annexADirty || annexBDirty ? " Unsaved changes are not in these figures yet." : ""}
              </div>
            </div>
          </div>
        )}

        {/* ---- ANNEX C ------------------------------------------------------- */}
        {opened.annexC && (
          <div style={{ padding: 20, display: activeSubTab === "annexC" ? undefined : "none" }}>
            <div style={{ marginBottom: 14 }}>
              <h4 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Annex C : Recommendation</h4>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                {plate ? `${plate} — ` : ""}The letter as the report prints it. Only the lump sum and the working days are typed here; the rest comes from the cover page and the annexes as saved.
              </div>
              {lockedNote}
            </div>

            <div className="annex-letter">
              <p>
                We have inspected thoroughly the actual damages found on the vehicle and our assessment is appended in the Annex(es) attached. The condition and age of the vehicle was
                considered before we reached our recommendation as to whether the parts need replacement or repair.
              </p>
              <p>
                {workshopName ? <Var>{workshopName}</Var> : <Missing>(Workshop not recorded — see the cover page)</Missing>} estimated repair cost is{" "}
                {anyPriced ? <Var>{formatSgd(savedTotal.est)}</Var> : <Missing>(not yet priced)</Missing>} and we have adjusted it to{" "}
                {anyPriced ? <Var>{formatSgd(savedTotal.adj)}</Var> : <Missing>(not yet priced)</Missing>} on a part by part basis. However, taking into consideration the age of the
                vehicle and the availability of used/recondition components. We, therefore recommend a contract lump sum repair cost.
              </p>
              <p>
                The repairer have agreed to undertake the repairs on a contract lump sum of {letterLump != null ? <Var>{formatSgd(letterLump)}</Var> : <Missing>(to be agreed)</Missing>},
                according to acceptable quality and standard. (Repairer discretion to repair parts, to replace with old parts, or to replace with OEM or genuine parts).
              </p>
              <p>
                The entire repair of the damaged vehicle should be completed within a reasonable period of {letterDays != null ? <Var>{letterDays}</Var> : <Missing>(to be agreed)</Missing>}{" "}
                working days.
              </p>
              <p>
                We have advised the repairer that the inspection was conducted strictly on a WITHOUT PREJUDICE basis and we have not given any instruction and authorisation to proceed
                with the repair of the vehicle.
              </p>
              <p>We are reverting the matter to you for your discretion and enclosed is our invoice for service rendered.</p>

              <div className="letter-closing">
                <p style={{ marginBottom: 4 }}>Yours faithfully,</p>
                {firmName && <p style={{ marginBottom: 4, fontWeight: 700, textTransform: "uppercase" }}>{firmName}</p>}
                {isSignedOff ? (
                  <>
                    {signOff?.signature_data_url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={signOff.signature_data_url} alt="Signature" style={{ height: 60, display: "block", margin: "8px 0" }} />
                    ) : (
                      <div style={{ height: 40 }} />
                    )}
                    <p style={{ marginBottom: 2, fontWeight: 700, textTransform: "uppercase" }}>{surveyorName || "Not recorded"}</p>
                    <p style={{ marginBottom: 2 }}>{signOff?.signatory_title || DEFAULT_TITLE}</p>
                    {qualifications && <p style={{ marginBottom: 0 }}>{qualifications}</p>}
                  </>
                ) : (
                  <div className="letter-draft">DRAFT — not signed off</div>
                )}
              </div>
            </div>

            <div className="annex-c-inputs">
              <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
                Contract lump sum (S$)
                <input
                  type="text"
                  inputMode="decimal"
                  value={lumpText}
                  placeholder="Not recorded"
                  aria-invalid={isBadAmount(lumpText) ? true : undefined}
                  disabled={annexCLocked}
                  onChange={(e) => setLumpText(e.target.value)}
                  style={{ ...fieldInputStyle, fontFamily: "var(--font-mono)", width: 160 }}
                />
              </label>
              <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
                Working days
                <input
                  type="text"
                  inputMode="numeric"
                  value={daysText}
                  placeholder="Not recorded"
                  aria-invalid={badDays ? true : undefined}
                  disabled={annexCLocked}
                  onChange={(e) => setDaysText(e.target.value)}
                  style={{ ...fieldInputStyle, fontFamily: "var(--font-mono)", width: 100 }}
                />
              </label>
              {!readOnly && (
                <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginLeft: "auto" }}>
                  {unsavedNote(annexCSave, annexCDirty)}
                  {savedNote(annexCSave, annexCDirty)}
                  <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "6px 12px" }} disabled={!annexCDirty || annexCLocked} onClick={handleDiscardAnnexC}>
                    <RotateCcw style={{ width: 13, height: 13 }} /> Discard changes
                  </button>
                  <button
                    type="button"
                    className="btn-primary-modern"
                    style={{ fontSize: 11, padding: "6px 14px" }}
                    disabled={!annexCDirty || annexCLocked || isBadAmount(lumpText) || badDays}
                    onClick={() => void handleSaveAnnexC()}
                  >
                    <Save style={{ width: 13, height: 13 }} /> {annexCSave === "saving" ? "Saving…" : "Save Annex C"}
                  </button>
                </div>
              )}
            </div>
            {annexCSave === "error" && annexCSaveError && (
              <div className="setup-error" style={{ marginTop: 10, marginBottom: 0 }}>
                {annexCSaveError}
              </div>
            )}

            {/* ---- Sign-off ------------------------------------------------- */}
            <div className="signoff-box">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
                <div>
                  <h4 style={{ fontSize: 14, fontWeight: 800, margin: 0 }}>Sign-off</h4>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                    {isSignedOff
                      ? "Signed off. The report prints with the signature below."
                      : "Signing off locks the case with the cover page and Annexes A to C as last saved, and prints the signature on Annex C."}
                  </div>
                </div>
                {isSignedOff ? (
                  <span className="chip-severity minor" style={{ fontSize: 11, display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <CheckCircle2 style={{ width: 14, height: 14 }} /> Signed off
                    {signOff?.signature_date && (
                      <span suppressHydrationWarning>on {new Date(signOff.signature_date).toLocaleDateString("en-SG")}</span>
                    )}
                  </span>
                ) : (
                  <span className="chip-severity moderate" style={{ fontSize: 11 }}>
                    Not signed off
                  </span>
                )}
              </div>

              <div className="signoff-grid">
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  <div>
                    <div className="detail-field-label">Signed by</div>
                    <div style={{ fontSize: 14, fontWeight: 800, color: surveyorName ? "var(--text-primary)" : "var(--text-muted)" }}>{surveyorName || "Not recorded"}</div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                      {qualifications || "No qualifications on the profile"}
                      {licenseNumber ? ` · Licence ${licenseNumber}` : ""}
                    </div>
                  </div>
                  <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
                    Title printed under the name
                    <input
                      type="text"
                      value={isSignedOff ? signOff?.signatory_title || DEFAULT_TITLE : signatoryTitle}
                      disabled={signOffLocked}
                      onChange={(e) => setSignatoryTitle(e.target.value)}
                      style={fieldInputStyle}
                    />
                  </label>
                  <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
                    Remarks (kept on the case, not printed)
                    <textarea
                      value={isSignedOff ? signOff?.remarks || "" : remarks}
                      placeholder={isSignedOff ? "None" : "Anything the next person should know"}
                      rows={3}
                      disabled={signOffLocked}
                      onChange={(e) => setRemarks(e.target.value)}
                      style={{ ...fieldInputStyle, resize: "vertical" }}
                    />
                  </label>
                </div>

                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <div className="detail-field-label">Signature</div>
                    {!signOffLocked && (
                      <button
                        type="button"
                        style={{ fontSize: 11, background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}
                        onClick={handleClearSignature}
                      >
                        <Eraser style={{ width: 12, height: 12 }} /> Clear
                      </button>
                    )}
                  </div>
                  {!signOffLocked ? (
                    <canvas
                      ref={canvasRef}
                      width={360}
                      height={160}
                      className="signature-canvas-pad"
                      style={{ width: "100%", maxWidth: 360, background: "#ffffff" }}
                      aria-label="Draw your signature"
                      onMouseDown={handlePadDown}
                      onMouseUp={() => setIsDrawing(false)}
                      onMouseLeave={() => setIsDrawing(false)}
                      onMouseMove={handlePadMove}
                    />
                  ) : (
                    <div className="signature-canvas-pad" style={{ cursor: "default" }}>
                      {/* A signature only once signed off -- this used to
                          render the surveyor's name as a cursive signature
                          on unsigned cases. */}
                      {isSignedOff ? (
                        signOff?.signature_data_url ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={signOff.signature_data_url} alt="Signature" style={{ maxHeight: 120, maxWidth: "100%" }} />
                        ) : (
                          <div style={{ fontFamily: "Georgia, serif", fontStyle: "italic", fontSize: 22, color: "var(--text-primary)" }}>{surveyorName || "Signed"}</div>
                        )
                      ) : (
                        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Not signed.</div>
                      )}
                    </div>
                  )}
                  {!signOffLocked && (
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                      Draw with the mouse. Left blank, the report prints the name and title without a signature image.
                    </div>
                  )}
                </div>
              </div>

              <label className="compliance-check-item">
                <input type="checkbox" checked={declared} disabled={signOffLocked} onChange={(e) => setDeclared(e.target.checked)} />
                <span>
                  I inspected the vehicle, and Annexes A to C are my assessment of the actual damage found, made strictly on a WITHOUT PREJUDICE basis; no repair was authorised.
                </span>
              </label>

              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: 10, borderTop: "1px solid var(--border-color)", flexWrap: "wrap", gap: 10 }}>
                <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {isSignedOff ? "This case is locked. To change anything, reopen the report from the edit tab." : "Save every tab first: sign-off takes the case as saved."}
                </div>

                {signOffError && <div style={{ color: "var(--danger, #ef4444)", fontSize: 12 }}>{signOffError}</div>}

                {profileIncomplete && (
                  <div style={{ color: "var(--badge-amber-text)", fontSize: 12 }}>
                    {noFullName ? "Your profile has no full name yet, so the report will print your username." : "Your profile has no licence number, so the report will print without one."}{" "}
                    <a href="/settings#my-profile" className="setup-help-link">
                      Add it in Settings
                    </a>
                  </div>
                )}

                {!isSignedOff && !canSignOff ? (
                  <div style={{ color: "var(--badge-amber-text)", fontSize: 12 }}>Your account can&apos;t sign off reports yet. Ask an admin to sign it off, or to allow you.</div>
                ) : !isSignedOff ? (
                  <button type="button" className="btn-primary-modern" style={{ padding: "10px 24px", fontSize: 13 }} disabled={isSigningOff} onClick={() => void handleSignOffSubmit()}>
                    <CheckCircle2 style={{ width: 16, height: 16 }} />
                    {isSigningOff ? "Signing off…" : "Sign off"}
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
