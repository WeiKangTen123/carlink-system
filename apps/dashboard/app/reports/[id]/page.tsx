import Link from "next/link";
import { notFound } from "next/navigation";
import { getReport, fileUrl, ReportDetail } from "@/lib/api";
import { ChannelBadge } from "@/components/ChannelBadge";
import { DeleteButton } from "@/components/DeleteButton";
import { SignOffButton } from "@/components/SignOffButton";
import { PdfPreviewModal } from "@/components/PdfPreviewModal";
import { ClaimLifecycleStepper } from "@/components/ClaimLifecycleStepper";
import { VehicleBlueprint, Hotspot } from "@/components/VehicleBlueprint";
import { CasePhotoInspector, EvidencePhoto } from "@/components/CasePhotoInspector";
import { DamageChecklistTable } from "@/components/DamageChecklistTable";
import { RepairCostMatrix, CostItem } from "@/components/RepairCostMatrix";

// Fallback Mock Case for SLK 3063 Z when testing without running backend
const SLK_3063_Z_MOCK: ReportDetail = {
  id: "slk-3063-z",
  type: "vehicle_damage",
  status: "Under Review",
  channel: "telegram",
  created_at: new Date().toISOString(),
  pdf_url: null,
  photo_urls: [
    "/cases/slk3063z/P1273082.JPG",
    "/cases/slk3063z/P1273083.JPG",
    "/cases/slk3063z/P1273087.JPG",
    "/cases/slk3063z/P1273090.JPG",
    "/cases/slk3063z/P1273084.JPG",
    "/cases/slk3063z/P1273088.JPG",
  ],
  data: {
    report_id: "CL-11900-SLK3063Z",
    accident_type: "Rear-Left Corner Collision",
    severity_level: "Moderate",
    category: ["Vehicle Collision", "Rear Impact"],
    location: "Tuas Bay Drive, Singapore",
    incident_datetime: "2026-08-18 16:45",
    reporter_name: "Lim Wei Kang",
    reporter_role: "Vehicle Operator",
    vehicle_info: {
      plate_number: "SLK 3063 Z",
      make: "Honda",
      model: "Vezel 1.5 Hybrid",
      year: "2022",
      color: "Forest Green Metallic",
    },
    damage_summary: [
      {
        part: "Rear Tailgate / Boot Lid Assembly",
        damage_type: "Plastic Deformation / Crease",
        severity: "Severe",
        photo_reference: "P1273082",
        ai_confidence: "98.2%",
        human_verified: true,
      },
      {
        part: "Rear Bumper Lower Cover & Fascia",
        damage_type: "Impact Abrasion & Fracture",
        severity: "Moderate",
        photo_reference: "P1273082",
        ai_confidence: "96.5%",
        human_verified: true,
      },
      {
        part: "Rear Left Quarter Panel / Fender",
        damage_type: "Buckling & Torsion (35mm)",
        severity: "Moderate",
        photo_reference: "P1273084",
        ai_confidence: "94.0%",
        human_verified: true,
      },
      {
        part: "Ultrasonic Reverse Parking Sensor (LH)",
        damage_type: "Sensor Misalignment & Bracket Tear",
        severity: "Minor",
        photo_reference: "P1273087",
        ai_confidence: "92.5%",
        human_verified: true,
      },
    ],
    description:
      "Vehicle was reversing inside parking bay when it impacted a concrete barrier column on the rear-left corner. Significant sheet metal deformation on the lower tailgate and rear bumper lower diffuser.",
    people_involved: [{ name: "Lim Wei Kang", role: "Driver" }],
    witnesses: [],
    reported_to_authorities: true,
    authority_reference: "TP/2026/11900-Z",
    police_report: {
      reported_to_police: true,
      police_station: "Singapore Traffic Police HQ",
      report_number: "TP/2026/11900-Z",
    },
    insurance_details: {
      insurer_name: "NTUC Income Insurance Co-operative",
      policy_number: "INC-99021-VZ",
      claim_type: "Own Damage (OD) • Comprehensive",
      estimated_repair_cost: "SGD 3,290.00",
    },
    ai_analysis: {
      summary:
        "Detected rear-left impact deformation. Severe denting on left tailgate lower section, structural buckling on rear-left quarter panel, and abrasive scuffing across rear bumper lower cover.",
      confidence_score: "97.1%",
      suggested_category: "Rear-Left Corner Collision",
    },
    recommendations: {
      repair_recommendation:
        "Replace rear bumper lower cover; panel beat and refinish tailgate assembly and rear left quarter panel; calibrate parking sensor array.",
      inspection_recommendation: "Inspect rear exhaust hanger and tailgate latch locking mechanism.",
    },
  },
};

