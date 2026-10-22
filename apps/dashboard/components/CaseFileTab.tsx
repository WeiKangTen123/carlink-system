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
import type { ReportDetail, TyreItem } from "@/lib/api";
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
        suppressHydrationWarning
        className={`dossier-field-value ${mono ? "mono" : ""} ${highlight ? "highlight" : ""}`}
      >
        {displayVal}
      </div>
    </div>
  );
}

const HISTORY_LABELS: Record<string, string> = {
  created: "Created",
  edited: "Edited",
  finalized: "Filed as a case",
  signed_off: "Signed off",
  reopened: "Reopened",
};

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

  // Every value below is the recorded one or "—". This dossier is printed
  // for insurers and courts; until 2026-10-21 empty fields were filled with
  // fixed sample values (VIN, engine no., odometer, police report, driver,
  // witness, third party, GPS, quantum figures), so every case read as
  // fully surveyed.
  const caseIdDisplay = formatCarlinkRef(report.id, v?.plate_number, report.created_at);
  const plateDisplay = v?.plate_number || "Plate not recorded";
  const vehicleName = inferVehicleName({
    vehicle_name: v ? `${v.make || ""} ${v.model || ""}`.trim() : null,
    plate_number: v?.plate_number,
    accident_type: d.accident_type,
    category: d.category,
  });

  const isSignedOff = report.status === "Signed Off" || signOff?.status === "Signed Off";
  const insurerName = inferInsurerName({ insurer_name: ins?.insurer_name });
  // The workshop field only -- this used to be handed the incident
  // location, which then showed as the appointed workshop.
  const workshopName = inferWorkshopName(ins?.workshop_assigned);
  const claimArchetype = getClaimArchetype({ claim_type: ins?.claim_type });
  const claimTypeLabel = ins?.claim_type || claimArchetype.label;
  const giaSla = getGiaSla(report.created_at, Boolean(signOff?.signature_date));
  const bola = d.bola_assessment;
  const annexC = d.annex_c;

  // Tyres as surveyed. These used to default to "Dunlop Enasave 215/60 R16",
  // 6.0 mm and 32/33 PSI, so every case showed four LTA-compliant tyres;
  // pressure isn't a recorded field at all, so that column is now the
  // recorded condition.
  const tyres = v?.tyres || {};
  const tyreRow = (pos: string, t?: TyreItem | null) => {
    const depth = t?.tread_depth_mm ?? null;
    return {
      pos,
      spec: [t?.brand, t?.size].filter(Boolean).join(" "),
      depth,
      condition: t?.condition || "",
      status: depth == null ? "unrecorded" : depth >= 3.0 ? "pass" : depth >= 1.6 ? "warn" : "fail",
    };
  };
  const tyreRows = [
    tyreRow("[FL] Front-Left", tyres.front_nearside),
    tyreRow("[FR] Front-Right", tyres.front_offside),
    tyreRow("[RL] Rear-Left", tyres.rear_nearside),
    tyreRow("[RR] Rear-Right", tyres.rear_offside),
  ];

  // Completeness Audit calculation -- each check passes only on recorded
  // data (they used to pass on the guessed insurer/workshop and on any
  // odometer reading).
  const checks = [
    { label: "Vehicle Particulars Recorded", done: Boolean(v?.plate_number && (v?.make || v?.model)) },
    { label: "Reported to Police", done: Boolean(d.reported_to_authorities || pol?.reported_to_police || pol?.report_number) },
    { label: "Underwriting Linked", done: Boolean(ins?.insurer_name?.trim()) },
    { label: "Workshop Assigned", done: Boolean(ins?.workshop_assigned?.trim()) },
    { label: "Tyre Matrix Surveyed", done: tyreRows.some((t) => t.depth != null) },
    { label: "Evidence & Photos Secured", done: (report.photo_urls?.length ?? 0) > 0 },
  ];
  const completedCount = checks.filter((c) => c.done).length;
  const completionPct = Math.round((completedCount / checks.length) * 100);

  const formatSgd = (n?: number | null) =>
    n != null && !Number.isNaN(n)
      ? `SGD ${n.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
      : "—";
  const gstAmount =
    annexC?.gst_amount ??
    (annexC?.agreed_lump_sum != null ? Math.round(annexC.agreed_lump_sum * (annexC.gst_rate || 0.09) * 100) / 100 : null);

  const integrityParts = [
    v?.steering_condition && `Steering: ${v.steering_condition}`,
    v?.brakes_condition && `Brakes: ${v.brakes_condition}`,
  ].filter(Boolean);

  return (
    <div className="dossier-workstation">
      {/* 1. DOSSIER COMMAND & ACTIONS BAR */}
      <div className="dossier-header-bar">
        <div className="dossier-header-left">
          <div className="dossier-plate-badge">
            <Car style={{ width: 16, height: 16, color: "var(--accent-primary)" }} />
            <span>{plateDisplay}</span>
            {/* No "LTA VALIDATED" badge: nothing here checks the plate with LTA. */}
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
            suppressHydrationWarning
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
            disabled={!v?.vin}
            title={v?.vin ? undefined : "No VIN recorded"}
            onClick={() => handleCopy(v?.vin || "", "vin")}
          >
            <Copy style={{ width: 12, height: 12 }} />
            {copiedKey === "vin" ? "VIN Copied!" : "Copy VIN"}
          </button>

          <button
            type="button"
            className="dossier-action-btn"
            disabled={!ins?.claim_number}
            title={ins?.claim_number ? undefined : "No claim number recorded"}
            onClick={() => handleCopy(ins?.claim_number || "", "claim")}
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
            <span>CLAIM HEALTH & COMPLETION AUDIT</span>
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
              Tier 1: Vehicle Engineering & LTA Dossier
            </div>
            <span style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              COE / LTA SPEC
            </span>
          </div>

          <div className="dossier-field-grid">
            <DossierField label="Registration Plate" value={v?.plate_number} mono />
            {/* Not checked against LTA anywhere, so not asserted. */}
            <DossierField label="LTA Reg Status" value={null} />
            <DossierField label="Body / Classification" value={v?.body_type} />

            <DossierField label="Make & Model" value={vehicleName} />
            <DossierField label="Model Year" value={v?.year} />
            <DossierField label="Transmission" value={v?.transmission} />

            <DossierField
              label="Chassis Number (VIN)"
              value={v?.vin}
              mono
              onCopy={() => handleCopy(v?.vin || "", "vin_cell")}
              copyLabel="VIN"
              isCopied={copiedKey === "vin_cell"}
            />
            <DossierField label="Engine Serial No." value={v?.engine_number} mono />
            <DossierField label="Ownership Type" value={v?.ownership_type} />

            <DossierField label="Odometer Reading" value={v?.odometer_reading} mono highlight />
            <DossierField label="Colour / Paintwork" value={[v?.color, v?.paintwork_condition].filter(Boolean).join(" / ")} />
            <DossierField label="Static Integrity Check" value={integrityParts.join(" | ")} />
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
                    <th>Tyre Spec & Brand</th>
                    <th>Tread Depth</th>
                    <th>Condition</th>
                    <th>LTA Status</th>
                  </tr>
                </thead>
                <tbody>
                  {tyreRows.map((tr, idx) => (
                    <tr key={idx}>
                      <td style={{ fontWeight: 700, fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>{tr.pos}</td>
                      <td>{tr.spec || "—"}</td>
                      <td>
                        {tr.depth != null ? (
                          <span className={`tyre-gauge-pill ${tr.status}`}>
                            {tr.depth.toFixed(1)} mm
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td>{tr.condition || "—"}</td>
                      <td>
                        {tr.status === "unrecorded" ? (
                          <span style={{ fontSize: 10, fontWeight: 700, color: "var(--text-muted)" }}>PENDING SURVEYOR</span>
                        ) : (
                          <span style={{ fontSize: 10, fontWeight: 700, color: tr.status === "pass" ? "var(--badge-green-text)" : tr.status === "warn" ? "var(--badge-amber-text)" : "var(--badge-red-text)" }}>
                            {tr.status === "pass" ? "PASS / LTA COMPLIANT" : tr.status === "warn" ? "WARN / NEAR LIMIT" : "FAIL / LTA VIOLATION"}
                          </span>
                        )}
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
              Tier 2: Claim & Commercial Governance
            </div>
            <span style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              GIA / IDAC FRAMEWORK
            </span>
          </div>

          <div className="dossier-field-grid">
            <DossierField label="Underwriting Insurer" value={insurerName} highlight />
            <DossierField label="Policy Number" value={ins?.policy_number} mono />
            {/* The insurer's claim number only -- not our generated REF, which
                must not pass for one. */}
            <DossierField
              label="Master Claim Number"
              value={ins?.claim_number}
              mono
              onCopy={() => handleCopy(ins?.claim_number || "", "claim_cell")}
              copyLabel="Claim #"
              isCopied={copiedKey === "claim_cell"}
            />

            <DossierField label="Claim Archetype" value={claimTypeLabel} />
            {/* Coverage and excess aren't recorded fields. */}
            <DossierField label="Policy Coverage" value={null} />
            <DossierField label="Excess / Deductible" value={null} mono />

            {/* The signer's own, once signed off (from their account). These
                were one fixed firm and licence on every case. */}
            <DossierField label="Appointed Loss Adjuster" value={isSignedOff ? signOff?.firm_name || signOff?.surveyor_name : null} />
            <DossierField label="Surveyor License #" value={isSignedOff ? signOff?.license_number : null} mono />
            <DossierField label="GIA Survey SLA" value={`${giaSla.badgeText} · ${giaSla.remainingText}`} />

            <DossierField label="Appointed Workshop" value={workshopName} />
            <DossierField label="Workshop Cluster" value={null} />
            {/* No authorization is recorded anywhere; this used to read
                "Authorized by Surveyor" on every case. Shows the recorded
                recommendation instead. */}
            <DossierField label="Disassembly Status" value={d.recommendations?.disassembly_required ? "Required" : null} />
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
                Financial Reserves & Settlement Ledger
              </span>
              <span style={{ fontSize: 10, color: "var(--badge-green-text)", fontFamily: "var(--font-mono)" }}>
                SINGAPORE DOLLARS (SGD)
              </span>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, background: "var(--surface-elevated)", padding: 10, borderRadius: 8 }}>
              {/* Recorded figures only (these were fixed 13,734 / 10,209.20 /
                  8,700 / 783 on every case). */}
              <div>
                <div className="dossier-field-label">Est. Repair Cost</div>
                <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--text-primary)", fontSize: 13 }}>
                  {ins?.estimated_repair_cost || "—"}
                </div>
              </div>
              <div>
                <div className="dossier-field-label">Final Approved</div>
                <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--accent-cyan)", fontSize: 13 }}>
                  {ins?.final_approved_cost || "—"}
                </div>
              </div>
              <div>
                <div className="dossier-field-label">Lump Sum Agt.</div>
                <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--badge-green-text)", fontSize: 13 }}>
                  {formatSgd(annexC?.agreed_lump_sum)}
                </div>
              </div>
              <div>
                <div className="dossier-field-label">GST (9%)</div>
                <div style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text-secondary)", fontSize: 13 }}>
                  {formatSgd(gstAmount)}
                </div>
              </div>
            </div>

            <div style={{ marginTop: 10, display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 11, color: "var(--text-secondary)", background: "rgba(59, 130, 246, 0.05)", padding: "6px 10px", borderRadius: 6 }}>
              <span>
                <strong>GIA BOLA Matrix:</strong>{" "}
                {bola?.scenario_number != null
                  ? `Scenario #${bola.scenario_number}${bola.scenario_name ? ` (${bola.scenario_name})` : ""}`
                  : "Pending surveyor assessment"}
              </span>
              {bola?.insured_liability_pct != null && bola?.third_party_liability_pct != null ? (
                <span style={{ fontWeight: 700, color: "var(--badge-green-text)" }}>
                  Insured Liability: {bola.insured_liability_pct}% | TP: {bola.third_party_liability_pct}%
                </span>
              ) : (
                <span style={{ fontWeight: 700, color: "var(--text-muted)" }}>Liability: Pending surveyor</span>
              )}
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
              Tier 3: Incident Forensics & Environmental Telematics
            </div>
            <span style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              SGT TELEMETRY
            </span>
          </div>

          <div className="dossier-field-grid">
            <DossierField label="Incident Date / Time" value={formatSgtDateTime(d.incident_datetime)} mono highlight />
            {/* Lighting, coordinates and drivability aren't recorded fields. */}
            <DossierField label="Lighting / Solar Phase" value={null} />
            <DossierField label="Intake Channel" value={report.channel.toUpperCase()} mono />

            <DossierField label="Roadway Location" value={d.location} />
            <DossierField label="Decimal Coordinates" value={null} mono />
            <DossierField label="Collision Severity" value={d.severity_level} highlight />

            <DossierField label="Weather Condition" value={d.weather_condition} />
            <DossierField label="Roadway Surface" value={d.road_condition} />
            <DossierField label="Traffic Flow" value={d.traffic_condition} />

            <DossierField label="Impact Vector" value={v?.point_of_impact} highlight />
            <DossierField label="Drivability Status" value={null} />
            <DossierField label="Dispatched Facility" value={workshopName} />
          </div>

          {/* NARRATIVE STATEMENT */}
          <div style={{ padding: "0 16px 14px", borderTop: "1px solid var(--border-color)", paddingTop: 10 }}>
            <div className="dossier-field-label" style={{ marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
              <FileText style={{ width: 12, height: 12 }} />
              Accident Narrative & Surveyor Assessment Notes
            </div>
            <p style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.6, margin: 0, background: "var(--surface-elevated)", padding: "8px 12px", borderRadius: 6 }}>
              {d.description || "No description recorded"}
            </p>
          </div>
        </div>

        {/* TIER 4: REGULATORY, POLICE & PARTIES DOSSIER */}
        <div className="dossier-tier-card">
          <div className="dossier-tier-header">
            <div className="dossier-tier-title">
              <ShieldCheck style={{ width: 15, height: 15, color: "var(--accent-primary)" }} />
              Tier 4: Regulatory, Police & Parties Dossier
            </div>
            <span style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              SINGAPORE TRAFFIC POLICE
            </span>
          </div>

          <div className="dossier-field-grid">
            <DossierField
              label="Traffic Police Report #"
              value={pol?.report_number}
              mono
              highlight
              onCopy={() => handleCopy(pol?.report_number || "", "tp_report")}
              copyLabel="Police Report #"
              isCopied={copiedKey === "tp_report"}
            />
            <DossierField label="Investigating Station" value={pol?.police_station} />
            <DossierField label="Investigating Officer" value={pol?.officer_name} />

            <DossierField label="Insured Driver Name" value={v?.driver_name} />
            {/* NRIC, trigger, licence class and sobriety result aren't
                recorded fields; these used to show a made-up NRIC fragment,
                "Class 3 (Valid)" and a negative 0.0 mg/L breath test. */}
            <DossierField label="Driver NRIC Mask" value={null} mono />
            <DossierField label="Driver Contact" value={v?.driver_contact} mono />

            <DossierField label="Statutory Trigger" value={null} />
            <DossierField label="Driving License Class" value={null} />
            <DossierField label="Field Sobriety Test" value={null} mono />
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
                  {tp?.plate_number || "—"}
                </div>
              </div>
              <div>
                <div className="dossier-field-label">TP Vehicle Model</div>
                <div style={{ fontWeight: 600, color: "var(--text-secondary)", fontSize: 12 }}>
                  {tp?.make_model || "—"}
                </div>
              </div>
              <div>
                <div className="dossier-field-label">TP Insurer</div>
                <div style={{ fontWeight: 600, color: "var(--accent-primary)", fontSize: 12 }}>
                  {tp?.insurer_name || "—"}
                </div>
              </div>
              <div>
                <div className="dossier-field-label">TP Driver</div>
                <div style={{ fontWeight: 600, color: "var(--text-secondary)", fontSize: 12 }}>
                  {tp?.driver_name || "—"}
                </div>
              </div>
              <div>
                <div className="dossier-field-label">TP Contact</div>
                <div style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text-secondary)", fontSize: 12 }}>
                  {tp?.driver_contact || "—"}
                </div>
              </div>
              <div>
                <div className="dossier-field-label">Observed TP Damage</div>
                <div style={{ fontWeight: 600, color: "var(--badge-amber-text)", fontSize: 12 }}>
                  {tp?.damage_description || "—"}
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
              Witnesses & Evidence Custody
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
              // Empty state, not a sample witness: this used to show an
              // invented witness with a phone number and a quoted statement.
              <div style={{ padding: 8, background: "var(--surface-elevated)", borderRadius: 6, fontSize: 12, color: "var(--text-muted)" }}>
                No witnesses recorded
              </div>
            )}
            {/* No dashcam hash line: no footage is hashed or verified here. */}
          </div>
        </div>

        {/* REPORTER & SURVEYOR PARTICULARS */}
        <div className="dossier-tier-card">
          <div className="dossier-tier-header">
            <div className="dossier-tier-title">
              <UserCheck style={{ width: 15, height: 15, color: "var(--accent-primary)" }} />
              Reporter & Dispatch Particulars
            </div>
            <span style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              ORIGINATOR
            </span>
          </div>

          <div className="dossier-field-grid grid-2col">
            {/* The case's reporter as recorded -- not the firm's surveyor. */}
            <DossierField label="Reporting Assessor / Lead" value={d.reporter_name} />
            <DossierField label="Professional Role" value={d.reporter_role} />
            <DossierField label="Organisation / Firm" value={d.company_name || d.reporter_department} />
            <DossierField label="Contact Telephony / Email" value={d.reporter_contact || d.reporter_email} mono />
            <DossierField label="Filing Date & Time" value={formatSgtDateTime(report.created_at)} mono />
            {/* From the case status; this read "Surveyor Certified" on
                unsigned cases too. */}
            <DossierField
              label="Audit Status"
              value={isSignedOff ? `Signed off${signOff?.surveyor_name ? ` by ${signOff.surveyor_name}` : ""}` : "Pending surveyor sign-off"}
              highlight={isSignedOff}
            />
          </div>
        </div>
      </div>

      {/* CASE HISTORY -- who created, edited, signed off and reopened it
          (recorded by the API since sign-in was added). */}
      {(report.history?.length ?? 0) > 0 && (
        <div className="dossier-tier-card">
          <div className="dossier-tier-header">
            <div className="dossier-tier-title">
              <Clock style={{ width: 15, height: 15, color: "var(--accent-primary)" }} />
              Case History
            </div>
            <span style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              AUDIT TRAIL
            </span>
          </div>
          <ul className="case-history">
            {report.history!.map((h, i) => (
              <li key={`${h.action}-${h.at}-${i}`}>
                <span className={`case-history-dot ${h.action}`} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <strong>{HISTORY_LABELS[h.action] || h.action}</strong>
                  {h.user_name ? <> by {h.user_name}</> : null}
                  {h.detail ? <span style={{ color: "var(--text-muted)" }}> — {h.detail}</span> : null}
                </div>
                <span suppressHydrationWarning style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)", whiteSpace: "nowrap" }}>
                  {formatSgtDateTime(h.at)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 6. STATUTORY WITHOUT PREJUDICE LEGAL DECLARATION */}
      <div className="legal-without-prejudice-banner">
        <Scale style={{ width: 20, height: 20, color: "var(--accent-primary)", flexShrink: 0, marginTop: 2 }} />
        <div>
          <div style={{ fontWeight: 800, color: "var(--text-primary)", letterSpacing: "0.04em", marginBottom: 2 }}>
            STATUTORY WITHOUT PREJUDICE DECLARATION
          </div>
          <div>
            This vehicle inspection, damage audit, and forensic report was conducted entirely on a <strong>&ldquo;WITHOUT PREJUDICE&rdquo;</strong> basis.
            The appointed loss adjuster has not authorised any repair on behalf of any insurer or third-party underwriter.
            {/* No blanket "all measurements comply with GIA IDAC Guidelines"
                line: it was printed whether or not anything was measured. */}
          </div>
        </div>
      </div>
    </div>
  );
}
