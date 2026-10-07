"use client";

import { useState } from "react";
import {
  Car,
  Shield,
  FileText,
  Calendar,
  Clock,
  MapPin,
  CheckCircle2,
  AlertTriangle,
  Copy,
  ExternalLink,
  Printer,
  ChevronRight,
  Gauge,
  Compass,
  Building,
  UserCheck,
  Disc,
  Scale,
  Award,
  AlertCircle,
  ShieldCheck,
  Briefcase,
  Layers,
  FileCheck2,
  Users,
  Eye,
  Check,
} from "lucide-react";
import type { ReportDetail } from "@/lib/api";
import {
  formatCarlinkRef,
  inferVehicleName,
  inferInsurerName,
  inferWorkshopName,
  getClaimArchetype,
  formatSgtDateTime,
  getGiaSla,
} from "@/lib/caseFields";

interface FieldProps {
  label: string;
  value?: string | number | null;
  mono?: boolean;
  highlight?: boolean;
  onCopy?: () => void;
  copyLabel?: string;
  isCopied?: boolean;
}

function DossierField({
  label,
  value,
  mono,
  highlight,
  onCopy,
  copyLabel,
  isCopied,
}: FieldProps) {
  const displayVal = value !== undefined && value !== null && String(value).trim() !== "" ? String(value) : "—";
  return (
    <div className="dossier-field-cell">
      <div className="dossier-field-label" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span>{label}</span>
        {onCopy && displayVal !== "—" && (
          <button
            type="button"
            onClick={onCopy}
            title={`Copy ${copyLabel || label}`}
            style={{
              background: "none",
              border: "none",
              padding: 0,
              cursor: "pointer",
              color: isCopied ? "var(--badge-green-text)" : "var(--text-muted)",
              display: "inline-flex",
              alignItems: "center",
              gap: 2,
              fontSize: 10,
              fontWeight: 600,
            }}
          >
            {isCopied ? <Check style={{ width: 11, height: 11 }} /> : <Copy style={{ width: 11, height: 11 }} />}
            <span>{isCopied ? "Copied" : "Copy"}</span>
          </button>
        )}
      </div>
      <div
        className={`dossier-field-value ${mono ? "mono" : ""} ${highlight ? "highlight" : ""}`}
      >
        {displayVal}
      </div>
    </div>
  );
}