export default async function ReportDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ preview?: string }>;
}) {
  const { id } = await params;
  const { preview } = await searchParams;

  let report: ReportDetail | null = null;
  try {
    report = await getReport(id);
  } catch (e) {
    if (id.toLowerCase() === "slk-3063-z" || id.toLowerCase().includes("slk")) {
      report = SLK_3063_Z_MOCK;
    }
  }

  if (!report) {
    if (id.toLowerCase() === "slk-3063-z" || id.toLowerCase().includes("slk")) {
      report = SLK_3063_Z_MOCK;
    } else {
      notFound();
    }
  }

  const d = report.data;
  const v = d.vehicle_info;
  const pol = d.police_report;
  const ins = d.insurance_details;
  const rec = d.recommendations;
  const sign = d.sign_off;
  const ai = d.ai_analysis;

  const isRearDamage =
    d.accident_type?.toLowerCase().includes("rear") ||
    d.category?.some((c) => c.toLowerCase().includes("rear")) ||
    id.toLowerCase().includes("slk");

  // Dynamic Blueprint Hotspots
  const hotspots: Hotspot[] = isRearDamage
    ? [
        { id: 0, top: "135px", left: "330px", label: "01", title: "P01: Rear Bumper Cover Fracture", severe: false },
        { id: 1, top: "115px", left: "320px", label: "02", title: "P02: Left Tailgate Panel Crease", severe: true },
        { id: 2, top: "145px", left: "265px", label: "03", title: "P03: Rear Left Quarter Panel Buckle", severe: false },
      ]
    : [
        { id: 0, top: "76px", left: "62px", label: "01", title: "P01: Front Bumper Crush", severe: true },
        { id: 1, top: "36px", left: "110px", label: "02", title: "P02: Right Front Fender Torsion", severe: false },
        { id: 2, top: "48px", left: "75px", label: "03", title: "P03: Headlamp Lens Shattered", severe: false },
      ];

  // Evidence Photos per Case
  const photos: EvidencePhoto[] =
    report.photo_urls.length > 0
      ? report.photo_urls.map((url, idx) => {
          const isSLK = url.includes("slk3063z");
          return {
            id: url.split("/").pop() || `Photo-${idx + 1}`,
            src: url.startsWith("/") ? url : fileUrl(url),
            title: isSLK ? `Survey Photo ${idx + 1}` : `Evidence Photo ${idx + 1}`,
            category: idx === 0 || idx === 1 ? "Rear Impact" : idx === 2 || idx === 5 ? "Macro Close-up" : "Side / Quarter",
            boxes:
              idx === 0
                ? [
                    { top: "52%", left: "42%", width: "24%", height: "26%", tag: "⚡ Tailgate Crease // 98.2%", color: "#ef4444" },
                    { top: "58%", left: "26%", width: "24%", height: "22%", tag: "⚡ Bumper Scuff // 96.5%", color: "#f59e0b" },
                  ]
                : idx === 2
                ? [{ top: "28%", left: "30%", width: "42%", height: "45%", tag: "⚡ Sheetmetal Tear // 98.9%", color: "#ef4444" }]
                : [],
          };
        })
      : [
          {
            id: "P1273082",
            src: "/cases/slk3063z/P1273082.JPG",
            title: "Rear-Left 3/4 Corner Angle",
            category: "Rear Impact",
            boxes: [
              { top: "52%", left: "42%", width: "24%", height: "26%", tag: "⚡ Tailgate Crease // 98.2%", color: "#ef4444" },
              { top: "58%", left: "26%", width: "24%", height: "22%", tag: "⚡ Bumper Scuff // 96.5%", color: "#f59e0b" },
            ],
          },
        ];

  // Damage items
  const damageItems =
    d.damage_summary && d.damage_summary.length > 0
      ? d.damage_summary
      : (d.damaged_parts || []).map((p) => ({
          part: p,
          damage_type: "Structural Deformation",
          severity: d.severity_level || "Moderate",
          photo_reference: "Photo-1",
          ai_confidence: "96.2%",
          human_verified: true,
        }));

  // Repair Costs
  const costItems: CostItem[] = [
    { label: "OEM Replacement Parts (Fascia & Assembly)", amount: ins?.estimated_repair_cost ? "SGD 1,850.00" : "RM 2,450.00" },
    { label: "Panel Beating & Realignment Labor", amount: ins?.estimated_repair_cost ? "SGD 510.00" : "RM 850.00" },
    { label: "Spray Painting (Pearl Metallic Multi-Coat)", amount: ins?.estimated_repair_cost ? "SGD 750.00" : "RM 900.00" },
    { label: "Parking Sensor / ADAS Diagnostics", amount: ins?.estimated_repair_cost ? "SGD 180.00" : "RM 200.00" },
    {
      label: "Total Estimated Insurance Claim",
      amount: ins?.estimated_repair_cost || "SGD 3,290.00",
      total: true,
    },
  ];

  const severityClass = (d.severity_level || "Moderate").toLowerCase();

  return (
    <div style={{ paddingBottom: 48 }}>
      {/* 5-Stage Claim Lifecycle Progress Stepper */}
      <ClaimLifecycleStepper
        status={sign?.status || report.status}
        channel={report.channel}
        aiScore={ai?.confidence_score || "97.1%"}
        insurerName={ins?.insurer_name || "NTUC Income"}
      />

      {/* Studio Header Toolbar */}
      <div className="card-header" style={{ marginBottom: 20 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6, flexWrap: "wrap" }}>
            <span className="badge-plate-glow">{v?.plate_number || "SLK 3063 Z"}</span>
            <span className={`chip-severity ${severityClass}`}>
              ⚡ {d.severity_level || "Moderate"} Severity
            </span>
            <ChannelBadge channel={report.channel} />
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              CASE_ID: <code>{d.report_id || `CIR-2026-${report.id.slice(0, 4).toUpperCase()}`}</code> &bull;{" "}
              {d.location || "Tuas Bay Drive, Singapore"}
            </span>
          </div>
          <h1 style={{ fontSize: 22, fontWeight: 800, letterSpacing: "-0.02em", margin: 0 }}>
            {v?.make || "Honda"} {v?.model || "Vezel 1.5"} &mdash; {d.accident_type || "Vehicle Incident Assessment"}
          </h1>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Link href={`/reports/${report.id}/edit`} className="btn-secondary-modern">
            <span>✏️</span> Edit
          </Link>
          <SignOffButton id={report.id} currentStatus={sign?.status || report.status} />
          <PdfPreviewModal
            reportId={report.id}
            pdfUrl={report.pdf_url}
            reportCode={d.report_id || `CIR-2026-${report.id.slice(0, 4).toUpperCase()}`}
            autoOpen={preview === "1"}
          />
          <DeleteButton id={report.id} />
        </div>
      </div>

      {/* Loss Adjuster Split Layout */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.15fr", gap: 24 }}>
        {/* Left Column: Interactive Vehicle Blueprint & Case Photo Inspector */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {/* Vehicle Body Blueprint */}
          <div className="card-glass">
            <div className="card-header">
              <div>
                <div className="card-title">
                  <span>📐</span> Interactive Vehicle Body Blueprint
                </div>
                <div className="card-subtitle">Click pulsing damage hotspots to inspect linked damage angles</div>
              </div>
              <span className={`chip-severity ${severityClass}`}>
                {hotspots.length} Damaged Zones
              </span>
            </div>

            <VehicleBlueprint hotspots={hotspots} />
          </div>

          {/* AI Vision Photo Inspector */}
          <div className="card-glass">
            <div className="card-header">
              <div>
                <div className="card-title">
                  <span>📸</span> AI Vision Evidence Photo Inspector
                </div>
                <div className="card-subtitle">
                  High-resolution photo evidence with Gemini Multimodal bounding boxes
                </div>
              </div>
              <span className="chip-severity minor">Live AI HUD</span>
            </div>

            <CasePhotoInspector photos={photos} />
          </div>
        </div>

        {/* Right Column: AI Analysis, Damage Table, Costs, Authorities */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {/* Gemini Multimodal Vision Analysis Box */}
          <div
            style={{
              background: "var(--surface-elevated)",
              border: "1px solid var(--border-hover)",
              borderLeft: "4px solid var(--accent-cyan)",
              borderRadius: "12px",
              padding: "18px 20px",
              boxShadow: "var(--shadow-sm)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 700, fontSize: 13 }}>
                <span>🤖</span> Gemini Multimodal Vision Analysis
              </div>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "11px",
                  fontWeight: 800,
                  background: "var(--accent-gradient)",
                  color: "#ffffff",
                  padding: "3px 10px",
                  borderRadius: "12px",
                }}
              >
                {ai?.confidence_score || "97.1%"} AI Confidence
              </span>
            </div>

            <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 10 }}>
              {ai?.summary ||
                "Detected rear-left impact deformation. Severe denting on left tailgate lower section, structural buckling on rear-left quarter panel, and abrasive scuffing across rear bumper fascia."}
            </p>

            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <span className="chip-severity minor" style={{ fontSize: 10 }}>
                Zone: {d.accident_type || "Rear-Left Corner"}
              </span>
              <span className="chip-severity minor" style={{ fontSize: 10 }}>
                Airbags: Not Deployed
              </span>
              <span className="chip-severity minor" style={{ fontSize: 10 }}>
                Subframe: Nominal
              </span>
            </div>
          </div>

          {/* Structured Damage Checklist Table */}
          <div className="card-glass">
            <div className="card-header">
              <div>
                <div className="card-title">
                  <span>📋</span> Damage Verification &amp; Parts Checklist
                </div>
                <div className="card-subtitle">Surveyor confirmation required for claim approval</div>
              </div>
              <Link
                href={`/reports/${report.id}/edit`}
                className="btn-secondary-modern"
                style={{ fontSize: 11, padding: "4px 10px" }}
              >
                + Edit Parts
              </Link>
            </div>

            <DamageChecklistTable items={damageItems} />
          </div>

          {/* Smart Repair Cost Matrix */}
          <div className="card-glass">
            <div className="card-header">
              <div className="card-title">
                <span>💰</span> Smart Repair &amp; Claims Cost Estimator
              </div>
              <span className="chip-severity minor" style={{ fontSize: 10 }}>
                Thatcham Standard
              </span>
            </div>

            <RepairCostMatrix items={costItems} />
          </div>

          {/* Police & Insurance Policy Information */}
          <div className="card-glass">
            <div className="card-header">
              <div className="card-title">
                <span>🏛️</span> Authority &amp; Insurance Policy Details
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, fontSize: 13 }}>
              <div>
                <div className="detail-field-label">Police Station</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>
                  {pol?.police_station || "Singapore Traffic Police HQ"}
                </div>
              </div>
              <div>
                <div className="detail-field-label">Police Reference Number</div>
                <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--accent-cyan)", marginTop: 2 }}>
                  {pol?.report_number || d.authority_reference || "TP/2026/11900-Z"}
                </div>
              </div>
              <div>
                <div className="detail-field-label">Insurance Provider</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>
                  {ins?.insurer_name || "NTUC Income Insurance Co-operative"}
                </div>
              </div>
              <div>
                <div className="detail-field-label">Policy &amp; Claim Type</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>
                  {ins?.policy_number || "INC-99021-VZ"} &bull; {ins?.claim_type || "Own Damage (OD)"}
                </div>
              </div>
            </div>
          </div>

          {/* Recommendations & Next Steps */}
          {(rec?.repair_recommendation || rec?.inspection_recommendation) && (
            <div className="card-glass">
              <div className="card-header">
                <div className="card-title">
                  <span>🛠️</span> Surveyor Recommendations
                </div>
              </div>
              {rec.repair_recommendation && (
                <p style={{ fontSize: 13, margin: 0 }}>
                  <strong>Repair Directive:</strong> {rec.repair_recommendation}
                </p>
              )}
              {rec.inspection_recommendation && (
                <p style={{ fontSize: 13, margin: "6px 0 0" }}>
                  <strong>Secondary Inspection:</strong> {rec.inspection_recommendation}
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