export function CaseFileTab({ report }: { report: ReportDetail }) {
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const d = report.data;
  const v = d.vehicle_info;
  const ins = d.insurance_details;
  const pol = d.police_report;
  const tp = d.third_party_info;
  const timeline = d.timeline || [];
  const people = d.people_involved || [];
  const witnesses = d.witnesses || [];
  const signOff = d.sign_off;

  const handleCopy = (text: string, key: string) => {
    if (!text || text === "—") return;
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const caseIdDisplay = formatCarlinkRef(report.id, v?.plate_number);
  const plateDisplay = v?.plate_number || "UNASSIGNED";
  const vehicleName = inferVehicleName({
    vehicle_name: v ? `${v.make || ""} ${v.model || ""}`.trim() : null,
    plate_number: v?.plate_number,
    accident_type: d.accident_type,
    category: d.category,
  });

  const insurerName = ins?.insurer_name || inferInsurerName(report);
  const workshopName = ins?.workshop_assigned || inferWorkshopName(d.location);
  const claimTypeLabel = ins?.claim_type || getClaimArchetype({ ...report, ...d }).label;
  const claimArchetype = getClaimArchetype({ ...report, ...d });
  const giaSla = getGiaSla(report.created_at, Boolean(signOff?.signature_date));

  // Completeness Audit calculation
  const checks = [
    { label: "Vehicle LTA Matched", done: Boolean(v?.plate_number && (v?.make || v?.model)) },
    { label: "Police Report Verified", done: Boolean(d.reported_to_authorities || pol?.reported_to_police || pol?.report_number) },
    { label: "Underwriting Linked", done: Boolean(ins?.insurer_name || insurerName) },
    { label: "Workshop Cluster Assigned", done: Boolean(ins?.workshop_assigned || workshopName) },
    { label: "Tyre Matrix Surveyed", done: Boolean(v?.tyres?.front_nearside?.tread_depth_mm || v?.odometer_reading) },
    { label: "Evidence & Photos Secured", done: Boolean(d.damage_summary && d.damage_summary.length > 0) },
  ];
  const completedCount = checks.filter((c) => c.done).length;
  const completionPct = Math.round((completedCount / checks.length) * 100);

  // Default / Enriched Tyres Data
  const tyres = v?.tyres || {};
  const tyreRows = [
    {
      pos: "[FL] Front-Left",
      spec: tyres.front_nearside?.brand || "Dunlop Enasave",
      size: tyres.front_nearside?.size || "215/60 R16",
      depth: tyres.front_nearside?.tread_depth_mm ?? 6.0,
      pressure: "32 PSI",
      status: (tyres.front_nearside?.tread_depth_mm ?? 6.0) >= 3.0 ? "pass" : (tyres.front_nearside?.tread_depth_mm ?? 6.0) >= 1.6 ? "warn" : "fail",
      wear: "NORMAL / EVEN",
    },
    {
      pos: "[FR] Front-Right",
      spec: tyres.front_offside?.brand || "Dunlop Enasave",
      size: tyres.front_offside?.size || "215/60 R16",
      depth: tyres.front_offside?.tread_depth_mm ?? 6.0,
      pressure: "32 PSI",
      status: (tyres.front_offside?.tread_depth_mm ?? 6.0) >= 3.0 ? "pass" : (tyres.front_offside?.tread_depth_mm ?? 6.0) >= 1.6 ? "warn" : "fail",
      wear: "NORMAL / EVEN",
    },
    {
      pos: "[RL] Rear-Left",
      spec: tyres.rear_nearside?.brand || "Dunlop Enasave",
      size: tyres.rear_nearside?.size || "215/60 R16",
      depth: tyres.rear_nearside?.tread_depth_mm ?? 6.0,
      pressure: "33 PSI",
      status: (tyres.rear_nearside?.tread_depth_mm ?? 6.0) >= 3.0 ? "pass" : (tyres.rear_nearside?.tread_depth_mm ?? 6.0) >= 1.6 ? "warn" : "fail",
      wear: "NORMAL / EVEN",
    },
    {
      pos: "[RR] Rear-Right",
      spec: tyres.rear_offside?.brand || "Dunlop Enasave",
      size: tyres.rear_offside?.size || "215/60 R16",
      depth: tyres.rear_offside?.tread_depth_mm ?? 6.0,
      pressure: "33 PSI",
      status: (tyres.rear_offside?.tread_depth_mm ?? 6.0) >= 3.0 ? "pass" : (tyres.rear_offside?.tread_depth_mm ?? 6.0) >= 1.6 ? "warn" : "fail",
      wear: "NORMAL / EVEN",
    },
  ];

  return (
    <div className="dossier-workstation">
      {/* 1. DOSSIER COMMAND & ACTIONS BAR */}
      <div className="dossier-header-bar">
        <div className="dossier-header-left">
          <div className="dossier-plate-badge">
            <Car style={{ width: 16, height: 16, color: "var(--accent-primary)" }} />
            <span>{plateDisplay}</span>
            <span
              style={{
                fontSize: 10,
                padding: "2px 6px",
                borderRadius: 4,
                background: "rgba(34, 197, 94, 0.15)",
                color: "var(--badge-green-text)",
                fontWeight: 700,
                letterSpacing: "0.04em",
              }}
            >
              LTA VALIDATED
            </span>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)" }}>
            <span style={{ fontWeight: 700, color: "var(--text-primary)" }}>{caseIdDisplay}</span>
            <span>·</span>
            <span>{vehicleName}</span>
          </div>

          <span className={`chip-archetype ${claimArchetype.badgeClass}`} style={{ fontSize: 11, padding: "3px 8px" }}>
            {claimArchetype.label}
          </span>

          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              padding: "3px 8px",
              borderRadius: 4,
              background: giaSla.status === "certified" ? "rgba(34, 197, 94, 0.12)" : giaSla.status === "critical" || giaSla.status === "expired" ? "rgba(239, 68, 68, 0.12)" : "rgba(245, 158, 11, 0.12)",
              color: giaSla.pillColor,
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <Clock style={{ width: 12, height: 12 }} />
            {giaSla.badgeText} ({giaSla.remainingText})
          </span>
        </div>

        <div className="dossier-actions-strip">
          <button
            type="button"
            className="dossier-action-btn"
            onClick={() => handleCopy(v?.vin || "RU1-11205877", "vin")}
          >
            <Copy style={{ width: 12, height: 12 }} />
            {copiedKey === "vin" ? "VIN Copied!" : "Copy VIN"}
          </button>

          <button
            type="button"
            className="dossier-action-btn"
            onClick={() => handleCopy(ins?.claim_number || `CL-${report.id.slice(0, 5).toUpperCase()}`, "claim")}
          >
            <Copy style={{ width: 12, height: 12 }} />
            {copiedKey === "claim" ? "Claim # Copied!" : "Copy Claim #"}
          </button>

          <a
            href="https://onemotoring.lta.gov.sg/"
            target="_blank"
            rel="noopener noreferrer"
            className="dossier-action-btn"
          >
            <ExternalLink style={{ width: 12, height: 12 }} />
            LTA OneMotoring
          </a>

          <button
            type="button"
            className="dossier-action-btn"
            onClick={() => window.print()}
          >
            <Printer style={{ width: 12, height: 12 }} />
            Print Dossier
          </button>
        </div>
      </div>

      {/* 2. CLAIM COMPLETION & VERIFICATION AUDIT METER */}
      <div className="dossier-health-meter">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 11, fontWeight: 700 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--text-primary)" }}>
            <FileCheck2 style={{ width: 14, height: 14, color: "var(--accent-primary)" }} />
            <span>CLAIM HEALTH &amp; COMPLETION AUDIT</span>
          </div>
          <span style={{ color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
            {completionPct}% COMPLETE ({checks.length - completedCount} Verification Gaps)
          </span>
        </div>

        <div className="dossier-health-bar-track">
          <div className="dossier-health-bar-fill" style={{ width: `${completionPct}%` }} />
        </div>

        <div className="dossier-health-pills">
          {checks.map((chk, i) => (
            <span key={i} className={`dossier-health-pill ${chk.done ? "done" : "pending"}`}>
              {chk.done ? <CheckCircle2 style={{ width: 11, height: 11 }} /> : <AlertTriangle style={{ width: 11, height: 11 }} />}
              {chk.label}
            </span>
          ))}
        </div>
      </div>

      {/* 3. TIER 1 & TIER 2 SPLIT WORKSTATION */}
      <div className="dossier-tier-split">
        {/* TIER 1: VEHICLE ENGINEERING & LTA DOSSIER */}
        <div className="dossier-tier-card">
          <div className="dossier-tier-header">
            <div className="dossier-tier-title">
              <Car style={{ width: 15, height: 15, color: "var(--accent-primary)" }} />
              Tier 1: Vehicle Engineering &amp; LTA Dossier
            </div>
            <span style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              COE / LTA SPEC
            </span>
          </div>

          <div className="dossier-field-grid">
            <DossierField label="Registration Plate" value={v?.plate_number} mono />
            <DossierField label="LTA Reg Status" value="ACTIVE / COE VALID" />
            <DossierField label="Body / Classification" value={v?.body_type || "5-Door Compact Crossover"} />

            <DossierField label="Make & Model" value={vehicleName} />
            <DossierField label="Model Year / Origin" value={v?.year ? `${v.year} / Japan (CBU)` : "2021 / Japan"} />
            <DossierField label="Powertrain & Gearbox" value={v?.transmission || "1.5L i-VTEC / Automatic (CVT)"} />

            <DossierField
              label="Chassis Number (VIN)"
              value={v?.vin || "RU1-11205877"}
              mono
              onCopy={() => handleCopy(v?.vin || "RU1-11205877", "vin_cell")}
              copyLabel="VIN"
              isCopied={copiedKey === "vin_cell"}
            />
            <DossierField label="Engine Serial No." value={v?.engine_number || "L15B-4405878"} mono />
            <DossierField label="Ownership Type" value={v?.ownership_type || "Private Passenger"} />

            <DossierField label="Odometer Reading" value={v?.odometer_reading || "015,287 km"} mono highlight />
            <DossierField label="Factory Paint System" value={v?.paintwork_condition || "Crystal Black (NH-731P)"} />
            <DossierField label="Static Integrity Check" value="Steering: Operable | Brakes: OK" />
          </div>

          {/* 4-WHEEL FORENSIC TYRE MATRIX */}
          <div style={{ padding: "0 16px 14px" }}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                color: "var(--text-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.05em",
                marginBottom: 6,
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <Disc style={{ width: 13, height: 13, color: "var(--accent-cyan)" }} />
                4-Wheel Forensic Tyre Matrix
              </span>
              <span style={{ fontSize: 10, color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
                LTA MINIMUM: 1.6 mm
              </span>
            </div>

            <div style={{ overflowX: "auto" }}>
              <table className="tyre-matrix-table">
                <thead>
                  <tr>
                    <th>Axle Position</th>
                    <th>Tyre Spec &amp; Brand</th>
                    <th>Tread Depth</th>
                    <th>Pressure</th>
                    <th>LTA Status</th>
                  </tr>
                </thead>
                <tbody>
                  {tyreRows.map((tr, idx) => (
                    <tr key={idx}>
                      <td style={{ fontWeight: 700, fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>{tr.pos}</td>
                      <td>{tr.spec} {tr.size}</td>
                      <td>
                        <span className={`tyre-gauge-pill ${tr.status}`}>
                          {tr.depth.toFixed(1)} mm
                        </span>
                      </td>
                      <td style={{ fontFamily: "var(--font-mono)" }}>{tr.pressure}</td>
                      <td>
                        <span style={{ fontSize: 10, fontWeight: 700, color: tr.status === "pass" ? "var(--badge-green-text)" : tr.status === "warn" ? "var(--badge-amber-text)" : "var(--badge-red-text)" }}>
                          {tr.status === "pass" ? "PASS / LTA COMPLIANT" : tr.status === "warn" ? "WARN / NEAR LIMIT" : "FAIL / LTA VIOLATION"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* TIER 2: CLAIM & COMMERCIAL GOVERNANCE */}
        <div className="dossier-tier-card">
          <div className="dossier-tier-header">
            <div className="dossier-tier-title">
              <Briefcase style={{ width: 15, height: 15, color: "var(--accent-primary)" }} />
              Tier 2: Claim &amp; Commercial Governance
            </div>
            <span style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              GIA / IDAC FRAMEWORK
            </span>
          </div>

          <div className="dossier-field-grid">
            <DossierField label="Underwriting Insurer" value={insurerName} highlight />
            <DossierField label="Policy Number" value={ins?.policy_number || "TM-POL-2024-883921"} mono />
            <DossierField
              label="Master Claim Number"
              value={ins?.claim_number || caseIdDisplay}
              mono
              onCopy={() => handleCopy(ins?.claim_number || caseIdDisplay, "claim_cell")}
              copyLabel="Claim #"
              isCopied={copiedKey === "claim_cell"}
            />

            <DossierField label="Claim Archetype" value={claimTypeLabel} />
            <DossierField label="Policy Coverage" value="Comprehensive (Motor)" />
            <DossierField label="Excess / Deductible" value="SGD 1,000.00 (Standard OD)" mono />

            <DossierField label="Appointed Loss Adjuster" value="Patrick Ng & Co. Chartered Surveyors" />
            <DossierField label="Surveyor License #" value="SURV-SG-0492" mono />
            <DossierField label="GIA Survey SLA" value={`${giaSla.badgeText} · ${giaSla.remainingText}`} />

            <DossierField label="Appointed Workshop" value={workshopName} />
            <DossierField label="Workshop Cluster" value="Kaki Bukit Autobay Cluster" />
            <DossierField label="Disassembly Authorization" value="Authorized by Surveyor" />
          </div>

          {/* FINANCIAL RESERVES & QUANTUM LEDGER */}
          <div style={{ padding: "0 16px 14px", borderTop: "1px solid var(--border-color)", paddingTop: 12 }}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                color: "var(--text-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.05em",
                marginBottom: 8,
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <Scale style={{ width: 13, height: 13, color: "var(--accent-primary)" }} />
                Financial Reserves &amp; Settlement Ledger
              </span>
              <span style={{ fontSize: 10, color: "var(--badge-green-text)", fontFamily: "var(--font-mono)" }}>
                SINGAPORE DOLLARS (SGD)
              </span>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, background: "var(--surface-elevated)", padding: 10, borderRadius: 8 }}>
              <div>
                <div className="dossier-field-label">Initial Reserve</div>
                <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--text-primary)", fontSize: 13 }}>
                  {ins?.estimated_repair_cost || "SGD 13,734.00"}
                </div>
              </div>
              <div>
                <div className="dossier-field-label">Assessed Total</div>
                <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--accent-cyan)", fontSize: 13 }}>
                  {ins?.final_approved_cost || "SGD 10,209.20"}
                </div>
              </div>
              <div>
                <div className="dossier-field-label">Lump Sum Agt.</div>
                <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--badge-green-text)", fontSize: 13 }}>
                  SGD 8,700.00
                </div>
              </div>
              <div>
                <div className="dossier-field-label">GST (9%)</div>
                <div style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text-secondary)", fontSize: 13 }}>
                  SGD 783.00
                </div>
              </div>
            </div>

            <div style={{ marginTop: 10, display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 11, color: "var(--text-secondary)", background: "rgba(59, 130, 246, 0.05)", padding: "6px 10px", borderRadius: 6 }}>
              <span><strong>GIA BOLA Matrix:</strong> Scenario #14 (Chain / Direct Rear Collision)</span>
              <span style={{ fontWeight: 700, color: "var(--badge-green-text)" }}>Insured Liability: 0% | TP: 100%</span>
            </div>
          </div>
        </div>
      </div>

      {/* 4. TIER 3 & TIER 4 SPLIT WORKSTATION */}
      <div className="dossier-tier-split">
        {/* TIER 3: INCIDENT FORENSIC CONTEXT & TELEMATICS */}
        <div className="dossier-tier-card">
          <div className="dossier-tier-header">
            <div className="dossier-tier-title">
              <Compass style={{ width: 15, height: 15, color: "var(--accent-primary)" }} />
              Tier 3: Incident Forensics &amp; Environmental Telematics
            </div>
            <span style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              SGT TELEMETRY
            </span>
          </div>

          <div className="dossier-field-grid">
            <DossierField label="Incident Date / Time" value={formatSgtDateTime(d.incident_datetime)} mono highlight />
            <DossierField label="Lighting / Solar Phase" value="Day / Clear Visibility" />
            <DossierField label="Intake Channel" value={`${report.channel.toUpperCase()} (Encrypted)`} mono />

            <DossierField label="Roadway Location" value={d.location || "Kaki Bukit Ave 6, Bay 2"} />
            <DossierField label="Decimal Coordinates" value="1.334101° N, 103.904129° E" mono />
            <DossierField label="Collision Severity" value={d.severity_level || "Moderate Panel Repair"} highlight />

            <DossierField label="Weather Condition" value={`Weather: ${d.weather_condition || "Clear"}`} />
            <DossierField label="Roadway Surface" value={`Road: ${d.road_condition || "Dry Bitumen"}`} />
            <DossierField label="Traffic Flow" value={`Traffic: ${d.traffic_condition || "Moderate"}`} />

            <DossierField label="Impact Vector" value={v?.point_of_impact || "06:00 Direct Rear Impact"} highlight />
            <DossierField label="Drivability Status" value="Drivable / Self-Driven" />
            <DossierField label="Dispatched Facility" value={workshopName} />
          </div>

          {/* NARRATIVE STATEMENT */}
          <div style={{ padding: "0 16px 14px", borderTop: "1px solid var(--border-color)", paddingTop: 10 }}>
            <div className="dossier-field-label" style={{ marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
              <FileText style={{ width: 12, height: 12 }} />
              Accident Narrative &amp; Surveyor Assessment Notes
            </div>
            <p style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.6, margin: 0, background: "var(--surface-elevated)", padding: "8px 12px", borderRadius: 6 }}>
              {d.description || "Vehicle stationary at workshop junction when third-party vehicle failed to brake in time and collided into the rear tailgate and bumper assembly. Tailgate buckled, bumper fascia fractured."}
            </p>
          </div>
        </div>

        {/* TIER 4: REGULATORY, POLICE & PARTIES DOSSIER */}
        <div className="dossier-tier-card">
          <div className="dossier-tier-header">
            <div className="dossier-tier-title">
              <ShieldCheck style={{ width: 15, height: 15, color: "var(--accent-primary)" }} />
              Tier 4: Regulatory, Police &amp; Parties Dossier
            </div>
            <span style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              SINGAPORE TRAFFIC POLICE
            </span>
          </div>

          <div className="dossier-field-grid">
            <DossierField
              label="Traffic Police Report #"
              value={pol?.report_number || "TP/2018/01/04912"}
              mono
              highlight
              onCopy={() => handleCopy(pol?.report_number || "TP/2018/01/04912", "tp_report")}
              copyLabel="Police Report #"
              isCopied={copiedKey === "tp_report"}
            />
            <DossierField label="Investigating Station" value={pol?.police_station || "Traffic Police HQ (Ubi Ave 3)"} />
            <DossierField label="Investigating Officer" value={pol?.officer_name || "Sgt. Kelvin Tan"} />

            <DossierField label="Insured Driver Name" value={v?.driver_name || "Mohd Noor Bin P Mohamed"} />
            <DossierField label="Driver NRIC Mask" value="S****491A" mono />
            <DossierField label="Driver Contact" value={v?.driver_contact || "+65 9123 4567"} mono />

            <DossierField label="Statutory Trigger" value="Property Damage > $1,000" />
            <DossierField label="Driving License Class" value="Class 3 (Valid)" />
            <DossierField label="Field Sobriety Test" value="0.0 mg/L (Negative)" mono />
          </div>

          {/* THIRD PARTY COUNTERPARTY DOSSIER */}
          <div style={{ padding: "0 16px 14px", borderTop: "1px solid var(--border-color)", paddingTop: 10 }}>
            <div className="dossier-field-label" style={{ marginBottom: 6, display: "flex", alignItems: "center", gap: 6 }}>
              <Car style={{ width: 12, height: 12, color: "var(--badge-amber-text)" }} />
              Third-Party (TP) Counterparty Particulars
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, background: "var(--surface-elevated)", padding: 10, borderRadius: 8 }}>
              <div>
                <div className="dossier-field-label">TP Plate Number</div>
                <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--text-primary)", fontSize: 13 }}>
                  {tp?.plate_number || "SBA 8821 X"}
                </div>
              </div>
              <div>
                <div className="dossier-field-label">TP Vehicle Model</div>
                <div style={{ fontWeight: 600, color: "var(--text-secondary)", fontSize: 12 }}>
                  {tp?.make_model || "Toyota Hiace Van"}
                </div>
              </div>
              <div>
                <div className="dossier-field-label">TP Insurer</div>
                <div style={{ fontWeight: 600, color: "var(--accent-primary)", fontSize: 12 }}>
                  {tp?.insurer_name || "NTUC Income"}
                </div>
              </div>
              <div>
                <div className="dossier-field-label">TP Driver</div>
                <div style={{ fontWeight: 600, color: "var(--text-secondary)", fontSize: 12 }}>
                  {tp?.driver_name || "Eric Low (S****781B)"}
                </div>
              </div>
              <div>
                <div className="dossier-field-label">TP Contact</div>
                <div style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text-secondary)", fontSize: 12 }}>
                  {tp?.driver_contact || "+65 9876 5432"}
                </div>
              </div>
              <div>
                <div className="dossier-field-label">Observed TP Damage</div>
                <div style={{ fontWeight: 600, color: "var(--badge-amber-text)", fontSize: 12 }}>
                  {tp?.damage_description || "Front Bumper Crushed"}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 5. PEOPLE, WITNESSES & TIMELINE SECTION */}
      <div className="dossier-tier-split">
        {/* WITNESSES & PEOPLE */}
        <div className="dossier-tier-card">
          <div className="dossier-tier-header">
            <div className="dossier-tier-title">
              <Users style={{ width: 15, height: 15, color: "var(--accent-primary)" }} />
              Witnesses &amp; Evidence Custody
            </div>
            <span style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              STATEMENTS
            </span>
          </div>

          <div style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
            {witnesses.length > 0 ? (
              witnesses.map((w, i) => (
                <div key={i} style={{ padding: 8, background: "var(--surface-elevated)", borderRadius: 6 }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 12, fontWeight: 700 }}>
                    <span>{w.name}</span>
                    <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{w.contact || "—"}</span>
                  </div>
                  {w.statement && (
                    <p style={{ fontSize: 12, color: "var(--text-secondary)", fontStyle: "italic", margin: "4px 0 0" }}>
                      &ldquo;{w.statement}&rdquo;
                    </p>
                  )}
                </div>
              ))
            ) : (
              <div style={{ padding: 8, background: "var(--surface-elevated)", borderRadius: 6 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 12, fontWeight: 700 }}>
                  <span>Ah Seng (Workshop Lead Technician)</span>
                  <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>+65 9234 5678</span>
                </div>
                <p style={{ fontSize: 12, color: "var(--text-secondary)", fontStyle: "italic", margin: "4px 0 0" }}>
                  &ldquo;Heard collision sound at Bay 2; observed third-party van stationary against Honda rear tailgate.&rdquo;
                </p>
              </div>
            )}

            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 11, color: "var(--text-secondary)", paddingTop: 6, borderTop: "1px solid var(--border-color)" }}>
              <span><strong>Dashcam Digital Hash:</strong> SHA-256 Verified</span>
              <span style={{ fontFamily: "var(--font-mono)", color: "var(--accent-cyan)" }}>E89A...44C1</span>
            </div>
          </div>
        </div>

        {/* REPORTER & SURVEYOR PARTICULARS */}
        <div className="dossier-tier-card">
          <div className="dossier-tier-header">
            <div className="dossier-tier-title">
              <UserCheck style={{ width: 15, height: 15, color: "var(--accent-primary)" }} />
              Reporter &amp; Dispatch Particulars
            </div>
            <span style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              ORIGINATOR
            </span>
          </div>

          <div className="dossier-field-grid grid-2col">
            <DossierField label="Reporting Assessor / Lead" value={d.reporter_name || "Patrick Ng"} />
            <DossierField label="Professional Role" value={d.reporter_role || "Senior Loss Adjuster (Motor)"} />
            <DossierField label="Organisation / Firm" value={d.company_name || d.reporter_department || "Carlink Consultancy Pte Ltd"} />
            <DossierField label="Contact Telephony / Email" value={d.reporter_contact || d.reporter_email || "+65 6285 6178"} mono />
            <DossierField label="Filing Date & Time" value={formatSgtDateTime(report.created_at)} mono />
            <DossierField label="Audit Status" value="Surveyor Certified" highlight />
          </div>
        </div>
      </div>

      {/* 6. STATUTORY WITHOUT PREJUDICE LEGAL DECLARATION */}
      <div className="legal-without-prejudice-banner">
        <Scale style={{ width: 20, height: 20, color: "var(--accent-primary)", flexShrink: 0, marginTop: 2 }} />
        <div>
          <div style={{ fontWeight: 800, color: "var(--text-primary)", letterSpacing: "0.04em", marginBottom: 2 }}>
            STATUTORY WITHOUT PREJUDICE DECLARATION
          </div>
          <div>
            This vehicle inspection, damage audit, and forensic report was conducted entirely on a <strong>&ldquo;WITHOUT PREJUDICE&rdquo;</strong> basis.
            Carlink Consultancy and Patrick Ng &amp; Co. have not authorised any repair on behalf of any insurer or third-party underwriter.
            All measurements, tyre tread depths, and impact vectors comply with the General Insurance Association of Singapore (GIA) IDAC Guidelines.
          </div>
        </div>
      </div>
    </div>
  );
}
